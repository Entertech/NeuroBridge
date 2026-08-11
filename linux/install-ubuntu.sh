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
if [[ ! -r /etc/os-release ]] || ! . /etc/os-release || [[ ${ID:-} != "ubuntu" || ${VERSION_ID:-} != "24.04" ]]; then
  echo "NeuroBridge deployment requires Ubuntu 24.04 LTS." >&2
  exit 1
fi
[[ $# -eq 0 ]] || { echo "Usage: $0" >&2; exit 1; }

root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
install_dir=/opt/neurobridge
config_dir=/etc/neurobridge
data_dir=/var/lib/neurobridge/recordings
algorithm_build_dir=/var/lib/neurobridge/algorithm-build
algorithm_bridge_dir=/usr/local/lib/neurobridge
algorithm_bridge_path=$algorithm_bridge_dir/neurobridge_affective_bridge

for command in python3 rsync cmake c++ netplan; do
  command -v "$command" >/dev/null 2>&1 || { echo "Missing system prerequisite: $command. Install it in the Ubuntu 24.04 base image before deployment." >&2; exit 1; }
done
[[ -d /usr/include/eigen3 ]] || { echo "Missing system prerequisite: libeigen3-dev. Install it in the Ubuntu 24.04 base image before deployment." >&2; exit 1; }
getent group neurobridge >/dev/null 2>&1 || groupadd --system neurobridge
id -u neurobridge >/dev/null 2>&1 || useradd --system --gid neurobridge --home /nonexistent --shell /usr/sbin/nologin neurobridge
getent group bluetooth >/dev/null && usermod -aG bluetooth neurobridge || true
install -d -o neurobridge -g neurobridge -m 0750 /var/lib/neurobridge "$data_dir" "$algorithm_build_dir" /var/log/neurobridge
install -d -m 0755 "$install_dir"
install -d -o root -g neurobridge -m 0750 "$config_dir"
rsync -a --delete --exclude .git --exclude .venv --exclude venv "$root_dir/" "$install_dir/"
[[ -x "$install_dir/venv/bin/python" && -x "$install_dir/venv/bin/pip" ]] || { echo "Missing existing NeuroBridge Python environment at $install_dir/venv. Provision it in the Ubuntu 24.04 base image before deployment." >&2; exit 1; }
"$install_dir/venv/bin/python" -c 'import bleak, websockets' || { echo "Existing NeuroBridge Python environment is missing bleak or websockets. Provision them in the Ubuntu 24.04 base image before deployment." >&2; exit 1; }
PIP_NO_INDEX=1 "$install_dir/venv/bin/pip" install --no-index --no-deps --no-build-isolation "$install_dir"
if [[ ! -e "$config_dir/gateway.toml" ]]; then
  install -o root -g neurobridge -m 0640 "$install_dir/config/gateway.toml.example" "$config_dir/gateway.toml"
else
  chown root:neurobridge "$config_dir/gateway.toml"
  chmod 0640 "$config_dir/gateway.toml"
fi
# Configure the dedicated B-side link from gateway.toml.  The configurator
# refuses an ambiguous multi-NIC host or an interface owned by another Netplan
# file, so it cannot silently change the management network.
"$install_dir/venv/bin/neurobridge-network-config" --config "$config_dir/gateway.toml" --apply
# Build the same locked C++ bridge used by the macOS POC, but do it as the
# unprivileged service account.  The service never needs a per-host command in
# gateway.toml: config.py resolves the installed fixed path automatically.
runuser -u neurobridge -- "$install_dir/linux/build-algorithm-bridge.sh" "$install_dir/third_party" "$algorithm_build_dir/output"
install -d -o root -g root -m 0755 "$algorithm_bridge_dir"
install -o root -g root -m 0755 "$algorithm_build_dir/output/neurobridge_affective_bridge" "$algorithm_bridge_path"
install -m 0644 "$install_dir/linux/systemd/neurobridge.service" /etc/systemd/system/neurobridge.service
install -m 0644 "$install_dir/linux/systemd/neurobridge-dhcp.service" /etc/systemd/system/neurobridge-dhcp.service
install -m 0644 "$install_dir/linux/systemd/neurobridge-config-ui.service" /etc/systemd/system/neurobridge-config-ui.service
install -m 0644 "$install_dir/linux/logrotate/neurobridge" /etc/logrotate.d/neurobridge
# Do not allow the distribution-wide dnsmasq unit to serve an unintended
# network. The dedicated unit uses a generated, interface-bound configuration.
systemctl disable --now dnsmasq.service 2>/dev/null || true
systemctl disable --now dnsmasq.socket 2>/dev/null || true
systemctl daemon-reload
systemctl enable neurobridge.service
systemctl enable neurobridge-dhcp.service
systemctl enable --now neurobridge-config-ui.service
echo "NeuroBridge is enabled for every boot and the dedicated Ethernet link is configured from $config_dir/gateway.toml. On the gateway's local desktop, open http://127.0.0.1:8090/ to review or update it. Confirm the deployment values, then run: systemctl start neurobridge"
