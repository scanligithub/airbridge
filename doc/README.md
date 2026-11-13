# AirBridge 文档索引

本文档目录存放 AirBridge Core 架构、通用协议、Runtime Adapter 接入和软件生命周期设计。专业 API / 对象模型属于相应 Skill；职责边界以《AirBridge 职责边界与 Skill 集成规范 v1.0》为准。

## 当前设计基线

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
| [Capture Tcl Bridge 实测基线](Capture_Tcl_Bridge_实测基线_v1.0.md) | Capture 17.2 Runtime Adapter / Bridge 接入实测、capAutoLoad / Session、多请求和进程生命周期；附带历史 Dbo 命令枚举证据 | 2026-11-13 M1 真实 A/B 完整回归 PASS；当前新 Session PID 3156，Session ID 52f1184b-a3cf-413e-b6a0-6f02dafa9399；Dbo 命令清单不是 Adapter API 契约 |
| [AirBridge MCP Gateway：M2.1 / M2.2 实施设计](AirBridge_MCP_Gateway_M2_实施设计_v1.0.md) | 主机 A 独立 MCP stdio Gateway、四个通用 Tools、HTTP 转发、安全默认值和验收边界 | 2026-11-13：11 项单测、Python 3.11/3.12 CI、Antigravity / OpenCode 双 Host 只读和执行关闭验收 PASS；真实 EDA 脚本执行未开放 |
| [AirBridge MCP Gateway：M2.1 / M2.2 双 Host 验收记录 v1.0](AirBridge_MCP_Gateway_M2.1_M2.2_双Host验收记录_v1.0.md) | 11 项单测、CI 双 Python 版本、Host A HTTP API、Antigravity / OpenCode 四工具发现与只读调用、执行关闭保护 | 2026-11-13 双 Host PASS；未执行 Tcl / SKILL |\n| [AirBridge M2.3：Agent 身份认证、权限控制与审计设计 v1.0](AirBridge_MCP_Gateway_M2.3_Agent_Identity_Authorization_Design_v1.0.md) | 每 Agent 独立 Bearer Token、服务端 scopes、请求所有权、审计、配额和明确的非目标 | M2.3.1 代码实现及 Windows CI（Python 3.10–3.12）PASS；Host A 双 Token 实测、配额与审计加固待完成 |\n| [GitHub Actions Runtime Session CI 验收配置](GitHub_Actions_Runtime_Session_AB验收配置.md) | GitHub 托管 Windows Runner、协议单测、生产 SerialWorker 双端模拟和验收边界 | 云端 CI 配置；物理串口实测不在该 CI 范围 |
| [Capture Dbo API Tcl 示例索引 v1.0（临时归档）](Capture_Dbo_API_Tcl_示例索引_v1.0.md) | Capture Skill 的 DboSession / Design / Schematic / Page / 属性读取研究资料；仅为迁移前历史归档，不属于 AirBridge API 契约 | 后续归入 Capture Skill；不得作为 AirBridge Core / Adapter 的专业 API 契约 |

## 现有专业软件生命周期文档

- [Capture 启动、开图与关闭流程（旧版）](Capture_启动开图与关闭流程.md)：记录历史一次性启动实现。其旧版生命周期描述已被新的 Runtime Session 实现与设计基线取代。
- [Allegro PCB 启动与关闭流程](Allegro_PCB_启动与关闭流程.md)：记录现有 Allegro SKILL 启动和关闭流程；未来应按 Runtime Adapter 模型迁移，迁移前保留为现状说明。

## 文档约定

- “实测 PASS”只表示相应环境和具体测试已通过，不自动代表所有 SPB 版本兼容。
- “设计基线 / 待实现”不得描述为已实现功能。
- 代码、协议和文档发生变化时，应同步更新本目录。
- 正式实现应提供自动化测试和主机 A/B 实机验收记录。
