"""Native Qt audio and offline speech, with independent scheduled/live channels.

In ``playlist`` mode, ``repeat`` counts complete playlist passes. ``tracks``
mode loops until ``track_limit`` successful tracks, and ``duration`` mode loops
until the requested seconds elapse. Zero limits are unlimited in those modes.
A nonzero duration also caps other modes; paused time is excluded. Playlists
advance lazily so large repetition counts never allocate repeated file lists.
"""

from __future__ import annotations

import base64
import math
import random
import time
from collections import deque
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
from PySide6.QtTextToSpeech import QTextToSpeech

START_TIMEOUT_SECONDS = 30
STALL_TIMEOUT_SECONDS = 15

MEDIA_EXTENSIONS = frozenset(
    {
        ".mp3",
        ".wav",
        ".flac",
        ".ogg",
        ".opus",
        ".aac",
        ".m4a",
        ".wma",
        ".aiff",
        ".aif",
        ".ape",
        ".wv",
        ".mp4",
        ".mkv",
        ".webm",
        ".alac",
        ".m4b",
        ".mov",
        ".mp2",
        ".mpa",
        ".m1a",
        ".m2a",
        ".tak",
        ".tta",
    }
)


def expand_sources(sources: list[str]) -> tuple[list[str], list[str]]:
    """Resolve files/directories deterministically, removing duplicate paths."""
    files, errors, seen = [], [], set()
    for source in sources:
        path = Path(source).expanduser()
        try:
            if path.is_dir():
                candidates = sorted(path.rglob("*"), key=lambda p: str(p).casefold())
            elif path.is_file():
                candidates = [path]
            else:
                errors.append(f"媒体路径不存在：{source}")
                continue
            for candidate in candidates:
                if not candidate.is_file() or candidate.suffix.lower() not in MEDIA_EXTENSIONS:
                    continue
                resolved = str(candidate.resolve())
                if resolved not in seen:
                    seen.add(resolved)
                    files.append(resolved)
        except OSError as exc:
            errors.append(f"无法读取媒体路径 {source}：{exc}")
    return files, errors


def _device_id(device) -> str:
    return base64.b64encode(bytes(device.id())).decode("ascii")


class _Channel(QObject):
    changed = Signal()
    log = Signal(str, str)
    finished = Signal()
    runChanged = Signal(str, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.player = QMediaPlayer(self)
        self.output = QAudioOutput(self)
        self.player.setAudioOutput(self.output)
        self.speech = None  # Load TTS only when requested; no online dependency.
        self.program = {}
        self.paths = []
        self.index = 0
        self.pass_index = 0
        self.failures = set()
        self.active = False
        self.paused = False
        self.volume = 80
        self.duck = 1.0
        self._elapsed = 0.0
        self._started = 0.0
        self._advance_pending = False
        self._generation = 0
        self._speech_started = False
        self._run_started = False
        self.completed_tracks = 0
        self.requested_device = ""
        self.effective_device = ""
        self.effective_device_name = ""
        self.device_error = ""
        self._last_position = 0
        self._last_progress_at = 0.0
        self.player.playbackStateChanged.connect(self._playback_state)
        self.player.mediaStatusChanged.connect(self._media_status)
        self.player.errorOccurred.connect(self._media_error)
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(lambda _: self.changed.emit())
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._tick)

    def elapsed(self):
        return self._elapsed + (
            time.monotonic() - self._started if self.active and not self.paused else 0.0
        )

    def snapshot(self):
        speech = self.program.get("action") == "speech"
        return {
            "name": self.program.get("name", "") if self.active else "",
            "status": ("paused" if self.paused else "playing") if self.active else "stopped",
            "position": self.elapsed() if speech and self.active else self.player.position() / 1000,
            "duration": float(self.program.get("duration", 0))
            if speech
            else self.player.duration() / 1000,
            "volume": self.volume,
            "file": self.paths[self.index] if self.active and self.paths else "",
            "elapsed": self.elapsed() if self.active else 0,
            "pass": self.pass_index + 1 if self.active else 0,
            "program_id": self.program.get("id", "") if self.active else "",
            "completed_tracks": self.completed_tracks,
            "seekable": bool(self.active and not speech and self.player.isSeekable()),
            "requested_device": self.requested_device,
            "effective_device": self.effective_device,
            "effective_device_name": self.effective_device_name,
            "device_error": self.device_error,
        }

    def start(self, program):
        self.stop()
        self.program = dict(program)
        self._run_started = False
        self.completed_tracks = 0
        self.paths = []
        self.index = self.pass_index = 0
        self.failures.clear()
        self.volume = max(0, min(100, int(program.get("volume", 80))))
        device_name = program.get("output_device", "")
        self.requested_device = device_name
        self.effective_device = ""
        self.effective_device_name = ""
        self.device_error = ""
        device = QMediaDevices.defaultAudioOutput()
        if device_name:
            requested = next(
                (
                    d
                    for d in QMediaDevices.audioOutputs()
                    if _device_id(d) == device_name or d.description() == device_name
                ),
                None,
            )
            if requested is None:
                self.device_error = "指定的输出设备不可用，改用系统默认设备：" + device_name
                self.log.emit("warning", self.device_error)
            else:
                device = requested
        try:
            self.output.setDevice(device)
        except (RuntimeError, TypeError) as exc:
            self.device_error = f"无法连接音频输出设备：{exc}"
            self._finish(self.device_error)
            return
        if not device.isNull():
            self.effective_device = _device_id(device)
            self.effective_device_name = device.description()
        self.active = True
        self._elapsed = 0.0
        self._started = time.monotonic()
        self._last_progress_at = self._started
        self._last_position = 0
        self.timer.start()
        if program.get("action") == "speech":
            self._start_speech()
            return
        self.paths, errors = expand_sources(program.get("files", []))
        for error in errors:
            self.log.emit("warning", error)
        if not self.paths:
            self._finish("没有可播放的媒体文件")
            return
        if device.isNull():
            self.device_error = "没有可用的音频输出设备"
            self._finish(self.device_error)
            return
        if program.get("shuffle"):
            random.shuffle(self.paths)
        self.index = self.pass_index = 0
        self.failures.clear()
        self._load()

    def _position_changed(self, position):
        if position != self._last_position:
            self._last_position = position
            self._last_progress_at = time.monotonic()
        self.changed.emit()

    def reconcile_devices(self, outputs, default):
        """Re-route active media in place. Never restart a finished/failed run.

        Return False when there is no usable route, so the owner can cancel the
        waiting queue rather than repeatedly opening media against a dead sink.
        """
        if not self.active:
            return True
        available = {_device_id(device): device for device in outputs if not device.isNull()}
        requested = (
            next(
                (
                    device
                    for key, device in available.items()
                    if key == self.requested_device or device.description() == self.requested_device
                ),
                None,
            )
            if self.requested_device
            else None
        )
        default_id = _device_id(default) if not default.isNull() else ""
        desired = requested or available.get(default_id)
        if desired is None:
            self.device_error = "播放期间音频输出设备断开，且没有可用的系统默认设备；节目已停止"
            self.effective_device = self.effective_device_name = ""
            self._finish(self.device_error)
            return False
        desired_id = _device_id(desired)
        if self.requested_device and requested is None:
            warning = "指定的输出设备已不可用，切换系统默认设备：" + desired.description()
            if warning != self.device_error:
                self.device_error = warning
                self.log.emit("warning", warning)
        if desired_id == self.effective_device:
            return True
        if self.program.get("action") == "speech":
            self.device_error = (
                "朗读期间系统默认音频设备发生变化；离线语音引擎无法保证中途切换，已停止本次朗读"
            )
            self._finish(self.device_error)
            return True
        reason = (
            "系统默认音频设备已变化，切换至：" if not self.requested_device else "音频输出切换至："
        )
        self.log.emit("warning", reason + desired.description())
        # QAudioOutput switches only its sink: leave source, position and pause
        # state intact. Reopening the media would replay audio and lose timing.
        try:
            self.output.setDevice(desired)
        except (RuntimeError, TypeError) as exc:
            self.device_error = f"切换音频输出设备失败：{exc}"
            self._finish(self.device_error)
            return False
        self.effective_device = desired_id
        self.effective_device_name = desired.description()
        if not self.requested_device or requested is not None:
            self.device_error = ""
        self._last_progress_at = time.monotonic()
        self.changed.emit()
        return True

    def check_health(self):
        """Bound loading/stalled states which some audio drivers never error."""
        if not self.active or self.paused:
            return
        stalled = time.monotonic() - self._last_progress_at
        if not self._run_started and stalled >= START_TIMEOUT_SECONDS:
            self.device_error = "媒体或语音引擎在30秒内未开始播放；请检查文件和音频设备"
            self._finish(self.device_error)
        elif (
            self._run_started
            and self.program.get("action") != "speech"
            and stalled >= STALL_TIMEOUT_SECONDS
            and self.player.mediaStatus() != QMediaPlayer.MediaStatus.EndOfMedia
        ):
            self.device_error = "播放进度持续15秒未推进，音频设备或解码器可能异常；节目已停止"
            self._finish(self.device_error)

    def _run_state(self, status, message=""):
        event_id = self.program.get("_run_event_id", "")
        if event_id:
            self.runChanged.emit(str(event_id), status, message or self.program.get("name", ""))

    def _mark_started(self):
        if self.active and not self._run_started:
            self._run_started = True
            self._last_progress_at = time.monotonic()
            self._run_state("started")

    def _playback_state(self, state):
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._mark_started()

    def _mode(self):
        return self.program.get("stop_mode", "playlist")

    def _should_repeat_speech(self):
        if self._mode() == "duration":
            return True
        if self._mode() == "tracks":
            limit = int(self.program.get("track_limit", 0))
            return not limit or self.completed_tracks < limit
        return self.pass_index < int(self.program.get("repeat", 1))

    def _load(self):
        if not self.active:
            return
        self._apply_volume()
        self.player.setSource(QUrl.fromLocalFile(self.paths[self.index]))
        self.player.play()
        self.changed.emit()

    def _start_speech(self):
        if self.program.get("output_device"):
            self._finish("离线朗读使用系统默认语音输出；请清空指定音频设备")
            return
        if not self.program.get("text", "").strip():
            self._finish("朗读内容为空")
            return
        if self.speech is None:
            if not QTextToSpeech.availableEngines():
                self._finish("当前系统未安装离线语音引擎")
                return
            self.speech = QTextToSpeech(self)
            self.speech.stateChanged.connect(self._speech_state)
            self.speech.errorOccurred.connect(self._speech_error)
        if self.speech.state() == QTextToSpeech.State.Error:
            self._finish("离线语音引擎不可用：" + self.speech.errorString())
            return
        requested_voice = self.program.get("voice", "")
        if requested_voice:
            voice = next(
                (v for v in self.speech.availableVoices() if v.name() == requested_voice), None
            )
            if voice is None:
                self._finish("指定的离线语音不可用：" + requested_voice)
                return
            self.speech.setVoice(voice)
        self.pass_index = 0
        self._speech_started = False
        self.speech.setRate(float(self.program.get("speech_rate", 0)))
        self.speech.setPitch(float(self.program.get("speech_pitch", 0)))
        self._apply_volume()
        self.speech.say(self.program["text"])
        self.changed.emit()

    def _speech_state(self, state):
        if not self.active or self.program.get("action") != "speech":
            return
        if state == QTextToSpeech.State.Speaking:
            self._speech_started = True
            self._mark_started()
        elif state == QTextToSpeech.State.Ready and self._speech_started and not self.paused:
            self._speech_started = False
            self.pass_index += 1
            self.completed_tracks += 1
            if self._should_repeat_speech():
                generation = self._generation
                QTimer.singleShot(0, lambda: self._repeat_speech(generation))
            else:
                self._finish()

    def _repeat_speech(self, generation):
        if (
            self.active
            and self.speech is not None
            and generation == self._generation
            and self.program.get("action") == "speech"
        ):
            self.speech.say(self.program["text"])

    def _speech_error(self, *_):
        if self.active and self.program.get("action") == "speech":
            self._finish("朗读失败：" + self.speech.errorString())

    def _media_status(self, status):
        if self.active and self.program.get("action") != "speech":
            if status == QMediaPlayer.MediaStatus.EndOfMedia:
                if not self._advance_pending:
                    self.completed_tracks += 1
                self._schedule_advance()
            elif status == QMediaPlayer.MediaStatus.InvalidMedia:
                self._media_error()

    def _media_error(self, *_):
        if not self.active or not self.paths or self.program.get("action") == "speech":
            return
        path = self.paths[self.index]
        if path not in self.failures:
            self.failures.add(path)
            self.log.emit("error", f"无法播放 {path}：{self.player.errorString()}")
        self._schedule_advance()

    def _schedule_advance(self):
        if not self._advance_pending:
            self._advance_pending = True
            generation = self._generation
            QTimer.singleShot(0, lambda: self._advance(generation))

    def _advance(self, generation=None):
        if generation is not None and generation != self._generation:
            return
        self._advance_pending = False
        if not self.active or not self.paths:
            return
        track_limit = int(self.program.get("track_limit", 0))
        if self._mode() == "tracks" and track_limit and self.completed_tracks >= track_limit:
            self._finish()
            return
        if all(path in self.failures for path in self.paths):
            self._finish("本节目全部媒体均播放失败")
            return
        # Each bad source is skipped for the remainder of this programme.
        for _ in range(len(self.paths) + 1):
            self.index += 1
            if self.index >= len(self.paths):
                self.pass_index += 1
                if self._mode() == "playlist" and self.pass_index >= int(
                    self.program.get("repeat", 1)
                ):
                    self._finish()
                    return
                self.index = 0
                if self.program.get("shuffle"):
                    random.shuffle(self.paths)
            if self.paths[self.index] not in self.failures:
                self._load()
                return
        self._finish("本节目全部媒体均播放失败")

    def _apply_volume(self):
        factor = 1.0
        elapsed = self.elapsed()
        fade_in = float(self.program.get("fade_in", 0))
        fade_out = float(self.program.get("fade_out", 0))
        limit = float(self.program.get("duration", 0))
        if fade_in > 0:
            factor = min(factor, elapsed / fade_in)
        remaining = limit - elapsed if limit else None
        # Without an explicit limit, fade out on the final item of the final pass.
        last_playlist = (
            self._mode() == "playlist"
            and self.index == len(self.paths) - 1
            and self.pass_index + 1 >= int(self.program.get("repeat", 1))
        )
        track_limit = int(self.program.get("track_limit", 0))
        last_track = (
            self._mode() == "tracks"
            and track_limit > 0
            and self.completed_tracks + 1 >= track_limit
        )
        if (
            not limit
            and self.paths
            and (last_playlist or last_track)
            and self.player.duration() > 0
        ):
            remaining = (self.player.duration() - self.player.position()) / 1000
        if fade_out > 0 and remaining is not None:
            factor = min(factor, remaining / fade_out)
        value = self.volume / 100 * self.duck * max(0.0, min(1.0, factor))
        self.output.setVolume(value)
        if self.speech is not None:
            self.speech.setVolume(value)

    def _tick(self):
        if not self.active or self.paused:
            return
        limit = float(self.program.get("duration", 0))
        if limit and self.elapsed() >= limit:
            self._finish()
            return
        self._apply_volume()
        if self.program.get("action") == "speech":
            self.changed.emit()

    def pause(self):
        if self.active and not self.paused:
            self._elapsed = self.elapsed()
            self.paused = True
            self.player.pause()
            if self.speech is not None and self.program.get("action") == "speech":
                self.speech.pause()
            self.changed.emit()

    def resume(self):
        if self.active and self.paused:
            self._started = time.monotonic()
            self._last_progress_at = self._started
            self.paused = False
            if self.program.get("action") == "speech" and self.speech is not None:
                self.speech.resume()
            else:
                self.player.play()
            self.changed.emit()

    def seek(self, position):
        position = float(position)
        if not math.isfinite(position):
            raise ValueError("播放位置必须是有限秒数")
        if not self.active:
            return False
        if self.program.get("action") == "speech":
            self.log.emit("warning", "离线朗读不支持跳转播放位置")
            return False
        if not self.player.isSeekable():
            self.log.emit("warning", "当前媒体尚不可跳转播放位置")
            return False
        milliseconds = max(0, int(position * 1000))
        duration = self.player.duration()
        if duration > 0:
            milliseconds = min(milliseconds, duration)
        self.player.setPosition(milliseconds)
        self.changed.emit()
        return True

    def set_volume(self, value):
        self.volume = max(0, min(100, int(value)))
        self._apply_volume()
        self.changed.emit()

    def stop(self, _result="cancelled", _message=""):
        was_active = self.active
        if was_active:
            self._run_state(_result, _message)
        self._generation += 1
        self.active = False
        self.paused = False
        self._advance_pending = False
        self._elapsed = 0.0
        self.timer.stop()
        self.player.stop()
        self.player.setSource(QUrl())
        if self.speech is not None:
            self.speech.stop()
        self.changed.emit()

    def _finish(self, error=""):
        name = self.program.get("name", "")
        if not self.active:
            self._run_state("failed" if error else "completed", error or name)
        self.stop("failed" if error else "completed", error or name)
        if error:
            self.log.emit("error", f"{name}：{error}")
        else:
            self.log.emit("info", f"播放完成：{name}")
        self.finished.emit()


class Playback(QObject):
    """Two playback channels: a scheduled queue and immediate live playback."""

    changed = Signal()
    log = Signal(str, str)
    runChanged = Signal(str, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scheduled = _Channel(self)
        self.immediate = _Channel(self)
        self.queue = deque()
        self.duck_volume = 25
        self._draining = False
        for channel in (self.scheduled, self.immediate):
            channel.changed.connect(self._changed)
            channel.log.connect(self.log)
            channel.runChanged.connect(self.runChanged)
        self.scheduled.finished.connect(self._schedule_next)
        self.immediate.finished.connect(self._restore_duck)
        self.device_monitor = QMediaDevices(self)
        self.device_monitor.audioOutputsChanged.connect(self._check_devices)
        self.device_timer = QTimer(self)
        self.device_timer.setInterval(1000)
        self.device_timer.timeout.connect(self._poll_devices)
        self.device_timer.start()

    def _poll_devices(self):
        if self.scheduled.active or self.immediate.active:
            self._check_devices()

    def _check_devices(self):
        outputs = QMediaDevices.audioOutputs()
        default = QMediaDevices.defaultAudioOutput()
        healthy = self.scheduled.reconcile_devices(outputs, default)
        self.immediate.reconcile_devices(outputs, default)
        if not healthy:
            self._cancel_queue("音频输出不可用，取消等待播放的节目；设备恢复后不会重播")
        self.scheduled.check_health()
        self.immediate.check_health()
        self.changed.emit()

    @staticmethod
    def devices():
        return [
            {"id": _device_id(d), "name": d.description()} for d in QMediaDevices.audioOutputs()
        ]

    def state(self):
        channel = self.immediate if self.immediate.active else self.scheduled
        result = channel.snapshot()
        result["channel"] = "immediate" if channel is self.immediate else "scheduled"
        result["queued"] = len(self.queue)
        result["scheduled"] = self.scheduled.snapshot()
        result["immediate"] = self.immediate.snapshot()
        return result

    def _changed(self):
        self._restore_duck()
        self.changed.emit()

    def _restore_duck(self):
        self.scheduled.duck = self.duck_volume / 100 if self.immediate.active else 1.0
        self.scheduled._apply_volume()
        self.changed.emit()

    def set_duck_volume(self, value):
        self.duck_volume = max(0, min(100, int(value)))
        self._restore_duck()

    def play(self, program, immediate=False):
        program = dict(program)
        if "action" not in program:
            program["action"] = {"tts": "speech"}.get(
                program.get("kind"), program.get("kind", "audio")
            )
        if program.get("action") not in ("audio", "speech"):
            self.log.emit("error", "播放引擎仅接受媒体和朗读节目")
            return
        if immediate:
            self.immediate.start(program)
        elif self.scheduled.active and program.get("playback_mode", "interrupt") == "queue":
            priority = int(program.get("priority", 0))
            position = next(
                (i for i, item in enumerate(self.queue) if int(item.get("priority", 0)) < priority),
                len(self.queue),
            )
            self.queue.insert(position, program)
            self.log.emit("info", "已加入播放队列：" + program.get("name", ""))
        else:
            self.scheduled.start(program)
        self.changed.emit()

    def _schedule_next(self):
        # Defer to avoid recursive error cascades when queued paths are missing.
        if not self._draining:
            self._draining = True
            QTimer.singleShot(0, self._next)

    def _next(self):
        self._draining = False
        if not self.scheduled.active and self.queue:
            self.scheduled.start(self.queue.popleft())
        self.changed.emit()

    def _cancel_queue(self, reason):
        count = len(self.queue)
        for item in self.queue:
            if item.get("_run_event_id"):
                self.runChanged.emit(str(item["_run_event_id"]), "cancelled", reason)
        self.queue.clear()
        if count:
            self.log.emit("warning", f"{reason}（{count} 个节目）")

    def stop_scheduled(self):
        self._cancel_queue("播放队列已清空")
        self.scheduled.stop()
        self.changed.emit()

    def stop_immediate(self):
        self.immediate.stop()
        self._restore_duck()
        self.changed.emit()

    def stop(self):
        self.stop_scheduled()
        self.stop_immediate()

    def seek(self, position):
        return (self.immediate if self.immediate.active else self.scheduled).seek(position)

    def pause(self):
        (self.immediate if self.immediate.active else self.scheduled).pause()

    def resume(self):
        (self.immediate if self.immediate.active else self.scheduled).resume()

    def set_volume(self, value):
        (self.immediate if self.immediate.active else self.scheduled).set_volume(value)
