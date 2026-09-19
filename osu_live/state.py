"""Continuously merges memory reads, pp calculation and bancho.py data.

One background thread owns the osu! process; every consumer reads the latest
immutable state, so a slow client can never stall the poller.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict
from typing import Any

from .bancho_api import BanchoClient, BanchoSnapshot
from .config import Config
from .paths import DATA_ROOT
from .pp_engine import PpEngine
from .reader import HitCounts, OsuReader, Snapshot, set_failure_sink
from .winmem import MemoryError_

#: How long the selected map must hold still before pp is calculated. Song
#: select changes maps on every keypress while scrolling.
_SELECT_DEBOUNCE_MS = 400

#: Consecutive unreadable frames tolerated before the reader is rebuilt. osu!
#: frees its gameplay and profile objects the instant a map ends, so reads fail
#: for a few frames around that transition; 8 frames is roughly 400ms at the
#: default poll interval, comfortably past the gap but short enough that a
#: genuinely closed client is noticed within about half a second.
_READ_FAILURES_BEFORE_REATTACH = 8

#: Where read failures are recorded. A failure that lasts a few frames is
#: invisible by the time anyone looks at the overlay, so it is written down as
#: it happens rather than reconstructed afterwards. Beside the executable in a
#: frozen build, since the bundle itself is temporary.
LOG_PATH = DATA_ROOT / ".osu_live.log"

log = logging.getLogger("osu_live")
log.setLevel(logging.INFO)
log.propagate = False
if not log.handlers:
    try:
        _handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
        _handler.setFormatter(
            logging.Formatter("%(asctime)s.%(msecs)03d %(message)s", "%H:%M:%S")
        )
        log.addHandler(_handler)
    except OSError:
        # A read-only install must still run; it just loses the log.
        log.addHandler(logging.NullHandler())

# A field that fails while the rest of the frame reads cleanly never surfaces
# to the caller as an error, so the reader hands it here directly. Registered
# at import so it is in place before any reader exists.
set_failure_sink(lambda message: log.info("degraded  %s", message))


def _hits_from(snapshot: Snapshot) -> HitCounts | None:
    gameplay = snapshot.gameplay
    if not gameplay:
        return None
    hits = gameplay.get("hits") or {}
    return HitCounts(
        hit300=int(hits.get("hit300", 0)),
        hit100=int(hits.get("hit100", 0)),
        hit50=int(hits.get("hit50", 0)),
        geki=int(hits.get("geki", 0)),
        katu=int(hits.get("katu", 0)),
        miss=int(hits.get("miss", 0)),
    )


class StateManager:
    """Owns the reader thread and exposes the newest merged state."""

    def __init__(self, config: Config):
        self.config = config
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {"connected": False, "error": None}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._reader: OsuReader | None = None
        self._engine = PpEngine(config.songs_folder)
        self._last_pp: dict[str, Any] | None = None
        #: pp of the finished play, keyed by the score it was calculated for.
        self._score_pp_key: tuple[str, str, float] | None = None
        self._score_pp: dict[str, Any] | None = None
        self._bancho = BanchoClient(
            config.bancho_api_url, config.bancho_player, config.bancho_interval_s
        )
        self._generation = 0  # bumped when a new state is published
        #: Newest fully-read state, re-published while the client is
        #: mid-transition so a map change never blanks the overlay.
        self._last_good: dict[str, Any] | None = None
        self._failures = 0
        #: Which map the cached pp belongs to, and when it was computed.
        self._pp_key_of_last: tuple[str, str, int] | None = None
        self._pp_computed_at = 0.0
        log.info("state manager created (songs=%s)", config.songs_folder or "-")

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        self._bancho.start()
        self._thread = threading.Thread(target=self._run, name="reader", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        self._bancho.stop()
        if self._reader is not None:
            self._reader.close()
            self._reader = None

    @property
    def generation(self) -> int:
        return self._generation

    def state(self) -> dict[str, Any]:
        with self._lock:
            return self._state

    def wait_for_update(self, generation: int, timeout: float = 25.0) -> int:
        """Block until state changes; returns the generation it observed."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._generation != generation:
                return self._generation
            time.sleep(0.02)
        return self._generation

    # -- polling -------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.perf_counter()
            try:
                if self._reader is None or not self._reader.is_alive():
                    self._reader = OsuReader()
                snapshot = self._reader.snapshot()
                if not snapshot.ok:
                    # A read failed. On its own that means very little: osu!
                    # frees the gameplay and profile objects the instant a map
                    # ends, so the transition into the results screen always
                    # produces a few bad frames. Republishing the last complete
                    # state carries the overlay through that gap; only a run of
                    # failures is treated as the client being gone.
                    self._on_read_failure(snapshot.error or "read failed")
                    elapsed = time.perf_counter() - started
                    self._stop.wait(max(0.0, self.config.poll_interval_ms / 1000 - elapsed))
                    continue

                if self._failures:
                    log.info("recovered after %d unreadable frame(s)", self._failures)
                self._failures = 0
                self._publish(self._advance(snapshot))
            except MemoryError_ as exc:
                # No reader, or one that cannot even be attached: osu! is not
                # running. Tear it down so the next tick does a fresh process
                # lookup, which is what notices the game being restarted.
                self._on_read_failure(str(exc))
                self._stop.wait(1.0)
                continue
            except Exception as exc:  # never let the poller die
                self._reader = None
                self._publish({"connected": False, "error": f"{type(exc).__name__}: {exc}"})
                self._stop.wait(0.5)
                continue
            elapsed = time.perf_counter() - started
            self._stop.wait(max(0.0, self.config.poll_interval_ms / 1000 - elapsed))

    def _on_read_failure(self, error: str) -> None:
        """Keep serving through a read gap, or declare the client gone.

        Holding the state rather than flapping to "disconnected" is what stops
        a map ending from flashing the overlay; rebuilding the reader is
        deliberately last because a reattach re-scans the whole address space
        for every signature, far too expensive to pay for a single unreadable
        frame. The held state is rebuilt rather than replayed so its clock and
        progress keep moving -- a frozen picture reads as a frozen overlay.
        """
        self._failures += 1
        transient = (
            self._failures < _READ_FAILURES_BEFORE_REATTACH
            and self._reader is not None
            and self._last_good is not None
        )
        if transient:
            log.info("held      frame %d/%d unreadable: %s",
                     self._failures, _READ_FAILURES_BEFORE_REATTACH, error)
            payload = self._degenerate(self._last_good, error)
        else:
            log.warning("reattach  after %d unreadable frame(s), last error: %s",
                        self._failures, error)
            if self._reader is not None:
                self._reader.close()
                self._reader = None
            payload = {
                "connected": False,
                "error": error,
                "waiting_for": "osu!.exe",
                "pp": self._last_pp,
                "score_pp": self._score_pp,
            }
        self._publish(payload)

    def _degenerate(self, last: dict[str, Any], error: str) -> dict[str, Any]:
        """Carry the last good read forward while the client is unreadable.

        Live-only values are dropped rather than shown stale: a judgement count
        or HP bar that stops updating mid-play is worse than one that empties,
        because nothing on screen says it has stopped being true. The map,
        account and pp are kept -- those are what the next screen needs.
        """
        payload = dict(last)
        payload["timestamp"] = time.time()
        payload["error"] = error
        payload["stale"] = True
        payload["gameplay"] = None
        payload["key_overlay"] = []
        payload["hit_errors"] = []
        return payload

    def _advance(self, snapshot: Snapshot) -> dict[str, Any]:
        """Refresh pp for a successfully read frame and build its payload."""
        if self._reader is not None:
            key = self._pp_key(snapshot)
            now = time.perf_counter()
            if key != self._pp_key_of_last:
                # A different map is selected: never show the old map's pp
                # under the new title, even for one frame.
                self._last_pp = None
                self._pp_key_of_last = key
            if self._should_compute_pp(snapshot, now, self._pp_computed_at):
                self._last_pp = self._compute_pp(snapshot)
                self._pp_computed_at = time.perf_counter()

        # pp for the finished play, recalculated when a new score lands.
        score = snapshot.last_score
        score_key = None
        if score:
            beatmap = score.get("beatmap")
            score_key = (
                getattr(beatmap, "folder", "") or "",
                getattr(beatmap, "filename", "") or "",
                float(score.get("captured_at") or 0.0),
            )
        if score_key != self._score_pp_key:
            self._score_pp_key = score_key
            self._score_pp = self._compute_score_pp(score) if score else None

        payload = self._build(snapshot)
        payload["pp"] = self._last_pp
        payload["score_pp"] = self._score_pp
        return payload

    @staticmethod
    def _pp_key(snapshot: Snapshot) -> tuple[str, str, int]:
        """What a pp figure belongs to: map, difficulty, mods and mode."""
        beatmap = snapshot.beatmap
        mode = StateManager._enabled_mode(snapshot)
        return (beatmap.folder, beatmap.filename, int(snapshot.mods.get("bits", 0)) ^ mode)

    def _can_compute_pp(self, snapshot: Snapshot) -> bool:
        beatmap = snapshot.beatmap
        return bool(
            self._engine.songs_folder and beatmap.filename and beatmap.folder
        )

    def _should_compute_pp(
        self, snapshot: Snapshot, now: float, computed_at: float
    ) -> bool:
        """Whether to calculate pp on this tick.

        Song select is where you browse for pp, so a newly selected map is
        always calculated -- after a short settle, since scrolling changes maps
        rapidly. Mid-play it refreshes no faster than ``pp_interval_ms``.
        """
        if not self._can_compute_pp(snapshot):
            return False
        interval = (
            self.config.pp_interval_ms
            if snapshot.state == "play"
            else _SELECT_DEBOUNCE_MS
        )
        return (now - computed_at) * 1000 >= interval

    @staticmethod
    def _enabled_mode(snapshot: Snapshot) -> int:
        """The ruleset the current pp belongs to.

        During a play the score carries its own mode. Outside one -- song
        select, where pp is browsed most -- osu! only lets a map be selected
        under its own ruleset, so the profile's active mode is that map's mode.
        Without this, taiko/catch/mania maps were priced as osu!standard.
        """
        gameplay = snapshot.gameplay or {}
        if gameplay:
            return int(gameplay.get("mode") or 0)
        return int(snapshot.player.get("play_mode") or 0)

    def _compute_pp(self, snapshot: Snapshot) -> dict[str, Any] | None:
        beatmap = snapshot.beatmap
        if not beatmap.filename or not beatmap.folder:
            return None
        return self._pp_payload(
            beatmap,
            self._enabled_mode(snapshot),
            int(snapshot.mods.get("bits", 0)),
            _hits_from(snapshot),
            (snapshot.gameplay or {}).get("combo"),
        )

    def _pp_payload(
        self,
        beatmap,
        mode: int,
        mods: int,
        hits: HitCounts | None,
        combo: int | None,
    ) -> dict[str, Any]:
        result = self._engine.compute(
            beatmap.folder or "Songs",
            beatmap.filename,
            mode,
            mods,
            hits,
            combo,
            beatmap.object_count,
        )
        return {
            "error": result.error,
            "current": asdict(result.current),
            "fc": asdict(result.fc),
            "max": asdict(result.max),
            "acc_list": result.acc_list,
        }

    def _compute_score_pp(self, score: dict[str, Any]) -> dict[str, Any] | None:
        """pp for a *finished* play.

        osu! tears down the gameplay structures the moment a map ends, so the
        live pp block would otherwise recompute from no hits at all and report
        the map's theoretical maximum as if it were the score's value. The
        captured score carries its own hits, so it is recalculated from those.
        """
        beatmap = score.get("beatmap")
        if beatmap is None or not getattr(beatmap, "filename", ""):
            return None
        hits_data = score.get("hits") or {}
        hits = HitCounts(
            hit300=int(hits_data.get("hit300", 0)),
            hit100=int(hits_data.get("hit100", 0)),
            hit50=int(hits_data.get("hit50", 0)),
            geki=int(hits_data.get("geki", 0)),
            katu=int(hits_data.get("katu", 0)),
            miss=int(hits_data.get("miss", 0)),
        )
        payload = self._pp_payload(
            beatmap,
            int(score.get("mode", 0)),
            int((score.get("mods") or {}).get("bits", 0)),
            hits,
            score.get("combo"),
        )
        return payload

    def _build(self, snapshot: Snapshot) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "connected": True,
            "error": None,
            "pid": snapshot.pid,
            "timestamp": snapshot.timestamp,
            "state": snapshot.state,
            "state_id": snapshot.state_id,
            "play_time": snapshot.play_time,
            "audio_length": round(snapshot.audio_length, 3),
            "progress": _progress(snapshot.play_time, snapshot.audio_length),
            "is_watching_replay": snapshot.is_watching_replay,
            "player": snapshot.player,
            "beatmap": snapshot.beatmap.__dict__ | {"full_name": snapshot.beatmap.full_name},
            "mods": snapshot.mods,
            "gameplay": snapshot.gameplay,
            "key_overlay": snapshot.key_overlay,
            "hit_errors": snapshot.hit_errors[-64:],
            "leaders": snapshot.leaders,
            "last_score": _last_score_json(snapshot.last_score),
        }
        bancho = self._bancho
        payload["bancho"] = _bancho_json(bancho, bancho.latest(), snapshot.player)
        return payload

    def _publish(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self._state = payload
            self._generation += 1
            if payload.get("connected"):
                self._last_good = payload


def _last_score_json(score: dict[str, Any] | None) -> dict[str, Any] | None:
    """Serialise the cached last play, expanding its nested beatmap."""
    if not score:
        return None
    beatmap = score.get("beatmap")
    data = dict(score)
    data["beatmap"] = (
        beatmap.__dict__ | {"full_name": beatmap.full_name}
        if beatmap is not None
        else None
    )
    return data


def _progress(play_time: int, audio_length: float) -> float:
    if audio_length <= 0:
        return 0.0
    return max(0.0, min(1.0, play_time / audio_length))


def _bancho_json(
    bancho: BanchoClient, snapshot: BanchoSnapshot, player: dict[str, Any]
) -> dict[str, Any]:
    if not bancho.enabled:
        return {"enabled": False}
    mode = int(player.get("play_mode") or 0)
    stats = snapshot.stats.get(mode)
    return {
        "enabled": True,
        "ok": snapshot.ok,
        "error": snapshot.error,
        "updated_at": snapshot.updated_at,
        "player_id": snapshot.player_id,
        "name": snapshot.name,
        "country": snapshot.country,
        "mode": mode,
        "stats": asdict(stats) if stats else None,
        "status": asdict(snapshot.status),
    }
