# ADR-014：独立 EDA Skills 与通用传输解耦架构

- 文档编号：AB-ADR-014
- 版本：1.0
- 日期：2026-11-26
- 状态：**项目负责人已确认的架构约束；通过 PR 合并后作为后续设计与实施基线**
- 适用范围：AirBridge Core、Capture Skill、Allegro Skill、未来其他 EDA Skills、Antigravity / OpenCode 等 Agent Host，以及 Skill 与远端运行环境之间的通用访问层

> 本 ADR 补充并细化 [ADR-006：专业软件生命周期由 Agent Skill 自主掌握](ADR-006_Skill自主掌握专业软件生命周期.md)。ADR-006 规定 AirBridge Core 不拥有专业软件生命周期；本 ADR 进一步规定 EDA Skills 必须是独立、可安装、可演进的软件包，并通过稳定的通用传输契约与远端环境交互。
>
> 本 ADR 是未来架构约束，不表示本文描述的所有模块、接口或安全能力已经实现。现有实现与目标架构不一致时，应记录差距并分阶段迁移，不得把目标设计描述成当前能力。

---

## 1. 决策摘要

**Capture Skill 与 Allegro Skill 必须是两个相互独立、可分别安装、可分别升级、可分别测试和发布的 Skill；二者均不得与 AirBridge Core 或某一个 Agent 产品绑定。**

目标关系：

1. Capture Skill 专注 OrCAD Capture 的专业软件生命周期、Tcl / Dbo API、工程对象模型、版本差异、Capture 工作流和结果验证。
2. Allegro Skill 专注 Allegro 的专业软件生命周期、SKILL API、PCB 对象模型、版本差异、PCB 工作流和结果验证。
3. 两个 Skill 都可安装到 Antigravity、OpenCode 等支持相应 Skill 规范的 Agent Host；不能要求用户必须同时安装另一个 Skill。
4. Skill 的领域逻辑不得依赖 AirBridge 的内部 Python 模块、内部类、私有目录结构、内部数据库或 AirBridge.exe 进程内代码。
5. Skill 通过定义清晰、版本化、可替换的通用传输接口访问远端环境。AirBridge 可以作为该接口的一种传输实现，但不得成为 Skill 领域逻辑的组成部分或唯一可理解的运行环境。
6. 允许存在独立的通用访问适配器、客户端库或本机 Broker，以承接认证、授权、审计和传输；这些组件不得引入 Capture / Allegro 专属业务语义。
7. Capture Skill 与 Allegro Skill 不得相互导入、调用或继承对方的领域实现。确有通用基础设施需要共享时，必须抽取为独立、通用、版本化的公共库。

## 2. 总体架构与依赖方向

    Agent Host（Antigravity / OpenCode / 其他兼容 Host）
           │
           ├── 独立安装：Capture Skill
           │       └── Capture 领域工作流 / Tcl / Dbo / 版本适配
           │
           ├── 独立安装：Allegro Skill
           │       └── Allegro 领域工作流 / SKILL / PCB 对象模型
           │
           └── 可选：其他专业软件 Skill
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
           Host B 上由对应 Skill 管理的专业软件
           Capture / Allegro / 其他软件

依赖方向必须保持为：

- Skill 依赖通用 Transport Interface，不依赖某个 Provider 的内部实现。
- AirBridge Provider 依赖通用 Transport Interface，并适配 AirBridge 对外公开的通用 API。
- AirBridge Core 不依赖 Capture Skill、Allegro Skill 或其领域模型。
- Agent Host 适配只处理 Skill 发现、安装、加载和必要的 Host 能力差异，不承载领域业务。
- Capture Skill 与 Allegro Skill 之间不存在运行时依赖。

图中 AirBridge 是当前项目的远端通信实现，不意味着 Skill 包必须与 AirBridge 同仓库、同安装包、同版本或同一发布周期。

## 3. Skill 独立性要求

### 3.1 独立安装

- Capture Skill 和 Allegro Skill 必须可以分别安装、卸载和升级。
- 安装 Capture Skill 不得强制安装 Allegro Skill；安装 Allegro Skill 不得强制安装 Capture Skill。
- 两个 Skill 均应能够分别被 Antigravity 和 OpenCode 发现、加载和使用。
- Skill 包应声明自身名称、版本、入口文件、脚本和资源、依赖、兼容范围及安装说明。
- Agent Host 特有的元数据或配置应通过薄适配层处理；不得复制两套不同的领域逻辑。
- Skill 安装或加载成功，只能证明 Host 能发现并加载该 Skill，不能据此声称远端通信、专业软件启动或业务操作已验收。

### 3.2 独立开发和发布

每个 Skill 应有独立的版本号和变更记录、领域脚本与资源、参考资料、自动化测试与测试夹具、兼容性声明和已验证的软件版本矩阵、安全权限声明与风险说明、发布包、安装说明和升级 / 回滚策略。

Capture Skill 的版本升级不应要求 Allegro Skill 同步升级，反之亦然。若某个共享库升级影响多个 Skill，必须明确声明兼容范围并分别验证受影响的 Skill。

### 3.3 领域边界隔离

Capture Skill 独占 Capture 领域知识，包括但不限于：

- Capture 安装发现、版本选择和运行环境检查；
- Capture 进程识别、启动、复用和正常退出策略；
- Tcl Bridge、capAutoLoad Hook、Session / Request / Result 机制；
- Capture Tcl / Dbo API、对象模型和版本差异；
- OPJ / DSN 工程工作流、设计数据检查和结果验证；
- Capture 保存决策及异常恢复策略。

Allegro Skill 独占 Allegro 领域知识，包括但不限于：

- Allegro 安装发现、版本选择和运行环境检查；
- Allegro 进程识别、启动、复用和正常退出策略；
- SKILL Bridge、加载入口、Session / Request / Result 机制；
- Allegro SKILL API、PCB 对象模型和版本差异；
- BRD 工程工作流、设计数据检查和结果验证；
- Allegro 保存决策及异常恢复策略。

禁止把 Capture / Dbo 逻辑放入 Allegro Skill，禁止把 Allegro / axl 逻辑放入 Capture Skill，也禁止把任一领域逻辑迁入 AirBridge Core。

## 4. 与 AirBridge 解耦的具体含义

“与 AirBridge 解耦”是指领域实现、安装包、生命周期和版本演进不依赖 AirBridge 内部实现；**不等于 Skill 在没有任何远端传输服务时仍能完成远端操作**。

必须满足：

1. Skill 不得直接 import AirBridge 仓库内部模块或依赖其内部类、私有函数、数据库表和源码目录。
2. Skill 不得要求被复制进 AirBridge.exe、PyInstaller bundle 或 AirBridge Core 的 Python 运行环境。
3. Skill 不得依赖 AirBridge Core 的内部进程状态、内部消息帧、串口实现或私有状态文件。
4. Skill 只依赖明确公开、版本化的通用 Transport Interface；AirBridge Provider 负责把该接口映射到 AirBridge 当前公开的 HTTP / Shell / RFS / 文件传输能力。
5. Skill 的领域单元测试必须能够使用 Mock / Fake Transport Provider 执行，不需要启动 AirBridge.exe、连接串口或安装 Cadence 软件。
6. Skill 的集成测试可以选择 AirBridge Provider，但必须将传输层测试和专业软件领域测试分层报告。
7. 将来替换 AirBridge、调整 ABFP、修改串口实现或更换 Agent Host 时，不应要求重写 Capture / Allegro 的领域工作流；最多调整相应 Provider 或 Host Adapter。
8. AirBridge 不可用时，Skill 应报告传输不可用或依赖缺失，不得偷偷绕过认证、权限、审计或安全策略。

## 5. 通用 Transport Interface 的设计要求

Transport Interface 是 Skill 与远端执行环境之间的稳定边界。它只表达通用远程能力，不表达 Capture / Allegro 业务动作。

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
- Capture / Allegro 专用保存、关闭、重启、实例复用或工程状态操作；
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

允许 Capture Skill 与 Allegro Skill 共享基础设施，但共享内容必须是领域无关的，例如：

- Transport Interface 的数据类型和协议客户端；
- 请求 ID、超时、重试分类和结果封装工具；
- 通用文件分块、摘要校验和文件清单工具；
- 通用配置读取、日志、诊断和错误分类；
- 通用权限声明格式和策略校验客户端。

共享库必须：

1. 独立于 AirBridge Core 的内部实现；
2. 不包含 Capture / Allegro 专用分支；
3. 有独立版本和兼容性策略；
4. 可独立测试和发布；
5. 由 Skill 通过明确依赖声明引用；
6. 避免把共享库变成包含所有 EDA 领域代码的“大一统 Skill Runtime”。

共享库可以与 AirBridge 仓库同仓管理，也可以独立仓库发布；仓库位置不改变其逻辑独立性。不得仅为了代码复用而让两个 Skill 在安装时互相依赖。

## 7. Agent Host 兼容原则

- Antigravity、OpenCode 和其他兼容 Agent Host 是 Skill 的宿主，不是 Skill 领域逻辑的所有者。
- 每个 Skill 应提供 Host 可发现的标准入口和必要元数据。
- Host 差异应收敛在安装布局、元数据、工具调用或生命周期的薄适配层。
- Capture / Allegro 领域逻辑不得分别维护 Antigravity 版和 OpenCode 版。
- 不得把某个 Agent Host 的私有 Token 注入方式、内部工具对象或私有 API 作为 Skill 的唯一认证入口。
- 若某项 Host 能力无法跨 Host 使用，应通过能力检测和显式适配处理，并清楚声明限制；不得静默降级到不安全路径。
- Skill 必须能在没有另一款 Agent Host 安装的情况下单独安装和维护。

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

必须分别报告 Skill 包安装 / 加载结果、Skill 单元测试结果、Transport Contract 测试结果、Provider 集成结果、真实 Capture / Allegro 版本和主机环境验收结果。

任何一层 PASS 不得替代其他层 PASS。特别是 Skill 被 Antigravity / OpenCode 发现并加载，不代表它已经取得远端权限、成功调用 AirBridge 或执行过 Capture / Allegro 操作。

## 10. 后续架构与实施顺序

后续实施按以下顺序推进，避免先为当前 Capture Skill 的局部需求冻结错误接口：

1. **盘点现状：** 明确当前 Skill 的领域代码、AirBridge 调用点、认证来源、权限需求和 Host 特有依赖；区分已实现与目标设计。
2. **定义通用 Transport Contract：** 规定版本、能力协商、请求 / 结果、错误、超时、UNKNOWN、文件传输和认证策略；不加入 EDA 业务 API。
3. **定义 Provider 边界：** 设计 AirBridge Provider 与 Fake / Mock Provider；先验证契约，再逐步迁移现有 Skill。
4. **定义 Skill 包规范：** 明确独立目录、元数据、依赖、版本、测试、发布、安装和升级策略。
5. **整理通用共享库：** 只提取领域无关代码；避免 Capture 与 Allegro 互相依赖。
6. **完善 Capture Skill：** 以独立包方式逐步完善，不因其当前功能不完整而把领域能力下沉到 Core。
7. **设计并实现 Allegro Skill：** 独立设计、独立包、独立测试，不复制 Capture 的领域实现。
8. **完善权限模型：** 结合两个 Skill 的实际操作矩阵，设计可扩展、最小权限的授权方式。
9. **分层验收和迁移：** 保留现有兼容路径，逐项迁移并验证，不进行未经评估的大规模重构。

在 Transport Contract 和包规范评审通过前，不应为了当前 Skill 方便而把某一组 Shell / RFS 调用方式永久固化成唯一抽象，也不应贸然创建大量 Capture / Allegro 专用 AirBridge API。

## 11. 新增功能和代码评审的强制检查项

今后任何涉及 AirBridge、Capture Skill、Allegro Skill、共享库或 Agent Host 集成的 PR，都必须回答：

1. 这段代码属于 Core、Transport Provider、共享库、Host Adapter，还是某个领域 Skill？
2. 是否引入 Capture / Allegro 专属语义到 AirBridge Core 或通用 Transport Contract？
3. Capture Skill 和 Allegro Skill 是否仍可独立安装、测试、升级和发布？
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
- **现有 Capture Skill**：是当前迁移对象和功能基线，不是未来所有 EDA Skills 的模板上限。其目录和调用方式可以逐步重构，不应被视为已满足本 ADR。
- **未来 Allegro Skill**：必须按独立 Skill 设计，不得作为 Capture Skill 的子模块或依赖 Capture Skill 才能安装的扩展。
- **历史文档和兼容代码**：继续保留历史事实和测试证据；与本 ADR 冲突的旧目标设计不再作为新实现依据。

---

## 13. 最终原则

> **AirBridge 负责通用、安全、可靠的远程通信；Transport Provider 负责把通用传输契约映射到具体通信实现；Capture Skill 负责 Capture；Allegro Skill 负责 Allegro。**
>
> **Capture Skill 和 Allegro Skill 是独立产品化的 Agent Skills：可分别安装到 Antigravity / OpenCode，可分别开发、测试、升级和发布；它们不依赖 AirBridge 内部实现，也不依赖彼此。**
>
> **任何未来功能都必须遵守领域隔离、接口稳定、最小权限和分层验收原则。**
