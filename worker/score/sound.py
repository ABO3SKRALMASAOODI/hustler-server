"""The film's sound design (lane J, 13 Nov): one pass that gives the film's chosen picture events a sound from
the owner's library, peak on the event, and the master that sets the voice clean and level.

    apply(l1, work=None, index=None, perc=None, asks=None)   the seam: the L1 with its designed `sound`, never raises
    design(l1, ...) -> (sounds, report)                      the same, returning what it decided
    plan_sounds(events, ctx) -> (sounds, report)             the core, on picture events from any source

The master (voice levelled, sounds on it, -14 LUFS, under -1 dBTP, AAC) is scene.audio.master: the engine reads no
score code (test_the_engine_imports_no_model_code), so the mix lives on its side of the seam.

Owner rules (binding; 13 Nov 2026: the references are the judge of the sound effects, their kinds and their amount):
only the approved recordings of `sound_library` (manifest, sound:<id>; approved by the owner's ear or by reference
match, sound_approval); nothing synthesised; never music; never a sound on a caption; never a cheap or cartoon
sound. A sound answers a picture event (a new picture, a card or page landing, a display line, a counter step, a
flash on a cut, the hook and the payoff) with its PEAK on the event's frame, 13 ms early (atlas 8.5: locked sounds
peak a median -13 ms from the cut, in every class).

The shape (SND3, 14 Nov; the independent check of SND2 found every sound within 1-2.5 dB of one level, thin openings,
64-83% of sounds on cuts, the same dozen recordings in every film and a riser under the song):
  level    one weight hierarchy fitted to the references' spread (TIER_DB): hero hits (the hook, the turn, the close) at
           the voice, accents (the film's heaviest fifth of moments) a few dB under, the body under that, small ticks
           (counter steps, train clicks, the lightest type) far under; the film's own ladder decides who is which;
  density  by section (PROFILE): a dense, designed opening (a sweep flies into the hook's hit, clicks on each hook cut
           and word), breathing in the middle, a build into the turn, the rest after it, nothing after the close;
  motive   new type, cards, counters and camera moves are tried before cuts; after the opening (a cluster on the
           hook's cuts and words) cuts hold at most CUT_SHARE of the film's sounds unless the film has too little else
           (CUT_FILL);
  variety  no recording twice in a film; each film's seed turns the library its own way (ROTATE); the class comes from
           the moment (sound_choice) and the tier (an accent is never a tick's click, the body never an impact);
  one mix  the turn's hit is the song's drop (music.sounds_on_the_drop), the song dips under each hero hit and its own
           landing is eased when it would cover the hit (scene.audio.duck_curve), the close is the song's last hit
           (close_time), a riser or a quiet hit the song still covers is taken out (present_over).

How many (SND2, 14 Nov): the references' amount for the film's supply. A film of new pictures carries the judge's
3.6 clearly audible sounds per 10 s (judge/metromedia/TEARDOWN.md section 9, median of the 8), a film of some
pictures and a talking take what the governor allows them (taste.allow), a long-form film 0.8 (the study's 20
long films), never more than the picture supply earns (taste.allow) and never more in any 10 s than the rate and
a half, and spread over the whole film: it is cut into as many equal stretches as it may carry sounds and every
stretch's best event is tried before any stretch's second (the payoff is not left in silence because the opening
spent the allowance). Two gestures are never closer than 0.6 s (the references' median gap is 0.94 s), one recording at most
twice in 30 s and never twice inside 3 s. The hook and the payoff are layered (a lead whose peak meets the hit):
one gesture to the ear and to every counter. A burst of quick cuts (four or more new pictures inside 0.4 s of each
other) is one gesture too: a train of clicks, one on each cut, as the judge's list bursts are sounded.

How loud: at the references' level for the class. A recording that joined by reference match sits where the
references put that class against the voice (sound_profiles.CLASSES over_voice_spoken_db: a click 6.9 dB under
the voice, a hit 8.2, a whoosh 7.7, an impact 3.0, measured on stems as the K-weighted loudest 200 ms against the
voice's speech level), plus the 3 dB a stem loses of a sound under a voice (STEM_LOSS_DB: measured by reading this
engine's own mixes back on the same ruler), so that it reads where theirs do; the first library keeps its judged
role targets (ROLE below). Gains are set against the voice the
mix will have (the renderer levels speech to dialogue_level's -20 LUFS first). Under speech a sound sits at least
the class's margin under the voice. Then a signal check: each layer is laid over the programme's own voice in
20 ms frames, and one whose speech band (300-3400 Hz) comes within 3 dB of the voice on spoken frames is lowered
to its floor, else dropped.

Which recording answers which event is sound_choice's (13 Nov, the sound engine lane): the references' classes for
the moment (a click, a hit, an impact, a whoosh; a riser only as the climb into a hit), the class the film has
used least against their shares, then the approved recording nearest that class's reference shape on the signal
ruler (sound_signal), leaning away from the speech band under a word. gesture() below is the fixed table it
replaced, kept for a library the ruler cannot read. A sound that lands in a breath comes up as the references' do.

Nothing here reads what anyone asked for. When the Scout bound an ask to the sound department (a model read it),
the Director's own lines carry that ask and this pass adds nothing.
"""
import hashlib
import json
import math
import os
import re
import subprocess
import zlib

import numpy as np

from . import sound_choice as choice
try:
    from . import sound_profiles as SP
except Exception:  # noqa: BLE001 - without the study's numbers the fixed table answers
    SP = None

HERE = os.path.dirname(os.path.abspath(__file__))
SOUNDS = os.environ.get("VALMERA_SOUNDS") or os.path.join(os.path.dirname(HERE), "sound_library")
FF = os.environ.get("FFMPEG_BIN", "ffmpeg")

SR = 48000                  # the mix rate
AR = 16000                  # the analysis rate
LEAD_S = 0.013              # a placed sound's peak lands this far before its frame (atlas 8.5)
VOICE_LUFS = -20.0          # dialogue_level.TARGET_LUFS: the voice every gain is set against
GAIN_MIN, GAIN_MAX = -36.0, -2.0
MERGE_S = 0.6               # sounds closer than this are one gesture
SPACE_S = 0.6               # two gestures never closer than this (the references' median gap is 0.94 s, their tenth percentile 0.19)
SAME_GAP_S, SAME_WIN_S, SAME_MAX = 3.0, 30.0, 2
HOOK_S = 3.0
PAYOFF_FROM = 0.6           # the payoff is a designed landing in the film's last 40%
END_GUARD_S = 0.3           # no sound lands in the last 0.3 s (the brand's end card follows)
MASK_DB = 3.0               # a sound within 3 dB of the voice in the speech band, on a spoken frame, masks it
MASK_FRAMES = 3             # for 60 ms or more
FRAME = 320                 # 20 ms at AR
SPEECH_BAND = (300.0, 3400.0)
COUNTER_TICKS = 8
PRESENT_DB = 9.0            # a placed sound stands this far over the voice in the band it lives in, at its hit (the atlas's
PRESENT_MIN = 5.0           # detector counts a reference's sound from 9 dB over its surroundings); under PRESENT_MIN it is not there
PRESENT_LIFT = 4.0          # the most a sound comes up to be heard (still inside the references' own levels, and never into the voice)
PRESENT_BANDS = ((60.0, 300.0), (300.0, 1000.0), (1000.0, 3400.0), (3400.0, 8000.0))
# The references' placed layer per 10 s by the film's supply: clearly audible sounds (50 ms peak 6 dB or more over the
# bed). P: the judge's median of 8 (TEARDOWN section 9: 3.6, range 1.1-10.4). L: the study's 20 long films (0.8 placed).
# M and T are the governor's own rates (taste.allow: 3 and 2 over a bed), under the judge's.
RATE_10S = {"P": 3.6, "M": 3.6, "T": 3.6, "L": 0.8}
BURST = 1.5                 # in any 10 s, at most the rate and a half (the references' densest 10 s runs to twice their median)
KINDS_30S = 5               # the references' five classes may all sound inside 30 s; a sixth kind of sound may not
OWN_CUT_10S = 1             # the upload's own cuts: at most one sounded in any 10 s
# A burst: a run of quick cuts, one new picture after another (the judge's list bursts: 8 to 14 cuts 0.12-0.25 s apart,
# TEARDOWN B31). The references sound it as a train, one click a cut; so do we, as ONE gesture.
TRAIN_GAP_S = 0.4           # cuts this close are one run
TRAIN_MIN, TRAIN_MAX = 4, 14
TRAIN_EVERY_S = 8.0         # two trains never start closer than this
TRAIN_VOICES = 3            # a train alternates up to three recordings of one kind: the same sample in a row is a machine
TRAIN_CLICK_S = 0.3         # a train's click is over before the cut after the next
TRIES = 3                   # recordings tried on one event before the voice is given the moment

# SND3 (14 Nov): the shape of the references' sound, fitted to the 28 @metromedia.house reels on the study's ruler
# (lanes/S/study: every placed sound of 9 dB prominence or more off the song's grid, K-weighted loudest 200 ms against
# the voice; the independent check of SND2, RATINGS/2026-11-13-SND2.json, read the same way).
# Level: one weight hierarchy. The references' sounds spread 6.2 dB from their 25th to 75th percentile (3.8-8.8 over
# the 28, 5.5-14 over the owner's 8) and by class sit at click -9.1, hit -8.6, whoosh -6.4, impact -5.0 (the impact's
# upper tenth +1.5): a hero hit (the hook, the turn, the close) at or over the voice, an accent (a big picture change, a
# reveal) a few dB under it, the body (moves, cuts, type) under that, a tick (a counter step, a train's click, a small
# type pop) far under. SND2 put every sound within 1-2.5 dB of one level.
TIER_DB = {"hero": 1.0, "accent": -4.0, "body": -8.5, "tick": -14.5}
TIER_MID = {"hero": 1.0, "accent": 0.82, "body": 0.6, "tick": 0.3}     # the weight a tier's level is set for
HERO_W = {"hook": 1.0, "payoff": 1.15, "close": 1.5}                  # +0, +1.2 and +4 dB inside the hero tier: one heavy close
HERO_SPREAD = 4.0           # the hero tier's own reach over TIER_DB (the close; under a word `under` still caps it)
TIER_SPREAD = 2.0           # inside a tier the moment's weight moves the level this far either way
TIER_UNDER = {"hero": 0.0, "accent": 2.0, "body": None, "tick": None}  # under a word: never over the voice (None: the governor's margin)
TIER_PRESENT = {"hero": 12.0, "accent": 9.0, "body": 7.0, "tick": 4.0}  # how far over the voice the signal check lifts a tier toward
TIER_HEARD = {"hero": 6.0, "accent": 5.0, "body": 4.0, "tick": 2.5}     # under this the sound is not there and gives its place
TIER_LIFT = {"hero": 5.0, "accent": 4.0, "body": 3.0, "tick": 2.0}      # the most the check raises it (the hierarchy stays)
TIER_CEIL = {"hero": TIER_DB["hero"] + 4.0 + 1.0, "accent": TIER_DB["hero"] - 2.0,
             "body": TIER_DB["accent"] - 1.0, "tick": TIER_DB["body"] - 2.0}   # a lift never reaches the tier above
TIER_FLOOR_DB = 6.0         # the signal check may lower a sound this far under its tier, no further
ACCENT_W, BODY_W = 0.6, 0.35                                          # never an accent under this weight (sound_choice.weight_of), never a body under that
DISPLAY_W = 0.7             # the most a line of type weighs on its own
ACCENT_Q, TICK_Q = 0.2, 0.4                                           # the film's heaviest fifth are accents, its lightest two fifths ticks
PREROLL_S = 0.3             # a hero hit that sounds this long before its hit point takes no lead (it carries its own)
LEAD_UNDER_DB = 4.0         # a riser climbs to this far under its hit
LEAD_MAX_S = 2.5            # and climbs for this long at most: the references' risers run 1-2.5 s into the hit, never under a scene
DUCK_DB = 8.0               # the most the song is dipped under a hero hit (scene.audio.master reads the signal for how much)
BED_HEARD_DB = 3.0          # over a song, a body or tick hit that still changes the mix less than this in its band is taken out
# Density by section. Over the 28 reels the opening's first 3 s carry 1.56 times the film's own rate and the first 5 s
# 1.43 (owner's 8: a median of 6 sounds in the first 5 s); the middle breathes (deciles 3-5 at 0.67-0.90, 12% of 5 s
# windows empty); the stretch before the payoff builds (deciles 8-9 at 1.09 and 1.05); the last breath before the end
# card thins (0.81). SND2 spread one sound every ~2.8 s from first frame to last.
# SND3 14 Nov: the opening at the owner's 8 in absolute terms (6 in the first 5 s at our lower rate needs 2.2), the
# middle given up to it, the build a touch steeper (the check heard no build into the turn).
PROFILE = {"open": 2.2, "middle": 0.55, "build": 1.5, "after": 1.0, "tail": 0.0}
OPEN_MIN_S, OPEN_MAX_S, OPEN_SHARE = 4.5, 6.0, 0.15
BUILD_S = 6.0               # the build into the turn (into the close when no turn is named), at most a fifth of the film
BIN_S = 2.5                 # the stretches a section's share is spread over
# The tick layer: small clicks on type, counters and list bursts, under the audible layer (RATE_10S). On the study's
# ruler the references carry 7.0 sounds per 10 s (28 reels; 7.4 the owner's 8), of which TEARDOWN counts 3.6 as
# clearly audible over the bed: the rest are quiet ticks and sweeps under the voice.
TICK_10S = {"P": 2.4, "M": 1.0, "T": 0.0, "L": 0.0}
TICK_BURST = 2.5            # in any 10 s at most this many times the tick rate
GAP_S = 8.0                 # no stretch longer than this goes without a sound (the references' longest rests)
TICK_SPACE_S = 0.2          # any two sounds at least this far apart (the references' tenth percentile gap is 0.19 s)
OPEN_SPACE_S = 0.4          # inside the opening two audible sounds may sit this close (a click on each hook word and cut)
CUT_SHARE = 0.3             # after the opening at most this share sits on cuts (the references: 15-43%; SND2 64-83%),
CUT_FILL = 2.0              # unless the film has too little else: then the cuts fill what twice its type, graphics and moves cannot
HOOK_LEAD_S = 0.5           # the hook's first hit lands this late or later: a sweep flies into it (a designed opening, not one hit)
SNAP_S = 0.3                # the turn takes the landing this near the story's mark, as the music's drop does (music.SNAP_S)
ROTATE_HERO = 1.1           # the hero hits and risers (three a film, from a class of a dozen) turn wider: two films rarely land alike
ROTATE = 0.7                # inside a class the film's own seed chooses among the recordings near the reference shape (the
                            # class spreads 0.5 on the ruler), so two films share about half their audible recordings, not all

# Role targets, dB of the hit (K-weighted loudest 200 ms, manifest hit_lufs) against the voice. They began as sfx_mix.ROLE_MIX,
# judged on the showcase shorts (Oct 10), and moved on 13 Nov toward what the references carry (lanes/S study, 132 films,
# the same measure on their stems, medians under a word: click -6.9, hit -8.2, whoosh -7.7, impact -3.0): the owner's
# "they need to also be there". Each stays a step under its reference median: our recordings are dry and sit on no bed.
# "lo" is the floor the signal check may lower a sound to.
_SWEEP = {"target": -8.0, "lo": -12.5}
_TONAL = {"target": -8.5, "lo": -13.0}
_LOW = {"target": -10.5, "cap": -5.5, "lo": -14.0, "hp": True}
ROLE = {"whoosh": _SWEEP, "swish": _SWEEP, "riser": {"target": -7.0, "lo": -11.0},
        "ding": {"target": -10.0, "lo": -14.0}, "pop": _TONAL, "tick": _TONAL, "click": _TONAL, "shutter": _TONAL, "hit": _TONAL,
        "cash": {"target": -10.0, "lo": -14.0}, "glitch": {"target": -10.0, "lo": -14.0},
        "typing": {"target": -14.0, "lo": -18.0}, "impact": _LOW, "heartbeat": _LOW}
LEAD_DB = -2.0              # a lead sits under its hit
COUNTER_DB = -4.0           # ticks on a counter are texture, under one hit
NO_VOICE_LIFT = 8.0         # with nothing spoken, the "voice" a sound is set against is the track 8 dB up
RISER_TOP = -3.0            # a climb never comes nearer the voice than this (the references' risers sit at it; ours play dry)
RISER_HEARD_DB = 4.0        # a riser stands this far over what plays under it through the top of its climb, or it is taken out
                            # (a dB over the check's 3: its ruler reads to 16 kHz, ours to 8)
RISER_LIFT = 6.0            # the most a riser is raised to get there
# The references' levels were read off Demucs stems, and a stem gives back less of a sound that plays under a voice
# than was mixed in. Read back on the same ruler (lanes/S study.py), the sounds this engine placed in two re-mixed
# films measured a median 3.0 dB (17 sounds) and 2.9 dB (11) under the level they were mixed at. So the level a
# recording is mixed at is the references' reading plus that loss: then it reads where theirs do.
STEM_LOSS_DB = 3.0


def _class_mix():
    """Level by reference class, for recordings that joined by reference match: the class's own median against the
    voice under a word (sound_profiles, the study's 132 films) plus what the stem ruler loses (STEM_LOSS_DB), and a
    floor 3 dB under its lower quartile."""
    out = {}
    try:
        from . import sound_profiles as SP
        for k, c in SP.CLASSES.items():
            t, q = c.get("over_voice_spoken_db"), c.get("over_voice_db")
            if t is None:
                continue
            t = min(float(t), RISER_TOP) if k == "riser" else float(t) + STEM_LOSS_DB
            out[k] = {"target": t, "lo": min(t - 4.0, float(q[0]) - 3.0) if q else t - 6.0}
    except Exception:  # noqa: BLE001 - without the profiles the role table answers
        pass
    return out


CLASS_MIX = _class_mix()

# What answers what. A gesture is its layers, each (recording, part); every peak lands on the event.
# Alternates keep the one-recording rules without changing the gesture's kind.
ALT = {"whoosh_soft_2": "whoosh_soft_1", "whoosh_soft_1": "whoosh_soft_2", "shutter_1": "shutter_2",
       "shutter_2": "shutter_1", "riser_3": "riser_1", "riser_1": "riser_2", "riser_2": "riser_4",
       "swish_1": "whoosh_soft_2", "pop_1": "click_2", "click_2": "click_1", "glitch_1": "glitch_2"}
KIND_RANK = {"picture": 0, "burst": 0.5, "card": 1, "page": 1.5, "display": 2, "counter": 2.5, "move": 2.7, "flash": 3, "cut": 4}
MOVE_FAST, MOVE_STILL = 0.012, 0.004        # a camera move's start: 1.2% of the frame in one frame, after three under 0.4%
DESIGNED = ("picture", "card", "page", "display", "counter", "flash")
_PIECE = re.compile(r"\.p\d+$")


def gesture(ev, part):
    """The layers for one event. part: hook | payoff | plain."""
    k = ev["kind"]
    room = ev.get("room_s", 0.0)              # seconds before the event since the film's start or the last sound
    if part == "payoff":
        if room >= 2.0:
            return [("riser_3", "lead"), ("impact_1", "hit")]
        return [("riser_2", "lead"), ("impact_1", "hit")] if room >= 1.2 else [("impact_1", "hit")]
    if part == "hook" and k in ("display", "card", "page", "picture") and ev["f"] > 0:
        return [("riser_2", "lead"), ("impact_1", "hit")] if room >= 1.2 else [("impact_1", "hit")]
    if part == "hook" and k in ("display", "card", "page"):
        return [("impact_1", "hit")]
    if k == "picture":
        return [("shutter_1", "hit")] if ev.get("still") else [("whoosh_soft_2", "hit")]
    if k == "card":
        return [("whoosh_soft_1", "lead"), ("shutter_2", "hit")] if ev.get("photo") else [("pop_1", "hit")]
    if k == "page":
        return [("whoosh_soft_1", "hit")]
    if k == "display":
        return [("swish_1", "hit")]
    if k == "counter":
        return [("tick_1", "hit")]
    if k == "flash":
        return [("glitch_1", "hit")] if ev.get("glitch") else [("swish_1", "hit")]
    return [("whoosh_soft_2", "hit")]            # the upload's own cut


# ----------------------------------------------------------------- the library

_lib = {}


def library():
    if "t" not in _lib:
        try:
            rows = json.load(open(os.path.join(SOUNDS, "manifest.json")))
        except (OSError, ValueError):
            rows = []
        _lib["t"] = {r["id"]: r for r in rows if r.get("id") and r.get("file")}
    return _lib["t"]


def role_of(sid):
    return (library().get(sid) or {}).get("role") or "ding"


def class_of(sid):
    """The reference class a recording joined by (None for the first library)."""
    ref = (library().get(sid) or {}).get("reference")
    return ref.get("class") if isinstance(ref, dict) else None


def kind_of(sid):
    """What kind of sound this is to the ear's count of kinds: its class as the ruler hears it, else its role."""
    return measured_kind(sid) or role_of(sid)


def spec_of(sid):
    c = class_of(sid)
    return CLASS_MIX[c] if c in CLASS_MIX else ROLE.get(role_of(sid), _TONAL)


def gain_for(sid, ref, under=None, offset=0.0):
    """The gain that puts this recording's hit at its target against a voice at `ref` LUFS (its reference class's
    level, or its role's), then at least `under` dB under it when the voice speaks there."""
    x = library().get(sid) or {}
    sp = spec_of(sid)
    k = float(x.get("hit_lufs", -15.0))
    hp = x.get("hit_lufs_hp150")
    if sp.get("hp") and hp is not None:
        g = ref + sp["target"] - float(hp)
        g = min(g, ref + sp["cap"] - k)
    else:
        g = ref + sp["target"] - k
    g += offset
    if under is not None:
        g = min(g, ref - under - k)
    return round(min(GAIN_MAX, max(GAIN_MIN, g)), 1)


def floor_for(sid, ref):
    x = library().get(sid) or {}
    return ref + spec_of(sid)["lo"] - float(x.get("hit_lufs", -15.0))


def allowance(cls, np10, dur_s, bed, delivery_tail_s=0.0):
    """How many sounds a film of this supply carries, and how many in any 10 s: (total, in 10 s, the governor's row).
    The governor's allowance (taste.allow: what the picture supply earns) under the references' rate for the class."""
    al = dict(_allow(cls, np10, dur_s, bed))
    rate = min(float(RATE_10S.get(cls, 3.0)), float(al["sounds_10s"]) + 0.999) if al.get("sounds_10s") else float(RATE_10S.get(cls, 3.0))
    # A workflow is measured on its complete export, including its branded
    # tail. Budget those seconds here, but keep every event in the programme.
    budget_s = dur_s + max(0.0, float(delivery_tail_s))
    count = math.ceil(rate * budget_s / 10.0) if delivery_tail_s > 0 or dur_s < 10 else int(rate * dur_s / 10.0 + 1e-9)
    total = max(1, min(int(al["sounds"]), int(count)))
    return total, max(1, min(int(math.ceil(rate * BURST)), total)), al


# ----------------------------------------------------------------- audio helpers

_pcm_cache = {}


def pcm(path, rate=SR, ch=2, t0=None, dur=None):
    """Any file to float32 at `rate` (ch channels). Empty when it has no audio. Cached by path, mtime and cut."""
    try:
        key = (path, os.path.getmtime(path), rate, ch, t0, dur)
    except OSError:
        return np.zeros((0, ch), np.float32)
    if key in _pcm_cache:
        return _pcm_cache[key]
    cmd = [FF, "-hide_banner", "-nostdin", "-loglevel", "error"]
    if t0:
        cmd += ["-ss", f"{t0:.3f}"]
    cmd += ["-i", path]
    if dur:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-map", "0:a:0?", "-vn", "-ac", str(ch), "-ar", str(rate), "-f", "f32le", "pipe:1"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=600)
        x = np.frombuffer(r.stdout, np.float32).reshape(-1, ch).copy()
    except (OSError, subprocess.SubprocessError, ValueError):
        x = np.zeros((0, ch), np.float32)
    if len(_pcm_cache) > 48:
        _pcm_cache.clear()
    _pcm_cache[key] = x
    return x


def sample_pcm(sid, rate=AR):
    x = library().get(sid)
    if not x:
        return np.zeros(0, np.float32)
    a = pcm(os.path.join(SOUNDS, x["file"]), rate, 2)
    return a.mean(axis=1) if len(a) else np.zeros(0, np.float32)


def spine_audio(spine, fps, src, n, rate=AR, ch=1):
    """The programme's own sound: the spine in programme order (L1 spine entries {f: [a, b], src: [s0, s1]})."""
    a = pcm(src, rate, ch) if src and os.path.exists(src) else np.zeros((0, ch), np.float32)
    out = np.zeros((n, ch), np.float32)
    if not len(a):
        return out
    for e in spine or []:
        f0, f1 = e["f"]
        at = int(round(f0 / fps * rate))
        to = min(int(round(f1 / fps * rate)), n)
        s0 = int(round(float(e["src"][0]) * rate))
        seg = a[s0:s0 + max(0, to - at)]
        out[at:at + len(seg)] += seg
    return out


def _ffmpeg_raw(raw, rate, ch, af, extra=(), timeout=600):
    return subprocess.run([FF, "-hide_banner", "-nostdin", "-nostats", "-f", "f32le", "-ar", str(rate), "-ac", str(ch),
                           "-i", raw, "-af", af, *extra], capture_output=True, text=True, timeout=timeout)


def loudness_of(x, rate, tmp, name="lv"):
    """(integrated LUFS, true peak dBTP, LRA) of a float array, by ffmpeg's ebur128. Stereo as mixed."""
    x = np.asarray(x, np.float32)
    if x.ndim == 1:
        x = np.stack([x, x], axis=1)
    raw = os.path.join(tmp, f"{name}.f32")
    x.tofile(raw)
    r = _ffmpeg_raw(raw, rate, x.shape[1], "ebur128=peak=true", ("-f", "null", "-"))
    os.remove(raw)
    return _parse_summary(r.stderr)


def _parse_summary(t):
    def num(pat):
        m = re.findall(pat, t)
        try:
            return float(m[-1]) if m else None
        except ValueError:
            return None
    i = num(r"I:\s+(-?[0-9.]+|-inf) LUFS")
    tp = num(r"Peak:\s+(-?[0-9.]+|-inf) dBFS")
    lra = num(r"LRA:\s+(-?[0-9.]+) LU")
    return i, tp, lra


# ----------------------------------------------------------------- speech, in 20 ms frames

def frames_db(x):
    """Per 20 ms frame of a mono AR signal: (speech-band dB, all-band dB)."""
    n = len(x) // FRAME
    if n == 0:
        return np.zeros(0), np.zeros(0)
    seg = x[:n * FRAME].reshape(n, FRAME) * np.hanning(FRAME).astype(np.float32)
    sp = np.abs(np.fft.rfft(seg, axis=1)) ** 2
    hz = np.fft.rfftfreq(FRAME, 1.0 / AR)
    band = (hz >= SPEECH_BAND[0]) & (hz <= SPEECH_BAND[1])
    return 10 * np.log10(sp[:, band].sum(axis=1) + 1e-12), 10 * np.log10(sp.sum(axis=1) + 1e-12)


def speech_frames(voice, words_prog=None):
    """Spoken 20 ms frames. With a transcript (words_prog not None) its words decide, each held 0.05 s: an empty
    transcript is a film nobody speaks in, whatever its song sounds like. Without one, the signal's speech-shaped
    frames (speech band within 25 dB of the loud frames and half the frame's energy, edit_delta's test)."""
    b, a = frames_db(voice)
    n = len(b)
    m = np.zeros(n, bool)
    if words_prog is not None:
        for t0, t1 in words_prog:
            m[max(0, int((t0 - 0.05) * AR / FRAME)):min(n, int(math.ceil((t1 + 0.05) * AR / FRAME)))] = True
        return m, b
    if n and np.isfinite(b).any() and b.max() > -100:
        ref = np.percentile(b, 95)
        m |= (b > ref - 25.0) & (b - a > 10 * math.log10(0.5)) & (b > -90)
    k = 5
    if n:
        m = np.convolve(m.astype(np.float32), np.ones(2 * k + 1, np.float32), mode="same") > 0
    return m, b


def voice_shift(voice, speech):
    """dB that brings the programme's spoken frames to VOICE_LUFS, as the renderer's leveller will (0 when unknown)."""
    import tempfile
    try:
        m = np.zeros(len(voice), bool)
        k = min(len(speech) * FRAME, len(voice))
        m[:k] = np.repeat(speech, FRAME)[:k]
        with tempfile.TemporaryDirectory() as tmp:
            i, _tp, _lra = loudness_of(voice[m] if m.sum() > AR else voice, AR, tmp)
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0.0
    return VOICE_LUFS - i if i is not None and i > -70 else 0.0


# ----------------------------------------------------------------- picture events from an L1

def _kshots(perc, index):
    rows = []
    for s in (perc or {}).get("shots") or []:
        t = s.get("t")
        if t and len(t) == 2:
            rows.append((float(t[0]), float(t[1])))
    if not rows:
        for s in (index or {}).get("shots") or []:
            if s.get("start") is not None and s.get("end") is not None:
                rows.append((float(s["start"]), float(s["end"])))
    return sorted(rows)


def _kshot_of(ks, t):
    for i, (a, z) in enumerate(ks):
        if a - 1e-3 <= t < z:
            return i
    return None


def reframe_change(before, after):
    """Visible viewport discontinuity, using the two frames at the join.

    Continuous camera motion and a timing-only split are not sound events.
    This uses the same distinct-framing threshold as the picture engine.
    """
    if not before or before["f"][1] != after["f"][0]:
        return 0.0
    from scene.pace import distinct
    a = before.get("crop") or [0.0, 0.0, 1.0, 1.0]
    b = after.get("crop") or [0.0, 0.0, 1.0, 1.0]
    a = a[-1] if isinstance(a[0], (list, tuple)) else a
    b = b[0] if isinstance(b[0], (list, tuple)) else b
    if len(a) != 4 or len(b) != 4 or min(a[2]-a[0], a[3]-a[1], b[2]-b[0], b[3]-b[1]) <= 0:
        return 0.0
    if not distinct(a, b):
        return 0.0
    overlap = max(0.0, min(a[2], b[2])-max(a[0], b[0])) * max(0.0, min(a[3], b[3])-max(a[1], b[1]))
    union = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - overlap
    return round(1.0-overlap/union, 4)


def l1_events(l1, kshots=()):
    """What lands on screen, by programme frame: [{f, kind, ...}]. Distinct designed reframes are cuts;
    timing-only splits, continuous camera moves and captions are never cut events."""
    fps, D = float(l1["fps"]), int(l1["dur_f"])
    assets = l1.get("assets") or {}
    items = l1.get("items") or []
    shots = sorted((i for i in items if i.get("k") == "shot"), key=lambda i: i["f"][0])
    ev = []

    def key_of(s):
        p = str(s.get("pic"))
        if p == "plate":
            return ("framed", str(s["framed"])) if s.get("framed") else ("plate",)
        if p == "main" or p.startswith("own:"):
            return ("take", _kshot_of(kshots, float((s.get("src") or [0.0])[0])))
        return (p,)
    last, previous = None, None
    for s in shots:
        k = key_of(s)
        f = s["f"][0]
        if last is not None and k != last:
            if k == ("plate",) or last == ("plate",):
                ev.append({"f": f, "kind": "page", "id": s["id"]})
            elif k[0] == "take" and last[0] == "take":
                ev.append({"f": f, "kind": "cut", "id": s["id"]})
            else:
                a = assets.get(str(s.get("pic"))) or {}
                ev.append({"f": f, "kind": "picture", "id": s["id"], "still": a.get("kind") == "image" or k[0] == "framed"})
        elif last is not None and k[0] == "take":
            change = reframe_change(previous, s)
            if change:
                ev.append({"f": f, "kind": "cut", "id": s["id"], "designed": True, "change": change})
        last, previous = k, s
        if k[0] == "take" and s.get("src") and kshots:      # the upload's own cuts inside one shot of it
            a0, z0 = float(s["src"][0]), float(s["src"][1])
            n = max(1, s["f"][1] - f)
            for i, (ka, _kz) in enumerate(kshots):
                if a0 + 0.05 < ka < z0 - 0.05:
                    ev.append({"f": f + int(round((ka - a0) / max(z0 - a0, 1e-6) * n)), "kind": "cut", "id": s["id"]})
                    last = ("take", i)
    cuts = {s["f"][0] for s in shots}
    seen_on, counters = set(), {}
    for it in items:
        k, iid, f = it.get("k"), str(it.get("id")), it["f"][0]
        if k == "card":
            area = float(it.get("w") or 0) * float(it.get("h") or 0) if not isinstance(it.get("w"), list) else 0.2
            photo = it.get("pic") not in (None, "plate")
            ev.append({"f": f, "kind": "card" if (photo or area >= 0.08) else "display", "id": iid, "photo": photo, "area": area})
        elif k == "word" and it.get("role") == "big":
            if ".counter" in iid:
                g = iid.split(".counter")[0] + ".counter"
                counters.setdefault(g, []).append((f, str(it.get("t") or "")))
                continue
            if _PIECE.search(iid):
                continue
            on = int(it.get("on", f))
            if any(abs(on - o) <= 3 for o in seen_on):
                continue
            seen_on.add(on)
            ev.append({"f": on, "kind": "display", "id": iid})
        elif k == "flash" and it.get("mode") not in ("pool", "recolour") and not iid.endswith((".bar", ".pool")) \
                and ".hl" not in iid and any(abs(f - c) <= 1 for c in cuts):
            ev.append({"f": f, "kind": "flash", "id": iid, "glitch": "glitch" in iid})
    for g, rows in counters.items():
        rows.sort()
        steps, prev = [], None
        for f, t in rows:
            if t != prev:
                steps.append(f)
            prev = t
        ev.append({"f": rows[0][0], "kind": "counter", "id": g, "steps": steps[:COUNTER_TICKS]})
    moving = {it["f"][0] for it in items if it.get("k") in ("shot", "card") and isinstance(it.get("blur"), list) and len(it["blur"]) > 1}
    for e in ev:
        if e["kind"] in ("picture", "cut") and e["f"] in moving:
            e["moving"] = True                    # the cut travels (a whip, a slide): the picture itself asks for a sweep
    for s in shots:                               # a fast camera move inside a shot (a punch-in, a whip): its start is a moment
        ev += [{"f": f, "kind": "move", "id": s["id"]} for f in move_starts(s)]
    out = []
    for e in sorted(ev, key=lambda e: (e["f"], KIND_RANK.get(e["kind"], 9))):
        if 0 <= e["f"] < D and not any(abs(e["f"] - o["f"]) <= 2 for o in out):
            out.append(e)
        elif out and abs(e["f"] - out[-1]["f"]) <= 2 and KIND_RANK.get(e["kind"], 9) < KIND_RANK.get(out[-1]["kind"], 9):
            out[-1] = e
    return out


def move_starts(s):
    """The frames inside a shot where its camera starts a fast move: the crop's size or centre moves MOVE_FAST of the
    frame or more in one frame after holding still (under MOVE_STILL) for three. A slow push is not a moment; the
    references' moves peak in the first third of their shot and settle (TEARDOWN section 8)."""
    crop = s.get("crop")
    if not isinstance(crop, list) or not crop or not isinstance(crop[0], (list, tuple)) or len(crop) < 6:
        return []
    out, still = [], 0
    for i in range(1, len(crop)):
        a, b = crop[i - 1], crop[i]
        w0, w1 = max(a[2] - a[0], 1e-6), max(b[2] - b[0], 1e-6)
        v = max(abs(w1 / w0 - 1.0), abs((b[0] + b[2]) - (a[0] + a[2])) / 2.0 / w0, abs((b[1] + b[3]) - (a[1] + a[3])) / 2.0 / w0)
        if v >= MOVE_FAST and still >= 3 and i >= 3 and (not out or i - (out[-1] - s["f"][0]) > 15):
            out.append(s["f"][0] + i)
        still = still + 1 if v < MOVE_STILL else 0
    return out


def words_in_programme(l1, index):
    """Spoken words in programme seconds, through the spine."""
    fps = float(l1["fps"])
    out = []
    if not index or index.get("words") is None:
        return None
    words = index.get("words") or []
    for e in l1.get("spine") or []:
        s0, s1 = float(e["src"][0]), float(e["src"][1])
        p0 = e["f"][0] / fps
        for w in words:
            t0, t1 = float(w.get("t0", 0)), float(w.get("t1", 0))
            if t1 > s0 and t0 < s1:
                out.append((p0 + max(t0, s0) - s0, p0 + min(t1, s1) - s0))
    return sorted(out)


def has_bed(perc, n_words, src_dur):
    """taste.has_bed, from the perception alone: a track under most of the film that is not a voice."""
    a = (perc or {}).get("audio") or {}
    bins = a.get("rms_db") or []
    if not bins or n_words / max(src_dur, 1e-3) >= 1.0:
        return False
    top = sorted(bins)[int(len(bins) * 0.9)]
    return sum(1 for b in bins if b >= top - 20.0) >= 0.5 * len(bins) and top > -45.0


# ----------------------------------------------------------------- the plan

def _allow(cls, np10, dur_s, bed):
    try:
        from . import taste
        return taste.allow({"cls": cls, "np10": np10, "dur_s": dur_s, "bed": bed})
    except Exception:  # noqa: BLE001 — the numbers below are TASTE 4.2's
        snd = {"T": 1.5 if bed else 0.6, "M": 2.5 if bed else 1.5, "L": 1.2}.get(cls, min(0.9 * np10, 8.0 if bed else 6.0))
        return {"sounds": max(1, int(snd * dur_s / 10.0 + 1e-9)), "sounds_10s": max(1, int(snd)),
                "roles": {"T": 3, "M": 3, "P": 4, "L": 2}[cls], "under_voice_db": {"T": 9.0, "M": 6.0, "P": 4.5, "L": 12.0}[cls]}


def klass(np10, dur_s):
    return "L" if dur_s > 240 else "T" if np10 < 1 else "M" if np10 < 5 else "P"


def _gestures(times):
    """Sound times grouped into gestures (MERGE_S)."""
    out = []
    for t in sorted(times):
        if out and t - out[-1][-1] < MERGE_S:
            out[-1].append(t)
        else:
            out.append([t])
    return [g[0] for g in out]


def bursts(events, fps):
    """Runs of quick cuts: [[event, ...]] of TRAIN_MIN or more new pictures, each inside TRAIN_GAP_S of the one before."""
    pics = sorted((e for e in events if e["kind"] in ("picture", "cut")), key=lambda e: e["f"])
    runs, run = [], []
    for e in pics:
        if run and (e["f"] - run[-1]["f"]) / fps <= TRAIN_GAP_S:
            run.append(e)
        else:
            if len(run) >= TRAIN_MIN:
                runs.append(run)
            run = [e]
    if len(run) >= TRAIN_MIN:
        runs.append(run)
    return runs


# ----------------------------------------------------------------- SND3: the references' shape

_mk = {}


def measured_kind(sid):
    """The class the study's ruler hears in this recording (sound_signal.describe on its own samples, the ruler the
    references were read on), once per recording. The manifest's reference class is what the recording matched when
    it joined; a slot is filled by what the recording sounds like (SND2's braam_2 joined as a whoosh and reads as an
    impact; hit_1 joined as a hit and reads as a whoosh)."""
    x = library().get(sid) or {}
    key = (sid, x.get("sha"), x.get("file"))
    if key not in _mk:
        k = None
        try:
            from . import sound_signal as SS
            a = sample_pcm(sid, SS.SR)
            d = SS.describe(a) if len(a) else None
            k = d.get("kind") if d else None
        except Exception:  # noqa: BLE001 - a recording the ruler cannot read keeps the class it joined by
            k = None
        _mk[key] = k or class_of(sid)
    return _mk[key]


def measured(desc):
    """The library's descriptions with each recording's class as the ruler hears it."""
    return {sid: dict(d, kind=measured_kind(sid) or d.get("kind")) for sid, d in (desc or {}).items()}


def film_seed(*parts):
    """A number that names one film: the same film always draws the same recordings, two films draw differently."""
    return zlib.crc32(json.dumps(parts, default=str, sort_keys=True).encode("utf-8")) & 0x7FFFFFFF


def _u(seed, sid):
    """A uniform draw in [0, 1) named by the film and the recording. A real hash: CRC32 is linear, so two films' draws
    for recordings of one name length moved together (both proof films closed on impact_10 and riser_11)."""
    h = hashlib.blake2b(f"{seed}:{sid}".encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(h, "big") / float(1 << 64)


def opening_end(dur_s):
    return min(max(OPEN_MIN_S, OPEN_SHARE * dur_s), OPEN_MAX_S, 0.4 * dur_s)


def section_of(t, dur_s, turn_s, close_s):
    """Which part of the film's shape t sits in: open | middle | build | after | tail."""
    b = min(BUILD_S, 0.2 * dur_s)
    if close_s is not None and t > close_s + 0.05:
        return "tail"
    if t < opening_end(dur_s):
        return "open"
    goal = turn_s if turn_s is not None else close_s
    if goal is not None and goal - b <= t < goal:
        return "build"
    if turn_s is not None and t >= turn_s:
        return "after"
    return "middle"


def section_targets(dur_s, total, turn_s, close_s, bin_s=None):
    """[(t0, t1, section, target)]: the film's sounds spread by the references' shape (PROFILE): a dense opening,
    breathing in the middle, a build into the turn (the payoff when no turn is named), the rest after it, nothing
    after the close."""
    bin_s = bin_s or BIN_S
    nb = max(1, int(math.ceil(dur_s / bin_s - 1e-9)))
    rows = []
    for k in range(nb):
        a, b = k * bin_s, min(dur_s, (k + 1) * bin_s)
        sec = section_of((a + b) / 2, dur_s, turn_s, close_s)
        rows.append([a, b, sec, PROFILE.get(sec, 0.0) * (b - a)])
    s = sum(r[3] for r in rows) or 1.0
    return [(a, b, sec, total * w / s) for a, b, sec, w in rows]


def tiers_for(cands):
    """The film's own weight ladder: the weights (sound_choice.weight_of) at which its moments become accents and at
    which they fall to ticks. Ranked inside the film (its heaviest ACCENT_Q are accents, its lightest TICK_Q ticks), so
    every film spreads its levels as the references do, whatever its pictures weigh; never an accent under ACCENT_W
    nor a body under BODY_W."""
    ws = sorted(_weight(e) for e in cands if e["kind"] not in ("counter", "burst"))
    if len(ws) < 4:
        return ACCENT_W, BODY_W
    hi = max(ACCENT_W, float(np.percentile(ws, 100 * (1 - ACCENT_Q))))
    lo = max(BODY_W, float(np.percentile(ws, 100 * TICK_Q)))
    return hi, min(lo, hi)


def tier_of(e, part, ladder=None):
    """How heavy this sound is in the film's mix: hero (the hook, the turn, the close), accent, body or tick. What the
    picture does there decides it (the share of the frame that changed, a card's size, against the film's own
    ladder), never what anyone called it."""
    if part in ("hook", "payoff", "close"):
        return "hero"
    k = e["kind"]
    if k in ("counter", "burst"):
        return "tick"
    if k == "move":
        return "body"
    hi, lo = ladder or (ACCENT_W + 0.15, BODY_W)
    w = _weight(e)
    return "accent" if w >= hi else "body" if w > lo else "tick"


def _weight(e):
    if e["kind"] == "move":
        return 0.55
    w = choice.weight_of(e, "plain")
    return min(w, DISPLAY_W) if e["kind"] == "display" else w       # type alone is the body's or a tick's; with a new picture it is a reveal


def on_cut(e):
    return e["kind"] in ("picture", "cut", "burst", "page")


def gain_tier(sid, ref, tier, w, under=None, lead=False):
    """The gain that puts this recording's hit (K-weighted loudest 200 ms, manifest hit_lufs) where its tier sits against
    the voice: TIER_DB, moved inside the tier by the moment's weight (TIER_SPREAD), a lead (a riser into a hero hit)
    under its hit and never nearer the voice than RISER_TOP. Under a word at least `under` dB under the voice."""
    x = library().get(sid) or {}
    k = float(x.get("hit_lufs", -15.0))
    if lead:
        t = min(TIER_DB[tier] - LEAD_UNDER_DB, RISER_TOP)
    else:
        top = HERO_SPREAD if tier == "hero" else TIER_SPREAD
        t = TIER_DB[tier] + max(-TIER_SPREAD, min(top, (float(w) - TIER_MID[tier]) * 8.0))
    g = ref + t - k
    if under is not None:
        g = min(g, ref - under - k)
    return round(min(GAIN_MAX, max(GAIN_MIN, g)), 1)


def tier_ceiling(sid, ref, tier):
    """The most a lift may raise this recording: never into the tier above (TIER_CEIL), so an accent that a pause
    lets rise still sits under the film's hero hits and the hierarchy holds whatever the bed does."""
    x = library().get(sid) or {}
    return min(GAIN_MAX, ref + TIER_CEIL.get(tier, 99.0) - float(x.get("hit_lufs", -15.0)))


def tier_floor(sid, ref, tier, lead=False):
    x = library().get(sid) or {}
    t = min(TIER_DB[tier] - LEAD_UNDER_DB, RISER_TOP) if lead else TIER_DB[tier]
    return ref + t - TIER_FLOOR_DB - float(x.get("hit_lufs", -15.0))


def close_time(events_s, words, end_s, breath):
    """Where the film closes: the first landing after the last word, early enough to ring out before the end card
    (the music engine's own rule for its last hit, music.marks_from_l1, so the song's last hit and this one are the
    same frame); with no landing there, a breath after the last word (music.end_hit_time) when `breath` allows a close
    with nothing new on screen. None when the film gives nowhere to close."""
    last = max((b for _a, b in words or []), default=None)
    if last is None:
        return None, None
    after = [t for t in events_s if last - 0.1 <= t <= end_s - 0.7]
    if after:
        return min(after), "landing"
    t = last + 0.18
    if breath and t <= end_s - END_GUARD_S:
        return t, "breath"
    return None, None


def plan_sounds(events, ctx):
    """The core. events: [{f, kind, ...}] in programme frames. ctx: fps, dur_f, existing (sound items already in the
    film), speech (bool per 20 ms frame) and voice (mono AR programme voice) or None, words (programme seconds),
    bed, prog_end_f, np10 (or None: counted from the events), turn_f (the story's turn, when the caller knows its
    frame), close_s (where the film closes, when the caller has read it as the music will), seed (names the film).
    Returns (sound items, report)."""
    fps, D = float(ctx["fps"]), int(ctx["dur_f"])
    end_f = int(ctx.get("prog_end_f") or D)
    dur_s = end_f / fps
    lib = library()
    pics = [e for e in events if e["kind"] in ("picture", "cut") or (e["kind"] in ("card",) and e.get("area", 0) >= 0.35)]
    np10 = ctx.get("np10")
    if np10 is None:
        np10 = len(pics) / max(dur_s, 1e-3) * 10
    cls = klass(np10, dur_s)
    bed = bool(ctx.get("bed"))
    total, win, al = allowance(cls, np10, dur_s, bed, ctx.get("delivery_tail_s", 0.0))
    gov = int(al["sounds"])
    # The tick layer is its own layer, under the audible one (round seven: capped by what the governor left of the
    # audible allowance, a film of some pictures carried none, and every display line of a Director film went unsounded:
    # "the film carries 0 small ticks" on 4 of 4 lines in p5574 and p5565).
    n_tick = max(0, int(TICK_10S.get(cls, 0.0) * dur_s / 10.0 + 1e-9))
    tick_win = max(1, int(math.ceil(TICK_10S.get(cls, 0.0) * TICK_BURST))) if n_tick else 0
    al["sounds"], al["sounds_10s"] = total, win
    seed = ctx.get("seed")
    if seed is None:
        seed = film_seed(D, [(e["f"], e["kind"]) for e in events][:400])
    report = {"class": cls, "np10": round(np10, 2), "bed": bed, "allow": total, "allow_10s": win, "allow_ticks": n_tick,
              "events": len(events), "placed": [], "body": [], "ticks": [], "dropped": [], "kept_director": len(ctx.get("existing") or []),
              "seed": seed, "budget_duration_s": dur_s + float(ctx.get("delivery_tail_s", 0.0))}
    existing = list(ctx.get("existing") or [])
    ex_t = [float(s.get("event_f", 0)) / fps for s in existing]
    speech = ctx.get("speech")
    words = ctx.get("words") or []

    def spoken(t, a=-0.1, b=0.25):
        if speech is None or not len(speech):
            return any(w0 < t + b and w1 > t + a for w0, w1 in words)
        i0, i1 = max(0, int((t + a) * AR / FRAME)), min(len(speech), int((t + b) * AR / FRAME) + 1)
        return bool(speech[i0:i1].any())

    try:
        desc = measured(choice.describe_library(lib, SOUNDS, pcm))
    except Exception:  # noqa: BLE001 - without the ruler the fixed table still answers
        desc = {}
    used = {s.get("s") for s in existing if s.get("s") in lib}         # one recording answers one gesture in a film
    tally = {}
    for s in existing:
        k = (desc.get(s.get("s")) or {}).get("kind")
        if k:
            tally[k] = tally.get(k, 0) + 1

    # ---- candidates: what lands on screen, never in the last breath, never on the Director's own sounds
    cands = [e for e in events if e["f"] < end_f - int(END_GUARD_S * fps)
             and (e["f"] > 1 or e["kind"] in ("display", "card", "page", "counter"))
             and not any(abs(e["f"] / fps - t) <= 3.0 / fps for t in ex_t)]
    trains, last_train = [], -1e9
    for run in bursts(cands, fps):
        if run[0]["f"] / fps - last_train < TRAIN_EVERY_S:
            continue
        last_train = run[0]["f"] / fps
        run = run[:TRAIN_MAX]
        still = sum(1 for e in run if e.get("still")) * 2 > len(run)
        trains.append({"f": run[0]["f"], "kind": "burst", "id": run[0].get("id"), "steps": [e["f"] for e in run], "still": still,
                       "end_f": run[-1]["f"]})
        inside = {id(e) for e in run}
        cands = [e for e in cands if id(e) not in inside]
    cands = sorted(cands + trains, key=lambda e: e["f"])

    # ---- the three hero landings: the hook, the turn, the close
    lands = [e for e in cands if e["kind"] in ("picture", "cut", "card", "page", "display", "counter", "flash")]
    hook = next((e for e in cands if 1 < e["f"] < HOOK_S * fps and e["kind"] in ("picture", "card", "page") and _weight(e) >= 0.45), None) \
        or next((e for e in cands if e["f"] < HOOK_S * fps and e["kind"] in DESIGNED), None)
    close_s, close_by = (ctx.get("close_s"), "caller") if ctx.get("close_s") is not None else \
        close_time([e["f"] / fps for e in events if e["kind"] in DESIGNED + ("cut", "move")], words, dur_s, bed)
    close = None
    if close_s is not None:
        near = [e for e in cands if abs(e["f"] / fps - close_s) <= 2.5 / fps and e is not hook]
        close = min(near, key=lambda e: abs(e["f"] / fps - close_s)) if near else None
        if close is None and close_s * fps < end_f - int(END_GUARD_S * fps):
            close = {"f": int(round(close_s * fps)), "kind": "close"}
            cands.append(close)
    if close is None:                                      # no word to close after: the last designed landing of the film
        late = [e for e in cands if e["f"] >= PAYOFF_FROM * end_f and e["kind"] in ("display", "card", "page", "picture", "counter")]
        close = max(late, key=lambda e: e["f"]) if late else None
        close_by = "the last landing" if close is not None else None
    close_t = close["f"] / fps if close is not None else None
    turn = None
    if ctx.get("turn_f") is not None:
        tf = int(ctx["turn_f"])
        near = [e for e in lands if abs(e["f"] - tf) <= SNAP_S * fps and e is not hook]      # the music's own snap: the drop lands there
        turn = min(near, key=lambda e: abs(e["f"] - tf)) if near else None
        if turn is None and 0 < tf < end_f - int(END_GUARD_S * fps):
            turn = {"f": tf, "kind": "turn"}
            cands.append(turn)
    elif close is not None and cls in ("P", "M"):          # nobody named the turn: the heaviest landing of the second half is it
        late = [e for e in lands if 0.5 * close["f"] <= e["f"] <= close["f"] - 2.0 * fps and e is not hook]
        if late:
            turn = max(late, key=lambda e: (_weight(e), e["f"]))
    if turn is not None and close is not None and abs(turn["f"] - close["f"]) / fps < 1.5:
        if turn["kind"] != "turn" or close["kind"] == "close":
            close = turn                                   # the turn is the close: one landing, not two
            close_t = close["f"] / fps
        turn = None
    turn_t = turn["f"] / fps if turn is not None else None
    cands = sorted(cands, key=lambda e: e["f"])
    ladder = tiers_for([e for e in cands if not any(e is x for x in (hook, turn, close))])
    open_s = opening_end(dur_s)
    report["shape"] = {"open_s": round(open_s, 2), "ladder": [round(ladder[0], 3), round(ladder[1], 3)], "turn_s": round(turn_t, 3) if turn_t is not None else None,
                       "close_s": round(close_t, 3) if close_t is not None else None, "close_by": close_by}

    # ---- the sections: how many sounds each part of the film carries
    want = total + n_tick
    bins = section_targets(dur_s, want, turn_t, close_t)
    nb = len(bins)
    bin_s = bins[0][1] - bins[0][0] if bins else BIN_S

    def bin_of(t):
        return max(0, min(nb - 1, int(t / max(bin_s, 1e-6))))
    sec_target = {}
    for a, b, sec, tg in bins:
        sec_target[sec] = sec_target.get(sec, 0.0) + tg
    # each layer keeps the shape on its own: the audible sounds and the ticks are each spread by the sections' shares, so
    # the opening is not left with ticks alone once the rest of the film has spent the audible allowance
    lay_cap = {(sec, lay): int(math.floor(tg * n / max(want, 1) + 0.5)) or (1 if tg * n / max(want, 1) >= 0.34 else 0)
               for sec, tg in sec_target.items() for lay, n in (("aud", total), ("tick", n_tick))}
    in_lay = {}
    quota = {"on": True}
    pool = {}
    structural = [e for e in (hook, turn, close) if e is not None]
    for e in cands:
        if not any(e is x for x in structural):
            pool.setdefault(bin_of(e["f"] / fps), []).append(e)
    for rows in pool.values():                 # new graphics and type first, then the bigger picture change, a breath before a word
        rows.sort(key=lambda e: (e["kind"] != "burst", 1 if on_cut(e) else 0, -_weight(e), 1 if spoken(e["f"] / fps) else 0, e["f"]))
    in_bin = [0] * nb
    in_sec = {}
    n_else = sum(1 for e in cands if not on_cut(e) and not any(e is x for x in structural))
    cut_budget = max(int(math.ceil(CUT_SHARE * want)), want - int(CUT_FILL * n_else))
    report["cut_budget"] = cut_budget
    state = {"aud": [], "tick": [], "all": [], "busy": [], "cut_items": 0, "items": 0, "own_cut": 0, "own_t": []}
    placed = []
    # The Director's own library sounds are the film's own gestures (the impact under the hook title, the riser into
    # the payoff's hit): they take their place in the one hierarchy. Round seven: they were set at the first library's
    # role levels (an impact 10.5 dB under the voice) while the pass's own hook hit sits at the voice, so the film's
    # designed opening and close were its quietest sounds, and nothing else could land where they stood.
    own = sorted((s for s in existing if s.get("s") in lib), key=lambda s: float(s.get("event_f", 0)))
    hits_own = [s for s in own if role_of(s["s"]) != "riser"]
    dir_hook = dir_close = False
    for s in own:
        t = float(s.get("event_f", 0)) / fps
        ref = ctx["ref_at"](t) if callable(ctx.get("ref_at")) else VOICE_LUFS
        was = s.get("db")
        if not ctx.get("own_tiers"):             # a caller that sets its own sounds' place (the workflow shell): the role levels, as before
            s["db"] = gain_for(s["s"], ref, al["under_voice_db"] if spoken(t) else None)
            s["director_db"] = was
            placed.append({"t": t, "ev": {"kind": s.get("for") or "director", "f": s.get("event_f", 0)}, "part": "director", "tier": "body",
                           "items": [dict(s, layer="hit", **({"for": "director"} if "for" not in s else {}))], "ref": ref, "director": True})
            state["aud"].append(t)
            state["all"].append(t)
            continue
        lead = role_of(s["s"]) == "riser"
        nxt = next((h for h in hits_own if 0 <= float(h.get("event_f", 0)) / fps - t <= 1.5), None) if lead else s
        tn = float(nxt.get("event_f", 0)) / fps if nxt is not None else t
        if nxt is not None and hits_own and nxt is hits_own[0] and tn <= HOOK_S:
            tier, part_d, dir_hook = "hero", "hook", True
        elif nxt is not None and hits_own and nxt is hits_own[-1] and tn >= PAYOFF_FROM * dur_s:
            tier, part_d, dir_close = "hero", "close", True
        else:
            tier, part_d = "accent", "plain"
        under = TIER_UNDER[tier] if spoken(t) else None
        s["db"] = gain_tier(s["s"], ref, tier, HERO_W.get(part_d, 1.0) if tier == "hero" else TIER_MID["accent"], under, lead=lead)
        s["director_db"] = was
        s["tier"] = tier
        if lead:
            s["tier"] = "accent"                      # a climb is never raised like its hit: it stays under it
        it = dict(s, layer="hit", **({"for": "director"} if "for" not in s else {}))
        if tier == "hero" and not lead:
            it["duck_db"] = DUCK_DB
        try:
            if it.get("start_s") is not None and not lead:
                _heard([it], ctx, ref, under, True, tier)
        except Exception:  # noqa: BLE001 - unread: the tier's own level stands
            pass
        placed.append({"t": t, "ev": {"kind": s.get("for") or "director", "f": s.get("event_f", 0)}, "part": "director", "tier": tier,
                       "items": [it], "ref": ref, "director": True})
        state["aud"].append(t)
        state["all"].append(t)
    own_cap = max(1, want)
    own_spacing = max(SPACE_S, 10.0 / max(RATE_10S.get(cls, 3.6), 0.1))
    st = {"desc": desc, "seed": seed, "tally": tally, "used": used, "spoken": spoken, "cls_at": [], "open_s": open_s}

    def refuse(e, why):
        report["dropped"].append({"f": e["f"], "kind": e["kind"], "why": why})
        return False

    def attempt(e, part):
        """Place one event's sound if the rules, the library and the voice allow it. True when it was placed."""
        e0 = e
        t = e["f"] / fps
        tier = tier_of(e, part, ladder)
        if tier in ("accent", "body") and not on_cut(e) and len(state["tick"]) < n_tick and (
                len(state["aud"]) >= cap["aud"] or any(sum(1 for g in state["aud"] + [t] if w0 <= g < w0 + 10.0) > win
                                                       for w0 in [g for g in state["aud"] + [t] if t - 10.0 < g <= t])):
            tier = "tick"                             # type, a card or a move past the audible amount still lands, as a quiet tick
        layer = "tick" if tier == "tick" else "aud"
        hero = tier == "hero"
        steps = e.get("steps") if e["kind"] in ("counter", "burst") else None
        n_items = len(steps) if (steps and e["kind"] == "burst") else 1
        if close_t is not None and t > close_t + 1e-6:
            return refuse(e, "after the close: the film's last hit is its last sound")
        if layer == "aud" and len(state["aud"]) >= cap["aud"]:
            return refuse(e, f"the footage carries {total} (class {cls}{', a bed' if bed else ''})")
        if layer == "tick" and len(state["tick"]) >= n_tick:
            return refuse(e, f"the film carries {n_tick} small ticks")
        sec_e = section_of(t, dur_s, turn_t, close_t)
        if quota["on"] and not hero and e["kind"] != "burst" and in_lay.get((sec_e, layer), 0) >= lay_cap.get((sec_e, layer), 0):
            return False                                  # held for the second pass (spare), logged there if it never sounds
        if e["kind"] == "cut" and not e.get("designed") and (state["own_cut"] >= own_cap or any(abs(u - t) < own_spacing for u in state["own_t"])):
            return refuse(e, "the upload's own cuts already hold their share")
        if on_cut(e) and not hero and t >= open_s and state["cut_items"] + n_items > cut_budget:
            return refuse(e, f"the cuts already hold their share ({cut_budget}): the references sound type, graphics and moves too")
        end_t = e["end_f"] / fps if e.get("end_f") else t
        if any(a - TICK_SPACE_S < t < b + TICK_SPACE_S or a - TICK_SPACE_S < end_t < b + TICK_SPACE_S for a, b in state["busy"]):
            return refuse(e, "inside a train or a climb")
        if any(abs(t - g) < TICK_SPACE_S for g in state["all"]) or (e["kind"] == "burst" and any(t - TICK_SPACE_S < g < end_t + TICK_SPACE_S for g in state["all"])):
            return refuse(e, f"another sound is inside {TICK_SPACE_S:g} s")
        space = OPEN_SPACE_S if t < open_s else SPACE_S
        if layer == "aud" and not hero and any(abs(t - g) < space for g in state["aud"]):
            return refuse(e, f"another sound is inside {space:g} s")
        if layer == "aud" and any(sum(1 for g in state["aud"] + [t] if w0 <= g < w0 + 10.0) > win for w0 in [g for g in state["aud"] + [t] if t - 10.0 < g <= t]):
            return refuse(e, f"more than {win} inside 10 s")
        if layer == "tick" and any(sum(1 for g in state["tick"] + [t] if w0 <= g < w0 + 10.0) > tick_win for w0 in [g for g in state["tick"] + [t] if t - 10.0 < g <= t]):
            return refuse(e, f"more than {tick_win} ticks inside 10 s")
        prev = max([g for g in state["all"] if g < t], default=0.0)
        e = dict(e, room_s=t - prev)
        if e["kind"] == "burst":
            group, voices = _train(e, t, st, ctx, al, spoken, fps, len(placed))
            if not group:
                return refuse(e, "no click left for a train")
            placed.append({"t": t, "ev": e, "part": "plain", "tier": "tick", "items": group, "ref": VOICE_LUFS, "class": "click", "train": len(group)})
            used.update(voices)
            tally["click"] = tally.get("click", 0) + 1
        else:
            ref = ctx["ref_at"](t) if callable(ctx.get("ref_at")) else VOICE_LUFS
            hidden, group, kcls, layers = None, [], None, []
            # Round seven: a hit that still sat in the voice at its tier's floor was planned, then taken out by the last
            # signal check, and the moment went silent (the turn of p5574, three pictures of p5565). It is given another
            # recording, then the tier under it: a landing under a word is a quieter landing, never no landing.
            for tier_now in {"hero": ("hero", "accent", "body"), "accent": ("accent", "body")}.get(tier, (tier,)):
                part_now = part if tier_now == "hero" else "plain" if tier != tier_now else part
                hero_now = tier_now == "hero"
                under = (TIER_UNDER[tier_now] if TIER_UNDER[tier_now] is not None else al["under_voice_db"]) if spoken(t) and tier_now != "tick" else None
                w = HERO_W.get(part_now, 1.0) if hero_now else _weight(e)     # the close is the film's heaviest hit, the turn next
                avoid = set()
                for _ in range(TRIES):
                    layers, kcls = _layers_for(e, part_now, tier_now, t, st, avoid)
                    if not layers:
                        break
                    group = []
                    if steps:                               # a counter: one tick a step, the roll one gesture
                        sid = layers[-1][0]
                        for sf in steps:
                            group.append(_item(f"snd.j{len(placed)}.{len(group)}", sid, sf, fps, gain_tier(sid, ref, "tick", 0.2, under), e, part_now, "hit"))
                    else:
                        for sid, what in layers:
                            group.append(_item(f"snd.j{len(placed)}.{len(group)}", sid, e["f"], fps,
                                               gain_tier(sid, ref, tier_now, w, under, lead=what == "lead"), e, part_now, what))
                    for it in group:
                        it["tier"] = tier_now
                        if hero_now and it["layer"] == "hit":
                            it["duck_db"] = DUCK_DB          # the song makes room under a hero hit (scene.audio.master)
                    hidden = _heard(group, ctx, ref, under, hero_now, tier_now)
                    if not hidden or steps:
                        break
                    avoid |= {sid for sid, what in layers if what == "hit"}
                if layers and not hidden:
                    break
            if tier_now != tier:
                tier, part, hero = tier_now, part_now, hero_now
            if not layers:
                return refuse(e, hidden or "no recording of its class is left unused in this film")
            if hidden:
                return refuse(e, hidden)
            placed.append({"t": t, "ev": e, "part": part, "tier": tier, "items": group, "ref": ref, "class": kcls})
            if layer == "aud" and kcls and not hero:
                st["cls_at"].append((t, kcls))
            used.update(it["s"] for it in group)
            if kcls and not hero:                       # the class balance is the body's: the three landings are impacts by design
                tally[kcls] = tally.get(kcls, 0) + 1
            leads = [it for it in group if it["layer"] == "lead"]
            if leads:                                   # a climb is the hero's: nothing else sounds inside it
                climb = max(float(it["peak_s"]) - float(it.get("trim_s", 0)) for it in leads)
                state["busy"].append((t - climb, t - TICK_SPACE_S))
        (state["tick"] if layer == "tick" else state["aud"]).append(t)
        state["all"].append(t)
        if e["kind"] == "burst":
            state["busy"].append((t, end_t))
        if e["kind"] == "cut" and not e.get("designed"):
            state["own_cut"] += 1
            state["own_t"].append(t)
        state["items"] += n_items
        if on_cut(e) and t >= open_s:                # the opening is a cluster on its cuts and words (OPEN_SPACE_S): its
            state["cut_items"] += n_items           # cuts are the hook's, outside the budget the rest of the film keeps
        k = bin_of(t)
        in_bin[k] += 1
        sec = bins[k][2]
        in_sec[sec] = in_sec.get(sec, 0) + 1
        in_lay[(sec_e, layer)] = in_lay.get((sec_e, layer), 0) + 1
        done.add(id(e0))
        placed[-1]["layer_of"] = layer
        return True

    spare, done, cap = [], set(), {"aud": total}
    for e, part in sorted(((hook, "hook"), (turn, "payoff"), (close, "close")), key=lambda x: (x[1] != "close", x[1] != "payoff")):
        if e is None:
            continue
        if (part == "hook" and dir_hook) or (part == "close" and dir_close):
            attempt(e, "plain")                  # the Director wrote that landing: this event is one more moment of the film
            continue
        attempt(e, part)
    # Then the sections: always the part of the film furthest under its share, and inside it the stretch furthest under
    # its own, is given its next best event, until every part holds its share or has nothing left that can sound.
    while True:
        open_secs = [s for s in sec_target if sec_target[s] - in_sec.get(s, 0) >= 0.5
                     and any(pool.get(k) for k in range(nb) if bins[k][2] == s)]
        if not open_secs:
            break
        sec = max(open_secs, key=lambda s: ((sec_target[s] - in_sec.get(s, 0)) / max(sec_target[s], 1e-6), sec_target[s]))
        ks = [k for k in range(nb) if bins[k][2] == sec and pool.get(k)]
        k = min(ks, key=lambda k: ((in_bin[k] + 0.5) / max(bins[k][3], 1e-3), k))
        e = pool[k].pop(0)
        if not attempt(e, "plain"):
            spare.append(e)
    # What a part could not use (it had too few events that can sound) goes to the parts that still have events, in
    # the same order: the film still carries its amount, and the shape holds wherever the footage gives it.
    quota["on"] = False
    for e in sorted(spare, key=lambda e: (["open", "build", "after", "middle", "tail"].index(section_of(e["f"] / fps, dur_s, turn_t, close_t)),
                                          1 if on_cut(e) else 0, -_weight(e), e["f"])):
        if len(state["aud"]) >= total and len(state["tick"]) >= n_tick:
            refuse(e, "the film's amount was spent where its shape asked for it")
            continue
        attempt(e, "plain")
    # No stretch of the film goes without sound for longer than GAP_S (the references breathe, they do not stop): an
    # event in the middle of such a gap is sounded, and the plain sound in the film's densest stretch gives it its place.
    left = [e for e in spare + [x for rows in pool.values() for x in rows] if id(e) not in done]
    end_s = close_t if close_t is not None else dur_s - END_GUARD_S
    for _ in range(8):
        edges = [0.0] + sorted(state["all"]) + [end_s]
        gaps = sorted(((b - a, a, b) for a, b in zip(edges, edges[1:]) if b - a > GAP_S), reverse=True)
        fixed = False
        for _g, a, b in gaps:
            mid = (a + b) / 2
            for e in sorted((x for x in left if a + 1.0 < x["f"] / fps < b - 1.0), key=lambda x: abs(x["f"] / fps - mid)):
                cap["aud"] = total + 1
                ok = attempt(e, "plain")
                cap["aud"] = total
                if not ok:
                    continue
                left = [x for x in left if x is not e]
                if len(state["aud"]) > total:
                    _give_place(placed, state, tally, st, fps, exclude=e["f"] / fps)
                fixed = True
                break
            if fixed:
                break
        if not fixed:
            break
    sounds, report = _signal_check(placed, ctx, report)
    sounds += [s for s in existing if s.get("s") not in lib]          # the Director's voice layers (echo, swell) as written
    sounds.sort(key=lambda s: (s["event_f"], s["id"]))
    report["sounds"] = len(sounds)
    report["shape"].update(shape_of(sounds, report, fps, dur_s, open_s))
    report["shape"]["targets"] = {s: round(v, 1) for s, v in sec_target.items()}
    return sounds, report


def _give_place(placed, state, tally, st, fps, exclude):
    """Take out the plain audible sound with the most company within 5 s (never a hero, a train or the Director's own),
    when taking it out leaves no gap over GAP_S. Returns whether one was taken out."""
    best = None
    all_t = sorted(state["all"])
    for p in placed:
        if p.get("director") or p["tier"] in ("hero", "tick") or p.get("layer_of") != "aud" or abs(p["t"] - exclude) < 1e-6:
            continue
        t = p["t"]
        prev = max([g for g in all_t if g < t - 1e-6], default=0.0)
        nxt = min([g for g in all_t if g > t + 1e-6], default=t)
        if nxt - prev > GAP_S:
            continue
        crowd = sum(1 for g in all_t if abs(g - t) <= 5.0)
        key = (crowd, -_weight(p["ev"]) if p.get("ev") else 0.0)
        if best is None or key > best[0]:
            best = (key, p)
    if best is None:
        return False
    p = best[1]
    placed.remove(p)
    state["aud"].remove(p["t"])
    state["all"].remove(p["t"])
    if p.get("class") and tally.get(p["class"]):
        tally[p["class"]] -= 1
    st["cls_at"] = [x for x in st["cls_at"] if abs(x[0] - p["t"]) > 1e-6]
    if on_cut(p["ev"]) and p["t"] >= st.get("open_s", 0.0):
        state["cut_items"] -= 1
    return True


def shape_of(sounds, report, fps, dur_s, open_s):
    """The film's sound measured on the references' terms: the level spread of the placed sounds against the voice
    (their planned K-weighted level, 25th to 75th percentile), how many sound in the opening and in the first 5 s,
    the share on cuts against type, graphics and moves, and recordings heard twice."""
    lib = library()
    rows = [s for s in sounds if s.get("s") in lib and s.get("by") == "sound.py"]
    lv = [float(s["db"]) + float((lib[s["s"]] or {}).get("hit_lufs", -15.0)) - VOICE_LUFS for s in rows]
    q = np.percentile(lv, [25, 50, 75]).tolist() if len(lv) >= 4 else None
    gest = {}
    for s in rows:
        gest.setdefault(s["id"].rsplit(".", 1)[0], set()).add(s["s"])
    by_rec = {}
    for g, ids in gest.items():
        for sid in ids:
            by_rec[sid] = by_rec.get(sid, 0) + 1
    cut_kinds = ("picture", "cut", "burst", "page")
    return {"level_iqr_db": round(q[2] - q[0], 1) if q else None, "level_median_db": round(q[1], 1) if q else None,
            "level_range_db": [round(min(lv), 1), round(max(lv), 1)] if lv else None,
            "first_5s": sum(1 for s in rows if s["event_f"] / fps < 5.0), "in_opening": sum(1 for s in rows if s["event_f"] / fps < open_s),
            "on_cut_share": round(sum(1 for s in rows if s.get("for") in cut_kinds) / max(1, len(rows)), 2),
            "on_graphics_share": round(sum(1 for s in rows if s.get("for") in ("card", "display", "counter", "flash", "move")) / max(1, len(rows)), 2),
            "repeats": sorted(k for k, n in by_rec.items() if n > 1), "recordings": len(by_rec),
            "per_10s": round(len(rows) / max(dur_s, 1e-3) * 10, 2)}


def _layers_for(e, part, tier, t, st, avoid=()):
    """The recordings for one gesture and the class they were given: [(id, 'lead' | 'hit')], class. A hero is an
    impact (the turn and the close climb into it on a riser; the hook takes no braam: the references open on a
    cluster, not a trailer); a tick is a click; the rest take the class the references use for the moment (the
    film's own tally against their shares, sound_choice.class_for). The recording is of that class as the ruler hears
    it, unused in this film, the nearest the class's reference shape at the moment's weight, rotated per film."""
    desc = st["desc"]
    if not desc:
        layers, cls = _choose(e, part if part != "close" else "payoff", t, [], st["spoken"](t), st["tally"], {}, avoid)
        return [(s, w) for s, w in layers if s not in st["used"]], cls
    spoken = st["spoken"](t)
    room = float(e.get("room_s", 0.0))
    ex = set(st["used"]) | set(avoid)
    fits = set(choice.fits_of(dict(e, turn=False), "payoff" if part in ("payoff", "close") else "plain"))
    if e["kind"] in ("turn", "close"):
        fits.add("braam")
    if tier == "hero":
        lead_room = room >= (HOOK_LEAD_S if part == "hook" else float(SP.CLASSES.get("riser", {}).get("lead_s", 1.0)) + 0.2 if SP else 1.2)
        hit = _pick("impact", st, ex, 0.75 if part == "hook" else 1.0, spoken, fits, prefer_long=part == "close", low=part != "hook",
                    clean=lead_room)                      # where a sweep or a riser can fly in, the hit is a clean one
        if hit is None:
            return [], "impact"
        out = [(hit, "hit")]
        lead_s = float(SP.CLASSES.get("riser", {}).get("lead_s", 1.0)) if SP else 1.0
        if preroll(hit) >= PREROLL_S:                       # the hit flies in on its own (a trailer whoosh-in): a lead on
            return out, "impact"                            # it would sit under its climb, masked, and be two sounds for one
        if part in ("payoff", "close") and room >= lead_s + 0.2:
            lid = _pick("riser", st, ex | {hit}, 1.0, False, set(), want_s=max(0.3, min(room - 0.2, LEAD_MAX_S)))
            if lid:
                out.insert(0, (lid, "lead"))
        elif part == "hook" and room >= HOOK_LEAD_S:          # the opening is designed: a sweep flies into the first hit
            lid = _pick("whoosh", st, ex | {hit}, 0.6, False, fits, want_s=max(0.25, room - 0.1))
            if lid:
                out.insert(0, (lid, "lead"))
        return out, "impact"
    w = _weight(e)
    moment = "moving_cut" if (e["kind"] == "move" or e.get("moving")) else choice.moment_of(e, "plain")
    if e["kind"] in ("counter", "burst"):
        sid = _pick("click", st, ex, 0.25, spoken, fits)
        return ([(sid, "hit")] if sid else []), "click"
    if tier == "tick":                                 # the references' quiet layer is ticks and sweeps under the voice: a
        try:                                           # move or a card's slide takes a soft sweep, type a click
            cls, _lead = choice.class_for(moment, min(w, 0.5), room, st["tally"], spoken)
        except Exception:  # noqa: BLE001
            cls = "whoosh" if moment == "moving_cut" else "click"
        first = "whoosh" if moment == "moving_cut" else cls if cls in ("click", "whoosh", "hit") else "click"
        for c in dict.fromkeys([first, "click", "whoosh"]):
            sid = _pick(c, st, ex, 0.25, spoken, fits)
            if sid:
                return [(sid, "hit")], c
        return [], "click"
    try:
        cls, lead = choice.class_for(moment, w, room, st["tally"], spoken)
    except Exception:  # noqa: BLE001
        cls, lead = ("whoosh" if moment == "moving_cut" else "click"), None
    if cls == "riser" or moment == "moving_cut":     # a riser is a lead, never the hit; a camera move is a sweep
        cls = "whoosh"
    # the hierarchy in the classes too: an accent is an impact, a hit or a sweep, never a tick's click; the body never
    # takes an impact (the weight of an impact is the accents' and the heroes')
    banned = ("click",) if tier == "accent" else ("impact",)
    shares = (SP.MOMENTS.get(moment) or {}) if SP else {}
    tot = sum(st["tally"].values()) + 1.0
    rest = sorted((c for c in shares if c != "riser"), key=lambda c: -(shares[c] - st["tally"].get(c, 0) / tot))   # the class still most owed
    order = [c for c in [cls] + rest if c not in banned] if SP else [cls]
    order = list(dict.fromkeys(order))
    near = sorted(st.get("cls_at") or [], key=lambda x: abs(x[0] - t))[:2]
    if len(near) == 2 and near[0][1] == near[1][1] and near[0][1] in order and len(order) > 1:
        order = [c for c in order if c != near[0][1]] + [near[0][1]]      # a third of one class in a row is a machine
    for c in order:
        if c == "impact" and w < choice.HEAVY:
            continue
        sid = _pick(c, st, ex, w, spoken, fits)
        if sid:
            out = [(sid, "hit")]
            if lead == "whoosh" and tier == "accent":
                lid = _pick("whoosh", st, ex | {sid}, 0.5, False, fits, want_s=max(0.15, room - 0.1))
                if lid:
                    out.insert(0, (lid, "lead"))
            return out, c
    return [], cls


_pre = {}


def preroll(sid):
    """Seconds a recording sounds before its hit point (from 5% of its peak to hit_s or peak_s): an impact cut from a
    trailer whoosh-in carries its own climb (impact_7: 0.5 s)."""
    if sid not in _pre:
        x = library().get(sid) or {}
        try:
            a = np.abs(sample_pcm(sid))
            on = int(np.argmax(a > 0.05 * a.max())) / AR if len(a) and a.max() > 0 else 0.0
        except Exception:  # noqa: BLE001
            on = 0.0
        _pre[sid] = max(0.0, float(x.get("hit_s") or x.get("peak_s") or 0.0) - on)
    return _pre[sid]


def _pick(cls, st, exclude, weight, spoken, fits, want_s=None, prefer_long=False, low=False, clean=False):
    """The unused recording of this class (as the ruler hears it) nearest the class's reference shape at the moment's
    weight. A recording that means something answers only the picture that shows it. Each film turns the library
    its own way (ROTATE): between near neighbours, the film's seed decides, so two films do not hear one house sound."""
    desc, lib = st["desc"], library()
    try:
        from . import sound_signal as SS
        ref = choice._target(cls, weight)
        scale = np.asarray(SP.SCALE, np.float32)
    except Exception:  # noqa: BLE001 - a choice module without the target: its own nearest, on this film's free rows
        rows = {k: d for k, d in desc.items() if d.get("kind") == cls and k not in exclude}
        return choice.nearest(cls, rows, spoken, lambda s: True, weight=weight, fits=fits) if rows else None
    best = None
    for sid, d in desc.items():
        if sid in exclude or d.get("kind") != cls:
            continue
        fam = d.get("family")
        if fam and fam not in fits:
            continue
        dist = SS.distance(SS.vector(d), ref, scale)
        if fam:
            dist -= 0.12
        if not d.get("matched"):
            dist += 0.1
        if spoken:
            dist += 0.5 * sum(10 ** (d["bands"][i] / 10) for i in (2, 3, 4))
        if want_s is not None and d.get("attack_s", 0.0) > want_s:
            if d["attack_s"] > want_s + 0.25:     # a climb longer than its room swallows the sounds before it
                continue
            dist += 2.0 * (d["attack_s"] - want_s)
        x = lib.get(sid) or {}
        if prefer_long:                           # the close rings out: the longer tail
            dist -= 0.35 * min(1.0, float(x.get("max_s") or x.get("duration_s") or 0.5) / 3.0)
        if low:                                   # a hero hit carries the room's floor (40-80 Hz)
            dist -= 0.25 * float(d.get("low", 0.0) if d.get("low") is not None else 10 ** (d["bands"][0] / 10))
        if clean and preroll(sid) >= PREROLL_S:  # a hit with its own whoosh-in leaves no room for the designed lead
            dist += 1.0
        dist += (ROTATE_HERO if low or prefer_long or cls == "riser" else ROTATE) * _u(st["seed"], sid)
        if best is None or dist < best[0]:
            best = (dist, sid)
    return best[1] if best else None


def _train(e, t, st, ctx, al, spoken, fps, n):
    """A burst's train: one click on each of its cuts, alternating up to TRAIN_VOICES unused recordings of one kind
    (camera clicks when the pictures are stills, else plain clicks), at the tick level. Returns (items, recordings)."""
    desc = st["desc"]
    if not desc:
        return [], []
    fits = {"shutter"} if e.get("still") else set()
    lib = library()
    pool = {k: d for k, d in desc.items() if d["kind"] == "click" and k not in st["used"] and not (lib.get(k) or {}).get("hit_s")
            and float((lib.get(k) or {}).get("max_s") or (lib.get(k) or {}).get("duration_s") or 9.0) <= TRAIN_CLICK_S}
    voices = []
    for _ in range(TRAIN_VOICES):
        sid = choice.nearest("click", pool, False, lambda s: True, exclude=tuple(voices), weight=0.2, fits=fits)
        if sid is None:
            break
        if not voices:
            pool = {k: d for k, d in pool.items() if d.get("family") == pool[sid].get("family")}      # one kind of click a train
        voices.append(sid)
    if not voices:
        return [], []
    group = []
    for k, f in enumerate(e["steps"]):
        sid = voices[k % len(voices)]
        tk = f / fps
        ref = ctx["ref_at"](tk) if callable(ctx.get("ref_at")) else VOICE_LUFS
        it = _item(f"snd.j{n}.{k}", sid, f, fps, gain_tier(sid, ref, "tick", 0.15), e, "plain", "hit")
        it["tier"] = "tick"
        group.append(it)
    return group, voices


def _free_ok(sid, t, uses):
    """The one-recording rules for this recording at t: twice in 30 s at most, never twice inside 3 s."""
    near = [u for u, s in uses if s == sid]
    return not any(abs(u - t) < SAME_GAP_S for u in near) and sum(1 for u in near if abs(u - t) < SAME_WIN_S / 2) < SAME_MAX


def _choose(e, part, t, uses, is_spoken, tally, desc, avoid=()):
    """The layers for one event and the class they were given: by the references' shares for the moment and the
    signal ruler (sound_choice) when the library is described; else by the fixed table. A counter keeps its tick.
    avoid: recordings already tried on this event (the voice would hide them here)."""
    lib = library()
    if desc and e["kind"] != "counter":
        try:
            heard = {}
            for _u, sid_ in uses:
                heard[sid_] = heard.get(sid_, 0) + 1
            got, cls = choice.layers(e, part, {"desc": desc, "tally": tally, "spoken": is_spoken, "used": heard,
                                               "free": lambda sid: sid not in avoid and _free_ok(sid, t, uses)})
        except Exception:  # noqa: BLE001
            got, cls = None, None
        if got:
            return [(sid, w) for sid, w in got if sid in lib], cls
        if avoid:
            return [], cls
    layers = []
    for sid, what in gesture(e, part):
        sid = _free(sid, t, uses)
        if sid is None or sid not in lib or sid in avoid:
            if what == "hit":
                return [], None
            continue
        layers.append((sid, what))
    return layers, None


def _free(sid, t, uses):
    """This recording, or its alternate, inside the one-recording rules: twice in 30 s, never twice inside 3 s."""
    for _ in range(4):
        if sid is None:
            return None
        near = [u for u, s in uses if s == sid]
        if not any(abs(u - t) < SAME_GAP_S for u in near) and sum(1 for u in near if abs(u - t) < SAME_WIN_S / 2) < SAME_MAX:
            return sid
        sid = ALT.get(sid)
    return None


def _item(iid, sid, f, fps, db, ev, part, what):
    """One placed recording. Its hit (the library's hit_s where the ear hears it earlier than the loudest 10 ms, else
    peak_s) lands LEAD_S before frame f; `peak_s` carries that offset so validate_l1's start = event - peak holds.
    dur_s is the measured audible tail (max_s) from the trim point: the renderer and the scene mix stop there."""
    x = library()[sid]
    peak = float(x.get("hit_s") or x.get("peak_s") or 0.0) + LEAD_S
    start = f / fps - peak
    trim = 0.0
    if start < 0:
        trim, start = -start, 0.0
    it = {"id": iid, "s": sid, "file": x["file"], "event_f": int(f), "start_s": round(start, 4), "trim_s": round(trim, 4),
          "db": db, "peak_s": round(peak, 4), "for": ev["kind"], "part": part, "layer": what, "by": "sound.py"}
    if x.get("max_s") and float(x["max_s"]) - trim >= 0.05:
        it["dur_s"] = round(float(x["max_s"]) - trim, 3)
    return it


def _bands_pw(x):
    """Per 20 ms frame of a mono AR signal: power in each of PRESENT_BANDS (frames x 4)."""
    n = len(x) // FRAME
    if n == 0:
        return np.zeros((0, len(PRESENT_BANDS)))
    seg = x[:n * FRAME].reshape(n, FRAME) * np.hanning(FRAME).astype(np.float32)
    sp = np.abs(np.fft.rfft(seg, axis=1)) ** 2
    hz = np.fft.rfftfreq(FRAME, 1.0 / AR)
    return np.stack([sp[:, (hz >= a) & (hz < b)].sum(axis=1) for a, b in PRESENT_BANDS], axis=1) + 1e-12


def _voice_levels(ctx):
    """The programme's voice as the checks read it, once per plan: (syllable level of the speech band per frame, band powers)."""
    if "_levels" not in ctx:
        voice, speech = ctx.get("voice"), ctx.get("speech")
        if voice is None or speech is None or not len(voice) or not speech.any():
            ctx["_levels"] = (None, None)
        else:
            v = voice * (10 ** (float(ctx.get("voice_shift_db") or 0.0) / 20))
            vb = frames_db(v)[0]
            k = 7                                   # the voice's level over a syllable: the loudest frame within 0.15 s
            pad = np.pad(vb, k, mode="edge")
            vb = np.max(np.stack([pad[j:j + len(vb)] for j in range(2 * k + 1)]), axis=0)
            ctx["_levels"] = (vb, _bands_pw(v))
            ctx["_fine"] = _bands_fine(v)
    return ctx["_levels"]


def _presence(it, vbands):
    """dB this layer lifts the mix over the voice at its hit, in the band where it shows most: 10 log (1 + S / V) with S
    the sound's loudest frame within 60 ms of its hit and V the voice's loudest frame there. A sound nobody could
    pick out of the voice reads near 0; a reference's placed sound reads 9 or more."""
    smp = sample_pcm(it["s"])
    if not len(smp) or vbands is None or not len(vbands):
        return 99.0
    smp = smp[int(round(float(it.get("trim_s", 0)) * AR)):] * (10 ** (float(it["db"]) / 20))
    at = int(round(float(it["start_s"]) * AR))
    f0 = at // FRAME
    sb = _bands_pw(np.concatenate([np.zeros(at - f0 * FRAME, np.float32), smp]))
    h = int(round(float(it["event_f"]) / max(float(it.get("_fps", 30.0)), 1e-6) * AR / FRAME)) - f0
    a, b = max(0, h - 3), min(len(sb), h + 4)
    va, vz = max(0, f0 + a), min(len(vbands), f0 + b)
    if b <= a or vz <= va:
        return 99.0
    s_pk = sb[a:b].max(axis=0)
    v_pk = vbands[va:vz].max(axis=0)
    return float(np.max(10 * np.log10(1.0 + s_pk / v_pk)))


CLIMB_BANDS = tuple(float(x) for x in np.geomspace(50.0, 16000.0, 19) if x < AR / 2) + (AR / 2,)   # the check's ruler, to 8 kHz


def _bands_fine(x):
    """Per 20 ms frame of a mono AR signal: power in each of the ruler's bands (CLIMB_BANDS), frames x 16."""
    n = len(x) // FRAME
    if n == 0:
        return np.zeros((0, len(CLIMB_BANDS) - 1))
    seg = x[:n * FRAME].reshape(n, FRAME) * np.hanning(FRAME).astype(np.float32)
    sp = np.abs(np.fft.rfft(seg, axis=1)) ** 2
    hz = np.fft.rfftfreq(FRAME, 1.0 / AR)
    return np.stack([sp[:, (hz >= a) & (hz < b)].sum(axis=1) for a, b in zip(CLIMB_BANDS, CLIMB_BANDS[1:])], axis=1) + 1e-12


def _item_bands(it, f0, nf):
    """An item's band powers (PRESENT_BANDS) on the film's 20 ms frames f0 .. f0+nf, as laid (trim, gain, start)."""
    smp = sample_pcm(it["s"])
    out = np.zeros((nf, len(CLIMB_BANDS) - 1))
    if not len(smp):
        return out
    smp = smp[int(round(float(it.get("trim_s", 0)) * AR)):] * (10 ** (float(it["db"]) / 20))
    at = int(round(float(it["start_s"]) * AR)) - f0 * FRAME
    lay = np.zeros(nf * FRAME, np.float32)
    a, b = max(0, at), min(len(lay), at + len(smp))
    if b > a:
        lay[a:b] = smp[a - at:b - at]
        out = _bands_fine(lay)[:nf]
    return out


def _climb_presence(it, bands_pw, also=()):
    """dB a lead (a riser, a sweep into a hero hit) changes what plays with it through the top of its climb, on the
    check's ruler: the 0.4 s before its hit, power summed over the window, in the bands that hold 5% or more of the
    lead's own energy there, the most of 10 log ((R + S) / R). R is the voice and the bed plus `also` (the gesture's
    own hit, whose attack sits in the window). A riser the song, the voice or its own hit covers reads near 0 (SND2's
    riser_7 read 2.4; the hook's sweep into a trailer impact read 0.8)."""
    if bands_pw is None or not len(bands_pw) or not len(sample_pcm(it["s"])):
        return 99.0
    h = int(round(float(it["event_f"]) / max(float(it.get("_fps", 30.0)), 1e-6) * AR / FRAME))
    a, b = max(0, h - 20), min(len(bands_pw), h)
    if b <= a:
        return 99.0
    S = _item_bands(it, a, b - a).sum(axis=0)
    R = bands_pw[a:b].sum(axis=0)
    for o in also or ():
        R = R + _item_bands(o, a, b - a).sum(axis=0)
    keep = S / (S.sum() + 1e-20) >= 0.05
    keep[-1] = False                         # the last band is the ruler's 6.1-8.4 kHz cut at 8: its share is not known here
    if not keep.any():
        return 0.0
    return float((10 * np.log10((R[keep] + S[keep]) / (R[keep] + 1e-12))).max())


def _heard(group, ctx, ref, under, structural, tier="body"):
    """Each hit of a gesture, made to be there at its tier: lowered while it masks the voice (as the signal check will),
    then raised toward the tier's presence when the voice would hide it, never past the tier's lift, the voice's margin
    or the mask. A lead (the riser into a hero hit) must stand over the voice through the top of its climb, else it
    goes and the hit lands alone. Returns None when the gesture can be heard, else why not (the planner then tries
    the next event; the hook, the turn and the close keep their sound either way)."""
    vb, vbands = _voice_levels(ctx)
    if vb is None:
        return None
    speech, fps = ctx["speech"], float(ctx["fps"])
    worst = masked = None
    for it in list(group):
        t_ = it.get("tier") or tier
        if it.get("layer") == "lead":
            it["_fps"] = fps
            need = _masking(it, vb, speech, fps)
            if need > 0:
                it["db"] = round(max(it["db"] - need, tier_floor(it["s"], ref, t_, lead=True)), 1)
            hits = [o for o in group if o.get("layer") == "hit"]
            p = _climb_presence(it, ctx.get("_fine"), hits)
            if p < RISER_HEARD_DB:
                was = it["db"]
                it["db"] = round(min(it["db"] + min(RISER_HEARD_DB + 2.0 - p, RISER_LIFT), GAIN_MAX), 1)
                if _masking(it, vb, speech, fps) > 0:
                    it["db"] = was
                p = _climb_presence(it, ctx.get("_fine"), hits)
            it.pop("_fps", None)
            if p < RISER_HEARD_DB:
                group.remove(it)                     # a climb nobody hears is not a climb
            else:
                it["present_db"] = round(min(p, 60.0), 1)
            continue
        if it.get("layer") not in ("hit", "accent") or it.get("for") == "counter":
            continue
        it["_fps"] = fps
        need = _masking(it, vb, speech, fps)
        if need > 0:
            fl = tier_floor(it["s"], ref, t_)
            it["db"] = round(max(it["db"] - need, fl), 1)
            if it["db"] <= fl + 0.05 and _masking(it, vb, speech, fps) > 0.5:
                masked = "at its tier's floor it still sits in the voice"
        p = _presence(it, vbands)
        goal = TIER_PRESENT.get(t_, PRESENT_DB)
        if p < goal:
            cap = tier_ceiling(it["s"], ref, t_)
            if under is not None:
                cap = min(cap, ref - under - float((library().get(it["s"]) or {}).get("hit_lufs", -15.0)) + PRESENT_LIFT / 2)
            was = it["db"]
            it["db"] = round(min(it["db"] + min(goal - p, TIER_LIFT.get(t_, PRESENT_LIFT)), cap), 1)
            if it["db"] > was:
                back = _masking(it, vb, speech, fps)
                if back > 0:
                    it["db"] = round(max(was, it["db"] - back), 1)
                if it["db"] > was:
                    it["lifted_db"] = round(it["db"] - was, 1)
                p = _presence(it, vbands)
        it.pop("_fps", None)
        it["present_db"] = round(min(p, 60.0), 1)
        if p < TIER_HEARD.get(t_, PRESENT_MIN) and not structural:
            worst = f"the voice would hide it here ({p:.1f} dB over it)"
    return masked or worst


def present_over(sounds, voice, bed, fps, voice_shift_db=0.0):
    """The same sounds once the film's bed is known (a song, arranged and levelled): each hit raised toward its tier's
    presence over the voice and the bed together, in the band it lives in, by its tier's lift at most and never into
    the voice, so the hierarchy holds over the song too. A riser into a hero hit must stand over the song through the
    top of its climb (lifted up to RISER_LIFT): one the song still covers is taken out, never left masked. voice, bed:
    mono at AR on the film's clock. Returns adjusted copies; counter ticks and anything not from the library come back
    as they were."""
    if voice is None or bed is None or not len(voice) or not len(bed):
        return [dict(s) for s in sounds]
    n = min(len(voice), len(bed))
    v = voice[:n] * (10 ** (float(voice_shift_db) / 20))
    both = _bands_pw(v) + _bands_pw(np.asarray(bed[:n], np.float32))
    both_fine = _bands_fine(v) + _bands_fine(np.asarray(bed[:n], np.float32))
    speech, _ = speech_frames(voice[:n], None)
    vb = frames_db(v)[0]
    k = 7
    pad = np.pad(vb, k, mode="edge")
    vb = np.max(np.stack([pad[j:j + len(vb)] for j in range(2 * k + 1)]), axis=0)
    lib, out = library(), []
    for s in sounds:
        it = dict(s)
        if it.get("s") not in lib or it.get("for") == "counter" or it.get("start_s") is None:
            out.append(it)
            continue
        it["_fps"] = float(fps)
        if it.get("layer") == "lead":
            if it.get("part") not in ("payoff", "close", "hook") and it.get("tier") != "hero":
                it.pop("_fps", None)
                out.append(it)                       # a landing's fly-in rides with its hit
                continue
            gid = str(it.get("id", "")).rsplit(".", 1)[0]
            hits = [o for o in sounds if o.get("layer") == "hit" and o.get("s") in lib and o.get("start_s") is not None
                    and str(o.get("id", "")).rsplit(".", 1)[0] == gid]
            p = _climb_presence(it, both_fine, hits)
            if p < RISER_HEARD_DB:
                was = it["db"]
                it["db"] = round(min(it["db"] + min(RISER_HEARD_DB + 2.0 - p, RISER_LIFT), GAIN_MAX), 1)
                if speech.any() and _masking(it, vb, speech, fps) > 0:
                    it["db"] = was
                p = _climb_presence(it, both_fine, hits)
                if it["db"] > was:
                    it["lifted_for_bed_db"] = round(it["db"] - was, 1)
            it.pop("_fps", None)
            if p < RISER_HEARD_DB:
                continue                             # the song covers the climb: no masked riser, the hit lands alone
            it["climb_over_bed_db"] = round(min(p, 60.0), 1)
            out.append(it)
            continue
        tier = it.get("tier") or "body"
        goal = TIER_PRESENT.get(tier, PRESENT_DB) if it.get("tier") else PRESENT_DB
        p = _presence(it, both)
        if p < goal:
            was = it["db"]
            top = tier_ceiling(it["s"], VOICE_LUFS, tier) if it.get("tier") else GAIN_MAX
            it["db"] = round(max(was, min(it["db"] + min(goal - p, TIER_LIFT.get(tier, PRESENT_LIFT) if it.get("tier") else PRESENT_LIFT), top)), 1)
            back = _masking(it, vb, speech, fps) if speech.any() else 0.0
            if back > 0:
                it["db"] = round(max(was, it["db"] - back), 1)
            if it["db"] > was:
                it["lifted_for_bed_db"] = round(it["db"] - was, 1)
                p = _presence(it, both)
        it.pop("_fps", None)
        if it.get("tier") in ("body", "tick") and it.get("layer") == "hit" and it.get("for") != "burst" and p < BED_HEARD_DB:
            continue                                 # the song and the voice cover it even lifted: a sound nobody hears is clutter
        it["over_bed_db"] = round(min(p, 60.0), 1)
        out.append(it)
    return out


def _signal_check(placed, ctx, report):
    """Each layer laid over the programme's own voice: a sound that masks the speech band on spoken frames is
    lowered to its tier's floor, else dropped with its gesture's leads. The report lists the hero and accent
    gestures under `placed` (the moments the film itself sounds: the music swells into these), the body under
    `body` and the small ticks under `ticks`."""
    speech = ctx.get("speech")
    out = []
    vb, _vbands = _voice_levels(ctx)
    for g in placed:
        hits = [i for i in g["items"] if i["layer"] == "hit"]
        keep = []
        for it in g["items"]:
            if vb is not None and it["for"] != "counter":
                need = _masking(it, vb, speech, ctx["fps"])
                if need > 0:
                    lowest = tier_floor(it["s"], g["ref"], it["tier"], lead=it["layer"] == "lead") if it.get("tier") else floor_for(it["s"], g["ref"])
                    if it["db"] - need >= lowest:
                        it["db"] = round(it["db"] - need, 1)
                        it["eased_db"] = round(need, 1)
                    else:
                        report["dropped"].append({"f": it["event_f"], "kind": it["for"], "s": it["s"],
                                                  "why": f"masks the voice by {need:.1f} dB at {it['event_f'] / ctx['fps']:.2f} s"})
                        if it["layer"] == "hit":
                            hits.remove(it)
                        continue
            keep.append(it)
        if not hits:
            continue
        if g.get("director"):
            for i in keep:
                if i.get("for") == "director":
                    i.pop("for")
        out += keep
        if g.get("director"):
            report.setdefault("director", []).append({"t": round(g["t"], 2), "s": keep[0]["s"], "db": keep[0]["db"], "was": keep[0].get("director_db")})
            continue
        lay = []
        for i in keep:
            if not any(x[0] == i["s"] for x in lay):
                lay.append([i["s"], i["db"], sum(1 for k in keep if k["s"] == i["s"])])
        over = [i["present_db"] for i in keep if i.get("present_db") is not None and i["layer"] != "lead"]
        row = {"t": round(g["t"], 2), "kind": g["ev"]["kind"], "part": g["part"], "tier": g.get("tier"), "class": g.get("class"), "layers": lay,
               "on_cut": on_cut(g["ev"]), **({"train": len(keep)} if g.get("train") else {}), **({"over_voice_db": min(over)} if over else {})}
        report.setdefault("placed" if g.get("tier") in ("hero", "accent") else "ticks" if g.get("tier") == "tick" else "body", []).append(row)
    out.sort(key=lambda s: (s["event_f"], s["id"]))
    report["gestures"] = len(report.get("placed") or []) + len(report.get("body") or []) + len(report.get("ticks") or [])
    report["sounds"] = len(out)
    return out, report


def _masking(it, vb, speech, fps):
    """dB this layer must come down so it never sits within MASK_DB of the voice's speech band on spoken frames."""
    smp = sample_pcm(it["s"])
    if not len(smp):
        return 0.0
    smp = smp[int(round(float(it.get("trim_s", 0)) * AR)):] * (10 ** (float(it["db"]) / 20))
    at = int(round(float(it["start_s"]) * AR))
    f0 = at // FRAME
    sb = frames_db(np.concatenate([np.zeros(at - f0 * FRAME, np.float32), smp]))[0]
    n = min(len(sb), len(vb) - f0)
    if n <= 0:
        return 0.0
    sb, v, sp = sb[:n], vb[f0:f0 + n], speech[f0:f0 + n]
    over = sb - (v - MASK_DB)
    # a frame the transcript calls spoken but that carries no voice (a pause inside a word's span, a breath before a
    # figure: 30 dB or more under the film's own spoken level) has nothing to mask
    k = min(len(vb), len(speech))
    spoken_lv = float(np.median(vb[:k][speech[:k]])) if k and speech[:k].any() else None
    live = (v > spoken_lv - 30.0) if spoken_lv is not None and np.isfinite(spoken_lv) else np.ones(n, bool)
    bad = over[sp & live & (sb > -80)]
    if (bad > 0).sum() < MASK_FRAMES:
        return 0.0
    return float(np.percentile(bad[bad > 0], 80)) + 0.5


# ----------------------------------------------------------------- the seam

def _asked(asks):
    for a in (asks or {}).values() if isinstance(asks, dict) else (asks or []):
        if isinstance(a, dict) and a.get("probe") in ("sfx_count", "music", "loudness"):
            return True
    return False


def design(l1, work=None, index=None, perc=None, asks=None, src=None, supply=None, turn_s=None, bed=None, own_tiers=False):
    """The designed sound for a compiled film: (sounds, report). The Director's own sounds stay and count first.
    supply: the governor's {np10, bed} (taste.supply) when the compiler has it; else read from the L1 and the upload.
    turn_s: the story's turn on the film's clock, when the caller knows it (a workflow's script names its payoff).
    bed: True when a song will play under the film (the owner or the customer supplied one).
    own_tiers: the sounds already in the film are the Director's own gestures and take their place in the level hierarchy
    (the hook's impact and the close are hero hits); without it they keep their role levels."""
    fps, D = float(l1["fps"]), int(l1["dur_f"])
    if turn_s is None:                       # the scene's own marks, as the music engine reads them: the drop lands on the payoff
        own = l1.get("music_marks") or {}
        turn_s = own.get("payoff_s") if own.get("payoff_s") is not None else own.get("turn_s")
    if work and index is None and os.path.exists(os.path.join(work, "index.json")):
        index = json.load(open(os.path.join(work, "index.json")))
    if work and perc is None and os.path.exists(os.path.join(work, "perception.json")):
        perc = json.load(open(os.path.join(work, "perception.json")))
    if work and asks is None and os.path.exists(os.path.join(work, "scout.json")):
        try:
            asks = {str(a.get("id")): a for a in json.load(open(os.path.join(work, "scout.json"))).get("asks") or [] if isinstance(a, dict)}
        except (OSError, ValueError):
            asks = None
    existing = [dict(s) for s in l1.get("sound") or []]
    if _asked(asks):
        return existing, {"asked": True, "sounds": len(existing), "placed": [], "dropped": []}
    if src is None:
        main = (l1.get("assets") or {}).get("main") or {}
        p = main.get("abs") or main.get("path")
        if p and work and not os.path.isabs(p):
            p = os.path.join(work, p)
        src = p if p and os.path.exists(p) else (os.path.join(work, "source.mp4") if work else None)
    if not (src and os.path.exists(src)):
        # no upload to listen to: nothing can be checked against the voice, so nothing is added or moved
        return existing, {"no_signal": True, "sounds": len(existing), "placed": [], "dropped": []}
    n = int(round(D / fps * AR))
    voice = spine_audio(l1.get("spine"), fps, src, n)[:, 0]
    words = words_in_programme(l1, index)
    speech, shift = None, 0.0
    if voice is not None and np.abs(voice).max() > 1e-5:
        speech, _ = speech_frames(voice, words)
        shift = voice_shift(voice, speech)
    else:
        voice = None
    src_dur = float(((index or {}).get("video") or {}).get("duration") or D / fps)
    if bed is None:
        bed = has_bed(perc, len((index or {}).get("words") or []), src_dur) if not supply else bool(supply.get("bed"))
    speaks = sum(b - a for a, b in words) >= 1.0 if words is not None else (speech is not None and speech.sum() * FRAME / AR >= 1.0)

    def ref_at(t):
        if speaks or voice is None:
            return VOICE_LUFS
        a, b = max(0, int((t - 1.5) * AR)), int((t + 1.5) * AR)
        seg = voice[a:b]
        if not len(seg):
            return VOICE_LUFS
        lv = 20 * math.log10(float(np.sqrt((seg ** 2).mean())) + 1e-9) + 3.0 - 0.691 + shift
        return max(min(lv + NO_VOICE_LIFT, VOICE_LUFS + 6), VOICE_LUFS - 10)
    events = l1_events(l1, _kshots(perc, index))
    # where the film closes, read exactly as the music engine reads its last hit (music.marks_from_l1: the first landing
    # of l1_events after the last word, 0.7 s before the end at the latest), so the two land on one frame
    # (with a song under the film, a film with nothing landing after its last word closes a breath after it, where
    # the song's last hit goes)
    close_s, _by = close_time([e["f"] / fps for e in l1_events(l1)], [(a, b) for a, b in (words or []) if a < D / fps], D / fps, bed)
    ctx = {"fps": fps, "dur_f": D, "existing": existing, "speech": speech, "voice": voice, "voice_shift_db": shift,
           "words": words or [], "bed": bed, "ref_at": ref_at, "np10": (supply or {}).get("np10"), "own_tiers": bool(own_tiers),
           "turn_f": int(round(float(turn_s) * fps)) if turn_s is not None else None, "close_s": close_s,
           "delivery_tail_s": float((supply or {}).get("delivery_tail_s", 0.0)),
           "seed": film_seed(l1.get("sid"), l1.get("seed"), D, [e.get("src") for e in (l1.get("spine") or [])][:64],
                             sorted(str((a or {}).get("path")) for a in (l1.get("assets") or {}).values())[:64])}
    sounds, report = plan_sounds(events, ctx)
    report["speaks"] = bool(speaks)
    return sounds, report


def apply(l1, work=None, index=None, perc=None, asks=None, supply=None, turn_s=None, bed=None, own_tiers=False):
    """The seam for the compile path and the workflow shell: the L1 with its designed sound. Never raises; on any
    fault the L1 comes back as it was, with the fault in l1["sound_design"]. VALMERA_SOUND_DESIGN=0 turns it off."""
    if os.environ.get("VALMERA_SOUND_DESIGN", "1") == "0":
        return l1
    try:
        sounds, report = design(l1, work=work, index=index, perc=perc, asks=asks, supply=supply, turn_s=turn_s, bed=bed, own_tiers=own_tiers)
        l1["sound"] = sounds
        l1["sound_design"] = report
    except Exception as e:  # noqa: BLE001 — a film is never lost to its sound design
        l1["sound_design"] = {"fault": repr(e)[:300]}
    return l1


# ----------------------------------------------------------------- command line

def main(argv=None):
    """python -m score.sound design --l1 l1.json --work DIR --out l1_sound.json [--turn S] [--bed]"""
    import argparse
    ap = argparse.ArgumentParser(prog="score.sound")
    ap.add_argument("cmd", choices=["design"])
    ap.add_argument("--l1", required=True)
    ap.add_argument("--work")
    ap.add_argument("--out", required=True)
    ap.add_argument("--turn", type=float, help="the story's turn on the film's clock, seconds")
    ap.add_argument("--bed", action="store_true", help="a supplied song will play under the film")
    a = ap.parse_args(argv)
    l1 = json.load(open(a.l1))
    l1 = apply(l1, work=a.work or os.path.dirname(os.path.abspath(a.l1)), turn_s=a.turn, bed=True if a.bed else None)
    tmp = a.out + ".part"
    with open(tmp, "w") as fh:
        json.dump(l1, fh)
    os.replace(tmp, a.out)
    print(json.dumps(l1.get("sound_design") or {}, default=float)[:2000])


if __name__ == "__main__":
    main()
