# AirBridge M2.5 文档索引

- 当前评审版本：v0.2
- 日期：2026-11-20
- 状态：架构评审中；尚未进入产品代码实施
- 文档分支：`docs/m2.5-architecture`

## 一、主文档（v0.2）

1. [统一运行时与 Gateway GUI 管理架构设计 v0.2](AirBridge_M2.5_统一运行时与Gateway_GUI管理架构设计_v0.2.md)
   - 统一 AirBridge.exe、Python 3.11+、Gateway Worker、Agent 安全、Streamable HTTP、OAuth、双机状态交换与职责边界。
2. [Gateway GUI 管理与配置交互规范 v0.2](AirBridge_M2.5_GUI管理与配置交互规范_v0.2.md)
   - Gateway 总览、启停、配置、Agent 管理、Antigravity / OpenCode 配置、OAuth 授权状态、日志诊断和双机冲突提示。
3. [实施路线图与验收计划 v0.2](AirBridge_M2.5_实施路线图与验收计划_v0.2.md)
   - M2.5.0–M2.5.8 里程碑、OAuth/HTTPS Go-No-Go、PyInstaller Worker 门禁、质量门禁和双机发布验收。

## 二、架构决策记录（ADR）

1. [ADR-007：Streamable HTTP 与 OAuth 本机授权](ADR-007_M2.5_Streamable_HTTP与OAuth本机授权.md)
2. [ADR-008：统一 AirBridge.exe 的 Gateway Worker 进程模型](ADR-008_M2.5_统一EXE_Gateway_Worker进程模型.md)
3. [ADR-009：Antigravity / OpenCode MCP 配置适配规范](ADR-009_M2.5_Antigravity与OpenCode配置适配规范.md)

## 三、v0.1 初稿

以下 v0.1 文件保留为早期草稿；评审时以 v0.2 为当前版本：
- [架构设计 v0.1](AirBridge_M2.5_统一运行时与Gateway_GUI管理架构设计_v0.1.md)
- [GUI 规范 v0.1](AirBridge_M2.5_GUI管理与配置交互规范_v0.1.md)
- [路线图 v0.1](AirBridge_M2.5_实施路线图与验收计划_v0.1.md)

## 四、评审与实施约束

- 主机 A、B 使用同一个 AirBridge.exe 和相同安装包；功能不得按主机角色裁剪。
- Gateway 的启停由各自主机 AirBridge GUI 配置决定；GUI 是完整管理控制台。
- Streamable HTTP 是托管模式目标传输，旧 stdio 暂时保留。
- 本机 OAuth Authorization Server 的 HTTPS 信任和真实客户端兼容性，是 HTTP 上线前的 Go / No-Go Gate。
- M2.4 Agent Service Token 不得写入 MCP Host 配置。
- PyInstaller onefile Worker 的独立解包、进程生命周期必须通过 frozen EXE 验收。
- 文档评审通过前不修改产品代码、不构建正式 EXE、不改变 M1/M2 验收基线。
