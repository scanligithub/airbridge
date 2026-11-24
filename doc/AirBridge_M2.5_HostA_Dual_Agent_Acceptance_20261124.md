# AirBridge M2.5：Host A 双 Agent 独立配置、权限与启停验收记录

- 验收日期：2026-11-24
- 仓库：`scanligithub/airbridge`
- 验收分支：`docs/m2.5-dual-agent-acceptance`
- 环境：Windows Host A；AirBridge GUI / MCP STDIO Adapter；Antigravity 与 OpenCode
- 验收性质：Host A 真实客户端与 Gateway 集成验收；只读安全验证
- 结果：本记录列明的测试项通过；“未覆盖边界”不得视为通过

## 1. 验收目标与安全约束

验证 Antigravity 和 OpenCode 能否通过同一 AirBridge 安装分别连接 Gateway，并满足：

1. 使用独立 Agent 身份及独立凭据；
2. 仅拥有只读权限；
3. capabilities / EDA versions 等只读请求可正常工作；
4. EDA Execute 请求被服务端安全策略拒绝；
5. GUI 中单独关闭 Antigravity 时，其 Adapter 配置同步禁用，而 OpenCode 不受影响；
6. 重新启用 Antigravity 后，配置持久化且真实 MCP 调用恢复。

本轮严格未执行 EDA Tcl / SKILL、Shell、文件写入或其他修改性操作。未调用 `airbridge_eda_execute` 以外的执行类工具；所有 EDA Execute 拒绝证据来自此前已产生的审计记录，不代表本轮实际执行了设计脚本。

## 2. 测试环境与凭据配置

### 2.1 Gateway 与 Adapter

- Gateway 策略文件：`%LOCALAPPDATA%\AirBridge\gateway.json`
- Antigravity Adapter 配置：`%USERPROFILE%\.gemini\config\mcp_config.json`
- OpenCode Adapter 配置：`%USERPROFILE%\.config\opencode\opencode.json`
- Agent Security DB：`%LOCALAPPDATA%\AirBridge\agent_security.sqlite3`

Gateway 使用同一台 Host A 上的 AirBridge 安装，分别配置：
- Agent ID：`antigravity`
- Agent ID：`opencode`

两个 Agent 各自具有 Windows Credential Manager Generic Credential：
- `AirBridge/Agent/antigravity`
- `AirBridge/Agent/opencode`

SQLite 只读检查确认两个 Agent 各有一条有效凭据，均未撤销，权限集合均为：

```json
["eda.versions.read", "request.read", "runtime.read"]
```

数据库中还存在一条旧 Antigravity 凭据，其状态为 disabled / revoked。旧凭据不作为本轮有效身份使用。本文不记录任何 Token secret，也不记录完整凭据材料。

### 2.2 Gateway 状态与工具边界

- Gateway 总开关：ON
- 两个 Agent 的策略开关：分别控制
- 每个 MCP Adapter 提供四个工具：
  - `airbridge_get_capabilities`
  - `airbridge_get_eda_versions`
  - `airbridge_request_status`
  - `airbridge_eda_execute`

其中 EDA Execute 属于执行入口；本轮只核查其拒绝审计，不执行任何 EDA 内容。

## 3. 独立身份与真实 MCP 调用

### 3.1 OpenCode

OpenCode 通过 `airbridge-opencode` 成功调用 `airbridge_get_capabilities`：

| 字段 | 实际结果 |
|---|---|
| state | `negotiated` |
| negotiated | `true` |
| protocol | `runtime-session.v1` |
| peer_version | `airbridge-v2.4` |
| http_status | `200` |
| error | `null` |
| can_create_session | `false` |
| max_frame_payload | `65000` |

Gateway 审计记录与 Agent Credential 表 JOIN 后，确认该成功请求使用的是 `host-a-opencode` 对应的有效 OpenCode 凭据，scope 为上述只读集合。

### 3.2 Antigravity

Antigravity 通过 `airbridge-antigravity` 成功调用 `airbridge_get_capabilities`，结果：

| 字段 | 实际结果 |
|---|---|
| state | `negotiated` |
| negotiated | `true` |
| protocol | `runtime-session.v1` |
| peer_version | `airbridge-v2.4` |
| http_status | `200` |
| error | `null` |
| can_create_session | `false` |
| max_frame_payload | `65000` |

恢复启用后的最后一次调用同样返回 HTTP 200、`negotiated=true`、`error=null`。审计记录与 Credential 表 JOIN 后，确认成功请求使用的是 `host-a-antigravity` 对应的有效 Antigravity 凭据，scope 为上述只读集合。

两客户端的 capabilities 响应均包含：
- `handshake`
- `request.correlation`
- `request.deduplication`
- `shell.task.result-query`

两者的 `profiles=[]`，不应解读为已提供专用 EDA Profile。Capabilities 握手成功也不等于 EDA 执行已开放。

### 3.3 Request Status 的 404 解释

OpenCode 曾有 Request Status 请求返回 HTTP 404、错误码 `REQUEST_NOT_FOUND_OR_NOT_OWNED`。该结果可能表示请求不存在或不属于当前 Agent，不能单独作为 scope 拒绝成功的证据，也不能据此认定请求所有权隔离已经完整验收。

## 4. EDA Execute 安全拒绝审计

SQLite 审计记录共发现三条 EDA Execute 拒绝事件，分别关联到各自 Agent 的有效凭据：

| Agent | HTTP | 拒绝原因 | 结论 |
|---|---:|---|---|
| Antigravity | 403 | `AGENT_SCOPE_DENIED` | 只读 Token 不具备执行 scope |
| Antigravity | 403 | `EDA_EXECUTION_DISABLED_SERVER` | 服务端执行总开关关闭 |
| OpenCode | 403 | `AGENT_SCOPE_DENIED` | 只读 Token 不具备执行 scope |

这组记录证明权限 scope 与服务端执行开关是两道独立的安全门。不能把服务端关闭导致的拒绝全部归因于 Token scope。

本轮没有执行 Tcl / SKILL 或其他 EDA 脚本，没有保存、修改或关闭设计文件。

## 5. GUI 独立关闭与恢复实测

### 5.1 关闭 Antigravity

用户通过 AirBridge GUI 关闭 Antigravity，GUI 日志：

```text
[16:16:00] [INFO] 已保存本机 Gateway 策略：ON, Antigravity=False, OpenCode=True
[16:16:00] [INFO] 已同步已安装的 MCP Adapter：antigravity: disabled
```

随后直接读取策略和 Adapter 配置文件，确认：

```text
GATEWAY_ENABLED=True
ANTIGRAVITY_POLICY_ENABLED=False
OPENCODE_POLICY_ENABLED=True
ANTIGRAVITY_ADAPTER_PRESENT=True
ANTIGRAVITY_ADAPTER_DISABLED=True
```

关闭期间，Antigravity 的 MCP 工具已从客户端工具注册表卸载。尝试调用时，客户端返回工具验证错误（unknown tool / `call_mcp_tool` 不可用），该请求没有到达 Gateway，因此不是服务端 HTTP 403 证据。

同一关闭窗口内，OpenCode 仍成功调用 `airbridge-opencode/airbridge_get_capabilities`，返回 HTTP 200、`negotiated=true`、`error=null`。这证明关闭 Antigravity 的策略和 Adapter 没有破坏 OpenCode 的连接与只读调用。

### 5.2 恢复 Antigravity

用户重新启用 Antigravity，GUI 日志：

```text
[16:23:24] [INFO] 已保存本机 Gateway 策略：ON, Antigravity=True, OpenCode=True
[16:23:24] [INFO] 已同步已安装的 MCP Adapter：antigravity: enabled
```

随后只读核验文件，结果：

```text
GATEWAY_ENABLED=True
ANTIGRAVITY_POLICY_ENABLED=True
OPENCODE_POLICY_ENABLED=True
ANTIGRAVITY_ADAPTER_DISABLED=False
```

最后在 Antigravity 中仅调用一次 `airbridge-antigravity/airbridge_get_capabilities`，成功返回：

```json
{
  "state": "negotiated",
  "negotiated": true,
  "protocol": "runtime-session.v1",
  "peer_version": "airbridge-v2.4",
  "error": null,
  "can_create_session": false,
  "http_status": 200
}
```

因此，策略文件和 Adapter 配置已持久化恢复，Antigravity 真实 MCP 工具调用也已恢复。

## 6. 验收结果汇总

| 编号 | 验收项 | 结果 | 证据 |
|---|---|---|---|
| A-01 | OpenCode 独立 MCP 连接 | PASS | capabilities HTTP 200 |
| A-02 | Antigravity 独立 MCP 连接 | PASS | capabilities HTTP 200 |
| A-03 | 两个 Agent 使用独立有效凭据 | PASS | Credential Manager + SQLite 只读检查及审计关联 |
| A-04 | 两个 Agent 仅持有只读 scopes | PASS | SQLite 凭据记录 |
| A-05 | EDA Execute 受到安全拒绝 | PASS | 两个 Agent 的 HTTP 403 审计事件 |
| A-06 | Antigravity OFF 策略持久化 | PASS | GUI 日志 + 策略文件 |
| A-07 | Antigravity Adapter 同步禁用 | PASS | GUI 日志 + Adapter 配置文件 |
| A-08 | Antigravity OFF 时 OpenCode 不受影响 | PASS | OpenCode capabilities HTTP 200 |
| A-09 | Antigravity ON 策略持久化 | PASS | GUI 日志 + 策略文件 |
| A-10 | Antigravity Adapter 同步恢复 | PASS | GUI 日志 + Adapter 配置文件 |
| A-11 | Antigravity 恢复后真实调用成功 | PASS | capabilities HTTP 200 |

## 7. 未覆盖边界与禁止过度推断

### 7.1 已建立 STDIO 会话的服务端即时拦截

**未验收。**

关闭 Antigravity 时，Adapter 被禁用，客户端工具注册表随之卸载。工具调用在客户端验证阶段失败，没有向 Gateway 发出请求。因此本轮没有证明：对于已经建立且仍存活的 STDIO 会话，服务端是否会在策略切换为 OFF 后立即拒绝后续请求。

后续如需验证，应设计能保留现有 STDIO 会话、并确认请求确实抵达 Gateway 的专项测试；必须使用无副作用的只读请求，禁止借此执行 EDA / Shell 或修改文件。

### 7.2 其他边界

- 本记录不证明跨机器 HTTPS、证书或远程网络部署场景。
- 本记录不证明 Request Status 的所有权隔离完整性；404 结果存在“请求不存在或不属于当前 Agent”的歧义。
- 本记录不证明 EDA 执行功能可用；相反，本轮确认执行入口被安全拒绝。
- 本记录不覆盖 Host B Capture / Allegro 生命周期，也不改变 ADR-006 所确立的 Core 与 Skill 职责边界。
- 相同的 `last_handshake_id` 不能单独用来判断客户端是否重连。

## 8. 最终结论

截至 2026-11-24，Host A 上 Antigravity 与 OpenCode 的独立配置、独立身份凭据、只读权限、真实 capabilities 调用、EDA Execute 安全拒绝、GUI 独立关闭、关闭期间另一 Agent 保持可用、重新启用及恢复调用，均有实际运行证据。

**本轮双 Agent 独立启停与恢复验收通过。**

“已建立 STDIO 会话的服务端即时策略拦截”仍为明确未验收项，应单独跟踪，不得纳入本轮 PASS 范围。
