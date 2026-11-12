# AirBridge 文档索引

本文档目录存放 AirBridge 架构、协议、Runtime Adapter 和软件生命周期设计。

## 当前设计基线

| 文档 | 内容 | 状态 |
|---|---|---|
| [通用 Runtime Session 架构设计 v1.0](AirBridge_Runtime_Session_架构设计_v1.0.md) | Agent Skill、AirBridge Client、Session Broker、Process Supervisor、Runtime Adapter 的边界与生命周期 | P1.2–P1.7 软件实现分阶段通过 CI；Session 生命周期 / Allegro 接入 / 实机验收待后续阶段 |
| [Runtime Session Protocol v1](AirBridge_Session_Protocol_v1.0.md) | Session / Request / Result 数据结构、ABFP 帧、HTTP API、幂等、超时和取消语义 | P1.2–P1.7 已实现软件部分通过 CI；Session Create / Close 专用协议与 FIFO Broker 待实现 |
| [Runtime Adapter / Profile 接口设计 v1.0](Runtime_Adapter_Profile_v1.0.md) | 通用 Adapter 接口、Profile Registry、旧版 EDA / Shell 兼容适配器及执行边界 | P1.5–P1.7 GitHub Actions 软件 CI PASS；真实 Capture Bridge / GUI 进程关闭实机复验待完成 |
| [Capture Session Host-A E2E 验收工具 v1.0](Capture_Session_E2E_验收工具_v1.0.md) | 从 Host A 调用 HTTP API，验证两条连续 Tcl 请求复用 Capture PID / Interpreter，检查 Tcl 状态与 Dbo API | P1.8 自动化工具已提交并通过单元 CI；真实 A/B E2E 待主机联调 |
| [Process Supervisor 设计与验收 v1.0](Process_Supervisor_v1.0.md) | Capture / Allegro 进程发现、映像身份校验、WM_CLOSE、退出确认、启动阻断策略 | P1.7 软件 CI PASS；真实 Capture GUI 关闭与保存提示实机验收待完成 |
| [Capture Tcl Bridge 实测基线](Capture_Tcl_Bridge_实测基线_v1.0.md) | Capture 17.2 实测原型证据、P1.6 capAutoLoad Bridge、P1.7 ProcessSupervisor，以及 Tcl 8.6 解释器执行回归 | P1.6 / P1.7 软件 CI PASS；Capture 17.2 / Dbo / GUI 与真实进程关闭仍待实机验收 |
| [GitHub Actions Runtime Session CI 验收配置](GitHub_Actions_Runtime_Session_AB验收配置.md) | GitHub 托管 Windows Runner、协议单测、生产 SerialWorker 双端模拟和验收边界 | 云端 CI 配置；物理串口实测不在当前 CI 范围 |

## 现有专业软件生命周期文档

- [Capture 启动、开图与关闭流程（旧版）](Capture_启动开图与关闭流程.md)：记录历史一次性启动实现。其“仅轻量 Tcl 探针、通过 DSN 命令行开图、只关闭 AirBridge managed_processes”的设计已被新的 Runtime Session 目标架构取代；请先阅读上述三份新文档。
- [Allegro PCB 启动与关闭流程](Allegro_PCB_启动与关闭流程.md)：记录现有 Allegro SKILL 启动和关闭实现。未来应按 Runtime Adapter 模型迁移，迁移前保留为现状说明。

## 文档约定

- “实测 PASS”只表示相应环境和具体测试已通过，不自动代表所有 SPB 版本兼容。
- “设计基线 / 待实现”不得描述为已实现功能。
- 代码、协议和文档发生变化时，应同步更新本目录。
- 正式实现应提供自动化测试和主机 A/B 实机验收记录。
