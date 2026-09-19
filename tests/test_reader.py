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


class RecycledBeatmap:
    """A freed beatmap page whose fields are readable but meaningless.

    This is the shape that reached the log as
    ``degraded beatmap: implausible C# string length 33353``: the object was
    gone, the slot held something else, and the string pointer read out of it
    led to data that was not a .NET string.
    """

    def __init__(self, bad_length=33353, digest="not-a-real-checksum-at-all-xx"):
        self.bad_length = bad_length
        self.digest = digest
        # Only the checksum's string object reports a sane length, so a valid
        # digest can be read while the other fields still look like garbage.
        self.checksum_pointer = 0x3000

    def read_u32(self, address):
        if address == 0x1000 - 0xC:
            return 0x2000           # the object pointer resolves ...
        if address == 0x2000 + 0x6C:
            return self.checksum_pointer
        if address in (0x2000 + 0x90, 0x2000 + 0x78, 0x2000 + 0x68,
                       0x2000 + 0x18, 0x2000 + 0x24, 0x2000 + 0xAC, 0x2000 + 0x7C):
            return 0x4000           # ... other string pointers are readable ...
        raise MemoryError_(f"ReadProcessMemory({address:#x}, 4) failed: 299")

    def read_i32(self, address):
        if address == self.checksum_pointer + 0x4:
            return len(self.digest) if self.digest else self.bad_length
        if address == 0x4000 + 0x4:
            return self.bad_length   # ... and their "length" is nonsense
        raise MemoryError_(f"ReadProcessMemory({address:#x}, 4) failed: 299")

    def read_ptr(self, address):
        return self.read_u32(address)

    def read_ptr_ptr(self, address):
        return self.read_u32(self.read_u32(address))

    def read_f32(self, address):
        raise MemoryError_(f"ReadProcessMemory({address:#x}, 4) failed: 299")

    def read_csharp_string(self, address):
        length = self.read_i32(address + 0x4)
        if length < 0 or length > 512:
            raise MemoryError_(f"implausible C# string length {length}")
        return self.digest if address == self.checksum_pointer else ""


def test_recycled_beatmap_yields_no_partial_record():
    """A half-readable beatmap must not be published with real-looking fields."""
    r = bare_reader()
    r._process = RecycledBeatmap()
    r.pattern = lambda name: 0x1000  # type: ignore[assignment]
    info = r._cached_beatmap(0x1000)
    check("no partial record", (info.title, info.artist, info.object_count), ("", "", 0))


class ChecksumOnlyBeatmap:
    """Only the checksum resolves, so the MD5 guard is what decides.

    Every other field fails, which is the point: the all-or-nothing read would
    otherwise reject the record before the digest is ever examined.
    """

    def __init__(self, digest):
        self.digest = digest

    def _resolve(self, address):
        if address == 0x1000 - 0xC:
            return 0x2000
        if address == 0x2000 + 0x6C:
            return 0x3000
        raise MemoryError_(f"ReadProcessMemory({address:#x}, 4) failed: 299")

    def read_u32(self, address):
        return self._resolve(address)

    def read_ptr(self, address):
        return self._resolve(address)

    def read_ptr_ptr(self, address):
        return self._resolve(self._resolve(address))

    def read_i32(self, address):
        if address == 0x3000 + 0x4:
            return len(self.digest)
        raise MemoryError_(f"ReadProcessMemory({address:#x}, 4) failed: 299")

    def read_f32(self, address):
        raise MemoryError_(f"ReadProcessMemory({address:#x}, 4) failed: 299")

    def read_csharp_string(self, address):
        if address == 0x3000:
            return self.digest
        raise MemoryError_(f"ReadProcessMemory({address:#x}, 4) failed: 299")


def test_checksum_guard():
    """Fields can be readable and still meaningless; the digest catches that.

    Tested on the guard directly, because the all-or-nothing read in
    ``_read_beatmap`` rejects a record with any failing field *before* the
    digest is examined -- so a fake with only a checksum never gets that far.
    """
    check("real md5 accepted", R._is_md5("4ce502e521a7342276ad821bf967d6bb"), True)
    check("uppercase accepted", R._is_md5("4CE502E521A7342276AD821BF967D6BB"), True)
    check("wrong length rejected", R._is_md5("4ce502e521a7342276ad821bf967d6b"), False)
    check("non-hex rejected", R._is_md5("zzzz-not-a-digest-zzzz-not-a-dig"), False)
    check("empty rejected", R._is_md5(""), False)
    check("long garbage rejected", R._is_md5("x" * 512), False)


def test_partial_record_is_rejected_before_the_digest():
    """The digest is a backstop; a failing field already ends the read."""
    r = bare_reader()
    r._process = ChecksumOnlyBeatmap("4ce502e521a7342276ad821bf967d6bb")
    r.pattern = lambda name: 0x1000  # type: ignore[assignment]
    # The checksum itself would pass, but the record is incomplete.
    check("incomplete record dropped", r._cached_beatmap(0x1000).checksum, "")


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
        test_recycled_beatmap_yields_no_partial_record,
        test_checksum_guard,
        test_partial_record_is_rejected_before_the_digest,
        test_read_once_then_cleared,
    ]:
        print(f"\n{fn.__name__}")
        fn()
    print(f"\nfailures: {failures}")
    sys.exit(1 if failures else 0)

