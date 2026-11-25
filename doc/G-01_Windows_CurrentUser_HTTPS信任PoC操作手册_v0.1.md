# G-01：Windows CurrentUser HTTPS 信任 PoC（已取消）

- 文档编号：AB-M2.5-G01-RUNBOOK-001
- 原版本：0.1
- 状态：**已由用户明确取消；不再执行、不再作为 M2.5 验收门禁**
- 取代决策：[ADR-013：M2.5 STDIO 本机 MCP 接入与客户端托管生命周期](ADR-013_M2.5_Stdio本机MCP接入与客户端托管生命周期.md)

## 取消说明

M2.5 首版改为 Antigravity / OpenCode 启动本机 AirBridge.exe STDIO 子进程。不使用证书，不建立本机或远程 HTTP / HTTPS MCP endpoint，不实现 OAuth Broker。因此以下 G-01 工作全部取消：

- 测试 CA / localhost Leaf 证书创建；
- CurrentUser Root 信任导入、CryptUIWizImport、证书轮换 / 撤销 / 清理；
- Chrome、Antigravity、OpenCode 的 localhost TLS 信任矩阵；
- OAuth discovery、PKCE、CIMD / DCR、OAuth Grant 和 Bearer Token over HTTPS；
- G-01A / G-01A2 / G-01A3 / G-01B Windows 证书 CI / 桌面 PoC；
- 为交互式证书导入设置 Windows Self-hosted Runner。

本文保留仅用于记录此前研究过程；其中 PowerShell 命令、测试脚本、证书导入流程和 PASS / NOT RUN 状态均不是当前实施指引。不得再按本文执行证书创建或导入。

## 取代后的验证方式

GitHub Actions 改为验证：
- STDIO MCP framing、initialize、tools/list、Tool 调用、取消、EOF 和 broken pipe；
- Antigravity / OpenCode 本机配置生成、更新、幂等性和回滚；
- M2.4 Credential Provider、Agent / Scope 隔离以及 Gateway OFF fail-closed；
- 子进程有界退出、无孤儿进程、无 TCP listener；
- 原有 Capture Process Inventory 回归。

Antigravity / OpenCode 的真实桌面连接在目标 Windows 桌面上分别验收，不需要证书或 Windows 桌面 CI Runner。

**当前权威方案：ADR-013。**
