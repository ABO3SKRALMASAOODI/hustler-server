"""The layout solver: maximise the payload, never touch what matters.

ROADMAP 2A objective: maximise payload size subject to
  * no collision with a face (behind the subject: never the eyes-to-mouth
    core), the corner mark, the safe area's edges or another graphic;
  * no collision between words, and correct reading order;
  * a ladder of at most 30:1 with secondary type >= 2.2% cap height;
  * one accent;
  * for type set behind the subject: every hero word >= 65% visible
    (glyph_visible, estimated on the subject prior here and measured on
    the program matte by the proof loop).

Search: each family in play x its candidate regions x its options; per
candidate the hero cap height C is scanned from the family's ceiling down
and refined by bisection. Branch-and-bound: a candidate whose ceiling
cannot beat the best score so far is skipped. Everything is deterministic
for a given (intent, canvas, keepouts, seed).
"""

import math
import time

from . import freespace as FS
from . import layouts as L
from . import roles as R
from . import voices as V

# Hero cap height ceilings (fraction of the canvas height) per family.
CAP_MAX = {"magazine": 0.30, "stack": 0.16, "staircase": 0.12,
           "stack_up": 0.13, "justified": 0.09, "side_column": 0.07,
           "slots": 0.12, "flank": 0.10, "constellation": 0.11,
           "spread": 0.22, "tilt": 0.12, "quote_window": 0.20,
           "arc": 0.045}
CAP_MIN_FRAC = 0.022            # secondary type floor (x canvas height)
LADDER_MAX = 30.0
WORD_PAD = 0.010                # min gap between two words (x W)
VISIBLE_MIN = 0.65              # glyph_visible floor for hero words
WHISPER_VISIBLE = 0.92          # small words set behind must stay readable
SCAN = 14
BISECT = 7


def cap_ceiling(family, W, H):
    portrait = H / max(1.0, W) >= 1.6
    c = CAP_MAX.get(family, 0.12)
    if not portrait and family in ("magazine", "spread", "quote_window"):
        c *= 1.25
    return c * H


class Candidate:
    __slots__ = ("layout", "C", "score", "region", "report")

    def __init__(self, layout, C, score, region, report):
        self.layout, self.C, self.score = layout, C, score
        self.region, self.report = region, report


def check(lay, space, toks, depth, H):
    """None when ``lay`` passes every hard constraint, else the reason."""
    pad = WORD_PAD * space.W
    ps = lay.placed
    for p in ps:
        if not space.in_safe(p.ink):
            return "safe"
        hits = space.hard_hits(p.ink, depth=depth)
        if hits:
            return "+".join(sorted(set(hits)))
    # a quote window shows one word at a time in one place: its words
    # replace each other, they never share the frame
    pairs = [] if lay.family == "quote_window" else [
        (a, b) for a in range(len(ps)) for b in range(a + 1, len(ps))]
    for a, b in pairs:
        if True:
            # a clear gap scaled to the smaller word (never a fixed pixel
            # pad: a 13 px whisper and its neighbour sit 4 px apart)
            gap = 0.04 * min(ps[a].size, ps[b].size)
            if L._overlap(ps[a].ink, ps[b].ink, gap):
                return "collision"
    if depth == "behind":
        for p in ps:
            floor = VISIBLE_MIN if p.tok.rung == "hero" else WHISPER_VISIBLE
            if 1.0 - space.subject_share(p.ink) < floor:
                return "hidden"
    caps = [p.tok.shaped(p.size).cap for p in ps]
    if caps:
        hi, lo = max(caps), min(caps)
        if lo < CAP_MIN_FRAC * H * 0.999:
            return "too_small"
        if hi / max(1e-6, lo) > LADDER_MAX:
            return "ladder"
    return None


def score(lay, C, space, depth, family, voice, region):
    W, H = space.W, space.H
    s = 3.0 * C / H
    box = lay.bbox()
    s += 0.25 * min(1.0, FS.area(box) / max(1.0, FS.area(region)))
    clear = min(space.clearance(p.ink) for p in lay.placed)
    s += 0.6 * min(0.04, clear / W)
    pref = voice.get("layouts") or []
    if family in pref:
        s += 0.06 * (len(pref) - pref.index(family)) / max(1, len(pref))
    lines = 1 + max(p.line for p in lay.placed)
    if lines > 4:
        s -= 0.05 * (lines - 4)
    if depth == "behind":
        vis = [1.0 - space.subject_share(p.ink) for p in lay.placed
               if p.tok.rung == "hero"]
        if vis:
            mv = sum(vis) / len(vis)
            # some occlusion sells the depth; too little reads as flat
            s += 0.15 if 0.65 <= mv <= 0.92 else (0.0 if mv > 0.92 else
                                                   -0.3)
    return s


def _regions(family, space, depth, opts):
    if family in ("flank", "constellation", "arc"):
        return [space.safe]
    named = space.named(depth)
    regs = space.regions(depth, min_frac=0.05, top=6)
    out = []
    if family == "magazine":
        # full-width bands: above the face core and below it
        out = [r for r in regs if (r[2] - r[0]) >= 0.5 * (
            space.safe[2] - space.safe[0])]
    elif family == "side_column":
        out = [named[k] for k in ("left", "right") if k in named]
    elif family == "quote_window":
        out = [named[k] for k in ("below", "above") if k in named] + regs[:2]
    else:
        pref = opts.get("region")
        if pref and pref in named:
            out = [named[pref]]
        out += [named[k] for k in ("above", "below", "left", "right")
                if k in named and named[k] not in out]
        out += [r for r in regs if r not in out]
    seen, uniq = set(), []
    for r in out:
        k = tuple(round(v) for v in r)
        if k not in seen and r[2] - r[0] > 40 and r[3] - r[1] > 30:
            seen.add(k)
            uniq.append(r)
    return uniq[:5]


def _variants(family, voice, seed):
    """Option sets tried for a family (align, gap, fit), seeded order so
    equally good layouts alternate between items."""
    fit = voice.get("fit", "width")
    if family == "stack":
        vs = [{"align": a, "fit": f, "gap": g}
              for a in ("left", "center", "right")
              for f in ((fit, "none") if fit != "none" else ("none",))
              for g in ("tight", "normal")]
    elif family == "magazine":
        vs = [{"align": a, "fill": f} for f in (1.0, 0.72) for a in
              ("left", "center", "right")]
    elif family in ("staircase", "stack_up", "justified"):
        vs = [{"align": "left", "gap": "tight"},
              {"align": "left", "gap": "normal"}]
    elif family == "side_column":
        vs = [{"side": "auto"}]
    elif family == "slots":
        vs = [{"start": "left"}, {"start": "right"}]
    elif family == "tilt":
        vs = [{"angle": -7.0, "align": "left"}, {"angle": 6.0,
                                                 "align": "right"}]
    else:
        vs = [{}]
    if seed:
        k = seed % len(vs)
        vs = vs[k:] + vs[:k]
    return vs


def _best_C(build, lines, region, space, toks, depth, H, lo, hi):
    """The largest hero cap in [lo, hi] at which the family builds a layout
    that passes every constraint (scan down, then bisect)."""
    last_bad = None
    for k in range(SCAN):
        C = hi * (1.0 - k / SCAN) + lo * (k / SCAN)
        lay = build(lines, C, region, space)
        if lay and not check(lay, space, toks, depth, H):
            good = (C, lay)
            if last_bad is None:
                return good
            a, b = C, last_bad
            for _ in range(BISECT):
                m = (a + b) / 2
                lay2 = build(lines, m, region, space)
                if lay2 and not check(lay2, space, toks, depth, H):
                    a, good = m, (m, lay2)
                else:
                    b = m
            return good
        last_bad = C
    return None


def solve(words, *, W, H, voice_name="auto", family="auto", depth="front",
          **kw):
    """Solve one typeset phrase (see _solve). Type asked to sit behind the
    subject that finds no layout there (a close-up leaves no room the head
    does not hide) is solved in front instead, and says so."""
    res = _solve(words, W=W, H=H, voice_name=voice_name, family=family,
                 depth=depth, **kw)
    if res[0] is None and res[4].get("depth_asked") == "behind":
        res2 = _solve(words, W=W, H=H, voice_name=voice_name, family=family,
                      depth="front", **kw)
        if res2[0] is not None:
            res2[4]["depth_fallback"] = "behind -> front: no layout keeps " \
                "the words readable behind this subject"
            return res2
        res = res2 if res2[0] is not None else res
    if res[0] is None and voice_name != V.DEFAULT_VOICE:
        # the voice's faces cannot be set legibly in this free space at all
        # (a tight close-up leaves one band): the plainest voice, said so
        res3 = _solve(words, W=W, H=H, voice_name=V.DEFAULT_VOICE,
                      family="auto", depth="front", **kw)
        if res3[0] is not None:
            res3[4]["voice_fallback"] = (
                f"{voice_name} -> {V.DEFAULT_VOICE}: its faces do not fit "
                "this frame's free space at a legible size")
            if depth == "behind":
                res3[4]["depth_fallback"] = "behind -> front: no room " \
                    "behind this subject"
            return res3
    return res


def _solve(words, *, W, H, voice_name="auto", family="auto", depth="front",
          onsets=None, durations=None, faces=(), watermark=None,
          captions=None, graphics=(), emphasis=None, seed=0, region=None,
          prefer=None, budget_s=2.0, families=None):
    """Solve one typeset phrase.

    words: [(printed word, starred)] (roles.tokens). onsets/durations: per
    word, item seconds (None = unsaid). faces/watermark/graphics: frame
    fraction boxes; captions: (y0, y1) band to keep clear. prefer: a
    callback's geometry {family, region, C, opts} tried first.

    Returns (Layout, voice, toks, space, report)."""
    t0 = time.monotonic()
    v = V.voice(voice_name if voice_name in V.VOICES else V.DEFAULT_VOICE)
    roles = R.assign(words, durations, emphasis,
                     quote=(voice_name == "quote_window"),
                     hero_share=float(v.get("hero_share", 0.0)))
    toks = [L.Tok(i, w, roles[i], v,
                  (onsets or [None] * len(words))[i],
                  (durations or [None] * len(words))[i])
            for i, (w, _s) in enumerate(words)]
    for t in toks:
        t.floor = CAP_MIN_FRAC * H if t.rung != "hero" else 0.0
    space = FS.Space(W, H, faces=faces, watermark=watermark,
                     captions=captions, graphics=graphics)
    if depth == "auto":
        depth = v.get("depth", "front")
        if depth == "auto":
            depth = "front"
    if family not in L.FAMILIES:
        fams = list(families or v.get("layouts") or ["stack"])
        n = len(words)
        if n == 1 and "spread" not in fams:
            fams.append("spread")
        if n > 10:
            fams = [f for f in fams if f not in ("spread", "quote_window",
                                                 "arc")] + ["side_column",
                                                            "justified"]
        for extra in ("stack", "constellation"):
            if extra not in fams:
                fams.append(extra)
    else:
        fams = [family]
    if depth == "behind" and "magazine" not in fams and family == "auto":
        fams.insert(0, "magazine")
    report = {"tried": 0, "rejected": {}, "family": None,
              "depth_asked": depth}
    lines_for = {}

    def orders(fam_list):
        out = []
        for fam in fam_list:
            nw = len(words)
            bal = tuple(f"bal{k}" for k in (2, 3, 4) if k < nw)
            if fam in ("quote_window", "side_column", "constellation", "arc"):
                modes = ("kicker",)
            elif fam in ("magazine", "spread"):
                modes = ("pack",) + bal + ("kicker",)
            else:
                modes = ("kicker", "pack") + bal
            for mode in modes:
                for reg in _regions(fam, space, depth, {"region": region}):
                    for opts in _variants(fam, v, seed):
                        out.append((fam, reg, dict(opts, lines=mode), None))
        return out

    def search(order, best=None):
        for fam, reg, opts, fixed_C in order:
            if time.monotonic() - t0 > budget_s and best is not None:
                report["budget_hit"] = True
                break
            lmode = opts.get("lines", "kicker")
            if (fam, lmode) not in lines_for:
                lines_for[(fam, lmode)] = L.break_lines(toks, fam, mode=lmode)
            lines = lines_for[(fam, lmode)]
            builder = L.BUILDERS[fam]
            o = dict(opts, depth=depth)

            def build(ls, C, rg, sp, _b=builder, _o=o):
                try:
                    return _b(ls, C, rg, sp, _o)
                except (ZeroDivisionError, ValueError):
                    return None
            reg = reg or space.safe
            hi = cap_ceiling(fam, W, H)
            lo = min(1.6 * CAP_MIN_FRAC * H, hi)
            report["tried"] += 1
            if fixed_C:
                lay = build(lines, fixed_C, reg, space)
                why = check(lay, space, toks, depth, H) if lay else "no_fit"
                if lay and not why:
                    sc = score(lay, fixed_C, space, depth, fam, v, reg) + 1.0
                    return Candidate(lay, fixed_C, sc, reg,
                                     {"callback": True}), True
                report["rejected"]["callback"] = why
                continue
            if best is not None and 3.0 * hi / H + 0.9 < best.score:
                continue                     # bound: cannot win
            got = _best_C(build, lines, reg, space, toks, depth, H, lo, hi)
            if not got:
                report["rejected"][fam] = report["rejected"].get(fam, 0) + 1
                continue
            C, lay = got
            sc = score(lay, C, space, depth, fam, v, reg)
            if best is None or sc > best.score + 1e-9:
                best = Candidate(lay, C, sc, reg, {})
        return best, False

    best = None
    if prefer and prefer.get("family") in L.BUILDERS:
        reg = prefer.get("region")
        if reg and max(reg) <= 1.5:          # fractions of the canvas
            reg = (reg[0] * W, reg[1] * H, reg[2] * W, reg[3] * H)
        C = prefer.get("C")
        if C and C <= 1.0:
            C = C * H
        best, hit = search([(prefer["family"], reg, dict(
            prefer.get("opts") or {}), C)])
        if hit:
            report["callback"] = True
        else:
            best = None
    if best is None:
        best, _ = search(orders(fams))
    if best is None and family not in L.FAMILIES:
        # the voice's own families found nothing legible here (a tight
        # close-up): every other family before giving up
        rest = [f for f in ("stack", "side_column", "justified",
                            "staircase", "stack_up", "spread", "flank",
                            "slots", "constellation", "tilt")
                if f not in fams]
        best, _ = search(orders(rest))
        if best is not None:
            report["family_fallback"] = True
    report["ms"] = round((time.monotonic() - t0) * 1000.0, 1)
    if best is None:
        return None, v, toks, space, report
    report.update(family=best.layout.family, C=round(best.C, 2),
                  payload_cap_pct=round(100.0 * best.C / H, 2),
                  score=round(best.score, 4), depth=depth,
                  region=[round(x, 1) for x in best.region],
                  opts={k: v_ for k, v_ in best.layout.opts.items()
                        if isinstance(v_, (int, float, str))})
    # the geometry a callback reuses (canvas fractions: typeset.memory)
    report["hint"] = {"family": best.layout.family,
                      "region": [round(best.region[0] / W, 4),
                                 round(best.region[1] / H, 4),
                                 round(best.region[2] / W, 4),
                                 round(best.region[3] / H, 4)],
                      "C": round(best.C / H, 5),
                      "opts": {k: v_ for k, v_ in best.layout.opts.items()
                               if k in ("align", "fit", "gap", "fill",
                                        "lines", "start", "angle", "side",
                                        "valign")}}
    return best.layout, v, toks, space, report
