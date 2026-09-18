"""Signature patterns for osu! stable (b20240820.1 / Stable40).

Patterns are taken from tosu (https://github.com/tosuapp/tosu,
``packages/tosu/src/memory/stable.ts``) and re-verified against this build.
``offset`` is applied to the address the scanner reports, exactly as tosu does.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Signature:
    pattern: str
    offset: int = 0
    #: Extra dereference: tosu reads some of these as ``[[addr]]``.
    pointer: bool = False


#: Order matters only for readability; scanning is a single pass over regions.
SIGNATURES: dict[str, Signature] = {
    # [Base - 0xc] -> Beatmap, [Base - 0x33] -> GameBase
    "base": Signature("F8 01 74 04 83 65"),
    # [addr + 0x5] -> current play time
    "play_time": Signature("5E 5F 5D C3 A1 ?? ?? ?? ?? 89 ?? 04"),
    # [addr - 0xb] -> GameBase, then +0x4 -> Ruleset
    "rulesets": Signature("7D 15 A1 ?? ?? ?? ?? 85 C0"),
    # [[addr - 0x4]] -> GameState
    "status": Signature("48 83 F8 04 73 1E", offset=-0x4),
    "menu_mods": Signature(
        "C8 FF ?? ?? ?? ?? ?? 81 0D ?? ?? ?? ?? ?? 08 00 00", offset=0x9
    ),
    # [[addr + 0x46]] non-zero while a replay is being watched.
    "can_run_slowly": Signature("55 8B EC 80 3D ?? ?? ?? ?? 00 75 26 80 3D"),
    "audio_length": Signature("55 8B EC 83 EC 08 A1 ?? ?? ?? ?? 85 C0", offset=0x7),
    # [[addr + 0x7]] -> UserProfile
    "user_profile": Signature(
        "FF 15 ?? ?? ?? ?? A1 ?? ?? ?? ?? 8B 48 54 33 D2", offset=0x7
    ),
    "raw_login_status": Signature("B8 0B 00 00 8B 35", offset=-0xB),
}

#: osu! stable game states, indexed by the value read from ``status``.
GAME_STATES: tuple[str, ...] = (
    "menu",
    "edit",
    "play",
    "exit",
    "selectEdit",
    "selectPlay",
    "selectDrawings",
    "resultScreen",
    "update",
    "busy",
    "unknown",
    "lobby",
    "matchSetup",
    "selectMulti",
    "rankingVs",
    "onlineSelection",
    "optionsOffsetWizard",
    "rankingTagCoop",
    "rankingTeam",
    "beatmapImport",
    "packageUpdater",
    "benchmark",
    "tourney",
    "charts",
    "spectating",
    "watchingReplay",
)

#: ``UserProfile.rawBanchoStatus`` values (tosu's ``BanchoStatus`` enum).
BANCHO_STATUS: tuple[str, ...] = (
    "idle",
    "afk",
    "playing",
    "editing",
    "modding",
    "multiplayer",
    "watching",
    "unknown",
    "testing",
    "submitting",
    "paused",
    "lobby",
    "multiplaying",
    "osuDirect",
)

#: ``UserProfile.rawLoginStatus`` values (tosu's ``UserLoginStatus`` enum).
LOGIN_STATUS: tuple[str, ...] = (
    "notLoggedIn",
    "loggedIn",
    "notConnected",
    "connecting",
    "disconnecting",
)


def game_state(value: int) -> str:
    if 0 <= value < len(GAME_STATES):
        return GAME_STATES[value]
    return f"unknown({value})"


def bancho_status(value: int) -> str:
    if 0 <= value < len(BANCHO_STATUS):
        return BANCHO_STATUS[value]
    return f"unknown({value})"


def login_status(value: int) -> str:
    if 0 <= value < len(LOGIN_STATUS):
        return LOGIN_STATUS[value]
    return f"unknown({value})"
