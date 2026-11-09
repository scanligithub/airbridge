# airbridge_app.py
# -*- coding: utf-8 -*-
"""
AirBridge - 通用串口数据传输通道 v2.5
可靠文件传输、原始数据传输、远程文件系统与可选远程 Shell
"""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import os
import queue
import sys
import threading
import time
from typing import Any, Dict, List, Tuple

from fs_engine import FSEngine
from protocol import ABFPFrame, FrameParser, FrameType, MAX_PAYLOAD_LEN
from shell_engine import shell_engine, ShellEngine
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

class SerialWorkerSignals(QObject):
  sig_connected = Signal(bool, str)
  sig_rx_log = Signal(str, str)
  sig_tx_activity = Signal()
  sig_rx_activity = Signal()
  sig_file_progress = Signal(int, int)
  sig_file_done = Signal(str)
  sig_file_aborted = Signal(str)
  sig_data_received = Signal(bytes)
  sig_fs_response = Signal(dict)


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
    self.shell_engine = shell_engine
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

  def connect_serial(self, port: str, baud: int):
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
    # 高优先级控制帧（ACK/NACK/ABORT/HEARTBEAT）入高优队列，防止被大文件数据包队头阻塞
    if frame_type in (
        FrameType.ACK,
        FrameType.NACK,
        FrameType.FILE_ABORT,
        FrameType.HEARTBEAT,
    ):
      self.tx_high_queue.put(frame)
    else:
      self.tx_low_queue.put(frame)

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
    if frame.frame_type == FrameType.ACK:
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

    elif frame.frame_type == FrameType.RAW_STREAM:
      # Raw payload is transport data only. It is never parsed or executed here.
      self.sig.sig_data_received.emit(frame.payload)

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
        self.sig.sig_rx_log.emit(
            "SHELL",
            f"任务结果: {json.dumps(obj, ensure_ascii=False)[:500]}",
        )
        # 若是同步等待中的结果，也可通过 fs_pending 类似机制唤醒（简化：日志即可）
      except Exception:
        pass

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
    result = FSEngine.dispatch(req_obj)
    compressed_resp = FSEngine.pack_payload(result)
    self.send_frame(FrameType.FS_RESPONSE, compressed_resp)


  def _handle_shell_exec(self, req: Dict[str, Any]):
    """在本机（通常是主机 B）执行远程 Shell 请求，并回传结果。"""
    command = req.get("command") or req.get("cmd") or ""
    shell = req.get("shell", "powershell")
    cwd = req.get("cwd", "")
    timeout = int(req.get("timeout", 60) or 60)
    async_mode = bool(req.get("async", False))
    env = req.get("env") or {}

    res = self.shell_engine.exec(
        command=command,
        shell=shell,
        cwd=cwd,
        timeout=timeout,
        async_mode=async_mode,
        env=env if isinstance(env, dict) else {},
    )

    # 同步结果 或 异步启动回执 都通过 SHELL_RESULT 回传
    self.send_frame(
        FrameType.SHELL_RESULT,
        json.dumps(res, ensure_ascii=False).encode("utf-8"),
    )
    if not async_mode:
      # 同步时也把关键信息打到本地日志
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
        self._reply_json(404, {"error": "Not Found"})

      def do_POST(self):
        content_len = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_len).decode("utf-8", errors="ignore")

        if self.path == "/api/v1/data/send":
          try:
            if raw_body.strip().startswith("{"):
              request_data = json.loads(raw_body)
              data_text = request_data.get("data", "")
            else:
              data_text = raw_body
            if not isinstance(data_text, str):
              raise ValueError("'data' must be a UTF-8 string")
            payload = data_text.encode("utf-8")
            if not payload:
              self._reply_json(400, {"error": "data must not be empty"})
            elif len(payload) > MAX_PAYLOAD_LEN:
              self._reply_json(
                  413,
                  {
                      "error": "payload too large for one RAW_STREAM frame",
                      "max_bytes": MAX_PAYLOAD_LEN,
                      "hint": "Use the normal file-transfer function for larger content.",
                  },
              )
            else:
              worker_ref.send_frame(FrameType.RAW_STREAM, payload)
              self._reply_json(
                  202,
                  {
                      "status": "transmitted",
                      "bytes": len(payload),
                      "execution": "not_performed",
                  },
              )
          except Exception as exc:
            self._reply_json(400, {"error": str(exc)})
        elif self.path == "/api/v1/message":
          worker_ref.send_frame(FrameType.TEXT_MSG, raw_body.encode("utf-8"))
          self._reply_json(200, {"status": "sent"})


        # ---------- Remote Shell API ----------
        elif self.path in ("/api/v1/shell/exec", "/api/shell/exec"):
          try:
            task = json.loads(raw_body) if raw_body.strip() else {}
          except Exception:
            task = {"command": raw_body}
          # 通过串口把命令发到对端（主机 B）执行
          worker_ref.send_frame(
              FrameType.SHELL_EXEC,
              json.dumps(task, ensure_ascii=False).encode("utf-8"),
          )
          # 异步模式立即返回；同步模式当前简化为“已提交”，结果走日志/后续轮询
          self._reply_json(200, {
              "status": "submitted",
              "async": bool(task.get("async", False)),
              "hint": "结果将通过 SHELL_RESULT / 控制台日志回传；异步任务可用 /api/v1/shell/tasks 查询",
          })

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
    self.setWindowTitle("AirBridge - 通用串口数据传输通道 v2.5")
    self.resize(1160, 820)
    self.setStyleSheet(DARK_STYLE)

    self.current_remote_dir = "e:\\RK3588智能盒子"

    self.signals = SerialWorkerSignals()
    self.worker = SerialWorker(self.signals)
    self.worker.start()

    self.ipc = AntigravityIPCServer(self.worker, port=8888)
    self.ipc.start()

    self.init_ui()
    self.bind_events()
    self.scan_ports()
    # Auto-connect to host B: prefer the USB/FTDI gateway and verify the link with a probe request
    QTimer.singleShot(100, self.auto_connect_serial)

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
      res = self.worker.send_fs_request_sync({"action": "drives"}, timeout=8.0)
      if res.get("success"):
        self.statusBar().showMessage(f"串口链路已连接: {dev}")
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

    # Tab 3: 通用原始数据通道（不识别、不执行发送内容）
    data_tab = QWidget()
    data_layout = QVBoxLayout(data_tab)
    data_ctrl = QHBoxLayout()
    self.btn_load_data = QPushButton("📂 加载文本文件...")
    self.btn_send_data = QPushButton("➡ 发送原始数据")
    self.btn_send_data.setStyleSheet("background-color: #0E639C;")
    data_ctrl.addWidget(self.btn_load_data)
    data_ctrl.addWidget(self.btn_send_data)
    data_ctrl.addStretch()
    data_layout.addLayout(data_ctrl)

    data_layout.addWidget(QLabel(
        "发送区（UTF-8 文本；最大单帧 65,000 字节。大文件请使用文件传输。）"
    ))
    self.txt_data_editor = QTextEdit()
    self.txt_data_editor.setPlaceholderText(
        "在此粘贴要发送到对端的数据。AirBridge 只传输原始内容，不识别或执行任何代码。"
    )
    data_layout.addWidget(self.txt_data_editor)

    data_layout.addWidget(QLabel("最近收到的原始数据（只显示，不执行）："))
    self.txt_data_received = QTextEdit()
    self.txt_data_received.setReadOnly(True)
    data_layout.addWidget(self.txt_data_received)
    self.tabs.addTab(data_tab, "通用数据通道")

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

    self.btn_load_data.clicked.connect(self.load_data_file)
    self.btn_send_data.clicked.connect(self.send_raw_data)

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
    self.signals.sig_data_received.connect(self.on_raw_data_received)
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

  def load_data_file(self):
    path, _ = QFileDialog.getOpenFileName(
        self, "选择要加载的文本文件", "", "文本文件 (*.txt *.tcl *.il *.skill *.scr *.json *.csv);;所有文件 (*.*)"
    )
    if path:
      with open(path, "r", encoding="utf-8-sig", errors="replace") as file:
        self.txt_data_editor.setPlainText(file.read())
      self.append_log("INFO", f"已加载文本文件: {os.path.basename(path)}")

  def send_raw_data(self):
    text = self.txt_data_editor.toPlainText()
    if not text:
      self.append_log("WARN", "发送内容为空。")
      return
    payload = text.encode("utf-8")
    if len(payload) > MAX_PAYLOAD_LEN:
      self.append_log(
          "WARN",
          f"内容为 {len(payload)} 字节，超过单帧上限 {MAX_PAYLOAD_LEN}；请改用文件传输。",
      )
      return
    self.worker.send_frame(FrameType.RAW_STREAM, payload)
    self.append_log(
        "SUCCESS",
        f"已将 {len(payload)} 字节原始数据加入串口发送队列；AirBridge 不执行发送内容。",
    )

  def on_raw_data_received(self, payload: bytes):
    try:
      display_text = payload.decode("utf-8")
    except UnicodeDecodeError:
      display_text = "[收到非 UTF-8 原始数据，以下为 HEX 展示]\n" + payload.hex(" ")
    self.txt_data_received.setPlainText(display_text)
    self.append_log(
        "SUCCESS",
        f"收到对端原始数据: {len(payload)} 字节（未解析、未执行）。",
    )

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
