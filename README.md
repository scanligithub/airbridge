# AirBridge

通用串口数据传输通道（ABFP）v2.5

## 核心定位

AirBridge 负责在主机 A 与主机 B 之间可靠传输数据，不理解或执行传输内容。

- 串口全双工通信与帧校验
- 通用原始数据传输（RAW_STREAM）
- 双向大文件传输（滑动窗口、分片确认与重传）
- 远程文件系统（RFS）
- 可选远程 Shell（独立通用模块）

**AirBridge 不集成 Cadence 自动化引擎。** 它不探测 Cadence 版本，不启动或关闭 OrCAD Capture / Allegro，不注入 Tcl/SKILL，不监控 Cadence 进程，也不解析或执行脚本。

Tcl 与 SKILL 的启动、运行、会话和退出流程由独立的应用侧脚本/适配器负责。AirBridge 仅传输脚本文本或文件。

## 通用原始数据 API（主机 A）

前提：A/B 两端均运行 AirBridge，串口链路已连接。

```http
POST http://127.0.0.1:8888/api/v1/data/send
Content-Type: application/json

{
  "data": "任意 UTF-8 文本或脚本文本"
}
```

该 API 将 data 原样编码为 UTF-8，通过 RAW_STREAM 帧发送到主机 B。主机 B 的 AirBridge 只显示收到的数据，不会执行它。

单帧上限为 65,000 字节；更大的内容请使用文件传输功能。

也可直接在 GUI 的“通用数据通道”编辑和发送文本。接收端会显示最近收到的数据。需要传输大型 Tcl/SKILL 文件时，请使用“文件传输”页。

## 远程文件系统（RFS）

主机 A 的 HTTP API 仍提供 /api/v1/fs/<action>，支持 drives、ls、read、write、mkdir、rm、mv、stat 和 pull。

## 可选远程 Shell

主机 A 的 HTTP API 仍提供 /api/v1/shell/exec、/api/v1/shell/kill 和 /api/v1/shell/tasks。这是独立的通用远程 Shell 功能，不参与 Cadence 自动化生命周期。

## 打包

在 Windows 主机上运行 build.bat，生成 dist\\AirBridge.exe。

## 版本

- v2.5：移除内置 Cadence 执行引擎；增加通用 RAW_STREAM 数据传输；AirBridge 不再管理 Cadence 应用生命周期。
- v2.4：远程 Shell。
- v2.3：多版本 Cadence 与 EDA 执行（已从 AirBridge 核心移除）。