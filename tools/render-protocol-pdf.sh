#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 path/to/protocol.md" >&2
  exit 2
fi

source_file=$1
root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source_path=$(cd "$(dirname "$source_file")" && pwd)/$(basename "$source_file")
output_path="${source_path%.md}.pdf"
html_path=$(mktemp /tmp/neurobridge-protocol.XXXXXX.html)
chrome_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
document_title=$(sed -n 's/^# //p' "$source_path" | head -n 1)

trap 'rm -f "$html_path"' EXIT
pandoc "$source_path" --from gfm --to html5 --standalone \
  --metadata title="$document_title" \
  --css "file://${root_dir}/tools/protocol-pdf.css" \
  -o "$html_path"
"$chrome_path" --headless --no-sandbox --allow-file-access-from-files \
  --print-to-pdf="$output_path" --print-to-pdf-no-header "$html_path"
echo "Generated $output_path"
