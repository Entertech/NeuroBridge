#!/usr/bin/env bash
# Read-only collection. Never copy deployment credentials, recordings, or raw BLE data.
set -euo pipefail

since='72 hours ago'
output_dir=/tmp
log_dir=/var/log/neurobridge
while [[ $# -gt 0 ]]; do
  case "$1" in
    --since|--output-dir|--log-dir)
      [[ $# -ge 2 ]] || { echo "Missing value for $1" >&2; exit 2; }
      case "$1" in
        --since) since=$2 ;;
        --output-dir) output_dir=$2 ;;
        --log-dir) log_dir=$2 ;;
      esac
      shift 2
      ;;
    -h|--help)
      echo "Usage: sudo $0 [--since '72 hours ago'] [--output-dir /tmp] [--log-dir /var/log/neurobridge]"
      exit 0
      ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

umask 077
work_dir=$(mktemp -d)
archive=
completed=false
cleanup() {
  rm -rf -- "$work_dir"
  if [[ "$completed" != true && -n "$archive" ]]; then rm -f -- "$archive"; fi
}
trap cleanup EXIT
mkdir -p -- "$output_dir" "$work_dir/runtime-logs"
archive=$(mktemp "$output_dir/neurobridge-runtime-XXXXXXXX.tar.gz")

capture() {
  local name=$1
  shift
  local result=0
  timeout 15 "$@" >"$work_dir/$name" 2>&1 || result=$?
  printf '%s exitCode=%s\n' "$name" "$result" >>"$work_dir/collection-status.txt"
}

printf 'since=%s\n' "$since" >"$work_dir/collection-status.txt"
capture time.txt date -Is
capture system.txt uname -a
capture neurobridge-journal.log journalctl -u neurobridge.service --since "$since" --no-pager -o short-iso-precise
capture bluetooth.log journalctl -u bluetooth.service --since "$since" --no-pager -o short-iso-precise
capture network.log journalctl -u NetworkManager.service -u systemd-networkd.service -u neurobridge-dhcp.service --since "$since" --no-pager -o short-iso-precise
capture kernel.log journalctl -k --since "$since" --no-pager -o short-iso-precise
capture operations.log journalctl -t neurobridge-ops-audit --since "$since" --no-pager -o short-iso-precise
capture journal-boots.txt journalctl --list-boots --no-pager
capture services.txt systemctl status neurobridge.service bluetooth.service NetworkManager.service systemd-networkd.service --no-pager -l
capture restart-state.txt systemctl show neurobridge.service -p NRestarts -p Result -p ExecMainCode -p ExecMainStatus -p ActiveEnterTimestamp
capture addresses.txt ip -br address
capture links.txt ip -s link
capture routes.txt ip route show table all
capture neighbours.txt ip neigh show
capture listeners.txt ss -lntp
capture bluetooth-controller.txt bluetoothctl show
capture radio-blocks.txt rfkill list

if [[ -d "$log_dir" ]]; then
  # Copy only regular log files, including rotations; never follow symlinks.
  while IFS= read -r -d '' logfile; do
    cp -P -- "$logfile" "$work_dir/runtime-logs/" 2>>"$work_dir/collection-status.txt" || true
  done < <(find "$log_dir" -maxdepth 1 -type f -name '*.log*' -print0)
else
  printf 'Persistent log directory unavailable\n' >>"$work_dir/collection-status.txt"
fi

tar -C "$work_dir" -czf "$archive" .
if [[ ${EUID} -eq 0 && -n ${SUDO_USER:-} ]]; then
  chown "$SUDO_USER:$(id -gn "$SUDO_USER")" "$archive"
fi
chmod 0600 "$archive"
completed=true
printf 'Diagnostics archive created: %s\n' "$archive"
printf 'Check collection-status.txt for missing permissions or unavailable commands.\n'
