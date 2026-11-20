# AirBridge M2.5 文档索引

- 状态：初稿，待用户评审
- 日期：2026-11-20
- 文档分支：`docs/m2.5-architecture`

## 文档

1. [统一运行时与 Gateway GUI 管理架构设计 v0.1](AirBridge_M2.5_%E7%BB%9F%E4%B8%80%E8%BF%90%E8%A1%8C%E6%97%B6%E4%B8%8EGateway_GUI%E7%AE%A1%E7%90%86%E6%9E%B6%E6%9E%84%E8%AE%BE%E8%AE%A1_v0.1.md)
   - 定义统一 AirBridge.exe、Python 3.11+、Gateway Worker、传输、安全、Agent、双机状态交换和职责边界。
2. [Gateway GUI 管理与配置交互规范 v0.1](AirBridge_M2.5_GUI%E7%AE%A1%E7%90%86%E4%B8%8E%E9%85%8D%E7%BD%AE%E4%BA%A4%E4%BA%92%E8%A7%84%E8%8C%83_v0.1.md)
   - 定义 Gateway 总览、启停、配置、Agent 管理、MCP 客户端配置、日志诊断和冲突提示。
3. [实施路线图与验收计划 v0.1](AirBridge_M2.5_%E5%AE%9E%E6%96%BD%E8%B7%AF%E7%BA%BF%E5%9B%BE%E4%B8%8E%E9%AA%8C%E6%94%B6%E8%AE%A1%E5%88%92_v0.1.md)
   - 定义 M2.5.0–M2.5.8 里程碑、质量门禁、回归保护和双机发布验收。

## 评审原则

- 三份文档均为设计初稿，不代表功能已实现。
- 评审通过前不进入代码实现，不修改 M1/M2 已验收功能。
- 架构决策中的待验证项（MCP Host transport/schema、本地访问认证、PyInstaller Worker 行为等）必须在实施前关闭。
- 主机 A、B 始终使用同一构建产物；Gateway 开关只由各自 GUI 配置决定。
