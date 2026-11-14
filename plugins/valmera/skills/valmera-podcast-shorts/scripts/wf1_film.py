"""Workflow 1's films: a podcast moment as a film of what is said, on this week's engines, in one of four styles.

The speaker's take is the spine and the sound. Over the sentences that name real people, companies, places and
events the picture goes to real footage and photographs of them (worker/footage, Commons), a new one about every
second, then back to him; the payoff is his face. Captions ink word by word at one seat; the title holds that seat
over his first breath; the words that carry the story land large on the words that say them; a named thing is
labelled once. Sentences about the past are drawn in black and white (a finish by section, never film-wide, except
in the style whose whole idea it is). Then the engines: the judge's pace and moves (scene.pace), one look solved on
the film's own pictures (scene.looks), a soft first frame on one new picture in three, and the references' sounds on
what lands (score.sound). The film's music is laid after the render by the shell.

What differs between the styles is how a found picture is shown and the film's type and colour:

  footage-cut    the picture fills the phone, in colour; yellow accent, heavy grotesk display words
  archive-reel   the picture fills the phone in black and white with heavy grain, a projector flash into each run;
                 cream serif italic display words
  window         the picture plays in a framed window over itself out of focus (three layers); cyan accent,
                 condensed capitals
  prints         pictures land as tilted prints that stack beside and over each other on the picture's own dark,
                 out-of-focus ground (four layers and more by the third); orange accent, high-contrast serif

No model is read here. Which words are displayed, which sentences speak of the past and which years the story
speaks from are handed in by the caller (wf1_short.py: a model that read the sentences).
"""
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

WINDOW_BOX = (0.04, 0.205, 0.96, 0.593)        # four by three, the width of the phone, between the mark and the caption seat
PRINT_W = 0.62                                 # a square print, 670 px: small enough that the one under it shows
PRINT_ROT = (-2.6, 1.9, -1.2, 2.4)
PRINT_AT = ((0.36, 0.392), (0.64, 0.452), (0.385, 0.458), (0.615, 0.388))      # where prints land, in turn (frame fractions)
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


def fill_gaps(ins, footage, pics, script, seg_f, f_first, f_last, stress, gap_s=5.0, hold_s=1.2, rest_s=2.0):
    """A stretch of the take longer than gap_s with no picture over it is a held framing. It takes the pictures the
    film found and has not shown (clips first, those found for the nearest sentence first), one about every three
    seconds, each landing on the voice's stress, with the take between them. Nothing is shown twice: when the film
    has no picture to spare the take stays, eased by the pacing driver. Returns the cutaways, in order."""
    shown = {x[4]["path"] for x in ins}
    spare = [(i, "clip", c.get("subject"), c) for i, lst in footage.items() for c in lst if c["path"] not in shown]
    for n, lst in pics.items():
        lines = [i for i, s in enumerate(script["segments"]) if n in (s.get("pictures") or [])] or [0]
        spare += [(lines[0], "photo", n, r) for r in lst if r["path"] not in shown]
    if not spare:
        return ins
    hold, rest, out = int(hold_s * FPS), int(rest_s * FPS), list(ins)
    edges = [f_first] + [f for x in sorted(ins) for f in (x[0], x[1])] + [f_last]
    for a, b in zip(edges[0::2], edges[1::2]):
        if b - a <= gap_s * FPS:
            continue
        seg = max([i for i in range(len(seg_f)) if seg_f[i] <= (a + b) // 2] or [0])
        pos = a + rest
        while spare and b - pos >= hold + rest:
            spare.sort(key=lambda r: (abs(r[0] - seg), r[1] != "clip"))
            i, kind, name, rec = spare.pop(0)
            near = [(st, -f) for f, st in stress if pos - 5 <= f <= pos + 8]
            f0 = -max(near)[1] if near else pos
            n = min(hold, int(float(rec["seconds"]) * FPS) - 1) if kind == "clip" else hold
            if n < 16:
                continue
            if kind == "clip":
                t0 = max((float(rec["seconds"]) - n / FPS) / 2, 0.0)
                rec = dict(rec, src=[round(t0, 3), round(t0 + n / FPS, 3)])
            out.append([f0, f0 + n, kind, name, rec])
            pos = f0 + n + rest + hold // 2
    return sorted(out, key=lambda x: x[0])


def _runs(ins):
    """Cutaways that follow each other without the take between them, as lists of indexes."""
    out = []
    for k, x in enumerate(ins):
        if out and ins[out[-1][-1]][1] == x[0]:
            out[-1].append(k)
        else:
            out.append([k])
    return out


def build(script, sup, style_id, direction=None):
    """(l1, beats) for one short. script: {"title", "segments": [{"say", "pictures": [names]}]}; sup: the pack
    (wf1_short.supply); direction: {"display": [{"seg", "text"}], "past_segs": [i], "turn_seg": i, "arc": [...]}."""
    st = STYLES[style_id]
    d = dict(aim_s=st["aim_s"], look=st["look"], family="story", share=st["share"], bed=True, title=True,
             band=dict(hold=(0.34, 0.62), never=2.8, ease_takes=False, whip=0.04, ease=0.85, shutter=0.6))
    d.update(direction or {})
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
    pics = {k: [r for r in v if r.get("title") not in drop and os.path.basename(str(r.get("path"))) not in drop] for k, v in (sup.get("pictures") or {}).items()}
    pics = {k: v for k, v in pics.items() if v}
    footage = {int(ln["i"]): [c for c in ln.get("clips") or [] if os.path.basename(str(c.get("file") or c.get("path"))) not in drop
                              and os.path.exists(str(c.get("path"))) and not c.get("dull")] for ln in sup.get("footage") or []}
    take = sup["take"]
    keys, meta = {}, {}
    # the take's own shots (a show cut between cameras): each is framed on its own speaker. One file, one asset a shot
    tshots = [s_ for s_ in take.get("cuts") or [] if float(s_["t1"]) - float(s_["t0"]) > 0.2] or [dict(t0=0.0, t1=float(take["dur"]), faces=take.get("faces") or [])]
    tkeys = []
    for k, s_ in enumerate(tshots):
        key = P.video("main" if k == 0 else f"own:{k}", dict(path=take["path"], w=take["w"], h=take["h"], fps=take.get("fps") or FPS, seconds=take["dur"]))
        fm = F.face_meta(dict(faces=s_.get("faces") or take.get("faces") or []))
        if fm:                                           # a speaker's head, not a face-shaped thing on the shelf behind him
            tall = max(f[3] for f in fm)
            fm = [f for f in fm if f[3] >= 0.5 * tall]
        meta[key] = dict(pw=int(take["w"]), ph=int(take["h"]), faces=fm, max_up=float(d.get("take_up", 2.4)))
        tkeys.append(key)
    for lst in pics.values():
        for rec in lst:
            if rec["path"] not in keys:
                keys[rec["path"]] = P.image(f"p{len(keys):02d}", rec)
    for lst in footage.values():
        for c in lst:
            if c["path"] not in keys:
                keys[c["path"]] = P.video(f"v{len(keys):02d}", c)
    if st["layout"] == "bleed":
        seat = float(st["cap_y"]) - 0.045

        def under_seat(c):
            return int(c.get("w") or W) <= int(c.get("h") or H) and any(f["box"][3] > seat and f["box"][3] - f["box"][1] > 0.3 for f in c.get("faces") or [])
        for i, lst in footage.items():
            ok = [c for c in lst if not under_seat(c)]
            if ok or any(pics.get(n) for n in (script["segments"][i].get("pictures") or []) if i < len(script["segments"])):
                footage[i] = ok
    f_voice_end = Clock.f(clock.dur)
    f_end = min(f_voice_end + 8, int((float(take["dur"]) + LEAD_F / FPS) * FPS))      # out on his last word: the end card follows
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
    # the payoff is his face, but never more than its last four seconds: a long close on one framing is a held picture
    f_last = max(turn_f - 6, f_voice_end - int(4.0 * FPS)) if turn_f is not None else f_voice_end - 45
    from placement_check import propose, check
    ins = propose(script, clock, footage, pics, int(0.6 * FPS), f_voice_end - 24, stress)
    if st["layout"] == "prints":
        # A retained print is still visible after the next one lands. Check
        # everything said during that visible lifetime, not only its entrance.
        for run in _runs(ins):
            end = ins[run[-1]][1]
            for k in run:
                ins[k][4] = dict(ins[k][4], audit_until=end)
    ins, placement_report = check(ins, script, clock, sup["work"], sup["ffmpeg"])
    past = {int(i) for i in d.get("past_segs") or [] if isinstance(i, int)}
    cut_pts, drive_ids, look_pics, mono_spans, bright = [], set(), [], [], []
    if take.get("still"):
        look_pics.append(take["still"])
    tcuts = [int(round(float(s_["t0"]) * FPS)) + LEAD_F for s_ in tshots[1:]]      # the take's own cuts, in film frames

    take_phrase = 0

    def take_shot(a, b):
        """The take on frames a..b, split at its own cuts so every piece is framed on the speaker it shows."""
        nonlocal take_phrase
        if b - a < 2:
            return
        # Break long takes into phrase-sized pieces before the face-safe driver.
        # The driver may have only two valid framings; a later phrase may return
        # to the wider shot while the source sound and time stay continuous.
        pace, _, _ = F._engines()
        phrase_band = (0.55, 0.85) if st["layout"] == "window" else (0.75, 1.15)
        phrase = pace.splits(a, b, stress, int(phrase_band[0] * FPS), int(phrase_band[1] * FPS))
        edges = sorted({a, b, *phrase, *[c for c in tcuts if a + 3 <= c <= b - 3]})
        for fa, fb in zip(edges, edges[1:]):
            mid = (fa + fb) / 2
            k = sum(1 for c in tcuts if c <= mid)
            t0 = max(fa - LEAD_F, 0) / FPS
            t1 = min(max(fb - LEAD_F, 1) / FPS, float(take["dur"]))
            src = [round(t0, 4), round(max(t1, t0 + 1 / FPS), 4)]
            if st["layout"] == "window" and take_phrase % 2:
                # A window film needs a genuinely wider view between its close
                # framings. Small face-safe crop jumps can be visually identical
                # after the camera move; preserve the source's complete frame.
                P.shot([fa, fb], tkeys[k], F.cover_crop(take["w"], take["h"], W/H,
                       (.5,.45),(.5,.4)), src=src, blur=26, grade=dict(DIM))
                x0,y0,x1,y1 = WINDOW_BOX
                aspect = float(take["w"])/float(take["h"])
                h = min(y1-y0,(x1-x0)*W/H/aspect)
                w = h*H/W*aspect
                cx,cy = (x0+x1)/2,(y0+y1)/2
                P.card([fa,fb],tkeys[k],[cx-w/2,cy-h/2,cx+w/2,cy+h/2],
                       src=src,crop=[0,0,1,1],z=1,radius=.010,
                       shadow=dict(a=.55,blur=.03,dx=0,dy=.012))
            else:
                it = P.shot([fa, fb], tkeys[k], None, src=src)
                it.pop("crop")
                drive_ids.add(it["id"])
            take_phrase += 1
            cut_pts.append(fa)

    past_share = sum(x[1] - x[0] for x in ins if seg_at(x[0]) in past) / max(sum(x[1] - x[0] for x in ins), 1)
    by_section = 0.0 < past_share <= 0.6                 # a finish for a section of the story: a story that is all past keeps its colour

    def is_mono(f0):
        return st["mono"] == "all" or (st["mono"] == "past" and by_section and seg_at(f0) in past)

    def note(f0, f1, kind, name, rec, mode):
        P.pictures.append(dict(f=[f0, f1], visible_f=[f0, rec.get("audit_until", f1)], kind=kind, name=name, path=rec["path"], shows=rec.get("shows"), mode=mode,
                               finish="black and white" if is_mono(f0) else None,
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
        if kind == "clip" and int(rec.get("w") or W) > int(rec.get("h") or H):
            return window(f0, f1, kind, name, rec, key)          # footage kept wide is never blown up to fill the phone
        if kind == "clip":
            it = P.shot([f0, f1], key, None, src=list(rec["src"]))
            it.pop("crop")
            fm = F.face_meta(rec)
            meta[key] = dict(pw=int(rec.get("w") or W), ph=int(rec.get("h") or H), faces=fm, max_up=1.0 if any(f[3] >= 0.24 for f in fm) else 1.12)
        else:
            (fx, fy), _ = F.focus_of(rec)
            it = P.shot([f0, f1], key, F.cover_crop(rec["w"], rec["h"], W / H, (fx, fy), (0.5, 0.40)))
            if key not in meta:
                m = dict(pw=int(rec["w"]), ph=int(rec["h"]), faces=[f for f in F.face_meta(rec) if f[3] * rec["h"] >= 60], max_up=F.MAX_UP)
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
        """A run of pictures as prints: each lands tilted, beside and over the one before, and stays to the run's end.
        The ground under them is the newest print's own picture, far out of focus and dark."""
        r1 = ins[run[-1]][1]
        hw, hh = PRINT_W / 2, PRINT_W * W / H / 2
        for q, k in enumerate(run):
            f0, f1, kind, name, rec = ins[k]
            key = keys[rec["path"]]
            mono = is_mono(f0)
            pw_, ph_ = float(rec.get("w") or W), float(rec.get("h") or H)
            bed = dict(blur=34, grade=dict(BED))
            if kind == "clip":
                bed["src"] = list(rec["src"])
            if mono:
                bed["look"] = "mono_silver"
            P.shot([f0, f1], key, F.cover_crop(pw_, ph_, W / H, (0.5, 0.45), (0.5, 0.40)), **bed)
            n, m = f1 - f0, r1 - f1
            cx, cy = PRINT_AT[q % len(PRINT_AT)]
            aspect = pw_/ph_
            hh = min(0.17, PRINT_W*W/H/aspect/2)
            hw = hh*H/W*aspect
            box = [cx - hw, cy - hh, cx + hw, cy + hh]
            land = [r4(1.0 + 0.10 * (1 - smooth(j / 6.0))) if j < 7 else r4(1.0 + 0.018 * (j - 7) / max(n - 8, 1)) for j in range(n)]
            kw = dict(z=1 + q, rot=PRINT_ROT[q % len(PRINT_ROT)], crop=[0.0, 0.0, 1.0, 1.0], radius=0.006,
                      shadow=dict(a=0.62, blur=0.03, dx=0.004, dy=0.014))
            if mono:
                kw["look"] = "mono_silver"
            live = dict(kw, s=land, op=[min(1.0, 0.4 + 0.3 * j) for j in range(n)])
            if kind == "clip":
                live["src"] = list(rec["src"])
            P.card([f0, f1], key, box, **live)
            if m > 0:                                         # under the next print it holds its last frame, as a print does
                rest = dict(kw, s=land[-1])
                if kind == "clip":
                    rest["src"] = [round(max(rec["src"][1] - 1.0 / FPS, 0.0), 4), round(rec["src"][1], 4)]
                P.card([f1, r1], key, box, **rest)
            note(f0, f1, kind, name, rec, "print")

    pos, labelled, labels, flashes = 0, set(), [], []
    runs = _runs(ins)
    for run in runs:
        r0, r1 = ins[run[0]][0], ins[run[-1]][1]
        take_shot(pos, r0)
        if st["layout"] == "prints":
            prints(run)
        for k in run:
            f0, f1, kind, name, rec = ins[k]
            if st["layout"] == "bleed":
                bleed(f0, f1, kind, name, rec, keys[rec["path"]])
            elif st["layout"] == "window":
                window(f0, f1, kind, name, rec, keys[rec["path"]])
            cut_pts.append(f0)
            P.events.append(f0)
            if d.get("labels", False) and name not in labelled:                         # a named thing is labelled once, small, while its own pictures are up
                labelled.add(name)
                b_lab, j = f1, k + 1
                while j < len(ins) and ins[j][0] == b_lab and ins[j][3] == name:
                    b_lab, j = ins[j][1], j + 1
                labels.append((f0 + 2, min(b_lab, f0 + int(2.4 * FPS)), str(name)))
        if st["flash"] == "runs" and (not flashes or r0 - flashes[-1] >= 4 * FPS):      # the projector catches: into a run of archive
            flashes.append(r0)
        pos = r1
    take_shot(pos, f_end)
    if st["flash"] == "turn" and turn_f is not None and ins and 0 <= turn_f - ins[-1][1] <= FPS:
        flashes.append(ins[-1][1])                           # the cut back to his face for the payoff
    for f in flashes:
        if 0 < f < f_end - 3:
            P.flash([f, f + 2], mode="fill", c="#FFFFFF", a=[0.62, 0.22])
            P.events.append(f)
    # ---- the title holds the caption seat over his first breath; the captions yield to it
    _, _, bake = F._engines()
    shown = []
    title = str(script.get("title") or "")

    def cased(t):
        return t.upper() if st["upper"] else t

    def land(a, b, text, vk, y, ink, delay=0):
        n = b - a
        kk = min(int(bake.BOUNCE["n"]), n)
        ys = [r4(y + float(q)) for q in bake.bounce(kk, rest=0.0)] + [r4(y)] * (n - kk)
        bl = [round(float(q), 3) for q in bake.bounce_blur(kk)] + [0.0] * (n - kk)
        P.word([a + delay, b], text, vk, 0.5, ys[delay:] if delay else ys, ink, [min(1.0, (q + 1) / 2) for q in range(n - delay)],
               align="c", z=8, blur=bl[delay:] if delay else bl, role="big", stroke=dict(w=3.0, c="#0B0A09"))

    if d.get("title") and title:
        ws = cased(title).split()
        cut = min(range(1, len(ws)), key=lambda k: abs(len(" ".join(ws[:k])) - len(" ".join(ws[k:])))) if len(title) > 16 and len(ws) > 1 else len(ws)
        rows = [" ".join(ws[:cut])] + ([" ".join(ws[cut:])] if cut < len(ws) else [])
        a, b = 2, int(2.3 * FPS)
        px = min([P.R.fit_px("dsp", r, 0.70 * W, 132) for r in rows])      # inside the platform's right rail, as every display word
        for k, row in enumerate(rows):
            vk = f"dsp_t{k}"
            P.voices[vk] = dict(P.voices["dsp"], px=px)
            land(a, b, row, vk, cap_y + 0.012 - (len(rows) - 1 - k) * px * 1.04 / H, pal["accent"] if k == len(rows) - 1 else pal["ink"], delay=2 * k)
        shown.append((a, b, title))
        P.events.append(a)
    # ---- the words that carry the story, large, each on the word that says it; never two inside two seconds
    last = shown[-1][1] + 6 if shown else -10 ** 6
    for row in d.get("display") or []:
        try:
            seg, text = int(row["seg"]), " ".join(str(row["text"]).split())
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 <= seg < nseg) or not text or len(text) > 22 or any(text.lower() in t.lower() or t.lower() in text.lower() for _, _, t in shown):
            continue
        hit = F.said_at(clock, seg, text)
        if hit is None:
            continue
        a = max(Clock.f(hit[0]) - 1, 1)
        if a - last < 2.0 * FPS or a >= f_voice_end - 12:
            continue
        b = int(min(a + 1.15 * FPS, f_end, Clock.f(clock.segs[seg][1]) + 10))
        if b - a < 14:
            continue
        vk = f"dsp{a}"
        P.voices[vk] = dict(P.voices["dsp"])
        P.voices[vk]["px"] = P.R.fit_px(vk, cased(text), 0.70 * W, float(st["dsp"]["px"]) * 1.45)
        land(a, b, cased(text), vk, cap_y + 0.012, pal["accent"] if any(ch.isdigit() for ch in text) or row.get("accent") else pal["ink"])
        P.events.append(a)
        shown.append((a, b, text))
        last = a
    for a, b, text in labels:
        if b - a < 14:
            continue
        t = text.upper()
        wd = P.R.width("lab", t) / W
        if wd > 0.66:
            continue
        y = 0.752
        P.block([a, b], [0.5 - wd / 2 - 0.022, y - 0.030, 0.5 + wd / 2 + 0.022, y + 0.013], pal["plate"], z=6, op=0.78, radius=0.008)
        P.word([a, b], t, "lab", 0.5, y, pal["ink"], 0.95, align="c", z=7, role="stat")
    lines = F.caption_build(P, clock, "cap", cap_y, f_end=min(f_voice_end + 8, f_end), hold=[(a - 1, b) for a, b, _ in shown], z=5)
    P.items = inside(P.items, f_end)
    P.items = ink_by_ground(P.items, P.voices, bright)
    edges = sorted({0, f_end, *[f for s0, s1 in bright for f in (s0, s1) if 0 < f < f_end]})
    for fa, fb in zip(edges, edges[1:]):                 # the pool of shade under the seat, except where the ink is dark
        if fb - fa >= 1 and not any(s0 <= fa < s1 for s0, s1 in bright):
            P.flash([fa, fb], mode="pool", a=float(d.get("pool", 0.42)), rect=[0.06, cap_y - 0.105, 0.94, cap_y + 0.075], z=-1)
    film = dict(grain=float(st["grain"]), vignette=float(st["vignette"]), over_type=False, grade=dict(lift=0, gamma=1, gain=1, sat=1, contrast=1, temp=0, tint=0))
    l1 = P.done(sup.get("sid", "wf1"), f_end, int(d.get("seed", 1114)), film)
    l1 = F.engines_finish(l1, sup, stress, drive_ids, meta, dict(d, look_pictures=look_pics), turn_f=turn_f, mono=mono_spans,
                          clear=[0.10, cap_y - 0.085, 0.90, cap_y + 0.02],
                          spoken=[(w["t0"] - LEAD_F / FPS, w["t1"] - LEAD_F / FPS) for w in clock.words])

    from wf1_layout import seat_display, caption_restarts
    # Use the final camera crops, not the source face coordinates.
    for path, key in keys.items():
        if key not in meta:
            rec = next((r for rows in list(pics.values()) + list(footage.values()) for r in rows if r["path"] == path), {})
            meta[key] = dict(faces=F.face_meta(rec))
    title_layout = seat_display(l1, meta, P.R)
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
                 pictures=P.pictures, on_screen=P.shown, caption_y=cap_y, caption_lines=lines, display=[dict(f=[a, b], text=t) for a, b, t in shown],
                 labels=[dict(f=[a, b], text=t) for a, b, t in labels], turn_f=turn_f, pace=l1.get("pace"), cutaway_share=round(cover, 3),
                 style=style_id, layout=st["layout"], placement_check=placement_report,
                 title_layout=title_layout, caption_restarts=restarts, face_metadata=meta,
                 finishes=dict(black_and_white=len([p for p in P.pictures if p.get("finish")]), flashes=len(flashes), dark_ink_spans=len(bright)),
                 alien=dict(asked=[a["dev"] for a in l1.get("alien") or []], why=why, never=list(ALIEN_NEVER)),
                 sound_design={k: v for k, v in (l1.get("sound_design") or {}).items() if k != "dropped"},
                 look=[f for f in look_f if 0 <= f < f_end])
    return l1, beats
