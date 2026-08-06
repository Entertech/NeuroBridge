#!/usr/bin/env bash
# Fetch exact source revisions for a local, reviewable C++ SDK build. Do not run as root.
set -euo pipefail

target_dir=${1:?usage: $0 /absolute/controlled/sdk-directory}
case "$target_dir" in
  /*) ;;
  *) echo "Target must be an absolute controlled path." >&2; exit 2 ;;
esac

clone_exact() {
  local repo=$1 commit=$2 destination=$3
  if [[ -e "$destination" ]]; then
    echo "Refusing to overwrite existing path: $destination" >&2
    exit 3
  fi
  git clone "$repo" "$destination"
  git -C "$destination" checkout --detach "$commit"
}

mkdir -p "$target_dir"
clone_exact https://github.com/Entertech/Enter-Biomodule-BLE-PC-SDK.git 6870a6187d8cadd0759f8f5d1c5f81a90e2bacbd "$target_dir/Enter-Biomodule-BLE-PC-SDK"
clone_exact https://github.com/Entertech/AffectiveCloud-Algorithm-SDK.git 5623c5a1a43c6b04c3907d84ba2b9b86f9b010a2 "$target_dir/AffectiveCloud-Algorithm-SDK"
echo "Sources are checked out. Follow doc/tech/算法 SDK 接入 POC.md before enabling algorithm.enabled."
