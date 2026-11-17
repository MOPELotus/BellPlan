"""Domain and scheduling regressions; runnable with unittest, no GUI required."""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from bellplan.domain import validate_program
from bellplan.scheduler import Scheduler
from bellplan.storage import Store


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "schedule.sqlite3"
        self.store = Store(self.path)
        self.plan = self.store.save_plan({"name": "校园作息"})
        self.program = self.store.save_program(
            {"plan_id": self.plan["id"], "name": "上课铃", "time": "08:00"}
        )

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def at(self, stamp):
        return datetime.fromisoformat(stamp)

    def test_catchup_and_durable_claim_across_restart(self):
        scheduler = Scheduler(self.store)
        now = self.at("2026-11-17T08:00:30")
        self.assertEqual(len(scheduler.poll(now)), 1)
        self.assertEqual(scheduler.poll(now), [])
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(Scheduler(self.store).poll(now), [])
        self.assertEqual(self.store.list_runs()[0]["status"], "claimed")

    def test_clock_rollback_does_not_replay(self):
        scheduler = Scheduler(self.store)
        self.assertEqual(len(scheduler.poll(self.at("2026-11-17T08:00:00"))), 1)
        self.assertEqual(scheduler.poll(self.at("2026-11-17T07:59:59")), [])
        self.assertEqual(scheduler.poll(self.at("2026-11-17T08:00:00")), [])

    def test_no_unbounded_catchup_after_suspend(self):
        scheduler = Scheduler(self.store, catchup_seconds=10)
        scheduler.poll(self.at("2026-11-17T07:00:00"))
        self.assertEqual(scheduler.poll(self.at("2026-11-17T09:00:00")), [])

    def test_midnight_catchup_and_exact_boundary(self):
        self.program["time"] = "23:59:30"
        self.store.save_program(self.program)
        self.assertEqual(len(Scheduler(self.store, 90).poll(self.at("2026-11-18T00:01:00"))), 1)
        self.assertEqual(len(Scheduler(self.store, 90).poll(self.at("2026-11-19T00:01:01"))), 0)

    def test_weekday_and_date_rules(self):
        self.program.update(weekdays=[0], dates=["2026-11-17"], excluded_dates=["2026-11-18"])
        self.store.save_program(self.program)
        scheduler = Scheduler(self.store)
        self.assertEqual(len(scheduler.poll(self.at("2026-11-17T08:00:00"))), 1)
        self.assertEqual(scheduler.poll(self.at("2026-11-18T08:00:00")), [])
        self.assertEqual(scheduler.poll(self.at("2026-11-23T08:00:00")), [])

    def test_plan_enable_and_program_disable(self):
        self.plan["enabled"] = False
        self.store.save_plan(self.plan)
        scheduler = Scheduler(self.store)
        self.assertEqual(scheduler.poll(self.at("2026-11-17T08:00:00")), [])
        self.plan["enabled"] = True
        self.store.save_plan(self.plan)
        self.program["enabled"] = False
        self.store.save_program(self.program)
        self.assertEqual(scheduler.poll(self.at("2026-11-18T08:00:00")), [])

    def test_multiple_times_and_priority(self):
        self.program.update(times=["08:00", "08:00:00", "08:01"], priority=0)
        self.store.save_program(self.program)
        other = self.store.save_program(
            {"plan_id": self.plan["id"], "name": "紧急通知", "priority": 10}
        )
        events = Scheduler(self.store, 90).poll(self.at("2026-11-17T08:01:00"))
        self.assertEqual(
            [x["program"]["id"] for x in events],
            [other["id"], self.program["id"], self.program["id"]],
        )

    def test_atomic_import_preserves_state_on_invalid_data(self):
        backup = self.store.export_data()
        backup["programs"].append({"name": "损坏的数据", "plan_id": "missing"})
        with self.assertRaises(ValueError):
            self.store.import_data(backup, replace=True)
        self.assertEqual(self.store.list_programs(), [self.program])
        self.assertEqual(self.store.list_plans(), [self.plan])

    def test_import_roundtrip_preserves_all_metadata(self):
        self.program.update(
            action="command",
            command=["echo", "hello"],
            repeat=8,
            duration=45,
            shuffle=True,
            fade_in=1.5,
            fade_out=2,
            output_device="USB DAC",
            playback_mode="queue",
            month_days=[17, 18],
            exclude_windows=[{"start": "2026-12-01", "end": "2026-12-08"}],
        )
        self.program = self.store.save_program(self.program)
        backup = self.store.export_data()
        self.store.import_data(backup, replace=True)
        self.assertEqual(self.store.list_programs(), [self.program])

    def test_backup_preserves_settings_and_calendar_rules(self):
        calendar = {"2026-11-17": 0, "2026-11-18": "disabled"}
        self.store.set_setting("calendar_overrides", calendar)
        backup = self.store.export_data()
        self.store.set_setting("calendar_overrides", {})
        self.store.import_data(backup, replace=True)
        self.assertEqual(self.store.get_setting("calendar_overrides"), calendar)

    def test_duplicate_and_delete_cascade(self):
        copy = self.store.duplicate_program(self.program["id"])
        self.assertNotEqual(copy["id"], self.program["id"])
        self.store.delete_program(copy["id"])
        self.assertEqual(len(self.store.list_programs()), 1)
        self.store.delete_plan(self.plan["id"])
        self.assertEqual(self.store.list_programs(), [])

    def test_more_than_free_edition_limit(self):
        backup = self.store.export_data()
        backup["programs"] = [
            dict(self.program, id=f"program-{i}", name=f"节目 {i}") for i in range(1500)
        ]
        self.store.import_data(backup, replace=True)
        self.assertEqual(len(self.store.list_programs()), 1500)
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(len(self.store.list_programs()), 1500)

    def test_month_days_and_calendar_override(self):
        self.program.update(month_days=[17], weekdays=[0])
        self.store.save_program(self.program)
        self.store.set_setting("calendar_overrides", {"2026-11-17": 0, "2026-12-17": "disabled"})
        scheduler = Scheduler(self.store)
        self.assertEqual(len(scheduler.poll(self.at("2026-11-17T08:00:00"))), 1)
        self.assertEqual(scheduler.poll(self.at("2026-12-17T08:00:00")), [])

    def test_exclude_time_window_including_overnight(self):
        self.program.update(
            times=["07:00", "08:00", "23:00"],
            exclude_windows=[
                {
                    "start": "2026-11-17",
                    "end": "2026-11-17",
                    "time_from": "22:00",
                    "time_to": "07:30",
                }
            ],
        )
        self.store.save_program(self.program)
        scheduler = Scheduler(self.store)
        self.assertEqual(scheduler.poll(self.at("2026-11-17T07:00:00")), [])
        self.assertEqual(len(scheduler.poll(self.at("2026-11-17T08:00:00"))), 1)
        self.assertEqual(scheduler.poll(self.at("2026-11-17T23:00:00")), [])

    def test_preview_has_no_side_effects_and_matches_rules(self):
        preview = Scheduler(self.store).next_occurrences(self.at("2026-11-17T07:00:00"), days=2)
        self.assertEqual(len(preview), 2)
        self.assertEqual(self.store.list_runs(), [])
        self.assertEqual(preview[0]["scheduled_at"], "2026-11-17T08:00:00")

    def test_aware_datetime_and_mixed_timezone_error(self):
        scheduler = Scheduler(self.store)
        self.assertEqual(len(scheduler.poll(datetime(2026, 11, 17, 8, tzinfo=timezone.utc))), 1)
        with self.assertRaises(ValueError):
            scheduler.poll(datetime(2026, 11, 17, 8, 1))

    def test_global_exclusion_applies_to_poll_and_preview(self):
        self.store.set_setting(
            "exclude_windows",
            [
                {
                    "start": "2026-11-17",
                    "end": "2026-11-17",
                    "time_from": "07:00",
                    "time_to": "09:00",
                }
            ],
        )
        scheduler = Scheduler(self.store)
        self.assertEqual(scheduler.poll(self.at("2026-11-17T08:00:00")), [])
        self.assertEqual(scheduler.next_occurrences(self.at("2026-11-17T07:00:00"), days=1), [])
        self.assertEqual(len(scheduler.poll(self.at("2026-11-18T08:00:00"))), 1)

    def test_global_and_program_exclusions_are_additive(self):
        self.program.update(
            times=["07:00", "08:00", "09:00"],
            exclude_windows=[
                {
                    "start": "2026-11-17",
                    "end": "2026-11-17",
                    "time_from": "07:00",
                    "time_to": "07:30",
                }
            ],
        )
        self.store.save_program(self.program)
        self.store.set_setting(
            "exclude_windows",
            [
                {
                    "start": "2026-11-17",
                    "end": "2026-11-17",
                    "time_from": "08:00",
                    "time_to": "08:30",
                }
            ],
        )
        scheduler = Scheduler(self.store)
        preview = scheduler.next_occurrences(self.at("2026-11-17T06:00:00"), days=1)
        self.assertEqual([x["scheduled_at"] for x in preview], ["2026-11-17T09:00:00"])
        self.assertEqual(scheduler.poll(self.at("2026-11-17T07:00:00")), [])
        self.assertEqual(scheduler.poll(self.at("2026-11-17T08:00:00")), [])
        self.assertEqual(len(scheduler.poll(self.at("2026-11-17T09:00:00"))), 1)

    def test_date_mapping_uses_target_program_pattern(self):
        # Tuesday November 17 follows Monday November 16's program.
        self.program.update(
            weekdays=[0],
            month_days=[16],
            dates=["2026-11-16"],
            date_from="2026-11-17",
            date_to="2026-11-17",
        )
        self.store.save_program(self.program)
        self.store.set_setting("calendar_overrides", {"2026-11-17": "2026-11-16"})
        scheduler = Scheduler(self.store)
        preview = scheduler.next_occurrences(self.at("2026-11-17T07:00:00"), days=1)
        self.assertEqual(len(preview), 1)
        self.assertEqual(preview[0]["scheduled_at"], "2026-11-17T08:00:00")
        self.assertEqual(len(scheduler.poll(self.at("2026-11-17T08:00:00"))), 1)

    def test_date_mapping_still_checks_real_validity_and_exclusions(self):
        self.program.update(dates=["2026-11-16"], excluded_dates=["2026-11-17"])
        self.store.save_program(self.program)
        self.store.set_setting("calendar_overrides", {"2026-11-17": "2026-11-16"})
        scheduler = Scheduler(self.store)
        self.assertEqual(scheduler.poll(self.at("2026-11-17T08:00:00")), [])
        self.program.update(excluded_dates=[], date_to="2026-11-16")
        self.store.save_program(self.program)
        self.assertEqual(scheduler.poll(self.at("2026-11-17T08:00:00")), [])

    def test_date_mapping_uses_target_weekday(self):
        self.program.update(weekdays=[0])
        self.store.save_program(self.program)
        self.store.set_setting("calendar_overrides", {"2026-11-17": "2026-11-16"})
        self.assertEqual(len(Scheduler(self.store).poll(self.at("2026-11-17T08:00:00"))), 1)

    def test_reset_discards_paused_occurrences_and_keeps_next_events(self):
        self.program.update(times=["08:00", "08:00:20", "08:00:40"])
        self.store.save_program(self.program)
        scheduler = Scheduler(self.store)
        scheduler.poll(self.at("2026-11-17T07:59:59"))
        scheduler.reset(self.at("2026-11-17T08:00:25"))
        self.assertEqual(scheduler.poll(self.at("2026-11-17T08:00:26")), [])
        self.assertEqual(len(scheduler.poll(self.at("2026-11-17T08:00:40"))), 1)

    def test_default_catchup_window_is_30_seconds(self):
        self.assertEqual(Scheduler(self.store).poll(self.at("2026-11-17T08:00:31")), [])
        self.assertEqual(len(Scheduler(self.store).poll(self.at("2026-11-17T08:00:30"))), 1)

    def test_invalid_calendar_settings_are_atomic(self):
        for value in (
            {"2026-11-17": 7},
            {"bad-date": 0},
            {"2026-11-17": True},
            {"2026-11-17": ""},
            {"2026-11-17": "2026-13-01"},
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.set_setting("calendar_overrides", value)
        self.assertIsNone(self.store.get_setting("calendar_overrides"))
        backup = self.store.export_data()
        backup["settings"] = {"exclude_windows": [{"start": "bad", "end": "date"}]}
        with self.assertRaises(ValueError):
            self.store.import_data(backup, replace=True)
        self.assertEqual(self.store.list_programs(), [self.program])

    def test_stop_mode_and_track_limit_roundtrip(self):
        self.program.update(stop_mode="tracks", track_limit=120)
        saved = self.store.save_program(self.program)
        self.store.import_data(self.store.export_data(), replace=True)
        self.assertEqual(self.store.get_program(saved["id"])["stop_mode"], "tracks")
        self.assertEqual(self.store.get_program(saved["id"])["track_limit"], 120)
        self.assertEqual(validate_program(dict(saved, track_limit=0))["track_limit"], 0)
        for change in (
            {"stop_mode": "unknown"},
            {"track_limit": -1},
            {"track_limit": 1.5},
            {"track_limit": True},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_program(dict(saved, **change))
        backup = self.store.export_data()
        backup["programs"][0]["stop_mode"] = "unknown"
        with self.assertRaises(ValueError):
            self.store.import_data(backup, replace=True)
        self.assertEqual(self.store.get_program(saved["id"]), saved)

    def test_program_order_is_stable_and_move_normalizes_equal_ranks(self):
        self.store.save_program(dict(self.program, id="second", name="第二个"))
        self.store.save_program(dict(self.program, id="third", name="第三个"))
        self.assertEqual(
            [x["id"] for x in self.store.list_programs()], [self.program["id"], "second", "third"]
        )
        self.store.move_program("second", -1)
        self.assertEqual(
            [x["id"] for x in self.store.list_programs()], ["second", self.program["id"], "third"]
        )
        self.assertEqual([x["sort_order"] for x in self.store.list_programs()], [0, 1, 2])
        self.store.move_program("second", 1)
        self.store.move_program("third", 1)  # Last entry stays in place.
        self.store.move_program(self.program["id"], -1)  # First entry stays in place.
        self.assertEqual(
            [x["id"] for x in self.store.list_programs()], [self.program["id"], "second", "third"]
        )
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(
            [x["id"] for x in self.store.list_programs()], [self.program["id"], "second", "third"]
        )

    def test_move_and_rank_are_isolated_to_one_plan(self):
        other_plan = self.store.save_plan({"name": "另一个计划"})
        other = self.store.save_program(
            dict(self.program, id="other", plan_id=other_plan["id"], sort_order=-10)
        )
        self.store.save_program(dict(self.program, id="second", sort_order=50))
        self.store.move_program("second", -1)
        self.assertEqual(
            [x["id"] for x in self.store.list_programs(self.plan["id"])],
            ["second", self.program["id"]],
        )
        self.assertEqual(self.store.get_program("other"), other)
        backup = self.store.export_data()
        self.store.import_data(backup, replace=True)
        self.assertEqual(
            [x["id"] for x in self.store.list_programs(self.plan["id"])],
            ["second", self.program["id"]],
        )
        for direction in (0, 2, True, 1.0):
            with self.subTest(direction=direction), self.assertRaises(ValueError):
                self.store.move_program("second", direction)
        with self.assertRaises(ValueError):
            self.store.move_program("missing", -1)
        with self.assertRaises(ValueError):
            self.store.save_program(dict(self.program, sort_order=1.5))

    def test_disabled_named_holiday_preserved_without_suppression(self):
        self.store.set_setting(
            "exclude_windows",
            [{"name": "运动会", "enabled": False, "start": "2026-11-17", "end": "2026-11-17"}],
        )
        stored = self.store.get_setting("exclude_windows")[0]
        self.assertEqual(stored["name"], "运动会")
        self.assertFalse(stored["enabled"])
        scheduler = Scheduler(self.store)
        self.assertEqual(len(scheduler.next_occurrences(self.at("2026-11-17T07:00:00"), days=1)), 1)
        self.assertEqual(len(scheduler.poll(self.at("2026-11-17T08:00:00"))), 1)
        # Named entries and enabled flags survive the backup boundary.
        self.store.import_data(self.store.export_data(), replace=True)
        self.assertEqual(self.store.get_setting("exclude_windows")[0], stored)
        self.program.update(
            id="disabled-window-program", exclude_windows=[dict(stored, name="节目内禁播")]
        )
        self.store.save_program(self.program)
        events = scheduler.poll(self.at("2026-11-17T08:00:00"))
        self.assertEqual([event["program"]["id"] for event in events], ["disabled-window-program"])

    def test_calendar_details_disabled_mapping_restores_real_day(self):
        self.store.set_setting("calendar_overrides", {"2026-11-17": "disabled"})
        self.store.set_setting(
            "calendar_details", {"2026-11-17": {"name": "临时调休", "enabled": False}}
        )
        scheduler = Scheduler(self.store)
        self.assertEqual(len(scheduler.next_occurrences(self.at("2026-11-17T07:00:00"), days=1)), 1)
        self.assertEqual(len(scheduler.poll(self.at("2026-11-17T08:00:00"))), 1)
        self.store.set_setting("calendar_overrides", {"2026-11-18": 0})
        self.store.set_setting("calendar_details", {"2026-11-18": {"enabled": False}})
        self.program.update(weekdays=[0])
        self.store.save_program(self.program)
        self.assertEqual(scheduler.poll(self.at("2026-11-18T08:00:00")), [])
        self.store.set_setting("calendar_details", {"2026-11-18": {"name": "补课"}})
        self.assertTrue(self.store.get_setting("calendar_details")["2026-11-18"]["enabled"])
        self.assertEqual(len(scheduler.poll(self.at("2026-11-18T08:00:00"))), 1)

    def test_calendar_names_and_flags_are_validated(self):
        for value in ({"bad": {}}, {"2026-11-17": {"name": 123}}, {"2026-11-17": {"enabled": 1}}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.set_setting("calendar_details", value)
        for window in ({"name": 1}, {"enabled": 1}):
            with self.subTest(window=window), self.assertRaises(ValueError):
                self.store.set_setting(
                    "exclude_windows", [dict(start="2026-11-17", end="2026-11-17", **window)]
                )
        defaults = validate_program(
            dict(self.program, exclude_windows=[{"start": "2026-11-17", "end": "2026-11-17"}])
        )
        self.assertEqual(defaults["exclude_windows"][0]["name"], "")
        self.assertTrue(defaults["exclude_windows"][0]["enabled"])

    def test_same_time_priority_ties_follow_program_order(self):
        self.store.save_program(dict(self.program, id="ordered-first", sort_order=-1))
        self.store.save_program(dict(self.program, id="higher-priority", priority=1))
        scheduler = Scheduler(self.store)
        preview = scheduler.next_occurrences(self.at("2026-11-17T07:00:00"), days=1)
        self.assertEqual(
            [item["program"]["id"] for item in preview],
            ["higher-priority", "ordered-first", self.program["id"]],
        )
        events = scheduler.poll(self.at("2026-11-17T08:00:00"))
        self.assertEqual(
            [item["program"]["id"] for item in events],
            ["higher-priority", "ordered-first", self.program["id"]],
        )

    def test_monthly_dates_ignore_weekday_selection_across_weekends(self):
        # November 1 is Sunday and December 1 is Tuesday; both match monthly day 1.
        self.program.update(month_days=[1], weekdays=[0])
        self.store.save_program(self.program)
        scheduler = Scheduler(self.store)
        self.assertEqual(len(scheduler.poll(self.at("2026-11-01T08:00:00"))), 1)
        self.assertEqual(scheduler.poll(self.at("2026-11-02T08:00:00")), [])
        self.assertEqual(len(scheduler.poll(self.at("2026-12-01T08:00:00"))), 1)
        self.assertEqual(len(scheduler.next_occurrences(self.at("2026-11-01T07:00:00"), days=1)), 1)

    def test_single_explicit_date_overrides_weekly_and_monthly_patterns(self):
        self.program.update(dates=["2026-11-17"], month_days=[1], weekdays=[])
        self.store.save_program(self.program)
        scheduler = Scheduler(self.store)
        self.assertEqual(len(scheduler.poll(self.at("2026-11-17T08:00:00"))), 1)
        self.assertEqual(scheduler.poll(self.at("2026-12-01T08:00:00")), [])
        self.assertEqual(scheduler.poll(self.at("2026-11-18T08:00:00")), [])

    def test_datetime_validity_is_inclusive_for_poll_and_preview(self):
        self.program.update(
            times=["07:59:59", "08:00", "08:00:01", "08:00:02"],
            datetime_from="2026-11-17T08:00",
            datetime_to="2026-11-17 08:00:01",
        )
        saved = self.store.save_program(self.program)
        self.assertEqual(saved["datetime_from"], "2026-11-17T08:00:00")
        self.assertEqual(saved["datetime_to"], "2026-11-17T08:00:01")
        scheduler = Scheduler(self.store)
        preview = scheduler.next_occurrences(self.at("2026-11-17T07:00:00"), days=2)
        self.assertEqual(
            [x["scheduled_at"] for x in preview], ["2026-11-17T08:00:00", "2026-11-17T08:00:01"]
        )
        events = scheduler.poll(self.at("2026-11-17T08:00:02"))
        self.assertEqual(
            [x["scheduled_at"] for x in events], ["2026-11-17T08:00:00", "2026-11-17T08:00:01"]
        )
        self.store.import_data(self.store.export_data(), replace=True)
        self.assertEqual(self.store.get_program(saved["id"]), saved)

    def test_datetime_validity_uses_actual_day_after_calendar_remapping(self):
        self.program.update(
            dates=["2026-11-16"], datetime_from="2026-11-17T08:00", datetime_to="2026-11-17T08:00"
        )
        self.store.save_program(self.program)
        self.store.set_setting("calendar_overrides", {"2026-11-17": "2026-11-16"})
        self.assertEqual(len(Scheduler(self.store).poll(self.at("2026-11-17T08:00:00"))), 1)

    def test_datetime_validity_input_rejects_reverse_or_incompatible_ranges(self):
        for change in (
            {"datetime_from": "2026-11-17T08:00:01", "datetime_to": "2026-11-17T08:00"},
            {"datetime_from": "2026-11-17"},
            {"datetime_to": "2026-11-17T08:00:00Z"},
            {"datetime_from": "2026-11-17T08:00:00.5"},
            {"date_from": "2026-11-18", "datetime_to": "2026-11-17T23:59:59"},
            {"date_to": "2026-11-16", "datetime_from": "2026-11-17T00:00:00"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_program(dict(self.program, **change))
        valid = validate_program(
            dict(self.program, datetime_from="2026-11-17T08:00", datetime_to="2026-11-17T08:00")
        )
        self.assertEqual(valid["datetime_from"], valid["datetime_to"])

    def test_validation_rejects_invalid_and_nonfinite_values(self):
        for change in (
            {"volume": 101},
            {"duration": float("nan")},
            {"weekdays": [7]},
            {"name": None},
            {"times": ["25:00"]},
            {"repeat": 1.5},
            {"date_from": "2026-12-01", "date_to": "2026-11-01"},
            {"month_days": [0]},
            {"playback_mode": "unknown"},
            {"enabled": 1},
            {"files": "not a list"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_program(dict(self.program, **change))


if __name__ == "__main__":
    unittest.main()
