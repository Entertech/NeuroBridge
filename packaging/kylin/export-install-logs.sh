#!/usr/bin/env bash
# Works with an unpacked/half-configured package and without a Python runtime.
set -euo pipefail
if [[ ${1:-} == --help ]]; then
  echo 'Usage: sudo export-install-logs.sh --output-dir <directory>'
  exit 0
fi
[[ $# -eq 2 && $1 == --output-dir ]] || { echo 'Usage: sudo export-install-logs.sh --output-dir <directory>' >&2; exit 2; }
[[ ${EUID:-$(id -u)} -eq 0 ]] || { echo 'Run as root.' >&2; exit 1; }
umask 077
output_dir=$2
mkdir -p -- "$output_dir"
work=$(mktemp -d /var/tmp/neurobridge-support.XXXXXX)
trap 'rm -rf -- "$work"' EXIT
mkdir "$work/install-logs" "$work/build-logs"
{
  date -u +%FT%TZ
  uname -srmo
  cat /etc/os-release
  dpkg-query -W -f='${Package} ${Version} ${Status}\n' neurobridge-bootstrap 2>&1 || true
  systemctl is-active neurobridge.service 2>&1 || true
  systemctl is-enabled neurobridge.service 2>&1 || true
  cat /usr/lib/neurobridge-bootstrap/build-info.txt 2>/dev/null || true
  for tool in g++ make; do "$tool" --version 2>&1 | head -n 1 || true; done
  # Only a digest is exported; configuration may contain credentials.
  if [[ -f /etc/neurobridge/gateway.toml && ! -L /etc/neurobridge/gateway.toml ]]; then
    sha256sum /etc/neurobridge/gateway.toml
  fi
} > "$work/system.txt"
for source in /var/log/neurobridge-bootstrap/install-*.log /var/log/neurobridge-bootstrap/build-*.log; do
  [[ -f $source && ! -L $source ]] || continue
  destination=install-logs
  [[ $(basename "$source") != build-* ]] || destination=build-logs
  tail -c 2097152 "$source" > "$work/$destination/$(basename "$source")"
done
journalctl -u neurobridge.service --no-pager -n 300 > "$work/service-journal.txt" 2>&1 || true
if [[ -f /var/log/dpkg.log && ! -L /var/log/dpkg.log ]]; then
  tail -n 300 /var/log/dpkg.log > "$work/package-manager.txt"
fi
printf '%s\n' 'Install diagnostics only. No gateway configuration, recordings or device data are included. Log files are limited to their last 2 MiB each.' > "$work/README.txt"
archive=$(mktemp "$output_dir/neurobridge-install-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX.tar.gz")
tar -czf "$archive" -C "$work" .
if [[ ${SUDO_UID:-0} =~ ^[0-9]+$ && ${SUDO_GID:-0} =~ ^[0-9]+$ && ${SUDO_UID:-0} -ne 0 ]]; then
  chown "$SUDO_UID:$SUDO_GID" "$archive"
fi
printf 'Log export: %s\n' "$archive"
