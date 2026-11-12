# GitHub Actions：Runtime Session 云端 A/B 软件验收

## 目标与运行环境

本项目不依赖用户提供 Windows 实体机，也不注册 self-hosted runner。CI 使用 GitHub 托管的 `windows-2025` 标准 Runner；每个 Job 由 GitHub 自动分配临时 Windows 虚拟机。

当前仓库为公开仓库，GitHub 官方文档列明公开仓库的标准 GitHub-hosted Runner 免费且不限标准 Runner 使用量。Runner 的标签和规格以 GitHub 官方文档为准：
https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/choose-the-runner-for-a-job

## CI 分层

工作流：`.github/workflows/runtime-session-ci.yml`

### 第一层：协议、A 端跟踪器与 B 端 Broker 单元测试

- 在 GitHub 托管 Windows 2025 虚拟机上运行；
- 执行 `test/test_runtime_session_protocol.py`、`test/test_request_tracker.py`、`test/test_request_broker.py` 和 `test/test_runtime_adapter.py`；
- 覆盖 JSON Envelope、UUID、schema、载荷上限、ABFP 帧编解码、A 端 waiter 状态 / TTL、B 端 SQLite 去重、相同 ID 不同 Payload 冲突、并发重复登记、Broker 重启恢复、心跳检测、容量限制和结果缓存裁剪；并验证 RuntimeAdapter / RuntimeProfile 注册、能力子集约束、只读 Request Payload、Profile 分派、禁用 / 未知 Profile 拒绝，以及 EDA / Shell 兼容适配器的参数与结果透传。

### 第二层：A/B 双端软件模拟

- 在另一台 GitHub 托管 Windows 2025 虚拟机上运行；
- 安装 PySide6 和 pyserial，以便导入 AirBridge 当前生产模块；
- 创建两个真实的 `SerialWorker` 对象，但不启动 GUI、线程或真实串口；
- 通过内存双向链路传输 `ABFPFrame.pack()` 产生的二进制帧，并由对端 `FrameParser.feed()` 解析，再调用生产代码的帧处理方法；
- 对 A、B 两端分别发起 HELLO，验证双向 CAPABILITIES 响应及 correlation_id 关联；
- 验证双方协商 `handshake`、`request.correlation` 与 `request.deduplication`，且 `profiles=[]`、`can_create_session=false`；
- 验证无关 correlation_id 的 CAPABILITIES 被忽略，链路重置会结束旧 waiter；
- 通过真实 ABFP 内存双向链路，将 EDA_TASK_SUBMIT / EDA_TASK_FIN 与 SHELL_EXEC / SHELL_RESULT 在两端 Worker 之间往返，验证 UUID request_id 被原样关联；
- 重放相同 EDA / Shell 帧，验证 B 端执行函数只被调用一次且返回缓存结果；相同 ID、不同 Payload 返回 `REQUEST_ID_CONFLICT`；
- 通过真实本地 HTTP Server 端口测试 EDA / Shell POST、pending 轮询、异步 Shell accepted、调用方指定 request_id、重复 POST 返回 409、缺少去重能力及串口断开时拒绝提交；
- 验证无效 Shell timeout 在发送任何 ABFP 帧之前被拒绝。

这层不是另写一套替代协议的模拟器；它驱动 `SerialWorker` 中的生产握手 / 响应处理方法，并启动实际 `AntigravityIPCServer` 进行 HTTP 测试。ABFP 帧仍经过真实打包和流式解析。

## 触发方式

- Push 到 `feature/runtime-session-p1-2` 且修改协议实现、握手实现、相关测试或该 Workflow 时自动运行；
- 配置了 `workflow_dispatch` 手动触发入口；按照 GitHub 规则，手动触发要求该 Workflow 文件存在于默认分支，因此本功能分支目前依靠 `push` 路径过滤器自动触发，合并到 `main` 后才可从 UI 手动运行；
- 不需要注册 Runner、不需要配置仓库 Secrets，也不需要用户提供 Windows 机器。

## 验收结果的边界

CI PASS 代表：协议、A 端 RequestTracker 和 B 端 SQLite RequestBroker 单元测试通过；生产 `SerialWorker` 的双端握手 / EDA / Shell 请求关联与重复帧去重通过内存 ABFP 链路模拟；A 端本地 HTTP EDA / Shell 请求接口通过集成测试。

CI PASS **不代表**以下内容已经实测：

- 两台独立 Windows 主机之间的真实 COM/USB/串口物理通信；
- 串口驱动、波特率、电气链路、硬件丢包和真实链路重连；
- Capture / Allegro 已安装、已启动或能够执行 Tcl / SKILL；
- 真实远端 Cadence / Shell 进程执行；真实 Capture 17.2 capAutoLoad 是否加载本轮生成的正式 Bridge、真实 Tcl / Dbo 命令执行与 GUI 消息循环行为；
- 真实 Capture.exe 的 WM_CLOSE、多个实例关闭、保存提示、无响应退出阻断和重启生命周期；P1.7 的这部分目前仅通过 FakeProcessBackend 状态机测试；
- Session Create / Close 专用 ABFP 消息、统一 SESSION_EXEC、每 Session FIFO Broker、完整 Skill 保存 / Exit 生命周期；
- Allegro 旧 SKILL 执行路径尚未接入 ProcessSupervisor，留待 P1.9。

由于当前没有可供本项目使用的 Windows 实体机，真实物理链路验收暂不纳入自动 CI；不得把软件模拟 PASS 写成物理 A/B 实测 PASS。后续如果获得实际硬件环境，可另行增加可选的硬件在环验收，而不影响云端 CI。

## 结果解释

- **Protocol unit tests PASS**：协议 Envelope 与 ABFP 帧契约通过；
- **A/B simulation PASS**：两个生产 SerialWorker 状态机在模拟链路上完成双向握手，关联及重置行为符合预期；P1.6 额外验证 EDA Tcl 请求选择 Capture Session Adapter、结果 PID / Tcl 元数据回传及 request_id 重复投递不重复执行；
- **Capture Bridge unit PASS**：Python Bridge 客户端、capAutoLoad Tcl 源码生成、请求/结果文件契约、READY nonce/PID 校验、错误与 UNKNOWN 状态映射通过模拟器测试；不等同 Capture 真实 Tcl Interpreter 实机测试；
- **CI FAIL**：查看对应 Job 日志，按失败测试定位；
- 新工作流不再依赖 `airbridge-a` / `airbridge-b` 自托管 Runner；此前旧工作流创建的 queued 运行记录可能仍留在 Actions 历史中，不属于新的云端 CI。

## 安全边界

- Workflow 权限仅为 `contents: read`；
- 不启动任意远程 Shell；
- 不访问用户主机、COM 端口或 Cadence 工程；
- 不上传设计文件或真实工程数据作为 Action artifact。

## P1.5 软件 CI 实测记录

- GitHub Actions Run：[#62](https://github.com/scanligithub/airbridge/actions/runs/43146376763)
- 测试提交：`322904a06d25aa522574a50dd885277913e30d73`
- Protocol / RequestTracker / Runtime Adapter / RequestBroker 单元测试：46 项通过；
- A/B 生产 SerialWorker 双端软件模拟：11 项通过；
- HTTP correlated request 集成：9 项通过；
- **合计 66 项通过，两个 Job 均成功。**

该结果是 GitHub 托管 Windows Runner 上的软件级验收。测试通过内存 ABFP 双向链路模拟两个 Worker，不使用物理 COM 端口，也未启动真实 Capture 或 Allegro。因此不能把此 CI 结果解释为双主机串口实测、Capture 常驻 Tcl Bridge 验收或专业软件 GUI 生命周期验收。

## P1.6 Capture Tcl Adapter 软件 CI 实测记录

- GitHub Actions Run：[#76](https://github.com/scanligithub/airbridge/actions/runs/43148279572)
- 测试提交：`9fc895c56c34f9f18c30fb573df8b7aa981485f9`
- Protocol：10 项；RequestTracker：11 项；Capture Tcl Bridge：12 项；Runtime Adapter：11 项；RequestBroker：15 项；
- 单元测试合计：59 项通过；
- A/B 生产 SerialWorker 软件模拟：12 项通过（包含 Capture Profile 路由、PID / Tcl 元数据回传、相同 request_id 去重）；
- HTTP correlated request 集成：9 项通过；
- Runtime / Capture Bridge / Worker 模块 `py_compile`：通过；
- **合计 80 项测试通过，两个 Job 均成功。**

P1.6 测试中，A/B 链路由内存 ABFP 双向模拟；Capture Adapter 使用 Fake Capture Bridge；Bridge 文件队列测试使用 Python 确定性模拟器。GitHub Runner 没有运行 Cadence Capture，也没有真实 COM / USB 串口。因此本轮 PASS 只覆盖软件 Adapter、文件协议、READY 校验、请求路由、去重、结果映射和超时状态；不覆盖真实 Capture 17.2 自动加载新版 Bridge、Tcl/Dbo API 执行、GUI 消息循环、工程保存或 Exit 生命周期。以上实机验收仍待主机 B 执行。

## P1.7 Process Supervisor 软件 CI 实测记录

- GitHub Actions Run：[#85](https://github.com/scanligithub/airbridge/actions/runs/43149369815)
- 测试提交：`42ba2e31c3902ae39abe249187360147b0d15151`
- Protocol / RequestTracker / ProcessSupervisor / Capture Bridge / Runtime Adapter / RequestBroker 单元测试：68 项通过；
- A/B 生产 SerialWorker 软件模拟：12 项通过；
- HTTP correlated request 集成：9 项通过；
- **合计 89 项通过，两个 Job 均成功。**

ProcessSupervisor 专项测试 9 项，其中 Windows-only Runner 集成测试实际通过 `tasklist` 发现当前 Python 进程、查询 executable path 并调用 EnumWindows 枚举窗口；FakeProcessBackend 测试覆盖多实例优雅关闭、模态提示阻断、超时、无可见主窗口、Allegro/Capture 匹配隔离及关闭期间新进程出现等场景。

该 Run 没有在 Runner 上关闭真实 Capture.exe。它验证了 Win32 发现 / 映像查询 / 窗口枚举 API 可调用，以及 Supervisor 状态机的安全策略；实际 Cadence WM_CLOSE、保存提示和进程退出必须在主机 B 实机验收。

