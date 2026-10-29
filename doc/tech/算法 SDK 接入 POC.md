# 算法 SDK 接入 POC

版本：v0.1

日期：2026-08-05

## 当前实现结论

NeuroBridge 已实现 AffectiveCloud C++ SDK 的进程边界：网关在 `algorithm.enabled=true` 时启动配置的行 JSON bridge 进程，每个完整窗口将原始 EEG 和心率字节以 Base64 交给 bridge。BLE 来源直接使用 `FF31`/`FF51`；当前 USB 串口来源从 28 字节帧原样提取偏移 4～23 的 20 字节 EEG 和偏移 24 的 1 字节心率，复用同一算法合同。bridge 可调用 SDK 的双通道 `appendEEG` 和 `appendHR` 并返回既有嵌套 `algorithm` 对象；原始 EEG、心率和每类算法指标以独立事件文件按会话保存；录播只读取已保存事件，不重新调用算法。

**这不是算法 POC 或现场验收通过的声明。** 当前公开 SDK 的 `Affective.h` 注释默认双通道 EEG 每 0.6 秒为 50 个包、HR 为 3 个包；真实数据的包数、采样率、字节序、输入分组及输出时序仍须验证。项目内麒麟模板保持 `algorithm.enabled=false`；只有目标机本地构建且进程自检成功后，一键脚本才会自动启用。进程自检不包含人体数据，只证明 bridge 在当前系统可启动并符合进程合同；不得以其零值、单个窗口或成功退出码作为算法有效结论。

## 固定来源

SDK 的仓库、提交和版本锁定在仓库根目录的 [sdk.lock](../../sdk.lock)。Ubuntu 24.04 和银河麒麟 V10 x86_64 所需的 AffectiveCloud C++ package 与 NumCpp 锁定源码已随仓库保存在 [`third_party/`](../../third_party/)，因此 bridge 构建不需要再访问 GitHub 或下载 SDK。macOS POC 仍可使用 [build-algorithm-bridge.command](../../mac/build-algorithm-bridge.command) 构建合成输入烟雾测试。不要把构建产物、录制人体数据或 SDK 缓存提交到本仓库。

Ubuntu 24.04 安装器以 `neurobridge` 服务账户调用 [`linux/build-algorithm-bridge.sh`](../../linux/build-algorithm-bridge.sh)，从当前 checkout 的 `third_party/` 构建，并以 root 所有、只读的形式安装到 `/usr/local/lib/neurobridge/neurobridge_affective_bridge`。网关未配置 `algorithm.command` 时自动使用该固定路径。目标机可匿名使用 Git 获取 NeuroBridge 源码；源码到位后的安装、bridge 构建和运行不执行 `git` 或网络下载。SDK 构建与固定路径安装不代表算法 POC 或现场验收通过；启用前仍须在 Ubuntu x86_64 记录 Ubuntu 版本、`cmake --version`、`c++ --version`、Eigen3 版本、NumCpp 版本、SDK commit 和 bridge SHA-256。C++ SDK 要求 C++17、Eigen3、NumCpp。

银河麒麟 V10 x86_64 使用 [`linux/setup-kylin-algorithm.sh`](../../linux/setup-kylin-algorithm.sh)，在目标机上调用同一构建脚本，将成品安装到项目 `.runtime/algorithm/neurobridge_affective_bridge`。脚本验证 CMake 3.22+、C++17 编译器、Eigen3、NumCpp 和 SDK 锁定源码；缺少系统构建依赖时先询问 `yes/no`，未获授权或安装失败则不更改配置。新 bridge 先在独立尝试目录构建，然后执行无人体数据进程自检；只有自检成功才原子替换产物、备份 `.runtime/config/gateway.toml` 并写入 `algorithm.enabled=true` 及项目内命令路径。失败尝试、日志、配置备份和最终产物均保存在 `.runtime/` 且不提交 Git。

## bridge 合同

### 2026-10-28 SDK 更新影响

- 锁定提交由 `5623c5a1a43c6b04c3907d84ba2b9b86f9b010a2` 更新至 `cd967c4f3d26ab1100c15b5e8c23604cc495dd23`。相对旧锁定提交，上游仅修改 `EEGHandler.cpp`；内置快照保留的四处编译补丁见 [`third_party/README.md`](../../third_party/README.md)。
- `EEGHandler` 完成处理后，无论质量为 `NONE`、`POOR` 或 `GOOD`，均输出实际处理后的波形、特征波形与能量；质量评级继续按原算法输出 `0/1/2`，不会统一变为 `-1`。先前佩戴检测失败仍清空缓存并返回零，数据未满窗口仍返回零；注意力、放松度、睡眠等下游算法的质量判断未移除。
- 当前耳机 bridge 使用双通道 `appendEEG`，并与 SDK 的单通道路径共用此处理器。输入的原始字节序、20 字节 EEG/1 字节 HR 分组、JSON 字段及录制格式不变；根对象的 `valid` 仍表示现有处理管线有效性，不因本次更新自动等同于 EEG 质量评级。不得仅凭非零波形认定佩戴正常或算法结果准确。
- Linux、Windows 使用更新后的内置源码；Windows 的源码指纹包含 `sdk.lock` 和 SDK 文件，升级时会使旧自动管理 bridge 缓存失效。手工配置的外部 bridge 仍由操作者自行重建；macOS POC 和源码准备脚本同步固定提交。现有历史录制的结果仍直接读取，不重新计算。
- 合成原生回归位于 [`tests/native/test_sdk_eeg_output.cpp`](../../tests/native/test_sdk_eeg_output.cpp)，覆盖 NONE/GOOD 评级、波形与能量、预热、佩戴失败清理、单双通道路径。配置现有 bridge CMake 工程时增加 `-DNEUROBRIDGE_BUILD_SDK_TESTS=ON`，构建后运行 `ctest --test-dir <构建目录> --output-on-failure`；默认生产构建不包含测试程序。
- 结论级别仍为“源码支持”；合成测试不能替代真实录制数据比对。升级后须分别在银河麒麟 V10 对应架构与 Windows 目标机完成编译、算法对比、实时采集、存在历史录制时串口离线不录播、拔插重连、服务重启及浏览器断开/恢复验证。

本次开发机验证环境为 macOS arm64、Apple Clang 15.0.0、CMake 3.31.1、Eigen 5.0.1、NumCpp 2.11.0。内置 SDK 与 bridge 构建、原生回归及进程自检通过；相同原生回归使用旧处理器时在“NONE 质量保留波形”断言失败。另以 SDK 定义生成的合成字节完成 8 组连续 EEG/心率 bridge 请求，确认 NONE 评级下双通道波形非零、频段能量有限且心率字段保留；这组字节不作为真实耳机采样率或算法准确性证据。Ubuntu CI 已增加独立原生回归任务，其结果须以实际 CI 执行为准。

Python 完整回归命令为 `.venv/bin/python -m unittest discover -s tests -v`，本次共 426 项：418 通过、7 跳过、1 失败。失败项为 `test_kylin_uninstall_helper_purges_a_checkout_end_to_end`：macOS BSD `rm` 不支持卸载脚本的 GNU `--one-file-system` 参数；使用 `origin/master` 的原测试及原卸载脚本也复现相同失败，两个文件均未改动。算法配置/POC、进程隔离和 Windows 构建逻辑的 20 项专项测试全部通过；版本台账/对外协议检查、网页连接恢复检查和修改脚本语法检查通过。

### 左右脑波输出限幅调整（2026-10-28）

`sdk.lock` 的 `local_patches` 标识本项目相对上游的 `dual_eeg_waveform_limit_2000` 补丁：`DP/EEG.cpp` 左、右波形从原 `[-500, 500]` 改为 `[-2000, 2000]` 限幅。范围内保留 `EEGHandler` 滤波后的幅值，超界分别截到 -2000 或 2000，仍按既有规则保留两位小数。原始字节、滤波、能量计算、质量评级、预热与佩戴检测逻辑保持原样；单通道兼容路径没有本次左右通道修改。限幅本身不改变窗口有效性，真实幅值单位与准确性仍须按原 POC 门禁确认。

macOS 构建改用本仓库内 SDK 快照，与 Windows/麒麟一致，避免从上游重新拉取而丢失本地补丁。必须重建 bridge 后才生效；先前提交 `071b687` 的 CI 包仍有原限幅，历史录制仍直接读取已保存结果。用户随后将“完全移除”修订为正负 2000，并明确要求不打包；提交 `895dee8` 的无限幅实现已被本节规则替代。原生测试对正负超界和界内输入分别逐点比对两个通道，覆盖 SDK 的普通/耳道双通道模式。

原生回归覆盖正负超界值精确截到 2000/-2000、界内幅值逐点保留、既有舍入及质量评级；正负 2000 边界断言在前一个无限幅实现上复现失败。更新后原生回归、29 项相关 Python 测试、8 组连续合成 JSON 请求、macOS 构建脚本语法和协议台账检查通过，左右通道最终 JSON 的正负峰值均为 2000/-2000。合成结果不替代目标机真实数据验收，本次不触发打包。

### 进程消息格式

Windows CI 打包修复（2026-10-28）：运行 `40644417375` 在准备嵌入式 Python 时因浮动 `get-pip.py` 地址内容变化而摘要不符。现固定至 PyPA 提交 `af54dfe793b24685f8dc4ebba0630d9f2d77653c` 的同一文件，独立下载核验仍匹配现有 `fb24e693bab954209a063d90953621412ccad4a500905a726286e038f508ddf6`；没有跳过校验或升级安装器输入。重新构建的源码提交及包摘要以 CI Artifact 为准。

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

1. 在最终目标 Linux x86_64 环境（正式环境为 N100/N150 + 银河麒麟 V10）上用实际头环采集连续原始 EEG/心率投影，确认 20、1 字节长度以及每 600 ms 的实际包数。
2. 使用同一录制数据运行 [`tools/run-algorithm-poc.py`](../../tools/run-algorithm-poc.py)，验证 bridge 获得的 `FF31`/`FF51` 输入字节序、包数、完整性、启动和响应时序；报告不得包含原始字节或算法数值。
3. 抽样比对 bridge 输出与 SDK 官方演示/参考实现；记录算法字段、单位、范围、延迟和无效条件。
4. 启用 `algorithm.enabled` 后，分别验证实时、录播、设备断线重连、bridge 异常与恢复；录播不得重新计算算法。
5. 通过上述项后，确认固定 bridge 路径可用并更新此文档的 POC 结论为“POC 已验证”；现场演练通过后才可标为“现场验收通过”。

## 目标 Linux x86_64 受控执行流程

以下操作只能在目标 Linux x86_64、已获得受试者授权的受控环境执行。全程不打印、复制或提交 JSONL 中的 Base64 原始数据；报告只保存包数、异常计数、bridge 摘要和输出字段名。麒麟环境先在项目根目录执行 `./linux/setup-kylin-algorithm.sh`；看到 `Algorithm bridge smoke test: OK`、`algorithmEnabled=true` 和 `Algorithm setup complete` 只表示可继续真实数据 POC。Ubuntu 兼容基线则继续按下述固定服务路径流程执行。

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
