"""hook_title's type size without a browser (WAVE_A, 31 Oct 2026).

"Make the opening title a little bigger" rendered pixel-identical: the
template fits the widest line to the column, so a larger ``size`` only raised
a cap the type never reached, while the reply said "bigger". The template now
reads ``base_size`` (the size the lines were laid out at) and scales the type
by size / base_size, re-breaking into more lines when the width is full
(motion/templates/hook_title.html, ``choose``).

The agent image has no Chromium, so the write-time probe cannot read the
template's own measurement there (motion_engine.probe -> ``info``). This is
the same layout with estimated glyph widths, so the tool can tell the editor
what really changed: the type's size before and after, and its lines. It
mirrors the template's rules; widths are estimates (relative widths, which
decide the re-break and the ratio, are close; the absolute size is within
about 10%).
"""
import re

MAXL = 4
GAP_PX = 6.0
LINE_H = 1.02
# average advance per character at 1 px font size (uppercase / mixed case)
_ADV = {"Anton": (0.47, 0.42), "Bebas Neue": (0.42, 0.40),
        "Archivo Black": (0.78, 0.66), "Montserrat": (0.72, 0.60),
        "Poppins": (0.70, 0.60), "Syne": (0.70, 0.60),
        "Plus Jakarta Sans": (0.68, 0.58), "Inter Display": (0.68, 0.58)}
_NARROW = set("IJ1.,'!:;|il")
_WIDE = set("MW@%mw")


def _lines(text):
    raw = re.sub(r"\s*/\s*", "\n", str(text or ""))
    return [s.strip() for s in raw.split("\n") if s.strip()][:3]


def _phrase_w(phrase, params):
    """Estimated width of one line at a 100 px font."""
    font = str(params.get("font") or "Inter Display")
    upper = bool(params.get("uppercase"))
    adv_u, adv_l = _ADV.get(font, _ADV["Inter Display"])
    serif = str(params.get("treatment") or "serif") == "serif"
    total, words = 0.0, phrase.split()
    in_star = False
    for k, w in enumerate(words):
        stars = w.count("*")
        acc = in_star or stars > 0
        if stars % 2:
            in_star = not in_star
        t = w.replace("*", "")
        t = t.upper() if upper else t
        ww = 0.0
        for ch in t:
            a = adv_u if (upper or ch.isupper()) else adv_l
            ww += a * (0.55 if ch in _NARROW else 1.3 if ch in _WIDE else 1.0)
        if acc and serif:
            ww *= 1.2 * 0.78        # Instrument Serif italic, set 1.2em
        total += ww * 100.0
        if k < len(words) - 1:
            total += 22.0           # .mg-w margin-right .22em
    return max(total, 1.0)


def _cap(n, ls):
    return min((210 if n == 1 else 170 if n == 2 else 140) * ls, 150 * ls) * ls


def _auto(lines, params, max_w, ls):
    def fit(cand):
        return max(40.0, min(_cap(len(cand), ls),
                             max_w * 100.0 / max(_phrase_w(c, params) for c in cand)))
    if len(lines) != 1:
        return lines, fit(lines)
    ws = lines[0].split()
    best = (lines, fit(lines), fit(lines))
    if len(ws) < 3:
        return best[0], best[1]
    for a in range(1, len(ws)):
        two = [" ".join(ws[:a]), " ".join(ws[a:])]
        f2 = fit(two)
        if f2 / 1.25 > best[2]:
            best = (two, f2, f2 / 1.25)
        if len(ws) >= 5:
            for b in range(a + 1, len(ws)):
                three = [" ".join(ws[:a]), " ".join(ws[a:b]), " ".join(ws[b:])]
                f3 = fit(three)
                if f3 / 1.5 > best[2]:
                    best = (three, f3, f3 / 1.5)
    return best[0], best[1]


def _tokens(lines):
    out = []
    for li, line in enumerate(lines):
        ws = line.split()
        in_star = False
        for k, w in enumerate(ws):
            if w.count("*") % 2:
                in_star = not in_star
            out.append((w, k == len(ws) - 1 and li < len(lines) - 1,
                        k < len(ws) - 1 and not in_star))
    return out


def _splits(toks, n):
    import itertools
    forced = [i for i, t in enumerate(toks[:-1]) if t[1]]
    free = [i for i, t in enumerate(toks[:-1]) if not t[1] and t[2]]
    k = n - 1 - len(forced)
    if k < 0 or k > len(free):
        return
    for extra in itertools.islice(itertools.combinations(free, k), 600):
        cuts = sorted(forced + list(extra))
        ls, a = [], 0
        for c in cuts:
            ls.append(" ".join(t[0] for t in toks[a:c + 1]))
            a = c + 1
        ls.append(" ".join(t[0] for t in toks[a:]))
        yield ls


def estimate(params, W, H):
    """{"type_px", "lines", "base_px", "base_lines"} the template would draw
    at these params on a W x H canvas (estimated widths)."""
    params = dict(params or {})
    try:
        size = float(params.get("size", 1.0))
        base = float(params.get("base_size") or 0.0)
        y = float(params.get("y", 0.42))
    except (TypeError, ValueError):
        return None
    lines = _lines(params.get("text"))
    if not lines:
        return None
    ls = min(base or size, 1.6)
    gain = size / base if base else 1.0
    max_w = W - 140.0
    ls0, f0 = _auto(lines, params, max_w, ls)
    out = {"type_px": round(f0, 1), "lines": len(ls0), "base_px": round(f0, 1),
           "base_lines": len(ls0)}
    if not base or abs(gain - 1.0) < 0.005:
        return out
    target = f0 * gain
    if gain < 1.0:
        out["type_px"] = round(target, 1)
        return out
    h0 = len(ls0) * LINE_H * f0 + GAP_PX * (len(ls0) - 1)
    portrait = H > W
    lo, hi = H * (0.08 if portrait else 0.06), H * (0.86 if portrait else 0.92)
    c0 = H * y
    t0, b0 = c0 - h0 / 2.0, c0 + h0 / 2.0
    try:
        bt, bb = float(params.get("band_top") or 0), float(params.get("band_bottom") or 0)
    except (TypeError, ValueError):
        bt = bb = 0.0
    if bb > bt and bb > 0:
        lo, hi = max(lo, min(t0, H * bt)), min(hi, max(b0, H * bb))
        room = max(h0, hi - lo)
    else:
        room = max(h0, (hi - t0) if y >= 0.55 else (b0 - lo) if y <= 0.45
                   else 2.0 * min(c0 - lo, hi - c0))

    def fit_h(n):
        return (room - GAP_PX * (n - 1)) / (n * LINE_H)

    def f_of(cand):
        return min(target, max_w * 100.0 / max(_phrase_w(c, params) for c in cand),
                   fit_h(len(cand)))
    best = (ls0, f_of(ls0))
    if best[1] < target * 0.995:
        toks = _tokens(lines)
        for n in range(len(ls0) + 1, min(MAXL, len(toks)) + 1):
            bn = max(((c, f_of(c)) for c in _splits(toks, n)),
                     key=lambda x: x[1], default=None)
            if bn and bn[1] > best[1] * 1.02:
                best = bn
            if best[1] >= target * 0.995:
                break
    if best[1] < f0 * 1.04:
        return out
    out.update(type_px=round(best[1], 1), lines=len(best[0]))
    return out
