# AirBridge MCP Gateway：M2.1 / M2.2 实施设计

- 日期：2026-11-13
- 版本：1.0
- 状态：功能分支实现中；MCP SDK 注册与 in-process Client 测试等待 GitHub Actions 验证；主机 A 实机接入尚未验收
- 基线：M1 合并提交 43c0900b5728d9d13710e265bd87647804be2227

## 1. 目标与边界

M2.1 / M2.2 为其他 AI Agent 提供标准 MCP 接入，不修改 M1 已验收的 AirBridge Core、Capture Runtime Adapter 或 ABFP 链路。

MCP Gateway 是主机 A 上的独立进程，只调用 AirBridge 已公开的 HTTP API。它不 import AirBridge 内部 Python 模块，不直接访问串口，不连接主机 B 文件目录，也不解释 Tcl、Dbo、SKILL 或专业软件对象模型。

Capture / Allegro API、版本兼容、领域工作流和结果判断仍由各自 Agent Skill 负责。

## 2. 部署模型

- Agent MCP Client 在主机 A 启动 Gateway 子进程。
- Gateway 使用 MCP stdio transport。
- Gateway 使用 HTTP 访问 http://127.0.0.1:8888。
- AirBridge 继续使用既有 ABFP 串口链路与主机 B 通信。
- 多个 MCP Client 可启动多个 Gateway 进程；这些进程共享同一个 AirBridge HTTP 服务和当前 Runtime Session。

本阶段不提供公网 MCP listener，不实现远程 Agent 接入。

## 3. SDK 与运行环境

采用官方 MCP Python SDK v2.3.0，单独安装在 Python 3.11+ 虚拟环境。Gateway 依赖固定在 mcp_gateway/pyproject.toml，不进入 AirBridge 桌面程序的运行环境。

官方依据：
- SDK v2 文档：https://py.sdk.modelcontextprotocol.io/v2/
- v2.3.0 Release：https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.3.0
- stdio 规范：https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/transports/stdio.mdx

stdio 的 stdout 专用于 MCP JSON-RPC 消息；普通日志使用 stderr / logging。

## 4. 首批 MCP Tools

| Tool | AirBridge HTTP Endpoint | 权限属性 |
|---|---|---|
| airbridge_get_capabilities | GET /api/v1/runtime/capabilities | 只读 |
| airbridge_get_eda_versions | GET /api/v1/eda/versions | 只读 |
| airbridge_eda_execute | POST /api/v1/eda/execute | 代码执行，默认禁用 |
| airbridge_request_status | GET /api/v1/requests/{request_id} | 只读 |

不开放 Remote Shell；不虚构 /sessions、/profiles 或 per-Agent Session API。

## 5. 执行与恢复语义

- 执行工具为每次请求生成独立 UUID request_id。
- Gateway 将脚本作为不透明字符串发送，不解析或修改正文。
- 序列化后的 HTTP 请求体限制为 60,000 字节，留出 ABFP 帧头和封装字段余量。
- 执行工具默认关闭；只有明确设置 AIRBRIDGE_ENABLE_EDA_EXECUTE=1 才发送请求。
- 若 HTTP 响应无法确认，返回 unconfirmed 和原 request_id；调用方应查询状态，禁止自动重发。
- Remote Shell 不在本阶段暴露。
- 大载荷 artifact / RFS 上传不在本阶段实现。

## 6. 安全边界与已知限制

Gateway 配置只接受 loopback HTTP 地址，默认 127.0.0.1:8888。stdio 不创建网络监听。

当前 AirBridge HTTP API 尚未具备 Agent 身份认证和服务端角色授权。因此：
- 执行工具默认禁用；
- MCP annotations 只是客户端提示，不是授权机制；
- loopback 绑定不能构成每 Agent 身份隔离；
- 不得将 8888 暴露到 LAN 或公网；
- Agent 身份认证、服务端授权、审计和配额属于 M2.3；
- 每 Agent 独立 Runtime Session 尚未实现。

## 7. 验证范围

本地已完成 8 项 HTTP Gateway 单元测试，覆盖现有端点路由、请求 ID、脚本不透明透传、默认禁用、参数验证、请求体大小、非 UUID 状态查询和 loopback URL 限制。

另有 3 项 MCP SDK 测试，覆盖四个工具注册、执行工具的 destructive / non-idempotent 注解，以及官方 SDK in-process Client 对工具列表和工具调用的测试。这些测试依赖 MCP SDK，需要由新建 GitHub Actions 工作流执行。

尚未完成：
- GitHub Actions 两个 Python 版本的实际结果；
- 主机 A 安装独立 Python 3.11 环境；
- 至少一个真实 MCP Host 的工具发现与只读调用；
- 在用户明确开启执行权限后，使用当前运行 Capture Session 做一次 EDA execute / request status 实机点到点验证；
- 第二个独立 Agent MCP Client 的并发联调。

完成上述验收后，才将 M2.1 / M2.2 标记为 PASS。
