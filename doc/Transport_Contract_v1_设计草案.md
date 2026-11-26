# Transport Contract v1（MVP）设计草案

- 状态：Draft，供第一阶段实现与评审
- 目标：先跑通 Fake Provider、AirBridge Provider 和第一个 Skill
- 适用范围：通用远程通信；不包含任何 EDA 产品业务逻辑
- 关联决策：ADR-014

## 1. 设计目标

Transport Contract 为 Skill 提供一个简单、统一的远程操作接口。

第一阶段只解决这些问题：

1. Skill 不依赖 AirBridge 内部代码。
2. Fake Provider 和 AirBridge Provider 使用同一套接口。
3. Host Adapter 负责接入不同 Agent Host。
4. Provider 提供实际可用的远程操作能力。
5. 请求超时或连接中断时，不把未知结果误判为失败。
6. 保留现有服务端认证、权限和审计机制。

暂不建设复杂的 Provider Runtime、Profile 协商、Package Trust 或独立发布平台。

## 2. 最小架构

```text
Agent Host
    |
Host Adapter
    |
EDA Skill
    |
Transport Interface
    |
    +---- Fake Provider（测试）
    |
    +---- AirBridge Provider（实际使用）
              |
          AirBridge API
              |
        ABFP / Serial
              |
        Host B AirBridge
              |
       Generic Remote Operations
```

职责：

| 组件 | 负责内容 |
|---|---|
| Skill | EDA 产品业务逻辑和操作流程 |
| Host Adapter | Skill 加载、工具注册、Host 调用适配 |
| Transport Interface | 定义通用远程操作接口和返回结果 |
| Provider | 将接口映射到具体远程通信实现 |
| AirBridge Core | 通用通信、Shell/RFS、认证、请求关联和审计 |

约束：

- Capture、OrCAD PCB Editor、Allegro、AD 等保持独立 Skill。
- Skill 不调用 AirBridge 内部模块或内部 HTTP 路由。
- AirBridge Core 不增加 EDA 专用 API，不承担 EDA 生命周期逻辑。
- 不同 EDA Skill 之间不互相依赖。
- Fake Provider 不需要 AirBridge 或 EDA 环境。

## 3. 最小 Transport Interface

第一版采用简单接口，不要求复杂的 Factory、Runtime Manager 或多层 Session 管理。

```text
TransportProvider
  get_info() -> ProviderInfo
  get_capabilities() -> CapabilityList
  shell_execute(command, options, request_id) -> OperationResponse
  filesystem_*() -> OperationResponse   // 可选，按实际需要实现
  close() -> None
```

其中：

- `get_info()` 返回 Provider 名称、实现版本和支持的 Contract 版本。
- 第一版 Contract 固定为 `1.0`；不实现复杂版本协商。双方版本不兼容时直接拒绝初始化。
- `get_capabilities()` 返回当前 Provider 实际支持的操作。
- Shell 和 Filesystem 是可选能力；未实现的操作明确报告不支持。
- `close()` 释放本 Provider 实例使用的本地资源，不代表远端请求被取消。
- 一个 Skill 实例持有自己的 Provider 实例；第一版不要求共享连接池。

建议的最小数据结构：

```text
ProviderInfo {
  provider_name,
  provider_version,
  contract_version
}

Capability {
  name,
  supported
}

OperationResponse =
  COMPLETED {
    request_id,
    success,
    result?,
    error?
  }
| ACCEPTED {
    request_id,
    provider_request_id?
  }
| UNKNOWN {
    request_id,
    reason
}
```

第一版不要求实现独立的 Profile、CapabilitySnapshot 版本绑定或完整公共类型体系。Provider 必须如实报告不支持的能力，不能用空结果伪装成功。

## 4. 请求 ID 与 UNKNOWN

这是第一版必须保留的可靠性要求。

- 每次逻辑远程操作由调用方生成唯一 `request_id`。
- 查询或恢复同一操作时继续使用原 ID。
- 本地提交超时或连接中断，不代表远端没有执行。
- 如果无法确定远端是否接收或完成请求，返回 `UNKNOWN`。
- UNKNOWN 状态下不得自动生成新 ID 重放非幂等操作。
- 只有取得可靠结果后，才能把 UNKNOWN 更新为成功或失败。
- 请求查询能力如果 Provider 不支持，应明确报告，不得假装能够查询。
- 请求关联不等于服务端去重；只有后端确实保证去重时，才允许据此安全重试。

第一版只要求覆盖最常见的三种结果：

| 结果 | 含义 |
|---|---|
| COMPLETED | 已取得确定的执行结果 |
| ACCEPTED | 远端已接收，但尚未取得最终结果 |
| UNKNOWN | 无法确定远端是否接收或执行完成 |

不在第一阶段实现复杂的请求恢复协议和多层状态机。

## 5. Shell 与 Filesystem

### 5.1 Shell

第一阶段优先实现 Shell：

```text
shell_execute(command, options, request_id)
```

- 只提供通用远程命令执行。
- 不提供 Capture、Allegro 等 EDA 专用命令接口。
- 执行权限由远端服务端控制。
- 命令执行结果必须保留 request_id。
- 超时不能被解释为远端进程已停止。

### 5.2 Filesystem

Filesystem 作为可选能力，按实际 Skill 需要逐项实现：

- list
- stat
- read
- write
- mkdir
- move
- remove

第一阶段不要求实现大文件分块传输、复杂写入事务或跨卷原子移动。Provider 必须明确支持哪些操作，以及写入失败时是否可能留下部分结果。

## 6. 认证与基本安全

保留现有 AirBridge 安全机制，不重新建设一套认证系统。

- Skill 不读取或输出 Token、密码等秘密。
- 凭据由 Host/Provider 现有受控配置提供。
- AirBridge 服务端继续执行认证、Scope、请求所有权、审计和限流。
- 不扩大现有 Token Scope。
- 不通过 Shell 或 RFS 绕过被禁用的 EDA Execute。
- 日志只记录必要的请求 ID、操作类型和脱敏结果。
- Provider 不支持某项能力时必须拒绝调用，不能静默切换到权限更宽的操作。

第一阶段暂不要求独立 Credential Source 产品化、签名密钥轮换、离线撤销协议或构建来源证明。

## 7. Skill 与 Host Adapter

Skill 保持简单、独立：

```text
Skill
  initialize(provider)
  invoke(input)
  shutdown()
```

- Host Adapter 负责发现和加载 Skill，并把 Host 调用映射到 Skill 的 invoke。
- Skill 只接收 TransportProvider 接口，不依赖 OpenCode、Antigravity 等 Host 私有对象。
- Host Adapter 为 Skill 提供 Provider 实例。
- Skill 初始化时检查 Provider 版本和所需能力；不满足时明确报错。
- 不同 EDA Product Skill 独立安装和升级。
- 第一阶段不设计复杂的 Skill Package 签名、依赖解析和原子升级机制；先使用仓库内受控目录和简单 Manifest。

建议最小 Manifest：

```yaml
skill_id: orcad.capture
version: 0.1.0
contract_version: "1.0"
entrypoint: skill.py
required_capabilities:
  - shell.execute
```

此 Manifest 仅用于第一阶段加载和能力检查，不作为正式包签名或发布信任协议。

## 8. Fake Provider 与测试

先做少量、能直接帮助开发的测试：

1. Fake Provider 可以被 Skill 初始化和调用。
2. Fake Provider 与 AirBridge Provider 使用相同接口。
3. 能力缺失时 Skill 明确报错。
4. Shell 成功执行时返回确定结果。
5. 提交超时或连接中断时返回 UNKNOWN，不误报失败。
6. request_id 在提交和结果中保持一致。
7. Provider close 后不能继续调用。
8. 凭据不出现在日志和返回结果中。

第一阶段不建设 45 类完整 Conformance Suite。随着真实问题出现，再增加对应测试。

## 9. 当前 AirBridge 的实现顺序

结合现有 AirBridge 能力，按以下顺序推进：

1. **精简并评审本 Contract。**
2. **实现 Fake Provider**，用最小测试验证 Skill 与 Provider 解耦。
3. **实现 AirBridge Provider**，先映射现有 Shell API，不修改 AirBridge Core 的 EDA 职责边界。
4. **验证 Host A 到 Host B** 的真实 Shell 调用、请求 ID 和 UNKNOWN 行为。
5. **把 OrCAD Capture Skill 接入 Transport Interface**，先完成一个只读、低风险的实际操作。
6. 验证通过后，再逐步加入 Filesystem 和更多 Skill。
7. 后续独立开发 OrCAD PCB Editor、Allegro、AD Skills。

每一步都先测试再进入下一步。Fake Provider 测试通过不等于真实 AirBridge 或 EDA 集成验收通过。

## 10. 明确暂缓的事项

以下内容不作为第一阶段前置条件：

- 多层 Provider Runtime、共享连接池和复杂 Factory。
- Optional Profile 独立版本协商。
- 完整 Skill Lifecycle / Invocation 规范。
- 复杂 Skill Package / Manifest / Trust 规范。
- 数字签名信封、发布者密钥轮换和离线撤销元数据。
- 构建来源证明和可复现构建体系。
- 大规模 Conformance 测试框架。
- 复杂版本兼容矩阵和独立规范仓库。
- 大文件分块传输协议。

如果后续出现实际需求，再单独设计和实现，不提前阻塞第一版。

## 11. 第一阶段验收标准

第一阶段完成时，必须能够证明：

- Fake Provider 可以独立运行并通过基础测试。
- AirBridge Provider 能通过既有 AirBridge API 通信。
- Host A 能经 AirBridge 与 Host B 完成一次真实远程 Shell 操作。
- request_id 能贯穿调用和结果。
- 发生不确定情况时返回 UNKNOWN，不盲目重放。
- 一个最小 Capture Skill 能通过 Transport Interface 调用 Provider。
- AirBridge Core 没有增加 EDA 专用业务逻辑。
- 未扩大现有权限或改变凭据配置。

以上验收完成后，才认为 Transport MVP 跑通。
