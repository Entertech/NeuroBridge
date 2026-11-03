# Linux 共享构建内核

`build-algorithm-bridge.sh` 只执行锁定 SDK 的 CMake 构建、Eigen 版本检查、日志与输出安装。不选择发行版，不导入麒麟打包工具，也不管理设备、网络、服务或 SSH。

调用方向固定为 `linux/kylin/` 或 `linux/ubuntu/` → `linux/common/`。各平台入口先检查宿主和工具链，再传入 `NEUROBRIDGE_BRIDGE_PLATFORM` 与 `NEUROBRIDGE_EIGEN_LOCK_KEY`；部署脚本不得直接调用共享内核。SDK/CMake 工程路径、版本与依赖锁保持既有约定。
