# ADR-011：Runtime Session Gateway 状态扩展与旧版兼容

- 状态：**提议采用；协议单元测试和旧版 EXE 双向实测前不冻结**
- 日期：2026-11-20
- 决策范围：ABFP Runtime Session 0x15 / 0x16 扩展
- 关联：`runtime_session_protocol.py`、`AirBridge_Session_Protocol_v1.0.md`、M2.5 统一运行时架构

## 1. 背景

M2.5 要求两台 AirBridge 主机在建立双机通信后交换本机 Gateway 是否启用的配置状态：

| 本机 | 对端 | UI 行为 |
|---|---|---|
| ON | OFF | 状态互补，不提示冲突 |
| OFF | ON | 状态互补，不提示冲突 |
| ON | ON | 显示配置冲突提示 |
| OFF | OFF | 显示配置冲突提示 |

双方使用相同 AirBridge.exe。Gateway enabled 是每台主机自己的 GUI 配置，不是 Controller / Runtime 软件角色，也不是 AirBridge Runtime 是否启动。

当前 Runtime Session 已有 `SESSION_HELLO (0x15)` 与 `SESSION_CAPABILITIES (0x16)`。现有协议使用 `runtime-session.v1` 和 Envelope `schema_version=1`；两端在连接后均可主动发送 HELLO。当前 HELLO / CAPABILITIES 已有固定必需字段。

## 2. 当前代码兼容性事实

当前 `runtime_session_protocol.py` 的校验行为：
- HELLO 校验 `supported_protocols`、`client_version` 和 `max_frame_payload`；
- CAPABILITIES 校验 `supported_protocols`、`features`、`profiles`、`server_version` 和 `max_frame_payload`；
- 校验器只要求这些必需字段，不拒绝额外的 Payload 字段；
- CAPABILITIES 的 `features` 是字符串列表，当前校验器不会因为出现未知 Feature 字符串而拒绝消息。

这意味着可通过**新增可选 Payload 扩展字段**增加 Gateway 状态，而不更改现有必需字段、Envelope schema_version 或 ABFP 帧编号。此兼容性结论来自当前源码；仍需用实际旧版 AirBridge.exe 做双向回归，不能只依赖源码推断。

## 3. 决策提议

### D11-1：复用现有 0x15 / 0x16，不新增 ABFP FrameType

M2.5 不需要新分配 Session 帧编号，不更改 ABFP 帧头、CRC、seq_id、最大载荷或现有消息语义。

Gateway 状态作为 Runtime Session 的通用可选能力元数据交换，不是 EDA 专用字段。

### D11-2：定义 Feature `gateway.state.v1`

新版本 CAPABILITIES 的 `features` 增加：

```json
"features": [
  "handshake",
  "request.correlation",
  "request.deduplication",
  "shell.task.result-query",
  "gateway.state.v1"
]
```

旧版本收到未知 Feature 时，按当前源码行为仍会将其作为普通字符串接受；旧版本不会因此获得 Gateway 状态语义。

### D11-3：HELLO 与 CAPABILITIES 都携带本机配置状态

两条消息的 Payload 均可包含同样的可选字段：

```json
"gateway_state": {
  "schema_version": 1,
  "configured_enabled": true
}
```

要求：
- `configured_enabled` 必须是 JSON Boolean，不接受 0 / 1、字符串或 null；
- 字段表示本机 GUI 中的 Gateway 总开关配置；
- 不表示 Gateway Worker 实际是否运行；
- 不表示 MCP Client 是否已连接；
- 不包含 Agent ID、端口、endpoint、PID、计算机名、用户名或任何凭据；
- 当 `gateway.enabled=false` 时，值必须为 false，即使 Gateway 管理模块仍可在 GUI 使用。

同时在 CAPABILITIES 的 `features` 中声明 `gateway.state.v1`。只有字段结构有效且能力版本匹配时，接收端才把状态标记为可信的“已协商状态”。

### D11-4：保留 Envelope schema_version=1

新增字段全部可选，因此本提案不改变：
- `SCHEMA_VERSION=1`；
- `SESSION_PROTOCOL_ID="runtime-session.v1"`；
- FrameType 0x15 / 0x16；
- 既有必需字段；
- 现有 Request correlation / deduplication 语义。

未来若新增必需字段或改变既有字段语义，必须通过新的协议版本或显式 Feature 协商，不能静默改变 v1 含义。

## 4. 双向交换流程

1. 串行链路进入当前连接代次，双方按既有流程各自发送一次 HELLO。
2. 新版本在 HELLO 中附加本机 `gateway_state`。
3. 接收端在验证 HELLO 必需字段后，读取可选 Gateway 状态；若字段缺失则记录为 UNKNOWN。
4. 接收端返回 CAPABILITIES，并在新版本中附带自己的 `gateway_state`，同时声明 `gateway.state.v1` Feature。
5. 发起端通过 correlation_id 匹配响应，读取对端状态。
6. 两端各自完成上述双向过程后，才有足够信息判断冲突。
7. 用户在 GUI 修改本机 Gateway 总开关并成功持久化后，本机再次发送 HELLO；对端更新状态，响应也使本机刷新对端状态。
8. 收到旧连接代次、无效 correlation_id 或格式错误的状态消息时，不更新当前对端状态。

两端同时发起 HELLO 属于当前协议已有行为，不构成冲突；每个 HELLO / CAPABILITIES 仍用各自 message_id / correlation_id 关联。

## 5. 旧版互操作行为

| 本机版本 | 对端版本 | 可观察结果 |
|---|---|---|
| 新版 | 新版 | 双向交换 Gateway 状态；可判断四种组合 |
| 新版 | 旧版 | 旧版忽略新可选字段并正常返回旧 CAPABILITIES；新版将对端状态标记 UNKNOWN，不弹冲突窗 |
| 旧版 | 新版 | 旧版忽略新 CAPABILITIES 可选字段；新版收到旧 HELLO 后将旧版状态标记 UNKNOWN |
| 旧版 | 旧版 | 无 Gateway 状态能力；不进行冲突判断 |

**禁止把缺失字段解释为 OFF。** 缺失字段表示旧版本、能力不支持、未交换或状态未知。

## 6. 冲突判断规则

仅当以下条件全部成立时判断冲突：
- 已建立当前双机 Runtime Session / 串行链路连接；
- 本机 Gateway 配置已读取成功；
- 对端 Gateway 状态来自当前连接代次的合法 HELLO / CAPABILITIES；
- 双方状态字段 schema_version 均支持 v1。

冲突规则：

```text
local.enabled == remote.enabled
    true  -> configuration conflict
    false -> complementary configuration
```

由于用户明确要求 ON/ON 与 OFF/OFF 都提示，两种相同状态都属于冲突。

单机使用、没有建立对端会话、链路断开、对端旧版本、字段缺失、协议错误或状态过期时，不弹冲突窗口，只显示 UNKNOWN / 未协商。

## 7. 状态变化与过期

- 每次新连接代次开始时，清除上一次连接的对端 Gateway 状态；
- 每条新 HELLO / CAPABILITIES 状态只应用于当前连接代次；
- 本机配置变更后发送新的 HELLO，不能等待下一次设备重连；
- GUI 按“连接代次 + 当前冲突状态”去重：同一连接代次内，每次进入新的冲突状态只弹一次；重复 HELLO / CAPABILITIES、重传和重复状态更新不得重复弹窗。
- 对端从冲突状态切换为互补状态时，清除当前冲突锁存与非模态提示；如果同一连接代次之后再次进入冲突，则允许重新弹窗；
- 链路断开时，立即把对端状态变为 UNKNOWN，清除当前连接代次的冲突锁存并撤下持续冲突提示，不把历史最后值当作实时值；
- 不依赖两台主机时钟完全同步；以当前连接代次、message_id 和 correlation_id 防止旧消息覆盖新状态。

是否增加持久化 config revision 作为未来扩展，当前不作为最小协议要求。

## 8. 冲突处理

冲突仅用于 UI 提醒：
- 不自动修改本机配置；
- 不请求对端自动修改配置；
- 不自动停止 Gateway Worker；
- 不改变 Runtime Session、串行链路或 HTTP API 的运行状态；
- 不据此改变 AirBridge 请求权限；
- 是否继续其他 Runtime 操作不由本字段决定。

推荐弹窗文案：

**Gateway 配置冲突**

本机与对端的 Gateway 启用状态相同。请检查两端 AirBridge GUI 中的 Gateway 总开关。AirBridge 不会自动修改配置或停止服务。

用户操作：
- **稍后处理**：关闭当前模态弹窗；不清除冲突状态，不等于忽略或解决冲突。
- **打开 Gateway 设置**：关闭弹窗并导航到本机 Gateway 设置页面。

M2.5 首版不提供“忽略本次会话”或永久忽略按钮。Gateway 总览与通信状态页持续显示非模态冲突提示，直到状态恢复或变为 UNKNOWN。

## 9. 安全边界

- Gateway 状态仅是一个 Boolean 配置提示，不是身份凭据或认证机制；
- 不将此字段用于 Agent 认证、授权、路由或执行决策；
- 不在握手中传递 Token、Scope、Agent 列表、端口或敏感主机信息；
- 当前 ABFP CRC 用于传输校验，不提供密码学身份认证；本字段不能作为对端可信身份的证明；
- 若收到格式错误的字段，忽略该扩展并记录非秘密诊断事件，不能中断既有 Runtime Session v1 握手。

## 10. 验收

- [ ] 新版 HELLO / CAPABILITIES 可编码、解码并通过 schema 验证。
- [ ] 当前旧版本代码能接受含未知可选 Gateway 字段的新 HELLO / CAPABILITIES。
- [ ] 新版能接受不含 Gateway 扩展的旧版消息。
- [ ] 新/新双向交换能获得两端最新 configured_enabled。
- [ ] 新/旧、旧/新、旧/旧均不误报 OFF。
- [ ] ON/OFF、OFF/ON 不弹冲突。
- [ ] ON/ON、OFF/OFF 在建立会话且获得双方状态后弹冲突。
- [ ] 无链路、断线、重连、超时、未协商时状态变为 UNKNOWN。
- [ ] 本机 Gateway 配置改变后能及时重新广播状态。
- [ ] 重复 HELLO、乱序/迟到 CAPABILITIES 不造成 UI 弹窗风暴或旧状态覆盖。
- [ ] Gateway 状态交换不新增 EDA 字段、不改变 ABFP 帧分配和串口传输。
- [ ] Host A / Host B 使用同一个新 EXE 实测四种组合。

## 11. 参考代码与文档

- `runtime_session_protocol.py`
- `protocol.py`（FrameType 0x15 / 0x16）
- `doc/AirBridge_Session_Protocol_v1.0.md`
- `doc/AirBridge_Runtime_Session_架构设计_v1.0.md`

---
