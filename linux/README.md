# Linux 平台文件

此目录保存 Ubuntu x86_64 网关的部署专属内容：

- `install-ubuntu.sh`：安装 Python 运行环境、BlueZ、服务账户和 systemd 服务，并启用开机自启。
- `systemd/`：开机自启服务单元；异常退出后 3 秒自动重启。
- `logrotate/`：网关持久化日志的每日轮转配置。

从仓库根目录以 root 运行 `./linux/install-ubuntu.sh`。服务使用 `/etc/neurobridge/gateway.toml`，持久化录播保存在 `/var/lib/neurobridge/recordings`，运行日志在 `/var/log/neurobridge/neurobridge.log`。安装脚本会为服务账户创建这些目录并启用服务；确认静态 IP、端口和下载服务配置后执行 `systemctl start neurobridge`，之后每次开机都会自动运行。

配置中的 `[download]` 会在专用有线网络提供无鉴权 HTTP 下载服务；上线前必须确认其静态 IP 与端口，并保持该网络不连接公网或不受控局域网：

- `GET /downloads`：返回已结束录播会话的下载索引；
- `GET /downloads/recordings/<recordingId>.zip`：按需导出一个已结束录播会话；
- `GET /downloads/logs/neurobridge-logs.zip`：下载当前与轮转后的运行日志快照。

正在采集的会话不能导出，避免得到不完整的录播文件。可用 `systemctl status neurobridge`、`journalctl -u neurobridge` 和持久化日志排查运行情况。
