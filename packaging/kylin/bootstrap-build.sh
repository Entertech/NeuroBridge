#!/usr/bin/env bash
# Run the one-time Kylin build from the source tree this package carries.
#
# The bootstrap package holds two things: the per-machine installer, and the
# source plus scripts that produce the runtime.  On the single Galaxy Kylin
# machine that produces the runtime, this is the only command to run.  It
# builds the runtime from the bundled source tree, then hands the resulting
# archive to the per-machine installer, which verifies it and installs it on
# this same machine.  No archive has to be published anywhere first.
set -euo pipefail

if [[ ${NEUROBRIDGE_INSTALL_LOGGED:-0} != 1 && ${1:-} != --help && ${1:-} != -h ]]; then
  exec bash "$(dirname "${BASH_SOURCE[0]}")/run-logged.sh" "${BASH_SOURCE[0]}" "$@"
fi

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: bootstrap-build.sh

Runs on the one Galaxy Kylin V10 x86_64 machine that produces the runtime.
Builds the runtime from the source tree shipped in this package, writes the
archive and its manifest under /var/lib/neurobridge-bootstrap/runtime, then
installs that archive on this machine.

The package's postinst runs this on install, so it is not normally invoked by
hand.  It runs as root: the package declares the compiler and Eigen as
dependencies, and the build happens in a disposable copy of the source tree.
EOF
}

if [[ ${1:-} == -h || ${1:-} == --help ]]; then
  usage
  exit 0
fi
[[ $# -eq 0 ]] || fail "Unknown option: $1"

[[ ${EUID:-$(id -u)} -eq 0 ]] || fail "Run as root."
[[ $(uname -m) == x86_64 ]] || fail "The runtime is built for x86_64; detected $(uname -m)."
[[ -r /etc/os-release ]] || fail "/etc/os-release is unavailable."
# shellcheck disable=SC1091
. /etc/os-release
[[ ${ID,,} == kylin ]] || fail "This build requires Galaxy Kylin; detected ID=${ID:-unknown}."

package_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
source_root=$package_dir/source
builder=$source_root/tools/build-kylin-runtime-archive.sh
installer=$package_dir/bootstrap-install.sh
output_dir=/var/lib/neurobridge-bootstrap/runtime
build_log_keep=${NEUROBRIDGE_BUILD_LOG_KEEP:-40}
[[ $build_log_keep =~ ^[1-9][0-9]{0,2}$ && $build_log_keep -le 100 ]] || fail "NEUROBRIDGE_BUILD_LOG_KEEP must be 1..100."

[[ -d $source_root && ! -L $source_root ]] || fail "This package has no bundled source tree: $source_root"
[[ -x $builder ]] || fail "Bundled runtime build script is missing or not executable: $builder"
[[ -x $installer ]] || fail "Bundled installer is missing or not executable: $installer"

# The bundled source tree is root-owned and must stay as shipped, so the build
# runs in a copy that is removed afterwards.  The setup scripts allow root only
# when NEUROBRIDGE_BOOTSTRAP=1, which is set here and nowhere else.
work=$(mktemp -d /var/tmp/neurobridge-build.XXXXXX)
cleanup() {
  status=$?
  # Setup/CMake logs used to disappear with this disposable build tree.
  log_dir=/var/log/neurobridge-bootstrap
  index=0
  while IFS= read -r file; do
    index=$((index + 1))
    tail -c 2097152 "$file" > "$log_dir/build-$(date -u +%Y%m%dT%H%M%SZ)-$$-$index.log" || true
  done < <(find "$work/source/.runtime" -type f -name '*.log' 2>/dev/null)
  for old in $(find "$log_dir" -maxdepth 1 -type f -name 'build-*.log' | sort -r | tail -n +"$((build_log_keep + 1))"); do rm -f -- "$old"; done
  rm -rf -- "$work"
  return "$status"
}
trap cleanup EXIT

build_tree=$work/source
echo 'PHASE prepare-source'
cp -a "$source_root/." "$build_tree/"
[[ ! -f $package_dir/build-info.txt ]] || cp "$package_dir/build-info.txt" "$build_tree/build-info.txt"

echo 'PHASE build-runtime'
NEUROBRIDGE_BOOTSTRAP=1 "$build_tree/tools/build-kylin-runtime-archive.sh" \
  --source-root "$build_tree" --output-dir "$work/output"

archive=$(find "$work/output" -maxdepth 1 -name '*.tar.gz' -type f)
[[ -n $archive && $(printf '%s\n' "$archive" | wc -l) -eq 1 ]] || fail "The build did not produce exactly one runtime archive."
manifest=$work/output/kylin-runtime-manifest.toml
[[ -f $manifest ]] || fail "The build did not write a runtime manifest."

# Keep the archive and the manifest that records its digest.  Other machines
# install from a copy of this directory; nothing here is published anywhere.
install -d -m 0755 "$output_dir"
cp -p -- "$archive" "$output_dir/"
cp -p -- "$manifest" "$output_dir/"

echo 'PHASE deploy-runtime'
"$installer" --local-archive "$archive" --manifest "$manifest"

printf 'Runtime built and installed from %s\n' "$output_dir"
printf 'Other machines install with: bootstrap-install.sh --local-archive <copy of %s>\n' "$(basename "$archive")"
