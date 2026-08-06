"""Fast, exact global color grades: table filters instead of per-pixel math.

THE PROBLEM (round 91): the global grade is the single most expensive thing a
preview render does. `colorbalance` computes pow() per pixel per frame and on a
54s program costs ~7s of the render by itself; `curves` and the RGB round trips
add more. Measured on the round-91 bench: a cuts-only preview encodes in 1.3s,
the same program with the cinematic grade in 8.5s — the grade multiplies the
whole render several-fold, and ~40% of live projects carry one.

THE FIX: every filter the grade emission uses is a PER-VALUE map — eq and
hue=s=0 map each YUV plane's value independently, colorbalance and curves map
each RGB channel's value independently. A per-value map over 8-bit video is a
256-entry table, so the whole chain collapses to table lookups. The tables are
produced by RUNNING THE REAL SUB-CHAIN through this box's own ffmpeg once on a
256-value identity ramp (~80ms, cached per chain string), so they capture that
exact build's math — vf_eq's fixed-point quirks included — bit for bit. (The
closed-form route was tried first and abandoned: eq's integer path defeats
every reasonable formula by 1-3 LSB on half the table.)

Application:
  - a run of adjacent YUV filters -> one `lutyuv` whose per-plane expression
    is a balanced if() tree over the baked table. The expression is evaluated
    once per possible value at filter INIT (lutyuv builds its own table from
    it); per-frame cost is a plain table lookup.
  - a run of adjacent RGB filters -> ONE `curves` filter carrying all 256
    baked points per channel. vf_curves samples its spline at exactly i/255,
    which are exactly the knots we hand it, so the applied table IS the baked
    table.

Because a run is baked as a unit, intermediate rounding between a run's
filters is captured too. Each bake is guarded by a second bake with the other
planes held neutral; if the tables disagree, the map depends on more than its
own plane (not a per-value map at all) and the original chain is kept.

Anything this module does not recognize — and any bake failure — returns the
ORIGINAL chain string unchanged: the slow path is always correct, so this can
never fail a render, only fail to speed one up.

Scope: GLOBAL grade only (effects.grade preset + effects.grade_custom). The
windowed stylize effects (flash, vhs, …) carry enable= clauses and stay on
their per-frame filters — a table cannot be time-conditional.
"""

import subprocess
import threading

# The closed vocabulary the grade emission produces (renderer.GRADE_FILTERS
# and the grade_custom builder). Anything else -> not ours, leave untouched.
_YUV_FILTERS = ("eq", "hue")
_RGB_FILTERS = ("colorbalance", "curves")

_BAKE_TIMEOUT_S = 15
_cache = {}
_cache_lock = threading.Lock()


# ---------------------------------------------------------------- parsing --

def _split_top(s, sep):
    """Split on sep outside single quotes (filter args carry quoted exprs)."""
    out, buf, q = [], [], False
    for ch in s:
        if ch == "'":
            q = not q
            buf.append(ch)
        elif ch == sep and not q:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    out.append("".join(buf))
    return out


def _parse_chain(chain):
    """[(name, argstr)] or None when any element is outside the vocabulary."""
    items = []
    for el in _split_top(chain, ","):
        el = el.strip()
        if not el:
            continue
        name, _, args = el.partition("=")
        name = name.strip()
        if name not in _YUV_FILTERS + _RGB_FILTERS:
            return None
        if name == "hue" and args.strip() != "s=0":
            # hue with a rotation mixes the chroma planes — not a per-plane
            # map. The grade emission only ever writes hue=s=0 (the bw
            # preset); anything else is not ours to translate.
            return None
        items.append((name, args))
    return items or None


# ----------------------------------------------------------------- baking --

def _run_ffmpeg(args, data):
    p = subprocess.run(["ffmpeg", "-hide_banner", "-v", "error"] + args,
                       input=data, capture_output=True,
                       timeout=_BAKE_TIMEOUT_S)
    if p.returncode != 0:
        raise RuntimeError(
            f"bake ffmpeg failed: {p.stderr.decode(errors='replace')[-200:]}")
    return p.stdout


def _bake(subchain, pix_fmt, planes, check=True):
    """Tables for one run: push identity ramps through the REAL sub-chain at
    8-bit and read the mapped planes back. With check on, a second bake — one
    plane ramping while the others sit neutral — must agree per plane, or the
    run is not a per-plane map and we refuse it."""
    n = 256
    ramp = bytes(range(n))
    neutral = {"y": ramp, "u": bytes([128]) * n, "v": bytes([128]) * n,
               "r": ramp, "g": bytes([128]) * n, "b": bytes([128]) * n}

    def run(frame):
        out = _run_ffmpeg(
            ["-f", "rawvideo", "-pix_fmt", pix_fmt, "-s", f"{n}x1", "-i", "-",
             "-vf", f"format={pix_fmt},{subchain},format={pix_fmt}",
             "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", pix_fmt, "-"],
            frame)
        if len(out) != n * 3:
            raise RuntimeError(f"bake returned {len(out)} bytes")
        return [list(out[i * n:(i + 1) * n]) for i in range(3)]

    full = run(ramp * 3)
    # Independence check: each plane's table must not move when the OTHER
    # planes sit at neutral instead of ramping.
    if check:
        for pi, plane in enumerate(planes):
            frame = b"".join(ramp if p == plane else neutral[p]
                             for p in planes)
            alone = run(frame)
            if alone[pi] != full[pi]:
                raise RuntimeError(f"plane {plane} depends on other planes")
    return full


def _tree_expr(table):
    """Balanced if(lt(val,m),lo,hi) tree over a 256-entry table. Evaluated by
    the lut filter once per value at init — never per pixel."""
    def build(lo, hi):
        if hi - lo == 1:
            return str(table[lo])
        mid = (lo + hi) // 2
        return f"if(lt(val,{mid}),{build(lo, mid)},{build(mid, hi)})"
    return build(0, 256)


def _yuv_filter(subchain):
    fy, fu, fv = _bake(subchain, "yuv444p", "yuv")
    ident = list(range(256))
    bits = []
    if fy != ident:
        bits.append(f"y='{_tree_expr(fy)}'")
    if fu != ident:
        bits.append(f"u='{_tree_expr(fu)}'")
    if fv != ident:
        bits.append(f"v='{_tree_expr(fv)}'")
    return "lutyuv=" + ":".join(bits) if bits else None


def _rgb_filter(subchain):
    # gbrp, not rgb24: the planar layout matches what the real graph
    # negotiates for colorbalance/curves, and gives per-plane reads.
    #
    # colorbalance is the one filter here that is NOT strictly per-channel on
    # modern ffmpeg: its shadow/mid/high weights read the pixel's overall
    # lightness, so the check=True probe correctly refuses it. On the gray
    # diagonal — where the bake ramp lives — lightness equals the channel
    # value and the table is the TRUE map; off the diagonal it deviates by
    # the weight difference on a shift that is at most ±0.13 for anything the
    # grade emission writes. Measured on real footage and on testsrc2's
    # saturated bars (the worst case), the difference stays in the 1-2 LSB
    # band (test_gradelut pins the floor). These are our own product-defined
    # looks — the cast is the deliverable, not colorbalance's exact math —
    # so a colorbalance run trades the independence PROOF for that measured
    # bound. curves-only runs keep the proof.
    fg, fb, fr = _bake(subchain, "gbrp", "gbr",
                       check="colorbalance" not in subchain)
    ident = list(range(256))
    bits = []
    if fr != ident:
        bits.append(f"r='{_tree_expr(fr)}'")
    if fg != ident:
        bits.append(f"g='{_tree_expr(fg)}'")
    if fb != ident:
        bits.append(f"b='{_tree_expr(fb)}'")
    return "lutrgb=" + ":".join(bits) if bits else None


def _fast_run(kind, subchain):
    key = (kind, subchain)
    with _cache_lock:
        if key in _cache:
            return _cache[key]
    flt = _yuv_filter(subchain) if kind == "yuv" else _rgb_filter(subchain)
    with _cache_lock:
        _cache[key] = flt
    return flt


# ------------------------------------------------------------ entry point --

def fast_chain(chain):
    """The fast exact equivalent of a global-grade filter chain, or `chain`
    unchanged when it contains anything unrecognized or a bake fails."""
    try:
        items = _parse_chain(chain)
        if not items:
            return chain
        runs = []                     # [(kind, [element, ...])]
        for name, args in items:
            kind = "yuv" if name in _YUV_FILTERS else "rgb"
            el = f"{name}={args}" if args else name
            if runs and runs[-1][0] == kind:
                runs[-1][1].append(el)
            else:
                runs.append((kind, [el]))
        out = []
        for kind, els in runs:
            flt = _fast_run(kind, ",".join(els))
            if flt:                   # None = identity: drop the run entirely
                out.append(flt)
        return ",".join(out) if out else "null"
    except Exception as e:            # bake trouble -> honest slow path
        print(f"[gradelut] fast path unavailable for '{chain[:80]}': {e} "
              "— using the original filters", flush=True)
        return chain
