#!/usr/bin/env bash
# Render the project-local Galaxy Kylin systemd unit without writing system files.

neurobridge_systemd_exec_quote() {
  local value=$1
  case $value in
    *$'\n'*|*$'\r'*)
      printf 'systemd ExecStart path contains a line break\n' >&2
      return 1
      ;;
  esac
  value=${value//\\/\\\\}
  value=${value//\"/\\\"}
  value=${value//%/%%}
  printf '"%s"' "$value"
}

neurobridge_systemd_working_directory() {
  local value=$1
  [[ $value == /* ]] || {
    printf 'systemd WorkingDirectory must be absolute: %s\n' "$value" >&2
    return 1
  }
  case $value in
    *$'\n'*|*$'\r'*)
      printf 'systemd WorkingDirectory contains a line break\n' >&2
      return 1
      ;;
  esac

  # systemd 245 on Galaxy Kylin passes outer quotes through to the path parser
  # for WorkingDirectory=. Emit the absolute path directly and only escape the
  # percent sign used by systemd specifier expansion.
  value=${value//%/%%}
  printf '%s' "$value"
}

render_neurobridge_kylin_unit() {
  [[ $# -eq 5 ]] || {
    printf 'render_neurobridge_kylin_unit expects root, start script, user, group, and marker\n' >&2
    return 2
  }
  local root_dir=$1 start_script=$2 service_user=$3 service_group=$4 managed_marker=$5
  local working_directory quoted_start

  case $managed_marker in
    *$'\n'*|*$'\r'*)
      printf 'systemd managed marker contains a line break\n' >&2
      return 1
      ;;
  esac

  working_directory=$(neurobridge_systemd_working_directory "$root_dir") || return
  quoted_start=$(neurobridge_systemd_exec_quote "$start_script") || return

  cat <<EOF
$managed_marker
[Unit]
Description=NeuroBridge Galaxy Kylin USB serial gateway
After=local-fs.target systemd-udev-settle.service
Wants=systemd-udev-settle.service
StartLimitIntervalSec=60
StartLimitBurst=10

[Service]
Type=simple
User=$service_user
Group=$service_group
WorkingDirectory=$working_directory
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=$quoted_start
Restart=on-failure
RestartSec=3
TimeoutStopSec=20
UMask=0077
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=full
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes

[Install]
WantedBy=multi-user.target
EOF
}
