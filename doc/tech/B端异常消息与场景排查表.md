# B 端异常消息与场景排查表

受众：内部部署、联调与问题定位人员。本文按当前实现整理，包含 `068cabd` 的中文提示修正及本轮禁止录播切回 `live` 的规则，属于未发布的运行参考；已发布协议仍以版本台账登记的独立文档为准。本文不改写已锁定协议，也不代表现场验收通过。

## 1. 先判断异常来自哪里

| B 端看到的内容 | 所属层次 | 能确认什么 | 下一步 |
| --- | --- | --- | --- |
| 收到 `{protocolVersion, code, data, message}` 且 `code != 200` | 网关 WS 业务响应 | 请求已到达网关并得到响应；该 `code` 是 JSON 字段，不是 HTTP 状态码 | 按第 2 节核对动作、参数与场景 |
| 连接失败、握手拒绝、关闭码或浏览器 `error` | WebSocket 连接/传输 | 可能尚未进入业务处理，通常没有上述 JSON 根对象 | 按第 3 节检查地址、端口、子协议和连接恢复 |
| 页面 `ERROR` 提示“尚未建立连接”“至少选择一个流”等 | B 端测试网页自身 | 可能根本没有发送请求 | 按第 4 节处理页面输入和连接状态 |
| capture 页访问 `/api/...` 得到 HTTP 错误 | 网关运维 HTTP 接口 | 属于采集控制台，不是 B 端 WS 取数响应 | 参见[内部指南中的 HTTP 契约](网关%20SSH%20运维与%20B%20端录播联调指南.md#静态-capture-页的运维-http-契约) |

记录完整响应及 B 端发出的动作、`params`、`requestId` 和时间。解析阶段失败时，响应可能没有 `data.requestId`。`message` 用于展示，程序分支优先根据 `code`、`data.reason` 和 `data.details` 判断，不通过英文提示文本判断业务状态。当前只有下表中指定场景改为“头环未连接”，其余提示原文保持实现中的英文。

## 2. 网关 JSON message 错误表

表中的 `reason` 保留当前实现和既有协议标识的原样拼写，包括已有的非英文字符；不要自行改名或推导另一种拼写。当前 `503`、`429`、`500` 的 `retryable=true`，其余下列业务错误为 `false`。可重试表示条件恢复后可以重试，不表示应立即循环发送。

### 2.1 头环、录播与数据可用性

| code | data.reason | message 原文 | 出现场景 | 处理方式 |
| --- | --- | --- | --- | --- |
| 503 | `REPLAY_NOT_AVAILA设备` | `头环未连接` | 头环未连接，网关配置禁止录播；调用 `getLatest` 或 `subscribe` | 展示中文提示。保持或重新查询状态，由网关运维确认头环连接；B 端不发送录播开关请求 |
| 503 | `REPLAY_NOT_AVAILA设备` | `No replay data is available.` | 头环未连接，允许录播，但没有可读取的非空历史会话；历史会话全部损坏、不可读也属于此类 | 联系网关运维核对录播策略和可用数据；不要把它当作网线未连通 |
| 409 | `STREAM_NOT_AVAILA设备` | `One or more streams are unavailable.` | `getLatest`/`subscribe` 请求了当前不可用或不允许的流；例如 `getLatest` 请求原始流，或请求状态中未提供的算法流 | 查看 `data.details.streams`；重新 `getStatus`，按 `availableStreams` 与动作允许范围选择流 |

禁止录播不妨碍 `getStatus` 返回 `200`，也不妨碍已连接头环的实时采集。禁止录播时，`mode` 必须为 `live`，即使头环未连接；这表示等待实时设备，不代表实时数据已就绪。允许录播且头环未连接时才可为 `replay`，但仍须核对可读会话和实际取数结果。

以下是“禁止录播且头环未连接”的响应示例；`requestId` 对应调用方本次请求：

```json
{
  "protocolVersion": "1.0",
  "code": 503,
  "data": {
    "reason": "REPLAY_NOT_AVAILA设备",
    "retryable": true,
    "details": {},
    "requestId": "latest-example"
  },
  "message": "头环未连接"
}
```

### 2.2 请求格式与参数

| code | data.reason | message 原文 | 出现场景 | 处理方式 |
| --- | --- | --- | --- | --- |
| 400 | `INVALID_REQUEST` | `Request is not valid JSON.` | 文本帧无法解析为 JSON，例如缺引号、括号或尾部多余字符 | 使用标准 JSON 序列化后发送 UTF-8 文本帧 |
| 400 | `INVALID_REQUEST` | `Request root fields are invalid.` | 根对象不是对象，或根字段缺少/多出；请求必须恰含 `protocolVersion`、`messageType`、`requestId`、`action`、`params` | 对照请求模板，移除自定义根字段，补全必需字段 |
| 400 | `INVALID_REQUEST` | `Request fields are invalid.` | `messageType` 不是 `request`、`requestId` 不是字符串，或 `params` 不是对象 | 修正对应类型与固定值 |
| 400 | `INVALID_REQUEST` | `Unknown action.` | 动作不属于 `getStatus`、`getLatest`、`subscribe`、`unsubscribe`；将 `replay enable/disable` 当成 WS 动作也会触发 | 只发送支持的动作；录播策略由网关运维设置 |
| 400 | `INVALID_REQUEST` | `Request contains unsupported params.` | 某动作携带了未支持的参数，例如给 `getStatus` 传 `streams` | 根据 `data.details.params` 删除额外参数；`getStatus.params` 应为 `{}` |
| 400 | `INVALID_REQUEST` | `params.streams must be a non-empty string array.` | `streams` 不是数组、为空或包含非字符串；`subscribe` 省略 `streams` 也触发 | 提供非空字符串数组；`getLatest` 仅允许 `eeg`、`hr`，省略时默认请求二者 |
| 400 | `INVALID_REQUEST` | `params.includeInvalid must be boolean.` | `subscribe.params.includeInvalid` 使用了字符串 `"false"`、数字等 | 使用 JSON 布尔 `true`/`false`；省略默认 `false` |
| 400 | `INVALID_REQUEST` | `params.subscriptionId is required.` | `unsubscribe` 缺少字符串类型的 `subscriptionId` | 使用本次连接中订阅成功响应返回的 ID |
| 505 | `UNSUPPORTED_VERSION` | `Protocol version is not supported.` | 请求的 `protocolVersion` 不等于当前报文版本字符串 `"1.0"` | 使用报文版本，不能填文档修订版本或应用版本 |

### 2.3 订阅状态

| code | data.reason | message 原文 | 出现场景 | 处理方式 |
| --- | --- | --- | --- | --- |
| 404 | `SUBSCRIPTION_NOT_FOUND` | `Subscription does not exist.` | 取消的 ID 不属于当前连接、已被取消，或使用了旧连接的 ID | 清理本地旧 ID；重连后重新订阅，不复用旧 ID |
| 429 | `RATE_LIMITED` | `Subscription limit exceeded.` | 同一连接已有 4 个订阅，又尝试新增 | 合并所需流到较少订阅，或先取消不需要的订阅 |
| 429 | `RATE_LIMITED` | `A stream is already subscribed on this connection.` | 新订阅与当前连接已有订阅包含重复流 | 查看 `data.details.streams`；复用已有订阅，或先退订再调整 |

请求同时存在多个问题时，网关返回校验过程中首先遇到的问题。修正一项后可能得到下一项错误，不应据此认定错误随机变化。

### 2.4 网关内部异常

| code | data.reason | message 原文 | 出现场景 | 处理方式 |
| --- | --- | --- | --- | --- |
| 500 | `INTERNAL_ERROR` | `Gateway request failed.` | 处理请求时发生未转换为明确业务错误的异常 | 保留时间、动作、`requestId` 与完整响应，交给网关运维查询同一时间的异常堆栈 |

收到该响应说明本次请求已经到达网关。不能仅凭 500 判断网线、蓝牙或硬件故障，也不能只凭英文提示确定异常根因。

本次对话确认过一条现场路径：旧版本查询状态时，历史录制 JSON 解析失败导致 500。当前修复会保留原文件并排除整个坏会话；正常会话仍可使用。若全部会话不可用，`getStatus` 仍成功，取数返回第 2.1 节相应的 503。具体坏文件及形成原因须结合网关日志核实，不能仅凭 `JSONDecodeError` 的列号推断。

## 3. 握手拒绝与连接关闭

这些情况没有网关业务 JSON 的 `message` 字段。浏览器可能只给出笼统连接错误，未必暴露 HTTP 响应体或最终关闭原因。

| 信号 | 原文/表现 | 出现场景 | 处理方式 |
| --- | --- | --- | --- |
| 握手 HTTP 426 | `WebSocket subprotocol neurobridge.v1 is required.` | 客户端没有提供 `neurobridge.v1` 子协议 | 浏览器使用 `new WebSocket(url, "neurobridge.v1")`；其他客户端在握手中提供该子协议 |
| WS 1008 | `Unsupported endpoint` | 提供正确子协议，但 WS URL 的路径不等于网关配置路径 | 核对完整 URL，默认 `/neurobridge/v1/ws`，不能填 HTTP 下载或 capture 地址 |
| WS 1003 | `Text JSON required` | B 端发送二进制请求帧 | 请求用 UTF-8 JSON 文本帧；本表不改变数据事件的协议约定 |
| WS 1011 | `Delivery failed; reconnect and resubscribe` | 网关发送失败、超时或订阅队列积压，关闭对应故障连接 | 网络恢复后重连、先 `getStatus` 再订阅；旧 ID 不复用，不假定自动补传 |
| WS 1000 | 例如 `B-side test console disconnect` | B 端测试页主动点击断开；其他正常关闭也可能使用 1000 | 按操作意图处理；需要取数时重新连接和订阅 |
| WS 1006 / 浏览器 `error` | 可能没有原因文字 | 浏览器未观察到完整关闭过程；可能是网络、服务退出、握手或其他异常 | 对照两端网络与网关日志，不能根据该码单独确定硬件原因 |

链路已断时，即使网关尝试用 1011 关闭，B 端也可能只观察到异常关闭。`1006` 是客户端观察到的异常关闭标记，不是网关发送的关闭帧。

## 4. 测试网页自身的 ERROR 日志

下面的文字由 B 端网页产生，不是服务器返回的 `message`，也不具有服务器 `code/reason`。带尖括号的内容表示动态部分。

| 页面文字/模式 | 场景 | 处理方式 |
| --- | --- | --- |
| `尚未建立 WebSocket 连接，无法发送请求。` | 未连接时点击取数/订阅/退订 | 先连接网关 |
| `地址必须以 ws:// 或 wss:// 开头。` | 输入地址的 scheme 不正确 | 本部署使用专用有线 `ws://` 地址；不要填 `http://` 或 capture 页面地址 |
| `getLatest 至少选择 EEG 或 HR 一个流。` | 页面未选任何最新值流 | 勾选需要的流 |
| `subscribe 至少选择一个流。` | 页面未选订阅流 | 勾选需要的流 |
| `没有可取消的订阅。` | 页面没有可用订阅 ID | 先成功订阅，或保持无订阅状态 |
| `发送失败：<浏览器异常>` | `socket.send` 抛异常 | 查看连接状态；实际断开后重新连接和订阅 |
| `连接 <地址> 失败或中断；浏览器不提供底层原因，请对照网关网络日志、两端 IP/掩码、端口和子协议。` | 浏览器触发 `error` 事件 | 核对对应地址与网关日志；不要将其当作根因诊断 |
| `收到无法处理的消息（字符数 <N>）。` | JSON 解析或页面处理收到的消息失败 | 保留日志并核对服务地址、响应格式和页面版本 |

## 5. 200 响应也需要检查的数据状态

- `getStatus` 返回 200 只表示状态查询成功，不保证头环已连接或存在可用数据。禁止录播时的 `mode="live"` 可能是等待头环的状态，须另查 `connectionState`。
- `getLatest` 返回 200 且 `valid=false`、空 `payload` 时，没有可作为有效结果使用的数据；不能用空对象或零值替代真实结果。
- `subscribe` 返回 200 只确认订阅建立，仍需观察后续数据事件及 `valid`；未收到数据不能直接判为业务请求失败。
- `replayEnded` 是录播循环边界事件，不是错误消息，也不等于断开连接。

## 6. 提交问题时保留什么

1. B 端完整目标 WS 地址、页面/客户端版本、操作动作及参数。
2. 原始响应的四个根字段，尤其 `code`、`message`、`data.reason`、`data.details` 和可用的 `requestId`。
3. ISO 时间及其时区。测试页时间带 `Z` 表示 UTC，北京时间需加 8 小时；例如 `01:33:09Z` 对应 `09:33:09+08:00`。
4. 若是连接异常，附关闭码、`wasClean`、关闭原因和是否执行过重启/更新/录播开关。
5. 将这些信息交给网关运维，按[内部诊断指南](网关%20SSH%20运维与%20B%20端录播联调指南.md#网络与蓝牙分别诊断)查询日志；日志不附人体原始数据或凭据。

实现核对入口：[业务请求处理](../../neurobridge/business/gateway.py)、[WebSocket 适配](../../neurobridge/northbound/websocket.py)、[B 端测试页](../../web/b-client-test/app.js)。本文的源码核对和模拟验证不能替代现场验收。
