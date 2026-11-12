# Runtime Adapter / Profile 接口设计 v1.0

- 文档编号：AB-RUNTIME-ADAPTER-001
- 版本：1.0
- 日期：2026-11-12
- 对应阶段：P1.5
- 状态：**P1.5 GitHub Actions 软件 CI PASS**（Run #62 / commit 322904a / 66 项测试通过）；真实串口、Capture 及 Allegro 实机验收仍未完成

## 1. 目标与边界

P1.5 建立 AirBridge 通用执行层与具体 Runtime Engine 之间的稳定边界。RequestBroker 只负责请求登记、去重、结果持久化和重放；它不应直接调用 EDAExecutor、ShellEngine，也不应识别 Capture、Tcl、Allegro 或 SKILL。

本阶段完成：
- RuntimeAdapter 抽象接口；
- RuntimeExecutionRequest / RuntimeExecutionContext 通用执行上下文；
- RuntimeProfile 声明式配置对象；
- RuntimeAdapterRegistry 注册、校验、解析 Profile 并执行请求；
- 两个兼容适配器，包装当前 EDAExecutor 与 ShellEngine；
- 在 SerialWorker 的现有执行路径中通过 Profile Registry 调用适配器，保持已有 ABFP 帧、HTTP 路由和 RequestBroker 幂等语义不变。

本阶段不实现：
- SESSION_CREATE / SESSION_CLOSE / SESSION_EXEC 新帧；
- 持久化 Session Registry、每 Session FIFO 或多 Session 并发策略；
- 专业软件进程扫描、优雅关闭、启动和身份校验；
- Capture Tcl Bridge、Capture 常驻 Interpreter 或 Allegro SKILL Bridge；
- Adapter 安装、自动升级或远程分发。

上述能力分别留给 P1.6–P1.9。当前两个 Profile 是旧执行路径的兼容包装，不是 Capture 长驻 Session 已经可用的证明。

## 2. 模块职责

| 模块 | 职责 | 不应承担 |
|---|---|---|
| RequestBroker | request_id 幂等登记、payload hash 冲突检测、结果缓存和重放 | 解析脚本或选择专业软件 |
| RuntimeAdapterRegistry | Adapter / Profile 注册和匹配；执行前验证 Profile、Operation | 请求持久化、ABFP 传输、HTTP |
| RuntimeProfile | 描述可用 Adapter、Operation、能力与执行模式 | 保存密码、环境变量或任意本机秘密 |
| RuntimeAdapter | 将一个通用 Operation 转给底层 Runtime Engine，并返回结构化结果 | Agent 工作流规划或决定是否保存设计 |
| RuntimeExecutionContext | 提供每次调用可用的日志回调等通用服务 | 暴露 SerialWorker、串口对象或 GUI 控件 |
| Legacy EDA Adapter | 兼容包装原 EDAExecutor | 成为最终 Capture Session Adapter |
| Legacy Shell Adapter | 兼容包装当前 ShellEngine | 管理专业软件生命周期 |

依赖方向：

~~~text
ABFP / HTTP
    |
SerialWorker
    |
RequestBroker ---- (durable idempotency / result cache)
    |
RuntimeAdapterRegistry
    |                 |
RuntimeProfile       RuntimeAdapter
                      |
              Legacy EDAExecutor / ShellEngine
~~~

Broker 的执行回调仍可用于执行事务，但 Worker 应把实际执行封装为 Registry Operation。Broker 不接收 Runtime Engine 对象，也不需要知道 Profile 的具体类型。

## 3. Python 接口契约

### 3.1 RuntimeExecutionRequest

每次 Adapter 调用使用一个不可变 Request 对象：

~~~python
RuntimeExecutionRequest(
    operation="execute",
    request_id="optional UUID",
    payload={...},
)
~~~

- operation：通用操作名称，不写入 Capture/Tcl/Allegro/SKILL 等实现细节；
- request_id：可选 UUID；关联请求时必须与 RequestBroker 的 ID 相同；
- payload：JSON 对象，Adapter 按自己的 Operation 契约解释。

### 3.2 RuntimeExecutionContext

调用方通过 Context 提供可选日志回调。Adapter 不需要访问 Worker 的发送帧函数、Qt Signal 或全局 UI 对象。

### 3.3 RuntimeAdapter

每个 Adapter 必须提供：
- 唯一 adapter_id；
- adapter_version；
- operations：支持的通用 Operation；
- capabilities：该 Adapter 实际支持的能力标识；
- execute(request, context)：返回 dict 结构化结果；异常向上抛出，由 Broker 记录为执行失败。

P1.5 当前只定义并使用 execute 契约。install / start / wait_ready / submit / query / close 等生命周期能力将在 Session / Process Supervisor 实施阶段按需要扩展，不在本阶段伪造空实现。

### 3.4 RuntimeProfile

Profile 描述某个可选择的运行配置，至少包括：

~~~json
{
  "profile_id": "legacy-cadence-oneshot",
  "adapter_id": "legacy-cadence-eda",
  "display_name": "Legacy Cadence one-shot compatibility",
  "operations": ["execute"],
  "capabilities": ["script.execute", "one-shot-process"],
  "execution_mode": "one-shot-process",
  "enabled": true
}
~~~

Profile 只声明可执行范围和能力；它不在本阶段保存 Cadence 安装路径、完整环境变量、用户凭据或进程 PID。未来的 Capture 17.2 / Capture 17.4 等专用 Profile 可以在不改变 Broker 的情况下注册。

### 3.5 Registry 校验规则

1. adapter_id 和 profile_id 在同一 Registry 内必须唯一；
2. Profile 必须引用已注册的 Adapter；
3. Profile.operations 必须是 Adapter.operations 的子集；
4. Profile.capabilities 必须是 Adapter.capabilities 的子集；
5. disabled Profile 不允许执行；
6. 不存在的 Profile 或不允许的 Operation 必须在调用 Engine 前失败；
7. Adapter 必须返回 dict；不合约的返回类型视为 Adapter 错误；
8. 对外描述只包含安全的 Profile 元数据，不导出内部运行配置。

## 4. 当前兼容 Profile

| profile_id | adapter_id | operation | execution_mode | 说明 |
|---|---|---|---|---|
| legacy-cadence-oneshot | legacy-cadence-eda | execute | one-shot-process | 临时包装 EDAExecutor；仍由旧引擎判断 Tcl / SKILL 并按原方式启动 |
| legacy-shell-process | legacy-shell-process | execute | process-per-request | 包装现有 ShellEngine；保留同步、异步启动与现有输出回调 |

两个 Profile 通过 Engine Provider 延迟取得底层 Engine，避免 Registry 注册后测试替换或主程序重建 Engine 时持有过期对象引用。

当前 Runtime Session 握手仍返回 profiles: []。P1.5 的 Registry 是本机执行内部抽象；在 P1.6 完成真实 Capture Adapter、验证 Profile 主机可用性以及明确 A/B 端角色前，不对对端宣称已安装可用的专业 Runtime Profile。

## 5. 执行路径与兼容性

### EDA

现有 EDA_TASK_SUBMIT → RequestBroker（eda.execute）→ RuntimeAdapterRegistry（legacy-cadence-oneshot）→ LegacyCadenceOneShotAdapter → EDAExecutor。

Adapter 将旧执行器结果统一为：
- success：returncode == 0；
- returncode：原执行器返回码；
- output：文本输出。

原 EDA_TASK_FIN 帧、request_id 回显、Broker 缓存、HTTP /api/v1/eda/execute 和结果轮询行为保持不变。无 request_id 的旧 GUI 请求也走同一 Adapter，但仍保留原响应格式。

### Shell

现有 SHELL_EXEC → RequestBroker（shell.exec）→ RuntimeAdapterRegistry（legacy-shell-process）→ LegacyShellProcessAdapter → ShellEngine。

Adapter 透传现有 ShellEngine 的结构化结果；同步 / 异步语义、Shell 输出流、SHELL_RESULT 和 HTTP API 不变。Kill / List 操作仍由现有 Shell 管理路径处理，本阶段不迁移任务控制。

## 6. 失败语义

- Profile 不存在、禁用、Operation 不匹配：在调用 Engine 前抛出 RuntimeAdapterError；
- Engine 执行异常：交由外层 Broker 以执行异常记录失败结果；
- Engine 返回非预期类型：视为 Adapter 契约错误；
- EDA 非零 returncode：仍以结构化 success=false 返回，让 Broker 缓存为 failed；
- 本模块不把调用方等待超时推断成 Runtime 已取消，也不提供 exactly-once 保证。

## 7. 测试与验收

新增 test/test_runtime_adapter.py，覆盖：
- 两个兼容 Profile 注册与描述；
- EDA / Shell 参数透传与结构化结果；
- Engine Provider 延迟解析；
- 非零 EDA 返回码；
- 未知 Profile、Operation 越权和禁用 Profile；
- 重复注册、Adapter/Profile 能力不匹配；
- Request Payload / request_id 校验。

既有 A/B 软件模拟测试继续覆盖通过 Registry 执行后的 EDA / Shell RequestBroker 去重、缓存和 request_id 关联。GitHub Actions PASS 仅表示软件测试通过，不代表真实串口、Capture 或 Allegro 实机验收。

## 8. 后续演进

- P1.6：新增 Capture Tcl Adapter 与 Capture Profile；它应通过 capAutoLoad 自动建立 Tcl Bridge，并复用同一个 Capture PID / Tcl Interpreter 处理连续请求。不得把 Tcl API 写入 SerialWorker 或 RequestBroker。
- P1.7：Process Supervisor 消费 Profile 的进程匹配 / 独占策略，负责扫描所有冲突进程、优雅关闭并确认退出、启动与身份验证。
- P1.8：Capture Skill E2E。
- P1.9：新增 Allegro SKILL Adapter / Profile，不改变通用 Broker 契约。
