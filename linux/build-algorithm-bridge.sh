#!/usr/bin/env bash
# Build the bridge from SDK sources vendored in the NeuroBridge checkout.
# No source download or GitHub request is permitted after this checkout exists.
set -euo pipefail

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

[[ ${EUID} -ne 0 ]] || fail "Do not build the SDK bridge as root. Use a dedicated POC operator account."
[[ $(uname -m) == "x86_64" ]] || fail "The first-release algorithm bridge supports Linux x86_64 only."
[[ -r /etc/os-release ]] || fail "Cannot identify the operating system."
. /etc/os-release
case ${ID,,} in
  ubuntu) platform=ubuntu; eigen_lock_key=ubuntu_24_04_x86_64 ;;
  kylin) platform=galaxy-kylin; eigen_lock_key=galaxy_kylin_v10_x86_64 ;;
  *) fail "The algorithm bridge build supports Ubuntu or Galaxy Kylin; detected ID=${ID:-unknown}." ;;
esac

[[ $# -eq 2 ]] || fail "Usage: $0 /absolute/controlled-sdk-dir /absolute/output-dir"
sdk_root=$1
output_dir=$2
case "$sdk_root" in /*) ;; *) fail "SDK directory must be absolute.";; esac
case "$output_dir" in /*) ;; *) fail "Output directory must be absolute.";; esac

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
for command in cmake c++; do
  command -v "$command" >/dev/null 2>&1 || fail "Missing system prerequisite: $command"
done
python_command=${PYTHON:-python3}
[[ -x $python_command ]] || command -v "$python_command" >/dev/null 2>&1 \
  || fail "Missing Python required for build-version validation: $python_command"
cmake_version=$(cmake --version | awk 'NR == 1 { print $3 }')
"$python_command" - "$cmake_version" <<'PY' || fail "CMake 3.22 or newer is required; detected ${cmake_version:-unknown}."
import sys

parts = sys.argv[1].split(".")
try:
    version = tuple(int(part) for part in parts[:3])
except ValueError:
    raise SystemExit(1)
raise SystemExit(0 if version >= (3, 22) else 1)
PY
eigen_config=
for candidate in \
  /usr/share/eigen3/cmake/Eigen3Config.cmake \
  /usr/lib/cmake/eigen3/Eigen3Config.cmake \
  /usr/lib64/cmake/eigen3/Eigen3Config.cmake \
  /usr/local/share/eigen3/cmake/Eigen3Config.cmake; do
  if [[ -f $candidate ]]; then
    eigen_config=$candidate
    break
  fi
done
[[ -n $eigen_config && -d /usr/include/eigen3 ]] || fail \
  "Eigen3 headers/CMake configuration are missing. Install the approved Eigen3 development package for $platform."
eigen_macros=/usr/include/eigen3/Eigen/src/Core/util/Macros.h
[[ -f $eigen_macros ]] || fail "Eigen3 version header is missing: $eigen_macros"
eigen_version=$(awk '
  $2 == "EIGEN_WORLD_VERSION" { world=$3 }
  $2 == "EIGEN_MAJOR_VERSION" { major=$3 }
  $2 == "EIGEN_MINOR_VERSION" { minor=$3 }
  END { if (world ~ /^[0-9]+$/ && major ~ /^[0-9]+$/ && minor ~ /^[0-9]+$/) print world "." major "." minor }
' "$eigen_macros")
expected_eigen_version=$("$python_command" - "$repo_root/sdk.lock" "$eigen_lock_key" <<'PY'
import sys
import tomllib
from pathlib import Path
lock = tomllib.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(lock["affective_algorithm_sdk"]["build"]["eigen_versions"][sys.argv[2]])
PY
)
[[ -n $eigen_version && $eigen_version == "$expected_eigen_version" ]] || fail \
  "Eigen3 version mismatch for $platform: expected $expected_eigen_version, detected ${eigen_version:-unknown}."
sdk_dir="$sdk_root/AffectiveCloud-Algorithm-SDK"
numcpp_dir="$sdk_root/NumCpp"
[[ -f "$sdk_dir/cpp/package/CMakeLists.txt" ]] || fail "Vendored AffectiveCloud SDK source is missing from this checkout."
[[ -f "$numcpp_dir/CMakeLists.txt" ]] || fail "Vendored NumCpp source is missing from this checkout."

mkdir -p "$output_dir"
build_log="$output_dir/build.log"
build_root="$output_dir/build"
[[ "$build_root" == "$output_dir/build" && "$output_dir" != / ]] || fail "Refusing to clear an unsafe build directory."
# CMake caches compiler, package-discovery and host-specific results. Never
# reuse a cache created by a different Ubuntu image or an earlier failed run.
rm -rf "$build_root"
exec > >(tee "$build_log") 2>&1
echo "NeuroBridge algorithm bridge build started: $(date --iso-8601=seconds)"
echo "Output log: $build_log"
echo "Platform: $platform ID=${ID:-unknown} VERSION_ID=${VERSION_ID:-unknown}"
echo "Kernel: $(uname -r)"
echo "CMake: $(cmake --version | head -n 1)"
echo "Compiler: $(c++ --version | head -n 1)"
echo "Eigen3Config: $eigen_config"
echo "Eigen3Version: $eigen_version"
numcpp_prefix="$build_root/numcpp-prefix"
cmake -Wno-dev -S "$numcpp_dir" -B "$build_root/numcpp" \
  -DCMAKE_BUILD_TYPE=Release -DNUMCPP_NO_USE_BOOST=ON -DCMAKE_INSTALL_PREFIX="$numcpp_prefix"
cmake --build "$build_root/numcpp" --parallel 2
cmake --install "$build_root/numcpp"

cmake -Wno-dev -S "$repo_root/mac/algorithm_bridge" -B "$build_root/bridge" \
  -DCMAKE_BUILD_TYPE=Release \
  -DAFFECTIVE_SDK_SOURCE_DIR="$sdk_dir" \
  -DNUMCPP_NO_USE_BOOST=ON \
  -DCMAKE_PREFIX_PATH="/usr;$numcpp_prefix"
cmake --build "$build_root/bridge" --parallel 2

install -m 0755 "$build_root/bridge/bin/neurobridge_affective_bridge" "$output_dir/neurobridge_affective_bridge"
sha256sum "$output_dir/neurobridge_affective_bridge"
echo "Bridge built: $output_dir/neurobridge_affective_bridge"
echo "Next: run a bridge smoke test, then tools/run-algorithm-poc.py against a consented, completed recording before treating algorithm semantics as validated."
