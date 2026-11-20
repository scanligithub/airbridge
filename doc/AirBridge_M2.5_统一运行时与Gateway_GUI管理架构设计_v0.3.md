# AirBridge M2.5：统一运行时与 Gateway GUI 管理架构设计

- 文档编号：AB-M2.5-ARCH-001
- 版本：0.3（基线状态、OAuth/TLS 和 Runtime Session 兼容性复核稿）
- 日期：2026-11-20
- 状态：文档阶段；未进入代码实现
- 适用范围：AirBridge 主机 A / 主机 B
- 上游基线：M1、M2 MCP Gateway、M2.3 Agent Security、M2.4 Zero-Touch Agent Authentication、Runtime Session
- 本文性质：目标架构与约束；其中标注“待决策/待验证”的内容不得视为已实现

## 1. 目标

M2.5 将 MCP Gateway 纳入 AirBridge 桌面产品的统一交付、运行时和 GUI 管理体系。用户在 AirBridge GUI 中即可完成 Gateway 的启用、配置、Agent 实例管理、启动/停止、运行状态查看和日志诊断。M2.5 首批专用 MCP Client 配置 Adapter 仅覆盖 Antigravity / OpenCode；其他专用 Adapter 不属于首批范围。Generic / Manual 通用 MCP Client 手动接入已确认不纳入首版，留待后续版本评估。

M2.5 不改变 AirBridge 的产品定位：AirBridge 仍是通用、透明的双机通信与 Runtime 基础设施；Gateway 是其本机 MCP 接入层，不承担 Capture / Allegro 专业业务生命周期。

## 2. 已确认的产品约束（强制）

1. 主机 A、主机 B 使用完全相同的 AirBridge.exe、同一个安装包、同一套代码和相同的完整功能。
2. 两端 EXE 均包含 GUI、Gateway 管理能力、Gateway Server、Agent Security、HTTP API、Runtime Session、串行通信等产品模块；不得通过 A/B 专用构建裁剪功能。
3. Gateway 是否在本机运行，只由本机 AirBridge GUI 中的配置决定。不得根据 Cadence 是否安装、Capture / Allegro 是否存在、计算机名、用户名、目录或操作系统环境推断角色。
4. 主机 A 通常设置 Gateway ON，主机 B 通常设置 Gateway OFF；这只是部署习惯，不是程序内置角色。
5. 主机 B 设置 Gateway OFF 时，只是不启动本机 MCP Gateway 服务；其 Gateway 管理界面和模块仍然存在，AirBridge Runtime、串行通信及其他功能继续正常运行。
6. Gateway 管理、配置、启停、Agent 管理、MCP Host 配置和运行状态显示必须集成在 AirBridge GUI 中。GUI 是 Gateway 的统一管理控制台，不是单一启动按钮。
7. AirBridge.exe 与 Gateway 使用统一的 Python 3.11+ 运行时和同一套依赖/打包体系。Gateway 不另行发布独立 EXE 或独立安装包。
8. 两端连接后交换 Gateway 启用状态。ON/ON、OFF/OFF 时向用户显示角色配置冲突提示；不自动更改任何一端配置，也不自动关闭 Gateway。
9. 冲突检测只在双方已经建立可交换状态的双机通信会话后进行。没有对端、链路未建立或对端不支持该能力时，不得误报冲突。
10. AirBridge 不接管 Capture / Allegro 的启动、关闭、工程打开、保存、Tcl / SKILL 业务流程；这些流程仍由对应 Agent Skill 实现。
11. **首次安装时 Gateway 总开关默认 OFF**；首次安装不得自动启动任何 Gateway Worker。
12. `gateway.start_with_airbridge` 是独立启动策略，默认 ON；仅当 `gateway.enabled=true` 且 Agent 实例已启用时，GUI 启动才会拉起对应 Worker。首次安装的总开关 OFF 优先级更高。
13. **M2.5 首版不配置 Windows 登录自动启动 AirBridge GUI**；这与 GUI 启动后的 Gateway Worker 启动策略分离，留待后续版本评估。
14. 本机 HTTPS 证书只在当前 Windows 用户（CurrentUser）范围建立信任；安装时必须取得用户明确同意，不要求管理员权限，不修改 LocalMachine Root。用户拒绝或撤销信任时，托管 HTTPS Gateway 不可用，禁止关闭 TLS 校验或降级成无认证 HTTP。
15. Gateway 端口默认固定为 Broker TCP 38861、首个 Antigravity Worker TCP 38862、首个 OpenCode Worker TCP 38863；每个 Agent 实例端口独立、持久化，冲突必须显式报告，不能随机改端口。
16. **升级安装保留所有有效 Gateway / Agent / Broker OAuth Grant / 证书信任 / 当前用户 MCP 配置状态**；仅首次安装应用 Gateway 默认 OFF。升级迁移必须版本化、备份、原子提交和可回滚；异常配置不得静默重置。

## 3. 当前基线与 M2.5 变化

### 3.1 当前仓库可确认的基线

- 当前主仓库已有独立的 `mcp_gateway/` Python 包，M2.1 / M2.2 使用 MCP stdio transport。
- `mcp_gateway/pyproject.toml` 声明 Python `>=3.11`，并固定 MCP Python SDK `mcp==2.3.0`。
- 现有 Gateway 通过本机 AirBridge HTTP API（默认 loopback `127.0.0.1:8888`）访问 AirBridge 能力。
- M2.3 提供每 Agent 身份、服务端 Token 校验、Scope、请求所有权与审计等安全基础。
- M2.4.1 的 PR #14 已于 2026-11-20 合并，GitHub merge commit 为 `454e1dc1e43009cb0eb5f23e85327a6135c9418a`。当前 main 包含 `mcp_gateway/airbridge_mcp_gateway/credential_store.py`、Agent ID 凭据读取和 `tools/airbridge_agent_admin.py` tokenless provision / revoke 工作流。
- 当前 `AirBridge.spec` / `build.bat` 尚未体现“AirBridge GUI 与 Gateway 共享同一 Python 3.11+ 运行时、同一 EXE worker 入口”的完整目标。

但 M2.4.1 Host A Runbook 仍保留旧状态（写着 PR #14 Draft / not merged），并记载其 EXE 仅来自早期提交 `35dd48dd162bd47db882de9137c870b388a58601`；Runbook 本身也明确指出后续 PR #14 生命周期 / 审计 / 撤销变更需重新构建并复测。因此：**代码已合并不等于最新合并代码已在 Host A 完成 EXE 验收**。M2.5 实施前应更新该 Runbook 或新增基于 merge HEAD 的验收记录。

另一个关键基线差异：当前 `.github/workflows/build-airbridge-exe.yml` 的 AirBridge.exe 构建仍使用 Python `3.10.11`；`build.bat` 也动态安装未锁定的 PyInstaller / PySide6 / pyserial。Gateway `mcp_gateway/pyproject.toml` 则要求 Python `>=3.11` 并固定 `mcp==2.3.0`。因此 M2.5 的 Python 3.11+ 统一运行时是实际版本迁移，不是简单的打包整合。

### 3.2 M2.5 目标变化

| 维度 | 当前形态 | M2.5 目标 |
|---|---|---|
| 交付 | AirBridge 桌面程序与 Gateway 独立 Python 部署 | 同一个 AirBridge.exe / 安装包 |
| Python | Gateway 独立 Python 3.11+ 环境；桌面构建未统一约束 | 全产品统一 Python 3.11+ |
| Gateway 启动 | MCP Host 通过 stdio 配置自行启动 Gateway | AirBridge GUI 负责管理 Gateway 生命周期 |
| 配置 | 主要依赖 MCP Host JSON 与环境变量 | AirBridge GUI 管理本机 Gateway、Agent 和客户端配置 |
| 状态 | 主要从 MCP Host 侧观察 | GUI 显示 Gateway / Agent / 客户端连接状态 |
| 双机协同 | 无 Gateway 开关状态冲突提示 | Runtime Session 能力元数据交换 Gateway enabled 状态 |

## 4. 目标架构

### 4.1 统一产品结构

主机 A 与主机 B 的软件结构完全相同：

```text
                 同一个 AirBridge.exe / 同一个安装包
┌──────────────────────────────────────────────────────────────┐
│ AirBridge GUI                                                 │
│  ├─ 通信与 Runtime 管理                                       │
│  ├─ Gateway 管理与配置                                       │
│  ├─ Agent 类型注册表 / 实例 / 凭据 / Scope 管理                 │
│  ├─ 专用 MCP Client Adapter（首批：Antigravity / OpenCode）     │
│  └─ 状态、日志与诊断                                          │
├──────────────────────────────────────────────────────────────┤
│ AirBridge 本机服务                                            │
│  ├─ HTTP API / Agent Security                                 │
│  ├─ Runtime Session / ABFP / 串行通信                         │
│  ├─ Gateway Supervisor                                        │
│  └─ MCP Gateway Worker（按配置启用，可有多个 Agent 实例）      │
├──────────────────────────────────────────────────────────────┤
│ 统一内置 Python 3.11+ 运行时与依赖                             │
└──────────────────────────────────────────────────────────────┘
```

主机 A、B 的差异仅来自各自 GUI 保存的本机配置，不来自不同软件包或不同代码分支。

### 4.2 Gateway 管理进程与 Worker

建议由 AirBridge 主程序中的 Gateway Supervisor 负责：
- 读取并校验 Gateway 配置；
- 按已启用 Agent 启动对应 Gateway Worker；
- 监控 Worker PID、启动时间、端口、健康状态和退出码；
- 执行启动、停止、重启及有界优雅关闭；
- 采集 Worker 日志和诊断信息；
- 防止重复启动、端口冲突和孤儿进程；
- 在配置变更或 GUI 关闭时按定义完成生命周期收敛。

为避免 Gateway 网络服务阻塞桌面 GUI，目标实现优先采用独立 Worker 进程。Worker 由同一个 AirBridge.exe 的内部启动入口运行，例如概念性命令：

```powershell
AirBridge.exe --gateway-worker --agent-instance-id <stable-id>
```

以上参数只是内部入口设计示意，不是当前已存在的命令行接口。Worker 模式必须在 GUI 初始化之前分流，不能再次创建桌面窗口；不得使用另一个 Gateway 专用 EXE。是否采用多 Worker 进程或单进程多 Server，须通过可靠性、PyInstaller frozen 行为和故障隔离测试最终确认。

### 4.3 MCP 传输方式

现有 stdio 模式由 MCP Host 创建 Gateway 子进程，生命周期由 MCP Host 控制，不能完整满足“AirBridge GUI 启停并监控 Gateway”的目标。

M2.5 目标建议增加可由 AirBridge 管理的持久 MCP 网络传输，优先评估 MCP Streamable HTTP：
- 每个 Agent 使用独立的 Gateway Worker / 服务实例；
- 服务默认只监听本机 loopback，不监听 LAN 或公网；
- 每个实例绑定固定 Agent ID，使用对应 Agent 的独立凭据和服务端 Scope；
- 首批由 Antigravity 与 OpenCode 分别连接各自的本机 MCP endpoint；其他 MCP Client 只有在相应接入方式通过兼容性与安全验证后才能纳入，Generic / Manual 手动接入不属于 M2.5 首版；
- 默认 TCP 端口由 GUI 配置 / 展示：OAuth Broker 38861、首个 Antigravity Worker 38862、首个 OpenCode Worker 38863；每个 Agent 实例独立持久化，冲突处理与变更规则见 D-06；
- 现有 stdio 模式在迁移期保留兼容，除非兼容性测试证明无法安全共存。托管 HTTP 模式的 OAuth Authorization Server 与 MCP Resource Server endpoint 均采用 HTTPS；HTTP 仅可用于规范允许的 loopback OAuth redirect URI，不用于携带 Bearer Access Token 的 MCP 请求。

**安全阻断条件：** loopback 绑定不等于进程身份认证，也不能单独构成 Agent 隔离。Streamable HTTP 上线前必须明确并验证本地客户端到 Gateway 的身份认证、Agent 绑定、访问控制、凭据保护和请求来源校验。不得以“只监听 127.0.0.1”为由开放无认证的本机 MCP 管理/执行接口。

Antigravity / OpenCode 的具体 MCP 配置 schema、HTTP transport 支持版本、认证字段和自动配置方式，列为 M2.5 前置兼容性研究；不得未经验证把示例配置当成最终实现规范。

## 5. 配置模型

建议配置文件位于当前 Windows 用户的 AirBridge 本地配置目录，例如：

`%LOCALAPPDATA%\AirBridge\config.json`

初始结构示意：

```json
{
  "schema_version": 1,
  "gateway": {
    "enabled": false,
    "start_with_airbridge": true,
    "oauth_broker": {
      "listen_host": "localhost",
      "port": 38861
    },
    "agent_instances": {
      "<agent_instance_id_1>": {
        "client_type_id": "antigravity",
        "adapter_id": "antigravity.config.v1",
        "enabled": false,
        "transport": "streamable-http",
        "listen_host": "localhost",
        "port": 38862
      },
      "<agent_instance_id_2>": {
        "client_type_id": "opencode",
        "adapter_id": "opencode.config.v1",
        "enabled": false,
        "transport": "streamable-http",
        "listen_host": "localhost",
        "port": 38863
      }
    }
  }
}
```

该 JSON 是目标配置模型示意：首次注册的 Antigravity / OpenCode 实例分别使用 38862 / 38863；OAuth Broker 使用 38861。新增 Agent 实例从默认候选池中分配唯一空闲端口并持久化。不得把 Token、密码或其他秘密写入该文件。Agent 凭据沿用 M2.4 Credential Provider / Windows Credential Manager 的安全设计。

配置语义：
- `gateway.enabled`：本机 Gateway 总开关；false 时不启动任何 Gateway Worker。首次安装初始值必须为 `false`（OFF），由用户在 GUI 中明确启用后才允许启动 Worker。
- `gateway.start_with_airbridge`：Gateway 总开关为 true 时，是否随 AirBridge 主程序启动 Worker。
- `agent_instances.<agent_instance_id>.enabled`：单个已注册 Agent 实例的 Worker 是否启用。
- `client_type_id`：客户端产品类型；`adapter_id`：配置适配器类型。两者都不作为 Agent 实例身份。
- `agent_instance_id`：AirBridge 为每个本机注册实例生成的稳定唯一 ID；同一种客户端可注册多个实例。
- `listen_host`：默认固定为 loopback；是否允许修改必须经过安全评审，初版不得开放公网监听。
- `gateway.oauth_broker.port`：OAuth Broker HTTPS issuer 的固定 TCP 端口，默认 38861；Broker 使用稳定 `https://localhost:<port>/` issuer。
- `agent_instances.<agent_instance_id>.port`：每个 Agent Resource Server 的独立 TCP 端口；首次 Antigravity / OpenCode 实例默认 38862 / 38863。新增实例从默认候选池 `38801–38864` 中选择空闲且未被 AirBridge 分配的端口，并持久化。
- 默认候选池 `38801–38864` 当前在 IANA Registry 中标记为 Unassigned（属于 IANA User Ports 范围），但不是 AirBridge 专属保留段；发布时应复核登记状态。本机是否可用必须以启动时实际 TCP bind 检查为准。启动前同时检查 IPv4 / IPv6 loopback、Windows 实际动态 / excluded port range 和端口占用。Windows 默认动态端口范围通常为 `49152–65535`，但可能被修改。
- 参考：IANA https://www.iana.org/assignments/service-names-port-numbers/；Microsoft https://learn.microsoft.com/en-us/troubleshoot/windows-client/networking/tcp-ip-port-exhaustion-troubleshooting 及 https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/netsh-interface。
- 端口冲突时不启动冲突服务，不静默改用随机端口；GUI 显示冲突端口及可获得的 PID / 进程名称。Broker 失败会阻断依赖其授权的 Managed HTTPS Worker；单个 Worker 冲突不影响其它实例。
- 端口变更须同步 Resource / issuer metadata 与对应当前用户级 MCP Host 配置；Resource Audience 或 OAuth issuer 改变后必须重新授权。写入失败回滚，避免端口、listener、metadata、客户端配置不一致。

### 5.1 首次安装与升级迁移（D-01，用户已确认）

**首次安装：**
- 只有确认没有既有 AirBridge 安装 / 实例标记时，才创建全新 Gateway 配置；`gateway.enabled=false`，`gateway.start_with_airbridge=true`，首批 Agent 实例初始为 disabled。
- 首次安装不得启动任何 Gateway Worker；Gateway 总开关必须由用户在 GUI 中明确开启。

**升级安装：**
- 必须通过持久化安装 / 实例标记识别升级；不能仅因配置文件不存在或无法解析就把升级误判为首次安装。
- 保留现有 `gateway.enabled`、`gateway.start_with_airbridge`、Agent 实例 ID / 类型 / Adapter / enabled / Scope、Broker / Worker 端口和 endpoint。
- 保留 M2.4 Credential Provider / Windows Credential Manager 中现有 Agent Service Credential；升级不得自动重签、轮换或撤销。
- 保留 Broker 客户端注册、Grant / Revocation、issuer / Resource Audience 绑定及维持授权有效所需的持久化数据 / 密钥材料；稳定 issuer、Resource URL、Audience 和有效证书身份，避免正常升级触发无故重新授权。
- 保留有效的 AirBridge CurrentUser 证书、私钥关联和信任状态；不重新导入、不扩大信任范围、不无故删除或重建证书。
- 保留 Adapter 管理的当前用户级 Antigravity / OpenCode MCP 配置条目；更新时只修改明确归属 AirBridge 的条目，保留其他 MCP Server、未知字段与 JSONC 注释 / 格式。项目级 / 系统级配置仍只读、不修改。
- 客户端自己的 OAuth Token Vault 完全由客户端管理。AirBridge 不读取、复制、备份、改写或删除其中的 Access Token / Refresh Token。正常升级保留客户端端点及 Broker 状态，使客户端原授权继续有效；若必要迁移导致某个授权失效，必须明确说明并只要求受影响的客户端 / Agent 重新授权。
- 对旧 schema 中不存在的新字段，只能按版本化迁移表添加明确默认值；现有用户值和未知字段不得被新默认值覆盖。迁移必须幂等，可重复运行而不改变已迁移值。

**安全迁移与失败恢复：**
- 修改前为 AirBridge 配置、Broker 持久化状态 / 密钥元数据、AirBridge 管理的证书元数据，以及本次确实要写入的当前用户 MCP 配置建立受当前 Windows 用户 ACL 保护的本地备份；不备份客户端 OAuth Token Vault，也不复制 / 导出 M2.4 Credential Manager Secret。
- 先备份、校验、暂存，再原子提交；跨多个文件 / 数据库的迁移必须有明确提交标记和恢复日志，确保失败后可判定回滚或完成，不留下半迁移状态。
- 配置损坏、未知 schema、备份失败或回滚失败时，GUI 显示明确恢复提示；不得自动清空、重建或覆盖原配置。迁移未解决前，Gateway Worker 保持停止，原始用户数据和持久化 ON / OFF 值保留供恢复。
- 迁移成功后，按保留下来的 Gateway 总开关、启动策略和各 Agent enabled 状态恢复服务；升级过程不得自动开启 Gateway 或 Agent。

配置读写必须具备 schema 版本、原子写入、损坏恢复提示和未知字段保留策略。

## 6. Agent 身份与权限

- 每个已注册 Agent 实例都拥有独立稳定的 `agent_instance_id`、Credential 和服务端 Scope；`client_type_id` / `adapter_id` 描述客户端与配置适配方式，不代替实例身份。
- M2.5 首批专用 Adapter 仅为 Antigravity / OpenCode；Generic / Manual 通用手动接入不纳入首版。
- 一个 Worker 固定绑定一个 `agent_instance_id`；MCP 请求不能通过参数切换 Agent 身份。
- Gateway 从 M2.4 Credential Provider 获取对应 Token；Token 不进入 MCP JSON、普通配置文件、命令行参数、GUI 明文日志或模型上下文。
- 默认只授予 M2.3 / M2.4 规定的只读 Scope：`runtime.read`、`eda.versions.read`、`request.read`。
- `filesystem.write`、`shell.execute`、`eda.execute` 等敏感权限必须由用户明确授权；GUI 不得因 Agent 初始化而自动授予。
- 凭据不可读、已撤销、Agent disabled 或 Scope 不足时必须 fail closed。

## 7. GUI 管理职责

Gateway GUI 至少提供以下功能：
1. **总览 / 运行状态**：总开关、启用配置、运行状态、Worker PID、endpoint/端口、运行时长、版本、最近错误。
2. **服务管理**：启动、停止、重启；显示启动中、运行中、停止中、已停止、失败、降级等状态；停止过程显示排空/关闭结果。
3. **Gateway 配置**：总开关、随 AirBridge 启动选项、Agent 实例开关、传输类型、监听地址和端口。
4. **Agent 管理**：添加/初始化、启用/停用、凭据状态、Scope 查看与授权、凭据轮换、撤销。
5. **MCP 客户端管理**：首批专用 Adapter 仅检测和配置 Antigravity / OpenCode 当前 Windows 用户级配置；展示发现结果；备份现有配置；合并而不是覆盖其他 MCP Server；写入后验证配置；提供恢复备份入口。Generic / Manual 通用 MCP Client 手动接入不纳入首版交付功能。
6. **日志与诊断**：按 Agent / Worker 筛选日志；显示端口冲突、认证失败、客户端未连接、MCP initialize / tools/list 失败、健康检查失败等诊断信息。

GUI 显示的“已启用配置”和“当前正在运行”必须是两个独立字段，不能把 enabled=true 错报为 running。

## 8. 双机 Gateway 状态交换与冲突提示

### 8.1 状态交换原则

双方建立 AirBridge Runtime Session 并完成能力协商后，交换通用的本机 Gateway 配置状态。建议至少包含：
- Gateway 状态字段的 schema / capability 版本；
- 本机 Gateway configured-enabled（用户配置开关）；
- 可选的运行状态摘要，仅用于诊断，不携带 Agent Token、端口秘密或凭据。

交换的是本机 Gateway 启用配置，不是固定的 Controller / Runtime 角色。双方都使用同一协议和同一软件。

Gateway 状态扩展采用现有 SESSION_HELLO / SESSION_CAPABILITIES（0x15 / 0x16）的可选 Payload 字段 `gateway_state`，Feature 为 `gateway.state.v1`，不新增帧编号、不修改 Envelope schema_version=1。详细字段和双向兼容见《ADR-011：Runtime Session Gateway 状态扩展与旧版兼容》。扩展必须向后兼容，不能改变既有 ABFP 帧语义或把 EDA 领域字段加入通用握手。

### 8.2 冲突矩阵

| 本机 Gateway 配置 | 对端 Gateway 配置 | 结果 |
|---|---|---|
| ON | OFF | 正常，不提示角色冲突 |
| OFF | ON | 正常，不提示角色冲突 |
| ON | ON | 弹窗提示 Gateway 配置冲突 |
| OFF | OFF | 弹窗提示 Gateway 配置冲突 |

冲突处理：
- 仅提示，不自动修改本机或对端配置；
- 不自动停止任何 Gateway Worker；
- 提示用户分别检查两端 GUI 的 Gateway 总开关；
- 用户修改配置后，在下一次状态交换/更新时刷新提示；
- 若对端不支持该能力、状态字段缺失或链路未建立，显示“未知/未协商”，不把未知当作 OFF；
- **用户已确认的弹窗策略：** 同一连接代次内，每次进入新的冲突状态只弹一次；重复 HELLO / CAPABILITIES 或重复状态更新不重复弹窗。状态恢复为互补 / 非冲突时清除该次冲突锁存；之后再次进入冲突可再次提醒。连接代次变化时重置去重状态。
- 弹窗只提供“稍后处理”和“打开 Gateway 设置”；“稍后处理”只关闭模态弹窗，不忽略冲突。M2.5 首版不提供会话忽略或永久忽略按钮。
- Gateway 总览和通信状态页持续显示非模态冲突提示，直到状态恢复或变为 UNKNOWN。
- 冲突只警告、不阻断：不自动修改配置、不停止 Worker、不阻断 Runtime 或其它 AirBridge 操作。

### 8.3 单机与断线场景

无已配对对端的单机使用，不触发 OFF/OFF 冲突提示。链路断开时立即将对端状态标记为 UNKNOWN，清除当前冲突锁存并撤下非模态冲突提示，不将旧状态当作当前实时状态。下一连接代次重新协商后，再按新状态判断。

## 9. 生命周期与故障处理

目标行为：
- AirBridge 启动：加载配置；Gateway enabled 且 start_with_airbridge=true 时启动已启用 Agent Worker。
- 用户手动启动：只启动已启用且当前未运行的 Worker。
- 用户停止：停止接受新请求，等待在途请求有界排空，完成 MCP Server shutdown，再退出 Worker。
- GUI 关闭：按用户已确认的默认行为，对所有受管 Gateway Worker 停止接收新请求、进行有界排空并退出；首版不保留后台 Worker，不允许 Worker 成为孤儿进程。
- OAuth Broker 等共享依赖由 Supervisor 在所有 Worker 退出后按依赖顺序关闭；这是相应的生命周期实现方案，不扩展为用户另行确认的 Worker 范围。
- Worker 崩溃：GUI 标记 Failed，保留退出码和日志；是否自动重启必须有次数上限、退避和用户可见记录，不能无限重启。
- AirBridge Runtime / 串口链路故障：Gateway 可继续运行并向客户端返回明确的依赖不可用错误；不得因此误报 Gateway 进程已停止。
- 端口占用：启动失败并显示占用端口及解决建议，不自动绑定 LAN 地址。
- 配置变更：涉及监听端口、Agent ID 或 transport 的变更，采用停止—应用—启动—健康检查流程；失败时回滚到最后有效配置。

## 10. 与 AirBridge Core 的职责边界

Gateway 是通用 MCP 到 AirBridge HTTP API 的适配层。它可以提供通用能力发现、请求状态查询和已授权操作入口，但不得：
- 解析 Tcl / SKILL 正文或识别 Capture / Allegro 对象；
- 代替 Capture Skill / Allegro Skill 决定工程打开、保存、关闭流程；
- 根据 Cadence 软件安装情况决定 Gateway 角色；
- 接管 AirBridge Runtime / 串口通信进程的生命周期；
- 将 Agent 身份等同于 Runtime Session 或专业软件 Session；
- 把某个 Agent 的授权扩大为另一个 Agent 的授权。

AirBridge 继续作为透明通信通道。专业软件 API、版本兼容和领域工作流属于对应 Skill。

## 11. 非目标

- 不发布独立 AirBridgeGateway.exe。
- 不提供主机 A 专用包或主机 B 专用包。
- 不按硬件、软件安装情况或环境变量自动分配主机角色。
- 不开放公网 MCP 服务。
- 不默认授予 Shell / EDA 执行权限。
- 不实现 Agent 专属 Capture / Allegro Runtime Session。
- 不改变 M1 已验收的串口链路、ABFP 核心语义或通用 Runtime 边界。
- 不在本阶段扩展 Capture / Allegro 业务生命周期。

## 12. 待评审 / 待验证决策

| 编号 | 议题 | 需要得到的结论 |
|---|---|---|
| D-01 | Worker 进程模型 | 单 EXE 多 Worker 与单进程多 Server 的可靠性比较 |
| D-02 | MCP Transport | Streamable HTTP 的 SDK / 客户端兼容性与 stdio 迁移策略 |
| D-03 | 本地访问认证 | 不依赖 loopback 作为唯一边界的 Agent 到 Worker 身份校验 |
| D-04 | Antigravity / OpenCode 配置 | 官方支持的 transport、配置 schema、认证字段与配置路径 |
| D-05 | Python / PyInstaller | Python 3.11+ 统一打包、frozen 子进程重入、依赖体积和升级策略 |
| D-06 | GUI 关闭行为 | **用户已确认：** GUI 关闭时有界排空并停止所有受管 Gateway Worker；首版不保留后台 Worker。共享依赖的关闭顺序由 Supervisor 管理 |
| D-07 | 双机冲突策略 | 默认只警告，是否需要用户可选阻断 |
| D-08 | 配置迁移 | 旧 stdio 配置、旧 Token 环境变量和 M2.4 凭据如何无损迁移 |

## 13. 架构验收原则

M2.5 架构只有在以下事实均可验证后，才能进入代码实施：
- A/B 同一个 EXE、同一包，模块完整一致；
- Python 3.11+ 统一运行时与打包方案明确；
- Gateway 完全由 GUI 管理，GUI 能显示真实运行状态；
- Worker 生命周期、端口、日志和异常恢复有可测试设计；
- 每个 Agent 实例身份和凭据隔离；
- 本地 MCP HTTP 访问控制不是仅依赖 loopback；
- MCP Host 配置格式和目标 transport 已由实际版本验证；
- 双机 Gateway 状态交换向后兼容；
- ON/OFF 正常、ON/ON 和 OFF/OFF 冲突提示行为明确；
- 不改变 AirBridge Core 与 Capture / Allegro Skill 的职责边界。

---


## 14. 2026-11-20 官方兼容性与架构决策补充（v0.2）

本节将已完成的官方文档核查纳入架构基线。详细决策分别见 ADR-007、ADR-008、ADR-009。

### 14.1 MCP 传输结论

- Antigravity 官方文档确认远程 MCP 使用 Streamable HTTP，配置字段为 `serverUrl`；旧 HTTP+SSE / `httpUrl` 不作为目标方案。
- OpenCode 官方文档确认远程 MCP 使用 Streamable HTTP，配置字段为 `type="remote"` + `url`。
- 因此，**M2.5 托管模式选择 Streamable HTTP 作为目标传输**；现有 stdio 保留为兼容和 OAuth / HTTPS 验证未完成期间的回退路径。
- 客户端配置格式不同，必须由独立 Adapter 管理；不得用一个共享 JSON 模板覆盖两端。

### 14.2 OAuth 授权边界

M2.5 必须区分两种凭据：

1. MCP OAuth Access Token：MCP Host → Gateway Worker，证明本地客户端有权调用该 Worker。
2. M2.4 Agent Service Token：Gateway Worker → AirBridge HTTP API，从 Credential Provider 读取。

两种凭据用途、受众、签发方和生命周期不同，禁止复用。M2.4 Agent Service Token 不得写入 MCP Host 配置。

当前 MCP Python SDK 提供 HTTP Resource Server 的 TokenVerifier / AuthSettings / Protected Resource Metadata 能力，不负责完整 Authorization Server 的登录、Consent、DCR / CIMD 和 Token 签发。M2.5 必须单独选择/实现 Authorization Server。

**新的实施阻断项：** MCP Authorization 规范要求 Authorization Server endpoints 使用 HTTPS。即使 MCP Worker endpoint 仅监听 loopback，也不能因此把明文 HTTP Authorization Server 当作符合规范的最终方案。Windows 本地 HTTPS 证书信任、Antigravity / OpenCode OAuth flow 必须先完成原型验证；不得因为证书困难而去掉授权或把 M2.4 Service Token 写入客户端配置。

### 14.3 Worker 进程模型

目标采用同一个 AirBridge.exe 的内部 Worker 模式，每个 Agent 一个独立 Gateway Worker，由 GUI Supervisor 管理。不得发布 Gateway 专用 EXE。

PyInstaller onefile 独立子进程如果需要独立于父 GUI 运行，应按 PyInstaller 6.9+ 文档为新实例设置 `PYINSTALLER_RESET_ENVIRONMENT=1`，避免复用父进程退出后会被清理的临时解包目录。实际 PyInstaller 版本必须锁定并通过 frozen EXE 测试确定。

### 14.4 MCP Host 配置事实

- Antigravity App：由客户端 UI 提供实际配置文件路径；CLI 有全局 `~/.gemini/config/mcp_config.json` 和工作区 `.agents/mcp_config.json`。远程配置使用 `mcpServers.<name>.serverUrl`。IDE CIMD 需 2.6.0+，可按官方机制评估 DCR 兼容。
- OpenCode：V2 使用 `mcp.servers.<name>`，远程字段为 `type="remote"` 和 `url`，OAuth 默认启用。用户全局配置为 `~/.config/opencode/opencode.json(c)`，Windows 系统配置位于 `%ProgramData%\\opencode`，还可能存在项目配置。
- 首批专用 Adapter 仅为 Antigravity / OpenCode。Adapter 必须检测目标客户端版本、配置 schema 和路径；自动配置只写当前 Windows 用户级 global / App 用户配置。项目级和系统级配置只检测并提示，绝不由 Adapter 自动修改。
- OAuth Client 注册遵循 D-07：Antigravity `https://antigravity.google/oauth/client-metadata.json` 与 OpenCode `https://opencode.ai/oauth/opencode/client.json` 两个官方 CIMD URL 精确白名单优先；受支持客户端不支持 / 未采用 CIMD 时 DCR 兼容回退。托管配置不得写预注册 client credentials 或自定义 redirect_uri。
- CIMD / DCR registration 不等于 Grant。用户必须对每个 Agent / Resource 单独 Consent；Grant 精确绑定 OAuth Client ID、Agent instance ID、Resource Audience 和 Scope。DCR 提供的 Client Metadata 不是身份真实性证明，GUI 显示未经验证的客户端声明。禁止任意 CIMD URL fetch。

### 14.5 决策状态更新

| 决策 | 当前结论 | 状态 |
|---|---|---|
| D-01 Worker 进程模型 | 同 EXE、每 Agent 独立 Worker、Supervisor 管理；PyInstaller 独立解包 | 提议采用，待 Windows frozen 原型 |
| D-02 MCP Transport | Streamable HTTP 为托管模式目标；保留 stdio | 官方文档兼容性已确认 |
| D-03 本地访问认证 | MCP OAuth 2.1 / PKCE；M2.4 Service Token 不进入客户端配置 | 授权服务器 HTTPS 为阻断项 |
| D-04 Host 配置 | M2.5 首批专用 Adapter 仅 Antigravity / OpenCode；自动配置仅限当前 Windows 用户级配置 | 用户已确认范围；官方 schema 已核查，目标版本验收待完成 |
| D-05 Python / PyInstaller | 统一 Python 3.11+；PyInstaller 版本锁定 | 进程行为待 PoC |
| D-06 GUI 退出 | GUI 退出时有界排空并停止所有受管 Gateway Worker；不保留后台 Worker | 用户已确认；异常退出收敛仍需 PoC |
| D-07 双机冲突 | ON/ON、OFF/OFF 仅提示，不自动改配置或停服务 | 已按用户确认作为默认要求 |
| D-08 迁移 | 旧 stdio 暂时保留；客户端配置无明文 Service Token | 迁移策略待实施设计 |
| OAuth Client 注册（评审清单 D-07） | 官方 Antigravity / OpenCode CIMD URL 白名单优先；DCR 兼容回退；每 Grant 独立 Consent 并绑定 Agent / Resource / Scope | 用户已确认策略；真实客户端 OAuth / Broker 安全 PoC 待完成 |

### 14.6 本版本仍未完成的工作

- 未验证本机 OAuth Authorization Server HTTPS 在两种真实客户端中的兼容性；
- 未验证 Antigravity App / IDE / CLI 和 OpenCode 当前安装版本的完整连接；
- 未验证 PyInstaller frozen 单文件 Worker 的进程隔离和恢复；
- OAuth Client registration 行为已冻结为 CIMD allowlist 优先 + DCR fallback；Broker 实现、重定向校验、实际目标版本行为和安全测试仍待 PoC；
- 未定义最终端口范围、证书部署/更新方式和 OAuth Server 具体实现；
- 本文仍为设计评审稿，不代表 M2.5 任何功能已实现。


### 14.7 本机授权拓扑提议（ADR-010）

进一步建议由 AirBridge GUI / Gateway Supervisor 托管一个统一的本机 OAuth Broker（HTTPS Authorization Server），各 Agent Gateway Worker 则作为独立 MCP Resource Server。OAuth Broker 为各 Worker endpoint 签发不同 audience / Agent ID / Scope 的 Access Token；各 Worker 只验证发给自身的 Token。M2.4 Agent Service Token 仍由 Worker 从 Credential Provider 读取，用于 Worker → AirBridge HTTP API。

该拓扑可复用一个本机 HTTPS issuer 和证书生命周期，同时保持各 Agent Worker 的 Resource Audience 与凭据隔离。具体授权记录、Token 存储方案（Opaque Token / JWT）、证书安装和撤销流程见《ADR-010：M2.5 本机 OAuth Broker 与多 Agent Worker 授权拓扑》。此处仍是提议，必须先通过真实 Antigravity / OpenCode HTTPS OAuth 原型。


## 15. v0.3 基线复核与新增决策

### 15.1 M2.4.1 当前合并状态

截至 2026-11-20，GitHub PR #14 已合并，merge commit：
`454e1dc1e43009cb0eb5f23e85327a6135c9418a`。

当前 main 已包含 M2.4.1 Credential Provider 与 tokenless Agent 管理代码。但现有 Host A Runbook 状态尚未同步，并且其历史 EXE 对应早期提交；因此不能把该 Runbook 当作最新 merge HEAD 的完整 EXE 验收证据。M2.5 必须复用已合并 Credential Provider，同时安排最新 EXE 的基线验收。

### 15.2 Python 统一运行时的真实差距

| 组件 | 当前仓库事实 | M2.5 要求 |
|---|---|---|
| AirBridge.exe CI 构建 | Python 3.10.11 | Python 3.11+ |
| Gateway 包 | `requires-python >=3.11` | 与 AirBridge.exe 相同的 Python 3.11+ |
| MCP SDK | `mcp==2.3.0` | 统一锁定并纳入 AirBridge 构建 |
| PyInstaller | 当前构建脚本未锁定版本 | 锁定版本并验证 onefile Worker |
| GUI / Gateway | 当前为独立运行/打包体系 | 一个 AirBridge.exe、同一套打包依赖 |

M2.5.1 必须迁移 build workflow、开发环境说明和打包依赖；不得仅改变 EXE 的 Python 解释器而继续让 Gateway 使用独立 venv。

### 15.3 OAuth / TLS 传输边界

MCP Authorization 规范要求 Authorization Server endpoints 使用 HTTPS；OAuth Bearer Token Usage 规范要求客户端到 Resource Server 的 Bearer Token 请求使用 TLS。因此托管 Gateway 的两个 HTTPS 边界均需建立：
- Local OAuth Broker：HTTPS Authorization Server；
- 每 Agent Gateway Worker：HTTPS MCP Resource Server；
- HTTP loopback 仅可作为 OAuth authorization code redirect URI 的允许例外，不可作为携带 Access Token 的 MCP 资源请求通道。

目标 endpoint 统一采用稳定 loopback hostname，例如 `https://localhost:<port>/mcp`。本机证书必须包含 SAN、受目标客户端信任，且客户端必须执行正常证书链验证。

**用户已确认的证书信任策略：**
- 证书信任安装限定在当前 Windows 用户（CurrentUser）范围；不得修改 LocalMachine 证书存储区或 LocalMachine Root。
- 首次安装 / 初始配置证书时，必须说明证书用途与信任范围，并由用户明确同意后才导入；不要求管理员权限。
- 用户拒绝导入或撤销信任后，托管 Streamable HTTP / OAuth HTTPS Gateway 不可用；不得忽略证书错误、关闭 TLS 校验或降级为无认证 HTTP。
- 此决定不关闭既有 stdio 兼容路径，也不影响 AirBridge Core / Runtime。

**技术边界：** CurrentUser 根证书信任能否被目标客户端及其所用 TLS 栈共同识别，仍须用 Antigravity App / IDE、OpenCode 和系统浏览器进行 Windows 实测。不得把“写入 CurrentUser 证书存储成功”误判为所有客户端均已信任。若真实客户端无法正常验证证书，Managed HTTPS Mode 判定 No-Go，并提交新的架构决策；不得自行扩大到 LocalMachine Root 或要求管理员权限。

参考：
- [MCP Authorization Specification — Communication Security](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)
- [RFC 6750 — Bearer Token Usage](https://www.rfc-editor.org/rfc/rfc6750)
- [Microsoft New-SelfSignedCertificate](https://learn.microsoft.com/en-us/powershell/module/pki/new-selfsignedcertificate)

Microsoft 的自签名证书示例可用于 Windows 原型，但不能据此推断 Antigravity / OpenCode 一定信任该证书。证书安装 / CurrentUser 信任、SAN、重启、轮换、撤销和卸载均必须在真实目标客户端验证。

### 15.4 两类客户端 OAuth 操作由客户端完成

- Antigravity App / IDE 的官方流程要求用户在客户端 MCP Servers 页面点击 Authenticate，完成浏览器授权，再按客户端提示输入/提交授权码。Antigravity 自己维护 OAuth Token；AirBridge GUI 不读取或修改客户端 Token 文件。
- OpenCode 通过 `/mcps` 或 `opencode mcp auth <server-name>` 发起授权，并在客户端配置之外保存 OAuth 凭据。AirBridge GUI 只负责安装 endpoint 配置、显示 Broker grant 和最近认证请求状态，并可在确认客户端存在时引导/启动其授权入口。
- GUI 不得把“写入 MCP 配置成功”显示为“OAuth 已授权”或“MCP 已连接”。

参考：
- [Antigravity MCP authentication](https://antigravity.google/docs/mcp)
- [OpenCode MCP OAuth](https://opencode.ai/docs/mcp-servers/)

### 15.5 Runtime Session Gateway 状态扩展

当前 Runtime Session 两端已在连接后各自发起 HELLO。M2.5 直接扩展 0x15 / 0x16：
- HELLO 与 CAPABILITIES 可选携带 `gateway_state.schema_version=1` 和 `configured_enabled:boolean`；
- 新版 CAPABILITIES Feature 增加 `gateway.state.v1`；
- 旧版缺少字段时一律标记 UNKNOWN，不得推断为 OFF；
- 本机配置变更后重新发送 HELLO；
- 链路断开 / 重连时清除旧对端状态；
- 双方状态相同时提示，互补时正常；
- 只用于 GUI 提醒，不作为认证、授权或运行控制信号。

完整兼容规则和验收见 ADR-011。

### 15.6 当前架构评审退出 Gate

进入代码实施前需完成：
1. OAuth Broker + MCP Resource Server 双 HTTPS 的真实 Windows 客户端 PoC；
2. Antigravity 用户授权流程和 OpenCode `opencode mcp auth` / `/mcps` 流程实测；
3. 本机证书 trust bootstrap / 更新 / 卸载验证；
4. Opaque Token / grant 撤销与 Resource Audience 校验原型；
5. PyInstaller onefile 同 EXE Worker 原型；
6. Runtime Session 0x15 / 0x16 新旧版本双向测试；
7. M2.4.1 merge HEAD 的 Host A 构建与验收状态核对。

以上 Gate 未通过前，M2.5 仍处于设计 / PoC，不得以未认证 loopback HTTP 代替生产方案。
