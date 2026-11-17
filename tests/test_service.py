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
        time.sleep(max(0, (at - datetime.now()).total_seconds()))
        deadline = time.monotonic() + 2
        while not self.store.list_runs() and time.monotonic() < deadline:
            # WAL commit/fsync and OS thread scheduling can exceed a nominal
            # polling interval, particularly on Windows CI. Keep the GUI event
            # loop unprocessed while allowing a bounded eventual claim.
            time.sleep(0.01)
        self.assertEqual(
            len(self.store.list_runs()),
            1,
            "the worker must claim without the GUI event loop, including slow storage",
        )
        self.assertTrue(self.wait_until(lambda: events.count() == 1))
        event = events.at(0)[0]
        self.assertGreaterEqual(event["lateness_ms"], 0)
        self.assertAlmostEqual(
            event["lateness_ms"],
            (datetime.fromisoformat(event["detected_at"]) - at).total_seconds() * 1000,
            delta=1,
        )

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


class PollingCacheTests(unittest.TestCase):
    def setUp(self):
        from bellplan.service import _PollingStore

        self.temporary = TemporaryDirectory()
        self.path = Path(self.temporary.name) / "cached.sqlite3"
        self.store = Store(self.path)
        self.plan = self.store.save_plan({"name": "默认节目单"})
        self.now = datetime(2026, 11, 17, 8, 0, 1)
        self.program = self.store.save_program(
            {
                "id": "bell",
                "plan_id": self.plan["id"],
                "name": "上课铃",
                "time": "08:00:00",
                "dates": ["2026-11-17"],
                "files": ["bell.wav"],
            }
        )
        self.worker = _PollingStore(self.path)
        self.queries = []
        self.worker.connection.set_trace_callback(self.queries.append)

    def tearDown(self):
        self.worker.close()
        self.store.close()
        self.temporary.cleanup()

    def begin(self):
        self.worker.connection.execute("BEGIN")
        self.worker.begin_poll()

    def end(self):
        self.worker.connection.commit()
        self.worker.end_poll()

    def poll(self):
        self.begin()
        try:
            result = Scheduler(self.worker).poll(self.now)
            self.worker.connection.commit()
            return result
        except Exception:
            self.worker.connection.rollback()
            raise
        finally:
            self.worker.end_poll()

    def count(self, fragment):
        return sum(fragment in query for query in self.queries)

    def load_all(self):
        self.begin()
        try:
            programs = self.worker.list_programs()
            plans = self.worker.list_plans()
            settings = self.worker.get_setting("calendar_overrides", {})
            # Multiple consumers in one cycle must not resample the version.
            self.worker.list_programs()
            self.worker.list_plans()
            self.worker.get_setting("calendar_overrides", {})
        finally:
            self.end()
        return programs, plans, settings

    def test_loaders_run_once_for_unchanged_data_and_version_once_per_cycle(self):
        first = self.load_all()
        second = self.load_all()
        third = self.load_all()
        self.assertIs(first[0], second[0])
        self.assertIs(first[1], third[1])
        self.assertEqual(self.count("SELECT body FROM programs"), 1)
        self.assertEqual(self.count("SELECT body FROM plans"), 1)
        self.assertEqual(self.count("SELECT value FROM metadata"), 1)
        self.assertEqual(self.count("PRAGMA data_version"), 3)

    def test_external_program_edit_delete_and_plan_change_invalidate_next_cycle(self):
        first, _, _ = self.load_all()
        self.store.save_program(dict(self.program, name="新名称"))
        edited, _, _ = self.load_all()
        self.assertIsNot(first, edited)
        self.assertEqual(edited[0]["name"], "新名称")
        self.store.save_plan(dict(self.plan, enabled=False))
        _, plans, _ = self.load_all()
        self.assertFalse(plans[0]["enabled"])
        self.store.delete_program(self.program["id"])
        deleted, _, _ = self.load_all()
        self.assertEqual(deleted, ())
        self.assertEqual(self.count("SELECT body FROM programs"), 4)

    def test_external_setting_commit_is_visible_without_restart(self):
        self.load_all()
        self.store.set_setting("calendar_overrides", {"2026-11-17": "disabled"})
        _, _, calendar = self.load_all()
        self.assertEqual(calendar["2026-11-17"], "disabled")
        self.assertEqual(self.count("SELECT value FROM metadata"), 2)

    def test_own_run_claim_and_status_commit_do_not_reload_configuration(self):
        first = self.load_all()
        self.begin()
        self.assertTrue(self.worker.claim("event-one", self.program["id"], self.now.isoformat()))
        self.end()
        self.worker.finish_run("event-one", "completed", "完成")
        second = self.load_all()
        self.assertIs(first[0], second[0])
        self.assertIs(first[1], second[1])
        self.assertEqual(self.count("SELECT body FROM programs"), 1)
        self.assertEqual(self.count("SELECT body FROM plans"), 1)
        self.assertEqual(self.count("SELECT value FROM metadata"), 1)

    def test_cached_records_are_deeply_read_only_and_due_copy_is_detached(self):
        from bellplan.service import _thaw

        programs, _, _ = self.load_all()
        with self.assertRaises(TypeError):
            programs[0]["name"] = "不能写入"
        with self.assertRaises(AttributeError):
            programs[0]["files"].append("unexpected.wav")
        detached = _thaw(programs[0])
        self.assertIsInstance(detached, dict)
        self.assertIsInstance(detached["files"], list)
        detached["name"] = "接收者自己的副本"
        detached["files"].append("receiver.wav")
        again, _, _ = self.load_all()
        self.assertEqual(again[0]["name"], "上课铃")
        self.assertEqual(again[0]["files"], ("bell.wav",))

    def test_missing_settings_keep_each_callers_default_without_reloading(self):
        self.begin()
        first = self.worker.get_setting("not-present", {"mode": 1})
        second = self.worker.get_setting("not-present", {"mode": 2})
        self.end()
        self.assertEqual(first["mode"], 1)
        self.assertEqual(second["mode"], 2)
        self.assertEqual(self.count("SELECT value FROM metadata"), 1)
        with self.assertRaises(TypeError):
            first["mode"] = 3

    def test_begin_poll_requires_transaction_for_snapshot_safety(self):
        with self.assertRaises(RuntimeError):
            self.worker.begin_poll()

    def test_commit_after_version_sampling_cannot_claim_using_stale_rules(self):
        self.load_all()
        self.begin()
        self.store.save_program(dict(self.program, enabled=False))
        # PRAGMA data_version fixed the read snapshot before the external
        # commit. Upgrading it to a writer must fail, rather than claim using
        # cached rules that are older than the new committed configuration.
        try:
            with self.assertRaises(sqlite3.OperationalError):
                Scheduler(self.worker).poll(self.now)
        finally:
            self.worker.connection.rollback()
            self.worker.end_poll()
        self.assertEqual(self.store.list_runs(), [])
        self.assertEqual(self.poll(), [])
        self.assertFalse(self.worker.list_programs()[0]["enabled"])

    def test_holiday_calendar_and_plan_disables_apply_to_next_poll(self):
        self.load_all()
        self.store.save_plan(dict(self.plan, enabled=False))
        self.assertEqual(self.poll(), [])
        self.store.save_plan(dict(self.plan, enabled=True))
        self.store.set_setting(
            "exclude_windows",
            [{"start": "2026-11-17", "end": "2026-11-17", "enabled": True}],
        )
        self.assertEqual(self.poll(), [])
        self.store.set_setting("exclude_windows", [])
        self.store.set_setting("calendar_overrides", {"2026-11-17": "disabled"})
        self.assertEqual(self.poll(), [])
        self.store.set_setting("calendar_details", {"2026-11-17": {"enabled": False}})
        events = self.poll()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["program"]["id"], self.program["id"])


if __name__ == "__main__":
    unittest.main()
