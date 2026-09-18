"""Cross-references the live client with the bancho.py server.

Memory can tell us what is *happening*; only the server knows the player's
server-side rank and total performance. This module reads the latter through
bancho.py's public v1 API.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BanchoStats:
    pp: int = 0
    rank: int = 0
    country_rank: int = 0
    accuracy: float = 0.0
    play_count: int = 0
    ranked_score: int = 0
    total_score: int = 0
    max_combo: int = 0
    playtime: int = 0
    global_rank: int = 0

    @classmethod
    def from_api(cls, stats: dict[str, Any]) -> "BanchoStats":
        return cls(
            pp=int(stats.get("pp") or 0),
            rank=int(stats.get("rank") or 0),
            country_rank=int(stats.get("country_rank") or 0),
            accuracy=float(stats.get("acc") or 0.0),
            play_count=int(stats.get("plays") or 0),
            ranked_score=int(stats.get("rscore") or 0),
            total_score=int(stats.get("tscore") or 0),
            max_combo=int(stats.get("max_combo") or 0),
            playtime=int(stats.get("playtime") or 0),
        )


@dataclass
class BanchoStatus:
    online: bool = False
    action: int = 0
    mode: int = 0
    mods: int = 0
    info_text: str = ""
    beatmap_md5: str = ""
    beatmap_title: str = ""


@dataclass
class BanchoSnapshot:
    ok: bool = False
    error: str | None = None
    updated_at: float = 0.0
    player_id: int = 0
    name: str = ""
    country: str = ""
    stats: dict[str, BanchoStats] = field(default_factory=dict)
    status: BanchoStatus = field(default_factory=BanchoStatus)


class BanchoClient:
    """Polls one bancho.py instance for a player's server-side data."""

    def __init__(self, api_url: str, player: str, interval: float = 15.0):
        self.api_url = api_url.rstrip("/")
        self.player = player
        self.interval = interval
        self._snapshot = BanchoSnapshot()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="bancho", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    @property
    def enabled(self) -> bool:
        return bool(self.api_url and self.player)

    def latest(self) -> BanchoSnapshot:
        with self._lock:
            return self._snapshot

    def _run(self) -> None:
        while not self._stop.is_set():
            snapshot = self.fetch()
            with self._lock:
                self._snapshot = snapshot
            self._stop.wait(self.interval)

    def fetch(self) -> BanchoSnapshot:
        """One refresh; never raises."""
        snapshot = BanchoSnapshot(updated_at=time.time())
        try:
            info = self._get("get_player_info", scope="all", name=self.player)
            player = info.get("player") or {}
            card = player.get("info") or {}
            snapshot.player_id = int(card.get("id") or 0)
            snapshot.name = str(card.get("name") or self.player)
            snapshot.country = str(card.get("country") or "")
            stats = player.get("stats") or {}
            preferred = int(card.get("preferred_mode") or 0)
            for mode_key, mode_stats in stats.items():
                try:
                    mode = int(mode_key)
                except (TypeError, ValueError):
                    continue
                snapshot.stats[mode] = BanchoStats.from_api(mode_stats)
            # Rank is global across a leaderboard; read it for the main mode.
            if preferred in snapshot.stats:
                snapshot.stats[preferred].global_rank = self._leaderboard_rank(
                    preferred, snapshot.player_id
                )
            status = self._get("get_player_status", id=snapshot.player_id)
            status_data = (status.get("player_status") or {})
            snapshot.status.online = bool(status_data.get("online"))
            inner = status_data.get("status") or {}
            snapshot.status.action = int(inner.get("action") or 0)
            snapshot.status.mode = int(inner.get("mode") or 0)
            snapshot.status.mods = int(inner.get("mods") or 0)
            snapshot.status.info_text = str(inner.get("info_text") or "")
            beatmap = inner.get("beatmap")
            if isinstance(beatmap, dict):
                snapshot.status.beatmap_md5 = str(beatmap.get("md5") or "")
                snapshot.status.beatmap_title = str(beatmap.get("title") or "")
            snapshot.ok = True
        except Exception as exc:
            snapshot.error = f"{type(exc).__name__}: {exc}"
        return snapshot

    def _leaderboard_rank(self, mode: int, player_id: int) -> int:
        """The player's position, by scanning the paged global leaderboard."""
        if not player_id:
            return 0
        offset = 0
        while offset < 500:
            data = self._get(
                "get_leaderboard", mode=mode, limit=100, offset=offset, sort="pp"
            )
            rows = data.get("leaderboard") or []
            if not rows:
                return 0
            for index, row in enumerate(rows):
                if int(row.get("player_id") or 0) == player_id:
                    return offset + index + 1
            if len(rows) < 100:
                return 0
            offset += len(rows)
        return 0

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v != ""})
        url = f"{self.api_url}/v1/{path}" + (f"?{query}" if query else "")
        request = urllib.request.Request(
            url, headers={"Accept": "application/json", "User-Agent": "osu-live/1.0"}
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if isinstance(payload, dict) and payload.get("status") not in (None, "success"):
            raise RuntimeError(str(payload.get("message") or payload.get("status")))
        return payload
