# AirBridge MCP Gateway：M2.1 / M2.2 双 Host 验收记录

- 验收日期：2026-11-13
- 代码分支：`feature/m2-mcp-gateway`
- 验收提交：`b1538fd660329f0cc84071f6721dfffb5ed4389d`
- PR：[#4 feat(m2): add standalone AirBridge MCP Gateway](https://github.com/scanligithub/airbridge/pull/4)
- 基线：M1 正式 main commit `43c0900b5728d9d13710e265bd87647804be2227`

## 1. 代码和 CI

| 检查 | 结果 |
|---|---|
| Python HTTP Gateway 单元测试 | 8 / 8 PASS |
| MCP SDK 注册 / in-process Client 测试 | 3 / 3 PASS |
| 本机总计 | 11 / 11 PASS |
| compileall | PASS |
| GitHub Actions Python 3.11 | PASS |
| GitHub Actions Python 3.12 | PASS |

GitHub Actions Run：`43373982166`，两个测试 Job 均完成并成功。

## 2. 主机 A HTTP API

从 Host A 的 PowerShell 成功调用：
- GET `/api/v1/runtime/capabilities`：`state=negotiated`、`protocol=runtime-session.v1`、`peer_version=airbridge-v2.4`、HTTP 200。
- GET `/api/v1/eda/versions`：`success=true`，发现 Cadence SPB 17.2、Capture / Allegro 可执行路径，HTTP 200。

## 3. 两种 MCP Host 实际验收

MCP Hosts：
- Antigravity
- OpenCode Desktop

两个 Host 均：
1. 成功连接本地 stdio Gateway。
2. 发现完全一致的四个工具：`airbridge_get_capabilities`、`airbridge_get_eda_versions`、`airbridge_eda_execute`、`airbridge_request_status`。
3. 成功调用 capabilities 和 EDA versions，均获得 HTTP 200。
4. 使用测试 UUID `d20f8b2c-6d01-4f9b-9c18-53ddf5dcae31` 调用 request status，均得到预期 `state=not_found` / HTTP 404。
5. 使用空脚本参数调用 EDA execute，均得到 `state=disabled`、`error.code=EDA_EXECUTION_DISABLED`；没有 request_id，没有提交 HTTP execute 请求。

## 4. 安全边界

- 全程 `AIRBRIDGE_ENABLE_EDA_EXECUTE=0`。
- 没有调用任何有效 Tcl / SKILL 脚本。
- 没有触发 Host B EDA 执行。
- Remote Shell 未作为 MCP Tool 暴露。
- 真实 EDA 执行点到点测试有意留待后续授权和安全设计完成后再做。

## 5. 验收结论

**M2.1 / M2.2 的 MCP 接入、只读工具调用、请求状态错误路径和执行默认关闭保护通过双 Host 实际验收。**

本记录不表示 Agent 身份认证、per-Agent 授权、审计、配额或 Runtime Session 隔离已经实现；这些属于 M2.3 及后续阶段。
