# AirBridge Runtime Session Protocol v1

- 文档编号：AB-PROTO-SESSION-001
- 版本：1.0
- 状态：**协议设计基线（未实现）**
- 日期：2026-11-12
- 关联架构：AirBridge 通用 Runtime Session 架构设计 v1.0

> 本文件定义建议的消息语义、JSON Envelope、ABFP 帧分配和北向 HTTP API。当前 main 分支尚未实现本协议；帧编号在代码合并前视为预留建议。

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
- 现有 Shell 消息：0x30 至 0x35。

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

### 4.1 SESSION_HELLO（建议 FrameType 0x15）

A → B。交换协议能力和最低版本。

Payload 示例：

~~~json
{
  "supported_protocols": ["runtime-session.v1"],
  "client_version": "airbridge-x.y",
  "max_frame_payload": 65000
}
~~~

### 4.2 SESSION_CAPABILITIES（建议 FrameType 0x16）

B → A。返回支持的协议、Runtime Adapter、Profiles 和限制。

~~~json
{
  "supported_protocols": ["runtime-session.v1"],
  "profiles": [
    {
      "profile_id": "capture-17.2",
      "adapter_id": "capture-tcl-filequeue",
      "capabilities": ["execute", "status", "graceful_close"]
    }
  ],
  "max_frame_payload": 65000
}
~~~

Profile 只作为 B 端注册的通用能力描述。A 端不能假设所有主机都安装了某个 Profile。

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

- ACCEPTED：已接收；
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

建议保留以下 Session 帧号：

| 建议值 | 名称 | 方向 |
|---:|---|---|
| 0x15 | SESSION_HELLO | A → B |
| 0x16 | SESSION_CAPABILITIES | B → A |
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

0x24 暂不分配。上述编号是协议设计建议，代码合并前必须重新检查 A/B 当前分支和所有 FrameType 使用情况。

新协议启用前先做能力协商。旧版 B 不支持 runtime-session.v1 时，A 端应明确报告不支持，不能静默发送新帧或把任务误判为成功。

## 11. HTTP API（主机 A 北向接口）

统一前缀：/api/v1/runtime

| 方法与路径 | 作用 | 推荐状态码 |
|---|---|---|
| GET /profiles | 列出 B 端已注册 Runtime Profiles | 200 |
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

提交接口默认异步返回 202 和 ID。Skill 通过 GET 查询最终状态；可选增加 bounded wait，但不得让 HTTP Handler 长时间阻塞工作线程。

现有 /api/v1/eda/execute、/api/v1/shell/exec 和 FrameType.EDA_* / SHELL_* 在迁移期保持兼容。Session API 稳定后再评估 deprecated 时间表，不在第一阶段删除旧接口。

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

| 阶段 | 验收 |
|---|---|
| Frame 协商 | 新旧版本明确识别 Session 能力 |
| Session Create | 旧 Capture 全部退出后创建新进程；READY 匹配 PID 与 nonce |
| Request | 一个 Session 连续执行多条不同请求 |
| Result | 完整结构化结果通过 ABFP 返回并关联原 request_id |
| 幂等 | 重复请求不重复执行；不同摘要冲突 |
| 错误 | Runtime 异常返回 code、message、stack |
| 超时 | Client wait timeout 不误报执行取消 |
| Cancel | 排队任务可取消；运行中状态符合 Adapter 能力 |
| Close | 保存确认后 Exit；进程退出后才报告 CLOSED |
| Skill | Capture 与 Allegro Skill 使用同一套通用 API |
