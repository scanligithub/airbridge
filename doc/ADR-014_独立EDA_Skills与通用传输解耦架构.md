# ADR-014：独立 EDA Skills 与通用传输解耦架构

- 文档编号：AB-ADR-014
- 版本：1.0
- 日期：2026-11-26
- 状态：**项目负责人已确认的架构约束；通过 PR 合并后作为后续设计与实施基线**
- 适用范围：AirBridge Core、面向不同 EDA 厂商及具体专业产品的独立 EDA Skills（包括 OrCAD Capture、OrCAD PCB Editor、Altium Designer、Allegro 等未来可分别建设的 Skill）、各类现有及未来 Agent Host（Antigravity、OpenCode 仅为当前示例），以及 Skill 与远端运行环境之间的通用访问层

> 本 ADR 补充并细化 [ADR-006：专业软件生命周期由 Agent Skill 自主掌握](ADR-006_Skill自主掌握专业软件生命周期.md)。ADR-006 规定 AirBridge Core 不拥有专业软件生命周期；本 ADR 进一步规定 EDA Skills 必须是独立、可安装、可演进的软件包，并通过稳定的通用传输契约与远端环境交互。
>
> 本 ADR 是未来架构约束，不表示本文描述的所有模块、接口或安全能力已经实现。现有实现与目标架构不一致时，应记录差距并分阶段迁移，不得把目标设计描述成当前能力。

---

## 1. 决策摘要

**每一种 EDA 专业产品的领域能力应由对应的独立 Skill 承担；不同产品的 Skill 必须可分别安装、升级、测试和发布，且不得与 AirBridge Core 或某一个 Agent 产品绑定。**

例如，OrCAD Capture 与 OrCAD PCB Editor 必须分别建设为两个独立 Skill；Altium Designer（AD）和 Allegro 也分别由各自的产品 Skill 承担。未来新增 EDA 厂商或产品时，按同一原则新增独立 Skill。上述名称描述目标架构方向，不表示这些 Skill 均已实现或通过验收。

目标关系：

1. OrCAD Capture Skill 专注 OrCAD Capture 的专业软件生命周期、Tcl / Dbo API、工程对象模型、版本差异、Capture 工作流和结果验证。
2. OrCAD PCB Editor Skill 专注 OrCAD PCB Editor 的专业软件生命周期、相应自动化接口、PCB 对象模型、版本差异、PCB 工作流和结果验证；它与 Allegro Skill 分别维护，不因产品关系而合并。
3. Altium Designer（AD）Skill 专注 Altium Designer 自身的专业软件生命周期、自动化接口、设计对象模型、版本差异和工作流。
4. Allegro Skill 专注 Allegro 的专业软件生命周期、SKILL API、PCB 对象模型、版本差异、PCB 工作流和结果验证。
5. 未来其他 EDA 厂商或产品应按具体产品边界建设独立 Skill，不以当前列出的产品作为支持范围上限。
6. 上述各 Skill 都应能够安装到不同厂商、不同形态的 Agent Host。支持原生 Skill 规范的 Host 可直接加载；不原生支持的 Host 可通过独立 Host Adapter 接入。Antigravity 和 OpenCode 是当前示例，不构成支持范围上限。不能要求用户必须同时安装另一个 Skill。
4. Skill 的领域逻辑不得依赖 AirBridge 的内部 Python 模块、内部类、私有目录结构、内部数据库或 AirBridge.exe 进程内代码。
5. Skill 通过定义清晰、版本化、可替换的通用传输接口访问远端环境。AirBridge 可以作为该接口的一种传输实现，但不得成为 Skill 领域逻辑的组成部分或唯一可理解的运行环境。
6. 允许存在独立的通用访问适配器、客户端库或本机 Broker，以承接认证、授权、审计和传输；这些组件不得引入任何 EDA 产品专属业务语义。
7. 不同 EDA 产品 Skill 不得相互导入、调用或继承对方的领域实现。确有通用基础设施需要共享时，必须抽取为独立、通用、版本化的公共库。

## 2. 总体架构与依赖方向

    Agent Host（任意当前或未来的兼容 Agent Host）
           │
           ├── 独立安装：OrCAD Capture Skill
           │       └── Capture 领域工作流 / Tcl / Dbo / 版本适配
           │
           ├── 独立安装：OrCAD PCB Editor Skill
           │       └── PCB Editor 产品领域工作流 / 对象模型 / 版本适配
           │
           ├── 独立安装：Altium Designer（AD）Skill
           │       └── Altium Designer 产品领域工作流 / 对象模型 / 版本适配
           │
           ├── 独立安装：Allegro Skill
           │       └── Allegro 领域工作流 / SKILL / PCB 对象模型
           │
           └── 可独立增加：其他 EDA 产品 Skill
                        │
                        ▼
           通用 Skill Transport Interface
           （稳定契约；不包含 EDA 领域语义）
                        │
              可替换的 Transport Provider
              ├── AirBridge Provider
              ├── 未来其他远程传输实现
              └── 测试 / 本地模拟 Provider
                        │
                        ▼
                 远端通用运行环境
              Host A AirBridge.exe
                        │
                    ABFP / Serial
                        │
              Host B AirBridge.exe
                        │
              通用 Shell / RFS / 文件传输
                        │
                        ▼
           Host B 上的专业软件（由对应产品 Skill 通过通用 Transport 交互）
           OrCAD Capture / OrCAD PCB Editor / Altium Designer / Allegro / 其他 EDA 产品

依赖方向必须保持为：

- Skill 依赖通用 Transport Interface，不依赖某个 Provider 的内部实现。
- AirBridge Provider 依赖通用 Transport Interface，并适配 AirBridge 对外公开的通用 API。
- AirBridge Core 不依赖任何 EDA Skill 或其领域模型。
- Agent Host 适配只处理 Skill 发现、安装、加载和必要的 Host 能力差异，不承载领域业务。
- 不同 EDA 产品 Skill 之间不存在运行时依赖，包括同一产品套件中的 OrCAD Capture Skill 与 OrCAD PCB Editor Skill。

图中 AirBridge 是当前项目的远端通信实现，不意味着 Skill 包必须与 AirBridge 同仓库、同安装包、同版本或同一发布周期。

## 3. Skill 独立性要求

### 3.1 独立安装

- 每个 EDA 产品 Skill 必须可以独立安装、卸载和升级；例如 OrCAD Capture Skill 与 OrCAD PCB Editor Skill 之间不存在安装依赖。
- 安装任一 EDA 产品 Skill 不得强制安装其他 EDA 产品 Skill。
- 每个产品 Skill 均应能够被所有声明兼容的 Agent Host 分别发现、加载和使用；新 Host 的接入应通过标准 Skill 契约或独立 Host Adapter 完成，不得要求复制或重写领域逻辑。
- Skill 包应声明自身名称、版本、入口文件、脚本和资源、依赖、兼容范围及安装说明。
- Agent Host 特有的元数据或配置应通过薄适配层处理；不得复制两套不同的领域逻辑。
- Skill 安装或加载成功，只能证明 Host 能发现并加载该 Skill，不能据此声称远端通信、专业软件启动或业务操作已验收。

### 3.2 独立开发和发布

每个 Skill 应有独立的版本号和变更记录、领域脚本与资源、参考资料、自动化测试与测试夹具、兼容性声明和已验证的软件版本矩阵、安全权限声明与风险说明、发布包、安装说明和升级 / 回滚策略。

任一 EDA 产品 Skill 的版本升级不应要求其他产品 Skill 同步升级。若某个共享库升级影响多个 Skill，必须明确声明兼容范围并分别验证受影响的 Skill。

### 3.3 领域边界隔离

每个 Skill 只拥有其对应专业产品的领域知识。以下列出当前明确的产品边界示例：

**OrCAD Capture Skill** 独占 OrCAD Capture 领域知识，包括但不限于：

- Capture 安装发现、版本选择和运行环境检查；
- Capture 进程识别、启动、复用和正常退出策略；
- Tcl Bridge、capAutoLoad Hook、Session / Request / Result 机制；
- Capture Tcl / Dbo API、对象模型和版本差异；
- OPJ / DSN 工程工作流、设计数据检查和结果验证；
- Capture 保存决策及异常恢复策略。

**OrCAD PCB Editor Skill** 独占 OrCAD PCB Editor 产品领域知识，包括但不限于：

- PCB Editor 安装发现、版本选择和运行环境检查；
- PCB Editor 进程及会话生命周期管理；
- PCB Editor 所支持的自动化入口、脚本执行与结果收集机制；
- PCB Editor 自身的 PCB 对象模型、设计数据和版本差异；
- PCB Editor 工程工作流、设计数据检查和结果验证；
- 与 PCB Editor 产品行为相关的保存决策及异常恢复策略。

**Altium Designer（AD）Skill** 独占 Altium Designer 产品领域知识，包括但不限于：

- Altium Designer 安装发现、版本选择和运行环境检查；
- Altium Designer 进程及会话生命周期管理；
- Altium Designer 所支持的自动化入口、脚本执行与结果收集机制；
- Altium Designer 自身的设计对象模型、项目数据和版本差异；
- Altium Designer 工作流、设计数据检查和结果验证；
- 与 Altium Designer 产品行为相关的保存决策及异常恢复策略。

**Allegro Skill** 独占 Allegro 领域知识，包括但不限于：

- Allegro 安装发现、版本选择和运行环境检查；
- Allegro 进程识别、启动、复用和正常退出策略；
- SKILL Bridge、加载入口、Session / Request / Result 机制；
- Allegro SKILL API、PCB 对象模型和版本差异；
- BRD 工程工作流、设计数据检查和结果验证；
- Allegro 保存决策及异常恢复策略。

OrCAD Capture Skill、OrCAD PCB Editor Skill、Altium Designer Skill 与 Allegro Skill 必须各自维护独立的产品领域实现。不得因为产品属于同一厂商或同一产品套件而合并领域逻辑；也不得因为不同厂商产品具有相似功能而共享产品专属对象模型。

禁止把 Capture / Dbo 逻辑放入其他 EDA 产品 Skill，禁止把 Allegro / axl 逻辑放入其他 EDA 产品 Skill，也禁止把 OrCAD PCB Editor 或 Altium Designer 的产品专属逻辑迁入其他 Skill 或 AirBridge Core。不同产品即使存在相似工作流，也只能共享领域无关的基础设施。

## 4. 与 AirBridge 解耦的具体含义

“与 AirBridge 解耦”是指领域实现、安装包、生命周期和版本演进不依赖 AirBridge 内部实现；**不等于 Skill 在没有任何远端传输服务时仍能完成远端操作**。

必须满足：

1. Skill 不得直接 import AirBridge 仓库内部模块或依赖其内部类、私有函数、数据库表和源码目录。
2. Skill 不得要求被复制进 AirBridge.exe、PyInstaller bundle 或 AirBridge Core 的 Python 运行环境。
3. Skill 不得依赖 AirBridge Core 的内部进程状态、内部消息帧、串口实现或私有状态文件。
4. Skill 只依赖明确公开、版本化的通用 Transport Interface；AirBridge Provider 负责把该接口映射到 AirBridge 当前公开的 HTTP / Shell / RFS / 文件传输能力。
5. Skill 的领域单元测试必须能够使用 Mock / Fake Transport Provider 执行，不需要启动 AirBridge.exe、连接串口或安装 Cadence 软件。
6. Skill 的集成测试可以选择 AirBridge Provider，但必须将传输层测试和专业软件领域测试分层报告。
7. 将来替换 AirBridge、调整 ABFP、修改串口实现、增加新的 Agent Host 或更换现有 Agent Host 时，不应要求重写任何 EDA 产品 Skill 的领域工作流；最多调整相应 Transport Provider 或 Host Adapter。
8. AirBridge 不可用时，Skill 应报告传输不可用或依赖缺失，不得偷偷绕过认证、权限、审计或安全策略。

## 5. 通用 Transport Interface 的设计要求

Transport Interface 是 Skill 与远端执行环境之间的稳定边界。它只表达通用远程能力，不表达任何 EDA 产品的业务动作。

### 5.1 通用能力范围

接口可逐步覆盖以下通用能力，实际实现以独立能力协商和版本化契约为准：

- 远端能力、运行时身份和版本信息读取；
- 通用 Shell 命令提交及执行结果读取；
- 远端文件 / 目录读取、写入、列表、状态和必要的文件管理；
- 大文件分块传输、完整性校验和传输状态；
- 通用异步任务状态、请求结果查询和取消（仅在语义明确且受支持时）；
- 请求 ID、关联、去重、超时和 UNKNOWN 状态处理；
- 认证、授权、Scope / Policy 检查、审计和限流；
- 错误分类、能力缺失和 Provider 版本协商。

上述能力是接口设计方向，不表示当前 AirBridge 已全部提供或已具备足够细粒度的安全控制。不得在没有核实现有实现的情况下，将建议接口描述为现有 API。

### 5.2 不允许进入 Transport Interface 的内容

禁止将以下领域动作设计成 AirBridge Core 的专用 API 或通用 Transport Interface 的固定业务方法：

- start_capture、open_capture_project、submit_capture_tcl；
- start_allegro、open_allegro_board、submit_allegro_skill；
- 任一 EDA 产品专用的保存、关闭、重启、实例复用或工程状态操作；
- Dbo Design、Schematic、Page、Instance、Net 或 Allegro Board、Symbol、Shape 等专业对象方法；
- 根据脚本扩展名、关键字或内容猜测应使用哪个 EDA 软件的路由逻辑。

这些方法可以作为对应 Skill 内部的领域 API，但不能成为 AirBridge Core 的协议或服务端业务分支。

### 5.3 契约版本化

- Transport Interface 必须有明确的接口版本和兼容规则。
- Provider 必须声明其实现的接口版本和支持的能力；不得把“Provider 存在”误判为“所有能力均支持”。
- 增加、删除或改变接口语义时，应评估向后兼容性并更新契约测试。
- 领域 Skill 不得依赖 Provider 未声明的隐式行为。
- 传输层错误、远端命令错误和专业软件业务错误必须可区分。
- 请求已提交不等于远端命令完成；远端命令完成不等于专业软件业务成功。
- 对于结果不确定的请求，必须保留 UNKNOWN / reconciliation 语义；不得将 UNKNOWN 自动解释为失败并盲目重放非幂等操作。

## 6. 通用共享库和模块边界

不同 EDA 产品 Skill 可以共享基础设施，但共享内容必须是领域无关的，例如：

- Transport Interface 的数据类型和协议客户端；
- 请求 ID、超时、重试分类和结果封装工具；
- 通用文件分块、摘要校验和文件清单工具；
- 通用配置读取、日志、诊断和错误分类；
- 通用权限声明格式和策略校验客户端。

共享库必须：

1. 独立于 AirBridge Core 的内部实现；
2. 不包含任何 EDA 产品专用分支；
3. 有独立版本和兼容性策略；
4. 可独立测试和发布；
5. 由 Skill 通过明确依赖声明引用；
6. 避免把共享库变成包含所有 EDA 领域代码的“大一统 Skill Runtime”。

共享库可以与 AirBridge 仓库同仓管理，也可以独立仓库发布；仓库位置不改变其逻辑独立性。不得仅为了代码复用而让不同 EDA 产品 Skill 在安装时互相依赖。

## 7. Agent Host 兼容原则

本架构面向开放、持续扩展的 Agent Host 生态，不以当前已验证的产品列表限定未来支持范围。Antigravity 和 OpenCode 仅代表当前接入示例。

- **不绑定具体 Agent 产品：** Skill 的领域逻辑、数据模型和工作流不得依赖某个 Agent Host 的私有 API、内部工具对象或专属运行时。
- **标准优先：** 优先采用公开、稳定、可跨 Host 使用的 Skill 包格式、入口约定和工具调用契约；对不同 Host 的能力差异进行显式声明。
- **Adapter 扩展：** 对不原生支持相应 Skill 规范的 Agent Host，可以增加独立的 Host Adapter，负责安装布局转换、入口注册、工具调用适配和必要的生命周期衔接。Adapter 不得承载任何 EDA 产品领域逻辑，也不得成为 Skill 的强制组成部分。
- **能力检测：** Skill 或 Host Adapter 应检测所需能力并给出明确诊断；不能因为 Host 名称或版本而假定某项能力必然存在。
- **按能力声明兼容：** 每个 Skill 应声明最低 Host 能力要求、可选能力和已验证的 Host / 版本矩阵。未测试的 Host 应标记为“待验证”，不得宣传为已验收。
- **新增 Host 的接入成本：** 增加一种 Agent Host，原则上只需验证标准兼容性或新增 / 更新 Host Adapter，不应复制 Skill 领域代码，也不应要求修改 AirBridge Core。
- **认证解耦：** 不得把某个 Agent Host 的私有 Token 注入方式、内部工具对象或私有 API 作为 Skill 的唯一认证入口。凭据获取应通过独立、受控、可替换的凭据提供机制。
- **独立维护：** Skill 必须能在不同兼容 Agent Host 上独立安装和维护；不得依赖另一款 Agent Host 的安装。

## 8. 认证、权限和安全边界

独立 Skill 不意味着绕过 AirBridge 的认证和授权，也不意味着每个 Skill 都应获得宽泛的远程执行权限。

- Skill 只申请当前工作流确实需要的权限，并声明用途、资源范围、操作类型和风险级别。
- Transport Provider 必须使用受支持的认证方式调用远端服务；不得通过未认证 API、Shell 或文件操作绕过服务端策略。
- Agent 身份、Token 和授权信息由受信任的凭据提供机制管理；不得通过打印 Token、探测 Token 是否存在 / 长度、读取凭据数据库或写入日志的方式诊断连接。
- 不得假设 Windows Credential Manager 的用户级凭据天然具备进程级隔离或不可导出属性。
- 不应把宽泛的 shell.execute 或 filesystem.write 视为长期最小权限模型。权限体系应逐步支持操作、资源和风险范围约束；当前能力不足时必须记录为安全差距。
- Skill 自身的业务授权检查不能替代 AirBridge 服务端认证、Scope / Policy、审计和限流。
- 新增权限或扩大现有 Scope 前，必须先说明具体调用点、必要性、风险和替代方案，并获得项目负责人明确授权。
- 禁止通过 Shell / RFS 绕过被禁用的专用 API 或服务端策略。
- 对写入、覆盖、删除、保存、关闭等具有数据影响的操作，必须由对应 Skill 实施明确的安全策略，并遵循用户授权边界。

## 9. 测试与验收分层

每个 Skill 至少应有以下测试层次：

| 测试层 | 目标 | 是否需要 AirBridge | 是否需要真实 EDA 软件 |
|---|---|---:|---:|
| Skill 单元测试 | 领域状态机、策略、输入校验、结果解释 | 否；使用 Fake / Mock Provider | 否 |
| Transport Contract 测试 | Skill 与 Provider 的接口兼容、错误和 UNKNOWN 语义 | 使用 Contract Test Provider | 否 |
| Provider 集成测试 | 认证、能力协商、Shell / RFS / 文件传输和请求关联 | 是，或等价 Provider 测试环境 | 否 |
| 专业软件集成测试 | Bridge、Tcl / SKILL 执行、软件版本和专业结果 | 需要目标 Transport Provider | 是 |
| Host 兼容测试 | Skill 安装、发现、加载和 Host 工具适配 | 不必；除非测试真实远端调用 | 否 |
| 端到端验收 | Agent Host → Skill → Provider → 远端软件 → 结果验证 | 需要所选 Provider | 是 |

必须分别报告 Skill 包安装 / 加载结果、Skill 单元测试结果、Transport Contract 测试结果、Provider 集成结果，以及各个目标 EDA 产品版本和主机环境的验收结果。

任何一层 PASS 不得替代其他层 PASS。特别是 Skill 被某一个或多个 Agent Host 发现并加载，不代表它已经取得远端权限、成功调用 Transport Provider / AirBridge 或执行过对应 EDA 产品操作。

## 10. 后续架构与实施顺序

后续实施按以下顺序推进，避免先为当前 Capture Skill 的局部需求冻结错误接口：

1. **盘点现状：** 明确当前 Skill 的领域代码、AirBridge 调用点、认证来源、权限需求和 Host 特有依赖；区分已实现与目标设计。
2. **定义通用 Transport Contract：** 规定版本、能力协商、请求 / 结果、错误、超时、UNKNOWN、文件传输和认证策略；不加入 EDA 业务 API。
3. **定义 Provider 边界：** 设计 AirBridge Provider 与 Fake / Mock Provider；先验证契约，再逐步迁移现有 Skill。
4. **定义 Skill 包规范：** 明确独立目录、元数据、依赖、版本、测试、发布、安装和升级策略。
5. **整理通用共享库：** 只提取领域无关代码；避免不同 EDA 产品 Skill 互相依赖。
6. **完善 OrCAD Capture Skill：** 以独立包方式逐步完善，不因其当前功能不完整而把领域能力下沉到 Core。
7. **独立设计 OrCAD PCB Editor Skill：** 单独定义产品接口、领域模型、测试和发布，不与 OrCAD Capture Skill 合并。
8. **独立设计 Altium Designer（AD）Skill 与 Allegro Skill：** 分别按各自产品边界建设；后续其他 EDA 产品按相同原则扩展。
9. **完善权限模型：** 结合各产品 Skill 的实际操作矩阵，设计可扩展、最小权限的授权方式。
10. **分层验收和迁移：** 保留现有兼容路径，逐项迁移并验证，不进行未经评估的大规模重构。

在 Transport Contract 和包规范评审通过前，不应为了当前 Skill 方便而把某一组 Shell / RFS 调用方式永久固化成唯一抽象，也不应贸然创建任何 EDA 产品专用 AirBridge API。

## 11. 新增功能和代码评审的强制检查项

今后任何涉及 AirBridge、任一 EDA 产品 Skill、共享库或 Agent Host 集成的 PR，都必须回答：

1. 这段代码属于 Core、Transport Provider、共享库、Host Adapter，还是某个具体 EDA 产品 Skill？
2. 是否引入任何 EDA 产品专属语义到 AirBridge Core 或通用 Transport Contract？
3. 各 EDA 产品 Skill 是否仍可独立安装、测试、升级和发布？
4. 是否引入了对另一个 Skill、AirBridge 内部模块或某个 Agent Host 私有 API 的不必要依赖？
5. 是否明确声明了接口版本、能力要求和兼容策略？
6. 是否遵循最小权限、认证、审计和用户授权边界？
7. 是否覆盖错误、超时、重复请求和 UNKNOWN 状态？
8. 是否区分单元测试、Provider 集成测试和真实专业软件验收？
9. 是否同步更新架构文档、Skill 文档、依赖声明和测试证据？
10. 如果本次变更偏离 ADR-006 或本 ADR，是否先提交明确的架构决策修订，而不是通过实现悄悄改变边界？

不满足以上约束的设计不得作为默认架构方案合并。确需变更时，应先提出 ADR 修订并说明影响范围、迁移方案和兼容策略。

## 12. 与现有决策的关系

- **ADR-006**：继续作为 AirBridge Core 与专业软件生命周期所有权的最高优先级决策；本 ADR 不改变其“双端同一 AirBridge.exe、Core 通用透明、生命周期归 Skill”的原则。
- **ADR-009 / ADR-012 / ADR-013**：Agent Host 配置、Adapter 注册和 MCP STDIO 接入仍按各自决策执行；不得据此把领域 Skill 与某个 Host 或 AirBridge Gateway 绑定。
- **现有 OrCAD Capture Skill**：是当前迁移对象和功能基线，不是未来所有 EDA 产品 Skill 的模板上限。其目录和调用方式可以逐步重构，不应被视为已满足本 ADR。
- **未来 OrCAD PCB Editor、Altium Designer（AD）及 Allegro Skills**：必须分别按产品边界独立设计，不得作为 OrCAD Capture Skill 的子模块或依赖其他 EDA Skill 才能安装的扩展。
- **历史文档和兼容代码**：继续保留历史事实和测试证据；与本 ADR 冲突的旧目标设计不再作为新实现依据。

---

## 13. 最终原则

> **AirBridge 负责通用、安全、可靠的远程通信；Transport Provider 负责把通用传输契约映射到具体通信实现；每个 EDA 产品 Skill 负责且只负责对应产品的专业领域工作。**
>
> **OrCAD Capture 与 OrCAD PCB Editor 分别建设为独立 Skill；Altium Designer、Allegro 及未来新增的 EDA 产品也按具体产品边界独立建设。所有 Skill 均可分别安装到兼容的 Agent Host，分别开发、测试、升级和发布，不依赖 AirBridge 内部实现，也不依赖其他 EDA Skill。**
>
> **任何未来功能都必须遵守领域隔离、接口稳定、最小权限和分层验收原则。**
