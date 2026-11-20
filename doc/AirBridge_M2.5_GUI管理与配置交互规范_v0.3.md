# AirBridge M2.5：Gateway GUI 管理与配置交互规范

- 文档编号：AB-M2.5-GUI-001
- 版本：0.3（首批 Adapter 范围与用户配置边界修订稿）
- 日期：2026-11-20
- 状态：目标交互规范；GUI 尚未实现
- 关联文档：《AirBridge M2.5：统一运行时与 Gateway GUI 管理架构设计》

## 1. 设计目标

AirBridge GUI 必须成为 Gateway 的统一管理控制台。用户无需手工创建 Python 虚拟环境、手工运行 Gateway 命令或直接编辑 MCP JSON，即可完成 Gateway 日常管理。

主机 A 和主机 B 的 GUI 页面、功能和选项完全相同。两台主机的初始配置可以不同，但不得根据主机环境隐藏功能或自动推断角色。

## 2. 导航结构建议

在 AirBridge 主界面增加 Gateway 管理入口，页面分为：

1. Gateway 总览
2. 服务与传输配置
3. Agent 管理
4. MCP 客户端配置
5. 日志与诊断

页面入口在 A/B 两端均可见；Gateway OFF 时仍允许查看和编辑配置、初始化 Agent、诊断配置，不启动服务。

## 3. Gateway 总览

总览至少显示：

| 字段 | 说明 |
|---|---|
| 本机 Gateway 开关 | 用户配置的 ON / OFF |
| 实际运行状态 | Stopped / Starting / Running / Stopping / Failed / Degraded |
| 启动策略 | 是否随 AirBridge 启动 |
| Worker 数量 | 当前运行、已启用和失败的实例数量 |
| Agent | 每个已注册 Agent 实例的独立状态；M2.5 首批专用 Adapter 仅覆盖 Antigravity / OpenCode |
| 传输方式 | 当前配置的 stdio / Streamable HTTP 等 |
| Endpoint / 端口 | 仅展示实际绑定成功的 endpoint |
| PID | 对应 Worker 的真实进程 ID |
| 运行时长 | Worker 本次运行时长 |
| 版本 | AirBridge / Gateway / MCP SDK 版本 |
| 最近错误 | 最近一次启动或健康检查错误摘要 |
| 双机配置状态 | 本机 / 对端 ON / OFF / UNKNOWN、当前连接代次和冲突状态 |

必须将“配置已启用”与“服务正在运行”分开显示。例如：
- Enabled + Running：已启用且运行正常；
- Enabled + Failed：配置要求运行，但启动失败；
- Disabled + Stopped：用户主动关闭；
- Disabled + Running：状态异常，应触发受管进程收敛。

## 4. 服务控制

提供：
- 启动 Gateway；
- 停止 Gateway；
- 重启 Gateway；
- 刷新状态；
- 查看 Worker 日志。

按钮行为：
- Starting 时禁用重复启动；
- Stopping 时禁用重复停止/重启；
- Failed 时允许查看诊断并重新启动；
- 启动前校验 Agent 凭据、端口和配置；
- 停止前显示在途请求数量；排空超时则明确报告，不得谎报停止成功；
- 所有操作显示实际结果，不仅根据按钮点击推断成功。

## 5. Gateway 配置

### 5.1 总开关

`Gateway enabled` 是本机总开关：
- **首次安装默认 OFF**：安装后的初始配置为关闭状态，不自动启动任何 Gateway Worker；用户必须在 GUI 中明确启用。
- ON：允许 Gateway Supervisor 启动已启用 Agent 的 Worker。
- OFF：停止所有受管 Worker；不关闭 AirBridge HTTP API、Runtime Session 或串行通信。
- 升级安装不得把已有 Gateway 开关重置为默认值；应保留用户已保存的 ON / OFF 设置。
- 配置更改必须持久化；写入失败时恢复 UI 原值并提示错误。

### 5.2 随 AirBridge 启动

提供独立选项 `gateway.start_with_airbridge`：
- **默认 ON（`true`）**：AirBridge GUI 启动后，仅当 Gateway 总开关 ON 且对应 Agent 实例已启用时，Supervisor 才启动其 Worker。
- OFF（`false`）：AirBridge GUI 启动时不自动启动 Gateway Worker；用户仍可在 GUI 中手动启动。
- 该选项不能覆盖 `gateway.enabled` 总开关。首次安装时 `gateway.enabled=false`，所以首次启动仍不会运行任何 Worker。
- **M2.5 首版不实现 Windows 登录自动启动 AirBridge GUI**，不创建注册表 Run 项、Startup 快捷方式或计划任务。Windows 登录自启动与 GUI 启动 Gateway 是两个独立功能，前者留待后续版本评估。

### 5.3 Agent 实例开关

每个已注册 Agent 实例独立启用。

M2.5 首批专用 Adapter 仅为 Antigravity、OpenCode。其他产品的专用 Adapter 留待后续版本评估。Generic / Manual 通用 MCP Client 手动接入已确认不纳入首版。

Agent enabled 仅表示允许为该 Agent 启动 Worker；仍必须有有效凭据和服务端权限策略。

### 5.4 传输与网络

托管模式采用 Streamable HTTP，并要求 MCP Resource Server endpoint 使用 HTTPS，以保护 Authorization Bearer Access Token。OAuth Authorization Server endpoint 同样使用 HTTPS；HTTP 仅作为规范允许的 loopback redirect URI 例外，不用于 MCP 资源请求。页面可显示：
- transport；
- listen host；
- port；
- MCP endpoint；
- 访问认证状态。

安全限制：
- 初版只允许 loopback；
- 对客户端显示的 MCP endpoint 使用稳定 HTTPS hostname（建议 `localhost`），并验证 SAN / 证书链；
- 不允许用户通过 GUI 将 Gateway 直接暴露到 LAN / 公网；
- loopback 不是身份认证，启动前必须完成本地客户端访问认证设计；
- 端口冲突必须明确报错，不能静默改成任意端口后不更新 MCP 客户端配置。

**M2.5 默认 TCP 端口：**

| 服务 | 默认端口 | HTTPS Endpoint |
|---|---:|---|
| OAuth Broker | 38861 | `https://localhost:38861/` |
| 首个 Antigravity Worker | 38862 | `https://localhost:38862/mcp` |
| 首个 OpenCode Worker | 38863 | `https://localhost:38863/mcp` |

端口管理要求：
- AirBridge HTTP API 继续使用现有 `127.0.0.1:8888`；不得与 Gateway Broker / Worker 共用端口。
- Broker 和每个 Agent Worker 使用独立 TCP 端口；同一客户端类型可以注册多个 Agent 实例，每个实例必须有独立端口。
- 默认候选池为 `38801–38864`；IANA 当前将其标记为 Unassigned（非 AirBridge 保留端口），发布时须复核登记状态。新增实例自动选择池内未分配且通过本机检查的空闲端口，并持久化。用户可在 GUI 主动修改为池内其他空闲端口。
- 服务只绑定 loopback（`127.0.0.1` / `::1`）；客户端 endpoint 使用 `localhost`。不得绑定通配地址或自动开放 LAN / 公网访问。
- 启动前做实际 TCP bind / 占用验证，并检查系统当前动态端口和 excluded port range。不能只依赖进程列表或默认 Windows 端口范围。
- 冲突时 GUI 显示端口和可取得的进程名称 / PID；Broker 冲突阻断 Managed HTTPS OAuth，Worker 冲突仅阻断对应 Agent。不得静默换随机端口；可让其它独立 Worker 继续运行。
- 用户主动修改端口时，先验证新端口，再同步 endpoint、Resource / issuer metadata 与相应 MCP Client 当前用户配置。写入前备份、原子更新；任一步骤失败回滚。
- Worker 端口变更导致 Resource Audience 改变；Broker 端口变更导致 OAuth issuer 改变。两种情况均要求对应 Client 重新 discovery / OAuth 授权，旧 Grant 不得直接迁移到新 Resource / issuer。
- 证书 SAN 绑定 hostname（`localhost`），不包含端口；端口变化不改变 SAN，但仍必须进行完整 TLS 校验。
- 首次安装采用上述默认端口；升级安装保留用户已经配置的端口。

### 5.5 本机 HTTPS 证书与用户信任

- 安装 / 初始配置时，GUI 必须清楚说明本机证书用途、信任范围和影响，并在用户明确同意后才导入。
- 证书信任只写入当前 Windows 用户（CurrentUser）范围；不要求管理员权限，不修改 LocalMachine 证书存储区或 LocalMachine Root。
- GUI 显示证书状态、有效期、SAN / hostname 匹配、信任状态及可执行的修复引导；不得将“证书已导入”直接显示为“所有 MCP 客户端均已信任”。
- 用户拒绝导入、证书失效或用户撤销信任时，托管 Streamable HTTP / OAuth HTTPS Gateway 标记为不可用，说明原因和恢复方式。
- 不得提示用户忽略证书错误，不得提供关闭 TLS 校验的选项，不得降级为无认证 HTTP。
- 证书信任问题不得关闭既有 stdio 兼容路径或 AirBridge Core / Runtime。

## 6. Agent 管理

每个 Agent 行展示：
- Agent ID；
- 显示名称；
- 是否启用；
- 凭据状态（有效 / 缺失 / 已撤销 / 读取失败 / 未验证）；
- 已授权 Scope；
- Worker 运行状态；
- 最近连接时间；
- 最近错误。

操作：
- 添加 / 初始化 Agent；
- 启用 / 停用 Agent；
- 查看权限；
- 授予或撤销敏感权限（必须二次确认）；
- 轮换凭据；
- 撤销 Agent 凭据；
- 运行连接诊断。

安全 UX：
- 不提供 Token 明文查看/复制入口；
- 不在普通配置页面显示秘密；
- 新 Agent 默认只读；
- Scope 变更明确展示新增/移除权限并要求确认；
- 凭据读取失败时 fail closed，不得提供“跳过认证继续运行”按钮。

## 7. MCP 客户端配置

### 7.1 客户端发现

M2.5 首批自动发现与自动配置仅支持：
- Antigravity；
- OpenCode。

Cursor、VS Code / Copilot、Cline 等不属于首批专用 Adapter 范围。Generic / Manual 通用 MCP Client 手动接入不纳入首版，GUI 不得展示相应添加入口。

发现结果需区分：
- 已安装且找到配置；
- 已安装但未找到配置；
- 未检测到客户端；
- 配置文件格式错误；
- 配置文件不可读/不可写。

不得因为未检测到客户端而阻止 Gateway 自身运行。

### 7.2 配置写入

自动配置流程：
1. 读取现有客户端配置；
2. 解析并校验格式；
3. 展示将新增/更新的 AirBridge MCP Server 项；
4. 备份原文件；
5. 仅新增或更新 AirBridge 对应条目；
6. 保留其他 MCP Server、用户自定义字段和无关配置；
7. 原子写入；
8. 重新读取并验证；
9. 展示成功/失败和备份位置。

如果格式未知、解析失败或不能安全合并，必须停止写入并提示用户；不得覆盖整个文件。配置文件中不得写入 Agent Token。

### 7.3 客户端连接验证

提供分层诊断：
- 配置文件可解析；
- endpoint 可达；
- 本地访问认证通过；
- MCP initialize 成功；
- tools/list 成功；
- 指定只读工具调用成功。

连接验证默认使用只读工具，不调用 EDA execute、Shell execute 或文件写入能力。

## 8. 日志与诊断

日志页面支持按时间、Agent、Worker、级别筛选。展示：
- Worker 启停；
- PID / 端口绑定；
- MCP initialize / tools/list；
- Agent 认证和 Scope 拒绝的非秘密摘要；
- HTTP API 依赖状态；
- 崩溃退出码；
- 端口冲突；
- 配置解析和写入结果。

禁止记录：
- Bearer Token、凭据内容、Authorization Header；
- 用户密码；
- 完整脚本正文；
- 不必要的工程数据和文件内容。

日志应可导出脱敏诊断包；导出前提示用户检查敏感路径或环境信息。

## 9. 双机状态与提示

在 AirBridge 通信状态页显示：
- 本机 Gateway：ON / OFF；
- 对端 Gateway：ON / OFF / 未知；
- 最后交换时间；
- 协议能力是否支持 Gateway 状态字段。

收到双方状态后：
- ON/OFF：显示状态互补，不提示冲突；
- OFF/ON：显示状态互补，不提示冲突；
- ON/ON：显示配置冲突；
- OFF/OFF：显示配置冲突；
- UNKNOWN / 未协商 / 链路断开 / 旧版字段缺失 / 状态过期：不弹窗，不按 OFF 处理。

弹窗建议文案：

**Gateway 配置冲突**

本机与对端的 Gateway 启用状态相同。通常双机部署应由一端启用 Gateway，另一端关闭 Gateway。

请分别检查两端 AirBridge GUI 中的 Gateway 开关。本提示不会自动修改配置或停止服务。

**弹窗去重规则：**
- 同一 Runtime Session 连接代次内，每次进入一个新的冲突状态时，只弹一次；重复 HELLO / CAPABILITIES、重传或重复状态更新不能重复弹窗。
- 状态恢复为互补 / 非冲突时，清除当前冲突锁存；后续再次进入冲突可再次弹窗。连接代次变化时重置去重状态。
- 弹窗仅提供“稍后处理”和“打开 Gateway 设置”。“稍后处理”只关闭当前模态弹窗，不视为忽略冲突。
- M2.5 首版不提供“忽略本次会话”或永久忽略按钮。

**持续状态提示：** Gateway 总览和通信状态页保留非模态冲突提示，直到状态恢复或变为 UNKNOWN。关闭弹窗后，该提示仍然可见。

如果对端状态未知、链路未连接或对端协议版本不支持该字段，不弹冲突窗，只显示“尚未获取对端 Gateway 状态”；断线时清除旧连接代次的冲突提示。

## 10. GUI 状态模型

建议统一状态枚举：
- DISABLED：总开关关闭；
- STOPPED：已启用但尚未启动；
- STARTING：启动中；
- RUNNING：健康检查通过；
- DEGRADED：进程存在但依赖或健康检查异常；
- STOPPING：正在排空和关闭；
- FAILED：启动失败或进程异常退出。

状态来源必须是 Supervisor 对进程、健康检查和配置的真实观察。GUI 不得仅依据配置值推断 Worker 已运行。

## 11. 可访问性与可维护性

- 关键状态同时使用文本和图标/颜色表达，不只依赖颜色；
- 启停操作显示进行中状态和结果；
- 所有错误提供可复制的非秘密诊断代码；
- 配置变更有明确保存反馈；
- 页面刷新不应触发服务重启；
- UI 线程不得执行阻塞式网络调用、进程等待或配置文件扫描；
- 日志刷新和健康检查应有频率上限，不能影响 AirBridge 串行通信与 GUI 响应。

## 12. GUI 验收清单

- [ ] A/B 两端显示相同 Gateway 页面和相同功能入口。
- [ ] 首次安装完成后 Gateway 总开关为 OFF，且没有任何 Gateway Worker 自动启动。
- [ ] 升级安装保留已有 Gateway 总开关配置。
- [ ] Gateway OFF 时所有管理页面仍可访问，AirBridge Runtime 正常。
- [ ] 总开关、Agent 开关和随 AirBridge 启动配置正确持久化。
- [ ] GUI 能准确显示配置状态与实际运行状态。
- [ ] 启动、停止、重启和失败恢复反馈真实。
- [ ] 能显示每个 Worker 的 PID、endpoint、端口和最近错误。
- [ ] Agent 凭据状态、Scope 和撤销状态正确。
- [ ] 首批专用 Adapter 仅包含 Antigravity 和 OpenCode。
- [ ] 首版 GUI 不提供 Generic / Manual 通用 MCP Client 添加入口。
- [ ] MCP Adapter 自动写入仅限当前 Windows 用户级配置；项目级 / 系统级配置不被修改。
- [ ] MCP 配置备份、合并、验证与恢复有效，其他 Server 配置不丢失。
- [ ] 日志脱敏，不出现 Token 或 Authorization Header。
- [ ] 双机 ON/ON 与 OFF/OFF 弹窗正确；ON/OFF 与 OFF/ON 不弹冲突。
- [ ] 未建立链路、对端未知和旧协议场景不误报。
- [ ] Gateway 故障不会导致 GUI 假死或 AirBridge Runtime 停止。

---


## 13. 2026-11-20 MCP Host 配置与授权交互补充（v0.2）

### 13.1 客户端配置 Adapter

Antigravity 与 OpenCode 使用不同的配置结构，GUI 必须分别实现 Adapter。

**Antigravity：**
- App 优先读取用户从 Antigravity Customizations / MCP Servers 页面复制的配置路径或通过已验证方式发现路径；不猜测 App 专用目录。
- CLI 的 global 配置为 `~/.gemini/config/mcp_config.json`，workspace 配置为 `<workspace>/.agents/mcp_config.json`。
- 自动配置仅写入当前 Windows 用户级 global / App 用户配置；workspace 配置只检测并提示，不自动修改。
- 远程服务器写入 `mcpServers.<name>.serverUrl`；需要依据实际产品版本决定 OAuth / CIMD / DCR 行为。
- Antigravity IDE 的 CIMD 支持要求 2.6.0+；旧版不能直接据此判定 OAuth 不可用，应继续验证其 DCR 支持或提示升级。

**OpenCode：**
- V2 远程配置位于 `mcp.servers.<name>`，字段为 `type: "remote"`、`url`。
- 用户全局配置通常位于 `~/.config/opencode/opencode.json(c)`；Windows 系统级配置目录为 `%ProgramData%\\opencode`；项目级配置可覆盖同名 Server。
- GUI 自动配置只修改当前 Windows 用户级 global 配置。系统级和项目级配置即使被发现，也只显示路径、优先级或冲突提示；不自动修改，也不提供首版自动写入入口。
- OpenCode 远程 OAuth 默认启用，不应设置 `oauth: false` 来绕开安全授权。

### 13.2 凭据显示与 OAuth 授权

GUI 必须明确区分：
- **Agent Service Credential**：AirBridge 内部凭据，存在 Windows Credential Manager，不向用户显示明文，不写入 MCP JSON。
- **MCP Host OAuth Authorization**：由 Antigravity / OpenCode 与 Gateway Authorization Server 完成，客户端保存其 OAuth 授权状态。GUI 只显示“未授权 / 授权中 / 已授权 / 已撤销 / 失败”，不读取或显示 Access Token / Refresh Token。

GUI 可以打开对应客户端的授权界面或触发客户端标准重新连接，但不得假装自己能代替客户端保存其私有 OAuth Token。

### 13.3 JSONC / 多层配置安全

OpenCode 支持 JSON 和 JSONC，且全局、系统、项目配置存在优先级。配置写入器必须识别 JSONC、保留未管理条目和未知字段；若不能安全保留注释/格式，应先备份并向用户明确说明，不能静默覆盖。

配置 Adapter 必须报告：
- 实际写入路径与路径来源；
- 配置层级及优先级；
- 是否严格属于当前 Windows 用户级配置（否则不得写入）；
- 是否存在同名 AirBridge Server 冲突；
- 是否需重载/重启客户端才能生效。

### 13.4 本地 HTTPS 授权服务的 GUI 状态

在 OAuth Authorization Server HTTPS 原型通过前，GUI 不得显示“本地 MCP OAuth 安全授权已就绪”。至少应显示：
- 当前用户证书信任状态（未安装 / 等待同意 / 已信任 / 用户拒绝 / 失效 / 已撤销）；
- Broker 与 Worker 证书的 SAN / 有效期 / TLS 健康状态；
- 托管 HTTPS Gateway 是否因证书信任未完成而不可用。
- Gateway Worker 进程状态；
- MCP Resource Server endpoint 状态；
- Authorization Server / TLS 状态；
- Agent Service Credential 状态；
- MCP Host OAuth 授权状态。

上述状态不可合并成单一绿色“Gateway 正常”指示灯。


## 13. 2026-11-20 OAuth 用户交互与授权状态规范（v0.3）

### 13.1 配置和授权是两个不同步骤

GUI 的 MCP Client 页面必须将以下动作分开显示：
1. **写入 MCP Server 配置**：将对应 HTTPS endpoint 写入 Antigravity / OpenCode 的正确配置文件。
2. **客户端 OAuth 授权**：用户在 Antigravity / OpenCode 自己的 UI / CLI 中完成标准 OAuth PKCE 流程。
3. **连接验证**：观察真实的 MCP initialize、tools/list 和只读工具调用。

配置文件成功写入不代表用户已经完成 OAuth，也不代表 Gateway 已经连接。

### 13.2 Antigravity 授权引导

官方 Antigravity App / IDE 流程包括：
- 打开 Customizations / Installed / MCP Servers 页面；
- 在 AirBridge Server 条目旁点击 Authenticate；
- 按客户端流程完成浏览器授权；
- 在客户端提示时提交授权码；
- 授权成功后客户端自行保存和刷新 OAuth Token。

GUI 应：
- 提供“打开 Antigravity MCP Servers 设置”或清晰的操作路径；
- 显示待授权状态；
- 授权后通过 Worker 成功接收的认证请求确认连接；
- 不读取或修改 Antigravity 的 OAuth Token 存储文件；
- 不要求用户复制 M2.4 Agent Service Token。

参考：https://antigravity.google/docs/mcp

### 13.3 OpenCode 授权引导

OpenCode Remote MCP 默认启用 OAuth。用户可通过：
- OpenCode UI 的 `/mcps` 页面选择 AirBridge Server 并登录；
- CLI 执行 `opencode mcp auth <server-name>`。

GUI 可在检测到并验证 OpenCode CLI 后提供“打开授权流程”快捷操作；若 CLI 不存在，显示 `/mcps` 操作说明。GUI 不得假定它能替 OpenCode 管理客户端 OAuth Token；OpenCode 将凭据存储在项目配置之外。

参考：https://opencode.ai/docs/mcp-servers/

### 13.4 授权状态拆分

不要用一个绿色 “Authenticated” 指示灯代表所有环节。每个 Agent 至少显示：

| 状态 | 权威来源 | 说明 |
|---|---|---|
| MCP 配置 | 对应 Adapter 重新读取配置文件 | 已写入 / 缺失 / 格式错误 |
| HTTPS 证书 | Windows 证书检查与 TLS 健康检查 | 有效 / 不受信任 / 过期 / 缺失 |
| OAuth Broker | 本机授权数据库 | 未授权 / Grant 已签发 / 已撤销 / 过期 |
| Agent Service Credential | M2.4 Credential Provider | 有效 / 缺失 / 读取失败 / 已撤销 |
| MCP Worker | Supervisor / Worker 健康状态 | Stopped / Starting / Running / Failed |
| MCP 客户端请求 | Worker 观测到的实际请求 | 从未认证 / 最近成功认证 / 最近认证失败 |
| MCP 功能验证 | initialize / tools/list / 只读 tool call | 各步骤独立显示通过或失败 |

AirBridge 不能读取客户端自己的 Token Vault，因此不能仅凭 Broker 有 Grant 就宣称客户端当前持有有效 Token。建议通过 Worker 最近一次成功验证的请求时间，显示“客户端最近成功认证”；无请求时显示“等待客户端连接/授权验证”。

### 13.5 证书管理界面

本机 HTTPS 证书状态必须单独显示：
- issuer / subject；
- SAN；
- thumbprint；
- not-before / not-after；
- 当前用户证书库位置；
- trust chain 验证结果；
- 是否由本 AirBridge 实例创建；
- 更新 / 移除操作。

导入本机信任证书前必须明确提示用户证书作用和信任范围，并获得用户确认。只管理本产品记录的证书 thumbprint；不得清理或覆盖其他软件证书。卸载时仅删除由本产品创建且确认不再被其他组件引用的证书。

### 13.6 GUI 验收补充

- [ ] Antigravity 配置写入后，GUI 明确提示用户仍需在客户端点击 Authenticate 并完成授权。
- [ ] OpenCode 配置写入后，可引导用户使用 `/mcps` 或 `opencode mcp auth`。
- [ ] GUI 不读取、导出或覆盖客户端 OAuth Token。
- [ ] MCP 配置、HTTPS、OAuth Grant、M2.4 Service Credential、Worker 和实际调用状态分别显示。
- [ ] 配置写入成功但未授权时，不显示“已连接”。
- [ ] Broker 有 Grant 但客户端从未发起已认证请求时，显示“等待客户端连接/授权验证”。
- [ ] HTTPS 证书不受信任时，拒绝把 Worker 标记为安全可用。
- [ ] 只在 Worker 观察到成功的认证请求后，更新“最近成功认证”时间。
