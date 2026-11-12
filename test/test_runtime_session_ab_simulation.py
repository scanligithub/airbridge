# test_runtime_session_ab_simulation.py
# -*- coding: utf-8 -*-
"""Software-only A/B handshake acceptance using the production SerialWorker code.

The two peers run as separate SerialWorker objects in this test process. The
in-memory duplex link transports real ABFP frames through FrameParser and
dispatches them to the production Runtime Session handlers. No COM port,
Cadence installation, or physical Windows host is required.
"""
import json
import threading
import uuid
import unittest
from types import SimpleNamespace

from airbridge_app import SerialWorker
from protocol import ABFPFrame, FrameParser, FrameType
from runtime_session_protocol import (
    encode_session_message,
    make_session_capabilities,
)
from request_tracker import RequestTracker
from request_broker import RequestBroker


class _FakeSerialPort:
    is_open = True


class _SignalSink:
    def emit(self, *args, **kwargs):
        pass


class _ObservedEvent(threading.Event):
    """Expose when a waiter has entered Event.wait()."""

    def __init__(self):
        super().__init__()
        self.wait_entered = threading.Event()

    def wait(self, timeout=None):
        self.wait_entered.set()
        return super().wait(timeout)


def _new_worker():
    # Bypass the GUI/EDA/serial-thread startup path. Only initialize the
    # state consumed by the production Runtime Session methods under test.
    worker = SerialWorker.__new__(SerialWorker)
    worker.ser = _FakeSerialPort()
    worker.lock = threading.Lock()
    worker.session_protocol_lock = threading.RLock()
    worker.session_pending_handshakes = {}
    worker.session_peer_status = {
        "state": "disconnected",
        "negotiated": False,
        "protocol": "runtime-session.v1",
        "supported_protocols": [],
        "features": [],
        "profiles": [],
        "last_handshake_id": None,
        "error": None,
    }
    worker.session_link_generation = 1
    worker.request_tracker = RequestTracker()
    worker.request_broker = RequestBroker(":memory:")
    worker.parser = FrameParser()
    worker.seq_counter = 0
    worker.sig = SimpleNamespace(sig_rx_log=_SignalSink())
    return worker


def _connect_duplex(peer_a, peer_b):
    """Connect two workers with a fragmented, in-memory ABFP byte stream."""
    def bind(source, target):
        def send_frame(frame_type, payload=b""):
            source.seq_counter = (source.seq_counter + 1) & 0xFFFF
            wire = ABFPFrame(frame_type, source.seq_counter, payload).pack()
            # Fragment frames to exercise the streaming parser rather than
            # passing already-decoded Python objects between the peers.
            for offset in range(0, len(wire), 7):
                chunk = wire[offset:offset + 7]
                for received in target.parser.feed(chunk):
                    target._handle_incoming_frame(received)
        source.send_frame = send_frame

    bind(peer_a, peer_b)
    bind(peer_b, peer_a)


class RuntimeSessionABSimulationTests(unittest.TestCase):
    def test_both_peers_initiate_and_complete_correlated_handshakes(self):
        host_a = _new_worker()
        host_b = _new_worker()
        _connect_duplex(host_a, host_b)

        request_a = host_a.begin_session_negotiation()
        request_b = host_b.begin_session_negotiation()

        self.assertTrue(request_a["success"], request_a)
        self.assertTrue(request_b["success"], request_b)
        self.assertNotEqual(request_a["message_id"], request_b["message_id"])

        result_a = host_a._wait_session_negotiation(
            request_a["message_id"], timeout=0.1
        )
        result_b = host_b._wait_session_negotiation(
            request_b["message_id"], timeout=0.1
        )

        self.assertEqual(result_a["state"], "negotiated", result_a)
        self.assertEqual(result_b["state"], "negotiated", result_b)
        self.assertEqual(
            result_a["peer_capabilities"]["features"], ["handshake", "request.correlation", "request.deduplication"]
        )
        self.assertEqual(
            result_b["peer_capabilities"]["features"], ["handshake", "request.correlation", "request.deduplication"]
        )

        status_a = host_a.get_session_peer_status()
        status_b = host_b.get_session_peer_status()
        self.assertTrue(status_a["negotiated"])
        self.assertTrue(status_b["negotiated"])
        self.assertEqual(status_a["protocol"], "runtime-session.v1")
        self.assertEqual(status_b["protocol"], "runtime-session.v1")
        self.assertFalse(status_a["can_create_session"])
        self.assertFalse(status_b["can_create_session"])
        self.assertEqual(status_a["profiles"], [])
        self.assertEqual(status_b["profiles"], [])

    def test_unmatched_capabilities_correlation_is_ignored(self):
        host = _new_worker()
        host.send_frame = lambda *_args, **_kwargs: None

        request = host.begin_session_negotiation()
        self.assertTrue(request["success"], request)
        pending = host.session_pending_handshakes[request["message_id"]]

        unrelated = make_session_capabilities(
            "00000000-0000-4000-8000-000000000001"
        )
        payload = encode_session_message(
            FrameType.SESSION_CAPABILITIES, unrelated
        )
        host._handle_incoming_frame(
            ABFPFrame(FrameType.SESSION_CAPABILITIES, 1, payload)
        )

        self.assertFalse(pending["event"].is_set())
        self.assertEqual(
            host.get_session_peer_status()["state"], "negotiating"
        )

        matched = make_session_capabilities(request["message_id"])
        payload = encode_session_message(
            FrameType.SESSION_CAPABILITIES, matched
        )
        host._handle_incoming_frame(
            ABFPFrame(FrameType.SESSION_CAPABILITIES, 2, payload)
        )
        result = host._wait_session_negotiation(
            request["message_id"], timeout=0.1
        )
        self.assertEqual(result["state"], "negotiated", result)

    def test_link_reset_resolves_pending_waiter_and_rejects_stale_generation(self):
        host = _new_worker()
        host.send_frame = lambda *_args, **_kwargs: None

        old_generation = host.session_link_generation
        request = host.begin_session_negotiation(
            expected_generation=old_generation
        )
        self.assertTrue(request["success"], request)

        observed_event = _ObservedEvent()
        host.session_pending_handshakes[request["message_id"]]["event"] = observed_event
        waiter_result = {}
        waiter = threading.Thread(
            target=lambda: waiter_result.update(
                host._wait_session_negotiation(
                    request["message_id"], timeout=2.0
                )
            )
        )
        waiter.start()
        self.assertTrue(
            observed_event.wait_entered.wait(timeout=1.0),
            "handshake waiter did not enter its blocking wait",
        )

        host._reset_session_peer_status("serial link disconnected")
        waiter.join(timeout=1.0)
        self.assertFalse(waiter.is_alive(), "link reset did not wake the waiter")
        result = waiter_result

        self.assertFalse(result["success"])
        self.assertEqual(result["state"], "disconnected")
        self.assertEqual(result["error"], "serial link disconnected")

        stale = host.begin_session_negotiation(
            expected_generation=old_generation
        )
        self.assertFalse(stale["success"])
        self.assertEqual(stale["state"], "stale")



    def test_eda_task_fin_resolves_matching_http_request_id(self):
        host = _new_worker()
        request_id = str(uuid.uuid4())
        host.request_tracker.register(request_id, "eda.execute")
        response = {
            "request_id": request_id,
            "returncode": 0,
            "output": "Capture Tcl result",
        }
        host._handle_incoming_frame(
            ABFPFrame(
                FrameType.EDA_TASK_FIN,
                7,
                __import__("json").dumps(response).encode("utf-8"),
            )
        )

        result = host.request_tracker.get(request_id)
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["result"]["returncode"], 0)
        self.assertEqual(result["result"]["output"], "Capture Tcl result")

    def test_shell_result_resolves_matching_http_request_id(self):
        host = _new_worker()
        request_id = str(uuid.uuid4())
        host.request_tracker.register(request_id, "shell.exec")
        response = {
            "request_id": request_id,
            "success": True,
            "task_id": "shell-test",
            "exit_code": 0,
            "stdout": "hello",
            "stderr": "",
        }
        host._handle_incoming_frame(
            ABFPFrame(
                FrameType.SHELL_RESULT,
                8,
                __import__("json").dumps(response).encode("utf-8"),
            )
        )

        result = host.request_tracker.get(request_id)
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["result"]["task_id"], "shell-test")
        self.assertEqual(result["result"]["stdout"], "hello")


    def test_nonzero_eda_returncode_is_persisted_as_failed(self):
        class FailingEDAExecutor:
            def execute_script(self, *args, **kwargs):
                return 1, "script returned nonzero"

        host_a = _new_worker()
        host_b = _new_worker()
        host_b.eda_executor = FailingEDAExecutor()
        _connect_duplex(host_a, host_b)

        request_id = str(uuid.uuid4())
        host_a.request_tracker.register(request_id, "eda.execute")
        payload = {
            "request_id": request_id,
            "script": "error \"expected\"",
            "hint": "failure.tcl",
            "version": "auto",
            "target_file": "",
            "timeout": 10,
            "keep_open": True,
        }
        host_a.send_frame(
            FrameType.EDA_TASK_SUBMIT,
            json.dumps(payload).encode("utf-8"),
        )

        result = host_a.request_tracker.wait(request_id, timeout=2.0)
        self.assertEqual(result["state"], "failed", result)
        self.assertEqual(result["result"]["returncode"], 1)
        broker_result = host_b.request_broker.get(request_id)
        self.assertEqual(broker_result["state"], "failed")

    def test_duplicate_eda_frame_replays_cached_result_without_reexecution(self):
        class CountingEDAExecutor:
            def __init__(self):
                self.calls = 0
                self.lock = threading.Lock()

            def execute_script(self, *args, **kwargs):
                with self.lock:
                    self.calls += 1
                return 0, "single side effect"

        host_a = _new_worker()
        host_b = _new_worker()
        executor = CountingEDAExecutor()
        host_b.eda_executor = executor
        _connect_duplex(host_a, host_b)

        responses = []
        response_condition = threading.Condition()
        send_to_a = host_b.send_frame

        def capture_eda_result(frame_type, payload=b""):
            if frame_type == FrameType.EDA_TASK_FIN:
                with response_condition:
                    responses.append(json.loads(payload.decode("utf-8")))
                    response_condition.notify_all()
            send_to_a(frame_type, payload)

        host_b.send_frame = capture_eda_result
        request_id = str(uuid.uuid4())
        host_a.request_tracker.register(request_id, "eda.execute")
        request_payload = {
            "request_id": request_id,
            "script": "puts side-effect",
            "hint": "dedupe.tcl",
            "version": "auto",
            "target_file": "",
            "timeout": 10,
            "keep_open": True,
        }
        frame_payload = json.dumps(request_payload).encode("utf-8")

        host_a.send_frame(FrameType.EDA_TASK_SUBMIT, frame_payload)
        with response_condition:
            self.assertTrue(
                response_condition.wait_for(lambda: len(responses) >= 1, timeout=2.0)
            )

        host_a.send_frame(FrameType.EDA_TASK_SUBMIT, frame_payload)
        with response_condition:
            self.assertTrue(
                response_condition.wait_for(lambda: len(responses) >= 2, timeout=2.0)
            )

        self.assertEqual(executor.calls, 1)
        self.assertEqual(responses[0]["returncode"], 0)
        self.assertEqual(responses[1]["output"], "single side effect")
        self.assertEqual(responses[0]["request_id"], request_id)
        self.assertEqual(responses[1]["request_id"], request_id)

    def test_same_eda_request_id_with_changed_payload_returns_conflict(self):
        class CountingEDAExecutor:
            def __init__(self):
                self.calls = 0

            def execute_script(self, *args, **kwargs):
                self.calls += 1
                return 0, "first payload only"

        host_a = _new_worker()
        host_b = _new_worker()
        executor = CountingEDAExecutor()
        host_b.eda_executor = executor
        _connect_duplex(host_a, host_b)

        responses = []
        condition = threading.Condition()
        send_to_a = host_b.send_frame

        def capture(frame_type, payload=b""):
            if frame_type == FrameType.EDA_TASK_FIN:
                with condition:
                    responses.append(json.loads(payload.decode("utf-8")))
                    condition.notify_all()
            send_to_a(frame_type, payload)

        host_b.send_frame = capture
        request_id = str(uuid.uuid4())
        host_a.request_tracker.register(request_id, "eda.execute")

        first = {
            "request_id": request_id,
            "script": "puts one",
            "hint": "same-id.tcl",
            "version": "auto",
            "target_file": "",
            "timeout": 10,
            "keep_open": True,
        }
        second = dict(first, script="puts different")
        host_a.send_frame(
            FrameType.EDA_TASK_SUBMIT, json.dumps(first).encode("utf-8")
        )
        with condition:
            self.assertTrue(condition.wait_for(lambda: len(responses) >= 1, timeout=2.0))
        host_a.send_frame(
            FrameType.EDA_TASK_SUBMIT, json.dumps(second).encode("utf-8")
        )
        with condition:
            self.assertTrue(condition.wait_for(lambda: len(responses) >= 2, timeout=2.0))

        self.assertEqual(executor.calls, 1)
        self.assertEqual(
            responses[1]["error"]["code"], "REQUEST_ID_CONFLICT"
        )
        self.assertEqual(responses[1]["request_state"], "failed")

    def test_duplicate_shell_frame_does_not_start_a_second_process(self):
        class CountingShellEngine:
            def __init__(self):
                self.calls = 0

            def exec(self, **kwargs):
                self.calls += 1
                return {
                    "success": True,
                    "task_id": "shell-deduped",
                    "exit_code": 0,
                    "stdout": "one launch",
                    "stderr": "",
                }

        host_a = _new_worker()
        host_b = _new_worker()
        engine = CountingShellEngine()
        host_b.shell_engine = engine
        _connect_duplex(host_a, host_b)

        responses = []
        condition = threading.Condition()
        send_to_a = host_b.send_frame

        def capture(frame_type, payload=b""):
            if frame_type == FrameType.SHELL_RESULT:
                with condition:
                    responses.append(json.loads(payload.decode("utf-8")))
                    condition.notify_all()
            send_to_a(frame_type, payload)

        host_b.send_frame = capture
        request_id = str(uuid.uuid4())
        host_a.request_tracker.register(request_id, "shell.exec")
        payload = {
            "request_id": request_id,
            "command": "echo one",
            "shell": "cmd",
            "cwd": "",
            "timeout": 10,
            "async": False,
            "env": {},
        }
        frame_payload = json.dumps(payload).encode("utf-8")

        host_a.send_frame(FrameType.SHELL_EXEC, frame_payload)
        with condition:
            self.assertTrue(condition.wait_for(lambda: len(responses) >= 1, timeout=2.0))
        host_a.send_frame(FrameType.SHELL_EXEC, frame_payload)
        with condition:
            self.assertTrue(condition.wait_for(lambda: len(responses) >= 2, timeout=2.0))

        self.assertEqual(engine.calls, 1)
        self.assertEqual(responses[1]["task_id"], "shell-deduped")
        self.assertEqual(responses[1]["request_id"], request_id)

    def test_http_eda_request_id_round_trips_through_both_workers(self):
        class FakeEDAExecutor:
            def execute_script(self, *args, **kwargs):
                return 0, "fake Capture Tcl output"

        host_a = _new_worker()
        host_b = _new_worker()
        host_b.eda_executor = FakeEDAExecutor()
        _connect_duplex(host_a, host_b)

        request_id = str(uuid.uuid4())
        host_a.request_tracker.register(request_id, "eda.execute")
        payload = {
            "request_id": request_id,
            "script": "puts \"fake\"",
            "hint": "ci.tcl",
            "timeout": 10,
        }
        host_a.send_frame(
            FrameType.EDA_TASK_SUBMIT,
            json.dumps(payload).encode("utf-8"),
        )

        result = host_a.request_tracker.wait(request_id, timeout=1.0)
        self.assertEqual(result["state"], "completed", result)
        self.assertEqual(result["result"]["request_id"], request_id)
        self.assertEqual(result["result"]["returncode"], 0)
        self.assertEqual(
            result["result"]["output"], "fake Capture Tcl output"
        )

    def test_http_shell_request_id_round_trips_through_both_workers(self):
        class FakeShellEngine:
            def exec(self, **kwargs):
                return {
                    "success": True,
                    "task_id": "shell-ci",
                    "exit_code": 0,
                    "stdout": "hello from remote shell",
                    "stderr": "",
                }

        host_a = _new_worker()
        host_b = _new_worker()
        host_b.shell_engine = FakeShellEngine()
        _connect_duplex(host_a, host_b)

        request_id = str(uuid.uuid4())
        host_a.request_tracker.register(request_id, "shell.exec")
        payload = {
            "request_id": request_id,
            "command": "Write-Output hello",
            "shell": "powershell",
            "timeout": 10,
            "async": False,
        }
        host_a.send_frame(
            FrameType.SHELL_EXEC,
            json.dumps(payload).encode("utf-8"),
        )

        result = host_a.request_tracker.wait(request_id, timeout=1.0)
        self.assertEqual(result["state"], "completed", result)
        self.assertEqual(result["result"]["request_id"], request_id)
        self.assertTrue(result["result"]["success"])
        self.assertEqual(result["result"]["task_id"], "shell-ci")
        self.assertEqual(result["result"]["stdout"], "hello from remote shell")


if __name__ == "__main__":
    unittest.main()
