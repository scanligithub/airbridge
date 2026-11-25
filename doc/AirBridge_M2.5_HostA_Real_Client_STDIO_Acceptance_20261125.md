# AirBridge M2.5：Host A 双客户端新版 STDIO 实机验收记录

- 验收日期：2026-11-25
- 仓库：`scanligithub/airbridge`
- 验收分支：`docs/m2.5-architecture`
- PR：[ #16 — feat(m2.5): integrate local STDIO Gateway and client adapters into AirBridge.exe](https://github.com/scanligithub/airbridge/pull/16)
- 验收主机：Windows Host A
- 验收性质：当前 PR 构建产物的真实 MCP 客户端启动、进程路径及只读 Tool 调用验收
- 结果：OpenCode 与 Antigravity 均通过新版 EXE 的真实客户端只读调用；Host B EXE 文件哈希与最新 capabilities 握手仍有明确未验收边界。

## 1. 验收范围与安全约束

本记录验证 Antigravity 和 OpenCode 是否通过本机 STDIO MCP Host 启动 PR #16 的同一新版 `AirBridge.exe mcp stdio`，并通过真实客户端执行只读查询。

本轮只调用：

- `airbridge_get_eda_versions`

本轮未调用：

- `airbridge_eda_execute`
- Shell 执行工具
- Tcl / SKILL
- 文件写入或任何设计修改、保存、关闭操作

不通过读取 Token、Secret 或凭据 Hash 来证明身份；既有 Credential Manager / SQLite 检查仅以只读方式检查有效凭据数量与权限范围，不读取敏感列。

## 2. 构建产物与来源

PR #16 当前验收分支 HEAD：

```text
f6f8e804d1709282b3f74c81e37039847f2b92d5
```

本次桌面部署的 EXE 来自 GitHub Actions PR 合并构建引用（synthetic PR merge commit）：

```text
aedad7495da85a15a981dae6d4be4a6990ad0f73
```

Host A EXE：

```text
E:\download\AirBridge-Windows-x64-aedad7495da85a15a981dae6d4be4a6990ad0f73\AirBridge.exe
```

SHA-256：

```text
1C67DA2608180BD72784910644B58957A71E2D4C0D8F80171B170B102BF80EFD
```

文件大小：61,979,449 bytes。

实机迁移前，Host A 新 EXE 的 SHA-256 与上述值完全一致。这里的 source commit 是 PR 合并构建引用；它不应被误称为 PR branch HEAD 本身。

## 3. OpenCode 真实客户端验收

### 3.1 配置迁移

当前用户级配置：

```text
%USERPROFILE%\.config\opencode\opencode.json
```

将 `airbridge-opencode` 的 command 从旧版 `bf085142...` 更新为本记录第 2 节的新版 EXE，保留：

- `mcp stdio`
- `AIRBRIDGE_AGENT_ID=opencode`
- 原有其他 MCP 配置
- 旧 Python Gateway 条目（仍保留并 disabled）

迁移前配置备份已创建，备份 SHA-256 与迁移前原文件一致；迁移后的配置回读校验通过。

### 3.2 实际进程路径

OpenCode 启动的 AirBridge MCP 进程均使用新版路径，且命令参数包含 `mcp stdio`。核对的进程包括：

| AirBridge PID | 父进程 | EXE 路径 |
|---:|---|---|
| 12204 | opencode-cli.exe（PID 15700） | 新版 `aedad749...` |
| 8780 | opencode-cli.exe（PID 15700） | 新版 `aedad749...` |
| 3108 | opencode-cli.exe（PID 15700） | 新版 `aedad749...` |
| 21936 | AirBridge.exe（PID 12204） | 新版 `aedad749...` |
| 13360 | AirBridge.exe（PID 8780） | 新版 `aedad749...` |
| 20500 | AirBridge.exe（PID 3108） | 新版 `aedad749...` |

### 3.3 真实只读 Tool 调用

用户在 OpenCode 中调用 `airbridge-opencode.airbridge_get_eda_versions`：

| 字段 | 结果 |
|---|---|
| isError | `false` |
| HTTP | `200` |
| success | `true` |
| req_id | `275531254` |
| Cadence | SPB 17.2 |

返回的远端环境路径包括：

- CDSROOT：`D:\Cadence\SPB_17.2`
- Capture：`D:\Cadence\SPB_17.2\tools\bin\capture.exe`
- Allegro：`D:\Cadence\SPB_17.2\tools\bin\allegro.exe`

结论：**OpenCode 已通过新版 EXE 完成真实客户端 STDIO 只读调用验收。**

## 4. Antigravity 真实客户端验收

### 4.1 配置迁移

当前用户级配置：

```text
%USERPROFILE%\.gemini\config\mcp_config.json
```

迁移前核对确认 `airbridge-antigravity` 仍指向旧版：

```text
AirBridge-Windows-x64-bf0851422f631a4f1bc755cf61a1ff25934ca130
```

用户授权后，仅将该 Adapter 的 `command` 更新为第 2 节的新版 EXE；保留：

- `args=mcp stdio`
- `AIRBRIDGE_AGENT_ID=antigravity`
- `disabled=false`
- 其他 MCP Server 配置

更新前自动创建备份：

```text
mcp_config.json.airbridge-m25-migration-20261125T101147787.bak
```

新版 EXE 哈希校验、配置写入后回读校验均通过。更新后配置 SHA-256：

```text
4A817156F758CA4A96B5677EEC28F5A58AA7A50C45B5F5733ACE020E79C87905
```

### 4.2 重启后的实际进程路径

用户重启 Antigravity 后，进程核对结果：

| PID | 进程关系 | EXE 路径 |
|---:|---|---|
| 15364 | Antigravity 根进程 | Antigravity.exe |
| 19904 | Antigravity Language Server | language_server.exe |
| 10096 | Language Server 的子进程；MCP STDIO | 新版 `aedad749...` |
| 13192 | AirBridge 子进程 | 新版 `aedad749...` |

PID 10096 与 PID 13192 的实际 `ExecutablePath` 均指向：

```text
E:\download\AirBridge-Windows-x64-aedad7495da85a15a981dae6d4be4a6990ad0f73\AirBridge.exe
```

PID 10096 的父进程链确认属于 Antigravity Language Server；两个 AirBridge 进程均有 `mcp stdio` 参数。

### 4.3 真实只读 Tool 调用

用户重启 Antigravity 后，在真实客户端中调用 `airbridge-antigravity/airbridge_get_eda_versions`：

| 字段 | 结果 |
|---|---|
| isError | `false` |
| HTTP | `200` |
| success | `true` |
| req_id | `276591061` |
| Cadence | SPB 17.2 |

返回的远端环境路径：

- CDSROOT：`D:\Cadence\SPB_17.2`
- Capture：`D:\Cadence\SPB_17.2\tools\bin\capture.exe`
- Allegro：`D:\Cadence\SPB_17.2\tools\bin\allegro.exe`
- Capture autoload：`D:\Cadence\SPB_17.2\tools\capture\tclscripts\capAutoLoad`

结论：**Antigravity 已完成“配置迁移 → 客户端重启 → 新版 STDIO 子进程启动 → 真实只读 Tool 调用成功”的完整验收链。**

## 5. Gateway 与凭据安全边界

Host A Gateway 配置回读显示：

- Gateway 总开关：ON
- Antigravity Agent：enabled
- OpenCode Agent：enabled

既有只读 SQLite 检查确认两个客户端 Agent 均有有效凭据，权限仅为：

```json
["runtime.read", "eda.versions.read", "request.read"]
```

本记录不包含 Token、Secret 或凭据 Hash。配置文件与进程命令行中均未放置 Agent Token；STDIO 子进程通过既有 M2.4 Credential Provider 获取对应 Agent 凭据。

## 6. 汇总

| 编号 | 验收项 | 结果 |
|---|---|---|
| M25-HOSTA-01 | PR #16 新版 EXE SHA-256 校验 | PASS |
| M25-HOSTA-02 | OpenCode Adapter 指向新版 EXE | PASS |
| M25-HOSTA-03 | OpenCode 实际 STDIO 子进程路径 | PASS |
| M25-HOSTA-04 | OpenCode 真实只读 Tool 调用 | PASS |
| M25-HOSTA-05 | Antigravity Adapter 指向新版 EXE | PASS |
| M25-HOSTA-06 | Antigravity 重启后实际 STDIO 子进程路径 | PASS |
| M25-HOSTA-07 | Antigravity 真实只读 Tool 调用 | PASS |
| M25-HOSTA-08 | 两 Agent 只读 Scope 核对 | PASS |
| M25-HOSTA-09 | 未调用执行类或写入类工具 | PASS |

**Host A 上两个真实 MCP 客户端均已使用同一新版 AirBridge.exe 完成只读 STDIO 验收。**

## 7. 未验收边界

以下事项不得因本次 PASS 而推定通过：

1. **Host B AirBridge.exe 文件哈希尚未独立核实。** 两个客户端返回了 Host B Cadence SPB 17.2 的版本和路径信息；这不能替代 Host B 本地 AirBridge.exe 的 SHA-256 检查，也不能证明 A/B 两机部署文件字节完全一致。
2. **新版 EXE 的最新 capabilities 握手尚未通过。** 先前对新版 EXE 的独立 STDIO 测试中，`airbridge_get_capabilities` 曾返回 `state=unconfirmed`、`negotiated=false`，错误为 peer 未响应 capability handshake。此次两个客户端通过的是 `airbridge_get_eda_versions`，不得把它写成 capabilities 握手通过。
3. **已建立 STDIO 会话的策略即时拦截边界**仍以专门的冻结进程策略测试为准，不把 GUI 将 Adapter 禁用、客户端卸载工具误记为服务端拒绝测试。
4. 本记录不验收 EDA Execute、Tcl / SKILL、Shell、文件写入或 Capture / Allegro 生命周期。
5. 本记录不验收跨机器 MCP HTTPS、证书、TLS、OAuth Broker、SSE 或 WebSocket；这些不属于 ADR-013 当前架构。
6. 本记录仅覆盖 Host A 的桌面客户端实测，不等同于 Host A / Host B 双机最终发布验收。

## 8. PR 状态

记录写入时 PR #16：

- 分支：`docs/m2.5-architecture`
- HEAD：`f6f8e804d1709282b3f74c81e37039847f2b92d5`
- 状态：Open / Draft / not merged

本记录仅补充验收证据，不代表授权合并、转为 Ready for review 或发布。
