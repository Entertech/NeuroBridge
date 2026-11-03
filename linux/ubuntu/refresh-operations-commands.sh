#!/usr/bin/env bash
# Refresh an already-authorized operations command without touching sshd/accounts.
set -euo pipefail
[[ ${EUID} -eq 0 && $# -eq 0 ]] || { echo 'Run as root without arguments.' >&2; exit 1; }
# No operations authorization is created by an application update.
[[ -f /etc/sudoers.d/neurobridge-operator && -f /usr/local/sbin/neurobridge-ops-command ]] || exit 0
root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
for name in neurobridge-ops neurobridge-ops-command; do
  source_path="$root_dir/linux/ubuntu/operations/$name"
  [[ -f "$source_path" && ! -L "$source_path" ]] || { echo "Missing operations source: $name" >&2; exit 1; }
  bash -n "$source_path"
done
cli_tmp=$(mktemp /usr/local/bin/.neurobridge-ops.XXXXXX)
gate_tmp=$(mktemp /usr/local/sbin/.neurobridge-ops-command.XXXXXX)
trap 'rm -f -- "$cli_tmp" "$gate_tmp"' EXIT
install -o root -g root -m 0755 "$root_dir/linux/ubuntu/operations/neurobridge-ops" "$cli_tmp"
install -o root -g root -m 0755 "$root_dir/linux/ubuntu/operations/neurobridge-ops-command" "$gate_tmp"
mv -f -- "$gate_tmp" /usr/local/sbin/neurobridge-ops-command
mv -f -- "$cli_tmp" /usr/local/bin/neurobridge-ops
