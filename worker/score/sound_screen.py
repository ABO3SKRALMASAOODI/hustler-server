"""Screen placement: every sound answers what the picture does (lane sfx, 20 Nov 2026). Opt-in.

The owner, 20 Nov, on the night's workflow films: the SFX were "weird sounds ... not related at all", they "need to be
high quality". Measured on those films (the night's SFX map): the class of a sound came from which class the film
had used least against the references' shares, not from the picture; "no recording twice in a film" pushed the
choice down to the library's fragments (a clock-tick loop in a whoosh slot, a waterphone as the only hit); a clip
whose subject moved made its plain cut a "moving" cut; every display word took a different tick timbre; and ticks
the K-weighted level ruler called quiet stood 10-31 dB over the voice in the 4-12 kHz band.

This module is the other way, for the workflow films (VALMERA_SFX_PLACEMENT=screen, set with the workflow's own
sound set, VALMERA_SOUNDS, by the workflow shell; a customer's film never sets it and keeps score/sound.py as it is):

  character  the class comes from the picture: a picture or a camera that travels gets a sweep whose length is the
             move's own length and whose brightness follows its speed; a card or a word that lands hard gets a hit; a
             small graphic or a line of type a click; a still photograph its shutter, a document its paper, a glitched
             flash its glitch (an object recording answers only the picture that shows the object); the hook, the turn
             and the close an impact; a riser only climbs into the turn or the close, and never into a turn the song's
             own build and drop already carry (a film with a bed).
  palette    each film draws a small palette, as an editor does: up to three sweeps, two hits, two impacts, one riser
             and two clicks, reused with spacing (the same recording never inside SAME_GAP_S), so a film has one tick
             voice and one family of sweeps instead of twenty timbres.
  pitch      the song is laid after the render, so its key is unknown here: a strongly tonal recording in the mid
             band (its five strongest partials hold most of its energy, the strongest over 150 Hz) gives way to a
             noise-shaped one.
  restraint  each sound's 2-8 kHz level (the set's measured hf_db plus its gain) stays under the voice's own 2-8 kHz
             level by its tier (HF_OVER), so a click or a shutter is heard as a detail, never as a spike over the voice.

Review round, 20 Nov: a cut is travel only when its travel is measured at its own frame (the stories path flags a
clip's inner cut `moving` by its subject's motion, which put a whoosh on plain cuts); the palette's voices rotate for
real (the voice heard nearest in time gives way, and one recording plays at most about twice in 30 s while another
voice of its class fits); a hero impact is chosen for what a phone plays (its 200 Hz-8 kHz body, the hook by its
attack's) and weight under 150 Hz is a bonus only on top of that; a glitched flash (a treated patch, a mosaic, an
on-off-on flicker) and type being typed (single characters revealed left to right) are read from the plan, so the
glitch and typing recordings can answer them; and the 2-8 kHz restraint is applied again after the song's lift
(score.sound.present_over, score.music), so it is the final level.

Moments round, 20 Nov (lane sfx-moments; independent raters on the ten Cuban shorts: of 13-21 sounds a film about 4
were audible, "most sit under speech", the hook, the turn and the payoff were dry in half the films, whooshes rode
in-shot camera drifts, and nothing sounded the paper or the money a picture showed). Fewer sounds, each heard:
  moments    the hook's landing, the turn and the close are found among every landing on screen (a new picture on
             the story's beat sounds even when it is a plain cut): the hook on an opening landing in a breath first, the
             turn SNAP_S either side of its mark, the close on the song's last hit; each hero tries HERO_TRIES
             recordings and keeps the one heard best (an impact whose body sits in the speech band is held under the
             word and lost); the rest of the film
             ranks its moments (matters): an object the picture shows (paper, or the family a model named for the
             picture, asset `foley`), a glitch, typing, a hard landing, a cut that really travels (a whip, a card's
             slide, a crop moving at WHIP_V of a whip), then type. An in-shot camera move sounds only as a whip.
  register   a hero on a beat the Director held (its energy `hold`, scene `sound_beats`) lands a short dry hit at an
             accent's level, never a trailer impact: a model chose the beat's weight, the signal sets the level.
  amount     RATE_10S sounds a 10 s at most besides the hook, the turn and the close (which always land), and no quiet
             tick layer: what is placed is meant to be heard.
  heard      each hit is levelled on the judge's own ruler (TEARDOWN 9: a 50 ms K-weighted peak against the voice's
             400 ms level; the references' audible sounds sit at about the voice's level, "as loud as a stressed
             word"), read at the mix rate in stereo as it plays: LEVEL by tier, raised up to LEVEL_TOP while its own
             loudest 50 ms stands under HEARD_AIM over the median of what plays around it (the voice, and the song once
             it is known), never into the speech band (the voice's own masking check; a hero's attack may sit at the
             syllable it lands on, HERO_MASK_DB) and never a 2-8 kHz spike (HF_OVER over the voice's own top band). A
             sound the voice still hides gives its place to the next moment; a hero keeps its landing and is raised to
             its ceiling. The ceilings are applied again after any later lift (restrain), so they are the final level.
"""
import hashlib
import math
import os

import numpy as np

MODE_ENV = "VALMERA_SFX_PLACEMENT"
PALETTE = {"whoosh": 3, "hit": 2, "impact": 2, "riser": 1, "click": 2}
SAME_GAP_S = 2.5            # one recording again only this far from its last use
MOVE_STILL = 0.004          # score.sound.MOVE_STILL: under this a frame does not move
FAST_V = 0.05               # a crop moving this share of the frame in one frame is a whip
HF_BAND = (2000.0, 8000.0)
# dB against the voice's own 2-8 kHz level (p90): a spike ceiling, not a level. Under it a sound at the references' level
# is heard; the old values (hero +3, accent 0, body -3, tick -6) held every sound under the voice's top band, and the
# raters measured 9-17 of a film's 13-21 sounds under the voice (lane sfx-moments, 20 Nov).
HF_OVER = {"hero": 12.0, "accent": 10.0, "body": 8.0, "tick": 4.0}
TONAL = 0.45                # five partials holding this share of the energy: a pitched recording
GAIN_MIN = -36.0
GAIN_MAX = -2.0             # score.sound.GAIN_MAX

# moments and amount (lane sfx-moments)
RATE_10S = 2.0              # sounds a 10 s at most besides the hook, the turn and the close (the judge's median, 3.6 audible
                            # a 10 s, counts those three and the sounds under the bed's own hits)
SNAP_S = 0.35               # the turn takes a landing this near its mark (music.sounds_on_the_drop joins a hit this near the drop)
HERO_TRIES = 4              # recordings a hero tries for the one heard best
WHIP_V = 0.6                # a crop travelling this share of a whip's speed is a cut that travels; an in-shot move sounds only as fast
# heard (lane sfx-moments): the judge's ruler, a 50 ms K-weighted peak against the voice's 400 ms level
LEVEL = {"hero": 4.0, "accent": 2.0, "body": 0.0, "tick": -2.0}        # where a hit starts (the references' median: about +2)
LEVEL_TOP = {"hero": 6.5, "accent": 4.5, "body": 3.0, "tick": 1.0}     # the most it comes up to be heard (the references'
                                                                         # loud reels: +3.4 to +6.8 median, the voice's syllables +3 to +5)
HEARD_DB = 6.0              # its 50 ms peak this far over the median of its surroundings (TEARDOWN 9: "audible")
HEARD_AIM = 7.5             # what the plan aims for: the renderer's leveller moves the voice a dB or two after it
SNR_MIN = -6.0              # and at its own loudest 50 ms no further under what plays there (a syllable's peak in the
                            # window is the voice's own dynamics, not the sound: read at the sound's peak, against both)
HERO_MASK_DB = 6.0          # a hero hit may come this much nearer the voice's syllables than the masking margin (MASK_DB 3):
                            # for its attack up to 3 dB over a syllable's peak, as the references' hero hits sit ("at or over
                            # the voice"); the hook's landing is often on its first word
WIN_S, HOP_S, VOICE_S, AROUND_S = 0.05, 0.01, 0.4, 1.0
STEP_DB = 0.5


def on(supply=None):
    """Whether this film is placed by the screen: the caller's supply says so, else the environment the workflow set."""
    if isinstance(supply, dict) and supply.get("placement"):
        return supply["placement"] == "screen"
    return os.environ.get(MODE_ENV, "") == "screen"


def _u(seed, sid):
    h = hashlib.blake2b(f"{seed}:screen:{sid}".encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(h, "big") / float(1 << 64)


# ----------------------------------------------------------------- what the picture does

def _vel(a, b):
    w0, w1 = max(a[2] - a[0], 1e-6), max(b[2] - b[0], 1e-6)
    return max(abs(w1 / w0 - 1.0), abs((b[0] + b[2]) - (a[0] + a[2])) / 2.0 / w0, abs((b[1] + b[3]) - (a[1] + a[3])) / 2.0 / w0)


def crop_travel(crop, i0, fps, most=45):
    """(seconds, speed 0-1) of the camera move that starts at crop index i0: it runs while the crop moves at least
    MOVE_STILL a frame; its speed is its fastest frame against a whip's (FAST_V)."""
    if not isinstance(crop, list) or len(crop) < 3 or not isinstance(crop[0], (list, tuple)):
        return None, None
    i0 = max(1, min(int(i0), len(crop) - 1))
    n, top = 0, 0.0
    for i in range(i0, min(len(crop), i0 + most)):
        v = _vel(crop[i - 1], crop[i])
        if v < MOVE_STILL and n >= 2:
            break
        n += 1
        top = max(top, v)
    if n < 2:
        return None, None
    return n / fps, min(1.0, top / FAST_V)


def _curve(v):
    return [float(x) for x in v] if isinstance(v, list) and len(v) > 1 else None


def card_motion(it, fps):
    """How a card arrives: (seconds it travels, speed 0-1, slam). A slam is a card that arrives scaled up and settles
    inside eight frames; a slide is a card whose position travels."""
    xs, ys, ss = _curve(it.get("x")), _curve(it.get("y")), _curve(it.get("s"))
    slam = bool(ss and ss[0] >= 1.04 * max(ss[-1], 1e-6) and
                next((i for i, s in enumerate(ss) if abs(s - ss[-1]) <= 0.01 * max(ss[-1], 1e-6)), 99) <= 8)
    path = []
    for i in range(1, max(len(xs or []), len(ys or []), len(ss or []))):
        dx = abs(xs[min(i, len(xs) - 1)] - xs[min(i - 1, len(xs) - 1)]) if xs else 0.0
        dy = abs(ys[min(i, len(ys) - 1)] - ys[min(i - 1, len(ys) - 1)]) if ys else 0.0
        ds = abs(ss[min(i, len(ss) - 1)] - ss[min(i - 1, len(ss) - 1)]) if ss else 0.0
        path.append(max(dx, dy, ds))
    n = 0
    for v in path:
        if v < MOVE_STILL and n >= 2:
            break
        n += 1
    if n < 2:
        return None, None, slam
    return n / fps, min(1.0, max(path[:n]) / FAST_V), slam


GLITCH_MODES = ("neg", "threshold", "posterise", "mosaic", "halftone", "solarise", "complement", "pixel_stretch")
PATCH_AREA = 0.3            # a treated patch under this share of the frame is a glitch's block, not a full-frame flash
FLICKER_F = 4               # a treated flash back inside this many frames after one ends: on-off-on, a glitch's stutter
TYPE_RUN = 4                # characters revealed one after another, at most TYPE_STEP_F frames apart: type being typed
TYPE_STEP_F = 3


def _rect_area(r):
    r = r[0] if isinstance(r, list) and r and isinstance(r[0], (list, tuple)) else r
    if not isinstance(r, (list, tuple)) or len(r) != 4:
        return 1.0
    return max(0.0, float(r[2]) - float(r[0])) * max(0.0, float(r[3]) - float(r[1]))


def glitched(flashes):
    """The frames whose flash is a glitch, read from the plan's own flash items: a treated flash (GLITCH_MODES) that
    is a small patch, a mosaic or a pixel stretch, or that flickers (another treated flash back within FLICKER_F frames
    of its end: on, off, on)."""
    tr = sorted((int(i["f"][0]), int(i["f"][1]), i) for i in flashes if i.get("mode") in GLITCH_MODES)
    out = set()
    for a, b, it in tr:
        if it.get("mode") in ("mosaic", "pixel_stretch") or _rect_area(it.get("rect")) < PATCH_AREA or \
                any(a2 != a and (b < a2 <= b + FLICKER_F or b2 < a <= b2 + FLICKER_F) for a2, b2, _i in tr):
            out.add(a)
    return out


def typed_runs(items, fps):
    """[(first frame, seconds, id)] of type being typed: TYPE_RUN or more single characters on one line, each revealed
    at most TYPE_STEP_F frames after the one before, left to right (a typewriter reveal, read from the plan's words)."""
    rows = {}
    for it in items:
        t = str(it.get("t") or "")
        if it.get("k") != "word" or len(t.strip()) != 1 or isinstance(it.get("y"), list) or isinstance(it.get("x"), list):
            continue
        rows.setdefault(round(float(it.get("y") or 0.0), 3), []).append((int(it["f"][0]), float(it.get("x") or 0.0), str(it.get("id"))))
    out = []
    for line in rows.values():
        line.sort()
        run = [line[0]]
        for r in line[1:] + [None]:
            if r is not None and 0 <= r[0] - run[-1][0] <= TYPE_STEP_F and r[1] > run[-1][1]:
                run.append(r)
                continue
            if len(run) >= TYPE_RUN:
                out.append((run[0][0], (run[-1][0] - run[0][0] + 1) / fps, run[0][2]))
            if r is not None:
                run = [r]
    # one line typed after another is one typing gesture
    out.sort()
    merged = []
    for f0, s, iid in out:
        if merged and f0 <= merged[-1][0] + int(round(merged[-1][1] * fps)) + 2 * TYPE_STEP_F:
            a, s0, i0 = merged[-1]
            merged[-1] = (a, max(s0, (f0 - a) / fps + s), i0)
        else:
            merged.append((f0, s, iid))
    return merged


def annotate(events, l1):
    """The events with what their pictures do: travel_s and speed (a camera move, a whip's blur, a card's slide),
    slam (a card that lands hard), still (a photograph), document (a page), glitch (a glitched flash), typed (a line
    typed a character at a time). Measured on the plan's own curves. A cut is `moving` only when its travel is
    measured at its own frame (a whip's blur there, the crop travelling from there, a card's slide): a clip whose own
    subject moves is not a cut that travels (owner, 20 Nov: "the SFX is really not related at all")."""
    fps = float(l1.get("fps") or 30.0)
    raw = l1.get("items") or []
    items = {str(i.get("id")): i for i in raw}
    assets = l1.get("assets") or {}
    fl_items = [i for i in raw if i.get("k") == "flash" and i.get("mode") not in ("pool", "recolour")
                and not str(i.get("id")).endswith((".bar", ".pool")) and ".hl" not in str(i.get("id"))]
    flashes = [int(i["f"][0]) for i in fl_items]
    glitch_f = glitched(fl_items)
    out = []
    for e in events:
        e = dict(e)
        k = e.get("kind")
        if k in ("picture", "cut") and any(abs(int(e["f"]) - f) <= 2 for f in flashes):
            e["flash_cut"] = True                    # a flash frame on the cut: the cut is a designed, visible transition
        if k in ("flash", "picture", "cut") and any(abs(int(e["f"]) - f) <= 2 for f in glitch_f):
            e["glitch"] = True
        it = items.get(str(e.get("id"))) or {}
        a = assets.get(str(it.get("pic"))) or {}
        if a.get("kind") == "document" or a.get("provider") == "primary-document" or a.get("document") is True:
            e["document"] = True
        if a.get("foley") and k in ("picture", "cut", "card"):
            e["foley"] = str(a["foley"])             # the object a model saw in this picture (the workflow's picture check)
        if k == "move" and it.get("k") == "shot":
            s, v = crop_travel(it.get("crop"), int(e["f"]) - int(it["f"][0]), fps)
            if s:
                e.update(travel_s=round(s, 3), speed=round(v, 3))
        elif k in ("picture", "cut") and it.get("k") == "shot":
            i0 = max(0, int(e["f"]) - int(it["f"][0]))          # the cut's own frame inside its shot (0 at the shot's start)
            blur = it.get("blur")
            bl = [float(b or 0) for b in blur[i0:i0 + 30]] if isinstance(blur, list) else []
            if len(bl) > 1 and max(bl[:4]) >= 0.5:               # drawn blurred at the cut's own frames: a whip
                n = sum(1 for b in bl if b >= 0.5)
                e.update(travel_s=round(max(n, 2) / fps, 3), speed=round(min(1.0, max(bl) / 12.0), 3), whip=True)
            else:
                s, v = crop_travel(it.get("crop"), max(1, i0), fps)
                if s and v >= WHIP_V:                   # a crop that only drifts from the cut is a plain cut (raters: whooshes
                    e.update(travel_s=round(s, 3), speed=round(v, 3))      # on camera drift, not on travel)
            if a.get("kind") == "image":
                e["still"] = True
        elif k in ("card", "picture") and it.get("k") == "card":
            s, v, slam = card_motion(it, fps)
            if s:
                e.update(travel_s=round(s, 3), speed=round(v, 3))
            if slam:
                e["slam"] = True
            if a.get("kind") == "image":
                e["still"] = True
        if k in ("picture", "cut"):
            e["moving"] = bool(e.get("whip") or (e.get("travel_s") and not e.get("slam")))
        out.append(e)
    for f0, s, iid in typed_runs(raw, fps):
        hit = next((e for e in out if abs(int(e["f"]) - f0) <= 2), None)
        if hit is None:                              # the typing starts where nothing else lands: it is its own moment
            out.append({"f": f0, "kind": "display", "id": iid, "typed": True, "type_s": round(s, 3)})
        elif hit.get("kind") in ("display", "card"):
            hit.update(typed=True, type_s=round(s, 3))
    out.sort(key=lambda e: int(e["f"]))
    return out


def travels(e):
    """A picture that travels, by measure only: a camera move, a whip's blur, a crop or a card travelling. The bare
    `moving` flag a workflow writes (its clip's own subject moving) is not travel: a plain cut stays a plain cut."""
    return bool(e.get("kind") == "move" or e.get("whip") or (e.get("travel_s") and not e.get("slam")))


def fits(e, part=None):
    """The object families this moment's picture shows: paper for a document, a shutter for a still photograph
    landing, a glitch for a glitched flash, typing for type being typed."""
    f = set()
    k = e.get("kind")
    if e.get("document"):
        f.add("paper")
    if e.get("still") and k in ("picture", "card"):
        f.add("shutter")
    if k in ("flash", "picture", "cut") and e.get("glitch"):
        f.add("glitch")
    if e.get("typed"):
        f.add("type")
    if e.get("foley") and k in ("picture", "cut", "card"):
        f.add(str(e["foley"]))
    return f


EFFECT_FAMILIES = ("shutter", "glitch", "type")


def whip_move(e):
    """An in-shot camera move that travels like a whip (raters, 20 Nov: whooshes on drifting camera moves read as noise)."""
    return e.get("kind") == "move" and float(e.get("speed") or 0.0) >= WHIP_V


def matters(e):
    """How much this moment asks for a sound, by what the picture does (lane sfx-moments): an object it shows landing,
    a glitch or typing first, a hard landing, a cut that really travels, a photograph's shutter, then type and cards."""
    f = fits(e)
    if f - {"shutter"}:
        return 3.0
    if e.get("slam"):
        return 2.5
    if e.get("whip") or (e.get("travel_s") and float(e.get("speed") or 0.0) >= WHIP_V and e.get("kind") != "move"):
        return 2.0
    if whip_move(e):
        return 1.6
    if "shutter" in f:
        return 1.5
    if e.get("kind") in ("card", "page", "display", "counter", "flash", "burst"):
        return 1.0
    return 0.5


# ----------------------------------------------------------------- the choice

def _scr(x):
    return (x or {}).get("screen") or {}


def sweep_s(x):
    """A recording's audible gesture: its climb to the peak and its tail (the set's measures), else its length."""
    s = _scr(x)
    if s.get("attack_s") is not None and s.get("tail_s") is not None:
        return max(0.05, float(s["attack_s"]) + float(s["tail_s"]))
    return float(x.get("max_s") or x.get("duration_s") or 0.5)


TURN_PEN = 1.5             # the palette voice heard nearest this moment, while another fits within an octave
TURN_WINDOW_S = 30.0        # one recording at most TURN_MOST times inside this window while the class has another voice
TURN_MOST = 2
TURN_FRESH = 2.0            # per use inside the window, weighted by how recent it is
TURN_CAP = 4.0
ROTATE = ("whoosh", "hit", "impact", "riser")   # a film's type keeps one click voice, as the references' type does
PHONE_DB = -6.0             # a hit's 200 Hz-8 kHz body this far under its full level is still a hit a phone plays
PHONE_W = 0.12              # dist per dB under PHONE_DB: a sub-only boom (99% under 93 Hz) is near silent on a phone


def audible(x, transient=False):
    """dB of a recording's 200 Hz-8 kHz band against its full band (the set's measures): what a phone speaker plays of
    it. transient: in the 80 ms around its loudest moment (its attack), else its loudest 100 ms. None unmeasured."""
    s = _scr(x)
    v = s.get("trans_db" if transient else "body_db")
    if v is None and transient:
        v = s.get("body_db")
    return None if v is None else float(v)


def pick(cls, st, lib, t, want_s=None, want_c=None, fits_=(), avoid=(), low=False, room=None, clean=False, preroll=None,
         transient=False, only=None):
    """The recording of this class (the class the set gave it) that best fits the moment, inside the film's palette.
    A hit or an impact is chosen for what a phone plays: its 200 Hz-8 kHz body (transient: its attack's) stands near
    its full level; weight under 150 Hz (low) is a bonus only on top of that body. Inside the palette the voices take
    turns: the one heard nearest in time, or one already heard TURN_MOST times within TURN_WINDOW_S of it, gives way
    whenever another voice of the class fits the moment's length within an octave."""
    desc = st["desc"]
    pal = st.setdefault("palette", {}).setdefault(cls, [])
    heard = st.setdefault("heard", [])
    seed = st.get("seed") or 0
    cands = []
    for sid, d in desc.items():
        if sid in avoid or d.get("kind") != cls:
            continue
        x = lib.get(sid) or {}
        fam = d.get("family")
        if fam and fam not in fits_:
            continue                                   # an object answers only the picture that shows it
        if only and fam != only:
            continue
        if x.get("align") == "start" and not (fam == "type" and "type" in fits_):
            continue                                   # a bed that starts on its frame: only type being typed
        s = _scr(x)
        if room is not None and cls == "riser" and float(s.get("attack_s", 0.0)) > room + 0.25:
            continue                                   # a climb longer than its room swallows what plays before it
        cands.append((sid, x, s, fam))
    if not cands:
        return None
    full = len(pal) >= PALETTE.get(cls, 2)

    def octave(x):
        return not want_s or abs(math.log2(max(sweep_s(x), 0.05) / max(want_s, 0.05))) <= 1.0

    def free(sid):                                     # a voice that may take this turn: in the palette, or room for it
        return (sid in pal or not full) and not any(s2 == sid and abs(t - u) < SAME_GAP_S for u, s2 in heard)
    # the films are placed out of time order (the hook, the turn and the close first): the voice heard next to this
    # moment, on either side, is the one that must not play again here
    near = min(((abs(t - u), s2) for u, s2 in heard if s2 in pal), default=(None, None))[1]
    best = None
    for sid, x, s, fam in cands:
        dist = 0.0
        if want_s:
            dist += abs(math.log2(max(sweep_s(x), 0.05) / max(want_s, 0.05)))
        if want_c and s.get("centroid_hz"):
            dist += 0.35 * abs(math.log2(max(float(s["centroid_hz"]), 80.0) / want_c))
        if float(s.get("pitch_hz", 0.0)) >= 150.0:
            dist += 1.5 * max(0.0, float(s.get("tonal", 0.0)) - TONAL)       # a pitch that may fight the song's key
        if cls in ("impact", "hit"):
            body = audible(x, transient)
            if body is not None:
                dist += PHONE_W * max(0.0, PHONE_DB - body)                   # what a phone speaker plays of it
                if low:                                # weight under 150 Hz on top of an audible body, never instead of it
                    dist -= 0.4 * min(1.0, float(s.get("low_share", 0.0))) * min(1.0, max(0.0, (body + 15.0) / 9.0))
            elif low:
                dist -= 0.4 * min(1.0, float(s.get("low_share", 0.0)))
        elif low:
            dist -= 0.4 * min(1.0, float(s.get("low_share", 0.0)))
        if clean and preroll is not None and preroll(sid) >= 0.3:
            dist += 1.0                                # a hit with its own whoosh-in leaves no room for the designed lead
        if fam:
            dist -= 0.5
        if sid in pal:
            dist -= 0.25                               # the film's own palette: the same voices again
        elif full:
            dist += 0.9
        if cls in ROTATE and PALETTE.get(cls, 2) > 1:
            other = any(o != sid and free(o) and octave(ox) for o, ox, _s, _f in cands)
            ages = [abs(t - u) for u, s2 in heard if s2 == sid and abs(t - u) < TURN_WINDOW_S]
            if other:
                if sid == near:
                    dist += TURN_PEN                   # inside the palette the voices take turns, as an editor's do
                dist += TURN_FRESH * sum(1.0 - a / TURN_WINDOW_S for a in ages)   # the least lately heard first
                if len(ages) >= TURN_MOST:
                    dist += TURN_CAP                   # one whip four times in 25 s reads as a loop, not a cut
        elif sid == near and PALETTE.get(cls, 2) > 1:
            dist += 0.5
        if any(s2 == sid and abs(t - u) < SAME_GAP_S for u, s2 in heard):
            dist += 3.0
        dist += 0.25 * _u(seed, sid)
        if best is None or dist < best[0]:
            best = (dist, sid)
    return best[1] if best else None


def forget(st, t):
    """The tries made at t leave the palette and the voices heard (a try the voice hid, or one a better try replaced)."""
    heard = st.setdefault("heard", [])
    gone = {s2 for u, s2 in heard if abs(u - t) < 1e-6}
    if gone:
        heard[:] = [(u, s2) for u, s2 in heard if abs(u - t) >= 1e-6]
        for pal in st.setdefault("palette", {}).values():
            pal[:] = [s2 for s2 in pal if s2 not in gone or any(s3 == s2 for _u, s3 in heard)]


def retake(st, t, pairs):
    """The moment at t plays these [(class, recording)] after all: the palette and the voices heard say so."""
    forget(st, t)
    for cls, sid in pairs:
        if sid:
            _keep(st, cls, sid, t)


def class_of(st, sid):
    return ((st.get("desc") or {}).get(sid) or {}).get("kind") or "whoosh"


def _keep(st, cls, sid, t):
    pal = st.setdefault("palette", {}).setdefault(cls, [])
    if sid not in pal:
        pal.append(sid)
    st.setdefault("heard", []).append((t, sid))


def _speed_c(e):
    v = e.get("speed")
    return None if v is None else 700.0 * 2 ** (2.5 * float(v))      # a slow move dark (700 Hz), a whip bright (~4 kHz)


def _sweep_len(e, w):
    if e.get("travel_s"):
        return max(0.18, min(1.1, float(e["travel_s"]) + 0.12))
    return 0.35 if w < 0.5 else 0.55


def layers(e, part, tier, t, st, lib, preroll, avoid=()):
    """[(recording, 'lead' | 'hit')] for one moment, and its class, chosen by what the picture does."""
    k = e.get("kind")
    room = float(e.get("room_s", 0.0))
    w = float(e.get("_w", 0.5))
    fit = fits(e, part)
    avoid = set(avoid)
    out = []
    # every call for this moment is a new try: an earlier try here was hidden by the voice (at this tier or the one
    # above it), so it never joins the palette and never counts as heard
    forget(st, t)

    def take(cls, sid, what="hit"):
        out.append((sid, what))
        _keep(st, cls, sid, t)

    if tier == "hero" and e.get("soft"):
        # a held beat (the Director's energy at the hook, the turn or the close): a hit, short and dry, no climb and no
        # trailer impact (raters, 20 Nov: an impact on 'the lowest moment of my life' read tonally wrong)
        sid = pick("hit", st, lib, t, want_s=0.4, fits_=fit, avoid=avoid) or \
            pick("impact", st, lib, t, want_s=0.6, fits_=fit, avoid=avoid, transient=True)
        if sid is None:
            return [], "hit"
        cls = (st.get("desc", {}).get(sid) or {}).get("kind") or "hit"
        take(cls, sid)
        return out, cls
    if tier == "hero":
        want = {"hook": 0.8, "payoff": 1.0, "close": 1.2}.get(part, 1.0)
        lead_room = room >= (0.4 if part == "hook" else 1.2)
        hit = pick("impact", st, lib, t, want_s=want, fits_=fit, avoid=avoid, low=part != "hook", clean=lead_room, preroll=preroll,
                   transient=part == "hook")               # the hook is heard by its attack: ranked by the attack's audible band
        if hit is None:
            return [], "impact"
        lead = None
        if preroll(hit) < 0.3:
            if part == "hook" and travels(e) and room >= 0.4:
                lead = ("whoosh", pick("whoosh", st, lib, t, want_s=min(0.6, room - 0.1), want_c=_speed_c(e), fits_=fit, avoid=avoid | {hit}))
            elif (part == "close" or (part == "payoff" and not st.get("bed"))) and room >= 1.2:
                # with a song under the film its own build and drop carry the turn: no riser stacked on them
                lead = ("riser", pick("riser", st, lib, t, want_s=min(room - 0.2, 2.5), fits_=set(), avoid=avoid | {hit}, room=room - 0.2))
        if lead and lead[1]:
            take(lead[0], lead[1], "lead")
        take("impact", hit)
        return out, "impact"
    if k in ("counter", "burst"):
        sid = pick("click", st, lib, t, want_s=0.1, want_c=2500.0, fits_=fit, avoid=avoid)
        if sid:
            take("click", sid)
        return out, "click"
    if e.get("flash_cut") and k != "flash":
        k = "flash"
    if k == "flash" and "glitch" in fit:
        for cls in ("whoosh", "click", "hit"):
            sid = pick(cls, st, lib, t, want_s=0.4, fits_={"glitch"}, avoid=avoid)
            if sid and (lib.get(sid) or {}).get("family") == "glitch":
                take(cls, sid)
                return out, cls
    for fam in sorted(fit - set(EFFECT_FAMILIES)):   # paper, or the object a model named: the object's own sound
        for cls in ("whoosh", "click", "hit"):
            sid = pick(cls, st, lib, t, want_s=0.5, fits_={fam}, avoid=avoid, only=fam)
            if sid and (lib.get(sid) or {}).get("family") == fam:
                take(cls, sid)
                return out, cls
    if "shutter" in fit and tier != "accent" and not travels(e):
        sid = pick("click", st, lib, t, want_s=0.25, fits_={"shutter"}, avoid=avoid)
        if sid:
            take("click", sid)
            return out, "click"
    if e.get("typed") and "type" in fit:              # type being typed: the keys under it, from its first character
        sid = pick("click", st, lib, t, fits_={"type"}, avoid=avoid, only="type")      # trimmed to the typing's length
        if sid:
            take("click", sid)
            return out, "click"
    if k == "display":
        sid = pick("click", st, lib, t, want_s=0.12, want_c=2500.0, fits_=fit, avoid=avoid)    # the film's one type voice
        if sid:
            take("click", sid)
        return out, "click"
    if travels(e) or k == "flash":
        want_c = _speed_c(e) if k != "flash" else 4000.0
        sid = pick("whoosh", st, lib, t, want_s=_sweep_len(e, w) if k != "flash" else 0.3, want_c=want_c, fits_=fit, avoid=avoid)
        if sid:
            take("whoosh", sid)
        return out, "whoosh"
    if k in ("card", "page", "picture", "cut"):
        hard = bool(e.get("slam")) or float(e.get("area") or 0.0) >= 0.35 or w >= 0.75
        if hard and tier == "accent":
            cls = "impact" if w >= 0.85 else "hit"
            sid = pick(cls, st, lib, t, want_s=0.7 if cls == "impact" else 0.4, fits_=fit, avoid=avoid, low=cls == "impact")
            if sid:
                take(cls, sid)
                return out, cls
        if k == "card" and float(e.get("area") or 0.0) < 0.08:
            sid = pick("click", st, lib, t, want_s=0.15, want_c=1500.0, fits_=fit, avoid=avoid)      # a small graphic pops in
            if sid:
                take("click", sid)
            return out, "click"
        sid = pick("hit", st, lib, t, want_s=0.35, fits_=fit, avoid=avoid)
        if sid:
            take("hit", sid)
        return out, "hit"
    sid = pick("whoosh", st, lib, t, want_s=0.4, fits_=fit, avoid=avoid)
    if sid:
        take("whoosh", sid)
    return out, "whoosh"


# ----------------------------------------------------------------- restraint in the band the voice leaves bare

def band_frames(x, rate, lo, hi, win=0.1):
    """RMS dB of the lo-hi band in `win` frames (half overlapped), mono."""
    n = int(win * rate)
    if x is None or len(x) < n:
        return np.zeros(0)
    hz = np.fft.rfftfreq(n, 1.0 / rate)
    m = (hz >= lo) & (hz < hi)
    w = np.hanning(n)
    norm = float((w ** 2).sum())
    out = []
    for i in range(0, len(x) - n + 1, n // 2):
        X = np.abs(np.fft.rfft(x[i:i + n] * w)) ** 2
        out.append(10 * np.log10(2.0 * float(X[m].sum()) / (n * norm) + 1e-18))
    return np.asarray(out)


def voice_hf(voice, rate, shift_db=0.0):
    """The voice's own 2-8 kHz level at the mix (p90 of its spoken frames), dB; None without a voice."""
    if voice is None or not len(voice):
        return None
    hi = band_frames(voice, rate, *HF_BAND)
    full = band_frames(voice, rate, 80.0, min(8000.0, rate / 2.0 - 1.0))
    if not len(hi):
        return None
    spoken = full >= full.max() - 30.0
    return float(np.percentile(hi[spoken], 90)) + float(shift_db)


def restrain(sounds, voice, rate, shift_db, lib, report=None, fps=None, sample=None, srate=None):
    """The ceilings, the final word on every set recording's level: its 2-8 kHz level (its measured hf_db plus its
    gain) never more than HF_OVER over the voice's own top band (a spike), and, with fps and sample, its 50 ms
    K-weighted peak never more than LEVEL_TOP over the voice's 400 ms level (the judge's ruler: a hit that loud is a
    cringe, whatever lifted it; score.audibility's broadband lift rides up to the voice's loudest syllables)."""
    ref = voice_hf(voice, rate, shift_db)
    done = []
    if ref is None:
        return sounds
    v = None
    if fps and sample is not None:
        v = np.asarray(voice, np.float64) * (10 ** (float(shift_db) / 20))
    for s in sounds:
        x = lib.get(s.get("s")) or {}
        hf = _scr(x).get("hf_db")
        if s.get("db") is None:
            continue
        was = s["db"]
        tier = s.get("tier") or "body"
        if s.get("soft") and tier == "hero":
            tier = "accent"
        if hf is not None:
            over = float(hf) + float(s["db"]) - (ref + HF_OVER.get(tier, 8.0))
            if over > 0:
                s["db"] = round(max(GAIN_MIN, float(s["db"]) - over), 1)
        if v is not None and s.get("layer") in ("hit", "accent") and s.get("for") not in ("counter", "burst") \
                and s.get("start_s") is not None and s.get("event_f") is not None:
            r = reading(s, sample(s["s"]), v, rate, fps, srate=srate)
            if r is not None and r[5] is not None:
                top = r[5] + LEVEL_TOP.get(tier, 3.0) - float(_db(r[2][r[3]].max()))
                if float(s["db"]) > top + 0.05:
                    s["db"] = round(max(GAIN_MIN, top), 1)
        if s["db"] != was:
            done.append(dict(id=s.get("id"), s=s.get("s"), tier=s.get("tier"), db=[was, s["db"]]))
    if report is not None:
        report.setdefault("screen", {}).update(voice_hf_db=round(ref, 1), restrained=done)
    return sounds


# ----------------------------------------------------------------- heard: the judge's own ruler (lane sfx-moments)

_KW = {}


def _kw(n, rate):
    if (n, rate) not in _KW:
        from .sound_signal import _kweight
        _KW[(n, rate)] = _kweight(np.fft.rfftfreq(n, 1.0 / rate)).astype(np.float64)
    return _KW[(n, rate)]


def k_power(x, rate, k0=0, k1=None, win=WIN_S, hop=HOP_S):
    """K-weighted mean-square power of x in frames k0 .. k1-1 of the film's grid (frame k spans k * hop to
    k * hop + win seconds); a frame past either end reads 0. Stereo: the mean of its channels' powers (as loudness
    sums them; the mean of the samples reads a wide stereo sweep up to 3 dB under what plays)."""
    x = np.asarray(x, np.float64)
    if x.ndim == 2:
        return np.mean([k_power(x[:, c], rate, k0, k1, win, hop) for c in range(x.shape[1])], axis=0) if x.shape[1] else \
            np.zeros(max(0, int(k1 or 0) - int(k0)))
    n, h = int(round(win * rate)), int(round(hop * rate))
    total = 1 + (len(x) - n) // h if len(x) >= n else 0
    k1 = total if k1 is None else int(k1)
    k0 = int(k0)
    out = np.zeros(max(0, k1 - k0))
    a, b = max(k0, 0), min(k1, total)
    if b <= a:
        return out
    idx = np.arange(a, b) * h
    X = np.abs(np.fft.rfft(x[idx[:, None] + np.arange(n)[None, :]], axis=1)) ** 2
    out[a - k0:b - k0] = 2.0 * (X * _kw(n, rate)).sum(axis=1) / float(n * n)
    return out


def _db(p):
    return 10.0 * np.log10(np.maximum(np.asarray(p, np.float64), 1e-20))


def voice_level(pv, ctr, t):
    """The voice's 400 ms K level around t (TEARDOWN 9's reference), from its 50 ms frame powers `pv` centred at
    `ctr`: the level its speech holds within 1.5 s (the median of its 400 ms levels within 12 dB of their loudest), so
    a hit in a breath or on a word's last syllable is set against the speech around it, not against the dip."""
    k = max(1, int(round(VOICE_S / HOP_S)))
    l400 = _db(np.convolve(pv, np.ones(k) / k, mode="same"))
    near = np.abs(ctr - t) <= 1.5
    if not near.any():
        return None
    top = float(l400[near].max())
    if top < -90:
        return None
    return float(np.median(l400[near][l400[near] >= top - 12.0]))


def reading(it, smp, under, rate, fps, voice=None, srate=None):
    """What the judge's ruler reads of one placed hit: (frame centres, the surroundings' power, the hit's power at
    0 dB, the hit window, the surroundings window, the voice's level there). None when it cannot be read. The hit's
    samples may come at their own rate (srate: the full band; a bright sweep read at 16 kHz loses its top octave and
    reads several dB quieter than the ear and the judge hear it); the powers are mean squares on one time grid."""
    t = float(it["event_f"]) / float(fps)
    srate = srate or rate
    smp = np.asarray(smp, np.float64)[int(round(float(it.get("trim_s", 0)) * srate)):]
    if it.get("dur_s"):
        smp = smp[:int(round(float(it["dur_s"]) * srate))]
    if not len(smp) or under is None or not len(under):
        return None
    k0 = max(0, int(math.floor((t - AROUND_S - 0.6) / HOP_S)))
    k1 = int(math.ceil((t + AROUND_S + 0.6) / HOP_S))
    pu = k_power(under, rate, k0, k1)
    pv = pu if voice is None else k_power(voice, rate, k0, k1)
    lay = np.zeros((int(round((k1 - k0) * HOP_S * srate + WIN_S * srate)) + 1,) + smp.shape[1:])
    at = int(round(float(it["start_s"]) * srate)) - int(round(k0 * HOP_S * srate))
    a, b = max(0, at), min(len(lay), at + len(smp))
    if b > a:
        lay[a:b] = smp[a - at:b - at]
    ps = k_power(lay, srate, 0, k1 - k0)
    ctr = np.arange(k0, k1) * HOP_S + WIN_S / 2.0
    hitw = (ctr >= t - 0.05) & (ctr <= t + 0.15)
    sur = (np.abs(ctr - t) <= AROUND_S) & ~((ctr >= t - 0.1) & (ctr <= t + 0.25))
    if not hitw.any() or not sur.any() or float(ps[hitw].max()) <= 0:
        return None
    return ctr, pu, ps, hitw, sur, voice_level(pv, ctr, t)


def prominence(r, g_db):
    """How heard the hit is at gain g_db, dB: the mix's 50 ms frame at the hit's own loudest over the median of what
    plays around it (TEARDOWN 9's 'audible'; the voice and the song, not the hit's own tail), held under HEARD_DB
    while the hit sits more than -SNR_MIN under what plays with it (a syllable peak in the window is the voice's
    dynamics, not the sound)."""
    _ctr, pu, ps, hitw, sur, _vo = r
    g = 10 ** (g_db / 10.0)
    k = np.flatnonzero(hitw)[int(np.argmax(ps[hitw]))]
    over = float(_db(pu[k] + ps[k] * g) - np.median(_db(pu[sur])))
    snr = float(_db(ps[k] * g) - _db(pu[k]))
    return min(over, HEARD_DB + (snr - SNR_MIN))


def level(group, under, rate, sample, fps, voice=None, masking=None, hf_ref=None, lib=None, gain_min=GAIN_MIN, gain_max=GAIN_MAX,
          start=None, srate=None):
    """Each hit of a gesture made heard on the judge's ruler: set at LEVEL[tier] over the voice's 400 ms level (its
    own 50 ms K-weighted peak), then raised in STEP_DB steps up to LEVEL_TOP[tier] while its peak stands under
    HEARD_DB over the median of its surroundings (`under`: the voice, or the voice and the song). Never into the
    speech band (masking(item) > 0 lowers it) and never a 2-8 kHz spike (HF_OVER over the voice's own top band,
    hf_ref). start: a level already set (the plan's, when the song comes in): never lowered under it. Writes db,
    heard_db and over_voice_db on each item; returns the lowest heard_db (None: nothing read)."""
    worst = None
    for it in group:
        if it.get("layer") not in ("hit", "accent") or it.get("for") == "counter" or it.get("start_s") is None:
            continue
        tier = it.get("tier") or "body"
        if it.get("soft") and tier == "hero":
            tier = "accent"                            # a held beat's hit sits at an accent's level, kept as the hero's landing
        r = reading(it, sample(it["s"]), under, rate, fps, voice, srate)
        if r is None or r[5] is None:
            continue
        vo = r[5]
        own0 = float(_db(r[2][r[3]].max()))
        g = vo + LEVEL.get(tier, 0.0) - own0
        top = vo + LEVEL_TOP.get(tier, 3.0) - own0
        hf = _scr((lib or {}).get(it["s"])).get("hf_db")
        if hf_ref is not None and hf is not None:
            top = min(top, hf_ref + HF_OVER.get(tier, 8.0) - float(hf))
        top = min(top, gain_max)
        g = max(gain_min, min(g, top))
        if start is not None:
            g = max(g, float(start))
            top = max(top, g)
        ceiling = top
        if masking is not None and start is None:
            for _ in range(8):                         # down until the speech band is clear (its 80th percentile first)
                it["db"] = round(g, 1)
                need = masking(it)
                if need <= 0 or g <= gain_min:
                    break
                g = max(gain_min, g - need)
                top = min(top, g)
        p = prominence(r, g)
        while p < HEARD_AIM and g + STEP_DB <= top + 1e-6:
            it["db"] = round(g + STEP_DB, 1)
            if masking is not None and masking(it) > 0:
                break
            g += STEP_DB
            p = prominence(r, g)
        if p < HEARD_DB and it.get("tier") == "hero":
            # the hook, the turn and the close land where the story does, often on a word: heard there, up to its
            # ceiling, for its attack over the syllable it lands on (the references' hero hits sit at or over the voice)
            while p < HEARD_AIM and g + STEP_DB <= ceiling + 1e-6:
                g += STEP_DB
                p = prominence(r, g)
        it["db"] = round(g, 1)
        it["heard_db"] = round(p, 1)
        it["over_voice_db"] = round(own0 + g - vo, 1)
        worst = p if worst is None else min(worst, p)
    return worst
