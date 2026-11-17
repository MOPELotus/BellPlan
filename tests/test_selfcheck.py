"""Non-graphical bundle self-check: real installed catalogue and tamper rejection."""

import json
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from bellplan.resources import builtin_media
from bellplan.selfcheck import write_report


def test_installed_bundle_selfcheck_succeeds_without_gui():
    with TemporaryDirectory() as directory:
        destination = Path(directory) / "reports" / "selfcheck.json"
        assert write_report(destination) == 0
        report = json.loads(destination.read_text(encoding="utf-8"))
        media = builtin_media()
        assert report["ok"] is True
        assert report["audio_count"] == len(media) == 119
        assert report["audio_bytes"] == sum(item["size"] for item in media)
        assert report["version"]
        assert "error" not in report


def test_bundle_selfcheck_rejects_same_size_tampering_without_touching_assets():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        media_file = root / "test.mp3"
        original = b"original"
        media_file.write_bytes(b"modified")  # Same length: checksum must catch it.
        relative = "test.mp3"
        manifest = {
            "media_count": 1,
            "media": [{"relative_path": relative, "sha256": sha256(original).hexdigest()}],
        }
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        catalogue = [{"path": str(media_file), "relative_path": relative, "size": len(original)}]
        report_file = root / "reports" / "tamper.json"
        with (
            patch("bellplan.selfcheck.resource_root", return_value=root),
            patch("bellplan.selfcheck.builtin_media", return_value=catalogue),
        ):
            assert write_report(report_file) == 1
        report = json.loads(report_file.read_text(encoding="utf-8"))
        assert report["ok"] is False
        assert report["audio_count"] == 0
        assert "音频校验失败" in report["error"]
        assert relative in report["error"]
