#!/usr/bin/env bash
set -euo pipefail
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
[[ -r /etc/os-release ]] || { echo 'Cannot identify the operating system.' >&2; exit 1; }
. /etc/os-release
[[ ${ID,,} == ubuntu && $(uname -m) == x86_64 ]] || { echo 'Ubuntu compatibility build requires Ubuntu x86_64.' >&2; exit 1; }
export NEUROBRIDGE_BRIDGE_PLATFORM=ubuntu NEUROBRIDGE_EIGEN_LOCK_KEY=ubuntu_24_04_x86_64
export NEUROBRIDGE_BRIDGE_OS_ID="$ID" NEUROBRIDGE_BRIDGE_OS_VERSION="$VERSION_ID"
exec bash "$repo_root/linux/common/build-algorithm-bridge.sh" "$@"
