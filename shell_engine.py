# shell_engine.py
# -*- coding: utf-8 -*-
"""
AirBridge Remote Shell Engine v1.0
默认 PowerShell，支持同步/异步执行、实时输出流、任务管理与安全黑名单。
"""
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

# ---------------------------------------------------------------------------
# 危险命令黑名单（不区分大小写，子串匹配）
# 命中后直接拒绝执行，返回明确错误。
# ---------------------------------------------------------------------------
DANGEROUS_PATTERNS = [
    # 磁盘 / 分区破坏
    r"\bformat\b",
    r"\bdiskpart\b",
    r"\bClear-Disk\b",
    r"\bInitialize-Disk\b",
    r"\bRemove-Partition\b",
    r"\bNew-Partition\b",
    r"\bcipher\s+/w",
    r"\bformat-volume\b",
    # 关机 / 重启
    r"\bshutdown\b",
    r"\brestart-computer\b",
    r"\bstop-computer\b",
    r"\bRestart-Computer\b",
    r"\bStop-Computer\b",
    r"\bshutdown\.exe\b",
    # 用户 / 账户破坏
    r"\bnet\s+user\b.*(/delete|\s+delete)",
    r"\bRemove-LocalUser\b",
    r"\bDisable-LocalUser\b",
    r"\bnet\s+localgroup\b.*administrators.*(/add|/delete)",
    # 注册表高危
    r"\breg\s+delete\b",
    r"\bRemove-ItemProperty\b.*HKLM",
    r"\bRemove-Item\b.*HKLM:\\",
    # 强制递归删除系统关键路径
    r"Remove-Item\s+.*-Recurse.*-Force.*(C:\\|C:/|\\Windows|\\System32|\\Program Files)",
    r"rmdir\s+/s\s+/q\s+[cC]:",
    r"del\s+/[sSfF].*[cC]:\\",
    # 其他高危
    r"\bbcdedit\b",
    r"\bbootrec\b",
    r"\bwipe\b",
    r"\bcipher\b.*/w",
]

_COMPILED_DANGEROUS = [re.compile(p, re.IGNORECASE) for p in DANGEROUS_PATTERNS]

DEFAULT_CWD = r"E:\_airbridge_temp"
DEFAULT_TIMEOUT = 60  # 秒，0 表示不超时（仅异步场景建议）


def is_dangerous(command: str) -> Optional[str]:
    """返回命中的危险模式描述，安全则返回 None。"""
    if not command or not command.strip():
        return "空命令"
    for pat in _COMPILED_DANGEROUS:
        if pat.search(command):
            return f"命中安全黑名单: {pat.pattern}"
    return None


@dataclass
class ShellTask:
    task_id: str
    command: str
    shell: str  # "powershell" | "cmd"
    cwd: str
    started_at: float
    process: Optional[subprocess.Popen] = None
    exit_code: Optional[int] = None
    stdout_buf: List[str] = field(default_factory=list)
    stderr_buf: List[str] = field(default_factory=list)
    finished: bool = False
    killed: bool = False
    async_mode: bool = False


class ShellEngine:
    """
    远程 Shell 执行引擎。
    - 默认 powershell.exe -NoProfile -NonInteractive
    - 支持同步（等待结束）与异步（后台 + 实时输出回调）
    - 任务注册表，支持按 task_id 强杀
    """

    def __init__(self):
        self._tasks: Dict[str, ShellTask] = {}
        self._lock = threading.Lock()
        self._output_callback: Optional[Callable[[str, str, str], None]] = None
        # callback(task_id, stream_name, line)  stream_name = "stdout" | "stderr" | "system"

    def set_output_callback(self, cb: Callable[[str, str, str], None]):
        self._output_callback = cb

    def _emit(self, task_id: str, stream: str, line: str):
        if self._output_callback:
            try:
                self._output_callback(task_id, stream, line)
            except Exception:
                pass

    def _make_task_id(self) -> str:
        return f"shell-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"

    def _resolve_shell(self, shell: str) -> List[str]:
        shell = (shell or "powershell").lower().strip()
        if shell in ("cmd", "cmd.exe"):
            return ["cmd.exe", "/c"]
        # 默认 PowerShell
        return [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy", "Bypass",
            "-Command",
        ]

    def list_tasks(self) -> List[Dict[str, Any]]:
        with self._lock:
            result = []
            for t in self._tasks.values():
                result.append({
                    "task_id": t.task_id,
                    "command": t.command[:200],
                    "shell": t.shell,
                    "cwd": t.cwd,
                    "started_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t.started_at)),
                    "finished": t.finished,
                    "killed": t.killed,
                    "exit_code": t.exit_code,
                    "async": t.async_mode,
                    "pid": t.process.pid if t.process and t.process.poll() is None else None,
                })
            return result

    def kill_task(self, task_id: str = "", pid: int = 0) -> Dict[str, Any]:
        with self._lock:
            target: Optional[ShellTask] = None
            if task_id and task_id in self._tasks:
                target = self._tasks[task_id]
            elif pid:
                for t in self._tasks.values():
                    if t.process and t.process.pid == pid:
                        target = t
                        break

            if not target:
                return {"success": False, "error": f"未找到任务 task_id={task_id} pid={pid}"}

            if target.finished:
                return {"success": True, "message": "任务已结束", "task_id": target.task_id}

            try:
                if target.process and target.process.poll() is None:
                    # Windows: 先 terminate，再 kill
                    target.process.terminate()
                    try:
                        target.process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        target.process.kill()
                    target.killed = True
                    target.finished = True
                    target.exit_code = -9
                    self._emit(target.task_id, "system", "[AirBridge] 任务已被强制终止")
                return {"success": True, "task_id": target.task_id, "killed": True}
            except Exception as e:
                return {"success": False, "error": str(e), "task_id": target.task_id}

    def exec(
        self,
        command: str,
        shell: str = "powershell",
        cwd: str = "",
        timeout: int = DEFAULT_TIMEOUT,
        async_mode: bool = False,
        env: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        执行命令。
        - async_mode=False: 阻塞直到结束或超时，返回完整 stdout/stderr/exit_code
        - async_mode=True: 立即返回 task_id，输出通过 callback 实时推送
        """
        danger = is_dangerous(command)
        if danger:
            return {
                "success": False,
                "error": f"安全策略拦截: {danger}",
                "blocked": True,
            }

        work_dir = cwd.strip() if cwd and cwd.strip() else DEFAULT_CWD
        try:
            os.makedirs(work_dir, exist_ok=True)
        except Exception:
            work_dir = os.getcwd()

        task_id = self._make_task_id()
        shell_args = self._resolve_shell(shell)
        full_cmd = shell_args + [command]

        task = ShellTask(
            task_id=task_id,
            command=command,
            shell=shell or "powershell",
            cwd=work_dir,
            started_at=time.time(),
            async_mode=async_mode,
        )

        with self._lock:
            self._tasks[task_id] = task

        try:
            popen_env = os.environ.copy()
            if env:
                popen_env.update({str(k): str(v) for k, v in env.items()})

            proc = subprocess.Popen(
                full_cmd,
                cwd=work_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                env=popen_env,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            task.process = proc
        except Exception as e:
            task.finished = True
            task.exit_code = -1
            return {
                "success": False,
                "task_id": task_id,
                "error": f"启动进程失败: {e}",
            }

        if async_mode:
            # 后台线程读取输出并回调
            def _reader():
                try:
                    def _read_stream(stream, name):
                        try:
                            for line in iter(stream.readline, ""):
                                if not line:
                                    break
                                line = line.rstrip("\r\n")
                                task.stdout_buf.append(line) if name == "stdout" else task.stderr_buf.append(line)
                                self._emit(task_id, name, line)
                        except Exception:
                            pass

                    t_out = threading.Thread(target=_read_stream, args=(proc.stdout, "stdout"), daemon=True)
                    t_err = threading.Thread(target=_read_stream, args=(proc.stderr, "stderr"), daemon=True)
                    t_out.start()
                    t_err.start()

                    try:
                        if timeout and timeout > 0:
                            proc.wait(timeout=timeout)
                        else:
                            proc.wait()
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        task.killed = True
                        self._emit(task_id, "system", f"[AirBridge] 超时 ({timeout}s) 已强制终止")

                    t_out.join(timeout=1)
                    t_err.join(timeout=1)
                    task.exit_code = proc.returncode if not task.killed else -9
                    task.finished = True
                    self._emit(task_id, "system", f"[AirBridge] 任务结束 exit_code={task.exit_code}")
                except Exception as ex:
                    task.finished = True
                    task.exit_code = -1
                    self._emit(task_id, "system", f"[AirBridge] 内部错误: {ex}")

            threading.Thread(target=_reader, daemon=True).start()
            return {
                "success": True,
                "task_id": task_id,
                "status": "running",
                "async": True,
                "pid": proc.pid,
            }

        # ---------- 同步模式 ----------
        try:
            stdout, stderr = proc.communicate(timeout=timeout if timeout > 0 else None)
            task.exit_code = proc.returncode
            task.finished = True
            task.stdout_buf = (stdout or "").splitlines()
            task.stderr_buf = (stderr or "").splitlines()
            return {
                "success": True,
                "task_id": task_id,
                "exit_code": task.exit_code,
                "stdout": stdout or "",
                "stderr": stderr or "",
                "duration_ms": int((time.time() - task.started_at) * 1000),
                "cwd": work_dir,
            }
        except subprocess.TimeoutExpired:
            proc.kill()
            task.killed = True
            task.finished = True
            task.exit_code = -9
            try:
                stdout, stderr = proc.communicate(timeout=2)
            except Exception:
                stdout, stderr = "", ""
            return {
                "success": False,
                "task_id": task_id,
                "error": f"命令超时 ({timeout}s) 已强制终止",
                "exit_code": -9,
                "stdout": stdout or "",
                "stderr": stderr or "",
                "killed": True,
            }
        except Exception as e:
            task.finished = True
            task.exit_code = -1
            return {
                "success": False,
                "task_id": task_id,
                "error": str(e),
            }

    def get_task_result(self, task_id: str) -> Dict[str, Any]:
        with self._lock:
            t = self._tasks.get(task_id)
            if not t:
                return {"success": False, "error": "任务不存在"}
            return {
                "success": True,
                "task_id": t.task_id,
                "finished": t.finished,
                "killed": t.killed,
                "exit_code": t.exit_code,
                "stdout": "\n".join(t.stdout_buf),
                "stderr": "\n".join(t.stderr_buf),
                "command": t.command,
                "cwd": t.cwd,
            }


# 模块级单例，方便 Worker 直接使用
shell_engine = ShellEngine()
