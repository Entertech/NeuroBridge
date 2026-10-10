#!/usr/bin/env bash
# Capture an entire install attempt, including failures before runtime creation.
set -euo pipefail
[[ ${EUID:-$(id -u)} -eq 0 ]] || { echo 'Run as root.' >&2; exit 1; }
umask 077
log_dir=/var/log/neurobridge-bootstrap
keep=${NEUROBRIDGE_INSTALL_LOG_KEEP:-10}
[[ $keep =~ ^[1-9][0-9]{0,2}$ && $keep -le 100 ]] || { echo 'NEUROBRIDGE_INSTALL_LOG_KEEP must be 1..100.' >&2; exit 1; }
install -d -m 0700 "$log_dir"
# A timestamp plus PID avoids overwriting previous attempts and concurrent runs.
log=$log_dir/install-$(date -u +%Y%m%dT%H%M%SZ)-$$.log
for old in $(find "$log_dir" -maxdepth 1 -type f -name 'install-*.log' | sort -r | tail -n +"$keep"); do
  rm -f -- "$old"
done
echo "Installation log: $log"
set +e
(
  set -e
  printf 'INSTALL_BEGIN utc=%s entry=%s\n' "$(date -u +%FT%TZ)" "$(basename "$1")"
  if [[ -f $(dirname "$1")/build-info.txt ]]; then
    cat "$(dirname "$1")/build-info.txt"
  fi
  export NEUROBRIDGE_INSTALL_LOGGED=1
  bash "$@"
) 2>&1 | tee "$log"
statuses=("${PIPESTATUS[@]}")
status=${statuses[0]}
[[ ${statuses[1]} -eq 0 ]] || status=1
printf 'INSTALL_END utc=%s exit_code=%s\n' "$(date -u +%FT%TZ)" "$status" | tee -a "$log"
if [[ $status -ne 0 ]]; then
  echo "Installation failed. Export logs: sudo /usr/lib/neurobridge-bootstrap/export-install-logs.sh --output-dir <directory>" >&2
fi
exit "$status"
