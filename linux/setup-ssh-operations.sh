#!/usr/bin/env bash
# Interactive convenience wrapper for the strict SSH operations configurator.
set -euo pipefail

root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
configure="$root_dir/linux/configure-ssh-operations.sh"

usage() {
  cat <<'EOF'
Usage:
  sudo ./linux/setup-ssh-operations.sh
  sudo ./linux/setup-ssh-operations.sh <configure-ssh-operations arguments>

With no arguments, this starts an interactive setup wizard. It never creates
or copies a private key: prepare the operator public key on the management
computer and copy only that .pub file to the gateway first.
EOF
}

[[ -x "$configure" ]] || { echo "ERROR: Missing executable configurator: $configure" >&2; exit 1; }
if [[ $# -gt 0 ]]; then
  if [[ $1 == -h || $1 == --help ]]; then
    usage
    exit 0
  fi
  exec "$configure" "$@"
fi

private_addresses=()
while IFS= read -r address; do
  private_addresses+=("$address")
done < <(ip -o -4 addr show | awk '{split($4, address, "/"); print address[1]}' | \
  python3 -c 'import ipaddress, sys; [print(value.strip()) for value in sys.stdin if ipaddress.ip_address(value.strip()).is_private]')

default_address=
if [[ ${#private_addresses[@]} -eq 1 ]]; then
  default_address=${private_addresses[0]}
fi
default_source=
if [[ -n ${SSH_CONNECTION:-} ]]; then
  default_source=${SSH_CONNECTION%% *}/32
fi

echo "NeuroBridge SSH 运维一键配置"
echo "只会写入公钥；请不要把私钥复制到网关。"
read -r -p "运维账户 [neuroops]: " operator_user
operator_user=${operator_user:-neuroops}
read -r -p "运维公钥文件路径 [/tmp/neurobridge-ops.pub]: " authorized_key_file
authorized_key_file=${authorized_key_file:-/tmp/neurobridge-ops.pub}
read -r -p "网关私有监听 IP${default_address:+ [$default_address]}: " listen_address
listen_address=${listen_address:-$default_address}
read -r -p "允许的运维主机 IP/CIDR${default_source:+ [$default_source]}: " allow_from
allow_from=${allow_from:-$default_source}
read -r -p "SSH 端口 [22]: " port
port=${port:-22}

[[ -n "$listen_address" && -n "$allow_from" ]] || {
  echo "ERROR: 必须提供网关监听 IP 和允许来源 IP/CIDR。" >&2
  exit 1
}

echo
echo "即将仅允许 $operator_user 从 $allow_from 通过 $listen_address:$port 使用公钥登录。"
read -r -p "输入 YES 确认: " confirm
[[ $confirm == YES ]] || { echo "已取消。"; exit 0; }

exec "$configure" \
  --operator-user "$operator_user" \
  --authorized-key-file "$authorized_key_file" \
  --listen-address "$listen_address" \
  --allow-from "$allow_from" \
  --port "$port"
