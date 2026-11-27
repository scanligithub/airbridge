# -*- coding: utf-8 -*-
"""Host-A Provider using the credential-managed AirBridge MCP STDIO Gateway."""
from __future__ import annotations

import math
import os
import re
from typing import Any, Mapping, Optional, Sequence

from .contract import (
    CONTRACT_VERSION,
    Capability,
    OperationResponse,
    ProviderClosedError,
    ProviderInfo,
    normalize_request_id,
)
from .stdio_client import McpStdioClient, McpStdioError


AGENT_ID_ENV = "AIRBRIDGE_AGENT_ID"
EXE_PATH_ENV = "AIRBRIDGE_EXE_PATH"
_AGENT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")


class AirBridgeProviderError(RuntimeError):
    """AirBridge Provider configuration, authorization, or protocol error."""


def _child_environment(
    source: Mapping[str, str],
    agent_id: str,
) -> dict[str, str]:
    """Pass only OS runtime variables and non-secret AirBridge settings."""
    allowed_os = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "LOCALAPPDATA",
        "APPDATA",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "PROGRAMDATA",
        "HOME",
        "TMPDIR",
    }
    result = {
        key: value
        for key, value in source.items()
        if key.upper() in allowed_os
    }
    for key in ("AIRBRIDGE_BASE_URL", "AIRBRIDGE_HTTP_TIMEOUT_SECONDS"):
        value = source.get(key)
        if value:
            result[key] = value
    # The STDIO executable itself sets the internal M2.5 policy marker.
    # No Agent Bearer token is read, copied, or passed to this child.
    result[AGENT_ID_ENV] = agent_id
    return result


class AirBridgeProvider:
    """Map the generic Transport Contract to the public AirBridge MCP STDIO tools.

    The Provider starts its own short-lived MCP client process. The executable
    loads the Agent credential internally from Windows Credential Manager;
    this Python process never receives the credential.
    """

    def __init__(
        self,
        executable_path: Optional[str] = None,
        agent_id: Optional[str] = None,
        timeout_seconds: float = 75.0,
        command: Optional[Sequence[str]] = None,
        environ: Optional[Mapping[str, str]] = None,
        popen_factory=None,
    ) -> None:
        source = os.environ if environ is None else environ
        selected_agent = str(agent_id if agent_id is not None else source.get(AGENT_ID_ENV, "")).strip().lower()
        if not _AGENT_ID_PATTERN.fullmatch(selected_agent):
            raise AirBridgeProviderError("A valid AIRBRIDGE_AGENT_ID is required")
        try:
            self.timeout_seconds = float(timeout_seconds)
        except (TypeError, ValueError) as exc:
            raise AirBridgeProviderError("timeout_seconds must be positive") from exc
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise AirBridgeProviderError("timeout_seconds must be positive")

        if command is None:
            selected_exe = str(
                executable_path
                if executable_path is not None
                else source.get(EXE_PATH_ENV, "")
            ).strip()
            if not selected_exe:
                raise AirBridgeProviderError(
                    "AIRBRIDGE_EXE_PATH or executable_path is required"
                )
            child_command = [selected_exe, "mcp", "stdio"]
        else:
            child_command = list(command)
            if not child_command:
                raise AirBridgeProviderError("MCP stdio command must not be empty")

        self.agent_id = selected_agent
        child_env = _child_environment(source, selected_agent)
        try:
            self._client = McpStdioClient(
                command=child_command,
                environ=child_env,
                timeout_seconds=self.timeout_seconds,
                popen_factory=popen_factory,
            )
        except McpStdioError as exc:
            raise AirBridgeProviderError(str(exc)) from exc
        self._closed = False

    def _ensure_open(self) -> None:
        if self._closed:
            raise ProviderClosedError("Transport Provider is closed")

    def get_info(self) -> ProviderInfo:
        self._ensure_open()
        return ProviderInfo("airbridge", "0.2.0", CONTRACT_VERSION)

    def get_capabilities(self) -> list[Capability]:
        self._ensure_open()
        return [
            Capability("shell.execute", "airbridge_shell_execute" in self._client.tool_names),
            Capability("request.read", "airbridge_request_status" in self._client.tool_names),
        ]

    @staticmethod
    def _error_text(value: Any, fallback: str) -> str:
        if isinstance(value, Mapping):
            code = value.get("code")
            message = value.get("message")
            if code and message:
                return "{}: {}".format(code, message)
            if message:
                return str(message)
            if code:
                return str(code)
            return fallback
        return str(value) if value else fallback

    @staticmethod
    def _convert(payload: Mapping[str, Any], request_id: str) -> OperationResponse:
        request_id = normalize_request_id(request_id)
        response_id = payload.get("request_id")
        if response_id is not None and normalize_request_id(str(response_id)) != request_id:
            raise AirBridgeProviderError("AirBridge response request_id did not match")
        state = str(payload.get("state") or "").lower()
        if state in ("pending", "accepted"):
            return OperationResponse.accepted(request_id)
        if state in ("unknown", "not_found", "unavailable", "unconfirmed"):
            return OperationResponse.unknown(
                request_id,
                AirBridgeProvider._error_text(
                    payload.get("error"), "AirBridge outcome is unknown"
                ),
            )
        if state not in ("completed", "failed"):
            detail = AirBridgeProvider._error_text(
                payload.get("error"), "AirBridge rejected the operation"
            )
            raise AirBridgeProviderError(
                "AirBridge operation was not submitted or was denied: {}".format(detail)
            )
        result = payload.get("result")
        result = result if isinstance(result, dict) else {}
        exit_code = result.get("exit_code")
        success = (
            False
            if state == "failed"
            else (
                exit_code == 0 and not result.get("killed", False)
                if exit_code is not None
                else bool(result.get("success", True))
            )
        )
        error = None if success else AirBridgeProvider._error_text(
            result.get("stderr") or result.get("error") or payload.get("error"),
            "remote Shell command failed",
        )
        return OperationResponse.completed(request_id, success, result, error)

    def shell_execute(
        self, command: str, options: Optional[Mapping[str, Any]], request_id: str
    ) -> OperationResponse:
        self._ensure_open()
        if not isinstance(command, str) or not command.strip():
            raise ValueError("command must be a non-empty string")
        request_id = normalize_request_id(request_id)
        if options is not None and not isinstance(options, Mapping):
            raise TypeError("options must be a mapping or None")
        opts = dict(options or {})
        unknown = set(opts) - {
            "shell", "cwd", "timeout", "http_timeout_seconds", "wait_timeout_ms"
        }
        if unknown:
            raise ValueError("unsupported Shell option: {}".format(sorted(unknown)[0]))
        shell = opts.get("shell", "powershell")
        task_timeout = opts.get("timeout", 60)
        if shell not in ("powershell", "cmd"):
            raise ValueError("shell must be 'powershell' or 'cmd'")
        if (
            isinstance(task_timeout, bool)
            or not isinstance(task_timeout, int)
            or not 1 <= task_timeout <= 300
        ):
            raise ValueError("timeout must be an integer between 1 and 300")
        cwd = opts.get("cwd", "")
        if not isinstance(cwd, str):
            raise TypeError("cwd must be a string")
        wait_timeout_ms = opts.get("wait_timeout_ms", 60_000)
        if (
            isinstance(wait_timeout_ms, bool)
            or not isinstance(wait_timeout_ms, int)
            or not 0 <= wait_timeout_ms <= 300_000
        ):
            raise ValueError("wait_timeout_ms must be an integer between 0 and 300000")
        call_timeout = opts.get("http_timeout_seconds", self.timeout_seconds)
        try:
            call_timeout = float(call_timeout)
        except (TypeError, ValueError) as exc:
            raise ValueError("http_timeout_seconds must be positive") from exc
        if not math.isfinite(call_timeout) or call_timeout <= 0:
            raise ValueError("http_timeout_seconds must be positive")

        arguments = {
            "request_id": request_id,
            "command": command,
            "shell": shell,
            "cwd": cwd,
            "timeout": task_timeout,
            "wait_timeout_ms": wait_timeout_ms,
        }
        try:
            payload = self._client.call_tool(
                "airbridge_shell_execute",
                arguments,
                timeout_seconds=call_timeout,
            )
        except McpStdioError:
            return OperationResponse.unknown(
                request_id,
                "AirBridge MCP STDIO response was not confirmed; query the same request_id",
            )
        return self._convert(payload, request_id)

    def get_operation(self, request_id: str) -> OperationResponse:
        """Read the original request record; this method never resubmits work."""
        self._ensure_open()
        request_id = normalize_request_id(request_id)
        try:
            payload = self._client.call_tool(
                "airbridge_request_status",
                {"request_id": request_id},
                timeout_seconds=self.timeout_seconds,
            )
        except McpStdioError:
            return OperationResponse.unknown(
                request_id,
                "AirBridge MCP status response was not confirmed",
            )
        return self._convert(payload, request_id)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._client.close()
