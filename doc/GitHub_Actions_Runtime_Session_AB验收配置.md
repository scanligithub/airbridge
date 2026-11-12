# GitHub Actions：Runtime Session A/B 实机验收

## 两个 Workflow

- **runtime-session-a-acceptance.yml**：只在主机 A 的 Windows self-hosted runner 上运行。
- **runtime-session-b-acceptance.yml**：只在主机 B 的 Windows self-hosted runner 上运行。

每个 Workflow 都会：

1. 检出本次触发的 Git commit；
2. 运行 test_runtime_session_protocol.py；
3. 访问本机 127.0.0.1:8888 的 Runtime API；
4. 主动发送一次 HELLO 协商请求；
5. 等待本机 AirBridge 确认对端已协商 runtime-session.v1，且具备 handshake 功能；
6. 验证当前 P1.2 没有错误地宣称已支持 Session 创建。

两个 Workflow 通过同一次 feature 分支 push 并行触发。它们分别验证 A、B 两侧的本地状态，不会模拟或伪造串口链路结果。

## 前提：在 A、B 两台 Windows 主机分别注册 Runner

GitHub 云端 runner 无法访问你的本机 COM 串口、Windows AirBridge 进程或 Capture 桌面环境，因此实机验收必须使用 self-hosted runner。

在 GitHub 仓库：

1. 打开 Settings → Actions → Runners → New self-hosted runner；
2. 选择 Windows / x64；
3. 按 GitHub 当前页面给出的命令，在主机 A 注册 runner，并添加自定义标签 airbridge-a；
4. 在主机 B 重复注册，添加自定义标签 airbridge-b；
5. 确认两个 Runner 在仓库 Runner 列表中均显示 Idle / 在线。

注册命令由 GitHub 页面现场生成，包含短时有效 token。不要把 token 发到聊天、写入仓库或提交到日志。

标准标签 self-hosted、windows、x64 由 Runner 自动提供；本仓库 Workflow 额外要求 airbridge-a 或 airbridge-b 标签。

## 每次验收前

- A、B 两台机器都运行同一提交版本的 AirBridge；
- 两端串口链路已连接，AirBridge 本地 HTTP API 监听 127.0.0.1:8888；
- A/B 端各自的 API /api/v1/runtime/capabilities 可从本机访问；
- Python 3 可在 Runner 账号的 PATH 中通过 python 命令运行；
- Runner 在线，且分别带有正确的自定义标签。

Workflow 不会替用户启动 AirBridge、连接串口、启动 Capture 或关闭现有 Cadence 进程。

## 结果解释

- PASS：该主机本地 AirBridge 已通过协议单元测试，并确认其对端完成 Runtime Session v1 握手。
- queued：GitHub 尚未找到匹配标签且在线的 Runner，或 Runner 暂时忙碌；这不是测试 PASS。
- API 不可达：AirBridge 尚未运行，或本机 API 未监听指定地址/端口。
- unconfirmed：未得到对端 CAPABILITIES，可能是对端未升级、串口未连通或链路没有响应；不能直接断言是版本不兼容。
- no_common_protocol / handshake_not_supported：对端明确未声明双方需要的协议/握手能力。

本验收只覆盖 P1.2 的能力协商，不代表 Session Create、Capture Tcl 执行、结果关联、进程监督或关闭流程已通过。

## 安全边界

- Runner 使用仓库级标签定向，不接受通用 windows-latest 替代实机验收。
- Workflow 仅授予 contents: read。
- 不使用 GitHub Secrets 保存 Runner 注册 token；token 仅用于首次注册。
- 不在 Workflow 中执行任意远程 Shell 命令。
- 不把 COM 端口、Cadence 工程或设计文件上传为 Action artifact。
