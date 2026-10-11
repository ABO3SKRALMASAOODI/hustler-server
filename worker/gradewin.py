"""Grade windows and the hue key: the ``grade_window`` fx compiler (1E).

``fx_schemas/finish.py`` ``GradeWindowFx`` items compile here
(``gradewin:GRADE``) at the world or screen stage: a grade over a shot or a
section, ramped in and out over whole frames (mono -> colour landing ON a
drop frame), with colour isolation by hue, an imported or shot-matched
``.cube`` LUT, a split tone, and skin / brand protection.

How a grade is built (every step a per-value table, ROADMAP §2):

  LUT          ``lut3d`` (tetrahedral) of the asset's cube, strength baked
               into the cube; only items with a LUT pay for RGB
  luma         one ``lut`` table: exposure in linear light with a soft
               shoulder, contrast about mid-grey
  isolate      a hue key over the SOURCE chroma (``lut2`` on Cb/Cr at
               chroma resolution, unsmoothed) scales chroma: the kept
               hue is untouched, everything else goes to neutral grey
  chroma       ``lut`` (saturation) or ``lut2`` over (luma, chroma) when the
               offset depends on brightness: temperature, tint and the
               split tone fade to nothing at black, so blacks stay black
  protect      skin (and the brand hue) keep their SOURCE chroma
               (``maskedmerge`` of the chroma planes on a skin/brand key)
  window       ``finish.window_composite``: bit-exact outside the window
               and the region, ramps as per-frame alpha

A grade with only saturation / exposure / contrast is one ``lutyuv`` (about
1 ms a 1080p frame). The pinned peers live here too (INTERFACES_W1 §1.5):

  hue_key(colour, tolerance, softness) -> lutlib.HueKey
  hue_mask(plan, name, space, ctx)     the ``hue:<rrggbb>`` mask provider
  auto_hue(tool_ctx, start, end)       the subject's dominant hue (#rrggbb)
"""

import hashlib
import json
import math
import os

import faultinject
import fxgraph
import fxprims
import lutlib
import regions

FILTERS = ("split", "trim", "setpts", "tpad", "overlay", "format", "scale",
           "extractplanes", "mergeplanes", "lut", "lut2", "lutyuv", "gblur",
           "maskedmerge", "alphamerge", "nullsink", "null")
# composites through finish.window_composite (fxprims overlays): frames and
# their tags pass through
KEEPS_FRAME_TAGS = True

# chroma offsets at full white, 8-bit Cb/Cr units, per unit of the field
TEMP_U, TEMP_V = -11.0, 11.0          # +1 = warm
TINT_U, TINT_V = 6.0, 8.0             # +1 = magenta, -1 = green
SPLIT_AMOUNT = 0.42                   # share of the tone colour's chroma
# A brand hue is protected inside this narrow band.
BRAND_TOLERANCE, BRAND_SOFTNESS = 0.06, 0.08
DEFAULT_ACCENT = "#e9b44c"            # when a stale client stored "accent"

# measured CPU ms (ffmpeg 9.0, M5 Pro; 270x480 and 1080x1920 fits): a
# fixed setup (lut2 tables are built at init) plus per frame (fixed, per
# megapixel)
COST_FAST = (150.0, 1.4, 1.9)
COST_PLANAR = (450.0, 2.0, 3.2)
COST_LUT_EXTRA = (200.0, 0.5, 13.0)

# Test / refbench only: {asset key: local path} the renderer may read
# instead of fetching (``VALMERA_FX_ASSET_LOCALS`` holds the same map as
# JSON). Production never sets either.
ASSET_LOCALS = {}


# ── the hue key (peer for 1B) ────────────────────────────────────────────

def hue_key(colour, tolerance=lutlib.DEFAULT_TOLERANCE,
            softness=lutlib.DEFAULT_SOFTNESS):
    """The solved hue key of ``colour`` (#rrggbb): a lutlib.HueKey whose
    ``hue_weight`` (numpy) and ``hue_expr`` (ffmpeg lut2 over Cb, Cr) are
    one formula. INTERFACES_W1 §1.3: 1B's subject isolate takes M ∪/∩
    this key."""
    return lutlib.HueKey(colour, tolerance, softness)


def emit_skin(parts, u_label, v_label, y_label, out, *, uid, near=None):
    """Gray skin weight at CHROMA resolution from two chroma planes and the
    luma at chroma size (all consumed): the Cb/Cr skin box, gated by
    brightness (a dark red curtain shares skin's chroma, never its luma);
    ``near`` keeps a key's own colour out of it."""
    parts.append(f"[{u_label}][{v_label}]lut2=c0='"
                 f"{lutlib.skin_expr(near=near)}'[{uid}skc]")
    parts.append(f"[{y_label}][{uid}skc]lut2=c0='{lutlib.skin_luma_expr()}'"
                 f"[{out}]")
    return out


def emit_hue_weight(parts, u_label, v_label, out, key, *, uid,
                    y_label=None, smooth=0.7):
    """Gray weight map at CHROMA resolution: 255 where the picture is the
    key's colour. Consumes the two chroma planes and, when given, the luma
    at chroma size: then saturation is judged relative to brightness and
    skin is excluded as the key asks (lutlib.HueKey.exclude_skin) — exactly
    lutlib.hue_weight(..., y). Without luma: the chroma-only weight."""
    blur = f",gblur=sigma={smooth:.2f}" if smooth else ""
    if not y_label:
        parts.append(f"[{u_label}][{v_label}]lut2=c0='"
                     f"{lutlib.hue_expr(key)}'{blur}[{out}]")
        return out
    skin = key.exclude_skin
    n = 3 if skin else 2
    us = [f"{uid}hu{i}" for i in range(n)]
    vs = [f"{uid}hv{i}" for i in range(n)]
    parts.append(f"[{u_label}]split={n}" + "".join(f"[{x}]" for x in us))
    parts.append(f"[{v_label}]split={n}" + "".join(f"[{x}]" for x in vs))
    if skin:
        parts.append(f"[{y_label}]split[{uid}hy1][{uid}hy2]")
        y1, y2 = f"{uid}hy1", f"{uid}hy2"
    else:
        y1, y2 = y_label, None
    parts.append(f"[{us[0]}][{vs[0]}]lut2=c0='{lutlib.hue_angle_expr(key)}'"
                 f"[{uid}ha]")
    parts.append(f"[{us[1]}][{vs[1]}]lut2=c0='{lutlib.RADIUS2_EXPR}'"
                 f"[{uid}hr]")
    parts.append(f"[{y1}][{uid}hr]lut2=c0='{lutlib.rel_gate_expr(key)}'"
                 f"[{uid}hg]")
    last = out if not skin else f"{uid}hw"
    parts.append(f"[{uid}ha][{uid}hg]lut2=c0='x*y/255'"
                 f"{blur if not skin else ''}[{last}]")
    if skin:
        emit_skin(parts, us[2], vs[2], y2, f"{uid}hs", uid=f"{uid}h",
                  near=key.near)
        parts.append(f"[{uid}hw][{uid}hs]lut2=c0='x*(255-y)/255'{blur}"
                     f"[{out}]")
    return out


def hue_mask(plan, name, space, ctx):
    """Mask provider for ``hue:<rrggbb>`` (fxgraph.MASK_PEERS): where the
    picture at this stage is that colour, as a FRESH full-length gray
    program mask (W x H). Splits ``ctx.label`` and rebinds it to the
    pass-through branch (INTERFACES_W1 §1.3)."""
    if ctx is None or ":" not in str(name):
        return None
    hexs = str(name).split(":", 1)[1].strip().lower().lstrip("#")
    try:
        key = hue_key("#" + hexs)
    except ValueError:
        return None
    u = ctx.uid("hm")
    p = ctx.parts
    cw, ch = (int(ctx.W) + 1) // 2, (int(ctx.H) + 1) // 2
    p.append(f"[{ctx.label}]split[{u}k][{u}h]")
    ctx.label = f"{u}k"
    p.append(f"[{u}h]scale,format=yuv420p,extractplanes=y+u+v"
             f"[{u}y][{u}u][{u}v]")
    p.append(f"[{u}y]scale={cw}:{ch}:flags=area[{u}ys]")
    emit_hue_weight(p, f"{u}u", f"{u}v", f"{u}w", key, uid=u,
                    y_label=f"{u}ys")
    p.append(f"[{u}w]scale={int(ctx.W)}:{int(ctx.H)}:flags=bilinear,"
             f"format=gray[{u}m]")
    return f"{u}m"


# ── tables (numpy-free mirrors are in tests) ─────────────────────────────

def _soft_shoulder(y, knee=0.86):
    if y <= knee:
        return y
    return knee + (1 - knee) * math.tanh((y - knee) / (1 - knee))


def y_table(ev=None, contrast=None):
    """256 output values of the luma plane (8-bit video range)."""
    ev = float(ev or 0.0)
    c = 1.0 if contrast is None else float(contrast)
    out = []
    for v in range(256):
        if abs(ev) < 1e-9 and abs(c - 1.0) < 1e-9:
            out.append(v)
            continue
        x = (v - 16.0) / 219.0
        xc = min(max(x, 0.0), 1.0)
        if abs(ev) > 1e-9:
            lin = float(lutlib.srgb_to_linear(xc)) * (2.0 ** ev)
            y = float(lutlib.linear_to_srgb(lin))
            y = _soft_shoulder(y) if ev > 0 else y
        else:
            y = xc
        if abs(c - 1.0) > 1e-9:
            y = 0.46 + (y - 0.46) * c
        y = min(max(y, 0.0), 1.0)
        out.append(int(round(16 + 219 * y)))
    return out


def sat_table(s):
    s = float(s)
    return [int(round(min(240, max(16, 128 + (v - 128) * s))))
            for v in range(256)]


def _tone_offsets(item, accent=DEFAULT_ACCENT):
    """(U, V) chroma offsets per luma: (temp/tint at white, shadow tone,
    highlight tone, crossover) — all in 8-bit Cb/Cr units."""
    t = float(item.get("temperature") or 0.0)
    m = float(item.get("tint") or 0.0)
    a_u, a_v = TEMP_U * t + TINT_U * m, TEMP_V * t + TINT_V * m
    st = item.get("split_tone") or None
    s_u = s_v = h_u = h_v = 0.0
    cross = 0.5
    if st:
        def chroma(col):
            col = accent if col == "accent" else \
                {"white": "#ffffff", "black": "#000000"}.get(col, col)
            r, g, b = lutlib.hex_rgb(col)
            _, cb, cr = lutlib.rgb_to_ycc8([r / 255, g / 255, b / 255])
            return (cb - 128) * SPLIT_AMOUNT, (cr - 128) * SPLIT_AMOUNT
        s_u, s_v = chroma(st.get("shadows"))
        h_u, h_v = chroma(st.get("highlights"))
        cross = 0.5 + 0.25 * float(st.get("balance") or 0.0)
    return a_u, a_v, s_u, s_v, h_u, h_v, cross


def _sm(lo, hi, v):
    t = f"clip(({v}-({lo:.4f}))/{max(1e-6, hi - lo):.4f},0,1)"
    return f"({t}*{t}*(3-2*{t}))"


def chroma_expr(item, plane, accent=DEFAULT_ACCENT):
    """lut2 expression over x = luma (8-bit, at chroma size) and y = the
    chroma plane: saturation, then the luma-weighted offsets."""
    s = 1.0 if item.get("saturation") is None else float(item["saturation"])
    a_u, a_v, s_u, s_v, h_u, h_v, cross = _tone_offsets(item, accent)
    a, so, ho = (a_u, s_u, h_u) if plane == "u" else (a_v, s_v, h_v)
    lum = "clip((x-16)/219,0,1)"
    terms = [f"128+(y-128)*{s:.5f}"]
    if abs(a) > 1e-6:
        terms.append(f"{a:.4f}*pow({lum},0.6)")
    if abs(so) > 1e-6:
        terms.append(f"{so:.4f}*(1-{_sm(cross - 0.3, cross + 0.05, lum)})"
                     f"*{_sm(0.0, 0.12, lum)}")
    if abs(ho) > 1e-6:
        terms.append(f"{ho:.4f}*{_sm(cross - 0.05, cross + 0.3, lum)}")
    return f"clip({'+'.join(terms)},16,240)"


def chroma_value(item, plane, x, y, accent=DEFAULT_ACCENT):
    """numpy/py mirror of chroma_expr (tests and the proof board)."""
    s = 1.0 if item.get("saturation") is None else float(item["saturation"])
    a_u, a_v, s_u, s_v, h_u, h_v, cross = _tone_offsets(item, accent)
    a, so, ho = (a_u, s_u, h_u) if plane == "u" else (a_v, s_v, h_v)

    def sm(lo, hi, v):
        t = min(max((v - lo) / max(1e-6, hi - lo), 0.0), 1.0)
        return t * t * (3 - 2 * t)
    lum = min(max((x - 16) / 219.0, 0.0), 1.0)
    v = 128 + (y - 128) * s
    v += a * lum ** 0.6
    v += so * (1 - sm(cross - 0.3, cross + 0.05, lum)) * sm(0.0, 0.12, lum)
    v += ho * sm(cross - 0.05, cross + 0.3, lum)
    return min(240.0, max(16.0, v))


def _brand_key(item):
    want = set(item.get("protect") or ())
    if "brand" not in want or not item.get("brand_hue"):
        return None
    try:
        return hue_key(item["brand_hue"], BRAND_TOLERANCE, BRAND_SOFTNESS)
    except ValueError:
        return None


def protect_kinds(item):
    """('skin'?, brand HueKey or None) the grade must leave untouched."""
    return ("skin" in set(item.get("protect") or ())), _brand_key(item)


# ── what a render does with one item ─────────────────────────────────────

def _needs(item, brand=None):
    """Which parts of the planar graph an item needs."""
    iso = item.get("isolate") is not None
    skin, bkey = protect_kinds(item)
    prot = bool(skin or bkey)
    tone = any(item.get(k) for k in ("temperature", "tint", "split_tone"))
    sat = item.get("saturation") is not None and \
        abs(float(item["saturation"]) - 1.0) > 1e-9
    lum = (item.get("ev") not in (None, 0, 0.0)) or \
        (item.get("contrast") is not None and
         abs(float(item["contrast"]) - 1.0) > 1e-9)
    return {"iso": iso, "protect": prot, "tone": tone, "sat": sat,
            "lum": lum, "lut": item.get("lut") is not None}


def _state(plan):
    return plan.__dict__.setdefault("_gradewin_state", {})


def _asset_local(plan, key):
    """A local path for a project asset (the LUT), or None. Never raises."""
    if not key:
        return None
    hit = ASSET_LOCALS.get(key)
    if not hit:
        try:
            hit = json.loads(os.environ.get("VALMERA_FX_ASSET_LOCALS")
                             or "{}").get(key)
        except ValueError:
            hit = None
    if hit and os.path.exists(hit):
        return hit
    if not plan.workdir:
        return None
    try:
        import renderer
        local = renderer._job_cached_source(key, plan.workdir)
        if local:
            return local
    except Exception:  # noqa: BLE001
        pass
    try:
        import storage
        local = os.path.join(plan.workdir, "fxlut_" + hashlib.sha256(
            key.encode()).hexdigest()[:16] + ".cube")
        if not os.path.exists(local):
            storage.download_to(key, local)
        return local
    except Exception:  # noqa: BLE001
        return None


def load_lut(path, strength=1.0):
    with open(path, "rb") as f:
        raw = f.read(lutlib.CUBE_MAX_BYTES + 1)
    lut = lutlib.parse_cube(raw)
    return lutlib.mix(lut, strength)


def _write_cube(plan, item, lut):
    text = lutlib.cube_text(lut, title=f"valmera {item.get('id')}")
    sha = hashlib.sha256(text.encode()).hexdigest()[:16]
    path = os.path.join(plan.workdir, f"fxgrade_{sha}.cube")
    if not os.path.exists(path):
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    return path


def _escape(path):
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


class _Grade:
    FILTERS = FILTERS
    KEEPS_FRAME_TAGS = KEEPS_FRAME_TAGS

    def prepare(self, plan, item):
        if not plan.W or not plan.H:
            return ("omit", "no geometry for the grade")
        st = {"rung": "full", "reason": "ok", "lut_path": None}
        if item.get("region"):
            got = regions.prepare(plan, item, item["region"])
            if got:
                return got
        lut = item.get("lut")
        if lut:
            other = any(item.get(k) is not None for k in (
                "saturation", "ev", "contrast", "temperature", "tint",
                "isolate", "split_tone"))
            why = None
            if fxgraph.filter_missing("lut3d"):
                why = "this ffmpeg lacks lut3d"
            elif not plan.workdir:
                why = "no workdir for the LUT"
            else:
                if faultinject.fault("oom:fx_precompute"):
                    raise MemoryError("the LUT bake was OOM-killed")
                path = _asset_local(plan, lut.get("asset_key"))
                if not path:
                    why = "the LUT asset could not be fetched"
                else:
                    try:
                        cube = load_lut(path, lut.get("strength", 1.0))
                        st["lut_path"] = _write_cube(plan, item, cube)
                        st["lut_size"] = cube.size
                    except (OSError, lutlib.LutError) as e:
                        why = f"the LUT is unusable ({str(e)[:100]})"
            if why:
                if not other:
                    return ("omit", why)
                st.update(rung="reduced", reason=f"graded without its LUT: "
                          f"{why}")
        _state(plan)[item.get("id")] = st
        if st["rung"] != "full":
            return (st["rung"], st["reason"])
        return None

    def cost_ms(self, plan, item):
        f0, f1 = fxprims.frame_span(item["start"], item["end"], plan.fps)
        mpx = float(plan.W or 1080) * float(plan.H or 1920) / 1e6
        need = _needs(item, item.get("brand_hue"))
        fixed, per_f, per_mpx = COST_FAST
        if need["iso"] or need["protect"] or need["tone"]:
            fixed, per_f, per_mpx = COST_PLANAR
        if need["lut"]:
            fixed += COST_LUT_EXTRA[0]
            per_f += COST_LUT_EXTRA[1]
            per_mpx += COST_LUT_EXTRA[2]
        return fixed + (f1 - f0) * (per_f + mpx * per_mpx)

    def landing(self, plan, item):
        """The frame a grade LANDS on (proof strips, land_err_f): a grade
        that ramps OUT lands where it is fully gone (the drop it hands
        colour back on: its end frame); any other lands on its first."""
        if item.get("ramp_out_f"):
            return [fxprims.frame_at(float(item["end"]), plan.fps)]
        return [fxprims.frame_at(float(item["start"]), plan.fps)]

    def stage(self, ctx, items):
        label = ctx.label
        for it in items:
            label = self._emit(ctx, label, it)
        return label

    def _emit(self, ctx, label, it):
        import finish
        st = _state(ctx.plan).get(it.get("id"), {})
        f0, f1 = ctx.frames(it)
        mask = None
        if it.get("region"):
            mask = finish.region_pad(ctx, it)
            if mask is None:
                raise ValueError("the grade's region could not be built")

        def build(parts, src, uid):
            return grade_graph(parts, src, uid, it, ctx.W, ctx.H,
                               lut_path=st.get("lut_path"))
        return finish.window_composite(
            ctx, label, [(f0, f1)], build, mask=mask,
            ramp_in=it.get("ramp_in_f") or 0,
            ramp_out=it.get("ramp_out_f") or 0, uid=ctx.uid("gw"))


def _fan(parts, label, n, tag):
    """``n`` labels carrying ``label`` (a split when n > 1)."""
    if n <= 1:
        return [label]
    outs = [f"{tag}{i}" for i in range(n)]
    parts.append(f"[{label}]split={n}" + "".join(f"[{o}]" for o in outs))
    return outs


def grade_graph(parts, src, u, item, W, H, *, lut_path=None,
                accent=DEFAULT_ACCENT):
    """The graded picture of ``src`` (yuv420p, W x H, consumed) -> label.

    Planes: the BASE (the picture, through the LUT when there is one) is
    graded; weights (the hue key, skin, the brand hue) are read from the
    SOURCE planes, so a LUT never moves what a key selects."""
    import finish
    need = _needs(item, item.get("brand_hue"))
    if lut_path is None:
        need["lut"] = False
    planar = need["iso"] or need["protect"] or need["tone"]
    if not planar and not need["lut"]:
        yt = finish.table_expr(y_table(item.get("ev"), item.get("contrast")))
        s = 1.0 if item.get("saturation") is None else \
            float(item["saturation"])
        ct = finish.table_expr(sat_table(s))
        parts.append(f"[{src}]lutyuv=y='{yt}':u='{ct}':v='{ct}'[{u}g]")
        return f"{u}g"
    cw, ch = (int(W) + 1) // 2, (int(H) + 1) // 2
    key = None
    if need["iso"]:
        iso = item["isolate"]
        key = hue_key(iso["hue"], iso.get("tolerance", 0.12),
                      iso.get("softness", 0.1))
    skin_p, bkey = protect_kinds(item)
    # consumers of the SOURCE planes
    n_su = (1 if key is not None else 0) + (1 if skin_p else 0) + \
        (1 if bkey else 0) + (1 if need["protect"] else 0)
    n_sy = (1 if key is not None else 0) + (1 if skin_p else 0)
    need_src = n_su > 0
    if need["lut"]:
        if need_src:
            parts.append(f"[{src}]split[{u}s0][{u}s1]")
            orig, base = f"{u}s0", f"{u}s1"
        else:
            orig, base = None, src
        parts.append(f"[{base}]lut3d=file='{_escape(lut_path)}'"
                     f":interp=tetrahedral,scale,format=yuv420p[{u}L]")
        parts.append(f"[{u}L]extractplanes=y+u+v[{u}y][{u}u][{u}v]")
        if orig is not None:
            parts.append(f"[{orig}]extractplanes=y+u+v"
                         f"[{u}sy0][{u}su0][{u}sv0]")
            if n_sy:
                parts.append(f"[{u}sy0]scale={cw}:{ch}:flags=area[{u}sy]")
            else:
                parts.append(f"[{u}sy0]nullsink")
    else:
        parts.append(f"[{src}]extractplanes=y+u+v[{u}y0][{u}u0][{u}v0]")
        if need_src:
            yy = _fan(parts, f"{u}y0", 2 if n_sy else 1, f"{u}yf")
            parts.append(f"[{yy[0]}]null[{u}y]")
            if n_sy:
                parts.append(f"[{yy[1]}]scale={cw}:{ch}:flags=area[{u}sy]")
            uu = _fan(parts, f"{u}u0", 2, f"{u}uf")
            vv = _fan(parts, f"{u}v0", 2, f"{u}vf")
            parts.append(f"[{uu[0]}]null[{u}u]")
            parts.append(f"[{vv[0]}]null[{u}v]")
            parts.append(f"[{uu[1]}]null[{u}su0]")
            parts.append(f"[{vv[1]}]null[{u}sv0]")
        else:
            parts.append(f"[{u}y0]null[{u}y]")
            parts.append(f"[{u}u0]null[{u}u]")
            parts.append(f"[{u}v0]null[{u}v]")
    sus = _fan(parts, f"{u}su0", n_su, f"{u}sU") if need_src else []
    svs = _fan(parts, f"{u}sv0", n_su, f"{u}sV") if need_src else []
    sys_ = _fan(parts, f"{u}sy", n_sy, f"{u}sY") if n_sy else []
    # luma
    yl = f"{u}y"
    if need["lum"]:
        yt = finish.table_expr(y_table(item.get("ev"), item.get("contrast")))
        parts.append(f"[{yl}]lut=c0='{yt}'[{u}y1]")
        yl = f"{u}y1"
    ys = None
    if need["tone"]:
        yy = _fan(parts, yl, 3, f"{u}yt")
        yl = yy[0]
        parts.append(f"[{yy[1]}]scale={cw}:{ch}:flags=area[{u}ysu]")
        parts.append(f"[{yy[2]}]scale={cw}:{ch}:flags=area[{u}ysv]")
        ys = (f"{u}ysu", f"{u}ysv")
    cu, cv = f"{u}u", f"{u}v"
    if key is not None:
        # unsmoothed: the weight scales chroma AT chroma resolution and
        # the decoder's upsampling softens it; a blur here spread the kept
        # colour onto its neighbours (V04 neon: fringe C* 3.5 -> ~1 without,
        # kept dE no worse; measured on AT-1E-1's frames)
        emit_hue_weight(parts, sus.pop(0), svs.pop(0), f"{u}wi", key,
                        uid=f"{u}i", y_label=sys_.pop(0), smooth=0)
        wi = _fan(parts, f"{u}wi", 2, f"{u}wf")
        parts.append(f"[{cu}][{wi[0]}]lut2=c0='128+(x-128)*y/255'[{u}ui]")
        parts.append(f"[{cv}][{wi[1]}]lut2=c0='128+(x-128)*y/255'[{u}vi]")
        cu, cv = f"{u}ui", f"{u}vi"
    if need["tone"]:
        parts.append(f"[{ys[0]}][{cu}]lut2=c0='"
                     f"{chroma_expr(item, 'u', accent)}'[{u}ut]")
        parts.append(f"[{ys[1]}][{cv}]lut2=c0='"
                     f"{chroma_expr(item, 'v', accent)}'[{u}vt]")
        cu, cv = f"{u}ut", f"{u}vt"
    elif need["sat"]:
        ct = finish.table_expr(sat_table(item["saturation"]))
        parts.append(f"[{cu}]lut=c0='{ct}'[{u}us]")
        parts.append(f"[{cv}]lut=c0='{ct}'[{u}vs]")
        cu, cv = f"{u}us", f"{u}vs"
    if need["protect"]:
        ws = []
        if skin_p:
            emit_skin(parts, sus.pop(0), svs.pop(0), sys_.pop(0),
                      f"{u}pk", uid=f"{u}p")
            ws.append(f"{u}pk")
        if bkey is not None:
            parts.append(f"[{sus.pop(0)}][{svs.pop(0)}]lut2=c0='"
                         f"{lutlib.hue_expr(bkey)}'[{u}pb]")
            ws.append(f"{u}pb")
        if len(ws) == 2:
            parts.append(f"[{ws[0]}][{ws[1]}]lut2=c0='max(x,y)'[{u}pm]")
            pw0 = f"{u}pm"
        else:
            pw0 = ws[0]
        parts.append(f"[{pw0}]gblur=sigma=0.7[{u}pw]")
        pw = _fan(parts, f"{u}pw", 2, f"{u}pf")
        parts.append(f"[{cu}][{sus.pop(0)}][{pw[0]}]maskedmerge[{u}up]")
        parts.append(f"[{cv}][{svs.pop(0)}][{pw[1]}]maskedmerge[{u}vp]")
        cu, cv = f"{u}up", f"{u}vp"
    assert not sus and not svs and not sys_, (sus, svs, sys_)
    parts.append(f"[{yl}][{cu}][{cv}]mergeplanes=0x001020:yuv420p[{u}g]")
    return f"{u}g"


GRADE = _Grade()


# ── auto hue (write time, browserless) ───────────────────────────────────

def _subject_ellipse(h, w):
    import numpy as np
    yy, xx = np.mgrid[0:h, 0:w]
    return (((xx / w - 0.5) / 0.32) ** 2 +
            ((yy / h - 0.72) / 0.42) ** 2) <= 1.0


def hue_from_frames(frames):
    """(hex, share) of the subject's dominant colour over RGB frames (the
    centre-weighted region below the face line; skin never counts), or the
    whole frame's when the subject holds none."""
    best = None
    for rgb in frames:
        h, w = rgb.shape[:2]
        hexv, share = lutlib.dominant_hue(rgb, _subject_ellipse(h, w),
                                          min_share=0.03)
        if hexv is None:
            hexv, share = lutlib.dominant_hue(rgb, None, min_share=0.01)
        if hexv and (best is None or share > best[1]):
            best = (hexv, share)
    return best or (None, 0.0)


def key_from_frames(frames, colour):
    """(tolerance, softness) that hold ``colour`` as it actually spreads in
    the shot. The colour's own pixels are found with a narrow key first
    (+-15 deg) and then within their measured spread, so neighbours that
    merely lean the same way (shadowed skin next to a deep red curtain, a
    brown wall) never widen it; the half-width covers the 5-95 % spread
    + 3 deg, never under 7 deg or over 32 deg."""
    import numpy as np
    base = hue_key(colour, 0.1, 0.0)
    rows = []
    for rgb in frames:
        c = lutlib.rgb_to_ycc8(rgb.reshape(-1, 3) / 255.0)
        rad = np.hypot(c[:, 1] - 128.0, c[:, 2] - 128.0)
        keep = (rad >= base.sat_lo) & \
            (lutlib.skin_weight(c[:, 1], c[:, 2], c[:, 0]) < 0.5)
        a = np.arctan2(c[keep, 2] - 128.0, c[keep, 1] - 128.0)
        rows.append(np.degrees((a - base.angle + np.pi) % (2 * np.pi)
                               - np.pi))
    d = np.concatenate(rows) if rows else np.zeros(0)
    win = 15.0
    for _pass in range(2):
        sel = d[np.abs(d) <= win]
        if sel.size < 200:
            return lutlib.DEFAULT_TOLERANCE, lutlib.DEFAULT_SOFTNESS
        lo, hi = np.percentile(sel, [5, 95])
        win = max(abs(lo), abs(hi)) + 6.0
    half = min(32.0, max(7.0, max(abs(lo), abs(hi)) + 3.0))
    return round(half / 180.0, 3), 0.06


def auto_hue(tool_ctx, start, end):
    """The dominant saturated hue of the SUBJECT region over program
    [start, end), as '#rrggbb', or None. Solved at write time from proxy
    frames (no model): the subject region is the centre-weighted frame
    below the face line; skin never counts. When the subject holds no clear
    colour, the whole frame's dominant hue is used (the red curtain behind a
    speaker)."""
    try:
        frames = _sample_frames(tool_ctx, float(start), float(end), n=3)
    except Exception:  # noqa: BLE001
        return None
    return hue_from_frames(frames)[0] if frames else None


def auto_key(tool_ctx, start, end, colour=None):
    """(hex, tolerance, softness) for isolate_colour: the hue (solved when
    ``colour`` is None) and the key width that colour needs in this
    window. (None, None, None) when nothing can be solved."""
    try:
        frames = _sample_frames(tool_ctx, float(start), float(end), n=3)
    except Exception:  # noqa: BLE001
        return None, None, None
    if not frames:
        return colour, None, None
    if colour is None:
        colour = hue_from_frames(frames)[0]
        if colour is None:
            return None, None, None
    tol, soft = key_from_frames(frames, colour)
    return colour, tol, soft


def _sample_frames(tool_ctx, start, end, n=3):
    """RGB uint8 frames of the main video at n program times in
    [start, end) (proxy resolution)."""
    import numpy as np
    from PIL import Image
    import media
    from timeline import Timeline
    edl = (tool_ctx.latest_edl() or {}).get("json") or {}
    tl = Timeline(edl.get("keep") or [[0, tool_ctx.duration]],
                  edl.get("inserts") or [], edl.get("speed") or [])
    span = max(0.04, end - start)
    out = []
    proxy = tool_ctx.proxy_path()
    for k in range(n):
        t = start + span * (k + 0.5) / n
        src_t = tl.out_to_src(t)
        if src_t is None:
            continue
        dst = os.path.join(tool_ctx.workdir, f"autohue_{k}_{t:.2f}.jpg")
        media.frame_at(proxy, src_t, dst)
        out.append(np.asarray(Image.open(dst).convert("RGB")))
    return out
