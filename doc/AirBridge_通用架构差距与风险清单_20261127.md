# AirBridge 通用架构差距与风险清单

**基线：** `main@ed7140fd940f5e1c3cf391cad052f94d5924e3c4`  
**日期：** 2026-11-27

## 1. 总体判断

当前 AirBridge 已有通用通信、认证、请求关联、Shell 和基础 RFS 能力，但尚未形成完整的通用 Transport / Runtime 体系。

## 2. 风险清单

| ID | 优先级 | 问题 | 影响 | 计划阶段 |
|---|---|---|---|---|
| G-01 | P1 | Runtime Session 缺少统一状态新鲜度表达 | 可能误用过期能力快照 | Phase 1 |
| G-02 | P1 | 双端并发启动和重连边界需要专项验证 | 握手竞争和恢复误判 | Phase 1 |
| G-03 | P1 | 已建立 STDIO 会话的动态策略即时拦截未验收 | 会话可能绕过最新策略的风险未排除 | Phase 1 |
| G-04 | P1 | Provider 三态与目标七态任务模型不一致 | 任务语义和接口兼容困难 | Phase 2 |
| G-05 | P1 | MCP Gateway 未暴露完整 RFS Provider 工具 | Skill 无法通过统一 Provider 使用完整文件系统能力 | Phase 2 |
| G-06 | P1 | RFS 主要为同步请求，缺少 Artifact-RFS 任务化 | 大文件操作可能占用请求处理时间 | Phase 2 |
| G-07 | P1 | STDIO 客户端使用请求锁串行化 | 长调用会阻塞同会话其他调用 | Phase 2 |
| G-08 | P1 | 现有高优先级队列不能中断正在发送的帧 | 控制命令最坏等待时间未被界定 | Phase 2 |
| G-09 | P1 | Agent Scope 与 Skill 权限未形成可信裁剪 | Skill 权限隔离可能只是调用方自律 | Phase 3 |
| G-10 | P1 | task_id / request_id / agent_id 尚无统一持久化关联 | 任务恢复和多 Agent 隔离不完整 | Phase 3 |
| G-11 | P1 | RFS 路径边界与完整防穿越机制需专项核验 | 可能访问不应开放的远程文件 | Phase 2 / 4 |
| G-12 | P1 | Shell 通用命令执行策略需细化 | 黑名单不能替代受控执行策略 | Phase 4 |
| G-13 | P2 | airbridge_app.py 职责集中 | 后续开发和回归成本高 | Phase 1.5 |
| G-14 | P2 | Core 仍包含旧 EDA 适配器与执行路径 | 长期可能影响通用边界 | Phase 4 |
| G-15 | P1 | Capture Exit 部分新增恢复路径尚未完成实机复验 | CI 通过不代表所有 Host B 故障路径通过 | 冻结管理 |
| G-16 | P1 | transport-demo 尚不存在 | 缺少领域无关完整验收载体 | Phase 5 |

## 3. 重点风险与处理原则

### G-01 / G-02：Runtime Session 状态新鲜度

当前已有握手、重试、generation 和对端状态清理。

需要补齐：

- 最近一次成功握手时间。
- 当前状态对应的链路 generation。
- 状态快照更新时间和年龄。
- 过期握手响应处理。
- 双端同时启动和重连竞态测试。

不应在没有根因证据时增加无限重试或重写协议。

### G-03：动态策略即时拦截

必须在同一个 STDIO 会话保持存活的条件下测试：

1. 建立 Gateway 会话并完成 initialize / tools/list。
2. 保持 STDIO 进程存活。
3. 将测试策略从允许切换为拒绝。
4. 使用同一会话再次调用受控工具。
5. 检查服务端拒绝结果与审计事件。

不能通过客户端卸载工具来代替服务端拒绝测试。测试使用隔离凭据和测试数据库，不读取或输出 Token，不修改真实 Host A 配置。

### G-04：七态任务与 Provider 三态

目标 Runtime 状态：

- ACCEPTED
- RUNNING
- SUCCEEDED
- FAILED
- CANCEL_REQUESTED
- CANCELLED
- UNKNOWN

建议服务端 Runtime / Task Store 持久化细粒度状态；Provider v1.1 保留兼容粗粒度字段并提供结构化 task_state，或进行显式主版本升级。

| Runtime 状态 | Provider 粗粒度状态 |
|---|---|
| ACCEPTED | ACCEPTED |
| RUNNING | ACCEPTED |
| CANCEL_REQUESTED | ACCEPTED |
| SUCCEEDED | COMPLETED，success=true |
| FAILED | COMPLETED，success=false |
| CANCELLED | COMPLETED，success=false，保留取消原因 |
| UNKNOWN | UNKNOWN |

必须定义状态转换、终态、取消确认和重启恢复，不能仅靠 success 布尔值表达全部语义。

### G-07 / G-08：STDIO 与数据面隔离

当前 STDIO 客户端请求 / 响应串行化，能保护 JSON-RPC 关联，但不能并行执行同一客户端的同步交换。

Artifact-RFS 应：

- 启动上传 / 下载后快速返回 ACCEPTED 和 transfer_id。
- 由 AirBridge 进程内部后台 Worker 驱动数据面。
- MCP STDIO 负责启动、查询进度、结果和取消。
- 不要求 Skill 同步逐片调用。
- 不承诺未经测量的毫秒级响应指标。
- 控制帧调度、流控、取消和已开始串口写操作的边界必须明确。

### G-09：Skill 权限可信执行

目标：

`P_effective = P_Agent ∩ P_Skill ∩ P_Policy`

当前服务端主要识别 Agent Principal，不能仅凭调用方自报 `skill_id` 建立安全边界。

需要受信任 Runtime / Gateway 强制裁剪，必要时采用受限凭据或 Sub-Scope。同一 Windows 用户账户内不构成 OS 级进程隔离；服务端 Scope、路径约束、Shell 策略和审计仍是核心防线。

### G-11：RFS 路径安全

必须专项处理：

- `..` 路径穿越。
- 符号链接。
- Windows Junction。
- UNC 路径。
- 目录递归删除。
- 路径规范化与授权检查之间的 TOCTOU 竞态。
- 文件大小、数量和资源限制。
- 审计及拒绝原因。

在安全边界完成前，不应通过 MCP 向 Agent 开放不受约束的写入和删除能力。

### G-12：Shell 执行安全

需要明确：

- Agent Scope 与具体 Shell 操作的授权关系。
- 可执行文件和参数策略。
- 工作目录边界。
- 超时、取消和进程树处理。
- stdout / stderr 大小限制。
- 并发配额和审计。

不能仅以命令字符串黑名单作为主要安全控制。

### G-13：拆包风险

拆包前先形成依赖图和行为测试基线。必须保留 `airbridge_entry.py` 的 GUI / STDIO 入口分流，禁止结构搬迁顺便改变业务语义。

### G-14 / G-15：旧 EDA 路径与 Skill 冻结

Phase 1–5 期间：

- 不新增 EDA 业务特性。
- 不为 Capture / Allegro 特化 Core。
- 允许必要的安全、兼容性和 CI 维护。
- 不提前删除旧 EDA 兼容代码。
- 在 Phase 5 通过后，单独制定迁移和退役计划。

### G-16：transport-demo

必须提供非 EDA 的端到端验证载体，覆盖 Fake、CI、Frozen EXE、Host A/B 物理链路四层证据。

## 4. 链路参数原则

串口波特率由实际配置决定，不存在固定 115200 的架构要求。

Artifact-RFS 应根据 ABFP 最大帧载荷、实际吞吐量、往返时延、内存限制、重传和队列负载设计分片、窗口与超时。每次物理验收记录实际参数，但不能把测试参数写成协议固定值。

## 5. 风险关闭标准

每项风险必须具有：

1. 明确代码位置。
2. 明确失败场景。
3. 可重复测试。
4. 兼容性要求。
5. 验收证据和证据适用范围。

不能仅凭代码存在就认定功能已通过。CI、FakeProvider、Frozen EXE 和物理双机验收分别记录，不能互相替代。
