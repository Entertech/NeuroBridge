# Ubuntu 网关部署与运行教程

本目录保存 Ubuntu x86_64 网关的部署专属内容：

- `update-ubuntu.sh`：目标机一键部署入口；只使用当前源码目录，不执行 Git 或网络操作。
- `prepare-ubuntu24.04-environment.sh`：一次性联网环境准备，安装系统包与锁定的 Python 运行依赖；完成后可断网。
- `configure-ssh-operations.sh`：在已确认的私有运维网络上显式启用仅公钥的 SSH 运维入口。
- `setup-ssh-operations.sh`：交互式一键 SSH 运维配置入口，调用严格的配置器。
- `collect-ubuntu-build-diagnostics.sh`：收集编译日志、工具版本和 CMake 诊断，不包含现场配置或原始数据。
- `install-ubuntu.sh`：部署实现，安装锁定的算法 bridge、服务账户和 systemd 服务，并启用开机自启。
- `systemd/`：开机自启服务单元；异常退出后 3 秒自动重启。
- `logrotate/`：网关持久化日志的每日轮转配置。

本文用于在一台**Ubuntu 24.04 LTS x86_64** 主机上部署 NeuroBridge。首期部署目标是 N100/N150 等 x86_64 小主机；不要在 ARM、Windows、macOS 或非 systemd 环境上使用这些脚本。目标机可匿名从 GitHub 公开 HTTPS 地址获取本仓库源码，无需登录；源码到位后，所有部署、bridge 构建和运行步骤均不访问互联网。网关和 B 端主机必须只接入双方确认的专用有线网络，不能暴露到公网、无线网络或不受控局域网。

> 文中的 `192.168.88.10`、`192.168.88.20`、端口和网卡名只是示例。安装前必须由双方确认实际静态 IP、子网掩码、WebSocket 端口、下载端口和网卡名；不要把示例值直接用于现场。

## 1. 安装前准备

准备好以下内容：

- Ubuntu **24.04 LTS** x86_64，使用 systemd 启动；安装账户具有 `sudo` 权限。
- 已审查的 NeuroBridge 源码版本（建议固定到已确认的 tag 或 commit），以及可连接头环的已验证蓝牙 5.x 适配器（仅实时采集需要）。可直接匿名获取：`git clone https://github.com/Entertech/NeuroBridge.git`。
- 可联网时先运行一次 `./linux/prepare-ubuntu24.04-environment.sh`，它会安装 `python3`、`rsync`、CMake、C++17 编译器、Eigen3、BlueZ、dnsmasq，并创建 `/opt/neurobridge/venv`、安装锁定的 Python 运行依赖。完成后即可断开互联网；安装器只验证这些前提，绝不调用 APT、PyPI 或其他下载服务。
- 网关与 B 端的专用有线链路。先确定网关地址、B 端地址、掩码、端口和网卡名。
- 若要使用录播，准备好录制数据目录及要回放的 `recordingId`；若要使用算法，先完成该 Ubuntu 主机上的真实数据 POC。算法默认启用，但 POC 前可暂时设为 `false`。
- 若要使用 SSH 运维，准备一台受控运维主机的 **SSH 公钥**、它的固定 IP/CIDR，以及网关用于运维的私有静态 IP。公钥可传入网关；私钥绝不能传入或保存到网关。

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
./linux/prepare-ubuntu24.04-environment.sh
```

脚本成功后可断开互联网；后续使用 `./linux/update-ubuntu.sh` 即可完成安装、更新和 bridge 重建。

## 3. 先配置专用有线网络

服务会绑定 `[server].host` 指定的 IP；因此，先让 Ubuntu 的专用网卡拥有该静态地址，再启动服务。使用 `ip -br link` 找到实际网卡名，例如 `enp1s0`。编辑现有的 `/etc/netplan/*.yaml`，并按现场网络改成相应地址；不要覆盖安装器或现场已有的其他网卡配置。

下面仅为“网关 `enp1s0` 直连 B 端、网段 `192.168.88.0/24`”的最小示例：

```yaml
network:
  version: 2
  ethernets:
    enp1s0:
      addresses:
        - 192.168.88.10/24
```

应用后核对地址：

```bash
sudo netplan apply
ip -br addr show enp1s0
```

静态地址模式下，B 端也应配置为同一网段的另一个固定地址（例如 `192.168.88.20/24`），然后连接 `ws://<网关IP>:<端口><路径>`。如现场启用了 UFW 或其他防火墙，只允许该专用网卡访问已确认的 TCP WebSocket 端口；若启用下载服务，也只放行其 HTTP 端口。

## 4. 安装网关

安装脚本会创建 `neurobridge` 服务账户，将源码同步至 `/opt/neurobridge`，在本地构建并安装算法 bridge，并安装与启用 systemd 单元。它不会安装系统包或 Python 包，缺少基础镜像前提时会失败。使用一键入口：

```bash
./linux/update-ubuntu.sh
```

安装后会创建但**不会自动启动**网关，避免使用未确认的示例网络参数。关键位置如下：

| 位置 | 用途 |
| --- | --- |
| `/opt/neurobridge` | 被 systemd 实际运行的源码与 Python 虚拟环境。 |
| `/etc/neurobridge/gateway.toml` | 现场部署配置；root 可写，`neurobridge` 服务账户可读。 |
| `/var/lib/neurobridge/recordings` | 实时录制和录播数据。 |
| `/var/log/neurobridge/neurobridge.log` | 持久化运行日志；每天轮转，默认保留 14 份压缩归档。 |

## 4.1 启用 SSH 运维入口（可选）

SSH 只用于网关运维，**不**用于 B 端读取数据、控制采集或调用算法。`prepare-ubuntu24.04-environment.sh` 会安装 `openssh-server`，但会保持其停用；只有执行下面的显式配置命令后，SSH 才会启动并开机自启。

网关与 B 端主机通过单根专用网线直连、头环离线时的完整引导、录播联调、审核版本的一键更新重载与排障步骤，见 [网关 SSH 运维与 B 端录播联调指南](../doc/tech/%E7%BD%91%E5%85%B3%20SSH%20%E8%BF%90%E7%BB%B4%E4%B8%8E%20B%20%E7%AB%AF%E5%BD%95%E6%92%AD%E8%81%94%E8%B0%83%E6%8C%87%E5%8D%97.md)。

在受控运维主机生成一对密钥，只把 `.pub` 公钥复制到网关。例如：

```bash
ssh-keygen -t ed25519 -a 64 -f ~/.ssh/neurobridge-ops
scp ~/.ssh/neurobridge-ops.pub <现有管理员>@<网关私有IP>:/tmp/neurobridge-ops.pub
```

在网关的**本地控制台**执行下面的一键配置。它会自动给出唯一的本机私有 IP、当前 SSH 客户端 `/32`（如可识别）和常用公钥路径作为默认值，仍要求人工确认；私网来源、监听地址和公钥不能被脚本擅自猜测或放宽：

```bash
sudo ./linux/setup-ssh-operations.sh
```

若要在自动化部署中使用非交互方式，仍可直接调用严格配置器。以下 IP 和网段只是示例，必须替换为现场确认值：

```bash
sudo ./linux/configure-ssh-operations.sh \
  --operator-user neuroops \
  --authorized-key-file /tmp/neurobridge-ops.pub \
  --listen-address 192.168.88.10 \
  --allow-from 192.168.88.20/32
```

若必须通过现有 SSH 会话执行，现有登录用户名必须与新建的 `--operator-user` 相同，以避免访问策略切换时将当前管理员锁在门外。配置器要求监听地址已经配置在本机网卡上，且监听地址与允许来源均为 RFC1918 私有 IPv4 地址。它创建或更新单一 `neuroops` 本地账户，锁定密码、写入公钥，并安装 `/etc/ssh/sshd_config.d/00-neurobridge-operations.conf`：只监听指定地址，仅允许该账户使用公钥；禁止 root 登录、密码登录、端口转发、代理转发、隧道和 X11 转发。脚本会拒绝已有 SSH 配置留下的额外端口或监听地址。来源不在 `--allow-from` 范围内的连接即使通过认证也不能获得 shell 或执行命令。仍须保持网关只接入受控专用网络，并在现场防火墙中仅放行确认的运维来源与 SSH 端口。

配置完成后，使用如下方式登录。`neurobridge-ops` 会自动使用受限的无密码 sudo 权限，适合日常值守而不会获得配置文件编辑、软件安装或任意 root shell 权限：

```bash
ssh -i ~/.ssh/neurobridge-ops neuroops@192.168.88.10
neurobridge-ops status             # 当前服务状态 + 最近 200 条日志
neurobridge-ops logs --lines 500   # 查询指定数量的历史日志，最大 1000 条
neurobridge-ops logs --follow      # 实时追踪新日志，Ctrl-C 停止
watch -n 2 neurobridge-ops status  # 每 2 秒刷新一次实时服务状态
neurobridge-ops restart            # 重启网关
neurobridge-ops stop
neurobridge-ops start
```

`restart` 或 `stop` 会断开 B 端 WebSocket 连接；恢复后 B 端必须重新连接、调用 `getStatus` 并重新订阅。需要修改 `/etc/neurobridge/gateway.toml`、部署新版本或更改防火墙时，应由现场的独立系统管理员账户在本地控制台或已批准的更高权限运维流程执行，不能扩大 `neuroops` 的权限。

应用后在本地控制台检查：

```bash
sudo sshd -t
sudo systemctl --no-pager --full status ssh.service
```

如需撤销这个 SSH 运维入口，在本地控制台删除 `/etc/ssh/sshd_config.d/00-neurobridge-operations.conf`、`/etc/sudoers.d/neurobridge-operator`、`/usr/local/sbin/neurobridge-ops-status`、`/usr/local/sbin/neurobridge-ops-logs` 和 `/usr/local/bin/neurobridge-ops`，再执行 `sudo systemctl restart ssh.service`。是否停用 SSH 服务由现场运维策略决定。

## 5. 填写现场配置

编辑安装脚本生成的配置，不要编辑 `/opt/neurobridge/config/gateway.toml.example`，因为后者会在后续部署时被源码覆盖：

```bash
sudoedit /etc/neurobridge/gateway.toml
```

至少逐项确认下面的字段：

| 配置项 | 首次部署要求 |
| --- | --- |
| `[server] host` | 与第 3 步中网关专用网卡的静态 IP 完全一致。 |
| `[server] port`、`path` | 填写双方确认的固定 WebSocket 端口和路径。 |
| `[network] mode` | 通常选 `static`；只有 B 端程序能读取 DHCP 默认网关、且现场明确要自动分配地址时才选 `dhcp`。 |
| `[ble] enabled` | 无头环/冒烟验证时保持 `false`；完成该 Ubuntu 主机的真实头环 POC 后才改为 `true`，同时确认扫描匹配字段。 |
| `[recording] directory` | 保持默认的 `/var/lib/neurobridge/recordings`，除非已按权限和容量要求另行配置。 |
| `[recording] subject_id` | 填写演示或采集使用的受试者标识；无值可留空。 |
| `[recording] replay_recording_id` | 可选：填写一个存在且非空的录制会话 ID 以固定回放目标；留空、填写已删除 ID 或空会话时，离线录播自动选择该目录中最新的非空历史会话。 |
| `[download]` | 仅在专用有线网络确有导出需求时启用；`host` 必须是网关静态 IP，端口由双方确认。 |
| `[algorithm] enabled` | 默认 `true`。安装器已提供 bridge，无需填写路径；需要仅采集原始数据或维护时才改为 `false`。 |

配置语法和权限可先由服务账户验证：

```bash
sudo -u neurobridge /opt/neurobridge/venv/bin/python -c 'from neurobridge.config import load; load("/etc/neurobridge/gateway.toml"); print("gateway.toml OK")'
```

若采用 DHCP 模式，还要填写 `interface`、`subnet_cidr`、`dhcp_range_start`、`dhcp_range_end` 与 `dhcp_lease_time`。网关本身仍必须在该网卡使用 `[server].host` 作为静态地址；DHCP 只为 B 端分配地址，不提供 DNS，也不会让端口动态化。

算法默认开启：安装器会使用锁定的 SDK 和依赖自动构建 bridge，并安装到 `/usr/local/lib/neurobridge/neurobridge_affective_bridge`；网关在未配置 `algorithm.command` 时自动使用该路径。受控的真实数据 POC 可临时将 `[algorithm].enabled = false`，以录制只含原始数据的基线；算法结果的字段语义和有效性仍须以实际数据验证。详情见 [算法 SDK 接入 POC](../doc/tech/%E7%AE%97%E6%B3%95%20SDK%20%E6%8E%A5%E5%85%A5%20POC.md)。

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

从 B 端主机使用已确认的 WebSocket 地址连接，并在握手中提供子协议 `neurobridge.v1`。连接后先调用 `getStatus`，再调用 `getLatest` 或 `subscribe`；断线重连后必须重新订阅，不能复用旧 `subscriptionId`。可用仓库的 [B 端联调网页](../web/b-client-test/README.md) 做人工验证。

首次无头环冒烟验证可保持 `[ble].enabled = false`，此时 `getStatus` 应显示未连接；只要 `[recording].directory` 下存在非空历史会话，离线 `getLatest` 或 `subscribe` 就会自动选择最新会话并产生 `mode="replay"` 数据。若填写有效的 `replay_recording_id`，则该会话优先。实时采集验收还应覆盖头环连接、数据到达、断线重连和服务重启后的恢复。

如果 `[download].enabled = true`，可从专用有线网络的 B 端访问 `http://<网关IP>:<下载端口><下载路径>` 查看已结束会话的下载索引。该接口没有 TLS 或应用层认证，绝不能暴露到公共或不受控网络。

## 7. 静态地址与 DHCP 模式

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
./linux/update-ubuntu.sh
```

也可以由其他受控流程将新的源码工作树放到目标机，然后直接运行 `./linux/update-ubuntu.sh`。该脚本适用于 Python、安装器和 bridge 的全部代码更新：它会保留 `/etc/neurobridge/gateway.toml`、`/var/lib/neurobridge/recordings` 与现有 Python 虚拟环境，并从当前源码的 `third_party/` 本地重建 bridge。缺少 Ubuntu 基础镜像依赖或既有虚拟环境时，它会失败而不会尝试联网修复。

完整安装会保留已有的 `/etc/neurobridge/gateway.toml`，但仍应在执行前按现场运维要求备份该文件和 `/var/lib/neurobridge/recordings`。不要将录播数据、日志或现场配置提交到 Git。

## 9. 常见问题排查

| 现象 | 检查与处理 |
| --- | --- |
| 服务启动失败或不断重启 | 运行 `sudo journalctl -u neurobridge.service -n 100 --no-pager`。优先检查 TOML 语法、`server.host` 是否已配置在网卡上、端口是否被占用，以及 `/etc/neurobridge/gateway.toml` 是否可由 `neurobridge` 读取。 |
| 日志出现无法绑定地址 | 用 `ip -br addr` 检查专用网卡；`[server].host` 必须是该主机实际拥有的私有或回环 IP，不能填写 DNS 名称、通配地址或 B 端地址。 |
| B 端无法建立 WebSocket | 确认网线、两端 IP/掩码、专用网卡防火墙、端口和 `path`；握手必须提供 `neurobridge.v1` 子协议。连接恢复后先 `getStatus`，再重新订阅。 |
| `neurobridge-dhcp.service` 显示未运行 | 在 `static` 模式下这是预期行为：其 `ExecCondition` 会跳过 DHCP 服务。只有配置为 `dhcp` 并填写完整 DHCP 参数后才应运行。 |
| 未收到实时数据 | 确认 `[ble].enabled = true`、蓝牙适配器可见、设备名称/UUID 与已确认 profile 一致。通过网关日志查看扫描或连接失败原因；不要仅凭服务进程存活判断头环已连接。 |
| 离线时没有录播数据 | 检查 `[recording].directory` 是否指向存放历史会话的目录，并确认其中至少有一个非空会话；网关会自动选择最新会话。若填写了 `replay_recording_id`，该会话无效时会回退到自动选择。 |
| 下载接口不可用 | 确认 `[download].enabled = true`、监听 IP/端口与防火墙配置一致；只有已结束的录制会话能导出。 |

如果 `update-ubuntu.sh` 在编译 bridge 时失败，先不要反复修改依赖或安装额外库。运行下面命令并将生成的压缩包上传给开发人员；包内只有 CMake/build 日志、系统与工具版本、当前源码 revision 和最近的服务日志，不包含 `gateway.toml`、录播或原始 BLE 数据：

```bash
sudo ./linux/collect-ubuntu-build-diagnostics.sh
```

使用 `sudo` 运行时，生成的压缩包会归发起该命令的登录用户所有，权限为仅该用户可读写（`0600`），可直接上传或复制；不会归 root 锁定。

部署或修改设备接入、算法、网络依赖后，至少重新验证实时采集、录播、头环断线重连、B 端不可达与恢复五个场景。所有日志、配置和录播数据均应仅保留在受控环境中。
