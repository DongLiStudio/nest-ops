#!/bin/sh
set -eu
[ "$(id -u)" = 0 ] || { echo 'Run as root'; exit 1; }
systemctl disable --now nestops-collector.service
rm -f /etc/systemd/system/nestops-collector.service
systemctl daemon-reload
rm -f /opt/nestops-collector/collector.py
printf '%s\n' 'Collector removed. Private config retained at /opt/nestops-collector/config.json; revoke its token at the center.'
