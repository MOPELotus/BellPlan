"""External command monitoring without owning or terminating launched programs."""

from __future__ import annotations

import ctypes
import json
import shlex
import subprocess
import sys
import threading
import time
from ctypes import wintypes

from PySide6.QtCore import QObject, QTimer, Signal, Slot


def _validate_arguments(arguments):
    if (
        not isinstance(arguments, list)
        or not arguments
        or any(not isinstance(value, str) or "\0" in value for value in arguments)
        or not arguments[0].strip()
    ):
        raise ValueError("命令必须包含有效的程序路径和字符串参数")
    return list(arguments)


def _windows_split(text):
    """Use the OS command-line rules, preserving quoted paths and empty arguments."""
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    split = shell.CommandLineToArgvW
    split.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    split.restype = ctypes.POINTER(wintypes.LPWSTR)
    free = kernel.LocalFree
    free.argtypes = [ctypes.c_void_p]
    free.restype = ctypes.c_void_p
    count = ctypes.c_int()
    pointer = split(text, ctypes.byref(count))
    if not pointer:
        raise ValueError("无法解析 Windows 命令行")
    try:
        return [pointer[index] for index in range(count.value)]
    finally:
        free(ctypes.cast(pointer, ctypes.c_void_p))


def parse_command(text):
    """Parse a JSON argument array or a native command line, never a shell script."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("命令不能为空")
    text = text.strip()
    if text.startswith("["):
        try:
            arguments = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("命令参数的 JSON 格式不正确") from exc
    elif sys.platform == "win32":
        arguments = _windows_split(text)
    else:
        try:
            arguments = shlex.split(text, posix=True)
        except ValueError as exc:
            raise ValueError("命令行引号不完整") from exc
    return _validate_arguments(arguments)


def _reap_processes(processes):
    """Collect our detached children without Qt callbacks or blocking application exit."""
    pending = list(processes)
    while pending:
        active = []
        for process in pending:
            try:
                if process.poll() is None:
                    active.append(process)
            except OSError:
                pass
        pending = active
        if pending:
            time.sleep(0.25)


class CommandRunner(QObject):
    """Monitor detached OS processes while this application is running.

    Closing the runner stops observation. It never terminates programs, sends
    close-window messages or reports detached programs as successfully completed.
    """

    runChanged = Signal(str, str, str)
    log = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._closed = False
        self._running = []
        self._reaper = None
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._poll)

    def _publish(self, item, status, message):
        if self._closed:
            return
        if item["event_id"]:
            self.runChanged.emit(item["event_id"], status, message)
        if not self._closed:
            self.log.emit("error" if status == "failed" else "info", message)

    def start(self, program):
        if self._closed:
            raise RuntimeError("命令执行服务已关闭")
        arguments = _validate_arguments(program.get("command"))
        item = {
            "event_id": str(program.get("_run_event_id") or ""),
            "name": str(program.get("name") or "外部命令"),
        }
        options = {
            "shell": False,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
        }
        if sys.platform == "win32":
            options["creationflags"] = getattr(
                subprocess, "DETACHED_PROCESS", 0x00000008
            ) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        else:
            options["start_new_session"] = True
        try:
            process = subprocess.Popen(arguments, **options)
        except (OSError, ValueError) as exc:
            self._publish(item, "failed", f"命令无法启动：{item['name']}；{exc}")
            return False
        item["process"] = process
        self._running.append(item)
        self._publish(item, "started", f"命令已启动：{item['name']}；PID {process.pid}")
        if not self._closed:
            self.timer.start()
        return True

    @Slot()
    def _poll(self):
        if self._closed:
            return
        for item in tuple(self._running):
            if self._closed:
                return
            try:
                code = item["process"].poll()
            except OSError as exc:
                self._running.remove(item)
                self._publish(item, "failed", f"无法读取命令状态：{item['name']}；{exc}")
                continue
            if code is None:
                continue
            self._running.remove(item)
            self._publish(
                item,
                "completed" if code == 0 else "failed",
                f"命令结束：{item['name']}；退出码 {code}",
            )
        if not self._running:
            self.timer.stop()

    def snapshot(self):
        return [
            {"event_id": item["event_id"], "name": item["name"], "pid": item["process"].pid}
            for item in self._running
        ]

    def close(self):
        """Return still-running processes; cease notifications without killing them."""
        if self._closed:
            return []
        self._closed = True
        self.timer.stop()
        running = []
        detached = []
        for item in self._running:
            process = item["process"]
            try:
                active = process.poll() is None
            except OSError:
                active = True
            if active:
                running.append(
                    {"event_id": item["event_id"], "name": item["name"], "pid": process.pid}
                )
                detached.append(process)
        self._running.clear()
        if detached:
            self._reaper = threading.Thread(
                target=_reap_processes,
                args=(tuple(detached),),
                name="BellPlan-command-reaper",
                daemon=True,
            )
            self._reaper.start()
        return running
