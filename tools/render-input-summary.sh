#!/usr/bin/env bash
# Render the per-target release-input records into one message-shaped block.
#
# Each platform job of the native-inputs workflow writes a record named
# status-<targetId>.txt containing three key=value lines:
#
#   target=<targetId>
#   outcome=<built|blocked|failed>
#   reason=<one line>
#
# "blocked" means the target declared up front that it cannot produce an
# installer: a Windows matrix entry carrying a blocked_reason, or a Kylin target
# whose staged runtime is missing.  "failed" means it tried and did not, which
# is a defect rather than a declared limitation.
#
# The output is deliberately plain text with 【】 section markers: it is meant to
# be pasted into a chat message as-is, so it must not depend on a Markdown
# renderer.  The native-inputs workflow appends it to $GITHUB_STEP_SUMMARY, and
# a future Feishu step can post the same text unchanged.
#
# Usage:  tools/render-input-summary.sh <statuses-dir>
#
# Environment (all optional; omitted lines are left out of the header):
#   GITHUB_RUN_ID, GITHUB_RUN_ATTEMPT, GITHUB_REF_NAME, GITHUB_SHA, SOURCE_REF

set -uo pipefail

statuses=${1:-}
if [ -z "$statuses" ] || [ ! -d "$statuses" ]; then
  printf '没有找到目标记录目录：%s\n' "${statuses:-<未给出>}"
  exit 0
fi

built=()
blocked=()
failed=()

field() {
  # Tolerant on purpose: a Windows-written record may carry a UTF-8 BOM and
  # CRLF endings, and matching the key anywhere in the line survives both.
  grep -oE "$1=.*" "$2" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\r'
}

for record in "$statuses"/*/status-*.txt; do
  [ -f "$record" ] || continue
  target=$(field target "$record")
  [ -n "$target" ] || continue
  outcome=$(field outcome "$record")
  reason=$(field reason "$record")
  case "$outcome" in
    built) built+=("$target") ;;
    blocked) blocked+=("$target — ${reason:0:120}") ;;
    *) failed+=("$target — ${reason:0:200}") ;;
  esac
done

total=$(( ${#built[@]} + ${#blocked[@]} + ${#failed[@]} ))

printf '发布输入构建记录\n'
[ -n "${GITHUB_RUN_ID:-}" ] && printf 'run %s（第 %s 次尝试）\n' "$GITHUB_RUN_ID" "${GITHUB_RUN_ATTEMPT:-1}"
[ -n "${GITHUB_REF_NAME:-}" ] && printf '分支 %s · commit %s\n' "$GITHUB_REF_NAME" "${GITHUB_SHA:-unknown}"
[ -n "${SOURCE_REF:-}" ] && printf '源码 ref %s\n' "$SOURCE_REF"
printf '\n'

if [ "$total" -eq 0 ]; then
  printf '没有收集到任何目标记录：两个平台任务都没有产出 status 记录。\n'
  exit 0
fi

printf '共 %d 个目标：成功 %d · 声明阻断 %d · 失败 %d\n' \
  "$total" "${#built[@]}" "${#blocked[@]}" "${#failed[@]}"

if [ "${#failed[@]}" -gt 0 ]; then
  printf '\n【失败】\n'
  for line in "${failed[@]}"; do printf -- '- %s\n' "$line"; done
fi

if [ "${#blocked[@]}" -gt 0 ]; then
  printf '\n【声明阻断】\n'
  for line in "${blocked[@]}"; do printf -- '- %s\n' "$line"; done
fi

if [ "${#built[@]}" -gt 0 ]; then
  printf '\n【成功】\n'
  for line in "${built[@]}"; do printf -- '- %s\n' "$line"; done
fi
