#!/usr/bin/env bash
# Diagnose why rpmbuild cannot execute the %install script it writes under
# /var/tmp.  CI shows it as:
#
#   Executing(%install): /bin/sh -e /var/tmp/rpm-tmp.XXXXXX
#   /bin/sh: 0: /var/tmp/rpm-tmp.XXXXXX: 权限不够
#   error: Bad exit status from /var/tmp/rpm-tmp.XXXXXX (%install)
#
# Run this on the Galaxy Kylin runner host as the normal desktop user.
#
# What is already known (2026-10-08):
#   * The same machine built both Kylin RPM targets successfully at 16:54 and
#     failed the same targets at 19:26/19:29 with a byte-for-byte identical
#     KYSEC state, so the KYSEC *settings* are not the differentiator.
#   * The runner is a systemd service that has been up since 15:45, so the same
#     runner process produced both the success and the failure.
#   * The generated spec is byte-identical in %install across those two commits;
#     only %preun/%postun changed, and -bb never runs those.
#   * The temp script the failing run left behind is readable now, mode 0600,
#     owned by admain, with no immutable flag, and its content is the ordinary
#     rpm preamble plus two commands.  So the refusal was a decision taken at
#     the time, not a property of the file.
#   * SELinux is disabled and AppArmor is not installed, so KYSEC is the only
#     module that could have refused it -- and it did stamp security.ksip on
#     exactly those two files.
# This script therefore (a) forensically reads the temp scripts that the
# failing run left behind, (b) looks up the security-module log for the window
# those files bracket, and (c) reruns the *real* spec both from this terminal
# and detached from any graphical session.
#
# The script is read-only with respect to the system.  It writes only under a
# scratch directory in /tmp, never changes KYSEC, never installs or removes a
# package, and never starts or stops the neurobridge service.  The %preun body
# in TEST B uses echo instead of systemctl precisely so it cannot disturb a
# running gateway even if it were executed by mistake.  rpmbuild -bb never runs
# %post/%preun/%postun: they are only packaged as scriptlets.
#
# Usage:  ./linux/diagnose-kylin-rpm-build.sh
#         NEUROBRIDGE_KYLIN_RUNTIME_ROOT=/opt/neurobridge-release-inputs \
#           ./linux/diagnose-kylin-rpm-build.sh

set -uo pipefail

scratch=/tmp/neurobridge-rpm-diagnose
repo=$(cd "$(dirname "$0")/.." && pwd)
runtime_root=${NEUROBRIDGE_KYLIN_RUNTIME_ROOT:-/opt/neurobridge-release-inputs}
runtime=$runtime_root/kylin-server-x86_64-rpm/runtime

a_ok=""
b_ok=""
c_ok=""
d_ok=""
e_ok=""

cleanup() { rm -rf "$scratch"; }
trap cleanup EXIT

section() { printf '\n===== %s =====\n' "$*"; }

# ------------------------------------------------------------------ context

section "环境"
printf 'host        : %s\n' "$(hostname 2>/dev/null || echo unknown)"
printf 'kernel      : %s\n' "$(uname -r)"
printf 'user        : %s\n' "$(id -un)"
printf 'os          : %s\n' "$(grep -E '^PRETTY_NAME=' /etc/os-release 2>/dev/null | cut -d= -f2- | tr -d '"' || echo unknown)"

if ! command -v rpmbuild >/dev/null 2>&1; then
  printf 'rpmbuild    : NOT INSTALLED\n'
  printf '\n结论：本机没有 rpmbuild，无法诊断。\n'
  exit 1
fi
printf 'rpmbuild    : %s\n' "$(rpmbuild --version 2>&1 | head -1)"
printf '/var/tmp    : %s\n' "$(ls -ld /var/tmp 2>&1)"
printf 'df /var/tmp : %s\n' "$(df -h /var/tmp 2>/dev/null | tail -1)"
printf 'df -i       : %s\n' "$(df -i /var/tmp 2>/dev/null | tail -1)"
if command -v findmnt >/dev/null 2>&1; then
  printf 'mount       : %s\n' "$(findmnt -no SOURCE,FSTYPE,OPTIONS /var/tmp 2>/dev/null || echo 'not a separate mount')"
fi
printf 'root mount  : %s\n' "$(findmnt -no SOURCE,FSTYPE,OPTIONS / 2>/dev/null || echo unknown)"
printf 'repo        : %s\n' "$repo"
printf 'runtime dir : %s%s\n' "$runtime" "$([ -x "$runtime/bin/python" ] && echo '' || echo '   <-- 不存在或不可执行')"

section "安全策略现状"
if command -v getstatus >/dev/null 2>&1; then
  getstatus 2>&1 | head -20
else
  echo 'getstatus: not available'
fi
printf 'selinux     : %s\n' "$(command -v getenforce >/dev/null 2>&1 && getenforce 2>&1 || echo 'getenforce not installed')"
printf 'apparmor    : %s\n' "$(command -v aa-status >/dev/null 2>&1 && (aa-status --enabled >/dev/null 2>&1 && echo enabled || echo 'not enabled') || echo 'aa-status not installed')"
printf 'proc attr   : %s\n' "$(cat /proc/self/attr/current 2>/dev/null || echo unavailable)"
sysctl fs.protected_regular fs.protected_fifos 2>/dev/null || true

# The CI build is driven by the self-hosted runner, not by this terminal.  If
# the runner has no session or no controlling terminal, a KYSEC execution
# dialog has nowhere to appear and the request can be refused outright.
section "runner 与当前会话"
ps -eo pid,user,tty,sess,lstart,args 2>/dev/null \
  | grep -iE 'actions-runner|Runner\.Listener' | grep -v grep | head -5 || true
if command -v systemctl >/dev/null 2>&1; then
  systemctl list-units --type=service --all 2>/dev/null | grep -i 'actions.runner' | head -5 || true
fi
if command -v loginctl >/dev/null 2>&1; then
  printf 'loginctl sessions:\n'
  loginctl list-sessions --no-legend 2>/dev/null | head -5 || true
fi
printf 'current shell: tty=%s XDG_SESSION_TYPE=%s DISPLAY=%s\n' \
  "$(tty 2>/dev/null || echo none)" "${XDG_SESSION_TYPE:-none}" "${DISPLAY:-none}"

# A systemd unit can sandbox the build in ways this terminal never sees.  The
# leftovers being visible in the host /var/tmp already rules PrivateTmp out,
# but the rest of the unit is worth recording once.
runner_unit=$(systemctl list-units --type=service --all --no-legend 2>/dev/null \
  | awk '/actions\.runner/ {print $1; exit}')
if [ -n "${runner_unit:-}" ]; then
  section "runner 服务单元属性"
  systemctl cat "$runner_unit" 2>/dev/null | sed 's/^/  /' | head -40
  printf -- '--- 关键属性\n'
  systemctl show "$runner_unit" \
    -p PrivateTmp -p PrivateDevices -p ProtectSystem -p ProtectHome \
    -p NoNewPrivileges -p ReadWritePaths -p InaccessiblePaths -p UMask \
    -p User -p ExecStart 2>/dev/null | sed 's/^/  /'
fi

# ------------------------------------------------------------------ forensics

# The failing run left its own %install script on disk.  Reading it now is the
# cheapest way to learn whether the denial was a property of that file (mode,
# ACL, LSM label) or a transient state of the machine.
section "残留的 rpm-tmp 脚本取证"
leftovers=()
while IFS= read -r f; do leftovers+=("$f"); done < <(compgen -G '/var/tmp/rpm-tmp.*' || true)
if [ "${#leftovers[@]}" -eq 0 ]; then
  echo '(none)'
else
  for f in "${leftovers[@]}"; do
    printf -- '--- %s\n' "$f"
    stat -c '  mode=%A (%a)  uid=%u(%U)  gid=%g(%G)  size=%s  mtime=%y' "$f" 2>&1
    command -v lsattr >/dev/null 2>&1 && printf '  lsattr: %s\n' "$(lsattr "$f" 2>&1 | head -1)"
    command -v getfattr >/dev/null 2>&1 && getfattr -d -m - "$f" 2>&1 | sed 's/^/  /' | head -6
    command -v getfacl >/dev/null 2>&1 && getfacl -p "$f" 2>&1 | grep -vE '^#|^$' | sed 's/^/  /' | head -6
    printf '  读取测试: '
    if head -c 1 "$f" >/dev/null 2>&1; then
      echo '可读（说明当时的拒绝不是文件属性造成的）'
    else
      echo '读取失败 —— 与 CI 里那条「权限不够」一致，这就是可复现的现场'
    fi
    printf -- '  --- 内容（前 45 行）\n'
    head -n 45 "$f" 2>&1 | sed 's/^/  /'
  done
fi

# The files are readable now, so the refusal was a decision taken at the time,
# not a property of the file.  Every LSM records such a decision, so look it up
# in the window the leftover mtimes bracket.  This is the one piece of evidence
# that can name the refusing component instead of guessing at it.
section "失败时刻的安全模块日志"
if [ "${#leftovers[@]}" -gt 0 ]; then
  oldest=9999999999
  newest=0
  for f in "${leftovers[@]}"; do
    m=$(stat -c %Y "$f" 2>/dev/null || echo 0)
    [ "$m" -lt "$oldest" ] && oldest=$m
    [ "$m" -gt "$newest" ] && newest=$m
  done
  t0=$(date -d "@$((oldest - 300))" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo '')
  t1=$(date -d "@$((newest + 300))" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo '')
  printf '时间窗（由残留脚本 mtime 推出）: %s .. %s\n' "$t0" "$t1"
  if command -v journalctl >/dev/null 2>&1 && [ -n "$t0" ]; then
    printf -- '--- journalctl 该窗口内命中 kysec/拒绝 的行\n'
    journalctl --since "$t0" --until "$t1" --no-pager 2>/dev/null \
      | grep -iE 'kysec|ksip|权限|denied|refus|拒绝' | head -40 \
      || echo '  (无匹配)'
  fi
  if dmesg -T >/dev/null 2>&1; then
    printf -- '--- dmesg 中残留的 kysec 记录\n'
    dmesg -T 2>/dev/null | grep -iE 'kysec|ksip' | tail -20 || echo '  (无匹配)'
  else
    printf -- '--- dmesg 需要权限，跳过（可用 sudo dmesg -T | grep -i kysec 单独看）\n'
  fi
  printf -- '--- /var/log 下与 kysec 相关的日志文件\n'
  grep -rli kysec /var/log 2>/dev/null | head -10 || echo '  (无匹配)'
  for logfile in /var/log/kysec.log /var/log/kysec/kysec.log /var/log/messages /var/log/secure; do
    [ -f "$logfile" ] || continue
    printf -- '--- %s 中该窗口附近的行\n' "$logfile"
    grep -iE 'kysec|ksip' "$logfile" 2>/dev/null | tail -20 || echo '  (无匹配)'
  done
else
  echo '没有残留脚本，无法推出时间窗。'
fi

# Is security.ksip a "this file was refused" marker, or just a generic label on
# anything created under this boot?  Comparing a file created here with one the
# runner created answers it without guessing.
section "security.ksip 对比"
if command -v getfattr >/dev/null 2>&1; then
  printf -- '--- 本终端新建的文件\n'
  : > "$scratch/ksip-user"
  getfattr -d -m - "$scratch/ksip-user" 2>&1 | sed 's/^/  /'
  printf -- '--- runner 检出目录里的文件\n'
  wf=$(find "$HOME/actions-runner/_work" -maxdepth 3 -type f 2>/dev/null | head -1)
  if [ -n "$wf" ]; then
    printf '  %s\n' "$wf"
    getfattr -d -m - "$wf" 2>&1 | sed 's/^/  /'
  else
    echo '  (没找到，跳过)'
  fi
else
  echo 'getfattr 不可用，跳过。'
fi

# ----------------------------------------------------------------- fixtures

mkdir -p "$scratch/rpmbuild"/{SPECS,SOURCES,BUILD,BUILDROOT,RPMS,SRPMS}
mkdir -p "$scratch/rpmbuild-d"/{SPECS,SOURCES,BUILD,BUILDROOT,RPMS,SRPMS}

# Control case: an ordinary single-command %install.
cat > "$scratch/rpmbuild/SPECS/a.spec" <<'SPEC'
Name: nbd-a
Version: 1.0
Release: 1
Summary: minimal control case
License: Proprietary
BuildArch: x86_64

%description
Minimal spec with a plain %install section.

%install
mkdir -p %{buildroot}/opt/nbd-a
echo hi > %{buildroot}/opt/nbd-a/hello.txt

%files
/opt/nbd-a/hello.txt
SPEC

# Mirrors the shape tools/build-native-package.py emits for %preun today: an
# `if` block wrapping multi-line shell function definitions.  systemctl calls
# are replaced by echo so this spec can never disturb a real service.
cat > "$scratch/rpmbuild/SPECS/b.spec" <<'SPEC'
Name: nbd-b
Version: 1.0
Release: 1
Summary: multi-line preun control case
License: Proprietary
BuildArch: x86_64

%description
Spec whose %preun wraps multi-line function definitions in an if block.

%install
mkdir -p %{buildroot}/opt/nbd-b
echo hi > %{buildroot}/opt/nbd-b/hello.txt

%preun
if [ "$1" -eq 0 ]; then
  unit=/etc/systemd/system/neurobridge.service
  unit_is_ours() {
    [ -f "$unit" ] || return 1
    grep -q -- 'ExecStart=/opt/neurobridge/runtime/bin/python' "$unit" 2>/dev/null
  }
  stop_our_unit() {
    unit_is_ours || return 0
    echo "would stop neurobridge.service"
    attempts=0
    while [ "$attempts" -lt 10 ]; do
      attempts=$((attempts + 1))
      break
    done
  }
  stop_our_unit
fi

%files
/opt/nbd-b/hello.txt
SPEC

# Generate the spec exactly as the repository does today, so the probes below
# exercise the real %install/%post/%preun/%postun text rather than a hand copy.
# It needs the pinned Python 3.11 because tools/release_pipeline.py reads TOML
# through tomllib; fall back to the system python only for a clear message.
generate_real_spec() {
  local topdir=$1 py=""
  for candidate in "$runtime/bin/python" /opt/neurobridge-release-inputs/kylin-server-x86_64-rpm/runtime/bin/python; do
    [ -x "$candidate" ] && { py=$candidate; break; }
  done
  if [ -z "$py" ]; then
    printf '  无法生成真实 spec：没有找到 $runtime/bin/python\n'
    return 1
  fi
  mkdir -p "$topdir/SOURCES/payload/nested"
  printf 'diagnostic payload\n' > "$topdir/SOURCES/payload/README.txt"
  printf 'nested\n' > "$topdir/SOURCES/payload/nested/file.txt"
  "$py" - "$repo" "$topdir" <<'PY'
import importlib.util, pathlib, sys
repo, topdir = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
loader = importlib.util.spec_from_file_location("bnp", repo / "tools/build-native-package.py")
module = importlib.util.module_from_spec(loader)
sys.modules["bnp"] = module
loader.loader.exec_module(module)
module.rpm_spec({"edition": "server", "architecture": "x86_64"}, topdir)
print("  已用仓库当前代码生成 spec:", topdir / "SPECS/neurobridge.spec")
PY
}

run_case() {
  local label=$1 spec=$2 rc=0
  section "$label"
  rpmbuild -bb --target x86_64 --define "_topdir $scratch/rpmbuild" "$spec" 2>&1
  rc=$?
  printf '%s -> exit=%d\n' "$label" "$rc"
  return $rc
}

# Any refusal by an LSM leaves a kernel record; capture it while it is fresh.
capture_kernel() {
  section "内核 / 安全模块拒绝记录"
  if dmesg >/dev/null 2>&1; then
    dmesg 2>/dev/null | tail -n 30 | sed 's/^/  /'
  else
    echo '  dmesg: 需要权限，跳过'
  fi
  if command -v journalctl >/dev/null 2>&1; then
    journalctl -k -n 30 --no-pager 2>/dev/null | sed 's/^/  /' || true
  fi
}

# -------------------------------------------------------------------- cases

run_case "TEST A  最小 spec（普通 %install）" "$scratch/rpmbuild/SPECS/a.spec" && a_ok=1 || a_ok=0
run_case "TEST B  带多行 %preun 的 spec" "$scratch/rpmbuild/SPECS/b.spec" && b_ok=1 || b_ok=0

section "TEST C  真实 spec（仓库当前代码生成）+ 极小 payload，前台会话"
if generate_real_spec "$scratch/rpmbuild"; then
  rpmbuild -bb --target x86_64 --define "_topdir $scratch/rpmbuild" \
    "$scratch/rpmbuild/SPECS/neurobridge.spec" 2>&1
  c_rc=$?
  printf 'TEST C  -> exit=%d\n' "$c_rc"
  [ "$c_rc" -eq 0 ] && c_ok=1 || c_ok=0
else
  c_ok=skip
fi

# TEST D is the discriminator: the CI build runs under the self-hosted runner,
# which has no controlling terminal and usually no graphical session, while a
# manual build inherits both.  setsid detaches the probe the same way.
section "TEST D  同一个真实 spec，脱离会话（setsid，无控制终端）"
if [ "$c_ok" = skip ]; then
  echo 'TEST C 未能生成 spec，跳过'
  d_ok=skip
else
  generate_real_spec "$scratch/rpmbuild-d" >/dev/null
  cat > "$scratch/detached.sh" <<EOF
#!/usr/bin/env bash
exec rpmbuild -bb --target x86_64 --define "_topdir $scratch/rpmbuild-d" "$scratch/rpmbuild-d/SPECS/neurobridge.spec"
EOF
  if setsid --wait true >/dev/null 2>&1; then
    setsid --wait bash "$scratch/detached.sh" </dev/null > "$scratch/detached.log" 2>&1
  else
    setsid bash "$scratch/detached.sh" </dev/null > "$scratch/detached.log" 2>&1
  fi
  d_rc=$?
  cat "$scratch/detached.log"
  printf 'TEST D  -> exit=%d\n' "$d_rc"
  [ "$d_rc" -eq 0 ] && d_ok=1 || d_ok=0
fi

# Only meaningful once a failure is in hand: moving the script out of /var/tmp
# is the candidate fix that does not require loosening any security policy.
section "TEST E  真实 spec，_tmppath 改到 \$HOME/rpm-tmp"
if [ "$c_ok" = 0 ] || [ "$d_ok" = 0 ]; then
  mkdir -p "$HOME/rpm-tmp"
  rpmbuild -bb --target x86_64 --define "_topdir $scratch/rpmbuild" \
    --define "_tmppath $HOME/rpm-tmp" "$scratch/rpmbuild/SPECS/neurobridge.spec" 2>&1
  e_rc=$?
  printf 'TEST E  -> exit=%d\n' "$e_rc"
  [ "$e_rc" -eq 0 ] && e_ok=1 || e_ok=0
else
  echo '前台与脱离会话两种跑法都通过了，暂不需要重定位试验。'
  e_ok=skip
fi

if [ "$a_ok" = 0 ] || [ "$b_ok" = 0 ] || [ "$c_ok" = 0 ] || [ "$d_ok" = 0 ]; then
  capture_kernel
fi

# ------------------------------------------------------------------ verdict

section "结论"
if [ "$a_ok" = 0 ]; then
  cat <<'MSG'
最小 spec 就失败：rpmbuild 无法执行自己写在 /var/tmp 的 %install 脚本。
这是本机 rpmbuild 环境问题，与 NeuroBridge 的 spec 内容无关。
可选处理：
  1. 给 rpmbuild 换一个临时目录（不依赖放宽安全策略）：
       mkdir -p ~/rpm-tmp
       rpmbuild -bb --define "_tmppath $HOME/rpm-tmp" ...
     确认可行后我把它固化进 tools/build-native-package.py。
  2. 或修 rpmbuild 与 KYSEC 执行控制的交互。
请把上面的完整输出发回再决定。
MSG
elif [ "$b_ok" = 0 ]; then
  cat <<'MSG'
最小 spec 通过，但带多行 %preun 的 spec 失败：触发点在 spec 生成。
请把上面的完整输出发回，我改 tools/build-native-package.py 的段生成方式。
MSG
elif [ "$c_ok" = 1 ] && [ "$d_ok" = 1 ]; then
  cat <<'MSG'
本机四种跑法全部通过：最小 spec、带多行 %preun 的 spec、以及仓库当前代码生成
的真实 spec —— 前台和脱离会话都一样成功。

配合已知事实，可以排除的东西已经很多了：
  * 机器、rpmbuild、/var/tmp 可用（TEST A）；
  * spec 文本无问题，%install 与成功轮逐字节相同（TEST C）；
  * 会话 / 图形上下文不是触发条件（TEST D）；
  * 残留脚本内容完全正常：#!/bin/sh + rpm 前导 + 两行命令 + brp-*，且现在可读；
  * SELinux 已关、AppArmor 未装，唯一的 LSM 是 KYSEC。

剩下的差别只有两个：
  1. 真正的 payload（约 378 MB）参与时才会触发；
  2. KYSEC 执行控制当时做了一个「拒绝」的决定 —— 残留文件上的
     security.ksip 扩展属性说明 KYSEC 确实评估过这两个文件。

**请重点看上面「失败时刻的安全模块日志」那一段。** 它决定了往哪边修：
  * 若日志里出现 kysec 的拒绝记录，就是 KYSEC 拦的，改法是让 rpmbuild 不再
    从 /var/tmp 执行脚本（--define "_tmppath <目录>"），或在 runner 侧放行；
  * 若日志里干干净净，那就是瞬时状态或 payload 规模相关，直接复跑一次
    CI（platforms=available）即可判定：这次成功说明是瞬时，再次失败则与
    payload 或 runner 上下文相关。

另外请回答一个问题：**这次跑 v2 的时候，屏幕上有弹出 KYSEC 的权限框吗？**
之前 19:26 那次你说弹过框 —— 如果执行控制需要人工点「允许」，而那次没人点，
就完全能解释「16:54 成功、19:26 失败，中间什么都没改」。

请把完整输出发回，我据此改 tools/build-native-package.py。
MSG
elif [ "$c_ok" = 1 ] && [ "$d_ok" = 0 ]; then
  cat <<'MSG'
前台通过、脱离会话失败 —— 触发条件已经定位到「会话 / 图形上下文」。

CI 的构建跑在自托管 runner 里，它没有控制终端、通常也没有图形会话；而手工
构建是在桌面终端里跑的。KYSEC 的执行控制需要弹框确认，在无图形会话时无处
弹框，于是直接拒绝，报的就是「权限不够」。

下一步二选一（我可以直接把选中的那条固化进仓库）：
  1. 让 rpmbuild 不再依赖 /var/tmp 的可执行脚本：
       tools/build-native-package.py 里给 rpmbuild 加 --define "_tmppath <目录>"
     上面的 TEST E 就是验证这条路。
  2. 调整 runner 侧的 KYSEC 策略，使 runner 用户执行 /var/tmp 下的临时脚本
     不需要弹框（linux/setup-kylin-release-runner.sh 里补一条并写进文档）。

请把上面的完整输出发回，我按结果改代码。
MSG
else
  cat <<'MSG'
前台用真实 spec 就复现了失败 —— 现场就在这台机器上，可以直接定位。

请看上面 TEST C 的输出与「内核 / 安全模块拒绝记录」：
  * 若 dmesg/journalctl 里出现 kysec/avc 记录，那就是拒绝方与原因；
  * 若 TEST E（_tmppath 改到 $HOME/rpm-tmp）通过，则改用 _tmppath 就是修复。

请把完整输出发回，我据此改 tools/build-native-package.py。
MSG
fi
