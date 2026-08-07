#!/usr/bin/env bash
# Build the locked AffectiveCloud C++ bridge from an already verified local SDK.
# The target deployment must never fetch source or contact GitHub.
set -euo pipefail

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

[[ ${EUID} -ne 0 ]] || fail "Do not build the SDK bridge as root. Use a dedicated POC operator account."
[[ $(uname -m) == "x86_64" ]] || fail "The first-release algorithm POC supports Ubuntu x86_64 only."
[[ -r /etc/os-release ]] || fail "Cannot identify the operating system."
. /etc/os-release
[[ ${ID:-} == "ubuntu" ]] || fail "The first-release algorithm POC supports Ubuntu only."

[[ $# -eq 2 ]] || fail "Usage: $0 /absolute/controlled-sdk-dir /absolute/output-dir"
sdk_root=$1
output_dir=$2
case "$sdk_root" in /*) ;; *) fail "SDK directory must be absolute.";; esac
case "$output_dir" in /*) ;; *) fail "Output directory must be absolute.";; esac

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
for command in cmake c++ python3; do
  command -v "$command" >/dev/null 2>&1 || fail "Missing $command. Provide the Ubuntu 24.04 offline dependency bundle."
done
[[ -d /usr/include/eigen3 ]] || fail "Eigen3 headers are missing. Provide the Ubuntu 24.04 offline dependency bundle."
sdk_dir="$sdk_root/AffectiveCloud-Algorithm-SDK"
numcpp_dir="$sdk_root/NumCpp"
[[ -f "$sdk_dir/cpp/package/CMakeLists.txt" ]] || fail "Verified AffectiveCloud SDK source is missing from the offline bundle."
[[ -f "$numcpp_dir/CMakeLists.txt" ]] || fail "Verified NumCpp source is missing from the offline bundle."

build_root="$sdk_root/build"
numcpp_prefix="$build_root/numcpp-prefix"
cmake -Wno-dev -S "$numcpp_dir" -B "$build_root/numcpp" \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$numcpp_prefix"
cmake --build "$build_root/numcpp" --parallel 2
cmake --install "$build_root/numcpp"

cmake -Wno-dev -S "$repo_root/mac/algorithm_bridge" -B "$build_root/bridge" \
  -DCMAKE_BUILD_TYPE=Release \
  -DAFFECTIVE_SDK_SOURCE_DIR="$sdk_dir" \
  -DCMAKE_PREFIX_PATH="/usr;$numcpp_prefix"
cmake --build "$build_root/bridge" --parallel 2

mkdir -p "$output_dir"
install -m 0755 "$build_root/bridge/bin/neurobridge_affective_bridge" "$output_dir/neurobridge_affective_bridge"
sha256sum "$output_dir/neurobridge_affective_bridge"
echo "Bridge built: $output_dir/neurobridge_affective_bridge"
echo "Next: run tools/run-algorithm-poc.py against a consented, completed recording before enabling it in gateway.toml."
