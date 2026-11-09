"""Caption tool-defect check: measured on pixels, after the asset row exists.

Owner, 9 Nov 2026 (project 5574, render v35): a caption's semi-transparent
plate was drawn ABOVE "dan polanya sama" instead of behind it. A render
that our tools drew wrong is a tool defect and becomes a ticket, whatever
the model asked for.

The captions ASS the render burned is drawn again by the same libass
(ffmpeg's subtitles filter, the bundled fonts, the output's own size) at
the caption frames the renderer knows, in isolated layers on a flat key
colour, and the pixels are measured:

  plate-offset   a caption's glyphs are not inside its backing plate
  plate-empty    a backing plate with no glyphs on it
  shadow-offset  a drop shadow that does not sit on its own glyphs
  text-cut       glyphs touching the frame edge (cut by it)
  type-overlap   two different caption items' glyphs on the same pixels

No model and no word list: event classes come from the ASS's own machine
syntax (a drawing `\\p1` event is a plate) and every verdict is a pixel
measurement. A check that cannot run returns no defects (a render never
fails over its own review); a defect is evidence, never a render gate.
"""
import os
import re
import subprocess

import numpy as np

KEY = (250, 0, 250)            # flat key colour the layers are drawn on
DRAWN = 40                     # max channel distance from KEY = drawn pixel
MAX_TIMES = 40
MAX_OVERLAP_TIMES = 6
PLATE_MIN_COVER = 0.97         # share of a plated line's glyphs on its plate
SHADOW_MAX_STRAY = 0.06        # share of shadow pixels off their glyphs
OVERLAP_MIN = 0.004            # shared pixels / smaller item's glyph pixels

_DRAWING = re.compile(r"\\p[1-9]")
_TIME = re.compile(r"(\d+):(\d+):(\d+(?:\.\d+)?)")
# Machine syntax of the ASS we wrote (override tags), never customer text.
_BORD = re.compile(r"\\(bord|xbord|ybord|shad|xshad|yshad|be|blur)-?[\d.]+")
_FADE = re.compile(r"\\fade?\([^)]*\)")
_SHAD = re.compile(r"\\(shad|xshad|yshad)-?[\d.]+")
_FILL_ALPHA = re.compile(r"\\(1a|alpha)&H[0-9A-Fa-f]{2}&")


def _secs(stamp):
    m = _TIME.match(stamp.strip())
    if not m:
        return 0.0
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def parse(path):
    """(header lines, style fields by name, events). Each event: dict with
    layer, start, end, style, text, line (prefix up to the text field),
    drawing (a vector plate)."""
    header, styles, events = [], {}, []
    fmt_style = None
    with open(path, encoding="utf-8-sig") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if line.startswith("Dialogue:"):
                parts = line[len("Dialogue:"):].split(",", 9)
                if len(parts) < 10:
                    continue
                events.append({
                    "layer": int(parts[0].strip() or 0),
                    "start": _secs(parts[1]), "end": _secs(parts[2]),
                    "style": parts[3].strip(), "prefix": parts[:9],
                    "text": parts[9],
                    "drawing": bool(_DRAWING.search(parts[9]))})
                continue
            if line.startswith("Format:") and fmt_style is None \
                    and "Fontname" in line:
                fmt_style = [x.strip() for x in line[7:].split(",")]
            if line.startswith("Style:") and fmt_style:
                vals = [x.strip() for x in line[6:].split(",")]
                styles[vals[0]] = dict(zip(fmt_style, vals))
            header.append(line)
    return header, styles, events


def _style_line(st, glyph_only, no_shadow=False):
    st = dict(st)
    if glyph_only:
        st["Outline"], st["Shadow"], st["BorderStyle"] = "0", "0", "1"
    elif no_shadow:
        st["Shadow"] = "0"
    return "Style: " + ",".join(st.values())


def _write(path, header, styles, events, glyph_only=False,
           opaque_plates=False, no_shadow=False):
    out = []
    for line in header:
        if line.startswith("Style:"):
            name = line[6:].split(",", 1)[0].strip()
            out.append(_style_line(styles[name], glyph_only, no_shadow)
                       if name in styles else line)
        else:
            out.append(line)
    for ev in events:
        text = _FADE.sub("", ev["text"])
        if glyph_only:
            text = _BORD.sub(lambda m: "\\" + m.group(1) + "0", text)
        elif no_shadow:
            text = _SHAD.sub(lambda m: "\\" + m.group(1) + "0", text)
        if opaque_plates and ev["drawing"]:
            text = _FILL_ALPHA.sub(lambda m: "\\" + m.group(1) + "&H00&",
                                   text)
        out.append("Dialogue:" + ",".join(ev["prefix"]) + "," + text)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    return path


def _ff_escape(path):
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def render(ass_path, W, H, times, fontsdir, ffmpeg="ffmpeg", timeout=120):
    """The ASS drawn by libass at each output time, on the key colour:
    uint8 array (len(times), H, W, 3), or None."""
    if not times:
        return None
    expr = "%.3f" % times[-1]
    for k in range(len(times) - 2, -1, -1):
        expr = "if(eq(N,%d),%.3f,%s)" % (k, times[k], expr)
    vf = (f"setpts='({expr})/TB',subtitles=filename='{_ff_escape(ass_path)}'"
          f":fontsdir='{_ff_escape(fontsdir)}'")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
           "-f", "lavfi", "-i",
           "color=c=0x%02X%02X%02X:s=%dx%d:r=1:d=%d" % (KEY + (W, H, len(times))),
           "-vf", vf, "-fps_mode", "passthrough", "-frames:v",
           str(len(times)), "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    n = W * H * 3
    if proc.returncode != 0 or len(proc.stdout) < n * len(times):
        return None
    return np.frombuffer(proc.stdout[:n * len(times)], np.uint8) \
        .reshape(len(times), H, W, 3)


def drawn(frame):
    """Pixels the layer drew (anything off the key colour)."""
    key = np.array(KEY, np.int16)
    return np.abs(frame.astype(np.int16) - key).max(axis=2) > DRAWN


def _bbox(mask):
    ys, xs = np.nonzero(mask)
    if not len(ys):
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def _dilate(mask, r):
    if r <= 0:
        return mask
    out = mask.copy()
    for _ in range(r):
        m = out
        out = m.copy()
        out[1:, :] |= m[:-1, :]
        out[:-1, :] |= m[1:, :]
        out[:, 1:] |= m[:, :-1]
        out[:, :-1] |= m[:, 1:]
    return out


def _lines(glyphs, gap):
    """Row bands of glyph pixels (one per caption line): [(y0, y1)]."""
    rows = np.nonzero(glyphs.any(axis=1))[0]
    bands = []
    for y in rows:
        if bands and y - bands[-1][1] <= gap:
            bands[-1][1] = int(y)
        else:
            bands.append([int(y), int(y)])
    return [tuple(b) for b in bands]


def review_times(events, duration=None, max_times=MAX_TIMES):
    """One output time per distinct caption state (the midpoint of every
    glyph event's span), bounded and spread evenly."""
    spans = sorted({(round(e["start"], 3), round(e["end"], 3))
                    for e in events if not e["drawing"] and e["end"] > e["start"]})
    times = []
    for a, b in spans:
        t = round((a + b) / 2, 3)
        if duration is not None and t >= duration:
            continue
        times.append(t)
    times = sorted(set(times))
    if len(times) > max_times:
        step = len(times) / float(max_times)
        times = [times[int(i * step)] for i in range(max_times)]
    return times


def _active(events, t):
    return [e for e in events if e["start"] <= t < e["end"]]


def check(ass_path, W, H, workdir, *, fontsdir, ffmpeg="ffmpeg",
          duration=None, layer="captions", times=None):
    """[defect] for the captions ASS burned at W x H. Each defect:
    {layer, defect, cause_key, t, detail, box}."""
    if not ass_path or not os.path.exists(ass_path):
        return []
    header, styles, events = parse(ass_path)
    glyph_ev = [e for e in events if not e["drawing"]]
    plate_ev = [e for e in events if e["drawing"]]
    if not glyph_ev:
        return []
    times = times if times is not None else review_times(glyph_ev, duration)
    times = [t for t in times if _active(glyph_ev, t)]
    if not times:
        return []
    os.makedirs(workdir, exist_ok=True)
    g_path = _write(os.path.join(workdir, "qc_glyph.ass"), header, styles,
                    glyph_ev, glyph_only=True)
    t_path = _write(os.path.join(workdir, "qc_type.ass"), header, styles,
                    glyph_ev)
    glyph = render(g_path, W, H, times, fontsdir, ffmpeg)
    typed = render(t_path, W, H, times, fontsdir, ffmpeg)
    if glyph is None or typed is None:
        return []
    plates = None
    if plate_ev:
        p_path = _write(os.path.join(workdir, "qc_plate.ass"), header,
                        styles, plate_ev, opaque_plates=True)
        plates = render(p_path, W, H, times, fontsdir, ffmpeg)
    shadow_styles = {name for name, st in styles.items()
                     if _num(st.get("Shadow")) > 0.05}
    # The shadow is measured against the OUTLINED glyphs (outline kept,
    # shadow removed): typed minus bare glyphs is outline + shadow, and a
    # style with a 3.9 px outline read as a stray shadow (tickets #976,
    # projects 5590 / 5591, 9 Nov 2026) though libass drew it correctly.
    outlined = None
    if any(e["style"] in shadow_styles for e in glyph_ev):
        o_path = _write(os.path.join(workdir, "qc_outlined.ass"), header,
                        styles, glyph_ev, no_shadow=True)
        outlined = render(o_path, W, H, times, fontsdir, ffmpeg)
    defects, seen = [], set()

    def add(kind, t, detail, box):
        if kind in seen:
            return
        seen.add(kind)
        defects.append({"layer": layer, "defect": kind,
                        "cause_key": f"tool:{layer}:{kind}", "t": round(t, 2),
                        "detail": detail, "box": box})

    overlap_times = []
    for k, t in enumerate(times):
        g = drawn(glyph[k])
        if g.sum() < 12:
            continue
        line_h = max(4, int(0.02 * H))
        # text-cut: glyph pixels on the outermost row or column
        border = (int(g[0, :].sum()) + int(g[-1, :].sum())
                  + int(g[:, 0].sum()) + int(g[:, -1].sum()))
        if border >= 2:
            add("text-cut", t, f"{border} glyph pixels on the frame edge "
                f"at {W}x{H}", _bbox(g))
        active = _active(events, t)
        # plates: every glyph line near a plate must sit on it
        if plates is not None and any(e["drawing"] for e in active):
            pm = drawn(plates[k])
            pbox = _bbox(pm)
            if pbox is not None:
                gap = max(2, line_h // 2)
                for y0, y1 in _lines(g, gap):
                    band = np.zeros_like(g)
                    band[y0:y1 + 1] = g[y0:y1 + 1]
                    n = int(band.sum())
                    if n < 12:
                        continue
                    near = (y1 >= pbox[1] - (y1 - y0 + 1)
                            and y0 <= pbox[3] + (y1 - y0 + 1))
                    if not near:
                        continue
                    cover = float((band & pm).sum()) / n
                    if cover < PLATE_MIN_COVER:
                        add("plate-offset", t,
                            f"{cover:.0%} of a caption line's glyphs on its "
                            f"plate (text rows {y0}-{y1}, plate rows "
                            f"{pbox[1]}-{pbox[3]}, cols {pbox[0]}-{pbox[2]})",
                            [pbox[0], min(y0, pbox[1]), pbox[2],
                             max(y1, pbox[3])])
                if not (pm & _dilate(g, 2)).any():
                    add("plate-empty", t, "a caption plate with no glyphs on "
                        "it", list(pbox))
        # shadow: the drop shadow must sit on its own glyphs
        if outlined is not None and any(
                e["style"] in shadow_styles for e in active
                if not e["drawing"]):
            ty = drawn(typed[k])
            body = drawn(outlined[k]) | g
            shadow = ty & ~body
            n = int(shadow.sum())
            if n >= 24:
                # A drop shadow of s px is offset (s, s): 2s steps of the
                # 4-neighbour dilation, plus antialiasing.
                shad = max((_num(styles.get(e["style"], {}).get("Shadow"))
                            for e in active if not e["drawing"]),
                           default=0.0)
                reach = max(3, int(0.15 * line_h) + 2,
                            int(2 * shad + 0.999) + 2)
                stray = float((shadow & ~_dilate(body, reach)).sum()) / n
                if stray > SHADOW_MAX_STRAY:
                    add("shadow-offset", t, f"{stray:.0%} of the shadow "
                        f"lies off its glyphs", _bbox(shadow))
        spans = {(e["start"], e["end"]) for e in active if not e["drawing"]}
        if len(spans) >= 2 and len(overlap_times) < MAX_OVERLAP_TIMES:
            overlap_times.append(t)
    for t in overlap_times:
        _overlap(t, header, styles, glyph_ev, W, H, workdir, fontsdir,
                 ffmpeg, add)
    return defects


def _overlap(t, header, styles, glyph_ev, W, H, workdir, fontsdir, ffmpeg,
             add):
    """Two caption items (distinct time spans) on screen together: each is
    drawn alone; shared glyph pixels are overlapping type."""
    groups = {}
    for e in _active(glyph_ev, t):
        groups.setdefault((e["start"], e["end"]), []).append(e)
    masks = []
    for n, evs in enumerate(list(groups.values())[:4]):
        path = _write(os.path.join(workdir, f"qc_item{n}.ass"), header,
                      styles, evs, glyph_only=True)
        fr = render(path, W, H, [t], fontsdir, ffmpeg)
        if fr is not None:
            masks.append(drawn(fr[0]))
    for i in range(len(masks)):
        for j in range(i + 1, len(masks)):
            both = int((masks[i] & masks[j]).sum())
            small = max(1, min(int(masks[i].sum()), int(masks[j].sum())))
            if both >= 12 and both / small >= OVERLAP_MIN:
                add("type-overlap", t, f"two caption items share {both} glyph "
                    f"pixels", _bbox(masks[i] & masks[j]))


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0
