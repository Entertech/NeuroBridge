#!/usr/bin/env bash
# One-command USB serial strategy configuration for Galaxy Kylin.
# Default mode keeps generated configuration, backups, logs, and data paths in
# the current checkout. --system-install preserves the formal systemd layout.
set -euo pipefail

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

contains_value() {
  local expected=$1 value
  shift
  for value in "$@"; do
    [[ $value == "$expected" ]] && return 0
  done
  return 1
}

usage() {
  cat <<EOF
Usage: sudo $0 [options]

Options:
  --device auto|/dev/tty...   Serial candidate or fixed absolute TTY path
  --python /absolute/python   Explicit Python 3.11+ executable
  --config /absolute/path     Override generated configuration path
  --system-install            Use /etc + /opt systemd installation layout
  --no-restart                Do not restart an installed systemd service
  -h, --help                  Show this help

Default project mode writes only below:
  $(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)/.runtime/
EOF
}

root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
config_path=
device=auto
restart_service=true
python_override=
system_install=false
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
    --python)
      [[ $# -ge 2 ]] || fail "--python requires an absolute Python executable path"
      python_override=$2
      shift 2
      ;;
    --system-install)
      system_install=true
      shift
      ;;
    --no-restart)
      restart_service=false
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "Unknown option: $1"
      ;;
  esac
done

[[ $device == auto || $device == /* ]] || fail "--device must be auto or an absolute path"
[[ -z $python_override || $python_override == /* ]] || fail "--python must be an absolute path"
[[ ${EUID:-$(id -u)} -eq 0 ]] || fail "Run with sudo."
[[ $(uname -m) == x86_64 ]] || fail "The confirmed Kylin deployment requires x86_64."
[[ -r /etc/os-release ]] || fail "/etc/os-release is unavailable."
. /etc/os-release
[[ ${ID,,} == kylin ]] || fail "This helper is only for Galaxy Kylin; detected ID=${ID:-unknown}."

runtime_dir="$root_dir/.runtime"
invoking_uid=${SUDO_UID:-0}
invoking_gid=${SUDO_GID:-0}
invoking_user=${SUDO_USER:-root}
[[ $invoking_uid =~ ^[0-9]+$ ]] || invoking_uid=0
[[ $invoking_gid =~ ^[0-9]+$ ]] || invoking_gid=0
if [[ -z $config_path ]]; then
  if [[ $system_install == true ]]; then
    config_path=/etc/neurobridge/gateway.toml
  else
    config_path="$runtime_dir/config/gateway.toml"
  fi
fi
[[ $config_path == /* ]] || fail "--config must be an absolute path"
if [[ $system_install != true ]]; then
  command -v realpath >/dev/null 2>&1 || fail "Required command is unavailable: realpath"
  runtime_dir=$(realpath -m -- "$runtime_dir")
  config_path=$(realpath -m -- "$config_path")
  case $config_path in
    "$runtime_dir"/config/*) ;;
    *) fail "Project mode --config must stay under: $runtime_dir/config" ;;
  esac
fi

python_path=
runtime_ready=false
python_candidates=()
if [[ -n $python_override ]]; then
  python_candidates+=("$python_override")
else
  [[ -n ${VIRTUAL_ENV:-} ]] && python_candidates+=("$VIRTUAL_ENV/bin/python")
  python_candidates+=(
    "$root_dir/.venv/bin/python"
    "$root_dir/venv/bin/python"
    "/opt/neurobridge/venv/bin/python"
  )
  for command_name in python3.11 python3; do
    command_path=$(command -v "$command_name" 2>/dev/null || true)
    [[ -n $command_path ]] && python_candidates+=("$command_path")
  done
fi

for candidate in "${python_candidates[@]}"; do
  [[ -x $candidate ]] || continue
  if PYTHONPATH=$root_dir "$candidate" -c \
    'import sys; assert sys.version_info >= (3, 11); import neurobridge, serial' \
    >/dev/null 2>&1; then
    python_path=$candidate
    runtime_ready=true
    break
  fi
done
if [[ -z $python_path ]]; then
  for candidate in "${python_candidates[@]}"; do
    [[ -x $candidate ]] || continue
    if PYTHONPATH=$root_dir "$candidate" -c \
      'import sys; assert sys.version_info >= (3, 11); import neurobridge' \
      >/dev/null 2>&1; then
      python_path=$candidate
      break
    fi
  done
fi
if [[ -z $python_path ]]; then
  {
    echo "ERROR: no usable Python 3.11+ environment was found."
    echo "Checked the active environment, project .venv/venv, /opt installation, python3.11, and python3."
    echo "Prepare the ignored project environment from the project root:"
    echo "  ./linux/setup-kylin-python.sh"
    echo "Then rerun this command, or pass --python /absolute/path/to/python."
  } >&2
  exit 1
fi

if [[ $system_install == true ]]; then
  getent group neurobridge >/dev/null 2>&1 || groupadd --system neurobridge
  id -u neurobridge >/dev/null 2>&1 || \
    useradd --system --gid neurobridge --home /nonexistent --shell /usr/sbin/nologin neurobridge
  install -d -o root -g neurobridge -m 0750 "$(dirname "$config_path")"
  config_template="$root_dir/config/gateway.toml.example"
  config_owner=root
  config_group=neurobridge
  config_mode=0640
  permission_user=neurobridge
  setup_log=
else
  install -d -o "$invoking_uid" -g "$invoking_gid" -m 0750 \
    "$runtime_dir" "$runtime_dir/config" "$runtime_dir/logs" \
    "$runtime_dir/recordings" "$runtime_dir/diagnostics"
  config_template="$root_dir/config/gateway.project.toml.example"
  config_owner=$invoking_uid
  config_group=$invoking_gid
  config_mode=0640
  permission_user=$invoking_user
  setup_log="$runtime_dir/logs/setup-kylin-serial-$(date -u +%Y%m%dT%H%M%SZ).log"
  touch "$setup_log"
  chown "$invoking_uid:$invoking_gid" "$setup_log"
  chmod 0600 "$setup_log"
  exec > >(tee -a "$setup_log") 2>&1
fi

[[ -f $config_template ]] || fail "Missing configuration template: $config_template"
if [[ ! -e $config_path ]]; then
  install -o "$config_owner" -g "$config_group" -m "$config_mode" "$config_template" "$config_path"
fi
[[ -f $config_path && ! -L $config_path ]] || fail "Configuration must be a regular file, not a symlink: $config_path"

backup_path="${config_path}.before-serial-$(date -u +%Y%m%dT%H%M%SZ)"
cp -p -- "$config_path" "$backup_path"
PYTHONPATH=$root_dir "$python_path" -m neurobridge.serial_setup \
  --config "$config_path" \
  --device "$device"
chown "$config_owner:$config_group" "$config_path" "$backup_path"
chmod "$config_mode" "$config_path" "$backup_path"

groups_required=()
groups_newly_added=()
shopt -s nullglob
tty_candidates=(/dev/ttyACM* /dev/ttyUSB*)
shopt -u nullglob
if (( ${#tty_candidates[@]} )); then
  for tty_path in "${tty_candidates[@]}"; do
    tty_group=$(stat -Lc '%G' "$tty_path" 2>/dev/null || true)
    if [[ -n $tty_group && $tty_group != root ]] && getent group "$tty_group" >/dev/null 2>&1; then
      if [[ $permission_user != root ]]; then
        contains_value "$tty_group" "${groups_required[@]}" || groups_required+=("$tty_group")
        if ! id -nG "$permission_user" | tr ' ' '\n' | grep -Fxq -- "$tty_group"; then
          usermod -aG "$tty_group" "$permission_user"
          contains_value "$tty_group" "${groups_newly_added[@]}" \
            || groups_newly_added+=("$tty_group")
        fi
      fi
    fi
  done
else
  echo "WARNING: no ttyACM/ttyUSB device exists; no serial group membership was changed." >&2
  echo "Connect and detect the actual device before granting permissions: sudo $root_dir/linux/diagnose-kylin-usb-serial.sh" >&2
fi

PYTHONPATH=$root_dir "$python_path" -m neurobridge.serial_setup --config "$config_path" --check-only
service_state=project-mode
if [[ $system_install == true ]]; then
  service_state=not-restarted
  if [[ $restart_service == true && -x /opt/neurobridge/venv/bin/neurobridge && -f /etc/systemd/system/neurobridge.service ]]; then
    systemctl daemon-reload
    systemctl enable neurobridge.service >/dev/null
    systemctl restart neurobridge.service
    service_state=$(systemctl is-active neurobridge.service || true)
  fi
fi

echo "Serial setup complete"
if [[ $system_install == true ]]; then
  echo "mode=system-install"
else
  echo "mode=project"
fi
echo "project=$root_dir"
echo "config=$config_path"
echo "backup=$backup_path"
echo "device=$device"
echo "python=$python_path"
echo "runtimeReady=$runtime_ready"
echo "requiredGroups=${groups_required[*]:-none}"
echo "newGroups=${groups_newly_added[*]:-none}"
if (( ${#groups_newly_added[@]} )); then
  echo "accountGroupMembershipChanged=true"
  echo "sessionGroupState=refresh-required"
  echo "NOTICE: the current login session may not yet have the new serial group."
  echo "NOTICE: start-kylin-gateway.sh will try one safe group-activation restart; if that fails, log out and back in or reboot."
else
  echo "accountGroupMembershipChanged=false"
  echo "sessionGroupState=verify-at-start"
fi
echo "service=$service_state"
[[ -n $setup_log ]] && echo "setupLog=$setup_log"
if [[ $runtime_ready == true && $system_install != true ]]; then
  echo "run=$root_dir/linux/start-kylin-gateway.sh"
elif [[ $runtime_ready != true ]]; then
  echo "WARNING: configuration succeeded, but pyserial is missing from $python_path" >&2
  echo "Run ./linux/setup-kylin-python.sh before starting NeuroBridge." >&2
fi
if [[ $system_install == true ]]; then
  echo "logs=sudo journalctl -u neurobridge.service -f -o short-iso-precise"
else
  echo "logs=$runtime_dir/logs/neurobridge.log"
fi
