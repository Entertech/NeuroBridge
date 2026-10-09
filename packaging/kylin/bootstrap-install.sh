#!/usr/bin/env bash
# Install NeuroBridge on one Galaxy Kylin machine from a fetched runtime.
#
# The package that ships this script carries no Python runtime and no compiled
# bridge.  Those are produced once, on a single Kylin machine, and arrive here
# as one archive: downloaded from the URL in the manifest, or read from a local
# copy when this machine cannot reach that URL.  This script checks the
# archive, unpacks it, and only then creates the account and enables the
# service.  A failed check leaves the machine as it was.
set -euo pipefail

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: bootstrap-install.sh [--local-archive <file>] [--manifest <file>]

Installs NeuroBridge for the current user of this machine.  Run as root.

With no arguments the runtime archive is downloaded from the URL recorded in
the bundled manifest.  --local-archive installs from a copy of that archive
already on this machine and does not use the network; use it for a machine
that cannot reach the publish location.

--manifest names the manifest to verify against.  It defaults to the manifest
shipped in this package.  The one-time build on a Kylin machine passes the
manifest it just wrote, whose sha256 matches the archive it just built.

The archive is verified against the manifest sha256 before it is unpacked.
Configuration, recordings and logs that already exist are kept.
EOF
}

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
manifest=$script_dir/kylin-runtime-manifest.toml
fetch=$script_dir/fetch-runtime.sh
local_archive=

while [[ $# -gt 0 ]]; do
  case $1 in
    --local-archive) local_archive=${2:-}; shift 2 ;;
    --manifest) manifest=${2:-}; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) fail "Unknown option: $1" ;;
  esac
done

[[ ${EUID:-$(id -u)} -eq 0 ]] || fail "Run as root."
[[ $(uname -m) == x86_64 ]] || fail "This installer requires x86_64; detected $(uname -m)."
[[ -r /etc/os-release ]] || fail "/etc/os-release is unavailable."
# shellcheck disable=SC1091
. /etc/os-release
[[ ${ID,,} == kylin ]] || fail "This installer requires Galaxy Kylin; detected ID=${ID:-unknown}."
[[ -f $manifest && ! -L $manifest ]] || fail "Bundled runtime manifest is missing: $manifest"
[[ -x $fetch ]] || fail "Bundled fetch script is missing or not executable: $fetch"

command -v tar >/dev/null 2>&1 || fail "tar is required to unpack the runtime archive."
command -v systemctl >/dev/null 2>&1 || fail "systemctl is required to install the service."

download_dir=$(mktemp -d /var/tmp/neurobridge-bootstrap.XXXXXX)
stage=$(mktemp -d /var/tmp/neurobridge-stage.XXXXXX)
cleanup() { rm -rf -- "$download_dir" "$stage"; }
trap cleanup EXIT

fetch_args=(--manifest "$manifest" --destination "$download_dir")
[[ -z $local_archive ]] || fetch_args+=(--local-archive "$local_archive")
archive=$("$fetch" "${fetch_args[@]}")
[[ -f $archive && ! -L $archive ]] || fail "Fetch step did not return an archive."

tar -xzf "$archive" -C "$stage"
[[ -x $stage/runtime/bin/python ]] || fail "Archive has no runtime/bin/python."
[[ -x $stage/runtime/bin/neurobridge_affective_bridge ]] || fail "Archive has no runtime/bin/neurobridge_affective_bridge."
[[ -d $stage/payload/neurobridge ]] || fail "Archive has no gateway payload."
[[ -f $stage/gateway.toml.example ]] || fail "Archive has no configuration template."
[[ -f $stage/packaging/neurobridge.service ]] || fail "Archive has no service unit."

# Confirm the interpreter can load the gateway it shipped with before any
# account or service is created.  A corrupt archive stops here.
PYTHONPATH=$stage/payload "$stage/runtime/bin/python" -c 'import neurobridge' \
  || fail "The runtime archive cannot import the gateway. Nothing was installed."

getent group neurobridge >/dev/null 2>&1 || groupadd --system neurobridge
id -u neurobridge >/dev/null 2>&1 || useradd --system --gid neurobridge --home-dir /nonexistent --shell /usr/sbin/nologin neurobridge

install -d -o neurobridge -g neurobridge -m 0750 /var/lib/neurobridge/recordings /var/log/neurobridge
install -d -o root -g neurobridge -m 0750 /etc/neurobridge
install -d -o root -g root -m 0755 /opt/neurobridge

# Replace the installed tree in one move so a running service never sees a
# half-written directory.  An existing tree is kept beside it until the new
# one is in place, then removed.
rm -rf -- /opt/neurobridge.next
mkdir -m 0755 /opt/neurobridge.next
cp -a "$stage/runtime" /opt/neurobridge.next/runtime
cp -a "$stage/payload/." /opt/neurobridge.next/
cp "$stage/gateway.toml.example" /opt/neurobridge.next/gateway.toml.example
install -d -m 0755 /opt/neurobridge.next/packaging
cp "$stage/packaging/neurobridge.service" /opt/neurobridge.next/packaging/neurobridge.service
chown -R root:root /opt/neurobridge.next

if [[ -d /opt/neurobridge ]]; then
  rm -rf -- /opt/neurobridge.previous
  mv /opt/neurobridge /opt/neurobridge.previous
fi
mv /opt/neurobridge.next /opt/neurobridge
rm -rf -- /opt/neurobridge.previous

[[ -e /etc/neurobridge/gateway.toml ]] || install -o root -g neurobridge -m 0640 \
  /opt/neurobridge/gateway.toml.example /etc/neurobridge/gateway.toml
install -m 0644 /opt/neurobridge/packaging/neurobridge.service /etc/systemd/system/neurobridge.service

systemctl daemon-reload
systemctl enable neurobridge.service
systemctl restart neurobridge.service
systemctl is-active --quiet neurobridge.service || fail "neurobridge.service did not stay active after installation."

printf 'NeuroBridge installed from %s\n' "$archive"
printf 'Configuration: /etc/neurobridge/gateway.toml (left unchanged if it already existed)\n'
