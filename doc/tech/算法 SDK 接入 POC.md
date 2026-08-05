# 算法 SDK 接入 POC

版本：v0.1

日期：2026-08-05

## 当前实现结论

NeuroBridge 已实现 AffectiveCloud C++ SDK 的进程边界：网关在 `algorithm.enabled=true` 时启动配置的行 JSON bridge 进程，每个完整 BLE 窗口将原始 `FF31` 和 `FF51` 字节以 Base64 交给 bridge。bridge 以双通道 `appendEEG` 和 `appendHR` 调用 SDK，显式启用 EEG、HR、放松、注意力、愉悦、睡眠、心流、压力、和谐度与激活度，并返回已映射的嵌套 `algorithm` 对象。算法结果与原始窗口分别写入 `algorithm/`、`raw/` JSONL 文件，录播只读取两个文件，不重新调用算法。

**这不是算法 POC 或现场验收通过的声明。** 当前公开 SDK 的 `Affective.h` 注释默认双通道 EEG 每 0.6 秒为 50 个包、HR 为 3 个包；当前接入的 Flowtime 文档 profile 是 `FF31=20` 字节、`FF51=1` 字节。macOS POC 已完成 SDK 源码构建与合成输入烟雾测试，未使用任何人体数据；真实数据的包数、采样率、字节序、输入分组及输出时序尚未完成验证。因此生产模板仍保持 `algorithm.enabled=false`，不得以 SDK 的零值或单个窗口作为有效算法结论。

## 固定来源

SDK 的仓库、提交和版本锁定在仓库根目录的 [sdk.lock](../../sdk.lock)。macOS POC 使用 [build-algorithm-bridge.command](../../mac/build-algorithm-bridge.command) 将源码和 NumCpp 2.11.0 构建在 `/tmp/neurobridge-affective-runtime`；Ubuntu 可使用 [prepare-algorithm-sdk.sh](../../tools/prepare-algorithm-sdk.sh) 下载到受控构建目录后按同一锁定版本构建。不要把构建产物、录制人体数据或 SDK 缓存提交到本仓库。

SDK 构建前在 Ubuntu x86_64 记录以下信息：Ubuntu 版本、`cmake --version`、`c++ --version`、Eigen3 版本、NumCpp 版本、SDK commit 和 bridge commit。C++ SDK 要求 C++17、Eigen3、NumCpp；其源码的 CMake 配置可作为构建入口。

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

bridge 保留每个窗口内的原始字节顺序，不补零、截断、重排或跨相邻窗口拼接；`FF31` 不是 20 字节整数倍时拒绝该窗口。窗口不足、算法异常或返回不符合合同时，bridge 或网关必须令该窗口 `valid=false` 并给出 `invalidReasons`。数值范围、单位和有效阈值须记录在验收报告后才向 B 端承诺。

## POC 门槛

1. 在目标 Ubuntu x86_64 上用实际头环采集连续原始 `FF31`、`FF51`，确认 20、1 字节长度以及每 600 ms 的实际包数。
2. 使用同一录制数据验证 bridge 输入与 SDK 的每个算法调用：字节序、包数、完整性、触发条件和结果时序。
3. 抽样比对 bridge 输出与 SDK 官方演示/参考实现；记录算法字段、单位、范围、延迟和无效条件。
4. 启用 `algorithm.enabled` 后，分别验证实时、录播、设备断线重连、bridge 异常与恢复；录播不得重新计算算法。
5. 通过上述项后，填写部署配置的 bridge 命令并更新此文档的 POC 结论为“POC 已验证”；现场演练通过后才可标为“现场验收通过”。
