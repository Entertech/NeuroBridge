# macOS 头环采集 POC

此目录用于在 Ubuntu 网关到货前，以 macOS 和现有头环验证真实 BLE 原始通知、600 ms 分窗、录制和北向录播。它不是 Ubuntu 部署验证，也不能代替目标机上的 BlueZ、蓝牙芯片或 systemd 验收。

仅在已获得受试者授权的受控环境采集。原始人体数据不得提交、发到聊天或接入公共网络。

## 一键启动

在 Finder 中双击 [start-poc.command](start-poc.command)，或在仓库根目录运行：

```bash
./mac/start-poc.command
```

它会准备兼容的本机环境、保留已有的本机配置、启动服务并自动打开采集网页。关闭启动器所在终端窗口或按 `Ctrl-C` 会停止采集。若启动失败，终端会给出 `/tmp/neurobridge-headband-poc/poc-server.log`，可直接提供该文件用于排查。首次运行若提示找不到 `uv`，先执行 `brew install uv` 后重试。

同一台 Mac 只允许一个采集实例。重复双击时，启动器会打开已有控制台而不打断当前采集；若 `8090` 被其他程序占用，会在启动前明确提示。

采集过程中可在网页点击“一键保存 ZIP”。导出包把原始 EEG、原始心率和每类算法指标分别保存，并带有清单和校验和；文件字段见 [头环数据采集包格式说明 v0.1](../doc/tech/对外/头环数据采集包格式说明/头环数据采集包格式说明_v0.1.md)。

## 一键停止

在 Finder 中双击 [stop-poc.command](stop-poc.command)，或执行：

```bash
./mac/stop-poc.command
```

它只会向本项目的本机采集进程发送正常停止信号，让服务停止采集、断开头环并完成当前窗口的清理；不会按端口终止其他程序。

## 1. 安装本地依赖

在仓库根目录运行：

```bash
# 若尚未安装 uv：brew install uv
uv python install 3.11
uv venv /tmp/neurobridge-mac-venv --python 3.11
uv pip install --python /tmp/neurobridge-mac-venv/bin/python "setuptools<81"
uv pip install --python /tmp/neurobridge-mac-venv/bin/python --no-deps -e .
uv pip install --python /tmp/neurobridge-mac-venv/bin/python bleak==0.19.0 websockets==12.0
```

首次使用时，请在 macOS 的“隐私与安全性 → 蓝牙”中允许你的终端应用访问蓝牙。首次一键启动会自动通过 Homebrew 安装缺少的 `cmake`、`eigen`，随后构建官方 C++ 算法 bridge；仍须预先安装 Homebrew 和 Xcode Command Line Tools（提供 `git`）。已成功构建过的锁定 SDK 源码可在离线状态复用。

macOS POC 必须使用 Python 3.11；系统默认 Python 3.14 与锁定的 Bleak 0.19 / PyObjC 8.5 不兼容。上述命令将虚拟环境建在项目外，避免产生工作树文件。

若页面扫描不到头环，先执行只读扫描并据此确认模板里的 `device_name` 和 `model_nbr_uuid`：

```bash
/tmp/neurobridge-mac-venv/bin/python mac/scan_devices.py
```

该工具不输出设备地址，也不连接、配对或写入头环。

## 2. 配置并开始采集

复制模板到项目外并仅修改本机配置；录制目录已经默认设在项目外：

```bash
cp mac/gateway.capture.toml.example /tmp/neurobridge-gateway.capture.toml
/tmp/neurobridge-mac-venv/bin/python mac/poc_server.py --config /tmp/neurobridge-gateway.capture.toml
```

浏览器打开终端提示的 `http://127.0.0.1:8090/`。页面加载会立即发起本机扫描；网关会扫描名称和 Model Number UUID 均匹配的头环，订阅 `FF31`、`FF32`、`FF51` 与电量通知，启动算法输入 bridge，随后向 `FF21` 写入 `0x05` 启动采集。按 `Ctrl-C` 结束时会尝试写入 `0x06` 停止采集并断开。

成功连接后的原始记录会保存到 `/tmp/neurobridge-headband-poc/raw/rec-*.jsonl`。文件名中的 `rec-...` 是后续录播所需的 recording ID。请只记录 ID，不要复制 JSONL 中的 Base64 原始字节。

## 3. 输出不泄露原始字节的采集报告

停止采集后运行：

```bash
/tmp/neurobridge-mac-venv/bin/python mac/capture_report.py --recording-dir /tmp/neurobridge-headband-poc
```

报告会验证并汇总：

- `FF31` 的 20 字节与 `FF51` 的 1 字节通知契约；
- 每个 600 ms 窗口的包数、记录时间范围和窗口间隔；
- 声明字节数与实际 Base64 解码后的长度是否一致；
- 无效窗口和原因。

报告不打印任何原始字节或 Base64 内容。只有所有格式检查通过时退出状态才为 0；是否佩戴和电量数值仍处于待 POC 确认状态，不应从此报告推断。

## 4. 用真实录制验证录播和 B 端

打开采集控制台中的“打开 B 端订阅页”。它会连接同一进程提供的 `ws://127.0.0.1:8765/neurobridge/v1/ws`；实时采集中订阅 `eeg.raw`/`hr.raw`，并验证 `getStatus`、`unsubscribe`。录播时才将 `/tmp/neurobridge-gateway.capture.toml` 中的 `ble.enabled` 改为 `false`，在 `recording.replay_recording_id` 写入已采集的 `rec-...`，保持同一 `recording.directory` 后重启 POC；随后验证 `replayEnded`。这只验证本地环回，不应替代专用有线网络联调。

## 算法 bridge 的当前边界

`start-poc.command` 会先运行 `build-algorithm-bridge.command`，从锁定的 AffectiveCloud C++ SDK 和 NumCpp 2.11.0 构建本机 bridge；产物仅位于 `/tmp/neurobridge-affective-runtime/affective_bridge`。它将每个完整窗口的原始字节不经重排地交给 SDK 的双通道 EEG 与心率入口，并返回现有算法数据结构中的脑波、频段、睡眠、注意力/放松度/愉悦度/心流、心率/HRV、压力、和谐度和激活度。采集页的“算法输出”会显示这些标量和频段；为避免在浏览器日志中保留第二份生理时序，页面日志不会显示处理后的波形数组。

本机已完成 C++ SDK 的构建和合成输入烟雾测试，证明 bridge 调用链和输出字段可用；**这不等于真实头环算法结果已验收。** SDK 在预热、信号质量不佳或尚未形成结果时可能输出 `0`，不能把单个窗口的数值当作有效性结论。Ubuntu x86_64 构建、真实 Flowtime 原始字节对比、字段范围/单位/延迟确认以及现场验收仍未完成；生产配置须继续保持 `algorithm.enabled=false`，直至这些验证完成。

## POC 结束时应保存的非敏感结果

- 设备是否能稳定被扫描和连接；
- 采集报告的汇总输出及退出状态；
- 连接、断开和重连的时间点与错误摘要；
- recording ID、采集时长和授权记录的引用。

不要保存或共享未脱敏的 JSONL、Base64 原始字节、受试者身份或任何凭据。
