from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import airbridge_agent_admin


class _FakeSecurityStore:
    def __init__(self, *, existing=None):
        self.existing = existing or []
        self.issued = None
        self.revoked = []

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
        return True


class _FakeCredentialStore:
    def __init__(self, *, fail_store=False):
        self.values = {}
        self.fail_store = fail_store

    def store(self, agent_id, token):
        if self.fail_store:
            raise airbridge_agent_admin.CredentialStoreError("vault write failed")
        self.values[agent_id] = token

    def load(self, agent_id):
        return self.values[agent_id]

    def delete(self, agent_id):
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


if __name__ == "__main__":
    unittest.main()
