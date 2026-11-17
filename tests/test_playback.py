"""Playback state-machine tests; these do not create/render a GUI window."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtCore import QCoreApplication
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTextToSpeech import QTextToSpeech

from bellplan.playback import Playback, _device_id, expand_sources

APP = QCoreApplication.instance() or QCoreApplication([])


class FakeAudioDevice:
    """Device metadata only: no fake sink or software audio is rendered."""

    def __init__(self, identifier="", name=""):
        self.identifier = identifier
        self.name = name or identifier

    def id(self):
        return self.identifier.encode()

    def description(self):
        return self.name

    def isNull(self):
        return not self.identifier


class PlaybackTests(unittest.TestCase):
    def setUp(self):
        self.player = Playback()
        self.messages = []
        self.player.log.connect(lambda level, msg: self.messages.append((level, msg)))

    def tearDown(self):
        self.player.stop()
        APP.processEvents()
        self.player.deleteLater()
        APP.processEvents()

    def test_recursive_media_paths_deduplicated_and_filtered(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            (folder / "sub").mkdir()
            wav = folder / "sub" / "a.WAV"
            wav.write_bytes(b"RIFF")
            (folder / "ignored.txt").write_text("not media")
            files, errors = expand_sources([str(folder), str(wav), str(folder / "missing")])
            # Canonical paths may expand Windows 8.3 temporary-directory names.
            self.assertEqual(files, [str(wav.resolve())])
            self.assertEqual(len(errors), 1)

    def test_unsupported_file_is_not_playable(self):
        with tempfile.TemporaryDirectory() as root:
            f = Path(root) / "note.txt"
            f.write_text("a")
            files, errors = expand_sources([str(f)])
            self.assertEqual(files, [])
            self.assertEqual(errors, [])

    def test_queue_does_not_interrupt_current_program(self):
        self.player.scheduled.active = True
        self.player.scheduled.program = {"name": "current"}
        with patch.object(self.player.scheduled, "start") as start:
            self.player.play({"action": "audio", "name": "queued", "playback_mode": "queue"})
            start.assert_not_called()
        self.assertEqual(self.player.state()["queued"], 1)
        self.assertEqual(self.player.state()["name"], "current")

    def test_interrupt_replaces_current_program(self):
        self.player.scheduled.active = True
        new = {"action": "audio", "name": "replacement"}
        with patch.object(self.player.scheduled, "start") as start:
            self.player.play(new)
            start.assert_called_once_with(new)

    def test_live_channel_is_independent(self):
        self.player.scheduled.active = True
        with patch.object(self.player.immediate, "start") as live:
            with patch.object(self.player.scheduled, "start") as scheduled:
                self.player.play({"action": "audio", "name": "live"}, immediate=True)
                live.assert_called_once()
                scheduled.assert_not_called()
        self.assertTrue(self.player.scheduled.active)

    def test_duck_restored_after_live_playback(self):
        self.player.scheduled.active = True
        self.player.immediate.active = True
        self.player.set_duck_volume(17)
        self.assertEqual(self.player.scheduled.duck, 0.17)
        self.player.immediate.active = False
        self.player._restore_duck()
        self.assertEqual(self.player.scheduled.duck, 1)

    def test_stop_clears_queue_and_both_channels(self):
        self.player.queue.append({"name": "queued"})
        self.player.scheduled.active = self.player.immediate.active = True
        self.player.stop()
        self.assertEqual(self.player.state()["status"], "stopped")
        self.assertFalse(self.player.scheduled.active)
        self.assertFalse(self.player.immediate.active)
        self.assertEqual(len(self.player.queue), 0)

    def test_elapsed_time_excludes_pause(self):
        channel = self.player.scheduled
        channel.active = True
        channel._started = 100
        with patch("bellplan.playback.time.monotonic", return_value=105):
            channel.pause()
        self.assertEqual(channel.elapsed(), 5)
        with patch("bellplan.playback.time.monotonic", return_value=200):
            with patch.object(channel.player, "play"):
                channel.resume()
        with patch("bellplan.playback.time.monotonic", return_value=203):
            self.assertEqual(channel.elapsed(), 8)

    def test_stale_end_event_does_not_advance_replacement(self):
        channel = self.player.scheduled
        old = channel._generation
        channel.stop()
        channel.active = True
        channel.paths = ["a.wav", "b.wav"]
        channel.index = 0
        with patch.object(channel, "_load") as load:
            channel._advance(old)
            load.assert_not_called()
        self.assertEqual(channel.index, 0)

    def test_zero_playlist_repeat_advances_across_three_rounds(self):
        channel = self.player.scheduled
        channel.active = True
        channel.paths = ["a.wav", "b.wav"]
        channel.program = {"action": "audio", "stop_mode": "playlist", "repeat": 0}
        with patch.object(channel, "_load") as load:
            for _ in range(6):
                channel._media_status(QMediaPlayer.MediaStatus.EndOfMedia)
                APP.processEvents()
                self.assertTrue(channel.active)
            self.assertEqual(load.call_count, 6)
        self.assertEqual(channel.pass_index, 3)
        self.assertEqual(channel.completed_tracks, 6)

    def test_positive_playlist_repeat_finishes_after_exact_pass_count(self):
        channel = self.player.scheduled
        for repeat in (1, 2):
            with self.subTest(repeat=repeat):
                channel.stop()
                channel.active = True
                channel.paths = ["a.wav", "b.wav"]
                channel.index = channel.pass_index = channel.completed_tracks = 0
                channel.program = {"action": "audio", "stop_mode": "playlist", "repeat": repeat}
                with patch.object(channel, "_load") as load:
                    for number in range(repeat * 2):
                        channel._media_status(QMediaPlayer.MediaStatus.EndOfMedia)
                        APP.processEvents()
                        self.assertEqual(channel.active, number < repeat * 2 - 1)
                    self.assertEqual(load.call_count, repeat * 2 - 1)
                self.assertEqual(channel.completed_tracks, repeat * 2)

    def test_zero_speech_repeat_keeps_scheduling_after_three_utterances(self):
        channel = self.player.scheduled
        channel.active = True
        channel.speech = Mock()
        channel.program = {"action": "speech", "text": "test", "stop_mode": "playlist", "repeat": 0}
        for _ in range(3):
            channel._speech_state(QTextToSpeech.State.Speaking)
            channel._speech_state(QTextToSpeech.State.Ready)
            APP.processEvents()
            self.assertTrue(channel.active)
        self.assertEqual(channel.speech.say.call_count, 3)
        self.assertEqual(channel.pass_index, 3)
        self.assertEqual(channel.completed_tracks, 3)

    def test_positive_speech_repeat_stops_at_exact_utterance_count(self):
        channel = self.player.scheduled
        for repeat in (1, 2):
            with self.subTest(repeat=repeat):
                channel.stop()
                channel.active = True
                channel.speech = Mock()
                channel.pass_index = channel.completed_tracks = 0
                channel.program = {
                    "action": "speech",
                    "text": "test",
                    "stop_mode": "playlist",
                    "repeat": repeat,
                }
                for number in range(repeat):
                    channel._speech_state(QTextToSpeech.State.Speaking)
                    channel._speech_state(QTextToSpeech.State.Ready)
                    APP.processEvents()
                    self.assertEqual(channel.active, number < repeat - 1)
                self.assertEqual(channel.speech.say.call_count, repeat - 1)

    def test_zero_playlist_repeat_does_not_fade_each_pass_as_final_pass(self):
        channel = self.player.scheduled
        channel.active = True
        channel.paths = ["one.wav"]
        channel.volume = 100
        channel.program = {"stop_mode": "playlist", "repeat": 0, "fade_out": 1}
        with patch.object(channel.player, "duration", return_value=1000):
            with patch.object(channel.player, "position", return_value=950):
                channel._apply_volume()
        self.assertAlmostEqual(channel.output.volume(), 1.0, places=5)

    def test_playlist_repetition_is_lazy_and_finishes(self):
        channel = self.player.scheduled
        channel.active = True
        channel.paths = ["a.wav", "b.wav"]
        channel.program = {"repeat": 2}
        with patch.object(channel, "_load") as load:
            channel._advance()
            self.assertEqual((channel.index, channel.pass_index), (1, 0))
            channel._advance()
            self.assertEqual((channel.index, channel.pass_index), (0, 1))
            channel._advance()
            channel._advance()
            self.assertEqual(load.call_count, 3)
        self.assertFalse(channel.active)

    def test_all_broken_sources_terminate_without_retry_loop(self):
        channel = self.player.scheduled
        channel.active = True
        channel.paths = ["a.wav", "b.wav"]
        channel.failures = set(channel.paths)
        channel.program = {"repeat": 1_000_000}
        with patch.object(channel, "_load") as load:
            channel._advance()
            load.assert_not_called()
        self.assertFalse(channel.active)

    def test_missing_media_reports_error_and_finishes(self):
        self.player.play(
            {"action": "audio", "name": "missing", "files": ["/definitely/not/a/file.mp3"]}
        )
        self.assertFalse(self.player.scheduled.active)
        self.assertTrue(any(level == "error" for level, _ in self.messages))

    def test_commands_are_never_executed_by_audio_engine(self):
        self.player.play({"action": "command", "command": ["anything"]})
        self.assertFalse(self.player.scheduled.active)
        self.assertTrue(any(level == "error" for level, _ in self.messages))

    def test_missing_device_warns_and_uses_available_default(self):
        channel = self.player.scheduled
        default = FakeAudioDevice("default")
        with patch("bellplan.playback.QMediaDevices.defaultAudioOutput", return_value=default):
            with patch("bellplan.playback.QMediaDevices.audioOutputs", return_value=[]):
                with patch.object(channel.output, "setDevice") as select:
                    with patch("bellplan.playback.expand_sources", return_value=(["test.wav"], [])):
                        with patch.object(channel, "_load") as load:
                            self.player.play(
                                {
                                    "action": "audio",
                                    "name": "device",
                                    "output_device": "missing-device",
                                }
                            )
                            load.assert_called_once()
                    select.assert_called_once_with(default)
        self.assertTrue(channel.active)
        self.assertTrue(
            any(level == "warning" and "改用系统默认设备" in msg for level, msg in self.messages)
        )

    def test_missing_device_and_missing_default_still_fail(self):
        channel = self.player.scheduled
        default = FakeAudioDevice()
        with patch("bellplan.playback.QMediaDevices.defaultAudioOutput", return_value=default):
            with patch("bellplan.playback.QMediaDevices.audioOutputs", return_value=[]):
                with patch.object(channel.output, "setDevice"):
                    with patch("bellplan.playback.expand_sources", return_value=(["test.wav"], [])):
                        self.player.play({"action": "audio", "output_device": "missing"})
        self.assertFalse(channel.active)
        self.assertTrue(
            any(
                level == "error" and "没有可用的音频输出设备" in msg for level, msg in self.messages
            )
        )

    def test_time_limit_finishes_program(self):
        channel = self.player.scheduled
        channel.active = True
        channel.program = {"duration": 3}
        channel._started = 100
        with patch("bellplan.playback.time.monotonic", return_value=104):
            channel._tick()
        self.assertFalse(channel.active)

    def test_track_mode_loops_until_number_of_tracks(self):
        channel = self.player.scheduled
        channel.active = True
        channel.paths = ["one.wav"]
        channel.program = {"stop_mode": "tracks", "track_limit": 3, "repeat": 1}
        with patch.object(channel, "_load") as load:
            channel.completed_tracks = 1
            channel._advance()
            channel.completed_tracks = 2
            channel._advance()
            self.assertTrue(channel.active)
            self.assertEqual(load.call_count, 2)
            channel.completed_tracks = 3
            channel._advance()
        self.assertFalse(channel.active)

    def test_duration_mode_ignores_playlist_repeat(self):
        channel = self.player.scheduled
        channel.active = True
        channel.paths = ["one.wav"]
        channel.program = {"stop_mode": "duration", "duration": 30, "repeat": 1}
        with patch.object(channel, "_load") as load:
            for _ in range(5):
                channel._advance()
            self.assertEqual(load.call_count, 5)
        self.assertTrue(channel.active)

    def test_zero_track_limit_has_no_artificial_limit(self):
        channel = self.player.scheduled
        channel.active = True
        channel.paths = ["one.wav"]
        channel.program = {"stop_mode": "tracks", "track_limit": 0}
        with patch.object(channel, "_load"):
            channel.completed_tracks = 1_000_000
            channel._advance()
        self.assertTrue(channel.active)

    def test_run_events_report_started_once_and_completion(self):
        channel = self.player.scheduled
        channel.active = True
        channel.program = {"name": "test", "_run_event_id": "event-1"}
        events = []
        self.player.runChanged.connect(lambda *args: events.append(args))
        channel._mark_started()
        channel._mark_started()
        channel._finish()
        self.assertEqual(
            [(e[0], e[1]) for e in events], [("event-1", "started"), ("event-1", "completed")]
        )

    def test_missing_source_reports_failed_not_completed(self):
        events = []
        self.player.runChanged.connect(lambda *args: events.append(args))
        self.player.play({"action": "audio", "files": [], "_run_event_id": "bad"})
        self.assertEqual([(e[0], e[1]) for e in events], [("bad", "failed")])

    def test_stop_cancels_queued_and_active_runs(self):
        events = []
        self.player.runChanged.connect(lambda *args: events.append(args))
        channel = self.player.scheduled
        channel.active = True
        channel.program = {"name": "active", "_run_event_id": "active"}
        self.player.queue.append({"_run_event_id": "queued"})
        self.player.stop()
        self.assertEqual(
            {(e[0], e[1]) for e in events}, {("active", "cancelled"), ("queued", "cancelled")}
        )

    def test_queue_prioritizes_higher_priority_stably(self):
        self.player.scheduled.active = True
        for name, priority in [("low", 1), ("first-high", 5), ("second-high", 5)]:
            self.player.play(
                {"action": "audio", "name": name, "priority": priority, "playback_mode": "queue"}
            )
        self.assertEqual(
            [p["name"] for p in self.player.queue], ["first-high", "second-high", "low"]
        )

    def test_seek_targets_live_channel_and_clamps_to_media_duration(self):
        channel = self.player.immediate
        self.player.scheduled.active = channel.active = True
        with patch.object(channel.player, "isSeekable", return_value=True):
            with patch.object(channel.player, "duration", return_value=10000):
                with patch.object(channel.player, "setPosition") as position:
                    self.assertTrue(self.player.seek(20))
                    position.assert_called_once_with(10000)

    def test_seek_falls_back_to_scheduled_channel(self):
        channel = self.player.scheduled
        channel.active = True
        with patch.object(channel.player, "isSeekable", return_value=True):
            with patch.object(channel.player, "setPosition") as position:
                self.assertTrue(self.player.seek(-20))
                position.assert_called_once_with(0)

    def test_nonseekable_and_speech_have_explicit_warnings(self):
        channel = self.player.scheduled
        channel.active = True
        with patch.object(channel.player, "isSeekable", return_value=False):
            self.assertFalse(self.player.seek(1))
        channel.program = {"action": "speech"}
        self.assertFalse(self.player.seek(1))
        self.assertEqual(sum(level == "warning" for level, _ in self.messages), 2)

    def test_seek_rejects_nonfinite_value(self):
        with self.assertRaises(ValueError):
            self.player.seek(float("nan"))

    def test_stop_live_preserves_scheduled_and_queue(self):
        self.player.scheduled.active = self.player.immediate.active = True
        self.player.queue.append({"name": "queued"})
        self.player.stop_immediate()
        self.assertTrue(self.player.scheduled.active)
        self.assertFalse(self.player.immediate.active)
        self.assertEqual(len(self.player.queue), 1)
        self.assertEqual(self.player.scheduled.duck, 1)

    def test_stop_scheduled_preserves_live(self):
        self.player.scheduled.active = self.player.immediate.active = True
        self.player.queue.append({"name": "queued"})
        self.player.stop_scheduled()
        self.assertFalse(self.player.scheduled.active)
        self.assertTrue(self.player.immediate.active)
        self.assertEqual(len(self.player.queue), 0)
        self.assertEqual(self.player.state()["channel"], "immediate")

    def test_manual_playback_never_emits_run_callbacks(self):
        events = []
        self.player.runChanged.connect(lambda *args: events.append(args))
        channel = self.player.scheduled
        channel.active = True
        channel.program = {"_run_event_id": ""}
        channel._mark_started()
        channel._finish()
        channel.active = True
        channel.program = {}
        channel._mark_started()
        channel.stop()
        self.player.queue.append({"_run_event_id": ""})
        self.player.stop()
        self.assertEqual(events, [])

    def test_legacy_extension_sources_are_scanned(self):
        extensions = (".mp2", ".mpa", ".m1a", ".m2a", ".tak", ".tta")
        with tempfile.TemporaryDirectory() as root:
            for extension in extensions:
                (Path(root) / ("test" + extension)).write_bytes(b"example")
            files, errors = expand_sources([root])
        self.assertEqual(len(files), len(extensions))
        self.assertEqual(errors, [])

    def _active_route(self, device, requested="", channel=None):
        channel = channel or self.player.scheduled
        channel.active = True
        channel._run_started = True
        channel._last_progress_at = time.monotonic()
        channel._started = time.monotonic()
        channel.program = {"action": "audio", "_run_event_id": "hotplug-run"}
        channel.requested_device = requested
        channel.effective_device = _device_id(device)
        channel.effective_device_name = device.description()
        return channel

    def test_hotplug_missing_explicit_device_switches_default_without_reopening(self):
        original = FakeAudioDevice("usb", "USB speaker")
        fallback = FakeAudioDevice("default", "Built-in speaker")
        channel = self._active_route(original, _device_id(original))
        channel.paused = True
        with patch.object(channel.output, "setDevice") as switch:
            with patch.object(channel.player, "setSource") as reopen:
                with patch.object(channel.player, "setPosition") as seek:
                    with patch.object(channel.player, "play") as play:
                        self.assertTrue(channel.reconcile_devices([fallback], fallback))
                        switch.assert_called_once_with(fallback)
                        reopen.assert_not_called()
                        seek.assert_not_called()
                        play.assert_not_called()
        self.assertTrue(channel.active)
        self.assertTrue(channel.paused)
        self.assertEqual(channel.snapshot()["requested_device"], _device_id(original))
        self.assertEqual(channel.snapshot()["effective_device"], _device_id(fallback))
        self.assertTrue(channel.snapshot()["device_error"])
        self.assertTrue(any(level == "warning" for level, _ in self.messages))

    def test_hotplug_default_channel_follows_new_default(self):
        original, replacement = FakeAudioDevice("old"), FakeAudioDevice("new")
        channel = self._active_route(original)
        with patch.object(channel.output, "setDevice") as switch:
            channel.reconcile_devices([original, replacement], replacement)
            switch.assert_called_once_with(replacement)
        self.assertEqual(channel.effective_device, _device_id(replacement))
        self.assertEqual(channel.device_error, "")

    def test_hotplug_explicit_present_device_ignores_changed_default(self):
        usb, replacement = FakeAudioDevice("usb"), FakeAudioDevice("new-default")
        channel = self._active_route(usb, _device_id(usb))
        with patch.object(channel.output, "setDevice") as switch:
            channel.reconcile_devices([usb, replacement], replacement)
            switch.assert_not_called()
        self.assertEqual(channel.effective_device, _device_id(usb))

    def test_hotplug_missing_all_devices_fails_current_and_cancels_queue(self):
        original = FakeAudioDevice("usb")
        channel = self._active_route(original)
        self.player.queue.extend([{"_run_event_id": "queued-1"}, {"_run_event_id": "queued-2"}])
        events = []
        self.player.runChanged.connect(lambda *args: events.append(args))
        with patch("bellplan.playback.QMediaDevices.audioOutputs", return_value=[]):
            with patch(
                "bellplan.playback.QMediaDevices.defaultAudioOutput",
                return_value=FakeAudioDevice(),
            ):
                self.player._check_devices()
                APP.processEvents()
        self.assertFalse(channel.active)
        self.assertEqual(len(self.player.queue), 0)
        self.assertEqual(
            {(e[0], e[1]) for e in events},
            {("hotplug-run", "failed"), ("queued-1", "cancelled"), ("queued-2", "cancelled")},
        )
        self.assertEqual(channel.effective_device, "")
        self.assertIn("没有可用", channel.device_error)
        # A later device notification never revives the failed run.
        with patch.object(channel, "start") as restart:
            channel.reconcile_devices([original], original)
            restart.assert_not_called()
        self.assertFalse(channel.active)

    def test_hotplug_both_channels_are_checked(self):
        original = FakeAudioDevice("usb")
        self._active_route(original)
        self._active_route(original, channel=self.player.immediate)
        with patch("bellplan.playback.QMediaDevices.audioOutputs", return_value=[]):
            with patch(
                "bellplan.playback.QMediaDevices.defaultAudioOutput",
                return_value=FakeAudioDevice(),
            ):
                self.player._check_devices()
        self.assertFalse(self.player.scheduled.active)
        self.assertFalse(self.player.immediate.active)

    def test_hotplug_switch_failure_is_observable_and_stops_channel(self):
        original, fallback = FakeAudioDevice("usb"), FakeAudioDevice("default")
        channel = self._active_route(original)
        with patch.object(channel.output, "setDevice", side_effect=RuntimeError("driver error")):
            self.assertFalse(channel.reconcile_devices([fallback], fallback))
        self.assertFalse(channel.active)
        self.assertIn("driver error", channel.device_error)

    def test_device_poll_is_one_second_and_signal_is_connected(self):
        self.assertEqual(self.player.device_timer.interval(), 1000)
        self.assertTrue(self.player.device_timer.isActive())
        with patch.object(self.player.scheduled, "reconcile_devices", return_value=True) as check:
            self.player.device_monitor.audioOutputsChanged.emit()
            check.assert_called_once()

    def test_stalled_media_fails_instead_of_staying_silent_forever(self):
        channel = self._active_route(FakeAudioDevice("usb"))
        channel._last_progress_at = 100
        with patch("bellplan.playback.time.monotonic", return_value=116):
            channel.check_health()
        self.assertFalse(channel.active)
        self.assertIn("未推进", channel.device_error)

    def test_loading_timeout_is_bounded_and_pause_does_not_time_out(self):
        channel = self._active_route(FakeAudioDevice("usb"))
        channel._run_started = False
        channel._last_progress_at = 100
        channel.paused = True
        with patch("bellplan.playback.time.monotonic", return_value=140):
            channel.check_health()
            self.assertTrue(channel.active)
            channel.paused = False
            channel.check_health()
        self.assertFalse(channel.active)
        self.assertIn("未开始播放", channel.device_error)

    def test_speech_device_change_stops_with_explicit_engine_limitation(self):
        original, replacement = FakeAudioDevice("old"), FakeAudioDevice("new")
        channel = self._active_route(original)
        channel.program["action"] = "speech"
        channel.reconcile_devices([replacement], replacement)
        self.assertFalse(channel.active)
        self.assertIn("离线语音引擎", channel.device_error)

    def test_fades_and_duck_share_the_output_volume(self):
        channel = self.player.scheduled
        channel.active = True
        channel.volume = 80
        channel.duck = 0.25
        channel.program = {"fade_in": 4, "fade_out": 2, "duration": 10}
        channel._started = 100
        with patch("bellplan.playback.time.monotonic", return_value=102):
            channel._apply_volume()
        self.assertAlmostEqual(channel.output.volume(), 0.1, places=5)
        with patch("bellplan.playback.time.monotonic", return_value=109):
            channel._apply_volume()
        self.assertAlmostEqual(channel.output.volume(), 0.1, places=5)


if __name__ == "__main__":
    unittest.main()
