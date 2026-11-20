# AirBridge M2.5：实施路线图与验收计划

- 文档编号：AB-M2.5-PLAN-001
- 版本：0.3（首批 Adapter 范围、配置边界与用户决策修订稿）
- 日期：2026-11-20
- 状态：计划草案；尚未授权代码实施
- 关联文档：
  - 《AirBridge M2.5：统一运行时与 Gateway GUI 管理架构设计》
  - 《AirBridge M2.5：Gateway GUI 管理与配置交互规范》

## 1. 阶段目标

M2.5 的最终交付是一个统一的 AirBridge Windows 安装包 / AirBridge.exe：
- 主机 A、主机 B 使用同一个构建产物；
- 两端包含完全相同的 Gateway 功能；
- 用户在 GUI 中配置 Gateway ON/OFF；
- GUI 能管理 Gateway 生命周期、Agent、MCP 客户端配置，并显示真实运行状态；
- 双机 Runtime Session 能交换 Gateway 状态并在配置冲突时提示；
- 现有 AirBridge Runtime、串口通信和 M2.3 / M2.4 Agent Security 能力不回退。

文档评审通过前，不开始功能实现。

## 2. 实施前置条件

进入代码实施前必须完成：
1. 核对 M2.4 / M2.4.1 最新 PR 是否已合并，确定凭据 Provider、Provisioning API 和 Agent Registry 的实际基线。
2. 明确当前 main 与本地开发工作树的基线提交，避免将不同工作树的旧构建产物误认为当前版本。M2.4.1 PR #14 已合并，但 Host A Runbook 记录的 EXE 来自早期提交，需以最新 merge HEAD 重新核验。
3. 明确 Python 3.11+ 的构建、运行、测试和依赖锁定策略。
4. Antigravity / OpenCode 的 Streamable HTTP 配置 schema 已由官方文档核查；需完成目标安装版本实测及 OAuth 授权用户流程验收。
5. 完成本机 OAuth Broker + Worker Resource Server 的双 HTTPS 认证设计；loopback 不能作为唯一访问控制，也不能以 HTTP Bearer Token 作为生产方案。
6. 按用户已确认策略实现 GUI 关闭时所有受管 Gateway Worker 的有界排空与退出；共享依赖的关闭顺序由 Supervisor 管理。
7. 评审 Runtime Session Gateway 状态扩展字段及旧版本兼容策略。

## 3. 里程碑

### M2.5.0 — 文档与架构冻结

交付：
- 统一运行时与 Gateway 架构设计；
- GUI 管理与配置交互规范；
- 实施路线图与验收计划；
- Worker、Transport、本地认证、MCP Host 配置和 Runtime Session 扩展的 ADR。

验收：
- 用户确认同一个 EXE / 同一个安装包；
- 用户确认 A/B 仅通过 GUI Gateway enabled 配置不同；
- GUI 管理、配置、运行状态显示成为强制需求；
- 职责边界、冲突矩阵和非目标无歧义。

### M2.5.1 — Python 3.11+ 统一构建基线

工作：
- AirBridge 桌面应用和 MCP Gateway 使用统一 Python 3.11+；
- 统一依赖声明、版本锁定和构建入口；
- 更新 PyInstaller spec / build 脚本；
- 验证 MCP SDK、PySide6、串口依赖、Windows Credential Manager 依赖共存；
- 记录 EXE 体积、启动耗时和依赖冲突。

验收：
- 构建明确拒绝低于 Python 3.11 的环境；
- CI 覆盖选定的 Python 3.11+ 版本矩阵；
- AirBridge GUI、串口通信、HTTP API、Agent Security 和 Runtime Session 回归通过；
- 一个构建产物包含 Gateway 所需模块；
- 不生成或发布独立 Gateway EXE。

### M2.5.2 — 统一 EXE Gateway Worker 与 Supervisor

工作：
- 在同一个 AirBridge.exe 中增加内部 Gateway Worker 启动入口；
- GUI 主进程与 Worker 进程入口严格分流；
- Supervisor 实现启动、停止、重启、PID 记录、健康检查、日志采集和重复实例保护；
- 验证 PyInstaller frozen 单文件 EXE 重新启动 Worker 的行为；
- 实现有界优雅关闭和崩溃状态回收。

验收：
- Worker 使用与 GUI 相同的 EXE 路径和版本；
- Worker 启动不产生额外 GUI 窗口；
- GUI 退出/Worker 退出行为符合已冻结策略；
- Worker 崩溃不会导致 AirBridge Runtime 崩溃；
- 不出现孤儿 Worker 或错误 PID 认领。

### M2.5.3 — Gateway 配置持久化与 GUI 管理

工作：
- 配置 schema、版本迁移、原子写入；
- Gateway 总开关、Agent 实例开关和启动策略；
- GUI 总览、服务控制、配置页面；
- 真实状态轮询和错误显示。

验收：
- A/B 使用相同页面和功能；
- OFF 时 Gateway 不运行，但其他 AirBridge 能力正常；
- ON 时按 Agent 配置启动 Worker；
- 新安装后默认 OFF，无 Worker 自动启动；升级时保留原有 Gateway 开关配置；
- GUI 状态与进程真实状态一致；
- 配置损坏、写入失败和端口冲突有明确恢复行为。

### M2.5.4 — MCP 持久传输与本地访问认证

工作：
- 评估并实现 Streamable HTTP；
- 明确每 Agent endpoint / 端口分配；
- 设计并实现本地客户端到 Worker 的身份认证和授权；
- 保留现有 stdio 兼容入口；
- 限制监听地址为 loopback；
- 完成 initialize、tools/list、只读工具调用与故障恢复测试。

验收：
- Antigravity / OpenCode 能分别连接自己的 Gateway 实例；
- Agent A 不能借用 Agent B 的身份或凭据；
- 未认证本机客户端不能访问受保护 MCP 能力；
- 不依赖 loopback 作为唯一安全边界；
- Token 不进入 MCP 配置、日志、命令行参数或模型上下文；
- 不开放 LAN / 公网 listener。

### M2.5.5 — Agent 管理与凭据生命周期 GUI

范围约束：Agent Registry / Agent 实例模型应可扩展；M2.5 首批专用 MCP Client Adapter 仅为 Antigravity、OpenCode。Generic / Manual 通用 MCP Client 手动接入已确认不纳入 M2.5 首版，不得纳入首版开发或验收范围。

工作：
- GUI 添加 / 初始化 Agent；
- 凭据状态查询；
- Scope 查看和明确授权；
- 轮换、撤销和错误恢复；
- 与 M2.4 Credential Provider / Agent Security 复用。

验收：
- GUI 可完成正常 Agent provisioning；
- Token 不向用户明文暴露；
- 新 Agent 默认只读；
- 高风险权限需显式授权；
- 凭据存储失败、撤销失败和服务端不可达时状态准确，失败关闭。

### M2.5.6 — Antigravity / OpenCode MCP 配置管理

范围约束：本里程碑只实现 Antigravity 和 OpenCode 两个专用 Adapter；Cursor、VS Code / Copilot、Cline 等专用 Adapter 明确排除在 M2.5 首批之外。Generic / Manual 通用 MCP Client 手动接入不属于 M2.5 首版；未来版本如需加入，必须重新进行产品范围评审。

工作：
- 研究并适配两种客户端实际版本的配置 schema；
- 自动发现配置文件；
- 安全备份、合并、原子写入和校验；
- 保留其他 MCP Server 和用户自定义字段；
- 提供恢复备份能力。

验收：
- 自动配置不要求用户手工编辑 JSON；
- 原配置可恢复；
- 其他 MCP Server 不丢失、不被覆盖；
- 配置不包含 Token；
- 客户端能完成 MCP initialize 和只读工具发现/调用。

### M2.5.7 — 双机 Gateway 状态交换与冲突提示

工作：
- 在通用 Runtime Session 能力元数据中增加 Gateway enabled 状态；
- 明确字段版本、缺省值和旧版本兼容；
- 双端 GUI 展示本机/对端状态；
- 实现冲突弹窗和状态刷新。

验收矩阵：

| Host A | Host B | 预期 |
|---|---|---|
| ON | OFF | 正常，不提示冲突 |
| OFF | ON | 正常，不提示冲突 |
| ON | ON | 两端提示配置冲突 |
| OFF | OFF | 两端提示配置冲突 |

额外验收：
- 无链路、未建立会话、对端能力未知、旧版对端、字段缺失、状态过期 / UNKNOWN 时不误报；
- 同一连接代次、同一冲突状态的重复 HELLO / CAPABILITIES 不重复弹窗；新冲突状态可按状态迁移再次提醒；
- 状态恢复为互补 / 非冲突后清除冲突锁存；链路断开立即将对端状态设为 UNKNOWN，并清除该连接代次的冲突提示；
- 弹窗“稍后处理”仅关闭本次模态弹窗；“打开 Gateway 设置”正确导航；首版没有会话忽略或永久忽略按钮；
- Gateway 总览和通信状态页持续显示非模态冲突提示，直到状态恢复或变为 UNKNOWN；
- 不自动修改配置、不自动停止 Gateway、不阻断 Runtime；
- 旧协议对端可正常协商，不因新增字段导致握手失败；
- Gateway 状态交换不改变 ABFP 核心数据传输语义。

### M2.5.8 — 双机集成回归与发布验收

在主机 A、主机 B 使用同一个最终安装包进行验收：
- A Gateway ON / B Gateway OFF；
- A Gateway OFF / B Gateway ON；
- ON/ON 冲突提示；
- OFF/OFF 冲突提示；
- GUI 服务管理与状态显示；
- Agent 独立身份和权限；
- MCP Host 自动配置、连接和只读调用；
- Gateway Worker 崩溃恢复；
- AirBridge Runtime / 串口通信 / 文件传输回归；
- Python 3.11+ CI 与 Windows EXE 构建回归。

## 4. 质量门禁

每个里程碑均要求：
- 对应单元测试与集成测试；
- 静态检查、类型/导入检查和构建检查；
- 安全测试（凭据泄露、Agent 越权、未认证访问、配置注入）；
- 明确列出未通过项，不以部分 PASS 宣称整阶段完成；
- 不在没有用户授权时对实体主机执行高风险 EDA / Shell 操作。

## 5. 回归保护

M2.5 不得破坏以下既有能力：
- M1 已验收的 AirBridge A/B 通信和串口链路；
- Runtime Session 握手、请求关联和去重；
- AirBridge HTTP API；
- M2.3 Agent Token 服务端验证、Scope、请求所有权和审计；
- M2.4 Credential Provider 的 fail-closed 行为；
- MCP 现有只读工具；
- EDA execute 默认关闭的多重门禁；
- RFS / 文件传输 / Shell 现有权限边界。

不得因为 Gateway OFF 而关闭 AirBridge Runtime 或 HTTP API。

## 6. 发布与部署模型

- 只构建和发布一个 AirBridge Windows 安装包 / EXE；
- 主机 A 和主机 B 安装同一个版本；
- 两端配置分别保存在各自本机；
- **用户已确认：新安装 Gateway 总开关默认 OFF**，安装完成后不得自动启动任何 Gateway Worker；
- **用户已确认：升级安装保留所有有效用户配置与授权状态**，包括 Gateway / Agent 开关、Agent identity / Scope、端口 / endpoint、M2.4 Credential Manager 条目、Broker Grant / Revocation / issuer 数据、有效 CurrentUser 证书和 Adapter 管理的当前用户 MCP 配置；不得套用首次安装默认值重置；
- 升级迁移必须版本化、幂等、先备份再原子提交；失败必须回滚。配置损坏或未知 schema 不得误判为首次安装、不得重建空白配置或 Grant Store；迁移未解决前 Gateway Worker 保持停止；
- AirBridge 不读取 / 备份 / 迁移 Antigravity / OpenCode 私有 OAuth Token Vault，也不复制 / 导出 M2.4 Credential Manager Secret；保持 issuer / Resource Audience 稳定，避免正常升级无故要求重新授权；
- **用户已确认：`gateway.start_with_airbridge` 默认 ON**，但只有 Gateway 总开关与对应 Agent 实例均启用时，GUI 启动才拉起 Worker；
- **M2.5 首版不实现 Windows 登录自动启动 AirBridge GUI**；不创建注册表 Run 项、Startup 快捷方式或计划任务；
- 主机 A 可由用户在 GUI 启用 Gateway 并配置 Agent；
- 主机 B 可保持 OFF，仍具有完整 Gateway 管理能力；
- 不要求在 B 上安装不同软件或移除 Gateway 模块。

## 7. 阶段退出条件

只有在 M2.5.8 全部关键验收通过，且用户确认 A/B 双机实测结果后，M2.5 才能标记为完成。CI PASS、单机 GUI 演示或单个 MCP 客户端连接成功，均不能替代同包双机验收。

---


## 8. 2026-11-20 技术核查后的路线图修订（v0.2）

### 8.1 新增 M2.5.0-A：本机 OAuth / HTTPS 可行性 Gate

在生产 Gateway HTTP 授权实现前，必须先完成一个可丢弃的 Windows 技术原型；该原型不进入正式 AirBridge 产品包，除非通过评审后合并。

研究范围：
- MCP Authorization 规范要求的 HTTPS Authorization Server endpoints；
- 本地证书生成、受信任证书链、安装/更新/撤销和卸载；
- Antigravity App / IDE / CLI 对本地 HTTPS Authorization Server 的支持；
- OpenCode 对本地 HTTPS issuer、PKCE、CIMD / DCR 的支持；
- OAuth callback、state、resource / audience、Token refresh 和 revoke；
- 两个 Agent Worker 的授权隔离。

Go / No-Go：
- **Go**：两类客户端都能完成标准 OAuth 授权，且 HTTPS 信任和凭据隔离可部署、可恢复；进入 M2.5.4 实现。
- **No-Go**：任一目标客户端无法安全完成本机 OAuth，或必须把 M2.4 Service Token 写入 MCP 配置；暂停 Managed HTTP 上线，保留 stdio 路径，重新评审本机身份验证架构。不得降低为无认证 loopback HTTP。

### 8.2 M2.5.1 构建门禁补充

- PyInstaller 使用锁定版本，不允许 build.bat 每次动态安装未锁定版本。
- 若使用 `sys.executable` 启动会独立存活的 Worker，按 PyInstaller 6.9+ 规则设置 `PYINSTALLER_RESET_ENVIRONMENT=1`，Worker 必须独立解包。
- CI 除源码单测外，必须运行实际 frozen EXE 的 GUI / Worker 启动、健康握手、优雅关闭和 GUI 异常退出测试。

### 8.3 M2.5.2 Worker 门禁补充

- 每 Agent 独立 Worker；Worker 使用同一个 AirBridge.exe 文件，但独立进程和独立启动上下文。
- Worker 通过受当前 Windows 用户 SID ACL 保护的本机控制通道与 Supervisor 通信。
- 启动参数不包含任何 Token / Secret。
- GUI 关闭时先排空、再停 Worker；GUI 异常退出时 Worker 必须自行收敛，不遗留可被认领的孤儿服务。

### 8.4 M2.5.4 授权实现补充

M2.5.4 不只是启用 Streamable HTTP Server，还必须完成：
- OAuth Authorization Server 与 Resource Server 的分离职责；
- Protected Resource Metadata 与 Authorization Server Metadata；
- PKCE S256、DCR / CIMD、redirect URI 精确校验、state、resource / audience 验证；
- HTTP Worker 的 TokenVerifier 与 Agent / scope 映射；
- M2.4 Service Token 由 Credential Provider 独立读取，绝不透传客户端 OAuth Token；
- HTTPS issuer / certificate trust 原型先通过 M2.5.0-A Gate。
- Broker 默认 TCP 38861、首个 Antigravity Worker 38862、首个 OpenCode Worker 38863；新增 Agent Worker 使用唯一持久端口。
- 端口仅绑定 loopback，启动前完成真实 bind / 占用与系统 excluded / dynamic range 检查；Broker 冲突阻断 Managed HTTPS OAuth，单 Worker 冲突只阻断该实例。
- Worker / Broker 端口变更后同步 Resource / issuer metadata 与当前用户级 MCP Host 配置，并按 Resource Audience / issuer 变化要求重新授权；更新失败回滚。

### 8.5 M2.5.6 Host Adapter 补充

- Antigravity 与 OpenCode 分别实现配置 Adapter；
- 自动检测产品版本和 schema；
- 自动写入严格限定当前 Windows 用户级 / 应用用户配置；项目级 / 系统级配置仅检测并提示，不修改；
- Antigravity IDE CIMD 需确认版本 2.6.0+；否则验证 DCR 路径；
- OpenCode V2 使用 `mcp.servers.<name>` 的 remote schema；
- 两类客户端的 OAuth Token 都不得由 AirBridge GUI 读出或写入 MCP JSON。

### 8.6 发布 Gate 补充

M2.5.8 双机验收前必须分别检查以下状态，不能只看 Gateway 总绿灯：
1. GUI 配置 enabled；
2. Gateway Worker 进程与健康检查；
3. MCP Resource Server endpoint；
4. OAuth Authorization Server HTTPS / discovery；
5. Agent Service Credential；
6. MCP Host OAuth authorization；
7. MCP initialize / tools/list / 只读 tool call。

任何一个安全环节未通过，都不能把对应 Agent 标记为已连接。


## 8. v0.3：当前仓库基线与实施 Gate 修订

### 8.1 M2.4.1 基线说明

GitHub PR #14 已于 2026-11-20 合并，merge commit：
`454e1dc1e43009cb0eb5f23e85327a6135c9418a`。

但现有《M2.4.1 Host A Credential Acceptance Runbook》仍写明 PR #14 为 Draft / not merged，且它只记录较早 EXE source commit `35dd48dd162bd47db882de9137c870b388a58601`。Runbook 本身指出后续生命周期 / Final Audit / revoke changes 需要重构建和 Host A retest。

因此 M2.5 实施前必须：
- 更新 M2.4.1 Runbook 的 PR / merge 状态；
- 以 main 的最新合并提交重新构建 AirBridge.exe；
- 独立记录 EXE SHA-256 和 source commit；
- 完成 Agent provisioning / tokenless lookup / revoke / shutdown 的最小回归；
- 不把“PR 已合并”误写成“最新 EXE 已在 Host A 验收”。

### 8.2 M2.5.1 Python 3.11+ 真正统一

当前 AirBridge Windows EXE Build workflow 仍使用 Python 3.10.11，而 Gateway package 的 `pyproject.toml` 已声明 Python `>=3.11`、MCP SDK `mcp==2.3.0`。因此统一 Python Runtime 应包含：
- AirBridge GUI / Core / Gateway Worker / Agent admin tool 统一 Python 3.11+；
- 所有依赖显式锁定版本；
- 更新 `.github/workflows/build-airbridge-exe.yml`、`build.bat` 和 `AirBridge.spec`；
- CI 与 EXE 构建同时验证 Gateway Worker 所需模块和依赖；
- 验证 PySide6、pyserial、MCP SDK、Windows Credential Manager 组件共存；
- 不保留生产 Gateway 的独立虚拟环境 / 独立 EXE 部署。

### 8.3 M2.5.0-A：OAuth / HTTPS 端到端可行性 Gate

OAuth 托管模式必须同时验证：
- Local OAuth Broker Authorization Server HTTPS；
- 每 Agent MCP Resource Server HTTPS；
- Protected Resource Metadata 与 Authorization Server Metadata discovery；
- Authorization Code + PKCE S256；
- Resource Indicator / audience 绑定；
- CIMD / DCR / pre-registration；
- Access Token / Refresh Token 生命周期与撤销；
- TLS 证书在 Antigravity App / IDE、OpenCode 和系统浏览器中均可信；
- 证书信任仅写入 CurrentUser；首次安装取得明确用户同意；无管理员权限；不修改 LocalMachine Root；
- 用户拒绝 / 撤销信任时 Managed HTTPS Mode fail-closed，不允许关闭 TLS 校验或降级到无认证 HTTP；
- MCP Worker 与 Agent Service Credential 完全隔离。

**TLS 规范基线：**
- MCP Authorization 规范要求 Authorization Server endpoints 使用 HTTPS；
- OAuth Bearer Token Usage 规范要求客户端到受保护 Resource Server 的 Token 请求使用 TLS；
- loopback HTTP 仅可作为规范允许的 OAuth redirect URI 例外，不应用于承载 MCP Bearer Token 的资源请求。

官方依据：
- https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization
- https://www.rfc-editor.org/rfc/rfc6750

Go / No-Go：
- **Go**：Antigravity 与 OpenCode 均能在正常证书校验下完成 OAuth 并访问各自 Resource Server，Agent / audience / scope 隔离、撤销和恢复测试通过。
- **No-Go**：任一客户端必须关闭 TLS 校验、不能信任本机 issuer / resource certificate，CurrentUser 证书信任无法覆盖目标客户端，或要求扩大到 LocalMachine Root / 管理员权限，或要求写入 M2.4 Agent Service Token；暂停 Streamable HTTP Managed Mode，保留既有 stdio，不得降级为无认证 HTTP。

证书策略验收还必须覆盖：
- 安装 / 初始配置明确展示用途和信任范围，并等待用户同意；
- 同意时只写 CurrentUser 证书信任存储，不触发 UAC，不修改 LocalMachine；
- 拒绝、撤销、证书过期或证书不匹配时，HTTPS Gateway 明确不可用并 fail-closed；
- 用户拒绝证书信任时，stdio 和 AirBridge Core / Runtime 仍可用；
- 卸载与重装能清理 / 恢复 AirBridge 自己管理的证书对象，不误删用户其他证书。

### 8.4 OAuth 客户端交互验收

**Antigravity：**
- GUI 写入 `mcpServers.<name>.serverUrl`；
- 提示用户在 Antigravity Customizations / MCP Servers 中点击 Authenticate；
- 按其官方流程完成浏览器授权和授权码提交；
- 通过 Worker 的成功认证请求确认实际连通；
- 不读取客户端 OAuth Token 存储文件。

**OpenCode：**
- GUI 写入 V2 `mcp.servers.<name>` 的 remote `url`，首个实例默认 `https://localhost:38863/mcp`；
- 用户在 `/mcps` 中授权，或通过 `opencode mcp auth <name>` 启动授权；
- 通过 Worker 的成功认证请求确认实际连通；
- 不读取 OpenCode 私有 OAuth credential store。

**端口验收：**
- 默认安装配置为 Broker 38861、首个 Antigravity Worker 38862、首个 OpenCode Worker 38863，且不与 AirBridge HTTP API 8888 冲突；
- 所有 Agent 实例端口唯一、持久化；多个同类型实例不能共用端口；
- 使用真实 TCP bind 检查端口占用；注入 Broker 端口冲突与单 Worker 端口冲突，验证故障隔离和 GUI 诊断；
- 修改 Worker / Broker 端口后，验证 metadata、客户端配置备份 / 原子更新、OAuth 重新授权和失败回滚；
- 验证服务只绑定 IPv4 / IPv6 loopback，不创建 LAN / 公网监听或自动防火墙入站规则；
- 验证升级保留用户配置端口，首次安装才使用默认值。

### 8.5 Runtime Session Gateway State Gate

按 ADR-011 扩展 0x15 / 0x16：
- Feature `gateway.state.v1`；
- optional `gateway_state.schema_version`；
- `configured_enabled` 必须为 Boolean；
- HELLO / CAPABILITIES 双向承载配置状态；
- 保持 Envelope schema_version=1 与既有必需字段；
- 旧版缺失字段视为 UNKNOWN，不得当成 OFF；
- 配置修改后重新发送 HELLO；
- 链路断开/重连清空旧对端状态；
- 四种状态组合按用户要求提示；
- 只提示，不自动更改、不停 Gateway、不影响 Runtime 操作。

### 8.6 Gateway OAuth 状态 UI Gate

GUI 不把以下状态合成一个简单绿灯：
1. 本机 Gateway 配置；
2. HTTPS certificate / trust；
3. OAuth Broker 服务健康；
4. OAuth grant registry；
5. Agent Service Credential；
6. Agent Worker；
7. MCP Client 最近成功认证请求；
8. MCP initialize / tools/list / 只读调用。

AirBridge 不读取 Antigravity / OpenCode 的客户端 Token Vault。Broker 有 Grant 记录不等于客户端当前保存了有效 Token；只有 Worker 观察到真实成功请求后，才显示“最近成功认证”。

### 8.7 发布前额外验收

- [ ] 首次安装后 Gateway 总开关默认 OFF，且所有 Worker 保持停止。
- [ ] 升级安装保留此前的 Gateway ON / OFF 配置。
- [ ] AirBridge EXE 和 Gateway 使用 Python 3.11+ 同一运行时。
- [ ] AirBridge Build Workflow 与依赖版本锁定。
- [ ] Worker 使用同一 EXE，PyInstaller onefile 独立运行测试通过。
- [ ] OAuth Broker 和 MCP Worker endpoint 均使用 HTTPS。
- [ ] 证书链在真实目标客户端中验证，无 insecure bypass。
- [ ] Antigravity Authenticate UI 流程完成。
- [ ] OpenCode `/mcps` / CLI auth 流程完成。
- [ ] OAuth Token audience 与 Worker endpoint 精确绑定。
- [ ] 两个 Agent Worker 不可互换 Access Token。
- [ ] OAuth revoke 后旧 Access / Refresh Token 按定义失效。
- [ ] M2.4 Agent Service Token 不进入 MCP Host 配置。
- [ ] 首次安装与升级安装通过独立路径识别；升级不能因配置缺失 / 损坏误判为首次安装。
- [ ] 升级保留 gateway.enabled、start_with_airbridge、Agent instance IDs / enabled / Scope、Broker / Worker ports 和 endpoints。
- [ ] 现存 Agent Service Credential、Broker Client Registration、Grant / Revocation、签发所需密钥材料、有效 CurrentUser 证书身份保持不变；已撤销 Grant 不得复活。
- [ ] Adapter 只变更其管理的用户级 MCP 条目；其它 MCP Server / JSONC 数据保留；不访问客户端 OAuth Token Vault。
- [ ] 模拟成功迁移、中途崩溃、配置损坏、未知 schema、备份失败和回滚失败；验证原始数据保留、Worker fail-closed 和 GUI 恢复说明。
- [ ] Runtime Session 新/旧版本互操作与四种冲突组合通过。
- [ ] A/B 使用相同最终 AirBridge.exe 完成双机实测。
