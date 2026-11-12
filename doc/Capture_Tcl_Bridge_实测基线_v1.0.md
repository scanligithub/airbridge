# Capture Tcl Bridge 实测基线与接入规范

- 文档编号：AB-ADAPTER-CAPTURE-001
- 版本：1.0
- 状态：**P1.6 Capture Tcl Adapter / Bridge 已在真实 Capture 17.2 / Tcl 8.6 实机验证；Capture 正常关闭与重启复用 PASS；DboSession / DboDesign 命令清单已实测登记；工程对象深度读取及专用 Session Close 协议仍待后续验证**
- 日期：2026-11-12
- 实测环境：Windows / OrCAD Capture SPB 17.2-2016 / Tcl 8.6.0
- 关联文档：
  - AirBridge 通用 Runtime Session 架构设计 v1.0
  - AirBridge Runtime Session Protocol v1

> 本文前半记录早期真实 Capture 17.2 原型实测证据；第 8 节记录 P1.6 正式 Python Adapter / 生成式 Tcl Bridge 的实现与软件 CI；第 9 节补充 2026-11-12 在真实主机 B / Capture 17.2 / Tcl 8.6 上完成的正式 Bridge、进程生命周期及 Dbo 命令清单验收。云端 CI 本身仍使用确定性模拟器，不会启动真实 Cadence Capture；实机结论以第 9 节记录的主机 B 证据为准。

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
- [x] 2026-11-12 在主机 B 对 AirBridge 启动的 Capture PID 6648（Start Page、无工程）发送 WM_CLOSE，约 1 秒正常退出；随后 CIM 查询确认 PID 6648 不存在且无残留 Capture.exe。
- [ ] 保存提示、多个手动实例、无主窗口及无响应进程场景仍待真实主机验收；本次关闭测试未覆盖这些场景。
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

**当前结论：** 2026-11-12 已在真实 Capture 17.2 / Tcl 8.6.0 上完成正式生成式 Bridge 自动加载、单条及连续请求、Tcl 全局状态保持、错误隔离后恢复、并发请求串行隔离、WM_CLOSE 正常关闭、关闭后新 Session 重启及新 Session 状态复用；真实进程和 READY / LOCK PID 均得到独立核验。同期完成 `DboSession*` 53 条和 `DboDesign*` 96 条 Tcl 命令名称清单采集，详见第 9 节。P1.6 / P1.7 软件 CI 仍保留 Run #76、#85、#88 等历史证据。**本轮 Dbo 命令清单只证明命令在 Capture Tcl Interpreter 中注册，不代表 149 个命令的调用签名、副作用或实际设计对象操作均已验证**；真实保存提示处理、多个手动实例和无响应进程场景、Session Create / Close 专用 ABFP 消息、每 Session FIFO Broker、完整 Skill 生命周期仍待后续阶段。

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

标准 Tcl 8.6 解释器测试证明生成 Tcl 的语法和基础文件队列执行闭环可运行；它本身不包含 Capture 专有命令 / Dbo API、Capture GUI 消息循环和真实工程对象。真实 Capture 17.2 / Tcl 8.6.0 的 Bridge 加载及请求执行已于 2026-11-12 另行实机验证，证据见第 9 节。



## 9. 2026-11-12 正式 Bridge 主机 B 实测增补

### 9.1 实测环境与 Session 标识

本轮在真实 Windows 主机 B 上对正式生成式 Capture Tcl Bridge 进行实测：

| 项目 | 实测值 |
|---|---|
| Capture 版本 | OrCAD Capture SPB 17.2-2016 |
| Tcl 版本 | 8.6 |
| Capture 可执行文件 | `D:\Cadence\SPB_17.2\tools\bin\capture.exe` |
| 第一次 Session Capture PID | 6648 |
| 第一次 Session ID | `54cd6ad0-7ee7-4bea-8b28-c18820107cea` |
| 重启后 Capture PID | 3776 |
| 重启后 Session ID | `52c6e80b-d4cf-47aa-9143-f5e36815c152` |

第二次 Session 的 Bridge READY 记录和 `bridge.lock` 内容均指向 PID 3776；CIM 独立检查确认 Capture.exe PID 3776 存在。第一次 Session 正常关闭后，其 Session 目录仍保留，9 个结果文件未被新 Session 覆盖。

### 9.2 正式 Bridge 生命周期与请求行为

下列项目均通过主机 A → AirBridge → 主机 B → Capture 的真实链路执行：

| 验收项 | 实测结果 |
|---|---|
| capAutoLoad 自动加载生成式 Bridge，自动建立 READY / LOCK | PASS |
| Hook 被 Bridge source 后自动删除 | PASS |
| Tcl 单条请求执行和结果回传 | PASS，表达式结果 42 |
| 同一 Capture PID / Session 连续请求 | PASS |
| Tcl 全局变量跨请求保留 | PASS |
| Tcl 脚本报错后 Bridge 继续服务后续请求 | PASS |
| 两个并发 HTTP 请求在同一 Interpreter 中串行执行 | PASS，Trace 为 `A_START,A_END,B_START,B_END` |
| WM_CLOSE 正常关闭 Capture PID 6648 | PASS，约 1 秒退出 |
| 关闭后 CIM 确认 PID 6648 不存在，主机 B 无残留 Capture.exe | PASS |
| 关闭后重新执行请求，创建全新 PID / Session | PASS，PID 3776，Session ID 与旧 Session 不同 |
| 新 Session 的 READY / LOCK PID 与 Capture 进程一致 | PASS，均为 3776 |
| 新 Session 连续请求复用相同 PID / Session，且跨请求全局变量保留 | PASS |

并发测试的最终 Tcl Trace 是：

~~~text
A_START A_END B_START B_END
~~~

关闭测试针对 Start Page 状态，没有打开 DSN / OPJ 工程，也没有触发工程保存提示。因此本次 PASS 不覆盖未保存设计、保存提示、多个手动实例或无响应进程的处理。

### 9.3 Dbo 命令注册探测概况

在重启后的 Capture PID 3776 / Session `52c6e80b-d4cf-47aa-9143-f5e36815c152` 中执行只读 Tcl 命令枚举：

- `info commands *Dbo*` 返回 4,736 个匹配命令（宽泛匹配总数）。
- `info commands DboSession*` 返回 53 个命令。
- `info commands DboDesign*` 返回 96 个命令。
- `::DboSession_s_pDboSession` 全局变量存在且非空。
- `DboSession -this $lSession` 可执行；另一次只读探测中 `DboSession_GetActiveDesign` 命令存在。
- 命令库存采请求返回 `completed`、`returncode=0`，Tcl 表达式结果为 42。

以下是 2026-11-12 在该 Capture 17.2 / Tcl 8.6 环境中实际枚举到的完整命令名称。清单只记录命令注册情况，不提供参数签名，也不表示所有命令均已调用验证。

### 9.4 `DboSession*` 完整命令清单（53）

~~~text
DboSession
DboSession_CallTclAttributeTrigger
DboSession_CopyLibAll
DboSession_CreateDesign
DboSession_CreateLib
DboSession_FindExternalDesignsToBeRemoved
DboSession_GetActiveDesign
DboSession_GetDataRelease
DboSession_GetDataVersion
DboSession_GetDesign
DboSession_GetDesignAndSchematics
DboSession_GetDesignDataVersion
DboSession_GetFlatNetNamingStrategy
DboSession_GetIndexForString
DboSession_GetInterfaceVersion
DboSession_GetLib
DboSession_GetLibAndSchematics
DboSession_GetObjectFromUniqueStr
DboSession_GetOccByCanonicalName
DboSession_GetOpenDesign
DboSession_GetOpenDesigns
DboSession_GetOpenLib
DboSession_GetStringPtrFromIndex
DboSession_GetUniqueStringFromObject
DboSession_IsBitSet
DboSession_IsDesignPersistent
DboSession_IsEditingDisabledInSession
DboSession_IsFlatNetModificationRegistrationActive
DboSession_IsLibPersistent
DboSession_IsObjLocked
DboSession_m_StringTable_get
DboSession_m_StringTable_set
DboSession_m_StrTableArray_get
DboSession_m_StrTableArray_set
DboSession_MarkAllLibForSave
DboSession_NewDesignsIter
DboSession_NewLibsIter
DboSession_RemoveDesign
DboSession_RemoveLib
DboSession_SaveDesign
DboSession_SaveDesignAs
DboSession_SaveLib
DboSession_SaveLibAs
DboSession_SetBit
DboSession_SetFlatNetNamingStrategy
DboSessionDesignsIter
DboSessionDesignsIter_GetKey
DboSessionDesignsIter_Next
DboSessionDesignsIter_NextDesign
DboSessionLibsIter
DboSessionLibsIter_GetKey
DboSessionLibsIter_Next
DboSessionLibsIter_NextLib
~~~

### 9.5 `DboDesign*` 完整命令清单（96）

~~~text
DboDesign
DboDesign_AnnotateControlExists
DboDesign_DesignHasExternalSchematics
DboDesign_DesignHasOccurrenceProperties
DboDesign_DesignHasReusedSchematics
DboDesign_EndDBBatchUpdate
DboDesign_GetCISDesign
DboDesign_GetCISNotStuffedString
DboDesign_GetContainingLib
DboDesign_GetDCFPath
DboDesign_GetDesignId
DboDesign_GetDesignLoadStateInCM
DboDesign_GetDesignModifiedTime
DboDesign_GetFlatNetfrmId
DboDesign_GetFlatNetfrmName
DboDesign_GetFlatNetNamefrmId
DboDesign_GetInstOccurrence
DboDesign_GetInstOccurrenceByName
DboDesign_GetInstOccurrenceByRef
DboDesign_GetModifiedFlatNets
DboDesign_GetNetOccurrence
DboDesign_GetNetOccurrenceByRef
DboDesign_GetObjectType
DboDesign_GetOccurrence
DboDesign_GetOffPageOccurrence
DboDesign_GetOffPageOccurrenceByRef
DboDesign_GetOpenCISDesign
DboDesign_GetPageFromID
DboDesign_GetParentObj
DboDesign_GetPortOccurrence
DboDesign_GetPortOccurrenceByRef
DboDesign_GetPowerPinsVisible
DboDesign_GetRefDesMgr
DboDesign_GetRoot
DboDesign_GetRootName
DboDesign_GetRootOccurrence
DboDesign_GetRootSchematic
DboDesign_GetTitleBlockOccurrence
DboDesign_GetTitleBlockOccurrenceByRef
DboDesign_HasBoardNetlistGenerated
DboDesign_HasRootOccurrence
DboDesign_IsDesignCMDisabled
DboDesign_IsDesignCMEnabled
DboDesign_IsDesignInOccurrenceMode
DboDesign_IsDesignPartiallyCMEnabled
DboDesign_IsFlatDesign
DboDesign_isFlatNetsPresent
DboDesign_isHSObjectsExist
DboDesign_IsModified
DboDesign_IsPNNMActive
DboDesign_IsRemovePNNMSelected
DboDesign_IsRootOccurrenceExisting
DboDesign_LimitExceededNets
DboDesign_LimitExceededParts
DboDesign_MarkModified
DboDesign_NewCachesIter
DboDesign_NewConnectivityChangeIter
DboDesign_NewFlatNetsIter
DboDesign_NewGlobalsIter
DboDesign_NewOccurrencesIter
DboDesign_NewSchematicOccurrencesIter
DboDesign_OccurrencesExist
DboDesign_SetBoardNetListGenerated
DboDesign_SetCISNotStuffedString
DboDesign_SetConnectivityUpdateTracking
DboDesign_setDesignLoadStateInCM
DboDesign_SetIsPNNMActive
DboDesign_SetPowerPinsVisible
DboDesign_SetRemovePNNMSelected
DboDesign_SetRoot
DboDesign_sGetOpenPwdDialog
DboDesign_sGetPasswordFromUser
DboDesign_StartDBBatchUpdate
DboDesignCachesIter
DboDesignCachesIter_GetKey
DboDesignCachesIter_Next
DboDesignCachesIter_NextDesignCache
DboDesignFlatNetsIter
DboDesignFlatNetsIter_GetKey
DboDesignFlatNetsIter_Next
DboDesignFlatNetsIter_NextFlatNet
DboDesignGlobalsIter
DboDesignGlobalsIter_GetKey
DboDesignGlobalsIter_Next
DboDesignGlobalsIter_NextGlobal
DboDesignOccurrencesIter
DboDesignOccurrencesIter_GetKey
DboDesignOccurrencesIter_Next
DboDesignOccurrencesIter_NextOccurrence
DboDesignSchematicOccurrencesIter
DboDesignSchematicOccurrencesIter_GetKey
DboDesignSchematicOccurrencesIter_HasOccurrences
DboDesignSchematicOccurrencesIter_Next
DboDesignSchematicOccurrencesIter_NextSchematicOccurrence
DboDesignUpdateOccurrenceBlockerManager
DboDesignUpdateOccurrenceBlockerManager_Release
~~~

### 9.6 命令清单的使用边界

1. `DboSession*` / `DboDesign*` 的数量和名称是本次 Capture 17.2 安装、当前 Tcl Interpreter 的实测快照；不能直接推广为所有 Capture / SPB 版本的固定清单。
2. 构造器、迭代器类型、包装命令、getter、setter 和操作方法均包含在枚举结果中；命令名本身不表达完整 Tcl 调用签名。
3. 清单中包含可能修改对象或设计状态的命令，例如 `DboSession_SaveDesign`、`DboSession_RemoveDesign`、`DboDesign_MarkModified`、`DboDesign_StartDBBatchUpdate` 和各类 `Set*`。本次只枚举名称，没有调用这些写入方法。
4. 本轮未在当前 Start Page 测试中打开 DSN / OPJ，也未验证 `GetActiveDesign` 返回的设计指针、设计对象属性、迭代器遍历或保存行为。后续需在专用测试工程上逐项设计只读 API 验证，再单独批准写入测试。
5. 下一步可以依据本清单选择明确签名、可验证副作用的只读方法，先完成 Session / Design / Schematic / Page 的对象关系探测；在此之前不得仅凭命令名称推测调用参数。

