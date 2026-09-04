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
SerialAdapter
  ├─ 候选发现与 USB 身份校验
  ├─ 已有流观察 / ACK 与 01 验证
  ├─ E1/E0 控制
  ├─ 28 字节分帧、重同步、序列统计
  └─ DevicePacket
       ↓
Gateway
  ├─ WindowAssembler → AlgorithmRunner
  ├─ RecordingStore
  └─ Northbound WebSocket → 127.0.0.1 浏览器
```

职责边界：

| 模块 | 职责 | 禁止事项 |
|---|---|---|
| `neurobridge/serial/adapter.py` | TTY 发现、验证、启停、读取、分帧、接收时间和重连 | 不调用算法或拼北向消息 |
| `neurobridge/device/packet.py` | 传递通道、原始字节、来源和接收时间 | 不解释设备字段 |
| `neurobridge/business/gateway.py` | 状态、窗口、算法、录制、录播和北向发布 | 不依赖 pyserial 对象 |
| `neurobridge/business/recording.py` | 保存公开会话数据和内部完整设备帧 | 不把内部帧混入对外包 |
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
- ACK 等待窗口只把独立单字节 `0x01` 视为成功；完整重复握手触发再次 ACK，其他内容仅脱敏计数。
- 收到独立 `0x01` 后立即报告 `validated`，再初始化算法。
- 算法 ready 后无响应写入单字节 `0xE1`，随后进入数据读取。
- 正常停止尽力无响应写入一次 `0xE0`，无论写入是否成功都关闭资源。

算法初始化、E1 写失败和数据超时都发生在设备验证之后；日志记录实际阶段，但不得产生 `validation_failed`。

## 6. 读取边界时间

串口每次 `read()` 返回非空字节后立即记录 `receivedAtMs`。分帧器消费该缓冲时：

- 一个读取批次中的完整帧使用该批次的时间；
- 跨读取批次完成的帧使用最后一个字节到达批次的时间；
- 已有流观察缓冲将读取时间与字节一起传入正式流处理，不能在算法初始化后重新取时间；
- 同一帧派生的 `serial.frame`、`ff31` 和 `ff51` 共用同一时间。

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
serial.frame = frame[0:28]   # 内部完整帧
ff31         = frame[4:24]   # 2 字节序列号 + 18 字节 EEG
ff51         = frame[24:25]  # 1 字节 HR
```

算法兼容仅表示保持现有 20 字节 EEG 和 1 字节 HR 原始输入合同；最终算法正确性必须使用真实耳机数据验证。

## 8. 算法状态广播

`Gateway.on_device_ready()` 初始化新的算法会话，并统一通过 `update_status()` 更新状态：

- 可用：`algorithmState=ready`；
- bridge 报错：`algorithmState=error`；
- 配置或进程不可用：`algorithmState=unavailable`。

状态变化必须向订阅 `status` 的浏览器广播完整北向状态，避免串口已经 `validated` 后页面仍停留在 `unavailable`。

## 9. 持久化和录播

内部完整设备事件写入：

```text
<recording-root>/internal-device/<recordingId>/packets.jsonl
```

每行包含 `sessionId`、`transport`、`channel`、`receivedAtMs`、`byteLength`、`encoding` 和 `bytesBase64`。内部文件继承录制目录最小权限，不进入普通网页下载、诊断包、Git 或既有对外采集包。

算法结果继续写入会话事件。当前耳机 USB 串口策略不读取这些会话做录播；`subscribe`/`getLatest` 在串口未验证或断开时直接返回 `409 STREAM_NOT_AVAILABLE_REASON`。录播读取逻辑仅保留给明确支持录播的历史兼容数据源。

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
