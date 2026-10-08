#!/usr/bin/env bash
# Prepare one Galaxy Kylin machine for the matching native package targets.
# The machine does not need a GitHub login. Source is fetched anonymously
# from the public repository; runner registration uses a one-time token.
set -euo pipefail

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage:
  ./linux/setup-kylin-release-runner.sh prepare
  ./linux/setup-kylin-release-runner.sh register --token <one-time-token>

prepare clones the public repository (if needed), builds the local runtime,
and copies it to the four package targets for this machine's architecture.
It does not log in to GitHub.

register installs the GitHub Actions runner. Create the one-time token on a
machine that already has repository access:

  gh api --method POST repos/Entertech/NeuroBridge/actions/runners/registration-token --jq .token

The token expires quickly. Copy only that value to this machine; do not copy
GitHub credentials.

The current algorithm build supports x86_64 only. Other architectures can
register a runner, but their runtime must be supplied separately.
EOF
}

require_kylin() {
  [[ ${EUID:-$(id -u)} -ne 0 ]] || fail "Run as the normal desktop user. The script uses sudo only for system packages and the runner service."
  [[ -r /etc/os-release ]] || fail "/etc/os-release is unavailable."
  # shellcheck disable=SC1091
  . /etc/os-release
  [[ ${ID,,} == kylin ]] || fail "This helper requires Galaxy Kylin; detected ID=${ID:-unknown}."
}

machine_arch() {
  case $(uname -m) in
    x86_64) printf 'x86_64\n' ;;
    aarch64) printf 'arm64\n' ;;
    loongarch64|mips64el|sw64) uname -m ;;
    *) fail "Unsupported architecture: $(uname -m)." ;;
  esac
}

runner_asset_arch() {
  case $1 in
    x86_64) printf 'x64\n' ;;
    arm64) printf 'arm64\n' ;;
    *) fail "GitHub does not publish an Actions runner package for $1. Use an x86_64 or arm64 runner host." ;;
  esac
}

root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
repo_url=${NEUROBRIDGE_REPO_URL:-https://github.com/Entertech/NeuroBridge.git}
repo_ref=${NEUROBRIDGE_REPO_REF:-codex/workflow-release-packaging}
runtime_root=${NEUROBRIDGE_KYLIN_RUNTIME_ROOT:-/opt/neurobridge-release-inputs}
runner_home=${NEUROBRIDGE_RUNNER_HOME:-"$HOME/actions-runner"}
runner_version=${NEUROBRIDGE_RUNNER_VERSION:-2.338.0}

stage_runtime() {
  local source_root=$1
  local destination=$2
  [[ -x $source_root/bin/python && -d $source_root/lib ]] || fail "Python runtime layout is incomplete: $source_root"
  rm -rf "$destination/python-runtime"
  mkdir -p "$destination/python-runtime" "$destination"
  cp -a "$source_root/." "$destination/python-runtime/"
  ln -sfn python-runtime/bin/python "$destination/python"
}

prepare_runtime() {
  require_kylin
  local arch python_runtime bridge_bin target
  arch=$(machine_arch)
  [[ $arch == x86_64 ]] || fail "The current Kylin runtime build supports x86_64 only; detected $arch."
  [[ -d $root_dir/.git ]] || fail "Run this command from a NeuroBridge checkout."

  if ! command -v git >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y git
  fi
  git -C "$root_dir" remote set-url origin "$repo_url"
  git -C "$root_dir" fetch --depth 1 origin "$repo_ref"
  git -C "$root_dir" checkout --detach FETCH_HEAD

  if ! command -v dpkg-deb >/dev/null 2>&1 || ! command -v rpmbuild >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y dpkg-dev rpm
  fi

  "$root_dir/linux/setup-kylin-python.sh"
  install -d -m 0750 "$root_dir/.runtime/config"
  [[ -f $root_dir/.runtime/config/gateway.toml ]] || cp "$root_dir/config/gateway.toml.example" "$root_dir/.runtime/config/gateway.toml"
  "$root_dir/linux/setup-kylin-algorithm.sh"

  python_runtime=$root_dir/python-runtime/python
  bridge_bin=$root_dir/.runtime/algorithm/neurobridge_affective_bridge
  [[ -x $python_runtime/bin/python3 && -x $bridge_bin ]] || fail "Python runtime or algorithm bridge was not produced."

  sudo install -d -o "$USER" -g "$USER" -m 0755 "$runtime_root"
  for edition in server desktop; do
    for format in deb rpm; do
      target="$runtime_root/kylin-$edition-$arch-$format/runtime/bin"
      install -d -m 0755 "$target"
      stage_runtime "$python_runtime" "$target"
      install -m 0755 "$bridge_bin" "$target/neurobridge_affective_bridge"
      [[ -x $target/python && -x $target/neurobridge_affective_bridge ]] || fail "Runtime copy failed: $target"
      "$target/python" -c 'import sys; assert sys.version_info >= (3, 11)' || fail "Staged Python failed: $target"
    done
  done

  printf 'Runtime root: %s\n' "$runtime_root"
  printf 'Architecture label: %s\n' "$arch"
  printf 'On an authenticated machine, set the repository variable:\n'
  printf '  gh variable set NEUROBRIDGE_KYLIN_RUNTIME_ROOT --body %q\n' "$runtime_root"
}

register_runner() {
  require_kylin
  local token=${1:-} arch asset_arch package url
  [[ -n $token ]] || fail "A one-time registration token is required."
  arch=$(machine_arch)
  asset_arch=$(runner_asset_arch "$arch")
  command -v curl >/dev/null 2>&1 || fail "curl is required to download the public runner package."
  command -v tar >/dev/null 2>&1 || fail "tar is required to unpack the runner package."

  if [[ -x $runner_home/svc.sh ]]; then
    sudo "$runner_home/svc.sh" stop || true
    sudo "$runner_home/svc.sh" uninstall || true
  fi
  rm -rf "$runner_home"
  install -d -m 0755 "$runner_home"
  package="$runner_home/actions-runner.tar.gz"
  url="https://github.com/actions/runner/releases/download/v${runner_version}/actions-runner-linux-${asset_arch}-${runner_version}.tar.gz"
  curl --fail --location --retry 3 --output "$package" "$url"
  tar -xzf "$package" -C "$runner_home"
  (
    cd "$runner_home"
    ./config.sh --unattended --replace \
      --url https://github.com/Entertech/NeuroBridge \
      --token "$token" \
      --name "kylin-v10-$arch" \
      --labels "self-hosted,kylin-v10,$arch"
  )
  sudo "$runner_home/svc.sh" install "$USER"
  sudo "$runner_home/svc.sh" start
  printf 'Runner registered for %s.\n' "$arch"
}

command_name=${1:-}
case $command_name in
  -h|--help|help|"")
    usage
    ;;
  prepare)
    [[ $# -eq 1 ]] || fail "prepare does not accept additional arguments."
    prepare_runtime
    ;;
  register)
    shift
    token=
    while [[ $# -gt 0 ]]; do
      case $1 in
        --token)
          [[ $# -ge 2 ]] || fail "--token requires a value."
          token=$2
          shift 2
          ;;
        *)
          fail "Unknown option: $1"
          ;;
      esac
    done
    register_runner "$token"
    ;;
  *)
    fail "Unknown command: $command_name"
    ;;
esac
