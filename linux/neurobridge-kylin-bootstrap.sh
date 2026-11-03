#!/usr/bin/env bash
# Compatibility entrypoint; implementation lives in linux/kylin/neurobridge-kylin-bootstrap.sh.
set -euo pipefail
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/linux/kylin/neurobridge-kylin-bootstrap.sh" "$@"
