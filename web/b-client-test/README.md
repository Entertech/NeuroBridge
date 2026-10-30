# 本机可视化与兼容 B 端联调网页

该页面复用版本台账登记的 WebSocket 契约。当前默认场景由同一台银河麒麟网关主机加载页面；兼容 `wired_b_side` 策略时，也可供独立 B 端联调。

生产/现场运行先启动 NeuroBridge，然后在同机浏览器访问：

```text
http://127.0.0.1:8080/
```

网关会动态注入 `ws://127.0.0.1:8765/neurobridge/v1/ws`。页面使用 `neurobridge.v1` 子协议，可测试 `getStatus`、`getLatest`、`subscribe` 和 `unsubscribe`，且不会发送 Ping/Pong 或 JSON 心跳。

本机策略会校验页面 Origin；直接双击 `index.html` 形成的 `file://` 页面仅用于资源开发检查，不能连接生产回环 WebSocket。切换旧有线模式时应关闭本机网页服务并使用批准的隔离专网地址，仍不得暴露公网、无线网络或不受控局域网。

## 没有网页时验证收数

将仓库中的 `tools/b-client-probe.py` 单独复制到 B 端，在已准备好的 Python 环境中安装 `websockets==12.0`（离线现场提前准备依赖），然后运行：

```bash
python3 b-client-probe.py ws://192.168.88.10:8765/neurobridge/v1/ws
```

Windows 可使用 `python` 替代 `python3`。地址与端口按现场确认值填写；默认观察最多 20 秒或 5 批数据，可用 `--seconds`、`--max-batches` 调整。

脚本携带 `neurobridge.v1` 子协议，先 `getStatus`、再按可用流订阅，结束时退订；不发送保活帧，只打印批次数、数据来源和时间戳，不打印原始人体数据。`PASS` 表示收到标为有效的实时数据，`REPLAY ONLY` 仅证明历史录播可接收，`NO DATA` 或 `NO VALID DATA` 表示尚未验证有效收数。退出码 `0` 表示收到有效实时或录播数据，`2` 表示无有效数据，`1` 表示连接或协议错误。必须在 B 端运行，才能验证 B 端到网关的网络链路；在网关本机运行不能替代该项验收。
