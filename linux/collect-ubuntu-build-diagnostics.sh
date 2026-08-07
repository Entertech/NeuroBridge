#!/usr/bin/env bash
# Collect build diagnostics only. It never includes gateway.toml, recordings,
# raw BLE data, or private keys. Attach the resulting archive to the issue.
set -euo pipefail

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

[[ $# -eq 0 ]] || fail "Usage: $0"
root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
stamp=$(date -u +%Y%m%dT%H%M%SZ)
result="/tmp/neurobridge-ubuntu-build-diagnostics-$stamp.tar.gz"
archive_owner=${SUDO_USER:-$(id -un)}
archive_group=$(id -gn "$archive_owner")
work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT

capture() {
  local name=$1
  shift
  "$@" >"$work_dir/$name" 2>&1 || true
}

capture os-release cat /etc/os-release
capture uname uname -a
capture cmake-version cmake --version
capture compiler-version c++ --version
capture python-version python3 --version
capture dpkg-build-dependencies dpkg-query -W -f='${Package}\t${Version}\n' build-essential cmake libeigen3-dev libboost-dev
capture git-revision git -C "$root_dir" rev-parse HEAD
capture git-status git -C "$root_dir" status --short --branch

for source in \
  /var/lib/neurobridge/algorithm-build/output/build.log \
  /var/lib/neurobridge/algorithm-build/output/build/CMakeCache.txt \
  /var/lib/neurobridge/algorithm-build/output/build/numcpp/CMakeFiles/CMakeOutput.log \
  /var/lib/neurobridge/algorithm-build/output/build/numcpp/CMakeFiles/CMakeError.log \
  /var/lib/neurobridge/algorithm-build/output/build/bridge/CMakeFiles/CMakeOutput.log \
  /var/lib/neurobridge/algorithm-build/output/build/bridge/CMakeFiles/CMakeError.log; do
  if [[ -r "$source" ]]; then
    cp "$source" "$work_dir/$(basename "$source").$(echo "$source" | sha256sum | cut -d' ' -f1)"
  fi
done

if command -v journalctl >/dev/null 2>&1; then
  capture neurobridge-journal journalctl -u neurobridge.service -n 200 --no-pager
fi

tar -C "$work_dir" -czf "$result" .
chown "$archive_owner:$archive_group" "$result"
chmod 0600 "$result"
echo "Diagnostics archive created: $result"
