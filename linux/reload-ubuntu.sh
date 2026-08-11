#!/usr/bin/env bash
# Deploy the current checkout to the installed Ubuntu gateway, then restart it.
set -euo pipefail

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

if [[ ${EUID} -ne 0 ]]; then
  command -v sudo >/dev/null 2>&1 || fail "Administrator permission is required, but sudo is unavailable. Log in as an administrator and run: sudo bash linux/reload-ubuntu.sh"
  exec sudo --preserve-env=PATH bash "$0" "$@"
fi

root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
install_dir=/opt/neurobridge
config_dir=/etc/neurobridge
unit_dir=/etc/systemd/system

[[ $(uname -m) == "x86_64" ]] || fail "This deployment supports Ubuntu x86_64 only; detected $(uname -m). Do not use this reload script on this host."
[[ -r /etc/os-release ]] || fail "Cannot identify the operating system. Use an Ubuntu x86_64 gateway installed through linux/install-ubuntu.sh."
. /etc/os-release
[[ ${ID:-} == "ubuntu" ]] || fail "This deployment supports Ubuntu only; detected ${ID:-unknown}. Do not use this reload script on this host."
command -v systemctl >/dev/null 2>&1 || fail "systemd is unavailable. This script requires the systemd-based Ubuntu deployment."
command -v rsync >/dev/null 2>&1 || fail "rsync is unavailable. Repair the Ubuntu deployment with: sudo ./linux/install-ubuntu.sh"
[[ -d /run/systemd/system ]] || fail "systemd is not running. Start the host with systemd, then retry."

[[ -f "$root_dir/pyproject.toml" && -f "$root_dir/requirements.lock" && -f "$root_dir/linux/install-ubuntu.sh" ]] \
  || fail "The current directory is not a complete NeuroBridge source checkout. Run the script from the repository root."
[[ -d "$install_dir" && -x "$install_dir/venv/bin/python" && -x "$install_dir/venv/bin/pip" ]] \
  || fail "NeuroBridge is not installed in $install_dir. First run: sudo ./linux/install-ubuntu.sh"
[[ -f "$unit_dir/neurobridge.service" ]] \
  || fail "The NeuroBridge systemd service is not installed. First run: sudo ./linux/install-ubuntu.sh"
[[ -r "$install_dir/requirements.lock" && -r "$install_dir/linux/install-ubuntu.sh" ]] \
  || fail "The installed deployment is incomplete. Repair it with: sudo ./linux/install-ubuntu.sh"

if [[ ! -r "$config_dir/gateway.toml" ]]; then
  fail "Missing gateway configuration: $config_dir/gateway.toml. Create and confirm it with sudoedit, then run this script again."
fi

if ! cmp -s "$root_dir/requirements.lock" "$install_dir/requirements.lock"; then
  fail "Python dependencies changed. Do not hot-reload this version; run: sudo ./linux/install-ubuntu.sh"
fi

if ! cmp -s "$root_dir/linux/install-ubuntu.sh" "$install_dir/linux/install-ubuntu.sh"; then
  fail "The Ubuntu installation procedure changed and may require system packages or service setup. Run: sudo ./linux/install-ubuntu.sh"
fi

echo "WARNING: Restarting the gateway disconnects current B-end WebSocket clients; they must reconnect and subscribe again."
echo "Syncing current checkout to $install_dir ..."
rsync -a --delete --exclude .git --exclude .venv --exclude venv "$root_dir/" "$install_dir/"

echo "Updating Python package ..."
"$install_dir/venv/bin/pip" install --no-deps "$install_dir"

install -m 0644 "$install_dir/linux/systemd/neurobridge.service" "$unit_dir/neurobridge.service"
install -m 0644 "$install_dir/linux/systemd/neurobridge-dhcp.service" "$unit_dir/neurobridge-dhcp.service"
install -m 0644 "$install_dir/linux/systemd/neurobridge-config-ui.service" "$unit_dir/neurobridge-config-ui.service"

systemctl daemon-reload
systemctl enable --now neurobridge-config-ui.service
systemctl restart neurobridge-config-ui.service
if ! systemctl restart neurobridge.service; then
  fail "Gateway restart failed. Inspect the reason with: sudo journalctl -u neurobridge.service -n 100 --no-pager"
fi
if ! systemctl is-active --quiet neurobridge.service; then
  systemctl --no-pager --full status neurobridge.service || true
  fail "Gateway is not active after restart. Inspect the reason with: sudo journalctl -u neurobridge.service -n 100 --no-pager"
fi

echo "NeuroBridge reload completed successfully."
