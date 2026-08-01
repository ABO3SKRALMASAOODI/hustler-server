"""Round 74 — the transition stays inside its scene, and edits stop fighting.

Same launch-video session as round 72, three real failures:

  * "split the last scene and delete the last chunk" came back as a raw
    `effects.zooms[0]: end 9.5 exceeds the limit 8.88s` — an insert-anchored
    zoom is kept VERBATIM by the remap, so a shortened program fails
    validation and the whole unrelated cut is refused. (Stylize has clamped
    this exact case since round 71; zooms never did.)
  * the takeover glued its arrival to the handoff clip but kept its length,
    so when the device shot shrank from 2.42s to 1.88s the push started
    0.54s INSIDE THE PREVIOUS SCENE — and its pinned footage kept an offset
    measured against the clip's old start, leaving a 0.7s jump inside the
    one join the effect exists to hide.
  * "change the transition" had no edit path: remove + re-add re-measured a
    good 0.30-wide laptop trapezoid into a 0.10-wide bright patch at 0.57
    confidence. add_screen_takeover at the SAME arrival now REPLACES —
    parameters inherited, accepted pin corners reused.

Run:  python -m pytest tests/test_takeover_anchor.py -q     (from worker/)
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_tools                                            # noqa: E402
import remote                                                 # noqa: E402
from schemas import default_edl, validate_edl                 # noqa: E402
from timeline import Timeline, remap_program_items            # noqa: E402

SRC = 354.6
KEEP = [[113.7, 117.25]]                    # scene 1: 0-3.55
LAPTOP = "clips/1/laptop.mov"
REC = "clips/1/rec.mov"
QUAD = [0.177, 0.561, 0.46, 0.544, 0.202, 0.931, 0.473, 0.852]


def _ins(id, dur, src, asset):
    return {"id": id, "asset_key": asset, "kind": "video",
            "at_output_s": 3.55, "duration_s": dur, "source_start_s": src}


def _tk(start, dur, src, corner_path=None, land=None, ease="smooth"):
    scr = {"corners": list(QUAD), "push": 1.0, "ease": ease,
           "corners_source": "read"}
    if corner_path is not None:
        scr["corner_path"] = corner_path
    if land is not None:
        scr["land"] = land
    return {"id": "tk1", "asset_key": REC, "kind": "video", "start": start,
            "duration_s": dur, "x": 0.5, "y": 0.5, "scale": 1.0,
            "source_start_s": src, "screen": scr}


# ------------------------------------------------ the blocked delete ----

def test_deleting_the_tail_is_no_longer_blocked_by_an_insert_zoom():
    """The exact prod sequence: zoom 7.5-9.5 inside the inserts, tail chunk
    deleted, program shrinks to 8.88s — the write must validate, with the
    zoom clamped instead of the cut refused."""
    e = default_edl(SRC)
    e["keep"] = [list(k) for k in KEEP]
    all_ins = [_ins("ins1", 1.88, 12.41, LAPTOP),
               _ins("ins2", 3.45, 8.78, REC),
               _ins("ins3", 2.55, 12.23, REC)]
    e["inserts"] = [dict(i) for i in all_ins]
    e["effects"] = {"zooms": [{"id": "zm1", "start": 7.5, "end": 9.5,
                               "strength": 1.2, "mode": "ease",
                               "cx": 0.0, "cy": 1.0}]}
    old_tl = Timeline(e["keep"], e["inserts"], [])
    e["inserts"] = [dict(i) for i in all_ins[:2]]          # delete ins3
    new_tl = Timeline(e["keep"], e["inserts"], [])
    notes = remap_program_items(e, old_tl, new_tl)
    z = e["effects"]["zooms"][0]
    assert z["end"] == 8.88 and z["start"] == 7.5
    validate_edl(e, SRC)                # this raising WAS the user's error
    assert any("now ends at 8.88" in n for n in notes)


def test_a_zoom_entirely_past_the_shortened_edit_is_dropped():
    e = default_edl(SRC)
    e["keep"] = [list(k) for k in KEEP]
    all_ins = [_ins("ins1", 1.88, 12.41, LAPTOP),
               _ins("ins2", 3.45, 8.78, REC),
               _ins("ins3", 2.55, 12.23, REC)]
    e["inserts"] = [dict(i) for i in all_ins]
    e["effects"] = {"zooms": [{"id": "zm1", "start": 9.0, "end": 9.6,
                               "strength": 0.5, "cx": 0.5, "cy": 0.5}]}
    old_tl = Timeline(e["keep"], e["inserts"], [])
    e["inserts"] = [dict(i) for i in all_ins[:2]]
    new_tl = Timeline(e["keep"], e["inserts"], [])
    notes = remap_program_items(e, old_tl, new_tl)
    assert e["effects"]["zooms"] == []
    assert any("was removed" in n and "zoom" in n for n in notes)
    validate_edl(e, SRC)


# ------------------------------------------ the takeover re-anchor ----

def test_takeover_shortens_into_its_shrunken_scene():
    """Device shot shrinks 2.42s -> 1.88s: the push must shorten to stay
    inside it (not bleed into scene 1), shift its tracked path, and keep the
    handoff frame-continuous."""
    e = default_edl(SRC)
    e["keep"] = [list(k) for k in KEEP]
    old_ins = [_ins("ins1", 2.42, 12.41, LAPTOP),      # window 3.55-5.97
               _ins("ins2", 6.0, 8.4, REC)]            # window 5.97-11.97
    path = [[0.0] + list(QUAD), [1.21] + list(QUAD), [2.42] + list(QUAD)]
    e["inserts"] = [dict(i) for i in old_ins]
    e["overlays"] = [_tk(3.55, 2.42, 8.4 - 2.42, corner_path=path)]
    old_tl = Timeline(e["keep"], e["inserts"], [])
    e["inserts"] = [dict(old_ins[0], duration_s=1.88),  # window 3.55-5.43
                    dict(old_ins[1])]
    new_tl = Timeline(e["keep"], e["inserts"], [])
    notes = remap_program_items(e, old_tl, new_tl)
    ov = e["overlays"][0]
    assert abs(ov["duration_s"] - 1.83) < 0.02          # 5.43-3.55-0.05
    assert abs(ov["start"] - 3.6) < 0.02
    # frame-continuity: pin ends where the clip begins
    assert abs(ov["source_start_s"] - (8.4 - ov["duration_s"])) < 0.02
    # tracked knots shifted by the cut-off front; the t=0 knot fell away
    kp = ov["screen"]["corner_path"]
    assert len(kp) == 2
    assert abs(kp[0][0] - (1.21 - 0.59)) < 0.02
    assert abs(kp[1][0] - 1.83) < 0.02
    assert any("stays inside" in n for n in notes)
    validate_edl(e, SRC)


def test_takeover_source_realigns_when_the_clip_start_moved():
    """set_insert_window changed the handoff's clip_start (8.08 -> 8.78) and
    the pin kept an offset against the old start — the real EDL carried
    source 5.66 against a clip at 8.78, a 0.7s jump at the cut. The remap
    re-derives the offset every pass, even when no window moved."""
    e = default_edl(SRC)
    e["keep"] = [list(k) for k in KEEP]
    ins = [_ins("ins1", 1.88, 12.41, LAPTOP),
           _ins("ins2", 6.0, 8.78, REC)]                # moved clip start
    e["inserts"] = [dict(i) for i in ins]
    e["overlays"] = [_tk(3.63, 1.8, 5.66)]              # stale offset
    tl = Timeline(e["keep"], e["inserts"], [])
    notes = remap_program_items(e, tl, tl)
    ov = e["overlays"][0]
    assert abs(ov["source_start_s"] - (8.78 - 1.8)) < 0.02
    assert ov["duration_s"] == 1.8 and ov["start"] == 3.63
    assert any("re-aligned" in n for n in notes)


def test_takeover_dropped_when_its_scene_is_too_short():
    e = default_edl(SRC)
    e["keep"] = [list(k) for k in KEEP]
    old_ins = [_ins("ins1", 1.88, 12.41, LAPTOP),
               _ins("ins2", 6.0, 8.4, REC)]
    e["inserts"] = [dict(i) for i in old_ins]
    e["overlays"] = [_tk(3.55, 1.88, 8.4 - 1.88)]
    old_tl = Timeline(e["keep"], e["inserts"], [])
    e["inserts"] = [dict(old_ins[0], duration_s=0.3),   # device shot gutted
                    dict(old_ins[1])]
    new_tl = Timeline(e["keep"], e["inserts"], [])
    notes = remap_program_items(e, old_tl, new_tl)
    assert e["overlays"] == []
    assert any("too short for the push" in n for n in notes)


def test_takeover_untouched_when_nothing_changed():
    """The remap runs on every insert write — an aligned takeover must pass
    through byte-identical, with no notes."""
    e = default_edl(SRC)
    e["keep"] = [list(k) for k in KEEP]
    ins = [_ins("ins1", 1.88, 12.41, LAPTOP),
           _ins("ins2", 6.0, 8.78, REC)]
    e["inserts"] = [dict(i) for i in ins]
    e["overlays"] = [_tk(3.63, 1.8, 8.78 - 1.8)]
    before = dict(e["overlays"][0])
    tl = Timeline(e["keep"], e["inserts"], [])
    notes = remap_program_items(e, tl, tl)
    assert e["overlays"][0] == before
    assert not any("takeover" in n for n in notes)


# -------------------------------------- the 16s-shift class (round 75) ----

def test_zoom_follows_its_scene_when_a_clip_lands_in_front():
    """The v327 incident: a choreographed zoom stayed at its absolute
    seconds while a 16s intro spliced in FRONT shifted every scene — the
    move played over a different recording entirely. Insert-anchored spans
    now follow their scenes when they all moved together; spans touching
    kept footage stay put."""
    e = default_edl(SRC)
    e["keep"] = [list(k) for k in KEEP]
    old_ins = [_ins("ins1", 1.88, 12.41, LAPTOP),
               _ins("ins2", 3.45, 8.78, REC),
               _ins("ins4", 17.26, 30.7, "clips/1/rec2.mov")]
    e["inserts"] = [dict(i) for i in old_ins]
    e["effects"] = {"zooms": [
        {"id": "zp1", "start": 6.6, "end": 8.8, "strength": 1.2,
         "mode": "ease", "cx": 0.0, "cy": 1.0},
        {"id": "zm0", "start": 2.0, "end": 7.0, "strength": 0.3,
         "mode": "ease", "cx": 0.5, "cy": 0.5}]}
    old_tl = Timeline(e["keep"], e["inserts"], [])
    intro = {"id": "ins9", "asset_key": "clips/1/intro.mov", "kind": "video",
             "at_output_s": 3.55, "duration_s": 16.0}
    e["inserts"] = [intro] + [dict(i) for i in old_ins]
    new_tl = Timeline(e["keep"], e["inserts"], [])
    notes = remap_program_items(e, old_tl, new_tl)
    z = e["effects"]["zooms"][0]
    assert z["start"] == 22.6 and z["end"] == 24.8
    assert any("stays on the same spliced footage" in n for n in notes)
    z0 = e["effects"]["zooms"][1]
    assert z0["start"] == 2.0 and z0["end"] == 7.0   # touches footage: stays
    validate_edl(e, SRC)


def test_takeover_survives_a_16s_shift():
    """The same incident killed the takeover: the near-match caps at 2.5s,
    read a 16s arrival shift as 'clip gone', and silently dropped a tuned
    transition. The handoff is now found by the OLD windows (id-stable)."""
    e = default_edl(SRC)
    e["keep"] = [list(k) for k in KEEP]
    old_ins = [_ins("ins1", 1.88, 12.41, LAPTOP),
               _ins("ins2", 6.0, 8.78, REC)]
    e["inserts"] = [dict(i) for i in old_ins]
    e["overlays"] = [_tk(3.63, 1.8, 8.78 - 1.8)]
    old_tl = Timeline(e["keep"], e["inserts"], [])
    intro = {"id": "ins9", "asset_key": "clips/1/intro.mov", "kind": "video",
             "at_output_s": 3.55, "duration_s": 16.0}
    e["inserts"] = [intro] + [dict(i) for i in old_ins]
    new_tl = Timeline(e["keep"], e["inserts"], [])
    notes = remap_program_items(e, old_tl, new_tl)
    ov = e["overlays"][0]
    assert abs(ov["start"] - 19.63) < 0.02
    assert ov["duration_s"] == 1.8
    assert abs(ov["source_start_s"] - 6.98) < 0.02
    assert any("staying joined to its clip" in n for n in notes)
    validate_edl(e, SRC)


# ------------------------------------------------- upsert / replace ----

class _DB:
    def __init__(self, assets):
        self.assets = assets

    def run(self, fn, *a):
        name = getattr(fn, "__name__", "")
        if name == "asset_by_key":
            return self.assets.get(a[1])
        if name == "assets_by_kinds":
            return list(self.assets.values())
        return None


class _Ctx:
    def __init__(self, edl, assets, duration=SRC):
        self.project_id = 1
        self.duration = duration
        self.index = {"video": {"duration": duration, "width": 3840,
                                "height": 2160, "fps": 30},
                      "words": [], "sentences": []}
        self.has_main_video = True
        self.workdir = tempfile.mkdtemp(prefix="tkup_")
        self.db = _DB(assets)
        self.written = []
        self._edl = validate_edl(edl, duration).model_dump()

    def latest_edl(self):
        return {"version": len(self.written) + 1, "json": self._edl}

    def write_edl(self, edl, desc):
        self._edl = validate_edl(dict(edl), self.duration).model_dump()
        self.written.append(desc)
        return f"EDL v{len(self.written)}: {desc}"


ASSETS = {
    LAPTOP: {"id": 1, "kind": "video_clip", "storage_key": LAPTOP,
             "duration_s": 23.9, "meta": {"filename": "laptop.mov"}},
    REC: {"id": 2, "kind": "video_clip", "storage_key": REC,
          "duration_s": 14.8, "meta": {"filename": "rec.mov"}},
}


def _studio_edl(land=None, ease="accelerate"):
    e = default_edl(SRC)
    e["keep"] = [list(k) for k in KEEP]
    e["inserts"] = [_ins("ins1", 1.88, 12.41, LAPTOP),
                    _ins("ins2", 6.0, 8.78, REC)]
    e["overlays"] = [_tk(3.63, 1.8, 8.78 - 1.8, land=land, ease=ease)]
    return e


def test_readd_at_same_arrival_replaces_and_reuses_the_pin(monkeypatch):
    monkeypatch.setattr(remote, "track_available", lambda: False)
    ctx = _Ctx(_studio_edl(), ASSETS)
    res = agent_tools.add_screen_takeover(ctx, REC, at_output_s=5.43,
                                          settle=False)
    assert res.startswith("EDL v"), res
    ovs = ctx.latest_edl()["json"]["overlays"]
    assert len(ovs) == 1                        # replaced, never stacked
    ov = ovs[0]
    assert ov["screen"]["corners"] == QUAD      # accepted pin KEPT
    assert ov["screen"]["land"] is False        # the flat landing
    assert ov["screen"]["ease"] == "accelerate"  # inherited, not reset
    assert ov["duration_s"] == 1.8              # inherited
    assert abs(ov["source_start_s"] - (8.78 - 1.8)) < 0.02
    assert "REPLACED" in res and "KEPT" in res
    assert "DEAD FLAT" in res                   # the reply says what it did
    assert "brief punch past full frame" not in res


def test_move_insert_reorders_and_anchored_zooms_follow():
    """Round 75: 'move the uploaded video between any other split' had no
    tool. move_insert reorders in place, and a zoom choreographed on the
    moved scene follows it through the shared remap."""
    e = default_edl(SRC)
    e["keep"] = [list(k) for k in KEEP]
    e["inserts"] = [
        {"id": "a", "asset_key": "clips/1/a.mov", "kind": "video",
         "at_output_s": 3.55, "duration_s": 2.0},
        {"id": "b", "asset_key": "clips/1/b.mov", "kind": "video",
         "at_output_s": 3.55, "duration_s": 3.0},
        {"id": "c", "asset_key": "clips/1/c.mov", "kind": "video",
         "at_output_s": 3.55, "duration_s": 4.0}]
    e["effects"] = {"zooms": [{"id": "zm1", "start": 9.0, "end": 12.0,
                               "strength": 0.5, "mode": "ease",
                               "cx": 0.2, "cy": 0.5}]}
    ctx = _Ctx(e, ASSETS)
    res = agent_tools.move_insert(ctx, "c")          # first at the boundary
    assert res.startswith("EDL v"), res
    j = ctx.latest_edl()["json"]
    assert [i["id"] for i in j["inserts"]] == ["c", "a", "b"]
    z = j["effects"]["zooms"][0]
    assert z["start"] == 4.0 and z["end"] == 7.0     # followed c back 5s
    assert "3.55-7.55s" in res
    res2 = agent_tools.move_insert(ctx, "c", after_id="b")
    assert res2.startswith("EDL v"), res2
    j2 = ctx.latest_edl()["json"]
    assert [i["id"] for i in j2["inserts"]] == ["a", "b", "c"]
    z2 = j2["effects"]["zooms"][0]
    assert z2["start"] == 9.0 and z2["end"] == 12.0  # followed it back
    assert "REJECTED" in agent_tools.move_insert(ctx, "zz")
    assert "REJECTED" in agent_tools.move_insert(ctx, "c", after_id="c")
    assert "REJECTED" in agent_tools.move_insert(ctx, "c", after_id="zz")


def test_fresh_add_with_corners_is_unchanged_by_the_none_defaults(
        monkeypatch):
    monkeypatch.setattr(remote, "track_available", lambda: False)
    e = _studio_edl()
    e["overlays"] = []
    ctx = _Ctx(e, ASSETS)
    res = agent_tools.add_screen_takeover(ctx, REC, at_output_s=5.43,
                                          corners=list(QUAD))
    assert res.startswith("EDL v"), res
    ov = ctx.latest_edl()["json"]["overlays"][0]
    assert ov["duration_s"] == 1.2              # the documented default
    assert ov["screen"]["ease"] == "smooth"
    assert ov["screen"].get("land") is None     # settle defaults ON
    assert "REPLACED" not in res
    assert "brief punch past full frame" in res
