from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from airbridge_mcp_gateway.client_adapters import (
    AdapterConflictError,
    AdapterConfigurationError,
    AntigravityAdapter,
    OpenCodeAdapter,
    _JsoncParser,
)


class ClientAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.exe = self.root / "AirBridge.exe"
        self.exe.write_bytes(b"test exe")
        self.antigravity = AntigravityAdapter(self.exe)
        self.opencode = OpenCodeAdapter(self.exe)

    def tearDown(self):
        self.temp.cleanup()

    def read_jsonc(self, path: Path):
        source = path.read_text(encoding="utf-8-sig")
        return _JsoncParser(source).parse().value

    def test_inspect_missing_antigravity_config_is_read_only(self):
        path = self.root / "not-created" / "mcp_config.json"

        status = self.antigravity.inspect(path, "antigravity")

        self.assertFalse(status.present)
        self.assertFalse(status.owned)
        self.assertIsNone(status.enabled)
        self.assertFalse(path.exists())
        self.assertFalse(path.parent.exists())

    def test_inspect_reports_owned_antigravity_entry_enabled_state(self):
        path = self.root / "mcp_config.json"
        self.antigravity.install_or_update(path, "antigravity", enabled=False)

        disabled = self.antigravity.inspect(path, "antigravity")
        self.assertTrue(disabled.present)
        self.assertTrue(disabled.owned)
        self.assertFalse(disabled.enabled)

        self.antigravity.install_or_update(path, "antigravity", enabled=True)
        enabled = self.antigravity.inspect(path, "antigravity")
        self.assertTrue(enabled.present)
        self.assertTrue(enabled.owned)
        self.assertTrue(enabled.enabled)

    def test_inspect_reports_name_collision_without_claiming_ownership(self):
        path = self.root / "mcp_config.json"
        original = json.dumps({
            "mcpServers": {
                "airbridge-antigravity": {
                    "command": "python.exe",
                    "args": ["-m", "not-airbridge"],
                }
            }
        })
        path.write_text(original, encoding="utf-8")

        status = self.antigravity.inspect(path, "antigravity")

        self.assertTrue(status.present)
        self.assertFalse(status.owned)
        self.assertIsNone(status.enabled)
        self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_antigravity_other_airbridge_binary_path_is_not_owned(self):
        path = self.root / "mcp_config.json"
        other_exe = self.root / "other-install" / "AirBridge.exe"
        other_exe.parent.mkdir(parents=True)
        other_exe.write_bytes(b"other build")
        original = json.dumps({
            "mcpServers": {
                "airbridge-antigravity": {
                    "command": str(other_exe.resolve()),
                    "args": ["mcp", "stdio"],
                    "env": {"AIRBRIDGE_AGENT_ID": "antigravity"},
                    "disabled": True,
                }
            }
        })
        path.write_text(original, encoding="utf-8")

        status = self.antigravity.inspect(path, "antigravity")

        self.assertTrue(status.present)
        self.assertFalse(status.owned)
        with self.assertRaises(AdapterConflictError):
            self.antigravity.remove_owned(path, "antigravity")
        with self.assertRaises(AdapterConflictError):
            self.antigravity.install_or_update(path, "antigravity", enabled=True)
        self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_opencode_other_airbridge_binary_path_is_not_owned(self):
        path = self.root / "opencode.json"
        other_exe = self.root / "other-install" / "AirBridge.exe"
        other_exe.parent.mkdir(parents=True)
        other_exe.write_bytes(b"other build")
        original = json.dumps({
            "mcp": {
                "servers": {
                    "airbridge-opencode": {
                        "type": "local",
                        "command": [str(other_exe.resolve()), "mcp", "stdio"],
                        "environment": {"AIRBRIDGE_AGENT_ID": "opencode"},
                        "disabled": True,
                    }
                }
            }
        })
        path.write_text(original, encoding="utf-8")

        status = self.opencode.inspect(path, "opencode")

        self.assertTrue(status.present)
        self.assertFalse(status.owned)
        with self.assertRaises(AdapterConflictError):
            self.opencode.remove_owned(path, "opencode")
        with self.assertRaises(AdapterConflictError):
            self.opencode.install_or_update(path, "opencode", enabled=True)
        self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_opencode_explicitly_migrates_legacy_airbridge_entry(self):
        path = self.root / "opencode.jsonc"
        old_exe = self.root / "previous-install" / "AirBridge.exe"
        old_exe.parent.mkdir(parents=True)
        old_exe.write_bytes(b"previous AirBridge build")
        original = f'''{{
  // preserve the user's OpenCode configuration
  "mcp": {{
    "servers": {{
      "airbridge-opencode": {{
        "type": "local",
        "command": [{json.dumps(str(old_exe.resolve()))}, "mcp", "stdio"],
        "environment": {{ "AIRBRIDGE_AGENT_ID": "opencode" }},
        "disabled": true,
        "operatorMetadata": {{ "note": "keep this field" }}
      }},
      "other-server": {{ "type": "local", "command": ["other.exe"] }}
    }}
  }}
}}
'''
        path.write_text(original, encoding="utf-8")

        candidate = self.opencode.inspect_migration_candidate(path, "opencode")
        self.assertEqual(candidate, str(old_exe.resolve()))
        before = self.opencode.inspect(path, "opencode")
        self.assertTrue(before.present)
        self.assertFalse(before.owned)

        result = self.opencode.migrate_legacy(path, "opencode", enabled=True)

        payload = self.read_jsonc(path)
        entry = payload["mcp"]["servers"]["airbridge-opencode"]
        self.assertEqual(
            entry["command"],
            [str(self.exe.resolve()), "mcp", "stdio"],
        )
        self.assertEqual(entry["environment"], {"AIRBRIDGE_AGENT_ID": "opencode"})
        self.assertFalse(entry["disabled"])
        self.assertEqual(entry["operatorMetadata"], {"note": "keep this field"})
        self.assertIn("// preserve the user's OpenCode configuration", path.read_text(encoding="utf-8"))
        self.assertIn("other-server", payload["mcp"]["servers"])
        self.assertTrue(result.changed)
        self.assertTrue(result.enabled)
        self.assertIsNotNone(result.backup_path)
        self.assertEqual(Path(result.backup_path).read_text(encoding="utf-8"), original)

        after = self.opencode.inspect(path, "opencode")
        self.assertTrue(after.owned)
        self.assertTrue(after.enabled)

    def test_opencode_migration_candidate_rejects_unrecognized_or_missing_binary(self):
        path = self.root / "opencode.json"
        old_exe = self.root / "old" / "AirBridge.exe"
        old_exe.parent.mkdir(parents=True)
        old_exe.write_bytes(b"old exe")
        invalid_entries = [
            {
                "type": "local",
                "command": [str(old_exe.resolve()), "mcp", "stdio"],
                "environment": {"AIRBRIDGE_AGENT_ID": "different-agent"},
                "disabled": True,
            },
            {
                "type": "local",
                "command": [str(old_exe.resolve()), "mcp", "other"],
                "environment": {"AIRBRIDGE_AGENT_ID": "opencode"},
                "disabled": True,
            },
            {
                "type": "local",
                "command": [str(self.root / "missing" / "AirBridge.exe"), "mcp", "stdio"],
                "environment": {"AIRBRIDGE_AGENT_ID": "opencode"},
                "disabled": True,
            },
        ]
        for entry in invalid_entries:
            path.write_text(json.dumps({
                "mcp": {"servers": {"airbridge-opencode": entry}}
            }), encoding="utf-8")
            original = path.read_bytes()
            self.assertIsNone(
                self.opencode.inspect_migration_candidate(path, "opencode")
            )
            with self.assertRaises(AdapterConflictError):
                self.opencode.migrate_legacy(path, "opencode", enabled=False)
            self.assertEqual(path.read_bytes(), original)

    def test_inspect_reports_opencode_owned_entry_and_does_not_create_missing_config(self):
        path = self.root / "opencode.json"
        missing_status = self.opencode.inspect(path, "opencode")
        self.assertFalse(missing_status.present)
        self.assertFalse(path.exists())

        self.opencode.install_or_update(path, "opencode", enabled=False)
        status = self.opencode.inspect(path, "opencode")
        self.assertTrue(status.present)
        self.assertTrue(status.owned)
        self.assertFalse(status.enabled)

    def test_antigravity_adds_only_managed_stdio_entry_and_backs_up(self):
        path = self.root / "mcp_config.json"
        original = (
            '{\n'
            '  "mcpServers": {\n'
            '    "other-server": {"command": "other.exe", "args": []}\n'
            '  },\n'
            '  "userSetting": true\n'
            '}\n'
        )
        path.write_text(original, encoding="utf-8")

        result = self.antigravity.install_or_update(
            path, "Antigravity", enabled=False
        )

        payload = self.read_jsonc(path)
        entry = payload["mcpServers"]["airbridge-antigravity"]
        self.assertEqual(entry["command"], str(self.exe.resolve()))
        self.assertEqual(entry["args"], ["mcp", "stdio"])
        self.assertEqual(entry["env"], {"AIRBRIDGE_AGENT_ID": "antigravity"})
        self.assertTrue(entry["disabled"])
        self.assertEqual(payload["mcpServers"]["other-server"], {
            "command": "other.exe", "args": []
        })
        self.assertTrue(payload["userSetting"])
        self.assertTrue(result.changed)
        self.assertIsNotNone(result.backup_path)
        self.assertEqual(Path(result.backup_path).read_text(encoding="utf-8"), original)

    def test_antigravity_enable_updates_only_disabled_flag_and_keeps_comments(self):
        path = self.root / "mcp_config.json"
        source = f'''{{
  "mcpServers": {{
    "airbridge-antigravity": {{
      // preserve this operator comment
      "command": {json.dumps(str(self.exe.resolve()))},
      "args": ["mcp", "stdio"],
      "env": {{
        "AIRBRIDGE_AGENT_ID": "antigravity"
      }},
      "disabled": true,
      "operatorMetadata": {{ "note": "keep me" }}
    }}
  }}
}}
'''
        path.write_text(source, encoding="utf-8")

        result = self.antigravity.install_or_update(
            path, "antigravity", enabled=True
        )

        updated = path.read_text(encoding="utf-8")
        payload = self.read_jsonc(path)
        entry = payload["mcpServers"]["airbridge-antigravity"]
        self.assertFalse(entry["disabled"])
        self.assertEqual(entry["operatorMetadata"], {"note": "keep me"})
        self.assertIn("// preserve this operator comment", updated)
        self.assertTrue(result.changed)
        self.assertIsNotNone(result.backup_path)

    def test_antigravity_idempotent_update_creates_no_backup(self):
        path = self.root / "mcp_config.json"
        self.antigravity.install_or_update(path, "antigravity", enabled=True)
        first = path.read_bytes()

        result = self.antigravity.install_or_update(
            path, "antigravity", enabled=True
        )

        self.assertFalse(result.changed)
        self.assertIsNone(result.backup_path)
        self.assertEqual(path.read_bytes(), first)

    def test_antigravity_conflicting_user_entry_is_not_overwritten(self):
        path = self.root / "mcp_config.json"
        source = json.dumps({
            "mcpServers": {
                "airbridge-antigravity": {
                    "command": "python.exe",
                    "args": ["-m", "some.other.server"],
                }
            }
        })
        path.write_text(source, encoding="utf-8")

        with self.assertRaises(AdapterConflictError):
            self.antigravity.install_or_update(path, "antigravity", enabled=True)

        self.assertEqual(path.read_text(encoding="utf-8"), source)

    def test_opencode_updates_global_jsonc_and_preserves_comments_trailing_comma(self):
        path = self.root / "opencode.jsonc"
        source = '''{
  // OpenCode user configuration
  "model": "example/model",
  "mcp": {
    "servers": {
      "other": {
        "type": "local",
        "command": ["other.exe"],
      },
      // keep this trailing comma and comment
    },
  },
}
'''
        path.write_text(source, encoding="utf-8")

        result = self.opencode.install_or_update(
            path, "opencode", enabled=True
        )

        updated = path.read_text(encoding="utf-8")
        payload = self.read_jsonc(path)
        entry = payload["mcp"]["servers"]["airbridge-opencode"]
        self.assertEqual(entry["type"], "local")
        self.assertEqual(
            entry["command"],
            [str(self.exe.resolve()), "mcp", "stdio"],
        )
        self.assertEqual(
            entry["environment"],
            {"AIRBRIDGE_AGENT_ID": "opencode"},
        )
        self.assertFalse(entry["disabled"])
        self.assertIn('"model": "example/model"', updated)
        self.assertIn('"other"', updated)
        self.assertIn("// OpenCode user configuration", updated)
        self.assertIn("// keep this trailing comma and comment", updated)
        self.assertTrue(result.changed)
        self.assertTrue(Path(result.backup_path).exists())

    def test_opencode_can_create_missing_nested_config(self):
        path = self.root / "fresh" / "opencode.json"
        result = self.opencode.install_or_update(
            path, "opencode", enabled=False
        )

        payload = self.read_jsonc(path)
        self.assertTrue(payload["mcp"]["servers"]["airbridge-opencode"]["disabled"])
        self.assertFalse((path.parent / "opencode.json.airbridge").exists())
        self.assertTrue(result.changed)
        self.assertIsNone(result.backup_path)

    def test_opencode_conflicting_entry_is_not_overwritten(self):
        path = self.root / "opencode.json"
        source = json.dumps({
            "mcp": {
                "servers": {
                    "airbridge-opencode": {
                        "type": "local",
                        "command": ["python.exe", "-m", "other"],
                        "environment": {"AIRBRIDGE_AGENT_ID": "opencode"},
                    }
                }
            }
        })
        path.write_text(source, encoding="utf-8")

        with self.assertRaises(AdapterConflictError):
            self.opencode.install_or_update(path, "opencode", enabled=False)

        self.assertEqual(path.read_text(encoding="utf-8"), source)

    def test_remove_owned_antigravity_entry_preserves_other_servers_and_backs_up(self):
        path = self.root / "mcp_config.json"
        original = json.dumps({
            "mcpServers": {
                "airbridge-antigravity": {
                    "command": str(self.exe.resolve()),
                    "args": ["mcp", "stdio"],
                    "env": {"AIRBRIDGE_AGENT_ID": "antigravity"},
                    "disabled": True,
                },
                "other-server": {"command": "other.exe", "args": []},
            },
            "userSetting": True,
        }, indent=2)
        path.write_text(original, encoding="utf-8")

        result = self.antigravity.remove_owned(path, "antigravity")

        payload = self.read_jsonc(path)
        self.assertNotIn("airbridge-antigravity", payload["mcpServers"])
        self.assertEqual(payload["mcpServers"]["other-server"], {
            "command": "other.exe", "args": []
        })
        self.assertTrue(payload["userSetting"])
        self.assertTrue(result.changed)
        self.assertIsNotNone(result.backup_path)
        self.assertEqual(Path(result.backup_path).read_text(encoding="utf-8"), original)

    def test_remove_owned_refuses_same_name_user_entry_without_modification(self):
        path = self.root / "mcp_config.json"
        original = json.dumps({
            "mcpServers": {
                "airbridge-antigravity": {
                    "command": "python.exe",
                    "args": ["-m", "some.other.server"],
                }
            }
        })
        path.write_text(original, encoding="utf-8")

        with self.assertRaises(AdapterConflictError):
            self.antigravity.remove_owned(path, "antigravity")

        self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_remove_owned_opencode_removes_only_dedicated_server_entry(self):
        path = self.root / "opencode.jsonc"
        payload = {
            "mcp": {
                "servers": {
                    "airbridge-opencode": {
                        "type": "local",
                        "command": [str(self.exe.resolve()), "mcp", "stdio"],
                        "environment": {"AIRBRIDGE_AGENT_ID": "opencode"},
                        "disabled": True,
                    },
                    "other": {"type": "local", "command": ["other.exe"]},
                }
            }
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        result = self.opencode.remove_owned(path, "opencode")

        updated = self.read_jsonc(path)
        self.assertNotIn("airbridge-opencode", updated["mcp"]["servers"])
        self.assertEqual(
            updated["mcp"]["servers"]["other"],
            {"type": "local", "command": ["other.exe"]},
        )
        self.assertTrue(result.changed)
        self.assertIsNotNone(result.backup_path)

    def test_remove_last_opencode_property_with_trailing_comma_keeps_valid_jsonc(self):
        path = self.root / "opencode.jsonc"
        source = f'''{{
  "mcp": {{
    "servers": {{
      "other": {{"type": "local", "command": ["other.exe"]}},
      // operator note adjacent to the managed entry
      "airbridge-opencode": {{
        "type": "local",
        "command": [{json.dumps(str(self.exe.resolve()))}, "mcp", "stdio"],
        "environment": {{"AIRBRIDGE_AGENT_ID": "opencode"}},
        "disabled": true
      }},
    }}
  }}
}}
'''
        path.write_text(source, encoding="utf-8")

        result = self.opencode.remove_owned(path, "opencode")

        updated = path.read_text(encoding="utf-8")
        payload = self.read_jsonc(path)
        self.assertNotIn("airbridge-opencode", payload["mcp"]["servers"])
        self.assertEqual(
            payload["mcp"]["servers"]["other"],
            {"type": "local", "command": ["other.exe"]},
        )
        self.assertIn("// operator note adjacent to the managed entry", updated)
        self.assertTrue(result.changed)
        self.assertIsNotNone(result.backup_path)
        self.assertEqual(Path(result.backup_path).read_text(encoding="utf-8"), source)

    def test_jsonc_utf8_bom_is_preserved(self):
        path = self.root / "mcp_config.json"
        path.write_bytes(b"\xef\xbb\xbf" + b'{"mcpServers": {}}')

        self.antigravity.install_or_update(path, "antigravity", enabled=True)

        self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertIn(
            "airbridge-antigravity",
            self.read_jsonc(path)["mcpServers"],
        )

    def test_invalid_jsonc_does_not_modify_original(self):
        path = self.root / "opencode.jsonc"
        source = '{ "mcp": /* missing object */ , }'
        path.write_text(source, encoding="utf-8")

        with self.assertRaises(AdapterConfigurationError):
            self.opencode.install_or_update(path, "opencode", enabled=True)

        self.assertEqual(path.read_text(encoding="utf-8"), source)

    def test_global_path_helpers_do_not_choose_project_or_system_config(self):
        home = self.root / "home"
        (home / ".gemini" / "config").mkdir(parents=True)
        expected_ant = home / ".gemini" / "config" / "mcp_config.json"
        self.assertEqual(AntigravityAdapter.global_config(home), expected_ant)
        self.assertEqual(AntigravityAdapter.cli_global_config(home), expected_ant)

        opencode_dir = home / ".config" / "opencode"
        opencode_dir.mkdir(parents=True)
        project_file = home / "workspace" / "opencode.json"
        project_file.parent.mkdir(parents=True)
        project_file.write_text("{}", encoding="utf-8")
        json_file = opencode_dir / "opencode.json"
        json_file.write_text("{}", encoding="utf-8")
        self.assertEqual(OpenCodeAdapter.global_config(home, environ={}), json_file)

        jsonc_file = opencode_dir / "opencode.jsonc"
        jsonc_file.write_text("{}", encoding="utf-8")
        self.assertEqual(OpenCodeAdapter.global_config(home, environ={}), jsonc_file)
        self.assertNotEqual(OpenCodeAdapter.global_config(home, environ={}), project_file)

    def test_opencode_global_path_respects_xdg_config_home(self):
        xdg_home = self.root / "custom-xdg"
        jsonc_path = xdg_home / "opencode" / "opencode.jsonc"
        jsonc_path.parent.mkdir(parents=True)
        jsonc_path.write_text("{}", encoding="utf-8")

        self.assertEqual(
            OpenCodeAdapter.global_config(
                home=self.root / "ignored-home",
                environ={"XDG_CONFIG_HOME": str(xdg_home)},
            ),
            jsonc_path,
        )

    def test_opencode_global_path_respects_explicit_config_dir_over_xdg(self):
        explicit_dir = self.root / "opencode-config-dir"
        xdg_home = self.root / "xdg-home"
        explicit_jsonc = explicit_dir / "opencode.jsonc"
        xdg_jsonc = xdg_home / "opencode" / "opencode.jsonc"
        explicit_dir.mkdir(parents=True)
        xdg_jsonc.parent.mkdir(parents=True)
        explicit_jsonc.write_text("{}", encoding="utf-8")
        xdg_jsonc.write_text("{}", encoding="utf-8")

        self.assertEqual(
            OpenCodeAdapter.global_config(
                home=self.root / "ignored-home",
                environ={
                    "XDG_CONFIG_HOME": str(xdg_home),
                    "OPENCODE_CONFIG_DIR": str(explicit_dir),
                },
            ),
            explicit_jsonc,
        )

    def test_invalid_agent_id_is_rejected(self):
        path = self.root / "mcp_config.json"
        with self.assertRaises(ValueError):
            self.antigravity.install_or_update(path, "../other", enabled=True)
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
