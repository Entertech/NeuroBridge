#!/usr/bin/env bash
# Compatibility entrypoint; implementation lives in linux/ubuntu/setup-ssh-operations.sh.
set -euo pipefail
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/linux/ubuntu/setup-ssh-operations.sh" "$@"
