from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_security import (
    AgentAuthenticationError,
    AgentPrincipal,
    AgentSecurityError,
    AgentSecurityStore,
)


class AgentSecurityStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = AgentSecurityStore(str(Path(self.temp.name) / "security.sqlite3"))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_issue_token_and_authenticate_returns_server_side_identity(self):
        issued = self.store.issue_token(
            "OpenCode",
            ["runtime.read", "request.read"],
            label="OpenCode Desktop",
        )
        principal = self.store.authenticate("Bearer " + issued["token"])
        self.assertEqual(principal.agent_id, "opencode")
        self.assertEqual(set(principal.scopes), {"runtime.read", "request.read"})
        self.assertNotIn("token", self.store.list_tokens()[0])

    def test_invalid_or_missing_bearer_is_rejected(self):
        for header in (None, "", "Basic abc", "Bearer malformed"):
            with self.subTest(header=header):
                with self.assertRaises(AgentAuthenticationError):
                    self.store.authenticate(header)

    def test_revoked_token_is_rejected_immediately(self):
        issued = self.store.issue_token("antigravity", ["runtime.read"])
        self.assertTrue(self.store.revoke_token(issued["token_id"]))
        with self.assertRaises(AgentAuthenticationError):
            self.store.authenticate("Bearer " + issued["token"])

    def test_agent_wide_revocation(self):
        first = self.store.issue_token("opencode", ["runtime.read"])
        second = self.store.issue_token("opencode", ["request.read"])
        self.assertEqual(self.store.revoke_agent("opencode"), 2)
        for item in (first, second):
            with self.assertRaises(AgentAuthenticationError):
                self.store.authenticate("Bearer " + item["token"])

    def test_unknown_scopes_and_invalid_agent_ids_are_rejected(self):
        with self.assertRaises(AgentSecurityError):
            self.store.issue_token("antigravity", ["shell.root"])
        with self.assertRaises(AgentSecurityError):
            self.store.issue_token("../other", ["runtime.read"])

    def test_request_status_is_scoped_to_owning_agent(self):
        a = self.store.issue_token("antigravity", ["request.read"])
        b = self.store.issue_token("opencode", ["request.read"])
        principal_a = self.store.authenticate("Bearer " + a["token"])
        principal_b = self.store.authenticate("Bearer " + b["token"])
        request_id = "d20f8b2c-6d01-4f9b-9c18-53ddf5dcae31"
        self.store.bind_request_owner(request_id, "antigravity")
        self.assertTrue(self.store.can_read_request(request_id, principal_a))
        self.assertFalse(self.store.can_read_request(request_id, principal_b))

    def test_request_read_any_scope_is_explicit(self):
        issued = self.store.issue_token("audit-admin", ["request.read:any"])
        principal = self.store.authenticate("Bearer " + issued["token"])
        self.assertTrue(
            self.store.can_read_request(
                "d20f8b2c-6d01-4f9b-9c18-53ddf5dcae31", principal
            )
        )

    def test_audit_never_requires_or_stores_plaintext_token(self):
        self.store.audit_request(
            agent_id="opencode",
            token_id="token-key",
            method="GET",
            path="/api/v1/runtime/capabilities",
            decision="allowed",
            http_status=200,
            duration_ms=3,
        )
        records = self.store.list_audit(agent_id="opencode")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["decision"], "allowed")
        self.assertNotIn("token", records[0])
        self.assertNotIn("script", records[0])


if __name__ == "__main__":
    unittest.main()
