"""Program editor regression through actual controls on a physical GPU.

Run: DISPLAY=:92 QSG_RHI_BACKEND=opengl python tests/gui_editor_smoke.py
Screenshots and renderer evidence remain in .local, outside Git.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QObject, QPointF, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from bellplan.backend import Backend
from bellplan.domain import active_at, runs_on

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / ".local/evidence/editor-date-regression"


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
        scroll = find("editorScroll").property("contentItem")
        if find("editor").property("visible"):
            rect = item.mapToScene(QPointF(0, 0))
            top = scroll.mapToScene(QPointF(0, 0)).y()
            bottom = top + scroll.height()
            if rect.y() < top or rect.y() + item.height() > bottom:
                delta = rect.y() - top - 70
                maximum = max(0, scroll.property("contentHeight") - scroll.height())
                scroll.setProperty(
                    "contentY", min(maximum, max(0, scroll.property("contentY") + delta))
                )
                QTest.qWait(120)

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

    def record():
        records = backend.store.list_programs()
        assert len(records) == 1, records
        return records[0]

    def edit():
        click(find(text="修改"))
        assert find("editor").property("visible")

    def save():
        click("editorSave")
        QTest.qWait(350)
        if find("editor").property("visible"):
            snapshot("save-failure")
            print(
                [
                    (item.property("text"), item.objectName())
                    for item in objects()
                    if item.property("text")
                ],
                file=sys.stderr,
            )
        assert not find("editor").property("visible"), "Editor did not save"
        return record()

    click("newProgram")
    fill("nameInput", "Weekday schedule")
    fill("timesInput", "09:10:00")
    assert not find("startClockInput").isEnabled()
    assert not find("endClockInput").isEnabled()
    snapshot("01-weekday-without-dates")
    saved = save()
    assert saved["weekdays"] == [0, 1, 2, 3, 4]
    assert all(not saved[key] for key in ("date_from", "date_to", "datetime_from", "datetime_to"))
    edit()
    fill("nameInput", "Weekday schedule edited")
    assert save()["name"] == "Weekday schedule edited"

    edit()
    fill("startDateInput", "2026-11-18")
    assert find("startClockInput").isEnabled()
    fill("startClockInput", "09:10")
    saved = save()
    assert saved["datetime_from"] == "2026-11-18T09:10:00" and not saved["date_to"]
    edit()
    assert find("startClockInput").property("text") == "09:10:00"
    fill("startDateInput", "")
    assert not find("startClockInput").isEnabled()
    assert find("startClockInput").property("text") == ""
    fill("endDateInput", "2026-12-18")
    fill("endClockInput", "09:10:30")
    saved = save()
    assert not saved["datetime_from"] and saved["datetime_to"] == "2026-12-18T09:10:30"
    edit()
    fill("startDateInput", "2026-11-18")
    fill("startClockInput", "09:10:00")
    snapshot("02-both-validity-boundaries")
    saved = save()
    assert saved["datetime_from"] == "2026-11-18T09:10:00"
    assert saved["datetime_to"] == "2026-12-18T09:10:30"
    edit()
    fill("startDateInput", "")
    fill("endDateInput", "")
    assert find("endClockInput").property("text") == ""
    snapshot("03-cleared-validity-boundaries")
    cleared = save()
    assert all(not cleared[key] for key in ("date_from", "date_to", "datetime_from", "datetime_to"))

    for value in ("1,15,", "1 15 ", " 1，15， "):
        edit()
        fill("monthDaysInput", value)
        assert save()["month_days"] == [1, 15]
    edit()
    before = record()
    fill("monthDaysInput", "1,a")
    click("editorSave")
    assert find("editor").property("visible") and record() == before
    snapshot("04-invalid-month-day-rejected")
    click(find(text="确定"))
    fill("monthDaysInput", "")
    save()

    # Both independently imported constraints must survive an editor round trip.
    imported = dict(
        record(),
        date_from="2026-11-01",
        datetime_from="2026-11-18T09:10:00",
        date_to="2026-12-31",
        datetime_to="2026-12-18T09:10:30",
    )
    backend.store.save_program(imported)
    backend.refresh()
    QTest.qWait(150)
    edit()
    assert find("startDateInput").property("text") == "2026-11-18"
    assert find("endDateInput").property("text") == "2026-12-18"
    snapshot("05-imported-effective-validity")
    after = save()
    for stamp in (
        "2026-11-04T09:10:00",
        "2026-11-18T09:09:59",
        "2026-11-18T09:10:00",
        "2026-12-18T09:10:30",
        "2026-12-18T09:10:31",
        "2026-12-21T09:10:00",
    ):
        instant = datetime.fromisoformat(stamp)
        assert (runs_on(imported, instant.date()) and active_at(imported, instant)) == (
            runs_on(after, instant.date()) and active_at(after, instant)
        ), stamp
    snapshot("06-saved-overview")
    (EVIDENCE / "result.json").write_text(
        json.dumps({"result": "interactions-passed", "programs": 1}, indent=2)
    )
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
    report = json.loads((EVIDENCE / "result.json").read_text())
    report.update(result="passed", renderer=renderer)
    (EVIDENCE / "result.json").write_text(json.dumps(report, indent=2))
    print("Editor regression passed on " + renderer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
