"""Track 1E Variant Forge and the hue-mask peer.

Variants come from ONE frame through finish.py's own graphs, so they are
registered by construction: edges sit on the same pixels in every variant
(measured as edge agreement against the photo), and a set of six renders in
one ffmpeg process well inside the agent lane's 10 CPU-s budget.
The ``hue:<rrggbb>`` mask provider (1B's isolate peer) is checked on a real
ffmpeg run."""

import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import finish  # noqa: E402
import fxgraph  # noqa: E402
import gradewin  # noqa: E402
import variants  # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"),
                                reason="needs ffmpeg")


def _frame(path, w=640, h=360):
    """A structured frame: shapes with hard edges, a gradient, a face-ish
    disc, so every variant has edges to keep."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (w, h), (90, 96, 104))
    d = ImageDraw.Draw(im)
    for i in range(w):
        d.line([(i, 0), (i, h // 5)], fill=(i * 255 // w, 60, 160))
    d.rectangle([60, 120, 220, 300], fill=(200, 32, 42))
    d.ellipse([300, 100, 460, 300], fill=(224, 172, 140))
    d.rectangle([500, 80, 600, 330], fill=(30, 140, 60))
    d.polygon([(250, 330), (320, 200), (390, 330)], fill=(240, 240, 240))
    im.save(path)
    return path


def _edges(img):
    import cv2
    g = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2GRAY)
    return cv2.Canny(cv2.GaussianBlur(g, (5, 5), 1.2), 40, 110) > 0


def edge_agreement(photo, variant, tol=3):
    """Share of the PHOTO's edges found in the variant within ``tol`` px
    (recall) and IoU of the two tolerant edge maps. A drawn line is a few
    pixels wide, so its own edges sit up to 3 px either side of the
    photo's: 3 px is the line, not a misregistration (the shift itself is
    measured by ``registration_shift``)."""
    import cv2
    a, b = _edges(photo), _edges(variant)
    k = np.ones((2 * tol + 1, 2 * tol + 1), np.uint8)
    da = cv2.dilate(a.astype(np.uint8), k) > 0
    db = cv2.dilate(b.astype(np.uint8), k) > 0
    recall = (a & db).sum() / max(1, a.sum())
    iou = (da & db).sum() / max(1, (da | db).sum())
    return float(recall), float(iou)


@pytest.fixture(scope="module")
def made(tmp_path_factory):
    d = tmp_path_factory.mktemp("w1e_var")
    src = _frame(str(d / "frame.png"))
    sets = {}
    for i, kinds in enumerate((variants.DEFAULT_KINDS,
                               ("xray", "solarise", "posterise",
                                "kaleidoscope", "pixel_stretch",
                                "night_vision"), ("halftone",))):
        sets[i] = variants.render_variants(src, kinds, str(d / f"s{i}"),
                                           seed=3)
    return src, sets


def test_every_variant_kind_renders_at_the_frame_size(made):
    from PIL import Image
    _src, sets = made
    seen = set()
    for paths, stats in sets.values():
        assert stats["W"] == 640 and stats["H"] == 360
        for kind, p in paths.items():
            with Image.open(p) as im:
                assert im.size == (640, 360), kind
            seen.add(kind)
    assert seen == set(variants.VARIANT_KINDS)


def test_six_variants_stay_inside_the_cpu_budget(made):
    _src, sets = made
    assert sets[1][1]["cpu_s"] < 10.0, sets[1][1]


def registration_shift(photo, variant):
    """Sub-pixel (dx, dy) between the gradient maps of two variants."""
    import cv2

    def grad(img):
        g = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2GRAY).astype(
            np.float32)
        gx = cv2.Sobel(g, cv2.CV_32F, 1, 0)
        gy = cv2.Sobel(g, cv2.CV_32F, 0, 1)
        return cv2.GaussianBlur(np.hypot(gx, gy), (0, 0), 3)
    (dx, dy), _r = cv2.phaseCorrelate(grad(photo), grad(variant))
    return dx, dy


def test_variants_are_registered_to_the_photo(made):
    from PIL import Image
    _src, sets = made
    paths = sets[0][0]
    photo = Image.open(paths["photo"]).convert("RGB")
    for kind in ("negative", "line", "blueprint", "thermal"):
        v = Image.open(paths[kind]).convert("RGB")
        recall, iou = edge_agreement(photo, v)
        assert recall >= 0.9, (kind, recall, iou)
        dx, dy = registration_shift(photo, v)
        assert abs(dx) <= 1 and abs(dy) <= 1, (kind, dx, dy)


def test_variants_are_deterministic(made, tmp_path):
    src, sets = made
    again, _ = variants.render_variants(src, variants.DEFAULT_KINDS,
                                        str(tmp_path / "again"), seed=3)
    for kind, p in sets[0][0].items():
        assert open(p, "rb").read() == open(again[kind], "rb").read(), kind


def test_kind_checks():
    assert variants.check_kinds(None) == variants.DEFAULT_KINDS
    assert variants.check_kinds("photo, thermal, photo") == ("photo",
                                                            "thermal")
    with pytest.raises(ValueError, match="unknown variant"):
        variants.check_kinds(["photo", "hologram"])
    with pytest.raises(ValueError, match="at most 6"):
        variants.check_kinds(list(variants.VARIANT_KINDS))


def test_a_still_graph_matches_its_live_finish_kind(made, tmp_path):
    """make_variant_set and add_finish draw the same look: the still graph
    is the live builder on one frame."""
    src, sets = made
    g, maps = finish.still_graph("thermal", 640, 360, {"id": "variant_thermal",
                                                       "seed": 3},
                                 str(tmp_path))
    out = str(tmp_path / "t.png")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src]
                   + sum((["-i", m] for m in maps), [])
                   + ["-filter_complex", g, "-map", "[out]", "-frames:v",
                      "1", out], check=True)
    from PIL import Image
    a = np.asarray(Image.open(out).convert("RGB")).astype(int)
    b = np.asarray(Image.open(sets[0][0]["thermal"]).convert("RGB")) \
        .astype(int)
    assert np.abs(a - b).max() <= 1


# ── the hue: mask peer ──────────────────────────────────────────────────

class _Ctx:
    def __init__(self, plan, label):
        self.plan, self.parts, self.label = plan, [], label
        self.W, self.H = plan.W, plan.H

    def uid(self, p="fx"):
        return self.plan.uid(p)


def test_hue_mask_provider_marks_the_colour(made, tmp_path):
    src, _sets = made
    plan = fxgraph.FxPlan(W=640, H=360, fps=25.0, program_s=1.0)
    ctx = _Ctx(plan, "v0")
    pad = fxgraph.mask(plan, "hue:c8202a", "world", ctx=ctx)
    assert pad is not None and ctx.label != "v0"       # rebound
    graph = ";".join(["[0:v]format=yuv420p[v0]"] + ctx.parts
                     + [f"[{ctx.label}]nullsink"])
    out = str(tmp_path / "mask.png")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src,
                    "-filter_complex", graph, "-map", f"[{pad}]",
                    "-frames:v", "1", out], check=True)
    from PIL import Image
    m = np.asarray(Image.open(out).convert("L")).astype(int)
    assert m.shape == (360, 640)
    assert m[150:280, 80:200].mean() > 240              # the red block
    assert m[150:280, 320:440].max() < 10               # the skin disc
    assert m[120:300, 510:590].max() < 10               # green
    assert fxgraph.mask(plan, "hue:nothex", "world", ctx=ctx) is None
    key = gradewin.hue_key("#c8202a")
    assert key.exclude_skin is True
