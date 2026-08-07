#!/usr/bin/env bash
# One-command installer for an offline Ubuntu 24.04 gateway delivery.
set -euo pipefail

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

if [[ ${EUID} -ne 0 ]]; then
  command -v sudo >/dev/null 2>&1 || fail "Administrator permission is required, but sudo is unavailable."
  exec sudo --preserve-env=PATH bash "$0" "$@"
fi

root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
bundle_name=neurobridge-ubuntu24.04-offline-bundle
bundle_dir=${NEUROBRIDGE_OFFLINE_BUNDLE:-"$root_dir/../$bundle_name"}

if [[ $# -gt 1 || ${1:-} == "--help" || ${1:-} == "-h" ]]; then
  echo "Usage: $0 [offline-bundle-directory]" >&2
  echo "Default: ../$bundle_name (next to the NeuroBridge source directory)" >&2
  exit 2
fi
if [[ $# -eq 1 ]]; then
  bundle_dir=$1
fi
case "$bundle_dir" in /*) ;; *) bundle_dir=$(cd "$(dirname "$bundle_dir")" && pwd)/$(basename "$bundle_dir");; esac

[[ -r "$bundle_dir/manifest.toml" ]] || fail "Offline bundle is missing: $bundle_dir. Put $bundle_name next to the NeuroBridge source directory, or pass its directory as the single argument."
exec "$root_dir/linux/install-ubuntu.sh" --offline-bundle "$bundle_dir"
