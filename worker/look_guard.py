"""Brightness and readability measured for stacked looks (RS_ENGINE, 31 Oct).

Ticket #56 (user 2672, Dead by Daylight, 230 s): the trial editor stacked
set_grade_custom (contrast 1.12, shadows -0.1), the 'cinematic' preset
(contrast 1.12 again, brightness -0.02), a 0.3 vignette and grain on dark
gameplay. The delivered frames were about half as bright as the source, the
HUD perks and names faded and the killer nearly vanished. Nothing measured
the look against the source.

After every look write (grade preset, custom grade, grade windows, grain or
vignette) this measures program frames from the proxy through the exact
chain the render runs (renderer.cutaway_look_chain) against the untouched
source: mean luma, the bright detail (90th percentile: HUD, text, UI), the
share crushed to black and the mean saturation. The measurement goes back to
the editor as information on the tool result; the editor's look stands as
written and the editor decides whether to ease it (owner, 16 Nov 2026: no
automatic rewrite of the model's work, and no keyword reading of the
request). Never raises; a failed measurement says nothing.

The colour (EDIT-CORE gate, 17 Nov 2026, p5873): "warm, sun-touched ...
creamy neutrals" asked twice; the editor alternated tint +0.15 / -0.15 for
three passes and delivered whites at a* +14.7, b* +0.3 (pink-mauve, from the
source's +0.6 / +10.2) while it could only guess from frames which way the
cast moved. Every look write now also gets the CIELAB of the picture's whites
and mid-tones, graded against the source on the same pixels: what the grade
made, in numbers the editor can steer by. Information, never a gate.
"""
import json
import os
import subprocess

LOOK_TOOLS = frozenset((
    "set_color_grade", "set_grade_custom", "add_stylize", "apply_look",
    "apply_edit_batch", "apply_format_pack", "match_grade", "set_grade_window",
    "apply_recipe", "set_intensity", "add_custom_filter", "remove_custom_filter",
    "remove_stylize"))
SAMPLES = 4
DARK = 0.06                 # luma under this reads as black
# limits: graded / source
LUMA_MIN = 0.78             # mean luma kept
BRIGHT_MIN = 0.85           # 90th-percentile luma kept (HUD, text, UI)
CRUSH_MAX = 0.10            # extra share of the frame crushed to black


def signature(edl):
    """The look part of an EDL (what this guard measures)."""
    edl = edl or {}
    fx = edl.get("effects") or {}
    wins = [f for f in edl.get("fx") or []
            if isinstance(f, dict) and f.get("type") == "grade_window"]
    return json.dumps([fx.get("grade"), fx.get("grade_custom"),
                       fx.get("stylize"), fx.get("custom"), wins],
                      sort_keys=True, default=str)


def _custom_chains(edl, t):
    """The whole-picture custom filter chains (add_custom_filter) playing at
    programme second t."""
    out = []
    for c in ((edl or {}).get("effects") or {}).get("custom") or []:
        if not isinstance(c, dict) or not c.get("chain"):
            continue
        a, b = c.get("start"), c.get("end")
        if (a is None or float(a) <= t) and (b is None or t < float(b)):
            out.append(str(c["chain"]))
    return out


def frame_lab(path, chain=""):
    """(L, a, b) arrays (CIELAB, D65) of one image through ``chain``, or
    None."""
    import numpy as np
    vf = (chain + "," if chain else "") + "scale=192:-2,format=rgb24"
    try:
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-nostdin", "-i", path, "-vf", vf,
             "-frames:v", "1", "-f", "rawvideo", "-"],
            capture_output=True, timeout=30).stdout
    except Exception:  # noqa: BLE001
        return None
    if not raw or len(raw) % 3:
        return None
    rgb = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.float64) / 255.0
    lin = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    xyz = lin @ np.array([[0.4124, 0.2126, 0.0193],
                          [0.3576, 0.7152, 0.1192],
                          [0.1805, 0.0722, 0.9505]])
    xyz = xyz / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > (6 / 29) ** 3, np.cbrt(xyz), xyz / (3 * (6 / 29) ** 2) + 4 / 29)
    L = 116 * f[:, 1] - 16
    return L, 500 * (f[:, 0] - f[:, 1]), 200 * (f[:, 1] - f[:, 2])


def colour(src_labs, graded_labs):
    """{'whites': (src (L, a, b), graded (L, a, b)), 'mids': ...}: medians
    over the same pixels in source and graded frames. Whites are each
    frame's brightest tenth of the SOURCE (at least L 55), mids its 30th-70th
    percentile, so the grade is read where it moved the colour."""
    import numpy as np
    regions = {"whites": ([], []), "mids": ([], [])}
    whole = ([], [])
    for src, gr in zip(src_labs, graded_labs):
        if src is None or gr is None or len(src[0]) != len(gr[0]):
            continue
        whole[0].append(np.asarray(src[0]))
        whole[1].append(np.asarray(gr[0]))
        L = src[0]
        hi = L >= max(55.0, float(np.percentile(L, 90)))
        lo, up = np.percentile(L, 30), np.percentile(L, 70)
        mid = (L >= lo) & (L <= up)
        for name, mask in (("whites", hi), ("mids", mid)):
            if mask.sum() >= 8:
                regions[name][0].append(np.stack([c[mask] for c in src], 1))
                regions[name][1].append(np.stack([c[mask] for c in gr], 1))
    out = {}
    for name, (a, b) in regions.items():
        if a:
            out[name] = (tuple(float(v) for v in np.median(np.concatenate(a), 0)),
                         tuple(float(v) for v in np.median(np.concatenate(b), 0)))
    if whole[0]:
        # the whole picture's lightness: its mean and its darkest tenth
        # (p5873 v2a: "warm, sun-touched" came out mean L 54 -> 45)
        sl, gl = np.concatenate(whole[0]), np.concatenate(whole[1])
        out["lightness"] = ((float(sl.mean()), float(np.percentile(sl, 10))),
                            (float(gl.mean()), float(np.percentile(gl, 10))))
    return out


def _lean(a, b):
    words = []
    if a >= 3:
        words.append("magenta/red")
    elif a <= -3:
        words.append("green")
    if b >= 6:
        words.append("yellow/warm")
    elif b <= -3:
        words.append("blue/cool")
    return " and ".join(words) or "neutral"


def colour_line(c):
    """The LOOK COLOUR line for colour()'s result, '' when empty."""
    if not c:
        return ""
    parts = []
    if "lightness" in c:
        (sm, s10), (gm, g10) = c["lightness"]
        d = gm - sm
        word = (f"darker by {-d:.0f}" if d <= -2 else f"lighter by {d:.0f}"
                if d >= 2 else "as light as the source")
        parts.append(f"picture mean L {sm:.0f} -> {gm:.0f} ({word}; darkest "
                     f"tenth L {s10:.0f} -> {g10:.0f})")
    for name in ("whites", "mids"):
        if name not in c:
            continue
        (sL, sa, sb), (gL, ga, gb) = c[name]
        parts.append(f"{name} L {sL:.0f} a* {sa:+.1f} b* {sb:+.1f} ({_lean(sa, sb)}) -> "
                     f"L {gL:.0f} a* {ga:+.1f} b* {gb:+.1f} ({_lean(ga, gb)}; a* {ga - sa:+.1f}, "
                     f"b* {gb - sb:+.1f})")
    if not parts:
        return ""
    return ("\nLOOK COLOUR MEASURED (CIELAB, source -> graded, same pixels; the grain is in it, "
            "halation and glows are not): " + "; ".join(parts) + ". a* + is magenta/red, - green; "
            "b* + is yellow/warm, - blue/cool; a neutral white sits near a* 0, b* 0. Warm, golden, "
            "sun-touched or creamy looks keep the picture's mean L at the source's or above (light "
            "is part of warmth); a lower L reads as dark. Steer the next adjustment by these numbers.")


def frame_stats(path, chain=""):
    """(mean luma, p90 luma, dark share, mean saturation) of one image
    through ``chain`` (an ffmpeg filter string), or None."""
    import numpy as np
    vf = (chain + "," if chain else "") + "scale=192:-2,format=rgb24"
    try:
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-nostdin", "-i", path, "-vf", vf,
             "-frames:v", "1", "-f", "rawvideo", "-"],
            capture_output=True, timeout=30).stdout
    except Exception:  # noqa: BLE001
        return None
    if not raw or len(raw) % 3:
        return None
    px = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.float32) / 255.0
    luma = px @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    mx, mn = px.max(1), px.min(1)
    sat = (mx - mn) / np.maximum(mx, 1e-3)
    return (float(luma.mean()), float(np.percentile(luma, 90)),
            float((luma < DARK).mean()), float(sat.mean()))


def compare(src, graded):
    """Aggregate ratios over the samples: {'luma', 'bright', 'crush',
    'sat'}."""
    pairs = [(a, b) for a, b in zip(src, graded) if a and b]
    if not pairs:
        return None
    sl = sum(a[0] for a, _ in pairs); gl = sum(b[0] for _, b in pairs)
    sb = sum(a[1] for a, _ in pairs); gb = sum(b[1] for _, b in pairs)
    ss = sum(a[3] for a, _ in pairs); gs = sum(b[3] for _, b in pairs)
    crush = sum(b[2] - a[2] for a, b in pairs) / len(pairs)
    return {"luma": gl / max(sl, 1e-4), "bright": gb / max(sb, 1e-4),
            "crush": crush, "sat": gs / max(ss, 1e-4),
            "src_luma": sl / len(pairs), "graded_luma": gl / len(pairs)}


def verdict(r):
    """The measured departures from the source a reader would notice (empty:
    it reads like the source). Information for the editor, never a gate."""
    if not r:
        return []
    bad = []
    if r["luma"] < LUMA_MIN:
        bad.append(f"{100 * (1 - r['luma']):.0f}% darker than the source")
    if r["bright"] < BRIGHT_MIN:
        bad.append(f"its bright detail (HUD, text, UI) {100 * (1 - r['bright']):.0f}% dimmer")
    if r["crush"] > CRUSH_MAX:
        bad.append(f"{100 * r['crush']:.0f}% more of the frame crushed to black")
    return bad


def _samples(ctx, edl):
    from timeline import Timeline
    keep = edl.get("keep") or []
    if not keep:
        return []
    tl = Timeline(keep, edl.get("inserts") or [], edl.get("speed") or [])
    picks = []
    for i in range(SAMPLES):
        t = tl.out_duration * (i + 0.5) / SAMPLES
        s = tl.out_to_src(t)
        if s is not None:
            picks.append((t, s))
    return picks


def _chain_at(edl, t):
    import renderer
    return ",".join(renderer.cutaway_look_chain(edl, t, 0.04) + _custom_chains(edl, t))


def _measure(ctx, edl, picks, raws):
    out = []
    for (t, _s), raw in zip(picks, raws):
        out.append(frame_stats(raw, _chain_at(edl, t)) if raw else None)
    return out


def after_write(ctx, before_sig):
    """'' or the LOOK MEASURED line after a look write: what the look does to
    the picture's brightness against the source, measured on program frames.
    Information only: nothing is rewritten. Never raises."""
    try:
        row = ctx.latest_edl()
        edl = row["json"]
        if signature(edl) == before_sig:
            return ""
        fx = edl.get("effects") or {}
        if not (fx.get("grade") or fx.get("grade_custom") or fx.get("custom") or any(
                (s or {}).get("kind") == "vignette" for s in fx.get("stylize") or [])):
            return ""
        picks = _samples(ctx, edl)
        if len(picks) < 2:
            return ""
        import media
        proxy = ctx.proxy_path()
        raws = []
        for i, (_t, s) in enumerate(picks):
            raw = os.path.join(ctx.workdir, f"lookguard_{i}.jpg")
            try:
                media.frame_at(proxy, s, raw)
                raws.append(raw)
            except Exception:  # noqa: BLE001
                raws.append(None)
        src = [frame_stats(r) if r else None for r in raws]
        r = compare(src, _measure(ctx, edl, picks, raws))
        bad = verdict(r)
        hue = ""
        try:
            hue = colour_line(colour(
                [frame_lab(raw) if raw else None for raw in raws],
                [frame_lab(raw, _chain_at(edl, t)) if raw else None
                 for (t, _s), raw in zip(picks, raws)]))
        except Exception as exc:  # noqa: BLE001 - information only
            print(f"[look_guard] colour skipped ({str(exc)[:120]})", flush=True)
        if not bad:
            return hue
        return (f"\nLOOK MEASURED (information; the look is saved as written): on "
                f"{len(picks)} frames against the source, the picture is " + "; ".join(bad)
                + f" (mean brightness {r['luma']:.2f}x the source's, saturation "
                f"{r['sat']:.2f}x). Keep it if that is the look the request wants; "
                "otherwise ease the contrast, shadow drop or vignette so the content reads.") + hue
    except Exception as exc:  # noqa: BLE001
        print(f"[look_guard] skipped ({str(exc)[:160]})", flush=True)
        return ""
