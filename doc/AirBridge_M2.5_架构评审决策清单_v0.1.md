# AirBridge M2.5 架构评审决策清单

- 文档编号：AB-M2.5-REVIEW-001
- 版本：0.1
- 日期：2026-11-20
- 状态：产品决策 D-01 至 D-09 已确认；技术 PoC G-01 至 G-11 仍待完成
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
| P-14 | 每台部署 M2.5 的 Windows 主机在部署阶段准备本机 CA / localhost 服务器证书，并仅在目标 Windows 用户 CurrentUser Root 经明确同意后导入 CA 公钥；不要求管理员权限、不修改 LocalMachine Root；CA 部署不改变 Gateway 默认 OFF；拒绝信任时 Managed HTTPS Gateway 不可用，禁止不安全降级 | 用户已确认 |
| P-15 | M2.5 固定默认 TCP 端口：OAuth Broker 38861、首个 Antigravity Worker 38862、首个 OpenCode Worker 38863；仅监听 loopback，每个 Agent 实例端口独立持久化；冲突必须显式处理，不随机切换 | 用户已确认 |
| P-16 | 双机 Gateway 配置冲突按连接代次 / 冲突状态去重弹窗；提示可稍后处理或打开设置；GUI 保留非模态冲突提示直至恢复；首版不提供会话忽略按钮 | 用户已确认 |
| P-17 | 升级安装保留所有有效用户 Gateway / Agent / 端口 / 证书 / OAuth Broker Grant / 当前用户 MCP 配置状态；版本化迁移须备份、原子提交、可回滚；异常配置不得静默重置为首次安装默认值 | 用户已确认 |
| P-18 | OAuth 客户端注册：Antigravity / OpenCode 官方 CIMD Metadata URL 精确白名单优先；目标客户端不支持 CIMD 时 DCR 兼容回退；注册不授予 Agent 权限，每次 Grant 必须经用户 Consent 并绑定独立 Agent / Resource Audience / Scope；不启用任意 CIMD URL fetch | 用户已确认 |

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

## 3. 默认行为决策记录（已确认项与待决策项）

以下项目仍需产品决策；已由用户明确确认的条目已在条目内标记为冻结，不再视为待决策。

### D-01：首次安装默认与升级配置迁移

**用户已确认并冻结：首次安装时 Gateway 总开关默认 OFF。** 仅真正首次安装创建初始配置时，必须写入 `gateway.enabled=false`，首次启动不得自动启动任何 Gateway Worker。

**用户已确认并冻结：升级安装保留所有有效用户配置和授权状态，不得套用首次安装默认值重置用户环境。** 至少包括：
- Gateway 总开关 `gateway.enabled`、`gateway.start_with_airbridge`、各 Agent 实例启用状态；
- 稳定的 `agent_instance_id`、`client_type_id`、`adapter_id`、Scope、监听地址、Broker / Worker 端口及 endpoint；
- M2.4 Agent Service Credential 在当前 Windows 用户 Credential Manager 中的既有条目；升级不得自动重新签发、轮换或撤销；
- OAuth Broker 的客户端注册、Grant / Revocation 状态、issuer / Resource Audience 绑定及维持这些状态所需的持久化数据 / 密钥材料；
- AirBridge 当前用户级 Antigravity / OpenCode MCP 配置中由 Adapter 管理的条目，以及其他 MCP Server、未知字段和 JSONC 注释 / 格式；
- 仍有效且由 AirBridge 管理的本机 CA、CA 私钥关联、localhost Server Certificate 和 CurrentUser 信任状态；升级不得无故删除、重建、重复导入或静默扩大证书信任范围。

**授权数据边界：** Antigravity / OpenCode 自己的 OAuth Token Vault 仍完全由客户端管理。AirBridge 不读取、复制、备份、迁移、改写或删除客户端 Access Token / Refresh Token。正常升级应保持 issuer、Resource URL / Audience 和 Broker 持久化状态稳定，使已有授权无需无故重新进行；若必要变更确实导致某个授权失效，必须明确告知并只要求受影响的客户端 / Agent 重新授权。

**迁移与恢复要求：**
- 通过可靠的安装 / 实例标记区分首次安装与升级；升级时即使配置文件缺失、损坏或 schema 不支持，也不得误判为首次安装并静默写入默认配置。
- 升级前为 AirBridge 配置、Broker 持久化状态 / 密钥元数据、AirBridge 管理的本机 CA / 服务器证书元数据和将被 Adapter 修改的当前用户 MCP 配置建立受当前用户 ACL 保护的本地备份；不得把客户端 OAuth Token Vault 纳入备份。
- 按 schema version 执行可重复、幂等的迁移；保留未知字段和已存在的用户值。只允许为旧 schema 中确实不存在的新字段应用明确记录的迁移默认值，不得覆盖现有值。
- 迁移先备份、校验、暂存，再原子提交；任一步骤失败均回滚到升级前状态，不留下配置、Grant、证书或 MCP endpoint 的半迁移状态。
- 配置损坏、未知版本或备份 / 回滚失败时，GUI 显示明确恢复提示；不得自动清空或重建配置。托管 Gateway Worker 在迁移未解决前保持停止，原始用户设置和数据保留供恢复。
- 成功迁移后根据保留的用户开关和 Agent 状态恢复服务，不因“正在升级”而自动开启 Gateway 或 Agent。

### D-02：Gateway 随 AirBridge 启动

**用户已确认并冻结：**
- 提供独立的 “Start Gateway with AirBridge” 选项，配置键为 `gateway.start_with_airbridge`，默认值为 `true`（ON）。
- AirBridge GUI 启动时，只有在 Gateway 总开关 `gateway.enabled=true` 且至少一个 Agent 实例已启用时，Supervisor 才按配置启动对应 Worker。
- `gateway.start_with_airbridge=false` 时，GUI 启动不自动启动 Worker；用户仍可在 GUI 中手动启动。
- 该选项不能绕过 Gateway 总开关。首次安装时 `gateway.enabled=false` 仍是硬性门禁，因此即使 `start_with_airbridge=true` 也不会启动任何 Worker。

**M2.5 首版明确不包含：** Windows 登录时自动启动 AirBridge GUI（注册表 Run、Startup 文件夹、计划任务或等效自启动机制均不由本版本配置）。这与“AirBridge GUI 启动后是否启动 Gateway”是两个独立行为。

### D-03：GUI 关闭时 Gateway Worker 行为

**用户已确认：** GUI 退出时，先对在途请求进行有界排空，然后停止所有受管 Gateway Worker；不保留后台 Worker，不允许 Worker 脱离 Supervisor 孤立运行。OAuth Broker 等共享依赖的关闭顺序由 Supervisor 管理，未单独作为用户确认项。

### D-04：部署阶段本机 HTTPS CA 信任

**用户已确认并冻结：**
- CA / localhost Server Certificate 的准备和信任导入属于每台 Windows 主机的 AirBridge 部署流程，不等待用户在 GUI 打开 Gateway 或第一次连接 MCP Client 时才导入。
- 每台部署 M2.5 AirBridge 的主机都在部署阶段获得适用于本机 localhost 的 CA 公钥信任材料并导入；不从固定 Host A / Host B 手工导入，不指定某台主机为固定 CA 来源。正式 CA 签发 / 供应模型由 G-01 技术验证确定，不能把 CA 私钥放入普通安装包或经 AirBridge Runtime 跨机传输；各部署 Root Thumbprint 是否相同取决于最终供应模型。
- 部署时向目标 Windows 用户明确展示 CA Subject、Thumbprint、有效期、用途与信任范围；取得明确同意后，只把 CA 公钥导入该用户的 `Cert:\CurrentUser\Root`。CurrentUser 是每台机器、每个用户各自的证书库。
- 不要求管理员权限，不修改 LocalMachine 证书存储区或 LocalMachine Root。若部署程序提升权限，证书信任导入必须在实际目标用户的非提升上下文完成，不能误写入安装管理员账户的 CurrentUser。
- CA 部署与 Gateway 运行开关独立：首次安装 Gateway 总开关仍默认 OFF，部署完成不启动 Broker 或 Worker。
- 用户拒绝 CA Consent 时，AirBridge 安装和 Core / Runtime 继续可用，但 Managed HTTPS / OAuth Gateway 不可用；不得静默导入或在普通 Gateway ON 操作时补导入。以后补做 / 修复必须走显式部署 Repair 并重新 Consent。
- 升级保留有效 CA、私钥关联、服务器证书和 CurrentUser 信任；缺失或需要轮换时只允许在部署 / Repair 流程明示原因并再次取得 Consent。禁止 TLS bypass 或不安全 HTTP 降级。
- 证书信任拒绝 / 失效不影响既有 stdio 兼容路径与 AirBridge Core / Runtime。

**仍需技术 PoC：** CurrentUser Root 对目标 Windows 环境、Antigravity App / IDE、OpenCode 和系统浏览器的识别；部署进程 / 用户上下文正确性；证书 SAN、私钥保护、升级、Repair、轮换、撤销和卸载。任何 PoC 不得扩大上述信任范围。

### D-05：MCP Client 配置安装范围

**用户已确认：** 自动配置只修改当前 Windows 用户级配置。首批 Antigravity / OpenCode Adapter 不自动修改项目级或系统级配置；项目级 / 系统级配置最多检测并提示，不代替用户写入。后续新增 Adapter 默认沿用相同的用户级范围，除非另有明确产品决策。

### D-06：端口分配

**用户已确认并冻结默认端口（TCP）：**

| 服务 | 默认端口 | 默认 HTTPS 地址 |
|---|---:|---|
| Local OAuth Broker | **38861** | `https://localhost:38861/` |
| 首个 Antigravity Agent Worker | **38862** | `https://localhost:38862/mcp` |
| 首个 OpenCode Agent Worker | **38863** | `https://localhost:38863/mcp` |

**端口分配与监听规则：**
- 三个服务使用不同端口；AirBridge 现有 HTTP API `127.0.0.1:8888` 保持不变，不与 Gateway 共用端口。
- 仅监听本机 loopback（IPv4 `127.0.0.1` / IPv6 `::1`，按平台支持情况绑定）；面向 MCP Client 的稳定 hostname 为 `localhost`。禁止绑定 `0.0.0.0`、`::` 等通配地址，不自动开放 LAN / 公网访问。
- 每个 Agent 实例必须有独立、固定、持久化的 TCP 端口；同类型客户端的多个实例也不得共用 Worker 监听端口。新增实例由配置管理器从 M2.5 默认候选端口池中选择未占用端口并持久化。
- M2.5 默认候选端口池为 `38801–38864`。IANA 当前登记快照将该段列为 Unassigned；这些端口仍属于 IANA User Ports 范围，不代表为 AirBridge 保留，也不保证目标主机端口空闲。发布时应复核 IANA 登记状态，实际启动仍必须在本机检查。
- Windows 默认动态客户端端口范围通常为 `49152–65535`，但系统配置可能被修改；启动前仍须检查目标主机的实际动态 / excluded port range 和监听占用，不得仅凭默认值判断。可用 Microsoft `netsh` 命令核查（见下列参考）。
- 端口冲突时显示端口及可获得的进程名称 / PID；冲突服务不得启动。Broker 冲突会阻断依赖它的 Managed HTTPS OAuth 服务；单个 Worker 冲突只使对应 Agent 实例失败，其它独立 Worker 可继续运行。
- 禁止静默切换到随机端口。用户可在 GUI 主动选择候选池内的空闲端口；保存前重新校验并持久化。

**端口变更规则：**
- Worker 端口变化会改变 MCP Resource URL / Resource Audience。必须更新对应 Protected Resource Metadata 和当前用户级 Antigravity / OpenCode 配置，备份并原子写入；旧 Resource 授权失效，要求客户端重新授权。
- Broker 端口变化会改变 OAuth issuer / Discovery Metadata。必须更新 issuer 和 Worker 元数据引用，使客户端重新发现授权服务器并重新授权；旧 issuer 下的 Grant 不得被新 issuer 复用。
- 端口变更前先验证新端口可绑定；任一配置写入失败必须回滚配置，不得留下 listener、metadata 与 MCP Client 配置不一致的半迁移状态。证书 SAN 只绑定 hostname，不包含 TCP 端口；hostname 仍须为 `localhost` 并通过正常 TLS 校验。
- 首次安装初始化上述默认值；升级必须保留用户已保存端口，不得重置为默认值。

参考：
- IANA Service Name and Transport Protocol Port Number Registry（含 `38801–38864` 当前 Unassigned 记录）：https://www.iana.org/assignments/service-names-port-numbers/
- Microsoft：TCP/IP 默认动态端口范围与 `netsh` 检查命令：https://learn.microsoft.com/en-us/troubleshoot/windows-client/networking/tcp-ip-port-exhaustion-troubleshooting
- Microsoft：`netsh interface` 动态 / excluded port range 命令：https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/netsh-interface

### D-07：OAuth 客户端注册策略

**用户已确认并冻结：官方 CIMD 白名单优先 + DCR 兼容回退 + 每 Agent 独立用户授权。**

首批 CIMD Client ID Metadata URL 精确白名单：
- Antigravity：`https://antigravity.google/oauth/client-metadata.json`。官方文档指出 IDE CIMD 支持要求 2.6.0+；旧版应验证 DCR 回退。
- OpenCode：`https://opencode.ai/oauth/opencode/client.json`。当 Authorization Server 声明支持 CIMD、公有客户端且客户端未设置自定义 `redirect_uri` 时，OpenCode 优先使用 CIMD；否则按其官方注册优先级回退 DCR。
- AirBridge OAuth metadata 在支持的情况下声明 `client_id_metadata_document_supported=true`，同时保留 DCR registration endpoint 以兼容未支持 / 未采用 CIMD 的首批客户端版本。托管 MCP Host 配置不写入自定义 `oauth.client_id`、`client_secret` 或 `redirect_uri`，避免人为绕过标准发现顺序。

注册安全边界：
- CIMD 只接受以上两个精确、固定的 HTTPS Client ID Metadata URL；不得开放任意 URL、通配域名、用户自定义 Metadata URL 或任意重定向抓取。优先按官方已核验 Metadata 预登记 / allowlist，不需要为任意客户端 URL 发起网络请求。
- DCR 仅作为 Antigravity / OpenCode 首批兼容回退机制，不代表新增 Generic / Manual 客户端支持。DCR 提供的 client name、logo、URI 等元数据一律视为未经验证的用户声明，不把它们当成客户端真实性证明。新 DCR Client 必须显示来源 / 声明信息并取得用户明确 Consent。
- CIMD / DCR 注册本身只建立 OAuth Client Registration，不创建 Agent Grant、不授予 Scope、不允许访问任何 MCP Tool。
- 每次 Grant 必须由用户确认，并精确绑定 `oauth_client_id + agent_instance_id + resource/audience + scopes`；同一个 Client ID 访问不同 Agent 或 Resource 时必须分别授权。不得因 Client ID 在白名单中而自动授予权限。
- 严格校验 HTTPS issuer、PKCE S256、OAuth state、redirect URI 和 RFC 8707 resource；DCR 仅接受支持的公有客户端配置与授权码流程，拒绝不支持的 grant / response / token endpoint auth method。
- DCR endpoint 需要请求限流、注册数量 / 元数据大小限制、重复注册处理和审计。未知 / 不受支持客户端不得仅凭 DCR 成功连接或获得授权。

**已确认的客户端首批范围：** Antigravity 和 OpenCode 专用 Adapter；Cursor、VS Code / Copilot、Cline 等不属于首批专用适配器开发范围。

**用户已确认：** Generic / Manual 通用 MCP Client 手动接入不纳入 M2.5 首版，不开发首版 GUI 入口，不列入首版验收。未来版本如有需要再单独评估。

### D-08：双机冲突弹窗行为

**用户已确认并冻结：**
- ON/ON 与 OFF/OFF 均属于冲突；ON/OFF 与 OFF/ON 正常，不提示。
- 无链路、会话未建立、旧版对端不支持、字段缺失、状态过期或 UNKNOWN 时均不弹窗，不得把 UNKNOWN 推断为 OFF。
- 同一连接代次内，每次进入一个新的冲突状态时弹窗一次；重复 HELLO / CAPABILITIES、重传或重复状态更新不得反复弹窗。
- 状态恢复为互补 / 非冲突时，清除当前冲突锁存；之后再次进入冲突可以重新提醒。连接代次改变时重置去重状态。
- 弹窗提供“稍后处理”和“打开 Gateway 设置”两个操作。“稍后处理”只关闭本次模态弹窗，不代表忽略或解决冲突。
- Gateway 总览与通信状态页持续显示非模态冲突提示，直到状态恢复或变为 UNKNOWN。
- M2.5 首版不提供“忽略本次会话”或永久忽略冲突的按钮。
- 冲突提示只用于用户提醒；不自动修改本机 / 对端配置、不自动停止 Worker、不阻断 Runtime 或其它 AirBridge 操作。

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
| G-01 | Windows 本机 OAuth Broker HTTPS | CurrentUser 证书信任仅经用户明确同意写入；分别在 Antigravity App / IDE、OpenCode Desktop、OpenCode CLI（如纳入支持范围）和系统 Chrome 中完成 TLS / OAuth 实测；issuer 稳定、客户端 discovery 成功；拒绝 / 撤销时 fail-closed；不得通过 LocalMachine Root 或关闭校验实现通过 |
| G-02 | Antigravity OAuth | 当前目标 App / IDE 完成 Authenticate、Token 获取和只读 MCP 调用 |
| G-03 | OpenCode OAuth | 当前目标版本完成 /mcps 或 CLI auth，并成功调用只读 MCP Tool |
| G-04 | Worker HTTPS | 两个 Agent Resource Server 均能被目标客户端正常验证 TLS |
| G-05 | Opaque Token / IPC | 每次请求验证 Token、Resource、Agent、Scope；撤销后下一次请求拒绝 |
| G-06 | CIMD / DCR | 两个官方 Client Metadata URL 精确白名单；受支持客户端优先 CIMD；不支持时 DCR 回退；恶意 / 任意 URL 不被抓取；DCR 声明元数据不被当成身份认证；注册与 Grant / Scope 分离；每次授权精确绑定 Client / Agent / Resource / Scope；Consent、PKCE、redirect URI、state、resource、限流与审计测试通过 |
| G-07 | PyInstaller onefile Worker | 同一个 EXE 启动 Worker，GUI 退出/崩溃时 Worker 有界收敛，不依赖父进程临时目录 |
| G-08 | Runtime Session | 新/新四种组合正确；新/旧、旧/新、旧/旧无误报 |
| G-09 | Python 3.11+ | GUI、Gateway、Credential Provider、CI 和最终 EXE 统一运行时 |
| G-10 | Gateway 端口管理 | 默认端口正确；每实例唯一持久化；loopback-only；真实 bind / excluded port 检查；冲突隔离；端口变更同步 metadata / 客户端配置并完成重新授权或回滚 |
| G-11 | 升级配置与授权状态保留 | 首次安装默认 OFF；升级保留 Gateway / Agent / Credential / Broker Grant 与 Revocation / CurrentUser 证书 / 当前用户 MCP 配置；Token Vault 不读取；版本化幂等迁移、备份、原子提交、回滚及损坏恢复测试通过 |

### 4.1 G-01 公开资料研究结果（2026-11-20；不是 PoC PASS）

**已有公开证据：**
- Windows 将 Current User 与 Local Machine 证书存储分开；Current User 存储属于当前 Windows 用户上下文（HKCU）。Microsoft 文档：https://learn.microsoft.com/en-us/windows-hardware/drivers/install/local-machine-and-current-user-certificate-stores
- Chromium Certificate Verifier 在 Windows 上明确读取 Current User 的 Trusted Root Certification Authorities，用于本地 TLS 信任决策；本项目使用 TCP loopback HTTPS，与其 TCP 本地信任场景相符。Chromium 文档：https://chromium.googlesource.com/chromium/src/+/main/net/data/ssl/chrome_root_store/faq.md
- OpenCode 官方 Network 文档为私有 CA 提供 `NODE_EXTRA_CA_CERTS` 配置。Bun 文档另提供 `NODE_USE_SYSTEM_CA=1` / `--use-system-ca`，使运行时把系统 CA 加入信任集合；Bun v1.3.14 Windows 发布说明进一步列明该模式会读取 CurrentUser / LocalMachine 的 ROOT、CA、TrustedPeople 存储。资料：https://opencode.ai/docs/network、https://bun.com/docs/runtime/environment-variables、https://bun.com/blog/release-notes/bun-v1.3.14
- OpenCode 项目在 Windows Desktop V2 证书问题讨论中说明 V2 Desktop 使用操作系统证书存储；同一讨论对 CLI 提供了额外 CA 文件方式。该回复是项目支持讨论，不替代当前正式版本实测：https://github.com/anomalyco/opencode/issues/54095
- Antigravity 官方 MCP 文档定义了 OAuth / PKCE / CIMD / DCR 行为，但没有说明 Antigravity App / IDE / CLI 的 HTTPS 信任库来源，也没有承诺 CurrentUser Root 一定生效：https://antigravity.google/docs/mcp

**当前判断：**
- Windows CurrentUser Root + Chrome TCP TLS：公开资料支持，仍需在目标 Windows 环境实测 localhost SAN 和实际链验证。
- OpenCode Desktop V2：有项目方关于 OS certificate store 的支持说明，技术上有希望；必须在 M2.5 目标 Desktop 版本实测 OAuth discovery 与 Resource Server 请求。
- OpenCode CLI：运行时存在使用系统 CA 的配置能力，但是否由目标打包版本默认启用、是否识别 CurrentUser Root、是否需要额外环境变量，必须单独验证；不能把 Desktop 结果外推到 CLI。
- Antigravity App / IDE：证书信任来源尚无足够公开证据，必须实测。

**因此 G-01 当前状态为 Partial Research / NOT PASS。** 不得因 Chrome 成功或证书导入成功就判定目标 MCP 客户端兼容；不得扩大到 LocalMachine Root、要求管理员权限、设置 TLS bypass 或降级为无认证 HTTP。

### 4.1.1 G-01A：GitHub Actions Windows CurrentUser 存储测试（CI 层）

新增独立 workflow：
- `.github/workflows/g01-windows-currentuser-ci.yml`
- `.github/scripts/g01-currentuser-trust.ps1`

CI 层应验证：
1. 在 Windows `windows-2025` Runner 当前用户上下文中生成一次性测试 CA / Leaf，并只导出 CA 公钥 CER；
2. 在已记录用户 Consent 的等效测试前提下，将公钥写入 `CurrentUser\\Root`，用 `certutil -user -store Root <thumbprint>` 回读；
3. 确认 CurrentUser Root 条目没有私钥，CA 私钥留在 CurrentUser My 的临时测试证书对象中；
4. `X509Chain.Build` 对已信任 CA 签发的 Leaf 成功，对未导入信任的另一 CA 签发的 Leaf 失败；
5. LocalMachine Root 在导入前、导入后和清理后 Thumbprint 集合完全一致；
6. 通过精确 Thumbprint 清理本次创建 / 导入的证书，并保存不包含私钥的 JSON / certutil 证据。

**CI 的证明边界与当前状态：** GitHub-hosted Windows Runner 的父 workflow 进程以 elevated `runneradmin` 运行；workflow harness 现在会创建一次性普通本地 Windows 用户，并尝试在该用户的独立进程 / CurrentUser profile 中执行 CA 公钥导入与 X509Chain 正反向验证，结束后删除该临时账户。最早一次 `Import-Certificate` 尝试因非交互 Runner 报 `UI is not allowed in this operation` 而失败；后来增加的标准用户进程测试尚未得到成功结果，最新 run 仍处于执行中，因此 G-01A **NOT PASS / PENDING**。必须看到子进程身份确认为非管理员、CurrentUser Root 写入 / 回读成功、LocalMachine Root 前后完全不变、证书链正反向结果和清理成功，才能关闭 CI 子 Gate。该 workflow 不运行 Antigravity / OpenCode 桌面客户端，也不证明它们使用相同信任库。

因此 G-01 分为 CI OS store / chain 子项和真实 MCP Client TLS / OAuth 子项；CI 子项通过不能单独把 G-01 总体标记为 PASS。当前 workflow 已接入 PR #16，执行状态与实际结论以该 workflow run 为准。

### 4.2 G-01 必须执行的 Windows PoC 矩阵

**主机范围澄清：** G-01 不绑定 Host A / Host B 角色。Managed HTTPS endpoints 只监听 loopback，`localhost` 指向 MCP Client 所在的本机，因此 Gateway 与 MCP Client 必须位于同一台 Windows 主机。每台实际部署 M2.5 的主机都在部署阶段导入适用于该机 localhost 的 CA 公钥；CurrentUser Root 是该机当前 Windows 用户的信任库，不跨机器共享。不得依赖固定 Host A / Host B 作为 CA 来源。正式 CA 签发 / 供应模型尚未冻结；若 A、B 都部署 M2.5，必须分别执行部署导入和信任检查；Gateway OFF 不跳过部署阶段的 CA 导入。


**可执行操作手册：** [G-01 Windows CurrentUser HTTPS 信任 PoC 操作手册 v0.1](G-01_Windows_CurrentUser_HTTPS信任PoC操作手册_v0.1.md)。当前仅完成操作规范，未在任何目标 Windows 主机执行；PoC 应在每台计划启用 Managed HTTPS Gateway 的主机独立进行，G-01 状态继续保持 Partial Research / NOT PASS。

1. 建立一次性本机测试 CA 与 server leaf certificate：leaf 必须包含 DNS SAN `localhost`、Server Authentication EKU、有效期和完整链；服务监听 loopback TCP。不要使用无 SAN 证书或忽略证书错误。
2. 用户明确同意后，只把 CA 公证书导入 `Cert:\CurrentUser\Root`；私钥留在当前用户受保护密钥容器。记录导入前后 CurrentUser / LocalMachine 的证书 thumbprint，确认未修改 LocalMachine。
3. 用 Chrome 打开同一 `https://localhost:<port>/health`，确认无证书警告且链 / hostname 正确；再分别测试 Antigravity App / IDE 与 OpenCode Desktop 的 OAuth discovery、Authenticate、MCP initialize、tools/list 和只读 Tool 调用。
4. OpenCode CLI 单独测试默认环境及目标版本实际支持的 system-CA 配置；若必须设置 `NODE_USE_SYSTEM_CA=1` 或等效官方方式，记录是否属于首版支持前提，不能通过 `NODE_TLS_REJECT_UNAUTHORIZED=0` 或其它跳过校验方式通过。
5. 在同一客户端执行负向测试：移除 / 撤销测试 CA 信任、证书过期、SAN 不匹配、缺失中间证书；所有场景都必须拒绝连接。恢复用户信任后应能重新连接。
6. 分别记录客户端产品 / 版本 / 安装形态、TLS 栈 / system-CA 配置、证书 thumbprint、issuer、Resource URL、错误日志（脱敏）和测试结果；按 Desktop / CLI 分开出结论。

只有首批实际承诺支持的客户端形态都在 CurrentUser 信任范围内通过真实 TLS + OAuth + MCP 调用，且所有负向测试 fail-closed，G-01 才能 PASS。若某一客户端形态不通过，应缩小该形态支持范围并重新评审，不能突破既有 CurrentUser 产品决策。

任一关键安全 PoC 不通过，不允许以无认证 HTTP 或明文 Service Token 作为临时上线方案。

## 5. 实施前基线纠正

- M2.4.1 PR #14 已合并，但旧 Host A Runbook 和旧 EXE artifact 的验收记录没有自动升级为最新 merge HEAD 实机验收。
- 当前 AirBridge EXE workflow 仍使用 Python 3.10.11；Gateway package 要求 Python 3.11+。
- M2.5.1 应先建立最新 M2.4.1 merge HEAD 的可重复构建和 Host A 验收记录，再统一 Python 构建环境。
- M1 / M2 已验收的串口链路、Runtime、HTTP API、Agent Security 和只读 MCP 工具必须持续回归。

## 6. 评审通过后的冻结范围

文档评审完成后：
1. 将已确认 P-01 至 P-18 标记为 Frozen Product Requirements；
2. 对 D-01 至 D-09 记录用户选择或明确延期；
3. 关闭 G-01 至 G-11 的技术可行性门禁，或者明确标记 No-Go / 暂缓；
4. 更新 M2.5 架构文档和 ADR 状态；
5. 仅在用户明确批准后进入 M2.5.1 代码实施。

---
