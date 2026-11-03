#!/usr/bin/env bash
# Compatibility entrypoint; implementation lives in linux/ubuntu/prepare-ubuntu24.04-environment.sh.
set -euo pipefail
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/linux/ubuntu/prepare-ubuntu24.04-environment.sh" "$@"
