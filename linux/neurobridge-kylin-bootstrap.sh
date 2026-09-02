#!/usr/bin/env bash
# Standalone recovery entry for Galaxy Kylin field computers. This one file
# may be copied into an older NeuroBridge project root and run with Bash.
set -u -o pipefail

target_branch="codex/serial-usb-transport"
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

Copy this one file into the root of the NeuroBridge project on the Galaxy
Kylin computer, then run it as the normal desktop user. It can also run from
linux/ in a current checkout.

The bootstrap validates the exact project, repairs root-owned project/Git
files after a yes/no confirmation, obtains codex/serial-usb-transport when an
old checkout lacks the gateway menu, and then opens that numeric menu.

Do not run with sudo. The script requests sudo only for ownership repair; all
Git commands and the gateway run as the normal user.
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
printf 'project=%s\nlog=%s\ntargetBranch=%s\n' "$project_dir" "$bootstrap_log" "$target_branch"

if [[ -e $project_dir/.git/index.lock ]]; then
  printf '发现 .git/index.lock，本文件不会自动删除它。\n' >&2
  printf '先确认没有 Git 进程，再人工执行：rm -- %q\n' "$project_dir/.git/index.lock" >&2
  exit 1
fi

update_target_branch() {
  local current_branch
  current_branch=$(git -C "$project_dir" symbolic-ref --quiet --short HEAD 2>/dev/null || true)
  printf 'currentBranch=%s\n' "${current_branch:-detached}"

  if [[ $current_branch == "$target_branch" ]]; then
    git -C "$project_dir" status --short --branch || return 1
    git -C "$project_dir" pull --ff-only origin "$target_branch"
    return $?
  fi

  printf '当前分支 %s 不包含尚未合入 master 的麒麟串口方案。\n' "${current_branch:-detached}"
  printf '目标分支：%s\n' "$target_branch"
  ask_yes_no "是否切换到目标分支并获取最新代码？" || return 1

  if ! git -C "$project_dir" diff --quiet || ! git -C "$project_dir" diff --cached --quiet; then
    printf '当前存在已跟踪文件修改，为避免覆盖，已停止切换分支。\n' >&2
    git -C "$project_dir" status --short --branch
    return 1
  fi

  git -C "$project_dir" fetch origin \
    "refs/heads/$target_branch:refs/remotes/origin/$target_branch" || return 1
  if git -C "$project_dir" show-ref --verify --quiet "refs/heads/$target_branch"; then
    git -C "$project_dir" checkout "$target_branch" || return 1
    git -C "$project_dir" merge --ff-only "origin/$target_branch" || return 1
  else
    git -C "$project_dir" checkout -b "$target_branch" --track "origin/$target_branch" || return 1
  fi
}

if [[ ! -f $assistant ]]; then
  printf '当前旧代码缺少银河麒麟数字菜单，需要获取目标分支。\n'
  update_target_branch || fail \
    "未能取得目标分支。权限修复日志已保留；确认网络和工作区后重新运行本文件。"
fi

[[ -f $assistant ]] || fail "更新后仍未找到助手：$assistant"
printf '正在打开 NeuroBridge 银河麒麟数字菜单……\n'
exec bash "$assistant"
