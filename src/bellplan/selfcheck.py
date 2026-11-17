"""Non-graphical bundle integrity check, also run against the Windows EXE in CI."""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from pathlib import Path

from . import __version__
from .resources import builtin_media, resource_root


def write_report(destination: Path) -> int:
    report = {
        "ok": False,
        "version": __version__,
        "frozen": bool(getattr(sys, "frozen", False)),
        "platform": platform.system(),
        "audio_count": 0,
        "audio_bytes": 0,
    }
    try:
        root = resource_root()
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        checksums = {item["relative_path"]: item["sha256"] for item in manifest["media"]}
        media = builtin_media()
        if len(media) != manifest["media_count"]:
            raise ValueError("音频清单数量不一致")
        for item in media:
            path = Path(item["path"])
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            digest = digest.hexdigest()
            if path.stat().st_size != item["size"] or digest != checksums[item["relative_path"]]:
                raise ValueError("音频校验失败：" + item["relative_path"])
            report["audio_count"] += 1
            report["audio_bytes"] += item["size"]
        qml = Path(__file__).parent / "qml"
        for name in ("Main.qml", "CalendarPage.qml", "LivePanel.qml", "BuiltinLibrary.qml"):
            if not (qml / name).is_file():
                raise ValueError("缺少界面文件：" + name)
        report["ok"] = True
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report["error"] = str(exc)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1
