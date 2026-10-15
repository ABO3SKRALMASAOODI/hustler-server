"""Wave 3 (3A-3C): the world schema, the world compositor and the world verbs.

Measured exactly on CI: sidecar chunks are written straight into a local
store (no model), decal clips are synthetic qtrle squares (no Chromium),
so every assertion is on the compositor's own geometry, occlusion and
fallbacks (devices/INTERFACES_W3.md §3-4, §7).
"""

import copy
import json
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
sys.path.insert(0, WORKER)
sys.path.insert(0, HERE)

import schemas                                         # noqa: E402
import sidecars                                        # noqa: E402
import world_layer                                     # noqa: E402
import w3_support as w3s                               # noqa: E402
from schemas import EDLValidationError                 # noqa: E402

PLANE = {"id": "pl1", "type": "plane", "stage": "world", "start": 0.5,
         "end": 3.5, "src_t": 1.0, "surface": "desk",
         "quad": [[0.2, 0.55], [0.8, 0.55], [0.9, 0.95], [0.1, 0.95]]}


@pytest.fixture
def store(tmp_path, monkeypatch):
    root = str(tmp_path / "store")
    os.makedirs(root)
    prev = sidecars.use_store(sidecars.LocalStore(root))
    yield root
    sidecars.use_store(prev)


def _edl(**extra):
    e = {"keep": [[0.0, 4.0]]}
    e.update(extra)
    return e


# ── schema ─────────────────────────────────────────────────────────────

def test_world_types_and_the_anchor_slot_are_registered():
    assert {"track", "object", "plane", "light"} <= set(schemas.FX_TYPES)
    assert "MotionItem.anchor" in schemas._FX_SLOT_TYPES


@pytest.mark.parametrize("anchor,ok", [
    ({"to": "hand.index_tip"}, True),
    ({"to": "head", "follow": ["scale"]}, True),
    ({"to": "face.iris_l", "which": "left"}, True),
    ({"to": "pose.chest"}, True),
    ({"to": "screen", "occluders": ["person"]}, True),
    ({"to": "hand.nose"}, False),
    ({"to": "plane"}, False),
    ({"to": "head", "place": [0, 0, 1, 1]}, False),
    ({"to": "screen", "occluders": ["table"]}, False),
    ({"to": "head", "which": 9}, False),
])
def test_anchor_grammar(anchor, ok):
    edl = _edl(motion=[{"id": "m1", "template": "word_slam", "start": 1.0,
                        "end": 2.0, "params": {"text": "HI"},
                        "anchor": anchor}])
    if ok:
        e = schemas.validate_edl(edl, 10.0).model_dump()
        assert e["motion"][0]["anchor"]["to"] == anchor["to"]
        if anchor.get("follow"):
            assert e["motion"][0]["anchor"]["follow"][0] == "position"
    else:
        with pytest.raises(EDLValidationError):
            schemas.validate_edl(edl, 10.0)


def test_an_anchor_names_definitions_that_exist():
    edl = _edl(fx=[dict(PLANE)],
               motion=[{"id": "m1", "template": "word_slam", "start": 1.0,
                        "end": 2.0, "params": {"text": "HI"},
                        "anchor": {"to": "plane:pl1",
                                   "occluders": ["object:ob9"]}}])
    with pytest.raises(EDLValidationError, match="ob9"):
        schemas.validate_edl(copy.deepcopy(edl), 10.0)
    edl["motion"][0]["anchor"]["occluders"] = ["person"]
    schemas.validate_edl(copy.deepcopy(edl), 10.0)
    edl["motion"][0]["anchor"]["to"] = "object:pl1"
    with pytest.raises(EDLValidationError, match="is a plane"):
        schemas.validate_edl(edl, 10.0)


def test_definition_bounds():
    bad = dict(PLANE, quad=[[0.2, 0.5], [0.8, 0.9], [0.8, 0.5],
                            [0.2, 0.9]])
    with pytest.raises(EDLValidationError, match="convex"):
        schemas.validate_edl(_edl(fx=[bad]), 10.0)
    ob = {"id": "ob1", "type": "object", "stage": "world", "start": 0.0,
          "end": 7.0, "prompt": {"src_t": 1.0, "box": [0.3, 0.6, 0.6, 1.0]}}
    with pytest.raises(EDLValidationError, match="6 s"):
        schemas.validate_edl(_edl(keep=[[0.0, 9.0]], fx=[ob]), 10.0)
    ob["end"] = 3.5
    schemas.validate_edl(_edl(fx=[ob]), 10.0)
    late = dict(ob, prompt={"src_t": 12.0, "box": [0.3, 0.6, 0.6, 1.0]})
    with pytest.raises(EDLValidationError, match="past the end"):
        schemas.validate_edl(_edl(fx=[late]), 10.0)
    with pytest.raises(EDLValidationError, match="object_"):
        schemas.validate_edl(_edl(fx=[dict(ob, device="plane_type")]), 10.0)


def test_light_items_never_flash_full_frame():
    it = {"id": "lt", "type": "light", "stage": "world", "start": 1.0,
          "end": 1.2, "kind": "dissolve", "duration_s": 0.5}
    e = schemas.validate_edl(_edl(fx=[it]), 10.0)
    assert e.fx[0].flash_times() == []
    with pytest.raises(EDLValidationError, match="decay"):
        schemas.validate_edl(_edl(fx=[dict(it, decay=0.9)]), 10.0)


def test_an_edl_without_world_fields_keeps_its_signature():
    edl = _edl(motion=[{"id": "m1", "template": "word_slam", "start": 1.0,
                        "end": 2.0, "params": {"text": "HI"}}])
    a = schemas.validate_edl(copy.deepcopy(edl), 10.0).model_dump()
    assert a["motion"][0].get("anchor") is None
    sig = schemas.edl_signature(a)
    assert "anchor" not in sig and "track" not in sig


# ── the compositor ─────────────────────────────────────────────────────

def _track_chunk(store, src, fid, rows):
    import world_defs
    from timeline import Timeline
    d = {"id": fid, "type": "track", "stage": "world", "start": 0.0,
         "end": 4.0, "seed": {"src_t": 1.0, "x": 0.25, "y": 0.5}}
    tl = Timeline([[0.0, 4.0]], [], [])
    kind, ranges, params = world_defs.request(d, tl)
    for a, b in sidecars.chunks_for((), src.duration_s, ranges):
        w3s.put_chunk(store, src, "track.points", (a, b), params=params,
                      data={"version": 1, "tracks": [
                          {"id": fid, "seed_t": 1.0, "carried": False,
                           "rows": [r for r in rows if a <= r[0] < b]}]})
    return d


def test_a_graphic_rides_a_tracked_point(store, tmp_path):
    src = w3s.source()
    rows = [[round(k / 25.0, 3), round(0.2 + 0.1 * (k / 25.0), 4), 0.5,
             True] for k in range(0, 150)]
    d = _track_chunk(store, src, "tr1", rows)
    clip = w3s.square_clip(str(tmp_path / "sq.mov"), n=75)
    edl = _edl(fx=[d], motion=[{"id": "m1", "template": "html", "html": "x",
                                "start": 1.0, "end": 4.0,
                                "anchor": {"to": "track:tr1", "smooth": 0.0}}])
    c, e = w3s.ctx(edl, src, workdir=str(tmp_path))
    new, notes = world_layer.compose(e["motion"][0], clip, c)
    assert new is not None, notes
    fr = w3s.rgba_frames(new.path, new.w, new.h)
    assert len(fr) == 75
    for k in (0, 30, 74):
        t = 1.0 + (k + 0.5) / 25.0
        want_x = (0.2 + 0.1 * t) * 640
        cx, cy = w3s.alpha_centroid(fr[k])
        assert abs(cx + new.x - want_x) <= 1.5
        assert abs(cy + new.y - 0.5 * 360) <= 1.5


def test_the_render_geometry_maps_the_anchor_through_the_crop(store,
                                                               tmp_path):
    """A 9:16 crop with a focus: the anchor lands where the crop shows the
    point (keepout.Geometry, the render's own fit)."""
    src = w3s.source(sha="cd" * 32)
    rows = [[round(k / 25.0, 3), 0.55, 0.5, True] for k in range(150)]
    d = _track_chunk(store, src, "tr1", rows)
    clip = w3s.square_clip(str(tmp_path / "sq.mov"), w=10, h=10, n=25)
    edl = _edl(frame={"ratio": "9:16", "mode": "crop", "focus_x": 0.5},
               fx=[d], motion=[{"id": "m1", "template": "html", "html": "x",
                                "start": 1.0, "end": 2.0,
                                "anchor": {"to": "track:tr1"}}])
    W, H = 202, 360
    c, e = w3s.ctx(edl, src, W=W, H=H, workdir=str(tmp_path))
    new, _n = world_layer.compose(e["motion"][0], clip, c)
    fr = w3s.rgba_frames(new.path, new.w, new.h)
    cx, _cy = w3s.alpha_centroid(fr[10])
    # the crop shows source x in [0.5 - 0.5 * 202/640, 0.5 + ...]
    vis = W / 640.0
    want = (0.55 - (0.5 - vis / 2.0)) / vis * W
    assert abs(cx + new.x - want) <= 1.5


def test_the_person_in_front_cuts_the_graphic(store, tmp_path, monkeypatch):
    import matte_track
    src = w3s.source()
    params = matte_track.sidecar_params()
    for a, b in sidecars.chunk_ranges((), src.duration_s):
        n = int(round((b - a) * 25))
        p = w3s.gray_mask_clip(str(tmp_path / f"m{a}.mp4"), 640, 360, 25.0,
                               n, (300, 0, 640, 360))
        w3s.put_chunk(store, src, "matte.person", (a, b), path=p,
                      params=params, meta={"fps": 25.0, "frames": n,
                                           "width": 640, "height": 360})
    clip = w3s.square_clip(str(tmp_path / "sq.mov"), w=200, h=100, n=25)
    clip.x, clip.y = 200, 100
    edl = _edl(motion=[{"id": "m1", "template": "html", "html": "x",
                        "start": 1.0, "end": 2.0,
                        "anchor": {"to": "screen",
                                   "occluders": ["person"]}}])
    c, e = w3s.ctx(edl, src, workdir=str(tmp_path))
    new, notes = world_layer.compose(e["motion"][0], clip, c)
    assert new is not None and not notes, notes
    fr = w3s.rgba_frames(new.path, new.w, new.h)[12]
    a = fr[..., 3]
    # left of the person: drawn; on the person: cut
    assert a[50 + 100 - new.y, 250 - new.x] > 240
    assert a[50 + 100 - new.y, 350 - new.x] < 15


def test_a_static_plane_lays_the_graphic_inside_its_quad(store, tmp_path):
    src = w3s.source()
    pl = dict(PLANE, motion="static")
    clip = w3s.square_clip(str(tmp_path / "sq.mov"), w=300, h=100, n=25)
    edl = _edl(fx=[pl], motion=[{"id": "m1", "template": "html",
                                 "html": "x", "start": 1.0, "end": 2.0,
                                 "anchor": {"to": "plane:pl1",
                                            "occluders": []}}])
    c, e = w3s.ctx(edl, src, workdir=str(tmp_path))
    new, notes = world_layer.compose(e["motion"][0], clip, c)
    assert new is not None
    a = w3s.rgba_frames(new.path, new.w, new.h)[5][..., 3] > 127
    ys, xs = np.where(a)
    xs, ys = xs + new.x, ys + new.y
    # inside the quad's bounding box, centred on it, a trapezoid (wider at
    # the bottom: the desk comes toward the camera)
    assert xs.min() >= 0.1 * 640 - 2 and xs.max() <= 0.9 * 640 + 2
    assert ys.min() >= 0.55 * 360 - 2 and ys.max() <= 0.95 * 360 + 2
    top = xs[ys <= ys.min() + 2]
    bot = xs[ys >= ys.max() - 2]
    assert np.ptp(bot) > np.ptp(top)
    assert abs(xs.mean() - 320) < 12


def test_an_unmeasured_anchor_keeps_the_authored_placement(store, tmp_path):
    src = w3s.source()
    clip = w3s.square_clip(str(tmp_path / "sq.mov"), n=25)
    edl = _edl(motion=[{"id": "m1", "template": "html", "html": "x",
                        "start": 1.0, "end": 2.0,
                        "anchor": {"to": "hand.index_tip"}}])
    c, e = w3s.ctx(edl, src, workdir=str(tmp_path))
    new, notes = world_layer.compose(e["motion"][0], clip, c)
    assert new is None
    assert any("pinned static" in n for n in notes)
    items, clips = world_layer.finish([e["motion"][0]], [clip], c)
    assert clips[0] is clip


def test_items_without_world_fields_pass_through_untouched(tmp_path):
    src = w3s.source()
    clip = w3s.square_clip(str(tmp_path / "sq.mov"), n=5)
    item = {"id": "m1", "template": "word_slam", "start": 0.0, "end": 0.2}
    c, _e = w3s.ctx(_edl(), src, workdir=str(tmp_path))
    items, clips = world_layer.finish([item], [clip], c)
    assert items[0] is item and clips[0] is clip
    assert not world_layer.needed({"motion": [item]})
    assert world_layer.augment(item, c) is item


def test_the_largest_visible_rect():
    g = np.zeros((6, 8), bool)
    g[1:5, 2:7] = True
    g[2, 3] = False
    r = world_layer._largest_rect(g)
    r0, c0, r1, c1 = r
    assert (r1 - r0) * (c1 - c0) == 12


# ── the attachment language ────────────────────────────────────────────

@pytest.mark.parametrize("phrase,target,part", [
    ("hide it behind the laptop", "object", None),
    ("put the price on the desk", "plane", None),
    ("make the label follow his hand", "hand", "palm"),
    ("arc it round his head", "head", None),
    ("on her fingertip", "hand", "index_tip"),
    ("between his hands", "hand", "frame"),
    ("next to his face", "face", None),
    ("on his chest", "pose", "chest"),
    ("type on the floor under her shadow", "plane", None),
    ("on the LED wall", "plane", None),
])
def test_attachment_phrases(phrase, target, part):
    import attachment
    p = attachment.parse(phrase)
    assert p and p["target"] == target, p
    if part:
        assert p.get("part") == part
    if target == "object":
        assert p["name"] == "laptop" and p["relation"] == "behind"
    a = attachment.anchor_for(p)
    if target in ("object", "plane"):
        assert a is None
    else:
        e = schemas.validate_edl(_edl(motion=[{
            "id": "m1", "template": "word_slam", "start": 1.0, "end": 2.0,
            "params": {"text": "HI"}, "anchor": a}]), 10.0)
        assert e.motion[0].anchor is not None


# ── depth order (3B, objseg.depthorder) ──────────────────────────────────

def test_depth_order_from_the_masks():
    from objseg import depthorder
    H, W = 120, 160
    person = np.zeros((H, W), np.uint8)
    person[20:120, 50:110] = 255                 # a torso
    obj = np.zeros((H, W), np.uint8)
    obj[60:90, 70:85] = 255                      # a mic over the chest
    front = person.copy()
    front[60:90, 70:85] = 0                      # the matte has its hole
    assert depthorder.order(obj, front)[0] == "front"
    # the matte swallowed the mic as "person": still in front
    assert depthorder.order(obj, person)[0] == "front"
    # a chair behind: visible beside the torso, and through the gap
    # between the arm and the torso that the closed outline spans
    arm = np.zeros((H, W), np.uint8)
    arm[20:120, 50:100] = 255                    # torso
    arm[40:120, 108:118] = 255                   # an arm, a gap between
    arm[40:46, 100:108] = 255                    # the shoulder joins them
    chair = np.zeros((H, W), np.uint8)
    chair[50:110, 100:108] = 255                 # in the gap ...
    chair[50:110, 118:150] = 255                 # ... and beside the arm
    assert depthorder.order(chair, arm)[0] == "behind"
    far = np.zeros((H, W), np.uint8)
    far[10:30, 5:25] = 255                       # nowhere near the person
    assert depthorder.order(far, person)[0] is None
    o, _d = depthorder.vote([(obj, front), (obj, front), (chair, arm)])
    assert o == "front"
    assert depthorder.vote([(far, person)])[0] is None


def test_an_object_behind_the_person_brings_the_person_in_front(
        store, tmp_path, monkeypatch):
    src = w3s.source()
    ob = {"id": "ob1", "type": "object", "stage": "world", "start": 1.0,
          "end": 2.0, "prompt": {"src_t": 1.5, "box": [0.1, 0.1, 0.4, 0.4]},
          "depth": "behind"}
    clip = w3s.square_clip(str(tmp_path / "sq.mov"), n=25)
    edl = _edl(fx=[ob], motion=[{"id": "m1", "template": "html",
                                 "html": "x", "start": 1.0, "end": 2.0,
                                 "anchor": {"to": "screen",
                                            "occluders": ["object:ob1"]}}])
    c, e = w3s.ctx(edl, src, workdir=str(tmp_path))
    assert world_layer.object_depth(c, ob) == ("behind", "authored")
    _new, notes = world_layer.compose(e["motion"][0], clip, c)
    assert any("behind the person" in n for n in notes), notes
    auto = dict(ob, depth="auto")
    c2, _e2 = w3s.ctx(dict(edl, fx=[auto]), src, workdir=str(tmp_path))
    assert world_layer.object_depth(c2, auto) == ("front", "not measurable")
