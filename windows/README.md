# Windows 平台扩展

## 一键入口

服务启动失败时，双击 `windows\diagnose-service.cmd`，在 UAC 提示中允许管理员权限。脚本列出服务状态、PID/父 PID、Python 路径与命令行，尝试启动本项目已停止的 `NeuroBridgeProject`，并保存启动前后诊断及最近 200 行服务宿主日志到 `.runtime\diagnostics\windows-service-diagnosis-<UTC时间>-<唯一标识>.txt`。把该报告提供给开发人员即可继续排查；分享前检查命令行中是否含其他程序的敏感参数。

诊断报告还会记录本机本地/UTC 时间、Python 进程创建时间，并通过项目 Python 解析磁盘上的实际日志配置（目录、文件名、级别、统计周期与配置摘要，不导出配置正文）。随后列出最近 20 个匹配日志的大小与 UTC 修改时间，读取当前日志及最近两份未压缩归档各 300 行；当前文件即使比归档旧也会读取。配置解析失败时明确提示并退回项目默认目录。磁盘配置不代表运行中进程已重新加载，须与日志内启动记录核对；报告用于区分旧文件、时钟差异和当前串口错误，不代表断连故障已经修复。

已运行的服务不会被重启，启动/停止中的服务只提示稍后再试，同名服务指向其他目录或账户时拒绝操作。脚本不安装服务、不结束进程、不删除锁、不修改自启配置，也不上传报告。发现前台网关占用时，先在该窗口按 `Ctrl+C` 正常退出，再双击诊断脚本；服务达到 Running 只表示启动成功，采集仍需在页面与运行日志中核对。CMD 与同目录的 `diagnose-service.ps1` 须留在完整项目中，不依赖项目 Python 环境执行诊断。

在完整项目中双击 `windows\neurobridge-windows-bootstrap.cmd` 即可自动准备并启动，无需输入菜单数字。也可在项目根目录的 PowerShell 运行：

```powershell
.\windows\neurobridge-windows-bootstrap.cmd
```

入口适用于 Windows 10/11 x64 源码联调。它复用 Python 3.11 x64，缺少时尝试通过 winget 为当前用户安装，创建 `.runtime\windows-venv` 并安装 `requirements.lock` 依赖；生成 `.runtime\config\windows-gateway.toml`，保留已有现场配置；检查算法和端口后，首次通过 Windows UAC 请求管理员授权，安装并启动 `NeuroBridgeProject` 自动服务，随后打开本机 capture 页面。以后开机无需登录桌面即可启动网关，关闭启动窗口不影响服务；没有 COM 时等待耳机插入。服务仍调用正常网关 Bootstrap，源码启动不执行 Git、不改写系统执行策略。

算法程序由双击流程自动构建，无需手动提供 exe/DLL。首次联网下载 `sdk.lock` 锁定的 LLVM-MinGW、CMake、Ninja 和 Eigen ZIP，校验 SHA-256 后在项目临时目录编译仓库内 SDK/NumCpp；不安装 Visual Studio，不改变系统 PATH。Release 产物静态链接编译器运行库，检查 Windows x64 PE 和系统 DLL 导入，并在不含工具链的 PATH 下执行空输入自检，通过后才安装到 `.runtime\algorithm\neurobridge_affective_bridge.exe`。旧程序和 manifest 会先备份，构建失败保留原文件且不启动本次采集。

服务已运行时再次双击会复用该服务并打开页面，不重建算法或启动第二个网关。服务停止时，后续双击按源码指纹及 EXE 摘要复用产物，源码/锁文件变化或校验失败时自动重建。自定义 `algorithm.command` 路径只自检、不会被覆盖。构建日志在 `.runtime\logs\windows-algorithm-build.log`（保留上一份），产物旁 `.manifest.json` 记录源码提交/指纹、工具链、依赖、导入 DLL 和 EXE 摘要。Windows CI 使用同一构建器并上传与提交对应的 unsigned EXE/manifest Artifact，保留 14 天；构建和空输入测试不代表真实设备/算法语义验收。

当前验证证据：Python 回归、Windows 原生 PowerShell/工具包构建 CI（提交 `3395e57`，Actions `34322201486`）已通过；此前还完成同版本 LLVM-MinGW 交叉编译和 Wine 空输入自检。2026-09-09 用户提供 Inspiron 7559（i7-6700HQ、16 GB RAM、Windows x64）设备规格截图，确认运行联调成功；补充的系统规格截图确认 Windows 10 专业版 22H2，OS 内部版本 19045.6466。用户现场确认与 CI/合成输入结果分别记录；真实算法结果比对、24 小时长稳等未逐项提供的验收记录仍待补齐。

离线运行使用 `-Offline`：已有环境不再安装依赖；缺失环境须预先提供完整 Python 3.11 x64（含 venv/pip，可放 `python-runtime\windows\`）及 `wheelhouse\windows\` 下的匹配 wheel。算法未构建时还需在 `algorithm-packages\windows\` 放入 `sdk.lock` 中四个原名 ZIP；已验证的缓存可直接复用。离线参数禁止 winget、网络 pip 安装及算法依赖下载，缺少材料时显示具体文件名。双击入口使用进程级 ExecutionPolicy Bypass；若组织策略禁止脚本，按组织批准方式运行，不修改机器策略。

需要排障菜单时运行 `.\windows\neurobridge-windows-bootstrap.cmd -Action menu`。菜单支持 `1` 一键准备启动、`2` 直接启动、`4` COM 枚举、`5` 创建/校验配置、`6` 算法准备/修复、`7` 诊断摘要、`8` 最近日志、`9` 自启状态、`10` 启用自启并启动、`11` 关闭自启并停止服务、`12` 一键退出（移除服务注册与源码）、`13` 导出运行日志、`0` 退出。也可用 `-Action start|check|config|algorithm|diagnostics|logs|export-logs|uninstall|purge` 执行对应动作；`uninstall` 和 `purge` 是破坏性动作，非交互执行需显式加 `-Yes`。除 `prepare` 外不安装运行时，缺少环境时重新双击入口即可准备。没有 COM 时网关仍可启动并等待插入设备；算法不可用、已有进程占用端口或配置错误时不会启动第二个网关。

自动化测试覆盖配置保留、路径转义、录播/监听策略拒绝、端口冲突、算法失败与诊断脱敏；Windows CI 另外运行 PowerShell 5.1 语法、进程锁和离线一键流程检查。CI 不替代真实设备或目标机验收。

PowerShell 环境准备、COM 检查、配置、前台启动、日志排障与已安装服务操作，见[内部手册第 10 节：Windows 操作教程](../doc/tech/麒麟V10网关运行与串口联调内部文档.md#10-windows-操作教程源码联调)。该教程用于源码联调，用户已确认上述实机运行成功，完整产品验收与签名发布另行管理。

仓库已提供 Windows USB 虚拟串口的源码扩展：`WindowsSerialSource` 使用 pyserial 的 COM 枚举/打开能力，只选择 USB 派生 COM 端口，并复用 `HeadsetRev181Parser`、统一 ApplicationService、本机回环 WebSocket 与串口耳机禁用录播规则。

录制兼容性：Windows 录制索引使用可写句柄写入、刷新和同步后原子替换，避免旧版每 10 分钟切段时的 `Bad file descriptor`。切段失败会隔离已关闭句柄，保留可恢复文件，真实写入失败仍记录持久化缺口；未完成的分段不会标记为完整会话。升级前正常停止网关并保留录制目录，升级后检查跨越至少两次分段周期的日志。历史已丢失的数据不能自动补回；本次修复已加入本地故障注入回归和 Windows 原生 CI，现场复测仍待完成。

## 停止后恢复串口

2026-09-10 用户确认耳机未握手也能直接 E1 出数。Windows 与银河麒麟统一并行发现/打开串口与准备算法：已有合法流直接接管，静默候选在算法 ready 后发送 E1，收到完整合法 28 字节帧才验证并创建录制会话。预备算法移交正常会话复用，不重复 E1 或初始化。

不发送 ACK，不把独立 `01` 或命令写成功当作连接依据；停止提示文件 `.windows-serial-resume.json` 不再读写，历史文件保留但不影响行为。首次上电、E0 停止后服务重启和电脑重启均使用同一路径。

观察已有流保留旧配置键 `serial.handshake_timeout_ms`，串口写超时使用 `serial.command_response_timeout_ms`（不等待控制应答），E1 后首帧等待使用 `serial.data_timeout_seconds`。失败/取消时尝试过 E1 的候选尽力 E0 并关闭，释放未接管算法，按 `serial.reconnect_delay_seconds` 重试；不自动切换 DTR/RTS 或重置 USB。

日志 `startupPolicy=direct_e1` 表示新流程，`Serial target selected ... matchBasis=valid_28_byte_frame` 表示已收到合法帧。模拟及生产接线测试覆盖并行准备、初次启动/主机重启、无帧不误报、算法失败、取消清理和首帧保存；Windows 实机仍须完成冷启动、不拔插停止重启、整机重启与长稳复测。

## 开机自启与维护

首次双击时同意管理员授权即可；拒绝授权会显示错误，不自动改为前台运行。服务使用当前项目 `.runtime\windows-venv`、`.runtime\config\windows-gateway.toml` 和已准备的算法，开机时不联网安装或编译，也不打开桌面浏览器。登录后双击入口打开页面，或手动访问启动时提示的本机地址。

服务以 **LocalSystem** 运行。项目、基础 Python、虚拟环境、配置及算法必须放在固定的本地磁盘，并由可信人员管理写权限；不要使用共享可写目录、映射盘、网络盘或会被清理的临时目录。用户安装的基础 Python 也必须保留，服务需要 SYSTEM 可访问这些路径。脚本不自动扩大目录权限；受组织权限限制的机器需由管理员安排安装位置。

在项目根目录使用以下命令，启用和关闭会按需请求管理员授权：

```powershell
.\windows\neurobridge-windows-bootstrap.cmd -Action autostart-status
.\windows\neurobridge-windows-bootstrap.cmd -Action autostart-disable
.\windows\neurobridge-windows-bootstrap.cmd -Action autostart-enable
```

关闭自启会正常停止网关并保存 `.runtime\config\windows-autostart.json`；此后双击使用前台模式，`Ctrl+C` 停止。再次选择菜单 `10` 恢复自启。网页“停止”仅取消订阅，不停止后台服务。更新源码、依赖或算法前先关闭自启并等待服务停止；更新完成后再启用，启动预检失败时先修复，不要启动多个实例。

服务异常退出后由 Windows 服务管理器等待 3 秒重启。服务启动/退出错误写入 `.runtime\logs\windows-service.log`，管理员设置错误写入 `windows-service-control.log`（两者各 1 MiB，保留 3 份轮转）；运行日志仍由项目配置管理，默认 `.runtime\logs\neurobridge.log`。若宿主导入前就失败，还需查看 Windows 事件查看器的“Windows 日志 → 系统 / 应用程序”。

项目服务与候选安装包的 `NeuroBridge` 服务独立，拒绝覆盖其他项目目录注册的同名服务。迁移或删除项目之前先用下节的一键退出移除 `NeuroBridgeProject` 注册，不要手工 `sc.exe delete`；迁移后核对配置中的绝对路径，重新双击准备。不要让两种服务或前台网关争用相同端口和 COM。

新增自启有单元回归和 Windows CI 服务注册、虚拟环境宿主启动、HTTP 就绪、重复启动复用及启停测试；这次变更的 Windows 原生 CI 尚未执行。此前用户确认的联调成功不包含本次新增服务。目标机仍需验证重启后未登录桌面时服务已启动、USB 拔插恢复、正常停机、异常恢复及有历史录制时仍不回放。

## 一键退出（移除服务注册与源码）

不再需要这台 Windows 网关时，运行入口并输入菜单 `12`，再选择：

```text
一键退出（移除服务注册与源码）
  1. 只移除服务注册（保留源码与 .runtime 数据）
  2. 移除服务注册、备份现场数据并删除整个源码目录
  0. 返回主菜单
```

选 `1` 后 `NeuroBridgeProject` 被停止并删除注册，源码目录与 `.runtime` 现场数据原样保留，以后重新双击入口即可再次部署。选 `2` 会先把 `.runtime\config` 与 `.runtime\recordings` 打包成 `%USERPROFILE%\neurobridge-backup-<UTC时间戳>.zip`，再删除整个源码目录；删除前必须在终端输入 `DELETE` 确认，输入其它内容一律取消且不做任何改动。日志不在备份内，需要保留时在删除前另行复制 `.runtime\logs`。

高级人员可直接执行（破坏性动作非交互时须加 `-Yes`）：

```powershell
.\windows\neurobridge-windows-bootstrap.cmd -Action uninstall -Yes   # 只移除服务注册
.\windows\neurobridge-windows-bootstrap.cmd -Action purge           # 交互确认后删除源码
.\windows\neurobridge-windows-bootstrap.cmd -Action purge -Yes      # 非交互执行
```

顺序不能反过来：服务注册的启动命令行指向源码目录，先手工删源码会留下一个指向不存在路径的服务。`autostart-disable` 只把服务改成手动启动并停止，不删除注册，所以彻底移除必须走本节入口。

脚本的安全边界：只处理注册命令包含**当前**源码目录 `windows\project_service.py`、以 `host` 子命令启动、且账户为 `LocalSystem` 的服务，否则拒绝停止和删除；拒绝删除驱动器根目录、`%SystemRoot%`、`%ProgramFiles%`、`%ProgramData%`、`%USERPROFILE%` 以及不含 `pyproject.toml` 的目录。发现前台网关仍持有 `.runtime\windows-gateway.lock` 时拒绝执行，请先在该窗口按 `Ctrl+C` 正常退出。

这条路径不依赖项目虚拟环境，因此环境已损坏、`python` 或算法缺失时仍可移除服务注册和源码。移除时删除 `.runtime\config\windows-autostart.json`，使自启偏好回到默认值，便于以后重新部署时恢复默认开机自启。

**Windows 特有的一点：** 启动器自己的文件在运行期间被占用，`windows\neurobridge-windows-bootstrap.cmd`（以及通过双击启动时的 PowerShell 宿主）无法被同一个进程删除。脚本会在项目外的临时目录复制一份自身来执行删除，尽量清空目录；如果仍有被占用的文件，会打印 `sourceCheckout=partial`、列出剩余内容，并给出一条可在新 PowerShell 中直接执行的 `Remove-Item` 命令。这不是失败，而是 Windows 文件锁的正常表现——按提示关闭窗口后清理剩余文件即可。

若这台机器装过 `packaging/windows/install.ps1` 的候选安装包，那属于另一条链路：它安装到 `%ProgramFiles%\NeuroBridge`、注册的服务名是 `NeuroBridge`，用该目录自带的 `uninstall.ps1` 移除，与本节的 `NeuroBridgeProject` 互不影响。

## 导出运行日志

菜单 `13`（或 `-Action export-logs`）导出单个带时间戳的 `.zip` 并附 SHA-256。它与菜单 `7` 的诊断摘要不同：`7` 只导出摘要、明确不含日志正文，`13` 导出日志正文本身，且**网关没在运行也能用**。

脚本自动识别部署形态，因此源码部署与 MSI/EXE 安装包部署共用同一个命令：

```powershell
# 源码部署
.\windows\export-logs.ps1 -OutputDirectory C:\Temp

# MSI 安装部署
powershell -ExecutionPolicy Bypass -File 'C:\Program Files\NeuroBridge\windows\export-logs.ps1' -OutputDirectory C:\Temp
```

归档包含 `manifest.txt`（布局、服务名、生效日志目录、被跳过的内容、文件清单）、`application-logs\`（应用日志）、`service\snapshot.txt`（服务快照）、`service\event-log.txt`（近 7 天应用程序日志中与 NeuroBridge 相关的条目）、`system\ports.txt`（COM 端口清单，用 `-NoSystem` 可跳过）。单个日志超过 `-MaxLogBytes`（默认 32 MiB）时只保留尾部并改名为 `<名字>.tail`。

命令是只读的：不启停服务、不改配置、不删锁、不动录制。它**不包含配置正文、录制数据和凭据**，配置只记录 SHA-256。读取事件日志需要管理员权限，非管理员运行时相应小节会记录为已跳过。

`diagnostic-context.json` 记录部署文件的应用版本、源码提交、诊断时间/类型、Windows 名称/版本/OS 构建号、原生 CPU 架构、系统和导出进程位数、PowerShell 与部署 Python 版本/位数。缺失值标为 `unknown`；磁盘文件版本不表示旧运行进程已加载新版本，服务状态应同时查看。菜单 `7` 的摘要和网页日志下载也含运行 Python 进程的版本与环境。

交付目录中的 `install-with-logs.ps1`、`diagnostic-context.ps1` 与 `build-info.txt` 须保存在一起。安装用 `-Installer <MSI或EXE路径>`，失败用 `-Export -OutputDirectory C:\Temp`；尚未创建服务也能导出。安装摘要保存安装开始时的上下文，导出 ZIP 另附 `diagnostic-context-<UTC时间及唯一后缀>.json`，将 `applicationVersion`（当前部署文件）与 `packageApplicationVersion`（本次交付包）分开，源码提交也分别记录；不要将升级包版本当作已安装成功的版本。

网关自带的 HTTP 导出（`http://127.0.0.1:8766/downloads/logs/neurobridge-logs.zip`）只在网关运行且下载服务启用时可用，且按设计不含事件日志与 COM 端口信息；排障时优先用上面的命令。

## MSI / EXE 安装包部署

源码部署与安装包部署是两套独立形态，服务名和数据位置都不同，不要混用：

| 项目 | 源码部署 | MSI / EXE 安装包 |
|---|---|---|
| 服务名 | `NeuroBridgeProject` | `NeuroBridge` |
| 程序位置 | 源码 checkout | `C:\Program Files\NeuroBridge` |
| 配置 | `<checkout>\.runtime\config\windows-gateway.toml` | `C:\ProgramData\NeuroBridge\gateway.toml` |
| 日志 | `<checkout>\.runtime\logs` | `C:\ProgramData\NeuroBridge\logs` |
| 卸载 | 本文件「一键退出」章节 | 「应用和功能」或 `msiexec /x` |

卸载时服务正在运行不会冲突：WiX 的 `ServiceControl Stop="both" Wait="yes" Remove="uninstall"` 会先停止 `NeuroBridge`、等待其进入 Stopped，再删除文件，最后删除服务注册。服务进程持有 `C:\Program Files\NeuroBridge` 下的文件也不会阻断删除。

服务首次启动时会自建数据目录：创建 `C:\ProgramData\NeuroBridge` 下的 `logs` 和 `recordings`，并在 `gateway.toml` 不存在时从随包模板复制一份。已存在的配置不会被覆盖，重装和卸载都不会丢现场数据。`C:\ProgramData\NeuroBridge` 不归 MSI 所有，卸载后保留，需要彻底清除时手动删除。

安装包部署不要用启动器菜单里的服务动作：那些动作针对源码形态的 `NeuroBridgeProject`。菜单 `13` 与 `export-logs.ps1` 是例外——它按布局自动识别，两种形态都正确。

`packaging\windows\` 下的 `install.ps1` / `uninstall.ps1` 供候选包（`tools/build-product-candidate.py`）使用，**不随 MSI 分发**：MSI 的载荷根目录只有 `windows\`。

本目录包含：

- `gateway.toml.example`：`windows_headset_local`、COM 耳机和 `127.0.0.1` 本机页面配置；安装包会把它作为 `C:\ProgramData\NeuroBridge\gateway.toml` 的模板；
- `neurobridge-windows-bootstrap.cmd` / `setup-windows-gateway.ps1`：双击入口和数字菜单；
- `export-logs.ps1`：导出运行日志，自动识别源码与安装包布局；
- `diagnose-service.ps1`：只针对源码形态的服务诊断；
- `gateway_helper.py`：项目配置、启动预检、正常 Bootstrap 调用与不含原始数据的诊断摘要；
- `algorithm_build.py`：锁定工具下载、源码构建、产物校验和失败保护；
- `project_service.py`：项目内开机自启服务宿主、注册/启停和偏好保存；
- `service.py`：基于 pywin32 的 Windows Service 宿主，安装包形态下同时负责首次启动时创建 ProgramData 数据目录；
- `../packaging/windows/`：unsigned 候选安装/卸载 PowerShell 骨架，不随 MSI 分发。

生成可检查的候选包：

```powershell
python tools/build-product-candidate.py --platform windows --output-dir artifacts
```

没有 `--runtime-dir` 的候选会在 manifest 中记录 `runtimeBundled=false`，只用于结构检查，不能安装。可安装候选的运行时目录必须直接包含 `python.exe` 与 `neurobridge_affective_bridge.exe`；构建器会拒绝缺少任一文件的运行时。

Windows CI 在 `windows-latest` 上执行 `tools/prepare-windows-candidate-runtime.ps1`：校验并解压 Python 3.11.9 官方 embeddable x64 包，启用 `import site`，按 `requirements.lock` 安装依赖，再用锁定工具链编译算法，然后以 `--runtime-dir` 打入 zip。产物 manifest 的 `runtimeBundled` 为 `true`，解压后可由管理员运行 `install.ps1`。该包仍未签名，也不代表目标机验收通过。

安装候选 ZIP 时先完整解压，再在管理员 PowerShell 中执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1
```

Actions 中只有 `neurobridge-windows-unsigned-candidate` 是这个可安装 ZIP。`release-target-windows-*` 仅保存原生 MSI/EXE 的离线验收结果；PR 运行若没有配置离线输入，会是 `blocked` 日志，不是安装包。PR 诊断总 ZIP 里的 Windows 平台包也可能为空，不能直接安装。

当前仍不能宣称 Windows 产品已交付。最低目标是 Windows 7 x86_64，但具体 Service Pack、SHA-2 补丁、可运行的 Python/冻结运行时、浏览器、pywin32 兼容版本、安装器技术、代码签名和时间戳服务尚未锁定。至少需要在该最低基线完成 COM 枚举、直接 E1/完整帧验证、E0、拔插重连、服务重启、禁用录播、安装/升级/卸载和 24 小时长稳后，才能进入正式发布。
