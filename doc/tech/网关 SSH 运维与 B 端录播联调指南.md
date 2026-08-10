# 网关 SSH 运维与 B 端录播联调指南

本文是面向部署和联调人员的内部操作手册，适用于 Ubuntu 24.04 x86_64 网关与一台 B 端主机通过专用有线网络直连的场景。

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

一键 SSH 配置还会创建 `/srv/neurobridge-release`。该目录只允许 root 写入，用于暂存经过审核、完整的下一版本源码；它不是录播数据目录，也不是 B 端上传目录。

若网关仅部署 `master`，确认该分支已经包含上述文件；否则应先合入对应的 SSH 运维变更，再部署到现场。部署后使用以下命令确认文件存在：

```bash
test -x linux/setup-ssh-operations.sh
test -x linux/configure-ssh-operations.sh
```

首次准备环境时必须完成：

```bash
sudo ./linux/prepare-ubuntu24.04-environment.sh
```

该命令在可访问受控软件源时安装 `openssh-server`，但会保持 `ssh.service` 停用。现场已经隔离、只剩网关与 B 端直连时，不能再依赖该直连网络临时安装缺失的 OpenSSH 包；应在隔离前完成准备，或按现场受控的软件交付流程补齐系统包。

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

## 4. 首次一键启用 SSH 运维

首次 SSH 尚未启动时，必须给网关连接一次本地显示器和键盘，完成引导配置。不要为初次设置密码开放临时的明文远程终端。

### 4.1 在网关本地控制台运行一键配置

```bash
sudo ./linux/setup-ssh-operations.sh
```

按现场值回答提示。直连示例：

```text
运维账户：neuroops
运维账户密码（至少 12 位）：<隐藏输入>
再次输入运维账户密码：<隐藏输入>
网关私有监听 IP：192.168.88.10
允许的运维主机 IP/CIDR：192.168.88.20/32
SSH 端口：22
确认：YES
```

脚本以隐藏方式读取并二次确认密码，要求至少 12 位；密码不会写入参数、配置、日志或命令历史。脚本会验证 IP 已配置在本机网卡、来源与监听地址均为私有 IPv4 地址，并写入受控 SSH 配置。它只监听指定 IP，关闭 root 登录、公钥登录、X11/代理/端口转发和隧道；来源不在允许范围内的连接不能获得 shell 或执行命令。

如果需要接入自动化部署，可使用非交互入口。密码必须由受控的标准输入提供，不能出现在参数或版本库中：

```bash
printf '%s\\n' '<至少12位的密码>' | sudo ./linux/configure-ssh-operations.sh \
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

## 5. 从 B 端主机进行运维

```bash
ssh neuroops@192.168.88.10
```

登录后使用 `neurobridge-ops`：

```bash
neurobridge-ops status             # 服务状态和最近 200 条日志
neurobridge-ops logs --lines 500   # 查询历史日志，最多 1000 条
neurobridge-ops logs --follow      # 实时追踪运行日志；Ctrl-C 停止
neurobridge-ops audit --lines 500  # 查询 SSH 运维操作审计，最多 1000 条
watch -n 2 neurobridge-ops status  # 每 2 秒刷新服务状态
neurobridge-ops update             # 应用已暂存的审核版本并重新加载
neurobridge-ops restart            # 重启网关服务
neurobridge-ops stop
neurobridge-ops start
```

运维账户只有上述状态、日志和 `neurobridge.service` 启停权限，不能编辑 `/etc/neurobridge/gateway.toml`、安装软件或获得任意 root shell。配置变更、软件升级和防火墙调整应由现场独立系统管理员在本地控制台或已批准的高权限流程中执行。

每次 `status`、`logs`、`audit`、`update`、`start`、`stop` 或 `restart` 都会写入系统日志标识 `neurobridge-ops-audit`。日志包含系统时间、`sudo` 确认的运维账户、受限动作、参数摘要和开始/成功/失败结果；`logs --follow` 是持续命令，只记录开始。不记录账号密码、SSH 会话内容、网关配置或业务原始数据。用 `neurobridge-ops audit` 查询这份审计日志；SSH 登录来源仍以系统 `sshd` 日志为准。

`restart` 或 `stop` 会中断 B 端 WebSocket。服务恢复后，B 端必须重新连接，先调用 `getStatus`，再重新订阅；旧 `subscriptionId` 不可复用。

## 5.1 一键更新代码并重新加载

`neurobridge-ops update` 不执行 `git pull`、不访问公网，也不接受 SSH 用户传入的源码路径。它只应用管理员预置在 `/srv/neurobridge-release` 的完整审核版本，然后调用该版本的 `linux/reload-ubuntu.sh` 重新加载网关。

对于已经启用旧版 SSH 运维的网关，发布管理员先通过完整部署流程安装包含本功能的新版本，再在网关本地控制台重新运行一次 `sudo ./linux/setup-ssh-operations.sh`。该操作会更新 root-owned helper 和受限 sudo 规则；完成后 SSH 运维人员才可使用 `neurobridge-ops update`。

发布管理员在网关本地控制台或批准的高权限交付流程中预置版本，目录必须满足以下要求：

- 包含 `pyproject.toml`、`requirements.lock`、`linux/reload-ubuntu.sh` 和 `linux/install-ubuntu.sh`；
- 所有文件和目录归 root 所有，且不得对组或其他用户开放写权限；
- 不包含符号链接；
- 依赖版本和安装流程未变化。若 `reload-ubuntu.sh` 检测到依赖或安装流程变化，会拒绝热更新，要求管理员执行完整安装。

预置完成后，SSH 运维人员只需运行：

```bash
neurobridge-ops update
```

该命令会同步已审核源码、更新 Python 包、重新加载 systemd 单元并重启网关。B 端 WebSocket 会断开，恢复后必须重新连接、调用 `getStatus` 并重新订阅。更新失败时，执行 `neurobridge-ops logs --lines 500`，并由发布管理员检查暂存版本和完整安装要求。

## 6. B 端录播联调步骤

1. 在 B 端主机直接打开 `web/b-client-test/index.html`，或按现场方式托管联调页。
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

| 现象 | 排查与处理 |
| --- | --- |
| SSH 连接被拒绝 | 在网关本地控制台运行 `sudo systemctl status ssh.service`；确认监听 IP、端口和网线连通。 |
| SSH 显示 `Permission denied (password)` | 确认使用 `neuroops` 账户并输入配置时设置的密码；确认来源 IP 与 `--allow-from` 一致。密码遗失时只能在网关本地控制台重新运行一键配置重置。 |
| 需要确认是否执行过重启或更新 | 执行 `neurobridge-ops audit --lines 500`；以 `action=restart` 或 `action=update` 的 `result=success`/`failed:<退出码>` 判断结果。 |
| 一键脚本拒绝已有监听地址或端口 | 这是防止意外暴露 SSH 的保护。回到本地控制台检查已有 `/etc/ssh/sshd_config` 与片段，不要直接覆盖。 |
| B 端连接成功但没有录播数据 | 确认 `[ble].enabled = false`、录制目录有非空已完成会话，并执行 `neurobridge-ops logs --lines 500` 查看原因。 |
| 重启后 B 端没有继续收数据 | B 端需要重新建立 WebSocket，调用 `getStatus`，再重新 `subscribe`。 |
| `neurobridge-ops update` 提示暂存版本不完整或权限不安全 | 由发布管理员在 `/srv/neurobridge-release` 重新放置完整、root 所有且不可被组或其他用户写入的审核版本；SSH 运维账户不能绕过此限制。 |

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
