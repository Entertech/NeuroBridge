# 跨平台网页

本目录仅保存浏览器可直接运行的静态资源，不包含任何 macOS、Windows 或 Linux 的进程启动、蓝牙访问或部署逻辑。

- `capture/`：采集控制台。macOS POC 提供采集 API；Ubuntu 网关通过仅监听 `127.0.0.1:8090` 的 `neurobridge-config-ui` 挂载本目录，并额外提供本机读取、校验、保存 `gateway.toml` 的接口。静态页面本身不包含蓝牙、配置写入或提权逻辑。
- `b-client-test/`：B 端 WebSocket 联调页。可用任意静态文件服务器运行，也可由采集服务挂载到 `/b-client/`。

页面必须继续使用服务公开的 HTTP/WebSocket 契约，不能依赖某个平台的文件系统路径或命令。配置 API 仅用于网关本机，不属于 B 端北向协议，也绝不能绑定到专用有线网卡、公网或不受控局域网。
