#!/usr/bin/env bash
# Create a self-contained N100/N150 + Kylin runtime diagnostic bundle.
#
# Deliberately excluded: gateway.toml contents, process environments, SSH
# credentials/keys, recordings, raw EEG/HR packets, and algorithm payloads.
set -u -o pipefail

root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
default_output_dir="$root_dir/.runtime/diagnostics"

usage() {
  cat <<'EOF'
Usage: sudo ./linux/collect-kylin-runtime-diagnostics.sh [options]

Options:
  --output-dir DIR       Absolute directory below project .runtime/diagnostics
                         (default: project .runtime/diagnostics)
  --journal-lines N      Maximum lines per service/kernel journal (default: 5000)
  -h, --help             Show this help

The command creates a .tar.gz archive and a matching .sha256 file. The archive
contains operational and system metadata but no gateway configuration contents,
recordings, raw EEG/HR data, passwords, tokens, or private keys.
EOF
}

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

output_dir=$default_output_dir
journal_lines=5000
while [[ $# -gt 0 ]]; do
  case $1 in
    --output-dir)
      [[ $# -ge 2 ]] || fail "--output-dir requires a value"
      output_dir=$2
      shift 2
      ;;
    --journal-lines)
      [[ $# -ge 2 ]] || fail "--journal-lines requires a value"
      journal_lines=$2
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "Unknown option: $1"
      ;;
  esac
done

[[ ${EUID:-$(id -u)} -eq 0 ]] || fail "Run this command with sudo so journal, driver, and service diagnostics are complete."
[[ $output_dir == /* ]] || fail "--output-dir must be an absolute path"
[[ $journal_lines =~ ^[0-9]+$ ]] || fail "--journal-lines must be an integer"
(( journal_lines >= 100 && journal_lines <= 50000 )) || fail "--journal-lines must be between 100 and 50000"

umask 077
command -v realpath >/dev/null 2>&1 || fail "Required command is unavailable: realpath"
output_dir=$(realpath -m -- "$output_dir")
case $output_dir in
  "$default_output_dir"|"$default_output_dir"/*) ;;
  *) fail "Output must stay under the ignored project directory: $default_output_dir" ;;
esac
archive_uid=${SUDO_UID:-0}
archive_gid=${SUDO_GID:-0}
[[ $archive_uid =~ ^[0-9]+$ ]] || archive_uid=0
[[ $archive_gid =~ ^[0-9]+$ ]] || archive_gid=0
install -d -o "$archive_uid" -g "$archive_gid" -m 0750 \
  "$root_dir/.runtime" "$default_output_dir" "$output_dir" \
  || fail "Could not create project diagnostic directory: $output_dir"
[[ -d $output_dir ]] || fail "Output directory does not exist: $output_dir"
[[ -w $output_dir ]] || fail "Output directory is not writable: $output_dir"

stamp=$(date -u +%Y%m%dT%H%M%SZ)
archive_name="neurobridge-kylin-runtime-diagnostics-${stamp}-$$.tar.gz"
archive_path="$output_dir/$archive_name"
checksum_path="$archive_path.sha256"
[[ ! -e $archive_path && ! -e $checksum_path ]] || fail "Refusing to overwrite an existing diagnostic bundle"

work_dir=$(mktemp -d "$output_dir/.neurobridge-kylin-diagnostics.XXXXXX") \
  || fail "Could not create a temporary diagnostic directory under: $output_dir"
warnings_file="$work_dir/collection-warnings.txt"
touch "$warnings_file"

cleanup() {
  case $work_dir in
    "$output_dir"/.neurobridge-kylin-diagnostics.*)
      rm -rf -- "$work_dir"
      ;;
    *)
      echo "WARNING: refusing to remove unexpected temporary path: $work_dir" >&2
      ;;
  esac
}
trap cleanup EXIT

warn() {
  printf '%s\n' "$*" >>"$warnings_file"
}

capture() {
  local name=$1
  shift
  {
    printf 'command:'
    printf ' %q' "$@"
    printf '\nstartedAtUtc: %s\n\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    "$@"
    local result=$?
    printf '\nexitCode: %s\n' "$result"
  } >"$work_dir/$name.txt" 2>&1 || true
}

capture_if_available() {
  local name=$1
  local command_name=$2
  shift 2
  if command -v "$command_name" >/dev/null 2>&1; then
    capture "$name" "$command_name" "$@"
  else
    warn "Command unavailable: $command_name (skipped $name)"
  fi
}

capture_shell() {
  local name=$1
  local script=$2
  capture "$name" bash -o pipefail -c "$script"
}

cat >"$work_dir/README.txt" <<'EOF'
NeuroBridge Kylin runtime diagnostic bundle

Purpose:
- Enable offline diagnosis of an N100/N150 gateway without Codex or internet.
- Correlate one incident across application logs, systemd, kernel, USB/TTY,
  network listeners, resources, installed dependencies, and deployed binaries.

Intentionally not collected:
- /etc/neurobridge/gateway.toml contents or process environment variables
- passwords, tokens, cookies, API keys, SSH configuration, or private keys
- recordings, raw EEG/HR bytes, Base64 payloads, or algorithm result payloads
- core dump contents

The bundle does contain operational metadata such as host name, private IP
addresses, USB VID/PID/serial properties, service logs, process IDs, and file
paths. Inspect it before sharing and use only the approved project channel.
EOF

{
  printf 'generatedAtUtc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'collector=%s\n' "${BASH_SOURCE[0]}"
  printf 'collectorRoot=%s\n' "$root_dir"
  printf 'journalLines=%s\n' "$journal_lines"
  printf 'archiveFormat=tar.gz\n'
  printf 'configContentsCollected=false\n'
  printf 'recordingsCollected=false\n'
  printf 'processEnvironmentCollected=false\n'
} >"$work_dir/manifest.txt"

# Host, operating system, clock, hardware, storage, and resource state.
capture os-release cat /etc/os-release
[[ -r /etc/issue ]] && capture issue cat /etc/issue || warn "/etc/issue is unavailable"
[[ -r /etc/kylin-release ]] && capture kylin-release cat /etc/kylin-release || warn "/etc/kylin-release is unavailable"
if [[ -r /etc/.kyinfo ]]; then
  capture_shell kylin-build-info 'grep -Ei "^[[:space:]]*(name|milestone|arch|version|release|build|buildid|time|dist_id)[[:space:]]*=" /etc/.kyinfo || true'
else
  warn "/etc/.kyinfo is unavailable"
fi
[[ -r /etc/lsb-release ]] && capture lsb-release cat /etc/lsb-release || warn "/etc/lsb-release is unavailable"
capture_if_available kylin-version-command nkvers
capture_if_available uname uname -a
capture_if_available architecture uname -m
capture_if_available hostname hostname
capture_if_available hostnamectl hostnamectl
capture_if_available date date --iso-8601=seconds
capture_if_available uptime uptime
capture_if_available timedatectl timedatectl status
[[ -r /proc/sys/kernel/random/boot_id ]] && capture boot-id cat /proc/sys/kernel/random/boot_id
[[ -r /proc/cmdline ]] && capture kernel-command-line cat /proc/cmdline
capture_if_available lscpu lscpu
capture_if_available memory free -h
capture_if_available disk-space df -hT
capture_if_available inode-space df -ih
capture_if_available block-devices lsblk -o NAME,KNAME,TYPE,SIZE,FSTYPE,MOUNTPOINTS,MODEL,ROTA,TRAN
capture_if_available mount mount
if command -v dmidecode >/dev/null 2>&1; then
  capture_shell hardware-identification '
    for field in system-manufacturer system-product-name system-version baseboard-manufacturer baseboard-product-name bios-vendor bios-version bios-release-date; do
      printf "%s=" "$field"
      dmidecode -s "$field" 2>/dev/null || true
    done
  '
else
  warn "Command unavailable: dmidecode (hardware model details omitted)"
fi

# Toolchain, runtime, package inventory, and deployed artifact identity.
capture_if_available python-version python3 --version
capture_if_available python-path command -v python3
capture_if_available systemd-version systemctl --version
capture_if_available glibc-version ldd --version
capture_if_available cmake-version cmake --version
capture_if_available compiler-version c++ --version
capture_if_available collector-sha256 sha256sum "${BASH_SOURCE[0]}"
if command -v dpkg-query >/dev/null 2>&1; then
  capture dpkg-packages dpkg-query -W '-f=${Package}\t${Version}\t${Architecture}\n'
elif command -v rpm >/dev/null 2>&1; then
  capture rpm-packages rpm -qa '--qf=%{NAME}\t%{VERSION}-%{RELEASE}\t%{ARCH}\n'
else
  warn "No dpkg-query or rpm command found; package inventory omitted"
fi
python_runtime=
for candidate in "$root_dir/.venv/bin/python" "$root_dir/venv/bin/python" /opt/neurobridge/venv/bin/python; do
  if [[ -x $candidate ]]; then
    python_runtime=$candidate
    break
  fi
done
if [[ -n $python_runtime ]]; then
  capture python-runtime-version "$python_runtime" --version
  capture python-packages "$python_runtime" -m pip freeze --all
else
  warn "No project .venv/venv or installed /opt NeuroBridge Python runtime is available"
fi
if command -v git >/dev/null 2>&1 && [[ -d $root_dir/.git ]]; then
  capture source-revision git -C "$root_dir" rev-parse HEAD
  capture source-status git -C "$root_dir" status --short --branch
else
  warn "Collector checkout has no readable Git metadata"
fi

write_deployed_file_metadata() {
  local path
  for path in \
    "$root_dir/.runtime/config/gateway.toml" \
    "$root_dir/.runtime/logs" \
    "$root_dir/.runtime/recordings" \
    "$root_dir/.runtime/diagnostics" \
    /etc/neurobridge/gateway.toml \
    /opt/neurobridge/venv/bin/neurobridge \
    /usr/local/lib/neurobridge/neurobridge_affective_bridge \
    /etc/systemd/system/neurobridge.service \
    /var/log/neurobridge \
    /var/lib/neurobridge; do
    if [[ -e "$path" ]]; then
      stat -c "path=%n mode=%a owner=%U group=%G size=%s modified=%y" "$path" 2>&1 || true
      if [[ -f "$path" ]]; then
        sha256sum "$path" 2>&1 || true
      fi
    else
      printf "missing=%s\n" "$path"
    fi
  done
}
capture deployed-file-metadata write_deployed_file_metadata
if [[ -x /usr/local/lib/neurobridge/neurobridge_affective_bridge ]]; then
  capture algorithm-bridge-dependencies ldd /usr/local/lib/neurobridge/neurobridge_affective_bridge
fi

# Service lifecycle, restart reasons, recent application logs, and kernel events.
if command -v systemctl >/dev/null 2>&1; then
  capture service-status systemctl status neurobridge.service --no-pager -l
  capture service-properties systemctl show neurobridge.service \
    -p Id -p LoadState -p ActiveState -p SubState -p UnitFileState \
    -p FragmentPath -p DropInPaths -p MainPID -p ExecMainCode -p ExecMainStatus \
    -p NRestarts -p RestartUSec -p ActiveEnterTimestamp -p InactiveEnterTimestamp \
    -p MemoryCurrent -p MemoryPeak -p CPUUsageNSec -p TasksCurrent \
    -p LimitNOFILE -p LimitNPROC -p LimitCORE
  capture service-unit systemctl cat neurobridge.service
  capture failed-units systemctl --failed --no-pager
  for support_unit in neurobridge-dhcp.service NetworkManager.service systemd-networkd.service bluetooth.service; do
    safe_unit=${support_unit//./-}
    capture "service-$safe_unit-status" systemctl status "$support_unit" --no-pager -l
  done
fi
if command -v journalctl >/dev/null 2>&1; then
  capture journal-disk-usage journalctl --disk-usage
  capture service-journal-current-boot journalctl -u neurobridge.service -b -n "$journal_lines" --no-pager -o short-iso-precise
  capture service-journal-previous-boot journalctl -u neurobridge.service -b -1 -n "$journal_lines" --no-pager -o short-iso-precise
  capture kernel-journal-current-boot journalctl -k -b -n "$journal_lines" --no-pager -o short-iso-precise
  capture kernel-journal-previous-boot journalctl -k -b -1 -n "$journal_lines" --no-pager -o short-iso-precise
  capture system-warnings-current-boot journalctl -b -p warning..alert -n "$journal_lines" --no-pager -o short-iso-precise
  for support_unit in neurobridge-dhcp.service NetworkManager.service systemd-networkd.service bluetooth.service; do
    safe_unit=${support_unit//./-}
    capture "journal-$safe_unit-current-boot" journalctl -u "$support_unit" -b -n "$journal_lines" --no-pager -o short-iso-precise
  done
fi
capture_if_available coredump-list coredumpctl list neurobridge --no-pager
capture_if_available processes ps -eo pid,ppid,user,group,lstart,etime,%cpu,%mem,stat,comm
if command -v dmesg >/dev/null 2>&1; then
  capture_shell dmesg "dmesg --ctime 2>&1 | tail -n $journal_lines"
fi
capture_if_available selinux-enforcement getenforce
capture_if_available selinux-status sestatus
capture_if_available apparmor-status aa-status

mkdir -p "$work_dir/application-logs"
log_directories=("$root_dir/.runtime/logs" /var/log/neurobridge)
copied_logs=0
for log_directory in "${log_directories[@]}"; do
  if [[ ! -d $log_directory ]]; then
    warn "Application log directory does not exist: $log_directory"
    continue
  fi
  log_source_label=$(basename "$(dirname "$log_directory")")-$(basename "$log_directory")
  mkdir -p "$work_dir/application-logs/$log_source_label"
  copied_logs=0
  while IFS= read -r -d '' source; do
    base=$(basename "$source")
    size=$(stat -c %s "$source" 2>/dev/null || printf '0')
    if (( size > 52428800 )); then
      if [[ $base == *.gz ]]; then
        warn "Skipped compressed application log larger than 50 MiB: $source"
        continue
      fi
      tail -c 52428800 "$source" >"$work_dir/application-logs/$log_source_label/$base.tail-50MiB" 2>/dev/null || warn "Could not copy log tail: $source"
    else
      cp --preserve=timestamps "$source" "$work_dir/application-logs/$log_source_label/$base" 2>/dev/null || warn "Could not copy application log: $source"
    fi
    copied_logs=$((copied_logs + 1))
  done < <(find "$log_directory" -maxdepth 1 -type f -name '*.log*' -print0)
  (( copied_logs > 0 )) || warn "No application *.log* files found under $log_directory"
done

# USB, serial, Bluetooth, driver, and device-permission state. These commands
# inspect metadata only and do not open/configure a TTY or send device bytes.
capture_if_available usb-devices lsusb
capture_if_available usb-tree lsusb -t
capture_if_available usb-device-details usb-devices
capture_if_available loaded-modules lsmod
capture_if_available rfkill rfkill list
capture_if_available bluetooth-controller bluetoothctl show
capture_if_available bluetooth-devices bluetoothctl devices
for module in cdc_acm ch341 cp210x ftdi_sio usbserial; do
  if command -v modinfo >/dev/null 2>&1; then
    capture "module-$module" modinfo "$module"
  fi
done

tty_nodes=()
while IFS= read -r node; do
  tty_nodes+=("$node")
done < <(find /dev -maxdepth 1 -type c \( -name 'ttyACM*' -o -name 'ttyUSB*' \) -print 2>/dev/null | sort)
if (( ${#tty_nodes[@]} > 0 )); then
  capture serial-nodes ls -l "${tty_nodes[@]}"
  for node in "${tty_nodes[@]}"; do
    safe_name=${node#/dev/}
    if command -v udevadm >/dev/null 2>&1; then
      capture "udev-$safe_name" udevadm info --query=all --name "$node"
    fi
    if command -v lsof >/dev/null 2>&1; then
      capture "openers-$safe_name" lsof "$node"
    fi
  done
else
  warn "No /dev/ttyACM* or /dev/ttyUSB* nodes were present at collection time"
fi
if [[ -d /dev/serial/by-id ]]; then
  capture serial-by-id ls -la /dev/serial/by-id
else
  warn "/dev/serial/by-id does not exist"
fi

# Network state is needed to diagnose WebSocket/download reachability. It may
# include private addresses and should be reviewed before the bundle is shared.
capture_if_available network-links ip -details -brief link
capture_if_available network-addresses ip -brief address
capture_if_available network-routes ip route show table all
capture_if_available network-rules ip rule show
capture_if_available listening-sockets ss -lntup
capture_if_available neighbor-table ip neigh show
capture_if_available firewall-nft nft list ruleset
capture_if_available firewall-iptables iptables-save
if command -v firewall-cmd >/dev/null 2>&1; then
  capture firewall-state firewall-cmd --state
  capture firewall-zones firewall-cmd --list-all-zones
fi

# Apply a final best-effort scrub to text files. The collector avoids sensitive
# sources entirely; this handles accidental credential-like values in logs.
if command -v python3 >/dev/null 2>&1; then
  python3 - "$work_dir" <<'PY'
from __future__ import annotations

from pathlib import Path
import re
import sys

root = Path(sys.argv[1])
credential = re.compile(
    r"(?i)\b(password|passwd|token|secret|api[_-]?key|authorization|cookie)\b"
    r"(\s*[:=]\s*|\s+)([^\s,;]+)"
)
private_key = re.compile(
    r"-----BEGIN [^-\n]*PRIVATE KEY-----.*?-----END [^-\n]*PRIVATE KEY-----",
    re.DOTALL,
)
redacted_files = 0
redacted_values = 0
for path in root.rglob("*"):
    if not path.is_file() or path.suffix == ".gz":
        continue
    data = path.read_bytes()
    if b"\x00" in data:
        continue
    text = data.decode("utf-8", errors="replace")
    text, private_count = private_key.subn("[REDACTED_PRIVATE_KEY]", text)
    text, credential_count = credential.subn(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]",
        text,
    )
    count = private_count + credential_count
    if count:
        path.write_text(text, encoding="utf-8")
        redacted_files += 1
        redacted_values += count
(root / "redaction-report.txt").write_text(
    f"redactedFiles={redacted_files}\nredactedValues={redacted_values}\n",
    encoding="utf-8",
)
PY
else
  warn "python3 is unavailable; final credential-pattern scrub was skipped"
fi

if ! tar -C "$work_dir" -czf "$archive_path" .; then
  rm -f -- "$archive_path"
  fail "Could not create diagnostic archive"
fi
if ! (
  cd "$output_dir" || exit 1
  sha256sum "$archive_name" >"$archive_name.sha256"
); then
  rm -f -- "$archive_path" "$checksum_path"
  fail "Could not create archive checksum"
fi

if ! chmod 0600 "$archive_path" "$checksum_path"; then
  rm -f -- "$archive_path" "$checksum_path"
  fail "Could not restrict diagnostic bundle permissions to 0600"
fi
if ! chown "$archive_uid:$archive_gid" "$archive_path" "$checksum_path"; then
  rm -f -- "$archive_path" "$checksum_path"
  fail "Could not assign the diagnostic bundle to the invoking user"
fi

echo "Diagnostics archive created: $archive_path"
echo "SHA-256 file created:       $checksum_path"
echo "Review README.txt and the collected metadata before sharing through an approved channel."
