# AirBridge M2.3 Host-A 双 Agent 只读验收记录

- 验收日期：2026-11-17
- 代码分支：`feature/m2-agent-security`
- 验收代码 HEAD：`173791844e47704e972d9d3b8c4d48d721e1fbaa`
- 对应 PR：[#5 feat(m2.3): add local Agent authentication and authorization](https://github.com/scanligithub/airbridge/pull/5)
- 验收模式：Host-A 本机、loopback HTTP、双 Agent 只读；不执行 EDA / Shell

## 1. 构建与部署

| 项目 | 实测结果 |
|---|---|
| Host-A Python | 3.10.11 |
| PyInstaller | 6.22.3 |
| 目标程序 | `AirBridge.exe`（M2.3 分支构建） |
| 构建输出目录 | `dist_m23_hosta_20261117_150811` |
| 文件大小 | 46,715,384 bytes |
| SHA-256 | `9E8D8749058BF5E1E9979094BEDB8DE2F8DC4A32AD9ECC81EA7DE2BFF53EA9BC` |
| 服务端数据库 | `%LOCALAPPDATA%\AirBridge\agent_security.sqlite3` |
| HTTP 监听 | `127.0.0.1:8888`，单一 loopback listener |
| 服务端 EDA 总开关 | 启动前显式设置 `AIRBRIDGE_ALLOW_EDA_EXECUTE=0` |

构建使用独立时间戳目录，没有覆盖既有 `build_*` / `dist_*` 产物。进程检查确认监听进程是本次 M2.3 EXE 启动进程的子进程，映像路径相同。

## 2. 两端 MCP 配置

| 客户端 | 配置 | Gateway Python | Token 身份 | Token Scope |
|---|---|---|---|---|
| Antigravity | 全局 `mcp_config.json` 的 `mcpServers.airbridge` | M2.3 `mcp_gateway/.venv` | `antigravity` | `runtime.read`、`eda.versions.read`、`request.read` |
| OpenCode Desktop 2.2.1 | 全局 `opencode.json` 的 `mcp.servers.airbridge` | M2.3 `mcp_gateway/.venv` | `opencode` | `runtime.read`、`eda.versions.read`、`request.read` |

- 两端使用不同的独立 Bearer Token；Token 明文未写入本记录。
- 两端 `AIRBRIDGE_ENABLE_EDA_EXECUTE=0`。
- OpenCode 原有 `provider`、`plugin`、`disabled_providers` 设置保留。
- 两份配置修改前均生成了时间戳备份。
- Antigravity 已运行 M2.3 Gateway 子进程；OpenCode CLI `mcp list --print-logs` 返回 `✓ airbridge connected`。

## 3. Host-A 只读 HTTP 验收

正式探针：`tools/airbridge_agent_hosta_acceptance.py`。探针通过内存读取两份本机 MCP 配置中的 Token，不打印或落盘密钥，只发送 loopback HTTP GET。

| 检查 | Antigravity | OpenCode |
|---|---:|---:|
| Token 在本机凭据库中有效，Agent 身份正确 | PASS | PASS |
| Scope 恰好为三个只读 Scope | PASS | PASS |
| `GET /api/v1/runtime/capabilities` | HTTP 200 | HTTP 200 |
| 随机、未归属 UUID 的 request status | HTTP 404 / `not_found` | HTTP 404 / `not_found` |

公共认证检查：
- 缺少 Authorization：HTTP 401 / `INVALID_AGENT_TOKEN`。
- 错误 Authorization：HTTP 401 / `INVALID_AGENT_TOKEN`。
- OpenCode Token 独立身份核验：`agent_id=opencode`，精确只读 Scope，HTTP 200。
- Antigravity Token 独立身份核验：`agent_id=antigravity`，精确只读 Scope，HTTP 200。
- MCP stdio `initialize` 握手通过，`tools/list` 发现预期四个工具：`airbridge_get_capabilities`、`airbridge_get_eda_versions`、`airbridge_eda_execute`、`airbridge_request_status`。
- OpenCode 2.2.1 的 MCP 状态为 `airbridge connected`。

正式只读探针基础验收结果：8 项 PASS，0 项 FAIL。

## 3.1 Host-A 合成请求归属 / 403 / 429 探针

独立探针：`tools/airbridge_agent_hosta_ownership_probe.py`。执行日期：2026-11-17。

本探针不依赖正式双 Agent EXE 或生产凭据库。脚本使用 `tempfile.TemporaryDirectory` 创建临时 SQLite 数据库，以内存 `RequestTracker` 预置一条归属于 `fixture-owner` 的合成完成请求；使用禁止发送帧的 `SyntheticWorker` 和随机 loopback 端口启动独立 HTTP 服务。整个探针只发送 GET 请求，不访问正式 `127.0.0.1:8888` 服务。

| 检查 | 实测结果 |
|---|---|
| Owner 查询预置合成请求 | HTTP 200，state=`completed` |
| 另一 Agent 查询该请求 | HTTP 404，`not_found` |
| Non-owner 与 random-unknown 的公开响应 | 均为 HTTP 404 / `not_found`，错误文本完全一致 |
| 缺少 `request.read` scope | HTTP 403 |
| 状态查询超过测试配额 | HTTP 429，含 `Retry-After: 60` |
| ABFP frame 发送次数 | 0 |
| RequestTracker 中请求数 | 仅预置的 1 条合成请求 |
| 临时凭据数据库 | 脚本退出时由临时目录清理 |

**结果：8 项检查全部 PASS，0 项 FAIL。** 脚本输出明确确认只向临时 in-process 服务发送 loopback GET，没有使用生产数据库、串口、EDA / Shell POST 或 ABFP 帧，也没有执行 Capture Tcl / Allegro SKILL。

本次探针运行时 Git HEAD：`101d8baf5787f36dad3b67ae274b9626e09d5321`。该 HEAD 是当时工作区的基线；合成探针脚本及本次文档变更尚未提交，因此不能把这些新增文件描述为已包含在该 commit 中。

该结果验证的是 HTTP 层合成请求归属隔离，以及只读状态查询路径的 scope / quota 响应；不等同于真实 EDA request_id 的端到端跨 Agent 验收，也不覆盖 EDA Execute 403、Shell scope 拒绝或真实 EDA 429。

## 4. Token 撤销与审计

使用临时 Agent `m23-revoke-probe` 创建一枚临时只读 Token，随即撤销：
- 本机凭据库确认 Token 已撤销。
- 无需重启 AirBridge，运行中的 HTTP 服务对该 Token 返回 HTTP 401 / `INVALID_AGENT_TOKEN`。
- 临时凭据审计表包含两条管理记录：`AGENT_TOKEN_ISSUED` 和 `AGENT_TOKEN_REVOKED`。
- 扩展探针（含撤销检查）全部 PASS；没有撤销 Antigravity 或 OpenCode 的正式 Token。

最终审计查询时：
- Antigravity：1 枚有效 Token（历史共 2 条凭据记录，另一条已撤销），11 条近期审计记录。
- OpenCode：1 枚有效 Token，9 条近期审计记录。
- 审计输出只包含 Agent、路径、决策、HTTP 状态、reason code、request ID 等元数据；本记录不包含 Token 明文。

## 5. 本机测试与 CI 证据

### Host-A 本机 Python 3.10

- `py_compile`：通过。
- `test_agent_security.py`：14 / 14 通过。
- `test_request_api_http.py`：20 / 20 通过。
- 合计：34 / 34 通过。

### GitHub Actions（PR #5 当前验收代码树）

- [Agent Security PR Run #32](https://github.com/scanligithub/airbridge/actions/runs/44035221337)：Windows Python 3.10、3.11、3.12 三个矩阵 Job 全部成功；每个版本 14 项凭据 / Scope / 请求归属 / 审计测试及 20 项 HTTP 集成测试全部通过。
- [MCP Gateway PR Run #36](https://github.com/scanligithub/airbridge/actions/runs/44035221274)：Python 3.11、3.12 两个矩阵 Job 全部成功；每个版本 14 项 Gateway / MCP SDK 测试全部通过，包含重定向时不向目标转发 Bearer Token 的回归测试。
- [Redirect security regression Run #22](https://github.com/scanligithub/airbridge/actions/runs/44030460636)：Python 3.11、3.12 的 GET / EDA POST 重定向凭据不转发回归测试通过。
- 当前 PR #5 HEAD：`173791844e47704e972d9d3b8c4d48d721e1fbaa`。PR 描述记录的 Agent Security 与 Gateway CI 已在该 HEAD 通过。

### 5.2 后续代码 HEAD 与 CI 验证（2026-11-17）

原 Host-A 双 Agent EXE 的构建 / 实机验收基线仍为本文档顶部记录的 `173791844e47704e972d9d3b8c4d48d721e1fbaa`，不因后续代码更新而改变。

后续 PR 代码 HEAD 更新至 `101d8baf5787f36dad3b67ae274b9626e09d5321`，其中包含 Shell 串口提交异常处理修复（commit `101d8ba fix(shell): mark uncertain serial submissions unknown`）：提交结果不确定时将请求标记为 `unknown`，返回 HTTP 503 和结构化错误，保留原 request_id 供轮询，并记录 `SHELL_FRAME_SUBMISSION_UNCONFIRMED`，避免客户端误以为可安全重提。

该代码 HEAD 的回归验证结果：

- Python 3.10 全量测试：133 / 133 PASS。
- `test_request_api_http.py`：21 / 21 PASS，包含 uncertain Shell submission 状态与轮询回归测试。
- MCP Gateway 专用虚拟环境：14 / 14 PASS。
- `git diff --check`：PASS（Shell 修复提交前的变更检查）。

GitHub Actions 对 `101d8baf...` 的验证：

- [Agent Security PR Run #38](https://github.com/scanligithub/airbridge/actions/runs/44044449477)：Python 3.10、3.11、3.12 全部成功。
- [Agent Security push Run #37](https://github.com/scanligithub/airbridge/actions/runs/44044438830)：Python 3.10、3.11、3.12 全部成功。
- [MCP Gateway PR Run #41](https://github.com/scanligithub/airbridge/actions/runs/44044449488)：Python 3.11、3.12 全部成功。
- 上述 3 个 workflow run 均为 `completed / success`，合计 8 个 matrix jobs 全部成功。

**范围说明：** 上述 GitHub Actions 和本机回归结果验证的是 `101d8ba` 代码 HEAD。新增的 `tools/airbridge_agent_hosta_ownership_probe.py` 及本次验收文档改动是在该 HEAD 之后加入工作区、尚未包含在该 commit / CI run 中；合成探针已另行在本机独立执行并通过，不能把它描述为已由上述 CI 运行验证。

## 6. 明确未执行 / 未覆盖项目

- 未发送 EDA Execute 或 Shell POST。
- 未产生 ABFP 执行帧。
- 未执行 Capture Tcl、Allegro SKILL 或任何真实 EDA 操作。
- 未验证 Host-B 在线时的 EDA versions 查询。
- 原双 Agent 正式只读探针中的随机未归属 UUID 404 **不等于**跨 Agent 请求所有权隔离实测。新增的 Host-A synthetic ownership probe 已在隔离临时 HTTP 服务中预置 owner 请求，并验证 owner 200、non-owner 404，以及 non-owner 与 random-unknown 的公开错误完全一致。这证明 HTTP 层合成请求归属隔离，但 Host-A 仍未创建或查询真实 EDA request_id，因此不属于真实 EDA 端到端验收。真实请求状态归属隔离另有 CI HTTP 集成测试 `test_request_status_is_not_visible_to_another_agent` 覆盖。
- Host-A synthetic probe 已直接验证缺少 `request.read` scope 的 HTTP 403，以及状态查询配额超限的 HTTP 429 / `Retry-After`。它没有测试 EDA Execute 403、Shell scope 拒绝、真实 EDA 执行请求的 429，也没有发送任何 EDA / Shell POST；这些具体路径仍不属于本次 Host-A 实测范围，执行开关始终保持关闭。

## 7. 验收结论

**M2.3 Host-A 双 Agent 身份认证、最小只读授权、loopback API 接入、MCP 连接、Token 即时撤销和审计验收通过；独立 synthetic ownership probe 的 HTTP 层请求归属隔离、缺少 request.read scope 的 403、状态查询配额 429 / Retry-After 也已实测通过。**

此结论仅覆盖本记录列出的 Host-A 只读与合成 HTTP 测试范围，不代表 Host-B EDA versions 查询或真实 EDA request_id 端到端验收完成，也不代表 EDA Execute / Shell 403、真实 EDA 执行配额 429 已在 Host-A 实测，更不代表 Agent 获得独立 Runtime Session / Capture Interpreter。PR #5 仍按 GitHub 审查与合并流程另行决定。
