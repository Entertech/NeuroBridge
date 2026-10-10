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

if [[ ${NEUROBRIDGE_INSTALL_LOGGED:-0} != 1 && ${1:-} != --help && ${1:-} != -h ]]; then
  exec bash "$(dirname "${BASH_SOURCE[0]}")/run-logged.sh" "${BASH_SOURCE[0]}" "$@"
fi

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
command -v udevadm >/dev/null 2>&1 || fail "udevadm is required to authorize USB serial devices."

rule_name=70-neurobridge-usb-serial.rules
rule_source=$script_dir/$rule_name
rule=/etc/udev/rules.d/$rule_name
rule_marker='# Managed by neurobridge-bootstrap: USB serial access'
[[ -f $rule_source && ! -L $rule_source ]] || fail "Bundled USB serial rule is missing: $rule_source"
[[ ! -L $rule ]] || fail "Refusing to replace a linked USB serial rule: $rule"
had_rule=false
if [[ -e $rule ]]; then
  [[ -f $rule ]] && grep -Fxq "$rule_marker" "$rule" \
    || fail "The existing USB serial rule belongs to another deployment: $rule"
  had_rule=true
fi

apply_serial_rules() {
  udevadm control --reload-rules || return 1
  # Limit change events to serial candidates; do not trigger unrelated devices.
  udevadm trigger --action=change --subsystem-match=tty --sysname-match='ttyUSB*' || return 1
  udevadm trigger --action=change --subsystem-match=tty --sysname-match='ttyACM*' || return 1
  udevadm settle --timeout=10
}

unit=/etc/systemd/system/neurobridge.service
[[ ! -L $unit ]] || fail "Refusing to replace a linked or masked service unit: $unit"
had_unit=false
if [[ -f $unit ]]; then
  grep -q '^ExecStart=/opt/neurobridge/runtime/bin/python ' "$unit" \
    || fail "The existing service belongs to another deployment: $unit"
  had_unit=true
fi
download_dir=$(mktemp -d /var/tmp/neurobridge-bootstrap.XXXXXX)
stage=$(mktemp -d /var/tmp/neurobridge-stage.XXXXXX)
service_was_active=false
systemctl is-active --quiet neurobridge.service 2>/dev/null && service_was_active=true
service_was_enabled=false
systemctl is-enabled --quiet neurobridge.service 2>/dev/null && service_was_enabled=true
next_tree=
previous_tree=
tree_swapped=false
service_touched=false
created_config=false
committed=false
rule_touched=false

restore_installation() {
  # Keep the backup if stopping or restoring fails. Never remove a tree from
  # underneath a service that could still be using it.
  if [[ $had_unit == true || -f $unit ]]; then
    systemctl daemon-reload || return 1
    systemctl stop neurobridge.service || return 1
    systemctl disable neurobridge.service || return 1
  fi
  if systemctl is-active --quiet neurobridge.service; then
    return 1
  fi
  if [[ $tree_swapped == true ]]; then
    rm -rf -- /opt/neurobridge || return 1
    if [[ -d $previous_tree/app ]]; then
      mv "$previous_tree/app" /opt/neurobridge || return 1
    fi
  fi
  if [[ $had_unit == true ]]; then
    cp -p "$stage/previous.service" "$unit" || return 1
  else
    rm -f -- "$unit" || return 1
  fi
  if [[ $created_config == true ]]; then
    rm -f -- /etc/neurobridge/gateway.toml || return 1
  fi
  if [[ $rule_touched == true ]]; then
    if [[ $had_rule == true ]]; then
      cp -p "$stage/previous.rules" "$rule" || return 1
    else
      rm -f -- "$rule" || return 1
    fi
    apply_serial_rules || return 1
  fi
  systemctl daemon-reload || return 1
  if [[ $service_was_enabled == true ]]; then
    systemctl enable neurobridge.service || return 1
  fi
  if [[ $service_was_active == true ]]; then
    systemctl start neurobridge.service || return 1
    systemctl is-active --quiet neurobridge.service || return 1
  fi
}

cleanup() {
  local result=$?
  trap - EXIT
  if [[ $committed != true && $service_touched == true ]]; then
    echo 'PHASE rollback'
    if ! restore_installation; then
      printf 'ERROR: Rollback incomplete; backups retained at %s and %s\n' "$previous_tree" "$stage" >&2
      exit 1
    fi
    echo 'Rollback completed; previous deployment and service state restored.'
  fi
  rm -rf -- "$download_dir" "$stage"
  [[ -z $next_tree ]] || rm -rf -- "$next_tree"
  [[ -z $previous_tree ]] || rm -rf -- "$previous_tree"
  exit "$result"
}
trap cleanup EXIT
[[ $had_unit == false ]] || cp -p "$unit" "$stage/previous.service"
[[ $had_rule == false ]] || cp -p "$rule" "$stage/previous.rules"

fetch_args=(--manifest "$manifest" --destination "$download_dir")
echo 'PHASE verify-archive'
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
usermod -aG neurobridge neurobridge || fail "Cannot grant neurobridge access to its managed USB serial group."

# Retain access through the actual non-root groups of existing devices. The
# managed udev rule below also authorizes future plug-in without reinstalling.
serial_candidates=0
echo 'PHASE authorize-device'
for device in /dev/ttyACM* /dev/ttyUSB*; do
  [[ -c $device ]] || continue
  serial_candidates=$((serial_candidates + 1))
  device_group=$(stat -Lc '%G' -- "$device")
  if [[ $device_group == root ]] || ! getent group "$device_group" >/dev/null 2>&1; then
    printf 'USB TTY %s group=%s; managed udev rule will provide access.\n' "$device" "$device_group"
    continue
  fi
  usermod -aG "$device_group" neurobridge \
    || fail "Cannot grant neurobridge access to $device (group $device_group)."
  printf 'Authorized USB TTY group: %s\n' "$device_group"
done
if [[ $serial_candidates -eq 0 ]]; then
  echo 'DEVICE_STATUS waiting_for_device; no USB TTY present. Installation will continue.'
fi

install -d -o neurobridge -g neurobridge -m 0750 /var/lib/neurobridge/recordings /var/log/neurobridge
install -d -o root -g neurobridge -m 0750 /etc/neurobridge

next_tree=$(mktemp -d /opt/neurobridge-next.XXXXXX)
chmod 0755 "$next_tree"
cp -a "$stage/runtime" "$next_tree/runtime"
cp -a "$stage/payload/." "$next_tree/"
cp "$stage/gateway.toml.example" "$next_tree/gateway.toml.example"
install -d -m 0755 "$next_tree/packaging"
cp "$stage/packaging/neurobridge.service" "$next_tree/packaging/neurobridge.service"
chown -R root:root "$next_tree"
previous_tree=$(mktemp -d /opt/neurobridge-previous.XXXXXX)

# Build and stage while the previous version runs; stop before replacing any
# installed paths, so shutdown finishes against the old code and algorithm.
service_touched=true
printf 'PHASE replace-service previous_active=%s previous_enabled=%s\n' "$service_was_active" "$service_was_enabled"
if [[ $had_unit == true || $service_was_active == true ]]; then
  systemctl stop neurobridge.service
  if systemctl is-active --quiet neurobridge.service; then
    fail "neurobridge.service did not stop; the installed tree was not changed."
  fi
fi
echo 'PHASE configure-hotplug-permissions'
install -d -o root -g root -m 0755 /etc/udev/rules.d
rule_touched=true
install -o root -g root -m 0644 "$rule_source" "$rule"
apply_serial_rules || fail "Cannot apply USB serial permissions; restoring the previous deployment."
if [[ -d /opt/neurobridge ]]; then
  mv /opt/neurobridge "$previous_tree/app"
fi
tree_swapped=true
mv "$next_tree" /opt/neurobridge

if [[ ! -e /etc/neurobridge/gateway.toml ]]; then
  created_config=true
  install -o root -g neurobridge -m 0640 /opt/neurobridge/gateway.toml.example /etc/neurobridge/gateway.toml
fi
install -m 0644 /opt/neurobridge/packaging/neurobridge.service "$unit"

systemctl daemon-reload
echo 'PHASE activate-service'
if [[ $had_unit == false || $service_was_enabled == true ]]; then
  systemctl enable neurobridge.service
fi
if [[ $had_unit == false || $service_was_active == true ]]; then
  systemctl start neurobridge.service
  systemctl is-active --quiet neurobridge.service || fail "neurobridge.service did not stay active after installation."
fi
committed=true

printf 'NeuroBridge installed from %s\n' "$archive"
printf 'Configuration: /etc/neurobridge/gateway.toml (left unchanged if it already existed)\n'
if [[ $serial_candidates -eq 0 ]]; then
  echo 'Connect the headset to begin live capture; USB serial permissions apply on plug-in. No reinstall is needed.'
fi
