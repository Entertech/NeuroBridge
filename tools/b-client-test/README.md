# B 端联调网页

这是用于联调当前已发布的 [北向网络协议 v0.2](../../doc/tech/头环数据网关北向网络协议_v0.2.md) 的静态网页，不依赖构建工具或第三方服务。

在仓库根目录运行以下命令后，浏览器访问终端显示的地址：

```bash
python3 -m http.server 8088 --directory tools/b-client-test
```

在页面中填写网关 WebSocket 地址，例如 `ws://192.168.1.10:8080/neurobridge/v1/ws`，然后连接并测试 `getStatus`、`getLatest`、`subscribe` 和 `unsubscribe`。网页使用 `neurobridge.v1` 子协议，且不会发送 Ping/Pong 或 JSON 心跳。

该工具只应连接专用有线网络中的测试网关；不得暴露到公网、无线网络或不受控局域网。
