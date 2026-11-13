# AirBridge 职责边界与 Skill 集成规范

- 文档编号：AB-ARCH-BOUNDARY-001
- 版本：1.0
- 日期：2026-11-13
- 状态：**架构职责边界冻结基线；文档决策已确定，现有代码的模块迁移与接口收敛尚未完成**
- 适用范围：AirBridge Core、Runtime Adapter、专业软件内 Bridge、Agent Skill，以及主机 A / 主机 B 的协作方式

> 本文是 AirBridge 架构边界的规范性文件。若旧文档或旧代码与本文边界不一致，以本文的目标职责划分为准；现有兼容实现应通过渐进迁移收敛，不要求本次立即破坏现有调用路径。

---

## 1. 核心决策

**AirBridge 是通用远程 Runtime 通信与生命周期基础设施，不是 EDA 专业 API 库。**

Capture Dbo Tcl API、Allegro SKILL API、器件/引脚/网络对象模型、专业操作流程、版本差异和工程结果判断，全部属于对应的 Agent Skill。

Runtime Adapter 位于两者之间，只解决“如何在远端软件中建立可控执行环境，并把不透明的请求可靠送进去、把结果取出来”的问题。

因此采用三层职责模型：

1. **AirBridge Core：** 通信、通用协议、Session / Request 管理、进程监督和结果回传。
2. **Runtime Adapter：** 专业软件的技术接入插件，负责发现、启动、Bridge 初始化、请求投递和进程关闭等软件运行时问题。
3. **Agent Skill：** 专业 API、对象模型、版本兼容、领域工作流、操作策略和结果解释。

专业软件内的轻量 Bridge 是 Runtime Adapter 的执行端，不构成第四个领域知识层。它只负责接收、执行和回传，不负责理解专业对象的业务含义。

## 2. 总体关系

```text
主机 A                                                        主机 B
┌───────────────────────────┐                    ┌────────────────────────────┐
│ Agent / Capture Skill     │                    │ Capture Runtime Adapter    │
│                           │  通用 Session API  │                            │
│ - Dbo Tcl API 知识        │ ─────────────────▶ │ - Capture.exe 发现/启动     │
│ - 版本 API 适配            │                    │ - capAutoLoad / Bridge      │
│ - Design / Schematic      │                    │ - READY / PID / 版本核验    │
│ - Page / Instance / Net   │                    │ - Tcl 请求投递与结果回传    │
│ - 设计工作流与保存决策     │                    └─────────────┬──────────────┘
└───────────────────────────┘                                  │
              │                                                 │
              ▼                                                 ▼
┌───────────────────────────┐                    ┌────────────────────────────┐
│ AirBridge Client          │ ◀════ ABFP ══════▶ │ AirBridge Core / Broker    │
│ - HTTP API                │                    │ - Session / Request        │
│ - request_id 关联         │                    │ - FIFO / 幂等 / 状态缓存    │
│ - 结果/错误传递            │                    │ - 通用 Process Supervisor  │
└───────────────────────────┘                    │ - 超时 / UNKNOWN / 安全策略 │
                                                 └────────────────────────────┘
```

以上是逻辑职责关系。Runtime Adapter 可以和 AirBridge Core 位于同一仓库或同一安装包，但必须保持独立模块边界、独立测试和可替换接口；Capture Skill 的领域知识应独立于 AirBridge Core 发布和升级。

## 3. 各层职责

### 3.1 AirBridge Core

Core 只实现与具体专业软件无关的通用能力：

- A/B 双向通信、ABFP 帧处理、协议版本与能力协商；
- 通用 HTTP 北向接口和通用 Runtime Session / Request / Result Envelope；
- Session 标识、状态、请求关联、排队、幂等、去重和结果缓存；
- 请求超时、状态未知、断线恢复和取消语义；
- 通用文件 / Artifact 传输、大小限制、摘要校验和受控路径；
- 通用 Process Supervisor 状态机、进程身份核验、优雅关闭请求及退出确认；
- Adapter / Profile 注册、能力声明、调度和标准化执行结果；
- 日志、审计、安全策略和资源限制。

Core 不应该因为请求来自 Capture Skill，就改变协议结构或执行 Capture 专属分支。

### 3.2 Runtime Adapter

Adapter 是主机 B 上的技术接入插件。它可以知道目标软件是什么、如何启动以及如何与该软件的运行时通信，但不能承载专业 API 业务语义。

允许承担：

- 查找软件安装位置、可执行文件、版本和必要的运行环境；
- 向 Core 报告实际运行时版本、Adapter 版本、支持的内容类型和通用能力；
- 提供进程匹配规则、启动参数、环境变量和独占组等 Runtime Profile 数据；
- 安装或启用软件内 Bridge，处理专用自动加载入口；
- 等待 Bridge READY，并核验 Session ID、startup_nonce、PID 和运行时版本；
- 将通用 Request 的不透明 Payload 投递到软件内 Bridge；
- 回收软件内 Bridge 的执行结果，转换为通用 Result；
- 接收关闭请求，安排软件退出，并由 Process Supervisor 确认实际进程退出。

Adapter 不承担：

- 调用或封装 DboDesign、DboSchematic、DboPage、Instance、Pin、Net 等领域对象；
- 定义 Dbo API 的方法签名、参数、返回类型或版本兼容规则；
- 决定如何修改原理图、如何连接网络、如何检查电气规则；
- 制定专业工作流、解释设计结果或决定是否保存；
- 将某个业务操作包装成 Core 固有的专用 Operation。

### 3.3 专业软件内 Bridge

Bridge 是 Adapter 在目标软件进程内部使用的轻量执行端。

以 Capture 为例，Bridge 可以：

- 通过 capAutoLoad 自动 source；
- 在 Capture 的 Tcl 事件循环中调度请求；
- 校验 Session / Request 元数据；
- 接收 UTF-8 Tcl 脚本文本并执行；
- 收集 Tcl 返回值、stdout、stderr、错误栈和时间；
- 把结果写回受控队列；
- 维持当前 Interpreter 和 Session 的绑定关系。

Bridge 不应该内置 Dbo API 的业务封装、设计对象遍历逻辑、器件/网络操作或设计修改策略。它只需要知道如何执行一段 Tcl，不需要知道这段 Tcl 正在调用哪个 Dbo 类。

### 3.4 Agent Skill

Skill 是专业知识和工作流的唯一归属层。Capture Skill 负责：

- Capture Tcl 命令、Dbo Tcl API 和对象模型；
- 官方资料、内置 Tcl 示例、参数签名、返回类型和资源释放方式；
- 各 Capture/SPB 版本的 API 差异、兼容矩阵和版本专用实现；
- Design、Schematic、Page、Part Instance、Pin、Net、Occurrence、Property 等对象的读取与关系构建；
- 原理图分析、对象创建/删除、属性或连接修改等业务操作；
- 对每个操作定义前置条件、只读/写入等级、预期副作用和验证方法；
- 决定何时保存、保存哪个工程，以及保存成功后何时请求关闭 Session；
- 解释运行结果、识别错误并规划下一步专业操作。

Allegro Skill 同理负责 Allegro SKILL API、数据库对象、BRD 操作工作流及其专业结果判断。

## 4. 版本兼容的边界

必须区分两种不同的“版本适配”。

| 问题 | 归属 | 例子 |
|---|---|---|
| 软件安装与启动层差异 | Runtime Adapter | 安装目录、可执行文件路径、启动参数、capAutoLoad 位置、Bridge 启动方式 |
| 专业 API 语义差异 | Agent Skill | Dbo 方法是否存在、参数签名、返回对象、对象遍历行为、属性读写差异 |

对于 Capture：

1. Capture Runtime Adapter 检测并报告实际 Capture 版本、Tcl 版本、可执行文件路径和 Adapter 版本。
2. Capture Skill 根据这些运行时信息选择对应的 API 兼容包，例如 17.2、17.4、23.1、24.1。
3. API 兼容包由 Capture Skill 自己维护、测试和发布。
4. Adapter 不应维护 Dbo API 版本矩阵，也不应因为 Dbo 方法发生变化而升级 AirBridge Core。
5. 如果某个 Capture 版本改变了自动加载目录、启动方式或进程行为，这属于 Adapter 的运行时接入兼容范围。

**版本探测由 Adapter 提供事实，版本 API 选择由 Skill 作出决策。**

## 5. Skill 与 AirBridge 的交互契约

Skill 通过公开、稳定、与专业 API 无关的 Session / Request 接口调用 AirBridge。Skill 不应 import AirBridge 内部 Python 模块，也不应直接操作 B 端 Session 目录或串口对象。

### 5.1 Session 创建

Skill 提供 Profile 请求和必要的运行约束，例如：

- profile_id；
- 可选 runtime_version 约束；
- 通用启动超时；
- skill_id / skill_version 等审计元数据。

Core 根据 Profile 选择 Adapter；Adapter 启动软件、初始化 Bridge 并返回 READY。创建成功后，Core 向 Skill 返回 session_id 和 Adapter 报告的 runtime metadata。

### 5.2 Request 执行

Core 不解析专业脚本内容。请求只包含通用 Operation 和不透明 Payload，例如：

```json
{
  "operation": "execute",
  "payload": {
    "content_type": "text/x-tcl",
    "encoding": "utf-8",
    "body": "puts [pid]"
  },
  "metadata": {
    "skill_id": "capture-skill",
    "skill_version": "1.0",
    "action_id": "read-design-metadata"
  }
}
```

其中 text/x-tcl 是 Runtime Adapter 声明支持的内容类型；Core 只校验通用 envelope、大小、权限和完整性，不解析 Tcl，更不识别 body 内的 Dbo API。

Adapter 按 Profile 将 Payload 投递给目标 Interpreter，并返回通用结果，包括状态、运行时 PID、耗时、输出、错误和运行时元数据。Skill 负责解释这些输出的专业含义。

### 5.3 保存与关闭

- 是否保存、何时保存、保存哪个设计，由 Skill 决定。
- Skill 通过普通 Execute Request 明确提交保存脚本或调用。
- Adapter 只执行请求并回报结果，不擅自保存。
- Skill 确认保存成功后，才请求通用 Session Close。
- Core / Adapter 不应自动选择“保存、丢弃或取消”对话框；遇到无法安全判断的 UI 状态应阻断关闭并报告。
- CLOSED 必须以目标进程实际退出为依据，而不是仅凭脚本发出 Exit 命令。

## 6. Runtime Profile 与能力声明

Profile 是运行时连接配置，不是专业 API 描述。

Profile 可以包含：

- profile_id、adapter_id、adapter_version；
- 目标 runtime_id 和可选版本约束；
- 进程映像匹配规则、独占组、启动/关闭超时；
- 启动环境、Bridge 安装/初始化入口；
- transport 类型、支持的 content_types；
- 通用能力，例如 persistent-interpreter、request-serialization、graceful-close。

Profile 不应该包含：

- DboDesign.GetRoot 等 API 方法列表；
- Dbo API 参数签名和 Capture 对象类型；
- “读取器件”“遍历网络”等业务 Operation；
- 专业软件对象的业务 schema 或保存策略。

Core 可以把 capture-tcl-session 视为一个不透明的 profile_id，但不得在 Broker 中为这个字符串写入 Dbo 专属执行逻辑。

## 7. 代码与发布边界建议

建议采用“AirBridge Core + Runtime Adapter 插件 + 独立 Agent Skill”的逻辑分包。初期可以继续同仓库开发，但目录、依赖、测试和发布边界必须独立；不要为了物理拆仓而打断已通过的现场运行路径。

建议目标结构：

```text
airbridge/
  core/
    protocol/
    client/
    broker/
    request/
    process_supervisor/
    artifact/
  runtime_adapters/
    capture/
      process_profile/
      autoload_bridge/
      tcl_transport/
    allegro/
      process_profile/
      skill_transport/
  legacy/
    eda_executor/
    shell_compat/

capture-skill/
  api/
    common/
    versions/
      capture_17_2/
      capture_17_4/
      capture_23_1/
      capture_24_1/
  objects/
    design/
    schematic/
    page/
    instance/
    pin/
    net/
    property/
  workflows/
    inspect_design/
    inspect_connectivity/
    modify_design/
    save_design/
  tests/
    unit/
    version_fixtures/
    host_integration/
```

以上是目标分包示意，不代表当前目录已经按此结构实现，也不要求本次立刻创建 Capture Skill 仓库。

## 8. 对当前 AirBridge 代码的边界审计

当前实现已经有一部分符合目标边界，但仍存在历史兼容代码混入通用模块的问题。

| 当前实现 / 文件 | 目标归属 | 处理建议 |
|---|---|---|
| ABFP、Runtime Session Protocol、RequestTracker、RequestBroker | AirBridge Core | 保持通用，不加入专业 API 逻辑 |
| ProcessSupervisor 状态机 | AirBridge Core | 保持操作系统级通用；应用进程规则由 Profile 提供 |
| RuntimeAdapterRegistry / RuntimeProfile | AirBridge Core | 只做注册、能力校验、选择和调度 |
| CaptureTclAdapter、CaptureTclBridgeSession、capAutoLoad Hook 生成 | Capture Runtime Adapter | 保持为薄接入层；后续在包结构上与 Core 隔离 |
| Capture 17.2 / Tcl 8.6 实际运行时探测 | Capture Runtime Adapter | 返回事实元数据，不解释 Dbo 语义 |
| eda_engine.py 中 ORCAD_TCL_POLYFILL 的 DboTclHelper、ab_get_prop、ab_set_prop、ab_open_dsn、ab_save_and_close 等 | Capture Skill | 迁出业务封装；保留必要的旧版兼容入口直到迁移验收完成 |
| eda_engine.py 中 ALLEGRO_SKILL_POLYFILL 的 axl* 操作和设计工作流 | Allegro Skill | 迁出专业 API 和工作流；过渡期不破坏现有 Allegro 启动行为 |
| detect_script_type / select_eda_profile 的源码关键字分流 | 过渡兼容层 | 目标改为明确 profile_id / content_type，不让 Core 猜测脚本领域语义 |
| Dbo API 调用示例、方法签名、对象模型和版本差异文档 | Capture Skill | 从 AirBridge 研究资料迁入 Capture Skill 文档和测试 |
| Capture Skill 的分析/修改/保存工作流 | Capture Skill | 不进入 AirBridge Core 或 Capture Runtime Adapter |

这里列出的迁移是边界收敛任务，不表示本次文档提交已经修改或迁出了任何 Python 代码。迁移前必须补齐旧路径回归测试，并保证 A/B 当前已通过的 Capture Bridge 链路不回退。

## 9. 测试与验收分层

### 9.1 AirBridge Core 测试

Core 测试不依赖 Capture 或 Cadence 安装：

- 使用 Fake Runtime Adapter 验证 Session / Request 协议；
- 检查 FIFO、request_id、幂等、超时、UNKNOWN、重连和结果缓存；
- 验证 ProcessSupervisor 对通用 ProcessSpec 的安全行为；
- 验证未知 Profile / 能力不匹配的失败路径。

### 9.2 Runtime Adapter 实机测试

Adapter 测试只验证软件技术接入：

- 发现正确的可执行文件和运行时版本；
- 进程启动、身份核验和独占策略；
- Bridge 自动初始化、READY nonce / PID / 版本核验；
- 多条不透明脚本请求能送达并回传结果；
- 同一 Interpreter 的串行、错误隔离和重启行为；
- 优雅关闭请求与真实进程退出确认。

Adapter 验收不应以“成功读取 DboDesign”或“成功修改某个网络”作为标准，那是 Capture Skill 的功能验收。

### 9.3 Agent Skill 测试

Capture Skill 负责按 Capture 版本测试：

- Dbo API 调用签名、对象返回类型和资源释放；
- Design / Schematic / Page / Instance / Pin / Net / Property 对象关系；
- 只读操作、写入副作用、保存和重新打开验证；
- 17.2、17.4、23.1、24.1 等目标版本矩阵；
- 业务工作流正确性、失败恢复和设计结果验收。

## 10. 当前已验收内容与未完成事项

2026-11-12 主机 B / Capture SPB 17.2 / Tcl 8.6 的现场结果已经证明：正式生成式 Bridge 自动加载、READY / LOCK、单条和连续请求、Interpreter 全局状态保持、错误隔离、并发请求串行、WM_CLOSE 正常关闭、关闭后新 Session 重启和状态复用均已通过。

这足以证明当前 Capture Runtime Adapter 接入路线可行。它不代表 AirBridge 已完成全部 Session 协议，也不代表 Capture Skill 的 Dbo API 业务能力已完成。

截至本规范日期：

- 真实 Capture Bridge 和 Start Page 正常关闭/重启：实机 PASS。
- 三个 Active Design 查询入口在无工程 Start Page 状态下均正常返回 NULL：属于 Capture API 观察记录，不是 AirBridge Core 的 API 契约。
- Dbo API 的完整调用字典、版本矩阵、设计对象遍历及读写工作流：归入 Capture Skill，当前不在 AirBridge 阶段继续扩展。
- 专用 SESSION_CREATE / SESSION_EXEC / SESSION_CLOSE 协议及完整每 Session FIFO Broker：仍待实现。
- Allegro 专用 Runtime 生命周期与主机 B 实机回归：仍待后续阶段。
- 当前代码中遗留的 Capture / Allegro 专业 Polyfill 和源码关键字分流：需要按第 8 节逐步迁移。

## 11. 迁移顺序

1. **冻结职责文档：** 本规范作为边界基线，后续设计和代码评审据此判断模块归属。
2. **不破坏当前已验收链路：** 暂不删除 ORCAD_TCL_POLYFILL、旧 EDA 路由或现有 Capture Bridge；先为兼容路径增加清晰标记和测试。
3. **收敛 Runtime Adapter 接口：** 明确通用 Profile、content_type、runtime metadata 和 Session 生命周期操作；Core 不做脚本内容分析。
4. **逐步拆出 Capture / Allegro 插件：** 将软件启动、Bridge 初始化和各自进程规格移入 Adapter 包；将 Dbo / axl API 助手与工作流迁出旧 EDA 引擎。
5. **建设 Capture Skill：** 将现有 Dbo API 索引和实测记录作为迁移输入，在 Capture Skill 内建立版本专用 API 适配、对象模型和工作流。
6. **验证并退役旧兼容路径：** 只有新 Adapter / Skill E2E 通过后，才移除旧 Polyfill、源码关键字分流和旧 fallback 中已替代的代码。

每一步均需独立提交、CI 和必要的 A/B 实机回归。不得把文档冻结误解成要求立即重构或重启 Capture。

---

## 12. 架构决策记录

- **ADR-001：** Dbo Tcl API 与 Capture 对象模型由 Capture Skill 维护，禁止进入 AirBridge Core。
- **ADR-002：** Capture Runtime Adapter 只负责软件运行时接入和不透明 Tcl 执行，不负责 Dbo API 调用语义。
- **ADR-003：** Adapter 报告实际软件版本；Skill 依据版本选择专业 API 实现。
- **ADR-004：** 是否保存、何时保存及保存对象由 Skill 决策；Core / Adapter 不自行保存设计。
- **ADR-005：** 逻辑分包先于物理拆仓；以不破坏现有 A/B 运行路径为前提渐进迁移。
