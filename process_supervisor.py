# process_supervisor.py
# -*- coding: utf-8 -*-
"""Generic, graceful process supervision for desktop Runtime profiles.

The supervisor never terminates a process forcibly. It enumerates every
matching image (including processes not launched by AirBridge), requests
normal GUI closure through WM_CLOSE, waits for actual exit, and blocks a new
runtime when a modal prompt or hung process remains.
"""
import csv
import ctypes
import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


class ProcessSupervisorError(RuntimeError):
    """Raised when exclusive process ownership cannot be established."""

    def __init__(self, message: str, report: Optional["ProcessSupervisorReport"] = None):
        super().__init__(message)
        self.report = report


@dataclass(frozen=True)
class ProcessSpec:
    app_id: str
    image_names: Tuple[str, ...]
    display_name: str

    def __post_init__(self):
        if not self.app_id.strip():
            raise ValueError("app_id must be non-empty")
        if not self.display_name.strip():
            raise ValueError("display_name must be non-empty")
        if not self.image_names or any(not name.strip() for name in self.image_names):
            raise ValueError("image_names must contain non-empty names")
        object.__setattr__(
            self,
            "image_names",
            tuple(name.lower() for name in self.image_names),
        )


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    image_name: str
    executable_path: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "pid": self.pid,
            "image_name": self.image_name,
            "executable_path": self.executable_path,
        }


@dataclass(frozen=True)
class ProcessWindow:
    hwnd: int
    title: str
    class_name: str
    visible: bool

    @property
    def is_dialog(self) -> bool:
        title = (self.title or "").lower()
        dialog_keywords = (
            "save",
            "confirm",
            "choices",
            "product choices",
            "license",
            "missing library",
            "missing file",
            "migration",
            "overwrite",
        )
        return self.class_name == "#32770" or any(
            keyword in title for keyword in dialog_keywords
        )


@dataclass
class ProcessCloseOutcome:
    process: ProcessInfo
    status: str
    message: str
    windows: List[ProcessWindow] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "process": self.process.to_dict(),
            "status": self.status,
            "message": self.message,
            "windows": [
                {
                    "hwnd": window.hwnd,
                    "title": window.title,
                    "class_name": window.class_name,
                    "visible": window.visible,
                    "is_dialog": window.is_dialog,
                }
                for window in self.windows
            ],
        }


@dataclass
class ProcessSupervisorReport:
    app_id: str
    state: str
    discovered: List[ProcessInfo] = field(default_factory=list)
    outcomes: List[ProcessCloseOutcome] = field(default_factory=list)
    remaining: List[ProcessInfo] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "app_id": self.app_id,
            "state": self.state,
            "discovered": [process.to_dict() for process in self.discovered],
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
            "remaining": [process.to_dict() for process in self.remaining],
        }


class ProcessBackend:
    """Minimal replaceable operating-system process/window backend."""

    def discover_processes(self, image_names: Sequence[str]) -> List[ProcessInfo]:
        raise NotImplementedError

    def list_windows(self, pid: int) -> List[ProcessWindow]:
        raise NotImplementedError

    def request_close(
        self,
        process: ProcessInfo,
        windows: Sequence[ProcessWindow],
    ) -> Tuple[int, List[str]]:
        raise NotImplementedError

    def wait_for_exit(self, pid: int, timeout_seconds: float) -> bool:
        raise NotImplementedError


class WindowsProcessBackend(ProcessBackend):
    """Windows implementation using tasklist and Win32 window/process APIs."""

    WM_CLOSE = 0x0010
    SYNCHRONIZE = 0x00100000
    WAIT_OBJECT_0 = 0x00000000
    WAIT_TIMEOUT = 0x00000102
    WAIT_FAILED = 0xFFFFFFFF

    def __init__(self):
        if os.name != "nt":
            raise ProcessSupervisorError(
                "WindowsProcessBackend is available only on Windows"
            )
        from ctypes import wintypes

        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)

        self._kernel32.OpenProcess.argtypes = [
            wintypes.DWORD, wintypes.BOOL, wintypes.DWORD
        ]
        self._kernel32.OpenProcess.restype = wintypes.HANDLE
        self._kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL
        self._kernel32.WaitForSingleObject.argtypes = [
            wintypes.HANDLE, wintypes.DWORD
        ]
        self._kernel32.WaitForSingleObject.restype = wintypes.DWORD

        self._user32.EnumWindows.argtypes = [
            ctypes.c_void_p, wintypes.LPARAM
        ]
        self._user32.EnumWindows.restype = wintypes.BOOL
        self._user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
        ]
        self._user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self._user32.GetWindowTextW.argtypes = [
            wintypes.HWND, wintypes.LPWSTR, ctypes.c_int
        ]
        self._user32.GetWindowTextW.restype = ctypes.c_int
        self._user32.GetClassNameW.argtypes = [
            wintypes.HWND, wintypes.LPWSTR, ctypes.c_int
        ]
        self._user32.GetClassNameW.restype = ctypes.c_int
        self._user32.IsWindowVisible.argtypes = [wintypes.HWND]
        self._user32.IsWindowVisible.restype = wintypes.BOOL
        self._user32.PostMessageW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        ]
        self._user32.PostMessageW.restype = wintypes.BOOL

    def _query_executable_path(self, pid: int) -> str:
        process = self._kernel32.OpenProcess(0x1000, False, int(pid))
        if not process:
            return ""
        try:
            size = ctypes.c_uint(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            ok = self._kernel32.QueryFullProcessImageNameW(
                process, 0, buffer, ctypes.byref(size)
            )
            return buffer.value if ok else ""
        finally:
            self._kernel32.CloseHandle(process)

    def discover_processes(self, image_names: Sequence[str]) -> List[ProcessInfo]:
        if not image_names:
            return []
        discovered: Dict[int, ProcessInfo] = {}
        for image_name in image_names:
            try:
                result = subprocess.run(
                    [
                        "tasklist",
                        "/FI",
                        f"IMAGENAME eq {image_name}",
                        "/FO",
                        "CSV",
                        "/NH",
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=8,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise ProcessSupervisorError(
                    f"cannot enumerate {image_name} processes; refusing to launch: {exc}"
                ) from exc
            if result.returncode != 0:
                raise ProcessSupervisorError(
                    f"tasklist failed for {image_name}; refusing to launch"
                )

            for row in csv.reader(result.stdout.splitlines()):
                if len(row) < 2 or not row[1].strip().isdigit():
                    continue
                pid = int(row[1].strip())
                discovered[pid] = ProcessInfo(
                    pid=pid,
                    image_name=row[0].strip(),
                    executable_path=self._query_executable_path(pid),
                )
        return [discovered[pid] for pid in sorted(discovered)]

    def list_windows(self, pid: int) -> List[ProcessWindow]:
        from ctypes import wintypes

        windows: List[ProcessWindow] = []
        callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
        )

        def callback(hwnd, _lparam):
            owner_pid = wintypes.DWORD()
            self._user32.GetWindowThreadProcessId(
                hwnd, ctypes.byref(owner_pid)
            )
            if owner_pid.value != int(pid):
                return True

            title_buffer = ctypes.create_unicode_buffer(1024)
            class_buffer = ctypes.create_unicode_buffer(256)
            self._user32.GetWindowTextW(hwnd, title_buffer, len(title_buffer))
            self._user32.GetClassNameW(hwnd, class_buffer, len(class_buffer))
            windows.append(
                ProcessWindow(
                    hwnd=int(hwnd),
                    title=title_buffer.value,
                    class_name=class_buffer.value,
                    visible=bool(self._user32.IsWindowVisible(hwnd)),
                )
            )
            return True

        callback_ref = callback_type(callback)
        if not self._user32.EnumWindows(callback_ref, 0):
            error_code = ctypes.get_last_error()
            if error_code:
                raise ProcessSupervisorError(
                    f"EnumWindows failed for PID {pid}: WinError {error_code}"
                )
        return windows

    def request_close(
        self,
        process: ProcessInfo,
        windows: Sequence[ProcessWindow],
    ) -> Tuple[int, List[str]]:
        sent = 0
        errors: List[str] = []
        current_path = self._query_executable_path(process.pid)
        if not current_path:
            return 0, [
                f"cannot verify executable path for PID={process.pid}; "
                "refusing to send WM_CLOSE"
            ]

        current_name = os.path.basename(current_path).lower()
        expected_name = process.image_name.lower()
        if current_name != expected_name:
            return 0, [
                f"PID={process.pid} image changed from {expected_name} "
                f"to {current_name}; refusing to close a reused PID"
            ]

        if process.executable_path:
            expected_path = os.path.normcase(os.path.abspath(process.executable_path))
            actual_path = os.path.normcase(os.path.abspath(current_path))
            if expected_path != actual_path:
                return 0, [
                    f"PID={process.pid} executable path changed; refusing to "
                    "close a possibly reused PID"
                ]

        for window in windows:
            if not window.visible or window.is_dialog:
                continue
            ok = self._user32.PostMessageW(
                ctypes.c_void_p(window.hwnd),
                self.WM_CLOSE,
                0,
                0,
            )
            if ok:
                sent += 1
            else:
                errors.append(
                    f"WM_CLOSE failed for HWND={window.hwnd} PID={process.pid} "
                    f"(WinError {ctypes.get_last_error()})"
                )
        return sent, errors

    def wait_for_exit(self, pid: int, timeout_seconds: float) -> bool:
        process = self._kernel32.OpenProcess(self.SYNCHRONIZE, False, int(pid))
        if not process:
            # The process may have exited between enumeration and OpenProcess.
            error_code = ctypes.get_last_error()
            if error_code in (6, 87):
                return True
            raise ProcessSupervisorError(
                f"cannot wait for PID {pid}: WinError {error_code}"
            )
        try:
            timeout_ms = max(0, int(float(timeout_seconds) * 1000))
            result = self._kernel32.WaitForSingleObject(process, timeout_ms)
            if result == self.WAIT_OBJECT_0:
                return True
            if result == self.WAIT_TIMEOUT:
                return False
            if result == self.WAIT_FAILED:
                raise ProcessSupervisorError(
                    f"WaitForSingleObject failed for PID {pid}: "
                    f"WinError {ctypes.get_last_error()}"
                )
            raise ProcessSupervisorError(
                f"unexpected process wait result {result!r} for PID {pid}"
            )
        finally:
            self._kernel32.CloseHandle(process)


class ProcessSupervisor:
    """Ensure one runtime profile has exclusive ownership of its app process."""

    DEFAULT_SPECS = (
        ProcessSpec("capture", ("Capture.exe",), "OrCAD Capture"),
        ProcessSpec("allegro", ("allegro.exe",), "Cadence Allegro"),
    )

    def __init__(
        self,
        backend: Optional[ProcessBackend] = None,
        specs: Optional[Iterable[ProcessSpec]] = None,
    ):
        self.backend = backend or WindowsProcessBackend()
        supplied = tuple(specs or self.DEFAULT_SPECS)
        self._specs = {spec.app_id: spec for spec in supplied}
        if len(self._specs) != len(supplied):
            raise ValueError("ProcessSpec app_id values must be unique")

    def ensure_exclusive(
        self,
        app_id: str,
        timeout_seconds: float = 60.0,
    ) -> ProcessSupervisorReport:
        spec = self._specs.get(app_id)
        if spec is None:
            raise ProcessSupervisorError(f"unknown managed application: {app_id}")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

        processes = self.backend.discover_processes(spec.image_names)
        report = ProcessSupervisorReport(
            app_id=app_id,
            state="ready" if not processes else "closing_existing",
            discovered=list(processes),
        )
        if not processes:
            return report

        outcomes: List[ProcessCloseOutcome] = []
        wait_deadline = time.monotonic() + float(timeout_seconds)

        # First inspect every process. Existing modal prompts are never closed
        # or acknowledged automatically; the process is left untouched.
        eligible: List[Tuple[ProcessInfo, List[ProcessWindow]]] = []
        for process in processes:
            windows = self.backend.list_windows(process.pid)
            dialogs = [
                window for window in windows
                if window.visible and window.is_dialog
            ]
            if dialogs:
                outcomes.append(
                    ProcessCloseOutcome(
                        process,
                        "blocked_by_dialog",
                        "visible modal/save/confirmation dialog detected; "
                        "no window message was sent",
                        dialogs,
                    )
                )
                continue

            main_windows = [
                window for window in windows
                if window.visible and not window.is_dialog
            ]
            if not main_windows:
                outcomes.append(
                    ProcessCloseOutcome(
                        process,
                        "blocked_no_main_window",
                        "no visible main window was found; refusing to infer "
                        "that the process can be closed safely",
                        windows,
                    )
                )
                continue
            eligible.append((process, main_windows))

        # Request graceful closure for all eligible processes before waiting.
        requested: List[Tuple[ProcessInfo, List[ProcessWindow]]] = []
        for process, windows in eligible:
            sent, errors = self.backend.request_close(process, windows)
            if sent <= 0 or errors:
                message = "; ".join(errors) if errors else "no WM_CLOSE message was accepted"
                outcomes.append(
                    ProcessCloseOutcome(process, "close_request_failed", message, windows)
                )
            else:
                requested.append((process, windows))
                outcomes.append(
                    ProcessCloseOutcome(
                        process,
                        "close_requested",
                        f"sent WM_CLOSE to {sent} visible main window(s); "
                        "waiting for process exit",
                        windows,
                    )
                )

        # Wait for process handles, including manually launched processes.
        for process, windows in requested:
            remaining = max(0.0, wait_deadline - time.monotonic())
            exited = self.backend.wait_for_exit(process.pid, remaining)
            if exited:
                for outcome in outcomes:
                    if outcome.process.pid == process.pid:
                        outcome.status = "exited"
                        outcome.message = "process exited gracefully after WM_CLOSE"
                        break
                continue

            latest_windows = self.backend.list_windows(process.pid)
            dialogs = [
                window for window in latest_windows
                if window.visible and window.is_dialog
            ]
            for outcome in outcomes:
                if outcome.process.pid == process.pid:
                    outcome.status = (
                        "blocked_by_dialog" if dialogs else "timeout"
                    )
                    outcome.message = (
                        "process remains alive with a visible modal/save prompt; "
                        "no automatic dialog interaction or force termination was attempted"
                        if dialogs
                        else f"process did not exit within {timeout_seconds:.1f}s after WM_CLOSE"
                    )
                    outcome.windows = dialogs or latest_windows
                    break

        remaining = self.backend.discover_processes(spec.image_names)
        remaining_pids = {process.pid for process in remaining}
        for outcome in outcomes:
            if outcome.process.pid not in remaining_pids and outcome.status != "exited":
                outcome.status = "exited"
                outcome.message = (
                    "process exited while graceful supervision was in progress"
                )
        report.outcomes = outcomes
        report.remaining = remaining
        if remaining:
            report.state = "blocked"
            detail = "; ".join(
                f"PID {outcome.process.pid} [{outcome.status}: {outcome.message}]"
                for outcome in outcomes
                if outcome.status not in ("exited",)
            )
            remaining_text = ", ".join(
                f"{proc.image_name} PID={proc.pid}" for proc in remaining
            )
            raise ProcessSupervisorError(
                f"{spec.display_name} processes remain; refusing to start a new "
                f"instance. Remaining: {remaining_text}. {detail}",
                report,
            )

        if any(outcome.status not in ("exited",) for outcome in outcomes):
            report.state = "blocked"
            raise ProcessSupervisorError(
                f"{spec.display_name} close requests did not all complete cleanly",
                report,
            )

        report.state = "closed_existing"
        return report
