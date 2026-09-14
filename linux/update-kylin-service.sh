#!/usr/bin/env bash
# Pull the checked-out Kylin gateway source and restart the project service.
set -euo pipefail

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
unit_name=neurobridge.service

[[ ${EUID:-$(id -u)} -ne 0 ]] || fail "Run as the normal desktop user, without sudo."
[[ -f $root_dir/pyproject.toml && -d $root_dir/.git ]] || fail "Not a complete NeuroBridge checkout: $root_dir"
command -v git >/dev/null 2>&1 || fail "git is required."
command -v systemctl >/dev/null 2>&1 || fail "systemctl is required."

cd "$root_dir"
if [[ -n $(git status --porcelain) ]]; then
  fail "Working tree is not clean; commit or stash local changes before updating."
fi

branch=$(git branch --show-current)
[[ -n $branch ]] || fail "Detached HEAD; check out the deployment branch first."
before=$(git rev-parse HEAD)
printf 'project=%s\nbranch=%s\nbefore=%s\n' "$root_dir" "$branch" "$before"

git pull --ff-only
after=$(git rev-parse HEAD)
printf 'after=%s\n' "$after"

# Re-render the managed unit so systemd keeps this checkout and its current
# start script, then restart the already-installed service.
"$root_dir/linux/setup-kylin-autostart.sh" enable
sudo systemctl daemon-reload
sudo systemctl restart "$unit_name"

sleep 1
sudo systemctl --no-pager --full status "$unit_name"
printf '\nRecent gateway startup lines:\n'
sudo journalctl -u "$unit_name" -n 60 --no-pager -o short-iso-precise \
  | grep -E 'Process starting|Runtime configuration|Serial runtime configuration|Gateway started|activeAckProbe|deviceValidationMode|Serial capture enable|Connection state changed' \
  || true

printf '\nIf the output still contains ack_01 or activeAckProbe=true, this checkout does not contain the new serial handshake code.\n'
