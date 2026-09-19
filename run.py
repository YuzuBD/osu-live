"""Entry point for the packaged build.

Running from a checkout, ``python -m osu_live`` is the way in and this file is
not needed. PyInstaller cannot start from a package's ``__main__.py``: it
executes the given script as a top-level module, where ``from .config import``
has no parent package to resolve against, and the build dies on import with
"attempted relative import with no known parent package". So the build needs a
real top-level module that imports the package, which is this.

Keeping it here rather than generating one at build time means the exact entry
point is in the repository and can be run directly:

    python run.py --port 24051
"""

from __future__ import annotations

import sys

from osu_live.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
