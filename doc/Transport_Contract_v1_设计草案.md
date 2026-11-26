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
| `limits` | 大小、超时、并发、路径等限制 |
| `notes` | 可选的非规范说明 |

能力声明应采用显式枚举，不应只用一个布尔值表达具有不同保证等级的行为。

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

## 5. 核心接口草案

以下为语言无关的逻辑接口，不限定 Python、TypeScript 或具体包布局。

### 5.1 Provider 生命周期

```text
TransportProvider
  get_contract_info() -> ContractInfo
  get_capabilities(refresh=false) -> CapabilitySnapshot
  get_runtime_info() -> RuntimeInfo
  close() -> None
```

`RuntimeInfo` 仅描述通用远端运行环境身份与版本，不包含 EDA 生命周期信息。

### 5.2 Shell

```text
ShellTransport
  submit(command, options, client_request_id) -> Submission
  get_status(provider_request_id) -> RequestStatus
  get_result(provider_request_id) -> OperationResult

RequestTracking (optional capability)
  find_by_client_request_id(client_request_id) -> RequestLookupResult
```

约束：

- Shell 是通用远端命令能力，不提供 Capture/Allegro 专用方法。
- 命令执行必须由 Provider 的授权策略和后端权限控制；客户端接口不能取代服务端授权。
- `get_status` 与 `get_result` 是可选能力，必须由能力声明确认。
- `request.lookup_by_client_id` 是独立可选能力，适用于任何声明支持请求跟踪的操作，不限于 Shell。
- `find_by_client_request_id` 用于提交响应丢失、调用方未取得 `provider_request_id` 时恢复原请求映射；它不是重新提交操作。
- 查询结果必须区分“找到原请求”“权威确认不存在”和“无法判断”；不具备权威查询保证时不得返回“未提交”。
- 若后端不能按客户端请求 ID 查询，Provider 必须明确声明不支持该恢复能力；调用方不能因此生成新 ID 并重放非幂等请求。
- 如果后端仅支持同步执行，Provider 可以返回已完成结果，但不得伪造异步任务 ID 或状态查询能力。
- Provider 必须明确命令超时是“停止等待”还是“远端执行已被终止”。客户端等待超时不能被解释为远端执行已停止。

### 5.3 Filesystem / RFS

建议先定义通用原语：

```text
FilesystemTransport
  list(path, options, client_request_id) -> OperationResult
  stat(path, client_request_id) -> OperationResult
  read(path, range, client_request_id) -> OperationResult
  write(path, content, write_mode, options, client_request_id) -> OperationResult
  mkdir(path, options, client_request_id) -> OperationResult
  move(source, destination, options, client_request_id) -> OperationResult
  remove(path, options, client_request_id) -> OperationResult
```

约束：

- 路径语义、路径根限制、覆盖策略、权限和符号链接处理必须由 Provider 明确声明。
- `write_mode` 至少区分覆盖与追加；追加属于非幂等操作，不能默认安全重试。
- 目录、文件不存在、目标已存在等情况必须有稳定错误分类。
- 文件读取结果应能表达长度及内容摘要；若后端无法提供摘要，应明确标记而非生成未经验证的摘要。
- 上传/下载的大文件分块机制单独定义，不能把多次普通 `write` 自动视为可靠分块协议。

### 5.4 大文件传输扩展

若声明 `filesystem.chunked_transfer`，建议提供：

```text
begin_transfer(metadata, client_request_id) -> TransferSession
put_chunk(transfer_id, index, bytes, chunk_digest) -> ChunkReceipt
get_transfer_status(transfer_id) -> TransferStatus
complete_transfer(transfer_id, expected_digest) -> TransferReceipt
abort_transfer(transfer_id) -> AbortReceipt
```

必须明确：

- transfer ID 的作用域、有效期和所有权；
- 分块大小、总块数、序号规则；
- 相同块重复提交的行为；
- 不同内容重复提交相同块序号时的冲突行为；
- 已确认块的持久性及查询方式；
- 完成时完整文件长度与摘要校验；
- 中断后恢复和清理策略；
- abort 与 complete 竞争时的结果语义。

如果 Provider 不支持这些保证，就不得声明可恢复的分块传输能力。

## 6. 请求标识、关联与幂等性

### 6.1 两种请求标识

- `client_request_id`：由调用 Skill 生成，在一次逻辑操作的所有查询和任何经授权的重复提交中保持不变。
- `provider_request_id`：由 Provider/后端生成，用于映射到后端请求或任务。

两者不得混为一谈。Provider 必须说明两者映射关系是否持久、是否可查询，以及映射保留期限。若提交响应丢失，调用方可能只有 `client_request_id`，因此契约必须明确是否支持按该 ID 恢复映射；若不支持，结果保持未知，不能假设后端未接收请求。

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
- 去重记录过期后的行为。

建议将规范化请求的 payload digest 与请求标识绑定。相同 key 对应不同 digest 必须明确拒绝，不能悄悄执行第二个请求。

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
| `NOT_SUBMITTED` | 已确认请求尚未交给 Provider；或在本地校验阶段被拒绝且没有发生提交 |
| `SUBMISSION_UNKNOWN` | 提交调用发生中断，无法确认后端是否接受 |
| `ACCEPTED` | Provider 明确确认已接受请求 |
| `RUNNING` | 后端明确报告正在执行 |
| `COMPLETED_SUCCESS` | 已确认执行成功 |
| `COMPLETED_FAILURE` | 已确认执行完成但失败 |
| `REJECTED` | 后端明确拒绝，且能保证没有执行 |
| `UNKNOWN` | 无法确认请求是否执行或最终结果 |

Provider 可将状态映射为更少的内部状态，但不得丢失“已确认”与“未知”的语义差异。

### 7.2 UNKNOWN 处理

UNKNOWN 是一种真实结果，不是普通异常，也不等于失败。

建议状态转换约束：

- `NOT_SUBMITTED -> ACCEPTED`：Provider 明确确认接受。
- `NOT_SUBMITTED -> REJECTED`：Provider 明确拒绝且保证未执行。
- `NOT_SUBMITTED -> SUBMISSION_UNKNOWN`：提交调用中断，接受情况无法确认。
- `ACCEPTED -> RUNNING`：后端明确报告开始执行。
- `ACCEPTED/RUNNING -> COMPLETED_SUCCESS/COMPLETED_FAILURE`：取得权威终态结果。
- 任一已提交但无法核实的状态可转入 `UNKNOWN`。
- `UNKNOWN` 不能仅因查询超时、连接恢复或本地重启而自动转成失败或未提交；只能由权威查询结果或业务层证据更新。

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

建议契约测试套件：

1. Contract version compatibility。
2. Capability declaration and unsupported operation behavior。
3. Request ID preservation and provider ID mapping。
4. Lookup by client_request_id after a lost submission response（仅在声明支持时）。
5. Same-key/same-payload and same-key/different-payload behavior（仅在声明 dedup 时）。
6. Submission interruption and SUBMISSION_UNKNOWN。
7. Status/result query consistency（仅在声明支持时）。
8. UNKNOWN does not trigger blind resubmission。
9. Read-only / idempotent / non-idempotent retry policy。
10. File path, overwrite, append and conflict semantics。
11. Chunk duplicate, conflicting chunk, integrity failure and resume semantics（仅在声明 chunked transfer 时）。
12. Error redaction and credential non-disclosure。
13. Provider close and resource cleanup。

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
| RFS 读写 | 已有同步 RFS 路径 | 映射通用文件操作 |
| RFS 请求 ID | Host A 内部使用自己的整数请求 ID；Skill 传入 ID 不等同于服务端关联 ID | 定义 client/provider ID 映射或明确声明不支持 |
| RFS 去重/结果查询 | 当前审计未证实具备与 Shell 等价的保证 | 不声明支持，除非补充实现并验证 |
| 分块传输 | Skill 有分块上传流程 | 与后端确认、完整性校验、恢复语义分开审计 |
| 凭据来源 | 当前 Capture Skill 客户端直接处理 Bearer 凭据/环境变量 | 引入 Credential Source 抽象，但不扩大权限 |
| Fake Provider | 现有测试可进一步盘点 | 建立公共契约测试套件 |

### 12.1 迁移限制

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
