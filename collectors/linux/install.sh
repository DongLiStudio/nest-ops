#!/bin/sh
set -eu
[ "$(id -u)" = 0 ] || { echo 'Run as root'; exit 1; }
[ "$#" = 1 ] || { echo 'Usage: install.sh /absolute/config.json'; exit 1; }
case "$1" in /*) ;; *) echo 'Absolute config path required'; exit 1;; esac
src=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python3 -c 'import json,sys; c=json.load(open(sys.argv[1])); assert c["endpoint"].startswith("https://"); assert c["token"] and c["server"]' "$1"
[ ! -e /opt/nestops-collector/config.json ] || { echo 'Existing collector; upgrade explicitly'; exit 1; }
install -d -m 700 /opt/nestops-collector
install -m 600 "$1" /opt/nestops-collector/config.json
install -m 644 "$src/collector.py" /opt/nestops-collector/collector.py
cat > /etc/systemd/system/nestops-collector.service <<'UNIT'
[Unit]
Description=NestOps allowlisted read-only collector
After=network-online.target
Wants=network-online.target
[Service]
ExecStart=/usr/bin/python3 -B /opt/nestops-collector/collector.py /opt/nestops-collector/config.json
Restart=always
RestartSec=15
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=true
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now nestops-collector.service
