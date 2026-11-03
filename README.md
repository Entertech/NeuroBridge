# NeuroBridge

NeuroBridge 是将回车科技头环数据接入第三方 B 端主机的跨平台 PC 网关。网关核心负责设备采集、数据与算法处理、北向协议适配、录播和运行维护；第三方 B 端主机负责展示与保存。首期交付目标是 Ubuntu x86_64，macOS 仅用于真实设备 POC，Windows 尚未完成网关运行验证。

当前对接方案详见：[头环蓝牙网关对接方案 v0.1](doc/tech/%E5%A4%B4%E7%8E%AF%E8%93%9D%E7%89%99%E7%BD%91%E5%85%B3%E5%AF%B9%E6%8E%A5%E6%96%B9%E6%A1%88_v0.1.md)。

## 可运行网关与部署

仓库现已包含可部署的 Python 网关：`neurobridge/`。它按 v0.1 协议提供 WebSocket 服务、以 600 ms 窗口批量发送数据、管理订阅与自动录播。实时采集时，原始 EEG、心率和每类算法指标按会话分文件持久化；网页可将一个会话导出为 ZIP。网关按设备通信规范处理 `FF31`、`FF51` 原始字节，落盘和算法调用前不解包或改写；北向仅按协议发送允许的原始流。

Ubuntu 24.04 x86_64 网关可先用公开 HTTPS 地址匿名获取本仓库源码，不需要 GitHub 账号或密码：

```bash
git clone https://github.com/Entertech/NeuroBridge.git
cd NeuroBridge
./linux/prepare-ubuntu24.04-environment.sh  # 一次性联网准备
# 此处可断开互联网
./linux/update-ubuntu.sh
sudoedit /etc/neurobridge/gateway.toml
```

`git clone`/`git pull` 仅用于人为获取新版本源码。源码已在目标机后，安装、C++ bridge 构建、Python 包更新和运行均不访问 GitHub、APT、PyPI 或其他互联网服务。`update-ubuntu.sh` 只部署当前目录已有的源码；更新代码时，先自行执行匿名 `git pull --ff-only`，再运行该脚本：

```bash
git pull --ff-only
./linux/update-ubuntu.sh
```

脚本会自动申请 `sudo` 权限，将当前工作树同步到 `/opt/neurobridge`、从本地源码更新 Python 包、重建算法 bridge、重新加载 systemd 单元并重启网关。网关重启会使现有 B 端连接断开，B 端需重新建连并订阅。

需要远程运维时，Ubuntu 环境准备会安装但不启用 OpenSSH；部署后执行 `sudo ./linux/setup-ssh-operations.sh` 即可交互式完成运维账号密码、私有监听地址和来源网段配置。一键配置会把当前源码同步为运维账号自己的固定项目目录 `~/NeuroBridge`。SSH 场景中的源码更新只能使用 `neurobridge-ops update`：该命令默认从 `origin` 更新当前分支，也可使用 `neurobridge-ops update --branch <分支名>` 选择批准分支，使用 `neurobridge-ops branch` 查询当前分支；仅快进更新后部署重启，运维人员不应直接运行 Git。还可使用 `status`、`logs --follow`、`audit` 和 `restart`。`audit` 不记录密码或业务原始数据。由于该账号维护的代码随后会以 root 安装，必须把它作为网关管理员凭据管理。完整命令和回滚步骤见 [Ubuntu 网关部署与运行教程](linux/README.md#41-启用-ssh-运维入口可选) 与 [SSH 运维和录播联调指南](doc/tech/网关%20SSH%20运维与%20B%20端录播联调指南.md)。

部署脚本会创建 `neurobridge` 服务账户、运行目录和 systemd 服务。先在联网阶段运行 `linux/prepare-ubuntu24.04-environment.sh`，它会安装 Ubuntu 系统依赖并创建含 `bleak`、`websockets` 的 Python 运行环境；随后可断网。算法 SDK 与 NumCpp 源码已经随仓库保存在 `third_party/`，不再要求填 bridge 路径或另行下载 SDK。`update-ubuntu.sh` 在断网阶段只使用本地源码。安装和每次 systemd 启动时，在确认的物理以太网口追加默认直连地址 `192.168.88.10/24`，已有局域网/DHCP 地址、默认路由、DNS 和 IPv6 均保留；无网线或无 DHCP 也可准备地址。独立辅助服务默认每 5 秒检查并补回被网络重连移除的固定地址。无网口、多网口未明确选择、地址或其他网口路由冲突时拒绝猜测并记录原因。专用地址仅接受 RFC1918 IPv4 网段，现场不同参数仍须在 `/etc/neurobridge/gateway.toml` 确认配置。B 端网卡需设置同网段地址，例如 `192.168.88.20/24`；双击静态联调页后默认连接 `ws://192.168.88.10:8765/neurobridge/v1/ws` 并查询状态。当 `[recording].replay_enabled = true` 时，`replay_recording_id` 可留空，网关离线时会自动选择最新的非空、可读取历史会话。示例配置在 [config/gateway.toml.example](config/gateway.toml.example)。开发机可使用：

```bash
python3 -m venv .venv
.venv/bin/pip install --no-deps -e .  # unit tests do not need BLE hardware
.venv/bin/pip install websockets==12.0
.venv/bin/python -m unittest discover -s tests -v
```

Flowtime 接入层以 Bleak 实现扫描/连接/通知/断连重连：只要未连接便扫描匹配 `device_name` 与 `model_nbr_uuid` 的设备，并选择 RSSI 最高者。连接后先初始化新算法会话和录制会话，再订阅 `FF31`、`FF32`、`FF51` 通知、向 `FF21` 写入 `0x05`，最后才公布 `connected`，确保启动期间的首包可落盘；初始化失败时关闭对应会话；停止时写入 `0x06`。`FF31` 原始 EEG 与 `FF51` 原始心率均会持久化，`FF51` 用于北向 `hr.raw`；算法输入保留原始字节序和完整窗口。首次 Ubuntu 安装会以锁定版本自动构建并安装行 JSON C++ bridge 到 `/usr/local/lib/neurobridge/neurobridge_affective_bridge`，因此部署配置不需要填写算法命令；`algorithm.enabled` 默认开启，也是唯一启停项。macOS 仅完成源码构建及合成输入烟雾测试；SDK 输入分组、真实 Flowtime 数据输出和 Ubuntu x86_64 构建尚未验证，现场仍须根据状态、日志和真实数据验证算法结果。

佩戴状态按设备协议表 4-4 解释：FF32 两字节均为零表示佩戴且接触良好，任一非零表示未佩戴或接触不良，长度不符为未知。电量按表 4-2：3.1 V 对应 0%、4.1 V 对应 100%，单字节 `0x52` 对应 3.92 V，即 82%；有效值为 0–100，其他数据返回空。连接建立后尽力读取一次电量，并接收可选通知；电量读取或订阅失败只记诊断，不中断 EEG/HR 采集。FF32 与电量原始字节另存 `raw/wear.jsonl`、`raw/battery.jsonl`，包含会话及窗口时间，不进入 EEG/HR 算法窗口或北向原始数据录播。旧录制格式仍可读取，断线后清空当前设备遥测。本轮依据协议实现并完成合成回归，真实头环及 Ubuntu 现场验收待完成。

SDK 的固定来源和算法启用 POC 见 [sdk.lock](sdk.lock) 与 [算法 SDK 接入 POC](doc/tech/%E7%AE%97%E6%B3%95%20SDK%20%E6%8E%A5%E5%85%A5%20POC.md)。

运行时代码按职责组织：`neurobridge/ble/` 负责 Flowtime 扫描、连接、通知与原始字节窗口；`neurobridge/algorithm/` 隔离算法 SDK bridge；`neurobridge/business/` 负责订阅、状态、录制与录播；`neurobridge/northbound/` 仅负责 WS/WebSocket 传输。

订阅事件通过有上限的独立队列发送，实时采集、落盘、蓝牙状态更新和录播时钟均不等待网络发送。发送失败、超时或积压时关闭对应 B 端连接，其他连接和采集继续；恢复后须重新连接、查询状态并订阅，不自动补传。发送期限与队列上限可在 `[server]` 配置。运行日志增加连接关联标识、发送失败/积压、蓝牙阶段与收包摘要；Linux 还会只读采集网卡、地址、路由与邻居状态变化。网关本地执行 `sudo ./linux/collect-runtime-diagnostics.sh` 可打包最近 72 小时系统日志和现有轮转日志，详见 [运行诊断](linux/README.md#运行诊断与日志采集)。自动回归覆盖故障隔离；Ubuntu 实机蓝牙及拔插网线验收仍需现场完成。

网关本机无需安装 `neurobridge-ops` 也可管理录播：

```bash
sudo /opt/neurobridge/venv/bin/python -I -m neurobridge.replay_control enable   # 允许录播
sudo /opt/neurobridge/venv/bin/python -I -m neurobridge.replay_control disable  # 禁止录播
sudo /opt/neurobridge/venv/bin/python -I -m neurobridge.replay_control status   # 查询配置
```

开关持久化到 `[recording].replay_enabled`（默认 `true`）。运行中的网关会重启生效，B 端须重新连接和订阅；已停止时仅保存配置。开启仍由取数请求按需启动录播，实时头环优先；禁止保留历史文件，生效后 `mode` 为 `live`，即使头环尚未连接。`status` 的 `applied=null` 表示只查询，不能据此确认运行时已应用策略。已配置 SSH 运维时才可使用 `neurobridge-ops replay ...`。模块缺失、部署更新和完整输出说明见 [本机录播操作](linux/README.md#61-在网关本机管理录播无需-ssh)。

历史会话中存在无效 JSON、编码错误或无法读取的数据文件时，录播读取排除整个会话并尝试其他正常会话，保留原文件。`getStatus` 继续返回状态；全部会话不可用时取数返回既有录播不可用错误。错误日志包含会话、文件和 JSON/编码错误行号，便于定位；真实采集状态查询不读取历史录播。详见 [内部诊断说明](doc/tech/网关%20SSH%20运维与%20B%20端录播联调指南.md)。

Windows B 端将静态包完整解压后，双击 `b-client-test/configure-wired-ip.bat`，允许 UAC 并按需选择有线网卡，追加 `192.168.88.20/24`，再打开 `index.html` 自动连接默认网关。脚本记录本地配置日志，DHCP 共存检查失败时停止，不以重置原网络作为回退；详见 [B 端文件夹说明](web/b-client-test/README.md)。

采集窗口和算法请求串行处理；算法交换超时或取消后终止旧进程，防止迟到响应写入新窗口，直到下一设备会话初始化才恢复算法，原始通知继续落盘。`[algorithm].evaluation_timeout_seconds` 默认 2 秒；`stop_timeout_seconds` 默认 1 秒，终止超时后升级强制结束，再最多等待同一期限，不阻塞后续蓝牙重连。断线回调兼容锁定的 Bleak backend 对象，并保留轮询兜底。日志包含断线检测来源、阶段、扫描 RSSI、电量/佩戴快照、最后收包间隔、待处理回调数和清理耗时；控制器原因码需在测试前显式启动 [HCI 断连原因采集](linux/README.md#hci-断连原因采集)，仅持久化断连元数据。上述能力已覆盖模拟故障回归，Ubuntu 实机验收仍待进行。

## 仓库结构

- `neurobridge/`：跨平台网关核心，不依赖 macOS、Windows 或 Linux 的启动方式。
- `web/`：纯静态网页；`capture/` 是采集控制台，`b-client-test/` 是 B 端联调页，可由任意平台的本地服务或静态服务器托管。
- `mac/`：仅 macOS POC 的启动器、蓝牙验证、原生算法 bridge 与本机配置模板。
- `linux/`：Ubuntu 部署脚本、systemd 单元与日志轮转配置。
- `windows/`：Windows 平台接入说明；当前没有经过验证的 Windows 服务启动器或部署脚本。

采集控制台也可直接双击 [`web/capture/index.html`](web/capture/index.html)，默认访问 `http://192.168.88.10:8766`；开始扫描、停止采集和保存 ZIP 全部由网关执行，本机无需运行 Python。保存当前会话会先停止采集并完成录制，HTTP/WS 服务继续运行。需更新网关并启用采集控制与 BLE 配置，详见 [采集控制台说明](web/capture/README.md)。可用 `python3 tools/build-static-console.py` 生成包含两个页面及 Windows 配置脚本的独立 ZIP。

网页不再放入平台目录。macOS POC 仍通过同样的 `/capture/` 与 `/b-client/` URL 提供页面，因此已有书签和使用流程不受影响。

## 按平台使用

| 场景 | 入口 | 当前状态 |
| --- | --- | --- |
| Ubuntu x86_64 网关部署 | [`linux/install-ubuntu.sh`](linux/install-ubuntu.sh) | 首期部署入口；安装为 systemd 服务。 |
| macOS 真实头环 POC | [`mac/start-poc.command`](mac/start-poc.command) | 一键启动本机采集、算法 bridge 和控制台；详见 [`mac/README.md`](mac/README.md)。 |
| Windows 网关 | [`windows/README.md`](windows/README.md) | 尚未完成设备、算法和后台服务验证，不能作为交付部署入口。 |
| 采集控制台网页 | [`web/capture/`](web/capture/) | 直接打开静态页，远程查看网关状态、启停采集和保存 ZIP。 |
| B 端联调网页 | [`web/b-client-test/`](web/b-client-test/) | 平台无关纯静态资源；直接打开 `index.html` 即可使用。 |

仅使用 B 端联调网页时，直接双击 [`web/b-client-test/index.html`](web/b-client-test/index.html) 或将其拖入浏览器；无需启动 HTTP 服务。然后填写专用有线网络内的网关 WebSocket 地址。该网页不代替网关进程，也不会访问本机蓝牙。订阅 EEG、HR 和状态后，可查看左右/单通道脑波、脑波频段能量，以及放松度、压力、心率、HRV、注意力和 Flow；电量与佩戴状态兼容状态查询及事件。详见 [页面使用说明](web/b-client-test/README.md#波形频段能量与指标)。

## 首期交付结论

- 目标硬件：N100/N150 x86 小主机，8 GB 内存、256 GB SSD、千兆网口（双网口优先）、经实机验证可稳定连接目标头环的蓝牙 5.x 芯片。
- 目标系统：Ubuntu LTS/Linux。网关不需要运行 Android；Android 或浏览器仅作为第三方展示端。
- 链路：`头环 → BLE → 网关 → 有线以太网 → 第三方 B 端主机`。
- 运行方式：实时模式和录播模式均为交付范围；头环未连接、允许录播且存在可读历史会话时，B 端首次 `subscribe` 或 `getLatest` 会启动网关级录播流程，并以 `mode="replay"` 标记数据来源。录播优先使用部署配置明确指定且有效的会话；否则自动遍历录播目录，选择最新的非空历史会话。后续订阅加入该流程的当前进度。最后一个 B 端 WebSocket 连接断开时，网关停止录播并重置进度；仍有连接时，文件播放到末尾会从首条重新循环。头环重连时自动停止录播并回到实时。网关应开机自启，现场无需操作。
- 运维与交付：Ubuntu 安装脚本将网关注册为 systemd 开机自启服务；运行日志持久化并每日轮转。受控专用有线网络上的下载服务可导出已结束录播 ZIP 与运行日志快照，地址和端口由部署配置确认。B 端地址分配可选静态地址（默认）或独立 DHCP 单元；后者仅自动分配 B 端地址并发布固定网关地址，端口保持固定。
- 演示范围：本次为单人；北向协议仅保留录播关联所需的 `subjectId`。
- 当前设备通信规范定义：EEG `FF31` 为 20 字节大端数据包（2 字节序号与 6 个 24 位采样），心率 `FF51` 为 1 字节，佩戴/接触状态 `FF32` 为 2 字节；北向仍最多按 600 ms 分组，但实际包数由网关运行时记录，不写死。

若现场禁止任何蓝牙，实时链路不能工作；只能改用有线采集、在允许蓝牙的区域采集后录播，或取得场地方对限制范围的书面确认。

## 北向协议要点

第三方 B 端主机按需请求数据，而不是网关无条件推送：

首期北向传输统一采用 **WS/WebSocket + UTF-8 JSON**：网关作为服务端，B 端通过专用有线以太网直连；不使用 TLS、证书或 Token，WS 数据为明文，禁止接入公网、无线网络或不受控局域网。联调前确认两端静态 IP、子网掩码、端口与防火墙放行。连续数据最多每 600 ms 推送一组。请求、数据结构、错误码与验收项见《[头环数据网关北向网络协议 v0.2](doc/tech/%E5%AF%B9%E5%A4%96/%E5%A4%B4%E7%8E%AF%E6%95%B0%E6%8D%AE%E7%BD%91%E5%85%B3%E5%8C%97%E5%90%91%E7%BD%91%E7%BB%9C%E5%8D%8F%E8%AE%AE/%E5%A4%B4%E7%8E%AF%E6%95%B0%E6%8D%AE%E7%BD%91%E5%85%B3%E5%8C%97%E5%90%91%E7%BD%91%E7%BB%9C%E5%8D%8F%E8%AE%AE_v0.2.md)》。

| 请求 | 用途 | 返回行为 |
| --- | --- | --- |
| `getLatest` | 获取注意力、放松度等当前值 | 一请求一响应 |
| `subscribe` | 连续展示原始波形或脑波节律 | 确认后每 600 ms 返回一组/批数据 |
| `unsubscribe` | 停止连续数据 | 确认响应 |
| `getStatus` | 查询连接、信号、电量和当前模式 | 状态响应 |

网关所有输出统一为 `{protocolVersion, code, data, message}`：当前 `protocolVersion` 固定为 `1.0`，`code=200` 成功，非 `200` 时 `message` 为错误原因。连续数据事件的 `data` 中包含 `gatewayBootId`、`mode`（`live`/`replay`）、`subjectId`、`timestampMs`、`valid` 与 `payload`。v0.1 为单头环、单网关实例，不传 `deviceId` 或 `gatewayId`。原始波形和节律数据使用批量 JSON 或二进制帧，避免单采样点逐条 JSON 发送。

最终消息名称、字段和传输协议（TCP、WebSocket、HTTP(S) 等）以双方确认的接口文档为准。协议、采样率、每 600 ms 的样本数和录播规则仍是联调前待确认项。

## SDK 适配评估

| SDK | 结论 | 依据与处理方式 |
| --- | --- | --- |
| [Enter-Biomodule-BLE-PC-SDK](https://github.com/Entertech/Enter-Biomodule-BLE-PC-SDK) | **适配，作为 BLE 接入层的首选 POC** | Python SDK 基于 Bleak 0.19，声明支持 macOS、Linux 和 Windows；可承担 Flowtime 的扫描、连接、通知、启动采集与断线回调。BLE UUID 与数据契约以 [头环蓝牙通信协议](https://entertech.feishu.cn/docs/doccnlmMLpxwY25gJQyiFQmBeRd?from=from_copylink) 为准，不能以 SDK 的旧解包示例推断。Linux 上仍须实机验证。 |
| [AffectiveCloud-Algorithm-SDK](https://github.com/Entertech/AffectiveCloud-Algorithm-SDK) | **适配 x86 Linux 网关的算法层，须完成构建 POC 后上线** | C++ SDK 提供双通道 `appendEEG`、单通道 `appendSCEEG`、`appendHR` 及注意力、放松度、脑波等报表；接口注释以 0.6 秒为默认触发周期，和本网关 600 ms 分组一致。它要求 C++17、Eigen3、NumCpp；仓库当前没有可直接交付的 Linux 产物，需在 Ubuntu x86_64 上补齐依赖、编译并用真实 BLE 原始字节流做结果比对。 |

算法 SDK 还带有 Python 实现，但其依赖固定在 TensorFlow 1.8、Keras 2.2、NumPy 1.16 等较旧版本。首期网关优先选 C++ 实现；不要在未完成独立环境验证的情况下将该 Python 环境直接用于生产镜像。

## 实现边界

- 网关处理头环 BLE 协议、数据解析、重连、时间戳、缓存、算法调用和北向适配。
- 实时采集时，网关分别保存原始 BLE 数据与算法结果，并保留可关联的录制会话和时间戳；录播直接读取这些已保存数据，按原始时间间隔发送。
- 第三方 B 端主机处理展示与保存；其是否继续上传服务端不属于网关边界。
- 使用专用有线以太网直连第三方主机；联调前确认两端静态 IP、子网掩码、WS 端口、连接方向、防火墙放行与断线补传规则。

## 上线前 POC 与验收

1. 在目标 N100/N150 + Ubuntu LTS 上测试 PC BLE SDK：扫描、连接、订阅、断线重连、重启恢复和长时间运行。
2. 在同一环境编译算法 C++ SDK，固定 Eigen3/NumCpp/CMake/编译器版本，并以录制的真实 `FF31`/`FF51` 原始字节验证算法输入长度、字节序、分组方式和 600 ms 触发节奏。
3. 完成北向模拟服务端和真实 B 端主机联调，覆盖实时、录播、网关/服务端离线、恢复补传和异常数据。
4. 在展演网络完成一次全链路演练，交付版本号、配置备份、操作手册和问题日志。

## 文档

- [头环蓝牙网关对接方案 v0.1](doc/tech/%E5%A4%B4%E7%8E%AF%E8%93%9D%E7%89%99%E7%BD%91%E5%85%B3%E5%AF%B9%E6%8E%A5%E6%96%B9%E6%A1%88_v0.1.md)：架构、边界、待决项和验收要求。

### 对外文档

- [头环数据网关安装部署指南 v0.1（未发布评审稿）](doc/tech/对外/头环数据网关安装部署指南/头环数据网关安装部署指南_v0.1.md)：适用于 `codex/linux-headband-ethernet-gateway`，补充从新系统到 B 端验收的首次部署流程；CI 单独生成评审 PDF，不进入正式交付 ZIP。

- [头环数据网关北向网络协议 v0.2](doc/tech/%E5%AF%B9%E5%A4%96/%E5%A4%B4%E7%8E%AF%E6%95%B0%E6%8D%AE%E7%BD%91%E5%85%B3%E5%8C%97%E5%90%91%E7%BD%91%E7%BB%9C%E5%8D%8F%E8%AE%AE/%E5%A4%B4%E7%8E%AF%E6%95%B0%E6%8D%AE%E7%BD%91%E5%85%B3%E5%8C%97%E5%90%91%E7%BD%91%E7%BB%9C%E5%8D%8F%E8%AE%AE_v0.2.md)：已发布，B 端 WS/JSON 接入契约与联调验收。
- [头环数据采集包格式说明 v0.1](doc/tech/%E5%AF%B9%E5%A4%96/%E5%A4%B4%E7%8E%AF%E6%95%B0%E6%8D%AE%E9%87%87%E9%9B%86%E5%8C%85%E6%A0%BC%E5%BC%8F%E8%AF%B4%E6%98%8E/%E5%A4%B4%E7%8E%AF%E6%95%B0%E6%8D%AE%E9%87%87%E9%9B%86%E5%8C%85%E6%A0%BC%E5%BC%8F%E8%AF%B4%E6%98%8E_v0.1.md)：已发布，一键保存 ZIP 的文件、字段和校验规则。
- [头环数据网关 SSH 运维操作指南 v1.0](doc/tech/%E5%AF%B9%E5%A4%96/%E5%A4%B4%E7%8E%AF%E6%95%B0%E6%8D%AE%E7%BD%91%E5%85%B3%20SSH%20%E8%BF%90%E7%BB%B4%E6%93%8D%E4%BD%9C%E6%8C%87%E5%8D%97/%E5%A4%B4%E7%8E%AF%E6%95%B0%E6%8D%AE%E7%BD%91%E5%85%B3%20SSH%20%E8%BF%90%E7%BB%B4%E6%93%8D%E4%BD%9C%E6%8C%87%E5%8D%97_v1.0.md)：已发布，供经授权的外部运维人员操作网关 SSH 服务。
- [头环数据网关有线网络配置指南 v1.0](doc/tech/%E5%AF%B9%E5%A4%96/%E5%A4%B4%E7%8E%AF%E6%95%B0%E6%8D%AE%E7%BD%91%E5%85%B3%E6%9C%89%E7%BA%BF%E7%BD%91%E7%BB%9C%E9%85%8D%E7%BD%AE%E6%8C%87%E5%8D%97/%E5%A4%B4%E7%8E%AF%E6%95%B0%E6%8D%AE%E7%BD%91%E5%85%B3%E6%9C%89%E7%BA%BF%E7%BD%91%E7%BB%9C%E9%85%8D%E7%BD%AE%E6%8C%87%E5%8D%97_v1.0.md)：已发布，供网关交付与 B 端运维人员配置专用有线直连网络。

### 其他文档

- [B 端异常消息与场景排查表](doc/tech/B端异常消息与场景排查表.md)：按业务 JSON、连接异常与页面提示分类，列出实际 `message`、触发场景和处理方式；内部联调参考。
- [静态采集控制台](web/capture/README.md)：直接打开、远程启停和完整 ZIP 下载。
- [B 端联调网页](web/b-client-test/README.md)：零依赖的浏览器测试工具，可连接网关并测试 `getStatus`、`getLatest`、`subscribe`、`unsubscribe`。

对外文档的 Markdown 独立保存在仓库，PDF 不提交仓库。包含未发布对外文档的 PR 必须先在该 PR 分支运行 `python3 tools/mark-external-documents-published.py --date <YYYY-MM-DD>`，将文档状态、发布日期、摘要、发布记录和锁定区间一并提交；CI 会拒绝仍含未发布文档的 PR，因而无法合入 `master`。PR 的 CI 生成候选包；状态已发布且已锁定的 PR 合入 `master` 后，CI 将四份外部 Markdown 转为 PDF，连同可直接双击打开的 `b-client-test/index.html` 联调网页打包为 `neurobridge-external-documents.zip` 并上传正式 Artifact。`candidate` 仅生成候选包；`publish` 仅允许所有打包源文档已发布且锁定时执行。如需额外生成某个历史正式版本或内部预发布版的北向协议 PDF，可填写 `protocol_version` 和 `protocol_stage`。版本清单见 [版本台账](neurobridge/version_registry.toml)。
