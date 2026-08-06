# Linux 平台文件

此目录保存 Ubuntu x86_64 网关的部署专属内容：

- `install-ubuntu.sh`：安装 Python 运行环境、BlueZ、服务账户和 systemd 服务，并启用开机自启。
- `systemd/`：开机自启服务单元；异常退出后 3 秒自动重启。
- `logrotate/`：网关持久化日志的每日轮转配置。

从仓库根目录以 root 运行 `./linux/install-ubuntu.sh`。服务使用 `/etc/neurobridge/gateway.toml`，持久化录播保存在 `/var/lib/neurobridge/recordings`，运行日志在 `/var/log/neurobridge/neurobridge.log`。安装脚本会为服务账户创建这些目录并启用服务；确认网卡、地址、端口和下载服务配置后执行 `systemctl start neurobridge`，之后每次开机都会自动运行。

## B 端连接地址模式

`[network].mode` 将地址分配与采集/录播业务隔离，可在两种模式间切换：

- `static`（默认）：网关和 B 端预先配置静态地址；B 端直接使用 `server.host`、`server.port` 和 `server.path` 建立 WebSocket。
- `dhcp`：网关的接入网卡仍必须预先配置为固定的 `server.host`。独立的 `neurobridge-dhcp.service` 在该网卡为 B 端分配地址，并通过 DHCP 默认网关选项发布 `server.host`；B 端从系统 DHCP 租约读取默认网关，再使用固定 `server.port` 与 `server.path` 连接。

DHCP 不做 DNS（`dnsmasq` 仅服务配置的网卡），也不能让端口动态化。B 端程序必须具备读取操作系统默认网关的能力；纯浏览器 JavaScript 无法读取 DHCP 租约，不能单独实现自动发现。切换模式后执行：

```bash
sudo systemctl restart neurobridge-dhcp
sudo systemctl restart neurobridge
```

从 DHCP 切回静态模式时，`ExecCondition` 会停止并跳过 DHCP 单元。若确定永久不再使用 DHCP，可执行 `sudo systemctl disable --now neurobridge-dhcp.service`，再按运维策略卸载 `dnsmasq`；这不会修改网关业务进程、录播文件或北向 WebSocket 服务。

配置中的 `[download]` 会在专用有线网络提供无鉴权 HTTP 下载服务；上线前必须确认其静态 IP 与端口，并保持该网络不连接公网或不受控局域网：

- `GET /downloads`：返回已结束录播会话的下载索引；
- `GET /downloads/recordings/<recordingId>.zip`：按需导出一个已结束录播会话；
- `GET /downloads/logs/neurobridge-logs.zip`：下载当前与轮转后的运行日志快照。

正在采集的会话不能导出，避免得到不完整的录播文件。可用 `systemctl status neurobridge`、`journalctl -u neurobridge` 和持久化日志排查运行情况。
