# AirBridge 通用架构建设方案与实施计划 v1.0

**状态：** 项目阶段主基线  
**制定日期：** 2026-11-27  
**代码基线：** `main@ed7140fd940f5e1c3cf391cad052f94d5924e3c4`  
**适用范围：** AirBridge Core、Transport、Agent / Skill 接入与通用集成验收

## 1. 战略目标

将 AirBridge 建设为不依赖任何特定 EDA 产品、Agent Host 或 Skill 实现的通用远程通信与执行基础设施。

AirBridge Core 负责通用、透明、可审计的远程通信能力；专业软件 API、对象模型、生命周期和业务工作流由独立 Skill 负责。未来增加 Agent、Skill 或 EDA 产品，不应要求修改 AirBridge Core。

当前阶段优先建设通用底座，不以开发 Capture / Allegro 等专业 Skill 为主线。现有 EDA Skill 与兼容路径进入特性冻结和维护状态，待通用架构通过验收后再迁移和扩展。

## 2. 核心架构原则

### 2.1 双端一致

Host A 和 Host B 运行同一套 AirBridge 程序。Host A 可运行 Agent Host、Agent、Skill 和本机 Runtime；Host B 不需要安装 Agent 或 Skill，只运行 AirBridge。

### 2.2 Core 保持领域无关

Core 的职责限于：

- ABFP 与串口传输。
- 通用 Shell、RFS 和文件传输。
- 请求关联、去重、状态与 UNKNOWN。
- Agent 身份、Scope、认证、审计和限流。
- Runtime Session 与能力发现。
- 通用任务和传输调度。

Capture、Allegro、OrCAD、Altium 等专业软件 API、Tcl / SKILL 逻辑、设计对象模型及专业生命周期不得进入 Core。

### 2.3 Transport 与物理链路解耦

串口波特率、端口、帧传输速率和链路质量均属于配置或运行时能力，不得写死为 115200 bps 或其他固定值。

分片尺寸、窗口大小、超时和重试策略应依据 ABFP 帧载荷上限、实际吞吐量、往返时延、资源限制和链路状态动态配置或协商。

### 2.4 控制面与数据面分离

MCP STDIO 主要承担控制面：能力查询、任务/传输创建、状态查询和取消请求。

大文件数据面由 AirBridge 进程内部异步 Worker 执行，不能要求上层 Skill 通过同一条同步 STDIO 会话逐片阻塞传输。

控制帧应具备优先调度能力。但必须明确：队列优先级不能中断已经开始的底层串口写操作，取消和抢占语义需要单独设计和验证。

### 2.5 Agent Host 边界

Antigravity、OpenCode 等 Agent Host 已有自身的 Skill 发现、安装和对话编排能力。AirBridge 不重复建设 Agent Host 的 Skill 包管理器或 LLM 多智能体编排系统。

AirBridge 侧只实现自身必须掌握的能力：身份和权限约束、通用任务关联、远端执行状态、共享通道隔离、审计与恢复。

## 3. 统一任务与权限设计原则

### 3.1 七态任务模型

目标 Runtime 任务状态：

- `ACCEPTED`
- `RUNNING`
- `SUCCEEDED`
- `FAILED`
- `CANCEL_REQUESTED`
- `CANCELLED`
- `UNKNOWN`

状态转换、终态规则、持久化、重启恢复和取消语义应在 Phase 2 设计中正式定义。

当前 Provider v1.0 的 `COMPLETED / ACCEPTED / UNKNOWN` 三态不能直接等同于七态。建议 Provider v1.1 保留兼容粗粒度字段，同时提供结构化 `task_state`；或者采用明确的主版本升级。不得静默改变旧字段语义。

建议兼容映射：

| Runtime task_state | Provider 粗粒度状态 | 说明 |
|---|---|---|
| ACCEPTED | ACCEPTED | 已受理 |
| RUNNING | ACCEPTED | 仍在执行 |
| CANCEL_REQUESTED | ACCEPTED | 已请求取消，尚未确认 |
| SUCCEEDED | COMPLETED | success=true |
| FAILED | COMPLETED | success=false，保留失败原因 |
| CANCELLED | COMPLETED | success=false，保留取消原因 |
| UNKNOWN | UNKNOWN | 无法确认最终结果 |

### 3.2 可信权限裁剪

目标有效权限：

`P_effective = P_Agent ∩ P_Skill ∩ P_Policy`

其中：

- `P_Agent`：Agent 凭据被服务端授予的权限。
- `P_Skill`：可信 Runtime 为该 Skill 会话授予的权限上限。
- `P_Policy`：服务端当前策略允许的权限。

Skill 身份不能仅由外部脚本自报 `skill_id` 后就被当作可信身份。必须设计由受信任本机 Runtime / Gateway 强制实施的权限裁剪机制，必要时采用受限凭据或 Sub-Scope。

同一 Windows 用户账户下的不同进程不构成 OS 级安全隔离。服务端 Scope、路径约束、命令策略、审计和限流仍是核心安全边界。

### 3.3 任务关联

目标建立统一关联：

`task_id ↔ request_id ↔ agent_id`

并明确 Skill / Runtime 上下文、创建时间、执行状态、取消状态、结果摘要和审计关联。任务状态应支持只读查询、断线恢复和服务重启后的可解释性。

## 4. 六阶段路线图

### Phase 1：Core 稳定性与安全闭环

目标：在已有功能上补齐诊断、边界测试和安全验收，不推倒重写。

主要工作：

1. Runtime Session 握手状态新鲜度、generation、双端并发启动与重连诊断。
2. Build Identity 的 `match / mismatch / unverified` 表达。
3. 已建立 STDIO 会话期间的服务端动态策略即时拦截测试。
4. Request ownership、去重、UNKNOWN、审计和只读恢复边界验证。
5. 明确 RequestTracker、RequestBroker、ShellEngine 的状态权威和职责边界。
6. 归档 Phase 1 测试与验收基线。

准出条件：上述关键边界有自动化测试证据，现有 Gateway、Transport 和 Windows EXE 构建无回归。

### Phase 1.5：Core 纯结构拆分

目标：在 Phase 1 稳定性闭环之后，先拆分 `airbridge_app.py`，再扩展 Transport 和 Runtime。

原则：

- 纯结构搬迁，保持外部行为等价。
- 不改变 ABFP、串口行为、Runtime Session、HTTP API、Agent Scope、请求语义、Shell / RFS 行为。
- 保留 `airbridge_entry.py` 已有的 GUI / MCP STDIO 入口分流；MCP STDIO 不应因拆包而初始化 Qt。
- 优先复用已独立存在的 `protocol.py`、`fs_engine.py`、`shell_engine.py`、`agent_security.py` 等模块。
- 分批搬迁 Worker、HTTP API、UI 和应用组合入口；每批单独测试和提交。
- 不删除旧 EDA 兼容路径。

准出条件：模块依赖清晰、无循环依赖、现有测试通过、Frozen EXE 和 GUI / STDIO 行为等价。

### Phase 2：通用 Transport 完整化

目标：完善已有 Provider 和 Core 文件系统能力，不另造一套平行通信栈。

主要工作：

1. 设计 TransportProvider v1.1，明确能力发现、Shell、RFS、异步任务、取消和结果查询契约。
2. 明确三态 Provider 与七态 Runtime 的兼容或版本升级策略。
3. 将现有 Core RFS 能力接入 MCP / Provider，覆盖 stat、list、mkdir、read、write、remove、move 等受控原语。
4. 设计 Artifact-RFS：transfer_id、分片序号/偏移、大小限制、校验和、重试、恢复、完成确认、取消和清理。
5. 上传 / 下载创建后立即返回 `ACCEPTED + transfer_id`；由 AirBridge 内部后台 Worker 执行数据面。
6. STDIO 控制面负责启动、进度、状态和取消；不得逐片同步阻塞同一 STDIO 会话。
7. FakeProvider 增加虚拟文件系统、传输任务和故障注入。
8. 复用长生命周期 McpStdioClient / Provider 实例，避免每个分片拉起一次 EXE。
9. 依据运行时链路能力确定分片、窗口和超时；不固定串口波特率。
10. 控制帧高优先级调度、取消帧和数据面流控必须有明确实现及测试。

安全前置：RFS 对外开放前必须定义受控根目录、路径规范化、符号链接 / Junction / UNC / traversal 防护、文件大小限制和审计。

### Phase 3：轻量 Agent / Skill 权限与任务关联层

目标：只建设 AirBridge 必须拥有的 Runtime 信任和任务能力，不重造 Agent Host。

主要工作：

1. Skill Manifest 的能力声明与版本兼容元数据。
2. 可信 Runtime / Gateway 对 Skill 权限进行降级和强制执行。
3. 建立 `task_id ↔ request_id ↔ agent_id` 的持久化关联。
4. 支持任务状态查询、断线恢复、服务重启恢复和取消结果确认。
5. 多 Agent 共享通道的请求隔离、配额、队列公平性和审计归属。
6. 定义 Host Adapter 的最小接口，使不同 Agent Host 可接入，不绑定 Antigravity / OpenCode。

不在本阶段建设通用 LLM 编排器、对话级多 Agent 委派系统或替代 Host 的 Skill 安装 UI。

### Phase 4：安全治理与旧 EDA 路径渐进退役

目标：完善通用安全边界，并在替代路径充分验证后再清理旧实现。

主要工作：

- RFS 路径根目录、符号链接、Junction、UNC、traversal、TOCTOU 和递归删除保护。
- Shell 可执行文件 / 参数策略、权限细分、限流、超时、输出大小和审计。
- Agent / Skill / Policy 权限交集的可信执行。
- 对旧 EDA Adapter、EDAExecutor、专用 HTTP / ABFP 路径做依赖盘点。
- 只有在新通用能力覆盖并通过兼容性验收后，才分批退役旧 EDA 路径。
- 按职责拆分 Core 模块，不改变双端同一 AirBridge 程序原则。

Phase 1–5 期间旧 EDA 路径和现有 EDA Skill 实行特性冻结：不新增业务特性、不为其特化 Core；必要的安全修复、兼容性修复和维持 CI 通过仍允许。

### Phase 5：领域无关 transport-demo 集成验收

目标：在不依赖任何 EDA 软件的情况下，证明通用架构可用。

建设一个独立的 `transport-demo` / `runtime-demo` Skill，覆盖：

- 能力发现和远端环境信息。
- Shell 只读与受控操作。
- RFS 读写和目录操作。
- 大文件上传 / 下载与完整性校验。
- 异步任务状态、取消和恢复。
- Agent 隔离与权限撤销。
- 多 Skill 并发使用共享通道。
- 断线、重连、超时、UNKNOWN 和服务重启。
- 速率变化、不同链路参数和压力测试。

验收分层：

| 层级 | 验收范围 |
|---|---|
| L1 | FakeProvider、契约测试、故障注入 |
| L2 | Python CI、Gateway / Runtime 集成测试 |
| L3 | Windows Frozen EXE、真实 STDIO 进程和本机集成 |
| L4 | Host A / Host B 真实物理链路、长稳、吞吐、并发、断线恢复 |

CI 或 FakeProvider 通过不能代替物理双机验收。物理验收报告必须记录当时实际串口参数和链路条件，但这些参数不是架构固定值。

Phase 5 是放开专业 Skill 大规模迁移和扩展的准出门槛。

### Phase 6：独立专业 Skill 生态

仅在通用架构通过 Phase 5 后推进。

建议先将现有 `orcad-capture` 作为迁移样板，验证专业 Skill 如何使用通用 Provider；随后按实际需求独立建设 OrCAD PCB Editor、Allegro、Altium Designer 等 Skill。

每个 Skill 独立拥有专业 API、对象模型、生命周期、版本兼容、测试、安装和升级说明。Skill 之间不共享专业软件生命周期实现；Core 不增加 EDA 特化逻辑。

## 5. 执行顺序和变更管理

执行顺序：

`Phase 1 → Phase 1.5 → Phase 2 → Phase 3 → Phase 4 → Phase 5 → Phase 6`

采用小 PR、分阶段验收。每个阶段开始前更新依赖和风险清单；每个阶段完成后归档代码、CI、Frozen EXE 和实机证据。

禁止把结构重构、协议升级、权限模型变化和新功能堆进同一个 PR。

## 6. 当前项目基线

- 当前 main 基线：`ed7140fd940f5e1c3cf391cad052f94d5924e3c4`。
- PR #25 Transport Fake Provider MVP 已合并。
- 用户已报告 PR #25 后 CI 成功。
- PR #25 没有完成新 Provider 的 Host A / Host B 物理双机验收。
- 当前已有 Runtime Session、Agent Security、Shell、Core RFS、RequestBroker、Provider MVP 等基础能力。
- 详细现状见《AirBridge 当前架构现状报告》；差距见《AirBridge 通用架构差距与风险清单》。

## 7. 明确非目标

- 不将串口固定为 115200 bps。
- 不将 Capture / Allegro 生命周期纳入 Core。
- 不在 Phase 1–5 扩展 EDA Skill 业务特性。
- 不重造 Agent Host 的 Skill 安装、对话编排和多 Agent LLM 系统。
- 不把 FakeProvider 或 CI 通过等同于物理双机通过。
- 不在没有替代方案和兼容证据时删除旧 EDA 路径。
