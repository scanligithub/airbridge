# Allegro SKILL Runtime Adapter v1.0

- 文档编号：AB-RUNTIME-ALLEGRO-001
- 版本：1.0
- 日期：2026-11-12
- 阶段：P1.9.1
- 状态：**软件 CI PASS（Run #94，102 项）；Host B 真实 Allegro 17.2 回归未执行**

## 1. 目标

将 Allegro SKILL 从通用 Cadence fallback 路由中分离出来，形成独立 Runtime Adapter / Profile，明确脚本类型和能力范围，同时保持已实机验证的旧 Allegro 工作流不变。

## 2. 边界

AirBridge Core / RequestBroker 仍只处理通用请求 ID、幂等、结果缓存和 Profile 执行分派。Allegro 专业语义仅存在于 Allegro Adapter 与既有 EDAExecutor 中；PCB 工作流决策仍由 Agent Skill / 用户负责。

本阶段不实现：
- Allegro SKILL 常驻 Interpreter；
- Allegro 专用文件队列 Bridge；
- Allegro ProcessSupervisor 接入；
- 新的 SESSION_CREATE / SESSION_CLOSE / SESSION_EXEC 帧；
- 自动保存、确认或丢弃 PCB 设计；
- 对用户手动启动 Allegro 的进程边界做任何改变。

## 3. Adapter / Profile

| 字段 | 值 |
|---|---|
| adapter_id | `allegro-skill-legacy` |
| profile_id | `allegro-skill-oneshot` |
| operation | `execute` |
| execution_mode | `one-shot-gui` |
| capabilities | `skill.execute`, `one-shot-process`, `gui.keep-open`, `board.open` |

Adapter 使用现有 `detect_script_type()` 验证脚本被识别为 `ALLEGRO_SKILL`。Tcl 脚本会在调用 EDAExecutor 前被拒绝。

Adapter 将下列参数传给原 `EDAExecutor.execute_script()`：
- SKILL 内容与文件名提示；
- 超时；
- Cadence 版本选择；
- 主机 B 本地 `.brd` 路径；
- `keep_open`；
- 日志回调。

结果保留 `success`、`returncode`、`output` 三个字段，Worker / Broker 再按原协议包装 request_id 和请求状态。

## 4. 路由

```text
EDA_TASK_SUBMIT
    |
RequestBroker (eda.execute)
    |
select_eda_profile(script, hint)
    +-- Allegro SKILL -> allegro-skill-oneshot
    |                     -> AllegroSkillAdapter
    |                     -> existing EDAExecutor
    |
    +-- Capture Tcl + Capture Adapter ready
    |                     -> capture-tcl-session
    |                     -> CaptureTclAdapter
    |                     -> CaptureTclBridgeSession
    |
    +-- compatibility fallback -> legacy-cadence-oneshot
```

## 5. 保持不变的 Allegro 行为

专用 Adapter 仍委托给原 EDAExecutor，因此以下路径不变：
1. Cadence 版本 / 安装位置选择；
2. 主机 B 检查 `.brd` 路径；
3. 生成 `_polyfill.il`、`_task.il`、路径 sidecar 和 `_run.scr`；
4. 使用 `allegro.exe -s _run.scr` 启动；
5. 加载 Polyfill 与 SKILL 探针；
6. 通过 `axlOpenDesign()` 在交互式 GUI 中打开 BRD；
7. `keep_open=True` 时保留 GUI；
8. 原有独立关闭命令请求正常关闭；不自动保存或丢弃。

## 6. 软件验收项

- Profile 已注册，Adapter / Profile 能力集合一致；
- Allegro SKILL 被路由到专用 Profile；
- Tcl 脚本不会进入 Allegro Adapter；
- version / target_file / timeout / keep_open / log callback 完整透传；
- 非零退出码返回 `success=false`；
- 旧 Cadence fallback 与 Shell Profile 不回归。

软件 CI 不证明真实 Allegro 17.2 GUI 已启动或 BRD 已打开。完成软件验收后应在 Host B 做一次最小回归：通过主机 A 既有“打开 PCB”流程选取 `.brd`，确认 GUI 显示目标设计，再通过原“关闭 PCB”功能正常退出。

## 7. 后续决策

P1.9.2 是否将 Allegro 接入 ProcessSupervisor，必须先确认用户手动启动 Allegro 的实例边界与旧关闭行为；不能简单复制 Capture 的“启动前关闭所有同名进程”策略。长期是否实现持久 SKILL Bridge，也应先验证 Allegro 17.2 可用的自动加载入口和脚本接收机制，不假设存在与 Capture Tcl 相同的 capAutoLoad 行为。


## 8. P1.9.1 软件验收结果

[GitHub Actions Run #94](https://github.com/scanligithub/airbridge/actions/runs/43158226213) 在提交 `7d6ecedee4177d3b1b5d1604b062383ee334ffb7` 上完成：
- Protocol Unit Job：81 项测试通过；
- A/B duplex + HTTP Job：12 项 Worker 模拟 + 9 项 HTTP 集成通过；
- 两个 Job 均成功，合计 102 项通过、0 项失败。

新增的 4 项覆盖 Allegro Adapter 委托与参数透传、Tcl 拒绝、非零退出码、Capture / Allegro / fallback Profile 分流。此次 CI 不启动真实 Allegro，也不验证 GUI 开板，因此 Host B 最小实机回归仍是下一项验收。
