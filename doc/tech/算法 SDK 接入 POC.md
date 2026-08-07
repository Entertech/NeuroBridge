# 算法 SDK 接入 POC

版本：v0.1

日期：2026-08-05

## 当前实现结论

NeuroBridge 已实现 AffectiveCloud C++ SDK 的进程边界：网关在 `algorithm.enabled=true` 时启动配置的行 JSON bridge 进程，每个完整 BLE 窗口将原始 `FF31` 和 `FF51` 字节以 Base64 交给 bridge。bridge 可调用 SDK 的双通道 `appendEEG` 和 `appendHR` 并返回既有嵌套 `algorithm` 对象；原始 EEG、心率和每类算法指标以独立事件文件按会话保存；指标记录使用各自的输入接收时间和计算完成时间，录播只读取已保存事件，不重新调用算法。

**这不是算法 POC 或现场验收通过的声明。** 当前公开 SDK 的 `Affective.h` 注释默认双通道 EEG 每 0.6 秒为 50 个包、HR 为 3 个包；设备通信规范定义 `FF31=20` 字节、`FF51=1` 字节，且未定义 `FF52`。网关仅持久化这两类原始字节，并将完整的 `FF31`/`FF51` 窗口交给 bridge。部署模板默认 `algorithm.enabled=true`；真实数据的包数、采样率、字节序、输入分组及输出时序仍须验证，不得以 SDK 的零值或单个窗口作为有效算法结论。

## 固定来源

SDK 的仓库、提交和版本锁定在仓库根目录的 [sdk.lock](../../sdk.lock)。Ubuntu 24.04 所需的 AffectiveCloud C++ package 与 NumCpp 锁定源码已随仓库保存在 [`third_party/`](../../third_party/)，因此完成 NeuroBridge 源码 checkout 后，bridge 构建不需要再访问 GitHub 或下载 SDK。macOS POC 仍可使用 [build-algorithm-bridge.command](../../mac/build-algorithm-bridge.command) 构建合成输入烟雾测试。不要把构建产物、录制人体数据或 SDK 缓存提交到本仓库。

Ubuntu 24.04 安装器以 `neurobridge` 服务账户调用 [`linux/build-algorithm-bridge.sh`](../../linux/build-algorithm-bridge.sh)，从当前 checkout 的 `third_party/` 构建，并以 root 所有、只读的形式安装到 `/usr/local/lib/neurobridge/neurobridge_affective_bridge`。网关未配置 `algorithm.command` 时自动使用该固定路径。目标机可匿名使用 Git 获取 NeuroBridge 源码；源码到位后的安装、bridge 构建和运行不执行 `git` 或网络下载。SDK 构建与固定路径安装不代表算法 POC 或现场验收通过；启用前仍须在 Ubuntu x86_64 记录 Ubuntu 版本、`cmake --version`、`c++ --version`、Eigen3 版本、NumCpp 版本、SDK commit 和 bridge SHA-256。C++ SDK 要求 C++17、Eigen3、NumCpp。

## bridge 合同

网关通过标准输入输出与 bridge 通信，每行一个 UTF-8 JSON 对象。请求只含未经解包的窗口字节：

```json
{
  "timestampMs": 1785800000600,
  "eegRawBase64": "...",
  "hrRawBase64": "..."
}
```

成功响应必须是：

```json
{
  "algorithm": {
    "eeg": { "wave": { "left": [], "right": [], "single": null }, "bandPower": { "alpha": 0.0 }, "quality": 0.0 },
    "sleep": { "updated": false, "degree": 0.0, "state": 0, "stage": 0, "spindle": 0.0 },
    "attention": 0.0,
    "hr": { "value": 72, "hrv": 0.0 },
    "pressure": 0.0
  }
}
```

bridge 保留每个窗口内的原始字节顺序，不补零、截断、重排或跨相邻窗口拼接；不得以 SDK 示例的包长拒绝或重编码已确认的设备包。窗口不足、算法异常或返回不符合合同时，bridge 或网关必须令该算法结果无效并给出 `invalidReasons`。数值范围、单位和有效阈值须记录在验收报告后才向 B 端承诺。

## POC 门槛

1. 在目标 Ubuntu x86_64 上用实际头环采集连续原始 `FF31`、`FF51`，确认 20、1 字节长度以及每 600 ms 的实际包数。
2. 使用同一录制数据运行 [`tools/run-algorithm-poc.py`](../../tools/run-algorithm-poc.py)，验证 bridge 获得的 `FF31`/`FF51` 输入字节序、包数、完整性、启动和响应时序；报告不得包含原始字节或算法数值。
3. 抽样比对 bridge 输出与 SDK 官方演示/参考实现；记录算法字段、单位、范围、延迟和无效条件。
4. 启用 `algorithm.enabled` 后，分别验证实时、录播、设备断线重连、bridge 异常与恢复；录播不得重新计算算法。
5. 通过上述项后，确认固定 bridge 路径可用并更新此文档的 POC 结论为“POC 已验证”；现场演练通过后才可标为“现场验收通过”。

## Ubuntu x86_64 受控执行流程

以下操作只能在目标 Ubuntu x86_64、已获得受试者授权的受控环境执行。全程不打印、复制或提交 JSONL 中的 Base64 原始数据；报告只保存包数、异常计数、bridge 摘要和输出字段名。

1. 先以 `algorithm.enabled=false` 完成一次真实头环采集，并停止网关使会话成为已结束录制。记录 `recordingId`，但不要导出或传输原始 JSONL。
2. 从当前 checkout 运行安装器，以服务账户使用仓库内锁定源码构建 bridge 并安装至固定服务路径。该步骤不执行 `apt-get`、`git` 或网络下载；Ubuntu 基础镜像须已具备 Python 运行环境、CMake、C++17 编译器和 Eigen3：

   ```bash
   ./linux/update-ubuntu.sh
   ```

3. 使用 `neurobridge` 服务账户运行一次离线 bridge 传输验证。摘要文件放在受控录制目录中，权限为 `0640`：

   ```bash
   sudo -u neurobridge /opt/neurobridge/venv/bin/python /opt/neurobridge/tools/run-algorithm-poc.py \
     --recording-dir /var/lib/neurobridge/recordings \
     --recording-id <rec-...> \
     --bridge /usr/local/lib/neurobridge/neurobridge_affective_bridge \
     --summary /var/lib/neurobridge/recordings/poc/<rec-...>-algorithm-poc.json
   ```

4. 仅当摘要的 `outcome` 为 `bridge_transport_passed`，并已人工核对输入窗口包数、bridge 版本、输出字段、延迟和异常计数后，才可将 `algorithm.enabled` 改为 `true`，然后重启网关。无需配置 `algorithm.command`。不得以该工具的成功退出码替代算法准确性、单位、范围或现场验收。

5. 开启算法后的现场 POC 必须保留非敏感证据：SDK/NumCpp commit、bridge SHA-256、Ubuntu/编译器/CMake/Eigen 版本、recording ID、起止时间、有效和无效窗口数、bridge 错误摘要、字段/单位/范围/延迟的人工比对结论。只有这些项目均通过，才能将结论更新为“POC 已验证”。
