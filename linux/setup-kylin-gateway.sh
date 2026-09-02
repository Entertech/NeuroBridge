#!/usr/bin/env bash
# Interactive, project-local Galaxy Kylin onboarding and recovery assistant.
set -u -o pipefail

root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
runtime_dir="$root_dir/.runtime"
setup_log=

usage() {
  cat <<'EOF'
Usage: ./linux/setup-kylin-gateway.sh

Galaxy Kylin project assistant. It provides a numeric menu for:
  1. First-time setup
  2. Repair project permissions and update source
  3. Check the currently connected USB/serial device
  4. Generate the project serial configuration
  5. Start the gateway
  6. Export complete diagnostics

Run it as the normal desktop user, not with sudo. The assistant requests sudo
only for the exact steps that need system permissions. Git is always run as
the normal user. Prompts with one decision accept yes/no; menus accept numbers.

Process logs are saved below project .runtime/logs. Runtime output remains in
the project and is ignored by Git.
EOF
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

if [[ ${1:-} == -h || ${1:-} == --help ]]; then
  usage
  exit 0
fi
[[ $# -eq 0 ]] || fail "Unknown option: $1"
[[ ${EUID:-$(id -u)} -ne 0 ]] || fail \
  "Run without sudo: ./linux/setup-kylin-gateway.sh"

validate_project_root() {
  [[ -n $root_dir && $root_dir != / ]] || fail "Unsafe project root: $root_dir"
  [[ -f $root_dir/pyproject.toml ]] || fail "pyproject.toml is missing: $root_dir"
  [[ -d $root_dir/.git && ! -L $root_dir/.git ]] || fail \
    ".git must be a real directory in this checkout: $root_dir/.git"
  [[ -f $root_dir/linux/setup-kylin-gateway.sh ]] || fail \
    "Gateway assistant is missing from the validated project root."
}

init_log() {
  [[ -z $setup_log ]] || return 0
  if ! mkdir -p -- "$runtime_dir/logs" 2>/dev/null; then
    printf 'WARNING: 当前项目不可写，选择 2 修复权限后才会开始保存助手日志。\n' >&2
    return 1
  fi
  setup_log="$runtime_dir/logs/setup-kylin-gateway-$(date -u +%Y%m%dT%H%M%SZ)-$$.log"
  if ! touch "$setup_log" 2>/dev/null; then
    setup_log=
    printf 'WARNING: 无法创建助手日志，选择 2 修复项目权限。\n' >&2
    return 1
  fi
  chmod 0600 "$setup_log" 2>/dev/null || true
  printf '[%s] project=%s\n' "$(date --iso-8601=seconds 2>/dev/null || date)" "$root_dir" \
    >>"$setup_log"
  printf 'log=%s\n' "$setup_log"
}

log_message() {
  local message=$*
  printf '%s\n' "$message"
  if [[ -n $setup_log ]]; then
    printf '[%s] %s\n' "$(date --iso-8601=seconds 2>/dev/null || date)" "$message" \
      >>"$setup_log"
  fi
}

run_step() {
  local title=$1
  shift
  log_message ""
  log_message "== $title =="
  if [[ -n $setup_log ]]; then
    {
      printf 'command:'
      printf ' %q' "$@"
      printf '\n'
    } >>"$setup_log"
    "$@" 2>&1 | tee -a "$setup_log"
    local result=${PIPESTATUS[0]}
  else
    "$@"
    local result=$?
  fi
  log_message "result=$result step=$title"
  return "$result"
}

ask_yes_no() {
  local prompt=$1 answer
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

show_next() {
  log_message "下一步命令：$1"
}

project_has_permission_problem() {
  local current_uid
  current_uid=$(id -u)
  [[ -w $root_dir/.git ]] || return 0
  [[ ! -e $root_dir/.git/index || -w $root_dir/.git/index ]] || return 0
  if find "$root_dir" -xdev ! -uid "$current_uid" -print -quit 2>/dev/null | grep -q .; then
    return 0
  fi
  return 1
}

repair_project_permissions() {
  local current_uid current_gid foreign_path
  validate_project_root
  current_uid=$(id -u)
  current_gid=$(id -g)
  foreign_path=$(find "$root_dir" -xdev ! -uid "$current_uid" -print -quit 2>/dev/null || true)

  if ! project_has_permission_problem; then
    log_message "项目权限正常：当前用户可写 Git 索引，项目内文件均属于当前用户。"
    return 0
  fi

  log_message "检测到项目权限异常。示例路径：${foreign_path:-$root_dir/.git}"
  log_message "将只修复已校验项目：$root_dir"
  if ! ask_yes_no "是否修复这个项目的所有权和 Git 写权限？"; then
    log_message "已取消权限修复。"
    show_next "bash linux/neurobridge-kylin-bootstrap.sh（选择 2）"
    return 1
  fi

  run_step "修复项目所有权" \
    sudo chown -R --no-dereference "$current_uid:$current_gid" "$root_dir" || return 1
  run_step "补充 Git 所需的用户权限" \
    sudo find "$root_dir/.git" -type d -exec chmod u+rwx {} + || return 1
  run_step "补充 Git 文件读写权限" \
    sudo find "$root_dir/.git" -type f -exec chmod u+rw {} + || return 1

  [[ -w $root_dir/.git ]] || fail "Permission repair finished, but .git is still not writable."
  [[ ! -e $root_dir/.git/index || -w $root_dir/.git/index ]] || fail \
    "Permission repair finished, but .git/index is still not writable."
  foreign_path=$(find "$root_dir" -xdev ! -uid "$current_uid" -print -quit 2>/dev/null || true)
  [[ -z $foreign_path ]] || fail "Project still contains a file owned by another user: $foreign_path"

  init_log || true
  log_message "项目权限修复完成。以后不要执行 sudo git pull、sudo git checkout 或 sudo git add。"
}

check_git_lock() {
  if [[ -e $root_dir/.git/index.lock ]]; then
    log_message "发现 .git/index.lock；助手不会自动删除它。"
    log_message "先确认没有 git 进程，再人工执行：rm -- '$root_dir/.git/index.lock'"
    return 1
  fi
}

repair_and_update() {
  local before_revision after_revision
  repair_project_permissions || return 1
  check_git_lock || return 1
  before_revision=$(git -C "$root_dir" rev-parse HEAD 2>/dev/null) || {
    log_message "无法读取当前 Git revision。"
    return 1
  }
  run_step "检查 Git 工作区" git -C "$root_dir" status --short --branch || return 1
  run_step "以普通用户更新代码" git -C "$root_dir" pull --ff-only || {
    log_message "更新失败。完整输出已保存在助手日志。"
    show_next "bash linux/neurobridge-kylin-bootstrap.sh（选择 2 重试）"
    return 1
  }
  after_revision=$(git -C "$root_dir" rev-parse HEAD 2>/dev/null) || return 1
  if [[ $before_revision != "$after_revision" ]]; then
    log_message "代码已从 $before_revision 更新到 $after_revision。"
    log_message "为避免继续运行更新前的脚本，本助手现在退出。"
    show_next "bash linux/neurobridge-kylin-bootstrap.sh"
    return 20
  fi
  log_message "代码已经是最新版本。"
  show_next "bash linux/neurobridge-kylin-bootstrap.sh（选择 1 首次配置，或选择 5 启动）"
}

check_current_serial() {
  run_step "检查当前 USB/串口（无需拔插）" \
    sudo "$root_dir/linux/diagnose-kylin-usb-serial.sh"
}

optional_plug_cycle() {
  if ask_yes_no "当前未找到串口，是否执行 60 秒拔插枚举诊断？"; then
    run_step "USB 拔插枚举诊断" \
      sudo "$root_dir/linux/diagnose-kylin-usb-serial.sh" --plug-cycle --timeout 60
    return $?
  fi
  log_message "已跳过拔插诊断；日常运行不要求拔插，网关会遍历当前串口并以握手确认设备。"
  show_next "sudo ./linux/diagnose-kylin-usb-serial.sh"
  return 1
}

generate_serial_config() {
  run_step "生成 USB 串口运行配置" sudo "$root_dir/linux/setup-kylin-serial.sh"
}

start_gateway() {
  log_message "启动后用本机浏览器打开：http://127.0.0.1:8080/"
  log_message "按 Ctrl+C 停止网关并返回菜单。"
  run_step "启动 NeuroBridge" "$root_dir/linux/start-kylin-gateway.sh"
}

export_diagnostics() {
  run_step "导出完整诊断包" \
    sudo "$root_dir/linux/collect-kylin-runtime-diagnostics.sh" --journal-lines 10000
}

first_time_setup() {
  local serial_result
  repair_project_permissions || return 1
  run_step "初始化项目 Python 环境" "$root_dir/linux/setup-kylin-python.sh" || {
    show_next "./linux/setup-kylin-python.sh"
    return 1
  }

  check_current_serial
  serial_result=$?
  if (( serial_result == 2 )); then
    optional_plug_cycle || true
    if ! ask_yes_no "尚未确认串口，是否仍继续生成运行配置？"; then
      log_message "首次配置暂停，已保留全过程日志。"
      show_next "sudo ./linux/diagnose-kylin-usb-serial.sh"
      return 1
    fi
  elif (( serial_result != 0 )); then
    log_message "USB/串口检查执行失败或被中断，不进入后续配置。"
    show_next "sudo ./linux/diagnose-kylin-usb-serial.sh"
    return "$serial_result"
  fi

  generate_serial_config || {
    show_next "sudo ./linux/setup-kylin-serial.sh"
    return 1
  }
  log_message "首次配置完成。"
  if ask_yes_no "是否现在启动网关？"; then
    start_gateway
  else
    show_next "./linux/start-kylin-gateway.sh"
  fi
}

show_menu() {
  cat <<'EOF'

NeuroBridge 银河麒麟一键助手
  1. 新设备首次配置
  2. 修复项目权限并更新代码
  3. 检查当前 USB/串口（无需拔插）
  4. 生成 USB 串口配置
  5. 启动网关
  6. 导出完整诊断包
  0. 退出
EOF
}

validate_project_root
init_log || true

while true; do
  show_menu
  printf '请输入选项 [0-6]: '
  IFS= read -r choice || {
    log_message "输入结束，助手退出。"
    exit 0
  }
  log_message "selected=$choice"
  case $choice in
    1) first_time_setup || true ;;
    2)
      repair_and_update
      result=$?
      (( result == 20 )) && exit 0
      ;;
    3)
      check_current_serial
      result=$?
      if (( result == 0 )); then
        show_next "sudo ./linux/setup-kylin-serial.sh"
      elif (( result == 2 )); then
        optional_plug_cycle || true
      else
        show_next "sudo ./linux/diagnose-kylin-usb-serial.sh"
      fi
      ;;
    4)
      if generate_serial_config; then
        show_next "./linux/start-kylin-gateway.sh"
      else
        show_next "./linux/setup-kylin-python.sh"
      fi
      ;;
    5)
      start_gateway || true
      show_next "./linux/start-kylin-gateway.sh"
      ;;
    6)
      export_diagnostics || true
      show_next "find .runtime/diagnostics -maxdepth 1 -name '*.tar.gz' -ls"
      ;;
    0)
      log_message "助手已退出。"
      exit 0
      ;;
    *) printf '无效选项，请输入 0 到 6。\n' ;;
  esac
done
