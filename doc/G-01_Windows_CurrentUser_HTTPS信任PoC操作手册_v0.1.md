# G-01：Windows CurrentUser HTTPS 信任 PoC 操作手册

- 文档编号：AB-M2.5-G01-RUNBOOK-001
- 版本：0.1；日期：2026-11-20
- 状态：草案，尚未在任何目标 Windows 主机执行；G-01 仍为 Partial Research / NOT PASS
- 关联：架构评审决策清单 G-01、ADR-007、实施路线图 §8.1

## 1. 目的与判定边界

**部署时点是本 PoC 的核心验证对象。** M2.5 的 CA / localhost 服务器证书创建及 CA 公钥信任导入属于每台 AirBridge 主机的部署流程。这里的 PowerShell 操作仅用于模拟并验证未来部署程序在目标用户上下文完成的同一流程，不是要求用户安装后再手动导入，也不是 Gateway 启动时的临时步骤。

每台安装 M2.5 的主机都在部署阶段执行 CA trust bootstrap；无论该机首次安装后 Gateway ON 还是 OFF，都不跳过 CA 导入。CA 导入成功不会改变 `gateway.enabled=false`，不会启动 OAuth Broker 或任何 Worker。用户拒绝时安装仍可完成，但 Managed HTTPS 保持不可用。本次 PowerShell PoC 在目标机本地创建一次性测试 CA 以模拟部署导入动作，并不冻结正式 CA 的签发 / 供应来源；正式部署不应要求从 Host A 或 Host B 手工导出 CA。

G-01 分为三个阶段：

1. **证书与 Windows 信任库**：创建短期一次性 CA 和 localhost 服务器证书；用户明确同意后，只把 CA 公钥导入 CurrentUser Trusted Root；确认 LocalMachine Root 未变化。
2. **客户端 TLS 兼容**：使用 loopback HTTPS 测试端点，分别验证 Chrome、Antigravity App / IDE、OpenCode Desktop。OpenCode CLI 如列入首版支持矩阵，必须单独测试。
3. **真实协议集成**：在 OAuth Broker / Agent Worker 或等价本机 PoC 服务上完成 discovery、OAuth 授权、MCP initialize、tools/list 和只读 Tool 调用，再执行负向测试。

**阶段 1 通过不代表阶段 2 通过；Chrome 通过不代表 MCP 客户端通过；任何单客户端 TLS 成功都不代表完整 G-01 PASS。** 只有首版承诺支持的客户端形态完成真实 TLS + OAuth + MCP，且负向场景均 fail-closed，才允许 G-01 PASS。

本文只是操作规范，不代表已执行命令或已创建、导入、删除证书。

## 2. 安全约束

- 只使用当前 Windows 用户证书存储；禁止写入 Cert:\LocalMachine\Root 或其它 LocalMachine 信任位置。
- 使用普通权限，不要求管理员权限。
- 信任导入前向用户展示 CA Subject、Thumbprint、有效期和用途，并取得明确同意；拒绝时停止。
- 只导出 CA 公钥 CER；CA 私钥必须留在当前用户密钥容器并设为不可导出。
- 测试 CA 必须短期、一次性，不得复用为生产 CA。
- 服务端只允许绑定 127.0.0.1 和 / 或 ::1；禁止 0.0.0.0、::、通配地址或 LAN 接口。
- Leaf 必须有 DNS SAN localhost、Server Authentication EKU；客户端必须正常校验证书链、有效期和主机名。
- 禁止 NODE_TLS_REJECT_UNAUTHORIZED=0、忽略证书错误、关闭 TLS 验证或降级到明文 / 无认证 HTTP。
- 测试 URL 必须指向本机 loopback。
- 清理时只删除本次新建并记录的精确 Thumbprint，不按 Subject 通配删除。

## 2.1 每台主机的证书归属与测试范围

本 PoC 不指定 Host A 或 Host B。对**每一台实际部署 M2.5 AirBridge 的 Windows 主机**，均须在部署阶段独立执行本机 CA trust bootstrap；即使该机的 Gateway 默认 OFF，也执行证书准备与 Consent。

- 本次 PoC 在目标机本地创建短期测试 CA 和 localhost Leaf；正式产品部署流程提供 / 准备适用于该机 localhost 的 CA 公钥信任材料。正式 CA 签发 / 供应方式仍由 G-01 技术验证确定，不预设从哪台主机导出。
- 只将该机 CA 的公钥证书，在运行 MCP Client 的 Windows 用户上下文中经明确同意导入该用户的 CurrentUser Root。CA 私钥不复制、不发送给另一台机器，也不放入仓库、共享目录或 AirBridge Runtime 通道。
- Gateway Broker / Worker 使用该机本地 Leaf；MCP Client 通过 localhost 连接同一台机器的 loopback listener。localhost 永远指向客户端所在机器，因此不支持由另一台主机上的 MCP Client 通过 localhost 访问本机 Gateway。
- A、B 的 AirBridge Runtime / 串口通信与本机 HTTPS 信任是两条独立路径；对端不需要信任本机 CA。即使某台机器 Gateway 保持 OFF，部署阶段仍完成该机 CA 信任准备；OFF 只阻止 Broker / Worker 启动。
- CurrentUser 是每台 Windows 主机上的用户配置，不是跨机器共享的信任库。若两台机器都部署 M2.5，必须分别在各自部署流程中征求同意、导入该机适用的 CA 公钥并核验 CurrentUser Root；不要求从某一台机器导出 CA，Root Thumbprint 是否相同取决于最终供应模型。
- 本手册的默认测试假设 AirBridge GUI / Gateway 与 MCP Client 由同一 Windows 登录用户运行。若实际部署采用不同 Windows 账户，必须分别验证客户端账户的 CurrentUser Root 信任和 Gateway 账户的私钥访问权限；不得假定一个账户的导入会影响另一个账户。

## 3. 目标 Windows 主机执行前基线

在当前要验证的 Windows 主机上，使用运行 AirBridge / MCP Client 的目标 Windows 用户，以普通权限打开 PowerShell。此阶段只读取信息；本节适用于当前被选作 PoC 的目标主机，不区分 A / B 角色。

~~~powershell
$PSVersionTable
Get-Module -ListAvailable PKI
Get-Date -Format o

$evidence = Join-Path $env:USERPROFILE ("Desktop\AirBridge-G01-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
New-Item -ItemType Directory -Path $evidence -Force | Out-Null

$lmRootBefore = @(Get-ChildItem 'Cert:\LocalMachine\Root' | ForEach-Object { $_.Thumbprint } | Sort-Object)
$cuMyBefore = @(Get-ChildItem 'Cert:\CurrentUser\My' | ForEach-Object { $_.Thumbprint } | Sort-Object)
$lmRootBefore | Set-Content (Join-Path $evidence 'localmachine-root-before.txt')
$cuMyBefore | Set-Content (Join-Path $evidence 'currentuser-my-before.txt')

Get-ChildItem 'Cert:\CurrentUser\Root' | Select-Object Subject, Thumbprint, NotBefore, NotAfter | Sort-Object Thumbprint
Get-ChildItem 'Cert:\LocalMachine\Root' | Select-Object Subject, Thumbprint, NotBefore, NotAfter | Sort-Object Thumbprint
~~~

注意：这里的 CurrentUser 指**本机当前登录用户**的证书视图，并非跨机器的共享存储。Windows CurrentUser 逻辑证书视图还可能继承本机 LocalMachine 证书。因此，CurrentUser Root 中“可见”本身不足以证明证书是用户级新导入。证据须同时包括本次唯一 Thumbprint、certutil 用户范围查询，以及 LocalMachine Root 前后清单一致。若无法读取 LocalMachine 基线或系统状态无法解释，停止并记录阻塞原因。

**会话要求：** 第 3 至第 9 节应在同一个普通权限 PowerShell 会话中依次执行，以保留变量。若会话中断，重新打开 PowerShell 后，必须将 $evidence 设为原证据目录，并从其中的 Thumbprint / 基线文件恢复变量；不得重新生成证书来补齐旧证据。

## 4. 创建一次性测试 CA 和 localhost Leaf

下列命令把 CA 和 Leaf 放在 CurrentUser My，私钥均不可导出；不会把 CA 加入 Root 信任库。

~~~powershell
$rootParams = @{
    Type = 'Custom'
    Subject = 'CN=AirBridge G-01 Disposable Test Root'
    KeyAlgorithm = 'RSA'
    KeyLength = 3072
    HashAlgorithm = 'SHA256'
    KeyExportPolicy = 'NonExportable'
    KeyUsageProperty = 'All'
    KeyUsage = @('CertSign', 'CRLSign', 'DigitalSignature')
    TextExtension = @('2.5.29.19={critical}{text}ca=TRUE&pathlength=0')
    NotAfter = (Get-Date).AddDays(30)
    CertStoreLocation = 'Cert:\CurrentUser\My'
}
$root = New-SelfSignedCertificate @rootParams

$leafParams = @{
    Type = 'Custom'
    Subject = 'CN=localhost'
    DnsName = 'localhost'
    Signer = $root
    KeyAlgorithm = 'RSA'
    KeyLength = 2048
    HashAlgorithm = 'SHA256'
    KeyExportPolicy = 'NonExportable'
    KeyUsageProperty = 'All'
    KeyUsage = @('DigitalSignature', 'KeyEncipherment')
    TextExtension = @('2.5.29.19={critical}{text}ca=FALSE', '2.5.29.37={text}1.3.6.1.5.5.7.3.1')
    NotAfter = (Get-Date).AddDays(14)
    CertStoreLocation = 'Cert:\CurrentUser\My'
}
$leaf = New-SelfSignedCertificate @leafParams

$rootCer = Join-Path $evidence ("g01-root-" + $root.Thumbprint + ".cer")
$leafCer = Join-Path $evidence ("g01-localhost-" + $leaf.Thumbprint + ".cer")
Export-Certificate -Cert $root -FilePath $rootCer -Type CERT | Out-Null
Export-Certificate -Cert $leaf -FilePath $leafCer -Type CERT | Out-Null

$root.Thumbprint | Set-Content (Join-Path $evidence 'root-thumbprint.txt')
$leaf.Thumbprint | Set-Content (Join-Path $evidence 'leaf-thumbprint.txt')
certutil.exe -dump $rootCer
certutil.exe -dump $leafCer
~~~

人工核对：
- Root 的 Basic Constraints 为 CA，Key Usage 含 Certificate Signing / CRL Signing，path length 为 0。
- Leaf DNS SAN 精确包含 localhost；EKU 包含 Server Authentication（OID 1.3.6.1.5.5.7.3.1）；Issuer 对应本次 Root。
- Root 和 Leaf 都在 CurrentUser My，私钥不可导出。
- 导出的 CER 仅含公钥。不得导出 PFX 或任何 CA 私钥文件。

若扩展、Issuer、SAN、EKU、有效期或私钥属性不符合预期，停止，不要导入 Root。

## 5. 部署阶段的明确 Consent 与 CurrentUser Root 导入

在实际产品中，这一步必须由 AirBridge 安装 / 部署程序在部署流程中执行，不得推迟到 Gateway ON、Worker 启动或第一次 MCP 连接时。当前手册因正式安装器尚未实现，使用 PowerShell 显式模拟部署程序动作，以验证目标 Windows 用户上下文和 CurrentUser trust 行为。

向目标 Windows 用户展示 Root Subject、Thumbprint、NotBefore / NotAfter、导入目标（Cert:\CurrentUser\Root）、CA 私钥用途、证书可被本用户信任链使用的影响，以及“拒绝后 AirBridge 仍安装成功但 Managed HTTPS 不可用”的结果，取得明确同意。用户未明确同意时不得运行导入命令。

GitHub-hosted Windows Actions 的 CurrentUser Root 写入测试揭示了一个重要的交互约束：

- PowerShell `Import-Certificate` 在 hosted Runner 中立即报 `UI is not allowed in this operation`。
- 普通权限子进程中，`.NET X509Store.Add()` 和 `certutil -f -user -addstore Root` 都未返回；后者停在 `before-certutil-currentuser-root-addstore`，120 秒后被 wrapper 终止。
- 后续一次 CI-only `crypt32.dll` 探针也未在 120 秒内返回。它把 `CertOpenStore` 与 `CertAddEncodedCertificateToStore` 放在同一个包装方法内，最后阶段标记为 `before-cryptapi-currentuser-root-add`，所以不能判定究竟是哪一个 Win32 调用阻塞。其证据包同样只有公开 CER 和阶段标记，没有 Root 回读、链验证结果或成功导入 JSON；不能记为导入成功。随后曾提交拆分 native open / add 阶段的测试版本，但该运行在进入测试步骤前被新版本取消，没有得到结果。
- Microsoft 对 Windows 证书库的说明指出，Root 库受保护，用户界面会提醒用户只放置自己信任的根证书。CurrentUser Root 写入会触发 Windows 信任确认；无人值守的 GitHub-hosted Actions 执行无法由操作员完成该确认；子进程即使报告 `Environment.UserInteractive=True`，也没有人工批准 Windows 安全确认的操作员。参考：[Microsoft：Managing Certificates with Certificate Stores](https://learn.microsoft.com/en-us/windows/win32/seccrypto/managing-certificates-with-certificate-stores)。
- 因此，G-01A 的只读子测试不写入 CurrentUser Root，也不调用 certutil。G-01A 只执行 Store 范围快照、`X509ChainPolicy.CustomTrustStore` 正向 / 负向链验证和 SAN 夹具，不写入任一 Root store。另一个 G-01A2 子测试在临时普通用户 Profile 中调用 `CryptUIWizImport` 的 `NO_UI` 模式，实际验证 API 导入 CurrentUser Root、Thumbprint 回读、Windows 默认链及精确清理；它不验证交互向导或用户 Consent。
- 真实 CurrentUser Root 导入必须在目标 Windows 用户的交互式桌面部署流程内进行：AirBridge 先展示并取得应用层明确 Consent，再调用 Windows 信任导入；Windows 自身的根证书确认也必须由用户实际批准。用户拒绝 / 取消或无法显示交互确认时，Managed HTTPS 保持不可用，安装仍可继续。禁止通过注册表直接写入、隐藏/自动点击确认、LocalMachine Root 或 TLS bypass 绕过保护。

**最新 CI 证据（2026-11-20，run #44737654804，commit `588d911`）：** Windows job 为 SUCCESS。标准用户子进程 `runner_is_elevated_admin=false`；CustomTrustStore 正向链、未受信任 CA 拒绝、SAN 夹具均 PASS；CurrentUser Root / LocalMachine Root 前后数量均为 564 且 Thumbprint 集合无变化；临时证书清理 PASS。进程报告 `SessionId=2`、`Environment.UserInteractive=True`，但这两个标志不表示有人工操作员能批准 Windows 的根证书确认；Root 导入字段仍为 `NOT_RUN_INTERACTIVE_WINDOWS_CONSENT_REQUIRED`。因此 CI 子项仅证明只读范围和内存链验证，不能证明 CurrentUser Root 导入。

这几次 Actions 运行说明 hosted Runner 并不适合完成用户交互式 Root 授权；它仍可持续验证证书结构、链构建逻辑、账户隔离、只读信任范围和清理。下列 .NET X509Store 示例只有在交互式 Windows 用户会话中由实际部署 PoC 验证后，才能作为安装器候选，不是已确认产品实现：
导入目标必须明确指定为 `StoreLocation.CurrentUser` + `StoreName.Root`；禁止改成 LocalMachine。Consent 必须在调用该 API 之前由用户明确完成，不能把 API 调用本身当成 Consent。拒绝时不导入，但模拟 AirBridge 安装仍应继续完成；只将 Managed HTTPS / OAuth 标记为不可用，Gateway 保持默认 OFF，AirBridge Core / Runtime / 串口功能不受影响。若以后需要补做，必须再次进入明确的部署 Repair 流程并重新 Consent，不能通过 Gateway ON 静默导入。

## 6. 验证信任范围

~~~powershell
$rootThumb = (Get-Content (Join-Path $evidence 'root-thumbprint.txt')).Trim()
$leafThumb = (Get-Content (Join-Path $evidence 'leaf-thumbprint.txt')).Trim()

certutil.exe -user -store Root $rootThumb |
    Tee-Object -FilePath (Join-Path $evidence 'currentuser-root-test-ca.txt')

$lmRootAfter = @(Get-ChildItem 'Cert:\LocalMachine\Root' | ForEach-Object { $_.Thumbprint } | Sort-Object)
$lmRootAfter | Set-Content (Join-Path $evidence 'localmachine-root-after.txt')
$lmDelta = Compare-Object -ReferenceObject $lmRootBefore -DifferenceObject $lmRootAfter
if ($lmDelta) {
    $lmDelta | Format-Table -AutoSize
    throw 'LocalMachine Root changed. Stop G-01 and investigate.'
}

$leaf = Get-Item -LiteralPath ("Cert:\CurrentUser\My\" + $leafThumb)
$leaf | Format-List Subject, Issuer, Thumbprint, NotBefore, NotAfter, HasPrivateKey, DnsNameList, EnhancedKeyUsageList
~~~

要求：
- certutil -user -store Root <RootThumbprint> 能找到本次 Root。
- LocalMachine Root 导入前后 Thumbprint 清单完全一致；否则停止。
- Leaf 在 CurrentUser My 且 HasPrivateKey=True；Root 私钥也留在 CurrentUser My，未导出。
- certutil -dump Leaf CER 显示正确 DNS SAN / Server Authentication EKU。
- 若 LocalMachine Root 被修改，不得继续把本次 PoC 记为合格。

## 7. 客户端 TLS 与真实协议测试（按目标主机分别执行）

必须有一个与当前 MCP Client 位于**同一台 Windows 主机**的专用本机测试端点使用第 4 节 Leaf 证书，且只监听 127.0.0.1 / ::1。由于端点 hostname 是 localhost，客户端访问的就是客户端自己的机器；不能让另一台主机上的 MCP Client 用 localhost 访问本机 Gateway。**本手册不创建或启动监听服务。** 正式 Gateway Broker / Worker 尚未完成时，应使用经单独评审的可丢弃 PoC 服务，不得以公网服务器或其它机器替代。

D-06 已确认的默认端口：
- OAuth Broker：https://localhost:38861/
- Antigravity Worker：https://localhost:38862/mcp
- OpenCode Worker：https://localhost:38863/mcp

端点尚未提供这些端口时，记录实际端口及原因；不得擅自改动已确认的产品默认端口。每次启动服务都核对监听地址，不得出现通配或 LAN listener。

| 客户端形态 | TLS 测试 | OAuth / MCP 测试 | 独立记录 |
|---|---|---|---|
| Chrome | 本机 https://localhost:<port>/health 无证书警告 | 不替代 OAuth / MCP PASS | Chrome 版本、URL、截图 / 脱敏日志 |
| Antigravity App | 目标版本访问本机 OAuth discovery | Authenticate、MCP initialize、tools/list、只读 Tool | App 版本、安装形态 |
| Antigravity IDE | 与 App 分开验证 | OAuth、只读 MCP 调用，记录 CIMD / DCR 实际路径 | IDE 版本、CIMD 是否采用 |
| OpenCode Desktop | 与 CLI 分开验证 | OAuth discovery、授权、Resource 请求、只读 MCP 调用 | Desktop 版本、安装形态 |
| OpenCode CLI（如属首版支持范围） | 用 CLI 实际运行时单独验证 | CLI auth / MCP 只读调用 | CLI 版本、运行时、system-CA 配置 |

顺序：Chrome TLS smoke test；Antigravity App / IDE 分开测试；OpenCode Desktop；OpenCode CLI（如纳入支持）；最后完成真实 OAuth + MCP 调用。只打开 /health 或 metadata 页面不算协议验收。

若 OpenCode CLI 只有设置 NODE_USE_SYSTEM_CA=1 或运行时明确支持的等效系统 CA 选项后才成功，记录为该形态的支持前提，并评估是否纳入首版；不得把 CLI 结果外推到 Desktop。禁止 NODE_TLS_REJECT_UNAUTHORIZED=0。

## 8. 负向测试

每种失败场景都必须 fail-closed，不能忽略 TLS 错误、吞掉错误或降级到无认证 HTTP。

| 场景 | 操作 | 预期 |
|---|---|---|
| CA 信任移除 | 从 CurrentUser Root 移除本次精确 Root Thumbprint | Chrome / 目标客户端拒绝 TLS，OAuth / MCP 不可继续 |
| Leaf 过期 | 使用同一 CA 签发已过期 localhost Leaf | 客户端拒绝 |
| SAN 不匹配 | 使用 SAN 不含 localhost 的测试 Leaf，但访问 localhost URL | 客户端因 hostname mismatch 拒绝 |
| 链不完整 | 使用包含中间 CA 的测试链，服务端不发送必需中间证书 | 无法构建链时拒绝；记录客户端是否从缓存补链并清理缓存重测 |
| 信任恢复 | 重新导入本次 Root 公钥证书 | 仅在证书有效、SAN 正确、链完整时恢复连接 |

移除 CurrentUser Root 验证的是当前信任不存在，不等同于 OCSP / CRL 撤销验证。若要测试 OCSP / CRL，须另行设计带可达撤销端点的 PoC，不能把本表描述成 OCSP / CRL 已通过。

## 9. 清理与回滚

归档证据后核对两个 Thumbprint，确认它们是本次新建的唯一证书，再清理。不要按 Subject 通配删除。

~~~powershell
$rootThumb = (Get-Content (Join-Path $evidence 'root-thumbprint.txt')).Trim()
$leafThumb = (Get-Content (Join-Path $evidence 'leaf-thumbprint.txt')).Trim()

$rootCer = Join-Path $evidence ("g01-root-" + $rootThumb + ".cer")
$leafCer = Join-Path $evidence ("g01-localhost-" + $leafThumb + ".cer")
$rootTrustPath = "Cert:\CurrentUser\Root\$rootThumb"
$leafPath = "Cert:\CurrentUser\My\$leafThumb"
$rootMyPath = "Cert:\CurrentUser\My\$rootThumb"

if (Test-Path -LiteralPath $rootTrustPath) {
    Remove-Item -LiteralPath $rootTrustPath -WhatIf
    # 检查 WhatIf 输出后，再单独决定是否移除本次 Root 信任。
}
if (Test-Path -LiteralPath $leafPath) {
    Remove-Item -LiteralPath $leafPath -WhatIf
    # 检查输出后，再单独决定是否删除本次 Leaf。
}
if (Test-Path -LiteralPath $rootMyPath) {
    Remove-Item -LiteralPath $rootMyPath -WhatIf
    # 检查输出后，再单独决定是否删除本次 Root 私钥容器关联。
}
Remove-Item -LiteralPath $rootCer -WhatIf
Remove-Item -LiteralPath $leafCer -WhatIf
~~~

先用 -WhatIf 逐项核对，再由操作者决定实际删除。完成后确认：
- certutil -user -store Root <RootThumbprint> 不再找到本次 Root。
- CurrentUser My 中不再有本次 Root / Leaf。
- LocalMachine Root 前后清单仍完全一致。
- 测试端点进程已退出、无监听残留。
- 保留测试报告和脱敏日志；CER 在证据归档后按数据处理要求删除。

## 10. 证据记录模板

每个客户端形态分别填写，Desktop / CLI 不得合并：

| 字段 | 记录 |
|---|---|
| Windows edition / build、PowerShell 版本 | |
| 客户端产品 / 版本 / 安装形态 | |
| OpenCode CLI 运行时与 system-CA 配置（如适用） | |
| Root Subject / Thumbprint / 有效期 | |
| Leaf Subject / Thumbprint / SAN / EKU / 有效期 | |
| CurrentUser Root 查询结果 | |
| LocalMachine Root 前后清单比较 | |
| 服务监听地址 / 端口 | |
| HTTPS URL / Issuer / Resource URL | |
| TLS 正向结果 | PASS / FAIL / NOT RUN |
| OAuth discovery / Authenticate | PASS / FAIL / NOT RUN |
| MCP initialize / tools/list / 只读 Tool | PASS / FAIL / NOT RUN |
| 负向测试结果 | |
| 脱敏日志 / 截图路径及备注 | |

## 11. G-01 Go / No-Go（按部署主机分别判定）

**每台实际部署 M2.5 AirBridge 的目标主机，部署阶段 CA trust bootstrap 的 PASS 必须同时满足：**
- 用户明确同意导入；仅 CurrentUser 信任；LocalMachine Root 前后完全未变化。
- 所有首版支持的客户端形态接受有效 localhost TLS 链，并严格拒绝过期、SAN 不匹配、信任移除和不能构建的证书链。
- Antigravity App / IDE、OpenCode Desktop，以及首版如承诺支持的 OpenCode CLI，分别完成 OAuth discovery / Authenticate、MCP initialize、tools/list 和只读 Tool 调用。
- 没有 TLS bypass、关闭验证或无认证 HTTP 降级。
- 有可复核的版本、证书、URL、监听地址、测试日志和清理记录。

**NO-GO / 暂缓：**
- 必需客户端不识别 CurrentUser Root，或只有绕过 TLS 校验时才工作。
- 必须修改 LocalMachine Root、获取管理员权限或不安全降级才能连接。
- 客户端只打开 HTTPS 页面，未完成 OAuth / MCP，或 Agent / Resource Audience / Token 隔离未验证。
- 负向证书测试不能 fail-closed，或测试证据不足。

G-01 未通过时保留现有 stdio 兼容路径；Managed HTTPS 不上线。不得把部分 PASS 写成整体 PASS，也不得突破用户已确认的 CurrentUser-only 信任边界。

## 12. 公开技术参考

- Microsoft：[New-SelfSignedCertificate](https://learn.microsoft.com/en-us/powershell/module/pki/new-selfsignedcertificate?view=windowsserver2025-ps)（测试证书、SAN、EKU、Signer 和 CurrentUser My）。
- Microsoft：[Import-Certificate](https://learn.microsoft.com/en-us/powershell/module/pki/import-certificate?view=windowsserver2025-ps)（明确导入 Cert:\CurrentUser\Root）。
- Microsoft：[Manage Certificate Stores in Windows Server](https://learn.microsoft.com/en-us/windows-server/identity/ad-cs/inspect-certificates-manage-certificate-stores)（Current User / Local Computer store scope 和继承规则）。
- Microsoft：[Configure certificate authentication in ASP.NET Core](https://learn.microsoft.com/en-us/aspnet/core/security/authentication/certauth?view=aspnetcore-10.0)（测试 CA / 叶证书 PowerShell 示例；本文不采用其 LocalMachine 示例）。

---

**当前执行状态：NOT RUN。** 本文尚未在任何目标 Windows 主机创建或导入证书、启动监听服务、运行客户端测试或清理证书。实际产品必须在每台安装部署期间执行 CA trust bootstrap；本手册仅模拟部署动作。PoC 结果必须注明具体主机标识和目标 Windows 用户上下文；如 A / B 都部署 M2.5，则分别验证其独立部署流程。


## 13. G-01B：CryptUIWizImport 交互式 CurrentUser Root 导入 PoC

仓库提供一次性测试脚本：

`.github/scripts/g01-interactive-currentuser-import.ps1`

该脚本用于验证**当前 Windows 用户的交互式会话能否通过 Windows 原生导入向导完成 CurrentUser Root 导入和系统默认证书链验证**。它生成短期、一次性的测试 CA / localhost Leaf；不是正式 AirBridge CA 供应方案，也不验证安装器、Antigravity、OpenCode 或 OAuth / MCP。核心 API 为 CryptUIWizImport，P/Invoke 包装位于 .github/scripts/g01-cryptui-native.ps1。

### 执行前提

- 在本次要验证的实际 Windows 主机上，以将运行 AirBridge / MCP Client 的目标 Windows 用户登录。
- 打开可见、非提升（非管理员运行）的 PowerShell；不得从服务、计划任务、无交互会话或 GitHub-hosted Runner 执行。
- 确认该账户对本机 CurrentUser`My` 和 CurrentUser`Root` 有正常用户级访问权限。
- 如果 AirBridge GUI 与 MCP Client 使用不同 Windows 账户，必须对实际 MCP Client 账户单独执行；本脚本不能证明其它账户继承了该信任。
- 运行前关闭其它正在修改证书库的安装 / 管理操作，避免并发变化影响前后快照。

### 执行步骤

从已检出的 PR 分支根目录运行：

~~~powershell
.\.github\scripts\g01-interactive-currentuser-import.ps1
~~~

脚本会先检查交互式会话、Session ID 和非提升身份，然后在 CurrentUser`My` 创建一次性 CA / Leaf，并导出**仅含公钥**的 Root CER。随后显示 Root Subject、完整 Thumbprint 和有效期。

1. 仔细核对 Subject、Thumbprint、有效期和用途。
2. 只有明确同意信任这个一次性测试 Root，才输入完整 Thumbprint。输入不匹配或直接取消时，不执行 Root 导入。
3. 脚本先用 CertOpenStore 打开 CERT_STORE_PROV_SYSTEM_W + CERT_SYSTEM_STORE_CURRENT_USER + Root 句柄，再调用 CryptUIWizImport。生产交互模式设置 ALLOW_CERT (0x00020000) 和 NO_CHANGE_DEST_STORE (0x00010000)，不设置 NO_UI (0x00000001)，因此显示 Windows 原生导入向导；目标 Store 由传入的句柄固定，不能切换到 LocalMachine。用户必须亲自核对并完成 Windows 向导；不得隐藏、自动点击或绕过系统确认。
4. 导入后，脚本按精确 Thumbprint 回读 CurrentUser Root，并用 Windows 默认 X509Chain 构建器验证 localhost Leaf 链。
5. CryptUIWizImport 成功返回表示原生向导完成导入；最终 PASS 还要求精确 Thumbprint 回读和 Windows 默认链验证成功。不得把仅运行浏览器或 CustomTrustStore 自定义链验证记为系统 Root 验收。
6. 脚本在 finally 阶段只按本次唯一 Thumbprint 删除本次导入的测试 Root、Leaf 和本次 Root 私钥关联，并比较 CurrentUser Root 回到基线、LocalMachine Root 完全不变。输出 `g01b-result.json` 作为证据。

若用户拒绝应用层 Consent、取消 Windows 导入向导、API 调用失败、导入没有回读、链验证失败或清理不完整，脚本返回非零，不能记为 PASS。若脚本意外中断，必须先检查 JSON / 当前证书库快照，再由操作者按本次记录的 Thumbprint 决定如何清理；不得按 Subject 通配删除证书。

### G-01B 的结论边界

- G-01B PASS 只证明：当前被测 Windows 用户上下文中，交互式导入和 Windows 默认链验证可行，且本次测试没有修改 LocalMachine Root、清理后 CurrentUser Root 恢复原状。
- G-01B 不证明生产安装器的 CA 生成 / 交付、升级 / Repair、Antigravity 或 OpenCode 的 TLS 信任行为，也不证明真实 OAuth / MCP 连通。
- G-01B 测试的 CA 是短期 disposable CA，不能把其私钥、CER 或 Thumbprint 当作生产部署材料。
- G-01A Windows Actions 验证非交互 Store-scope / CustomTrustStore 链；新增 G-01A2 在一次性标准用户 Profile 内使用同一个 CryptUIWizImport API 的 NO_UI 模式，验证 CurrentUser Root 目标句柄、导入回读、Windows 默认链和清理；G-01B 则在真实目标用户桌面运行同一 API 的交互模式。G-01A2 不验证交互 UI，也不代表用户授权。G-01 总体仍需完成所有目标 MCP 客户端 TLS + OAuth + MCP 正向和负向测试。

**当前执行状态：G-01B NOT RUN。** GitHub Actions 会在一次性标准用户 Profile 中执行 G-01A2 的 NO_UI API test，但不会执行交互脚本或显示 Windows 导入向导。

### 13.5 G-01A2：GitHub Actions 的无 UI API 验证

Hosted Actions 无法由操作员完成 Windows 原生证书导入向导，因此 CI 不运行 G-01B。CI 会创建一次性普通用户及独立 Profile，在该隔离 Profile 中调用同一个 CryptUIWizImport API，但仅在 CI 模式设置 CRYPTUI_WIZ_NO_UI，并把目的 Store 改为 CurrentUser\\My 做 API control test。该路径不尝试修改 CurrentUser\\Root。验证项目包括：

- P/Invoke 对 crypt32.dll / cryptui.dll 的声明能加载；
- CRYPTUI_WIZ_IMPORT_SRC_INFO 结构体可正确构造；
- CertOpenStore 打开 CERT_SYSTEM_STORE_CURRENT_USER 下的 My Store；
- ALLOW_CERT + NO_CHANGE_DEST_STORE + NO_UI 调用成功，并从 CurrentUser My 按 Thumbprint 回读测试证书；
- LocalMachine Root 前后 Thumbprint 集合不变；
- CurrentUser Root 与 LocalMachine Root 前后 Thumbprint 集合不变；清理后 CurrentUser My 精确临时测试证书已删除；
- 最后按 SID 删除整个 disposable Profile。

该 CI 只验证 API / 结构体 / 句柄 / flags 在 CurrentUser My 控制场景下的导入与清理路径。

**已完成的 CI 证据（2026-11-20）：** [run #44744616225](https://github.com/scanligithub/airbridge/actions/runs/44744616225) SUCCESS。一次性非管理员用户 Profile 中 API 返回、CurrentUser My Thumbprint 回读、CurrentUser Root / LocalMachine Root 不变、清理全部 PASS。该结果证明 CryptUIWizImport 的 API/P/Invoke 控制调用可工作，但不证明 Root 导入或图形 UI。

**Root 对照结果：** [run #44743044286](https://github.com/scanligithub/airbridge/actions/runs/44743044286) 和 [run #44743660093](https://github.com/scanligithub/airbridge/actions/runs/44743660093) 以 CurrentUser Root 为目的 Store 时，native API 调用内 120 秒超时；第二轮明确记录了调用前 marker、无调用后 marker。两次都未改变 CurrentUser Root / LocalMachine Root，临时 Profile 清理完成。将这一点标记为 hosted-runner Root no-UI 操作的限制/失败，而不是生产交互向导失败。
此前将同一 NO_UI API 指向 CurrentUser Root 的 hosted Actions 测试在 native call 内 120 秒超时，阶段标记证明未返回；这不能说明目标机交互向导失败，但也不能算 Root 导入成功。NO_UI 模式不显示 Windows 向导，不证明用户授权，也不能代替 G-01B。