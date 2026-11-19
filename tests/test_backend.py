"""Bridge regressions: no graphics, audio hardware or system actions required."""

import json
import sys
import unittest
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QObject, Qt, Signal

from bellplan.backend import Backend, ui_program
from bellplan.domain import active_at, runs_on


class SilentPlayback(QObject):
    changed = Signal()
    log = Signal(str, str)
    runChanged = Signal(str, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.calls = []

    def set_duck_volume(self, value):
        self.duck = value

    def state(self):
        return {"name": "", "status": "stopped", "volume": 80}

    def devices(self):
        return []

    def stop(self):
        pass

    def play(self, program, immediate=False):
        self.calls.append((program, immediate))


class BackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.playback_patch = patch("bellplan.backend.Playback", SilentPlayback)
        self.playback_patch.start()
        self.backend = Backend(Path(self.directory.name))
        self.backend.timer.stop()
        self.plan = self.backend.store.list_plans()[0]
        self.confirm_patch = patch.object(self.backend, "_confirm_action", return_value=True)
        self.confirm_patch.start()

    def tearDown(self):
        self.backend.close()
        self.confirm_patch.stop()
        self.playback_patch.stop()
        self.directory.cleanup()

    def act(self, action, payload=None):
        return json.loads(self.backend.dispatch(action, json.dumps(payload or {})))

    def add(self, **changes):
        payload = {
            "name": "晨间铃声",
            "kind": "audio",
            "time": "08:00:00",
            "plan_id": self.plan["id"],
        }
        payload.update(changes)
        result = self.act("add_program", payload)
        self.assertTrue(result["ok"], result)
        return self.backend.store.list_programs()[-1]

    def test_operation_lock_blocks_dispatch_without_interrupting_scheduling(self):
        program = self.add(time="08:00:00")
        self.assertTrue(
            self.act(
                "security_set_password",
                {"new_password": "test password", "confirmation": "test password"},
            )["ok"]
        )
        self.assertTrue(self.act("security_lock")["ok"])
        before = self.backend.store.export_data()
        with patch("bellplan.backend.QFileDialog.getOpenFileName") as open_dialog:
            for action in (
                "add_program",
                "update_program",
                "delete_program",
                "save_settings",
                "play",
                "stop",
                "import",
                "export",
                "security_set_password",
                "security_remove_password",
                "live_clear",
                "toggle_scheduler",
            ):
                result = self.act(action, {"id": program["id"]})
                self.assertFalse(result["ok"], action)
            open_dialog.assert_not_called()
        self.assertEqual(self.backend.store.export_data(), before)
        self.assertTrue(self.backend.enabled)
        self.assertEqual(len(self.backend.scheduler.poll(datetime(2026, 11, 19, 8))), 1)
        self.assertFalse(self.act("security_unlock", {"password": "wrong"})["ok"])
        self.assertTrue(self.backend.security.locked)
        self.assertTrue(self.act("security_unlock", {"password": "test password"})["ok"])
        self.assertTrue(self.act("delete_program", {"id": program["id"]})["ok"])
        state = json.loads(self.backend.stateJson)
        self.assertEqual(state["security"], {"configured": True, "locked": False})
        self.assertNotIn("test password", self.backend.stateJson)
        self.assertNotIn("test password", (Path(self.directory.name) / "activity.log").read_text())

    def test_crud_and_ui_mapping(self):
        p = self.add(kind="tts", text="早上好", start_date="2026-11-01", end_date="2026-12-31")
        self.assertEqual(p["action"], "speech")
        self.assertEqual(p["date_from"], "2026-11-01")
        ui = json.loads(self.backend.stateJson)["programs"][0]
        self.assertEqual(ui["kind"], "tts")
        self.assertTrue(
            self.act("update_program", {"id": p["id"], "kind": "tts", "name": "晚间铃声"})["ok"]
        )
        self.assertEqual(self.backend.store.get_program(p["id"])["name"], "晚间铃声")
        self.assertTrue(self.act("duplicate_program", {"id": p["id"]})["ok"])
        self.assertEqual(len(self.backend.store.list_programs()), 2)
        self.assertTrue(self.act("delete_program", {"id": p["id"]})["ok"])
        self.assertEqual(len(self.backend.store.list_programs()), 1)

    def test_editor_preserves_effective_validity_bounds(self):
        cases = [
            ({}, ("", "", "", "")),
            ({"date_from": "2026-11-10"}, ("2026-11-10", "", "", "")),
            ({"date_to": "2026-11-20"}, ("", "", "2026-11-20", "")),
            (
                {"datetime_from": "2026-11-10T09:00:00"},
                ("2026-11-10", "2026-11-10T09:00:00", "", ""),
            ),
            (
                {"datetime_to": "2026-11-20T17:00:00"},
                ("", "", "2026-11-20", "2026-11-20T17:00:00"),
            ),
            (
                {
                    "date_from": "2026-11-01",
                    "datetime_from": "2026-11-10T09:00:00",
                    "date_to": "2026-11-30",
                    "datetime_to": "2026-11-20T17:00:00",
                },
                ("2026-11-10", "2026-11-10T09:00:00", "2026-11-20", "2026-11-20T17:00:00"),
            ),
            (
                {
                    "date_from": "2026-11-10",
                    "datetime_from": "2026-11-01T09:00:00",
                    "date_to": "2026-11-20",
                    "datetime_to": "2026-11-30T17:00:00",
                },
                ("2026-11-10", "2026-11-10T00:00:00", "2026-11-20", "2026-11-20T23:59:59"),
            ),
            (
                {
                    "date_from": "2026-11-10",
                    "datetime_from": "2026-11-01T09:00:00",
                    "date_to": "2026-11-30",
                    "datetime_to": "2026-11-20T17:00:00",
                },
                ("2026-11-10", "2026-11-10T00:00:00", "2026-11-20", "2026-11-20T17:00:00"),
            ),
            (
                {
                    "date_from": "2026-11-01",
                    "datetime_from": "2026-11-10T09:00:00",
                    "date_to": "2026-11-20",
                    "datetime_to": "2026-11-30T17:00:00",
                },
                ("2026-11-10", "2026-11-10T09:00:00", "2026-11-20", "2026-11-20T23:59:59"),
            ),
        ]
        samples = {
            datetime(2026, 11, 1) + timedelta(days=day, hours=hour)
            for day in range(31)
            for hour in (0, 9, 17, 23)
        }
        for bounds, expected in cases:
            with self.subTest(bounds=bounds):
                original = self.backend.store.save_program(
                    dict(name="有效期回归", plan_id=self.plan["id"], **bounds)
                )
                ui = ui_program(original)
                self.assertEqual(
                    tuple(
                        ui.get(key, "")
                        for key in ("start_date", "datetime_from", "end_date", "datetime_to")
                    ),
                    expected,
                )
                self.assertEqual(self.backend.store.get_program(original["id"]), original)
                # Match the editor payload: its aliases replace the stored dates,
                # and its clocks reconstruct the projected timestamp boundaries.
                payload = {
                    key: ui.get(key, "")
                    for key in ("id", "start_date", "datetime_from", "end_date", "datetime_to")
                }
                payload["name"] = "仅修改名称"
                result = self.act("update_program", payload)
                self.assertTrue(result["ok"], result)
                saved = self.backend.store.get_program(original["id"])
                boundary_samples = set(samples)
                for stamp in (*bounds.values(), *expected):
                    if stamp:
                        point = datetime.fromisoformat(stamp)
                        boundary_samples.update(
                            point + timedelta(seconds=delta) for delta in (-1, 0, 1)
                        )
                for occurrence in sorted(boundary_samples):
                    before = runs_on(original, occurrence.date()) and active_at(
                        original, occurrence
                    )
                    after = runs_on(saved, occurrence.date()) and active_at(saved, occurrence)
                    self.assertEqual(after, before, (bounds, occurrence))

    def test_command_edit_replaces_actual_arguments(self):
        p = self.add(kind="command", text='["first.exe", "old"]')
        result = self.act(
            "update_program", {"id": p["id"], "kind": "command", "text": '["second.exe", "new"]'}
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.backend.store.get_program(p["id"])["command"], ["second.exe", "new"])

    def test_settings_support_all_calendar_override_forms(self):
        for value in (0, 6, "disabled", "2026-11-15"):
            with self.subTest(value=value):
                result = self.act("save_settings", {"calendar_overrides": {"2026-11-17": value}})
                self.assertTrue(result["ok"], result)
                self.assertEqual(
                    self.backend.store.get_setting("calendar_overrides"), {"2026-11-17": value}
                )

    def test_invalid_holiday_time_rejected_without_partial_save(self):
        before = dict(self.backend.settings)
        result = self.act(
            "save_settings",
            {
                "duckVolume": 55,
                "exclude_windows": [
                    {"start": "2026-11-17", "end": "2026-11-18", "time_from": "nonsense"}
                ],
            },
        )
        self.assertFalse(result["ok"])
        self.assertEqual(self.backend.settings, before)

    def test_resume_discards_paused_occurrences(self):
        now = datetime.now()
        past = now - timedelta(seconds=10)
        self.add(time=past.strftime("%H:%M:%S"))
        self.assertTrue(self.act("set_scheduler", {"enabled": False})["ok"])
        self.assertTrue(self.act("set_scheduler", {"enabled": True})["ok"])
        self.backend.tick()
        self.assertEqual(
            self.backend.player.calls, [], "Resuming must not catch up explicitly paused programs"
        )

    def test_queued_due_is_cancelled_after_pause_then_resume(self):
        class Producer(QObject):
            due = Signal(dict)

        past = datetime.now() - timedelta(seconds=2)
        self.add(time=past.strftime("%H:%M:%S"))
        event = self.backend.scheduler.poll(datetime.now())[0]
        producer = Producer()
        producer.due.connect(self.backend.handle_due, Qt.QueuedConnection)
        producer.due.emit(event)
        self.assertTrue(self.act("set_scheduler", {"enabled": False})["ok"])
        self.assertTrue(self.act("set_scheduler", {"enabled": True})["ok"])
        QCoreApplication.processEvents()
        self.assertEqual(self.backend.player.calls, [])
        self.assertEqual(self.backend.store.list_runs()[0]["status"], "cancelled")

    def test_delivery_held_across_sleep_cannot_bypass_compensation_window(self):
        program = self.add()
        scheduled = datetime.now() - timedelta(minutes=5)
        event_id = "expired-delivery-test"
        self.backend.store.claim(event_id, program["id"], scheduled.isoformat())
        self.backend.handle_due(
            {"program": program, "event_id": event_id, "scheduled_at": scheduled.isoformat()}
        )
        self.assertEqual(self.backend.player.calls, [])
        run = self.backend.store.list_runs()[0]
        self.assertEqual(run["status"], "cancelled")
        self.assertIn("补播窗口", run["message"])

    def test_import_disables_executable_actions(self):
        self.add(kind="command", text='["do-not-run.exe"]')
        self.add(kind="shutdown")
        self.add(kind="audio")
        backup = Path(self.directory.name) / "backup.json"
        backup.write_text(json.dumps(self.backend.store.export_data()), encoding="utf-8")
        with patch(
            "bellplan.backend.QFileDialog.getOpenFileName", return_value=(str(backup), "JSON")
        ):
            result = self.act("import")
        self.assertTrue(result["ok"], result)
        for p in self.backend.store.list_programs():
            self.assertEqual(p["enabled"], p["action"] == "audio")
        self.assertEqual(self.backend.player.calls, [])

    def test_export_import_round_trips_settings(self):
        self.assertTrue(self.act("save_settings", {"duckVolume": 65})["ok"])
        backup = Path(self.directory.name) / "export.json"
        with patch(
            "bellplan.backend.QFileDialog.getSaveFileName", return_value=(str(backup), "JSON")
        ):
            self.assertTrue(self.act("export")["ok"])
        self.assertTrue(self.act("save_settings", {"duckVolume": 20})["ok"])
        with patch(
            "bellplan.backend.QFileDialog.getOpenFileName", return_value=(str(backup), "JSON")
        ):
            self.assertTrue(self.act("import")["ok"])
        self.assertEqual(self.backend.settings["duckVolume"], 65)
        self.assertEqual(self.backend.player.duck, 65)

    def import_backup(self, data, sections=None):
        backup = Path(self.directory.name) / "import.json"
        backup.write_text(json.dumps(data), encoding="utf-8")
        payload = {} if sections is None else {"sections": sections}
        with patch(
            "bellplan.backend.QFileDialog.getOpenFileName", return_value=(str(backup), "JSON")
        ):
            return self.act("import", payload)

    def export_backup(self, sections=None):
        backup = Path(self.directory.name) / "export.json"
        payload = {} if sections is None else {"sections": sections}
        with patch(
            "bellplan.backend.QFileDialog.getSaveFileName", return_value=(str(backup), "JSON")
        ):
            result = self.act("export", payload)
        self.assertTrue(result["ok"], result)
        return json.loads(backup.read_text(encoding="utf-8"))

    def test_holidays_only_import_does_not_modify_other_sections(self):
        self.add(name="Keep existing program")
        self.backend.live.add(["/user/keep.wav"])
        self.assertTrue(
            self.act(
                "save_settings",
                {
                    "duckVolume": 61,
                    "calendar_overrides": {"2026-12-19": "2026-12-21"},
                    "exclude_windows": [{"start": "2026-12-10", "end": "2026-12-10"}],
                },
            )["ok"]
        )
        before = deepcopy(self.backend.store.export_data())
        incoming = deepcopy(before)
        incoming["plans"][0]["name"] = "Do not overwrite this plan"
        incoming["programs"][0]["name"] = "Do not overwrite this program"
        incoming["settings"]["ui"]["duckVolume"] = 999
        incoming["settings"]["live"] = {"files": [], "selectedIndex": 99}
        incoming["settings"]["calendar_overrides"] = {"2026-12-19": "disabled"}
        holiday = {"name": "Imported holiday", "start": "2026-12-24", "end": "2026-12-25"}
        incoming["settings"]["exclude_windows"] = [holiday]
        result = self.import_backup(incoming, ["holidays"])
        self.assertTrue(result["ok"], result)
        after = self.backend.store.export_data()
        self.assertEqual(after["programs"], before["programs"])
        self.assertEqual(after["plans"], before["plans"])
        for key in ("ui", "live", "calendar_overrides"):
            self.assertEqual(after["settings"][key], before["settings"][key])
        self.assertEqual(self.backend.settings["duckVolume"], 61)
        self.assertEqual(self.backend.live.snapshot()["files"], ["/user/keep.wav"])
        self.assertEqual(self.backend.settings["exclude_windows"][0]["name"], "Imported holiday")
        self.assertEqual(self.backend.settings["exclude_windows"][0]["start"], "2026-12-24")

    def test_holidays_only_export_does_not_leak_other_sections(self):
        self.add()
        self.backend.live.add(["/user/private-playlist.wav"])
        self.assertTrue(
            self.act(
                "save_settings",
                {
                    "exclude_windows": [{"start": "2026-12-24", "end": "2026-12-25"}],
                    "calendar_overrides": {"2026-12-19": "disabled"},
                },
            )["ok"]
        )
        exported = self.export_backup(["holidays"])
        self.assertEqual(exported["plans"], [])
        self.assertEqual(exported["programs"], [])
        self.assertEqual(set(exported["settings"]), {"exclude_windows"})
        self.assertEqual(exported["settings"]["exclude_windows"][0]["start"], "2026-12-24")

    def test_corrupt_live_or_ui_backup_is_rejected_before_any_write(self):
        self.add(name="Original name")
        self.backend.live.add(["/user/original.wav"])
        self.assertTrue(self.act("save_settings", {"duckVolume": 44})["ok"])
        before = deepcopy(self.backend.store.export_data())
        before_ui, before_live = deepcopy(self.backend.settings), self.backend.live.snapshot()
        for key, broken in (
            ("live", {"files": ["duplicate.wav", "duplicate.wav"], "selectedIndex": 0}),
            ("live", {"files": ["/incoming/file.wav"], "selectedIndex": 99}),
            ("ui", {"duckVolume": 101}),
            ("ui", {"closeToTray": "yes"}),
        ):
            with self.subTest(key=key, broken=broken):
                incoming = deepcopy(before)
                incoming["plans"][0]["name"] = "Changed plan must roll back"
                incoming["programs"][0]["name"] = "Changed name must roll back"
                incoming["settings"][key] = broken
                incoming["settings"]["exclude_windows"] = [
                    {"start": "2026-12-25", "end": "2026-12-25"}
                ]
                result = self.import_backup(incoming)
                self.assertFalse(result["ok"], result)
                self.assertEqual(self.backend.store.export_data(), before)
                self.assertEqual(self.backend.settings, before_ui)
                self.assertEqual(self.backend.live.snapshot(), before_live)
                self.assertEqual(self.backend.player.calls, [])

    def test_builtin_sources_stay_portable_in_storage_and_backup(self):
        media = self.backend.builtin_media[0]
        absolute, portable = media["path"], "builtin://" + media["relative_path"]
        p = self.add(name="Bundled bell", files=[absolute])
        self.assertEqual(p["files"], [portable])
        ui = json.loads(self.backend.stateJson)["programs"][0]
        self.assertEqual(ui["files"], [absolute])
        self.assertTrue(Path(ui["files"][0]).is_absolute())
        self.assertTrue(self.act("play_program", {"id": p["id"]})["ok"])
        played, immediate = self.backend.player.calls[-1]
        self.assertEqual(played["files"], [absolute])
        self.assertTrue(immediate)
        exported = self.export_backup(["programs"])
        self.assertEqual(exported["programs"][0]["files"], [portable])
        self.assertNotIn(absolute, json.dumps(exported, ensure_ascii=False))
        self.backend.store.delete_program(p["id"])
        self.assertTrue(self.import_backup(exported)["ok"])
        self.assertEqual(self.backend.store.get_program(p["id"])["files"], [portable])
        self.assertEqual(json.loads(self.backend.stateJson)["programs"][0]["files"], [absolute])

    def test_live_builtin_configuration_and_playback_use_independent_channel(self):
        entries = self.backend.builtin_media[:2]
        paths = [entry["path"] for entry in entries]
        uris = ["builtin://" + entry["relative_path"] for entry in entries]
        self.assertTrue(self.act("live_add_builtin", {"paths": paths})["ok"])
        self.assertEqual(self.backend.live.snapshot()["files"], uris)
        self.assertEqual(self.backend.store.get_setting("live")["files"], uris)
        self.assertEqual(json.loads(self.backend.stateJson)["live"]["files"], paths)
        settings = {
            "mode": "single",
            "volume": 52,
            "stop_mode": "tracks",
            "track_limit": 3,
            "repeat": 0,
            "selectedIndex": 1,
        }
        self.assertTrue(self.act("live_save_settings", settings)["ok"])
        self.assertTrue(self.act("live_play", {"index": 1})["ok"])
        program, immediate = self.backend.player.calls[-1]
        self.assertTrue(immediate)
        self.assertEqual(program["files"], [paths[1]])
        self.assertEqual(program["volume"], 52)
        self.assertEqual(program["stop_mode"], "tracks")
        self.assertEqual(program["track_limit"], 3)
        self.assertEqual(self.backend.store.list_programs(), [])
        self.assertEqual(self.backend.store.list_runs(), [])
        exported = self.export_backup(["live"])
        self.assertEqual(exported["settings"]["live"]["files"], uris)
        self.assertTrue(self.act("live_clear")["ok"])
        self.assertTrue(self.import_backup(exported)["ok"])
        self.assertEqual(self.backend.live.snapshot()["files"], uris)
        self.assertEqual(self.backend.live.snapshot()["volume"], 52)

    def test_unknown_imported_builtin_reference_is_rejected_atomically(self):
        original = self.add(name="Original program")
        self.backend.live.add(["/user/keep.wav"])
        before = deepcopy(self.backend.store.export_data())
        incoming = deepcopy(before)
        incoming["programs"][0]["name"] = "Must not replace original"
        incoming["programs"][0]["files"] = ["builtin://unknown/missing.mp3"]
        result = self.import_backup(incoming)
        self.assertFalse(result["ok"], result)
        self.assertEqual(self.backend.store.export_data(), before)
        self.assertEqual(self.backend.store.get_program(original["id"])["name"], "Original program")
        self.assertEqual(self.backend.player.calls, [])

    def test_imported_login_startup_preference_never_changes_registration(self):
        incoming = self.backend.store.export_data()
        incoming["settings"]["ui"] = {"startAtLogin": True, "duckVolume": 35}
        with patch.object(self.backend, "_set_startup") as register:
            result = self.import_backup(incoming, ["settings"])
        self.assertTrue(result["ok"], result)
        register.assert_not_called()
        self.assertFalse(self.backend.settings["startAtLogin"])
        self.assertEqual(self.backend.settings["duckVolume"], 35)

    def test_invalid_builtin_reference_does_not_change_programs_or_live(self):
        before_live = self.backend.live.snapshot()
        result = self.act(
            "add_program", {"name": "Invalid source", "files": ["builtin://../outside.wav"]}
        )
        self.assertFalse(result["ok"])
        self.assertEqual(self.backend.store.list_programs(), [])
        result = self.act("live_add_builtin", {"paths": ["/unlisted/bell.wav"]})
        self.assertFalse(result["ok"])
        self.assertEqual(self.backend.live.snapshot(), before_live)

    def test_scheduled_run_changes_only_on_playback_receipts(self):
        past = datetime.now() - timedelta(seconds=2)
        p = self.add(time=past.strftime("%H:%M:%S"))
        self.backend.tick()
        runs = self.backend.store.list_runs()
        self.assertEqual(len(runs), 1)
        event_id = runs[0]["event_id"]
        self.assertEqual(runs[0]["program_id"], p["id"])
        self.assertEqual(runs[0]["status"], "claimed")
        program, immediate = self.backend.player.calls[0]
        self.assertFalse(immediate)
        self.assertEqual(program["_run_event_id"], event_id)
        self.backend.player.runChanged.emit(event_id, "started", "Audio started")
        self.assertEqual(self.backend.store.list_runs()[0]["status"], "started")
        self.backend.player.runChanged.emit(event_id, "completed", "Audio ended")
        self.assertEqual(self.backend.store.list_runs()[0]["status"], "completed")
        self.assertEqual(self.backend.store.list_runs()[0]["message"], "Audio ended")
        self.backend.tick()
        self.assertEqual(
            len(self.backend.player.calls), 1, "An acknowledged occurrence cannot replay"
        )

    def test_failed_playback_receipt_is_persisted_and_logged(self):
        past = datetime.now() - timedelta(seconds=2)
        self.add(time=past.strftime("%H:%M:%S"))
        self.backend.tick()
        event_id = self.backend.store.list_runs()[0]["event_id"]
        self.backend.player.runChanged.emit(event_id, "failed", "Output device unavailable")
        run = self.backend.store.list_runs()[0]
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["message"], "Output device unavailable")
        # Playback logs are published in batches to keep the event loop responsive.
        self.backend.refresh()
        self.assertEqual(json.loads(self.backend.stateJson)["logs"][0]["level"], "error")

    def test_close_stops_all_timers_and_is_idempotent(self):
        self.backend.request_refresh()
        self.assertTrue(self.backend.publish_timer.isActive())
        self.backend.close()
        self.assertTrue(self.backend.closed)
        self.assertFalse(self.backend.timer.isActive())
        self.assertFalse(self.backend.publish_timer.isActive())
        self.assertFalse(self.backend.power_timer.isActive())
        self.backend.request_refresh()
        self.assertFalse(self.backend.publish_timer.isActive())
        self.backend.close()

    def test_invalid_action_is_reported(self):
        result = self.act("made_up")
        self.assertFalse(result["ok"])
        self.assertTrue(result["error"])
        self.assertEqual(json.loads(self.backend.stateJson)["logs"][0]["level"], "error")

    def test_nonempty_plan_cannot_be_deleted(self):
        self.add()
        self.assertTrue(self.act("add_plan", {"name": "Empty spare plan"})["ok"])
        result = self.act("delete_plan", {"id": self.plan["id"]})
        self.assertFalse(result["ok"])
        self.assertEqual(len(self.backend.store.list_programs()), 1)
        self.assertEqual(len(self.backend.store.list_plans()), 2)

    def test_declining_action_does_not_save_program(self):
        with patch.object(self.backend, "_confirm_action", return_value=False):
            result = self.act("add_program", {"name": "Power task", "kind": "shutdown"})
        self.assertFalse(result["ok"])
        self.assertTrue(result["cancelled"])
        self.assertEqual(self.backend.store.list_programs(), [])

    def test_close_preserves_external_process_without_late_database_callbacks(self):
        gate = Path(self.directory.name) / "release-child"
        script = (
            "import pathlib,sys,time; gate=pathlib.Path(sys.argv[1]); "
            "deadline=time.monotonic()+15; "
            "exec('while not gate.exists() and time.monotonic()<deadline: time.sleep(0.01)')"
        )
        event_id = "external-close-test"
        self.backend.store.claim(event_id, "command-test", datetime.now().isoformat())
        self.backend._execute(
            {
                "action": "command",
                "name": "测试外部程序",
                "command": [sys.executable, "-c", script, str(gate)],
                "_run_event_id": event_id,
            }
        )
        process = self.backend.commands._running[0]["process"]
        try:
            self.backend.close()
            self.assertIsNone(process.poll())
            self.assertFalse(self.backend.commands.timer.isActive())
            self.backend.commands.runChanged.emit(event_id, "completed", "late signal")
            QCoreApplication.processEvents()
        finally:
            gate.touch()
            process.wait(timeout=10)
        from bellplan.storage import Store

        store = Store(Path(self.directory.name) / "bellplan.sqlite3")
        try:
            run = store.list_runs()[0]
            self.assertEqual(run["status"], "started")
            self.assertIn("不再跟踪", run["message"])
        finally:
            store.close()

    def test_close_records_audio_cancellation_before_closing_storage(self):
        event_id = "audio-close-test"
        self.backend.store.claim(event_id, "audio-test", datetime.now().isoformat())
        self.backend.player.stop = lambda: self.backend.player.runChanged.emit(
            event_id, "cancelled", "程序退出"
        )
        self.backend.close()
        from bellplan.storage import Store

        store = Store(Path(self.directory.name) / "bellplan.sqlite3")
        try:
            self.assertEqual(store.list_runs()[0]["status"], "cancelled")
        finally:
            store.close()

    def test_last_plan_cannot_be_deleted(self):
        result = self.act("delete_plan", {"id": self.plan["id"]})
        self.assertFalse(result["ok"])
        self.assertEqual(len(self.backend.store.list_plans()), 1)


if __name__ == "__main__":
    unittest.main()
