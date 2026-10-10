#!/usr/bin/env bash
# Standalone DEB entry: select resources before the first package configure.
set -euo pipefail
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
usage() {
  cat <<'EOF'
Usage: sudo bash install-bootstrap.sh --package <bootstrap.deb> [resource [URL|file] ...]

Resources: python cmake eigen pyserial websockets.
No resource arguments, or a resource name alone, use the bundled input.
An HTTPS URL forces download; a file path forces that local archive or wheel.
Every input must match the bundled lock for this machine's Kylin V10 CPU/ABI.
Relative paths use the invoking directory; quote paths containing spaces.

Example: sudo bash install-bootstrap.sh --package ./bootstrap.deb cmake /media/usb/cmake.tar.gz
Prepare system build dependencies separately before running this command.
Selections apply only to this attempt; rerun this command with the same
arguments after a failure. Plain dpkg configure uses bundled defaults.
EOF
}
[[ ${1:-} != -h && ${1:-} != --help ]] || { usage; exit 0; }
[[ ${EUID:-$(id -u)} -eq 0 ]] || fail 'Run as root.'
[[ ${1:-} == --package && -n ${2:-} ]] || { usage >&2; exit 1; }
package=$2
shift 2
[[ -f $package && ! -L $package ]] || fail 'Package is missing or is a symlink.'
package=$(cd "$(dirname "$package")" && pwd -P)/$(basename "$package")
[[ $(dpkg-deb --field "$package" Package) == neurobridge-bootstrap ]] || fail 'Not a NeuroBridge bootstrap DEB.'
work=$(mktemp -d /var/tmp/neurobridge-package.XXXXXX)
trap 'rm -rf -- "$work"' EXIT
dpkg-deb --extract "$package" "$work/package"
payload=$work/package/usr/lib/neurobridge-bootstrap
for file in platform.sh resources.sh run-logged.sh install-bootstrap.sh kylin-bootstrap-inputs.toml; do
  [[ -f $payload/$file && ! -L $payload/$file ]] || fail "Package lacks resource-selection support: $file"
done
# The initial extraction finds the package's logger without any installed
# files. The logged invocation owns its own staging tree and keeps its inputs
# alive until dpkg's synchronous configure (and postinst) has returned.
if [[ ${NEUROBRIDGE_INSTALL_LOGGED:-0} != 1 ]]; then
  bash "$payload/run-logged.sh" "$payload/install-bootstrap.sh" --package "$package" "$@"
  exit $?
fi
NB_INPUT_LOCK=$payload/kylin-bootstrap-inputs.toml
. "$payload/platform.sh"
. "$payload/resources.sh"
nb_parse_resources "$@" || fail 'Invalid resource selection.'
nb_select_platform || fail 'Unsupported target OS/CPU.'
nb_require_python_development || fail 'Prepare native development dependencies first.'
nb_prepare_resources "$payload/source" "$work/inputs" || fail 'Resource preflight failed; package was not installed.'
nb_event install_package 'resources_verified=true'
# Do not invoke apt from postinst or while dpkg owns the package lock.
NEUROBRIDGE_RESOURCE_DIR=$work/inputs dpkg --install "$package"
