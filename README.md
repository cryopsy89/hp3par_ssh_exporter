# HP 3PAR / Primera Exporter

Prometheus exporter for HPE 3PAR and Primera storage systems.
Collects metrics via SSH using the HPE CLI and exposes them
in Prometheus format.

## Why this exists

HPE 3PAR and Primera have no official Prometheus exporter.
This tool fills that gap by connecting over SSH, parsing CLI output,
and exposing storage metrics for Grafana dashboards and alerting.

## Features

- Collects volume, disk, node, port, and system metrics
- SSH-based — no additional agents required on the storage system
- Two modes: full monitoring and lightweight (low-overhead) polling
- Configurable via YAML
- Docker-ready

## Metrics exposed

| Metric | Description |
|---|---|
| `hp3par_system_info` | System name, model, serial |
| `hp3par_node_state` | Controller node status |
| `hp3par_disk_state` | Physical disk status |
| `hp3par_volume_size_bytes` | Allocated volume size |
| `hp3par_port_state` | FC/iSCSI port status |

## Quick start

```bash
git clone https://github.com/cryopsy89/hp3par_ssh_exporter
cd hp3par_ssh_exporter
cp config.yaml.example config.yaml
# Edit config.yaml with your 3PAR credentials
docker compose up -d
```

For low-overhead environments:
```bash
python lightweight_monitoring.py
```

## Tech stack

Python · Paramiko (SSH) · Docker
