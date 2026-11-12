# test_process_supervisor.py
# -*- coding: utf-8 -*-
"""Unit tests for the generic graceful Process Supervisor."""
import unittest

from process_supervisor import (
    ProcessInfo,
    ProcessSupervisor,
    ProcessSupervisorError,
    ProcessSpec,
    ProcessWindow,
)


class FakeProcessBackend:
    def __init__(
        self,
        processes=None,
        windows=None,
        exit_on_wait=None,
        wait_errors=None,
        new_process_on_rescan=None,
    ):
        self.processes = list(processes or [])
        self.windows = dict(windows or {})
        self.exit_on_wait = set(exit_on_wait or [])
        self.wait_errors = dict(wait_errors or {})
        self.new_process_on_rescan = new_process_on_rescan
        self.discover_calls = 0
        self.close_requests = []
        self.wait_calls = []

    def discover_processes(self, image_names):
        self.discover_calls += 1
        if (
            self.new_process_on_rescan is not None
            and self.discover_calls >= 2
            and all(p.pid != self.new_process_on_rescan.pid for p in self.processes)
        ):
            self.processes.append(self.new_process_on_rescan)
            self.windows[self.new_process_on_rescan.pid] = [
                ProcessWindow(7000 + self.new_process_on_rescan.pid, "Capture", "MainWnd", True)
            ]
        expected = {name.lower() for name in image_names}
        return [p for p in self.processes if p.image_name.lower() in expected]

    def list_windows(self, pid):
        return list(self.windows.get(pid, []))

    def request_close(self, process, windows):
        self.close_requests.append((process.pid, tuple(w.hwnd for w in windows)))
        return len(windows), []

    def wait_for_exit(self, pid, timeout_seconds):
        self.wait_calls.append((pid, timeout_seconds))
        if pid in self.wait_errors:
            raise self.wait_errors[pid]
        if pid in self.exit_on_wait:
            self.processes = [p for p in self.processes if p.pid != pid]
            return True
        return False


class ProcessSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.specs = (
            ProcessSpec("capture", ("Capture.exe",), "OrCAD Capture"),
            ProcessSpec("allegro", ("allegro.exe",), "Cadence Allegro"),
        )

    def test_no_existing_process_returns_ready_without_close_requests(self):
        backend = FakeProcessBackend()
        supervisor = ProcessSupervisor(backend=backend, specs=self.specs)

        report = supervisor.ensure_exclusive("capture", timeout_seconds=1)

        self.assertEqual(report.state, "ready")
        self.assertEqual(report.discovered, [])
        self.assertEqual(backend.close_requests, [])
        self.assertEqual(backend.wait_calls, [])

    def test_all_capture_processes_including_manual_instances_are_closed_gracefully(self):
        processes = [
            ProcessInfo(101, "Capture.exe", r"D:\Cadence\SPB_17.2\tools\bin\Capture.exe"),
            ProcessInfo(202, "Capture.exe", r"C:\OrCAD\Capture.exe"),
        ]
        windows = {
            101: [ProcessWindow(1001, "Capture - board A", "MainWnd", True)],
            202: [ProcessWindow(2001, "Capture - board B", "MainWnd", True)],
        }
        backend = FakeProcessBackend(
            processes=processes,
            windows=windows,
            exit_on_wait={101, 202},
        )
        supervisor = ProcessSupervisor(backend=backend, specs=self.specs)

        report = supervisor.ensure_exclusive("capture", timeout_seconds=5)

        self.assertEqual(report.state, "closed_existing")
        self.assertEqual({p.pid for p in report.discovered}, {101, 202})
        self.assertEqual({pid for pid, _ in backend.close_requests}, {101, 202})
        self.assertEqual({pid for pid, _ in backend.wait_calls}, {101, 202})
        self.assertEqual(report.remaining, [])
        self.assertTrue(all(o.status == "exited" for o in report.outcomes))

    def test_existing_modal_save_dialog_is_never_closed_or_acknowledged(self):
        process = ProcessInfo(303, "Capture.exe", "")
        dialog = ProcessWindow(3001, "Save Design?", "#32770", True)
        backend = FakeProcessBackend(
            processes=[process],
            windows={303: [dialog]},
            exit_on_wait=set(),
        )
        supervisor = ProcessSupervisor(backend=backend, specs=self.specs)

        with self.assertRaises(ProcessSupervisorError) as raised:
            supervisor.ensure_exclusive("capture", timeout_seconds=1)

        self.assertEqual(backend.close_requests, [])
        self.assertEqual(backend.wait_calls, [])
        self.assertEqual(raised.exception.report.state, "blocked")
        self.assertEqual(
            raised.exception.report.outcomes[0].status,
            "blocked_by_dialog",
        )
        self.assertIn("Remaining", str(raised.exception))

    def test_process_that_does_not_exit_after_wm_close_blocks_new_runtime(self):
        process = ProcessInfo(404, "Capture.exe", "")
        main = ProcessWindow(4001, "Capture", "MainWnd", True)
        backend = FakeProcessBackend(
            processes=[process],
            windows={404: [main]},
            exit_on_wait=set(),
        )
        supervisor = ProcessSupervisor(backend=backend, specs=self.specs)

        with self.assertRaises(ProcessSupervisorError) as raised:
            supervisor.ensure_exclusive("capture", timeout_seconds=0.1)

        self.assertEqual(backend.close_requests, [(404, (4001,))])
        self.assertEqual(len(backend.wait_calls), 1)
        self.assertEqual(raised.exception.report.remaining[0].pid, 404)
        self.assertIn("did not exit", str(raised.exception))
        # The backend intentionally exposes no terminate()/kill() method.

    def test_process_without_visible_main_window_fails_closed(self):
        process = ProcessInfo(505, "Capture.exe", "")
        hidden = ProcessWindow(5001, "Capture", "MainWnd", False)
        backend = FakeProcessBackend(
            processes=[process],
            windows={505: [hidden]},
            exit_on_wait=set(),
        )
        supervisor = ProcessSupervisor(backend=backend, specs=self.specs)

        with self.assertRaises(ProcessSupervisorError) as raised:
            supervisor.ensure_exclusive("capture", timeout_seconds=1)

        self.assertEqual(backend.close_requests, [])
        self.assertEqual(backend.wait_calls, [])
        self.assertEqual(
            raised.exception.report.outcomes[0].status,
            "blocked_no_main_window",
        )

    def test_process_appearing_during_close_scan_prevents_launch(self):
        first = ProcessInfo(606, "Capture.exe", "")
        second = ProcessInfo(607, "Capture.exe", "")
        backend = FakeProcessBackend(
            processes=[first],
            windows={606: [ProcessWindow(6001, "Capture", "MainWnd", True)]},
            exit_on_wait={606},
            new_process_on_rescan=second,
        )
        supervisor = ProcessSupervisor(backend=backend, specs=self.specs)

        with self.assertRaises(ProcessSupervisorError) as raised:
            supervisor.ensure_exclusive("capture", timeout_seconds=2)

        self.assertEqual(raised.exception.report.state, "blocked")
        self.assertEqual([p.pid for p in raised.exception.report.remaining], [607])

    def test_allegro_profile_uses_its_own_executable_match(self):
        capture = ProcessInfo(701, "Capture.exe", "")
        backend = FakeProcessBackend(processes=[capture])
        supervisor = ProcessSupervisor(backend=backend, specs=self.specs)

        report = supervisor.ensure_exclusive("allegro", timeout_seconds=1)

        self.assertEqual(report.state, "ready")
        self.assertEqual(backend.close_requests, [])

    def test_unknown_profile_and_invalid_timeouts_are_rejected(self):
        supervisor = ProcessSupervisor(
            backend=FakeProcessBackend(),
            specs=self.specs,
        )
        with self.assertRaises(ProcessSupervisorError):
            supervisor.ensure_exclusive("unknown")
        with self.assertRaises(ValueError):
            supervisor.ensure_exclusive("capture", timeout_seconds=0)


if __name__ == "__main__":
    unittest.main()
