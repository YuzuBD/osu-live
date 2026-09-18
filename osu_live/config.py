"""Configuration, from environment or a ``.env`` file next to the project."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


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


def _guess_songs_folder() -> str:
    """Look next to an installed osu! for its ``Songs`` directory."""
    for drive in ("C:", "D:", "E:"):
        for base in (rf"{drive}\osu!", rf"{drive}\Games\osu!", rf"{drive}\Game\osu!"):
            candidate = Path(base) / "Songs"
            if candidate.is_dir():
                return str(candidate)
    return ""
