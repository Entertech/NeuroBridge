#!/usr/bin/env bash
# Build, smoke-test, and enable the project-local native algorithm bridge on
# Galaxy Kylin V10 x86_64. Generated files remain under ignored .runtime/.
set -euo pipefail

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

ask_yes_no() {
  local prompt=$1 answer
  while true; do
    printf '%s [yes/no]: ' "$prompt"
    IFS= read -r answer || return 1
    case $answer in
      [Yy][Ee][Ss]|[Yy]) return 0 ;;
      [Nn][Oo]|[Nn]) return 1 ;;
      *) printf '请输入 yes 或 no。\n' ;;
    esac
  done
}

root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
runtime_dir="$root_dir/.runtime"
algorithm_dir="$runtime_dir/algorithm"
config_path="$runtime_dir/config/gateway.toml"

usage() {
  cat <<EOF
Usage: ./linux/setup-kylin-algorithm.sh

Builds the locked C++ algorithm bridge locally on Galaxy Kylin V10 x86_64,
runs a non-biological empty-input process smoke test, and only then enables it
in the project runtime configuration.

Generated bridge, build files, and logs remain below:
  $algorithm_dir
  $runtime_dir/logs

Run as the normal desktop user. Missing approved system build dependencies can
be installed after a yes/no confirmation through the detected package manager.
EOF
}

if [[ ${1:-} == -h || ${1:-} == --help ]]; then
  usage
  exit 0
fi
[[ $# -eq 0 ]] || fail "Unknown option: $1"
[[ ${EUID:-$(id -u)} -ne 0 ]] || fail "Run without sudo so project build files belong to the current user."
[[ -n $root_dir && $root_dir != / && -f $root_dir/pyproject.toml ]] || fail "Invalid NeuroBridge project root: $root_dir"
for path in "$runtime_dir" "$algorithm_dir"; do
  [[ ! -L $path ]] || fail "$path must be a real project directory, not a symlink."
done
[[ -f $config_path && ! -L $config_path ]] || fail \
  "Project configuration is missing. First run: sudo $root_dir/linux/setup-kylin-serial.sh"
[[ $(uname -m) == x86_64 ]] || fail "The algorithm bridge requires x86_64; detected $(uname -m)."
[[ -r /etc/os-release ]] || fail "/etc/os-release is unavailable."
. /etc/os-release
[[ ${ID,,} == kylin ]] || fail "This helper requires Galaxy Kylin; detected ID=${ID:-unknown}."

python_path=
for candidate in "$root_dir/.venv/bin/python" "$root_dir/venv/bin/python"; do
  [[ -x $candidate ]] || continue
  if PYTHONPATH=$root_dir "$candidate" -c \
    'import sys; assert sys.version_info >= (3, 11); import neurobridge' >/dev/null 2>&1; then
    python_path=$candidate
    break
  fi
done
[[ -n $python_path ]] || fail "Project Python environment is not ready. Run: $root_dir/linux/setup-kylin-python.sh"
[[ -f $root_dir/third_party/AffectiveCloud-Algorithm-SDK/cpp/package/CMakeLists.txt ]] \
  || fail "Locked AffectiveCloud algorithm SDK source is missing from third_party/."
[[ -f $root_dir/third_party/NumCpp/CMakeLists.txt ]] \
  || fail "Locked NumCpp source is missing from third_party/."
[[ -f $root_dir/sdk.lock && ! -L $root_dir/sdk.lock ]] \
  || fail "sdk.lock is missing or unsafe; algorithm source provenance cannot be verified."

install -d -m 0750 "$runtime_dir" "$runtime_dir/logs" "$algorithm_dir"
setup_log="$runtime_dir/logs/setup-kylin-algorithm-$(date -u +%Y%m%dT%H%M%SZ)-$$.log"
touch "$setup_log"
chmod 0600 "$setup_log"
exec > >(tee -a "$setup_log") 2>&1

echo "NeuroBridge Galaxy Kylin algorithm setup started"
echo "project=$root_dir"
echo "config=$config_path"
echo "log=$setup_log"
echo "osId=${ID:-unknown}"
echo "osVersion=${VERSION_ID:-unknown}"
echo "architecture=$(uname -m)"
echo "kernel=$(uname -r)"
lock_details=$("$python_path" - "$root_dir/sdk.lock" <<'PY'
import re
import sys
import tomllib
from pathlib import Path

lock = tomllib.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
sdk = lock["affective_algorithm_sdk"]
build = sdk["build"]
expected = {
    "sdkVendorPath": (sdk["vendor_path"], "third_party/AffectiveCloud-Algorithm-SDK"),
    "numCppVendorPath": (build["vendor_path"], "third_party/NumCpp"),
    "cmakeMinimum": (str(build["cmake_minimum_version"]), "3.22"),
    "cxxStandard": (str(build["cxx_standard"]), "17"),
}
for label, (actual, wanted) in expected.items():
    if actual != wanted:
        raise SystemExit(f"{label} mismatch: expected {wanted}, found {actual}")
for label, value in (
    ("affectiveSdkCommit", sdk["commit"]),
    ("numCppCommit", build["numcpp_commit"]),
):
    if re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise SystemExit(f"{label} is not a full lowercase Git commit")
print(f"affectiveSdkVersion={sdk['version']}")
print(f"affectiveSdkCommit={sdk['commit']}")
print(f"numCppVersion={build['numcpp_version']}")
print(f"numCppCommit={build['numcpp_commit']}")
print(f"lockedEigenVersion={build['eigen_version']}")
print(f"lockedCmakeMinimum={build['cmake_minimum_version']}")
print(f"lockedCxxStandard={build['cxx_standard']}")
PY
) || fail "sdk.lock is incomplete or inconsistent with the vendored algorithm source layout."
printf '%s\n' "$lock_details"

cmake_is_usable() {
  command -v cmake >/dev/null 2>&1 || return 1
  local version
  version=$(cmake --version 2>/dev/null | awk 'NR == 1 { print $3 }')
  "$python_path" - "$version" <<'PY' >/dev/null 2>&1
import sys
try:
    version = tuple(int(part) for part in sys.argv[1].split(".")[:3])
except ValueError:
    raise SystemExit(1)
raise SystemExit(0 if version >= (3, 22) else 1)
PY
}

eigen_is_usable() {
  [[ -d /usr/include/eigen3 ]] || return 1
  [[ -f /usr/share/eigen3/cmake/Eigen3Config.cmake \
    || -f /usr/lib/cmake/eigen3/Eigen3Config.cmake \
    || -f /usr/lib64/cmake/eigen3/Eigen3Config.cmake \
    || -f /usr/local/share/eigen3/cmake/Eigen3Config.cmake ]]
}

build_dependencies_ready() {
  command -v c++ >/dev/null 2>&1 && cmake_is_usable && eigen_is_usable
}

install_build_dependencies() {
  if command -v apt-get >/dev/null 2>&1; then
    echo "packageManager=apt-get"
    sudo apt-get install -y cmake g++ libeigen3-dev
  elif command -v dnf >/dev/null 2>&1; then
    echo "packageManager=dnf"
    sudo dnf install -y cmake gcc-c++ eigen3-devel
  elif command -v yum >/dev/null 2>&1; then
    echo "packageManager=yum"
    sudo yum install -y cmake gcc-c++ eigen3-devel
  else
    fail "No supported package manager was found. Install CMake 3.22+, a C++17 compiler, and Eigen3 development files, then rerun."
  fi
}

if ! build_dependencies_ready; then
  echo "Build prerequisites are incomplete. Required: CMake 3.22+, C++17 compiler, Eigen3 headers and CMake config."
  if ask_yes_no "是否使用银河麒麟当前软件源安装算法构建依赖？"; then
    install_build_dependencies || fail "System dependency installation failed. See: $setup_log"
  else
    fail "Dependency installation was cancelled. Configuration was not changed."
  fi
fi
build_dependencies_ready || fail \
  "Build dependencies remain unavailable after installation. Check CMake version and Eigen3 development files. Configuration was not changed."

echo "cmake=$(cmake --version | head -n 1)"
echo "compiler=$(c++ --version | head -n 1)"
attempt_dir="$algorithm_dir/attempt-$(date -u +%Y%m%dT%H%M%SZ)-$$"
install -d -m 0750 "$attempt_dir"
echo "buildAttempt=$attempt_dir"

PYTHON="$python_path" "$root_dir/linux/build-algorithm-bridge.sh" \
  "$root_dir/third_party" "$attempt_dir" \
  || fail "Algorithm bridge build failed. Configuration was not changed. Build log: $attempt_dir/build.log"

attempt_bridge="$attempt_dir/neurobridge_affective_bridge"
[[ -x $attempt_bridge ]] || fail "Build reported success but did not produce an executable bridge."

# Validate the newly built file before it can replace a previously usable
# bridge.  This first pass never changes gateway.toml.
PYTHONPATH=$root_dir "$python_path" -m neurobridge.algorithm_setup \
  --config "$config_path" \
  --bridge "$attempt_bridge" \
  --timeout-seconds 5 \
  --check-only \
  || fail "Bridge smoke test failed. The installed bridge and configuration were not changed. Review: $setup_log"

final_bridge="$algorithm_dir/neurobridge_affective_bridge"
temporary_bridge="$algorithm_dir/.neurobridge_affective_bridge.$$"
previous_bridge=
rollback_pending=false

restore_previous_bridge() {
  [[ $rollback_pending == true ]] || return 0
  rm -f -- "$temporary_bridge" "$final_bridge"
  if [[ -n $previous_bridge && -e $previous_bridge ]]; then
    mv -f -- "$previous_bridge" "$final_bridge"
    echo "Previous algorithm bridge restored after setup failure."
  fi
}
trap restore_previous_bridge EXIT

install -m 0750 "$attempt_bridge" "$temporary_bridge"
if [[ -e $final_bridge || -L $final_bridge ]]; then
  previous_bridge="$attempt_dir/previous-neurobridge_affective_bridge"
  rollback_pending=true
  mv -f -- "$final_bridge" "$previous_bridge"
else
  rollback_pending=true
fi
mv -f -- "$temporary_bridge" "$final_bridge"

if ! PYTHONPATH=$root_dir "$python_path" -m neurobridge.algorithm_setup \
    --config "$config_path" \
    --bridge "$final_bridge" \
    --timeout-seconds 5; then
  fail "Bridge installation or configuration update failed. The previous bridge was restored and the original configuration was retained. Review: $setup_log"
fi
rollback_pending=false
trap - EXIT
[[ -z $previous_bridge ]] || rm -f -- "$previous_bridge"

echo "Algorithm setup complete"
echo "algorithmReady=true"
echo "bridge=$final_bridge"
echo "config=$config_path"
echo "log=$setup_log"
echo "next=$root_dir/linux/start-kylin-gateway.sh"
