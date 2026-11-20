# M2.4：Zero-Touch Agent Authentication 与凭据生命周期管理设计

- 日期：2026-11-19
- 状态：M2.4.1 首批代码及 tokenless provision CLI 已提交 Draft PR #14；Windows Credential Manager 与 Gateway CI（Python 3.11 / 3.12）PASS；尚未合并、尚未在 Host A 实机部署
- 目标分支：`feature/m2-4-zero-touch-agent-auth`
- 基线：当前 `main`（M2.3 Agent Security 已合并）
- 上游设计：`AirBridge_MCP_Gateway_M2.3_Agent_Identity_Authorization_Design_v1.0.md`

## 1. 产品原则

**Zero-Touch Agent Authentication：Agent 身份认证对用户透明。**

Token 是 AirBridge 内部安全凭据，不是用户配置项。用户管理 Agent 身份、连接状态和权限；AirBridge 自动负责凭据生成、保存、读取、轮换和撤销。

验收时，普通用户不应被要求：
- 手工生成、查看、复制或粘贴 Token；
- 将 Token 写入 MCP JSON、环境变量、脚本或普通配置文件；
- 在 Token 过期或轮换时手工修改 Agent 配置；
- 为每个新 Agent 修改 AirBridge Core 或重新构建 AirBridge.exe。

用户仍然需要明确决定是否添加 Agent，以及是否授予文件写入、Shell、EDA 等敏感权限。**认证透明不等于授权透明，更不等于自动授予高权限。**

## 2. 现状与问题

M2.3 已实现：
- 每 Agent 独立 Bearer Token；
- 服务端 SQLite 仅保存 Token SHA-256 摘要；
- 服务端 Scope、请求所有权、审计和配额；
- 本地管理员 CLI 的 issue / list / revoke / audit；
- MCP Gateway 通过 `AIRBRIDGE_AGENT_TOKEN` 环境变量取得凭据。

当前人工签发流程会一次性显示明文 Token，用户还需将其配置到 Agent MCP Host。该流程安全基础已具备，但不符合 Zero-Touch 用户体验。

M2.4 应复用 M2.3 服务端认证与授权，不削弱 HTTP API 的强制认证，不引入共享主 Token，也不允许客户端自行声明 Agent ID 或 Scope。

## 3. 目标架构

### 3.1 Agent Registry

服务端以稳定的 `agent_id` 标识 Agent，维护名称、类型、启用状态、创建时间和默认权限策略。Agent ID 不是秘密，也不能单独作为身份凭据。

同一 Agent 可在凭据轮换期间短暂拥有新旧两枚有效凭据；不同 Agent 永远使用不同凭据。删除/停用 Agent 时撤销其全部凭据。

### 3.2 Credential Provider

引入凭据存储抽象，至少提供：
- `store(agent_id, credential)`
- `load(agent_id)`
- `replace(agent_id, credential)`
- `delete(agent_id)`

Windows 首选 Windows Credential Manager（由当前 Windows 用户凭据保险库保护）；不得把明文 Token 写入 MCP JSON、普通配置文件、SQLite、日志或命令行参数。凭据存储不可用或读取失败时必须 fail closed，不得退回无认证访问。

凭据 Provider 应可注入测试实现。非 Windows 环境不得假装提供同等级的系统凭据保护；仅用于 CI 的 mock 不可作为生产默认后端。

### 3.3 Provisioning Broker

Agent 注册和凭据签发必须经本机受信任的管理路径完成。不能新增一个允许任意 loopback HTTP 客户端匿名注册高权限 Agent 的端点。

Broker 负责：
1. 接收用户明确发起的“添加 Agent”操作；
2. 创建服务端 Agent 身份；
3. 按 Agent 类型分配最小默认 Scope；
4. 调用现有凭据签发逻辑生成高熵 Token；
5. 将 Token 直接写入当前 Windows 用户的 Credential Manager；
6. 确认凭据持久化成功后，才把 Agent 配置为可连接；
7. 对用户只显示 Agent 名称、连接状态和权限，不显示 Token。

Broker 的本地管理接口必须限制为当前 Windows 用户，并且只允许管理操作；它不得成为绕过 M2.3 HTTP API Token 验证的旁路。实现前需比较 Windows named pipe ACL 与现有桌面进程内管理入口，选择最小复杂度且可测试的方式。

### 3.4 MCP Gateway

Gateway 改为支持非秘密的 `AIRBRIDGE_AGENT_ID` / credential reference，并通过 Credential Provider 读取对应 Token，再在 HTTP 请求中附加 Bearer Header。

- 新安装的 Agent 不配置 `AIRBRIDGE_AGENT_TOKEN`。
- `AIRBRIDGE_AGENT_TOKEN` 暂时保留为显式兼容模式，供升级过渡和 CI 使用；日志不得输出其值。
- 缺少凭据、凭据不可读、Agent ID 不匹配或凭据已撤销时，连接失败并给出不含秘密的可操作错误。
- Gateway 不得把 Token 回传给 MCP Tool、模型上下文或 stdout。
- stdout 继续只承载 MCP stdio 协议；诊断信息写 stderr，且必须脱敏。

## 4. 默认权限策略

新 Agent 默认只获得完成连接诊断所需的只读权限：
- `runtime.read`
- `eda.versions.read`
- `request.read`

以下权限绝不因自动注册而默认授予：
- `filesystem.write`
- `shell.execute`、`shell.manage`
- `eda.execute`
- 管理类 Scope

Agent 可以声明所需能力，但声明不是授权。新增或提升权限必须通过服务端策略检查和用户明确授权；认证通过不代表操作获准。

## 5. 生命周期

### 首次添加
用户在 AirBridge 管理入口选择“添加 Agent”并确认 Agent 类型。系统创建 Agent 身份、分配只读 Scope、签发凭据、写入凭据保险库，并生成不含 Token 的 MCP 配置。任一步骤失败都应回滚或保持 Agent disabled，不能留下半注册的可用身份。

### 日常启动
Gateway 根据 Agent ID 从凭据保险库读取 Token，正常认证。用户不需要输入凭据。

### 轮换
先签发新 Token 并写入凭据保险库，确认新凭据可用后再撤销旧 Token。失败时保留旧凭据，不得先撤销再尝试签发。轮换过程应审计，但审计不包含 Token。

### 撤销与删除
用户停用或删除 Agent 时，服务端立即撤销其全部 Token，并清理本地凭据。若服务端撤销失败，不得报告删除成功；应保留明确的待完成状态并重试/提示管理员。

### 凭据丢失
不得静默创建具有旧权限的新身份。通过受信任管理入口重新签发凭据，保留原 Agent ID 和经审核的权限策略，并审计恢复行为。

## 6. 现有 Agent 迁移

M2.3 的环境变量 Token 模式继续兼容一个过渡期。迁移工具可在用户明确发起后，从现有 MCP 配置读取 Token、写入 Credential Manager，再将配置改为 Agent ID 引用。

迁移必须：
- 不把 Token 输出到终端、日志或报告；
- 写入新凭据成功并验证后，才修改 MCP 配置；
- 保留可恢复的配置备份，但备份不得继续包含可被普通用户/进程随意读取的明文 Token；若无法安全备份则采用原子更新和回滚；
- 迁移后验证认证成功，再撤销旧 Token；
- 支持 OpenCode / Antigravity 等不同 MCP Host 的配置适配器；适配器不能进入 AirBridge Core 的 EDA 业务层。

迁移工具不能假定能从服务端 SQLite 恢复旧 Token，因为数据库只保存摘要。

## 7. 安全边界

M2.3 的威胁模型继续有效：这是同一 Host A、同一受信任 Windows 用户环境内的应用层 Agent 身份隔离，不是抵御同一 OS 用户下恶意代码的强隔离。

Windows Credential Manager 可以避免明文 Token 出现在普通配置文件中，但同一用户上下文中运行的恶意程序仍可能尝试访问可用凭据或进程内存。不得宣传为操作系统级沙箱。

禁止：
- 取消 Bearer Token 服务端认证；
- 使用所有 Agent 共用的主 Token；
- 仅凭 loopback 地址信任请求；
- 允许 Agent 自报 agent_id 或 scopes；
- 将注册/轮换端点开放给匿名 HTTP 客户端；
- 将 Token 放入命令行参数、MCP Tool 参数、模型提示词或日志；
- 在凭据后端不可用时降级为无认证模式。

## 8. 实施分期

### M2.4.1 — Credential Provider 与 Gateway 透明读取
**当前进展（PR #14，Draft）：**
- 已新增 `credential_store.py`，使用当前 Windows 用户的 Windows Credential Manager（Generic Credential）保存每 Agent 凭据；不写入 MCP JSON 或普通配置文件。
- Gateway 支持 `AIRBRIDGE_AGENT_ID`，通过 Credential Provider 读取 Token；`AIRBRIDGE_AGENT_TOKEN` 作为显式旧模式继续兼容。
- 凭据读取失败时配置加载失败关闭；Agent ID 与旧 Token 环境变量同时配置会被拒绝。
- 管理 CLI 新增 `provision`：默认授予三个只读 Scope，直接写入并回读验证 Windows Credential Manager，只输出非秘密元数据；凭据写入失败会撤销刚签发的服务端 Token；拒绝覆盖已有有效凭据。
- 旧 `issue` 命令现在必须显式提供 `--reveal-token` 才显示 Token；普通路径优先使用 `provision`。
- Agent-wide revoke 同时撤销服务端凭据并尝试删除本机凭据；结果明确区分 `deleted`、`not_found`、`failed`。本地清理失败时 CLI 返回非零状态；服务端撤销仍是安全有效性的权威步骤。
- 单 Token `revoke --token-id` 会先定位该 Token 所属 Agent，再读取当前 Credential Manager 条目；仅当 Vault 中 Token 的 ID 与被撤销 ID 完全匹配时才删除。若 Vault 保存的是同一 Agent 的另一枚凭据，返回 `credential_cleanup=not_current` 并保留它，防止撤销旧 Token 时误删当前凭据；Vault 条目缺失与读取失败分别报告。
- provision、legacy issue、单 Token revoke、Agent-wide revoke 均通过当前 Windows 会话中按 Agent ID 哈希命名的本机 Mutex 串行化，避免并发签发 / 撤销交错导致新凭据被旧撤销流程清理。Mutex 对象名不包含 Agent ID 明文。
- Provisioning 通过 `AgentSecurityStore.issue_token(reject_if_active=True)` 在 SQLite `BEGIN IMMEDIATE` 事务内原子检查 Agent 是否已有活动凭据，避免并发 provision 为同一 Agent 创建多枚活动 Token；普通签发接口仍保留多凭据能力以支持后续受控轮换。
- IPC Server 停止时先进入排空态并跟踪活动 HTTP handler；排空期间新接受的连接会被立即关闭。使用默认 5 秒有界排空；若超时则恢复正常接纳请求、保留监听器和 Store、拒绝关闭 GUI。只有排空成功才停止 serve loop、关闭监听器、join 服务线程并关闭自身创建的 AgentSecurityStore。Handler 使用 daemon thread，避免 `server_close()` 隐式无限 join；外部注入的 Store 仍由调用方负责关闭。
- API 处理前先持久化 `admitted` 审计记录；Admission Audit 写入失败时请求在触及 Runtime 前以 503 拒绝。响应前再持久化最终结果；若最终审计写入失败，不返回原本的成功响应，而返回 `AGENT_AUDIT_FINALIZATION_FAILED` / HTTP 503，并将操作状态标记为 `unknown`，有 `request_id` 时要求先查询原请求、禁止盲目重提。
- 新增测试覆盖 Windows Credential Manager 真实写入 / 读取 / 删除 round-trip、Windows per-Agent Mutex 并发串行化、tokenless provisioning / 失败回滚 / 原子单活动凭据签发 / Agent 与单 Token 撤销清理状态 / Store 所有权 / 有界 Handler 排空 / Admission Audit 与 Final Audit 失败行为。
- 当前 PR #14 HEAD 的 CI：M2.4 Agent Credential Provider Run #32、MCP Gateway Run #79、Agent Security Run #70、Capture Process Inventory Run #218、Runtime Session Run #119 均最终 PASS；Python 3.10 / 3.11 / 3.12 与 Windows Credential Manager 集成测试均通过。Agent Security Python 3.12 首次 HTTP 集成运行出现一次 3 秒请求超时，单独重跑该失败 Job 后通过。
- M2.4.1 Host A Runbook 已记录 2026-11-20 在源提交 `35dd48dd162bd47db882de9137c870b388a58601` 上完成的 EXE SHA-256、Stage C–E 凭据 / MCP 只读验收以及 Stage F 正常关闭和重启验收。随后新增的有界 Shutdown、Final Audit 失败保护和单 Token 撤销清理代码尚未包含在该 EXE 中；必须对最新 PR HEAD 重新构建、检查 Artifact，并在另行授权后对更新 EXE 重做 Stage F。
- 尚未实现桌面管理 UI、自动生成 MCP Host 配置、自动轮换和现有配置迁移。

- 建立凭据 Provider 接口和 Windows Credential Manager 后端；
- Gateway 支持 Agent ID / credential reference；
- 保留旧环境变量兼容模式；
- 增加脱敏、故障闭锁和跨平台测试。

### M2.4.2 — 受信任注册与 Agent Registry
- 建立 Agent Registry 与本机 Provisioning Broker；
- 复用 M2.3 签发、Scope 和审计逻辑；
- 提供用户可见的添加/停用 Agent 管理入口；
- 新 Agent 注册后无需人工接触 Token。

### M2.4.3 — 轮换、撤销与迁移
- 自动轮换及失败回滚；
- 删除/停用时批量撤销；
- 迁移现有 OpenCode / Antigravity 配置；
- 验证凭据丢失、数据库不可用、服务重启和中断恢复。

## 9. 验收标准

1. 新增一个 Agent 的正常流程中，用户从未看到或复制 Token。
2. MCP 配置文件不含 Token；日志、stdout、审计和错误消息不含 Token。
3. 服务端仍对所有受保护 API 强制认证；无凭据返回 401，Scope 不足返回 403。
4. 每个 Agent 拥有独立 agent_id、Token、Scope、请求归属和审计记录。
5. 新 Agent 默认只读；高风险权限必须显式授权。
6. 凭据存储不可用、损坏或缺失时 fail closed。
7. 轮换先验证新凭据，再撤销旧凭据；失败不会造成无凭据窗口。
8. 停用/删除 Agent 后，其旧 Token 立即失效。
9. 现有 M2.3 环境变量模式在迁移期保持兼容。
10. AirBridge Core 不增加 Capture / Allegro 专用生命周期或 EDA 业务逻辑。
11. Windows CI 覆盖 Python 3.10 / 3.11 / 3.12；Gateway 现有 CI 版本继续通过。
12. Host A 实机验收只验证 Agent 注册、连接、轮换、撤销和只读访问；不发送 EDA / Shell 执行请求。

## 10. 非目标

- 不改变 ABFP、串口链路或 Runtime Session 协议；
- 不为每个 Agent 创建独立 Capture 进程或 Tcl Interpreter；
- 不开放公网/远程 Agent 注册；
- 不在本阶段授予 Shell / EDA 执行权限；
- 不修改 Capture Skill / Allegro Skill 生命周期；
- 不要求本阶段部署到 Host B；
- 不在 M2.4 中移除 M2.3 的旧 Token 环境变量兼容入口。

