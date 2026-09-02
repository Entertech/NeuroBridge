#!/usr/bin/env bash
# Start the Galaxy Kylin gateway directly from this checkout. All persistent
# runtime artifacts remain under the ignored project .runtime directory.
set -euo pipefail

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
config_path="$root_dir/.runtime/config/gateway.toml"
python_override=
original_args=("$@")

usage() {
  cat <<EOF
Usage: ./linux/start-kylin-gateway.sh [options]

Options:
  --python /absolute/python   Explicit Python 3.11+ executable
  --config /absolute/path     Override project runtime configuration
  -h, --help                  Show this help

Default configuration: $config_path
Persistent output:     $root_dir/.runtime/
Stop the foreground process with Ctrl+C.
EOF
}

while [[ $# -gt 0 ]]; do
  case $1 in
    --python)
      [[ $# -ge 2 ]] || fail "--python requires a value"
      python_override=$2
      shift 2
      ;;
    --config)
      [[ $# -ge 2 ]] || fail "--config requires a value"
      config_path=$2
      shift 2
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

[[ ${EUID:-$(id -u)} -ne 0 ]] || fail "Run without sudo so project files belong to the current user."
[[ $config_path == /* ]] || fail "--config must be an absolute path"
[[ -z $python_override || $python_override == /* ]] || fail "--python must be an absolute path"
[[ -f $config_path && ! -L $config_path ]] \
  || fail "Project configuration is missing. Run: sudo $root_dir/linux/setup-kylin-serial.sh"

python_candidates=()
if [[ -n $python_override ]]; then
  python_candidates+=("$python_override")
else
  [[ -n ${VIRTUAL_ENV:-} ]] && python_candidates+=("$VIRTUAL_ENV/bin/python")
  python_candidates+=("$root_dir/.venv/bin/python" "$root_dir/venv/bin/python")
fi

python_path=
for candidate in "${python_candidates[@]}"; do
  [[ -x $candidate ]] || continue
  if PYTHONPATH=$root_dir "$candidate" -c \
    'import sys; assert sys.version_info >= (3, 11); import neurobridge, serial, websockets' \
    >/dev/null 2>&1; then
    python_path=$candidate
    break
  fi
done
[[ -n $python_path ]] || fail \
  "A complete project .venv/venv was not found. Run ./linux/setup-kylin-python.sh first."

runtime_dir="$root_dir/.runtime"
if ! transport=$(PYTHONPATH=$root_dir "$python_path" - "$config_path" "$root_dir" "$runtime_dir" <<'PY'
from pathlib import Path
import os
import sys

from neurobridge.config import load

config_path, project_root, runtime_root = map(Path, sys.argv[1:])
config = load(config_path)
runtime_root = runtime_root.resolve()
for label, configured in (
    ("logging.directory", config.logging.directory),
    ("recording.directory", config.recording.directory),
):
    resolved = (configured if configured.is_absolute() else project_root / configured).resolve()
    try:
        resolved.relative_to(runtime_root)
    except ValueError as error:
        raise SystemExit(f"{label} must remain under {runtime_root}: {resolved}") from error
if config.data_source.type == "serial":
    if not config.algorithm.enabled:
        raise SystemExit(
            "Serial capture requires the local algorithm gate. Run linux/setup-kylin-algorithm.sh first."
        )
    if len(config.algorithm.command) != 1:
        raise SystemExit("Galaxy Kylin project mode requires one local algorithm bridge executable.")
    bridge = Path(config.algorithm.command[0]).resolve()
    algorithm_root = (runtime_root / "algorithm").resolve()
    try:
        bridge.relative_to(algorithm_root)
    except ValueError as error:
        raise SystemExit(f"algorithm.command must remain under {algorithm_root}: {bridge}") from error
    if bridge.is_symlink() or not bridge.is_file() or not os.access(bridge, os.X_OK):
        raise SystemExit(f"algorithm.command is not an executable regular file: {bridge}")
print(config.data_source.type)
PY
); then
  fail "Runtime configuration validation failed. No gateway process was started."
fi
install -d -m 0750 \
  "$runtime_dir" "$runtime_dir/logs" "$runtime_dir/recordings" "$runtime_dir/diagnostics"
console_log="$runtime_dir/logs/neurobridge-console.log"
touch "$console_log"
chmod 0600 "$console_log"

log_preflight() {
  printf '%s\n' "$*" | tee -a "$console_log"
}

group_is_listed() {
  local expected=$1 listed
  shift
  for listed in "$@"; do
    [[ $listed == "$expected" ]] && return 0
  done
  return 1
}

if [[ $transport == serial ]]; then
  shopt -s nullglob
  tty_candidates=(/dev/serial/by-id/* /dev/ttyACM* /dev/ttyUSB*)
  shopt -u nullglob
  declare -A resolved_seen=()
  read -r -a effective_groups <<<"$(id -nG)"
  read -r -a account_groups <<<"$(id -nG "$(id -un)")"
  unique_candidate_count=0
  accessible_candidate_count=0
  inaccessible_candidate_count=0
  first_inaccessible_path=
  first_required_group=
  first_account_group_member=false
  activation_group=
  for tty_path in "${tty_candidates[@]}"; do
    resolved_path=$(readlink -f -- "$tty_path" 2>/dev/null || true)
    [[ -n $resolved_path && -z ${resolved_seen[$resolved_path]:-} ]] || continue
    resolved_seen[$resolved_path]=1
    ((unique_candidate_count += 1))
    tty_readable=false
    tty_writable=false
    [[ -r $tty_path ]] && tty_readable=true
    [[ -w $tty_path ]] && tty_writable=true
    if [[ $tty_readable == true && $tty_writable == true ]]; then
      ((accessible_candidate_count += 1))
      continue
    fi
    ((inaccessible_candidate_count += 1))
    tty_group=$(stat -Lc '%G' "$tty_path" 2>/dev/null || true)
    [[ -n $tty_group ]] || tty_group=unknown
    effective_group_active=false
    account_group_member=false
    group_is_listed "$tty_group" "${effective_groups[@]}" && effective_group_active=true
    group_is_listed "$tty_group" "${account_groups[@]}" && account_group_member=true
    log_preflight "Serial permission preflight: path=$tty_path group=$tty_group readable=$tty_readable writable=$tty_writable accountGroupMember=$account_group_member sessionGroupActive=$effective_group_active"
    if [[ -z $first_inaccessible_path ]]; then
      first_inaccessible_path=$tty_path
      first_required_group=$tty_group
      first_account_group_member=$account_group_member
    fi
    if [[ -z $activation_group && $tty_group != unknown \
      && $account_group_member == true && $effective_group_active == false ]]; then
      activation_group=$tty_group
    fi
  done

  if [[ -n $activation_group && ${NEUROBRIDGE_GROUP_REEXEC:-0} != 1 ]]; then
    command -v sg >/dev/null 2>&1 || fail \
      "The $activation_group group was added, but this login session has not activated it. Log out and back in, then rerun this command."
    printf -v quoted_script '%q' "$root_dir/linux/start-kylin-gateway.sh"
    reexec_command="NEUROBRIDGE_GROUP_REEXEC=1 exec $quoted_script"
    for original_arg in "${original_args[@]}"; do
      printf -v quoted_arg '%q' "$original_arg"
      reexec_command+=" $quoted_arg"
    done
    log_preflight "Current login session has not activated group '$activation_group'; restarting once with that group."
    exec sg "$activation_group" -c "$reexec_command"
  fi

  if (( unique_candidate_count > 0 && accessible_candidate_count == 0 )); then
    if [[ $first_account_group_member == true ]]; then
      fail "No serial candidate is readable/writable; group '$first_required_group' is registered for the account but not effective for $first_inaccessible_path. Log out and back in (or reboot), then retry without sudo."
    fi
    fail "No serial candidate is readable/writable. First blocked path: $first_inaccessible_path (required group: $first_required_group). Run sudo $root_dir/linux/setup-kylin-serial.sh, then retry without sudo."
  fi
  if (( inaccessible_candidate_count > 0 && accessible_candidate_count > 0 )); then
    log_preflight "Serial permission preflight warning: accessibleCandidates=$accessible_candidate_count inaccessibleCandidates=$inaccessible_candidate_count; discovery will continue with accessible candidates."
  fi
fi

exec > >(tee -a "$console_log") 2>&1

echo "NeuroBridge project runtime starting"
echo "project=$root_dir"
echo "python=$python_path"
echo "config=$config_path"
echo "logs=$runtime_dir/logs"
echo "recordings=$runtime_dir/recordings"
echo "browser=http://127.0.0.1:8080/"
echo "stop=Ctrl+C"

cd "$root_dir"
PYTHONPATH=$root_dir exec "$python_path" -m neurobridge --config "$config_path"
