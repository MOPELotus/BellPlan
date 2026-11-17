"""CLI diagnostics work on Western Windows consoles and windowed builds."""

import io
import sys

import pytest

from bellplan.app import main


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
