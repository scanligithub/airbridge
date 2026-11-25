# AirBridge M2.5 文档索引

- 当前决策版本：STDIO pivot（2026-11-20）
- 状态：M2.5 首版实现已提交至 PR #16；Host A 上 Antigravity / OpenCode 已通过 PR 构建版 EXE 的真实 STDIO 只读调用验收；PR 当前 HEAD 的六项 GitHub Actions 全部通过；Host B AirBridge.exe 哈希与 A/B 二进制一致性尚未独立核实；PR 仍为 Draft，尚未合并
- 文档分支：docs/m2.5-architecture
- 文档 PR：[PR #16](https://github.com/scanligithub/airbridge/pull/16)（Draft）

## 一、当前强制架构决策

**M2.5 首版仅使用本机 STDIO；不使用证书、TLS、HTTP / HTTPS MCP endpoint 或 OAuth Broker。**

权威决策：[ADR-013：M2.5 STDIO 本机 MCP 接入与客户端托管生命周期](ADR-013_M2.5_Stdio本机MCP接入与客户端托管生命周期.md)。

- Antigravity / OpenCode 各自启动统一 AirBridge.exe STDIO 子进程。
- MCP Host 管理 STDIO 子进程生命周期；AirBridge GUI 管理 Gateway 开关、Adapter 配置、Agent、M2.4 Credential / Scope、审计和可观察状态。
- 子进程通过现有 M2.4 Credential Provider 读取自身 Agent Service Token；Token 不进入 MCP 配置、命令行或环境变量。
- 首次安装 Gateway 默认 OFF；Gateway OFF / Agent OFF 时存量子进程也必须拒绝新的 Tool 调用。
- Host A ↔ Host B 继续使用现有 ABFP / 串口链路；没有跨机器 MCP HTTPS 服务。
- AirBridge Core 保持通用通信通道定位；Capture / Allegro 生命周期由 Skill 负责。

当前实现基线补充：main 已存在 M2.1 / M2.2 STDIO Gateway（mcp_gateway/airbridge_mcp_gateway），包括 MCP Server、Agent Credential Manager 读取、AirBridge HTTP API 客户端和基础 MCP / Credential 单元测试。M2.5 以此为基础整合进统一 AirBridge.exe / Python 3.11+，不从零重写。详细见 ADR-013 §3.1 / §10。

## 二、当前有效的架构决策记录

1. [ADR-013：STDIO 本机 MCP 接入与客户端托管生命周期](ADR-013_M2.5_Stdio本机MCP接入与客户端托管生命周期.md) — 当前首版 transport、认证、进程生命周期、Gateway OFF 语义和验收标准。
2. [ADR-008：统一 AirBridge.exe 进程模型](ADR-008_M2.5_统一EXE_Gateway_Worker进程模型.md) — 仅保留统一 EXE 与显式入口分派的参考；持久 Worker 生命周期设计已由 ADR-013 取代。
3. [ADR-009：Antigravity / OpenCode 配置适配规范](ADR-009_M2.5_Antigravity与OpenCode配置适配规范.md) — 仅保留 Adapter 和配置保护原则；具体 STDIO 配置以 ADR-013 为准。
4. [ADR-012：Agent 扩展与 MCP Client Adapter 注册机制](ADR-012_M2.5_Agent扩展与MCP_Client_Adapter注册机制.md) — 首批专用 Adapter 仅 Antigravity / OpenCode；Generic / Manual 不纳入首版。
5. [ADR-011：Runtime Session Gateway 状态扩展与旧版兼容](ADR-011_RuntimeSession_Gateway_State_Extension.md) — 复用 0x15 / 0x16，新增可选 gateway.state.v1；旧版缺失字段视为 UNKNOWN。

## 三、实机验收记录\n\n1. [Host A 双 Agent 独立配置、权限与启停验收（2026-11-24）](AirBridge_M2.5_HostA_Dual_Agent_Acceptance_20261124.md) — 独立凭据、只读 Scope、GUI 启停与恢复的实机记录。\n2. [Host A 双客户端新版 STDIO 实机验收（2026-11-25）](AirBridge_M2.5_HostA_Real_Client_STDIO_Acceptance_20261125.md) — 记录 PR #16 构建版 EXE 的 SHA-256、OpenCode / Antigravity 配置迁移、实际 STDIO 子进程路径和真实只读 Tool 调用，并明确 Host B EXE 哈希与最新 capabilities 握手的未验收边界。\n\n## 四、历史提案（不得作为当前实施要求）

以下文档保留设计过程，冲突内容全部以 ADR-013 为准：

- [ADR-007：Streamable HTTP 与本机 OAuth](ADR-007_M2.5_Streamable_HTTP与OAuth本机授权.md) — **已被取代**。
- [ADR-010：本机 OAuth Broker 与多 Agent Worker](ADR-010_M2.5_Local_OAuth_Broker.md) — **已被取代，不实施 Broker**。
- [统一运行时与 Gateway GUI 管理架构 v0.3](AirBridge_M2.5_统一运行时与Gateway_GUI管理架构设计_v0.3.md) — 历史评审稿。
- [Gateway GUI 规范 v0.3](AirBridge_M2.5_GUI管理与配置交互规范_v0.3.md) — 历史评审稿。
- [实施路线图与验收计划 v0.3](AirBridge_M2.5_实施路线图与验收计划_v0.3.md) — 历史评审稿。
- [架构评审决策清单 v0.1](AirBridge_M2.5_架构评审决策清单_v0.1.md) — 其中 CA / OAuth / G-01 决策已被 ADR-013 取代。
- [G-01 Windows HTTPS / CA PoC 手册](G-01_Windows_CurrentUser_HTTPS信任PoC操作手册_v0.1.md) — 已取消，仅保留历史记录；不可执行。

## 五、实施前置 Gate

- Antigravity App / IDE 与 OpenCode 目标版本的 STDIO 启动实测；
- 统一 AirBridge.exe 显式 STDIO 入口与 Python 3.11+ frozen build；
- stdout framing 纯净性、initialize、tools/list、Tool 调用、取消、EOF 和 broken-pipe 测试；
- 正确 Agent Credential 获取、Agent / Scope 隔离、撤销和 fail-closed 测试；
- Gateway OFF / Agent OFF 对存量 STDIO 进程立即生效；
- MCP Host reload / exit 后子进程有界退出，无孤儿进程、无 TCP listener；
- Adapter 配置写入幂等、用户其它条目保留、备份、回滚和升级迁移；
- 现有 Capture Process Inventory、AirBridge Core、Runtime Session 与 A/B 串口回归；
- A/B 使用同一 AirBridge.exe 的最终桌面验收。

## 五、继续有效的产品原则

- 主机 A、B 使用同一个 AirBridge.exe、同一个安装包和完整相同功能。
- Gateway ON/OFF 只由各自 GUI 配置决定，不按环境自动推断主机角色。
- 首次安装 Gateway 默认 OFF；升级保留有效配置、Agent 身份、M2.4 Credential 和 Scope。
- Gateway OFF 不关闭 AirBridge Runtime、ABFP 串口通信或既有 HTTP API。
- 首批专用 MCP Client Adapter 仅支持 Antigravity 与 OpenCode；不提供 Generic / Manual 通用接入。
- M2.4 Agent Service Token 不进入 MCP Host 配置；Scope、所有权、撤销、限流和审计继续由服务端执行。
- AirBridge 不接管 Capture / Allegro 业务生命周期。
