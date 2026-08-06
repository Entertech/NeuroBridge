# Linux 平台文件

此目录保存 Ubuntu x86_64 网关的部署专属内容：

- `install-ubuntu.sh`：安装 Python 运行环境、BlueZ、服务账户和 systemd 服务。
- `systemd/`：开机自启服务单元。
- `logrotate/`：网关日志轮转配置。

从仓库根目录以 root 运行 `./linux/install-ubuntu.sh`。网关业务代码仍位于 `neurobridge/`，网页资源位于 `web/`。
