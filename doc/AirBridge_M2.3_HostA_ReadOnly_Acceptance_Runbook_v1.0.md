# AirBridge M2.3 — Host A 只读安全验收操作手册

- 更新日期：2026-11-17
- 适用分支：feature/m2-agent-security
- 当前 PR：#5（目标 main；Ready for review；尚未合并）
- 验收工具：tools/airbridge_agent_hosta_acceptance.py

## 1. 验收目标与安全边界

本操作只验证 Host A 的 Agent 身份认证和只读权限：

- 无 Authorization / 错误 Token 返回 HTTP 401。
- Antigravity、OpenCode 分别使用不同 Token。
- 两个 Token 只具有 runtime.read、eda.versions.read、request.read。
- 两个 Agent 均可读取 Runtime capabilities。
- request.read scope 可用；随机且不属于任何 Agent 的 UUID 返回 404 / not_found。
- 可验证已撤销 Token 在运行中的 HTTP 服务上返回 401。
- 审计记录能按 Agent 查询，且不包含明文 Token 或脚本正文。

**本操作不执行 EDA 或 Shell 请求。** 验收脚本只发送 loopback HTTP GET，不发送 POST，不产生 ABFP 帧，不调用 Capture Tcl 或 Allegro SKILL。EDA 执行开关在整个阶段保持关闭。

本次 Host A 实测不能证明真实请求的跨 Agent 所有权隔离：只读 Agent 不会创建真实 EDA request_id。该隔离已有 Agent Security / HTTP CI 测试覆盖；如需 Host A 实测，必须另行设计不触发 EDA 的合成请求 fixture，不能用真实 EDA 执行来换取一个 request_id。

## 2. 更新 Host A 工作区

在 Host A 仓库根目录打开 PowerShell：

\`\`\`powershell
git status --short --branch
git branch --show-current
git fetch origin
git pull --ff-only origin feature/m2-agent-security
git rev-parse --short HEAD
\`\`\`

确认当前分支为 feature/m2-agent-security，且 HEAD 已包含本操作手册和只读验收工具。若存在未提交的 tracked 修改，先停止并检查，不要自动 stash、reset 或覆盖。历史 build_* / dist_* 目录属于未跟踪构建产物，不要删除。

本地更新后可先运行：

\`\`\`powershell
py -3.10 -m py_compile agent_security.py tools/airbridge_agent_admin.py tools/airbridge_agent_hosta_acceptance.py
py -3.10 -m unittest discover -s test -p "test_agent_security.py" -v
py -3.10 -m unittest discover -s test -p "test_request_api_http.py" -v
\`\`\`

## 3. 确认服务端安全配置

在启动 AirBridge 桌面进程的 PowerShell 环境中设置：

\`\`\`powershell
$env:AIRBRIDGE_AGENT_SECURITY_DB = Join-Path $env:LOCALAPPDATA "AirBridge\agent_security.sqlite3"
$env:AIRBRIDGE_ALLOW_EDA_EXECUTE = "0"
$env:AIRBRIDGE_AGENT_READ_RPM = "60"
$env:AIRBRIDGE_AGENT_STATUS_RPM = "120"
$env:AIRBRIDGE_AGENT_EXECUTE_RPM = "5"
$env:AIRBRIDGE_AGENT_MAX_PENDING_EDA = "1"
\`\`\`

若你原来使用自定义 AIRBRIDGE_AGENT_SECURITY_DB，必须把该变量设置为原数据库的准确路径；不要创建第二个数据库。管理员 CLI、AirBridge 服务端必须使用相同 Windows 用户及相同数据库路径。

确保 AirBridge 服务进程在上述环境下启动。若它已经运行，应先正常关闭并保存工作，再通过现有的正常启动方式从该 PowerShell 环境启动；不要强制结束进程。只在设置环境变量后启动一个新 PowerShell，不会改变已经运行的 Explorer 或 AirBridge 进程环境。

确认 8888 只监听 loopback：

\`\`\`powershell
Get-NetTCPConnection -State Listen -LocalPort 8888 |
  Format-Table LocalAddress, LocalPort, OwningProcess
\`\`\`

预期 LocalAddress 为 127.0.0.1。若显示 0.0.0.0 或 LAN 地址，停止验收并先修复监听范围。

## 4. 签发两个独立只读 Token

在仓库根目录执行，每个命令只运行一次：

\`\`\`powershell
py -3.10 tools/airbridge_agent_admin.py issue --agent-id antigravity --scope runtime.read --scope eda.versions.read --scope request.read --label host-a-antigravity
py -3.10 tools/airbridge_agent_admin.py issue --agent-id opencode --scope runtime.read --scope eda.versions.read --scope request.read --label host-a-opencode
\`\`\`

Token 明文只在签发时显示一次。将每个 Token 直接复制到对应 MCP Host 的本地配置，不要贴入聊天、Issue、PR、日志或仓库文件。不要把两个 Token 互换。

检查凭证元数据和 scope：

\`\`\`powershell
py -3.10 tools/airbridge_agent_admin.py list --agent-id antigravity
py -3.10 tools/airbridge_agent_admin.py list --agent-id opencode
\`\`\`

必须确认两个 Agent 各有一枚 enabled 凭证，scope 恰好是 runtime.read、eda.versions.read、request.read；没有 eda.execute、Shell、api.admin 或通配权限。

## 5. 更新 MCP Host 配置

分别在 Antigravity 和 OpenCode 的 MCP Gateway 配置中设置：

\`\`\`json
{
  "env": {
    "AIRBRIDGE_BASE_URL": "http://127.0.0.1:8888",
    "AIRBRIDGE_AGENT_TOKEN": "各自独立的本地 Token",
    "AIRBRIDGE_ENABLE_EDA_EXECUTE": "0"
  }
}
\`\`\`

保持两边 Gateway 执行开关均为 0。保存配置并按客户端的正常方式重启 MCP Host / Gateway 子进程。配置文件应只允许当前 Windows 用户读取；不要把包含 Token 的完整配置文件上传或提交到 Git。

## 6. 运行只读验收探针

在设置了与 AirBridge 服务端相同的 AIRBRIDGE_AGENT_SECURITY_DB 环境变量的 PowerShell 中执行：

\`\`\`powershell
py -3.10 tools/airbridge_agent_hosta_acceptance.py
\`\`\`

探针会通过隐藏输入分别读取两枚 Token。Token 不会被打印或写入文件。脚本先用本地 SQLite 校验 Agent ID 和精确的只读 scope，再发送 HTTP GET：

| 检查 | 预期 |
|---|---|
| 无 Authorization | 401 / INVALID_AGENT_TOKEN |
| 虚构 Token | 401 / INVALID_AGENT_TOKEN |
| Antigravity capabilities | 200 |
| Antigravity 随机未归属 request UUID | 404 / not_found |
| OpenCode capabilities | 200 |
| OpenCode 随机未归属 request UUID | 404 / not_found |

若 Host B / 串口链路已正常连接，可增加 EDA versions 检查：

\`\`\`powershell
py -3.10 tools/airbridge_agent_hosta_acceptance.py --check-eda-versions
\`\`\`

这仍然只是 GET 查询，不会执行 EDA 脚本。

## 7. 验证撤销与审计

建议只使用一枚临时探针 Token 验证撤销，不要撤销 Antigravity / OpenCode 正式 Token：

\`\`\`powershell
py -3.10 tools/airbridge_agent_admin.py issue --agent-id m23-revoke-probe --scope runtime.read --scope eda.versions.read --scope request.read --label temporary-revocation-test
\`\`\`

记下输出中的 token_id 和 Token 明文。然后撤销该临时 Token：

\`\`\`powershell
py -3.10 tools/airbridge_agent_admin.py revoke --token-id <上一步的-token_id>
\`\`\`

用以下命令验证撤销记录：

\`\`\`powershell
py -3.10 tools/airbridge_agent_admin.py list --agent-id m23-revoke-probe
py -3.10 tools/airbridge_agent_hosta_acceptance.py --check-revoked
\`\`\`

探针会隐藏输入已撤销的临时 Token，并验证本地凭证库和正在运行的 HTTP 服务均拒绝它（401）。完成后无需删除审计记录；保留撤销元数据作为验收证据。

检查两枚正式 Token 的审计记录：

\`\`\`powershell
py -3.10 tools/airbridge_agent_admin.py audit --agent-id antigravity --limit 30
py -3.10 tools/airbridge_agent_admin.py audit --agent-id opencode --limit 30
\`\`\`

确认记录只包含 Agent、路径、HTTP 状态、decision / reason_code、request_id（如有）、payload 长度和摘要；不得包含 Bearer Token 或脚本正文。

## 8. 不在本轮进行的测试

- 不调用 airbridge_eda_execute。
- 不向 /api/v1/eda/execute、/api/v1/shell/exec 或旧版执行别名发送 POST。
- 不切换 AIRBRIDGE_ALLOW_EDA_EXECUTE 或 AIRBRIDGE_ENABLE_EDA_EXECUTE 为 1。
- 不为制造 request_id 而执行空 Tcl、测试 Tcl、SKILL 或其他脚本。
- 不通过密集请求人为触发正式 Agent 的配额限制；配额 429 和 Retry-After 已由 Windows CI 集成测试覆盖。
- 不把随机 UUID 返回的 404 描述为真实跨 Agent 所有权隔离验收。真实归属隔离由 CI 测试覆盖，Host A 的无执行合成 fixture 留待单独设计。

## 9. 验收结果记录

完成后记录以下项目，不要记录 Token 值：

- Host A 代码 HEAD：
- AirBridge 监听地址：
- Agent Security DB 路径（可只写文件名或脱敏路径）：
- Antigravity capabilities：
- OpenCode capabilities：
- 两 Agent request.read 未归属 UUID：
- 无 Token / 错误 Token：
- 临时 Token 撤销：
- Agent 审计记录：
- Host B EDA versions（已连接时）：
- EDA 两个执行开关是否保持 0：

本轮全部通过后，M2.3 Host-A 只读身份与授权基础验收通过。PR #5 的合并仍按项目审查和 GitHub 流程另行决定。
