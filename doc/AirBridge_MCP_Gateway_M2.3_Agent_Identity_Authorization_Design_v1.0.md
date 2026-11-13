# AirBridge M2.3：Agent 身份认证、权限控制与审计设计

- 日期：2026-11-13
- 版本：1.0
- 状态：M2.3.1 已在 feature/m2-agent-security 实现并通过 Windows CI（Python 3.10 / 3.11 / 3.12）；Host A 凭证配置和双 Host 安全验收待进行；配额与审计加固待 M2.3.2
- 代码基线：M1 `43c0900b5728d9d13710e265bd87647804be2227`
- 依赖阶段：M2.1 / M2.2 MCP Gateway 双 Host 安全模式验收已通过

## 1. 目标

为运行在 Host A 的多个本地 MCP Agent 建立可区分、可撤销、可审计的应用层身份，并在 AirBridge HTTP 服务端实施权限检查和配额限制。

首批客户端：
- `antigravity`
- `opencode`

本阶段不把 Agent 身份扩展为 Runtime Session 身份，不创建新的 Session，也不改变 M1 已验收的 Capture Runtime 生命周期、ABFP 协议或串口链路。

## 2. 当前代码事实与风险（M2.3.1 实施前基线）

代码审查确认：

1. `airbridge_app.py` 的 `AntigravityIPCServer` 通过 `ThreadingHTTPServer(("127.0.0.1", ...))` 监听 loopback；当前服务不会直接监听 LAN / 公网。
2. 现有 `GET /api/v1/runtime/capabilities`、`GET /api/v1/eda/versions`、`GET /api/v1/requests/{request_id}` 和 `POST /api/v1/eda/execute` handler 未验证 Authorization 身份。
3. `mcp_gateway/airbridge_mcp_gateway/config.py` 目前没有 Agent ID / Token 配置；`http_client.py` 请求也没有 Authorization Header。
4. `AIRBRIDGE_ENABLE_EDA_EXECUTE` 只在 Gateway 进程内短路执行工具。直接访问本地 HTTP API 的客户端不会经过该开关。
5. Host A 的 `RequestTracker` 默认记录保留期为 900 秒；MCP Gateway 的状态查询应依赖返回的 `state`，不能假定记录永久存在。
6. 多个 Gateway 子进程目前共用同一个 AirBridge HTTP 服务、同一个 Runtime Session 和同一个 Capture Interpreter。

因此，仅在 MCP 配置文件添加 Agent 名称、或只在 Gateway 检查权限，都不足以构成服务端身份认证与授权。\n\nM2.3.1 已在独立堆叠分支 `feature/m2-agent-security` 实现第一批服务端保护，但尚未部署到用户 Host A：SQLite 凭证库、一次性显示 Token 的本地管理 CLI、Gateway Bearer Header、HTTP route scopes、请求所有权绑定、服务端 EDA kill switch 和审计元数据已加入代码。GitHub Actions 的 Python 3.10 / 3.11 / 3.12 测试已通过；真实 Host A 凭证签发与 Antigravity / OpenCode 双 Token 验收仍待执行。

## 3. 威胁模型与信任边界

### 3.1 本阶段保护目标

- 区分不同 MCP Gateway 所代表的 Agent 凭证。
- 未认证客户端不能访问受保护的 `/api` 与 `/api/v1` 接口。
- 已认证 Agent 只能调用其 Token scopes 允许的操作。
- Agent 只能读取自己创建的请求状态与结果。
- 执行开关、服务端授权和 Agent scope 必须同时满足，才能提交 EDA 执行。
- 记录授权通过、授权拒绝、执行提交与执行结果状态；不记录明文 Token 或脚本正文。
- 对每个 Agent 实施请求频率与在途执行数限制。

### 3.2 明确不提供的保证

本方案提供的是同一 Host A 上、同一受信任 Windows 用户环境内的应用层 Agent 身份隔离，不是操作系统级沙箱。

若恶意程序已经以同一 Windows 用户身份执行，它可能读取该用户可访问的配置文件或进程信息。因此：
- 不把本方案描述为抵御同一 OS 用户下恶意代码的强隔离。
- 不允许把 MCP Gateway 或 AirBridge HTTP 服务暴露给 LAN / 公网。
- 若将来需要接入不可信远程 Agent，应另行设计 TLS、远程认证、密钥托管、网络边界和操作系统身份隔离。
- Agent 权限隔离不等于 Runtime Session / Capture Interpreter 隔离。

## 4. 身份凭证模型

### 4.1 每 Agent 独立 Token

每个 MCP Host 配置一枚不同的高熵随机 Bearer Token：
- `antigravity` 使用 Token A。
- `opencode` 使用 Token B。
- Token 由 Host A 本地管理员工具生成，不由 MCP 模型自行声明或生成。
- 建议使用至少 32 字节密码学安全随机数，并以 URL-safe Base64 输出。
- 服务端数据库只保存 Token 的 SHA-256 摘要，不保存明文。
- 明文 Token 只在生成时显示一次，写入对应 MCP Host 的本地配置。
- 支持单 Token 撤销、Agent 全部 Token 撤销和 Token 轮换。
- 不接受请求体参数或 `X-Agent-ID` 头所声明的身份；Agent ID 必须由服务端根据 Bearer Token 查表得出。

### 4.2 Gateway 配置

Gateway 新增：
- `AIRBRIDGE_AGENT_TOKEN`：必需的服务端凭证。
- `AIRBRIDGE_BASE_URL`：仍限制为 loopback。
- `AIRBRIDGE_ENABLE_EDA_EXECUTE`：保留作为 Gateway 侧本地执行开关。

不新增可由 Agent 工具参数任意修改的 Agent ID。Gateway 将 Bearer Token 放入 HTTP `Authorization: Bearer ...` Header，所有工具调用都使用相同的进程级 Token。

### 4.3 服务端凭证存储

建议使用独立 SQLite 数据库，默认位置：
- Windows：`%LOCALAPPDATA%\AirBridge\agent_security.sqlite3`
- 可通过 `AIRBRIDGE_AGENT_SECURITY_DB` 覆盖路径。

初始表：
- `agent_credentials`：token_id、agent_id、token_sha256、scopes、enabled、created_at、expires_at、revoked_at。
- `agent_audit`：timestamp、agent_id（允许为空，供未认证尝试）、action、request_id、decision、http_status、reason_code、duration_ms、payload_bytes、payload_sha256。
- `agent_request_owners`：request_id、agent_id、created_at。

数据库只保存 Token 摘要。数据库文件应继承当前 Windows 用户的本地配置目录 ACL；安装说明需明确不可将其放入仓库或同步到公共目录。

## 5. 服务端认证与授权

### 5.1 认证方式

使用标准 HTTP Header：

```http
Authorization: Bearer <agent-token>
```

服务端对 Token 计算 SHA-256，并匹配活动凭证。Agent ID、scope 和 enabled 状态均来自服务端凭证记录，而不是客户端传入的字段。

失败语义：
- 缺少、无效、过期或已撤销 Token：HTTP 401，`AUTHENTICATION_REQUIRED` 或统一的 `INVALID_AGENT_TOKEN`。
- Token 有效但缺少 scope：HTTP 403，`AGENT_SCOPE_DENIED`。
- 超出配额：HTTP 429，`AGENT_RATE_LIMITED`，尽可能返回 `Retry-After`。
- 认证/授权失败不得向 ABFP、Runtime Adapter 或 Capture 发送任何执行帧。

### 5.2 初始 Scope

| Scope | 可访问能力 |
|---|---|
| `runtime.read` | GET `/api/v1/runtime/capabilities` |
| `eda.versions.read` | GET `/api/v1/eda/versions` |
| `request.read` | GET `/api/v1/requests/{request_id}`，且只能读取本 Agent 的请求 |
| `eda.execute` | POST `/api/v1/eda/execute` |
| `runtime.manage` | Session 协商等管理操作；首批 Agent 不授予 |
| `shell.read` / `shell.execute` / `shell.manage` | Shell 能力；MCP 首批 Agent 不授予 |

未明确映射的 API 路径默认拒绝，不能因为某条 legacy 路径名称不同而绕过同一权限策略。特别是旧别名 `/api/eda/exec`、`/api/shell/exec` 和 Shell kill/list/task 路径必须纳入同一服务端访问控制，不得留下未认证执行旁路。

初始 Antigravity / OpenCode Token 均只授予：
- `runtime.read`
- `eda.versions.read`
- `request.read`

不授予 `eda.execute` 或任何 Shell scope。后续如需开放执行，必须单独审核并签发具备相应 scope 的 Token。

### 5.3 EDA 执行的三重门禁

远端 EDA 请求仅在以下条件全部成立时才允许提交：
1. Gateway 的 `AIRBRIDGE_ENABLE_EDA_EXECUTE=1`。
2. 服务端 Token 具有 `eda.execute` scope。
3. AirBridge 服务端全局执行开关 `AIRBRIDGE_ALLOW_EDA_EXECUTE=1`。

服务端全局执行开关默认关闭。Gateway 本地开关只是第一道门，不能替代服务端授权。Shell 执行不纳入首批 MCP 能力。

## 6. 请求归属和状态结果隔离

当前 `GET /api/v1/requests/{request_id}` 仅按 UUID 查询 RequestTracker。加入 Agent 身份后，必须同时校验请求归属：

1. 接受 EDA 执行请求前，服务端把 `request_id -> agent_id` 绑定写入 `agent_request_owners`。
2. 同一 Agent 可以查询自己请求的 pending / accepted / completed / failed / unknown 状态。
3. 其他 Agent 查询该 UUID 时返回与 not_found 等价的结果，不泄露请求是否存在、脚本或执行输出。
4. 没有归属记录的历史请求默认不向普通 Agent 开放；仅显式管理权限可访问。
5. 归属记录的保留期不得短于 RequestTracker 的状态可查询期；过期清理必须同步或采取安全的 deny-by-default 策略。

这项控制避免 Agent 通过共享、日志或误传 UUID 读取另一个 Agent 的执行结果。

## 7. 审计与隐私

审计记录应覆盖：
- 未认证访问、Token 无效、Scope 拒绝、配额拒绝。
- 只读查询及其 HTTP 状态。
- EDA 请求提交、request_id、最终状态和耗时。
- Token 签发、撤销、轮换等本地管理操作。

审计禁止记录：
- 明文 Bearer Token。
- Tcl / SKILL 脚本正文。
- 完整 HTTP Authorization Header。
- 未经必要性评估的工程内容或完整文件路径。

EDA 请求可记录 UTF-8 正文的字节数和 SHA-256 摘要，便于关联而不保存脚本内容。日志查询和导出应限制为本机管理员操作。

## 8. 配额与并发

M2.3 初始建议值（实现时应可配置，最终以测试和实际负载验证为准）：
- 普通 API 调用：每 Agent 60 次 / 分钟。
- Request Status 轮询：每 Agent 120 次 / 分钟。
- EDA 执行提交：每 Agent 5 次 / 分钟。
- 每 Agent 最多 1 个未完成 EDA 请求。
- 触发限额返回 HTTP 429；必须在服务端拒绝，不得只在 Gateway 限制。

多个 Agent 仍共享一个 Runtime Session。Runtime / Interpreter 的实际串行执行语义继续由 AirBridge 既有请求层保证。配额只能限制请求进入量，不能赋予每个 Agent 独立 Capture Interpreter，也不应宣称 Agent 之间拥有运行时隔离。

## 9. 模块建议

- `agent_security.py`：Token 认证、scope 校验、凭证签发/撤销、请求归属、审计和限流。
- `airbridge_app.py`：在 HTTP Handler 的 API 路由入口统一进行认证、授权和审计；先拦截再读取或处理写请求正文。
- `mcp_gateway/airbridge_mcp_gateway/config.py`：读取并校验 `AIRBRIDGE_AGENT_TOKEN`。
- `mcp_gateway/airbridge_mcp_gateway/http_client.py`：附加 Authorization Header。
- `mcp_gateway/airbridge_mcp_gateway/service.py`：将 401 / 403 / 429 保留为结构化业务结果，不自动重试写请求。
- `tools/airbridge_agent_admin.py`：本机管理员 CLI，支持 issue / list / revoke / rotate；明文 Token 仅签发时输出一次。
- `test/test_agent_security.py` 与 `mcp_gateway/tests/test_gateway.py`：覆盖服务端策略及 Gateway HTTP Header 契约。

不能把认证代码放进 Capture Tcl / Dbo、Allegro SKILL 或 Runtime Adapter 的专业业务逻辑；它属于 Host A HTTP 接入边界。

## 10. 实施分期与当前状态

### M2.3.1 — 身份认证与初始服务端授权：代码实现 / CI PASS

已在 `feature/m2-agent-security` 实现：
- SQLite 凭证库；Token 使用密码学安全随机值，数据库仅存 SHA-256 摘要。
- 本地 CLI 支持 issue / list / revoke / audit；签发时明文 Token 只显示一次。
- Gateway 必须配置独立的 `AIRBRIDGE_AGENT_TOKEN`，并为所有 HTTP API 请求附加 Bearer Header。
- AirBridge HTTP Handler 统一认证 `/api/` 路由；未认证返回 401，scope 不足返回 403，未分类路由默认要求 `api.admin`。
- 初始支持 runtime / EDA versions / request / EDA execute / Shell / filesystem scopes；初始 Agent Token 仅规划只读三个 scope。
- EDA / Shell request_id 与 agent_id 绑定；其他 Agent 查询该 ID 返回等同 not_found 的 404。
- 服务端 `AIRBRIDGE_ALLOW_EDA_EXECUTE` kill switch 默认关闭，与 Gateway 本地开关和 Token scope 形成三重门禁。
- 审计表记录身份、路径、状态、请求 ID、正文长度和 SHA-256，不存 Token 或脚本正文。
- Windows CI：Agent Security 的 Python 3.10 / 3.11 / 3.12 全部 PASS；MCP Gateway 的 Python 3.11 / 3.12 全部 PASS。

尚待：Host A 安装、分别签发 Antigravity / OpenCode Token、更新两端 MCP 配置和实机验收。未启用真实 EDA 执行。

### M2.3.2 — 审计加固、配额与生命周期治理：待实现
- 审计写入失败时的 fail-closed admission / finalization 语义。
- 审计保留期、请求归属记录清理和数据容量策略。
- 每 Agent API 速率限制、EDA 提交速率及最大在途执行限制，超限返回 429。
- 审计 CLI 查询和导出边界测试。
- Token 撤销、轮换、过期和并发访问压力测试。

### M2.3.3 — 双 Host 安全验收：待实机
- Antigravity / OpenCode 分别使用不同 Token。
- 两个只读 Token 均可读取各自允许的三项接口。
- 无 Token、错误 Token、撤销 Token、scope 不足分别得到预期 401 / 403。
- Agent A 不能查询 Agent B 的 request_id。
- 只读 Token 调用 EDA Execute 得到 403；Gateway 关闭开关得到本地 disabled；服务端总开关关闭时即使 Gateway 打开也不能提交。
- 超限返回 429，且没有执行帧。
- 审计可按 Agent / request_id 查询，不包含脚本正文和 Token。
- A/B 实机确认 AirBridge Runtime 正常，未触发未授权的 Capture 操作。

## 11. 验收边界与后续

M2.3 完成后，能够对两个本地 MCP Agent 进行应用层身份识别、最小权限授权、请求结果归属检查、审计和基础限流。

仍不等于：
- 每 Agent 独立 Runtime Session。
- 每 Agent 独立 Capture 进程 / Tcl Interpreter。
- 对同一 Windows 用户下恶意本地进程的强隔离。
- 可安全接入公网或不可信远程 Agent。

每 Agent Session / Interpreter 隔离应另立后续阶段，不能由 Agent Token 或 MCP Gateway 代替。

## 12. 源码审查依据

本设计基于当前代码：
- `airbridge_app.py`：`AntigravityIPCServer` 的 GET / POST 路由与 loopback bind。
- `request_tracker.py`：`RequestTracker.DEFAULT_TTL_SECONDS = 900`。
- `request_broker.py`：B 侧持久化去重 Broker；它不等于 A 侧 Agent 身份或请求所有权机制。
- `mcp_gateway/airbridge_mcp_gateway/config.py`：目前只有 Base URL、HTTP timeout、Gateway 执行开关。
- `mcp_gateway/airbridge_mcp_gateway/http_client.py`：目前尚未附加 Agent Authorization Header。
