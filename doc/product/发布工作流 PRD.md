# NeuroBridge 对外发布工作流 PRD

- **文档状态**：需求评审稿
- **适用范围**：Windows 软件、银河麒麟系统 V10 软件及 GitHub Actions 发布流程
- **版本依据**：`neurobridge/version_registry.toml`

## 1. 背景与目标

当前仓库已有单元测试、北向协议文档 PDF 和部分外部文档打包流程，但缺少统一的应用版本门禁和跨平台发布产物流程。本 PRD 目标是让合入 `master` 的变更可审计、可复现，并避免业务代码变化却沿用旧版本号。

目标：

1. 在 PR 合入 `master` 前，自动识别是否改变对应软件的业务代码并执行版本号规则。
2. 形成 Windows 和银河麒麟系统 V10 两类软件的版本化发布包流程。
3. 记录每个软件产物的版本、来源、校验值、构建环境和发布状态。

## 2. 功能需求

### 2.1 应用版本门禁

- 版本唯一来源为 `neurobridge/version_registry.toml` 的 `[application].version`；平台覆盖矩阵唯一来源为 [`release/release_matrix.toml`](../../release/release_matrix.toml)。
- CI 以 PR 目标分支（固定为 `master`）为基线比较版本。
- 业务代码范围由配置维护，至少覆盖 `neurobridge/`、平台部署目录、运行依赖和打包配置；只改文档、测试或网页资源不强制升应用版本。
- 应用版本使用三段数字格式 `MAJOR.MINOR.PATCH`。业务代码变化时，PR 版本不得低于 `master`：修复递增 `PATCH`，向后兼容功能递增 `MINOR` 并将 `PATCH` 归零，破坏兼容性的变化递增 `MAJOR` 并将 `MINOR`、`PATCH` 归零。未按影响级别递增、跳过必要的版本证据或发生版本回退时失败。
- 未改业务代码时版本可不变，但不得低于 `master`。
- 每次版本递增必须在变更记录中写明旧版本、新版本、影响级别、兼容性边界和依据。Actions summary 必须包含基线版本、PR 版本、变更文件和判定理由。

### 2.2 Windows 发布包

- 按发布配置中的版本矩阵为每个 Windows 目标版本生成独立构建结果；正式下载入口只提供汇总 ZIP。
- Windows 支持矩阵固定为 Windows 7、Windows 10、Windows 11，分别覆盖 x86 和 x86_64；每个系统版本与架构组合均必须尝试建包并记录成功、失败或阻断原因。具体配置见 `release/release_matrix.toml`。
- Windows 安装包格式至少拆分为 **EXE** 和 **MSI** 两类；两类包分别构建、校验和发布，不得仅通过重命名复用同一文件。
- 每个包包含软件文件、依赖锁定信息、启动/卸载说明、版本清单和 SHA-256 校验值。
- 尚未完成可执行包验证时只能标记为 `source-only` 或 `candidate`，不得伪称正式安装包。
- 包名、清单和 Artifact 名称必须含平台、架构和应用版本。

### 2.3 银河麒麟系统 V10 发布包

- 银河麒麟 V10 支持服务器版和桌面版，以及 x86_64、ARM64、LoongArch64、MIPS64el、SW64 五类架构；每个发行版与架构组合均必须尝试建包并记录成功、失败或阻断原因。具体配置见 `release/release_matrix.toml`。
- 麒麟每个组合同时交付 **DEB** 和 **RPM** 两类包；两类包分别生成对应的安装、升级和卸载元数据。Workflow 不根据目标机在线探测缩减矩阵。
- 版本和下载来源以麒麟官网或双方确认的官方数据为准，记录来源 URL、抓取时间和校验值。
- 每个包包含安装/升级、依赖检查、服务启动、回滚说明、版本清单和 SHA-256 校验值。
- 未完成目标架构现场验证时只能标记为 `candidate`。

### 2.4 当前流程不包含的历史交付物

- 旧版 Ubuntu 网关使用的 SSH 运维操作指南不属于当前 Windows 或银河麒麟 V10 软件包。
- 旧版 Ubuntu 网关使用的有线网络配置指南不属于当前 Windows 或银河麒麟 V10 软件包。
- 上述历史文档及旧 Ubuntu 部署物不得被当前 Workflow 自动标记为 Windows 或麒麟 V10 的交付内容；如需维护，另建独立的 Ubuntu 维护流程。

### 2.5 发布清单

达到最低发布门槛的构建生成符合 [`schemas/release-manifest.schema.json`](../../schemas/release-manifest.schema.json) 的包外 `release-manifest.json`，包含应用版本、Git commit、构建时间、触发方式、全部 Windows EXE/MSI 与麒麟 DEB/RPM 目标的版本/架构/格式/状态，以及合格包的文件名/SHA-256、平台 ZIP、总 ZIP、官网来源和验证日志。它在公开 Release 前固定为 `candidate`；公开成功后由独立发布回执记录 `published`，不回写已封包文件。北向协议版本仍可作为软件兼容性元数据记录，但不触发本流程的文档打包。

## 3. 工作流与门禁

1. `pull_request -> master`：运行版本门禁、测试和 Windows/麒麟候选包构建，不创建 GitHub Release 或 tag。
2. PR 合入 `master` 后由 `push -> master` 自动触发发布流程。若应用版本未递增，记录跳过原因而不重发同版本；若版本递增，尝试全部矩阵目标并记录逐项结果；Windows 和麒麟各至少一个可执行且通过自动校验的包时，生成两个平台 ZIP 和唯一总 ZIP。
3. 两个平台达到最小成功条件，且平台 ZIP、总 ZIP、清单和日志构建并校验通过后，创建指向本次 `master` commit 的注释 tag `v<applicationVersion>`；随后创建 draft GitHub Release，上传唯一总 ZIP、包外清单和验证日志并复验，最后公开 Release。未完成的矩阵组合在 Release 说明中逐项列明；tag 创建后的网络失败按 §7.2 原地恢复，不移动 tag 或覆盖已有正式 Release。
4. 正式 Artifact 保留 **30 天**，下载权限为**所有能够访问仓库 Actions 的用户**，不做额外权限限制。
5. 当前版本暂不进行代码签名、安装包签名或公证；清单中的 SHA-256 仅用于完整性校验。后续启用签名时必须新增版本规则和受保护凭据流程。
6. 任一平台无合格包、成功包的校验值不一致、成功包缺日志或发布资产上传失败时，工作流失败并说明原因。其他矩阵组合失败或来源数据缺失时，记录该组合为 `failed`/`blocked`，不隐藏缺口。

## 4. 非功能要求

- 固定 Python、编译器和依赖版本，保存构建日志，确保可重复构建。
- **离线优先**：构建、测试、打包和校验阶段不访问公网；系统版本/架构矩阵由仓库内固定配置维护，正式 Workflow 不查询官网。
- 依赖和安装源进入受控离线缓存，缓存缺失或校验失败时流程直接失败，不临时联网绕过。
- 官网资料仅用于人工更新仓库配置，不属于 CI 运行时输入。
- 每个 Artifact 可反查 Git commit、版本台账和官网来源。
- 不把凭据、私钥、现场配置或人体原始数据写入 Artifact/日志。
- 不改变北向报文 `protocolVersion`，不把 BLE、SDK 或算法内部细节写入对外文档。

## 5. 验收标准

- 业务代码变更且版本不变的 PR 必须失败并提示提升版本。
- 版本高于 `master` 的 PR 通过，低于 `master` 的 PR 失败并说明回退原因。
- PR 构建生成 Windows EXE/MSI 和麒麟 V10 DEB/RPM 的候选清单，状态与验证事实一致。
- 应用版本递增的 `master` 构建在最低门槛通过时生成带 SHA-256 的包外 `release-manifest.json`、唯一总 ZIP 和 GitHub Release；版本未递增时跳过同版本发布；Actions Artifact 保留 30 天，GitHub Release 作为长期下载入口。
- Artifact 可在无公网环境按清单校验。
- 现有单元测试和协议兼容性检查全部通过。

## 6. 已确认约束

- 麒麟 V10 当前固定支持服务器版、桌面版，以及 x86_64、ARM64、LoongArch64、MIPS64el、SW64 五类架构；每个组合同时生成 DEB 和 RPM，矩阵写入仓库配置，Workflow 不联网发现版本。
- Windows 适配矩阵固定包含 Windows 7、Windows 10、Windows 11，并分别覆盖 32 位（x86）和 64 位（x86_64）；每个组合单独验证并记录兼容性结果。
- Windows 每个适配组合都尝试生成 EXE 和 MSI；麒麟 V10 每个适配组合都尝试生成 DEB 和 RPM，并分别记录安装、升级和卸载结果。全部 32 个包目标均属于支持矩阵；某目标未形成合格包时必须在清单和 Release 说明中写明状态、原因与后续验证项。
- “各个版本”专指操作系统版本适配性，不要求回溯构建历史应用版本。
- 构建、测试、打包和校验默认离线；官网版本信息、依赖和安装源先缓存并校验，正式构建只读取缓存。
- PR 合入 `master` 后触发发布；包与总 ZIP 校验通过后创建 tag，随后创建 draft GitHub Release，上传和复验成功后公开。Actions Artifact 保留 30 天，GitHub Release 长期保留；当前不做签名或公证。
- Windows 和麒麟支持矩阵以本 PRD 与仓库配置为准；官网新增版本不会自动进入 CI，需人工审阅后更新配置。
- 先分别生成 `windows-v<version>-<date>.zip` 和 `kylin-v<version>-<date>.zip`，再将两个平台 ZIP、构建清单和验证日志压缩为唯一交付物 `neurobridge-v<version>-<date>.zip`。每个平台 ZIP 至少包含一个可执行且通过自动校验的包才允许进入汇总；32 个目标的未完成项随包透明列示。
- 总 ZIP 内的构建清单记录 Git commit、构建时间、目标矩阵和包/平台 ZIP 的 SHA-256；总 ZIP 自身的 SHA-256 只能写入包外 `release-manifest.json` 和 `.sha256` 文件，避免文件校验值包含自身。公开 Release 后另生成 `release-receipt.json` 记录实际 tag、Release URL、总 ZIP SHA-256 和 `published` 状态；不回写已上传 ZIP。
- 构建、下载、缓存或打包步骤失败时自动重试，单个步骤最多 3 次；3 次仍失败则 Workflow 失败并保留明确错误原因。
- Windows 安装器方案：EXE 提供可独立执行的安装/启动入口，MSI 提供标准 Windows Installer 安装、升级和卸载入口；两者都必须支持静默安装参数、版本检测和卸载。Windows 7、10、11 的 x86/x86_64 组合均需纳入验证。
- 运行时依赖清单基线：Python 运行时（按支持的 Windows 架构提供）、`bleak==0.19.0`、`websockets==12.0`、网关 Python 包及其锁定依赖；BLE 驱动/运行库、Windows 服务运行组件和 VC++ 运行库按每个系统组合的实际构建结果补齐并写入清单。
- 依赖必须随包提供或进入离线缓存，安装阶段不得临时访问公网；每项依赖记录版本、架构、来源和 SHA-256。

## 7. 麒麟安装包实现建议

为避免等待不明确的官方资料，当前实现采用以下兼容基线，并在每个系统/架构组合上验证：

- 使用原生工具分别构建 DEB（`dpkg-deb`）和 RPM（`rpmbuild`），不把一种格式改名为另一种格式；服务器版和桌面版的五架构组合均须产出两种格式。
- 两种包安装相同的网关文件、配置目录、日志目录和 systemd 服务单元；安装、升级、卸载动作保持一致。
- 安装前探测 `/etc/os-release`、CPU 架构、`systemd`、`dpkg`/`rpm` 和可用磁盘空间；不匹配时返回可读错误并停止，不尝试联网修复。
- Python 运行时、wheel 依赖和网关资源随包或随离线缓存提供；目标机不依赖公网 APT/YUM 源。
- 系统库只声明经过矩阵验证的最小依赖；不能确认的发行版差异记录为兼容性结果，不在安装脚本中静默绕过。
- 包元数据至少包含名称、应用版本、架构、维护脚本、依赖声明、服务启停动作和卸载清理范围；每个组合的安装/升级/卸载日志进入发布清单。
- 麒麟服务器版和桌面版的官方安装包元数据差异作为矩阵测试结果记录，不把某一发行版或架构的行为推断为全部 V10 组合的行为。

## 7.1 发布状态和验证日志

状态枚举固定为：

- `source-only`：只有源码或未形成可执行包，不得进入正式 ZIP；
- `candidate`：已构建并通过自动检查，尚未完成本次发布门禁或真机验证；
- `published`：两平台最低成功门槛、安装包校验、日志和 GitHub Release 均成功，仅出现于公开后的 `release-receipt.json`；未完成目标仍须在 Release 说明中列明；
- `failed`：构建、测试、打包、校验或上传失败；
- `blocked`：目标矩阵、构建环境或验证输入缺失，必须补齐后重跑。

每个 Windows/麒麟矩阵目标都记录安装、升级、卸载、启动/停止、服务重启、离线运行和历史录制不触发 replay 的结果。发布时真机验证先记录为 `pending`，已有日志放入对应平台 ZIP 和总 ZIP。后续真机日志形成按版本、目标和时间命名的独立验证补充报告，记录 `validation.status`、`validation.checkedAt`、`validation.environment`、`validation.summary` 和 `validation.logFiles`，作为新增 Release 附件保存；不得修改原包、原清单或已有资产的 SHA-256。日志不得包含凭据或完整敏感原始数据。

`release-manifest.json` 只描述达到最低发布门槛的不可变候选产物：总状态、纳入平台 ZIP 的包及 ZIP 均为 `candidate`；`coverage.targetResults` 逐项记录未纳入包的 `source-only`、`failed` 或 `blocked`。未达到门槛的运行只保留 Actions 日志与测试报告，不伪造总 ZIP 或符合成功清单 Schema 的文件。

### 7.2 本次确定的构建与发布方案

**构建环境：**GitHub Actions 用固定版本的 Windows x64 与 Linux x64 runner 负责调度、检查和汇总，实际目标构建从受控离线缓存读取锁定工具链、Python/wheel、算法 SDK 与每个麒麟发行版/架构的 sysroot。Windows x86/x86_64 分别生成 PE、运行时和安装器；麒麟 x86_64/ARM64 优先使用 ABI 匹配的原生构建环境，其余架构使用经目标机验证的交叉工具链。每个目标必须记录 runner 镜像标识、编译器/CMake/Python/SDK 版本、sysroot 或 Windows SDK 摘要和构建命令；缺少已校验输入则将该目标标记 `blocked`。交叉构建只证明包结构与目标架构，不替代对应真机安装、串口和算法验证。GitHub checkout、缓存准备和 Artifact/Release 上传属于 CI 控制阶段；构建、测试、打包、校验阶段禁用公网依赖解析。

**Windows 7：**最低基线定为 Windows 7 SP1 的 x86 和 x86_64，浏览器兼容基线为 Edge 109。该通道使用独立、随包携带的 Python 3.8.10 对应架构运行时和离线依赖；Windows 10/11 继续使用仓库当前 Python 3.11 运行线。当前 `pyproject.toml` 要求 Python ≥3.11，因此 Windows 7 通道必须先完成代码及依赖的 3.8 兼容移植、KB2533623 等必需补丁、VC/UCRT 与浏览器页面检查，并通过 32/64 位干净机验证；在此之前只能 `blocked`，不能将现有包标为 Windows 7 候选或正式包。MSI 与自包含 EXE 采用 WiX Toolset v7 与 Burn，分别校验静默安装、升级、卸载和版本查询；固定补丁版本、工具摘要与许可证审核结果进入离线缓存台账。Windows 7 与 Python 3.8 均已结束官方维护，作为独立兼容通道记录风险和安全补丁基线。Windows 7 目标机不运行 GitHub Actions runner，现场日志通过受控方式回收。

**重试与幂等：**发布 job 仅授予 `contents: write`，PR job 保持 `contents: read`；同一应用版本串行发布。重跑先检查远端 tag：不存在时在全部本地校验通过后创建；存在且指向同一 commit 时继续；指向不同 commit 时立即失败，绝不移动或删除。Release 不存在则创建 draft，已存在 draft 则只补齐缺失资产并校验同名资产的大小和 SHA-256；已公开且资产完全一致时视为幂等成功，已公开但内容不一致时失败并要求新应用版本。上传失败保留 draft 供同 commit 重试，公开前校验唯一总 ZIP 与包外清单一致。公开成功后生成符合 [`schemas/release-receipt.schema.json`](../../schemas/release-receipt.schema.json) 的回执并保存为 Actions Artifact；不能把 draft 或仅有 tag 记为 `published`。

**版本关系：**正式包名、tag、GitHub Release 和清单统一使用台账 `[application].version`。`[platform_releases.windows]` 与 `[platform_releases.kylin]` 是旧流程元数据，本次 Workflow 不读取、不自动递增，也不作为安装器显示版本；后续迁移清理需单独变更台账。仅某个平台的安装器或依赖变化时仍按应用版本影响规则升级 `[application].version`，全部 32 个目标使用相同应用版本。重跑同一 commit 的 `GITHUB_RUN_ID/GITHUB_RUN_ATTEMPT` 只作为构建追踪号，不改变产品版本。

**待真机补录：**各麒麟版型的 ISO/SHA-256、包管理器与系统库 ABI，以及 Windows 7 的补丁、浏览器、COM/Service 和算法实际结果，由对应目标机日志补充。未完成对应组合的安装/升级/卸载、24 小时运行与离线无 replay 验证时，其 `physicalVerification` 保持 `pending`，Release 说明必须列出未验证组合，不能写成现场验收通过。

选型依据：[Python 官方 Windows 支持说明](https://docs.python.org/3/using/windows.html#supported-windows-versions)、[Python 3.8.10 发布页](https://www.python.org/downloads/release/python-3810/)、[GitHub self-hosted runner 支持范围](https://docs.github.com/en/actions/reference/runners/self-hosted-runners)、[WiX 系统要求](https://docs.firegiant.com/wix/#system-requirements)、[GitHub Release API](https://docs.github.com/en/rest/releases/releases)、[Actions token 权限](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#defining-access-for-the-github_token-scopes)。

## 8. 当前实现范围

本次实现不拆分子 PR，必须在当前 Workflow 需求中一次性完成以下能力：

- PR 业务代码变更的应用版本门禁；
- Windows 7/10/11、x86/x86_64 的 EXE 与 MSI 产物；
- 银河麒麟服务器版/桌面版、五类架构的 DEB 与 RPM 产物；
- 离线优先的官方版本信息、依赖和安装源缓存；
- PR 候选构建、合入 `master` 后的发布、GitHub Release、tag、30 天 Artifact 保留和统一发布清单；
- 失败原因、来源 URL、版本、架构、格式和 SHA-256 的可审计记录。
