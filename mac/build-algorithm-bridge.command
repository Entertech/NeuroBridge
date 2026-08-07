#!/usr/bin/env bash
# Build the local macOS bridge against the exact C++ algorithm SDK revision.
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

sdk_repository="https://github.com/Entertech/AffectiveCloud-Algorithm-SDK.git"
sdk_commit="5623c5a1a43c6b04c3907d84ba2b9b86f9b010a2"
numcpp_repository="https://github.com/dpilger26/NumCpp.git"
numcpp_tag="Version_2.11.0"
numcpp_commit="cd324a7f09ec1ec4ba2ef2d7bac4861c9a84ee47"
runtime_root="/tmp/neurobridge-affective-runtime"
sdk_dir="$runtime_root/AffectiveCloud-Algorithm-SDK"
numcpp_dir="$runtime_root/NumCpp"
numcpp_build_dir="$runtime_root/numcpp-build"
numcpp_prefix="$runtime_root/numcpp-prefix"
bridge_build_dir="$runtime_root/bridge-build"
bridge_path="$runtime_root/affective_bridge"
repo_root="$(cd "$(dirname "$0")/.." && pwd)"

if ! command -v brew >/dev/null 2>&1; then
  echo "需要 Homebrew 才能构建本地算法 bridge。请先安装 Homebrew 后重试。"
  exit 1
fi
if ! command -v git >/dev/null 2>&1; then
  echo "需要 git 才能构建本地算法 bridge。请先安装 Xcode Command Line Tools 后重试。"
  exit 1
fi

missing_formulae=()
if ! command -v cmake >/dev/null 2>&1; then
  missing_formulae+=(cmake)
fi
if ! brew list --versions eigen >/dev/null 2>&1; then
  missing_formulae+=(eigen)
fi
if (( ${#missing_formulae[@]} > 0 )); then
  echo "正在通过 Homebrew 安装算法构建依赖：${missing_formulae[*]}"
  brew install "${missing_formulae[@]}"
fi

mkdir -p "$runtime_root"
if [[ ! -d "$sdk_dir/.git" ]]; then
  git clone --quiet "$sdk_repository" "$sdk_dir"
fi
if ! git -C "$sdk_dir" cat-file -e "$sdk_commit^{commit}" 2>/dev/null; then
  git -C "$sdk_dir" fetch --tags --quiet
fi
git -C "$sdk_dir" checkout --detach --quiet "$sdk_commit"

if [[ ! -d "$numcpp_dir/.git" ]]; then
  git clone --quiet --branch "$numcpp_tag" "$numcpp_repository" "$numcpp_dir"
fi
if ! git -C "$numcpp_dir" cat-file -e "$numcpp_commit^{commit}" 2>/dev/null; then
  git -C "$numcpp_dir" fetch --tags --quiet
fi
git -C "$numcpp_dir" checkout --detach --quiet "$numcpp_commit"

eigen_prefix="$(brew --prefix eigen)"
cmake -Wno-dev -S "$numcpp_dir" -B "$numcpp_build_dir" \
  -DCMAKE_BUILD_TYPE=Release \
  -DNUMCPP_NO_USE_BOOST=ON \
  -DCMAKE_INSTALL_PREFIX="$numcpp_prefix"
cmake --build "$numcpp_build_dir" --parallel 2
cmake --install "$numcpp_build_dir" >/dev/null
cmake -Wno-dev -S "$repo_root/mac/algorithm_bridge" -B "$bridge_build_dir" \
  -DCMAKE_BUILD_TYPE=Release \
  -DAFFECTIVE_SDK_SOURCE_DIR="$sdk_dir" \
  -DNUMCPP_NO_USE_BOOST=ON \
  -DCMAKE_PREFIX_PATH="$eigen_prefix;$numcpp_prefix"
cmake --build "$bridge_build_dir" --parallel 2
cp "$bridge_build_dir/bin/neurobridge_affective_bridge" "$bridge_path"
chmod +x "$bridge_path"
echo "算法 bridge 已构建：$bridge_path"
