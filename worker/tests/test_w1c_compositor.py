"""Track 1C: the compositor's parts, without the renderer.

  * blend modes are exactly ffmpeg's in the blend domain (a difference word
    is |plate - ink| within one level; screen never darkens);
  * keyframed regions ease between keys and rasterise where they should;
  * displacement maps are identity at strength 0, periodic for ribbed
    glass, a lens for refraction, and a seamless loop for heat;
  * ``auto`` picks difference over a busy plate and screen over a dark one,
    and falls back when the result would not read;
  * fills resolve to what the render can draw (an insert across the cut
    with its own handles, the program's edge frame held otherwise);
  * an item WITHOUT a composite takes today's overlay filters byte for
    byte; the stroke-only paint mode joins only the documents that ask;
  * a face region draws through the face mask provider once one exists
    (1A's matte_track), and degrades until then.
"""

import json
import os
import shutil
import subprocess
import sys
import types

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import compositor                                          # noqa: E402
import displacement                                        # noqa: E402
import fxgraph                                             # noqa: E402
import motion_layer                                        # noqa: E402

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"),
                                  reason="needs ffmpeg")


# ── blend math, in the blend domain (RGB, no 4:2:0 encode) ──────────────

class _Clip:
    def __init__(self, path, x, y, w, h):
        self.path, self.x, self.y, self.w, self.h = path, x, y, w, h
        self.frames, self.empty = 10, False


def _rgba_clip(tmp, w, h, ink=(255, 255, 255)):
    """A 10-frame qtrle clip: an opaque ink rectangle in the middle of a
    transparent box (straight alpha, like the engine's captures)."""
    a = np.zeros((h, w, 4), np.uint8)
    a[h // 4:3 * h // 4, w // 4:3 * w // 4] = list(ink) + [255]
    png = os.path.join(tmp, "ink.png")
    from PIL import Image
    Image.fromarray(a, "RGBA").save(png)
    out = os.path.join(tmp, "ink.mov")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", png,
                    "-frames:v", "10", "-r", "25", "-c:v", "qtrle",
                    "-pix_fmt", "argb", out], check=True, capture_output=True)
    return out


def _run_emit(tmp, comp, mode_rgb=True):
    """Lay one composite item over a random-noise plate in an RGB graph;
    returns (plate, out) as float arrays at frame 5."""
    W, H = 64, 48
    plate = os.path.join(tmp, "plate.png")
    rng = np.random.default_rng(7)
    from PIL import Image
    Image.fromarray(rng.integers(0, 256, (H, W, 3), np.uint8), "RGB") \
        .save(plate)
    clip_path = _rgba_clip(tmp, 32, 24)
    clip = _Clip(clip_path, 16, 12, 32, 24)
    clip.composite = compositor.CompositeSide(W=W, H=H, fps=25.0,
                                              blend=comp.get("blend",
                                                             "normal"))
    item = {"id": "m", "start": 0.0, "end": 0.4, "composite": comp}
    parts = ["[0:v]format=gbrp[base]"]
    lab = compositor.emit(parts, "base", 1, item, clip, 25.0, "t")
    parts.append(f"[{lab}]format=rgb24[o]")
    out = os.path.join(tmp, "out.rgb")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1",
                    "-framerate", "25", "-t", "0.4", "-i", plate, "-i",
                    clip_path, "-filter_complex", ";".join(parts), "-map",
                    "[o]", "-frames:v", "8", "-f", "rawvideo", out],
                   check=True, capture_output=True)
    raw = np.fromfile(out, np.uint8).reshape(-1, H, W, 3)
    base = np.asarray(Image.open(plate).convert("RGB"), float)
    return base, raw[5].astype(float)


@needs_ffmpeg
@pytest.mark.parametrize("mode,fn", [
    ("difference", lambda b, l: np.abs(b - l)),
    ("screen", lambda b, l: 255 - (255 - b) * (255 - l) / 255),
    ("multiply", lambda b, l: b * l / 255),
    ("exclusion", lambda b, l: b + l - 2 * b * l / 255)])
def test_blend_modes_are_exact_in_the_blend_domain(tmp_path, mode, fn):
    base, out = _run_emit(str(tmp_path), {"blend": mode})
    ink = 255.0
    inside = (slice(12 + 6, 12 + 18), slice(16 + 8, 16 + 24))
    want = fn(base, ink)
    assert np.abs(out[inside] - want[inside]).max() <= 1.0, mode
    outside = np.ones(base.shape[:2], bool)
    outside[12:36, 16:48] = False
    assert np.abs(out - base)[outside].max() == 0


@needs_ffmpeg
def test_screen_light_never_darkens_in_the_blend_domain(tmp_path):
    base, out = _run_emit(str(tmp_path), {"blend": "screen"})
    assert (out - base).min() >= -1.0


@needs_ffmpeg
def test_opacity_scales_the_mix(tmp_path):
    base, out = _run_emit(str(tmp_path), {"opacity": 0.5})
    inside = (slice(12 + 6, 12 + 18), slice(16 + 8, 16 + 24))
    want = base * 0.5 + 255 * 0.5
    assert np.abs(out[inside] - want[inside]).max() <= 1.5


# ── keyframed regions ────────────────────────────────────────────────────

def test_keyed_points_ease_and_hold():
    r = {"keys": [{"t": 0, "points": [[0, 0], [0.2, 0.2]]},
                  {"t": 1, "points": [[0.5, 0.5], [0.7, 0.7]],
                   "ease": "hold"},
                  {"t": 2, "points": [[0.8, 0.8], [1.0, 1.0]]}]}
    assert compositor.keyed_points(r, -1) == [[0, 0], [0.2, 0.2]]
    assert compositor.keyed_points(r, 0.5) == [
        [pytest.approx(0.25), pytest.approx(0.25)],
        [pytest.approx(0.45), pytest.approx(0.45)]]
    assert compositor.keyed_points(r, 1.5) == [[0.5, 0.5], [0.7, 0.7]]
    assert compositor.keyed_points(r, 9) == [[0.8, 0.8], [1.0, 1.0]]
    r["keys"][0]["ease"] = "in_out"
    p = compositor.keyed_points(r, 0.25)[0][0]
    assert 0 < p < 0.125                       # slow start
    # a polygon whose vertex count changes steps instead of morphing
    poly = {"keys": [{"t": 0, "points": [[0, 0], [1, 0], [0, 1]]},
                     {"t": 1, "points": [[0, 0], [1, 0], [1, 1], [0, 1]]}]}
    assert len(compositor.keyed_points(poly, 0.5)) == 3


@needs_ffmpeg
def test_raster_keyed_draws_each_frame_where_its_keys_say(tmp_path):
    r = {"shape": "rect", "keys": [{"t": 0, "points": [[0, 0], [0.5, 0.5]]},
                                   {"t": 1, "points": [[0.5, 0.5],
                                                       [1, 1]]}]}
    p = compositor.raster_keyed(r, 40, 40, 10, 0.0, 11,
                                str(tmp_path / "k.mkv"))
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", p, "-f",
                          "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, 40, 40)
    assert len(fr) == 11
    assert fr[0][5, 5] == 255 and fr[0][30, 30] == 0
    assert fr[10][30, 30] == 255 and fr[10][5, 5] == 0
    assert fr[5][5, 5] == 0 and fr[5][15, 15] == 255     # half way
    assert fr[5][35, 35] == 0
    assert compositor.keyed_bbox(r, 40, 40) == (0, 0, 40, 40)
    assert compositor.keyed_bbox(dict(r, invert=True), 40, 40) is None


# ── displacement maps ───────────────────────────────────────────────────

def test_material_maps():
    x, y, shade = displacement.ribbed(200, 20, H=1080, strength=0.0)
    assert (x == 128).all() and (y == 128).all()
    x, y, shade = displacement.ribbed(200, 20, H=1080)
    rib = int(displacement.RIB_PX)
    row = x[0].astype(int)
    assert np.abs(row[:200 - rib] - row[rib:]).max() <= 1   # periodic
    assert row.max() > 128 > row.min()
    assert shade.shape == (20, 200, 4) and shade[..., 3].max() > 0
    x, y = displacement.refraction(101, 81, H=1080, seed=3)
    # the lens pulls toward the centre: left of centre samples further
    # right (positive), right of centre further left
    assert x[40, 5] > 128 > x[40, 95]
    assert (x == displacement.refraction(101, 81, H=1080, seed=3)[0]).all()
    frames = displacement.heat_frames(40, 30, H=1080, fps=10)
    assert len(frames) == 10
    a0, a9 = frames[0][1].astype(int), frames[9][1].astype(int)
    assert np.abs(a0 - a9).max() <= np.abs(a0 - frames[5][1]).max()


# ── auto blend ──────────────────────────────────────────────────────────

def _plate(means, dets=None, cols=4, rows=4):
    import base64
    g = base64.b64encode(bytes(means)).decode()
    s = {"t": 0.0, "g": g}
    if dets is not None:
        q = [max(0, min(15, d // 4)) for d in dets]
        s["d"] = base64.b64encode(bytes((q[i] << 4) | q[i + 1]
                                        for i in range(0, len(q), 2))
                                  ).decode()
    return {"c": cols, "r": rows, "s": [s], "dq": 4}


def test_auto_blend_reads_the_plate():
    box = (0, 0, 100, 100)
    # dark and calm: screen
    m, why = compositor.auto_blend(_plate([20] * 16, [2] * 16), box,
                                   100, 100, 250.0)
    assert m == "screen", why
    # busy and bimodal (black and white cells): difference reads
    busy = [10, 240] * 8
    m, why = compositor.auto_blend(_plate(busy, [40] * 16), box, 100, 100,
                                   250.0)
    assert m == "difference", why
    # busy but mid-grey everywhere: difference would not read -> not it
    mid = [120, 135] * 8
    m, _ = compositor.auto_blend(_plate(mid, [40] * 16), box, 100, 100,
                                 250.0)
    assert m != "difference"
    # nothing measured: normal
    assert compositor.auto_blend(None, box, 100, 100, 250.0)[0] == "normal"


# ── fills ────────────────────────────────────────────────────────────────

def _edl_with_insert(clip_start=2.0):
    return {"keep": [[0.0, 4.0], [10.0, 14.0]],
            "inserts": [{"id": "b1", "kind": "video",
                         "asset_key": "media/p/b.mp4", "at_output_s": 4.0,
                         "duration_s": 2.0, "source_start_s": clip_start}]}


def test_next_fill_reads_the_insert_with_its_handles(monkeypatch):
    monkeypatch.setattr(compositor, "media_kind", lambda p: "video")
    args, notes = [], []
    item = {"id": "t", "start": 3.0, "end": 4.0}
    f = compositor._resolve_fill({"kind": "next"}, item, _edl_with_insert(),
                                 108, 192, 25.0, args, 7,
                                 lambda k: "/x/b.mp4", None, notes)
    assert f["kind"] == "media" and f["lead"] == 0.0
    # the insert's clip second at the item's start: 2.0 - (4.0 - 3.0)
    assert args[args.index("-ss") + 1] == "1.000"
    assert f.pop("_next") == 8
    # no handle (the clip starts at 0): its first frame held for the lead
    args, notes = [], []
    f = compositor._resolve_fill({"kind": "next"}, item,
                                 _edl_with_insert(0.4), 108, 192, 25.0,
                                 args, 7, lambda k: "/x/b.mp4", None, notes)
    assert f["lead"] == pytest.approx(0.6) and "held" in notes[0]


def test_fills_across_main_footage_hold_the_edge_frame():
    edl = {"keep": [[0.0, 4.0], [10.0, 14.0]]}
    notes = []
    f = compositor._resolve_fill({"kind": "next"},
                                 {"id": "t", "start": 3.0, "end": 4.0}, edl,
                                 108, 192, 25.0, [], 1, None, None, notes)
    assert f == {"kind": "freeze_next", "cut": 4.0}
    f = compositor._resolve_fill({"kind": "previous"},
                                 {"id": "t", "start": 4.0, "end": 5.0}, edl,
                                 108, 192, 25.0, [], 1, None, None, notes)
    assert f == {"kind": "freeze_prev", "cut": 4.0}
    # too long a hold: dropped, named
    f = compositor._resolve_fill({"kind": "next"},
                                 {"id": "t", "start": 0.1, "end": 3.9}, edl,
                                 108, 192, 25.0, [], 1, None, None, notes)
    assert f is None and "would hold the program" in notes[-1]
    f = compositor._resolve_fill({"kind": "source", "src_t": 2.5},
                                 {"id": "t", "start": 1.0, "end": 2.0}, edl,
                                 108, 192, 25.0, [], 1, None, None, notes)
    assert f == {"kind": "program", "offset": 1.5}


def test_colour_roles_resolve():
    assert compositor.resolve_colour("white") == "#ffffff"
    assert compositor.resolve_colour("#AA00ff") == "#aa00ff"
    edl = {"captions": {"mode": "from_transcript",
                        "style": {"highlight_color": "#22CCFF"}}}
    assert compositor.resolve_colour("accent", edl) == "#22ccff"
    assert compositor.resolve_colour("accent", {}) == \
        compositor.ACCENT_FALLBACK.lower()


def test_glyph_refs_and_registry():
    edl = {"fx": [{"id": "a", "region": {"shape": "glyph", "ref": "m1"}},
                  {"id": "b", "origin": {"kind": "glyph", "ref": "m1"}},
                  {"id": "c", "region": {"shape": "rect"}}]}
    assert compositor.glyph_refs(edl) == {"m1": 2}
    compositor.register_glyphs("/tmp/w1c_reg", {"m1": {
        "inputs": [5], "x": 108, "y": 192, "w": 216, "h": 96,
        "start": 1.0, "end": 2.0, "W": 1080, "H": 1920, "fps": 25.0}})
    plan = fxgraph.FxPlan(W=1080, H=1920, fps=25.0, program_s=5.0,
                          workdir="/tmp/w1c_reg")
    assert compositor.glyph_box(plan, "m1") == [0.1, 0.1, 0.3, 0.15]
    assert compositor.glyph_box(plan, "m1", 3.0) is None
    assert compositor.glyph_box(plan, "zz") is None
    assert fxgraph.peer("compositor:glyph_box", plan, "m1", 1.5) == \
        [0.1, 0.1, 0.3, 0.15]

    class Ctx:
        parts = []

        def __init__(self):
            self.plan = plan

        def uid(self, p):
            return p + "1"
    ctx = Ctx()
    lab = fxgraph.mask(plan, "glyph:m1", "screen", ctx=ctx)
    assert lab == "gm1" and ctx.parts[-1].startswith("[5:v]")
    # one input per reference: a second request this build is absent...
    assert fxgraph.mask(plan, "glyph:m1", "screen", ctx=ctx) is None
    # ...and a new filtergraph build hands it out again
    ctx2 = Ctx()
    ctx2.parts = []
    assert fxgraph.mask(plan, "glyph:m1", "screen", ctx=ctx2) == "gm1"


# ── the legacy path is untouched ────────────────────────────────────────

def test_an_item_without_a_composite_takes_todays_filters():
    class Clip:
        x, y = 10, 20
    parts = []
    out = motion_layer.overlay_clip(parts, "v0", 7, {"start": 1.0,
                                                     "end": 2.0},
                                    Clip(), 30.0, "mga0")
    assert out == "mga0o"
    assert parts == [
        "[7:v]setpts=PTS-STARTPTS,format=rgba,tpad=stop_mode=clone:"
        "stop_duration=0.0667,setpts=PTS+1.0000/TB[mga0c]",
        "[v0][mga0c]overlay=x=10:y=20:eof_action=pass:format=auto:"
        "enable='gte(t,1.0000)*lt(t,2.0000)'[mga0o]"]
    parts2 = []
    motion_layer.append_graph(parts2, "v0", [(7, {"start": 1.0, "end": 2.0,
                                                  "layer": "above_captions"},
                                              Clip())], "above_captions",
                              30.0)
    assert parts2 == parts
    assert not compositor.needed({"fx": []}, [{"composite": None}])


def test_the_paint_library_joins_only_documents_that_ask():
    import motion_engine
    body = "<div>HELLO</div>"
    plain = motion_engine.build_document(body, params={"a": 1})
    assert "MG.paint" not in plain
    painted = motion_engine.build_document(
        body, params={"a": 1, "_paint": {"mode": "stroke", "width": 2}})
    assert "MG.paint" in painted and "-webkit-text-stroke" in painted
    assert "paint" in motion_engine.libs_used(body, {"_paint": {}})
    assert "paint" not in motion_engine.libs_used(body, {})


# ── the face region through 1A's provider (stubbed until it merges) ─────

@needs_ffmpeg
def test_a_face_region_draws_through_the_face_mask_provider(
        tmp_path, monkeypatch):
    """With no face provider the item is omitted with its reason; with one
    (the shape matte_track.face_mask will have), the effect lands inside
    that mask only."""
    import regions
    fake = types.ModuleType("w1c_fake_matte_track")

    def face_mask(plan, name, space, ctx):
        out = ctx.uid("face")
        ctx.parts.append(f"color=c=black:s={plan.W}x{plan.H}:r=25:d=1,"
                         "format=gray,drawbox=x=8:y=8:w=16:h=16:c=white:"
                         f"t=fill[{out}]")
        return out
    fake.face_mask = face_mask
    item = {"id": "f", "type": "region", "stage": "world", "start": 0.0,
            "end": 0.4, "effect": "negate", "region": {"shape": "face"}}
    plan = fxgraph.FxPlan(W=32, H=32, fps=25.0, program_s=1.0,
                          workdir=str(tmp_path))
    assert compositor.REGION.prepare(plan, item) is None
    plan.by_stage["world"].append(item)
    plan._compilers["region"] = compositor.REGION
    parts = ["color=c=0x204060:s=32x32:r=25:d=1,format=yuv420p[v0]"]
    monkeypatch.setitem(fxgraph.MASK_PEERS, "face", "w1c_no_module:x")
    out = fxgraph.apply_stage("world", parts, "v0", plan)
    assert out == "v0" and plan.rungs["f"] == "omit"
    monkeypatch.setitem(sys.modules, "w1c_fake_matte_track", fake)
    monkeypatch.setitem(fxgraph.MASK_PEERS, "face",
                        "w1c_fake_matte_track:face_mask")
    plan2 = fxgraph.FxPlan(W=32, H=32, fps=25.0, program_s=1.0,
                           workdir=str(tmp_path))
    plan2.by_stage["world"].append(item)
    plan2._compilers["region"] = compositor.REGION
    parts = ["color=c=0x204060:s=32x32:r=25:d=1,format=yuv420p[v0]"]
    out = fxgraph.apply_stage("world", parts, "v0", plan2)
    assert out != "v0"
    parts.append(f"[{out}]format=gray[o]")
    raw = subprocess.run(["ffmpeg", "-v", "error", "-filter_complex",
                          ";".join(parts), "-map", "[o]", "-frames:v", "3",
                          "-f", "rawvideo", "-"], capture_output=True,
                         check=True).stdout
    f = np.frombuffer(raw, np.uint8).reshape(-1, 32, 32)[1].astype(int)
    ref = f[0, 0]
    assert abs(f[16, 16] - ref) > 60              # negated inside the face
    assert f[0:6, :].max() == ref and f[26:, :].max() == ref
    assert regions.bbox({"shape": "face"}, 32, 32) is None


# ── degrade, never fail (review fixes) ──────────────────────────────────

def _media(tmp, name, *lavfi):
    path = os.path.join(tmp, name)
    subprocess.run(["ffmpeg", "-v", "error", "-y", *lavfi, path],
                   check=True, capture_output=True)
    return path


@needs_ffmpeg
def test_fill_media_without_a_picture_is_dropped_and_gifs_are_video(
        tmp_path):
    """A fill whose file has no video stream (an audio upload) or an
    animated GIF (whose demuxer refuses -loop) would fail the WHOLE render
    if opened as before: the first is dropped, the second plays as video."""
    tmp = str(tmp_path)
    gif = _media(tmp, "a.gif", "-f", "lavfi", "-i",
                 "testsrc=s=64x48:r=10:d=0.5")
    mp3 = _media(tmp, "a.m4a", "-f", "lavfi", "-i", "sine=d=0.5",
                 "-c:a", "aac")
    png = _media(tmp, "a.png", "-f", "lavfi", "-i",
                 "color=c=red:s=16x16:d=0.04", "-frames:v", "1")
    noext = os.path.join(tmp, "still")
    shutil.copy(png, noext)
    assert compositor.media_kind(gif) == "video"
    assert compositor.media_kind(mp3) is None
    assert compositor.media_kind(png) == "image"
    assert compositor.media_kind(noext) == "image"
    assert compositor.media_kind(os.path.join(tmp, "missing.mp4")) is None
    item = {"id": "t", "start": 0.0, "end": 1.0}
    keys = {"media/p/a.gif": gif, "media/p/a.m4a": mp3}
    args, notes = [], []
    f = compositor._resolve_fill({"kind": "asset",
                                  "asset_key": "media/p/a.m4a"}, item, {},
                                 64, 48, 25.0, args, 3, keys.get, None,
                                 notes)
    assert f is None and args == [] and "no picture" in notes[-1]
    f = compositor._resolve_fill({"kind": "asset", "asset_key":
                                  "media/p/a.gif", "loop": True}, item, {},
                                 64, 48, 25.0, args, 3, keys.get, None,
                                 notes)
    assert f["kind"] == "media" and "-loop" not in args
    # the input as opened, through the fill chain: ffmpeg accepts it
    parts = [f"[0:v]{f['chain']},format=rgba,trim=end_frame=5[o]"]
    subprocess.run(["ffmpeg", "-v", "error", *args, "-filter_complex",
                    ";".join(parts), "-map", "[o]", "-f", "null", "-"],
                   check=True, capture_output=True)


def test_an_unserved_clip_region_leaves_the_graphic_out(monkeypatch):
    """A face / card clip region the render cannot serve (no plan, no
    provider) omits the graphic and names it once — never draws it
    unclipped over the face — and appends nothing to the graph."""
    clip = _Clip("/x/c.mov", 10, 20, 40, 20)
    clip.composite = compositor.CompositeSide(
        W=270, H=480, fps=25.0, region={"mask": "face"})
    item = {"id": "mf", "start": 1.0, "end": 2.0,
            "composite": {"region": {"shape": "face"}}}
    motion_layer.LAST_WARNINGS.clear()
    parts = []
    for _ in range(2):                      # two builds, one warning
        assert motion_layer.overlay_clip(parts, "v0", 4, item, clip, 25.0,
                                         "mga0") == "v0"
    assert parts == []
    assert len(motion_layer.LAST_WARNINGS) == 1
    assert "'mf' not drawn" in motion_layer.LAST_WARNINGS[0]
    # a region that could not be built at prepare: likewise
    clip.composite = compositor.CompositeSide(W=270, H=480, fps=25.0)
    item2 = dict(item, id="mr", composite={"region": {
        "shape": "rect", "box": [0.1, 0.1, 0.2, 0.2]}})
    assert motion_layer.overlay_clip(parts, "v0", 4, item2, clip, 25.0,
                                     "mga0") == "v0" and parts == []
    motion_layer.LAST_WARNINGS.clear()


def test_a_composite_that_cannot_be_built_draws_the_plain_graphic(
        monkeypatch):
    def boom(*a, **k):
        raise KeyError("idx")
    monkeypatch.setattr(compositor, "_fill_stream", boom)
    clip = _Clip("/x/c.mov", 10, 20, 40, 20)
    clip.composite = compositor.CompositeSide(
        W=270, H=480, fps=25.0, fill={"kind": "colour", "hex": "#ff0000"})
    item = {"id": "mb", "start": 1.0, "end": 2.0,
            "composite": {"fill": {"kind": "colour", "colour": "#ff0000"}}}
    parts = []
    out = motion_layer.overlay_clip(parts, "v0", 7, item, clip, 30.0, "mga0")
    # exactly the legacy overlay, nothing of the failed composite
    assert out == "mga0o" and len(parts) == 2
    assert parts[0].startswith("[7:v]setpts=PTS-STARTPTS,format=rgba,tpad")


def test_prepare_inputs_survives_a_composite_prepare_failure(
        monkeypatch, tmp_path):
    """prepare_composites raising drops what it opened: the input indices
    the render goes on to use stay aligned."""
    import motion_engine
    import motion_templates

    class Clip:
        path, x, y, w, h, frames, empty = "/x/c.mov", 0, 0, 10, 10, 5, False
    monkeypatch.setattr(motion_engine, "render_jobs",
                        lambda jobs, out: [Clip() for _ in jobs])
    monkeypatch.setattr(motion_templates, "build_job",
                        lambda part, *a, **k: object())

    def bad(edl, inputs, *, args, next_idx, **kw):
        args += ["-i", "/x/fill.mp4"]
        raise RuntimeError("boom")
    monkeypatch.setattr(compositor, "prepare_composites", bad)
    edl = {"motion": [{"id": "m1", "template": "word_slam", "start": 0.0,
                       "end": 1.0, "params": {"text": "HI"},
                       "composite": {"blend": "difference"}}]}
    args = ["-i", "src.mp4"]
    inputs, nxt = motion_layer.prepare_inputs(
        edl, str(tmp_path), 270, 480, 25.0, 5.0, args, 1)
    assert args == ["-i", "src.mp4", "-i", "/x/c.mov"]
    assert nxt == 2 and [i for i, _it, _c in inputs] == [1]
