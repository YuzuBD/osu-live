"""Reads live osu! stable game state out of process memory.

Structure offsets are calibrated against the client in this environment: osu!
stable ``b20240820.1`` (Stable40, reported as ``Version 20240820.1`` in
``Logs/session.log``). Signatures come from tosu.

osu! moves these structures between builds, so instead of trusting one layout
the reader probes the candidates and accepts whichever is self-consistent --
``_gameplay_base``, ``_key_overlay_base`` and ``_leaderboard_base`` all work
this way. A build where a location moved costs only that one field.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import signatures
from .winmem import MemoryError_, Process, list_processes

#: Where a partial read failure is reported. A field that fails while the rest
#: of the frame succeeds never reaches the caller as an error -- it comes back
#: empty, which is right for the overlay but useless for diagnosing a
#: transition. Anything registered here hears about it as it happens.
_failure_sink: Callable[[str], None] | None = None


def set_failure_sink(sink: Callable[[str], None] | None) -> None:
    """Register a callback for read failures that would otherwise be silent."""
    global _failure_sink
    _failure_sink = sink


def _note_failure(message: str) -> None:
    if _failure_sink is not None:
        try:
            _failure_sink(message)
        except Exception:
            pass  # diagnostics must never break the read path

#: Where the most recent completed play is kept, so the results screen survives
#: an osu! restart or this program restarting.
LAST_SCORE_PATH = Path(__file__).resolve().parent.parent / ".last_score.json"

#: osu! process names, stable first.
OSU_PROCESS_NAMES = ("osu!.exe",)

#: ``Ruleset`` members.
_RULESET_GAMEPLAY_NEW = 0x64
_RULESET_GAMEPLAY_OLD = 0x68
_RULESET_LEADERBOARD_NEW = 0x78
_RULESET_LEADERBOARD_OLD = 0x7C
_RULESET_KEY_OVERLAY_NEW = 0xAC
_RULESET_KEY_OVERLAY_OLD = 0xB0
_RULESET_SCORE_NEW = 0xF8

#: ``Gameplay`` members.
_GAMEPLAY_SCORE = 0x38
_GAMEPLAY_HP_BAR = 0x40
_GAMEPLAY_ACCURACY = 0x48
_GAMEPLAY_SCORE_PROCESSOR = 0x54

#: ``Score`` members.
_SCORE_MODS_PAIR = 0x1C
_SCORE_PLAYER_NAME = 0x28
_SCORE_HIT_ERRORS = 0x38
_SCORE_MODE = 0x64
_SCORE_MAX_COMBO = 0x68
_SCORE_TOTAL = 0x78
_SCORE_HIT100 = 0x88
_SCORE_HIT300 = 0x8A
_SCORE_HIT50 = 0x8C
_SCORE_HIT_GEKI = 0x8E
_SCORE_HIT_KATU = 0x90
_SCORE_HIT_MISS = 0x92
_SCORE_COMBO = 0x94

#: ``UserProfile`` members. Builds disagree on the tail: tosu reads pp at 0x88
#: and the country code at 0x9c, while the build calibrated here has pp at 0x9c
#: and the country code at 0x98. ``_profile_layout`` picks between them.
_PROFILE_ACCURACY = 0x04
_PROFILE_RANKED_SCORE = 0x0C
_PROFILE_PLAYER_NAME = 0x30
_PROFILE_PLAYER_ID = 0x70
_PROFILE_LEVEL = 0x74
_PROFILE_PLAY_COUNT = 0x7C
_PROFILE_PLAY_MODE = 0x80
_PROFILE_BANCHO_STATUS = 0x8C
_PROFILE_PP_NEW = 0x88
_PROFILE_COUNTRY_NEW = 0x9C
_PROFILE_COUNTRY_LEGACY = 0x98
_PROFILE_PP_LEGACY = 0x9C

#: Game state ids (index into ``signatures.GAME_STATES``) the reader branches on.
_STATE_PLAY = 2
_STATE_WATCHING_REPLAY = 25

#: Sanity bound for a key's press count. A real play cannot exceed a few
#: thousand presses per key; larger means we are reading the wrong object.
_MAX_KEY_COUNT = 200_000

#: Largest index in osu!'s country enumeration.
_MAX_COUNTRY_CODE = 260

#: osu! mods bit flags (stable).
MOD_NIGHTCORE = 1 << 9
MOD_DOUBLETIME = 1 << 6
MOD_HALFTIME = 1 << 8
MOD_HIDDEN = 1 << 3
MOD_FLASHLIGHT = 1 << 10
MOD_SCOREV2 = 1 << 29


@dataclass
class HitCounts:
    hit300: int = 0
    hit100: int = 0
    hit50: int = 0
    geki: int = 0
    katu: int = 0
    miss: int = 0

    @property
    def total(self) -> int:
        return self.hit300 + self.hit100 + self.hit50 + self.miss


@dataclass
class BeatmapInfo:
    checksum: str = ""
    filename: str = ""
    folder: str = ""
    background: str = ""
    artist: str = ""
    title: str = ""
    difficulty: str = ""
    creator: str = ""
    ar: float = 0.0
    cs: float = 0.0
    hp: float = 0.0
    od: float = 0.0
    map_id: int = 0
    set_id: int = 0
    object_count: int = 0
    ranked_status: int = 0

    @property
    def full_name(self) -> str:
        if not self.title:
            return ""
        if self.difficulty:
            return f"{self.artist} - {self.title} [{self.difficulty}]".strip(" -")
        return f"{self.artist} - {self.title}".strip(" -")


@dataclass
class Snapshot:
    """One consistent read of the client, ready to be serialised."""

    ok: bool = False
    error: str | None = None
    pid: int = 0
    timestamp: float = field(default_factory=time.time)
    state: str = "unknown"
    state_id: int = -1
    play_time: int = 0
    audio_length: float = 0.0
    is_watching_replay: bool = False
    player: dict[str, Any] = field(default_factory=dict)
    beatmap: BeatmapInfo = field(default_factory=BeatmapInfo)
    mods: dict[str, Any] = field(default_factory=dict)
    gameplay: dict[str, Any] | None = None
    key_overlay: list[dict[str, Any]] = field(default_factory=list)
    hit_errors: list[int] = field(default_factory=list)
    leaders: list[dict[str, Any]] = field(default_factory=list)
    last_score: dict[str, Any] | None = None


class OsuReader:
    """Attaches to an osu! stable process and reads its live state."""

    def __init__(self, pid: int | None = None):
        self._process: Process | None = None
        self.pid = 0
        self._patterns: dict[str, int] = {}
        self._gameplay_old: bool | None = None
        self._beatmap_cache: tuple[str, BeatmapInfo] | None = None
        self._last_score: dict[str, Any] | None = self._load_last_score()
        #: Fields currently reported as degraded, so each failure is logged
        #: once per run rather than once per frame.
        self._noted: dict[str, bool] = {}
        self.attach(pid)

    # -- lifecycle -----------------------------------------------------------

    @property
    def process(self) -> Process:
        if self._process is None:
            raise MemoryError_("not attached to osu!")
        return self._process

    def attach(self, pid: int | None = None) -> None:
        if pid is None:
            found = list_processes(OSU_PROCESS_NAMES)
            if not found:
                raise MemoryError_("osu!.exe is not running")
            pid = found[0][0]
        self.close()
        self._process = Process(pid)
        self.pid = pid
        self._patterns.clear()
        self._gameplay_old = None
        self._beatmap_cache = None

    def close(self) -> None:
        if self._process is not None:
            self._process.close()
            self._process = None

    def __enter__(self) -> "OsuReader":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def is_alive(self) -> bool:
        if self._process is None:
            return False
        try:
            self._process.read_u32(self.pattern("base"))
            return True
        except MemoryError_:
            return False

    # -- pattern resolution --------------------------------------------------

    def resolve(self, force: bool = False) -> dict[str, int]:
        """Locate every signature, applying each pattern's offset."""
        if self._patterns and not force:
            return self._patterns
        resolved: dict[str, int] = {}
        for name, sig in signatures.SIGNATURES.items():
            address = self.process.scan_one(sig.pattern)
            resolved[name] = address + sig.offset
        self._patterns = resolved
        self._gameplay_old = None
        return resolved

    def pattern(self, name: str) -> int:
        if not self._patterns:
            self.resolve()
        return self._patterns[name]

    # -- pointer chains ------------------------------------------------------

    @property
    def ruleset(self) -> int:
        """``[[rulesets - 0xb] + 0x4]`` -- the active ruleset instance."""
        base = self.process.read_u32(self.pattern("rulesets") - 0xB)
        return self.process.read_u32(base + 0x4)

    def _gameplay_base(self, ruleset: int) -> int:
        """The ``Gameplay`` instance, or 0 when there is none right now.

        Builds disagree on where the ruleset keeps it (0x64 vs 0x68; see the
        module docstring), and the wrong one is often a non-zero pointer to an
        unrelated object. So a candidate is accepted only when the score it
        points at actually looks like a score: a loadable player name at +0x28.

        The remembered layout is re-probed rather than trusted. It records
        *which offset* this build uses, but says nothing about whether the
        object behind it still exists -- and osu! frees ``Gameplay`` the moment
        a map ends, after which the slot reads back as recycled data (a small
        integer, a length, another object). Returning that unchecked is what
        produced ``ReadProcessMemory(0x10039, 4)`` on a finished map.
        """
        if self._gameplay_old is not None:
            offset = _RULESET_GAMEPLAY_OLD if self._gameplay_old else _RULESET_GAMEPLAY_NEW
            cached = self._optional(
                lambda offset=offset: self.process.read_u32(ruleset + offset), 0
            )
            if cached and self._looks_like_gameplay(cached):
                return cached
        for old_layout in (True, False):
            offset = _RULESET_GAMEPLAY_OLD if old_layout else _RULESET_GAMEPLAY_NEW
            candidate = self._optional(
                lambda offset=offset: self.process.read_u32(ruleset + offset), 0
            )
            if candidate and self._looks_like_gameplay(candidate):
                self._gameplay_old = old_layout
                return candidate
        return 0

    def _looks_like_gameplay(self, gameplay_base: int) -> bool:
        """True when ``gameplay_base`` really is a ``Gameplay`` instance."""
        def probe() -> bool:
            score_base = self.process.read_u32(gameplay_base + _GAMEPLAY_SCORE)
            if not score_base:
                return False
            name = self.process.read_csharp_string(
                self.process.read_u32(score_base + _SCORE_PLAYER_NAME)
            )
            return bool(name) and len(name) <= 32

        return bool(self._optional(probe, False))

    def _key_overlay_base(self, ruleset: int) -> int:
        """The key overlay object; layout differs across builds like gameplay's."""
        for offset in (_RULESET_KEY_OVERLAY_OLD, _RULESET_KEY_OVERLAY_NEW):
            candidate = self._optional(
                lambda offset=offset: self.process.read_u32(ruleset + offset), 0
            )
            if candidate and self._looks_like_key_overlay(candidate):
                return candidate
        return 0

    def _looks_like_key_overlay(self, pointer: int) -> bool:
        """A key overlay holds the .NET button array, whose length is at least 4."""
        def probe() -> bool:
            array = self.process.read_u32(self.process.read_u32(pointer + 0x10) + 0x4)
            return bool(array) and self.process.read_i32(array + 0x4) >= 4

        return bool(self._optional(probe, False))

    def user_profile(self) -> int:
        return self.process.read_ptr_ptr(self.pattern("user_profile"))

    def _profile_layout(self) -> tuple[int, int]:
        """``(pp offset, country offset)`` for this build.

        tosu's layout puts pp at 0x88 and the country code at 0x9c; the build
        calibrated here swaps them (pp at 0x9c, country at 0x98). Country codes
        are small enum indices, while pp is normally in the thousands, so the
        two slots discriminate the layouts unambiguously.
        """
        def read(offset: int) -> int:
            return self._optional(
                lambda: self.process.read_u32(self.user_profile() + offset), -1
            )

        if 0 < read(_PROFILE_COUNTRY_NEW) <= _MAX_COUNTRY_CODE:
            return _PROFILE_PP_NEW, _PROFILE_COUNTRY_NEW
        return _PROFILE_PP_LEGACY, _PROFILE_COUNTRY_LEGACY

    # -- snapshot ------------------------------------------------------------

    @staticmethod
    def _optional(read: Any, default: Any) -> Any:
        """Run ``read`` and fall back to ``default`` when memory is unreadable."""
        try:
            return read()
        except MemoryError_:
            return default

    def snapshot(self) -> Snapshot:
        """Read the complete state in one pass.

        Smooths over the moment a play ends. osu! frees the gameplay, score and
        profile objects on the frame it switches to the results screen, so a
        pointer that resolved a millisecond ago can still be dereferenced just
        after it was freed -- a wild read that lands on a low address and comes
        back ``ERROR_PARTIAL_COPY``. Those gaps last a few milliseconds, so the
        read is retried inside this frame before it is called a failure; only a
        state that stays unreadable is handed up as ``ok=False``.
        """
        last: MemoryError_ | None = None
        for attempt in range(3):
            try:
                return self._snapshot()
            except MemoryError_ as exc:
                last = exc
                if attempt < 2:
                    time.sleep(0.004)
        return Snapshot(ok=False, error=str(last), pid=self.pid)

    def _snapshot(self) -> Snapshot:
        process = self.process
        snap = Snapshot(ok=True, pid=self.pid)

        status = process.read_ptr_ptr(self.pattern("status"))
        snap.state_id = status
        snap.state = signatures.game_state(status)
        snap.play_time = process.read_i32(process.read_u32(self.pattern("play_time") + 0x5))

        # [[audio_length]] + 0x4 holds the track length in milliseconds.
        snap.audio_length = self._optional(
            lambda: process.read_f64(
                process.read_ptr_ptr(self.pattern("audio_length")) + 0x4
            ),
            default=0.0,
        )

        # The replay flag's own field moved across builds, while ``status`` has
        # kept its meaning, so derive it from there.
        snap.is_watching_replay = snap.state_id == _STATE_WATCHING_REPLAY

        ruleset = self.ruleset
        snap.player = self._read_player()
        snap.beatmap = self._cached_beatmap(self.pattern("base"))
        snap.mods = self._read_mods(ruleset, snap.state_id)

        gameplay_base = self._gameplay_base(ruleset)
        if gameplay_base:
            # Safe to attempt: a gameplay object that is mid-teardown yields no
            # block rather than an exception, same as a freed profile does.
            snap.gameplay = self._optional(
                lambda: self._read_gameplay(gameplay_base, ruleset, snap.mods), None
            )
        mode = (snap.gameplay or {}).get("mode", 0)

        snap.key_overlay = self._read_key_overlay(ruleset, mode, snap.state_id)
        snap.hit_errors = self._read_hit_errors(ruleset)
        snap.leaders = self._read_leaders(ruleset)
        snap.last_score = self._track_last_score(snap)
        return snap

    def _track_last_score(self, snap: Snapshot) -> dict[str, Any] | None:
        """Keep the most recent completed play for the results screen.

        osu! clears the gameplay structures the moment a map ends, and the
        result screen object sits at a build-specific offset, so the score is
        captured from the last frame in which it was still readable. It is
        written to disk because the reader reconnects whenever osu! restarts,
        and the results screen must survive that.
        """
        if snap.state_id == _STATE_PLAY and snap.gameplay:
            score = {
                "player_name": snap.gameplay.get("player_name", ""),
                "mode": snap.gameplay.get("mode", 0),
                "score": snap.gameplay.get("score", 0),
                "combo": snap.gameplay.get("combo", 0),
                "max_combo": snap.gameplay.get("max_combo", 0),
                "accuracy": snap.gameplay.get("accuracy", 0.0),
                "grade": snap.gameplay.get("grade", ""),
                "hits": dict(snap.gameplay.get("hits") or {}),
                "mods": dict(snap.mods),
                "beatmap": snap.beatmap,
                "captured_at": time.time(),
            }
            # Only persist a meaningful play; a map that was just started would
            # otherwise overwrite the last real score.
            if score["score"] > 0:
                self._last_score = score
                self._save_last_score(score)
        return self._last_score

    def _save_last_score(self, score: dict[str, Any]) -> None:
        beatmap = score.get("beatmap")
        payload = dict(score)
        payload["beatmap"] = beatmap.__dict__ if beatmap is not None else None
        try:
            LAST_SCORE_PATH.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass  # a read-only install must not break live data

    def _load_last_score(self) -> dict[str, Any] | None:
        try:
            raw = json.loads(LAST_SCORE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        beatmap = raw.get("beatmap")
        if isinstance(beatmap, dict):
            raw["beatmap"] = BeatmapInfo(**{
                k: v for k, v in beatmap.items() if k in BeatmapInfo.__dataclass_fields__
            })
        # Recompute the grade instead of trusting what was stored: a cached file
        # can outlive a change to the grading rules, and a stale letter would be
        # served for as long as the file is kept.
        hits = raw.get("hits")
        if isinstance(hits, dict):
            raw["grade"] = grade(
                int(raw.get("mode", 0)),
                HitCounts(**{k: int(hits.get(k, 0)) for k in
                             ("hit300", "hit100", "hit50", "geki", "katu", "miss")}),
                float(raw.get("accuracy", 0.0)),
                int((raw.get("mods") or {}).get("bits", 0)),
            )
        return raw

    def _read_player(self) -> dict[str, Any]:
        process = self.process
        profile = self.user_profile()
        if not profile:
            # Zero here means the profile object is gone or not yet built. The
            # overlay falls back to "not logged in", which is what the account
            # panel flashing blank during a screen change looks like.
            self._note_once("player", "user_profile resolved to 0")
            return {}
        # The profile object is among the things osu! frees between screens, so
        # a partially valid pointer is expected rather than exceptional. An
        # unreadable player block costs the account panel, not the whole frame.
        try:
            return self._read_player_fields(process, profile)
        except MemoryError_ as exc:
            self._note_once("player", str(exc))
            return {}

    def _note_once(self, key: str, message: str) -> None:
        """Report a degraded field once per run of failures.

        Clearing the flag when the field reads cleanly again keeps the log
        proportional to how often something goes wrong, not to the frame rate.
        """
        if self._noted.get(key):
            return
        self._noted[key] = True
        _note_failure(f"{key}: {message}")

    def _cleared(self, key: str) -> None:
        self._noted.pop(key, None)

    def _read_player_fields(self, process: Process, profile: int) -> dict[str, Any]:
        name = process.read_csharp_string(process.read_u32(profile + _PROFILE_PLAYER_NAME))
        pp_offset, country_offset = self._profile_layout()
        data = {
            "name": name,
            "accuracy": round(process.read_f64(profile + _PROFILE_ACCURACY), 3),
            "ranked_score": process.read_i64(profile + _PROFILE_RANKED_SCORE),
            "id": process.read_i32(profile + _PROFILE_PLAYER_ID),
            "level": round(process.read_f32(profile + _PROFILE_LEVEL), 2),
            "play_count": process.read_i32(profile + _PROFILE_PLAY_COUNT),
            "play_mode": process.read_i32(profile + _PROFILE_PLAY_MODE),
            "pp": process.read_i32(profile + pp_offset),
            "country_code": process.read_i32(profile + country_offset),
            "bancho_status": signatures.bancho_status(
                process.read_u8(profile + _PROFILE_BANCHO_STATUS)
            ),
            "login_status": signatures.login_status(
                process.read_u8(process.read_u32(self.pattern("raw_login_status")))
            ),
        }
        self._cleared("player")
        return data

    def _cached_beatmap(self, base: int) -> BeatmapInfo:
        """Beatmap metadata, re-read only when the selected map changes.

        A null beatmap pointer is handled inside ``_read_beatmap``, but a
        *recycled* one still throws on every field offset -- the same freed-page
        situation as the gameplay object. Losing the map detail for a frame is
        survivable; failing the whole read is not.
        """
        if self._beatmap_cache is not None:
            checksum = self._beatmap_cache[0]
            if checksum and self._checksum_unchanged(base, checksum):
                self._cleared("beatmap")
                return self._beatmap_cache[1]
        try:
            info = _read_beatmap(self.process, base)
        except MemoryError_ as exc:
            self._note_once("beatmap", str(exc))
            return self._beatmap_cache[1] if self._beatmap_cache else BeatmapInfo()
        if info.filename or info.title:
            self._cleared("beatmap")
        else:
            self._note_once("beatmap", "beatmap resolved to an empty record")
        self._beatmap_cache = (info.checksum, info)
        return info

    def _checksum_unchanged(self, base: int, expected: str) -> bool:
        try:
            beatmap = self.process.read_ptr_ptr(base - 0xC)
            if not beatmap:
                return False
            current = self.process.read_csharp_string(
                self.process.read_u32(beatmap + 0x6C)
            )
        except MemoryError_:
            return False
        return current == expected

    def _read_mods(self, ruleset: int, state_id: int) -> dict[str, Any]:
        process = self.process
        raw = 0
        if state_id == 2:  # play
            # The play's mods come off the score, which disappears with the
            # play; losing them costs the mod chips, not the frame.
            try:
                gameplay_base = self._gameplay_base(ruleset)
                score_base = (
                    process.read_u32(gameplay_base + _GAMEPLAY_SCORE) if gameplay_base else 0
                )
                if score_base:
                    pair = process.read_u32(score_base + _SCORE_MODS_PAIR)
                    if pair:
                        raw = process.read_u32(pair + 0xC) ^ process.read_u32(pair + 0x8)
                    if process.read_u32(score_base + _GAMEPLAY_SCORE_PROCESSOR):
                        raw |= MOD_SCOREV2
            except MemoryError_:
                return self.decode_mods(0)
        else:
            raw = process.read_ptr_ptr(self.pattern("menu_mods"))
        return self.decode_mods(raw)

    def _read_gameplay(
        self, gameplay_base: int, ruleset: int, mods: dict[str, Any]
    ) -> dict[str, Any] | None:
        process = self.process
        score_base = process.read_u32(gameplay_base + _GAMEPLAY_SCORE)
        if not score_base:
            return None
        hp_base = process.read_u32(gameplay_base + _GAMEPLAY_HP_BAR)
        hits = _read_hits(process, score_base)
        mode = process.read_i32(score_base + _SCORE_MODE)

        score = process.read_i32(score_base + _SCORE_TOTAL)
        # v2 scores live on the ruleset when a score processor is attached.
        if process.read_u32(score_base + _GAMEPLAY_SCORE_PROCESSOR):
            score = process.read_i32(ruleset + _RULESET_SCORE_NEW)

        accuracy = 0.0
        acc_base = process.read_u32(gameplay_base + _GAMEPLAY_ACCURACY)
        if acc_base:
            accuracy = process.read_f64(acc_base + 0xC)
        if not accuracy:
            accuracy = _accuracy(mode, hits)

        player_hp = process.read_f64(hp_base + 0x1C) if hp_base else 0.0
        return {
            "player_name": process.read_csharp_string(
                process.read_u32(score_base + _SCORE_PLAYER_NAME)
            ),
            "mode": mode,
            "score": score,
            "combo": process.read_i16(score_base + _SCORE_COMBO),
            "max_combo": process.read_i16(score_base + _SCORE_MAX_COMBO),
            "accuracy": round(accuracy, 3),
            "grade": grade(mode, hits, accuracy, int(mods.get("bits", 0))),
            "hits": hits.__dict__,
            "hp": round(player_hp, 3),
            "failed": player_hp <= 0,
            "mods": mods,
        }

    def _read_key_overlay(self, ruleset: int, mode: int, state_id: int) -> list[dict[str, Any]]:
        """Per-key press state and press count, while a play is running.

        Only meaningful during gameplay: outside it the ruleset still holds a
        pointer, but the key objects behind it are uninitialised, which reads
        back as absurd press counts. Requiring the state avoids showing them.
        """
        if state_id != _STATE_PLAY:
            return []
        process = self.process
        pointer = self._key_overlay_base(ruleset)
        if not pointer:
            return []
        try:
            array = process.read_u32(process.read_u32(pointer + 0x10) + 0x4)
            if not array or process.read_i32(array + 0x4) < 4:
                return []
            names = ("K1", "K2", "M1", "M2") if mode == 0 else ("L", "R", "D", "M2")
            keys = []
            for index, name in enumerate(names):
                slot = process.read_u32(array + 0x8 + 0x4 * index)
                if not slot:
                    return []
                count = process.read_i32(slot + 0x14)
                if not 0 <= count <= _MAX_KEY_COUNT:
                    # Not a real key object; drop the whole overlay rather than
                    # report one absurd number.
                    return []
                keys.append(
                    {
                        "name": name,
                        "pressed": bool(process.read_u8(slot + 0x1C)),
                        "count": count,
                    }
                )
            return keys
        except MemoryError_:
            return []

    def _read_hit_errors(self, ruleset: int, limit: int = 256) -> list[int]:
        """Judgement offsets for the current play, oldest kept first.

        Guarded like the gameplay block itself: the score's hit-error list goes
        away with the play, and a partially freed object can still present a
        readable-looking pointer.
        """
        try:
            return self._read_hit_errors_unguarded(ruleset, limit)
        except MemoryError_:
            return []

    def _read_hit_errors_unguarded(self, ruleset: int, limit: int) -> list[int]:
        process = self.process
        gameplay_base = self._gameplay_base(ruleset)
        if not gameplay_base:
            return []
        score_base = process.read_u32(gameplay_base + _GAMEPLAY_SCORE)
        if not score_base:
            return []
        base = process.read_u32(score_base + _SCORE_HIT_ERRORS)
        if not base:
            return []
        items = process.read_u32(base + 0x4)
        size = process.read_i32(base + 0xC)
        if not items or size <= 0:
            return []
        # The array starts after the .NET header (leader start == 0x8).
        result = []
        for index in range(max(0, size - limit), size):
            value = process.read_i32(items + 0x8 + 0x4 * index)
            if value < -10000 or value > 10000:
                break
            result.append(value)
        return result

    def _read_leaders(self, ruleset: int, limit: int = 50) -> list[dict[str, Any]]:
        """Leaderboard rows for the current map, best-first."""
        process = self.process
        leaders = self._leaderboard_base(ruleset)
        if not leaders:
            return []
        try:
            board = process.read_u32(leaders + 0x24)
            if not board:
                return []
            players = process.read_u32(board + 0x4)
            if not players:
                return []
            rows = process.read_u32(players + 0x4)
            size = process.read_i32(players + 0xC)
            if not rows or size <= 0:
                return []
            result = []
            for index in range(min(size, limit)):
                # The array payload starts after the .NET header (leader start).
                slot = process.read_u32(rows + 0x8 + 0x4 * index)
                if not slot:
                    break
                entry = _read_leader(process, slot)
                if entry is None:
                    break
                result.append(entry)
            return result
        except MemoryError_:
            return []

    def _leaderboard_base(self, ruleset: int) -> int:
        """The leaderboard object; its offset also moved between builds."""
        for offset in (_RULESET_LEADERBOARD_OLD, _RULESET_LEADERBOARD_NEW):
            candidate = self._optional(
                lambda offset=offset: self.process.read_u32(ruleset + offset), 0
            )
            if candidate and self._looks_like_leaderboard(candidate):
                return candidate
        return 0

    def _looks_like_leaderboard(self, base: int) -> bool:
        """A leaderboard resolves to a populated row list."""
        def probe() -> bool:
            board = self.process.read_u32(base + 0x24)
            players = self.process.read_u32(board + 0x4) if board else 0
            if not players:
                return False
            return self.process.read_i32(players + 0xC) > 0

        return bool(self._optional(probe, False))


    # -- mods ----------------------------------------------------------------

    @staticmethod
    def decode_mods(raw: int) -> dict[str, Any]:
        """osu! stable stores mods as ``acronym ^ legacy`` in the score."""
        known = {
            "NF": 1 << 0,
            "EZ": 1 << 1,
            "TD": 1 << 2,
            "HD": 1 << 3,
            "HR": 1 << 4,
            "SD": 1 << 5,
            "DT": 1 << 6,
            "RX": 1 << 7,
            "HT": 1 << 8,
            "NC": 1 << 9,
            "FL": 1 << 10,
            "AT": 1 << 11,
            "SO": 1 << 12,
            "AP": 1 << 13,
            "PF": 1 << 14,
            "4K": 1 << 15,
            "5K": 1 << 16,
            "6K": 1 << 17,
            "7K": 1 << 18,
            "8K": 1 << 19,
            "FI": 1 << 20,
            "RD": 1 << 21,
            "CN": 1 << 22,
            "TP": 1 << 23,
            "9K": 1 << 24,
            "CO": 1 << 25,
            "1K": 1 << 26,
            "3K": 1 << 27,
            "2K": 1 << 28,
            "V2": 1 << 29,
            "MR": 1 << 30,
        }
        if raw & MOD_NIGHTCORE:
            raw |= MOD_DOUBLETIME
        acronyms = [name for name, bit in known.items() if raw & bit]
        return {"array": acronyms, "bits": raw, "rate": _clock_rate(raw)}


def _read_leader(process: Process, slot: int) -> dict[str, Any] | None:
    """One leaderboard row. ``slot`` wraps the score; +0x20 points at it."""
    score_entry = process.read_u32(slot + 0x20)
    if not score_entry:
        return None
    name = process.read_csharp_string(process.read_u32(slot + 0x8))
    mods_pair = process.read_u32(score_entry + 0x1C)
    mods = 0
    if mods_pair:
        mods = process.read_u32(mods_pair + 0x8) ^ process.read_u32(mods_pair + 0xC)
    return {
        "name": name,
        "score": process.read_i32(slot + 0x30),
        "combo": process.read_i16(score_entry + _SCORE_COMBO),
        "max_combo": process.read_i16(score_entry + _SCORE_MAX_COMBO),
        "position": process.read_i32(slot + 0x2C),
        "is_passing": bool(process.read_u8(slot + 0x4B)),
        "mods": OsuReader.decode_mods(mods)["array"] if mods else [],
    }


def _clock_rate(mods: int) -> float:
    if mods & (MOD_DOUBLETIME | MOD_NIGHTCORE):
        return 1.5
    if mods & MOD_HALFTIME:
        return 0.75
    return 1.0


def grade(
    mode: int,
    hits: HitCounts,
    accuracy: float,
    mods: int = 0,
) -> str:
    """The letter grade osu! awards for a score.

    Mirrors the rules osu! stable itself applies (the same thresholds tosu uses
    in ``utils/calculators.ts``). The modes differ in which counts they key off:

    * osu! and taiko grade on the 300 ratio, with a clean ``miss`` relaxing the
      requirement by one band -- a run over 90% 300s is an A even with misses.
    * catch and mania grade on accuracy alone.

    Hidden and Flashlight upgrade any S to its silver variant (``SH``/``XH``).
    """
    # A widescreen "katu"/"geki" are mode-specific and not part of any rule.
    total = hits.total
    if total == 0:
        return ""

    silver = bool(mods & (MOD_HIDDEN | MOD_FLASHLIGHT))
    ss = "XH" if silver else "X"
    s_rank = "SH" if silver else "S"

    if mode in (0, 1):  # osu!, taiko
        ratio300 = hits.hit300 / total
        ratio50 = hits.hit50 / total
        if ratio300 == 1.0:
            return ss
        if ratio300 > 0.9 and ratio50 < 0.01 and hits.miss == 0:
            return s_rank
        if (ratio300 > 0.8 and hits.miss == 0) or ratio300 > 0.9:
            return "A"
        if (ratio300 > 0.7 and hits.miss == 0) or ratio300 > 0.8:
            return "B"
        if ratio300 > 0.6:
            return "C"
        return "D"

    if mode == 2:  # catch
        if accuracy >= 100.0:
            return ss
        if accuracy > 98.0:
            return s_rank
        if accuracy > 94.0:
            return "A"
        if accuracy > 90.0:
            return "B"
        if accuracy > 85.0:
            return "C"
        return "D"

    # mode == 3: mania
    if accuracy >= 100.0:
        return ss
    if accuracy > 95.0:
        return s_rank
    if accuracy > 90.0:
        return "A"
    if accuracy > 80.0:
        return "B"
    if accuracy > 70.0:
        return "C"
    return "D"


def _accuracy(mode: int, hits: HitCounts) -> float:
    if mode == 3:  # mania
        total = (
            6 * hits.hit300
            + 6 * hits.geki
            + 4 * hits.katu
            + 2 * hits.hit100
            + hits.hit50
        )
        maximum = 6 * (hits.total + hits.geki + hits.katu)
        return (total / maximum * 100.0) if maximum else 0.0
    if mode == 1:  # taiko
        total = 2 * hits.hit300 + hits.hit100
        maximum = 2 * (hits.hit300 + hits.hit100 + hits.miss)
        return (total / maximum * 100.0) if maximum else 0.0
    if mode == 2:  # catch
        total = hits.hit300 + hits.hit100 + hits.hit50
        maximum = hits.total
        return (total / maximum * 100.0) if maximum else 0.0
    total = 6 * hits.hit300 + 2 * hits.hit100 + hits.hit50
    maximum = 6 * hits.total
    return (total / maximum * 100.0) if maximum else 0.0


def _read_beatmap(process: Process, pattern_base: int) -> BeatmapInfo:
    """Beatmap metadata, or an empty record when the map is being swapped.

    ``read_ptr_ptr`` can resolve to a freed -- and therefore zeroed -- object
    while osu! tears the play down, and every field below is an offset off that
    pointer. Left unguarded the first one, ``+0x68``, becomes a read of the
    literal address 0x68, which is the ``ReadProcessMemory(0x68, 4) failed:
    299`` seen when a map ends. A null base means "no map right now", not a
    broken process, so it returns empty rather than failing the whole read.
    """
    beatmap = process.read_ptr_ptr(pattern_base - 0xC)
    if not beatmap:
        return BeatmapInfo()
    read_string = process.read_csharp_string
    return BeatmapInfo(
        checksum=read_string(process.read_u32(beatmap + 0x6C)),
        filename=read_string(process.read_u32(beatmap + 0x90)),
        folder=read_string(process.read_u32(beatmap + 0x78)),
        background=read_string(process.read_u32(beatmap + 0x68)),
        artist=read_string(process.read_u32(beatmap + 0x18)),
        title=read_string(process.read_u32(beatmap + 0x24)),
        difficulty=read_string(process.read_u32(beatmap + 0xAC)),
        creator=read_string(process.read_u32(beatmap + 0x7C)),
        ar=process.read_f32(beatmap + 0x2C),
        cs=process.read_f32(beatmap + 0x30),
        hp=process.read_f32(beatmap + 0x34),
        od=process.read_f32(beatmap + 0x38),
        map_id=process.read_i32(beatmap + 0xC8),
        set_id=process.read_i32(beatmap + 0xCC),
        object_count=process.read_i32(beatmap + 0xF8),
        ranked_status=process.read_i32(beatmap + 0x12C),
    )


def _read_hits(process: Process, score_base: int) -> HitCounts:
    return HitCounts(
        hit300=process.read_i16(score_base + _SCORE_HIT300),
        hit100=process.read_i16(score_base + _SCORE_HIT100),
        hit50=process.read_i16(score_base + _SCORE_HIT50),
        geki=process.read_i16(score_base + _SCORE_HIT_GEKI),
        katu=process.read_i16(score_base + _SCORE_HIT_KATU),
        miss=process.read_i16(score_base + _SCORE_HIT_MISS),
    )
