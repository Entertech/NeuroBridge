#!/usr/bin/env bash
# Double-click this file in Finder, or run ./mac/stop-poc.command from the repository root.
# Stop only this repository's local POC process and let it cleanly disconnect the headband.
set -euo pipefail

config_path="/tmp/neurobridge-gateway.capture.toml"
server_pattern='[m]ac/poc_server\.py --config /tmp/neurobridge-gateway\.capture\.toml'
server_pids=()

while IFS= read -r pid; do
  [[ -n "$pid" ]] && server_pids+=("$pid")
done < <(/usr/bin/pgrep -f "$server_pattern" || true)

if (( ${#server_pids[@]} == 0 )); then
  echo "未发现正在运行的本机头环采集服务。"
  exit 0
fi

echo "正在正常停止本机头环采集服务…"
for pid in "${server_pids[@]}"; do
  if kill -0 "$pid" 2>/dev/null; then
    kill -INT "$pid"
  fi
done

for _ in {1..50}; do
  running=0
  for pid in "${server_pids[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      running=1
      break
    fi
  done
  if (( running == 0 )); then
    echo "采集服务已停止，头环断开与窗口落盘清理已完成。"
    exit 0
  fi
  sleep 0.2
done

echo "采集服务尚未在 10 秒内退出。请检查：/tmp/neurobridge-headband-poc/poc-server.log"
exit 1
