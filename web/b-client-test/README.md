# B 端联调网页

这是用于联调当前已发布的 [北向网络协议 v0.2](../../doc/tech/对外/头环数据网关北向网络协议/头环数据网关北向网络协议_v0.2.md) 的纯静态网页，不依赖构建工具、HTTP 服务或第三方服务。

直接双击打开本目录的 `index.html`，或将其拖入浏览器。浏览器地址会是类似下面的本地文件地址：

```text
file:///…/NeuroBridge/web/b-client-test/index.html
```

在页面中填写网关 WebSocket 地址，例如 `ws://192.168.1.10:8080/neurobridge/v1/ws`，然后连接并测试 `getStatus`、`getLatest`、`subscribe` 和 `unsubscribe`。网页使用 `neurobridge.v1` 子协议，且不会发送 Ping/Pong 或 JSON 心跳。

该工具只应连接专用有线网络中的测试网关；不得暴露到公网、无线网络或不受控局域网。
