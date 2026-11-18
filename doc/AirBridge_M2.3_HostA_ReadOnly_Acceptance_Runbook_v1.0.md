# AirBridge M2.3 — Host A 只读安全验收操作手册

- 更新日期：2026-11-17
- 适用分支：feature/m2-agent-security
- 当前 PR：#5（目标 main；Ready for review；尚未合并）
- 验收工具：tools/airbridge_agent_hosta_acceptance.py
- 独立合成所有权探针：tools/airbridge_agent_hosta_ownership_probe.py（临时数据库、假 Worker、随机 loopback HTTP 服务；不连接正式服务）

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

真实 EDA request_id 的跨 Agent 隔离与合成请求隔离需要区分。为避免执行真实 EDA，本项目增加了独立探针 `tools/airbridge_agent_hosta_ownership_probe.py`：它在临时 SQLite 数据库、内存 RequestTracker、假 Worker 和随机 loopback 端口上启动隔离 HTTP 服务，预置一条由 fixture-owner 拥有的合成完成请求，再只发送 GET 查询。它验证请求所有者得到 HTTP 200、另一 Agent 得到 HTTP 404 / not_found，且非所有者与随机未知 UUID 的公开错误完全一致；同时验证无 request.read scope 返回 403，以及状态查询限额返回 429 / Retry-After。本探针不访问正式 AirBridge 8888 服务、不使用生产凭据库、不连接串口、不发送 POST、不发送 ABFP 帧，也不执行 Capture Tcl / Allegro SKILL。2026-11-17 Host-A 实测全部通过。该结果验证了 HTTP 层的合成请求归属隔离，不等同于真实 EDA 请求端到端隔离验收。

## 2. 更新 Host A 工作区

在 Host A 仓库根目录打开 PowerShell：

```powershell
git status --short --branch
git branch --show-current
git fetch origin
git pull --ff-only origin feature/m2-agent-security
git rev-parse --short HEAD
```

确认当前分支为 feature/m2-agent-security，且 HEAD 已包含本操作手册和只读验收工具。若存在未提交的 tracked 修改，先停止并检查，不要自动 stash、reset 或覆盖。历史 build_* / dist_* 目录属于未跟踪构建产物，不要删除。

本地更新后可先运行：

```powershell
py -3.10 -m py_compile agent_security.py tools/airbridge_agent_admin.py tools/airbridge_agent_hosta_acceptance.py
py -3.10 -m unittest discover -s test -p "test_agent_security.py" -v
py -3.10 -m unittest discover -s test -p "test_request_api_http.py" -v
```

Host A 必须运行包含 M2.3 代码的程序，不能继续使用旧的 M1 / main 分支 EXE。若用 PyInstaller EXE，建议使用独立输出目录构建，避免覆盖历史 build / dist 产物：

```powershell
py -3.10 -m PyInstaller --version
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$distDir = "dist_m23_hosta_$stamp"
$buildDir = "build_m23_hosta_$stamp"
py -3.10 -m PyInstaller --distpath $distDir --workpath $buildDir AirBridge.spec
```

构建完成后，后续从设置好安全环境变量的同一个 PowerShell 启动：

```powershell
Start-Process -FilePath (Resolve-Path "$distDir\AirBridge.exe").Path
```

每次使用新的带时间戳目录，不覆盖以前的 build / dist 产物；失败构建目录也先保留，不要清理。

如按源码方式运行，则用项目现有 Python 3.10 环境从当前分支启动 `airbridge_app.py`。不要从 Explorer 启动一个继承不到本终端环境变量的旧进程。

## 3. 确认服务端安全配置

在启动 AirBridge 桌面进程的 PowerShell 环境中设置：

```powershell
$env:AIRBRIDGE_AGENT_SECURITY_DB = Join-Path $env:LOCALAPPDATA "AirBridge\agent_security.sqlite3"
$env:AIRBRIDGE_ALLOW_EDA_EXECUTE = "0"
$env:AIRBRIDGE_AGENT_READ_RPM = "60"
$env:AIRBRIDGE_AGENT_STATUS_RPM = "120"
$env:AIRBRIDGE_AGENT_EXECUTE_RPM = "5"
$env:AIRBRIDGE_AGENT_MAX_PENDING_EDA = "1"
```

若你原来使用自定义 AIRBRIDGE_AGENT_SECURITY_DB，必须把该变量设置为原数据库的准确路径；不要创建第二个数据库。管理员 CLI、AirBridge 服务端必须使用相同 Windows 用户及相同数据库路径。

确保 AirBridge 服务进程在上述环境下启动。若它已经运行，应先正常关闭并保存工作，再通过现有的正常启动方式从该 PowerShell 环境启动；不要强制结束进程。只在设置环境变量后启动一个新 PowerShell，不会改变已经运行的 Explorer 或 AirBridge 进程环境。

确认 8888 只监听 loopback：

```powershell
Get-NetTCPConnection -State Listen -LocalPort 8888 |
  Format-Table LocalAddress, LocalPort, OwningProcess
```

预期 LocalAddress 为 127.0.0.1。若显示 0.0.0.0 或 LAN 地址，停止验收并先修复监听范围。

## 4. 签发两个独立只读 Token

在仓库根目录执行，每个命令只运行一次：

```powershell
py -3.10 tools/airbridge_agent_admin.py issue --agent-id antigravity --scope runtime.read --scope eda.versions.read --scope request.read --label host-a-antigravity
py -3.10 tools/airbridge_agent_admin.py issue --agent-id opencode --scope runtime.read --scope eda.versions.read --scope request.read --label host-a-opencode
```

Token 明文只在签发时显示一次。将每个 Token 直接复制到对应 MCP Host 的本地配置，不要贴入聊天、Issue、PR、日志或仓库文件。不要把两个 Token 互换。

检查凭证元数据和 scope：

```powershell
py -3.10 tools/airbridge_agent_admin.py list --agent-id antigravity
py -3.10 tools/airbridge_agent_admin.py list --agent-id opencode
```

必须确认两个 Agent 各有一枚 enabled 凭证，scope 恰好是 runtime.read、eda.versions.read、request.read；没有 eda.execute、Shell、api.admin 或通配权限。

## 5. 更新 MCP Host 配置

分别在 Antigravity 和 OpenCode 的 MCP Gateway 配置中设置：

```json
{
  "env": {
    "AIRBRIDGE_BASE_URL": "http://127.0.0.1:8888",
    "AIRBRIDGE_AGENT_TOKEN": "各自独立的本地 Token",
    "AIRBRIDGE_ENABLE_EDA_EXECUTE": "0"
  }
}
```

保持两边 Gateway 执行开关均为 0。保存配置并按客户端的正常方式重启 MCP Host / Gateway 子进程。配置文件应只允许当前 Windows 用户读取；不要把包含 Token 的完整配置文件上传或提交到 Git。

## 6. 运行只读验收探针

在设置了与 AirBridge 服务端相同的 AIRBRIDGE_AGENT_SECURITY_DB 环境变量的 PowerShell 中执行：

```powershell
py -3.10 tools/airbridge_agent_hosta_acceptance.py
```

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

```powershell
py -3.10 tools/airbridge_agent_hosta_acceptance.py --check-eda-versions
```

这仍然只是 GET 查询，不会执行 EDA 脚本。

### 6.1 独立合成所有权 / 403 / 429 探针

完成正式只读探针后，可单独运行以下探针，验证 HTTP 层的请求归属隔离和只读状态查询授权边界：

```powershell
py -3.10 tools/airbridge_agent_hosta_ownership_probe.py
```

该脚本在临时目录中创建临时 SQLite 凭证库，以内存 RequestTracker 和禁止发送帧的假 Worker 启动随机 loopback 端口上的隔离 HTTP 服务。它预置一条合成完成请求，并且只发送 GET 请求。预期检查包括：请求所有者查询返回 200；另一 Agent 查询返回 404 / not_found；非所有者与随机未知 UUID 的公开错误完全一致；缺少 request.read scope 返回 403；状态查询超过测试配额后返回 429 并带 Retry-After；全过程发送帧次数为 0，Tracker 仅保留预置的合成请求。

该探针不需要正式 Agent Token，不连接正式 AirBridge 8888 服务，不读取生产凭据数据库，不访问 Host B 或串口，不发送 POST / ABFP 帧，也不执行 EDA、Shell、Capture Tcl 或 Allegro SKILL。临时数据库随脚本退出而清理。2026-11-17 本机实测全部通过。此项只证明隔离 HTTP 服务上的合成请求归属，不替代真实 EDA request_id 的端到端验收。

## 7. 验证撤销与审计

建议只使用一枚临时探针 Token 验证撤销，不要撤销 Antigravity / OpenCode 正式 Token：

```powershell
py -3.10 tools/airbridge_agent_admin.py issue --agent-id m23-revoke-probe --scope runtime.read --scope eda.versions.read --scope request.read --label temporary-revocation-test
```

记下输出中的 token_id 和 Token 明文。然后撤销该临时 Token：

```powershell
py -3.10 tools/airbridge_agent_admin.py revoke --token-id <上一步的-token_id>
```

用以下命令验证撤销记录：

```powershell
py -3.10 tools/airbridge_agent_admin.py list --agent-id m23-revoke-probe
py -3.10 tools/airbridge_agent_hosta_acceptance.py --check-revoked
```

探针会隐藏输入已撤销的临时 Token，并验证本地凭证库和正在运行的 HTTP 服务均拒绝它（401）。完成后无需删除审计记录；保留撤销元数据作为验收证据。

检查两枚正式 Token 的审计记录：

```powershell
py -3.10 tools/airbridge_agent_admin.py audit --agent-id antigravity --limit 30
py -3.10 tools/airbridge_agent_admin.py audit --agent-id opencode --limit 30
```

确认记录只包含 Agent、路径、HTTP 状态、decision / reason_code、request_id（如有）、payload 长度和摘要；不得包含 Bearer Token 或脚本正文。

## 8. 不在本轮进行的测试

- 不调用 airbridge_eda_execute。
- 不向 /api/v1/eda/execute、/api/v1/shell/exec 或旧版执行别名发送 POST。
- 不切换 AIRBRIDGE_ALLOW_EDA_EXECUTE 或 AIRBRIDGE_ENABLE_EDA_EXECUTE 为 1。
- 不为制造 request_id 而执行空 Tcl、测试 Tcl、SKILL 或其他脚本。
- 不通过密集请求人为触发正式 Agent 的配额限制；HTTP 429 / Retry-After 已由隔离的 Host-A 合成探针本机验证，并由 Windows CI 集成测试覆盖。
- 随机 UUID 返回的 404 单独看不能证明跨 Agent 隔离。Host-A synthetic ownership probe 已在隔离临时 HTTP 服务中验证 owner 200、non-owner 404，且 non-owner 与 random-unknown 的公开错误一致；该验证属于 HTTP 层合成 fixture，不是真实 EDA request_id 端到端验收。

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
- Host-A synthetic ownership / missing-scope 403 / status-quota 429 probe：
- Synthetic probe 日期：
- Synthetic probe 运行时 Git HEAD（未提交改动另行注明）：
- EDA 两个执行开关是否保持 0：

本轮全部通过后，M2.3 Host-A 只读身份与授权基础验收通过。PR #5 的合并仍按项目审查和 GitHub 流程另行决定。
