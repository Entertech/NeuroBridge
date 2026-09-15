# 本机可视化与兼容 B 端联调网页

该页面复用版本台账登记的 WebSocket 契约。当前默认场景由同一台银河麒麟网关主机加载页面；兼容 `wired_b_side` 策略时，也可供独立 B 端联调。

生产/现场运行先启动 NeuroBridge，然后在同机浏览器访问：

```text
http://127.0.0.1:8080/
```

网关会动态注入 `ws://127.0.0.1:8765/neurobridge/v1/ws`。页面使用 `neurobridge.v1` 子协议，可测试 `getStatus`、`getLatest`、`subscribe` 和 `unsubscribe`，且不会发送 Ping/Pong 或 JSON 心跳。

本机策略会校验页面 Origin；直接双击 `index.html` 形成的 `file://` 页面仅用于资源开发检查，不能连接生产回环 WebSocket。切换旧有线模式时应关闭本机网页服务并使用批准的隔离专网地址，仍不得暴露公网、无线网络或不受控局域网。
