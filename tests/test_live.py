import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from bellplan.live import LivePlaylist
from bellplan.storage import Store


class LivePlaylistTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.live = LivePlaylist(self.store)

    def tearDown(self):
        self.store.close()

    def test_add_preserves_directory_and_deduplicates(self):
        result = self.live.add(["目录/铃声", "", "  ", "a.mp3", "目录/铃声"])
        self.assertEqual(result["files"], ["目录/铃声", "a.mp3"])
        self.assertEqual(result["selectedIndex"], 0)
        self.assertEqual(LivePlaylist(self.store).snapshot(), result)

    def test_snapshot_is_detached(self):
        self.live.add(["a.mp3"])
        snap = self.live.snapshot()
        snap["files"].append("not-saved.mp3")
        self.assertEqual(self.live.snapshot()["files"], ["a.mp3"])

    def test_move_tracks_selection(self):
        self.live.add(["a", "b", "c"])
        self.live.configure({"selectedIndex": 1})
        self.live.move(0, 1)
        self.assertEqual(self.live.snapshot()["selectedIndex"], 0)
        self.live.move(0, 1)
        self.assertEqual(self.live.snapshot()["selectedIndex"], 1)
        self.assertEqual(self.live.snapshot()["files"], ["a", "b", "c"])

    def test_remove_updates_selection_and_empty(self):
        self.live.add(["a", "b", "c"])
        self.live.configure({"selectedIndex": 2})
        self.live.remove(0)
        self.assertEqual(self.live.snapshot()["selectedIndex"], 1)
        self.live.remove(1)
        self.assertEqual(self.live.snapshot()["selectedIndex"], 0)
        self.live.remove(0)
        self.assertEqual(self.live.snapshot()["selectedIndex"], -1)

    def test_reordering_retains_selected_file(self):
        self.live.add(["c", "A", "b"])
        self.live.configure({"selectedIndex": 2})
        self.live.sort()
        self.assertEqual(self.live.snapshot()["files"], ["A", "b", "c"])
        self.assertEqual(self.live.snapshot()["selectedIndex"], 1)
        self.live.sort(True)
        self.assertEqual(self.live.snapshot()["selectedIndex"], 1)
        with patch("bellplan.live.random.shuffle", side_effect=lambda files: files.reverse()):
            self.live.shuffle()
        selected = self.live.snapshot()["selectedIndex"]
        self.assertEqual(self.live.snapshot()["files"][selected], "b")

    def test_type_sort_uses_extension_name_and_full_path(self):
        paths = [
            r"C:\z\a.WAV",
            r"C:\z\Beta.MP3",
            "/b/alpha.mp3",
            "/a/ALPHA.Mp3",
            "/directory/no-extension",
            "/a/z.flac",
        ]
        self.live.add(paths)
        self.live.configure({"selectedIndex": 1})
        expected = [
            "/directory/no-extension",
            "/a/z.flac",
            "/a/ALPHA.Mp3",
            "/b/alpha.mp3",
            r"C:\z\Beta.MP3",
            r"C:\z\a.WAV",
        ]
        self.live.sort(by="type")
        result = self.live.snapshot()
        self.assertEqual(result["files"], expected)
        self.assertEqual(result["selectedIndex"], 4)
        self.assertEqual(LivePlaylist(self.store).snapshot(), result)
        self.live.sort(descending=True, by="type")
        result = self.live.snapshot()
        self.assertEqual(result["files"], list(reversed(expected)))
        self.assertEqual(result["files"][result["selectedIndex"]], paths[1])

    def test_unknown_sort_mode_is_rejected_without_changes(self):
        self.live.add(["b.mp3", "a.wav"])
        before = self.live.snapshot()
        for by in ("size", "", None, []):
            with self.subTest(by=by), self.assertRaises(ValueError):
                self.live.sort(by=by)
            self.assertEqual(self.live.snapshot(), before)
            self.assertEqual(self.store.get_setting("live"), before)

    def test_list_program_rotates_without_dropping_files(self):
        self.live.add(["a", "b", "c"])
        self.live.configure(
            {"volume": 42, "repeat": 0, "duration": 12.5, "output_device": "device"}
        )
        result = self.live.build_program(1)
        self.assertEqual(result["files"], ["b", "c", "a"])
        self.assertEqual(result["volume"], 42)
        self.assertEqual(result["repeat"], 0)
        self.assertEqual(result["duration"], 12.5)
        self.assertEqual(result["output_device"], "device")
        self.assertNotIn("plan_id", result)
        self.assertEqual(self.live.snapshot()["selectedIndex"], 1)

    def test_single_and_random_modes(self):
        self.live.add(["a", "b"])
        self.live.configure(
            {"mode": "single", "selectedIndex": 1, "stop_mode": "tracks", "track_limit": 3}
        )
        result = self.live.build_program()
        self.assertEqual(result["files"], ["b"])
        self.assertFalse(result["shuffle"])
        self.assertEqual(result["track_limit"], 3)
        self.live.configure({"mode": "random"})
        result = self.live.build_program()
        self.assertTrue(result["shuffle"])
        self.assertEqual(set(result["files"]), {"a", "b"})

    def test_clear_preserves_playback_preferences(self):
        self.live.add(["a"])
        self.live.configure({"volume": 37})
        self.live.clear()
        self.assertEqual(self.live.snapshot()["files"], [])
        self.assertEqual(self.live.snapshot()["volume"], 37)
        self.assertEqual(LivePlaylist(self.store).snapshot()["selectedIndex"], -1)

    def test_reject_invalid_changes_atomically(self):
        self.live.add(["a"])
        before = self.live.snapshot()
        invalid = [
            {"mode": "invalid"},
            {"repeat": -1},
            {"volume": 101},
            {"volume": True},
            {"duration": float("inf")},
            {"duration": float("nan")},
            {"duration": -1},
            {"files": []},
            {"selectedIndex": 1},
            {"track_limit": 1.2},
            {"output_device": 2},
        ]
        for change in invalid:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.live.configure(change)
            self.assertEqual(self.live.snapshot(), before)
            self.assertEqual(self.store.get_setting("live"), before)

    def test_invalid_indices_and_inputs(self):
        with self.assertRaises(ValueError):
            self.live.build_program()
        self.live.add(["a", "b"])
        for index in (-1, 2, True, 1.0):
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.live.remove(index)
        for index, direction in ((0, -1), (1, 1), (0, 2), (0, True)):
            with self.subTest(move=(index, direction)), self.assertRaises(ValueError):
                self.live.move(index, direction)
        for paths in ("a.mp3", [3]):
            with self.assertRaises(ValueError):
                self.live.add(paths)

    def test_failed_persistence_preserves_memory(self):
        before = self.live.snapshot()
        with patch.object(self.store, "set_setting", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.live.add(["a"])
        self.assertEqual(self.live.snapshot(), before)

    def test_file_store_survives_restart(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "data.sqlite3"
            store = Store(path)
            live = LivePlaylist(store)
            live.add([f"{i}.mp3" for i in range(2500)])
            live.configure({"mode": "random", "stop_mode": "duration", "duration": 120})
            expected = live.snapshot()
            store.close()
            reopened = Store(path)
            try:
                self.assertEqual(LivePlaylist(reopened).snapshot(), expected)
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
