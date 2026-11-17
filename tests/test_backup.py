import unittest
from copy import deepcopy

from bellplan.backup import prepare_import, select_sections
from bellplan.live import LivePlaylist
from bellplan.storage import Store


def sample():
    return {
        "format": "bellplan",
        "version": 1,
        "plans": [{"id": "p", "name": "工作日", "enabled": True}],
        "programs": [
            {"id": "audio", "plan_id": "p", "name": "铃声", "action": "audio", "enabled": True},
            {"id": "cmd", "plan_id": "p", "name": "程序", "action": "command", "enabled": True},
            {"id": "power", "plan_id": "p", "name": "关机", "action": "system", "enabled": True},
        ],
        "settings": {
            "ui": {
                "closeToTray": False,
                "startAtLogin": True,
                "duckVolume": 45,
                "exclude_windows": [{"start": "2000-01-01", "end": "2000-01-01"}],
                "calendar_overrides": {"2000-01-01": "disabled"},
                "calendar_details": {"2000-01-01": {"enabled": False}},
            },
            "exclude_windows": [{"start": "2026-11-17", "end": "2026-11-18"}],
            "calendar_overrides": {"2026-11-17": "2026-11-15"},
            "calendar_details": {"2026-11-17": {"name": "调休", "enabled": True}},
            "live": {"files": ["a.mp3"], "selectedIndex": 0},
            "schedulerEnabled": True,
        },
    }


class BackupTests(unittest.TestCase):
    def test_default_selects_all_supported_groups(self):
        result = select_sections(sample())
        self.assertEqual(
            set(result["settings"]),
            {"ui", "exclude_windows", "calendar_overrides", "calendar_details", "live"},
        )
        self.assertEqual(len(result["programs"]), 3)
        self.assertNotIn("exclude_windows", result["settings"]["ui"])
        self.assertNotIn("calendar_overrides", result["settings"]["ui"])
        self.assertNotIn("calendar_details", result["settings"]["ui"])

    def test_each_scope_is_independent(self):
        expected = {
            "programs": set(),
            "holidays": {"exclude_windows"},
            "calendar": {"calendar_overrides", "calendar_details"},
            "live": {"live"},
            "settings": {"ui"},
        }
        for section, keys in expected.items():
            with self.subTest(section=section):
                result = select_sections(sample(), [section])
                self.assertEqual(set(result["settings"]), keys)
                self.assertEqual(bool(result["programs"]), section == "programs")
                self.assertEqual(bool(result["plans"]), section == "programs")

    def test_empty_scope_keeps_no_user_records(self):
        result = select_sections(sample(), [])
        self.assertEqual(result["plans"], [])
        self.assertEqual(result["programs"], [])
        self.assertEqual(result["settings"], {})

    def test_selection_and_prepare_never_modify_source(self):
        original = sample()
        unchanged = deepcopy(original)
        selected = select_sections(original)
        prepared = prepare_import(selected, {"startAtLogin": False})
        prepared["settings"]["live"]["files"].append("b.mp3")
        self.assertEqual(original, unchanged)
        self.assertEqual(selected["settings"]["live"]["files"], ["a.mp3"])

    def test_invalid_scope_and_containers_rejected(self):
        for scope in ("live", ["missing"], [False], {}):
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                select_sections(sample(), scope)
        for data in ([], {"settings": []}, {"settings": None}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                select_sections(data)

    def test_preference_merge_preserves_login_startup(self):
        data = select_sections(sample(), ["settings"])
        result = prepare_import(
            data, {"startAtLogin": False, "catchupSeconds": 12, "defaultDevice": "current"}
        )
        ui = result["settings"]["ui"]
        self.assertFalse(ui["startAtLogin"])
        self.assertFalse(ui["closeToTray"])
        self.assertEqual(ui["catchupSeconds"], 12)
        self.assertEqual(ui["defaultDevice"], "current")
        self.assertEqual(ui["duckVolume"], 45)
        self.assertTrue(
            prepare_import(data, {"startAtLogin": True})["settings"]["ui"]["startAtLogin"]
        )

    def test_unselected_preferences_remain_absent(self):
        result = prepare_import(select_sections(sample(), ["live"]), {"duckVolume": 99})
        self.assertNotIn("ui", result["settings"])
        self.assertEqual(result["plans"], [])

    def test_import_disables_only_executable_actions(self):
        prepared = prepare_import(select_sections(sample()), {})
        by_id = {p["id"]: p for p in prepared["programs"]}
        self.assertTrue(by_id["audio"]["enabled"])
        self.assertFalse(by_id["cmd"]["enabled"])
        self.assertFalse(by_id["power"]["enabled"])

    def test_ui_values_all_validated_before_import(self):
        invalid = [
            {"closeToTray": 1},
            {"startAtLogin": "yes"},
            {"duckVolume": True},
            {"duckVolume": 101},
            {"catchupSeconds": -1},
            {"catchupSeconds": 301},
            {"catchupSeconds": 0.5},
            {"defaultDevice": 3},
            {"theme": "dark"},
            {"unknown": "x"},
        ]
        for ui in invalid:
            with self.subTest(ui=ui), self.assertRaises(ValueError):
                prepare_import({"programs": [], "settings": {"ui": ui}}, {})

    def test_live_and_calendar_validation_rejects_invalid_data(self):
        invalid = [
            {"live": {"files": "a.mp3"}},
            {"live": {"files": [], "selectedIndex": 1}},
            {"live": {"volume": -1}},
            {"live": {"duration": float("nan")}},
            {"exclude_windows": [{"start": "bad", "end": "2026-01-01"}]},
            {"calendar_overrides": {"bad": 0}},
            {"calendar_details": {"2026-01-01": {"enabled": "yes"}}},
            {"schedulerEnabled": True},
        ]
        for settings in invalid:
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                prepare_import({"programs": [], "settings": settings}, {})

    def test_normalized_import_keeps_live_and_calendar_metadata(self):
        result = prepare_import(select_sections(sample()), {})
        self.assertEqual(result["settings"]["live"]["mode"], "list")
        self.assertEqual(result["settings"]["calendar_details"]["2026-11-17"]["name"], "调休")
        self.assertEqual(result["settings"]["exclude_windows"][0]["time_from"], "")

    def test_malformed_programs_rejected(self):
        for programs in (None, {}, [None]):
            with self.subTest(programs=programs), self.assertRaises(ValueError):
                prepare_import({"programs": programs, "settings": {}}, {})

    def test_live_only_import_does_not_change_other_store_settings(self):
        store = Store(":memory:")
        try:
            store.set_setting("ui", {"duckVolume": 80})
            store.set_setting("calendar_overrides", {"2026-11-17": "disabled"})
            data = prepare_import(select_sections(sample(), ["live"]), {"duckVolume": 80})
            store.import_data(data)
            self.assertEqual(store.get_setting("ui"), {"duckVolume": 80})
            self.assertEqual(store.get_setting("calendar_overrides"), {"2026-11-17": "disabled"})
            self.assertEqual(LivePlaylist(store).snapshot()["files"], ["a.mp3"])
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
