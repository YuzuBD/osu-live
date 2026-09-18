"""Reader unit tests for the freed-object cases found in .osu_live.log.

Run directly:  python tests/test_reader.py
No test framework needed -- this project has no dev dependencies.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from osu_live import reader as R  # noqa: E402
from osu_live.winmem import MemoryError_  # noqa: E402

RULESET = 0x5000
GAMEPLAY = 0x9000
SCORE = 0xA000
NAME = 0xB000

#: What the gameplay slot holds once osu! has freed the object and the
#: allocator recycled the page. This exact value produced the reported
#: "ReadProcessMemory(0x10039, 4) failed: 299".
RECYCLED = 0x10000

failures = 0


def check(label, got, want):
    global failures
    if got == want:
        print(f"  pass  {label} :: {got!r}")
    else:
        failures += 1
        print(f"  FAIL  {label} :: got {got!r}, want {want!r}")


class FakeProcess:
    """Serves only the addresses it is told about, like a freed page."""

    def __init__(self, memory, player_name="Yuzu"):
        self.memory = memory
        self.player_name = player_name

    def read_u32(self, address):
        if address in self.memory:
            return self.memory[address]
        raise MemoryError_(f"ReadProcessMemory({address:#x}, 4) failed: 299")

    def read_i32(self, address):
        raise MemoryError_(f"ReadProcessMemory({address:#x}, 4) failed: 299")

    def read_i16(self, address):
        raise MemoryError_(f"ReadProcessMemory({address:#x}, 2) failed: 299")

    def read_f64(self, address):
        raise MemoryError_(f"ReadProcessMemory({address:#x}, 8) failed: 299")

    def read_csharp_string(self, address):
        if address == NAME:
            return self.player_name
        raise MemoryError_(f"ReadProcessMemory({address:#x}, 4) failed: 299")

    def read_ptr(self, address):
        return self.read_u32(address)

    def read_ptr_ptr(self, address):
        return self.read_u32(self.read_u32(address))


def bare_reader():
    """An OsuReader without attach(), so no osu! process is required."""
    r = R.OsuReader.__new__(R.OsuReader)
    r._process = None
    r._patterns = {}
    r._gameplay_old = None
    r._beatmap_cache = None
    r._noted = {}
    r.pid = 0
    return r


def live_memory():
    return {
        RULESET + R._RULESET_GAMEPLAY_NEW: GAMEPLAY,
        GAMEPLAY + R._GAMEPLAY_SCORE: SCORE,
        SCORE + R._SCORE_PLAYER_NAME: NAME,
    }


def test_gameplay_base_revalidates_remembered_layout():
    """A remembered offset must not be trusted to still hold a live object."""
    r = bare_reader()

    r._process = FakeProcess(live_memory())
    check("live play resolves", r._gameplay_base(RULESET), GAMEPLAY)
    check("layout remembered", r._gameplay_old, False)

    # The map ends: the slot now reads the recycled value.
    r._process = FakeProcess({RULESET + R._RULESET_GAMEPLAY_NEW: RECYCLED})
    check("freed object rejected", r._gameplay_base(RULESET), 0)


def test_gameplay_base_without_cache_also_rejects():
    r = bare_reader()
    r._process = FakeProcess({RULESET + R._RULESET_GAMEPLAY_NEW: RECYCLED,
                              RULESET + R._RULESET_GAMEPLAY_OLD: 0})
    check("unprobed search rejects", r._gameplay_base(RULESET), 0)
    check("layout stays unset", r._gameplay_old, None)


def test_gameplay_read_never_raises():
    """The score read off a freed gameplay object returns None, not an error."""
    r = bare_reader()
    r._process = FakeProcess({RULESET + R._RULESET_GAMEPLAY_NEW: RECYCLED})
    bundle = r._optional(
        lambda: r._read_gameplay(RECYCLED, RULESET, {"bits": 0}), None
    )
    check("_read_gameplay degrades to None", bundle, None)


def test_hit_errors_never_raise():
    r = bare_reader()
    r._process = FakeProcess({RULESET + R._RULESET_GAMEPLAY_NEW: RECYCLED})
    check("_read_hit_errors degrades to []", r._read_hit_errors(RULESET), [])


def test_mods_never_raise():
    r = bare_reader()
    r._process = FakeProcess({RULESET + R._RULESET_GAMEPLAY_NEW: RECYCLED})
    mods = r._read_mods(RULESET, 2)
    check("_read_mods degrades to NoMod", mods["array"], [])


def test_beatmap_null_pointer_returns_empty():
    """A zeroed beatmap pointer must not become a read of address 0x68.

    ``_read_beatmap`` reads offsets off the beatmap pointer, so a null base
    turned the first field into a literal read of ``0x0 + 0x68`` -- the error
    originally reported on a finished map.
    """
    r = bare_reader()
    # base - 0xC holds a pointer into a zeroed -- i.e. freed -- object.
    r._process = FakeProcess({0x1000 - 0xC: 0x2000, 0x2000: 0})
    info = R._read_beatmap(r._process, 0x1000)
    check("empty beatmap record", (info.filename, info.title, info.background), ("", "", ""))


def test_beatmap_recycled_pointer_is_not_a_crash():
    """A recycled (non-null, unreadable) base degrades instead of raising."""
    r = bare_reader()
    # The pointer resolves, but every field offset off it is unreadable.
    r._process = FakeProcess({0x1000 - 0xC: 0x2000, 0x2000: 0x2000})
    r.pattern = lambda name: 0x1000  # type: ignore[assignment]
    info = r._cached_beatmap(0x1000)
    check("degraded, no exception", (info.filename, info.title), ("", ""))


def test_read_once_then_cleared():
    """A degraded field is reported once per run of failures, not per frame."""
    reported = []

    def sink(message):
        reported.append(message)

    R.set_failure_sink(sink)
    try:
        r = bare_reader()
        r._process = FakeProcess({})
        r._noted = {}
        # The profile pointer resolves to nothing, which is the "not logged in"
        # state the overlay used to flash on a screen change.
        r.user_profile = lambda: 0  # type: ignore[assignment]
        for _ in range(5):
            check_player = r._read_player()
        check("empty player block", check_player, {})
        check("reported once for five frames", len(reported), 1)
        check("message names the field", reported[0].split(":")[0], "player")

        # A clean read clears the flag, so a later failure is reported again.
        r._cleared("player")
        r._read_player()
        check("reported again after recovery", len(reported), 2)
    finally:
        R.set_failure_sink(None)


if __name__ == "__main__":
    for fn in [
        test_gameplay_base_revalidates_remembered_layout,
        test_gameplay_base_without_cache_also_rejects,
        test_gameplay_read_never_raises,
        test_hit_errors_never_raise,
        test_mods_never_raise,
        test_beatmap_null_pointer_returns_empty,
        test_beatmap_recycled_pointer_is_not_a_crash,
        test_read_once_then_cleared,
    ]:
        print(f"\n{fn.__name__}")
        fn()
    print(f"\nfailures: {failures}")
    sys.exit(1 if failures else 0)
