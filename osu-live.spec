# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build for osu! live.

    python -m PyInstaller osu-live.spec --noconfirm

Writes ``dist/osu-live.exe``: one file, no Python installation needed on the
machine that runs it.

Two things a frozen build gets wrong if left alone:

* The web assets are data rather than imports, so nothing discovers them.
  They are added under ``osu_live/web``, which is the layout
  ``osu_live.paths`` looks for at run time. Get that destination wrong and the
  overlay serves 404s while still printing a healthy banner.
* ``akatsuki_pp_py`` is a compiled Rust extension inside its package
  directory. PyInstaller follows the import and picks the ``.pyd`` up; if a
  future version stops bundling it, pp silently returns nothing rather than
  failing, so the build is worth checking by hand after an upgrade.
"""

from pathlib import Path

#: Injected by PyInstaller: the directory holding this file. Using it rather
#: than a relative path keeps the build working from any working directory.
ROOT = Path(SPECPATH)

a = Analysis(
    # run.py, not osu_live/__main__.py: PyInstaller executes the entry as a
    # top-level module, where the package's relative imports cannot resolve.
    [str(ROOT / "run.py")],
    pathex=[str(ROOT)],
    binaries=[],
    # The whole web directory, diagnostics included: they are the pages used
    # to check layout on a real phone, so they are worth having in the build.
    datas=[(str(ROOT / "osu_live" / "web"), "osu_live/web")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Nothing here draws a window or a plot; these only add weight.
    excludes=["tkinter", "unittest", "pydoc", "doctest", "test", "lib2to3"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="osu-live",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX is not installed here, and a compressed binary is a little more
    # likely to be quarantined by Windows Defender.
    upx=False,
    runtime_tmpdir=None,
    # It prints the phone URL and logs read failures to the terminal.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
