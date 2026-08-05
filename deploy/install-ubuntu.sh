#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root on Ubuntu x86_64." >&2
  exit 1
fi

root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
install_dir=/opt/neurobridge
config_dir=/etc/neurobridge
data_dir=/var/lib/neurobridge/recordings

apt-get update
apt-get install -y python3 python3-venv bluez rsync
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
install -m 0644 "$install_dir/deploy/neurobridge.service" /etc/systemd/system/neurobridge.service
install -m 0644 "$install_dir/deploy/logrotate/neurobridge" /etc/logrotate.d/neurobridge
systemctl daemon-reload
systemctl enable neurobridge.service
echo "Edit $config_dir/gateway.toml, then run: systemctl restart neurobridge"
