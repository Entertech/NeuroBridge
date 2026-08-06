# 跨平台网页

本目录仅保存浏览器可直接运行的静态资源，不包含任何 macOS、Windows 或 Linux 的进程启动、蓝牙访问或部署逻辑。

- `capture/`：采集控制台。平台侧的采集服务负责提供 `/api/` 接口，并可将本目录挂载到 `/capture/`。
- `b-client-test/`：B 端 WebSocket 联调页。可用任意静态文件服务器运行，也可由采集服务挂载到 `/b-client/`。

页面必须继续使用网关公开的 HTTP/WebSocket 契约，不能依赖某个平台的文件系统路径或命令。
