# AirBridge M2.5：实施路线图与验收计划

- 文档编号：AB-M2.5-PLAN-001
- 版本：0.1（初稿，待评审）
- 日期：2026-11-20
- 状态：计划草案；尚未授权代码实施
- 关联文档：
  - 《AirBridge M2.5：统一运行时与 Gateway GUI 管理架构设计》
  - 《AirBridge M2.5：Gateway GUI 管理与配置交互规范》

## 1. 阶段目标

M2.5 的最终交付是一个统一的 AirBridge Windows 安装包 / AirBridge.exe：
- 主机 A、主机 B 使用同一个构建产物；
- 两端包含完全相同的 Gateway 功能；
- 用户在 GUI 中配置 Gateway ON/OFF；
- GUI 能管理 Gateway 生命周期、Agent、MCP 客户端配置，并显示真实运行状态；
- 双机 Runtime Session 能交换 Gateway 状态并在配置冲突时提示；
- 现有 AirBridge Runtime、串口通信和 M2.3 / M2.4 Agent Security 能力不回退。

文档评审通过前，不开始功能实现。

## 2. 实施前置条件

进入代码实施前必须完成：
1. 核对 M2.4 / M2.4.1 最新 PR 是否已合并，确定凭据 Provider、Provisioning API 和 Agent Registry 的实际基线。
2. 明确当前 main 与本地开发工作树的基线提交，避免将不同工作树的旧构建产物误认为当前版本。
3. 明确 Python 3.11+ 的构建、运行、测试和依赖锁定策略。
4. 完成 Antigravity / OpenCode 的 MCP Streamable HTTP 兼容性研究。
5. 完成本地 MCP Worker 访问认证设计；loopback 不能作为唯一访问控制。
6. 评审 GUI 关闭时 Gateway Worker 的退出策略。
7. 评审 Runtime Session Gateway 状态扩展字段及旧版本兼容策略。

## 3. 里程碑

### M2.5.0 — 文档与架构冻结

交付：
- 统一运行时与 Gateway 架构设计；
- GUI 管理与配置交互规范；
- 实施路线图与验收计划；
- Worker、Transport、本地认证、MCP Host 配置和 Runtime Session 扩展的 ADR。

验收：
- 用户确认同一个 EXE / 同一个安装包；
- 用户确认 A/B 仅通过 GUI Gateway enabled 配置不同；
- GUI 管理、配置、运行状态显示成为强制需求；
- 职责边界、冲突矩阵和非目标无歧义。

### M2.5.1 — Python 3.11+ 统一构建基线

工作：
- AirBridge 桌面应用和 MCP Gateway 使用统一 Python 3.11+；
- 统一依赖声明、版本锁定和构建入口；
- 更新 PyInstaller spec / build 脚本；
- 验证 MCP SDK、PySide6、串口依赖、Windows Credential Manager 依赖共存；
- 记录 EXE 体积、启动耗时和依赖冲突。

验收：
- 构建明确拒绝低于 Python 3.11 的环境；
- CI 覆盖选定的 Python 3.11+ 版本矩阵；
- AirBridge GUI、串口通信、HTTP API、Agent Security 和 Runtime Session 回归通过；
- 一个构建产物包含 Gateway 所需模块；
- 不生成或发布独立 Gateway EXE。

### M2.5.2 — 统一 EXE Gateway Worker 与 Supervisor

工作：
- 在同一个 AirBridge.exe 中增加内部 Gateway Worker 启动入口；
- GUI 主进程与 Worker 进程入口严格分流；
- Supervisor 实现启动、停止、重启、PID 记录、健康检查、日志采集和重复实例保护；
- 验证 PyInstaller frozen 单文件 EXE 重新启动 Worker 的行为；
- 实现有界优雅关闭和崩溃状态回收。

验收：
- Worker 使用与 GUI 相同的 EXE 路径和版本；
- Worker 启动不产生额外 GUI 窗口；
- GUI 退出/Worker 退出行为符合已冻结策略；
- Worker 崩溃不会导致 AirBridge Runtime 崩溃；
- 不出现孤儿 Worker 或错误 PID 认领。

### M2.5.3 — Gateway 配置持久化与 GUI 管理

工作：
- 配置 schema、版本迁移、原子写入；
- Gateway 总开关、Agent 实例开关和启动策略；
- GUI 总览、服务控制、配置页面；
- 真实状态轮询和错误显示。

验收：
- A/B 使用相同页面和功能；
- OFF 时 Gateway 不运行，但其他 AirBridge 能力正常；
- ON 时按 Agent 配置启动 Worker；
- GUI 状态与进程真实状态一致；
- 配置损坏、写入失败和端口冲突有明确恢复行为。

### M2.5.4 — MCP 持久传输与本地访问认证

工作：
- 评估并实现 Streamable HTTP；
- 明确每 Agent endpoint / 端口分配；
- 设计并实现本地客户端到 Worker 的身份认证和授权；
- 保留现有 stdio 兼容入口；
- 限制监听地址为 loopback；
- 完成 initialize、tools/list、只读工具调用与故障恢复测试。

验收：
- Antigravity / OpenCode 能分别连接自己的 Gateway 实例；
- Agent A 不能借用 Agent B 的身份或凭据；
- 未认证本机客户端不能访问受保护 MCP 能力；
- 不依赖 loopback 作为唯一安全边界；
- Token 不进入 MCP 配置、日志、命令行参数或模型上下文；
- 不开放 LAN / 公网 listener。

### M2.5.5 — Agent 管理与凭据生命周期 GUI

工作：
- GUI 添加 / 初始化 Agent；
- 凭据状态查询；
- Scope 查看和明确授权；
- 轮换、撤销和错误恢复；
- 与 M2.4 Credential Provider / Agent Security 复用。

验收：
- GUI 可完成正常 Agent provisioning；
- Token 不向用户明文暴露；
- 新 Agent 默认只读；
- 高风险权限需显式授权；
- 凭据存储失败、撤销失败和服务端不可达时状态准确，失败关闭。

### M2.5.6 — Antigravity / OpenCode MCP 配置管理

工作：
- 研究并适配两种客户端实际版本的配置 schema；
- 自动发现配置文件；
- 安全备份、合并、原子写入和校验；
- 保留其他 MCP Server 和用户自定义字段；
- 提供恢复备份能力。

验收：
- 自动配置不要求用户手工编辑 JSON；
- 原配置可恢复；
- 其他 MCP Server 不丢失、不被覆盖；
- 配置不包含 Token；
- 客户端能完成 MCP initialize 和只读工具发现/调用。

### M2.5.7 — 双机 Gateway 状态交换与冲突提示

工作：
- 在通用 Runtime Session 能力元数据中增加 Gateway enabled 状态；
- 明确字段版本、缺省值和旧版本兼容；
- 双端 GUI 展示本机/对端状态；
- 实现冲突弹窗和状态刷新。

验收矩阵：

| Host A | Host B | 预期 |
|---|---|---|
| ON | OFF | 正常，不提示冲突 |
| OFF | ON | 正常，不提示冲突 |
| ON | ON | 两端提示配置冲突 |
| OFF | OFF | 两端提示配置冲突 |

额外验收：
- 无链路、未建立会话、对端能力未知时不误报；
- 不自动修改配置；
- 默认只警告，不自动停 Gateway；
- 旧协议对端可正常协商，不因新增字段导致握手失败；
- Gateway 状态交换不改变 ABFP 核心数据传输语义。

### M2.5.8 — 双机集成回归与发布验收

在主机 A、主机 B 使用同一个最终安装包进行验收：
- A Gateway ON / B Gateway OFF；
- A Gateway OFF / B Gateway ON；
- ON/ON 冲突提示；
- OFF/OFF 冲突提示；
- GUI 服务管理与状态显示；
- Agent 独立身份和权限；
- MCP Host 自动配置、连接和只读调用；
- Gateway Worker 崩溃恢复；
- AirBridge Runtime / 串口通信 / 文件传输回归；
- Python 3.11+ CI 与 Windows EXE 构建回归。

## 4. 质量门禁

每个里程碑均要求：
- 对应单元测试与集成测试；
- 静态检查、类型/导入检查和构建检查；
- 安全测试（凭据泄露、Agent 越权、未认证访问、配置注入）；
- 明确列出未通过项，不以部分 PASS 宣称整阶段完成；
- 不在没有用户授权时对实体主机执行高风险 EDA / Shell 操作。

## 5. 回归保护

M2.5 不得破坏以下既有能力：
- M1 已验收的 AirBridge A/B 通信和串口链路；
- Runtime Session 握手、请求关联和去重；
- AirBridge HTTP API；
- M2.3 Agent Token 服务端验证、Scope、请求所有权和审计；
- M2.4 Credential Provider 的 fail-closed 行为；
- MCP 现有只读工具；
- EDA execute 默认关闭的多重门禁；
- RFS / 文件传输 / Shell 现有权限边界。

不得因为 Gateway OFF 而关闭 AirBridge Runtime 或 HTTP API。

## 6. 发布与部署模型

- 只构建和发布一个 AirBridge Windows 安装包 / EXE；
- 主机 A 和主机 B 安装同一个版本；
- 两端配置分别保存在各自本机；
- 默认安装配置建议 Gateway OFF；由用户在 GUI 中显式启用。该默认值须在产品评审中确认；
- 主机 A 可由用户在 GUI 启用 Gateway 并配置 Agent；
- 主机 B 可保持 OFF，仍具有完整 Gateway 管理能力；
- 不要求在 B 上安装不同软件或移除 Gateway 模块。

## 7. 阶段退出条件

只有在 M2.5.8 全部关键验收通过，且用户确认 A/B 双机实测结果后，M2.5 才能标记为完成。CI PASS、单机 GUI 演示或单个 MCP 客户端连接成功，均不能替代同包双机验收。

---
