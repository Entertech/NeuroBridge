# Windows 平台扩展

## 一键入口

在完整项目中双击 `windows\neurobridge-windows-bootstrap.cmd` 即可自动准备并启动，无需输入菜单数字。也可在项目根目录的 PowerShell 运行：

```powershell
.\windows\neurobridge-windows-bootstrap.cmd
```

入口适用于 Windows 10/11 x64 源码联调。它复用 Python 3.11 x64，缺少时尝试通过 winget 为当前用户安装，创建 `.runtime\windows-venv` 并安装 `requirements.lock` 依赖；生成 `.runtime\config\windows-gateway.toml`，保留已有现场配置；检查算法、端口与 COM 后在前台运行正常网关 Bootstrap。源码启动不执行 Git，不安装服务、不改写系统执行策略，`Ctrl+C` 停止网关。

算法仍须由开发人员提供配套 Windows x64 `neurobridge_affective_bridge.exe` 与 DLL，默认位置 `.runtime\algorithm\`。脚本只执行空输入自检；缺少或自检失败时停止，不自动禁用算法或切换录播。Windows 算法构建链和真实输入比对仍待验证。

离线运行使用 `-Offline`：已有环境不再安装依赖；缺失环境须预先提供完整 Python 3.11 x64（含 venv/pip，可放 `python-runtime\windows\`）及 `wheelhouse\windows\` 下的匹配 wheel。离线参数禁止 winget 和网络 pip 安装。双击入口使用进程级 ExecutionPolicy Bypass；若组织策略禁止脚本，按组织批准方式运行，不修改机器策略。

需要排障菜单时运行 `.\windows\neurobridge-windows-bootstrap.cmd -Action menu`。菜单支持 `1` 一键准备启动、`2` 直接启动、`4` COM 枚举、`5` 创建/校验配置、`6` 算法自检、`7` 诊断摘要、`8` 最近日志、`0` 退出。也可用 `-Action start|check|config|algorithm|diagnostics|logs` 执行对应动作。除 `prepare` 外不安装运行时，缺少环境时重新双击入口即可准备。没有 COM 时网关仍可启动并等待插入设备；算法不可用、已有进程占用端口或配置错误时不会启动第二个网关。

自动化测试覆盖配置保留、路径转义、录播/监听策略拒绝、端口冲突、算法失败与诊断脱敏；Windows CI 另外运行 PowerShell 5.1 语法、进程锁和离线一键流程检查。CI 不替代真实设备或目标机验收。

PowerShell 环境准备、COM 检查、配置、前台启动、日志排障与已安装服务操作，见[内部手册第 10 节：Windows 操作教程](../doc/tech/麒麟V10网关运行与串口联调内部文档.md#10-windows-操作教程源码联调)。该教程用于源码联调，尚未完成 Windows 实机验收。

仓库已提供 Windows USB 虚拟串口的源码扩展：`WindowsSerialSource` 使用 pyserial 的 COM 枚举/打开能力，只选择 USB 派生 COM 端口，并复用 `HeadsetRev181Parser`、统一 ApplicationService、本机回环 WebSocket 与串口耳机禁用录播规则。

本目录包含：

- `gateway.toml.example`：`windows_headset_local`、COM 耳机和 `127.0.0.1` 本机页面配置；
- `neurobridge-windows-bootstrap.cmd` / `setup-windows-gateway.ps1`：双击入口和数字菜单；
- `gateway_helper.py`：项目配置、启动预检、正常 Bootstrap 调用与不含原始数据的诊断摘要；
- `service.py`：基于 pywin32 的 Windows Service 宿主，仍从正常 Bootstrap 启动；
- `../packaging/windows/`：unsigned 候选安装/卸载 PowerShell 骨架。

生成可检查的候选包：

```powershell
python tools/build-product-candidate.py --platform windows --output-dir artifacts
```

没有 `--runtime-dir` 的候选会在 manifest 中记录 `runtimeBundled=false`，只用于结构检查，不能安装。可安装候选的运行时目录必须直接包含 `python.exe` 与 `neurobridge_affective_bridge.exe`；构建器会拒绝缺少任一文件的运行时。正式候选必须在受控 Windows x86_64 runner 注入经过验证的完整离线运行时与算法 bridge。

当前仍不能宣称 Windows 产品已交付。最低目标是 Windows 7 x86_64，但具体 Service Pack、SHA-2 补丁、可运行的 Python/冻结运行时、浏览器、pywin32 兼容版本、安装器技术、代码签名和时间戳服务尚未锁定。至少需要在该最低基线完成 COM 枚举、ACK/0x01、E1/E0、拔插重连、服务重启、禁用录播、安装/升级/卸载和 24 小时长稳后，才能进入正式发布。
