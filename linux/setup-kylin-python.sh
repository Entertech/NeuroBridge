#!/usr/bin/env bash
# One-command project-local Python runtime preparation for Galaxy Kylin.
set -euo pipefail

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
runtime_dir="$root_dir/.runtime"
venv_dir="$root_dir/.venv"
wheelhouse_dir="$root_dir/wheelhouse"

usage() {
  cat <<EOF
Usage: ./linux/setup-kylin-python.sh

Checks Galaxy Kylin x86_64 and Python 3.11+, creates the ignored project
.venv, installs requirements.lock, and verifies the runtime. Existing wheels
under wheelhouse/ are used offline; otherwise pip uses the current network.

All generated files remain under:
  $venv_dir
  $runtime_dir
  $wheelhouse_dir
EOF
}

if [[ ${1:-} == -h || ${1:-} == --help ]]; then
  usage
  exit 0
fi
[[ $# -eq 0 ]] || fail "Unknown option: $1"
[[ ${EUID:-$(id -u)} -ne 0 ]] || fail "Run without sudo so project files belong to the current user."
[[ ! -L $runtime_dir ]] || fail ".runtime must be a real project directory, not a symlink."
[[ ! -L $venv_dir ]] || fail ".venv must be a real project directory, not a symlink."
[[ ! -L $wheelhouse_dir ]] || fail "wheelhouse must be a real project directory, not a symlink."

install -d -m 0750 \
  "$runtime_dir" "$runtime_dir/logs" "$runtime_dir/cache/pip" "$runtime_dir/tmp"
install -d -m 0755 "$wheelhouse_dir"
setup_log="$runtime_dir/logs/setup-kylin-python-$(date -u +%Y%m%dT%H%M%SZ).log"
touch "$setup_log"
chmod 0600 "$setup_log"
exec > >(tee -a "$setup_log") 2>&1

echo "NeuroBridge Python setup started"
echo "project=$root_dir"
echo "log=$setup_log"

[[ $(uname -m) == x86_64 ]] || fail "This deployment requires x86_64; detected $(uname -m)."
[[ -r /etc/os-release ]] || fail "/etc/os-release is unavailable."
. /etc/os-release
[[ ${ID,,} == kylin ]] || fail "This helper requires Galaxy Kylin; detected ID=${ID:-unknown}."
[[ -f $root_dir/requirements.lock ]] || fail "requirements.lock is missing from the project."

python_path=
for command_name in python3.11 python3; do
  candidate=$(command -v "$command_name" 2>/dev/null || true)
  [[ -n $candidate ]] || continue
  if "$candidate" -c 'import sys; assert sys.version_info >= (3, 11)' >/dev/null 2>&1; then
    python_path=$candidate
    break
  fi
done
[[ -n $python_path ]] || fail \
  "Python 3.11+ is not installed. Install the approved Galaxy Kylin Python 3.11 package, then rerun this command."
echo "python=$python_path ($("$python_path" --version 2>&1))"

if [[ ! -d $venv_dir ]]; then
  echo "Creating project Python environment: $venv_dir"
  "$python_path" -m venv "$venv_dir" \
    || fail "Could not create .venv. Confirm that the Python venv component is installed."
else
  [[ -x $venv_dir/bin/python ]] || fail ".venv exists but bin/python is unavailable."
  echo "Reusing project Python environment: $venv_dir"
fi

export PIP_CACHE_DIR="$runtime_dir/cache/pip"
export PIP_DISABLE_PIP_VERSION_CHECK=1
export TMPDIR="$runtime_dir/tmp"

shopt -s nullglob
wheel_files=("$wheelhouse_dir"/*.whl)
shopt -u nullglob
if (( ${#wheel_files[@]} > 0 )); then
  install_mode=offline-wheelhouse
  echo "Installing locked dependencies from project wheelhouse (offline mode)."
  if ! "$venv_dir/bin/python" -m pip install \
    --no-index --find-links "$wheelhouse_dir" -r "$root_dir/requirements.lock"; then
    fail "Offline installation failed. Replace wheelhouse/ with wheels matching Kylin x86_64 and this Python version."
  fi
else
  install_mode=current-network
  echo "wheelhouse/ is empty; installing locked dependencies through the current network."
  if ! "$venv_dir/bin/python" -m pip install -r "$root_dir/requirements.lock"; then
    fail "Network installation failed. Check network access, or copy the approved wheels into wheelhouse/ and rerun."
  fi
fi

"$venv_dir/bin/python" -m pip check \
  || fail "Installed Python packages have dependency conflicts."
PYTHONPATH=$root_dir "$venv_dir/bin/python" -c \
  'import neurobridge, serial, websockets; print("Runtime import check: OK")' \
  || fail "Runtime import check failed. See the setup log for the missing package."

echo "Python environment ready"
echo "mode=$install_mode"
echo "python=$venv_dir/bin/python"
echo "next=sudo $root_dir/linux/diagnose-kylin-usb-serial.sh --timeout 60"
