#!/usr/bin/env bash
# Compatibility dispatcher. New callers use the explicit platform entrypoint.
set -euo pipefail
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
. /etc/os-release
case ${ID,,} in
  ubuntu|kylin) exec bash "$repo_root/linux/$ID/build-algorithm-bridge.sh" "$@" ;;
  *) echo "Unsupported Linux platform: ${ID:-unknown}" >&2; exit 1 ;;
esac
