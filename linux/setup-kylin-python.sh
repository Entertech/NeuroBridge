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
portable_root="$root_dir/python-runtime"
portable_python="$portable_root/python/bin/python3"
portable_archive_name="cpython-3.11.16+20260825-x86_64-unknown-linux-gnu-install_only.tar.gz"
portable_archive="$portable_root/$portable_archive_name"
portable_url="https://github.com/astral-sh/python-build-standalone/releases/download/20260825/$portable_archive_name"
portable_sha256="25844eb97cdc72cdc78addaad0969ce3b2133a4de54bfcfa4d57f8a6d095eaab"
wheelhouse_manifest="$root_dir/config/kylin-wheelhouse.sha256"
requirements="$root_dir/config/kylin-serial-requirements.lock"

usage() {
  cat <<EOF
Usage: ./linux/setup-kylin-python.sh

Detects Galaxy Kylin V10 OS/CPU; checks interpreter ABI and, prepares a project-local Python 3.11 when the
system only has Python 3.8, creates .venv, installs serial-only requirements, and
verifies the runtime. Existing local archives/wheels are used offline;
otherwise the pinned runtime and packages use the current network.

All generated files remain under:
  $venv_dir
  $runtime_dir
  $wheelhouse_dir
  $portable_root
EOF
}

if [[ ${1:-} == -h || ${1:-} == --help ]]; then
  usage
  exit 0
fi
[[ $# -eq 0 ]] || fail "Unknown option: $1"
# The bootstrap package runs this as root, on a temporary copy of the source
# tree that is thrown away after the runtime is installed.  A normal checkout
# still refuses root so the project files keep the invoking user as owner.
[[ ${EUID:-$(id -u)} -ne 0 || ${NEUROBRIDGE_BOOTSTRAP:-} == 1 ]] || fail "Run without sudo so project files belong to the current user."
[[ ! -L $runtime_dir ]] || fail ".runtime must be a real project directory, not a symlink."
[[ ! -L $venv_dir ]] || fail ".venv must be a real project directory, not a symlink."
[[ ! -L $wheelhouse_dir ]] || fail "wheelhouse must be a real project directory, not a symlink."
[[ ! -L $portable_root ]] || fail "python-runtime must be a real project directory, not a symlink."

install -d -m 0750 \
  "$runtime_dir" "$runtime_dir/logs" "$runtime_dir/cache/pip" \
  "$runtime_dir/tmp" "$runtime_dir/backups"
install -d -m 0755 "$wheelhouse_dir"
install -d -m 0755 "$portable_root"
setup_log="$runtime_dir/logs/setup-kylin-python-$(date -u +%Y%m%dT%H%M%SZ).log"
touch "$setup_log"
chmod 0600 "$setup_log"
exec > >(tee -a "$setup_log") 2>&1

echo "NeuroBridge Python setup started"
echo "project=$root_dir"
echo "log=$setup_log"
trap 'result=$?; printf "EVENT utc=%s phase=python_setup_end exit_code=%s duration_seconds=%s\n" "$(date -u +%FT%TZ)" "$result" "$SECONDS"' EXIT

NB_INPUT_LOCK="$root_dir/config/kylin-bootstrap-inputs.toml"
. "$root_dir/packaging/kylin/platform.sh"
if [[ -f $root_dir/packaging/kylin/diagnostic-context.sh ]]; then
  . "$root_dir/packaging/kylin/diagnostic-context.sh"
  nb_event software_identity "application_version=$(nb_application_version "$root_dir") source_commit=$(nb_source_commit "$root_dir")"
fi
nb_select_platform || fail "Platform selection failed. See: $setup_log"
input_root="$root_dir/packaging/kylin/offline"
portable_archive_name=$(nb_lock_value "artifacts.$NB_PYTHON_INPUT" filename)
portable_sha256=$(nb_lock_value "artifacts.$NB_PYTHON_INPUT" sha256)
portable_url=$(nb_lock_value "artifacts.$NB_PYTHON_INPUT" url)
if [[ $NB_PYTHON_INPUT == python_source ]]; then
  portable_archive="$input_root/$portable_archive_name"
else
  portable_archive="$portable_root/$portable_archive_name"
fi
[[ -f $requirements ]] || fail "Serial requirements lock is missing from the project."

verify_portable_archive() {
  nb_verify_input "$NB_PYTHON_INPUT" "$portable_archive"
}

download_portable_archive() {
  local partial_archive="$portable_archive.partial"
  echo "Galaxy Kylin does not provide Python 3.11; downloading the pinned project-local runtime."
  echo "url=$portable_url"
  if command -v curl >/dev/null 2>&1; then
    if ! curl --fail --location --retry 3 --connect-timeout 20 \
      --output "$partial_archive" "$portable_url"; then
      rm -f -- "$partial_archive"
      return 1
    fi
  elif command -v wget >/dev/null 2>&1; then
    if ! wget --tries=3 --timeout=20 --output-document="$partial_archive" "$portable_url"; then
      rm -f -- "$partial_archive"
      return 1
    fi
  else
    return 1
  fi
  mv -- "$partial_archive" "$portable_archive"
}

prepare_portable_python() {
  if [[ $NB_PYTHON_INPUT == python_source ]]; then
    nb_verify_input python_source "$portable_archive" || fail "Copy approved source input: $portable_archive_name."
    nb_require_python_development || fail "Native Python development dependencies are incomplete; see the repair commands above."
    local source_dir="$runtime_dir/python-source-$NB_ARCH" build_dir="$runtime_dir/python-build-$NB_ARCH"
    install -d -m 0750 "$source_dir" "$build_dir"
    tar -xJf "$portable_archive" -C "$source_dir" --strip-components=1 || fail "Python source extraction failed."
    nb_event build_python "version=$(nb_lock_value "" python_version) architecture=$NB_ARCH prefix=$portable_root/python"
    (cd "$build_dir" && "$source_dir/configure" --prefix="$portable_root/python" --with-ensurepip=install &&
      make -j2 && make install) || fail "Native Python build failed; see compiler/configure output above."
    "$portable_python" -c 'import ssl,sqlite3,bz2,lzma,ctypes,zlib,venv; import sys; assert sys.version_info[:3] == (3,11,16)' \
      || fail "Built Python is incomplete: install target OS SSL/sqlite/bzip2/xz/ffi/zlib development packages and rebuild."
    nb_event build_python_complete "architecture=$NB_ARCH executable=$portable_python"
    return
  fi
  local extract_dir="$portable_root/.extract-$(date -u +%Y%m%dT%H%M%SZ)-$$"
  command -v sha256sum >/dev/null 2>&1 || fail "sha256sum is required to verify the Python runtime."
  command -v tar >/dev/null 2>&1 || fail "tar is required to unpack the Python runtime."

  if [[ ! -f $portable_archive ]]; then
    download_portable_archive || fail \
      "Could not download Python. Connect this computer to the network, or copy $portable_archive_name into python-runtime/ and rerun."
  else
    echo "Using project-local Python archive: $portable_archive"
  fi
  verify_portable_archive || fail \
    "Python archive SHA-256 mismatch. Replace python-runtime/$portable_archive_name with the approved file."
  [[ ! -e $portable_root/python ]] || fail \
    "python-runtime/python exists but is unusable. Move it aside, then rerun this command."

  install -d -m 0750 "$extract_dir"
  if ! tar -xzf "$portable_archive" -C "$extract_dir"; then
    fail "Could not unpack the project-local Python runtime. Partial files remain at $extract_dir for diagnosis."
  fi
  [[ -x $extract_dir/python/bin/python3 ]] || fail \
    "The approved Python archive has an unexpected directory structure: $extract_dir"
  mv -- "$extract_dir/python" "$portable_root/python"
  rmdir -- "$extract_dir"
  echo "Project-local Python runtime prepared: $portable_python"
}

python_path=
python_candidates=("$portable_python")
# Runtime archives must carry the pinned interpreter and cp311 wheels, even
# when this machine has a suitable system Python for a source-tree deployment.
if [[ ${NEUROBRIDGE_PORTABLE_PYTHON:-} != 1 && $NB_PYTHON_INPUT == python_x86_64 ]]; then
  python_candidates+=("$(command -v python3.11 2>/dev/null || true)" \
    "$(command -v python3 2>/dev/null || true)")
fi
for candidate in "${python_candidates[@]}"; do
  [[ -n $candidate ]] || continue
  if "$candidate" -c 'import sys; assert sys.version_info >= (3, 11)' >/dev/null 2>&1; then
    python_path=$candidate
    break
  fi
done
if [[ -z $python_path ]]; then
  prepare_portable_python
  python_path=$portable_python
fi
echo "python=$python_path ($("$python_path" --version 2>&1))"
if [[ $NB_PYTHON_INPUT == python_source ]]; then
  "$python_path" -c 'import sys; assert sys.version_info[:3] == (3,11,16)' || fail "Expected the locked source Python 3.11.16."
fi
"$python_path" -c 'import struct,sys; assert struct.calcsize("P")*8 == int(sys.argv[1]); assert sys.byteorder == "little"' "$NB_BITS" \
  || fail "Python architecture/byte order does not match the selected profile."

venv_check='import sys,struct; assert sys.version_info >= (3,11); assert struct.calcsize("P")*8 == int(sys.argv[1]); assert sys.byteorder == "little"'
if [[ $NB_PYTHON_INPUT == python_source || ${NEUROBRIDGE_PORTABLE_PYTHON:-} == 1 ]]; then
  venv_check+='; assert sys.version_info[:3] == (3,11,16)'
fi
if [[ -d $venv_dir ]] && ! "$venv_dir/bin/python" -c "$venv_check" "$NB_BITS" >/dev/null 2>&1; then
  venv_backup="$runtime_dir/backups/venv-before-python311-$(date -u +%Y%m%dT%H%M%SZ)"
  echo "Existing .venv does not match the selected Python/ABI; moving it to: $venv_backup"
  mv -- "$venv_dir" "$venv_backup"
fi
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
  [[ -f $wheelhouse_manifest ]] || fail "Missing wheel checksum manifest: $wheelhouse_manifest"
  if ! (cd "$root_dir" && sha256sum -c "${wheelhouse_manifest#$root_dir/}"); then
    fail "wheelhouse/ is incomplete or failed SHA-256 verification. Copy the complete approved offline package set, then rerun."
  fi
  if ! "$venv_dir/bin/python" -m pip install \
    --no-index --find-links "$wheelhouse_dir" -r "$requirements"; then
    fail "Offline installation failed. Replace wheelhouse/ with wheels matching the selected Kylin architecture and Python version."
  fi
else
  install_mode=current-network
  echo "wheelhouse/ is empty; installing locked dependencies through the current network."
  if ! "$venv_dir/bin/python" -m pip install -r "$requirements"; then
    fail "Network installation failed. Check network access, or copy the approved wheels into wheelhouse/ and rerun."
  fi
fi

"$venv_dir/bin/python" -m pip check \
  || fail "Installed Python packages have dependency conflicts."
PYTHONPATH=$root_dir "$venv_dir/bin/python" -c \
  'import neurobridge, serial, websockets; print("Runtime import check: OK")' \
  || fail "Runtime import check failed. See the setup log for the missing package."

nb_event python_ready "architecture=$NB_ARCH bits=$NB_BITS install_mode=$install_mode requirements=serial-only"
echo "Python environment ready"
echo "mode=$install_mode"
echo "python=$venv_dir/bin/python"
echo "next=sudo $root_dir/linux/diagnose-kylin-usb-serial.sh"
