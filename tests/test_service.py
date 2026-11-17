"""Worker timing and lifecycle tests; no GUI, media or system actions run."""

from __future__ import annotations

import math
import sqlite3
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication
from PySide6.QtTest import QSignalSpy

from bellplan.scheduler import Scheduler
from bellplan.service import SchedulerService
from bellplan.storage import Store


class ServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.path = Path(self.temporary.name) / "service.sqlite3"
        self.store = Store(self.path)
        self.plan = self.store.save_plan({"name": "工作日节目单"})
        self.services = []

    def tearDown(self):
        for service in self.services:
            self.assertTrue(service.stop(), "worker must stop within its bounded join")
        self.store.close()
        self.app.processEvents()
        self.temporary.cleanup()

    def wait_until(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.005)
        self.app.processEvents()
        return bool(predicate())

    def wait_briefly(self, duration=0.12):
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)

    def make_service(self, **options):
        service = SchedulerService(self.path, interval=0.02, **options)
        self.services.append(service)
        return service

    def start_service(self, service):
        ready = QSignalSpy(service.ready)
        self.assertTrue(service.start())
        self.assertTrue(self.wait_until(lambda: ready.count() == 1), "first poll must complete")

    def program(self, at, identifier="bell"):
        return self.store.save_program(
            {
                "id": identifier,
                "plan_id": self.plan["id"],
                "name": "上课铃",
                "time": at.strftime("%H:%M:%S"),
                "dates": [at.date().isoformat()],
            }
        )

    def test_near_future_event_has_measured_detection_lateness(self):
        at = (datetime.now() + timedelta(seconds=1.2)).replace(microsecond=0)
        program = self.program(at)
        service = self.make_service()
        events = QSignalSpy(service.due)
        self.start_service(service)
        self.assertTrue(self.wait_until(lambda: events.count() == 1))
        event = events.at(0)[0]
        self.assertEqual(event["program"]["id"], program["id"])
        detected = datetime.fromisoformat(event["detected_at"])
        expected_ms = (detected - at).total_seconds() * 1000
        self.assertGreaterEqual(event["lateness_ms"], 0)
        self.assertAlmostEqual(event["lateness_ms"], expected_ms, delta=1)
        self.assertGreater(event["detected_monotonic"], 0)
        self.assertEqual(self.store.list_runs()[0]["status"], "claimed")
        self.wait_briefly()
        self.assertEqual(events.count(), 1)

    def test_other_connection_program_changes_are_visible(self):
        service = self.make_service()
        events = QSignalSpy(service.due)
        self.start_service(service)
        at = (datetime.now() + timedelta(seconds=1.2)).replace(microsecond=0)
        self.program(at, "added-while-running")
        self.assertTrue(self.wait_until(lambda: events.count() == 1))
        self.assertEqual(events.at(0)[0]["program"]["id"], "added-while-running")

    def test_resume_rechecks_recent_window_once_and_ignores_old_occurrences(self):
        service = self.make_service(catchup_seconds=5)
        events = QSignalSpy(service.due)
        self.start_service(service)
        recent = datetime.now() - timedelta(seconds=2)
        self.program(recent, "recent")
        self.program(datetime.now() - timedelta(seconds=40), "outside-window")
        self.wait_briefly()
        self.assertEqual(events.count(), 0)
        service.notify_resume()
        self.assertTrue(self.wait_until(lambda: events.count() == 1))
        self.assertEqual(events.at(0)[0]["program"]["id"], "recent")
        service.notify_resume()
        self.wait_briefly()
        self.assertEqual(events.count(), 1)
        self.assertEqual(len(self.store.list_runs()), 1)

    def test_pause_enable_and_os_resume_do_not_replay_user_paused_tasks(self):
        service = self.make_service(enabled=False)
        events = QSignalSpy(service.due)
        self.start_service(service)
        self.program(datetime.now() - timedelta(seconds=2), "paused")
        service.notify_resume()
        self.wait_briefly()
        self.assertEqual(events.count(), 0)
        service.set_enabled(True)
        service.notify_resume()
        self.wait_briefly()
        self.assertEqual(events.count(), 0)
        self.assertEqual(self.store.list_runs(), [])

    def test_explicit_reset_skips_prior_occurrence_without_claiming(self):
        service = self.make_service()
        events = QSignalSpy(service.due)
        self.start_service(service)
        self.program(datetime.now() - timedelta(seconds=2))
        service.reset()
        self.wait_briefly()
        self.assertEqual(events.count(), 0)
        self.assertEqual(self.store.list_runs(), [])

    def test_start_stop_are_idempotent_and_restart_preserves_claims(self):
        self.program(datetime.now() - timedelta(seconds=1))
        service = self.make_service()
        events = QSignalSpy(service.due)
        self.start_service(service)
        self.assertTrue(self.wait_until(lambda: events.count() == 1))
        self.assertFalse(service.start())
        self.assertTrue(service.is_running)
        self.assertTrue(service.stop())
        self.assertTrue(service.stop())
        self.assertFalse(service.is_running)
        self.start_service(service)
        self.wait_briefly()
        self.assertEqual(events.count(), 1)

    def test_transient_database_lock_is_reported_then_retried(self):
        service = self.make_service()
        events = QSignalSpy(service.due)
        diagnostics = QSignalSpy(service.diagnostic)
        self.start_service(service)
        recent = self.program(datetime.now() - timedelta(seconds=1))
        locker = sqlite3.connect(str(self.path), timeout=0.1)
        try:
            locker.execute("BEGIN IMMEDIATE")
            service.notify_resume()
            self.assertTrue(
                self.wait_until(
                    lambda: any(
                        "锁" in str(diagnostics.at(i)) or "locked" in str(diagnostics.at(i))
                        for i in range(diagnostics.count())
                    ),
                    timeout=2,
                )
            )
            self.assertTrue(service.is_running)
        finally:
            locker.rollback()
            locker.close()
        self.assertTrue(self.wait_until(lambda: events.count() == 1))
        self.assertEqual(events.at(0)[0]["program"]["id"], recent["id"])

    def test_failed_multi_event_batch_does_not_leave_partial_claims(self):
        at = datetime.now() - timedelta(seconds=1)
        self.program(at, "first")
        self.program(at, "second")
        from bellplan import service as service_module

        original = service_module._PollingStore.claim
        fail_once = threading.Event()

        def failing_claim(store, event_id, program_id, scheduled_at):
            if program_id == "second" and not fail_once.is_set():
                fail_once.set()
                raise sqlite3.OperationalError("temporary batch failure")
            return original(store, event_id, program_id, scheduled_at)

        service = self.make_service()
        events = QSignalSpy(service.due)
        with patch.object(service_module._PollingStore, "claim", new=failing_claim):
            self.start_service(service)
            self.assertTrue(self.wait_until(lambda: events.count() == 2))
        self.assertTrue(fail_once.is_set())
        self.assertEqual({events.at(i)[0]["program"]["id"] for i in range(2)}, {"first", "second"})
        self.assertEqual(len(self.store.list_runs()), 2)

    def test_pause_during_poll_cancels_batch_before_delivery(self):
        self.program(datetime.now() - timedelta(seconds=1))
        entered, release = threading.Event(), threading.Event()
        original = Scheduler.poll

        def blocked_poll(scheduler, now=None):
            entered.set()
            release.wait(2)
            return original(scheduler, now)

        service = self.make_service()
        events = QSignalSpy(service.due)
        try:
            with patch.object(Scheduler, "poll", new=blocked_poll):
                service.start()
                self.assertTrue(entered.wait(2))
                service.set_enabled(False)
                release.set()
                self.assertTrue(self.wait_until(lambda: bool(self.store.list_runs())))
                self.wait_briefly()
        finally:
            release.set()
        self.assertEqual(events.count(), 0)
        self.assertEqual(self.store.list_runs()[0]["status"], "cancelled")

    def test_gui_thread_block_does_not_stop_durable_occurrence_claiming(self):
        service = self.make_service()
        events = QSignalSpy(service.due)
        self.start_service(service)
        at = (datetime.now() + timedelta(seconds=1.2)).replace(microsecond=0)
        self.program(at)
        # Deliberately do not process Qt's main event loop over the scheduled
        # instant. The worker must still commit its occurrence independently.
        time.sleep(max(0, (at - datetime.now()).total_seconds()) + 0.15)
        self.assertEqual(len(self.store.list_runs()), 1)
        self.assertTrue(self.wait_until(lambda: events.count() == 1))
        event = events.at(0)[0]
        self.assertLess(event["lateness_ms"], 1000)

    def test_restart_after_pause_controls_retains_floor_and_delivers_new_events(self):
        service = self.make_service()
        events = QSignalSpy(service.due)
        self.start_service(service)
        self.program(datetime.now() - timedelta(seconds=2), "old-paused")
        service.set_enabled(False)
        service.set_enabled(True)
        self.wait_briefly()
        self.assertTrue(service.stop())
        self.start_service(service)
        service.notify_resume()
        self.wait_briefly()
        self.assertEqual(events.count(), 0)
        future = (datetime.now() + timedelta(seconds=1.2)).replace(microsecond=0)
        self.program(future, "new-after-restart")
        self.assertTrue(self.wait_until(lambda: events.count() == 1))
        self.assertEqual(events.at(0)[0]["program"]["id"], "new-after-restart")

    def test_stop_while_initial_database_is_locked_is_bounded(self):
        locker = sqlite3.connect(str(self.path), timeout=0.1)
        locker.execute("BEGIN IMMEDIATE")
        service = self.make_service()
        try:
            service.start()
            time.sleep(0.02)
            started = time.monotonic()
            self.assertTrue(service.stop(timeout=1))
            self.assertLess(time.monotonic() - started, 1)
            self.assertFalse(service.is_running)
        finally:
            locker.rollback()
            locker.close()

    def test_clock_discontinuity_diagnostics_distinguish_rollback_and_stall(self):
        service = self.make_service()
        before = datetime(2026, 11, 17, 8)
        self.assertIsNone(
            service._clock_message(before, 100, before + timedelta(seconds=0.1), 100.1)
        )
        self.assertIn(
            "回拨", service._clock_message(before, 100, before - timedelta(seconds=20), 101)
        )
        self.assertIn(
            "休眠", service._clock_message(before, 100, before + timedelta(seconds=120), 101)
        )
        self.assertIn(
            "停顿", service._clock_message(before, 100, before + timedelta(seconds=10), 110)
        )

    def test_validation_and_no_thread_leak_when_never_started(self):
        for options in (
            {"interval": 0},
            {"interval": math.nan},
            {"stall_threshold": -1},
            {"enabled": 1},
        ):
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    SchedulerService(self.path, **options)
        with self.assertRaises(ValueError):
            SchedulerService(":memory:")
        service = self.make_service()
        self.assertTrue(service.stop())
        with self.assertRaises(ValueError):
            service.set_enabled(1)
        with self.assertRaises(ValueError):
            service.stop(timeout=-1)


if __name__ == "__main__":
    unittest.main()
