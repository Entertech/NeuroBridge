#!/usr/bin/env bash
set -euo pipefail

# This check protects the B-side contract from accidentally becoming an
# implementation document. Version changes are intentional: update this gate
# together with the newly approved external protocol.
external_protocol='doc/tech/头环数据网关北向网络协议_v0.2.md'
external_pdf='doc/tech/头环数据网关北向网络协议_v0.2.pdf'
expected_title='# 头环数据网关北向网络协议 v0.2'

[[ -f "$external_protocol" ]] || {
  echo "Missing current B-side protocol: $external_protocol" >&2
  exit 1
}
[[ -s "$external_pdf" ]] || {
  echo "Missing current B-side protocol PDF: $external_pdf" >&2
  exit 1
}
[[ "$(head -n 1 "$external_protocol")" == "$expected_title" ]] || {
  echo "B-side protocol must retain the approved v0.2 title: $expected_title" >&2
  exit 1
}

if rg -n --pcre2 '(蓝牙|(?i:\bBLE\b)|(?i:\bFlowtime\b)|(?i:\bEnter-Biomodule\b)|0000ff[0-9a-f-]*|\bFF(?:[0-9A-Fa-f]{2})\b|设备扫描|RSSI|连接策略|JSONL)' "$external_protocol"; then
  echo "B-side protocol contains internal gateway implementation details." >&2
  exit 1
fi

if ! rg -q '头环数据网关北向网络协议 v0\.2' README.md; then
  echo "README must link B-side users to the current v0.2 external protocol." >&2
  exit 1
fi
