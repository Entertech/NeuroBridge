#!/usr/bin/env bash
# Configure a password-authenticated, operations-only SSH entry point for a deployed gateway.
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
    --operator-password-stdin \
    --listen-address <gateway-private-ip> \
    --allow-from <operator-ip-or-cidr> [--port <1-65535>]

The password must be supplied as one line on standard input; it is never an
argument, configuration value, or log entry. This command restricts SSH to the
named local operator account, disables root and public-key login, and binds
sshd only to the specified gateway address. The account receives only NeuroBridge status, log,
staged-code update, start, stop, and restart privileges through sudo. The same
trusted operator account can upload a complete release to
/srv/neurobridge-release and apply it; the update command never fetches code
from a network or accepts another source path.
EOF
}

operator_user=
operator_password_stdin=false
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
    --operator-password-stdin)
      operator_password_stdin=true
      shift
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
[[ -n "$operator_user" && "$operator_password_stdin" == true && -n "$listen_address" && -n "$allow_from" ]] || {
  usage >&2
  exit 1
}
[[ "$operator_user" =~ ^[a-z_][a-z0-9_-]{0,31}\$?$ ]] || fail "--operator-user must be a valid local Linux user name."
[[ "$port" =~ ^[0-9]+$ ]] && ((port >= 1 && port <= 65535)) || fail "--port must be between 1 and 65535."
[[ -r /etc/os-release ]] || fail "Cannot identify the operating system."
. /etc/os-release
[[ ${ID:-} == "ubuntu" && ${VERSION_ID:-} == "24.04" ]] || fail "SSH operations setup requires Ubuntu 24.04 LTS."
command -v sshd >/dev/null 2>&1 || fail "openssh-server is missing. Run linux/prepare-ubuntu24.04-environment.sh while the gateway can access the approved package source."
command -v chpasswd >/dev/null 2>&1 || fail "chpasswd is missing; install the Ubuntu account-management prerequisites."
command -v systemctl >/dev/null 2>&1 || fail "systemd is required."
IFS= read -r operator_password || fail "--operator-password-stdin requires one password line on standard input."
[[ ${#operator_password} -ge 12 ]] || fail "Operator password must contain at least 12 characters."

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
command_script=/usr/local/sbin/neurobridge-ops-command
install -d -o root -g root -m 0755 "$config_dir"
install -d -o root -g root -m 0755 /run/sshd
# The directory is initially root-owned. After the trusted operator account is
# ready, setup transfers this directory to that account for release uploads.
install -d -o root -g root -m 0750 "$update_source_dir"

config_tmp=$(mktemp)
sudoers_tmp=$(mktemp)
status_tmp=$(mktemp)
logs_tmp=$(mktemp)
ops_cli_tmp=$(mktemp)
update_tmp=$(mktemp)
command_tmp=$(mktemp)
previous_config=$(mktemp)
previous_sudoers=$(mktemp)
previous_status=$(mktemp)
previous_logs=$(mktemp)
previous_ops_cli=$(mktemp)
previous_update=$(mktemp)
previous_command=$(mktemp)
had_previous_config=false
had_previous_sudoers=false
had_previous_status=false
had_previous_logs=false
had_previous_ops_cli=false
had_previous_update=false
had_previous_command=false
operator_existed=false
operator_created=false
operator_shadow_before=
ssh_was_enabled=false
ssh_was_active=false
transaction_active=false

cleanup() {
  rm -f "$config_tmp" "$sudoers_tmp" "$status_tmp" "$logs_tmp" "$ops_cli_tmp" "$update_tmp" "$command_tmp" \
    "$previous_config" "$previous_sudoers" "$previous_status" "$previous_logs" "$previous_ops_cli" "$previous_update" "$previous_command"
}

backup_path() {
  local path=$1 backup=$2 existed_name=$3
  if [[ -e "$path" ]]; then
    cp -p "$path" "$backup"
    printf -v "$existed_name" '%s' true
  fi
}

restore_path() {
  local path=$1 backup=$2 existed=$3
  if [[ "$existed" == true ]]; then
    cp -p "$backup" "$path"
  else
    rm -f "$path"
  fi
}

rollback() {
  set +e
  restore_path "$sudoers_path" "$previous_sudoers" "$had_previous_sudoers"
  restore_path "$status_script" "$previous_status" "$had_previous_status"
  restore_path "$logs_script" "$previous_logs" "$had_previous_logs"
  restore_path "$ops_cli" "$previous_ops_cli" "$had_previous_ops_cli"
  restore_path "$update_script" "$previous_update" "$had_previous_update"
  restore_path "$command_script" "$previous_command" "$had_previous_command"

  if [[ "$operator_created" == true ]]; then
    userdel --remove "$operator_user" 2>/dev/null || userdel "$operator_user" 2>/dev/null || true
  elif [[ "$operator_existed" == true ]]; then
    shadow_hash_before=${operator_shadow_before#*:}
    shadow_hash_before=${shadow_hash_before%%:*}
    usermod --password "$shadow_hash_before" "$operator_user" 2>/dev/null || true
  fi

  restore_path "$config_path" "$previous_config" "$had_previous_config"
  if [[ "$ssh_was_enabled" == true ]]; then
    systemctl enable ssh.service >/dev/null 2>&1 || true
  else
    systemctl disable ssh.service >/dev/null 2>&1 || true
  fi
  if [[ "$ssh_was_active" == true ]]; then
    systemctl restart ssh.service >/dev/null 2>&1 || true
  else
    systemctl stop ssh.service >/dev/null 2>&1 || true
  fi
}

on_exit() {
  local status=$?
  if [[ "$transaction_active" == true ]]; then
    rollback
  fi
  cleanup
  trap - EXIT
  exit "$status"
}
trap on_exit EXIT

backup_path "$config_path" "$previous_config" had_previous_config
backup_path "$sudoers_path" "$previous_sudoers" had_previous_sudoers
backup_path "$status_script" "$previous_status" had_previous_status
backup_path "$logs_script" "$previous_logs" had_previous_logs
backup_path "$ops_cli" "$previous_ops_cli" had_previous_ops_cli
backup_path "$update_script" "$previous_update" had_previous_update
backup_path "$command_script" "$previous_command" had_previous_command
if id -u "$operator_user" >/dev/null 2>&1; then
  operator_existed=true
  operator_shadow_before=$(getent shadow "$operator_user") || fail "Cannot back up the existing operator account."
fi
if systemctl is-enabled --quiet ssh.service; then
  ssh_was_enabled=true
fi
if systemctl is-active --quiet ssh.service; then
  ssh_was_active=true
fi
transaction_active=true

cat >"$config_tmp" <<EOF
# Managed by linux/configure-ssh-operations.sh. Do not edit in place.
# Remote maintenance only; this is not a B-end data interface.
AddressFamily inet
ListenAddress $listen_address
Port $port
PermitRootLogin no
PasswordAuthentication yes
KbdInteractiveAuthentication no
PermitEmptyPasswords no
PubkeyAuthentication no
AuthenticationMethods password
X11Forwarding no
AllowAgentForwarding no
AllowTcpForwarding no
GatewayPorts no
PermitTunnel no
AllowUsers $operator_user

# Connections outside the approved operations source range cannot obtain a
# shell or execute a command even if they provide otherwise valid credentials.
Match User $operator_user Address *,!$allow_from
    ForceCommand /usr/bin/false
    PermitTTY no
    AllowTcpForwarding no
Match all
EOF

cat >"$sudoers_tmp" <<EOF
# Managed by linux/configure-ssh-operations.sh.
Cmnd_Alias NEUROBRIDGE_OPERATIONS = /usr/local/sbin/neurobridge-ops-command, /usr/local/sbin/neurobridge-ops-command *
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
  audit [--lines N]      Show SSH operations audit events (default 200, maximum 1000).
  update                 Apply the operator-staged release and reload the gateway.
  start | stop | restart  Control the NeuroBridge gateway service.
  help                   Show this help.
USAGE
}

case "${1:-help}" in
  status)
    [[ $# -eq 1 ]] || { usage >&2; exit 2; }
    exec sudo -- /usr/local/sbin/neurobridge-ops-command status
    ;;
  logs)
    shift
    exec sudo -- /usr/local/sbin/neurobridge-ops-command logs "$@"
    ;;
  audit)
    shift
    exec sudo -- /usr/local/sbin/neurobridge-ops-command audit "$@"
    ;;
  update)
    [[ $# -eq 1 ]] || { usage >&2; exit 2; }
    exec sudo -- /usr/local/sbin/neurobridge-ops-command update
    ;;
  start|stop|restart)
    [[ $# -eq 1 ]] || { usage >&2; exit 2; }
    exec sudo -- /usr/local/sbin/neurobridge-ops-command "$1"
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

cat >"$command_tmp" <<'EOF'
#!/usr/bin/env bash
# Root-owned command gate and journal audit logger for SSH operations.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export LC_ALL=C

fail() {
  echo "ERROR: $*" >&2
  exit 2
}

[[ ${EUID} -eq 0 ]] || fail "This helper must run as root through sudo."

operator=${SUDO_USER:-unknown}
audit() {
  local action=$1 request=$2 result=$3
  /usr/bin/logger -p authpriv.notice -t neurobridge-ops-audit -- \
    "operator=$operator action=$action request=$request result=$result"
}

run_and_audit() {
  local action=$1 request=$2
  shift 2
  audit "$action" "$request" started
  if "$@"; then
    audit "$action" "$request" success
    return 0
  fi
  local status=$?
  audit "$action" "$request" "failed:$status"
  return "$status"
}

lines=200
parse_lines() {
  if [[ $# -eq 0 ]]; then
    return 0
  fi
  [[ $# -eq 2 && $1 == --lines && $2 =~ ^[0-9]+$ && $2 -ge 1 && $2 -le 1000 ]] \
    || fail "Usage: neurobridge-ops $current_action [--lines N] (N: 1-1000)"
  lines=$2
}

case "${1:-}" in
  status)
    [[ $# -eq 1 ]] || fail "Usage: neurobridge-ops status"
    run_and_audit status status /usr/local/sbin/neurobridge-ops-status
    ;;
  logs)
    shift
    if [[ ${1:-} == --follow || ${1:-} == -f ]]; then
      [[ $# -eq 1 ]] || fail "Usage: neurobridge-ops logs [--follow|-f|--lines N]"
      audit logs follow started
      exec /usr/local/sbin/neurobridge-ops-logs --follow
    fi
    current_action=logs
    parse_lines "$@"
    run_and_audit logs "lines:$lines" /usr/local/sbin/neurobridge-ops-logs --lines "$lines"
    ;;
  audit)
    shift
    current_action=audit
    parse_lines "$@"
    run_and_audit audit "lines:$lines" /usr/bin/journalctl -t neurobridge-ops-audit -n "$lines" --no-pager
    ;;
  update)
    [[ $# -eq 1 ]] || fail "Usage: neurobridge-ops update"
    run_and_audit update staged-release /usr/local/sbin/neurobridge-ops-update
    ;;
  start|stop|restart)
    [[ $# -eq 1 ]] || fail "Usage: neurobridge-ops start|stop|restart"
    run_and_audit "$1" neurobridge.service /usr/bin/systemctl "$1" neurobridge.service
    ;;
  *)
    fail "Usage: neurobridge-ops {status|logs|audit|update|start|stop|restart}"
    ;;
esac
EOF

cat >"$update_tmp" <<'EOF'
#!/usr/bin/env bash
# Apply a complete release staged by the trusted SSH operator without network access.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

[[ ${EUID} -eq 0 ]] || fail "This helper must run as root through sudo."
release_owner=${SUDO_USER:-}
[[ -n "$release_owner" && "$release_owner" != root ]] || fail "Cannot identify the trusted SSH operator."
source_dir=/srv/neurobridge-release
[[ -d "$source_dir" ]] || fail "Missing staged release directory: $source_dir"
for required in pyproject.toml requirements.lock linux/reload-ubuntu.sh linux/install-ubuntu.sh; do
  [[ -f "$source_dir/$required" ]] || fail "Staged release is incomplete: missing $required"
done
[[ -x "$source_dir/linux/reload-ubuntu.sh" ]] || fail "Staged reload script is not executable."

# Keep the upload predictable: only the authenticated operator may own staged
# content, and symlinks or group/world-writable paths are rejected.
unsafe_link=$(find "$source_dir" -xdev -type l -print -quit)
[[ -z "$unsafe_link" ]] || fail "Staged release contains a symlink: $unsafe_link"
unsafe_path=$(find "$source_dir" -xdev \( -type f -o -type d \) \( ! -user "$release_owner" -o -perm -0022 \) -print -quit)
[[ -z "$unsafe_path" ]] || fail "Staged release must be owned by $release_owner and not group/world writable: $unsafe_path"

echo "Applying staged NeuroBridge release from $source_dir ..."
exec /bin/bash "$source_dir/linux/reload-ubuntu.sh"
EOF

visudo -cf "$sudoers_tmp" >/dev/null || fail "Generated sudo policy did not validate."
install -o root -g root -m 0644 "$config_tmp" "$config_path"

if ! sshd -t -f /etc/ssh/sshd_config; then
  fail "Generated sshd configuration did not validate; previous SSH configuration was restored."
fi

# Explicit Port directives may be cumulative on some sshd versions. Reject a
# host with an unexpected effective listener rather than accidentally exposing
# the service through a pre-existing SSH policy.
effective_ports=$(sshd -T -f /etc/ssh/sshd_config | awk '$1 == "port" {print $2}' | sort -u)
[[ "$effective_ports" == "$port" ]] || {
  fail "Existing SSH configuration leaves unexpected listening ports: ${effective_ports:-none}. Resolve the conflict before enabling operations SSH."
}
effective_addresses=$(sshd -T -f /etc/ssh/sshd_config | awk '$1 == "listenaddress" {print $2}' | sort -u)
[[ "$effective_addresses" == "$listen_address:$port" ]] || {
  fail "Existing SSH configuration leaves unexpected listening addresses: ${effective_addresses:-none}. Resolve the conflict before enabling operations SSH."
}

if ! systemctl enable --now ssh.service || ! systemctl restart ssh.service; then
  fail "Could not start the hardened SSH service; previous SSH configuration was restored."
fi

if [[ "$operator_existed" == false ]]; then
  useradd --create-home --shell /bin/bash "$operator_user"
  operator_created=true
fi
printf '%s:%s\n' "$operator_user" "$operator_password" | chpasswd
unset operator_password
usermod --unlock "$operator_user"

install -o root -g root -m 0755 "$status_tmp" "$status_script"
install -o root -g root -m 0755 "$logs_tmp" "$logs_script"
install -o root -g root -m 0755 "$ops_cli_tmp" "$ops_cli"
install -o root -g root -m 0755 "$update_tmp" "$update_script"
install -o root -g root -m 0755 "$command_tmp" "$command_script"
install -o root -g root -m 0440 "$sudoers_tmp" "$sudoers_path"
# This is a single-role deployment: the trusted SSH operator both uploads and
# applies the release. Because uploaded code is later installed as root, treat
# this account as a gateway administrator credential.
chown "$operator_user:$operator_user" "$update_source_dir"
chmod 0750 "$update_source_dir"

transaction_active=false

echo "SSH operations access is ready: ssh -p $port $operator_user@$listen_address"
echo "Allowed source: $allow_from. Root and public-key login are disabled."
echo "After login, use 'neurobridge-ops status', 'neurobridge-ops audit', or 'neurobridge-ops update'."
