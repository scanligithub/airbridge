# AirBridge 文档索引

本文档目录存放 AirBridge 架构、协议、Runtime Adapter 和软件生命周期设计。

## 当前设计基线

| 文档 | 内容 | 状态 |
|---|---|---|
| [通用 Runtime Session 架构设计 v1.0](AirBridge_Runtime_Session_架构设计_v1.0.md) | Agent Skill、AirBridge Client、Session Broker、Process Supervisor、Runtime Adapter 的边界与生命周期 | 设计基线，待实现 |
| [Runtime Session Protocol v1](AirBridge_Session_Protocol_v1.0.md) | Session / Request / Result 数据结构、ABFP 帧、HTTP API、幂等、超时和取消语义 | P1.2 / P1.3 已验收；P1.4 B 端持久化请求去重已接入、云端 CI 验收中；Session 生命周期 / Adapter 待实现 |
| [Capture Tcl Bridge 实测基线](Capture_Tcl_Bridge_实测基线_v1.0.md) | Capture 17.2 自动加载、文件队列原型、Dbo/页面操作和 A→B→Capture→B→A 实测证据 | 原型实测通过，正式 Adapter 待实现 |
| [GitHub Actions Runtime Session CI 验收配置](GitHub_Actions_Runtime_Session_AB验收配置.md) | GitHub 托管 Windows Runner、协议单测、生产 SerialWorker 双端模拟和验收边界 | 云端 CI 配置；物理串口实测不在当前 CI 范围 |

## 现有专业软件生命周期文档

- [Capture 启动、开图与关闭流程（旧版）](Capture_启动开图与关闭流程.md)：记录历史一次性启动实现。其“仅轻量 Tcl 探针、通过 DSN 命令行开图、只关闭 AirBridge managed_processes”的设计已被新的 Runtime Session 目标架构取代；请先阅读上述三份新文档。
- [Allegro PCB 启动与关闭流程](Allegro_PCB_启动与关闭流程.md)：记录现有 Allegro SKILL 启动和关闭实现。未来应按 Runtime Adapter 模型迁移，迁移前保留为现状说明。

## 文档约定

- “实测 PASS”只表示相应环境和具体测试已通过，不自动代表所有 SPB 版本兼容。
- “设计基线 / 待实现”不得描述为已实现功能。
- 代码、协议和文档发生变化时，应同步更新本目录。
- 正式实现应提供自动化测试和主机 A/B 实机验收记录。
