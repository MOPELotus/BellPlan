"""Persistent immediate-play list, independent of scheduling and UI frameworks."""

from __future__ import annotations

import math
import random
from copy import deepcopy
from pathlib import PurePosixPath

_DEFAULT = {
    "files": [],
    "selectedIndex": -1,
    "mode": "list",
    "volume": 80,
    "stop_mode": "playlist",
    "repeat": 1,
    "track_limit": 0,
    "duration": 0,
    "output_device": "",
}
_CONFIGURABLE = frozenset(_DEFAULT) - {"files"}


def _integer(value, label, minimum=0, maximum=None):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"{label}无效")
    return value


class LivePlaylist:
    """Each successful change persists a complete, validated snapshot atomically.

    Paths are retained as entered and are not opened or expanded here. The playback
    service resolves directories and reports missing or unsupported media.
    """

    def __init__(self, store):
        self.store = store
        saved = store.get_setting("live", None)
        self._data = self._validate(deepcopy(_DEFAULT) if saved is None else saved)

    @staticmethod
    def _validate(record):
        if not isinstance(record, dict) or set(record) - set(_DEFAULT):
            raise ValueError("立即播列表设置无效")
        result = dict(deepcopy(_DEFAULT), **deepcopy(record))
        paths = result["files"]
        if not isinstance(paths, list) or any(
            not isinstance(x, str) or not x.strip() for x in paths
        ):
            raise ValueError("媒体路径必须为非空字符串列表")
        if len(set(paths)) != len(paths):
            raise ValueError("立即播列表中存在重复路径")
        selected = _integer(result["selectedIndex"], "选中项", -1)
        if selected >= len(paths) or (not paths and selected != -1):
            raise ValueError("选中项超出列表范围")
        if result["mode"] not in ("list", "single", "random"):
            raise ValueError("立即播模式无效")
        if result["stop_mode"] not in ("playlist", "tracks", "duration"):
            raise ValueError("停止方式无效")
        _integer(result["volume"], "音量", 0, 100)
        _integer(result["repeat"], "循环次数")
        _integer(result["track_limit"], "播放首数")
        duration = result["duration"]
        if (
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or not math.isfinite(duration)
            or duration < 0
        ):
            raise ValueError("播放秒数必须为非负有限数字")
        if not isinstance(result["output_device"], str):
            raise ValueError("输出设备无效")
        return result

    def snapshot(self):
        return deepcopy(self._data)

    def _commit(self, candidate):
        valid = self._validate(candidate)
        self.store.set_setting("live", valid)
        self._data = valid
        return self.snapshot()

    def _index(self, value):
        _integer(value, "列表索引")
        if value >= len(self._data["files"]):
            raise ValueError("列表索引超出范围")
        return value

    def add(self, paths):
        if not isinstance(paths, (list, tuple)) or any(not isinstance(x, str) for x in paths):
            raise ValueError("媒体路径必须是字符串列表")
        data = self.snapshot()
        known = set(data["files"])
        for path in paths:
            if not path.strip() or path in known:
                continue
            data["files"].append(path)
            known.add(path)
        if data["files"] and data["selectedIndex"] == -1:
            data["selectedIndex"] = 0
        return self._commit(data)

    def remove(self, index):
        index = self._index(index)
        data = self.snapshot()
        data["files"].pop(index)
        selected = data["selectedIndex"]
        if selected > index:
            selected -= 1
        elif selected == index:
            selected = min(index, len(data["files"]) - 1)
        data["selectedIndex"] = selected if data["files"] else -1
        return self._commit(data)

    def clear(self):
        data = self.snapshot()
        data.update(files=[], selectedIndex=-1)
        return self._commit(data)

    def move(self, index, delta):
        index = self._index(index)
        if type(delta) is not int or delta not in (-1, 1):
            raise ValueError("移动方向必须为 -1 或 1")
        target = index + delta
        if not 0 <= target < len(self._data["files"]):
            raise ValueError("无法移动到列表范围外")
        data = self.snapshot()
        data["files"][index], data["files"][target] = data["files"][target], data["files"][index]
        if data["selectedIndex"] == index:
            data["selectedIndex"] = target
        elif data["selectedIndex"] == target:
            data["selectedIndex"] = index
        return self._commit(data)

    def _reorder(self, files):
        data = self.snapshot()
        selected = data["files"][data["selectedIndex"]] if data["selectedIndex"] >= 0 else None
        data["files"] = files
        data["selectedIndex"] = files.index(selected) if selected is not None else -1
        return self._commit(data)

    def sort(self, descending=False, by="name"):
        if type(descending) is not bool:
            raise ValueError("排序方向必须为布尔值")
        if by not in ("name", "type"):
            raise ValueError("排序方式必须为 name 或 type")

        def type_key(path):
            # Both separators are accepted when a Windows backup is opened on Linux.
            name = path.replace("\\", "/").rsplit("/", 1)[-1]
            return PurePosixPath(name).suffix.casefold(), name.casefold(), path.casefold()

        key = type_key if by == "type" else str.casefold
        return self._reorder(sorted(self._data["files"], key=key, reverse=descending))

    def shuffle(self):
        files = list(self._data["files"])
        random.shuffle(files)
        return self._reorder(files)

    def configure(self, changes):
        if not isinstance(changes, dict) or set(changes) - _CONFIGURABLE:
            raise ValueError("不支持的立即播设置")
        return self._commit(dict(self.snapshot(), **changes))

    def build_program(self, index=None):
        """Construct a playback record; no plan or scheduler occurrence is created."""
        if not self._data["files"]:
            raise ValueError("立即播列表为空")
        if index is None:
            index = max(0, self._data["selectedIndex"])
        index = self._index(index)
        data = self.configure({"selectedIndex": index})
        paths = data["files"]
        files = [paths[index]] if data["mode"] == "single" else paths[index:] + paths[:index]
        return {
            "action": "audio",
            "name": "立即播",
            "files": files,
            "shuffle": data["mode"] == "random",
            "volume": data["volume"],
            "stop_mode": data["stop_mode"],
            "repeat": data["repeat"],
            "track_limit": data["track_limit"],
            "duration": data["duration"],
            "output_device": data["output_device"],
            "playback_mode": "interrupt",
        }
