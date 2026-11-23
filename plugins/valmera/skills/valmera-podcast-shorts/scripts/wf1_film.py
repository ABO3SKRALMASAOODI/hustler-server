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
CAPTION_CONTRACT = 11
Clock, r4, smooth = F.Clock, F.r4, F.smooth

WALL = "YouTube is refusing this Mac as a bot"       # the words a short stops with when its footage could not be fetched for that reason
# The film's Director (_shell/director.py) chooses its devices moment by moment from the whole palette (worker/scene/palette,
# the alien bank included): no device is banned or gated here (owner, 20 Nov 2026: maximum creativity in the edits).
# A style is a starting palette (type and colour identity); the Director's sections choose layout, surface and pace.
LAYOUT_AS = {"bleed": "bleed", "window": "window", "prints": "prints", "collage": "prints", "grid": "window", "split": "bleed",
             "type": "bleed"}      # a layout another lane has not built yet is drawn on its nearest built one

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


def _director():
    """_shell/director.py under a name of its own: the worker has a director.py too (the customer's Director)."""
    import importlib.util
    name = "valmera_shell_director"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, os.path.join(SKILLS, "_shell", "director.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


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
        if it.get("k") != "word" or it.get("role") not in ("cap", "big") or it.get("fact_type"):
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
                if it["v"] in voices:
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
    """Fill long holds inside a planned clause using its own spare clips or source stills."""
    from wf1_coverage import shared_candidates, same_picture, fresh_interval, camera_remainder
    pool = shared_candidates(coverage, footage)
    for block in coverage.get("blocked_sources", []):
        i = block["window"]
        pool[i] = [r for r in pool.get(i, []) if (r.get("source") or {}).get("url") != block["source"]]
    report = dict(proposed=0, accepted=0, rejected=0, fresh=0, spend=[], placements=[])
    taken = {}
    for clips in list(pool.values()) + [[x[4] for x in ins]]:
        for clip in clips:
            url = (clip.get("source") or {}).get("url")
            if url and clip.get("time_range"):
                taken.setdefault(url, []).append(tuple(clip["time_range"]))
    for i, window in enumerate(coverage["windows"]):
        if window.get('on_take') or camera_remainder(window):
            continue
        if window.get("single_action") and any(r[2]=="clip" and r[4].get("window")==i for r in ins):
            continue
        a = max(f_first, round(window["t0"] * FPS) + LEAD_F)
        b = min(f_last, round(window["t1"] * FPS) + LEAD_F)
        if b - a <= FILL_GAP_S * FPS:
            continue
        skip = set(rejected)
        names = [window["name"], *(window.get("subjects") or [])]
        for _ in range(rounds):
            local = [r for r in ins if r[0] < b and a < r[1]]
            edges = [a] + [f for row in local for f in row[:2]] + [b]
            stride = round(2.6 * FPS)
            slots = sum(1 + (end - start - round(2.8 * FPS)) // stride
                        for start, end in zip(edges[::2], edges[1::2]) if end - start > FILL_GAP_S * FPS)
            if not slots:
                break
            def unused(rec):
                return rec["path"] not in skip and not any(same_picture(rec, r[4]) for r in ins)
            clips = [c for c in pool[i] if unused(c)]
            photos = {name: [r for r in pics.get(name, []) if unused(r)] for name in names}
            available = [("clip", c.get("subject"), c) for c in pool[i] if c["path"] not in skip]
            for _ in range(slots - len(clips) - sum(map(len, photos.values()))):
                fresh = fresh_interval(available, taken, originals, cut_dir, 1.2 + 2 / FPS, ffmpeg, skip=skip)
                if not fresh:
                    break
                clips.append(fresh[0][2])
                report["fresh"] += 1
            filled = fill_gaps(local, {i: clips}, photos, script, seg_f, a, b, stress,
                               gap_s=FILL_GAP_S, hold_s=1.2, rest_s=0.8, key_f={i: (a+b)//2})
            new = [r for r in filled if r not in local]
            if not new:
                break
            for r in new:
                r[4] = dict(r[4], window=i, gap_fill=True, illustration=window["must_show"],
                            clause=window.get("say"), clause_t=[window["t0"], window["t1"]])
            accepted, result = checker(new)
            for row in accepted:
                if row[4]["path"] in composed_stills:
                    row[4]["verified_motion"] = False
            report["proposed"] += len(new)
            report["accepted"] += len(accepted)
            report["rejected"] += len(new) - len(accepted)
            report["spend"].extend(result.get("spend") or [])
            report["placements"].extend(result.get("placements") or [])
            ins = sorted([*ins, *accepted], key=lambda r: r[0])
            skip.update(r[4]["path"] for r in new)
            if len(new) == len(accepted):
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
    from placement_check import file_digest
    import math
    import subprocess
    up = clip_cover_up(rec)
    path = str(rec.get("path") or "")
    if up <= F.MAX_UP or not work or not ffmpeg or not rec.get("w") or not rec.get("h") or not os.path.isfile(path):
        return rec
    w2 = int(math.ceil(float(rec["w"]) * up / 2) * 2)
    h2 = int(math.ceil(float(rec["h"]) * up / 2) * 2)
    out_dir = os.path.join(work, "filled")
    dst = os.path.join(out_dir, hashlib.sha256(json.dumps([file_digest(path), w2, h2]).encode()).hexdigest()[:16] + ".mp4")
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


def archive_event(rec):
    """A short dated event credit quoted verbatim from the publisher's title."""
    if not (rec.get("faces") or rec.get("attribution")):
        return ""
    source=rec.get("source") or {}
    if not source.get("url") or not source.get("channel"):
        return ""
    title=(source.get("title") or "").strip()
    tail=title.rpartition("(")[2][:-1] if title.endswith(")") and "(" in title else title
    for sep in (" — ", " – ", " - "):
        tail=tail.rsplit(sep,1)[-1].strip()
    # A calendar-only title suffix can be a release date and does not identify the recorded event.
    import datetime
    for fmt in ("%B %Y","%b %Y","%Y"):
        try:
            datetime.datetime.strptime(tail,fmt)
        except ValueError:
            continue
        return ""
    # This reads a literal date from a citation; it does not date events from upload time.
    if len(tail)<=44 and any(word.isdecimal() and len(word)==4 and 1800<=int(word)<=2199 for word in tail.split()):
        return tail
    return ""


def clipped_source_ink(rec):
    """Would the phone crop slice through measured lettering on an attributed source?"""
    if not rec.get("attribution") or not rec.get("still"):
        return []
    import cv2
    from footage import overlay
    image = cv2.imread(rec["still"])
    if image is None:
        return []
    height, width = image.shape[:2]
    span = (W / H) / (width / height)
    if span >= 1:
        return []
    centre = .5 if rec.get("x") is None else float(rec["x"])
    left = max(0., min(1. - span, centre - span / 2))
    preview = cv2.resize(image, (overlay.WIDTH, round(height * overlay.WIDTH / width)))
    _, boxes = overlay.lines(preview)
    return boxes if any(x0 < left - .008 or x1 > left + span + .008 for x0, _, x1, _ in boxes) else []


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


def heal_base(l1):
    """Every frame of the film shows exactly one base picture, the renderer's own rule (scene.plan.eligible). A gap
    holds the picture before it (or the one after, at the film's start) and an overlap gives the frame to the later
    shot, so a frame lost at a boundary or a shot a repair dropped never loses the short (19 Nov m12 stopped in
    render on "1 frames without exactly one base picture"). Per-frame values keep their own frames. Returns what it
    changed: [{"id", "f": [old], "to": [new]}]."""
    dur = int(l1["dur_f"])
    shots = sorted((it for it in l1["items"] if it.get("k") == "shot"), key=lambda it: (int(it["f"][0]), int(it["f"][1])))
    if not shots:
        return []
    changed = []

    def refit(it, a, b):
        a0, b0 = int(it["f"][0]), int(it["f"][1])
        if (a, b) == (a0, b0):
            return
        for k, v in list(it.items()):
            if k != "f" and isinstance(v, list) and len(v) == b0 - a0 and b0 - a0 > 2:
                head = v[max(a - a0, 0):b - a0] if a >= a0 else [v[0]] * (a0 - a) + v[:b - a0]
                it[k] = head + [head[-1]] * (b - a - len(head)) if head else v
        changed.append(dict(id=it.get("id"), f=[a0, b0], to=[a, b]))
        it["f"] = [a, b]

    out = []
    for it in shots:                                   # an overlap: the frames go to the later shot
        a, b = int(it["f"][0]), min(int(it["f"][1]), dur)
        if out and a < int(out[-1]["f"][1]):
            prev = out[-1]
            if a <= int(prev["f"][0]):                 # the later shot covers the earlier one whole: the earlier one keeps
                a = int(prev["f"][1])                  # its frames and the later one starts after it
            else:
                refit(prev, int(prev["f"][0]), a)
        if b <= a:
            continue
        refit(it, a, b)
        out.append(it)
    keep = {id(it) for it in out}
    gone = [it for it in shots if id(it) not in keep]
    if gone:
        changed += [dict(id=it.get("id"), f=list(it["f"]), to=None) for it in gone]
        l1["items"] = [it for it in l1["items"] if not (it.get("k") == "shot" and id(it) not in keep)]
    if int(out[0]["f"][0]) > 0:                        # the film's start: the first picture is held from frame 0
        refit(out[0], 0, int(out[0]["f"][1]))
    for prev, nxt in zip(out, out[1:]):                # a gap: the picture before it is held
        if int(prev["f"][1]) < int(nxt["f"][0]):
            refit(prev, int(prev["f"][0]), int(nxt["f"][0]))
    if int(out[-1]["f"][1]) < dur:
        refit(out[-1], int(out[-1]["f"][0]), dur)
    return changed


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
            faces = [dict(box=box["box"], eyes=box["eyes"], samples=box.get("samples", []), score=1.0)] if box and sp.get("who") != "quiet" else None
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
    (wf1_short.supply); direction: {"display": [{"seg", "text"}], "past_segs": [i], "turn_seg": i, "arc": [...],
    "director": DIRECTOR_CONTRACT 4's beats, sections and asks}. With a director, each section takes its own layout,
    surface and pace, and the scene carries the Director's asks under ``devices`` for the palette to lay at the render."""
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
    P = F.Plan(voices, sup.get("sounds") or {}, frame_ids=True)
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
    reframes = {(r.get("window"),r.get("origin") or r.get("path")) if isinstance(r, dict) else (None,r)
                for r in d.get("reframe") or []}
    # (window, path): that window's slot goes to its next candidate; (window, None): the window rests on the speaker.
    rejected_pairs = {(r.get("window"), r.get("origin") or r.get("path")) for r in d.get("reject") or []
                      if isinstance(r, dict) and (r.get("origin") or r.get("path") or (r.get("fault") == "speaker" and r.get("window") is not None))}
    take = sup["take"]
    keys, meta = {}, {}
    from placement_check import file_digest
    # A rescout can give identical bytes another filename. Carry its exact
    # window-local finding when both the original bytes and provenance match.
    findings = [*d.get("reject", []), *d.get("reframe", [])]
    known = {}
    for r in findings:
        if isinstance(r, dict) and r.get("origin_sha256") and r.get("source"):
            known.setdefault((r["origin_sha256"], r["source"]), []).append(r)
    if known:
        for candidates in [*footage.values(), *pics.values()]:
            for c in candidates:
                path = c["path"]
                if not os.path.isfile(path):
                    continue
                source = (c.get("source") or {}).get("url") or c.get("page")
                for r in known.get((file_digest(path), source), []):
                    pairs = reframes if r["fault"] == "reframe" else rejected_pairs
                    pairs.add((r["window"],path))
    def asset_id(kind, rec):
        path = rec["path"]
        import hashlib
        # Stable across a rescout that inserts or reorders unused candidates.
        identity = file_digest(path) if os.path.isfile(path) else hashlib.sha256(path.encode()).hexdigest()
        return kind + identity[:24]
    # the take's own shots (a show cut between cameras): each is framed on its own speaker. One file, one asset a shot
    tshots = [s_ for s_ in take.get("cuts") or [] if float(s_["t1"]) - float(s_["t0"]) > 0.2] or [dict(t0=0.0, t1=float(take["dur"]), faces=take.get("faces") or [])]
    for lst in pics.values():
        for rec in lst:
            if rec["path"] not in keys:
                keys[rec["path"]] = P.image(asset_id("p",rec), rec)
    for lst in footage.values():
        for c in lst:
            if c["path"] not in keys:
                keys[c["path"]] = P.video(asset_id("v",c), c)
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
    dr = d.get("director") or {}

    def word_frame(w):
        """The programme frame a spoken word (numbered in order) starts on; the first word's is the film's first frame."""
        if not (0 <= int(w) < len(clock.words)):
            return None
        return 0 if int(w) == 0 else max(Clock.f(clock.words[int(w)]["t0"]) - 1, 0)

    # Bind source pictures to the very same spread word clock used by captions.
    reading = coverage.get('source_reading') or {}
    from wf1_source_frame import shared_presenter_region
    decode_region = shared_presenter_region(reading)
    source_rows = []
    speakers = []
    sequence = reading.get('inserts', []) + reading.get('speaker_inserts', [])
    starts = {rec['first'] for rec in sequence}
    def source_span(rec, live):
        a = word_frame(rec['first'])
        b = min(f_end, Clock.f(clock.words[rec['last']]['t1']))
        if rec['last']+1 in starts:
            b = word_frame(rec['last']+1)
        elif rec['last'] == len(clock.words)-1 and live:
            b = f_end
        if live:
            lo,hi = rec['shot']
            a = max(a, round(lo*FPS)+LEAD_F if lo>0 else 0)
            b = min(b, round(hi*FPS)+LEAD_F)
        return a,b

    for rec in reading.get('speaker_inserts', []):
        a,b = source_span(rec, True)
        if b > a:
            speakers.append((a,b,rec['tile']))
    for rec in reading.get('inserts', []):
        a,b = source_span(rec, 'panel_box' in rec)
        slot = f"source:{rec['first']}-{rec['last']}"
        if source_rows:
            a = max(a, source_rows[-1][1])
        if b <= a:
            continue
        if (slot,rec['path']) in rejected_pairs or (None,rec['path']) in rejected_pairs:
            if rec.get('tile'):
                lo,hi = rec.get('tile_shot',rec['shot'])
                first = max(a, round(lo*FPS)+LEAD_F if lo>0 else 0)
                last = min(b, round(hi*FPS)+LEAD_F)
                if first < last:
                    speakers.append((first,last,rec['tile']))
            continue
        if (slot,rec['path']) in reframes or (None,rec['path']) in reframes:
            left,top,right,bottom = rec['box']
            dx,dy = (right-left)*.07,(bottom-top)*.07
            wide = rec.get('panel_box')
            if not wide or wide == rec['box']:
                wide = [max(0.,left-dx),max(0.,top-dy),min(1.,right+dx),min(1.,bottom+dy)]
            rec = dict(rec,box=wide,reframe=True)
        name = rec['shows'] or 'Interview screen share'
        source = dict(url=script.get('source_url'),title=name,channel=script.get('source_credit'),
                      time_s=round(script.get('source_start',0)+rec['t'],3),box=rec['box'])
        source_rows.append([a,b,'photo',name,dict(rec,provider='interview-frame',source_slot=slot,source=source,page=source['url'],
                            clause=' '.join(w['w'] for w in clock.words[rec['first']:rec['last']+1]),
                            clause_t=[coverage['words'][rec['first']]['start'],coverage['words'][rec['last']]['end']])])
    source_spans = [r[:2] for r in source_rows]
    def on_evidence(a,b):
        return a is not None and b is not None and any(a < end and start < b for start,end in source_spans)

    import wf1_display
    reading_spans = []
    timed_facts = []
    pages = list(d.get("display_facts") or [])
    # A scenario on the same words as a sourced explanation shares its reading
    # surface. Give independent scenario steps their own remaining word interval.
    evidence = [p for p in pages if any(f.get("perspective") or f.get("excerpt") for f in p["facts"])]
    for page in list(pages):
        if not page.get("sequence"):
            continue
        overlaps = [p for p in evidence if p["first"] <= page["last"] and page["first"] <= p["last"]]
        if not overlaps:
            continue
        pages.remove(page)
        edges = sorted({page["first"], page["last"]+1, *(max(page["first"],p["first"]) for p in overlaps),
                        *(min(page["last"],p["last"])+1 for p in overlaps)})
        for lo, hi in zip(edges, edges[1:]):
            steps = [step for step in page["sequence"] if lo <= step["at"] < hi]
            if len(steps) >= 2 and all(lo > p["last"] or hi <= p["first"] for p in overlaps):
                pages.append(dict(page, first=steps[0]["at"], last=hi-1, sequence=steps))
    pages.sort(key=lambda p: p["first"])
    for comparison in coverage.get("comparisons") or []:
        competing = [p for p in pages if p["first"] <= comparison["last"] and comparison["first"] <= p["last"]]
        if any(p.get("sequence") or any(f.get("chain") or f.get("perspective") for f in p["facts"])
               for p in competing):
            continue
        pages = [p for p in pages if p not in competing]
        pages.append(dict(first=comparison["first"], last=comparison["last"], facts=[
            dict(relation=comparison["premise"], value=comparison["consequence"], source={})], analogy=True))
    # The take reader already located speakers in ordinary camera shots.
    # Screen-share presenter tiles are only one of the possible live returns.
    live_spans = [(round(sp["t0"]*FPS)+LEAD_F, round(sp["t1"]*FPS)+LEAD_F)
                  for shot in tshots for sp in shot.get("spans", []) if sp["who"] == "speaker"]
    for page in pages:
        a = word_frame(page["first"])
        b = min(f_end, Clock.f(clock.words[page["last"]]["t1"]))
        next_word = word_frame(page["last"] + 1)
        if next_word is not None:
            b = min(b, next_word)
        facts = page["facts"]
        # A speaker-led explanation already carries its report in voice and captions.
        # A quotation from an original or a qualified estimate has its own evidentiary work.
        qualified = page.get("sequence") or any((f.get("chain") or {}).get("stance") in wf1_display.CONTEXT_LABELS or f.get("perspective") or (f.get("chain") or {}).get("stance") in wf1_display.OPINION_LABELS or
                        (f.get("chain") or {}).get("stance") in wf1_display.ESTIMATE_LABELS or
                        (f.get("chain") or {}).get("original") for f in facts)
        narrated = all(f.get("chain") and not f.get("relation") for f in facts)
        on_presenter = (any(lo <= a and b <= hi for lo,hi,_ in speakers)
                        or any(lo < b and a < hi for lo,hi in live_spans))
        own_account = d.get("caption_ends") and all(
            f.get("chain") and f["chain"].get("speaker") == f["chain"].get("subject") for f in facts)
        if b > a and (qualified or not (on_evidence(a,b) or ((on_presenter or own_account) and narrated))):
            timed_facts.append((a,b,page))
    # Retained directions can predate the claims chain. A dense attributed block
    # still gets a reading interval, with all its authored copy and provenance.
    def opinion_byline(facts):
        return len(facts) == 1 and (facts[0].get("chain") or {}).get("stance") in wf1_display.OPINION_LABELS

    def needs_reading_page(facts):
        if opinion_byline(facts):
            return False
        if any(f.get("chain") or wf1_display.source_statement(f, script.get("source_url")) for f in facts):
            return True
        if not any(f.get("attribution") for f in facts):
            return False
        rows = wf1_display.typeset(facts, P.R, DISPLAY_W * W, str.upper if st["upper"] else str)
        return len(rows) > 4

    report_spans = [(a, b) for a, b, page in timed_facts if page.get("sequence") or needs_reading_page(page["facts"])]

    source_pages = [(a,b) for a,b,p in timed_facts if any(f.get("excerpt") or f.get("perspective") for f in p["facts"])]
    if source_pages:
        coverage = dict(coverage, windows=[
            dict(w, on_take=True) if any(lo <= round(w["t0"]*FPS)+LEAD_F+1 and
                round(w["t1"]*FPS)+LEAD_F <= hi+1 for lo,hi in source_pages) else w
            for w in coverage["windows"]])
    # A retained Director can predate the source reading. The original takes its word's
    # visual slot, while continuous rhythm direction still spans the film.
    comparison_spans = [(word_frame(c["first"]), Clock.f(clock.words[c["last"]]["t1"]))
                        for c in (coverage.get("comparisons") or []) + (coverage.get("speaker_claims") or [])]
    comparison_spans.extend(source_pages)
    comparison_spans.extend((a,b) for a,b,page in timed_facts if page.get("sequence"))
    if (source_rows or comparison_spans) and dr:
        def separate_ask(ask):
            a = word_frame(ask['at']['w'])
            b = (word_frame((ask.get('until') or ask['at'])['w']) or a or 0) + 1
            if a is None:
                return False
            return (not any(a < hi and lo < b for lo,hi in comparison_spans)
                    and (ask['dev'] == 'contour' or not on_evidence(a,b)))
        dr = dict(dr,asks=[ask for ask in dr.get('asks',[]) if separate_ask(ask)])

    def seg_span(i):
        return (seg_f[i], min(Clock.f(clock.segs[i][1]) + 6, f_end)) if 0 <= i < nseg else (None, None)
    secs = []                                            # the Director's sections as frame ranges: (f0, f1, section)
    if dr:
        for s_ in dr.get("sections") or []:
            a_, b_ = _director().section_frames(s_, word_frame, seg_span, f_end)
            if a_ is not None and b_ is not None and b_ > a_:
                secs.append((int(a_), int(b_), s_))
        if secs:
            secs[-1] = (secs[-1][0], max(secs[-1][1], f_end), secs[-1][2])

    def section_at(f):
        return next((s_ for a_, b_, s_ in secs if a_ <= f < b_), None)

    def layout_at(f):
        """The layout of the Director's section at frame f (the style's own when there is no director)."""
        s_ = section_at(f)
        return LAYOUT_AS.get((s_ or {}).get("layout"), st["layout"]) if s_ else st["layout"]
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
    punches = [turn_f] if turn_f is not None and turn_f > 2*FPS else []
    if story_turn_f and all(not (w["t0"] - 2 <= story_turn_f/FPS <= w["t1"] + 2)
                            for w in coverage["windows"]) and all(abs(f-story_turn_f)>3*FPS for f in punches):
        punches.append(story_turn_f)
    pieces = take_pieces(tshots, float(take["dur"]), punch=punches,
                         until=f_voice_end)
    for k, pc in enumerate(pieces):
        key = P.video("main" if k == 0 else f"own:cut{k}", dict(path=take["path"], w=take["w"], h=take["h"], fps=take.get("fps") or FPS, seconds=take["dur"]))
        fm = F.face_meta(dict(faces=pc["faces"] or take.get("faces") or []))
        if fm:                                           # a speaker's head, not a face-shaped thing on the shelf behind him
            tall = max(f[3] for f in fm)
            fm = [f for f in fm if f[3] >= 0.5 * tall]
        up = float(d.get("take_up", TAKE_UP))
        meta[key] = dict(pw=int(take["w"]), ph=int(take["h"]), faces=fm, max_up=up,
                         face_h=PUNCH_FACE_H if pc["punch"] else medium_face_h(fm, int(take["h"]), up))
        pc["key"] = key
        if fm:
            meta[key]["track"] = next((f["samples"] for f in pc["faces"] if f.get("samples")), [])
    from placement_check import CONTRACT as placement_contract, check
    from wf1_coverage import checked_proposals, require_coverage, originals_of
    checker=lambda rows:check(rows,script,clock,sup['work'],sup['ffmpeg'])
    # A replay can carry the delivered edit's time ranges and source-backed
    # picture decisions. Compose those cuts again through the current engine.
    # A new film makes its picture decisions below as usual.
    archive = sup.get("archived_placement_plan")
    if archive is not None:
        if archive["report"].get("contract") == placement_contract:
            ins, placement_report = archive["rows"], dict(archive["report"], spend=[])
        else:
            ins, placement_report = checker(archive["rows"])
        placement_report["replayed_from"] = archive["run_id"]
    else:
        ins,placement_report=checked_proposals(coverage,footage,pics,f_end/FPS,checker,composed_stills,
            rejected=rejected_pairs,originals=originals_of(sup['work']),cut_dir=os.path.join(sup['work'],'fresh-intervals'),ffmpeg=sup['ffmpeg'])
        ins, fill = fill_holes(ins, coverage, footage, pics, script, seg_f, LEAD_F+FPS, f_last, stress, checker,
                               placement_report.get("rejected_paths") or [], composed_stills,
                               originals=originals_of(sup["work"]), cut_dir=os.path.join(sup["work"], "fresh-intervals"),
                               ffmpeg=sup["ffmpeg"])
        placement_report["gap_fill"] = {k: v for k, v in fill.items() if k != "spend"}
        placement_report["spend"].extend(fill["spend"])
    from wf1_coverage import action_holds
    from placement_check import legacy_detail
    def detail(row, action):
        geometry = legacy_detail(row, sup["work"], sup["ffmpeg"], action)
        cost = geometry.pop("framing_spend", None)
        if cost:
            placement_report["spend"].append(cost)
        return geometry
    ins = action_holds(ins, coverage, FPS, LEAD_F, detail)
    for row in ins:
        if row[2] == "clip" and row[4].get("reframe") and "action_end" not in row[4]:
            row[4].update(detail(row, False))
    if source_rows:
        ins, _ = _director().hold_take(ins,source_spans,min_f=1,fps=FPS)
        ins = sorted(ins+source_rows,key=lambda row:row[0])
    for row in ins:
        rec = row[4]
        origin = (rec.get("text_cleared") or {}).get("from_path") or rec["path"]
        if (rec.get("window"),origin) in reframes or (None,origin) in reframes:
            row[4] = dict(rec,reframe=True)
    for row in ins:                                      # a clip the check cut clear of another editor's text is a new file
        if row[4]["path"] not in keys:
            keys[row[4]["path"]] = (P.video if row[2] == "clip" else P.image)(asset_id("v" if row[2]=="clip" else "p",row[4]), row[4])
        if row[4].get("foley") and keys.get(row[4]["path"]) in P.assets:
            # the object the check saw in this picture (placement_check foley): its landing sounds as that object
            # (worker/score/sound_screen.py)
            P.assets[keys[row[4]["path"]]]["foley"] = str(row[4]["foley"])
    coverage_report = require_coverage(ins, coverage, f_end/FPS, final=archive is not None or bool(d.get('coverage_final')))
    past = {int(i) for i in d.get("past_segs") or [] if isinstance(i, int)}
    cut_pts, drive_ids, mono_spans, bright = [], set(), [], []
    # Match inserts locally in bleed(); the interview anchors the programme-wide grade.
    look_pics = [take["still"]] if take.get("still") else []
    tcuts = [pc["f"][0] for pc in pieces[1:]]          # the take's own cuts and its cuts between speakers, in film frames
    from wf1_coverage import speaker_moves, move_frame
    composed_spans = [r[:2] for r in ins] + [[lo,hi] for lo,hi,_ in speakers] + [list(span) for span in source_pages]
    resolved_moves,move_spend=speaker_moves(coverage,composed_spans,f_end,tcuts,sup['work'])
    signal_moves = stress_moves(coverage.get('unresolved_holds'), stress)
    d['band']['motivated_frames']=sorted({move_frame(coverage,m,FPS,LEAD_F) for m in resolved_moves} | set(signal_moves))
    placement_report['spend']+=move_spend
    coverage_report['speaker_moves']=resolved_moves
    coverage_report['stress_moves']=signal_moves
    coverage_report['take_pieces']=[dict(f=pc["f"], who=pc["who"], punch=pc["punch"], cut=pc["cut"]) for pc in pieces]
    coverage_report['speaker_on_camera']=take.get("on_camera")
    # The Director's devices that stand on the speaker (a lamp relights his take, the world goes negative round him, a
    # spotlight finds his face) need him on screen over their span: the take holds there and the cutaways step round it
    # (coverage and the speaker's moves were measured above, before the hold, so their cached checks stay valid)
    pre = []
    if dr:
        DR = _director()
        pre, _ = DR.palette_asks(dr, word_frame, seg_span, None, f_end)
    if pre and take.get("on_camera") is not False:
        held = DR.take_spans(pre, f_end)
        if held:
            other = [row for row in ins if row[4].get('provider') != 'interview-frame']
            other, cleared = DR.hold_take(other, held, fps=FPS)
            ins = sorted(other+source_rows,key=lambda row:row[0])
            coverage_report['take_held'] = dict(spans=held, cleared_f=cleared)

    # Full-page authored type takes the screen. A documentary insert underneath would
    # report source footage and motion that cannot be seen, or date a later archival event.
    pages=[ask["f"] for ask in pre if ask.get("dev")=="type_run" and ask.get("words")
           and isinstance(ask.get("f"),list) and len(ask["f"])==2]
    if pages:
        other,cleared=DR.hold_take([row for row in ins if row[4].get('provider') != 'interview-frame'],pages,min_f=1,fps=FPS)
        ins=sorted(other+source_rows,key=lambda row:row[0])
        coverage_report["authored_pages"]=dict(spans=pages,cleared_f=cleared)
    if source_pages:
        ins, _ = _director().hold_take(ins, source_pages, min_f=1, fps=FPS)
    # Reserve picture space for actual multi-line performances on the speaker.
    # Read the same hold and word clock as the palette, including its implicit exit.
    said = [[w["w"], Clock.f(w["t0"])-1, Clock.f(w["t1"]), int(w["seg"])] for w in clock.words]
    stacks = []
    for ask in pre:
        if ask.get("dev") != "staircase" or len(ask.get("lines") or []) < 2:
            continue
        from scene.palette import kit
        lines = [line if isinstance(line, dict) else {"t":line} for line in ask["lines"]]
        first, end, _ = kit.span(dict(dur_f=f_end, said=said), ask, need=int(ask.get("hold",36)),
                                 text=" ".join(str(line.get("t","")) for line in lines))
        entry = min(line["at"] for line in lines) if all(isinstance(line.get("at"), int) for line in lines) else first
        stacks.append((max(0, max(first,entry)-8), min(f_end,end+8), ask["id"]))

    def quote_panel(fa, fb, clock_start):
        # The original camera can cut to a listener halfway through one speaker's account.
        # The preceding complete sentence takes its own cited reading interval.
        complete = [i for i,(_,end) in enumerate(clock.segs) if end <= clock_start+.12]
        if not script.get("source_credit"):
            return False
        segment = complete[-1] if complete else 0
        if segment not in (reading.get('quote_segments') or []):
            return False
        claim = next((c for c in d.get("claims") or [] if clock.words[c["first"]]["seg"] <= segment
                      <= clock.words[c["last"]]["seg"]), None)
        line = claim["report"] if claim else str(script["segments"][segment]["say"]).strip()
        if not complete:
            # Open a listener shot on the attributed words being heard, until
            # the interview cuts to the actual speaker. Use the whole off-camera
            # phrase even when a finish divides that source shot into two pieces.
            off = next((r for r in coverage.get("off_camera", []) if r["first"] == 0), None)
            if not off:
                return False
            claim = None
            phrase = [w["w"] for w in clock.words[:off["last"]+1]]
            punctuated = [i+1 for i,w in enumerate(phrase) if w.endswith((",", ".", "?", "!", ";"))]
            line = " ".join(phrase[:punctuated[-1]] if punctuated else phrase).rstrip(",;")
        if not line:
            return False
        P.page([fa,fb],"#171B23","#0D1015",grain=.018)
        source_time = script.get("source_start", 0) + clock.segs[segment][0] - LEAD_F/FPS
        stamp = int(source_time)
        citation = f'{script["source_credit"]}  ·  {stamp//60}:{stamp%60:02d}'
        if claim:
            label = wf1_display.ESTIMATE_LABELS.get(claim.get("stance"))
            byline = f'{claim["speaker"]} · {label}' if label else f'{claim["speaker"]} reports'
        else:
            byline = next((s.get("speaker") for s in script.get("speaking_turns", [])
                           if all(type(s.get(k)) in (int,float) for k in ("start","end"))
                           and s["start"] < script.get("source_start",0)+clock.segs[segment][1] and source_time < s["end"]), "") or ""
        credit = "  ·  ".join(filter(None,(byline,citation)))
        wf1_display.reading_card(P,(fa,fb),line if claim else "“"+line+"”",byline,citation,pal,centre=.46)
        # A short opening quotation hands the rest of the sentence back to
        # the ordinary timed captions, even while its source credit stays up.
        quoted_end = fb if complete else min(fb, Clock.f(clock.words[(punctuated[-1] if punctuated else len(phrase))-1]["t1"]))
        if quoted_end > fa:
            reading_spans.append((fa,quoted_end))
        record = dict(report=line, chain=claim) if claim else dict(quote=line)
        coverage_report.setdefault("quoted_source", []).append(dict(record, f=[fa,fb], credit=credit,
                                                                      url=script.get("source_url")))
        return True

    def take_shot(a, b):
        """The take between pictures: each framing on the one who speaks it, cut where the show cut and where the
        speaker changes; the moves inside a framing are the driver's (motivated words, the voice's stress)."""
        if b-a < 2:
            return
        # a section of the past is graded apart, so its edges are real cuts in the take; the payoff and the story turn
        # are not joins of their own (a flash there would land inside a continuous shot: it snaps to a real cut)
        joins = [f for sp in take_past for f in sp]
        # When a full-height crop has no room to tighten, widen the live source into a readable
        # interview panel on one of the director's actual rhetorical turns.
        anchors = sorted(move_frame(coverage,m,FPS,LEAD_F) for m in resolved_moves
                         if m.get("why") and a+2*FPS < move_frame(coverage,m,FPS,LEAD_F) < b-2*FPS)
        panel = ()
        eligible = []
        for pc in pieces:
            start,end=max(a,pc["f"][0]),min(b,pc["f"][1])
            if pc["who"]!="speaker" or end-start<=8*FPS:
                continue
            info=meta[pc["key"]]
            max_width=min(1.,(W/H)/(info["pw"]/info["ph"]))
            turns=[f for f in anchors if start+2*FPS<f<end-2*FPS]
            if turns and any(face[2]>.75*max_width for face in info.get("faces",[])):
                cue=min(turns,key=lambda f:abs(f-(start+end)/2))
                finish=min([end]+[f for f in turns if f>=cue+2*FPS])
                eligible.append((end-start,cue,finish))
        if eligible:
            _,cue,finish=max(eligible)
            panel=(cue,finish)
        authored = reading.get("authored_shots", [])
        reactions = [(word_frame(r["first"]), Clock.f(clock.words[r["last"]]["t1"]))
                     for r in reading.get("reactions", [])]
        layout_edges = [round(t*FPS)+LEAD_F for shot in authored for t in shot]
        layout_edges.extend(f for span in reactions for f in span)
        layout_edges.append(turn_f if turn_f is not None else f_last)
        layout_edges.extend(f for span in report_spans for f in span)
        layout_edges.extend(f for lo,hi,_ in speakers for f in (lo,hi))
        edges = sorted({a, b, *(f for f in layout_edges if a < f < b), *[c for c in tcuts + joins + list(panel) if a+3 <= c <= b-3],
                        *(f for span in stacks for f in span[:2] if a < f < b)})
        for fa, fb in zip(edges, edges[1:]):
            k = sum(1 for c in tcuts if c <= (fa+fb)/2)
            t0, t1 = max(fa-LEAD_F,0)/FPS, min(max(fb-LEAD_F,1)/FPS,float(take["dur"]))
            on_report = any(lo <= fa < hi for lo,hi in report_spans)
            if any(lo <= fa < hi for lo,hi in source_pages):
                P.page([fa,fb],"#14171B",grain=0)
                cut_pts.append(fa)
                continue
            tile = next((tile for lo,hi,tile in speakers if lo <= fa < hi), None)
            if tile:
                from scene.motion import fit_panel
                from wf1_source_frame import presenter_card
                seat = [0,.04,1,.48] if on_report else [0,.08,1,.82]
                dest = fit_panel(tile,(take['w'],take['h']),(W,H),seat)
                P.page([fa,fb],"#151A20",grain=0)
                presenter_card(P,[fa,fb],pieces[k]['key'],dest,tile,tile,[t0,t1],
                               sup['work'],sup['ffmpeg'],decode_region,presenter_insert=True,source_ink=[dest],caption_baseline=.87)
                cut_pts.append(fa)
                continue
            authored_span = next(((start, end) for start, end in authored if start <= (t0+t1)/2 < end), None)
            close_start = turn_f if turn_f is not None else f_last
            closing_speaker = (authored_span and fa >= close_start - FPS
                               and (round(authored_span[0]*FPS)+LEAD_F >= close_start or
                                    round(authored_span[1]*FPS)+LEAD_F >= f_voice_end)
                               and pieces[k]["who"] == "speaker" and meta[pieces[k]["key"]]["faces"]
                               and not any(lo <= fa < hi for lo, hi in reactions))
            if closing_speaker and on_report:
                # Keep the citation's reading surface below the live presenter.
                from scene.motion import Cam
                info = meta[pieces[k]["key"]]
                box = [.05, .10, .95, .47]
                camera = Cam(info["pw"], info["ph"], .90*W/(.37*H), .90*W, info["max_up"])
                face = max(info["faces"], key=lambda f: f[2]*f[3])
                crop = camera.on_face(face[:4], height=.68)
                P.page([fa,fb], "#14171B", grain=0)
                P.card([fa,fb], pieces[k]["key"], box, crop=crop, src=[t0,t1],
                       speaker_panel=True, source_ink=[box], caption_baseline=.88)
                cut_pts.append(fa)
                continue
            if on_report or (authored_span and not closing_speaker):
                # The source cut is already a composition. Fit all panels and both original
                # credit corners together, with an independent ground for the caption.
                aspect = take["w"]/take["h"]
                max_height, centre = (.38, .29) if on_report else (.70, .46)
                width = min(1., max_height * H / W * aspect)
                height = width * W / H / aspect
                box = [.5-width/2, centre-height/2, .5+width/2, centre+height/2]
                P.page([fa,fb],"#14171B",grain=0)
                P.card([fa,fb],pieces[k]["key"],box,src=[t0,t1],crop=[0,0,1,1],
                       source_frame=True,caption_baseline=.88 if on_report else min(.91,box[3]+.10))
                coverage_report.setdefault("authored_source",[]).append(dict(f=[fa,fb],url=script.get("source_url"),
                    publisher=script.get("source_credit"),start=script.get("source_start",0)+t0,box=box))
                cut_pts.append(fa)
                continue
            if pieces[k]["who"] == "listener" and quote_panel(fa,fb,t0):
                cut_pts.append(fa)
                continue
            stack = next((span for span in stacks if span[0] <= fa < span[1]), None)
            active_panel = stack or (panel if panel and panel[0] <= fa < panel[1] else None)
            info = meta[pieces[k]["key"]]
            # A landscape single already in close-up cannot fit shoulders and gestures in
            # the portrait cover. Keep the camera's own composition in a fitted live panel.
            cover = min(1., (W/H) / (info["pw"]/info["ph"]))
            if (cover < .65 and not active_panel and not reading.get("inserts") and pieces[k]["who"] == "speaker" and
                    any(face[2] > .82*cover for face in info["faces"])):
                from scene.motion import fit_panel
                face = max(info["faces"], key=lambda f:f[2]*f[3])
                width = min(1., max(cover, 2*face[2]))
                left = max(0., min(1.-width, face[0]+face[2]/2-width/2))
                full = [left, 0., left+width, 1.]
                box = fit_panel(full, (info["pw"],info["ph"]), (W,H), [0,.10,1,.56 if on_report else .80])
                P.page([fa,fb], "#171B23", "#0D1015", grain=.018)
                P.card([fa,fb], pieces[k]["key"], box, src=[t0,t1], crop=full, source_frame=True,
                       caption_baseline=.87)
                coverage_report.setdefault("speaker_framing",[]).append(dict(f=[fa,fb], source_crop=full))
                cut_pts.append(fa)
                continue
            if active_panel and info.get("faces") and pieces[k]["who"] == "speaker":
                face=max(info["faces"],key=lambda box:box[2]*box[3])
                box = [0.,0.,1.,.73] if stack else [0.,.055,1.,.915]
                asp=(box[2]-box[0])*W/((box[3]-box[1])*H)
                crop=F.cover_crop(info["pw"],info["ph"],asp,(face[0]+face[2]/2,face[1]+face[3]/2),(.5,.5))
                P.page([fa,fb],"#171B23","#0D1015",grain=.018)
                # A short pullback from bleed, then the reverse on the way out.
                # Keep the tracked face at the same horizontal anchor through the move.
                height = box[3] - box[1]
                centre = (box[3] + box[1]) / 2
                first, end = active_panel[:2]
                ramp = min(8, max(1, (end-first-1)//2))
                scale, centres = [], []
                for frame in range(fa, fb):
                    u = min(1., (frame-first)/ramp, (end-1-frame)/ramp)
                    eased = u*u*(3-2*u)
                    scale.append(1 + (1/height-1)*(1-eased))
                    centres.append(.5+(centre-.5)*eased)
                P.card([fa,fb],pieces[k]["key"],box,src=[t0,t1],crop=crop,
                       s=scale,y=centres,speaker_panel=True, **({"panel_type": stack[2]} if stack else {}))
                coverage_report.setdefault("speaker_panels",[]).append(dict(f=[fa,fb],word_frame=first))
                cut_pts.append(fa)
                continue
            if reading.get('inserts') and pieces[k]['who'] == 'speaker':
                from wf1_source_frame import presenter_card, presenter_crop
                height = .76
                framing = presenter_crop(reading['inserts'],pieces[k]['faces'],[t0,t1],W/(H*height))
                if framing:
                    tile,crop = framing
                    # Use the verified webcam tile on the explanation, then cut back to the document.
                    P.page([fa,fb],"#151A20",grain=0)
                    presenter_card(P,[fa,fb],pieces[k]['key'],[0,0,1,height],tile,crop,[t0,t1],
                                   sup['work'],sup['ffmpeg'],decode_region,source_ink=[[0,0,1,height]],caption_baseline=.87)
                    cut_pts.append(fa)
                    continue
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
    # with a director the sections it prints in black and white are the mono ones, whatever the style; else the style's rule
    mono_dir = [(a_, min(b_, f_voice_end)) for a_, b_, s_ in secs if str(s_.get("surface")).lower() in ("mono", "gate") and
                min(b_, f_voice_end) - a_ >= 12] if secs else None
    # the take itself takes the section's finish too (low saturation while he tells of the past), cut at the joins
    take_past = (mono_dir if mono_dir is not None else
                 section_spans(past, seg_f, f_voice_end) if st["mono"] == "past" and by_section else [])

    def is_mono(f0):
        if mono_dir is not None:
            return any(a_ <= f0 < b_ for a_, b_ in mono_dir)
        return st["mono"] == "all" or (st["mono"] == "past" and by_section and seg_at(f0) in past)

    def note(f0, f1, kind, name, rec, mode):
        # origin: the candidate this picture is (a copy the check cut clear of another editor's text is placed under a
        # new path). The composed review's findings are keyed on it, so its repair reaches the candidate the next build
        # proposes (adversarial review, 20 Nov: findings keyed on the cleared path never matched and the short was lost).
        origin = (rec.get("text_cleared") or {}).get("from_path") or rec["path"]
        P.pictures.append(dict(f=[f0, f1], visible_f=[f0, rec.get("audit_until", f1)], kind=kind, name=name, path=rec["path"], origin=origin,
                               shows=rec.get("shows"), mode=mode, pic=keys[rec["path"]], source_slice=rec.get("src"),
                               window=rec.get("window"), clause=rec.get("clause"), clause_t=rec.get("clause_t"), fit=rec.get("fit"),
                               reframed=bool(rec.get("reframe")), attribution=rec.get("attribution"), name_super=rec.get("name_super"),
                               **{k:rec[k] for k in ("action_box","action_end","action_caption") if k in rec},
                               w=rec.get("w"), h=rec.get("h"), upscaled_from=rec.get("upscaled_from"), rung=rec.get("rung"),
                               how=rec.get("how"), source_size=rec.get("source_size"),
                               illustration=rec.get('illustration'), source_evidence=rec.get('source') or dict(url=rec.get('page'),title=rec.get('title'),channel=rec.get('credit')),
                               finish="black and white" if is_mono(f0) else None, motion_p90=rec.get('placed_motion_p90'),
                               verified_motion=rec.get('verified_motion') is True,
                               source=(rec.get("source") or {}).get("url") if kind == "clip" else rec.get("page"), time_range=rec.get("time_range")))

        if rec.get("fresh_of") and (not rec.get("text_cleared") or rec.get("fresh_candidate")):
            # Restore the original freshly cut media, including its own dimensions,
            # before running a new placement check or text-clearing pass.
            saved = dict(rec)
            if rec.get("text_cleared"):
                saved = dict(rec["fresh_candidate"])
            P.pictures[-1]["fresh_clip"] = saved

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

    def window(f0, f1, kind, name, rec, key, source_ink=()):
        n = f1 - f0
        mono = is_mono(f0)
        evidence = bool(source_ink or rec.get("action_box") or rec.get("attribution") or rec.get("reframe") or rec.get("full_source") or archive_event(rec)) or rec.get("provider") == "primary-document"
        if evidence:
            P.page([f0, f1], "#171B23", "#0D1015", grain=.018)
        else:
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
        if evidence:
            # The source's labels and fine print belong together on the evidence card.
            kw["source_ink"] = [[x0, y0, x1, y1]]
        if kind == "clip":
            kw["src"] = list(rec["src"])
        if mono:
            kw["look"] = "mono_silver"
        from wf1_documents import valid_detail
        detail = rec.get("action_box")
        if valid_detail(detail):
            from scene.motion import fit_panel
            # The frame reader located the action on these same moving source pixels.
            crop = list(detail)
            if rec.get("action_caption"):
                # Include the adjacent changing rows when the result is a single line of UI.
                pad = .04*(crop[2]-crop[0])
                crop[0], crop[2] = max(0.,crop[0]-pad), min(1.,crop[2]+pad)
                height = min(1., max(crop[3]-crop[1], (crop[2]-crop[0])*aspect/1.6))
                top = max(0., min(1.-height, (crop[1]+crop[3]-height)/2))
                crop[1], crop[3] = top, top+height
            x0,y0,x1,y1 = fit_panel(crop,(rec["w"],rec["h"]),(W,H),WINDOW_BOX)
            kw.update(crop=crop, source_ink=[[x0,y0,x1,y1]])
        action_end = min(f1,max(f0+1,rec.get("action_end",f1)))
        kw["op"] = kw["op"][:action_end-f0]
        P.card([f0, action_end], key, [x0, y0, x1, y1], **kw)
        if action_end < f1:
            lo, hi = rec["src"]
            held = max(lo,hi-1/(rec.get("fps") or FPS))
            P.card([action_end,f1], key, [x0,y0,x1,y1], **dict(kw,src=[held,hi],op=1.))
        note(f0, f1, kind, name, rec, "window")
        P.pictures[-1]["source_crop"] = kw["crop"]

    def document(f0, f1, kind, name, rec, key):
        from scene.motion import fit_panel
        from wf1_documents import recorded_detail, valid_detail

        span = [f0, f1]
        size = (rec["w"], rec["h"])
        literal = rec.get("attribution") or ""
        detail = (rec.get("document") or {}).get("detail")
        if not valid_detail(detail):
            detail = recorded_detail(rec["path"], literal)
        P.page(span, "#171B23", "#0D1015", grain=.018)

        def view(crop, seat, during, **kw):
            box = fit_panel(crop, size, (W, H), seat)
            P.card(during, key, box, crop=crop, source_ink=[box], caption_baseline=.88, **kw)

        full = [0., 0., 1., 1.]
        if detail or literal:
            context = [.055, .198, .945, .30]
            reading = span
            # A broad UI or diagram needs its full overview before the enlarged detail.
            overview = fit_panel(full, size, (W, H), context)
            if overview[2] - overview[0] < .6 and f1-f0 >= 18:
                reveal = f0 + round((f1-f0)*.4)
                view(full, [.055, .198, .945, .50], [f0, reveal])
                reading = [reveal, f1]
            view(full, context, reading)
            if detail:
                # Older raster UI headings can be only a few dozen pixels wide.
                # Enlarge their ink without blowing a low-resolution label up to screen width.
                half = min(.445, (detail[2]-detail[0])*size[0] / 360)
                view(detail, [.5-half, .31, .5+half, .50], reading, document_excerpt=True)
            else:
                # The already-read label also works on machines without local text recognition.
                wf1_display.reading_card(P, reading, literal, "", "", pal, centre=.40, max_height=.15, group="excerpt")
        else:
            view(full, [.055, .198, .945, .48], span)
        note(f0, f1, kind, name, rec, "document")
        P.pictures[-1].update(source_crop=full, document=rec.get("document"), excerpt_crop=detail)

    def bleed(f0, f1, kind, name, rec, key):
        source_ink = clipped_source_ink(rec) if kind == "clip" else []
        if rec.get("action_box") or rec.get("reframe") or rec.get("full_source") or rec.get("provider") == "primary-document" or source_ink or archive_event(rec) or (rec.get("attribution") and rec.get("verified_motion") is False):
            return window(f0, f1, kind, name, rec, key, source_ink)             # the approved document crop is already the editorial rectangle
        bal = None if is_mono(f0) else clip_balance(take.get("still"), rec.get("still") if kind == "clip" else rec.get("path"))
        gk = dict(grade=bal) if bal else {}               # matched to the take it cuts against, part of the way (round1 colour)
        if kind == "clip":                                   # moving footage fills the phone, whatever its size (fill_size)
            it = P.shot([f0, f1], key, None, src=list(rec["src"]), **gk)
            it.pop("crop")
            meta[key] = clip_meta(rec)
            # A clip's own subject motion (a gesturing speaker, a basketball game) is not a cut that travels: marked
            # `moving`, every plain cut between clips took a sound (owner, 20 Nov: "the SFX is really not related at
            # all"). A cut sounds when the picture itself travels (a whip's blur, a camera move, a flash frame).
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
            if kind == "clip" or rec.get('provider') in ('interview-frame', 'primary-document'):
                continue                                     # composed in its own layout below
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

    document_pics = set()
    pos, labelled, labels, flashes = 0, set(), [], []
    runs = _runs(ins)
    layouts_used = []
    for run in runs:
        r0, r1 = ins[run[0]][0], ins[run[-1]][1]
        take_shot(pos, r0)
        lay = layout_at(r0)
        layouts_used.append(lay)
        if lay == "prints":
            prints(run)
        for k in run:
            f0, f1, kind, name, rec = ins[k]
            if rec.get('provider') == 'interview-frame':
                from wf1_source_frame import compose
                key = keys[rec["path"]]
                edges = sorted({f0,f1,*(f for span in report_spans for f in span if f0<f<f1)})
                for fa,fb in zip(edges,edges[1:]):
                    src = [max(0,(f-LEAD_F)/FPS) for f in (fa,fb)]
                    compose(P,[fa,fb],rec,key,pieces[0]["key"],src,W,H,sup["work"],sup["ffmpeg"],decode_region,
                            report=any(lo<=fa<hi for lo,hi in report_spans))
                note(f0,f1,kind,name,rec,"source-insert")
                P.pictures[-1].update(source_crop=rec["box"],source_slot=rec["source_slot"])
            elif rec.get("provider") == "primary-document":
                document(f0, f1, kind, name, rec, keys[rec["path"]])
                document_pics.add(keys[rec["path"]])
            elif lay == "bleed" or kind == "clip":
                bleed(f0, f1, kind, name, rec, keys[rec["path"]])
            elif lay == "window":
                window(f0, f1, kind, name, rec, keys[rec["path"]])
            cut_pts.append(f0)
            P.events.append(f0)
            label = rec.get("name_super") or (rec.get("attribution") if d.get("labels") else None)
            if rec.get("provider") == "primary-document":
                # The figure and the supporting statement may have different sources.
                urls = {url for url in (rec.get("page"), rec.get("url")) if url}
                credited = [(a, b) for a, b, page in timed_facts
                            if any(fact["source"].get("url") in urls for fact in page["facts"])]
                if rec.get("credit"):
                    intervals, _ = _director().hold_take([ins[k]], credited, min_f=14)
                    labels.extend((lo, hi, rec["credit"], keys[rec["path"]]) for lo, hi, *_ in intervals)
                continue
            event=archive_event(rec)
            if event:
                label=" · ".join(s for s in (rec.get("name_super"),event) if s)
                labels.append((f0,f1,label,keys[rec["path"]]))
                continue
            source_url = (rec.get("source") or {}).get("url") or rec.get("page")
            if (rec.get("attribution") or rec.get("action_caption")) and (rec.get("reframe") or rec.get("action_box")) and not label:
                publisher = (rec.get("source") or {}).get("channel")
                label = " · ".join(filter(None,(rec.get("action_caption") or rec.get("attribution"),publisher)))
            if source_url and not label and rec.get("provider") != "interview-frame":
                publisher = (rec.get("source") or {}).get("channel") or rec.get("credit")
                if publisher:
                    label = f"Source · {publisher}"
            if rec.get("action_box") and label:
                wf1_display.reading_card(P, [f0,f1], "", "", label, pal, centre=.12, group="figure-credit")
                continue
            if source_url and label and label not in labelled and f1 - f0 >= 16:
                labelled.add(label)
                labels.append((f0 + 2, min(f1, f0 + int(2.4 * FPS)), label, keys[rec["path"]]))
        if st["flash"] == "runs" and not dr and (not flashes or r0 - flashes[-1] >= 4 * FPS):      # the projector catches: into a run of archive
            flashes.append(r0)
        pos = r1
    take_shot(pos, f_end)
    if st["flash"] == "turn" and turn_f is not None and not dr:        # with a director its asks place every flash
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
    if st["flash"] == "turn" and story_turn_f is not None and not dr:
        sf = on_cut(story_turn_f)                            # where the story turns: a landing the sound's turn hit and the drop answer
        if sf is not None and all(abs(sf - f) >= 2 * FPS for f in flashes):
            flashes.append(sf)
    for a_, _b in (take_past[:2] if not dr else []):        # into a section of the past, on the take's join
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

    def land(a, b, text, vk, y, ink, delay=0, stroke="#0B0A09", on=None, **attrs):
        n = b - a
        kk = min(int(bake.BOUNCE["n"]), n)
        ys = [r4(y + float(q)) for q in bake.bounce(kk, rest=0.0)] + [r4(y)] * (n - kk)
        bl = [round(float(q), 3) for q in bake.bounce_blur(kk)] + [0.0] * (n - kk)
        P.word([a + delay, b], text, vk, 0.5, ys[delay:] if delay else ys, ink, [min(1.0, (q + 1) / 2) for q in range(n - delay)],
               align="c", z=8, blur=bl[delay:] if delay else bl, role="big", stroke=dict(w=3.0, c=stroke), on=a if on is None else on, **attrs)

    def ground_at(f, y0, y1):
        """The tone (0..1) of the picture up at frame f, under the band y0-y1 of the frame."""
        for f0, f1, kind, _n, rec in ins:
            if f0 <= f < f1:
                g = seat_luma(rec, kind, y0, y1)
                return g if layout_at(f0) == "bleed" or kind == "clip" else g * DIM["gain"]
        return seat_luma(dict(still=take.get("still")), "clip", y0, y1) if take.get("still") else 0.0

    # ---- the words that carry the story, large, each on the word that says it; never two inside two seconds. Typeset by
    # the job each does (display_rows): at least 1.6x the caption, on a seat of their own above the caption's, a long
    # phrase as a two-size stack round its key word, a contrast on a bar; ink chosen by the contrast measured on the
    # ground under the block (round1 default-design)
    # The direction ranks its proposals best first, with alternates: the film lands the first DISPLAY_MOST that fit its
    # timing (a word too early, too close to another or too short to hold gives its place to the next), so a film
    # never loses its display words to timing. A long phrase is stacked (display_rows), never skipped for its length.
    omit = []
    devices = []                                         # the palette's asks: rows whose type performs (laid at the render, chain.alien_bank)
    # An attributed claim carries the reading on the ground below its source picture.
    fact_pages = []
    for a, b, page in timed_facts:
        facts = page["facts"]
        if page.get("sequence"):
            fact = facts[0]
            steps = page["sequence"]
            cue = [a] + [word_frame(step["at"]) for step in steps[1:]] + [b]
            for i,(start,end) in enumerate(zip(cue,cue[1:])):
                if start >= end:
                    continue
                wf1_display.reading_card(P, [start,end], "", fact["attribution"]+" · Scenario",
                                         fact["source"].get("publisher") or "", pal, centre=.605,
                                         steps=[(s["label"],j==i) for j,s in enumerate(steps)])
                P.events.append(start)
            fact_pages.append(dict(page, f=[a,b]))
            continue
        if opinion_byline(facts):
            # The synchronous voice and captions carry the qualified words. Only identify their owner.
            claim = facts[0]["chain"]
            byline = claim["speaker"] + " · " + wf1_display.OPINION_LABELS[claim["stance"]]
            voice = f"opinion_credit{a}"
            P.voices[voice] = dict(P.voices["cap"], px=P.R.fit_px("cap", byline, .82*W, 48), track=0)
            P.word([a,b], byline, voice, .5, cap_y - .09, pal["ink"], align="c", role="graphic", z=8,
                   stroke=dict(w=4, c=DARK_INK), contrast_shadow=True, fact_type=True)
            fact_pages.append(dict(page, f=[a,b]))
            continue
        if (a, b) in report_spans:
            for i, fact in enumerate(facts):
                claim = fact.get("chain") or {}
                label = wf1_display.CONTEXT_LABELS.get(claim.get("stance")) or wf1_display.ESTIMATE_LABELS.get(claim.get("stance")) or wf1_display.OPINION_LABELS.get(claim.get("stance"))
                speaker = fact.get("attribution") or ""
                source = fact["source"].get("publisher") or fact["source"].get("url") or ""
                body = str(fact["value"])
                statement = wf1_display.source_statement(fact, script.get("source_url"))
                excerpt = fact.get("excerpt")
                if excerpt:
                    body = "“" + excerpt["words"] + "”"
                    label = excerpt["speaker"]
                    date = fact["source"].get("publication_date")
                    if date:
                        from datetime import datetime
                        try:
                            date = datetime.strptime(date, "%Y-%m").strftime("%B %Y")
                        except (TypeError, ValueError):
                            pass
                    # The exact section stays in the source report. Keep a recording's time on screen.
                    location = excerpt["locator"] if claim and (not date or claim.get("event")==excerpt["event"]) else None
                    citation = " · ".join(dict.fromkeys(filter(None, (excerpt["event"], date, location))))
                elif statement:
                    body = statement
                    value = str(fact["value"])
                    date = fact.get("publication_date") or fact["source"].get("publication_date")
                    # Older cards carry a dated value. Keep it apart from the prose.
                    stamp = date or (value if not fact.get("statement") and value not in statement else "")
                    citation = " · ".join(dict.fromkeys(filter(None, (speaker, fact["source"].get("author"), source, stamp))))
                    speaker = ""
                    if fact.get("perspective"):
                        label = ""
                        citation = " · ".join(dict.fromkeys(s.get("publisher") or s["url"] for s in fact["supporting_sources"]))
                elif claim:
                    citation = " · ".join(filter(None, (speaker if label else None, source, fact.get("relation"))))
                else:
                    body = " ".join(filter(None, (fact.get("qualifier"), body, fact.get("relation"))))
                    citation = source
                span = [a + round((b - a) * k / len(facts)) for k in (i, i + 1)]
                if (a,b) in source_pages:
                    wf1_display.reading_card(P, span, body, label or speaker, citation, pal,
                                             centre=.44, max_height=.55)
                else:
                    wf1_display.reading_card(P, span, body, label or speaker, citation, pal)
            if (a,b) not in source_pages and not all(wf1_display.source_statement(f, script.get("source_url")) for f in facts):
                reading_spans.append((a, b))
                omit.extend(clock.words[page["first"]:page["last"] + 1])
            fact_pages.append(dict(page, f=[a, b]))
            P.events.append(a)
            continue
        rows = wf1_display.typeset(page["facts"], P.R, DISPLAY_W * W, cased)
        y = cap_y - .075
        seats = []
        for k in reversed(range(len(rows))):
            line, px, emphasis = rows[k]
            vk = f"dsp_fact{a}_{k}"
            P.voices[vk] = dict(P.voices["dsp"], px=px)
            seats.append((line, vk, y, emphasis))
            y -= (P.R.cap(vk) + 18) / H
        # The final seat can be on the dark page beside a bright paper. Measure contrast there at draw time.
        for line, vk, base, emphasis in reversed(seats):
            ink = pal["accent"] if emphasis else pal["ink"]
            land(a, b, line, vk, base, ink, stroke=DARK_INK, contrast_shadow=True, fact_type=True, text_group=f"fact{a}")
        fact_pages.append(dict(page, f=[a, b]))
        P.events.append(a)

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
        if b - a < 14 or on_evidence(a,b):
            continue
        if any(a < page["f"][1] and page["f"][0] < b for page in fact_pages):
            continue
        role = row.get("role") or "hook"
        if role == "name" and not row.get("perform"):
            # Captions retain the spoken name. The identifying super belongs to its picture.
            continue
        import palette_menu
        # A Director can name the same phrase in both its display list and its timed type ask.
        # Let that performance carry the display. The palette hands off the ordinary captions
        # on the frames where the performed words actually appear, including after a fallback.
        performed = next((ask for ask in pre if ask["dev"] in palette_menu.REPLACES
                          and ask["dev"] not in palette_menu.FIGURES and isinstance(ask.get("f"), list)
                          and ask["f"][:1] == [a]
                          and all(palette_menu.drawn(dict(dev=ask["dev"], args=ask), text))), None)
        # A relit phrase can span several display rows and start a frame
        # before the display. Its timed lettering owns the whole phrase.
        lamp = any(ask["dev"] == "lamp" and ask["at"][0] < b and a < ask["at"][1]
                   and [F.norm_word(w) for w in str(ask.get("t") or "").split()] ==
                       [F.norm_word(w) for w in text.split()] for ask in pre)
        if lamp or performed:
            shown.append((a, b, text))
            P.events.append(a)
            continue
        perf = row.get("perform") if isinstance(row.get("perform"), dict) else None
        if perf and perf.get("dev"):
            # the model chose the device that makes this row's type perform (palette_menu): the palette draws it on the
            # scene's word clock, ending by the row's window (b) or its sentence, never past the next subject
            limit = min([Clock.f(clock.segs[seg][1]) + 10] + next_subject)
            ask = palette_menu.ask_for(row, seg, text, a, b, len(devices), limit=limit)
            devices.append(ask)
            if perf["dev"] in palette_menu.REPLACES:
                # a device that draws the words itself takes the row's place and the caption words it draws; a word of
                # the row it does not draw stays in the caption, which then keeps its seat (the device seats itself off it)
                mask = palette_menu.drawn(ask, text)
                span = F.said_span(clock, seg, text)
                if all(mask):
                    omit += span
                else:
                    omit += palette_menu.omit_of(span, text, mask, F.norm_word)
                P.events.append(a)
                shown.append((a, b, text))
                continue
        spoken_a = a
        # If a stencil already performs this same word, show the display
        # phrase on its release. Its words and the original caption survive.
        tokens = {F.norm_word(w) for w in text.split()}
        for ask in pre:
            span = ask.get("f")
            if ask.get("dev") != "diecut" or not isinstance(span, list) or F.norm_word(str(ask.get("t", ""))) not in tokens:
                continue
            if a < span[1] and span[0] < b and f_end - span[1] >= 10:
                length = b - a
                a, b = span[1], min(f_end, span[1] + length)
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
        for k, (line, px) in enumerate(rows):
            vk = f"dsp{a}_{k}"
            line_ink, line_edge = (DARK_INK, pal["accent"]) if role == "contrast" and k == big else (ink, edge)
            plate = {"backplate": pal["accent"]} if role == "contrast" and k == big else {}
            land(a, b, line, vk, ys[k], line_ink, **plate, delay=0 if k == big else min(2, b - a - 1), stroke=line_edge, on=a)
        if a == spoken_a:
            omit += F.said_span(clock, seg, text)
        P.events.append(a)
        shown.append((a, b, text))
    shown.sort()
    for a, b, text, pic in labels:
        if b - a < 14 or on_evidence(a,b):
            continue
        if pic in document_pics:
            # The full byline remains in the picture credit. Wrap it at its reading size.
            wf1_display.reading_card(P, [a,b], "", "", text, pal, centre=.09, group="figure-credit")
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
        for n, r_ in enumerate(rows):
            P.word([a, b], r_, "lab", 0.5, y + 0.036 * n, pal["ink"], 0.95, align="c", z=7, role="stat", name_picture=pic,
                   text_group=f"picture-credit-{pic}-{a}")
    lines = F.caption_build(P, clock, "cap", cap_y, f_end=min(f_voice_end + 8, f_end), z=5,
                            hold=reading_spans, omit=omit, phrase_ends=d.get("caption_ends") or (),
                            accents=d.get("caption_accents") or (), accent_ink=pal["accent"])
    for word in P.items:
        if word.get("role") == "cap":
            word["chunk"] = f'caption-{word["f"][1]}'
    P.items = inside(P.items, f_end)
    P.items = ink_by_ground(P.items, P.voices, bright)
    film = dict(grain=float(st["grain"]), vignette=float(st["vignette"]), over_type=False, grade=dict(lift=0, gamma=1, gain=1, sat=1, contrast=1, temp=0, tint=0))
    l1 = P.done(sup.get("sid", "wf1"), f_end, int(d.get("seed", 1114)), film)
    # The pace of each section is its energy (the Director's): a strobe section's pictures and framings are cut down to
    # two frames, a hold section's held up to about four seconds; brisk sections take the film's own band below.
    bands = []
    if secs:
        DR = _director()
        pace_, _, _ = F._engines()
        groups = {}
        take_keys = {pc["key"] for pc in pieces}
        for it in l1["items"]:
            if it["k"] == "shot" and it["id"] in drive_ids:
                en = (section_at(it["f"][0]) or {}).get("energy") or "brisk"
                if en in ("strobe", "hold"):
                    groups.setdefault((en, it.get("pic") in take_keys), set()).add(it["id"])
                    if any(a_ <= it["f"][0] < b_ for a_, b_ in mono_spans):
                        it["look"] = "mono_silver"           # the section's black and white, before its framings are cut
        for en, is_take in sorted(groups):
            ids = groups[(en, is_take)]
            try:
                l1 = pace_.drive_l1(l1, sup.get("work") or "/", stress, meta=meta, family=d.get("family") or "podcast",
                                    only=ids, band=DR.energy_band(en, d["band"], take=is_take))
                bands.append(dict(energy=en, take=is_take, shots=len(ids),
                                  **{k: v for k, v in (l1.get("pace") or {}).items() if k in ("hold_median", "hold_max", "eased", "punches", "whips")}))
                drive_ids -= ids
            except Exception as e:  # noqa: BLE001 - a section the driver cannot cut keeps the film's own band
                bands.append(dict(energy=en, take=is_take, fault=repr(e)[:200]))
    # the film's word clock, for the palette's devices: each word on the frame a display word lands on it, and its sentence
    l1["said"] = said
    # the Director's story beats for the sound's hero hits (worker/score/sound_screen.py): a beat it held (energy hold)
    # lands a short dry hit, not a trailer impact (lane sfx-moments, 20 Nov: an impact on a suicide line read wrong)
    l1["sound_beats"] = [dict(f=int(word_frame(b["w"])), role=b.get("role"), energy=b.get("energy"))
                         for b in (dr.get("beats") or []) if isinstance(b, dict) and isinstance(b.get("w"), int)
                         and b.get("role") in ("hook", "turn", "payoff", "close") and word_frame(b["w"]) is not None]
    l1["workflow_text_layout"] = True
    l1["picture_bound_sound"] = True
    l1["story_cues"] = (d["retained_story_cues"] if "retained_story_cues" in d
                        else _director().sound_cues(dr, word_frame, picture_keys={pid: keys.get(path, path) for pid,path in (dr.get("pictures") or {}).items()}))
    if devices:
        l1["devices"] = devices
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
    l1["speaker_layout"] = {pc["key"]: meta[pc["key"]] for pc in pieces if meta[pc["key"]]["faces"]}
    # A name super on the interview follows both the audible turn and the visible
    # camera asset. The palette resolves actual picture spans after its other cuts.
    turns = script.get("speaking_turns") or d.get("claims") or []
    credits = []
    for turn in turns:
        if not (turn.get("speaker") and all(type(turn.get(k)) in (int,float) for k in ("start","end"))):
            continue
        first, last = [round((turn[k]-script.get("source_start",0))*FPS)+LEAD_F for k in ("start","end")]
        for pc in pieces:
            a, b = max(first, pc["f"][0]), min(last, pc["f"][1])
            if a < b and pc["who"] == "speaker":
                credits.append(dict(f=[a,b], pic=pc["key"], name_super=turn["speaker"],
                                    source_evidence=dict(url=script.get("source_url"), time_s=turn["start"])))
    l1["interview_credits"] = credits
    title_layout = seat_display(l1, meta, P.R)
    caps = [i for i in l1["items"] if i["k"] == "word" and i.get("role") == "cap"]
    if caps:
        # The final renderer measures contrast beneath the actual positioned glyphs.
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
    # The Director's asks, in programme frames, for the palette to lay at the render (chain.lay_devices): every device
    # the engine can draw, the alien bank's included, each landing or falling back to its next device
    notes, asks = [], []
    if dr:
        DR = _director()
        # a picture the film placed is its scene asset; one it did not is its path, which the palette reads from the pool
        by_id = {pid: keys.get(path) or path for pid, path in (dr.get("pictures") or {}).items()}
        asks, notes = DR.palette_asks(dr, word_frame, seg_span, by_id.get, f_end)
        if asks:
            l1["devices"] = list(l1.get("devices") or []) + asks        # after the rows whose type performs (perform)
    why = notes
    changes = sorted({it["f"][0] for it in l1["items"] if it["k"] == "shot"})
    for s_ in l1.get("sound") or []:
        P.events.append(int(s_["event_f"]))
    cover = sum(x[1] - x[0] for x in ins) / max(f_voice_end, 1)
    look_f = sorted({0, 12, 40, *[x[0] + 5 for x in ins], *[x[1] + 8 for x in ins[::2]], *[a + 10 for a, _, _ in shown],
                     *([turn_f + 10] if turn_f is not None else []), f_end - 3})
    dev_events = _director().device_events(asks) if asks else []
    beats = dict(dur_f=f_end, voice_f=[LEAD_F, f_voice_end], cuts=sorted(set(cut_pts)), changes=changes,
                 events=sorted(set(P.events) | set(cut_pts) | set(changes) | {it["f"][0] for it in l1["items"] if it["k"] == "card"}
                               | {e["f"] for e in dev_events if 0 <= e["f"] < f_end}),
                 device_events=dev_events,
                 pictures=P.pictures, on_screen=P.shown, caption_y=cap_y, caption_lines=lines, caption_contract=CAPTION_CONTRACT, display=[dict(f=[a, b], text=t) for a, b, t in shown],
                 display_facts=fact_pages,
                 display_count=dict(proposed=len(proposed), landed=len(shown)),
                 labels=[dict(f=[a, b], text=t, pic=pic) for a, b, t, pic in labels], turn_f=turn_f, story_turn_f=story_turn_f, pace=l1.get("pace"), cutaway_share=round(cover, 3),
                 style=style_id, layout=(sorted(set(layouts_used)) if dr and len(set(layouts_used)) > 1 else (layouts_used[0] if dr and layouts_used else st["layout"])), placement_check=placement_report, coverage=coverage_report,
                 title_layout=title_layout, caption_restarts=restarts, face_metadata=meta,
                 finishes=dict(black_and_white=len([p for p in P.pictures if p.get("finish")]), flashes=len(flashes), dark_ink_spans=len(bright),
                               take_past=[list(sp) for sp in take_past], past_share=round(past_share, 3),
                               balanced=sum(1 for it in l1["items"] if it.get("k") == "shot" and it.get("grade") and it.get("pic") not in {pc["key"] for pc in pieces})),
                 devices=dict(by=dr.get("by"), asked=[a["dev"] for a in asks], notes=why, layouts=sorted(set(layouts_used)),
                              sections=[dict(f=[a_, b_], words=[s_.get("start"), s_.get("end")], surface=s_.get("surface"),
                                             layout=s_.get("layout"), energy=s_.get("energy")) for a_, b_, s_ in secs], bands=bands),
                 alien=dict(asked=[a["dev"] for a in asks if a["dev"] in ("rhyme", "diecut", "lamp", "arc", "answer")]),
                 perform=[dict(dev=x["dev"], beat=x.get("beat"), at=x.get("at")) for x in devices],
                 sound_design=dict(l1.get("sound_design") or {}),       # with its dropped list: a refusal is measured, never guessed
                 look=[f for f in look_f if 0 <= f < f_end])
    return l1, beats
