# 本机可视化与兼容 B 端联调网页

该页面复用版本台账登记的 WebSocket 契约。当前默认场景由同一台银河麒麟网关主机加载页面；兼容 `wired_b_side` 策略时，也可供独立 B 端联调。

生产/现场运行先启动 NeuroBridge，然后在同机浏览器访问：

```text
http://127.0.0.1:8080/
```

网关会动态注入 `ws://127.0.0.1:8765/neurobridge/v1/ws`。页面使用 `neurobridge.v1` 子协议，可测试 `getStatus`、`getLatest`、`subscribe` 和 `unsubscribe`，且不会发送 Ping/Pong 或 JSON 心跳。
Windows B 端先将整个 ZIP 解压到普通文件夹，双击 **`configure-wired-ip.bat`**，在系统 UAC 提示中允许管理员权限。只有一个可识别物理有线网卡时自动配置；存在多个时显示列表，输入要连接网关的网卡索引。脚本给该网卡追加 `192.168.88.20/24`，不填写默认网关或 DNS，不修改 Wi-Fi；已有局域网地址保留。DHCP 启用时先启用 Windows 的 DHCP/静态地址共存功能，该功能不受支持或验证失败时停止并报告，不以清空原网络配置作为回退。重复执行已有正确地址时不重复追加。适用 Windows 10/11 和内置 Windows PowerShell 5.1；不能在 ZIP 内直接运行，也不能只复制 BAT 而漏掉同目录的 PS1。

配置结果和错误日志保存在执行账号的 `%LOCALAPPDATA%\NeuroBridge\logs\b-side-ip-时间.log`；窗口显示实际日志路径，按回车关闭。未插网线时可以准备地址，但不能据此认定已连接网关；若 Windows 报告重复 IP，脚本报告错误并回退本次追加的地址。地址已存在但掩码错误、被其他网卡占用或路由冲突时要求人工处理。需要撤销时，由管理员确认该地址由本脚本追加后，仅删除该网卡上的 `192.168.88.20`，不要重置整个网络。

随后双击 `index.html`，页面自动连接部署默认 WebSocket 地址 `ws://192.168.88.10:8765/neurobridge/v1/ws` 并执行一次 `getStatus`；不依赖 `127.0.0.1` 或本机运行网关。现场 IP 或端口不同可手动修改，Windows 脚本中的默认地址也需相应调整。按所选流手动执行 `getLatest`、`subscribe` 和 `unsubscribe`，页面不自动订阅。使用 `neurobridge.v1` 子协议，不发送 Ping/Pong 或 JSON 心跳；重新连接也会先查询状态，旧订阅不复用。开发采集服务显式注入地址时仍优先采用注入值。

B 端专用有线网卡须配置同网段地址（默认 `192.168.88.20/24`），Windows 可用上述脚本，其他系统由管理员配置；网页本身不能修改系统 IP。浏览器连接失败可直接查看页面日志，并对照网关日志核对地址、端口和子协议，再点击连接重试。

Windows 命令依据：[Microsoft netsh interface](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/netsh-interface)。原生 Windows 网卡/UAC/DHCP 共存仍需目标机验收，模拟逻辑检查不代表现场网络已验收。

页面保留最近 500 条日志，包含完整 ISO 时间、目标地址、连接耗时、关闭码、关闭状态和发送错误；原始 Base64 字节与波形值在日志中省略。点击“复制日志”导出当前记录，浏览器限制剪贴板时可手动复制。浏览器不提供网线、ARP、路由等底层断线原因，不能只凭 `error` 或关闭码判定硬件问题。

本机策略会校验页面 Origin；直接双击 `index.html` 形成的 `file://` 页面仅用于资源开发检查，不能连接生产回环 WebSocket。切换旧有线模式时应关闭本机网页服务并使用批准的隔离专网地址，仍不得暴露公网、无线网络或不受控局域网。

## 没有网页时验证收数

将仓库中的 `tools/b-client-probe.py` 单独复制到 B 端，在已准备好的 Python 环境中安装 `websockets==12.0`（离线现场提前准备依赖），然后运行：

```bash
python3 b-client-probe.py ws://192.168.88.10:8765/neurobridge/v1/ws
```

Windows 可使用 `python` 替代 `python3`。地址与端口按现场确认值填写；默认观察最多 20 秒或 5 批数据，可用 `--seconds`、`--max-batches` 调整。

脚本携带 `neurobridge.v1` 子协议，先 `getStatus`、再按可用流订阅，结束时退订；不发送保活帧，只打印批次数、数据来源和时间戳，不打印原始人体数据。`PASS` 表示收到标为有效的实时数据，`REPLAY ONLY` 仅证明历史录播可接收，`NO DATA` 或 `NO VALID DATA` 表示尚未验证有效收数。退出码 `0` 表示收到有效实时或录播数据，`2` 表示无有效数据，`1` 表示连接或协议错误。必须在 B 端运行，才能验证 B 端到网关的网络链路；在网关本机运行不能替代该项验收。
