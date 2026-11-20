"""Windows Credential Manager adapter for non-exportable-at-rest Agent secrets.

The credential blob is protected by the current Windows user's credential vault.
This is not an OS-level boundary against malicious code running as that same user.
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import re
from ctypes import wintypes
from typing import Protocol


class CredentialStoreError(RuntimeError):
    """Credential storage is unavailable or an operation failed."""


class CredentialNotFound(CredentialStoreError):
    """No credential is stored for the requested Agent ID."""


class CredentialStore(Protocol):
    def store(self, agent_id: str, token: str) -> None: ...
    def load(self, agent_id: str) -> str: ...
    def delete(self, agent_id: str) -> bool: ...


def _validate_agent_id(agent_id: str) -> str:
    value = (agent_id or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,63}", value):
        raise CredentialStoreError("invalid Agent ID")
    return value


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class _CREDENTIAL_ATTRIBUTEW(ctypes.Structure):
    _fields_ = [
        ("Keyword", wintypes.LPWSTR),
        ("Flags", wintypes.DWORD),
        ("ValueSize", wintypes.DWORD),
        ("Value", ctypes.POINTER(ctypes.c_ubyte)),
    ]


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", _FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.POINTER(_CREDENTIAL_ATTRIBUTEW)),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


class WindowsCredentialStore:
    """Store one Agent token per current Windows user using Cred*W APIs."""

    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_LOCAL_MACHINE = 2
    MAX_BLOB_BYTES = 5 * 512
    ERROR_NOT_FOUND = 1168
    TARGET_PREFIX = "AirBridge/Agent/"

    def __init__(self) -> None:
        if os.name != "nt":
            raise CredentialStoreError(
                "Windows Credential Manager is available only on Windows"
            )
        try:
            self._advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
            self._kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
        except (AttributeError, OSError) as exc:
            raise CredentialStoreError(
                "Windows Credential Manager APIs are unavailable"
            ) from exc
        self._configure_api()

    def _configure_api(self) -> None:
        self._advapi32.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
        self._advapi32.CredWriteW.restype = wintypes.BOOL
        self._advapi32.CredReadW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(_CREDENTIALW)),
        ]
        self._advapi32.CredReadW.restype = wintypes.BOOL
        self._advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self._advapi32.CredDeleteW.restype = wintypes.BOOL
        self._advapi32.CredFree.argtypes = [ctypes.c_void_p]
        self._advapi32.CredFree.restype = None

    @classmethod
    def _target(cls, agent_id: str) -> str:
        return cls.TARGET_PREFIX + _validate_agent_id(agent_id)

    def store(self, agent_id: str, token: str) -> None:
        target = self._target(agent_id)
        if not isinstance(token, str) or not token or "\x00" in token:
            raise CredentialStoreError("Agent credential is empty or invalid")
        blob = token.encode("utf-8")
        if len(blob) > self.MAX_BLOB_BYTES:
            raise CredentialStoreError("Agent credential exceeds Windows vault limit")
        blob_buffer = ctypes.create_string_buffer(blob)
        credential = _CREDENTIALW()
        credential.Flags = 0
        credential.Type = self.CRED_TYPE_GENERIC
        credential.TargetName = target
        credential.Comment = "AirBridge managed Agent credential"
        credential.CredentialBlobSize = len(blob)
        credential.CredentialBlob = ctypes.cast(
            blob_buffer, ctypes.POINTER(ctypes.c_ubyte)
        )
        credential.Persist = self.CRED_PERSIST_LOCAL_MACHINE
        credential.AttributeCount = 0
        credential.Attributes = None
        credential.TargetAlias = None
        credential.UserName = _validate_agent_id(agent_id)
        if not self._advapi32.CredWriteW(ctypes.byref(credential), 0):
            code = ctypes.get_last_error()
            raise CredentialStoreError(
                f"Windows Credential Manager write failed (error {code})"
            )

    def load(self, agent_id: str) -> str:
        target = self._target(agent_id)
        pointer = ctypes.POINTER(_CREDENTIALW)()
        if not self._advapi32.CredReadW(
            target, self.CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)
        ):
            code = ctypes.get_last_error()
            if code == self.ERROR_NOT_FOUND:
                raise CredentialNotFound(
                    "Agent credential is not present in Windows Credential Manager"
                )
            raise CredentialStoreError(
                f"Windows Credential Manager read failed (error {code})"
            )
        try:
            credential = pointer.contents
            size = int(credential.CredentialBlobSize)
            if size <= 0 or size > self.MAX_BLOB_BYTES:
                raise CredentialStoreError("stored Agent credential has an invalid size")
            raw = ctypes.string_at(credential.CredentialBlob, size)
            try:
                token = raw.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise CredentialStoreError(
                    "stored Agent credential is not valid UTF-8"
                ) from exc
            if not token or "\x00" in token:
                raise CredentialStoreError("stored Agent credential is invalid")
            return token
        finally:
            self._advapi32.CredFree(pointer)

    def delete(self, agent_id: str) -> bool:
        target = self._target(agent_id)
        if self._advapi32.CredDeleteW(target, self.CRED_TYPE_GENERIC, 0):
            return True
        code = ctypes.get_last_error()
        if code == self.ERROR_NOT_FOUND:
            return False
        raise CredentialStoreError(
            f"Windows Credential Manager delete failed (error {code})"
        )


class WindowsAgentCredentialLock:
    """Cross-process per-Agent mutex for coordinated credential lifecycle changes."""

    WAIT_OBJECT_0 = 0x00000000
    WAIT_ABANDONED = 0x00000080
    WAIT_FAILED = 0xFFFFFFFF
    INFINITE = 0xFFFFFFFF

    def __init__(self, agent_id: str) -> None:
        if os.name != "nt":
            raise CredentialStoreError(
                "Windows Agent credential locking is available only on Windows"
            )
        normalized = _validate_agent_id(agent_id)
        digest = hashlib.sha256(normalized.encode("ascii")).hexdigest()
        # Keep the Agent ID out of the global object name; Local\ scopes the
        # mutex to this interactive Windows session.
        self.name = "Local\\AirBridge.AgentCredential." + digest
        self._kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
        self._kernel32.CreateMutexW.argtypes = [
            ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR
        ]
        self._kernel32.CreateMutexW.restype = wintypes.HANDLE
        self._kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self._kernel32.WaitForSingleObject.restype = wintypes.DWORD
        self._kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
        self._kernel32.ReleaseMutex.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL
        self._handle = None

    def __enter__(self):
        handle = self._kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            code = ctypes.get_last_error()
            raise CredentialStoreError(
                f"Windows Agent credential mutex creation failed (error {code})"
            )
        self._handle = handle
        result = int(self._kernel32.WaitForSingleObject(handle, self.INFINITE))
        if result not in (self.WAIT_OBJECT_0, self.WAIT_ABANDONED):
            code = ctypes.get_last_error()
            self._kernel32.CloseHandle(handle)
            self._handle = None
            raise CredentialStoreError(
                f"Windows Agent credential mutex wait failed "
                f"(result {result}, error {code})"
            )
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        handle = self._handle
        self._handle = None
        if handle:
            try:
                if not self._kernel32.ReleaseMutex(handle):
                    code = ctypes.get_last_error()
                    raise CredentialStoreError(
                        f"Windows Agent credential mutex release failed (error {code})"
                    )
            finally:
                self._kernel32.CloseHandle(handle)
        return False


def default_credential_store() -> CredentialStore:
    """Return the production credential backend; never downgrade on unsupported OS."""
    return WindowsCredentialStore()
