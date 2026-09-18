"""Live pp calculation.

Uses ``akatsuki-pp-py`` -- the exact library bancho.py itself runs
(``app/services/performance.py``) -- so the numbers the overlay shows line up
with what the server would award.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field

from akatsuki_pp_py import Beatmap, Calculator

from .reader import HitCounts

#: Accuracy values precomputed for the "pp at X% acc" table.
DEFAULT_ACCURACIES = (100.0, 99.0, 98.0, 97.0, 95.0, 90.0)


@dataclass
class PpBreakdown:
    pp: float = 0.0
    stars: float = 0.0
    pp_aim: float = 0.0
    pp_speed: float = 0.0
    pp_acc: float = 0.0
    pp_flashlight: float = 0.0
    pp_difficulty: float = 0.0
    max_combo: int = 0
    objects: int = 0


@dataclass
class PpResult:
    current: PpBreakdown = field(default_factory=PpBreakdown)
    fc: PpBreakdown = field(default_factory=PpBreakdown)
    max: PpBreakdown = field(default_factory=PpBreakdown)
    acc_list: dict[str, float] = field(default_factory=dict)
    error: str | None = None


class PpEngine:
    """Caches parsed beatmaps and recalculates pp as the play progresses."""

    def __init__(self, songs_folder: str = ""):
        self.songs_folder = songs_folder
        self._beatmap: Beatmap | None = None
        self._beatmap_key: tuple[str, int] | None = None
        self._lock = threading.Lock()

    def set_songs_folder(self, folder: str) -> None:
        self.songs_folder = folder

    def _map_path(self, folder: str, filename: str) -> str:
        return os.path.join(self.songs_folder, folder, filename)

    def _load(self, folder: str, filename: str, mode: int) -> Beatmap | None:
        key = (folder, filename)  # type: ignore[assignment]
        path = self._map_path(folder, filename)
        if self._beatmap is not None and self._beatmap_key == key:
            return self._beatmap
        if not os.path.isfile(path):
            return None
        try:
            beatmap = Beatmap(path=path)
        except Exception:
            return None
        self._beatmap = beatmap
        self._beatmap_key = key
        return beatmap

    @staticmethod
    def _breakdown(result: object) -> PpBreakdown:
        difficulty = result.difficulty
        return PpBreakdown(
            pp=round(result.pp, 3),
            stars=round(difficulty.stars, 3),
            pp_aim=round(result.pp_aim, 3),
            pp_speed=round(result.pp_speed, 3),
            pp_acc=round(result.pp_acc, 3),
            pp_flashlight=round(result.pp_flashlight, 3),
            pp_difficulty=round(getattr(result, "pp_difficulty", 0.0) or 0.0, 3),
            max_combo=difficulty.max_combo,
            objects=(
                difficulty.n_circles
                + difficulty.n_sliders
                + difficulty.n_spinners
            ),
        )

    def compute(
        self,
        folder: str,
        filename: str,
        mode: int,
        mods: int,
        hits: HitCounts | None = None,
        combo: int | None = None,
        object_count: int = 0,
    ) -> PpResult:
        """Current / FC / max pp plus a table of pp at fixed accuracies."""
        result = PpResult()
        if not folder or not filename:
            result.error = "no beatmap selected"
            return result
        with self._lock:
            beatmap = self._load(folder, filename, mode)
            if beatmap is None:
                result.error = f"beatmap file not found: {filename}"
                return result
            try:
                result.current = self._breakdown(
                    self._performance(beatmap, mode, mods, hits, combo)
                )
                result.fc = self._breakdown(
                    self._performance(beatmap, mode, mods, _fc_hits(hits), combo)
                )
                result.max = self._breakdown(
                    self._performance(beatmap, mode, mods, None, None)
                )
                if object_count > 0:
                    for acc in DEFAULT_ACCURACIES:
                        calculated = self._performance(
                            beatmap, mode, mods, None, None, acc=acc
                        )
                        result.acc_list[f"{acc:g}"] = round(calculated.pp, 3)
            except Exception as exc:  # malformed map, unsupported mode, ...
                result.error = f"{type(exc).__name__}: {exc}"
        return result

    @staticmethod
    def _performance(
        beatmap: Beatmap,
        mode: int,
        mods: int,
        hits: HitCounts | None,
        combo: int | None,
        acc: float | None = None,
    ):
        """One calculation. ``acc`` and ``hits`` are mutually exclusive, as in bancho.py."""
        kwargs: dict[str, object] = {"mode": mode, "mods": mods}
        if acc is not None:
            kwargs["acc"] = acc
        elif hits is not None:
            kwargs.update(
                n300=hits.hit300,
                n100=hits.hit100,
                n50=hits.hit50,
                n_geki=hits.geki,
                n_katu=hits.katu,
                n_misses=hits.miss,
            )
        if combo:
            kwargs["combo"] = combo
        calculator = Calculator(**kwargs)
        return calculator.performance(beatmap)


def _fc_hits(hits: HitCounts | None) -> HitCounts | None:
    """The same score with every miss converted to a 300."""
    if hits is None:
        return None
    return HitCounts(
        hit300=hits.hit300 + hits.miss,
        hit100=hits.hit100,
        hit50=hits.hit50,
        geki=hits.geki,
        katu=hits.katu,
        miss=0,
    )
