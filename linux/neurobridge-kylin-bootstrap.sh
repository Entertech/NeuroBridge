#!/usr/bin/env bash
# Network-free startup entry for a complete NeuroBridge checkout on Galaxy
# Kylin. Older checkouts must first receive the generated one-file offline
# update runner; startup itself never fetches or pulls source code.
set -u -o pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
if [[ -f $script_dir/pyproject.toml ]]; then
  project_dir=$script_dir
elif [[ -f $script_dir/../pyproject.toml ]]; then
  project_dir=$(cd "$script_dir/.." && pwd -P)
elif [[ -f $PWD/pyproject.toml ]]; then
  project_dir=$(pwd -P)
else
  project_dir=$script_dir
fi
assistant="$project_dir/linux/setup-kylin-gateway.sh"
bootstrap_log=

usage() {
  cat <<'EOF'
Usage: bash neurobridge-kylin-bootstrap.sh

Run this file from linux/ in a complete NeuroBridge checkout as the normal
desktop user. It validates permissions and opens the numeric gateway menu.

Startup never runs git fetch or git pull. To update an old/offline computer,
generate neurobridge-kylin-offline-update.run on a development computer,
transfer that single file to the project root, and run it once.

Do not run with sudo. The script requests sudo only for ownership repair; all
gateway commands run as the normal user.
EOF
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
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

if [[ ${1:-} == -h || ${1:-} == --help ]]; then
  usage
  exit 0
fi
[[ $# -eq 0 ]] || fail "Unknown option: $1"
[[ ${EUID:-$(id -u)} -ne 0 ]] || fail \
  "不要使用 sudo；请直接执行：bash neurobridge-kylin-bootstrap.sh"

[[ -n $project_dir && $project_dir != / ]] || fail "不安全的项目目录：$project_dir"
[[ -f $project_dir/pyproject.toml ]] || fail \
  "请把本文件放入 NeuroBridge 项目根目录，或从项目 linux/ 目录运行。"
[[ -d $project_dir/.git && ! -L $project_dir/.git ]] || fail \
  "当前目录不是可安全修复的 NeuroBridge Git 项目：$project_dir"

current_uid=$(id -u)
current_gid=$(id -g)
permission_problem=false
[[ -w $project_dir/.git ]] || permission_problem=true
[[ ! -e $project_dir/.git/index || -w $project_dir/.git/index ]] || permission_problem=true
foreign_path=$(find "$project_dir" -xdev ! -uid "$current_uid" -print -quit 2>/dev/null || true)
[[ -z $foreign_path ]] || permission_problem=true

if [[ $permission_problem == true ]]; then
  printf '检测到项目权限异常：%s\n' "${foreign_path:-$project_dir/.git}"
  printf '只会修复已验证项目：%s\n' "$project_dir"
  ask_yes_no "是否修复项目权限？" || fail "已取消；项目未修改。"
  sudo chown -R --no-dereference "$current_uid:$current_gid" "$project_dir" \
    || fail "项目所有权修复失败。"
  sudo find "$project_dir/.git" -type d -exec chmod u+rwx {} + \
    || fail "Git 目录权限修复失败。"
  sudo find "$project_dir/.git" -type f -exec chmod u+rw {} + \
    || fail "Git 文件权限修复失败。"
  [[ -w $project_dir/.git ]] || fail "修复后 .git 仍不可写。"
  [[ ! -e $project_dir/.git/index || -w $project_dir/.git/index ]] || fail \
    "修复后 .git/index 仍不可写。"
  foreign_path=$(find "$project_dir" -xdev ! -uid "$current_uid" -print -quit 2>/dev/null || true)
  [[ -z $foreign_path ]] || fail "仍有文件不属于当前用户：$foreign_path"
  printf '项目权限修复完成。以后不要执行 sudo git pull。\n'
fi

[[ ! -L $project_dir/.runtime ]] || fail ".runtime 不能是符号链接。"
mkdir -p -- "$project_dir/.runtime/logs" || fail "无法创建项目日志目录。"
bootstrap_log="$project_dir/.runtime/logs/kylin-bootstrap-$(date -u +%Y%m%dT%H%M%SZ)-$$.log"
touch "$bootstrap_log" || fail "无法创建引导日志。"
chmod 0600 "$bootstrap_log" 2>/dev/null || true
exec > >(tee -a "$bootstrap_log") 2>&1
printf 'project=%s\nlog=%s\nsourceUpdateAttempted=false\n' "$project_dir" "$bootstrap_log"

if [[ ! -f $assistant ]]; then
  fail "当前项目缺少 linux/setup-kylin-gateway.sh；启动入口不会自动执行 Git。请把开发机生成的 neurobridge-kylin-offline-update.run 传到项目根目录，执行 bash neurobridge-kylin-offline-update.run，更新完成后会自动打开菜单。"
fi

printf '正在打开 NeuroBridge 银河麒麟数字菜单……\n'
exec bash "$assistant"
