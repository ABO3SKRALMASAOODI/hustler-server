"""Bad tracks retired, the reference music set first (owner, 15 Nov 2026: "if they are bad they shouldn't be used ...
focus on quality ... like that metromedia account").

  * a track the signal gate failed is never chosen again, by the box's chooser or by the workflow films;
  * the workflow films choose from the reference set ($VALMERA_MUSIC_SET/index.json) first, with its entry point,
    and fall back only to library tracks that passed the full gate;
  * no audio of the set or the library is ever in this public repository.

Signal only: synthetic rows, a stub engine, no network, no storage."""
import json
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
sys.path.insert(0, WORKER)
sys.path.insert(0, HERE)

import music_choose as MC  # noqa: E402
import music_film as MF  # noqa: E402
from score import music as MU  # noqa: E402
from test_music_choose import STORY, _track, _z  # noqa: E402

FILM = {"kind": "story", "duration_s": 60.0, "turns": [{"t": 12.0, "role": "turn"}, {"t": 40.0, "role": "climax"}],
        "pace": {"cuts10": 9.0}, "speech_share": 0.85, "brief": "a founder bets the company"}
PROFILES = {"kinds": {k: {"n": 3, "bpm": {"p25": 95, "p50": 120, "p75": 135}, "minor_share": 0.6,
                          "shape10": [-2, -1, -1, 0, 0, 1, 2, 2, 3, 1], "voice_over_music_db": {"p25": 6, "p50": 9, "p75": 12},
                          "bed_lift_db": {"p50": 3}, "turns": {"climax": {"median_db": 4.0}}, "endings": {"hit_and_tail": 5},
                          "top_tags": {"Funk": 0.2}, "refs": [{"id": f"r{i}", "z": _z(10 + i, STORY)} for i in range(3)]}
                      for k in MC.KINDS}}


def _set_row(cid, dur=150.0, passed=True, fails=(), file=None, kinds=None, drop_at=70.0, brk=(50.0, 70.0)):
    curve = [0.0] * int(dur)
    for i in range(int(brk[0]), int(brk[1])):
        curve[i] = -6.0
    for i in range(int(drop_at), int(dur)):
        curve[i] = 1.5
    row = {"cid": cid, "file": file or f"audio/{cid}.m4a", "title": cid.title(), "author": "Ref", "duration_s": dur,
           "pass": passed, "fails": list(fails), "ebur128": {"I": -11.0},
           "tags": {"tempo_bpm": 120.0, "bar_s": 2.0, "first_downbeat_s": 0.0, "energy_curve_ref_db": -14.0, "energy_curve_db_1s": curve,
                    "sections": [{"t0": 0.0, "t1": brk[0], "label": "body", "level_db_vs_median": 0.0},
                                 {"t0": brk[0], "t1": brk[1], "label": "break", "level_db_vs_median": -6.0},
                                 {"t0": drop_at, "t1": dur, "label": "drop", "level_db_vs_median": 1.5}],
                    "entry_points": [{"t": 0.0, "why": "top of the song", "score": 1.0}, {"t": drop_at, "why": "drop", "score": 0.8},
                                     {"t": 28.0, "why": "re-entry after a break", "score": 0.65}]}}
    if kinds:
        row["kinds"] = kinds
    return row


@pytest.fixture
def lib(tmp_path, monkeypatch):
    # "best-but-bad" is the closest to the references and the gate failed it; "ok-pass" passed; "maybe" is provisional
    tracks = [_track("best-but-bad", _z(2, STORY), bpm=120.0, rise_at=40.0), _track("ok-pass", _z(4, STORY), bpm=118.0),
              _track("maybe", _z(3, STORY), bpm=121.0, rise_at=40.0), _track("far-pass", _z(9), bpm=70.0)]
    m, p, g = tmp_path / "manifest.json", tmp_path / "profiles.json", tmp_path / "gate.json"
    m.write_text(json.dumps({"tracks": tracks}))
    p.write_text(json.dumps(PROFILES))
    g.write_text(json.dumps({"pass": ["ok-pass", "far-pass"], "provisional": ["maybe"], "fail": {"best-but-bad": ["clipped: 57 pinned samples"]}}))
    monkeypatch.setattr(MC, "MANIFEST_PATH", str(m))
    monkeypatch.setattr(MC, "PROFILES_PATH", str(p))
    monkeypatch.setattr(MC, "GATE_PATH", str(g))
    monkeypatch.delenv(MC.SET_ENV, raising=False)
    MC._CACHE.clear()
    yield tmp_path
    MC._CACHE.clear()


def _make_set(root, rows, files=None):
    os.makedirs(root / "audio", exist_ok=True)
    for r in rows:
        f = root / r["file"]
        if files is None or r["cid"] in files:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"\0" * 64)
    (root / "index.json").write_text(json.dumps({"version": 1, "tracks": rows}))
    return root


class _Engine:
    """The music engine's analyse/arrange, stubbed: every song carries the film, and what it was asked is kept."""

    def __init__(self, bad=()):
        self.bad, self.asked, self.arcs = set(bad), [], []

    def analyse(self, path, sung=None, vocals=None):
        return {"path": path}

    def arrange(self, song, marks, profile=None):
        self.asked.append((os.path.basename(song["path"]), marks.get("open_s")))
        self.arcs.append(bool(marks.get("arc")))
        if any(b in song["path"] for b in self.bad):
            return {"ok": False, "why": "no arrangement lands the drop"}
        return {"ok": True, "drop_at": marks.get("payoff_s"), "drop_is": "entrance", "intro_bars": 0, "low_bars": 0, "jumps": 1, "tempo": 1.0}


def _marks():
    return {"dur_s": 60.0, "words": [(1.0, 2.0)], "cuts": [], "turn_s": 12.0, "payoff_s": 40.0, "end_hit_s": None, "quiet": []}


def _lib_fetch(track):
    return f"/lib/{track['cid']}.mp3"


# ----------------------------------------------------------------- a failed track is never chosen

def test_a_failed_track_is_never_chosen_but_a_project_that_plays_it_keeps_it(lib):
    assert "best-but-bad" in {t["cid"] for t in MC.tracks()}                # still in the manifest
    assert MC.find("best-but-bad") is not None                             # a project that already plays it keeps rendering
    box = {o["cid"] for o in MC.shortlist(FILM, n=8)[0]}
    assert "best-but-bad" not in box and {"ok-pass", "maybe"} <= box       # the box's chooser: failures out, provisional stays
    for _ in range(3):
        ch = MC.choose(FILM, call=lambda *a: {"option": 1, "why": "x"})
        assert ch["cid"] != "best-but-bad"
    # the workflow films: only full-gate passes
    pools = MC.workflow_pools(FILM)
    assert [n for n, _ in pools] == ["library"] and {t["cid"] for t in pools[0][1]} == {"ok-pass", "far-pass"}


def test_a_workflow_film_without_the_set_degrades_to_gate_passing_tracks_only(lib, monkeypatch):
    eng = _Engine()
    monkeypatch.setattr(MU, "analyse", eng.analyse)
    monkeypatch.setattr(MU, "arrange", eng.arrange)
    monkeypatch.setenv(MC.SET_ENV, str(lib / "no-such-set"))
    MC._CACHE.clear()
    for call in (None, lambda *a: {"order": [1, 2, 3], "why": "the model's first"}):
        ch, path, tried = MF.choose(FILM, _marks(), "/nonexistent", call=call, fetch=_lib_fetch)
        assert ch["cid"] in {"ok-pass", "far-pass"} and ch["pool"] == "library"
        assert all(r["cid"] in {"ok-pass", "far-pass"} for r in tried)
    # even when every passing track fails its audition, a failed or provisional track is never the fallback
    eng.bad = {"ok-pass", "far-pass"}
    ch, path, tried = MF.choose(FILM, _marks(), "/nonexistent", fetch=_lib_fetch)
    assert ch["cid"] in {"ok-pass", "far-pass"} and ch["carries"] >= MF.NO_ARRANGEMENT     # the mix then stands down: no music
    assert {r["cid"] for r in tried} <= {"ok-pass", "far-pass"}

    def offline(_t):
        raise OSError("offline")
    assert MF.choose(FILM, _marks(), "/nonexistent", fetch=offline)[0] is None


# ----------------------------------------------------------------- the reference set comes first

def test_the_reference_set_is_preferred_and_entered_at_its_entry_point(lib, monkeypatch):
    root = _make_set(lib / "music_ref", [_set_row("ref-groove"), _set_row("ref-podcast-only", kinds=["podcast"])])
    monkeypatch.setenv(MC.SET_ENV, str(root))
    MC._CACHE.clear()
    got = {t["cid"]: t for t in MC.set_tracks()}
    assert set(got) == {"ref-groove", "ref-podcast-only"} and got["ref-groove"]["path"].startswith(os.path.realpath(root))
    t = got["ref-groove"]
    assert t["drops"] == [70.0] and t["bpm"] == 120.0 and len(t["downbeats"]) >= 70 and t["lufs"] == -11.0
    pools = MC.workflow_pools(FILM)
    assert [n for n, _ in pools] == ["set", "library"] and [x["cid"] for x in pools[0][1]] == ["ref-groove"]   # its kinds only
    eng = _Engine()
    monkeypatch.setattr(MU, "analyse", eng.analyse)
    monkeypatch.setattr(MU, "arrange", eng.arrange)
    asked = []

    def model(system, user, schema):
        asked.append(user)
        return {"order": [1], "why": "the reference's own kind of groove"}
    ch, path, tried = MF.choose(FILM, _marks(), "/nonexistent", call=model, fetch=None)
    assert ch["cid"] == "ref-groove" and ch["pool"] == "set" and path == t["path"]          # its own file, never fetched
    assert "ok-pass" not in user_options(asked[0])                                          # the library is not even offered
    sec = ch["entry"]
    # the payoff at 40 s lands on the track's drop at 70 s: it is entered 30 s before it; the turn at 12 s on its break
    assert sec["start_s"] == 30.0 and any("lifts at 70.0" in w for w in sec["why"]), sec
    assert eng.asked and eng.asked[0][1] == (sec["point"]["t"] if sec["point"] else None)   # the engine gets the entry point
    # the box never sees the set
    assert all(o["cid"] != "ref-groove" for o in MC.shortlist(FILM, n=8)[0])


def user_options(text):
    return text.split("Options:", 1)[-1]


def test_the_set_falls_back_to_passing_library_tracks_when_none_of_it_carries_the_film(lib, monkeypatch):
    root = _make_set(lib / "music_ref", [_set_row("ref-one")])
    monkeypatch.setenv(MC.SET_ENV, str(root))
    MC._CACHE.clear()
    eng = _Engine(bad={"ref-one"})
    monkeypatch.setattr(MU, "analyse", eng.analyse)
    monkeypatch.setattr(MU, "arrange", eng.arrange)

    def fetch(t):
        return t["path"] if t.get("set") else _lib_fetch(t)
    ch, _p, tried = MF.choose(FILM, _marks(), "/nonexistent", fetch=fetch)
    assert tried[0]["cid"] == "ref-one" and tried[0]["pool"] == "set"
    assert ch["pool"] == "library" and ch["cid"] in {"ok-pass", "far-pass"}


def test_a_set_row_that_did_not_pass_or_has_no_file_inside_the_set_is_dropped(lib, monkeypatch):
    outside = lib / "elsewhere.m4a"
    outside.write_bytes(b"\0")
    rows = [_set_row("ref-good"), _set_row("ref-failed", passed=False), _set_row("ref-fails", fails=["clipped"]),
            _set_row("ref-missing"), _set_row("ref-escape", file="../elsewhere.m4a"), _set_row("ref-abs", file=str(outside)),
            dict(_set_row("Bad Id"))]
    root = _make_set(lib / "music_ref", rows, files={"ref-good", "ref-failed", "ref-fails", "Bad Id"})
    monkeypatch.setenv(MC.SET_ENV, str(root))
    MC._CACHE.clear()
    assert [t["cid"] for t in MC.set_tracks()] == ["ref-good"]
    monkeypatch.delenv(MC.SET_ENV)
    assert MC.set_tracks() == [] and [n for n, _ in MC.workflow_pools(FILM)] == ["library"]


# ----------------------------------------------------------------- the real library's verdicts

def test_the_real_gate_covers_the_library_and_no_failed_track_is_ever_shortlisted(monkeypatch):
    monkeypatch.delenv(MC.SET_ENV, raising=False)
    MC._CACHE.clear()
    if not MC.tracks():
        pytest.skip("library manifest not present")
    g = MC.gate()
    ids = {t["cid"] for t in MC.tracks()}
    assert g["pass"] and g["fail"]
    assert not (g["pass"] & set(g["fail"])) and not (g["provisional"] & set(g["fail"])) and not (g["pass"] & g["provisional"])
    assert (g["pass"] | g["provisional"] | set(g["fail"])) == ids
    assert {t["cid"] for t in MC.choosable(strict=True)} == g["pass"]
    for kind in MC.KINDS:
        for cuts in (2.0, 8.0, 15.0):
            film = dict(FILM, kind=kind, pace={"cuts10": cuts})
            assert not ({o["cid"] for o in MC.shortlist(film, n=8)[0]} & set(g["fail"]))
            for _name, pool in MC.workflow_pools(film):
                assert not ({t["cid"] for t in pool} & set(g["fail"]))
    MC._CACHE.clear()


# ----------------------------------------------------------------- no audio in this public repository

def _git(*args, cwd=WORKER):
    return subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True)


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_no_music_audio_is_ever_committed():
    top = _git("rev-parse", "--show-toplevel")
    if top.returncode:
        pytest.skip("not a git checkout")
    root = os.path.realpath(top.stdout.strip())
    files = _git("ls-files", cwd=root).stdout.splitlines()
    assert any(f.startswith("worker/music_library/") for f in files)          # the paths below are the repository's
    audio = (".mp3", ".m4a", ".wav", ".flac", ".ogg", ".aac", ".opus", ".aif", ".aiff")
    assert not [f for f in files if f.startswith("worker/music_library/") and f.lower().endswith(audio)]
    assert not [f for f in files if "music_ref/" in f or "music_cache/" in f]
    for p in ("music_ref/a.m4a", "worker/music_ref/x.wav", "worker/music_library/x.mp3", "worker/music_library/a/b.m4a",
              "plugins/x/music_cache/km-werq.mp3"):
        assert _git("check-ignore", "-q", "--no-index", p, cwd=root).returncode == 0, f"{p} is not ignored"
    # the set's default folder is outside the repository
    shell = os.path.join(root, "plugins", "valmera", "skills", "_shell", "chain.py")
    if os.path.exists(shell):
        src = open(shell).read()
        assert 'MUSIC_SET = f"{VV}/models/music_ref"' in src and "VALMERA_MUSIC_SET" in src


@pytest.mark.skipif(shutil.which(os.environ.get("FFMPEG_BIN", "ffmpeg")) is None, reason="needs ffmpeg")
def test_the_engine_opens_the_film_at_the_chosen_entry_point_when_the_bars_allow(tmp_path):
    from test_music_film import _marks as film_marks
    from test_music_film import _wav
    song = MU.analyse(_wav(tmp_path, "full", bars=40, intro_bars=4), sung=False)
    m = film_marks(dur=32.0, payoff=None)
    first = MU.arrange(song, m)["bars_played"][0]["bar"]
    assert first == 4                                                       # on its own: the song's first entrance
    for k in (10, 20):
        plan = MU.arrange(song, dict(m, open_s=song["bars"][k]["t0"]))
        assert plan["ok"] and plan["bars_played"][0]["bar"] == k and plan["intro_bars"] == 0
    # an entry point with too little song after it for the film is passed over, never looped into
    assert MU.arrange(song, dict(m, open_s=song["bars"][30]["t0"]))["bars_played"][0]["bar"] == 4
