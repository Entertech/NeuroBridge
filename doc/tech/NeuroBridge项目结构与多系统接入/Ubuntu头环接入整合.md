# Ubuntu 头环接入整合

日期：2026-11-03

状态：内部实施说明；合成验证与 Ubuntu x86_64 容器构建分别记录，目标机现场验收待完成。

## 基线与范围

分支 `codex/ubuntu-unified-architecture` 从 `codex/linux-headband-ethernet-gateway` 的 `896570050550fcaa2f55c92d2633051d3d8cbdfe` 创建，rebase 到默认分支 `master` 的 `715ddb8`。原分支与 `ubuntu_0.0.1`、`ubuntu_0.0.2`、`ubuntu_0.0.3` 标签保留，便于核对旧现场行为。

本轮在默认分支的统一架构中支持 Ubuntu 24.04 LTS x86_64 头环网关。应用版本沿用版本台账的默认分支基线，报文版本和已锁定协议保持原值；此次需求分支不执行发版或打新标签；本次新增 Ubuntu 原生 DEB 构建目标并接入发布矩阵。Ubuntu 使用预编译 DEB 或既有源码部署与 systemd，Windows/麒麟继续使用各自的串口本机 Profile。NanoPi、Ubuntu ARM、其他 Ubuntu 版本和现场验收不由本轮合成结果推定。

## 固定组合与依赖方向

Bootstrap 选择 Profile 并装配依赖；入口持有进程级 `GatewayRuntime`，每轮采集由一次性 `AcquisitionSession` 连接 `RawDataSource → RawDataParser → ApplicationService → RecordingRepository / AlgorithmEngine / NorthboundSink`。HTTP/WS 与录制仓库在进程级保留，采集恢复只替换设备、应用和算法会话。

| 层 | Ubuntu 实现与职责 |
| --- | --- |
| Profile | `ubuntu_headband_wired` 固定 Ubuntu x86_64、蓝牙头环、专用有线 B 端；支持录制与按部署许可录播，关闭本机 UI。 |
| Source / Control | `BluetoothBleakSource` 封装 `FlowtimeAdapter`，复制通知字节后形成有界 `RawChunk`；`BluetoothSessionControl` 按连接会话写启动/停止，过期会话拒绝写入。 |
| Parser | `HeadbandBleParser` 解析 EEG、HR、佩戴/接触与电量通道，保留原始字节和关联标识；不调用算法或网络。 |
| Application | 准备会话、600 ms 窗口、算法串行处理、原始与结果持久化、快照、订阅与录播许可；不导入设备 I/O。 |
| Storage | 复用共享有界分段 writer、manifest、容量状态和崩溃恢复，避免重复写一套旧格式。原始设备帧携带通道、会话及接收时间；算法结果关联原采集窗口。 |
| Northbound | 复用共享 Controller/Codec 与 WebSocket 服务；业务错误使用中文，码值、标识和结构沿用既有契约。 |
| Runtime / 运维 | `CaptureControl` 与 macOS POC 委托共享 `GatewayRuntime`，Bootstrap 提供会话工厂；监督真实设备生产任务，清理后才创建新的应用/设备/算法会话。 |

旧 `business.Gateway` 和 `device` 入口保留兼容调用与测试用途。Ubuntu 生产入口只通过 Bootstrap 的 `GatewayApplication` 与 ApplicationService，页面不选择设备类型或数据源。

## BLE 生命周期与故障隔离

1. 扫描配置匹配的头环，选择最强候选；连接后先建立录制与算法会话。
2. Application 只准备会话，此阶段不发送启动指令。Source 安装全部必需通知后才调用绑定该会话的 Control，发送一次启动指令。设备在订阅时推来的首包已有落盘会话。
3. 电量首次读取与可选通知失败只记录诊断。佩戴与电量状态投影到已有状态字段；原始字节走共享分段持久化，不进入 EEG/HR 算法窗口或录播。空包与非法长度有诊断，不让主任务退出。
4. 同时识别 Bleak 0.19 wrapper 与 backend 的断线回调，忽略旧连接回调，并以连接轮询兜底。断线排空已排队回调，清空当前电量/佩戴并关闭对应会话。
5. 算法继续使用默认分支更新后的 SDK 和原始输入，不重新编码或重采样。交换串行化，超时/取消废弃旧响应流；终止有期限，失败升级 kill。
6. 实时分发沿用默认分支的每流最新值合并，内存不随慢连接无限增长；录播使用有界队列。超时或发送失败关闭该连接，采集、落盘与其他客户端继续；恢复后重新 `getStatus` 和订阅。

## 架构审查修复：任务监督与状态隔离

`RawDataSource.wait_stopped()` 将底层生产任务的异常、意外正常返回和意外取消传递给 `AcquisitionSession`。会话内消费者与监督任务处于同一 TaskGroup，生产者失败不会继续显示为采集中。`GatewayRuntime` 上报错误、清理旧会话、复用仓库和监听，并按对应 BLE/串口 `reconnect_delay_seconds` 重建。主动停止取消重试；清理失败暂停恢复并拒绝新开采集，须先成功停止，避免新旧设备/算法同时占用。清理各阶段受 `pipeline.shutdown_timeout_ms` 约束。

正常停止先停止生产，保留消费者排空已接收原始字节，再关闭应用与算法、收尾录制，最后取消剩余观察任务。故障导致消费者已退出时不等待其排空；错误必须保留为可观测状态，不能声称故障前排队数据全部持久化。

`getStatus` 与只订阅 `status` 的路径不读取录播文件。可用流由实时状态或最近一次验证成功的目录快照投影；启动后目录未就绪时保守返回 `status`。启动预热在后台执行，离线 `getLatest`/数据订阅在独立期限内准备目录；超时或不可读使用既有不可用错误，不新增协议字段或错误标识。

`pipeline.replay_catalog_timeout_ms`（默认 2000 毫秒，正整数）限制每次离线查询等待，`replay_catalog_refresh_ms`（默认 30000 毫秒，正整数）控制目录检查结果缓存期限；数据请求触发过期重查，完成录制时主动失效。并发查询与多次超时共用一个检查任务，取消后也等待物理读线程退出才重开，防止慢盘反复创建扫描线程。目录扫描不重新运行算法。

macOS POC 的开始/停止与 Ubuntu 使用同一个 Runtime。停止仅关闭当前采集会话，保留网关、HTTP/WS 与仓库；再次开始通过 Bootstrap 创建新应用，避免复用已关闭的应用或重新绑定仓库。只有进程退出才关闭监听和仓库。

## 配置与升级

新安装使用 `config/gateway.ubuntu.toml.example`。示例地址 `192.168.88.10/24`、B 端 `192.168.88.20/24` 和端口必须在部署时确认。安装和 systemd 启动追加配置的专用地址，保留已有 DHCP、地址、默认路由与 DNS；不自动切换到其他物理网口。单口可选 `auto`，多口必须指定。回滚网络操作仅删除经确认由本功能追加的地址。

未声明 schema 且含旧 BLE 配置的现场文件在内存中迁移到显式蓝牙数据源；专网地址继续选择有线接入，回环配置保留原接入语义。不会写回或覆盖现场文件，不会把新 schema 中缺少数据源的错误配置自动改成 BLE。生产 Profile 仍检查操作系统、架构和接入方式，不匹配时拒绝启动。

算法新配置使用 `request_timeout_ms`。兼容旧 `evaluation_timeout_seconds` 并换算为毫秒；两项同时填写必须一致。`stop_timeout_seconds` 独立控制终止等待。录播仍使用原部署字段；`server.send_timeout_seconds` 与共享 `pipeline.send_timeout_ms` 同时存在时使用较短期限，`server.delivery_queue_size` 用于录播待发队列，实时采用每流最新快照。诊断间隔由 `logging.diagnostics_interval_seconds` 管理。

旧安装的录制目录保留。回放兼容旧逐包/指标文件与新分段文件；读取已保存算法结果，不在回放时计算算法。坏文件排除整个会话，继续尝试其他可读会话并保留坏文件。对外 ZIP 沿用已锁定格式，内部设备状态和分段文件不额外加入公开字段。

回退时使用原分支或原 Ubuntu 标签部署，保留 `/etc/neurobridge`、录制目录与现场配置备份；旧程序不能据此保证识别本轮新增的分段录制，禁止用旧程序重写新格式文件。回退前应停止采集并另存相关会话与日志。

## 页面与运维

默认分支 `web/capture/`、`web/b-client-test/` 继续由本机耳机 Profile 托管。Ubuntu 运维使用 `web/ubuntu/capture/`、`web/ubuntu/b-client-test/`，由专网下载服务在 `/capture/`、`/b-client-test/` 提供；`tools/build-static-console.py` 生成独立静态 ZIP。公共版本脚本与 Windows B 端 IP 脚本复用原文件，避免复制版本号。

开始、停止与导出只影响 Ubuntu 当前采集管线，HTTP/WS 不随采集关闭；重新启动创建新会话。采集管理 API 仅在启用下载与控制策略时开放，并复用既有 Origin、Host 和路径校验。诊断仍包含网络、BLE 阶段、接收摘要和算法耗时，不记录敏感原始数据。SSH 分支更新与录播运维命令继续可用。

## 验证与验收

新增回归覆盖生产任务抛错、正常返回与取消后的恢复、恢复期间主动停止、清理失败阻断替换、macOS 连续启停与监听保留、慢目录下状态响应、查询超时、目录缓存失效和并发扫描去重。

自动验证覆盖 Profile/依赖方向、首通知会话、订阅后才启动、全部原始状态字节落盘、设备遥测、停止后重启、录播禁止不读历史、旧配置迁移、网络发送失败/超时隔离、WS 重连与旧订阅失效。Ubuntu 24.04 x86_64 容器单独构建 C++17 算法桥并运行合成 SDK 回归；编译器、CMake、Eigen 和 NumCpp 版本保存在构建日志并对照 `sdk.lock`。

本轮构建记录：Ubuntu 24.04 `linux/amd64` 容器（ARM 宿主上的 x86_64 仿真）、CMake 3.28.3、GCC 13.3.0、Eigen 3.4.0、NumCpp 2.11.0；SDK 合成测试通过。该环境以普通构建账户执行部署用的构建入口；目录拆分后入口为 `linux/ubuntu/build-algorithm-bridge.sh`，内部调用共享构建内核。

完整回归记录：在普通用户拥有的 Ubuntu 24.04 x86_64 隔离 checkout 执行 `PYTHONPATH=tests:. python -m unittest discover -s tests -v`，本轮审查修复后的 633 个用例完成，7 项 Windows 专属用例按平台跳过，无失败；采集启停与首通知落盘场景另行重复 20 次通过。三个 Node 页面/连接顺序脚本、静态 ZIP 构建、compileall、协议/版本门禁及 source-train workflow 契约通过。本分支此前完成的原生桥 JSON 烟雾和 SDK CTest 通过，本轮未改原生算法源码。

目标 N100/N150 Ubuntu 主机仍需验证真实头环扫描/首包、拔断与重连、关机重启、自启与权限、有线断开/恢复、多客户端、录播循环和长时间运行；用真实且获准使用的录制数据比对 SDK。容器与合成测试不能作为这些项目的验收证明。

## Linux 部署目录分离

Ubuntu 部署与 SSH 运维、systemd、logrotate 均归入 `linux/ubuntu/`，麒麟项目脚本与 systemd 渲染器归入 `linux/kylin/`。两边各自校验发行版与编译器，只调用 `linux/common/` 的共享 CMake 构建内核；共享内核不选择平台、不导入麒麟打包器。麒麟引导源码包只收录 kylin/common，不携带 Ubuntu 部署策略。根目录旧命令作为兼容转发，保留已发布文档、SSH 更新器和旧服务入口；新调用均使用平台路径。现场数据和配置位置不变，目录整理不代表新的实机验收。

目录拆分验证：Ubuntu 24.04 `linux/amd64` 仿真容器、普通用户与可写隔离 checkout 的完整 Python 回归完成 636 项，7 项 Windows 专属测试跳过，无失败（424.579 秒）。新 Ubuntu 平台入口构建算法桥成功，SDK CTest、JSON 冒烟、旧构建入口分派、麒麟入口拒绝错误宿主、全部 Linux Shell 语法、README 链接、页面脚本和版本/协议门禁通过。新增回归验证平台依赖方向、兼容转发的参数/退出码/工作目录、旧 systemd 单元归属和麒麟包不含 Ubuntu 部署目录。真实麒麟/Ubuntu 主机的服务升级、SSH 更新及设备验收仍待现场执行。

## HRV 链路诊断

日志统一以 `HRV trace:` 开头，后接结构化 JSON。它只记录字段形态、输入字节/包计数、窗口时间、会话和批次标识、投递结果，不记录心率、HRV 数值、原始字节、完整算法输出或主体标识。输入计数不能用来推定已确认采样率。

| 阶段 | 可判断的问题 |
| --- | --- |
| `bridge_response` | 算法进程是否响应、有无 HR 输入；`sdkHrvState` 表示 C++ SDK 返回值形态，`serializedHrvState` 表示桥接序列化形态，`hrvState` 是 Python 实际解析到的字段。`sdkHrvState=non_finite` 且 `serializedHrvState=null` 明确定位到 C++ 序列化；`outcome=error/not_ready/cancelled` 与 SDK 返回 null 分开。 |
| `engine_result` / `application_result` | 同一 `batchId` 是否仍保留字段；无结果时可见错误原因及 backlog。 |
| `recording_enqueue` / `recording_confirmed` | 保存的字段形态、入队是否接受和持久化是否确认；入队成功不等于写盘完成。 |
| `late_result` / `application_skipped` | 算法迟到只保存而未再发布，或结果已过期/连接会话结束。 |
| `gateway_received` | 网关收到的应用结果、`gatewayBootId`、采集窗口与会话标识。 |
| `payload_projection` / `delivery_projection` | `upstreamHrvState`、`hrvState` 与 `change` 显示投影前后是否丢失；`hrRequested=false`、`outcome=not_requested` 表示该次投影未选择 HR，属于正常过滤。 |
| `latest_snapshot` / `latest_selected` / `latest_projection` / `latest_response` | `getLatest` 实际选择的快照、投影与待发送结果；不读取额外历史文件。 |
| `subscription_overwritten` / `subscription_skipped` / `subscription_failed` | 最新值合并、非法结果过滤或发送失败；与某个字段变成 null 分开判断。 |
| `subscription_sent` / `websocket_sent` | 回调成功与 WebSocket 实际编码、发送后的 JSON 字段状态；后者带投影前状态，发现编码后字段变化时显示 `change`。 |
| `replay_read` / `replay_projection` | 已保存数据读取和录播投影，`mode=replay`；不运行或重算算法。 |

字段状态为 `missing`、`null`、`zero`、`number`、`non_finite`、`invalid_type`。`zero` 只表示算法返回有限的 0，不推断其是否完成预热。`hrState` 与 `hrValueState` 分别说明 HR 对象、心率字段是否存在。旧桥接程序没有 SDK 诊断元数据时，`sdkHrvState=unavailable`；仍能观察返回 JSON，不能据此推断 SDK 返回了 null。更新源码后按平台既有部署流程重建 bridge，Ubuntu 使用 `linux/ubuntu/update-ubuntu.sh`。

默认 INFO 下记录首次观测、状态变化和周期摘要；`logging.diagnostics_interval_seconds` 控制稳定状态的摘要间隔，0 仅关闭周期摘要。`logging.level="DEBUG"` 可记录每个窗口，排查结束恢复 INFO。沿用既有日志轮转，不把 DEBUG 逐窗口日志作为长期运行配置。

先在同一 `batchId` 中比较 `bridge_response → engine_result → application_result → gateway_received`，再用 `timestampMs`（对应 `windowEndMs`）、`gatewayBootId`、`mode` 与 `subscriptionId` 关联投递日志。录播还应对照 `recordingSessionId`；循环录播会重复原始时间戳。日志字段不加入北向报文，算法输入、数值结果、有效性与订阅行为保持原规则。

在 Ubuntu 服务日志中检索：

```bash
sudo journalctl -u neurobridge --since '10 minutes ago' --no-pager | grep 'HRV trace:'
```

合成诊断示例（不代表真实输入频率）：

```text
HRV trace: {"stage":"bridge_response","batchId":"batch-example","timestampMs":600,"sdkHrvState":"non_finite","serializedHrvState":"null","hrvState":"null"}
HRV trace: {"stage":"payload_projection","timestampMs":600,"hrRequested":true,"upstreamHrvState":"number","hrvState":"missing","change":"number->missing"}
```

前者说明 SDK 的非有限值在 C++ 桥接时转成 null；后者说明该次投影丢失了上游数值字段。若 `websocket_sent` 仍为 `number/zero`，继续检查客户端接收与显示。本轮自动测试与合成 SDK 验证不替代现场异常数据定位。

HRV 诊断验证：Ubuntu 24.04 x86_64 仿真容器的完整 Python 回归完成 648 项，7 项 Windows 专属用例跳过，无失败（355.683 秒）；原生 CTest 两项通过，覆盖 SDK EEG 回归及 HRV 非有限值序列化、诊断启用/关闭。对 900 个合成 HR 样本、300 个窗口比较诊断前后 bridge，算法输出完全一致，新增 SDK 状态元数据完整；实际 WebSocket JSON、录播、持久化确认、脱敏、状态变化和节流均有自动回归。现场 HRV 缺失的根因仍需部署后日志确认。

## Ubuntu 原生 DEB 打包

Ubuntu 固定 Ubuntu 24.04 x86_64，采用预编译 `amd64` DEB；麒麟保持面向多种 CPU/ABI 的源码引导 DEB，两种安装器不互相调用。Ubuntu 在 CI 中用现有 `linux/ubuntu/build-algorithm-bridge.sh` 构建 C++17 算法桥，SDK 静态链接，Python 3.12 wheel 逐个锁定 URL/摘要后离线组包。安装时不下载依赖、不运行 pip、不编译；系统 Python 3.12、BlueZ、DBus 等作为明确的 DEB 依赖，由基础镜像提前准备。

资源归 `packaging/ubuntu/`；构建器为 `tools/build-ubuntu-package.py`，平台服务模板继续归 `linux/ubuntu/systemd/`。包内应用、依赖与启动器使用 `/opt/neurobridge/`，服务文件使用 `/usr/lib/systemd/system/`。Profile 由包内 defaults 固定为 `ubuntu_headband_wired`；配置加载规则和算法数学逻辑不变，未显式定制的算法命令指向随包二进制。

首次安装不启用、不启动服务、不修改网卡。操作员确认配置中的地址、子网、网卡、端口后再启用服务。升级停止并恢复原先运行的本包服务，保留已有配置、录制与日志；remove/purge 也保留这些数据。源码部署在 `/etc/systemd/system/` 的同名单元/drop-in 会明确阻止安装，须先备份并停止旧部署、迁移单元；包安装后不混用源码更新器。完整命令见[Ubuntu 安装包说明](../../../packaging/ubuntu/README.md)。

发布矩阵新增 `ubuntu-24.04-x86_64-deb`；Windows 4 项、麒麟 1 项、Ubuntu 1 项，共 6 项，聚合、校验与交付 ZIP 都识别 Ubuntu。新增 `Ubuntu native package` 工作流只构建并上传 Actions Artifact，发布生命周期仍由既有源码训练流程控制。安装验证使用断网的 Ubuntu 24.04 amd64 运行时容器，容器内没有编译器、pip 或 SDK 源码。验证记录区分实际 DEB 执行、合成服务状态测试与待完成的实机验收；首次新增包没有历史 DEB，因此自动升级验证为同版本重装，跨版本升级及真实 systemd/BLE/专网恢复验收仍待目标机完成。
