#!/usr/bin/env bash
# Diagnose why rpmbuild cannot execute the %install script it writes under
# /var/tmp.  CI shows it as:
#
#   /bin/sh: 0: /var/tmp/rpm-tmp.XXXXXX: 权限不够
#   error: Bad exit status from /var/tmp/rpm-tmp.XXXXXX (%install)
#
# Run this on the Galaxy Kylin runner host as the normal desktop user.
#
# The script is read-only with respect to the system.  It writes only under a
# scratch directory in /tmp, never changes KYSEC, never installs or removes a
# package, and never starts or stops the neurobridge service.  The %preun body
# in TEST B uses echo instead of systemctl precisely so it cannot disturb a
# running gateway even if it were executed by mistake.
#
# Usage:  ./linux/diagnose-kylin-rpm-build.sh

set -uo pipefail

scratch=/tmp/neurobridge-rpm-diagnose
a_ok=""
b_ok=""

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
if command -v findmnt >/dev/null 2>&1; then
  printf 'mount       : %s\n' "$(findmnt -no SOURCE,FSTYPE,OPTIONS /var/tmp 2>/dev/null || echo 'not a separate mount')"
fi

if command -v getstatus >/dev/null 2>&1; then
  section "KYSEC 状态"
  getstatus 2>&1 | head -20
fi

section "/var/tmp 下残留的 rpm-tmp 文件"
if compgen -G '/var/tmp/rpm-tmp.*' >/dev/null 2>&1; then
  ls -la /var/tmp/rpm-tmp.* 2>&1 | head -10
else
  echo "(none)"
fi

# ----------------------------------------------------------------- fixtures

mkdir -p "$scratch/rpmbuild"/{SPECS,SOURCES,BUILD,BUILDROOT,RPMS,SRPMS}

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

run_case() {
  local label=$1 spec=$2 rc=0
  section "$label"
  rpmbuild -bb --define "_topdir $scratch/rpmbuild" "$spec" 2>&1
  rc=$?
  printf '%s -> exit=%d\n' "$label" "$rc"
  return $rc
}

# -------------------------------------------------------------------- cases

run_case "TEST A  最小 spec（普通 %install）" "$scratch/rpmbuild/SPECS/a.spec" && a_ok=1 || a_ok=0
run_case "TEST B  带多行 %preun 的 spec" "$scratch/rpmbuild/SPECS/b.spec" && b_ok=1 || b_ok=0

# ------------------------------------------------------------------ verdict

section "结论"
if [ "$a_ok" = 1 ] && [ "$b_ok" = 1 ]; then
  cat <<'MSG'
两个测试都通过：这台机器的 rpmbuild 能正常执行自己的 %install 临时脚本。
失败因此与完整 spec 或 payload 有关，不是机器环境。
请把上面的完整输出发回，下一步切分 payload 与 spec。
MSG
elif [ "$a_ok" = 0 ]; then
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
else
  cat <<'MSG'
最小 spec 通过，但带多行 %preun 的 spec 失败：触发点在 spec 生成。
请把上面的完整输出发回，我改 tools/build-native-package.py 的段生成方式。
MSG
fi
