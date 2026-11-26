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

## 3. 版本与兼容性

建议使用独立语义版本：

- MAJOR：不兼容的接口、状态或保证语义变化。
- MINOR：新增可选能力、字段或接口，不改变既有保证。
- PATCH：文档澄清、兼容性修正和不改变契约语义的错误修订。

Provider 必须声明其实现的 Contract 版本范围。Skill 初始化时校验版本兼容性；不兼容时在任何远端副作用操作之前失败。

v1 不要求所有 Provider 支持所有可选能力。必需的基础接口与可选能力必须区分；不得通过返回成功、空结果或虚构默认值伪装能力。

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
| `get_capabilities()` | `runtime.capabilities.read`（初始化协商所需的基础能力） | 能力快照本身必须可读取；不能用快照内的能力声明循环授权首次读取 |
| `get_runtime_info()` | `runtime.identity.read` | 只描述通用运行时身份；若 Provider 无此信息，应明确不支持，不得用 EDA 版本信息代替 |
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

## 5. 核心接口草案

以下为语言无关的逻辑接口，不限定 Python、TypeScript 或具体包布局。

### 5.1 Provider 生命周期

```text
TransportProvider
  get_contract_info() -> ContractInfo
  get_capabilities(refresh=false) -> CapabilitySnapshot
  get_runtime_info() -> RuntimeInfo
  close() -> None

RequestTracking (optional capability; applies to any request-bearing transport operation)
  find_by_client_request_id(operation_kind, client_request_id) -> RequestLookupResult
  get_request_status(operation_kind, provider_request_id) -> RequestStatus
  get_request_result(operation_kind, provider_request_id) -> OperationResult
```

`RuntimeInfo` 仅描述通用远端运行环境身份与版本，不包含 EDA 生命周期信息。

### 5.2 Shell

```text
ShellTransport
  submit(command, options, client_request_id) -> OperationResponse
  // Shell-specific convenience aliases for RequestTracking; same semantics and authorization.
  get_status(provider_request_id) -> RequestStatus  // operation_kind is implicitly SHELL
  get_result(provider_request_id) -> OperationResult // operation_kind is implicitly SHELL

```

约束：

- Shell 是通用远端命令能力，不提供 Capture/Allegro 专用方法。
- 命令执行必须由 Provider 的授权策略和后端权限控制；客户端接口不能取代服务端授权。
- `get_status` 与 `get_result` 是 Shell 专属便捷别名，等价于 `RequestTracking.get_request_status(SHELL, provider_request_id)` 与 `get_request_result(SHELL, provider_request_id)`；不得定义不同的状态或授权语义。相应查询能力必须由 `shell.status_query` / `shell.result_query` 或适用的通用请求跟踪能力明确声明。
- `request.lookup_by_client_id`、`request.status_query` 和 `request.result_query` 是通用请求跟踪能力，适用于 Provider 明确声明支持跟踪的请求类型；它们不隐含所有 Shell/RFS 操作都支持查询。若同时声明通用能力和 Shell 专属能力，两者必须满足相同的语义保证，不得产生冲突。
- Shell 专属能力标识（如 `shell.status_query`、`shell.result_query`）只描述 Shell 操作范围；通用请求跟踪能力不能仅凭 Shell 能力推断，也不能反向推断。
- `find_by_client_request_id` 用于提交响应丢失、调用方未取得 `provider_request_id` 时恢复原请求映射；它不是重新提交操作。
- 查询结果必须区分“找到原请求”“权威确认不存在”和“无法判断”；不具备权威查询保证时不得返回“未提交”。
- 建议将结果结构固定为：
  ```text
  RequestLookupStatusAvailability = AVAILABLE | UNAVAILABLE

  RequestLookupResult =
    FOUND {
      client_request_id,
      provider_request_id?,
      status?,
      status_availability, // RequestLookupStatusAvailability
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
  provider_name,
  supported_contract_versions[],
  selected_contract_version
}

CapabilitySnapshot {
  provider_instance_id,
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
  client_request_id?,
  provider_request_id,
  state,             // RequestState; NOT_SUBMITTED is caller-local and not returned by Provider query
  observed_at,
  authority,         // authoritative | non_authoritative
  accepted_at?,
  started_at?,
  completed_at?
}

OperationCompletion = COMPLETED_SUCCESS | COMPLETED_FAILURE | UNKNOWN

OperationEffect = NOT_APPLIED | FULLY_APPLIED | PARTIALLY_APPLIED | UNKNOWN

OperationResult {
  operation_kind,
  client_request_id?,
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
  retryable,
  outcome,           // not_executed | executed_failed | unknown | not_applicable
  request_ids?,
  details?
}
```

类型约束：

- `Submission.ACCEPTED` 仅表示 Provider 已确认接受，不表示操作已完成；`provider_request_id` 若尚不可用可省略，但此时 Provider 必须说明是否可通过 `client_request_id` 恢复映射。
- `Submission.REJECTED` 仅可用于能保证请求未执行的拒绝；否则返回 `SUBMISSION_UNKNOWN`。
- `Submission.SUBMISSION_UNKNOWN` 不得携带暗示失败或未执行的默认值。调用方必须保留原 `client_request_id`。
- `CapabilitySnapshot.contract_version` 必须与当前协商选定的契约版本一致；快照不得在不同 Contract 版本之间复用。
- `RequestStatus` 只能由 Provider 对已提交请求返回；`NOT_SUBMITTED` 是调用方本地状态，不是远端可查询状态。Provider 无法确认状态时返回 `UNKNOWN` 或非权威观察，不能构造一个看似确定的状态。
- `RequestTracking` 的 `operation_kind` 是请求命名空间的一部分；`provider_request_id` 只在该操作类型和 Provider 声明的身份/命名空间范围内解释。
- `RequestStatus.authority=non_authoritative` 的观察不能单独将请求推进为已确认状态；它只能作为诊断信息。权威等级不能由调用方自行提升。
- `OperationResult.effect` 与 `OperationResult.state` 必须分开解释：前者描述副作用实际达到的程度，后者描述执行是否完成/成功。特别是 `COMPLETED_FAILURE` 可与 `PARTIALLY_APPLIED` 或 `UNKNOWN` 并存。
- `OperationResult.state=COMPLETED_FAILURE` 不代表没有副作用；必须通过 `effect` 表达 `NOT_APPLIED`、`PARTIALLY_APPLIED` 或 `UNKNOWN`。只有能证明完整目标效果已达成时才使用 `FULLY_APPLIED`。若最终执行状态无法确认，使用 `state=UNKNOWN`，并按证据设置 `effect`。
- `OperationResponse` 允许 Provider 在操作已同步完成时直接返回 `OperationResult`；若操作仍在执行或提交结果不确定，则返回 `Submission`。两种路径都必须保留相同的 `client_request_id` 和结果语义。
- `payload` 只承载对应操作的结果数据；通用层不得把 Shell stdout、文件字节或其他操作数据误解为 EDA 领域对象。具体 payload schema 应由通用操作类型定义，不能由 Provider 私自改变。
- `TransportError.retryable=true` 只表示按本契约自动重试不会违反操作语义及 Provider 保证；它不是“网络错误可再次尝试”的同义词。
- 所有类型均不得包含 Token、Authorization Header、私钥或其他凭据。可选字段缺失必须与空字符串、空对象或“已确认不存在”区分。

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

1. Contract version compatibility：兼容版本可初始化；不兼容版本在任何副作用提交前失败。
2. Capability declaration and operation gating：每个接口与能力映射一致；unsupported、conditional 条件不满足、能力未知或快照过期时，副作用操作在提交前被拒绝。
3. Request ID preservation and provider ID mapping：首次提交、查询、恢复均保留原 client_request_id；验证 provider_request_id 映射及其作用域。
4. Lookup after lost submission response：提交响应丢失且调用方尚无 provider_request_id 时，按原 client_request_id 查找；验证 FOUND、AUTHORITATIVE_NOT_FOUND、INDETERMINATE 三种结果的边界。
5. Same-key/same-payload and same-key/different-payload：仅在声明 dedup 时验证缓存/原请求返回、载荷冲突拒绝及并发重复提交；验证 request_kind、目标资源和语义选项参与 payload digest。
6. Deduplication retention and expiry：保留期内重复提交不产生第二次副作用；记录过期后不得虚构 AUTHORITATIVE_NOT_FOUND 或安全重放保证。
7. Submission interruption：提交调用在请求可能已离开调用方后中断，状态进入 SUBMISSION_UNKNOWN；不得把本地异常映射成 REJECTED。
8. Synchronous and asynchronous providers：同步完成直接返回 OperationResult；异步提交返回 Submission；两条路径保留一致的请求 ID、状态和副作用语义。
9. Status/result query consistency：分别验证状态查询和结果查询；只支持其中之一时，另一接口必须明确拒绝。
10. UNKNOWN reconciliation：查询超时、断线、重启和旧快照不得把 UNKNOWN 自动变成失败或未提交；只有权威证据才能推进状态，已确认终态不得被旧观察回退。
11. Ownership isolation：跨 Agent/主体猜测 request ID 时，不能泄漏请求存在性、状态、结果或 payload digest；同时验证合法所有者可查询。
12. Retry classification：READ_ONLY、IDEMPOTENT_WRITE、NON_IDEMPOTENT、UNKNOWN_SEMANTICS 分别验证；仅有 correlation 或 result query 时，非幂等操作不得自动重放。
13. Filesystem semantics：CREATE_NEW、REPLACE、APPEND、CONDITIONAL_REPLACE；目标已存在/不存在、权限失败、非原子条件写、部分写入、跨卷 move、递归 remove 等边界。
14. Partial write and effect reporting：验证 FULLY_APPLIED、NOT_APPLIED、PARTIALLY_APPLIED、UNKNOWN 与执行状态分开表达；部分写入不能伪装为成功。
15. Chunk transfer lifecycle：begin 响应丢失恢复、相同块重试、不同内容同序号冲突、已确认块持久性、断线/重启恢复、长度/摘要校验，以及 abort/complete 并发竞争。
16. Credential Source and redaction：凭据不可用、过期、撤销；验证 Provider/audience/purpose 与主体上下文不匹配时拒绝绑定、多个匹配凭据时 fail closed、无静默环境变量/默认账户回退；验证不向 Agent 暴露凭据或存在性元数据，秘密不进入日志、异常、能力快照、请求状态或结果，且不会触发凭据探测或 Scope 扩大。
17. Provider close and resource cleanup：关闭后行为明确；线程、文件句柄、临时传输记录按声明清理。

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
| 契约仓库位置 | 初期与 AirBridge 同仓管理，逻辑独立版本化；稳定后再评估独立包 | Proposed |

这些是供评审的建议，不应在未获批准前视为已冻结的架构决策。

## 14. 推荐实施顺序

1. 评审并冻结 v1 的请求状态、关联、去重、UNKNOWN 和重试语义。
2. 将能力模型、数据类型和错误模型形成可测试的接口定义。
3. 建立 Fake Provider 与公共 Contract Tests。
4. 盘点并实现 AirBridge Provider；逐项标注真实支持能力，不伪造缺失能力。
5. 在独立测试中验证 Provider 与 Fake Provider 的契约一致性。
6. 通过兼容性测试后，迁移 OrCAD Capture Skill。
7. 后续独立迁移 OrCAD PCB Editor、Allegro、AD 等 Skills；它们之间不形成依赖。

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
- [ ] Fake Provider 与 AirBridge Provider 使用相同契约测试。
- [ ] 文档中的目标能力没有被表述为当前已实现能力。
- [ ] 本文评审通过前，不进行生产 Skill 迁移。

---
