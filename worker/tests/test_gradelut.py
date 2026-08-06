"""Round 91 — gradelut: table filters must reproduce the grade chains.

The global grade was the single most expensive part of a preview render
(colorbalance's per-pixel pow() alone multiplied a render several-fold).
gradelut collapses grade chains into baked table filters. This pins:

  * exactness: chains made of eq / hue=s=0 / curves are PER-VALUE maps, so
    the fast chain must be BIT-IDENTICAL (PSNR inf) to the legacy chain;
  * the colorbalance bound: modern colorbalance weights its shifts by pixel
    lightness, so its 1D table is exact on the gray diagonal and approximate
    off it. testsrc2's saturated bars are the worst case anywhere — the
    measured floor (round 91) was 32.1dB on the heaviest stacked custom
    grade and 37.6dB on the heaviest preset; the gate asserts nothing ever
    sinks below 30dB even there. On real footage every case measured 45dB+
    and the 8x-amplified diff frame was black.
  * safety: unknown filters, hue-with-rotation and chains whose planes are
    NOT independent all keep the original string — the slow path is always
    correct.

LIVE ffmpeg; skipped where there is none (the bake runs the real filters).
"""

import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gradelut                                                # noqa: E402

PASS = 0
HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def check(name, cond):
    global PASS
    assert cond, f"FAIL: {name}"
    PASS += 1
    print(f"  ok  {name}")


def psnr(a, b):
    """PSNR between two filter chains over testsrc2. 'inf' = identical."""
    r = subprocess.run(
        ["ffmpeg", "-v", "info", "-f", "lavfi",
         "-i", "testsrc2=size=320x180:rate=30:duration=1",
         "-filter_complex",
         f"[0:v]format=yuv420p,split[x][y];[x]{a}[ref];"
         f"[y]{b}[test];[test][ref]psnr",
         "-f", "null", "-"], capture_output=True, text=True)
    m = re.search(r"average:([\w.]+)", r.stderr)
    assert m, f"psnr run failed: {r.stderr[-300:]}"
    return float("inf") if m.group(1) == "inf" else float(m.group(1))


# The exact strings renderer.py emits (GRADE_FILTERS + grade_custom shapes).
EXACT_CHAINS = {
    "vibrant": "eq=saturation=1.35:contrast=1.08",
    "bw": "hue=s=0,eq=contrast=1.1",
    "vintage": "curves=preset=vintage,eq=saturation=0.85",
    "custom_curves": "eq=brightness=-0.070:contrast=1.150,"
                     "curves=master='0/0.000 0.25/0.215 0.5/0.5 "
                     "0.75/0.785 1/1.000'",
}
CB_CHAINS = {
    "warm": "colorbalance=rs=.08:gs=.02:bs=-.08,eq=saturation=1.12",
    "cool": "colorbalance=rs=-.05:bs=.08,eq=saturation=1.05",
    "cinematic": "colorbalance=bs=.05:rs=-.03,"
                 "eq=contrast=1.12:saturation=1.12:brightness=-0.02",
    "custom_full": "eq=brightness=0.105:contrast=1.3:saturation=0.7,"
                   "colorbalance=rs=0.130:rm=0.078:bs=-0.070:bm=-0.042,"
                   "curves=master='0/0.036 0.25/0.292 0.5/0.5 "
                   "0.75/0.760 1/1.000'",
}

print("== 1. structure and safety (no ffmpeg needed) ==")

check("unknown filter falls back untouched",
      gradelut.fast_chain("gblur=sigma=2,eq=saturation=1.1")
      == "gblur=sigma=2,eq=saturation=1.1")
check("hue with a rotation falls back (chroma planes mix)",
      gradelut.fast_chain("hue=h=90") == "hue=h=90")
check("empty-ish input falls back", gradelut.fast_chain("") == "")

print("== 2. table filters vs the real chains ==")

if not HAVE_FFMPEG:
    print("  -- skipped (no ffmpeg on this machine)")
else:
    for name, chain in EXACT_CHAINS.items():
        fast = gradelut.fast_chain(chain)
        check(f"{name}: fast path engaged", fast != chain)
        check(f"{name}: BIT-IDENTICAL (PSNR inf)",
              psnr(chain, fast) == float("inf"))
    for name, chain in CB_CHAINS.items():
        fast = gradelut.fast_chain(chain)
        check(f"{name}: fast path engaged", fast != chain)
        p = psnr(chain, fast)
        check(f"{name}: >=30dB on the saturated worst case (got {p:.1f})",
              p >= 30.0)

    check("identity grade collapses to null",
          gradelut.fast_chain("eq=contrast=1.0:saturation=1.0") == "null")

    # A chain whose output planes read other planes must be REFUSED by the
    # independence probe. colorchannelmixer isn't in the vocabulary (parse
    # already blocks it), so probe the bake directly.
    try:
        gradelut._bake("colorchannelmixer=rr=0:rg=1", "gbrp", "gbr",
                       check=True)
        check("independence probe rejects a channel mixer", False)
    except RuntimeError:
        check("independence probe rejects a channel mixer", True)

    # The bake cache must serve repeats without re-running ffmpeg.
    n0 = len(gradelut._cache)
    gradelut.fast_chain(EXACT_CHAINS["vibrant"])
    gradelut.fast_chain(EXACT_CHAINS["vibrant"])
    check("bake cache reused across calls", len(gradelut._cache) == n0)

print(f"\nALL {PASS} CHECKS PASSED")
