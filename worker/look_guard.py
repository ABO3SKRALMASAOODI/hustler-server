"""Brightness and readability guard for stacked looks (RS_ENGINE, 31 Oct).

Ticket #56 (user 2672, Dead by Daylight, 230 s): the trial editor stacked
set_grade_custom (contrast 1.12, shadows -0.1), the 'cinematic' preset
(contrast 1.12 again, brightness -0.02), a 0.3 vignette and grain on dark
gameplay. The delivered frames were about half as bright as the source, the
HUD perks and names faded and the killer nearly vanished, on a request that
said "keep the original gameplay natural and clear ... avoid
oversaturation". Nothing measured the look against the source.

After every look write (grade preset, custom grade, grade windows, grain or
vignette) this measures program frames from the proxy through the exact
chain the render runs (renderer.cutaway_look_chain) against the untouched
source: mean luma, the bright detail (90th percentile: HUD, text, UI), the
share crushed to black and the mean saturation. A look that darkens the
picture past the limit, dims its bright detail or crushes it is TEMPERED in
the edit (vignette down, the shadow drop and doubled contrast eased, then
exposure lifted) until it reads, and the tool result says what changed. A
request to keep it natural / clear / not oversaturated tightens the limits
and caps the saturation gain. Never raises; a failed measurement changes
nothing.
"""
import json
import os
import re
import subprocess

LOOK_TOOLS = frozenset((
    "set_color_grade", "set_grade_custom", "add_stylize", "apply_look",
    "apply_edit_batch", "apply_format_pack", "match_grade", "set_grade_window",
    "apply_recipe", "set_intensity"))
SAMPLES = 4
DARK = 0.06                 # luma under this reads as black
# limits: graded / source
LUMA_MIN = 0.78             # mean luma kept
LUMA_MIN_NATURAL = 0.88     # ... when the user asked for natural / clear
BRIGHT_MIN = 0.85           # 90th-percentile luma kept (HUD, text, UI)
CRUSH_MAX = 0.10            # extra share of the frame crushed to black
SAT_MAX_NATURAL = 1.30      # saturation gain under "no oversaturation"
_NATURAL = re.compile(
    r"\b(?:natural|clear|clean|readable|legible|visible|not\s+too\s+dark|"
    r"keep\s+(?:it|the\s+\w+)\s+(?:natural|clear|visible)|"
    r"(?:avoid|no|without|not)\s+(?:over-?saturat\w*|too\s+saturated|"
    r"too\s+dark|crush\w*))\b", re.I)


def signature(edl):
    """The look part of an EDL (what this guard measures)."""
    edl = edl or {}
    fx = edl.get("effects") or {}
    wins = [f for f in edl.get("fx") or []
            if isinstance(f, dict) and f.get("type") == "grade_window"]
    return json.dumps([fx.get("grade"), fx.get("grade_custom"),
                       fx.get("stylize"), wins], sort_keys=True, default=str)


_DARK = re.compile(r"\b(?:dark(?:er)?|moody|low[- ]?key|night(?:time)?|noir|"
                   r"gloomy|shadowy)\b", re.I)


def wants_natural(text):
    return bool(_NATURAL.search(str(text or "")))


_VIBRANT = re.compile(r"\b(?:vibrant|vivid|rich|colou?rful|pop(?:py|s)?|"
                      r"saturated|punchy)\b", re.I)


def wants_vibrant(text):
    return bool(_VIBRANT.search(str(text or "")))


def wants_dark(text):
    """An explicit ask for a darker picture (and no 'keep it natural')."""
    return bool(_DARK.search(str(text or ""))) and not wants_natural(text)


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


def verdict(r, natural, dark=False):
    """The list of problems in a comparison (empty: it reads). ``dark``: the
    user asked for a darker picture, so only a near-black result fails."""
    if not r:
        return []
    bad = []
    luma_min = (LUMA_MIN_NATURAL if natural else 0.55 if dark else LUMA_MIN)
    if r["luma"] < luma_min:
        bad.append(f"{100 * (1 - r['luma']):.0f}% darker than the source")
    if r["bright"] < (0.7 if dark and not natural else BRIGHT_MIN):
        bad.append(f"its bright detail (HUD, text, UI) {100 * (1 - r['bright']):.0f}% dimmer")
    if r["crush"] > (0.2 if dark and not natural else CRUSH_MAX):
        bad.append(f"{100 * r['crush']:.0f}% more of the frame crushed to black")
    if natural and r["sat"] > SAT_MAX_NATURAL:
        bad.append(f"saturation up {100 * (r['sat'] - 1):.0f}% on a 'natural' ask")
    return bad


def temper_step(edl, step, r, natural):
    """One tempering move on a copy of ``edl``: (edl, note) or (None, '')
    when this step has nothing left to change."""
    edl = json.loads(json.dumps(edl))
    fx = edl.setdefault("effects", {})
    gc = dict(fx.get("grade_custom") or {})
    if step == 0:
        changed = []
        for st in fx.get("stylize") or []:
            if (st or {}).get("kind") == "vignette" and \
                    float(st.get("intensity") or 0.5) > 0.12:
                changed.append(f"{float(st.get('intensity') or .5):.2f}")
                st["intensity"] = 0.12
        if changed:
            return edl, "vignette " + "/".join(changed) + " -> 0.12"
        return None, ""
    if step == 1:
        notes = []
        if float(gc.get("shadows") or 0) < 0:
            notes.append(f"shadows {float(gc['shadows']):+.2f} -> 0")
            gc["shadows"] = 0.0
        if gc.get("contrast") is not None and float(gc["contrast"]) > 1.05 \
                and fx.get("grade"):
            notes.append(f"custom contrast {float(gc['contrast']):.2f} -> "
                         "1.04 (the preset adds its own)")
            gc["contrast"] = 1.04
        if natural and r and r["sat"] > SAT_MAX_NATURAL:
            cur = float(gc.get("saturation") or 1.0)
            new = round(max(0.7, cur * (SAT_MAX_NATURAL - 0.08) / r["sat"]), 3)
            notes.append(f"saturation {cur:.2f} -> {new:.2f}")
            gc["saturation"] = new
        if notes:
            fx["grade_custom"] = gc
            return edl, ", ".join(notes)
        return None, ""
    if step == 3:
        # the darks themselves: a dark source under any contrast sits on
        # the black floor; lift the shadow curve once (the mids stay put)
        if r and r["crush"] > CRUSH_MAX and float(gc.get("shadows") or 0) < 0.15:
            old = float(gc.get("shadows") or 0)
            gc["shadows"] = 0.15
            fx["grade_custom"] = gc
            return edl, f"shadows {old:+.2f} -> +0.15 (the darks were crushed)"
        return None, ""
    # exposure lift, in steps
    cur = float(gc.get("exposure") or 0.0)
    if cur >= 0.36:
        return None, ""
    gc["exposure"] = round(min(0.4, cur + 0.08), 3)
    fx["grade_custom"] = gc
    return edl, f"exposure {cur:+.2f} -> {gc['exposure']:+.2f}"


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


def _measure(ctx, edl, picks, raws):
    import renderer
    out = []
    for (t, _s), raw in zip(picks, raws):
        chain = ",".join(renderer.cutaway_look_chain(edl, t, 0.04))
        out.append(frame_stats(raw, chain) if raw else None)
    return out


def after_write(ctx, before_sig, request_text=""):
    """'' or the LEGIBILITY GUARD line after a look write; writes the
    tempered EDL when the look failed. Never raises."""
    try:
        row = ctx.latest_edl()
        edl = row["json"]
        if signature(edl) == before_sig:
            return ""
        fx = edl.get("effects") or {}
        if not (fx.get("grade") or fx.get("grade_custom") or any(
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
        natural = wants_natural(request_text)
        dark = wants_dark(request_text)
        r0 = compare(src, _measure(ctx, edl, picks, raws))
        bad = verdict(r0, natural, dark)
        if not bad:
            return ""
        cur, r, notes = edl, r0, []
        for step in (0, 1, 3, 2, 2, 2, 2, 3, 2):
            nxt, note = temper_step(cur, step, r, natural)
            if nxt is None:
                continue
            cur, notes = nxt, notes + [note]
            r = compare(src, _measure(ctx, cur, picks, raws))
            if not verdict(r, natural, dark):
                break
        if cur is not edl and natural and wants_vibrant(request_text) and r \
                and not verdict(r, natural, dark) and r["sat"] < 1.12:
            # "vibrant and rich ... avoid oversaturation" (2672): the
            # tempering (and the exposure lift) took the colour back to
            # the source's; give it back up to the natural ceiling
            gc = dict((cur.get("effects") or {}).get("grade_custom") or {})
            sat0 = float(gc.get("saturation") or 1.0)
            boost = json.loads(json.dumps(cur))
            boost["effects"].setdefault("grade_custom", {})
            boost["effects"]["grade_custom"]["saturation"] = round(
                min(1.6, sat0 * min(1.35, 1.2 / max(r["sat"], 0.5))), 3)
            rb = compare(src, _measure(ctx, boost, picks, raws))
            if rb and not verdict(rb, natural, dark):
                cur, r = boost, rb
                notes.append(f"saturation {sat0:.2f} -> "
                             f"{boost['effects']['grade_custom']['saturation']:.2f}"
                             " (vibrant, still natural)")
        if cur is edl:
            return ("\nLEGIBILITY GUARD: this look leaves the picture "
                    + "; ".join(bad) + ". Ease it (less contrast, no shadow "
                    "drop or vignette on dark footage) so the content reads.")
        res = ctx.write_edl(cur, "look tempered so the picture stays readable")
        if not str(res).startswith("EDL v"):
            return ""
        left = verdict(r, natural, dark)
        return (f"\nLEGIBILITY GUARD ({str(res).split(':')[0]}): the stacked look "
                f"left the picture {'; '.join(bad)} (measured on "
                f"{len(picks)} frames against the source). Tempered: "
                + "; ".join(notes) + ". Now the mean brightness is "
                f"{r['luma']:.2f}x the source's"
                + (" (still: " + "; ".join(left) + ")" if left else
                   ", the content reads") + ". Keep this level of "
                "darkness unless the user asks for darker, and in the reply "
                "say the look keeps the picture readable: never claim deeper "
                "or darker shadows than this.")
    except Exception as exc:  # noqa: BLE001
        print(f"[look_guard] skipped ({str(exc)[:160]})", flush=True)
        return ""
