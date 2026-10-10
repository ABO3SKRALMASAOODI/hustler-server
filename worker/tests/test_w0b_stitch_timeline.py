"""Track 0B: EDL.fx in stitched previews and through re-cuts.

* stitch: picture-stage and junction items are video-local windows; a
  music_bus item is sound (structural: the stitched file keeps the previous
  preview's audio); window_edl rebases items frame-exactly.
* timeline.remap_program_items: picture items are content-anchored (a
  1-frame device stays one frame), junction items follow their cut,
  music_bus items follow their music item; every move is disclosed.
* AT-0B-3 (probe child, lossless): editing one fx item re-encodes only its
  window; the rest of the preview is the previous file's own frames.
"""

import copy
import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import stitch                                                # noqa: E402
import timeline as timeline_mod                              # noqa: E402
import w0b_probe                                             # noqa: E402
from timeline import Timeline                                # noqa: E402


def _fx(fid, stage, **kw):
    it = {"id": fid, "type": "fx_probe", "stage": stage}
    it.update(kw)
    return it


BASE = {"keep": [[0.0, 8.0], [10.0, 18.0]],
        "music": [{"id": "m1", "storage_key": "k", "start": 0.0,
                   "end": 16.0}]}
A = _fx("a", "world", op="negate", start=2.0, end=2.04)
B = _fx("b", "over_type", op="negate", start=12.0, end=12.5)
J = _fx("j", "junction", at=8.0, frames=4)
BUS = _fx("bus", "music_bus", music_id="m1", op="gain", gain_db=-6)


def _tl(edl):
    return Timeline(edl["keep"], edl.get("inserts") or [],
                    edl.get("speed") or [])


# ── stitch ───────────────────────────────────────────────────────────────

def test_fx_items_are_changeable_except_the_music_bus():
    s, c = stitch._strip(dict(BASE, fx=[A, J, BUS]))
    assert [i["id"] for i in c["fx"]] == ["a", "j"]
    assert [i["id"] for i in s["fx"]] == ["bus"]
    s0, c0 = stitch._strip(dict(BASE, fx=[]))
    assert "fx" not in s0 and c0["fx"] is None
    assert stitch._strip(BASE)[0] == s0


def test_changing_one_fx_item_opens_only_its_window():
    prev, new = dict(BASE, fx=[A, B]), dict(BASE, fx=[A, dict(B, end=12.7)])
    windows, why = stitch.plan(prev, new, _tl(prev), _tl(new), 20.0, 16.0)
    assert why is None
    assert windows == [[11.5, 13.0], [11.5, 13.2]]
    # an added junction item opens its cut's neighbourhood
    w2, _ = stitch.plan(prev, dict(prev, fx=[A, B, J]), _tl(prev), _tl(prev),
                        20.0, 16.0)
    assert w2 == [[6.5, 9.5]]
    # a music_bus change is a change of SOUND: no stitch
    w3, why3 = stitch.plan(prev, dict(prev, fx=[A, B, BUS]), _tl(prev),
                           _tl(prev), 20.0, 16.0)
    assert w3 is None and "structural" in why3


def test_fx_items_are_contained_by_stitch_windows():
    spans = stitch._item_windows(dict(BASE, fx=[A, J, BUS]), _tl(BASE),
                                 20.0)
    assert (2.0, 2.04, "fx") in spans
    assert (7.0, 9.0, "fx") in spans
    assert not any(k == "fx" and a == 0.0 for a, _b, k in spans)


@pytest.mark.parametrize("fps", [23.976, 25.0, 29.97, 59.94])
def test_window_edl_rebases_fx_frame_exactly(fps):
    import fxprims
    f3 = round(fps, 3)
    w0 = 48 / f3                    # a keyframe of the previous preview
    one = _fx("x", "pre_captions", op="negate",
              start=round(130 / f3, 2), end=round(131 / f3, 2))
    edl = dict(BASE, fx=[one, J, BUS, A])
    win = stitch.window_edl(edl, _tl(edl), w0, w0 + 5.0, fps=fps)
    items = {i["id"]: i for i in win["fx"]}
    x = items["x"]
    assert fxprims.frame_span(x["start"], x["end"], fps) == (82, 83)
    assert "j" not in items                       # its cut is outside
    assert "bus" not in items                     # no audio in a piece
    assert items["a"]["end"] <= 5.0 and items["a"]["start"] >= 0.0
    keep_audio = stitch.window_edl(edl, _tl(edl), 6.0, 10.0, keep_audio=True,
                                   fps=fps)
    ids = {i["id"] for i in keep_audio["fx"]}
    assert {"j", "bus"} <= ids
    assert {i["id"]: i for i in keep_audio["fx"]}["j"]["at"] == 2.0


def test_timeline_mode_ignores_bus_items_and_carves_fx_windows():
    prev = dict(BASE, fx=[A, BUS])
    new = dict(prev, keep=[[0.0, 8.0], [11.0, 18.0]],
               fx=[A, dict(BUS, gain_db=-3)])
    assert stitch._strip_timeline(prev) == stitch._strip_timeline(new)
    assert stitch._out_spans_of_item("fx", J, None, 16.0) == [(7.0, 9.0)]


# ── timeline remap ───────────────────────────────────────────────────────

def _remap(edl, new_keep):
    old_tl = _tl(edl)
    edl = copy.deepcopy(edl)
    edl["keep"] = new_keep
    notes = timeline_mod.remap_program_items(edl, old_tl, _tl(edl))
    return edl, notes


def test_picture_items_follow_their_footage_and_keep_their_length():
    edl = dict(BASE, fx=[dict(B, start=12.0, end=12.04)])
    out, notes = _remap(edl, [[1.0, 8.0], [10.0, 18.0]])   # trim 1 s head
    b = out["fx"][0]
    assert (b["start"], b["end"]) == (11.0, 11.04)
    assert any("moved to 11.0-11.04s" in n for n in notes)
    out2, notes2 = _remap(edl, [[0.0, 8.0]])               # footage cut away
    assert out2["fx"] == [] and any("removed" in n for n in notes2)


def test_junction_items_follow_their_cut():
    edl = dict(BASE, fx=[J])
    out, notes = _remap(edl, [[2.0, 8.0], [10.0, 18.0]])
    assert out["fx"][0]["at"] == 6.0
    assert any("same cut" in n for n in notes)
    out2, notes2 = _remap(edl, [[0.0, 18.0]])               # cut is gone
    assert out2["fx"] == [] and any("cut it decorated" in n for n in notes2)


def test_music_bus_items_follow_their_music_item():
    edl = dict(BASE, fx=[BUS])
    out, _ = _remap(edl, [[0.0, 8.0], [10.0, 14.0]])
    assert out["fx"] == [BUS]
    edl2 = dict(edl, music=[])
    out2, notes2 = _remap(edl2, [[0.0, 8.0], [10.0, 14.0]])
    assert out2["fx"] == [] and any("music item" in n for n in notes2)


def test_no_fx_no_change():
    out, notes = _remap(BASE, [[1.0, 8.0], [10.0, 18.0]])
    assert "fx" not in out
    assert not any("fx" in n for n in notes)


# ── AT-0B-3: editing one fx item re-encodes only its window ─────────────

@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                    reason="needs ffmpeg")
def test_at_0b_3_one_fx_edit_reencodes_only_its_window(tmp_path):
    src = w0b_probe.make_source(str(tmp_path / "src.mp4"), w=320, h=180,
                                rate="30", dur=20.0)
    idx = {"video": {"duration": 20.0, "width": 320, "height": 180,
                     "fps": 30.0},
           "words": [], "sentences": [], "silences": []}
    a = _fx("a", "world", op="negate_window", start=2.0, end=2.5)
    b = _fx("b", "over_type", op="negate", start=12.0, end=12.5)
    v1 = {"keep": [[0.0, 8.0], [10.0, 18.0]], "fx": [a, b]}
    v2 = {"keep": [[0.0, 8.0], [10.0, 18.0]],
          "fx": [a, dict(b, start=12.2, end=12.9)]}
    res = w0b_probe.run([
        {"name": "v1", "src": src, "idx": idx, "edl": v1},
        {"name": "v2", "src": src, "idx": idx, "edl": v2},
        {"name": "s2", "src": src, "idx": idx, "edl": v2,
         "stitch": {"prev_edl": v1, "prev_out": "v1"}},
    ], str(tmp_path / "run"))
    r1, r2, s2 = res["v1"], res["v2"], res["s2"]
    assert r1["error"] is None and r2["error"] is None and s2["error"] is None
    assert s2["duration"] is not None, "the stitch fell back to a full render"
    windows = s2["windows"]
    assert len(windows) == 1
    w0, w1 = windows[0]
    # the window holds the edited item and not the other one
    assert w0 <= 12.0 and w1 >= 12.9 and w0 > 2.5
    covered = (w1 - w0) / 16.0
    assert covered < 0.4
    f = 30.0
    n = len(r2["frames"])
    assert len(s2["frames"]) == n
    inside = range(int(round(w0 * f)), int(round(w1 * f)))
    outside = [i for i in range(n) if i not in inside]
    # outside the window: the previous preview's own frames, bit for bit
    assert all(s2["frames"][i] == r1["frames"][i] for i in outside)
    # inside: what a full render of the new EDL draws (lossless)
    assert all(s2["frames"][i] == r2["frames"][i] for i in inside)
    # and the two full renders really differ only around item b
    diff = [i for i in range(n) if r1["frames"][i] != r2["frames"][i]]
    assert diff and all(int(round(12.0 * f)) <= i < int(round(12.9 * f))
                        for i in diff)
