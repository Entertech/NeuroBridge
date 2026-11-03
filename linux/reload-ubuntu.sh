#!/usr/bin/env bash
# Compatibility for SSH update helpers installed before the platform split.
set -euo pipefail
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/linux/ubuntu/reload-ubuntu.sh" "$@"
