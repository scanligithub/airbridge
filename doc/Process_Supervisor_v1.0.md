# AirBridge Process Supervisor 设计与验收 v1.0

- 文档编号：AB-PROCESS-SUPERVISOR-001
- 版本：1.0
- 日期：2026-11-12
- 对应阶段：P1.7
- 状态：**实现与 GitHub Actions 软件 CI PASS；2026-11-12 Capture 17.2 Start Page PID 6648 的 WM_CLOSE 与实际进程退出已在主机 B 实机 PASS；保存提示、多手动实例、无主窗口和无响应场景仍待实机验收**

## 1. 目标与安全原则

Process Supervisor 是 AirBridge 的通用进程生命周期模块。它处理“启动某个 Runtime 之前，旧的同类专业软件进程是否已全部退出”的问题。它不负责解释 Capture / Allegro 工程内容，不负责点击保存按钮，不决定是否丢弃未保存数据。

必须遵守：
1. 枚举所有匹配的进程，不区分由 AirBridge 启动还是用户手动启动；
2. 对可安全请求关闭的可见窗口只发送 Windows `WM_CLOSE`；
3. 不自动操作保存、许可证、迁移、确认或其他模态对话框；
4. 关闭请求后必须等待并确认进程句柄已退出；
5. 如果仍存在进程、出现新进程、无法识别可安全关闭的主窗口或遇到阻塞提示，拒绝启动新实例；
6. 不使用 `TerminateProcess`、`taskkill /F`、`Popen.kill()` 或等效强制结束方式；
7. 对 PID 复用风险采取额外校验：发送 WM_CLOSE 前重新查询映像名称和可执行文件路径。

## 2. 架构边界

~~~text
CaptureTclAdapter / RuntimeProfile
              |
              v
      ProcessSupervisor
              |
       ProcessBackend
              |
     WindowsProcessBackend
       |       |        |
    tasklist  EnumWindows  Process HANDLE
       |       |        |
     PID/path  Window info   WaitForSingleObject
~~~

- ProcessSupervisor：通用策略、状态机、超时、报告、启动阻断；
- ProcessBackend：可替换操作系统接口，便于测试；
- WindowsProcessBackend：Win32 进程发现、窗口枚举、WM_CLOSE 和退出等待；
- ProcessSpec：以 app_id 映射进程映像名及显示名；
- ProcessSupervisorReport：保留已发现进程、每 PID 处理状态和最终仍存活进程。

默认 ProcessSpec：
- `capture` → `Capture.exe`
- `allegro` → `allegro.exe`

当前 P1.7 已将 Capture 启动路径接入 Supervisor。Allegro ProcessSpec 已具备通用配置，但旧 Allegro SKILL 执行路径尚未迁移到专用 Allegro Runtime Adapter；这部分仍留给 P1.9。

## 3. 启动前检查与关闭状态机

### 3.1 发现进程

Windows 后端对每个 image name 调用 `tasklist /FI /FO CSV`，收集所有 PID；随后尝试使用 `QueryFullProcessImageNameW` 获取可执行文件路径。任何进程枚举失败都会 fail closed：抛出 ProcessSupervisorError，不继续启动。

进程路径不可读取时仍然报告 PID，但后续不会对其发送 WM_CLOSE，因为关闭前无法确认目标映像身份。

### 3.2 窗口检查

对每个 PID 调用 EnumWindows，查询窗口标题、窗口类和可见状态。

以下情况视为阻塞：
- 可见窗口类为 `#32770`；
- 可见窗口标题匹配 Save、Confirm、Choices、License、Missing Library、Migration、Overwrite 等安全提示关键词；
- 找不到任何可见、非对话框窗口，无法确认可安全关闭的主窗口。

对话框不会收到 Enter、IDOK、WM_CLOSE 或其他自动交互。该策略有意保守：遇到保存提示时宁可暂停任务，也不能替用户决定保存或丢弃工程。

### 3.3 关闭请求与 PID 身份验证

对每个可安全关闭的进程：
1. 重新查询当前 PID 的 executable path；
2. 验证映像文件名仍匹配 ProcessSpec；
3. 如果初次发现时已有路径，再比较规范化后的完整路径；
4. 只有身份仍匹配才对可见非对话框顶层窗口发送 WM_CLOSE。

这减少了 PID 在枚举后退出并被系统复用、导致 AirBridge 对错误进程发消息的风险。任何校验失败都返回 close_request_failed 并阻止启动。

### 3.4 等待真实退出

对关闭请求使用进程同步句柄和 WaitForSingleObject 等待。多个进程共享同一总超时窗口；所有关闭请求先发出，再等待各自退出。结束后重新枚举进程，确保：
- 初始扫描的进程都已经退出；
- 等待期间没有新同名进程出现；
- 对话框或无响应进程仍存在时，状态为 blocked。

最终仍有任何匹配进程时抛出 ProcessSupervisorError，携带完整 report。上层 Capture Adapter 记录每个 PID 的状态后中止启动。

## 4. 与 Capture Session 的集成

主机 B 的 Capture Tcl 执行路径：

1. 如果当前 Worker 已拥有一个运行中的 Capture Bridge Session，复用该 Session 和 PID，不重新执行 Supervisor，也不启动新 GUI；
2. 如果没有可复用 Session，调用 `ensure_exclusive("capture")`；
3. 所有旧 Capture.exe（包括手动启动的实例）必须退出，Supervisor 才返回；
4. 之后才查找 Cadence 安装、capAutoLoad、建立 session_dir / nonce、安装 hook 和 Popen 启动；
5. Bridge READY 仍需再次验证 session_id、startup_nonce、runtime_pid 和 Tcl 版本。

P1.7 不会关闭一个已经由同一 Worker 管理且正在服务的 Capture Session。进程意外退出后，再次启动请求会先重新扫描所有 Capture.exe，并按同一安全策略处理。

## 5. 状态和错误

ProcessCloseOutcome.status：
- `blocked_by_dialog`：发现可见模态/保存/确认对话框，没有向该进程发送窗口消息；
- `blocked_no_main_window`：没有可见非对话框窗口；
- `close_request_failed`：进程映像身份校验失败或 WM_CLOSE 发送失败；
- `close_requested`：WM_CLOSE 已提交，正在等待；
- `timeout`：进程仍存活但未发现可识别模态对话框；
- `exited`：进程已实际退出。

ProcessSupervisorReport.state：
- `ready`：初次扫描没有匹配进程，可以启动；
- `closing_existing`：发现旧进程，正在优雅关闭；
- `closed_existing`：旧进程均已退出，可以启动；
- `blocked`：仍有进程或安全检查不通过，禁止启动。

Supervisor 本身不发送 ABFP 帧，不创建 Session，不保存结果；它只返回结构化报告或抛出带报告的错误。执行策略由 Runtime Adapter 调用，Broker 继续只负责 Request 状态与结果持久化。

## 6. P1.7 软件测试与 CI

GitHub Actions [Run #85](https://github.com/scanligithub/airbridge/actions/runs/43149369815)，提交 `42ba2e31c3902ae39abe249187360147b0d15151`：
- Protocol 10 项；
- RequestTracker 11 项；
- ProcessSupervisor 9 项；
- Capture Tcl Bridge 12 项；
- Runtime Adapter 11 项；
- RequestBroker 15 项；
- 单元层合计 68 项；
- A/B 生产 SerialWorker 软件模拟 12 项；
- HTTP correlated request 集成 9 项；
- 合计 **89 项通过、0 项失败，两个 Job 均成功**。

9 项 ProcessSupervisor 测试覆盖：
- 无旧进程时可启动；
- 多个 Capture 实例（包含手动启动）逐个 WM_CLOSE 并等待退出；
- 模态保存提示阻断且不发送窗口消息；
- WM_CLOSE 后进程仍存活时阻断；
- 无可见主窗口时 fail closed；
- 关闭期间出现新 Capture 实例时阻断；
- Capture / Allegro 匹配隔离；
- 错误 Profile / 超时校验；
- 在 GitHub Windows Runner 上真实发现当前 Python 进程、验证映像路径并枚举其窗口。

**重要边界：** CI 的多 Capture 关闭、保存对话框、超时与新进程竞态场景使用 FakeProcessBackend；Windows-only 集成测试实际验证 tasklist / executable path / EnumWindows 接口，但没有向真实 Capture.exe 发送 WM_CLOSE，也没有实际关闭 Cadence GUI。因此 P1.7 的软件 CI PASS 不等于真实工程保存提示 / Capture 退出流程已经实机验收。

## 7. 主机 B 实机验收清单

在真实 Windows + Capture 17.2 环境上验证：
- [ ] 手动启动 1 个 Capture 实例，执行一次 Tcl Session 启动请求，确认先收到 WM_CLOSE、PID 完全退出后才启动新 Capture；
- [ ] 手动启动 2 个 Capture 实例，确认两个 PID 都被枚举、都退出后才创建新实例；
- [ ] 打开未保存工程并制造保存提示，确认 Supervisor 不点击保存/不保存/取消，只报告 blocked 并拒绝启动新实例；
- [ ] 手工处理提示后重新提交请求，确认重新扫描并成功启动；
- [ ] 模拟 Capture 无响应，确认超时后仍保持进程、没有任何强制结束命令，新 Session 启动被拒绝；
- [ ] 在初次扫描与最后复查之间新启动 Capture，确认新 PID 被发现并阻断；
- [ ] 验证 Worker 自己拥有的健康 Capture Session 被复用，不会被 Supervisor 关闭；
- [ ] 验证 Capture 崩溃后再次请求会重新执行进程扫描和独占检查。

上述实机项目完成前，不得把 Cadence GUI 生命周期验收标记为 PASS。
