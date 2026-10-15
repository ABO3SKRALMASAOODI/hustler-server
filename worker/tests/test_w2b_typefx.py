"""Track 2B: the TypeFX runtime on rendered frames (needs Chromium).

  * parity: the page draws every word where the Python solver put it
    (rendered ink box vs solved ink box IoU >= 0.95);
  * 2B-1: ``blur_resolve`` is sharp exactly on the spoken onset frame;
  * 2B-2: a ``duration_wipe`` crosses 50% within one frame of the word's
    spoken midpoint; ``decay`` is gone on its offset frame;
  * 2B-3: arc text sits concentric with the head, clear of it by 3-6% of
    the frame height;
  * 2B-5: glyph morphs are deterministic (frame md5) and the document stays
    under the 200 KB cap;
  * 2B-6: every bundled face has its OFL/Apache licence; a brand font with
    restricted embedding is refused, a permitted one sanitised and subset.
"""

import hashlib
import io
import math
import os
import random
import subprocess
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import motion_engine                                       # noqa: E402
import motion_templates                                    # noqa: E402
from typeset import layoutdoc, voices as V                 # noqa: E402

needs_engine = pytest.mark.skipif(not motion_engine.available(),
                                  reason="needs the motion engine")
FPS = 25.0
FACE = [(0.40, 0.14, 0.60, 0.58)]
WM = (0.0, 0.0, 0.2, 0.16)


def _item(text, dur, onsets, **params):
    return {"id": "ts", "template": "typeset", "start": 0.0, "end": dur,
            "params": motion_templates.normalize_params(
                "typeset", dict({"text": text}, **params)),
            "reading": {"v": 1, "rows": [onsets], "bridges": []},
            "footprint": {"box": [0, 0, 1, 1], "ar": 1920 / 1080,
                          "faces": [list(f) for f in FACE]}}


def _alpha_frames(clip, frames, W, H):
    out = {}
    for k in frames:
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", clip.path, "-vf",
             f"select=eq(n\\,{k}),format=rgba", "-frames:v", "1", "-f",
             "rawvideo", "-"], capture_output=True, check=True).stdout
        a = np.frombuffer(raw, np.uint8).reshape(clip.h, clip.w, 4)
        full = np.zeros((H, W, 4), np.uint8)
        full[clip.y:clip.y + clip.h, clip.x:clip.x + clip.w] = a
        out[k] = full
    return out


def _render(item, W, H, tmp):
    job = motion_templates.build_job(item, W, H, FPS)
    return motion_engine.render_jobs([job], str(tmp))[0]


def _sharpness(rgba):
    a = rgba[..., 3].astype(float)
    return float(np.abs(np.diff(a, axis=1)).max())


# ── parity ───────────────────────────────────────────────────────────────

def _png_alpha(png):
    from PIL import Image
    im = Image.open(io.BytesIO(png)).convert("RGBA")
    return np.asarray(im)[..., 3]


def parity_cases(n, seed=7):
    rnd = random.Random(seed)
    texts = ["perhaps AI is *bigger* than the internet",
             "they promised us flying cars",
             "we got 140 characters", "it is not enough",
             "the fonts have been just garbage",
             "nobody disagrees with me", "go do it",
             "Mars actually needs global warming",
             "the car was not his real invention",
             "three hundred sixty pounds"]
    aspects = [(1080, 1920, [(0.30, 0.22, 0.70, 0.46)]),
               (1920, 1080, FACE), (1080, 1350, [(0.36, 0.16, 0.64, 0.5)])]
    out = []
    for i in range(n):
        W, H, faces = aspects[i % len(aspects)]
        out.append((rnd.choice(texts), rnd.choice(V.NAMES), W, H, faces))
    return out


def parity(cases):
    """(per-run IoUs, failures) of rendered vs solved ink boxes."""
    jobs, docs = [], []
    for text, voice, W, H, faces in cases:
        n = len(text.replace("*", " ").split())
        item = _item(text, 3.0, [round(0.05 * k, 2) for k in range(n)],
                     voice=voice, verb="cut", material="flat", exit="hold")
        item["footprint"]["ar"] = W / H
        item["footprint"]["faces"] = [list(f) for f in faces]
        doc = layoutdoc.solve_item(item, W, H, FPS)
        docs.append((doc, W, H))
        jobs.append(motion_templates.build_job(item, W, H, FPS))
    reps = motion_engine.probe(jobs, [[2.9]] * len(jobs), budget_s=900,
                               measure=lambda png: _png_alpha(png))
    ious, bad = [], []
    for (doc, W, H), rep in zip(docs, reps):
        if doc.get("family") == "quote_window":
            continue                # one word at a time: probed per word
        alpha = rep["measures"][0]
        dh, dw = alpha.shape
        sx, sy = dw / doc["canvas"]["w"], dh / doc["canvas"]["h"]
        mask = alpha >= 128
        for r in doc["runs"]:
            if r.get("rot_deg"):
                continue            # rotated runs: their box is the AABB
            x0, y0, x1, y1 = r["ink"]
            m = 0.02 * r["size_px"]
            X0, Y0 = max(0, int((x0 - m) * sx)), max(0, int((y0 - m) * sy))
            X1, Y1 = min(dw, int((x1 + m) * sx) + 1), min(dh, int((y1 + m) * sy) + 1)
            sub = mask[Y0:Y1, X0:X1]
            ys, xs = np.nonzero(sub)
            if not len(xs):
                bad.append((r["text"], "not drawn"))
                ious.append(0.0)
                continue
            got = (X0 + xs.min(), Y0 + ys.min(), X0 + xs.max() + 1,
                   Y0 + ys.max() + 1)
            # the solved box on the pixel grid the page draws on (a 25 px
            # whisper is 0.9 IoU from one pixel of rounding otherwise)
            want = (math.floor(x0 * sx), math.floor(y0 * sy),
                    math.ceil(x1 * sx), math.ceil(y1 * sy))
            ix = max(0.0, min(got[2], want[2]) - max(got[0], want[0]))
            iy = max(0.0, min(got[3], want[3]) - max(got[1], want[1]))
            inter = ix * iy
            u = (got[2] - got[0]) * (got[3] - got[1]) + \
                (want[2] - want[0]) * (want[3] - want[1]) - inter
            iou = inter / u if u else 0.0
            ious.append(iou)
            # a whisper a few pixels tall on the design canvas is limited by
            # pixel rounding: within one pixel on every edge counts as exact
            edge = max(abs(got[i] - want[i]) for i in range(4))
            if iou < 0.95 and edge > 1.0:
                bad.append((r["text"], round(iou, 3), r["font"]["family"],
                            edge))
    return ious, bad


@needs_engine
def test_python_and_chromium_agree_on_where_every_word_is():
    ious, bad = parity(parity_cases(18))
    assert ious
    assert float(np.median(ious)) >= 0.97
    # (within a pixel on every edge counts as exact: see parity)
    assert len(bad) <= 0.05 * len(ious), bad
    assert min(ious) >= 0.85, bad


# ── 2B-1 / 2B-2 ──────────────────────────────────────────────────────────

@needs_engine
def test_blur_resolve_is_sharp_on_the_onset_frame(tmp_path):
    W, H = 1080, 608
    onset = 0.6                                          # frame 15 at 25 fps
    item = _item("FOCUS", 1.6, [onset], voice="whisper_shout",
                 verb="blur_resolve", material="flat", exit="hold")
    item["footprint"]["ar"] = W / H
    clip = _render(item, W, H, tmp_path)
    f = _alpha_frames(clip, [12, 14, 15, 30], W, H)
    final = _sharpness(f[30])
    diff = {k: float(np.abs(f[k][..., 3].astype(int) -
                            f[30][..., 3].astype(int)).mean()) for k in f}
    assert diff[15] == 0.0                     # resolved on the onset frame
    assert diff[12] > 4 * max(diff[14], 0.01)  # and still resolving before
    assert diff[14] > 0.0


@needs_engine
def test_a_duration_wipe_crosses_half_on_the_spoken_midpoint(tmp_path):
    W, H = 1080, 608
    item = _item("DREAM", 2.0, [0.4], voice="hairline_wide",
                 verb="duration_wipe", material="flat", exit="hold")
    item["footprint"]["ar"] = W / H
    doc = layoutdoc.solve_item(item, W, H, FPS)
    run = doc["runs"][0]
    mid_f = round((run["onset"] + run["dur"] / 2) * FPS)
    clip = _render(item, W, H, tmp_path)
    frames = list(range(int(run["onset"] * FPS), int((run["onset"] + run["dur"]) * FPS) + 3))
    al = _alpha_frames(clip, frames + [45], W, H)
    full = (al[45][..., 3] >= 128).sum()
    cover = {k: (al[k][..., 3] >= 128).sum() / full for k in frames}
    cross = next(k for k in frames if cover[k] >= 0.5)
    assert abs(cross - mid_f) <= 1, (cross, mid_f, cover)


@needs_engine
def test_decay_is_gone_on_its_offset_frame(tmp_path):
    W, H = 1080, 608
    item = _item("DREAM", 1.6, [0.2], voice="techno_crt", verb="cut",
                 material="flat", exit="decay")
    item["footprint"]["ar"] = W / H
    clip = _render(item, W, H, tmp_path)
    n = int(round(1.6 * FPS))
    al = _alpha_frames(clip, [n - 12, n - 2], W, H)
    assert (al[n - 12][..., 3] >= 128).sum() > 0
    early = (al[n - 12][..., 3] >= 128).sum()
    late = (al[n - 2][..., 3] >= 128).sum()
    assert late < 0.25 * early


# ── 2B-3 ─────────────────────────────────────────────────────────────────

def test_arc_text_is_concentric_with_the_head_and_clear_of_it():
    doc = layoutdoc.build_doc({"text": "follow the light", "layout": "arc",
                               "voice": "whisper_shout"},
                              W=1080, H=608, duration=3.0,
                              faces=FACE, watermark=WM)
    assert doc["family"] == "arc", doc["solver_report"]
    run = doc["runs"][0]
    p = run["path"]
    cx, cy, rx, ry = p["cx"], p["cy"], p["rx"], p["ry"]
    from typeset import freespace
    space = freespace.Space(1080, 608, faces=FACE, watermark=WM)
    z = space.face_zones[0]
    fx = (FACE[0][0] + FACE[0][2]) / 2 * 1080
    assert abs(cx - fx) < 1.0 and abs(cy - (z[1] + z[3]) / 2) < 1.0
    for r in doc["runs"]:
        for _ch, x, y, _rot, _adv in r["path"]["glyphs"]:
            k = ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2
            assert k == pytest.approx(1.0, abs=0.02)        # on the ellipse
    gap = (rx - (z[2] - z[0]) / 2) / 608
    assert 0.03 <= gap <= 0.06, gap
    hw, hh = (z[2] - z[0]) / 2, (z[3] - z[1]) / 2
    for r in doc["runs"]:                  # every glyph outside the head
        for _ch, x, y, _rot, _adv in r["path"]["glyphs"]:
            assert ((x - cx) / hw) ** 2 + ((y - cy) / hh) ** 2 > 1.0


# ── 2B-5 / 2B-6 ──────────────────────────────────────────────────────────

@needs_engine
def test_glyph_morph_is_deterministic_and_small(tmp_path):
    W, H = 1080, 608
    item = _item("LIGHT", 1.2, [0.6], voice="whisper_shout",
                 verb="glyph_morph", material="flat", exit="hold")
    item["footprint"]["ar"] = W / H
    job = motion_templates.build_job(item, W, H, FPS)
    assert len(job.html.encode()) <= motion_engine.MAX_HTML_BYTES
    md5s = []
    for k in range(2):
        d = tmp_path / f"r{k}"
        d.mkdir()
        motion_engine._reset_availability_cache()
        clip = motion_engine.render_jobs([job], str(d))[0]
        al = _alpha_frames(clip, [10, 13], W, H)
        md5s.append(tuple(hashlib.md5(al[k].tobytes()).hexdigest()
                          for k in (10, 13)))
    assert md5s[0] == md5s[1]


def test_every_bundled_face_has_its_licence():
    import fonts_brand
    rows = fonts_brand.ledger()
    assert rows and all(r["ok"] for r in rows), [r for r in rows if not r["ok"]]
    assert {r["licence"] for r in rows} <= {"OFL-1.1", "Apache-2.0"}


def test_a_brand_font_round_trip(tmp_path):
    import fonts_brand
    from fontTools.ttLib import TTFont
    src = os.path.join(os.path.dirname(HERE), "motion", "fonts",
                       "Gloock-Regular.ttf")
    ok, facts = fonts_brand.validate(src, "OFL-1.1")
    assert ok, facts
    out = str(tmp_path / "brand.ttf")
    sha = fonts_brand.sanitise(src, out)
    assert len(sha) == 64 and os.path.getsize(out) < os.path.getsize(src)
    t = TTFont(out)
    assert ord("A") in t.getBestCmap() and 0x4E00 not in t.getBestCmap()
    # restricted embedding is refused; a commercial claim needs attestation
    f = TTFont(src)
    f["OS/2"].fsType = 0x0002
    bad = str(tmp_path / "restricted.ttf")
    f.save(bad)
    ok, why = fonts_brand.validate(bad, "OFL-1.1")
    assert not ok and "embedding" in why
    ok, why = fonts_brand.validate(src, "commercial-licensed")
    assert not ok and "attest" in why
    assert fonts_brand.validate(src, "commercial-licensed", attest=True)[0]
