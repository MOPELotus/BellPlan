from pathlib import Path

root = Path(SPECPATH)
a = Analysis(
    [str(root / "run.py")],
    pathex=[str(root / "src")],
    binaries=[],
    datas=[
        (str(root / "src/bellplan/qml"), "bellplan/qml"),
        (str(root / "src/bellplan/assets"), "bellplan/assets"),
    ],
    hiddenimports=["PySide6.QtQuickControls2", "PySide6.QtMultimedia", "PySide6.QtTextToSpeech"],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="BellPlan", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False,
)
