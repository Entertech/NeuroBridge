#!/usr/bin/env bash
# Interactive convenience wrapper for the strict SSH operations configurator.
set -euo pipefail

root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
configure="$root_dir/linux/configure-ssh-operations.sh"
quick_config="$root_dir/config/ssh-operations.txt"
quick_config_template="$root_dir/config/ssh-operations.example.txt"

usage() {
  cat <<'EOF'
Usage:
  sudo ./linux/setup-ssh-operations.sh
  sudo ./linux/setup-ssh-operations.sh --quick [operator-ip]
  sudo ./linux/setup-ssh-operations.sh <configure-ssh-operations arguments>

With no arguments, this starts an interactive setup wizard. It securely asks
for an operator account password on the gateway console; the password is never
placed in a command-line argument, file, or log.

Quick mode keeps the safe password prompts and final confirmation, but fixes the
allowed source to one operator IP with a /32 mask. It reads values from
config/ssh-operations.txt and prompts only for missing or empty values.
The optional operator-ip keeps compatibility and overrides allow_from in the
file. A plaintext password is supported, so this file must stay out of Git and
be readable only by its owner.
EOF
}

trim_whitespace() {
  local value=$1
  value=${value#"${value%%[![:space:]]*}"}
  value=${value%"${value##*[![:space:]]}"}
  printf '%s' "$value"
}

[[ -x "$configure" ]] || { echo "ERROR: Missing executable configurator: $configure" >&2; exit 1; }
quick_mode=false
quick_operator_host=
if [[ $# -gt 0 ]]; then
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --quick)
      [[ $# -le 2 ]] || { echo "ERROR: Usage: $0 --quick [operator-ip]" >&2; exit 1; }
      quick_mode=true
      quick_operator_host=${2:-}
      ;;
    *)
      exec "$configure" "$@"
      ;;
  esac
fi

private_addresses=()
while IFS= read -r address; do
  private_addresses+=("$address")
done < <(ip -o -4 addr show scope global | awk '{split($4, address, "/"); print address[1]}' | \
  python3 -c 'import ipaddress, sys; [print(value.strip()) for value in sys.stdin if (address := ipaddress.ip_address(value.strip())).is_private and not address.is_loopback and not address.is_link_local and not address.is_unspecified]')

default_address=
if [[ -n ${SSH_CONNECTION:-} ]]; then
  read -r _ _ ssh_local_address _ <<<"$SSH_CONNECTION"
  for address in "${private_addresses[@]}"; do
    if [[ "$address" == "$ssh_local_address" ]]; then
      default_address=$address
      break
    fi
  done
fi
if [[ -z "$default_address" && ${#private_addresses[@]} -gt 0 ]]; then
  default_address=${private_addresses[0]}
fi
default_source=
default_operator_host=
if [[ -n ${SSH_CONNECTION:-} ]]; then
  default_operator_host=${SSH_CONNECTION%% *}
  default_source=$default_operator_host/32
fi

echo "NeuroBridge SSH 运维一键配置"
echo "将启用受限运维账户的密码登录；密码不会回显或写入日志。"

if [[ "$quick_mode" == true ]]; then
  operator_user=
  operator_password=
  listen_address=
  operator_host=
  port=
  [[ ! -L "$quick_config" ]] || {
    echo "ERROR: 快速配置文件不能是符号链接：${quick_config}" >&2
    exit 1
  }
  [[ ! -e "$quick_config" || -f "$quick_config" ]] || {
    echo "ERROR: 快速配置路径不是普通文件：${quick_config}" >&2
    exit 1
  }
  if [[ ! -e "$quick_config" && -f "$quick_config_template" ]]; then
    install -m 0600 "$quick_config_template" "$quick_config"
    echo "已创建快速配置文件：${quick_config}"
  fi
  if [[ -f "$quick_config" ]]; then
    config_line_number=0
    while IFS= read -r config_line || [[ -n "$config_line" ]]; do
      config_line_number=$((config_line_number + 1))
      config_line=${config_line%$'\r'}
      config_line=$(trim_whitespace "$config_line")
      [[ -z "$config_line" || "$config_line" == \#* ]] && continue
      [[ "$config_line" == *=* ]] || {
        echo "ERROR: $quick_config:$config_line_number 必须使用 key=value 格式。" >&2
        exit 1
      }
      config_key=$(trim_whitespace "${config_line%%=*}")
      config_value=$(trim_whitespace "${config_line#*=}")
      case "$config_key" in
        operator_user) operator_user=$config_value ;;
        password) operator_password=$config_value ;;
        listen_address) listen_address=$config_value ;;
        allow_from) operator_host=$config_value ;;
        port) port=$config_value ;;
        *)
          echo "ERROR: $quick_config:$config_line_number 包含未知参数：$config_key" >&2
          exit 1
          ;;
      esac
    done <"$quick_config"
    if [[ -n "$operator_password" ]]; then
      python3 - "$quick_config" <<'PY'
from __future__ import annotations

import os
import stat
import sys

path = sys.argv[1]
mode = stat.S_IMODE(os.stat(path).st_mode)
if mode & 0o077:
    raise SystemExit(
        "SSH quick config contains a plaintext password and must not grant "
        f"group/other permissions; run chmod 600: {path}"
    )
PY
    fi
    echo "快速模式已读取配置文件：${quick_config}"
  else
    echo "WARNING: 快速配置文件不存在，将逐项询问：${quick_config}" >&2
  fi

  [[ -z "$quick_operator_host" ]] || operator_host=$quick_operator_host

  if [[ -z "$operator_user" ]]; then
    read -r -p "运维账户 [neuroops]: " operator_user
    operator_user=${operator_user:-neuroops}
  fi
  if [[ -z "$listen_address" ]]; then
    read -r -p "网关私有监听 IP${default_address:+ [$default_address]}: " listen_address
    listen_address=${listen_address:-$default_address}
  fi
  if [[ -z "$operator_host" ]]; then
    read -r -p "B 端运维主机 IP${default_operator_host:+ [$default_operator_host]}: " operator_host
    operator_host=${operator_host:-$default_operator_host}
  fi
  if [[ -z "$port" ]]; then
    read -r -p "SSH 端口 [22]: " port
    port=${port:-22}
  fi
  [[ -n "$listen_address" ]] || {
    echo "ERROR: 快速模式必须提供网关私有监听 IP；当前设备未检测到可用默认值。" >&2
    exit 1
  }
  [[ -n "$operator_host" ]] || {
    echo "ERROR: 快速模式必须提供 B 端运维主机 IP。" >&2
    exit 1
  }
  allow_from=$(python3 - "$operator_host" <<'PY'
from __future__ import annotations

import ipaddress
import sys

raw = sys.argv[1]
try:
    interface = ipaddress.ip_interface(raw if "/" in raw else f"{raw}/32")
except ValueError as exc:
    raise SystemExit("Quick mode requires one valid operator IPv4 address.") from exc
address = interface.ip
if interface.network.prefixlen != 32:
    raise SystemExit("Quick mode accepts one operator IPv4 address, not a subnet.")
if (
    address.version != 4
    or not address.is_private
    or address.is_loopback
    or address.is_link_local
    or address.is_unspecified
):
    raise SystemExit("Quick mode requires one RFC1918 operator IPv4 address.")
print(f"{address}/32")
PY
  )
  echo "快速模式：账号 ${operator_user}，监听 ${listen_address}，来源 ${allow_from}，端口 ${port}。"
  echo "修改常用参数请编辑 ${quick_config}；需要允许来源网段时请使用无参数完整模式。"
else
  read -r -p "运维账户 [neuroops]: " operator_user
  operator_user=${operator_user:-neuroops}
  operator_password=
fi

if [[ -z "$operator_password" ]]; then
  read -r -s -p "运维账户密码（至少 6 位数字）: " operator_password
  echo
  read -r -s -p "再次输入运维账户密码: " operator_password_confirm
  echo
  [[ "$operator_password" == "$operator_password_confirm" ]] || {
    echo "ERROR: 两次输入的密码不一致。" >&2
    exit 1
  }
fi
[[ "$operator_password" =~ ^[0-9]{6,}$ ]] || {
  echo "ERROR: 运维账户密码必须是至少 6 位数字。" >&2
  exit 1
}

if [[ "$quick_mode" == false ]]; then
  read -r -p "网关私有监听 IP${default_address:+ [$default_address]}: " listen_address
  listen_address=${listen_address:-$default_address}
  read -r -p "允许的运维主机 IP/CIDR${default_source:+ [$default_source]}: " allow_from
  allow_from=${allow_from:-$default_source}
  read -r -p "SSH 端口 [22]: " port
  port=${port:-22}
fi

[[ -n "$listen_address" && -n "$allow_from" ]] || {
  echo "ERROR: 必须提供网关监听 IP 和允许来源 IP/CIDR。" >&2
  exit 1
}

echo
echo "即将仅允许 $operator_user 从 $allow_from 通过 $listen_address:$port 使用账号密码登录。"
read -r -p "输入 YES 确认（不区分大小写）: " confirm
case "$confirm" in
  [Yy][Ee][Ss]) ;;
  *) echo "已取消。"; exit 0 ;;
esac

printf '%s\n' "$operator_password" | "$configure" \
  --operator-user "$operator_user" \
  --operator-password-stdin \
  --listen-address "$listen_address" \
  --allow-from "$allow_from" \
  --port "$port"
configure_status=$?
unset operator_password operator_password_confirm
exit "$configure_status"
