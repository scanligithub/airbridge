# Transport Contract v1 设计草案

- 状态：Draft，待架构评审；不是已批准的接口规范
- 版本目标：v1
- 适用范围：所有独立 EDA Product Skills 及未来其他需要通用远程通信的 Skills
- 关联决策：ADR-014
- 本文性质：目标契约与迁移建议，不代表当前 AirBridge 已实现本文全部能力

## 1. 目标与非目标

### 1.1 目标

定义与 EDA 产品、Agent Host、具体传输协议无关的通用远程通信契约，使 Skill 可以：

1. 在不导入 AirBridge Core 模块的情况下执行通用远端操作。
2. 在 AirBridge 与 Fake/Mock Provider 之间替换实现，而不改变领域逻辑。
3. 通过能力声明判断 Provider 能做什么、不能做什么以及保证到什么程度。
4. 对请求关联、重试、去重、超时和 UNKNOWN 使用明确且可测试的语义。
5. 让不同 Agent Host 通过独立 Adapter 接入同一 Skill 实现，而不产生 Host 专属领域逻辑。

### 1.2 非目标

本契约不定义：

- Capture、OrCAD PCB Editor、Allegro、Altium Designer 或其他 EDA 产品的业务 API。
- EDA 软件的启动、关闭、保存、设计加载、Tcl、SKILL 或项目生命周期。
- Agent 的规划、提示词、工作流或 Host UI。
- AirBridge 内部 ABFP 帧格式、串口实现、HTTP 路由或内部类。
- 任何绕过服务端认证、授权、Scope、审计或限流的机制。

Skill 领域操作必须留在对应独立 Skill 内。AirBridge 只是 Provider 之一，不是 Skill 的内部依赖。

## 2. 架构与依赖方向

推荐依赖方向：

```text
EDA Product Skill
       |
       v
Transport Contract (versioned, EDA-neutral)
       |
       +----------------------+--------------------+
       |                      |                    |
       v                      v                    v
AirBridge Provider      Fake/Mock Provider    Future Provider
       |
       v
AirBridge API / remote transport
```

约束：

- Skill 只依赖契约定义的接口、数据类型和错误语义。
- Provider 负责把契约映射到具体后端。
- Fake/Mock Provider 实现同一契约，不要求连接 AirBridge 或安装 EDA 软件。
- Host Adapter 只处理 Skill 包布局、入口注册、工具调用和 Host 生命周期差异，不包含 EDA 领域逻辑。
- Provider 不得把后端专有字段泄漏到 Skill 的领域模型；确需透传时放入明确标记为 opaque 的诊断扩展字段。
- 不同 EDA Product Skills 不得互相依赖。

### 2.1 Host Adapter 与 Provider 的职责边界

Host Adapter 和 Transport Provider 是两个不同的适配层，不应合并为一个“Host 专用 Provider”：

| 组件 | 负责 | 不负责 |
|---|---|---|
| Skill | 产品领域工作流、用户意图校验、操作前置条件和业务后置验证 | Agent Host API、凭据读取、底层 HTTP/串口协议 |
| Host Adapter | Skill 包发现/加载、入口注册、Host 工具调用协议适配、Host 生命周期、Provider 工厂装配、注入可信调用主体上下文 | EDA 业务逻辑、决定服务端 Scope、读取并向模型暴露秘密 |
| Transport Provider | 实现本契约，将通用操作映射到 AirBridge 或其他传输后端；执行能力门控、请求关联和错误映射 | Agent Host 专有工具 schema、EDA 产品生命周期 |
| Credential Source | 按受信 Provider/audience/purpose 和调用主体上下文提供受控凭据绑定 | 选择 EDA 操作、授予服务端权限、修改 Token Scope |
| Authenticated Transport | 将不透明凭据绑定安全地应用于目标出站请求 | 向 Skill/Agent 返回凭据材料 |

约束：

- 同一个 Skill 包不得因 Antigravity、OpenCode 或其他 Host 而复制 EDA 领域实现；Host 差异应留在独立 Adapter。
- Host Adapter 可以提供 Provider 工厂和 Credential Source 装配，但不得把 Host 的私有对象或工具 schema 泄漏到 Skill 的领域 API。
- 若某 Host 无法安全提供可信主体上下文或凭据绑定能力，Adapter 必须明确报告不支持所需认证模式；不得退化为要求 Agent 把 Token 写入 Prompt、工具参数或 Skill 配置。
- Fake Provider 可与 Fake Host Adapter 配合做 Skill 单元测试；Host Compatibility 测试则单独验证真实 Host 的加载、调用和生命周期。Fake Host 通过不代表真实 Host 兼容。

### 2.2 Provider Runtime、Provider Session 与资源所有权（提案）

必须区分底层传输资源和面向单个 Skill 调用上下文的逻辑 Provider Session。串口、HTTP 连接池、IPC 通道等物理资源可以由 Provider Runtime 复用；Contract 协商状态、Profile 版本、能力快照、调用主体和请求上下文不得因此在不同 Skill Session 之间共享可变状态。

建议采用以下所有权模型：

| 对象 | 生命周期/所有权 | 可否跨 Skill 共享 |
|---|---|---|
| Provider Runtime / connection pool | 由 Host Adapter 注入的 Provider Factory 或其 Runtime Manager 创建和管理；随 Host/Adapter 生命周期或显式资源策略关闭 | 可以，但只能共享底层传输资源，不能共享调用主体状态 |
| Provider Session | 每个 Skill 实例或独立调用上下文单独创建；持有自己的 Contract/Profile 协商结果、Capability Snapshot、请求上下文和凭据绑定策略 | 不可以跨 Skill/主体共享 |
| Credential Binding | 绑定特定 Provider、audience、purpose 和可信调用主体；按请求/短生命周期管理 | 不可跨主体复用；不得因连接池共享而共享凭据 |
| Remote Request | 由原调用主体和 client_request_id 标识；请求所有权由 Provider/服务端保证 | 不可跨主体查询或复用 |

依赖和生命周期关系：

```text
Host Adapter / Provider Factory (owns Runtime)
  ├── Provider Runtime (optional shared transport pool)
  │     ├── Provider Session A -> Skill A
  │     └── Provider Session B -> Skill B
  └── Credential Source (trusted caller context)
        ├── binding for Session A / principal A
        └── binding for Session B / principal B
```

约束：

- Host Adapter/Provider Factory 负责创建、登记和回收 Provider Runtime 与独立 Provider Session；Skill 只持有自己的 Session 接口，不直接管理共享 Runtime。
- Provider Session 必须隔离协商版本、Profile 版本、能力快照、请求映射缓存、调用主体上下文和诊断上下文。不得使用单个可变全局 `negotiated_version` 或全局 capability cache 服务多个 Skill Session。
- Provider 可以在 Runtime 内复用物理连接，但必须按请求安全隔离身份、凭据、授权上下文和响应关联。若底层连接无法可靠隔离不同主体的认证上下文，则不得跨主体复用该连接。
- 一个 Session 的 `close()` 只关闭该 Session 并释放其独占资源；不得关闭仍被其他 Session 使用的共享 Runtime/连接池，也不得取消或删除远端请求记录。
- Runtime 的最终关闭由其创建者/所有者（Provider Factory/Host Adapter）执行；关闭前必须停止新建 Session，并按声明的策略等待、拒绝或终止本地 Session。Runtime shutdown 不是 Skill 可调用的 Contract 操作。
- Host Adapter 卸载某个 Skill 时只关闭该 Skill 的 Session。只有 Host/Adapter 生命周期结束或 Runtime Manager 确认没有活动 Session 且策略允许时，才关闭共享 Runtime。
- Session/Runtime 的本地关闭不代表远端请求已取消、失败或未执行；仍未确定的请求必须保留 UNKNOWN / SUBMISSION_UNKNOWN 语义，并可在后续获授权的 Session 中按原主体和原 request ID 查询。
- 不同主体不得通过共享 Runtime、连接池或缓存推断彼此请求是否存在；请求查询仍由服务端按已认证主体执行所有权检查。

该所有权模型是逻辑约束，不要求 Provider 一定实现共享连接池。最简单且合规的实现可以为每个 Session 创建独立 Runtime/连接；优化复用只能在不削弱隔离的前提下进行。

### 2.3 Provider Factory 与 Session 装配接口（提案，Host Adapter 层）

Provider Factory 是 Host Adapter 的装配基础设施，不属于 Transport Contract Core，也不是 Provider 必须向 Skill 暴露的远端操作接口。其职责是依据受信配置创建 Provider Session，并管理 Runtime 资源所有权；它不决定 EDA 操作、不授予服务端 Scope，也不代替 Skill 执行业务前置条件。

建议逻辑接口（非语言绑定、非冻结 API）：

```text
SessionRequest {
  provider_alias: TrustedProviderAlias
  skill_id: string
  skill_version: string
  accepted_contract_versions: string[]
  required_profiles: ProfileRequirement[]
  optional_profiles: ProfileRequirement[]
  // Host Adapter 注入的可信元数据；不得来自 Agent/模型可控 input。
  caller_context: OpaquePrincipalContext
}

ProviderFactory.create_session(request: SessionRequest)
  -> ProviderSession | ProviderFactoryError

ProviderFactory.shutdown() -> None
```

字段和边界：

- `provider_alias` 是 Host Adapter/部署配置中预先登记的逻辑别名；Agent/Skill 不得通过自由文本提供任意 endpoint、串口端口、凭据位置或认证目标。别名解析到 Provider 类型、目标地址、audience 和连接策略等受信配置。
- `skill_id`、`skill_version`、Contract 版本集合和 Profile requirements 来自已安装 Skill 的受信发布元数据，不接受模型在单次工具调用中覆盖。
- `caller_context` 是 Host Adapter 从可信认证上下文注入的不透明主体信息；不得由 Skill 参数或模型文本构造。缺少可信主体上下文时，Factory 必须拒绝创建需要主体绑定的 Session。
- Factory 不接收 Token、密码或 Authorization Header，也不返回凭据材料。Credential Source 由 Host Adapter 注入，并按第 10.1.1 节独立完成凭据绑定。
- Factory 只负责创建 Provider Session 和关联 Runtime 资源；Contract/Profile 协商、能力快照检查由 Session bootstrap 流程完成。Factory 返回对象不代表协商成功，也不代表 Skill 已具备操作资格。
- Skill bootstrap 必须先执行 Core Contract 协商，再协商必需 Profile，读取能力快照并检查全部 required_capabilities；任一步失败均不得向 Skill 注册可执行入口或提交副作用操作。
- Factory 不得扩大 Skill 声明的权限或能力要求；不得因 Provider 支持更多 Profile/Capability 而自动向 Skill 暴露额外操作。

**Provider 选择与装配：**

1. Host Adapter 从受信安装清单加载 Skill，并验证 Skill 包身份/版本及其 Manifest。
2. Host Adapter 根据已批准的 Skill-to-Provider 配置选择 `provider_alias`；默认 Provider、回退 Provider 和目标端点必须是显式配置，不得静默回退到其他身份或权限更宽的 Provider。
3. Factory 解析别名并取得或创建 Provider Runtime。Runtime 可以按安全边界复用；连接池复用不得复用 Session 的调用主体、凭据绑定或请求状态。
4. Factory 为本次 Skill 实例/调用主体创建新的 Provider Session，并注入不可伪造的 caller context 和获准的 Credential Source 引用。
5. Skill bootstrap 在该 Session 上完成 Contract/Profile/Capability 门控；全部通过后才将 Skill 入口标记为 ready。

**初始化失败与资源回收：**

- Session 创建应具有原子可见性：未完成创建和 bootstrap 的 Session 不得被 Skill 作为 ready 使用。
- Factory 创建 Runtime、Provider 或 Session 的任一阶段失败，必须回收本次创建的独占资源；若 Runtime 是共享资源，只能释放本次 acquire 的引用/租约，不得关闭其他 Session 正在使用的 Runtime。
- Session bootstrap 在协商、Profile 匹配或能力门控阶段失败时，调用该 Session 的 `close()`，撤销本地注册并释放独占资源；不得尝试通过降级 Profile 或更宽权限 Provider 自动恢复。
- Runtime acquire/release 应有明确所有权/引用计数或等效租约机制。最后一个 Session 释放后是否立即关闭 Runtime，由 Host Adapter 的明确资源策略决定；不能仅凭缓存超时推断没有活动使用者。
- `ProviderFactory.shutdown()` 是 Host Adapter 内部生命周期操作：停止接收新建请求、拒绝新的 Session、按策略等待或关闭本地 Session，再释放其拥有的 Runtime。它不是 Provider Session 的 `close()`，也不构成远端请求取消。
- Host Adapter 崩溃或进程被强制终止时，不能保证应用层 cleanup 一定执行。实现应依赖 OS 进程资源回收和 Provider/远端请求的持久状态语义；遗留请求仍按 UNKNOWN 规则恢复，不能把本地进程退出解释为远端失败。

**Skill 卸载、重载与并发：**

- 卸载 Skill 实例只关闭该实例拥有的 Session，并解除该实例的入口注册；不得影响其他 Skill 实例或主体。
- 重载 Skill 应先使旧实例停止接收新调用，再按策略等待已进入的本地调用，关闭旧 Session，创建新 Session 并重新协商；不得把旧 Session 的可变协商状态复制到新 Session。
- 并发创建同一 Skill 的多个实例时，每个实例仍有独立 Session；Factory 可复用 Runtime，但不得复用可变 Session 对象。
- 若同一 Skill 实例处理多个不同调用主体，必须为不同主体建立独立 Session 或提供等效且经 Conformance 验证的强隔离；不得把首个调用者的主体上下文缓存为整个 Skill 实例的身份。
- Provider 切换必须显式配置并重新创建 Session；不能把一个 Provider 的请求 ID、能力快照或凭据绑定直接迁移给另一个 Provider。

Factory 的具体类名、依赖注入容器、线程模型、连接池实现和 Host 生命周期 Hook 留给 Host Adapter 实现；Transport Contract 只要求最终交付的 Provider Session 遵守 Core/Profile 语义。

### 2.4 Provider Session 生命周期状态机（提案）

为避免 Factory 已返回 Session 就被误认为可以立即执行远端操作，建议明确 Session 的本地生命周期状态。该状态机描述本地对象是否允许接收调用，不替代远端 RequestState（ACCEPTED/RUNNING/UNKNOWN 等）。

```text
NEW -> NEGOTIATING -> READY -> CLOSING -> CLOSED
  \          \
   \          -> FAILED -> CLOSING -> CLOSED
    -> CLOSING -> CLOSED
```

| Session 状态 | 含义 | 允许的行为 |
|---|---|---|
| NEW | Factory 已创建 Session，但尚未完成协商 | 仅允许 Contract discovery / bootstrap 所需接口；禁止 Shell/Filesystem 等操作提交 |
| NEGOTIATING | 正在协商 Core Contract、Profiles 并验证能力快照 | 仅允许 bootstrap 流程；禁止业务操作提交 |
| READY | Core/Profile/Capability 门控全部通过 | 可调用已协商 Profile 中且能力/条件/授权均满足的操作 |
| FAILED | 初始化失败或 Session 已无法安全继续 | 不允许新的业务操作；只允许清理/关闭 |
| CLOSING | 正在关闭，已停止接收新操作 | 仅允许已进入调用的本地收尾和清理 |
| CLOSED | 本地 Session 资源已关闭 | 所有接口调用均返回明确的生命周期错误；不得自动重建 |

状态转换和并发规则：

1. Factory 返回的 Session 初始状态必须是 NEW；不得在未完成 bootstrap 时向 Skill 暴露为 READY。
2. 只有 Core Contract 协商、全部 required_profiles 协商、能力快照验证和 Skill 必需能力检查全部成功，才可从 NEGOTIATING 转为 READY。可选 Profile 缺失只能禁用对应可选功能，不得放宽 required_profile/required_capability。
3. bootstrap 任一步失败，Session 转为 FAILED，停止业务操作，并执行本地清理；不得自动切换到未配置 Provider、降低权限或静默选择其他版本。
4. `close()` 对同一 Session 必须幂等；并发 close 调用只能导致一次资源释放。关闭开始后不得接收新的业务操作。
5. 从 NEW、NEGOTIATING、READY 或 FAILED 均可进入 CLOSING；完成本地清理后进入 CLOSED。CLOSED 是终态，不允许 reopen；需要恢复时必须由 Factory 创建新 Session 并重新协商。
6. 若 close 与业务操作并发，Provider 必须定义本地调用的 drain 策略（等待、有限等待后返回明确本地错误，或拒绝未开始调用）。无论采用哪种策略，都不得把本地中断解释为远端取消或失败；请求可能已提交时仍按 SUBMISSION_UNKNOWN/UNKNOWN 处理。
7. close 不得悄悄重放、取消或删除远端请求。调用方必须保留尚未确定请求的原始 client_request_id，以便后续获授权的 Session 查询。
8. 连接短暂中断但 Session 仍可安全恢复时，Session 可保持 READY 但副作用调用必须按连接/能力快照有效性门控；若 Provider 身份、Contract/Profile 版本或安全主体上下文变化，则当前 Session 不得继续 READY，必须 FAILED/CLOSED 并由 Factory 创建新 Session。
9. 生命周期状态是本地控制状态，不得通过远端 RequestStatus 伪造，也不得把 Session CLOSED 推导为任何远端请求的终态。

### 2.5 多 Agent Host 兼容、隔离与复用边界（提案）

同一个 EDA Product Skill 应保持一份与 Agent Host 无关的领域实现；每个 Agent Host 通过独立 Host Adapter 接入。Host Adapter 负责把宿主的 Skill 加载、工具注册/调用、生命周期和可信主体上下文映射到共同的 Skill/Provider 装配边界。不得因 Host 不同而复制 Capture、Allegro 或其他 EDA 领域逻辑。

**兼容关系分层：**

| 组合关系 | 兼容责任 | 验证内容 |
|---|---|---|
| Skill ↔ Transport Contract | Skill 声明接受的 Core Contract 和必需 Profile/Capability | Contract/Profile 版本协商、公共 Conformance 行为 |
| Skill ↔ Host Adapter | Skill 声明所需的抽象 Host 功能；Adapter 声明其实现的 Host 功能和已验证 Skill API 范围 | Skill 发现/加载、入口注册、输入输出映射、错误传播、取消/关闭语义、主体上下文注入 |
| Host Adapter ↔ Agent Host | Adapter 声明支持的 Host 产品/版本范围 | 真实 Host 中的安装、加载、工具调用、并发、重载和卸载行为 |
| Host Adapter ↔ Provider Factory/Runtime | Adapter 装配受信 Provider 配置、Credential Source 和 Runtime 生命周期 | Provider 选择、资源所有权、身份隔离、失败回收 |
| Provider ↔ Backend | Provider 声明其实现的 Contract/Profile/Capability 及后端范围 | 公共 Conformance、Provider 集成和真实远端运行时验收 |

某个 Skill 与 Contract 兼容，不代表它自动兼容所有 Agent Host；某个 Adapter 能加载 Skill，也不代表其 Provider 或远端后端通过验收。兼容矩阵必须分别记录上述关系，不能用单一 `compatible=true` 概括。

**Host Adapter 的抽象能力声明：**

建议 Host Adapter 声明机器可读的 `host_features`，而不是让 Skill 检查 `host_id == opencode` 或 `host_id == antigravity` 后分支执行领域逻辑。初始可评估的功能标识包括：

- `skill.package.discover`：发现并验证 Skill 包及其 Manifest。
- `skill.entry.register`：注册 Skill 入口/工具定义。
- `skill.invocation.structured_input`：传递结构化输入并校验参数。
- `skill.invocation.structured_output`：返回结构化结果和稳定错误。
- `skill.lifecycle.unload`：在卸载时停止新调用并执行本地清理。
- `skill.lifecycle.reload`：支持受控重载或明确声明不支持。
- `identity.principal_context`：提供可信且不可由模型自由文本伪造的调用主体上下文。
- `credential.binding`：能够装配符合 Credential Source 边界的受控凭据绑定；不意味着 Host 本身可以把秘密暴露给 Skill。
- `invocation.concurrent_isolation`：并发调用间具备所需的上下文隔离。

以上是建议的抽象功能名，最终是否全部进入 v1 需评审。Adapter 必须声明 `supported`、`unsupported` 或 `conditional`，并记录适用 Host 版本、条件和限制。Skill 只声明真正需要的抽象功能；缺少必需功能时，在注册可执行入口或执行副作用操作前 fail closed。

**Manifest 示例（非冻结格式）：**

```yaml
host_adapter:
  id: opencode-adapter
  version: 0.1.0
  host:
    id: opencode
    tested_versions: ["<明确测试过的版本范围>"]
  implements_skill_api_versions: ["1.0.0"]
  host_features:
    - name: skill.package.discover
      support: supported
    - name: skill.entry.register
      support: supported
    - name: identity.principal_context
      support: conditional
      conditions: ["Host 提供已认证调用主体上下文"]
    - name: credential.binding
      support: conditional
      conditions: ["配置了受信 Credential Source"]
```

```yaml
skill:
  id: orcad.capture
  version: 0.4.0
  host_requirements:
    required_features:
      - skill.package.discover
      - skill.entry.register
      - invocation.structured_input
      - invocation.structured_output
      - identity.principal_context
      - credential.binding
    optional_features:
      - skill.lifecycle.reload
```

示例中的版本占位符不是可直接发布的兼容声明；实际 Manifest 必须列出明确验证过的 Host 版本范围和 Skill API 版本，不得使用 `latest` 或未验证的宽泛范围。`tested_host_adapters` 可作为发布测试记录，但不应取代基于 `host_features` 的运行时能力检查。

**Runtime 与 Credential Source 的跨 Host 复用：**

1. 不同 Agent Host 通常运行在不同进程或安全边界中；不得假设它们可以共享同一个进程内 Provider Runtime、Provider Session、Credential Source 实例或可变缓存。
2. 不同 Host Adapter 可以各自连接同一个逻辑远端 Provider，但必须分别创建 Session、分别协商、分别绑定可信主体和凭据。共享同一远端地址不代表共享认证身份或请求所有权。
3. 只有在明确存在独立、受信、具备主体隔离的 Runtime Manager 服务时，才可讨论跨进程复用底层连接；该服务本身必须成为独立安全边界并通过相应隔离测试。v1 不要求实现此机制。
4. 同一 Host Adapter 进程内是否允许多个 Skill 共享 Runtime，仍遵守第 2.2 节规则：只能共享底层资源，不能共享 Session、主体上下文、凭据绑定或请求状态。
5. Credential Source 的实现可以因 Host 而异（例如由宿主提供受控凭据集成，或使用 OS/组织凭据服务），但必须满足相同的 Credential Source 最小语义；不得把 Host 私有凭据 API 暴露给 Skill。
6. 若 Host 无法提供可信主体上下文或安全凭据绑定，Adapter 必须明确不满足对应必需功能；不得通过把 Token 放入 Prompt、工具参数、Skill Manifest 或普通环境变量回退来“兼容”。

**兼容性测试矩阵：**

- Skill Unit Tests：同一 Skill 领域实现使用 Fake Provider/Fake Host Adapter 测试，不依赖真实 Host。
- Host Adapter Contract Tests：用标准化 Host Adapter 测试夹具验证发现、注册、结构化输入输出、生命周期、错误映射和主体上下文边界。
- Host Compatibility Tests：在每个真实 Host 及明确版本上验证安装、加载、调用、并发、重载和卸载；记录 Host/Adapter/Skill 版本。
- Security Compatibility Tests：验证主体上下文不可伪造、凭据不进入模型上下文、不同 Host/主体之间无 Session 或凭据串用。
- End-to-End Compatibility：按 Skill × Host Adapter × Agent Host 版本 × Provider × Contract/Profile 组合记录结果；只对实际执行过的组合标记 PASS。

Fake Host Adapter 通过只证明抽象适配契约测试通过，不代表 Antigravity、OpenCode 或其他真实 Host 已通过兼容验收。新增 Host 时应新增/验证 Adapter，而不是复制或修改 EDA Skill 的领域实现。

### 2.6 Skill Lifecycle / Invocation Contract 的边界（关联提案，非 Transport Contract 组成部分）

本节仅记录 Skill Lifecycle / Invocation Contract 与 Transport Contract 的集成边界，并给出供后续独立规范评审的关联提案。**本节不属于 Transport Contract v1 的规范定义，不得据此将 Skill API 版本纳入 Transport Contract 版本协商、兼容性声明或发布制品。** Skill Lifecycle / Invocation Contract 必须作为独立版本化规范另行评审、冻结和发布；在该独立规范获批前，本节内容均为非冻结设计建议。Transport Contract 只规定 Skill 如何访问远端 Provider；Skill Lifecycle / Invocation Contract 规定 Host Adapter 如何发现、初始化、调用和关闭 Skill。两者通过明确的装配边界协作，不共享隐式版本号或发布门槛。Skill 不得依赖 Host 专有对象、工具 schema 或 Provider 私有 API。

**最小逻辑接口：**

```text
SkillDescriptor {
  skill_id: string
  skill_version: string
  manifest_version: string
  supported_skill_api_versions: string[] // 独立 Skill API 版本，不是 Transport Contract 版本
}

Skill.initialize(context: SkillInitContext)
  -> SkillInitResult | SkillLifecycleError

Skill.invoke(request: SkillInvocation)
  -> SkillInvocationResult | SkillInvocationError

Skill.shutdown(context: SkillShutdownContext)
  -> SkillShutdownResult
```

以上是语言无关的逻辑接口，不要求 Python/TypeScript 等语言使用相同函数签名，也不规定 Host 必须采用进程内函数调用、RPC 或其他具体机制。Host Adapter 负责将宿主调用映射到该逻辑接口，并保留错误、超时和取消语义。

**SkillInitContext（由 Host Adapter 受信注入）：**

- `invocation_context` / `caller_context`：不透明且不可由模型参数伪造的调用主体上下文；Skill 只能按明确授权使用，不得自行提升身份。
- `provider_sessions`：由 Host Adapter/Provider Factory 按受信 Manifest 与 Skill-to-Provider 配置预先创建的 Provider Session 接口集合；只包含该 Skill 获准使用的 Session。Skill 不接收 Provider Factory、Runtime Manager、provider_alias 解析器或凭据来源选择能力，也不得自行创建/选择 Provider。
- `host_services`：仅包含 Skill Manifest 明确声明并经 Adapter feature negotiation 满足的抽象服务；不得直接暴露 Host 私有对象。
- `configuration`：经验证的非秘密 Skill 配置；秘密材料必须通过 Credential Source/受控绑定路径取得，不得作为普通配置值传入。
- `contract_context`：分别记录已验证的 Skill API 版本，以及 Transport Contract/Profile 协商结果和能力快照引用/摘要；两类版本独立协商、独立记录。实际操作仍须遵守能力快照有效期和提交前门控。

初始化规则：

1. `initialize()` 只执行本地初始化、验证配置，并在 Host Adapter/Provider Factory 预先装配的 Provider Session 上完成 Contract/Profile/Capability bootstrap；Skill 不创建 Session、不选择 Provider，也不得访问 Factory/Runtime Manager。不得在初始化阶段自动执行有副作用的 EDA 操作。
2. Skill 只有在初始化成功且所有必需依赖满足后才进入 READY；初始化失败必须返回稳定、可诊断且已脱敏的错误，并释放本次创建的 Session/资源。
3. `initialize()` 对同一实例不得并发执行；重复初始化必须明确返回 ALREADY_INITIALIZED/INVALID_LIFECYCLE，或按实现定义的幂等语义处理，不得重复创建不可追踪资源。
4. Skill 不得自行选择未配置 Provider、改变 Scope、从环境变量探测凭据或把缺失的必需能力降级为可选。

**SkillInvocation（每次调用独立）：**

```text
SkillInvocation {
  invocation_id: string
  operation: string
  input: StructuredValue
  deadline?: Timestamp
  cancellation: CancellationSignal
  // 由 Host Adapter 注入的可信调用元数据；不是模型可控 input。
  caller_context: OpaquePrincipalContext
}

SkillInvocationResult {
  invocation_id: string
  outcome: SUCCEEDED | FAILED | INCOMPLETE
  output?: StructuredValue
  error?: SkillError
  diagnostics?: RedactedDiagnostics
}
```

- `invocation_id` 用于 Host ↔ Skill 单次调用关联；它不替代 Transport `client_request_id`。一次 Skill 调用可能产生多个远端操作，每个逻辑远端操作必须自行生成并持久化稳定的 `client_request_id`。
- `operation` 必须匹配 Skill Manifest 中声明的入口；不允许通过任意自由文本构造 Python/Tcl/SKILL 代码执行入口，除非该 Skill 明确将此类功能定义为受控业务能力并实施输入校验/授权。
- `input` 和 `output` 必须为结构化、可版本化数据；Host 私有对象、凭据句柄、任意可执行对象和未声明的隐式全局状态不得穿透边界。
- `caller_context` 是调用信封中的可信元数据字段，由 Host Adapter 在模型输入解析后注入并绑定本次调用；它不是 Agent/模型可填写的业务参数，也不得从 `input`、工具参数或 Skill Manifest 反序列化覆盖。若调用通道无法区分可信元数据与不可信输入，或 Host 不能保证主体上下文，必须在 Adapter feature negotiation 阶段报告不满足并 fail closed。
- `deadline` 是本地调用预算提示，不得被解释为远端操作一定会在该时刻停止；超时发生时，已提交远端请求按 SUBMISSION_UNKNOWN/UNKNOWN 处理。
- `cancellation` 表示调用方希望停止本地等待/后续编排，不自动等于远端取消。除非未来单独定义可授权、可验证的远端 cancellation capability，Skill/Adapter 不得声称已取消远端操作。
- `INCOMPLETE` 用于调用流程已停止但无法给出完整业务结论的情况；若包含可能已提交的远端操作，结果必须引用原 `client_request_id` 并明确 UNKNOWN，不能伪装成普通 FAILED 或未执行。
- `diagnostics` 必须脱敏，不得包含 Token、凭据绑定句柄、Authorization Header、秘密配置或完整敏感命令/文件内容。

**Skill 实例生命周期状态：**

```text
DISCOVERED -> INITIALIZING -> READY -> DRAINING -> CLOSED
                  \
                   -> FAILED -> DRAINING -> CLOSED
```

| 状态 | 含义 | 调用规则 |
|---|---|---|
| DISCOVERED | 包和 Manifest 已验证，尚未初始化 | 不接受业务调用 |
| INITIALIZING | 执行 initialize/bootstrap | 不接受 invoke |
| READY | 初始化成功 | 接受 Manifest 已声明且满足授权/能力要求的调用 |
| DRAINING | 停止接收新调用，处理或终止本地在途调用 | 拒绝新 invoke；按 shutdown 策略处理在途调用 |
| FAILED | 初始化失败或实例无法安全继续 | 不接受业务调用，只允许清理 |
| CLOSED | 本地实例关闭完成 | 终态；重新使用必须创建新实例并重新初始化 |

生命周期与并发规则：

1. 同一 Skill 实例的 `initialize()` 和 `shutdown()` 必须串行化；不得并发初始化或在初始化尚未完成时暴露 READY。
2. `invoke()` 可以并发，但只有 Skill Manifest 和 Host Adapter 都声明支持并发隔离，且实现通过并发契约测试时，Host 才可并发派发。否则 Adapter 必须串行派发，或明确拒绝并发请求。
3. 每次调用的 invocation_id、caller_context、deadline、cancellation、输入/输出和诊断上下文必须隔离；不得使用可变全局变量保存当前调用主体或请求 ID。
4. `shutdown()` 首先进入 DRAINING 并停止新调用，再按已声明策略等待或结束本地在途调用，关闭本实例拥有的 Provider Session 和资源，最后进入 CLOSED。它不得关闭其他 Skill 实例拥有的资源。
5. `shutdown()` 必须幂等；超出 shutdown deadline 时可返回 INCOMPLETE/清理未完成诊断，但不得谎称远端请求已取消或失败。遗留远端请求仍按 UNKNOWN 语义恢复。
6. Host Adapter 崩溃、强制卸载或进程退出时，不能保证 Skill shutdown 一定执行；远端请求恢复依赖持久化的 client_request_id 和 Provider 的查询/去重保证。
7. 对 READY 实例执行版本升级或重载，应创建新实例并重新验证 Manifest、Host features、Contract/Profile/Capability；不得把旧实例的可变状态、Session 或主体上下文复制给新实例。

**稳定错误边界：**

- `SkillLifecycleError`：例如 INVALID_LIFECYCLE、INITIALIZATION_FAILED、DEPENDENCY_UNAVAILABLE、HOST_FEATURE_UNAVAILABLE。
- `SkillInvocationError`：例如 INVALID_INPUT、UNKNOWN_OPERATION、NOT_READY、UNAUTHORIZED、DEADLINE_EXCEEDED、CANCELLED_LOCAL、INTERNAL_ERROR。
- 错误至少包含稳定 `code`、安全的 `message` 和可选脱敏 `details`；Host Adapter 可以映射到宿主错误格式，但不得丢失稳定 code 或把 UNKNOWN 改写为失败/未执行。
- TransportError 和 OperationResult 仍按 Transport Contract 原有边界表达；SkillError 是 Skill/Host 调用边界错误，不能替代或抹去底层远端请求状态。

**Skill Manifest 增补字段（提案，非冻结格式）：**

```yaml
skill:
  id: orcad.capture
  version: 0.4.0
  manifest_version: 1.0.0
  skill_api:
    compatible_versions: ["1.0.0"] # 独立 Skill API 版本范围，与 Transport Contract 版本分开协商
  entrypoints:
    - operation: capture.open_project
      input_schema: schemas/open_project.input.json
      output_schema: schemas/open_project.output.json
      concurrency: isolated
    - operation: capture.get_design_info
      input_schema: schemas/get_design_info.input.json
      output_schema: schemas/get_design_info.output.json
      concurrency: isolated
  lifecycle:
    initialize_required: true
    shutdown_required: true
    graceful_drain: true
```

Manifest 必须固定声明字段和 Schema 版本；示例中的 operation 只是说明结构，不代表本仓库已实现这些入口。Skill Manifest 不得包含 Token、密码、Authorization Header 或凭据文件路径。

### 2.7 Skill Package / Manifest 的完整性、依赖与配置边界（关联提案，非 Transport Contract 组成部分）

本节是 Skill Package / Manifest 规范的关联设计提案，不属于 Transport Contract v1，也不冻结包格式、签名算法或具体 Host Adapter API。Skill Package、Skill Manifest、Skill Lifecycle/Invocation API 应作为相互关联但独立版本化的规范/制品定义；不得把它们的版本合并进 Transport Contract 版本协商。以下规则用于明确安全边界和后续独立规范评审的必答项。

**1. 包身份、完整性与发布者信任**

- 每个可安装包必须具有稳定 `skill_id`、不可混淆的 `skill_version`、`manifest_version` 和包格式版本。包身份不得仅由文件名、目录名或 Host 中的显示名称决定。
- 发布制品必须有可验证的完整性摘要；摘要覆盖 Manifest、入口代码、Schema、资源文件和依赖锁定材料。必须定义规范化、文件排序、路径编码和摘要算法版本，不能让不同实现对同一包计算出不同摘要。
- 正式发布包应有独立于包内容的数字签名/签名信封，签名覆盖规范化 Manifest 与完整文件摘要清单。签名材料不得通过把签名字段本身递归纳入待签名内容形成循环依赖；具体封装格式和算法由独立 Package Trust 规范评审决定。
- 信任根、允许的发布者/签名密钥、撤销信息和允许安装的发布渠道由 Host/部署管理员的受信策略配置；不能由 Skill 包自行声明并信任自己的公钥，也不能由 Agent 在单次调用中选择信任根。
- Host Adapter/安装器必须在加载或执行任何 Skill 代码前验证包摘要、签名、发布者信任和撤销状态。验证失败、缺少必需签名或信任状态无法确定时 fail closed；不得降级为“仅警告后继续”。
- 签名只证明制品与受信发布者/签名密钥的关联及完整性，不自动授予 Skill 运行权限、Transport Scope、文件访问权、EDA 操作授权或 Credential Source 访问权。安装许可、运行许可和服务端授权仍是独立决策。
- Manifest 中声明的版本、能力、依赖和测试矩阵属于发布者声明；Host 必须验证包内结构与策略约束，但不得把这些声明当成服务端授权或运行时 CapabilitySnapshot。

**2. Manifest 依赖声明与依赖图**

- Manifest 可以声明对经批准的通用 Skill API、共享基础库/基础设施包以及必要运行时的依赖；每项依赖必须具有稳定 package ID、明确版本约束、依赖类型和是否必需等信息。
- OrCAD Capture、OrCAD PCB Editor、Allegro、AD 等 EDA Product Skills 之间禁止直接依赖。一个 EDA Product Skill 不得通过可选依赖、插件发现、动态导入或运行时回调隐式依赖另一个 EDA Product Skill。
- 共享库只能承载真正通用的基础设施，不得把某个 EDA 产品的业务逻辑抽到共享库，再由其他 Skill 间接依赖；共享库自身也必须独立版本化和发布。
- 安装/构建时必须解析完整传递依赖图并生成可复现的依赖锁定材料，固定每个依赖的确切版本和制品摘要。运行时不得根据宽泛版本范围临时下载或静默升级依赖。
- 必需依赖缺失、版本不兼容、摘要不匹配、依赖图循环或策略禁止时，Skill 不得进入 READY。可选依赖缺失只能禁用 Manifest 明确关联的可选入口/功能，不能改变必需功能的安全保证。
- 禁止未声明依赖、依赖路径逃逸、从包外任意目录加载代码，以及通过 `latest`、无界范围或未锁定 Git 分支作为可复现发布依赖。

**3. 配置 Schema 与秘密材料隔离**

- Manifest 必须引用有版本的配置 Schema；Host Adapter 在初始化前验证配置类型、必填字段、枚举、范围和未知字段策略。配置 Schema 必须随包发布并纳入完整性摘要，不得在运行时从不受信任的网络地址动态加载。
- 普通配置只承载非秘密参数。Token、密码、私钥、Authorization Header、凭据文件路径和可反推出秘密的内容不得写入 Manifest、普通配置文件、入口参数、日志或 Skill 输出。
- 需要秘密的配置项只能声明逻辑 Credential Reference/需求标识；实际秘密由受信任 Host Adapter / Credential Source 根据部署策略绑定。Skill 不得自行解析凭据路径、搜索环境变量、选择密钥或输出秘密内容。
- Credential Reference 不是凭据本身，也不授予访问权。Host Adapter 必须验证调用主体、Skill 身份、所请求的凭据用途和管理员策略后才可建立受控绑定；缺少可信主体或绑定条件时 fail closed。
- Schema 错误、未知配置字段、秘密字段误入普通配置、Credential Reference 无法解析或权限不满足时，初始化必须失败并提供脱敏诊断。

**4. 入口与包内资源验证**

- 每个入口必须使用 Manifest 中唯一、稳定的 operation ID，并绑定包内输入/输出 Schema。Host Adapter 在注册入口前必须验证引用存在、格式受支持且位于包根目录内。
- Schema、代码和资源引用必须使用包内相对路径；拒绝绝对路径、`..` 路径穿越、符号链接逃逸和解压后覆盖包外文件。验证与加载之间必须避免可被替换的 TOCTOU 窗口，或使用不可变、已验证的安装目录。
- 未在已验证 Manifest 中声明的入口不得注册或被调用；不得因 Host 工具注册便利而暴露通用任意代码执行入口。
- Manifest 解析器和 Schema 验证器本身属于受信安装/Host Adapter 攻击面；必须限制输入大小、嵌套深度、别名/引用扩展和资源消耗，拒绝重复或歧义字段，避免不同解析器对同一 Manifest 产生不同解释。

**5. 版本兼容与升级策略**

- `skill_version`、`manifest_version`、Skill API 版本、Transport Contract 版本、Profile 版本、Host Adapter 版本和共享库版本必须分别声明、分别记录；不得以一个版本字段代替其他组件兼容性。
- Manifest Schema 版本必须精确识别；不认识的 MAJOR 版本必须拒绝。对新增字段采用明确的未知字段策略：安全敏感字段不得静默忽略；普通扩展字段是否允许忽略须由该 Manifest Schema 版本明确规定。
- 兼容范围必须采用可确定解析的版本规则并由锁定的解析器实现；正式发布不能使用 `latest`、空范围或依赖未声明的宽松解析行为。测试过的 Host/Adapter/Provider 版本范围应与声明的理论兼容范围分开记录。
- 升级必须验证新制品的身份、签名、完整性、依赖图、配置 Schema 和兼容矩阵；不得在原地覆盖仍在运行的包文件。应先准备并验证新实例，再按 Host 能力执行入口切换和旧实例 drain/shutdown。
- 若 Host 不支持原子入口切换，升级流程必须明确停机窗口、回滚限制和在途调用处理；不能声称无中断升级。回滚到旧版本也必须重新验证其签名、撤销状态和依赖摘要。
- 防降级策略由受信部署策略明确规定；若组织要求禁止回退到存在已知安全问题的版本，安装器必须验证受信最低版本/撤销策略，而不能只比较 SemVer 大小。

**6. Host Adapter 的验证责任与顺序**

Host Adapter/安装器应按以下顺序完成验证，全部必需步骤成功前不得加载 Skill 代码或注册可执行入口：

1. 从受信安装源取得包，验证包格式、大小限制和安全解包路径。
2. 使用管理员配置的信任根验证发布者签名、签名状态和撤销策略。
3. 验证完整包摘要及文件清单，确保 Manifest、代码、Schema、资源和锁定依赖未被替换。
4. 解析并验证 Manifest Schema 版本、必填字段、入口唯一性、路径约束和未知字段策略。
5. 解析依赖图，拒绝 EDA Product Skill 间接/直接依赖、循环、未声明或未锁定依赖，并验证所有依赖制品摘要。
6. 验证 Skill API、Host Features、Provider Contract/Profile 要求和部署兼容策略；Transport 能力检查仍须在 Provider Session bootstrap 时执行，不能由静态 Manifest 取代。
7. 验证普通配置 Schema，并将秘密需求交由受控 Credential Source 绑定；不得把秘密值传入普通配置对象。
8. 创建隔离的 Skill 实例，完成 initialize/bootstrap；只有进入 READY 后才注册/启用对应入口。
9. 记录可审计但已脱敏的包摘要、发布者身份、版本、依赖锁定摘要、Host Adapter 版本和验证结果。

上述流程中，Host Adapter 负责包和 Manifest 的本地信任/结构验证；Provider 负责 Transport Contract 的远端语义；服务端继续独立执行 Token Scope、主体所有权和资源授权。任何一层的 PASS 都不能替代其他层的验证。

**7. 签名信封与确定性摘要规则（待独立规范冻结）**

- 包文件清单必须使用确定性编码：路径统一分隔符、明确 Unicode 规范化规则、明确大小写冲突策略、按规范化路径字节序排序；拒绝规范化后重名、大小写折叠冲突和重复条目。考虑 Windows Host 的文件系统语义时，不能让同一包在大小写敏感与不敏感文件系统上解析出不同文件集合。
- 包内路径必须相对包根目录，拒绝绝对路径、盘符/UNC 路径、`.`/`..` 逃逸、NUL、歧义编码和符号链接；解包器必须防止硬链接/重解析点等平台特有路径重定向，或明确拒绝无法安全验证的条目类型。
- 文件摘要必须覆盖每个可执行文件、脚本、Manifest、Schema、资源和依赖锁定材料。文件清单本身也必须被签名覆盖；不得只签 Manifest 中一个未绑定具体文件集合的版本字符串。
- 摘要计算必须定义字节级输入、算法标识、输出编码和版本；同一输入必须得到同一摘要。算法淘汰或迁移时通过新算法标识/新包格式版本处理，不得静默改变既有算法含义。
- 签名信封至少绑定 package identity（skill_id、skill_version、package format）、规范化 Manifest 摘要、文件清单摘要、签名算法标识、publisher identity 和 key identifier。签名信封自身不纳入其所签名的包摘要输入，避免递归。
- 验证器必须拒绝未知/不允许的算法、重复或歧义的签名字段、未知关键字段以及算法降级；不得仅凭信封自报的算法和公钥建立信任。
- 多签名可以支持密钥轮换或组织策略，但“任一有效签名即可”还是“达到阈值才有效”必须由受信策略明确决定。包不得自行降低所需签名阈值；v1 可先要求至少一个管理员信任策略明确授权的发布密钥签名，阈值签名作为策略扩展。

**8. 发布者密钥生命周期、轮换与撤销**

- 发布者身份与签名密钥是两个不同对象：一个发布者可在不同时间使用多个密钥；信任策略应绑定 publisher identity、允许的 key identifier、用途、有效期和适用包命名空间。
- 正常密钥轮换应支持明确的过渡期：管理员先通过独立可信渠道登记新密钥，再允许新旧密钥在受限时间内并行签名；过渡结束后撤销旧密钥。不能仅因新包自称“替代密钥”就自动信任新密钥。
- 旧密钥退役与密钥泄露/失陷必须区分。正常退役可以按受信策略保留对历史制品的验证能力；密钥失陷撤销则必须按撤销生效时间、影响范围和组织策略重新评估历史制品，不能把“签名数学上有效”当成当前仍可信。
- 撤销记录必须能表达撤销对象（publisher/key/package/version）、撤销原因/类别、生效时间或可信序列、签发者和完整性保护。具体撤销协议由独立 Package Trust 规范决定。
- 撤销信息更新必须防回滚；验证器应记录已接受的最新可信撤销状态版本/序列。较旧状态不能覆盖较新状态。若无法判断撤销信息是否新鲜，是否允许既有已安装 Skill 继续运行必须由管理员策略明确；新安装/升级默认 fail closed。
- 发布者密钥不得存放在 Skill 包、普通仓库配置或 Agent Prompt 中。发布签名操作应由受控 CI Signing Service、HSM 或组织批准的离线签名流程执行；构建系统只获得完成签名所需的最小权限。
- 密钥轮换/撤销不得修改已发布包的字节内容。若需要重新签名，必须生成新的签名信封或新发布制品身份，并保留旧制品摘要、原签名和撤销判定审计记录。

**9. 离线安装、缓存和撤销状态新鲜度**

- 离线安装必须携带可独立验证的包、签名信封、文件清单，以及由受信渠道发布并受完整性保护的信任/撤销元数据快照；包内自带的元数据不能自行成为信任根。
- 离线验证器必须检查信任元数据版本、有效期/新鲜度、签发者链和防回滚序列。超过策略允许的最大陈旧时间、缺少必需撤销数据或无法验证元数据来源时，新安装/升级必须拒绝。
- 离线策略必须明确区分“可验证签名但无法确认当前未撤销”与“已确认未撤销”。前者不得记录成完整信任 PASS；如组织允许受控应急例外，必须由管理员在带外明确授权、限定包摘要/用途/有效期并产生审计记录，不能由 Agent 或 Skill 自行触发。
- 已安装包的验证结果应绑定包摘要、信任策略版本、撤销元数据版本和验证时间。每次启动是否重新验证撤销状态由部署策略规定；不能无限期缓存一次 PASS 并忽略后续撤销。
- 缓存命中不能跳过包摘要复核；缓存键至少绑定完整包摘要和验证策略版本。信任根或撤销策略更新后，旧缓存必须重新评估。

**10. 构建来源证明与签名的边界**

- 发布者签名回答“哪个受信密钥对这个制品作出签名，以及制品字节是否匹配”；构建来源证明（provenance/attestation）回答“制品由哪个受信构建流程、从哪些源代码和依赖、使用哪些构建参数产生”。两者不能互相替代。
- 如组织要求供应链来源证明，证明材料至少应绑定源代码仓库/提交、构建工作流身份、构建器身份、依赖锁定摘要、构建参数和最终制品摘要；证明材料必须由受信构建系统签发并可独立验证。
- Host/安装器可按部署策略要求来源证明，但不得仅凭仓库 URL、CI 日志链接、分支名或 Manifest 自述认定来源可信。构建证明中引用的提交和依赖也必须与最终制品摘要建立可验证绑定。
- 可复现构建是增强验证手段，不等同于签名或来源证明；无法做到字节级可复现时，必须明确记录限制，不得声称已完成可复现验证。
- 生产发布凭据应与普通 CI 测试权限隔离。来自未受信分支、未批准工作流或外部贡献者上下文的工作流不得自动获得正式发布签名权限。

**11. 安装决策结果与审计边界**

- 安装验证结果至少区分 `VERIFIED_TRUSTED`、`REJECTED` 和 `INDETERMINATE`。`INDETERMINATE` 表示证据不足（例如撤销状态过期或信任服务不可用），不得折叠成 `VERIFIED_TRUSTED`。
- `VERIFIED_TRUSTED` 仅表示制品在给定信任策略和验证时点通过包信任验证；它不代表 Skill 已获准安装、可在当前 Host 运行、具备所需 Host Features、Transport 能力或远端服务端授权。
- 审计记录应包含包摘要、skill_id/version、publisher/key identifier、签名算法标识、信任策略版本、撤销元数据版本/序列、来源证明摘要（如要求）、验证结果和时间。不得记录私钥、Token、秘密配置或不必要的敏感本地路径。
- 验证失败日志应提供稳定错误类别和脱敏诊断；不能通过错误消息泄露信任根私密材料、凭据内容或本地秘密文件位置。

**Manifest 结构示意（非冻结格式）：**

```yaml
skill:
  id: orcad.capture
  version: 0.4.0
  manifest_version: 1.0.0
  skill_api:
    compatible_versions: ["1.0.0"]
  package:
    format_version: 1.0.0
    integrity:
      digest_algorithm: sha256
      file_manifest: META-INF/files.sha256
      signature_envelope: META-INF/skill.sig
  dependencies:
    required:
      - package_id: generic.skill-runtime
        version: "1.2.3"
        artifact_digest: "sha256:<发布制品摘要>"
    optional: []
  configuration:
    schema: schemas/config.schema.json
    schema_version: 1.0.0
    credential_requirements:
      - reference: cadence.session
        purpose: approved-provider-binding
  entrypoints:
    - operation: capture.get_design_info
      input_schema: schemas/get_design_info.input.json
      output_schema: schemas/get_design_info.output.json
      concurrency: isolated
```

该示例仅说明字段职责，不规定摘要清单/签名信封的最终编码格式，也不表示仓库已经存在 Skill Package 安装器、签名验证器或上述依赖包。`artifact_digest`、签名信封、发布者身份和撤销状态的权威来源必须由独立 Package Trust 规范定义；示例中的占位值不是可发布凭据。

## 3. 版本与兼容性

建议使用独立语义版本：

- MAJOR：不兼容的接口、状态或保证语义变化。
- MINOR：新增可选能力、字段或接口，不改变既有保证。
- PATCH：文档澄清、兼容性修正和不改变契约语义的错误修订。

Provider 必须声明其实现的 Contract 版本范围。Skill 初始化时校验版本兼容性；不兼容时在任何远端副作用操作之前失败。

v1 不要求所有 Provider 支持所有可选能力。必需的基础接口与可选能力必须区分；不得通过返回成功、空结果或虚构默认值伪装能力。

### 3.1 独立发布单元

Contract、Provider、Skill 和 Host Adapter 是不同的版本化发布单元，不得共享一个隐式版本号：

| 发布单元 | 版本负责内容 | 不应导致的连带升级 |
|---|---|---|
| Transport Contract | 接口、类型、状态机、错误语义、能力标识和保证 | 不要求 AirBridge Core 或任一 Skill 同步发布 |
| Contract Conformance Suite | 对某个 Contract 版本的可执行一致性测试与测试夹具 | 不与 AirBridge 的普通应用版本绑定 |
| Transport Provider | 具体后端适配、支持的 Contract 版本和能力实现 | 不要求 Skill 领域逻辑随 Provider 内部重构升级 |
| EDA Product Skill | 产品领域工作流及其兼容 Contract 范围 | 不要求其他 EDA Skill 同步升级 |
| Host Adapter | 特定 Agent Host 的发现、入口和生命周期适配 | 不要求修改 Skill 领域代码或 Transport Contract |

每个发布单元必须有自己的版本、变更记录和兼容性声明。某个单元的发布版本不得被解释为其他单元的版本。

### 3.2 契约版本协商与锁定

为避免“版本范围兼容”被不同实现解释成不同规则，建议 v1 使用保守的显式协商：

1. Contract 版本采用 `MAJOR.MINOR.PATCH`。MAJOR 表示不兼容的接口、状态或保证语义变化；MINOR 只增加可选接口、字段或能力且不改变既有保证；PATCH 只修正文档或不改变规范语义的缺陷。
2. Skill 发布元数据声明其实现并经过测试的 Contract 版本集合，以及最低必需能力。不得只写 `latest` 或不受约束的 `1.x`。
3. Provider 声明其实际实现并通过对应 Contract Conformance Suite 的 Contract 版本集合。不得仅根据依赖包版本或源码分支推断支持范围。
4. 初始化时双方按完整版本号求交集，并选择双方均明确声明支持的最高版本。若没有共同版本，必须在任何远端副作用操作前失败；不得静默降级、忽略未知语义或猜测兼容。
5. 协商结果必须记录完整的 `contract_version`、Provider 身份/实现版本和能力快照标识。能力快照必须绑定本次协商结果；不能把一个 Contract 版本的能力快照用于另一个版本。
6. 一次运行会话中，协商版本保持固定。若需重新协商，必须先结束或显式重建会话；不得在请求执行中途静默切换版本。
7. Contract PATCH 发布可在不改变语义的前提下替换同一 MAJOR.MINOR 的勘误版本，但 Provider 与 Skill 仍须声明其验证过的完整版本。若 PATCH 实际改变行为保证，则该变更不是 PATCH，必须提升 MINOR 或 MAJOR。
8. 能力支持是独立于版本兼容性的第二道门槛：版本协商成功不代表某项可选能力受支持；每个操作仍必须执行能力检查。

### 3.3 规范源文件、发布制品与依赖方式

建议将 Contract 的规范内容与具体实现解耦，形成可独立获取、不可变引用的发布制品：

- 规范源文件定义接口、字段、状态机、错误类别、能力语义和兼容性规则；采用语言无关的规范描述。具体语言 SDK 或类型绑定可以生成，但不能成为唯一规范来源。
- 每个 Contract 版本发布不可变的规范制品，并包含版本号、变更记录和内容摘要。已发布版本不得原地修改；错误通过新的 PATCH/MINOR/MAJOR 版本修正。
- 与该 Contract 版本对应的公共 Conformance Suite 必须一同标记版本，并明确测试套件版本与 Contract 版本的映射。Fake Provider、AirBridge Provider 和未来 Provider 使用同一套测试定义；允许各 Provider 增加专有集成测试，但不得以专有测试替代公共契约测试。
- Skill 依赖稳定的 Contract 接口定义或其语言绑定，不得 import AirBridge 仓库内部模块、内部路径或 Provider 私有类。Provider 依赖 Contract 定义，不得反向把 AirBridge 私有实现暴露给 Skill。
- Skill 的发布元数据声明允许的 Contract 版本和已验证 Provider/Host 组合；Provider 的发布元数据声明支持的 Contract 版本及逐项能力。兼容矩阵应分别记录“规范兼容”“CI 契约测试通过”和“真实 Provider/Host 集成验收”，不能合并成一个含糊的兼容标记。
- 初期可以在 AirBridge 仓库内维护规范源文件和公共测试套件，以降低协作成本；但必须独立版本化和发布，不要求 Skill 安装 AirBridge 仓库，也不要求与 AirBridge.exe 同步发布。待 Contract 稳定、出现多个独立 Provider/Skill 消费者或独立发布确有必要时，再评估迁移到独立仓库/包。迁移不得改变已发布 Contract 的版本身份和内容摘要。
- 本草案不指定具体包管理器、注册表、仓库 URL、SDK 语言或自动生成工具；这些属于后续实现决策，不能在未评审前预设。

### 3.4 规范描述格式与权威边界（提案）

建议采用“规范文档 + JSON Schema + 公共 Conformance Suite”的组合，而不是把 OpenAPI 或某一种编程语言接口作为整个 Transport Contract 的唯一规范来源。

三类材料分别承担不同职责，不能互相替代：

| 材料 | 权威范围 | 应表达的内容 | 不应承担的职责 |
|---|---|---|---|
| 语言无关的规范文档 | 契约语义的规范性权威 | 操作语义、前置条件、状态机、UNKNOWN、请求关联与去重、幂等性、副作用、错误含义、安全边界、兼容规则 | 不以某个 Provider 的 HTTP 路由或内部类定义契约 |
| JSON Schema | JSON 数据结构的机器可校验定义 | 公共请求/响应对象、枚举、必需字段、字段约束、可扩展字段边界 | 不单独定义跨请求状态迁移、服务端持久性、去重保证或操作副作用 |
| 公共 Contract Conformance Suite | 可执行行为符合性的验证基线 | Provider 对状态、错误、请求恢复、去重、权限隔离、文件语义和能力门控的实际行为 | 不代替规范文本，也不因某个测试未覆盖就放宽规范要求 |

具体规则：

1. 规范文档使用明确的规范性措辞区分 MUST、MUST NOT、SHOULD、MAY；每项强制语义应能关联到一个或多个契约测试，无法自动化的要求必须标明人工验收方式。
2. JSON Schema 只校验单个 JSON 文档的结构约束。跨字段条件可使用 Schema 支持的条件结构；跨请求时序、持久性、授权主体绑定、幂等和 UNKNOWN 收敛等行为必须由 Conformance Suite 验证。
3. Schema 应声明其 JSON Schema 方言版本；Contract 发布版本与 Schema 文件版本需建立明确映射。Schema 的结构兼容性不能单独证明 Contract 语义兼容。
4. 公共类型的 JSON 表示应保持语言无关。Python dataclass、TypeScript interface 或其他语言绑定属于 SDK/绑定层，不得成为规范源文件；生成代码必须能追溯到对应 Contract 版本和 Schema。
5. 公共 Conformance Suite 必须绑定一个明确的 Contract 版本，并对每个 Provider 使用相同的测试定义和公共 fixtures。Provider 专有测试可以补充后端集成行为，但不能修改或跳过公共测试来宣称完整符合契约。
6. OpenAPI 可用于描述 AirBridge HTTP Provider 或其他 HTTP Provider 的具体网络 API；它属于 Provider 的传输绑定描述。OpenAPI 文档不能把 HTTP 特有路径、Header、状态码或认证机制反向提升为 Skill 必须依赖的 Contract 接口。
7. 如果某一接口未来需要 HTTP、串口、IPC 等不同绑定，各绑定应分别说明其映射与限制；只有在满足同一规范语义和 Conformance Suite 时，才能声明实现同一 Contract。
8. 若规范文档、Schema 和测试出现冲突，不能由实现自行挑选对自己有利的解释。该版本应标记为规范缺陷/发布阻断项，由维护者通过勘误或新版本明确解决；已发布版本不得静默改变含义。

### 3.5 建议的规范工件组织（非冻结目录）

为便于独立发布和审查，建议未来按职责组织工件，而不是按某个 Provider 的源码结构组织：

```text
transport-contract/
  spec/
    overview.md
    operations.md
    request-lifecycle.md
    security.md
    compatibility.md
  schemas/
    common/
    shell/
    filesystem/
    requests/
  conformance/
    manifest.yaml
    fixtures/
    tests/
  bindings/
    http/                 # 可选：HTTP Provider 绑定说明
```

该结构只是职责示意，不要求现在创建目录或迁移文件。spec/ 是语义规范来源，schemas/ 是结构定义，conformance/ 是行为测试；bindings/ 描述特定传输映射，不能反向成为核心契约依赖。

### 3.6 三类材料的发布门槛

一个 Contract 版本只有在以下条件全部满足后，才可标记为正式发布：

- 规范文档完成评审，所有 MUST / MUST NOT 要求均有明确解释，关键语义无未决冲突。
- JSON Schema 可独立校验规范样例，且 Schema 方言、版本映射和兼容性规则明确。
- 公共 Conformance Suite 与 Contract 版本绑定；Fake Provider 通过全部适用的必需测试。
- 每项可选能力都有能力门控测试；未实现的可选能力明确标记 unsupported 或 conditional，不得以跳过测试冒充支持。
- 发布制品、规范文档、Schema、测试套件和 fixtures 均具有可追溯的版本与内容摘要。
- Provider 的测试报告明确记录 Provider 实现版本、Contract 版本、Conformance Suite 版本、能力快照和适用的 operation_kind。
- 真实 Provider 集成结果单独报告；Fake Provider 通过不能替代真实 Provider 验收。

### 3.7 兼容性声明示例（非冻结格式）

以下仅示意每个组件需要声明哪些信息，不代表当前仓库已创建这些 manifest：

Skill 发布元数据示例：

```yaml
skill:
  version: "0.4.0"
  transport_contract:
    accepted_versions:
      - "1.0.0"
    required_capabilities:
      - "shell.execute"
  tested_provider_versions:
    - "airbridge-provider:0.1.0"
  tested_host_adapters:
    - "opencode-adapter:0.1.0"
```

Provider 发布元数据示例：

```yaml
provider:
  id: "airbridge"
  implementation_version: "0.1.0"
  implemented_contract_versions:
    - "1.0.0"
  conformance_suite_version: "1.0.0"
  capabilities:
    - name: "shell.execute"
      support: "supported"
```

以上仅示意字段职责和版本相互独立的关系，不冻结 manifest 格式，也不表示仓库已创建这些独立软件包。Host Adapter 同样需要独立声明其实现版本及已验证的 Host/版本范围。

### 3.8 Optional Profile 的独立版本与协商（提案）

Core Contract 只定义 Provider 生命周期、契约发现/协商、能力快照和公共语义基础；具体操作集合通过独立 Optional Profile 提供。Profile 不改变 Core Contract 的身份，也不把某种操作强加给所有 Provider。

建议 v1 采用以下 Profile：

| Profile ID | 覆盖范围 | 说明 |
|---|---|---|
| transport.shell | Shell 命令提交及该 Profile 明确列出的查询接口 | 不默认包含状态查询、结果查询或去重；仍按能力逐项声明 |
| transport.request-tracking | 通用请求关联、Client ID 查找、状态查询、结果查询及服务端去重 | 各子能力独立声明，并限定 operation_kind、主体范围和保证 |
| transport.filesystem | list/stat/read/write/mkdir/move/remove | 每个文件操作独立声明能力及语义保证 |
| transport.filesystem.chunked-transfer | begin/put/status/complete/abort | 作为 Filesystem Profile 的可选扩展 Profile；必须声明完整生命周期和恢复保证，且依赖兼容的 Filesystem Profile 版本 |

Profile ID 是稳定的命名空间标识；Profile 自身采用独立的 MAJOR.MINOR.PATCH 版本，不与 Core Contract 版本或 Provider 软件版本共用版本号。Profile 的语义变更按其自身兼容性规则升级版本。

**版本协商规则：**

1. Skill 元数据声明自己支持并验证过的完整 Core Contract 版本集合，以及每个必需 Profile 的完整版本集合。
2. Provider 的能力快照声明其实现并验证过的 Profile 版本集合；不得仅因底层路由/函数存在就声明实现 Profile。
3. 先协商 Core Contract，再分别对 Skill 要求的每个 Profile 选择双方共同支持的最高完整版本。Profile 版本必须由该 Profile 对应的 Conformance Suite 验证。
4. 任一必需 Profile 没有共同版本时，Skill 初始化失败；不得静默降级到另一个 Profile 或将 Profile 缺失解释为能力 unsupported 后继续运行。
5. 一次 Provider 会话内，已协商的 Core Contract 版本和每个 Profile 版本固定。重新协商必须重建会话，并刷新能力快照。
6. Profile 版本兼容与 Profile 内具体能力支持是两道门槛：实现 Profile 不代表其所有可选能力都支持；操作前仍须逐项检查能力、条件、限制及服务端授权。
7. Profile 可声明依赖 Core Contract 的公共类型/语义，但不得依赖另一个 EDA Product Skill，也不得把 AirBridge 私有接口作为 Profile 要求。Chunked Transfer Profile 必须声明其依赖的 Filesystem Profile 版本范围；不得在没有兼容 Filesystem Profile 的情况下单独协商成功。

**能力快照的 Profile 绑定：**

每个 Profile 声明至少包含 profile_id、profile_version、conformance_suite_version 和该 Profile 实际支持的能力清单。能力条目必须能追溯到 Profile；相同能力名若跨 Profile 复用，必须保证语义完全一致，否则使用不同的能力标识。条件、limits、guarantees 和适用 operation_kind 仍按第 4 节规则声明。

Profile 能力快照示意（非冻结 JSON Schema）：

```yaml
provider:
  provider_id: example.remote
  implementation_version: 2.4.0
contract:
  negotiated_version: 1.0.0
profiles:
  - profile_id: transport.shell
    profile_version: 1.0.0
    conformance_suite_version: 1.0.0
    capabilities:
      - name: shell.execute
        support: supported
        guarantees: [client_request_id_preserved]
        conditions: []
        limits: { max_payload_bytes: 65536 }
      - name: shell.status_query
        support: unsupported
        guarantees: []
        conditions: []
        limits: {}
```

此例只表示 Shell Profile 被实现，但状态查询未实现；不得据此推断 shell.result_query、请求去重或其他能力已支持。

**Skill / Provider Profile Manifest 示例（非冻结格式）：**

Skill：
```yaml
skill_id: orcad.capture
contract:
  supported_versions: ["1.0.0"]
required_profiles:
  - profile_id: transport.shell
    supported_versions: ["1.0.0"]
    required_capabilities:
      - shell.execute
  - profile_id: transport.request-tracking
    supported_versions: ["1.0.0"]
    required_capabilities:
      - request.correlation
optional_profiles: []
```

Provider：
```yaml
provider_id: airbridge.serial
implementation_version: 2.4.0
contract:
  supported_versions: ["1.0.0"]
implemented_profiles:
  - profile_id: transport.shell
    supported_versions: ["1.0.0"]
    conformance_suite_version: "1.0.0"
  - profile_id: transport.request-tracking
    supported_versions: ["1.0.0"]
    conformance_suite_version: "1.0.0"
  - profile_id: transport.filesystem
    supported_versions: ["1.0.0"]
    conformance_suite_version: "1.0.0"
implemented_capabilities:
  - shell.execute
  - request.correlation
  - filesystem.list
  - filesystem.read
```

上述 Manifest 是设计示例，不代表仓库已经存在对应 Skill 包、Provider 包或 Profile 实现。能力快照才是当前 Provider 会话的实际能力声明；Manifest 是发布/验证范围声明，两者必须一致，不得用静态 Manifest 替代运行时能力门控。

**依赖声明规则：**

- Skill 的 required_profiles 表示缺失即不能初始化；optional_profiles 表示可在缺失时禁用相应功能，但不得影响核心工作流的安全性。
- required_capabilities 必须在对应 Profile 的能力命名空间/范围内明确列出。request.* 能力由 transport.request-tracking Profile 声明，不应混入 Shell Profile。若能力支持为 conditional，Skill 必须在提交前验证条件；无法验证时视为不满足。
- Skill 不得仅声明“需要 Shell”而不列出实际依赖的能力；例如 Shell 执行与 Shell 状态查询是不同能力。
- Provider 的 implemented_profiles 只声明已通过对应 Profile Conformance Suite 的版本；implemented_capabilities/运行时快照列出该 Profile 中实际支持的能力。不得用 Profile 存在推导全部操作支持。
- Profile Conformance Suite 独立版本化，并明确对应 Profile 版本；Core Contract Suite 只验证基础核心和公共语义，不替代 Profile 专项行为测试。

## 4. 能力协商

### 4.1 能力声明模型

建议能力条目至少包含：

| 字段 | 含义 |
|---|---|
| `name` | 稳定的能力标识 |
| `support` | `supported`、`unsupported` 或 `conditional` |
| `guarantees` | 能力提供的明确语义保证 |
| `conditions` | `conditional` 支持成立所需的可判定条件；其他状态为空数组 |
| `limits` | 大小、超时、并发、路径等限制 |
| `notes` | 可选的非规范说明 |

能力声明应采用显式枚举，不应只用一个布尔值表达具有不同保证等级的行为。

为避免不同 Provider 对同一字段作出不同解释，v1 的规范化要求如下：

- `support=supported`：在声明的全部适用范围内提供该能力及所列保证。
- `support=unsupported`：不提供该能力；调用方不得调用依赖该能力的接口。
- `support=conditional`：仅在明确列出的条件全部满足时提供能力；条件必须机器可判定，或明确要求调用方在提交前完成检查。条件未满足时按不支持处理。
- `guarantees` 必须列出可验证的语义保证，而不是使用“可靠”“安全”等无法测试的形容词。
- `limits` 必须表达适用边界，例如最大 payload、最大并发数、请求记录保留期、路径根和超时上限；未知限制应标记为未知，不得省略后暗示无限制。
- 每个快照必须带有 Provider 身份/实例标识、Contract 版本、生成时间和有效期或刷新策略。过期快照不得用于授权新的副作用操作；无法刷新时按能力未知处理。

### 4.2 建议的能力标识

- `runtime.identity.read`
- `runtime.capabilities.read`
- `shell.execute`
- `shell.status_query`
- `shell.result_query`
- `filesystem.read`
- `filesystem.list`
- `filesystem.stat`
- `filesystem.write`
- `filesystem.mkdir`
- `filesystem.move`
- `filesystem.remove`
- `filesystem.chunked_transfer`
- `request.correlation`
- `request.lookup_by_client_id`
- `request.status_query`
- `request.deduplication`
- `request.result_query`

这些标识是契约设计候选项；最终名称需在评审后冻结。特别是 `request.correlation`、`request.deduplication` 和 `request.result_query` 是彼此独立的能力。

### 4.3 能力协商规则

1. Provider 初始化后返回能力快照及其生成时间/有效期信息。
2. Skill 在执行依赖某能力的操作前检查声明。
3. 缺少必需能力时，Skill 在提交请求前失败。
4. Provider 的能力声明不得超过其后端实际保证。
5. 能力发生变化时，Provider 应提供刷新机制；Skill 不应无限期缓存能力。
6. 对条件支持的能力，必须明确条件与限制；不满足条件时按不支持处理。


### 4.4 能力与接口门控映射（v1 必须明确）

能力声明不是说明性元数据；每个可选接口调用都必须有可判定的能力门控。Provider 在能力缺失、为 `unsupported`、条件不满足或快照过期时，必须在提交任何副作用请求前拒绝调用。不得仅因为某个 HTTP 路由或底层函数存在，就推断对应契约能力已支持。

| 契约接口/操作 | 必需能力 | 说明 |
|---|---|---|
| `get_contract_info()` / `negotiate_contract()` | 基础契约接口，不通过可选能力门控 | 仅做发现与版本协商；不执行副作用操作 |
| `get_capabilities()` | 基础契约接口，不通过快照内能力门控 | 首次能力快照是引导流程的一部分，不能要求先声明 `runtime.capabilities.read` 才能读取自身 |
| `get_runtime_info()` | `runtime.identity.read` | 可选通用运行时身份信息；不得用 EDA 版本信息代替 |
| `ShellTransport.submit()` | `shell.execute` | 仅表示允许提交 Shell 操作，不隐含状态查询、结果查询或去重 |
| Shell `get_status()` | `shell.status_query` 或明确适用于 SHELL 的 `request.status_query` | 两种声明同时存在时语义保证必须一致 |
| Shell `get_result()` | `shell.result_query` 或明确适用于 SHELL 的 `request.result_query` | 不得由 status query 推断 result query |
| `FilesystemTransport.list()` | `filesystem.list` | 不得只凭 `filesystem.read` 推断目录列举 |
| `FilesystemTransport.stat()` | `filesystem.stat` | 不得只凭 `filesystem.read` 推断元数据查询 |
| `FilesystemTransport.read()` | `filesystem.read` | 读取能力不隐含写入能力 |
| `FilesystemTransport.write()` | `filesystem.write` | 具体 write mode 还必须受 capability limits/conditions 约束 |
| `FilesystemTransport.mkdir()` | `filesystem.mkdir` | 不得由 write 能力推断 |
| `FilesystemTransport.move()` | `filesystem.move` | 必须声明跨文件系统和原子性语义 |
| `FilesystemTransport.remove()` | `filesystem.remove` | 必须声明递归删除等边界 |
| 所有 chunked-transfer 方法 | `filesystem.chunked_transfer` | 该能力必须覆盖 begin/put/status/complete/abort 的完整生命周期及声明的恢复保证；不得只实现上传而宣称完整分块传输 |
| `find_by_client_request_id()` | `request.lookup_by_client_id` | 仅支持明确声明的 operation_kind |
| `get_request_status()` | `request.status_query` | 仅支持明确声明的 operation_kind |
| `get_request_result()` | `request.result_query` | 仅支持明确声明的 operation_kind |
| 对重复提交提供安全去重保证 | `request.deduplication` | 仅对声明的 operation_kind、键作用域、payload 规范化版本和保留期限有效；不能作为所有操作的全局保证 |
| 将后续响应关联到原请求 | `request.correlation` | 只保证关联，不代表去重、状态查询或结果查询 |

补充规则：

1. `request.correlation`、`request.lookup_by_client_id`、`request.status_query`、`request.result_query` 和 `request.deduplication` 必须分别声明；任何一个都不能自动推导另一个。
2. `filesystem.chunked_transfer` 是一个完整扩展能力。Provider 必须声明它覆盖的动作集合、每个动作的请求关联/去重保证、块回执持久性和恢复边界；如果某动作不支持，应将该能力标记为 conditional 并明确条件，或整体标记为 unsupported，不能让调用方误以为五个动作都可用。
3. 通用请求跟踪能力必须声明支持的 `operation_kind` 集合。能力名称存在但没有覆盖当前操作类型时，视为不支持。
4. 每个接口的调用方仍须通过服务端认证和授权；能力快照不是授权凭据，也不能扩大服务端 Scope。
5. Provider 的 conformance tests 必须验证“声明支持的能力确实可调用”和“声明不支持/条件不满足时在提交前拒绝”，不能只测试能力 JSON 的格式。

### 4.5 Profile 与 Capability 的绑定规则（提案）

能力快照不能只给出一组无来源的能力名称。每项可选能力必须能够关联到已协商的 Profile 及其版本。建议 Profile 级声明采用以下逻辑结构：

```text
ProfileDeclaration {
  profile_id,
  profile_version,
  conformance_suite_version,
  capabilities[]
}
```

Provider 只有在该 Profile 的必需 Conformance Tests 通过后，才能声明实现对应 Profile 版本。Profile 中可选能力仍可标记 supported / unsupported / conditional；Profile 合规不代表全部可选能力都受支持。

Skill 初始化门控顺序：

1. 协商 Core Contract 版本。
2. 对每个 required_profile 协商 Profile 版本；任一失败则初始化失败。
3. 获取并验证绑定上述版本的能力快照。
4. 检查 Skill 声明的 required_capabilities；缺失、unsupported、条件无法验证或快照过期均视为不满足。
5. 只有以上检查通过且服务端认证/授权成功后，才允许提交远端操作。

## 5. 核心接口草案

以下为语言无关的逻辑接口，不限定 Python、TypeScript 或具体包布局。

### 5.0 v1 最小核心与可选扩展边界

为避免所有 Provider 被迫实现同一组后端操作，v1 将“契约生命周期核心”与“可选操作能力”分开。符合基础契约不代表 Provider 已提供任何特定远端操作；Skill 必须检查自己实际需要的能力。

| 层级 | v1 定义 | 是否所有 Provider 必须实现 | 缺失时的处理 |
|---|---|---|---|
| 契约发现与版本协商 | 读取 Provider 身份/版本和支持的 Contract 版本；协商并固定一个共同版本 | 必须 | 初始化失败，不允许副作用操作 |
| 能力快照 | 返回与已协商 Contract 版本绑定的能力快照 | 必须 | 初始化失败或能力视为未知，不允许副作用操作 |
| Provider 生命周期 | 关闭 Provider 会话并释放其资源 | 必须 | 实现必须提供明确关闭语义 |
| 统一操作结果/错误模型 | OperationResponse、Submission、OperationResult、TransportError | 任何提供操作的 Provider 必须遵守 | 不能用 Provider 私有返回结构绕过公共语义 |
| Shell | ShellTransport.submit 及独立状态/结果查询能力 | 可选 | 未声明能力即不可调用 |
| Filesystem 基础操作 | list/stat/read/write/mkdir/move/remove | 可选，且逐项声明能力 | 未声明的单项操作不可调用 |
| RequestTracking | client ID 查找、状态查询、结果查询、服务端去重 | 可选，逐项声明能力和 operation_kind 范围 | 不支持时保留 UNKNOWN，不盲目重放 |
| Chunked Transfer | begin/put/status/complete/abort 完整生命周期 | 可选扩展，不是 v1 基础要求 | 未声明完整能力时不得按可恢复分块传输使用 |
| RuntimeInfo | 通用运行时身份/版本信息 | 可选 | 不影响基础契约协商；调用方不得伪造默认值 |

因此，v1 的基础合规只证明 Provider 能完成版本协商、能力发现和生命周期管理，并遵守公共类型/安全边界；它不意味着 Shell、Filesystem、RequestTracking 或 Chunked Transfer 必须全部实现。某个 Skill 的可用性则由“契约版本兼容 + 所需能力全部满足 + 服务端授权”共同决定。

### 5.0.1 基础生命周期接口

建议基础接口由 Host Adapter/Provider Factory 为每个 Skill 调用上下文创建一个独立的 Provider Session：

```text
ProviderSession
  get_contract_info() -> ContractInfo
  negotiate_contract(client_supported_versions[]) -> ContractNegotiationResult
  get_capabilities(refresh=false) -> CapabilitySnapshot
  close() -> None
```

- `ProviderSession` 是会话作用域对象；其 Contract/Profile 协商结果、能力快照和请求上下文只属于该 Session。
- `get_contract_info()` 是无副作用的发现操作，返回 Provider 身份、实现版本和支持的 Contract 版本集合；它不执行副作用操作。
- `negotiate_contract()` 根据调用方明确提供的完整版本集合选择最高共同版本；没有共同版本时返回明确的不兼容结果。不得静默选择未声明版本。
- 协商结果绑定当前 Session；该 Session 后续能力快照和操作必须使用该版本。不同 Session 不得通过共享可变状态互相覆盖协商结果。
- `get_capabilities()` 只返回当前已协商版本及已协商 Profile 版本绑定的能力快照。它是基础引导接口，不依赖快照内部的 `runtime.capabilities.read` 能力授权首次读取。
- `close()` 只关闭当前 Session 并释放其独占资源；不得关闭其他 Session 正在使用的共享 Provider Runtime。关闭后调用该 Session 的操作接口必须返回明确生命周期错误，不得悄悄创建新 Session。
- 共享 Runtime 的创建和最终关闭由 Host Adapter/Provider Factory 管理，不属于 Skill 可调用的 Transport Contract 接口；Provider 可以完全不实现 Runtime 共享。

### 5.1 Provider 生命周期

```text
ProviderSession
  get_contract_info() -> ContractInfo
  negotiate_contract(client_supported_versions[]) -> ContractNegotiationResult
  get_capabilities(refresh=false) -> CapabilitySnapshot
  close() -> None

Optional RuntimeInfo
  get_runtime_info() -> RuntimeInfo

Optional RequestTracking Profile
  find_by_client_request_id(operation_kind, client_request_id) -> RequestLookupResult
  get_request_status(operation_kind, request_reference) -> RequestStatus
  get_request_result(operation_kind, request_reference) -> OperationResult

RequestReference {
  client_request_id?,
  provider_request_id?
}
// At least one identifier is required; if both are supplied they must identify the same request.
```

`RuntimeInfo` 仅描述通用远端运行环境身份与版本，不包含 EDA 生命周期信息；它是可选信息，不属于所有 Provider 的基础合规门槛。

### 5.1.1 连接中断、恢复与重新协商（提案）

物理连接状态与逻辑 Provider Session 状态必须分开处理。底层连接短暂断开并不自动意味着 Provider Session 已结束；但连接恢复也不证明原请求未执行或已完成。

1. Provider Runtime 可以按实现策略恢复物理连接。恢复时必须重新验证目标 Provider 身份；不得把连接到不同 Provider 实例当成原实例的透明恢复。
2. 若 Provider 身份/实例、Contract 版本或已协商 Profile 版本发生变化，原 Session 不得继续使用旧能力快照提交操作；必须建立新 Session 并重新协商。若仅 provider_instance_id 因服务重启变化，只有在 provider_id/逻辑命名空间保持一致且 Provider 明确保证请求映射/记录可跨实例恢复时，才可通过新 Session 查询旧请求；若 Provider 身份或逻辑命名空间改变，不得假定新 Provider 能查询旧请求，原请求保持 UNKNOWN，除非存在显式、受验证的迁移映射。
3. 若身份/实例和已协商版本均未变化，Provider 可在原 Session 内恢复连接，但必须刷新或验证能力快照的有效性；能力未知或过期时，副作用操作 fail closed。
4. 连接中断期间已提交或可能已离开调用方的请求，按原 `client_request_id` 保持 `SUBMISSION_UNKNOWN` 或 `UNKNOWN`；不得因重连自动转成 REJECTED、NOT_SUBMITTED 或失败。
5. 恢复后优先执行受授权的原请求查找/状态/结果核实（若对应 Profile/能力可用）。查询不可用或结果不确定时保留 UNKNOWN；不得为“试试看”而重新提交非幂等请求。
6. 新 Session 若要查询旧请求，必须使用原调用主体身份和原 request ID；不得用新主体接管或伪造请求所有权。服务端必须继续执行主体隔离。
7. 重连、刷新凭据或重新创建本地 Session 都不改变原请求 ID、payload digest 或已知副作用事实；任何重试仍受第 8 节幂等性与服务端去重保证约束。
8. Session close 或 Runtime shutdown 仅表示本地资源生命周期结束，不代表远端取消。若需要远端取消，必须由未来独立、显式、可授权的能力定义，不得隐含在 close() 中。

### 5.2 Shell

```text
ShellTransport
  submit(command, options, client_request_id) -> OperationResponse
  // Shell-specific convenience aliases for RequestTracking; same semantics and authorization.
  get_status(request_reference) -> RequestStatus  // operation_kind is implicitly SHELL
  get_result(request_reference) -> OperationResult // operation_kind is implicitly SHELL

```

约束：

- Shell 是通用远端命令能力，不提供 Capture/Allegro 专用方法。
- 命令执行必须由 Provider 的授权策略和后端权限控制；客户端接口不能取代服务端授权。
- `get_status` 与 `get_result` 是 Shell 专属便捷别名，等价于 `RequestTracking.get_request_status(SHELL, request_reference)` 与 `get_request_result(SHELL, request_reference)`；不得定义不同的状态或授权语义。相应查询能力必须由 `shell.status_query` / `shell.result_query` 或适用的通用请求跟踪能力明确声明。
- `request.lookup_by_client_id`、`request.status_query` 和 `request.result_query` 是通用请求跟踪能力，适用于 Provider 明确声明支持跟踪的请求类型；它们不隐含所有 Shell/RFS 操作都支持查询。若同时声明通用能力和 Shell 专属能力，两者必须满足相同的语义保证，不得产生冲突。
- Shell 专属能力标识（如 `shell.status_query`、`shell.result_query`）只描述 Shell 操作范围；通用请求跟踪能力不能仅凭 Shell 能力推断，也不能反向推断。
- `find_by_client_request_id` 用于提交响应丢失、调用方未取得 `provider_request_id` 时恢复原请求映射；它不是重新提交操作。
- 查询结果必须区分“找到原请求”“权威确认不存在”和“无法判断”；不具备权威查询保证时不得返回“未提交”。
- 建议将结果结构固定为：
  ```text
  RequestQueryAvailability = AVAILABLE | UNAVAILABLE

  RequestLookupResult =
    FOUND {
      client_request_id,
      provider_request_id?,
      status?,
      status_availability, // RequestQueryAvailability
      result_availability, // RequestQueryAvailability
      payload_digest?,
      accepted_at?,
      result_reference?
    }
  | AUTHORITATIVE_NOT_FOUND {
      client_request_id,
      checked_at,
      authority_scope,
      retention_guarantee
    }
  | INDETERMINATE {
      client_request_id,
      reason,
      retry_after?
    }
  ```
- `FOUND` 必须返回原请求映射；如果状态/结果不可查询，必须明确标记相应字段不可用，不能伪造状态。
- `AUTHORITATIVE_NOT_FOUND` 只有在 Provider 能证明查询覆盖了该请求 ID 的完整接收范围，且记录持久性/保留期限覆盖该请求可能存在的时间窗口时才允许返回。仅仅“当前数据库没有这条记录”不满足条件。
- 超时、连接错误、权限不足、索引延迟、记录过期或查询范围不完整均属于 `INDETERMINATE`，不得折算为不存在。
- 该结果仅描述请求映射是否存在，不自动证明远端业务副作用是否发生；除非 Provider 的提交/记录原子性保证明确建立了这种联系。
- 若后端不能按客户端请求 ID 查询，Provider 必须明确声明不支持该恢复能力；调用方不能因此生成新 ID 并重放非幂等请求。
- 如果后端仅支持同步执行，Provider 可以返回已完成结果，但不得伪造异步任务 ID 或状态查询能力。
- Provider 必须明确命令超时是“停止等待”还是“远端执行已被终止”。客户端等待超时不能被解释为远端执行已停止。

### 5.3 Filesystem / RFS

建议先定义通用原语：

```text
FilesystemTransport
  list(path, options, client_request_id) -> OperationResponse
  stat(path, client_request_id) -> OperationResponse
  read(path, range, client_request_id) -> OperationResponse
  write(path, content, write_mode, options, client_request_id) -> OperationResponse
  mkdir(path, options, client_request_id) -> OperationResponse
  move(source, destination, options, client_request_id) -> OperationResponse
  remove(path, options, client_request_id) -> OperationResponse
```

约束：

- 路径语义、路径根限制、覆盖策略、权限和符号链接处理必须由 Provider 明确声明。
- `write_mode` 至少区分 `CREATE_NEW`（目标必须不存在）、`REPLACE`（创建或整体替换，具体语义需声明）、`APPEND`（追加）和可选的 `CONDITIONAL_REPLACE`（基于版本/摘要条件替换）。Provider 不支持的模式必须明确拒绝，不得静默降级为其他模式。
- `APPEND` 属于非幂等操作，不能默认安全重试；`REPLACE` 只有在完整替换、无额外副作用且后端语义得到保证时才可标记为幂等。
- 写入结果必须说明是完整提交、明确未提交、部分写入还是结果未知；部分写入不得伪装成成功。若后端支持原子替换，应明确原子性范围；不支持时应声明可能出现的部分文件状态。
- `move` 必须声明同一文件系统/跨文件系统行为、目标已存在策略及原子性保证；不得默认跨卷移动是原子操作。
- `remove` 必须声明目标不存在时的行为，以及递归删除是否支持；默认不得把递归删除隐含在普通删除中。
- 目录、文件不存在、目标已存在等情况必须有稳定错误分类。
- 文件读取结果应能表达长度及内容摘要；若后端无法提供摘要，应明确标记而非生成未经验证的摘要。
- 上传/下载的大文件分块机制单独定义，不能把多次普通 `write` 自动视为可靠分块协议。

### 5.4 大文件传输扩展

若声明 `filesystem.chunked_transfer`，建议提供：

```text
begin_transfer(metadata, client_request_id) -> OperationResponse
put_chunk(transfer_id, index, bytes, chunk_digest, client_request_id) -> OperationResponse
get_transfer_status(transfer_id, client_request_id) -> OperationResponse
complete_transfer(transfer_id, expected_digest, client_request_id) -> OperationResponse
abort_transfer(transfer_id, client_request_id) -> OperationResponse
```

必须明确：

- transfer ID 的作用域、有效期和所有权；
- 分块大小、总块数、序号规则；
- 每次 `put_chunk`、`complete_transfer` 和 `abort_transfer` 都是独立的请求操作，必须携带调用方生成并持久化的 `client_request_id`；对同一逻辑调用的查询/恢复复用原 ID。
- `transfer_id` 标识传输会话，不替代各操作的 `client_request_id`，也不自动提供去重保证。
- `get_transfer_status` 是只读查询，但仍是独立调用；如需跨调用恢复该查询本身，必须携带新的 `client_request_id`。
- `begin_transfer` 本身也必须按普通请求处理；若其响应丢失，必须按其 `client_request_id` 执行请求恢复，不能通过新建 transfer ID 盲目重开。
- 相同 transfer ID、块序号和块摘要重复提交时，Provider 必须明确是否返回原回执而不重复产生副作用；若不保证，则不得将块提交描述为可安全重试；
- 不同内容重复提交相同块序号时必须返回冲突，不能覆盖已确认块；
- 分块回执是否在断线/进程重启后仍有效，以及查询状态是否能区分已确认、未确认和未知块；
- 已确认块的持久性及查询方式；
- 完成时完整文件长度与摘要校验；
- 中断后恢复和清理策略；
- abort 与 complete 竞争时的结果语义。

如果 Provider 不支持这些保证，就不得声明可恢复的分块传输能力。

### 5.5 公共数据类型草案

以下类型是 Provider 间共享的逻辑数据模型。字段名和枚举值在 v1 冻结前仍可调整；Provider 不得省略会改变安全性或请求结果解释的字段。

```text
ContractInfo {
  contract_name,
  provider_id,
  provider_name,
  provider_implementation_version,
  supported_contract_versions[]
}

ContractNegotiationResult =
  NEGOTIATED {
    contract_version,
    provider_id,
    provider_implementation_version
  }
| INCOMPATIBLE {
    provider_supported_versions[],
    reason
}

CapabilitySnapshot {
  provider_id,
  provider_instance_id,
  provider_implementation_version,
  contract_version,
  generated_at,
  expires_at?,
  capabilities[]
}

Capability {
  name,
  support,          // supported | unsupported | conditional
  guarantees[],
  conditions[],
  limits,
  notes?
}

OperationResponse = Submission | OperationResult

Submission =
  ACCEPTED {
    operation_kind,
    client_request_id,
    provider_request_id?,
    accepted_at
  }
| REJECTED {
    operation_kind,
    client_request_id,
    error,            // outcome must be not_executed
    rejected_at
  }
| SUBMISSION_UNKNOWN {
    operation_kind,
    client_request_id,
    provider_request_id?,
    reason,
    observed_at
  }

RequestState = NOT_SUBMITTED | SUBMISSION_UNKNOWN | ACCEPTED | RUNNING
            | COMPLETED_SUCCESS | COMPLETED_FAILURE | REJECTED | UNKNOWN

RequestStatus {
  operation_kind,
  client_request_id,
  provider_request_id?,
  state,             // RequestState; NOT_SUBMITTED is caller-local and not returned by Provider query
  observed_at,
  authority,         // authoritative | non_authoritative
  accepted_at?,
  started_at?,
  completed_at?
}

OperationCompletion = COMPLETED_SUCCESS | COMPLETED_FAILURE | UNKNOWN

OperationEffect = NOT_APPLICABLE | NOT_APPLIED | FULLY_APPLIED | PARTIALLY_APPLIED | UNKNOWN

OperationResult {
  operation_kind,
  client_request_id,
  provider_request_id?,
  state,             // OperationCompletion
  effect,            // OperationEffect; describes side effects, not execution status
  completed_at?,
  payload?,          // operation-neutral envelope; payload schema belongs to operation kind
  error?
}

TransportError {
  category,
  code,
  message,
  outcome,           // not_executed | executed_failed | unknown | not_applicable
  retryable,         // advisory only; cannot authorize replay
  request_ids?,
  details?
}
```

类型约束：

- `Submission.ACCEPTED` 仅表示 Provider 已确认接受，不表示操作已完成；`provider_request_id` 若尚不可用可省略，但此时 Provider 必须说明是否可通过 `client_request_id` 恢复映射。
- `Submission.REJECTED` 仅可用于能保证请求未执行的拒绝；否则返回 `SUBMISSION_UNKNOWN`。
- `TransportError` 用于无法形成有效 Submission 或 OperationResult 的 Provider/调用边界错误；一旦请求可能已离开调用方，outcome 必须如实标记为 unknown，调用方必须保留原 client_request_id 并按 SUBMISSION_UNKNOWN 语义处理。明确未提交的本地/前置错误可标记 not_executed。
- `Submission.SUBMISSION_UNKNOWN` 不得携带暗示失败或未执行的默认值。调用方必须保留原 `client_request_id`。
- `CapabilitySnapshot.contract_version` 必须与当前协商选定的契约版本一致；快照不得在不同 Contract 版本之间复用。
- `RequestStatus` 只能由 Provider 对已提交请求返回；`NOT_SUBMITTED` 是调用方本地状态，不是远端可查询状态。Provider 无法确认状态时返回 `UNKNOWN` 或非权威观察，不能构造一个看似确定的状态。
- `RequestTracking` 的 `operation_kind` 是请求命名空间的一部分；`provider_request_id` 只在该操作类型和 Provider 声明的身份/命名空间范围内解释。
- `RequestStatus.authority=non_authoritative` 的观察不能单独将请求推进为已确认状态；它只能作为诊断信息。权威等级不能由调用方自行提升。
- `OperationResult.effect` 与 `OperationResult.state` 必须分开解释：前者描述副作用实际达到的程度，后者描述执行是否完成/成功。只读操作使用 `NOT_APPLICABLE`；有副作用的操作不得使用该值。特别是 `COMPLETED_FAILURE` 可与 `PARTIALLY_APPLIED` 或 `UNKNOWN` 并存。
- `OperationResult.state=COMPLETED_FAILURE` 不代表没有副作用；必须通过 `effect` 表达 `NOT_APPLIED`、`PARTIALLY_APPLIED` 或 `UNKNOWN`。只有能证明完整目标效果已达成时才使用 `FULLY_APPLIED`。若最终执行状态无法确认，使用 `state=UNKNOWN`，并按证据设置 `effect`。
- `OperationResponse` 允许 Provider 在操作已同步完成时直接返回 `OperationResult`；若操作仍在执行或提交结果不确定，则返回 `Submission`。两种路径都必须保留相同且必填的 `client_request_id`；`provider_request_id` 仅在 Provider 已分配后提供。`TransportError` 是独立的调用边界错误通道，不属于 `OperationResponse` 联合类型。
- 每个由 Contract 操作入口接收的逻辑请求都必须携带 `client_request_id`。因此 `OperationResult` 和 `RequestStatus` 中的 `client_request_id` 必填；Provider 不得因同步执行、缺少后端任务 ID 或结果缓存而丢弃它。
- `RequestReference` 至少包含 `client_request_id` 或 `provider_request_id` 之一；若两者同时提供，Provider 必须验证它们映射到同一请求。这样允许在尚未取得 Provider ID 时按 Client ID 查询，也允许后端只支持 Provider ID 的实现明确拒绝不支持的查询方式。
- `RequestStatus.provider_request_id` 可选，`client_request_id` 必填。若 Provider 只支持按 Provider ID 查询，必须在能力声明和查询错误中明确这一限制；不得把缺少 Provider ID 解释为请求不存在。
- `RequestLookupResult.FOUND` 必须分别声明状态查询和结果查询是否可用；不能用 `status_availability` 推断结果查询能力，反之亦然。
- `OperationEffect.NOT_APPLICABLE` 用于无副作用的读取/查询操作；不得将只读操作强行标为 `NOT_APPLIED` 或 `FULLY_APPLIED`。
- `OperationResult.error` 仅描述已确认完成的操作失败（`state=COMPLETED_FAILURE`）；连接、认证、能力门控或调用边界错误使用独立的 `TransportError`。两者不得互相替代。
- `payload` 只承载对应操作的结果数据；通用层不得把 Shell stdout、文件字节或其他操作数据误解为 EDA 领域对象。具体 payload schema 应由通用操作类型定义，不能由 Provider 私自改变。
- `TransportError.retryable` 是 Provider 对当前错误场景的重试建议，不是独立授权。调用方仍必须结合操作幂等分类、请求状态、去重能力和错误 outcome 决定是否重试；`retryable=true` 不得覆盖非幂等操作的禁止重放规则。
- 所有类型均不得包含 Token、Authorization Header、私钥或其他凭据。可选字段缺失必须与空字符串、空对象或“已确认不存在”区分。

### 5.6 公共类型的必填字段与组合约束（v1 提案）

| 类型 | 必填字段 | 可选字段 | 关键约束 |
|---|---|---|---|
| ContractInfo | contract_name、provider_id、provider_name、provider_implementation_version、supported_contract_versions | 无 | 版本集合必须是完整版本号，不以 latest 或宽泛范围替代 |
| ContractNegotiationResult | 结果分支及其分支字段 | 无 | NEGOTIATED 必须包含唯一选定版本；INCOMPATIBLE 不得伪造选定版本 |
| CapabilitySnapshot | provider_id、provider_instance_id、provider_implementation_version、contract_version、generated_at、capabilities | expires_at | 快照必须绑定已协商版本和 Provider 身份/实现版本；过期规则明确 |
| Submission | operation_kind、client_request_id、分支时间/原因字段 | provider_request_id | 同一逻辑请求始终保留 client_request_id |
| RequestStatus | operation_kind、client_request_id、state、observed_at、authority | provider_request_id、各阶段时间 | Provider 不返回 NOT_SUBMITTED；至少一个请求 ID 可用于查询 |
| OperationResult | operation_kind、client_request_id、state、effect | provider_request_id、completed_at、payload、error | 只读操作 effect=NOT_APPLICABLE；副作用操作必须报告真实效果 |
| TransportError | category、code、message、outcome、retryable | request_ids、details | 错误诊断脱敏；retryable 不覆盖幂等性与 UNKNOWN 规则 |

额外一致性约束：

1. RequestStatus 与 OperationResult 必须保留原始 client_request_id；不能只返回 Provider 内部 ID。
2. RequestReference 必须至少提供一个请求标识；当两个标识都提供时，映射不一致必须返回稳定的 CONFLICT 错误。
3. OperationResult.state=COMPLETED_SUCCESS 对有副作用的操作，通常应对应 effect=FULLY_APPLIED；若业务定义允许“成功但未产生目标变更”，该例外必须由操作类型明确说明。COMPLETED_FAILURE 不得默认映射为 NOT_APPLIED。
4. OperationResult.state=UNKNOWN 不得与“已确认完成”同时表达；effect 可为 UNKNOWN，或在已有独立证据时表达已确认达到的效果程度。
5. TransportError 不替代 Submission.REJECTED、Submission.SUBMISSION_UNKNOWN 或 OperationResult。它用于无法形成这些结构的调用边界错误；其 outcome 必须与当前已知的执行事实一致。若远端终态和副作用已知，应返回 OperationResult，而不是用 TransportError 代替结果。
6. TransportError.retryable 不能单独成为自动重试许可。非幂等操作仍须满足已验证的服务端去重保证，或由上层业务层完成核实并创建新的逻辑操作。

## 6. 请求标识、关联与幂等性

### 6.1 两种请求标识

- `client_request_id`：由调用 Skill 生成，在一次逻辑操作的所有查询和任何经授权的重复提交中保持不变。
- `provider_request_id`：由 Provider/后端生成，用于映射到后端请求或任务。

两者不得混为一谈。Provider 必须说明两者映射关系是否持久、是否可查询，以及映射保留期限。

请求标识规则：

- `client_request_id` 在调用方生成；同一逻辑操作的所有状态查询、结果查询和获准的恢复查询必须复用原值。调用方必须在首次提交前持久化该 ID 及其操作意图，避免调用方重启后丢失恢复线索。
- 唯一性至少在“调用方身份/安全主体 + Provider 逻辑命名空间”范围内保证。Provider 必须声明实际唯一性作用域；不得假设不同 Agent、用户或租户生成的相同字符串天然属于同一请求。
- Provider 必须将请求映射与已认证主体/所有权上下文绑定。查询不得因猜测或碰撞其他主体的 client ID 而泄漏请求存在性、状态或结果。
- `provider_request_id` 在 Provider 后端的声明作用域内唯一；Provider 必须声明其作用域及是否可能跨重启/实例保持稳定。
- Provider 必须声明映射记录的持久性、保留期限、清理规则及其对 lookup 结果的影响。记录过期后不得返回 `AUTHORITATIVE_NOT_FOUND`，除非仍能证明完整接收历史。
- 若提交响应丢失，调用方可能只有 `client_request_id`，因此契约必须明确是否支持按该 ID 恢复映射；若不支持，结果保持未知，不能假设后端未接收请求。

### 6.2 请求关联不等于去重

契约分别定义：

1. **Correlation**：能否将后续状态/结果关联到原请求。
2. **Deduplication**：重复提交相同逻辑请求是否会被识别并避免重复副作用。
3. **Result query**：提交后能否查询原请求的状态或结果。

Correlation 只解决“找到同一个请求”，不保证重复提交不会再次执行。Correlation 本身不能作为非幂等操作自动重放的安全依据。

只有后端具备并验证了相应保证，Provider 才能声明支持。单纯在客户端保存 UUID 不构成服务端去重。

### 6.3 去重键和载荷一致性

若 Provider 声明服务端去重，至少必须规定：

- 去重键的唯一性作用域；
- 去重记录的持久化范围和保留期限；
- 同一键、同一载荷的重复提交行为；
- 同一键、不同载荷的冲突行为；
- 并发重复提交的处理；
- 去重记录过期后的行为；
- 规范化 payload 的字段集合、编码方式、摘要算法及版本；
- 去重记录与请求接收/副作用之间的原子性边界；

建议将规范化请求的 payload digest 与请求标识绑定。规范化输入至少应覆盖操作类型、目标资源、命令/内容、影响语义的选项及写入模式；不应纳入易变的传输元数据（如重试计数、连接地址）。Provider 必须声明摘要算法与规范化版本，避免不同实现对同一请求计算出不一致的 digest。相同 key 对应不同 digest 必须明确拒绝，不能悄悄执行第二个请求。

### 6.4 不支持去重时

- Provider 不得自动重放可能产生副作用的请求。
- 若支持按 `client_request_id` 查询，可用于恢复请求映射和读取原请求结果，但这仍不构成去重保证。
- 若既不支持去重，也不支持按 `client_request_id` 查询，提交响应丢失后必须保留 UNKNOWN；不能以“没有 provider_request_id”作为未执行证据。
- 调用方不得因为客户端超时而生成新 ID 后盲目重试。
- 是否允许只读操作重试，应由操作语义和 Provider 声明决定。
- 非幂等写操作在结果不明时必须进入 UNKNOWN 或等价的不确定状态。

## 7. 请求状态与 UNKNOWN

### 7.1 状态定义

建议的规范状态：

| 状态 | 含义 |
|---|---|
| `NOT_SUBMITTED` | 调用方确认尚未开始提交；仅表示提交前状态，不表示 Provider 已拒绝 |
| `SUBMISSION_UNKNOWN` | 提交调用发生中断，无法确认 Provider/后端是否接受请求 |
| `UNKNOWN` | 已知请求可能已被接受或执行，但当前无法确认其执行状态或最终结果 |
| `ACCEPTED` | Provider 明确确认已接受请求 |
| `RUNNING` | 后端明确报告正在执行 |
| `COMPLETED_SUCCESS` | 已确认执行成功 |
| `COMPLETED_FAILURE` | 已确认执行完成但失败 |
| `REJECTED` | 后端明确拒绝，且能保证没有执行 |

Provider 可将状态映射为更少的内部状态，但不得丢失“已确认”与“未知”的语义差异。

状态是对请求事实的观察，不是任意可写的标签。Provider 必须为每次状态报告提供来源/权威等级及观察时间；调用方不得仅按收到响应的先后顺序覆盖更强的已确认事实。

### 7.2 UNKNOWN 处理

UNKNOWN 是一种真实结果，不是普通异常，也不等于失败。

建议状态转换约束：

- `NOT_SUBMITTED -> SUBMISSION_UNKNOWN`：调用方开始提交后，提交是否已交付无法确认。
- `SUBMISSION_UNKNOWN -> ACCEPTED`：Provider 的权威证据确认已接受。
- `SUBMISSION_UNKNOWN -> REJECTED`：Provider 的权威证据确认拒绝且保证未执行。
- 若调用方在提交前本地校验失败，保持 `NOT_SUBMITTED` 并返回本地错误；这不是 Provider 的 `REJECTED`。
- `ACCEPTED -> RUNNING`：后端明确报告开始执行；同步操作也可从 `ACCEPTED` 直接进入已确认终态。
- `RUNNING -> COMPLETED_SUCCESS/COMPLETED_FAILURE`：取得权威终态结果。
- 任一已提交但无法核实的非终态可转入 `UNKNOWN`。
- `COMPLETED_FAILURE` 表示操作以失败结果结束，不代表没有副作用；副作用是否部分发生必须由操作结果/错误详情明确说明。
- `REJECTED` 仅表示后端拒绝且保证未执行；若不能保证未执行，应报告 `UNKNOWN`，而不是 `REJECTED`。
- `SUBMISSION_UNKNOWN` 专指提交接受情况不明；`UNKNOWN` 用于已提交请求的执行状态/最终结果不明。两者不可互换，查询到原请求映射只能证明请求被记录，不能单独证明其已执行或完成。
- `UNKNOWN` 不能仅因查询超时、连接恢复或本地重启而自动转成失败或未提交；只能由权威查询结果或业务层证据更新。
- `UNKNOWN` 可依据权威证据恢复为 `ACCEPTED`、`RUNNING`、`COMPLETED_SUCCESS`、`COMPLETED_FAILURE` 或 `REJECTED`；每次恢复必须保留证据来源和观察时间。若证据只能证明请求映射存在、不能证明执行状态，则只能恢复到已证实的最低状态。
- 已确认终态（`COMPLETED_SUCCESS`、`COMPLETED_FAILURE`、`REJECTED`）不得因普通查询失败、超时或较旧快照而回退。若 Provider 发现先前终态报告本身错误，必须返回明确的纠正/冲突诊断，而不是静默回退。
- `NOT_SUBMITTED` 是调用方在提交前的本地事实或本地拒绝结果，不是 Provider 对一个已发出提交请求的推测。提交调用一旦可能离开调用方进程，若没有可靠证据证明未交付，就必须进入 `SUBMISSION_UNKNOWN`。

Provider 必须避免把“查询不到记录”直接解释为“请求从未执行”，除非后端明确保证查询覆盖范围、持久性和保留期限足以支持该结论。

进入 UNKNOWN 后：

1. 保留原 `client_request_id` 和已知的 `provider_request_id`。
2. 不自动生成新请求替代原请求。
3. 若支持查询，使用原请求标识进行只读查询。
4. 若不支持查询，向上层返回明确的不确定结果及可用证据。
5. 是否进行业务层核实由 Skill 决定；Transport Provider 不执行 EDA 领域推断。
6. 只有取得权威证据后才能转为已确认状态。

### 7.3 超时语义

必须区分：

- connect timeout；
- submit/accept timeout；
- remote execution timeout；
- status/result query timeout；
- caller deadline。

任何本地等待超时都不能单独证明远端操作已经停止或未执行。

## 8. 幂等性与重试分类

建议每个操作由契约或 Provider 标记为：

- `READ_ONLY`：无副作用读取；
- `IDEMPOTENT_WRITE`：相同请求重复执行后的目标状态相同，且 Provider 明确保证；
- `NON_IDEMPOTENT`：重复执行可能产生额外副作用；
- `UNKNOWN_SEMANTICS`：无法证明属于以上类别。

重试规则：

- READ_ONLY：可按 Provider 限制进行有限重试。
- IDEMPOTENT_WRITE：只有在操作定义与后端实现均保证幂等时才可重试。
- NON_IDEMPOTENT：只有经过验证的服务端去重保证，才能支持自动重放；仅有请求关联或结果查询并不足够。若没有服务端去重，上层必须先完成业务状态核实，并由明确的业务策略决定是否发起新的逻辑操作；Transport 层禁止盲目自动重放。
- UNKNOWN_SEMANTICS：按最保守规则处理，不自动重试。

不能仅凭 HTTP 方法、操作名称或客户端 UUID 推断幂等性。

## 9. 统一错误模型

建议错误至少携带：

| 字段 | 含义 |
|---|---|
| `category` | 稳定错误类别 |
| `code` | 稳定机器可读代码 |
| `message` | 不含秘密信息的说明 |
| `retryable` | 是否可按契约重试 |
| `outcome` | `not_executed`、`executed_failed`、`unknown` 或 `not_applicable` |
| `request_ids` | 可用的请求标识 |
| `details` | 经脱敏的结构化诊断信息 |

建议错误类别：

- `LOCAL_VALIDATION`
- `CAPABILITY_UNAVAILABLE`
- `AUTHENTICATION`
- `AUTHORIZATION`
- `RATE_LIMITED`
- `TRANSPORT_CONNECTIVITY`
- `SUBMISSION_REJECTED`
- `REMOTE_EXECUTION`
- `REMOTE_FILESYSTEM`
- `TIMEOUT`
- `INTEGRITY`
- `CONFLICT`
- `UNKNOWN_OUTCOME`
- `PROVIDER_INTERNAL`

错误消息、异常和诊断信息不得包含 Token、Authorization Header、私钥或其他凭据。

## 10. 身份、认证与安全

### 10.1 凭据边界

建议 Provider 接收抽象 Credential Source，而不是由 Skill 直接读取环境变量或拼接认证头。

逻辑边界：

```text
Skill
  -> Transport Provider
       -> Credential Source
            -> Host / OS / approved credential integration
```

Credential Source 负责按调用上下文提供凭据；Provider 负责安全使用凭据；服务端仍负责最终身份认证和授权。

接口设计必须遵守以下边界：

- Credential Source 的调用上下文至少应能表达目标 Provider/audience、所需用途及调用主体；不得由 Skill 任意指定更高权限的 Scope。
- Provider 应通过受控接口取得短生命周期凭据或不透明凭据句柄；具体采用句柄还是短暂凭据材料由 Host Adapter/安全集成决定，不能把秘密写入 Skill 配置、普通日志、异常文本或返回给 Agent。
- 凭据不得被序列化进请求状态、请求结果、能力快照或诊断扩展字段。
- 必须定义凭据不可用、过期、撤销及轮换时的错误语义。认证失败不能自动触发凭据探测、输出凭据元数据或扩大权限。
- Credential Source 只负责提供凭据，不负责授予服务端权限；服务端仍独立执行身份认证、Scope、所有权和审计。

### 10.1.1 Credential Source 的最小接口边界（提案）

为避免“抽象 Credential Source”停留在概念层，建议 v1 将职责拆为 Skill、Provider、Host Adapter 和 Credential Source 四个边界：

- Skill：只调用 Provider 的业务中立接口；不得接收、保存、检查或拼接凭据。
- Provider：根据固定的 Provider 配置和调用上下文请求凭据绑定；不得自行搜索环境变量、读取 Agent 配置文件或探测凭据。
- Host Adapter：负责把宿主调用生命周期与 Skill/Provider 工厂连接起来，并注入获准的 Credential Source 实现；不得把凭据放进 Agent 工具参数、模型上下文或 Skill 可见配置。
- Credential Source：连接经批准的 OS 凭据库或组织凭据集成；按 Provider/audience、用途和已认证调用主体上下文提供受限凭据能力。它不负责决定服务端 Scope，也不替代服务端授权。

建议逻辑接口（非语言绑定的伪类型）：

```text
CredentialContext {
  provider_id: string
  audience: string
  purpose: string
  caller_context: OpaquePrincipalContext
}

CredentialBinding {
  binding_handle: OpaqueNonSerializableHandle
  expires_at?: Timestamp
}

CredentialSource.bind(context: CredentialContext)
  -> CredentialBinding | CredentialSourceError

AuthenticatedTransport.send(request, binding_handle)
  -> TransportResponse | TransportError
```

约束：

1. `provider_id`、`audience` 和 `purpose` 来自 Provider 的受信配置/能力声明；Skill 可以选择已配置的 Provider，但不能任意改写 audience 或请求更高权限 Scope。
2. `caller_context` 必须由 Host Adapter/认证上下文提供，不得由模型生成的自由文本伪造。缺少可信主体上下文时，Credential Source 必须拒绝绑定。
3. `binding_handle` 是进程内不透明句柄，不是 Token 字符串；不得 JSON 序列化、持久化、复制到 Skill 返回值、日志或异常。若某实现无法提供不透明句柄，必须把秘密的注入限制在 Host/OS 安全集成内部，不能退化为把秘密返回给 Agent。
4. `AuthenticatedTransport` 是唯一允许把凭据绑定到出站请求的位置。Provider 只能提交请求和句柄，不得读取句柄内部秘密或自行生成 Authorization Header。
5. 凭据绑定应限定目标 Provider/audience 和用途，并尽可能短生命周期；过期时间是可选诊断信息，不得用于推断 Token 内容、长度或是否存在。
6. Credential Source 错误只返回稳定错误类别，例如 `CREDENTIAL_UNAVAILABLE`、`CREDENTIAL_BINDING_DENIED`；不得返回秘密或可用于凭据探测的详细元数据。不得静默回退到环境变量、默认账户或其他更宽权限凭据；若匹配到多个无法唯一确定的绑定，必须 fail closed。服务端返回的认证/授权失败仍按实际 HTTP/API 语义映射。
7. 若认证失败发生在请求可能已被远端接收之后，Provider 必须依据服务端响应权威性判断 outcome；不得仅凭本地刷新凭据后自动重放非幂等操作。
8. 凭据轮换不得改变原请求 ID、载荷摘要或请求所有权；轮换后恢复查询仍按原调用主体和原请求标识执行。
9. 不定义通用 `get_token()`、`read_secret()`、`is_token_present()` 或 `token_length()` 接口；不得通过探测存在性来决定是否调用 Provider。

此接口仅定义边界和安全语义，不规定 Antigravity、OpenCode 或其他 Agent Host 的私有配置格式，也不要求它们暴露凭据给 Skill。

### 10.2 服务端安全不可被客户端替代

- 所有远端操作仍由服务端执行认证、Scope 校验、所有权检查、审计和限流。
- Skill 的业务授权不能替代 AirBridge 服务端权限检查。
- 不能通过 Shell 或 RFS 绕过被禁用的专用执行能力。
- 不因引入 Provider 抽象而扩大 Token Scope。
- 不记录或探测 Token 内容、存在性或长度。
- 日志仅记录必要的请求标识、能力、结果类别和脱敏诊断信息。
- 路径限制、工作区边界和命令策略应由可信服务端执行，不能只依赖 Skill 传入参数。

## 11. Fake / Mock Provider 与契约测试

Fake/Mock Provider 必须实现同一接口和同一状态语义，不得为了测试方便而提供生产 Provider 不具备的隐式保证。

建议契约测试套件至少覆盖以下场景；标注“仅在声明支持时”的测试必须同时验证支持路径和不支持路径，不能跳过能力门控验证：

1. Contract discovery and version negotiation：读取 Provider 身份/实现版本及支持版本；选择最高共同完整版本；无交集时在副作用提交前失败；会话内版本固定且不同会话互不覆盖。
2. Capability declaration and operation gating：每个接口与能力映射一致；unsupported、conditional 条件不满足、能力未知或快照过期时，副作用操作在提交前被拒绝。
3. Request ID preservation and provider ID mapping：首次提交、查询、恢复均保留原 client_request_id；验证 provider_request_id 映射及其作用域。
4. Lookup after lost submission response：提交响应丢失且调用方尚无 provider_request_id 时，按原 client_request_id 查找；验证 FOUND、AUTHORITATIVE_NOT_FOUND、INDETERMINATE 三种结果的边界。
5. Same-key/same-payload and same-key/different-payload：仅在声明 dedup 时验证缓存/原请求返回、载荷冲突拒绝及并发重复提交；验证 request_kind、目标资源和语义选项参与 payload digest。
6. Deduplication retention and expiry：保留期内重复提交不产生第二次副作用；记录过期后不得虚构 AUTHORITATIVE_NOT_FOUND 或安全重放保证。
7. Submission interruption：提交调用在请求可能已离开调用方后中断，状态进入 SUBMISSION_UNKNOWN；不得把本地异常映射成 REJECTED。
8. Synchronous and asynchronous providers：同步完成直接返回 OperationResult；异步提交返回 Submission；两条路径保留一致的请求 ID、状态和副作用语义。
9. Status/result query consistency：分别验证状态查询和结果查询；只支持其中之一时，另一接口必须明确拒绝。验证 RequestReference 可按 client_request_id 或 provider_request_id 查询，双 ID 不一致时冲突；验证 RequestStatus/OperationResult 始终保留 client_request_id。
10. UNKNOWN reconciliation：查询超时、断线、重启和旧快照不得把 UNKNOWN 自动变成失败或未提交；只有权威证据才能推进状态，已确认终态不得被旧观察回退。
11. Ownership isolation：跨 Agent/主体猜测 request ID 时，不能泄漏请求存在性、状态、结果或 payload digest；同时验证合法所有者可查询。
12. Retry classification：READ_ONLY、IDEMPOTENT_WRITE、NON_IDEMPOTENT、UNKNOWN_SEMANTICS 分别验证；仅有 correlation 或 result query 时，非幂等操作不得自动重放。
13. Filesystem semantics：CREATE_NEW、REPLACE、APPEND、CONDITIONAL_REPLACE；目标已存在/不存在、权限失败、非原子条件写、部分写入、跨卷 move、递归 remove 等边界。
14. Partial write and effect reporting：验证 NOT_APPLICABLE（只读）、FULLY_APPLIED、NOT_APPLIED、PARTIALLY_APPLIED、UNKNOWN 与执行状态分开表达；部分写入不能伪装为成功。
15. Chunk transfer lifecycle：begin 响应丢失恢复、相同块重试、不同内容同序号冲突、已确认块持久性、断线/重启恢复、长度/摘要校验，以及 abort/complete 并发竞争。
16. Credential Source and redaction：凭据不可用、过期、撤销；验证 Provider/audience/purpose 与主体上下文不匹配时拒绝绑定、多个匹配凭据时 fail closed、无静默环境变量/默认账户回退；验证不向 Agent 暴露凭据或存在性元数据，秘密不进入日志、异常、能力快照、请求状态或结果，且不会触发凭据探测或 Scope 扩大。
17. Provider Session close and resource cleanup：关闭一个 Session 不影响其他 Session 或共享 Runtime；关闭后行为明确；线程、文件句柄、临时传输记录按声明清理。
18. Runtime sharing and principal isolation：多个 Skill Session 共享 Runtime 时，协商版本/能力快照/请求上下文隔离；不同主体凭据与响应不串线；连接无法隔离主体时禁止跨主体复用。
19. Reconnect and renegotiation：验证短暂断线恢复、Provider 实例变化、能力快照过期、旧请求 UNKNOWN 保留；重连不触发自动非幂等重放。
20. Host Adapter lifecycle：卸载一个 Skill 只关闭其 Session；Runtime 只由所有者按资源策略关闭，且关闭不隐含远端取消。
21. Provider Factory configuration and selection：验证 provider_alias 只能解析受信配置；端点、audience、凭据来源不能由 Skill/模型覆盖；无配置、歧义或不允许回退时 fail closed。
22. Session bootstrap atomicity：Factory 返回未协商 Session 不代表 ready；Contract/Profile/Capability 任一步失败均不注册 Skill 操作入口，并释放本次 Session 资源。
23. Partial initialization cleanup：Runtime 创建、Provider 构造、Session 创建和 bootstrap 各阶段注入失败；验证独占资源释放且共享 Runtime 不被误关闭。
24. Skill unload/reload and concurrency：卸载/重载一个 Skill 不影响其他 Session；并发 Skill 实例拥有独立 Session；多主体不得共享主体上下文。
25. Host Adapter crash/shutdown：验证本地资源回收策略、未完成请求 UNKNOWN 保留，以及本地 shutdown 不产生远端取消/失败结论。
26. Provider Session state machine and concurrent close：验证 NEW/NEGOTIATING 时业务操作被拒绝，只有门控全部通过才 READY；close 幂等、并发关闭只释放一次，CLOSED 后不自动重建；close 与提交竞争时保留 UNKNOWN 语义。
27. Host Adapter feature negotiation：验证 Skill 必需 host_features 均满足后才注册入口；conditional 条件未满足时 fail closed；Host 专有 schema/对象不泄漏到 Skill API。
28. Multi-Host isolation：验证不同 Host Adapter 即使连接同一 Provider，也使用独立 Session/主体/凭据绑定；不得共享进程内 Runtime 或可变缓存，除非通过独立 Runtime Manager 安全验证。
29. Real Host compatibility matrix：分别验证真实 Host 产品/版本上的安装、加载、结构化调用、并发、重载、卸载和错误传播；Fake Host PASS 不得标记真实 Host PASS。
30. Cross-Host credential boundary：验证 Host 私有凭据机制只在 Adapter/Credential Source 内部，Token 不进入 Prompt、工具参数、Skill Manifest、普通日志或结果。
31. Skill Lifecycle state machine：验证 DISCOVERED/INITIALIZING 阶段不接受 invoke，initialize 失败清理资源，shutdown 幂等且 CLOSED 为终态。
32. Skill Invocation isolation：验证 invocation_id、caller_context、deadline、cancellation、输入/输出和 diagnostics 并发隔离；不支持并发时 Adapter 必须串行或拒绝。
33. Skill cancellation/deadline semantics：验证本地取消/超时不被解释为远端取消；可能已提交的请求保留原 client_request_id 和 UNKNOWN。
34. Skill Manifest/entrypoint validation：验证 operation 与结构化 Schema 匹配，拒绝未声明入口、任意 Host 对象和秘密配置字段。
35. Skill error mapping：验证稳定错误 code 在 Host Adapter 映射后保留，Transport UNKNOWN 不被改写为普通失败或未执行。
36. Skill package integrity/signature：验证 Manifest、代码、Schema、资源和锁定依赖摘要覆盖完整；签名/发布者信任失败或撤销状态不确定时，在加载代码前 fail closed。
37. Skill dependency graph：验证传递依赖完整锁定、循环/未声明依赖被拒绝，且不同 EDA Product Skills 之间不存在直接或间接依赖。
38. Skill configuration/secret boundary：验证配置 Schema、未知字段策略和 Credential Reference；秘密不进入普通配置、入口参数、日志或输出。
39. Skill package path containment：验证入口/Schema/资源只能引用包内路径，拒绝绝对路径、路径穿越、符号链接逃逸和 TOCTOU 替换。
40. Skill manifest/upgrade compatibility：验证 Manifest/API/依赖版本分别解析；升级前验证签名、摘要、依赖和配置，非原子切换时正确执行 drain/停机策略。
41. Package signature canonicalization：验证文件路径规范化、排序、大小写冲突、Unicode 冲突、重复条目和算法标识在不同 Host 上得到一致结果。
42. Publisher key rotation/revocation：验证受信渠道登记新密钥、轮换过渡、旧密钥退役、失陷撤销、生效范围和撤销状态防回滚。
43. Offline trust metadata freshness：验证离线安装所需信任/撤销快照、过期/缺失/回滚元数据时 fail closed，且 INDETERMINATE 不得记录为 trusted。
44. Build provenance separation：验证来源证明与签名分别校验，并绑定源提交、构建身份、依赖摘要和最终制品摘要；不受信 CI 不得调用发布签名权限。
45. Install trust decision/audit：验证 VERIFIED_TRUSTED/REJECTED/INDETERMINATE 三态和脱敏审计字段，且包信任 PASS 不会授予运行权限或 Transport Scope。

Fake Provider 必须通过同一套测试，但不能把“Fake 中可模拟”当成真实 Provider 已具备保证。测试报告应记录 Provider 名称/版本、能力快照、适用 operation_kind、跳过的可选能力测试及原因。

测试分层：

- Skill Unit Tests：使用 Fake Provider，不连接 AirBridge 或 EDA。
- Transport Contract Tests：验证 Provider 是否符合公共契约。
- AirBridge Provider Integration：验证 Provider 到 AirBridge 的真实 API 映射。
- Remote Runtime Integration：验证 Host A 到 Host B 的通用传输行为。
- EDA Product Integration：由对应独立 Skill 验证专业软件行为。
- Host Compatibility：验证不同 Agent Host 的包加载和调用适配。

任何一层 PASS 都不能替代其他层验收。

## 12. 当前 AirBridge 映射与差距（静态审计基线）

以下内容是当前仓库实现的初步静态审计，不是运行时验收结论。后续实现前应重新对照当时的 main 代码和 API 行为。

| 契约领域 | 当前观察 | v1 设计要求 |
|---|---|---|
| Shell 提交 | 已有 HTTP API | 映射到 ShellTransport |
| Shell 请求跟踪 | 已有 Request Tracker / 状态查询路径 | 核实保留期限、状态语义和去重保证 |
| Shell 去重 | 不能仅凭 Request ID 宣称完整去重 | 逐项验证键作用域、持久性、载荷冲突和并发行为 |
| RFS 文件操作 | `fs_engine.py` 提供 drives/list/read/write/mkdir/remove/move/stat 等同步原语；HTTP 路由通过现有 RFS 请求路径转发 | 可映射文件原语，但逐项核实路径边界、写模式、部分写入、原子性和删除语义 |
| RFS 请求 ID | RFS 载荷使用 `req_id`，由 Host A 的请求等待/串口响应路径关联；该 ID 不等同于持久化的契约 client_request_id/provider_request_id 映射 | 当前不声明通用 RequestTracking；先定义并验证映射或明确不支持 |
| RFS 去重/结果查询 | 静态审计未发现 RFS 操作接入 B 侧 RequestBroker；现有 req_id 响应关联不等于持久化去重或结果查询 | 不声明 request.deduplication、request.lookup_by_client_id 或 request.result_query 覆盖 RFS |
| RFS 写入语义 | 当前写接口以文本/ Base64 和覆盖/追加模式执行；没有契约级 CREATE_NEW/CONDITIONAL_REPLACE、写入原子性或部分写入回执模型 | 逐项声明实际 write mode 和 effect；未实现的模式必须拒绝 |
| RFS 删除/移动 | 当前 remove 对目录使用递归删除；move 使用 shutil.move，未在契约层声明跨卷原子性 | 需明确递归删除和跨文件系统语义，不能默认满足 v1 保证 |
| 分块传输 | Capture Skill 存在分块上传流程，但该流程通过 Shell staging/publish 与独立请求 ID 组织，不等同于 RFS 原生 chunked-transfer API | 不能据此声明 filesystem.chunked_transfer；需单独实现/验证完整 begin/put/status/complete/abort 契约 |
| 凭据来源 | 当前 Capture Skill 客户端直接处理 Bearer 凭据/环境变量 | 引入 Credential Source 抽象，但不扩大权限 |
| Fake Provider | 现有测试可进一步盘点 | 建立公共契约测试套件 |

### 12.1 静态代码证据与能力边界

以下观察来自本次对 `main` 分支代码的静态检查；只说明代码结构，不代表 Host A/Host B 运行验收：

- `request_broker.py`：B 侧 SQLite RequestBroker 对 UUID request_id 建立唯一记录；payload 使用排序 JSON 和 SHA-256 摘要；同 ID、不同 request_kind 或不同摘要返回冲突；相同请求返回 in-progress 或缓存结果。默认 TTL 为 24 小时、默认最多 10000 条记录，并有运行中请求心跳/过期转 UNKNOWN 逻辑。但容量达到上限时，Broker 会提前回收最旧的终态记录，因此 24 小时是 TTL 配置而不是无条件的最短保留承诺；Provider 必须把容量驱逐纳入保留保证。其 SQLite 主键是 request_id，记录本身未包含调用方主体字段；HTTP 层另有 Agent owner 绑定/读取检查，不能仅凭 Broker 表结构声称其自身实现了主体隔离。
- `airbridge_app.py`：EDA Execute 与 Shell Execute 路径接入 RequestBroker；因此不能将现有 Shell/EDA 请求保护简单描述为“只有客户端 UUID”。但其当前字段是单一 request_id，并非草案中的 client_request_id/provider_request_id 双 ID 模型。
- `request_tracker.py`：A 侧 RequestTracker 是内存中的 HTTP/ABFP 请求响应关联表，默认 TTL 15 分钟、最多 500 条；它与 B 侧持久化 RequestBroker 是不同层次，不应混为一个具有统一持久性保证的 Tracker。
- `agent_security.py` 与 HTTP API：存在 Agent 请求 owner 绑定和读取隔离检查；这证明部分 API 路径具备所有权控制实现，但不能替代对所有 Provider 操作类型、跨重启和请求记录保留边界的契约测试。
- `fs_engine.py`：RFS 的文件操作由独立同步原语执行；`dispatch` 使用 `req_id` 回传关联，文件操作本身没有接入上述 RequestBroker 的证据。RFS 读、写、删除、移动的行为还存在契约需要明确的限制（例如读取预览截断、追加模式、目录递归删除、跨卷移动）。
- Capture Skill 的分块上传记录每块请求 ID，并通过 Shell staging/publish 进行传输编排；这不等于 Provider 已提供通用 chunked-transfer 生命周期接口或分块服务端去重保证。

因此，本草案的当前 AirBridge 能力声明应采取保守映射：Shell 可继续验证现有 Broker 能力；RFS 的 correlation、lookup、dedup、result query 和 chunked transfer 均保持未声明/未证实，直至专门的 Provider conformance tests 通过。

### 12.2 迁移限制

- 本文不授权修改现有 Scope、Token、Credential Manager 或主机配置。
- 不授权绕过禁用的 EDA Execute。
- 不授权增加 EDA 专用 Core API。
- 不要求立即更改当前 Capture Skill 的生产调用路径。
- 在契约通过评审、AirBridge Provider 能力验证和 Fake Provider 测试前，不迁移生产 Skill。

## 13. 建议决策（待评审）

| 决策 | 建议 | 状态 |
|---|---|---|
| Provider 能力模式 | 采用可选能力声明；不支持时明确失败 | Proposed |
| RFS 请求关联 | 契约定义关联能力；Provider 仅在真实支持时声明 | Proposed |
| RFS 服务端去重 | 契约定义去重保证；不要求所有 Provider v1 立即支持 | Proposed |
| 非幂等操作重试 | 不允许盲目自动重放 | Proposed |
| UNKNOWN | 作为一等结果状态，保留原请求标识并只读核实 | Proposed |
| 凭据来源 | 抽象 Credential Source；不绑定单一 Host 或环境变量 | Proposed |
| 契约源文件位置 | 初期可与 AirBridge 同仓维护，但 Contract 与公共 Conformance Suite 独立版本化、发布；满足条件后再评估独立仓库/包 | Proposed |
| 版本协商 | Skill/Provider 显式声明已验证的完整 Contract 版本集合，初始化选择最高共同版本；无交集时 fail closed | Proposed |
| 发布制品 | 每个 Contract 版本发布不可变规范制品、内容摘要和对应 Conformance Suite | Proposed |
| 兼容矩阵 | 分别记录规范兼容、CI 契约测试和真实 Provider/Host 集成验收 | Proposed |
| 规范描述方式 | 规范文档定义语义，JSON Schema 校验结构，公共 Conformance Suite 验证行为；OpenAPI 仅作可选 Provider 绑定描述 | Proposed |
| 规范权威边界 | 三类材料职责分离；发生冲突时阻断该版本发布并通过正式勘误解决 | Proposed |
| v1 最小核心 | 所有 Provider 必须支持契约发现、版本协商、能力快照和关闭；Shell、Filesystem、RequestTracking、Chunked Transfer 均为可选能力 | Proposed |
| Optional Profiles | Shell、RequestTracking、Filesystem、Chunked Transfer 通过独立版本化 Profile 声明；Skill/Provider 分别声明所需/实现的完整版本集合 | Proposed |
| Provider 生命周期 | 每个 Skill/调用上下文独立 Provider Session；底层 Runtime 可选共享，Session close 不关闭共享 Runtime；Runtime 由 Host Adapter/Factory 所有者关闭 | Proposed |
| Provider 装配 | Provider Factory 属于 Host Adapter 装配层；受信 alias 解析、创建独立 Session；Contract/Profile/Capability bootstrap 成功前 Skill 不 ready | Proposed |
| Session 状态机 | Provider Session 采用 NEW/NEGOTIATING/READY/FAILED/CLOSING/CLOSED 本地状态；仅 READY 接受已授权操作，CLOSED 不可重开 | Proposed |
| 多 Host 兼容 | 同一 Skill 领域实现通过独立 Host Adapter 接入；以抽象 host_features 和真实 Host 兼容矩阵验证，不以 Host 名称分支领域逻辑 | Proposed |
| Skill API | Skill 通过语言无关 Lifecycle/Invocation Contract 暴露 initialize/invoke/shutdown；Host Adapter 映射宿主调用，Skill 不依赖 Host 私有对象 | Proposed |
| Skill 并发与取消 | 并发需显式声明并通过隔离测试；deadline/cancellation 仅约束本地调用，不隐含远端取消 | Proposed |
| Skill Package / Manifest 信任 | 包摘要、发布者签名、管理员信任根、依赖锁定、配置 Schema 和包内路径验证在加载代码前完成；不替代运行授权 | Proposed |
| Skill 依赖 | EDA Product Skills 不互相依赖；只允许受批准、锁定版本的通用基础库依赖 | Proposed |
| Skill 配置与秘密 | 普通配置经随包 Schema 验证；秘密仅经受控 Credential Source 绑定 | Proposed |
| Package Trust | 确定性摘要/签名信封、管理员信任根、密钥轮换/撤销、离线元数据新鲜度和安装三态结果 | Proposed |
| Build Provenance | 与制品签名分离验证；按部署策略绑定源提交、构建身份、依赖和最终制品摘要 | Proposed |
| 跨 Host 资源隔离 | 不同 Host 默认独立 Session/主体/凭据绑定，不假设共享进程内 Runtime；跨进程共享需独立受信 Runtime Manager，v1 不要求 | Proposed |
| 断线恢复 | 物理连接恢复不改变 UNKNOWN；Provider 身份/版本变化时新建 Session 并重新协商；重连不得隐式重放非幂等操作 | Proposed |

这些是供评审的建议，不应在未获批准前视为已冻结的架构决策。

## 14. 推荐实施顺序

1. 评审并冻结 v1 的请求状态、关联、去重、UNKNOWN 和重试语义。
2. 将能力模型、数据类型和错误模型形成可测试的接口定义。
3. 定义 Core Contract、Optional Profiles 及其版本/Conformance Suite 映射。
4. 定义 Provider Runtime、Session、Factory 装配、资源所有权、主体隔离和重连语义。
5. 建立 Fake Provider、Fake Factory 与公共 Transport Contract Conformance Suite。
6. 独立定义并评审 Skill Package/Manifest/Trust 规范，以及语言无关 Skill Lifecycle/Invocation Contract；分别确定版本、制品和兼容边界。
7. 建立 Fake Skill、Fake Host Adapter 与包完整性/签名、依赖图、配置秘密隔离、生命周期/并发/取消契约测试，再验证真实 Host 兼容矩阵。
8. 盘点并实现 AirBridge Provider；逐项标注真实支持能力，不伪造缺失能力。
9. 使用公共 Conformance Suite 验证 AirBridge Provider 与 Fake Provider 的契约一致性；真实 Provider 集成结果单独记录。
10. 通过兼容性测试后，迁移 OrCAD Capture Skill。
11. 后续独立迁移 OrCAD PCB Editor、Allegro、AD 等 Skills；它们之间不形成依赖。

## 15. 评审检查表

- [ ] Transport Contract 不包含任何 EDA 产品领域方法。
- [ ] OrCAD Capture 与 OrCAD PCB Editor 保持独立 Skill。
- [ ] Skill 不依赖 AirBridge Core 内部模块或 HTTP 路径。
- [ ] Provider 能力声明区分支持、不支持和条件支持。
- [ ] Correlation、Deduplication、Result Query 分开定义。
- [ ] 仅有 Correlation/Result Query 不会被当成 Deduplication。
- [ ] 提交响应丢失后，按 client_request_id 恢复映射的能力明确声明。
- [ ] client_request_id 与 provider_request_id 分开定义。
- [ ] 超时不会被解释为远端未执行。
- [ ] UNKNOWN 不会触发盲目重放。
- [ ] 非幂等操作没有未经验证的自动重试。
- [ ] 文件覆盖、追加、移动、删除的语义明确。
- [ ] 分块传输具备明确完整性与恢复规则，或明确不支持。
- [ ] Credential Source 不泄漏凭据，不扩大 Scope。
- [ ] Contract、Conformance Suite、Provider、Skill 和 Host Adapter 分别版本化。
- [ ] Skill 与 Provider 显式声明完整 Contract 版本集合，不使用 latest 或未经验证的宽泛范围。
- [ ] 初始化选择最高共同版本；无交集时在副作用操作前失败，不静默降级。
- [ ] 能力快照绑定本次协商的 Contract 版本和 Provider 身份。
- [ ] 已发布 Contract 制品不可变，并附内容摘要和对应 Conformance Suite。
- [ ] 兼容矩阵区分规范兼容、CI 契约测试和真实 Provider/Host 集成验收。
- [ ] 规范文档、JSON Schema 和公共 Conformance Suite 的权威范围清晰且互不替代。
- [ ] JSON Schema 方言版本及其与 Contract 版本的映射明确。
- [ ] 跨请求状态、去重、UNKNOWN 和副作用语义由公共行为测试验证，而非仅依赖 Schema。
- [ ] OpenAPI（如采用）只描述具体 Provider 绑定，不成为 EDA Skill 的核心契约依赖。
- [ ] 规范、Schema、测试套件和 fixtures 具有可追溯版本与内容摘要。
- [ ] Fake Provider 与 AirBridge Provider 使用相同契约测试。
- [ ] 文档中的目标能力没有被表述为当前已实现能力。
- [ ] v1 基础 Provider 接口仅包含契约发现、版本协商、能力快照和关闭。
- [ ] RuntimeInfo、Shell、Filesystem、RequestTracking 和 Chunked Transfer 不被误列为所有 Provider 的必需能力。
- [ ] 能力快照引导读取不依赖自身声明的可选能力，避免自举循环。
- [ ] 不兼容版本在副作用操作前失败，协商版本绑定当前 Provider 会话。
- [ ] OperationResult 与 RequestStatus 必须保留必填 client_request_id；provider_request_id 可选。
- [ ] RequestReference 至少包含一个请求标识；双 ID 同时提供时必须验证映射一致。
- [ ] RequestLookupResult 分别声明状态查询与结果查询是否可用。
- [ ] 只读操作使用 NOT_APPLICABLE；副作用结果必须报告实际 effect。
- [ ] TransportError 与 OperationResult.error 的适用边界明确；retryable 不单独授权重试。
- [ ] CapabilitySnapshot 绑定 provider_id、provider_instance_id、实现版本和协商 Contract 版本。
- [ ] Core Contract 与 Optional Profile 分别版本化，不共享隐式版本号。
- [ ] Skill 与 Provider 对 Profile 声明已验证的完整版本集合。
- [ ] 必需 Profile 无共同版本时初始化失败；已协商 Profile 版本在会话内固定。
- [ ] Profile 声明与运行时 CapabilitySnapshot 一致；Profile 实现不推导其全部可选能力受支持。
- [ ] Skill 的 required_profiles、optional_profiles 和 required_capabilities 语义明确。
- [ ] Profile Conformance Suite 独立版本化，并与 Profile 版本建立映射。
- [ ] Provider Runtime 与 Provider Session 的生命周期、所有权和共享边界明确。
- [ ] 每个 Skill/调用主体拥有隔离的 Session 协商状态、能力快照和请求上下文。
- [ ] Session close 不会关闭其他 Session 使用的共享 Runtime，也不隐含远端取消。
- [ ] Credential Binding 和连接复用不会跨主体泄漏认证上下文或响应。
- [ ] 重连不改变原请求 ID/UNKNOWN；Provider 实例或契约版本变化时重新协商。
- [ ] Runtime shutdown 由 Host Adapter/Provider Factory 所有者管理，不暴露为 Skill 操作。
- [ ] Provider Factory 属于 Host Adapter 装配层，不属于 Transport Contract Core。
- [ ] Provider 选择只使用受信 provider_alias/配置，不接受模型自由文本指定 endpoint 或凭据来源。
- [ ] Session 创建与 bootstrap 分离；Contract/Profile/Capability 门控通过前 Skill 不标记 ready。
- [ ] 初始化失败时释放本次独占资源，不误关闭共享 Runtime。
- [ ] Skill 卸载/重载和并发实例不会串用 Session 或调用主体。
- [ ] Host Adapter 崩溃/关闭不把本地退出解释为远端取消或请求失败。
- [ ] Provider Session 生命周期状态与远端 RequestState 分离；只有 READY 接受操作，close 幂等且 CLOSED 为终态。
- [ ] Skill 与 Agent Host 通过独立 Host Adapter 解耦，同一 Skill 不复制 Host/EDA 领域实现。
- [ ] Host Adapter 声明抽象 host_features；Skill 必需功能未满足时不得注册可执行入口。
- [ ] 不同 Host 默认不共享进程内 Runtime、Session、Credential Source 实例或可变缓存。
- [ ] Host/Adapter/Skill/Provider/Contract 兼容矩阵分别记录规范、契约测试和真实 Host 验收结果。
- [ ] Fake Host Adapter PASS 不得替代真实 Agent Host 兼容验收。
- [ ] Skill Lifecycle/Invocation Contract 作为独立版本化规范评审和发布，不纳入 Transport Contract v1 的规范范围、版本协商或发布制品。
- [ ] Skill API 版本与 Transport Contract 版本分别协商、分别记录，不共享隐式版本号。
- [ ] Skill 只接收 Host Adapter/Factory 预先装配的 Provider Session，不接收 Factory、Runtime Manager 或 Provider 选择能力。
- [ ] caller_context 只能由 Host Adapter 作为可信调用元数据注入，不能由 Agent/模型通过 input 或工具参数伪造。
- [ ] Skill Lifecycle/Invocation Contract 不暴露 Host/Provider 私有对象。
- [ ] Skill Manifest 声明版本、入口、结构化输入输出 Schema 和并发策略。
- [ ] Skill Package 完整性摘要覆盖 Manifest、代码、Schema、资源和锁定依赖；签名在加载代码前由管理员信任根验证。
- [ ] 签名/发布者信任不被误认为 Skill 运行授权、Transport Scope 或 EDA 操作授权。
- [ ] EDA Product Skills 不存在直接或间接依赖；共享库仅承载通用基础设施。
- [ ] 依赖图完整锁定版本和制品摘要，拒绝未声明依赖、循环和运行时静默下载/升级。
- [ ] 配置 Schema 随包发布并验证；秘密仅通过受控 Credential Source 绑定，不进入普通配置或日志。
- [ ] Manifest 入口、Schema 和资源引用均限制在已验证包根目录内，拒绝路径穿越和符号链接逃逸。
- [ ] Host Adapter 在代码加载/入口注册前完成签名、摘要、Manifest、依赖、配置和兼容性验证。
- [ ] 包文件清单采用确定性路径规范化、排序和编码；拒绝重复路径、大小写/Unicode 冲突及平台路径重定向。
- [ ] 签名信封绑定包身份、规范化 Manifest 摘要、完整文件清单摘要、publisher identity 和 key identifier；不接受算法降级或未知关键字段。
- [ ] 发布者密钥轮换通过管理员可信渠道登记；正常退役与失陷撤销具有不同语义，撤销元数据防回滚。
- [ ] 离线安装验证信任/撤销元数据来源、版本、新鲜度和防回滚；INDETERMINATE 不得作为可信 PASS。
- [ ] 构建来源证明与制品签名分别验证；来源证明绑定源提交、构建器、依赖和最终制品摘要。
- [ ] 安装信任验证结果区分 VERIFIED_TRUSTED、REJECTED、INDETERMINATE，并明确不授予运行授权或 Transport Scope。
- [ ] Skill 升级验证新制品后再切换入口；非原子切换时明确停机窗口和在途调用策略。
- [ ] initialize/invoke/shutdown 生命周期状态和失败清理规则明确。
- [ ] deadline/cancellation 只表达本地调用语义，不隐含远端请求取消。
- [ ] invocation_id 与 Transport client_request_id 分离，调用关联不替代远端请求幂等/恢复。
- [ ] SkillError 稳定 code 不吞掉底层 TransportError/OperationResult/UNKNOWN 语义。
- [ ] 本文评审通过前，不进行生产 Skill 迁移。

---
