# B 端联调网页

这是用于联调当前已发布的 [北向网络协议 v0.2](../../doc/tech/对外/头环数据网关北向网络协议/头环数据网关北向网络协议_v0.2.md) 的纯静态网页，不依赖构建工具、HTTP 服务或第三方服务。

直接双击打开本目录的 `index.html`，或将其拖入浏览器。浏览器地址会是类似下面的本地文件地址：

```text
file:///…/NeuroBridge/web/b-client-test/index.html
```

页面已预填网关默认 WebSocket 地址 `ws://192.168.88.10:8765/neurobridge/v1/ws`；现场 IP 或端口不同时可手动修改。点击连接后，测试 `getStatus`、`getLatest`、`subscribe` 和 `unsubscribe`。页面不会自动建连。网页使用 `neurobridge.v1` 子协议，且不会发送 Ping/Pong 或 JSON 心跳。由采集服务托管并注入地址时，仍优先使用服务提供的地址。

该工具只应连接专用有线网络中的测试网关；不得暴露到公网、无线网络或不受控局域网。

## 没有网页时验证收数

将仓库中的 `tools/b-client-probe.py` 单独复制到 B 端，在已准备好的 Python 环境中安装 `websockets==12.0`（离线现场提前准备依赖），然后运行：

```bash
python3 b-client-probe.py ws://192.168.88.10:8765/neurobridge/v1/ws
```

Windows 可使用 `python` 替代 `python3`。地址与端口按现场确认值填写；默认观察最多 20 秒或 5 批数据，可用 `--seconds`、`--max-batches` 调整。

脚本携带 `neurobridge.v1` 子协议，先 `getStatus`、再按可用流订阅，结束时退订；不发送保活帧，只打印批次数、数据来源和时间戳，不打印原始人体数据。`PASS` 表示收到标为有效的实时数据，`REPLAY ONLY` 仅证明历史录播可接收，`NO DATA` 或 `NO VALID DATA` 表示尚未验证有效收数。退出码 `0` 表示收到有效实时或录播数据，`2` 表示无有效数据，`1` 表示连接或协议错误。必须在 B 端运行，才能验证 B 端到网关的网络链路；在网关本机运行不能替代该项验收。
