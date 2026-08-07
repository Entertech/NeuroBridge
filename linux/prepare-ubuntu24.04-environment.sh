#!/usr/bin/env bash
# One-time online preparation for an Ubuntu 24.04 x86_64 NeuroBridge host.
# After this succeeds, deployment through update-ubuntu.sh is network-free.
set -euo pipefail

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

if [[ ${EUID} -ne 0 ]]; then
  command -v sudo >/dev/null 2>&1 || fail "Administrator permission is required; run: sudo bash linux/prepare-ubuntu24.04-environment.sh"
  exec sudo --preserve-env=PATH bash "$0" "$@"
fi

[[ $# -eq 0 ]] || fail "Usage: $0"
[[ $(uname -m) == "x86_64" ]] || fail "NeuroBridge first-release deployment requires Ubuntu x86_64; detected $(uname -m)."
[[ -r /etc/os-release ]] || fail "Cannot identify the operating system."
. /etc/os-release
[[ ${ID:-} == "ubuntu" && ${VERSION_ID:-} == "24.04" ]] || fail "Environment preparation requires Ubuntu 24.04 LTS."

root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
[[ -f "$root_dir/requirements.lock" && -f "$root_dir/pyproject.toml" ]] || fail "Run this from a complete NeuroBridge source checkout."

export DEBIAN_FRONTEND=noninteractive
echo "Installing Ubuntu 24.04 prerequisites (this one-time step requires internet access)..."
apt-get update
apt-get install -y --no-install-recommends \
  bluez \
  build-essential \
  cmake \
  dnsmasq \
  git \
  libeigen3-dev \
  python3 \
  python3-dev \
  python3-pip \
  python3-venv \
  rsync

install_dir=/opt/neurobridge
install -d -m 0755 "$install_dir"
if [[ ! -x "$install_dir/venv/bin/python" ]]; then
  python3 -m venv "$install_dir/venv"
fi

echo "Installing locked Python runtime dependencies (this one-time step requires internet access)..."
"$install_dir/venv/bin/python" -m pip install --upgrade pip setuptools
"$install_dir/venv/bin/python" -m pip install -r "$root_dir/requirements.lock"
"$install_dir/venv/bin/python" -m pip install --no-deps --no-build-isolation "$root_dir"
"$install_dir/venv/bin/python" -c 'import bleak, websockets; print("NeuroBridge Python runtime ready")'

echo "Environment preparation completed. You may now disconnect from the internet."
echo "Then deploy only local source with: $root_dir/linux/update-ubuntu.sh"
