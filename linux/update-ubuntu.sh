#!/usr/bin/env bash
# Deploy a checked-out Ubuntu 24.04 gateway source tree with one command.
# This script intentionally performs no Git or network operation.
set -euo pipefail

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root_dir"
[[ $# -eq 0 ]] || fail "Usage: $0"
[[ -f pyproject.toml && -f linux/install-ubuntu.sh ]] || fail "Run this from a complete NeuroBridge source checkout."

if [[ ${EUID} -ne 0 ]]; then
  command -v sudo >/dev/null 2>&1 || fail "Administrator permission is required; run: sudo bash linux/update-ubuntu.sh"
  exec sudo --preserve-env=PATH bash "$0"
fi

echo "WARNING: Updating restarts the gateway; B-end WebSocket clients must reconnect and subscribe again."
bash "$root_dir/linux/install-ubuntu.sh"
systemctl restart neurobridge.service
systemctl --no-pager --full status neurobridge.service
