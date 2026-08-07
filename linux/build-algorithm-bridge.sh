#!/usr/bin/env bash
# Build the bridge from SDK sources vendored in the NeuroBridge checkout.
# No source download or GitHub request is permitted after this checkout exists.
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
  command -v "$command" >/dev/null 2>&1 || fail "Missing system prerequisite: $command"
done
[[ -d /usr/include/eigen3 ]] || fail "Eigen3 headers are missing. Install libeigen3-dev in the Ubuntu base image."
sdk_dir="$sdk_root/AffectiveCloud-Algorithm-SDK"
numcpp_dir="$sdk_root/NumCpp"
[[ -f "$sdk_dir/cpp/package/CMakeLists.txt" ]] || fail "Vendored AffectiveCloud SDK source is missing from this checkout."
[[ -f "$numcpp_dir/CMakeLists.txt" ]] || fail "Vendored NumCpp source is missing from this checkout."

build_root="$output_dir/build"
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

mkdir -p "$output_dir"
install -m 0755 "$build_root/bridge/bin/neurobridge_affective_bridge" "$output_dir/neurobridge_affective_bridge"
sha256sum "$output_dir/neurobridge_affective_bridge"
echo "Bridge built: $output_dir/neurobridge_affective_bridge"
echo "Next: run tools/run-algorithm-poc.py against a consented, completed recording before enabling it in gateway.toml."
