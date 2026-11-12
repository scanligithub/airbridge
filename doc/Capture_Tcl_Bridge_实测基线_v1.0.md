# Capture Tcl Bridge 实测基线与接入规范

- 文档编号：AB-ADAPTER-CAPTURE-001
- 版本：1.0
- 状态：**原型 Capture 17.2 实测证据保留；P1.6 Capture Tcl Adapter / Bridge 软件 CI PASS；新正式 Bridge 尚待真实 Capture 17.2 实机复验**
- 日期：2026-11-12
- 实测环境：Windows / OrCAD Capture SPB 17.2-2016 / Tcl 8.6.0
- 关联文档：
  - AirBridge 通用 Runtime Session 架构设计 v1.0
  - AirBridge Runtime Session Protocol v1

> 本文前半记录用户在真实 Capture 17.2 环境中的原型实测证据；第 8 节记录 P1.6 正式 Python Adapter / 生成式 Tcl Bridge 的软件实现与 GitHub Actions 验收。云端 CI 使用确定性文件队列模拟器，不会启动真实 Cadence Capture，因此不能替代新版 Bridge 在 Capture 17.2 / Tcl 8.6.0 上的实机复验。

---

## 1. 实测结论

目前已经验证以下能力：

1. Capture 启动时会自动加载 capAutoLoad 目录下的 Tcl 文件。
2. 自动加载的 Tcl Bridge 可以在 Capture 事件循环中使用 after 定时轮询本地任务队列。
3. 同一个 Capture Tcl Interpreter 可以连续执行多条独立请求。
4. 不同请求之间，全局变量、namespace 和自定义 Tcl proc 均保持存在。
5. Bridge 可以执行 Capture Tcl 命令、Project Manager 操作和 Dbo API。
6. 每条请求可以返回独立的成功/失败状态、结果、错误栈和 Capture PID。
7. 主机 A 可通过现有 AirBridge Remote Shell / 串口路径间接投递请求，并取得 Capture 的执行结果。

这证明了文件队列式 Capture Tcl Bridge 作为 Runtime Adapter 原型的可行性。

## 2. 环境和已验证文件位置

测试环境：

~~~text
Capture.exe:
D:\Cadence\SPB_17.2\tools\bin\Capture.exe

capAutoLoad:
D:\Cadence\SPB_17.2\tools\capture\tclscripts\capAutoLoad

测试工作区:
E:\_airbridge_temp\airbridge_tcl_bridge_test
~~~

Capture Tcl 环境报告：

~~~text
TCL_VERSION=8.6.0
~~~

以上为实测机器环境路径。正式安装程序应通过 Runtime Profile 定位安装目录和 capAutoLoad，不应将这些绝对路径硬编码到 AirBridge 核心。

## 3. Capture 17.2 已实测 Tcl 能力

### 3.1 OPJ 工程打开

在 Capture Command Window 和自动加载 Bridge 请求中均实测：

~~~tcl
set opj {E:/_airbridge_temp/RK3588_PDF_Output/rk3588_wx8116-sch-v2t.opj}
Open $opj
~~~

自动 Bridge 请求 008 返回：

~~~text
status OK
result {
  OPJ_OPENED ...
  PM_DESIGN <DboDesign pointer>
  SESSION_DESIGN <same DboDesign pointer>
  PID 7476
}
capture_pid 7476
tcl_version 8.6.0
~~~

**结论：** Capture 17.2 的 Tcl Open 命令可以打开指定 OPJ；随后 GetActivePMDesign 和 Dbo Session 的 GetActiveDesign 返回同一个设计对象。

### 3.2 Dbo Session

已经实测：

~~~tcl
set s $::DboSession_s_pDboSession
DboSession -this $s
set d [$s GetActiveDesign]
set pm [GetActivePMDesign]
~~~

工程打开后，Dbo Session 与 Project Manager 返回同一活动设计对象。

### 3.3 页面打开与页面名称读取

已实测 OPage 参数顺序：

~~~tcl
OPage "SCHEMATIC1" "P01-BLOCK"
~~~

第一个参数是原理图名称，第二个参数是页面名称。

随后可读取活动页面：

~~~tcl
set p [GetActivePage]
set c [DboTclHelper_sMakeCString]
$p GetName $c
set pageName [DboTclHelper_sGetConstCharPtr $c]
~~~

页面所属原理图：

~~~tcl
set schematic [$p GetOwner]
set c2 [DboTclHelper_sMakeCString]
$schematic GetName $c2
set schematicName [DboTclHelper_sGetConstCharPtr $c2]
~~~

实测结果：

~~~text
ACTIVE_PAGE=P01-BLOCK
SCHEMATIC_NAME=SCHEMATIC1
~~~

### 3.4 页面枚举

通过 DboSchematic 的 NewPagesIter / NextPage 枚举了 SCHEMATIC1 下的 34 个页面。

测试中实际出现的页面包括 P01-BLOCK、P03-WX8128_DDR、P10-PWR、P15-RJ45 等。

枚举结果只代表被测工程中的 SCHEMATIC1，不应将 34 作为所有工程的固定页面数。

### 3.5 页面上下文要求

在未激活 SchematicView 的上下文中调用 GetActivePage，实测得到：

~~~text
RuntimeError Command Requires SchematicView
~~~

在先执行 OPage 激活页面后，GetActivePage 和 DboPage GetName 正常工作。

因此 Capture Skill 必须先确认/激活目标原理图页面，再调用依赖 SchematicView 的页面 API。不能把此类错误误判为 Session 或 Bridge 通信失败。

### 3.6 保存和退出

通过 Journaling 记录到 Project Manager 保存流程：

~~~tcl
SelectPMItem "Design Resources"
Menu "File::Save"
~~~

实测返回：

~~~text
SAVE_RC=0
SAVE_RESULT=1
~~~

OPJ 文件时间戳更新；本轮没有原理图修改，DSN 时间戳未变化，因此不能把该次测试描述为“修改后的 DSN 内容已写盘”。

Capture 17.2 的 Tcl 命令 Exit 已实测能正常退出 Capture。正式 Adapter 仍需处理退出回执：Exit 会终止 Tcl Interpreter，最终 CLOSED 应由 B 端进程监督确认，而不是依赖 Exit 之后的 Tcl 代码写结果。

## 4. Tcl Bridge 原型测试记录

### 4.1 手动安装原型 V0

先在 Command Window 安装临时 Tcl 接收器，使用 200 ms after 轮询 requests 目录，并把结果写入 results 目录。

| 请求 | 内容 | 结果 |
|---|---|---|
| 001 | 普通 Tcl 表达式 20 + 22 | OK，结果 42 |
| 002 | 未激活 SchematicView 时调用 GetActivePage | ERROR，正确报告 Command Requires SchematicView |
| 003 | 主动抛出 AB_EXPECTED_ERROR | ERROR，正确返回错误和调用栈 |
| 004 | OPage 切换到 P01-BLOCK 并读取名称 | OK |
| 005 | 读取 Dbo Session 与 Project Manager 设计 | OK，两个对象一致 |
| 006 | 在 namespace 中定义变量和 health proc | OK |
| 007 | 独立请求再次调用 health proc | OK，状态和 PID 保留 |

手动原型测试中的请求 001–007 使用同一个 Capture PID 10204。更早的独立测试曾使用其他 PID；不同轮次 PID 不同是因为 Capture 被重新启动。

### 4.2 capAutoLoad 自动加载探针

在 capAutoLoad 放入临时探针文件，正常启动 Capture，不输入 Command Window 命令。

自动生成的日志内容：

~~~text
AUTOLOAD_OK
PID=1240
TCL_VERSION=8.6.0
~~~

随后删除了该临时探针文件。

**结论：** 被测 Capture 17.2 启动时自动执行 capAutoLoad 目录中的 Tcl 文件。

### 4.3 自动加载任务 Bridge

在 capAutoLoad 中放入临时任务 Bridge，正常启动 Capture 后由 Bridge 自动写入：

~~~text
status READY
capture_pid 7476
tcl_version 8.6.0
~~~

PowerShell 查询到的 Capture.exe PID 同为 7476。

自动加载 Bridge 上的请求结果：

| 请求 | 内容 | 结果 |
|---|---|---|
| 008 | Open OPJ，比较 PM Design 和 Session Design | OK，对象相同 |
| 009 | OPage 打开 P01-BLOCK，读取页面及原理图 | OK |
| 010 | 在 namespace 中定义变量和 health proc | OK |
| 011 | 独立请求调用 010 定义的 proc | OK，变量和 proc 持续存在 |

请求 008–011 均由 Capture PID 7476 执行。

### 4.4 AirBridge A → B → Capture → B → A 闭环

通过主机 A 的现有 HTTP Remote Shell API 提交 PowerShell 请求。PowerShell 在主机 B 写入 request 012，等待 Capture Bridge 执行并读取 result 文件。

主机 A 收到的关键结果：

~~~text
success: true
exit_code: 0
duration_ms: 671

request_id 012
status OK
result {AIRBRIDGE_SERIAL_OK PID 7476 TCL 8.6.0}
capture_pid 7476
tcl_version 8.6.0
~~~

**结论：** 现有 AirBridge 串口链路可以间接驱动已运行的 Capture Bridge，并把结果带回主机 A。该测试通过 Remote Shell 和文件队列完成，尚不代表新的 SESSION_EXEC ABFP 协议或专用 Session HTTP API 已经实现。

## 5. 文件队列原型机制

原型目录：

~~~text
airbridge_tcl_bridge_test/
├── requests/
├── running/
├── results/
└── bridge.ready
~~~

原型行为：

1. B 端提交者先写临时文件，再原子重命名为 .req；
2. Capture Tcl Bridge 用 after 定期扫描 requests；
3. 将请求文件原子移动到 running 目录，作为认领；
4. 在 Capture Tcl Interpreter 中执行请求脚本；
5. 捕获 Tcl catch 返回码、结果、errorInfo 和 errorCode；
6. 先写临时结果文件，再原子重命名为 .result；
7. 删除已处理的 running 文件；
8. 每个轮询周期只执行一个任务。

这是一份测试原型，不能直接视作生产安全实现。正式版需增加 Session 独立目录、request_id 校验、payload hash、去重缓存、权限 ACL、大小限制、结果保留策略、启动 nonce 和故障恢复。

## 6. 对正式 Capture Adapter 的约束

1. Bridge 由 capAutoLoad 自动初始化，不要求用户手动开启 Communication Server。
2. Bridge 不能为每条请求启动或关闭 Capture。
3. 一个 Capture Session 内同一时间只执行一条 Tcl 请求。
4. Tcl 变量、namespace 和 proc 可跨请求保存；Skill 可在自己的命名空间建立专业辅助函数。
5. Bridge 框架命名空间应与 Skill 命名空间隔离，例如 ::ABBridge::* 与 ::CaptureSkill::*。
6. 请求结果必须包含 session_id、request_id、status、runtime_pid、result、errorInfo / errorCode 和时间信息。
7. Tcl after 轮询在 Capture 的事件循环内运行；长时间同步 Tcl / Dbo 操作会延迟轮询并可能阻塞 GUI。
8. A 端等待超时不代表 Tcl 已取消；第一版不承诺中断运行中的 Dbo 操作。
9. Skill 决定何时保存。保存成功后才请求关闭 Session。
10. Capture Exit 会结束 Tcl Interpreter。关闭流程应先记录 close intent，再安排 Exit，由 B 端 Process Supervisor 确认进程退出后返回 SESSION_CLOSED。

## 7. 已完成与待实机 / 后续阶段验收

- [x] Session 使用独立目录和随机 startup_nonce。
- [x] P1.7 已实现启动前扫描全部 Capture.exe（含手动启动实例）、检查可见窗口、校验 PID 对应映像身份、对可安全请求的窗口发送 WM_CLOSE，并等待进程句柄实际退出；残留进程或新实例出现时阻断启动；
- [ ] 在主机 B 的真实 Capture 17.2 环境复验 WM_CLOSE、保存提示、多个手动实例及无响应进程；GitHub CI 使用模拟进程后端，未关闭真实 Cadence GUI。
- [x] Bridge READY 与本次 session_id / startup_nonce 绑定，并校验 runtime_pid 与 Worker 启动的 PID。
- [x] B 端 RequestBroker 对相同 request_id 去重；相同 Payload 重放缓存结果，不同 Payload 返回冲突。
- [ ] 主机 A 使用专用 Session ABFP 消息提交 Request；当前 P1.6 仍复用 EDA_TASK_SUBMIT / EDA_TASK_FIN，不新增 SESSION_CREATE / SESSION_EXEC。
- [ ] A/B 实际物理串口断连、重连及两台 Windows 主机联调；当前 GitHub CI 仅用内存 ABFP 链路模拟。
- [x] 长任务等待超时返回 UNKNOWN，Capture 仍可能运行时不删除请求文件，不自动重试。
- [ ] 排队任务取消、运行中脚本真正取消、Broker / Worker 重启和 Capture 崩溃后的完整恢复策略。
- [ ] Skill 保存后 Exit，B 端确认实际 PID 退出并返回 SESSION_CLOSED；Supervisor 已提供可复用的进程退出等待，但 Session Close 协议与 Skill E2E 尚未实现。
- [x] Capture Tcl 脚本限制 1 MiB；结果保留默认 24 小时 / 最多 1000 条，并受 ABFP 单帧结果大小限制。
- [ ] 为 Session 目录和 capAutoLoad hook 配置专用 ACL；当前使用运行用户默认文件权限，尚未实现独立 ACL 策略。

---

**当前结论：** 用户此前已在真实 Capture 17.2 / Tcl 8.6.0 环境验证 capAutoLoad 自动初始化、after 轮询、同一 Tcl Interpreter 连续请求、跨请求变量/proc 保留、Dbo / 页面操作和原型结果回传。P1.6 / P1.7 先后通过 Run #76（80 项）和 Run #85（89 项）；后续完整回归 Run #88（提交 309a13f）进一步将生成式 bridge.tcl source 到 Windows Runner 的标准 Tcl 8.6 解释器中，实际执行了一条文件队列请求并校验结果文件和 stdout，整套 CI 共 90 项通过。**但新版生成 Bridge 尚未在真实 Capture 17.2 中重新加载验收，ProcessSupervisor 也尚未向真实 Capture 发送 WM_CLOSE**；Capture 专有 Dbo / 页面 / 工程保存与退出、Session Create / Close 专用 ABFP 消息、每 Session FIFO Broker、真实保存提示处理和完整 Skill 生命周期仍待后续阶段。

## 8. P1.6 正式 Capture Tcl Adapter / Bridge 软件实现

### 8.1 实现边界

新增 `capture_tcl_bridge.py`，包含：
- `CaptureTclBridgeSession`：为每个 Session 创建独立工作目录、随机 startup_nonce、桥接脚本与专属 capAutoLoad hook；
- `bridge.tcl`：由 Capture 的 capAutoLoad 自动 source，在 Capture 自己的 Tcl 事件循环内以 `after 200` 轮询；
- 请求文件经临时文件 + 原子重命名投递；Capture 将 `.req` 原子认领到 `running`，单次轮询只执行一个请求；
- READY 文件包含 session_id、startup_nonce、runtime_pid、Tcl 版本；B 端必须匹配本次 nonce 和 Popen PID；
- 结果包含 request_id、payload SHA-256、Capture PID、Tcl 版本、return code、result、stdout/stderr、errorInfo/errorCode 和运行时间；
- 脚本大小限制 1 MiB；请求等待超时或 Capture 退出时返回 UNKNOWN，不自动删除仍可能执行的请求，也不自动重放；
- `RequestBroker` 将 `request_state=unknown` 持久化为 UNKNOWN，重复 request_id 返回缓存状态。

### 8.2 Worker 集成

- EDA Tcl 请求选择 `capture-tcl-session` Profile；
- Allegro SKILL 仍由 `legacy-cadence-oneshot` 兼容 Profile 执行；
- 继续使用既有 EDA_TASK_SUBMIT / EDA_TASK_FIN 和 request_id 关联，不在 P1.6 新增 SESSION_CREATE / SESSION_EXEC 帧；
- 当前对端 CAPABILITIES 仍返回 `profiles=[]`，不广播未经主机级验证的 Capture Profile；
- P1.7 ProcessSupervisor 现在会先扫描所有 Capture.exe（包括手动实例）；对可见非对话框窗口只发送 WM_CLOSE，并等待真实退出；检测到保存/确认对话框、无法识别的窗口、超时或进程仍存活时阻止启动，不自动点击、不强杀。

### 8.3 软件 CI 证据与限制

GitHub Actions [Run #76](https://github.com/scanligithub/airbridge/actions/runs/43148279572)，提交 `9fc895c56c34f9f18c30fb573df8b7aa981485f9`：
- Protocol 10、RequestTracker 11、Capture Bridge 12、Runtime Adapter 11、RequestBroker 15 项，共 59 项；
- A/B 生产 SerialWorker 软件模拟 12 项；
- HTTP correlated request 集成 9 项；
- 合计 **80 项通过，两个 Job 均成功**。

本轮 Capture Bridge 测试使用 Python 文件队列模拟器；A/B 测试使用内存 ABFP 双向链路和 Fake Capture Bridge。它们验证 Adapter、文件协议、状态、去重、结果映射和 Worker 路由，不执行真实 Cadence API，也不证明生成 Tcl 在 Capture 17.2 上实际运行。因此需要下一步在真实 B 主机进行 Capture 17.2 / Tcl 8.6.0 实机复验。

补充回归 [GitHub Actions Run #88](https://github.com/scanligithub/airbridge/actions/runs/43150162732)，提交 `309a13f582b1d6aa1e58ad043f64fd002459dfe2)：新增 `test_generated_bridge_executes_in_standard_tcl_interpreter`，使用 Windows Runner 随 Python 3.12 安装的 Tcl 8.6 解释器直接 source 生成的 `bridge.tcl`，等待 after 轮询，执行 Tcl 脚本并验证 READY nonce / PID、返回值、stdout 和 result 文件。该回归将 Capture Bridge 单元测试增至 13 项；完整单元层 69 项、A/B Worker 模拟 12 项、HTTP 集成 9 项，合计 **90 项通过，两个 Job 均成功**。

标准 Tcl 8.6 解释器测试证明生成 Tcl 的语法和基础文件队列执行闭环可运行；它不包含 Capture 专有命令 / Dbo API、Capture GUI 消息循环和真实工程对象。因此新版 Bridge 仍必须在真实 Capture 17.2 / Tcl 8.6.0 中加载与验证。

