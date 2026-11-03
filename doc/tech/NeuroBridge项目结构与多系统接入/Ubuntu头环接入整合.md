# Ubuntu 头环接入整合

日期：2026-11-03

状态：内部实施说明；合成验证与 Ubuntu x86_64 容器构建分别记录，目标机现场验收待完成。

## 基线与范围

分支 `codex/ubuntu-unified-architecture` 从 `codex/linux-headband-ethernet-gateway` 的 `896570050550fcaa2f55c92d2633051d3d8cbdfe` 创建，rebase 到默认分支 `master` 的 `715ddb8`。原分支与 `ubuntu_0.0.1`、`ubuntu_0.0.2`、`ubuntu_0.0.3` 标签保留，便于核对旧现场行为。

本轮在默认分支的统一架构中支持 Ubuntu 24.04 LTS x86_64 头环网关。应用版本沿用版本台账的默认分支基线，报文版本和已锁定协议保持原值；此次需求分支不执行发版、打新标签或修改正式安装包矩阵。Ubuntu 使用源码部署与 systemd，Windows/麒麟继续使用各自的串口本机 Profile。NanoPi、Ubuntu ARM、其他 Ubuntu 版本和现场验收不由本轮合成结果推定。

## 固定组合与依赖方向

`entrypoints → Bootstrap → Profile → RawDataSource → RawDataParser → ApplicationService → RecordingRepository / AlgorithmEngine / NorthboundSink`。

| 层 | Ubuntu 实现与职责 |
| --- | --- |
| Profile | `ubuntu_headband_wired` 固定 Ubuntu x86_64、蓝牙头环、专用有线 B 端；支持录制与按部署许可录播，关闭本机 UI。 |
| Source / Control | `BluetoothBleakSource` 封装 `FlowtimeAdapter`，复制通知字节后形成有界 `RawChunk`；`BluetoothSessionControl` 按连接会话写启动/停止，过期会话拒绝写入。 |
| Parser | `HeadbandBleParser` 解析 EEG、HR、佩戴/接触与电量通道，保留原始字节和关联标识；不调用算法或网络。 |
| Application | 准备会话、600 ms 窗口、算法串行处理、原始与结果持久化、快照、订阅与录播许可；不导入设备 I/O。 |
| Storage | 复用共享有界分段 writer、manifest、容量状态和崩溃恢复，避免重复写一套旧格式。原始设备帧携带通道、会话及接收时间；算法结果关联原采集窗口。 |
| Northbound | 复用共享 Controller/Codec 与 WebSocket 服务；业务错误使用中文，码值、标识和结构沿用既有契约。 |
| 运维 | `CaptureControl` 持有统一采集适配器工厂；停止和重启采集均经过同一组合根，监听服务持续存在，重启创建新的应用/设备/算法会话。 |

旧 `business.Gateway` 和 `device` 入口保留兼容调用与测试用途。Ubuntu 生产入口只通过 Bootstrap 的 `GatewayApplication` 与 ApplicationService，页面不选择设备类型或数据源。

## BLE 生命周期与故障隔离

1. 扫描配置匹配的头环，选择最强候选；连接后先建立录制与算法会话。
2. Application 只准备会话，此阶段不发送启动指令。Source 安装全部必需通知后才调用绑定该会话的 Control，发送一次启动指令。设备在订阅时推来的首包已有落盘会话。
3. 电量首次读取与可选通知失败只记录诊断。佩戴与电量状态投影到已有状态字段；原始字节走共享分段持久化，不进入 EEG/HR 算法窗口或录播。空包与非法长度有诊断，不让主任务退出。
4. 同时识别 Bleak 0.19 wrapper 与 backend 的断线回调，忽略旧连接回调，并以连接轮询兜底。断线排空已排队回调，清空当前电量/佩戴并关闭对应会话。
5. 算法继续使用默认分支更新后的 SDK 和原始输入，不重新编码或重采样。交换串行化，超时/取消废弃旧响应流；终止有期限，失败升级 kill。
6. 实时分发沿用默认分支的每流最新值合并，内存不随慢连接无限增长；录播使用有界队列。超时或发送失败关闭该连接，采集、落盘与其他客户端继续；恢复后重新 `getStatus` 和订阅。

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

自动验证覆盖 Profile/依赖方向、首通知会话、订阅后才启动、全部原始状态字节落盘、设备遥测、停止后重启、录播禁止不读历史、旧配置迁移、网络发送失败/超时隔离、WS 重连与旧订阅失效。Ubuntu 24.04 x86_64 容器单独构建 C++17 算法桥并运行合成 SDK 回归；编译器、CMake、Eigen 和 NumCpp 版本保存在构建日志并对照 `sdk.lock`。

本轮构建记录：Ubuntu 24.04 `linux/amd64` 容器（ARM 宿主上的 x86_64 仿真）、CMake 3.28.3、GCC 13.3.0、Eigen 3.4.0、NumCpp 2.11.0；SDK 合成测试通过。该环境复用了部署使用的 `linux/build-algorithm-bridge.sh`，以普通构建账户执行。

完整回归记录：在普通用户拥有的 Ubuntu 24.04 x86_64 隔离 checkout 执行 `PYTHONPATH=tests:. python -m unittest discover -s tests -v`，623 个用例完成，8 项按平台或前置条件跳过，无失败；原生桥 JSON 烟雾、SDK CTest、两个静态页面与连接顺序脚本、静态 ZIP 清单及协议/版本门禁通过。

目标 N100/N150 Ubuntu 主机仍需验证真实头环扫描/首包、拔断与重连、关机重启、自启与权限、有线断开/恢复、多客户端、录播循环和长时间运行；用真实且获准使用的录制数据比对 SDK。容器与合成测试不能作为这些项目的验收证明。
