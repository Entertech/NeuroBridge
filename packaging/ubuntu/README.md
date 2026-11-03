# Ubuntu 24.04 amd64 安装包

Ubuntu 使用预编译 DEB，类似 Windows 的直接安装方式；麒麟继续使用多架构引导包。当前只支持 Ubuntu 24.04 x86_64（Debian 架构名 `amd64`），不适用于 Ubuntu 22.04、ARM 或麒麟。应用版本取自版本台账，不由打包器修改。

包内包含应用、页面、锁定的 CPython 3.12 Python 依赖、已静态链接 SDK 的算法桥；现场不需要 Git、pip、编译器、Eigen 或 NumCpp。使用系统 Python 3.12、glibc 和 C++ 运行库，声明 BlueZ、DBus、iproute2、dnsmasq-base、logrotate、systemd、adduser 等系统依赖。DEB 本身不包含这些系统软件包。安装脚本不联网；离线镜像必须提前准备 `dpkg-deb -f <安装包> Depends` 列出的依赖。联网配置基础镜像时可由 APT 解析依赖，网关业务仍部署在受控专网。

## 安装与首次启动

核对同目录 SHA-256 文件与构建来源后，在 Ubuntu 本机或受控 SSH 运维终端执行：

```bash
sha256sum -c neurobridge-*.deb.sha256
sudo dpkg -i ./neurobridge-*-ubuntu-24.04-x86_64.deb
sudoedit /etc/neurobridge/gateway.toml
# 确认专用网卡、地址、子网、端口和蓝牙策略后再执行：
sudo systemctl enable --now neurobridge.service neurobridge-dhcp.service
sudo systemctl status neurobridge.service
sudo journalctl -u neurobridge.service -n 100 --no-pager
```

首次安装创建 `neurobridge` 服务账号和 Ubuntu 头环配置，但不启动或启用服务、不改网卡地址。配置中的地址是待现场确认的示例；`ensure_static_address=true` 会在明确启动服务后维护专用网卡地址。DHCP 单元在静态模式下跳过。启用后服务随系统启动。配置固定为 BLE 头环、有线 B 端、`local_ui=false`。

- 应用与私有 Python 依赖：`/opt/neurobridge/app`、`/opt/neurobridge/python`。
- 启动入口与算法桥：`/opt/neurobridge/runtime/bin/`；SDK 静态链接，清除算法桥的构建机 RPATH。
- 构建来源：`/opt/neurobridge/build-info.json`；包含提交、工具链、输入与运行时摘要，包未签名。
- 配置：`/etc/neurobridge/gateway.toml`，默认 `root:neurobridge 0640`。
- 录制与日志：`/var/lib/neurobridge/recordings`、`/var/log/neurobridge`。
- 系统服务：`/usr/lib/systemd/system/neurobridge*.service`。

## 升级、移除与已有源码部署

升级前备份配置和录制，确认允许中断采集与客户端连接。使用相同 `dpkg -i` 命令；配置不覆盖，录制不迁移、不重算。算法命令未自定义时继承包内默认路径；显式自定义命令继续保留，请确认仍然有效。升级会停止原先运行的本包服务，安装完成后恢复其运行状态；原先停止的服务保持停止。B 端恢复连接后先 `getStatus` 再重新订阅。仅配置失败时应查看 dpkg 与 journal 日志，不能视为升级成功。

```bash
sudo apt remove neurobridge-ubuntu
# purge 同样保留现场配置、录制、日志及服务账号；数据删除由运维单独执行。
sudo apt purge neurobridge-ubuntu
```

安装包与麒麟包互斥。若已有源码部署在 `/etc/systemd/system/neurobridge*.service` 或其 drop-in 中，安装会明确拒绝，避免本包单元被旧启动路径覆盖。先备份配置和数据，停止并禁用原服务，将这三个同名单元及 drop-in 备份到 systemd 搜索路径之外，执行 `systemctl daemon-reload`，再安装 DEB。保留的源码虚拟环境与源码目录不由 DEB 删除。切换到 DEB 后使用本包升级，不再运行 `update-ubuntu.sh` 覆盖应用。

## 构建与验证（开发人员）

在 Ubuntu 24.04 amd64、Python 3.12 的干净源码提交上，用独立构建账号安装构建依赖：`python3-venv python3-pip cmake g++ libeigen3-dev dpkg-dev patchelf`。Eigen 版本按 `sdk.lock` 校验，算法使用仓库内锁定源码，CMake/编译器/Python/dpkg 版本记录到构建清单。Python 环境需有 pip。

```bash
# 唯一联网阶段：下载并逐个校验锁定 wheel；随后可断网组包。
python3 tools/build-ubuntu-package.py --prepare-inputs --cache /tmp/ubuntu-wheels
python3 tools/build-ubuntu-package.py --cache /tmp/ubuntu-wheels --output-dir /tmp/ubuntu-package
# 仅在可销毁的 Ubuntu 容器内运行，会安装/移除软件：
bash tools/verify-ubuntu-package.sh /tmp/ubuntu-package/*.deb
```

CI 使用 `Ubuntu native package` 工作流生成 DEB、SHA-256、来源和验证日志，保留 30 天；正式发布矩阵也包含 `ubuntu-24.04-x86_64-deb`，不会自动创建 tag 或发布 Release。验证覆盖 DEB 元数据、ELF 架构、系统动态库加载、依赖导入、算法 JSON 冒烟以及隔离容器中的安装、同版本重装升级、remove/purge 数据保留。首次引入此包，尚无历史 Ubuntu DEB 可做跨版本升级验证；容器验证不等于真实 systemd、头环 BLE、专用网卡或现场验收通过。
