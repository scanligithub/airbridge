# runtime_adapter.py
# -*- coding: utf-8 -*-
"""Generic runtime adapter and profile registry.

This module defines the boundary between AirBridge's transport/broker code
and concrete execution engines. It contains no Capture, Tcl, Allegro, SKILL,
Windows process-lifecycle, or GUI automation logic.
"""
import threading
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Dict, Mapping, Optional, Tuple


class RuntimeAdapterError(ValueError):
    """Raised for invalid adapter, profile, or execution requests."""


@dataclass(frozen=True)
class RuntimeProfile:
    """Declarative description of one selectable runtime configuration."""

    profile_id: str
    adapter_id: str
    display_name: str
    operations: Tuple[str, ...]
    capabilities: Tuple[str, ...]
    execution_mode: str = "managed"
    enabled: bool = True

    def __post_init__(self):
        for field_name in ("profile_id", "adapter_id", "display_name", "execution_mode"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise RuntimeAdapterError(f"{field_name} must be a non-empty string")

        for field_name in ("operations", "capabilities"):
            values = getattr(self, field_name)
            if not isinstance(values, (tuple, list)):
                raise RuntimeAdapterError(f"{field_name} must be a sequence")
            if any(not isinstance(item, str) or not item.strip() for item in values):
                raise RuntimeAdapterError(f"{field_name} must contain non-empty strings")
            if len(values) != len(set(values)):
                raise RuntimeAdapterError(f"{field_name} must not contain duplicates")
            object.__setattr__(self, field_name, tuple(values))

        if not self.operations:
            raise RuntimeAdapterError("profile must declare at least one operation")
        if type(self.enabled) is not bool:
            raise RuntimeAdapterError("enabled must be a boolean")

    def to_public_dict(self) -> Dict[str, Any]:
        """Return safe metadata without exposing local runtime configuration."""
        return {
            "profile_id": self.profile_id,
            "adapter_id": self.adapter_id,
            "display_name": self.display_name,
            "operations": list(self.operations),
            "capabilities": list(self.capabilities),
            "execution_mode": self.execution_mode,
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class RuntimeExecutionRequest:
    """One generic operation submitted to a Runtime Adapter."""

    operation: str
    payload: Mapping[str, Any]
    request_id: Optional[str] = None

    def __post_init__(self):
        if not isinstance(self.operation, str) or not self.operation.strip():
            raise RuntimeAdapterError("operation must be a non-empty string")
        if not isinstance(self.payload, Mapping):
            raise RuntimeAdapterError("payload must be a mapping")
        # A read-only top-level snapshot prevents callers from mutating the
        # submitted mapping after request construction.
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))
        if self.request_id is not None:
            if not isinstance(self.request_id, str):
                raise RuntimeAdapterError("request_id must be a UUID string")
            try:
                object.__setattr__(self, "request_id", str(uuid.UUID(self.request_id)))
            except (ValueError, TypeError, AttributeError) as exc:
                raise RuntimeAdapterError("request_id must be a valid UUID") from exc


@dataclass(frozen=True)
class RuntimeExecutionContext:
    """Per-call services supplied without exposing Worker or GUI internals."""

    log_callback: Optional[Callable[[str], None]] = None

    def log(self, message: str) -> None:
        if self.log_callback is not None:
            self.log_callback(str(message))


class RuntimeAdapter(ABC):
    """Minimal adapter contract used by the current one-shot execution path."""

    adapter_id = ""
    adapter_version = "1.0"
    operations: Tuple[str, ...] = ()
    capabilities: Tuple[str, ...] = ()

    @abstractmethod
    def execute(
        self,
        request: RuntimeExecutionRequest,
        context: RuntimeExecutionContext,
    ) -> Dict[str, Any]:
        """Execute one operation and return a JSON-serializable result."""


class RuntimeAdapterRegistry:
    """Thread-safe adapter/profile registry with explicit profile resolution."""

    def __init__(self):
        self._lock = threading.RLock()
        self._adapters: Dict[str, RuntimeAdapter] = {}
        self._profiles: Dict[str, RuntimeProfile] = {}

    def register_adapter(self, adapter: RuntimeAdapter) -> None:
        if not isinstance(adapter, RuntimeAdapter):
            raise RuntimeAdapterError("adapter must implement RuntimeAdapter")

        adapter_id = getattr(adapter, "adapter_id", "")
        if not isinstance(adapter_id, str) or not adapter_id.strip():
            raise RuntimeAdapterError("adapter_id must be a non-empty string")

        operations = getattr(adapter, "operations", ())
        capabilities = getattr(adapter, "capabilities", ())
        for field_name, values in (
            ("operations", operations),
            ("capabilities", capabilities),
        ):
            if not isinstance(values, (tuple, list)):
                raise RuntimeAdapterError(f"adapter {field_name} must be a sequence")
            if any(not isinstance(item, str) or not item.strip() for item in values):
                raise RuntimeAdapterError(
                    f"adapter {field_name} must contain non-empty strings"
                )
            if len(values) != len(set(values)):
                raise RuntimeAdapterError(
                    f"adapter {field_name} must not contain duplicates"
                )
        if not operations:
            raise RuntimeAdapterError("adapter must declare supported operations")

        with self._lock:
            if adapter_id in self._adapters:
                raise RuntimeAdapterError(f"adapter already registered: {adapter_id}")
            self._adapters[adapter_id] = adapter

    def register_profile(self, profile: RuntimeProfile) -> None:
        if not isinstance(profile, RuntimeProfile):
            raise RuntimeAdapterError("profile must be a RuntimeProfile")

        with self._lock:
            if profile.profile_id in self._profiles:
                raise RuntimeAdapterError(
                    f"profile already registered: {profile.profile_id}"
                )
            adapter = self._adapters.get(profile.adapter_id)
            if adapter is None:
                raise RuntimeAdapterError(
                    f"profile references an unknown adapter: {profile.adapter_id}"
                )

            unsupported = set(profile.operations) - set(adapter.operations)
            if unsupported:
                raise RuntimeAdapterError(
                    f"profile operations are not supported by adapter "
                    f"{profile.adapter_id}: {sorted(unsupported)}"
                )

            unsupported_capabilities = (
                set(profile.capabilities) - set(adapter.capabilities)
            )
            if unsupported_capabilities:
                raise RuntimeAdapterError(
                    f"profile capabilities are not supported by adapter "
                    f"{profile.adapter_id}: {sorted(unsupported_capabilities)}"
                )
            self._profiles[profile.profile_id] = profile

    def get_profile(self, profile_id: str) -> RuntimeProfile:
        with self._lock:
            profile = self._profiles.get(profile_id)
        if profile is None:
            raise RuntimeAdapterError(f"unknown runtime profile: {profile_id}")
        return profile

    def list_profiles(self, enabled_only: bool = False) -> Tuple[RuntimeProfile, ...]:
        with self._lock:
            profiles = tuple(
                self._profiles[key] for key in sorted(self._profiles)
            )
        if enabled_only:
            profiles = tuple(profile for profile in profiles if profile.enabled)
        return profiles

    def describe_profiles(self, enabled_only: bool = True) -> list:
        return [
            profile.to_public_dict()
            for profile in self.list_profiles(enabled_only=enabled_only)
        ]

    def execute(
        self,
        profile_id: str,
        request: RuntimeExecutionRequest,
        context: Optional[RuntimeExecutionContext] = None,
    ) -> Dict[str, Any]:
        if not isinstance(request, RuntimeExecutionRequest):
            raise RuntimeAdapterError("request must be a RuntimeExecutionRequest")

        profile = self.get_profile(profile_id)
        if not profile.enabled:
            raise RuntimeAdapterError(f"runtime profile is disabled: {profile_id}")
        if request.operation not in profile.operations:
            raise RuntimeAdapterError(
                f"operation {request.operation!r} is not allowed by profile {profile_id}"
            )

        with self._lock:
            adapter = self._adapters.get(profile.adapter_id)
        if adapter is None:
            raise RuntimeAdapterError(f"adapter is not registered: {profile.adapter_id}")

        result = adapter.execute(request, context or RuntimeExecutionContext())
        if not isinstance(result, dict):
            raise RuntimeAdapterError("adapter execute() must return a dictionary")
        return dict(result)


class LegacyCadenceOneShotAdapter(RuntimeAdapter):
    """Compatibility adapter around the pre-Session EDAExecutor."""

    adapter_id = "legacy-cadence-eda"
    operations = ("execute",)
    capabilities = ("script.execute", "one-shot-process")

    def __init__(self, engine_provider: Callable[[], Any]):
        self._engine_provider = engine_provider

    def execute(
        self,
        request: RuntimeExecutionRequest,
        context: RuntimeExecutionContext,
    ) -> Dict[str, Any]:
        engine = self._engine_provider()
        if engine is None or not callable(getattr(engine, "execute_script", None)):
            raise RuntimeAdapterError("legacy EDA execution engine is unavailable")

        payload = request.payload
        script = payload.get("script") or payload.get("script_content", "")
        hint = payload.get("hint") or payload.get("script_name", "agent_auto.tcl")
        try:
            timeout = int(payload.get("timeout", 60))
        except (TypeError, ValueError) as exc:
            raise RuntimeAdapterError("EDA timeout must be an integer") from exc

        returncode, output = engine.execute_script(
            script,
            hint,
            context.log,
            timeout=timeout,
            version=payload.get("version", "auto"),
            target_file=payload.get("target_file", ""),
            keep_open=bool(payload.get("keep_open", False)),
        )
        return {
            "success": returncode == 0,
            "returncode": returncode,
            "output": output if isinstance(output, str) else str(output),
        }


class AllegroSkillAdapter(RuntimeAdapter):
    """Explicit Allegro SKILL adapter retaining the proven legacy execution path."""

    adapter_id = "allegro-skill-legacy"
    operations = ("execute",)
    capabilities = ("skill.execute", "one-shot-process", "gui.keep-open", "board.open")

    def __init__(self, engine_provider: Callable[[], Any]):
        self._engine_provider = engine_provider

    def execute(
        self,
        request: RuntimeExecutionRequest,
        context: RuntimeExecutionContext,
    ) -> Dict[str, Any]:
        payload = request.payload
        script = payload.get("script") or payload.get("script_content", "")
        hint = payload.get("hint") or payload.get("script_name", "agent_auto.il")
        if not isinstance(script, str) or not script.strip():
            raise RuntimeAdapterError("Allegro SKILL script must be a non-empty string")
        if not isinstance(hint, str) or not hint.strip():
            raise RuntimeAdapterError("Allegro SKILL filename hint must be a non-empty string")

        # Lazy import keeps generic adapter imports independent from EDA modules.
        from eda_engine import EDALanguage, detect_script_type
        if detect_script_type(script, hint) != EDALanguage.ALLEGRO_SKILL:
            raise RuntimeAdapterError(
                "allegro-skill-oneshot accepts Allegro SKILL scripts only"
            )

        engine = self._engine_provider()
        if engine is None or not callable(getattr(engine, "execute_script", None)):
            raise RuntimeAdapterError("Allegro execution engine is unavailable")
        try:
            timeout = int(payload.get("timeout", 60))
        except (TypeError, ValueError) as exc:
            raise RuntimeAdapterError("Allegro timeout must be an integer") from exc

        returncode, output = engine.execute_script(
            script,
            hint,
            context.log,
            timeout=timeout,
            version=payload.get("version", "auto"),
            target_file=payload.get("target_file", ""),
            keep_open=bool(payload.get("keep_open", False)),
        )
        return {
            "success": returncode == 0,
            "returncode": returncode,
            "output": output if isinstance(output, str) else str(output),
        }


class LegacyShellProcessAdapter(RuntimeAdapter):
    """Compatibility adapter around the existing per-request ShellEngine."""

    adapter_id = "legacy-shell-process"
    operations = ("execute",)
    capabilities = ("process.execute", "async-output")

    def __init__(self, engine_provider: Callable[[], Any]):
        self._engine_provider = engine_provider

    def execute(
        self,
        request: RuntimeExecutionRequest,
        context: RuntimeExecutionContext,
    ) -> Dict[str, Any]:
        engine = self._engine_provider()
        if engine is None or not callable(getattr(engine, "exec", None)):
            raise RuntimeAdapterError("legacy Shell execution engine is unavailable")

        payload = request.payload
        env = payload.get("env") or {}
        if not isinstance(env, dict):
            env = {}
        result = engine.exec(
            command=payload.get("command") or payload.get("cmd") or "",
            shell=payload.get("shell", "powershell"),
            cwd=payload.get("cwd", ""),
            timeout=int(payload.get("timeout", 60) or 60),
            async_mode=bool(payload.get("async", False)),
            env=env,
        )
        if not isinstance(result, dict):
            raise RuntimeAdapterError("Shell engine must return a dictionary")
        return result


def select_eda_profile(
    script: str,
    filename_hint: str,
    capture_session_enabled: bool = False,
) -> str:
    """Resolve a script to its explicit local EDA Runtime Profile."""
    from eda_engine import EDALanguage, detect_script_type
    language = detect_script_type(script, filename_hint)
    if language == EDALanguage.ALLEGRO_SKILL:
        return "allegro-skill-oneshot"
    if language == EDALanguage.ORCAD_TCL and capture_session_enabled:
        return "capture-tcl-session"
    return "legacy-cadence-oneshot"


def create_legacy_runtime_registry(
    eda_engine_provider: Callable[[], Any],
    shell_engine_provider: Callable[[], Any],
) -> RuntimeAdapterRegistry:
    """Create the legacy-compatible Cadence and Shell profiles."""
    registry = RuntimeAdapterRegistry()
    cadence = LegacyCadenceOneShotAdapter(eda_engine_provider)
    allegro = AllegroSkillAdapter(eda_engine_provider)
    shell = LegacyShellProcessAdapter(shell_engine_provider)
    registry.register_adapter(cadence)
    registry.register_adapter(allegro)
    registry.register_adapter(shell)

    registry.register_profile(
        RuntimeProfile(
            profile_id="legacy-cadence-oneshot",
            adapter_id=cadence.adapter_id,
            display_name="Legacy Cadence one-shot compatibility",
            operations=("execute",),
            capabilities=cadence.capabilities,
            execution_mode="one-shot-process",
        )
    )
    registry.register_profile(
        RuntimeProfile(
            profile_id="allegro-skill-oneshot",
            adapter_id=allegro.adapter_id,
            display_name="Allegro SKILL one-shot compatibility",
            operations=("execute",),
            capabilities=allegro.capabilities,
            execution_mode="one-shot-gui",
        )
    )
    registry.register_profile(
        RuntimeProfile(
            profile_id="legacy-shell-process",
            adapter_id=shell.adapter_id,
            display_name="Legacy Shell process compatibility",
            operations=("execute",),
            capabilities=shell.capabilities,
            execution_mode="process-per-request",
        )
    )
    return registry
