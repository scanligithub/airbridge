# AirBridge Runtime Session Protocol v1

- 文档编号：AB-PROTO-SESSION-001
- 版本：1.0
- 状态：**P1.2–P1.6 当前实现均已通过 GitHub Actions 软件 CI（P1.6 Run #76，80 项测试通过）；Session Create / Close 专用消息、Process Supervisor 与真实 Capture 17.2 实机复验仍待完成**
- 日期：2026-11-12
- 关联架构：AirBridge 通用 Runtime Session 架构设计 v1.0

> 本文件定义消息语义、JSON Envelope、ABFP 帧分配和北向 HTTP API。P1.2 已实现 0x15 / 0x16 能力协商；P1.3 在现有 EDA / Shell 帧的 JSON Payload 中增加 request_id 回显与 A 端 HTTP 结果关联；P1.4 增加通用 B 端持久化 Request 去重；P1.5 增加本机 Runtime Adapter / Profile；P1.6 将 Capture Tcl 接入本机持久 Bridge。P1.5 / P1.6 均未新增 Session Create / SESSION_EXEC 帧，统一 Session 生命周期协议仍为后续阶段。

---

## 1. 协议目标

统一主机 A Agent Skill 到主机 B Runtime Adapter 的 Session 生命周期和请求执行接口。

协议不定义专业软件命令语义。Tcl、SKILL、Python 或其他脚本内容都作为不透明 Payload 传递，由对应 Runtime Adapter 解释。

## 2. 现有 ABFP 基线

当前 protocol.py 的关键参数：

- 二进制帧协议：ABFP；
- 当前协议版本字段：0x01；
- 帧头：AA 55；
- 帧尾：55 AA；
- 当前单帧最大 Payload：65000 字节；
- seq_id：16 位传输帧序号；
- 现有 EDA 消息：0x10 至 0x14；
- 现有 RFS 消息：0x20、0x21；
- 现有 Shell 消息：0x30 至 0x35；
- P1.2 Runtime Session 握手：0x15、0x16。

**ABFP seq_id 只用于传输帧，不得作为 Session ID 或 Request ID。** 业务关联 ID 必须使用独立 UUID 字符串。

## 3. 通用 JSON Envelope

所有 Session 业务消息使用 UTF-8 JSON。建议公共 Envelope：

~~~json
{
  "schema_version": 1,
  "message_id": "UUID",
  "correlation_id": null,
  "session_id": null,
  "request_id": null,
  "sent_at_utc": "2026-11-12T01:00:00.000Z",
  "payload": {}
}
~~~

字段：

| 字段 | 必需性 | 说明 |
|---|---|---|
| schema_version | 必需 | JSON 消息结构版本；与 ABFP frame version 分开 |
| message_id | 必需 | 当前消息唯一 ID |
| correlation_id | 条件必需 | 响应消息引用对应请求的 message_id |
| session_id | 条件必需 | 目标会话 UUID；创建请求可携带客户端预生成的 ID |
| request_id | 条件必需 | 执行请求 UUID |
| sent_at_utc | 必需 | UTC 时间 |
| payload | 必需 | 消息类型专属字段 |

接收端应拒绝未知的 schema_version，或按协商能力明确降级；不得静默按另一版本解释。

## 4. Session 消息

### 4.1 SESSION_HELLO（FrameType 0x15）

任一端 → 对端。连接建立后，A/B 两端均可主动发送 HELLO；接收端必须返回 CAPABILITIES。

Payload 示例：

~~~json
{
  "schema_version": 1,
  "message_id": "UUID",
  "correlation_id": null,
  "session_id": null,
  "request_id": null,
  "sent_at_utc": "2026-11-12T01:00:00.000Z",
  "payload": {
    "supported_protocols": ["runtime-session.v1"],
    "client_version": "airbridge-v2.4",
    "max_frame_payload": 65000
  }
}
~~~

P1.2 已实现。收到 HELLO 的节点会返回 CAPABILITIES；两端在连接建立后也会各自发起一次自动协商，因此不依赖固定的发起端角色。重试在后台线程执行，不占用 HTTP 请求线程。自动重试绑定当前串口连接代次；断开 / 重连会使旧握手失效，防止旧线程在新链路上继续重试。

### 4.2 SESSION_CAPABILITIES（FrameType 0x16）

接收 HELLO 的一端 → 发起端。通过 correlation_id 引用原 HELLO 的 message_id，返回协议、功能、Profiles 和限制。

~~~json
{
  "schema_version": 1,
  "message_id": "UUID",
  "correlation_id": "HELLO.message_id",
  "session_id": null,
  "request_id": null,
  "sent_at_utc": "2026-11-12T01:00:00.000Z",
  "payload": {
    "supported_protocols": ["runtime-session.v1"],
    "features": ["handshake", "request.correlation", "request.deduplication"],
    "profiles": [],
    "server_version": "airbridge-v2.4",
    "max_frame_payload": 65000
  }
}
~~~

P1.2 提供 `handshake`，P1.3 增加 `request.correlation`，P1.4 增加 `request.deduplication`。P1.5 已在每台 AirBridge 主机的本地执行层实现 RuntimeAdapterRegistry，并注册旧版 EDA / Shell 兼容 Profile；但当前 `profiles` 仍为空。这是有意的安全边界：本机注册信息不能证明对端已安装某个 Adapter，也不能证明其专业软件版本、运行环境和执行 Bridge 已就绪。只有后续阶段实现主机级 Profile 发现与实测验证后，才可通过 CAPABILITIES 广播可用 Profile。`runtime-session.v1` 标识 Envelope / 协议族版本，实际已实现通用协议功能以 `features` 为准，具体运行环境能力以经过验证的 Profile 元数据为准。

### 4.3 SESSION_CREATE（建议 FrameType 0x17）

A → B。请求创建一个 Session。A 端可预生成 session_id，便于在创建响应丢失时查询同一 ID。

~~~json
{
  "profile_id": "capture-17.2",
  "startup_options": {
    "startup_timeout_ms": 60000,
    "exclusive": true
  },
  "client_metadata": {
    "skill_id": "capture-skill",
    "skill_version": "x.y"
  }
}
~~~

B 端应先检查 Profile、独占组和进程策略，再依次执行旧进程清理、启动和 Bridge READY 握手。

### 4.4 SESSION_EVENT（建议 FrameType 0x18）

B → A。异步报告 Session 状态变化，例如 CREATING、STARTING、READY、FAILED、LOST。

~~~json
{
  "event_id": "UUID",
  "state": "READY",
  "runtime": {
    "pid": 7476,
    "process_path": "D:/Cadence/SPB_17.2/tools/bin/Capture.exe",
    "process_created_at": "2026-11-12T01:21:03.000Z",
    "runtime_version": "17.2",
    "adapter_version": "0.1"
  },
  "error": null
}
~~~

READY 必须匹配本次 Session 的 session_id 和 startup_nonce，并通过进程身份交叉验证。

## 5. Request 消息

### 5.1 SESSION_EXEC（建议 FrameType 0x19）

A → B。提交一个 Runtime 请求。

~~~json
{
  "operation": "execute",
  "timeout_ms": 60000,
  "payload": {
    "content_type": "text/x-tcl",
    "encoding": "utf-8",
    "body": "puts [pid]"
  },
  "payload_sha256": "hex-sha256",
  "metadata": {
    "skill_id": "capture-skill",
    "step": "query-active-design"
  }
}
~~~

对大脚本不直接塞入超过帧上限的 JSON。应通过现有文件传输 / RFS 机制传递 Artifact 引用及 SHA-256，再由 B 端读取并校验。

### 5.2 SESSION_ACCEPTED（建议 FrameType 0x1A）

B → A。表示请求已登记，不代表脚本已执行成功。

~~~json
{
  "request_state": "QUEUED",
  "queue_position": 1,
  "accepted_at_utc": "2026-11-12T01:22:00.000Z"
}
~~~

### 5.3 SESSION_RESULT（建议 FrameType 0x1B）

B → A。返回请求状态或最终结果。

成功示例：

~~~json
{
  "status": "SUCCEEDED",
  "started_at_utc": "2026-11-12T01:22:00.100Z",
  "completed_at_utc": "2026-11-12T01:22:00.220Z",
  "duration_ms": 120,
  "runtime_pid": 7476,
  "result": {
    "content_type": "text/plain",
    "body": "7476"
  },
  "stdout": "",
  "stderr": "",
  "error": null
}
~~~

失败示例：

~~~json
{
  "status": "FAILED",
  "runtime_pid": 7476,
  "result": null,
  "error": {
    "code": "RUNTIME_EXECUTION_ERROR",
    "message": "Command Requires SchematicView",
    "stack": "runtime stack trace",
    "retryable": false
  }
}
~~~

Result 应提供运行 PID、真实执行状态、返回值、stdout/stderr（如 Adapter 支持）和结构化错误。

## 6. Status / Cancel / Close 消息

建议帧分配：

| FrameType | 名称 | 方向 | 用途 |
|---:|---|---|---|
| 0x1C | SESSION_STATUS_GET | A → B | 查询 Session 或 Request |
| 0x1D | SESSION_STATUS_RESULT | B → A | 返回查询状态 |
| 0x1E | SESSION_CLOSE | A → B | 请求关闭 Session |
| 0x1F | SESSION_CLOSE_RESULT | B → A | 返回关闭进展或最终状态 |
| 0x22 | SESSION_CANCEL | A → B | 请求取消一个 Request |
| 0x23 | SESSION_CANCEL_RESULT | B → A | 返回取消是否被接受/确认 |

## 7. 状态定义

### 7.1 Session 状态

- CREATING
- STARTING
- READY
- BUSY
- STOPPING
- CLOSED
- FAILED
- LOST

### 7.2 Request 状态

- ACCEPTED：已接收；对于当前 Shell 异步 HTTP 接口，表示对端已返回后台任务启动回执，不代表执行完成；
- QUEUED：等待执行；
- RUNNING：正在执行；
- SUCCEEDED：执行成功；
- FAILED：执行失败；
- CANCEL_REQUESTED：取消请求已发送，尚未确认；
- CANCELLED：Runtime 已确认请求未开始或已安全取消；
- TIMED_OUT：Runtime 明确确认执行期限已到；
- UNKNOWN：无法确认执行结果。

HTTP 客户端自身等待结束应返回 pending / wait timeout，而不是擅自把 Runtime Request 标记为 TIMED_OUT。

## 8. 幂等与重传

- request_id 在保留窗口内全局唯一。
- 首次接受时记录 request_id、session_id 和 payload_sha256。
- 同 ID、同摘要的重复提交：返回现有状态或缓存结果，不重复执行。
- 同 ID、不同摘要：返回 REQUEST_ID_CONFLICT。
- A/B 断线后 A 应先查询 Request 状态，不得对副作用操作盲目换新 ID 重放。
- 结果缓存和去重记录必须有容量、TTL 和清理策略；清理前应保证足够的重连恢复窗口。

这提供可恢复的去重语义，不应宣称所有专业操作都具备理论上的 exactly-once 效果。

## 9. 执行顺序、超时与取消

- 同一 Session 采用 FIFO 队列，单一消费者。
- 同一 Session 同时最多一个 RUNNING Request。
- 不同 Session 是否并行由 Broker 和 Profile 的资源约束决定。
- 对尚未开始的 Request 可安全从队列移除并确认 CANCELLED。
- Capture 当前通过 Tcl after 在 Capture 事件循环执行脚本。正在执行的同步 Tcl / Dbo 调用可能阻塞下一次轮询，第一版不承诺强制中断。
- 运行任务取消不能通过杀掉 Capture 进程实现。
- A 端超时只表示等待结束；若结果未知，返回 UNKNOWN / pending 并允许后续查询。

## 10. 建议 ABFP FrameType 分配

当前 protocol.py 已使用：

- 0x10 EDA_TASK_SUBMIT
- 0x11 EDA_LOG_STREAM
- 0x12 EDA_TASK_FIN
- 0x13 EDA_FORCE_KILL
- 0x14 EDA_APP_CLOSE
- 0x20 FS_REQUEST
- 0x21 FS_RESPONSE
- 0x30 至 0x35 Shell 消息

已分配及预留的 Session 帧号：

| 建议值 | 名称 | 方向 |
|---:|---|---|
| 0x15 | SESSION_HELLO | 任一端 → 对端；请求能力协商（P1.2 已实现） |
| 0x16 | SESSION_CAPABILITIES | 接收端 → 发起端；对 HELLO 响应（P1.2 已实现） |
| 0x17 | SESSION_CREATE | A → B |
| 0x18 | SESSION_EVENT | B → A |
| 0x19 | SESSION_EXEC | A → B |
| 0x1A | SESSION_ACCEPTED | B → A |
| 0x1B | SESSION_RESULT | B → A |
| 0x1C | SESSION_STATUS_GET | A → B |
| 0x1D | SESSION_STATUS_RESULT | B → A |
| 0x1E | SESSION_CLOSE | A → B |
| 0x1F | SESSION_CLOSE_RESULT | B → A |
| 0x22 | SESSION_CANCEL | A → B |
| 0x23 | SESSION_CANCEL_RESULT | B → A |

0x24 暂不分配。0x15 / 0x16 已写入 `protocol.py`；0x17–0x1F、0x22–0x23 仍是预留编号，尚未接收或处理。

协商超时只能报告 `unconfirmed`（对端未响应 / 链路无响应），不能仅凭超时断言对端一定是旧版本。收到合法 CAPABILITIES 但没有共同协议时报告 `no_common_protocol`。当前功能列表声明 `handshake` 与 `request.correlation`，未声明 `session.create`。

## 11. HTTP API（主机 A 北向接口）

统一前缀：/api/v1/runtime

| 方法与路径 | 作用 | 推荐状态码 |
|---|---|---|
| GET /capabilities | 查询最近一次对端协议协商状态及功能列表（P1.2 已实现） | 200 |
| POST /negotiate | 异步触发一次能力协商，立即返回 message_id（P1.2 已实现） | 202 / 503 |
| GET /api/v1/requests/{request_id} | 查询 A 端保留的 EDA / Shell 请求状态及结果（P1.3 已实现） | 200 / 202 / 400 / 404 / 502 / 503 |
| GET /profiles | 列出 B 端已注册 Runtime Profiles | 200（待实现） |
| POST /sessions | 创建 Session | 202 |
| GET /sessions | 列出当前会话 | 200 |
| GET /sessions/{session_id} | 查询 Session | 200 / 404 |
| POST /sessions/{session_id}/requests | 提交 Request | 202 |
| GET /sessions/{session_id}/requests/{request_id} | 查询 Request / 结果 | 200 / 404 |
| POST /sessions/{session_id}/requests/{request_id}/cancel | 请求取消 | 202 |
| POST /sessions/{session_id}/close | 请求关闭 Session | 202 |

HTTP 创建 Session 示例：

~~~json
{
  "profile_id": "capture-17.2",
  "startup_options": {
    "startup_timeout_ms": 60000
  },
  "metadata": {
    "skill_id": "capture-skill"
  }
}
~~~

HTTP 提交 Request 示例：

~~~json
{
  "operation": "execute",
  "timeout_ms": 60000,
  "payload": {
    "content_type": "text/x-tcl",
    "encoding": "utf-8",
    "body": "puts [pid]"
  }
}
~~~

Session 创建和 Request 提交接口未来默认异步返回 202 和 ID。P1.2 的 negotiate 端点同样立即返回 message_id；Skill 通过 GET capabilities 查询协商结果，不让 HTTP Handler 等待串口响应。

实际 P1.2 HTTP 路径为 `/api/v1/runtime/capabilities` 与 `/api/v1/runtime/negotiate`。P1.3 当前支持：
- `POST /api/v1/eda/execute`：提交 EDA 脚本；
- `POST /api/v1/shell/exec`：提交远程 Shell 命令；
- `GET /api/v1/requests/{request_id}`：按 UUID 查询请求状态和结果。

A 端生成独立 UUID `request_id`，把它作为可选 JSON 字段放入现有 `EDA_TASK_SUBMIT` / `SHELL_EXEC` 帧 Payload；B 端在 `EDA_TASK_FIN` / `SHELL_RESULT` 中原样回显。既有 ABFP FrameType 与 16 位 `seq_id` 均不改变，`seq_id` 仍只用于传输帧编号。A 端只有在最近一次能力协商成功，且对端同时声明 `request.correlation` 与 `request.deduplication` 时，才允许新的结构化 HTTP EDA / Shell 请求提交；断线或对端不支持时返回 `not_submitted`。调用方可在 POST body 中提供 UUID `request_id`；A 端尚保留该 ID 时再次 POST 会返回 409，并要求改用 GET 查询。A 端进程重启或记录过期后，相同 ID 可重新下发给 B 端 Broker，由 B 端持久化记录决定返回缓存结果、冲突或 UNKNOWN。

HTTP 行为：
- 请求等待超过 `wait_timeout_ms` 时返回 HTTP 202 和 `state=pending`；这只代表 HTTP 等待结束，不代表远端任务取消。随后可用 GET 请求状态接口轮询。
- EDA / Shell 同步结果完成时返回 200；请求失败返回 502；链路断开或结果未知返回 503。
- Shell 异步模式返回 HTTP 202 / `state=accepted`，表示已收到后台启动回执，不代表命令执行完成；后续可轮询观察是否有最终结果（当前 Shell 异步引擎先返回启动回执，真正结束状态仍通过日志输出路径通知）。
- GET 状态接口对 pending / accepted 返回 202，对 completed 返回 200，对 failed 返回 502，对 unknown 返回 503，对不存在或已过期 ID 返回 404；无效 UUID 返回 400。

A 端请求记录仅保存在内存中，默认最多 500 条、保留 TTL 为 900 秒。等待超时不删除记录；等待记录超过保留期限后转为 `unknown` 并唤醒等待者，终态记录到期后可被回收。串口断开也会将所有 pending 请求置为 `unknown`。重启 A 端进程后不保证恢复这些记录。

P1.4 B 端 RequestBroker 当前行为（云端 CI 验收中）：
- 使用 SQLite 持久化请求 ID、请求类型、规范化 JSON Payload 的 SHA-256、状态和有界结果；默认数据库路径为 Windows `%LOCALAPPDATA%/AirBridge/runtime_broker.sqlite3`，可用环境变量 `AIRBRIDGE_RUNTIME_DB` 覆盖。
- 默认最多保留 10000 条请求记录，终态 TTL 为 24 小时；单条缓存结果限制在 64000 字节以内。清理优先回收过期 / 最早的终态记录，不驱逐仍在执行的 RUNNING 请求。
- Broker 在调用执行函数前原子登记 RUNNING；相同 `request_id`、相同请求类型和相同 Payload hash 的重发：RUNNING 时不启动第二次执行，终态时返回缓存结果；相同 ID 对应不同请求类型或不同 Payload hash 时返回 `REQUEST_ID_CONFLICT`。
- 正在执行的请求每 30 秒刷新一次 Broker 心跳；默认 120 秒未收到心跳的 RUNNING 记录转成 UNKNOWN，之后不会以同一 ID 自动重放。此策略可避免另一个 Broker 实例把仍在执行的请求误判为中断。
- Shell 异步请求的 `accepted` 记录缓存后台任务启动回执，不代表 Shell 进程已结束；同一 ID 重发只返回相同回执，不会再次启动命令。

P1.4 仅实现通用 Request 去重和有界结果缓存；当前过渡执行回调仍委托给旧版 EDAExecutor / ShellEngine，尚未引入 Runtime Adapter / Profile，也没有 Session ID、每 Session FIFO 队列、去重窗口外的保护或 exactly-once 保证。若 Broker 在外部副作用执行后、结果落盘前崩溃，恢复状态为 UNKNOWN，系统不会自动重跑。现有 GUI 发出的无 `request_id` 任务仍保留旧式执行路径。其他 Session API 尚未实现。

## 12. 大载荷与文件引用

ABFP 单帧最大载荷为 65000 字节。JSON Envelope、协议头和其他字段也占用空间，因此实际脚本数据上限应低于 65000。

大脚本、大输出或二进制附件使用现有文件传输 / RFS，Request 中携带：

~~~json
{
  "artifact_ref": {
    "host_path": "E:/_airbridge_temp/sessions/.../payload.tcl",
    "size": 123456,
    "sha256": "..."
  }
}
~~~

B 端在执行前校验文件大小和 SHA-256。路径必须属于该 Session 的受控工作目录，不能允许任意路径读取。

## 13. 安全约束

- Session Bridge 的任务目录应使用受限 ACL。
- session_id、request_id 必须格式校验。
- 限制并发 Session 数、队列长度、脚本大小、结果大小和结果保留时间。
- Runtime 执行脚本属于代码执行能力，只接受可信 AirBridge 对端和被授权的 Skill 请求。
- 当前 ABFP CRC 只提供传输错误检测，不提供身份认证。
- 未完成认证升级前，安全边界必须依赖受控串口、主机权限和本地 ACL；不得把它描述为具备密码学认证。
- 结果日志应避免输出凭据和不必要的设计数据。

## 14. 实施与兼容验收

P1.2 已实现内容：

- `FrameType.SESSION_HELLO = 0x15`、`SESSION_CAPABILITIES = 0x16`；
- 独立 `runtime_session_protocol.py` 负责 Envelope 编解码和结构校验；
- 串口连接后自动后台协商，支持手动异步重试；
- `GET /api/v1/runtime/capabilities` 与 `POST /api/v1/runtime/negotiate`；
- 对端旧版本无响应时标记 `unconfirmed`，不将静默丢帧误判为明确不兼容。

P1.3 当前实现内容（已通过软件 CI）：

- 协议能力增加 `request.correlation`；未协商出该能力时拒绝结构化 EDA / Shell HTTP 提交；
- A 端独立生成 UUID request_id，并在现有 EDA / Shell 帧的 JSON Payload 中传递；
- B 端 EDA_TASK_FIN / SHELL_RESULT 回显 request_id，A 端按 ID 唤醒对应 HTTP waiter；
- A 端新增有限容量、TTL 的内存 RequestTracker 和 `GET /api/v1/requests/{request_id}`；
- HTTP 等待超时返回 pending 并可轮询；断线或保留期限到达时返回 unknown；
- 大型文本结果裁剪到单帧 ABFP Payload 上限。

P1.4 的 RequestBroker 仍只覆盖现有 EDA / Shell 帧中的 request_id，不负责 Session Create、每 Session FIFO 或专业软件语义。P1.5 已增加本机 Runtime Adapter / Profile 注册与执行分派；P1.6 已将 Capture Tcl 请求路由至本机 CaptureTclAdapter / capAutoLoad Bridge，但继续复用 EDA_TASK_SUBMIT / EDA_TASK_FIN，不新增 SESSION_CREATE / SESSION_EXEC 帧。CAPABILITIES 的 profiles 仍为空，且真实 Capture 17.2 实机复验、Process Supervisor 与完整 Session 生命周期尚未完成。

| 阶段 | 验收 |
|---|---|
| Frame 协商 | 新版双方协商成功；旧版 / 无响应只报告 `unconfirmed`，不误判为确定版本 |
| Session Create | 旧 Capture 全部退出后创建新进程；READY 匹配 PID 与 nonce |
| Request | 一个 Session 连续执行多条不同请求 |
| Result | 完整结构化结果通过 ABFP 返回并关联原 request_id |
| 幂等 | 重复请求不重复执行；不同摘要冲突 |
| 错误 | Runtime 异常返回 code、message、stack |
| 超时 | Client wait timeout 不误报执行取消 |
| Cancel | 排队任务可取消；运行中状态符合 Adapter 能力 |
| Close | 保存确认后 Exit；进程退出后才报告 CLOSED |
| Skill | Capture 与 Allegro Skill 使用同一套通用 API |
