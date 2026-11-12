# Capture Dbo API：Tcl 示例索引与验证基线 v1.0

- 文档编号：AB-CAPTURE-DBO-001
- 版本：1.0
- 日期：2026-11-12
- 对应项目：AirBridge / Capture 17.2
- 状态：**源码整理完成；部分接口已有 Capture 17.2 实测证据；Start Page 状态下 GetActiveDesign 的实际返回仍待单项验证**
- 主要来源：
  - 资料库：/orcad_tclscripts/tclscripts.zip
  - 资料库：OrCAD_Capture_TclTk_Extensions.pdf
  - 项目文档：Capture_Tcl_Bridge_实测基线_v1.0.md

> 本文是基于现有 Tcl 示例的调用索引和验证指导，不是 OrCAD API 的完整参考手册。文中的 API 名称及示例调用来自现有资料；不同 Capture/SPB 版本的实际行为仍以目标环境为准。不能仅凭 Tcl 命令已注册，就推断其参数和行为均已通过实测。

---

## 1. 整理目标与来源范围

本次整理不是把 4,393 个归档条目逐个复制到 AirBridge，而是筛出后续 AirBridge / Capture Skill 最常用、最值得复用的 Dbo API 模式：

1. 获取 Capture 当前 Dbo Session；
2. 获取当前活动 Design；
3. 枚举当前 Session 已打开的 Design 和 Library；
4. 读取 Design、Schematic、Page 等对象名称；
5. 遍历 Schematic / Page / Part Instance；
6. 读取对象属性；
7. 正确释放迭代器和 DboState；
8. 区分只读调用与可能修改工程、Library 或保存状态的调用。

### 1.1 资料归档信息

| 项目 | 值 |
|---|---|
| 资料库归档 | /orcad_tclscripts/tclscripts.zip |
| 归档大小 | 57,210,072 bytes |
| ZIP 条目数 | 4,393 |
| ZIP 完整性 | Python zipfile.testzip() 未发现损坏条目 |
| SHA-256 | 21e614ee5ab3946d59689754a0e59d20bab0245cf4c5a4ffcbbc02cf5a48c905 |

扫描结果：有 157 个 Tcl 文件包含 Dbo 类名或 Dbo API / Helper 相关引用；其中 28 个 Tcl 文件直接引用当前 Session 全局变量或 Session 相关入口。属性读取/写入模式分布在多个示例中，不能把所有命中脚本都视为安全只读脚本。

源码路径均相对于 ZIP 内部的 tclscripts/ 目录。本文列出的行号对应本次归档中的文件版本。

### 1.2 官方 API 说明文档

资料库中的 OrCAD_Capture_TclTk_Extensions.pdf 提供了以下直接相关内容：

- 第 28 页：Tcl 字符串与 CString 转换、DboTclHelper 命令；
- 第 28–29 页：获取当前 Capture Session、创建新 Session、按路径获取 Design、枚举已打开 Design；
- 第 63–64 页：capSessionUtil 示例，枚举当前 Session 中已打开的 Design 和 Library；
- 第 174–177 页：DboSession 类方法及签名，包括 GetDesign、GetDesignAndSchematics、GetLib、GetActiveDesign、NewDesignsIter、NewLibsIter。

该手册的示例章节包含 16.3 时代的脚本说明。它可以作为类和方法签名的文档依据，但不能代替 Capture 17.2 实测。

---

## 2. 核心 API 约定

### 2.1 获取当前 Capture Session

当前 Capture 进程已经建立的 Dbo Session：

~~~tcl
set lSession $::DboSession_s_pDboSession
DboSession -this $lSession
~~~

这是官方示例及多个 Capture 内置工具反复使用的方式。

注意区分：

- ::DboSession_s_pDboSession：当前 Capture Session 的全局指针；
- DboSession -this $lSession：将 Tcl DboSession wrapper 绑定到指定 Session；
- DboTclHelper_sCreateSession：创建一个新的 Dbo Session。

本项目探测当前交互式 Capture 状态时，应优先使用当前 Session 全局变量，不应为了读取当前工程而擅自调用 DboTclHelper_sCreateSession 创建另一个 Session。

新建 Session 的示例通常配合 GetDesignAndSchematics 等路径访问操作使用。这与“读取当前 GUI 正在使用的 Capture Session”不是同一件事。

### 2.2 获取当前活动 Design

官方 API 文档列出的接口：

| 项目 | 定义 |
|---|---|
| 类 | DboSession |
| 方法 | GetActiveDesign() |
| 返回类型 | DboDesign |
| 参数 | 无 |

Tcl 示例：

~~~tcl
set lNullObj NULL
set lDesign [$lSession GetActiveDesign]

if {$lDesign == $lNullObj} {
    # 当前没有活动 Design
}
~~~

多个官方脚本直接使用字符串 NULL 判断 Dbo 指针：

- capCustomSamples/capCommServerMethods.tcl
- capDB/capExportDesignCache.tcl
- capDB/capShortNet.tcl
- capUtils/capChangeName.tcl
- capUtils/capSyncPropPCBFootprint.tcl
- capXMLImportExport/tcl/OrHandlerXMLImportExport.tcl

不要在没有实测依据时，把空对象判断扩大为空字符串、0、0x0 等多个猜测值。API 示例中的 NULL 是目前已有直接源码依据的判断形式。Start Page 下的真实返回仍需单独记录。

### 2.3 CString 输入/输出转换

Dbo API 大量使用 CString，而不是直接接收普通 Tcl 字符串。

读取对象名称的典型方式：

~~~tcl
set lName [DboTclHelper_sMakeCString]
$lDesign GetName $lName
set lNameStr [DboTclHelper_sGetConstCharPtr $lName]
~~~

含义：

- DboTclHelper_sMakeCString：创建 CString，可传入初始 Tcl 字符串，也可创建空 CString 作为输出参数；
- $object GetName $cstring：将对象名称写入 CString；
- DboTclHelper_sGetConstCharPtr：将 CString 转换为 Tcl 字符串。

官方 API 说明第 28 页明确展示了相同转换模式。

### 2.4 DboState 和迭代器

常见创建方式：

~~~tcl
set lStatus [DboState]
set lIter [$lSession NewDesignsIter $lStatus]
set lDesign [$lIter NextDesign $lStatus]
~~~

典型迭代模式是：

1. 创建 DboState；
2. 创建对应迭代器；
3. 用 NextXxx 取得第一个对象；
4. 对象不为 NULL 时继续循环；
5. 每轮调用 NextXxx；
6. 用该迭代器类型对应的 delete 命令释放迭代器；
7. 按示例释放 DboState。

例如：

| 迭代器 | 对应释放命令 |
|---|---|
| DboSessionDesignsIter | delete_DboSessionDesignsIter |
| DboSessionLibsIter | delete_DboSessionLibsIter |
| DboEffectivePropsIter | delete_DboEffectivePropsIter |
| DboSchematicPagesIter | delete_DboSchematicPagesIter |
| DboPagePartInstsIter | delete_DboPagePartInstsIter |
| DboLibViewsIter | delete_DboLibViewsIter |

不要根据类名自行猜测释放函数；使用具体示例中出现的成对构造/释放方式。对同一个指针，不要重复调用多个未经确认的析构方式。

---

## 3. DboSession：方法与副作用分类

以下签名依据 OrCAD_Capture_TclTk_Extensions.pdf 第 174–177 页，以及归档示例。

| 方法 / 模式 | 手册中的作用 | 本项目处理原则 |
|---|---|---|
| GetActiveDesign() | 返回当前活动 DboDesign | 只读探测，优先验证 |
| NewDesignsIter(status) | 创建已打开 Design 的迭代器 | 只读枚举，优先验证 |
| NewLibsIter(status) | 创建已打开 Library 的迭代器 | 只读枚举，优先验证 |
| GetDesign(Name,status) | 按名称获取 Design | 可能触发设计加载；不放入 Start Page 探测 |
| GetDesignAndSchematics(Name,status) | 获取 Design 及 Schematics | 可能触发设计加载；不放入只读 Session 探测 |
| GetOpenDesign(Name,status) | 获取已打开 Design | 后续可作为按名称查询方式研究 |
| GetLib(Name,status) | 按名称获取 Library | 可能打开/加载 Library；不放入当前 Session 只读探测 |
| GetOpenLib(Name,status) | 获取已打开 Library | 后续可研究，优先于可能打开 Library 的接口 |
| SaveDesign / SaveDesignAs | 保存 Design | 写入磁盘，未经单独批准不调用 |
| SaveLib / SaveLibAs | 保存 Library | 写入磁盘，未经单独批准不调用 |
| RemoveDesign / RemoveLib | 从 Session 移除对象 | 改变 Session 状态，不调用 |
| MarkAllLibForSave | 标记 Library 保存状态 | 改变保存状态，不调用 |

以上分类是本项目的安全执行约束。即使某方法从命名上看像查询，也必须先核实其参数、打开/加载语义和副作用，不能仅凭方法名推断为只读。

---

## 4. 相关 Tcl 示例索引

### 4.1 当前 Session：枚举已打开的 Design 和 Library

**首选基础示例：**

tclscripts/capUtils/capSessionUtil.tcl

关键位置：

| 行 | 内容 |
|---|---|
| 15–16 | 获取当前 Session 并绑定 DboSession wrapper |
| 18 | 创建 DboState |
| 24–32 | NewDesignsIter / NextDesign / NULL 判断 / 释放 Design 迭代器 |
| 37–45 | NewLibsIter / NextLib / NULL 判断 / 释放 Library 迭代器 |
| 47 | 释放 DboState |

它是当前 Session 只读枚举最简洁、最值得复用的脚本。

另一个实现：

tclscripts/capOpenLibDesignList/tcl/capSessionUtil.tcl

关键位置：

- 15–16：获取当前 Session；
- 31–47：枚举 Design，并读取名称；
- 51–67：枚举 Library，并读取名称；
- 69：释放 DboState。

区别：该脚本还把枚举结果组织为 JSON 结构，适合借鉴“把 Dbo 对象信息转换成上层结构化数据”的方法。

**注意：** 两份脚本枚举的是当前 Session 中已打开的对象，不等同于扫描磁盘目录中的所有 DSN/OLB 文件。

### 4.2 Active Design：活动对象获取与 NULL 判断

相关示例：

| 文件 | 行 | 价值 |
|---|---:|---|
| tclscripts/capCustomSamples/capCommServerMethods.tcl | 182–192 | 当前 Session、GetActiveDesign、NULL 判断 |
| tclscripts/capDB/capExportDesignCache.tcl | 23–31 | 活动 Design 为空时记录“No Design active”并返回 |
| tclscripts/capDB/capShortNet.tcl | 177–184 | 获取活动 Design，空对象时停止后续遍历 |
| tclscripts/capUtils/capChangeName.tcl | 70–73 | 获取活动 Design 并处理空对象 |
| tclscripts/capUtils/capSyncPropPCBFootprint.tcl | 163–168 | 活动 Design 为空时报告错误 |
| tclscripts/capXMLImportExport/tcl/OrHandlerXMLImportExport.tcl | 81–89 | 读取活动 Design 名称并转为普通 Tcl 字符串 |

这些示例都说明调用方必须对“当前没有活动 Design”作保护处理。它们证明了代码存在 NULL 分支，但并不能单独证明 Start Page 下 API 的具体返回值或是否抛出异常。

### 4.3 Design / Schematic / Page 遍历

tclscripts/capDB/capShortNet.tcl

关键位置：

| 行 | 调用 | 用途 |
|---|---|---|
| 162–174 | Design.NewViewsIter / NextView | 枚举 Design 的 Schematic Views |
| 143–157 | Schematic.NewPagesIter / NextPage | 枚举 Schematic 下的 Pages |
| 120–139 | Page 遍历 Net | 读取页面网络对象 |
| 77–89 | Wire.NewAliasesIter / NextAlias | 读取 Wire 的 Alias |

该脚本用于短路网络检查，主要提供对象遍历模式，遍历和日志操作本身不等同于修改原理图。

tclscripts/capDB/capDesignUtil.tcl

该文件包含大量可复用的对象遍历示例：

- 561–625：遍历 Schematic 的 Pages；
- 641–705：遍历 Design 的 Schematic Views；
- 722 起：取得当前 Session / Design 并调用相关处理过程；
- 841 起：继续遍历 Page / Schematic 对象。

但该文件同时包含替换文字、替换 Alias 等操作。**只能提取其中明确的只读遍历片段，不得把整个工具当作只读探针运行。**

### 4.4 Page / Part Instance 遍历

tclscripts/capCustomSamples/capCommServerMethods.tcl

关键位置：

- 162–171：Page.NewPartInstsIter / NextPartInst，遍历 Part Instance；
- 72–98：读取对象的 Effective Properties；
- 182–192：获取活动 Design。

这个样例文件同时含有属性写入过程（第 60 行附近的 SetEffectivePropStringValue）。复用时必须只调用读取过程，不能将整个文件的所有 procedure 当成无副作用操作。

### 4.5 对象 Effective Properties 读取

优先参考：

tclscripts/capCustomSamples/capCommServerMethods.tcl 第 72–98 行。

主要调用：

~~~tcl
set lStatus [DboState]
set pPropsIter [$pObject NewEffectivePropsIter $lStatus]

set lPropName [DboTclHelper_sMakeCString]
set lPropValue [DboTclHelper_sMakeCString]
set lPropType [DboTclHelper_sMakeDboValueType]
set lEditable [DboTclHelper_sMakeInt]

set lStatus [$pPropsIter NextEffectiveProp \
    $lPropName $lPropValue $lPropType $lEditable]

while {[$lStatus OK] == 1} {
    set lPropNameStr [DboTclHelper_sGetConstCharPtr $lPropName]
    set lPropValueStr [DboTclHelper_sGetConstCharPtr $lPropValue]

    # 只收集名称、值等读取结果
    set lStatus [$pPropsIter NextEffectiveProp \
        $lPropName $lPropValue $lPropType $lEditable]
}

delete_DboEffectivePropsIter $pPropsIter
$lStatus -delete
~~~

该片段展示了属性迭代器和输出参数的基本模式。对正式验证脚本，还应检查 DboState 生命周期和错误路径，保证任何提前退出都能释放已创建的迭代器。

另一个参考：

tclscripts/capAnnotateHBlockPage/tcl/capAnnotateHBlockPageNumber.tcl

- 76–100：读取 Page TitleBlock 的 Page Number 属性；
- 104–115：遍历 Pages；
- 157–160：读取 Part Reference 属性。

但该脚本后续会写入 Page Number 属性、标记并保存 Design（约 169–211 行），因此只能摘取前面的读取片段，不得作为完整只读验证脚本执行。

### 4.6 Library / Part 属性

tclscripts/capDB/capLibPropUtil.tcl

可参考：

- 61–64：GetEffectivePropStringValue 和 SetEffectivePropStringValue 对照；
- 90–116：当前 Session、GetLib、遍历 Parts / Symbols，并标记 Library 保存；
- 174–199、290–318：其他相近操作。

该文件是典型的读写混合示例，包含属性修改和 MarkAllLibForSave。当前阶段不执行它。未来确实需要改 OLB 时，应另立写入测试，使用可丢弃的测试 Library。

---

## 5. 现有 Capture 17.2 实测证据：避免重复验证

项目已有文档 Capture_Tcl_Bridge_实测基线_v1.0.md 记录了 2026-11-12 主机 B 的实测结果。

已确认：

1. Capture 17.2 / Tcl 8.6.0 中，正式 Tcl Bridge 可以连续处理多条请求；
2. Capture Session 中 Tcl 变量、namespace 和 proc 可以跨独立请求保留；
3. 工程打开后，Dbo Session 的 GetActiveDesign 与 Project Manager 的 GetActivePMDesign 返回同一活动 Design 对象；
4. OPage、GetActivePage、DboPage.GetName 已在打开工程和激活页面的上下文中实测；
5. Design 下的 Schematic / Page 遍历已经在真实工程中得到 34 个页面；
6. DboSession_* 与 DboDesign_* 命令名称已在 Capture 17.2 Tcl Interpreter 中枚举并记录。

因此，下列项目不应重复作为“新接口发现”：

- 再次枚举已注册的 DboSession / DboDesign 命令名称；
- 再次证明 Capture Tcl Interpreter 能跨请求保存变量；
- 再次证明打开工程后 GetActiveDesign 与 GetActivePMDesign 指向同一个活动对象；
- 再次从同一工程遍历已经验证过的页面列表。

但上述历史证据不包含 Start Page 下 GetActiveDesign 的实际返回，也不表示所有已注册 Dbo 方法都已调用验证。

---

## 6. 本项目应该优先验证什么

采用“最少测试、覆盖关键未知行为”的原则。所有探测通过现有 AirBridge EDA HTTP API 发送 Tcl；只在明确需要真实运行时证据时才进行主机 B 实测。

### DBO-V01：Start Page 下 GetActiveDesign 的实际返回

<badge>实机 PASS · 2026-11-12</badge>

**优先级：P0 / 已完成。**

前置条件：Capture 17.2 已启动，处于 Start Page，没有打开 DSN/OPJ；不传 target_file。

只探测：

- Tcl 进程 PID / Tcl 版本；
- ::DboSession_s_pDboSession 是否存在；
- Session 指针是否非空；
- DboSession -this 是否成功；
- DboSession_GetActiveDesign wrapper 命令是否注册（仅记录，不作为最终判定）；
- $lSession GetActiveDesign 是成功返回 NULL、返回非空对象，还是抛出异常。

不打开工程、不调用 GetDesign / GetDesignAndSchematics、不保存、不关闭、不修改对象。

结果判读：

| 结果 | 解释 |
|---|---|
| Session 存在；wrapper 成功；GetActiveDesign 返回 NULL | 当前 Session 正常，但 Start Page 没有活动 Design |
| GetActiveDesign 返回非 NULL | 需要记录指针及后续只读对象身份检查，不能立即认定它是有效工程 |
| GetActiveDesign 抛出异常 | 保存完整 errorInfo/errorCode，分析当前 Session 生命周期或 API 上下文 |
| Session 全局变量不存在或为空 | 说明 Start Page 初始化时机可能早于 Dbo Session 全局变量就绪，不能继续调用 |

此项只有实际在 Capture 17.2 Start Page 状态下运行，才可以标记为实测。


### DBO-V01 实机记录（2026-11-12）

**结论：PASS。** Capture 17.2 启动后停留在 Start Page，未打开 DSN / OPJ；通过主机 A 的 AirBridge HTTP API 发起 Tcl 只读探测，未传 `target_file`。

| 字段 | 实测值 | 结论 |
|---|---|---|
| Request ID | `03eaf572-db9d-40ec-a654-9044e0fe2bab` | 完整请求记录 |
| Session ID | `52c6e80b-d4cf-47aa-9143-f5e36815c152` | Runtime Session |
| Capture PID | `3776` | 与 Tcl `pid` 一致 |
| Tcl 版本 | `8.6` | Capture Interpreter |
| request state | `completed` | 请求完成 |
| result.success | `true` | Adapter 执行成功 |
| returncode | `0` | Tcl 执行无错误 |
| duration_ms | `11` | Tcl 执行耗时 |
| session_variable_exists | `1` | 当前 Session 全局变量存在 |
| session_pointer_nonempty | `1` | Session 指针非空 |
| session_wrapper_rc | `0` | `DboSession -this` 成功 |
| active_design_method_registered | `1` | 相关 wrapper 命令已注册；仅作诊断信息 |
| active_design_rc | `0` | `$lSession GetActiveDesign` 成功 |
| active_design_value | `NULL` | 无活动 Design |
| active_design_is_null | `1` | 与官方示例的 NULL 判断一致 |
| stderr | 空 | 无错误输出 |

**正式结论：** 在被测 Capture 17.2 / Tcl 8.6 环境中，Start Page 状态下 Dbo Session 已初始化且可正常绑定；调用 `GetActiveDesign` 不需要打开工程，正常返回 `NULL`，而不是抛出异常。此行为可作为 AirBridge 判断“Capture 已启动但当前没有活动 Design”的只读状态探针。

本次没有打开工程、调用路径加载接口、修改对象、保存或关闭 Capture。该结果只证明本次 17.2 实测环境，不自动推广到其他 Capture/SPB 版本。

### DBO-V02：当前 Session 的 Open Design / Open Library 枚举

**优先级：P1 / 当前下一条验证。**

**第一阶段前置条件：** Capture 17.2 保持 Start Page 状态，没有打开 DSN / OPJ；不传 target_file。先枚举当前 Session 中实际已打开的 Design / Library，不预设两类对象都为空（尤其不预设 Library 数量）。

可复用脚本：[`scripts/capture_dbo_session_iterators_probe.tcl`](../scripts/capture_dbo_session_iterators_probe.tcl)。

只调用已有示例明确使用的接口：

- NewDesignsIter / NextDesign；
- NewLibsIter / NextLib；
- 对返回对象使用 GetName；
- 正常释放两个迭代器和 DboState。

目标：确认迭代器创建是否成功、NextDesign / NextLib 的结束标记、实际名称列表以及资源释放结果。该探测只枚举当前 Session 已打开的对象，不打开 DSN/OPJ，也不主动加载 OLB。

**第二阶段（如有必要）：** 用户预先打开一个测试工程和/或 Library 后，再验证非空集合的对象名称读取。只有第一阶段实际出现空集合或未覆盖非空路径时，才安排第二阶段。

不调用 GetDesign、GetDesignAndSchematics、GetLib、SaveDesign、RemoveDesign、MarkAllLibForSave。

### DBO-V03：Design 的只读名称和基础对象关系

**优先级：P1。**

前置条件：Capture 中已经打开专用测试 OPJ / DSN，由用户事先打开。

验证内容：

1. GetActiveDesign 返回活动 Design；
2. 通过 CString 输出参数读取 $lDesign GetName；
3. 可将读取结果与已验证的 PM Design 名称对照；
4. 不触发 Design 打开、关闭、保存操作。

此前已验证 PM / Dbo Design 对象相同，因而本项重点是将 Design 名称读取方式和状态返回纳入 API 字典，不重复做对象身份探索。

### DBO-V04：Part Instance Effective Properties 只读读取

**优先级：P2。**

在专用测试工程已打开的情况下：

1. 通过 Design / Schematic / Page 迭代器找到一个已有 Part Instance；
2. 使用 NewEffectivePropsIter / NextEffectiveProp 读取属性名称、值、类型和 editable 标志；
3. 将结果作为结构化报告返回；
4. 验证正常结束和异常结束时迭代器、DboState 的释放。

不调用 SetEffectivePropStringValue、SetDisplayProp、MarkModified、MarkAllLibForSave 或 SaveDesign。

这项验证可为未来读取器、原理图审计、属性检查和 AI 生成脚本提供可复用基础。

### 当前不验证的功能

- GetDesignAndSchematics：可能根据文件路径获取 / 加载设计，不适合 Start Page 只读探测；
- GetLib：可能加载 Library，先不测试；
- SetEffectivePropStringValue / SetDisplayProp：会修改对象；
- MarkModified / MarkAllLibForSave：会影响修改/保存状态；
- SaveDesign / SaveLib：写入磁盘；
- RemoveDesign / RemoveLib：改变 Session；
- 任何删除对象、写网表、批量修改或关闭 Capture 的接口。

---

## 7. 验证记录规则

每条 Dbo API 记录必须区分以下证据等级：

| 标签 | 含义 |
|---|---|
| DOC | 官方 TclTk API 手册存在对应方法和签名 |
| SAMPLE | 资料库 Tcl 示例实际使用过该调用 |
| REGISTERED | 目标 Capture Tcl Interpreter 中命令已注册 |
| RUNTIME | 已在 Capture 17.2 中执行过该方法并记录真实返回 |
| SAFE-READ | 当前已确认的调用路径不改变设计、Library 或保存状态 |
| WRITE | 会修改对象、工程、Library 或保存状态，需单独批准 |

一条 API 可以同时有多个标签。例如 GetActiveDesign 目前有 DOC / SAMPLE / REGISTERED；打开工程状态下有 RUNTIME；但 Start Page 状态仍需补充 RUNTIME 证据。

不要将 DOC、SAMPLE 或 REGISTERED 自动提升为 RUNTIME。

每次运行至少保存：

- Capture 版本、Tcl 版本和 PID；
- Capture 当前状态（Start Page / 工程已打开 / 页面已激活）；
- 请求 ID 和完整 HTTP 结果；
- Tcl 标准结果、catch 返回码、errorInfo、errorCode；
- 实际调用的方法和输入；
- 是否执行了对象修改或磁盘写入（只读探测应为否）。

---

## 8. 源码与参考文档索引

### 主要 Tcl 示例

| 归档路径 | 本文使用的关键位置 | 功能 |
|---|---:|---|
| capUtils/capSessionUtil.tcl | 15–47 | 当前 Session、Design / Library 枚举 |
| capOpenLibDesignList/tcl/capSessionUtil.tcl | 15–69 | 当前 Session 枚举并生成 JSON |
| capCustomSamples/capCommServerMethods.tcl | 72–98 | Effective Properties 读取 |
| capCustomSamples/capCommServerMethods.tcl | 162–171 | Part Instance 枚举 |
| capCustomSamples/capCommServerMethods.tcl | 182–192 | GetActiveDesign 与 NULL 判断 |
| capDB/capExportDesignCache.tcl | 23–31 | GetActiveDesign 与无活动 Design 分支 |
| capDB/capShortNet.tcl | 143–184 | Design / Schematic / Page / Net 遍历 |
| capDB/capDesignUtil.tcl | 561–705 | Schematic / Page 遍历范例（混合读写脚本） |
| capDB/capLibPropUtil.tcl | 57–74 | Library Part 属性读写对照（写入脚本） |
| capAnnotateHBlockPage/tcl/capAnnotateHBlockPageNumber.tcl | 76–115 | Page / TitleBlock 属性读取（后续含写入和保存） |
| capXMLImportExport/tcl/OrHandlerXMLImportExport.tcl | 81–89 | 从 Active Design 读取名称 |

### AirBridge 项目文档

- doc/Capture_Tcl_Bridge_实测基线_v1.0.md：Capture 17.2 实机验证的现状基线；
- doc/Capture_Session_E2E_验收工具_v1.0.md：主机 A 经 EDA HTTP API 执行 Tcl 的 E2E 工具；
- doc/Runtime_Adapter_Profile_v1.0.md：Adapter / Profile 接口边界；
- doc/README.md：本项目文档索引。

---

## 9. 下一步执行顺序

1. DBO-V01：Start Page 下 GetActiveDesign 返回行为（**2026-11-12 实机 PASS**）；
2. 当前进入 DBO-V02：验证 Start Page 下空 Design / Library 集合的迭代器创建、NULL 终止和资源释放；
3. 在用户手动打开的测试工程中完成 DBO-V03：读取 Design 名称；
4. 最后再决定是否开展 DBO-V04：读取 Part Instance Effective Properties。

前两项都不需要打开工程；DBO-V03 / V04 需要用户预先打开测试工程，但探测脚本本身不传 target_file，不负责打开工程。

**禁止为了验证只读 API 而自动调用任何打开、修改、保存、移除或关闭工程的接口。**
