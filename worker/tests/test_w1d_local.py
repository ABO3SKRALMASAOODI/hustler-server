"""Track 1D acceptance (ROADMAP 1D, AT-1D-1 … AT-1D-7 and perf) on the
Valmera Footage Library — LOCAL ONLY (fixtures never reach CI or git).

Each AT renders the fixture window through the real renderer, LOSSLESS,
against a control render of the same EDL without the device, and measures
the bar the roadmap names. Devices are written the way an agent or a stale
MCP client writes them: through the 1D verbs on a tool context, or as
apply_edit_batch operations (refbench cases/1D/ hold the same operations).

``measure_*`` helpers return plain dicts so the internal board driver can
print the numbers next to the reference stills; the tests assert the bars.
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
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

from localfix import fixture, local_only                   # noqa: E402
from w1d_support import (audio_md5, changed, frame_md5s, frames,  # noqa
                         lossless, probe, render)

V02 = fixture("V02", "thiel_flying_cars.mp4")
V03 = fixture("V03", "jobs_fonts.mp4")
V13 = [fixture("V13", n) for n in ("dragon_unveil.mp4",
                                   "starship_launch_4k60.mp4",
                                   "optimus_walk.mp4")]
V12 = fixture("V12", "walkon.mp4")

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"),
                                reason="needs ffmpeg")


def _win(vfl_id, name):
    from benchmarks.refbench import engine, vfl
    win = vfl.window(vfl_id, name)
    return win, engine.asr_words(win), engine.index_for(win)


class _DB:
    def __init__(self, keys=()):
        self.keys = {k: {"duration_s": None} for k in keys}

    def run(self, fn, *a):
        return self.keys


class ToolCtx:
    """The tool context the 1D verbs need, on a fixture window."""

    def __init__(self, edl, index, duration, user_message="", keys=()):
        import schemas
        self.db, self.project_id, self.duration = _DB(keys), 1, duration
        self.index = index
        self.user_message = user_message
        self._asset_perception = {}
        self.edl = schemas.validate_edl(copy.deepcopy(edl),
                                        duration).model_dump()
        self.v = 1

    def latest_edl(self):
        return {"version": self.v, "json": copy.deepcopy(self.edl)}

    def write_edl(self, edl, desc):
        self.edl, self.v = edl, self.v + 1
        return f"EDL v{self.v - 1} -> v{self.v}: {desc}"

    def proxy_path(self):
        raise RuntimeError("fixture: no proxy")


_PERCEPTION = {}


def _no_perception(monkeypatch, path=None):
    """The verbs read the main video's perception sidecar
    (agent_tools._get_perception: the index row in production). Here it is
    computed from the fixture itself (perception.analyze_audio: the same
    analysis, file clock = the words' clock); without a path, absent."""
    import agent_tools

    def get(ctx):
        if not path:
            raise RuntimeError("no perception")
        if path not in _PERCEPTION:
            import perception
            _PERCEPTION[path] = perception.analyze_audio(path)
        return _PERCEPTION[path]
    monkeypatch.setattr(agent_tools, "_get_perception", get)


def _pair(edl, fx, src, out_dir, name, *, idx, duration, preview=False,
          want_wm=False, asset_locals=None):
    """(control path, fx path, report): lossless renders with and without
    the devices."""
    c = os.path.join(out_dir, f"{name}_ctrl.mp4")
    o = os.path.join(out_dir, f"{name}_fx.mp4")
    if not os.path.exists(c):
        render(edl, src, c, idx=idx, preview=preview, want_wm=want_wm,
               duration=duration, asset_locals=asset_locals)
    _d, rep = render(dict(edl, fx=fx), src, o, idx=idx, preview=preview,
                     want_wm=want_wm, duration=duration,
                     asset_locals=asset_locals)
    return c, o, rep


def _runs(frames_list):
    """[(first, length)] runs of consecutive frame indexes."""
    out = []
    for f in sorted(frames_list):
        if out and f == out[-1][0] + out[-1][1]:
            out[-1][1] += 1
        else:
            out.append([f, 1])
    return [tuple(r) for r in out]


# ── AT-1D-1: accelerating 1-frame negatives on the prosody grid ─────────

def measure_at1(out_dir, monkeypatch, opted_in=True):
    win, words, idx = _win("V02", "thiel_flying_cars")
    edl = {"keep": [[34.0, 46.5]], "captions": {"enabled": False},
           "frame": {"ratio": "9:16", "mode": "crop", "focus_x": 0.41,
                     "focus_y": 0.31}}
    dur = float(win["file_span"][1] - win["file_span"][0])
    _no_perception(monkeypatch, win["path"])
    from tools_ext import punctuation_tools as P
    msg = ("accelerating negative flashes into the payoff, strobe it"
           if opted_in else "accelerating negatives into the payoff")
    ctx = ToolCtx(edl, dict(idx, words=words), dur, user_message=msg)
    res = P.add_accent(ctx, "negative", on="word", density="accelerate",
                       start=1.5, end=10.15, count=9)
    assert res.startswith("EDL"), res
    fx = [i for i in ctx.edl["fx"] if i["type"] == "accent"]
    lossless(monkeypatch)
    c, o, rep = _pair(edl, fx, win["path"], out_dir,
                      "at1" + ("" if opted_in else "_safe"), idx=idx,
                      duration=dur, want_wm=True)
    fps = 29.97
    import fxprims
    planned = [fxprims.frame_at(i["start"], fps) for i in fx]
    _, a = frames(c)
    y1, b = frames(o)
    got = changed(a, b)
    runs = _runs(got)
    gaps = [q - p for p, q in zip(planned, planned[1:])]
    # the watermark never inverts (it is composited after every stage)
    import renderer
    H, W = y1.shape[1:]
    g = renderer.watermark_geometry(W, H)
    y0, _ = frames(c)
    roi = (slice(g["margin_y"], g["margin_y"] + g["rh"]),
           slice(g["margin_x"], g["margin_x"] + g["rw"]))
    wm_alpha = _robot_alpha(W, H) if g else None
    wm_diff = max(int(np.abs(y1[f][roi].astype(int) - y0[f][roi])[
        wm_alpha].max()) for f in got) if got else 0
    import flash_safety
    return {"result": res.splitlines()[0][:160], "planned": planned,
            "rendered_runs": runs, "gaps_f": gaps,
            "exact_1_frame": all(n == 1 for _f, n in runs),
            "on_planned_frame": [f for f, _n in runs] == planned,
            "monotone_decreasing": all(g2 < g1 for g1, g2 in
                                       zip(gaps, gaps[1:])),
            "watermark_robot_max_diff": wm_diff,
            "rungs": sorted({r["rung"] for r in rep}),
            "flash": flash_safety.assess(ctx.edl,
                                         request_text=msg)}


def _robot_alpha(W, H):
    import renderer
    g = renderer.watermark_geometry(W, H)
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", renderer.robot_path(), "-vf",
         f"scale={g['rw']}:{g['rh']},format=rgba", "-f", "rawvideo", "-"],
        capture_output=True, check=True).stdout
    a = np.frombuffer(raw, np.uint8).reshape(g["rh"], g["rw"], 4)[..., 3]
    core = a == 255
    return (core & np.roll(core, 1, 0) & np.roll(core, -1, 0)
            & np.roll(core, 1, 1) & np.roll(core, -1, 1))


@local_only(V02)
def test_at_1d_1_accelerating_negatives(tmp_path, monkeypatch):
    m = measure_at1(str(tmp_path), monkeypatch)
    assert len(m["planned"]) >= 6, m
    assert m["exact_1_frame"] and m["on_planned_frame"], m
    assert m["monotone_decreasing"], m
    assert m["watermark_robot_max_diff"] == 0, m
    assert m["rungs"] == ["full"]
    assert m["flash"]["contains_flashing"] is True     # opted in: flagged


@local_only(V02)
def test_at_1d_1_without_the_ask_flash_safety_thins_the_tail(
        tmp_path, monkeypatch):
    m = measure_at1(str(tmp_path), monkeypatch, opted_in=False)
    assert m["flash"]["peak_per_s"] <= 3 and not m["flash"]["over_limit"]
    assert m["exact_1_frame"] and m["on_planned_frame"], m


# ── AT-1D-2: white hits inside the rounded card on stressed syllables ───

def measure_at2(out_dir, monkeypatch):
    win, words, idx = _win("V03", "jobs_fonts")
    dur = float(win["file_span"][1] - win["file_span"][0])
    card = {"id": "pc", "start": 0.0, "end": 7.9, "entrance": "none",
            "exit": "none"}
    edl = {"keep": [[19.5, 27.4]], "captions": {"enabled": False},
           "frame": {"ratio": "9:16"},
           "effects": {"picture_cards": [card]}}
    _no_perception(monkeypatch, win["path"])
    from tools_ext import punctuation_tools as P
    ctx = ToolCtx(edl, dict(idx, words=words), dur)
    res = P.add_accent(ctx, "white_hit", on="stress", density="pulse",
                       region="card", count=9)
    assert res.startswith("EDL"), res
    fx = [i for i in ctx.edl["fx"] if i["type"] == "accent"]
    lossless(monkeypatch)
    c, o, rep = _pair(ctx.edl | {"fx": []}, fx, win["path"], out_dir, "at2",
                      idx=idx, duration=dur)
    fps = 29.97
    import fxprims
    import wipe_masks
    y0, a = frames(c)
    y1, b = frames(o)
    H, W = y0.shape[1:]
    spec = ctx.edl["effects"]["picture_cards"][0]
    inside = np.asarray(wipe_masks.card_mask(W, H, spec))
    out_mask = inside == 0
    got = changed(a, b)
    outside = max(int(np.abs(y1[f].astype(int) - y0[f])[out_mask].max())
                  for f in got) if got else 0
    planned = [fxprims.frame_at(i["start"], fps) for i in fx]
    targets = []
    for it in fx:
        w = words[it["bind"]["ref"]]
        from timeline import Timeline
        tl = Timeline(ctx.edl["keep"])
        targets.append(fxprims.frame_at(tl.src_to_out(w["t0"]), fps))
    peaks, decays, curves = [], [], []
    full = inside == 255

    def strength(f):
        bb = y0[f].astype(float)
        m = full & ((235 - bb) > 30)
        return float(np.median((y1[f][m] - bb[m]) / (235 - bb[m])))
    for f in planned:
        peaks.append(strength(f))
        # the measured fall-off after the peak, frame by frame (a 2-frame
        # peak or a missing decay step shows here, not in a changed-frame
        # count): every frame that still differs, and its strength
        dec, curve = 0, []
        for k in range(1, 8):
            if f + k < len(y1) and np.abs(y1[f + k].astype(int)
                                          - y0[f + k])[full].max() > 2:
                dec += 1
                curve.append(round(strength(f + k), 3))
        decays.append(dec)
        curves.append(curve)
    import qa_metrics
    land = qa_metrics.land_err_f(events=[r[0] for r in _runs(got)],
                                 targets=targets)
    return {"result": res.splitlines()[0][:160], "hits": len(fx),
            "outside_card_max_abs_diff": outside,
            "peak_strength": [round(p, 3) for p in peaks],
            "decay_frames": decays, "decay_curves": curves,
            "land_err_f": land["value"],
            "rungs": sorted({r["rung"] for r in rep})}


@local_only(V03)
def test_at_1d_2_card_clipped_white_hits(tmp_path, monkeypatch):
    m = measure_at2(str(tmp_path), monkeypatch)
    assert m["hits"] >= 4, m
    assert m["outside_card_max_abs_diff"] == 0, m
    assert all(p >= 0.97 for p in m["peak_strength"]), m
    assert all(3 <= d <= 5 for d in m["decay_frames"]), m
    # a 1-frame peak, then the ref-02 fall-off itself, step by step
    from fx_schemas.accents import decay_strengths
    want = [round(v, 3) for v in decay_strengths(1.0, 5)]   # the default
    for curve in m["decay_curves"]:
        assert np.allclose(curve, want, atol=0.04), (curve, want)
    assert m["land_err_f"] <= 1, m
    assert m["rungs"] == ["full"]


# ── AT-1D-3: 2-frame strobe of a registered variant set ─────────────────

def make_variant_stills(out_dir, src, t=8.0):
    """Stand-in for 1E's make_variant_set (not merged in this branch):
    photo, negative, mono line-art, thermal and posterized stills from ONE
    frame, all the same size (registration is by construction)."""
    vf = {"photo": "null", "negative": "negate",
          "line": "format=gray,edgedetect=low=0.08:high=0.2,negate",
          "thermal": "format=gray,pseudocolor=preset=turbo",
          "posterize": "lutrgb=r='floor(val/64)*64':g='floor(val/64)*64'"
                       ":b='floor(val/64)*64'"}
    out = {}
    for k, f in vf.items():
        p = os.path.join(out_dir, f"variant_{k}.png")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}",
                        "-i", src, "-frames:v", "1", "-vf",
                        f"scale=1280:-2,{f},format=rgb24", p], check=True)
        out[f"media/p1/variant_{k}.png"] = p
    return out


def measure_at3(out_dir, monkeypatch):
    src = V13[2]
    keys = make_variant_stills(out_dir, src)
    edl = {"keep": [[3.0, 9.0]], "captions": {"enabled": False},
           "frame": {"ratio": "9:16"}}
    idx = {"words": [], "video": {"duration": 22.0, "width": 3840,
                                  "height": 2160, "fps": 24.0}}
    st = {"id": "st", "type": "strobe", "stage": "pre_captions",
          "start": 1.0, "end": 2.25, "fit": "register",
          "device": "strobe_frames",
          "frames": [{"source": {"kind": "asset", "asset_key": k},
                      "hold_f": 2} for k in sorted(keys)]}
    lossless(monkeypatch)
    c, o, rep = _pair(edl, [st], src, out_dir, "at3", idx=idx,
                      duration=22.0, asset_locals=keys)
    md5 = frame_md5s(o)
    _, a = frames(c)
    y1, b = frames(o)
    got = changed(a, b)
    runs = []
    for f in got:
        if runs and md5[f] == md5[runs[-1][0]] and f == runs[-1][0] + \
                runs[-1][1]:
            runs[-1][1] += 1
        else:
            runs.append([f, 1])
    # registration: edges of consecutive images align (phase correlation)
    offs = []
    starts = [r[0] for r in runs]
    for p, q in zip(starts, starts[1:]):
        offs.append(_shift(_edges(y1[p]), _edges(y1[q])))
    return {"changed": (got[0], got[-1], len(got)) if got else None,
            "hold_lengths": [n for _f, n in runs],
            "registration_px": [round(max(abs(dx), abs(dy)), 2)
                                for dx, dy in offs],
            "audio_identical": audio_md5(o) == audio_md5(c),
            "rungs": sorted({r["rung"] for r in rep})}


def _edges(y):
    y = y.astype(float)
    gx = np.abs(np.diff(y, axis=1))[:-1]
    gy = np.abs(np.diff(y, axis=0))[:, :-1]
    e = gx + gy
    return (e - e.mean()) / (e.std() + 1e-6)


def _shift(a, b):
    """(dx, dy) of b against a by phase correlation (integer px)."""
    fa, fb = np.fft.fft2(a), np.fft.fft2(b)
    r = fa * np.conj(fb)
    r /= np.abs(r) + 1e-9
    c = np.abs(np.fft.ifft2(r))
    dy, dx = np.unravel_index(np.argmax(c), c.shape)
    H, W = a.shape
    return (dx if dx <= W // 2 else dx - W, dy if dy <= H // 2 else dy - H)


@local_only(V13[2])
def test_at_1d_3_variant_strobe_two_frames_each_registered(tmp_path,
                                                           monkeypatch):
    m = measure_at3(str(tmp_path), monkeypatch)
    assert m["rungs"] == ["full"], m
    assert all(n == 2 for n in m["hold_lengths"][:-1]), m
    assert all(r <= 1 for r in m["registration_px"]), m
    assert m["audio_identical"], m


# ── AT-1D-4 / 1D-5 / 1D-6: junctions on library clips ──────────────────

def make_v13_montage(out_dir, seg=3.0):
    """dragon_unveil | starship_launch | optimus_walk | walkon, 1080p25,
    concatenated so keep spans can cut between real shots with handles."""
    p = os.path.join(out_dir, "v13_montage.mp4")
    if os.path.exists(p):
        return p
    srcs = [(V13[0], 4.0), (V13[1], 6.0), (V13[2], 3.0), (V12, 10.0)]
    args, fc = [], ""
    for k, (s, t) in enumerate(srcs):
        args += ["-ss", f"{t}", "-t", f"{seg}", "-i", s]
        fc += (f"[{k}:v]scale=1920:1080:force_original_aspect_ratio="
               f"increase,crop=1920:1080,fps=25,setsar=1,format=yuv420p"
               f"[v{k}];")
    args += ["-f", "lavfi", "-t", f"{4 * seg}", "-i",
             "sine=f=330:r=48000"]
    fc += "".join(f"[v{k}]" for k in range(4)) + "concat=n=4:v=1:a=0[v]"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args, "-filter_complex",
                    fc, "-map", "[v]", "-map", "4:a", "-c:v", "libx264",
                    "-crf", "14", "-preset", "fast", "-g", "25", "-c:a",
                    "aac", "-shortest", p], check=True)
    return p


MONTAGE_KEEP = [[0.3, 2.6], [3.4, 5.6], [6.4, 8.6], [9.4, 11.6]]
MONTAGE_IDX = {"words": [], "video": {"duration": 12.0, "width": 1920,
                                      "height": 1080, "fps": 25.0}}


def montage_cuts():
    out, acc = [], 0.0
    for a, b in MONTAGE_KEEP[:-1]:
        acc += b - a
        out.append(round(acc, 2))
    return out


def measure_junctions(out_dir, monkeypatch, styles, name, preview=False,
                      extra=None):
    src = make_v13_montage(out_dir)
    edl = {"keep": MONTAGE_KEEP, "captions": {"enabled": False},
           "frame": {"ratio": "9:16"}}
    cuts = montage_cuts()
    fx = []
    for k, (style, kw) in enumerate(styles):
        it = {"id": f"j{k}", "type": "junction", "stage": "junction",
              "at": cuts[k], "style": style}
        it.update(kw)
        fx.append(it)
    lossless(monkeypatch)
    c, o, rep = _pair(edl, fx, src, out_dir, name, idx=MONTAGE_IDX,
                      duration=12.0, preview=preview)
    _, a = frames(c)
    _, b = frames(o)
    got = changed(a, b)
    windows = []
    import fxprims
    for it in fx:
        cut = fxprims.frame_at(it["at"], 25.0)
        h = max(1, int(round(float(it.get("duration_s", 0.4)) * 25 / 2)))
        windows.append((cut - h, cut + h))
    inside = all(any(w0 <= f < w1 for w0, w1 in windows) for f in got)
    yb, _ = frames(o)
    ya, _ = frames(c)
    # a black frame the device introduced: near-black where the plain cut
    # is not (the footage's own dark frames do not count)
    black = [f for f in got if yb[f].mean() < 20 <= ya[f].mean()]
    return {"frames": (len(a), len(b)), "changed_inside_windows": inside,
            "audio_identical": audio_md5(o) == audio_md5(c),
            "rows": [{k: r[k] for k in ("id", "rung", "reason")}
                     for r in rep],
            "black_frames_introduced": black,
            "out": o, "ctrl": c}


@local_only(*V13, V12)
def test_at_1d_4_columns_box_steps_and_an_element_iris(tmp_path,
                                                        monkeypatch):
    m = measure_junctions(str(tmp_path), monkeypatch, [
        ("columns", {"duration_s": 0.5, "count": 6, "direction": "left"}),
        ("box_steps", {"duration_s": 0.8}),
        ("iris", {"duration_s": 0.4, "origin": {"kind": "point",
                                                "point": [0.62, 0.38]}})],
        "at4")
    assert m["frames"][0] == m["frames"][1]
    assert m["audio_identical"] and m["changed_inside_windows"], m
    assert all(r["rung"] == "full" and "held" not in r["reason"]
               for r in m["rows"]), m          # source handles used
    # the iris opens on the element: the first lit mask pixel's centroid
    import glob
    from PIL import Image
    d = [p for p in glob.glob(os.path.join(m["out"] + ".wd",
                                           "w1d_jn_*_m"))
         if os.path.exists(os.path.join(p, "f_00009.png"))]
    # (soft masks are stored at half size: the centroid is read in the
    # mask's pixels and judged in the frame's, +-1 frame pixel)
    fw = probe(m["out"])["width"]
    centres = []
    for dd in d:
        seq = [np.asarray(Image.open(os.path.join(dd, f"f_{k:05d}.png")))
               for k in range(10)]
        lit = next((s for s in seq if 0 < (s == 255).sum() < 2000), None)
        if lit is not None:
            ys, xs = np.nonzero(lit == 255)
            k = fw / float(lit.shape[1])
            centres.append(((xs.mean() + 0.5) * k, (ys.mean() + 0.5) * k,
                            lit.shape[1] * k, lit.shape[0] * k))
    assert any(abs(cx - 0.62 * W) <= 1.0 and abs(cy - 0.38 * H) <= 1.0
               for cx, cy, W, H in centres), centres


@local_only(*V13, V12)
def test_at_1d_5_datamosh_is_deterministic_and_smears(tmp_path,
                                                       monkeypatch):
    md5s = []
    for k in range(3):
        m = measure_junctions(str(tmp_path), monkeypatch, [
            ("datamosh", {"duration_s": 1.0})], f"at5_{k}")
        md5s.append(frame_md5s(m["out"]))
        assert m["rows"][0]["rung"] == "reduced", m
    assert md5s[0] == md5s[1] == md5s[2]
    assert m["audio_identical"] and m["changed_inside_windows"]


@local_only(*V13, V12)
def test_at_1d_6_matte_cut_degrades_to_the_flash_without_black_frames(
        tmp_path, monkeypatch):
    m = measure_junctions(str(tmp_path), monkeypatch, [
        ("dissolve", {}), ("dissolve", {}),
        ("matte_cut", {"duration_s": 0.5})], "at6")
    rows = {r["id"]: r for r in m["rows"]}
    assert rows["j2"]["rung"] in ("full", "substitute")
    assert m["black_frames_introduced"] == [] and m["audio_identical"], m


# ── AT-1D-7: flash safety ───────────────────────────────────────────────

def measure_at7(out_dir, monkeypatch):
    win, words, idx = _win("V02", "thiel_flying_cars")
    dur = float(win["file_span"][1] - win["file_span"][0])
    edl = {"keep": [[40.0, 46.0]], "captions": {"enabled": False},
           "frame": {"ratio": "9:16"}}
    hits = [2.0, 2.16, 2.32, 2.48, 2.64, 2.8]
    from tools_ext import punctuation_tools as P
    import flash_safety
    import qa_metrics
    import quality_verifier
    out = {}
    lossless(monkeypatch)
    for name, msg in (("auto", "punch up the payoff"),
                      ("optin", "strobe negatives on that line")):
        ctx = ToolCtx(edl, dict(idx, words=words), dur, user_message=msg)
        res = P.add_accent(ctx, "negative", at=hits)
        fx = [i for i in ctx.edl["fx"] if i["type"] == "accent"]
        c, o, _rep = _pair(edl, fx, win["path"], out_dir, f"at7_{name}",
                           idx=idx, duration=dur, preview=True)
        risk = qa_metrics.flash_risk(video=o, opted_in=(name == "optin"))
        flagged = bool((quality_verifier.flash_assessment(
            ctx.edl, {}, msg) or {}).get("over_limit"))
        out[name] = {"kept": len(fx), "result": res.splitlines()[0][:160],
                     "thinned_reported": "flash safety held" in res,
                     "flash_risk": risk["value"],
                     "contains_flashing": flagged,
                     "assess": flash_safety.assess(ctx.edl,
                                                   request_text=msg)}
    return out


@local_only(V02)
def test_at_1d_7_flash_safety_thins_or_flags(tmp_path, monkeypatch):
    m = measure_at7(str(tmp_path), monkeypatch)
    assert m["auto"]["kept"] == 3 and m["auto"]["thinned_reported"], m
    assert m["auto"]["flash_risk"] <= 3 and not m["auto"][
        "contains_flashing"], m
    assert m["optin"]["kept"] == 6 and m["optin"]["contains_flashing"], m


# ── perf ────────────────────────────────────────────────────────────────

def measure_perf(out_dir, monkeypatch):
    """Filters per accent kind for 1 vs 24 accents (graph size flat), and
    a mask wipe's added CPU-seconds per 1080p second of window."""
    import resource
    import renderer
    graphs = []
    real = renderer.build_filtergraph

    def spy(*a, **kw):
        g = real(*a, **kw)
        graphs.append(g)
        return g
    monkeypatch.setattr(renderer, "build_filtergraph", spy)
    src = make_v13_montage(out_dir)
    edl = {"keep": MONTAGE_KEEP, "captions": {"enabled": False},
           "frame": {"ratio": "9:16"}}
    counts = {}
    for kind in ("negative", "white_hit", "box_invert"):
        for n in (1, 24):
            fx = []
            for k in range(n):
                f = 10 + k * 9
                it = {"id": f"a{k}", "type": "accent",
                      "stage": "pre_captions", "start": round(f / 25, 2),
                      "end": round((f + 1) / 25, 2), "kind": kind}
                if kind == "white_hit":
                    it["decay_f"] = 3
                fx.append(it)
            graphs.clear()
            render(dict(edl, fx=fx), src, os.path.join(
                out_dir, f"perf_{kind}_{n}.mp4"), idx=MONTAGE_IDX,
                duration=12.0)
            g = graphs[-1]
            counts[(kind, n)] = len([p for p in g.split(";") if p.strip()])
    flat = {k: counts[(k, 24)] - counts[(k, 1)] for k in
            ("negative", "white_hit", "box_invert")}

    def cpu():
        # ffmpeg (children) AND this process: the masks are computed here,
        # in numpy, before ffmpeg runs, and are part of the wipe's cost
        c = resource.getrusage(resource.RUSAGE_CHILDREN)
        s = resource.getrusage(resource.RUSAGE_SELF)
        return c.ru_utime + c.ru_stime + s.ru_utime + s.ru_stime

    def cost(name, fx):
        best = None
        for _ in range(2):                    # the quieter of two runs
            t0 = cpu()
            render(dict(edl, fx=fx), src, os.path.join(
                out_dir, f"perf_{name}.mp4"), idx=MONTAGE_IDX, duration=12.0,
                preview=False)
            got = cpu() - t0
            best = got if best is None else min(best, got)
        return best
    lossless(monkeypatch)
    base = cost("ctrl", [])
    fx = [{"id": f"j{k}", "type": "junction", "stage": "junction", "at": a,
           "style": "columns", "duration_s": 1.0}
          for k, a in enumerate(montage_cuts())]
    # the Wave 0 junction seam on its own (source handles, the window's
    # overlay): a window that is the plain cut rebuilt
    import junctions_v2

    def plain(self, ctx, items):
        h = int(ctx.h)
        return self._settle(ctx, self._concat(ctx, ctx.a_label, ctx.b_label,
                                              h, 2 * h), 2 * h)
    monkeypatch.setattr(junctions_v2._Junction, "junction", plain)
    seam = cost("seam", fx)
    monkeypatch.undo()
    lossless(monkeypatch)
    wipe = cost("wipe", fx)
    n = len(fx) * 1.0
    return {"graph_parts_24_minus_1": flat,
            # the device's own cost: the column wipe over the plain cut
            "wipe_cpu_s_per_window_s": round(max(0.0, wipe - seam) / n, 3),
            "seam_cpu_s_per_window_s": round(max(0.0, seam - base) / n, 3),
            "control_cpu_s": round(base, 2), "seam_cpu_s": round(seam, 2),
            "wipe_cpu_s": round(wipe, 2)}


@local_only(*V13, V12)
def test_at_1d_perf_flat_graph_and_cheap_wipes(tmp_path, monkeypatch):
    m = measure_perf(str(tmp_path), monkeypatch)
    assert all(v <= 2 for v in m["graph_parts_24_minus_1"].values()), m
    assert m["wipe_cpu_s_per_window_s"] <= 0.5, m     # ROADMAP 1D perf
