# Capture Session Host-A E2E 验收工具 v1.0

- 文档编号：AB-CAPTURE-E2E-001
- 版本：1.0
- 日期：2026-11-12
- 对应阶段：P1.8 验收准备
- 状态：**Host-A E2E harness 已实现并通过 GitHub Actions Run #91 的软件 CI；真实 Host A / Host B Capture E2E 待执行**

## 1. 目的

scripts/capture_session_e2e.py 从主机 A 调用本机 AirBridge HTTP API，连续提交两条独立 Tcl 请求，验证主机 B 上的 Capture Tcl Session 是否真正复用同一个 Capture PID / Tcl Interpreter。

核心判据：
- 两次请求返回相同的 Capture PID；
- 第一条请求创建的 Tcl 全局变量和自定义 proc 在第二条请求中仍存在；
- 第二条请求不重新定义变量 / proc，而是直接读取它们；
- GetActivePMDesign 命令存在且实际调用成功；
- GetActivePage 和 OPage 命令存在；
- 两个 Request ID 独立、都以 completed 成功结束。

该测试只探测当前设计会话，不执行放置、改网表、保存、关闭或 Exit 操作。结束后 Capture 保持打开，供用户人工检查。

## 2. 前置条件

主机 A：
- AirBridge Client / SerialWorker 正在运行；
- 本机北向 HTTP Server 监听 127.0.0.1:8888；
- A/B 串口链路已连接；
- A/B 已协商 request.correlation 与 request.deduplication。

主机 B：
- 已安装预期版本的 Cadence OrCAD Capture（默认 17.2）；
- Capture 对应的 capAutoLoad 目录可写，且 Worker 能发现它；
- 用户允许 AirBridge 使用 ProcessSupervisor 处理旧 Capture 进程；
- 目标 DSN 文件路径是主机 B 上的绝对路径，不能填主机 A 本地路径。

开始前请确认旧 Capture 没有未保存的重要工程。P1.7 Supervisor 对可安全关闭的可见主窗口只发送 WM_CLOSE；如果发现保存提示、确认对话框、无可见主窗口或进程超时，会阻断启动，不自动点击保存 / 不保存 / 取消，也不会强杀进程。

## 3. 运行方式

在主机 A 的 AirBridge 仓库根目录执行：

~~~powershell
python scripts/capture_session_e2e.py "D:\\RK3588\\project\\project.dsn"
~~~

可选参数：

~~~powershell
python scripts/capture_session_e2e.py "D:\\RK3588\\project\\project.dsn" --base-url http://127.0.0.1:8888 --version 17.2 --timeout 60
~~~

--timeout 是每条 Tcl 请求的执行超时，不是整个 Capture 启动时限。首次请求另有最多约 120 秒的启动宽限，用于旧进程优雅关闭和新 Bridge READY。

## 4. 测试步骤

1. GET Runtime capabilities；如未协商则发起 negotiate，并等待必需能力。
2. 生成随机 token。
3. 提交第一条 EDA Tcl 请求：
   - 在 ::AirBridgeE2E namespace 定义全局 token；
   - 定义 ::AirBridgeE2E::probe proc；
   - 读取 Capture PID / Tcl 版本；
   - 检测 GetActivePMDesign、GetActivePage、OPage 命令；
   - 只读调用 GetActivePMDesign 并把报告作为 Tcl 结果回传。
4. 提交第二条独立 EDA Tcl 请求：
   - 不重新定义 token 或 proc；
   - 检查两者依然存在；
   - 再次调用 GetActivePMDesign；
   - 返回相同 PID 与 Tcl 版本。
5. 对比两条 Request 的 Capture PID、token、proc、命令可用性和结构化结果。

## 5. 结果解释

成功时输出 Capture Session E2E PASS 及 JSON 报告，至少包括：
- capture_pid
- tcl_version
- persistent_variable=true
- persistent_proc=true
- same_capture_pid=true
- capture_api_commands
- get_active_pm_design_call_succeeded=true
- design_saved_or_closed=false

失败时工具返回非零退出码，并输出阻塞点：
- Runtime capabilities 未协商；
- Session / Capture 启动或 READY 失败；
- Request failed / UNKNOWN / timeout；
- Capture PID 改变；
- Tcl 变量或 proc 未跨请求保留；
- Dbo 命令不存在或 GetActivePMDesign 调用失败。

UNKNOWN 代表请求结果无法确认，不能按“脚本没执行”处理，也不应使用同一个 request_id 自动重发。请先检查主机 B Capture 窗口和 Broker / Worker 状态。

## 6. 安全范围

此工具不会：
- 自动保存工程；
- 自动关闭 Capture；
- 自动调用 Tcl Exit；
- 修改原理图对象、页面或网表；
- 通过 Shell 执行任意命令。

它会在 Capture Interpreter 中创建临时的 ::AirBridgeE2E namespace、变量和 proc，并调用只读的活动设计探测命令。Capture 结束后这些内存状态随进程消失。

## 7. 验收边界

本工具的单元测试由 GitHub Actions 覆盖，证明 HTTP 请求包装、报告解析、PID 一致性判定和错误路径可用。只有在真实主机 A / B 运行成功后，才可将 P1.8 Capture Session E2E 标记为实机 PASS。

Run #92 通过后，测试 harness 本身的 CI PASS 仍不等同真实 Cadence Capture PASS。真实验收结果需记录：日期、主机 A / B 软件版本、Capture 版本、Tcl 版本、Capture PID、两条 request_id、Supervisor 对旧进程的处理结果及最终 JSON 报告。

## 8. Harness 软件 CI 记录

GitHub Actions [Run #92](https://github.com/scanligithub/airbridge/actions/runs/43151269632)，提交 `063d79297907a72377ef9aee15727e0924a7ab39`：
- Host-A Capture Session E2E harness 单元测试：8 项通过；
- Protocol / RequestTracker / ProcessSupervisor / Capture Bridge / Runtime Adapter / RequestBroker 单元测试：共 77 项；
- A/B 生产 SerialWorker 模拟：12 项；
- HTTP correlated request 集成：9 项；
- **合计 98 项通过、0 项失败，两个 Job 均成功。**

这些测试使用 Mock HTTP / Mock E2E 结果，不访问主机 B、不启动真实 Capture。P1.8 实机验收仍需在主机 A 执行本工具，并把输出 JSON 与实际 Capture PID / Tcl 版本记录到项目验收记录。
