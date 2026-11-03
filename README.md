# NeuroBridge

NeuroBridge 是将设备数据接入本机浏览器或兼容第三方 B 端主机的 PC 网关。运行时首个现场验证基线仍是 N100/N150 x86_64 主机上的银河麒麟 V10，设备为通过 USB 派生 TTY 通信的耳机；当前发布矩阵包含一个银河麒麟 V10 通用引导 DEB（all）、Windows 10/11 的 x86_64 EXE/MSI 和 Ubuntu 24.04 amd64 预编译 DEB 目标。本分支增加 Ubuntu 24.04 x86_64 蓝牙头环与专网 B 端接入；网关核心负责采集、数据与算法处理、北向协议适配、录播和运行维护。

统一架构与分阶段范围见 [NeuroBridge 项目结构与多系统接入 PRD](doc/tech/NeuroBridge项目结构与多系统接入/NeuroBridge项目结构与多系统接入_PRD.md)和[技术方案](doc/tech/NeuroBridge项目结构与多系统接入/NeuroBridge项目结构与多系统接入_技术方案.md)；M1 设备细节继续以[银河麒麟 V10 耳机 USB 串口接入 PRD](doc/tech/%E9%93%B6%E6%B2%B3%E9%BA%92%E9%BA%9FV10%E8%80%B3%E6%9C%BAUSB%E4%B8%B2%E5%8F%A3%E6%8E%A5%E5%85%A5_PRD.md)和[专项技术方案](doc/tech/%E9%93%B6%E6%B2%B3%E9%BA%92%E9%BA%9FV10%E8%80%B3%E6%9C%BAUSB%E4%B8%B2%E5%8F%A3%E6%8E%A5%E5%85%A5_%E6%8A%80%E6%9C%AF%E6%96%B9%E6%A1%88.md)为准。Ubuntu 头环作为独立 Profile 整合，按单独的目标机清单验收；各平台结果分别记录。

源码发布采用 `需求分支 → dev_B → publish_B → 独立候选提交 → 打包与发布验证 → vB → finalize_B → master`。需求开发不升版，用户选定版本和需求范围后才初始化版本；各阶段 PR 和 push 只做代码检查，合入 `master` 不再自动发版。源码流程通过 GitHub App 编排，配置见 [code-release-train.toml](.release/code-release-train.toml)，操作和启用条件见[发布技术方案](doc/tech/发布工作流技术方案.md)。

Windows 10/11 x64 EXE/MSI、麒麟引导 DEB 和 Ubuntu 预编译 DEB 构建由 `Release` 提供打包接口。训练发布要求全部 6 个安装包通过校验；总 ZIP 上传 Actions Artifacts 并回下载验证后，由源码流程给同一候选提交打不可变 tag，再创建 GitHub Release 下载入口和最终合入 PR。只有远端 tag 身份及默认分支祖先校验通过，才算发布完成。手动 `Release` 与 `Kylin bootstrap package` 仍可构建诊断候选，不创建 tag。应用版本仍以台账 `[application].version` 为准；旧平台字段不自动递增，不改变协议版本或目标机验收状态。

原生麒麟 DEB/RPM 安装时为 `neurobridge` 服务账户追加实际 USB TTY 所属的非 root 组。建议连接耳机后安装；未接设备会提示授权尚未就绪，接入后需重装同包完成授权，再重启服务。授权失败阻止安装脚本继续，安装成功不代表采集已通过目标机验收。操作步骤见[原生安装说明](doc/tech/源码原生安装包构建.md#麒麟deb--rpm)。

麒麟引导源码按系统/CPU/ABI 选择依赖，Profile 包括 x86_64、aarch64、loongarch64、mips64el、sw64、x86 与 armhf；x86_64 沿用预编译 Python/CMake，其余从锁定源码本机构建。新增架构为待真机验收候选，all 不意味着运行时跨 CPU 通用；详细支持表见[发布 PRD](doc/product/发布工作流%20PRD.md#27-系统与-cpu-架构支持表)。本轮不打包、不推送，已发布指南保持历史范围；最新适配部署步骤见[内部手册](doc/tech/麒麟V10网关运行与串口联调内部文档.md#多架构引导部署补充2026-10-10)。允许未接耳机时安装，服务等待设备；安装器保留实际 USB TTY 非 root 组授权，并为 USB 派生串口安装 `neurobridge` 组、`0660` 权限规则，后续插入无需重装授权。升级和重装都会使用当前包内源码重新构建并部署，归档固定使用随包 Python；已有服务保留原运行与自启状态，首次安装启用并启动服务。失败回滚恢复原程序、配置、服务单元、权限规则及状态；卸载自动停止服务、取消自启并移除单元及本包权限规则，保留程序目录、配置、日志和录制数据。详细行为及目标机待验收项见[发布工作流技术方案](doc/tech/发布工作流技术方案.md#麒麟引导包安装生命周期)。

麒麟通用包只声明共用系统依赖；x86_64 不要求 Python 源码开发库。其余源码 Profile 在构建前检查 SSL/sqlite/bzip2/xz/ffi/zlib 头文件与链接库，失败日志列出缺项和补齐命令。旧候选包在 dpkg 依赖阶段失败时，恢复步骤见[内部手册](doc/tech/麒麟V10网关运行与串口联调内部文档.md#安装前环境与依赖)。

手动运行 `Release` 可直接构建 Windows 原生安装包，或复用同分支、同提交并通过校验的 `native_input_run_id`。正式发版从 `Source version train` 的显式 `release` 操作进入；重试恢复原候选 SHA 和已验证的 Artifact，不重新初始化版本、移动 tag 或重复发送成功的发布结果。

## Ubuntu 24.04 头环网关

Ubuntu x86_64 使用 `ubuntu_headband_wired` Profile：头环 BLE → 网关 → 专用有线网络 → B 端。它与 Windows/麒麟耳机共用 Bootstrap、ApplicationService、算法引擎和分段存储，通过 Profile 固定设备、传输、接入方式和录播能力。整合范围与兼容性见 [Ubuntu 头环接入整合](doc/tech/NeuroBridge项目结构与多系统接入/Ubuntu头环接入整合.md)。

Ubuntu 推荐使用[预编译 DEB](packaging/ubuntu/README.md)：安装时不联网、不编译，系统依赖需由基础镜像预先准备；首次安装不启用或启动服务。核验包来源后安装，确认 `/etc/neurobridge/gateway.toml` 的现场网络参数，再执行 `systemctl enable --now neurobridge.service neurobridge-dhcp.service`。升级保留配置和录制；已有源码部署需先按说明迁移 systemd 单元。`Ubuntu native package` 工作流上传 DEB 与验证证据，不发版、不打 tag。

以下为保留的**源码部署**入口；DEB 部署使用包升级，不运行源码更新器：

```bash
git clone https://github.com/Entertech/NeuroBridge.git
cd NeuroBridge
# 切到经批准的 Ubuntu 分支/提交后再准备与部署
./linux/ubuntu/prepare-ubuntu24.04-environment.sh  # 一次性联网准备
sudo ./linux/ubuntu/install-ubuntu.sh             # 使用本地源码构建、安装；不会首次启动服务
sudoedit /etc/neurobridge/gateway.toml
sudo systemctl start neurobridge
```

首次配置来自 [Ubuntu 专属模板](config/gateway.ubuntu.toml.example)。示例网关为 `192.168.88.10/24`，B 端为 `192.168.88.20/24`，WS 为 `ws://192.168.88.10:8765/neurobridge/v1/ws`；地址、端口、网卡与扫描匹配条件必须在现场确认。安装和 systemd 启动追加专用地址并保留已有地址、路由与 DNS；多网口必须指定目标接口。部署后更新使用 `./linux/ubuntu/update-ubuntu.sh`，会重启服务，B 端须重新连接、查询状态并订阅。SSH 运维继续使用 `neurobridge-ops branch`、`update [--branch 分支]`、`status` 和 `logs --follow`。详见 [Linux 部署与运维](linux/README.md)。

Ubuntu 的采集控制台由下载服务提供：`http://192.168.88.10:8766/capture/`；兼容 B 端页面位于 `/b-client-test/`。也可生成独立静态包后双击打开：

```bash
python3 tools/build-static-console.py
```

该包使用 `web/ubuntu/` 中的页面，包含扫描/停止/导出、左右脑波、频段和六项指标。`web/capture/`、`web/b-client-test/` 继续用于默认分支的本机耳机展示。采集控制只管理当前 Ubuntu 采集管线，HTTP/WS 监听持续存在；重复启动复用当前采集，停止后再启动创建新的采集和算法会话。静态页仅连接已批准的隔离专网，不发送心跳。

录播由部署配置管理。Ubuntu 离线时只在许可开启且存在可读历史会话时按请求回放；禁止时 `mode=live`，取数提示“头环未连接”，历史文件保留。串口耳机 Profile 始终禁止录播。网关本机可执行：

```bash
sudo /opt/neurobridge/venv/bin/python -I -m neurobridge.replay_control enable
sudo /opt/neurobridge/venv/bin/python -I -m neurobridge.replay_control disable
sudo /opt/neurobridge/venv/bin/python -I -m neurobridge.replay_control status
```

旧 Ubuntu 配置可在加载时迁移为蓝牙专网配置，保留现场地址、录播许可和算法期限；已声明新 schema 的配置仍须显式选择数据源。新部署使用共享的有界分段存储，兼容读取旧录制；原始数据与算法结果分别持久化，回放不重新计算算法。录制格式、运行诊断和旧安装回退见整合说明。Ubuntu 源码集成与合成回归不等于真实头环、N100/N150 或现场验收。

采集生命周期由共享 `GatewayRuntime` 监督：设备生产任务异常会更新错误状态、清理旧会话并按配置重建，不关闭 HTTP/WS。`getStatus` 与纯状态订阅只读取内存；离线取数的录播目录检查有独立期限与缓存，详见整合说明。macOS POC 停止采集后同样保留监听，重新开始使用新会话。

HRV 缺失或为 null 时，可使用 [HRV 链路诊断](doc/tech/NeuroBridge项目结构与多系统接入/Ubuntu头环接入整合.md#hrv-链路诊断) 对照 SDK、桥接、应用结果、录制、投影与实际发送的字段状态。新增 HRV trace 不记录心率/HRV 数值或原始字节；默认按状态变化和周期记录，DEBUG 可逐窗口追踪。

## 仓库结构

- `neurobridge/`：跨平台网关核心，不依赖 macOS、Windows 或 Linux 的启动方式。
- `neurobridge/domain/`、`ports/`、`application/`：不可变领域模型、端口合同和设备无关应用管线。
- `neurobridge/adapters/`、`profiles/`、`bootstrap/`：I/O 实现、固定 Deployment Profile 和唯一运行时组合根。
- `web/`：由网关托管、无构建步骤的静态网页；`capture/` 是通过网关 WebSocket 查看耳机原始数据的页面，`b-client-test/` 是完整的 B 端协议联调页。
- `mac/`：仅 macOS POC 的启动器、蓝牙验证、原生算法 bridge 与本机配置模板。
- `linux/kylin/`、`linux/ubuntu/`：分别维护麒麟与 Ubuntu 部署；`linux/common/` 只提供共享构建内核，两个平台互不依赖。
- `windows/`：Windows COM Source 配置、Service 入口与后续目标机验收说明；`packaging/` 分别维护麒麟引导包、Windows 安装包和 Ubuntu 原生 DEB 的资源与生命周期。

网页不再放入平台目录。两个页面都由当前网关的回环 HTTP 服务托管并连接同一个北向 WebSocket，不直接访问 USB 串口，也不控制 systemd 进程。

## 按平台使用

| 场景 | 入口 | 当前状态 |
| --- | --- | --- |
| 银河麒麟 V10 x86_64 网关 | [`linux/kylin/neurobridge-kylin-bootstrap.sh`](linux/kylin/neurobridge-kylin-bootstrap.sh) | 旧平台台账版本 `0.0.1`，build `1`，仅供现有入口参考；新发布包使用应用版本。菜单 `1` 完成项目内配置并默认安装/启动 systemd 开机自启服务；菜单 `9` 可查看状态或显式配置为非自启。 |
| Ubuntu x86_64 网关部署 | [DEB 安装说明](packaging/ubuntu/README.md)；[源码部署](linux/ubuntu/install-ubuntu.sh) | 固定 BLE 头环与专用 B 端 Profile；共用分层管线，支持受控采集、诊断和录播策略；实机验收待完成。 |
| macOS 历史 POC | [`mac/start-poc.command`](mac/start-poc.command) | 已接入统一 Bootstrap/BLE Profile，不属于当前 M1 验收。 |
| Windows 网关 | [`windows/README.md`](windows/README.md)；[一键操作教程](doc/tech/麒麟V10网关运行与串口联调内部文档.md#101-一键准备与启动推荐) | 旧平台台账版本 `0.0.1`，build `1`，仅供现有入口参考；新发布包使用应用版本。双击 `windows/neurobridge-windows-bootstrap.cmd` 自动准备配置、构建自检算法，首次管理员授权后安装开机自启服务并打开本机页面；可在菜单关闭自启。用户已确认上述 Windows x64 实机运行成功，新增开机自启仍待实机重启验证。串口发现与算法准备并行，直接 E1 后以合法帧验证；真实耳机新流程复测待完成。Windows 7 与 32 位系统不在交付范围内；签名不属于当前发布门禁。 |
| 耳机原始数据查看页 | [`web/capture/`](web/capture/) | 启动网关后访问 `http://127.0.0.1:8080/capture/`；明确区分实时耳机连接与 `live`/`replay` 数据来源，原始数据区与解析数据区固定上下排列。 |
| 本机可视化/兼容 B 端联调网页 | [`web/b-client-test/`](web/b-client-test/) | 默认由网关在回环地址提供；兼容模式仍可作为独立 B 端联调页。 |

默认本机场景启动网关后访问 `http://127.0.0.1:8080/` 使用完整联调台，访问 `http://127.0.0.1:8080/capture/` 查看耳机原始数据。两个页面都使用网关动态注入的 `ws://127.0.0.1:8765/neurobridge/v1/ws`。`capture` 的开始/停止按钮只订阅或取消当前网页的数据推送，设备连接、串口、算法、录制和服务生命周期仍由后台网关负责。

## 首期交付结论

- 目标硬件：N100/N150 x86 小主机，8 GB 内存、256 GB SSD、可稳定枚举目标耳机 TTY 的 USB 端口。
- 目标系统：N100/N150 x86_64 主机上的银河麒麟 V10；其他系统结果不替代正式验收。
- 当前链路：`耳机 → USB 派生串口 → 网关后台 → 本机回环 WebSocket → 同机浏览器`。不需要 B 端专用网线、静态地址或 DHCP。
- 运行方式：当前银河麒麟耳机 USB 串口仅支持实时采集；串口未验证或断开时，`subscribe` 与 `getLatest` 不会启动录播，而是返回 `409 STREAM_NOT_AVAILABLE_REASON`。录播仅保留给明确支持该模式的历史兼容数据源。
- 运维与交付：网关注册为 systemd 开机自启服务；运行日志持久化并每日轮转。本机策略下网页、WebSocket 和下载服务只监听 `127.0.0.1`。只有切换兼容有线策略时，才配置专网静态地址或独立 DHCP 单元。
- 演示范围：本次为单人；北向协议仅保留录播关联所需的 `subjectId`。
- 当前耳机通信规范定义固定 28 字节串口帧：3 字节包头、1 字节长度、2 字节大端序列号、18 字节 EEG、1 字节心率和 3 字节包尾。算法兼容层从中投影 20 字节 EEG 与 1 字节 HR 原始输入。

当前银河麒麟部署固定使用 USB 串口，不依赖蓝牙；BLE 兼容代码不进入本次部署或验收。

## 北向协议要点

本机可视化前端（兼容模式下为第三方 B 端）按需请求数据，而不是网关无条件推送：

数据传输继续采用 **WS/WebSocket + UTF-8 JSON**。默认本机策略仅监听 `127.0.0.1`，由本机 HTTP 页面发起连接并校验固定 Origin；无需物理网线，也不改变消息字段、请求动作或 600 ms 批量节奏。兼容有线策略继续遵守已发布的专用网络约束。当前对外契约见版本台账登记的北向协议；本次拓扑调整未改写已锁定对外文档。

| 请求 | 用途 | 返回行为 |
| --- | --- | --- |
| `getLatest` | 获取注意力、放松度等当前值 | 一请求一响应 |
| `subscribe` | 连续展示原始波形或脑波节律 | 确认后每 600 ms 返回一组/批数据 |
| `unsubscribe` | 停止连续数据 | 确认响应 |
| `getStatus` | 查询连接、信号、电量和当前模式 | 状态响应 |

网关所有输出统一为 `{protocolVersion, code, data, message}`：当前 `protocolVersion` 固定为 `1.0`，`code=200` 成功，非 `200` 时 `message` 为错误原因。连续数据事件的 `data` 中包含 `gatewayBootId`、`mode`（`live`/`replay`）、`subjectId`、`timestampMs`、`valid` 与 `payload`。当前为单耳机、单网关实例，不传 `deviceId` 或 `gatewayId`。原始波形和节律数据使用批量 JSON 或二进制帧，避免单采样点逐条 JSON 发送。

北向消息名称、字段和 WebSocket 传输以版本台账登记的当前对外协议为准；设备采样率、每 600 ms 实际样本数等未确认值继续配置化或由运行日志实测，不在 README 中假定。

## SDK 适配评估

| SDK | 结论 | 依据与处理方式 |
| --- | --- | --- |
| [Enter-Biomodule-BLE-PC-SDK](https://github.com/Entertech/Enter-Biomodule-BLE-PC-SDK) | **仅保留历史兼容** | 不属于当前银河麒麟耳机 USB 串口实现或验收范围。 |
| [AffectiveCloud-Algorithm-SDK](https://github.com/Entertech/AffectiveCloud-Algorithm-SDK) | **适配 x86 Linux 网关的算法层，目标麒麟仍须真实数据 POC** | C++ SDK 提供双通道 `appendEEG`、单通道 `appendSCEEG`、`appendHR` 及注意力、放松度、脑波等报表；它要求 C++17、Eigen3、NumCpp。仓库部署流程已能构建 bridge，但银河麒麟目标机仍须用真实串口原始字节验证构建、输入分组、性能和结果。 |

算法 SDK 还带有 Python 实现，但其依赖固定在 TensorFlow 1.8、Keras 2.2、NumPy 1.16 等较旧版本。首期网关优先选 C++ 实现；不要在未完成独立环境验证的情况下将该 Python 环境直接用于生产镜像。

## 实现边界

- 网关处理耳机 USB 串口协议、数据解析、重连、时间戳、缓存、算法调用和北向适配。
- 实时采集时，网关分别保存设备原始数据与算法结果，并保留可关联的录制会话和时间戳；当前串口数据源不读取这些录制数据做回放。
- 默认由同机浏览器展示；兼容模式下第三方 B 端主机处理展示与保存，其后续服务端上传不属于网关边界。
- 当前银河麒麟交付只使用本机回环 HTTP/WS；旧专网兼容代码不属于本次验收。

## 上线前 POC 与验收

1. 在最终 N100/N150 + 银河麒麟镜像上验证多 TTY 遍历、已有合法 28 字节流直接进入 `validated` 并跳过 ACK/`0xE1`、静默候选在算法 ready 后直接 E1，以首个完整合法帧进入 `validated`、无响应 `0xE1`/`0xE0` 写入、数据超时、分帧、丢包统计、拔插重连和长时间运行。
2. 在同一环境构建算法 C++ bridge，固定 Eigen3/NumCpp/CMake/编译器版本，并以真实串口原始字节验证输入长度、字节序、分组、性能和结果。
3. 使用同机浏览器覆盖实时、设备离线/恢复和浏览器断线重连，并确认耳机离线及存在历史录制时均不会产生 replay；若进入 M2 兼容阶段，再单独完成头环录播和专网 B 端联调。
4. 完成启动、systemd、日志轮转、一键诊断导出、升级和回滚演练，交付版本号、配置哈希、操作手册和问题日志。

## 文档

- [NeuroBridge 项目结构与多系统接入 PRD](doc/tech/NeuroBridge项目结构与多系统接入/NeuroBridge项目结构与多系统接入_PRD.md)：统一架构、固定系统映射与分阶段验收基线。
- [NeuroBridge 项目结构与多系统接入技术方案](doc/tech/NeuroBridge项目结构与多系统接入/NeuroBridge项目结构与多系统接入_技术方案.md)：领域模型、Ports、Profile、组合根、存储和迁移方案。
- [银河麒麟 V10 耳机 USB 串口接入 PRD](doc/tech/%E9%93%B6%E6%B2%B3%E9%BA%92%E9%BA%9FV10%E8%80%B3%E6%9C%BAUSB%E4%B8%B2%E5%8F%A3%E6%8E%A5%E5%85%A5_PRD.md)：当前内部需求基线。
- [银河麒麟 V10 耳机 USB 串口接入技术方案](doc/tech/%E9%93%B6%E6%B2%B3%E9%BA%92%E9%BA%9FV10%E8%80%B3%E6%9C%BAUSB%E4%B8%B2%E5%8F%A3%E6%8E%A5%E5%85%A5_%E6%8A%80%E6%9C%AF%E6%96%B9%E6%A1%88.md)：当前内部实现基线。
- [头环蓝牙网关对接方案 v0.1](doc/tech/%E5%A4%B4%E7%8E%AF%E8%93%9D%E7%89%99%E7%BD%91%E5%85%B3%E5%AF%B9%E6%8E%A5%E6%96%B9%E6%A1%88_v0.1.md)：历史方案，不属于当前银河麒麟耳机 USB 串口需求。

### 对外文档

- [头环数据网关安装部署指南 v0.1（未发布评审稿）](doc/tech/对外/头环数据网关安装部署指南/头环数据网关安装部署指南_v0.1.md)：适用于 `codex/linux-headband-ethernet-gateway`，补充从新系统到 B 端验收的首次部署流程；CI 单独生成评审 PDF，不进入正式交付 ZIP。

- [头环数据网关北向网络协议 v0.2](doc/tech/%E5%AF%B9%E5%A4%96/%E5%A4%B4%E7%8E%AF%E6%95%B0%E6%8D%AE%E7%BD%91%E5%85%B3%E5%8C%97%E5%90%91%E7%BD%91%E7%BB%9C%E5%8D%8F%E8%AE%AE/%E5%A4%B4%E7%8E%AF%E6%95%B0%E6%8D%AE%E7%BD%91%E5%85%B3%E5%8C%97%E5%90%91%E7%BD%91%E7%BB%9C%E5%8D%8F%E8%AE%AE_v0.2.md)：已发布，B 端 WS/JSON 接入契约与联调验收。
- [头环数据采集包格式说明 v0.1](doc/tech/%E5%AF%B9%E5%A4%96/%E5%A4%B4%E7%8E%AF%E6%95%B0%E6%8D%AE%E9%87%87%E9%9B%86%E5%8C%85%E6%A0%BC%E5%BC%8F%E8%AF%B4%E6%98%8E/%E5%A4%B4%E7%8E%AF%E6%95%B0%E6%8D%AE%E9%87%87%E9%9B%86%E5%8C%85%E6%A0%BC%E5%BC%8F%E8%AF%B4%E6%98%8E_v0.1.md)：已发布，一键保存 ZIP 的文件、字段和校验规则。
- [头环数据网关 SSH 运维操作指南 v1.0](doc/tech/%E5%AF%B9%E5%A4%96/%E5%A4%B4%E7%8E%AF%E6%95%B0%E6%8D%AE%E7%BD%91%E5%85%B3%20SSH%20%E8%BF%90%E7%BB%B4%E6%93%8D%E4%BD%9C%E6%8C%87%E5%8D%97/%E5%A4%B4%E7%8E%AF%E6%95%B0%E6%8D%AE%E7%BD%91%E5%85%B3%20SSH%20%E8%BF%90%E7%BB%B4%E6%93%8D%E4%BD%9C%E6%8C%87%E5%8D%97_v1.0.md)：已发布，供经授权的外部运维人员操作网关 SSH 服务。
- [头环数据网关有线网络配置指南 v1.0](doc/tech/%E5%AF%B9%E5%A4%96/%E5%A4%B4%E7%8E%AF%E6%95%B0%E6%8D%AE%E7%BD%91%E5%85%B3%E6%9C%89%E7%BA%BF%E7%BD%91%E7%BB%9C%E9%85%8D%E7%BD%AE%E6%8C%87%E5%8D%97/%E5%A4%B4%E7%8E%AF%E6%95%B0%E6%8D%AE%E7%BD%91%E5%85%B3%E6%9C%89%E7%BA%BF%E7%BD%91%E7%BB%9C%E9%85%8D%E7%BD%AE%E6%8C%87%E5%8D%97_v1.0.md)：已发布，供网关交付与 B 端运维人员配置专用有线直连网络。
- [数据网关 Windows 部署与使用指南 v1.1](doc/tech/%E5%AF%B9%E5%A4%96/%E6%95%B0%E6%8D%AE%E7%BD%91%E5%85%B3%20Windows%20%E9%83%A8%E7%BD%B2%E4%B8%8E%E4%BD%BF%E7%94%A8%E6%8C%87%E5%8D%97/%E6%95%B0%E6%8D%AE%E7%BD%91%E5%85%B3%20Windows%20%E9%83%A8%E7%BD%B2%E4%B8%8E%E4%BD%BF%E7%94%A8%E6%8C%87%E5%8D%97_v1.0.md)：已发布，供 B 端接入与现场操作人员安装网关、确认运行状态并使用本机控制台。

### 其他文档

- [B 端联调网页](web/b-client-test/README.md)：零依赖的浏览器测试工具，可连接网关并测试 `getStatus`、`getLatest`、`subscribe`、`unsubscribe`。

对外文档的 Markdown 独立保存在仓库，PDF 不提交仓库。包含未发布对外文档的 PR 必须先在该 PR 分支运行 `python3 tools/mark-external-documents-published.py --date <YYYY-MM-DD>`，将文档状态、发布日期、摘要、发布记录和锁定区间一并提交；CI 会拒绝仍含未发布文档的 PR，因而无法合入 `master`。PR 的 CI 生成候选包；状态已发布且已锁定的 PR 合入 `master` 后，CI 将四份外部 Markdown 转为 PDF，连同可直接双击打开的 `b-client-test/index.html` 联调网页打包为 `neurobridge-external-documents.zip` 并上传正式 Artifact。`candidate` 仅生成候选包；`publish` 仅允许所有打包源文档已发布且锁定时执行。如需额外生成某个历史正式版本或内部预发布版的北向协议 PDF，可填写 `protocol_version` 和 `protocol_stage`。版本清单见 [版本台账](neurobridge/version_registry.toml)。

安装排错：麒麟安装日志保存在 `/var/log/neurobridge-bootstrap`，服务尚未创建时也可运行 `sudo /usr/lib/neurobridge-bootstrap/export-install-logs.sh --output-dir "$HOME/下载"`。Windows 10/11 64 位包随附 `install-with-logs.ps1`，通过 `-Installer` 安装、`-Export` 导出失败日志；Windows 7 和 32 位目标不参与构建与交付。交付 ZIP 的 PDF 副本使用英文文件名，原名称对照保存在 `metadata/document-filenames.json`。上述源码与模拟验证不替代银河麒麟现场验收。

交付总 ZIP 根目录生成 `bundle-directory-guide.pdf`（《NeuroBridge 交付包目录说明》），面向部署人员介绍各层压缩包、解压顺序、安装包与部署指南、日志工具及校验资料的位置；按本次实际清单生成，不再交付根目录 `PRD.md`。PDF 使用英文文件名、中文标题和正文。需求见[发布工作流 PRD](doc/product/发布工作流%20PRD.md#26-总包根目录-pdf-目录说明)。

详细支持表及扩展架构影响见[发布工作流 PRD §2.7–2.9](doc/product/发布工作流%20PRD.md#27-系统与-cpu-架构支持表)：Windows 保持 10/11 x86_64；麒麟新增六个源码构建候选 Profile，与既有 x86_64 共用引导入口。运行时依然须匹配 CPU/ABI，各架构分别完成算法与 USB 真机验收；既有 N100/N150 结果不能替代其他架构。

两平台安装与运行日志导出新增诊断上下文，记录软件版本/源码提交、安装包版本/提交、导出时间、系统版本/构建、CPU 架构/位数、Python 与脚本环境。Windows 为 `diagnostic-context.json`（安装导出带时间后缀），麒麟为 `diagnostic-context.txt`；磁盘部署版本与安装包版本分别标识，缺失值写 `unknown`，服务状态另行记录。网页日志下载也附运行进程的版本和环境；配置正文、全量环境变量和录制数据不导出。麒麟运行日志可用 `sudo /opt/neurobridge/kylin/export-logs.sh --output-dir "$HOME/下载"` 导出；Windows 用安装目录 `windows/export-logs.ps1 -OutputDirectory C:\Temp`。
