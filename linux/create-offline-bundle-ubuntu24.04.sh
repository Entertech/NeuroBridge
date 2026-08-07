#!/usr/bin/env bash
# Create a self-contained Ubuntu 24.04 amd64 deployment bundle on a connected build host.
set -euo pipefail

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

[[ ${EUID} -ne 0 ]] || fail "Run as an unprivileged release builder, not root."
[[ $(uname -m) == "x86_64" ]] || fail "Offline bundle creation supports Ubuntu 24.04 x86_64 only."
[[ -r /etc/os-release ]] || fail "Cannot identify the operating system."
. /etc/os-release
[[ ${ID:-} == "ubuntu" && ${VERSION_ID:-} == "24.04" ]] || fail "Build the bundle on Ubuntu 24.04."
[[ $# -eq 1 ]] || fail "Usage: $0 /absolute/output-directory"

output_dir=$1
case "$output_dir" in /*) ;; *) fail "Output directory must be absolute.";; esac
[[ ! -e "$output_dir" ]] || fail "Refusing to overwrite existing output directory: $output_dir"
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
for command in git python3 pip3 apt-get apt-cache sha256sum tar; do
  command -v "$command" >/dev/null 2>&1 || fail "Missing build-host command: $command"
done

readarray -t locked < <(python3 - "$repo_root/sdk.lock" <<'PY'
import sys
import tomllib

with open(sys.argv[1], "rb") as source:
    lock = tomllib.load(source)
sdk = lock["affective_algorithm_sdk"]
build = sdk["build"]
print(sdk["repository"])
print(sdk["commit"])
print(build["numcpp_repository"])
print(build["numcpp_commit"])
PY
)
sdk_repo=${locked[0]}
sdk_commit=${locked[1]}
numcpp_repo=${locked[2]}
numcpp_commit=${locked[3]}

workspace=$(mktemp -d)
trap 'rm -rf "$workspace"' EXIT
mkdir -p "$output_dir/sdk" "$output_dir/wheels" "$output_dir/debs"

archive_exact() {
  local name=$1 repository=$2 commit=$3 directory=$4
  local checkout="$workspace/$name"
  git clone --quiet "$repository" "$checkout"
  git -C "$checkout" checkout --detach --quiet "$commit"
  [[ $(git -C "$checkout" rev-parse HEAD) == "$commit" ]] || fail "$name did not resolve to the locked commit"
  git -C "$checkout" archive --format=tar --prefix="$directory/" "$commit" > "$output_dir/sdk/$name.tar"
}

archive_exact affective "$sdk_repo" "$sdk_commit" AffectiveCloud-Algorithm-SDK
archive_exact numcpp "$numcpp_repo" "$numcpp_commit" NumCpp
python3 -m pip download --dest "$output_dir/wheels" -r "$repo_root/requirements.lock"

browser_package=chromium
if ! apt-cache show "$browser_package" >/dev/null 2>&1; then
  browser_package=chromium-browser
fi
root_packages=(python3 python3-venv bluez dnsmasq rsync pandoc "$browser_package" fonts-noto-cjk build-essential cmake libeigen3-dev)
mapfile -t package_closure < <(
  { printf '%s\n' "${root_packages[@]}"; apt-cache depends --recurse --no-recommends --no-suggests --no-conflicts --no-breaks --no-replaces --no-enhances "${root_packages[@]}"; } \
    | sed -n -E 's/^[[:space:]]*(Pre)?Depends:[[:space:]]*([^[:space:]]+).*$/\2/p; /^[a-z0-9][a-z0-9+.-]*(:[a-z0-9]+)?$/p' \
    | grep -v '^<' \
    | sort -u
)
(( ${#package_closure[@]} > 0 )) || fail "Cannot resolve the Ubuntu package closure. Run apt-get update on the build host."
(
  cd "$output_dir/debs"
  apt-get download "${package_closure[@]}"
)

affective_sha=$(sha256sum "$output_dir/sdk/affective.tar" | awk '{print $1}')
numcpp_sha=$(sha256sum "$output_dir/sdk/numcpp.tar" | awk '{print $1}')
cat > "$output_dir/manifest.toml" <<EOF
[bundle]
format_version = 1
ubuntu_version = "24.04"
architecture = "amd64"

[archives.affective]
path = "sdk/affective.tar"
commit = "$sdk_commit"
sha256 = "$affective_sha"

[archives.numcpp]
path = "sdk/numcpp.tar"
commit = "$numcpp_commit"
sha256 = "$numcpp_sha"
EOF

echo "Offline bundle created: $output_dir"
echo "Transfer this directory together with the reviewed NeuroBridge source to the offline Ubuntu 24.04 gateway."
