"""Where this program's files live.

Running from source there is one root and everything is under it. Frozen into a
single executable there are two, and they are not the same place:

* ``BUNDLE_ROOT`` is where the code and the web assets were packed. PyInstaller
  unpacks that to a temporary directory which is deleted when the process
  exits, so it can be read but never written to.
* ``DATA_ROOT`` is where anything the program produces belongs -- the ``.env``
  it reads, the finished-score cache, the log. That has to be next to the
  executable, or it goes away with the temporary directory.

Both cases resolve to the checkout when running from source, so callers do not
have to care which one they are in.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: True inside a PyInstaller build. PyInstaller sets ``sys.frozen`` on the
#: interpreter it bootstraps, and ``sys._MEIPASS`` to the unpacked bundle.
FROZEN: bool = bool(getattr(sys, "frozen", False))

_PACKAGE_PARENT = Path(__file__).resolve().parent.parent


def _bundle_root() -> Path:
    if FROZEN:
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return _PACKAGE_PARENT


def _data_root() -> Path:
    if FROZEN:
        return Path(sys.executable).resolve().parent
    return _PACKAGE_PARENT


#: Read-only: the packed web assets underneath this.
BUNDLE_ROOT: Path = _bundle_root()

#: Writable: ``.env``, ``.last_score.json`` and ``.osu_live.log`` go here.
DATA_ROOT: Path = _data_root()

#: The overlay's HTML, CSS and JavaScript.
WEB_ROOT: Path = BUNDLE_ROOT / "osu_live" / "web"
