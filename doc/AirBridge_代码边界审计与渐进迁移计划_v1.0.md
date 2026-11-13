# AirBridge 代码边界审计与渐进迁移计划 v1.0

- 文档编号：AB-ARCH-CODE-AUDIT-001
- 版本：1.0
- 日期：2026-11-13
- 审计分支：`feature/runtime-session-p1-2`
- 审计基线：`42ff121fecf3aa25dc792bb9a223088efa04f606`
- 状态：**静态代码审计完成；未修改运行代码；迁移顺序建议已确定，尚未执行代码拆分**

> 本文依据上述分支的实际源码进行职责审计。它记录的是当前代码现状，不把目标架构描述成已经实现。代码路径和行号对应审计基线；后续代码变化后应重新核对。

---

## 1. 审计结论

AirBridge 已经建立 Runtime Adapter、Runtime Profile、RequestBroker 和 ProcessSupervisor 等抽象，核心方向是正确的；但**现有运行代码还没有完全落实 2026-11-13 冻结的 Core / Adapter / Skill 职责边界**。

主要问题不是缺少新的目录，而是实际调用链仍然跨层：

1. Capture 的 Session 启动、安装发现、capAutoLoad 定位和进程创建由 `airbridge_app.py` 的 `SerialWorker._ensure_capture_tcl_bridge()` 承担，并未完全封装在 Capture Runtime Adapter 内。
2. `eda_engine.py` 同时包含 Capture Dbo Tcl Polyfill、Allegro SKILL Polyfill、Cadence 版本/安装探测和两种 Cadence 软件的旧式一次性执行器。
3. `runtime_adapter.py` 名称上是通用 Adapter 注册模块，但同时包含 Cadence / Allegro / Shell 具体适配器，以及基于脚本文本的 EDA 类型识别与 Profile 分流。
4. `airbridge_app.py` 同时承载 Qt GUI、SerialWorker、HTTP API、协议收发、Runtime 注册和 Capture 专用启动协调，模块职责过于集中。
5. Capture PDF Export Skill 实际依赖旧 Tcl Polyfill 注入的 `is_dbo_null`；因此 Polyfill 不能简单从 `eda_engine.py` 删除，必须先解除 Skill 对隐式全局函数的依赖。

**建议：先把 Capture Runtime Adapter 的生命周期接入从 SerialWorker 中抽出，再逐步拆分旧 EDA 引擎。暂不进行全仓目录重排、物理拆仓或删除旧兼容代码。**

## 2. 当前代码模块审计

| 当前文件 / 模块 | 观察到的实际职责 | 目标归属 | 审计结论 |
|---|---|---|---|
| `protocol.py`、`runtime_session_protocol.py` | ABFP 帧、Envelope 和能力协商 | AirBridge Core | 基本符合 |
| `request_tracker.py`、`request_broker.py` | HTTP 请求关联、request_id 幂等、结果持久化/缓存 | AirBridge Core | 基本符合；后续扩展 Session 维度 |
| `process_supervisor.py` | Windows 进程发现、映像路径、窗口检查、WM_CLOSE、退出等待 | AirBridge Core | 核心状态机符合；默认应用规格应由 Profile/Adapter 注入 |
| `runtime_adapter.py` | 通用 Adapter/Profile 契约与 Registry，同时包含 Legacy Cadence、Allegro、Shell 具体 Adapter 和 EDA 分流函数 | Core + 具体 Adapter | 混合，需渐进拆分 |
| `capture_tcl_bridge.py` | Capture Tcl Bridge 文件队列、READY、结果解析、CaptureTclAdapter | Capture Runtime Adapter | 大体归属正确；Adapter 当前只有 execute，Session 启动由 Worker 外部 Provider 提供 |
| `eda_engine.py` | Dbo/Allegro Polyfill、Cadence 安装发现、版本选择、Tcl/SKILL 一次性执行和旧进程管理 | Capture Skill + Allegro Skill + Cadence Runtime Adapter + Legacy Adapter | 明显混合，是最大耦合点 |
| `airbridge_app.py` | Qt GUI、SerialWorker、HTTP Handler、协议收发、Broker/Registry 初始化、Capture 启动生命周期 | Core composition/UI + Runtime Adapter 调用 | 过度集中；Capture 专用流程应先迁出 |
| `scripts/capture_dbo_session_iterators_probe.tcl` | DboSession / Design 迭代器 API 专业探测 | Capture Skill | 临时研究资料，不属于 AirBridge 运行代码 |

以上“基本符合”只表示当前模块职责与目标方向一致，不代表所有目标 Session 功能都已实现。

## 3. 关键边界问题与证据

### 3.1 Capture Session 生命周期仍在 SerialWorker 内

`airbridge_app.py` 中：

- `SerialWorker.__init__` 创建 `CaptureTclAdapter(self._ensure_capture_tcl_bridge)`，并把 Worker 方法作为 Session Provider 注入 Adapter；
- `_ensure_capture_tcl_bridge()` 负责检查并复用现有 Capture Session；
- 同一方法调用 `ProcessSupervisor.ensure_exclusive("capture")`；
- 调用 `CadenceVersionManager.select_best_installation()`，查找 Capture.exe 和 capAutoLoad；
- 创建 `CaptureTclBridgeSession`，构造 Popen、启动 Capture、等待 READY；
- 还将 Capture 进程写入 `eda_executor.managed_processes["capture"]`，使新 Session 依赖旧 EDAExecutor 的进程台账。

这意味着 Worker 不只是通用调度器，还知道 Capture 的启动细节。CaptureTclAdapter 目前负责“提交 Tcl 并收取结果”，却没有真正拥有完整的 Capture Session 创建/关闭生命周期。

**目标调整：**

- AirBridge Core / Worker 负责通用 Session ID、Request ID、状态、排队、权限和请求转发；
- Capture Runtime Adapter 持有 Capture 进程句柄、CaptureTclBridgeSession 和 READY 状态；
- Adapter 负责 Capture 安装发现、capAutoLoad、Bridge 初始化和运行时版本报告；
- 通用 ProcessSupervisor 继续由 Core 提供，但 Adapter 通过 ProcessSpec / Profile 提交所需规则；
- 不再借用 `EDAExecutor.managed_processes` 保存 Capture Adapter 的进程所有权。

此处是第一个应实施的代码重构点。

### 3.2 eda_engine.py 包含三个层次的代码

当前 `eda_engine.py` 大致包含：

**A. Capture 专业 API 辅助代码：**

- `ORCAD_TCL_POLYFILL`；
- `is_dbo_null`、`ab_cstr`、`ab_get_str`；
- `ab_get_prop` / `ab_set_prop`；
- `ab_open_dsn` / `ab_save_and_close`。

这些函数理解 Dbo / CString / Design 属性和保存语义，应由 Capture Skill 维护，不应由 AirBridge Core 或通用 Runtime Adapter 维护。

**B. Allegro 专业 API 与工作流代码：**

- `ALLEGRO_SKILL_POLYFILL`；
- `axlSetVariable`、`axlOpenDesign` 等 Skill 辅助函数；
- BRD 侧文件、交互式开板及 SKILL 前置流程。

这些应归 Allegro Skill。AirBridge Adapter 只提供 Allegro 软件运行时接入和 SKILL 文本执行通道。

**C. 软件发现与运行时接入代码：**

- `CadenceVersionManager`；
- Capture.exe / allegro.exe 定位；
- capAutoLoad 目录发现；
- Cadence 运行环境 PATH / CDSROOT 构造；
- 旧式一次性进程启动和结果日志收集。

这部分应拆到 Cadence Runtime Adapter 的共享安装发现工具和具体应用 Adapter 中。它不是 Dbo API Skill 兼容层，也不应留在 AirBridge Core。

`EDAExecutor` 当前同时承担 Capture Tcl 和 Allegro SKILL 的 legacy one-shot 执行，是兼容过渡层。迁移完成之前必须保留，不能直接删除或改变其对旧 GUI / Skill 的行为。

### 3.3 通用 Adapter 模块混入具体领域路由

`runtime_adapter.py` 同时定义了：

- `RuntimeAdapter`、`RuntimeProfile`、`RuntimeExecutionRequest`、`RuntimeAdapterRegistry`：这些是通用契约，应该保留在 Core；
- `LegacyCadenceOneShotAdapter`、`AllegroSkillAdapter`、`LegacyShellProcessAdapter`：这些是具体实现，应移到对应 Adapter 包；
- `select_eda_profile()`：调用 `detect_script_type()`，根据脚本内容和文件名决定 Capture / Allegro / Legacy Profile。

其中 `AllegroSkillAdapter` 还需要从 `eda_engine` 导入 `EDALanguage` / `detect_script_type` 做脚本类型验证。

**目标调整：**

- Core Registry 只根据显式 `profile_id`、operation 和能力声明分发；
- Agent Skill 明确选择所需 Profile 和脚本 content_type；
- 对未升级的旧客户端，单独保留 Legacy EDA Router 作为兼容入口；
- Legacy Router 可以暂时识别旧 hint / 文件后缀或源码特征，但它不能成为新 Runtime 协议的必需路径。

这属于渐进迁移，不能在旧客户端仍使用 `/api/v1/eda/execute` 默认字段时突然取消类型识别。

### 3.4 ProcessSupervisor 的应用规格仍有默认 Cadence 配置

`process_supervisor.py` 的 ProcessSupervisor 本身使用可替换 ProcessBackend 和可注入 ProcessSpec，适合作为通用 Core 模块。但 `DEFAULT_SPECS` 当前内置 Capture.exe 与 allegro.exe 两个应用配置。

目标应是：ProcessSupervisor 保留通用状态机和 Windows 操作；Capture / Allegro Adapter 或 Runtime Profile 提供具体 `app_id`、image_names、display_name 和独占组配置。这样未来增加其他 Runtime 时不需要修改 Core 的 ProcessSupervisor 源码。

窗口对话框的通用安全策略可继续留在 Core；特殊应用若有例外，应由 Profile 提供额外匹配规则，而不是在通用逻辑中堆积 Cadence API 语义。

### 3.5 Capture PDF Export Skill 存在隐式 Polyfill 依赖

当前仓库已经有两个不同性质的 Agent Skill：

- `skill/airbridge/`：通用远端控制 Skill，负责 Host B 的 RFS、Shell 和 EDA API 调用；
- `skill/orcad-pdf-export/`：专业 Capture 工作流 Skill，负责原理图 PDF 导出。

`skill/orcad-pdf-export/references/export-template.tcl` 使用 Dbo Session 的 `GetActiveDesign`，并调用 `is_dbo_null`。后者来自 `eda_engine.py` 自动注入的 `ORCAD_TCL_POLYFILL`，并非 PDF Export Skill 自己定义或显式 source 的依赖。

这正是目前 Skill 与旧 EDA 引擎隐式耦合的实例。

**迁移要求：**

1. 先把 Skill 依赖的轻量 NULL 检查函数移入 Capture Skill 自己的 helper 文件，或在模板中定义局部 helper；
2. 增加 PDF Export Skill 的脚本级回归，证明不依赖 AirBridge 注入也能运行；
3. 再评估从旧 Capture one-shot 路径移除对应 Polyfill；
4. 对其他 Capture Skill / Tcl 模板做同类依赖搜索，不能只根据 PDF Skill 推断全部调用者已迁移。

不得因为这一个 Skill 已迁移，就直接删除整个 `ORCAD_TCL_POLYFILL`。

### 3.6 Capture E2E 测试混合了 Runtime 验收和 Dbo API 验收

`scripts/capture_session_e2e.py` / `test/test_capture_session_e2e.py` 当前检查：

- 两条 Tcl 请求复用同一个 PID / Interpreter；
- 全局变量和 proc 跨请求保持；
- `GetActivePMDesign`、`GetActivePage`、`OPage` 命令存在；
- 实际调用 `GetActivePMDesign` 成功。

前两项属于 Capture Runtime Adapter / Bridge 的接入验收；后三项属于 Capture 专业 Tcl API 的验收。

未来应拆成：

- **Adapter E2E：** 只检查不透明 Tcl 脚本的提交、PID/Session、Interpreter 持久状态、错误回传、串行和重启；
- **Capture Skill E2E：** 在目标 Capture 版本中检查 Dbo API、工程对象关系、页面操作、读写和保存。

Adapter 不应因某版本缺少某个 Dbo 方法就被判定为通信或进程接入失败。

## 4. 目标模块归属

| 模块/能力 | 应归属 |
|---|---|
| ABFP / Runtime Session Protocol / RequestTracker / RequestBroker | AirBridge Core |
| HTTP API / A-B Serial Worker / 文件传输 / RFS / Shell | AirBridge Core |
| RuntimeAdapter 抽象、Profile Registry、通用 Result | AirBridge Core |
| 通用 ProcessSupervisor 状态机、ProcessBackend | AirBridge Core |
| Capture 进程发现、版本报告、capAutoLoad、Tcl Bridge、Capture Session 进程句柄 | Capture Runtime Adapter |
| Allegro 进程发现、版本报告、SKILL 执行接入、Allegro Session/GUI 接入 | Allegro Runtime Adapter |
| Dbo Tcl API、CString、Design/Schematic/Page/Instance/Pin/Net、对象遍历及其版本差异 | Capture Skill |
| Allegro axl API、数据库对象、PCB 操作与版本差异 | Allegro Skill |
| PDF 导出业务流程 | OrCAD PDF Export Skill |
| 旧 EDAExecutor 与源码类型识别 | Legacy Compatibility Adapter / Router，迁移完成后退役 |

## 5. 建议的渐进迁移顺序

迁移原则：一次只改变一条职责边界；先添加新接口和回归，再切换调用；保留旧接口直到所有已知客户端迁移；每一阶段都不破坏当前 Host A → AirBridge → Host B → Capture 17.2 的已验收路径。

### M1：Capture Adapter 接管 Session 生命周期（首要任务）

**目标：** 将 `_ensure_capture_tcl_bridge()` 的 Capture 专用内容从 `SerialWorker` 移到 Capture Runtime Adapter / Session Manager。

建议范围：

- Adapter 拥有 Capture 进程句柄、Bridge Session 和 READY 状态；
- Adapter 提供通用的 create/ensure-session、execute、close/status 能力；
- Adapter 内部调用通用 ProcessSupervisor，但 ProcessSupervisor 不认识 Dbo/Tcl；
- Capture 安装/版本探测、capAutoLoad 路径和隔离运行环境由 Capture Adapter 管理；
- 不再写入 `EDAExecutor.managed_processes`；
- SerialWorker 只根据 Profile 路由、执行 Request、回传 Result，不自行拼 Capture 命令行或 hook 路径。

**兼容要求：** M1 暂不改变 `/api/v1/eda/execute`、现有 EDA_TASK_SUBMIT / EDA_TASK_FIN、Request ID、结果格式和 Capture Tcl payload。先把内部所有权移入 Adapter，避免同时重做 ABFP 协议和进程管理。

**验收：**
- Capture 17.2 自动初始化、READY nonce/PID、单条/连续请求；
- 错误隔离、并发串行、同 PID 复用；
- WM_CLOSE 正常关闭、关闭后重新创建 Session；
- 旧 EDA / Allegro one-shot 测试保持通过；
- 不再通过 EDAExecutor 保存 Capture Adapter 进程句柄；
- A/B Host B 实机回归。

### M2：把 Cadence 软件发现代码移入 Runtime Adapter 包

将 `CadenceVersionManager`、`find_cadence_tool`、`find_cap_autoload_dirs`、Cadence 运行环境生成从 `eda_engine.py` 拆出。

允许建立共享 Cadence 安装发现工具供 Capture / Allegro Adapter 使用，但 Core 不应导入 Cadence 相关模块。版本报告必须是目标机器检测到的事实；API 方法选择仍由 Skill 决定。

### M3：解除 Skill 对隐式 Polyfill 的依赖

- 为 Capture Skill 建立自己的 Dbo Tcl helper source 文件；
- 为 Allegro Skill 建立自己的 SKILL helper 文件；
- 迁移前扫描整个 `skill/`、`scripts/` 和旧模板，列出 `ab_*`、`is_dbo_null`、`axl*` 等依赖；
- 更新各 Skill 和脚本的显式引用；
- 保留 EDAExecutor 旧注入逻辑作为 Legacy 路径，直到所有调用方有独立测试；
- 迁移完成后再从 legacy EDA engine 移除专业 Polyfill。

### M4：从脚本内容猜测路由迁移到显式 Profile

为 EDA 执行请求增加可选的显式 `profile_id` 和 `content_type`，由 Skill 指定 Capture Tcl / Allegro SKILL。Core 根据 Profile 注册表路由，不解析脚本正文。

老客户端未提供 Profile 时，暂时继续走 Legacy EDA Router。新 Skill 完成迁移并经过回归后，才能逐步把猜测式路由降级为纯兼容功能。

### M5：基于统一 Adapter 生命周期实现 Runtime Session 协议

在 Adapter 生命周期接口确定后，再实现：

- SESSION_CREATE；
- SESSION_EXEC；
- SESSION_STATUS；
- SESSION_CANCEL；
- SESSION_CLOSE；
- 每 Session 单一 FIFO 消费者；
- A/B Profile 发现、能力验证与 profiles 发布；
- Broker / Worker 重启、Capture 崩溃、断连恢复和 UNKNOWN 恢复语义。

不要在 Adapter 尚未拥有 Session 创建/关闭职责时，先把 Session 协议做成一个只会调用现有 Worker 私有方法的薄封装。

### M6：拆分 airbridge_app.py 的 UI / HTTP / Worker 结构

将 Qt 界面、HTTP Handler、SerialWorker 和服务装配逐步分离。此项是结构性重构，风险和范围都较大；应在 M1–M5 的依赖边界稳定后进行，避免与 Capture 适配器迁移同时发生。

### M7：将 Capture Dbo 研究资料迁入独立 Capture Skill 项目

当前仓库中的：

- `doc/Capture_Dbo_API_Tcl_示例索引_v1.0.md)；
- `scripts/capture_dbo_session_iterators_probe.tcl)；
- 相关 Dbo 命令清单和实验结果；

仅暂时保留作迁移资料。待 Capture Skill 仓库 / 发布结构确立后整体迁移，不应继续把 Dbo API 研究作为 AirBridge 开发任务。

## 6. 迁移验收门槛

每个模块迁移必须同时满足：

1. **Core 隔离：** Core 模块不 import Capture / Allegro API 或脚本 helper。
2. **Adapter 薄化：** Adapter 只接触进程、Bridge、传输和运行时元数据；脚本正文对它是不透明内容。
3. **Skill 自主：** Skill 显式携带自身的专业 Tcl/SKILL helper，不依赖 AirBridge 启动时偷偷注入的领域业务函数。
4. **兼容保留：** 未迁移的旧客户端仍走 Legacy Adapter / Router，不破坏旧 API。
5. **软件回归：** 相关单元、A/B Worker 模拟、HTTP 集成和旧流程回归全部通过。
6. **真实回归：** 对 Capture Adapter 变更，需在真实 Capture 17.2 / Tcl 8.6 实机复验；软件 CI 不能替代现场证据。
7. **生命周期安全：** 不强杀进程，不自动决定保存/丢弃工程；UNKNOWN 不得被当成已取消。
8. **发布边界：** Adapter 报告 Runtime 版本；Skill 负责选择版本化专业 API 包。

---

## 7. 本轮不做的事情

本次审计没有修改任何 Python/Tcl 代码，也没有：

- 删除 `ORCAD_TCL_POLYFILL` 或 `ALLEGRO_SKILL_POLYFILL`；
- 改变旧 EDA HTTP API 或 ABFP 帧；
- 搬动 `capture_tcl_bridge.py`；
- 修改 Capture / Allegro 的启动和关闭方式；
- 创建新的 Skill 仓库或移动已有 Skill；
- 重启 A/B AirBridge 或主机 B Capture。

本文件只给出代码现状和迁移次序。实际迁移应按 M1 开始逐项实施。

## 8. 相关代码入口

以下链接对应本次审计基线：

- [airbridge_app.py — SerialWorker 与 Capture 启动入口](../airbridge_app.py)
- [capture_tcl_bridge.py — Capture Tcl Bridge / Adapter](../capture_tcl_bridge.py)
- [runtime_adapter.py — Runtime Adapter / Profile Registry](../runtime_adapter.py)
- [eda_engine.py — 旧 EDA Engine、Polyfill 与 Cadence 发现](../eda_engine.py)
- [process_supervisor.py — 通用进程监督](../process_supervisor.py)
- [scripts/capture_session_e2e.py — Host-A E2E harness](../scripts/capture_session_e2e.py)
- [skill/orcad-pdf-export — Capture 专业 Skill](../skill/orcad-pdf-export/)

