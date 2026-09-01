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
  "A complete project .venv/venv was not found. Create .venv and install requirements.lock first."

runtime_dir="$root_dir/.runtime"
if ! PYTHONPATH=$root_dir "$python_path" - "$config_path" "$root_dir" "$runtime_dir" <<'PY'
from pathlib import Path
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
PY
then
  fail "Runtime configuration validation failed. No gateway process was started."
fi
install -d -m 0750 \
  "$runtime_dir" "$runtime_dir/logs" "$runtime_dir/recordings" "$runtime_dir/diagnostics"
console_log="$runtime_dir/logs/neurobridge-console.log"
touch "$console_log"
chmod 0600 "$console_log"
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
