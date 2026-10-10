#!/usr/bin/env bash
# Shared host selection and input verification, usable before Python exists.
nb_event() { printf 'EVENT utc=%s phase=%s %s\n' "$(date -u +%FT%TZ)" "$1" "${2:-}" >&2; }
nb_die() { nb_event failure "$*"; return 1; }
nb_lock_value() {
  local section=$1 key=$2 value
  value=$(awk -v wanted="[$section]" -v key="$key" '
    BEGIN { active=(wanted == "[]") }
    /^\[/ { active=($0 == wanted); next }
    active && $1 == key && $2 == "=" { count++; line=$0 }
    END { if(count != 1) exit 1; sub(/^[^=]*=[[:space:]]*/, "", line); print line }
  ' "$NB_INPUT_LOCK") || { nb_die "invalid_lock section=$section key=$key"; return 1; }
  [[ $value == \"*\" && $value != *'$'* && $value != *'`'* && $value != *'\\'* ]] || { nb_die "invalid_literal section=$section key=$key"; return 1; }
  value=${value#\"}; value=${value%\"}
  [[ $value != *'"'* ]] || return 1
  printf '%s\n' "$value"
}
nb_select_platform() {
  local ID= VERSION_ID= PRETTY_NAME= elf magic1 magic2 magic3 magic4 elf_class elf_data expected
  nb_event detect_host "kernel=$(uname -m) bits=$(getconf LONG_BIT 2>/dev/null || printf unknown)"
  [[ -r /etc/os-release ]] || { nb_die 'os_release_unavailable'; return 1; }
  . /etc/os-release
  NB_OS_VERSION=${VERSION_ID:-unknown}
  [[ $(printf '%s' "${ID:-}" | tr '[:upper:]' '[:lower:]') == kylin && $NB_OS_VERSION =~ ^[Vv]?10([.].*)?$ ]] || {
    nb_die "unsupported_os id=${ID:-unknown} version=$NB_OS_VERSION expected=kylin-v10"; return 1;
  }
  NB_RAW_ARCH=$(uname -m)
  NB_BITS=$(getconf LONG_BIT) || return 1
  case $NB_RAW_ARCH in
    x86_64|amd64) NB_ARCH=x86_64;; aarch64|arm64) NB_ARCH=aarch64;;
    loongarch64|loong64) NB_ARCH=loongarch64;; mips64|mips64el) NB_ARCH=mips64el;;
    sw64|sw_64) NB_ARCH=sw64;; i386|i486|i586|i686) NB_ARCH=x86;;
    armv7l|armv8l) NB_ARCH=armhf;; *) nb_die "unsupported_cpu architecture=$NB_RAW_ARCH"; return 1;;
  esac
  elf=$(od -An -tu1 -N6 /bin/sh) || return 1
  read -r magic1 magic2 magic3 magic4 elf_class elf_data <<< "$elf"
  [[ $magic1 == 127 && $magic2 == 69 && $magic3 == 76 && $magic4 == 70 && $elf_data == 1 ]] || {
    nb_die "unsupported_abi architecture=$NB_RAW_ARCH expected=little-endian-ELF class=$elf_class data=$elf_data"; return 1;
  }
  expected=$(nb_lock_value "profiles.$NB_ARCH" bits) || return 1
  [[ $NB_BITS == "$expected" && ( $elf_class == 2 && $NB_BITS == 64 || $elf_class == 1 && $NB_BITS == 32 ) ]] || {
    nb_die "abi_bitness_mismatch architecture=$NB_ARCH expected=$expected detected=$NB_BITS elf_class=$elf_class"; return 1;
  }
  NB_PYTHON_INPUT=$(nb_lock_value "profiles.$NB_ARCH" python) || return 1
  NB_CMAKE_INPUT=$(nb_lock_value "profiles.$NB_ARCH" cmake) || return 1
  NB_EIGEN_LOCK=$(nb_lock_value "profiles.$NB_ARCH" eigen_lock) || return 1
  nb_event selected_profile "os=kylin version=$NB_OS_VERSION cpu=$NB_RAW_ARCH architecture=$NB_ARCH bits=$NB_BITS endian=little python=$NB_PYTHON_INPUT cmake=$NB_CMAKE_INPUT physical_validation=pending"
  export NB_ARCH NB_BITS NB_OS_VERSION NB_EIGEN_LOCK
}
nb_verify_input() {
  local key=$1 file=$2 expected actual
  expected=$(nb_lock_value "artifacts.$key" sha256) || return 1
  [[ $expected =~ ^[0-9a-f]{64}$ && -f $file && ! -L $file ]] || { nb_die "missing_or_unsafe_input key=$key file=$file"; return 1; }
  actual=$(sha256sum "$file" | awk '{print $1}') || return 1
  nb_event verify_input "key=$key file=$(basename "$file") expected=$expected actual=$actual"
  [[ $actual == "$expected" ]] || { nb_die "input_hash_mismatch key=$key"; return 1; }
}
nb_require_compiler() {
  local triplet compiler role
  command -v make >/dev/null || { nb_die 'missing_native_make'; return 1; }
  for role in CC CXX; do
    if [[ $role == CC ]]; then compiler=${CC:-cc}; else compiler=${CXX:-c++}; fi
    command -v "$compiler" >/dev/null || { nb_die "missing_native_compiler role=$role executable=$compiler"; return 1; }
    triplet=$("$compiler" -dumpmachine) || return 1
    case "$NB_ARCH:$triplet" in
      x86_64:x86_64*|aarch64:aarch64*|loongarch64:loongarch64*|mips64el:mips64el*|sw64:sw_64*|sw64:sw64*|x86:i?86*|armhf:arm*gnueabihf*) ;;
      *) nb_die "compiler_target_mismatch role=$role architecture=$NB_ARCH triplet=$triplet"; return 1;;
    esac
    nb_event toolchain "role=$role compiler_triplet=$triplet compiler=$("$compiler" --version | head -n 1) libc=$(getconf GNU_LIBC_VERSION 2>/dev/null || printf unknown)"
  done
}
