"""Where this program's files live.

Running from source there is one root and everything is under it. Frozen into an
executable there are two, and they are not the same place:

* ``BUNDLE_ROOT`` is where the code and the web assets were packed. PyInstaller
  unpacks that to a temporary directory which is deleted when the process
  exits, so it can be read but never written to.
* ``DATA_ROOT`` is where anything the program produces belongs -- the ``.env``
  it reads, the finished-score cache, the log.

A packaged build keeps its data under the user's local application data rather
than beside the executable. An executable is one file, and a program that
scatters a config, a cache and a log next to itself is a mess to look at; it
also fails outright when run from somewhere read-only, such as a Program Files
directory or a network share. Running from source keeps everything in the
checkout, where it is being edited.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: True inside a PyInstaller build. PyInstaller sets ``sys.frozen`` on the
#: interpreter it bootstraps, and ``sys._MEIPASS`` to the unpacked bundle.
FROZEN: bool = bool(getattr(sys, "frozen", False))

#: Folder created under the user's application data.
APP_DIR_NAME = "osu-live"

_PACKAGE_PARENT = Path(__file__).resolve().parent.parent


def _bundle_root() -> Path:
    if FROZEN:
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return _PACKAGE_PARENT


def _data_root() -> Path:
    if not FROZEN:
        return _PACKAGE_PARENT
    # LOCALAPPDATA is the right home for a cache and a log; APPDATA is the
    # fallback for the rare profile where the former is not set.
    for variable in ("LOCALAPPDATA", "APPDATA"):
        base = os.environ.get(variable)
        if base:
            return Path(base) / APP_DIR_NAME
    return Path.home() / "AppData" / "Local" / APP_DIR_NAME


#: Read-only: the packed web assets underneath this.
BUNDLE_ROOT: Path = _bundle_root()

#: Writable: ``.env``, ``.last_score.json`` and ``.osu_live.log`` go here.
DATA_ROOT: Path = _data_root()

#: The overlay's HTML, CSS and JavaScript.
WEB_ROOT: Path = BUNDLE_ROOT / "osu_live" / "web"

#: Created once, here, so that every writer can assume it exists. The log
#: handler in particular is built while state.py is being imported, and would
#: otherwise fail and quietly log nowhere. A location that cannot be created is
#: not fatal: it costs the log and the score cache, not the live overlay.
try:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
except OSError:
    pass
