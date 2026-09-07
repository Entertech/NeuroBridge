#!/usr/bin/env bash
set -euo pipefail
[[ ${EUID} -eq 0 ]] || { echo "Run as root." >&2; exit 1; }
[[ $(uname -m) == x86_64 ]] || { echo "Kylin package requires x86_64." >&2; exit 1; }
grep -Eqi 'kylin|银河麒麟' /etc/os-release || { echo "This package is restricted to Galaxy Kylin V10." >&2; exit 1; }
package_root=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
[[ -x "$package_root/payload/runtime/bin/python" ]] || { echo "Candidate has no bundled runtime; rebuild with --runtime-dir." >&2; exit 1; }
[[ -x "$package_root/payload/runtime/bin/neurobridge_affective_bridge" ]] || { echo "Candidate runtime has no executable algorithm bridge." >&2; exit 1; }
getent group neurobridge >/dev/null 2>&1 || groupadd --system neurobridge
id -u neurobridge >/dev/null 2>&1 || useradd --system --gid neurobridge --home /nonexistent --shell /usr/sbin/nologin neurobridge
serial_group_added=false
for device in /dev/ttyACM* /dev/ttyUSB*; do
  [[ -c $device ]] || continue
  device_group=$(stat -Lc '%G' -- "$device")
  [[ $device_group != root ]] || continue
  getent group "$device_group" >/dev/null 2>&1 || continue
  usermod -aG "$device_group" neurobridge
  serial_group_added=true
done
[[ $serial_group_added == true ]] || { echo "No USB serial device group was found; connect the headset and rerun the installer." >&2; exit 1; }
install -d -o root -g neurobridge -m 0750 /etc/neurobridge
install -d -o neurobridge -g neurobridge -m 0750 /var/lib/neurobridge/recordings /var/log/neurobridge
staging=/opt/neurobridge.new.$$
previous=/opt/neurobridge.previous
rm -rf "$staging"
install -d -m 0755 "$staging"
cp -a "$package_root/payload/." "$staging/"
"$staging/runtime/bin/python" -m compileall -q "$staging/neurobridge"
rm -rf "$previous"
if [[ -d /opt/neurobridge ]]; then
  mv /opt/neurobridge "$previous"
fi
if ! mv "$staging" /opt/neurobridge; then
  [[ ! -d "$previous" ]] || mv "$previous" /opt/neurobridge
  exit 1
fi
if [[ ! -e /etc/neurobridge/gateway.toml ]]; then
  install -o root -g neurobridge -m 0640 "$package_root/gateway.toml.example" /etc/neurobridge/gateway.toml
fi
"/opt/neurobridge/runtime/bin/python" "/opt/neurobridge/neurobridge/configuration/migration.py" \
  /etc/neurobridge/gateway.toml \
  --backup-directory /etc/neurobridge/backups \
  --history-path /var/lib/neurobridge/config-migration-history.jsonl
install -m 0644 "$package_root/neurobridge.service" /etc/systemd/system/neurobridge.service
systemctl daemon-reload
if ! systemctl enable --now neurobridge.service; then
  systemctl disable --now neurobridge.service 2>/dev/null || true
  rm -rf /opt/neurobridge
  [[ ! -d "$previous" ]] || mv "$previous" /opt/neurobridge
  systemctl enable --now neurobridge.service 2>/dev/null || true
  exit 1
fi
rm -rf "$previous"
