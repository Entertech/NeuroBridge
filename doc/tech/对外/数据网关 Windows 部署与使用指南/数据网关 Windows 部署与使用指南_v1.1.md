# 数据网关 Windows 部署与使用指南 v1.1

版本：v1.1

状态：已发布

创建日期：2026-10-10

## 1. 用途与边界

本指南说明如何在 Windows 主机上安装、启动、确认运行状态、使用本机控制台页面、查看运行日志、排查常见问题和卸载数据网关。

本文只描述 B 端可观察到的安装位置、服务状态、本机页面地址、运行日志和操作步骤，不包含设备接入、数据处理、算法或其他网关内部实现细节。网关对 B 端的数据接口规则以随交付包发布的北向网络协议文档为准，本文不重复其契约内容。

本版本随本次发布 PR 更新，PR 合入后由 CI 生成正式 PDF。安装包结构和模拟回归不等于目标机现场验收通过；现场仍须记录安装、采集、断线、重连和重启结果。

本文使用以下术语：

- **网关主机**：运行数据网关的这台 Windows 电脑。
- **本机控制台页面**：网关在 `127.0.0.1` 提供的网页控制台，只能在网关主机本机打开。
- **网关服务**：Windows 服务 `NeuroBridge`。


### 1.1 版本关系与迁移

v1.1 接替 v1.0 的部署操作说明，旧版独立保留。本次补充安装前提、安装失败取证与日志导出、升级和卸载说明；北向网络协议及报文版本不变。请使用本次交付清单对应的新版安装包和 v1.1 指南，不混用旧版流程。

交付 ZIP 中 PDF 使用英文副本文件名，避免部分解压工具把中文文件名解码成乱码。中文标题、内容和文档版本不变，原名称对照位于总包 `metadata/document-filenames.json`。

## 2. 前提与默认参数

### 2.1 环境前提

- Windows 10 或 Windows 11，x64 架构。**不支持 Windows 7 或任何 32 位系统**；交付包仅包含 Windows 10/11 x64 的 EXE/MSI，不包含对应空目录或占位包。
- 由交付方提供的数据网关 Windows 安装包。
- 安装、启停服务需要管理员权限。

### 2.2 默认安装位置

| 项目 | 默认位置 |
| --- | --- |
| 程序目录 | `C:\Program Files\NeuroBridge` |
| 配置文件 | `C:\ProgramData\NeuroBridge\gateway.toml` |
| 运行日志目录 | `C:\ProgramData\NeuroBridge\logs` |
| 录制数据目录 | `C:\ProgramData\NeuroBridge\recordings` |
| Windows 服务名 | `NeuroBridge` |

`C:\ProgramData\NeuroBridge` 由网关在首次启动时自动创建。重装和卸载都不会删除该目录，现场配置、日志和录制数据因此得以保留。

### 2.3 默认监听地址与端口

网关的全部网络服务只监听本机回环地址 `127.0.0.1`，不向本机以外的网络提供访问。

| 服务 | 默认地址 | 用途 |
| --- | --- | --- |
| 本机控制台页面 | `http://127.0.0.1:8080/` | 浏览器访问的网页控制台 |
| 数据接口 | `ws://127.0.0.1:8765/neurobridge/v1/ws` | B 端接入连接 |
| 日志下载 | `http://127.0.0.1:8766/downloads` | 从网关内导出运行日志 |

以上端口取自配置文件，仅作为默认示例。现场已确认其他值时，以实际部署配置为准。

## 3. 安装

1. 取得交付方提供的 Windows 安装包。
2. 优先通过随 Windows 版本 ZIP 附带的 `install-with-logs.ps1` 安装，以保留安装失败日志。以 PowerShell 打开该文件所在目录，执行以下命令，将占位文件名替换为实际文件名：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\install-with-logs.ps1 `
  -Installer .\neurobridge-<实际文件名>.msi
```

EXE 使用同一命令，仅将 `-Installer` 改为实际 EXE 路径。安装时会弹出管理员授权窗口。管理员身份直接双击也可安装，但排错时应使用以上日志入口。
3. 安装程序注册并启动 Windows 服务 `NeuroBridge`，并配置为开机自动启动。
4. 首次启动时，网关自动创建 `C:\ProgramData\NeuroBridge` 下的日志和录制目录；配置文件不存在时，从随包模板生成一份。
5. 将耳机通过 USB 连接到网关主机。网关会自动建立连接，不需要手工指定端口。

安装不会覆盖已存在的 `gateway.toml`，重复安装不会丢失现场配置和录制数据。

## 4. 配置

配置文件默认位于 `C:\ProgramData\NeuroBridge\gateway.toml`。需要修改时以管理员权限编辑，按 UTF-8 保存，然后重启网关服务使配置生效。

```toml
[server]
host = "127.0.0.1"
port = 8765
path = "/neurobridge/v1/ws"

[local_ui]
enabled = true
host = "127.0.0.1"
port = 8080

[download]
enabled = true
host = "127.0.0.1"
port = 8766
path = "/downloads"
```

必须保持以上三个 `host` 均为 `127.0.0.1`。网关只在本机回环地址提供服务并校验请求来源，改成其他地址或从其他主机访问都不会成功。

如果默认端口与网关主机上的其他程序冲突，可在双方确认后修改。修改后，第 6 节和第 7 节中的地址要同步改用新端口。

## 5. 启动、停止与开机自启

安装后网关服务默认开机自动启动，不需要先登录桌面。

以管理员身份打开 PowerShell，在网关主机上执行：

```powershell
# 启动
Start-Service NeuroBridge

# 停止
Stop-Service NeuroBridge

# 查看状态
Get-Service NeuroBridge
```

`Get-Service` 输出的 `Status` 为 `Running` 表示服务正在运行，`Stopped` 表示已停止。

更新配置或更换程序版本前，先停止服务，完成后再启动。不要同时运行两个网关实例。

## 6. 判断网关是否正在运行

按以下顺序检查。三层都通过，才表示网关已就绪。

### 6.1 检查服务状态

```powershell
Get-Service NeuroBridge
```

`Status` 为 `Running` 表示服务进程已经启动。服务处于 `Running` 只说明启动成功，还需要继续检查端口和页面。

### 6.2 检查端口监听

```powershell
Get-NetTCPConnection -State Listen |
  Where-Object { $_.LocalPort -in 8080, 8765, 8766 } |
  Select-Object LocalAddress, LocalPort, OwningProcess
```

预期看到这三个端口均以 `127.0.0.1` 为本地地址处于监听状态。端口没有出现时，说明网关尚未完成启动，应查看第 8 节的运行日志确认原因。

### 6.3 检查控制台页面

在**网关主机本机**的浏览器打开 `http://127.0.0.1:8080/`。页面能正常加载，即表示本机控制台已就绪。

### 6.4 确认正在采集数据

页面能打开，不等于耳机已经连接并在采集。打开采集页 `http://127.0.0.1:8080/capture/`，点击“开始”，核对页面持续出现按 600 ms 窗口到达的数据。也可以查看第 8 节运行日志中的连接和采集记录。

## 7. 控制台与页面地址

| 页面 | 地址 | 说明 |
| --- | --- | --- |
| 采集页 | `http://127.0.0.1:8080/capture/` | 查看耳机实时数据，开始或停止接收 |
| 综合页 | `http://127.0.0.1:8080/` | 网关本机控制台入口 |
| 日志下载 | `http://127.0.0.1:8766/downloads/logs/neurobridge-logs.zip` | 下载网关运行日志压缩包（下载服务启用且网关运行时可用） |

打开页面时注意：

- 必须在**运行网关的这台主机**上打开，并且必须使用 `127.0.0.1`。改用 `localhost`、`file://` 或其他主机的地址都会被拒绝。
- 不要直接双击 HTML 文件打开页面，页面必须由网关服务提供。
- 页面上的“停止”只停止当前页面的数据订阅，不会停止网关服务。

## 8. 运行日志

运行日志默认位于 `C:\ProgramData\NeuroBridge\logs`，当前文件为 `neurobridge.log`，按大小轮转并保留历史归档。

```powershell
# 查看最近 200 行
Get-Content C:\ProgramData\NeuroBridge\logs\neurobridge.log -Tail 200

# 持续跟随新日志（Ctrl+C 结束，不影响网关）
Get-Content C:\ProgramData\NeuroBridge\logs\neurobridge.log -Tail 200 -Wait
```

日志时间按 UTC 记录，北京时间为 UTC+8。日志包含网关版本、耳机连接状态、数据时间范围和错误原因，不记录凭据和完整原始数据。

### 8.1 安装失败日志与导出

安装入口在运行安装程序之前即创建 `%LOCALAPPDATA%\NeuroBridge\installer-logs`，记录操作系统、安装包摘要、错误原因和退出码。MSI 使用 `/L*V` 生成详细日志，EXE 使用 `/log` 生成引导日志。默认保留最近 20 个日志文件，可在安装入口通过 `-KeepLogFiles` 设置 2-200 个；退出码 `0` 为成功，`1641` 或 `3010` 表示需要重启，其他值表示失败。成功退出仍应核对第 6 节服务、端口、页面和采集。

网关安装失败或服务不存在时，在原交付脚本目录运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\install-with-logs.ps1 -Export `
  -OutputDirectory "$env:USERPROFILE\Desktop"
```

按终端打印的 `Log export:` 路径取得 ZIP，连同安装包文件名交给交付方。导出仅包含该入口保存的安装日志，不复制现场配置或录制数据；没有日志时会明确提示先通过入口重现安装。即使安装程序尚未创建程序目录，也能使用随交付包附带的脚本。

网关已安装时，运行日志可从第 7 节地址下载；网页不可用时可在管理员 PowerShell 使用随程序安装的导出脚本：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File "C:\Program Files\NeuroBridge\windows\export-logs.ps1" `
  -OutputDirectory "$env:USERPROFILE\Desktop"
```

安装入口日志和网关运行日志分别导出，排查安装失败请先提供安装日志。Windows 交付指南副本名为 `windows-deployment-guide_v1.1.pdf`。

## 9. 常见问题

| 现象 | 检查与处理 |
| --- | --- |
| 服务列表中没有 `NeuroBridge` | 安装未完成或已被卸载。用交付方提供的安装包重新安装。 |
| 服务为 `Stopped`，手动启动后立即停止 | 查看第 8 节日志中的启动错误。常见原因是配置文件语法错误或端口被其他程序占用。 |
| 服务为 `Running` 但端口未监听 | 启动预检未通过。查看日志确认是配置问题还是端口冲突，修复后重启服务。 |
| 页面打不开 | 确认服务正在运行、端口正在监听，并确认使用的是 `127.0.0.1`，而不是 `localhost` 或其他主机地址。 |
| 页面能打开但一直没有数据 | 确认耳机已通过 USB 连接到网关主机；在采集页点击“开始”后再观察。 |
| 提示端口被占用 | 用 `Get-NetTCPConnection -State Listen` 找到占用端口的进程；停止冲突程序，或与交付方确认后修改配置端口。 |
| 需要重新开始采集 | 先在采集页点击“停止”，再点击“开始”。页面上的停止不会影响网关服务。 |

## 10. 停止与卸载

停止网关服务：

```powershell
Stop-Service NeuroBridge
```

卸载网关：在“设置 → 应用 → 已安装的应用”中找到数据网关并卸载；也可以使用管理员 PowerShell 执行：

```powershell
msiexec /x "安装包路径"
```

卸载会自动停止服务并删除程序文件。`C:\ProgramData\NeuroBridge` 不属于安装包，卸载后会保留现场配置、日志和录制数据；需要彻底清除时手动删除该目录。

## 11. 现场交付记录

完成后由双方记录以下实际值。本文不填写现场地址、凭据或其他敏感信息。

| 项目 | 现场确认值 |
| --- | --- |
| 网关主机名与 Windows 版本 |  |
| 安装包版本 |  |
| 控制台页面端口 |  |
| 服务状态检查结果 |  |
| 端口监听检查结果 |  |
| 采集验证结果 |  |
| 安装执行人和时间 |  |
