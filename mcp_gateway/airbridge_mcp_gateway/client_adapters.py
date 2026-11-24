"""Safe current-user Antigravity / OpenCode MCP STDIO configuration adapters.

Only the dedicated AirBridge entries are managed. The JSONC parser records source
spans so edits replace only AirBridge-owned property values and preserve comments,
formatting, trailing commas, and all unrelated MCP host entries.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


class AdapterConfigurationError(ValueError):
    """The MCP host configuration is invalid or unsupported."""


class AdapterConflictError(AdapterConfigurationError):
    """A name reserved for AirBridge is already owned by another entry."""


@dataclass(slots=True)
class _Node:
    kind: str
    value: Any
    start: int
    end: int
    properties: dict[str, "_Node"] = field(default_factory=dict)
    property_spans: dict[str, tuple[int, int]] = field(default_factory=dict)
    children: list["_Node"] = field(default_factory=list)
    close_pos: int = -1
    trailing_comma: bool = False
    property_comma_positions: dict[str, int] = field(default_factory=dict)
    trailing_comma_pos: int = -1


class _JsoncParser:
    """Small strict JSONC parser: comments + trailing commas, with source spans."""

    def __init__(self, source: str):
        self.source = source
        self.index = 0
        self.decoder = json.JSONDecoder()

    def parse(self) -> _Node:
        node = self._value()
        self._skip()
        if self.index != len(self.source):
            raise AdapterConfigurationError(
                f"Unexpected content after JSONC value at offset {self.index}"
            )
        return node

    def _skip(self) -> None:
        text = self.source
        size = len(text)
        while self.index < size:
            char = text[self.index]
            if char.isspace() or char == "\ufeff":
                self.index += 1
                continue
            if char == "/" and self.index + 1 < size:
                next_char = text[self.index + 1]
                if next_char == "/":
                    self.index += 2
                    while self.index < size and text[self.index] not in "\r\n":
                        self.index += 1
                    continue
                if next_char == "*":
                    start = self.index
                    end = text.find("*/", self.index + 2)
                    if end < 0:
                        raise AdapterConfigurationError(
                            f"Unterminated JSONC block comment at offset {start}"
                        )
                    self.index = end + 2
                    continue
            break

    def _string(self) -> tuple[str, int, int]:
        self._skip()
        start = self.index
        if start >= len(self.source) or self.source[start] != '"':
            raise AdapterConfigurationError(
                f"Expected JSON string at offset {start}"
            )
        try:
            value, consumed = self.decoder.raw_decode(self.source[start:])
        except json.JSONDecodeError as exc:
            raise AdapterConfigurationError(
                f"Invalid JSON string at offset {start + exc.pos}: {exc.msg}"
            ) from exc
        if not isinstance(value, str):
            raise AdapterConfigurationError(f"Expected JSON string at offset {start}")
        self.index = start + consumed
        return value, start, self.index

    def _value(self) -> _Node:
        self._skip()
        start = self.index
        if start >= len(self.source):
            raise AdapterConfigurationError("Unexpected end of JSONC document")
        char = self.source[start]
        if char == "{":
            return self._object()
        if char == "[":
            return self._array()
        try:
            value, consumed = self.decoder.raw_decode(self.source[start:])
        except json.JSONDecodeError as exc:
            raise AdapterConfigurationError(
                f"Invalid JSONC value at offset {start + exc.pos}: {exc.msg}"
            ) from exc
        self.index = start + consumed
        kind = "string" if isinstance(value, str) else "primitive"
        return _Node(kind=kind, value=value, start=start, end=self.index)

    def _object(self) -> _Node:
        start = self.index
        self.index += 1
        properties: dict[str, _Node] = {}
        spans: dict[str, tuple[int, int]] = {}
        value: dict[str, Any] = {}
        trailing_comma = False
        property_comma_positions: dict[str, int] = {}
        trailing_comma_pos = -1
        self._skip()
        if self.index < len(self.source) and self.source[self.index] == "}":
            close = self.index
            self.index += 1
            return _Node(
                "object", value, start, self.index,
                properties=properties, property_spans=spans,
                close_pos=close, trailing_comma=False,
                property_comma_positions=property_comma_positions,
            )

        while True:
            key, key_start, key_end = self._string()
            if key in properties:
                raise AdapterConfigurationError(
                    f"Duplicate JSONC object key {key!r} at offset {key_start}"
                )
            self._skip()
            if self.index >= len(self.source) or self.source[self.index] != ":":
                raise AdapterConfigurationError(
                    f"Expected ':' after object key {key!r} at offset {self.index}"
                )
            self.index += 1
            child = self._value()
            properties[key] = child
            spans[key] = (key_start, child.end)
            value[key] = child.value
            self._skip()
            if self.index >= len(self.source):
                raise AdapterConfigurationError("Unterminated JSONC object")
            char = self.source[self.index]
            if char == "}":
                close = self.index
                self.index += 1
                return _Node(
                    "object", value, start, self.index,
                    properties=properties, property_spans=spans,
                    close_pos=close, trailing_comma=trailing_comma,
                    property_comma_positions=property_comma_positions,
                )
            if char != ",":
                raise AdapterConfigurationError(
                    f"Expected ',' or '}}' at offset {self.index}"
                )
            comma_pos = self.index
            property_comma_positions[key] = comma_pos
            self.index += 1
            self._skip()
            if self.index < len(self.source) and self.source[self.index] == "}":
                trailing_comma = True
                trailing_comma_pos = comma_pos
                close = self.index
                self.index += 1
                return _Node(
                    "object", value, start, self.index,
                    properties=properties, property_spans=spans,
                    close_pos=close, trailing_comma=True,
                    property_comma_positions=property_comma_positions,
                    trailing_comma_pos=self.index - 1,
                )
            trailing_comma = False

    def _array(self) -> _Node:
        start = self.index
        self.index += 1
        children: list[_Node] = []
        value: list[Any] = []
        self._skip()
        if self.index < len(self.source) and self.source[self.index] == "]":
            close = self.index
            self.index += 1
            return _Node("array", value, start, self.index, children=children, close_pos=close)

        while True:
            child = self._value()
            children.append(child)
            value.append(child.value)
            self._skip()
            if self.index >= len(self.source):
                raise AdapterConfigurationError("Unterminated JSONC array")
            char = self.source[self.index]
            if char == "]":
                close = self.index
                self.index += 1
                return _Node("array", value, start, self.index, children=children, close_pos=close)
            if char != ",":
                raise AdapterConfigurationError(
                    f"Expected ',' or ']' at offset {self.index}"
                )
            self.index += 1
            self._skip()
            if self.index < len(self.source) and self.source[self.index] == "]":
                close = self.index
                self.index += 1
                return _Node(
                    "array", value, start, self.index,
                    children=children, close_pos=close, trailing_comma=True,
                )


@dataclass(frozen=True, slots=True)
class AdapterResult:
    changed: bool
    config_path: str
    backup_path: str | None
    server_name: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class AdapterStatus:
    config_path: str
    server_name: str
    agent_id: str
    present: bool
    owned: bool
    enabled: bool | None


def _line_indent(source: str, offset: int) -> str:
    line_start = source.rfind("\n", 0, offset) + 1
    prefix = source[line_start:offset]
    return prefix[: len(prefix) - len(prefix.lstrip(" \t"))]


def _newline(source: str) -> str:
    return "\r\n" if "\r\n" in source else "\n"


def _render_value(value: Any, base_indent: str) -> str:
    rendered = json.dumps(value, ensure_ascii=False, indent=2)
    return rendered.replace("\n", "\n" + base_indent)


def _insert_property(source: str, node: _Node, key: str, value: Any) -> str:
    if node.kind != "object":
        raise AdapterConfigurationError("Cannot add a property to a non-object value")
    if key in node.properties:
        raise AdapterConfigurationError(f"Property {key!r} already exists")
    close_pos = node.close_pos
    if close_pos < 0:
        raise AdapterConfigurationError("JSONC object source span is incomplete")

    newline = _newline(source)
    parent_indent = _line_indent(source, node.start)
    if node.properties:
        first_key = next(iter(node.property_spans))
        first_key_start = node.property_spans[first_key][0]
        child_indent = _line_indent(source, first_key_start) or (parent_indent + "  ")
        comma = "" if node.trailing_comma else ","
        insertion = (
            comma + newline
            + child_indent + json.dumps(key, ensure_ascii=False) + ": "
            + _render_value(value, child_indent)
            + newline + parent_indent
        )
    else:
        child_indent = parent_indent + "  "
        insertion = (
            newline + child_indent + json.dumps(key, ensure_ascii=False) + ": "
            + _render_value(value, child_indent)
            + newline + parent_indent
        )
    return source[:close_pos] + insertion + source[close_pos:]


def _replace_node(source: str, node: _Node, value: Any, *, base_indent: str) -> str:
    return source[:node.start] + _render_value(value, base_indent) + source[node.end:]


def _property_value_indent(source: str, obj: _Node, key: str) -> str:
    key_span = obj.property_spans.get(key)
    if not key_span:
        return _line_indent(source, obj.start) + "  "
    return _line_indent(source, key_span[0])


def _backup_and_replace(path: Path, source_bytes: bytes, updated_bytes: bytes) -> str | None:
    if updated_bytes == source_bytes:
        return None

    path.parent.mkdir(parents=True, exist_ok=True)
    backup_path: Path | None = None
    if path.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = path.with_name(
            f"{path.name}.airbridge-{stamp}-{uuid.uuid4().hex[:8]}.bak"
        )
        shutil.copy2(path, backup_path)

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=".airbridge-", suffix=".tmp",
            dir=path.parent, delete=False,
        ) as stream:
            temp_path = Path(stream.name)
            stream.write(updated_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            try:
                shutil.copystat(path, temp_path)
            except OSError:
                pass
        os.replace(temp_path, path)
        temp_path = None
    except Exception:
        if backup_path is not None and backup_path.exists() and not path.exists():
            shutil.copy2(backup_path, path)
        raise
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
    return str(backup_path) if backup_path else None


def _patch_jsonc_property(source: str, property_path: tuple[str, ...], value: Any) -> str:
    """Patch one object property or insert it while preserving all other source text."""
    root = _JsoncParser(source).parse()
    if root.kind != "object":
        raise AdapterConfigurationError("MCP configuration root must be an object")
    parent = root
    for segment in property_path[:-1]:
        child = parent.properties.get(segment)
        if child is None or child.kind != "object":
            raise AdapterConfigurationError(
                f"Expected object at configuration path {'.'.join(property_path[:-1])!r}"
            )
        parent = child
    key = property_path[-1]
    existing = parent.properties.get(key)
    if existing is not None:
        if existing.value == value:
            return source
        base_indent = _property_value_indent(source, parent, key)
        return _replace_node(source, existing, value, base_indent=base_indent)
    return _insert_property(source, parent, key, value)


def _remove_jsonc_property(source: str, property_path: tuple[str, ...]) -> str:
    """Remove one property span without rebuilding the parent JSONC object."""
    root = _JsoncParser(source).parse()
    parent = root
    for segment in property_path[:-1]:
        child = parent.properties.get(segment)
        if child is None or child.kind != "object":
            return source
        parent = child
    key = property_path[-1]
    target = parent.properties.get(key)
    if target is None:
        return source

    keys = list(parent.property_spans)
    index = keys.index(key)
    key_start = parent.property_spans[key][0]
    target_end = target.end
    if index < len(keys) - 1:
        comma_pos = parent.property_comma_positions.get(key)
        if comma_pos is None:
            raise AdapterConfigurationError("JSONC property separator is missing")
        start, end = key_start, comma_pos + 1
    elif index > 0:
        previous_key = keys[index - 1]
        comma_pos = parent.property_comma_positions.get(previous_key)
        if comma_pos is None:
            raise AdapterConfigurationError("JSONC property separator is missing")
        start, end = comma_pos, target_end
        if parent.trailing_comma:
            if parent.trailing_comma_pos < 0:
                raise AdapterConfigurationError("JSONC trailing comma span is missing")
            end = parent.trailing_comma_pos + 1
    else:
        start, end = key_start, target_end
        if parent.trailing_comma:
            if parent.trailing_comma_pos < 0:
                raise AdapterConfigurationError("JSONC trailing comma span is missing")
            end = parent.trailing_comma_pos + 1
    return source[:start] + source[end:]


class _BaseAdapter:
    server_name = ""

    def __init__(self, executable: str | os.PathLike[str]):
        self.executable = str(Path(executable).expanduser().resolve())

    def _read(self, path: str | os.PathLike[str]) -> tuple[Path, bytes, str, _Node]:
        config_path = Path(path).expanduser()
        try:
            raw = config_path.read_bytes()
        except FileNotFoundError:
            raw = b""
        except OSError as exc:
            raise AdapterConfigurationError(
                f"Cannot read MCP configuration {config_path}: {exc}"
            ) from exc

        if not raw:
            source = "{}\n"
        else:
            try:
                source = raw.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise AdapterConfigurationError(
                    f"MCP configuration is not UTF-8: {config_path}"
                ) from exc
        root = _JsoncParser(source).parse()
        if root.kind != "object":
            raise AdapterConfigurationError("MCP configuration root must be an object")
        return config_path, raw, source, root

    @staticmethod
    def _require_object(node: _Node, label: str) -> _Node:
        if node.kind != "object":
            raise AdapterConfigurationError(f"{label} must be an object")
        return node

    @staticmethod
    def _existing_or_new_object(
        source: str, parent: _Node, key: str, label: str
    ) -> tuple[str, _Node]:
        child = parent.properties.get(key)
        if child is not None:
            return source, _BaseAdapter._require_object(child, label)
        parent_start = parent.start
        updated = _insert_property(source, parent, key, {})
        reparsed = _JsoncParser(updated).parse()

        def find_parent(node: _Node) -> _Node | None:
            if node.kind == "object" and node.start == parent_start:
                return node
            for child in node.properties.values():
                found = find_parent(child)
                if found is not None:
                    return found
            for child in node.children:
                found = find_parent(child)
                if found is not None:
                    return found
            return None

        reparsed_parent = find_parent(reparsed)
        if reparsed_parent is None or key not in reparsed_parent.properties:
            raise AdapterConfigurationError(
                f"Cannot locate newly created configuration object {key!r}"
            )
        return updated, reparsed_parent.properties[key]

    def _managed_updates(self, agent_id: str, enabled: bool) -> list[tuple[tuple[str, ...], Any]]:
        raise NotImplementedError

    def _server_config(self, agent_id: str, enabled: bool) -> dict[str, Any]:
        raise NotImplementedError

    def _root_server_container(self, source: str, root: _Node) -> tuple[str, _Node]:
        raise NotImplementedError

    def _existing_server_container(self, root: _Node) -> _Node | None:
        raise NotImplementedError

    def _is_owned(self, config: Any, agent_id: str) -> bool:
        raise NotImplementedError

    def inspect(
        self, path: str | os.PathLike[str], agent_id: str
    ) -> AdapterStatus:
        """Read status without creating files, modifying config, or touching other entries."""
        normalized_id = _validate_agent_id(agent_id)
        config_path, _raw, _source, root = self._read(path)
        if root.kind != "object":
            raise AdapterConfigurationError("MCP configuration root must be an object")
        container = self._existing_server_container(root)
        if container is None:
            return AdapterStatus(
                str(config_path), self.server_name, normalized_id,
                present=False, owned=False, enabled=None,
            )
        entry = container.properties.get(self.server_name)
        if entry is None:
            return AdapterStatus(
                str(config_path), self.server_name, normalized_id,
                present=False, owned=False, enabled=None,
            )
        if not self._is_owned(entry.value, normalized_id):
            return AdapterStatus(
                str(config_path), self.server_name, normalized_id,
                present=True, owned=False, enabled=None,
            )
        disabled = entry.value.get("disabled", False)
        return AdapterStatus(
            str(config_path), self.server_name, normalized_id,
            present=True,
            owned=True,
            enabled=(not disabled) if isinstance(disabled, bool) else None,
        )

    def install_or_update(
        self, path: str | os.PathLike[str], agent_id: str, *, enabled: bool
    ) -> AdapterResult:
        normalized_id = _validate_agent_id(agent_id)
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a Boolean")
        config_path, original_bytes, source, root = self._read(path)
        source, container = self._root_server_container(source, root)
        # Earlier insertions changed offsets; reparse before finding the target.
        root = _JsoncParser(source).parse()
        container = self._find_container(root)
        desired = self._server_config(normalized_id, enabled)
        existing_node = container.properties.get(self.server_name)

        if existing_node is None:
            updated = _insert_property(source, container, self.server_name, desired)
        else:
            existing_value = existing_node.value
            if not self._is_owned(existing_value, normalized_id):
                raise AdapterConflictError(
                    f"MCP server name {self.server_name!r} already exists and is not an AirBridge entry for Agent {normalized_id!r}"
                )
            # Patch only the fields this Adapter owns. Comments, unknown fields,
            # and the formatting of the surrounding JSONC remain untouched.
            updated = source
            for property_path, property_value in self._managed_updates(normalized_id, enabled):
                updated = _patch_jsonc_property(updated, property_path, property_value)

        # Reparse our result before committing; never write an invalid document.
        _JsoncParser(updated).parse()
        has_bom = original_bytes.startswith(b"\xef\xbb\xbf")
        updated_bytes = (b"\xef\xbb\xbf" if has_bom else b"") + updated.encode("utf-8")
        backup = _backup_and_replace(config_path, original_bytes, updated_bytes)
        return AdapterResult(
            changed=(updated_bytes != original_bytes),
            config_path=str(config_path),
            backup_path=backup,
            server_name=self.server_name,
            enabled=enabled,
        )

    def remove_owned(self, path: str | os.PathLike[str], agent_id: str) -> AdapterResult:
        """Remove only the exact managed AirBridge entry; reject user-owned collisions."""
        normalized_id = _validate_agent_id(agent_id)
        config_path, original_bytes, source, root = self._read(path)
        source, _container = self._root_server_container(source, root)
        root = _JsoncParser(source).parse()
        container = self._find_container(root)
        existing = container.properties.get(self.server_name)
        if existing is None:
            return AdapterResult(
                changed=False,
                config_path=str(config_path),
                backup_path=None,
                server_name=self.server_name,
                enabled=False,
            )
        if not self._is_owned(existing.value, normalized_id):
            raise AdapterConflictError(
                f"Refusing to remove {self.server_name!r}: it is not an AirBridge entry owned by Agent {normalized_id!r}"
            )
        updated = _remove_jsonc_property(
            source,
            self._container_path() + (self.server_name,),
        )
        _JsoncParser(updated).parse()
        has_bom = original_bytes.startswith(b"\xef\xbb\xbf")
        updated_bytes = (b"\xef\xbb\xbf" if has_bom else b"") + updated.encode("utf-8")
        backup = _backup_and_replace(config_path, original_bytes, updated_bytes)
        return AdapterResult(
            changed=(updated_bytes != original_bytes),
            config_path=str(config_path),
            backup_path=backup,
            server_name=self.server_name,
            enabled=False,
        )

    def _container_path(self) -> tuple[str, ...]:
        raise NotImplementedError

    def _find_container(self, root: _Node) -> _Node:
        raise NotImplementedError


def _same_executable_path(command: Any, expected: str) -> bool:
    """Match the exact AirBridge binary, not merely another file named AirBridge.exe."""
    if not isinstance(command, str) or not command.strip():
        return False
    try:
        actual_path = Path(command).expanduser().resolve()
        expected_path = Path(expected).expanduser().resolve()
        return os.path.normcase(str(actual_path)) == os.path.normcase(str(expected_path))
    except (OSError, RuntimeError, ValueError):
        return False


def _validate_agent_id(agent_id: str) -> str:
    if not isinstance(agent_id, str):
        raise ValueError("Agent ID must be a string")
    value = agent_id.strip().lower()
    if not value or any(not (char.isalnum() or char in "._-") for char in value):
        raise ValueError(f"Invalid Agent ID: {agent_id!r}")
    if not value[0].isalnum() or len(value) > 64:
        raise ValueError(f"Invalid Agent ID: {agent_id!r}")
    return value


class AntigravityAdapter(_BaseAdapter):
    """Manage Antigravity App/IDE/CLI's current-user global MCP configuration."""

    server_name = "airbridge-antigravity"

    @staticmethod
    def global_config(home: str | os.PathLike[str] | None = None) -> Path:
        return Path(home or Path.home()) / ".gemini" / "config" / "mcp_config.json"

    @staticmethod
    def cli_global_config(home: str | os.PathLike[str] | None = None) -> Path:
        """Backward-compatible alias for the global Antigravity config path."""
        return AntigravityAdapter.global_config(home)

    def _root_server_container(self, source: str, root: _Node) -> tuple[str, _Node]:
        return self._existing_or_new_object(source, root, "mcpServers", "mcpServers")

    def _find_container(self, root: _Node) -> _Node:
        return self._require_object(root.properties["mcpServers"], "mcpServers")

    def _existing_server_container(self, root: _Node) -> _Node | None:
        node = root.properties.get("mcpServers")
        if node is None:
            return None
        return self._require_object(node, "mcpServers")

    def _container_path(self) -> tuple[str, ...]:
        return ("mcpServers",)

    def _server_config(self, agent_id: str, enabled: bool) -> dict[str, Any]:
        return {
            "command": self.executable,
            "args": ["mcp", "stdio"],
            "env": {"AIRBRIDGE_AGENT_ID": agent_id},
            "disabled": not enabled,
        }

    def _is_owned(self, config: Any, agent_id: str) -> bool:
        if not isinstance(config, dict):
            return False
        command = config.get("command")
        args = config.get("args")
        env = config.get("env")
        return (
            _same_executable_path(command, self.executable)
            and isinstance(args, list)
            and args == ["mcp", "stdio"]
            and isinstance(env, dict)
            and env.get("AIRBRIDGE_AGENT_ID") == agent_id
        )

    def _managed_updates(self, agent_id: str, enabled: bool) -> list[tuple[tuple[str, ...], Any]]:
        prefix = ("mcpServers", self.server_name)
        return [
            (prefix + ("command",), self.executable),
            (prefix + ("args",), ["mcp", "stdio"]),
            (prefix + ("env", "AIRBRIDGE_AGENT_ID"), agent_id),
            (prefix + ("disabled",), not enabled),
        ]


class OpenCodeAdapter(_BaseAdapter):
    """OpenCode V2 uses local command arrays in the current user's global config."""

    server_name = "airbridge-opencode"

    @staticmethod
    def global_config(
        home: str | os.PathLike[str] | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> Path:
        env = os.environ if environ is None else environ
        # Match OpenCode's actual Global service resolution: the explicit
        # OPENCODE_CONFIG_DIR replaces the global root; otherwise XDG_CONFIG_HOME
        # supplies the root; finally default to ~/.config/opencode.
        explicit_config_dir = env.get("OPENCODE_CONFIG_DIR", "").strip()
        xdg_config_home = env.get("XDG_CONFIG_HOME", "").strip()
        if explicit_config_dir:
            directory = Path(explicit_config_dir).expanduser()
        elif xdg_config_home:
            directory = Path(xdg_config_home).expanduser() / "opencode"
        else:
            directory = Path(home or Path.home()) / ".config" / "opencode"
        jsonc_path = directory / "opencode.jsonc"
        json_path = directory / "opencode.json"
        if jsonc_path.exists():
            return jsonc_path
        if json_path.exists():
            return json_path
        return json_path

    def _root_server_container(self, source: str, root: _Node) -> tuple[str, _Node]:
        source, mcp_node = self._existing_or_new_object(source, root, "mcp", "mcp")
        # Reparse after adding mcp so offsets and property nodes remain valid.
        root = _JsoncParser(source).parse()
        mcp_node = self._require_object(root.properties["mcp"], "mcp")
        source, servers_node = self._existing_or_new_object(
            source, mcp_node, "servers", "mcp.servers"
        )
        return source, servers_node

    def _find_container(self, root: _Node) -> _Node:
        mcp_node = self._require_object(root.properties["mcp"], "mcp")
        return self._require_object(mcp_node.properties["servers"], "mcp.servers")

    def _existing_server_container(self, root: _Node) -> _Node | None:
        mcp_node = root.properties.get("mcp")
        if mcp_node is None:
            return None
        mcp_object = self._require_object(mcp_node, "mcp")
        servers_node = mcp_object.properties.get("servers")
        if servers_node is None:
            return None
        return self._require_object(servers_node, "mcp.servers")

    def _container_path(self) -> tuple[str, ...]:
        return ("mcp", "servers")

    def _server_config(self, agent_id: str, enabled: bool) -> dict[str, Any]:
        return {
            "type": "local",
            "command": [self.executable, "mcp", "stdio"],
            "environment": {"AIRBRIDGE_AGENT_ID": agent_id},
            "disabled": not enabled,
        }

    def _legacy_executable(self, config: Any, agent_id: str) -> str | None:
        """Return a structurally valid prior AirBridge path for explicit migration.

        This is deliberately a *candidate*, not proof of ownership. The caller
        must obtain explicit user confirmation before replacing the path.
        """
        if not isinstance(config, dict) or config.get("type") != "local":
            return None
        command = config.get("command")
        environment = config.get("environment")
        if (
            not isinstance(command, list)
            or len(command) != 3
            or command[1:] != ["mcp", "stdio"]
            or not isinstance(environment, dict)
            or environment.get("AIRBRIDGE_AGENT_ID") != agent_id
        ):
            return None
        disabled = config.get("disabled", False)
        if not isinstance(disabled, bool):
            return None
        raw_path = command[0]
        if not isinstance(raw_path, str) or not raw_path.strip():
            return None
        try:
            old_path = Path(raw_path).expanduser().resolve(strict=True)
            current_path = Path(self.executable).resolve()
        except (OSError, RuntimeError, ValueError):
            return None
        if (
            old_path == current_path
            or old_path.name.casefold() != "airbridge.exe"
            or not old_path.is_file()
        ):
            return None
        return str(old_path)

    def inspect_migration_candidate(
        self, path: str | os.PathLike[str], agent_id: str
    ) -> str | None:
        """Read-only detection of an older OpenCode AirBridge entry.

        A returned path is only a candidate. It is never treated as owned by
        this installation unless the user explicitly confirms migration.
        """
        normalized_id = _validate_agent_id(agent_id)
        _config_path, _raw, _source, root = self._read(path)
        container = self._existing_server_container(root)
        if container is None:
            return None
        entry = container.properties.get(self.server_name)
        if entry is None:
            return None
        return self._legacy_executable(entry.value, normalized_id)

    def migrate_legacy(
        self,
        path: str | os.PathLike[str],
        agent_id: str,
        *,
        enabled: bool,
    ) -> AdapterResult:
        """Explicitly migrate one verified-shape legacy entry to this EXE.

        The prior binary must still exist and the dedicated entry must match
        OpenCode's local STDIO schema and this Agent ID. The caller is
        responsible for obtaining explicit user confirmation first.
        """
        normalized_id = _validate_agent_id(agent_id)
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a Boolean")
        config_path, original_bytes, source, root = self._read(path)
        container = self._existing_server_container(root)
        if container is None:
            raise AdapterConflictError(
                f"Cannot migrate missing OpenCode server {self.server_name!r}"
            )
        entry = container.properties.get(self.server_name)
        if entry is None:
            raise AdapterConflictError(
                f"Cannot migrate missing OpenCode server {self.server_name!r}"
            )
        old_executable = self._legacy_executable(entry.value, normalized_id)
        if old_executable is None:
            raise AdapterConflictError(
                f"OpenCode server {self.server_name!r} is not a valid legacy AirBridge migration candidate"
            )

        prefix = ("mcp", "servers", self.server_name)
        updated = _patch_jsonc_property(
            source,
            prefix + ("command",),
            [self.executable, "mcp", "stdio"],
        )
        updated = _patch_jsonc_property(
            updated,
            prefix + ("disabled",),
            not enabled,
        )
        _JsoncParser(updated).parse()
        has_bom = original_bytes.startswith(b"\\xef\\xbb\\xbf")
        updated_bytes = (b"\\xef\\xbb\\xbf" if has_bom else b"") + updated.encode("utf-8")
        backup = _backup_and_replace(config_path, original_bytes, updated_bytes)
        return AdapterResult(
            changed=(updated_bytes != original_bytes),
            config_path=str(config_path),
            backup_path=backup,
            server_name=self.server_name,
            enabled=enabled,
        )

    def _is_owned(self, config: Any, agent_id: str) -> bool:
        if not isinstance(config, dict):
            return False
        command = config.get("command")
        environment = config.get("environment")
        return (
            config.get("type") == "local"
            and isinstance(command, list)
            and len(command) == 3
            and _same_executable_path(command[0], self.executable)
            and command[1:] == ["mcp", "stdio"]
            and isinstance(environment, dict)
            and environment.get("AIRBRIDGE_AGENT_ID") == agent_id
        )

    def _managed_updates(self, agent_id: str, enabled: bool) -> list[tuple[tuple[str, ...], Any]]:
        prefix = ("mcp", "servers", self.server_name)
        return [
            (prefix + ("command",), [self.executable, "mcp", "stdio"]),
            (prefix + ("type",), "local"),
            (prefix + ("environment", "AIRBRIDGE_AGENT_ID"), agent_id),
            (prefix + ("disabled",), not enabled),
        ]
