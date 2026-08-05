# NeuroBridge

NeuroBridge 是将回车科技头环的 BLE 数据接入第三方 B 端主机的 PC/Linux 蓝牙网关。网关负责 BLE 采集、数据与算法处理、北向协议适配、录播和运行维护；第三方 B 端主机负责展示与保存。

当前对接方案详见：[头环蓝牙网关对接方案 v0.1](doc/tech/%E5%A4%B4%E7%8E%AF%E8%93%9D%E7%89%99%E7%BD%91%E5%85%B3%E5%AF%B9%E6%8E%A5%E6%96%B9%E6%A1%88_v0.1.md)。

## 可运行网关与部署

仓库现已包含可部署的 Python 网关：`neurobridge/`。它按 v0.1 协议提供 WebSocket 服务、以 600 ms 窗口批量发送数据、管理订阅与自动录播，并将原始 BLE 窗口和算法结果分别持久化为 JSONL。网关不从旧 PC SDK 继承其错误的解包逻辑：`FF31`、`FF51`、`FF52` 都在落盘和算法调用前完整保留原始字节；北向仅按协议发送允许的原始流。

在 Ubuntu x86_64 目标机上，从已审查的工作树以 root 执行：

```bash
./deploy/install-ubuntu.sh
sudoedit /etc/neurobridge/gateway.toml
systemctl restart neurobridge
systemctl status neurobridge
```

部署脚本会创建 `neurobridge` 服务账户、运行目录、Python 虚拟环境和 systemd 服务。静态 IP、端口、Flowtime 扫描匹配条件、录播文件和回放倍率必须在 `/etc/neurobridge/gateway.toml` 中填入双方确认值；示例配置在 [config/gateway.toml.example](config/gateway.toml.example)。开发机可使用：

```bash
python3 -m venv .venv
.venv/bin/pip install --no-deps -e .  # unit tests do not need BLE hardware
.venv/bin/pip install websockets==12.0
.venv/bin/python -m unittest discover -s tests -v
```

Flowtime 接入层以 Bleak 实现扫描/连接/通知/断连重连：只要未连接便扫描匹配 `device_name` 与 `model_nbr_uuid` 的设备，并选择 RSSI 最高者。完成 `FF31`、`FF32`、`FF51`、`FF52` 通知订阅后，网关初始化新算法会话、向 `FF21` 写入 `0x05`，最后才公布 `connected`；停止时写入 `0x06`。因此每个采集启动后的原始 EEG/HR 窗口都会自动进入算法 bridge。算法 SDK 通过独立的行 JSON bridge 进程接入，避免 Python 进程直接依赖 C++ ABI。由于 SDK 默认输入包长与本项目确认的 `FF31=14`、`FF52=20` 不一致，`algorithm.enabled` 默认关闭；在真实录制字节完成输入分组与输出核验前，网关只发布已验证的原始流，不会生成伪算法指标。

SDK 的固定来源和算法启用 POC 见 [sdk.lock](sdk.lock) 与 [算法 SDK 接入 POC](doc/tech/%E7%AE%97%E6%B3%95%20SDK%20%E6%8E%A5%E5%85%A5%20POC.md)。

运行时代码按职责组织：`neurobridge/ble/` 负责 Flowtime 扫描、连接、通知与原始字节窗口；`neurobridge/algorithm/` 隔离算法 SDK bridge；`neurobridge/business/` 负责订阅、状态、录制与录播；`neurobridge/northbound/` 仅负责 WS/WebSocket 传输。

## 首期交付结论

- 目标硬件：N100/N150 x86 小主机，8 GB 内存、256 GB SSD、千兆网口（双网口优先）、经实机验证可稳定连接目标头环的蓝牙 5.x 芯片。
- 目标系统：Ubuntu LTS/Linux。网关不需要运行 Android；Android 或浏览器仅作为第三方展示端。
- 链路：`头环 → BLE → 网关 → 有线以太网 → 第三方 B 端主机`。
- 运行方式：实时模式和录播模式均为交付范围；头环未连接时，网关在 B 端 `subscribe` 或 `getLatest` 后自动使用已配置录播，并以 `mode="replay"` 标记数据来源。网关应开机自启，现场无需操作。
- 演示范围：本次为单人；北向协议仅保留录播关联所需的 `subjectId`。
- 已确认的首期 Flowtime BLE profile：EEG `FF31` 为 14 字节大端数据包，HR 原始 `FF52` 为 20 字节数据包，佩戴/接触状态 `FF32` 为 2 字节；北向仍最多按 600 ms 分组，但实际包数由网关运行时记录，不写死。

若现场禁止任何蓝牙，实时链路不能工作；只能改用有线采集、在允许蓝牙的区域采集后录播，或取得场地方对限制范围的书面确认。

## 北向协议要点

第三方 B 端主机按需请求数据，而不是网关无条件推送：

首期北向传输统一采用 **WS/WebSocket + UTF-8 JSON**：网关作为服务端，B 端通过专用有线以太网直连；不使用 TLS、证书或 Token，WS 数据为明文，禁止接入公网、无线网络或不受控局域网。联调前确认两端静态 IP、子网掩码、端口与防火墙放行。连续数据最多每 600 ms 推送一组。请求、数据结构、BLE 字节映射、算法字段、错误码与验收项见《[头环蓝牙网关北向网络协议 v0.1](doc/tech/%E5%A4%B4%E7%8E%AF%E8%93%9D%E7%89%99%E7%BD%91%E5%85%B3%E5%8C%97%E5%90%91%E7%BD%91%E7%BB%9C%E5%8D%8F%E8%AE%AE_v0.1.md)》。

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
| [Enter-Biomodule-BLE-PC-SDK](https://github.com/Entertech/Enter-Biomodule-BLE-PC-SDK) | **适配，作为 BLE 接入层的首选 POC** | Python SDK 基于 Bleak 0.19，声明支持 macOS、Linux 和 Windows；可承担 Flowtime 的扫描、连接、通知、启动采集与断线回调。BLE UUID、14 字节 EEG 帧、20 字节 HR 原始帧等数据契约以 [头环蓝牙通信协议](https://entertech.feishu.cn/docs/doccnlmMLpxwY25gJQyiFQmBeRd?from=from_copylink) 为准，不能以 SDK 的旧解包示例推断。Linux 上仍须实机验证。 |
| [AffectiveCloud-Algorithm-SDK](https://github.com/Entertech/AffectiveCloud-Algorithm-SDK) | **适配 x86 Linux 网关的算法层，须完成构建 POC 后上线** | C++ SDK 提供双通道 `appendEEG`、单通道 `appendSCEEG`、`appendHR` 及注意力、放松度、脑波等报表；接口注释以 0.6 秒为默认触发周期，和本网关 600 ms 分组一致。它要求 C++17、Eigen3、NumCpp；仓库当前没有可直接交付的 Linux 产物，需在 Ubuntu x86_64 上补齐依赖、编译并用真实 BLE 原始字节流做结果比对。 |

算法 SDK 还带有 Python 实现，但其依赖固定在 TensorFlow 1.8、Keras 2.2、NumPy 1.16 等较旧版本。首期网关优先选 C++ 实现；不要在未完成独立环境验证的情况下将该 Python 环境直接用于生产镜像。

## 实现边界

- 网关处理头环 BLE 协议、数据解析、重连、时间戳、缓存、算法调用和北向适配。
- 实时采集时，网关分别保存原始 BLE 数据与算法结果，并保留可关联的录制会话和时间戳；录播直接读取这些已保存数据，按原始时间间隔发送。
- 第三方 B 端主机处理展示与保存；其是否继续上传服务端不属于网关边界。
- 使用专用有线以太网直连第三方主机；联调前确认两端静态 IP、子网掩码、WS 端口、连接方向、防火墙放行与断线补传规则。

## 上线前 POC 与验收

1. 在目标 N100/N150 + Ubuntu LTS 上测试 PC BLE SDK：扫描、连接、订阅、断线重连、重启恢复和长时间运行。
2. 在同一环境编译算法 C++ SDK，固定 Eigen3/NumCpp/CMake/编译器版本，并以录制的真实 `FF31`/`FF51`/`FF52` 原始字节验证算法输入长度、字节序、分组方式和 600 ms 触发节奏。
3. 完成北向模拟服务端和真实 B 端主机联调，覆盖实时、录播、网关/服务端离线、恢复补传和异常数据。
4. 在展演网络完成一次全链路演练，交付版本号、配置备份、操作手册和问题日志。

## 文档

- [头环蓝牙网关对接方案 v0.1](doc/tech/%E5%A4%B4%E7%8E%AF%E8%93%9D%E7%89%99%E7%BD%91%E5%85%B3%E5%AF%B9%E6%8E%A5%E6%96%B9%E6%A1%88_v0.1.md)：架构、边界、待决项和验收要求。
- [头环数据网关北向网络协议 v0.2](doc/tech/%E5%AF%B9%E5%A4%96/%E5%A4%B4%E7%8E%AF%E6%95%B0%E6%8D%AE%E7%BD%91%E5%85%B3%E5%8C%97%E5%90%91%E7%BD%91%E7%BB%9C%E5%8D%8F%E8%AE%AE/%E5%A4%B4%E7%8E%AF%E6%95%B0%E6%8D%AE%E7%BD%91%E5%85%B3%E5%8C%97%E5%90%91%E7%BD%91%E7%BB%9C%E5%8D%8F%E8%AE%AE_v0.2.md)：B 端 WS/JSON 接入契约与联调验收。
- [B 端联调网页](tools/b-client-test/README.md)：零依赖的浏览器测试工具，可连接网关并测试 `getStatus`、`getLatest`、`subscribe`、`unsubscribe`。

对外协议版本的 Markdown 独立保存在仓库，PDF 不提交仓库。可在 GitHub Actions 的 **Run workflow** 中填写 `protocol_version` 和 `protocol_stage`：`published` 生成已发布对外版本，`prerelease` 只生成当前内部预发布版本的评审 Artifact。本地/Codex 使用同一入口：`python3 tools/build-external-protocol-artifact.py --stage <published|prerelease> --version <版本号> --output-dir <目录>`。版本清单、已发布版本和预发布版本见 [版本台账](neurobridge/version_registry.toml)。
