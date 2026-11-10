# OrCAD Capture 原理图启动、开图与关闭流程

- 文档状态：当前实现基线 / 已完成主机 B 实机验证
- 适用程序：AirBridge（主机 A 与主机 B 使用同一套代码）
- 适用工具：OrCAD Capture
- 当前目标设计：`RK3588_WX8116-SCH-V2T.DSN`
- 对应实现基线：`ba562740bb8f50edeb21ef512866661225be8c7d`
- 验证结论：用户已确认可以顺畅打开和关闭原理图；此前通过启动期 Tcl `Open` 打开设计时出现的恢复提示，在改为 Capture 命令行传入 DSN 后不再影响当前工作流程。

---

## 1. 设计目标与基本原则

Capture 的生命周期拆分为三个独立阶段：

1. **启动 Capture**：由 AirBridge 在主机 B 启动 `capture.exe`。
2. **打开原理图**：由 Capture 原生启动参数接收 DSN 路径并打开设计。
3. **关闭 Capture**：由主机 A 的独立关闭按钮发出关闭请求，主机 B 对 AirBridge 跟踪的 Capture 进程发送正常窗口关闭消息。

必须遵循以下原则：

- 主机 A 只负责界面操作和通过 AirBridge 通信链路下发命令。
- Cadence 程序实际运行在主机 B，设计文件路径也是主机 B 本地路径。
- Tcl 只用于 Capture 启动期的轻量初始化和环境探针，不负责打开 DSN。
- 不在 `capAutoLoad` 启动钩子中使用 `Open`、`after` 或 `vwait` 来打开设计或等待设计打开。
- Capture 启动后保持运行，不因启动探针执行完成而自动关闭。
- 关闭操作是独立命令，正常情况下使用 Windows `WM_CLOSE`，不以强制结束进程作为正常关闭方式。
- Capture 的启动、进程跟踪和关闭必须与 Allegro 分别管理，互不影响。

## 2. 当前固定目标设计

当前「打开原理图」按钮使用以下主机 B 本地路径：

```text
E:\\_airbridge_temp\\RK3588_PDF_Output\\RK3588_WX8116-SCH-V2T.DSN
```

该路径只在主机 B 上有意义。主机 A 不需要能够访问该文件，也不应在主机 A 检查它是否存在。

## 3. 打开原理图：完整执行流程

### 3.1 主机 A：按钮与任务载荷

用户在主机 A 的 AirBridge 界面点击「打开原理图」。

`AirBridgeMainWindow.trigger_builtin_orcad_test()` 准备：

- 内置 Tcl 启动探针 `BUILTIN_TEST_ORCAD_TCL`
- 脚本提示名 `builtin_test.tcl`
- 当前选择的 Cadence 版本
- `keep_open=True`
- `target_file`：上述 DSN 完整路径

随后调用：

```python
self._dispatch_script(
    BUILTIN_TEST_ORCAD_TCL,
    self.current_hint,
    ver,
    keep_open=True,
    target_file=dsn_path,
)
```

`_dispatch_script()` 将参数序列化为 JSON，并通过 `FrameType.EDA_TASK_SUBMIT` 发送。

关键字段示意：

```json
{
  "script": "<轻量 Capture Tcl 探针>",
  "hint": "builtin_test.tcl",
  "version": "auto",
  "keep_open": true,
  "target_file": "E:\\_airbridge_temp\\RK3588_PDF_Output\\RK3588_WX8116-SCH-V2T.DSN"
}
```

JSON 序列化会负责对反斜杠进行转义；主机 B 解析后得到正常的 Windows 路径字符串。

### 3.2 主机 B：接收任务并传递参数

主机 B 的 `SerialWorker._handle_incoming_frame()` 收到 `EDA_TASK_SUBMIT` 后解析 JSON，并启动后台执行线程。

参数传递关系：

```text
SerialWorker._handle_incoming_frame()
    └─ task_data["target_file"]
       └─ _exec_eda_async(..., target_file, ..., keep_open)
          └─ EDAExecutor.execute_script(
                 ...,
                 target_file=target_file,
                 keep_open=keep_open
             )
             └─ _run_orcad_tcl(..., target_file=target_file)
```

主机 B 使用本机扫描到的 Cadence 安装信息选择 Capture。当前实机测试日志显示使用 SPB 17.2：

```text
D:\\Cadence\\SPB_17.2\\tools\\bin\\capture.exe
```

### 3.3 capAutoLoad：只执行轻量 Tcl 探针

`EDAExecutor._run_orcad_tcl()` 生成以下临时文件：

- `_user_task.tcl`：本次内置 Tcl 探针。
- `_bootstrap.tcl`：日志捕获、环境变量设置、加载用户 Tcl 和完成标记。
- `_cadence_stdout.log`：Capture Tcl 输出日志。
- `_orcad_done.flag`：表示 Tcl 探针执行结束。
- `zz_airbridge_autorun.tcl`：注入到 Capture 的 `capAutoLoad` 目录的一次性引导钩子，执行后自删除。

当前内置 Tcl 探针只输出版本、CDSROOT、Tcl 版本和工作目录等信息。它不打开 DSN，也不等待事件变量。

**禁止恢复以下启动期阻塞开图逻辑：**

```tcl
after 5000 ...
vwait ...
Open $dsn_path
```

保持 `capAutoLoad` 钩子快速执行并返回，让 Capture 自己完成正常初始化。

### 3.4 Capture 原生启动参数打开 DSN

`EDAExecutor._run_orcad_tcl()` 将命令构造成参数列表：

```python
cmd = [capture_exe]
if target_file:
    cmd.append(target_file)
```

因此实际启动形式为：

```powershell
D:\\Cadence\\SPB_17.2\\tools\\bin\\capture.exe "E:\\_airbridge_temp\\RK3588_PDF_Output\\RK3588_WX8116-SCH-V2T.DSN"
```

由 Capture 自己解析 DSN 启动参数并打开设计。这里不通过 Tcl Dbo 的 `GetDesignAndSchematics` 或 Tcl GUI `Open` 命令代替原生开图。

### 3.5 探针完成后保持 Capture 打开

`EDAExecutor._spawn_and_monitor()` 检测到 `_orcad_done.flag` 后：

- 因为本次任务的 `keep_open=True`，只报告 Tcl 探针已执行完成。
- 不调用 `request_graceful_close()`。
- 不向 Capture 发送 `WM_CLOSE`。
- 继续保留 Capture 进程句柄，登记到 `managed_processes["capture"]`。
- 结束本次探针监控线程，但不结束 Capture GUI。

此后用户可以在主机 B 的 Capture 窗口中正常查看、编辑和保存原理图。

## 4. 关闭原理图：完整执行流程

### 4.1 主机 A：点击独立关闭按钮

用户点击「关闭原理图Capture」。

`AirBridgeMainWindow.trigger_close_orcad()` 调用：

```python
self._dispatch_close_eda_application("capture")
```

主机 A 发送独立协议帧：

- 帧类型：`FrameType.EDA_APP_CLOSE`（`0x14`）
- JSON 载荷：

```json
{
  "application": "capture"
}
```

这不是重新提交 Tcl 脚本，也不依赖原启动任务仍然运行。

### 4.2 主机 B：定位 AirBridge 启动的 Capture

主机 B 收到 `EDA_APP_CLOSE` 后，在后台线程调用：

```python
self.eda_executor.close_application("capture", log_callback)
```

执行器查询：

```python
self.managed_processes["capture"]
```

只有由当前运行中的 AirBridge 实例启动并跟踪的 Capture 进程才会被本关闭命令处理。若找不到记录，AirBridge 会明确报告未找到受管实例，不会扫描并关闭用户手动启动的其他 Capture 进程。

### 4.3 正常关闭与等待

执行器对受管进程的可见顶层窗口发送 Windows `WM_CLOSE`：

- 不使用 `taskkill /F` 作为正常关闭手段。
- 不直接关闭类名为 `#32770` 的模态对话框。
- 最多等待 15 秒确认进程退出。

结果处理：

| 结果 | AirBridge 行为 |
|---|---|
| Capture 正常退出 | 清除 `managed_processes["capture"]` 记录并报告成功 |
| 出现保存提示或确认对话框，15 秒内未退出 | 保留进程记录并报告需要人工处理 |
| 没有受管 Capture 进程 | 报告未找到由 AirBridge 启动的实例，不关闭其他进程 |

如有未保存设计，用户应在 Capture 中自行完成保存或取消关闭提示，再处理关闭流程。AirBridge 不替用户决定是否丢弃未保存修改。

## 5. 可重复的启停循环

正常操作可以重复执行：

```text
点击「打开原理图」
    ↓
Capture 启动并由命令行打开目标 DSN
    ↓
Tcl 探针完成，Capture 保持运行
    ↓
用户编辑 / 保存原理图
    ↓
点击「关闭原理图Capture」
    ↓
WM_CLOSE，Capture 正常退出
    ↓
清除该 Capture 进程记录
    ↓
再次点击「打开原理图」
```

每次启动都会重新创建本次任务需要的临时脚本、日志和完成标记；每次正常关闭都会释放当前受管进程记录。

## 6. 关键实现位置

| 文件 | 函数 / 常量 | 职责 |
|---|---|---|
| `airbridge_app.py` | `BUILTIN_TEST_ORCAD_TCL` | 轻量 Tcl 环境探针，不执行开图 |
| `airbridge_app.py` | `trigger_builtin_orcad_test()` | 设置固定 DSN 路径和 `keep_open=True` |
| `airbridge_app.py` | `_dispatch_script()` | 将 `target_file` 等参数写入任务 JSON |
| `protocol.py` | `EDA_TASK_SUBMIT` | 传递启动任务 |
| `protocol.py` | `EDA_APP_CLOSE` | 独立关闭 Cadence 应用 |
| `airbridge_app.py` | `SerialWorker._handle_incoming_frame()` | 主机 B 接收启动与关闭命令 |
| `airbridge_app.py` | `_exec_eda_async()` | 将目标路径和保持打开参数传给执行器 |
| `eda_engine.py` | `EDAExecutor.execute_script()` | 识别 Tcl 并分派 Capture 执行 |
| `eda_engine.py` | `_run_orcad_tcl()` | 注入 Tcl 引导钩子并构造 Capture 命令行 |
| `eda_engine.py` | `_spawn_and_monitor()` | 监控 Tcl 探针，同时在 `keep_open=True` 时保留 GUI |
| `eda_engine.py` | `close_application()` | 独立正常关闭受管 Capture 进程 |

## 7. 实机验收标准

以下项目构成 Capture 启停流程的验收标准：

- [x] 主机 A 点击「打开原理图」后，主机 B 能启动 Capture。
- [x] Capture 能通过原生启动参数打开指定 DSN。
- [x] 不再依赖 capAutoLoad 中的 Tcl `Open`、`after`、`vwait` 开图。
- [x] Capture 启动后保持运行，不随 Tcl 探针结束而关闭。
- [x] 主机 A 可以通过独立按钮关闭由 AirBridge 启动的 Capture。
- [x] 打开、关闭可以多次循环执行。
- [x] 用户已确认本方案能够顺畅打开和关闭原理图。

## 8. 当前约束与注意事项

1. **固定路径**：当前「打开原理图」按钮把目标 DSN 路径固定在主机 A 的内置按钮逻辑中。更换设计文件时，需要调整该路径或后续增加路径选择功能。
2. **关闭范围**：独立关闭按钮只关闭当前 AirBridge 进程所跟踪的 Capture 实例，不接管用户手动打开的 Capture。
3. **进程记录是内存状态**：`managed_processes` 目前保存在主机 B 的 AirBridge 进程内。Capture 尚未关闭时，不要重启主机 B 的 AirBridge；否则新的 AirBridge 实例不会自动继承旧实例的进程句柄记录。
4. **保存决策由用户负责**：遇到未保存设计或模态对话框时，AirBridge 不强制退出、不替用户选择保存或丢弃。
5. **与 Allegro 分离**：本说明只定义 Capture 原理图流程。Allegro PCB 使用自己的 SKILL 启动探针、进程记录和关闭命令，不应与 Capture 的进程状态混用。
6. **不要回退到启动期 Tcl 开图**：已验证有效的当前方案是将 DSN 作为 `capture.exe` 启动参数传入；不要把 `Open` 或阻塞等待重新放入 capAutoLoad 钩子。

---

**当前标准：Capture 由 AirBridge 启动，DSN 由 Capture 原生启动参数打开，Tcl 只做轻量初始化，关闭由独立的 AirBridge 命令完成。**
