#!/usr/bin/env bash
# Retire a Galaxy Kylin NeuroBridge source deployment: stop and remove the
# project-managed systemd unit, and optionally delete the source checkout.
# Every persistent artifact (configuration, recordings, logs, algorithm bridge,
# Python environment) lives inside the ignored project .runtime directory, so
# removing this checkout is the complete removal of a source-mode deployment.
set -euo pipefail

unit_name=neurobridge.service
unit_path="/etc/systemd/system/$unit_name"
managed_marker="# Managed by NeuroBridge Galaxy Kylin project autostart"
script_path=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/$(basename "${BASH_SOURCE[0]}")
root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
runtime_dir="$root_dir/.runtime"
start_script="$root_dir/linux/start-kylin-gateway.sh"
config_path="$runtime_dir/config/gateway.toml"
preference_path="$runtime_dir/config/kylin-autostart.conf"
port_pattern='127\.0\.0\.1:(8080|8765|8766)([[:space:]]|$)'

action=
assume_yes=false
skip_backup=false
force=false
backup_dir=${HOME:-}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: ./linux/uninstall-kylin-project.sh <action> [options]

Actions:
  status      Report the managed unit, leftover gateway processes, listening
              ports and the checkout contents. Changes nothing.
  uninstall   Stop and remove the project-managed neurobridge.service unit.
              The source checkout and .runtime field data are kept.
  purge       uninstall, archive the .runtime field data, then delete the whole
              source checkout. This is the complete removal of a source-mode
              deployment.

Options:
  --yes             Skip the interactive confirmations (non-interactive runs).
  --no-backup       With purge: do not archive .runtime data before deleting.
  --backup-dir DIR  Directory for the archive (default: $HOME).
  --force           Remove a managed unit whose ExecStart points at a different
                    checkout that no longer exists.

Run as the normal desktop user, not with sudo. The script requests sudo only
for the exact systemd changes and for deleting files owned by root. A managed
unit that was not installed by this project is never touched.

Examples:
  ./linux/uninstall-kylin-project.sh status
  ./linux/uninstall-kylin-project.sh uninstall
  ./linux/uninstall-kylin-project.sh purge --yes
EOF
}

require_systemd_tools() {
  command -v systemctl >/dev/null 2>&1 || fail "systemctl is unavailable."
  command -v sudo >/dev/null 2>&1 || fail "sudo is required for systemd changes."
}

require_kylin_x86_64() {
  [[ -r /etc/os-release ]] || fail "/etc/os-release is unavailable."
  # shellcheck source=/dev/null
  . /etc/os-release
  [[ ${ID,,} == kylin ]] || fail "This helper requires Galaxy Kylin; detected ID=${ID:-unknown}."
  [[ $(uname -m) == x86_64 ]] || fail "This deployment requires x86_64; detected $(uname -m)."
}

validate_project_root() {
  [[ -n $root_dir && $root_dir != / ]] || fail "Unsafe project root: $root_dir"
  [[ $root_dir == /* ]] || fail "Project root must be absolute: $root_dir"
  [[ -f $root_dir/pyproject.toml ]] || fail "pyproject.toml is missing: $root_dir"
  [[ -d $root_dir/.git && ! -L $root_dir/.git ]] || fail \
    "A complete NeuroBridge Git checkout is required: $root_dir"
  [[ -f $start_script ]] || fail "Startup script is missing: $start_script"
  [[ ! -L $runtime_dir ]] || fail ".runtime must be a real directory, not a symlink."
}

validate_purge_target() {
  validate_project_root
  [[ $root_dir == */*/* ]] || fail "Refusing to delete a path this shallow: $root_dir"
  case $root_dir in
    /|/bin|/boot|/dev|/etc|/home|/lib|/lib64|/media|/mnt|/opt|/proc|/root|/run|/sbin|/srv|/sys|/tmp|/usr|/var)
      fail "Refusing to delete a system directory: $root_dir" ;;
  esac
  [[ -z ${HOME:-} || $root_dir != "$HOME" ]] || fail "Refusing to delete the home directory: $root_dir"
  [[ ! -L $root_dir ]] || fail "Refusing to delete through a symlink: $root_dir"
}

confirm() {
  local prompt=$1 answer
  if [[ $assume_yes == true ]]; then
    printf '%s [--yes: 已自动确认]\n' "$prompt"
    return 0
  fi
  while true; do
    printf '%s [yes/no]: ' "$prompt"
    IFS= read -r answer || return 1
    case $answer in
      [Yy][Ee][Ss]|[Yy]) return 0 ;;
      [Nn][Oo]|[Nn]) return 1 ;;
      *) printf '请输入 yes 或 no。\n' ;;
    esac
  done
}

confirm_typed() {
  local prompt=$1 expected=$2 answer
  if [[ $assume_yes == true ]]; then
    printf '%s [--yes: 已自动确认]\n' "$prompt"
    return 0
  fi
  printf '%s\n请输入 %s 继续：' "$prompt" "$expected"
  IFS= read -r answer || return 1
  [[ $answer == "$expected" ]]
}

unit_state() {
  if ! sudo test -e "$unit_path"; then
    if systemctl cat "$unit_name" >/dev/null 2>&1; then
      printf 'foreign'
    else
      printf 'absent'
    fi
    return 0
  fi
  if sudo grep -Fqx "$managed_marker" "$unit_path" 2>/dev/null; then
    printf 'managed'
  else
    printf 'foreign'
  fi
}

unit_references_this_checkout() {
  # The unit renderer doubles '%' for systemd specifier expansion, so accept
  # both the raw path and the escaped form.
  sudo grep -Fq -- "$start_script" "$unit_path" 2>/dev/null && return 0
  sudo grep -Fq -- "${start_script//%/%%}" "$unit_path" 2>/dev/null
}

foreground_processes() {
  local uid
  uid=$(id -u)
  pgrep -u "$uid" -af -- "-m neurobridge --config $config_path" 2>/dev/null || true
}

stop_foreground_processes() {
  local pids pid
  pids=$(foreground_processes)
  if [[ -z $pids ]]; then
    printf 'foregroundProcesses=none\n'
    return 0
  fi
  printf '发现以前台方式运行的项目网关进程：\n%s\n' "$pids"
  if ! confirm "是否停止这些前台进程？"; then
    printf '已跳过。它们会继续占用串口和端口；请手工停止后再重试。\n'
    return 1
  fi
  while read -r pid _; do
    [[ -n $pid ]] || continue
    kill -TERM -- "$pid" 2>/dev/null || true
  done <<<"$pids"
  sleep 2
  pids=$(foreground_processes)
  if [[ -n $pids ]]; then
    printf '正常停止未生效，改用强制停止：\n%s\n' "$pids"
    while read -r pid _; do
      [[ -n $pid ]] || continue
      kill -KILL -- "$pid" 2>/dev/null || true
    done <<<"$pids"
    sleep 1
  fi
  pids=$(foreground_processes)
  if [[ -n $pids ]]; then
    printf 'WARNING: 仍有项目网关进程存活：\n%s\n' "$pids" >&2
    return 1
  fi
  printf 'foregroundProcesses=stopped\n'
}

stop_and_remove_unit() {
  local state
  state=$(unit_state)
  case $state in
    absent)
      printf '未发现 %s；系统侧没有需要移除的单元。\n' "$unit_name"
      return 0
      ;;
    foreign)
      fail "$unit_path 存在但不是本项目安装的受管单元，拒绝停止或删除。
如需处理，请人工确认该单元的来源后再操作。"
      ;;
  esac

  if ! unit_references_this_checkout; then
    if [[ $force != true ]]; then
      fail "受管单元 $unit_path 的 ExecStart 不指向本项目：
  期望：$start_script
如果它指向的旧源码目录已经不存在，确认无误后重新执行并加上 --force。"
    fi
    printf 'WARNING: --force 已指定，将移除指向其它源码目录的受管单元：%s\n' "$unit_path" >&2
  fi

  sudo systemctl disable --now "$unit_name" || fail \
    "无法停止并禁用 $unit_name。请用 sudo systemctl status $unit_name 检查。"
  sudo rm -f -- "$unit_path" || fail "无法删除 $unit_path。"
  sudo systemctl daemon-reload || fail "systemctl daemon-reload 失败。"
  sudo systemctl reset-failed "$unit_name" 2>/dev/null || true
  printf 'unitRemoved=%s\n' "$unit_path"
}

clear_autostart_preference() {
  if [[ -e $preference_path && ( ! -f $preference_path || -L $preference_path ) ]]; then
    printf 'WARNING: 自启偏好文件不是安全的普通文件，保留不动：%s\n' "$preference_path" >&2
    return 0
  fi
  [[ -f $preference_path ]] || return 0
  rm -f -- "$preference_path" || fail "无法删除自启偏好文件：$preference_path"
  printf 'autostartPreference=reset(default enabled) path=%s\n' "$preference_path"
}

backup_field_data() {
  local stamp archive item
  local items=()
  [[ -n $backup_dir ]] || fail "HOME 未设置；请用 --backup-dir 指定备份目录。"
  [[ -d $backup_dir ]] || fail "备份目录不存在：$backup_dir"
  [[ -w $backup_dir ]] || fail "备份目录不可写：$backup_dir"
  for item in config recordings; do
    [[ -e $runtime_dir/$item ]] || continue
    items+=("$item")
  done
  if [[ ${#items[@]} -eq 0 ]]; then
    printf 'backup=skipped(no .runtime/config or .runtime/recordings)\n'
    return 0
  fi
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  archive="$backup_dir/neurobridge-backup-$stamp.tar.gz"
  (cd "$runtime_dir" && tar -czf "$archive" "${items[@]}") || fail "备份失败：$archive"
  chmod 0600 -- "$archive" 2>/dev/null || true
  printf 'backup=%s\n' "$archive"
  printf '提示：日志不在备份内；如需保留请另行复制 %s\n' "$runtime_dir/logs"
}

checkout_inventory() {
  local heading=${1:-项目内可清理的内容} path
  printf '%s：\n' "$heading"
  for path in .venv venv .runtime python-runtime recordings; do
    [[ -e $root_dir/$path ]] || continue
    printf '  %s\n' "$root_dir/$path"
  done
  if command -v du >/dev/null 2>&1; then
    printf '  合计：%s\n' "$(du -sh -- "$root_dir" 2>/dev/null | cut -f1)"
  fi
}

remove_checkout() {
  local target=$1 staging=$2
  [[ -n $target && $target == /* && $target != / ]] || fail "Unsafe removal target: ${target:-<empty>}"
  [[ $target != "$HOME" ]] || fail "Refusing to delete the home directory: $target"
  [[ ! -L $target ]] || fail "Refusing to delete through a symlink: $target"
  [[ -f $target/pyproject.toml && -d $target/.git ]] || fail \
    "Removal target is no longer a complete checkout: $target"
  printf '正在删除源码目录：%s\n' "$target"
  # The caller usually started from inside the checkout; leave the doomed
  # directory before removing it so this process keeps a valid working tree.
  cd /
  sudo rm -rf --one-file-system -- "$target" || fail "删除失败：$target"
  if [[ -e $target ]]; then
    printf 'WARNING: %s 仍然存在，可能有内容位于其它挂载点；请人工确认后再删除。\n' "$target" >&2
  else
    printf 'sourceCheckout=removed path=%s\n' "$target"
    printf '提示：原项目目录已不存在，请在你的终端执行 cd ~ 后再继续操作。\n'
  fi
  case $staging in
    "${TMPDIR:-/tmp}"/neurobridge-purge.*) rm -rf -- "$staging" || true ;;
  esac
}

purge_checkout() {
  local staging
  staging=$(mktemp -d "${TMPDIR:-/tmp}/neurobridge-purge.XXXXXX") || fail \
    "无法创建临时目录。"
  install -m 0700 -- "$script_path" "$staging/self.sh" || fail "无法准备临时副本。"
  printf '改用临时副本继续删除，避免删除运行中的脚本：%s\n' "$staging/self.sh"
  exec bash "$staging/self.sh" __remove-checkout "$root_dir" "$staging"
}

show_status() {
  local state exec_start listeners
  state=$(unit_state)
  printf 'project=%s\n' "$root_dir"
  printf 'unit=%s state=%s path=%s\n' "$unit_name" "$state" "$unit_path"
  printf 'enabled=%s\n' "$(systemctl is-enabled "$unit_name" 2>/dev/null || printf 'not-installed')"
  printf 'active=%s\n' "$(systemctl is-active "$unit_name" 2>/dev/null || printf 'inactive')"
  exec_start=$(sudo grep -Fm1 'ExecStart=' "$unit_path" 2>/dev/null || true)
  [[ -n $exec_start ]] && printf 'unitExecStart=%s\n' "$exec_start"
  printf 'runtimeDir=%s\n' "$runtime_dir"
  printf 'foregroundProcesses=%s\n' "$(foreground_processes | tr '\n' '|' || true)"
  if command -v ss >/dev/null 2>&1; then
    listeners=$(ss -lnt 2>/dev/null | grep -E "$port_pattern" || true)
    if [[ -n $listeners ]]; then
      printf '%s\n' "$listeners"
    else
      printf 'listening=8080/8765/8766 none\n'
    fi
  fi
  if [[ -e $preference_path ]]; then
    printf 'autostartPreference=%s\n' "$(cat "$preference_path" 2>/dev/null || true)"
  fi
  checkout_inventory
}

# Internal action used by the purge staging copy; not part of the public usage.
if [[ ${1:-} == __remove-checkout ]]; then
  shift
  remove_checkout "${1:-}" "${2:-}"
  exit 0
fi

while [[ $# -gt 0 ]]; do
  case $1 in
    status|uninstall|purge)
      [[ -z $action ]] || fail "Expected exactly one action; got '$action' and '$1'."
      action=$1
      shift
      ;;
    --yes|-y) assume_yes=true; shift ;;
    --no-backup) skip_backup=true; shift ;;
    --force) force=true; shift ;;
    --backup-dir)
      [[ $# -ge 2 ]] || fail "--backup-dir requires a value."
      backup_dir=$2
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) fail "Unknown argument: $1 (see --help)" ;;
  esac
done

if [[ -z $action ]]; then
  usage >&2
  exit 2
fi

[[ ${EUID:-$(id -u)} -ne 0 ]] || fail \
  "Run without sudo: ./linux/uninstall-kylin-project.sh $action"

case $action in
  status)
    require_systemd_tools
    validate_project_root
    show_status
    ;;
  uninstall)
    require_systemd_tools
    require_kylin_x86_64
    validate_project_root
    printf '将停止并移除 %s；源码目录与 .runtime 现场数据会保留。\n' "$unit_name"
    confirm "是否继续卸载？" || { printf '已取消，未做任何改动。\n'; exit 1; }
    stop_foreground_processes || exit 1
    stop_and_remove_unit
    clear_autostart_preference
    printf '卸载完成：systemd 单元已移除，源码目录保留在 %s\n' "$root_dir"
    printf '如需彻底删除源码，请执行：./linux/uninstall-kylin-project.sh purge\n'
    show_status
    ;;
  purge)
    require_systemd_tools
    require_kylin_x86_64
    validate_purge_target
    printf '\n即将停止服务、备份现场数据并删除整个源码目录：\n  %s\n' "$root_dir"
    checkout_inventory "将被删除的内容"
    printf '删除后无法恢复；已配置的串口、算法、日志与录制都会一并消失。\n'
    if [[ $skip_backup == true ]]; then
      printf 'WARNING: --no-backup 已指定，.runtime 现场数据不会备份。\n' >&2
    else
      printf '现场数据（.runtime/config、.runtime/recordings）将备份到：%s\n' "$backup_dir"
    fi
    confirm_typed "确认继续？" "DELETE" || { printf '已取消，未做任何改动。\n'; exit 1; }
    stop_foreground_processes || exit 1
    stop_and_remove_unit
    if [[ $skip_backup == true ]]; then
      printf 'backup=skipped(--no-backup)\n'
    else
      backup_field_data
    fi
    purge_checkout
    ;;
esac
