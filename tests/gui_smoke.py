"""Explicit desktop smoke check. Requires a real hardware-backed DISPLAY.

Run: DISPLAY=:92 QSG_RHI_BACKEND=opengl QSG_INFO=1 python tests/gui_smoke.py
Screenshots and the renderer log are local diagnostics, never test fixtures.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QObject, QPoint, QPointF, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from bellplan.backend import Backend

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / ".local/evidence/gui-smoke"


def worker_main():
    if not os.getenv("DISPLAY") and sys.platform == "linux":
        raise RuntimeError("An actual hardware display is required")
    if (
        os.getenv("QT_QPA_PLATFORM") in ("offscreen", "minimal")
        or os.getenv("QSG_RHI_BACKEND") == "software"
    ):
        raise RuntimeError("Software rendering is not accepted")
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    QQuickStyle.setStyle("Material")
    app = QApplication([])
    app.setOrganizationName("BellPlanSmoke")
    app.setApplicationName("BellPlanSmoke")
    temp = TemporaryDirectory(prefix="bellplan-gui-")
    backend = Backend(Path(temp.name))
    backend.timer.stop()
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("backend", backend)
    engine.load(QUrl.fromLocalFile(str(ROOT / "src/bellplan/qml/Main.qml")))
    if not engine.rootObjects():
        raise RuntimeError("QML failed to load")
    window = engine.rootObjects()[0]
    window.show()
    QTest.qWait(2500)
    assert window.isExposed()

    def objects():
        found = [window] + window.findChildren(QObject)

        def visit(item):
            for child in item.childItems():
                if child not in found:
                    found.append(child)
                visit(child)

        visit(window.contentItem())
        return found

    def active(item):
        if not isinstance(item, QQuickItem) or item.width() < 1 or item.height() < 1:
            return False
        node = item
        while node:
            if not node.isVisible():
                return False
            node = node.parentItem()
        scene = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
        return 0 <= scene.x() < window.width() and 0 <= scene.y() < window.height()

    def find(name=None, text=None):
        matches = [
            x
            for x in objects()
            if (name is None or x.objectName() == name)
            and (text is None or x.property("text") == text)
            and active(x)
        ]
        assert matches, f"Visible item not found: name={name}, text={text}"
        return matches[0]

    def click(item):
        if isinstance(item, str):
            item = find(name=item)
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
        QTest.qWait(160)

    def fill(name, value):
        item = find(name=name)
        click(item)
        QTest.keyClick(window, Qt.Key_A, Qt.ControlModifier)
        for character in value:
            QTest.keyClick(window, character)
        QTest.qWait(80)
        if item.property("text") != value:
            window.grabWindow().save(str(EVIDENCE / "failure.png"))
        assert item.property("text") == value, (name, item.property("text"))

    def snapshot(name):
        QTest.qWait(250)
        assert window.grabWindow().save(str(EVIDENCE / f"{name}.png"))

    assert backend.store.list_programs() == []
    snapshot("01-empty-overview")
    click("newProgram")
    fill("nameInput", "Morning Broadcast")
    fill("timesInput", "08:00:00")
    # A path is filled without playing anything; the scheduled time is not due.
    QTest.wheelEvent(window, QPointF(1050, 650), QPoint(0, -480))
    QTest.qWait(300)
    fill("filesInput", "/local/test/bell.wav")
    snapshot("02-program-editor")
    click("editorSave")
    records = backend.store.list_programs()
    assert len(records) == 1 and records[0]["name"] == "Morning Broadcast", records
    snapshot("03-program-created")
    click(find(text="修改"))
    QTest.wheelEvent(window, QPointF(1050, 400), QPoint(0, 1200))
    QTest.qWait(800)
    fill("nameInput", "Morning Broadcast Edited")
    click("editorSave")
    assert backend.store.list_programs()[0]["name"] == "Morning Broadcast Edited"

    # Actual navigation exercises all pages; screenshots are inspected separately.
    for page in range(1, 6):
        click(f"nav{page}")
        assert window.property("page") == page
        snapshot(f"04-page-{page}")
    click("nav5")
    click("calendarAdd")
    fill("holidayName", "Winter break")
    fill("holidayStart", "2026-12-20")
    fill("holidayEnd", "2026-12-22")
    click("holidaySave")
    assert backend.settings["exclude_windows"][0]["start"] == "2026-12-20"
    snapshot("04-holiday-saved")
    click(find(text="调休"))
    click("calendarAdd")
    fill("overrideActual", "2026-12-19")
    fill("overrideTarget", "2026-12-21")
    fill("overrideNote", "Makeup workday")
    click("overrideSave")
    assert backend.settings["calendar_overrides"]["2026-12-19"] == "2026-12-21"
    snapshot("04-override-saved")
    click("nav4")
    duck = find(name="duckSetting")
    click(duck.property("contentItem"))
    QTest.keyClick(window, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClick(window, "6")
    QTest.keyClick(window, "0")
    QTest.keyClick(window, Qt.Key_Return)
    click("settingsSave")
    assert backend.settings["duckVolume"] == 60
    snapshot("04-settings-saved")
    click("nav0")
    click("planManage")
    fill("planNameInput", "Weekend Plan")
    click("planSave")
    assert any(p["name"] == "Weekend Plan" for p in backend.store.list_plans())
    snapshot("05-plan-created")
    entry = find(text="Weekend Plan")
    delete = next(
        item for item in entry.parentItem().childItems() if item.property("text") == "删除"
    )
    click(delete)
    click("planDeleteConfirm")
    assert not any(p["name"] == "Weekend Plan" for p in backend.store.list_plans())
    snapshot("05-plan-deleted")
    # Close plan popup by Escape, then test search using actual keystrokes.
    QTest.keyClick(window, Qt.Key_Escape)
    QTest.qWait(180)
    fill("searchInput", "no-such-program")
    snapshot("06-search-empty")
    fill("searchInput", "Morning")
    assert window.property("search") == "Morning"
    snapshot("07-final-overview")
    window.setWidth(1050)
    window.setHeight(720)
    snapshot("08-minimum-window")
    window.setWidth(1360)
    window.setHeight(900)
    QTest.qWait(300)
    click(find(text="删除"))
    click("programDeleteConfirm")
    assert backend.store.list_programs() == []
    snapshot("09-program-deleted")
    (EVIDENCE / "result.json").write_text(
        json.dumps(
            {
                "result": "interactions-passed-awaiting-renderer-check",
                "display": os.environ.get("DISPLAY"),
                "programs": len(backend.store.list_programs()),
                "plans": len(backend.store.list_plans()),
                "screenshots": sorted(p.name for p in EVIDENCE.glob("*.png")),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    window.hide()
    backend.close()
    engine.deleteLater()
    app.processEvents()
    temp.cleanup()
    print("GUI interaction worker passed.")
    return 0


def main():
    if "--worker" in sys.argv:
        return worker_main()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ, QSG_INFO="1")
    result = subprocess.run(
        [sys.executable, __file__, "--worker"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=90,
    )
    combined = result.stdout + result.stderr
    (EVIDENCE / "renderer.log").write_text(combined, encoding="utf-8")
    if result.returncode:
        print(combined, file=sys.stderr)
        return result.returncode
    renderer = "\n".join(x for x in combined.splitlines() if "VENDOR:" in x or "RENDERER:" in x)
    if "NVIDIA" not in renderer or not ("Tesla T10" in renderer or "RTX A4000" in renderer):
        raise AssertionError("Physical GPU not confirmed: " + renderer)
    if any(x in combined for x in ("llvmpipe", "SwiftShader", "lavapipe")):
        raise AssertionError("Software rendering detected")
    errors = [
        line
        for line in combined.splitlines()
        if any(
            x in line for x in ("ReferenceError:", "TypeError:", "SyntaxError:", "Cannot assign")
        )
    ]
    if errors:
        raise AssertionError(errors)
    report = json.loads((EVIDENCE / "result.json").read_text(encoding="utf-8"))
    report.update(result="passed", renderer=renderer)
    (EVIDENCE / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("GUI smoke passed with physical GPU: " + renderer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
