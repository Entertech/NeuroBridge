#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 && $# -ne 3 ]]; then
  echo "Usage: $0 path/to/document.md path/to/output.pdf [path/to/style.css]" >&2
  exit 2
fi

source_file=$1
output_file=$2
root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
style_file=${3:-${root_dir}/tools/protocol-pdf.css}
style_path=$(cd "$(dirname "$style_file")" && pwd)/$(basename "$style_file")
source_path=$(cd "$(dirname "$source_file")" && pwd)/$(basename "$source_file")
output_path=$(cd "$(dirname "$output_file")" && pwd)/$(basename "$output_file")
render_work=$(mktemp -d "${TMPDIR:-/tmp}/neurobridge-protocol.XXXXXX")
html_path=$render_work/document.html
chrome_profile=$render_work/chrome-profile
document_title=$(sed -n 's/^# //p' "$source_path" | head -n 1)

if [[ -n "${CHROME_BIN:-}" ]]; then
  chrome_path="$CHROME_BIN"
else
  for candidate in google-chrome google-chrome-stable chromium chromium-browser "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"; do
    if [[ "$candidate" == /* && -x "$candidate" ]]; then
      chrome_path="$candidate"
      break
    fi
    if command -v "$candidate" >/dev/null 2>&1; then
      chrome_path=$(command -v "$candidate")
      break
    fi
  done
fi

[[ -n "${chrome_path:-}" ]] || {
  echo "Chrome/Chromium was not found; set CHROME_BIN." >&2
  exit 1
}

trap 'rm -rf "$render_work"' EXIT
pandoc "$source_path" --from gfm --to html5 --standalone \
  --metadata title="$document_title" \
  --css "file://${style_path}" \
  -o "$html_path"
"$chrome_path" --headless --no-sandbox --allow-file-access-from-files --user-data-dir="$chrome_profile" \
  --print-to-pdf="$output_path" --no-pdf-header-footer "$html_path"
echo "Generated $output_path"
