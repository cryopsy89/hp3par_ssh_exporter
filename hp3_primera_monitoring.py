#!/opt/hp_3par/venv/python3
#hp3_primera_monitoring.py
#v.3.0.2

import paramiko
import json
import argparse
import sys
import concurrent.futures

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--zabhost", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--passw", required=True)
    parser.add_argument("--lifetresh", default="10")
    parser.add_argument("--checknum", required=True, choices=["One", "Two"])
    return parser.parse_args()

def run_ssh_command(args):
    """Выполняет одну SSH команду и возвращает результат"""
    device, userName, passWord, command = args
    try:
        clConnector = paramiko.SSHClient()
        clConnector.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        clConnector.connect(device, username=userName, password=passWord, timeout=15)
        stdin, stdout, stderr = clConnector.exec_command(command)
        output = stdout.readlines()
        clConnector.close()
        return command, output
    except Exception as e:
        print(f"SSH Error in command {command}: {e}", file=sys.stderr)
        return command, []

def get_data_from_device_one(device, userName, passWord):
    commands = [
        "showpd -showcols CagePos,State",
        "showld -state",
        "showport -state",
        "shownode -state",
        "shownode -ps -showcols Node,PS,PSState",
        "showcage -d",
        "shownodeenv"
    ]

    # Подготавливаем аргументы для параллельного выполнения
    args_list = [(device, userName, passWord, cmd) for cmd in commands]

    dataArray = []

    # Параллельное выполнение всех команд с сохранением порядка
    with concurrent.futures.ThreadPoolExecutor(max_workers=7) as executor:
        # Запускаем все команды параллельно
        future_to_command = {
            executor.submit(run_ssh_command, args): args[3] for args in args_list
        }

        # Создаем словарь для результатов
        results_dict = {}
        for future in concurrent.futures.as_completed(future_to_command):
            command = future_to_command[future]
            try:
                cmd, result = future.result()
                results_dict[command] = result
            except Exception as e:
                print(f"Command failed: {e}", file=sys.stderr)
                results_dict[command] = []

        # Восстанавливаем правильный порядок
        for cmd in commands:
            dataArray.append(results_dict.get(cmd, []))

    return dataArray

def get_data_from_device_two(device, userName, passWord):
    commands = [
        "showcim",
        "showsys -d",
        "statvv -iter 1",
        "statld -iter 1",
        "showpd -e"
    ]

    # Подготавливаем аргументы для параллельного выполнения
    args_list = [(device, userName, passWord, cmd) for cmd in commands]

    dataArray = []

    # Параллельное выполнение всех команд с сохранением порядка
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        # Запускаем все команды параллельно
        future_to_command = {
            executor.submit(run_ssh_command, args): args[3] for args in args_list
        }

        # Создаем словарь для результатов
        results_dict = {}
        for future in concurrent.futures.as_completed(future_to_command):
            command = future_to_command[future]
            try:
                cmd, result = future.result()
                results_dict[command] = result
            except Exception as e:
                print(f"Command failed: {e}", file=sys.stderr)
                results_dict[command] = []

        # Восстанавливаем правильный порядок
        for cmd in commands:
            dataArray.append(results_dict.get(cmd, []))

    return dataArray

def print_metrics(metrics_data):
    """Вывод метрик в формате JSON в консоль"""
    print(json.dumps(metrics_data, indent=2))

def pd_information(device, pdData):
    disksArray, outJS = [], []
    zabbixData = {}

    if not pdData:
        return {device: {"error": "No PD data received"}}

    for element in pdData:
        if element and ":" in element:
            disksArray.append(element.replace("\n","").split())

    pdNotNorm = 0
    for element in disksArray:
        if len(element) >= 2:
            outJS.append({'PDISK': element[0]})
            resState = 0
            if "normal" in element[1]:
                resState = 1
            elif "degraded" in element[1]:
                resState = 2
                pdNotNorm += 1
            elif "failed" in element[1]:
                resState = 3
                pdNotNorm += 1
            elif "new" in element[1]:
                resState = 4
            zabbixData[f"pd_state[{element[0]}]"] = resState

    outputJS = json.dumps({'data': outJS}, indent=4)
    zabbixData.update({
        "disks_array": outputJS,
        "pd_notnormal": pdNotNorm
    })
    return {device: zabbixData}

def ld_information(device, ldData):
    disksArray, outJS = [], []
    zabbixData = {}

    if not ldData:
        return {device: {"error": "No LD data received"}}

    for element in ldData:
        if element and ("-----" not in element) and ("Name" not in element) and ("total" not in element):
            parts = element.replace("\n","").split()
            if len(parts) >= 3:
                disksArray.append(parts)

    ldNotNorm = 0
    for element in disksArray:
        outJS.append({'LDISK': element[1]})
        resState = 0
        if "normal" in element[2]:
            resState = 1
        elif "degraded" in element[2]:
            resState = 2
            ldNotNorm += 1
        elif "failed" in element[2]:
            resState = 3
            ldNotNorm += 1
        zabbixData[f"ld_state[{element[1]}]"] = resState

    outputJS = json.dumps({'data': outJS}, indent=4)
    zabbixData.update({
        "ldisks_array": outputJS,
        "ld_notnormal": ldNotNorm
    })
    return {device: zabbixData}

def port_information(device, pData):
    portsArray, outJS = [], []
    zabbixData = {}

    if not pData:
        return {device: {"error": "No port data received"}}

    for element in pData:
        if element and ("-" not in element) and (":" in element):
            parts = element.replace("\n","").split()
            if len(parts) >= 2:
                portsArray.append(parts)

    for element in portsArray:
        outJS.append({'PORT': element[0]})
        resState = 0
        if "ready" in element[1]:
            resState = 1
        elif "loss_sync" in element[1]:
            resState = 2
        elif "offline" in element[1]:
            resState = 3
        zabbixData[f"port_state[{element[0]}]"] = resState

    outputJS = json.dumps({'data': outJS}, indent=4)
    zabbixData["ports_array"] = outputJS
    return {device: zabbixData}

def node_information(device, nData):
    nodesArray, outJS = [], []
    zabbixData = {}

    if not nData:
        return {device: {"error": "No node data received"}}

    for element in nData:
        if element and "State" not in element:
            parts = element.replace("\n","").split()
            if len(parts) >= 2:
                nodesArray.append(parts)

    for element in nodesArray:
        outJS.append({'NODE': element[0]})
        resState = 0
        state_str = element[1].lower()
        if "ok" in state_str:
            resState = 1
        elif "degraded" in state_str:
            resState = 2
        elif "failed" in state_str:
            resState = 3
        zabbixData[f"node_state[{element[0]}]"] = resState

    outputJS = json.dumps({'data': outJS}, indent=4)
    zabbixData["nodes_array"] = outputJS
    return {device: zabbixData}

def psup_information(device, psupData):
    psupArray, outJS = [], []
    zabbixData = {}

    if not psupData:
        return {device: {"error": "No PSU data received"}}

    for element in psupData:
        if element and ("Node" not in element) and ("------" not in element) and ("total" not in element):
            parts = element.replace("\n","").split()
            if len(parts) >= 3:
                psupArray.append(parts)

    for element in psupArray:
        psName = f"{element[0]}_{element[1]}"
        outJS.append({'PSUP': psName})
        resState = 0
        state_str = element[2].lower()
        if "ok" in state_str:
            resState = 1
        elif "degraded" in state_str:
            resState = 2
        elif "failed" in state_str:
            resState = 3
        zabbixData[f"psup_state[{psName}]"] = resState

    outputJS = json.dumps({'data': outJS}, indent=4)
    zabbixData["psup_array"] = outputJS
    return {device: zabbixData}

def cages_information(device, ccData):
    ccArray, outJS = [], []
    zabbixData = {}

    if not ccData:
        return {device: {"error": "No cage data received"}}

    try:
        for element in ccData:
            if element and ("info for cage" in element or "Interface Board Info" in element or "State(self,partner)" in element):
                ccArray.append(element.replace("\n","").split())
    except Exception:
        pass

    ccNames = ["cage0Card0", "cage0Card1", "cage1Card0", "cage1Card1"]
    for name in ccNames:
        outJS.append({'CC': name})
        zabbixData[f"cc_state[{name}]"] = 1

    try:
        if len(ccArray) > 2 and len(ccArray[2]) > 1:
            if ccArray[2][1] == "OK,OK":
                zabbixData["cc_state[cage0Card0]"] = 1
        if len(ccArray) > 2 and len(ccArray[2]) > 2:
            if ccArray[2][2] == "OK,OK":
                zabbixData["cc_state[cage0Card1]"] = 1
        if len(ccArray) > 5 and len(ccArray[5]) > 1:
            if ccArray[5][1] == "OK,OK":
                zabbixData["cc_state[cage1Card0]"] = 1
        if len(ccArray) > 5 and len(ccArray[5]) > 2:
            if ccArray[5][2] == "OK,OK":
                zabbixData["cc_state[cage1Card1]"] = 1
    except Exception:
        pass

    outputJS = json.dumps({'data': outJS}, indent=4)
    zabbixData["cc_array"] = outputJS
    return {device: zabbixData}

def temperature_information(device, tempData):
    """Обрабатывает данные о температуре из shownodeenv и формирует JSON для Zabbix LLD"""
    nodes = {}

    if not tempData:
        return {device: {"error": "No temperature data received"}}

    current_node = None
    sensor_id = 0

    import re

    for line in tempData:
        if line is None:
            continue
        line = line.strip()
        if not line:
            continue

        # Ищем начало данных нового узла
        if line.startswith("Node "):
            if "No information" not in line:
                current_node = line.split()[1]
                nodes[current_node] = {"sensors": [], "values": {}}
                sensor_id = 0
            else:
                current_node = None
        # Обрабатываем данные температуры для текущего узла
        elif current_node and "C" in line:
            # Улучшенное регулярное выражение для поиска температур
            match = re.search(r'(.+?)\s+([0-9]+\.?[0-9]*)\s*C\s*([0-9]*\.?[0-9]*)?\s*F?', line)
            if match:
                name = match.group(1).strip()
                try:
                    value = float(match.group(2))
                except ValueError:
                    value = None

                # Создаем уникальный ключ для сенсора с указанием ноды
                sensor_key = f"node{current_node}_temp_sensor{sensor_id}"
                nodes[current_node]["values"][sensor_key] = value
                nodes[current_node]["sensors"].append({"TEMP_SENSOR": f"temp_sensor{sensor_id}", "name": name})
                sensor_id += 1

    zabbixData = {}
    for node, data in nodes.items():
        if data["sensors"]:
            # Используем правильный ключ для массива температур
            zabbixData[f"node{node}_temperature_array"] = json.dumps({'data': data["sensors"]}, indent=4)
            # Добавляем значения температур с уникальными ключами для каждой ноды
            zabbixData.update(data["values"])

    return {device: zabbixData}

def cim_information(device, cimData):
    zabbixData = {}

    if not cimData or len(cimData) < 2:
        return {device: {"cim_state": 0, "error": "No CIM data or insufficient data"}}

    cimState = 0
    if "Active" in cimData[1]:
        cimState = 1

    zabbixData["cim_state"] = cimState
    return {device: zabbixData}

def capacity_information(device, capData):
    zabbixData = {}

    if not capData:
        return {device: {"error": "No capacity data received"}}

    capacities = {
        "total_space": "0",
        "allocated_space": "0",
        "free_space": "0",
        "failed_space": "0"
    }

    system_name = "Unknown"

    for element in capData:
        if element:
            if "System Name" in element:
                parts = element.split(":")
                if len(parts) > 1:
                    system_name = parts[1].strip()
            if "Total Capacity" in element:
                parts = element.split(":")
                if len(parts) > 1:
                    capacities["total_space"] = parts[1].replace(" ","").replace("\n","")
            elif "Allocated Capacity" in element:
                parts = element.split(":")
                if len(parts) > 1:
                    capacities["allocated_space"] = parts[1].replace(" ","").replace("\n","")
            elif "Free Capacity" in element:
                parts = element.split(":")
                if len(parts) > 1:
                    capacities["free_space"] = parts[1].replace(" ","").replace("\n","")
            elif "Failed Capacity" in element:
                parts = element.split(":")
                if len(parts) > 1:
                    capacities["failed_space"] = parts[1].replace(" ","").replace("\n","")

    zabbixData.update(capacities)
    zabbixData["system_name"] = system_name
    return {device: zabbixData}


def vv_perfomance(device, vvData):
    vvArray, outJS = [], []
    zabbixData = {}

    if not vvData or len(vvData) < 3:
        return {device: {"error": "No VV performance data or insufficient data"}}

    total_iops = "0"
    total_kbps = "0"

    if len(vvData) >= 3:
        totalBW = vvData[-2].replace("\n","").split()
        if len(totalBW) > 4:
            total_iops = totalBW[2]
            total_kbps = totalBW[4]

    for element in vvData[2:-3]:
        if element:
            parts = element.replace("\n","").split()
            if len(parts) > 5:
                vvArray.append(parts)

    for element in vvArray:
        outJS.append({'VVPERF': element[0]})
        zabbixData[f"vv_iops[{element[0]}]"] = element[2]
        zabbixData[f"vv_kbps[{element[0]}]"] = element[5]

    outputJS = json.dumps({'data': outJS}, indent=4)
    zabbixData.update({
        "vv_perf_array": outputJS,
        "total_iops": total_iops,
        "total_kbps": total_kbps
    })
    return {device: zabbixData}

def ld_perfomance(device, ldData):
    ldArray, outJS = [], []
    zabbixData = {}

    if not ldData or len(ldData) < 3:
        return {device: {"error": "No LD performance data or insufficient data"}}

    for element in ldData[2:-3]:
        if element:
            parts = element.replace("\n","").split()
            if len(parts) > 5:
                ldArray.append(parts)

    for element in ldArray:
        outJS.append({'{#LDPERF}': element[0]})
        zabbixData[f"ld_iops[{element[0]}]"] = element[2]
        zabbixData[f"ld_kbps[{element[0]}]"] = element[5]

    outputJS = json.dumps({'data': outJS}, indent=4)
    zabbixData["ld_perf_array"] = outputJS
    return {device: zabbixData}

def pd_information_life(device, pdData, lTresh):
    disksArray = []
    zabbixData = {}

    if not pdData:
        return {device: {"error": "No PD life data received"}}

    for element in pdData:
        if element and ":" in element:
            parts = element.replace("\n","").split()
            if len(parts) > 9:
                disksArray.append(parts)

    pdNotNorm = 0
    for element in disksArray:
        zabbixData[f"pd_percent_life[{element[1]}]"] = element[9]
        try:
            life_value = element[9].replace("N/A", "99")
            if int(life_value) < int(lTresh):
                pdNotNorm += 1
        except ValueError:
            pass

    zabbixData["pd_life_notnormal"] = pdNotNorm
    return {device: zabbixData}

def main():
    arguments = parse_args()
    try:
        all_metrics = {arguments.zabhost: {}}

        if arguments.checknum == "One":
            deviceData = get_data_from_device_one(arguments.host, arguments.user, arguments.passw)

            if len(deviceData) >= 7:
                pd_data = pd_information(arguments.zabhost, deviceData[0])
                ld_data = ld_information(arguments.zabhost, deviceData[1])
                port_data = port_information(arguments.zabhost, deviceData[2])
                node_data = node_information(arguments.zabhost, deviceData[3])
                psup_data = psup_information(arguments.zabhost, deviceData[4])
                cage_data = cages_information(arguments.zabhost, deviceData[5])
                temp_data = temperature_information(arguments.zabhost, deviceData[6])

                all_metrics[arguments.zabhost].update(pd_data[arguments.zabhost])
                all_metrics[arguments.zabhost].update(ld_data[arguments.zabhost])
                all_metrics[arguments.zabhost].update(port_data[arguments.zabhost])
                all_metrics[arguments.zabhost].update(node_data[arguments.zabhost])
                all_metrics[arguments.zabhost].update(psup_data[arguments.zabhost])
                all_metrics[arguments.zabhost].update(cage_data[arguments.zabhost])
                all_metrics[arguments.zabhost].update(temp_data[arguments.zabhost])

            else:
                all_metrics[arguments.zabhost] = {"error": "Incomplete data received from device"}

        elif arguments.checknum == "Two":
            deviceData = get_data_from_device_two(arguments.host, arguments.user, arguments.passw)

            if len(deviceData) >= 5:
                cim_data = cim_information(arguments.zabhost, deviceData[0])
                capacity_data = capacity_information(arguments.zabhost, deviceData[1])
                vv_data = vv_perfomance(arguments.zabhost, deviceData[2])
                ld_perf_data = ld_perfomance(arguments.zabhost, deviceData[3])
                pd_life_data = pd_information_life(arguments.zabhost, deviceData[4], arguments.lifetresh)

                all_metrics[arguments.zabhost].update(cim_data[arguments.zabhost])
                all_metrics[arguments.zabhost].update(capacity_data[arguments.zabhost])
                all_metrics[arguments.zabhost].update(vv_data[arguments.zabhost])
                all_metrics[arguments.zabhost].update(ld_perf_data[arguments.zabhost])
                all_metrics[arguments.zabhost].update(pd_life_data[arguments.zabhost])
            else:
                all_metrics[arguments.zabhost] = {"error": "Incomplete data received from device"}

        print_metrics(all_metrics)

    except Exception as e:
        error_msg = {"error": f"Script execution failed: {str(e)}"}
        print(json.dumps(error_msg, indent=2))
        sys.exit(1)

if __name__ == '__main__':
    main()
