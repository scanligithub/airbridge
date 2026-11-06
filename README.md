# AirBridge

物理网闸通用中继器 & EDA 自动化中枢 + 远程 Shell（v2.4）

## 能力概览

- 串口可靠大文件双向传输（滑动窗口）
- 远程文件系统（RFS）
- Cadence OrCAD Tcl / Allegro SKILL 远程执行
- **远程 PowerShell / CMD**（同步 + 异步，实时输出，可强杀，危险命令黑名单）
- 主机 A 本地 HTTP API（`127.0.0.1:8888`）供大模型 / Agent 调用

## 远程 Shell API（主机 A）

前提：A/B 两端均运行 AirBridge，串口已连通。

### 执行命令

```http
POST http://127.0.0.1:8888/api/v1/shell/exec
Content-Type: application/json

{
  "command": "Get-Process | Select-Object -First 5 | Format-Table -AutoSize | Out-String",
  "shell": "powershell",
  "cwd": "E:\\_airbridge_temp",
  "timeout": 60,
  "async": false
}
```

- `async: false`：同步执行，结果经串口 `SHELL_RESULT` 回传并打印到控制台
- `async: true`：后台任务，实时输出经 `SHELL_OUTPUT` 流式回传

### 强杀任务

```http
POST http://127.0.0.1:8888/api/v1/shell/kill
{"task_id": "shell-..."}
```

### 列出任务

```http
POST http://127.0.0.1:8888/api/v1/shell/tasks
{}
```

## 安全

`shell_engine.py` 内置危险命令黑名单（format / shutdown / diskpart / 递归删系统盘等），命中即拒绝执行。

## 打包

```bat
build.bat
```

生成 `dist\\AirBridge.exe`（已包含 shell_engine）。

## 版本

- v2.4：远程 Shell
- v2.3：多版本 Cadence + 大文件吞吐
