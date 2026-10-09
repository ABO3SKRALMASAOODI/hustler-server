"""Sound-effect timing and tails.

An approved library recording placed at a time HITS there: its measured peak
lands on the requested frame (the file starts early, or skips into itself
when the hit is too close to 0 s), long tails stop at their measured fade
point, template cues sit on their composition's visual landing, and later
moves and cuts keep the hit on its moment. Uploaded/fetched sounds keep
plain file-start placement.
"""

import asyncio
import base64
import hashlib
import io
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import agent_tools  # noqa: E402
import db as dbx  # noqa: E402
import motion_engine  # noqa: E402
import motion_templates  # noqa: E402
import motion_tools  # noqa: E402
import sound_library  # noqa: E402
from schemas import default_edl, validate_edl  # noqa: E402
from timeline import Timeline, remap_program_items  # noqa: E402

GRID = 0.006          # the EDL keeps sfx times on a 10 ms grid


class _Db:
    """Project assets: one uploaded sound, nothing else."""

    def run(self, fn, *a, **k):
        if fn is dbx.asset_by_key and a[1] == "music/7/boom.wav":
            return {"kind": "music", "storage_key": a[1], "duration_s": 2.0,
                    "meta": {"filename": "boom.wav"}}
        return None


class _Ctx:
    project_id = 7

    def __init__(self, duration=40.0):
        self.duration = duration
        self._edl = validate_edl(default_edl(duration), duration).model_dump()
        self.writes = []
        self.db = _Db()
        self.index = {"video": {"width": 1080, "height": 1920}}

    def latest_edl(self):
        return {"version": len(self.writes) + 1, "json": self._edl}

    def write_edl(self, edl, description):
        self._edl = validate_edl(edl, self.duration).model_dump()
        self.writes.append(description)
        return f"EDL v{len(self.writes)} -> v{len(self.writes) + 1}: {description}"

    def sfx(self, sid):
        return next(s for s in self._edl["sfx"] if s["id"] == sid)


@pytest.fixture
def lib(monkeypatch):
    """Library uploads resolve to their real project keys (no storage/db)."""
    monkeypatch.setattr(motion_tools, "ensure_library_asset",
                        lambda ctx, sid: sound_library.asset_key(ctx.project_id, sid))


# ── the manifest: measured, never re-encoded ────────────────────────────

def test_approved_files_are_untouched_and_timing_fields_are_measured():
    for r in sound_library.catalog():
        data = open(sound_library.path(r["id"]), "rb").read()
        assert hashlib.sha256(data).hexdigest()[:16] == r["sha"], r["id"]
        assert 0 <= r["peak_s"] < r["duration_s"], r["id"]
        if r.get("max_s") is not None:
            # a tail cap ends after the hit and before the file does
            assert r["peak_s"] + 0.2 < r["max_s"] < r["duration_s"] - 0.15, r["id"]
    # the long boom that rang under the next line stops ~0.6 s after its hit
    assert sound_library.max_s("impact_1") == 1.25
    # risers end on their peak and typing runs to its last keystroke: no cap
    for sid in ("riser_1", "riser_2", "riser_3", "riser_4", "typing_1", "typing_2"):
        assert sound_library.max_s(sid) is None, sid
    # typing plays UNDER its action from the first keystroke
    assert sound_library.hit_s("typing_1") == sound_library.hit_s("typing_2") == 0.0
    # the boom reaches full level ~0.69 s in (where the judges heard it); its
    # loudest 10 ms, a fluctuation inside the boom, is later
    assert sound_library.hit_s("impact_1") == 0.69 and sound_library.peak_s("impact_1") == 0.755


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not on PATH")
def test_hit_times_are_measured_from_the_recordings():
    import numpy as np
    for r in sound_library.catalog():
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", sound_library.path(r["id"]),
                              "-ac", "1", "-ar", "48000", "-f", "f32le", "-"],
                             capture_output=True, check=True).stdout
        x = np.frombuffer(raw, dtype=np.float32)
        win, hop = 480, 48                                  # 10 ms, every 1 ms
        env = np.array([20 * np.log10(np.sqrt(np.mean(x[k:k + win] ** 2)) + 1e-9)
                        for k in range(0, max(1, len(x) - win), hop)])
        t = (np.arange(len(env)) * hop + win / 2) / 48000.0
        # peak_s is the loudest 10 ms (shutter_1's once named its quieter
        # second click, 85 ms after the shutter)
        assert abs(r["peak_s"] - t[int(np.argmax(env))]) <= 0.01, r["id"]
        if r.get("hit_s") is not None:
            # an override is where the sound reaches full level, clearly
            # before a later loudest fluctuation
            attack = t[int(np.nonzero(env >= env.max() - 1.0)[0][0])]
            assert abs(r["hit_s"] - attack) <= 0.015 and r["hit_s"] < r["peak_s"] - 0.04, r["id"]


def test_listing_tells_the_agent_where_each_sound_peaks():
    listing = motion_tools.list_sound_library(None)
    assert "should HIT" in listing
    assert "sound:impact_1 [impact, 2.624s, hits 0.69s in, stops by default 1.25s in" in listing
    assert "sound:typing_1 [typing, 3.498s, plays from its first sound" in listing


# ── placement ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("sid", [r["id"] for r in sound_library.catalog()])
def test_every_library_hit_lands_on_the_requested_time(sid):
    for hit in (0.0, 0.05, 0.3, 1.0, 12.34, 26.97):
        pl = sound_library.place(sid, hit)
        assert pl["at"] >= 0
        item = {"storage_key": sound_library.asset_key(7, sid), "at": pl["at"],
                "offset_s": pl["offset_s"]}
        assert abs(sound_library.hit_at(item) - hit) <= GRID, (sid, hit, pl)


def test_peak_preroll_and_negative_preroll_skip_into_the_file():
    # the judged failure: impact_1 placed by file start hit 0.69 s late
    pl = sound_library.place("impact_1", 13.45)
    assert abs(pl["at"] - 12.76) <= GRID and pl["offset_s"] is None
    assert pl["lead_s"] == 0.69
    # too close to 0 s for the lead-in: start at 0, skip into the file
    pl = sound_library.place("impact_1", 0.3)
    assert pl["at"] == 0.0 and pl["offset_s"] == 0.39 and abs(pl["lead_s"] - 0.3) < GRID
    # a riser ENDS on the moment
    pl = sound_library.place("riser_1", 5.0)
    assert pl["at"] == round(5.0 - 1.828, 2)
    # typing starts on the moment
    assert sound_library.place("typing_2", 0.05, dur_s=3.4) == {
        "at": 0.05, "offset_s": None, "dur_s": 3.4, "lead_s": 0.0}


def test_tail_cap_is_the_default_play_length_and_dur_s_wins():
    assert sound_library.place("impact_1", 10.0)["dur_s"] == 1.25
    # the cap is file time: a skip into the file shortens what is left of it
    assert sound_library.place("impact_1", 0.3)["dur_s"] == pytest.approx(1.25 - 0.39)
    assert sound_library.place("impact_1", 10.0, dur_s=2.0)["dur_s"] == 2.0
    assert sound_library.place("whoosh_soft_1", 10.0)["dur_s"] is None


def test_add_sfx_library_reports_the_hit_and_stores_the_preroll(lib):
    ctx = _Ctx()
    out = agent_tools.add_sfx(ctx, "sound:impact_1", at=26.97, gain_db=-9,
                              purpose="NEXT LEVEL slam")
    assert out.startswith("EDL v1"), out
    assert "HIT: its peak lands at 26.97s" in out
    assert "0.69s early" in out and "measured fade point" in out
    s = ctx.sfx("sx1")
    assert s["storage_key"] == sound_library.asset_key(7, "impact_1")
    assert s["at"] == round(26.97 - 0.69, 2) and s["dur_s"] == 1.25
    assert not s.get("offset_s")
    assert abs(sound_library.hit_at(s) - 26.97) <= GRID
    # the audit shows the hit next to the file start
    rows = agent_tools._declared_mix_state(ctx, ctx._edl)["sfx"]
    assert abs(rows[0]["hit_at"] - 26.97) <= GRID and rows[0]["at"] == s["at"]


def test_add_sfx_near_zero_skips_into_the_file(lib):
    ctx = _Ctx()
    out = agent_tools.add_sfx(ctx, "sound:whoosh_soft_1", at=0.1, gain_db=-13)
    assert out.startswith("EDL v1"), out
    s = ctx.sfx("sx1")
    assert s["at"] == 0.0 and s["offset_s"] == pytest.approx(0.484 - 0.1, abs=GRID)
    assert abs(sound_library.hit_at(s) - 0.1) <= GRID
    assert "starts 0.38s into the file" in out


def test_add_sfx_explicit_lengths_and_typing(lib):
    ctx = _Ctx()
    agent_tools.add_sfx(ctx, "sound:impact_1", at=10.0, gain_db=-9, dur_s=0.9)
    assert ctx.sfx("sx1")["dur_s"] == 0.9
    # "all of it" past the cap stays explicit
    agent_tools.add_sfx(ctx, "sound:impact_1", at=20.0, gain_db=-9, dur_s=5.0)
    assert ctx.sfx("sx2")["dur_s"] == pytest.approx(2.624)
    out = agent_tools.add_sfx(ctx, "sound:typing_2", at=4.0, gain_db=-17, dur_s=1.2)
    s = ctx.sfx("sx3")
    assert s["at"] == 4.0 and s["dur_s"] == 1.2 and "it starts at 4.00s" in out


def test_uploaded_sounds_keep_file_start_placement():
    ctx = _Ctx()
    out = agent_tools.add_sfx(ctx, "music/7/boom.wav", at=12.0)
    assert out.startswith("EDL v1"), out
    s = ctx.sfx("sx1")
    assert s["at"] == 12.0 and not s.get("offset_s") and not s.get("dur_s")
    assert "HIT:" not in out
    assert agent_tools.move_sfx(ctx, "sx1", at=15.0).startswith("EDL v2")
    assert ctx.sfx("sx1")["at"] == 15.0
    # a look-alike upload is never mistaken for a library recording
    assert sound_library.id_for_key("sfx/7/lib-impact_1-0000000000.flac") is None
    assert sound_library.id_for_key("uploads/lib-impact_1.flac") is None


def test_move_sfx_moves_the_hit_and_rederives_automatic_fields(lib):
    ctx = _Ctx()
    agent_tools.add_sfx(ctx, "sound:impact_1", at=0.3, gain_db=-9)
    s = ctx.sfx("sx1")
    assert s["at"] == 0.0 and s["offset_s"] == 0.39 and s["dur_s"] == pytest.approx(0.86)
    out = agent_tools.move_sfx(ctx, "sx1", at=8.0)
    assert out.startswith("EDL v2") and "-> 8s (starts 7.31s" in out, out
    s = ctx.sfx("sx1")
    # the forced skip and its shortened cap are gone: the full lead-in plays
    assert s["at"] == round(8.0 - 0.69, 2) and not s.get("offset_s")
    assert s["dur_s"] == 1.25
    assert abs(sound_library.hit_at(s) - 8.0) <= GRID
    # a deliberate dur_s survives a move
    agent_tools.add_sfx(ctx, "sound:impact_1", at=20.0, gain_db=-9, dur_s=0.9)
    agent_tools.move_sfx(ctx, "sx2", at=22.0)
    s2 = ctx.sfx("sx2")
    assert s2["dur_s"] == 0.9 and abs(sound_library.hit_at(s2) - 22.0) <= GRID


def test_cuts_keep_a_library_hit_on_its_moment():
    key = sound_library.asset_key(7, "impact_1")
    pl = sound_library.place("impact_1", 26.97)
    edl = validate_edl(dict(default_edl(40.0), sfx=[{
        "id": "sx1", "storage_key": key, "at": pl["at"], "dur_s": pl["dur_s"],
        "gain_db": -9.0}]), 40.0).model_dump()
    old = Timeline([[0.0, 40.0]], [])
    # a pause cut BETWEEN the file start and the hit: the hit follows its
    # moment (26.57), the lead is re-applied before it
    edl1 = dict(edl, keep=[[0.0, 26.5], [26.9, 40.0]])
    remap_program_items(edl1, old, Timeline(edl1["keep"], []))
    assert abs(sound_library.hit_at(edl1["sfx"][0]) - 26.57) <= GRID
    # a cut over the file start alone no longer drops the sound
    edl2 = dict(edl, keep=[[0.0, 26.0], [26.5, 40.0]])
    remap_program_items(edl2, old, Timeline(edl2["keep"], []))
    assert len(edl2["sfx"]) == 1
    assert abs(sound_library.hit_at(edl2["sfx"][0]) - 26.47) <= GRID
    # cutting the hit's own moment still removes it
    edl3 = dict(edl, keep=[[0.0, 26.9], [27.1, 40.0]])
    notes = remap_program_items(edl3, old, Timeline(edl3["keep"], []))
    assert edl3["sfx"] == [] and notes is not None
    # an untouched sound keeps its exact placement
    edl4 = dict(edl, keep=[[0.0, 40.0]])
    remap_program_items(edl4, old, Timeline(edl4["keep"], []))
    assert edl4["sfx"][0]["at"] == edl["sfx"][0]["at"]


def test_a_cut_moves_only_the_timing_of_older_library_sounds():
    # placed by file start before peak timing existed: an impact near the end
    # peaks past it, and plays its whole recording
    key = sound_library.asset_key(7, "impact_1")
    edl = validate_edl(dict(default_edl(30.0), sfx=[
        {"id": "sx1", "storage_key": key, "at": 29.5, "gain_db": -9.0},
        {"id": "sx2", "storage_key": key, "at": 10.0, "gain_db": -9.0}]), 30.0).model_dump()
    cut = dict(edl, keep=[[0.0, 5.0], [6.0, 30.0]])
    remap_program_items(cut, Timeline([[0.0, 30.0]], []), Timeline(cut["keep"], []))
    # an unrelated cut keeps both (it starts inside the shorter edit)...
    by_id = {s["id"]: s for s in cut["sfx"]}
    assert by_id["sx1"]["at"] == 28.5 and by_id["sx2"]["at"] == 9.0
    # ...and adds no tail cap the editor never chose: only the timing moved
    assert not by_id["sx1"].get("dur_s") and not by_id["sx2"].get("dur_s")


def test_a_deliberate_length_stops_at_the_same_point_in_the_file(lib):
    ctx = _Ctx()
    agent_tools.add_sfx(ctx, "sound:impact_1", at=0.3, gain_db=-9, dur_s=1.5)
    s = ctx.sfx("sx1")
    assert s["at"] == 0.0 and s["offset_s"] == 0.39 and s["dur_s"] == 1.5
    stop = s["offset_s"] + s["dur_s"]
    # moved clear of 0 s: the full lead-in returns and it still rings to the
    # same point after its hit
    agent_tools.move_sfx(ctx, "sx1", at=8.0)
    s = ctx.sfx("sx1")
    assert not s.get("offset_s") and s["dur_s"] == pytest.approx(stop)
    agent_tools.move_sfx(ctx, "sx1", at=0.3)
    s = ctx.sfx("sx1")
    assert (s["offset_s"] or 0.0) + s["dur_s"] == pytest.approx(stop)


def test_a_preserved_sound_lane_survives_a_cut_that_retimes_library_sounds():
    import scope_guard
    impact, whoosh = (sound_library.asset_key(7, sid) for sid in ("impact_1", "whoosh_soft_1"))
    pl = sound_library.place("whoosh_soft_1", 0.2)          # skips into its file
    prev = validate_edl(dict(default_edl(30.0), sfx=[
        {"id": "sx1", "storage_key": impact, "at": 10.0, "gain_db": -9.0},
        {"id": "sx2", "storage_key": whoosh, "at": pl["at"], "offset_s": pl["offset_s"],
         "gain_db": -13.0}]), 30.0).model_dump()
    ask = "add the photo at the start but don't touch the sound effects"
    ins = {"id": "ins1", "asset_key": "img/7/0.png", "kind": "image",
           "at_output_s": 0.0, "duration_s": 2.0}
    new = dict(prev, inserts=[ins], sfx=[dict(s) for s in prev["sfx"]])
    remap_program_items(new, Timeline([[0.0, 30.0]], []), Timeline(new["keep"], [ins]))
    new = validate_edl(new, 30.0).model_dump()
    # the whoosh no longer needs its skip, yet nothing the editor set changed
    whoosh_new = next(s for s in new["sfx"] if s["id"] == "sx2")
    assert not whoosh_new.get("offset_s") and abs(sound_library.hit_at(whoosh_new) - 2.2) <= GRID
    assert scope_guard.preservation_violations(prev, new, ask) == []
    louder = json_copy(new)
    louder["sfx"][0]["gain_db"] = -3.0
    assert scope_guard.preservation_violations(prev, louder, ask) == ["sound effects"]
    longer = json_copy(new)
    next(s for s in longer["sfx"] if s["id"] == "sx1")["dur_s"] = 0.9
    assert scope_guard.preservation_violations(prev, longer, ask) == ["sound effects"]


def json_copy(value):
    import json
    return json.loads(json.dumps(value))


def test_a_junction_whoosh_follows_its_broll_entry():
    # look/transition whooshes HIT the B-roll entry cut, a point with no
    # source time of its own: they follow the footage they lead in from
    key = sound_library.asset_key(7, "whoosh_soft_2")
    pl = sound_library.place("whoosh_soft_2", 8.0)
    ins = {"id": "ins1", "asset_key": "img/7/0.png", "kind": "image",
           "at_output_s": 8.0, "duration_s": 2.0}
    edl = validate_edl(dict(default_edl(30.0), keep=[[0.0, 8.0], [8.0, 30.0]], inserts=[ins],
                            sfx=[{"id": "look_tx1", "storage_key": key, "at": pl["at"],
                                  "gain_db": -9.0}]), 30.0).model_dump()
    assert sound_library.hit_at(edl["sfx"][0]) >= 8.0      # on/inside the entry
    old = Timeline(edl["keep"], edl["inserts"])
    moved = dict(ins, at_output_s=7.0)                     # a 1 s cut before it
    new = dict(edl, keep=[[0.0, 2.0], [3.0, 8.0], [8.0, 30.0]], inserts=[moved])
    remap_program_items(new, old, Timeline(new["keep"], [moved]))
    assert abs(sound_library.hit_at(new["sfx"][0]) - 7.0) <= GRID


def test_a_proof_window_keeps_a_hit_whose_lead_in_starts_before_it():
    import stitch
    key = sound_library.asset_key(7, "impact_1")
    pl = sound_library.place("impact_1", 10.2)
    edl = validate_edl(dict(default_edl(30.0), sfx=[
        {"id": "sx1", "storage_key": key, "at": pl["at"], "dur_s": pl["dur_s"],
         "gain_db": -9.0},
        {"id": "sx2", "storage_key": key, "at": 6.0, "gain_db": -9.0}]), 30.0).model_dump()
    win = stitch.window_edl(edl, Timeline(edl["keep"]), 10.0, 14.0, keep_audio=True)
    # the slam at 10.2 still hits 0.2 s into the proof; the earlier sound
    # (hit 6.755) stays out
    (s,) = win["sfx"]
    assert s["id"] == "sx1" and s["at"] == 0.0
    assert abs(sound_library.hit_at(s) - 0.2) <= GRID
    assert s["dur_s"] == pytest.approx(pl["dur_s"] - (10.0 - pl["at"]), abs=0.002)


def test_a_changed_sound_flags_the_seconds_up_to_its_hit():
    import edl_diff
    key = sound_library.asset_key(7, "riser_1")
    pl = sound_library.place("riser_1", 12.0)                # starts 1.83 s early
    prev = validate_edl(default_edl(30.0), 30.0).model_dump()
    new = validate_edl(dict(prev, sfx=[{"id": "sx1", "storage_key": key, "at": pl["at"],
                                        "gain_db": -14.0}]), 30.0).model_dump()
    (rng,) = edl_diff.change_ranges(prev, new)["out_ranges"]
    assert rng[0] == pytest.approx(pl["at"]) and rng[1] >= 12.0
    # a plain upload keeps its short window from where it starts
    up = validate_edl(dict(prev, sfx=[{"id": "sx1", "storage_key": "music/7/boom.wav",
                                       "at": 12.0}]), 30.0).model_dump()
    assert edl_diff.change_ranges(prev, up)["out_ranges"] == [[12.0, 12.6]]


def test_audits_space_library_sounds_by_where_they_hit():
    import quality_gate
    import taste

    def lib(sid, hit, n):
        pl = sound_library.place(sid, hit)
        return {"id": f"sx{n}", "storage_key": sound_library.asset_key(7, sid),
                "at": pl["at"], "offset_s": pl["offset_s"], "gain_db": -13.0}

    # two whooshes HITTING 0.2 s apart flam, though their files start 0.6 s apart
    a, b = lib("whoosh_soft_1", 10.0, 1), lib("swish_1", 10.2, 2)
    assert abs(b["at"] - a["at"]) > taste.SFX_MIN_SPACING_S
    assert taste.sfx_muddy_pair(a, b)
    prev = validate_edl(dict(default_edl(30.0), sfx=[a]), 30.0).model_dump()
    new = validate_edl(dict(default_edl(30.0), sfx=[a, b]), 30.0).model_dump()
    found = quality_gate.advisory_findings(prev, new, "")
    assert any("0.20s apart" in f for f in found), found
    # ...while two hitting 0.6 s apart are not one, though their files start
    # 0.19 s apart
    a, b = lib("whoosh_soft_1", 10.6, 1), lib("swish_1", 10.0, 2)
    assert abs(b["at"] - a["at"]) < taste.SFX_MIN_SPACING_S
    assert not taste.sfx_muddy_pair(a, b)
    # an uploaded sound is still spaced from where it starts
    assert taste.sfx_time({"at": 4.0, "storage_key": "music/7/boom.wav"}) == 4.0


def test_the_listening_review_is_told_where_library_sounds_hit():
    key = sound_library.asset_key(7, "impact_1")
    pl = sound_library.place("impact_1", 12.0)
    edl = {"sfx": [{"id": "sx1", "storage_key": key, "at": pl["at"], "dur_s": pl["dur_s"],
                    "purpose": "payoff"},
                   {"id": "sx2", "storage_key": "music/7/boom.wav", "at": 20.0}]}
    # the excerpt holds the hit but not the file start
    label = agent_tools._audio_window_label(edl, {}, 11.5, 14.0)
    hit = sound_library.hit_at(edl["sfx"][0])
    assert abs(hit - 12.0) <= GRID
    assert f"SFX id=sx1 at={pl['at']:.2f}s hits={hit:.2f}s purpose=payoff" in label
    context = agent_tools._audio_execution_context(edl, {})
    assert f"hits={hit:.2f}s" in context and "SFX sx2 at=20.0s touches" in context


def test_mcp_clients_are_not_told_to_preroll_by_hand():
    path = os.path.join(os.path.dirname(__file__), "..", "..", "backend", "routes", "mcp.py")
    text = " ".join(open(path).read().split())
    assert "pre-rolled" not in text
    assert "lands each recording's peak on `at`, so never pre-roll by hand" in text


def test_backend_loads_the_remap_standalone():
    # routes/video.py loads worker/timeline.py by path without the worker on
    # sys.path; the library lookup must load by path there too.
    worker = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = f"""
import importlib.util, sys
sys.path = [p for p in sys.path if p not in ('', {worker!r})]
def load(name, fn):
    spec = importlib.util.spec_from_file_location(name, {worker!r} + '/' + fn)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, mod)
    spec.loader.exec_module(mod)
    return mod
load('worker_schemas', 'schemas.py')
tl = load('worker_timeline', 'timeline.py')
lib = tl._sound_library()
key = lib.asset_key(7, 'impact_1')
edl = {{'keep': [[0.0, 26.5], [26.9, 40.0]], 'sfx': [{{'id': 'sx1', 'storage_key': key,
       'at': 26.28, 'gain_db': -9.0}}]}}
tl.remap_program_items(edl, tl.Timeline([[0.0, 40.0]], []), tl.Timeline(edl['keep'], []))
print(lib.__name__, lib.hit_at(edl['sfx'][0]))
"""
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd="/", timeout=60)
    assert out.returncode == 0, out.stderr
    name, hit = out.stdout.split()
    assert name == "worker_sound_library" and abs(float(hit) - 26.57) <= GRID


def _rendered_sfx(item, prog=20.0):
    """The renderer's own filter chain for one sfx item, run through ffmpeg
    on the real library file: (mono samples at 48 kHz, envelope dB per 5 ms)."""
    import numpy as np
    import renderer
    sid = sound_library.id_for_key(item["storage_key"])
    g = renderer.build_filtergraph(
        {"keep": [[0.0, prog]], "sfx": [item]}, prog, True, Timeline([[0.0, prog]], []),
        None, [], {"words": []}, True, W=1080, H=1920, fps=30.0,
        sfx_inputs=[(1, item, sound_library.get(sid)["duration_s"])])
    i = g.index("[1:a]")
    chain = g[i + len("[1:a]"):g.index("[sfx0]", i)]
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", sound_library.path(sid), "-filter_complex",
         f"[0:a]{chain},pan=mono|c0=c0[o]", "-map", "[o]", "-f", "f32le", "-"],
        capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype=np.float32)
    hop, win = 240, 960
    env = [20 * np.log10(np.sqrt(np.mean(x[k:k + win] ** 2)) + 1e-9)
           for k in range(0, max(1, len(x) - win), hop)]
    return x, np.array(env), hop / 48000.0, win / 48000.0


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not on PATH")
@pytest.mark.parametrize("sid,hit", [("impact_1", 13.45), ("impact_1", 0.3),
                                     ("whoosh_soft_1", 4.0), ("whoosh_soft_2", 0.1),
                                     ("riser_2", 6.0)])
def test_rendered_peak_lands_on_the_requested_time(sid, hit):
    import numpy as np
    pl = sound_library.place(sid, hit)
    item = {"id": "a", "storage_key": sound_library.asset_key(7, sid), "at": pl["at"],
            "offset_s": pl["offset_s"], "dur_s": pl["dur_s"], "gain_db": -9.0}
    _x, env, hop, win = _rendered_sfx(item)
    loudest = int(np.argmax(env)) * hop + win / 2
    # where it reaches full level (within 1 dB of its loudest)
    attack = int(np.nonzero(env >= env.max() - 1.0)[0][0]) * hop + win / 2
    assert abs(attack - hit) <= 0.045 and attack - 0.03 <= hit <= loudest + 0.03, \
        (sid, hit, attack, loudest)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not on PATH")
def test_rendered_impact_tail_stops_at_its_fade_point():
    import numpy as np
    pl = sound_library.place("impact_1", 10.0)
    item = {"id": "a", "storage_key": sound_library.asset_key(7, "impact_1"),
            "at": pl["at"], "dur_s": pl["dur_s"], "gain_db": -9.0}
    x, _env, _hop, _win = _rendered_sfx(item)
    # the boom used to ring ~1.9 s past its hit, under the next line
    end = (np.nonzero(np.abs(x) > 1e-4)[0][-1] + 1) / 48000.0
    assert 10.52 <= end <= 10.6, end


# ── template-owned cues ─────────────────────────────────────────────────

def _cues(template, params, start, end, seed="mg"):
    clean = motion_templates.normalize_params(template, params)
    return motion_tools._sfx_cues(motion_templates.spec(template), clean, start, end,
                                  seed=seed, with_dur=True)


def test_cue_landings_follow_each_templates_own_timing():
    # word_slam's slam lands 0.2 s in: the whoosh sucks into the kick there
    cues = _cues("word_slam", {"text": "garbage", "entrance": "slam"}, 13.24, 14.09)
    assert sorted((round(t, 3), k) for t, k, _g, _d in cues) == [
        (13.44, "impact_1"), (13.44, sorted(c[1] for c in cues)[1])]
    # counter: the count lands at min(1.0, max(0.5, 0.55 * duration))
    for dur, land in ((3.0, 1.0), (1.4, 0.77), (0.8, 0.5)):
        kick = [t for t, k, _g, _d in _cues("counter", {"value": "140"}, 30.0, 30.0 + dur)
                if k == "impact_1"]
        assert kick == [pytest.approx(30.0 + land)], dur
    # the data templates' landings scale the same way their JS does
    for name, dur, kind, land in (("line_chart", 3.5, "ding_1", 0.72),
                                  ("line_chart", 1.0, "ding_1", 0.55),
                                  ("progress_ring", 3.0, "ding_1", 1.2),
                                  ("progress_ring", 1.2, "ding_1", 0.66),
                                  ("stat_card", 3.5, "pop_1", 0.78),
                                  ("stat_card", 1.5, "pop_1", 0.405),
                                  ("stat_card", 0.8, "pop_1", 0.22)):
        spec = motion_templates.spec(name)
        params = dict(spec.get("example") or {})
        got = [t for t, k, _g, _d in _cues(name, params, 0.0, dur) if k == kind]
        assert got == [pytest.approx(land, abs=0.001)], (name, dur, got)
    # chapter_title's collapse swish lands as the title folds into its chip
    got = [t for t, k, _g, _d in _cues("chapter_title", {"title": "Part two", "exit": "collapse"},
                                        0.0, 2.6) if k == "swish_1"]
    assert got == [pytest.approx(1.8)]
    # typewriter: typing starts with the first character (later on a plate)
    for plate, at in ((False, 0.04), (True, 0.16)):
        got = _cues("typewriter", {"text": "hello world", "plate": plate}, 0.0, 3.0)
        assert [round(t, 3) for t, _k, _g, _d in got] == [at], plate


def test_declared_cue_times_are_their_default_duration_landings():
    for name in motion_templates.names():
        spec = motion_templates.spec(name)
        for c in spec.get("sfx") or []:
            if isinstance(c.get("land"), dict):
                at = motion_tools._land_at(c["land"], float(spec["duration"]), None)
                assert at == pytest.approx(float(c["at"]), abs=0.011), (name, c)


def test_owned_cues_hit_their_landing_and_follow_moves(lib, monkeypatch):
    monkeypatch.setattr(motion_tools, "_probe_item", lambda item, W, H, fps=30.0:
                        {"errors": [], "visible_frames": 4, "samples": 4,
                         "bboxes": [[.1, .3, .9, .5]]})
    ctx = _Ctx()
    out = motion_tools.add_motion_graphic(ctx, "word_slam", 13.24, 14.74, id="slam",
                                          params={"text": "garbage", "entrance": "slam"},
                                          sfx=True)
    assert out.startswith("EDL v1") and "hitting at" in out, out
    owned = [s for s in ctx._edl["sfx"] if s["id"].startswith("mg_slam_sfx")]
    assert len(owned) == 2
    for s in owned:
        assert abs(sound_library.hit_at(s) - 13.44) <= GRID, s
    impact = next(s for s in owned if "impact_1" in s["storage_key"])
    assert impact["at"] == round(13.44 - 0.69, 2) and impact["dur_s"] == 1.25
    # moved to the head of the program: the hit follows, skipping into the file
    assert motion_tools.set_motion_graphic(ctx, "slam", start=0.0, end=1.5).startswith("EDL v")
    owned = [s for s in ctx._edl["sfx"] if s["id"].startswith("mg_slam_sfx")]
    for s in owned:
        assert abs(sound_library.hit_at(s) - 0.2) <= GRID, s
    impact = next(s for s in owned if "impact_1" in s["storage_key"])
    assert impact["at"] == 0.0 and impact["offset_s"] == pytest.approx(0.49, abs=GRID)
    # and back: the full lead-in returns
    assert motion_tools.set_motion_graphic(ctx, "slam", start=10.0, end=11.5).startswith("EDL v")
    impact = next(s for s in ctx._edl["sfx"] if "impact_1" in s["storage_key"])
    assert impact["at"] == round(10.2 - 0.69, 2) and not impact.get("offset_s")
    assert impact["dur_s"] == 1.25


def test_a_new_length_moves_the_cues_that_follow_it(lib, monkeypatch):
    monkeypatch.setattr(motion_tools, "_probe_item", lambda item, W, H, fps=30.0:
                        {"errors": [], "visible_frames": 4, "samples": 4,
                         "bboxes": [[.1, .3, .9, .5]]})
    ctx = _Ctx()
    motion_tools.add_motion_graphic(ctx, "counter", 10.0, 13.0, id="num",
                                    params={"value": "140"}, sfx=True)

    def hits():
        return sorted(sound_library.hit_at(s) for s in ctx._edl["sfx"]
                      if s["id"].startswith("mg_num_sfx"))
    assert hits() == pytest.approx([10.05, 11.0], abs=GRID)
    # shortened to 1.4 s the count lands at 0.77 s: the kick lands with it
    assert motion_tools.set_motion_graphic(ctx, "num", end=11.4).startswith("EDL v")
    assert hits() == pytest.approx([10.05, 10.77], abs=GRID)
    # a plain move keeps the (possibly hand-tuned) cues and shifts them
    next(s for s in ctx._edl["sfx"] if s["id"] == "mg_num_sfx1")["gain_db"] = -20.0
    assert motion_tools.set_motion_graphic(ctx, "num", start=12.0, end=13.4).startswith("EDL v")
    assert hits() == pytest.approx([12.05, 12.77], abs=GRID)
    assert next(s for s in ctx._edl["sfx"] if s["id"] == "mg_num_sfx1")["gain_db"] == -20.0


# ── the cue times against the rendered compositions ─────────────────────

def _chromium_ok():
    if not motion_engine.available():
        return False
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:
        return False


async def _frames(template, dur, times, params=None):
    from playwright.async_api import async_playwright
    import numpy as np
    from PIL import Image
    clean = motion_templates.normalize_params(template, params or {})
    job = motion_templates.build_job({"id": "m", "template": template, "start": 0.0,
                                      "end": dur, "params": clean}, 1080, 1920, 30.0)
    dw, dh = motion_engine.design_size(job.out_w, job.out_h)
    out = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=motion_engine.CHROME_ARGS)
        try:
            ctx = await browser.new_context(viewport={"width": dw, "height": dh},
                                            device_scale_factor=1)
            await ctx.route("**/*", await motion_engine._route_factory(job))
            page = await ctx.new_page()
            await page.goto(motion_engine.ORIGIN + "/", wait_until="load")
            await page.evaluate("async () => { await document.fonts.ready; return true; }")
            cdp = await ctx.new_cdp_session(page)
            await cdp.send("Emulation.setDefaultBackgroundColorOverride",
                           {"color": {"r": 0, "g": 0, "b": 0, "a": 0}})
            for t in times:
                await page.evaluate("t => window.__mgSeek(t)", float(t))
                r = await cdp.send("Page.captureScreenshot", {
                    "format": "png", "clip": {"x": 0, "y": 0, "width": dw, "height": dh,
                                              "scale": 0.125}})
                im = Image.open(io.BytesIO(base64.b64decode(r["data"]))).convert("RGBA")
                out.append(np.asarray(im, dtype=np.float32))
        finally:
            await browser.close()
    return out


@pytest.mark.skipif(not _chromium_ok(), reason="Chromium not available")
@pytest.mark.parametrize("template,dur", [("flash_transition", 0.3), ("flash_transition", 0.2),
                                          ("film_burn", 0.8), ("film_burn", 0.5),
                                          ("glitch_burst", 0.35), ("glitch_burst", 0.6)])
def test_transition_cues_hit_the_brightest_frame(template, dur):
    import numpy as np
    times = [i / 30 for i in range(int(round(dur * 30)))]
    frames = asyncio.run(_frames(template, dur, times))
    lum = [float(np.mean(f[..., :3].mean(-1) * f[..., 3] / 255)) for f in frames]
    brightest = times[int(np.argmax(lum))]
    (cue,) = [t for t, _k, _g, _d in _cues(template, {}, 0.0, dur)]
    assert abs(cue - brightest) <= 1.5 / 30, (template, dur, cue, brightest)


@pytest.mark.skipif(not _chromium_ok(), reason="Chromium not available")
def test_word_slam_cues_hit_the_slam_frame():
    import numpy as np
    times = [i / 60 for i in range(0, 25)]
    params = {"text": "Ordinary", "entrance": "slam"}
    frames = asyncio.run(_frames("word_slam", 1.5, times, params))
    # the type accelerates in (inQuad) and locks at full size: the landing is
    # the last frame of that fast run, after which the picture holds still
    change = [float(np.mean(np.abs(b - a))) for a, b in zip(frames, frames[1:])]
    landing = times[1 + max(i for i, c in enumerate(change) if c >= 0.5 * max(change))]
    cues = _cues("word_slam", params, 0.0, 1.5)
    assert all(abs(t - landing) <= 1.5 / 30 for t, _k, _g, _d in cues), (cues, landing)


# ── apply_look keeps its single pre-roll ────────────────────────────────

def test_apply_look_transition_sound_is_not_double_shifted(lib):
    pl = sound_library.place("whoosh_soft_1", 10.0)
    # exactly the look's former round(t - peak_s) placement, nothing added
    assert pl["at"] == round(10.0 - sound_library.peak_s("whoosh_soft_1"), 2)
    assert pl["offset_s"] is None and pl["dur_s"] is None
    for sid in ("whoosh_soft_2", "swish_1", "glitch_1", "glitch_2"):
        assert sound_library.place(sid, 10.0)["dur_s"] is None, sid
