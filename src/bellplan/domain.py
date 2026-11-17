"""Validated, JSON-compatible scheduling records independent of any GUI toolkit."""

from __future__ import annotations

from datetime import date, datetime, time
from uuid import uuid4

ACTIONS = frozenset({"audio", "speech", "command", "system"})
SYSTEM_ACTIONS = frozenset({"shutdown", "restart", "sleep", "lock", "stop", "volume"})


def _boolean(value, name):
    if not isinstance(value, bool):
        raise ValueError(f"{name} 必须是布尔值")
    return value


def _number(value, name, low, high, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} 必须是数字")
    if not low <= value <= high or (integer and int(value) != value):
        raise ValueError(f"{name} 必须在 {low} 至 {high} 范围内")
    return int(value) if integer else float(value)


def _string_list(value, name):
    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
        raise ValueError(f"{name} 必须是字符串列表")
    return list(value)


def _date(value):
    if value in ("", None):
        return ""
    if not isinstance(value, str):
        raise ValueError("日期必须使用 YYYY-MM-DD 格式")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("日期必须使用 YYYY-MM-DD 格式") from exc
    if parsed.isoformat() != value:
        raise ValueError("日期必须使用 YYYY-MM-DD 格式")
    return value


def _time(value):
    if not isinstance(value, str):
        raise ValueError("时间必须使用 HH:MM 或 HH:MM:SS 格式")
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("时间必须使用 HH:MM 或 HH:MM:SS 格式") from exc
    if parsed.tzinfo is not None or parsed.microsecond or len(value) not in (5, 8):
        raise ValueError("时间必须使用 HH:MM 或 HH:MM:SS 格式")
    return parsed.strftime("%H:%M:%S")


def _datetime(value):
    if value in ("", None):
        return ""
    if not isinstance(value, str) or len(value) not in (16, 19) or value[10] not in ("T", " "):
        raise ValueError("有效期必须使用本地 YYYY-MM-DDTHH:MM:SS 日期时间")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("有效期必须使用本地 YYYY-MM-DDTHH:MM:SS 日期时间") from exc
    if parsed.tzinfo is not None or parsed.microsecond:
        raise ValueError("有效期必须使用无时区的本地日期时间")
    return parsed.isoformat(timespec="seconds")


def validate_plan(record: dict) -> dict:
    if not isinstance(record, dict):
        raise ValueError("计划必须是对象")
    raw_name = record.get("name", "")
    if not isinstance(raw_name, str):
        raise ValueError("名称必须是字符串")
    name = raw_name.strip()
    if not name:
        raise ValueError("计划名称不能为空")
    identifier = record.get("id") or str(uuid4())
    if not isinstance(identifier, str):
        raise ValueError("计划 ID 必须是字符串")
    return {
        "id": identifier,
        "name": name,
        "enabled": _boolean(record.get("enabled", True), "计划启用状态"),
    }


def validate_program(record: dict) -> dict:
    if not isinstance(record, dict):
        raise ValueError("节目必须是对象")
    raw_name = record.get("name", "")
    if not isinstance(raw_name, str):
        raise ValueError("名称必须是字符串")
    name = raw_name.strip()
    if not name:
        raise ValueError("节目名称不能为空")
    identifier = record.get("id") or str(uuid4())
    plan_id = record.get("plan_id")
    if not isinstance(identifier, str) or not isinstance(plan_id, str) or not plan_id:
        raise ValueError("节目必须属于一个计划")
    action = record.get("action", "audio")
    if action not in ACTIONS:
        raise ValueError("不支持的节目类型")
    weekdays = record.get("weekdays", list(range(7)))
    if not isinstance(weekdays, list) or any(
        type(x) is not int or x not in range(7) for x in weekdays
    ):
        raise ValueError("星期必须是 0（周一）至 6（周日）的列表")
    days = [_date(x) for x in _string_list(record.get("dates", []), "指定日期")]
    excluded = [_date(x) for x in _string_list(record.get("excluded_dates", []), "排除日期")]
    if "" in days or "" in excluded:
        raise ValueError("指定日期和排除日期不能包含空日期")
    start, end = _date(record.get("date_from")), _date(record.get("date_to"))
    if start and end and start > end:
        raise ValueError("开始日期不能晚于结束日期")
    datetime_start = _datetime(record.get("datetime_from"))
    datetime_end = _datetime(record.get("datetime_to"))
    earliest = max(filter(None, [datetime_start, start + "T00:00:00" if start else ""]), default="")
    latest = min(filter(None, [datetime_end, end + "T23:59:59" if end else ""]), default="")
    if earliest and latest and earliest > latest:
        raise ValueError("有效期开始时间不能晚于结束时间")
    month_days = record.get("month_days", [])
    if not isinstance(month_days, list) or any(
        type(x) is not int or not 1 <= x <= 31 for x in month_days
    ):
        raise ValueError("每月日期必须是 1 至 31 的列表")
    output_device = record.get("output_device", "")
    if not isinstance(output_device, str):
        raise ValueError("输出设备必须是字符串")
    playback_mode = record.get("playback_mode", "interrupt")
    if playback_mode not in ("interrupt", "queue"):
        raise ValueError("播放模式必须是 interrupt 或 queue")
    stop_mode = record.get("stop_mode", "playlist")
    if stop_mode not in ("playlist", "tracks", "duration"):
        raise ValueError("结束方式必须是 playlist、tracks 或 duration")
    track_limit = record.get("track_limit", 0)
    if type(track_limit) is not int or track_limit < 0:
        raise ValueError("曲目次数必须是非负整数（0 为无限）")
    sort_order = record.get("sort_order", 0)
    if type(sort_order) is not int:
        raise ValueError("节目排序必须是整数")
    validated_windows = validate_exclude_windows(record.get("exclude_windows", []))
    at = _time(record.get("time", "08:00:00"))
    times = sorted(set(_time(x) for x in _string_list(record.get("times", []), "时间列表")))
    result = {
        "id": identifier,
        "plan_id": plan_id,
        "name": name,
        "enabled": _boolean(record.get("enabled", True), "节目启用状态"),
        "action": action,
        "files": _string_list(record.get("files", []), "媒体文件"),
        "text": record.get("text", ""),
        "command": _string_list(record.get("command", []), "命令参数"),
        "system_action": record.get("system_action", "stop"),
        "time": at,
        "times": times,
        "weekdays": sorted(set(weekdays)),
        "dates": sorted(set(days)),
        "excluded_dates": sorted(set(excluded)),
        "date_from": start,
        "date_to": end,
        "datetime_from": datetime_start,
        "datetime_to": datetime_end,
        "month_days": sorted(set(month_days)),
        "output_device": output_device,
        "playback_mode": playback_mode,
        "exclude_windows": validated_windows,
        "stop_mode": stop_mode,
        "track_limit": track_limit,
        "sort_order": sort_order,
        "volume": _number(record.get("volume", 80), "音量", 0, 100, True),
        "repeat": _number(record.get("repeat", 1), "播放次数", 0, 1_000_000, True),
        "duration": _number(record.get("duration", 0), "播放时长", 0, 31_536_000),
        "fade_in": _number(record.get("fade_in", 0), "淡入时长", 0, 86_400),
        "fade_out": _number(record.get("fade_out", 0), "淡出时长", 0, 86_400),
        "shuffle": _boolean(record.get("shuffle", False), "随机播放"),
        "priority": _number(record.get("priority", 0), "优先级", -1000, 1000, True),
    }
    if not isinstance(result["text"], str):
        raise ValueError("朗读文本必须是字符串")
    if result["system_action"] not in SYSTEM_ACTIONS:
        raise ValueError("不支持的系统动作")
    return result


def runs_on(program: dict, day: date, weekday_override=None) -> bool:
    """Evaluate a local day, optionally using another day's program pattern.

    Date-string overrides change weekday, monthly day and explicit date matching.
    Explicit dates override monthly dates, which override weekday selection.
    Integer overrides change weekday only. Actual exclusions and validity bounds
    always apply to the real day; remapping does not bypass holiday suppression.
    """
    stamp = day.isoformat()
    if stamp in program["excluded_dates"]:
        return False
    if program["date_from"] and stamp < program["date_from"]:
        return False
    if program["date_to"] and stamp > program["date_to"]:
        return False
    pattern_day = date.fromisoformat(weekday_override) if isinstance(weekday_override, str) else day
    if program["dates"]:
        return pattern_day.isoformat() in program["dates"]
    if program.get("month_days"):
        return pattern_day.day in program["month_days"]
    weekday = weekday_override if type(weekday_override) is int else pattern_day.weekday()
    return weekday in program["weekdays"]


def excluded_at(program: dict, day: date, at: str) -> bool:
    stamp = day.isoformat()
    for window in program.get("exclude_windows", []):
        if not window.get("enabled", True):
            continue
        if window["start"] <= stamp <= window["end"]:
            start, end = window["time_from"], window["time_to"]
            if not start:
                return True
            if start <= end and start <= at <= end:
                return True
            if start > end and (at >= start or at <= end):
                return True
    return False


def validate_exclude_windows(windows):
    if not isinstance(windows, list):
        raise ValueError("禁播时段必须是列表")
    validated_windows = []
    for window in windows:
        if not isinstance(window, dict):
            raise ValueError("禁播时段必须是对象")
        window_start, window_end = _date(window.get("start")), _date(window.get("end"))
        if not window_start or not window_end or window_start > window_end:
            raise ValueError("禁播时段的日期范围无效")
        time_from = _time(window["time_from"]) if window.get("time_from") else ""
        time_to = _time(window["time_to"]) if window.get("time_to") else ""
        if bool(time_from) != bool(time_to):
            raise ValueError("禁播开始和结束时间须同时指定")
        name = window.get("name", "")
        if not isinstance(name, str):
            raise ValueError("禁播时段名称必须是字符串")
        enabled = _boolean(window.get("enabled", True), "禁播时段启用状态")
        validated_windows.append(
            {
                "start": window_start,
                "end": window_end,
                "time_from": time_from,
                "time_to": time_to,
                "name": name,
                "enabled": enabled,
            }
        )
    return validated_windows


def validate_setting(key, value):
    if key == "exclude_windows":
        return validate_exclude_windows(value)
    if key == "calendar_details":
        if not isinstance(value, dict):
            raise ValueError("调休日详细设置必须是日期映射对象")
        normalized = {}
        for actual, details in value.items():
            if not _date(actual) or not isinstance(details, dict):
                raise ValueError("调休日详细设置必须包含有效日期和对象")
            name = details.get("name", "")
            if not isinstance(name, str):
                raise ValueError("调休日名称必须是字符串")
            enabled = _boolean(details.get("enabled", True), "调休日启用状态")
            normalized[actual] = dict(details, name=name, enabled=enabled)
        return normalized
    if key == "calendar_overrides":
        if not isinstance(value, dict):
            raise ValueError("调休日必须是日期映射对象")
        for actual, target in value.items():
            if not _date(actual):
                raise ValueError("调休日来源日期不能为空")
            if target == "disabled" or (type(target) is int and target in range(7)):
                continue
            if not isinstance(target, str) or not _date(target):
                raise ValueError("调休日目标必须是星期 0 至 6、disabled 或 YYYY-MM-DD 日期")
    return value


def active_at(program: dict, scheduled: datetime) -> bool:
    """Check inclusive validity bounds against the actual local occurrence time."""
    actual = scheduled.replace(tzinfo=None).isoformat(timespec="seconds")
    return (not program.get("datetime_from") or actual >= program["datetime_from"]) and (
        not program.get("datetime_to") or actual <= program["datetime_to"]
    )
