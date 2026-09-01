#!/usr/bin/env bash
# One-command serial strategy configuration for an installed Kylin gateway.
set -euo pipefail

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

[[ ${EUID:-$(id -u)} -eq 0 ]] || fail "Run with sudo."
[[ $(uname -m) == x86_64 ]] || fail "The confirmed Kylin deployment requires x86_64."
[[ -r /etc/os-release ]] || fail "/etc/os-release is unavailable."
. /etc/os-release
[[ ${ID,,} == kylin ]] || fail "This helper is only for Galaxy Kylin; detected ID=${ID:-unknown}."

root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
config_path=/etc/neurobridge/gateway.toml
device=auto
restart_service=true
while [[ $# -gt 0 ]]; do
  case $1 in
    --config)
      [[ $# -ge 2 ]] || fail "--config requires a path"
      config_path=$2
      shift 2
      ;;
    --device)
      [[ $# -ge 2 ]] || fail "--device requires auto or an absolute TTY path"
      device=$2
      shift 2
      ;;
    --no-restart)
      restart_service=false
      shift
      ;;
    -h|--help)
      echo "Usage: sudo $0 [--config PATH] [--device auto|/dev/tty...] [--no-restart]"
      exit 0
      ;;
    *)
      fail "Unknown option: $1"
      ;;
  esac
done
[[ $config_path == /* ]] || fail "--config must be an absolute path"
[[ $device == auto || $device == /* ]] || fail "--device must be auto or an absolute path"

python_path=
for candidate in /opt/neurobridge/venv/bin/python "$root_dir/.venv/bin/python"; do
  if [[ -x $candidate ]]; then
    python_path=$candidate
    break
  fi
done
[[ -n $python_path ]] || fail "NeuroBridge Python environment was not found under /opt/neurobridge/venv or the source checkout."
PYTHONPATH=$root_dir "$python_path" -c 'import neurobridge, serial' \
  || fail "The runtime is missing NeuroBridge or pyserial. Install requirements.lock before configuring serial."

getent group neurobridge >/dev/null 2>&1 || groupadd --system neurobridge
id -u neurobridge >/dev/null 2>&1 || useradd --system --gid neurobridge --home /nonexistent --shell /usr/sbin/nologin neurobridge
install -d -o root -g neurobridge -m 0750 "$(dirname "$config_path")"
if [[ ! -e $config_path ]]; then
  [[ -f $root_dir/config/gateway.toml.example ]] || fail "Missing config/gateway.toml.example"
  install -o root -g neurobridge -m 0640 "$root_dir/config/gateway.toml.example" "$config_path"
fi
[[ -f $config_path && ! -L $config_path ]] || fail "Configuration must be a regular file, not a symlink: $config_path"

backup_path="${config_path}.before-serial-$(date -u +%Y%m%dT%H%M%SZ)"
cp -p -- "$config_path" "$backup_path"
PYTHONPATH=$root_dir "$python_path" -m neurobridge.serial_setup \
  --config "$config_path" \
  --device "$device"
chown root:neurobridge "$config_path" "$backup_path"
chmod 0640 "$config_path" "$backup_path"

groups_added=()
shopt -s nullglob
tty_candidates=(/dev/ttyACM* /dev/ttyUSB*)
shopt -u nullglob
if (( ${#tty_candidates[@]} )); then
  for tty_path in "${tty_candidates[@]}"; do
    tty_group=$(stat -Lc '%G' "$tty_path" 2>/dev/null || true)
    if [[ -n $tty_group && $tty_group != root ]] && getent group "$tty_group" >/dev/null 2>&1; then
      usermod -aG "$tty_group" neurobridge
      groups_added+=("$tty_group")
    fi
  done
else
  echo "WARNING: no ttyACM/ttyUSB device exists; serial permissions cannot be verified yet." >&2
  echo "Run before setup: sudo $root_dir/linux/diagnose-kylin-usb-serial.sh --timeout 60" >&2
  for tty_group in dialout uucp; do
    if getent group "$tty_group" >/dev/null 2>&1; then
      usermod -aG "$tty_group" neurobridge
      groups_added+=("$tty_group")
    fi
  done
fi

PYTHONPATH=$root_dir "$python_path" -m neurobridge.serial_setup --config "$config_path" --check-only
if [[ $restart_service == true && -x /opt/neurobridge/venv/bin/neurobridge && -f /etc/systemd/system/neurobridge.service ]]; then
  systemctl daemon-reload
  systemctl enable neurobridge.service >/dev/null
  systemctl restart neurobridge.service
  service_state=$(systemctl is-active neurobridge.service || true)
else
  service_state=not-restarted
fi

echo "Serial setup complete"
echo "config=$config_path"
echo "backup=$backup_path"
echo "device=$device"
echo "groups=${groups_added[*]:-none}"
echo "service=$service_state"
echo "logs=sudo journalctl -u neurobridge.service -f -o short-iso-precise"
