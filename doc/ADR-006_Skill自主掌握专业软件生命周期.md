# ADR-006：专业软件生命周期由 Agent Skill 自主掌握

- 文档编号：AB-ADR-006
- 版本：1.0
- 日期：2026-11-18
- 状态：**已采纳；作为 M3.0 架构边界决议**
- 适用范围：AirBridge.exe 双端部署、AirBridge Core、Capture Skill、Allegro Skill 及其他专业软件 Skill
- 基线：`main` / `163798fced2b861d9d213f7a35af929a24cc9730`

> 本 ADR 记录用户明确确定的架构决策。它修订并优先于旧版《AirBridge 职责边界与 Skill 集成规范 v1.0》中将专业软件启动、Bridge 初始化和关闭交给 Runtime Adapter 的相关表述，也修订《AirBridge 代码边界审计与渐进迁移计划 v1.0》中以 Adapter 接管 Capture 生命周期为目标的相关计划。旧文档中的历史实施记录和已完成测试证据继续保留，但不再作为未来职责划分的依据。

---

## 1. 决策摘要

**AirBridge 是通用、透明的远程通信通道。专业软件的完整生命周期和专业业务工作流由相应 Agent Skill 自主实现。**

主机 A 和主机 B 运行的是同一个 `AirBridge.exe`。两端不拆分为不同产品，不新增 Capture Agent、Allegro Agent 或第二套专用远端程序。程序根据其所在主机和本地运行状态承担相应的通信角色：

- 主机 A：向本机 Agent / Skill 提供 HTTP 控制入口，并通过串行链路向主机 B 发送和接收通用请求。
- 主机 B：接收串行链路请求，提供通用 Shell、RFS、文件传输及协议响应能力。

AirBridge Core 不负责 Capture、Allegro 或其他专业软件的启动、复用、工程打开、Bridge 安装、业务执行、保存或退出决策。

对应 Skill 对上述流程拥有完整控制权，包括必要的专业软件发现、版本选择、进程启动、软件内 Bridge 部署、会话初始化、业务脚本提交、结果验证、保存策略和正常退出。

## 2. 双端同一程序原则

### 2.1 同一个 AirBridge.exe

Host A 与 Host B 使用同一个 AirBridge 程序和同一套通信协议实现。架构上可以区分控制侧与远端执行侧的职责，但不得把这种逻辑角色差异误解为两种独立产品或两个不同 Agent。

- 不创建独立的 `CaptureAgent.exe`。
- 不要求 Host B 安装第二套专用 EDA 服务。
- 不为了 Skill 的专业工作流而复制、分叉或另造一套 AirBridge 通信程序。
- 不让 Capture / Allegro 专业业务知识进入 AirBridge.exe 的通用协议与请求处理路径。

两端是否连接、串口是否打开、HTTP 服务是否可用以及对端能力是否协商成功，仍由 AirBridge 本身管理。

### 2.2 Skill 的执行位置

Skill 的决策和流程编排位于 Host A 的 Agent 环境。Skill 通过 Host A 本机的 AirBridge HTTP API 操作 Host B，不直接访问沙箱或 Host B 的本地文件系统，也不直接操作串口。

Host B 上的专业软件内脚本或 Bridge 是 Skill 为完成专业操作而部署的工作载荷，不是另一个 AirBridge 程序。

## 3. AirBridge Core 的职责边界

AirBridge Core 只保留与具体专业软件无关的通信和远程执行基础设施：

- ABFP 帧、串口读写、链路状态、协议版本与通用能力协商；
- 主机 A 本机 HTTP API；
- 通用 Request ID 关联、请求去重、结果回传及 UNKNOWN 状态处理；
- Agent 身份认证、权限检查、请求审计、限流和安全策略；
- 通用 Shell / PowerShell / CMD 命令提交和执行结果返回；
- 通用 RFS 文件与目录读写、目录列表、文件状态、受控删除/移动等能力；
- 通用文件传输、分片、校验、进度和中止；
- 通用日志、错误和通信状态报告。

Core 不应因为 Payload 是 Tcl、SKILL、Python、PowerShell 或其他文本而推断其领域含义。对于新接口，Payload 应作为不透明内容传递；需要文件类型时由 Skill 明确提供通用的内容类型元数据，不以分析脚本文本来决定业务路由。

AirBridge 可以继续保留通用 Shell 执行器。它执行 Skill 明确提交的命令，不替 Skill 决定启动哪个专业软件、打开哪个工程、何时保存或何时退出。

## 4. 专业软件生命周期由 Skill 负责

Capture Skill、Allegro Skill 或其他专业软件 Skill 应自主完成与其目标软件有关的整个工作流。

以 Capture Skill 为例，职责包括：

1. 确定目标 Capture 版本和所需运行环境；
2. 检查 Capture 是否已运行，并识别当前实例及其状态；
3. 决定复用现有实例还是启动新的实例；遇到用户实例、未保存工程、模态对话框或不确定状态时采取安全策略；
4. 通过 AirBridge Shell 在 Host B 启动目标 Capture 程序，并检查启动结果；
5. 通过 AirBridge RFS / Shell 部署或更新由 Capture Skill 自己维护的 Tcl Bridge、capAutoLoad Hook 和辅助脚本；
6. 等待 Skill 自己定义的 READY 条件，并核验 Session、PID、运行时版本和 Bridge 身份；
7. 通过 Skill 自己维护的 Bridge 协议提交 Tcl 请求、获取输出和结果；
8. 自主执行 `Open`、Dbo 查询、设计修改、PDF 导出等专业操作；
9. 自己验证专业操作结果和输出文件；
10. 根据用户指令和工作流要求决定是否保存、保存哪个工程；
11. 明确执行 Capture 自身的正常退出流程，例如由 Skill 提交经验证的 Tcl `Exit`，然后通过 Shell 查询进程是否确实退出；
12. 将成功、失败、部分完成或 UNKNOWN 状态如实返回。

Allegro Skill 按同一原则自行负责 Allegro 安装选择、程序启动、SKILL / Bridge 部署、BRD 打开、专业操作、保存和退出。

上述清单中的专业逻辑属于 Skill。Skill 可以复用通用 HTTP、Shell、RFS 和文件传输接口，但不能要求 AirBridge Core 内置对应软件的专属分支。

## 5. Skill 与 AirBridge 的交互方式

### 5.1 通用 Shell

Skill 通过 Host A 的通用 Shell API 在 Host B 执行明确的命令，例如查询进程、启动程序、检查 PID、读取环境信息或检查结果。

接口：

- `POST /api/v1/shell/exec`

Skill 必须检查远端执行结果，而不能把 HTTP 已接收或已提交误判为命令成功。

当前同步 Shell 返回结果中，`success` 表示 Shell 请求执行流程是否成功；Skill 还必须检查实际命令的 `exit_code`。命令退出码非零仍可能对应 `success: true` 的 Shell 结果。

对于启动 GUI 软件的命令，Skill 应让 Shell 命令本身尽快返回启动 PID / 启动状态，再通过后续只读查询确认目标进程存在、路径正确且处于预期状态。不得让一个同步 Shell 请求无限期等待 GUI 进程退出。

### 5.2 通用 RFS

Skill 通过 RFS 管理 Host B 上的文件，例如：

- 上传 Skill 自己维护的 Tcl / SKILL 辅助文件；
- 创建 Skill 自己的工作目录和任务文件；
- 检查 Bridge READY、Request、Result 和业务结果文件；
- 读取专业软件日志；
- 获取或传回专业工作流产生的文件。

接口位于 `/api/v1/fs/*`。RFS 只提供文件操作，不负责解释专业文件中的业务内容。

### 5.3 Bridge 的所有权

如果一个专业 Skill 需要在目标软件进程内部运行持久 Tcl / SKILL Interpreter Bridge：

- Bridge 源码和版本由对应 Skill 维护；
- Bridge 的安装、Hook 创建和清理由 Skill 编排；
- Bridge 的 Session 标识、启动随机数、请求文件、结果文件、校验和、READY 规则由 Skill 定义；
- Bridge 内只保留完成脚本投递、执行、输出收集及结果回传所必需的机制；
- Dbo / axl 专业 API Helper、对象模型和业务操作由 Skill 自己 source / load；
- AirBridge 仅通过通用 Shell / RFS / 文件传输能力传递这些内容。

Bridge 运行在 Capture / Allegro 进程内，不代表它属于 AirBridge Core。现有 `capture_tcl_bridge.py` 中经验证的文件队列、Nonce、READY、Request ID、Payload Hash、结果回收和 UNKNOWN 处理可以作为迁移技术参考，但不得因其已在 AirBridge 仓库中而默认继续由 AirBridge 管理其专业软件生命周期。

### 5.4 认证和审计

Skill 调用受保护的 Host A API 时，必须使用有效的 AirBridge Agent Bearer Token，并申请完成对应操作所需的最小权限。Skill 不得通过未认证的本地调用绕过 Agent Security、Scope、审计或限流。

每个 Skill 应使用自己的稳定请求 ID 策略，并正确处理：

- `not_submitted`；
- `pending` / `accepted`；
- `completed`；
- `failed`；
- `unknown`；
- 重复请求和请求结果过期。

HTTP 的 `202` 或 `submitted` 只代表请求已被接收 / 等待，不代表专业任务成功。

## 6. 禁止重新引入的 EDA 专属 Core 语义

新架构中，AirBridge Core 不应提供或依赖以下专业软件语义：

- Capture / Allegro 的内置安装扫描、版本选择和软件路径猜测；
- `Capture.exe`、`allegro.exe` 等专用默认进程规则；
- `target_file` 驱动 Capture 原生 DSN 启动或 Allegro BRD 打开的语义；
- `keep_open` 决定专业软件任务完成后是否关闭 GUI 的语义；
- 自动生成 Capture 的 Tcl `Open` 或 Allegro 的 BRD 打开脚本；
- 自动注入 `ORCAD_TCL_POLYFILL` 或 `ALLEGRO_SKILL_POLYFILL`；
- Capture 的 `Exit`、Allegro 专属退出命令、保存/丢弃策略；
- 通过源码关键字或扩展名猜测专业脚本类别并自动选用软件；
- EDA 专用关闭、强杀和进程保活控制路径。

专业脚本在传输层面是不透明 Payload。Skill 明确选择自己的工作流和调用方式。

## 7. 进程管理与退出安全

### 7.1 AirBridge 不替 Skill 管理专业进程

AirBridge Shell 可以启动一个 PowerShell 进程并返回 PID；它的任务管理能力只负责通用 Shell 子进程，不等于拥有由该命令启动的 Capture / Allegro 生命周期。

AirBridge 不得因为收到普通请求就自动关闭已存在的 Capture / Allegro 实例，也不得根据可执行文件名擅自决定复用、独占、关闭或重启专业软件。

当前 `ProcessSupervisor.DEFAULT_SPECS` 中的 Capture / Allegro 默认规则，以及 `ensure_exclusive("capture")` 可能对所有匹配实例发送 WM_CLOSE 的行为，不得作为新 Skill 工作流的默认执行路径。迁移过程中必须保留旧兼容行为的回归保护，但新 Skill 不应调用这类 EDA 生命周期路径。

如果仓库保留通用操作系统级进程工具，它必须保持真正通用，不能在 Core 中嵌入 Cadence 名称、专业软件默认策略或自动生命周期决策。若该工具只被旧 EDA 路径使用，则应随旧路径退役或迁出。

### 7.2 正常关闭和用户数据安全

- 何时关闭由 Skill 决定。
- 如何保存或丢弃由 Skill 根据用户明确指令和专业工作流决定。
- Skill 应优先使用目标软件自身已经验证的正常关闭入口。
- 发送退出请求后，Skill 必须再次检查进程状态，不得仅凭脚本返回就声称软件已退出。
- 出现保存、许可、升级、缺失库或其他无法自动安全判定的弹窗时，停止自动处理并报告用户。
- UNKNOWN 不等于取消，也不等于失败后可以安全重试。
- 除非用户明确授权并且专业工作流定义了安全恢复步骤，否则不得强制终止目标专业软件。

## 8. 现有接口和代码的过渡策略

本 ADR 不要求在一次提交中删除所有 EDA 旧代码，也不允许破坏 M1 / M2.3 已经验证的兼容路径。

下列内容暂时作为 Legacy Compatibility 保留：

- `/api/v1/eda/versions`；
- `/api/v1/eda/execute` 及其旧别名；
- `EDA_TASK_SUBMIT`、`EDA_TASK_FIN`、`EDA_APP_CLOSE`、`EDA_FORCE_KILL`、`EDA_LOG_STREAM` 等旧帧；
- `EDAExecutor`、旧版 Polyfill、`select_eda_profile()` 和脚本类型猜测；
- `CaptureTclAdapter`、`CaptureTclRuntimeManager`、旧 Capture Bridge 管理入口；
- 现有 PDF Export Skill 中依赖旧 EDA Execute 生命周期的实现。

过渡要求：

1. 将旧路径明确标记为 Legacy / Deprecated，不再作为新 Skill 的推荐集成方式。
2. 不在旧兼容入口上增加新的 Capture / Allegro 专业业务能力。
3. 新 Skill 工作流只使用通用 Shell / RFS / 文件传输能力。
4. 在移除旧路由或帧之前，搜索所有仓库内外已知调用者，完成兼容性迁移和回归。
5. 删除旧 EDA 功能前，分别验证同一个 AirBridge.exe 在 A / B 两端的通用通信、Shell、RFS、文件传输、认证、请求关联和去重没有退化。

## 9. 当前实现与决策的差距

以基线提交 `163798f` 为准，至少存在以下偏差：

| 现有实现 | 偏差 | 目标处理 |
|---|---|---|
| `CaptureTclRuntimeManager` | 负责 Capture 发现、启动、capAutoLoad Hook、READY、进程句柄和关闭 | 迁移到 Capture Skill 管理的工作流和 Bridge 资产 |
| `CaptureTclAdapter` | 理解 Capture Session、DSN / OPJ，并自动生成 `Open` | 旧兼容层保留；新 Skill 不调用 |
| `eda_engine.py` | Cadence 安装发现、Capture / Allegro 启动、Polyfill、工程打开和 GUI 生命周期集中耦合 | 按 Skill 所有权迁出；Legacy 路径逐步退役 |
| `process_supervisor.py` | 默认内置 Capture / Allegro 进程规格；独占检查会关闭匹配实例 | 新 Skill 不依赖该 EDA 生命周期逻辑；通用操作系统能力与旧路径解耦 |
| `runtime_adapter.py` | 通用 Registry 与 Cadence / Allegro / Shell 适配器共存，并做脚本类型猜测 | Core 仅保留真正通用的协议/Registry；新 Skill 明确选择通用传输方式 |
| `skill/orcad-pdf-export` | 调用 EDA Execute，并依赖 AirBridge 启动、打开和关闭 Capture | 改为 Skill 自主管理 Capture 全生命周期 |
| `submit_export.py` | 未按 M2.3 API 认证和请求响应语义完整更新 | 补齐 Bearer Token、同步等待 / Request ID 查询及错误状态处理 |

特别注意：当前 Shell 同步执行路径可以返回远端命令结果；异步 Shell 已有任务启动确认和实时输出，但最终结果与原请求的关联查询尚未形成完整 HTTP 闭环。此项属于 AirBridge 通用 Shell API 的独立完善工作，不应通过增加 Capture 专用接口解决，也不阻止 Skill 使用已具备的同步 Shell / RFS 能力。

## 10. M3 迁移阶段与验收

### M3.0：架构决策和文档修订

- 将本 ADR 作为最高优先级的专业软件生命周期边界决策；
- 修订旧职责规范和迁移计划中的冲突条款；
- 明确两端使用同一个 `AirBridge.exe`；
- 记录现有 EDA 专用代码仅作为过渡兼容层；
- 本阶段不改变任何运行代码、ABFP 帧和 HTTP 行为。

### M3.1：通用控制路径和 Skill API 契约核实

- 验证 A 端通过 Bearer Token 调用 Shell / RFS 的完整请求与响应；
- 以同步 Shell 为 Skill 多步骤工作流的基础；
- 正确检查远端 PowerShell 的 `exit_code`；
- 解决通用异步 Shell 任务最终结果的可查询性；
- 保留 RequestTracker、RequestBroker、去重、审计与权限能力；
- 不新增 Capture 专用 HTTP API。

### M3.2：Capture Skill 获得完整生命周期控制

详细实施边界、建议目录、Session 状态机、Bridge 文件协议、进程安全策略和分阶段验收见 [M3.2：Capture Skill 自主生命周期实施设计 v1.0](M3.2_Capture_Skill_自主生命周期实施设计_v1.0.md)。该文档属于实施设计，不代表 Capture Skill 代码或 Host B 实机验收已完成。


- 把 Capture 安装发现、版本选择和运行环境处理迁入 Capture Skill；
- 把 Tcl Bridge、capAutoLoad Hook、Session / Request / Result 文件协议作为 Capture Skill 自有资产；
- 通过 AirBridge Shell / RFS 部署、启动、查询和回收；
- Skill 自己打开设计、执行 Tcl、决定保存并正常退出；
- 不再依赖 `target_file`、`keep_open` 或 AirBridge 自动 WM_CLOSE。

### M3.3：PDF Export Skill 迁移和端到端验证

- 明确 PDF Export Skill 的调用权限和 Token 获取来源；
- 移除其对 AirBridge 自动启动 Capture、原生 DSN 启动参数和自动关闭的依赖；
- 将 `is_dbo_null` 等 Helper 显式纳入 Skill 自己的 Tcl 资产；
- 由 Skill 自己打开 DSN、查询 Active Design、执行 PDF 导出、验证结果、按策略保存和退出；
- 在目标 Host B 的真实 Capture 17.2 / Tcl 8.6 环境完成 E2E 验收；
- 同时验证 PDF 生成成功、错误恢复、保存提示和关闭失败时的保守处理。

### M3.4：Allegro Skill 边界迁移

- 将 Allegro 安装发现、启动、BRD 打开、SKILL Bridge、保存和退出工作流迁入 Allegro Skill；
- 移除新工作流对 AirBridge SKILL Polyfill 注入及 EDA Engine 进程管理的依赖；
- 完成真实 Allegro 目标版本回归。

### M3.5：AirBridge 旧 EDA 专用路径退役

只有在所有已知 Skill / Agent / GUI 调用者完成迁移并通过回归后，才逐步移除 EDA 专用 Adapter、Cadence 发现、Polyfill 注入、脚本猜测分流、EDA 专用生命周期帧和进程配置。

### M3 验收总门槛

- Host A 与 Host B 仍使用同一个 AirBridge.exe；不新增专用远端 Agent；
- AirBridge Core 不再执行专业软件生命周期决策；
- Capture / Allegro Skill 能独立完成各自的软件启动、工程打开、业务操作、保存和退出；
- 新 Skill 仅依赖通用 Shell / RFS / 文件传输 / Request API；
- 旧 EDA 兼容路径在迁移期间不回归；
- 所有通用通信能力、身份认证、请求关联、去重与 UNKNOWN 语义通过测试；
- Capture / Allegro 真实软件验收与通用 Core CI 分开报告，不以软件模拟测试代替现场验证；
- 未经用户授权，不执行真实 EDA 写入、保存、替换、覆盖或退出操作。

## 11. 被修订的旧决策和计划

本 ADR 对旧文档作如下明确修订：

| 旧文档条款 | 原有表述 | 本 ADR 的修订 |
|---|---|---|
| 职责规范 v1.0 §1 / §3.2 | Adapter 负责软件发现、启动、Bridge 初始化和关闭 | 专业软件生命周期由对应 Skill 负责；AirBridge 不做专业软件 Adapter 生命周期管理 |
| 职责规范 v1.0 §2 | Capture Runtime Adapter 位于主机 B 与 Core 之间 | 新 Skill 直接通过 Host A 的通用 AirBridge API 控制 Host B；旧 Adapter 仅为兼容实现 |
| 职责规范 v1.0 §5.1 / §5.3 | Core / Adapter 参与 Session 创建与关闭 | Skill 自己建立并管理专业软件内 Bridge Session；AirBridge 只提供通用传输 |
| 职责规范 v1.0 §6 / §7 | Profile 声明专业软件进程和 Bridge 配置 | 新 Skill 不依赖 AirBridge 的 Cadence Profile；不在 Core 中加入应用专用配置 |
| 职责规范 v1.0 §9.2 | Adapter 实机测试包含专业软件启动和 Bridge 生命周期 | 新 Skill 负责完整专业软件 E2E；Core 测试只验证通用通信能力 |
| 职责规范 v1.0 ADR-002 | Adapter 负责 Capture 运行时接入和不透明 Tcl 执行 | 该 ADR 被 ADR-006 取代；Bridge 和生命周期由 Capture Skill 管理 |
| 代码迁移计划 v1.0 M1 | Capture Adapter 接管 Session 生命周期 | 不再作为目标架构；当前 M1 只视为历史兼容实现 |
| 代码迁移计划 v1.0 M2 / M5 | 将 Cadence 发现和完整 Session 协议纳入 Adapter 体系 | 重新评估；只有真正通用的协议/传输能力留在 Core，专业 Session 生命周期由 Skill 管理 |

本 ADR 不否定 M1 已完成的工程事实，不撤销已有 CI 或实机证据；它只改变未来应当保留和迁出的职责归属。

## 12. 实施约束

- M3.0 仅提交架构文档和 ADR，不借文档提交修改 Python / Tcl 运行代码。
- 后续每一项代码迁移单独分支、单独提交并执行对应测试。
- 继续以 `163798fced2b861d9d213f7a35af929a24cc9730` 作为本 ADR 的审计基线；如 `main` 后续变化，开始代码迁移前重新核对主线。
- 不删除、不清理或覆盖用户已有的本地构建目录和测试产物。
- 不因本 ADR 而启动 Host A / Host B 的 AirBridge，不建立串行连接，也不启动 Capture / Allegro。
