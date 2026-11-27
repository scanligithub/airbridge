# -*- coding: utf-8 -*-
"""Minimal MCP stdio client for the public AirBridge Gateway tool contract."""
from __future__ import annotations

import json
import queue
import subprocess
import threading
from typing import Any, Mapping, Sequence


class McpStdioError(RuntimeError):
    """MCP stdio process or protocol failure."""


class McpStdioClient:
    """Own one AirBridge.exe mcp stdio child without handling Agent secrets."""

    PROTOCOL_VERSION = "2025-03-26"

    def __init__(
        self,
        command: Sequence[str],
        environ: Mapping[str, str],
        timeout_seconds: float = 75.0,
        popen_factory=None,
    ) -> None:
        if not command or not all(isinstance(part, str) and part for part in command):
            raise ValueError("MCP stdio command must be a non-empty string sequence")
        self.timeout_seconds = float(timeout_seconds)
        self._next_id = 0
        self._closed = False
        self._request_lock = threading.Lock()
        self._lines: queue.Queue[bytes | None] = queue.Queue()
        factory = popen_factory or subprocess.Popen
        try:
            self._process = factory(
                list(command),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
                env=dict(environ),
            )
        except OSError as exc:
            raise McpStdioError("Unable to start the AirBridge MCP STDIO process") from exc
        if self._process.stdin is None or self._process.stdout is None:
            self.close()
            raise McpStdioError("AirBridge MCP STDIO pipes are unavailable")
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()
        try:
            self._initialize()
        except Exception:
            self.close()
            raise

    def _read_stdout(self) -> None:
        try:
            stream = self._process.stdout
            for line in iter(stream.readline, b""):
                self._lines.put(line)
        except Exception:
            pass
        finally:
            self._lines.put(None)

    def _send(self, message: Mapping[str, Any]) -> None:
        if self._closed or self._process.poll() is not None:
            raise McpStdioError("AirBridge MCP STDIO process is not running")
        raw = json.dumps(
            dict(message), ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8") + b"\n"
        try:
            self._process.stdin.write(raw)
            self._process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            raise McpStdioError("AirBridge MCP STDIO input pipe failed") from exc

    def _read_message(self, timeout: float | None = None) -> dict[str, Any]:
        try:
            line = self._lines.get(
                timeout=self.timeout_seconds if timeout is None else timeout
            )
        except queue.Empty as exc:
            raise McpStdioError("Timed out waiting for AirBridge MCP STDIO response") from exc
        if line is None:
            raise McpStdioError("AirBridge MCP STDIO process closed its output")
        try:
            message = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise McpStdioError("AirBridge MCP STDIO returned an invalid protocol frame") from exc
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise McpStdioError("AirBridge MCP STDIO returned an invalid JSON-RPC message")
        return message

    def _request(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        # STDIO is one ordered byte stream. Serialize each request/response
        # exchange so concurrent callers cannot consume and discard each
        # other's JSON-RPC responses.
        with self._request_lock:
            self._next_id += 1
            request_id = self._next_id
            message: dict[str, Any] = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
            }
            if params is not None:
                message["params"] = dict(params)
            self._send(message)
            while True:
                response = self._read_message(timeout=timeout_seconds)
                if response.get("id") != request_id:
                    # Notifications and unrelated protocol messages have no bearing
                    # on this request; keep waiting for the matching response.
                    continue
                if "error" in response:
                    raise McpStdioError("AirBridge MCP request failed: " + method)
                result = response.get("result")
                if not isinstance(result, dict):
                    raise McpStdioError("AirBridge MCP response has no result object")
                return result

    def _notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = dict(params)
        self._send(message)

    def _initialize(self) -> None:
        result = self._request(
            "initialize",
            {
                "protocolVersion": self.PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "airbridge-transport-provider",
                    "version": "1.0.0",
                },
            },
        )
        if not result.get("protocolVersion"):
            raise McpStdioError("AirBridge MCP initialize response omitted protocolVersion")
        self._notify("notifications/initialized")
        tools_result = self._request("tools/list")
        tools = tools_result.get("tools")
        if not isinstance(tools, list):
            raise McpStdioError("AirBridge MCP tools/list response is invalid")
        self.tool_names = frozenset(
            item.get("name")
            for item in tools
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        )
        required = {"airbridge_shell_execute", "airbridge_request_status"}
        if not required.issubset(self.tool_names):
            raise McpStdioError("AirBridge MCP Gateway lacks the generic Shell tool contract")

    @staticmethod
    def _decode_tool_result(result: Mapping[str, Any]) -> dict[str, Any]:
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            return dict(structured)
        content = result.get("content")
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict) or item.get("type") != "text":
                    continue
                value = item.get("text")
                if not isinstance(value, str):
                    continue
                try:
                    decoded = json.loads(value)
                except json.JSONDecodeError:
                    continue
                if isinstance(decoded, dict):
                    return decoded
        raise McpStdioError("AirBridge MCP tool returned no structured JSON result")

    def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any],
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        if self._closed:
            raise McpStdioError("AirBridge MCP STDIO client is closed")
        if name not in self.tool_names:
            raise McpStdioError("AirBridge MCP Gateway does not expose the requested tool")
        request_timeout = (
            self.timeout_seconds
            if timeout_seconds is None
            else float(timeout_seconds)
        )
        if request_timeout <= 0:
            raise ValueError("timeout_seconds must be positive")
        result = self._request(
            "tools/call",
            {"name": name, "arguments": dict(arguments)},
            timeout_seconds=request_timeout,
        )
        if result.get("isError") is True:
            decoded = self._decode_tool_result(result)
            raise McpStdioError(
                str(decoded.get("error") or decoded.get("message") or "AirBridge MCP tool failed")
            )
        return self._decode_tool_result(result)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        process = getattr(self, "_process", None)
        if process is None:
            return
        try:
            if process.poll() is None and process.stdin is not None:
                try:
                    message = {"jsonrpc": "2.0", "method": "notifications/exit"}
                    process.stdin.write(
                        json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"
                    )
                    process.stdin.flush()
                except Exception:
                    pass
                try:
                    process.stdin.close()
                except Exception:
                    pass
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
        finally:
            for stream_name in ("stdin", "stdout"):
                stream = getattr(process, stream_name, None)
                if stream is not None and not stream.closed:
                    try:
                        stream.close()
                    except Exception:
                        pass
