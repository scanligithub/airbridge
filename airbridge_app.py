# airbridge_app.py
# -*- coding: utf-8 -*-
"""
AirBridge - 物理网闸通用中继器 & EDA 自动化中枢 v2.4
多版本 Cadence 自适应 + 大文件吞吐 + 远程 Shell (PowerShell/CMD)
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import hashlib
import math
import ntpath
import os
from pathlib import Path
import queue
import sys
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from eda_engine import (
    CadenceVersionManager,
    EDAExecutor,
    EDALanguage,
    detect_script_type,
    find_cap_autoload_dirs,
)
from capture_tcl_bridge import CaptureTclAdapter
from fs_engine import FSEngine
from protocol import ABFPFrame, FrameParser, FrameType, MAX_PAYLOAD_LEN
from runtime_session_protocol import (
    CLIENT_VERSION,
    SESSION_PROTOCOL_ID,
    SessionProtocolError,
    decode_session_message,
    encode_session_message,
    make_session_capabilities,
    make_session_hello,
)
from request_tracker import (
    RequestTracker,
    RequestTrackerError,
    encode_bounded_json_payload,
)
from request_broker import RequestBroker, RequestBrokerError
from agent_security import (
    AgentAuthenticationError,
    AgentSecurityError,
    AgentSecurityStore,
)
from runtime_adapter import (
    RuntimeExecutionContext,
    RuntimeExecutionRequest,
    RuntimeProfile,
    create_legacy_runtime_registry,
    select_eda_profile,
)
from shell_engine import shell_engine, ShellEngine
from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QComboBox,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
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
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
import serial
import serial.tools.list_ports

BUILTIN_TEST_ORCAD_TCL = """puts "=============================================="
puts ">>> AirBridge OrCAD Capture Bootstrap Test <<<"
puts "System Time: [clock format [clock seconds] -format {%Y-%m-%d %H:%M:%S}]"
if {[info exists ::AIRBRIDGE_SPB_VER]} {
    puts "Active SPB Version : $::AIRBRIDGE_SPB_VER (Tcl $::tcl_version)"
}
puts "Current CDSROOT    : [expr {[info exists ::env(CDSROOT)] ? $::env(CDSROOT) : {N/A}}]"
puts "Current Working Dir: [pwd]"
puts "Capture Tcl bootstrap loaded successfully."
puts "The DSN design is supplied through capture.exe command-line arguments."
puts "OrCAD Capture Bootstrap Test: PASS"
puts "=============================================="
"""

BUILTIN_TEST_ALLEGRO_SKILL = """procedure(AirBridgeTestSkill()
    let(()
        printf("\\n==============================================\\n")
        printf(">>> Hello from AirBridge Allegro SKILL!   <<<\\n")
        printf("Allegro System Time : %s\\n" getCurrentTime())
        printf("Allegro Tool Version: %L\\n" axlVersion('version))
        printf("Current CDSROOT     : %s\\n" or(getShellEnvVar("CDSROOT") "N/A"))
        printf("Allegro PCB Designer SKILL Check: PASS (ALL CHECKS OK)\\n")
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




class RemoteFileDialog(QDialog):
  """Browse files on the remote host through the existing RFS request path."""

  response_ready = Signal(int, dict)
  ROLE_PATH = int(Qt.ItemDataRole.UserRole)
  ROLE_IS_DIR = ROLE_PATH + 1

  def __init__(
      self,
      worker: "SerialWorker",
      parent: QWidget,
      title: str,
      allowed_extensions: List[str],
      initial_path: str = "",
  ):
    super().__init__(parent)
    self.worker = worker
    self.allowed_extensions = {
        ext.lower() if ext.startswith(".") else f".{ext.lower()}"
        for ext in allowed_extensions
    }
    self.selected_path = ""
    self._request_token = 0
    self._closing = False
    self._initial_path = initial_path.strip()
    self._initial_load_pending = True

    self.setWindowTitle(title)
    self.resize(860, 600)
    self.setModal(True)

    layout = QVBoxLayout(self)

    path_row = QHBoxLayout()
    path_row.addWidget(QLabel("主机 B 路径:"))
    self.path_edit = QLineEdit(self._initial_path)
    self.path_edit.setPlaceholderText("输入主机 B 目录路径，或扫描盘符后浏览")
    self.btn_go = QPushButton("进入")
    self.btn_up = QPushButton("上一级")
    self.btn_drives = QPushButton("扫描盘符")
    path_row.addWidget(self.path_edit, stretch=1)
    path_row.addWidget(self.btn_go)
    path_row.addWidget(self.btn_up)
    path_row.addWidget(self.btn_drives)
    layout.addLayout(path_row)

    drive_row = QHBoxLayout()
    drive_row.addWidget(QLabel("远程盘符:"))
    self.drive_combo = QComboBox()
    self.drive_combo.addItem("选择盘符...", "")
    drive_row.addWidget(self.drive_combo)
    drive_row.addStretch()
    drive_row.addWidget(
        QLabel("文件类型: " + ", ".join(sorted(self.allowed_extensions)))
    )
    layout.addLayout(drive_row)

    self.tree = QTreeWidget()
    self.tree.setColumnCount(4)
    self.tree.setHeaderLabels(["名称", "类型", "大小 (Bytes)", "修改时间"])
    self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    for col in (1, 2, 3):
      self.tree.header().setSectionResizeMode(
          col, QHeaderView.ResizeMode.ResizeToContents
      )
    self.tree.setSelectionBehavior(
        QAbstractItemView.SelectionBehavior.SelectRows
    )
    self.tree.setSelectionMode(
        QAbstractItemView.SelectionMode.SingleSelection
    )
    self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    layout.addWidget(self.tree, stretch=1)

    self.selected_label = QLineEdit()
    self.selected_label.setReadOnly(True)
    self.selected_label.setPlaceholderText("请选择一个匹配的远程文件")
    layout.addWidget(self.selected_label)

    self.status_label = QLabel("正在连接主机 B...")
    self.status_label.setWordWrap(True)
    layout.addWidget(self.status_label)

    self.button_box = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok
        | QDialogButtonBox.StandardButton.Cancel
    )
    self.ok_button = self.button_box.button(
        QDialogButtonBox.StandardButton.Ok
    )
    self.ok_button.setEnabled(False)
    layout.addWidget(self.button_box)

    self.response_ready.connect(self._on_response)
    self.btn_go.clicked.connect(self._navigate)
    self.path_edit.returnPressed.connect(self._navigate)
    self.btn_up.clicked.connect(self._navigate_up)
    self.btn_drives.clicked.connect(self._request_drives)
    self.drive_combo.currentIndexChanged.connect(self._on_drive_changed)
    self.tree.currentItemChanged.connect(self._on_current_item_changed)
    self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
    self.button_box.accepted.connect(self._accept_selection)
    self.button_box.rejected.connect(self.reject)

    QTimer.singleShot(0, self._request_drives)

  def _request(self, request: dict, status: str):
    self._request_token += 1
    token = self._request_token
    self.status_label.setText(status)

    def run_request():
      try:
        response = self.worker.send_fs_request_sync(
            dict(request), timeout=20.0
        )
      except Exception as exc:
        response = {
            "success": False,
            "action": request.get("action", ""),
            "error": str(exc),
        }
      try:
        self.response_ready.emit(token, response)
      except RuntimeError:
        # The dialog may have been closed while the request was in flight.
        pass

    threading.Thread(target=run_request, daemon=True).start()

  def _request_drives(self):
    self._request({"action": "drives"}, "正在读取主机 B 的盘符...")

  def _navigate(self, path: str = ""):
    target = path or self.path_edit.text().strip()
    if not target:
      self.status_label.setText("请先输入远程路径，或扫描主机 B 的盘符。")
      return
    # Clear stale rows immediately so a failed directory request cannot leave
    # files from the previous directory selectable by mistake.
    self.path_edit.setText(target)
    self.tree.clear()
    self.selected_label.clear()
    self.ok_button.setEnabled(False)
    self._request(
        {"action": "ls", "path": target},
        f"正在读取远程目录: {target}",
    )

  def _navigate_up(self):
    current = self.path_edit.text().strip()
    drive, tail = ntpath.splitdrive(current)
    if drive and tail in ("", "\\", "/"):
      return
    parent = ntpath.dirname(current.rstrip("\\/"))
    if drive and parent.lower() == drive.lower():
      parent = drive + "\\"
    if parent and ntpath.normcase(parent) != ntpath.normcase(current):
      self._navigate(parent)

  def _on_drive_changed(self, index: int):
    if index < 0:
      return
    drive_root = self.drive_combo.itemData(index)
    if drive_root:
      self._navigate(str(drive_root))

  def _on_response(self, token: int, response: dict):
    if self._closing or token != self._request_token:
      return

    if not response.get("success", False):
      self.status_label.setText(
          f"远程浏览失败: {response.get('error', '主机 B 无响应')}"
      )
      return

    action = response.get("action", "")
    if action == "drives":
      drives = response.get("drives", [])
      self.drive_combo.blockSignals(True)
      self.drive_combo.clear()
      self.drive_combo.addItem("选择盘符...", "")
      selected_index = 0
      for drive_info in drives:
        drive = str(drive_info.get("drive", "")).strip()
        if not drive:
          continue
        root = drive if drive.endswith(("\\", "/")) else drive + "\\"
        free_gb = drive_info.get("free_gb", 0)
        self.drive_combo.addItem(
            f"{drive}  (可用 {free_gb} GB)", root
        )
        drive_part, _ = ntpath.splitdrive(self._initial_path)
        if drive_part and drive_part.lower() == drive.lower():
          selected_index = self.drive_combo.count() - 1
      if not self._initial_path and self.drive_combo.count() > 1:
        selected_index = 1
      self.drive_combo.setCurrentIndex(selected_index)
      self.drive_combo.blockSignals(False)

      if self._initial_load_pending:
        self._initial_load_pending = False
        if self._initial_path:
          self._navigate(self._initial_path)
        elif selected_index > 0:
          self._navigate(str(self.drive_combo.itemData(selected_index)))
        else:
          self.status_label.setText("主机 B 没有返回可用盘符。")
      else:
        self.status_label.setText(
            f"已发现 {len(drives)} 个远程盘符。"
        )
      return

    if action != "ls":
      self.status_label.setText(
          f"收到未处理的远程响应: {action or 'unknown'}"
      )
      return

    remote_dir = str(response.get("path", self.path_edit.text().strip()))
    self.path_edit.setText(remote_dir)
    self.tree.clear()
    self.selected_label.clear()
    self.ok_button.setEnabled(False)

    visible_count = 0
    for entry in response.get("items", []):
      name = str(entry.get("name", ""))
      is_dir = bool(entry.get("is_dir", False))
      extension = ntpath.splitext(name)[1].lower()
      if not is_dir and extension not in self.allowed_extensions:
        continue

      full_path = ntpath.join(remote_dir, name)
      item = QTreeWidgetItem([
          name,
          "文件夹" if is_dir else "文件",
          "-" if is_dir else str(entry.get("size", 0)),
          str(entry.get("mtime", "")),
      ])
      item.setData(0, self.ROLE_PATH, full_path)
      item.setData(0, self.ROLE_IS_DIR, is_dir)
      self.tree.addTopLevelItem(item)
      visible_count += 1

    self.status_label.setText(
        f"目录加载完成: {remote_dir}（显示 {visible_count} 项；"
        f"文件过滤: {', '.join(sorted(self.allowed_extensions))}）"
    )

  def _on_current_item_changed(self, current, _previous):
    self.ok_button.setEnabled(False)
    self.selected_label.clear()
    if current is None:
      return
    if current.data(0, self.ROLE_IS_DIR):
      return

    path = str(current.data(0, self.ROLE_PATH) or "")
    extension = ntpath.splitext(path)[1].lower()
    if extension in self.allowed_extensions:
      self.selected_label.setText(path)
      self.ok_button.setEnabled(True)

  def _on_item_double_clicked(self, item, _column):
    path = str(item.data(0, self.ROLE_PATH) or "")
    if item.data(0, self.ROLE_IS_DIR):
      self._navigate(path)
    else:
      self._accept_selection()

  def _accept_selection(self):
    current = self.tree.currentItem()
    if current is None or current.data(0, self.ROLE_IS_DIR):
      self.status_label.setText("请先选中一个匹配类型的文件。")
      return

    path = str(current.data(0, self.ROLE_PATH) or "")
    extension = ntpath.splitext(path)[1].lower()
    if extension not in self.allowed_extensions:
      self.status_label.setText(
          f"文件类型不匹配: {extension or '无扩展名'}"
      )
      return

    self.selected_path = path
    self._closing = True
    super().accept()

  def reject(self):
    self._closing = True
    super().reject()

  def closeEvent(self, event):
    self._closing = True
    super().closeEvent(event)


class SerialWorker(threading.Thread):

  def __init__(self, signals: SerialWorkerSignals):
    super().__init__(daemon=True)
    self.sig = signals
    self.ser: serial.Serial = None
    self.running = True
    self.tx_high_queue = queue.Queue()  # 高优先级控制帧队列（ACK, NACK, ABORT, HEARTBEAT）
    self.tx_low_queue = queue.Queue()   # 常规数据帧队列（FILE_CHUNK, FS_REQ, EDA等）
    self.parser = FrameParser()
    self.seq_counter = 0
    self.eda_executor = EDAExecutor()
    self.shell_engine = shell_engine
    self.capture_tcl_adapter = CaptureTclAdapter(
        workspace_dir=self.eda_executor.workspace_dir
    )
    self.runtime_registry = create_legacy_runtime_registry(
        lambda: self.eda_executor,
        lambda: self.shell_engine,
    )
    self.runtime_registry.register_adapter(self.capture_tcl_adapter)
    self.runtime_registry.register_profile(
        RuntimeProfile(
            profile_id="capture-tcl-session",
            adapter_id=self.capture_tcl_adapter.adapter_id,
            display_name="OrCAD Capture persistent Tcl Session",
            operations=self.capture_tcl_adapter.operations,
            capabilities=self.capture_tcl_adapter.capabilities,
            execution_mode="persistent-gui-session",
        )
    )
    self.capture_tcl_adapter_registered = True
    # 远程 Shell 输出回调：把 stdout/stderr 实时推回对端
    def _shell_output_cb(task_id: str, stream: str, line: str):
      payload = json.dumps({
          "task_id": task_id,
          "stream": stream,
          "line": line,
      }, ensure_ascii=False).encode("utf-8")
      self.send_frame(FrameType.SHELL_OUTPUT, payload)
    self.shell_engine.set_output_callback(_shell_output_cb)
    self.lock = threading.Lock()

    # 滑动窗口流控（Sliding Window Pipeline）状态管理
    self.file_transfer_abort = False
    self.is_sending_file = False
    self.ack_event = threading.Event()
    self.expected_ack_tag = None

    self.window_lock = threading.Lock()
    self.window_cv = threading.Condition(self.window_lock)
    self.unacked_chunks: Dict[int, Dict[str, Any]] = {}
    self.acked_chunks = set()
    self.window_base = 0
    self.tx_total_chunks = 0
    self.sliding_window_size = 4  # 初始弹性窗口深度 (4KB * 4 = 16KB)，自适应爬升至 16KB~64KB

    self.rx_file_handle = None
    self.rx_file_name = ""
    self.rx_target_dir = ""
    self.rx_filesize = 0
    self.rx_chunk_size = 4096
    self.rx_total_chunks = 0
    self.rx_recv_chunks = 0
    self.rx_received_set = set()
    self.rx_expected_chunk = 0
    self.rx_nak_sent_time: Dict[int, float] = {}
    self.pending_dup_acks: List[Tuple[bytes, float]] = []
    self.default_recv_dir = os.path.abspath("./received_files")
    os.makedirs(self.default_recv_dir, exist_ok=True)

    self.fs_pending_lock = threading.Lock()
    self.fs_pending_requests: Dict[int, Dict[str, Any]] = {}

    # A-side HTTP <-> ABFP waiter correlation.
    self.request_tracker = RequestTracker()

    # B-side durable request admission / deduplication. The Broker remains
    # engine-agnostic; actual execution is resolved through runtime profiles.
    self.request_broker = RequestBroker()

    # Runtime Session protocol negotiation state. This is transport-level
    # capability discovery only; it does not create or manage Runtime Sessions.
    self.session_protocol_lock = threading.RLock()
    self.session_pending_handshakes: Dict[str, Dict[str, Any]] = {}
    self.session_peer_status: Dict[str, Any] = {
        "state": "disconnected",
        "negotiated": False,
        "protocol": SESSION_PROTOCOL_ID,
        "supported_protocols": [],
        "features": [],
        "profiles": [],
        "last_handshake_id": None,
        "error": None,
    }
    self.session_link_generation = 0

  def is_serial_connected(self) -> bool:
    with self.lock:
      return self.ser is not None and bool(getattr(self.ser, "is_open", False))

  def connect_serial(self, port: str, baud: int):
    self.request_tracker.fail_all_pending("serial connection is being reinitialized")
    self._reset_session_peer_status("serial connection is being reinitialized")
    connected = False
    with self.lock:
      try:
        self.parser = FrameParser()
        self.ser = serial.Serial(port, baudrate=baud, timeout=0.1)
        # Windows 驱动层缓冲区扩容至 64KB，防止 CH592/BLE 蓝牙或高速串口硬件 FIFO 溢出丢包
        if sys.platform == "win32":
          try:
            self.ser.set_buffer_size(rx_size=65536, tx_size=65536)
          except Exception:
            pass
        self.sig.sig_connected.emit(True, f"已连接 {port} @ {baud}")
        connected = True
      except Exception as e:
        self.ser = None
        err_str = str(e)
        if "87" in err_str and baud > 115200:
          err_str += " (主板原生 COM 端口最高仅支持 115200，请调低波特率！)"
        self.sig.sig_connected.emit(False, f"连接失败: {err_str}")
    if connected:
      self._start_auto_session_negotiation()

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
    self._reset_session_peer_status("serial link disconnected")
    self.request_tracker.fail_all_pending("serial link disconnected")

  def _reset_session_peer_status(self, reason: str):
    pending = []
    with self.session_protocol_lock:
      self.session_link_generation += 1
      pending = list(self.session_pending_handshakes.values())
      self.session_pending_handshakes.clear()
      self.session_peer_status = {
          "state": "disconnected",
          "negotiated": False,
          "protocol": SESSION_PROTOCOL_ID,
          "supported_protocols": [],
          "features": [],
          "profiles": [],
          "last_handshake_id": None,
          "error": reason,
      }
    for item in pending:
      item["response"] = {"state": "disconnected", "error": reason}
      item["event"].set()

  def get_session_peer_status(self) -> Dict[str, Any]:
    with self.session_protocol_lock:
      status = dict(self.session_peer_status)
      status["supported_protocols"] = list(status.get("supported_protocols", []))
      status["features"] = list(status.get("features", []))
      status["profiles"] = list(status.get("profiles", []))
      status["can_create_session"] = "session.create" in status["features"]
      return status

  def begin_session_negotiation(
      self, expected_generation: Optional[int] = None
  ) -> Dict[str, Any]:
    envelope = make_session_hello(CLIENT_VERSION)
    message_id = envelope["message_id"]
    try:
      payload = encode_session_message(FrameType.SESSION_HELLO, envelope)
    except SessionProtocolError as exc:
      return {"success": False, "state": "failed", "error": str(exc)}

    event = threading.Event()
    with self.lock:
      connected = self.ser is not None and bool(getattr(self.ser, "is_open", False))
      if not connected:
        return {"success": False, "state": "disconnected", "error": "serial link is not connected"}
      with self.session_protocol_lock:
        if (
            expected_generation is not None
            and expected_generation != self.session_link_generation
        ):
          return {"success": False, "state": "stale", "error": "serial link generation changed"}
        self.session_pending_handshakes[message_id] = {
            "event": event,
            "response": None,
        }
        self.session_peer_status = {
            "state": "negotiating",
            "negotiated": False,
            "protocol": SESSION_PROTOCOL_ID,
            "supported_protocols": [],
            "features": [],
            "profiles": [],
            "last_handshake_id": message_id,
            "error": None,
        }
      self.send_frame(FrameType.SESSION_HELLO, payload)
    return {"success": True, "state": "negotiating", "message_id": message_id}

  def _wait_session_negotiation(self, message_id: str, timeout: float) -> Dict[str, Any]:
    with self.session_protocol_lock:
      pending = self.session_pending_handshakes.get(message_id)
    if pending is None:
      return {"success": False, "state": "unknown", "error": "handshake is no longer pending"}

    if not pending["event"].wait(timeout=max(0.0, timeout)):
      with self.session_protocol_lock:
        self.session_pending_handshakes.pop(message_id, None)
        if self.session_peer_status.get("last_handshake_id") == message_id and self.session_peer_status.get("state") == "negotiating":
          self.session_peer_status["state"] = "unconfirmed"
          self.session_peer_status["error"] = "peer did not answer the capability handshake"
      return self.get_session_peer_status()

    with self.session_protocol_lock:
      self.session_pending_handshakes.pop(message_id, None)
      response = pending.get("response")
    if response:
      if response.get("state") == "disconnected":
        return {"success": False, **response}
      return {
          "success": response.get("state") == "negotiated",
          "state": response.get("state"),
          "message_id": message_id,
          "peer_capabilities": response.get("payload", {}),
      }
    return self.get_session_peer_status()

  def _start_auto_session_negotiation(self):
    with self.session_protocol_lock:
      generation = self.session_link_generation
    threading.Thread(
        target=self._auto_negotiate_session_peer,
        args=(generation,),
        name="AirBridgeSessionNegotiation",
        daemon=True,
    ).start()

  def _auto_negotiate_session_peer(self, generation: int):
    for attempt in range(1, 4):
      with self.session_protocol_lock:
        if generation != self.session_link_generation:
          return
      submitted = self.begin_session_negotiation(expected_generation=generation)
      if not submitted.get("success"):
        return
      status = self._wait_session_negotiation(submitted["message_id"], timeout=1.0)
      with self.session_protocol_lock:
        if generation != self.session_link_generation:
          return
      if status.get("state") == "negotiated":
        self.sig.sig_rx_log.emit(
            "INFO",
            "Runtime Session protocol handshake complete; features=" +
            ",".join(status.get("peer_capabilities", {}).get("features", [])),
        )
        return
      if status.get("state") in ("no_common_protocol", "handshake_not_supported"):
        self.sig.sig_rx_log.emit(
            "WARN", "对端未声明可用的 Runtime Session 握手能力。"
        )
        return
      if status.get("state") == "disconnected":
        return
      if attempt < 3:
        time.sleep(0.25)
    self.sig.sig_rx_log.emit(
        "WARN",
        "Runtime Session 能力协商未得到确认；对端可能是旧版本，也可能是链路暂时无响应。",
    )

  def _handle_session_hello(self, frame: ABFPFrame):
    try:
      hello = decode_session_message(FrameType.SESSION_HELLO, frame.payload)
      response = make_session_capabilities(hello["message_id"], CLIENT_VERSION)
      encoded = encode_session_message(FrameType.SESSION_CAPABILITIES, response)
      self.send_frame(FrameType.SESSION_CAPABILITIES, encoded)

      # If this peer previously exhausted its startup attempts before the
      # remote Runtime became ready, an inbound HELLO proves the peer is now
      # participating. Re-negotiate so this side can learn the peer's feature
      # set too; replying to the inbound HELLO alone only informs the peer.
      with self.session_protocol_lock:
        recover_unconfirmed_peer = (
            self.session_peer_status.get("state") == "unconfirmed"
            and not self.session_pending_handshakes
        )
        if recover_unconfirmed_peer:
          # Prevent multiple inbound HELLOs from starting duplicate recovery
          # threads in the short interval before the worker sends its HELLO.
          self.session_peer_status["state"] = "negotiating"
          self.session_peer_status["error"] = None
      if recover_unconfirmed_peer:
        self.sig.sig_rx_log.emit(
            "INFO",
            "收到对端 Runtime Session HELLO；本机先前握手未确认，重新发起能力协商。",
        )
        self._start_auto_session_negotiation()
    except SessionProtocolError as exc:
      self.sig.sig_rx_log.emit("WARN", f"Runtime Session HELLO 无效: {exc}")

  def _handle_session_capabilities(self, frame: ABFPFrame):
    try:
      capabilities = decode_session_message(
          FrameType.SESSION_CAPABILITIES, frame.payload
      )
    except SessionProtocolError as exc:
      self.sig.sig_rx_log.emit("WARN", f"Runtime Session CAPABILITIES 无效: {exc}")
      return

    correlation_id = capabilities.get("correlation_id")
    with self.session_protocol_lock:
      pending = self.session_pending_handshakes.get(correlation_id)
      if pending is None:
        self.sig.sig_rx_log.emit(
            "WARN", "收到未关联到当前 HELLO 的 Runtime Session CAPABILITIES，已忽略。"
        )
        return
      payload = capabilities["payload"]
      supported = payload.get("supported_protocols", [])
      features = list(payload.get("features", []))
      protocol_common = SESSION_PROTOCOL_ID in supported
      handshake_supported = "handshake" in features
      common = protocol_common and handshake_supported
      if common:
        state = "negotiated"
      elif not protocol_common:
        state = "no_common_protocol"
      else:
        state = "handshake_not_supported"
      if self.session_peer_status.get("last_handshake_id") == correlation_id:
        self.session_peer_status = {
            "state": state,
            "negotiated": common,
            "protocol": SESSION_PROTOCOL_ID,
            "supported_protocols": list(supported),
            "features": features,
            "profiles": list(payload.get("profiles", [])),
            "max_frame_payload": payload.get("max_frame_payload"),
            "peer_version": payload.get("server_version"),
            "last_handshake_id": correlation_id,
            "error": None if common else state,
        }
      pending["response"] = {"state": state, "payload": payload}
      pending["event"].set()

  def send_frame(self, frame_type: int, payload: bytes = b""):
    self.seq_counter = (self.seq_counter + 1) & 0xFFFF
    frame = ABFPFrame(frame_type, self.seq_counter, payload)
    # 高优先级控制帧（ACK/NACK/ABORT/HEARTBEAT/Session 握手）入高优队列，防止被大文件数据包队头阻塞
    if frame_type in (
        FrameType.ACK,
        FrameType.NACK,
        FrameType.FILE_ABORT,
        FrameType.HEARTBEAT,
        FrameType.SESSION_HELLO,
        FrameType.SESSION_CAPABILITIES,
    ):
      self.tx_high_queue.put(frame)
    else:
      self.tx_low_queue.put(frame)

  def send_fs_request_sync(
      self, req_dict: Dict[str, Any], timeout: float = 15.0
  ) -> Dict[str, Any]:
    wait_event = threading.Event()
    with self.fs_pending_lock:
      req_id = int(time.time() * 1000) & 0x7FFFFFFF
      while req_id in self.fs_pending_requests:
        req_id = (req_id + 1) & 0x7FFFFFFF
      req_dict["req_id"] = req_id
      self.fs_pending_requests[req_id] = {
          "event": wait_event,
          "response": None,
      }

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
      with self.window_cv:
        self.window_cv.notify_all()
      self.ack_event.set()

  def _on_chunk_ack_received(self, chunk_idx: int):
    """滑动窗口 ACK 响应回调：推进窗口右滑，并自适应测量 RTT 进行弹性伸缩"""
    with self.window_cv:
      if chunk_idx not in self.acked_chunks:
        self.acked_chunks.add(chunk_idx)
        info = self.unacked_chunks.pop(chunk_idx, None)

        # 动态自适应 RTT 测速：自动识别 USB CDC 高速通道与慢速物理串口
        if info and info.get("tx_time", 0.0) > 0.0:
          rtt = time.time() - info["tx_time"]
          if rtt < 0.08:
            # 极低延时 (<80ms)，判定为 USB CDC / 虚拟串口高速通道，逐步放开窗口
            if self.sliding_window_size < 16:
              self.sliding_window_size += 1
          elif rtt > 0.35:
            # 较长延时 (>350ms)，判定为 115200 等窄带物理串口，收敛窗口以减轻信道压力
            if self.sliding_window_size > 4:
              self.sliding_window_size = 4

        while self.window_base in self.acked_chunks:
          self.window_base += 1
        if self.tx_total_chunks > 0:
          self.sig.sig_file_progress.emit(len(self.acked_chunks), self.tx_total_chunks)
        self.window_cv.notify_all()

  def _on_chunk_nack_received(self, chunk_idx: int):
    """收到对端 NAK 快速负确认：无需等待 2.5s 超时，毫秒级就地重传缺失分片"""
    with self.window_cv:
      if chunk_idx in self.unacked_chunks:
        info = self.unacked_chunks[chunk_idx]
        if info["retries"] < 5:
          info["retries"] += 1
          info["tx_time"] = 0.0  # 重新入队，等待物理硬件真正写出时刷新时间戳
          payload_to_resend = info["payload"]
          retries = info["retries"]
          self.sig.sig_rx_log.emit(
              "WARN", f"[BLE快速重传] 收到对端 NAK，立刻重传分片 #{chunk_idx} ({retries}/5)..."
          )
          self.send_frame(FrameType.FILE_CHUNK, payload_to_resend)
          # 发生丢包时动态收敛窗口，保护弱网/蓝牙信道
          if self.sliding_window_size > 4:
            self.sliding_window_size = 4

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
    chunk_size = 4096  # 方案2：固定 4KB 高抗干扰分片
    total_chunks = math.ceil(filesize / chunk_size) if filesize > 0 else 1

    self.sig.sig_rx_log.emit(
        "INFO",
        f"启动大文件传输 [流水线模式]: {filename} ({filesize/1024/1024:.2f} MB, 共"
        f" {total_chunks} 包, 单包 4KB, 窗口深度 {self.sliding_window_size})...",
    )

    meta = json.dumps({
        "filename": filename,
        "filesize": filesize,
        "chunk_size": chunk_size,
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

    self.sig.sig_rx_log.emit("INFO", "对端就绪，开始流式滑动窗口全速推流...")

    # 初始化滑动窗口状态
    with self.window_cv:
      self.unacked_chunks.clear()
      self.acked_chunks.clear()
      self.window_base = 0
      self.tx_total_chunks = total_chunks

    chunk_timeout = 2.5      # 单分片物理重传超时 (仅计算真实出厂后的耗时，彻底排除队列排队延时)
    max_chunk_retries = 5    # 单分片最大重试次数

    with open(file_path, "rb") as f:
      next_seq = 0
      while len(self.acked_chunks) < total_chunks:
        if self.file_transfer_abort:
          self.send_frame(FrameType.FILE_ABORT, filename.encode("utf-8"))
          self.sig.sig_rx_log.emit("WARN", "传输已手动终止！")
          self.sig.sig_file_aborted.emit("传输已手动终止")
          self.is_sending_file = False
          return

        # 1. 窗口内快速推流（只要未超出滑动窗口深度且还有后续分片）
        while next_seq < total_chunks:
          with self.window_cv:
            if (next_seq - self.window_base) >= self.sliding_window_size:
              break

          f.seek(next_seq * chunk_size)
          data = f.read(chunk_size)
          chunk_header = (next_seq & 0xFFFF).to_bytes(2, byteorder="big")
          frame_payload = chunk_header + data

          with self.window_cv:
            self.unacked_chunks[next_seq] = {
                "payload": frame_payload,
                "tx_time": 0.0,  # 0.0 表示尚在串口队列排队，未真正写入物理线缆，不计入超时倒计时！
                "retries": 0,
            }

          self.send_frame(FrameType.FILE_CHUNK, frame_payload)
          next_seq += 1

        # 2. 检查窗口内已真正写入物理串口但尚未确认的分片是否超时
        now = time.time()
        resend_list = []
        with self.window_cv:
          for idx, info in list(self.unacked_chunks.items()):
            tx_time = info.get("tx_time", 0.0)
            if tx_time <= 0.0:
              continue  # 关键保障：仍在串口排队中，硬件尚未发出，绝不误判超时！
            if now - tx_time > chunk_timeout:
              if info["retries"] >= max_chunk_retries:
                self.sig.sig_rx_log.emit(
                    "ERROR", f"【物理中断】分片 #{idx} 重试超限 ({max_chunk_retries} 次)！"
                )
                self.send_frame(FrameType.FILE_ABORT, filename.encode("utf-8"))
                self.sig.sig_file_aborted.emit(f"分片 #{idx} 重传超限")
                self.is_sending_file = False
                return
              resend_list.append((idx, info["payload"], info["retries"] + 1))
              info["tx_time"] = 0.0  # 重发包重新进入排队等待物理发送状态
              info["retries"] += 1

        # 针对超时的分片重发
        for r_idx, r_payload, r_retry_cnt in resend_list:
          self.sig.sig_rx_log.emit(
              "WARN", f"分片 #{r_idx} 超时重试 ({r_retry_cnt}/{max_chunk_retries})..."
          )
          self.send_frame(FrameType.FILE_CHUNK, r_payload)

        # 3. 阻塞挂起等待：窗口占满或全部发完等待对端 ACK 收尾
        with self.window_cv:
          need_wait = (
              (next_seq - self.window_base >= self.sliding_window_size)
              or (next_seq >= total_chunks and len(self.acked_chunks) < total_chunks)
          )
          if need_wait and not self.file_transfer_abort:
            self.window_cv.wait(timeout=0.03)  # 30ms 细粒度轮转兼顾 ACK 唤醒与超时扫描

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
      had_activity = False
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
            had_activity = True
        except (OSError, serial.SerialException) as e:
          self.sig.sig_rx_log.emit("WARN", f"物理链路断开: {e}")
          self.disconnect_serial()
          continue

      # BLE 增强：处理延时冗余 ACK（抵御蓝牙透传信道逆向丢包）
      if is_open and self.pending_dup_acks:
        now_t = time.time()
        ready_acks = [ack for ack, due in self.pending_dup_acks if now_t >= due]
        self.pending_dup_acks = [(ack, due) for ack, due in self.pending_dup_acks if now_t < due]
        for ack_tag in ready_acks:
          self.send_frame(FrameType.ACK, ack_tag)
          had_activity = True

      if is_open:
        # 优先级流控调度：控制帧（ACK/NACK/ABORT）优先出队，避免大文件数据帧队头阻塞
        frame = None
        try:
          frame = self.tx_high_queue.get_nowait()
        except queue.Empty:
          try:
            frame = self.tx_low_queue.get_nowait()
          except queue.Empty:
            frame = None

        if frame is not None:
          try:
            with self.lock:
              if self.ser and self.ser.is_open:
                self.ser.write(frame.pack())
                self.sig.sig_tx_activity.emit()
            had_activity = True

            # 核心机制：真实物理硬件写入时间戳注入，彻底剥离软件队列排队延时
            if frame.frame_type == FrameType.FILE_CHUNK and len(frame.payload) >= 2:
              chunk_idx = int.from_bytes(frame.payload[:2], byteorder="big")
              with self.window_cv:
                if chunk_idx in self.unacked_chunks:
                  self.unacked_chunks[chunk_idx]["tx_time"] = time.time()
          except (OSError, serial.SerialException) as e:
            self.sig.sig_rx_log.emit("WARN", f"写入异常: {e}")
            self.disconnect_serial()
            continue

      if had_activity:
        time.sleep(0.0005)
      else:
        time.sleep(0.003)

  def _handle_incoming_frame(self, frame: ABFPFrame):
    if frame.frame_type == FrameType.SESSION_HELLO:
      self._handle_session_hello(frame)

    elif frame.frame_type == FrameType.SESSION_CAPABILITIES:
      self._handle_session_capabilities(frame)

    elif frame.frame_type == FrameType.ACK:
      if self.expected_ack_tag and frame.payload == self.expected_ack_tag:
        self.ack_event.set()
      # 支持流水线滑动窗口的分块 ACK
      if frame.payload.startswith(b"CHUNK_") and len(frame.payload) == 8:
        ack_idx = int.from_bytes(frame.payload[6:8], byteorder="big")
        self._on_chunk_ack_received(ack_idx)

    elif frame.frame_type == FrameType.NACK:
      # BLE 增强：处理对端快速 NAK 负确认
      if len(frame.payload) >= 2:
        nak_idx = int.from_bytes(frame.payload[:2], byteorder="big")
        self._on_chunk_nack_received(nak_idx)

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
      self.rx_filesize = meta.get("filesize", 0)
      self.rx_chunk_size = meta.get("chunk_size", 4096)
      self.rx_total_chunks = meta["total_chunks"]
      dest_dir = meta.get("dest_dir", "").strip()
      self.rx_target_dir = (
          dest_dir
          if dest_dir and os.path.isdir(dest_dir)
          else self.default_recv_dir
      )
      os.makedirs(self.rx_target_dir, exist_ok=True)
      self.rx_recv_chunks = 0
      self.rx_received_set = set()
      self.rx_expected_chunk = 0
      self.rx_nak_sent_time.clear()
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
        chunk_idx = int.from_bytes(chunk_header, byteorder="big")
        chunk_data = frame.payload[2:]

        # BLE 增强：快速 NAK 探测。若检测到序号跳变（空洞），立即主动索取丢失的分片
        if chunk_idx > self.rx_expected_chunk and self.rx_expected_chunk not in self.rx_received_set:
          now_n = time.time()
          if now_n - self.rx_nak_sent_time.get(self.rx_expected_chunk, 0) > 0.25:
            self.rx_nak_sent_time[self.rx_expected_chunk] = now_n
            self.send_frame(FrameType.NACK, self.rx_expected_chunk.to_bytes(2, byteorder="big"))

        # 基于分片索引随机寻址幂等写入，完全适应乱序或重传分片
        if chunk_idx not in self.rx_received_set:
          self.rx_file_handle.seek(chunk_idx * self.rx_chunk_size)
          self.rx_file_handle.write(chunk_data)
          self.rx_received_set.add(chunk_idx)
          while self.rx_expected_chunk in self.rx_received_set:
            self.rx_expected_chunk += 1
          self.rx_recv_chunks = len(self.rx_received_set)
          self.sig.sig_file_progress.emit(
              self.rx_recv_chunks, self.rx_total_chunks
          )

        # 立即回送当前分片 ACK
        self.send_frame(FrameType.ACK, b"CHUNK_" + chunk_header)
        # BLE 增强：延时 8ms 预约一次微冗余 ACK，抗击下行单向饱和导致的逆向 ACK 丢失
        if len(self.pending_dup_acks) < 16:
          self.pending_dup_acks.append((b"CHUNK_" + chunk_header, time.time() + 0.008))

    elif frame.frame_type == FrameType.FILE_END:
      if self.rx_file_handle:
        if self.rx_filesize > 0:
          self.rx_file_handle.truncate(self.rx_filesize)
        self.rx_file_handle.flush()
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

    elif frame.frame_type == FrameType.EDA_TASK_FIN:
      try:
        result = json.loads(frame.payload.decode("utf-8", errors="replace"))
        request_id = result.get("request_id")
        response_state = (
            "unknown"
            if result.get("request_state") == "unknown"
            else "failed"
            if (
                result.get("request_state") == "failed"
                or isinstance(result.get("error"), dict)
                or result.get("returncode", 0) not in (0, None)
            )
            else "completed"
        )
        if request_id and self.request_tracker.complete(
            request_id, result, state=response_state
        ):
          self.sig.sig_rx_log.emit(
              "EDA",
              f"请求 {request_id} 状态={response_state}，returncode={result.get('returncode')}",
          )
        else:
          self.sig.sig_rx_log.emit(
              "EDA",
              f"收到未关联或重复的 EDA_TASK_FIN: {json.dumps(result, ensure_ascii=False)[:500]}",
          )
      except Exception as exc:
        self.sig.sig_rx_log.emit("ERROR", f"EDA_TASK_FIN 解码失败: {exc}")

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
                task_data.get("keep_open", False),
                task_data.get("request_id"),
            ),
            daemon=True,
        ).start()
      except Exception as e:
        self.send_frame(
            FrameType.EDA_LOG_STREAM, f"[Fatal] 载荷解析失败: {e}".encode("utf-8")
        )

    elif frame.frame_type == FrameType.EDA_APP_CLOSE:
      try:
        close_data = json.loads(frame.payload.decode("utf-8"))
        application = close_data.get("application", "")
        self.sig.sig_rx_log.emit(
            "EDA", f"[任务调度] 收到关闭 Cadence 应用请求: {application}"
        )
        threading.Thread(
            target=self._close_eda_application_async,
            args=(application,),
            daemon=True,
        ).start()
      except Exception as e:
        self.send_frame(
            FrameType.EDA_LOG_STREAM,
            f"[EDA Close] 关闭请求解析失败: {e}".encode("utf-8"),
        )

    elif frame.frame_type == FrameType.EDA_FORCE_KILL:
      self.sig.sig_rx_log.emit("WARN", "[任务调度] 收到强杀指令，正在终止进程...")
      self.eda_executor.kill_active_task()

    # ---------- Remote Shell ----------
    elif frame.frame_type == FrameType.SHELL_EXEC:
      try:
        req = json.loads(frame.payload.decode("utf-8", errors="replace"))
      except Exception:
        req = {}
      threading.Thread(target=self._handle_shell_exec, args=(req,), daemon=True).start()

    elif frame.frame_type == FrameType.SHELL_KILL:
      try:
        req = json.loads(frame.payload.decode("utf-8", errors="replace"))
      except Exception:
        req = {}
      res = self.shell_engine.kill_task(
          task_id=req.get("task_id", ""),
          pid=int(req.get("pid", 0) or 0),
      )
      self.send_frame(
          FrameType.SHELL_RESULT,
          json.dumps(res, ensure_ascii=False).encode("utf-8"),
      )

    elif frame.frame_type == FrameType.SHELL_LIST:
      tasks = self.shell_engine.list_tasks()
      self.send_frame(
          FrameType.SHELL_LIST_RESP,
          json.dumps({"success": True, "tasks": tasks}, ensure_ascii=False).encode("utf-8"),
      )

    elif frame.frame_type == FrameType.SHELL_TASK_GET:
      # Read-only query: return the current state of an existing Shell task.
      try:
        req = json.loads(frame.payload.decode("utf-8", errors="replace"))
      except Exception:
        req = {}
      request_id = req.get("request_id")
      try:
        request_id = str(uuid.UUID(request_id))
      except (ValueError, TypeError, AttributeError):
        self.sig.sig_rx_log.emit("WARN", "Shell task query 缺少有效 request_id，已忽略")
        return

      task_id = req.get("task_id", "")
      try:
        result = self.shell_engine.get_task_result(task_id)
      except Exception as exc:
        result = {
            "success": False,
            "request_state": "unknown",
            "error": {"code": "SHELL_TASK_QUERY_ERROR", "message": str(exc)},
        }

      if result.get("success") is False and result.get("error") == "任务不存在":
        result["request_state"] = "unknown"
      elif result.get("finished") is False:
        result["request_state"] = "accepted"
      else:
        result.setdefault("request_state", "completed")
      result["request_id"] = request_id
      try:
        response_payload = encode_bounded_json_payload(result, MAX_PAYLOAD_LEN)
      except RequestTrackerError as exc:
        response_payload = encode_bounded_json_payload({
            "request_id": request_id,
            "success": False,
            "request_state": "failed",
            "error": {
                "code": "RESULT_ENCODING_ERROR",
                "message": str(exc),
            },
        }, MAX_PAYLOAD_LEN)
      self.send_frame(FrameType.SHELL_RESULT, response_payload)

    elif frame.frame_type == FrameType.SHELL_OUTPUT:
      # 对端推来的实时输出，转成日志显示
      try:
        obj = json.loads(frame.payload.decode("utf-8", errors="replace"))
        tid = obj.get("task_id", "")
        stream = obj.get("stream", "stdout")
        line = obj.get("line", "")
        self.sig.sig_rx_log.emit("SHELL", f"[{tid}][{stream}] {line}")
      except Exception:
        pass

    elif frame.frame_type == FrameType.SHELL_RESULT:
      try:
        obj = json.loads(frame.payload.decode("utf-8", errors="replace"))
        request_id = obj.get("request_id")
        response_state = (
            "unknown"
            if obj.get("request_state") == "unknown"
            else "failed"
            if obj.get("request_state") == "failed" or obj.get("success") is False and obj.get("error")
            else "accepted"
            if (
                obj.get("finished") is False
                or obj.get("status") == "running" and obj.get("async") is True
            )
            else "completed"
        )
        if request_id and self.request_tracker.complete(
            request_id, obj, state=response_state
        ):
          self.sig.sig_rx_log.emit(
              "SHELL",
              f"请求 {request_id} 已返回 task_id={obj.get('task_id', '')}",
          )
        else:
          self.sig.sig_rx_log.emit(
              "SHELL",
              f"任务结果: {json.dumps(obj, ensure_ascii=False)[:500]}",
          )
      except Exception as exc:
        self.sig.sig_rx_log.emit("ERROR", f"SHELL_RESULT 解码失败: {exc}")

    elif frame.frame_type == FrameType.SHELL_LIST_RESP:
      try:
        obj = json.loads(frame.payload.decode("utf-8", errors="replace"))
        self.sig.sig_rx_log.emit(
            "SHELL",
            f"任务列表: {len(obj.get('tasks', []))} 个",
        )
      except Exception:
        pass

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

  def _ensure_runtime_registry(self):
    """Return the local generic registry, including for lightweight test workers."""
    registry = getattr(self, "runtime_registry", None)
    if registry is None:
      registry = create_legacy_runtime_registry(
          lambda: self.eda_executor,
          lambda: self.shell_engine,
      )
      self.runtime_registry = registry
    return registry

  def _exec_eda_async(
      self,
      script: str,
      hint: str,
      log_fn,
      version: str = "auto",
      target_file: str = "",
      timeout: int = 60,
      keep_open: bool = False,
      request_id: Optional[str] = None,
  ):
    def execute_script() -> Dict[str, Any]:
      request = RuntimeExecutionRequest(
          operation="execute",
          request_id=request_id,
          payload={
              "script": script,
              "hint": hint,
              "version": version,
              "target_file": target_file,
              "timeout": timeout,
              "keep_open": keep_open,
          },
      )
      profile_id = select_eda_profile(
          script,
          hint,
          capture_session_enabled=getattr(
              self, "capture_tcl_adapter_registered", False
          ),
      )
      return self._ensure_runtime_registry().execute(
          profile_id,
          request,
          RuntimeExecutionContext(log_callback=log_fn),
      )

    if request_id:
      broker_payload = {
          "script": script,
          "hint": hint,
          "version": version,
          "target_file": target_file,
          "timeout": timeout,
          "keep_open": keep_open,
      }
      try:
        outcome = self.request_broker.execute(
            request_id,
            "eda.execute",
            broker_payload,
            execute_script,
        )
      except Exception as exc:
        outcome = {
            "disposition": "unknown",
            "state": "unknown",
            "error": {
                "code": "BROKER_STORAGE_ERROR",
                "message": str(exc),
            },
        }

      if outcome.get("disposition") == "in_progress":
        # The original worker owns execution and will publish the single result.
        return

      if outcome.get("error"):
        response = {
            "returncode": -1,
            "request_state": "failed",
            "error": outcome["error"],
        }
      else:
        response = dict(outcome.get("result") or {})
        if outcome.get("state") == "unknown":
          response["request_state"] = "unknown"
          response.setdefault("returncode", -1)
        elif outcome.get("state") == "failed":
          response["request_state"] = "failed"
          response.setdefault("returncode", -1)

      response["request_id"] = request_id
      try:
        encoded = encode_bounded_json_payload(response, MAX_PAYLOAD_LEN)
      except RequestTrackerError as exc:
        fallback = {
            "request_id": request_id,
            "returncode": -1,
            "request_state": "failed",
            "error": {
                "code": "RESULT_ENCODING_ERROR",
                "message": str(exc),
            },
        }
        encoded = encode_bounded_json_payload(fallback, MAX_PAYLOAD_LEN)
      self.send_frame(FrameType.EDA_TASK_FIN, encoded)
      return

    # Legacy GUI-originated one-shot task: preserve the old response shape.
    try:
      result = execute_script()
      response = {"returncode": result["returncode"]}
    except Exception as exc:
      response = {"returncode": -1}
      log_fn(f"[AirBridge] EDA execution failed: {exc}")
    self.send_frame(
        FrameType.EDA_TASK_FIN,
        json.dumps(response).encode("utf-8"),
    )


# ==============================================================================

  def _close_eda_application_async(self, application: str):
    """Close a previously launched Cadence GUI without force-killing it."""
    def stream_log(line: str):
      self.send_frame(FrameType.EDA_LOG_STREAM, line.encode("utf-8"))

    try:
      app_key = (application or "").strip().lower()
      if app_key == "capture":
        adapter = getattr(self, "capture_tcl_adapter", None)
        if adapter is None:
          stream_log(
              "[EDA Close] Capture Runtime Adapter 不可用；"
              "不会尝试关闭用户手动启动的 Capture"
          )
          return
        success, message = adapter.close_session(
            RuntimeExecutionContext(log_callback=stream_log)
        )
        if not success:
          stream_log(message)
        return
      self.eda_executor.close_application(application, stream_log)
    except Exception as exc:
      stream_log(f"[EDA Close] 关闭 {application} 时发生异常: {exc}")

  def _handle_shell_exec(self, req: Dict[str, Any]):
    """Execute a remote Shell request through the generic B-side request broker."""
    command = req.get("command") or req.get("cmd") or ""
    shell = req.get("shell", "powershell")
    cwd = req.get("cwd", "")
    try:
      timeout = int(req.get("timeout", 60) or 60)
    except (TypeError, ValueError):
      timeout = 60
    async_mode = bool(req.get("async", False))
    env = req.get("env") or {}
    safe_env = env if isinstance(env, dict) else {}

    request_id = req.get("request_id")

    def execute_shell() -> Dict[str, Any]:
      request = RuntimeExecutionRequest(
          operation="execute",
          request_id=request_id,
          payload={
              "command": command,
              "shell": shell,
              "cwd": cwd,
              "timeout": timeout,
              "async": async_mode,
              "env": safe_env,
          },
      )
      return self._ensure_runtime_registry().execute(
          "legacy-shell-process",
          request,
      )
    if request_id:
      broker_payload = {
          key: value
          for key, value in req.items()
          if key not in ("request_id", "wait_timeout_ms")
      }
      try:
        outcome = self.request_broker.execute(
            request_id,
            "shell.exec",
            broker_payload,
            execute_shell,
        )
      except Exception as exc:
        outcome = {
            "disposition": "unknown",
            "state": "unknown",
            "error": {
                "code": "BROKER_STORAGE_ERROR",
                "message": str(exc),
            },
        }

      if outcome.get("disposition") == "in_progress":
        # A duplicate frame must not launch a second process. The original
        # request thread will deliver its result when execution returns.
        return

      if outcome.get("error"):
        res = {
            "success": False,
            "request_state": "failed",
            "error": outcome["error"],
        }
      else:
        res = dict(outcome.get("result") or {})
        if outcome.get("state") == "unknown":
          res["request_state"] = "unknown"
          res.setdefault("success", False)
        elif outcome.get("state") == "failed":
          res["request_state"] = "failed"
          res.setdefault("success", False)
      res["request_id"] = request_id
    else:
      # Legacy uncorrelated GUI request: keep the previous direct behavior.
      try:
        res = execute_shell()
      except Exception as exc:
        res = {"success": False, "error": str(exc)}

    # Sync result or async-start acknowledgement is returned as one bounded frame.
    try:
      response_payload = encode_bounded_json_payload(res, MAX_PAYLOAD_LEN)
    except RequestTrackerError as exc:
      bounded = {
          "success": False,
          "request_id": request_id,
          "error": {
              "code": "RESULT_ENCODING_ERROR",
              "message": f"result exceeds ABFP frame capacity: {exc}",
          },
      }
      response_payload = encode_bounded_json_payload(bounded, MAX_PAYLOAD_LEN)
    self.send_frame(FrameType.SHELL_RESULT, response_payload)
    if not async_mode:
      if res.get("success"):
        self.sig.sig_rx_log.emit(
            "SHELL",
            f"[{res.get('task_id')}] exit={res.get('exit_code')} "
            f"stdout_len={len(res.get('stdout') or '')}",
        )
      else:
        self.sig.sig_rx_log.emit(
            "SHELL",
            f"执行失败: {res.get('error', res)}",
        )


# 北向 REST API 服务器
# ==============================================================================
class AntigravityIPCServer:

  def __init__(
    self,
    worker: SerialWorker,
    port: int = 8888,
    security_store: Optional[AgentSecurityStore] = None,
    allow_eda_execute: Optional[bool] = None,
    read_requests_per_minute: Optional[int] = None,
    status_requests_per_minute: Optional[int] = None,
    execute_requests_per_minute: Optional[int] = None,
    max_pending_eda_per_agent: Optional[int] = None,
  ):
    self.worker = worker
    self.port = port
    self._owns_security_store = security_store is None
    self.security_store = (
        security_store if security_store is not None else AgentSecurityStore.from_env()
    )
    self._stop_lock = threading.Lock()
    self._handler_condition = threading.Condition()
    self._active_handlers = 0
    self._shutting_down = False
    self._shutdown_timeout_seconds = 5.0
    if allow_eda_execute is None:
      allow_eda_execute = os.environ.get(
          "AIRBRIDGE_ALLOW_EDA_EXECUTE", "0"
      ).strip().lower() in ("1", "true", "yes", "on")
    self.allow_eda_execute = bool(allow_eda_execute)

    def _positive_limit(value: Optional[int], env_name: str, default: int)
      raw = os.environ.get(env_name, str(default)) if value is None else value
      try:
        parsed = int(raw)
      except (TypeError, ValueError) as exc:
        raise ValueError(f"{env_name} must be a positive integer") from exc
      if isinstance(raw, bool) or parsed < 1:
        raise ValueError(f"{env_name} must be a positive integer")
      return parsed

    self.read_requests_per_minute = _positive_limit(
        read_requests_per_minute, "AIRBRIDGE_AGENT_READ_RPM", 60
    )
    self.status_requests_per_minute = _positive_limit(
        status_requests_per_minute, "AIRBRIDGE_AGENT_STATUS_RPM", 120
    )
    self.execute_requests_per_minute = _positive_limit(
        execute_requests_per_minute, "AIRBRIDGE_AGENT_EXECUTE_RPM", 5
    )
    self.max_pending_eda_per_agent = _positive_limit(
        max_pending_eda_per_agent, "AIRBRIDGE_AGENT_MAX_PENDING_EDA", 1
    )
    self._server = None
    self._server_thread = None
    self._server_ready = threading.Event()
    self._server_error = None

  def _handler_finished(self):
    with self._handler_condition:
      if self._active_handlers > 0:
        self._active_handlers -= 1
      self._handler_condition.notify_all()

  def start(self):
    worker_ref = self.worker
    server_owner = self
    security_ref = self.security_store
    allow_eda_execute = self.allow_eda_execute
    read_requests_per_minute = self.read_requests_per_minute
    status_requests_per_minute = self.status_requests_per_minute
    execute_requests_per_minute = self.execute_requests_per_minute
    max_pending_eda_per_agent = self.max_pending_eda_per_agent

    class Handler(BaseHTTPRequestHandler):

      def log_message(self, format, *args):
        pass

      def _send_json(self, status_code: int, data: dict, extra_headers: Optional[dict] = None):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for header_name, header_value in (extra_headers or {}).items():
          self.send_header(str(header_name), str(header_value))
        self.end_headers()
        self.wfile.write(body)

      def _reply_json(self, status_code: int, data: dict, extra_headers: Optional[dict] = None):
        # Admission is durably recorded by _authorize_api before an API handler
        # may touch the Runtime. A failure to write the final outcome must not
        # be converted into an apparently successful HTTP response.
        if (
            getattr(self, "_audit_path", "").startswith("/api/")
            and not getattr(self, "_audit_preflight_failed", False)
        ):
          try:
            security_ref.audit_request(
                agent_id=getattr(getattr(self, "_principal", None), "agent_id", None),
                token_id=getattr(getattr(self, "_principal", None), "token_id", None),
                method=getattr(self, "_audit_method", "GET"),
                path=self._audit_path,
                decision=getattr(self, "_audit_decision", "denied"),
                http_status=status_code,
                reason_code=getattr(self, "_audit_reason", None),
                request_id=getattr(self, "_audit_request_id", None),
                duration_ms=int((time.perf_counter() - self._audit_started) * 1000)
                if hasattr(self, "_audit_started") else None,
                payload_bytes=getattr(self, "_audit_payload_bytes", None),
                payload_sha256=getattr(self, "_audit_payload_sha256", None),
            )
          except Exception:
            request_id = getattr(self, "_audit_request_id", None)
            failure = {
                "success": False,
                "state": "unknown",
                "error": {
                    "code": "AGENT_AUDIT_FINALIZATION_FAILED",
                    "message": (
                        (
                            "The request may already have been admitted. Query this request_id; "
                            "do not submit the operation again."
                            if request_id else
                            "The operation outcome is unknown because its final audit record "
                            "could not be persisted. Do not repeat a non-idempotent operation blindly."
                        )
                    ),
                },
            }
            if request_id:
              failure["request_id"] = request_id
            self._send_json(503, failure)
            return

        self._send_json(status_code, data, extra_headers)

      @staticmethod
      def _required_scope(method: str, path: str) -> Optional[str]:
        if method == "GET":
          if path == "/api/v1/runtime/capabilities":
            return "runtime.read"
          if path == "/api/v1/eda/versions":
            return "eda.versions.read"
          if path.startswith("/api/v1/requests/"):
            return "request.read"
          if path.startswith("/api/v1/fs/"):
            return "filesystem.read"
          if path.startswith("/api/v1/shell/"):
            return "shell.read"
        elif method == "POST":
          if path == "/api/v1/runtime/negotiate":
            return "runtime.manage"
          if path in ("/api/v1/eda/execute", "/api/eda/exec"):
            return "eda.execute"
          if path == "/api/v1/eda/versions":
            return "eda.versions.read"
          if path in ("/api/v1/shell/exec", "/api/shell/exec"):
            return "shell.execute"
          if path in ("/api/v1/shell/kill", "/api/shell/kill"):
            return "shell.manage"
          if path in (
              "/api/v1/shell/tasks", "/api/v1/shell/list", "/api/shell/tasks"
          ):
            return "shell.read"
          if path == "/api/v1/message":
            return "runtime.message"
          if path.startswith("/api/v1/fs/"):
            fs_action = path.rsplit("/", 1)[-1]
            if fs_action in ("drives", "ls", "read", "stat", "pull"):
              return "filesystem.read"
            if fs_action in ("write", "mkdir", "rm", "mv", "exec"):
              return "filesystem.write"
        # Fail closed for new or unclassified /api routes. An explicit
        # administrator scope is required until the route is classified.
        return "api.admin"

      def _authorize_api(self, method: str, path: str) -> bool:
        if not path.startswith("/api/"):
          return True
        self._audit_method = method
        self._audit_path = path
        self._audit_started = time.perf_counter()
        self._audit_decision = "denied"
        self._audit_reason = None
        self._audit_request_id = (
            path.rsplit("/", 1)[-1]
            if (
                path.startswith("/api/v1/requests/")
                or path.startswith("/api/v1/shell/tasks/by-request/")
            )
            else None
        )
        self._principal = None

        try:
          principal = security_ref.authenticate(self.headers.get("Authorization"))
        except AgentAuthenticationError:
          self._audit_reason = "INVALID_AGENT_TOKEN"
          self._reply_json(401, {
              "success": False,
              "state": "unauthorized",
              "error": {
                  "code": "INVALID_AGENT_TOKEN",
                  "message": "A valid AirBridge Agent Bearer token is required.",
              },
          })
          return False
        except Exception:
          self._audit_reason = "AGENT_AUTH_SERVICE_UNAVAILABLE"
          self._reply_json(503, {
              "success": False,
              "state": "unavailable",
              "error": {
                  "code": "AGENT_AUTH_SERVICE_UNAVAILABLE",
                  "message": "Agent authentication service is unavailable.",
              },
          })
          return False

        self._principal = principal
        required_scope = self._required_scope(method, path)
        if not principal.allows(required_scope):
          self._audit_reason = "AGENT_SCOPE_DENIED"
          self._reply_json(403, {
              "success": False,
              "state": "forbidden",
              "error": {
                  "code": "AGENT_SCOPE_DENIED",
                  "required_scope": required_scope,
              },
          })
          return False

        if required_scope == "eda.execute" and not allow_eda_execute:
          self._audit_reason = "EDA_EXECUTION_DISABLED_SERVER"
          self._reply_json(403, {
              "success": False,
              "state": "disabled",
              "error": {
                  "code": "EDA_EXECUTION_DISABLED_SERVER",
                  "message": "EDA execution is disabled by the AirBridge server policy.",
              },
          })
          return False

        if required_scope == "request.read":
          quota_action = "status"
          quota_limit = status_requests_per_minute
        elif required_scope == "eda.execute":
          quota_action = "execute"
          quota_limit = execute_requests_per_minute
        else:
          quota_action = "api"
          quota_limit = read_requests_per_minute
        retry_after = security_ref.consume_rate_limit(
            principal.agent_id,
            quota_action,
            limit=quota_limit,
            window_seconds=60.0,
        )
        if retry_after:
          self._audit_reason = "AGENT_RATE_LIMITED"
          self._reply_json(429, {
              "success": False,
              "state": "rate_limited",
              "error": {
                  "code": "AGENT_RATE_LIMITED",
                  "action": quota_action,
                  "retry_after_seconds": retry_after,
              },
          }, extra_headers={"Retry-After": str(retry_after)})
          return False

        request_owner_required = (
            required_scope == "request.read"
            or (
                method == "GET"
                and path.startswith("/api/v1/shell/tasks/by-request/")
            )
        )
        if request_owner_required and not security_ref.can_read_request(
            self._audit_request_id or "", principal
        ):
          # Return the same result for unknown and foreign request IDs.
          self._audit_reason = "REQUEST_NOT_FOUND_OR_NOT_OWNED"
          self._reply_json(404, {
              "request_id": self._audit_request_id,
              "state": "not_found",
              "error": "request_id is unknown or expired",
          })
          return False

        self._audit_decision = "allowed"
        try:
          # Persist an admission record before the API handler can reach the
          # Runtime. If audit storage is unavailable, fail closed.
          security_ref.audit_request(
              agent_id=principal.agent_id,
              token_id=principal.token_id,
              method=method,
              path=path,
              decision="admitted",
              http_status=200,
              reason_code="REQUEST_ADMITTED",
              request_id=self._audit_request_id,
          )
        except Exception:
          self._audit_preflight_failed = True
          self._audit_decision = "denied"
          self._audit_reason = "AGENT_AUDIT_UNAVAILABLE"
          self._reply_json(503, {
              "success": False,
              "state": "unavailable",
              "error": {
                  "code": "AGENT_AUDIT_UNAVAILABLE",
                  "message": "Audit storage is unavailable; request was not admitted.",
              },
          })
          return False
        return True

      def _set_payload_audit(self, raw_body: str):
        encoded = raw_body.encode("utf-8")
        self._audit_payload_bytes = len(encoded)
        self._audit_payload_sha256 = hashlib.sha256(encoded).hexdigest()

      def _set_request_audit(self, request_id: str):
        self._audit_request_id = request_id

      def do_GET(self):
        path = urlsplit(self.path).path
        if not self._authorize_api("GET", path):
          return
        if path == "/api/v1/runtime/capabilities":
          self._reply_json(200, worker_ref.get_session_peer_status())
        elif path.startswith("/api/v1/requests/"):
          request_id = path.rsplit("/", 1)[-1]
          self._set_request_audit(request_id)
          try:
            result = worker_ref.request_tracker.get(request_id)
          except RequestTrackerError as exc:
            self._reply_json(400, {"error": str(exc)})
            return
          status_code = (
              202 if result.get("state") in ("pending", "accepted")
              else 404 if result.get("state") == "not_found"
              else 503 if result.get("state") == "unknown"
              else 502 if result.get("state") == "failed"
              else 200
          )
          self._reply_json(status_code, result)
        elif path.startswith("/api/v1/shell/tasks/by-request/"):
          request_id = path.rsplit("/", 1)[-1]
          self._set_request_audit(request_id)
          try:
            original = worker_ref.request_tracker.get(request_id)
          except RequestTrackerError as exc:
            self._reply_json(400, {"error": str(exc)})
            return

          if original.get("state") == "not_found" or original.get("kind") != "shell.exec":
            self._reply_json(404, {
                "request_id": request_id,
                "state": "not_found",
                "error": "Shell request is unknown or expired",
            })
            return

          original_state = original.get("state")
          if original_state == "pending":
            self._reply_json(202, original)
            return
          if original_state == "unknown":
            self._reply_json(503, original)
            return
          if original_state == "failed":
            self._reply_json(502, original)
            return

          original_result = original.get("result") or {}
          task_id = original_result.get("task_id")
          if not task_id:
            self._reply_json(409, {
                "request_id": request_id,
                "state": "not_ready",
                "error": "The Shell request has no task_id to query yet.",
            })
            return

          if not worker_ref.is_serial_connected():
            self._reply_json(503, {
                "request_id": request_id,
                "state": "unavailable",
                "error": "serial link is disconnected; Shell task status was not queried",
            })
            return

          peer_status = worker_ref.get_session_peer_status()
          if (
              peer_status.get("state") != "negotiated"
              or "request.correlation" not in peer_status.get("features", [])
              or "shell.task.result-query" not in peer_status.get("features", [])
          ):
            self._reply_json(503, {
                "request_id": request_id,
                "state": "unsupported",
                "error": "peer has not confirmed shell.task.result-query capability",
                "peer_state": peer_status.get("state"),
                "peer_features": peer_status.get("features", []),
            })
            return

          query_id = str(uuid.uuid4())
          try:
            security_ref.bind_request_owner(query_id, self._principal.agent_id)
            worker_ref.request_tracker.register(query_id, "shell.task.get")
          except (AgentSecurityError, RequestTrackerError) as exc:
            security_ref.release_request_owner(query_id, self._principal.agent_id)
            self._reply_json(503, {
                "request_id": request_id,
                "state": "not_submitted",
                "error": str(exc),
            })
            return

          try:
            worker_ref.send_frame(
                FrameType.SHELL_TASK_GET,
                json.dumps({
                    "request_id": query_id,
                    "task_id": task_id,
                }, ensure_ascii=False).encode("utf-8"),
            )
          except Exception as exc:
            worker_ref.request_tracker.fail(query_id, str(exc), state="unknown")
            query_result = worker_ref.request_tracker.get(query_id)
            self._reply_json(503, {
                "request_id": request_id,
                "query_request_id": query_id,
                "task_id": task_id,
                "state": "unknown",
                "error": query_result.get("error") or str(exc),
            })
            return

          query_result = worker_ref.request_tracker.wait(query_id, 5.0)
          query_state = query_result.get("state")
          status_code = (
              200 if query_state == "completed"
              else 202 if query_state in ("pending", "accepted")
              else 503 if query_state == "unknown"
              else 502 if query_state == "failed"
              else 404
          )
          self._reply_json(status_code, {
              "request_id": request_id,
              "query_request_id": query_id,
              "task_id": task_id,
              "state": query_state,
              "result": query_result.get("result"),
              "error": query_result.get("error"),
          })

        elif path == "/api/v1/eda/versions":
          res = worker_ref.send_fs_request_sync(
              {"action": "eda_versions"}, timeout=10.0
          )
          self._reply_json(200 if res.get("success") else 500, res)
        else:
          self._reply_json(404, {"error": "Not Found"})

      def do_POST(self):
        path = urlsplit(self.path).path
        if not self._authorize_api("POST", path):
          return
        content_len = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_len).decode("utf-8", errors="ignore")
        self._set_payload_audit(raw_body)

        if self.path == "/api/v1/runtime/negotiate":
          result = worker_ref.begin_session_negotiation()
          self._reply_json(202 if result.get("success") else 503, result)

        elif self.path in ["/api/v1/eda/execute", "/api/eda/exec"]:
          try:
            task = (
                json.loads(raw_body)
                if raw_body.strip().startswith("{")
                else {"script": raw_body, "hint": "agent_auto.tcl"}
            )
          except Exception:
            task = {"script": raw_body, "hint": "agent_auto.tcl"}

          if not isinstance(task, dict):
            self._reply_json(400, {"state": "not_submitted", "error": "request body must be a JSON object"})
            return

          if not worker_ref.is_serial_connected():
            self._reply_json(503, {"state": "not_submitted", "error": "serial link is disconnected"})
            return

          peer_status = worker_ref.get_session_peer_status()
          if (
              peer_status.get("state") != "negotiated"
              or "request.correlation" not in peer_status.get("features", [])
              or "request.deduplication" not in peer_status.get("features", [])
          ):
            self._reply_json(
                503,
                {
                    "state": "not_submitted",
                    "error": "peer has not confirmed request.correlation and request.deduplication capabilities",
                    "peer_state": peer_status.get("state"),
                    "peer_features": peer_status.get("features", []),
                },
            )
            return

          script = task.get("script") or task.get("script_content", "")
          hint = task.get("hint") or task.get("script_name", "agent_auto.tcl")
          version = task.get("version", "auto")
          target_file = task.get("target_file", "")
          try:
            timeout = int(task.get("timeout", 60))
          except (TypeError, ValueError):
            self._reply_json(400, {"state": "not_submitted", "error": "timeout must be an integer"})
            return
          if timeout < 0:
            self._reply_json(400, {"state": "not_submitted", "error": "timeout must be >= 0"})
            return
          keep_open = bool(task.get("keep_open", False))
          try:
            request_id = worker_ref.request_tracker.validate_request_id(
                task.get("request_id") or str(uuid.uuid4())
            )
          except RequestTrackerError as exc:
            self._reply_json(400, {"state": "not_submitted", "error": str(exc)})
            return
          self._set_request_audit(request_id)
          existing = worker_ref.request_tracker.get(request_id)
          if existing.get("state") != "not_found":
            if not security_ref.can_read_request(request_id, self._principal):
              self._reply_json(404, {
                  "request_id": request_id,
                  "state": "not_found",
                  "error": "request_id is unknown or expired",
              })
              return
            self._reply_json(
                409,
                {
                    "request_id": request_id,
                    "state": "already_registered",
                    "existing_state": existing.get("state"),
                    "hint": "query GET /api/v1/requests/{request_id} instead of resubmitting",
                },
            )
            return
          if not security_ref.begin_execution(
              self._principal.agent_id,
              request_id,
              max_inflight=max_pending_eda_per_agent,
          ):
            self._audit_reason = "AGENT_EXECUTION_IN_FLIGHT_LIMIT"
            self._reply_json(429, {
                "success": False,
                "state": "rate_limited",
                "error": {
                    "code": "AGENT_EXECUTION_IN_FLIGHT_LIMIT",
                    "max_pending": max_pending_eda_per_agent,
                    "message": (
                        "An earlier EDA request is still pending or indeterminate; "
                        "query its request_id before submitting another request."
                    ),
                },
            })
            return

          try:
            security_ref.bind_request_owner(request_id, self._principal.agent_id)
          except AgentSecurityError:
            security_ref.end_execution(self._principal.agent_id, request_id)
            self._reply_json(409, {
                "request_id": request_id,
                "state": "already_registered",
                "error": "request_id already has an owner",
            })
            return

          submit_payload = {
              "request_id": request_id,
              "script": script,
              "hint": hint,
              "version": version,
              "target_file": target_file,
              "timeout": timeout,
              "keep_open": keep_open,
          }
          try:
            worker_ref.request_tracker.register(request_id, "eda.execute")
          except Exception as exc:
            security_ref.release_request_owner(request_id, self._principal.agent_id)
            security_ref.end_execution(self._principal.agent_id, request_id)
            self._reply_json(503, {
                "request_id": request_id,
                "state": "not_submitted",
                "error": str(exc),
            })
            return

          try:
            worker_ref.send_frame(
                FrameType.EDA_TASK_SUBMIT,
                json.dumps(submit_payload, ensure_ascii=False).encode("utf-8"),
            )
          except Exception as exc:
            # A write failure can occur after some bytes reached the serial
            # transport. Do not claim "not submitted", release the quota, or
            # encourage a retry with a new request_id.
            worker_ref.request_tracker.fail(request_id, str(exc), state="unknown")
            self._audit_reason = "EDA_FRAME_SUBMISSION_UNCONFIRMED"
            self._reply_json(503, {
                "request_id": request_id,
                "state": "unknown",
                "error": {
                    "code": "EDA_FRAME_SUBMISSION_UNCONFIRMED",
                    "message": str(exc),
                    "next_step": "Query this request_id; do not submit a new request.",
                },
            })
            return

          try:
            wait_ms = int(task.get("wait_timeout_ms", min(max(timeout + 10, 5) * 1000, 300000)))
          except (TypeError, ValueError):
            wait_ms = min(max(timeout + 10, 5) * 1000, 300000)
          wait_ms = max(0, min(wait_ms, 300000))
          result = worker_ref.request_tracker.wait(request_id, wait_ms / 1000.0)
          if result.get("state") in ("completed", "failed"):
            security_ref.end_execution(self._principal.agent_id, request_id)
          else:
            owner_agent_id = self._principal.agent_id

            def release_when_request_finishes():
              try:
                final_state = worker_ref.request_tracker.wait(request_id, None).get("state")
                if final_state in ("completed", "failed", "unknown"):
                  final_http_status = (
                      200 if final_state == "completed"
                      else 502 if final_state == "failed"
                      else 503
                  )
                  try:
                    security_ref.audit_request(
                        agent_id=owner_agent_id,
                        token_id=self._principal.token_id,
                        method="ASYNC",
                        path="/api/v1/eda/execute",
                        decision=final_state,
                        http_status=final_http_status,
                        reason_code="ASYNC_EXECUTION_FINALIZED",
                        request_id=request_id,
                    )
                  except Exception:
                    # Admission was durably audited before dispatch; the
                    # final outcome event may be absent if storage is failing.
                    if final_state in ("unknown",):
                      return
                  if final_state in ("completed", "failed"):
                    security_ref.end_execution(owner_agent_id, request_id)
                # UNKNOWN / not_found means the outcome cannot be confirmed.
                # Keep the persistent reservation until its conservative TTL.
              except Exception:
                # Retain the reservation rather than freeing quota on uncertainty.
                return

            # Pending, accepted, and unknown requests keep the per-Agent
            # reservation. The waiter releases only on a known terminal result.
            if result.get("state") in ("pending", "accepted"):
              threading.Thread(
                  target=release_when_request_finishes,
                  name=f"AgentExecutionRelease-{request_id[:8]}",
                  daemon=True,
              ).start()
          status_code = (
              200 if result.get("state") == "completed"
              else 503 if result.get("state") == "unknown"
              else 502 if result.get("state") == "failed"
              else 202
          )
          self._reply_json(status_code, result)

        elif self.path == "/api/v1/eda/versions":
          res = worker_ref.send_fs_request_sync(
              {"action": "eda_versions"}, timeout=10.0
          )
          self._reply_json(200 if res.get("success") else 500, res)

        elif self.path == "/api/v1/message":
          worker_ref.send_frame(FrameType.TEXT_MSG, raw_body.encode("utf-8"))
          self._reply_json(200, {"status": "sent"})


        # ---------- Remote Shell API ----------
        elif self.path in ("/api/v1/shell/exec", "/api/shell/exec"):
          try:
            task = json.loads(raw_body) if raw_body.strip() else {}
          except Exception:
            task = {"command": raw_body}

          if not isinstance(task, dict):
            self._reply_json(400, {"state": "not_submitted", "error": "request body must be a JSON object"})
            return

          if not worker_ref.is_serial_connected():
            self._reply_json(503, {"state": "not_submitted", "error": "serial link is disconnected"})
            return

          peer_status = worker_ref.get_session_peer_status()
          if (
              peer_status.get("state") != "negotiated"
              or "request.correlation" not in peer_status.get("features", [])
              or "request.deduplication" not in peer_status.get("features", [])
          ):
            self._reply_json(
                503,
                {
                    "state": "not_submitted",
                    "error": "peer has not confirmed request.correlation and request.deduplication capabilities",
                    "peer_state": peer_status.get("state"),
                    "peer_features": peer_status.get("features", []),
                },
            )
            return

          try:
            remote_timeout = int(task.get("timeout", 60) or 60)
          except (TypeError, ValueError):
            self._reply_json(400, {"state": "not_submitted", "error": "timeout must be an integer"})
            return
          if remote_timeout < 0:
            self._reply_json(400, {"state": "not_submitted", "error": "timeout must be >= 0"})
            return
          task["timeout"] = remote_timeout
          is_async = bool(task.get("async", False))
          try:
            request_id = worker_ref.request_tracker.validate_request_id(
                task.get("request_id") or str(uuid.uuid4())
            )
          except RequestTrackerError as exc:
            self._reply_json(400, {"state": "not_submitted", "error": str(exc)})
            return
          self._set_request_audit(request_id)
          existing = worker_ref.request_tracker.get(request_id)
          if existing.get("state") != "not_found":
            if not security_ref.can_read_request(request_id, self._principal):
              self._reply_json(404, {
                  "request_id": request_id,
                  "state": "not_found",
                  "error": "request_id is unknown or expired",
              })
              return
            self._reply_json(
                409,
                {
                    "request_id": request_id,
                    "state": "already_registered",
                    "existing_state": existing.get("state"),
                    "hint": "query GET /api/v1/requests/{request_id} instead of resubmitting",
                },
            )
            return
          try:
            security_ref.bind_request_owner(request_id, self._principal.agent_id)
          except AgentSecurityError:
            self._reply_json(409, {
                "request_id": request_id,
                "state": "already_registered",
                "error": "request_id already has an owner",
            })
            return
          task["request_id"] = request_id
          remote_task = {
              key: value for key, value in task.items() if key != "wait_timeout_ms"
          }
          try:
            worker_ref.request_tracker.register(request_id, "shell.exec")
          except Exception as exc:
            security_ref.release_request_owner(request_id, self._principal.agent_id)
            self._reply_json(503, {
                "request_id": request_id,
                "state": "not_submitted",
                "error": str(exc),
            })
            return

          try:
            worker_ref.send_frame(
                FrameType.SHELL_EXEC,
                json.dumps(remote_task, ensure_ascii=False).encode("utf-8"),
            )
          except Exception as exc:
            worker_ref.request_tracker.fail(request_id, str(exc), state="unknown")
            self._audit_reason = "SHELL_FRAME_SUBMISSION_UNCONFIRMED"
            self._reply_json(503, {
                "request_id": request_id,
                "state": "unknown",
                "error": {
                    "code": "SHELL_FRAME_SUBMISSION_UNCONFIRMED",
                    "message": str(exc),
                    "next_step": "Query this request_id; do not submit a new request.",
                },
            })
            return

          default_wait = 10000 if is_async else min(
              max(remote_timeout + 10, 5) * 1000, 300000
          )
          try:
            wait_ms = int(task.get("wait_timeout_ms", default_wait))
          except (TypeError, ValueError):
            wait_ms = default_wait
          wait_ms = max(0, min(wait_ms, 300000))
          result = worker_ref.request_tracker.wait(request_id, wait_ms / 1000.0)
          status_code = (
              202 if result.get("state") in ("pending", "accepted")
              else 503 if result.get("state") == "unknown"
              else 502 if result.get("state") == "failed"
              else 200
          )
          self._reply_json(status_code, result)

        elif self.path in ("/api/v1/shell/kill", "/api/shell/kill"):
          try:
            req = json.loads(raw_body) if raw_body.strip() else {}
          except Exception:
            req = {}
          worker_ref.send_frame(
              FrameType.SHELL_KILL,
              json.dumps(req, ensure_ascii=False).encode("utf-8"),
          )
          self._reply_json(200, {"status": "kill_submitted", "req": req})

        elif self.path in ("/api/v1/shell/tasks", "/api/v1/shell/list", "/api/shell/tasks"):
          worker_ref.send_frame(FrameType.SHELL_LIST, b"{}")
          self._reply_json(200, {
              "status": "list_submitted",
              "hint": "任务列表将通过 SHELL_LIST_RESP 回传并打印到控制台",
          })

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

    class _TrackedThreadingHTTPServer(ThreadingHTTPServer):
      # Handler threads are daemonized so server_close() never performs an
      # unbounded implicit join. The owner performs an explicit bounded drain.
      daemon_threads = True
      block_on_close = False

      def process_request(self, request, client_address):
        with server_owner._handler_condition:
          if server_owner._shutting_down:
            self.shutdown_request(request)
            return
          server_owner._active_handlers += 1
        try:
          super().process_request(request, client_address)
        except Exception:
          server_owner._handler_finished()
          raise

      def process_request_thread(self, request, client_address):
        try:
          super().process_request_thread(request, client_address)
        finally:
          server_owner._handler_finished()

    def serve():
      try:
        server = _TrackedThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self._server = server
        self.port = server.server_address[1]
        self._server_ready.set()
        server.serve_forever()
      except Exception as exc:
        self._server_error = exc
        self._server_ready.set()

    self._server_thread = threading.Thread(
        target=serve, name="AirBridgeHTTPServer", daemon=True
    )
    self._server_thread.start()
    if not self._server_ready.wait(timeout=5.0):
      raise RuntimeError("AirBridge HTTP server did not finish starting")
    if self._server_error is not None:
      raise RuntimeError(f"AirBridge HTTP server failed to start: {self._server_error}")

  def stop(self):
    with self._stop_lock:
      deadline = time.monotonic() + max(0.0, self._shutdown_timeout_seconds)
      with self._handler_condition:
        self._shutting_down = True
        while self._active_handlers:
          remaining = deadline - time.monotonic()
          if remaining <= 0:
            active = self._active_handlers
            # The close was not completed. Resume normal service so the GUI
            # can remain usable while the caller retries a later close.
            self._shutting_down = False
            self._handler_condition.notify_all()
            raise RuntimeError(
                f"AirBridge shutdown timed out with {active} "
                "in-flight HTTP handler(s); the window and API service remain open"
            )
          self._handler_condition.wait(timeout=remaining)

      # The drain completed while new accepted connections were being rejected.
      # It is now safe to stop the serve loop and close the listening socket.
      server = self._server
      if server is not None:
        server.shutdown()
        server.server_close()
        self._server = None

      thread = self._server_thread
      if thread is not None and thread is not threading.current_thread():
        # The drain deadline may already be consumed; allow a small, separate
        # bound for the serve_forever thread to return after shutdown().
        thread.join(timeout=max(1.0, deadline - time.monotonic()))
        if thread.is_alive():
          raise RuntimeError("AirBridge HTTP server thread did not stop")
        self._server_thread = None

      if self._owns_security_store and self.security_store is not None:
        self.security_store.close()
        self.security_store = None


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

    # Load the M2.5 support only when the GUI is instantiated. Some existing
    # protocol tests import this module without installing the optional MCP
    # package; the unified EXE bundles it, while source runs can use mcp_gateway/.
    try:
      from airbridge_mcp_gateway.gateway_policy import GatewayPolicyStore
      from airbridge_mcp_gateway.credential_store import default_credential_store
      from airbridge_mcp_gateway.client_adapters import AntigravityAdapter, OpenCodeAdapter
      from tools import airbridge_agent_admin
    except ModuleNotFoundError:
      gateway_package_dir = os.path.join(os.path.dirname(__file__), "mcp_gateway")
      if gateway_package_dir not in sys.path:
        sys.path.insert(0, gateway_package_dir)
      from airbridge_mcp_gateway.gateway_policy import GatewayPolicyStore
      from airbridge_mcp_gateway.credential_store import default_credential_store
      from airbridge_mcp_gateway.client_adapters import AntigravityAdapter, OpenCodeAdapter
      from tools import airbridge_agent_admin

    self.gateway_agent_admin = airbridge_agent_admin
    self.gateway_policy_store = GatewayPolicyStore.for_current_user()
    self._gateway_policy_agents: Dict[str, bool] = {}
    self._gateway_policy_invalid = False

    # Adapter edits are enabled only in the installed/frozen AirBridge.exe.
    # Source-run development sessions must never write "python.exe mcp stdio".
    self.gateway_adapter_executable = None
    if getattr(sys, "frozen", False):
      candidate = Path(sys.executable).resolve()
      if candidate.is_file() and candidate.name.lower() == "airbridge.exe":
        self.gateway_adapter_executable = candidate
    adapter_executable = self.gateway_adapter_executable or Path(sys.executable)
    self.gateway_antigravity_adapter = AntigravityAdapter(adapter_executable)
    self.gateway_opencode_adapter = OpenCodeAdapter(adapter_executable)
    self.gateway_antigravity_config_path = AntigravityAdapter.global_config()
    self.gateway_opencode_config_path = OpenCodeAdapter.global_config()
    self.gateway_adapter_actions_enabled = self.gateway_adapter_executable is not None
    try:
      self.gateway_credential_store = default_credential_store()
      self._gateway_credential_store_error = ""
    except Exception as exc:
      self.gateway_credential_store = None
      self._gateway_credential_store_error = str(exc)

    self.signals = SerialWorkerSignals()
    self.worker = SerialWorker(self.signals)
    self.worker.start()

    self.ipc = AntigravityIPCServer(self.worker, port=8888)
    self.ipc.start()
    # The existing M2.4 registry remains the authority for server-side Agent
    # credentials; the GUI reuses it rather than creating another SQLite DB.
    self.gateway_security_store = getattr(self.ipc, "security_store", None)

    self.init_ui()
    self.bind_events()
    self.scan_ports()
    # Auto-connect to host B: prefer the USB/FTDI gateway and verify the link with a probe request
    QTimer.singleShot(100, self.auto_connect_serial)

  def closeEvent(self, event):
    try:
      self.ipc.stop()
    except Exception as exc:
      QMessageBox.critical(
          self,
          "AirBridge shutdown failed",
          f"The local API server could not shut down safely. The window will remain open.\n{exc}",
      )
      event.ignore()
      return
    event.accept()

  def auto_connect_serial(self):
    candidates = []
    for p in serial.tools.list_ports.comports():
      candidates.append((p.device, "VID:PID" in (p.hwid or "")))
    candidates.sort(key=lambda x: not x[1])
    baud = int(self.combo_baud.currentText())
    for dev, _is_usb in candidates:
      self.worker.connect_serial(dev, baud)
      if not self.worker.ser:
        continue
      res = self.worker.send_fs_request_sync({"action": "eda_versions"}, timeout=8.0)
      if res.get("success"):
        self.statusBar().showMessage(f"链路已自动连接: {dev}")
        return
      self.worker.disconnect_serial()
    if candidates:
      self.worker.connect_serial(candidates[0][0], baud)

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

    file_layout.addWidget(QLabel("双端分片确认传输进度 (4KB 流水线弹性分片):"))
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

    self.btn_test_orcad = QPushButton("📐 打开原理图")
    self.btn_test_orcad.setStyleSheet("background-color: #2E7D32;")

    self.btn_test_allegro = QPushButton("⚡ 打开PCB（Allegro）")
    self.btn_test_allegro.setStyleSheet("background-color: #6A1B9A;")

    self.btn_close_orcad = QPushButton("关闭原理图Capture")
    self.btn_close_orcad.setStyleSheet("background-color: #8B3A3A;")
    self.btn_close_allegro = QPushButton("关闭PCB（Allegro）")
    self.btn_close_allegro.setStyleSheet("background-color: #8B3A3A;")

    self.btn_load_eda = QPushButton("📂 加载本地脚本...")
    self.btn_exec_eda = QPushButton("▶ 立即执行 (F5)")
    self.btn_exec_eda.setStyleSheet("background-color: #0E639C;")
    self.btn_abort_eda = QPushButton("⏹ 强杀任务")
    self.btn_abort_eda.setStyleSheet("background-color: #C62828;")

    eda_actions = QHBoxLayout()
    eda_actions.addWidget(self.btn_test_orcad)
    eda_actions.addWidget(self.btn_test_allegro)
    eda_actions.addWidget(self.btn_close_orcad)
    eda_actions.addWidget(self.btn_close_allegro)
    eda_actions.addWidget(self.btn_load_eda)
    eda_actions.addWidget(self.btn_exec_eda)
    eda_actions.addWidget(self.btn_abort_eda)
    eda_actions.addStretch()

    self.txt_eda_editor = QTextEdit()
    self.txt_eda_editor.setPlaceholderText(
        "OrCAD Tcl / Allegro SKILL 代码编辑区..."
    )

    eda_layout.addLayout(eda_ctrl)
    eda_layout.addLayout(eda_actions)
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

    # Tab 5: 本机 MCP STDIO Gateway settings
    gateway_tab = QWidget()
    gateway_layout = QVBoxLayout(gateway_tab)
    gateway_intro = QLabel(
        "本设置只控制当前 Windows 用户的本机 MCP STDIO 接入。"
        "Antigravity / OpenCode 负责启动和关闭 AirBridge.exe 子进程；"
        "这里不创建网络监听器，也不影响 A/B 串口通信。"
    )
    gateway_intro.setWordWrap(True)
    gateway_layout.addWidget(gateway_intro)

    self.chk_gateway_enabled = QCheckBox("启用本机 Gateway（新安装默认关闭）")
    self.chk_gateway_enabled.setStyleSheet("font-weight: bold;")
    gateway_layout.addWidget(self.chk_gateway_enabled)

    gateway_agents = QGroupBox("首批 MCP Client / Agent")
    gateway_agents_layout = QVBoxLayout(gateway_agents)
    self.chk_gateway_antigravity = QCheckBox("Antigravity")
    self.chk_gateway_opencode = QCheckBox("OpenCode")
    gateway_agents_layout.addWidget(self.chk_gateway_antigravity)
    gateway_agents_layout.addWidget(self.chk_gateway_opencode)

    self.lbl_gateway_antigravity_credential = QLabel("Antigravity 凭据：检查中")
    self.btn_gateway_antigravity_provision = QPushButton("Provision（只读）")
    self.btn_gateway_antigravity_revoke = QPushButton("Revoke 凭据")
    antigravity_credential_row = QHBoxLayout()
    antigravity_credential_row.addWidget(self.lbl_gateway_antigravity_credential, stretch=1)
    antigravity_credential_row.addWidget(self.btn_gateway_antigravity_provision)
    antigravity_credential_row.addWidget(self.btn_gateway_antigravity_revoke)
    gateway_agents_layout.addLayout(antigravity_credential_row)

    self.lbl_gateway_opencode_credential = QLabel("OpenCode 凭据：检查中")
    self.btn_gateway_opencode_provision = QPushButton("Provision（只读）")
    self.btn_gateway_opencode_revoke = QPushButton("Revoke 凭据")
    opencode_credential_row = QHBoxLayout()
    opencode_credential_row.addWidget(self.lbl_gateway_opencode_credential, stretch=1)
    opencode_credential_row.addWidget(self.btn_gateway_opencode_provision)
    opencode_credential_row.addWidget(self.btn_gateway_opencode_revoke)
    gateway_agents_layout.addLayout(opencode_credential_row)
    gateway_credential_note = QLabel(
        "Provision 使用只读 Scope（runtime.read、eda.versions.read、request.read），默认 90 天；"
        "Token 只写入当前用户 Credential Manager，不显示、不进入 MCP 配置。Revoke 会撤销该 Agent 的全部有效凭据。"
    )
    gateway_credential_note.setWordWrap(True)
    gateway_agents_layout.addWidget(gateway_credential_note)
    gateway_layout.addWidget(gateway_agents)

    antigravity_adapter_group = QGroupBox("Antigravity App / IDE")
    antigravity_adapter_layout = QVBoxLayout(antigravity_adapter_group)
    self.lbl_gateway_antigravity_adapter_path = QLabel("")
    self.lbl_gateway_antigravity_adapter_path.setWordWrap(True)
    self.lbl_gateway_antigravity_adapter_status = QLabel("配置状态：检查中")
    self.btn_gateway_antigravity_install = QPushButton("安装 / 更新本机 STDIO Adapter")
    self.btn_gateway_antigravity_remove = QPushButton("移除 AirBridge Adapter")
    antigravity_adapter_buttons = QHBoxLayout()
    antigravity_adapter_buttons.addWidget(self.btn_gateway_antigravity_install)
    antigravity_adapter_buttons.addWidget(self.btn_gateway_antigravity_remove)
    antigravity_adapter_layout.addWidget(self.lbl_gateway_antigravity_adapter_path)
    antigravity_adapter_layout.addWidget(self.lbl_gateway_antigravity_adapter_status)
    antigravity_adapter_layout.addLayout(antigravity_adapter_buttons)
    gateway_layout.addWidget(antigravity_adapter_group)

    opencode_adapter_group = QGroupBox("OpenCode Desktop / CLI")
    opencode_adapter_layout = QVBoxLayout(opencode_adapter_group)
    self.lbl_gateway_opencode_adapter_path = QLabel("")
    self.lbl_gateway_opencode_adapter_path.setWordWrap(True)
    self.lbl_gateway_opencode_adapter_status = QLabel("配置状态：检查中")
    self.btn_gateway_opencode_install = QPushButton("安装 / 更新本机 STDIO Adapter")
    self.btn_gateway_opencode_remove = QPushButton("移除 AirBridge Adapter")
    opencode_adapter_buttons = QHBoxLayout()
    opencode_adapter_buttons.addWidget(self.btn_gateway_opencode_install)
    opencode_adapter_buttons.addWidget(self.btn_gateway_opencode_remove)
    opencode_adapter_layout.addWidget(self.lbl_gateway_opencode_adapter_path)
    opencode_adapter_layout.addWidget(self.lbl_gateway_opencode_adapter_status)
    opencode_adapter_layout.addLayout(opencode_adapter_buttons)
    gateway_layout.addWidget(opencode_adapter_group)

    if not self.gateway_adapter_actions_enabled:
      adapter_note = QLabel(
          "配置写入仅在正式安装的 AirBridge.exe 中启用；源码运行模式只显示状态，不修改 MCP 客户端配置。"
      )
      adapter_note.setWordWrap(True)
      gateway_layout.addWidget(adapter_note)

    gateway_actions = QHBoxLayout()
    self.btn_gateway_refresh = QPushButton("刷新状态")
    self.btn_gateway_save = QPushButton("保存 Gateway 设置")
    self.btn_gateway_save.setStyleSheet("background-color: #0E639C; font-weight: bold;")
    gateway_actions.addWidget(self.btn_gateway_refresh)
    gateway_actions.addWidget(self.btn_gateway_save)
    gateway_actions.addStretch()
    gateway_layout.addLayout(gateway_actions)

    self.lbl_gateway_policy_status = QLabel("")
    self.lbl_gateway_policy_status.setWordWrap(True)
    gateway_layout.addWidget(self.lbl_gateway_policy_status)
    gateway_layout.addStretch()
    self.tabs.addTab(gateway_tab, "MCP Gateway")

    self.refresh_gateway_settings()

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
    self.btn_gateway_refresh.clicked.connect(self.refresh_gateway_settings)
    self.btn_gateway_save.clicked.connect(self.save_gateway_settings)
    self.btn_gateway_antigravity_provision.clicked.connect(
        lambda: self.provision_gateway_agent("antigravity")
    )
    self.btn_gateway_antigravity_revoke.clicked.connect(
        lambda: self.revoke_gateway_agent("antigravity")
    )
    self.btn_gateway_opencode_provision.clicked.connect(
        lambda: self.provision_gateway_agent("opencode")
    )
    self.btn_gateway_opencode_revoke.clicked.connect(
        lambda: self.revoke_gateway_agent("opencode")
    )
    self.btn_gateway_antigravity_install.clicked.connect(
        lambda: self.install_gateway_adapter("antigravity")
    )
    self.btn_gateway_antigravity_remove.clicked.connect(
        lambda: self.remove_gateway_adapter("antigravity")
    )
    self.btn_gateway_opencode_install.clicked.connect(
        lambda: self.install_gateway_adapter("opencode")
    )
    self.btn_gateway_opencode_remove.clicked.connect(
        lambda: self.remove_gateway_adapter("opencode")
    )

    self.btn_detect_eda_vers.clicked.connect(self.detect_remote_eda_versions)
    self.btn_test_orcad.clicked.connect(self.trigger_builtin_orcad_test)
    self.btn_test_allegro.clicked.connect(self.trigger_builtin_allegro_test)
    self.btn_close_orcad.clicked.connect(self.trigger_close_orcad)
    self.btn_close_allegro.clicked.connect(self.trigger_close_allegro)
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

  def _gateway_agent_credential_present(self, agent_id: str) -> bool:
    if self.gateway_credential_store is None:
      return False
    try:
      # CredReadW presence check frees the native structure without decoding
      # the credential blob into a Python string in the GUI process.
      return bool(self.gateway_credential_store.exists(agent_id))
    except Exception:
      # Missing credentials and Credential Manager lookup errors both mean
      # that this Agent cannot be enabled from the GUI yet.
      return False

  def refresh_gateway_settings(self):
    """Load the non-secret per-user Gateway policy and credential presence."""
    try:
      enabled, agents = self.gateway_policy_store.read_snapshot()
      self._gateway_policy_agents = dict(agents)
      self._gateway_policy_invalid = False
      self.chk_gateway_enabled.setChecked(enabled)
      self.chk_gateway_antigravity.setChecked(bool(agents.get("antigravity", False)))
      self.chk_gateway_opencode.setChecked(bool(agents.get("opencode", False)))
      if self.gateway_policy_store.path is None:
        self.lbl_gateway_policy_status.setText(
            "策略状态不可用：当前用户 LOCALAPPDATA 未设置。Gateway 保持 fail-closed。"
        )
      elif not enabled:
        self.lbl_gateway_policy_status.setText(
            "Gateway 当前为 OFF。首次安装或策略文件缺失时默认 OFF；"
            "已有 MCP 子进程的后续 Tool 调用也会被拒绝。"
        )
      else:
        self.lbl_gateway_policy_status.setText(
            "Gateway 策略已加载。保存开关后，现存 STDIO 子进程会在下一次 Tool 调用时读取新状态。"
        )
    except (OSError, ValueError) as exc:
      self._gateway_policy_agents = {}
      self._gateway_policy_invalid = True
      self.chk_gateway_enabled.setChecked(False)
      self.chk_gateway_antigravity.setChecked(False)
      self.chk_gateway_opencode.setChecked(False)
      self.lbl_gateway_policy_status.setText(
          f"Gateway 策略文件不可用或已损坏：{exc}。为安全起见当前视为 OFF；"
          "保存新设置前会要求确认修复。"
      )

    self._refresh_gateway_credential_status("antigravity")
    self._refresh_gateway_credential_status("opencode")
    if self.gateway_credential_store is None:
      self.lbl_gateway_policy_status.setText(
          self.lbl_gateway_policy_status.text()
          + " Windows Credential Manager 不可用，不能启用 Agent。"
      )
    if self.gateway_security_store is None:
      self.lbl_gateway_policy_status.setText(
          self.lbl_gateway_policy_status.text()
          + " Agent Security Registry 不可用；Provision / Revoke 已禁用。"
      )
    self.refresh_gateway_adapter_statuses()

  def _refresh_gateway_credential_status(self, agent_id: str):
    label = (
        self.lbl_gateway_antigravity_credential
        if agent_id == "antigravity"
        else self.lbl_gateway_opencode_credential
    )
    provision_button = (
        self.btn_gateway_antigravity_provision
        if agent_id == "antigravity"
        else self.btn_gateway_opencode_provision
    )
    revoke_button = (
        self.btn_gateway_antigravity_revoke
        if agent_id == "antigravity"
        else self.btn_gateway_opencode_revoke
    )
    local_present = self._gateway_agent_credential_present(agent_id)
    active_tokens = []
    registry_available = self.gateway_security_store is not None
    if registry_available:
      try:
        active_tokens = [
            item for item in self.gateway_security_store.list_tokens(agent_id)
            if item.get("enabled")
        ]
      except Exception:
        registry_available = False

    if registry_available:
      server_text = f"服务端有效凭据：{len(active_tokens)}"
    else:
      server_text = "服务端凭据状态：不可读取"

    local_text = "本机 Credential Manager：已存在" if local_present else "本机 Credential Manager：未配置"
    label.setText(f"{agent_id} — {local_text}；{server_text}")

    can_manage = (
        os.name == "nt"
        and registry_available
        and self.gateway_credential_store is not None
        and self.gateway_agent_admin is not None
    )
    provision_button.setEnabled(can_manage and not active_tokens)
    revoke_button.setEnabled(can_manage and (bool(active_tokens) or local_present))

  def provision_gateway_agent(self, agent_id: str):
    """Reuse the tested M2.4 zero-token provisioning transaction."""
    if self.gateway_security_store is None or not self.gateway_agent_admin:
      QMessageBox.warning(self, "Agent Provision 不可用", "M2.4 Agent Security Registry 不可用。")
      return
    display = "Antigravity" if agent_id == "antigravity" else "OpenCode"
    answer = QMessageBox.question(
        self,
        f"Provision {display}",
        "将创建该 Agent 的 M2.4 Service Credential，并原子写入当前 Windows 用户 Credential Manager。"
        "默认只读 Scope：runtime.read、eda.versions.read、request.read；有效期 90 天。"
        "Token 不会显示，也不会写入 MCP 配置。是否继续？",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    if answer != QMessageBox.StandardButton.Yes:
      return
    try:
      from types import SimpleNamespace

      args = SimpleNamespace(
          agent_id=agent_id,
          scope=None,
          label=f"{display} local MCP",
          expires_days=90,
      )
      result = self.gateway_agent_admin._provision_agent(self.gateway_security_store, args)
    except Exception as exc:
      QMessageBox.warning(self, f"{display} Provision 失败", str(exc))
      self.refresh_gateway_settings()
      return

    scopes = ", ".join(result.get("scopes", []))
    expires = result.get("expires_at") or "Never"
    QMessageBox.information(
        self,
        f"{display} Provision 成功",
        f"Agent：{result.get('agent_id')}\n"
        f"Token ID：{result.get('token_id')}\n"
        f"Scopes：{scopes}\n"
        f"到期时间：{expires}\n\n"
        "Token 已写入 Windows Credential Manager；明文未显示。",
    )
    self.refresh_gateway_settings()

  def revoke_gateway_agent(self, agent_id: str):
    """Switch the Agent OFF before revoking all server and local credentials."""
    if self.gateway_security_store is None or not self.gateway_agent_admin:
      QMessageBox.warning(self, "Agent Revoke 不可用", "M2.4 Agent Security Registry 不可用。")
      return
    display = "Antigravity" if agent_id == "antigravity" else "OpenCode"
    answer = QMessageBox.question(
        self,
        f"Revoke {display}",
        "将先把该 Agent 的本机策略设为 OFF，阻止存量 STDIO 子进程继续请求；"
        "然后撤销服务端该 Agent 的全部有效 M2.4 凭据，并删除当前用户 Credential Manager 中对应条目。"
        "此操作不能撤销。是否继续？",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    if answer != QMessageBox.StandardButton.Yes:
      return

    # Disable this Agent first; if persistence fails, do not revoke or clean up.
    try:
      try:
        gateway_enabled, agents = self.gateway_policy_store.read_snapshot()
      except (OSError, ValueError):
        gateway_enabled, agents = False, {}
      agents[agent_id] = False
      self.gateway_policy_store.write(enabled=gateway_enabled, agents=agents)
    except (OSError, ValueError) as exc:
      QMessageBox.critical(
          self,
          "无法安全撤销 Agent",
          f"Agent OFF 策略写入失败，未执行服务端撤销或 Credential 删除。\n{exc}",
      )
      return

    self._gateway_policy_agents = dict(agents)
    self._gateway_policy_invalid = False
    if agent_id == "antigravity":
      self.chk_gateway_antigravity.setChecked(False)
    else:
      self.chk_gateway_opencode.setChecked(False)
    adapter_outcomes = self._sync_installed_gateway_adapters(gateway_enabled, agents)

    try:
      result, exit_code = self.gateway_agent_admin._revoke_agent(
          self.gateway_security_store, agent_id
      )
    except Exception as exc:
      QMessageBox.critical(
          self,
          f"{display} Revoke 失败",
          f"Agent 已被本机策略设为 OFF，存量 MCP Tool 调用应被阻止；"
          f"但服务端撤销未确认：{exc}。请检查 Agent Security 状态。",
      )
      self.refresh_gateway_settings()
      return

    cleanup = result.get("credential_cleanup", "unknown")
    revoked = result.get("revoked", False)
    detail = (
        f"服务端凭据撤销：{'已完成' if revoked else '没有活动凭据'}\n"
        f"本机 Credential Manager 清理：{cleanup}"
    )
    if adapter_outcomes:
      detail += "\nAdapter 状态：" + "; ".join(adapter_outcomes)
    if exit_code != 0 or cleanup == "failed":
      QMessageBox.warning(
          self,
          f"{display} Revoke 部分完成",
          detail + "\nAgent 保持 OFF；请检查本机 Credential Manager 并重试清理。",
      )
    else:
      QMessageBox.information(self, f"{display} Revoke 完成", detail)
    self.refresh_gateway_settings()

  def _gateway_adapter_details(self, agent_id: str):
    if agent_id == "antigravity":
      return (
          self.gateway_antigravity_adapter,
          self.gateway_antigravity_config_path,
          self.lbl_gateway_antigravity_adapter_path,
          self.lbl_gateway_antigravity_adapter_status,
          self.btn_gateway_antigravity_install,
          self.btn_gateway_antigravity_remove,
      )
    if agent_id == "opencode":
      return (
          self.gateway_opencode_adapter,
          self.gateway_opencode_config_path,
          self.lbl_gateway_opencode_adapter_path,
          self.lbl_gateway_opencode_adapter_status,
          self.btn_gateway_opencode_install,
          self.btn_gateway_opencode_remove,
      )
    raise ValueError(f"Unsupported M2.5 Agent ID: {agent_id}")

  def refresh_gateway_adapter_statuses(self):
    """Observe only the two supported global config files; never scan projects."""
    for agent_id in ("antigravity", "opencode"):
      adapter, path, path_label, status_label, install_button, remove_button = (
          self._gateway_adapter_details(agent_id)
      )
      path_label.setText(f"当前用户全局配置：{path}")
      try:
        status = adapter.inspect(path, agent_id)
        migration_path = None
        if status.present and not status.owned:
          migration_path = adapter.inspect_migration_candidate(path, agent_id)
        if not status.present:
          text = "未安装"
        elif migration_path:
          text = (
              "检测到旧版 AirBridge STDIO Adapter；需确认迁移。"
              f"旧程序路径：{migration_path}"
          )
        elif not status.owned:
          text = "名称冲突：该条目不是可识别的 AirBridge STDIO 配置，未触碰"
        elif status.enabled is None:
          text = "AirBridge 条目存在，但 disabled 字段类型无效"
        elif status.enabled:
          text = "AirBridge Adapter 已安装且已启用"
        else:
          text = "AirBridge Adapter 已安装但已禁用"
        status_label.setText(f"配置状态：{text}")
        remove_button.setEnabled(
            self.gateway_adapter_actions_enabled and status.present and status.owned
        )
        install_button.setEnabled(
            self.gateway_adapter_actions_enabled
            and (not status.present or status.owned or bool(migration_path))
        )
      except Exception as exc:
        status_label.setText(f"配置状态：读取失败 / 配置无效（未修改）：{exc}")
        install_button.setEnabled(False)
        remove_button.setEnabled(False)

  def _gateway_adapter_enabled_target(self, agent_id: str) -> bool:
    # Client-side enablement follows only the durable runtime policy, never
    # unsaved checkbox edits. The STDIO process independently rechecks this
    # same policy before each Tool call.
    try:
      gateway_enabled, agents = self.gateway_policy_store.read_snapshot()
    except (OSError, ValueError):
      return False
    return (
        gateway_enabled
        and bool(agents.get(agent_id, False))
        and self._gateway_agent_credential_present(agent_id)
    )

  def install_gateway_adapter(self, agent_id: str):
    """Explicitly add/update only the selected current-user global MCP entry."""
    if not self.gateway_adapter_actions_enabled:
      QMessageBox.information(
          self, "需要正式安装版本",
          "请在正式安装的 AirBridge.exe 中管理客户端配置；源码运行模式不写入客户端配置。",
      )
      return
    adapter, path, _path_label, _status_label, _install, _remove = (
        self._gateway_adapter_details(agent_id)
    )
    desired_enabled = self._gateway_adapter_enabled_target(agent_id)
    try:
      persisted_gateway_enabled, persisted_agents = (
          self.gateway_policy_store.read_snapshot()
      )
    except (OSError, ValueError):
      persisted_gateway_enabled, persisted_agents = False, {}

    if (
        persisted_gateway_enabled
        and bool(persisted_agents.get(agent_id, False))
        and not self._gateway_agent_credential_present(agent_id)
    ):
      QMessageBox.warning(
          self, "Agent 凭据不可用",
          f"{agent_id} 的已保存策略为 ON，但当前 Windows 用户 Credential Manager 中没有可读取的凭据。"
          "请先完成 M2.4 Agent Provision，再启用 Adapter。",
      )
      return

    migration_path = None
    try:
      status = adapter.inspect(path, agent_id)
      if status.present and not status.owned:
        migration_path = adapter.inspect_migration_candidate(path, agent_id)
      if status.present and not status.owned and not migration_path:
        QMessageBox.warning(
            self, "MCP Adapter 名称冲突",
            "该条目不符合可识别的旧版 AirBridge STDIO 配置结构，未修改客户端配置。",
        )
        return
    except Exception as exc:
      QMessageBox.warning(self, "MCP Adapter 检查失败", f"未修改客户端配置。{exc}")
      self.refresh_gateway_adapter_statuses()
      return

    migration_notice = ""
    if migration_path:
      migration_notice = (
          "\n\n检测到旧版 AirBridge Adapter 候选："
          f"\n旧程序：{migration_path}"
          f"\n新程序：{adapter.executable}"
          "\n\n仅凭配置结构不能证明旧程序来源。请确认该路径确实是此前安装的 AirBridge.exe。"
          "确认后将先备份配置，再只更新 AirBridge command 路径和 disabled 状态。"
      )
    answer = QMessageBox.question(
        self,
        "更新本机 MCP 配置",
        f"将只修改当前用户全局配置中的 {agent_id} AirBridge 专用条目：\n"
        f"{path}\n\n"
        f"本次写入状态：{'启用' if desired_enabled else '禁用'}。"
        "如文件已存在会先备份；其它 MCP 条目和注释将保留。"
        f"{migration_notice}\n\n是否继续？",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    if answer != QMessageBox.StandardButton.Yes:
      return
    try:
      if migration_path:
        result = adapter.migrate_legacy(path, agent_id, enabled=desired_enabled)
      else:
        result = adapter.install_or_update(path, agent_id, enabled=desired_enabled)
    except Exception as exc:
      QMessageBox.warning(
          self, "MCP Adapter 更新失败",
          f"未修改客户端配置。{exc}",
      )
      self.refresh_gateway_adapter_statuses()
      return
    detail = f"配置文件：{result.config_path}"
    if result.backup_path:
      detail += f"\n原文件备份：{result.backup_path}"
    unsaved_enable_intent = (
        self.chk_gateway_enabled.isChecked()
        and (
            self.chk_gateway_antigravity.isChecked()
            if agent_id == "antigravity"
            else self.chk_gateway_opencode.isChecked()
        )
        and not desired_enabled
    )
    policy_note = (
        "页面中有尚未保存的 ON 设置；本次保持 Adapter Disabled，请先保存 Gateway 设置。"
        if unsaved_enable_intent else ""
    )
    self.lbl_gateway_policy_status.setText(
        f"{agent_id} Adapter 已{'更新' if result.changed else '检查，无需修改'}。"
        f"{policy_note}{detail}"
    )
    self.refresh_gateway_adapter_statuses()

  def remove_gateway_adapter(self, agent_id: str):
    """Remove only the exact adapter entry whose AirBridge ownership marker matches."""
    if not self.gateway_adapter_actions_enabled:
      return
    adapter, path, _path_label, _status_label, _install, _remove = (
        self._gateway_adapter_details(agent_id)
    )
    answer = QMessageBox.question(
        self,
        "移除 AirBridge MCP 配置",
        f"将先把 {agent_id} 的本机 Agent 策略设为 OFF，阻止仍在运行的 STDIO 子进程继续调用；"
        f"然后只移除该配置文件中的 AirBridge 专用条目，不删除其它 MCP Server，也不撤销 M2.4 Credential：\n"
        f"{path}\n\n是否继续？",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    if answer != QMessageBox.StandardButton.Yes:
      return

    # Disable the Agent policy before editing the MCP host config. A running
    # STDIO child checks this policy before every Tool call and must stop now.
    try:
      try:
        gateway_enabled, agents = self.gateway_policy_store.read_snapshot()
      except (OSError, ValueError):
        # A malformed/unreadable prior snapshot is already fail-closed. Replace
        # it with a valid global-OFF snapshot before proceeding with removal.
        gateway_enabled, agents = False, {}
      agents[agent_id] = False
      self.gateway_policy_store.write(enabled=gateway_enabled, agents=agents)
    except (OSError, ValueError) as exc:
      QMessageBox.critical(
          self,
          "无法安全移除 Adapter",
          f"未修改客户端配置，因为无法先持久化 Agent OFF 策略。\n{exc}",
      )
      return

    self._gateway_policy_agents = dict(agents)
    self._gateway_policy_invalid = False
    if agent_id == "antigravity":
      self.chk_gateway_antigravity.setChecked(False)
    else:
      self.chk_gateway_opencode.setChecked(False)

    try:
      result = adapter.remove_owned(path, agent_id)
    except Exception as exc:
      QMessageBox.warning(
          self, "移除 Adapter 失败",
          f"Agent 策略已安全设为 OFF，但客户端配置未能移除。{exc}",
      )
      self.refresh_gateway_settings()
      return
    detail = f"配置文件：{result.config_path}"
    if result.backup_path:
      detail += f"\n原文件备份：{result.backup_path}"
    self.lbl_gateway_policy_status.setText(
        f"{agent_id} 的 Agent 策略已设为 OFF；"
        f"AirBridge MCP 条目已{'移除' if result.changed else '不存在'}。{detail}"
        "M2.4 Credential 保留，未执行 revoke。"
    )
    self.append_log("INFO", f"{agent_id} Agent policy switched OFF before Adapter removal; M2.4 credential retained")
    self.refresh_gateway_settings()

  def _sync_installed_gateway_adapters(self, gateway_enabled: bool, agents: Dict[str, bool]):
    """Apply the policy state to already-owned entries; never create missing entries."""
    outcomes = []
    for agent_id in ("antigravity", "opencode"):
      adapter, path, _path_label, _status_label, _install, _remove = (
          self._gateway_adapter_details(agent_id)
      )
      try:
        status = adapter.inspect(path, agent_id)
        if not status.present or not status.owned:
          continue
        credential_ok = self._gateway_agent_credential_present(agent_id)
        desired_enabled = gateway_enabled and bool(agents.get(agent_id, False)) and credential_ok
        result = adapter.install_or_update(path, agent_id, enabled=desired_enabled)
        if result.changed:
          outcomes.append(f"{agent_id}: {'enabled' if desired_enabled else 'disabled'}")
      except Exception as exc:
        outcomes.append(f"{agent_id}: not modified ({exc})")
    return outcomes

  def save_gateway_settings(self):
    """Persist Gateway / Agent ON-OFF state without touching M2.4 credentials."""
    if self._gateway_policy_invalid:
      answer = QMessageBox.question(
          self,
          "修复 Gateway 策略",
          "现有 gateway.json 缺失以外的读取 / 格式错误。"
          "是否用当前页面的设置替换该策略文件？",
          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
          QMessageBox.StandardButton.No,
      )
      if answer != QMessageBox.StandardButton.Yes:
        return

    enabled = self.chk_gateway_enabled.isChecked()
    agents = dict(self._gateway_policy_agents)
    agents["antigravity"] = self.chk_gateway_antigravity.isChecked()
    agents["opencode"] = self.chk_gateway_opencode.isChecked()

    if enabled:
      missing = [
          agent_id
          for agent_id in ("antigravity", "opencode")
          if agents.get(agent_id, False)
          and not self._gateway_agent_credential_present(agent_id)
      ]
      if missing:
        QMessageBox.warning(
            self,
            "Agent 凭据不可用",
            "以下已启用 Agent 在当前 Windows 用户 Credential Manager 中没有可读取的凭据："
            + ", ".join(missing)
            + "。请先完成 M2.4 Agent Provision，再保存 Gateway 设置。",
        )
        return

    try:
      self.gateway_policy_store.write(enabled=enabled, agents=agents)
    except (OSError, ValueError) as exc:
      QMessageBox.critical(self, "保存 Gateway 设置失败", str(exc))
      return

    self._gateway_policy_agents = agents
    self._gateway_policy_invalid = False
    adapter_outcomes = self._sync_installed_gateway_adapters(enabled, agents)
    self.lbl_gateway_policy_status.setText(
        "Gateway 设置已原子保存。全局状态："
        + ("ON" if enabled else "OFF")
        + "。每个已运行的 STDIO 子进程会在下一次 Tool 调用时检查新策略；"
        "M2.4 Credential 不会因关闭 Gateway 而删除或撤销。"
    )
    self.append_log(
        "INFO",
        "已保存本机 Gateway 策略："
        + ("ON" if enabled else "OFF")
        + f", Antigravity={agents['antigravity']}, OpenCode={agents['opencode']}",
    )
    if adapter_outcomes:
      self.append_log("INFO", "已同步已安装的 MCP Adapter：" + "; ".join(adapter_outcomes))
    self.refresh_gateway_adapter_statuses()

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

  def _browse_remote_eda_file(
      self, application: str, extensions: List[str]
  ) -> str:
    if not self.worker.ser or not self.worker.ser.is_open:
      QMessageBox.warning(
          self,
          "远程文件浏览",
          "当前尚未连接主机 B。请先建立 AirBridge 通信连接，再选择设计文件。",
      )
      return ""

    labels = {
        "capture": "选择主机 B 上的 OrCAD Capture 原理图 DSN",
        "allegro": "选择主机 B 上的 Allegro PCB BRD",
    }
    initial_path = (
        self.ipt_rfs_path.text().strip() or self.current_remote_dir
    )
    dialog = RemoteFileDialog(
        self.worker,
        self,
        labels.get(application, "选择主机 B 文件"),
        extensions,
        initial_path=initial_path,
    )
    if dialog.exec() != QDialog.DialogCode.Accepted:
      self.append_log("INFO", f"已取消 {application} 文件选择。")
      return ""

    selected_path = dialog.selected_path
    if selected_path:
      self.current_remote_dir = ntpath.dirname(selected_path)
      self.ipt_rfs_path.setText(self.current_remote_dir)
      self.append_log(
          "SUCCESS", f"已选择主机 B 文件: {selected_path}"
      )
    return selected_path

  def trigger_builtin_orcad_test(self):
    dsn_path = self._browse_remote_eda_file("capture", [".dsn"])
    if not dsn_path:
      return

    self.txt_eda_editor.setText(BUILTIN_TEST_ORCAD_TCL)
    self.current_hint = "builtin_test.tcl"
    ver = self.combo_eda_ver.currentData()
    self.append_log(
        "INFO",
        f"【打开原理图】已选择远程 DSN，准备通过 Capture 启动参数打开 "
        f"(目标版本: {ver})，正在下发...",
    )
    self._dispatch_script(
        BUILTIN_TEST_ORCAD_TCL,
        self.current_hint,
        ver,
        keep_open=True,
        target_file=dsn_path,
    )

  def trigger_builtin_allegro_test(self):
    brd_path = self._browse_remote_eda_file("allegro", [".brd"])
    if not brd_path:
      return

    self.txt_eda_editor.setText(BUILTIN_TEST_ALLEGRO_SKILL)
    self.current_hint = "builtin_test.il"
    ver = self.combo_eda_ver.currentData()
    self.append_log(
        "INFO",
        f"【打开PCB（Allegro）】已选择远程 BRD，准备启动 Allegro 并打开设计 "
        f"(目标版本: {ver})，正在下发...",
    )
    self._dispatch_script(
        BUILTIN_TEST_ALLEGRO_SKILL,
        self.current_hint,
        ver,
        keep_open=True,
        target_file=brd_path,
    )

  def trigger_close_orcad(self):
    self._dispatch_close_eda_application("capture")

  def trigger_close_allegro(self):
    self._dispatch_close_eda_application("allegro")

  def _dispatch_close_eda_application(self, application: str):
    labels = {
        "capture": "原理图 Capture",
        "allegro": "PCB Allegro",
    }
    label = labels.get(application, application)
    self.append_log("INFO", f"正在请求主机 B 关闭 {label}...")
    payload = json.dumps(
        {"application": application}, ensure_ascii=False
    ).encode("utf-8")
    self.worker.send_frame(FrameType.EDA_APP_CLOSE, payload)

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
      self,
      script_text: str,
      hint_name: str,
      version: str = "auto",
      keep_open: bool = False,
      target_file: str = "",
  ):
    payload = json.dumps(
        {
            "script": script_text,
            "hint": hint_name,
            "version": version,
            "keep_open": keep_open,
            "target_file": target_file,
        }
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


def run_gui() -> int:
  """Start the existing AirBridge desktop UI."""
  app = QApplication(sys.argv)
  window = AirBridgeMainWindow()
  window.show()
  return app.exec()


if __name__ == "__main__":
  sys.exit(run_gui())
