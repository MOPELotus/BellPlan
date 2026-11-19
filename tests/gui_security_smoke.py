"""Password lock regression through actual controls on a physical GPU.

Run: DISPLAY=:92 QSG_RHI_BACKEND=opengl python tests/gui_editor_smoke.py
Screenshots and renderer evidence remain in .local, outside Git.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QObject, QPointF, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from bellplan.backend import Backend

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / ".local/evidence/security"


def worker():
    if not os.getenv("DISPLAY") or os.getenv("QT_QPA_PLATFORM") in ("minimal", "offscreen"):
        raise RuntimeError("A physical display is required")
    if os.getenv("QSG_RHI_BACKEND") == "software":
        raise RuntimeError("Software rendering is not accepted")
    QQuickStyle.setStyle("Material")
    app = QApplication([])
    temporary = TemporaryDirectory(prefix="bellplan-editor-")
    backend = Backend(Path(temporary.name))
    backend.timer.stop()
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("backend", backend)
    engine.load(QUrl.fromLocalFile(str(ROOT / "src/bellplan/qml/Main.qml")))
    assert engine.rootObjects(), "QML failed to load"
    window = engine.rootObjects()[0]
    window.setPosition(0, 0)
    window.show()
    QTest.qWait(1200)
    assert window.isExposed()

    def objects():
        found = [window] + window.findChildren(QObject)
        seen = {id(item) for item in found}

        def visit(item):
            for child in item.childItems():
                if id(child) not in seen:
                    found.append(child)
                    seen.add(id(child))
                visit(child)

        visit(window.contentItem())
        return found

    def find(name=None, text=None):
        matches = [
            item
            for item in objects()
            if (name is None or item.objectName() == name)
            and (text is None or item.property("text") == text)
        ]
        if text is not None:
            matches = [
                item for item in matches if isinstance(item, QQuickItem) and item.isVisible()
            ]
        assert matches, (name, text)
        return matches[0]

    def reveal(item):
        # Reveal controls in the settings page without moving popup controls.
        ancestor = item.parentItem()
        while ancestor:
            if ancestor.property("contentY") is not None:
                point = item.mapToScene(QPointF(0, 0))
                top = ancestor.mapToScene(QPointF(0, 0)).y()
                if point.y() < top or point.y() + item.height() > top + ancestor.height():
                    maximum = max(0, ancestor.property("contentHeight") - ancestor.height())
                    ancestor.setProperty(
                        "contentY",
                        min(maximum, max(0, ancestor.property("contentY") + point.y() - top - 30)),
                    )
                    QTest.qWait(150)
            ancestor = ancestor.parentItem()

    def click(item):
        if isinstance(item, str):
            item = find(item)
        reveal(item)
        assert item.isVisible() and item.isEnabled(), item.objectName()
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
        QTest.qWait(150)

    def fill(name, value):
        item = find(name)
        click(item)
        QTest.keyClick(window, Qt.Key_A, Qt.ControlModifier)
        app.clipboard().setText(value)
        QTest.keyClick(window, Qt.Key_V, Qt.ControlModifier)
        if not value:
            QTest.keyClick(window, Qt.Key_Backspace)
        QTest.qWait(60)
        assert item.property("text") == value, (name, item.property("text"), value)

    def snapshot(name):
        QTest.qWait(180)
        assert window.grabWindow().save(str(EVIDENCE / f"{name}.png"))

    def state():
        return json.loads(backend.stateJson)["security"]

    def act(action, payload=None):
        result = json.loads(backend.dispatch(action, json.dumps(payload or {})))
        QTest.qWait(400)
        return result

    click("nav4")
    click("settingsBackup")
    assert find(text="导出范围")
    snapshot("01-backup-scope")
    QTest.keyClick(window, Qt.Key_Escape)
    QTest.qWait(350)
    click("settingsRestore")
    assert find(text="导入范围")
    QTest.keyClick(window, Qt.Key_Escape)
    QTest.qWait(350)
    click("passwordManage")
    fill("newPassword", "Test-password-1")
    fill("confirmPassword", "Test-password-1")
    click("passwordSave")
    QTest.qWait(450)
    assert state() == {"configured": True, "locked": False}
    assert find("newPassword").property("text") == ""
    click("settingsBackup")
    assert act("security_lock")["ok"]
    assert find("securityOverlay").property("visible")
    snapshot("02-locked-over-old-dialog")
    QTest.keyClick(window, Qt.Key_Escape)
    assert state()["locked"]
    before = backend.store.export_data()
    assert not act("create_examples")["ok"]
    assert backend.store.export_data() == before
    fill("unlockPassword", "wrong-password")
    click("securityUnlock")
    assert state()["locked"]
    assert find("unlockPassword").property("text") == ""
    assert find("unlockError").property("text")
    snapshot("03-wrong-password")
    fill("unlockPassword", "Test-password-1")
    click("securityUnlock")
    QTest.qWait(400)
    assert not state()["locked"]
    assert not find("securityOverlay").property("visible")
    QTest.keyClick(window, Qt.Key_Escape)
    QTest.qWait(350)
    click("passwordManage")
    fill("currentPassword", "Test-password-1")
    fill("newPassword", "Test-password-2")
    fill("confirmPassword", "Test-password-2")
    click("passwordSave")
    QTest.qWait(350)
    assert not find("passwordDialog").property("visible")
    click("securityLock")
    fill("unlockPassword", "Test-password-2")
    click("securityUnlock")
    QTest.qWait(350)
    assert not state()["locked"]
    click("passwordRemove")
    fill("currentPassword", "Test-password-2")
    click("passwordSave")
    QTest.qWait(350)
    assert state() == {"configured": False, "locked": False}
    snapshot("04-password-removed")
    window.hide()
    backend.close()
    engine.deleteLater()
    app.processEvents()
    temporary.cleanup()
    return 0


def main():
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    if "--worker" in sys.argv:
        return worker()
    result = subprocess.run(
        [sys.executable, __file__, "--worker"],
        env=dict(os.environ, QSG_INFO="1"),
        capture_output=True,
        text=True,
        timeout=90,
    )
    combined = result.stdout + result.stderr
    (EVIDENCE / "renderer.log").write_text(combined)
    if result.returncode:
        print(combined, file=sys.stderr)
        return result.returncode
    renderer = "\n".join(
        line for line in combined.splitlines() if "VENDOR:" in line or "RENDERER:" in line
    )
    assert "NVIDIA" in renderer and ("Tesla T10" in renderer or "RTX A4000" in renderer), renderer
    assert not any(name in combined.lower() for name in ("llvmpipe", "lavapipe", "swiftshader"))
    errors = [
        line
        for line in combined.splitlines()
        if any(
            name in line
            for name in ("ReferenceError:", "TypeError:", "SyntaxError:", "Cannot assign")
        )
    ]
    assert not errors, errors
    (EVIDENCE / "result.json").write_text(
        json.dumps({"result": "passed", "renderer": renderer}, indent=2)
    )
    print("Security GUI passed on " + renderer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
