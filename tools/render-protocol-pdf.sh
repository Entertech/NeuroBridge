#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 path/to/protocol.md path/to/output.pdf" >&2
  exit 2
fi

source_file=$1
output_file=$2
root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source_path=$(cd "$(dirname "$source_file")" && pwd)/$(basename "$source_file")
output_path=$(cd "$(dirname "$output_file")" && pwd)/$(basename "$output_file")
html_path=$(mktemp "${TMPDIR:-/tmp}/neurobridge-protocol.XXXXXX.html")
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

trap 'rm -f "$html_path"' EXIT
pandoc "$source_path" --from gfm --to html5 --standalone \
  --metadata title="$document_title" \
  --css "file://${root_dir}/tools/protocol-pdf.css" \
  -o "$html_path"
"$chrome_path" --headless --no-sandbox --allow-file-access-from-files \
  --print-to-pdf="$output_path" --no-pdf-header-footer "$html_path"
echo "Generated $output_path"
