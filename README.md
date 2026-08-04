# NeuroBridge

NeuroBridge 是将回车科技头环的 BLE 数据接入第三方 B 端主机的 PC/Linux 蓝牙网关。网关负责 BLE 采集、数据与算法处理、北向协议适配、录播和运行维护；第三方 B 端主机负责展示与保存。

当前对接方案详见：[头环蓝牙网关对接方案 v0.1](doc/tech/%E5%A4%B4%E7%8E%AF%E8%93%9D%E7%89%99%E7%BD%91%E5%85%B3%E5%AF%B9%E6%8E%A5%E6%96%B9%E6%A1%88_v0.1.md)。

## 首期交付结论

- 目标硬件：N100/N150 x86 小主机，8 GB 内存、256 GB SSD、千兆网口（双网口优先）、经实机验证可稳定连接目标头环的蓝牙 5.x 芯片。
- 目标系统：Ubuntu LTS/Linux。网关不需要运行 Android；Android 或浏览器仅作为第三方展示端。
- 链路：`头环 → BLE → 网关 → 有线以太网 → 第三方 B 端主机`。
- 运行方式：实时模式和录播模式均为交付范围；头环未连接时，网关在 B 端 `subscribe` 或 `getLatest` 后自动使用已配置录播，并以 `mode="replay"` 标记数据来源。网关应开机自启，现场无需操作。
- 演示范围：本次为单人；北向协议仅保留录播关联所需的 `subjectId`。

若现场禁止任何蓝牙，实时链路不能工作；只能改用有线采集、在允许蓝牙的区域采集后录播，或取得场地方对限制范围的书面确认。

## 北向协议要点

第三方 B 端主机按需请求数据，而不是网关无条件推送：

首期北向传输统一采用 **WSS/WebSocket + UTF-8 JSON**：网关作为服务端，B 端主动连接；不使用 Token，访问控制由受控内网、VLAN/防火墙和 B 端主机地址白名单承担。连续数据最多每 600 ms 推送一组。请求、数据结构、BLE 字节映射、算法字段、错误码与验收项见《[头环蓝牙网关北向网络协议 v0.1](doc/tech/%E5%A4%B4%E7%8E%AF%E8%93%9D%E7%89%99%E7%BD%91%E5%85%B3%E5%8C%97%E5%90%91%E7%BD%91%E7%BB%9C%E5%8D%8F%E8%AE%AE_v0.1.md)》。

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
| [Enter-Biomodule-BLE-PC-SDK](https://github.com/Entertech/Enter-Biomodule-BLE-PC-SDK) | **适配，作为 BLE 接入层的首选 POC** | Python SDK 基于 Bleak 0.19，声明支持 macOS、Linux 和 Windows；内置 Flowtime 采集器，提供 EEG、心率、佩戴状态、电量及断连回调，并能启动设备采集。Linux 上仍须在目标蓝牙芯片与头环实机验证扫描、连接、通知、断线重连与长期稳定性。 |
| [AffectiveCloud-Algorithm-SDK](https://github.com/Entertech/AffectiveCloud-Algorithm-SDK) | **适配 x86 Linux 网关的算法层，须完成构建 POC 后上线** | C++ SDK 提供双通道 `appendEEG`、单通道 `appendSCEEG`、`appendHR` 及注意力、放松度、脑波等报表；接口注释以 0.6 秒为默认触发周期，和本网关 600 ms 分组一致。它要求 C++17、Eigen3、NumCpp；仓库当前没有可直接交付的 Linux 产物，需在 Ubuntu x86_64 上补齐依赖、编译并用真实 BLE 原始字节流做结果比对。 |

算法 SDK 还带有 Python 实现，但其依赖固定在 TensorFlow 1.8、Keras 2.2、NumPy 1.16 等较旧版本。首期网关优先选 C++ 实现；不要在未完成独立环境验证的情况下将该 Python 环境直接用于生产镜像。

## 实现边界

- 网关处理头环 BLE 协议、数据解析、重连、时间戳、缓存、算法调用和北向适配。
- 实时采集时，网关分别保存原始 BLE 数据与算法结果，并保留可关联的录制会话和时间戳；录播直接读取这些已保存数据，按原始时间间隔发送。
- 第三方 B 端主机处理展示与保存；其是否继续上传服务端不属于网关边界。
- 使用有线以太网连接第三方主机；联调前确认地址、端口、连接方向、ACL/VLAN/防火墙与断线补传规则。

## 上线前 POC 与验收

1. 在目标 N100/N150 + Ubuntu LTS 上测试 PC BLE SDK：扫描、连接、订阅、断线重连、重启恢复和长时间运行。
2. 在同一环境编译算法 C++ SDK，固定 Eigen3/NumCpp/CMake/编译器版本，并以录制的真实原始数据验证算法输入长度、字节序和 600 ms 触发节奏。
3. 完成北向模拟服务端和真实 B 端主机联调，覆盖实时、录播、网关/服务端离线、恢复补传和异常数据。
4. 在展演网络完成一次全链路演练，交付版本号、配置备份、操作手册和问题日志。

## 文档

- [头环蓝牙网关对接方案 v0.1](doc/tech/%E5%A4%B4%E7%8E%AF%E8%93%9D%E7%89%99%E7%BD%91%E5%85%B3%E5%AF%B9%E6%8E%A5%E6%96%B9%E6%A1%88_v0.1.md)：架构、边界、待决项和验收要求。
- [头环蓝牙网关北向网络协议 v0.1](doc/tech/%E5%A4%B4%E7%8E%AF%E8%93%9D%E7%89%99%E7%BD%91%E5%85%B3%E5%8C%97%E5%90%91%E7%BD%91%E7%BB%9C%E5%8D%8F%E8%AE%AE_v0.1.md)：WSS/JSON 契约、BLE/算法映射与联调验收。
