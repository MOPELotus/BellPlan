"""Command lifecycle checks use only processes created by these tests."""

import subprocess
import sys
import time
from unittest.mock import Mock, patch

import pytest
from PySide6.QtCore import QCoreApplication

from bellplan.commands import CommandRunner, parse_command


@pytest.fixture
def runner():
    application = QCoreApplication.instance() or QCoreApplication([])
    instance = CommandRunner()
    yield instance
    instance.close()
    application.processEvents()


def test_json_command_preserves_unicode_spaces_and_empty_quoted_argument():
    assert parse_command('["C:\\\\Program Files\\\\工具.exe", "中文 参数", ""]') == [
        "C:\\Program Files\\工具.exe",
        "中文 参数",
        "",
    ]


@pytest.mark.parametrize("text", ["", " ", "[]", '[" "]', "[1]", '["app",null]', '["app"'])
def test_invalid_command_is_rejected(text):
    with pytest.raises(ValueError):
        parse_command(text)


def test_null_arguments_are_rejected():
    with pytest.raises(ValueError):
        parse_command('["app", "\\u0000"]')


def test_native_command_line_uses_platform_rules():
    if sys.platform == "win32":
        text = '"C:\\Program Files\\工具.exe" "中文 参数" ""'
        expected = [r"C:\Program Files\工具.exe", "中文 参数", ""]
    else:
        text = '"/opt/My Tools/app" "中文 参数" ""'
        expected = ["/opt/My Tools/app", "中文 参数", ""]
    assert parse_command(text) == expected


def test_startup_failure_is_reported_without_registering_process(runner):
    changes, logs = [], []
    runner.runChanged.connect(lambda *event: changes.append(event))
    runner.log.connect(lambda *event: logs.append(event))
    with patch("bellplan.commands.subprocess.Popen", side_effect=FileNotFoundError("missing")):
        assert (
            runner.start({"command": ["missing"], "_run_event_id": "event", "name": "Test"})
            is False
        )
    assert changes[0][:2] == ("event", "failed")
    assert logs[0][0] == "error"
    assert runner.snapshot() == []
    assert not runner.timer.isActive()


def test_windows_launch_is_detached_and_never_uses_shell(runner):
    process = Mock(pid=123)
    process.poll.return_value = None
    with patch("bellplan.commands.sys.platform", "win32"):
        with patch("bellplan.commands.subprocess.Popen", return_value=process) as launch:
            assert runner.start({"command": ["tool.exe", "arg"]})
    options = launch.call_args.kwargs
    assert options["shell"] is False
    assert options["creationflags"] & 0x00000008
    assert options["creationflags"] & 0x00000200
    assert options["close_fds"] is True
    assert options["stdin"] == options["stdout"] == options["stderr"] == subprocess.DEVNULL
    assert "start_new_session" not in options
    assert runner.close() == [{"event_id": "", "name": "外部命令", "pid": 123}]
    process.kill.assert_not_called()
    process.terminate.assert_not_called()
    process.send_signal.assert_not_called()
    process.poll.return_value = 0
    runner._reaper.join(timeout=2)
    assert not runner._reaper.is_alive()


def test_real_command_completion_reports_actual_exit_status(runner):
    changes = []
    runner.runChanged.connect(lambda *event: changes.append(event))
    assert runner.start(
        {
            "command": [sys.executable, "-c", "raise SystemExit(7)"],
            "_run_event_id": "exit-7",
            "name": "Fixture",
        }
    )
    process = runner._running[0]["process"]
    process.wait(timeout=5)
    runner._poll()
    assert [event[1] for event in changes] == ["started", "failed"]
    assert "7" in changes[-1][2]
    assert runner.snapshot() == []
    assert not runner.timer.isActive()


def test_close_does_not_terminate_our_child_or_emit_late_notifications(runner):
    changes, logs = [], []
    runner.runChanged.connect(lambda *event: changes.append(event))
    runner.log.connect(lambda *event: logs.append(event))
    assert runner.start(
        {
            "command": [sys.executable, "-c", "import time; time.sleep(0.6)"],
            "_run_event_id": "sleep",
            "name": "Our fixture",
        }
    )
    process = runner._running[0]["process"]
    try:
        pending = runner.close()
        assert pending == [{"event_id": "sleep", "name": "Our fixture", "pid": process.pid}]
        assert process.poll() is None
        before = (len(changes), len(logs))
        process.wait(timeout=5)
        runner._poll()
        QCoreApplication.processEvents()
        assert (len(changes), len(logs)) == before
        assert runner.close() == []
        assert not runner.timer.isActive()
        with pytest.raises(RuntimeError):
            runner.start({"command": [sys.executable]})
    finally:
        if process.poll() is None:
            process.terminate()  # This test's explicitly created, harmless child only.
            process.wait(timeout=5)


def test_qt_timer_observes_successful_process_without_manual_poll(runner):
    changes = []
    runner.runChanged.connect(lambda *event: changes.append(event))
    assert runner.start({"command": [sys.executable, "-c", "pass"], "_run_event_id": "success"})
    process = runner._running[0]["process"]
    try:
        deadline = time.monotonic() + 5
        while len(changes) < 2 and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.01)
        assert [event[1] for event in changes] == ["started", "completed"]
    finally:
        process.wait(timeout=5)


def test_close_retains_external_processes_when_multiple_are_monitored(runner):
    first, second = Mock(pid=1), Mock(pid=2)
    first.poll.return_value = 0
    second.poll.return_value = None
    with patch("bellplan.commands.subprocess.Popen", side_effect=[first, second]):
        runner.start({"command": ["one"], "_run_event_id": "one"})
        runner.start({"command": ["two"], "_run_event_id": "two"})
    assert runner.close() == [{"event_id": "two", "name": "外部命令", "pid": 2}]
    first.kill.assert_not_called()
    second.kill.assert_not_called()
    second.poll.return_value = 0
    runner._reaper.join(timeout=2)
    assert not runner._reaper.is_alive()
