# Ubuntu 24.04 头环网关

本目录负责 Ubuntu 24.04 x86_64 蓝牙头环与有线 B 端部署，包括环境准备、安装、更新、SSH 运维、诊断、systemd 和日志轮转。入口始终从仓库根目录执行；不依赖 `linux/kylin/`。算法桥由本目录检查宿主，再调用 `linux/common/` 构建内核。

## 1. 安装前准备

准备好以下内容：

- Ubuntu **24.04 LTS** x86_64，使用 systemd 启动；安装账户具有 `sudo` 权限。
- 已审查的 NeuroBridge 源码版本（建议固定到已确认的 tag 或 commit），以及可连接头环的已验证蓝牙 5.x 适配器（仅实时采集需要）。可直接匿名获取：`git clone https://github.com/Entertech/NeuroBridge.git`。
- 可联网时先运行一次 `./linux/ubuntu/prepare-ubuntu24.04-environment.sh`，它会安装 `python3`、`rsync`、CMake、C++17 编译器、Eigen3、BlueZ、dnsmasq，并创建 `/opt/neurobridge/venv`、安装锁定的 Python 运行依赖。完成后即可断开互联网；安装器只验证这些前提，绝不调用 APT、PyPI 或其他下载服务。
- Ubuntu 固定使用 `wired_b_side`：准备经现场确认的网关/B 端地址、掩码、端口与专用网口。
- 若要使用录播，准备好录制数据目录及要回放的 `recordingId`；若要使用算法，先完成该 Ubuntu 主机上的真实数据 POC。算法默认启用，但 POC 前可暂时设为 `false`。
- 若要使用 SSH 运维，确认运维账户名、至少 6 位数字的密码、运维主机固定 IP/CIDR，以及网关用于运维的私有静态 IP。快速模式允许把明文密码保存在 Git 忽略且权限为 `600` 的现场 TXT 中，但不得写入现场文档、命令历史、日志或版本库。数字密码正式部署仅适用于专用网线直连和固定来源 IP 的封闭现场；短时局域网验证必须限制为受控网络和单机 `/32` 来源，禁止把 SSH 接入公网、无线网或不受控局域网。

在新主机上确认操作系统、CPU 架构、网卡及蓝牙设备：

```bash
cat /etc/os-release
uname -m
systemctl is-system-running
ip -br link
ip -br addr
bluetoothctl list
```

`uname -m` 必须输出 `x86_64`，`/etc/os-release` 中的 `ID` 必须是 `ubuntu`。`systemctl is-system-running` 输出 `running` 或 `degraded` 均表示 systemd 已运行；若为 `offline`，先修复主机启动环境。

## 2. 获取源码

目标机首次获取源码时，可使用公开 HTTPS Git 仓库；此操作不需要 GitHub 账号、密码或令牌：

```bash
git clone https://github.com/Entertech/NeuroBridge.git
cd NeuroBridge
```

之后的部署命令只读取当前目录中的源码。`third_party/` 已包含构建算法 bridge 所需的锁定 SDK 与 NumCpp 源码；不要在目标机上执行 SDK 的 `git clone` 或下载。

在仍可联网时，先运行一次环境准备脚本：

```bash
./linux/ubuntu/prepare-ubuntu24.04-environment.sh
```

脚本成功后可断开互联网；首次安装按第 4 节执行，后续使用 `./linux/ubuntu/update-ubuntu.sh` 完成更新和 bridge 重建。

## 3. Ubuntu 访问策略与有线网络

Ubuntu 首次安装使用 `config/gateway.ubuntu.toml.example`，固定 `profile="ubuntu_headband_wired"` 与 `access.mode="wired_b_side"`，关闭 `local_ui`；B 端通过批准的专用有线地址访问 WS 和采集控制台。本节描述这一 Ubuntu 直连部署。麒麟/Windows 的本机 Profile 才使用回环页面及固定 Origin，不应复制其模板到 Ubuntu。

安装及每次 `neurobridge.service` 启动时，辅助程序从 `/etc/neurobridge/gateway.toml` 读取 `[server].host` 与 `[network].subnet_cidr`，检查所选有线口的链路、现有地址和路由；缺少固定地址时只追加该地址，不清空已有地址，不改 DHCP、默认路由或 DNS，也不覆盖 Netplan 文件。不以公网 ping、DNS 或互联网可达性判断专用网络是否可用。

默认部署固定地址为 `192.168.88.10/24`。即使未插网线或没有 DHCP，也会将专用口启用并配置此地址；如果该口已有局域网地址，固定地址作为附加地址存在，保留原有地址、默认路由、DNS 和 IPv6。只为固定地址增加系统所需的直连子网路由，不增加默认路由。B 端仍须配置同网段、不同地址（例如 `192.168.88.20/24`），再使用 `ws://192.168.88.10:8765/neurobridge/v1/ws`。现场不同的地址、网段或端口以部署配置为准，且 `[download].host` 应与 `[server].host` 一致。局域网验证仅限受控现场，正式交付仍使用专用有线直连。

Windows B 端可将最新静态包完整解压，双击 `b-client-test/configure-wired-ip.bat`，允许管理员权限并按需选择有线网卡，追加默认 B 端地址；再双击 `index.html` 自动连接并查询状态。具体限制和日志位置见 [B 端文件夹说明](../../web/b-client-test/README.md)。该脚本在 B 端执行，不在 Ubuntu 网关执行。

`[network].interface = "auto"` 只在恰有一个物理以太网口时自动选中；已有局域网地址不再阻止追加固定地址。多个物理口必须显式指定；Wi-Fi、虚拟口、其他接口已占用目标地址、已有目标地址掩码不同，或固定子网与其他接口路由/已有经网关路由重叠时，拒绝改写并输出原因。请在本地控制台指定专用网口，例如：

```toml
[network]
mode = "static"
interface = "enp1s0"
subnet_cidr = "192.168.88.0/24"
```

保存后重新运行安装入口即可应用：

```bash
sudo ./linux/ubuntu/update-ubuntu.sh
ip -br addr show enp1s0
```

地址准备由 systemd 的 root 启动前置命令执行，网关业务进程仍以 `neurobridge` 普通账户运行。`neurobridge-wired-address.service` 随网关启动，默认每 5 秒检查一次，重连导致固定地址被移除时自动补回；启动后固定所选端口，不因后来出现其他网口而自动迁移。网关停止时辅助服务停止，已追加的地址保留到系统网络重配或重启，下次启动重新检查。因此该功能不依赖把静态地址写进 Netplan，也不等待 `network-online.target` 的 DHCP/公网判断。

已有现场配置默认启用上述追加行为；缺少网口配置时仅在一个物理以太网口上自动选择，缺少子网时使用默认专用子网。自定义地址请显式配置子网。可用 `[network].ensure_static_address = false` 禁用自动追加，或以 `address_check_interval_seconds` 调整维护间隔（`1..3600` 秒，默认 `5.0`）。手动运行 Python 网关不具备改系统地址的权限；部署入口是 systemd 服务。

地址准备与修复的决策、原有地址、链路状态、默认路由、操作耗时和失败原因记录在 journal：

```bash
sudo journalctl -u neurobridge.service -u neurobridge-wired-address.service --since '72 hours ago' --no-pager
```

前置失败会阻止本次业务启动并由 systemd 重试；运行中的辅助服务遇到暂时网卡消失时记录错误并继续检查，采集进程继续。现有诊断包也会包含此服务日志。回退时，在本地控制台将 `ensure_static_address` 设为 `false` 并重启网关；如确认该地址由本功能追加，可手动删除这一个地址，保留原有局域网地址和路由。

如果现场明确需要永久采用独占专线的 Netplan 配置，可继续手动使用旧配置器；这与默认的运行时附加地址模式不同，它仍拒绝覆盖活跃网络：

```bash
sudo /opt/neurobridge/venv/bin/neurobridge-network-config \
  --config /etc/neurobridge/gateway.toml --apply
```

手动 Netplan 配置器只改写带 NeuroBridge 标识的 `/etc/netplan/99-neurobridge-b-side.yaml`，保留其他文件；历史已存在的此文件也不会被运行时追加功能重写。若现场启用了防火墙，只允许专用网卡访问已确认的 WS/下载端口。

若已确认需要把已由配置器管理的 B 端专用网口从一个物理接口迁移到另一个接口，必须由现场运维人员在本地控制台显式执行 `--replace-managed-interface`。旧接口仍有 IPv4 地址或默认路由时，还必须同时确认 `--replace-active`；安装器不会自动迁移接口。

若已确认需要把一个当前正在使用的网口改作 B 端专线，必须先断开其原网络；只有无法断开且由现场运维人员明确确认时，才可在本地控制台使用 `--replace-active` 强制替换，安装器不会自动使用这个参数。

## 4. 安装网关

安装脚本会创建 `neurobridge` 服务账户，将源码同步至 `/opt/neurobridge`，在本地构建并安装算法 bridge，并安装与启用 systemd 单元。它不会联网安装系统包或下载 Python 依赖，只从本地源码更新网关包；缺少环境准备前提时会失败。首次使用安装入口，确认配置后显式启动：

```bash
sudo ./linux/ubuntu/install-ubuntu.sh
sudoedit /etc/neurobridge/gateway.toml
sudo systemctl start neurobridge
```

`install-ubuntu.sh` 创建并启用服务，但**不会首次自动启动**。后续 `update-ubuntu.sh` 则立即重启服务，B 端需要重新连接与订阅。关键位置如下：

| 位置 | 用途 |
| --- | --- |
| `/opt/neurobridge` | 被 systemd 实际运行的源码与 Python 虚拟环境。 |
| `/etc/neurobridge/gateway.toml` | 现场部署配置；root 可写，`neurobridge` 服务账户可读。 |
| `/var/lib/neurobridge/recordings` | 实时录制和录播数据。 |
| `/var/log/neurobridge/neurobridge.log` | 持久化运行日志；每天轮转，默认保留 14 份压缩归档。 |

## 4.1 启用 SSH 运维入口（可选）

SSH 只用于网关运维，**不**用于 B 端读取数据、控制采集或调用算法。`prepare-ubuntu24.04-environment.sh` 会安装 `openssh-server`，但会停用 Ubuntu 可能自动开启的 `ssh.socket` 和 `ssh.service`；只有执行下面的显式配置命令后，SSH 才会按指定地址启动并开机自启。

### 4.1.1 从源码完成首次 SSH 配置

先完成第 2 节的源码获取、第 3 节的专用网卡静态地址配置，以及首次联网阶段的环境准备。首次启用 SSH 必须在网关**本地控制台**、当前完整源码目录中进行；不要在尚未受限的远程连接中首次设置运维密码。

```bash
cd <NeuroBridge源码目录>
test -x linux/ubuntu/setup-ssh-operations.sh
test -x linux/ubuntu/configure-ssh-operations.sh
sudo ./linux/ubuntu/setup-ssh-operations.sh --quick
```

快速模式会按顺序确认运维账户、密码、网关私有监听 IP、唯一允许的运维主机 IP 和 SSH 端口。脚本只将 SSH 绑定到填写的网关地址，并把快速模式的单一来源地址规范为 `/32`。密码采用隐藏输入并要求二次确认；不要在源码、命令历史、终端截图或现场记录中保存密码。

配置完成后立即在本地控制台验证：

```bash
sudo sshd -t
sudo systemctl is-enabled ssh.service
sudo systemctl is-active ssh.service
sudo ss -ltnp 'sport = :<SSH端口>'
```

预期服务为 `enabled` 与 `active`，且只在已确认的网关 IP/端口监听。若配置器报告残留 `sshd` 监听，保持在本地控制台执行；不要重新启用 `ssh.socket`、启用通配监听或手工结束未知进程。日常外部运维操作见 [头环数据网关 SSH 运维操作指南 v1.0](../../doc/tech/对外/头环数据网关%20SSH%20运维操作指南/头环数据网关%20SSH%20运维操作指南_v1.0.md)。

网关与 B 端主机通过单根专用网线直连、头环离线时的完整引导、录播联调、审核版本的一键更新重载与排障步骤，见 [网关 SSH 运维与 B 端录播联调指南](../../doc/tech/%E7%BD%91%E5%85%B3%20SSH%20%E8%BF%90%E7%BB%B4%E4%B8%8E%20B%20%E7%AB%AF%E5%BD%95%E6%92%AD%E8%81%94%E8%B0%83%E6%8C%87%E5%8D%97.md)。指南同时记录临时局域网到最终直连的配置切换、SSH 验收清单，以及用户名、CIDR、PAM 密码策略、`ssh.socket` 和残留 sshd 端口冲突的现场排查过程。

常规单网卡、单 B 端场景，在网关的**本地控制台**使用快速模式。它读取 Git 忽略的 `config/ssh-operations.txt`；首次运行时会从模板自动创建权限为 `600` 的实际文件，某个字段为空或缺失时才提示输入：

```bash
sudo ./linux/ubuntu/setup-ssh-operations.sh --quick
```

配置文件支持 `operator_user`、`password`、`listen_address`、`allow_from` 和 `port`。其中 `password` 可以是至少 6 位数字明文；存在时直接使用，不回显或写入日志，缺失时隐藏输入并二次确认。配置含明文密码时必须保持权限 `600`。`allow_from` 只接受单台私有 IPv4 并自动收紧为 `/32`。原 `--quick 192.168.88.20` 仍兼容，并覆盖文件中的 `allow_from`。

需要允许来源网段，或不希望使用 TXT 时，继续使用保留的完整交互模式：

```bash
sudo ./linux/ubuntu/setup-ssh-operations.sh
```

完整模式会从当前设备已配置的全局私有 IPv4 中选择默认监听 IP；若通过已有 SSH 会话执行，优先使用该会话连接到的网关本地 IP，否则使用检测到的第一个地址。检测不到符合条件的地址时，提示中不显示默认值并要求手工输入。当前 SSH 客户端 `/32`（如可识别）仍作为允许来源默认值，所有值在应用前都要求人工确认。

完整模式会隐藏输入并要求再次确认至少 6 位纯数字密码；快速模式可从上述权限受限的现场 TXT 读取明文密码。两者都不会在命令行或日志中输出密码。配置器仅为该专用运维账户显式选择系统 `chpasswd` 支持的 SHA512 加盐哈希写入方式，从而不经过 PAM 的最短 8 位检查；不会修改整机 PAM 密码策略，其他账户不受影响。最终输入 `YES` 确认时不区分大小写。若要在自动化部署中使用非交互方式，通过标准输入提供密码；以下 IP 和网段只是示例，必须替换为现场确认值：

```bash
printf '%s\n' '<至少6位数字密码>' | sudo ./linux/ubuntu/configure-ssh-operations.sh \
  --operator-user neuroops \
  --operator-password-stdin \
  --listen-address 192.168.88.10 \
  --allow-from 192.168.88.20/32
```

配置器要求监听地址已经配置在本机网卡上，且监听地址与允许来源均为 RFC1918 私有 IPv4 地址。单个来源可写成 `192.168.88.20` 或 `192.168.88.20/32`；网段输入会在写入 sshd 前自动规范化，例如 `192.168.88.20/23` 会转换为其所属网段 `192.168.88.0/23`。它创建或更新单一 `neuroops` 本地账户并设置其密码，安装 `/etc/ssh/sshd_config.d/00-neurobridge-operations.conf`：只监听指定地址，仅允许该账户使用账号密码；禁止 root 登录、公钥登录、端口转发、代理转发、隧道和 X11 转发。启用前会关闭 Ubuntu 的 `ssh.socket` 通配监听，并停止 `ssh.service` 控制组内可能遗留的旧监听进程，确认端口释放后再启动新服务；失败时恢复原有 service/socket 状态。清理残留进程会中断已有 SSH 会话，因此发现端口残留时只允许在网关本地控制台执行，不会在远程会话中强制清理。脚本会拒绝已有 SSH 配置留下的额外端口或监听地址。来源不在 `--allow-from` 范围内的连接即使通过认证也不能获得 shell 或执行命令。仍须保持网关只接入受控专用网络，并在现场防火墙中仅放行确认的运维来源与 SSH 端口。

一键配置成功后，`ssh.service` 会被 systemd 设为开机自启，且独立于 `neurobridge.service`：启动、停止或重启网关业务服务不会关闭 SSH。配置器会同时安装仅供 SSH 使用的 systemd 覆盖：开机时先等待网络就绪，并在 `sshd` 绑定前最多等待 90 秒，直到指定监听 IP 实际出现在网卡上。它仍只监听该固定私有地址，不会改为通配监听。`config/ssh-operations.txt` 不会在开机时自动重读；修改账号、密码、监听 IP、允许来源或端口后，必须重新执行 `sudo ./linux/ubuntu/setup-ssh-operations.sh --quick`。详细启动状态表和重启验收命令见上述联调指南第 4.4 节。

配置完成后，现场只使用这一个受信任的 SSH 运维账号。一键配置会把当前源码同步到该账号固定的 `~/NeuroBridge` 项目目录；登录后先进入该目录，后续代码和运维操作都以它为准：

```bash
ssh neuroops@192.168.88.10
neurobridge-ops project            # 显示固定项目目录
neurobridge-ops status             # 当前服务状态 + 最近 200 条日志
neurobridge-ops logs --lines 500   # 查询指定数量的历史日志，最大 1000 条
neurobridge-ops logs --follow      # 实时追踪新日志，Ctrl-C 停止
neurobridge-ops audit --lines 500  # 查询 SSH 运维操作审计，最大 1000 条
watch -n 2 neurobridge-ops status  # 每 2 秒刷新一次实时服务状态
neurobridge-ops branch             # 查询当前分支，不拉取或部署
neurobridge-ops update             # 从 origin 快进更新当前分支并重新加载
neurobridge-ops update --branch codex/linux-headband-ethernet-gateway
neurobridge-ops restart            # 重启网关
neurobridge-ops stop
neurobridge-ops start
```

每次 `status`、`logs`、`audit`、`update`、`start`、`stop` 或 `restart` 都会写入系统日志标识 `neurobridge-ops-audit`，记录系统时间、`sudo` 确认的运维账户、受限动作、参数摘要和开始/成功/失败结果；`logs --follow` 是持续命令，只记录开始。日志不包含账号密码、SSH 会话内容、网关配置或业务原始数据。使用 `neurobridge-ops audit` 查询；SSH 认证来源仍以系统 `sshd` 日志为准。

SSH 运维时，源码更新由 `neurobridge-ops update` 统一执行：它只使用固定项目目录，确认没有本地修改后，从 `origin` 获取指定分支并仅快进部署；不传 `--branch` 时使用当前分支。运维人员不应直接运行 Git、替换源码目录或传入临时路径。取得变更授权且网关能访问批准代码源后，SSH 登录执行：

```bash
neurobridge-ops update
```

可用 `neurobridge-ops branch` 查询当前分支，或执行 `neurobridge-ops update --branch <分支名>` 切换到批准的远端分支；仅远端存在时自动建立本地同名分支。detached HEAD 必须显式指定分支。若工作树有本地修改、远端分支不存在、本地领先或分叉而无法快进，或现场隔离而无法访问批准代码源，`update` 会失败。不要通过手工 Git、`rsync` 或未知文件绕过检查；应联系网关交付方按批准流程处理。因为该账号维护的脚本随后会以 root 安装，所以该账号在权限意义上属于网关管理员账号；密码和允许来源 IP 必须按管理员凭据管理。

已经启用旧版 SSH 运维的网关升级到本方案后，需要从当前完整源码目录在网关本地重新运行一次 `sudo ./linux/ubuntu/setup-ssh-operations.sh`，脚本会初始化 `~/NeuroBridge`；之后直接维护这个项目目录。

分支查询和可选 `--branch` 参数也需要通过上述本地配置重新安装命令；仅更新 `/opt/neurobridge` 或源码不会自动升级已安装的运维入口。重新配置后，先执行 `neurobridge-ops branch` 和 `neurobridge-ops help` 验证。查询当前分支不会拉取或部署，工作树有修改时也可查询；detached HEAD 没有当前分支，须显式指定更新目标。更新审计记录实际分支及成功或失败结果。

`restart`、`stop` 或 `update` 会断开 B 端 WebSocket 连接；恢复后 B 端必须重新连接、调用 `getStatus` 并重新订阅。

应用后在本地控制台检查：

```bash
sudo sshd -t
sudo systemctl --no-pager --full status ssh.service
```

如需撤销这个 SSH 运维入口，在本地控制台删除 `/etc/ssh/sshd_config.d/00-neurobridge-operations.conf`、`/etc/sudoers.d/neurobridge-operator`、`/usr/local/sbin/neurobridge-ops-status`、`/usr/local/sbin/neurobridge-ops-logs`、`/usr/local/sbin/neurobridge-ops-update`、`/usr/local/sbin/neurobridge-ops-command` 和 `/usr/local/bin/neurobridge-ops`，再执行 `sudo systemctl restart ssh.service`。是否停用 SSH 服务由现场运维策略决定。

## 5. 填写现场配置

编辑安装脚本生成的配置，不要编辑 `/opt/neurobridge/config/gateway.ubuntu.toml.example`，因为后者会在后续部署时被源码覆盖：

```bash
sudoedit /etc/neurobridge/gateway.toml
```

至少逐项确认下面的字段：

| 配置项 | 首次部署要求 |
| --- | --- |
| `[access] mode` | Ubuntu 固定 `wired_b_side`；不要复制麒麟的 `local_browser` 配置。 |
| `[local_ui]` | Ubuntu 固定关闭；采集控制与静态页面使用独立的下载服务。 |
| `[server] host` | Ubuntu 为已批准的专用网卡地址，模板为 `192.168.88.10`。 |
| `[server] port`、`path` | 填写双方确认的固定 WebSocket 端口和路径。 |
| `[network] mode` | Ubuntu 默认 `static`；单物理网口可用 `auto`，多网口必须显式选择接口。 |
| `[ble] enabled` | 无头环/冒烟验证时保持 `false`；完成该 Ubuntu 主机的真实头环 POC 后才改为 `true`，同时确认扫描匹配字段。 |
| `[recording] directory` | 保持默认的 `/var/lib/neurobridge/recordings`，除非已按权限和容量要求另行配置。 |
| `[recording] subject_id` | 填写演示或采集使用的受试者标识；无值可留空。 |
| `[recording] replay_enabled` | 默认 `true`；`false` 禁止离线录播，保留实时采集及历史文件。可由运维命令持久化并重启生效。 |
| `[recording] replay_recording_id` | 可选：填写一个存在且非空的录制会话 ID 以固定回放目标；留空、填写已删除 ID 或空会话时，离线录播自动选择该目录中最新的非空历史会话。 |
| `[download]` | 仅在专用有线网络确有导出需求时启用；`host` 必须是网关静态 IP，端口由双方确认。 |
| `[algorithm] enabled` | 默认 `true`。安装器已提供 bridge，无需填写路径；需要仅采集原始数据或维护时才改为 `false`。 |

配置语法和权限可先由服务账户验证：

```bash
sudo -u neurobridge /opt/neurobridge/venv/bin/python -c 'from neurobridge.config import load; load("/etc/neurobridge/gateway.toml"); print("gateway.toml OK")'
```

若采用 DHCP 模式，还要将 `interface` 改为明确的网卡名（不能是 `auto`），并填写 `subnet_cidr`、`dhcp_range_start`、`dhcp_range_end` 与 `dhcp_lease_time`。网关本身仍必须在该网卡使用 `[server].host` 作为静态地址；DHCP 只为 B 端分配地址，不提供 DNS，也不会让端口动态化。

算法默认开启：安装器会使用锁定的 SDK 和依赖自动构建 bridge，并安装到 `/usr/local/lib/neurobridge/neurobridge_affective_bridge`；网关在未配置 `algorithm.command` 时自动使用该路径。受控的真实数据 POC 可临时将 `[algorithm].enabled = false`，以录制只含原始数据的基线；算法结果的字段语义和有效性仍须以实际数据验证。详情见 [算法 SDK 接入 POC](../../doc/tech/%E7%AE%97%E6%B3%95%20SDK%20%E6%8E%A5%E5%85%A5%20POC.md)。

## 6. 启动并验证

配置完成后启动服务并确认它已设为开机自启：

```bash
sudo systemctl start neurobridge.service
sudo systemctl is-enabled neurobridge.service
sudo systemctl --no-pager --full status neurobridge.service
sudo journalctl -u neurobridge.service -n 100 --no-pager
```

状态应为 `active (running)`，日志中应出现 `Listening on ws://...`。也可在网关上检查监听端口（将示例端口替换为实际值）：

```bash
sudo ss -lntp
```

Ubuntu 从 B 端访问 `http://192.168.88.10:8766/capture/` 或 `/b-client-test/`（以配置为准），并使用 `neurobridge.v1` 子协议连接专网 WS。连接后先调用 `getStatus`，再调用 `getLatest` 或 `subscribe`；断线重连后重新订阅。麒麟/Windows 本机 Profile 才使用回环网页。

兼容 BLE 策略的无头环冒烟验证可保持 `[ble].enabled = false`，此时 `getStatus` 应显示未连接并按兼容策略验证录播；当前银河麒麟耳机 `data_source.type="serial"` 不适用该回放流程，离线 `getLatest` 或 `subscribe` 必须返回 `409 STREAM_NOT_AVAILABLE_REASON`。实时采集验收还应覆盖耳机串口连接、数据到达、断线重连和服务重启后的恢复。
录播开关（更新到支持该功能的版本后，在网关/SSH 运维终端执行）：

```bash
neurobridge-ops replay enable   # 允许离线时按需录播
neurobridge-ops replay disable  # 禁止录播，保留原文件
neurobridge-ops replay status   # 查询保存的策略和服务状态
```

开关修改 `/etc/neurobridge/gateway.toml` 中 `[recording].replay_enabled`，正在运行的服务会重启，结束当前会话并断开 B 端 WS；随后需要重新连接、查询状态和订阅。原本停止的服务仅保存配置，不会启动。重启失败返回非零，已选择的策略仍保留，须查看服务日志，不能仅凭配置值宣称运行时已生效。`enable` 不强制切换头环或自动创建订阅，禁止时无头环的取数请求返回既有录播不可用错误。`getStatus` 中 `mode="replay"` 仍表示离线模式，不代表正在播放或允许播放；应结合可用流和运维策略判断。

没有 SSH 运维入口时，可在网关本地执行 `sudo /opt/neurobridge/venv/bin/python -I -m neurobridge.replay_control enable`（或 `disable`、`status`）。既有运维入口随 `neurobridge-ops update` 的部署流程刷新，无需重新配置 SSH、账号或密码；仅复制已授权入口的新版命令，不为未配置设备新增 SSH 授权。更多错误与审计语义见 [内部联调指南](../../doc/tech/网关%20SSH%20运维与%20B%20端录播联调指南.md#50-允许与禁止录播)。

首次无头环冒烟验证可保持 `[ble].enabled = false`，此时 `getStatus` 应显示未连接；只要 `[recording].directory` 下存在非空历史会话，离线 `getLatest` 或 `subscribe` 就会自动选择最新会话并产生 `mode="replay"` 数据。若填写有效的 `replay_recording_id`，则该会话优先。实时采集验收还应覆盖头环连接、数据到达、断线重连和服务重启后的恢复。

如果 `[download].enabled = true`，本机浏览器访问 `http://127.0.0.1:<下载端口><下载路径>`；旧有线策略才从专网 B 端访问私网地址。该接口没有 TLS 或应用层认证。

## 7. 兼容有线策略的静态地址与 DHCP 模式

`[network].mode` 将地址分配与采集/录播业务隔离，可在两种模式间切换：

- `static`（默认）：网关和 B 端预先配置静态地址；B 端直接使用 `server.host`、`server.port` 和 `server.path` 建立 WebSocket。
- `dhcp`：网关的接入网卡仍必须预先配置为固定的 `server.host`。独立的 `neurobridge-dhcp.service` 在该网卡为 B 端分配地址，并通过 DHCP 默认网关选项发布 `server.host`；B 端从系统 DHCP 租约读取默认网关，再使用固定 `server.port` 与 `server.path` 连接。

DHCP 不做 DNS（`dnsmasq` 仅服务配置的网卡），也不能让端口动态化。B 端程序必须具备读取操作系统默认网关的能力；纯浏览器 JavaScript 无法读取 DHCP 租约，不能单独实现自动发现。切换模式后执行：

```bash
sudo systemctl restart neurobridge-dhcp
sudo systemctl restart neurobridge
```

从 DHCP 切回静态模式时，`ExecCondition` 会停止并跳过 DHCP 单元。若确定永久不再使用 DHCP，可执行 `sudo systemctl disable --now neurobridge-dhcp.service`，再按运维策略卸载 `dnsmasq`；这不会修改网关业务进程、录播文件或北向 WebSocket 服务。

配置中的 `[download]` 会在专用有线网络提供无鉴权 HTTP 下载服务；上线前必须确认其静态 IP 与端口，并保持该网络不连接公网或不受控局域网：

- `GET /downloads`：返回已结束录播会话的下载索引；
- `GET /downloads/recordings/<recordingId>.zip`：按需导出一个已结束录播会话；
- `GET /downloads/logs/neurobridge-logs.zip`：下载当前与轮转后的运行日志快照。

采集控制台可直接双击静态包的 `capture/index.html`，默认访问 `http://192.168.88.10:8766`；也可打开网关托管的 `http://192.168.88.10:8766/capture/index.html`。电脑无需本地服务。需启用 `[download].enabled`、`capture_control_enabled`，并在设备接入验证后启用 `[ble].enabled`。页面支持开始扫描、停止采集、诊断摘要和保存 ZIP；停止只结束设备任务与当前录制，HTTP/WS 继续监听，保存当前会话会先执行停止。关闭页面不会停止采集。

`[download].capture_allowed_origins` 默认 `["null"]`，允许本地文件页面；配置的 HTTP 监听同源自动允许，其他静态服务器须填写精确 origin，不支持通配。来源检查在启停前执行，不代替操作员授权。`capture_control_enabled=false` 关闭控制 API 和页面托管，完成会话下载仍可用。完整契约和错误码见 [内部联调指南](../../doc/tech/网关%20SSH%20运维与%20B%20端录播联调指南.md)，使用步骤见 [采集页说明](../../web/capture/README.md)。本次未更改已发布 B 端 WebSocket 契约。

正在采集的会话不能导出，避免得到不完整的录播文件。可用 `systemctl status neurobridge`、`journalctl -u neurobridge` 和持久化日志排查运行情况。

## 8. 日常运行、升级与停机

常用运行命令：

```bash
sudo systemctl status neurobridge.service
sudo journalctl -u neurobridge.service -f
sudo tail -f /var/log/neurobridge/neurobridge.log
sudo systemctl restart neurobridge.service
sudo systemctl stop neurobridge.service
```

`restart` 和 `stop` 会断开所有 B 端 WebSocket 连接；B 端恢复连接后必须重新执行 `getStatus` 和订阅流程。服务已在首次安装时启用，正常重启主机后会自动启动。

更新代码前先备份现场配置与录播数据，并确认当前没有需要保持的 B 端连接。若要从 GitHub 获取新的已审查代码，在源码目录中执行匿名拉取；随后的一键部署完全不访问网络：

```bash
git pull --ff-only
./linux/ubuntu/update-ubuntu.sh
```

也可以由其他受控流程将新的源码工作树放到目标机，然后直接运行 `./linux/ubuntu/update-ubuntu.sh`。该脚本适用于 Python、安装器和 bridge 的全部代码更新：它会保留 `/etc/neurobridge/gateway.toml`、`/var/lib/neurobridge/recordings` 与现有 Python 虚拟环境，并从当前源码的 `third_party/` 本地重建 bridge。缺少 Ubuntu 基础镜像依赖或既有虚拟环境时，它会失败而不会尝试联网修复。

完整安装会保留已有的 `/etc/neurobridge/gateway.toml`，但仍应在执行前按现场运维要求备份该文件和 `/var/lib/neurobridge/recordings`。不要将录播数据、日志或现场配置提交到 Git。

## 9. 常见问题排查

### 运行诊断与日志采集

网关本地控制台使用包含本次改动的源码执行（不需要 Git 工作区或 SSH）：

```bash
sudo ./linux/ubuntu/collect-runtime-diagnostics.sh
# 也可指定起点与导出目录：
sudo ./linux/ubuntu/collect-runtime-diagnostics.sh --since '72 hours ago' --output-dir /tmp
```

命令打印 `neurobridge-runtime-*.tar.gz` 的完整路径。包内包括所选时间范围内的网关、BlueZ、网络管理器、内核和运维审计日志，已有启动记录、当前服务重启状态、网卡/地址/路由/邻居/监听端口及蓝牙控制器状态，以及 `/var/log/neurobridge/` 中全部现存轮转日志。持久化日志不按时间裁剪；自定义日志位置时传 `--log-dir <目录>`。不复制配置文件、密码、私钥、录制会话或业务原始数据文件。使用 `sudo` 时导出包归发起用户所有，权限为 `0600`；日志可能含现场网络地址，应在受控渠道传递，禁止提交 Git。

脚本只读查询，不改地址、不重启服务、不启动扫描或发送探测包。某项命令失败仍继续采集，结果见包内 `collection-status.txt`。若 journal 只保留本次启动，`--since` 无法找回已经丢失的历史；`journal-boots.txt` 可用于核对覆盖范围，运行文件轮转日志用于补充应用侧历史。

新增运维配置向后兼容，已有 `gateway.toml` 不增加字段也会采用以下默认值：

| 配置 | 默认值 | 含义 |
| --- | --- | --- |
| `[server].send_timeout_seconds` | `5.0` 秒 | 单次发送（含等待同连接发送锁）的期限，必须大于零且有限。 |
| `[server].delivery_queue_size` | `16` 条消息 | 每个订阅的事件队列上限，可设置 `1..1024`；用于隔离慢客户端，不提供断网补传。 |
| `[logging].diagnostics_interval_seconds` | `30.0` 秒 | Linux 网络状态轮询与蓝牙/采集摘要间隔；`0` 关闭周期诊断，错误与连接变化日志继续保留。 |

发送失败、超时或队列满时，只清理并关闭故障 B 端连接及其订阅；采集、原始字节/算法结果落盘、其他连接和头环重连继续。B 端恢复后重新连接，调用 `getStatus` 并重新订阅。没有新增 WebSocket Ping/Pong、JSON 心跳或补传。

定位时以 `bootId` 关联一次网关运行，以 `sessionId`/`subscriptionId` 关联 B 端连接和订阅，以 `attempt` 关联一次蓝牙扫描连接过程：

- 网络：`B-side delivery failed` 包含事件模式、时间戳、队列深度、耗时、错误类型、`errno` 和可用的关闭码；`backlog exceeded` 区分积压与发送异常；`Network diagnostic` 记录启动、网络状态变化和发送故障后的快照（故障触发最多每 5 秒一次）。快照的 `carrier`、IPv4 地址、路由、邻居 `state` 和 `hostAssigned` 用于判断链路/地址问题，不等同于双方互通验证；周期采样可能漏掉短暂变化，需要对照内核日志。
- 蓝牙：扫描结果包含发现/匹配数量和候选扫描 RSSI；失败记录 `scan/connect/subscribe/initialize_capture/start_capture/capture` 阶段；`BLE link disconnected` 包含连接持续时间、最后收包距今时间、分类型包数及主动停机/初始化失败清理上下文。Bleak 回调未提供 HCI 原因时明确记录 `backendReason=unavailable`，不推断头环关机或硬件故障。扫描未命中不再以一次真实连接断开计数。
- 采集：`Capture progress` 包含录制会话、窗口时间范围、当前窗口包数和有效性，`BLE receive summary` 为本次连接累计通知包数；二者均不打印人体原始字节或算法值。回调处理错误有独立日志，后续通知继续处理；底层库的 DEBUG 数据帧追踪限制为 INFO，避免完整原始数据进入诊断日志。
- 启动：网关版本和运行配置上限在启动日志中记录；TaskGroup 内启动/运行异常同时写入持久化日志，避免轮转文件只有重复启停而没有原因。

源码回归须覆盖实时、录播、头环断开重连、客户端不可达与重新接入，并检查故障客户端不会阻止另一客户端收数、原始数据与算法结果继续落盘、旧订阅不会复用。模拟通过不等于 Ubuntu 蓝牙实机或现场验收通过。

| 现象 | 检查与处理 |
| --- | --- |
| 服务启动失败或不断重启 | 运行 `sudo journalctl -u neurobridge.service -n 100 --no-pager`。优先检查 TOML 语法、`server.host` 是否已配置在网卡上、端口是否被占用，以及 `/etc/neurobridge/gateway.toml` 是否可由 `neurobridge` 读取。 |
| 日志出现无法绑定地址 | 用 `ip -br addr` 检查专用网卡；`[server].host` 必须是该主机实际拥有的私有或回环 IP，不能填写 DNS 名称、通配地址或 B 端地址。 |
| B 端无法建立 WebSocket | 确认网线、两端 IP/掩码、专用网卡防火墙、端口和 `path`；握手必须提供 `neurobridge.v1` 子协议。连接恢复后先 `getStatus`，再重新订阅。 |
| `neurobridge-dhcp.service` 显示未运行 | 在 `static` 模式下这是预期行为：其 `ExecCondition` 会跳过 DHCP 服务。只有配置为 `dhcp` 并填写完整 DHCP 参数后才应运行。 |
| 未收到实时数据 | 先从启动日志确认 `transport=serial` 或 `bluetooth`。串口检查候选/握手、ACK 后的 `01`、无响应 `0xE1` 是否写入、有效帧超时和累计/区间丢包；BLE 检查 `[ble].enabled`、控制器、名称/UUID。不要仅凭服务进程存活或串口有字节判断头环已连接。 |
| 兼容录播策略离线时没有录播数据 | 检查 `[recording].directory` 是否指向存放历史会话的目录，并确认其中至少有一个非空会话；该排障项不适用于当前银河麒麟耳机串口策略，串口离线应检查设备枚举、权限、占用和验证日志。 |
| 下载接口不可用 | 确认 `[download].enabled = true`、监听 IP/端口与防火墙配置一致；只有已结束的录制会话能导出。 |

如果 `update-ubuntu.sh` 在编译 bridge 时失败，先不要反复修改依赖或安装额外库。运行下面命令并将生成的压缩包上传给开发人员；包内只有 CMake/build 日志、系统与工具版本、当前源码 revision 和最近的服务日志，不包含 `gateway.toml`、录播或原始 BLE 数据：

```bash
sudo ./linux/ubuntu/collect-ubuntu-build-diagnostics.sh
```

使用 `sudo` 运行时，生成的压缩包会归发起该命令的登录用户所有，权限为仅该用户可读写（`0600`），可直接上传或复制；不会归 root 锁定。
