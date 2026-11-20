from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import airbridge_agent_admin


class _FakeSecurityStore:
    def __init__(self, *, existing=None, fail_revoke=False):
        self.existing = existing or []
        self.issued = None
        self.revoked = []
        self.fail_revoke = fail_revoke

    def list_tokens(self, _agent_id):
        return self.existing

    def issue_token(self, agent_id, scopes, **kwargs):
        self.issued = {
            "agent_id": agent_id.lower(),
            "token_id": "token-id",
            "token": "secret-must-not-be-returned",
            "scopes": sorted(scopes),
            "expires_at": "2030-01-01T00:00:00Z",
        }
        return self.issued

    def revoke_token(self, token_id):
        self.revoked.append(token_id)
        if self.fail_revoke:
            raise RuntimeError("security store unavailable")
        return True

    def revoke_agent(self, agent_id):
        self.revoked.append(agent_id)
        if self.fail_revoke:
            raise RuntimeError("security store unavailable")
        return True


class _FakeCredentialStore:
    def __init__(self, *, fail_store=False, fail_load=False, fail_delete=False):
        self.values = {}
        self.fail_store = fail_store
        self.fail_load = fail_load
        self.fail_delete = fail_delete
        self.delete_calls = []

    def store(self, agent_id, token):
        if self.fail_store:
            raise airbridge_agent_admin.CredentialStoreError("vault write failed")
        self.values[agent_id] = token

    def load(self, agent_id):
        if self.fail_load:
            raise airbridge_agent_admin.CredentialStoreError("vault readback failed")
        if agent_id not in self.values:
            raise airbridge_agent_admin.CredentialNotFound("credential not found")
        return self.values[agent_id]

    def delete(self, agent_id):
        self.delete_calls.append(agent_id)
        if self.fail_delete:
            raise airbridge_agent_admin.CredentialStoreError("vault delete failed")
        return self.values.pop(agent_id, None) is not None


class AgentProvisioningTests(unittest.TestCase):
    def test_legacy_issue_requires_explicit_token_reveal_opt_in(self):
        args = airbridge_agent_admin.build_parser().parse_args([
            "issue", "--agent-id", "opencode", "--scope", "runtime.read"
        ])
        self.assertFalse(args.reveal_token)
        explicit = airbridge_agent_admin.build_parser().parse_args([
            "issue", "--agent-id", "opencode", "--scope", "runtime.read",
            "--reveal-token",
        ])
        self.assertTrue(explicit.reveal_token)

    def _args(self, **overrides):
        values = {
            "agent_id": "OpenCode",
            "scope": None,
            "label": "OpenCode Desktop",
            "expires_days": 90,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_provision_stores_secret_and_returns_only_non_secret_metadata(self):
        security = _FakeSecurityStore()
        credentials = _FakeCredentialStore()
        with patch.object(airbridge_agent_admin.os, "name", "nt"), patch.object(
            airbridge_agent_admin, "WindowsCredentialStore", return_value=credentials
        ):
            result = airbridge_agent_admin._provision_agent(security, self._args())

        self.assertTrue(result["provisioned"])
        self.assertEqual(result["agent_id"], "opencode")
        self.assertEqual(
            result["scopes"],
            ["eda.versions.read", "request.read", "runtime.read"],
        )
        self.assertEqual(credentials.values["opencode"], "secret-must-not-be-returned")
        self.assertNotIn("token", result)
        self.assertNotIn("secret-must-not-be-returned", repr(result))

    def test_provision_failure_revokes_new_server_credential(self):
        security = _FakeSecurityStore()
        credentials = _FakeCredentialStore(fail_store=True)
        with patch.object(airbridge_agent_admin.os, "name", "nt"), patch.object(
            airbridge_agent_admin, "WindowsCredentialStore", return_value=credentials
        ):
            with self.assertRaises(airbridge_agent_admin.CredentialStoreError):
                airbridge_agent_admin._provision_agent(security, self._args())
        self.assertEqual(security.revoked, ["token-id"])

    def test_rollback_attempts_local_cleanup_when_server_revoke_fails(self):
        security = _FakeSecurityStore(fail_revoke=True)
        credentials = _FakeCredentialStore(fail_load=True)
        with patch.object(airbridge_agent_admin.os, "name", "nt"), patch.object(
            airbridge_agent_admin, "WindowsCredentialStore", return_value=credentials
        ):
            with self.assertRaisesRegex(
                airbridge_agent_admin.CredentialStoreError,
                "rollback incomplete: server credential revocation",
            ):
                airbridge_agent_admin._provision_agent(security, self._args())

        self.assertEqual(security.revoked, ["token-id"])
        self.assertEqual(credentials.delete_calls, ["opencode"])
        self.assertEqual(credentials.values, {})

    def test_rollback_reports_local_cleanup_failure(self):
        security = _FakeSecurityStore()
        credentials = _FakeCredentialStore(fail_load=True, fail_delete=True)
        with patch.object(airbridge_agent_admin.os, "name", "nt"), patch.object(
            airbridge_agent_admin, "WindowsCredentialStore", return_value=credentials
        ):
            with self.assertRaisesRegex(
                airbridge_agent_admin.CredentialStoreError,
                "rollback incomplete: local credential deletion",
            ):
                airbridge_agent_admin._provision_agent(security, self._args())

        self.assertEqual(security.revoked, ["token-id"])
        self.assertEqual(credentials.delete_calls, ["opencode"])
        self.assertIn("opencode", credentials.values)

    def test_provision_refuses_to_replace_existing_active_credential(self):
        security = _FakeSecurityStore(existing=[{"enabled": True}])
        with patch.object(airbridge_agent_admin.os, "name", "nt"), patch.object(
            airbridge_agent_admin, "WindowsCredentialStore"
        ):
            with self.assertRaisesRegex(
                airbridge_agent_admin.CredentialStoreError, "already has an active credential"
            ):
                airbridge_agent_admin._provision_agent(security, self._args())
        self.assertIsNone(security.issued)

    def test_single_token_revoke_deletes_vault_only_when_it_matches(self):
        security = _FakeSecurityStore(
            existing=[{"token_id": "token-id", "agent_id": "opencode", "enabled": True}]
        )
        credentials = _FakeCredentialStore()
        credentials.values["opencode"] = "abg1.token-id.secret"
        with patch.object(airbridge_agent_admin.os, "name", "nt"), patch.object(
            airbridge_agent_admin, "WindowsCredentialStore", return_value=credentials
        ):
            result, exit_code = airbridge_agent_admin._revoke_token(security, "token-id")
        self.assertEqual(exit_code, 0)
        self.assertTrue(result["revoked"])
        self.assertTrue(result["credential_deleted"])
        self.assertEqual(result["credential_cleanup"], "deleted")
        self.assertEqual(credentials.values, {})

    def test_single_token_revoke_preserves_a_different_current_vault_token(self):
        security = _FakeSecurityStore(
            existing=[{"token_id": "old-token-id", "agent_id": "opencode", "enabled": True}]
        )
        credentials = _FakeCredentialStore()
        credentials.values["opencode"] = "abg1.current-token-id.secret"
        with patch.object(airbridge_agent_admin.os, "name", "nt"), patch.object(
            airbridge_agent_admin, "WindowsCredentialStore", return_value=credentials
        ):
            result, exit_code = airbridge_agent_admin._revoke_token(security, "old-token-id")
        self.assertEqual(exit_code, 0)
        self.assertEqual(result["credential_cleanup"], "not_current")
        self.assertEqual(credentials.values["opencode"], "abg1.current-token-id.secret")
        self.assertEqual(credentials.delete_calls, [])

    def test_single_token_revoke_reports_missing_vault_entry(self):
        security = _FakeSecurityStore(
            existing=[{"token_id": "token-id", "agent_id": "opencode", "enabled": True}]
        )
        credentials = _FakeCredentialStore()
        with patch.object(airbridge_agent_admin.os, "name", "nt"), patch.object(
            airbridge_agent_admin, "WindowsCredentialStore", return_value=credentials
        ):
            result, exit_code = airbridge_agent_admin._revoke_token(security, "token-id")
        self.assertEqual(exit_code, 0)
        self.assertEqual(result["credential_cleanup"], "not_found")

    def test_agent_revoke_reports_deleted_local_credential(self):
        security = _FakeSecurityStore()
        credentials = _FakeCredentialStore()
        credentials.values["opencode"] = "old-token"
        with patch.object(airbridge_agent_admin.os, "name", "nt"), patch.object(
            airbridge_agent_admin, "WindowsCredentialStore", return_value=credentials
        ):
            result, exit_code = airbridge_agent_admin._revoke_agent(security, "opencode")
        self.assertEqual(exit_code, 0)
        self.assertTrue(result["revoked"])
        self.assertTrue(result["credential_deleted"])
        self.assertEqual(result["credential_cleanup"], "deleted")

    def test_agent_revoke_treats_missing_local_credential_as_clean(self):
        security = _FakeSecurityStore()
        credentials = _FakeCredentialStore()
        with patch.object(airbridge_agent_admin.os, "name", "nt"), patch.object(
            airbridge_agent_admin, "WindowsCredentialStore", return_value=credentials
        ):
            result, exit_code = airbridge_agent_admin._revoke_agent(security, "opencode")
        self.assertEqual(exit_code, 0)
        self.assertFalse(result["credential_deleted"])
        self.assertEqual(result["credential_cleanup"], "not_found")

    def test_agent_revoke_returns_failure_when_local_cleanup_fails(self):
        security = _FakeSecurityStore()
        credentials = _FakeCredentialStore(fail_delete=True)
        with patch.object(airbridge_agent_admin.os, "name", "nt"), patch.object(
            airbridge_agent_admin, "WindowsCredentialStore", return_value=credentials
        ):
            result, exit_code = airbridge_agent_admin._revoke_agent(security, "opencode")
        self.assertEqual(exit_code, 1)
        self.assertTrue(result["revoked"])
        self.assertFalse(result["credential_deleted"])
        self.assertEqual(result["credential_cleanup"], "failed")


if __name__ == "__main__":
    unittest.main()
