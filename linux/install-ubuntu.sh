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
  echo "NeuroBridge offline deployment requires Ubuntu 24.04 LTS." >&2
  exit 1
fi

if [[ $# -ne 2 || $1 != "--offline-bundle" ]]; then
  echo "Usage: $0 --offline-bundle /absolute/ubuntu24.04-offline-bundle" >&2
  exit 1
fi
offline_bundle=$2
case "$offline_bundle" in /*) ;; *) echo "Offline bundle path must be absolute." >&2; exit 1;; esac
if [[ ! -d "$offline_bundle" || ! -r "$offline_bundle/manifest.toml" ]]; then
  echo "Offline bundle is missing or unreadable: $offline_bundle" >&2
  exit 1
fi

root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
install_dir=/opt/neurobridge
config_dir=/etc/neurobridge
data_dir=/var/lib/neurobridge/recordings
algorithm_build_dir=/var/lib/neurobridge/algorithm-build
algorithm_bridge_dir=/usr/local/lib/neurobridge
algorithm_bridge_path=$algorithm_bridge_dir/neurobridge_affective_bridge
offline_debs_dir=$offline_bundle/debs
offline_wheels_dir=$offline_bundle/wheels

if [[ ! -d "$offline_debs_dir" ]]; then
  echo "Offline bundle contains no .deb package directory: $offline_debs_dir" >&2
  exit 1
fi
mapfile -t offline_debs < <(find "$offline_debs_dir" -maxdepth 1 -type f -name '*.deb' -print | sort)
if (( ${#offline_debs[@]} == 0 )); then
  echo "Offline bundle contains no .deb packages: $offline_debs_dir" >&2
  exit 1
fi
if [[ ! -d "$offline_wheels_dir" ]]; then
  echo "Offline bundle contains no Python wheel directory: $offline_wheels_dir" >&2
  exit 1
fi
# This command is deliberately offline: it can install only the .deb files
# delivered with the bundle and fails rather than contacting an APT repository.
apt-get install --no-download -y "${offline_debs[@]}"
getent group neurobridge >/dev/null 2>&1 || groupadd --system neurobridge
id -u neurobridge >/dev/null 2>&1 || useradd --system --gid neurobridge --home /nonexistent --shell /usr/sbin/nologin neurobridge
getent group bluetooth >/dev/null && usermod -aG bluetooth neurobridge || true
install -d -o neurobridge -g neurobridge -m 0750 /var/lib/neurobridge "$data_dir" "$algorithm_build_dir" /var/log/neurobridge
install -d -m 0755 "$install_dir"
install -d -o root -g neurobridge -m 0750 "$config_dir"
rsync -a --delete --exclude .git --exclude .venv "$root_dir/" "$install_dir/"
python3 -m venv "$install_dir/venv"
"$install_dir/venv/bin/pip" install --no-index --find-links "$offline_wheels_dir" -r "$install_dir/requirements.lock"
"$install_dir/venv/bin/pip" install --no-deps "$install_dir"
# Build the same locked C++ bridge used by the macOS POC, but do it as the
# unprivileged service account.  The service never needs a per-host command in
# gateway.toml: config.py resolves the installed fixed path automatically.
sdk_dir=$(mktemp -d "$algorithm_build_dir/sdk.XXXXXX")
chown neurobridge:neurobridge "$sdk_dir"
chmod 0750 "$sdk_dir"
runuser -u neurobridge -- python3 "$install_dir/tools/verify-offline-algorithm-bundle.py" "$offline_bundle" "$sdk_dir"
runuser -u neurobridge -- "$install_dir/linux/build-algorithm-bridge.sh" "$sdk_dir" "$algorithm_build_dir/output"
install -d -o root -g root -m 0755 "$algorithm_bridge_dir"
install -o root -g root -m 0755 "$algorithm_build_dir/output/neurobridge_affective_bridge" "$algorithm_bridge_path"
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
