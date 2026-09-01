# 跨平台网页

本目录仅保存浏览器可直接运行的静态资源，不包含任何 macOS、Windows 或 Linux 的进程启动、蓝牙访问或部署逻辑。

- `capture/`：采集控制台。平台侧的采集服务负责提供 `/api/` 接口，并可将本目录挂载到 `/capture/`。
- `b-client-test/`：本机可视化与兼容 B 端 WebSocket 联调页。默认由网关的回环 HTTP 服务发布到 `http://127.0.0.1:8080/`，并动态注入 WebSocket 地址；旧有线策略仍可复用同一页面。

页面必须继续使用网关公开的 HTTP/WebSocket 契约，不能直接访问蓝牙、串口、USB 或算法进程。生产环境使用网关提供的 HTTP 页面，不以 `file://` 绕过 Origin 校验。
