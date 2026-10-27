# fs_engine.py
# -*- coding: utf-8 -*-
"""
AirBridge Remote File System (RFS) Engine v2.0
特性：智能大文件安全分段预览、Cadence ANSI/GBK 自适应解码、系统保留项过滤
"""
import os
import sys
import shutil
import base64
import hashlib
import time
import zlib
import json
from typing import Dict, Any

IGNORE_SYSTEM_NAMES = {
    "$recycle.bin", "system volume information", "pagefile.sys", 
    "hiberfil.sys", "dumpstack.log", "swapfile.sys"
}

def decode_text_auto(raw_bytes: bytes) -> str:
    """自动智能嗅探编码（UTF-8 -> GB18030/GBK -> Latin1），消灭一切乱码与 Base64 误判"""
    encodings = ['utf-8', 'gb18030', 'gbk', 'cp936', 'utf-16']
    for enc in encodings:
        try:
            return raw_bytes.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw_bytes.decode('utf-8', errors='replace')

class FSEngine:
    @staticmethod
    def get_drives() -> list:
        drives = []
        if sys.platform == "win32":
            import string
            from ctypes import windll
            bitmask = windll.kernel32.GetLogicalDrives()
            for letter in string.ascii_uppercase:
                if bitmask & 1:
                    drive_path = f"{letter}:\\"
                    try:
                        total, used, free = shutil.disk_usage(drive_path)
                        drives.append({
                            "drive": f"{letter}:",
                            "total_gb": round(total / (1024**3), 2),
                            "free_gb": round(free / (1024**3), 2)
                        })
                    except Exception:
                        drives.append({"drive": f"{letter}:", "total_gb": 0, "free_gb": 0})
                bitmask >>= 1
        else:
            drives.append({"drive": "/", "total_gb": 0, "free_gb": 0})
        return drives

    @staticmethod
    def list_dir(target_dir: str) -> Dict[str, Any]:
        if len(target_dir) == 2 and target_dir[1] == ':':
            target_dir += "\\"
        target_dir = os.path.abspath(target_dir)

        if not os.path.exists(target_dir):
            return {"success": False, "error": f"目录不存在: {target_dir}"}
        if not os.path.isdir(target_dir):
            return {"success": False, "error": f"路径并非目录: {target_dir}"}

        items = []
        try:
            with os.scandir(target_dir) as it:
                for entry in it:
                    try:
                        name_lower = entry.name.lower()
                        if name_lower in IGNORE_SYSTEM_NAMES:
                            continue
                        stat = entry.stat()
                        is_dir = entry.is_dir()
                        items.append({
                            "name": entry.name,
                            "is_dir": is_dir,
                            "size": stat.st_size if not is_dir else 0,
                            "mtime": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stat.st_mtime))
                        })
                    except (PermissionError, OSError):
                        continue
            items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
            return {"success": True, "path": target_dir, "items": items}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def read_file(file_path: str, as_base64: bool = False, max_preview_bytes: int = 65536) -> Dict[str, Any]:
        """安全读取/预览：超过阈值自动截取前 64KB 内容并标注总大小，保护通信信道"""
        file_path = os.path.abspath(file_path)
        if not os.path.exists(file_path):
            return {"success": False, "error": f"文件不存在: {file_path}"}
        if os.path.isdir(file_path):
            return {"success": False, "error": f"目标为目录，无法读取文件内容: {file_path}"}

        size = os.path.getsize(file_path)
        is_truncated = size > max_preview_bytes

        try:
            # 仅读取前 max_preview_bytes 字节用于安全预览
            with open(file_path, "rb") as f:
                raw_data = f.read(max_preview_bytes)

            if as_base64:
                content = base64.b64encode(raw_data).decode('ascii')
                encoding = "base64"
            else:
                content = decode_text_auto(raw_data)
                encoding = "text"

            return {
                "success": True,
                "path": file_path,
                "size": size,
                "is_truncated": is_truncated,
                "preview_bytes": len(raw_data),
                "encoding": encoding,
                "content": content
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def write_file(file_path: str, content: str, is_base64: bool = False, mode: str = "w") -> Dict[str, Any]:
        file_path = os.path.abspath(file_path)
        try:
            parent = os.path.dirname(file_path)
            if parent and not os.path.exists(parent):
                os.makedirs(parent, exist_ok=True)

            if is_base64:
                raw_bytes = base64.b64decode(content.encode('ascii'))
                write_mode = "ab" if "a" in mode else "wb"
                with open(file_path, write_mode) as f:
                    f.write(raw_bytes)
            else:
                write_mode = "a" if "a" in mode else "w"
                with open(file_path, write_mode, encoding="utf-8") as f:
                    f.write(content)

            return {
                "success": True,
                "path": file_path,
                "size": os.path.getsize(file_path),
                "mtime": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(file_path)))
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def make_dir(dir_path: str) -> Dict[str, Any]:
        dir_path = os.path.abspath(dir_path)
        try:
            os.makedirs(dir_path, exist_ok=True)
            return {"success": True, "path": dir_path}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def remove(path: str) -> Dict[str, Any]:
        path = os.path.abspath(path)
        if not os.path.exists(path):
            return {"success": False, "error": f"目标不存在: {path}"}
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
            return {"success": True, "path": path}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def move(src: str, dst: str) -> Dict[str, Any]:
        src = os.path.abspath(src)
        dst = os.path.abspath(dst)
        if not os.path.exists(src):
            return {"success": False, "error": f"源路径不存在: {src}"}
        try:
            parent = os.path.dirname(dst)
            if parent and not os.path.exists(parent):
                os.makedirs(parent, exist_ok=True)
            shutil.move(src, dst)
            return {"success": True, "src": src, "dst": dst}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def stat_path(path: str) -> Dict[str, Any]:
        path = os.path.abspath(path)
        if not os.path.exists(path):
            return {"success": False, "exists": False, "path": path}
        try:
            st = os.stat(path)
            is_dir = os.path.isdir(path)
            md5_hash = ""
            if not is_dir and st.st_size <= 1024 * 1024 * 30:  # 30MB 内可生成 MD5
                h = hashlib.md5()
                with open(path, "rb") as f:
                    for chunk in iter(lambda: f.read(65536), b""):
                        h.update(chunk)
                md5_hash = h.hexdigest()

            return {
                "success": True,
                "exists": True,
                "path": path,
                "is_dir": is_dir,
                "size": st.st_size,
                "mtime": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.st_mtime)),
                "md5": md5_hash
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @classmethod
    def dispatch(cls, request: Dict[str, Any]) -> Dict[str, Any]:
        action = request.get("action", "")
        req_id = request.get("req_id", 0)
        result = {"req_id": req_id, "action": action}

        try:
            if action == "drives":
                res = {"success": True, "drives": cls.get_drives()}
            elif action == "ls":
                res = cls.list_dir(request.get("path", "."))
            elif action == "read":
                res = cls.read_file(
                    request.get("path", ""),
                    as_base64=request.get("as_base64", False),
                    max_preview_bytes=request.get("max_preview_bytes", 65536)
                )
            elif action == "write":
                res = cls.write_file(
                    request.get("path", ""),
                    request.get("content", ""),
                    is_base64=request.get("is_base64", False),
                    mode=request.get("mode", "w")
                )
            elif action == "mkdir":
                res = cls.make_dir(request.get("path", ""))
            elif action == "rm":
                res = cls.remove(request.get("path", ""))
            elif action == "mv":
                res = cls.move(request.get("src", ""), request.get("dst", ""))
            elif action == "stat":
                res = cls.stat_path(request.get("path", ""))
            else:
                res = {"success": False, "error": f"不支持的文件系统动作: {action}"}
        except Exception as e:
            res = {"success": False, "error": f"执行异常: {str(e)}"}

        result.update(res)
        return result

    @staticmethod
    def pack_payload(data_dict: dict) -> bytes:
        raw_json = json.dumps(data_dict).encode('utf-8')
        compressed = zlib.compress(raw_json, level=6)
        return b'\x7A' + compressed

    @staticmethod
    def unpack_payload(raw_bytes: bytes) -> dict:
        if not raw_bytes:
            return {}
        if raw_bytes.startswith(b'\x7A'):
            decompressed = zlib.decompress(raw_bytes[1:])
            return json.loads(decompressed.decode('utf-8'))
        else:
            return json.loads(raw_bytes.decode('utf-8'))
