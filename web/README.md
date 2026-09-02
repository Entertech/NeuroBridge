# 跨平台网页

本目录仅保存浏览器可直接运行的静态资源，不包含任何 macOS、Windows 或 Linux 的进程启动、蓝牙访问或部署逻辑。

- `capture/`：USB 串口采集日志检查页。直接双击 `capture/index.html`，选择或粘贴本地 `neurobridge.log` 即可在浏览器内检查串口、握手、`0xE1` 响应分类、有效帧和丢包信息；不调用 `/api/`，不连接 WebSocket，也不上传日志。
- `b-client-test/`：本机可视化与兼容 B 端 WebSocket 联调页。默认由网关的回环 HTTP 服务发布到 `http://127.0.0.1:8080/`，并动态注入 WebSocket 地址；旧有线策略仍可复用同一页面。

两个页面都是无构建步骤的本地 HTML/CSS/JavaScript。`capture/` 是完全离线的日志阅读工具，不直接访问串口或任何后台服务；`b-client-test/` 虽然资源是静态的，但其联调功能仍按北向协议连接网关 WebSocket，不能直接访问蓝牙、串口、USB 或算法进程。
