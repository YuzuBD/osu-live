"""Configuration, from environment or a ``.env`` file next to the project."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .paths import DATA_ROOT

#: Where ``.env`` is looked for: the checkout, or the folder holding the
#: executable in a frozen build.
PROJECT_ROOT = DATA_ROOT


def _load_dotenv(path: Path) -> None:
    """Minimal ``.env`` reader; real environment always wins."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass
class Config:
    #: Interface to bind. 0.0.0.0 makes the overlay reachable from a phone.
    host: str = "0.0.0.0"
    port: int = 24051
    #: Where osu! keeps its beatmaps, for pp calculation.
    songs_folder: str = ""
    #: Poll interval for the memory reader, in milliseconds.
    poll_interval_ms: int = 50
    #: How often pp is recomputed while playing, in milliseconds.
    pp_interval_ms: int = 1000
    #: bancho.py API base, used to cross-reference rank and performance.
    bancho_api_url: str = ""
    #: Player id or name on that server; blank disables the lookup.
    bancho_player: str = ""
    #: bancho.py avatar host (``a.`` subdomain), proxied so the phone only
    #: needs to reach this machine.
    avatar_url: str = ""
    #: Seconds between bancho.py refreshes.
    bancho_interval_s: float = 15.0
    #: Optional shared-secret style token; when set, requests must present it.
    access_token: str = ""

    @classmethod
    def from_env(cls) -> "Config":
        _load_dotenv(PROJECT_ROOT / ".env")
        songs = os.environ.get("SONGS_FOLDER", "")
        if not songs:
            songs = _guess_songs_folder()
        return cls(
            host=os.environ.get("HOST", "0.0.0.0"),
            port=int(os.environ.get("PORT", "24051")),
            songs_folder=songs,
            poll_interval_ms=int(os.environ.get("POLL_INTERVAL_MS", "50")),
            pp_interval_ms=int(os.environ.get("PP_INTERVAL_MS", "1000")),
            bancho_api_url=os.environ.get("BANCHO_API_URL", "").rstrip("/"),
            bancho_player=os.environ.get("BANCHO_PLAYER", ""),
            avatar_url=os.environ.get("AVATAR_URL", "").rstrip("/"),
            bancho_interval_s=float(os.environ.get("BANCHO_INTERVAL_S", "15")),
            access_token=os.environ.get("ACCESS_TOKEN", ""),
        )


#: Drives searched when nothing else identifies the install.
_DRIVES = ("C:", "D:", "E:", "F:", "G:")

#: Directories never worth descending into. None of them holds an osu! install.
_SCAN_SKIP = frozenset({
    "$recycle.bin", "system volume information", "windows", "winsxs",
    "program files", "program files (x86)", "programdata", "appdata",
    "$winreagent", "recovery", "perflogs", "onedrive",
})

#: How far below a drive root to look. osu! ends up at "D:\Game\b20220829",
#: which is two levels; one more covers a folder-per-drive layout.
_SCAN_DEPTH = 3

#: Stops a pathological tree from turning startup into a full disk crawl.
_SCAN_LIMIT = 4000


def _songs_folder_for(install: Path) -> str:
    """The Songs directory of an osu! install, or "" if there is not one.

    ``BeatmapDirectory`` in the game's own config is the authority, because the
    folder can be renamed or moved; it may be absolute or relative to the
    install. Whichever is used, it is only accepted when the directory is
    actually there -- which is also what tells a real install apart from a
    stale ``osu!.exe`` left lying in a downloads folder, where the same config
    line resolves to nothing.
    """
    for config_file in sorted(install.glob("osu!*.cfg")):
        try:
            text = config_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            key, separator, value = line.partition("=")
            if not separator or key.strip() != "BeatmapDirectory":
                continue
            candidate = Path(value.strip().strip('"'))
            if not candidate.is_absolute():
                candidate = install / candidate
            if candidate.is_dir():
                return str(candidate)
            break  # the configured one is missing; don't trust a default either
    fallback = install / "Songs"
    return str(fallback) if fallback.is_dir() else ""


def _candidate_installs() -> list[Path]:
    """Where an osu! install usually is. No disk walking."""
    found: list[Path] = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        found.append(Path(local) / "osu!")
    for drive in _DRIVES:
        for name in ("osu!", "osu", "Games\\osu!", "Game\\osu!"):
            found.append(Path(drive + "\\") / name)
    return found


def _scan_for_installs() -> list[Path]:
    """Directories holding an ``osu!.exe``, most recently updated first.

    Searching for a folder literally called "osu!" is not enough: the installer
    names it after the build, so this machine's is "b20220829" with two older
    builds still sitting beside it, each with its own Songs folder. Ordering by
    the executable's own timestamp puts the install that is still being updated
    -- and therefore the one being played -- ahead of the frozen ones.
    """
    found: list[Path] = []
    visited = 0
    for drive in _DRIVES:
        root = Path(drive + "\\")
        if not root.is_dir():
            continue
        queue: list[tuple[Path, int]] = [(root, 0)]
        while queue and visited < _SCAN_LIMIT:
            directory, depth = queue.pop(0)
            visited += 1
            try:
                with os.scandir(directory) as entries:
                    children = [entry.path for entry in entries if _is_dir(entry)]
            except OSError:
                continue
            for child in children:
                if Path(child).name.lower() in _SCAN_SKIP:
                    continue
                install = Path(child)
                # Recorded *and* descended into. Stopping at the first folder
                # holding an osu!.exe is what makes a stray copy in a downloads
                # folder hide the real install nested inside it.
                if (install / "osu!.exe").is_file():
                    found.append(install)
                if depth + 1 < _SCAN_DEPTH:
                    queue.append((install, depth + 1))

    found.sort(key=_install_rank, reverse=True)
    return found


def _install_rank(install: Path) -> float:
    """How much an install looks like the one being played.

    ``osu!.db``, the beatmap database, is rewritten every time the game runs,
    so its timestamp says which install is in use. The executable's does not:
    on the machine this was written on, the newest ``osu!.exe`` belongs to a
    stale copy holding 15 maps, while the install played every day has an older
    executable and 603. Only consulted when no osu! is running, since a running
    one is asked directly.
    """
    for name in ("osu!.db", "osu!.exe"):
        try:
            return (install / name).stat().st_mtime
        except OSError:
            continue
    return 0.0


def _is_dir(entry: os.DirEntry[str]) -> bool:
    try:
        return entry.is_dir(follow_symlinks=False)
    except OSError:
        return False


def _songs_from_running_client() -> str:
    """Ask the osu! that is actually running where its beatmaps are.

    This is the only answer that cannot be wrong when several installs exist.
    There are three on this machine, each with its own Songs folder, so a guess
    based on timestamps or names is a coin toss; the executable being played is
    not.
    """
    # Imported here rather than at module level: the process names belong to
    # the reader, and config is imported by nearly everything else.
    from .reader import OSU_PROCESS_NAMES
    from .winmem import list_processes, process_path

    try:
        processes = list_processes(OSU_PROCESS_NAMES)
    except Exception:
        return ""
    for pid, _name in processes:
        path = process_path(pid)
        if not path:
            continue
        songs = _songs_folder_for(Path(path).parent)
        if songs:
            return songs
    return ""


def _guess_songs_folder() -> str:
    """Find osu!'s Songs directory without being told where it is.

    In order of how much they can be trusted:

    1. the running client, asked for its own location -- exact;
    2. installs found on disk, ranked by which was used most recently.

    A literal guess at "C:\\osu!\\Songs" is not enough here. The installer names
    the folder after the build rather than "osu!", the folder can be renamed or
    moved, and several installs can sit side by side -- all three are true on
    the machine this was written on.
    """
    running = _songs_from_running_client()
    if running:
        return running

    installs: list[Path] = [p for p in _candidate_installs() if (p / "osu!.exe").is_file()]
    installs.extend(_scan_for_installs())

    ranked: list[Path] = []
    seen: set[str] = set()
    for install in sorted(installs, key=_install_rank, reverse=True):
        key = str(install).lower()
        if key not in seen:
            seen.add(key)
            ranked.append(install)

    for install in ranked:
        songs = _songs_folder_for(install)
        if songs:
            return songs
    return ""
