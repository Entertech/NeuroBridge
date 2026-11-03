# 跨平台网页

本目录仅保存浏览器可直接运行的静态资源，不包含任何 macOS、Windows 或 Linux 的进程启动、蓝牙访问或部署逻辑。

- `capture/`：[静态采集控制台](capture/README.md)。直接双击 `capture/index.html`，默认连接部署网关的 HTTP 控制接口；保留开始扫描、停止采集、保存 ZIP 和日志查看。也可由网关挂载到 `/capture/`。
- `b-client-test/`：B 端 WebSocket 联调页。可用任意静态文件服务器运行，也可由采集服务挂载到 `/b-client-test/`。

页面必须继续使用网关公开的 HTTP/WebSocket 契约，不能依赖某个平台的文件系统路径或命令。

完整静态包：`python3 tools/build-static-console.py`，输出 `output/neurobridge-static-console.zip`。必须完整解压，保留两个目录之间的相对位置；本机无需启动服务。

当前 B 端 `message`、触发场景与连接异常分类见 [内部异常消息表](../doc/tech/B端异常消息与场景排查表.md)；网关本机录播命令与输出说明见 [Linux 运行说明](../linux/README.md#61-在网关本机管理录播无需-ssh)。
