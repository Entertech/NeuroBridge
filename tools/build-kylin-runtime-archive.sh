#!/usr/bin/env bash
# Build the runtime archive once, on a Galaxy Kylin V10 x86_64 machine.
#
# The source it builds from does not have to be a git checkout.  The bootstrap
# package carries the same source tree, and on the one Kylin machine that
# produces the runtime this script is pointed at that tree with --source-root.
# It runs the two existing setup scripts, packs their output into one archive,
# and writes a manifest that records the archive digest next to it.  The source
# tree is never modified, so the copy inside the package stays as it shipped.
set -euo pipefail

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: build-kylin-runtime-archive.sh [--source-root <dir>] [--output-dir <dir>]

Runs on Galaxy Kylin V10 x86_64.  --source-root is the NeuroBridge tree to
build from; it defaults to the repository that contains this script.  The
bootstrap package passes the source tree it carries.

Builds the project Python runtime and the algorithm bridge, packs them into
one archive under --output-dir, and writes kylin-runtime-manifest.toml beside
it with the archive sha256 filled in.  The source tree is not modified.

The archive contains, at its top level:
  runtime/                         Python 3.11 and the algorithm bridge
  payload/                         gateway source the service runs
  gateway.toml.example             configuration copied on first install
  packaging/neurobridge.service    unit installed for the service

Nothing is downloaded by this script itself; the setup scripts it calls
download their own pinned inputs, or reuse copies already in the tree.
EOF
}

script_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
source_root=$script_root
output_dir=

while [[ $# -gt 0 ]]; do
  case $1 in
    --source-root) source_root=${2:-}; shift 2 ;;
    --output-dir) output_dir=${2:-}; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) fail "Unknown option: $1" ;;
  esac
done

[[ -n $source_root && -d $source_root && ! -L $source_root ]] || fail "Source root is missing or is a symlink: ${source_root:-<unset>}"
source_root=$(cd "$source_root" && pwd -P)
[[ -n $output_dir ]] || output_dir=$source_root/build/kylin-runtime

# Root is only acceptable when the bootstrap package drives the build, because
# a package install runs as root and has no desktop user to drop to.
[[ ${EUID:-$(id -u)} -ne 0 || ${NEUROBRIDGE_BOOTSTRAP:-} == 1 ]] || fail "Run as the normal desktop user. The setup scripts use sudo only where they need it."
[[ $(uname -m) == x86_64 ]] || fail "The runtime archive is built for x86_64; detected $(uname -m)."
[[ -r /etc/os-release ]] || fail "/etc/os-release is unavailable."
# shellcheck disable=SC1091
. /etc/os-release
[[ ${ID,,} == kylin ]] || fail "This archive must be built on Galaxy Kylin; detected ID=${ID:-unknown}."
[[ -f $source_root/pyproject.toml && ! -L $source_root/pyproject.toml ]] || fail "Source root is not a NeuroBridge tree: $source_root"

python_runtime=$source_root/python-runtime/python
bridge=$source_root/.runtime/algorithm/neurobridge_affective_bridge

[[ -x $python_runtime/bin/python3 && -x $bridge ]] || {
  printf 'Python runtime or algorithm bridge is not built yet; running the existing setup.\n'
  "$source_root/linux/setup-kylin-python.sh"
  install -d -m 0750 "$source_root/.runtime/config"
  [[ -f $source_root/.runtime/config/gateway.toml ]] || cp "$source_root/config/gateway.toml.example" "$source_root/.runtime/config/gateway.toml"
  "$source_root/linux/setup-kylin-algorithm.sh"
}
[[ -x $python_runtime/bin/python3 ]] || fail "Python runtime was not produced at $python_runtime."
[[ -x $bridge ]] || fail "Algorithm bridge was not produced at $bridge."
"$python_runtime/bin/python3" -c 'import sys; assert sys.version_info >= (3, 11)' \
  || fail "The built Python runtime is older than 3.11."

template=$source_root/config/kylin-runtime-manifest.toml
[[ -f $template && ! -L $template ]] || fail "Runtime manifest template is missing: $template"
file_name=$(awk -F'"' '/^file_name = / { print $2; exit }' "$template")
[[ $file_name =~ ^[A-Za-z0-9._+-]+$ ]] || fail "Manifest file name is not a safe path component: ${file_name:-<empty>}"

work=$(mktemp -d "${TMPDIR:-/tmp}/neurobridge-runtime.XXXXXX")
cleanup() { rm -rf -- "$work"; }
trap cleanup EXIT

stage=$work/stage
install -d -m 0755 "$stage/runtime/bin" "$stage/payload" "$stage/packaging"

cp -a "$python_runtime/." "$stage/runtime/bin/python-runtime/"
ln -s python-runtime/bin/python3 "$stage/runtime/bin/python"
install -m 0755 "$bridge" "$stage/runtime/bin/neurobridge_affective_bridge"

# The wheels are installed into .venv, not into the portable interpreter, so the
# interpreter copied above cannot import serial or websockets on its own.  Copy
# the installed packages across.  The gateway source is copied separately.
venv_site=("$source_root"/.venv/lib/python*/site-packages)
[[ -d ${venv_site[0]} ]] || fail "No installed packages found in .venv. Run linux/setup-kylin-python.sh before building the archive."
runtime_site=$("$stage/runtime/bin/python" -c 'import site; print([p for p in site.getsitepackages() if p.endswith("site-packages")][0])')
[[ -n $runtime_site && -d $runtime_site ]] || fail "The staged interpreter has no site-packages directory."
"$source_root/tools/stage-venv-packages.sh" "${venv_site[0]}" "$runtime_site"
[[ -x $stage/runtime/bin/python && -x $stage/runtime/bin/neurobridge_affective_bridge ]] \
  || fail "Staged runtime is incomplete."

for name in neurobridge web requirements.lock pyproject.toml sdk.lock config; do
  [[ -e $source_root/$name ]] || fail "Payload is missing from the source tree: $name"
  cp -a "$source_root/$name" "$stage/payload/$name"
done
cp "$source_root/config/gateway.toml.example" "$stage/gateway.toml.example"
cp "$source_root/packaging/kylin/neurobridge.service" "$stage/packaging/neurobridge.service"

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

# The manifest is written next to the archive, not back into the source tree:
# the tree shipped inside the bootstrap package must stay unchanged.
manifest=$output_dir/kylin-runtime-manifest.toml
awk -v digest="$digest" '
  /^sha256 = / { print "sha256 = \"" digest "\""; next }
  { print }
' "$template" >"$manifest"
grep -q "^sha256 = \"${digest}\"$" "$manifest" || fail "Could not record the archive digest in the manifest."

printf 'archive=%s\n' "$archive"
printf 'sha256=%s\n' "$digest"
printf 'manifest=%s\n' "$manifest"
printf 'The download URL in the manifest is unchanged. Set it to where this archive is published before building the bootstrap installer for other machines.\n'
