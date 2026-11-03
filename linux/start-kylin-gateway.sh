#!/usr/bin/env bash
# Compatibility entrypoint; implementation lives in linux/kylin/start-kylin-gateway.sh.
set -euo pipefail
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/linux/kylin/start-kylin-gateway.sh" "$@"
