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

需要排障菜单时运行 `.\windows\neurobridge-windows-bootstrap.cmd -Action menu`。菜单支持 `1` 一键准备启动、`2` 直接启动、`4` 检查采集来源、`5` 创建/校验配置、`6` 算法准备/修复、`7` 诊断摘要、`8` 最近日志、`9` 自启状态、`10` 启用自启并启动、`11` 关闭自启并停止服务、`12` 一键退出（移除服务注册与源码）、`13` 导出运行日志、`14` 打开原始数据查看页、`0` 退出。也可用 `-Action start|check|config|algorithm|diagnostics|logs|export-logs|capture|uninstall|purge` 执行对应动作；`capture` 打开 `http://127.0.0.1:<端口>/capture/`，不改变正在运行的网关。安装包部署可用 `neurobridge web capture` 打开同一页面，`neurobridge web` 仍打开联调首页。`uninstall` 和 `purge` 是破坏性动作，非交互执行需显式加 `-Yes`。除 `prepare` 外不安装运行时，缺少环境时重新双击入口即可准备。没有 COM 时网关仍可启动并等待插入设备；算法不可用、已有进程占用端口或配置错误时不会启动第二个网关。

自动化测试覆盖配置保留、路径转义、录播/监听策略拒绝、端口冲突、算法失败与诊断脱敏；Windows CI 另外运行 PowerShell 5.1 语法、进程锁和离线一键流程检查。CI 不替代真实设备或目标机验收。

PowerShell 环境准备、COM 检查、配置、前台启动、日志排障与已安装服务操作，见[内部手册第 10 节：Windows 操作教程](../doc/tech/麒麟V10网关运行与串口联调内部文档.md#10-windows-操作教程源码联调)。该教程用于源码联调，用户已确认上述实机运行成功，完整产品验收与签名发布另行管理。

仓库已提供 Windows USB 虚拟串口的源码扩展：`WindowsSerialSource` 使用 pyserial 的 COM 枚举/打开能力，只选择 USB 派生 COM 端口，并复用 `HeadsetRev181Parser`、统一 ApplicationService、本机回环 WebSocket 与串口耳机禁用录播规则。

录制兼容性：Windows 录制索引使用可写句柄写入、刷新和同步后原子替换，避免旧版每 10 分钟切段时的 `Bad file descriptor`。切段失败会隔离已关闭句柄，保留可恢复文件，真实写入失败仍记录持久化缺口；未完成的分段不会标记为完整会话。升级前正常停止网关并保留录制目录，升级后检查跨越至少两次分段周期的日志。历史已丢失的数据不能自动补回；本次修复已加入本地故障注入回归和 Windows 原生 CI，现场复测仍待完成。

## 使用蓝牙头环

Windows 10/11 x64 首次生成的配置默认同时启用 USB 串口耳机和 Flowtime BLE 头环。打开 Windows 蓝牙与头环电源后，网关自动扫描和重连。可以只接其中一种设备，联调页按实际连接情况显示。

Windows 安装包内置 Bleak 0.19.5，用于修复 Python 3.11 下头环连接时的 WinRT 服务发现异常。

从 v0.3.2 覆盖安装时，网关首次启动会识别原厂默认配置，将其备份至 `%ProgramData%\NeuroBridge\config-backups`，再换成带中文注释的双设备配置。修改过的配置和已明确设置头环开关的配置继续沿用。可在 Windows 事件查看器的应用程序日志中确认迁移结果。

已有配置需要开启头环或补注释时，参照带中文注释的 [Windows 配置模板](gateway.toml.example)。源码部署的配置是 `.runtime\config\windows-gateway.toml`，安装包部署是 `%ProgramData%\NeuroBridge\gateway.toml`。修改前停止网关。源码部署使用菜单 `11` 停止项目服务，前台运行时按 `Ctrl+C`。安装包部署在管理员 PowerShell 中执行 `Stop-Service NeuroBridge`。

- 同时采集时，使用 `profile = "windows_headset_local"`，在 `[data_source]` 设置 `type = "serial"` 与 `include_headband = true`。
- 只采集头环时，使用 `profile = "windows_headband_local"`，在 `[data_source]` 设置 `type = "bluetooth"` 与 `include_headband = false`。
- 双设备或仅头环模式须将 `[ble].enabled` 设为 `true`，扫描超时和重连间隔都要大于 0 秒。默认分别为 5 秒和 3 秒。旧配置没有 `[ble]` 时，从模板复制该段。网关按广播型号 UUID 匹配头环。可将 `device_name` 设为实际广播名称或留空。附近有多个同型号头环时，可在 `[ble].device_identifier` 填入目标头环的蓝牙 MAC 地址，未填写时选择信号最强的匹配设备。

排查头环连接时，在 `neurobridge.log` 搜索 `Flowtime scan completed`。若 `discovered=0`，优先检查蓝牙适配器、驱动和附近设备是否在广播。若 `discovered>0` 且 `modelMatches=0`，检查头环是否在广播及型号 UUID 是否正确。若 `modelMatches>0` 且 `matched=0`，检查名称和设备标识配置。

已连接的头环掉线后，Windows 立即开始第一次扫描。程序会记住本次运行中最后一个完成连接和采集初始化的头环地址，收到该地址的新广播后就尝试重连，并重新验证 GATT 特征和采集初始化。此时日志的 `previousDeviceSeen=True` 可以解释为什么名称或型号字段尚未收到就开始连接。如果在扫描窗口内没有遇到该地址，继续按原有名称、型号和设备标识配置筛选。如果首选设备连接或初始化失败，下次恢复完整扫描，并优先尝试本轮尚未失败的匹配设备。所有匹配设备都已尝试后，从信号最强的设备重新开始。扫描或连接失败后的重复尝试仍按 `reconnect_delay_seconds` 间隔进行。

`Flowtime EEG notifications` 和 `Flowtime EEG session ended` 记录头环 EEG 包的累计分类计数。全零和全 FF 只检查采样字节，跳过两字节序号。计数与原始数据来自同一通知回调，便于区分采样字节全零和通知停止。

Windows 在启动采集后持续监测 EEG 和心率通知。如果两路都停满 `[ble].notification_timeout_seconds`（默认 10 秒），网关结束旧会话并重新扫描，即使 Windows 仍报告蓝牙已连接。日志 `notificationTimedOut=True` 表示此次重连由通知停更触发。仍有一路在发送通知时继续采集。若本次连接从未收到任一路通知，按 `reconnect_delay_seconds` 等待后完整扫描，优先尝试其他匹配候选。旧配置缺少该字段时使用默认值。

头环断开时，`Flowtime streaming ended` 记录连接持续时间，以及最后一条 EEG 和心率通知距断开的毫秒数。值为 `None` 表示该连接尚未收到对应通知。`scanRssi` 是建立连接前的扫描信号强度。`Flowtime capture restarted` 的 `outageMs` 表示从蓝牙断开到网关重新启动采集的时间。可按日志时间与 `Data stream became stale` 对照。

Windows 头环建立连接后，`Flowtime WinRT GATT session status changed` 会记录 Windows 报告的会话状态及蓝牙错误（名称和数值）。可用 `attempt` 与同一连接的断开回调、`stopping` 标志及采集日志对照。`status=CLOSED(0) bluetoothError=SUCCESS(0)` 表示会话已关闭，Windows 未报告 GATT 错误。判断由哪一端发起断开需要蓝牙控制器层的证据。若 WinRT 会话事件无法订阅，日志会显示 `Flowtime WinRT GATT session diagnostics unavailable`。

Windows 网关会启动实时 HCI 跟踪，日志出现 `Windows Bluetooth HCI trace active` 后即可采集。使用 `Windows Bluetooth HCI` 搜索控制器事件，并按 `eventUtc`、`attempt` 和 `handle` 对照 WinRT 日志。日志记录 HCI 断开命令、断开完成事件、已匹配头环的连接参数及更新结果。只有通过连接完成事件关联到当前目标的句柄会标记 `association=headband`；`unmatched` 需与应用日志时间对照。跟踪使用内存会话，日志只写事件摘要、原因码和参数，不保存 ETL、蓝牙地址或 EEG／心率载荷。驱动或权限不支持时记录 `Windows Bluetooth HCI trace unavailable`，采集继续运行。

若 `disconnection_complete` 记录 `reason=0x3B`，按同一 `handle` 查找此前的 `le_remote_parameter_request`、正面／负面应答、`le_connection_update_command` 和控制器返回的状态。若走 LE L2CAP 信令，使用 `identifier` 将 `l2cap_parameter_update_request` 配对到 `l2cap_parameter_update_response`。请求会记录连接间隔范围、外设延迟和监督超时，`parameterIssues` 标出基础范围或超时裕量违规。只有整组拒绝码时，仍需对照头环固件或空口日志才能定位具体字段。修复后在 Windows 10 上连续采集，覆盖多个原掉线周期，再检查是否出现 `0x3B`。

源码部署双击启动器重新启动，安装包部署执行 `Start-Service NeuroBridge`。打开 `http://127.0.0.1:8080/`（自定义端口以配置为准），等待「头环蓝牙」区显示设备已连接。点击 `getLatest` 查看最新算法结果，或点击 `subscribe` 接收连续窗口。需要确认原始字节时，源码部署用菜单 `14` 或 `-Action capture`，安装包部署用 `neurobridge web capture`，打开 `/capture/`。该页同时展示耳机 28 字节串口帧和头环蓝牙通知原始包；仅头环模式不再跳回联调页。

双设备模式用同一个 WebSocket 地址和连接收两路数据，默认地址为 `ws://127.0.0.1:8765/neurobridge/v1/ws`。两路均为实时采集，响应和事件带有 `data.source`，协议请求在 `params.source` 指定设备。联调页每栏自动填写对应来源。双设备模式下头环录制保存在录制目录的 `headband` 子目录，两路均可从原下载地址导出。切回仅采集耳机时，在串口耳机配置中将 `include_headband` 设为 `false` 并重启。

双设备联调页分别显示 EEG 与心率状态。设备仍连接时，脑电质量为 `0` 且波形没有有效振幅的窗口显示「无有效脑波」。波形停更超过 `[data_source].stale_after_ms` 时显示「无脑波数据」，收到未佩戴状态或全 FF 波形提示时显示「脱落」。算法返回 `0 bpm` 时，心率及其相关指标显示「无心率数据」。设备断开后隐藏对应栏。

Windows 双设备模式默认开启 EEG 输出校准。耳机和头环各用原始数据独立计算。网关分别用两路的包序号和到达时间估算采样时间，允许从邻近接收窗口选取时间重叠的头环样本。每个头环样本在对应通道中只配对一次。耳机样本超出头环对应样本绝对值的默认 5% 偏差时取最近边界，波形继续遵守 SDK 的 ±2000 输出范围。两路时间模型和该通道参考均可用时，耳机输出只保留配对点，波形数组可能缩短或为空。两路的放松度、注意力、愉悦度、Flow 分数和频段能量均为有效非零值时，用同一接收窗口的头环结果校准，默认限制在参考值的 ±5% 内。校准后的结果同时用于实时输出和录制。脑电质量、心率、HRV、压力、和谐度、激活度、冥想提示和睡眠状态保持各自算法结果。可在 `[data_source]` 设置 `eeg_alignment_percent = 0` 并重启网关关闭。

耳机 EEG 质量大于 0 且波形已成功配对时，耳机 Flow 为 0 可使用参与配对的头环窗口的有效正值校准，优先取同一接收窗口。采用现有偏差范围的下界，例如头环 Flow 为 30 时，默认校准为 28.5。日志的 `reason=flow_fallback` 和 `flowFallbackReferenceBatchId` 用于核对参考窗口。

联调页按设备保留上次有效的实时算法读数。收到 0、缺失值或无效窗口时继续显示上次读数，首次收到有效值前显示「—」，断开时清除读数。详情中的脑电质量、冥想提示和睡眠状态保留各自的枚举语义。脑波和频段图表仍按当前 EEG 窗口刷新。

排查耳机 Flow 持续为 0 时，在 `neurobridge.log` 搜索 `Headset Flow diagnostic`。日志列出 EEG 质量、历史是否重置、当前和所需样本数，以及专家模型和 AI 模型的返回分数。同一诊断原因每 10 秒最多输出一条 INFO 日志。

耳机从接收窗口结束算起，最多等待 `eeg_alignment_output_wait_ms`，默认 800 ms，支持 0 至 1200 ms。设为 0 即刻使用已完成结果。头环参考覆盖耳机窗尾时立即结束等待，只有心率或无可用波形的头环结果会继续等待。断开会清除对应连接的序号时间模型，头环断开时同时清除头环参考并立即释放等待。时间估算需要至少三个接收窗口跨度的到达记录，模型或通道参考不可用时保留耳机波形。含任一原始 EEG 全 `FF` 包的窗口跳过校准。检查只读 EEG 样本字节，头环序号及两路心率均不参与判定。缺失指标、零值占位和无信号波形保持原值。旧配置里的 `eeg_alignment_wait_ms` 和 `eeg_alignment_raw_timeout_ms` 可保留。

头环参考缓存最多保留 `eeg_alignment_reference_batches` 个批次，默认 16。每路采样时间模型最多保留 `eeg_alignment_clock_packets` 条包观测记录，默认 4096，并淘汰超过 7.2 秒的观测记录。容量写满时淘汰最旧条目。

Windows 联调页把命中 ±2000 限幅的双通道样本画成断点，并在对应通道标出饱和点数。脑电质量为 0 时，全零通道显示为断点，全零频段显示为暂无有效频段能量。

在 `neurobridge.log` 搜索 `Windows EEG alignment configuration` 核对校准和等待上限。`Headset EEG alignment decision` 记录实际等待时间、回退原因、各通道配对、修改和舍弃点数。`waveformReferenceBatchIds` 记录实际参与波形配对的头环批次，`referenceBatchId` 记录 EEG 指标的参考批次。原始 EEG 包全为 `FF` 时，跳过原因会标记为 `headset_raw_eeg_all_ff` 或 `headband_raw_eeg_all_ff`。默认 `INFO` 每约 10 秒记录各原因的窗口数，并为每种原因保留一条窗口详情。联调时可将 `[logging].level` 设为 `DEBUG`，对照两路 `EEG algorithm response` 的成功状态、错误原因和算法耗时。这些日志只记录诊断元数据。

`Headset raw EEG` 在算法和校准之前记录 Windows 耳机的原始 EEG 异常窗口，关掉校准也能看到。记录区分 `all_zero`、`all_ff`、`near_adc_rail`，以及异常后样本回到 ADC 范围内的 `within_adc_range`。每条包括 `batchId`、连接会话、窗口时间、EEG 和心率包数、左右通道的零值与边界样本计数。每种状态在同一连接会话内最多每 10 秒记录一次。压力值走独立心率算法，EEG 异常时仍可能有值。

耳机串口与头环蓝牙各用独立的算法会话，结果随设备来源分别录制和显示。Windows 耳机送入 SDK 的心率按每 200 ms 一个接收时段取样，每个时段最多取一个实收值，优先取该次组包中最新到达的值。空时段不补值，同一时段拆成多批时只向 SDK 提交一次。这个 5 Hz 输入节奏与 SDK 预期一致，原始心率订阅、原始帧录制和 EEG 算法输入保留全部实收数据。启动日志的 `Windows headset HR algorithm input` 记录取样间隔和输入映射版本。升级后请重新构建或替换算法桥。源码部署可先停止网关，再用启动器菜单 `6` 重建。耳机模式启动前会检查算法桥是否支持 SDK 耳道 EEG 模式。

此功能为源码候选。Windows 蓝牙适配器、前台／服务账户下的真实扫描、采集、重连和重启仍待目标机验证。

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

## 局域网 SSH 排障

安装后，在 Windows 桌面运行 `neurobridge web`，即可用默认浏览器打开 B 端联调页。命令每次查询正在运行的网关所监听的端口。`neurobridge web --url` 只输出网页地址，方便脚本使用。

在 SSH 会话里运行 `neurobridge web`，再把输出的 SSH 转发命令粘贴到看网页的电脑上的新终端。保持转发连接打开，并用浏览器访问命令显示的地址。转发同时覆盖网页和 WebSocket。

在 Windows 10/11 完成 MSI / EXE 安装后，从开始菜单新开 PowerShell，即可在任意目录运行以下命令。如果一直开着 Windows Terminal，请关闭整个应用再打开，以加载安装时注册的系统 PATH。ZIP 候选包可将示例中的 `neurobridge` 换成 `& "$env:ProgramFiles\NeuroBridge\windows\cli\neurobridge.cmd"`。

```powershell
neurobridge ssh install
neurobridge ssh enable
neurobridge ssh enable "C:\Program Files\OpenSSH\sshd.exe"
neurobridge ssh status
neurobridge ssh disable
```

Windows MSI / EXE 和 ZIP 候选包安装时跳过 SSH。升级保留本机现有的 SSH 服务和防火墙规则。安装后可以直接运行 `neurobridge web` 打开本机联调页。

`install` 按需通过 Windows 安装 OpenSSH Server 并启用，可能需要访问 Windows Update。遇到系统重启提示时，重启后运行 `neurobridge ssh enable`。

`enable` 使用本机已注册的 `sshd` 服务并设为开机自启，将标准 TCP 22 入站规则限制在本地子网。可选路径须指向该服务实际使用的 `sshd.exe`，有空格时请加引号。找不到服务或可执行文件、路径不匹配时，命令报错并以非零状态退出。可用 `neurobridge ssh status` 查询服务状态、可执行文件路径和可用的连接地址。

`disable` 停止系统的 OpenSSH Server，将服务设为手动启动，并禁用标准 SSH 防火墙规则。这会断开现有 SSH 连接。从同一子网连接时使用自己的 Windows 账号。运行命令时按提示同意 Windows 管理员授权。NeuroBridge 服务可以处于停止状态，但必须已安装。

也可以双击 `windows\remote-support.cmd` 使用交互菜单，选 `1` 启用、`2` 关闭、`3` 查询、`4` 安装并启用。源码部署从项目目录打开，安装包部署从安装目录打开。

## MSI / EXE 安装包部署

覆盖安装会更新 EXE 和其中的 MSI，重新安装命令入口并注册系统 PATH。已安装的网关配置和录制数据保存在 ProgramData。升级完成后可在新开的 PowerShell 中运行 `neurobridge ssh status`。

源码部署与安装包部署是两套独立形态，服务名和数据位置都不同，不要混用：

| 项目 | 源码部署 | MSI / EXE 安装包 |
|---|---|---|
| 服务名 | `NeuroBridgeProject` | `NeuroBridge` |
| 程序位置 | 源码 checkout | `C:\Program Files\NeuroBridge` |
| 配置 | `<checkout>\.runtime\config\windows-gateway.toml` | `C:\ProgramData\NeuroBridge\gateway.toml` |
| 日志 | `<checkout>\.runtime\logs` | `C:\ProgramData\NeuroBridge\logs` |
| 卸载 | 本文件「一键退出」章节 | 「应用和功能」或 `msiexec /x` |

安装器升级或卸载时会请求停止 `NeuroBridge`，等服务退出后再处理程序文件。若旧服务未能退出，安装器可能报错 1921 并回滚。已使用 EXE 部署时，直接用新版 EXE 覆盖安装。使用 `install-with-logs.ps1 -Installer <安装包>` 可以保存日志。脚本发现网关已处于 `STOP_PENDING` 时最多等待 15 秒，仍未停止就结束本次尝试并提示检查服务。

服务首次启动时会自建数据目录：创建 `C:\ProgramData\NeuroBridge` 下的 `logs` 和 `recordings`，并在 `gateway.toml` 不存在时从随包模板复制一份。配置升级规则见上面的「使用蓝牙头环」。`C:\ProgramData\NeuroBridge` 不归 MSI 所有，卸载后保留。

安装包部署不要用启动器菜单里的服务动作：那些动作针对源码形态的 `NeuroBridgeProject`。菜单 `13` 与 `export-logs.ps1` 是例外——它按布局自动识别，两种形态都正确。

`packaging\windows\` 下的 `install.ps1` / `uninstall.ps1` 供候选包（`tools/build-product-candidate.py`）使用，**不随 MSI 分发**：MSI 的载荷根目录只有 `windows\`。

### 安装失败 1921、1618 或出现同名条目

1. 如果报 `0x80070652`（1618），关闭失败窗口，等正在进行的安装、卸载或回滚结束。用事件查看器的「Windows 日志 → 应用程序」筛选 `MsiInstaller`。事件 1040、1042 分别记录事务开始和结束，可用客户端 PID 和包名配对。先看失败时段是否有事件 11921（MSI 错误 1921）。外层可能只报告 1603。
2. 如果 1921 指向 `NeuroBridge Gateway`，在管理员 PowerShell 运行 `sc.exe queryex NeuroBridge`。如果是 `RUNNING`，运行 `sc.exe stop NeuroBridge`，然后复查。确认安装事务已结束、服务为 `STOPPED` 后，只运行一次新版 EXE。
3. 如果等了 30 秒仍是 `STOP_PENDING`，且检查点一直不变，在允许中断采集时可只结束卡住的网关进程。重新运行 `sc.exe queryex NeuroBridge`，核对 `WIN32_OWN_PROCESS` 和当前非零 PID，再用 `Stop-Process -Id <当前网关PID> -Force -ErrorAction Stop`。它可能丢失尚未写盘的数据。确认 `STOPPED` 后再安装。事件里的安装事务客户端 PID 属于安装方，请使用服务查询返回的 PID。
4. 如果仍失败，保留安装窗口的 `log file`、同一时间段的 `MsiInstaller` 事件和 `sc.exe queryex NeuroBridge` 输出。避免强制结束 `msiexec.exe` 或手动删除安装缓存、注册表条目和 `%ProgramData%\NeuroBridge`。

新版 EXE 的临时条目显示为 `NeuroBridge Setup (in progress)`。安装结束后，「应用和功能」应只显示一个 NeuroBridge 条目。如果仍有同名条目，保留安装日志和条目截图。

本目录包含：

- `gateway.toml.example`：带中文注释的 Windows 双设备配置模板；安装包用它首次生成 `C:\ProgramData\NeuroBridge\gateway.toml`；
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
