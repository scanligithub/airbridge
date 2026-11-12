# test_request_tracker.py
# -*- coding: utf-8 -*-
import threading
import time
import unittest
import uuid

from request_tracker import (
    RequestTracker,
    RequestTrackerError,
    encode_bounded_json_payload,
)


class RequestTrackerTests(unittest.TestCase):
    def test_register_wait_and_complete_returns_structured_result(self):
        tracker = RequestTracker()
        request_id = str(uuid.uuid4())
        tracker.register(request_id, "eda.execute")

        waiter_result = {}
        waiter = threading.Thread(
            target=lambda: waiter_result.update(tracker.wait(request_id, 2.0))
        )
        waiter.start()
        time.sleep(0.01)

        self.assertTrue(
            tracker.complete(
                request_id,
                {"returncode": 0, "output": "Capture Tcl completed"},
            )
        )
        waiter.join(timeout=1.0)

        self.assertFalse(waiter.is_alive())
        self.assertEqual(waiter_result["state"], "completed")
        self.assertEqual(waiter_result["result"]["returncode"], 0)
        self.assertEqual(
            waiter_result["result"]["output"], "Capture Tcl completed"
        )

    def test_http_wait_timeout_keeps_request_pollable(self):
        tracker = RequestTracker()
        request_id = str(uuid.uuid4())
        tracker.register(request_id, "shell.exec")

        timed_out = tracker.wait(request_id, 0)
        self.assertEqual(timed_out["state"], "pending")

        self.assertTrue(
            tracker.complete(request_id, {"success": True, "exit_code": 0})
        )
        polled = tracker.get(request_id)
        self.assertEqual(polled["state"], "completed")
        self.assertEqual(polled["result"]["exit_code"], 0)

    def test_disconnect_marks_pending_request_unknown_and_wakes_waiter(self):
        tracker = RequestTracker()
        request_id = str(uuid.uuid4())
        tracker.register(request_id, "eda.execute")
        observed = threading.Event()
        result = {}

        def wait_for_result():
            observed.set()
            result.update(tracker.wait(request_id, 2.0))

        waiter = threading.Thread(target=wait_for_result)
        waiter.start()
        self.assertTrue(observed.wait(timeout=1.0))

        self.assertEqual(tracker.fail_all_pending("serial link disconnected"), 1)
        waiter.join(timeout=1.0)

        self.assertFalse(waiter.is_alive())
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(result["error"], "serial link disconnected")

    def test_duplicate_and_invalid_request_ids_are_rejected(self):
        tracker = RequestTracker()
        request_id = str(uuid.uuid4())
        tracker.register(request_id, "eda.execute")

        with self.assertRaises(RequestTrackerError):
            tracker.register(request_id, "shell.exec")
        with self.assertRaises(RequestTrackerError):
            tracker.register("not-a-uuid", "eda.execute")

    def test_tracker_does_not_evict_pending_records_when_full(self):
        tracker = RequestTracker(max_records=1)
        tracker.register(str(uuid.uuid4()), "eda.execute")

        with self.assertRaises(RequestTrackerError):
            tracker.register(str(uuid.uuid4()), "shell.exec")

    def test_completed_records_expire_and_are_pruned(self):
        tracker = RequestTracker(ttl_seconds=1)
        request_id = str(uuid.uuid4())
        tracker.register(request_id, "eda.execute")
        tracker.complete(request_id, {"returncode": 0})
        time.sleep(1.05)

        snapshot = tracker.get(request_id)
        self.assertEqual(snapshot["state"], "not_found")

    def test_large_text_result_is_truncated_to_abfp_limit(self):
        payload = encode_bounded_json_payload(
            {"request_id": str(uuid.uuid4()), "stdout": "输出内容" * 30000},
            max_bytes=4096,
        )
        self.assertLessEqual(len(payload), 4096)
        import json
        decoded = json.loads(payload.decode("utf-8"))
        self.assertTrue(decoded["stdout_truncated"])
        self.assertLess(len(decoded["stdout"]), 30000)


if __name__ == "__main__":
    unittest.main()
