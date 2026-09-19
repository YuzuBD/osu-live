"""Does a packaged build keep its files where it should?

Runs a child interpreter that pretends to be frozen (``sys.frozen``,
``sys._MEIPASS``, ``sys.executable``) with LOCALAPPDATA pointed at a throwaway
directory, then drives the reader's capture path with an in-play snapshot.

This is the pair of mistakes worth guarding, both already made once: writing
beside the executable, and losing the write entirely because the bundle is a
temporary directory. The real cache is never touched -- everything happens
under a temporary LOCALAPPDATA.
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

fails = 0
def fail(m):
    global fails
    fails += 1
    print("  FAIL  " + m)
def pass_(m):
    print("  pass  " + m)
def chk(label, ok, got=""):
    (pass_ if ok else fail)(label + (" :: " + str(got) if got != "" else ""))

tmp = tempfile.mkdtemp(prefix="osu-live-frozen-")
child = textwrap.dedent(
    """
    import os, sys, json
    sys.frozen = True
    sys._MEIPASS = os.path.join(os.environ["FAKE_ROOT"], "bundle")
    sys.executable = os.path.join(os.environ["FAKE_ROOT"], "dist", "osu-live.exe")
    os.makedirs(sys._MEIPASS, exist_ok=True)
    os.makedirs(os.path.dirname(sys.executable), exist_ok=True)

    from osu_live import paths
    from osu_live.reader import OsuReader, Snapshot, BeatmapInfo, _STATE_PLAY

    report = {
        "frozen": paths.FROZEN,
        "data_root": str(paths.DATA_ROOT),
        "data_created": paths.DATA_ROOT.is_dir(),
        "web_root_exists": paths.WEB_ROOT.is_dir(),
    }

    reader = OsuReader(pid=None)
    report["loaded"] = reader._last_score

    snap = Snapshot()
    snap.state_id = _STATE_PLAY
    snap.beatmap = BeatmapInfo(artist="A", title="T", difficulty="D",
                               checksum="c" * 32, object_count=100)
    snap.mods = {"array": ["HD"], "bits": 0, "rate": 1.0}
    snap.gameplay = {"player_name": "Tester", "mode": 0, "score": 1234567,
                     "combo": 55, "max_combo": 99, "accuracy": 97.5, "grade": "A",
                     "hits": {"hit300": 90, "hit100": 9, "hit50": 0, "miss": 1}}
    out = reader._track_last_score(snap)
    report["captured"] = out["score"] if out else None

    written = paths.DATA_ROOT / ".last_score.json"
    report["written"] = written.is_file()
    if written.is_file():
        disk = json.loads(written.read_text(encoding="utf-8"))
        report["disk_score"] = disk["score"]
        report["disk_checksum_ok"] = disk["beatmap"]["checksum"] == "c" * 32
    print("REPORT " + json.dumps(report))
    """
)

env = dict(os.environ)
env["FAKE_ROOT"] = tmp
env["LOCALAPPDATA"] = os.path.join(tmp, "localappdata")
env["PYTHONPATH"] = ROOT

print("== a frozen build's file layout ==")
result = subprocess.run(
    [sys.executable, "-c", child], capture_output=True, text=True, env=env, cwd=ROOT
)
line = next((l for l in result.stdout.splitlines() if l.startswith("REPORT ")), None)
if line is None:
    fail("the child could not run")
    print(result.stdout)
    print(result.stderr)
    print("\nfailures:", fails)
    raise SystemExit(1)

r = json.loads(line[len("REPORT "):])
expected = os.path.join(tmp, "localappdata", "osu-live")

chk("runs as a frozen build", r["frozen"] is True)
chk("data goes under LOCALAPPDATA", os.path.normcase(r["data_root"]) == os.path.normcase(expected), r["data_root"])
chk("the directory is created for the writers", r["data_created"] is True)
chk("no cache was inherited", r["loaded"] is None, r["loaded"])

print("\n== capturing a play ==")
chk("an in-play snapshot is captured", r.get("captured") == 1234567, r.get("captured"))
chk("it is written to disk", r.get("written") is True)
chk("with the right score", r.get("disk_score") == 1234567, r.get("disk_score"))
chk("and the right beatmap", r.get("disk_checksum_ok") is True)

print("\n== nothing lands beside the executable ==")
beside = os.path.join(tmp, "dist")
leftovers = sorted(os.listdir(beside)) if os.path.isdir(beside) else []
chk("the exe's folder is untouched", leftovers == [], ", ".join(leftovers) or "empty")

print("\nfailures:", fails)
sys.exit(1 if fails else 0)
