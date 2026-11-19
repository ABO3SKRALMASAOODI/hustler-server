"""Workflow 1's films: a podcast moment as a film of what is said, on this week's engines, in one of four styles.

The speaker's take is the spine and the sound. Over the sentences that name real people, companies, places and
events the picture goes to real footage and photographs of them (worker/footage, Commons), a new one about every
second, then back to him; the payoff is his face. Captions ink word by word at one seat from the opening word; the words that carry the story land large on the words that say them; a named thing is
labelled once. Sentences about the past are drawn in black and white (a finish by section, never film-wide, except
in the style whose whole idea it is). Then the engines: the judge's pace and moves (scene.pace), one look solved on
the film's own pictures (scene.looks), a soft first frame on one new picture in three, and the references' sounds on
what lands (score.sound). The film's music is laid after the render by the shell.

What differs between the styles is how a found picture is shown and the film's type and colour:

  footage-cut    the picture fills the phone, in colour; yellow accent, heavy grotesk display words
  archive-reel   the picture fills the phone in black and white with heavy grain, a projector flash into each run;
                 cream serif italic display words
  window         a photograph or document plays in a framed window over itself out of focus (three layers); cyan
                 accent, condensed capitals
  prints         photographs and documents land as tilted prints on the picture's own dark, out-of-focus ground;
                 orange accent, high-contrast serif

Moving footage fills the phone in every style (owner's rating, 16 Nov 2026: cutaways as small windows over a blurred
fill are the defect; the references cut real footage full bleed). A wide clip is cover-cropped round the subject the
look placed (its x) and its people. Footage is fetched and cut at the phone's full size in every style (FOOTAGE_FRAME;
19 Nov: the window and prints styles asked for their card size, so every 16:9 clip arrived 1080x608 and fell into a
window card), and a clip still too small to fill the phone within 3x is brought up first with a detail-keeping scaler
(fill_size), never shown as a window.

No model is read here. Which words are displayed, which sentences speak of the past and which years the story
speaks from are handed in by the caller (wf1_short.py: a model that read the sentences).
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))        # through the Claude skill link to the plugin itself
SKILLS = os.path.dirname(os.path.dirname(HERE))
for _p in (os.path.join(SKILLS, "_shell"), os.path.join(SKILLS, "valmera-stories")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import film as F  # noqa: E402

FPS, W, H, LEAD_F = F.FPS, F.W, F.H, F.LEAD_F
Clock, r4, smooth = F.Clock, F.r4, F.smooth

# The alien bank (worker/scene/alien), by the independent verdict of 14 Nov 2026 (week-1112/notes/S.md):
# diecut and answer are gimmicks, never used; lamp is not ready; rhyme and arc are for workflow films only, gated.
WALL = "YouTube is refusing this Mac as a bot"       # the words a short stops with when its footage could not be fetched for that reason
ALIEN_NEVER = ("diecut", "answer", "lamp")
ALIEN_GATED = ("arc", "rhyme")

STYLES = {
    "footage-cut": dict(
        name="Footage Cut", layout="bleed", footage=(1080, 1920), look="podcast_low", ink="#F4EFE6", accent="#F2C14E",
        dsp=dict(font="InterDisplay-Black.ttf", px=128, track=-2), upper=True, grain=0.014, vignette=0.34,
        mono="past", flash="turn", aim_s=0.8, share=0.8, cap_y=0.615),
    "archive-reel": dict(
        name="Archive Reel", layout="bleed", footage=(1080, 1920), look="podcast_low", ink="#F2EDE4", accent="#E9D8A6",
        dsp=dict(font="DMSerifDisplay-Italic.ttf", px=140, track=0), upper=False, grain=0.055, vignette=0.44,
        mono="all", flash="runs", aim_s=1.0, share=0.78, cap_y=0.615),
    "window": dict(
        name="Window", layout="window", footage=(1080, 810), look="podcast_low", ink="#F4F6F8", accent="#7FE3FF",
        dsp=dict(font="Anton-Regular.ttf", px=150, track=1), upper=True, grain=0.012, vignette=0.36,
        mono="past", flash="turn", aim_s=1.0, share=0.74, cap_y=0.665),
    "prints": dict(
        name="Prints", layout="prints", footage=(1080, 1080), look="podcast_low", ink="#F6F1E7", accent="#FF7A3D",
        dsp=dict(font="PlayfairDisplay-Black.ttf", px=124, track=-1), upper=False, grain=0.03, vignette=0.38,
        mono="past", flash="turn", aim_s=0.95, share=0.76, cap_y=0.675),
}
ORDER = ["footage-cut", "window", "archive-reel", "prints"]
# The size every style asks the footage engine for: moving footage fills the phone in every style, so a clip keeps its
# source's own pixels up to the phone's long side (a 1280x720 source stays 1280x720 and fills the phone at 2.67x). A
# style's own `footage` size was its card's: asked of the engine it capped a 16:9 source at 1080x608 (3.16x, a window).
FOOTAGE_FRAME = (W, H)

WINDOW_BOX = (0.04, 0.205, 0.96, 0.593)        # four by three, the width of the phone, between the mark and the caption seat
PRINT_W = 0.62                                 # a square print, 670 px: small enough that the one under it shows
PRINT_ROT = (-2.6, 1.9, -1.2, 2.4)
PRINT_AT = ((0.36, 0.392), (0.64, 0.452), (0.385, 0.458), (0.615, 0.388))      # where prints land, in turn (frame fractions)
# The take moves like an editor (19 Nov rating: 17-22 s on one angle, 4-7% eases counted as cuts, the hook on a
# listener). Each stretch is framed on the person speaking it; the payoff is a punch-in to a close-up; a motivated
# move on the take is a new framing of its own (12% or more, pace.distinct; the judge zooms 5.3-12.4% inside a
# half-second shot, metromedia teardown section 8, and holds a framing far shorter than a take does), with a slow
# drift between moves.
TAKE_FACE_H = 0.30       # the speaker's face, a share of the frame's height, on his medium framing (references 0.29-0.38)
PUNCH_FACE_H = 0.40      # the payoff's punch-in: a close-up, a third tighter than the medium (a distinct framing, >12%)
TAKE_UP = 3.0            # the take may be pushed to the taste ceiling (TASTE 4.2: never past 3x)
PUNCH_STEP = 1.3         # the punch-in is at least this much tighter than the medium, inside the take's own pixels
TAKE_EASE = (0.12, 0.20)  # a motivated move on the take: 12-20% in or out
TAKE_DRIFT = 0.01        # between moves the take keeps a slow push
BREATH_F = 27            # the programme holds 0.9 s after the last word (the clip keeps 1 s): the end card joins after it
BED = dict(lift=0, gamma=1.0, gain=0.34, sat=0.6, contrast=1, temp=0, tint=0)
DIM = dict(lift=0, gamma=1.0, gain=0.46, sat=0.8, contrast=1, temp=0, tint=0)


def alien_asks(direction, style_id, frame_of):
    """The asks this film may hand the engine's alien bank. Never diecut, answer or lamp. `arc` only for a moment
    that speaks from two or more different years (each said aloud, so the year reads on its display word), and only
    in the style that keeps its pictures in colour. `rhyme` needs four to six moving round links, which a podcast
    moment does not supply: it is not asked for here. Returns (asks, why)."""
    st = STYLES[style_id]
    marks = []
    for m in (direction or {}).get("arc") or []:
        try:
            f, year = frame_of(int(m["seg"]), str(m.get("word") or m["year"])), int(m["year"])
        except (KeyError, TypeError, ValueError):
            continue
        if f is None or not (1000 <= year <= 2100):
            continue
        marks.append(dict(f=int(f), year=year, stakes=round(min(max(float(m.get("stakes") or 0), -1.0), 1.0), 2)))
    years = {m["year"] for m in marks}
    if st["layout"] != "bleed" or st["mono"] == "all":
        return [], "arc: this style has its own finish"
    if len(years) < 2:
        return [], f"arc: the moment speaks from {len(years)} year(s), the gate needs two"
    return [dict(dev="arc", marks=sorted(marks, key=lambda m: m["f"]), bars=0.085, develop=6)], "arc: gate passed"


def clean_asks(asks):
    """Whatever a caller hands in, only the gated devices pass."""
    return [a for a in asks or [] if isinstance(a, dict) and a.get("dev") in ALIEN_GATED and a.get("dev") not in ALIEN_NEVER]


def seat_luma(rec, kind, y0, y1):
    """The tone of a picture under the caption seat when it fills the phone (0 black, 1 white): the median of its
    middle band, on the clip's middle frame or the photograph, cut to the phone's shape. 0 when it cannot be read."""
    try:
        import numpy as np
        from PIL import Image
        with Image.open(rec.get("still") if kind == "clip" else rec["path"]) as im:
            g = np.asarray(im.convert("L").resize((270, max(int(270 * im.size[1] / im.size[0]), 2))), dtype=np.float32) / 255.0
        h, w = g.shape
        cw = min(w, max(int(h * W / H), 2))
        g = g[:, (w - cw) // 2:(w - cw) // 2 + cw]
        band = g[int(y0 * h):max(int(y1 * h), int(y0 * h) + 1), int(0.2 * cw):int(0.8 * cw)]
        return float(np.median(band)) if band.size else 0.0
    except Exception:  # noqa: BLE001
        return 0.0


def ink_by_ground(items, voices, bright):
    """Type that stands on a bright picture is set in dark ink for as long as that picture is up (white on a white
    page does not read): every caption and display word is cut at the bright spans and its part there takes dark
    ink, a light edge and the dark-ink caption voice. Returns the items."""
    if not bright:
        return items
    voices.setdefault("capd", dict(voices["cap"], shadow={"a": 0.0, "blur": 0.0}))
    out = []
    for it in items:
        if it.get("k") != "word" or it.get("role") not in ("cap", "big"):
            out.append(it)
            continue
        a, b = it["f"]
        cuts = sorted({a, b, *[f for s0, s1 in bright for f in (s0, s1) if a < f < b]})
        if len(cuts) == 2 and not any(s0 <= a < s1 for s0, s1 in bright):
            out.append(it)
            continue
        for k, (fa, fb) in enumerate(zip(cuts, cuts[1:])):
            n = dict(it, f=[fa, fb])
            if k:
                n["id"] = f"{it['id']}g{k}"
            for key in ("x", "y", "op", "blur", "s", "grow"):
                if isinstance(it.get(key), list):
                    n[key] = it[key][fa - a:fb - a] or it[key][-1:]
            if any(s0 <= fa < s1 for s0, s1 in bright):
                n["ink"] = "#0B0A09"
                n["stroke"] = dict(w=float((it.get("stroke") or {}).get("w", 1.6)), c="#F6F1E7")
                if it.get("role") == "cap":
                    n["v"] = "capd"
                elif it["v"] in voices:
                    voices.setdefault(it["v"] + "d", dict(voices[it["v"]], shadow={"a": 0.0, "blur": 0.0}))
                    n["v"] = it["v"] + "d"
            out.append(n)
    return out


def _lin(v):
    """sRGB channel (0..1) to linear light."""
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def rel_lum(hex_or_luma):
    """Relative luminance (WCAG) of a hex colour, or of a measured grey level 0..1 (seat_luma's median)."""
    if isinstance(hex_or_luma, (int, float)):
        return _lin(min(max(float(hex_or_luma), 0.0), 1.0))
    h = str(hex_or_luma).lstrip("#")
    r, g, b = (int(h[k:k + 2], 16) / 255.0 for k in (0, 2, 4))
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast(a, b):
    la, lb = rel_lum(a), rel_lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


DARK_INK = "#0B0A09"
INK_MIN = 3.0                 # the contrast a display word's ink keeps with the ground measured under it (large type)


def contrast_ink(ground, prefer, others):
    """The ink a display word takes on the ground measured under it: the designed ink while it holds INK_MIN against
    that ground, else whichever of the style's inks and dark ink reads best there. (ink, stroke colour)."""
    if contrast(prefer, ground) >= INK_MIN:
        ink = prefer
    else:
        ink = max([prefer, *others, DARK_INK], key=lambda c: contrast(c, ground))
    return ink, ("#F6F1E7" if rel_lum(ink) < 0.18 else DARK_INK)


CAP_PX = 58                   # the caption voice's size: a display word is set at least DISPLAY_MIN times it
DISPLAY_MIN = 1.6
DISPLAY_W = 2 * (F.RAIL_X - 0.5) - 0.004   # the widest a centred display line stands: clear of the platform's right rail
DISPLAY_MOST = 4              # display words a film sets at most: the first that fit, from the direction's ranked list
ROLE_SCALE = {"figure": 1.45, "payoff": 1.35, "contrast": 1.3, "turn": 1.25, "hook": 1.25, "name": 1.2}


def display_rows(ruler, voice, text, key=None, role="hook"):
    """How a display word is typeset by its role: [(text, px)], top line first. One line when the whole string stands
    at DISPLAY_MIN times the caption or more inside DISPLAY_W of the width (up to the role's size); else a two-size
    stack: the key word (the one the direction named, else the longest) large on its own line, the words said
    before it above and after it below at about half its size. Measured on the renderer's shaped glyphs."""
    ruler.voices["_fit"] = dict(voice)
    try:
        return _rows(ruler, voice, text, key, role)
    finally:
        ruler.voices.pop("_fit", None)                 # a measuring voice, never one of the scene's


def _rows(ruler, voice, text, key, role):
    floor = DISPLAY_MIN * CAP_PX
    top = float(voice["px"]) * ROLE_SCALE.get(role, 1.25)

    def fit(t, cap):
        return min(cap, ruler.fit_px("_fit", t, DISPLAY_W * W, cap))
    words = text.split()
    whole = fit(text, top)
    if whole >= floor or len(words) == 1:
        return [(text, max(whole, min(floor, fit(text, 10 ** 4))))]
    k = next((i for i, w in enumerate(words) if key and w.strip(",.!?;:").casefold() == str(key).strip(",.!?;:").casefold()), None)
    if k is None:
        k = max(range(len(words)), key=lambda i: len(words[i]))
    big = fit(words[k], top)
    small = max(CAP_PX * 1.05, 0.5 * big)
    rows = []
    if k:
        before = " ".join(words[:k])
        rows.append((before, fit(before, small)))
    rows.append((words[k], big))
    if k + 1 < len(words):
        after = " ".join(words[k + 1:])
        rows.append((after, fit(after, small)))
    return rows


def frame_stats(path, side=160):
    """(L50, chroma, red-blue, green-magenta) of a picture's mid-tones, gamma-encoded 0..1; None when unreadable."""
    try:
        import numpy as np
        from PIL import Image
        with Image.open(path) as im:
            a = np.asarray(im.convert("RGB").resize((side, max(int(side * im.size[1] / im.size[0]), 2))), dtype=np.float32) / 255.0
    except Exception:  # noqa: BLE001
        return None
    px = a.reshape(-1, 3)
    y = px @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    mid = px[(y > 0.12) & (y < 0.9)]
    if len(mid) < 50:
        mid = px
    return (float(np.median(y)), float(np.mean(mid.max(axis=1) - mid.min(axis=1))),
            float(np.mean(mid[:, 0] - mid[:, 2])), float(np.mean(mid[:, 1] - 0.5 * (mid[:, 0] + mid[:, 2]))))


def clip_balance(take_still, path, amount=0.6):
    """The grade that brings a cut-in picture part of the way (`amount`) to the take it cuts against, measured on the
    two pictures (exposure by the median, saturation by the mid-tones' chroma, white balance by their red-blue and
    green-magenta means): the engine's grade (gain multiplies, temp adds to red and takes from blue, tint adds green).
    None when either cannot be read."""
    a, b = frame_stats(take_still) if take_still else None, frame_stats(path) if path else None
    if not a or not b:
        return None

    def clamp(v, lo, hi):
        return round(min(max(v, lo), hi), 4)
    # exposure only a little (a cutaway may be brighter than a dim room; the film's look solves exposure for all of it),
    # colour more: white balance and saturation are what make a cool clip jump against a warm take
    gain = clamp((max(a[0], 0.02) / max(b[0], 0.02)) ** (0.5 * amount), 0.85, 1.15)
    sat = clamp((max(a[1], 0.01) / max(b[1], 0.01)) ** amount, 0.75, 1.25)
    temp = clamp(amount * (a[2] - b[2]) / 2.0, -0.03, 0.03)
    tint = clamp(amount * (a[3] - b[3]), -0.03, 0.03)
    return dict(lift=0, gamma=1.0, gain=gain, sat=sat, contrast=1, temp=temp, tint=tint)


TAKE_PAST = dict(lift=0, gamma=1.0, gain=0.97, sat=0.32, contrast=1.08, temp=0, tint=0)     # the take while he tells of the past


def section_spans(segs, seg_f, f_stop, min_f=36):
    """The frames of runs of consecutive sentences in `segs`: [(f0, f1)], each at least min_f long."""
    out = []
    for i in sorted(segs):
        if not (0 <= i < len(seg_f)):
            continue
        a = max(seg_f[i] - 1, 0)
        b = (seg_f[i + 1] - 1) if i + 1 < len(seg_f) else f_stop
        if out and abs(out[-1][1] - a) <= 2:
            out[-1][1] = b
        else:
            out.append([a, b])
    return [(a, b) for a, b in out if b - a >= min_f]


def caption_pools(l1, ruler, bright, a=0.42, pad=(0.05, 0.045, 0.04)):
    """The shade under the captions, one pool a caption page, centred on and sized to that page's own shaped words
    where they finally sit (after the seat moved them), never where the ink is dark (bright ground)."""
    from wf1_layout import caption_pages, _bounds
    caps = [i for i in l1["items"] if i["k"] == "word" and i.get("role") == "cap"]
    out = []
    for fa, fb, words in caption_pages(caps):
        bs = [b for _, b in _bounds(l1, words, ruler)]
        if not bs:
            continue
        x0, y0 = min(b[0] for b in bs), min(b[1] for b in bs)
        x1, y1 = max(b[2] for b in bs), max(b[3] for b in bs)
        rect = [r4(max(x0 - pad[0], 0.02)), r4(y0 - pad[1]), r4(min(x1 + pad[0], 0.98)), r4(y1 + pad[2])]
        cuts = sorted({fa, fb, *[f for s0, s1 in bright for f in (s0, s1) if fa < f < fb]})
        for ca, cb in zip(cuts, cuts[1:]):
            if cb > ca and not any(s0 <= ca < s1 for s0, s1 in bright):
                out.append(dict(k="flash", f=[ca, cb], mode="pool", c="#FFFFFF", a=a, rect=rect, z=-1))
    for n, it in enumerate(out):
        it["id"] = f"pool{n:03d}"
    return out


def inside(items, f_end):
    """Nothing outlives the film: an item that would run past the last frame ends on it (its per-frame values cut
    with it), and one that would start on or after it is left out. A clip cut close to its last word has no tail."""
    out = []
    for it in items:
        a, b = int(it["f"][0]), int(it["f"][1])
        if a >= f_end or b <= a:
            continue
        if b > f_end:
            it = dict(it, f=[a, f_end])
            for key, v in list(it.items()):
                per_frame = key in ("x", "y", "s", "rot", "op", "blur", "grow", "a", "th", "t", "dim") or (key in ("crop", "rect") and v and isinstance(v[0], list))
                if per_frame and isinstance(v, list) and len(v) == b - a:
                    it[key] = v[:f_end - a]
        out.append(it)
    return out


def fill_gaps(ins, footage, pics, script, seg_f, f_first, f_last, stress, gap_s=5.0, hold_s=1.2, rest_s=2.0, key_f=None):
    """A stretch of the take longer than gap_s with no picture over it is a held framing. It takes the pictures the
    film found and has not shown (clips first, those found for the nearest sentence first), one about every three
    seconds, each landing on the voice's stress, with the take between them. Nothing is shown twice: when the film
    has no picture to spare the take stays, eased by the pacing driver. Returns the cutaways, in order.
    key_f ({footage key: frame}): footage keyed by coverage window, not sentence; spares are then ordered by how near
    their window's frame is to the stretch."""
    shown = {x[4]["path"] for x in ins}
    if key_f is None:
        spare = [(i, "clip", c.get("subject"), c) for i, lst in footage.items() for c in lst if c["path"] not in shown]
    else:
        spare = [(key_f.get(i, 0), "clip", c.get("subject"), c) for i, lst in footage.items() for c in lst if c["path"] not in shown]
    for n, lst in pics.items():
        lines = [i for i, s in enumerate(script["segments"]) if n in (s.get("pictures") or [])] or [0]
        at = lines[0] if key_f is None else seg_f[min(lines[0], len(seg_f) - 1)] if seg_f else 0
        spare += [(at, "photo", n, r) for r in lst if r["path"] not in shown]
    if not spare:
        return ins
    hold, rest, out = int(hold_s * FPS), int(rest_s * FPS), list(ins)
    edges = [f_first] + [f for x in sorted(ins) for f in (x[0], x[1])] + [f_last]
    for a, b in zip(edges[0::2], edges[1::2]):
        if b - a <= gap_s * FPS:
            continue
        seg = max([i for i in range(len(seg_f)) if seg_f[i] <= (a + b) // 2] or [0]) if key_f is None else (a + b) // 2
        pos = a + rest
        while spare and b - pos >= hold + rest:
            spare.sort(key=lambda r: (abs(r[0] - seg), r[1] != "clip"))
            i, kind, name, rec = spare.pop(0)
            if rec["path"] in shown:                     # one picture pooled under two windows is still one picture
                continue
            near = [(st, -f) for f, st in stress if pos - 5 <= f <= pos + 8]
            f0 = -max(near)[1] if near else pos
            n = min(hold, int(float(rec["seconds"]) * FPS) - 1) if kind == "clip" else hold
            if n < 16:
                continue
            if kind == "clip":
                t0 = max((float(rec["seconds"]) - n / FPS) / 2, 0.0)
                rec = dict(rec, src=[round(t0, 3), round(t0 + n / FPS, 3)])
            out.append([f0, f0 + n, kind, name, rec])
            shown.add(rec["path"])
            pos = f0 + n + rest + hold // 2
    return sorted(out, key=lambda x: x[0])


FILL_GAP_S = 3.0          # no stretch of the take over about this long without a picture, when the film has one to spare


def fill_holes(ins, coverage, footage, pics, script, seg_f, f_first, f_last, stress, checker, rejected=(), composed_stills=(), rounds=3,
               originals=None, cut_dir=None, ffmpeg=None):
    """The final build's gap fill (19 Nov: m03 held the speaker 34 s once its windows' pictures were turned down, m10
    22 s, and fill_gaps had no caller). Any stretch of the take over FILL_GAP_S between f_first and f_last (the hook's
    first second and the payoff are his face) takes the approved pictures the film found and has not shown (fill_gaps:
    clips first, those of the nearest window first, about one every 2.6 s on the voice's stress). Each filler is
    checked on its own words by the same placement check as every picture; one turned down gives its place to the next
    spare. When the found pictures run out, fresh intervals of the kept originals (beside each interval the look
    verified, never one already shown: wf1_coverage.fresh_interval) are the spares. Returns (ins, report)."""
    from wf1_coverage import shared_candidates, same_picture, fresh_interval
    windows = coverage.get("windows") or []
    span = [(round(w["t0"] * FPS) + LEAD_F, round(w["t1"] * FPS) + LEAD_F) for w in windows]
    key_f = {i: (a + b) // 2 for i, (a, b) in enumerate(span)}
    skip = set(rejected)
    pool = shared_candidates(coverage, footage)
    report = dict(proposed=0, accepted=0, rejected=0, fresh=0, spend=[], placements=[])
    hold, rest = int(1.2 * FPS), int(0.8 * FPS)
    taken = {}
    for clips in list(pool.values()) + [[x[4] for x in ins]]:
        for c in clips:
            url = (c.get("source") or {}).get("url")
            if url and c.get("time_range") and len(c["time_range"]) == 2:
                taken.setdefault(url, []).append(tuple(c["time_range"]))
    fresh = {}
    for _ in range(rounds):
        placed = [x[4] for x in ins]
        spare_footage = {i: [c for c in lst + fresh.get(i, []) if c["path"] not in skip and not any(same_picture(c, p) for p in placed)]
                         for i, lst in pool.items()}
        edges = [f_first] + [f for x in sorted(ins) for f in (x[0], x[1])] + [f_last]
        stride = hold + rest + hold // 2                     # fill_gaps' pace: a picture, then the take, about every 2.6 s
        slots = sum((b - a - 2 * rest - hold) // stride + 1 for a, b in zip(edges[0::2], edges[1::2])
                    if b - a > FILL_GAP_S * FPS and b - a - rest >= hold + rest)
        short = slots - sum(map(len, spare_footage.values())) - sum(len(v) for v in pics.values())
        if short > 0 and originals and cut_dir and ffmpeg:
            # the same verified sources, intervals not yet shown: round-robin over the kept originals
            sources = {}
            for i, lst in pool.items():
                for c in lst:
                    url = (c.get("source") or {}).get("url")
                    if url in originals and c.get("time_range") and c["path"] not in skip:
                        sources.setdefault(url, (i, c))
            progress = True
            while short > 0 and progress:
                progress = False
                for i, c in list(sources.values()):
                    got = fresh_interval([("clip", c.get("subject"), c)], taken, originals, cut_dir, 1.2 + 2 / FPS, ffmpeg, skip=skip)
                    if got:
                        rec = dict(got[0][2], subject=c.get("subject"))
                        fresh.setdefault(i, []).append(rec)
                        spare_footage.setdefault(i, []).append(rec)
                        report["fresh"] += 1
                        short -= 1
                        progress = True
                        if short <= 0:
                            break
        spare_pics = {n: [r for r in lst if r["path"] not in skip and not any(same_picture(r, p) for p in placed)]
                      for n, lst in pics.items()}
        have = {(x[0], x[1], x[4]["path"]) for x in ins}
        out = fill_gaps([list(x) for x in ins], spare_footage, spare_pics, script, seg_f, f_first, f_last, stress,
                        gap_s=FILL_GAP_S, hold_s=1.2, rest_s=0.8, key_f=key_f)
        new = [x for x in out if (x[0], x[1], x[4]["path"]) not in have]
        if not new:
            break
        for x in new:
            mid = (x[0] + x[1]) // 2
            inside = next((i for i, (a, b) in enumerate(span) if a <= mid < b), None)
            near = inside if inside is not None else min(key_f, key=lambda i: abs(key_f[i] - mid)) if key_f else None
            x[4] = dict(x[4], window=inside, gap_fill=True,
                        illustration=windows[near]["must_show"] if near is not None else x[4].get("illustration"))
        accepted, rep = checker(new)
        for row in accepted:
            if row[4]["path"] in composed_stills:
                row[4]["verified_motion"] = False
        report["proposed"] += len(new)
        report["accepted"] += len(accepted)
        report["rejected"] += len(new) - len(accepted)
        report["spend"] += rep.get("spend") or []
        report["placements"] += rep.get("placements") or []
        ok = {id(r) for r in accepted}
        skip.update(x[4]["path"] for x in new if id(x) not in ok)
        ins = sorted(list(ins) + list(accepted), key=lambda x: x[0])
        if len(accepted) == len(new):
            break
    return ins, report


def clip_cover_up(rec):
    """The upscale a clip needs to fill the phone (cover): its height or its width onto the frame's, whichever binds."""
    pw, ph = float(rec.get("w") or W), float(rec.get("h") or H)
    return max(W / max(pw, 1.0), H / max(ph, 1.0))


def fill_size(rec, work, ffmpeg):
    """A clip too small to fill the phone within F.MAX_UP (a 360p or 480p source) is brought up once to fill it at 1x
    with a detail-keeping scaler (lanczos and a light unsharp mask, into <work>/filled) instead of being shown as a
    window card over its own blur (round 1, 19 Nov: every clip of four films sat in that card). Returns the filled copy's
    record (its faces and x are fractions and stand), or the record as it is when it already fills within MAX_UP,
    cannot be read, or the scale fails: then the driver fills the phone past 3x for that clip (clip_meta)."""
    import hashlib
    import math
    import subprocess
    up = clip_cover_up(rec)
    path = str(rec.get("path") or "")
    if up <= F.MAX_UP or not work or not ffmpeg or not rec.get("w") or not rec.get("h") or not os.path.isfile(path):
        return rec
    w2 = int(math.ceil(float(rec["w"]) * up / 2) * 2)
    h2 = int(math.ceil(float(rec["h"]) * up / 2) * 2)
    out_dir = os.path.join(work, "filled")
    dst = os.path.join(out_dir, hashlib.sha256(json.dumps([os.path.realpath(path), w2, h2]).encode()).hexdigest()[:16] + ".mp4")
    if not os.path.exists(dst):
        os.makedirs(out_dir, exist_ok=True)
        r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-i", path, "-vf",
                            f"scale={w2}:{h2}:flags=lanczos,unsharp=5:5:0.6:5:5:0.0,setsar=1,format=yuv420p", "-an",
                            "-c:v", "libx264", "-crf", "16", "-preset", "fast", "-movflags", "+faststart", "-y",
                            dst + ".part.mp4"], capture_output=True, text=True, timeout=600)
        if r.returncode != 0 or not os.path.exists(dst + ".part.mp4"):
            return rec
        os.replace(dst + ".part.mp4", dst)
    return dict(rec, path=dst, w=w2, h=h2, upscaled_from=dict(path=path, w=int(rec["w"]), h=int(rec["h"]), up=round(up, 3)))


def clip_meta(rec):
    """What the pacing driver needs to frame a clip full bleed: its size, its faces, where the look placed its subject
    (x, the vertical crop centred there) and how far it may be pushed in. A clip already at the frame's shape keeps
    the earlier limits (no push into a face at the references' size); a wide clip may come in 12% past its cover. A
    clip the composed review found cut through a face or missing its subject (rec['reframe']) is framed again: on its
    biggest face, else on its subject, never pushed past its cover."""
    fm = F.face_meta(rec)
    up = clip_cover_up(rec)
    big_face = any(f[3] >= 0.24 for f in fm)
    reframe = bool(rec.get("reframe"))
    m = dict(pw=int(rec.get("w") or W), ph=int(rec.get("h") or H), faces=fm,
             max_up=round(max(1.0, up) * (1.0 if big_face or reframe else 1.12), 3))
    if reframe and fm:
        big = max(fm, key=lambda f: f[2] * f[3])
        m["focus"] = (min(max(big[0] + big[2] / 2, 0.0), 1.0), min(max(big[4], 0.0), 1.0))      # its eyes
    elif rec.get("x") is not None and not fm:
        try:
            m["focus"] = (min(max(float(rec["x"]), 0.0), 1.0), 0.45)
        except (TypeError, ValueError):
            pass
    return m


def _runs(ins):
    """Cutaways that follow each other without the take between them, as lists of indexes."""
    out = []
    for k, x in enumerate(ins):
        if out and ins[out[-1][-1]][1] == x[0] and ins[out[-1][-1]][4].get("window") == x[4].get("window"):
            out[-1].append(k)
        else:
            out.append([k])
    return out


def follow_seat(flash, shifts):
    """A shade flash moved with the caption pages it lies under: [(first frame of a page, its shift)], in order.
    One shift moves it whole; pages at different heights cut it at their first frames."""
    def shift_at(f):
        return next((sh for a, sh in reversed(shifts) if a <= f), shifts[0][1])
    fa, fb = flash["f"]
    cuts = [fa] + [a for a, _ in shifts if fa < a < fb] + [fb]
    out = []
    for a, b in zip(cuts, cuts[1:]):
        sh = shift_at(a)
        if out and abs(out[-1][2] - sh) < 1e-9:
            out[-1][1] = b
        else:
            out.append([a, b, sh])
    pieces = []
    for n, (a, b, sh) in enumerate(out):
        piece = dict(flash, f=[a, b], rect=list(flash["rect"]))
        piece["rect"][1] += sh
        piece["rect"][3] += sh
        for k, v in flash.items():                       # a per-frame value keeps its own frames
            if k not in ("f", "rect") and isinstance(v, list) and len(v) == fb - fa:
                piece[k] = v[a - fa:b - fa]
        if n and flash.get("id"):
            piece["id"] = f'{flash["id"]}.{n}'
        pieces.append(piece)
    return pieces


def take_pieces(cuts, dur, punch=(), fps=FPS, lead=LEAD_F, until=None):
    """The take as framings, in film frames: [{f: [a, b], faces, who, cut, punch}]. Each of the show's camera shots
    is split where the person speaking changes, each part framed on the one who speaks it (wf1_speaker: a two-shot
    is cut between its two people as an editor cuts it), and at each `punch` frame (the payoff's cut-in to a
    close-up). The punch-in is always on the one speaking: when the payoff's word opens on a listener framing (the
    show's camera on the host while the guest talks) the close-up lands on the first speaker framing that opens inside
    the payoff (before `until`, the payoff's last frame), or there is none. A quiet stretch keeps the framing before
    it; a shot read before speaker spans existed is one piece on its faces."""
    segs = []
    for k, c in enumerate(cuts):
        spans = c.get("spans") or [dict(t0=c["t0"], t1=c["t1"], who="speaker", box=None)]
        for sp in spans:
            fa = 0 if (k == 0 and sp is spans[0]) else int(round(float(sp["t0"]) * fps)) + lead
            fb = int(round(float(sp["t1"]) * fps)) + lead
            if fb <= fa:
                continue
            box = sp.get("box")
            faces = [dict(box=box["box"], eyes=box["eyes"], score=1.0)] if box and sp.get("who") != "quiet" else None
            segs.append(dict(f=[fa, fb], faces=faces, who=sp.get("who") or "speaker", cut=k, punch=False))
    for i, sg in enumerate(segs):                     # a quiet stretch (or a reading with no face) keeps its neighbour's framing
        if sg["faces"] is None:
            same = [x for x in segs[:i][::-1] if x["cut"] == sg["cut"] and x["faces"]] or \
                   [x for x in segs[i + 1:] if x["cut"] == sg["cut"] and x["faces"]]
            sg["faces"] = same[0]["faces"] if same else list(cuts[sg["cut"]].get("faces") or [])
    out = []
    for sg in segs:
        if out and out[-1]["cut"] == sg["cut"] and out[-1]["faces"] == sg["faces"]:
            out[-1]["f"][1] = sg["f"][1]
            if out[-1]["who"] != "speaker":
                out[-1]["who"] = sg["who"]
        else:
            out.append(dict(sg, f=list(sg["f"])))
    for p in sorted(int(x) for x in punch or ()):
        i = next((i for i, pc in enumerate(out) if pc["f"][0] <= p < pc["f"][1]), None)
        if i is None:
            continue
        a, b = out[i]["f"]
        end = out[-1]["f"][1] if until is None else int(until)
        speaking = out[i]["who"] == "speaker"
        if speaking and p - a < 9:                    # the payoff opens a framing already: that framing is the punch-in
            out[i]["punch"] = True
        elif speaking and b - p >= 9:
            out[i:i + 1] = [dict(out[i], f=[a, p]), dict(out[i], f=[p, b], punch=True)]
        else:                                         # the next cut, or a listener's framing: the speaker's first framing in the payoff
            k = next((k for k in range(i + 1, len(out)) if out[k]["who"] == "speaker" and p <= out[k]["f"][0] < end), None)
            if k is not None:
                out[k]["punch"] = True
    return out


def medium_face_h(faces, ph, up=TAKE_UP):
    """The medium framing's face share for a take whose faces (face_meta rows) sit in a picture ph pixels tall: the
    references' medium (TAKE_FACE_H), widened when the take's pixels would leave the punch-in less than PUNCH_STEP
    tighter (a small face in a 1080p two-shot reaches the 3x ceiling before a close-up)."""
    fh = max((float(f[3]) for f in faces or []), default=0.0)
    if fh <= 0:
        return TAKE_FACE_H
    return round(min(TAKE_FACE_H, up / PUNCH_STEP * fh * float(ph) / H), 4)


def stress_moves(holds, stress, edge=FPS, most=3 * FPS):
    """Moves for the holds the director left unbroken (speaker_moves' unresolved holds, frame bounds): each on the
    voice's strongest stress peak inside the hold, a second clear of its ends, and again in either part still longer
    than `most` (the director's own three seconds). The voice's emphasis places every one, by signal, never a clock;
    a stretch with no stress peak keeps its slow drift."""
    out, todo = [], [tuple(h) for h in holds or []]
    while todo:
        a, b = todo.pop()
        if b - a <= most:
            continue
        inner = [(s_, f) for f, s_ in stress or [] if a + edge <= f <= b - edge]
        if inner:
            f = int(max(inner)[1])
            out.append(f)
            todo += [(a, f), (f, b)]
    return sorted(out)


def build(script, sup, style_id, direction=None):
    """(l1, beats) for one short. script: {"title", "segments": [{"say", "pictures": [names]}]}; sup: the pack
    (wf1_short.supply); direction: {"display": [{"seg", "text"}], "past_segs": [i], "turn_seg": i, "arc": [...]}."""
    st = STYLES[style_id]
    d = dict(aim_s=st["aim_s"], look=st["look"], family="podcast", share=st["share"], bed=True, title=False, tick_10s=1.5,
             band=dict(hold=(0.9, 2.6), never=3.0, ease_takes=True, motivated_frames=[], ease=0.85, shutter=0.5,
                       take_ease=TAKE_EASE, take_drift=TAKE_DRIFT))      # whips: the podcast band's own (into and out of picture runs)
    # tick_10s: a talking film (sound class T) carries the small tick layer on its display landings too (round1 thin-sound:
    # the references sound about 3 audible and 3-4 quiet events every 10 s; these films placed 1-4 sounds in 50 s)
    d.update(direction or {})
    coverage = sup.get("coverage")
    if not coverage:
        raise RuntimeError("podcast supply needs a word-addressed documentary coverage plan; refresh supply")
    d["band"] = dict(d["band"], motivated_frames=[round(coverage["words"][m["word"]]["start"]*FPS)+LEAD_F
                       for m in coverage.get("moves", []) if m.get("why") and 0 <= m.get("word", -1) < len(coverage["words"])])
    clock = Clock(sup["words"], script)
    voices = {
        "cap": {"font": "InterDisplay-Bold.ttf", "px": 58, "track": 0, "shadow": {"a": 0.85, "blur": 0.006}},
        "dsp": dict(st["dsp"], shadow={"a": 0.85, "blur": 0.010}),
        "lab": {"font": "InterDisplay-Bold.ttf", "px": 38, "track": 6},
    }
    P = F.Plan(voices, sup.get("sounds") or {})
    import config
    d["delivery_tail_s"] = float(config.OUTRO_DURATION_S)
    pal = dict(ink=st["ink"], accent=d.get("accent") or st["accent"], plate="#0A0A0A")
    drop = set(d.get("drop") or [])
    pics = {k: [r for r in v if r.get("title") not in drop and r.get('path') not in drop and os.path.basename(str(r.get("path"))) not in drop] for k, v in (sup.get("pictures") or {}).items()}
    pics = {k: v for k, v in pics.items() if v}
    footage = {int(ln["i"]): [c for c in ln.get("clips") or [] if os.path.basename(str(c.get("file") or c.get("path"))) not in drop
                              and c.get('path') not in drop and os.path.exists(str(c.get("path"))) and not c.get("dull")] for ln in sup.get("footage") or []}
    composed_stills=set(sup.get("composed_stills") or [])
    for candidates in footage.values():
        for candidate in candidates:
            if candidate["path"] in composed_stills:candidate["subject_motion"]=False
    # Clips fill the phone in every style: one too small to fill it within 3x is brought up first with a detail-keeping
    # scaler (fill_size), never put in a window card.
    footage = {i: [fill_size(c, sup.get("work"), sup.get("ffmpeg")) for c in lst] for i, lst in footage.items()}
    # The composed review's findings, each repaired where it was found (wf1_short._rebuild_rejected): a crop through a
    # face or a subject left outside the frame is framed again; a contradiction gives that one window's slot to its
    # next-ranked candidate and leaves the picture to every other window.
    # Findings are keyed on the candidate's own path (origin); a copy derived from it (text cleared) inherits the
    # candidate's reframe, so clip_meta frames it again.
    reframe = {(r.get("origin") or r.get("path")) if isinstance(r, dict) else r for r in d.get("reframe") or []}
    if reframe:
        footage = {i: [dict(c, reframe=True) if c["path"] in reframe else c for c in lst] for i, lst in footage.items()}
        pics = {k: [dict(r, reframe=True) if r["path"] in reframe else r for r in lst] for k, lst in pics.items()}
    # (window, path): that window's slot goes to its next candidate; (window, None): the window rests on the speaker.
    rejected_pairs = {(r.get("window"), r.get("origin") or r.get("path")) for r in d.get("reject") or []
                      if isinstance(r, dict) and (r.get("origin") or r.get("path") or (r.get("fault") == "speaker" and r.get("window") is not None))}
    take = sup["take"]
    keys, meta = {}, {}
    # the take's own shots (a show cut between cameras): each is framed on its own speaker. One file, one asset a shot
    tshots = [s_ for s_ in take.get("cuts") or [] if float(s_["t1"]) - float(s_["t0"]) > 0.2] or [dict(t0=0.0, t1=float(take["dur"]), faces=take.get("faces") or [])]
    for lst in pics.values():
        for rec in lst:
            if rec["path"] not in keys:
                keys[rec["path"]] = P.image(f"p{len(keys):02d}", rec)
    for lst in footage.values():
        for c in lst:
            if c["path"] not in keys:
                keys[c["path"]] = P.video(f"v{len(keys):02d}", c)
    if True:                                     # every style's footage fills the phone: a portrait face under the seat is left out
        seat = float(st["cap_y"]) - 0.045

        def under_seat(c):
            return int(c.get("w") or W) <= int(c.get("h") or H) and any(f["box"][3] > seat and f["box"][3] - f["box"][1] > 0.3 for f in c.get("faces") or [])
        for i, lst in footage.items():
            ok = [c for c in lst if not under_seat(c)]
            if ok:
                footage[i] = ok
    # the voice ends on its last word; the programme holds a breath after it (the clip keeps the source's second
    # after the word), so the end card joins after the payoff, never over it
    f_voice_end = Clock.f(max(w["t1"] for w in clock.words)) if clock.words else Clock.f(clock.dur)
    f_end = max(min(f_voice_end + BREATH_F, int((float(take["dur"]) + LEAD_F / FPS) * FPS)), f_voice_end + 1)
    stress = F.voice_stress(sup)
    nseg = len(script["segments"])
    cap_y = float(st["cap_y"])
    seg_f = [Clock.f(clock.segs[i][0]) for i in range(nseg)]

    def seg_at(f):
        return max([i for i in range(nseg) if seg_f[i] <= f + 1] or [0])
    # ---- the payoff is his face: the film's turn lands on the first word of the sentence the caller named (else the last)
    ts = d.get("turn_seg")
    ts = int(ts) if isinstance(ts, int) and 0 < ts < nseg else (nseg - 1 if nseg > 1 else None)
    turn_f = max(seg_f[ts] - 1, 0) if ts is not None else None
    if turn_f is not None and f_voice_end - turn_f > 9 * FPS and nseg > 1:      # a payoff longer than nine seconds would leave the close bare
        ts = nseg - 1
        turn_f = max(seg_f[ts] - 1, 0)
    # the story's own turn, before the payoff (the direction names it apart): the sound's turn hit and the music's drop
    sts = d.get("story_turn_seg")
    story_turn_f = max(seg_f[sts] - 1, 0) if isinstance(sts, int) and 0 < sts < nseg and (turn_f is None or seg_f[sts] - 1 < turn_f - FPS) else None
    # the payoff is his face, but never more than its last four seconds: a long close on one framing is a held picture
    # (the gap fill's last frame, fill_holes)
    f_last = max(turn_f - 6, f_voice_end - int(4.0 * FPS)) if turn_f is not None else f_voice_end - 45
    # the take as framings: each stretch on the one who speaks it, the payoff a punch-in. One file, one asset a framing
    pieces = take_pieces(tshots, float(take["dur"]), punch=[turn_f] if turn_f is not None and turn_f > 2 * FPS else [],
                         until=f_voice_end)
    for k, pc in enumerate(pieces):
        key = P.video("main" if k == 0 else f"own:{k}", dict(path=take["path"], w=take["w"], h=take["h"], fps=take.get("fps") or FPS, seconds=take["dur"]))
        fm = F.face_meta(dict(faces=pc["faces"] or take.get("faces") or []))
        if fm:                                           # a speaker's head, not a face-shaped thing on the shelf behind him
            tall = max(f[3] for f in fm)
            fm = [f for f in fm if f[3] >= 0.5 * tall]
        up = float(d.get("take_up", TAKE_UP))
        meta[key] = dict(pw=int(take["w"]), ph=int(take["h"]), faces=fm, max_up=up,
                         face_h=PUNCH_FACE_H if pc["punch"] else medium_face_h(fm, int(take["h"]), up))
        pc["key"] = key
    from placement_check import propose, check
    from wf1_coverage import checked_proposals, require_coverage, originals_of
    checker=lambda rows:check(rows,script,clock,sup['work'],sup['ffmpeg'])
    ins,placement_report=checked_proposals(coverage,footage,pics,f_end/FPS,checker,composed_stills,
        rejected=rejected_pairs,originals=originals_of(sup['work']),cut_dir=os.path.join(sup['work'],'fresh-intervals'),ffmpeg=sup['ffmpeg'])
    # what is still bare after the windows (a window whose pictures were all turned down, speech no window covered)
    # takes the approved pictures the film has to spare, each checked on its own words
    ins,fill_report=fill_holes(ins,coverage,footage,pics,script,seg_f,LEAD_F+FPS,f_last,stress,checker,
                               placement_report.get('rejected_paths') or [],composed_stills,
                               originals=originals_of(sup['work']),cut_dir=os.path.join(sup['work'],'fresh-intervals'),ffmpeg=sup['ffmpeg'])
    placement_report['gap_fill']={k:v for k,v in fill_report.items() if k!='spend'}
    placement_report['spend']=list(placement_report.get('spend') or [])+fill_report['spend']
    for row in ins:                                      # a clip the check cut clear of another editor's text is a new file
        if row[4]["path"] not in keys:
            keys[row[4]["path"]] = (P.video if row[2] == "clip" else P.image)(f"{'v' if row[2] == 'clip' else 'p'}{len(keys):02d}", row[4])
    coverage_report = require_coverage(ins, coverage, f_end/FPS, final=bool(d.get('coverage_final')))
    past = {int(i) for i in d.get("past_segs") or [] if isinstance(i, int)}
    cut_pts, drive_ids, look_pics, mono_spans, bright = [], set(), [], [], []
    if take.get("still"):
        look_pics.append(take["still"])
    tcuts = [pc["f"][0] for pc in pieces[1:]]          # the take's own cuts and its cuts between speakers, in film frames
    from wf1_coverage import speaker_moves, move_frame
    resolved_moves,move_spend=speaker_moves(coverage,ins,f_end,tcuts,sup['work'])
    signal_moves = stress_moves(coverage.get('unresolved_holds'), stress)
    d['band']['motivated_frames']=sorted({move_frame(coverage,m,FPS,LEAD_F) for m in resolved_moves} | set(signal_moves))
    placement_report['spend']+=move_spend
    coverage_report['speaker_moves']=resolved_moves
    coverage_report['stress_moves']=signal_moves
    coverage_report['take_pieces']=[dict(f=pc["f"], who=pc["who"], punch=pc["punch"], cut=pc["cut"]) for pc in pieces]
    coverage_report['speaker_on_camera']=take.get("on_camera")

    def take_shot(a, b):
        """The take between pictures: each framing on the one who speaks it, cut where the show cut and where the
        speaker changes; the moves inside a framing are the driver's (motivated words, the voice's stress)."""
        if b-a < 2:
            return
        # a section of the past is graded apart, so its edges are real cuts in the take; the payoff and the story turn
        # are not joins of their own (a flash there would land inside a continuous shot: it snaps to a real cut)
        joins = [f for sp in take_past for f in sp]
        edges = sorted({a, b, *[c for c in tcuts + joins if a+3 <= c <= b-3]})
        for fa, fb in zip(edges, edges[1:]):
            k = sum(1 for c in tcuts if c <= (fa+fb)/2)
            t0, t1 = max(fa-LEAD_F,0)/FPS, min(max(fb-LEAD_F,1)/FPS,float(take["dur"]))
            kw = dict(grade=dict(TAKE_PAST)) if any(s0 <= fa < s1 for s0, s1 in take_past) else {}
            it = P.shot([fa,fb], pieces[k]["key"], None, src=[round(t0,4),round(max(t1,t0+1/FPS),4)], **kw)
            it.pop("crop")
            drive_ids.add(it["id"])
            cut_pts.append(fa)

    # a finish for a section of the story, measured on what is said (spoken time), not on how many pictures it found:
    # a story that is all past keeps its colour (round1 default-design: 11 of 12 films had no section finish)
    spoken_s = [max(clock.segs[i][1] - clock.segs[i][0], 0.0) for i in range(nseg)]
    past_share = sum(spoken_s[i] for i in past if 0 <= i < nseg) / max(sum(spoken_s), 1e-6)
    by_section = 0.0 < past_share <= 0.6
    # the take itself takes the section's finish too (low saturation while he tells of the past), cut at the joins
    take_past = section_spans(past, seg_f, f_voice_end) if st["mono"] == "past" and by_section else []

    def is_mono(f0):
        return st["mono"] == "all" or (st["mono"] == "past" and by_section and seg_at(f0) in past)

    def note(f0, f1, kind, name, rec, mode):
        # origin: the candidate this picture is (a copy the check cut clear of another editor's text is placed under a
        # new path). The composed review's findings are keyed on it, so its repair reaches the candidate the next build
        # proposes (adversarial review, 20 Nov: findings keyed on the cleared path never matched and the short was lost).
        origin = (rec.get("text_cleared") or {}).get("from_path") or rec["path"]
        P.pictures.append(dict(f=[f0, f1], visible_f=[f0, rec.get("audit_until", f1)], kind=kind, name=name, path=rec["path"], origin=origin,
                               shows=rec.get("shows"), mode=mode,
                               window=rec.get("window"), clause=rec.get("clause"), clause_t=rec.get("clause_t"), fit=rec.get("fit"),
                               reframed=bool(rec.get("reframe")), attribution=rec.get("attribution"), name_super=rec.get("name_super"),
                               w=rec.get("w"), h=rec.get("h"), upscaled_from=rec.get("upscaled_from"), rung=rec.get("rung"),
                               how=rec.get("how"), source_size=rec.get("source_size"),
                               illustration=rec.get('illustration'), source_evidence=rec.get('source') or dict(url=rec.get('page'),title=rec.get('title'),channel=rec.get('credit')),
                               finish="black and white" if is_mono(f0) else None, motion_p90=rec.get('placed_motion_p90'),
                               verified_motion=rec.get('verified_motion') is True,
                               source=(rec.get("source") or {}).get("url") if kind == "clip" else rec.get("page"), time_range=rec.get("time_range")))
        if rec.get("still") or kind == "photo":
            look_pics.append(rec.get("still") or rec["path"])

    def settle_push(n, land=0.035, push=0.04):
        return [r4(1.0 + land - land * smooth(q / 7.0)) if q < 8 else r4(1.0 + push * (q - 8) / max(n - 9, 1)) for q in range(n)]

    def card_crop(rec, kind, asp):
        """The part of a picture a card of this shape shows, round its people."""
        pw_, ph_ = float(rec.get("w") or W), float(rec.get("h") or H)
        if kind == "clip":
            rw, rh = min(asp * ph_ / pw_, 1.0), min(pw_ / (asp * ph_), 1.0)
            fs_ = rec.get("faces") or []
            cx = 0.5
            if fs_:
                big = max(fs_, key=lambda q: (q["box"][2] - q["box"][0]) * (q["box"][3] - q["box"][1]))
                cx = (big["box"][0] + big["box"][2]) / 2
            x0 = min(max(cx - rw / 2, 0.0), 1.0 - rw)
            y0 = min(max(0.42 - rh / 2, 0.0), 1.0 - rh)
            return [r4(x0), r4(y0), r4(x0 + rw), r4(y0 + rh)]
        (fx, fy), _ = F.focus_of(rec)
        return F.cover_crop(pw_, ph_, asp, (fx, fy), (0.5, 0.42))

    def bed_of(f0, f1, key, kind, rec, mono):
        """The picture itself out of focus and dim, filling the phone: what a window or a print stands on."""
        pw_, ph_ = float(rec.get("w") or W), float(rec.get("h") or H)
        kw = dict(blur=26, grade=dict(DIM))
        if kind == "clip":
            kw["src"] = list(rec["src"])
        if mono:
            kw["look"] = "mono_silver"
        P.shot([f0, f1], key, F.cover_crop(pw_, ph_, W / H, (0.5, 0.45), (0.5, 0.40)), **kw)

    def window(f0, f1, kind, name, rec, key):
        n = f1 - f0
        mono = is_mono(f0)
        bed_of(f0, f1, key, kind, rec, mono)
        x0, y0, x1, y1 = WINDOW_BOX
        aspect = float(rec.get("w") or W) / float(rec.get("h") or H)
        height = min(y1-y0, (x1-x0)*W/H/aspect)
        width = height*H/W*aspect
        cx, cy = (x0+x1)/2, (y0+y1)/2
        x0, x1, y0, y1 = cx-width/2, cx+width/2, cy-height/2, cy+height/2
        asp = (x1 - x0) * W / ((y1 - y0) * H)
        kw = dict(z=1, s=1.0, crop=[0.0, 0.0, 1.0, 1.0], radius=0.010, op=[min(1.0, 0.5 + 0.25 * q) for q in range(n)],
                  shadow=dict(a=0.55, blur=0.03, dx=0, dy=0.012))
        if kind == "clip":
            kw["src"] = list(rec["src"])
        if mono:
            kw["look"] = "mono_silver"
        P.card([f0, f1], key, [x0, y0, x1, y1], **kw)
        note(f0, f1, kind, name, rec, "window")

    def bleed(f0, f1, kind, name, rec, key):
        if rec.get('provider')=='primary-document':
            return window(f0,f1,kind,name,rec,key)             # the approved document crop is already the editorial rectangle
        bal = None if is_mono(f0) else clip_balance(take.get("still"), rec.get("still") if kind == "clip" else rec.get("path"))
        gk = dict(grade=bal) if bal else {}               # matched to the take it cuts against, part of the way (round1 colour)
        if kind == "clip":                                   # moving footage fills the phone, whatever its size (fill_size)
            it = P.shot([f0, f1], key, None, src=list(rec["src"]), **gk)
            it.pop("crop")
            meta[key] = clip_meta(rec)
            if rec.get("verified_motion") is True and float(rec.get("placed_motion_p90") or 0) >= 1.0:
                it["moving"] = True                          # it arrives moving: a landing the sound design sounds
        else:
            (fx, fy), _ = F.focus_of(rec)
            it = P.shot([f0, f1], key, F.cover_crop(rec["w"], rec["h"], W / H, (fx, fy), (0.5, 0.40)), **gk)
            if key not in meta:
                m = dict(pw=int(rec["w"]), ph=int(rec["h"]), faces=[f for f in F.face_meta(rec) if f[3] * rec["h"] >= 60],
                         max_up=1.0 if rec.get("reframe") else F.MAX_UP)      # framed again: no push into the face it cut
                if not m["faces"]:
                    try:
                        import numpy as np
                        from PIL import Image
                        pace, _, _ = F._engines()
                        with Image.open(rec["path"]) as im_:
                            m["regions"] = pace.regions_of(np.asarray(im_.convert("L")), n=3, size=0.5)
                    except Exception:  # noqa: BLE001 - no regions: the picture's own thirds
                        pass
                meta[key] = m
        drive_ids.add(it["id"])
        if is_mono(f0):
            mono_spans.append((f0, f1))
        if seat_luma(rec, kind, cap_y - 0.06, cap_y + 0.02) > 0.6:      # a bright ground under the seat: dark ink while it is up
            bright.append((f0, f1))
        note(f0, f1, kind, name, rec, "bleed")

    def prints(run):
        """One readable print at a time; prior UI/document detail cannot occlude it."""
        for q, k in enumerate(run):
            f0, f1, kind, name, rec = ins[k]
            if kind == "clip":
                continue                                     # moving footage fills the phone (bleed)
            key = keys[rec["path"]]
            bed_of(f0, f1, key, kind, rec, is_mono(f0))
            aspect = float(rec.get("w") or W)/float(rec.get("h") or H)
            width = .91
            height = min(.38, width*W/H/aspect)
            width = height*H/W*aspect
            cx,cy=.5,.40
            n=f1-f0
            kw=dict(z=1,rot=(-.7 if q%2 else .7),crop=[0,0,1,1],radius=.006,
                    s=[r4(1+.025*(1-smooth(min(j/6,1)))) for j in range(n)],
                    shadow=dict(a=.62,blur=.03,dx=.004,dy=.014))
            if kind=="clip":kw['src']=list(rec['src'])
            if is_mono(f0):kw['look']='mono_silver'
            P.card([f0,f1],key,[cx-width/2,cy-height/2,cx+width/2,cy+height/2],**kw)
            note(f0,f1,kind,name,rec,"print")

    pos, labelled, labels, flashes = 0, set(), [], []
    runs = _runs(ins)
    for run in runs:
        r0, r1 = ins[run[0]][0], ins[run[-1]][1]
        take_shot(pos, r0)
        if st["layout"] == "prints":
            prints(run)
        for k in run:
            f0, f1, kind, name, rec = ins[k]
            if st["layout"] == "bleed" or kind == "clip":
                bleed(f0, f1, kind, name, rec, keys[rec["path"]])
            elif st["layout"] == "window":
                window(f0, f1, kind, name, rec, keys[rec["path"]])
            cut_pts.append(f0)
            P.events.append(f0)
            who = rec.get("name_super")
            if who and who not in labelled:
                # The person the picture shows, named as its source names them (a lower third the phone slice cuts
                # away, the source's title or channel: placement_check name_super), in the label type, once.
                labelled.add(who)
                b_lab, j = f1, k + 1
                while j < len(ins) and ins[j][0] == b_lab and ins[j][4].get("name_super") == who:
                    b_lab, j = ins[j][1], j + 1
                labels.append((f0 + 2, min(b_lab, f0 + int(2.4 * FPS)), str(who)))
            elif not who and d.get("labels", False) and name not in labelled:            # a named thing is labelled once, small, while its own pictures are up
                labelled.add(name)
                b_lab, j = f1, k + 1
                while j < len(ins) and ins[j][0] == b_lab and ins[j][3] == name:
                    b_lab, j = ins[j][1], j + 1
                labels.append((f0 + 2, min(b_lab, f0 + int(2.4 * FPS)), str(name)))
        if st["flash"] == "runs" and (not flashes or r0 - flashes[-1] >= 4 * FPS):      # the projector catches: into a run of archive
            flashes.append(r0)
        pos = r1
    take_shot(pos, f_end)
    if st["flash"] == "turn" and turn_f is not None:
        # the payoff's own cut, nearest its first word, among the film's real cuts (every picture's and every take
        # framing's first frame: the punch-in where it opens, else a picture cut a second either side of it); never a
        # frame inside a continuous shot
        cuts_near = [f for f in set(cut_pts) if abs(f - turn_f) <= FPS and 0 < f < f_end]
        if cuts_near:
            flashes.append(min(cuts_near, key=lambda f: (abs(f - turn_f), f)))
    real_cuts = sorted({f for f in cut_pts if 0 < f < f_end})

    def on_cut(f):
        """The real cut nearest f within a second (a flash never lands inside a continuous shot), else None."""
        near = [c for c in real_cuts if abs(c - f) <= FPS]
        return min(near, key=lambda c: (abs(c - f), c)) if near else None
    if st["flash"] == "turn" and story_turn_f is not None:
        sf = on_cut(story_turn_f)                            # where the story turns: a landing the sound's turn hit and the drop answer
        if sf is not None and all(abs(sf - f) >= 2 * FPS for f in flashes):
            flashes.append(sf)
    for a_, _b in take_past[:2]:                             # into a section of the past, on the take's join
        pf = on_cut(a_)
        if pf is not None and pf > FPS and all(abs(pf - f) >= 2 * FPS for f in flashes):
            flashes.append(pf)
    flashes = sorted(set(flashes))
    for f in flashes:
        if 0 < f < f_end - 3:
            P.flash([f, f + 2], mode="fill", c="#FFFFFF", a=[0.62, 0.22])
            P.events.append(f)
    # ---- Opening speech owns the caption seat; headlines are delivery metadata.
    _, _, bake = F._engines()
    shown = []

    def cased(t):
        return t.upper() if st["upper"] else t

    def land(a, b, text, vk, y, ink, delay=0, stroke="#0B0A09", on=None):
        n = b - a
        kk = min(int(bake.BOUNCE["n"]), n)
        ys = [r4(y + float(q)) for q in bake.bounce(kk, rest=0.0)] + [r4(y)] * (n - kk)
        bl = [round(float(q), 3) for q in bake.bounce_blur(kk)] + [0.0] * (n - kk)
        P.word([a + delay, b], text, vk, 0.5, ys[delay:] if delay else ys, ink, [min(1.0, (q + 1) / 2) for q in range(n - delay)],
               align="c", z=8, blur=bl[delay:] if delay else bl, role="big", stroke=dict(w=3.0, c=stroke), on=a if on is None else on)

    def ground_at(f, y0, y1):
        """The tone (0..1) of the picture up at frame f, under the band y0-y1 of the frame."""
        for f0, f1, kind, _n, rec in ins:
            if f0 <= f < f1:
                g = seat_luma(rec, kind, y0, y1)
                return g if st["layout"] == "bleed" or kind == "clip" else g * DIM["gain"]
        return seat_luma(dict(still=take.get("still")), "clip", y0, y1) if take.get("still") else 0.0

    # ---- the words that carry the story, large, each on the word that says it; never two inside two seconds. Typeset by
    # the job each does (display_rows): at least 1.6x the caption, on a seat of their own above the caption's, a long
    # phrase as a two-size stack round its key word, a contrast on a bar; ink chosen by the contrast measured on the
    # ground under the block (round1 default-design)
    # The direction ranks its proposals best first, with alternates: the film lands the first DISPLAY_MOST that fit its
    # timing (a word too early, too close to another or too short to hold gives its place to the next), so a film
    # never loses its display words to timing. A long phrase is stacked (display_rows), never skipped for its length.
    omit = []
    proposed = d.get("display") or []
    for row in proposed:
        if len(shown) >= DISPLAY_MOST:
            break
        try:
            seg, text = int(row["seg"]), " ".join(str(row["text"]).split())
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 <= seg < nseg) or not text or any(text.lower() in t.lower() or t.lower() in text.lower() for _, _, t in shown):
            continue
        hit = F.said_at(clock, seg, text)
        if hit is None:
            continue
        a = max(Clock.f(hit[0]) - 1, 1)
        if a < 2*FPS or any(abs(a - x) < 2.0 * FPS for x, _, _ in shown) or a >= f_voice_end - 12:
            continue
        b = int(min(a + 1.15 * FPS, f_end, Clock.f(clock.segs[seg][1]) + 10))
        # A display word cannot label the next documentary subject. If the
        # interval is too short for display, ordinary wordwise captions carry it.
        next_subject = [round(w['t0']*FPS)+LEAD_F for w in coverage['windows'] if round(w['t0']*FPS)+LEAD_F>a+1]
        if next_subject:
            b = min(b,min(next_subject))
        if b - a < 14:
            continue
        role = row.get("role") or "hook"
        rows = display_rows(P.R, P.voices["dsp"], cased(text), cased(str(row.get("key") or "")), role)
        base_y = cap_y - 0.062                           # the lowest baseline of the block, clear above the caption seat
        ys = [base_y]
        for k in range(len(rows) - 2, -1, -1):           # upward: the line below's cap height and a gap
            ys.insert(0, ys[0] - (0.74 * rows[k + 1][1] + 0.16 * min(rows[k][1], rows[k + 1][1])) / H)
        top = ys[0] - 0.74 * rows[0][1] / H
        prefer = pal["accent"] if any(ch.isdigit() for ch in text) or row.get("accent") else pal["ink"]
        ground = ground_at(a + 4, max(top, 0.0), base_y + 0.01)
        ink, edge = contrast_ink(ground, prefer, [pal["ink"], pal["accent"]])
        big = max(range(len(rows)), key=lambda k: rows[k][1])
        for k, (line, px) in enumerate(rows):
            P.voices[f"dsp{a}_{k}"] = dict(P.voices["dsp"], px=float(px))
        if role == "contrast":                           # the contrast on a bar: dark ink on the accent
            wd = P.R.width(f"dsp{a}_{big}", rows[big][0]) / W
            P.block([a, b], [0.5 - wd / 2 - 0.02, ys[big] - P.R.cap(f"dsp{a}_{big}") / H - 0.014, 0.5 + wd / 2 + 0.02, ys[big] + 0.016],
                    pal["accent"], z=7, op=0.96, radius=0.006, bar_on=a)     # part of the block: seat_display moves and shrinks it with its lines
        for k, (line, px) in enumerate(rows):
            vk = f"dsp{a}_{k}"
            line_ink, line_edge = (DARK_INK, pal["accent"]) if role == "contrast" and k == big else (ink, edge)
            land(a, b, line, vk, ys[k], line_ink, delay=0 if k == big else min(2, b - a - 1), stroke=line_edge, on=a)
        omit += F.said_span(clock, seg, text)
        P.events.append(a)
        shown.append((a, b, text))
    shown.sort()
    for a, b, text in labels:
        if b - a < 14:
            continue
        t = text.upper()
        rows = [t]
        words = t.split()
        if P.R.width("lab", t) / W > 0.66 and len(words) > 1:          # a long name goes on two lines, never smaller
            n = min(range(1, len(words)), key=lambda n: max(P.R.width("lab", " ".join(words[:n])), P.R.width("lab", " ".join(words[n:]))))
            rows = [" ".join(words[:n]), " ".join(words[n:])]
        wd = max(P.R.width("lab", r_) for r_ in rows) / W
        if wd > 0.66:
            continue
        y = 0.752
        y_last = y + 0.036 * (len(rows) - 1)
        P.block([a, b], [0.5 - wd / 2 - 0.022, y - 0.030, 0.5 + wd / 2 + 0.022, y_last + 0.013], pal["plate"], z=6, op=0.78, radius=0.008)
        for n, r_ in enumerate(rows):
            P.word([a, b], r_, "lab", 0.5, y + 0.036 * n, pal["ink"], 0.95, align="c", z=7, role="stat")
    lines = F.caption_build(P, clock, "cap", cap_y, f_end=min(f_voice_end + 8, f_end), hold=[(a - 1, b) for a, b, _ in shown], z=5,
                            omit=omit)
    P.items = inside(P.items, f_end)
    P.items = ink_by_ground(P.items, P.voices, bright)
    film = dict(grain=float(st["grain"]), vignette=float(st["vignette"]), over_type=False, grade=dict(lift=0, gamma=1, gain=1, sat=1, contrast=1, temp=0, tint=0))
    l1 = P.done(sup.get("sid", "wf1"), f_end, int(d.get("seed", 1114)), film)
    l1 = F.engines_finish(l1, sup, stress, drive_ids, meta, dict(d, look_pictures=look_pics),
                          turn_f=story_turn_f if story_turn_f is not None else turn_f, mono=mono_spans,
                          clear=[0.10, cap_y - 0.085, 0.90, cap_y + 0.02],
                          spoken=[(w["t0"] - LEAD_F / FPS, w["t1"] - LEAD_F / FPS) for w in clock.words])

    from wf1_layout import seat_display, caption_restarts, caption_pages
    # Use the final camera crops, not the source face coordinates.
    for path, key in keys.items():
        if key not in meta:
            rec = next((r for rows in list(pics.values()) + list(footage.values()) for r in rows if r["path"] == path), {})
            meta[key] = dict(faces=F.face_meta(rec))
    title_layout = seat_display(l1, meta, P.R)
    caps = [i for i in l1["items"] if i["k"] == "word" and i.get("role") == "cap"]
    if caps:
        # The shade under the captions: one pool a page, on that page's own words where the seat finally put them
        # (round1: one fixed 0.06-0.94 shape showed as a dark blob beside a one-word caption)
        l1["items"] += caption_pools(l1, P.R, bright, a=float(d.get("pool", 0.42)))
        first_word = caption_pages(caps)[0][2][0]
        cap_y = float(first_word["y"][-1] if isinstance(first_word["y"], list) else first_word["y"])
    restarts = caption_restarts(l1)
    if restarts:
        raise RuntimeError(f"caption track restarts in chunks: {restarts}")

    def frame_of(seg, word):
        if not (0 <= seg < nseg):
            return None
        hit = F.said_at(clock, seg, word)
        return Clock.f(hit[0]) - 1 if hit else None
    asks, why = alien_asks(d, style_id, frame_of)
    if asks:
        l1["alien"] = clean_asks(asks)
    changes = sorted({it["f"][0] for it in l1["items"] if it["k"] == "shot"})
    for s_ in l1.get("sound") or []:
        P.events.append(int(s_["event_f"]))
    cover = sum(x[1] - x[0] for x in ins) / max(f_voice_end, 1)
    look_f = sorted({0, 12, 40, *[x[0] + 5 for x in ins], *[x[1] + 8 for x in ins[::2]], *[a + 10 for a, _, _ in shown],
                     *([turn_f + 10] if turn_f is not None else []), f_end - 3})
    beats = dict(dur_f=f_end, voice_f=[LEAD_F, f_voice_end], cuts=sorted(set(cut_pts)), changes=changes,
                 events=sorted(set(P.events) | set(cut_pts) | set(changes) | {it["f"][0] for it in l1["items"] if it["k"] == "card"}),
                 pictures=P.pictures, on_screen=P.shown, caption_y=cap_y, caption_lines=lines, caption_contract=1, display=[dict(f=[a, b], text=t) for a, b, t in shown],
                 display_count=dict(proposed=len(proposed), landed=len(shown)),
                 labels=[dict(f=[a, b], text=t) for a, b, t in labels], turn_f=turn_f, story_turn_f=story_turn_f, pace=l1.get("pace"), cutaway_share=round(cover, 3),
                 style=style_id, layout=st["layout"], placement_check=placement_report, coverage=coverage_report,
                 title_layout=title_layout, caption_restarts=restarts, face_metadata=meta,
                 finishes=dict(black_and_white=len([p for p in P.pictures if p.get("finish")]), flashes=len(flashes), dark_ink_spans=len(bright),
                               take_past=[list(sp) for sp in take_past], past_share=round(past_share, 3),
                               balanced=sum(1 for it in l1["items"] if it.get("k") == "shot" and it.get("grade") and it.get("pic") not in tkeys)),
                 alien=dict(asked=[a["dev"] for a in l1.get("alien") or []], why=why, never=list(ALIEN_NEVER)),
                 sound_design=dict(l1.get("sound_design") or {}),       # with its dropped list: a refusal is measured, never guessed
                 look=[f for f in look_f if 0 <= f < f_end])
    return l1, beats
