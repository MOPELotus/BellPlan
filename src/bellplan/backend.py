"""Qt bridge: storage, scheduling and playback remain separate services."""

from __future__ import annotations

import json
import logging
import os
import shlex
import sys
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import Property, QObject, QProcess, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QMessageBox

from . import platform_services
from .backup import prepare_import, select_sections
from .domain import validate_setting
from .live import LivePlaylist
from .playback import Playback
from .resources import builtin_media, decode_sources, encode_sources, resource_root
from .scheduler import Scheduler
from .service import SchedulerService
from .storage import Store

DEFAULT_SETTINGS = {
    "closeToTray": True,
    "startAtLogin": False,
    "duckVolume": 30,
    "exclude_windows": [],
    "calendar_overrides": {},
    "theme": "light",
    "defaultDevice": "",
    "catchupSeconds": 30,
    "calendar_details": {},
}


def ui_program(program):
    item = dict(program)
    item["kind"] = {"speech": "tts"}.get(item["action"], item["action"])
    if item["action"] == "system":
        item["kind"] = {"restart": "reboot"}.get(item["system_action"], item["system_action"])
    item["start_date"], item["end_date"] = item["date_from"], item["date_to"]
    return item


class Backend(QObject):
    stateJsonChanged = Signal()
    fileSelectionJsonChanged = Signal()
    errorOccurred = Signal(str)
    settingsChanged = Signal()

    def __init__(self, data_dir: Path, parent=None, *, start_service=False):
        super().__init__(parent)
        self.closed = False
        self.system_volume = {
            "available": sys.platform == "win32",
            "volume": 0,
            "muted": False,
            "device": "",
            "error": "尚未读取" if sys.platform == "win32" else "此操作仅支持 Windows 10/11",
        }
        self.data_dir = data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        self.store = Store(data_dir / "bellplan.sqlite3")
        self.settings = dict(DEFAULT_SETTINGS, **self.store.get_setting("ui", {}))
        if not self.store.list_plans():
            self.store.save_plan({"name": "默认节目单"})
        self.scheduler = Scheduler(self.store, catchup_seconds=self.settings["catchupSeconds"])
        self.live = LivePlaylist(self.store)
        self.enabled = self.store.get_setting("schedulerEnabled", True)
        self.player = Playback(self)
        self.publish_timer = QTimer(self)
        self.publish_timer.setSingleShot(True)
        self.publish_timer.timeout.connect(self.refresh)
        self.player.changed.connect(self.request_refresh)
        self.player.log.connect(self.log)
        self.player.runChanged.connect(self._run_changed)
        self.player.set_duck_volume(self.settings["duckVolume"])
        self.logs = []
        self._state = "{}"
        self._next_cache = None
        self._revision = 0
        self._files = "[]"
        self.renderer = "等待图形设备初始化"
        self.builtin_media = builtin_media()
        self._builtin_uris = {
            "builtin://" + x["relative_path"]: x["path"] for x in self.builtin_media
        }
        for item in self.builtin_media:
            for alias in item.get("aliases", []):
                self._builtin_uris["builtin://" + alias] = item["path"]
        self._run_timings = {}
        self.startup_status = {
            "supported": sys.platform == "win32",
            "enabled": False,
            "matchesCurrentPath": False,
            "error": "尚未读取",
        }
        self.service = None
        self.processes = []
        self.power_pending = None
        self.power_timer = QTimer(self)
        self.power_timer.setSingleShot(True)
        self.power_timer.timeout.connect(self._execute_power)
        self.logger = logging.getLogger("bellplan." + str(id(self)))
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        handler = RotatingFileHandler(
            data_dir / "activity.log", maxBytes=2_000_000, backupCount=4, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        self.logger.addHandler(handler)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)
        self.startup_timer = QTimer(self)
        self.startup_timer.timeout.connect(self.check_startup)
        if start_service:
            self.service = SchedulerService(
                data_dir / "bellplan.sqlite3",
                self,
                enabled=self.enabled,
                catchup_seconds=self.settings["catchupSeconds"],
            )
            self.service.due.connect(self.handle_due)
            self.service.diagnostic.connect(self.log)
            self.service.start()
            self.startup_timer.start(30000)
            self.check_startup()
        self.log("info", "调度服务已启动")
        self.refresh()

    @Property(str, notify=stateJsonChanged)
    def stateJson(self):
        return self._state

    @Property(str, notify=fileSelectionJsonChanged)
    def fileSelectionJson(self):
        return self._files

    @Slot(str, str)
    def log(self, level, message):
        if hasattr(self, "logger"):
            getattr(self.logger, level if level in ("error", "warning", "info") else "info")(
                message
            )
        if hasattr(self, "logs"):
            self.logs.insert(
                0, {"time": datetime.now().strftime("%H:%M:%S"), "level": level, "message": message}
            )
            del self.logs[300:]
        if hasattr(self, "_state"):
            self.request_refresh()

    @Slot()
    def request_refresh(self):
        if self.closed:
            return
        if not self.publish_timer.isActive():
            self.publish_timer.start(150)

    def _next(self, programs=None, plans=None):
        now = datetime.now()
        cached = self._next_cache
        if cached and cached[0] == self._revision and cached[3] <= now < cached[2]:
            return cached[1]
        events = self.scheduler.next_occurrences(now, days=32, limit=1)
        if not events:
            result = "暂无待播节目"
            expires = now + timedelta(seconds=60)
        else:
            event = events[0]
            at = datetime.fromisoformat(event["scheduled_at"])
            result = f"{at:%m-%d %H:%M:%S} · {event['program']['name']}"
            expires = max(at, now + timedelta(milliseconds=1))
        self._next_cache = (self._revision, result, expires, now)
        return result

    @Slot(str, str, str)
    def _run_changed(self, event_id, status, message):
        if not event_id:
            return
        if status == "started" and event_id in self._run_timings:
            elapsed = max(
                0.0, (datetime.now() - self._run_timings[event_id]).total_seconds() * 1000
            )
            message += f"；启动延迟 {elapsed:.0f} 毫秒"
        if status in ("completed", "failed", "cancelled"):
            self._run_timings.pop(event_id, None)
        if status in ("started", "completed", "failed", "cancelled"):
            self.store.finish_run(event_id, status, message)
            self.log(
                "error" if status == "failed" else "info",
                f"任务{ {'started': '开始', 'completed': '完成', 'failed': '失败', 'cancelled': '已取消'}[status] }：{message}",
            )

    @Slot()
    def refresh(self):
        if self.closed:
            return
        if not hasattr(self, "logs"):
            return
        programs, plans = self.store.list_programs(), self.store.list_plans()
        ui_programs = [ui_program(x) for x in programs]
        for program in ui_programs:
            program["files"] = [self._builtin_uris.get(p, p) for p in program["files"]]
        live = self.live.snapshot()
        live["files"] = [self._builtin_uris.get(p, p) for p in live["files"]]
        self._state = json.dumps(
            {
                "programs": ui_programs,
                "plans": plans,
                "logs": self.logs,
                "playing": self.player.state(),
                "settings": self.settings,
                "schedulerEnabled": self.enabled,
                "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "next": self._next(programs, plans) if self.enabled else "定时调度已暂停",
                "dataPath": str(self.data_dir),
                "devices": self.player.devices(),
                "renderer": self.renderer,
                "powerPending": self.power_pending,
                "runs": self.store.list_runs(),
                "live": live,
                "builtinMedia": self.builtin_media,
                "systemVolume": self.system_volume,
                "startupStatus": self.startup_status,
                "schedulerService": {
                    "running": bool(self.service and self.service.is_running),
                    "intervalMs": 100 if self.service else 1000,
                },
            },
            ensure_ascii=False,
        )
        self.stateJsonChanged.emit()

    @Slot(dict)
    def handle_due(self, event):
        if self.closed:
            return
        program = event["program"]
        current = self.store.get_program(program["id"])
        plans = {p["id"] for p in self.store.list_plans() if p["enabled"]}
        if (
            not self.enabled
            or not current
            or not current["enabled"]
            or current["plan_id"] not in plans
        ):
            self.store.finish_run(event["event_id"], "cancelled", "执行前节目或节目单已停用")
            return
        scheduled = datetime.fromisoformat(event["scheduled_at"])
        self._run_timings[event["event_id"]] = scheduled.replace(tzinfo=None)
        lag = event.get(
            "lateness_ms",
            max(0.0, (datetime.now() - scheduled.replace(tzinfo=None)).total_seconds() * 1000),
        )
        self.log("info", f"定时任务到点：{program['name']}；调度延迟 {lag:.0f} 毫秒")
        try:
            self._execute(
                dict(program, _run_event_id=event["event_id"], _scheduled_at=event["scheduled_at"]),
                immediate=False,
            )
        except Exception as exc:
            self._run_changed(event["event_id"], "failed", str(exc))
            self.log("error", f"任务失败：{exc}")

    @Slot()
    def handle_resume(self):
        if self.closed:
            return
        self._next_cache = None
        self.log("info", "系统已恢复运行，检查补播窗口和输出设备")
        if self.service:
            self.service.notify_resume()
        self.handle_devices_changed()
        self.check_startup()

    @Slot()
    def handle_devices_changed(self):
        if not self.closed and hasattr(self.player, "_check_devices"):
            self.player._check_devices()
            self.request_refresh()

    @Slot()
    def check_startup(self):
        if self.closed:
            return
        status = platform_services.get_startup_status()
        if (
            self.settings.get("startAtLogin")
            and status.get("supported")
            and not status.get("matchesCurrentPath")
        ):
            try:
                previous = status
                status = platform_services.repair_start_at_login()
                if status.get("matchesCurrentPath") and not previous.get("matchesCurrentPath"):
                    self.log("info", "登录启动路径已修复")
            except OSError as exc:
                status["error"] = str(exc)
        self.startup_status = status
        self.request_refresh()

    @Slot()
    def tick(self):
        try:
            if not self.service:
                if self.enabled:
                    for event in self.scheduler.poll(datetime.now()):
                        self.handle_due(event)
                else:
                    self.scheduler.reset(datetime.now())
            self.refresh()
        except Exception as exc:
            self.log("error", f"调度异常：{exc}")

    def _normalize_program(self, payload):
        old = self.store.get_program(payload.get("id")) if payload.get("id") else {}
        result = dict(old or {}, **payload)
        kind = result.pop("kind", ui_program(old)["kind"] if old else "audio")
        result["action"] = {"tts": "speech"}.get(kind, kind)
        if kind in ("shutdown", "reboot", "stop", "volume", "lock", "sleep"):
            result["action"] = "system"
            result["system_action"] = {"reboot": "restart"}.get(kind, kind)
        result["date_from"] = result.pop("start_date", result.get("date_from", ""))
        result["date_to"] = result.pop("end_date", result.get("date_to", ""))
        if not result.get("plan_id"):
            result["plan_id"] = self.store.list_plans()[0]["id"]
        if result["action"] == "command" and ("text" in payload or not result.get("command")):
            text = result.get("text", "").strip()
            try:
                command = json.loads(text)
            except json.JSONDecodeError:
                command = shlex.split(text, posix=os.name != "nt")
            if (
                not isinstance(command, list)
                or not all(isinstance(x, str) for x in command)
                or not command
            ):
                raise ValueError('命令请填写非空 JSON 参数数组，例如 ["notepad.exe"]')
            result["command"] = command
        result["files"] = encode_sources(result.get("files", []))
        return result

    def _confirm_action(self, program):
        if program.get("action") == "command" or (
            program.get("action") == "system"
            and program.get("system_action") in ("shutdown", "restart", "sleep", "lock")
        ):
            return (
                QMessageBox.question(
                    None,
                    "确认节目动作",
                    f"节目「{program['name']}」将执行命令或系统动作。保存并启用此操作？",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                == QMessageBox.Yes
            )
        return True

    def _execute(self, program, immediate=False):
        program = dict(program)
        program["files"] = decode_sources(program.get("files", []))
        if not program.get("output_device"):
            program["output_device"] = self.settings.get("defaultDevice", "")
        action = program["action"]
        if action in ("audio", "speech"):
            self.player.play(program, immediate=immediate)
        elif action == "command":
            args = program.get("command", [])
            if not args:
                raise ValueError("命令参数为空")
            proc = QProcess(self)
            proc.setProgram(args[0])
            proc.setArguments(args[1:])
            proc.started.connect(
                lambda: self._run_changed(
                    program.get("_run_event_id", ""), "started", program["name"]
                )
            )
            proc.finished.connect(
                lambda code, status: self._run_changed(
                    program.get("_run_event_id", ""),
                    "completed" if code == 0 else "failed",
                    f"{program['name']} 退出码 {code}",
                )
            )
            proc.errorOccurred.connect(
                lambda error: self._run_changed(
                    program.get("_run_event_id", ""), "failed", proc.errorString()
                )
            )
            proc.finished.connect(
                lambda code, status: self.log(
                    "info" if code == 0 else "error", f"命令结束：{program['name']}，退出码 {code}"
                )
            )
            proc.errorOccurred.connect(
                lambda error: self.log("error", f"命令无法启动：{proc.errorString()}")
            )
            proc.finished.connect(lambda *_: self._release_process(proc))
            self.processes.append(proc)
            proc.start()
            self.log("info", f"开始命令：{program['name']}")
        elif action == "system":
            self._run_changed(program.get("_run_event_id", ""), "started", program["name"])
            operation = program["system_action"]
            if operation == "stop":
                self.player.stop()
            elif operation == "volume":
                self.player.set_volume(program["volume"])
            elif operation in ("shutdown", "restart"):
                if sys.platform != "win32":
                    raise ValueError("系统关机与重启仅在 Windows 上执行")
                if self.power_pending:
                    self._run_changed(
                        self.power_pending.get("event_id", ""), "cancelled", "已被新的系统操作替换"
                    )
                self.power_pending = {
                    "action": operation,
                    "name": program["name"],
                    "event_id": program.get("_run_event_id", ""),
                    "at": (datetime.now() + timedelta(seconds=60)).isoformat(),
                }
                self.power_timer.start(60000)
                self.log("warning", "系统动作将在 60 秒后执行，可在设置中取消")
            elif operation == "lock":
                if sys.platform != "win32":
                    raise ValueError("锁屏仅在 Windows 上执行")
                import ctypes

                if not ctypes.windll.user32.LockWorkStation():
                    raise OSError("锁屏失败")
            elif operation == "sleep":
                raise ValueError("休眠尚未验证，暂不执行")
            if operation in ("stop", "volume", "lock"):
                self._run_changed(program.get("_run_event_id", ""), "completed", program["name"])

    def _release_process(self, proc):
        if proc in self.processes:
            self.processes.remove(proc)
        proc.deleteLater()

    def _execute_power(self):
        if self.power_pending:
            operation = self.power_pending["action"]
            event_id = self.power_pending.get("event_id", "")
            self.power_pending = None
            ok = QProcess.startDetached(
                "shutdown.exe", ["/s" if operation == "shutdown" else "/r", "/t", "0"]
            )[0]
            self._run_changed(
                event_id,
                "completed" if ok else "failed",
                "系统操作已提交" if ok else "系统操作启动失败",
            )
            self.log("warning" if ok else "error", "已提交系统操作" if ok else "系统操作启动失败")
            self.refresh()

    def _save_settings(self, incoming):
        settings = dict(self.settings, **incoming)
        settings["exclude_windows"] = validate_setting(
            "exclude_windows", settings.get("exclude_windows", [])
        )
        settings["calendar_overrides"] = validate_setting(
            "calendar_overrides", settings.get("calendar_overrides", {})
        )
        settings["duckVolume"] = int(settings.get("duckVolume", 30))
        if not 0 <= settings["duckVolume"] <= 100:
            raise ValueError("压低音量必须为 0–100")
        settings["calendar_details"] = validate_setting(
            "calendar_details", settings.get("calendar_details", {})
        )
        if settings["startAtLogin"] != self.settings["startAtLogin"]:
            self._set_startup(settings["startAtLogin"])
        with self.store.connection:
            # Metadata updates share one transaction; no partial preference writes.
            for key, value in [
                ("ui", settings),
                ("exclude_windows", settings["exclude_windows"]),
                ("calendar_overrides", settings["calendar_overrides"]),
                ("calendar_details", settings["calendar_details"]),
            ]:
                self.store.connection.execute(
                    "INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    ("setting:" + key, json.dumps(value, ensure_ascii=False)),
                )
        self.settings = settings
        self.player.set_duck_volume(settings["duckVolume"])
        self.check_startup()
        self.settingsChanged.emit()

    def _set_startup(self, enabled):
        platform_services.set_start_at_login(enabled)

    def _live_action(self, action, payload):
        if action in ("live_add_files", "live_add_directory"):
            if action == "live_add_files":
                paths, _ = QFileDialog.getOpenFileNames(
                    None,
                    "添加音频文件",
                    "",
                    "音频文件 (*.mp3 *.wav *.flac *.ogg *.m4a *.aac *.wma *.ape *.opus *.mp2 *.tta *.tak *.wv);;所有文件 (*)",
                )
            else:
                folder = QFileDialog.getExistingDirectory(None, "添加音频目录")
                paths = [folder] if folder else []
            self.live.add(encode_sources(paths))
        elif action == "live_add_builtin":
            known = {x["path"] for x in self.builtin_media}
            paths = payload["paths"]
            if not isinstance(paths, list) or any(x not in known for x in paths):
                raise ValueError("无效的内置音频路径")
            self.live.add(encode_sources(paths))
        elif action == "live_remove":
            self.live.remove(payload["index"])
        elif action == "live_clear":
            self.live.clear()
        elif action == "live_move":
            self.live.move(payload["index"], payload["delta"])
        elif action == "live_sort":
            self.live.sort(payload.get("descending", False))
        elif action == "live_shuffle":
            self.live.shuffle()
        elif action == "live_save_settings":
            self.live.configure(payload)
        elif action == "live_play":
            program = self.live.build_program(payload.get("index"))
            program["files"] = decode_sources(program["files"])
            if not program.get("output_device"):
                program["output_device"] = self.settings.get("defaultDevice", "")
            self.player.play(program, immediate=True)
        elif action == "live_open_folder":
            files = decode_sources(self.live.snapshot()["files"])
            index = payload["index"]
            if type(index) is not int or not 0 <= index < len(files):
                raise ValueError("请选择文件")
            path = Path(files[index])
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(path if path.is_dir() else path.parent))
            )
        else:
            raise ValueError("不支持的立即播操作")

    @Slot(str, str, result=str)
    def dispatch(self, action, encoded="{}"):
        try:
            self._revision += 1
            p = json.loads(encoded or "{}")
            if not isinstance(p, dict):
                raise ValueError("操作参数必须为对象")
            if action in ("add_program", "update_program"):
                record = self._normalize_program(p)
                if not self._confirm_action(record):
                    return json.dumps({"ok": False, "cancelled": True})
                self.store.save_program(record)
                self.log("info", f"节目已保存：{record['name']}")
            elif action == "delete_program":
                self.store.delete_program(p["id"])
            elif action == "move_program":
                self.store.move_program(p["id"], p["delta"])
            elif action == "duplicate_program":
                self.store.duplicate_program(p["id"])
            elif action == "toggle_program":
                record = self.store.get_program(p["id"])
                record["enabled"] = p.get("enabled", not record["enabled"])
                if record["enabled"] and not self._confirm_action(record):
                    return json.dumps({"ok": False, "cancelled": True})
                self.store.save_program(record)
            elif action in ("add_plan", "update_plan", "toggle_plan"):
                record = next((x for x in self.store.list_plans() if x["id"] == p.get("id")), {})
                record = dict(record, **p)
                if action == "toggle_plan" and "enabled" not in p:
                    record["enabled"] = not record.get("enabled", True)
                self.store.save_plan(record)
            elif action == "delete_plan":
                if len(self.store.list_plans()) <= 1:
                    raise ValueError("请至少保留一个节目单")
                if self.store.list_programs(p["id"]):
                    raise ValueError("节目单内仍有节目，请先移动或删除节目")
                self.store.delete_plan(p["id"])
            elif action == "set_scheduler":
                self.enabled = bool(p.get("enabled", not self.enabled))
                self.store.set_setting("schedulerEnabled", self.enabled)
                self.scheduler.reset(datetime.now())
                if self.service:
                    self.service.set_enabled(self.enabled)
                self.log("info", "定时调度已开启" if self.enabled else "定时调度已暂停")
            elif action == "play_program":
                record = self.store.get_program(p["id"])
                if not record:
                    raise ValueError("节目不存在")
                if self._confirm_action(record):
                    self._execute(record, immediate=True)
            elif action == "seek":
                self.player.seek(float(p["position"]))
            elif action == "live_stop":
                self.player.stop_immediate()
            elif action == "stop_scheduled":
                self.player.stop_scheduled()
            elif action.startswith("live_"):
                self._live_action(action, p)
            elif action in ("pause", "resume", "stop"):
                getattr(self.player, action)()
            elif action == "set_volume":
                self.player.set_volume(int(p["volume"]))
            elif action == "open_files":
                files, _ = QFileDialog.getOpenFileNames(
                    None,
                    "选择音频文件",
                    "",
                    "媒体文件 (*.mp3 *.wav *.flac *.ogg *.m4a *.aac *.wma *.ape *.opus *.wv *.tta *.tak);;所有文件 (*)",
                )
                self._files = json.dumps(files, ensure_ascii=False)
                self.fileSelectionJsonChanged.emit()
            elif action == "open_directory":
                folder = QFileDialog.getExistingDirectory(None, "选择媒体目录")
                self._files = json.dumps([folder] if folder else [], ensure_ascii=False)
                self.fileSelectionJsonChanged.emit()
            elif action == "preview_builtin":
                path = p["path"]
                item = next((x for x in self.builtin_media if x["path"] == path), None)
                if item is None:
                    raise ValueError("内置音频不存在")
                self.player.play(
                    {
                        "action": "audio",
                        "name": item["name"],
                        "files": [path],
                        "repeat": 1,
                        "volume": 60,
                    },
                    immediate=True,
                )
            elif action in ("get_system_volume", "set_system_volume", "set_system_mute"):
                try:
                    if action == "get_system_volume":
                        result = platform_services.get_system_volume()
                    elif action == "set_system_volume":
                        result = platform_services.set_system_volume(p["volume"])
                    else:
                        result = platform_services.set_system_mute(p["muted"])
                    self.system_volume = dict(result, available=True, error="")
                except (ValueError, OSError) as exc:
                    self.system_volume = dict(self.system_volume, error=str(exc))
                    raise
            elif action == "get_startup_status":
                self.check_startup()
            elif action == "repair_startup":
                if not self.settings.get("startAtLogin"):
                    raise ValueError("请先启用登录启动")
                self.startup_status = platform_services.repair_start_at_login()
            elif action == "lock_workstation":
                platform_services.lock_workstation()
            elif action == "open_help":
                QDesktopServices.openUrl(
                    QUrl.fromLocalFile(str(resource_root() / "help" / "index.html"))
                )
            elif action == "open_log":
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.data_dir / "activity.log")))
            elif action == "open_data":
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.data_dir)))
            elif action == "save_settings":
                self._save_settings(p)
            elif action == "cancel_power":
                self.power_timer.stop()
                if self.power_pending:
                    self._run_changed(
                        self.power_pending.get("event_id", ""), "cancelled", "用户取消系统操作"
                    )
                self.power_pending = None
                self.log("info", "已取消系统倒计时")
            elif action == "export":
                filename, _ = QFileDialog.getSaveFileName(
                    None, "导出备份", "BellPlan-backup.json", "JSON (*.json)"
                )
                if filename:
                    data = self.store.export_data()
                    data["settings"]["ui"] = self.settings
                    data = select_sections(data, p.get("sections"))
                    target = Path(filename)
                    temporary = target.with_suffix(target.suffix + ".tmp")
                    temporary.write_text(
                        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    temporary.replace(target)
                    self.log("info", f"备份已导出：{filename}")
            elif action == "import":
                filename, _ = QFileDialog.getOpenFileName(
                    None, "导入新版 JSON 备份", "", "JSON (*.json)"
                )
                if filename:
                    data = json.loads(Path(filename).read_text(encoding="utf-8"))
                    data = prepare_import(select_sections(data, p.get("sections")), self.settings)
                    for program in data["programs"]:
                        program["files"] = encode_sources(program.get("files", []))
                    if "live" in data["settings"]:
                        data["settings"]["live"]["files"] = encode_sources(
                            data["settings"]["live"]["files"]
                        )
                    self.store.import_data(data)
                    self.settings = dict(DEFAULT_SETTINGS, **self.store.get_setting("ui", {}))
                    for setting in ("exclude_windows", "calendar_overrides", "calendar_details"):
                        self.settings[setting] = self.store.get_setting(
                            setting, DEFAULT_SETTINGS[setting]
                        )
                    self.live = LivePlaylist(self.store)
                    self.player.set_duck_volume(self.settings["duckVolume"])
                    self.settingsChanged.emit()
                    self.log("info", "备份已导入；命令和系统动作保持禁用，请核对后启用。")
            elif action == "create_examples":
                plan = self.store.save_plan({"name": "工作日作息表"})
                for name, at in [
                    ("早间提示", "08:00:00"),
                    ("午间休息", "12:00:00"),
                    ("下午开始", "14:00:00"),
                ]:
                    self.store.save_program(
                        {
                            "plan_id": plan["id"],
                            "name": name,
                            "time": at,
                            "weekdays": [0, 1, 2, 3, 4],
                            "enabled": False,
                        }
                    )
                self.log("info", "示例已创建，添加媒体并启用后才会执行")
            else:
                raise ValueError(f"不支持的操作：{action}")
            self.refresh()
            return json.dumps({"ok": True})
        except Exception as exc:
            message = str(exc)
            self.log("error", message)
            self.refresh()
            self.errorOccurred.emit(message)
            return json.dumps({"ok": False, "error": message}, ensure_ascii=False)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.timer.stop()
        self.startup_timer.stop()
        if self.service and not self.service.stop():
            self.log("warning", "调度线程尚未退出，正在等待数据库操作结束")
        self.publish_timer.stop()
        self.power_timer.stop()
        if self.power_pending:
            self._run_changed(
                self.power_pending.get("event_id", ""), "cancelled", "程序退出，系统倒计时已取消"
            )
        self.player.stop()
        self.store.close()
        for handler in list(self.logger.handlers):
            handler.close()
            self.logger.removeHandler(handler)
