"""Wave 3 showcase pieces (W3-S1 .. W3-S5) on the Valmera Footage Library.

    cd worker && python -m benchmarks.refbench.w3_showcase S1 [S2 ...]

Every piece is a plain EDL (what apply_edit_batch would hold): world
definitions, anchored graphics, Wave 3 elements and layouts, and the Wave 1
devices they meet. Sidecars are measured locally first (the batch-lane
children, ``VALMERA_SIDECAR_DIR``), then the piece renders as a final.
Owner rules held: no music chosen, no sound effects, no zooms; black and
white only as a section; flash-safe; the corner mark and the end card are
the renderer's own (boards render without the outro; the brand tests pin
it). Boards: ~/Documents/Valmera/.valmera/refbench/boards/wave3/W3-S*.

Internal only: fixtures, references and renders never enter git or product
storage.
"""

import json
import math
import os
import subprocess
import sys
import time

import numpy as np

from . import vfl as vflmod
from . import w3
from .at3 import BOARDS, board_dir, frames_bgr, _v13_media


# ── plumbing ───────────────────────────────────────────────────────────

def _win(vid, name):
    return vflmod.window(vid, name)


def _measure(w, edl):
    """Every sidecar this EDL's world items read, measured and waited
    for: definitions (their own params), the landmarks anchored items
    name, and the person matte over the anchored / light windows."""
    import schemas
    import sidecars
    import world_defs
    from timeline import Timeline
    src = w3.source_ref(w)
    e = schemas.validate_edl(edl, src.duration_s).model_dump()
    tl = Timeline(e["keep"], e.get("inserts") or [], e.get("speed") or [])
    for d in world_defs.definitions(e).values():
        world_defs.ensure(d, tl, src)
    want = {}
    for m in e.get("motion") or []:
        a = m.get("anchor") or {}
        fam = str(a.get("to") or "").split(".")[0].split(":")[0]
        kind = {"face": "landmarks.face", "head": "landmarks.face",
                "hand": "landmarks.hand", "pose": "landmarks.pose"}.get(fam)
        lo = tl.out_to_src(float(m["start"]))
        hi = tl.out_to_src(max(float(m["start"]),
                               float(m["end"]) - 0.02))
        if lo is None or hi is None:
            continue
        rng = (max(0.0, min(lo, hi) - 0.6), max(lo, hi) + 0.6)
        if kind:
            want.setdefault(kind, []).append(rng)
        if a.get("occluders") and "person" in a["occluders"] or \
                m.get("layer") == "behind_subject" or \
                (m.get("template") == "el_particles" and
                 (m.get("params") or {}).get("emit") == "edge") or \
                fam == "head":
            want.setdefault("matte.person", []).append(rng)
    for it in e.get("fx") or []:
        if it.get("type") in ("light", "subject", "background") or \
                it.get("matte_weighted"):
            lo = tl.out_to_src(float(it["start"]))
            hi = tl.out_to_src(max(float(it["start"]),
                                   float(it["end"]) - 0.02))
            if lo is not None and hi is not None:
                want.setdefault("matte.person", []).append(
                    (max(0.0, min(lo, hi) - 0.6), max(lo, hi) + 0.6))
    import matte_track
    for kind, ranges in want.items():
        params = matte_track.sidecar_params() if kind == "matte.person" \
            else None
        sidecars.ensure(kind, src, ranges, priority="interactive",
                        params=params)
    sidecars._launcher().join(3600)
    sidecars._reset_caches()
    return e


def _render(w, edl, out, locals_=None, preview=False):
    import fxgraph
    import renderer
    e = _measure(w, edl)
    rows = []
    tok = fxgraph.collect(rows)
    t0 = time.time()
    wd = os.path.join(os.path.dirname(out), "wd_" + os.path.splitext(
        os.path.basename(out))[0])
    os.makedirs(wd, exist_ok=True)
    try:
        renderer.render_edl(e, w3.index_for(w), w["path"], out, wd,
                            preview, want_wm=False, suppress_outro=True,
                            asset_locals=locals_ or {})
    finally:
        fxgraph.stop_collecting(tok)
    return {"seconds": round(time.time() - t0, 1),
            "devices": fxgraph.merge_report(rows), "edl": e}


def _sheet(path, out, times, w=360):
    import cv2
    ims = []
    for t in times:
        fr = frames_bgr(path, t, 1, None, w)[0].copy()
        cv2.putText(fr, f"{t:.1f}s", (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (255, 255, 255), 2, cv2.LINE_AA)
        ims.append(fr)
    cv2.imwrite(out, np.hstack(ims))
    return out


def _concat(parts, out):
    lst = out + ".txt"
    with open(lst, "w") as fh:
        for p in parts:
            fh.write(f"file '{p}'\n")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe",
                    "0", "-i", lst, "-c:v", "libx264", "-crf", "18",
                    "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a",
                    "aac", "-movflags", "+faststart", out], check=True)
    os.unlink(lst)
    return out


def _head_px(w, edl, src_t):
    """The head's height in program px at source second ``src_t`` (so a
    head-registered graphic is authored at the head's own size)."""
    from anchors import query
    from .at3 import world_ctx
    import world_layer
    c, _e = world_ctx(w, edl)
    samples, _sh, meta = query.load("landmarks.face", c.source,
                                    [(src_t - 0.3, src_t + 0.3)])
    s = query.face_series(samples, "head", "any", meta)
    xy, size, _a, _q, ok = s.at(np.array([src_t]))
    if not ok[0]:
        return None
    t_prog = None
    A = None
    acc = 0.0
    for a, b in edl["keep"]:
        if a <= src_t <= b:
            t_prog = acc + (src_t - a)
            break
        acc += b - a
    if t_prog is not None:
        A = world_layer.program_affine(c.geometry("world"), t_prog, src_t)
    if A is None:
        return None
    return float(size[0]) * A[1, 1] * c.H


# ── custom compositions (raw html motion items, the MG runtime) ─────────

CONE_HTML = """<style>.mg-root{position:absolute;inset:0}</style>
<div class="mg-root"><svg id="s"></svg></div>
<script>
(() => {
  // the projection goes dark except one narrow cone of light, which
  // tightens until it holds a single word: the world of bits
  const W = MG.W, H = MG.H, D = MG.duration;
  const bw = 1000, bh = 1000, x0 = (W - bw) / 2, y0 = (H - bh) / 2;
  MG.box = [x0, y0, x0 + bw, y0 + bh];
  const s = MG.$('#s');
  s.setAttribute('width', W); s.setAttribute('height', H);
  s.setAttribute('viewBox', `0 0 ${W} ${H}`);
  const ax = x0 + bw * 0.66, tipY = y0 + bh * 0.9;
  s.innerHTML = `<defs><mask id="cm"><rect x="${x0}" y="${y0}" width="${bw}" height="${bh}" fill="white"/>
    <path id="hole" fill="black"/></mask>
    <filter id="soft"><feGaussianBlur stdDeviation="6"/></filter></defs>
    <rect id="dark" x="${x0}" y="${y0}" width="${bw}" height="${bh}" fill="#0f1220" mask="url(#cm)"/>
    <text id="k" x="${ax}" y="${y0 + bh * 0.71}" text-anchor="middle" font-family="Inter, sans-serif"
      font-weight="900" font-size="34" fill="#0f1220" letter-spacing="9">THE WORLD OF</text>
    <text id="t" x="${ax}" y="${y0 + bh * 0.855}" text-anchor="middle"
      font-family="Anton, 'Bebas Neue', Impact, sans-serif" font-size="150" fill="#0f1220">BITS</text>`;
  const hole = s.querySelector('#hole'), dark = s.querySelector('#dark');
  const t1 = s.querySelector('#t'), k1 = s.querySelector('#k');
  MG.frame(t => {
    const a = MG.clamp(t / 0.5), e = 1 - Math.pow(1 - MG.clamp((t - 0.2) / 1.7), 3);
    const out = MG.clamp((D - t) / 0.35);
    const top = MG.lerp(bw * 0.5, bw * 0.24, e), bot = MG.lerp(bw * 0.3, bw * 0.15, e);
    hole.setAttribute('d', `M${ax - top} ${y0 - 2} L${ax + top} ${y0 - 2} L${ax + bot} ${tipY} L${ax - bot} ${tipY} Z`);
    dark.setAttribute('opacity', (0.86 * a * out).toFixed(3));
    t1.setAttribute('opacity', (MG.clamp((t - 2.75) / 0.2) * out).toFixed(3));
    k1.setAttribute('opacity', (MG.clamp((t - 2.45) / 0.3) * out * 0.9).toFixed(3));
  });
})();
</script>"""

CHART_HTML = """<style>.mg-root{position:absolute;inset:0}</style>
<div class="mg-root"><svg id="s"></svg></div>
<script>
(() => {
  const W = MG.W, H = MG.H, D = MG.duration;
  const bw = 640, bh = 400, x0 = (W - bw) / 2, y0 = (H - bh) / 2;
  MG.box = [x0, y0, x0 + bw, y0 + bh];
  const s = MG.$('#s');
  s.setAttribute('width', W); s.setAttribute('height', H);
  s.setAttribute('viewBox', `0 0 ${W} ${H}`);
  let grid = '';
  for (let i = 1; i < 6; i++) grid += `<line x1="${x0 + i * bw / 6}" y1="${y0 + 30}" x2="${x0 + i * bw / 6}" y2="${y0 + bh - 30}" stroke="#fff" stroke-opacity=".18" stroke-width="2"/>`;
  for (let i = 1; i < 4; i++) grid += `<line x1="${x0 + 30}" y1="${y0 + i * bh / 4}" x2="${x0 + bw - 30}" y2="${y0 + i * bh / 4}" stroke="#fff" stroke-opacity=".18" stroke-width="2"/>`;
  s.innerHTML = `<rect x="${x0}" y="${y0}" width="${bw}" height="${bh}" rx="28" fill="#0b0d14" fill-opacity=".42"
      stroke="#fff" stroke-opacity=".55" stroke-width="3"/>${grid}
    <polyline id="ln" fill="none" stroke="#7CF2C4" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/>
    <circle id="dot" r="16" fill="#7CF2C4"/>
    <text x="${x0 + 36}" y="${y0 + 60}" font-family="Inter, sans-serif" font-weight="900" font-size="36"
      fill="#fff" letter-spacing="3">HISTORICAL RATE</text>`;
  const ln = s.querySelector('#ln'), dot = s.querySelector('#dot');
  const N = 48, pts = [];
  for (let i = 0; i <= N; i++) {
    const u = i / N;
    const y = 0.86 - 0.62 * u + 0.025 * Math.sin(u * 23.0) * (1 - u * 0.4);
    pts.push([x0 + 50 + u * (bw - 100), y0 + y * bh]);
  }
  MG.frame(t => {
    const e = 1 - Math.pow(1 - MG.clamp((t - 0.15) / 1.4), 2);
    const out = MG.clamp((D - t) / 0.3), a = MG.clamp(t / 0.25) * out;
    const n = Math.max(1, Math.round(e * N));
    ln.setAttribute('points', pts.slice(0, n + 1).map(p => p[0].toFixed(1) + ',' + p[1].toFixed(1)).join(' '));
    dot.setAttribute('cx', pts[n][0].toFixed(1)); dot.setAttribute('cy', pts[n][1].toFixed(1));
    s.setAttribute('opacity', a.toFixed(3));
  });
})();
</script>"""


def wire_html(size_px):
    """A head-sized wireframe (a shader's own view of the face): latitude
    and longitude rings of an ellipsoid turning slowly, a scan line, cyan
    light — registered to the head with follow shape."""
    h = max(240, int(size_px))
    w = int(h * 0.78)
    return """<style>.mg-root{position:absolute;inset:0}</style>
<div class="mg-root"><canvas id="c"></canvas></div>
<script>
(() => {
  const W = MG.W, H = MG.H, D = MG.duration;
  const bw = %d, bh = %d, x0 = (W - bw) / 2, y0 = (H - bh) / 2;
  MG.box = [x0 - 24, y0 - 24, x0 + bw + 24, y0 + bh + 24];
  const cv = MG.$('#c'); cv.width = W; cv.height = H;
  const g = cv.getContext('2d');
  const cx = W / 2, cy = H / 2, rx = bw * 0.47, ry = bh * 0.49;
  MG.frame(t => {
    g.clearRect(0, 0, W, H);
    const a = MG.clamp(t / 0.35) * MG.clamp((D - t) / 0.4);
    const rot = t * 0.9;
    g.lineWidth = 2.2; g.shadowColor = 'rgba(80,220,255,.9)'; g.shadowBlur = 12;
    for (let i = 1; i < 12; i++) {            // latitude rings
      const v = -1 + i / 6, y = cy + v * ry * 0.98, r = Math.sqrt(Math.max(0, 1 - v * v));
      g.strokeStyle = `rgba(110,230,255,${(0.55 * a).toFixed(3)})`;
      g.beginPath(); g.ellipse(cx, y, rx * r, rx * r * 0.18, 0, 0, Math.PI * 2); g.stroke();
    }
    for (let j = 0; j < 10; j++) {            // longitude meridians, turning
      const ph = rot + j * Math.PI / 10, s = Math.cos(ph);
      g.strokeStyle = `rgba(110,230,255,${(0.6 * a * (0.35 + 0.65 * Math.abs(Math.sin(ph)))).toFixed(3)})`;
      g.beginPath(); g.ellipse(cx, cy, Math.abs(rx * s), ry, 0, 0, Math.PI * 2); g.stroke();
    }
    const sy = cy - ry + ((t * 0.55) %% 1) * ry * 2;   // the scan line
    const grd = g.createLinearGradient(0, sy - 30, 0, sy + 30);
    grd.addColorStop(0, 'rgba(120,240,255,0)'); grd.addColorStop(0.5, `rgba(160,250,255,${(0.75 * a).toFixed(3)})`);
    grd.addColorStop(1, 'rgba(120,240,255,0)');
    g.fillStyle = grd; g.fillRect(cx - rx, sy - 30, rx * 2, 60);
  });
})();
</script>""" % (w, h)


# ── W3-S1 "Type in the room" ───────────────────────────────────────────

def s1():
    bd = board_dir("W3-S1")
    parts, report = [], {}
    # (a) Thiel: a narrow cone of progress printed into the projection,
    # then FLYING CARS taking off from behind his laptop and bottle
    wa = _win("V02", "thiel_flying_cars")
    a = {"keep": [[27.8, 31.9], [42.5, 45.3]],
         "frame": {"ratio": "9:16", "mode": "crop", "focus_x": 0.5,
                   "focus_y": 0.5},
         "fx": [
             {"id": "proj", "type": "plane", "stage": "world", "start": 0.2,
              "end": 4.1, "src_t": 28.6, "surface": "screen",
              "quad": [[0.0, 0.0], [1.0, 0.0], [1.0, 0.555], [0.0, 0.585]],
              "label": "projection", "device": "plane_type"},
             {"id": "laptop", "type": "object", "stage": "world",
              "start": 4.1, "end": 6.9, "label": "laptop",
              "prompt": {"src_t": 43.1, "box": [0.398, 0.774, 0.664, 1.0]},
              "device": "object_occluder"},
             {"id": "bottle", "type": "object", "stage": "world",
              "start": 4.1, "end": 6.9, "label": "water bottle",
              "prompt": {"src_t": 43.1,
                         "box": [0.402, 0.811, 0.455, 0.963]},
              "device": "object_occluder"}],
         "motion": [
             {"id": "cone", "template": "html", "html": CONE_HTML,
              "start": 0.55, "end": 4.05, "layer": "below_captions",
              "anchor": {"to": "plane:proj", "occluders": ["person"],
                         "place": [0.335, 0.0, 0.665, 1.0]},
              "composite": {"blend": "multiply"}},
             {"id": "fly", "template": "word_slam", "start": 4.55,
              "end": 6.85, "layer": "above_captions",
              "params": {"text": "FLYING CARS", "entrance": "rise",
                         "role": "condensed", "width": 0.92, "y": 0.665},
              "anchor": {"to": "screen",
                         "occluders": ["object:laptop", "object:bottle"]}}],
         "captions": {"enabled": False}}
    oa = os.path.join(bd, "s1a_thiel.mp4")
    report["a"] = _render(wa, a, oa)
    parts.append(oa)
    # (b) Elon: the sum of human intelligence written between him and the
    # boom mic — the mic stays in front (depth measured)
    wb = _win("V06", "boom_mic")
    b = {"keep": [[39.2, 45.6]],
         "frame": {"ratio": "9:16", "mode": "crop", "focus_x": 0.46,
                   "focus_y": 0.5},
         "fx": [{"id": "mic", "type": "object", "stage": "world",
                 "start": 0.0, "end": 5.8, "label": "boom mic",
                 "prompt": {"src_t": 42.0,
                            "box": [0.43, 0.48, 0.51, 0.71]},
                 "device": "object_occluder"}],
         "motion": [{"id": "sum", "template": "word_slam", "start": 2.1,
                     "end": 5.7, "layer": "above_captions",
                     "params": {"text": "THE SUM OF HUMAN INTELLIGENCE",
                                "entrance": "rise", "role": "condensed",
                                "width": 0.84, "y": 0.6},
                     "anchor": {"to": "screen",
                                "occluders": ["object:mic"]}}],
         "captions": {"enabled": False}}
    ob = os.path.join(bd, "s1b_elon.mp4")
    report["b"] = _render(wb, b, ob)
    parts.append(ob)
    # (c) Altman: "a lot, fast" rides the fingertip that counts it; then
    # the historical rate appears in the window between his hands
    wc = _win("V12", "gestures")
    c = {"keep": [[8.3, 16.0]],
         "frame": {"ratio": "9:16", "mode": "crop"},
         "motion": [
             {"id": "card", "template": "glass_card", "start": 1.25,
              "end": 3.0, "layer": "above_captions",
              "params": {"value": "A LOT", "title": "in a short time",
                         "width": 0.3},
              "anchor": {"to": "hand.index_tip", "pin": [0.5, 0.72],
                         "lost": "hide"}},
             # the steady line rides his chest; the gesturing hands pass
             # in front of it (pose anchor + hands occluder)
             {"id": "rate", "template": "html", "html": CHART_HTML,
              "start": 5.5, "end": 7.65, "layer": "above_captions",
              "anchor": {"to": "pose.chest", "pin": [0.92, 0.38],
                         "occluders": ["hands"], "lost": "hold"}}],
         "captions": {"enabled": False}}
    oc = os.path.join(bd, "s1c_altman.mp4")
    report["c"] = _render(wc, c, oc)
    parts.append(oc)
    out = _concat(parts, os.path.join(bd, "W3-S1_type_in_the_room.mp4"))
    _sheet(oa, os.path.join(bd, "s1a.png"), [1.0, 2.2, 3.6, 5.0, 6.2])
    _sheet(ob, os.path.join(bd, "s1b.png"), [1.0, 2.6, 3.8, 5.0])
    _sheet(oc, os.path.join(bd, "s1c.png"), [1.6, 2.4, 6.0, 6.8, 7.4])
    return out, report


# ── W3-S5 "Figure to light" ────────────────────────────────────────────

def s5():
    bd = board_dir("W3-S5")
    w = _win("V09", "jensen_windows")
    keep = [[9.5, 19.6], [32.4, 36.4]]
    frame = {"ratio": "9:16", "mode": "crop", "focus_x": 0.55,
             "focus_y": 0.5}
    hp = _head_px(w, {"keep": keep, "frame": frame}, 11.0) or 520
    e = {"keep": keep, "frame": frame,
         "motion": [
             {"id": "wire", "template": "html", "html": wire_html(hp),
              "start": 1.2, "end": 4.4, "layer": "above_captions",
              "anchor": {"to": "head", "pin": [0.5, 0.5],
                         "follow": ["position", "scale", "rotation",
                                    "shape"]},
              "composite": {"blend": "screen"}},
             {"id": "bolt", "template": "el_lightning", "start": 7.5,
              "end": 8.6, "layer": "above_captions",
              "params": {"x0": 0.72, "y0": -0.05, "x1": 0.56, "y1": 0.3,
                         "at": ["0.1", "0.42"], "colour": "#9FD8FF"},
              "composite": {"blend": "screen"}},
             {"id": "emb", "template": "el_particles", "start": 12.0,
              "end": 14.1, "params": {"kind": "embers", "emit": "edge",
                                      "count": 560, "palette": "warm",
                                      "seed": 7},
              "composite": {"blend": "screen"}}],
         "fx": [{"id": "lt1", "type": "light", "stage": "world",
                 "start": 12.0, "end": 14.1, "kind": "dissolve",
                 "duration_s": 1.9, "colour": "#ffd8a0",
                 "device": "light_dissolve"}],
         "captions": {"enabled": False}}
    out = os.path.join(bd, "W3-S5_figure_to_light.mp4")
    rep = _render(w, e, out)
    _sheet(out, os.path.join(bd, "s5.png"), [1.6, 2.8, 4.0, 7.6, 12.4,
                                              13.6])
    return out, rep


# ── W3-S2 "Event recap" ────────────────────────────────────────────────

def s2():
    bd = board_dir("W3-S2")
    w = _win("V13", "dragon_stage_walk")
    vids, stills, locals_ = _v13_media(30, os.path.join(bd, "stills"))
    tiles = (vids + stills)[:36]
    hero = next((i for i, t in enumerate(tiles) if "dragon_unveil" in t), 0)
    beats = [round(0.1 + 0.075 * i, 3) for i in range(len(tiles))]
    e = {"keep": [[2.0, 14.5]],
         "frame": {"ratio": "9:16", "mode": "crop", "focus_x": 0.4,
                   "focus_y": 0.5},
         "fx": [
             # a black-and-white SECTION (the walk), colour back on the
             # cut to the floor type, with one white silhouette hit there
             {"id": "mono", "type": "grade_window", "stage": "world",
              "start": 3.6, "end": 6.0, "saturation": 0.0,
              "ramp_out_f": 4, "device": "grade_section"},
             {"id": "hit", "type": "subject", "stage": "world",
              "start": 6.0, "end": 6.07, "kind": "fill",
              "colour": "white", "decay_f": 3,
              "device": "subject_fill_flash"},
             {"id": "floor", "type": "plane", "stage": "world",
              "start": 6.0, "end": 9.6, "src_t": 8.0, "surface": "floor",
              "quad": [[0.14, 0.6], [0.6, 0.58], [0.74, 0.98],
                       [0.02, 0.99]], "device": "plane_type"}],
         "motion": [
             {"id": "mz", "template": "mosaic", "start": 0.0, "end": 3.6,
              "params": {"tiles": tiles, "at": [str(b) for b in beats],
                         "collapse_at": 2.6, "hero": hero,
                         "title": "DRAGON", "kicker": "SPACEX",
                         "backdrop": "#07070A"}},
             {"id": "hud", "template": "hud", "start": 3.6, "end": 12.5,
              "layer": "above_captions", "mute_captions": False,
              "params": {"tl": "HAWTHORNE", "tr": "UNVEIL",
                         "bl": "DRAGON V2", "rec": True, "timecode": True,
                         "brackets": True,
                         "mark": ["0.09", "0.05", "0.5", "0.13"]}},
             # the name laid on the stage floor he walks on: his legs
             # pass over it, the camera's drift carries it
             {"id": "fl", "template": "word_slam", "start": 6.0,
              "end": 9.5, "layer": "below_captions",
              "params": {"text": "DRAGON V2", "entrance": "rise",
                         "role": "condensed", "width": 0.95},
              "anchor": {"to": "plane:floor", "occluders": ["person"],
                         "place": [0.34, 0.08, 0.98, 0.95]}},
             {"id": "sw", "template": "scroll_wall", "start": 9.6,
              "end": 12.5, "params": {"tiles": vids + stills, "rows": 5,
                                      "speed": 0.16,
                                      "title": "BUILT HERE"}}],
         "captions": {"enabled": False}}
    out = os.path.join(bd, "W3-S2_event_recap.mp4")
    rep = _render(w, e, out, locals_=locals_)
    _sheet(out, os.path.join(bd, "s2.png"), [0.8, 2.0, 3.2, 4.6, 6.6,
                                              8.2, 10.6])
    return out, rep


# ── W3-S3 "Edit about the edit" ────────────────────────────────────────

def s3():
    bd = board_dir("W3-S3")
    import copy
    wa = _win("V02", "thiel_flying_cars")
    s1_edl = json.load(open(os.path.join(board_dir("W3-S1"),
                                         "s1a_edl.json")))
    e = copy.deepcopy(s1_edl)
    e["motion"] = list(e.get("motion") or []) + [
        {"id": "bd", "template": "breakdown", "start": 0.0, "end": 6.9,
         "layer": "above_captions",
         "params": {"title": "HOW THIS WAS MADE", "height": 0.27,
                    "position": "top"}}]
    out = os.path.join(bd, "W3-S3_edit_about_the_edit.mp4")
    rep = _render(wa, e, out)
    _sheet(out, os.path.join(bd, "s3.png"), [1.0, 2.6, 5.0, 6.4])
    return out, rep


# ── W3-S4 "Studio on a device" ─────────────────────────────────────────

def s4():
    bd = board_dir("W3-S4")
    w = _win("V13", "colossus_aisle")
    ui = os.path.expanduser(
        "~/Documents/Valmera/.valmera/fixtures/FX-UI/valmera_io.mp4")
    e = {"keep": [[2.0, 9.0]],
         "frame": {"ratio": "9:16", "mode": "crop"},
         "fx": [{"id": "bg", "type": "region", "stage": "pre_captions",
                 "start": 0.0, "end": 7.0, "effect": "blur",
                 "region": {"shape": "rect", "box": [0, 0, 1, 1]}},
                {"id": "dim", "type": "grade_window", "stage": "world",
                 "start": 0.0, "end": 7.0, "ev": -0.55, "saturation": 0.85}],
         "motion": [{"id": "ds", "template": "device_stage", "start": 0.0,
                     "end": 7.0,
                     "params": {"screen": "asset:local/ui.mp4",
                                "device": "laptop", "title": "STUDIO",
                                "breakout": ["0.06", "0.12", "0.62",
                                             "0.42"],
                                "breakout_at": 3.2}}],
         "captions": {"enabled": False}}
    out = os.path.join(bd, "W3-S4_studio_on_a_device.mp4")
    rep = _render(w, e, out, locals_={"local/ui.mp4": ui})
    _sheet(out, os.path.join(bd, "s4.png"), [0.8, 2.4, 3.8, 5.6])
    return out, rep


PIECES = {"S1": s1, "S2": s2, "S3": s3, "S4": s4, "S5": s5}


def main(argv):
    res_path = os.path.join(BOARDS, "showcase.json")
    try:
        res = json.load(open(res_path))
    except (OSError, ValueError):
        res = {}
    for k in argv or list(PIECES):
        out, rep = PIECES[k]()
        reps = rep if isinstance(rep, dict) and "seconds" not in rep \
            else {"main": rep}
        res[k] = {"out": out, "renders": {
            n: {"seconds": r["seconds"],
                "devices": [{x: d.get(x) for x in ("id", "device", "rung",
                                                   "reason")}
                            for d in r["devices"]]}
            for n, r in reps.items()}}
        if k == "S1":
            json.dump(reps["a"]["edl"], open(os.path.join(
                board_dir("W3-S1"), "s1a_edl.json"), "w"))
        json.dump(res, open(res_path, "w"), indent=1, default=str)
        print(k, out, json.dumps(res[k]["renders"], default=str)[:600],
              flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
