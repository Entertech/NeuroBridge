#!/usr/bin/env bash
# Resolve the runtime archive declared by the Kylin bootstrap manifest.
#
# A published archive is downloaded from the manifest URL.  The same archive
# copied onto this machine (for a host that cannot reach the publish location)
# is accepted instead, and a local copy always wins over the network.  Either
# way the bytes are checked against the manifest sha256 before anything is
# unpacked, so a download and a hand-carried file are the same input.
set -euo pipefail

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: fetch-runtime.sh --manifest <manifest.toml> --destination <directory> [--local-archive <file>]

Resolves the runtime archive declared by the manifest into <directory>.
Prints the absolute path of the verified archive on stdout.

--local-archive reuses a copy that is already on this machine and never
touches the network.  Without it, the archive is downloaded from the URL
recorded in the manifest.  Both paths are rejected unless the sha256 matches.
EOF
}

manifest=
destination=
local_archive=
while [[ $# -gt 0 ]]; do
  case $1 in
    --manifest) manifest=${2:-}; shift 2 ;;
    --destination) destination=${2:-}; shift 2 ;;
    --local-archive) local_archive=${2:-}; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) fail "Unknown option: $1" ;;
  esac
done

[[ -n $manifest && -f $manifest && ! -L $manifest ]] || fail "Manifest is missing or is a symlink: ${manifest:-<unset>}"
[[ -n $destination && ! -L $destination ]] || fail "Destination is unset or is a symlink."
command -v sha256sum >/dev/null 2>&1 || fail "sha256sum is required to verify the runtime archive."

# The manifest is a fixed three-key document, so it is read by scanning for the
# assignment rather than by pulling a TOML parser into the install path.  A
# value may only be a single quoted literal; anything else is a malformed
# manifest and stops the install.
manifest_value() {
  local key=$1 line value
  line=$(grep -E "^${key} = " "$manifest") || fail "Manifest has no '${key}' entry: $manifest"
  [[ $(printf '%s\n' "$line" | wc -l) -eq 1 ]] || fail "Manifest has more than one '${key}' entry: $manifest"
  value=${line#*\"}
  value=${value%\"}
  [[ $line == "${key} = \"${value}\"" ]] || fail "Manifest entry '${key}' is not a quoted literal: $manifest"
  printf '%s\n' "$value"
}

expected_sha=$(manifest_value sha256)
file_name=$(manifest_value file_name)
url=$(manifest_value url)

[[ $expected_sha =~ ^[0-9a-f]{64}$ ]] || fail "Manifest sha256 is empty or not 64 hex characters. Build the runtime archive before installing."
[[ $file_name =~ ^[A-Za-z0-9._+-]+$ ]] || fail "Manifest file name contains characters that are not safe in a path: ${file_name:-<empty>}"

install -d -m 0755 "$destination"
archive="$destination/$file_name"
[[ ! -L $archive ]] || fail "Refusing to write through a symlink: $archive"

archive_sha() {
  local path=$1
  [[ -f $path && ! -L $path ]] || fail "Runtime archive is missing or is a symlink: $path"
  sha256sum -- "$path" | awk '{print $1}'
}

reject_mismatch() {
  local path=$1 actual=$2
  # A rejected download must not stay where the next run would reuse it.
  rm -f -- "$path"
  fail "Runtime archive sha256 mismatch: file=$path expected=$expected_sha actual=$actual"
}

if [[ -n $local_archive ]]; then
  [[ -f $local_archive && ! -L $local_archive ]] || fail "Local runtime archive is missing or is a symlink: $local_archive"
  actual=$(archive_sha "$local_archive")
  [[ $actual == "$expected_sha" ]] || fail "Runtime archive sha256 mismatch: file=$local_archive expected=$expected_sha actual=$actual"
  if [[ $(cd "$(dirname "$local_archive")" && pwd -P)/$(basename "$local_archive") != "$(cd "$destination" && pwd -P)/$file_name" ]]; then
    cp -p -- "$local_archive" "$archive"
    actual=$(archive_sha "$archive")
    [[ $actual == "$expected_sha" ]] || reject_mismatch "$archive" "$actual"
  fi
else
  [[ -n $url ]] || fail "Manifest has no download URL. Pass --local-archive with a copy of the runtime archive."
  case $url in
    https://*|http://*) ;;
    *) fail "Manifest download URL must be http or https: $url" ;;
  esac
  partial="$archive.partial"
  rm -f -- "$partial"
  if command -v curl >/dev/null 2>&1; then
    curl --fail --location --retry 3 --connect-timeout 20 --output "$partial" "$url" \
      || fail "Could not download the runtime archive from $url. Pass --local-archive with a copy made on a connected machine."
  elif command -v wget >/dev/null 2>&1; then
    wget --tries=3 --timeout=20 --output-document="$partial" "$url" \
      || fail "Could not download the runtime archive from $url. Pass --local-archive with a copy made on a connected machine."
  else
    fail "Neither curl nor wget is available to download the runtime archive."
  fi
  mv -- "$partial" "$archive"
  actual=$(archive_sha "$archive")
  [[ $actual == "$expected_sha" ]] || reject_mismatch "$archive" "$actual"
fi

printf '%s\n' "$(cd "$(dirname "$archive")" && pwd -P)/$(basename "$archive")"
