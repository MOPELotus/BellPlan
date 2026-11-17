"""Hardware-rendered layout regressions for short windows and high DPI.

Run explicitly with a physical NVIDIA display, for example:
DISPLAY=:92 QSG_RHI_BACKEND=opengl python tests/gui_layout_smoke.py
No audio is played. Screenshots and renderer evidence stay in .local/.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QObject, QPoint, QPointF, QRectF, QUrl
from PySide6.QtGui import QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from bellplan import __version__
from bellplan.app import ui_font_family
from bellplan.backend import Backend

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / ".local/evidence/layout-regression"
MATRIX = ((1280, 720), (1280, 800), (1050, 720))


def worker(width, height, scale):
    if not os.getenv("DISPLAY") and sys.platform == "linux":
        raise RuntimeError("A physical display is required")
    if (
        os.getenv("QT_QPA_PLATFORM") in ("minimal", "offscreen")
        or os.getenv("QSG_RHI_BACKEND") == "software"
    ):
        raise RuntimeError("Software rendering is not accepted")
    label = f"scale-{scale}-{width}x{height}"
    directory = EVIDENCE / label
    directory.mkdir(parents=True, exist_ok=True)
    QQuickStyle.setStyle("Material")
    app = QApplication([])
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    font.setFamily(ui_font_family(QFontDatabase.families(), sys.platform, font.family()))
    app.setFont(font)
    app.setApplicationName("BellPlanLayoutSmoke")
    app.setApplicationVersion(__version__)
    temporary = TemporaryDirectory(prefix="bellplan-layout-")
    backend = Backend(Path(temporary.name))
    backend.timer.stop()
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("backend", backend)
    engine.load(QUrl.fromLocalFile(str(ROOT / "src/bellplan/qml/Main.qml")))
    if not engine.rootObjects():
        raise RuntimeError("Main.qml failed to load")
    window = engine.rootObjects()[0]
    window.resize(width, height)
    window.setPosition(0, 0)
    window.show()
    QTest.qWait(1800)
    assert window.isExposed()
    assert (window.width(), window.height()) == (width, height)
    assert abs(window.devicePixelRatio() - float(scale)) < 0.05
    screen = window.screen().geometry()
    if screen.width() < width or screen.height() < height:
        raise RuntimeError(
            f"Physical display is too small for {label}: screen logical size {screen.width()}x{screen.height()}. "
            "Configure a sufficiently large hardware-backed display before testing; off-screen pixels are not valid screenshot evidence."
        )

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

    def find(name):
        matches = [item for item in objects() if item.objectName() == name]
        assert matches, name
        return matches[0]

    def bounds(item):
        return item.mapRectToScene(QRectF(0, 0, item.width(), item.height()))

    def check_visible(item):
        rect = bounds(item)
        assert item.isVisible() and rect.width() > 0 and rect.height() > 0
        viewport = QRectF(0, 0, window.width(), window.height())
        ancestor = item.parentItem()
        while ancestor:
            assert ancestor.isVisible(), item.objectName()
            if ancestor.clip():
                viewport = viewport.intersected(bounds(ancestor))
            ancestor = ancestor.parentItem()
        assert viewport.adjusted(-0.5, -0.5, 0.5, 0.5).contains(rect), (
            item.objectName(),
            rect,
            viewport,
        )
        assert item.height() + 0.5 >= item.implicitHeight(), (
            item.objectName(),
            item.height(),
            item.implicitHeight(),
        )
        return {
            "x": rect.x(),
            "y": rect.y(),
            "width": rect.width(),
            "height": rect.height(),
            "implicitHeight": item.implicitHeight(),
        }

    def screenshot(name):
        QTest.qWait(200)
        pixmap = window.grabWindow()
        assert not pixmap.isNull()
        assert pixmap.width() == round(width * float(scale))
        assert pixmap.height() == round(height * float(scale))
        assert pixmap.save(str(directory / f"{name}.png"))

    scroll = find("overviewScroll")
    empty = find("overviewEmptyState")
    title = find("overviewEmptyTitle")
    actions = find("overviewEmptyActions")
    screenshot("empty-initial")
    # Scroll the real viewport if a short window requires extra content height.
    for _ in range(6):
        viewport = bounds(scroll)
        if viewport.adjusted(0, -0.5, 0, 0.5).contains(bounds(empty)):
            break
        point = QPointF(viewport.center().x(), viewport.center().y())
        QTest.wheelEvent(window, point, QPoint(0, -120))
        QTest.qWait(180)
    geometry = {
        "empty": check_visible(empty),
        "title": check_visible(title),
        "actions": check_visible(actions),
    }
    geometry["empty_parent"] = {
        "class": empty.parentItem().metaObject().className(),
        "height": empty.parentItem().height(),
        "is_list_content_item": empty.parentItem() is find("overviewList").property("contentItem"),
    }
    action_buttons = [
        item for item in actions.childItems() if item.property("text") and item.isVisible()
    ]
    assert len(action_buttons) == 2
    geometry["buttons"] = [check_visible(item) for item in action_buttons]
    icons = [find(f"navIcon{index}") for index in range(6)]
    icon_geometry = [check_visible(icon) for icon in icons]
    assert all(abs(icon.width() - 22) < 0.1 and abs(icon.height() - 22) < 0.1 for icon in icons)
    assert max(bounds(icon).x() for icon in icons) - min(bounds(icon).x() for icon in icons) < 0.1
    assert len({icon.property("name") for icon in icons}) == 6
    screenshot("empty-visible")

    plan = backend.store.list_plans()[0]
    backend.store.save_program(
        {"plan_id": plan["id"], "name": "界面高度验证节目", "time": "08:00:00", "enabled": False}
    )
    backend.refresh()
    scroll.setProperty("contentY", 0)
    QTest.qWait(300)
    rows = [
        item
        for item in objects()
        if isinstance(item, QQuickItem)
        and item.property("text") == "界面高度验证节目"
        and item.isVisible()
    ]
    assert rows
    check_visible(rows[0])
    assert backend.player.state()["status"] == "stopped"
    screenshot("program-present")
    result = {
        "logical_size": [width, height],
        "scale": float(scale),
        "physical_size": [round(width * float(scale)), round(height * float(scale))],
        "font": font.family(),
        "geometry": geometry,
        "icons": icon_geometry,
        "contentHeight": scroll.property("contentHeight"),
        "viewportHeight": scroll.height(),
    }
    (directory / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    window.hide()
    backend.close()
    engine.deleteLater()
    app.processEvents()
    temporary.cleanup()
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--scale", default="1")
    args = parser.parse_args()
    if args.worker:
        return worker(args.width, args.height, args.scale)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    results = []
    for scale in ("1", "2"):
        for width, height in MATRIX:
            label = f"scale-{scale}-{width}x{height}"
            environment = dict(os.environ, QT_SCALE_FACTOR=scale, QSG_INFO="1")
            process = subprocess.run(
                [
                    sys.executable,
                    __file__,
                    "--worker",
                    "--width",
                    str(width),
                    "--height",
                    str(height),
                    "--scale",
                    scale,
                ],
                env=environment,
                capture_output=True,
                text=True,
                timeout=90,
            )
            combined = process.stdout + process.stderr
            (EVIDENCE / f"{label}.log").write_text(combined, encoding="utf-8")
            if process.returncode:
                raise RuntimeError(f"{label} failed; see {EVIDENCE / (label + '.log')}")
            renderer = next(
                (line for line in combined.splitlines() if "OpenGL VENDOR:" in line), ""
            )
            assert "NVIDIA" in renderer and ("Tesla" in renderer or "RTX" in renderer), renderer
            assert not any(
                name in renderer.lower() for name in ("llvmpipe", "lavapipe", "swiftshader")
            )
            assert not any(
                error in combined
                for error in ("ReferenceError:", "TypeError:", "Binding loop detected")
            ), combined
            result = json.loads((EVIDENCE / label / "result.json").read_text(encoding="utf-8"))
            result.update(case=label, renderer=renderer)
            results.append(result)
    (EVIDENCE / "result.json").write_text(
        json.dumps({"result": "passed", "cases": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Layout regression passed: {len(results)} hardware-rendered DPI/size cases.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
