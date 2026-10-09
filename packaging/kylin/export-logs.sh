#!/usr/bin/env bash
# Export NeuroBridge operational logs for support.
#
# Works for both deployment shapes on Galaxy Kylin V10:
#   * source checkout  -> <checkout>/.runtime/logs
#   * deb/rpm install  -> /var/log/neurobridge
# The layout is detected, so the same command serves both.
#
# This command is read-only. It never stops or restarts the service, never
# changes configuration, and never copies recordings, raw device data or the
# contents of the gateway configuration.
set -euo pipefail

unit_name=neurobridge.service
unit_path="/etc/systemd/system/$unit_name"
package_root=/opt/neurobridge
package_config=/etc/neurobridge/gateway.toml
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
# From a checkout this script sits at <checkout>/packaging/kylin/.  Installed by
# a deb/rpm it sits at /opt/neurobridge/kylin/, where two levels up is /opt, so
# fall back to the application root rather than treating /opt as a checkout.
checkout_root=$(cd "$script_dir/../.." && pwd -P)
if [[ ! -f $checkout_root/pyproject.toml && ! -d $checkout_root/.runtime ]]; then
  checkout_root=$package_root
fi
source_config="$checkout_root/.runtime/config/gateway.toml"

output_dir=$PWD
max_log_bytes=$((32 * 1024 * 1024))
journal_lines=4000
include_journal=true
include_system=true

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }

usage() {
  cat <<'EOF'
Usage: export-logs.sh [options]

Collect the application logs, service state and USB/TTY diagnostics of this
machine into one timestamped archive. Read-only: nothing is stopped or changed.

Options:
  --output-dir DIR     Directory for the archive (default: current directory).
  --max-log-bytes N    Copy at most the last N bytes of each log file
                       (default: 33554432). Larger files are stored as
                       <name>.tail so the archive stays bounded.
  --journal-lines N    Journal lines per unit (default: 4000).
  --no-journal         Skip the systemd journal.
  --no-system          Skip USB, TTY and kernel diagnostics.
  -h, --help           Show this message.

Run with sudo on a deb/rpm install: /var/log/neurobridge is mode 0750 and the
journal needs elevated rights. Without sudo the archive is still produced, but
those sections are recorded as skipped in manifest.txt.

Examples:
  sudo /opt/neurobridge/kylin/export-logs.sh --output-dir /tmp
  sudo ./packaging/kylin/export-logs.sh --output-dir ~
EOF
}

while (($# > 0)); do
  case $1 in
    --output-dir)
      [[ $# -ge 2 ]] || fail "--output-dir needs a value"
      output_dir=$2
      shift 2
      ;;
    --max-log-bytes)
      [[ $# -ge 2 ]] || fail "--max-log-bytes needs a value"
      [[ $2 =~ ^[0-9]+$ ]] || fail "--max-log-bytes must be a whole number"
      max_log_bytes=$2
      shift 2
      ;;
    --journal-lines)
      [[ $# -ge 2 ]] || fail "--journal-lines needs a value"
      [[ $2 =~ ^[0-9]+$ ]] || fail "--journal-lines must be a whole number"
      journal_lines=$2
      shift 2
      ;;
    --no-journal) include_journal=false; shift ;;
    --no-system) include_system=false; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "Unknown option: $1 (use --help)" ;;
  esac
done

[[ -d $output_dir ]] || fail "Output directory does not exist: $output_dir"
[[ -w $output_dir ]] || fail "Output directory is not writable: $output_dir"
output_dir=$(cd "$output_dir" && pwd -P)

root_dir=$package_root
layout=package
if [[ ! -f $package_config ]]; then
  if [[ -d $checkout_root/.runtime ]]; then
    root_dir=$checkout_root
    layout=source
  else
    layout=unknown
  fi
fi

privileged=false
[[ ${EUID} -eq 0 ]] && privileged=true
if [[ $privileged == false && $layout == package ]]; then
  warn "Package install detected without root; /var/log/neurobridge, the service"
  warn "state and the journal will be skipped. Re-run with sudo for a full export."
fi

work_dir=$(mktemp -d "${TMPDIR:-/tmp}/neurobridge-logs.XXXXXX")
cleanup() { rm -rf "$work_dir"; }
trap cleanup EXIT

manifest=$work_dir/manifest.txt
inventory=$work_dir/inventory.txt
skipped=()
record_skip() {
  skipped+=("$1")
}

application_version() {
  local registry=$1
  [[ -f $registry ]] || { printf 'unknown\n'; return 0; }
  awk '
    /^\[application\]/ { inside = 1; next }
    inside && /^\[/ { exit }
    inside && /^version[[:space:]]*=/ { gsub(/[^0-9A-Za-z._-]/, "", $3); print $3; exit }
  ' "$registry"
}

# Read [logging] directory without loading or copying the rest of the file.
config_log_directory() {
  local config=$1 line section= key value
  [[ -f $config ]] || return 0
  while IFS= read -r line; do
    case $line in
      \[*\]*)
        section=${line#[}
        section=${section%]}
        section=${section// /}
        continue
        ;;
    esac
    [[ $section == logging ]] || continue
    key=${line%%=*}
    [[ ${key// /} == directory ]] || continue
    value=${line#*=}
    value=${value%%#*}
    value=$(printf '%s' "$value" | tr -d ' \t"')
    [[ -n $value ]] || return 0
    printf '%s\n' "$value"
    return 0
  done <"$config"
}

log_directories=()
add_log_directory() {
  local candidate=$1 resolved existing
  [[ -n $candidate ]] || return 0
  # A relative path in the configuration is relative to the application root,
  # not to the operator's working directory.
  case $candidate in
    /*) ;;
    *) candidate="$root_dir/$candidate" ;;
  esac
  [[ -d $candidate ]] || return 0
  resolved=$(cd "$candidate" && pwd -P)
  for existing in ${log_directories[@]+"${log_directories[@]}"}; do
    [[ $existing == "$resolved" ]] && return 0
  done
  log_directories+=("$resolved")
}

add_log_directory "$root_dir/.runtime/logs"
add_log_directory /var/log/neurobridge
if [[ $layout == package ]]; then
  add_log_directory "$(config_log_directory "$package_config")"
else
  add_log_directory "$(config_log_directory "$source_config")"
fi

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
{
  printf 'NeuroBridge log export\n'
  printf 'generatedAtUtc: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'layout: %s\n' "$layout"
  printf 'applicationRoot: %s\n' "$root_dir"
  printf 'applicationVersion: %s\n' "$(application_version "$root_dir/neurobridge/version_registry.toml")"
  printf 'privileged: %s\n' "$privileged"
  printf 'host: %s\n' "$(uname -srm)"
  printf 'osRelease: %s\n' "$( { . /etc/os-release 2>/dev/null && printf '%s %s' "${NAME:-unknown}" "${VERSION:-}"; } || printf 'unknown')"
  printf 'logDirectories: %s\n' "${log_directories[*]:-(none found)}"
  printf '\nExcluded by design:\n'
  printf '  - gateway configuration contents (only its SHA-256 is recorded)\n'
  printf '  - recordings and raw device data\n'
  printf '  - credentials, tokens and private keys\n'
} >"$manifest"

: >"$inventory"
for config in "$package_config" "$source_config"; do
  if [[ -f $config ]]; then
    printf 'configSha256 %s: %s\n' "$config" "$(sha256sum "$config" | awk '{print $1}')" >>"$inventory"
  fi
done

# ---- application logs -------------------------------------------------------
mkdir -p "$work_dir/application-logs"
copied_logs=0
for log_directory in ${log_directories[@]+"${log_directories[@]}"}; do
  label=$(printf '%s' "$log_directory" | tr '/' '_')
  destination="$work_dir/application-logs/${label#_}"
  mkdir -p "$destination"
  found=false
  while IFS= read -r -d '' source; do
    found=true
    base=$(basename "$source")
    size=$(wc -c <"$source" 2>/dev/null || printf '0')
    if ((size > max_log_bytes)); then
      tail -c "$max_log_bytes" "$source" >"$destination/$base.tail" 2>/dev/null || {
        warn "Could not read log tail: $source"
        continue
      }
      printf 'log %s bytes=%s stored=tail(%s)\n' "$source" "$size" "$max_log_bytes" >>"$inventory"
    else
      cp --preserve=timestamps "$source" "$destination/$base" 2>/dev/null || {
        warn "Could not copy log: $source"
        continue
      }
      printf 'log %s bytes=%s\n' "$source" "$size" >>"$inventory"
    fi
    copied_logs=$((copied_logs + 1))
  done < <(find "$log_directory" -maxdepth 1 -type f -name '*.log*' ! -type l -print0 2>/dev/null || true)
  if [[ $found == false ]]; then
    rmdir "$destination" 2>/dev/null || true
    warn "No *.log* files found under $log_directory"
  fi
done
if ((copied_logs == 0)); then
  warn "No application log files were collected."
fi

# ---- service state ---------------------------------------------------------
mkdir -p "$work_dir/service"
if [[ -f $unit_path ]]; then
  cp --preserve=timestamps "$unit_path" "$work_dir/service/$unit_name" 2>/dev/null \
    || warn "Could not copy the unit file: $unit_path"
else
  record_skip "unit file $unit_path is absent"
fi
if command -v systemctl >/dev/null 2>&1; then
  {
    printf 'is-active: %s\n' "$(systemctl is-active "$unit_name" 2>/dev/null || true)"
    printf 'is-enabled: %s\n' "$(systemctl is-enabled "$unit_name" 2>/dev/null || true)"
    printf '\n'
    systemctl status "$unit_name" --no-pager --full 2>&1 || true
  } >"$work_dir/service/status.txt"
else
  record_skip "systemctl is unavailable"
fi
if [[ $include_journal == true ]]; then
  if command -v journalctl >/dev/null 2>&1; then
    journalctl -u "$unit_name" --no-pager -n "$journal_lines" 2>"$work_dir/service/journal-error.txt" \
      >"$work_dir/service/journal.txt" || true
    if [[ -s $work_dir/service/journal-error.txt ]]; then
      warn "Journal collection reported: $(head -1 "$work_dir/service/journal-error.txt")"
    fi
    rm -f "$work_dir/service/journal-error.txt"
  else
    record_skip "journalctl is unavailable"
  fi
else
  record_skip "journal excluded by --no-journal"
fi

# ---- USB, TTY and kernel ---------------------------------------------------
if [[ $include_system == true ]]; then
  mkdir -p "$work_dir/system"
  {
    printf '=== USB devices ===\n'
    { lsusb 2>/dev/null || printf 'lsusb unavailable\n'; }
    printf '\n=== TTY candidates ===\n'
    { ls -l /dev/ttyACM* /dev/ttyUSB* 2>/dev/null || printf 'no /dev/ttyACM* or /dev/ttyUSB* devices\n'; }
    printf '\n=== Serial device ownership ===\n'
    for device in /dev/ttyACM* /dev/ttyUSB*; do
      [[ -c $device ]] || continue
      stat -Lc '%n mode=%A owner=%U group=%G' "$device" 2>/dev/null || true
    done
    printf '\n=== Recent kernel messages (USB/serial/tty) ===\n'
    { dmesg 2>/dev/null | tail -n 200 || printf 'dmesg unavailable\n'; }
  } >"$work_dir/system/usb-tty.txt"
else
  record_skip "system diagnostics excluded by --no-system"
fi

# ---- finalise the manifest -------------------------------------------------
{
  printf '\nSkipped sections:\n'
  if ((${#skipped[@]} > 0)); then
    for item in "${skipped[@]}"; do
      printf '  - %s\n' "$item"
    done
  else
    printf '  (none)\n'
  fi
  printf '\nFile inventory:\n'
  if [[ -s $inventory ]]; then
    sed 's/^/  /' "$inventory"
  else
    printf '  (no log files or configuration digests were recorded)\n'
  fi
} >>"$manifest"

# ---- archive ---------------------------------------------------------------
archive_name="neurobridge-logs-$timestamp.tar.gz"
archive="$output_dir/$archive_name"
tar -czf "$archive" -C "$work_dir" .

if [[ -n ${SUDO_UID:-} && -n ${SUDO_GID:-} ]]; then
  # The operator asked for this file; do not leave it owned by root.
  chown "${SUDO_UID}:${SUDO_GID}" "$archive" 2>/dev/null || true
fi

sha256sum "$archive" >"$archive.sha256"
if [[ -n ${SUDO_UID:-} ]]; then
  chown "${SUDO_UID}:${SUDO_GID}" "$archive.sha256" 2>/dev/null || true
fi

printf 'Log export: %s\n' "$archive"
printf 'Checksum:   %s\n' "$archive.sha256"
printf 'Size:       %s bytes\n' "$(wc -c <"$archive")"
if ((${#skipped[@]} > 0)); then
  printf 'Skipped:\n'
  for item in "${skipped[@]}"; do
    printf '  - %s\n' "$item"
  done
fi
printf 'The archive excludes configuration contents, recordings and credentials.\n'
