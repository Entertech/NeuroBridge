#!/usr/bin/env bash
# Source this helper; values are read as data, never evaluated as commands.
nb_info_value() {
  local file=$1 key=$2 value=
  if [[ -f $file && ! -L $file ]]; then
    value=$(awk -v key="$key" 'index($0, key "=") == 1 { print substr($0, length(key) + 2); exit }' "$file" 2>/dev/null || true)
  fi
  printf '%s\n' "${value:-unknown}"
}

nb_application_version() {
  local root=$1 version=
  if [[ -z $root ]]; then printf 'unknown\n'; return; fi
  if [[ -f $root/neurobridge/version_registry.toml ]]; then
    version=$(awk '
      /^\[application\]/ { inside = 1; next }
      inside && /^\[/ { exit }
      inside && /^version[[:space:]]*=/ { split($0, parts, "\""); print parts[2]; exit }
    ' "$root/neurobridge/version_registry.toml" 2>/dev/null || true)
  fi
  if [[ -n $version ]]; then printf '%s\n' "$version"; else nb_info_value "$root/build-info.txt" application_version; fi
}

nb_source_commit() {
  local root=$1 commit
  if [[ -z $root ]]; then printf 'unknown\n'; return; fi
  commit=$(nb_info_value "$root/build-info.txt" source_commit)
  if [[ $commit == unknown && -e $root/.git ]] && command -v git >/dev/null 2>&1; then
    commit=$(git -C "$root" rev-parse HEAD 2>/dev/null || printf unknown)
  fi
  printf '%s\n' "$commit"
}

nb_write_diagnostic_context() {
  local destination=$1 scope=$2 root=$3 package_root=${4:-} python= python_version=unknown python_bits=unknown
  local ID= NAME= PRETTY_NAME= VERSION= VERSION_ID= KYLIN_RELEASE_ID= BUILD_ID= os_error=
  for python in "$root/runtime/bin/python" "$root/.venv/bin/python"; do
    [[ ! -x $python ]] || break
  done
  if [[ -x $python ]]; then
    python_version=$("$python" -c 'import platform; print(platform.python_version())' 2>/dev/null || true)
    python_version=${python_version:-unknown}
    python_bits=$("$python" -c 'import struct; print(struct.calcsize("P") * 8)' 2>/dev/null || true)
    case $python_bits in 32|64) python_bits="$python_bits-bit";; *) python_bits=unknown;; esac
  fi
  {
    printf 'schemaVersion=1\ndiagnosticScope=%s\ngeneratedAtUtc=%s\n' "$scope" "$(date -u +%FT%TZ)"
    printf 'applicationVersion=%s\nsourceCommit=%s\n' "$(nb_application_version "$root")" "$(nb_source_commit "$root")"
    printf 'versionBasis=application files on disk; service status is recorded separately\n'
    printf 'packageApplicationVersion=%s\npackageSourceCommit=%s\n' "$(nb_application_version "$package_root")" "$(nb_source_commit "$package_root")"
    if [[ -r /etc/os-release ]]; then . /etc/os-release; else os_error='/etc/os-release unavailable'; fi
    printf 'osId=%s\nosName=%s\nosVersion=%s\nosVersionId=%s\nosBuild=%s\n' "${ID:-unknown}" "${PRETTY_NAME:-unknown}" "${VERSION:-${VERSION_ID:-unknown}}" "${VERSION_ID:-unknown}" "${KYLIN_RELEASE_ID:-${BUILD_ID:-unknown}}"
    printf 'kernelVersion=%s\nosArchitecture=%s\nosBits=%s\nshellVersion=%s\n' "$(uname -r)" "$(uname -m)" "$(getconf LONG_BIT 2>/dev/null || printf unknown)" "$BASH_VERSION"
    printf 'libcVersion=%s\n' "$(getconf GNU_LIBC_VERSION 2>/dev/null || printf unknown)"
    printf 'environmentQueryError=%s\n' "$os_error"
    printf 'pythonVersion=%s\npythonArchitecture=%s\n' "$python_version" "$python_bits"
  } > "$destination"
}
