# AirBridge 当前架构现状报告

**盘点日期：** 2026-11-27  
**仓库：** `scanligithub/airbridge`  
**分支：** `main`  
**基线提交：** `ed7140fd940f5e1c3cf391cad052f94d5924e3c4`  
**盘点性质：** GitHub 源码与文档只读检查；不代表 Host A/B 最新部署状态。

## 1. 总体结论

当前 AirBridge 已具备通用远程通信基础设施的主要组成部分，但仍处于通用 Transport MVP 阶段。

已存在：

- ABFP 串口通信协议。
- Runtime Session 能力握手与 Build Identity。
- 请求关联、去重和 UNKNOWN 处理。
- Agent 身份、Scope、请求所有权和审计数据库。
- 通用 Shell HTTP / MCP 接口。
- Core 文件系统 HTTP 路由和文件传输基础。
- TransportProvider、FakeProvider、AirBridgeProvider。
- Windows Frozen EXE 与 MCP STDIO 入口。

尚未形成：

- 完整的通用 RFS Provider 契约。
- 异步 Artifact-RFS。
- 统一七态任务模型。
- 可信 Skill 权限降级机制。
- 完整的通用 Runtime 任务关联与恢复机制。
- 领域无关的完整双机集成验收。

## 2. GitHub 基线与 PR #25

PR #25 `feature/transport-fake-provider-mvp` 已合并到 main，合并提交：

`ed7140fd940f5e1c3cf391cad052f94d5924e3c4`

PR #25 交付：

- EDA-neutral `TransportProvider` Protocol 和通用操作结果契约。
- Skill 层内存 FakeProvider。
- Skill 侧 AirBridgeProvider，通过公开 M2.5 MCP STDIO Gateway 接入。
- 通用 `airbridge_shell_execute` MCP 工具。
- 原始 UUID request status 查询，不重新提交请求。
- 不确定的 STDIO 响应映射为 UNKNOWN，并保留原 request ID。
- STDIO 请求 / 响应串行化及 JSON-RPC ID 检查。
- Python 3.10 / 3.11 / 3.12 Transport CI、MCP Gateway CI 和 Windows EXE 构建。

PR #25 CI 记录（按项目验收记录）：

| 工作流 | Run ID | 记录结果 |
|---|---:|---|
| Windows EXE Build | 45945635054 | SUCCESS |
| Transport Contract MVP CI | 45945635134 | SUCCESS |
| MCP Gateway | 45945635110 | SUCCESS |

上述 Run ID 属于项目验收记录；本报告不把它们解释为物理串口验收证据。

## 3. 当前代码结构

### 3.1 主入口

`airbridge_entry.py` 已承担 GUI 与 MCP STDIO 入口分流：

- `AirBridge.exe mcp stdio`：初始化 Windows STDIO 管道，再加载 MCP Gateway。
- GUI 模式：加载 `airbridge_app.run_gui()`。
- MCP STDIO 路径不初始化 Qt GUI。

Phase 1.5 必须保留该边界。

### 3.2 airbridge_app.py

基线文件：

- 大小：166,584 字节。
- 换行符数量：4,265（即通常文本行数约 4,266，末尾换行情况可能影响不同工具的行数显示）。

主要职责集中：

| 类 / 模块 | 当前职责 |
|---|---|
| `SerialWorkerSignals` | 串口 Worker 事件信号 |
| `RemoteFileDialog` | 远程文件浏览 UI |
| `SerialWorker` | 串口收发、ABFP、Session、Shell、RFS、旧 EDA 适配 |
| `AntigravityIPCServer` | 北向 REST API、Agent 认证授权和请求处理 |
| `AirBridgeMainWindow` | GUI |
| `run_gui()` | GUI 组合与启动 |

其中 `SerialWorker` 仍直接注册 EDAExecutor、CaptureTclAdapter、Capture Runtime Profile、Shell Engine、RequestTracker 和 RequestBroker。

该文件职责集中，是 Phase 1.5 结构拆分的主要对象。

### 3.3 已独立存在的通用模块

当前已有：

- `protocol.py`
- `runtime_session_protocol.py`
- `build_identity.py`
- `request_tracker.py`
- `request_broker.py`
- `agent_security.py`
- `runtime_adapter.py`
- `shell_engine.py`
- `fs_engine.py`
- `process_supervisor.py`

Phase 1.5 应优先复用这些模块，避免重复建设同类组件。

## 4. Runtime Session 与串口通信

### 4.1 已有握手能力

当前实现已有：

- 自动握手。
- 最多三次尝试。
- 单次等待约 1 秒、重试间隔约 0.25 秒。
- `session_link_generation`。
- HELLO / CAPABILITIES 请求关联。
- 对端重启后的状态刷新。
- 对端 Build Identity 读取。

对端重新发送 HELLO 时，现有逻辑会在相应路径清理旧状态并重新协商。

### 4.2 状态诊断缺口

当前对外状态包括 state、negotiated、features、peer_build_identity、last_handshake_id 和 error 等信息，但缺少统一的新鲜度表达：

- 最近一次成功握手时间。
- 当前状态对应的链路 generation。
- 状态快照更新时间 / 年龄。
- Build Identity 的可信度与新鲜度。
- `match / mismatch / unverified` 的明确结果。

Phase 1 应完善诊断和边界测试，不应无根因地重写握手协议。

### 4.3 串口参数

连接函数接收 `baud` 参数，并将其传给 `serial.Serial(..., baudrate=baud)`。因此波特率是可配置参数，不固定为 115200。

ABFP 帧另有最大载荷长度约束（当前源码常量为 65000）；它与波特率是不同层次的约束。

### 4.4 发送队列

当前有 `tx_high_queue` 和 `tx_low_queue`。高优先级帧类型包括 ACK、NACK、FILE_ABORT、HEARTBEAT、SESSION_HELLO、SESSION_CAPABILITIES 等。

发送循环在选择下一帧时优先检查高优先级队列。但：

- 不能中断已经开始的 `serial.write()`。
- 当前 `SHELL_KILL` 未列入高优先级帧类型。
- 尚无完整 Artifact-RFS 数据面流控与取消协议。

因此，现有队列是后续调度设计基础，不代表已经具备完整抢占能力。

## 5. Transport Provider

目录：`skill/transport/`

已有：

- `contract.py`
- `fake_provider.py`
- `airbridge_provider.py`
- `stdio_client.py`

### 5.1 契约

当前 `CONTRACT_VERSION = "1.0"`。

`OperationStatus` 包含：

- `COMPLETED`
- `ACCEPTED`
- `UNKNOWN`

COMPLETED 通过 `success: bool` 区分成功和失败。当前契约没有独立细粒度 `task_state` 字段，也未提供完整 RFS 方法。

### 5.2 AirBridgeProvider

当前支持：

- Provider 信息和能力查询。
- Shell 执行。
- 原始 request_id 状态查询。
- 不确定响应映射为 UNKNOWN。
- 不在 Skill Python 进程内直接读取 Agent Token。

Provider 实例启动 MCP STDIO 子进程，并在实例生命周期内复用客户端；不是每个工具调用都重新启动 EXE。

### 5.3 STDIO 并发

`McpStdioClient` 使用请求锁串行化 JSON-RPC 请求 / 响应，避免并发请求错误消费响应。

这保证了关联正确性，但同一 STDIO 客户端一次只能完成一个请求 / 响应交换。一个长时间同步工具调用会阻塞同会话其他调用。Artifact-RFS 应采用后台异步任务，而不是同步逐片 MCP 调用。

## 6. RFS 与文件传输

### 6.1 已有能力

Core 已有：

- `/api/v1/fs/*` HTTP 路由。
- `FSEngine`。
- FS_REQUEST / FS_RESPONSE。
- 文件列表、读取、写入、创建目录、删除、移动和 stat。
- 文件拉取及 FILE_CHUNK 分片传输基础。

### 6.2 缺口

现有文件系统请求主要采用同步请求 / 响应方式；相关同步等待默认超时约 15 秒。读取接口默认预览上限为 65,536 字节。

尚未形成 Provider / MCP 完整 RFS 工具，也没有完整的异步 Artifact-RFS：

- transfer_id。
- 可恢复的分片序号 / 偏移。
- 分片与整文件 SHA-256。
- 传输任务持久化状态。
- 取消、完成确认与清理。
- 依据实际链路能力调节窗口和分片的调度机制。

现有 FILE_CHUNK 不能直接等同于完整 Artifact-RFS。

## 7. 请求、任务与 Agent 安全

### 7.1 RequestTracker

Host A RequestTracker 当前以内存保存为主，默认 TTL 900 秒、最大记录数 500。它不是跨 Host A 重启的持久化任务数据库。

### 7.2 RequestBroker

Host B RequestBroker 使用 SQLite 持久化，默认 TTL 24 小时、最大记录数 10,000；具备 request_id 去重、payload SHA-256 冲突检测和长期 RUNNING 请求转 UNKNOWN 的处理。

### 7.3 ShellEngine

ShellEngine 维护独立的 Shell task 注册信息，包括 task_id、finished、killed、exit_code 和输出等。当前尚未与 RequestBroker / Agent Security 形成统一的持久化七态任务模型。

### 7.4 Agent Security

已有 Agent Principal、Token ID、Agent Scope、请求所有权、限流、SQLite 审计和凭据撤销。

当前授权主体以 Agent 为核心；尚无可由服务端可信验证的 Skill 身份与 Skill Scope 降级机制。

MCP Gateway 文档说明：同一 Windows 用户账户内不构成 OS 级进程隔离。

## 8. 旧 EDA 路径与现有 Skill

Core 当前仍包含：

- `eda_engine.py`
- `capture_tcl_bridge.py`
- CaptureTclAdapter
- EDAExecutor
- `/api/v1/eda/execute`
- EDA 专用 ABFP 帧类型

EDA Execute 有 Gateway 与 Host A 服务端双重开关，默认关闭，并要求 `eda.execute` Scope。现阶段保留兼容路径，不扩展其业务功能，不提前删除。

### 8.1 orcad-capture

当前仓库存在 `skill/orcad-capture/`，包含 Capture Session、Process、Startup、Requests、Design、Exit 等代码与 Tcl Bridge。

仓库已有 Capture 相关 Windows Hosted CI 和 Python 版本矩阵。历史 Host B 验收覆盖 Capture 17.2 受控启动、OPJ 打开、Dbo 只读检查、授权退出及原 PID 消失确认。

M3.2.6 后续新增的部分 Exit 恢复和故障路径主要有 CI 故障注入证据，尚不能据此认定所有对应场景已完成 Host B 实机复验。未保存提示、多实例 / PID 复用、Shell 异常、Host A 崩溃 / 重启恢复仍需专项验证。

### 8.2 orcad-pdf-export

仓库存在 `skill/orcad-pdf-export/`，包含 Skill 文档、参考资料、Tcl 模板和提交脚本。

本次 main 树检查未找到专门的 PDF Export CI 工作流或独立测试文件，也没有足够证据证明完整 Host B 实机验收已完成。应标记为验收证据不足，不推断为已通过或完全没有测试。

## 9. 验收证据边界

- FakeProvider 通过仅证明 Provider 契约测试，不证明真实串口。
- Python CI 通过仅证明对应自动化测试范围。
- Frozen EXE Smoke 可证明真实 Windows STDIN / STDOUT、JSON-RPC 和进程生命周期，不等于物理串口验收。
- Host A / Host B 物理验收必须记录 EXE 构建身份、链路配置、测试请求 ID、结果和故障恢复证据。
- 本报告为 GitHub 源码盘点，不声明当前 Host A/B 部署版本与本次 main 完全一致。

## 10. 总结

当前 AirBridge 已具备通用底座雏形。近期主线是：

1. Phase 1：Core 稳定性与安全闭环。
2. Phase 1.5：`airbridge_app.py` 纯结构拆分。
3. Phase 2：完善现有 Transport / RFS，并建设异步 Artifact-RFS。
4. Phase 3：轻量可信 Skill 权限与任务关联。
5. Phase 4：安全治理与旧 EDA 路径渐进退役。
6. Phase 5：transport-demo 领域无关集成验收。

专业 Skill 扩展留到 Phase 6。
