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

With no arguments, this starts an interactive setup wizard. It securely asks
for an operator account password on the gateway console; the password is never
placed in a command-line argument, file, or log.
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
echo "将启用受限运维账户的密码登录；密码不会写入文件或日志。"
read -r -p "运维账户 [neuroops]: " operator_user
operator_user=${operator_user:-neuroops}
read -r -s -p "运维账户密码（6 位数字）: " operator_password
echo
read -r -s -p "再次输入运维账户密码: " operator_password_confirm
echo
[[ "$operator_password" == "$operator_password_confirm" ]] || {
  echo "ERROR: 两次输入的密码不一致。" >&2
  exit 1
}
[[ "$operator_password" =~ ^[0-9]{6}$ ]] || {
  echo "ERROR: 运维账户密码必须是正好 6 位数字。" >&2
  exit 1
}
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
echo "即将仅允许 $operator_user 从 $allow_from 通过 $listen_address:$port 使用账号密码登录。"
read -r -p "输入 YES 确认: " confirm
[[ $confirm == YES ]] || { echo "已取消。"; exit 0; }

printf '%s\n' "$operator_password" | "$configure" \
  --operator-user "$operator_user" \
  --operator-password-stdin \
  --listen-address "$listen_address" \
  --allow-from "$allow_from" \
  --port "$port"
configure_status=$?
unset operator_password operator_password_confirm
exit "$configure_status"
