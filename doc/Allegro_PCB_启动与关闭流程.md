# Allegro PCB 启动、打开设计与关闭流程

- 文档状态：远程文件选择与 BRD 路径传递已实现；交互式自动开板待主机 B 实机验收
- 适用程序：AirBridge（主机 A 与主机 B 使用同一套代码）
- 适用工具：Cadence Allegro PCB Designer
- 设计文件：每次由主机 A 通过 RFS 远程浏览选择主机 B 上的 `.brd`
- 当前开板方式：SKILL 启动脚本调用 `axlOpenDesign()` 交互式 API；不是 Batch API
- 历史验证：此前启动 Allegro、手动打开 PCB、正常关闭及多次循环已由用户实机确认；本次自动开板改造尚待复测
- 代码位置：仓库当前 main 分支的 airbridge_app.py、eda_engine.py、protocol.py

---

## 1. 目标与基本原则

Allegro 的生命周期分为三个相互独立的步骤：

1. **选择 PCB 设计**：主机 A 使用 RFS 远程浏览主机 B 的目录，选择 `.brd` 文件并取得主机 B 的绝对路径。
2. **启动并打开 PCB**：AirBridge 将该路径作为 `target_file` 传给主机 B；Allegro 启动后加载 SKILL 探针，并调用交互式 `axlOpenDesign()` 打开所选设计。
3. **关闭 Allegro**：主机 A 点击「关闭PCB（Allegro）」，AirBridge 通知主机 B 对本次由 AirBridge 启动并跟踪的 Allegro 进程发送正常关闭请求。

需要坚持以下原则：

- 主机 A 负责按钮操作和 AirBridge 通信，不直接操作主机 B 的 Cadence GUI。
- Allegro 实际运行在主机 B；PCB 设计文件由主机 B 本地打开。
- SKILL 探针用于确认 Allegro 已启动、SKILL 环境可执行，并输出版本及环境信息。
- **当前启动按钮先要求用户从主机 B 远程选择 `.brd` 文件，再下发启动任务。**
- 用户仍在主机 B 的 Allegro GUI 中编辑和保存 PCB；文件选择发生在主机 A。
- 启动探针完成不等于关闭 Allegro。通过 keep_open=True 保持 GUI 运行，等待独立关闭命令。
- 正常关闭使用 Windows WM_CLOSE；不把强制结束进程作为正常关闭方式。
- Allegro 和 Capture 的受管进程分别记录，关闭命令按应用类型区分。

## 2. 主机 A 的按钮与任务载荷

### 2.1 启动按钮

主机 A 界面上的按钮为：

- 打开PCB（Allegro）
- 关闭PCB（Allegro）

点击启动按钮后，调用链从 `trigger_builtin_allegro_test()` 开始。该函数先调用 `_browse_remote_eda_file("allegro", [".brd"])`，打开远程文件选择器。用户选择文件后才继续：

1. 将内置 SKILL 探针 `BUILTIN_TEST_ALLEGRO_SKILL` 放入脚本编辑区。
2. 将提示文件名设为 `builtin_test.il`。
3. 读取当前选择的 Cadence 版本。
4. 设置 `keep_open=True`，并将所选 BRD 绝对路径作为 `target_file`。
5. 调用 `_dispatch_script(...)` 下发脚本和路径。

目录浏览复用 RFS 的 `drives` 与 `ls` 请求，不下载 PCB 文件。

当前内置 SKILL 探针主要输出：

- AirBridge SKILL 探针启动提示；
- Allegro 系统时间；
- Allegro 工具版本（axlVersion('version)）；
- 当前 CDSROOT；
- SKILL 检查通过信息。

探针自身只负责环境检查；`_run_allegro_skill()` 会在探针之后追加由 `target_file` 生成的交互式开板调用。

### 2.2 下发的任务载荷

_dispatch_script() 将任务序列化为 JSON，并通过 FrameType.EDA_TASK_SUBMIT 发送。与本流程相关的字段如下：

~~~json
{
  "script": "<内置 Allegro SKILL 探针>",
  "hint": "builtin_test.il",
  "version": "<当前选择的 Cadence 版本>",
  "keep_open": true,
  "target_file": "<主机 B 远程选择器返回的绝对 BRD 路径>"
}
~~~

说明：

- `keep_open=true` 是启动后保持 Allegro GUI 的关键参数。
- 当前 Allegro 启动按钮将选择器返回的 BRD 绝对路径写入 `target_file`。
- `execute_script()` 将 `target_file` 传入 `_run_allegro_skill()`；执行器会在主机 B 检查 `.brd` 扩展名和文件存在性，再构造交互式开板 SKILL 调用。

## 3. 主机 B：接收任务并启动 Allegro

### 3.1 接收任务

主机 B 的 SerialWorker._handle_incoming_frame() 收到 FrameType.EDA_TASK_SUBMIT 后：

1. 解码 JSON 任务载荷；
2. 取得 SKILL 脚本、提示文件名、版本、超时参数和 keep_open；
3. 在后台线程调用 _exec_eda_async()；
4. _exec_eda_async() 再调用 EDAExecutor.execute_script()。

主要调用链：

~~~text
主机 A：trigger_builtin_allegro_test()
    └─ RemoteFileDialog / RFS 选择 BRD
       └─ _dispatch_script(..., target_file=brd_path, keep_open=True)
       └─ EDA_TASK_SUBMIT
          ↓ AirBridge 通信链路
主机 B：SerialWorker._handle_incoming_frame()
    └─ _exec_eda_async(...)
       └─ EDAExecutor.execute_script(...)
          └─ _run_allegro_skill(..., keep_open=True)
~~~

### 3.2 脚本类型识别与版本定位

EDAExecutor.execute_script() 根据脚本内容和文件提示名识别脚本类型。当前内置脚本为 Allegro SKILL，因此分派到 _run_allegro_skill()。

执行器根据主机 B 上检测到的 Cadence 安装信息定位对应版本的 allegro.exe，并构建隔离的 Cadence 运行环境。

如果同一 AirBridge 实例已经启动了仍在运行的受管 Allegro，且再次收到 keep_open=True 的启动请求，执行器会检测到已有进程并跳过重复启动，避免通过启动按钮意外创建第二个受管实例。

### 3.3 生成临时 SKILL 和 SCR 文件

_run_allegro_skill() 在执行工作目录生成或更新以下文件：

| 文件 | 用途 |
|---|---|
| _polyfill.il | AirBridge 的 Allegro 跨版本 SKILL 辅助函数库 |
| _task.il | 本次需要执行的 SKILL 探针及任务完成标记 |
| _run.scr | Allegro 启动后执行的命令脚本 |
| _allegro_done.flag | 表示本次 SKILL 脚本已执行到完成标记 |
| allegro.log | 本次运行监控的 Allegro 日志文件 |

生成的 _run.scr 包含以下命令：

~~~text
set window message
skill load("_polyfill.il")
skill load("_task.il")
~~~

路径会按 SKILL/SCR 所需格式转换为正斜杠形式。

### 3.4 启动命令

当前 Allegro 启动命令由以下参数列表构成：

~~~python
cmd = [allegro_exe, "-s", scr_file]
~~~

等价的命令行形式为：

~~~powershell
allegro.exe -s "_run.scr"
~~~

其中没有附加 .brd 设计文件路径。Allegro 启动后通过 -s 执行 SCR，SCR 再加载 Polyfill 和本次 SKILL 探针。

## 4. 交互式自动打开 PCB

`eda_engine.py` 保留原有 `abOpenDesign()` 辅助函数（它优先尝试 Batch API），同时新增专门用于交互式 GUI 开板的 `abOpenDesignInteractive()`：

~~~skill
procedure(abOpenDesignInteractive(brdPath)
    let((openResult)
        openResult = nil
        if(isCallable('axlOpenDesign) then
            openResult = errset(
                axlOpenDesign(?design brdPath ?mode "wf")
                t
            )
        )
        if(openResult then
            car(openResult)
        else
            nil
        )
    )
)
~~~

但必须区分“定义函数”和“调用函数”：

- `_polyfill.il` 加载时会定义 `abOpenDesignInteractive()`，但该函数只在本次任务带有 `target_file` 时才会被追加调用。
- `abOpenDesignInteractive()` 调用 `axlOpenDesign(?design brdPath ?mode "wf")`，不调用 `axlOpenDesignForBatch()`，目标是打开当前交互式 Allegro GUI 设计。
- `_run_allegro_skill()` 会先验证目标文件扩展名为 `.brd` 且在主机 B 上真实存在，然后把路径转换为 SKILL 字符串并追加开板代码。
- 开板代码运行后在 Allegro 命令窗口输出 API 返回成功或失败的信息；任务完成标记仍在后面写入。

Cadence 社区对通过 SKILL 打开设计的讨论指向 `axlOpenDesign()`，同时也提醒 `axlShell()` 这类交互命令可能中断正在执行的 SKILL 函数。因此当前实现优先使用 `axlOpenDesign()`，不采用 `axlShell("open ...")` 或 Batch API 作为交互式 GUI 开板的替代。参考：[Cadence Community：Allegro shell command](https://community.cadence.com/cadence_technology_forums/pcb-design/f/allegro-x-scripting-skill/47967/allegro-shell-command)。

**注意：`axlOpenDesign()` 在目标机实际使用的 SPB 17.2 上尚待实机验收。代码已接通，但本次提交不能等同于 Allegro 自动开板 PASS。**

## 5. SKILL 执行完成后保持 Allegro 运行

_run_allegro_skill() 会在 _task.il 末尾追加完成标记代码。探针运行到末尾后创建 _allegro_done.flag。

_spawn_and_monitor() 启动进程时：

- 使用 subprocess.Popen() 启动 Allegro；
- 标准输出重定向到 DEVNULL，避免 GUI 长时间运行时因未读取的 PIPE 而阻塞；
- 因为 keep_open=True，将进程句柄登记到：

~~~python
managed_processes["allegro"] = proc
~~~

监控线程检测到完成标记后：

- 报告 SKILL 探针执行完成；
- 不对 Allegro 发送 WM_CLOSE；
- 不退出 Allegro GUI；
- 保留进程句柄，等待独立的关闭命令。

此时所选 PCB 已由追加的交互式 SKILL 调用尝试打开；用户在主机 B 的 Allegro 窗口中检查是否加载成功，然后正常编辑和保存。若 API 返回失败，应查看 Allegro 命令窗口日志，并按验收结果修正开板方法。

## 6. 从主机 A 远程选择 PCB 设计

1. 主机 A 点击「打开PCB（Allegro）」；
2. 在远程文件选择器中扫描盘符、逐级浏览主机 B 的目录；
3. 选择 `.brd` 文件并点击「确定」；
4. AirBridge 将主机 B 的完整路径传到主机 B；
5. 主机 B 启动 Allegro，加载 SKILL 探针并调用 `abOpenDesignInteractive()`；
6. 用户在主机 B 检查 PCB 是否已在交互式 Allegro 窗口中打开，然后进行编辑和保存。

RFS 只传输目录项元数据和文件路径，不传输 BRD 文件内容。

## 7. 关闭 Allegro：完整执行流程

### 7.1 主机 A 发送独立关闭请求

用户在主机 A 点击「关闭PCB（Allegro）」后，调用：

~~~python
trigger_close_allegro()
    └─ _dispatch_close_eda_application("allegro")
~~~

主机 A 发送：

- 帧类型：FrameType.EDA_APP_CLOSE，数值为 0x14；
- JSON 载荷：

~~~json
{
  "application": "allegro"
}
~~~

关闭操作是独立协议命令，不是再次提交 SKILL 脚本，也不依赖启动探针监控线程继续运行。

### 7.2 主机 B 接收关闭请求

主机 B 的 SerialWorker._handle_incoming_frame() 识别 EDA_APP_CLOSE 后：

1. 解析 application="allegro"；
2. 创建后台线程；
3. 调用 _close_eda_application_async("allegro")；
4. 后者调用 self.eda_executor.close_application("allegro", stream_log)；
5. 关闭过程日志通过 EDA_LOG_STREAM 返回主机 A。

调用链：

~~~text
主机 A：trigger_close_allegro()
    └─ _dispatch_close_eda_application("allegro")
       └─ EDA_APP_CLOSE (0x14)
          ↓ AirBridge 通信链路
主机 B：SerialWorker._handle_incoming_frame()
    └─ _close_eda_application_async("allegro")
       └─ EDAExecutor.close_application("allegro")
          └─ request_graceful_close(..., process=proc)
             └─ Windows WM_CLOSE
~~~

### 7.3 只关闭 AirBridge 跟踪的 Allegro

close_application("allegro") 首先查询：

~~~python
managed_processes["allegro"]
~~~

只有当前主机 B 的 AirBridge 进程启动并成功登记的 Allegro 实例，才属于该关闭按钮的管理范围。

如果找不到受管实例，AirBridge 会报告没有由 AirBridge 跟踪的 Allegro，不会因此扫描并关闭用户手动启动的其他 Allegro 进程。

### 7.4 发送正常关闭消息

执行器调用 request_graceful_close()，定位属于受管进程 PID 的可见顶层窗口，并发送：

~~~text
WM_CLOSE = 0x0010
~~~

关闭实现遵循以下规则：

- 不把 taskkill /F 用作正常关闭路径；
- 不直接向窗口类名为 #32770 的模态对话框发送关闭消息；
- 等待 Allegro 自己处理正常关闭流程；
- 最长等待时间默认为 15 秒。

进程在等待时间内退出后，AirBridge 清除 managed_processes["allegro"] 记录，并报告正常退出。

如果 Allegro 因保存确认、授权、迁移或其他对话框而没有退出，AirBridge 不强制结束进程，会保留进程记录并报告需要在主机 B 人工处理。用户应在 Allegro 中完成必要的保存或确认，再处理退出。

### 7.5 弹窗监视器的行为

启动 Allegro 时会启动 DialogKillerWatchdog。当前实现对检测到的对话框采取**只观察、只记录日志**的策略：

- 检测对话框窗口类名或若干已知弹窗标题；
- 记录提示信息；
- 不自动按 Enter；
- 不自动点击确定；
- 不自动关闭未知弹窗。

因此，保存确认、许可证或数据库迁移等需要用户判断的提示仍由用户处理。

## 8. 可重复的启动、开图和关闭循环

标准操作顺序如下：

~~~text
主机 A 点击「打开PCB（Allegro）」
        ↓
RemoteFileDialog 通过 RFS 浏览主机 B
        ↓
选择 .brd，返回主机 B 绝对路径
        ↓
EDA_TASK_SUBMIT 将 target_file 传给主机 B
        ↓
主机 B 启动 Allegro，执行 _run.scr
        ↓
加载 Polyfill / 探针并调用 axlOpenDesign() 打开 BRD
        ↓
Allegro GUI 保持运行
        ↓
用户编辑 / 保存 PCB
        ↓
主机 A 点击「关闭PCB（Allegro）」
        ↓
主机 B 对受管 Allegro 窗口发送 WM_CLOSE
        ↓
Allegro 正常退出，清除受管进程记录
        ↓
再次点击「打开PCB（Allegro）」即可开始下一轮
~~~

补充行为：

- Allegro 仍处于运行状态时再次点击启动按钮，执行器会检测到已有受管进程并跳过重复启动。
- Allegro 正常退出并清除进程记录后，可以重新启动。
- 用户此前已实机确认“SKILL 启动 Allegro → 手动打开 PCB → 关闭 Allegro”可以多次循环正常运行。新的远程选择与交互式自动开板链路尚未完成实机验收。

## 9. 关键实现位置

| 文件 | 函数 / 常量 | 职责 |
|---|---|---|
| airbridge_app.py | RemoteFileDialog | 通过 RFS 浏览主机 B，过滤并选择 `.brd` 文件 |
| airbridge_app.py | _browse_remote_eda_file() | 返回主机 B 选定文件的绝对路径 |
| airbridge_app.py | BUILTIN_TEST_ALLEGRO_SKILL | 内置 SKILL 启动探针，只输出环境信息 |
| airbridge_app.py | trigger_builtin_allegro_test() | 设置脚本、版本并以 keep_open=True 下发 |
| airbridge_app.py | trigger_close_allegro() | 触发 Allegro 独立关闭请求 |
| airbridge_app.py | _dispatch_script() | 序列化并发送 EDA 启动任务 |
| airbridge_app.py | _dispatch_close_eda_application() | 发送 EDA_APP_CLOSE JSON 载荷 |
| airbridge_app.py | SerialWorker._handle_incoming_frame() | 主机 B 接收 EDA 启动和关闭帧 |
| airbridge_app.py | _exec_eda_async() | 将启动任务交给 EDA 执行器 |
| airbridge_app.py | _close_eda_application_async() | 后台执行 Allegro 正常关闭 |
| protocol.py | EDA_TASK_SUBMIT | 提交 SKILL 启动任务 |
| protocol.py | EDA_APP_CLOSE = 0x14 | 独立关闭受管 Cadence GUI |
| eda_engine.py | ALLEGRO_SKILL_POLYFILL | 定义跨版本辅助函数及交互式 abOpenDesignInteractive() |
| eda_engine.py | EDAExecutor.execute_script() | 识别 SKILL 并分派 Allegro 执行 |
| eda_engine.py | _run_allegro_skill() | 校验 BRD 路径，生成开板调用、任务 SKILL、SCR 并启动 Allegro |
| eda_engine.py | _spawn_and_monitor() | 监控探针完成标记并在保持打开模式下登记进程 |
| eda_engine.py | DialogKillerWatchdog | 检测并记录弹窗，不自动处理 |
| eda_engine.py | close_application() | 关闭指定类型的受管 Cadence 进程 |
| eda_engine.py | request_graceful_close() | 向受管 GUI 窗口发送 WM_CLOSE |

## 10. 当前实现边界与注意事项

1. **PCB 文件需要手动打开**：当前启动按钮没有传入 .brd 路径，用户需在主机 B 的 Allegro 界面中手动打开设计。
2. **交互式开板 API 待实机验收**：当前使用 `axlOpenDesign(?design ... ?mode "wf")`。尤其需要在主机 B 实际使用的 SPB 17.2 上确认打开的是可见 GUI 设计，而非仅有 SKILL 返回值；如失败，应检查 Allegro 命令窗口日志并调整 API 调用。
3. **没有自动保存功能**：关闭按钮只请求正常退出，不替用户选择保存或丢弃设计修改。
4. **关闭范围有限**：关闭按钮只处理当前 AirBridge 实例跟踪的 Allegro 进程，不关闭用户手动启动的其他实例。
5. **进程记录保存在内存中**：managed_processes 属于主机 B 当前 AirBridge 进程的内存状态。若 Allegro 尚未退出就重启主机 B 的 AirBridge，新实例不会自动继承旧进程句柄；此时不要假设关闭按钮仍能管理之前的实例。
6. **正常关闭超时不强杀**：若 15 秒内没有退出，先检查主机 B 的 Allegro 窗口和弹窗，由用户处理后再确认退出状态。
7. **不要把 Capture 的开图机制套用到 Allegro**：Capture 当前使用 capture.exe 原生命令行参数传入 DSN；Allegro 当前使用 allegro.exe -s _run.scr 加载 SKILL。两者启动和开图路径不同。

---

**当前标准：主机 A 通过 RFS 选择主机 B 的 BRD 文件；AirBridge 将绝对路径传给主机 B，Allegro 启动后调用交互式 `axlOpenDesign()` 尝试开板。Allegro 编辑与保存仍由用户操作，关闭仍由独立 AirBridge 命令完成。自动开板须经主机 B 实机验收后才能标记 PASS。**
