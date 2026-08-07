#!/usr/bin/env bash
# Build the locked AffectiveCloud C++ bridge on the target Ubuntu x86_64 POC host.
# Run as the designated POC operator, never as root.  The output is a reviewable
# local artifact; install it to a service path only after the POC gate passes.
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
for command in git cmake c++ python3; do
  command -v "$command" >/dev/null 2>&1 || fail "Missing $command. Install: sudo apt-get install -y build-essential cmake git libeigen3-dev"
done
[[ -d /usr/include/eigen3 ]] || fail "Eigen3 headers are missing. Install: sudo apt-get install -y libeigen3-dev"

readarray -t locked < <(python3 - "$repo_root/sdk.lock" <<'PY'
import sys
import tomllib

with open(sys.argv[1], "rb") as source:
    lock = tomllib.load(source)
sdk = lock["affective_algorithm_sdk"]
build = sdk["build"]
print(sdk["repository"])
print(sdk["commit"])
print(build["numcpp_repository"])
print(build["numcpp_commit"])
PY
)
sdk_repo=${locked[0]}
sdk_commit=${locked[1]}
numcpp_repo=${locked[2]}
numcpp_commit=${locked[3]}
sdk_dir="$sdk_root/AffectiveCloud-Algorithm-SDK"
numcpp_dir="$sdk_root/NumCpp"

if [[ ! -d "$sdk_dir/.git" ]]; then
  [[ ! -d "$sdk_root" ]] || [[ -z $(find "$sdk_root" -mindepth 1 -maxdepth 1 -print -quit) ]] \
    || fail "Controlled SDK directory is not empty but does not contain the locked SDK: $sdk_root"
  "$repo_root/tools/prepare-algorithm-sdk.sh" "$sdk_root"
fi
[[ $(git -C "$sdk_dir" rev-parse HEAD) == "$sdk_commit" ]] || fail "AffectiveCloud SDK commit differs from sdk.lock. Recreate the controlled SDK directory."

if [[ ! -d "$numcpp_dir/.git" ]]; then
  git clone "$numcpp_repo" "$numcpp_dir"
fi
if ! git -C "$numcpp_dir" cat-file -e "$numcpp_commit^{commit}" 2>/dev/null; then
  git -C "$numcpp_dir" fetch --tags
fi
git -C "$numcpp_dir" checkout --detach "$numcpp_commit"
[[ $(git -C "$numcpp_dir" rev-parse HEAD) == "$numcpp_commit" ]] || fail "NumCpp commit differs from sdk.lock."

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
