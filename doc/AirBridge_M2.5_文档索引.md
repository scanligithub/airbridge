# AirBridge M2.5 文档索引

- 当前评审版本：v0.3
- 日期：2026-11-20
- 状态：架构评审中；尚未进入产品代码实施
- 文档分支：`docs/m2.5-architecture`
- 文档 PR：[PR #16](https://github.com/scanligithub/airbridge/pull/16)（Draft）

## 一、主文档（当前版本 v0.3）

1. [统一运行时与 Gateway GUI 管理架构设计 v0.3](AirBridge_M2.5_统一运行时与Gateway_GUI管理架构设计_v0.3.md)
   - 统一 AirBridge.exe、Python 3.11+、Gateway Worker、M2.4.1 真实合并基线、双 HTTPS OAuth、双 Agent 授权、Runtime Session Gateway 状态兼容。
2. [Gateway GUI 管理与配置交互规范 v0.3](AirBridge_M2.5_GUI管理与配置交互规范_v0.3.md)
   - Gateway 总览、启停、配置、Agent 管理、MCP 客户端配置、首批 Antigravity / OpenCode 授权引导、可扩展 Agent 类型、证书状态和真实运行状态显示。
3. [实施路线图与验收计划 v0.3](AirBridge_M2.5_实施路线图与验收计划_v0.3.md)
   - Python 3.11+ 统一构建、OAuth/TLS Go-No-Go、PyInstaller Worker、Host OAuth UX、Runtime Session 兼容和 A/B 双机验收。

## 二、架构决策记录（ADR）

1. [ADR-007：Streamable HTTP 与 OAuth 本机授权](ADR-007_M2.5_Streamable_HTTP与OAuth本机授权.md)
   - Streamable HTTP、OAuth、双 HTTPS 传输、客户端授权和 Bearer Token 安全。
2. [ADR-008：统一 AirBridge.exe 的 Gateway Worker 进程模型](ADR-008_M2.5_统一EXE_Gateway_Worker进程模型.md)
   - 同一个 EXE 内部启动 Worker、PyInstaller onefile 独立实例和生命周期管理。
3. [ADR-009：Antigravity / OpenCode MCP 配置适配规范](ADR-009_M2.5_Antigravity与OpenCode配置适配规范.md)
   - 两种客户端的配置 schema、OAuth 用户交互、配置优先级和 SSRF 防护。
4. [ADR-010：本机 OAuth Broker 与多 Agent Worker 授权拓扑](ADR-010_M2.5_Local_OAuth_Broker.md)
   - Opaque Token、Grant 撤销、双 HTTPS、Agent / Resource Audience 绑定和证书管理。
5. [ADR-011：Runtime Session Gateway 状态扩展与旧版兼容](ADR-011_RuntimeSession_Gateway_State_Extension.md)
   - 复用 0x15 / 0x16，新增可选 `gateway.state.v1`，兼容旧版并验证四种开关组合。
6. [ADR-012：Agent 扩展与 MCP Client Adapter 注册机制](ADR-012_M2.5_Agent扩展与MCP_Client_Adapter注册机制.md)
   - 区分客户端类型与 Agent 实例；首批专用 Adapter 仅 Antigravity / OpenCode；Generic / Manual 通用手动接入已明确延期，不纳入首版。
7. [M2.5 架构评审决策清单 v0.1](AirBridge_M2.5_架构评审决策清单_v0.1.md)
   - 区分用户已确认原则、技术提案、尚需产品决策的默认行为和实施前必须完成的 PoC Gate。

## 三、历史草稿

以下版本保留为历史评审稿；当前以 v0.3 为准：
- [架构设计 v0.2](AirBridge_M2.5_统一运行时与Gateway_GUI管理架构设计_v0.2.md)
- [GUI 规范 v0.2](AirBridge_M2.5_GUI管理与配置交互规范_v0.2.md)
- [路线图 v0.2](AirBridge_M2.5_实施路线图与验收计划_v0.2.md)
- [架构设计 v0.1](AirBridge_M2.5_统一运行时与Gateway_GUI管理架构设计_v0.1.md)
- [GUI 规范 v0.1](AirBridge_M2.5_GUI管理与配置交互规范_v0.1.md)
- [路线图 v0.1](AirBridge_M2.5_实施路线图与验收计划_v0.1.md)

## 四、不可变更的产品原则

- 主机 A、B 使用同一个 AirBridge.exe、同一个安装包和完整相同功能。
- Gateway ON/OFF 只由各自 GUI 配置决定，不按环境自动推断主机角色。
- Gateway GUI 是完整管理控制台，不是启动按钮；必须显示配置态与实际运行态。
- 首次安装 Gateway 总开关默认 OFF；用户在 GUI 中明确启用后才启动 Worker。升级安装应保留已有用户配置。
- `gateway.start_with_airbridge` 默认 ON，但受 Gateway 总开关和 Agent 实例开关约束；M2.5 首版不配置 Windows 登录自动启动 AirBridge GUI。
- Gateway OFF 不关闭 AirBridge Runtime、串口通信或 HTTP API。
- 每个 Agent 身份、OAuth Grant、M2.4 Service Credential 和 Resource Audience 相互隔离。
- OAuth Access Token 与 M2.4 Agent Service Token 不得混用；Service Token 不进入 MCP Host 配置。
- MCP Authorization Server 和携带 Bearer Token 的 MCP Resource Server endpoint 都使用 HTTPS。
- 本机 HTTPS 证书只在 CurrentUser 范围建立信任；安装时明确征求同意，不要求管理员权限、不修改 LocalMachine Root；拒绝信任时 Managed HTTPS Gateway 不可用且不得不安全降级。
- 旧 stdio 暂时保留；不能因 OAuth HTTPS PoC 不通过而降级成无认证 HTTP。
- Runtime Session 通过可选 `gateway.state.v1` 扩展交换状态；旧版缺失字段视为 UNKNOWN。
- ON/ON、OFF/OFF 只弹窗提醒，不自动改配置或停止服务。
- AirBridge 不接管 Capture / Allegro 的业务生命周期。

## 五、实施前置 Gate

- 本机 OAuth Broker + 两个 Worker endpoint 的 HTTPS 证书真实客户端验证；
- Antigravity Authenticate 与 OpenCode `/mcps` / CLI auth 验证；
- PyInstaller onefile 同 EXE Worker 独立实例及异常退出测试；
- Python 3.11+ 统一构建与依赖锁定；
- Runtime Session 新旧版本双向兼容；
- M2.4.1 merge HEAD 的 Host A EXE SHA-256 和实机验收证据。

文档评审通过前不修改产品代码、不构建正式 EXE、不改变 M1/M2 验收基线。
