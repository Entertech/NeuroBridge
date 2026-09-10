#!/usr/bin/env bash
set -euo pipefail
[[ ${EUID} -eq 0 ]] || { echo "Run as root." >&2; exit 1; }
[[ $(uname -m) == x86_64 ]] || { echo "Kylin package requires x86_64." >&2; exit 1; }
. /etc/os-release
[[ "${ID:-} ${NAME:-}" =~ [Kk][Yy][Ll][Ii][Nn]|银河麒麟 ]] && [[ ${VERSION_ID:-} =~ ^[Vv]?10([.]|$) ]] || {
  echo "This package is restricted to Galaxy Kylin V10." >&2; exit 1;
}
package_root=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# Verify every payload, configuration and installer file before executing it.
(cd "$package_root" && sha256sum --strict --check metadata/files.sha256) || {
  echo "Candidate integrity verification failed." >&2; exit 1;
}
required_kib=$(du -sk "$package_root/payload" | awk '{print $1}')
free_kib=$(df -Pk /opt | awk 'NR==2 {print $4}')
[[ $free_kib -gt $((required_kib * 2 + 102400)) ]] || { echo "Insufficient installation/rollback disk space." >&2; exit 1; }
[[ -x "$package_root/payload/runtime/bin/python" ]] || { echo "Candidate has no bundled runtime; rebuild with --runtime-dir." >&2; exit 1; }
[[ -x "$package_root/payload/runtime/bin/neurobridge_affective_bridge" ]] || { echo "Candidate runtime has no executable algorithm bridge." >&2; exit 1; }
exec 9>/run/lock/neurobridge-install.lock
flock -n 9 || { echo "Another installation is running." >&2; exit 1; }
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
staging=$(mktemp -d /opt/neurobridge-stage.XXXXXX)
cp -a "$package_root/payload/." "$staging/"
"$staging/runtime/bin/python" -m compileall -q "$staging/neurobridge"
previous=$(mktemp -d /opt/neurobridge-rollback.XXXXXX)
# Snapshot all upgrade state before stopping or replacing the installed service.
[[ ! -e /etc/neurobridge/gateway.toml ]] || cp -a /etc/neurobridge/gateway.toml "$previous/gateway.toml"
[[ ! -e /etc/systemd/system/neurobridge.service ]] || cp -a /etc/systemd/system/neurobridge.service "$previous/neurobridge.service"
systemctl is-active --quiet neurobridge.service && touch "$previous/was-active" || true
systemctl is-enabled --quiet neurobridge.service && touch "$previous/was-enabled" || true
[[ ! -d /opt/neurobridge ]] || touch "$previous/had-app"
install -m 0700 "$package_root/rollback.sh" "$previous/rollback.sh"
committed=false
restore_on_failure() {
  code=$?
  trap - EXIT
  if [[ $committed != true ]]; then
    echo "Installation failed; restoring application, configuration and service from $previous" >&2
    bash "$previous/rollback.sh" "$previous" || echo "Automatic rollback failed; retained backup: $previous" >&2
  fi
  exit "$code"
}
trap restore_on_failure EXIT
if systemctl cat neurobridge.service >/dev/null 2>&1; then
  systemctl stop neurobridge.service
fi
if [[ -d /opt/neurobridge ]]; then
  mv /opt/neurobridge "$previous/app"
fi
mv "$staging" /opt/neurobridge
if [[ ! -e /etc/neurobridge/gateway.toml ]]; then
  install -o root -g neurobridge -m 0640 "$package_root/gateway.toml.example" /etc/neurobridge/gateway.toml
fi
"/opt/neurobridge/runtime/bin/python" "/opt/neurobridge/neurobridge/configuration/migration.py" \
  /etc/neurobridge/gateway.toml \
  --backup-directory /etc/neurobridge/backups \
  --history-path /var/lib/neurobridge/config-migration-history.jsonl
install -m 0644 "$package_root/neurobridge.service" /etc/systemd/system/neurobridge.service
# Validate the complete migrated configuration and immutable platform mapping
# before systemd can open the device. The old files are still available for rollback.
(cd /opt/neurobridge && runtime/bin/python -c 'from neurobridge.configuration.runtime import load_runtime_config; from neurobridge.profiles.resolver import resolve_profile; resolve_profile(load_runtime_config("/etc/neurobridge/gateway.toml"))')
systemctl daemon-reload
if [[ ! -e "$previous/had-app" || -e "$previous/was-enabled" ]]; then
  systemctl enable neurobridge.service
fi
if [[ ! -e "$previous/had-app" || -e "$previous/was-active" ]]; then
  systemctl start neurobridge.service
  systemctl is-active --quiet neurobridge.service
fi
committed=true
echo "Installation completed. Rollback snapshot retained (recordings are untouched): $previous"
echo "To roll back: sudo bash $previous/rollback.sh $previous"
