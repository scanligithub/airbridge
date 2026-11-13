# Runtime Adapter / Profile 接口设计 v1.0

- 文档编号：AB-RUNTIME-ADAPTER-001
- 版本：1.0
- 日期：2026-11-12
- 对应阶段：P1.5–P1.9.1
- 状态：**P1.5–P1.7 与 P1.9.1 软件 CI PASS；2026-11-12 Capture 17.2 Bridge 和 Start Page 正常关闭/重启已在主机 B 实机 PASS；保存提示等边界场景及 Allegro 实机回归仍待完成**

## 1. 目标与边界

P1.5 建立 AirBridge 通用执行层与具体 Runtime Engine 之间的稳定边界。RequestBroker 只负责请求登记、去重、结果持久化和重放；它不应直接调用 EDAExecutor、ShellEngine，也不应识别 Capture、Tcl、Allegro 或 SKILL。

本阶段完成：
- RuntimeAdapter 抽象接口；
- RuntimeExecutionRequest / RuntimeExecutionContext 通用执行上下文；
- RuntimeProfile 声明式配置对象；
- RuntimeAdapterRegistry 注册、校验、解析 Profile 并执行请求；
- Cadence 兼容 Adapter、Allegro SKILL 专用 Adapter、Shell Adapter，分别包装当前 EDAExecutor 与 ShellEngine；
- 在 SerialWorker 的现有执行路径中通过 Profile Registry 调用适配器，保持已有 ABFP 帧、HTTP 路由和 RequestBroker 幂等语义不变。

本阶段不实现：
- SESSION_CREATE / SESSION_CLOSE / SESSION_EXEC 新帧；
- 持久化 Session Registry、每 Session FIFO 或多 Session 并发策略；
- 专业软件进程扫描、优雅关闭、启动和身份校验；
- Capture Tcl Bridge、Capture 常驻 Interpreter 或 Allegro SKILL Bridge；
- Adapter 安装、自动升级或远程分发。

上述能力分别留给 P1.6–P1.9。当前两个 Profile 是旧执行路径的兼容包装，不是 Capture 长驻 Session 已经可用的证明。



## 1.1 与 Agent Skill 的职责边界

本接口文档必须与《AirBridge 职责边界与 Skill 集成规范 v1.0》配套阅读。

Runtime Adapter 只负责目标软件的技术接入：安装发现、进程启动、Bridge 初始化、READY / PID / 运行时版本报告、不透明脚本请求投递、结果回传和退出确认。Adapter 可以识别 Capture.exe、Tcl 版本、capAutoLoad 路径等接入细节，但不能封装 Dbo API、设计对象模型或专业操作流程。

Capture Skill 负责 Dbo Tcl API、对象关系、调用签名、Capture 版本 API 差异、设计读写及保存策略。Allegro Skill 负责 Allegro SKILL API 与 PCB 专业工作流。Adapter 的 capabilities 应描述 persistent-interpreter、script.execute、graceful-close 等运行时能力，不应宣称 Dbo.ReadDesign、Net.Edit 等领域功能。

Core / Broker 将脚本 Payload 作为不透明内容传递。未来优先采用显式 profile_id 与 content_type 路由；不应依赖在通用 Core 中扫描 Tcl / SKILL 源码关键字来推断专业软件或 Skill。

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
| allegro-skill-oneshot | allegro-skill-legacy | execute | one-shot-gui | 只接受 Allegro SKILL；保留既有 EDAExecutor 启动与 GUI 行为 |
| legacy-cadence-oneshot | legacy-cadence-eda | execute | one-shot-process | 未匹配到专用 Profile 时的兼容 fallback |
| capture-tcl-session | capture-tcl-session | execute | persistent-gui-session | Capture Tcl 专用 Adapter；复用一份 Capture PID / Tcl Interpreter，通过 capAutoLoad Bridge 串行执行请求 |
| legacy-shell-process | legacy-shell-process | execute | process-per-request | 包装现有 ShellEngine；保留同步、异步启动与现有输出回调 |

两个 Profile 通过 Engine Provider 延迟取得底层 Engine，避免 Registry 注册后测试替换或主程序重建 Engine 时持有过期对象引用。

当前 Runtime Session 握手仍返回 profiles: []。P1.5 的 Registry 是本机执行内部抽象；在 P1.6 完成真实 Capture Adapter、验证 Profile 主机可用性以及明确 A/B 端角色前，不对对端宣称已安装可用的专业 Runtime Profile。

## 5. 执行路径与兼容性

### EDA

现有 EDA_TASK_SUBMIT → RequestBroker（eda.execute）→ RuntimeAdapterRegistry。当前分支暂时通过脚本类型识别选择 `capture-tcl-session` / `allegro-skill-oneshot`，另保留 `legacy-cadence-oneshot` fallback；这是过渡兼容行为，不是目标架构要求。目标状态由 Skill 明确选择 profile_id，并通过 content_type 声明脚本格式，Core / Broker 不解析脚本正文、不根据 Dbo 或 axl 关键字猜测专业语义。Capture Tcl 路径再由 CaptureTclAdapter → CaptureTclBridgeSession → capAutoLoad Bridge 执行；Allegro 由对应 Adapter 接入。

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

- P1.6（软件实现、CI 与 Capture 17.2 Host B 实机 PASS）：CaptureTclAdapter + capAutoLoad Bridge，READY nonce/PID 校验、持久 Interpreter、结构化结果与 UNKNOWN 超时语义；Host B 已验证 Bridge 自动加载、多请求、错误恢复、并发串行和重启复用。
- P1.7（软件实现与软件 CI PASS；Capture Start Page 关闭实机 PASS）：ProcessSupervisor 消费 app_id / ProcessSpec，扫描全部同名进程、校验映像身份、检查阻塞对话框、发送 WM_CLOSE、等待进程退出；真实 Host B 已验证 Capture PID 6648 的 WM_CLOSE 和进程退出。保存提示、多手动实例、无主窗口和无响应场景仍待实机验收。
- P1.8：Capture Skill E2E。
- P1.9：新增 Allegro SKILL Adapter / Profile，不改变通用 Broker 契约。

## 9. P1.6 Capture Tcl Session Adapter

P1.6 的 `CaptureTclAdapter` 不调用 `EDAExecutor.execute_script()` 启动每条 Tcl 脚本，而是取得 / 复用 B 端当前 Session 的 `CaptureTclBridgeSession`，把脚本写入 Session 专属请求队列。Capture 自己的 capAutoLoad Tcl Bridge 负责认领并在同一解释器内执行。

该 Adapter 当前仅在主机 B 本地 Registry 中注册，**不通过 SESSION_CAPABILITIES 广播**，因为主机 A 还没有主机级 Profile 可用性探测。P1.6 仍复用现有 EDA ABFP 帧，不代表 Session Create、Session FIFO Broker 或统一 SESSION_EXEC 协议已经完成。

P1.6 软件验收先在 [Run #76](https://github.com/scanligithub/airbridge/actions/runs/43148279572) 完成 80 项测试；后续 [Run #88](https://github.com/scanligithub/airbridge/actions/runs/43150162732) 又将生成的 bridge.tcl 直接 source 到标准 Tcl 8.6 解释器，执行文件队列请求并校验 READY / 返回值 / stdout / result 文件。Run #88 完整单元 + A/B + HTTP 共 90 项通过。**标准 Tcl 解释器不提供 Capture Dbo API 或真实 GUI。新版 Bridge 已于 2026-11-12 在真实 Capture 17.2 / Tcl 8.6.0 上单独完成实机验证；该实机验收验证的是接入和传输，不是 Dbo API 的业务支持。**



### 9.1 Capture Adapter 的专业边界

CaptureTclAdapter / CaptureTclBridgeSession 只管理 Capture 运行时和 Tcl 传输：启动与复用 Capture、建立 capAutoLoad Bridge、校验 READY、串行提交脚本、接收结构化结果。传入的 Tcl 正文对 Adapter 是不透明 Payload。

Adapter 不调用、包装或维护 DboDesign / DboSchematic / DboPage / Pin / Net 等专业对象 API。诸如 GetActiveDesign、NewDesignsIter、GetRootSchematic、属性读取/写入等调用签名与版本差异，均由 Capture Skill 维护。保存决策同样归 Skill；Adapter 只执行显式提交的 Tcl 请求。

## 10. P1.7 Process Supervisor 接入

P1.7 的通用实现见 [Process Supervisor 设计与验收 v1.0](Process_Supervisor_v1.0.md)。Capture Session 创建前，Worker 调用 `ProcessSupervisor.ensure_exclusive("capture")`；健康的当前 Worker-owned Session 直接复用，不触发关闭。旧进程（包含手动启动的 Capture.exe）则先做窗口检查，再请求 WM_CLOSE 并等待真实退出。

P1.7 软件验收见 [GitHub Actions Run #85](https://github.com/scanligithub/airbridge/actions/runs/43149369815)：ProcessSupervisor 9 项、全套单元 68 项、A/B Worker 12 项、HTTP 集成 9 项，合计 89 项通过。Windows Runner 实际验证当前 Python 进程发现、映像路径查询和窗口枚举；多 Capture 关闭、阻塞对话框与超时模拟场景使用 FakeProcessBackend。**此后已于 2026-11-12 在主机 B 对真实 Capture Start Page PID 6648 发送 WM_CLOSE，并独立确认进程退出；保存提示、多手动实例等边界场景仍未实机验收。**



## 11. P1.9.1 Allegro SKILL Adapter / Profile 隔离

新增 `AllegroSkillAdapter`（`allegro-skill-legacy`）和 `allegro-skill-oneshot` Profile。Worker 使用 `select_eda_profile()` 按 `detect_script_type()` 结果分流：
- Allegro SKILL → `allegro-skill-oneshot`；
- Capture Tcl 且 Capture Session Adapter 已注册 → `capture-tcl-session`；
- 其他兼容请求 → `legacy-cadence-oneshot`。

Allegro Adapter 在调用 EDAExecutor 前验证脚本确属 Allegro SKILL，避免 Tcl 请求误入 Allegro 执行路径。它把 timeout、version、target_file、keep_open 和日志回调原样交给现有 EDAExecutor，返回形状保持 `success / returncode / output`。因此本阶段不改变远程选择 BRD、SKILL 启动、`axlOpenDesign()` 自动开板、GUI 保持运行和独立关闭行为。

**本阶段明确没有做：** 不把 Allegro 生命周期切换到 ProcessSupervisor；不增加 SKILL 常驻 Interpreter / 文件队列；不加入 SESSION_CREATE / CLOSE 或新 ABFP 帧；不自动关闭用户手动启动的 Allegro；不改变保存提示处理。以上需要单独设计和实机验收。

软件测试覆盖专用 Profile 注册、SKILL 参数透传、日志回调、非零退出码、拒绝 Tcl 误路由，以及 Capture / Allegro / fallback 选择。软件 CI 通过不等同于本次新增路由已在 Host B Allegro 17.2 GUI 中实机回归。


P1.9.1 软件验收见 [GitHub Actions Run #94](https://github.com/scanligithub/airbridge/actions/runs/43158226213)：Protocol Unit Job 81 项、A/B duplex + HTTP Job 21 项，合计 102 项测试通过、0 项失败。该运行覆盖了 Allegro 专用 Profile 注册、脚本类型校验、参数与日志回调透传、非零退出码及 EDA Profile 选择。它只证明软件层路由与兼容契约，不代表真实 Allegro 17.2 GUI 已在此次提交后重新启动并开板。
