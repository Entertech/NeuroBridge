#!/usr/bin/env bash
# Double-click this file in Finder, or run ./mac/start-poc.command from the repository root.
set -euo pipefail

# Finder does not always inherit the interactive shell's Homebrew PATH.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
runtime_dir="/tmp/neurobridge-mac-venv"
config_path="/tmp/neurobridge-gateway.capture.toml"
ui_url="http://127.0.0.1:8090/"
log_dir="/tmp/neurobridge-headband-poc"
log_path="$log_dir/poc-server.log"
stopping=0

if ! command -v uv >/dev/null 2>&1; then
  echo "未找到 uv。请先在终端执行：brew install uv"
  exit 1
fi

cd "$repo_root"
mkdir -p "$log_dir"
echo "正在准备 macOS 采集环境…"
uv python install 3.11
if [[ ! -x "$runtime_dir/bin/python" ]]; then
  uv venv "$runtime_dir" --python 3.11
fi
uv pip install --python "$runtime_dir/bin/python" "setuptools<81"
uv pip install --python "$runtime_dir/bin/python" --no-deps -e .
uv pip install --python "$runtime_dir/bin/python" bleak==0.19.0 websockets==12.0

echo "正在构建本地 C++ 算法 bridge…"
/bin/bash mac/build-algorithm-bridge.command

if [[ ! -f "$config_path" ]]; then
  cp mac/gateway.capture.toml.example "$config_path"
  echo "已创建本机配置：$config_path"
elif rg --quiet 'mac/algorithm_ingest_bridge\.py' "$config_path"; then
  cp "$config_path" "${config_path}.pre-native-bridge.bak"
  perl -0pi -e 's#command = \["/usr/bin/env", "python3", "mac/algorithm_ingest_bridge\.py", "--audit-file", "/tmp/neurobridge-headband-poc/algorithm-ingest-audit\.jsonl"\]#command = ["/tmp/neurobridge-affective-runtime/affective_bridge"]#' "$config_path"
  echo "已将旧 POC 配置切换为 C++ 算法 bridge；原配置备份为：${config_path}.pre-native-bridge.bak"
fi

"$runtime_dir/bin/python" mac/poc_server.py --config "$config_path" >"$log_path" 2>&1 &
server_pid=$!

cleanup() {
  stopping=1
  if kill -0 "$server_pid" 2>/dev/null; then
    kill -INT "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

for _ in {1..50}; do
  if curl --fail --silent "$ui_url/api/status" >/dev/null 2>&1; then
    echo "采集控制台已启动：$ui_url"
    echo "关闭此终端窗口或按 Ctrl-C 可停止采集。运行日志：$log_path"
    if [[ "${NEUROBRIDGE_NO_BROWSER:-0}" != "1" ]]; then
      if ! /usr/bin/open "$ui_url"; then
        echo "浏览器未能自动打开，请手动访问：$ui_url"
      fi
    fi
    if ! wait "$server_pid"; then
      if [[ "$stopping" == "1" ]]; then
        exit 0
      fi
      echo "采集服务意外退出。请将此日志发给我：$log_path"
      exit 1
    fi
    exit 0
  fi
  sleep 0.2
done

echo "本地采集服务未能在 10 秒内启动。请将此日志发给我：$log_path"
cat "$log_path" || true
exit 1
