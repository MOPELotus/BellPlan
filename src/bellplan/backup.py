"""Scope-aware backups and side-effect-free validation before database import."""

from __future__ import annotations

from copy import deepcopy

from .domain import validate_setting
from .live import LivePlaylist

SECTIONS = frozenset({"programs", "holidays", "calendar", "live", "settings"})
_CALENDAR_KEYS = frozenset({"exclude_windows", "calendar_overrides", "calendar_details"})
_UI_DEFAULTS = {
    "closeToTray": True,
    "startAtLogin": False,
    "duckVolume": 30,
    "theme": "light",
    "defaultDevice": "",
    "catchupSeconds": 30,
}
_SETTING_SECTIONS = {
    "exclude_windows": "holidays",
    "calendar_overrides": "calendar",
    "calendar_details": "calendar",
    "live": "live",
    "ui": "settings",
}


def _backup(data):
    if not isinstance(data, dict):
        raise ValueError("备份必须是对象")
    if not isinstance(data.get("settings", {}), dict):
        raise ValueError("备份设置必须是对象")
    return deepcopy(data)


def select_sections(data, sections=None):
    """Keep only explicitly selected groups, without modifying the input.

    Calendar settings are stored separately from UI preferences. Removing their
    duplicate UI keys prevents a partial import from carrying excluded groups.
    """
    if sections is None:
        selected = SECTIONS
    else:
        if not isinstance(sections, list) or any(
            not isinstance(x, str) or x not in SECTIONS for x in sections
        ):
            raise ValueError("备份范围无效")
        selected = frozenset(sections)
    result = _backup(data)
    if "programs" not in selected:
        result["plans"], result["programs"] = [], []
    result["settings"] = {
        key: value
        for key, value in result.get("settings", {}).items()
        if key in _SETTING_SECTIONS and _SETTING_SECTIONS[key] in selected
    }
    if "ui" in result["settings"]:
        ui = result["settings"]["ui"]
        if not isinstance(ui, dict):
            raise ValueError("界面设置必须是对象")
        result["settings"]["ui"] = {
            key: value for key, value in ui.items() if key not in _CALENDAR_KEYS
        }
    return result


def _validate_ui(record):
    if not isinstance(record, dict):
        raise ValueError("界面设置必须是对象")
    if set(record) - set(_UI_DEFAULTS):
        raise ValueError("备份包含不支持的界面设置")
    result = deepcopy(record)
    for key in ("closeToTray", "startAtLogin"):
        if key in result and type(result[key]) is not bool:
            raise ValueError(f"{key} 必须是布尔值")
    for key, high in (("duckVolume", 100), ("catchupSeconds", 300)):
        if key in result and (type(result[key]) is not int or not 0 <= result[key] <= high):
            raise ValueError(f"{key} 必须是 0 至 {high} 的整数")
    if "defaultDevice" in result and not isinstance(result["defaultDevice"], str):
        raise ValueError("默认音频设备必须是字符串")
    if "theme" in result and result["theme"] != "light":
        raise ValueError("当前版本仅支持浅色界面")
    return result


def prepare_import(data, current_ui):
    """Validate all imported settings before any storage mutation or action.

    Executable programs remain disabled until the user explicitly enables them.
    Importing preferences never changes Windows login-startup registration.
    """
    result = _backup(data)
    if not isinstance(current_ui, dict):
        raise ValueError("当前界面设置必须是对象")
    settings = result.get("settings", {})
    if any(not isinstance(key, str) or key not in _SETTING_SECTIONS for key in settings):
        raise ValueError("备份包含不支持的设置")
    validated = {}
    for key, value in settings.items():
        if key == "ui":
            if not isinstance(value, dict):
                raise ValueError("界面设置必须是对象")
            incoming = _validate_ui({k: v for k, v in value.items() if k not in _CALENDAR_KEYS})
            current = {k: deepcopy(v) for k, v in current_ui.items() if k in _UI_DEFAULTS}
            merged = {**_UI_DEFAULTS, **_validate_ui(current), **incoming}
            merged["startAtLogin"] = current.get("startAtLogin", _UI_DEFAULTS["startAtLogin"])
            validated[key] = merged
        elif key == "live":
            validated[key] = LivePlaylist._validate(value)
        else:
            validated[key] = validate_setting(key, value)
    result["settings"] = validated
    programs = result.get("programs")
    if not isinstance(programs, list):
        raise ValueError("备份节目必须是列表")
    for program in programs:
        if not isinstance(program, dict):
            raise ValueError("备份节目必须是对象")
        if program.get("action") in ("command", "system"):
            program["enabled"] = False
    return result
