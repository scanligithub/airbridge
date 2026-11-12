# test_request_broker.py
# -*- coding: utf-8 -*-
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
import uuid

from request_broker import RequestBroker


class RequestBrokerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "broker.sqlite3")
        self.broker = RequestBroker(self.db_path, ttl_seconds=30, max_records=20)

    def tearDown(self):
        self.broker.close()
        self.temp_dir.cleanup()

    def test_first_request_executes_and_duplicate_replays_cached_result(self):
        request_id = str(uuid.uuid4())
        calls = []

        def execute():
            calls.append("executed")
            return {"returncode": 0, "output": "one result"}

        first = self.broker.execute(
            request_id, "eda.execute", {"script": "puts 1"}, execute
        )
        second = self.broker.execute(
            request_id, "eda.execute", {"script": "puts 1"}, execute
        )

        self.assertEqual(first["disposition"], "executed")
        self.assertEqual(first["state"], "completed")
        self.assertEqual(second["disposition"], "cached")
        self.assertEqual(second["result"], first["result"])
        self.assertEqual(calls, ["executed"])

    def test_same_id_with_different_payload_is_rejected(self):
        request_id = str(uuid.uuid4())
        calls = []

        first = self.broker.execute(
            request_id,
            "eda.execute",
            {"script": "puts 1"},
            lambda: calls.append("first") or {"returncode": 0},
        )
        second = self.broker.execute(
            request_id,
            "eda.execute",
            {"script": "puts 2"},
            lambda: calls.append("second") or {"returncode": 0},
        )

        self.assertEqual(first["state"], "completed")
        self.assertEqual(second["disposition"], "conflict")
        self.assertEqual(second["error"]["code"], "REQUEST_ID_CONFLICT")
        self.assertEqual(calls, ["first"])

    def test_same_id_cannot_be_reused_for_a_different_request_kind(self):
        request_id = str(uuid.uuid4())
        self.broker.execute(
            request_id, "eda.execute", {"command": "same"}, lambda: {"returncode": 0}
        )
        result = self.broker.execute(
            request_id, "shell.exec", {"command": "same"}, lambda: {"success": True}
        )
        self.assertEqual(result["disposition"], "conflict")
        self.assertEqual(result["error"]["code"], "REQUEST_ID_CONFLICT")

    def test_concurrent_duplicate_does_not_execute_while_first_is_running(self):
        request_id = str(uuid.uuid4())
        started = threading.Event()
        release = threading.Event()
        calls = []
        first_result = {}

        def execute():
            calls.append("first")
            started.set()
            self.assertTrue(release.wait(timeout=3.0))
            return {"returncode": 0, "output": "done"}

        first_thread = threading.Thread(
            target=lambda: first_result.update(
                self.broker.execute(
                    request_id, "eda.execute", {"script": "puts 1"}, execute
                )
            )
        )
        first_thread.start()
        self.assertTrue(started.wait(timeout=2.0))

        duplicate = self.broker.execute(
            request_id,
            "eda.execute",
            {"script": "puts 1"},
            lambda: calls.append("duplicate") or {"returncode": 99},
        )
        self.assertEqual(duplicate["disposition"], "in_progress")
        self.assertEqual(calls, ["first"])

        release.set()
        first_thread.join(timeout=2.0)
        self.assertFalse(first_thread.is_alive())
        self.assertEqual(first_result["state"], "completed")
        self.assertEqual(calls, ["first"])

    def test_duplicate_async_shell_start_returns_cached_acceptance(self):
        request_id = str(uuid.uuid4())
        calls = []

        def execute():
            calls.append("started")
            return {
                "success": True,
                "task_id": "shell-1",
                "status": "running",
                "async": True,
            }

        first = self.broker.execute(
            request_id, "shell.exec", {"command": "long-task", "async": True}, execute
        )
        second = self.broker.execute(
            request_id, "shell.exec", {"command": "long-task", "async": True}, execute
        )

        self.assertEqual(first["state"], "accepted")
        self.assertEqual(second["state"], "accepted")
        self.assertEqual(second["disposition"], "cached")
        self.assertEqual(second["result"]["task_id"], "shell-1")
        self.assertEqual(calls, ["started"])

    def test_completed_result_survives_broker_restart(self):
        request_id = str(uuid.uuid4())
        calls = []

        first = self.broker.execute(
            request_id,
            "eda.execute",
            {"script": "puts durable"},
            lambda: calls.append("first") or {"returncode": 0, "output": "cached"},
        )
        self.assertEqual(first["state"], "completed")

        self.broker.close()
        recovered = RequestBroker(self.db_path, ttl_seconds=30, max_records=20)
        try:
            duplicate = recovered.execute(
                request_id,
                "eda.execute",
                {"script": "puts durable"},
                lambda: calls.append("second") or {"returncode": 99},
            )
            self.assertEqual(duplicate["disposition"], "cached")
            self.assertEqual(duplicate["state"], "completed")
            self.assertEqual(duplicate["result"]["output"], "cached")
            self.assertEqual(calls, ["first"])
        finally:
            recovered.close()
        self.broker = RequestBroker(self.db_path, ttl_seconds=30, max_records=20)

    def test_broker_restart_recovers_running_request_as_unknown(self):
        request_id = str(uuid.uuid4())
        payload = {"script": "puts may-have-run"}
        digest = RequestBroker.payload_digest(payload)
        now = time.time() - 200
        self.broker.close()

        connection = sqlite3.connect(self.db_path)
        connection.execute(
            """
            INSERT INTO broker_requests
            (request_id, request_kind, payload_sha256, state,
             created_at, updated_at, result_json, error)
            VALUES (?, ?, ?, 'running', ?, ?, NULL, NULL)
            """,
            (request_id, "eda.execute", digest, now, now),
        )
        connection.commit()
        connection.close()

        recovered = RequestBroker(self.db_path, ttl_seconds=30, max_records=20)
        try:
            calls = []
            result = recovered.execute(
                request_id,
                "eda.execute",
                payload,
                lambda: calls.append("must-not-run") or {"returncode": 0},
            )
            self.assertEqual(result["disposition"], "cached")
            self.assertEqual(result["state"], "unknown")
            self.assertEqual(
                result["result"]["error"]["code"], "BROKER_HEARTBEAT_TIMEOUT"
            )
            self.assertEqual(calls, [])
        finally:
            recovered.close()
        # Prevent tearDown from closing the already-closed original object again.
        self.broker = RequestBroker(self.db_path, ttl_seconds=30, max_records=20)

    def test_second_broker_does_not_mark_recent_running_request_unknown(self):
        request_id = str(uuid.uuid4())
        payload = {"script": "still-running"}
        digest = RequestBroker.payload_digest(payload)
        now = time.time()
        self.broker._conn.execute(
            """
            INSERT INTO broker_requests
            (request_id, request_kind, payload_sha256, state,
             created_at, updated_at, result_json, error)
            VALUES (?, ?, ?, 'running', ?, ?, NULL, NULL)
            """,
            (request_id, "eda.execute", digest, now, now),
        )

        second_broker = RequestBroker(
            self.db_path, ttl_seconds=30, max_records=20
        )
        try:
            duplicate = second_broker.execute(
                request_id,
                "eda.execute",
                payload,
                lambda: {"returncode": 99},
            )
            self.assertEqual(duplicate["disposition"], "in_progress")
            self.assertEqual(duplicate["state"], "running")
        finally:
            second_broker.close()

    def test_live_long_request_refreshes_heartbeat(self):
        request_id = str(uuid.uuid4())
        broker = RequestBroker(
            self.db_path,
            ttl_seconds=30,
            max_records=20,
            running_stale_seconds=0.3,
            heartbeat_interval_seconds=0.05,
        )
        started = threading.Event()
        release = threading.Event()

        def long_execution():
            started.set()
            self.assertTrue(release.wait(timeout=3.0))
            return {"returncode": 0}

        result_holder = {}
        thread = threading.Thread(
            target=lambda: result_holder.update(
                broker.execute(
                    request_id,
                    "eda.execute",
                    {"script": "long-running"},
                    long_execution,
                )
            )
        )
        thread.start()
        self.assertTrue(started.wait(timeout=1.0))
        time.sleep(0.6)

        duplicate = broker.execute(
            request_id,
            "eda.execute",
            {"script": "long-running"},
            lambda: {"returncode": 99},
        )
        self.assertEqual(duplicate["disposition"], "in_progress")
        release.set()
        thread.join(timeout=2.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result_holder["state"], "completed")
        broker.close()

    def test_active_requests_are_not_evicted_when_broker_is_full(self):
        broker = RequestBroker(self.db_path, ttl_seconds=30, max_records=1)
        request_id = str(uuid.uuid4())
        started = threading.Event()
        release = threading.Event()
        def execute_long():
            started.set()
            self.assertTrue(release.wait(timeout=2.0))
            return {"returncode": 0}

        thread = threading.Thread(
            target=lambda: broker.execute(
                request_id,
                "eda.execute",
                {"script": "long"},
                execute_long,
            )
        )
        thread.start()
        self.assertTrue(started.wait(timeout=2.0))

        rejected = broker.execute(
            str(uuid.uuid4()),
            "shell.exec",
            {"command": "another"},
            lambda: {"success": True},
        )
        self.assertEqual(rejected["disposition"], "capacity_rejected")
        self.assertEqual(broker.get(request_id)["state"], "running")

        release.set()
        thread.join(timeout=2.0)
        broker.close()

    def test_terminal_result_ttl_is_measured_from_completion(self):
        broker = RequestBroker(self.db_path, ttl_seconds=1, max_records=10)
        request_id = str(uuid.uuid4())
        time.sleep(0.55)
        first = broker.execute(
            request_id, "eda.execute", {"script": "puts 1"}, lambda: {"returncode": 0}
        )
        self.assertEqual(first["state"], "completed")
        time.sleep(0.55)
        self.assertEqual(broker.get(request_id)["state"], "completed")
        time.sleep(0.55)
        self.assertEqual(broker.get(request_id)["state"], "not_found")
        broker.close()

    def test_result_output_is_bounded_before_persistence(self):
        request_id = str(uuid.uuid4())
        result = self.broker.execute(
            request_id,
            "shell.exec",
            {"command": "long-output"},
            lambda: {"success": True, "stdout": "输出" * 50000},
        )
        self.assertEqual(result["state"], "completed")
        self.assertTrue(result["result"]["stdout_truncated"])
        self.assertLessEqual(
            len(json.dumps(result["result"], ensure_ascii=False).encode("utf-8")),
            RequestBroker.MAX_RESULT_BYTES + 256,
        )

    def test_lookup_does_not_evict_existing_result_at_capacity(self):
        broker = RequestBroker(self.db_path, ttl_seconds=30, max_records=2)
        first = str(uuid.uuid4())
        second = str(uuid.uuid4())
        third = str(uuid.uuid4())
        broker.execute(first, "eda.execute", {"script": "1"}, lambda: {"returncode": 0})
        broker.execute(second, "eda.execute", {"script": "2"}, lambda: {"returncode": 0})

        self.assertEqual(broker.get(first)["state"], "completed")
        self.assertEqual(broker.get(second)["state"], "completed")
        added = broker.execute(
            third, "eda.execute", {"script": "3"}, lambda: {"returncode": 0}
        )
        self.assertEqual(added["disposition"], "executed")
        self.assertEqual(broker.get(first)["state"], "not_found")
        self.assertEqual(broker.get(second)["state"], "completed")
        broker.close()


if __name__ == "__main__":
    unittest.main()
