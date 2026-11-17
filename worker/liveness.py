"""Is a stretch with no words dead air, or footage where something happens?

EDIT-CORE A1 (16 Nov 2026). "Silence" used to mean "no transcribed words":
audit.speech_gaps lists the spans between words, and cut_silences, the
First cut and the editing model all read every such span as a pause to cut.
On footage with little or no speech that is wrong. A 93 s music video
(p5717, 7 words of a lyric heard) and an 11 s walk (p5744, one word) were
read as minutes of "gaps": asked to "tighten" and "preserve the original
framing and sound", the editor cut the walk to 2 s, which then made the
customer's "slow motion after 6 s" impossible.

A stretch is DEAD only when the picture AND the sound show nothing
happening. This module measures both on the activity track (activity.py:
changed-pixel share at 4 samples a second, and each sample's sound level):

- picture: the median share of the frame that changes between samples.
  A camera that walks or a dancing subject changes half the frame; a
  talking head in a pause changes 4-21% (measured: p5711, p5624). A shot
  change inside the stretch is activity too (an edited piece).
- sound: the share of samples that rise SOUND_OVER_FLOOR_DB over the take's
  own floor or are loud on any floor (LOUD_DBFS: a song, a crowd). Room tone
  and a far-off murmur sit near the floor; a song sits 20-30 dB over it.

It measures; it never decides what to cut. cut_silences cuts the dead
stretches, find_silences tells the model which stretches carry action and
why, and the model decides.
"""
import math

import numpy as np

PICTURE_MOVING = 0.25      # median changed share of the frame per sample
CUTS_PER_S = 0.5           # shot changes a second inside the stretch
SOUND_OVER_FLOOR_DB = 10.0
LOUD_DBFS = -38.0          # over 300 Hz: a song or a crowd, never room tone
SOUND_SHARE = 0.5          # share of the stretch's samples that sound
EDGE_S = 0.15              # word tails and onsets at a stretch's edges
# Sound is read only from level samples that lie wholly inside the gap
# trimmed by SOUND_EDGE_S. A level sample spans 1/fps (0.25 s); a sample
# that straddles the next word, plus whisper's timing slop, made a 0.5 s
# dead pause on a still talking head (p5574, -87 dB between words) read as
# "sound plays". With no such sample the picture decides alone.
SOUND_EDGE_S = 0.25
FLOOR_PCT = 10.0


def _arrays(track):
    fps = float(track.get("fps") or 4.0)
    motion = np.asarray(track.get("motion") or [], dtype=np.float32)
    level = track.get("level")
    level = (None if level is None
             else np.asarray(level, dtype=np.float32)[:motion.size])
    return fps, float(track.get("t0") or 0.0), motion, level


def floor_db(track):
    """The take's sound floor: FLOOR_PCT of its sounding samples (dBFS)."""
    _fps, _t0, _m, level = _arrays(track)
    if level is None or level.size == 0:
        return None
    real = level[level > -100.0]
    if real.size == 0:
        return -120.0
    return float(np.percentile(real, FLOOR_PCT))


def _window(track, start, end):
    fps, t0, motion, level = _arrays(track)
    a = max(0, int(math.floor((start - t0) * fps)))
    b = min(motion.size, int(math.ceil((end - t0) * fps)))
    return a, b


def covers(track, start, end):
    """Does the measured track cover [start, end]?"""
    fps, t0, motion, _level = _arrays(track)
    return motion.size > 0 and start >= t0 - 1.0 / fps and \
        end <= t0 + motion.size / fps + 1.0 / fps


def read_span(track, start, end, shots=(), floor=None):
    """The measured activity of one source span: {"live", "picture",
    "cuts", "sound", "level_db", "why"}. live is True when the picture moves
    or the sound carries something over the room's floor; why says what, in
    words the editor can repeat. None when the track does not cover it."""
    if end - start <= 0 or not covers(track, start, end):
        return None
    a_s, b_s = start, end
    if end - start > 2 * EDGE_S + 0.25:
        a_s, b_s = start + EDGE_S, end - EDGE_S
    a, b = _window(track, a_s, b_s)
    fps, _t0, motion, level = _arrays(track)
    if b <= a:
        return None
    pic = float(np.median(motion[a:b]))
    cuts = sum(1 for t in shots or () if a_s < float(t) < b_s)
    sound = level_db = None
    t0 = _arrays(track)[1]
    sa = max(0, int(math.ceil((start + SOUND_EDGE_S - t0) * fps - 1e-6)))
    sb = int(math.floor((end - SOUND_EDGE_S - t0) * fps + 1e-6))
    if level is not None and sb > sa and level.size >= sb:
        floor = floor_db(track) if floor is None else floor
        seg = level[sa:sb]
        thr = min(floor + SOUND_OVER_FLOOR_DB, LOUD_DBFS) \
            if floor is not None else LOUD_DBFS
        sound = float(np.mean(seg >= thr))
        level_db = float(np.median(seg))
    span = max(end - start, 1e-6)
    moving = pic >= PICTURE_MOVING
    edited = cuts >= 1 and cuts / span >= CUTS_PER_S
    sounding = sound is not None and sound >= SOUND_SHARE
    why = []
    if moving:
        why.append(f"the picture moves ({pic:.0%} of the frame changes "
                   "each quarter second)")
    if edited:
        why.append(f"{cuts} shot change(s)")
    if sounding:
        why.append(f"sound plays over the room's floor (median "
                   f"{level_db:.0f} dBFS, {sound:.0%} of it)")
    return {"live": bool(moving or edited or sounding),
            "picture": round(pic, 3), "cuts": cuts,
            "sound": None if sound is None else round(sound, 2),
            "level_db": None if level_db is None else round(level_db, 1),
            "why": "; ".join(why)}


def annotate(gaps, track, shots=()):
    """Add the measured reading to each gap dict in place (keys "live",
    "why", "picture", "sound", "level_db"); a gap the track does not cover
    is left without them. Returns how many gaps were read."""
    floor = floor_db(track)
    n = 0
    for g in gaps:
        r = read_span(track, float(g["start"]), float(g["end"]), shots, floor)
        if r is None:
            continue
        g.update(r)
        n += 1
    return n


def shot_starts(index):
    """Shot change times of an index (source seconds), the first excluded."""
    out = []
    for s in (index or {}).get("shots") or []:
        try:
            t = float(s.get("start") if isinstance(s, dict) else s[0])
        except (TypeError, ValueError, IndexError, AttributeError):
            continue
        if t > 0.05:
            out.append(t)
    return out
