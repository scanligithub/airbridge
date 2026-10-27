# protocol.py
# -*- coding: utf-8 -*-
"""
AirBridge Binary Framing Protocol (ABFP) v2.0 (全双工大文件拉取与极限吞吐版)
"""
import struct
import zlib
from typing import List

MAGIC_HEAD = b'\xAA\x55'
MAGIC_TAIL = b'\x55\xAA'
PROTOCOL_VER = 0x01
MAX_PAYLOAD_LEN = 65000  # 接近 16 位上限，支持 16KB~64KB 大块高效吞吐

class FrameType:
    ACK             = 0x01
    NACK            = 0x02
    HEARTBEAT       = 0x03
    TEXT_MSG        = 0x04
    FILE_START      = 0x05
    FILE_CHUNK      = 0x06
    FILE_END        = 0x07
    RAW_STREAM      = 0x08
    FILE_ABORT      = 0x09
    FILE_PULL_REQ   = 0x0A  # 【新增】远程反向文件拉取指令
    EDA_TASK_SUBMIT = 0x10
    EDA_LOG_STREAM  = 0x11
    EDA_TASK_FIN    = 0x12
    EDA_FORCE_KILL  = 0x13
    FS_REQUEST      = 0x20
    FS_RESPONSE     = 0x21

class ABFPFrame:
    def __init__(self, frame_type: int, seq_id: int, payload: bytes = b''):
        self.version = PROTOCOL_VER
        self.frame_type = frame_type
        self.seq_id = seq_id
        self.payload = payload

    def pack(self) -> bytes:
        length = len(self.payload)
        header_data = struct.pack('>BBHH', self.version, self.frame_type, self.seq_id, length)
        body = header_data + self.payload
        crc = zlib.crc32(body) & 0xFFFFFFFF
        return MAGIC_HEAD + body + struct.pack('>I', crc) + MAGIC_TAIL

class FrameParser:
    """流式字节解析状态机（防粘包、抗滑动噪声）"""
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data: bytes) -> List[ABFPFrame]:
        self.buffer.extend(data)
        frames = []

        while len(self.buffer) >= 14:
            head_idx = self.buffer.find(MAGIC_HEAD)
            if head_idx == -1:
                self.buffer = self.buffer[-1:]
                break
            if head_idx > 0:
                del self.buffer[:head_idx]

            if len(self.buffer) < 8:
                break

            version, f_type, seq_id, length = struct.unpack('>BBHH', self.buffer[2:8])
            if length > MAX_PAYLOAD_LEN:
                del self.buffer[:2]
                continue

            total_frame_len = 2 + 6 + length + 4 + 2
            if len(self.buffer) < total_frame_len:
                break

            tail = self.buffer[total_frame_len - 2 : total_frame_len]
            if tail != MAGIC_TAIL:
                del self.buffer[:2]
                continue

            check_data = self.buffer[2 : 8 + length]
            expected_crc, = struct.unpack('>I', self.buffer[total_frame_len - 6 : total_frame_len - 2])
            calc_crc = zlib.crc32(check_data) & 0xFFFFFFFF

            if expected_crc == calc_crc:
                payload = bytes(self.buffer[8 : 8 + length])
                frames.append(ABFPFrame(f_type, seq_id, payload))
                del self.buffer[:total_frame_len]
            else:
                del self.buffer[:2]

        return frames
