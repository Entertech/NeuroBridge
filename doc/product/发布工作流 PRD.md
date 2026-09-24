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

- 按发布配置中的版本矩阵为每个 Windows 目标版本生成独立 Artifact。
- Windows 支持矩阵固定为 Windows 7、Windows 10、Windows 11，分别覆盖 x86 和 x86_64；每个系统版本与架构组合均必须建包并记录验证结果。具体配置见 `release/release_matrix.toml`。
- Windows 安装包格式至少拆分为 **EXE** 和 **MSI** 两类；两类包分别构建、校验和发布，不得仅通过重命名复用同一文件。
- 每个包包含软件文件、依赖锁定信息、启动/卸载说明、版本清单和 SHA-256 校验值。
- 尚未完成可执行包验证时只能标记为 `source-only` 或 `candidate`，不得伪称正式安装包。
- 包名、清单和 Artifact 名称必须含平台、架构和应用版本。

### 2.3 银河麒麟系统 V10 发布包

- 银河麒麟 V10 支持服务器版和桌面版，以及 x86_64、ARM64、LoongArch64、MIPS64el、SW64 五类架构；每个发行版与架构组合均必须建包并记录验证结果。具体配置见 `release/release_matrix.toml`。
- 麒麟每个组合同时交付 **DEB** 和 **RPM** 两类包；两类包分别生成对应的安装、升级和卸载元数据。Workflow 不根据目标机在线探测缩减矩阵。
- 版本和下载来源以麒麟官网或双方确认的官方数据为准，记录来源 URL、抓取时间和校验值。
- 每个包包含安装/升级、依赖检查、服务启动、回滚说明、版本清单和 SHA-256 校验值。
- 未完成目标架构现场验证时只能标记为 `candidate`。

### 2.4 当前流程不包含的历史交付物

- 旧版 Ubuntu 网关使用的 SSH 运维操作指南不属于当前 Windows 或银河麒麟 V10 软件包。
- 旧版 Ubuntu 网关使用的有线网络配置指南不属于当前 Windows 或银河麒麟 V10 软件包。
- 上述历史文档及旧 Ubuntu 部署物不得被当前 Workflow 自动标记为 Windows 或麒麟 V10 的交付内容；如需维护，另建独立的 Ubuntu 维护流程。

### 2.5 发布清单

每次构建生成符合 [`schemas/release-manifest.schema.json`](../../schemas/release-manifest.schema.json) 的 `release-manifest.json`，包含应用版本、Git commit、构建时间、触发方式、Windows EXE/MSI 与麒麟 DEB/RPM 目标的版本/架构/格式/状态/文件名/SHA-256、平台 ZIP、总 ZIP、官网来源、验证日志和 `candidate`/`published` 状态。北向协议版本仍可作为软件兼容性元数据记录，但不触发本流程的文档打包。

## 3. 工作流与门禁

1. `pull_request -> master`：运行版本门禁、测试和 Windows/麒麟候选包构建，不创建 GitHub Release 或 tag。
2. PR 合入 `master` 后由 `push -> master` 自动触发发布流程，构建并验证全部矩阵目标，先生成两个平台 ZIP，再生成唯一总 ZIP。
3. 全部包、平台 ZIP、总 ZIP、清单和日志上传成功且校验通过后，创建注释 tag `v<applicationVersion>`；随后以该 tag 创建 GitHub Release，并上传唯一总 ZIP、清单和验证日志。若任一前置步骤失败，不创建 tag 或 GitHub Release。
4. 正式 Artifact 保留 **30 天**，下载权限为**所有能够访问仓库 Actions 的用户**，不做额外权限限制。
5. 当前版本暂不进行代码签名、安装包签名或公证；清单中的 SHA-256 仅用于完整性校验。后续启用签名时必须新增版本规则和受保护凭据流程。
6. 平台构建失败、矩阵目标缺失、来源数据缺失、校验值不一致或日志不完整时，工作流失败并说明原因。

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
- `master` 构建生成带 SHA-256 的 `release-manifest.json`、唯一总 ZIP 和 GitHub Release；Actions Artifact 保留 30 天，GitHub Release 作为长期下载入口。
- Artifact 可在无公网环境按清单校验。
- 现有单元测试和协议兼容性检查全部通过。

## 6. 已确认约束

- 麒麟 V10 当前固定支持服务器版、桌面版，以及 x86_64、ARM64、LoongArch64、MIPS64el、SW64 五类架构；每个组合同时生成 DEB 和 RPM，矩阵写入仓库配置，Workflow 不联网发现版本。
- Windows 适配矩阵固定包含 Windows 7、Windows 10、Windows 11，并分别覆盖 32 位（x86）和 64 位（x86_64）；每个组合单独验证并记录兼容性结果。
- Windows 每个适配组合都生成 EXE 和 MSI；麒麟 V10 每个适配组合都生成 DEB 和 RPM，并分别验证安装、升级和卸载行为。全部 32 个包目标均属于发布矩阵；任一预期目标缺失，正式发布失败。
- “各个版本”专指操作系统版本适配性，不要求回溯构建历史应用版本。
- 构建、测试、打包和校验默认离线；官网版本信息、依赖和安装源先缓存并校验，正式构建只读取缓存。
- PR 合入 `master` 后触发发布；包校验和总 ZIP/清单/日志上传成功后才创建 tag，随后创建 GitHub Release。Actions Artifact 保留 30 天，GitHub Release 长期保留；当前不做签名或公证。
- Windows 和麒麟支持矩阵以本 PRD 与仓库配置为准；官网新增版本不会自动进入 CI，需人工审阅后更新配置。
- 先分别生成 `windows-v<version>-<date>.zip` 和 `kylin-v<version>-<date>.zip`，再将两个平台 ZIP、总清单、总校验文件和验证日志压缩为唯一交付物 `neurobridge-v<version>-<date>.zip`。每个平台 ZIP 至少包含一个包才允许进入汇总；正式矩阵要求全部 32 个包目标齐全。
- 平台 ZIP 和总 ZIP 文件名必须包含应用版本号和构建日期；包内清单还必须记录 Git commit、构建时间、目标矩阵、状态、GitHub Release/tag 信息和各文件 SHA-256。
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
- `published`：全部矩阵目标、安装包校验、日志和 GitHub Release 均成功；
- `failed`：构建、测试、打包、校验或上传失败；
- `blocked`：目标矩阵、构建环境或验证输入缺失，必须补齐后重跑。

每个 Windows/麒麟矩阵目标都记录安装、升级、卸载、启动/停止、服务重启、离线运行和历史录制不触发 replay 的结果。当前真机验证先记录为 `pending`，日志放入对应平台 ZIP 和总 ZIP；后续以真机日志补填 `validation.status`、`validation.checkedAt`、`validation.environment`、`validation.summary` 和 `validation.logFiles`，不修改已生成包的 SHA-256。

## 8. 当前实现范围

本次实现不拆分子 PR，必须在当前 Workflow 需求中一次性完成以下能力：

- PR 业务代码变更的应用版本门禁；
- Windows 7/10/11、x86/x86_64 的 EXE 与 MSI 产物；
- 银河麒麟服务器版/桌面版、五类架构的 DEB 与 RPM 产物；
- 离线优先的官方版本信息、依赖和安装源缓存；
- PR 候选构建、合入 `master` 后的发布、GitHub Release、tag、30 天 Artifact 保留和统一发布清单；
- 失败原因、来源 URL、版本、架构、格式和 SHA-256 的可审计记录。
