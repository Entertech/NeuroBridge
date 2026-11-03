# Linux 平台入口

| 目录 | 职责 | 使用说明 |
| --- | --- | --- |
| `kylin/` | 银河麒麟 V10 串口耳机，项目依赖、运行、自启、诊断和卸载 | [麒麟入口](kylin/README.md) |
| `ubuntu/` | Ubuntu 24.04 x86_64 蓝牙头环与专网 B 端，部署、SSH、systemd 和日志轮转 | [Ubuntu 入口](ubuntu/README.md) |
| `common/` | 两个平台复用的算法桥构建内核；不包含平台部署策略 | [共享边界](common/README.md) |

```text
linux/
├── kylin/
│   ├── lib/                 # 麒麟项目 systemd 渲染
│   └── *.sh                 # 麒麟环境、串口、运行和卸载
├── ubuntu/
│   ├── operations/          # Ubuntu SSH 运维命令
│   ├── systemd/             # Ubuntu 服务与有线网络单元
│   ├── logrotate/           # Ubuntu 系统日志轮转
│   └── *.sh                 # Ubuntu 准备、安装、更新和诊断
└── common/
    └── build-algorithm-bridge.sh
```

调用方向为平台入口 → 共享构建内核，两个平台互不调用。麒麟引导包不携带 Ubuntu 部署文件。README 与 CI 使用新路径；应用业务代码仍由共享 Bootstrap/Profile 绑定设备能力。

根目录保留少量仅执行 `exec bash` 的兼容入口，供已发布文档、已安装的 SSH 更新器与旧麒麟 systemd 单元使用：`neurobridge-kylin-bootstrap.sh`、`start-kylin-gateway.sh`、`install-ubuntu.sh`、`update-ubuntu.sh`、`reload-ubuntu.sh`、`refresh-operations-commands.sh`、`prepare-ubuntu24.04-environment.sh`、`setup-ssh-operations.sh`、`configure-ssh-operations.sh`。旧 `build-algorithm-bridge.sh` 只分派到对应平台；新代码不调用这些兼容入口。兼容入口不复制实现，旧路径不再承载部署逻辑。

所有命令从仓库根目录执行，现场数据和既有配置路径保持不变。目标机实测与容器/合成验证分别记录，不以目录重构推定现场验收通过。
