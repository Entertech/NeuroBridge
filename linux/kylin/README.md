# 银河麒麟 V10 耳机网关

本目录负责麒麟串口耳机的项目运行、依赖准备、设备权限、算法、自启、诊断与卸载。所有入口从仓库根目录执行，不依赖 `linux/ubuntu/`，不使用 Ubuntu 的服务模板或 SSH 部署器。

```bash
bash linux/kylin/neurobridge-kylin-bootstrap.sh
```

| 入口 | 用途 |
| --- | --- |
| `setup-kylin-gateway.sh` | 数字菜单，配置、依赖准备与启动编排 |
| `setup-kylin-python.sh` | 项目 Python 与锁定依赖 |
| `setup-kylin-serial.sh` | 串口配置与权限 |
| `setup-kylin-algorithm.sh` | 准备锁定工具链并调用本平台 bridge 入口 |
| `build-algorithm-bridge.sh` | 校验麒麟与本机编译器，选择 Eigen 锁后调用共享构建内核 |
| `start-kylin-gateway.sh` | 使用项目运行目录启动网关 |
| `setup-kylin-autostart.sh` / `lib/` | 本平台 systemd 单元渲染与管理 |
| `diagnose-kylin-*.sh` / `collect-kylin-runtime-diagnostics.sh` | 设备、构建与运行诊断 |
| `setup-kylin-release-runner.sh` | 麒麟原生构建机准备 |

源码配置和现场数据位于 `.runtime/`；具体流程和验收边界见[内部手册](../../doc/tech/麒麟V10网关运行与串口联调内部文档.md)。正式引导包只携带本目录与 `linux/common/`；已安装的旧 systemd 单元仍可通过根目录兼容启动器运行，重新启用自启后使用新路径。

## 10. 拆除源码部署（一键退出）

银河麒麟源码模式只往系统里写一个文件 `/etc/systemd/system/neurobridge.service`，其 `ExecStart` 指向当前源码目录的 `linux/kylin/start-kylin-gateway.sh`，其余配置、算法、录制和日志都在项目 `.runtime/` 内。因此拆除顺序固定为**先停服务、再删源码**：先删源码会留下一个仍处于 enabled 的单元，每 3 秒重启失败一次。

现场人员用数字菜单即可：运行 `bash linux/kylin/neurobridge-kylin-bootstrap.sh`，输入 `10`，再选择只停服务（`1`）或连同源码一起删除（`2`）。删除前必须输入 `DELETE` 确认。

高级人员可直接执行：

```bash
./linux/kylin/uninstall-kylin-project.sh status      # 只读体检，不改动任何东西
./linux/kylin/uninstall-kylin-project.sh uninstall   # 停服务并移除 systemd 单元，保留源码与 .runtime
./linux/kylin/uninstall-kylin-project.sh purge       # 备份现场数据后删除整个源码目录
./linux/kylin/uninstall-kylin-project.sh purge --yes # 非交互执行（无人值守）
```

`purge` 会先把 `.runtime/config` 与 `.runtime/recordings` 打包到 `$HOME/neurobridge-backup-<UTC时间戳>.tar.gz`（权限 `0600`），日志不在备份内；需要保留日志时在删除前另行复制 `.runtime/logs`。备份失败会中止删除。`--no-backup` 可跳过备份，`--backup-dir DIR` 可改备份目录。

脚本的安全边界：只处理带 `# Managed by NeuroBridge Galaxy Kylin project autostart` 标记、且 `ExecStart` 指向当前源码目录的单元，否则拒绝停止；拒绝删除过浅路径、系统目录、用户主目录和符号链接目标；以普通用户运行，只在 systemd 操作和删除 root 属主文件时调用 `sudo`。仅当受管单元指向的旧源码目录已经不存在时，才需要显式加 `--force`。

`uninstall` 会同时删除项目内 `.runtime/config/kylin-autostart.conf`，使自启偏好回到默认值，便于以后重新部署时恢复默认开机自启。源码模式不会创建 `neurobridge` 系统账户，也不会写 udev 规则或 logrotate 配置；`setup-kylin-serial.sh` 可能把当前用户加入串口组（通常为 `dialout`），不再使用串口时可自行执行 `sudo gpasswd -d $USER dialout` 移除。若系统上曾装过 `packaging/kylin/install.sh` 的安装包版本（`/opt/neurobridge`），那属于另一条链路，使用它自带的 `sudo bash packaging/kylin/uninstall.sh`。

## 麒麟 V10 多架构引导

当前引导源码支持七个候选 Profile（x86_64/aarch64/loongarch64/mips64el/sw64/x86/armhf），在 Kylin V10 识别 CPU、位数、字节序和本机编译器，选择锁定的 Python/CMake 输入。x86_64 保留预编译依赖，其余六个架构本机编译源码；全体串口运行时只安装 pyserial/websockets，不引入 BLE/dbus。新架构尚待真机编译与设备验收。详细依赖、交付准备、安装/运行日志导出和失败原因见[内部部署补充](../../doc/tech/麒麟V10网关运行与串口联调内部文档.md#多架构引导部署补充2026-10-10)；通用引导 DEB 的 all 标识不能用于跨 CPU 复用运行时。源码检查不代表目标机验收。
