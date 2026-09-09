# Windows 平台扩展

PowerShell 环境准备、COM 检查、配置、前台启动、日志排障与已安装服务操作，见[内部手册第 10 节：Windows 操作教程](../doc/tech/麒麟V10网关运行与串口联调内部文档.md#10-windows-操作教程源码联调)。该教程用于源码联调，尚未完成 Windows 实机验收。

仓库已提供 Windows USB 虚拟串口的源码扩展：`WindowsSerialSource` 使用 pyserial 的 COM 枚举/打开能力，只选择 USB 派生 COM 端口，并复用 `HeadsetRev181Parser`、统一 ApplicationService、本机回环 WebSocket 与串口耳机禁用录播规则。

本目录包含：

- `gateway.toml.example`：`windows_headset_local`、COM 耳机和 `127.0.0.1` 本机页面配置；
- `service.py`：基于 pywin32 的 Windows Service 宿主，仍从正常 Bootstrap 启动；
- `../packaging/windows/`：unsigned 候选安装/卸载 PowerShell 骨架。

生成可检查的候选包：

```powershell
python tools/build-product-candidate.py --platform windows --output-dir artifacts
```

没有 `--runtime-dir` 的候选会在 manifest 中记录 `runtimeBundled=false`，只用于结构检查，不能安装。可安装候选的运行时目录必须直接包含 `python.exe` 与 `neurobridge_affective_bridge.exe`；构建器会拒绝缺少任一文件的运行时。正式候选必须在受控 Windows x86_64 runner 注入经过验证的完整离线运行时与算法 bridge。

当前仍不能宣称 Windows 产品已交付。最低目标是 Windows 7 x86_64，但具体 Service Pack、SHA-2 补丁、可运行的 Python/冻结运行时、浏览器、pywin32 兼容版本、安装器技术、代码签名和时间戳服务尚未锁定。至少需要在该最低基线完成 COM 枚举、ACK/0x01、E1/E0、拔插重连、服务重启、禁用录播、安装/升级/卸载和 24 小时长稳后，才能进入正式发布。
