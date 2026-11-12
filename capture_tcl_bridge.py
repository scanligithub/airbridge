# capture_tcl_bridge.py
# -*- coding: utf-8 -*-
"""Persistent Capture Tcl Bridge and Runtime Adapter.

The bridge is loaded by Capture's capAutoLoad mechanism and polls a
session-specific file queue from Capture's own Tcl event loop. No Cadence
Communication Server is required. This module does not force-terminate a
Capture process and deliberately refuses to launch alongside an unknown
Capture.exe instance; cross-process lifecycle management belongs to P1.7.
"""
import base64
import csv
import hashlib
import os
import secrets
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

from runtime_adapter import (
    RuntimeAdapter,
    RuntimeAdapterError,
    RuntimeExecutionContext,
    RuntimeExecutionRequest,
)


MAX_CAPTURE_SCRIPT_BYTES = 1024 * 1024
DEFAULT_POLL_INTERVAL_MS = 200
DEFAULT_RESULT_RETENTION_SECONDS = 24 * 60 * 60


class CaptureTclBridgeError(RuntimeAdapterError):
    """Raised when the Capture Bridge cannot be installed or used."""


def _tcl_quote(value: str) -> str:
    """Quote one Tcl word using double-quoted Tcl substitutions safely."""
    text = str(value)
    text = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )
    return '"' + text + '"'


def _decode_record(path: Path) -> Dict[str, str]:
    record: Dict[str, str] = {}
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except OSError as exc:
        raise CaptureTclBridgeError(f"cannot read bridge record {path}: {exc}") from exc

    for line in lines:
        key, sep, encoded = line.partition("=")
        if not sep or not key:
            continue
        try:
            raw = base64.b64decode(encoded.encode("ascii"), validate=True)
            record[key] = raw.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise CaptureTclBridgeError(
                f"bridge record contains invalid base64/UTF-8 field: {key}"
            ) from exc
    return record


def _encode_record(record: Mapping[str, Any]) -> str:
    lines = []
    for key, value in record.items():
        if not key or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for ch in key):
            raise CaptureTclBridgeError(f"invalid bridge record field name: {key!r}")
        raw = str(value).encode("utf-8")
        encoded = base64.b64encode(raw).decode("ascii")
        lines.append(f"{key}={encoded}")
    return "\n".join(lines) + "\n"


def list_running_capture_pids() -> list:
    """Conservatively list Capture.exe PIDs on Windows; fail closed on errors."""
    if os.name != "nt":
        return []

    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Capture.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CaptureTclBridgeError(
            f"cannot verify existing Capture processes; refusing to launch: {exc}"
        ) from exc

    if result.returncode != 0:
        raise CaptureTclBridgeError(
            "tasklist failed while checking existing Capture processes; "
            "refusing to launch another instance"
        )

    pids = []
    try:
        for row in csv.reader(result.stdout.splitlines()):
            if len(row) >= 2 and row[1].strip().isdigit():
                pids.append(int(row[1].strip()))
    except csv.Error as exc:
        raise CaptureTclBridgeError(
            f"cannot parse Capture process list; refusing to launch: {exc}"
        ) from exc
    return sorted(set(pids))


class CaptureTclBridgeSession:
    """One local Capture Tcl Interpreter session backed by an atomic file queue."""

    def __init__(
        self,
        session_id: str,
        startup_nonce: str,
        session_dir: Path,
        autoload_dir: Path,
        runtime_version: str = "unknown",
        target_file: str = "",
        process: Any = None,
        poll_interval_ms: int = DEFAULT_POLL_INTERVAL_MS,
    ):
        try:
            self.session_id = str(uuid.UUID(session_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise CaptureTclBridgeError("session_id must be a UUID") from exc
        if not isinstance(startup_nonce, str) or len(startup_nonce) < 24:
            raise CaptureTclBridgeError("startup_nonce must contain at least 24 characters")
        if type(poll_interval_ms) is not int or not 50 <= poll_interval_ms <= 5000:
            raise CaptureTclBridgeError("poll_interval_ms must be between 50 and 5000")

        self.startup_nonce = startup_nonce
        self.session_dir = Path(session_dir).resolve()
        self.autoload_dir = Path(autoload_dir).resolve()
        self.runtime_version = str(runtime_version)
        self.target_file = os.path.normcase(os.path.abspath(target_file)) if target_file else ""
        self.process = process
        self.poll_interval_ms = poll_interval_ms

        self.requests_dir = self.session_dir / "requests"
        self.running_dir = self.session_dir / "running"
        self.results_dir = self.session_dir / "results"
        self.consumed_dir = self.session_dir / "consumed"
        self.ready_path = self.session_dir / "bridge.ready"
        self.bridge_script_path = self.session_dir / "bridge.tcl"
        self.hook_path = self.autoload_dir / f"zz_airbridge_{self.session_id}.tcl"
        self._execute_lock = threading.Lock()

    @classmethod
    def create(
        cls,
        session_root: str,
        autoload_dir: str,
        runtime_version: str = "unknown",
        target_file: str = "",
        session_id: Optional[str] = None,
        startup_nonce: Optional[str] = None,
        poll_interval_ms: int = DEFAULT_POLL_INTERVAL_MS,
    ) -> "CaptureTclBridgeSession":
        session_id = session_id or str(uuid.uuid4())
        startup_nonce = startup_nonce or secrets.token_urlsafe(32)
        session_dir = Path(session_root).resolve() / str(uuid.UUID(session_id))
        autoload_path = Path(autoload_dir).resolve()
        if not autoload_path.is_dir():
            raise CaptureTclBridgeError(f"Capture capAutoLoad directory not found: {autoload_path}")

        bridge = cls(
            session_id=session_id,
            startup_nonce=startup_nonce,
            session_dir=session_dir,
            autoload_dir=autoload_path,
            runtime_version=runtime_version,
            target_file=target_file,
            poll_interval_ms=poll_interval_ms,
        )
        for directory in (
            bridge.session_dir,
            bridge.requests_dir,
            bridge.running_dir,
            bridge.results_dir,
            bridge.consumed_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

        bridge.bridge_script_path.write_text(
            bridge.render_tcl_bridge(),
            encoding="utf-8",
            newline="\n",
        )
        hook_path_posix = str(bridge.hook_path).replace("\\\\", "/")
        bridge_script_posix = str(bridge.bridge_script_path).replace("\\\\", "/")
        hook_content = (
            "# Auto-generated by AirBridge. Capture loads this via capAutoLoad.\\n"
            f"set ::ABBridgeHook_{bridge.session_id.replace('-', '_')} 1\\n"
            f"catch {{file delete -force {_tcl_quote(hook_path_posix)}}}\\n"
            f"source {_tcl_quote(bridge_script_posix)}\\n"
        )
        temporary_hook = bridge.hook_path.with_suffix(".tcl.tmp")
        temporary_hook.write_text(hook_content, encoding="utf-8", newline="\n")
        try:
            os.replace(temporary_hook, bridge.hook_path)
        except OSError as exc:
            try:
                temporary_hook.unlink(missing_ok=True)
            except OSError:
                pass
            raise CaptureTclBridgeError(
                f"cannot install Capture capAutoLoad hook: {exc}"
            ) from exc
        return bridge

    def attach_process(self, process: Any) -> None:
        self.process = process

    def render_tcl_bridge(self) -> str:
        q_session = _tcl_quote(self.session_id)
        q_nonce = _tcl_quote(self.startup_nonce)
        q_root = _tcl_quote(str(self.session_dir).replace("\\", "/"))
        q_ready = _tcl_quote(str(self.ready_path).replace("\\", "/"))
        q_lock = _tcl_quote(str(self.session_dir / "bridge.lock").replace("\\", "/"))
        q_hook = _tcl_quote(str(self.hook_path).replace("\\", "/"))
        poll_ms = self.poll_interval_ms

        return f'''# AirBridge persistent Capture Tcl Bridge; generated per Session.
if {{[info exists ::ABBridge::booted]}} {{ return }}
namespace eval ::ABBridge {{
    variable booted 1
    variable session_id {q_session}
    variable startup_nonce {q_nonce}
    variable root {q_root}
    variable requests_dir [file join $root requests]
    variable running_dir [file join $root running]
    variable results_dir [file join $root results]
    variable ready_path {q_ready}
    variable lock_path {q_lock}
    variable poll_ms {poll_ms}
    variable active 0
    variable stdout ""
    variable stderr ""
    variable poll_scheduled 0
}}

proc ::ABBridge::write_record {{path record}} {{
    set tmp "$path.[pid].[clock clicks].tmp"
    set fp [open $tmp "w"]
    fconfigure $fp -encoding ascii -translation lf
    foreach {{key value}} $record {{
        set encoded [binary encode base64 -maxlen 0 [encoding convertto utf-8 $value]]
        puts $fp "$key=$encoded"
    }}
    close $fp
    file rename -force $tmp $path
}}

proc ::ABBridge::read_record {{path}} {{
    set fp [open $path "r"]
    fconfigure $fp -encoding ascii -translation lf
    set raw [read $fp]
    close $fp
    set record [dict create]
    foreach line [split $raw "\\n"] {{
        set sep [string first "=" $line]
        if {{$sep <= 0}} {{ continue }}
        set key [string range $line 0 [expr {{$sep - 1}}]]
        set encoded [string range $line [expr {{$sep + 1}}] end]
        dict set record $key [encoding convertfrom utf-8 [binary decode base64 $encoded]]
    }}
    return $record
}}

# Preserve Capture's native puts behavior while also collecting request output.
if {{[llength [info commands ::puts]] && ![llength [info commands ::ABBridge::_raw_puts]]}} {{
    rename ::puts ::ABBridge::_raw_puts
}}
if {{[llength [info commands ::ABBridge::_raw_puts]]}} {{
    proc ::puts {{args}} {{
        set no_newline 0
        set channel stdout
        if {{[llength $args] > 0 && [lindex $args 0] eq "-nonewline"}} {{
            set no_newline 1
            set args [lrange $args 1 end]
        }}
        if {{[llength $args] == 1}} {{
            set message [lindex $args 0]
        }} elseif {{[llength $args] == 2}} {{
            set channel [lindex $args 0]
            set message [lindex $args 1]
        }} else {{
            return [uplevel 1 [linsert $args 0 ::ABBridge::_raw_puts]]
        }}
        set rc [uplevel 1 [linsert $args 0 ::ABBridge::_raw_puts]]
        if {{$::ABBridge::active}} {{
            if {{$channel eq "stdout"}} {{
                append ::ABBridge::stdout $message
                if {{!$no_newline}} {{ append ::ABBridge::stdout "\\n" }}
            }} elseif {{$channel eq "stderr"}} {{
                append ::ABBridge::stderr $message
                if {{!$no_newline}} {{ append ::ABBridge::stderr "\\n" }}
            }}
        }}
        return $rc
    }}
}}

proc ::ABBridge::execute_request {{request_path}} {{
    variable session_id
    variable startup_nonce
    variable running_dir
    variable results_dir
    variable active
    variable stdout
    variable stderr

    set running_path [file join $running_dir [file tail $request_path].run]
    if {{[catch {{file rename $request_path $running_path}} claim_error]}} {{
        return
    }}

    set request [dict create]
    set code [catch {{set request [::ABBridge::read_record $running_path]}} parse_error]
    if {{$code != 0}} {{
        catch {{file delete -force $running_path}}
        return
    }}

    set request_id ""
    if {{[dict exists $request request_id]}} {{ set request_id [dict get $request request_id] }}
    set result_path [file join $results_dir "$request_id.result"]
    set status FAILED
    set result ""
    set error_info ""
    set error_code ""
    set stdout ""
    set stderr ""
    set started_ms [clock milliseconds]
    set return_code 1

    if {{[dict exists $request session_id] && [dict get $request session_id] eq $session_id &&
         [dict exists $request startup_nonce] && [dict get $request startup_nonce] eq $startup_nonce &&
         [dict exists $request script_b64]}} {{
        set script_b64 [dict get $request script_b64]
        if {{[catch {{set script [encoding convertfrom utf-8 [binary decode base64 $script_b64]]}} decode_error] == 0}} {{
            set active 1
            set return_code [catch {{uplevel #0 $script}} result options]
            set active 0
            if {{$return_code == 0}} {{
                set status SUCCEEDED
            }} else {{
                set status FAILED
                if {{[dict exists $options -errorinfo]}} {{ set error_info [dict get $options -errorinfo] }}
                if {{[dict exists $options -errorcode]}} {{ set error_code [dict get $options -errorcode] }}
            }}
        }} else {{
            set error_info $decode_error
        }}
    }} else {{
        set error_info "request session_id / startup_nonce mismatch or missing script"
        set error_code "AIRBRIDGE INVALID_REQUEST"
    }}
    set active 0
    set completed_ms [clock milliseconds]
    set payload_sha256 ""
    if {{[dict exists $request payload_sha256]}} {{ set payload_sha256 [dict get $request payload_sha256] }}

    set record [list \\
        schema 1 \\
        session_id $session_id \\
        startup_nonce $startup_nonce \\
        request_id $request_id \\
        payload_sha256 $payload_sha256 \\
        status $status \\
        return_code $return_code \\
        result $result \\
        stdout $stdout \\
        stderr $stderr \\
        error_info $error_info \\
        error_code $error_code \\
        runtime_pid [pid] \\
        tcl_version $::tcl_version \\
        started_at_ms $started_ms \\
        completed_at_ms $completed_ms]
    catch {{::ABBridge::write_record $result_path $record}}
    catch {{file delete -force $running_path}}
}}

proc ::ABBridge::poll {{}} {{
    variable requests_dir
    variable poll_ms
    variable poll_scheduled
    set poll_scheduled 0
    if {{[catch {{
        set candidates [lsort -dictionary [glob -nocomplain -directory $requests_dir *.req]]
        if {{[llength $candidates] > 0}} {{
            ::ABBridge::execute_request [lindex $candidates 0]
        }}
    }} poll_error]}} {{
        if {{[llength [info commands ::ABBridge::_raw_puts]]}} {{
            catch {{::ABBridge::_raw_puts stderr "AirBridge Bridge poll error: $poll_error"}}
        }}
    }}
    if {{!$poll_scheduled}} {{
        set poll_scheduled 1
        after $poll_ms ::ABBridge::poll
    }}
}}

# Claim this session so a second Capture process cannot serve the same queue.
if {{![file exists $::ABBridge::lock_path]}} {{
    if {{![catch {{set lock_fp [open $::ABBridge::lock_path {{WRONLY CREAT EXCL}}]}}]}} {{
        puts $lock_fp "[pid]"
        close $lock_fp
    }}
}}
if {{![file exists $::ABBridge::lock_path]}} {{ return }}
set lock_fp [open $::ABBridge::lock_path "r"]
set lock_pid [string trim [read $lock_fp]]
close $lock_fp
if {{$lock_pid ne "[pid]"}} {{ return }}

catch {{file delete -force {q_hook}}}
::ABBridge::write_record $::ABBridge::ready_path [list \\
    schema 1 \\
    session_id $::ABBridge::session_id \\
    startup_nonce $::ABBridge::startup_nonce \\
    runtime_pid [pid] \\
    tcl_version $::tcl_version \\
    runtime_version {_tcl_quote(self.runtime_version)} \\
    ready_at_ms [clock milliseconds]]
::ABBridge::poll
'''

    def wait_ready(
        self,
        timeout_seconds: float = 60.0,
        expected_pid: Optional[int] = None,
    ) -> Dict[str, str]:
        deadline = time.monotonic() + max(0.1, float(timeout_seconds))
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                raise CaptureTclBridgeError(
                    f"Capture exited before Bridge READY (exit={self.process.returncode})"
                )
            if self.ready_path.is_file():
                record = _decode_record(self.ready_path)
                expected = {
                    "schema": "1",
                    "session_id": self.session_id,
                    "startup_nonce": self.startup_nonce,
                }
                for key, value in expected.items():
                    if record.get(key) != value:
                        raise CaptureTclBridgeError(
                            f"Capture Bridge READY {key} mismatch"
                        )
                try:
                    runtime_pid = int(record.get("runtime_pid", "0"))
                except ValueError as exc:
                    raise CaptureTclBridgeError("Capture Bridge READY has invalid PID") from exc
                if expected_pid is not None and runtime_pid != int(expected_pid):
                    raise CaptureTclBridgeError(
                        f"Capture Bridge READY PID mismatch: expected {expected_pid}, got {runtime_pid}"
                    )
                if self.process is not None and runtime_pid != int(self.process.pid):
                    raise CaptureTclBridgeError(
                        f"Capture Bridge PID {runtime_pid} does not match launched PID {self.process.pid}"
                    )
                if not record.get("tcl_version"):
                    raise CaptureTclBridgeError("Capture Bridge READY omitted tcl_version")
                return record
            time.sleep(0.05)
        raise CaptureTclBridgeError(
            f"Capture Bridge READY timeout after {timeout_seconds:.1f}s; "
            f"session_id={self.session_id}; hook={self.hook_path}"
        )

    def execute(
        self,
        script: str,
        request_id: str,
        timeout_seconds: float = 60.0,
        max_script_bytes: int = MAX_CAPTURE_SCRIPT_BYTES,
    ) -> Dict[str, Any]:
        if not isinstance(script, str):
            raise CaptureTclBridgeError("Capture Tcl script must be text")
        try:
            normalized_id = str(uuid.UUID(request_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise CaptureTclBridgeError("request_id must be a UUID") from exc

        script_bytes = script.encode("utf-8")
        if len(script_bytes) > max_script_bytes:
            raise CaptureTclBridgeError(
                f"Capture Tcl payload exceeds {max_script_bytes} bytes"
            )
        if max_script_bytes <= 0:
            raise CaptureTclBridgeError("max_script_bytes must be positive")

        payload_hash = hashlib.sha256(script_bytes).hexdigest()
        request_path = self.requests_dir / f"{normalized_id}.req"
        result_path = self.results_dir / f"{normalized_id}.result"
        record = {
            "schema": "1",
            "session_id": self.session_id,
            "startup_nonce": self.startup_nonce,
            "request_id": normalized_id,
            "payload_sha256": payload_hash,
            "script_b64": base64.b64encode(script_bytes).decode("ascii"),
        }

        with self._execute_lock:
            if self.process is not None and self.process.poll() is not None:
                return self._unknown_result(
                    normalized_id, payload_hash, "CAPTURE_SESSION_LOST",
                    f"Capture process exited with code {self.process.returncode}",
                )
            if result_path.exists():
                cached = self._read_result(normalized_id, payload_hash)
                if cached is not None:
                    return cached

            temp_path = self.requests_dir / f".{normalized_id}.{uuid.uuid4().hex}.tmp"
            try:
                with open(temp_path, "x", encoding="ascii", newline="\n") as handle:
                    handle.write(_encode_record(record))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, request_path)
            except FileExistsError:
                return self._unknown_result(
                    normalized_id, payload_hash, "CAPTURE_REQUEST_ALREADY_QUEUED",
                    "A request file with this request_id already exists",
                )
            except OSError as exc:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise CaptureTclBridgeError(f"cannot queue Capture Tcl request: {exc}") from exc

            deadline = time.monotonic() + max(0.1, float(timeout_seconds))
            while time.monotonic() < deadline:
                if self.process is not None and self.process.poll() is not None:
                    return self._unknown_result(
                        normalized_id, payload_hash, "CAPTURE_SESSION_LOST",
                        f"Capture process exited with code {self.process.returncode}",
                    )
                if result_path.is_file():
                    result = self._read_result(normalized_id, payload_hash)
                    if result is not None:
                        return result
                time.sleep(0.05)

            # The script may still be running inside Capture. Do not remove the
            # request or claim cancellation; the Broker must persist UNKNOWN.
            return self._unknown_result(
                normalized_id, payload_hash, "CAPTURE_REQUEST_TIMEOUT",
                "Capture did not return a result before the caller timeout; execution may still be running",
            )

    def _read_result(
        self,
        request_id: str,
        expected_hash: str,
    ) -> Optional[Dict[str, Any]]:
        path = self.results_dir / f"{request_id}.result"
        try:
            record = _decode_record(path)
        except CaptureTclBridgeError:
            return None
        expected = {
            "schema": "1",
            "session_id": self.session_id,
            "startup_nonce": self.startup_nonce,
            "request_id": request_id,
            "payload_sha256": expected_hash,
        }
        for key, value in expected.items():
            if record.get(key) != value:
                raise CaptureTclBridgeError(f"Capture result {key} mismatch")

        try:
            runtime_pid = int(record.get("runtime_pid", "0"))
            return_code = int(record.get("return_code", "1"))
            started = int(record.get("started_at_ms", "0"))
            completed = int(record.get("completed_at_ms", "0"))
        except ValueError as exc:
            raise CaptureTclBridgeError("Capture result contains invalid numeric fields") from exc

        status = record.get("status")
        if status not in ("SUCCEEDED", "FAILED"):
            raise CaptureTclBridgeError(f"unsupported Capture result status: {status!r}")
        success = status == "SUCCEEDED" and return_code == 0
        error = None
        if not success:
            error = {
                "code": "CAPTURE_TCL_EXECUTION_ERROR",
                "message": record.get("error_info") or record.get("error_code") or "Capture Tcl execution failed",
                "error_info": record.get("error_info", ""),
                "error_code": record.get("error_code", ""),
            }

        return {
            "success": success,
            "returncode": return_code,
            "output": record.get("result", ""),
            "stdout": record.get("stdout", ""),
            "stderr": record.get("stderr", ""),
            "error": error,
            "capture_pid": runtime_pid,
            "tcl_version": record.get("tcl_version", ""),
            "session_id": self.session_id,
            "request_id": request_id,
            "payload_sha256": expected_hash,
            "duration_ms": max(0, completed - started),
            "request_state": "completed" if success else "failed",
        }

    def _unknown_result(
        self,
        request_id: str,
        payload_hash: str,
        code: str,
        message: str,
    ) -> Dict[str, Any]:
        return {
            "success": False,
            "request_state": "unknown",
            "returncode": -2,
            "output": "",
            "capture_pid": getattr(self.process, "pid", None),
            "session_id": self.session_id,
            "request_id": request_id,
            "payload_sha256": payload_hash,
            "error": {"code": code, "message": message, "retryable": False},
        }

    def prune_results(
        self,
        retention_seconds: int = DEFAULT_RESULT_RETENTION_SECONDS,
        max_records: int = 1000,
    ) -> int:
        """Bound local result retention; never prune queued/running request files."""
        if retention_seconds < 0 or max_records < 1:
            raise CaptureTclBridgeError("invalid bridge result retention policy")
        now = time.time()
        files = sorted(
            self.results_dir.glob("*.result"),
            key=lambda item: item.stat().st_mtime,
        )
        removed = 0
        for path in files:
            expired = now - path.stat().st_mtime > retention_seconds
            over_capacity = len(files) - removed > max_records
            if expired or over_capacity:
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed


class CaptureTclAdapter(RuntimeAdapter):
    """Runtime Adapter that submits Tcl to an already managed Bridge session."""

    adapter_id = "capture-tcl-session"
    adapter_version = "0.1"
    operations = ("execute",)
    capabilities = (
        "script.execute",
        "capture.tcl.session",
        "persistent-interpreter",
        "capautoload.ready",
    )

    def __init__(
        self,
        session_provider: Callable[
            [Mapping[str, Any], RuntimeExecutionContext], CaptureTclBridgeSession
        ],
    ):
        if not callable(session_provider):
            raise CaptureTclBridgeError("session_provider must be callable")
        self._session_provider = session_provider

    def execute(
        self,
        request: RuntimeExecutionRequest,
        context: RuntimeExecutionContext,
    ) -> Dict[str, Any]:
        if request.operation != "execute":
            raise CaptureTclBridgeError(f"unsupported Capture operation: {request.operation}")
        payload = request.payload
        script = payload.get("script") or payload.get("script_content") or ""
        request_id = request.request_id or str(uuid.uuid4())
        timeout = payload.get("timeout", 60)
        try:
            timeout_seconds = float(timeout)
        except (TypeError, ValueError) as exc:
            raise CaptureTclBridgeError("Capture timeout must be numeric") from exc
        if timeout_seconds < 0:
            raise CaptureTclBridgeError("Capture timeout must be >= 0")

        bridge = self._session_provider(payload, context)
        target_file = payload.get("target_file", "") or ""
        if target_file:
            extension = Path(target_file).suffix.lower()
            if extension == ".opj":
                script = f"Open {_tcl_quote(target_file)}\n" + script
            elif extension == ".dsn":
                expected = os.path.normcase(os.path.abspath(target_file))
                if bridge.target_file != expected:
                    raise CaptureTclBridgeError(
                        "the requested DSN is not the design opened by this Capture session"
                    )
            else:
                raise CaptureTclBridgeError(
                    "Capture target_file must be a .dsn or .opj file"
                )

        context.log(
            f"[Capture Adapter] submit request={request_id} session={bridge.session_id}"
        )
        result = bridge.execute(
            script=script,
            request_id=request_id,
            timeout_seconds=timeout_seconds,
        )
        context.log(
            f"[Capture Adapter] request={request_id} state={result.get('request_state')} "
            f"pid={result.get('capture_pid')}"
        )
        return result
