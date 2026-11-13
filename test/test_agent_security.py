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
        audit = self.store.list_audit(agent_id="opencode", limit=5)
        self.assertEqual(audit[0]["decision"], "issued")
        self.assertEqual(audit[0]["reason_code"], "AGENT_TOKEN_ISSUED")

    def test_invalid_or_missing_bearer_is_rejected(self):
        for header in (None, "", "Basic abc", "Bearer malformed"):
            with self.subTest(header=header):
                with self.assertRaises(AgentAuthenticationError):
                    self.store.authenticate(header)

    def test_revoked_token_is_rejected_immediately(self):
        issued = self.store.issue_token("antigravity", ["runtime.read"])
        self.assertTrue(self.store.revoke_token(issued["token_id"]))
        audit = self.store.list_audit(agent_id="antigravity", limit=5)
        self.assertEqual(audit[0]["decision"], "revoked")
        self.assertEqual(audit[0]["reason_code"], "AGENT_TOKEN_REVOKED")
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


    def test_sliding_window_rate_limit_is_per_agent_and_action(self):
        self.assertEqual(
            self.store.consume_rate_limit("antigravity", "read", limit=2),
            0,
        )
        self.assertEqual(
            self.store.consume_rate_limit("antigravity", "read", limit=2),
            0,
        )
        self.assertGreater(
            self.store.consume_rate_limit("antigravity", "read", limit=2),
            0,
        )
        # Another Agent and another action have independent windows.
        self.assertEqual(
            self.store.consume_rate_limit("opencode", "read", limit=2),
            0,
        )
        self.assertEqual(
            self.store.consume_rate_limit("antigravity", "status", limit=1),
            0,
        )

    def test_per_agent_inflight_execution_limit(self):
        first = "d20f8b2c-6d01-4f9b-9c18-53ddf5dcae31"
        second = "a20f8b2c-6d01-4f9b-9c18-53ddf5dcae32"
        third = "b20f8b2c-6d01-4f9b-9c18-53ddf5dcae33"
        self.assertTrue(
            self.store.begin_execution("antigravity", first, max_inflight=1)
        )
        self.assertFalse(
            self.store.begin_execution("antigravity", second, max_inflight=1)
        )
        self.assertEqual(self.store.active_execution_count("antigravity"), 1)
        # A different Agent has an independent slot and a distinct request ID.
        self.assertTrue(
            self.store.begin_execution("opencode", second, max_inflight=1)
        )
        self.store.end_execution("antigravity", first)
        self.assertTrue(
            self.store.begin_execution("antigravity", third, max_inflight=1)
        )
        self.assertEqual(self.store.active_execution_count("antigravity"), 1)

    def test_active_execution_reservation_survives_store_reopen(self):
        first = "d20f8b2c-6d01-4f9b-9c18-53ddf5dcae31"
        second = "a20f8b2c-6d01-4f9b-9c18-53ddf5dcae32"
        db_path = self.store.db_path
        self.assertTrue(self.store.begin_execution("antigravity", first))
        self.store.close()
        reopened = AgentSecurityStore(db_path)
        try:
            self.assertEqual(reopened.active_execution_count("antigravity"), 1)
            self.assertFalse(
                reopened.begin_execution("antigravity", second, max_inflight=1)
            )
            reopened.end_execution("antigravity", first)
            self.assertTrue(
                reopened.begin_execution("antigravity", second, max_inflight=1)
            )
        finally:
            reopened.close()
        # Prevent tearDown from closing an already closed connection.
        self.store = AgentSecurityStore(db_path)

    def test_request_owner_expiration_is_bounded(self):
        request_id = "d20f8b2c-6d01-4f9b-9c18-53ddf5dcae31"
        token = self.store.issue_token("antigravity", ["request.read"])
        principal = self.store.authenticate("Bearer " + token["token"])
        self.store.bind_request_owner(request_id, "antigravity")
        self.assertTrue(self.store.can_read_request(request_id, principal))
        self.store._conn.execute(
            "UPDATE agent_request_owners SET created_at = ? WHERE request_id = ?",
            (0, request_id),
        )
        self.assertFalse(self.store.can_read_request(request_id, principal))
        self.assertEqual(self.store.prune_request_owners(), 0)

    def test_persistent_execution_reservation_survives_store_reopen(self):
        first = "d20f8b2c-6d01-4f9b-9c18-53ddf5dcae31"
        second = "a20f8b2c-6d01-4f9b-9c18-53ddf5dcae32"
        db_path = self.store.db_path
        self.assertTrue(self.store.begin_execution("antigravity", first))
        self.store.close()
        self.store = AgentSecurityStore(db_path)
        self.assertEqual(self.store.active_execution_count("antigravity"), 1)
        self.assertFalse(
            self.store.begin_execution("antigravity", second, max_inflight=1)
        )
        self.store.end_execution("antigravity", first)
        self.assertTrue(
            self.store.begin_execution("antigravity", second, max_inflight=1)
        )

    def test_request_owner_expiration_is_bounded(self):
        request_id = "d20f8b2c-6d01-4f9b-9c18-53ddf5dcae31"
        issued = self.store.issue_token("antigravity", ["request.read"])
        principal = self.store.authenticate("Bearer " + issued["token"])
        self.store.bind_request_owner(request_id, "antigravity")
        self.assertTrue(self.store.can_read_request(request_id, principal))
        self.store._conn.execute(
            "UPDATE agent_request_owners SET created_at = ? WHERE request_id = ?",
            (0, request_id),
        )
        self.assertFalse(self.store.can_read_request(request_id, principal))

    def test_audit_retention_and_max_record_pruning(self):
        for index in range(4):
            self.store.audit_request(
                agent_id="opencode",
                token_id="token-key",
                method="GET",
                path=f"/api/v1/test/{index}",
                decision="allowed",
                http_status=200,
            )
        self.store._conn.execute(
            "UPDATE agent_audit SET timestamp = '2000-01-01T00:00:00.000Z' "
            "WHERE audit_id = 1"
        )
        removed = self.store.prune_audit(retention_days=90, max_records=2)
        self.assertGreaterEqual(removed, 2)
        self.assertLessEqual(len(self.store.list_audit(limit=10)), 2)

if __name__ == "__main__":
    unittest.main()
