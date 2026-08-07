#!/usr/bin/env bash
# Configure a key-only, operations-only SSH entry point for a deployed gateway.
# It deliberately does not participate in the northbound B-end data protocol.
set -euo pipefail

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage:
  sudo ./linux/configure-ssh-operations.sh \
    --operator-user <local-user> \
    --authorized-key-file <public-key-file> \
    --listen-address <gateway-private-ip> \
    --allow-from <operator-ip-or-cidr> [--port <1-65535>]

The public-key file may contain one or more OpenSSH public keys. Private keys
are never copied to the gateway. This command restricts SSH to the named local
operator account, disables password and root login, and binds sshd only to the
specified gateway address. The account receives only NeuroBridge status, log,
approved staged-code update, start, stop, and restart privileges through sudo.
The update command only runs a root-owned release staged at
/srv/neurobridge-release; it never fetches code from a network or accepts a
source path from the SSH user.
EOF
}

operator_user=
authorized_key_file=
listen_address=
allow_from=
port=22

while [[ $# -gt 0 ]]; do
  case "$1" in
    --operator-user)
      [[ $# -ge 2 ]] || fail "--operator-user requires a value"
      operator_user=$2
      shift 2
      ;;
    --authorized-key-file)
      [[ $# -ge 2 ]] || fail "--authorized-key-file requires a value"
      authorized_key_file=$2
      shift 2
      ;;
    --listen-address)
      [[ $# -ge 2 ]] || fail "--listen-address requires a value"
      listen_address=$2
      shift 2
      ;;
    --allow-from)
      [[ $# -ge 2 ]] || fail "--allow-from requires a value"
      allow_from=$2
      shift 2
      ;;
    --port)
      [[ $# -ge 2 ]] || fail "--port requires a value"
      port=$2
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "Unknown argument: $1"
      ;;
  esac
done

[[ ${EUID} -eq 0 ]] || fail "Run this command with sudo on the Ubuntu gateway."
[[ -n "$operator_user" && -n "$authorized_key_file" && -n "$listen_address" && -n "$allow_from" ]] || {
  usage >&2
  exit 1
}
[[ "$operator_user" =~ ^[a-z_][a-z0-9_-]{0,31}\$?$ ]] || fail "--operator-user must be a valid local Linux user name."
[[ "$port" =~ ^[0-9]+$ ]] && ((port >= 1 && port <= 65535)) || fail "--port must be between 1 and 65535."
[[ -r /etc/os-release ]] || fail "Cannot identify the operating system."
. /etc/os-release
[[ ${ID:-} == "ubuntu" && ${VERSION_ID:-} == "24.04" ]] || fail "SSH operations setup requires Ubuntu 24.04 LTS."
command -v sshd >/dev/null 2>&1 || fail "openssh-server is missing. Run linux/prepare-ubuntu24.04-environment.sh while the gateway can access the approved package source."
command -v ssh-keygen >/dev/null 2>&1 || fail "ssh-keygen is missing; openssh-client must be installed."
command -v systemctl >/dev/null 2>&1 || fail "systemd is required."
[[ -r "$authorized_key_file" ]] || fail "Cannot read the public-key file: $authorized_key_file"
ssh-keygen -lf "$authorized_key_file" >/dev/null 2>&1 || fail "The public-key file is not valid OpenSSH public-key input."

python3 - "$listen_address" "$allow_from" <<'PY'
from __future__ import annotations

import ipaddress
import sys

listen = ipaddress.ip_address(sys.argv[1])
allowed = ipaddress.ip_network(sys.argv[2], strict=False)
if listen.version != 4 or not listen.is_private:
    raise SystemExit("--listen-address must be an RFC1918 IPv4 address.")
if allowed.version != 4 or not allowed.is_private:
    raise SystemExit("--allow-from must be an RFC1918 IPv4 address or CIDR.")
PY

ip -o -4 addr show | awk '{split($4, address, "/"); print address[1]}' | grep -Fx -- "$listen_address" >/dev/null \
  || fail "--listen-address is not configured on this gateway: $listen_address"

# Restarting sshd would terminate an existing administrator who has not been
# admitted by the new AllowUsers policy. Require console setup in that case.
if [[ -n ${SSH_CONNECTION:-} && ${SUDO_USER:-root} != "$operator_user" ]]; then
  fail "This SSH session belongs to ${SUDO_USER:-root}, not $operator_user. Run from the local console or reconnect as the operator user before applying the access policy."
fi

if ! id -u "$operator_user" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$operator_user"
fi
usermod --lock "$operator_user"

operator_home=$(getent passwd "$operator_user" | cut -d: -f6)
[[ -n "$operator_home" && "$operator_home" == /* && "$operator_home" != "/" ]] || fail "Cannot determine a safe home directory for $operator_user."
install -d -o "$operator_user" -g "$operator_user" -m 0700 "$operator_home/.ssh"
install -o "$operator_user" -g "$operator_user" -m 0600 "$authorized_key_file" "$operator_home/.ssh/authorized_keys"

config_dir=/etc/ssh/sshd_config.d
# Ubuntu includes snippets before the main sshd_config. A low lexical prefix
# makes these restrictive values win over any later cloud-init/default value.
config_path=$config_dir/00-neurobridge-operations.conf
sudoers_path=/etc/sudoers.d/neurobridge-operator
status_script=/usr/local/sbin/neurobridge-ops-status
logs_script=/usr/local/sbin/neurobridge-ops-logs
ops_cli=/usr/local/bin/neurobridge-ops
update_source_dir=/srv/neurobridge-release
update_script=/usr/local/sbin/neurobridge-ops-update
install -d -o root -g root -m 0755 "$config_dir"
install -d -o root -g root -m 0755 /run/sshd
# A release administrator stages an approved complete checkout here. The SSH
# operator cannot write this directory and can only trigger its reload.
install -d -o root -g root -m 0750 "$update_source_dir"

config_tmp=$(mktemp)
sudoers_tmp=$(mktemp)
status_tmp=$(mktemp)
logs_tmp=$(mktemp)
ops_cli_tmp=$(mktemp)
update_tmp=$(mktemp)
previous_config=$(mktemp)
had_previous_config=false
cleanup() {
  rm -f "$config_tmp" "$sudoers_tmp" "$status_tmp" "$logs_tmp" "$ops_cli_tmp" "$update_tmp" "$previous_config"
}
trap cleanup EXIT

if [[ -e "$config_path" ]]; then
  cp -p "$config_path" "$previous_config"
  had_previous_config=true
fi

cat >"$config_tmp" <<EOF
# Managed by linux/configure-ssh-operations.sh. Do not edit in place.
# Remote maintenance only; this is not a B-end data interface.
AddressFamily inet
ListenAddress $listen_address
Port $port
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
PubkeyAuthentication yes
AuthenticationMethods publickey
X11Forwarding no
AllowAgentForwarding no
AllowTcpForwarding no
GatewayPorts no
PermitTunnel no
AllowUsers $operator_user

# Connections outside the approved operations source range cannot obtain a
# shell or execute a command even if they present an otherwise valid key.
Match User $operator_user Address *,!$allow_from
    ForceCommand /usr/bin/false
    PermitTTY no
    AllowTcpForwarding no
Match all
EOF

cat >"$sudoers_tmp" <<EOF
# Managed by linux/configure-ssh-operations.sh.
Cmnd_Alias NEUROBRIDGE_OPERATIONS = /usr/local/sbin/neurobridge-ops-status, /usr/local/sbin/neurobridge-ops-logs, /usr/local/sbin/neurobridge-ops-logs *, /usr/local/sbin/neurobridge-ops-update, /usr/bin/systemctl start neurobridge.service, /usr/bin/systemctl stop neurobridge.service, /usr/bin/systemctl restart neurobridge.service
$operator_user ALL=(root) NOPASSWD: NEUROBRIDGE_OPERATIONS
EOF

cat >"$status_tmp" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

echo '== NeuroBridge service =='
/usr/bin/systemctl --no-pager --full status neurobridge.service || true
echo
echo '== Recent NeuroBridge journal entries =='
/usr/bin/journalctl -u neurobridge.service -n 200 --no-pager || true
EOF

cat >"$logs_tmp" <<'EOF'
#!/usr/bin/env bash
# Root-owned argument gate for the read-only operations log command.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export LC_ALL=C

case "${1:-}" in
  --follow|-f)
    [[ $# -eq 1 ]] || { echo "Usage: neurobridge-ops logs [--follow|-f|--lines N]" >&2; exit 2; }
    exec /usr/bin/journalctl -u neurobridge.service -f --no-pager
    ;;
  --lines)
    [[ $# -eq 2 && $2 =~ ^[0-9]+$ && $2 -ge 1 && $2 -le 1000 ]] || {
      echo "--lines requires an integer between 1 and 1000." >&2
      exit 2
    }
    exec /usr/bin/journalctl -u neurobridge.service -n "$2" --no-pager
    ;;
  '')
    exec /usr/bin/journalctl -u neurobridge.service -n 200 --no-pager
    ;;
  *)
    echo "Usage: neurobridge-ops logs [--follow|-f|--lines N]" >&2
    exit 2
    ;;
esac
EOF

cat >"$ops_cli_tmp" <<'EOF'
#!/usr/bin/env bash
# User-facing gateway operations command. Privileged operations are constrained
# by /etc/sudoers.d/neurobridge-operator and root-owned helper scripts.
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: neurobridge-ops <command>

Commands:
  status                 Show current service state and the latest 200 logs.
  logs [--lines N]       Show recent logs (default 200, maximum 1000).
  logs --follow           Follow new gateway logs in real time; Ctrl-C stops it.
  update                 Apply the root-owned staged release and reload the gateway.
  start | stop | restart  Control the NeuroBridge gateway service.
  help                   Show this help.
USAGE
}

case "${1:-help}" in
  status)
    [[ $# -eq 1 ]] || { usage >&2; exit 2; }
    exec sudo -- /usr/local/sbin/neurobridge-ops-status
    ;;
  logs)
    shift
    exec sudo -- /usr/local/sbin/neurobridge-ops-logs "$@"
    ;;
  update)
    [[ $# -eq 1 ]] || { usage >&2; exit 2; }
    exec sudo -- /usr/local/sbin/neurobridge-ops-update
    ;;
  start|stop|restart)
    [[ $# -eq 1 ]] || { usage >&2; exit 2; }
    exec sudo -- /usr/bin/systemctl "$1" neurobridge.service
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
EOF

cat >"$update_tmp" <<'EOF'
#!/usr/bin/env bash
# Apply a pre-staged, administrator-owned release without network access.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

[[ ${EUID} -eq 0 ]] || fail "This helper must run as root through sudo."
source_dir=/srv/neurobridge-release
[[ -d "$source_dir" ]] || fail "Missing staged release directory: $source_dir"
for required in pyproject.toml requirements.lock linux/reload-ubuntu.sh linux/install-ubuntu.sh; do
  [[ -f "$source_dir/$required" ]] || fail "Staged release is incomplete: missing $required"
done
[[ -x "$source_dir/linux/reload-ubuntu.sh" ]] || fail "Staged reload script is not executable."

# The SSH operator must not be able to turn this controlled reload privilege
# into arbitrary root execution. Reject symlinks, non-root ownership, and any
# group- or world-writable path in the staged release.
unsafe_link=$(find "$source_dir" -xdev -type l -print -quit)
[[ -z "$unsafe_link" ]] || fail "Staged release contains a symlink: $unsafe_link"
unsafe_path=$(find "$source_dir" -xdev \( -type f -o -type d \) \( ! -user root -o -perm -0022 \) -print -quit)
[[ -z "$unsafe_path" ]] || fail "Staged release must be root-owned and not group/world writable: $unsafe_path"

echo "Applying staged NeuroBridge release from $source_dir ..."
exec /bin/bash "$source_dir/linux/reload-ubuntu.sh"
EOF

visudo -cf "$sudoers_tmp" >/dev/null || fail "Generated sudo policy did not validate."
install -o root -g root -m 0644 "$config_tmp" "$config_path"

if ! sshd -t -f /etc/ssh/sshd_config; then
  if [[ $had_previous_config == true ]]; then
    install -o root -g root -m 0644 "$previous_config" "$config_path"
  else
    rm -f "$config_path"
  fi
  fail "Generated sshd configuration did not validate; previous SSH configuration was restored."
fi

# Explicit Port directives may be cumulative on some sshd versions. Reject a
# host with an unexpected effective listener rather than accidentally exposing
# the service through a pre-existing SSH policy.
effective_ports=$(sshd -T -f /etc/ssh/sshd_config | awk '$1 == "port" {print $2}' | sort -u)
[[ "$effective_ports" == "$port" ]] || {
  if [[ $had_previous_config == true ]]; then
    install -o root -g root -m 0644 "$previous_config" "$config_path"
  else
    rm -f "$config_path"
  fi
  fail "Existing SSH configuration leaves unexpected listening ports: ${effective_ports:-none}. Resolve the conflict before enabling operations SSH."
}
effective_addresses=$(sshd -T -f /etc/ssh/sshd_config | awk '$1 == "listenaddress" {print $2}' | sort -u)
[[ "$effective_addresses" == "$listen_address:$port" ]] || {
  if [[ $had_previous_config == true ]]; then
    install -o root -g root -m 0644 "$previous_config" "$config_path"
  else
    rm -f "$config_path"
  fi
  fail "Existing SSH configuration leaves unexpected listening addresses: ${effective_addresses:-none}. Resolve the conflict before enabling operations SSH."
}

install -o root -g root -m 0755 "$status_tmp" "$status_script"
install -o root -g root -m 0755 "$logs_tmp" "$logs_script"
install -o root -g root -m 0755 "$ops_cli_tmp" "$ops_cli"
install -o root -g root -m 0755 "$update_tmp" "$update_script"
install -o root -g root -m 0440 "$sudoers_tmp" "$sudoers_path"

if ! systemctl enable --now ssh.service || ! systemctl restart ssh.service; then
  if [[ $had_previous_config == true ]]; then
    install -o root -g root -m 0644 "$previous_config" "$config_path"
  else
    rm -f "$config_path"
  fi
  systemctl restart ssh.service || true
  fail "Could not start the hardened SSH service; previous SSH configuration was restored."
fi

echo "SSH operations access is ready: ssh -p $port $operator_user@$listen_address"
echo "Allowed source: $allow_from. Password and root login are disabled."
echo "After login, use 'neurobridge-ops status', 'neurobridge-ops logs --follow', or 'neurobridge-ops update'."
