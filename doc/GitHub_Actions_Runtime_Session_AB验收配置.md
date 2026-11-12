# GitHub Actions：Runtime Session 云端 A/B 软件验收

## 目标与运行环境

本项目不依赖用户提供 Windows 实体机，也不注册 self-hosted runner。CI 使用 GitHub 托管的 `windows-2025` 标准 Runner；每个 Job 由 GitHub 自动分配临时 Windows 虚拟机。

当前仓库为公开仓库，GitHub 官方文档列明公开仓库的标准 GitHub-hosted Runner 免费且不限标准 Runner 使用量。Runner 的标签和规格以 GitHub 官方文档为准：
https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/choose-the-runner-for-a-job

## CI 分层

工作流：`.github/workflows/runtime-session-ci.yml`

### 第一层：协议单元测试

- 在 GitHub 托管 Windows 2025 虚拟机上运行；
- 执行 `test/test_runtime_session_protocol.py`；
- 覆盖 JSON Envelope、UUID、schema、载荷上限、ABFP 帧编解码和旧 FrameType 兼容。

### 第二层：A/B 双端软件模拟

- 在另一台 GitHub 托管 Windows 2025 虚拟机上运行；
- 安装 PySide6 和 pyserial，以便导入 AirBridge 当前生产模块；
- 创建两个真实的 `SerialWorker` 对象，但不启动 GUI、线程或真实串口；
- 通过内存双向链路传输 `ABFPFrame.pack()` 产生的二进制帧，并由对端 `FrameParser.feed()` 解析，再调用生产代码的帧处理方法；
- 对 A、B 两端分别发起 HELLO，验证双向 CAPABILITIES 响应及 correlation_id 关联；
- 验证双方最终状态为 `negotiated`，功能仅包含 `handshake`，`profiles=[]`，且 `can_create_session=false`；
- 验证无关 correlation_id 的 CAPABILITIES 被忽略；
- 验证链路重置会结束旧 waiter，并拒绝旧连接代次的握手请求。

这层不是另写一套替代协议的模拟器；它驱动 `SerialWorker` 中的生产握手入口和处理方法，ABFP 帧仍经过真实打包和流式解析。

## 触发方式

- Push 到 `feature/runtime-session-p1-2` 且修改协议实现、握手实现、相关测试或该 Workflow 时自动运行；
- 配置了 `workflow_dispatch` 手动触发入口；分支上的代码变更由 `push` 路径过滤器自动触发；
- 不需要注册 Runner、不需要配置仓库 Secrets，也不需要用户提供 Windows 机器。

## 验收结果的边界

CI PASS 代表：协议单元测试通过，并且当前生产 `SerialWorker` 握手处理逻辑通过双端内存 ABFP 链路模拟。

CI PASS **不代表**以下内容已经实测：

- 两台独立 Windows 主机之间的真实 COM/USB/串口物理通信；
- 串口驱动、波特率、电气链路、硬件丢包和真实链路重连；
- Capture / Allegro 已安装、已启动或能够执行 Tcl / SKILL；
- Session Broker、Session Create、Request 执行、request_id 结果关联、进程监督或优雅关闭。

由于当前没有可供本项目使用的 Windows 实体机，真实物理链路验收暂不纳入自动 CI；不得把软件模拟 PASS 写成物理 A/B 实测 PASS。后续如果获得实际硬件环境，可另行增加可选的硬件在环验收，而不影响云端 CI。

## 结果解释

- **Protocol unit tests PASS**：协议 Envelope 与 ABFP 帧契约通过；
- **A/B simulation PASS**：两个生产 SerialWorker 状态机在模拟链路上完成双向握手，关联及重置行为符合预期；
- **CI FAIL**：查看对应 Job 日志，按失败测试定位；
- 新工作流不再依赖 `airbridge-a` / `airbridge-b` 自托管 Runner；此前旧工作流创建的 queued 运行记录可能仍留在 Actions 历史中，不属于新的云端 CI。

## 安全边界

- Workflow 权限仅为 `contents: read`；
- 不启动任意远程 Shell；
- 不访问用户主机、COM 端口或 Cadence 工程；
- 不上传设计文件或真实工程数据作为 Action artifact。
