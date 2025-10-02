#!/usr/bin/env python3
# lightweight_monitor10.py - Веб-интерфейс для мониторинга нескольких HP Primera систем

import subprocess
import time
import json
import re
import threading
import socket
import errno
import yaml
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from collections import defaultdict
from socketserver import ThreadingMixIn


class Config:
    """Класс для работы с конфигурацией из YAML файла"""
    
    _instance = None
    _config = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Config, cls).__new__(cls)
            cls._load_config()
        return cls._instance
    
    @classmethod
    def _load_config(cls):
        """Загружает конфигурацию из YAML файла"""
        config_paths = [
            './config.yaml',
            '/etc/hp_primera_monitor/config.yaml',
            os.path.expanduser('~/.hp_primera_monitor/config.yaml')
        ]
        
        config_file = None
        for path in config_paths:
            if os.path.exists(path):
                config_file = path
                break
        
        if config_file is None:
            # Создаем конфигурацию по умолчанию
            cls._config = cls._get_default_config()
            print(f"Config file not found, using default configuration")
        else:
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    cls._config = yaml.safe_load(f)
                print(f"Loaded configuration from {config_file}")
            except Exception as e:
                print(f"Error loading config from {config_file}: {e}, using default configuration")
                cls._config = cls._get_default_config()
        
        # Обновляем конфигурацию значениями по умолчанию для отсутствующих ключей
        default_config = cls._get_default_config()
        cls._config = cls._deep_update(default_config, cls._config)
    
    @staticmethod
    def _get_default_config():
        """Возвращает конфигурацию по умолчанию"""
        return {
            'server': {
                'host': '0.0.0.0',
                'port': 6767,
                'timeout': 300,
                'workers': 10,
                'reuse_addr': True
            },
            'cache': {
                'update_interval': 25,
                'enabled': True
            },
            'monitoring': {
                'default_life_threshold': 80,
                'script_timeout': 60,
                'storage_user': 'codmonitor',
                'storage_password': '1qaz@WSX',
                'zabbix_host_prefix': 'storage'
            },
            'logging': {
                'level': 'INFO',
                'format': '[%(asctime)s] [Thread-%(thread)d] %(client_ip)s - %(message)s'
            },
            'security': {
                'allowed_hosts': [],  # пустой список = все хосты разрешены
                'max_request_size': 1048576  # 1MB
            }
        }
    
    @staticmethod
    def _deep_update(default, user):
        """Рекурсивно обновляет словарь по умолчанию пользовательскими значениями"""
        result = default.copy()
        for key, value in user.items():
            if isinstance(value, dict) and key in result and isinstance(result[key], dict):
                result[key] = Config._deep_update(result[key], value)
            else:
                result[key] = value
        return result
    
    def get(self, key, default=None):
        """Получает значение конфигурации по ключу (точечная нотация)"""
        keys = key.split('.')
        value = self._config
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value
    
    def get_server_config(self):
        """Возвращает конфигурацию сервера"""
        return self._config['server']
    
    def get_cache_config(self):
        """Возвращает конфигурацию кэша"""
        return self._config['cache']
    
    def get_monitoring_config(self):
        """Возвращает конфигурацию мониторинга"""
        return self._config['monitoring']
    
    def get_logging_config(self):
        """Возвращает конфигурацию логирования"""
        return self._config['logging']


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Многопоточный HTTP сервер"""
    daemon_threads = True
    
    def __init__(self, server_address, RequestHandlerClass, config):
        self.timeout = config.get('timeout', 300)
        self.config = config
        super().__init__(server_address, RequestHandlerClass)

    def handle_timeout(self):
        """Обработка таймаута соединения"""
        pass


class CacheManager:
    def __init__(self, config):
        self.update_interval = config.get('update_interval', 25)
        self.cache = defaultdict(dict)
        self.last_update = defaultdict(lambda: 0)
        self.lock = threading.RLock()  # Используем RLock для вложенных блокировок
        self.update_locks = defaultdict(threading.Lock)  # Отдельные блокировки для каждого ключа кэша

    def should_update(self, host_ip, check_type, life_thresh='80'):
        cache_key = f"{check_type}_{life_thresh}"
        current_time = time.time()

        with self.lock:
            last_update_time = self.last_update[(host_ip, cache_key)]
            if current_time - last_update_time >= self.update_interval:
                return True
            return cache_key not in self.cache[host_ip]

    def get_cached_data(self, host_ip, check_type, life_thresh='80'):
        cache_key = f"{check_type}_{life_thresh}"

        with self.lock:
            return self.cache[host_ip].get(cache_key)

    def update_cache(self, host_ip, check_type, data, life_thresh='80'):
        cache_key = f"{check_type}_{life_thresh}"
        current_time = time.time()

        with self.lock:
            self.cache[host_ip][cache_key] = data
            self.last_update[(host_ip, cache_key)] = current_time

    def get_update_lock(self, host_ip, check_type, life_thresh='80'):
        """Получить блокировку для конкретного ключа кэша"""
        cache_key = f"{check_type}_{life_thresh}"
        return self.update_locks[(host_ip, cache_key)]


class MonitoringHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        self.cache_manager = kwargs.pop('cache_manager')
        self.config = kwargs.pop('config')
        super().__init__(*args, **kwargs)

    def do_GET(self):
        try:
            # Устанавливаем таймаут на операцию чтения/записи
            self.connection.settimeout(self.config.get('server.timeout', 300))

            parsed_path = urlparse(self.path)
            path = parsed_path.path

            host_match = re.match(r'/(\d+\.\d+\.\d+\.\d+)(/.*)?', path)

            if path == '/' or path == '':
                self.serve_home()
            elif path == '/health':
                self.serve_health()
            elif host_match:
                host_ip = host_match.group(1)
                sub_path = host_match.group(2) or '/'
                self.serve_host_request(host_ip, sub_path, parsed_path.query)
            else:
                self.send_error(404, "Endpoint not found")
        except socket.timeout:
            self.log_message("Request timeout for %s", self.path)
            try:
                self.send_error(408, "Request timeout")
            except:
                pass
        except (BrokenPipeError, ConnectionResetError, socket.error) as e:
            # Клиент разорвал соединение - просто логируем и игнорируем
            if isinstance(e, socket.error) and e.errno == errno.EPIPE:
                self.log_message("Client disconnected (broken pipe): %s", str(e))
            else:
                self.log_message("Client disconnected: %s", str(e))
        except Exception as e:
            self.log_message("Unexpected error: %s", str(e))
            try:
                self.send_error(500, "Internal server error")
            except:
                pass  # Игнорируем ошибки при отправке ошибки

    def serve_home(self):
        cache_config = self.config.get_cache_config()
        server_config = self.config.get_server_config()
        
        endpoints = {
            "service": "HP Primera Multi-Host Monitoring",
            "usage": "Use /IP_ADDRESS/endpoint to monitor specific host",
            "available_endpoints": {
                "/IP_ADDRESS/": "Host information and available checks",
                "/IP_ADDRESS/health": "Health check for specific host",
                "/IP_ADDRESS/check/one": "Status checks (disks, ports, nodes, PSU, cages)",
                "/IP_ADDRESS/check/two": "Performance and capacity checks",
                "/IP_ADDRESS/check/all": "Both checks",
                "/IP_ADDRESS/config": "Host configuration"
            },
            "examples": {
                "check_one": f"http://{server_config['host']}:{server_config['port']}/10.9.132.58/check/one",
                "check_two": f"http://{server_config['host']}:{server_config['port']}/10.9.132.58/check/two?lifetresh=85",
                "health": f"http://{server_config['host']}:{server_config['port']}/10.9.132.58/health"
            },
            "cache_info": {
                "update_interval": cache_config['update_interval'],
                "cache_enabled": cache_config['enabled'],
                "note": f"Data is updated every {cache_config['update_interval']} seconds, cached results are returned between updates"
            },
            "server_info": {
                "threaded": True,
                "timeout": server_config['timeout'],
                "workers": server_config['workers'],
                "connection_handling": "Improved with threading and better timeout management"
            }
        }
        self.send_json_response(200, endpoints)

    def serve_health(self):
        response = {
            'status': 'ok',
            'service': 'hp_primera_multi_monitor',
            'timestamp': time.time(),
            'timestamp_human': time.strftime('%Y-%m-%d %H:%M:%S'),
            'cache_info': f'Cache manager is running, {sum(len(v) for v in self.cache_manager.cache.values())} total entries',
            'server_status': 'threaded_server_active',
            'config_source': 'yaml'
        }
        self.send_json_response(200, response)

    def serve_host_request(self, host_ip, path, query_string):
        try:
            query_params = parse_qs(query_string)

            if path == '/':
                self.serve_host_info(host_ip)
            elif path == '/health':
                self.serve_host_health(host_ip)
            elif path == '/check/one':
                self.serve_check_one(host_ip)
            elif path == '/check/two':
                life_thresh = query_params.get('lifetresh', [str(self.config.get('monitoring.default_life_threshold', 80))])[0]
                self.serve_check_two(host_ip, life_thresh)
            elif path == '/check/all':
                self.serve_check_all(host_ip)
            elif path == '/config':
                self.serve_host_config(host_ip)
            else:
                self.send_error(404, f"Endpoint {path} not found for host {host_ip}")
        except socket.timeout:
            self.log_message("Host request timeout for %s%s", host_ip, path)
            try:
                self.send_error(408, f"Request timeout for host {host_ip}")
            except:
                pass
        except (BrokenPipeError, ConnectionResetError, socket.error) as e:
            raise  # Пробрасываем наверх для обработки в do_GET
        except Exception as e:
            self.log_message("Error in host request: %s", str(e))
            try:
                self.send_error(500, f"Internal error for host {host_ip}")
            except:
                pass  # Игнорируем если клиент отключился

    def serve_host_info(self, host_ip):
        host_info = {
            'host': host_ip,
            'status': 'available',
            'available_checks': {
                '/check/one': 'Status checks (disks, ports, nodes, PSU, cages)',
                '/check/two': 'Performance and capacity checks (with ?lifetresh=threshold)',
                '/check/all': 'Both checks',
                '/health': 'Host health check',
                '/config': 'Host configuration'
            },
            'timestamp': time.time(),
            'timestamp_human': time.strftime('%Y-%m-%d %H:%M:%S'),
            'cache_info': f'Cache status: {len(self.cache_manager.cache[host_ip])} entries',
            'server_type': 'threaded',
            'config_source': 'yaml'
        }
        self.send_json_response(200, host_info)

    def serve_host_health(self, host_ip):
        response = {
            'host': host_ip,
            'status': 'reachable',
            'health': 'good',
            'timestamp': time.time(),
            'timestamp_human': time.strftime('%Y-%m-%d %H:%M:%S'),
            'server_info': 'threaded_server',
            'config_source': 'yaml'
        }
        self.send_json_response(200, response)

    def serve_check_one(self, host_ip):
        try:
            cache_key = 'One'
            life_thresh = str(self.config.get('monitoring.default_life_threshold', 80))

            # Проверяем, нужно ли обновлять данные
            if self.cache_manager.should_update(host_ip, cache_key, life_thresh):
                # Используем блокировку для конкретного ключа, чтобы избежать дублирующих обновлений
                with self.cache_manager.get_update_lock(host_ip, cache_key, life_thresh):
                    # Двойная проверка после получения блокировки
                    if self.cache_manager.should_update(host_ip, cache_key, life_thresh):
                        result = self.run_check(host_ip, cache_key, life_thresh)
                        self.cache_manager.update_cache(host_ip, cache_key, result, life_thresh)
                    else:
                        result = self.cache_manager.get_cached_data(host_ip, cache_key, life_thresh)
                        result = result.copy()
                        result['cached'] = True
            else:
                result = self.cache_manager.get_cached_data(host_ip, cache_key, life_thresh)
                result = result.copy()  # Создаем копию чтобы не менять кэш
                result['cached'] = True

            # Добавляем временные метки кэша если данные из кэша
            if result.get('cached'):
                result['cache_timestamp'] = time.time()
                result['cache_timestamp_human'] = time.strftime('%Y-%m-%d %H:%M:%S')

            response = {
                'host': host_ip,
                'check_type': 'One',
                'description': 'Status checks (disks, ports, nodes, PSU, cages)',
                'timestamp': time.time(),
                'timestamp_human': time.strftime('%Y-%m-%d %H:%M:%S'),
                **result
            }
            self.send_json_response(200, response)
        except Exception as e:
            self.log_message("Error in check one for %s: %s", host_ip, str(e))
            try:
                self.send_error(500, f"Internal error for host {host_ip}: {str(e)}")
            except:
                pass  # Игнорируем если клиент отключился

    def serve_check_two(self, host_ip, life_thresh):
        try:
            cache_key = 'Two'

            # Проверяем, нужно ли обновлять данные
            if self.cache_manager.should_update(host_ip, cache_key, life_thresh):
                # Используем блокировку для конкретного ключа, чтобы избежать дублирующих обновлений
                with self.cache_manager.get_update_lock(host_ip, cache_key, life_thresh):
                    # Двойная проверка после получения блокировки
                    if self.cache_manager.should_update(host_ip, cache_key, life_thresh):
                        result = self.run_check(host_ip, cache_key, life_thresh)
                        self.cache_manager.update_cache(host_ip, cache_key, result, life_thresh)
                    else:
                        result = self.cache_manager.get_cached_data(host_ip, cache_key, life_thresh)
                        result = result.copy()
                        result['cached'] = True
            else:
                result = self.cache_manager.get_cached_data(host_ip, cache_key, life_thresh)
                result = result.copy()  # Создаем копию чтобы не менять кэш
                result['cached'] = True

            # Добавляем временные метки кэша если данные из кэша
            if result.get('cached'):
                result['cache_timestamp'] = time.time()
                result['cache_timestamp_human'] = time.strftime('%Y-%m-%d %H:%M:%S')

            response = {
                'host': host_ip,
                'check_type': 'Two',
                'description': 'Performance and capacity checks (CIM, capacity, VV/LD stats, disk life)',
                'lifetresh_used': life_thresh,
                'timestamp': time.time(),
                'timestamp_human': time.strftime('%Y-%m-%d %H:%M:%S'),
                **result
            }
            self.send_json_response(200, response)
        except Exception as e:
            self.log_message("Error in check two for %s: %s", host_ip, str(e))
            try:
                self.send_error(500, f"Internal error for host {host_ip}: {str(e)}")
            except:
                pass  # Игнорируем если клиент отключился

    def serve_check_all(self, host_ip):
        try:
            start_time = time.time()

            # Получаем данные для check one
            cache_key_one = 'One'
            life_thresh_one = str(self.config.get('monitoring.default_life_threshold', 80))

            if self.cache_manager.should_update(host_ip, cache_key_one, life_thresh_one):
                with self.cache_manager.get_update_lock(host_ip, cache_key_one, life_thresh_one):
                    if self.cache_manager.should_update(host_ip, cache_key_one, life_thresh_one):
                        result_one = self.run_check(host_ip, cache_key_one, life_thresh_one)
                        self.cache_manager.update_cache(host_ip, cache_key_one, result_one, life_thresh_one)
                        result_one_cached = False
                    else:
                        result_one = self.cache_manager.get_cached_data(host_ip, cache_key_one, life_thresh_one)
                        result_one = result_one.copy()
                        result_one_cached = True
            else:
                result_one = self.cache_manager.get_cached_data(host_ip, cache_key_one, life_thresh_one)
                result_one = result_one.copy()
                result_one_cached = True

            # Получаем данные для check two
            cache_key_two = 'Two'
            life_thresh_two = str(self.config.get('monitoring.default_life_threshold', 80))

            if self.cache_manager.should_update(host_ip, cache_key_two, life_thresh_two):
                with self.cache_manager.get_update_lock(host_ip, cache_key_two, life_thresh_two):
                    if self.cache_manager.should_update(host_ip, cache_key_two, life_thresh_two):
                        result_two = self.run_check(host_ip, cache_key_two, life_thresh_two)
                        self.cache_manager.update_cache(host_ip, cache_key_two, result_two, life_thresh_two)
                        result_two_cached = False
                    else:
                        result_two = self.cache_manager.get_cached_data(host_ip, cache_key_two, life_thresh_two)
                        result_two = result_two.copy()
                        result_two_cached = True
            else:
                result_two = self.cache_manager.get_cached_data(host_ip, cache_key_two, life_thresh_two)
                result_two = result_two.copy()
                result_two_cached = True

            total_time = round(time.time() - start_time, 2)

            response = {
                'host': host_ip,
                'check_type': 'All',
                'description': 'Both One and Two checks',
                'timestamp': time.time(),
                'timestamp_human': time.strftime('%Y-%m-%d %H:%M:%S'),
                'total_execution_time': total_time,
                'checks': {
                    'one': result_one,
                    'two': result_two
                },
                'overall_success': result_one['success'] and result_two['success'],
                'cache_info': {
                    'one_cached': result_one_cached,
                    'two_cached': result_two_cached
                }
            }
            self.send_json_response(200, response)
        except Exception as e:
            self.log_message("Error in check all for %s: %s", host_ip, str(e))
            try:
                self.send_error(500, f"Internal error for host {host_ip}: {str(e)}")
            except:
                pass  # Игнорируем если клиент отключился

    def serve_host_config(self, host_ip):
        monitoring_config = self.config.get_monitoring_config()
        config = {
            'host': host_ip,
            'storage_user': monitoring_config['storage_user'],
            'zabbix_host': monitoring_config['zabbix_host_prefix'],
            'default_life_threshold': monitoring_config['default_life_threshold'],
            'monitoring_enabled': True,
            'cache_interval': self.config.get('cache.update_interval'),
            'server_type': 'threaded',
            'config_source': 'yaml'
        }
        self.send_json_response(200, config)

    def run_check(self, host_ip, check_type, life_thresh='80'):
        start_time = time.time()
        monitoring_config = self.config.get_monitoring_config()

        cmd = [
            'python3', './hp3_primera_monitoring.py',
            '--zabhost', monitoring_config['zabbix_host_prefix'],
            '--host', host_ip,
            '--user', monitoring_config['storage_user'],
            '--passw', monitoring_config['storage_password'],
            '--checknum', check_type
        ]

        if check_type == 'Two':
            cmd.extend(['--lifetresh', life_thresh])

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True
            )

            script_timeout = monitoring_config.get('script_timeout', 60)
            stdout, stderr = process.communicate(timeout=script_timeout)
            returncode = process.returncode
            execution_time = round(time.time() - start_time, 2)

            # пробуем распарсить stdout как JSON
            try:
                parsed_output = json.loads(stdout)
            except json.JSONDecodeError:
                parsed_output = stdout.strip()

            return {
                'success': returncode == 0,
                'output': parsed_output,
                'error': stderr.strip(),
                'returncode': returncode,
                'execution_time': execution_time,
                'cached': False
            }

        except subprocess.TimeoutExpired:
            execution_time = round(time.time() - start_time, 2)
            return {
                'success': False,
                'output': '',
                'error': f'Timeout expired after {execution_time}s',
                'returncode': -1,
                'execution_time': execution_time,
                'cached': False
            }
        except Exception as e:
            execution_time = round(time.time() - start_time, 2)
            return {
                'success': False,
                'output': '',
                'error': str(e),
                'returncode': -1,
                'execution_time': execution_time,
                'cached': False
            }

    def send_json_response(self, status_code, data):
        try:
            self.send_response(status_code)
            self.send_header('Content-type', 'application/json')
            self.send_header('Connection', 'close')  # Явно закрываем соединение после ответа
            self.end_headers()
            response_data = json.dumps(data, indent=2).encode('utf-8')
            self.wfile.write(response_data)
            self.wfile.flush()  # Принудительно отправляем данные
        except (BrokenPipeError, ConnectionResetError, socket.error) as e:
            # Клиент разорвал соединение - просто логируем и игнорируем
            self.log_message("Client disconnected during response: %s", str(e))
            raise  # Пробрасываем исключение для обработки на верхнем уровне
        except Exception as e:
            self.log_message("Error sending response: %s", str(e))
            raise

    def log_message(self, format, *args):
        client_ip = self.client_address[0]
        thread_id = threading.current_thread().ident
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [Thread-{thread_id}] {client_ip} - {format % args}")

    def handle(self):
        """Переопределяем handle для обработки разрывов соединения"""
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError, socket.error) as e:
            if isinstance(e, socket.error) and e.errno == errno.EPIPE:
                self.log_message("Connection error (broken pipe) in handle: %s", str(e))
            else:
                self.log_message("Connection error in handle: %s", str(e))
        except Exception as e:
            self.log_message("Unexpected error in handle: %s", str(e))


def run_server():
    # Загружаем конфигурацию
    config = Config()
    server_config = config.get_server_config()
    cache_config = config.get_cache_config()
    monitoring_config = config.get_monitoring_config()

    server_address = (server_config['host'], server_config['port'])

    # Создаем менеджер кэша с конфигурацией
    cache_manager = CacheManager(cache_config)

    # Создаем кастомный handler с менеджером кэша и конфигурацией
    def handler(*args, **kwargs):
        return MonitoringHandler(*args, **kwargs, cache_manager=cache_manager, config=config)

    # Используем многопоточный сервер
    httpd = ThreadedHTTPServer(server_address, handler, server_config)

    # Настраиваем сокет
    if server_config.get('reuse_addr', True):
        httpd.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting HP Primera Multi-Host Monitoring Server (Threaded)")
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Configuration: YAML")
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Server: {server_config['host']}:{server_config['port']}")
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Workers: {server_config['workers']}")
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Cache update interval: {cache_config['update_interval']} seconds")
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Script timeout: {monitoring_config.get('script_timeout', 60)} seconds")
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Connection timeout: {server_config['timeout']} seconds")
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Available endpoints:")
    print(f"  http://{server_config['host']}:{server_config['port']}/")
    print(f"  http://{server_config['host']}:{server_config['port']}/health")
    print(f"  http://{server_config['host']}:{server_config['port']}/10.9.132.58/check/one")
    print(f"  http://{server_config['host']}:{server_config['port']}/10.9.132.58/check/two?lifetresh=85")
    print(f"  http://{server_config['host']}:{server_config['port']}/10.9.132.59/check/all")
    print(f"  http://{server_config['host']}:{server_config['port']}/10.9.132.60/health")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Shutting down server...")
    except Exception as e:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Server error: {e}")


if __name__ == '__main__':
    run_server()