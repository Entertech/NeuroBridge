#!/usr/bin/env bash
set -euo pipefail
[[ ${EUID} -eq 0 ]] || { echo "Run as root." >&2; exit 1; }
systemctl disable --now neurobridge.service 2>/dev/null || true
rm -f /etc/systemd/system/neurobridge.service
systemctl daemon-reload
rm -rf /opt/neurobridge
# /etc/neurobridge and /var/lib/neurobridge are intentionally retained.
