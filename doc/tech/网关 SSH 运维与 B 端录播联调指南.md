# 网关 SSH 运维与 B 端录播联调指南

本文是面向部署和联调人员的内部操作手册，适用于 Ubuntu 24.04 x86_64 网关与一台 B 端主机通过专用有线网络直连的场景。B 端主机大概率采用麒麟国产操作系统；其具体产品、版本和 CPU 架构仍须在进场前确认。

SSH 只用于网关运维；B 端取数、订阅和录播控制仍使用既有 WebSocket 北向协议。本文不修改 B 端协议，也不要求网关实现任何自定义加密、证书或 Token 逻辑。账号密码认证和 SSH 传输保护均由系统 OpenSSH 提供。

## 1. 场景与边界

现场只有两台主机：网关和 B 端主机。B 端主机既可以打开联调页，也可以作为唯一的 SSH 运维终端。

```text
网关 192.168.88.10  ───── 专用网线 ─────  B 端主机 192.168.88.20
  WebSocket：8765                           浏览器联调页
  SSH：22                                   SSH 运维终端
```

上图的 IP、端口和 WebSocket 路径均是示例，必须替换为双方确认的现场配置。该网口不得接入公网、无线网络或不受控局域网。

头环未连接时，网关在 B 端首次调用 `getLatest` 或 `subscribe` 后自动进入录播，并在数据中标记 `mode="replay"`。当前实现中，`replay_recording_id` 可留空：网关会从录制目录选择最新的非空历史会话；填写一个存在且非空的会话 ID 时，填写值优先。

## 2. 部署前检查

SSH 运维入口由下列文件提供：

- `linux/setup-ssh-operations.sh`：交互式一键配置入口；
- `linux/configure-ssh-operations.sh`：可自动化调用的严格配置器；
- 运行时命令：`neurobridge-ops`。

一键 SSH 配置会把执行配置时的完整源码同步到 SSH 运维账号固定的 `~/NeuroBridge` 项目目录。SSH 登录后可通过 `neurobridge-ops project` 查看实际绝对路径，后续代码更新、部署、状态和日志操作都以该目录为准，不再创建单独的版本暂存目录。

若网关仅部署 `master`，确认该分支已经包含上述文件；否则应先合入对应的 SSH 运维变更，再部署到现场。部署后使用以下命令确认文件存在：

```bash
test -x linux/setup-ssh-operations.sh
test -x linux/configure-ssh-operations.sh
```

首次准备环境时必须完成：

```bash
sudo ./linux/prepare-ubuntu24.04-environment.sh
```

该命令在可访问受控软件源时安装 `openssh-server`，但会保持 `ssh.socket` 和 `ssh.service` 停用。Ubuntu 24.04 安装 OpenSSH 后可能自动启用 socket 激活；环境准备脚本会一并关闭该通配监听，避免它提前占用 22 端口。现场已经隔离、只剩网关与 B 端直连时，不能再依赖该直连网络临时安装缺失的 OpenSSH 包；应在隔离前完成准备，或按现场受控的软件交付流程补齐系统包。

### 2.1 麒麟 B 端主机准备

麒麟系统只承担 B 端 WebSocket 客户端、浏览器联调页和 SSH 运维终端，不运行 NeuroBridge 网关服务。B 端是 x86_64 还是 ARM64 不改变北向协议和 SSH 命令，也不改变网关继续使用 Ubuntu 24.04 x86_64 的部署边界。

进场前在 B 端执行以下只读检查，并将输出中的系统产品、版本、CPU 架构和网卡名写入现场记录：

```bash
cat /etc/os-release
uname -m
ip -br link
command -v ssh && ssh -V
command -v python3 || true
```

至少需要：

- OpenSSH 客户端，用于账号密码登录网关；
- 支持 WebSocket 的现代浏览器，例如麒麟系统实际提供的 Chromium、Chrome 或 Firefox 兼容浏览器；
- 一块可配置静态 IPv4 地址的有线网卡；
- 可选的 Python 3，仅在浏览器不允许直接打开本地联调页时用于启动只监听本机的静态文件服务。

若缺少 SSH 客户端，必须在接入隔离网络前通过现场批准的软件源安装。不同麒麟产品可能使用 `apt`、`dnf` 或 `yum`，对应包名也可能是 `openssh-client` 或 `openssh-clients`；应先依据 `/etc/os-release` 和现场软件管理规范确认，不能在隔离网络中临时访问公网安装。

建议优先通过麒麟桌面的网络设置界面，为接入网关的专用有线网卡配置现场确认的静态地址，例如 `192.168.88.20/24`，不填写公网网关和 DNS。若该版本由 NetworkManager 管理，也可由系统管理员在确认连接名称后配置：

```bash
nmcli connection show
sudo nmcli connection modify '<专用有线连接名称>' \
  ipv4.method manual \
  ipv4.addresses 192.168.88.20/24 \
  ipv4.gateway '' \
  ipv4.dns '' \
  ipv4.never-default yes
sudo nmcli connection up '<专用有线连接名称>'
```

不要照抄连接名称，也不要修改承载其他业务的网卡。配置后检查地址和路由，确认该专用网口没有获得默认路由：

```bash
ip -br addr
ip route
ping -c 4 192.168.88.10
```

系统防火墙或终端安全软件如限制出站连接，应按现场审批仅允许 B 端访问网关确认后的 SSH 和 WebSocket 端口；不要直接关闭整机防火墙。

当前结论仅为源码和文档层面的兼容性说明，不能写成“麒麟已完成 POC”或“现场验收通过”。正式交付前仍需在实际麒麟版本和 CPU 架构上完成本文第 6 节的联调验收。

## 3. 配置网关网络与录播

先在网关本地控制台配置接入 B 端的网卡静态 IP。不要用安装脚本覆盖现有 Netplan 文件；只修改现场对应的网卡配置。示例地址为：

```text
网关：192.168.88.10/24
B 端：192.168.88.20/24
```

在 B 端主机验证连通性：

```bash
ping 192.168.88.10
```

编辑网关部署配置：

```bash
sudoedit /etc/neurobridge/gateway.toml
```

录播联调的最小相关配置如下：

```toml
[server]
host = "192.168.88.10"
port = 8765
path = "/neurobridge/v1/ws"

[ble]
enabled = false

[recording]
directory = "/var/lib/neurobridge/recordings"
replay_recording_id = ""
replay_speed = 1.0
```

确认录制目录存在已完成且非空的会话，然后启动网关：

```bash
sudo systemctl restart neurobridge.service
sudo systemctl --no-pager --full status neurobridge.service
```

### 3.1 临时局域网验证与最终网线直连

临时在同一受控局域网验证 SSH 时，监听地址填写网关当前网卡 IP；允许来源优先填写当前运维电脑的实际 IP 加 `/32`。`/32` 只表示“允许这一台来源主机”，与当前使用交换机、路由器还是单根网线无关。该方式仅用于进场前短时验证，不改变正式部署必须使用专用有线直连的边界。例如：

```text
网关当前 IP：192.168.88.10
运维电脑 IP：192.168.88.20
SSH 监听地址：192.168.88.10
允许来源：192.168.88.20/32
```

如果运维电脑通过 DHCP 获取地址，地址变化后会被来源限制拒绝。临时验证期间应固定地址、配置 DHCP 保留，或在每次地址变化后从网关本地控制台重新运行一键配置。不建议为了省事允许整个普通局域网；如现场明确需要允许一个网段，必须填写规范网络地址，例如：

```text
192.168.88.0/24    # 192.168.88.x 网段
192.168.88.0/23    # 192.168.88.x～192.168.89.x 网段
```

最终改为网关与 B 端单根网线直连时，两端应改用现场确认的静态 IP。由于网关监听 IP 和 B 端来源 IP 都发生变化，必须在网关本地控制台重新运行 SSH 一键配置，不能继续沿用临时局域网配置。切换顺序为：配置两端静态 IP → `ping` 验证 → 重新执行快速或完整配置 → 按第 4.3 节验收。

## 4. 首次一键启用 SSH 运维

首次 SSH 尚未启动时，必须给网关连接一次本地显示器和键盘，完成引导配置。不要为初次设置密码开放临时的明文远程终端。

### 4.1 推荐：快速一键配置

常规单网卡、单 B 端、默认账号和端口场景，在网关本地控制台执行：

```bash
sudo ./linux/setup-ssh-operations.sh --quick 192.168.88.20
```

命令中的 IP 是唯一允许登录的 B 端或临时运维电脑 IP。也可以省略 IP，由脚本单独询问：

```bash
sudo ./linux/setup-ssh-operations.sh --quick
```

快速模式自动使用：

- 运维账户 `neuroops`；
- SSH 端口 `22`；
- 当前设备检测到的网关私有 IPv4 作为监听地址；
- B 端 IP `/32` 作为唯一允许来源。

现场只需确认脚本显示的自动参数，隐藏输入并二次确认至少 6 位数字密码，最后输入任意大小写组合的 `YES`。密码仍不会进入命令参数、文件或日志。若未检测到网关私有 IP，快速模式会停止并提示使用完整模式；若检测到的 IP 不是目标网卡，也应取消并改用完整模式，不能让脚本猜测网卡。

### 4.2 完整交互配置（保留）

需要自定义账号、监听 IP、来源网段或 SSH 端口时，继续使用原有无参数命令：

```bash
sudo ./linux/setup-ssh-operations.sh
```

按现场值回答提示。直连示例：

```text
运维账户：neuroops
运维账户密码（至少 6 位数字）：<隐藏输入>
再次输入运维账户密码：<隐藏输入>
网关私有监听 IP：192.168.88.10
允许的运维主机 IP/CIDR：192.168.88.20/32
SSH 端口：22
确认：YES
```

脚本以隐藏方式读取并二次确认密码，要求至少 6 位纯数字；密码不会写入参数、配置、日志或命令历史。配置器仅为该专用运维账户使用系统 `chpasswd` 支持的 SHA512 加盐哈希写入方式，从而不经过 PAM 的最短 8 位检查；不会修改整机 PAM 密码策略，其他账户不受影响。最终输入 `YES` 确认时不区分大小写。数字密码正式部署只允许用于本文限定的专用网线直连和固定来源 IP 场景；临时局域网验证必须限制为受控网络和单机 `/32` 来源，并在验证后切换到直连配置。禁止将 SSH 接入公网、无线网或不受控局域网。脚本会验证 IP 已配置在本机网卡、来源与监听地址均为私有 IPv4 地址，并在写入 sshd 前规范化来源：单台 B 端填写 `192.168.88.20/32`，整个网段必须使用其网络地址；例如 `192.168.88.20/23` 会规范化为 `192.168.88.0/23`。它只监听指定 IP，关闭 root 登录、公钥登录、X11/代理/端口转发和隧道；来源不在允许范围内的连接不能获得 shell 或执行命令。

Ubuntu 24.04 可能同时提供 `ssh.socket` 和 `ssh.service`。配置器会在 SSH 配置通过语法和监听检查后，停用可能占用 22 端口或绕过指定监听 IP 的 `ssh.socket`，停止 `ssh.service` 控制组内可能遗留的旧 sshd 监听进程，确认端口已经释放，再启用地址受限的 `ssh.service`。清理控制组会断开已有 SSH 会话；若远程执行时发现残留监听，脚本会拒绝强制清理并要求改用网关本地控制台。配置失败时会恢复执行前的 SSH 配置以及 service/socket 启用和运行状态，不需要手工清理半成品。

“网关私有监听 IP”会默认显示当前设备已配置的全局私有 IPv4：通过已有 SSH 会话配置时优先选择该会话连接到的网关本地 IP，否则选择检测到的第一个非回环、非链路本地私有地址；没有符合条件的地址时不显示默认值，必须手工输入。直接回车表示采用方括号中的默认地址。

如果需要接入自动化部署，可使用非交互入口。密码必须由受控的标准输入提供，不能出现在参数或版本库中：

```bash
printf '%s\n' '<至少6位数字密码>' | sudo ./linux/configure-ssh-operations.sh \
  --operator-user neuroops \
  --operator-password-stdin \
  --listen-address 192.168.88.10 \
  --allow-from 192.168.88.20/32 \
  --port 22
```

配置后，在网关本地控制台验证：

```bash
sudo sshd -t
sudo systemctl --no-pager --full status ssh.service
```

### 4.3 配置完成后的 SSH 验收

先在网关本地控制台确认配置、账户和监听状态：

```bash
sudo sshd -t
sudo systemctl is-active ssh.service
sudo ss -ltnp 'sport = :22'
getent passwd neuroops
sudo passwd -S neuroops
```

预期 `ssh.service` 输出 `active`，22 端口只监听配置的网关 IP，`getent` 能找到 `neuroops`，`passwd -S` 的状态为 `P`。不要把密码写进上述命令。

然后在 B 端或临时运维电脑执行：

```bash
ssh -o PreferredAuthentications=password \
  -o PubkeyAuthentication=no \
  neuroops@192.168.88.10
```

必须显式写出 `neuroops@`；如果只运行 `ssh 192.168.88.10`，客户端会使用 B 端当前系统用户名，正确的运维密码也无法登录。登录后执行：

```bash
whoami
neurobridge-ops project
neurobridge-ops status
neurobridge-ops audit --lines 20
```

`whoami` 应输出 `neuroops`，其余命令应能读取项目目录、网关状态和运维审计记录。截至 2026-08-10，已根据现场反馈完成一次同一受控局域网内的账号密码 SSH 登录验证；该结论只覆盖 SSH 登录链路，不代表最终麒麟 B 端网线直连、WebSocket 录播或头环与算法链路已完成现场验收。

## 5. 从 B 端主机进行运维

```bash
ssh neuroops@192.168.88.10
```

登录后使用 `neurobridge-ops`：

```bash
neurobridge-ops status             # 服务状态和最近 200 条日志
neurobridge-ops project            # 显示固定项目目录
neurobridge-ops logs --lines 500   # 查询历史日志，最多 1000 条
neurobridge-ops logs --follow      # 实时追踪运行日志；Ctrl-C 停止
neurobridge-ops audit --lines 500  # 查询 SSH 运维操作审计，最多 1000 条
watch -n 2 neurobridge-ops status  # 每 2 秒刷新服务状态
neurobridge-ops update             # 应用已同步的完整版本并重新加载
neurobridge-ops restart            # 重启网关服务
neurobridge-ops stop
neurobridge-ops start
```

现场不再区分发布管理员和普通运维，配置的 SSH 账号就是唯一的受信任运维账号。登录后执行 `cd "$(neurobridge-ops project)"` 进入固定项目目录，在其中查看或更新代码，再触发安装、状态、日志和服务启停。因为该账号维护的脚本随后会以 root 安装，所以必须把它作为网关管理员凭据管理，不能共享密码或放宽允许来源 IP。

每次 `status`、`logs`、`audit`、`update`、`start`、`stop` 或 `restart` 都会写入系统日志标识 `neurobridge-ops-audit`。日志包含系统时间、`sudo` 确认的运维账户、受限动作、参数摘要和开始/成功/失败结果；`logs --follow` 是持续命令，只记录开始。不记录账号密码、SSH 会话内容、网关配置或业务原始数据。用 `neurobridge-ops audit` 查询这份审计日志；SSH 登录来源仍以系统 `sshd` 日志为准。

`restart` 或 `stop` 会中断 B 端 WebSocket。服务恢复后，B 端必须重新连接，先调用 `getStatus`，再重新订阅；旧 `subscriptionId` 不可复用。

## 5.1 一键更新代码并重新加载

`neurobridge-ops update` 不接收任意源码路径，只部署 `neurobridge-ops project` 显示的固定项目工作目录，然后调用其中的 `linux/reload-ubuntu.sh` 重新加载网关。

网关能够访问批准的代码源时，SSH 登录后直接在项目目录操作：

```bash
cd "$(neurobridge-ops project)"
git status --short --branch
git pull --ff-only
neurobridge-ops update
```

网关处于隔离网络时，在 B 端麒麟主机进入完整的新版本源码目录，先同步到同一个项目目录，再登录操作：

```bash
rsync -a --delete \
  --exclude '.git/' --exclude '.venv/' --exclude '__pycache__/' --exclude 'build/' \
  ./ neuroops@192.168.88.10:/home/neuroops/NeuroBridge/
ssh neuroops@192.168.88.10
cd "$(neurobridge-ops project)"
neurobridge-ops update
```

如 B 端没有 `rsync`，应在接入隔离网络前按实际麒麟版本和批准的软件源安装。项目目录必须满足：

- 包含 `pyproject.toml`、`requirements.lock`、`linux/reload-ubuntu.sh` 和 `linux/install-ubuntu.sh`；
- 所有文件和目录归当前 SSH 运维账号所有，且不得对组或其他用户开放写权限；
- 不包含符号链接；
- 依赖版本和安装流程未变化。若 `reload-ubuntu.sh` 检测到依赖或安装流程变化，会拒绝热更新，要求在网关本地执行完整安装。

已经启用旧版 SSH 运维的网关升级到本方案后，需要从当前完整源码目录在网关本地重新运行一次 `sudo ./linux/setup-ssh-operations.sh`，脚本会初始化该账号的 `~/NeuroBridge`；之后只维护这个项目目录。

版本同步完成后运行：

```bash
neurobridge-ops update
```

该命令会安装已同步源码、更新 Python 包、重新加载 systemd 单元并重启网关。B 端 WebSocket 会断开，恢复后必须重新连接、调用 `getStatus` 并重新订阅。更新失败时，同一个运维账号执行 `neurobridge-ops logs --lines 500`，检查暂存版本和完整安装要求。

## 6. B 端录播联调步骤

1. 在 B 端主机直接打开 `web/b-client-test/index.html`。若麒麟浏览器限制 `file://` 本地页面，且系统已有 Python 3，则在源码根目录执行以下命令，只在 B 端本机托管联调页，然后访问 `http://127.0.0.1:8080/`：

   ```bash
   python3 -m http.server 8080 --bind 127.0.0.1 --directory web/b-client-test
   ```

2. 填入确认后的 WebSocket 地址，例如 `ws://192.168.88.10:8765/neurobridge/v1/ws`。
3. 建立连接并调用 `getStatus`，确认头环未连接。
4. 调用 `getLatest` 或 `subscribe`。
5. 确认返回数据中的 `mode` 为 `replay`。
6. 在 B 端 SSH 终端并行执行：

   ```bash
   neurobridge-ops logs --follow
   ```

   观察服务状态、请求处理、录播启动和异常原因。运行日志不会记录完整敏感原始数据。

## 7. 常见问题

### 7.1 推荐排查顺序

遇到 SSH 登录或一键配置失败时，按以下顺序排查，不要反复猜测或回显密码：

1. 在 B 端执行 `ip -br addr`，确认实际来源 IP；再 `ping` 网关监听 IP。
2. 在网关本地执行 `ip -br addr`，确认一键配置使用的监听 IP 仍在网卡上。
3. 执行 `sudo systemctl status ssh.service --no-pager -l`，确认服务是否启动。
4. 执行 `sudo journalctl -u ssh.service -b -n 80 --no-pager`，查找第一条具体的 sshd 错误。
5. 执行 `sudo ss -ltnp 'sport = :22'`，确认端口占用进程和监听地址。
6. 执行 `getent passwd neuroops` 和 `sudo passwd -S neuroops`，确认账户存在且密码状态为 `P`。
7. 从 B 端使用 `ssh -vvv neuroops@<网关IP>` 获取客户端协商信息；对外提供日志时删除不相关主机信息，且绝不能提供密码。

### 7.2 本次联调问题复盘

| 现象或关键日志 | 根因 | 当前处理方式 |
| --- | --- | --- |
| `Failed password for invalid user <B端系统用户名>` | 登录命令没有写 `neuroops@`，SSH 默认使用 B 端当前用户名 | 使用 `ssh neuroops@<网关IP>`；密码是否正确不能弥补用户名错误 |
| `Invalid user neuroops` | 一键配置在创建账户前失败并回滚，或仍在运行旧版本脚本 | 先解决更早的配置/服务错误，再用最新源码从网关本地重新运行；用 `getent passwd neuroops` 验证 |
| `Invalid Match address argument` | 旧脚本把主机 IP 与非 `/32` 掩码原样写入 sshd，例如把某台主机地址直接配成 `/23` | 新脚本会自动规范化；单台 B 端优先使用实际 IP `/32`，整个网段使用规范网络地址 |
| 输出 `Removed ... ssh.socket` | 正在关闭 Ubuntu 24.04 默认 socket 激活，属于预期切换步骤 | 继续查看后续输出；这行本身不是失败原因 |
| `Found left-over process ... sshd`、`Address already in use`、`Cannot bind any address` | `ssh.socket` 已关闭，但此前启动的 sshd 监听进程仍留在 `ssh.service` 控制组并占用 22 端口 | 最新脚本会从本地控制台清理该控制组、确认端口释放后再启动一次地址受限服务 |
| `无效密码：少于 8 个字符` | 旧脚本通过 PAM 更新密码，与“至少 6 位数字”的运维规则冲突 | 最新脚本仅为专用运维账户使用 `chpasswd` SHA512 加盐哈希写入，不修改全局 PAM 策略 |
| 输入 `yes` 后被取消 | 旧交互脚本只接受全大写 `YES` | 最新脚本对 `YES` 的任意大小写组合均接受，其他输入仍取消 |
| 密码正确但仍 `Permission denied` | 可能是用户名错误、来源 IP 不匹配、账户未创建/被锁，并不一定是密码本身 | 同时核对登录命令、`--allow-from`、`getent passwd`、`passwd -S` 和 sshd 日志 |

旧版本遇到 sshd 残留监听时，只能在网关本地控制台按下列顺序临时恢复；这些命令会断开现有 SSH 会话：

```bash
sudo systemctl disable --now ssh.socket
sudo systemctl stop ssh.service
sudo systemctl kill --kill-who=all --signal=TERM ssh.service
sudo ss -ltnp 'sport = :22'
```

如果端口仍由 `ssh.service` 控制组内的旧 sshd 占用，再执行：

```bash
sudo systemctl kill --kill-who=all --signal=KILL ssh.service
sudo ss -ltnp 'sport = :22'
```

端口释放后应先更新到最新源码，再重新运行一键配置；不要用 `killall sshd`、`pkill sshd` 或不核对目标 PID 的命令。

### 7.3 其他常见问题

| 现象 | 排查与处理 |
| --- | --- |
| SSH 连接被拒绝 | 在网关本地控制台运行 `sudo systemctl status ssh.service`；确认监听 IP、端口和网线连通。 |
| 一键配置提示 `ssh.service` 启动失败 | 新版脚本会自动处理 Ubuntu 24.04 的 `ssh.socket` 以及旧 sshd 残留监听并回滚；确认使用最新项目代码后，在网关本地控制台重新执行。仍失败时运行 `sudo journalctl -u ssh.service -n 50 --no-pager` 和 `sudo ss -ltnp 'sport = :22'` 查看具体原因。 |
| sshd 提示 `Invalid Match address argument` | 使用新版脚本重新配置；脚本会把来源 IP/CIDR 规范化。单台 B 端优先填写实际地址加 `/32`。 |
| SSH 显示 `Permission denied (password)` | 确认使用 `neuroops` 账户并输入配置时设置的密码；确认来源 IP 与 `--allow-from` 一致。密码遗失时只能在网关本地控制台重新运行一键配置重置。 |
| 麒麟 B 端没有 `ssh` 命令 | 在接入隔离网络前，按实际麒麟版本和现场批准的软件源安装 OpenSSH 客户端；不要直接假定包管理器或包名。 |
| 麒麟浏览器无法直接打开联调页 | 确认已安装 Python 3，使用第 6 节的 `127.0.0.1:8080` 本地静态服务；不要绑定 B 端有线网卡地址。 |
| 麒麟 B 端能 ping 网关但 WebSocket 或 SSH 失败 | 检查系统防火墙、终端安全软件和端口策略，仅放行 B 端到网关确认后的 SSH/WebSocket 端口。 |
| 需要确认是否执行过重启或更新 | 执行 `neurobridge-ops audit --lines 500`；以 `action=restart` 或 `action=update` 的 `result=success`/`failed:<退出码>` 判断结果。 |
| 一键脚本拒绝已有监听地址或端口 | 这是防止意外暴露 SSH 的保护。回到本地控制台检查已有 `/etc/ssh/sshd_config` 与片段，不要直接覆盖。 |
| B 端连接成功但没有录播数据 | 确认 `[ble].enabled = false`、录制目录有非空已完成会话，并执行 `neurobridge-ops logs --lines 500` 查看原因。 |
| 重启后 B 端没有继续收数据 | B 端需要重新建立 WebSocket，调用 `getStatus`，再重新 `subscribe`。 |
| 不知道网关项目目录 | 执行 `neurobridge-ops project`，再运行 `cd "$(neurobridge-ops project)"`。 |
| `neurobridge-ops update` 提示项目不完整或权限不安全 | 在 `neurobridge-ops project` 显示的目录检查源码完整性、文件所有者、组/其他用户写权限和符号链接；隔离环境可从 B 端重新执行 `rsync -a --delete`。 |

## 8. 撤销 SSH 运维入口

只在网关本地控制台执行：

```bash
sudo rm /etc/ssh/sshd_config.d/00-neurobridge-operations.conf
sudo rm /etc/sudoers.d/neurobridge-operator
sudo rm /usr/local/sbin/neurobridge-ops-status
sudo rm /usr/local/sbin/neurobridge-ops-logs
sudo rm /usr/local/sbin/neurobridge-ops-update
sudo rm /usr/local/sbin/neurobridge-ops-command
sudo rm /usr/local/bin/neurobridge-ops
sudo systemctl restart ssh.service
```

是否进一步停用 SSH 服务，由现场运维策略决定。撤销 SSH 不会改动网关业务服务、录播文件或 B 端 WebSocket 协议。
