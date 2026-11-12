# AirBridge 通用 Runtime Session 架构设计

- 文档编号：AB-ARCH-SESSION-001
- 版本：1.0
- 状态：**目标架构设计基线；P1.2–P1.6 通用软件层与 Capture Adapter 已通过对应 GitHub Actions CI；P1.6 新版 Bridge 的真实 Capture 17.2 实机复验、P1.7 Process Supervisor、P1.8 Skill E2E 与 P1.9 Allegro Adapter 待完成**
- 日期：2026-11-12
- 适用范围：AirBridge 主机 A / 主机 B；未来所有通过 Agent Skill 使用远端专业软件的场景

> 本文定义目标架构，不代表当前 main 分支已经实现 Session Create / Close API、统一 Session Request 帧或 Process Supervisor。功能分支已完成 P1.5 Runtime Adapter / Profile 与 P1.6 Capture Tcl Adapter / Bridge 的软件级实现；新版 Bridge 尚待真实 Capture 17.2 实机复验。

---

## 1. 设计目标

AirBridge 需要从“一次性远程脚本启动器”演进为“通用远程 Runtime Session 基础设施”，使主机 A 上的 Agent Skill 能够：

1. 在主机 B 上创建一个受控的软件运行会话；
2. 在同一个软件进程中连续提交多条独立请求；
3. 获取每条请求的结构化结果、日志和错误；
4. 根据结果继续决策并提交下一条请求；
5. 在工作完成后保存专业数据，并优雅关闭会话；
6. 在通信中断、进程退出或超时时，准确识别会话与请求状态。

本架构必须支持后续 Capture Skill、Allegro Skill 及其他专业 Skill，不能将 Capture、Tcl、Dbo、Allegro 或 SKILL 的专业语义写入 AirBridge 核心。

## 2. 非目标

AirBridge 核心不负责：

- 决定原理图应如何修改、PCB 如何布线或如何检查设计；
- 解释专业软件的数据库对象和专业 API；
- 替代 Agent Skill 进行工作流规划和结果判断；
- 自动决定是否丢弃未保存的专业设计；
- 将每次脚本请求都转换成一次新的 GUI 进程启动；
- 将等待超时等同于脚本已取消或进程已退出。

专业操作由 Skill 决策；专业软件内部的 Runtime Adapter 负责执行对应脚本并与软件 API 交互。

## 3. 总体架构与职责边界

### 3.1 主机 A：Agent Skill 与 AirBridge Client

**Agent Skill** 负责专业工作流程、操作顺序、领域知识和结果判断。它通过稳定的 HTTP API 使用 AirBridge，不应直接依赖 AirBridge Python 私有函数。

**AirBridge Client** 负责：

- HTTP API；
- 生成并关联 session_id / request_id；
- 维护 HTTP 等待者与 ABFP 返回消息的关联；
- 将结构化请求发送到主机 B；
- 将状态、结果和错误返回给调用方。

### 3.2 主机 B：AirBridge Session Broker

Session Broker 是通用模块，职责包括：

- Session 创建、登记、状态维护和关闭；
- 请求队列、请求去重、状态和结果缓存；
- 调用 Process Supervisor；
- 根据 profile_id 选择 Runtime Adapter；
- 将请求交给 Runtime Adapter；
- 将 Adapter 结果封装为通用 Result；
- 处理掉线、重连、超时和恢复。

Session Broker 不解析 Tcl、SKILL、Dbo 或其他专业脚本内容。

### 3.3 Process Supervisor

Process Supervisor 是通用进程管理模块，负责：

- 按 Runtime Profile 的进程匹配规则发现进程；
- 记录 PID、可执行文件路径、进程创建时间等身份信息；
- 对冲突进程发起优雅关闭；
- 等待并确认进程真正退出；
- 在旧进程未退出时阻止新实例启动；
- 启动受控进程并监测进程退出。

PID 不能单独作为进程身份。必须同时校验可执行文件路径和进程创建时间，以避免 PID 重用造成误判。

### 3.4 Runtime Adapter 与专业软件内 Bridge

Runtime Adapter 是主机 B 上的可插拔适配器，负责实现统一接口：

- install / verify（安装或检查专业软件侧 Bridge）；
- start_runtime（启动软件进程）；
- wait_ready（等待带本次 session_id / startup_nonce 的 READY）；
- submit_request（将通用 Request 投递给专业软件 Bridge）；
- query_request（读取或恢复请求状态）；
- close_runtime（发起专业软件退出并确认进程退出）。

专业软件内 Bridge 是 Adapter 的执行端。例如：

- Capture Tcl Adapter + Capture Tcl Bridge；
- Allegro SKILL Adapter + Allegro SKILL Bridge；
- 未来其他应用自己的 Runtime Adapter。

Adapter 可以作为 Skill 配套组件或独立安装包发布，但不得把专业工作流决策塞进 AirBridge 核心。

### 3.5 Runtime Profile

Profile 是声明式运行配置，由 Adapter / 专业软件集成包注册，AirBridge 核心只读取通用字段。

示例：

~~~json
{
  "profile_id": "capture-17.2",
  "adapter_id": "capture-tcl-filequeue",
  "exclusive_group": "cadence.capture",
  "process_match": {
    "image_names": ["Capture.exe"],
    "scope": "host"
  },
  "startup_timeout_ms": 60000,
  "close_timeout_ms": 30000,
  "transport": "local-file-queue"
}
~~~

上例仅为设计示意。当前尚未实现 Profile Registry。

对于当前 Capture 需求，Profile 的冲突组应能覆盖主机 B 上已有的 Capture 实例，包括用户手动启动的 Capture。不能只检查 AirBridge 自己启动并登记的进程。若发现旧实例无法优雅退出，必须停止新会话启动并通知用户，不得强制杀进程。

## 4. Session 模型

Session 是受 AirBridge 管理的远端 Runtime 实例，不等同于 PID。

建议字段：

| 字段 | 含义 |
|---|---|
| session_id | 全局唯一会话 ID（UUID） |
| profile_id | 使用的 Runtime Profile |
| adapter_id / adapter_version | Adapter 标识和版本 |
| host_id | 远端主机标识 |
| runtime_pid | 专业软件 PID |
| process_image / process_path | 进程映像和完整路径 |
| process_created_at | 进程创建时间 |
| runtime_version | 专业软件版本 |
| state | 会话状态 |
| startup_nonce | 本次启动随机令牌，防止旧 READY 文件被误认 |
| created_at / updated_at | 创建和更新时间 |

Session 状态：

- CREATING：创建请求已接受；
- STARTING：正在检查旧进程、启动新进程及初始化 Bridge；
- READY：Runtime Bridge 已以本次 session_id / startup_nonce 报告就绪；
- BUSY：至少一个请求正在执行；
- STOPPING：正在结束会话；
- CLOSED：进程已确认退出；
- FAILED：创建或初始化失败；
- LOST：运行期间进程意外消失，或无法确认 Runtime 状态。

只有 READY 状态接受新任务。BUSY 状态可接受排队请求，但同一 Session 内仍然串行执行。

## 5. Session 创建与进程生命周期

推荐顺序：

1. A 端为会话生成 session_id 和 startup_nonce，发送 Session Create。
2. B 端按 profile_id 获取进程冲突规则，并取得对应 exclusive_group 锁。
3. 扫描主机 B 上所有匹配进程；Capture Profile 当前要求检查所有 Capture.exe，而非只查 managed_processes。
4. 对已有实例的主窗口发送正常关闭请求（Windows 可使用 WM_CLOSE），等待每个 PID 真正退出。
5. 不自动关闭未知模态对话框，不自动选择保存或丢弃。超时或仍有进程存活时，Session 创建失败，不能启动第二个实例。
6. 再次扫描，确认冲突组没有残留进程。
7. 为新 Session 创建独立工作目录，写入包含 session_id / startup_nonce 的启动清单。
8. 安装或启用专业软件侧 Bridge，启动受控软件进程。
9. 等待 Bridge 返回带匹配 session_id / startup_nonce、PID、版本和 Adapter 版本的 READY。
10. Process Supervisor 交叉验证 READY PID 与 Windows 进程身份一致后，Session 才进入 READY。

**不得用静态、可复用的 ready 文件作为唯一启动成功判据。** 启动文件应按 session_id 隔离，并通过临时文件 + 原子重命名发布。

### 5.1 Capture Profile 的已验证行为

当前 OrCAD Capture 17.2 已验证：

- Capture 启动时自动加载 capAutoLoad 目录内的 Tcl 文件；
- Tcl Bridge 可以通过 Tcl after 轮询本地任务队列；
- 可以在同一 Tcl Interpreter 内连续执行任务；
- 可以调用 Open 打开 OPJ；
- 可以使用 GetActivePMDesign、Dbo Session 的 GetActiveDesign 取得活动设计；
- 可以使用 OPage 切换页面；参数顺序为 schematic name、page name；
- 页面级 GetActivePage 需要先有 SchematicView 上下文；
- 可以通过 Tcl 调用 Project Manager 的 File::Save；
- Exit 可以正常关闭 Capture。

这些是当前 Capture 17.2 的实测基线，不意味着所有 Cadence 版本都具有完全相同行为。详细证据见《Capture Tcl Bridge 实测基线》。

## 6. Request 执行模型

Request 是一个 Session 内独立的执行单元。

核心原则：

1. 每条 Request 使用独立 UUID；
2. Request 必须绑定 session_id；
3. 同一 Session 内 FIFO 串行执行；
4. 同一 Session 同时最多一个 RUNNING Request；
5. 不同 Session 可由 Broker 按资源和 Profile 策略并行；
6. 每个 Request 只允许一个最终状态；
7. 同一 request_id 和相同 payload hash 的重发返回缓存结果，不重复执行；
8. 同一 request_id 对应不同 payload hash 时拒绝；
9. 请求执行结果必须与 request_id 一一对应，不能只依赖日志文本。

Capture 的 Tcl Bridge 使用 after 轮询文件队列是已验证的原型。正式版可保留本地文件队列作为 Adapter 传输方式，但必须实现按 Session 隔离目录、原子认领、原子发布结果、请求去重和保留策略。

## 7. 超时、取消和故障处理

必须区分：

- **客户端等待超时**：调用方停止等待，不代表 Runtime 已停止执行；
- **Runtime 执行超时**：只有 Adapter 能确认执行期限已触发时，才可报告 TIMED_OUT；
- **取消请求**：请求已发送，不等同取消成功；
- **取消确认**：Runtime 明确确认请求未开始或已安全取消，才能报告 CANCELLED；
- **状态未知**：通信中断且无法确认执行状态时报告 UNKNOWN，禁止盲目重试可能产生副作用的操作。

当前 Capture Bridge 在 Capture Tcl 事件循环中同步执行脚本。长时间 Tcl / Dbo 操作可能阻塞 GUI 和 after 轮询。因此第一版不承诺强制中断正在运行的 Tcl。取消至少应支持移除尚未开始的排队请求；执行中的请求需要返回取消不受支持或状态未知，不能杀死 Capture 进程作为普通取消路径。

## 8. 保存与关闭职责

- 是否保存、保存哪些设计、何时保存，由专业 Agent Skill 决策。
- Runtime Adapter 只执行 Skill 提交的保存操作，并返回真实执行结果。
- Skill 确认保存成功后，才请求 Session Close。
- Capture 的 Exit 会结束 Tcl Interpreter，因此不能依赖 Exit 之后再写结果文件。
- Capture Adapter 应先持久化 close intent / close accepted，再安排 Tcl 在当前请求收尾后调用 Exit；B 端 Process Supervisor 通过进程退出确认最终关闭。
- SESSION_CLOSED 只有在进程确实退出后才返回。
- 未保存提示、模态对话框或关闭超时时，不得强制终止专业软件。

## 9. 状态恢复与并发

- Broker 应持久化 Session 清单、最后状态和请求结果摘要，以便自身重启后进行核对。
- Broker 重启后不得仅凭 PID 认领旧进程；需校验进程创建时间、映像路径、Session nonce 和 Bridge 心跳。
- 无法确认身份的进程进入人工处理 / LOST 流程，不得直接控制。
- 对同一 exclusive_group 使用跨线程锁；启动和关闭不能并发执行。
- 对同一 Session 使用单一执行队列和唯一消费者。
- 不同 Session 的并行上限由 Profile / 资源策略控制。

## 10. 安全与资源约束

- Session Bridge 的任务目录只允许受控的主机 B AirBridge 服务账号和专业软件运行账号访问。
- 不应将 Runtime Bridge 的本地任务目录暴露给普通网络共享。
- 请求 ID、Session ID 必须校验格式；请求和结果大小必须受限。
- 对大脚本、大结果使用现有文件传输 / RFS 能力和内容摘要，不突破单个 ABFP 帧的载荷限制。
- ABFP 当前 CRC 用于传输完整性检查，不等同身份认证。正式跨主机执行代码仍必须建立可信对端策略；后续协议可增加消息认证与版本协商。
- 日志不得泄露不必要的工程内容、凭据或环境秘密。

## 11. 与当前代码的关系

P1.4 后，B 端 RequestBroker 独立负责 Request 幂等、结果持久化和缓存重放。P1.5 将实际 Engine 调用接入 RuntimeAdapterRegistry；P1.6 再为 Capture Tcl 引入专用 Adapter：

- Tcl EDA_TASK_SUBMIT → RequestBroker → RuntimeAdapterRegistry（capture-tcl-session）→ CaptureTclAdapter → CaptureTclBridgeSession → capAutoLoad Bridge；
- Allegro SKILL EDA_TASK_SUBMIT → RequestBroker → RuntimeAdapterRegistry（legacy-cadence-oneshot）→ LegacyCadenceOneShotAdapter → EDAExecutor；
- Shell 请求 → RequestBroker → RuntimeAdapterRegistry（legacy-shell-process）→ LegacyShellProcessAdapter → ShellEngine；
- RequestBroker 只负责请求去重、结果持久化和重放，不解析 Tcl / SKILL / Shell；
- Capture Bridge 由 capAutoLoad 自动 source，在 Capture 自己的 Tcl 事件循环内以 after 轮询 Session 独立文件队列；单个 Session 同时只执行一条请求；
- READY 校验 session_id、随机 startup_nonce、Capture PID 和 Tcl 版本；结果携带 request_id、payload hash、return code、输出、错误信息、PID 和时间；
- Capture 请求超时 / 进程退出返回 UNKNOWN，Broker 缓存 UNKNOWN，不自动重放可能已产生副作用的脚本；
- P1.7 新增通用 ProcessSupervisor：枚举所有匹配进程（含手动启动），检查窗口和阻塞提示，验证 PID 对应的映像身份后仅发送 WM_CLOSE，并等待进程句柄实际退出；任何进程残留、扫描期间出现新实例、保存/确认提示或无法识别的窗口都会阻断启动；
- Worker 的 Capture Tcl Session 启动路径已接入 Supervisor；健康的 Worker-owned Capture Session 仍直接复用，不会先关闭再启动；
- Allegro ProcessSpec 已定义，但旧 Allegro SKILL 执行路径尚未切换到 Supervisor 管理，留给 P1.9；
- 本机 Registry 注册了 Capture Profile，但握手仍保持 profiles=[]；不能据此宣称主机 A 已确认主机 B 的 Capture Profile 可用。

P1.6 软件 CI 已验证文件队列契约、READY 校验、结果解析、超时 UNKNOWN、Worker Profile 路由与重复请求去重；真实 Capture 17.2 / Tcl 8.6.0 的新版 Bridge 加载与实际 Dbo 命令仍需实机复验。P1.7 软件 CI 已验证 ProcessSupervisor 状态机、Windows 进程发现 / 映像路径 / 窗口枚举、WM_CLOSE 策略模拟、超时阻断和新进程竞态；但没有向真实 Capture.exe 发送 WM_CLOSE，也未测试真实保存提示。

## 12. 实施阶段与验收

| 阶段 | 工作 | 核心验收 |
|---|---|---|
| P1.1 | 冻结架构与协议文档 | 职责、状态、消息和错误语义明确 |
| P1.2 | ABFP 能力协商与 Session 消息 | 新旧 A/B 能识别协议能力 |
| P1.3 | A 端 Request ID 关联与结构化 HTTP 结果 | HTTP 调用方可读取完整结果 |
| P1.4 | B 端 Session Broker / 请求去重 | 同一请求不会因重传重复执行 |
| P1.5 | Runtime Adapter / Profile 抽象 | Broker 不包含 Capture/Allegro 专业逻辑 |
| P1.6 | Capture Tcl Adapter 集成 | capAutoLoad READY、同 PID 连续请求、错误回传 |
| P1.7 | 通用 Process Supervisor | 先关闭全部冲突实例，确认退出后再启动 |
| P1.8 | Capture Skill 端到端验收 | 打开工程、页面操作、保存、Exit、进程退出 |
| P1.9 | Allegro Adapter 验证 | 不影响既有 SKILL 工作流 |

验收必须包含：同一 Session 多次请求、请求重复投递、A/B 断连恢复、错误回传、长任务等待超时、关闭过程中的保存提示、Broker 重启以及 Capture 非正常退出。

**当前实现边界（2026-11-12）：** P1.2–P1.4 协议协商、Request ID 关联与 B 端 RequestBroker 已通过软件 CI；P1.5 Runtime Adapter / Profile 的 Run #62 通过 66 项；P1.6 Capture Tcl Adapter / Bridge 的 Run #76 通过 80 项；P1.7 Process Supervisor 的 Run #85（提交 42ba2e3）通过 89 项测试。P1.6 已实现本机 Capture Profile、capAutoLoad 常驻 Bridge、同一 Interpreter 文件队列、READY nonce/PID 校验、结构化结果和 UNKNOWN 超时处理；新版 Bridge 的 Capture 17.2 实机复验仍待完成。P1.7 已实现通用进程发现、映像身份校验、WM_CLOSE、退出等待与阻断策略，并接入 Capture 启动路径；实际 Capture 进程关闭 / 保存提示尚未实机验收。当前对端 CAPABILITIES 仍返回 profiles=[]；Session Create / Close 专用协议、每 Session FIFO Broker、Allegro ProcessSupervisor 集成、Capture Skill E2E 和 Allegro 专用 Adapter 尚未实现，分别留给 P1.8 / P1.9 及后续协议阶段。

---

## 架构决策摘要

1. AirBridge 是通用通信、Session Broker 和进程管理基础设施。
2. Agent Skill 拥有专业工作流和保存决策。
3. Runtime Adapter 是专业软件与通用 Session 的边界。
4. 一个 Session 可连续执行多条 Request；同一 Session 串行。
5. Session ID、Request ID 与 ABFP seq_id 相互独立。
6. 新实例启动前，必须先优雅关闭所有匹配的旧进程并确认退出。
7. Capture Bridge 通过 capAutoLoad 自动初始化；文件队列是当前已验证的 Adapter 原型。
8. 本文为设计基线，代码实现和版本兼容仍需逐阶段验收。
