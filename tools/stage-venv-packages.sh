#!/usr/bin/env bash
# Copy the packages installed in a .venv into a portable interpreter.
#
# setup-kylin-python.sh installs the locked wheels into .venv.  The runtime
# archive is built from the portable interpreter, which has none of those
# packages, so without this step the archive cannot import serial or
# websockets.  Editable installs are skipped: they only record a path back into
# the source tree, and that path does not exist on the machine being installed.
set -euo pipefail

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

[[ $# -eq 2 ]] || fail "Usage: stage-venv-packages.sh <venv-site-packages> <runtime-site-packages>"
venv_site=$1
runtime_site=$2
[[ -d $venv_site && ! -L $venv_site ]] || fail "venv site-packages is missing: $venv_site"
[[ -d $runtime_site && ! -L $runtime_site ]] || fail "runtime site-packages is missing: $runtime_site"

for entry in "$venv_site"/*; do
  [[ -e $entry ]] || continue
  name=$(basename "$entry")
  case $name in
    __pycache__|_distutils_hack|*.pth) continue ;;
  esac
  # An editable install's dist-info starts with a line naming the source path.
  if [[ -f $entry ]] && grep -q '^editable' "$entry" 2>/dev/null; then
    continue
  fi
  cp -a "$entry" "$runtime_site/$name"
done
