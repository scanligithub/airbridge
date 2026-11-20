# AirBridge 文档索引

本文档目录存放 AirBridge Core 架构、通用协议、Skill 集成和迁移设计。专业 API / 对象模型及专业软件生命周期属于对应 Agent Skill；未来职责边界以 [ADR-006](ADR-006_Skill自主掌握专业软件生命周期.md) 为准，旧规范作为历史基线保留。

## 当前设计基线

| [ADR-006：专业软件生命周期由 Agent Skill 自主掌握](ADR-006_Skill自主掌握专业软件生命周期.md) | 修订旧 Runtime Adapter / EDA Engine 的专业软件生命周期所有权；明确双端同一 AirBridge.exe、Skill 使用通用 Shell / RFS / 文件传输 | 2026-11-18 已采纳；M3 迁移按阶段实施 |
| [M3.2：Capture Skill 自主生命周期实施设计 v1.0](M3.2_Capture_Skill_自主生命周期实施设计_v1.0.md) | Capture Skill 的目录结构、Session 状态机、进程发现 / 受控启动、Tcl Bridge / 请求队列、OPJ / Dbo 只读检查、授权退出和安全边界 | M3.2.1–M3.2.6 已全部合并至 main（PR #8–#13）；2026-11-19 main 集成验证：Core 每版本 27 项、Capture 全量每版本 121 项、Runtime Session / A-B 双工仿真通过；Windows x64 AirBridge.exe 构建及 Artifact 上传成功。Host B 实测覆盖受控启动、OPJ / Dbo 只读核验、两阶段授权退出和原 PID 消失确认；未保存设计提示、多实例 / PID 复用、Shell 异常和 Host-A 崩溃 / 重启恢复仍需专项实机验证
| 文档 | 内容 | 状态 |
|---|---|---|
| [AirBridge 代码边界审计与渐进迁移计划 v1.0](AirBridge_代码边界审计与渐进迁移计划_v1.0.md) | 基于当前代码审计 Core / Adapter / Skill 混合点，提出 Capture Session 生命周期抽取、Polyfill 解耦、显式 Profile 路由与 Session 协议实施顺序 | 2026-11-13 静态审计完成；M1 已合并并实机验收；后续职责迁移渐进推进 |
| [AirBridge 职责边界与 Skill 集成规范 v1.0](AirBridge_职责边界与Skill集成规范_v1.0.md) | 冻结 Core、Runtime Adapter、软件内 Bridge 与 Agent Skill 的职责边界；Capture / Allegro API 归属、版本兼容、代码迁移和验收分层 | 2026-11-13 架构边界基线已建立；现有兼容代码按后续阶段渐进迁移 |
| [通用 Runtime Session 架构设计 v1.0](AirBridge_Runtime_Session_架构设计_v1.0.md) | Agent Skill、AirBridge Client、Session Broker、Process Supervisor、Runtime Adapter 的边界与生命周期 | M1 Capture 17.2 / Tcl 8.6 启动、READY、错误恢复、并发串行、WM_CLOSE 关闭和新 Session 重启实机 PASS；专用 Session API、完整 FIFO Broker 和 Allegro 实机验收仍待后续阶段 |
| [Runtime Session Protocol v1](AirBridge_Session_Protocol_v1.0.md) | Session / Request / Result 数据结构、ABFP 帧、HTTP API、幂等、超时和取消语义 | 当前已实现能力协商、EDA/Shell 请求关联及 Request 去重；SESSION_CREATE / SESSION_EXEC / SESSION_CLOSE 专用消息仍属后续目标 |
| [Runtime Adapter / Profile 接口设计 v1.0](Runtime_Adapter_Profile_v1.0.md) | 通用 Adapter 接口、Profile Registry、旧版 EDA / Shell 兼容适配器及执行边界 | M1 Capture Adapter 在真实主机 B 完成启动、持久化、错误隔离、并发串行、WM_CLOSE 和重启验收；保存提示等边界场景待验收 |
| [Capture Session Host-A E2E 验收工具 v1.0](Capture_Session_E2E_验收工具_v1.0.md) | 从 Host A 调用 HTTP API，验证连续 Tcl 请求复用 Capture PID / Interpreter；不是 Capture Skill 专业 API 验收 | 工具及历史 CI 保留；本次 M1 完整实机回归另有独立 Request ID / PID / Session 证据 |
| [Allegro SKILL Runtime Adapter v1.0](Allegro_SKILL_Runtime_Adapter_v1.0.md) | 将 Allegro SKILL 从通用 Cadence fallback 路由到独立 Adapter / Profile，保持既有启动、开板和 GUI 生命周期行为 | 软件 CI 历史证据保留；Host B Allegro 实机回归及生命周期扩展待验收 |
| [Process Supervisor 设计与验收 v1.0](Process_Supervisor_v1.0.md) | Capture / Allegro 进程发现、映像身份校验、WM_CLOSE、退出确认、启动阻断策略 | M1 真实 Capture PID 5536 经 WM_CLOSE 正常退出；保存提示、多手动实例、无主窗口和无响应场景仍待专项验收 |
| [M3.2.5：Capture OPJ 打开与 Dbo 只读查询实施记录](M3.2.5_Capture_OPJ_打开与Dbo只读查询_实施记录.md) | OPJ 显式授权打开、Dbo / Project Manager Active Design 身份校验、当前 Session Design / Library 只读枚举 | PR #12 已合并；Hosted CI 每版本 96 项通过；2026-11-19 Host B Capture 17.2 集成验收中真实打开 OPJ、核验 Active Design / Project Manager 同一对象，并通过 1 Design / 0 Library 的 Dbo 枚举与资源释放检查（详见 M3.2.6 记录）
| [M3.2.6：Capture 授权退出与进程确认实施记录](M3.2.6_Capture_授权退出与进程确认_实施记录.md) | 两阶段授权 Exit、持久化意图、Schedule Receipt / 脚本哈希核验、原 PID 退出确认和跨 Host-A 重启只读协调 | PR #13 已合并；Hosted CI 每版本 121 项通过；2026-11-19 Host B Capture 17.2 完成受控启动、真实 OPJ / Dbo 只读核验、两阶段授权退出及原 PID 消失确认。保存提示、多实例、PID 复用、Shell 故障和 Host-A 崩溃恢复仍未完成实机验收
| [Capture Tcl Bridge 实测基线](Capture_Tcl_Bridge_实测基线_v1.0.md) | Capture 17.2 Runtime Adapter / Bridge 接入实测、capAutoLoad / Session、多请求和进程生命周期；附带历史 Dbo 命令枚举证据 | 2026-11-13 M1 真实 A/B 完整回归 PASS；当前新 Session PID 3156，Session ID 52f1184b-a3cf-413e-b6a0-6f02dafa9399；Dbo 命令清单不是 Adapter API 契约 |
| [AirBridge MCP Gateway：M2.1 / M2.2 实施设计](AirBridge_MCP_Gateway_M2_实施设计_v1.0.md) | 主机 A 独立 MCP stdio Gateway、四个通用 Tools、HTTP 转发、安全默认值和验收边界 | 2026-11-13：11 项单测、Python 3.11/3.12 CI、Antigravity / OpenCode 双 Host 只读和执行关闭验收 PASS；真实 EDA 脚本执行未开放 |
| [AirBridge MCP Gateway：M2.1 / M2.2 双 Host 验收记录 v1.0](AirBridge_MCP_Gateway_M2.1_M2.2_双Host验收记录_v1.0.md) | 11 项单测、CI 双 Python 版本、Host A HTTP API、Antigravity / OpenCode 四工具发现与只读调用、执行关闭保护 | 2026-11-13 双 Host PASS；未执行 Tcl / SKILL |
| [AirBridge M2.3：Agent 身份认证、权限控制与审计设计 v1.0](AirBridge_MCP_Gateway_M2.3_Agent_Identity_Authorization_Design_v1.0.md) | 每 Agent 独立 Bearer Token、服务端 scopes、请求所有权、审计、配额和明确的非目标 | M2.3.1 / 2.3.2 代码 PASS；Agent Security Windows CI（Python 3.10–3.12）及 Gateway CI（Python 3.11–3.12）PASS；2026-11-17 Host-A 双 Agent 只读实机验收 PASS；PR #5 已于 2026-11-18 合并到 main |
| [AirBridge M2.4：Zero-Touch Agent Authentication 设计 v1.0](AirBridge_M2.4_Zero_Touch_Agent_Authentication_Design_v1.0.md) | Agent Registry、Windows Credential Manager、受信任本机注册、Gateway 凭据透明读取、轮换/撤销和旧 Agent 迁移 | 2026-11-20：PR #14 已合并（merge commit `454e1dc1e43009cb0eb5f23e85327a6135c9418a`）；M2.4.1 tokenless provision、Gateway 透明读取、Credential Manager、生命周期 / Audit / revoke 硬化代码进入 main。现有 Host A Runbook 仍记录早期 EXE source commit `35dd48d` 和旧 PR 状态；最新 merge HEAD 的 EXE 需重新构建并完成 Host A 回归后才能标记为最新实机验收 |
| [AirBridge M2.5：统一 Gateway 架构与 GUI 管理设计 v0.3](AirBridge_M2.5_文档索引.md) | 同一 AirBridge.exe / Python 3.11+、Gateway GUI 管理、Streamable HTTP + HTTPS OAuth、本机 OAuth Broker；首批专用 Adapter 仅 Antigravity / OpenCode；官方 CIMD 白名单优先、DCR 兼容回退、每 Agent 独立 Consent；Generic / Manual 通用手动接入不纳入首版；首次安装 Gateway 默认 OFF；升级保留 Gateway / Agent / OAuth Grant / CurrentUser 证书 / MCP 用户配置状态，版本化备份迁移与回滚；Runtime Session Gateway State 扩展及双机验收 | 2026-11-20：Draft PR #16；产品决策 D-01 至 D-09 已确认，技术 PoC 评审继续；G-01 CurrentUser TLS 信任研究为 Partial Research / NOT PASS；仅文档变更，未修改产品代码 / CI / EXE；HTTPS / OAuth、frozen Worker、升级迁移 PoC 尚未完成 |
| [AirBridge M2.5 架构评审决策清单 v0.1](AirBridge_M2.5_架构评审决策清单_v0.1.md) | 汇总已确认产品约束、建议技术方案、待产品决定项和实施前必须通过的 PoC Gate | 2026-11-20：随 M2.5 Draft PR #16 评审 |\n| [AirBridge M2.3 Host-A 双 Agent 只读验收记录 2026-11-17](AirBridge_M2.3_HostA_Acceptance_Record_20261117.md) | M2.3 EXE 构建、Antigravity / OpenCode 独立 Token、401/200/404、MCP 连接、撤销与审计、CI 证据和未覆盖边界 | 2026-11-17 Host-A 只读验收 PASS；未执行 EDA / Shell |
| [AirBridge M2.3 Host A 只读安全验收操作手册 v1.0](AirBridge_M2.3_HostA_ReadOnly_Acceptance_Runbook_v1.0.md) | Host A 分别签发 Antigravity / OpenCode 只读 Token，检查 401、capabilities、request.read、撤销和审计；不发送 EDA / Shell 请求 | 操作手册；对应实机验收已 PASS，详见验收记录 |
| [GitHub Actions Runtime Session CI 验收配置](GitHub_Actions_Runtime_Session_AB验收配置.md) | GitHub 托管 Windows Runner、协议单测、生产 SerialWorker 双端模拟和验收边界 | 云端 CI 配置；物理串口实测不在该 CI 范围 |
| [Capture Dbo API Tcl 示例索引 v1.0（临时归档）](Capture_Dbo_API_Tcl_示例索引_v1.0.md) | Capture Skill 的 DboSession / Design / Schematic / Page / 属性读取研究资料；仅为迁移前历史归档，不属于 AirBridge API 契约 | 后续归入 Capture Skill；不得作为 AirBridge Core / Adapter 的专业 API 契约 |

## 现有专业软件生命周期文档

- [Capture 启动、开图与关闭流程（旧版）](Capture_启动开图与关闭流程.md)：记录历史一次性启动实现。其旧版生命周期描述已被新的 Runtime Session 实现与设计基线取代。
- [Allegro PCB 启动与关闭流程](Allegro_PCB_启动与关闭流程.md)：记录现有 Allegro SKILL 启动和关闭流程；未来应按 Runtime Adapter 模型迁移，迁移前保留为现状说明。

## 文档约定

- “实测 PASS”只表示相应环境和具体测试已通过，不自动代表所有 SPB 版本兼容。
- “设计基线 / 待实现”不得描述为已实现功能。
- 代码、协议和文档发生变化时，应同步更新本目录。
- 正式实现应提供自动化测试和主机 A/B 实机验收记录。
