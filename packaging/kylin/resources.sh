#!/usr/bin/env bash
# Resolve the five locked build resources before a Python interpreter exists.
# Source after platform.sh; callers own temporary directories and cleanup.
nb_resource_index() {
  case $1 in
    python) NB_RESOURCE_INDEX=0;; cmake) NB_RESOURCE_INDEX=1;;
    eigen) NB_RESOURCE_INDEX=2;; pyserial) NB_RESOURCE_INDEX=3;;
    websockets) NB_RESOURCE_INDEX=4;; *) return 1;;
  esac
}

nb_parse_resources() {
  NB_RESOURCE_NAMES=(python cmake eigen pyserial websockets)
  NB_RESOURCE_SOURCES=('' '' '' '' '')
  local seen=(0 0 0 0 0) name index value
  while [[ $# -gt 0 ]]; do
    name=$1
    nb_resource_index "$name" || { nb_die 'unknown_resource (python/cmake/eigen/pyserial/websockets expected)'; return 1; }
    index=$NB_RESOURCE_INDEX
    [[ ${seen[$index]} == 0 ]] || { nb_die "duplicate_resource name=$name"; return 1; }
    seen[$index]=1
    shift
    value=
    if [[ $# -gt 0 ]] && ! nb_resource_index "$1"; then
      value=$1
      shift
      [[ -n $value && $value != -* && $value != *$'\n'* && $value != *$'\r'* && $value != *$'\t'* ]] \
        || { nb_die "invalid_resource_source name=$name"; return 1; }
      case $value in
        https://*)
          local authority=${value#https://}
          authority=${authority%%[/?#]*}
          [[ -n $authority && $authority != *@* ]] \
            || { nb_die "invalid_download_url name=$name"; return 1; };;
        *://*) nb_die "download_requires_https name=$name"; return 1;;
        *)
          # Resolve relative paths before entering any disposable build tree.
          [[ -f $value && ! -L $value ]] \
            || { nb_die "missing_or_unsafe_resource name=$name"; return 1; }
          value=$(cd "$(dirname "$value")" && pwd -P)/$(basename "$value") || return 1;;
      esac
    fi
    NB_RESOURCE_SOURCES[$index]=$value
  done
}

nb_resource_key() {
  case $1 in
    python) NB_RESOURCE_KEY=$NB_PYTHON_INPUT;;
    cmake) NB_RESOURCE_KEY=$NB_CMAKE_INPUT;;
    *) NB_RESOURCE_KEY=$1;;
  esac
}

nb_resource_bundled_path() {
  local tree=$1 key=$2 filename=$3
  case $key in
    python_x86_64) NB_RESOURCE_PATH=$tree/python-runtime/$filename;;
    pyserial|websockets) NB_RESOURCE_PATH=$tree/wheelhouse/$filename;;
    *) NB_RESOURCE_PATH=$tree/packaging/kylin/offline/$filename;;
  esac
}

nb_list_resources() {
  local tree=$1 name key filename bytes status file actual expected
  printf 'resource\tinput\tfile\tbytes\tstatus\n'
  for name in "${NB_RESOURCE_NAMES[@]}"; do
    nb_resource_key "$name"
    key=$NB_RESOURCE_KEY
    filename=$(nb_lock_value "artifacts.$key" filename) || return 1
    nb_resource_bundled_path "$tree" "$key" "$filename"
    file=$NB_RESOURCE_PATH
    bytes=0 status=missing
    if [[ -f $file && ! -L $file ]]; then
      bytes=$(wc -c < "$file")
      actual=$(sha256sum "$file" | awk '{print $1}') || return 1
      expected=$(nb_lock_value "artifacts.$key" sha256) || return 1
      status=checksum_mismatch
      [[ $actual != "$expected" ]] || status=verified
    fi
    printf '%s\t%s\t%s\t%s\t%s\n' "$name" "$key" "$filename" "${bytes// /}" "$status"
  done
}

nb_prepare_resources() {
  local tree=$1 destination=$2 prepared=${3:-} index name key filename source mode temporary
  # Report every missing default before any download, compilation or package
  # configure. Explicit URLs/files are independent of missing bundled inputs.
  if [[ -z $prepared ]]; then
    local missing=0
    for index in 0 1 2 3 4; do
      [[ -z ${NB_RESOURCE_SOURCES[$index]} ]] || continue
      name=${NB_RESOURCE_NAMES[$index]}
      nb_resource_key "$name"
      key=$NB_RESOURCE_KEY
      filename=$(nb_lock_value "artifacts.$key" filename) || return 1
      nb_resource_bundled_path "$tree" "$key" "$filename"
      if [[ ! -e $NB_RESOURCE_PATH && ! -L $NB_RESOURCE_PATH ]]; then
        missing=1
        nb_event resource_missing "name=$name key=$key file=$filename architecture=$NB_ARCH mode=bundled reason=not_in_package source_unspecified=true"
        printf 'ERROR: 引导包内缺少离线资源 %s（%s，架构 %s）。请在原安装命令中增加：%s <HTTPS下载URL或本机文件路径>；需要文件 %s。未自动联网或切换来源。\n' \
          "$name" "$key" "$NB_ARCH" "$name" "$filename" >&2
      fi
    done
    [[ $missing -eq 0 ]] || return 1
  fi
  mkdir -p "$destination" || return 1
  for index in 0 1 2 3 4; do
    name=${NB_RESOURCE_NAMES[$index]}
    nb_resource_key "$name"
    key=$NB_RESOURCE_KEY
    filename=$(nb_lock_value "artifacts.$key" filename) || return 1
    [[ $filename =~ ^[A-Za-z0-9+_.-]+$ ]] || { nb_die "unsafe_resource_filename name=$name"; return 1; }
    source=${NB_RESOURCE_SOURCES[$index]}
    mode=bundled
    if [[ -n $prepared ]]; then
      [[ -d $prepared && ! -L $prepared ]] || { nb_die 'unsafe_prepared_resources'; return 1; }
      [[ -z $source ]] || { nb_die 'conflicting_resource_sources'; return 1; }
      source=$prepared/$filename
      mode=prepared
    elif [[ -z $source ]]; then
      nb_resource_bundled_path "$tree" "$key" "$filename"
      source=$NB_RESOURCE_PATH
    elif [[ $source == https://* ]]; then
      mode=download
    else
      mode=local
    fi
    # URLs can contain signed query parameters; never put them in diagnostics.
    nb_event resource_select "name=$name key=$key mode=$mode file=$filename architecture=$NB_ARCH"
    temporary=$destination/.$filename.partial
    if [[ $mode == download ]]; then
      command -v curl >/dev/null || { nb_die "curl_required name=$name"; return 1; }
      if ! curl --silent --fail --location --proto '=https' --proto-redir '=https' \
          --retry 3 --connect-timeout 20 --max-time 300 --output "$temporary" "$source"; then
        rm -f -- "$temporary"
        nb_die "resource_download_failed name=$name"; return 1
      fi
    else
      nb_verify_input "$key" "$source" || return 1
      cp -- "$source" "$temporary" || return 1
    fi
    if ! nb_verify_input "$key" "$temporary"; then
      rm -f -- "$temporary"
      return 1
    fi
    mv -- "$temporary" "$destination/$filename" || return 1
  done
}

nb_stage_resources() {
  local prepared=$1 tree=$2 name key filename
  # Only the two approved wheels enter pip's find-links directory.
  rm -rf -- "$tree/wheelhouse" || return 1
  mkdir -p "$tree/wheelhouse" "$tree/python-runtime" "$tree/packaging/kylin/offline" || return 1
  for name in "${NB_RESOURCE_NAMES[@]}"; do
    nb_resource_key "$name"
    key=$NB_RESOURCE_KEY
    filename=$(nb_lock_value "artifacts.$key" filename) || return 1
    nb_resource_bundled_path "$tree" "$key" "$filename"
    cp -- "$prepared/$filename" "$NB_RESOURCE_PATH" || return 1
  done
}
