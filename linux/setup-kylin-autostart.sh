#!/usr/bin/env bash
# Install and manage a project-local NeuroBridge systemd service on Galaxy Kylin.
set -euo pipefail

unit_name=neurobridge.service
unit_path="/etc/systemd/system/$unit_name"
managed_marker="# Managed by NeuroBridge Galaxy Kylin project autostart"
root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
runtime_dir="$root_dir/.runtime"
start_script="$root_dir/linux/start-kylin-gateway.sh"
config_path="$runtime_dir/config/gateway.toml"
action=${1:-}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: ./linux/setup-kylin-autostart.sh enable|status|disable

  enable   Install/update neurobridge.service, enable it for every boot, and
           start it now as the current desktop user.
  status   Show whether the service is enabled and running, plus recent status.
  disable  Stop the service and disable boot startup; keep the unit file so it
           can be enabled again.

Run as the normal desktop user. The script requests sudo only for systemd
changes. The gateway, algorithm, configuration, recordings, and logs remain in
this project checkout; the service never runs the gateway as root.
EOF
}

systemd_quote() {
  local value=$1
  value=${value//\\/\\\\}
  value=${value//\"/\\\"}
  value=${value//%/%%}
  printf '"%s"' "$value"
}

show_status() {
  local enabled_state active_state
  enabled_state=$(systemctl is-enabled "$unit_name" 2>/dev/null || true)
  active_state=$(systemctl is-active "$unit_name" 2>/dev/null || true)
  printf 'service=%s\nenabled=%s\nactive=%s\n' \
    "$unit_name" "${enabled_state:-not-installed}" "${active_state:-inactive}"
  systemctl status "$unit_name" --no-pager -l 2>&1 || true
}

if [[ $action == -h || $action == --help || -z $action ]]; then
  usage
  [[ -n $action ]] && exit 0
  exit 2
fi
[[ $# -eq 1 ]] || fail "Expected exactly one action: enable, status, or disable."
case $action in
  enable|status|disable) ;;
  *) fail "Unknown action: $action" ;;
esac

[[ ${EUID:-$(id -u)} -ne 0 ]] || fail \
  "Run without sudo: ./linux/setup-kylin-autostart.sh $action"
[[ -n $root_dir && $root_dir != / && -f $root_dir/pyproject.toml ]] || fail \
  "Invalid NeuroBridge project root: $root_dir"
[[ -d $root_dir/.git && ! -L $root_dir/.git ]] || fail \
  "A complete NeuroBridge Git checkout is required: $root_dir"
[[ ! -L $runtime_dir ]] || fail ".runtime must be a real directory, not a symlink."
command -v systemctl >/dev/null 2>&1 || fail "systemctl is unavailable."
[[ -d /run/systemd/system ]] || fail "systemd is not running on this computer."

if [[ $action == status ]]; then
  show_status
  exit 0
fi

command -v sudo >/dev/null 2>&1 || fail "sudo is required for systemd changes."
[[ -r /etc/os-release ]] || fail "/etc/os-release is unavailable."
. /etc/os-release
[[ ${ID,,} == kylin ]] || fail "This helper requires Galaxy Kylin; detected ID=${ID:-unknown}."
[[ $(uname -m) == x86_64 ]] || fail "This deployment requires x86_64; detected $(uname -m)."

if [[ $action == disable ]]; then
  if ! sudo test -e "$unit_path"; then
    if systemctl cat "$unit_name" >/dev/null 2>&1; then
      fail "$unit_name exists outside $unit_path and is not managed by this project; refusing to stop it."
    fi
    printf 'NeuroBridge boot startup is already disabled; no managed unit is installed.\n'
    show_status
    exit 0
  fi
  sudo grep -Fqx "$managed_marker" "$unit_path" || fail \
    "$unit_path exists but is not managed by the Kylin project installer; refusing to stop it."
  sudo systemctl disable --now "$unit_name" || fail \
    "Could not stop and disable $unit_name. Inspect it with: sudo systemctl status $unit_name"
  printf 'NeuroBridge boot startup disabled.\n'
  show_status
  exit 0
fi

[[ -x $start_script && ! -L $start_script ]] || fail "Startup script is missing or unsafe: $start_script"
[[ -f $config_path && ! -L $config_path ]] || fail \
  "Project configuration is missing. Choose menu 1 before enabling autostart."
python_path=
for candidate in "$root_dir/.venv/bin/python" "$root_dir/venv/bin/python"; do
  if [[ -x $candidate ]]; then
    python_path=$candidate
    break
  fi
done
[[ -n $python_path ]] || fail "Project Python is missing. Choose menu 1 before enabling autostart."
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$root_dir "$python_path" - \
  "$config_path" "$runtime_dir" <<'PY' >/dev/null
import os
import sys
import tomllib
from pathlib import Path

config_path, runtime_dir = map(Path, sys.argv[1:])
config = tomllib.loads(config_path.read_text(encoding="utf-8"))
algorithm = config.get("algorithm", {})
command = algorithm.get("command", [])
if algorithm.get("enabled") is not True or len(command) != 1:
    raise SystemExit("algorithm is not enabled with one project bridge command")
bridge = Path(command[0]).resolve()
algorithm_root = (runtime_dir / "algorithm").resolve()
try:
    bridge.relative_to(algorithm_root)
except ValueError as error:
    raise SystemExit(f"algorithm bridge must remain below {algorithm_root}: {bridge}") from error
if bridge.is_symlink() or not bridge.is_file() or not os.access(bridge, os.X_OK):
    raise SystemExit(f"algorithm bridge is not an executable regular file: {bridge}")
PY

service_user=$(id -un)
service_group=$(id -gn)
[[ $service_user =~ ^[a-zA-Z_][a-zA-Z0-9_.-]*\$?$ ]] || fail \
  "Unsupported service user name: $service_user"
[[ $service_group =~ ^[a-zA-Z_][a-zA-Z0-9_.-]*\$?$ ]] || fail \
  "Unsupported service group name: $service_group"

install -d -m 0750 "$runtime_dir" "$runtime_dir/tmp" "$runtime_dir/backups"
unit_tmp_dir=$(mktemp -d "$runtime_dir/tmp/neurobridge-kylin-service.XXXXXX")
unit_tmp="$unit_tmp_dir/$unit_name"
cleanup() {
  rm -f -- "$unit_tmp"
  rmdir -- "$unit_tmp_dir" 2>/dev/null || true
}
trap cleanup EXIT

quoted_root=$(systemd_quote "$root_dir")
quoted_start=$(systemd_quote "$start_script")
cat >"$unit_tmp" <<EOF
$managed_marker
[Unit]
Description=NeuroBridge Galaxy Kylin USB serial gateway
After=local-fs.target systemd-udev-settle.service
Wants=systemd-udev-settle.service
StartLimitIntervalSec=60
StartLimitBurst=10

[Service]
Type=simple
User=$service_user
Group=$service_group
WorkingDirectory=$quoted_root
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=$quoted_start
Restart=on-failure
RestartSec=3
TimeoutStopSec=20
UMask=0077
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=full
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes

[Install]
WantedBy=multi-user.target
EOF
chmod 0600 "$unit_tmp"

if command -v systemd-analyze >/dev/null 2>&1; then
  systemd-analyze verify "$unit_tmp" >/dev/null \
    || fail "Generated systemd unit failed verification; no system file was changed."
fi
if sudo test -e "$unit_path"; then
  sudo grep -Fqx "$managed_marker" "$unit_path" || fail \
    "$unit_path exists but is not managed by the Kylin project installer; refusing to overwrite it."
  backup_path="$runtime_dir/backups/neurobridge.service.$(date -u +%Y%m%dT%H%M%SZ).before-autostart"
  sudo cat "$unit_path" >"$backup_path"
  chmod 0600 "$backup_path"
  printf 'previousUnitBackup=%s\n' "$backup_path"
fi

sudo install -o root -g root -m 0644 "$unit_tmp" "$unit_path"
sudo systemctl daemon-reload
sudo systemctl enable "$unit_name"
if systemctl is-active --quiet "$unit_name" 2>/dev/null; then
  printf 'serviceAlreadyActive=true; unit changes take effect on the next service start.\n'
elif ! sudo systemctl start "$unit_name"; then
  printf 'ERROR: service was installed and enabled, but immediate startup failed.\n' >&2
  show_status
  printf 'Logs: sudo journalctl -u %s -n 300 --no-pager -o short-iso-precise\n' "$unit_name" >&2
  exit 1
fi

printf 'NeuroBridge boot startup configured.\n'
printf 'project=%s\nrunAs=%s:%s\nunit=%s\n' \
  "$root_dir" "$service_user" "$service_group" "$unit_path"
show_status
printf 'Browser: http://127.0.0.1:8080/\n'
printf 'Capture: http://127.0.0.1:8080/capture/\n'
printf 'Logs: sudo journalctl -u %s -f -o short-iso-precise\n' "$unit_name"
