"""Pure due-event selection with durable, at-most-once occurrence claiming.

The service does not perform playback or commands. A claimed occurrence is never
replayed automatically after a crash; consumers record success/failure explicitly.
"""

from __future__ import annotations

import hashlib
import heapq
import math
from datetime import datetime, time, timedelta

from .domain import active_at, excluded_at, runs_on


class Scheduler:
    def __init__(self, store, catchup_seconds=30):
        if (
            isinstance(catchup_seconds, bool)
            or not isinstance(catchup_seconds, (int, float))
            or not math.isfinite(catchup_seconds)
            or catchup_seconds < 0
        ):
            raise ValueError("补播窗口必须是非负秒数")
        self.store = store
        self.catchup_seconds = catchup_seconds
        self._last_poll = None

    def reset(self, now=None):
        """Resume from this instant, discarding all paused-time catch-up."""
        now = now or datetime.now().astimezone()
        if not isinstance(now, datetime):
            raise TypeError("now 必须是 datetime")
        self._last_poll = now

    @staticmethod
    def event_id(program_id, scheduled):
        # Wall time uniquely identifies a local occurrence even across a DST fold.
        stamp = scheduled.replace(tzinfo=None).isoformat(timespec="seconds")
        return hashlib.sha256(f"{program_id}\0{stamp}".encode()).hexdigest()

    def poll(self, now=None):
        now = now or datetime.now().astimezone()
        if not isinstance(now, datetime):
            raise TypeError("now 必须是 datetime")
        earliest = now - timedelta(seconds=self.catchup_seconds)
        # Never expand the configured catch-up window after a long sleep, and
        # retain durable claims when the wall clock moves backward.
        if self._last_poll is not None:
            if (now.tzinfo is None) != (self._last_poll.tzinfo is None):
                raise ValueError("不能混用无时区与有时区的调度时间")
            if earliest < self._last_poll <= now:
                earliest = self._last_poll
        self._last_poll = now
        details = self.store.get_setting("calendar_details", {})
        overrides = {
            day: target
            for day, target in self.store.get_setting("calendar_overrides", {}).items()
            if details.get(day, {}).get("enabled", True)
        }
        global_exclusions = {"exclude_windows": self.store.get_setting("exclude_windows", [])}
        active_plans = {x["id"] for x in self.store.list_plans() if x["enabled"]}
        candidates = []
        for program in self.store.list_programs():
            if not program["enabled"] or program["plan_id"] not in active_plans:
                continue
            day = earliest.date()
            while day <= now.date():
                override = overrides.get(day.isoformat())
                if override == "disabled":
                    day += timedelta(days=1)
                    continue
                if runs_on(program, day, override):
                    for at in program["times"] or [program["time"]]:
                        if excluded_at(program, day, at) or excluded_at(global_exclusions, day, at):
                            continue
                        scheduled = datetime.combine(day, time.fromisoformat(at), tzinfo=now.tzinfo)
                        if earliest <= scheduled <= now and active_at(program, scheduled):
                            candidates.append((scheduled, program))
                day += timedelta(days=1)
        candidates.sort(key=lambda pair: (pair[0], -pair[1]["priority"]))
        result = []
        for scheduled, program in candidates:
            identifier = self.event_id(program["id"], scheduled)
            stamp = scheduled.isoformat(timespec="seconds")
            if self.store.claim(identifier, program["id"], stamp):
                result.append({"event_id": identifier, "scheduled_at": stamp, "program": program})
        return result

    def next_occurrences(self, now=None, days=8, limit=20):
        """Read-only schedule preview, with no claims or execution side effects."""
        now = now or datetime.now().astimezone()
        if (
            not isinstance(days, int)
            or not 1 <= days <= 3660
            or not isinstance(limit, int)
            or limit < 1
        ):
            raise ValueError("预览天数或条数不合法")
        details = self.store.get_setting("calendar_details", {})
        overrides = {
            day: target
            for day, target in self.store.get_setting("calendar_overrides", {}).items()
            if details.get(day, {}).get("enabled", True)
        }
        global_exclusions = {"exclude_windows": self.store.get_setting("exclude_windows", [])}
        plans = {x["id"] for x in self.store.list_plans() if x["enabled"]}

        def candidates():
            # Each program contributes at most limit chronological occurrences.
            # Later occurrences from that program cannot enter the global first
            # limit results because limit earlier ones already precede them.
            for order, program in enumerate(self.store.list_programs()):
                if not program["enabled"] or program["plan_id"] not in plans:
                    continue
                times = [
                    (at, time.fromisoformat(at)) for at in program["times"] or [program["time"]]
                ]
                emitted = 0
                for offset in range(days):
                    day = now.date() + timedelta(days=offset)
                    override = overrides.get(day.isoformat())
                    if override == "disabled" or not runs_on(program, day, override):
                        continue
                    for at, parsed_time in times:
                        scheduled = datetime.combine(day, parsed_time, tzinfo=now.tzinfo)
                        if scheduled < now or not active_at(program, scheduled):
                            continue
                        if excluded_at(program, day, at) or excluded_at(global_exclusions, day, at):
                            continue
                        stamp = scheduled.isoformat(timespec="seconds")
                        emitted += 1
                        yield stamp, -program["priority"], order, program
                        if emitted >= limit:
                            break
                    if emitted >= limit:
                        break

        # A bounded heap avoids allocating and sorting the full preview horizon.
        # Explicit list order preserves manual ordering for equal time/priority.
        first = heapq.nsmallest(limit, candidates(), key=lambda item: item[:3])
        return [{"scheduled_at": stamp, "program": program} for stamp, _, _, program in first]
