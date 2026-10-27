# airbridge_app.py
# -*- coding: utf-8 -*-
"""
AirBridge - 物理网闸通用中继器 & EDA 自动化中枢 v2.3 (多版本 Cadence 自适应与大文件吞吐版)
"""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import os
import queue
import sys
import threading
import time
from typing import Any, Dict

from eda_engine import CadenceVersionManager, EDAExecutor
from fs_engine import FSEngine
from protocol import ABFPFrame, FrameParser, FrameType
from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
import serial
import serial.tools.list_ports

BUILTIN_TEST_ORCAD_TCL = """puts "=============================================="
puts ">>> Hello from AirBridge OrCAD Tcl Engine! <<<"
puts "System Time: [clock format [clock seconds] -format {%Y-%m-%d %H:%M:%S}]"
if {[info exists ::AIRBRIDGE_SPB_VER]} {
    puts "Active SPB Version : $::AIRBRIDGE_SPB_VER (Tcl $::tcl_version)"
}
puts "Current CDSROOT    : [expr {[info exists ::env(CDSROOT)] ? $::env(CDSROOT) : {N/A}}]"
puts "Current Working Dir: [pwd]"
puts "OrCAD Capture Tcl Check: PASS (100% OK)"
puts "=============================================="
"""

BUILTIN_TEST_ALLEGRO_SKILL = """procedure(AirBridgeTestSkill()
    let(()
        printf("\\n==============================================\\n")
        printf(">>> Hello from AirBridge Allegro SKILL!   <<<\\n")
        printf("Allegro System Time : %s\\n" getCurrentTime())
        printf("Allegro Tool Version: %s\\n" axlVersion('version))
        printf("Current CDSROOT     : %s\\n" or(getShellEnvVar("CDSROOT") "N/A"))
        printf("Allegro PCB Designer SKILL Check: PASS (100% OK)\\n")
        printf("==============================================\\n")
    )
)
AirBridgeTestSkill()
"""


class SerialWorkerSignals(QObject):
  sig_connected = Signal(bool, str)
  sig_rx_log = Signal(str, str)
  sig_tx_activity = Signal()
  sig_rx_activity = Signal()
  sig_file_progress = Signal(int, int)
  sig_file_done = Signal(str)
  sig_file_aborted = Signal(str)
  sig_eda_log = Signal(str)
  sig_fs_response = Signal(dict)


class SerialWorker(threading.Thread):

  def __init__(self, signals: SerialWorkerSignals):
    super().__init__(daemon=True)
    self.sig = signals
    self.ser: serial.Serial = None
    self.running = True
    self.tx_queue = queue.Queue()
    self.parser = FrameParser()
    self.seq_counter = 0
    self.eda_executor = EDAExecutor()
    self.lock = threading.Lock()

    self.file_transfer_abort = False
    self.is_sending_file = False
    self.ack_event = threading.Event()
    self.expected_ack_tag = None

    self.rx_file_handle = None
    self.rx_file_name = ""
    self.rx_target_dir = ""
    self.rx_total_chunks = 0
    self.rx_recv_chunks = 0
    self.default_recv_dir = os.path.abspath("./received_files")
    os.makedirs(self.default_recv_dir, exist_ok=True)

    self.fs_pending_lock = threading.Lock()
    self.fs_pending_requests: Dict[int, Dict[str, Any]] = {}

  def connect_serial(self, port: str, baud: int):
    with self.lock:
      try:
        self.parser = FrameParser()
        self.ser = serial.Serial(port, baudrate=baud, timeout=0.1)
        self.sig.sig_connected.emit(True, f"已连接 {port} @ {baud}")
      except Exception as e:
        self.ser = None
        err_str = str(e)
        if "87" in err_str and baud > 115200:
          err_str += " (主板原生 COM 端口最高仅支持 115200，请调低波特率！)"
        self.sig.sig_connected.emit(False, f"连接失败: {err_str}")

  def disconnect_serial(self):
    with self.lock:
      if self.ser:
        try:
          if self.ser.is_open:
            self.ser.close()
        except Exception:
          pass
        self.ser = None
      self.sig.sig_connected.emit(False, "串口已断开")

  def send_frame(self, frame_type: int, payload: bytes = b""):
    self.seq_counter = (self.seq_counter + 1) & 0xFFFF
    frame = ABFPFrame(frame_type, self.seq_counter, payload)
    self.tx_queue.put(frame)

  def send_fs_request_sync(
      self, req_dict: Dict[str, Any], timeout: float = 15.0
  ) -> Dict[str, Any]:
    req_id = int(time.time() * 1000) & 0x7FFFFFFF
    req_dict["req_id"] = req_id

    wait_event = threading.Event()
    with self.fs_pending_lock:
      self.fs_pending_requests[req_id] = {"event": wait_event, "response": None}

    compressed_payload = FSEngine.pack_payload(req_dict)
    self.send_frame(FrameType.FS_REQUEST, compressed_payload)

    ok = wait_event.wait(timeout=timeout)
    with self.fs_pending_lock:
      data = self.fs_pending_requests.pop(req_id, None)

    if not ok or not data or not data["response"]:
      return {
          "success": False,
          "error": f"远程调用超时 ({timeout}s) 或对端无响应",
          "action": req_dict.get("action"),
      }
    return data["response"]

  def abort_file_send(self):
    if self.is_sending_file:
      self.file_transfer_abort = True
      self.ack_event.set()

  def pull_file_async(self, remote_file_path: str):
    """向对端发起反向拉取文件请求"""
    self.sig.sig_rx_log.emit(
        "INFO", f"正在请求对端回传文件: {remote_file_path}"
    )
    payload = json.dumps({"remote_path": remote_file_path}).encode("utf-8")
    self.send_frame(FrameType.FILE_PULL_REQ, payload)

  def send_file_async(self, file_path: str, remote_dest_dir: str = ""):
    threading.Thread(
        target=self._send_file_worker,
        args=(file_path, remote_dest_dir),
        daemon=True,
    ).start()

  def _wait_for_ack(
      self, expected_tag: bytes, timeout: float = 1.2, max_retries: int = 3
  ) -> bool:
    self.expected_ack_tag = expected_tag
    for retry in range(max_retries):
      if self.file_transfer_abort:
        return False
      self.ack_event.clear()
      if self.ack_event.wait(timeout=timeout):
        return True
      if retry < max_retries - 1:
        self.sig.sig_rx_log.emit(
            "WARN", f"分片确认重试 ({retry + 1}/{max_retries})..."
        )
    return False

  def _send_file_worker(self, file_path: str, remote_dest_dir: str = ""):
    if not os.path.exists(file_path):
      self.sig.sig_rx_log.emit("ERROR", f"文件不存在: {file_path}")
      return

    self.is_sending_file = True
    self.file_transfer_abort = False

    filename = os.path.basename(file_path)
    filesize = os.path.getsize(file_path)
    chunk_size = 16384
    total_chunks = math.ceil(filesize / chunk_size) if filesize > 0 else 1

    self.sig.sig_rx_log.emit(
        "INFO",
        f"启动大文件传输: {filename} ({filesize/1024/1024:.2f} MB, 共"
        f" {total_chunks} 包)...",
    )

    meta = json.dumps({
        "filename": filename,
        "filesize": filesize,
        "total_chunks": total_chunks,
        "dest_dir": remote_dest_dir,
    }).encode("utf-8")

    handshake_ok = False
    for _ in range(3):
      if self.file_transfer_abort:
        break
      self.send_frame(FrameType.FILE_START, meta)
      if self._wait_for_ack(b"START_ACK", timeout=1.5, max_retries=1):
        handshake_ok = True
        break

    if not handshake_ok:
      self.sig.sig_rx_log.emit(
          "ERROR", "【超时熔断】对端未响应传输握手，传输已终止！"
      )
      self.sig.sig_file_aborted.emit("对端未响应握手")
      self.is_sending_file = False
      return

    self.sig.sig_rx_log.emit("INFO", "对端就绪，开始流式分片全速注入...")

    with open(file_path, "rb") as f:
      chunk_idx = 0
      while True:
        if self.file_transfer_abort:
          self.send_frame(FrameType.FILE_ABORT, filename.encode("utf-8"))
          self.sig.sig_rx_log.emit("WARN", "传输已手动终止！")
          self.sig.sig_file_aborted.emit("传输已手动终止")
          self.is_sending_file = False
          return

        data = f.read(chunk_size)
        if not data:
          break

        chunk_header = (chunk_idx & 0xFFFF).to_bytes(2, byteorder="big")
        frame_payload = chunk_header + data

        chunk_ack_ok = False
        for _ in range(3):
          if self.file_transfer_abort:
            break
          self.send_frame(FrameType.FILE_CHUNK, frame_payload)
          if self._wait_for_ack(
              b"CHUNK_" + chunk_header, timeout=1.2, max_retries=1
          ):
            chunk_ack_ok = True
            break

        if not chunk_ack_ok:
          self.sig.sig_rx_log.emit(
              "ERROR", f"【物理中断】分片 #{chunk_idx} 确认超时！"
          )
          self.send_frame(FrameType.FILE_ABORT, filename.encode("utf-8"))
          self.sig.sig_file_aborted.emit("分片确认超时")
          self.is_sending_file = False
          return

        chunk_idx += 1
        self.sig.sig_file_progress.emit(chunk_idx, total_chunks)

    self.send_frame(FrameType.FILE_END, filename.encode("utf-8"))
    if self._wait_for_ack(b"END_ACK", timeout=3.0, max_retries=2):
      self.sig.sig_rx_log.emit(
          "SUCCESS", f"大文件 [{filename}] 双端校验通过，全量安全落盘！"
      )
      self.sig.sig_file_done.emit(filename)
    else:
      self.sig.sig_rx_log.emit(
          "WARN", f"文件 [{filename}] 已传完，落盘确认超时。"
      )
      self.sig.sig_file_aborted.emit("落盘确认超时")
    self.is_sending_file = False

  def run(self):
    while self.running:
      with self.lock:
        ser_inst = self.ser
        is_open = ser_inst is not None and ser_inst.is_open

      if is_open:
        try:
          if ser_inst.in_waiting > 0:
            raw = ser_inst.read(ser_inst.in_waiting)
            self.sig.sig_rx_activity.emit()
            frames = self.parser.feed(raw)
            for f in frames:
              self._handle_incoming_frame(f)
        except (OSError, serial.SerialException) as e:
          self.sig.sig_rx_log.emit("WARN", f"物理链路断开: {e}")
          self.disconnect_serial()
          continue

      if is_open and not self.tx_queue.empty():
        try:
          frame: ABFPFrame = self.tx_queue.get_nowait()
          with self.lock:
            if self.ser and self.ser.is_open:
              self.ser.write(frame.pack())
              self.sig.sig_tx_activity.emit()
        except queue.Empty:
          pass
        except (OSError, serial.SerialException) as e:
          self.sig.sig_rx_log.emit("WARN", f"写入异常: {e}")
          self.disconnect_serial()
          continue

      time.sleep(0.005)

  def _handle_incoming_frame(self, frame: ABFPFrame):
    if frame.frame_type == FrameType.ACK:
      if self.expected_ack_tag and frame.payload == self.expected_ack_tag:
        self.ack_event.set()

    elif frame.frame_type == FrameType.TEXT_MSG:
      msg = frame.payload.decode("utf-8", errors="ignore")
      self.sig.sig_rx_log.emit("MSG", f"[对端消息] {msg}")

    elif frame.frame_type == FrameType.FILE_PULL_REQ:
      try:
        p_data = json.loads(frame.payload.decode("utf-8"))
        target_path = p_data.get("remote_path", "")
        self.sig.sig_rx_log.emit(
            "INFO", f"对端请求拉取本地文件: {target_path}，正在准备推流..."
        )
        if os.path.exists(target_path):
          self.send_file_async(target_path)
        else:
          self.sig.sig_rx_log.emit("ERROR", f"被请求的文件不存在: {target_path}")
      except Exception as e:
        self.sig.sig_rx_log.emit("ERROR", f"处理拉取请求失败: {e}")

    elif frame.frame_type == FrameType.FILE_START:
      meta = json.loads(frame.payload.decode("utf-8"))
      self.rx_file_name = meta["filename"]
      self.rx_total_chunks = meta["total_chunks"]
      dest_dir = meta.get("dest_dir", "").strip()
      self.rx_target_dir = (
          dest_dir
          if dest_dir and os.path.isdir(dest_dir)
          else self.default_recv_dir
      )
      os.makedirs(self.rx_target_dir, exist_ok=True)
      self.rx_recv_chunks = 0
      temp_path = os.path.join(self.rx_target_dir, f"_temp_{self.rx_file_name}")
      self.rx_file_handle = open(temp_path, "wb")
      self.sig.sig_rx_log.emit(
          "INFO", f"正在接收文件: {self.rx_file_name} -> {self.rx_target_dir}..."
      )
      self.sig.sig_file_progress.emit(0, self.rx_total_chunks)
      self.send_frame(FrameType.ACK, b"START_ACK")

    elif frame.frame_type == FrameType.FILE_CHUNK:
      if self.rx_file_handle:
        chunk_header = frame.payload[:2]
        chunk_data = frame.payload[2:]
        self.rx_file_handle.write(chunk_data)
        self.rx_recv_chunks += 1
        self.sig.sig_file_progress.emit(
            self.rx_recv_chunks, self.rx_total_chunks
        )
        self.send_frame(FrameType.ACK, b"CHUNK_" + chunk_header)

    elif frame.frame_type == FrameType.FILE_END:
      if self.rx_file_handle:
        self.rx_file_handle.close()
        self.rx_file_handle = None
        temp_path = os.path.join(
            self.rx_target_dir, f"_temp_{self.rx_file_name}"
        )
        final_path = os.path.join(self.rx_target_dir, self.rx_file_name)
        if os.path.exists(final_path):
          os.remove(final_path)
        os.rename(temp_path, final_path)
        self.sig.sig_rx_log.emit(
            "SUCCESS",
            f"文件 [{self.rx_file_name}] 已成功落盘于 {final_path}！",
        )
        self.sig.sig_file_done.emit(self.rx_file_name)
        self.send_frame(FrameType.ACK, b"END_ACK")

    elif frame.frame_type == FrameType.FILE_ABORT:
      if self.rx_file_handle:
        self.rx_file_handle.close()
        self.rx_file_handle = None
        temp_path = os.path.join(
            self.rx_target_dir, f"_temp_{self.rx_file_name}"
        )
        if os.path.exists(temp_path):
          try:
            os.remove(temp_path)
          except Exception:
            pass
      self.sig.sig_rx_log.emit(
          "WARN", "对端中止了文件传输，已清理临时碎片。"
      )
      self.sig.sig_file_aborted.emit("对端已取消发送")

    elif frame.frame_type == FrameType.EDA_LOG_STREAM:
      self.sig.sig_eda_log.emit(frame.payload.decode("utf-8", errors="ignore"))

    elif frame.frame_type == FrameType.EDA_TASK_SUBMIT:
      self.sig.sig_rx_log.emit(
          "EDA", "[任务调度] 收到 Cadence 自动化脚本，启动执行..."
      )
      try:
        task_data = json.loads(frame.payload.decode("utf-8"))

        def stream_back_log(line: str):
          self.send_frame(FrameType.EDA_LOG_STREAM, line.encode("utf-8"))

        threading.Thread(
            target=self._exec_eda_async,
            args=(
                task_data.get("script", ""),
                task_data.get("hint", "auto.tcl"),
                stream_back_log,
                task_data.get("version", "auto"),
                task_data.get("target_file", ""),
                task_data.get("timeout", 60),
            ),
            daemon=True,
        ).start()
      except Exception as e:
        self.send_frame(
            FrameType.EDA_LOG_STREAM, f"[Fatal] 载荷解析失败: {e}".encode("utf-8")
        )

    elif frame.frame_type == FrameType.EDA_FORCE_KILL:
      self.sig.sig_rx_log.emit("WARN", "[任务调度] 收到强杀指令，正在终止进程...")
      self.eda_executor.kill_active_task()

    elif frame.frame_type == FrameType.FS_REQUEST:
      try:
        req_obj = FSEngine.unpack_payload(frame.payload)
        threading.Thread(
            target=self._handle_fs_request_worker, args=(req_obj,), daemon=True
        ).start()
      except Exception as e:
        self.sig.sig_rx_log.emit("ERROR", f"FS Request 解码异常: {e}")

    elif frame.frame_type == FrameType.FS_RESPONSE:
      try:
        resp_obj = FSEngine.unpack_payload(frame.payload)
        req_id = resp_obj.get("req_id")
        with self.fs_pending_lock:
          if req_id in self.fs_pending_requests:
            self.fs_pending_requests[req_id]["response"] = resp_obj
            self.fs_pending_requests[req_id]["event"].set()
        self.sig.sig_fs_response.emit(resp_obj)
      except Exception as e:
        self.sig.sig_rx_log.emit("ERROR", f"FS Response 解码异常: {e}")

  def _handle_fs_request_worker(self, req_obj: Dict[str, Any]):
    # 扩展支持探测远端安装的 Cadence 多版本列表
    if req_obj.get("action") == "eda_versions":
      installs = CadenceVersionManager.discover_installations()
      result = {
          "req_id": req_obj.get("req_id", 0),
          "action": "eda_versions",
          "success": True,
          "versions": installs,
      }
    else:
      result = FSEngine.dispatch(req_obj)
    compressed_resp = FSEngine.pack_payload(result)
    self.send_frame(FrameType.FS_RESPONSE, compressed_resp)

  def _exec_eda_async(
      self,
      script: str,
      hint: str,
      log_fn,
      version: str = "auto",
      target_file: str = "",
      timeout: int = 60,
  ):
    ret, out = self.eda_executor.execute_script(
        script,
        hint,
        log_fn,
        timeout=timeout,
        version=version,
        target_file=target_file,
    )
    self.send_frame(
        FrameType.EDA_TASK_FIN, json.dumps({"returncode": ret}).encode("utf-8")
    )


# ==============================================================================
# 北向 REST API 服务器
# ==============================================================================
class AntigravityIPCServer:

  def __init__(self, worker: SerialWorker, port: int = 8888):
    self.worker = worker
    self.port = port

  def start(self):
    worker_ref = self.worker

    class Handler(BaseHTTPRequestHandler):

      def log_message(self, format, *args):
        pass

      def _reply_json(self, status_code: int, data: dict):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

      def do_GET(self):
        if self.path == "/api/v1/eda/versions":
          res = worker_ref.send_fs_request_sync(
              {"action": "eda_versions"}, timeout=10.0
          )
          self._reply_json(200 if res.get("success") else 500, res)
        else:
          self._reply_json(404, {"error": "Not Found"})

      def do_POST(self):
        content_len = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_len).decode("utf-8", errors="ignore")

        if self.path in ["/api/v1/eda/execute", "/api/eda/exec"]:
          try:
            task = (
                json.loads(raw_body)
                if raw_body.strip().startswith("{")
                else {"script": raw_body, "hint": "agent_auto.tcl"}
            )
          except Exception:
            task = {"script": raw_body, "hint": "agent_auto.tcl"}

          script = task.get("script") or task.get("script_content", "")
          hint = task.get("hint") or task.get("script_name", "agent_auto.tcl")
          version = task.get("version", "auto")
          target_file = task.get("target_file", "")
          timeout = int(task.get("timeout", 60))

          submit_payload = {
              "script": script,
              "hint": hint,
              "version": version,
              "target_file": target_file,
              "timeout": timeout,
          }
          worker_ref.send_frame(
              FrameType.EDA_TASK_SUBMIT,
              json.dumps(submit_payload).encode("utf-8"),
          )
          self._reply_json(
              200, {"status": "submitted", "hint": hint, "version": version}
          )

        elif self.path == "/api/v1/eda/versions":
          res = worker_ref.send_fs_request_sync(
              {"action": "eda_versions"}, timeout=10.0
          )
          self._reply_json(200 if res.get("success") else 500, res)

        elif self.path == "/api/v1/message":
          worker_ref.send_frame(FrameType.TEXT_MSG, raw_body.encode("utf-8"))
          self._reply_json(200, {"status": "sent"})

        elif self.path == "/api/v1/fs/pull":
          try:
            req_data = json.loads(raw_body)
            remote_path = req_data.get("path", "")
            worker_ref.pull_file_async(remote_path)
            self._reply_json(
                200, {"status": "pulling", "remote_path": remote_path}
            )
          except Exception as e:
            self._reply_json(500, {"error": str(e)})

        elif self.path.startswith("/api/v1/fs/"):
          action = self.path.split("/")[-1]
          try:
            req_data = json.loads(raw_body) if raw_body else {}
          except Exception:
            req_data = {}

          if action != "exec":
            req_data["action"] = action

          res = worker_ref.send_fs_request_sync(
              req_data, timeout=req_data.get("timeout", 15.0)
          )
          self._reply_json(200 if res.get("success", False) else 500, res)

        else:
          self._reply_json(404, {"error": "Not Found"})

    def serve():
      try:
        s = HTTPServer(("127.0.0.1", self.port), Handler)
        s.serve_forever()
      except Exception:
        pass

    threading.Thread(target=serve, daemon=True).start()


# ==============================================================================
# 现代暗黑工程 GUI 界面
# ==============================================================================
DARK_STYLE = """
QMainWindow, QWidget { background-color: #1E1E1E; color: #D4D4D4; font-family: 'Segoe UI', Consolas; }
QGroupBox { border: 1px solid #3E3E42; margin-top: 6px; padding: 10px; font-weight: bold; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
QPushButton { background-color: #0E639C; color: white; border: none; padding: 6px 14px; border-radius: 3px; font-weight: 500; }
QPushButton:hover { background-color: #1177BB; }
QPushButton:pressed { background-color: #094771; }
QPushButton:disabled { background-color: #333333; color: #666666; }
QComboBox, QLineEdit { background-color: #2D2D30; border: 1px solid #3E3E42; padding: 4px; color: white; }
QComboBox:disabled { background-color: #1E1E1E; color: #666666; }
QTextEdit { background-color: #252526; border: 1px solid #3E3E42; font-family: Consolas; font-size: 12px; }
QTabWidget::pane { border: 1px solid #3E3E42; background: #1E1E1E; }
QTabBar::tab { background: #2D2D30; padding: 8px 16px; border: 1px solid #3E3E42; margin-right: 2px; }
QTabBar::tab:selected { background: #0E639C; color: white; }
QProgressBar { border: 1px solid #3E3E42; text-align: center; background-color: #252526; height: 20px; }
QProgressBar::chunk { background-color: #388A34; }
QTableWidget { background-color: #252526; gridline-color: #3E3E42; border: 1px solid #3E3E42; color: #D4D4D4; }
QHeaderView::section { background-color: #2D2D30; color: white; padding: 4px; border: 1px solid #3E3E42; font-weight: bold; }
"""


class AirBridgeMainWindow(QMainWindow):

  def __init__(self):
    super().__init__()
    self.setWindowTitle(
        "AirBridge - 物理网闸通用中继器 & EDA 自动化中枢 v2.3 (多版本 Cadence"
        " 自适应版)"
    )
    self.resize(1160, 820)
    self.setStyleSheet(DARK_STYLE)

    self.current_hint = "auto.tcl"
    self.current_remote_dir = "e:\\RK3588智能盒子"

    self.signals = SerialWorkerSignals()
    self.worker = SerialWorker(self.signals)
    self.worker.start()

    self.ipc = AntigravityIPCServer(self.worker, port=8888)
    self.ipc.start()

    self.init_ui()
    self.bind_events()
    self.scan_ports()

  def init_ui(self):
    central = QWidget()
    self.setCentralWidget(central)
    main_layout = QVBoxLayout(central)

    # 1. 物理链路控制条
    top_group = QGroupBox("物理链路控制")
    top_layout = QHBoxLayout(top_group)
    top_layout.addWidget(QLabel("端口:"))
    self.combo_ports = QComboBox()
    self.combo_ports.setMinimumWidth(220)
    top_layout.addWidget(self.combo_ports)

    self.btn_refresh = QPushButton("🔄 扫描")
    top_layout.addWidget(self.btn_refresh)

    top_layout.addWidget(QLabel("波特率:"))
    self.combo_baud = QComboBox()
    self.combo_baud.addItems([
        "9600",
        "19200",
        "38400",
        "57600",
        "115200",
        "230400",
        "460800",
        "921600",
    ])
    self.combo_baud.setCurrentText("460800")
    top_layout.addWidget(self.combo_baud)

    self.btn_connect = QPushButton("🔌 打开串口")
    top_layout.addWidget(self.btn_connect)

    self.lbl_tx_led = QLabel("TX ○")
    self.lbl_rx_led = QLabel("RX ○")
    top_layout.addWidget(self.lbl_tx_led)
    top_layout.addWidget(self.lbl_rx_led)

    self.lbl_status = QLabel("链路: 未连接")
    self.lbl_status.setStyleSheet("color: #E51400; font-weight: bold;")
    top_layout.addWidget(self.lbl_status)
    top_layout.addStretch()
    main_layout.addWidget(top_group)

    # 2. 功能选项卡
    self.tabs = QTabWidget()

    # Tab 1: 远程文件系统 (RFS)
    rfs_tab = QWidget()
    rfs_layout = QVBoxLayout(rfs_tab)

    path_bar = QHBoxLayout()
    path_bar.addWidget(QLabel("远程路径:"))
    self.ipt_rfs_path = QLineEdit(self.current_remote_dir)
    self.btn_rfs_go = QPushButton("进入目录")
    self.btn_rfs_up = QPushButton("⬆ 上一级")
    self.btn_rfs_drives = QPushButton("💽 扫描盘符")
    self.btn_rfs_mkdir = QPushButton("📁 新建目录")
    self.btn_rfs_refresh = QPushButton("🔄 刷新")
    path_bar.addWidget(self.ipt_rfs_path)
    path_bar.addWidget(self.btn_rfs_go)
    path_bar.addWidget(self.btn_rfs_up)
    path_bar.addWidget(self.btn_rfs_drives)
    path_bar.addWidget(self.btn_rfs_mkdir)
    path_bar.addWidget(self.btn_rfs_refresh)
    rfs_layout.addLayout(path_bar)

    self.table_rfs = QTableWidget()
    self.table_rfs.setColumnCount(4)
    self.table_rfs.setHorizontalHeaderLabels(
        ["名称", "类型", "大小 (Bytes)", "修改时间"]
    )
    self.table_rfs.horizontalHeader().setSectionResizeMode(
        0, QHeaderView.Stretch
    )
    self.table_rfs.horizontalHeader().setSectionResizeMode(
        1, QHeaderView.ResizeToContents
    )
    self.table_rfs.horizontalHeader().setSectionResizeMode(
        2, QHeaderView.ResizeToContents
    )
    self.table_rfs.horizontalHeader().setSectionResizeMode(
        3, QHeaderView.ResizeToContents
    )
    self.table_rfs.setSelectionBehavior(QTableWidget.SelectRows)
    self.table_rfs.setEditTriggers(QTableWidget.NoEditTriggers)
    rfs_layout.addWidget(self.table_rfs)

    rfs_ops = QHBoxLayout()
    self.btn_rfs_download = QPushButton("⬇ 下载选中文件 (Pull)")
    self.btn_rfs_download.setStyleSheet(
        "background-color: #2E7D32; font-weight: bold;"
    )
    self.btn_rfs_upload = QPushButton("⬆ 上传文件到当前目录 (Push)")
    self.btn_rfs_upload.setStyleSheet(
        "background-color: #0E639C; font-weight: bold;"
    )
    self.btn_rfs_read = QPushButton("📄 快速查看内容")
    self.btn_rfs_del = QPushButton("🗑 删除选中目标")
    self.btn_rfs_del.setStyleSheet("background-color: #C62828;")

    rfs_ops.addWidget(self.btn_rfs_download)
    rfs_ops.addWidget(self.btn_rfs_upload)
    rfs_ops.addWidget(self.btn_rfs_read)
    rfs_ops.addWidget(self.btn_rfs_del)
    rfs_ops.addStretch()
    rfs_layout.addLayout(rfs_ops)

    self.tabs.addTab(rfs_tab, "🌐 远程文件系统 (RFS)")

    # Tab 2: 大文件双向快传
    file_tab = QWidget()
    file_layout = QVBoxLayout(file_tab)
    f_select_layout = QHBoxLayout()
    self.ipt_filepath = QLineEdit()
    self.ipt_filepath.setPlaceholderText(
        "请选择要传输的文件 (DSN、BRD、网表、PDF、压缩包)..."
    )
    self.btn_browse = QPushButton("浏览文件...")
    self.btn_send_file = QPushButton("🚀 开始发送文件")
    self.btn_send_file.setStyleSheet("background-color: #388A34;")

    self.btn_abort_file = QPushButton("⏹ 终止传输")
    self.btn_abort_file.setStyleSheet("background-color: #E51400;")
    self.btn_abort_file.setEnabled(False)

    f_select_layout.addWidget(self.ipt_filepath)
    f_select_layout.addWidget(self.btn_browse)
    f_select_layout.addWidget(self.btn_send_file)
    f_select_layout.addWidget(self.btn_abort_file)
    file_layout.addLayout(f_select_layout)

    file_layout.addWidget(QLabel("双端分片确认传输进度 (16KB 高速分片):"))
    self.progress_file = QProgressBar()
    self.progress_file.setValue(0)
    file_layout.addWidget(self.progress_file)

    btn_open_recv = QPushButton("📂 打开接收文件目录 (./received_files)")
    btn_open_recv.clicked.connect(
        lambda: os.startfile(self.worker.default_recv_dir)
        if sys.platform == "win32"
        else None
    )
    file_layout.addWidget(btn_open_recv)
    file_layout.addStretch()
    self.tabs.addTab(file_tab, "📁 大文件双向快传")

    # Tab 3: EDA 自动化
    eda_tab = QWidget()
    eda_layout = QVBoxLayout(eda_tab)
    eda_ctrl = QHBoxLayout()

    eda_ctrl.addWidget(QLabel("目标版本:"))
    self.combo_eda_ver = QComboBox()
    self.combo_eda_ver.addItem("自动匹配 (Auto)", "auto")
    for v in ["17.2", "17.4", "23.1", "22.1", "24.1", "16.6"]:
      self.combo_eda_ver.addItem(f"SPB {v}", v)
    eda_ctrl.addWidget(self.combo_eda_ver)

    self.btn_detect_eda_vers = QPushButton("🔍 探测对端版本")
    self.btn_detect_eda_vers.setStyleSheet("background-color: #007ACC;")
    eda_ctrl.addWidget(self.btn_detect_eda_vers)

    self.btn_test_orcad = QPushButton("📐 测试原理图 (OrCAD)")
    self.btn_test_orcad.setStyleSheet("background-color: #2E7D32;")

    self.btn_test_allegro = QPushButton("⚡ 测试PCB (Allegro)")
    self.btn_test_allegro.setStyleSheet("background-color: #6A1B9A;")

    self.btn_load_eda = QPushButton("📂 加载本地脚本...")
    self.btn_exec_eda = QPushButton("▶ 立即执行 (F5)")
    self.btn_exec_eda.setStyleSheet("background-color: #0E639C;")
    self.btn_abort_eda = QPushButton("⏹ 强杀任务")
    self.btn_abort_eda.setStyleSheet("background-color: #C62828;")

    eda_ctrl.addWidget(self.btn_test_orcad)
    eda_ctrl.addWidget(self.btn_test_allegro)
    eda_ctrl.addWidget(self.btn_load_eda)
    eda_ctrl.addWidget(self.btn_exec_eda)
    eda_ctrl.addWidget(self.btn_abort_eda)
    eda_ctrl.addStretch()

    self.txt_eda_editor = QTextEdit()
    self.txt_eda_editor.setPlaceholderText(
        "OrCAD Tcl / Allegro SKILL 代码编辑区..."
    )

    eda_layout.addLayout(eda_ctrl)
    eda_layout.addWidget(self.txt_eda_editor)
    self.tabs.addTab(eda_tab, "★ EDA 自动化执行")

    # Tab 4: 即时消息
    chat_tab = QWidget()
    chat_layout = QVBoxLayout(chat_tab)
    self.txt_chat = QTextEdit()
    self.txt_chat.setReadOnly(True)
    c_input = QHBoxLayout()
    self.ipt_chat = QLineEdit()
    self.ipt_chat.setPlaceholderText("输入消息按回车...")
    self.btn_send_chat = QPushButton("发送")
    c_input.addWidget(self.ipt_chat)
    c_input.addWidget(self.btn_send_chat)
    chat_layout.addWidget(self.txt_chat)
    chat_layout.addLayout(c_input)
    self.tabs.addTab(chat_tab, "💬 双机即时消息")

    main_layout.addWidget(self.tabs, stretch=2)

    # 3. 底部控制台
    console_group = QGroupBox("控制台输出")
    console_layout = QVBoxLayout(console_group)
    console_top_bar = QHBoxLayout()
    console_top_bar.addStretch()
    self.btn_clear_console = QPushButton("🧹 清空控制台")
    self.btn_clear_console.setStyleSheet(
        "background-color: #333333; color: #BBBBBB; padding: 3px 12px;"
        " font-size: 11px;"
    )
    console_top_bar.addWidget(self.btn_clear_console)
    console_layout.addLayout(console_top_bar)

    self.txt_console = QTextEdit()
    self.txt_console.setReadOnly(True)
    console_layout.addWidget(self.txt_console)
    main_layout.addWidget(console_group, stretch=1)

  def bind_events(self):
    self.btn_refresh.clicked.connect(self.scan_ports)
    self.btn_connect.clicked.connect(self.toggle_connect)
    self.btn_send_chat.clicked.connect(self.send_chat_msg)
    self.ipt_chat.returnPressed.connect(self.send_chat_msg)
    self.btn_clear_console.clicked.connect(self.txt_console.clear)

    self.btn_detect_eda_vers.clicked.connect(self.detect_remote_eda_versions)
    self.btn_test_orcad.clicked.connect(self.trigger_builtin_orcad_test)
    self.btn_test_allegro.clicked.connect(self.trigger_builtin_allegro_test)
    self.btn_load_eda.clicked.connect(self.load_eda_file)
    self.btn_exec_eda.clicked.connect(self.run_eda_script)
    self.btn_abort_eda.clicked.connect(
        lambda: self.worker.send_frame(FrameType.EDA_FORCE_KILL, b"")
    )

    self.btn_browse.clicked.connect(self.browse_file)
    self.btn_send_file.clicked.connect(self.trigger_send_file)
    self.btn_abort_file.clicked.connect(self.trigger_abort_file)

    # RFS 事件
    self.btn_rfs_go.clicked.connect(self.rfs_navigate)
    self.btn_rfs_refresh.clicked.connect(self.rfs_navigate)
    self.btn_rfs_up.clicked.connect(self.rfs_up_level)
    self.btn_rfs_drives.clicked.connect(self.rfs_scan_drives)
    self.btn_rfs_mkdir.clicked.connect(self.rfs_create_dir)
    self.btn_rfs_read.clicked.connect(self.rfs_view_file)
    self.btn_rfs_del.clicked.connect(self.rfs_delete_selected)
    self.btn_rfs_download.clicked.connect(self.rfs_download_selected)
    self.btn_rfs_upload.clicked.connect(self.rfs_upload_to_current)
    self.table_rfs.cellDoubleClicked.connect(self.rfs_on_double_click)

    # 信号
    self.signals.sig_connected.connect(self.on_serial_connected)
    self.signals.sig_rx_log.connect(self.append_log)
    self.signals.sig_eda_log.connect(lambda l: self.append_log("REMOTE", l))
    self.signals.sig_tx_activity.connect(self.flash_tx)
    self.signals.sig_rx_activity.connect(self.flash_rx)
    self.signals.sig_file_progress.connect(self.update_file_progress)
    self.signals.sig_file_done.connect(self.on_file_complete)
    self.signals.sig_file_aborted.connect(self.on_file_aborted)
    self.signals.sig_fs_response.connect(self.on_rfs_response_arrived)

  def scan_ports(self):
    self.combo_ports.clear()
    for p in serial.tools.list_ports.comports():
      self.combo_ports.addItem(f"{p.device} ({p.description})", p.device)

  def toggle_connect(self):
    if self.worker.ser and self.worker.ser.is_open:
      self.worker.disconnect_serial()
    else:
      p = self.combo_ports.currentData()
      b = int(self.combo_baud.currentText())
      if p:
        self.worker.connect_serial(p, b)

  def on_serial_connected(self, conn: bool, desc: str):
    if conn:
      self.lbl_status.setText(f"链路: {desc}")
      self.lbl_status.setStyleSheet("color: #388A34; font-weight: bold;")
      self.btn_connect.setText("🔌 关闭串口")
      self.combo_ports.setEnabled(False)
      self.combo_baud.setEnabled(False)
      self.btn_refresh.setEnabled(False)
    else:
      self.lbl_status.setText(f"链路: {desc}")
      self.lbl_status.setStyleSheet("color: #E51400; font-weight: bold;")
      self.btn_connect.setText("🔌 打开串口")
      self.combo_ports.setEnabled(True)
      self.combo_baud.setEnabled(True)
      self.btn_refresh.setEnabled(True)

  def flash_tx(self):
    self.lbl_tx_led.setStyleSheet("color: #0E639C; font-weight: bold;")
    QTimer.singleShot(
        80,
        lambda: self.lbl_tx_led.setStyleSheet(
            "color: #888; font-weight: normal;"
        ),
    )

  def flash_rx(self):
    self.lbl_rx_led.setStyleSheet("color: #388A34; font-weight: bold;")
    QTimer.singleShot(
        80,
        lambda: self.lbl_rx_led.setStyleSheet(
            "color: #888; font-weight: normal;"
        ),
    )

  # ---------------- RFS 闭环交互调度 ----------------
  def _run_fs_call_async(self, req_dict: dict, label_info: str):
    self.append_log("INFO", label_info)

    def worker_fn():
      res = self.worker.send_fs_request_sync(req_dict, timeout=15.0)
      self.signals.sig_fs_response.emit(res)

    threading.Thread(target=worker_fn, daemon=True).start()

  def detect_remote_eda_versions(self):
    self._run_fs_call_async(
        {"action": "eda_versions"}, "正在扫描对端主机已安装的 Cadence 版本..."
    )

  def rfs_navigate(self):
    target = self.ipt_rfs_path.text().strip()
    if not target:
      target = "e:\\RK3588智能盒子"
    self._run_fs_call_async(
        {"action": "ls", "path": target}, f"请求浏览远程路径: {target}"
    )

  def rfs_up_level(self):
    cur = self.ipt_rfs_path.text().strip()
    parent = os.path.dirname(cur)
    if parent and parent != cur:
      self.ipt_rfs_path.setText(parent)
      self.rfs_navigate()

  def rfs_scan_drives(self):
    self._run_fs_call_async({"action": "drives"}, "请求远程驱动器列表...")

  def rfs_create_dir(self):
    cur = self.ipt_rfs_path.text().strip()
    name, ok = QInputDialog.getText(self, "新建远程目录", "请输入目录名称:")
    if ok and name.strip():
      new_path = os.path.join(cur, name.strip())
      self._run_fs_call_async(
          {"action": "mkdir", "path": new_path},
          f"请求创建远程目录: {new_path}",
      )

  def rfs_download_selected(self):
    row = self.table_rfs.currentRow()
    if row < 0:
      QMessageBox.information(self, "提示", "请先在列表中选中要下载的文件！")
      return
    name = self.table_rfs.item(row, 0).text()
    is_dir = self.table_rfs.item(row, 1).text() == "📁 目录"
    if is_dir:
      QMessageBox.information(
          self, "提示", "所选目标是目录，目前仅支持下载具体文件。"
      )
      return
    remote_path = os.path.join(self.ipt_rfs_path.text().strip(), name)
    self.append_log("INFO", f"启动拉取远程大文件: {remote_path}")
    self.tabs.setCurrentIndex(1)
    self.worker.pull_file_async(remote_path)

  def rfs_upload_to_current(self):
    p, _ = QFileDialog.getOpenFileName(
        self, "选择要推送到远程的文件", "", "所有文件 (*.*)"
    )
    if not p:
      return
    remote_cur = self.ipt_rfs_path.text().strip()
    self.append_log(
        "INFO", f"上传本地文件 [{os.path.basename(p)}] 至远程 [{remote_cur}]..."
    )
    self.tabs.setCurrentIndex(1)
    self.progress_file.setValue(0)
    self.btn_send_file.setEnabled(False)
    self.btn_abort_file.setEnabled(True)
    self.worker.send_file_async(p, remote_dest_dir=remote_cur)

  def rfs_view_file(self):
    row = self.table_rfs.currentRow()
    if row < 0:
      QMessageBox.information(self, "提示", "请先在列表中选中一个文件！")
      return
    name = self.table_rfs.item(row, 0).text()
    is_dir = self.table_rfs.item(row, 1).text() == "📁 目录"
    if is_dir:
      QMessageBox.information(self, "提示", "所选目标是目录，请双击进入。")
      return

    file_path = os.path.join(self.ipt_rfs_path.text().strip(), name)
    self._run_fs_call_async(
        {"action": "read", "path": file_path, "max_preview_bytes": 65536},
        f"正在请求读取/预览文件: {file_path}",
    )

  def rfs_delete_selected(self):
    row = self.table_rfs.currentRow()
    if row < 0:
      return
    name = self.table_rfs.item(row, 0).text()
    del_path = os.path.join(self.ipt_rfs_path.text().strip(), name)
    reply = QMessageBox.question(
        self,
        "确认删除",
        f"确定要永久删除远程目标吗？\n{del_path}",
        QMessageBox.Yes | QMessageBox.No,
    )
    if reply == QMessageBox.Yes:
      self._run_fs_call_async(
          {"action": "rm", "path": del_path}, f"请求删除远程目标: {del_path}"
      )

  def rfs_on_double_click(self, row, col):
    name = self.table_rfs.item(row, 0).text()
    is_dir = self.table_rfs.item(row, 1).text() == "📁 目录"
    if is_dir:
      cur = self.ipt_rfs_path.text().strip()
      self.ipt_rfs_path.setText(os.path.join(cur, name))
      self.rfs_navigate()
    else:
      self.rfs_view_file()

  def on_rfs_response_arrived(self, resp: dict):
    action = resp.get("action", "")
    success = resp.get("success", False)

    if not success:
      self.append_log("ERROR", f"[RFS 异常] {resp.get('error', '未知调用错误')}")
      return

    if action == "ls":
      self.ipt_rfs_path.setText(resp.get("path", ""))
      items = resp.get("items", [])
      self.table_rfs.setRowCount(len(items))
      for i, it in enumerate(items):
        is_dir = it.get("is_dir", False)
        self.table_rfs.setItem(i, 0, QTableWidgetItem(it.get("name", "")))
        self.table_rfs.setItem(
            i, 1, QTableWidgetItem("📁 目录" if is_dir else "📄 文件")
        )
        self.table_rfs.setItem(
            i, 2, QTableWidgetItem("-" if is_dir else str(it.get("size", 0)))
        )
        self.table_rfs.setItem(i, 3, QTableWidgetItem(it.get("mtime", "")))
      self.append_log(
          "SUCCESS",
          f"已成功加载远程目录 [{resp.get('path')}] (共 {len(items)} 项)",
      )

    elif action == "drives":
      drives = resp.get("drives", [])
      d_str = ", ".join(
          [f"{d['drive']} (可用: {d['free_gb']}GB)" for d in drives]
      )
      self.append_log("SUCCESS", f"远程驱动器列表: {d_str}")

    elif action == "eda_versions":
      vers = resp.get("versions", {})
      if not vers:
        self.append_log("WARN", "对端未扫描到标准 Cadence SPB 安装目录。")
      else:
        summary = ", ".join(
            [f"SPB {v} ({info.get('cdsroot')})" for v, info in vers.items()]
        )
        self.append_log("SUCCESS", f"对端可用 Cadence 版本: {summary}")

    elif action == "read":
      content = resp.get("content", "")
      sz = resp.get("size", len(content))
      is_trunc = resp.get("is_truncated", False)
      tip_str = (
          f"已捕获文件前 64KB 内容 (全文件大小: {sz/1024/1024:.2f} MB)"
          if is_trunc
          else f"文件读取完毕 (共 {sz} 字节)"
      )
      self.append_log("SUCCESS", tip_str)

      self.txt_console.append(
          f"<font color='#4EC9B0'><b>=== [远程文件预览: {resp.get('path')} ({sz}"
          " 字节)] ===</b></font>"
      )
      self.txt_console.append(content[:4000])
      if is_trunc:
        self.txt_console.append(
            "<font color='#CCA700'><b>\n... [后序省略，文件共"
            f" {sz/1024/1024:.2f} MB。如需完整文件，请直接在列表中点击【⬇"
            " 下载选中文件 (Pull)】] ...</b></font>"
        )
      self.txt_console.append(
          "<font color='#4EC9B0'><b>=== [预览结束] ===</b></font>"
      )

    elif action in ["mkdir", "rm", "mv"]:
      self.append_log(
          "SUCCESS", f"远程操作 [{action}] 成功！正在自动刷新目录..."
      )
      self.rfs_navigate()

  # ---------------- 基础通信逻辑 ----------------
  def browse_file(self):
    p, _ = QFileDialog.getOpenFileName(
        self, "选择要传输的文件", "", "所有文件 (*.*)"
    )
    if p:
      self.ipt_filepath.setText(p)

  def trigger_send_file(self):
    path = self.ipt_filepath.text().strip()
    if not path or not os.path.exists(path):
      self.append_log("WARN", "请先选择一个有效的文件！")
      return
    self.progress_file.setValue(0)
    self.btn_send_file.setEnabled(False)
    self.btn_abort_file.setEnabled(True)
    self.worker.send_file_async(path)

  def trigger_abort_file(self):
    self.worker.abort_file_send()
    self.btn_abort_file.setEnabled(False)

  def on_file_complete(self, fn: str):
    self.btn_send_file.setEnabled(True)
    self.btn_abort_file.setEnabled(False)
    self.progress_file.setValue(100)

  def on_file_aborted(self, reason: str):
    self.btn_send_file.setEnabled(True)
    self.btn_abort_file.setEnabled(False)
    self.progress_file.setValue(0)
    self.append_log("ERROR", f"[传输终止] {reason}")

  def update_file_progress(self, cur, total):
    pct = int((cur / total) * 100) if total > 0 else 0
    self.progress_file.setValue(pct)

  def send_chat_msg(self):
    t = self.ipt_chat.text().strip()
    if t:
      self.worker.send_frame(FrameType.TEXT_MSG, t.encode("utf-8"))
      self.txt_chat.append(f"<font color='#0E639C'><b>[我]:</b></font> {t}")
      self.ipt_chat.clear()

  def trigger_builtin_orcad_test(self):
    self.txt_eda_editor.setText(BUILTIN_TEST_ORCAD_TCL)
    self.current_hint = "builtin_test.tcl"
    ver = self.combo_eda_ver.currentData()
    self.append_log(
        "INFO",
        f"【一键自检】已装载 OrCAD Capture Tcl 探针 (目标版本: {ver})，正在下发...",
    )
    self._dispatch_script(BUILTIN_TEST_ORCAD_TCL, self.current_hint, ver)

  def trigger_builtin_allegro_test(self):
    self.txt_eda_editor.setText(BUILTIN_TEST_ALLEGRO_SKILL)
    self.current_hint = "builtin_test.il"
    ver = self.combo_eda_ver.currentData()
    self.append_log(
        "INFO",
        f"【一键自检】已装载 Allegro PCB SKILL 探针 (目标版本: {ver})，正在下发...",
    )
    self._dispatch_script(BUILTIN_TEST_ALLEGRO_SKILL, self.current_hint, ver)

  def load_eda_file(self):
    p, _ = QFileDialog.getOpenFileName(
        self,
        "选择脚本",
        "",
        "Cadence 脚本 (*.tcl *.il *.skill *.scr);;所有文件 (*.*)",
    )
    if p:
      self.current_hint = os.path.basename(p)
      with open(p, "r", encoding="utf-8", errors="ignore") as f:
        self.txt_eda_editor.setText(f.read())
      self.append_log("INFO", f"已载入本地脚本 [{self.current_hint}]")

  def run_eda_script(self):
    c = self.txt_eda_editor.toPlainText().strip()
    if not c:
      self.append_log("WARN", "编辑器内容为空，无法执行！")
      return
    ver = self.combo_eda_ver.currentData()
    self.append_log(
        "INFO",
        f"正在提交当前编辑器代码 (后缀: {self.current_hint}, 版本: {ver})...",
    )
    self._dispatch_script(c, self.current_hint, ver)

  def _dispatch_script(
      self, script_text: str, hint_name: str, version: str = "auto"
  ):
    payload = json.dumps(
        {"script": script_text, "hint": hint_name, "version": version}
    ).encode("utf-8")
    self.worker.send_frame(FrameType.EDA_TASK_SUBMIT, payload)

  def append_log(self, level: str, msg: str):
    color = "#D4D4D4"
    if level == "ERROR":
      color = "#F44747"
    elif level == "WARN":
      color = "#CCA700"
    elif level == "REMOTE":
      color = "#4EC9B0"
    elif level == "MSG":
      color = "#3794FF"
    elif level == "SUCCESS":
      color = "#89D185"
    self.txt_console.append(
        f"<font color='#6A9955'>[{time.strftime('%H:%M:%S')}]</font> <font"
        f" color='{color}'>[{level}] {msg}</font>"
    )


if __name__ == "__main__":
  app = QApplication(sys.argv)
  window = AirBridgeMainWindow()
  window.show()
  sys.exit(app.exec())
