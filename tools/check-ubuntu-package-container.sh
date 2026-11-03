#!/usr/bin/env bash
set -euo pipefail
[[ $# == 1 ]] || { echo 'Usage: check-ubuntu-package-container.sh /absolute/output-directory' >&2; exit 1; }
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
output=$(cd "$1" && pwd)
packages=("$output"/*.deb)
[[ ${#packages[@]} == 1 && -f ${packages[0]} ]]
image=neurobridge-ubuntu-package-validation
docker build --platform linux/amd64 -f "$repo/packaging/ubuntu/Dockerfile.verify" -t "$image" "$repo/packaging/ubuntu"
docker image inspect "$image" --format '{{.Id}}' > "$output/lifecycle-image.txt"
# No network, no compiler, no checkout mounted into the runtime container.
# Retain a separate log; append it only after all lifecycle checks pass.
if ! docker run --rm --platform linux/amd64 --network none \
    -v "$output:/packages:ro" \
    -v "$repo/tools/verify-ubuntu-package.sh:/verify.sh:ro" \
    "$image" bash /verify.sh "/packages/$(basename "${packages[0]}")" > "$output/lifecycle.log" 2>&1; then
    rm -f "$output/verification.json"
    cat "$output/lifecycle.log"
    exit 1
fi
cat "$output/lifecycle.log" >> "$output/validation.log"
cat "$output/lifecycle.log"
