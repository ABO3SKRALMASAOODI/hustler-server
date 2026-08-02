"""Round 77 — a travelling zoom's KEYFRAMES follow their content.

The real failure (project 300, v374): the user dragged a 3s meme image in
after scene 6. The master choreography path (18 keyframes over 6.6-34.8s)
kept its window and its FRACTIONS while every scene after the meme moved 3s
right — so the chat close-up choreographed for the next scene played ON the
meme, and every later beat was early. Windows have re-anchored through
content since round 71; the keyframes INSIDE them never did.

Each keyframe now maps through its own content (kept footage via source
time, spliced scenes via clip time, rate-aware), with two repairs:
  * brand-new content inside the move plays WIDE (the aims predate it);
  * a tight cut-pair pushed apart keeps its jump (hold, then re-aim).

Run:  python -m pytest tests/test_path_remap.py -q     (from worker/)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schemas import default_edl, validate_edl                 # noqa: E402
from timeline import Timeline, remap_program_items            # noqa: E402

SRC = 354.6
KEEP = [[113.7, 117.25]]                    # scene 1: 0-3.55
REC = "clips/1/rec.mov"
REC2 = "clips/1/rec2.mov"
MEME = "images/1/meme.jpg"


def _ins(id, dur, src, asset, kind="video", rate=None):
    it = {"id": id, "asset_key": asset, "kind": kind,
          "at_output_s": 3.55, "duration_s": dur}
    if kind == "video":
        it["source_start_s"] = src
    if rate is not None:
        it["rate"] = rate
    return it


def _abs_times(z):
    s, e = float(z["start"]), float(z["end"])
    return [round(s + float(p["f"]) * (e - s), 2) for p in z["path"]]


def _base():
    """keep 0-3.55, then A (3s, rec@10), B (4s, rec@20), C (5s, rec2@30):
    program 15.55s. Path zoom 4.0-15.0 with two beats per scene."""
    e = default_edl(SRC)
    e["keep"] = [list(k) for k in KEEP]
    e["inserts"] = [_ins("a", 3.0, 10.0, REC),
                    _ins("b", 4.0, 20.0, REC),
                    _ins("c", 5.0, 30.0, REC2)]
    span = 15.0 - 4.0
    kfs = [(4.0, 0.1, 0.2, 0.0), (6.0, 0.1, 0.2, 2.5),
           (7.0, 0.3, 0.4, 2.5), (10.0, 0.3, 0.4, 2.5),
           (12.0, 0.5, 0.6, 2.5), (15.0, 0.5, 0.6, 0.0)]
    e["effects"] = {"zooms": [{
        "id": "zp1", "start": 4.0, "end": 15.0, "strength": 2.5,
        "mode": "path", "ease": "cubic_in_out",
        "path": [{"f": round((t - 4.0) / span, 4), "cx": cx, "cy": cy,
                  "s": s} for t, cx, cy, s in kfs]}]}
    return e


def test_meme_inserted_mid_move_keeps_every_beat_on_its_scene():
    e = _base()
    old_tl = Timeline(e["keep"], e["inserts"], [])
    e["inserts"] = [_ins("a", 3.0, 10.0, REC), _ins("b", 4.0, 20.0, REC),
                    _ins("m1", 3.0, None, MEME, kind="image"),
                    _ins("c", 5.0, 30.0, REC2)]
    new_tl = Timeline(e["keep"], e["inserts"], [])
    notes = remap_program_items(e, old_tl, new_tl)
    z = e["effects"]["zooms"][0]
    ts = _abs_times(z)
    # beats on a and b stay; beats on c ride +3s with their scene
    for t in (4.0, 6.0, 7.0, 10.0, 15.0, 18.0):
        assert any(abs(x - t) < 0.06 for x in ts), (t, ts)
    assert z["start"] == 4.0 and z["end"] == 18.0
    # the meme window (10.55-13.55) plays WIDE: an injected s=0 point
    s_e = z["end"] - z["start"]
    inside = [p for p in z["path"]
              if 10.55 <= z["start"] + p["f"] * s_e <= 13.51]
    assert inside and all(float(p.get("s") or 0) == 0.0 for p in inside)
    assert any("plays WIDE" in n for n in notes)
    validate_edl(e, SRC)


def test_scene_move_drags_its_keyframes_and_reorders_the_trajectory():
    e = _base()
    old_tl = Timeline(e["keep"], e["inserts"], [])
    e["inserts"] = [_ins("c", 5.0, 30.0, REC2),      # c now plays first
                    _ins("a", 3.0, 10.0, REC), _ins("b", 4.0, 20.0, REC)]
    new_tl = Timeline(e["keep"], e["inserts"], [])
    remap_program_items(e, old_tl, new_tl)
    z = e["effects"]["zooms"][0]
    ts = _abs_times(z)
    # c's beats (were 12, 15 at local 1.45/4.45) now at 5.0 / 8.0;
    # a's at 9.0 / 11.0; b's at 12.0 / 15.0 — sorted into viewer order
    for t in (5.0, 8.0, 9.0, 11.0, 12.0, 15.0):
        assert any(abs(x - t) < 0.06 for x in ts), (t, ts)
    assert z["start"] == 5.0 and z["end"] == 15.0
    validate_edl(e, SRC)


def test_rate_change_scales_keyframes_inside_that_scene():
    e = _base()
    old_tl = Timeline(e["keep"], e["inserts"], [])
    e["inserts"] = [_ins("a", 3.0, 10.0, REC),
                    _ins("b", 2.0, 20.0, REC, rate=2.0),   # same clip, 2x
                    _ins("c", 5.0, 30.0, REC2)]
    new_tl = Timeline(e["keep"], e["inserts"], [])
    remap_program_items(e, old_tl, new_tl)
    ts = _abs_times(e["effects"]["zooms"][0])
    # b's beats were clip 20.45 / 23.45 -> now 6.55+0.225 / 6.55+1.725
    for t in (4.0, 6.0, 6.78, 8.28, 10.0, 13.0):
        assert any(abs(x - t) < 0.06 for x in ts), (t, ts)
    validate_edl(e, SRC)


def test_deleted_scene_drops_its_beats_and_the_rest_hold_position():
    e = _base()
    old_tl = Timeline(e["keep"], e["inserts"], [])
    e["inserts"] = [_ins("a", 3.0, 10.0, REC), _ins("c", 5.0, 30.0, REC2)]
    new_tl = Timeline(e["keep"], e["inserts"], [])
    notes = remap_program_items(e, old_tl, new_tl)
    z = e["effects"]["zooms"][0]
    ts = _abs_times(z)
    for t in (4.0, 6.0, 8.0, 11.0):        # c's beats ride -4s
        assert any(abs(x - t) < 0.06 for x in ts), (t, ts)
    assert not any(6.9 < x < 7.9 for x in ts)          # b's beats are gone
    assert any("dropped" in n and "zp1" in n for n in notes)
    validate_edl(e, SRC)


def test_split_survivor_is_refound_by_clip_time():
    """cut_output_range splits an insert and the tail gets a NEW id — a
    keyframe on the tail's footage must re-find it by asset + clip second."""
    e = _base()
    old_tl = Timeline(e["keep"], e["inserts"], [])
    # b (clip 20-24) split at clip 22: head keeps id 'b' (2s), tail 'b2'
    e["inserts"] = [_ins("a", 3.0, 10.0, REC), _ins("b", 2.0, 20.0, REC),
                    _ins("b2", 2.0, 22.0, REC), _ins("c", 5.0, 30.0, REC2)]
    new_tl = Timeline(e["keep"], e["inserts"], [])
    remap_program_items(e, old_tl, new_tl)
    ts = _abs_times(e["effects"]["zooms"][0])
    # beat at old 10.0 (clip 23.45) lands in b2: 8.55 + 1.45 = 10.0
    for t in (4.0, 6.0, 7.0, 10.0, 12.0, 15.0):
        assert any(abs(x - t) < 0.06 for x in ts), (t, ts)
    validate_edl(e, SRC)


def test_separated_cut_pair_plays_the_new_scene_wide_not_held():
    """A cut-pair (hold P, re-aim Q 0.1s later at the cut) separated by a
    NEW scene: the wide passage wins — P holds to the cut, the new scene
    plays wide, Q lands at its own scene. The close-up must NOT be held
    across pixels it was never aimed at."""
    e = _base()
    span = 15.0 - 4.0
    kfs = [(4.0, 0.1, 0.2, 0.0), (6.5, 0.1, 0.2, 2.5),
           (6.6, 0.3, 0.4, 2.5), (10.0, 0.3, 0.4, 2.5),
           (15.0, 0.3, 0.4, 0.0)]
    e["effects"]["zooms"][0]["path"] = [
        {"f": round((t - 4.0) / span, 4), "cx": cx, "cy": cy, "s": s}
        for t, cx, cy, s in kfs]
    old_tl = Timeline(e["keep"], e["inserts"], [])
    e["inserts"] = [_ins("a", 3.0, 10.0, REC),
                    _ins("m1", 3.0, None, MEME, kind="image"),
                    _ins("b", 4.0, 20.0, REC), _ins("c", 5.0, 30.0, REC2)]
    new_tl = Timeline(e["keep"], e["inserts"], [])
    notes = remap_program_items(e, old_tl, new_tl)
    z = e["effects"]["zooms"][0]
    s0, e0 = z["start"], z["end"]
    pts = [(round(s0 + p["f"] * (e0 - s0), 2), p) for p in z["path"]]
    # the meme window is 6.55-9.55: whatever plays there is at strength 0
    inside = [p for t, p in pts if 6.56 <= t <= 9.52]
    assert inside and all(float(p.get("s") or 0) == 0.0 for p in inside)
    # and the pair's second half landed with its own scene (b at 9.55+)
    assert any(abs(t - 9.65) < 0.06 and abs(p["cx"] - 0.3) < 0.01
               for t, p in pts), pts
    assert any("plays WIDE" in n for n in notes)
    validate_edl(e, SRC)


def test_reordered_pair_keeps_its_jump_via_a_hold():
    """The same tight pair separated NOT by new content (its own scene grew
    longer): the jump stays a jump — the first aim holds to just before the
    second."""
    e = _base()
    span = 15.0 - 4.0
    kfs = [(4.0, 0.1, 0.2, 0.0), (6.5, 0.1, 0.2, 2.5),
           (6.6, 0.3, 0.4, 2.5), (10.0, 0.3, 0.4, 2.5),
           (15.0, 0.3, 0.4, 0.0)]
    e["effects"]["zooms"][0]["path"] = [
        {"f": round((t - 4.0) / span, 4), "cx": cx, "cy": cy, "s": s}
        for t, cx, cy, s in kfs]
    old_tl = Timeline(e["keep"], e["inserts"], [])
    e["inserts"] = [_ins("a", 6.0, 10.0, REC),       # a grew 3s longer
                    _ins("b", 4.0, 20.0, REC), _ins("c", 5.0, 30.0, REC2)]
    new_tl = Timeline(e["keep"], e["inserts"], [])
    remap_program_items(e, old_tl, new_tl)
    z = e["effects"]["zooms"][0]
    s0, e0 = z["start"], z["end"]
    pts = [(round(s0 + p["f"] * (e0 - s0), 2), p) for p in z["path"]]
    # pair halves: 6.5 stays (inside a), 6.6 -> 9.6 (b shifted +3);
    # an injected hold of the FIRST aim sits just before the second half
    assert any(abs(t - 9.45) < 0.06 and abs(p["cx"] - 0.1) < 0.01
               and float(p.get("s") or 0) == 2.5 for t, p in pts), pts
    validate_edl(e, SRC)


def test_no_change_is_a_no_op_with_no_notes():
    e = _base()
    old_tl = Timeline(e["keep"], e["inserts"], [])
    new_tl = Timeline(e["keep"], [dict(i) for i in e["inserts"]], [])
    before = [dict(p) for p in e["effects"]["zooms"][0]["path"]]
    notes = remap_program_items(e, old_tl, new_tl)
    z = e["effects"]["zooms"][0]
    assert z["start"] == 4.0 and z["end"] == 15.0
    assert z["path"] == before
    assert not any("zp1" in n for n in notes)


def test_plain_zoom_inside_a_rerated_scene_scales_with_it():
    """The round-75 follow needs ONE common MOVE delta — a re-rated scene
    does not move (delta 0) yet every moment inside it lands at a new
    output second. Per-endpoint mapping through the clip catches it."""
    e = _base()
    e["effects"] = {"zooms": [{"id": "zm1", "start": 11.0, "end": 14.0,
                               "strength": 1.2, "mode": "ease",
                               "cx": 0.2, "cy": 0.5}]}
    old_tl = Timeline(e["keep"], e["inserts"], [])
    e["inserts"] = [_ins("a", 3.0, 10.0, REC), _ins("b", 4.0, 20.0, REC),
                    _ins("c", 2.5, 30.0, REC2, rate=2.0)]   # same clip @2x
    new_tl = Timeline(e["keep"], e["inserts"], [])
    notes = remap_program_items(e, old_tl, new_tl)
    z = e["effects"]["zooms"][0]
    # was clip 30.45-33.45 of c -> at 2x that is 10.78-12.28 output
    assert abs(z["start"] - 10.78) < 0.02 and abs(z["end"] - 12.28) < 0.02
    assert any("stays on the same spliced footage" in n for n in notes)
    validate_edl(e, SRC)
