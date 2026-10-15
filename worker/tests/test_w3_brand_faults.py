"""Wave 3 brand integrity and fault injection (owner rule: the end card and
the free-tier watermark stay bit-exact; AT-0C-3: a device may degrade, it
never fails a render).

One spec carries every Wave 3 render path at once: a measured point track,
a segmented object (a mask chunk), a static plane, the three light devices
(dissolve / trail / rim — their matte is absent here, so the substitute
rung), graphics anchored to the track (occluded by the object) and pinned
to the plane, an ember element and a HUD. Sidecar chunks are written
straight into a local store (no model); Chromium draws the graphics where
the motion engine is available and they degrade where it is not.
"""

import os
import shutil
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
sys.path.insert(0, WORKER)
sys.path.insert(0, HERE)

import faultinject                                         # noqa: E402
import fxgraph                                             # noqa: E402
import renderer                                            # noqa: E402
import sidecars                                            # noqa: E402
import w0b_probe                                           # noqa: E402
import w3_support as w3s                                   # noqa: E402
from schemas import program_duration, validate_edl         # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"),
                                reason="ffmpeg required")

SHA = "ce" * 32
SRC_S = 12.0
KEEP = [[0.0, 4.0], [5.0, 9.0]]
IDX = {"words": [], "silences": [], "sentences": [],
       "video": {"duration": SRC_S, "width": 640, "height": 360,
                 "fps": 25.0, "sha256": SHA}}


def _src():
    return sidecars.source_from(SHA, None, IDX["video"], None, ())


def _write_sidecars(root, tmp):
    """Ready chunks for the track and the object over their windows."""
    import world_defs
    from timeline import Timeline
    src = _src()
    tl = Timeline(KEEP, [], [])
    tr = {"id": "tr1", "type": "track", "stage": "world", "start": 0.5,
          "end": 3.5, "seed": {"src_t": 1.0, "x": 0.3, "y": 0.45},
          "device": "anchor_track"}
    ob = {"id": "ob1", "type": "object", "stage": "world", "start": 1.0,
          "end": 3.5, "prompt": {"src_t": 1.5, "box": [0.4, 0.5, 0.9, 1.0]},
          "label": "box", "device": "object_occluder"}
    kind, ranges, params = world_defs.request(tr, tl)
    rows = [[round(k / 25.0, 3), round(0.3 + 0.02 * (k / 25.0), 4), 0.45,
             True] for k in range(int(SRC_S * 25))]
    for a, b in sidecars.chunks_for((), src.duration_s, ranges):
        w3s.put_chunk(root, src, kind, (a, b), params=params,
                      data={"version": 1, "tracks": [
                          {"id": "tr1", "seed_t": 1.0, "carried": False,
                           "rows": [r for r in rows if a <= r[0] < b]}]})
    kind, ranges, params = world_defs.request(ob, tl)
    for a, b in sidecars.chunks_for((), src.duration_s, ranges):
        n = int(round((b - a) * 25))
        p = w3s.gray_mask_clip(os.path.join(tmp, f"ob{a}.mp4"), 640, 360,
                               25.0, n, (256, 180, 576, 360))
        w3s.put_chunk(root, src, kind, (a, b), path=p, params=params,
                      meta={"fps": 25.0, "frames": n, "width": 640,
                            "height": 360})
    return tr, ob


def _devices(tr, ob):
    plane = {"id": "pl1", "type": "plane", "stage": "world", "start": 3.5,
             "end": 7.5, "src_t": 6.0, "surface": "wall", "motion": "static",
             "quad": [[0.15, 0.2], [0.85, 0.15], [0.85, 0.6], [0.15, 0.65]],
             "device": "plane_type"}
    fx = [tr, ob, plane,
          {"id": "ld", "type": "light", "stage": "world", "start": 6.0,
           "end": 7.6, "kind": "dissolve", "duration_s": 1.6,
           "device": "light_dissolve"},
          {"id": "lt", "type": "light", "stage": "world", "start": 0.2,
           "end": 1.2, "kind": "trail", "device": "light_trail"},
          {"id": "lr", "type": "light", "stage": "world", "start": 4.2,
           "end": 5.2, "kind": "rim", "colour": "#ff5a1f",
           "device": "light_rim"}]
    motion = [
        {"id": "gc", "template": "glass_card", "start": 0.6, "end": 3.4,
         "params": {"title": "140"}, "layer": "above_captions",
         "anchor": {"to": "track:tr1", "occluders": ["object:ob1"]}},
        {"id": "ws", "template": "word_slam", "start": 3.6, "end": 7.4,
         "params": {"text": "FLYING CARS"}, "layer": "below_captions",
         "anchor": {"to": "plane:pl1"}, "composite": {"blend": "multiply"}},
        {"id": "em", "template": "el_particles", "start": 6.0, "end": 7.9,
         "params": {"kind": "embers", "emit": "point", "count": 160,
                    "seed": 7}, "composite": {"blend": "screen"}},
        {"id": "hud", "template": "hud", "start": 0.0, "end": 8.0,
         "params": {"tl": "DEV DAY", "br": "REC", "rec": True,
                    "mark": ["0.09", "0.05", "0.5", "0.13"]},
         "layer": "above_captions", "mute_captions": False},
    ]
    return fx, motion


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    d = tmp_path_factory.mktemp("w3brand")
    root = str(d / "store")
    os.makedirs(root)
    tr, ob = _write_sidecars(root, str(d))
    fx, motion = _devices(tr, ob)
    src = w0b_probe.make_source(str(d / "src25.mp4"), rate="25", dur=SRC_S)
    return {"dir": str(d), "store": root, "src": src, "fx": fx,
            "motion": motion}


def _base():
    return {"keep": [list(k) for k in KEEP], "captions": {"enabled": False},
            "frame": {"ratio": "9:16"}}


# ── brand: every W3 path before the end card and the watermark ──────────

@pytest.fixture(scope="module")
def brand(world):
    base = _base()
    every = dict(base, fx=world["fx"], motion=world["motion"])
    cases = [{"name": "plain", "src": world["src"], "idx": IDX,
              "edl": base, "want_wm": True, "dump_frames": [40, 160]},
             {"name": "every", "src": world["src"], "idx": IDX,
              "edl": every, "want_wm": True, "dump_frames": [40, 160]}]
    saved = {k: os.environ.get(k) for k in ("OUTRO_ON_PREVIEW",
                                             "VALMERA_SIDECAR_DIR")}
    os.environ["OUTRO_ON_PREVIEW"] = "1"
    os.environ["VALMERA_SIDECAR_DIR"] = world["store"]
    try:
        return w0b_probe.run(cases, os.path.join(world["dir"], "probe"))
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_every_w3_path_renders_and_the_end_card_is_untouched(brand):
    a, b = brand["plain"], brand["every"]
    assert a["error"] is None and b["error"] is None, (a["error"], b["error"])
    assert len(a["frames"]) == len(b["frames"]) == 200 + 125
    assert b["frames"][200:] == a["frames"][200:]          # the card, bit-exact
    changed = [i for i in range(200) if a["frames"][i] != b["frames"][i]]
    assert len(changed) >= 150, len(changed)
    rungs = {r["id"]: r["rung"] for r in b["report"]}
    assert {"ld", "lt", "lr"} <= set(rungs)
    assert all(r in fxgraph.RUNGS for r in rungs.values())


def test_the_watermark_is_drawn_after_every_w3_path(brand):
    from test_w0b_render import _robot_opaque, _yuv
    W, H = 270, 480
    m = _robot_opaque(W, H)
    assert m.sum() > 40
    for k in ("40", "160"):
        a = _yuv(brand["plain"]["dumps"][k], W, H)
        b = _yuv(brand["every"]["dumps"][k], W, H)
        assert np.abs(a[0] - b[0])[m].max() == 0
        # the picture around the mark changed (HUD, graphics, light)
        assert (np.abs(a[0] - b[0])[~m] > 8).mean() > 0.004


# ── faults: zero render failures ─────────────────────────────────────────

FAULTS = ("none",) + faultinject.FAULTS


@pytest.fixture
def store(world):
    prev = sidecars.use_store(sidecars.LocalStore(world["store"]))
    sidecars._reset_caches()
    yield world["store"]
    sidecars.use_store(prev)
    sidecars._reset_caches()


@pytest.mark.parametrize("fault", FAULTS)
def test_every_fault_times_every_w3_device_renders(fault, world, store,
                                                   tmp_path, monkeypatch):
    monkeypatch.setattr(fxgraph, "_device_budget", lambda plan, edl: None)
    edl = dict(_base(), fx=world["fx"], motion=world["motion"])
    norm = validate_edl(edl, SRC_S).model_dump()
    want = float(program_duration(norm))
    out = str(tmp_path / "out.mp4")
    rows = []
    names = () if fault == "none" else (fault,)
    tok = fxgraph.collect(rows)
    try:
        with faultinject.inject(*names):
            dur = renderer.render_edl(norm, IDX, world["src"], out,
                                      str(tmp_path), True,
                                      suppress_outro=True)
    finally:
        fxgraph.stop_collecting(tok)
    assert dur == pytest.approx(want, abs=0.07), (fault, dur, want)
    probe = renderer.media.probe(out)
    assert probe.get("width") and probe.get("height"), probe
    report = {r["id"]: r for r in fxgraph.merge_report(rows)}
    assert all(r["rung"] in fxgraph.RUNGS for r in report.values())
    if fault == "ffmpeg_filter_missing":
        assert all(report[k]["rung"] == "omit" for k in ("ld", "lt", "lr")
                   if k in report)


def test_the_world_compositor_is_off_for_an_edl_without_w3_items(world,
                                                                 store):
    """No anchored graphic, no Wave 3 template: no world context, and the
    render is the same bytes as before Wave 3 (renderer._world_ctx)."""
    import world_layer
    edl = validate_edl(_base(), SRC_S).model_dump()
    assert not world_layer.needed(edl)
    from timeline import Timeline
    tl = Timeline(edl["keep"], [], [])
    assert renderer._world_ctx(edl, 270, 480, 25.0, tl, IDX, True, None,
                               0.0, None, world["src"]) is None


