#!/usr/bin/env bash
set -euo pipefail
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
[[ -r /etc/os-release ]] || { echo 'Cannot identify the operating system.' >&2; exit 1; }
. /etc/os-release
[[ ${ID,,} == kylin ]] || { echo 'This build requires Galaxy Kylin.' >&2; exit 1; }
NB_INPUT_LOCK="$repo_root/config/kylin-bootstrap-inputs.toml"
. "$repo_root/packaging/kylin/platform.sh"
nb_select_platform && nb_require_compiler || exit 1
export NEUROBRIDGE_BRIDGE_PLATFORM=galaxy-kylin NEUROBRIDGE_EIGEN_LOCK_KEY="$NB_EIGEN_LOCK"
export NEUROBRIDGE_BRIDGE_OS_ID="$ID" NEUROBRIDGE_BRIDGE_OS_VERSION="$VERSION_ID"
exec bash "$repo_root/linux/common/build-algorithm-bridge.sh" "$@"
