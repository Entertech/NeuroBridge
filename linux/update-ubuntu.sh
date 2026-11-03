#!/usr/bin/env bash
# Compatibility entrypoint; implementation lives in linux/ubuntu/update-ubuntu.sh.
set -euo pipefail
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/linux/ubuntu/update-ubuntu.sh" "$@"
