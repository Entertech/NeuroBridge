#!/usr/bin/env bash
# Produce the runtime archive that every Kylin bootstrap installer consumes.
#
# This is the one step that has to run on Galaxy Kylin V10 x86_64, because the
# Python runtime and the algorithm bridge are built for that machine.  It runs
# the two existing setup scripts, then packs their output into one archive and
# records the archive digest in the manifest the installer ships with.  No
# other machine needs a compiler or a checkout of this repository.
set -euo pipefail

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: tools/build-kylin-runtime-archive.sh [--output-dir <directory>]

Runs on Galaxy Kylin V10 x86_64.  Builds the project Python runtime and the
algorithm bridge, packs them into one archive, and writes that archive's
sha256 into config/kylin-runtime-manifest.toml.

The archive contains, at its top level:
  runtime/                         Python 3.11 and the algorithm bridge
  payload/                         gateway source the service runs
  gateway.toml.example             configuration copied on first install
  packaging/neurobridge.service    unit installed for the service

Nothing is downloaded by this script itself; the setup scripts it calls
download their own pinned inputs, or reuse copies already in the tree.
EOF
}

root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
output_dir=$root_dir/build/kylin-runtime

while [[ $# -gt 0 ]]; do
  case $1 in
    --output-dir) output_dir=${2:-}; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) fail "Unknown option: $1" ;;
  esac
done

[[ ${EUID:-$(id -u)} -ne 0 ]] || fail "Run as the normal desktop user. The setup scripts use sudo only where they need it."
[[ $(uname -m) == x86_64 ]] || fail "The runtime archive is built for x86_64; detected $(uname -m)."
[[ -r /etc/os-release ]] || fail "/etc/os-release is unavailable."
# shellcheck disable=SC1091
. /etc/os-release
[[ ${ID,,} == kylin ]] || fail "This archive must be built on Galaxy Kylin; detected ID=${ID:-unknown}."
[[ -f $root_dir/pyproject.toml ]] || fail "Run this from a NeuroBridge checkout."

python_runtime=$root_dir/python-runtime/python
bridge=$root_dir/.runtime/algorithm/neurobridge_affective_bridge

[[ -x $python_runtime/bin/python3 && -x $bridge ]] || {
  printf 'Python runtime or algorithm bridge is not built yet; running the existing setup.\n'
  "$root_dir/linux/setup-kylin-python.sh"
  install -d -m 0750 "$root_dir/.runtime/config"
  [[ -f $root_dir/.runtime/config/gateway.toml ]] || cp "$root_dir/config/gateway.toml.example" "$root_dir/.runtime/config/gateway.toml"
  "$root_dir/linux/setup-kylin-algorithm.sh"
}
[[ -x $python_runtime/bin/python3 ]] || fail "Python runtime was not produced at $python_runtime."
[[ -x $bridge ]] || fail "Algorithm bridge was not produced at $bridge."
"$python_runtime/bin/python3" -c 'import sys; assert sys.version_info >= (3, 11)' \
  || fail "The built Python runtime is older than 3.11."

manifest=$root_dir/config/kylin-runtime-manifest.toml
[[ -f $manifest && ! -L $manifest ]] || fail "Runtime manifest is missing: $manifest"
file_name=$(awk -F'"' '/^file_name = / { print $2; exit }' "$manifest")
[[ $file_name =~ ^[A-Za-z0-9._+-]+$ ]] || fail "Manifest file name is not a safe path component: ${file_name:-<empty>}"

work=$(mktemp -d "${TMPDIR:-/tmp}/neurobridge-runtime.XXXXXX")
cleanup() { rm -rf -- "$work"; }
trap cleanup EXIT

stage=$work/stage
install -d -m 0755 "$stage/runtime/bin" "$stage/payload" "$stage/packaging"

cp -a "$python_runtime/." "$stage/runtime/bin/python-runtime/"
ln -s python-runtime/bin/python3 "$stage/runtime/bin/python"
install -m 0755 "$bridge" "$stage/runtime/bin/neurobridge_affective_bridge"
[[ -x $stage/runtime/bin/python && -x $stage/runtime/bin/neurobridge_affective_bridge ]] \
  || fail "Staged runtime is incomplete."

for name in neurobridge web requirements.lock pyproject.toml sdk.lock config; do
  [[ -e $root_dir/$name ]] || fail "Payload is missing from the checkout: $name"
  cp -a "$root_dir/$name" "$stage/payload/$name"
done
cp "$root_dir/config/gateway.toml.example" "$stage/gateway.toml.example"
cp "$root_dir/packaging/kylin/neurobridge.service" "$stage/packaging/neurobridge.service"

# The archive is only worth publishing if the interpreter it carries can
# import the gateway it carries.  This is the same check the per-machine
# installer repeats before it enables the service.
PYTHONPATH=$stage/payload "$stage/runtime/bin/python" -c 'import neurobridge, serial, websockets' \
  || fail "The staged runtime cannot import the gateway. The archive was not written."

mkdir -p -- "$output_dir"
archive=$output_dir/$file_name
rm -f -- "$archive"
tar -C "$stage" -czf "$archive" .
[[ -f $archive && ! -L $archive ]] || fail "Archive was not written: $archive"

digest=$(sha256sum -- "$archive" | awk '{print $1}')
[[ $digest =~ ^[0-9a-f]{64}$ ]] || fail "Could not hash the archive."

# Record the digest in place.  The URL is left untouched: publishing the
# archive and deciding where it is fetched from is a separate step.
updated=$(mktemp "$work/manifest.XXXXXX")
awk -v digest="$digest" '
  /^sha256 = / { print "sha256 = \"" digest "\""; next }
  { print }
' "$manifest" >"$updated"
grep -q "^sha256 = \"${digest}\"$" "$updated" || fail "Could not record the archive digest in the manifest."
cp -- "$updated" "$manifest"

printf 'archive=%s\n' "$archive"
printf 'sha256=%s\n' "$digest"
printf 'manifest=%s\n' "$manifest"
printf 'The download URL in the manifest is unchanged. Set it to where this archive is published before building the bootstrap installer.\n'
