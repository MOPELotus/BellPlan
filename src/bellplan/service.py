"""Independent polling thread; the UI receives durable due events only.

The default 100 ms polling period is an observation interval, not a guarantee of
media-start accuracy. Actual detection lateness accompanies every event.
"""

from __future__ import annotations

import math
import queue
import threading
import time
from datetime import datetime
from pathlib import Path
from types import MappingProxyType

from PySide6.QtCore import QObject, Signal

from .scheduler import Scheduler
from .storage import Store


def _freeze(value):
    """Keep decoded scheduling data immutable between worker polling cycles."""
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value):
    """Detach the rare due event from the read-only worker cache."""
    if isinstance(value, MappingProxyType):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


_MISSING = object()


class _PollingStore(Store):
    """Cache read-only configuration; claim one batch in an outer transaction.

    This connection is dedicated to polling and run-status writes. Configuration
    edits use the UI's separate Store connection. SQLite's data_version changes
    for those external commits, but not for this connection's own run claims.
    """

    def __init__(self, path):
        try:
            super().__init__(path, timeout=0.1)
            self.connection.execute("PRAGMA busy_timeout = 100")
            self._data_version = None
            self._poll_active = False
            self._programs_cache = None
            self._plans_cache = None
            self._settings_cache = {}
        except Exception:
            if hasattr(self, "connection"):
                self.connection.close()
            raise

    def _refresh_cache_version(self):
        version = self.connection.execute("PRAGMA data_version").fetchone()[0]
        if version != self._data_version:
            self._data_version = version
            self._programs_cache = None
            self._plans_cache = None
            self._settings_cache.clear()

    def begin_poll(self):
        """Sample once inside the transaction, fixing the matching WAL snapshot.

        Sampling before BEGIN is unsafe: an external commit could otherwise make
        the cached rules older than the transaction used to claim occurrences.
        """
        if not self.connection.in_transaction:
            raise RuntimeError("缓存版本须在调度事务中读取")
        self._refresh_cache_version()
        self._poll_active = True

    def end_poll(self):
        self._poll_active = False

    def _ensure_cache_version(self):
        if not self._poll_active:
            # Also keep direct diagnostic/test reads current outside a poll.
            self._refresh_cache_version()

    def list_programs(self, plan_id=None):
        self._ensure_cache_version()
        if self._programs_cache is None:
            self._programs_cache = _freeze(super().list_programs())
        if plan_id is None:
            return self._programs_cache
        return tuple(record for record in self._programs_cache if record["plan_id"] == plan_id)

    def list_plans(self):
        self._ensure_cache_version()
        if self._plans_cache is None:
            self._plans_cache = _freeze(super().list_plans())
        return self._plans_cache

    def get_setting(self, key, default=None):
        self._ensure_cache_version()
        if key not in self._settings_cache:
            value = super().get_setting(key, _MISSING)
            self._settings_cache[key] = _freeze(value) if value is not _MISSING else _MISSING
        value = self._settings_cache[key]
        return _freeze(default) if value is _MISSING else value

    def claim(self, event_id, program_id, scheduled_at):
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO runs(event_id,program_id,scheduled_at) VALUES(?,?,?)",
            (event_id, program_id, scheduled_at),
        )
        return cursor.rowcount == 1


class SchedulerService(QObject):
    """Poll on one Python thread with its own SQLite connection.

    Signals emitted from the worker are queued to QObject receivers on their own
    Qt thread. The service does not call a player, execute commands or perform
    power operations. ``stop`` returns False if its bounded join times out.
    """

    ready = Signal()
    due = Signal(dict)
    diagnostic = Signal(str, str)

    def __init__(
        self,
        path,
        parent=None,
        *,
        enabled=True,
        interval=0.1,
        catchup_seconds=30,
        stall_threshold=2.0,
    ):
        super().__init__(parent)
        if str(path) == ":memory:":
            raise ValueError("独立调度服务需要文件数据库")
        if type(enabled) is not bool:
            raise ValueError("调度启用状态必须是布尔值")
        for value, label in ((interval, "检查间隔"), (stall_threshold, "停顿检查阈值")):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{label}必须是正数")
        # Let the core's constructor validate the compensation policy.
        Scheduler(None, catchup_seconds=catchup_seconds)
        self.path = Path(path)
        self.interval = float(interval)
        self.catchup_seconds = catchup_seconds
        self.stall_threshold = float(stall_threshold)
        self._enabled = enabled
        self._control_serial = 0
        self._activation_floor = None
        self._lock = threading.RLock()
        self._commands = queue.Queue()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None

    @property
    def is_running(self):
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self):
        """Start once; starting an already running service is harmless."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop.clear()
            self._wake.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="BellPlan-Scheduler",
                daemon=True,
            )
            self._thread.start()
            return True

    def stop(self, timeout=3):
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout < 0
        ):
            raise ValueError("停止等待时间必须是非负秒数")
        with self._lock:
            self._stop.set()
            self._wake.set()
            thread = self._thread
        if thread is None:
            return True
        if thread is threading.current_thread():
            return False
        thread.join(timeout)
        if thread.is_alive():
            self.diagnostic.emit("error", "调度线程尚未停止，请勿关闭其数据库")
            return False
        with self._lock:
            if self._thread is thread:
                self._thread = None
        return True

    def set_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("调度启用状态必须是布尔值")
        with self._lock:
            if enabled == self._enabled:
                return
            self._enabled = enabled
            self._control_serial += 1
            stamp = datetime.now()
            self._activation_floor = stamp
            self._commands.put(("enabled", enabled, stamp, self._control_serial))
        self._wake.set()

    def reset(self):
        """Skip prior occurrences after an explicit user pause or reset."""
        with self._lock:
            self._control_serial += 1
            stamp = datetime.now()
            self._activation_floor = stamp
            self._commands.put(("reset", None, stamp, self._control_serial))
        self._wake.set()

    def notify_resume(self):
        """Recheck the catch-up window after an OS resume notification.

        This differs from an explicit user reset: eligible recent occurrences
        remain eligible. A user-pause boundary still takes precedence.
        """
        with self._lock:
            self._commands.put(("resume", None, datetime.now(), self._control_serial))
        self._wake.set()

    def _clock_message(self, old_wall, old_mono, wall, mono):
        wall_span = (wall - old_wall).total_seconds()
        mono_span = mono - old_mono
        if wall_span < -0.5:
            return "系统时间回拨；已执行任务保持去重"
        if abs(wall_span - mono_span) > self.stall_threshold:
            return "检测到系统时间变化或休眠恢复，按补播窗口检查任务"
        if max(wall_span, mono_span) > self.stall_threshold:
            return "调度检查发生长时间停顿，按补播窗口检查任务"
        return None

    def _run(self):
        store = None
        scheduler = None
        with self._lock:
            enabled = self._enabled
            applied_serial = self._control_serial
            activation_floor = self._activation_floor
        last_success = activation_floor
        old_wall, old_mono = datetime.now(), time.monotonic()
        error_at = float("-inf")
        announced = False
        deadline = old_mono
        try:
            while not self._stop.is_set():
                self._wake.clear()
                wall, mono = datetime.now(), time.monotonic()
                message = self._clock_message(old_wall, old_mono, wall, mono)
                if message:
                    self.diagnostic.emit("warning", message)
                old_wall, old_mono = wall, mono
                try:
                    if store is None:
                        store = _PollingStore(self.path)
                        scheduler = Scheduler(store, catchup_seconds=self.catchup_seconds)
                        if last_success is not None:
                            scheduler.reset(last_success)
                    while True:
                        try:
                            action, value, stamp, serial = self._commands.get_nowait()
                        except queue.Empty:
                            break
                        applied_serial = serial
                        if action == "enabled":
                            enabled = value
                            scheduler.reset(stamp)
                            last_success = stamp
                            activation_floor = stamp
                        elif action == "reset":
                            scheduler.reset(stamp)
                            last_success = stamp
                            activation_floor = stamp
                        elif action == "resume":
                            self.diagnostic.emit(
                                "info",
                                "系统已恢复，检查补播窗口内的定时任务"
                                if enabled
                                else "系统已恢复；定时播保持暂停",
                            )
                            scheduler = Scheduler(store, catchup_seconds=self.catchup_seconds)
                            if activation_floor is not None:
                                scheduler.reset(activation_floor)
                            last_success = activation_floor
                    # A control message may carry a timestamp newer than the
                    # beginning of this iteration, so take a fresh wall time.
                    wall = datetime.now()
                    if enabled and not self._stop.is_set():
                        # A read snapshot and all occurrence claims form one
                        # transaction. A failed batch leaves no partial claims.
                        try:
                            with store.connection:
                                store.connection.execute("BEGIN")
                                store.begin_poll()
                                events = scheduler.poll(wall)
                        finally:
                            store.end_poll()
                        last_success = wall
                        with self._lock:
                            cancelled = (
                                self._stop.is_set()
                                or not self._enabled
                                or applied_serial != self._control_serial
                            )
                        if cancelled:
                            for event in events:
                                store.finish_run(
                                    event["event_id"], "cancelled", "调度已暂停、重置或停止"
                                )
                            events = []
                        for event in events:
                            event["program"] = _thaw(event["program"])
                            detected = datetime.now()
                            scheduled = datetime.fromisoformat(event["scheduled_at"])
                            event["detected_at"] = detected.isoformat(timespec="milliseconds")
                            event["lateness_ms"] = max(
                                0.0, (detected - scheduled).total_seconds() * 1000
                            )
                            event["detected_monotonic"] = time.monotonic()
                            self.due.emit(event)
                    else:
                        scheduler.reset(wall)
                        last_success = wall
                    if not announced:
                        announced = True
                        self.ready.emit()
                except Exception as exc:
                    # A lock or transient I/O failure must not silently kill the
                    # worker. Recreate its connection and preserve the last
                    # successful cursor for a bounded retry.
                    if mono - error_at >= 1:
                        self.diagnostic.emit("error", f"调度检查失败，将重试：{exc}")
                        error_at = mono
                    if store is not None:
                        store.close()
                        store = None
                        scheduler = None
                deadline += self.interval
                now_mono = time.monotonic()
                if deadline <= now_mono:
                    deadline = now_mono + self.interval
                self._wake.wait(max(0.0, deadline - now_mono))
        finally:
            if store is not None:
                store.close()
