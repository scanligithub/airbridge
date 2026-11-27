# AirBridge Phase 1 / Phase 1.5 实施任务拆分 v1.0

**代码基线：** `main@ed7140fd940f5e1c3cf391cad052f94d5924e3c4`  
**日期：** 2026-11-27  
**实施原则：** 先诊断，再结构重构；不提前进入 Phase 2。

## 1. Phase 1：Core 稳定性与安全闭环

### P1-01：Runtime Session 握手状态诊断

检查：

- 自动握手启动条件和重试行为。
- HELLO 与 CAPABILITIES 关联。
- 双端并发启动。
- 对端重启和 generation 变化。
- 过期响应。
- 握手失败和恢复。

评估是否补充：

- `last_successful_handshake_at`
- `link_generation`
- `peer_build_identity`
- `state_updated_at`
- `handshake_error`

这些字段首先用于诊断和状态解释，不意味着必须立即修改 ABFP 协议。

验收：

- 双端同时启动能够正确完成握手。
- 旧 generation 的响应不会覆盖新连接状态。
- 对端重启时旧 Build Identity 不会继续冒充当前状态。
- 未确认的能力不会被当成已协商能力。

### P1-02：Build Identity 可信度表达

当前已有 build_commit、build_version、build_timestamp、build_platform 等信息。

定义：

| 状态 | 含义 |
|---|---|
| match | 对端构建身份符合预期 |
| mismatch | 对端构建身份明确不符合预期 |
| unverified | 无法取得或验证足够身份信息 |

不能把 unknown 直接判为 mismatch。

验收覆盖：正确构建、错误构建、开发构建、旧版本、缺少身份信息、重连后身份刷新。

### P1-03：已建立 STDIO 会话的策略即时拦截

在隔离自动化测试中：

1. 创建 MCP STDIO Gateway 会话。
2. 完成 initialize 和 tools/list。
3. 保持 STDIO 进程存活。
4. 将测试策略从允许切换为拒绝。
5. 通过同一 STDIO 会话再次调用工具。
6. 检查服务端拒绝结果和审计事件。

要求：

- 不通过客户端卸载工具代替服务端拒绝测试。
- 不修改真实 Host A 配置。
- 不读取、打印或探测 Token。
- 使用隔离测试凭据和测试数据库。

验收：已有会话不能绕过最新服务端策略。

### P1-04：请求所有权与 UNKNOWN 闭环

检查：

- Agent A 创建请求。
- Agent B 查询 Agent A 请求。
- 不存在的 request_id。
- 已存在但不属于当前 Agent 的 request_id。
- 请求响应丢失。
- 请求执行超时。
- UNKNOWN 后再次查询。
- 相同 request_id 重复提交。

验收：

- 请求所有权隔离有效。
- 不确定结果不会自动重新提交。
- 状态查询不会产生新的远端副作用。
- 审计与请求 ID 能够对应。

### P1-05：RequestBroker / RequestTracker 职责边界

梳理：

- Host A RequestTracker。
- Host B RequestBroker。
- ShellEngine。
- Runtime Adapter。
- Agent Security request ownership。

形成数据流图，明确：

- 哪个模块是执行状态权威。
- 哪个模块负责请求关联。
- 哪个模块负责去重。
- 哪个模块持久化。
- 重启后各层如何恢复。

本任务先明确现有状态语义和转换，不在 Phase 1 中提前实现新的七态任务模型。

### P1-06：Phase 1 文档和测试基线

形成：

- Runtime Session 状态机说明。
- Build Identity 判定规则。
- STDIO 动态策略测试报告。
- Request / UNKNOWN 状态与所有权报告。
- 当前 CI 基线记录。

Phase 1 准出条件：

- 握手状态可准确解释。
- Build Identity 能区分 match / mismatch / unverified。
- 服务端动态策略有持续会话测试证据。
- 请求所有权和 UNKNOWN 处理有回归测试。
- 现有 Windows EXE、Gateway 和 Transport CI 不回退。

## 2. Phase 1.5：airbridge_app.py 纯结构拆分

### P1.5-01：结构和依赖盘点

先记录：

- 顶层 import。
- 类之间的引用。
- Worker 与 GUI 依赖。
- HTTP API 与 Worker 依赖。
- AgentSecurityStore 生命周期。
- SerialWorker 生命周期。
- GUI 启停顺序。
- EXE 启动入口。

特别确认 `airbridge_entry.py` 已将 MCP STDIO 与 GUI 分开，拆包不能破坏该边界。

### P1.5-02：确定模块边界

候选职责：

| 当前代码 | 候选模块职责 |
|---|---|
| SerialWorkerSignals / SerialWorker | Core 串口 Worker |
| AntigravityIPCServer | HTTP API / Agent API |
| RemoteFileDialog | UI 远程文件浏览 |
| AirBridgeMainWindow | UI 主窗口 |
| run_gui | 应用组合与启动 |

最终目录和文件名根据真实导入关系确定，不预先强制规定。

现有 `protocol.py`、`fs_engine.py`、`shell_engine.py`、`agent_security.py` 等独立模块继续复用。

### P1.5-03：建立行为等价测试基线

拆分前记录：

- GUI 启动和关闭。
- 串口连接和断开。
- Runtime Session 握手。
- HTTP API 路由。
- Agent 认证与授权。
- Shell 请求。
- RFS 请求。
- MCP STDIO 入口。
- Frozen EXE 启动。

尽可能复用已有测试，不为结构重构改写协议行为。

### P1.5-04：分步搬迁

建议顺序：

1. 迁移 SerialWorkerSignals / SerialWorker。
2. 迁移 HTTP API 服务类。
3. 迁移 RemoteFileDialog。
4. 迁移 AirBridgeMainWindow。
5. 整理应用组合入口和兼容导入。
6. 检查模块循环依赖。
7. 检查 Frozen EXE 打包路径。

每一步独立测试、独立提交。不要一次性重写整个 4,266 行文件。

### P1.5-05：严格禁止的改动

本阶段不得：

- 改变 ABFP 帧格式。
- 改变串口收发行为或固定波特率假设。
- 改变 Runtime Session 握手行为。
- 改变 HTTP API 路由和响应格式。
- 改变 Agent Scope。
- 改变请求关联或 UNKNOWN 语义。
- 改变 Shell 或 RFS 行为。
- 增加 EDA 生命周期功能。
- 删除旧 EDA 兼容代码。
- 改变 EXE GUI / STDIO 启动方式。

只允许为结构迁移所必需的导入、依赖和测试调整。

### P1.5-06：验收

必须通过适用的：

- Python 单元测试。
- Runtime Session CI。
- Agent Security CI。
- MCP Gateway CI。
- Transport Contract CI。
- Windows Frozen EXE Build。
- MCP STDIO Frozen Smoke。
- GUI / API 启动相关回归测试。

验收要求：拆包前后的外部行为保持一致。

## 3. Phase 1.5 交付物

1. `airbridge_app.py` 原始依赖图。
2. 拆包后的模块依赖图。
3. 模块职责说明。
4. 行为等价测试报告。
5. Frozen EXE 构建报告。
6. 旧 EDA 兼容路径保留清单。

## 4. 实施节奏建议

| PR | 内容 |
|---|---|
| PR-A | Phase 1 握手与状态诊断 |
| PR-B | Build Identity 和状态判定 |
| PR-C | STDIO 动态策略测试 |
| PR-D | 请求所有权与 UNKNOWN 回归 |
| PR-E | Phase 1 文档和基线归档 |
| PR-F | Phase 1.5 第一批纯结构搬迁 |
| PR-G | Phase 1.5 后续模块搬迁 |
| PR-H | 行为等价和 Frozen EXE 验收 |

具体 PR 可按真实依赖调整，不要求机械拆成八个 PR。

## 5. 完成后的下一步

Phase 1 和 Phase 1.5 完成后，才进入 Phase 2：

- TransportProvider v1.1。
- 七态任务映射。
- RFS Provider。
- 异步 Artifact-RFS。
- STDIO 控制面与后台数据面隔离。
- FakeProvider 虚拟文件系统。

Phase 2 不应与 Phase 1.5 纯结构重构混在同一个 PR。

## 6. 安全与操作边界

本文档不授权以下操作：

- 修改 Host A/B 配置。
- 读取或探测 Agent Token。
- 启动或停止 Host A/B 进程。
- 执行 Host B Shell。
- 执行 Tcl / SKILL。
- 部署或覆盖 EXE。
- 删除旧 EDA 路径。

涉及真实机器的操作必须另行取得明确授权。
