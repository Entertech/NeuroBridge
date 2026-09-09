# 银河麒麟 V10 耳机 USB 串口接入技术方案

状态：内部实现基线  
日期：2026-09-04  
需求来源：[银河麒麟 V10 耳机 USB 串口接入 PRD](银河麒麟V10耳机USB串口接入_PRD.md)

## 1. 实施边界

本方案只描述耳机通过 USB 在银河麒麟 V10 中呈现为 TTY 后的网关实现。macOS、BLE、非 TTY 原生 USB 和独立 B 端专网均不属于本次实现或验收范围。

仓库现有兼容代码继续存在，但目标机部署配置固定选择：

```toml
[data_source]
type = "serial"

[access]
mode = "local_browser"
```

运行时不得自动切换到其他设备传输或访问拓扑。

## 2. 总体架构

```text
耳机 USB
  ↓
银河麒麟 USB/TTY 驱动
  ↓ read boundary + receivedAtMs
PosixSerialSource（SerialAdapter 唯一持有 TTY，会话绑定 DeviceControl）
  ├─ 候选发现与 USB 身份校验 / 已有流观察 / ACK 与 01 验证
  └─ RawChunk（保留读取边界及已知队列缺口）
       ↓
HeadsetRev181Parser → DeviceFrame + ParsedSignal + Diagnostics
       ↓
ApplicationService
  ├─ 算法 ready 后调用 DeviceControl E1；停止先调用 E0
  ├─ SignalWindowAssembler → 有界算法 Worker → WindowResult
  ├─ SegmentedRecordingRepository（raw / parsed / algorithm）
  └─ LatestSnapshotStore → GatewayApplication → SubscriptionFanout
       ↓
NorthboundController / WebSocket → 127.0.0.1 浏览器
```

职责边界：

| 模块 | 职责 | 禁止事项 |
|---|---|---|
| `neurobridge/adapters/sources/serial_posix.py` | 封装 TTY 发现、验证、读取、时间和重连；内部复用 SerialAdapter | 不调用算法或生成业务投影 |
| `neurobridge/adapters/parsers/headset_rev181.py` | 纯分帧、重同步、序列诊断和来源追踪 | 不进行 I/O 或调用 SDK |
| `neurobridge/application/service.py` | 双状态、DeviceControl 决策、窗口、有界算法任务与原子快照 | 不依赖 pyserial 对象 |
| `neurobridge/application/gateway.py` | 查询、订阅、发布及 Profile 能力约束 | 不直接创建具体设备、存储或 SDK |
| `neurobridge/adapters/storage/` | 独立分段写入、恢复、历史格式兼容和下载投影 | 不把内部帧混入公开采集包 |
| `neurobridge/northbound/` | 回环 HTTP/WS、请求与订阅 | 不直接访问串口 |
| `linux/` | 银河麒麟准备、构建、自检、systemd、诊断和更新 | 不记录敏感原始数据 |

## 3. 串口配置

| 参数 | 默认值/约束 | 用途 |
|---|---|---|
| `device` | `auto` 或 USB 派生 TTY 绝对路径 | 自动遍历或固定目标 |
| `candidate_types` | `ttyACM`、`ttyUSB` 的非空组合 | 限制候选类别 |
| `baud_rate` | 固定 115200 | 已确认协议参数 |
| `handshake_timeout_ms` | 默认 1000 | 打开后观察已有合法流 |
| `command_response_timeout_ms` | 默认 1000 | ACK 后等待独立 `0x01` |
| `data_timeout_seconds` | 默认 5 | 无合法帧时关闭重连 |
| `reconnect_delay_seconds` | 默认 3 | 下一轮发现间隔 |
| `stats_interval_seconds` | 默认 10 | 汇总日志周期 |
| `max_buffer_bytes` | 默认 65536 | 限制非法输入占用 |
| `dtr`、`rts` | 默认 `false` | 打开端口前设置控制线 |

串口固定为 115200 8-N-1、无软硬件流控。DTR/RTS 默认关闭只是保守实现，仍需在最终转换板上测量打开、关闭和异常退出时的真实电气行为。

## 4. 候选发现

发现器读取 `/dev/serial/by-id/*`、`/dev/ttyACM*` 和 `/dev/ttyUSB*`，解析真实路径和 sysfs USB 父设备。处理规则：

1. 排除不存在、普通文件、非字符设备和非 USB 派生 TTY。
2. 同一真实设备去重，优先保留稳定的 by-id 别名。
3. 按 by-id、USB 父路径、接口号、物理路径和设备节点稳定排序。
4. 候选严格串行打开和验证。
5. 首个验证成功的候选停止后续遍历。

日志记录脱敏后的路径、VID/PID、USB 序列号、接口和驱动；不得记录控制响应全文或设备原始数据。

## 5. 状态机和控制时序

内部状态：

```text
not_connected
  → connecting
  → validated（观察到已有合法流）
  → disconnected

候选静默、需要主动 ACK 时：
connecting → validating
  ├─ 收到独立 01 → validated
  └─ ACK 超时且没有其他候选通过 → validation_failed

`validation_failed` 在本轮结束后保持不变；下一轮重试开始才进入 `connecting`。没有找到或无法打开任何候选才使用 `not_connected`，已经完成验证的串口后来关闭才使用 `disconnected`。
```

每个候选打开后先被动观察：

- 缓冲中出现完整合法 28 字节帧：保存该缓冲及其读取边界时间，立即报告 `validated`，不发送 ACK 或 E1。
- 观察窗口没有合法帧：清理输入缓冲并写入 `AA 55 01 01 01 01 6F`。
- ACK 等待窗口只把独立单字节 `0x01` 视为成功；完整重复握手触发再次 ACK。遇到未知内容或损坏的响应前缀后，本轮剩余字节只做脱敏计数，不能通过清空缓冲把后续嵌入的 `0x01` 重新识别为确认；下轮探测重新验证。
- 收到独立 `0x01` 后立即报告 `validated`，再初始化算法。
- 算法 ready 后无响应写入单字节 `0xE1`，随后进入数据读取。
- 正常停止尽力无响应写入一次 `0xE0`，无论写入是否成功都关闭资源。

算法初始化、E1 写失败和数据超时都发生在设备验证之后；日志记录实际阶段，但不得产生 `validation_failed`。

## 6. 读取边界时间

串口每次 `read()` 返回非空字节后立即记录 `receivedAtMs`。分帧器消费该缓冲时：

- 一个读取批次中的完整帧使用该批次的时间；
- 跨读取批次完成的帧使用最后一个字节到达批次的时间；
- 已有流观察缓冲将读取时间与字节一起传入正式流处理，不能在算法初始化后重新取时间；
- 同一帧派生的 DeviceFrame、EEG 和 HR 共用同一时间。

该时间用于窗口、内部持久化和算法输入关联，不以页面发送时间替代。

## 7. 分帧和数据映射

合法帧条件：

- 3 字节固定包头匹配；
- 长度字节为 28；
- 缓冲至少包含 28 字节；
- 3 字节固定包尾匹配。

非法长度或包尾时每次至少丢弃一个字节并继续搜索包头。缓冲超过 `max_buffer_bytes` 时只保留上限并记录丢弃统计，防止无限增长。

每个合法帧产生：

```text
DeviceFrame.raw_bytes = frame[0:28]  # 原样持久化
DeviceFrame.sequence  = frame[4:6]   # 解析为大端整数
ParsedSignal.eeg      = frame[6:24]  # 不含序列号
ParsedSignal.hr       = frame[24:25]
AlgorithmInput.eeg    = frame[4:24]  # SDK 专用 20 字节投影
Northbound.eegRaw     = sequence + ParsedSignal.eeg  # 保持既有 20 字节合同
```

算法兼容仅表示保持现有 20 字节 EEG 和 1 字节 HR 原始输入合同；最终算法正确性必须使用真实耳机数据验证。

## 8. 算法状态广播

Bootstrap 的 `device_ready()` 调用 `ApplicationService.prepare_session()` 初始化新算法会话，并通过 `GatewayApplication.update_status()` 广播：

- 可用：`algorithmState=ready`；
- bridge 报错：`algorithmState=error`；
- 配置或进程不可用：`algorithmState=unavailable`。

状态变化必须向订阅 `status` 的浏览器广播完整北向状态，避免串口已经 `validated` 后页面仍停留在 `unavailable`。

运行中的算法报错、超时、输出非法或进程退出，在当前会话窗口处理后同步为 `algorithmState=error`，更新内部数据状态快照及 `getStatus`，并通过已有 `status` 订阅广播。原始数据采集、保存与分发继续；积压占位窗口、旧会话结果和迟到结果不改变当前算法状态。bridge 失步退出后保持错误状态，下一设备会话重新初始化成功才恢复 ready，不自动重算历史数据。

## 9. 持久化和录播

生产链路按会话分别写入：

```text
<recording-root>/sessions/<recordingId>/raw/000001.jsonl.partial
<recording-root>/sessions/<recordingId>/parsed/000001.jsonl.partial
<recording-root>/sessions/<recordingId>/algorithm/000001.jsonl.partial
<recording-root>/sessions/<recordingId>/manifest.json
```

每行保留 schemaVersion、recordingSessionId、recordType、capturedAtMs 和 correlationId；payload 中保存完整帧、解析批次或算法结果。算法结果同时携带采集窗口起止时间与计算起止时间，迟到结果单独保存但不覆盖当时发布的结果。完整帧目录与文件分别以 0700/0600 创建。

Writer 独立于事件循环，按 `storage.fsync_interval_records` 有界批量 fsync 后才确认持久化；默认每 10 分钟或 256 MiB 关闭分段，原子重命名去掉 `.partial` 并更新摘要。确认、会话关闭和 Writer 退出都有超时，磁盘卡住时保留部分文件等待下次恢复，不无限等待。队列满或写失败不改变业务 valid，内部状态和日志单独说明持久化缺口。

生产路径不再重复写旧版 raw/metric 文件。下载时在临时目录投影成既有采集包格式，公开文件白名单排除完整设备帧、parsed、数字分段和 `.partial`；历史 `internal-device/` 文件保留，不自动搬迁或删除。

算法结果继续写入会话事件。当前耳机 USB 串口策略不读取这些会话做录播；`subscribe`/`getLatest` 在串口未验证或断开时直接返回 `409 STREAM_NOT_AVAILABLE_REASON`。录播读取逻辑仅保留给明确支持录播的历史兼容数据源。

### 9.1 有界处理与观测

`[pipeline]` 将 Source 队列/等待、算法队列、持久化确认、退出、发送超时和日志周期配置化，默认值及本轮完成情况见[需求补齐与验收清单](NeuroBridge项目结构与多系统接入/需求补齐与验收清单.md)。这些值是可调初始值，不是已通过现场验证的性能承诺。

算法慢时采集仍运行，队列满的窗口记录 `ALGORITHM_BACKLOG`；旧会话和迟到结果不回写快照。已知 Source 丢块后先丢弃解析器残帧，避免跨缺口拼帧。每个订阅按流保存一个待发最新值，状态和波形不互相覆盖；发送超时同时取消该订阅的发送任务。

固定周期 `Runtime metrics` 日志记录 CPU、RSS/峰值 RSS、任务/线程、FD/Handle、数据/连接状态、窗口和算法耗时、队列深度、发送/覆盖、离线时间及存储缺口。不可获得的指标为 null；服务重启次数通过 gatewayBootId 和 systemd 日志关联，不能当作进程内重连次数。

## 10. 北向和本机页面

部署固定使用 `local_browser`：

- HTTP、WebSocket 和下载绑定 `127.0.0.1`；
- 页面由网关托管并注入实际 WebSocket 地址；
- WebSocket 只接受页面对应的固定 Origin；
- 页面使用既有 `getStatus`、`getLatest`、`subscribe` 和 `unsubscribe`；
- 串口内部 `validated` 映射为北向 `connected`；`not_connected`、`validation_failed`、`disconnected` 映射为 `disconnected`。

本方案不修改根包络、动作、错误码或 `protocolVersion="1.0"`，也不修改已发布或预发布 B 端协议正文。

## 11. 银河麒麟部署

一键流程按以下顺序执行：

1. 验证 x86_64、项目路径、运行用户和系统版本。
2. 创建项目 `.runtime/`，准备离线 Python 运行时与 wheelhouse。
3. 检查 USB/TTY、权限、占用和 pyserial 打开能力。
4. 从锁定源码构建 C++ 算法 bridge。
5. 运行无人体数据进程自检，成功后原子更新项目配置。
6. 验证本机端口空闲，默认安装或复用以当前桌面用户运行的 systemd 服务并立即启动。
7. 操作员显式配置为非自启时持久记录该偏好，后续日常启动改用前台实例；重新启用后恢复 systemd 默认自启。

`serial_configuration_ready()` 必须读取 `[data_source].type="serial"` 和 `[access].mode="local_browser"`，保证重复执行时跳过已完成配置，不反复覆盖文件。

## 12. 测试与验收

自动化测试至少覆盖：

- 候选过滤、去重、排序和固定路径安全校验；
- 已有合法流接管及读取边界时间保留；
- ACK、独立 `0x01`、重复握手和超时；
- E1/E0 无响应写入；
- 粘包、拆包、噪声、非法长度、错误包尾和缓冲上限；
- 序列号回绕、间隙、重复、乱序和迟到补包；
- 算法 ready/error/unavailable 状态广播；
- 北向三态映射、Origin/Host 拒绝和本机页面资源；
- systemd、一键助手重复执行和诊断脱敏。

自动化通过只表示源码支持。上线前仍需在最终银河麒麟镜像和真实耳机上完成 USB 枚举、电气行为、握手、持续实时数据、算法结果、拔插恢复、服务重启、串口离线拒绝录播和长稳验收。

候选安装脚本先暂存校验、停止旧服务，再替换应用、迁移配置和加载 unit；任一步失败恢复应用/配置/unit/启用与运行状态。成功后也保留唯一回滚快照及 rollback.sh，不删除录制数据。SIGTERM 走正常应用清理与 E0 路径。该脚本已有隔离文件夹回滚测试及 shell 语法检查，但不等于最终麒麟安装验收。


## 13. 需求审查后的代码修复（2026-09-08）

- 启动前拒绝配置错误类型，不把字符串 `"false"` 转为开启；真实 Kylin 系统必须提供 V10 的 VERSION_ID。最终 ISO 身份仍需现场锁定。
- 当前北向合同固定 600 ms。配置和 Profile 均拒绝其他窗口值；不能仅修改 subscribe 返回值来扩大合同。
- Capture 建连/重连先 getStatus，成功后订阅 status，再读取一次快照以覆盖查询与订阅之间的状态变化；状态订阅成功前禁用数据开始按钮。
- 日志默认使用进程内大小轮转，`logging.rotation_mode="size"`、`max_bytes=10485760`、`backup_count=14`；该策略同时适用于源码目录自启与候选安装布局，不依赖麒麟额外安装 logrotate。明确由外部工具管理时配置 `rotation_mode="external"`，同一日志不得同时启用两种轮转。
- 源码启动脚本的 stdout/stderr 直接交给终端或 systemd journal；不再通过 `tee -a` 额外写入 `neurobridge-console.log`。持久运行日志使用上述轮转文件；旧控制台日志保留但不再增长。
- 引导及离线更新入口在转入菜单前恢复原 stdout/stderr，菜单前台启动也不使用安装步骤的 `tee`，避免持续采集日志进入这些有限步骤的日志文件。
- 磁盘满/配额耗尽分别记为 full/no_space、full/quota_exceeded；只读、权限、路径和其他 I/O 错误分别分类。失败写入不推进最后成功时间。录制路径启动失败只导致持久化降级，北向服务仍可启动；状态仍仅用于内部日志，不新增已锁定报文字段。
- 可选 `recording.transport_trace_enabled=false` 默认关闭。开启后，RawChunk 以 `raw.transport_chunk` 写受保护 raw 分段；`transport_trace_max_bytes` 默认 1 MiB，为单次进程录制会话的原始字节预算，超限整块省略并计数，不截断、不中止完整帧录制，也不进入公开 ZIP。字节预算不含 JSON 编码开销。
- 候选安装前校验 `metadata/files.sha256` 和安装/回滚磁盘空间，迁移后校验完整配置与固定 Profile，再启动服务。候选包自带 defaults.toml；正式入口按包默认值、系统配置加载，仅显式 `--development --override-config <文件>` 可增加临时覆盖，且不能改变包固定 Profile。

回归测试均使用合成数据、临时目录或模拟设备。真实耳机、电气行为、目标机升级/回滚和 24 小时长稳保持待验收。
