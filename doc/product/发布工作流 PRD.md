# NeuroBridge 对外发布工作流 PRD

- **文档状态**：需求评审稿
- **适用范围**：Windows 软件、银河麒麟系统 V10 软件及 GitHub Actions 发布流程
- **版本依据**：`neurobridge/version_registry.toml`

## 1. 背景与目标

当前仓库已有单元测试、北向协议文档 PDF 和部分外部文档打包流程，但缺少统一的应用版本门禁和跨平台发布产物流程。本 PRD 目标是让合入 `master` 的变更可审计、可复现，并避免业务代码变化却沿用旧版本号。

目标：

1. 在 PR 合入 `master` 前，自动识别是否改变对应软件的业务代码或随包交付的对外文档，并执行版本号规则。
2. 形成 Windows 和银河麒麟系统 V10 两类软件的版本化发布包流程。
3. 记录每个软件产物的版本、来源、校验值、构建环境和发布状态。

## 2. 功能需求

### 2.1 应用版本门禁

- 版本唯一来源为 `neurobridge/version_registry.toml` 的 `[application].version`；平台覆盖矩阵唯一来源为 [`release/release_matrix.toml`](../../release/release_matrix.toml)。
- **是否产出发布包只由 `[application].version` 相对基线是否递增决定**，与改了哪些文件无关。变更文件列表只用于判定「是否必须升版本」：改了交付范围内的文件却没升版本时门禁**失败**，而不是安静跳过。因此「改了某个目录就等效于该变化」并不成立，必须落实为版本号递增。
- PR 门禁以 PR 目标分支（固定为 `master`）的提交为基线比较版本；打包运行以**最后一个已发布 tag**（`v<应用版本>`）为基线，仓库尚无 tag 时退回被处理提交的父提交。
- 业务代码范围由配置维护（`tools/release_pipeline.py` 的 `PRODUCT_PATHS`），至少覆盖 `neurobridge/`、平台部署目录、运行依赖和打包配置，并包含**随包交付的对外文档目录 `doc/tech/对外/`**——改了交付给用户的文档必须升版本，否则该文档不会随包发出。只改其他文档、测试或网页资源不强制升应用版本。
- 应用版本使用三段数字格式 `MAJOR.MINOR.PATCH`。业务代码变化时，PR 版本不得低于 `master`：修复递增 `PATCH`，向后兼容功能递增 `MINOR` 并将 `PATCH` 归零，破坏兼容性的变化递增 `MAJOR` 并将 `MINOR`、`PATCH` 归零。未按影响级别递增、跳过必要的版本证据或发生版本回退时失败。
- 未改业务代码时版本可不变，但不得低于 `master`。
- 每次版本递增必须在变更记录中写明旧版本、新版本、影响级别、兼容性边界和依据。Actions summary 必须包含基线版本、PR 版本、变更文件和判定理由。

### 2.2 Windows 发布包

- 按发布配置中的版本矩阵为每个 Windows 目标版本生成独立构建结果；正式下载入口只提供汇总 ZIP。
- Windows 支持矩阵固定为 Windows 10、Windows 11，仅覆盖 x86_64；每个系统版本与架构组合均必须尝试建包并记录成功、失败或阻断原因。具体配置见 `release/release_matrix.toml`。
- Windows 安装包格式至少拆分为 **EXE** 和 **MSI** 两类；两类包分别构建、校验和发布，不得仅通过重命名复用同一文件。
- 每个包包含软件文件、依赖锁定信息、启动/卸载说明、版本清单和 SHA-256 校验值。
- 尚未完成可执行包验证时只能标记为 `source-only` 或 `candidate`，不得伪称正式安装包。
- 包名、清单和 Artifact 名称必须含平台、架构和应用版本。

### 2.3 银河麒麟系统 V10 发布包

- 银河麒麟当前交付一个 **引导包**（DEB），目标为银河麒麟 V10 的七个架构 Profile（见 §2.7）。引导包携带网关源码和离线构建输入，在安装该包的麒麟机器上构建运行时并安装服务，不再按服务器版/桌面版、五种架构和 DEB/RPM 预先构建完整包。具体配置见 `release/release_matrix.toml` 的 `package_kind = "bootstrap"`。
- 引导包的安装、失败回滚和重装规则随包内脚本交付；Workflow 不根据目标机在线探测改写矩阵。
- Python 运行时、wheel、CMake 和 Eigen 的版本与校验值随包内离线输入固定，安装阶段不再下载。
- 包内包含安装说明、版本清单和 SHA-256 校验值。
- 未完成目标机器现场验证时只能标记为 `candidate`。
- 当前引导包只支持 Intel/AMD 的 `x86_64`，不支持 32 位 x86、ARM/aarch64、龙芯/LoongArch/MIPS、申威等其他架构；银河麒麟 V10 不意味着多架构通用，不能将所有 64 位系统视为兼容。
- 未连接耳机时须允许完成安装并启动服务等待设备；后续接入通过持久化 USB 串口权限规则授权，无需重装。权限设置失败仍须报错并回滚旧程序、配置、服务及原权限规则；卸载仅移除本包规则。
- 安装失败诊断包须包括设备是否存在、串口节点权限与服务账户组信息，不读取设备数据或导出现场配置正文。

### 2.4 当前流程不包含的历史交付物

- 旧版 Ubuntu 网关使用的 SSH 运维操作指南不属于当前 Windows 或银河麒麟 V10 软件包。
- 旧版 Ubuntu 网关使用的有线网络配置指南不属于当前 Windows 或银河麒麟 V10 软件包。
- 上述历史文档及旧 Ubuntu 部署物不得被当前 Workflow 自动标记为 Windows 或麒麟 V10 的交付内容；如需维护，另建独立的 Ubuntu 维护流程。

### 2.5 发布清单

达到最低发布门槛的构建生成符合 [`schemas/release-manifest.schema.json`](../../schemas/release-manifest.schema.json) 的包外 `release-manifest.json`，包含应用版本、Git commit、构建时间、触发方式、全部 Windows EXE/MSI 目标与麒麟引导包的版本/架构/格式/状态，以及合格包的文件名/SHA-256、平台 ZIP、总 ZIP、官网来源和验证日志。它在公开 Release 前固定为 `candidate`；公开成功后由独立发布回执记录 `published`，不回写已封包文件。北向协议版本仍可作为软件兼容性元数据记录，但不触发本流程的文档打包。

### 2.6 总包根目录 PDF 目录说明

- 总 ZIP 根目录必须包含 `bundle-directory-guide.pdf`，中文标题为“NeuroBridge 交付包目录说明”，面向部署人员；使用 ASCII 文件名以减少麒麟解压后的名称乱码，不再交付 `PRD.md` 或以 `README.txt` 代替。
- 说明书介绍各层系统/版本/安装文件分组 ZIP、解压顺序、实际安装包、部署指南与日志工具位置，以及 `docs/external/`、`metadata/` 和校验清单的用途；注明应用版本、源码提交与交付状态。详细操作转到随包部署指南，不写产品需求评审内容或架构开发成本。
- 系统 ZIP、版本 ZIP、架构 ZIP、安装包及部署指南列表必须由本次交付清单生成，不能展示不存在或被阻断的目录/包。无平台产物时不得生成该平台的归档路径。
- 根目录 PDF 由本次交付清单生成，纳入 `bundle-manifest.json.documents` 和完整性门禁；缺失或未生成 PDF 时阻止交付。保持 PDF 英文名称与中文名称对照。
- 中文字体、表格和长文件名必须完整可读；复用 PDF 渲染工具链，CI 渲染全部页面供检查。PDF 不提交 Git。
- 本轮修改生成逻辑与要求、制作独立排版预览；不触发安装包、正式交付 PDF 或总 ZIP 构建。后续交付构建才将对应真实清单的目录说明 PDF 放入根目录。

### 2.7 系统与 CPU 架构支持表

引导包为一个 `Architecture: all` DEB；`all` 表示共用安装入口，运行时仍必须匹配实际架构。系统固定为银河麒麟 V10，七个源码适配 Profile 如下。既有 x86_64 联调事实不自动成为新版引导安装或其他架构的验收结果。

| 系统 / CPU | 位数与别名 | 依赖选择 | 当前验证状态 |
| --- | --- | --- | --- |
| 麒麟 V10 x86_64 | 64；amd64 | 锁定预编译 Python / CMake | 既有 N100/N150 联调基线；新版安装需复验 |
| 麒麟 V10 aarch64 | 64；arm64 | 本机编译锁定 Python / CMake 源码 | 源码入口与模拟回归；待实际目标机编译和验收 |
| 麒麟 V10 loongarch64 | 64；loong64 | 同上 | 同上；不等同于龙芯 MIPS |
| 麒麟 V10 mips64el | 64；mips64 且小端 ELF | 同上 | 同上；大端及 32 位 MIPS 不接收 |
| 麒麟 V10 sw64 | 64；sw_64 | 同上 | 同上；供应商工具链可能需要独立补丁 |
| 麒麟 V10 x86 | 32；i386/i486/i586/i686 | 同上 | 同上；需验证 32 位资源上限 |
| 麒麟 V10 armhf | 32；armv7l/armv8l，小端硬浮点 ABI | 同上 | 同上；不接收软浮点工具链 |
| Windows 10/11 x86_64 | 64；AMD64 | 既有 EXE/MSI | 保持原发布范围，当前版本仍需目标机验证 |
| Windows 7、Windows 32 位、Windows ARM；其他 Linux 或 CPU | — | 无 | 不支持，拒绝安装或不构建 |

全体麒麟 Profile 共用 Eigen 3.3.7、锁定 SDK/NumCpp 源码与无架构依赖的 pyserial/websockets wheel。当前串口链路不安装 BLE/dbus 原生依赖。配置仍为 serial + local_browser、回环地址、600 ms 批次与禁止耳机录播；不改北向报文。未知发行版/版本/CPU、混合位数、大端、外来编译器、缺输入或摘要错误必须停止，不能回退到 x86_64 文件。

### 2.8 多架构引导与验收要求

1. **选择与输入**：安装前记录 `/etc/os-release`、内核 CPU、用户空间位数、ELF 字节序/位数，按 `config/kylin-bootstrap-inputs.toml` 选依赖；所有输入固定版本/URL/SHA-256。准备阶段可显式下载；正式打包和目标机安装只读取已校验的离线输入。缓存缺失则失败，不在安装器内调用包管理器或下载源码。
2. **本机构建**：非 x86_64 从 CPython 和 CMake 锁定源码编译，再构建 C++17 算法。需要系统 C/C++ 编译器、make、开发头文件及库；32 位内存限制、供应商编译器、老 glibc 和 SDK 数值差异须独立验证。不能承诺所有供应商 V10 镜像自动编译成功；失败报告中必须保留阶段、命令输出和退出码。
3. **运行时隔离**：生成文件名与 manifest 使用真实架构；复用归档必须同时匹配 Kylin V10 与 CPU，再验证 SHA-256、Python 位数/字节序及必要模块。跨 CPU 运行时在账号和服务修改前拒绝。保留已有部署的原子替换与失败回滚，未接耳机仍允许安装并等待接入。
4. **交付规模**：发布矩阵仍是一个麒麟引导 DEB 与四个 Windows 包，共五个目标；新增源文件和 wheel 增加单引导包大小，而不新增每架构预编译包。可生成手工 RPM noarch，但不纳入当前正式矩阵；RPM 系统库依赖仍须供应商确认。
5. **真机准入**：逐架构完成编译、算法输入/结果比对、安装/升级/失败回滚/卸载、冷启动 E1、停止 E0、USB 拔插、服务/整机重启、浏览器恢复及长稳。存在历史录制的离线耳机仍不得录播。缺真机证据保持 candidate，不标记现场通过；不同架构结果不能相互替代。

本轮只修改源码和文档、执行本地自动化检查，不打包、不推送，不触发依赖准备或发布 Workflow。

### 2.9 安装与运行诊断的版本和环境

Windows 与麒麟的安装/运行导出均须携带独立诊断上下文：Windows 为 `diagnostic-context.json`（安装导出添加唯一时间后缀），麒麟为 `diagnostic-context.txt`。安装未成功、服务未创建或 Python 不可运行时仍须生成；无法读取的字段明确写 `unknown`，不能填入正在导出工具所在仓库的版本作为已安装版本。

| 信息 | 字段与来源 |
| --- | --- |
| 诊断时间及类型 | `generatedAtUtc`、`diagnosticScope=installation/runtime` |
| 磁盘上的软件身份 | `applicationVersion` 来自部署目录版本台账，`sourceCommit` 来自随部署保存的 `build-info.txt`；源码部署可读取自身 Git HEAD |
| 本次安装包身份 | `packageApplicationVersion`、`packageSourceCommit` 来自引导包或 Windows 交付目录的 `build-info.txt`；运行导出没有安装包时为 `unknown` |
| 系统版本 | Windows 名称、版本、OS 构建号；麒麟发行版名称/ID、版本、发行构建标识与内核版本 |
| 环境架构 | 系统 CPU 架构、系统位数；Windows 额外记录导出进程位数，避免把 32 位 PowerShell 误判为 32 位系统 |
| 执行环境 | Windows PowerShell 版本；麒麟 Bash/glibc 版本；两者的部署 Python 版本和 Python 位数 |

版本字段描述磁盘上部署文件，不宣称正在运行的旧进程已加载新版本；服务状态另行导出。Windows 安装摘要保存安装开始时的上下文，导出时另采集当前上下文，升级失败可据时间及两个版本字段区分旧部署与新安装包。麒麟安装日志保留本次包版本/提交，导出同时读取当前部署和引导包身份。OS 查询失败应保留原因，不能因此丢弃已有安装错误日志。只采集这些必要字段，不导出全部环境变量、配置正文、令牌、密钥或人体录制数据。

## 3. 工作流与门禁

打包链路与 PR 解耦，由独立的 `Release` workflow 承担；PR 只做校验，不产出任何包。

1. `pull_request -> master`：只运行版本门禁与测试（含对外文档发布状态门禁），**不构建候选包、平台 ZIP 或总 ZIP**，也不创建 GitHub Release 或 tag。
2. 打包有两个入口，二者之外不触发：(a) PR 合入 `master` 后，`Test` workflow 成功完成时自动触发发布流程——发布仍以测试通过为前提；(b) 在 Actions 上手动触发，对**所选分支的最新提交**打包。手动触发属于非正式构建，只产出候选包，不创建 tag 或 GitHub Release。
3. 打包触发后，若应用版本未递增，记录跳过原因而不重发同版本；若版本递增，尝试全部矩阵目标并记录逐项结果；Windows 和麒麟各至少一个可执行且通过自动校验的包时，生成两个平台 ZIP 和唯一总 ZIP。
4. 两个平台达到最小成功条件，且平台 ZIP、总 ZIP、清单和日志构建并校验通过后，创建指向本次 `master` commit 的注释 tag `v<applicationVersion>`；随后创建 draft GitHub Release，上传唯一总 ZIP、包外清单和验证日志并复验，最后公开 Release。未完成的矩阵组合在 Release 说明中逐项列明；tag 创建后的网络失败按 §7.2 原地恢复，不移动 tag 或覆盖已有正式 Release。手动触发的非正式构建不进入本步。
5. 正式 Artifact 保留 **30 天**，下载权限为**所有能够访问仓库 Actions 的用户**，不做额外权限限制。
6. 当前版本暂不进行代码签名、安装包签名或公证；清单中的 SHA-256 仅用于完整性校验。后续启用签名时必须新增版本规则和受保护凭据流程。
7. 任一平台无合格包、成功包的校验值不一致、成功包缺日志或发布资产上传失败时，工作流失败并说明原因。其他矩阵组合失败或来源数据缺失时，记录该组合为 `failed`/`blocked`，不隐藏缺口。

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
- 改了交付范围内的文件（含 `doc/tech/对外/` 的对外文档）但未升应用版本的 PR 必须失败；版本未递增时不产出任何发布包，运行页写明原因。
- PR 只做校验，不产生任何候选包或 ZIP；打包由 `Release` workflow 承担，手动对分支触发时生成该分支最新提交的 Windows EXE/MSI 和麒麟引导包候选清单，状态与验证事实一致。
- 应用版本递增的 `master` 构建在最低门槛通过时生成带 SHA-256 的包外 `release-manifest.json`、唯一总 ZIP 和 GitHub Release；版本未递增时跳过同版本发布；Actions Artifact 保留 30 天，GitHub Release 作为长期下载入口。
- Artifact 可在无公网环境按清单校验。
- 现有单元测试和协议兼容性检查全部通过。

## 6. 已确认约束

- 麒麟交付物固定为一个银河麒麟 V10 通用引导包（DEB all）。它在安装机器上构建运行时，不按服务器版/桌面版和五种架构预先生成 DEB/RPM；矩阵写入仓库配置，Workflow 不联网发现版本。
- Windows 适配矩阵固定包含 Windows 10、Windows 11，仅覆盖 64 位（x86_64）；每个组合单独验证并记录兼容性结果。
- Windows 每个适配组合都尝试生成 EXE 和 MSI；麒麟尝试生成一个引导包，并记录安装与回滚结果。全部 5 个包目标均属于支持矩阵；某目标未形成合格包时必须在清单和 Release 说明中写明状态、原因与后续验证项。
- “各个版本”专指操作系统版本适配性，不要求回溯构建历史应用版本。
- 构建、测试、打包和校验默认离线；官网版本信息、依赖和安装源先缓存并校验，正式构建只读取缓存。
- PR 合入 `master` 且 `Test` workflow 通过后触发发布；也可在 Actions 手动触发，对所选分支的最新提交打包（非正式构建，不创建 tag 或 Release）。包与总 ZIP 校验通过后创建 tag，随后创建 draft GitHub Release，上传和复验成功后公开。Actions Artifact 保留 30 天，GitHub Release 长期保留；当前不做签名或公证。
- Windows 和麒麟支持矩阵以本 PRD 与仓库配置为准；官网新增版本不会自动进入 CI，需人工审阅后更新配置。
- 先分别生成 `windows-v<version>-<date>.zip` 和 `kylin-v<version>-<date>.zip`，再将两个平台 ZIP、构建清单和验证日志压缩为唯一交付物 `neurobridge-v<version>-<date>.zip`。麒麟平台 ZIP 只含一个引导包。每个平台 ZIP 至少包含一个可执行且通过自动校验的包才允许进入汇总；5 个目标的未完成项随包透明列示。
- 总 ZIP 内的构建清单记录 Git commit、构建时间、目标矩阵和包/平台 ZIP 的 SHA-256；总 ZIP 自身的 SHA-256 只能写入包外 `release-manifest.json` 和 `.sha256` 文件，避免文件校验值包含自身。公开 Release 后另生成 `release-receipt.json` 记录实际 tag、Release URL、总 ZIP SHA-256 和 `published` 状态；不回写已上传 ZIP。
- 构建、下载、缓存或打包步骤失败时自动重试，单个步骤最多 3 次；3 次仍失败则 Workflow 失败并保留明确错误原因。
- Windows 安装器方案：EXE 提供可独立执行的安装/启动入口，MSI 提供标准 Windows Installer 安装、升级和卸载入口；两者都必须支持静默安装参数、版本检测和卸载。Windows 10、11 的 x86_64 组合均需纳入验证；Windows 7 和 32 位系统不构建、不归档、不交付。
- 运行时依赖清单基线：Python 运行时（按支持的 Windows 架构提供）、`bleak==0.19.0`、`websockets==12.0`、网关 Python 包及其锁定依赖；BLE 驱动/运行库、Windows 服务运行组件和 VC++ 运行库按每个系统组合的实际构建结果补齐并写入清单。
- 依赖必须随包提供或进入离线缓存，安装阶段不得临时访问公网；每项依赖记录版本、架构、来源和 SHA-256。

## 7. 麒麟安装包实现建议

当前麒麟交付物是一个引导包，在安装它的银河麒麟 V10 x86_64 机器上完成构建：

- 使用 `dpkg-deb` 构建一个 DEB。包内携带网关源码、Python 3.11、wheel、CMake 和 Eigen 3.3.7；安装时在本机编译运行时和算法桥，不再为服务器版/桌面版和五种架构分别预构建。
- 安装写入配置目录、日志目录、录制目录和 systemd 服务单元。安装失败时撤回本次写入的 `/opt/neurobridge`；已有运行时的机器重装不重复构建。
- 安装时需要本机的编译器和 `systemd`；不匹配时返回可读错误并停止。包内已经带齐构建输入，不在安装阶段访问公网。
- 包元数据包含名称、应用版本、架构、安装脚本和依赖声明；安装与回滚结果进入发布清单。
- 引导包只验证银河麒麟 V10 x86_64。不把这一台机器的结果推断成其他发行版或架构。

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

**构建环境：**GitHub Actions 使用 Windows x64 runner 准备锁定的 Windows 10/11 x64 运行时和 WiX 安装包；Ubuntu runner 构建携带离线输入的麒麟 V10 通用引导 DEB（all）。麒麟安装时在目标机器构建运行时，不再获取预编译版型/架构输入。每个目标记录 runner、编译器/CMake/Python 与依赖版本和构建命令；缺少已校验输入时明确记录阻断。构建与模拟校验不替代目标机安装、串口、算法及重启验证。

**Windows 支持范围：**Windows 10/11 使用锁定的 Python 3.11 x64 运行时与 WiX Toolset v7 / Burn 构建 MSI/EXE。仅四个受支持目标参与构建；Windows 7 和 32 位目标不运行、不生成空目录或占位归档，清单拒绝过期的矩阵外输入。静默安装、升级、卸载和版本查询仍须验证。安装入口附带 MSI/Burn 日志及安装失败导出能力，尚未创建网关程序或服务时也能取证。

**重试与幂等：**发布 job 仅授予 `contents: write`，PR job 保持 `contents: read`；同一应用版本串行发布。重跑先检查远端 tag：不存在时在全部本地校验通过后创建；存在且指向同一 commit 时继续；指向不同 commit 时立即失败，绝不移动或删除。Release 不存在则创建 draft，已存在 draft 则只补齐缺失资产并校验同名资产的大小和 SHA-256；已公开且资产完全一致时视为幂等成功，已公开但内容不一致时失败并要求新应用版本。上传失败保留 draft 供同 commit 重试，公开前校验唯一总 ZIP 与包外清单一致。公开成功后生成符合 [`schemas/release-receipt.schema.json`](../../schemas/release-receipt.schema.json) 的回执并保存为 Actions Artifact；不能把 draft 或仅有 tag 记为 `published`。

**版本关系：**正式包名、tag、GitHub Release 和清单统一使用台账 `[application].version`。`[platform_releases.windows]` 与 `[platform_releases.kylin]` 是旧流程元数据，本次 Workflow 不读取、不自动递增，也不作为安装器显示版本；后续迁移清理需单独变更台账。仅某个平台的安装器或依赖变化时仍按应用版本影响规则升级 `[application].version`，全部 5 个目标使用相同应用版本。重跑同一 commit 的 `GITHUB_RUN_ID/GITHUB_RUN_ATTEMPT` 只作为构建追踪号，不改变产品版本。

**待真机补录：**银河麒麟 V10 x86_64 的安装/升级/卸载、真实采集、耳机离线与拔插、服务/整机重启、浏览器恢复，以及 Windows 10/11 x64 的安装和采集结果，由对应目标机日志补充。未完成验收时 `physicalVerification` 保持 `pending`，不得把 CI 或模拟回归写成现场验收通过。

选型依据：[Python 官方 Windows 支持说明](https://docs.python.org/3/using/windows.html#supported-windows-versions)、[Python 3.8.10 发布页](https://www.python.org/downloads/release/python-3810/)、[GitHub self-hosted runner 支持范围](https://docs.github.com/en/actions/reference/runners/self-hosted-runners)、[WiX 系统要求](https://docs.firegiant.com/wix/#system-requirements)、[GitHub Release API](https://docs.github.com/en/rest/releases/releases)、[Actions token 权限](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#defining-access-for-the-github_token-scopes)。

## 8. 当前实现范围

本次实现不拆分子 PR，必须在当前 Workflow 需求中一次性完成以下能力：

- PR 业务代码变更的应用版本门禁；
- Windows 10/11、x86_64 的 EXE 与 MSI 产物；
- 银河麒麟 V10 通用引导包（DEB all）；安装时在目标机器上构建运行时；
- 离线优先的官方版本信息、依赖和安装源缓存；
- 手动触发的分支候选构建、合入 `master` 且 `Test` 通过后的发布、GitHub Release、tag、30 天 Artifact 保留和统一发布清单；
- 失败原因、来源 URL、版本、架构、格式和 SHA-256 的可审计记录。

交付 PDF 使用 ASCII 副本文件名避免解压编码歧义，并保留台账原名对照。麒麟安装日志持久化于 `/var/log/neurobridge-bootstrap`；Windows 交付附带安装与失败日志导出脚本。安装失败须提供退出码、源码提交/安装包摘要与阶段，不依赖已运行的网页才能导出。
