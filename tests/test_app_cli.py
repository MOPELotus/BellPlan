"""CLI diagnostics work on Western Windows consoles and windowed builds."""

import io
import sys

import pytest

from bellplan.app import main, ui_font_family


def test_help_can_print_chinese_on_cp1252_console(monkeypatch):
    buffer = io.BytesIO()
    output = io.TextIOWrapper(buffer, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", output)
    with pytest.raises(SystemExit) as result:
        main(["--help"])
    assert result.value.code == 0
    output.flush()
    text = buffer.getvalue().decode("utf-8")
    assert "--data-dir" in text
    assert "独立数据目录" in text


def test_help_in_windowed_build_without_console(monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    with pytest.raises(SystemExit) as result:
        main(["--help"])
    assert result.value.code == 0


def test_windows_ui_font_uses_installed_chinese_sans_family():
    families = ["SimSun", "Segoe UI", "Microsoft YaHei", "Microsoft YaHei UI"]
    assert ui_font_family(families, "win32", "SimSun") == "Microsoft YaHei UI"
    assert ui_font_family(["Microsoft YaHei"], "win32", "SimSun") == "Microsoft YaHei"


def test_ui_font_never_selects_missing_family():
    assert ui_font_family(["Installed Sans"], "win32", "Installed Sans") == "Installed Sans"
    assert ui_font_family(["Noto Sans CJK SC"], "linux", "Sans") == "Noto Sans CJK SC"
