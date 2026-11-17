"""Desktop startup, hardware renderer diagnostics and tray lifecycle."""

from __future__ import annotations

import argparse
import sqlite3
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, QStandardPaths, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QSGRendererInterface
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QMessageBox, QSystemTrayIcon

from .backend import Backend
from .selfcheck import write_report
from .windows_events import WindowsEvents


def app_icon():
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor("#6754dc"))
    painter = QPainter(pixmap)
    painter.setPen(QColor("white"))
    font = painter.font()
    font.setPixelSize(39)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), 0x84, "♪")
    painter.end()
    return QIcon(pixmap)


def main(argv=None):
    # Windowed executables may have no stream; attached consoles may use cp1252.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass
    parser = argparse.ArgumentParser(description="BellPlan")
    parser.add_argument("--data-dir", type=Path, help="独立数据目录")
    parser.add_argument("--screenshot", type=Path, help="启动后保存真实窗口截图")
    parser.add_argument("--quit-after", type=float, default=0, help="诊断：指定秒后退出")
    parser.add_argument(
        "--self-check-report", type=Path, help="检查便携包内容并写入 JSON，不启动界面"
    )
    args = parser.parse_args(argv)
    if args.self_check_report:
        return write_report(args.self_check_report)
    # Use the native graphics API selected by Qt. Validation may explicitly choose
    # QSG_RHI_BACKEND=opengl to record the actual OpenGL device below.
    QQuickStyle.setStyle("Material")
    app = QApplication(sys.argv[:1])
    app.setOrganizationName("BellPlan")
    app.setApplicationName("BellPlan")
    app.setApplicationDisplayName("铃序（BellPlan）")
    icon = app_icon()
    app.setWindowIcon(icon)
    if args.data_dir:
        data_dir = args.data_dir
    elif getattr(sys, "frozen", False):
        data_dir = Path(sys.executable).resolve().parent / "BellPlan-data"
    else:
        data_dir = Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation))
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=data_dir):
            pass
    except OSError:
        QMessageBox.information(
            None, "数据目录", "程序所在位置无法写入，请选择用于保存数据的目录。"
        )
        selected = QFileDialog.getExistingDirectory(None, "选择数据目录")
        if not selected:
            return 1
        data_dir = Path(selected)
    lock = QLockFile(str(data_dir / "instance.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, "铃序", "此数据目录已有程序实例运行，请从系统托盘打开。")
        return 1
    try:
        backend = Backend(data_dir, start_service=True)
    except (OSError, sqlite3.Error, ValueError) as exc:
        QMessageBox.critical(
            None, "无法打开数据", "无法读取程序数据，请检查目录权限或从备份恢复。\n" + str(exc)
        )
        lock.unlock()
        return 1
    native_events = WindowsEvents(app)
    native_events.resume.connect(backend.handle_resume)
    native_events.devicesChanged.connect(backend.handle_devices_changed)
    native_events.suspend.connect(lambda: backend.log("info", "系统即将休眠"))
    native_events.install(app)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("backend", backend)
    qml = Path(__file__).parent / "qml" / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml)))
    if not engine.rootObjects():
        backend.close()
        lock.unlock()
        return 1
    window = engine.rootObjects()[0]
    tray = QSystemTrayIcon(icon, app)
    tray.setToolTip("铃序（BellPlan）")
    menu = QMenu()
    show_action = QAction("打开主窗口", menu)
    show_action.triggered.connect(
        lambda: (window.show(), window.raise_(), window.requestActivate())
    )
    menu.addAction(show_action)
    stop_action = QAction("停止所有播放", menu)
    stop_action.triggered.connect(backend.player.stop)
    menu.addAction(stop_action)
    quit_action = QAction("退出程序", menu)
    quit_action.triggered.connect(app.quit)
    menu.addAction(quit_action)
    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: (
            show_action.trigger()
            if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick)
            else None
        )
    )
    if QSystemTrayIcon.isSystemTrayAvailable():
        tray.show()

    def on_closing(event):
        if backend.settings.get("closeToTray") and tray.isVisible():
            event.setAccepted(False)
            window.hide()

    window.closing.connect(on_closing)

    class RenderProbe(QObject):
        detected = Signal(str)

    probe = RenderProbe(app)

    def publish_renderer(name):
        backend.renderer = name
        backend.log("info", "图形设备：" + name)

    probe.detected.connect(publish_renderer)

    def capture_renderer():
        # Called directly on Qt's render thread: inspect the actual scenegraph
        # context, rather than a separately created test context.
        name = str(window.rendererInterface().graphicsApi())
        try:
            if window.rendererInterface().graphicsApi() == QSGRendererInterface.OpenGL:
                import ctypes
                import ctypes.util

                from PySide6.QtGui import QOpenGLContext

                if QOpenGLContext.currentContext():
                    lib = (
                        ctypes.CDLL(ctypes.util.find_library("GL"))
                        if sys.platform != "win32"
                        else ctypes.WinDLL("opengl32")
                    )
                    lib.glGetString.restype = ctypes.c_char_p
                    values = [lib.glGetString(code) for code in (0x1F00, 0x1F01, 0x1F02)]
                    name = " | ".join(v.decode(errors="replace") for v in values if v)
        except Exception as exc:
            name += "（设备详情读取失败：" + str(exc) + "）"
        probe.detected.emit(name)

    window.sceneGraphInitialized.connect(capture_renderer, Qt.DirectConnection)
    if args.screenshot:

        def capture_window():
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            window.grabWindow().save(str(args.screenshot))

        QTimer.singleShot(2000, capture_window)
    if args.quit_after:
        QTimer.singleShot(int(args.quit_after * 1000), app.quit)
    result = app.exec()
    tray.hide()
    native_events.close()
    # Destroy QML bindings before destroying backend to avoid shutdown warnings.
    engine.clearComponentCache()
    backend.close()
    lock.unlock()
    return result
