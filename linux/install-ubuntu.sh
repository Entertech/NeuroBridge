#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root on Ubuntu x86_64." >&2
  exit 1
fi
if [[ $(uname -m) != "x86_64" ]]; then
  echo "NeuroBridge first-release deployment requires Ubuntu x86_64; detected $(uname -m)." >&2
  exit 1
fi
if [[ ! -r /etc/os-release ]] || ! . /etc/os-release || [[ ${ID:-} != "ubuntu" ]]; then
  echo "NeuroBridge first-release deployment requires Ubuntu LTS." >&2
  exit 1
fi

root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
install_dir=/opt/neurobridge
config_dir=/etc/neurobridge
data_dir=/var/lib/neurobridge/recordings

apt-get update
# Archive export renders the published capture-package document locally.  These
# are runtime dependencies of RecordingStore.export(), not CI-only tools.
browser_package=chromium
if ! apt-cache show "$browser_package" >/dev/null 2>&1; then
  browser_package=chromium-browser
fi
# dnsmasq is only activated by neurobridge-dhcp.service when [network].mode is
# dhcp. In static mode its NeuroBridge unit is skipped at boot.
apt-get install -y python3 python3-venv bluez dnsmasq rsync pandoc "$browser_package" fonts-noto-cjk
getent group neurobridge >/dev/null 2>&1 || groupadd --system neurobridge
id -u neurobridge >/dev/null 2>&1 || useradd --system --gid neurobridge --home /nonexistent --shell /usr/sbin/nologin neurobridge
getent group bluetooth >/dev/null && usermod -aG bluetooth neurobridge || true
install -d -o neurobridge -g neurobridge -m 0750 "$data_dir" /var/log/neurobridge
install -d -m 0755 "$install_dir"
install -d -o root -g neurobridge -m 0750 "$config_dir"
rsync -a --delete --exclude .git --exclude .venv "$root_dir/" "$install_dir/"
python3 -m venv "$install_dir/venv"
"$install_dir/venv/bin/pip" install --upgrade pip
"$install_dir/venv/bin/pip" install -r "$install_dir/requirements.lock"
"$install_dir/venv/bin/pip" install --no-deps "$install_dir"
if [[ ! -e "$config_dir/gateway.toml" ]]; then
  install -o root -g neurobridge -m 0640 "$install_dir/config/gateway.toml.example" "$config_dir/gateway.toml"
else
  chown root:neurobridge "$config_dir/gateway.toml"
  chmod 0640 "$config_dir/gateway.toml"
fi
install -m 0644 "$install_dir/linux/systemd/neurobridge.service" /etc/systemd/system/neurobridge.service
install -m 0644 "$install_dir/linux/systemd/neurobridge-dhcp.service" /etc/systemd/system/neurobridge-dhcp.service
install -m 0644 "$install_dir/linux/logrotate/neurobridge" /etc/logrotate.d/neurobridge
# Do not allow the distribution-wide dnsmasq unit to serve an unintended
# network. The dedicated unit uses a generated, interface-bound configuration.
systemctl disable --now dnsmasq.service 2>/dev/null || true
systemctl disable --now dnsmasq.socket 2>/dev/null || true
systemctl daemon-reload
systemctl enable neurobridge.service
systemctl enable neurobridge-dhcp.service
echo "NeuroBridge is enabled for every boot. Confirm $config_dir/gateway.toml, then run: systemctl start neurobridge"
