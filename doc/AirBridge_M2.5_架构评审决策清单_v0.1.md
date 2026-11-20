# AirBridge M2.5 架构评审决策清单

- 文档编号：AB-M2.5-REVIEW-001
- 版本：0.1
- 日期：2026-11-20
- 状态：待评审
- 关联：M2.5 v0.3 主文档、ADR-007 至 ADR-012
- 目的：把用户已确认的产品约束、当前建议方案和仍需产品决策的项目区分开

## 1. 已确认、不得在实现中擅自改变的产品原则

| 编号 | 产品原则 | 当前状态 |
|---|---|---|
| P-01 | 主机 A / B 使用完全相同的 AirBridge.exe、安装包、代码和功能 | 用户已确认 |
| P-02 | 两端都包含 GUI、Gateway 模块、管理配置功能和全部 Runtime 功能 | 用户已确认 |
| P-03 | Gateway 启停只由各自主机 GUI 配置决定 | 用户已确认 |
| P-04 | 不按 Cadence / Capture / Allegro 安装情况或系统环境自动判断角色 | 用户已确认 |
| P-05 | 主机 B Gateway OFF 时，Gateway 管理 GUI 仍完整存在，AirBridge Runtime 正常运行 | 用户已确认 |
| P-06 | Gateway GUI 必须支持完整管理、配置、启停、运行状态显示、日志与诊断 | 用户已确认 |
| P-07 | Python 运行时统一为 3.11+；不发布独立 Gateway EXE | 用户已确认 |
| P-08 | ON/OFF 与 OFF/ON 正常；ON/ON 与 OFF/OFF 提示冲突 | 用户已确认 |
| P-09 | 冲突提示不得自动修改任何一端配置或自动停止 Gateway | 用户已确认 |
| P-10 | AirBridge 不接管 Capture / Allegro 专业软件业务生命周期 | 既有项目架构原则 |
| P-11 | M2.5 首批专用 MCP Client Adapter 仅支持 Antigravity 与 OpenCode；其他专用 Adapter 不属于首批范围 | 用户已确认 |
| P-12 | M2.5 首版不提供 Generic / Manual 通用 MCP Client 手动接入；该能力留待后续版本评估 | 用户已确认 |
| P-13 | 首次安装时 Gateway 总开关默认 OFF | 用户已确认 |

## 2. 建议采用的技术决策

这些方案在文档中作为提案记录；部分仍须 PoC，不能视为实现完成。

| 编号 | 建议决策 | 依据 / 状态 |
|---|---|---|
| T-01 | GUI 管理的 Gateway 采用 Streamable HTTP；旧 stdio 保留迁移兼容 | Antigravity / OpenCode 官方文档支持；目标版本实测待完成 |
| T-02 | OAuth Authorization Server 与每 Agent MCP Resource Server 均采用 HTTPS | MCP Authorization / RFC 6750 安全要求；Windows 本地证书和真实客户端信任待 PoC |
| T-03 | MCP Client OAuth Token 与 M2.4 Agent Service Token 完全分离 | 安全边界强制要求 |
| T-04 | 一个本机 OAuth Broker + 每 Agent 一个独立 Resource Server Worker | ADR-010 提案 |
| T-05 | 初版使用 Opaque Access Token + Broker introspection；每次请求验证有效性 / audience / Agent / scope / revocation | 易于即时撤销；Named Pipe / SQLite 并发性能待 PoC |
| T-06 | Worker 以同一个 AirBridge.exe 的内部入口启动；每个 Agent 独立进程 | ADR-008 提案；PyInstaller onefile frozen EXE 待验证 |
| T-07 | Windows Named Pipe 作为 Supervisor / Worker 和 Worker / OAuth Broker 的本机受控 IPC 候选 | 需验证当前用户 SID ACL、生命周期和崩溃恢复 |
| T-08 | Antigravity / OpenCode 分别使用独立配置 Adapter，配置成功与 OAuth 授权成功分开显示 | 官方 schema 不同 |
| T-09 | Runtime Session 复用现有 0x15 / 0x16 增加可选 gateway.state.v1，不新分配 ABFP 帧 | ADR-011 提案；新旧 EXE 双向测试待完成 |

## 3. 仍需产品决策的默认行为

以下项目仍需产品决策；已由用户明确确认的条目已在条目内标记为冻结，不再视为待决策。

### D-01：首次安装时 Gateway 默认状态

**用户已确认并冻结：首次安装时 Gateway 总开关默认 OFF。** 新安装创建初始配置时必须写入 `gateway.enabled=false`，首次启动不得自动启动任何 Gateway Worker。

升级行为建议：升级安装保留用户已有的 Gateway 配置，不将现有 ON/OFF 状态重置；作为配置迁移兼容要求实施。

### D-02：Gateway 随 AirBridge 启动

建议提供独立的 “Start Gateway with AirBridge” 选项，默认 ON；但仅在 Gateway 总开关 ON 时生效。

需要决定：
- GUI 默认启动策略；
- Windows 登录自动启动是否属于 M2.5，还是暂不包含。

### D-03：GUI 关闭时 Gateway Worker 行为

**用户已确认：** GUI 退出时，先对在途请求进行有界排空，然后停止所有受管 Gateway Worker；不保留后台 Worker，不允许 Worker 脱离 Supervisor 孤立运行。OAuth Broker 等共享依赖的关闭顺序由 Supervisor 管理，未单独作为用户确认项。

### D-04：本机 HTTPS 证书信任

建议优先研究 CurrentUser 范围的本机 CA / Server Certificate 信任，避免无必要的 LocalMachine Root 修改。

需要决定：
- 是否接受安装时由用户明确确认导入本机信任证书；
- 证书被用户拒绝时，是否将 Managed HTTP Mode 保持为不可用（建议是）；
- 是否要求管理员权限（建议初版不要求，除非 PoC 证明不可避免）。

### D-05：MCP Client 配置安装范围

**用户已确认：** 自动配置只修改当前 Windows 用户级配置。首批 Antigravity / OpenCode Adapter 不自动修改项目级或系统级配置；项目级 / 系统级配置最多检测并提示，不代替用户写入。后续新增 Adapter 默认沿用相同的用户级范围，除非另有明确产品决策。

### D-06：端口分配

建议：
- Broker 使用稳定 issuer 地址；
- Antigravity / OpenCode Worker 使用固定且不同的 loopback 端口；
- 端口冲突时提示用户解决，不静默切换到随机端口；
- 变更端口时自动更新相应 MCP Host 配置。

最终端口范围需在 OAuth redirect / certificate SAN / 客户端 compatibility PoC 后决定。

### D-07：OAuth 客户端注册策略

建议：
- M2.5 首批仅为 Antigravity / OpenCode 内置专用 Adapter；
- Generic / Manual 通用 MCP Client 手动接入明确排除在首版之外；
- 其他客户端后续只有在通过 Streamable HTTP、HTTPS 和 MCP OAuth 兼容性验证，并完成对应产品范围评审后，才考虑增加专用 Adapter 或通用接入能力；
- 不开放无限制的任意 CIMD Metadata URL fetch；DCR / CIMD 注册不代表用户已授权 Agent；新 Client / 新 Grant 必须明确 Consent。

**已确认：** M2.5 首批专用 Adapter 仅包含 Antigravity 和 OpenCode；Cursor、VS Code / Copilot、Cline 等不属于首批专用适配器开发范围，留待后续版本按需求和兼容性验证评估。

**用户已确认：** Generic / Manual 通用 MCP Client 手动接入不纳入 M2.5 首版，不开发首版 GUI 入口，不列入首版验收。未来版本如有需要再单独评估。

### D-08：双机冲突弹窗行为

已确认的规则：
- ON/ON 与 OFF/OFF 均提示；
- ON/OFF 与 OFF/ON 不提示；
- 无链路、旧版对端或 UNKNOWN 不提示；
- 不自动修改配置或停止服务。

仍需决定：
- 每个冲突状态只弹一次，直到状态恢复或连接代次改变（建议是）；
- 是否提供“本次会话忽略”按钮；
- 是否需要 GUI 同时提供持久化的冲突状态提示。

### D-09：扩展更多 Agent / MCP Client 的机制

建议：采用“Agent 类型注册表 + 客户端配置 Adapter”作为 M2.5 首版扩展基础，详见 ADR-012。Generic / Manual 是未来可选扩展，不属于首版功能。

- Agent 类型不写死为 Antigravity / OpenCode。
- 每个注册实例有独立且稳定的内部 Agent ID；Agent 类型 / Adapter ID 与 Agent 实例 ID 分离。
- Worker、Service Credential、OAuth Grant、Resource Audience 和 Scope 按 Agent 实例隔离。
- **用户已确认：** 首版专用 Adapter 仅有 Antigravity / OpenCode；不开发其他产品的专用 Adapter。
- **用户已确认：** Generic / Manual 通用 MCP Client 手动接入不纳入 M2.5 首版。
- 后续增加产品专用 Adapter 时不修改 Gateway 核心 Worker / OAuth Broker。

**已确认：** 首批专用 Adapter 仅 Antigravity / OpenCode；Generic / Manual 通用手动接入不纳入 M2.5 首版；其他产品专用 Adapter 留待后续版本评估。

## 4. 必须通过技术 PoC 的事项

这部分是技术 Go / No-Go，不属于产品偏好投票。

| 编号 | PoC 项目 | 通过条件 |
|---|---|---|
| G-01 | Windows 本机 OAuth Broker HTTPS | 证书可信、issuer 稳定、客户端 discovery 成功 |
| G-02 | Antigravity OAuth | 当前目标 App / IDE 完成 Authenticate、Token 获取和只读 MCP 调用 |
| G-03 | OpenCode OAuth | 当前目标版本完成 /mcps 或 CLI auth，并成功调用只读 MCP Tool |
| G-04 | Worker HTTPS | 两个 Agent Resource Server 均能被目标客户端正常验证 TLS |
| G-05 | Opaque Token / IPC | 每次请求验证 Token、Resource、Agent、Scope；撤销后下一次请求拒绝 |
| G-06 | CIMD / DCR | 注册、用户 Consent、SSRF 防护、重定向 / DNS rebinding 测试通过 |
| G-07 | PyInstaller onefile Worker | 同一个 EXE 启动 Worker，GUI 退出/崩溃时 Worker 有界收敛，不依赖父进程临时目录 |
| G-08 | Runtime Session | 新/新四种组合正确；新/旧、旧/新、旧/旧无误报 |
| G-09 | Python 3.11+ | GUI、Gateway、Credential Provider、CI 和最终 EXE 统一运行时 |

任一关键安全 PoC 不通过，不允许以无认证 HTTP 或明文 Service Token 作为临时上线方案。

## 5. 实施前基线纠正

- M2.4.1 PR #14 已合并，但旧 Host A Runbook 和旧 EXE artifact 的验收记录没有自动升级为最新 merge HEAD 实机验收。
- 当前 AirBridge EXE workflow 仍使用 Python 3.10.11；Gateway package 要求 Python 3.11+。
- M2.5.1 应先建立最新 M2.4.1 merge HEAD 的可重复构建和 Host A 验收记录，再统一 Python 构建环境。
- M1 / M2 已验收的串口链路、Runtime、HTTP API、Agent Security 和只读 MCP 工具必须持续回归。

## 6. 评审通过后的冻结范围

文档评审完成后：
1. 将已确认 P-01 至 P-10 标记为 Frozen Product Requirements；
2. 对 D-01 至 D-09 记录用户选择或明确延期；
3. 关闭 G-01 至 G-09 的技术可行性门禁，或者明确标记 No-Go / 暂缓；
4. 更新 M2.5 架构文档和 ADR 状态；
5. 仅在用户明确批准后进入 M2.5.1 代码实施。

---
