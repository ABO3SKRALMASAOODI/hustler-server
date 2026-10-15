"""Wave 3 independent review (w3r): regressions for what the review found.

  * a stale client (apply_edit_batch, no world verb) gets its world items
    measured from their first render: world_layer.requests / warm launch
    exactly the keys the readers read; previews never wait, finals wait
    within the render's sidecar budget;
  * an anchored graphic reports a device row (full / reduced / substitute),
    so a final that pinned it static is held like any degraded device;
  * the face detector's box is the rung between the face mesh and a static
    placement (ROADMAP 3A degrade ladder);
  * "behind the <object>" sets the word where the object can hide it (the
    verb and the planner), not at the template's default height;
  * the planner proposes the light dissolve only where the last line earns
    it (or the user asked): an ending on every short is a house style;
  * a canvas program hands the world context to its layout pages (slots).

CI-safe: local sidecar store, synthetic clips, no Chromium, no model.
"""

import copy
import json
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
sys.path.insert(0, WORKER)
sys.path.insert(0, HERE)

import attachment                                      # noqa: E402
import fxgraph                                         # noqa: E402
import schemas                                         # noqa: E402
import sidecars                                        # noqa: E402
import world_layer                                     # noqa: E402
import world_planner                                   # noqa: E402
import w3_support as w3s                               # noqa: E402
from test_w3f_agent import BASE, Ctx, WORDS            # noqa: E402
from tools_ext import world_tools as WT                # noqa: E402


@pytest.fixture
def store(tmp_path):
    root = str(tmp_path / "store")
    os.makedirs(root)
    prev = sidecars.use_store(sidecars.LocalStore(root))
    yield root
    sidecars.use_store(prev)


@pytest.fixture(autouse=True)
def _no_executor_check(monkeypatch):
    monkeypatch.setattr(WT, "_executor_refusal", lambda: None)
    monkeypatch.setattr(WT, "_launch_source", lambda ctx: None)


WORLD_EDL = {
    "keep": [[0.0, 5.0]],
    "fx": [{"id": "ob1", "type": "object", "stage": "world", "start": 0.5,
            "end": 3.5, "prompt": {"src_t": 1.0,
                                   "box": [0.4, 0.75, 0.66, 1.0]}},
           {"id": "pl1", "type": "plane", "stage": "world", "start": 0.5,
            "end": 3.5, "src_t": 1.0, "surface": "desk",
            "quad": [[0.2, 0.55], [0.8, 0.55], [0.9, 0.95], [0.1, 0.95]]}],
    "motion": [
        {"id": "m1", "template": "word_slam", "start": 1.0, "end": 2.0,
         "params": {"text": "A"}, "anchor": {"to": "hand.index_tip"}},
        {"id": "m2", "template": "word_slam", "start": 1.0, "end": 2.0,
         "params": {"text": "B"}, "anchor": {"to": "head"}},
        {"id": "m3", "template": "word_slam", "start": 1.0, "end": 2.0,
         "params": {"text": "C"},
         "anchor": {"to": "screen", "occluders": ["object:ob1"]}},
        {"id": "m4", "template": "word_slam", "start": 1.0, "end": 2.0,
         "params": {"text": "D"},
         "anchor": {"to": "plane:pl1", "shadow": True}},
        {"id": "m5", "template": "el_particles", "start": 3.0, "end": 4.0,
         "params": {"kind": "embers", "emit": "edge"}}]}


# ── the stale-client path: render-time measurement ─────────────────────

def test_requests_name_every_sidecar_the_world_items_read():
    src = w3s.source()
    c, e = w3s.ctx(WORLD_EDL, src)
    kinds = {k for k, _r, _p in world_layer.requests(c)}
    assert kinds == {"landmarks.hand", "landmarks.face", "faces.yunet",
                     "objects.sam2", "planes.h", "plate.clean",
                     "matte.person"}
    # a definition is requested exactly as the readers read it
    import world_defs
    reqs = world_layer.requests(c)
    ob = world_defs.request(world_defs.definitions(e)["ob1"], c.tl, 0.0)
    assert ob in reqs


def test_warm_launches_on_renders_and_waits_only_on_finals(monkeypatch):
    src = w3s.source()
    c, _e = w3s.ctx(WORLD_EDL, src)
    launched, waited = [], []

    def ensure(kind, source, ranges, priority="interactive", params=None):
        launched.append((kind, priority))
        return sidecars.Handle(kind=kind, source_sha=source.sha256,
                               chunks=(), priority=priority, params={})
    monkeypatch.setattr(sidecars, "ensure", ensure)
    monkeypatch.setattr(sidecars, "wait",
                        lambda hs, b: waited.append(b) or {})
    monkeypatch.setattr(sidecars, "status", lambda h: "pending")
    # a read-only lane launches nothing
    assert world_layer.warm(c, None, 30.0) == {}
    assert not launched
    # a preview launches and never waits
    res = world_layer.warm(c, src, 0.0)
    assert {k for k, _p in launched} >= {"landmarks.hand", "objects.sam2",
                                          "matte.person"}
    assert all(p == "interactive" for _k, p in launched)
    assert not waited and set(res.values()) == {"pending"}
    # a final waits within its budget
    world_layer.warm(c, src, 30.0)
    assert waited and 0 < waited[0] <= 30.0


def test_renders_warm_the_world_through_the_render_launch(monkeypatch):
    import matte_track
    import renderer
    src = w3s.source()
    _c, e = w3s.ctx(WORLD_EDL, src)
    seen = []
    monkeypatch.setattr(world_layer, "warm",
                        lambda ctx, launch, wait_s: seen.append(
                            (launch, wait_s)) or {})
    tok = matte_track.RENDER_LAUNCH.set(src)
    try:
        from timeline import Timeline
        tl = Timeline(e["keep"])
        assert renderer._world_ctx(e, 640, 360, 25.0, tl, w3s.index_for(src),
                                   True, None, 0.0, "/tmp", None)
        assert renderer._world_ctx(e, 640, 360, 25.0, tl, w3s.index_for(src),
                                   False, None, 0.0, "/tmp", None)
    finally:
        matte_track.RENDER_LAUNCH.reset(tok)
    assert seen[0] == (src, 0.0)
    assert seen[1][0] is src and 0 < seen[1][1] <= sidecars.FINAL_WAIT_MAX_S


# ── device rows: an anchored graphic that degrades holds the final ──────

def _fake_clip(tmp_path):
    return w3s.square_clip(str(tmp_path / "sq.mov"), n=10)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg")
@pytest.mark.parametrize("composed,notes,rung", [
    (False, ["pinned static: face landmarks not measured yet"],
     "substitute"),
    (True, ["the person matte is not measured yet: drawn in front of the "
            "person"], "substitute"),
    (True, [world_layer.YUNET_NOTE], "reduced"),
    (True, [], "full")])
def test_anchored_items_report_their_rung(tmp_path, monkeypatch, composed,
                                          notes, rung):
    src = w3s.source()
    c, e = w3s.ctx(WORLD_EDL, src)
    clip = _fake_clip(tmp_path)
    monkeypatch.setattr(world_layer, "compose", lambda item, cl, ctx: (
        cl if composed else None, list(notes)))
    rows = []
    tok = fxgraph.collect(rows)
    try:
        world_layer.finish([e["motion"][1]], [clip], c)
    finally:
        fxgraph.stop_collecting(tok)
    (row,) = fxgraph.merge_report(rows)
    assert row["id"] == "m2" and row["device"] == "anchor_face"
    assert row["rung"] == rung


def test_a_degraded_anchor_is_held_at_export():
    import quality_verifier
    rows = [{"id": "m2", "device": "anchor_face", "family": "world",
             "stage": "world", "rung": "substitute",
             "reason": "pinned static: face landmarks not measured yet"}]
    assert quality_verifier.unacknowledged_degradations({}, rows) == rows


# ── the face detector's box: the rung under the face mesh ──────────────

def test_a_face_anchor_rides_the_detector_box_while_the_mesh_measures(store):
    src = w3s.source()
    rows = []
    for k in range(40):
        t = round(k * 0.1, 3)
        x = 0.4 + 0.002 * k
        rows.append({"t": t, "faces": [[x, 0.2, x + 0.2, 0.6, 0.95,
                                        x + 0.06, 0.33, x + 0.14, 0.33,
                                        x + 0.1, 0.42, x + 0.07, 0.5,
                                        x + 0.13, 0.5]]})
    w3s.put_chunk(store, src, "faces.yunet", (0.0, 6.0),
                  data={"version": 1, "fps": 10.0, "samples": rows})
    c, _e = w3s.ctx({"keep": [[0.0, 4.0]]}, src)
    for anchor, want in (({"to": "head"}, None),
                         ({"to": "face.eye_l"}, (0.46, 0.33)),
                         ({"to": "face.mouth"}, (0.5, 0.5))):
        series, why = world_layer.series_for(anchor, c, 1.0, 2.0)
        assert series is not None and why == world_layer.YUNET_NOTE
        xy, size, _a, _q, ok = series.at(np.array([1.0]))
        assert ok[0]
        if want:
            assert abs(xy[0][0] - (want[0] + 0.02)) < 0.01, anchor
            assert abs(xy[0][1] - want[1]) < 0.01, anchor
        else:
            # the head: centred on the face, taller than the face box
            assert abs(xy[0][0] - 0.52) < 0.01 and size[0] > 0.4
    # the mesh, once measured, wins (no detector note)
    assert world_layer.rung_of(object(), [world_layer.YUNET_NOTE])[0] == \
        "reduced"


# ── "behind the <object>": the word where the object can hide it ───────

def test_behind_a_wide_object_the_word_rises_from_its_top_edge():
    p = attachment.behind_params([0.2, 0.78, 0.85, 1.0], 1080, 1920)
    assert abs(p["y"] - 0.76) < 0.01 and 0.45 <= p["width"] <= 0.85
    # a tall one (a mic) crosses the word at its middle
    q = attachment.behind_params([0.45, 0.4, 0.55, 0.7], 1080, 1920)
    assert abs(q["y"] - 0.55) < 0.01 and abs(q["x"] - 0.5) < 0.01


def test_place_in_world_behind_sets_the_word_over_the_object():
    ctx = Ctx(BASE)
    res = WT.place_in_world(ctx, "hide it behind the laptop",
                            text="FLYING CARS", t=4.0,
                            box=[0.15, 0.8, 0.85, 1.0])
    assert res.startswith("EDL v3 -> v4"), res
    (m,) = [x for x in ctx.edl["motion"] if x["template"] == "word_slam"]
    assert m["anchor"]["occluders"] == ["object:ob1"]
    # the word's centre just above the laptop's top edge (0.8): its lower
    # part is hidden; without the fix it sat at the template's 0.56
    assert 0.74 <= m["params"]["y"] <= 0.8


def test_the_planner_puts_an_object_word_where_the_object_is(monkeypatch):
    src = w3s.source(w=1920, h=1080, dur=20.0)
    monkeypatch.setattr(world_planner, "_src", lambda index: src)
    monkeypatch.setattr(world_planner, "_valid", lambda e, ops, d: True)
    edl = schemas.validate_edl(dict(BASE, fx=[{
        "id": "ob1", "type": "object", "stage": "world", "start": 1.0,
        "end": 6.0, "label": "mic",
        "prompt": {"src_t": 2.0, "box": [0.45, 0.3, 0.55, 0.6]}}]),
        20.0).model_dump()
    plan = {"words": WORDS, "moments": [
        {"t": 2.0, "word": "FLYING", "kind": "stress", "strength": 0.9}]}
    index = {"video": {"width": 1920, "height": 1080, "fps": 25.0,
                       "duration": 20.0}}
    cands = world_planner.candidates(edl, index, plan)
    (c,) = [x for x in cands if x["device"] == "object_occluder"]
    p = c["op"]["value"]["params"]
    # the mic crosses the word: its vertical middle (0.45), not 0.72
    assert abs(p["y"] - 0.45) < 0.02


# ── the dissolve ending is earned, never a default ─────────────────────

def _ending_plan(last_words):
    words = [{"w": w, "t0": 0.5 + 0.4 * i, "t1": 0.8 + 0.4 * i}
             for i, w in enumerate(("We built it. " + last_words).split())]
    return {"words": words, "moments": [
        {"t": words[-1]["t1"], "kind": "phrase_end"}]}


def test_the_dissolve_needs_a_line_that_earns_it(monkeypatch):
    src = w3s.source(w=1920, h=1080, dur=20.0)
    monkeypatch.setattr(world_planner, "_src", lambda index: src)
    monkeypatch.setattr(world_planner, "_valid", lambda e, ops, d: True)
    monkeypatch.setattr(world_planner, "_matte_ready", lambda *a: True)
    edl = schemas.validate_edl(dict(BASE), 20.0).model_dump()
    index = {"video": {"width": 1920, "height": 1080, "fps": 25.0,
                       "duration": 20.0}}

    def devs(plan, asked=()):
        return [c["device"] for c in world_planner.candidates(
            edl, index, plan, asked=asked)]
    assert "light_dissolve" not in devs(_ending_plan("And then we ship."))
    assert "light_dissolve" in devs(_ending_plan("And then it was gone."))
    assert "light_dissolve" in devs(_ending_plan("We ship."),
                                    asked=("dissolve",))


# ── the canvas lane hands layout pages their world context ─────────────

@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg")
def test_a_canvas_program_gives_layout_pages_the_world(tmp_path,
                                                       monkeypatch):
    import motion_layer
    import renderer
    png = str(tmp_path / "a.png")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=red:s=64x64", "-frames:v", "1", png],
                   check=True)
    edl = {"keep": [], "canvas": {"width": 320, "height": 568, "fps": 25,
                                  "bg_color": "#000000"},
           "inserts": [{"id": "c", "asset_key": "local/a.png",
                        "kind": "image", "at_output_s": 0.0,
                        "duration_s": 1.0}],
           "motion": [{"id": "mz", "template": "mosaic", "start": 0.0,
                       "end": 1.0, "params": {"tiles": ["asset:local/a.png"]}}]}
    seen = []

    def prep(*a, **kw):
        seen.append(kw.get("world"))
        return [], a[7]
    monkeypatch.setattr(motion_layer, "prepare_inputs", prep)
    out = str(tmp_path / "o.mp4")
    renderer.render_edl(edl, None, None, out, str(tmp_path), True,
                        suppress_outro=True,
                        asset_locals={"local/a.png": png})
    assert seen and isinstance(seen[0], world_layer.Ctx)
    assert seen[0].source is None


# ── seating: a word behind an object sits where the object cuts it ─────

def _object_chunks(store, tmp_path, src, item, tl, rect):
    import world_defs
    kind, ranges, params = world_defs.request(item, tl, 0.0)
    for a, b in sidecars.chunks_for(src.shots, src.duration_s, ranges):
        n = int(round((b - a) * 25))
        p = w3s.gray_mask_clip(str(tmp_path / f"o{a}.mp4"), 640, 360, 25.0,
                               n, rect)
        w3s.put_chunk(store, src, kind, (a, b), path=p, params=params,
                      meta={"fps": 25.0, "frames": n, "width": 640,
                            "height": 360})


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg")
def test_a_pinned_word_is_seated_on_the_objects_top_edge(store, tmp_path):
    src = w3s.source()
    ob = {"id": "ob1", "type": "object", "stage": "world", "start": 0.5,
          "end": 3.5, "prompt": {"src_t": 1.0, "box": [0.15, 0.7, 0.85,
                                                         1.0]}}
    edl = {"keep": [[0.0, 4.0]], "fx": [ob], "motion": [
        {"id": "w1", "template": "html", "html": "x", "start": 1.0,
         "end": 2.5, "anchor": {"to": "screen", "pin": [0.5, 0.7],
                                "occluders": ["object:ob1"]}}]}
    c, e = w3s.ctx(edl, src, workdir=str(tmp_path))
    _object_chunks(store, tmp_path, src, e["fx"][0], c.tl,
                   (100, 250, 540, 360))
    clip = w3s.square_clip(str(tmp_path / "sq.mov"), w=200, h=60, n=40)
    clip.x, clip.y = 60, 40                  # authored far above the laptop
    new, notes = world_layer.compose(e["motion"][0], clip, c)
    assert new is not None and any("seated on ob1" in n for n in notes)
    a = w3s.rgba_frames(new.path, new.w, new.h)[20][..., 3]
    ys, xs = np.where(a > 127)
    ys, xs = ys + new.y, xs + new.x
    # the word is centred on the laptop and 30% of it is behind its lid
    assert abs(xs.mean() - 320) <= 3
    assert abs(ys.min() - (250 - 0.7 * 60)) <= 3
    assert ys.max() <= 251                   # cut by the lid at y=250


# ── print polarity: type multiplied into a surface must be darker ───────

def _flat_source(tmp_path, colour):
    p = str(tmp_path / f"{colour}.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c={colour}:s=640x360:r=25:d=6", "-c:v",
                    "libx264", "-pix_fmt", "yuv420p", p], check=True)
    return p


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg")
@pytest.mark.parametrize("surface,expect", [("white", "ink"),
                                            ("black", "screen"),
                                            ("0x606060", None)])
def test_printed_type_takes_the_polarity_the_surface_needs(store, tmp_path,
                                                           surface, expect):
    src = w3s.source()
    pl = {"id": "pl1", "type": "plane", "stage": "world", "start": 0.5,
          "end": 3.5, "src_t": 1.0, "surface": "wall", "motion": "static",
          "quad": [[0.2, 0.1], [0.8, 0.1], [0.8, 0.5], [0.2, 0.5]]}
    edl = {"keep": [[0.0, 4.0]], "fx": [pl], "motion": [
        {"id": "w1", "template": "html", "html": "x", "start": 1.0,
         "end": 2.5, "composite": {"blend": "multiply"},
         "anchor": {"to": "plane:pl1", "occluders": []}}]}
    c, e = w3s.ctx(edl, src, workdir=str(tmp_path))
    c.src_path = _flat_source(tmp_path, surface)
    clip = w3s.square_clip(str(tmp_path / "sq.mov"), w=200, h=60, n=40)
    items, clips = world_layer.finish([e["motion"][0]], [clip], c)
    if expect == "ink":
        assert "w1" in c._ink
        fr = w3s.rgba_frames(clips[0].path, clips[0].w, clips[0].h)[20]
        on = fr[..., 3] > 200
        assert on.any() and fr[..., :3][on].mean() < 40
        assert items[0]["composite"]["blend"] == "multiply"
    elif expect == "screen":
        assert items[0]["composite"]["blend"] == "screen"
        assert "w1" not in c._ink
    else:
        assert "w1" not in c._ink and "w1" not in c._blend
        assert items[0]["composite"]["blend"] == "multiply"


def test_every_plane_puts_the_person_in_front_by_default():
    for surface in ("floor", "desk", "wall", "screen", "other"):
        defs = {"pl1": {"id": "pl1", "type": "plane", "surface": surface}}
        assert world_layer._default_occluders(
            {"to": "plane:pl1"}, {}, defs) == ["person"]
        assert world_layer._default_occluders(
            {"to": "plane:pl1", "occluders": []}, {}, defs) == []


# ── a graphic that leaves the frame is reported; orbits need headroom ───

def test_a_graphic_mostly_outside_the_frame_is_a_reduced_rung():
    import cv2
    rgba = np.zeros((100, 100, 4), np.uint8)
    rgba[20:80, 20:80, 3] = 255
    inside = np.array([[1.0, 0, 50], [0, 1.0, 50], [0, 0, 1]])
    half_out = np.array([[1.0, 0, -50], [0, 1.0, 50], [0, 0, 1]])
    assert world_layer._inside_share(cv2, rgba, inside, 640, 360) > 0.99
    assert world_layer._inside_share(cv2, rgba, half_out, 640, 360) < 0.6
    assert world_layer.rung_of(object(), ["leaves the frame: 50% of it is "
                                          "outside"])[0] == "reduced"


def test_an_orbit_is_proposed_only_where_the_ring_fits(store):
    from timeline import Timeline
    edl = schemas.validate_edl(dict(BASE), 20.0).model_dump()
    index = {"video": {"width": 1920, "height": 1080, "fps": 25.0,
                       "duration": 20.0}}
    tl = Timeline(edl["keep"])

    def fits(sha, cy, ry):
        src = w3s.source(sha=sha * 64, w=1920, h=1080, dur=20.0)
        rows = [{"t": round(k * 0.1, 2), "faces": [{
            "b": [0.4, cy - ry, 0.6, cy + ry], "s": 0.9, "p": [],
            "he": [0.5, cy, ry * 0.8, ry, 0.0]}]} for k in range(200)]
        for a, b in sidecars.chunk_ranges((), src.duration_s):
            w3s.put_chunk(store, src, "landmarks.face", (a, b), data={
                "version": 1, "samples": [r for r in rows
                                          if a <= r["t"] < b]})
        wctx = world_planner._wctx(edl, index, src)
        return world_planner._ring_fits(wctx, src, tl, 3.0)
    assert fits("c", 0.45, 0.12)        # a medium shot with headroom
    assert not fits("d", 0.3, 0.3)      # a tight close-up near the top


# ── a stitched preview keeps the measurement keys (stitch._window_fx
#    rebases world definitions exactly: they draw nothing, their window only
#    keys the measurement) ─────────────────────────────────────────────────

@pytest.mark.parametrize("w0,w1", [(4.1, 6.95), (4.0, 7.0), (3.5, 6.9),
                                   (4.2, 6.9)])
def test_definition_keys_survive_a_stitch_window(w0, w1):
    import stitch
    import world_defs
    from timeline import Timeline
    e = schemas.validate_edl({
        "keep": [[27.8, 31.9], [42.5, 45.3]],
        "fx": [{"id": "ob1", "type": "object", "stage": "world",
                "start": 4.25, "end": 6.85, "prompt": {
                    "src_t": 43.1, "box": [0.4, 0.775, 0.66, 1.0]}},
               {"id": "pl1", "type": "plane", "stage": "world",
                "start": 4.25, "end": 6.85, "src_t": 43.0,
                "surface": "wall", "quad": [[0.3, 0.0], [0.7, 0.0],
                                            [0.7, 0.5], [0.3, 0.55]]}]},
        47.5).model_dump()
    tl = Timeline(e["keep"])
    we = schemas.validate_edl(stitch.window_edl(e, tl, w0, w1, fps=30.0),
                              47.5, render_fragment=True).model_dump()
    tl2 = Timeline(we["keep"], we.get("inserts") or [], we.get("speed") or [])
    for fid in ("ob1", "pl1"):
        full = world_defs.request(world_defs.definitions(e)[fid], tl, 0.0)
        part = world_defs.request(world_defs.definitions(we)[fid], tl2, 0.0)
        assert full == part, fid


def test_world_graphics_never_join_a_screen_series():
    import motion_look
    a = {"id": "a", "template": "word_slam", "start": 0.6, "end": 3.9,
         "params": {"text": "THE WORLD OF BITS", "role": "condensed"},
         "anchor": {"to": "plane:pl1"}}
    b = {"id": "b", "template": "word_slam", "start": 4.55, "end": 6.85,
         "params": {"text": "FLYING CARS", "role": "condensed"},
         "anchor": {"to": "screen", "occluders": ["object:ob1"]}}
    c = dict(b, id="c", anchor=None)
    d = dict(a, id="d", anchor=None)
    assert motion_look.series_runs([a, b]) == []
    assert len(motion_look.series_runs([d, c])) == 1


def test_the_w1_card_loop_leaves_wave3_cards_to_the_world_planner():
    import device_planner
    assert device_planner._world_card("anchor_track")
    assert device_planner._world_card("layout_mosaic")
    assert device_planner._world_card("light_dissolve")
    assert not device_planner._world_card("subject_fill_flash")
    assert not device_planner._world_card("grade_section")


# ── a device-stage breakout can lift the element the recording clicked ──

def test_breakout_element_lifts_the_clicked_dom_element(monkeypatch):
    import agent_tools
    import webrecord
    from tools_ext import layout_tools as LT
    events = [{"t": 1.0, "kind": "click", "x": 0.3, "y": 0.2,
               "box": [0.2, 0.15, 0.4, 0.25]},
              {"t": 4.0, "kind": "click", "x": 0.7, "y": 0.6,
               "box": [0.55, 0.5, 0.85, 0.7]}]
    monkeypatch.setattr(agent_tools, "_resolve_media_asset",
                        lambda ctx, key, kinds: ({"meta": {
                            "demo_events": events}}, None))
    ctx = Ctx(BASE)
    res = LT.add_device_stage(ctx, "asset:media/p1/a.mp4", start=2.0,
                              end=8.0, breakout_element=True,
                              breakout_at=6.1)
    assert res.startswith("EDL v3 -> v4"), res
    (m,) = [x for x in ctx.edl["motion"] if x["template"] == "device_stage"]
    b = [float(v) for v in m["params"]["breakout"]]
    # the 4.0 s click (6.1 - 2.0 = 4.1 s into the recording), padded 15%
    assert b == pytest.approx([0.505, 0.47, 0.895, 0.73], abs=1e-3)
    assert "box [0.55,0.50,0.85,0.70]" in webrecord.summarize_events(events)
    # no element near: an honest refusal
    res2 = LT.add_device_stage(ctx, "asset:media/p1/a.mp4", start=2.0,
                               end=8.0, breakout_element=True,
                               breakout_at=9.5)
    assert res2.startswith("REJECTED")
