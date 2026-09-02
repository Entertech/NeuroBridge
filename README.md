# NeuroBridge

NeuroBridge 是将回车科技头环数据接入本机浏览器或兼容第三方 B 端主机的跨平台 PC 网关。网关核心负责设备采集、数据与算法处理、北向协议适配、录播和运行维护。当前正式目标为 N100/N150 x86_64 主机上的银河麒麟 V10；Ubuntu x86_64 保留为兼容与回归基线，macOS 用于真实设备 POC，Windows 尚未完成网关运行验证。

当前对接方案详见：[头环蓝牙网关对接方案 v0.1](doc/tech/%E5%A4%B4%E7%8E%AF%E8%93%9D%E7%89%99%E7%BD%91%E5%85%B3%E5%AF%B9%E6%8E%A5%E6%96%B9%E6%A1%88_v0.1.md)。设备侧 BLE、USB 串口与后续原生 USB 的设计、实现状态和验收门禁见[多传输数据源接入技术方案](doc/tech/%E5%A4%B4%E7%8E%AF%E7%BD%91%E5%85%B3%E5%A4%9A%E4%BC%A0%E8%BE%93%E6%95%B0%E6%8D%AE%E6%BA%90%E6%8E%A5%E5%85%A5_%E6%8A%80%E6%9C%AF%E6%96%B9%E6%A1%88.md)。

## 可运行网关与部署

仓库现已包含可部署的 Python 网关：`neurobridge/`。它通过策略模式提供两种可切换的访问拓扑：当前默认 `local_browser` 让同一台网关主机上的浏览器通过 `127.0.0.1` 使用 WebSocket；兼容策略 `wired_b_side` 保留独立 B 端主机的专用网线访问。两种策略复用相同的消息契约、600 ms 批量数据、订阅、录播和持久化逻辑。

设备侧也使用独立策略：`data_source.type = "serial"` 选择当前 USB 派生 TTY 方案，`"bluetooth"` 切回 Flowtime BLE，一次只运行一种；`"usb"` 仅为原生 HID/Bulk/Interrupt 预留，协议未确认时会拒绝启动并提示改用串口。串口策略会稳定排序并逐个探测 `/dev/serial/by-id/`、`ttyACM*`、`ttyUSB*`，收到首个固定握手后立即 ACK、停止遍历并进入 `connected`；银河麒麟一键流程使用项目内锁定的 CMake 3.31.6、目标系统 Eigen 3.3.7 与仓库源码，将 C++ 算法 bridge 构建到 `.runtime/algorithm/`，进程自检成功后才启用配置。本地算法准备成功后才进入 `validating` 并发送 `0xE1`，只有收到单字节 `0x01` 应答才进入 `validated` 并读取、转发实时数据。超时、`AA` 握手残片、完整握手或任何其他应答均进入 `validation_failed` 并记录脱敏分类；关闭串口后为 `disconnected`，从未找到目标设备则回到 `not_connected`。`0xE0` 也只把单字节 `0x01` 记为成功，但无论停止应答是否成功都释放资源。16 位大端序号用于输出累计和区间丢包率，日志不记录控制响应原文或完整脑波帧。完整 28 字节设备帧只写入受保护的内部录制目录，不进入现有采集包、诊断包或北向消息。

银河麒麟 x86_64 项目的日常入口不会自动访问 Git。耳机 USB 已连接时运行入口并输入 `1`；已有完整配置时也可输入 `2` 直接启动：

```bash
bash linux/neurobridge-kylin-bootstrap.sh
```

旧设备无法 `git pull` 时，在开发机对已提交且测试通过的分支执行 `./tools/build-kylin-offline-update.sh`，只传输生成的 `neurobridge-kylin-offline-update.run`。目标机在项目根目录运行该单文件；它校验内嵌 Git bundle 后从本地快进代码、保留 `.runtime` 与现场数据，并自动打开菜单，全程不访问远端。完整部署、日志字段和导出方法见[银河麒麟 V10 内部手册](doc/tech/麒麟V10网关运行与串口联调内部文档.md)。

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

需要远程运维时，Ubuntu 环境准备会安装但不启用 OpenSSH；部署后执行 `sudo ./linux/setup-ssh-operations.sh` 即可交互式完成运维账号密码、私有监听地址和来源网段配置。一键配置会把当前源码同步为运维账号自己的固定项目目录 `~/NeuroBridge`。SSH 场景中的源码更新只能使用 `neurobridge-ops update`：该命令固定切换至 `master`、快进拉取批准的上游版本并部署重启，运维人员不应直接运行 Git。还可使用 `status`、`logs --follow`、`audit` 和 `restart`。`audit` 不记录密码或业务原始数据。由于该账号维护的代码随后会以 root 安装，必须把它作为网关管理员凭据管理。完整命令和回滚步骤见 [Ubuntu 网关部署与运行教程](linux/README.md#41-启用-ssh-运维入口可选) 与 [SSH 运维和录播联调指南](doc/tech/网关%20SSH%20运维与%20B%20端录播联调指南.md)。

部署脚本会创建 `neurobridge` 服务账户、运行目录和 systemd 服务。先在联网阶段运行 `linux/prepare-ubuntu24.04-environment.sh`，它会安装 Ubuntu 系统依赖并创建含 `bleak`、`websockets` 的 Python 运行环境；随后可断网。算法 SDK 与 NumCpp 源码已经随仓库保存在 `third_party/`，不再要求填 bridge 路径或另行下载 SDK。`update-ubuntu.sh` 在断网阶段只使用本地源码。首次安装会自动给唯一且未在使用的物理以太网口设置默认的封闭直连地址 `192.168.88.10/24`；无网口、多网口、已有 IPv4/IPv6 地址或默认路由、或 Netplan 冲突时会安全停止，需指定 `[network].interface` 或断开原网络后重试。专用链路地址仅接受 RFC1918 IPv4 网段。该默认值不替代双方现场确认：静态 IP、网段、端口、Flowtime 扫描匹配条件、录播目录和回放倍率均可在 `/etc/neurobridge/gateway.toml` 覆盖；`replay_recording_id` 可留空，网关离线时会自动选择目录中最新的非空历史会话。示例配置在 [config/gateway.toml.example](config/gateway.toml.example)。开发机可使用：

```bash
python3 -m venv .venv
.venv/bin/pip install --no-deps -e .  # unit tests do not need BLE hardware
.venv/bin/pip install websockets==12.0 pyserial==3.5
.venv/bin/python -m unittest discover -s tests -v
```

Flowtime 接入层以 Bleak 实现扫描/连接/通知/断连重连：只要未连接便扫描匹配 `device_name` 与 `model_nbr_uuid` 的设备，并选择 RSSI 最高者。完成 `FF31`、`FF32`、`FF51` 通知订阅后，网关初始化新算法会话、向 `FF21` 写入 `0x05`，最后才公布 `connected`；停止时写入 `0x06`。`FF31` 原始 EEG 与 `FF51` 原始心率均会持久化，`FF51` 用于北向 `hr.raw`；算法输入保留原始字节序和完整窗口。Ubuntu 首次安装会将锁定的行 JSON C++ bridge 安装到 `/usr/local/lib/neurobridge/neurobridge_affective_bridge`；银河麒麟一键流程则在当前 N100/N150 本机构建到项目 `.runtime/algorithm/`，进程自检通过后才自动启用。macOS 仅完成源码构建及合成输入烟雾测试；三种环境的自检都不代表真实数据 POC，SDK 输入分组、串口原始投影、输出指标和性能仍须在最终麒麟主机根据状态、日志和真实数据验证。

SDK 的固定来源和算法启用 POC 见 [sdk.lock](sdk.lock) 与 [算法 SDK 接入 POC](doc/tech/%E7%AE%97%E6%B3%95%20SDK%20%E6%8E%A5%E5%85%A5%20POC.md)。

运行时代码按职责组织：`neurobridge/device/` 选择设备策略，`neurobridge/ble/` 和 `neurobridge/serial/` 分别负责 Flowtime BLE 与 USB TTY 接入；`neurobridge/algorithm/` 隔离算法 SDK bridge；`neurobridge/business/` 负责订阅、状态、录制与录播；`neurobridge/northbound/` 仅负责 WS/WebSocket 传输。

## 仓库结构

- `neurobridge/`：跨平台网关核心，不依赖 macOS、Windows 或 Linux 的启动方式。
- `web/`：由网关托管、无构建步骤的静态网页；`capture/` 是通过网关 WebSocket 查看耳机原始数据的页面，`b-client-test/` 是完整的 B 端协议联调页。
- `mac/`：仅 macOS POC 的启动器、蓝牙验证、原生算法 bridge 与本机配置模板。
- `linux/`：银河麒麟项目一键流程、Ubuntu 兼容部署脚本、systemd 单元与日志轮转配置。
- `windows/`：Windows 平台接入说明；当前没有经过验证的 Windows 服务启动器或部署脚本。

网页不再放入平台目录。两个页面都由当前网关的回环 HTTP 服务托管并连接同一个北向 WebSocket，不直接访问 USB 串口，也不控制 systemd 进程。

## 按平台使用

| 场景 | 入口 | 当前状态 |
| --- | --- | --- |
| Ubuntu x86_64 网关部署 | [`linux/install-ubuntu.sh`](linux/install-ubuntu.sh) | 首期部署入口；安装为 systemd 服务。 |
| macOS 真实头环 POC | [`mac/start-poc.command`](mac/start-poc.command) | 一键启动本机采集、算法 bridge 和控制台；详见 [`mac/README.md`](mac/README.md)。 |
| Windows 网关 | [`windows/README.md`](windows/README.md) | 尚未完成设备、算法和后台服务验证，不能作为交付部署入口。 |
| 耳机原始数据查看页 | [`web/capture/`](web/capture/) | 启动网关后访问 `http://127.0.0.1:8080/capture/`；可订阅/停止页面数据并打印 EEG、HR 原始值。 |
| 本机可视化/兼容 B 端联调网页 | [`web/b-client-test/`](web/b-client-test/) | 默认由网关在回环地址提供；兼容模式仍可作为独立 B 端联调页。 |

默认本机场景启动网关后访问 `http://127.0.0.1:8080/` 使用完整联调台，访问 `http://127.0.0.1:8080/capture/` 查看耳机原始数据。两个页面都使用网关动态注入的 `ws://127.0.0.1:8765/neurobridge/v1/ws`。`capture` 的开始/停止按钮只订阅或取消当前网页的数据推送，设备连接、串口、算法、录制和服务生命周期仍由后台网关负责。

## 首期交付结论

- 目标硬件：N100/N150 x86 小主机，8 GB 内存、256 GB SSD、可稳定枚举目标耳机 TTY 的 USB 端口；蓝牙控制器仅用于保留的 BLE 回归策略。
- 目标系统：N100/N150 x86_64 主机上的银河麒麟 V10；Ubuntu LTS 仅保留为兼容和回归基线。
- 当前默认链路：`头环 → USB/串口（源码已支持，目标机 POC/现场验收待完成）→ 网关后台 → 本机回环 WebSocket → 同机浏览器`。不需要 B 端专用网线、静态地址或 DHCP。
- 兼容链路：配置切换为 `wired_b_side` 后，继续支持 `网关 → 专用有线以太网 → 独立 B 端主机`，消息契约不变。
- 运行方式：实时模式和录播模式均为交付范围；头环未连接时，B 端首次 `subscribe` 或 `getLatest` 会启动网关级录播流程，并以 `mode="replay"` 标记数据来源。录播优先使用部署配置明确指定且有效的会话；否则自动遍历录播目录，选择最新的非空历史会话。后续订阅加入该流程的当前进度。最后一个 B 端 WebSocket 连接断开时，网关停止录播并重置进度；仍有连接时，文件播放到末尾会从首条重新循环。头环重连时自动停止录播并回到实时。网关应开机自启，现场无需操作。
- 运维与交付：网关注册为 systemd 开机自启服务；运行日志持久化并每日轮转。本机策略下网页、WebSocket 和下载服务只监听 `127.0.0.1`。只有切换兼容有线策略时，才配置专网静态地址或独立 DHCP 单元。
- 演示范围：本次为单人；北向协议仅保留录播关联所需的 `subjectId`。
- 当前设备通信规范定义：EEG `FF31` 为 20 字节大端数据包（2 字节序号与 6 个 24 位采样），心率 `FF51` 为 1 字节，佩戴/接触状态 `FF32` 为 2 字节；北向仍最多按 600 ms 分组，但实际包数由网关运行时记录，不写死。

当前默认实时链路使用 USB 串口，不依赖蓝牙。只有显式切换到 `bluetooth` 回归策略时，才需要 BlueZ 和经验证的蓝牙控制器。

## 北向协议要点

本机可视化前端（兼容模式下为第三方 B 端）按需请求数据，而不是网关无条件推送：

数据传输继续采用 **WS/WebSocket + UTF-8 JSON**。默认本机策略仅监听 `127.0.0.1`，由本机 HTTP 页面发起连接并校验固定 Origin；无需物理网线，也不改变消息字段、请求动作或 600 ms 批量节奏。兼容有线策略继续遵守已发布的专用网络约束。当前对外契约见版本台账登记的北向协议；本次拓扑调整未改写已锁定对外文档。

| 请求 | 用途 | 返回行为 |
| --- | --- | --- |
| `getLatest` | 获取注意力、放松度等当前值 | 一请求一响应 |
| `subscribe` | 连续展示原始波形或脑波节律 | 确认后每 600 ms 返回一组/批数据 |
| `unsubscribe` | 停止连续数据 | 确认响应 |
| `getStatus` | 查询连接、信号、电量和当前模式 | 状态响应 |

网关所有输出统一为 `{protocolVersion, code, data, message}`：当前 `protocolVersion` 固定为 `1.0`，`code=200` 成功，非 `200` 时 `message` 为错误原因。连续数据事件的 `data` 中包含 `gatewayBootId`、`mode`（`live`/`replay`）、`subjectId`、`timestampMs`、`valid` 与 `payload`。v0.1 为单头环、单网关实例，不传 `deviceId` 或 `gatewayId`。原始波形和节律数据使用批量 JSON 或二进制帧，避免单采样点逐条 JSON 发送。

北向消息名称、字段和 WebSocket 传输以版本台账登记的当前对外协议为准；设备采样率、每 600 ms 实际样本数等未确认值继续配置化或由运行日志实测，不在 README 中假定。

## SDK 适配评估

| SDK | 结论 | 依据与处理方式 |
| --- | --- | --- |
| [Enter-Biomodule-BLE-PC-SDK](https://github.com/Entertech/Enter-Biomodule-BLE-PC-SDK) | **适配，作为 BLE 接入层的首选 POC** | Python SDK 基于 Bleak 0.19，声明支持 macOS、Linux 和 Windows；可承担 Flowtime 的扫描、连接、通知、启动采集与断线回调。BLE UUID 与数据契约以 [头环蓝牙通信协议](https://entertech.feishu.cn/docs/doccnlmMLpxwY25gJQyiFQmBeRd?from=from_copylink) 为准，不能以 SDK 的旧解包示例推断。Linux 上仍须实机验证。 |
| [AffectiveCloud-Algorithm-SDK](https://github.com/Entertech/AffectiveCloud-Algorithm-SDK) | **适配 x86 Linux 网关的算法层，目标麒麟仍须真实数据 POC** | C++ SDK 提供双通道 `appendEEG`、单通道 `appendSCEEG`、`appendHR` 及注意力、放松度、脑波等报表；它要求 C++17、Eigen3、NumCpp。仓库部署流程已能构建 bridge，但银河麒麟目标机仍须用真实串口原始字节验证构建、输入分组、性能和结果。 |

算法 SDK 还带有 Python 实现，但其依赖固定在 TensorFlow 1.8、Keras 2.2、NumPy 1.16 等较旧版本。首期网关优先选 C++ 实现；不要在未完成独立环境验证的情况下将该 Python 环境直接用于生产镜像。

## 实现边界

- 网关按已选策略处理头环串口或 BLE 协议、数据解析、重连、时间戳、缓存、算法调用和北向适配。
- 实时采集时，网关分别保存设备原始数据与算法结果，并保留可关联的录制会话和时间戳；录播直接读取已保存数据，按原始时间间隔发送。
- 默认由同机浏览器展示；兼容模式下第三方 B 端主机处理展示与保存，其后续服务端上传不属于网关边界。
- 默认只使用本机回环 HTTP/WS；只有切换 `wired_b_side` 时才使用双方批准的专用有线网络参数。

## 上线前 POC 与验收

1. 在最终 N100/N150 + 银河麒麟镜像上验证多 TTY 遍历、首握手短路、`0xE1`/`0xE0` 单字节 `0x01` 应答门禁、异常应答拒绝、分帧、丢包统计、拔插重连和长时间运行。
2. 在同一环境构建算法 C++ bridge，固定 Eigen3/NumCpp/CMake/编译器版本，并以真实串口原始字节验证输入长度、字节序、分组、性能和结果。
3. 使用同机浏览器覆盖实时、录播、设备离线/恢复和浏览器断线重连；若项目切换旧方案，再单独完成专网 B 端联调。
4. 完成启动、systemd、日志轮转、一键诊断导出、升级和回滚演练，交付版本号、配置哈希、操作手册和问题日志。

## 文档

- [头环蓝牙网关对接方案 v0.1](doc/tech/%E5%A4%B4%E7%8E%AF%E8%93%9D%E7%89%99%E7%BD%91%E5%85%B3%E5%AF%B9%E6%8E%A5%E6%96%B9%E6%A1%88_v0.1.md)：架构、边界、待决项和验收要求。

### 对外文档

- [头环数据网关北向网络协议 v0.2](doc/tech/%E5%AF%B9%E5%A4%96/%E5%A4%B4%E7%8E%AF%E6%95%B0%E6%8D%AE%E7%BD%91%E5%85%B3%E5%8C%97%E5%90%91%E7%BD%91%E7%BB%9C%E5%8D%8F%E8%AE%AE/%E5%A4%B4%E7%8E%AF%E6%95%B0%E6%8D%AE%E7%BD%91%E5%85%B3%E5%8C%97%E5%90%91%E7%BD%91%E7%BB%9C%E5%8D%8F%E8%AE%AE_v0.2.md)：已发布，B 端 WS/JSON 接入契约与联调验收。
- [头环数据采集包格式说明 v0.1](doc/tech/%E5%AF%B9%E5%A4%96/%E5%A4%B4%E7%8E%AF%E6%95%B0%E6%8D%AE%E9%87%87%E9%9B%86%E5%8C%85%E6%A0%BC%E5%BC%8F%E8%AF%B4%E6%98%8E/%E5%A4%B4%E7%8E%AF%E6%95%B0%E6%8D%AE%E9%87%87%E9%9B%86%E5%8C%85%E6%A0%BC%E5%BC%8F%E8%AF%B4%E6%98%8E_v0.1.md)：已发布，一键保存 ZIP 的文件、字段和校验规则。
- [头环数据网关 SSH 运维操作指南 v1.0](doc/tech/%E5%AF%B9%E5%A4%96/%E5%A4%B4%E7%8E%AF%E6%95%B0%E6%8D%AE%E7%BD%91%E5%85%B3%20SSH%20%E8%BF%90%E7%BB%B4%E6%93%8D%E4%BD%9C%E6%8C%87%E5%8D%97/%E5%A4%B4%E7%8E%AF%E6%95%B0%E6%8D%AE%E7%BD%91%E5%85%B3%20SSH%20%E8%BF%90%E7%BB%B4%E6%93%8D%E4%BD%9C%E6%8C%87%E5%8D%97_v1.0.md)：已发布，供经授权的外部运维人员操作网关 SSH 服务。
- [头环数据网关有线网络配置指南 v1.0](doc/tech/%E5%AF%B9%E5%A4%96/%E5%A4%B4%E7%8E%AF%E6%95%B0%E6%8D%AE%E7%BD%91%E5%85%B3%E6%9C%89%E7%BA%BF%E7%BD%91%E7%BB%9C%E9%85%8D%E7%BD%AE%E6%8C%87%E5%8D%97/%E5%A4%B4%E7%8E%AF%E6%95%B0%E6%8D%AE%E7%BD%91%E5%85%B3%E6%9C%89%E7%BA%BF%E7%BD%91%E7%BB%9C%E9%85%8D%E7%BD%AE%E6%8C%87%E5%8D%97_v1.0.md)：已发布，供网关交付与 B 端运维人员配置专用有线直连网络。

### 其他文档

- [B 端联调网页](web/b-client-test/README.md)：零依赖的浏览器测试工具，可连接网关并测试 `getStatus`、`getLatest`、`subscribe`、`unsubscribe`。

对外文档的 Markdown 独立保存在仓库，PDF 不提交仓库。包含未发布对外文档的 PR 必须先在该 PR 分支运行 `python3 tools/mark-external-documents-published.py --date <YYYY-MM-DD>`，将文档状态、发布日期、摘要、发布记录和锁定区间一并提交；CI 会拒绝仍含未发布文档的 PR，因而无法合入 `master`。PR 的 CI 生成候选包；状态已发布且已锁定的 PR 合入 `master` 后，CI 将四份外部 Markdown 转为 PDF，连同可直接双击打开的 `b-client-test/index.html` 联调网页打包为 `neurobridge-external-documents.zip` 并上传正式 Artifact。`candidate` 仅生成候选包；`publish` 仅允许所有打包源文档已发布且锁定时执行。如需额外生成某个历史正式版本或内部预发布版的北向协议 PDF，可填写 `protocol_version` 和 `protocol_stage`。版本清单见 [版本台账](neurobridge/version_registry.toml)。
