#!/bin/sh
# Keep the bundled standard library discoverable after copying or relocating
# the runtime, even when interpreter symlinks have been flattened.
set -eu
runtime_prefix=$(CDPATH= cd -- "$(dirname -- "$0")/python-runtime" && pwd -P)
PYTHONHOME="$runtime_prefix" exec "$runtime_prefix/bin/python3" "$@"
