"""The film's music: a song cut to the film (sound engine lane, 13 Nov; section choice and level flow, MUSIC2, 14 Nov).

The song is somebody's choice: the owner's or the customer's own, or the one music_choose picked from the licensed
library (owner, 13 Nov: Valmera chooses music the way the references do). This engine does an editor's work on it,
whatever it is: read it (rhythm.analyze: beats, downbeats, sections, drops, gaps; here: one bar grid, each bar's
level, low end and voice, how alike bars are, where the song makes an entrance and where its intro is), choose the
parts that play (arrange: the film opens on an entrance and keeps a full part under it, never the intro or a flat
stretch; a build where the story turns; the drop on the payoff; the last hit on the film's last landing; joins at
phrase ends, on picture cuts, 12 ms before a downbeat), ride its level (track: under every word, up in the voice's
pauses, a swell through the build, out for one beat before the drop, the hit over the bed, the last hit with its ring,
nothing under the end card), and move the picture's free cuts onto its beats where nobody is speaking (fit_cuts).
The film without the song is always written beside it.

    song = analyse(path)                                  the song's map (a few seconds per minute of song)
    plan = arrange(song, marks)                           which bars play where: [{song: [a, b], film: [a, b], tempo}]
    music, report = track(path, song, plan, marks, voice) stereo 48 kHz on the film's clock, levels solved
    cuts, moved = fit_cuts(cuts, plan, marks, fps)        picture cuts on beats, only where no word is near
    report = mix(l1, work, song_path, film=, out=)        a compiled film with the song under it (the clean film is the engine's)
    report = mix_film(film, song_path, marks, out=)       the same for a film whose sound is one file (no voice track apart)
    marks_from_l1(l1, index, turn_s=None, payoff_s=None)  what a compiled film gives the arrangement

marks: {dur_s (the programme, without the end card), words [(t0, t1)], cuts [t] and landings [t] (the picture's cuts
and designed landings, optional), accents [t] (the moments the film itself sounds: the music swells into each),
turn_s (where the story turns: the build starts there), payoff_s (where it pays off: the drop lands there, on the
picture cut within SNAP_S of it unless snap is False; with only turn_s the drop lands on it), end_hit_s (where the last hit lands, or None: after the last word), quiet [(t0, t1)]
(held low), drop_landing (optional: the sound's own turn hit, a hit carrying `drop` in the film's sound, screen
placement only; within DROP_REACH_S of the mark the drop lands on it, so the impact and the drop are one landing)}.

Numbers are the judge's: the 28 @metromedia.house reels on Demucs stems, K-weighted every 10 ms, read against their
words and story turns (.valmera/week-1112/lanes/MUSIC2/refs/FLOW.json, 14 Nov; PROFILE below). numpy, ffmpeg and the
rhythm engine only. Nothing here reads a title, a tag or a request.
"""
import math
import os
import subprocess

import numpy as np

FF = os.environ.get("FFMPEG_BIN", "ffmpeg")
SR = 48000
VOICE_LUFS = -20.0                 # the levelled voice every level here is set against (dialogue_level)
STRETCH_MAX = 0.04                 # a song is never played more than 4% off its tempo
MIN_SEG_BARS = 2                   # a piece of the song shorter than this is a stutter, not an edit
JOIN_S = 0.012                     # bars are joined 12 ms before the downbeat, so the hit's attack is whole
XFADE_S = 0.020
VOICE_SPEECH_DB = 2.5              # a levelled voice's frames while it speaks sit about this far under its integrated level
VOICED = 0.2                       # a bar whose voice holds this share of its 250-4000 Hz is sung in
CUT_TOL_S = 0.045                  # a picture cut within this of a beat is on it (a frame and a third at 30 fps)
SNAP_S = 0.3                       # the drop takes the picture cut this near the story's mark: the visual turn
DROP_REACH_S = 1.5                 # the sound's turn hit (marks["drop_landing"]) this near the mark: the drop lands with it
JUMP_MIN = 0.7                     # what any join costs the arrangement, so a song is cut only where the film needs it
BUILD_BARS = (2, 4)                # the build before the drop: never under two bars, never a stretch over four

# What the judge's music does, measured on stems (lanes/MUSIC2/refs/FLOW.json: the 28 @metromedia.house reels, the
# owner's 8 first; K-weighted, 10 ms, against whisper word times and the story turns a model read):
#   under the words   the music sits 4.5 dB under the voice in the owner's 8 (3.5 to 7.4), 6.2 in the 28 (4.5 to 9.1)
#   pauses            nothing in a breath: through a pause under 0.8 s the music is where it was (-0.3 to -2 dB), and its
#                     fast level does not follow the voice's (r -0.03 to -0.05: no sidechain). From 0.8 s it is up 1.3 dB
#                     at the median, 5.6 at the upper quartile, 6.5 at its peak; it starts up 0.2 s after the last word
#   swells            11 to 12 a minute, +7.2 dB over 2.0 s (6.4 to 8.7), 93% of them under words, ending on a word's onset
#   before a hit      17 steps up a minute of 6 dB or more; 54 to 58% are led by a hole: the level sinks 3 to 4 dB over
#                     the second before, then 11 dB down 0.2 s before the hit, back on it
#   turns             a hole of 6 dB or more inside the 1.5 s before 42 to 53% of story turns; the peak after one is +3.4 dB
#                     (to +6.9), and the level after it is where it was before (step 0.2 dB): an accent, not a new level
#   the end           the last word 0.8 to 1.2 s before the end; a last peak of +3.9 to +6.2 dB over the bed 2.4 to 2.6 s
#                     before it, then its ring: -5 dB at 1.2 s, -17 at 0.5 s. Never a slow fade of a level that is held
#   over the film     the music's 2 s level moves from -6 to +4.4 dB around its middle: the song's own parts keep their distance
PROFILE = {
    "under_voice_db": 5.0,         # the bed sits this far under the voice while it speaks, on the judge's ruler (see UNDER_VOICE)
    "open_db": 1.5,                # under the voice's level when nobody has spoken yet, or after the last word
    "quiet_max_db": 8.0,           # a quiet part of the song (a build) plays at most this far under its loud part
    "lift_gap_s": 0.6,             # a pause from here up lets the music up; a breath does not (no pumping)
    "lift_full_s": 1.5,            # and from here it has its whole lift
    "lift_db": 1.5, "lift_max_db": 5.0,
    "lift_wait_s": 0.1, "lift_rise_s": 0.3,        # up from a tenth after the last word, over three tenths
    "duck_ahead_s": 0.1, "lift_fall_s": 0.25,      # down a tenth before the next word, over a quarter second
    "swell_db": 4.0, "swell_max_s": 4.0,           # the rise through the build into the drop
    "hole_db": 18.0, "hole_min_s": 0.25, "hole_max_s": 0.6, "hole_in_s": 0.04, "hole_out_s": 0.01,   # out for one beat before a hit
    "accent_db": 3.5, "accent_rise_s": 1.5, "accent_fall_s": 1.0,   # a swell into each moment the film itself sounds (its placed sounds), then back
    "hit_db": 3.0,                 # the drop over the bed, for a beat, eased back over a bar
    "after_drop_db": 2.0,          # the bed stays this much higher after its drop (the SpaceX reel is up 5 dB after the turn it rises through; pooled it is level: kept small)
    "end_hole_db": 12.0, "end_hit_db": 1.5,
    "duck_attack_s": 0.12, "duck_release_s": 0.30,
    "mask_db": 6.0,                # the bed's speech band stays at least this far under the voice's syllables
    "carve_max_db": 6.0,           # by a dip of the speech band alone, never more than this
    "end_tail_beats": 1.0,         # the last hit rings at least one beat
    "end_tail_beats_max": 4.0,     # and up to a bar when the film leaves it room (the judge's last hit rings two seconds)
    "end_gap_s": 0.08,             # silence before the end card
}
# under_voice_db by the kind of film. Stories: the judge. Marketing, speech clips and long form: the references of those
# kinds on the same ruler (lanes/MUSIC study: 8.0, 4.7 with the owner's speech reels, 15.6), a talking take kept clear.
UNDER_VOICE = {"stories": 5.0, "marketing": 8.0, "podcast": 5.5, "longform": 15.0}
# The story's arc, held under every word (music-mix lane, 20 Nov 2026). The ten Cuban shorts played the song's own arc
# at its own level: its quiet first half 8-13 dB under the voice, its loud last third 1.5-3.4 dB under, the bed over the
# voice on 4-23% of voiced 400 ms frames, while the film's median read 5.5 under. One template that climbs, whatever
# the story. The 28 judge reels hold the music's level flat across a film's thirds (median +0.2, -0.1, -0.2 dB against
# its own median) and move it with the story. With "hold" in a profile (the podcast kind) each part of the story sits
# at the depth its arc chose (hold_under_words), the song's own swells play inside a part, and the floor is read on the
# voice itself: the phrase's level (not each word's, so the bed does not pump with the words) and the voice's own 0.4 s
# level wherever it speaks, stamped or not. The customer Director's bed (PROFILE alone) is unchanged.
HOLD = {"hold": True,
        "hold_floor_db": 2.0,          # the bed's 0.4 s K level stays at least this far under the phrase it plays under
        "hold_frame_db": 0.5,          # and under the voice's own 0.4 s level on every voiced frame
        "hold_active_db": 8.0,         # a frame is voiced when the voice's 0.4 s level is within this of its 90th percentile
        "hold_deep_db": 10.0,          # a voiced frame quieter than the 90th percentile less this is read at that level (no holes)
        "hold_phrase_gap_s": 0.35,     # a phrase: the voice's run, through breaths shorter than this
        "hold_room_db": 1.0,           # the closest part sits at least this far under the floor's own line
        "hold_raise_max_db": 6.0,      # a part of the song quieter than its depth is brought up by at most this
        "hold_edge_s": 0.3,            # a part's depth changes inside this either side of its first word
        "hold_spread_s": 0.3, "hold_attack_s": 0.12, "hold_release_s": 0.4}   # the floor's dip: over its window, eased
# A part's depth against the kind's under_voice_db. The depths are the story's own: they are not re-centred on the
# film's average, so a low setup sits 5 dB further under the voice than a high payoff whatever the rest of the arc.
ARC_DB = {"low": 2.5, "mid": 0.0, "high": -2.5}
ARC_PARTS = ("setup", "build", "payoff", "close")
ARC_DEFAULT = {"setup": "mid", "build": "mid", "payoff": "high", "close": "mid"}


def profile_for(kind):
    """The PROFILE for a kind of film (stories | marketing | podcast | longform)."""
    out = dict(PROFILE, under_voice_db=UNDER_VOICE.get(kind, PROFILE["under_voice_db"]))
    if kind == 'podcast':
        out.update(HOLD)
        # Dialogue carries the cut. Keep the beat arrangement and ride phrases
        # gently without making holes before the turn, payoff or final hit.
        out.update(broadband_bed_db=5.5, build_floor_db=6., hole_db=0., end_hole_db=0.,
                   swell_db=0., accent_db=0., hit_db=0., after_drop_db=0., end_hit_db=0.,
                   open_db=4.5, lift_gap_s=1.2, lift_full_s=2.5, lift_db=0.5, lift_max_db=1.,
                   lift_rise_s=1.2, lift_fall_s=0.8, duck_attack_s=1.5, duck_release_s=3.,
                   phrase_ride_s=8., carve_max_db=1.5)
    return out


# ----------------------------------------------------------------- the song's map

def _decode(path, rate, ch, ffmpeg=None):
    r = subprocess.run([ffmpeg or FF, "-hide_banner", "-nostdin", "-loglevel", "error", "-i", path, "-map", "0:a:0", "-vn", "-ac", str(ch),
                        "-ar", str(rate), "-f", "f32le", "pipe:1"], capture_output=True, timeout=900)
    x = np.frombuffer(r.stdout, np.float32)
    return x.reshape(-1, ch).copy() if ch > 1 else x.copy()


def _grid(rmap, dur):
    """One bar grid for the whole song when it keeps one tempo: (downbeats, beat_s, steady). A drop is a downbeat,
    so the grid is hung on the drops when the song has them; else on the downbeats the tracker found."""
    beats = np.array(rmap.get("beats") or [], np.float64)
    downs = np.array(rmap.get("downbeats") or [], np.float64)
    if len(beats) < 8:
        return list(downs), None, False
    T = float(np.median(np.diff(beats)))
    k = np.round((beats - beats[0]) / T)
    A = np.stack([k, np.ones(len(k))], axis=1)
    for _ in range(3):
        (T, t0), *_ = np.linalg.lstsq(A, beats, rcond=None)
        res = beats - (k * T + t0)
        keep = np.abs(res - np.median(res)) < 0.25 * T
        if keep.sum() < 8 or keep.all():
            break
        A, beats, k = A[keep], beats[keep], k[keep]
    res = beats - (k * T + t0)
    steady = float(np.std(res)) < 0.035 and len(beats) >= 16
    if not steady:
        return list(downs), float(T), False
    bar = 4.0 * T
    drops = [d for d in rmap.get("drops") or [] if 0 <= d <= dur]
    if drops:
        anchor = float(drops[0])
        # the bar length the drops themselves keep
        if len(drops) > 1:
            n = np.round((np.array(drops) - anchor) / bar)
            ok = n > 0
            if ok.any():
                est = float(np.median((np.array(drops)[ok] - anchor) / n[ok]))
                if abs(est - bar) / bar < 0.01:
                    bar = est
    elif len(downs):
        ph = np.mod(downs, bar)
        ang = np.angle(np.mean(np.exp(2j * math.pi * ph / bar)))
        anchor = float(np.mod(ang / (2 * math.pi) * bar, bar))
    else:
        anchor = float(t0)
    first = anchor - math.floor(anchor / bar) * bar
    out = [first + i * bar for i in range(int((dur - first) / bar) + 2)]
    return [round(d, 4) for d in out if d < dur + 1e-6], bar / 4.0, True


def analyse(path, rmap=None, vocals=None, sung=None):
    """The song's map: {dur_s, bpm, beat_s, bar_s, steady, bars: [{i, t0, t1, k, low, voc, e, sec, drop, hit}],
    sim (bars x bars), drops, gaps, sections}. bars are whole bars on one grid. voc is how much of a bar is a
    lead voice: the share of its 2-8 kHz (where consonants live, and where a second voice fights a narrator) that
    its most alike bars do not explain, read on the song's vocal stem when there is one (vocals: a file from the
    separator, stems.py), else on the song itself. A looped sample explains itself, sung or not, and a filtered
    one has nothing up there; a verse does. sung=False: the song is known to carry no voice (a library track
    measured as instrumental), so no bar is kept from under the film's voice for a lead line that is an instrument."""
    import rhythm
    from rhythm import dsp
    y = dsp.decode(path, max_s=900.0)
    dur = len(y) / float(dsp.SR)
    if rmap is None:
        rmap = rhythm.analyze_samples(y)
    feat = dsp.features(y)
    downs, beat_s, steady = _grid(rmap, dur)
    lm = np.expm1(np.asarray(feat["logmel"], np.float64))           # frames x 64, linear
    fps = dsp.FPS
    n = len(lm)

    def hz2mel(f):
        return 2595.0 * np.log10(1.0 + f / 700.0)
    pts = 700.0 * (10 ** (np.linspace(hz2mel(30.0), hz2mel(dsp.SR / 2.0), dsp.N_MELS + 2) / 2595.0) - 1.0)
    cen = pts[1:-1]
    pres = (cen >= 2000) & (cen <= 8000)             # consonants: where a lead voice fights a narrator
    low = cen < 150
    rms = np.asarray(feat["rms_db"], np.float64)
    lv_voc = None
    if vocals and os.path.exists(vocals):
        try:
            lv_voc = np.expm1(np.asarray(dsp.features(dsp.decode(vocals, max_s=900.0))["logmel"], np.float64))
        except Exception:  # noqa: BLE001 - the stem is a better reading, never a requirement
            lv_voc = None
    bars = []
    steps = 32
    M = []
    for i in range(len(downs) - 1):
        a, b = downs[i], downs[i + 1]
        if a < -1e-6 or b > dur + 0.02:
            continue
        f0, f1 = int(round(a * fps)), int(round(b * fps))
        if f1 - f0 < steps or f1 > n:
            continue
        seg = lm[f0:f1]
        idx = np.linspace(0, len(seg), steps + 1).astype(int)
        m = np.stack([seg[idx[j]:max(idx[j] + 1, idx[j + 1])].mean(axis=0) for j in range(steps)])
        M.append(m)
        lv = rms[f0:f1]
        head = lm[f0:f0 + max(2, int(0.12 * fps))]
        pre = lm[max(0, f0 - int(0.25 * fps)):max(1, f0 - 2)]
        hit = 10 * math.log10((head[:, low].mean() + 1e-9) / (pre[:, low].mean() + 1e-9)) if len(pre) else 0.0
        bars.append({"i": len(bars), "t0": round(a, 4), "t1": round(b, 4),
                     "k": round(float(10 * np.log10(np.mean(10 ** (lv / 10)) + 1e-12)), 1),
                     "low": round(float(10 * np.log10(seg[:, low].mean() + 1e-9)), 1), "hit": round(float(hit), 1)})
    if not bars:
        return {"dur_s": round(dur, 3), "bpm": rmap.get("bpm"), "beat_s": beat_s, "bar_s": None, "steady": False, "bars": [],
                "sim": [], "drops": [], "gaps": rmap.get("gaps") or [], "sections": rmap.get("sections") or []}
    M = np.stack(M)                                                  # bars x steps x mels
    L = np.log1p(M).reshape(len(M), -1)
    L = L - L.mean(axis=1, keepdims=True)
    L /= np.linalg.norm(L, axis=1, keepdims=True) + 1e-9
    sim = L @ L.T
    k = min(6, len(M) - 1)
    MV = None
    if lv_voc is not None:                             # the vocal stem per bar, on the same steps
        MV = np.zeros_like(M)
        for i, b in enumerate(bars):
            f0, f1 = int(round(b["t0"] * fps)), min(int(round(b["t1"] * fps)), len(lv_voc))
            seg = lv_voc[f0:f1]
            if len(seg) >= steps:
                idx = np.linspace(0, len(seg), steps + 1).astype(int)
                MV[i] = np.stack([seg[idx[j]:max(idx[j] + 1, idx[j + 1])].mean(axis=0) for j in range(steps)])
    pres_ref = float(np.median(M[:, :, pres].sum(axis=(1, 2))))      # a filtered bar has little up there to be a voice
    for i, b in enumerate(bars):
        if k < 2:
            b["voc"] = 0.5
            continue
        order = np.argsort(-sim[i])
        near = [j for j in order if j != i][:k]
        # what the bar's most alike bars do not explain: a looped sample (sung or not) explains itself, a lead does not
        src = MV if MV is not None else M
        res = np.maximum(src[i] - 1.25 * np.median(src[near], axis=0), 0.0)
        b["voc"] = round(float(min(1.0, res[:, pres].sum() / (max(M[i][:, pres].sum(), pres_ref) + 1e-9))), 3)
    if sung is False:
        for b in bars:
            b["voc"] = 0.0
    ks = np.array([b["k"] for b in bars])
    lo, hi = np.percentile(ks, 10), np.percentile(ks, 95)
    secs = rmap.get("sections") or []
    drops = [float(d) for d in rmap.get("drops") or []]
    bar_s = float(np.median([b["t1"] - b["t0"] for b in bars]))
    gaps = rmap.get("gaps") or []
    for b in bars:
        b["e"] = round(float(np.clip((b["k"] - lo) / max(hi - lo, 1e-6), 0, 1)), 3)
        # a bar that ends in a hole of the song is a wind-up: what follows it has to be the entry it winds up to
        b["windup"] = any(g["t1"] > b["t0"] + 0.6 * (b["t1"] - b["t0"]) and g["t0"] < b["t1"] + 0.05 and g["t1"] - g["t0"] >= 0.15 for g in gaps)
        mid_t = 0.5 * (b["t0"] + b["t1"])
        s = next((s for s in secs if s["t0"] <= mid_t < s["t1"]), None)
        b["sec"] = (s or {}).get("label")
        b["sec_start"] = bool(s and abs(s["t0"] - b["t0"]) < 0.3 * bar_s)
        b["drop"] = any(abs(d - b["t0"]) < 0.3 * bar_s for d in drops)
    out = {"dur_s": round(dur, 3), "bpm": rmap.get("bpm"), "beat_s": round(bar_s / 4.0, 5), "bar_s": round(bar_s, 5),
           "steady": bool(steady), "bars": bars, "sim": np.round(sim, 3).tolist(), "drops": drops,
           "gaps": rmap.get("gaps") or [], "sections": secs, "confidence": rmap.get("confidence")}
    F = _features(out)                                               # what the arrangement reads, kept in the map
    for i, b in enumerate(bars):
        b["entry"], b["intro"], b["pre"] = bool(F["entry"][i]), bool(F["intro"][i]), int(F["pre"][i])
        b["nov"] = round(float(1.0 - max(sim[i, j] for j in (i - 1, i - 2, i - 4) if j >= 0)), 3) if i else 1.0
    return out


# ----------------------------------------------------------------- the arrangement

def _spoken_share(words, a, b):
    if b <= a:
        return 0.0
    return min(1.0, sum(max(0.0, min(b, w1) - max(a, w0)) for w0, w1 in words) / (b - a))


def end_hit_time(marks):
    """Where the last hit lands: on the landing the film names, else a breath after the last word, and always
    early enough that the hit has rung out before the end card."""
    dur = float(marks["dur_s"])
    words = marks.get("words") or []
    last = max((w1 for _w0, w1 in words), default=None)
    t = marks.get("end_hit_s")
    if t is None:
        t = (last + 0.18) if last is not None else dur - 1.0
    return t


def _features(song):
    """What the arrangement reads of each bar, as arrays: level 0..1, sung lead, downbeat hit, an entrance (a drop, a
    section that starts 3 dB over what came before, or a full bar 5 dB over the bar before it), the song's intro, how
    many bars to the next entrance."""
    bars = song["bars"]
    nb = len(bars)
    en = np.array([b["e"] for b in bars], np.float64)
    k = np.array([b["k"] for b in bars], np.float64)
    voc = np.array([b["voc"] for b in bars], np.float64)
    hit = np.clip(np.array([b["hit"] for b in bars], np.float64), 0.0, 12.0) / 12.0
    step = np.zeros(nb)
    entry = np.zeros(nb, bool)
    for i, b in enumerate(bars):
        prev = k[max(0, i - 2):i]
        step[i] = float(k[i] - prev.mean()) if len(prev) else 0.0
        if i and b.get("drop") and en[i] >= 0.5:
            entry[i] = True
        elif i and b.get("sec_start") and step[i] >= 3.0 and en[i] >= 0.55:
            entry[i] = True
        elif i and en[i] >= 0.6 and k[i] - k[i - 1] >= 5.0 and en[i] - en[i - 1] >= 0.35:
            entry[i] = True                                       # the song comes in over a bar that held back, whatever the map calls it
    intro = np.zeros(nb, bool)
    if entry.any():
        first = int(np.argmax(entry))
        intro[:first] = en[:first] < 0.45
    else:
        for i, b in enumerate(bars):
            if i and b.get("sec_start"):
                break
            intro[i] = en[i] < 0.35
    pre = np.zeros(nb, np.int32)
    for i in range(nb):
        for d in range(1, 5):
            if i + d < nb and entry[i + d]:
                pre[i] = d
                break
    return {"en": en, "k": k, "voc": voc, "hit": hit, "step": step, "entry": entry, "intro": intro, "pre": pre,
            "windup": np.array([bool(b.get("windup")) for b in bars]), "sec_start": np.array([bool(b.get("sec_start")) for b in bars])}


def _span_beats(L, beat, cuts, t0, from_end, prefer=None):
    """How many beats a stretch of film takes, and the beat's length there. Whole beats inside STRETCH_MAX of the
    song's tempo; among those the count that puts the most picture cuts on beats (a downbeat counts half again),
    whole bars before odd beats, the song's own tempo before a bent one. None when the stretch is too short for any
    whole count inside the limit (the caller keeps the tempo and lets the far end give)."""
    n0 = L / beat
    best = None
    for n in range(max(4, int(math.floor(n0)) - 2), int(math.ceil(n0)) + 3):
        bs = L / n
        f = bs / beat
        if abs(f - 1.0) > STRETCH_MAX + 1e-9:
            continue
        sc = 0.0
        if cuts:
            got = 0.0
            for c in cuts:
                x = (c - t0) / bs
                q = int(round(x))
                if abs(x - q) * bs <= CUT_TOL_S:
                    got += 1.5 if ((n - q) % 4 == 0 if from_end else q % 4 == 0) else 1.0
            sc += 0.8 * got / len(cuts)
        sc += 0.5 if n % 4 == 0 else (0.1 if n % 2 == 0 else 0.0)        # whole bars: the film opens on a downbeat
        sc -= 0.35 * abs(f - 1.0) / STRETCH_MAX
        if prefer is not None:
            sc -= 0.3 * abs(f - prefer) / STRETCH_MAX
        if best is None or sc > best[0]:
            best = (sc, n, bs)
    return (best[1], best[2]) if best else None


ARC_RISE_DB = 9.0                  # the payoff's bars over the film's opening bars, on the song's own level, that an arc run aims for
ARC_PEAK_DB = 1.5                  # a bar this near the payoff's level is the song's peak material


def arc_measures(song, seq, slots, kd, turn=None):
    """How a played bar sequence carries the story's arc, measured on the song's own bar levels (dB): the opening
    (its first bars), the payoff (the drop and the bar after it; the last bars when the film has no drop), the rise
    between them, bars played twice, bars of peak material spent before the turn, and the true joins (a bar that is
    not the one the song plays next)."""
    k = np.array([float(b["k"]) for b in song["bars"]], np.float64)
    n = len(seq)
    if not n:
        return {}
    first = [i for i, s in enumerate(slots) if s["part"] != "pickup"][:4] or [0]
    open_db = float(np.mean([k[seq[i]] for i in first]))
    if kd is not None:
        pay = [seq[i] for i in range(kd, min(n, kd + 2))]
        before = [i for i, s in enumerate(slots) if i < kd and s["role"] not in ("build",)]
    else:
        pay = [seq[i] for i in range(max(0, n - 4), n)]
        before = list(range(0, n // 2))
    if turn is not None and kd is None:
        before = [i for i in before if slots[i]["t0"] < float(turn)]
    pay_db = float(np.mean([k[b] for b in pay]))
    peak_before = sum(1 for i in before if k[seq[i]] >= pay_db - ARC_PEAK_DB)
    repeats = len(seq) - len(set(seq))
    joins = sum(1 for i in range(1, n) if seq[i] != seq[i - 1] + 1)
    return {"open_db": round(open_db, 1), "payoff_db": round(pay_db, 1), "rise_db": round(pay_db - open_db, 1),
            "peak_before_turn": int(peak_before), "repeats": int(repeats), "joins": int(joins)}


def _arc_run(song, F, slots, kd, n_build, J, P, has_free):
    """The story path's arrangement (arrange(arc=True)): the song played by its own measured arc, as one run where it
    can be and never a bar twice. Its quieter part sits under the setup, the bars the song itself plays before its
    peak sit under the build from the turn, and the peak lands on the payoff and plays on to the last hit. The setup
    run may be taken from earlier in the song than the build (one join, forward or around the build) when the song's
    own lead-in is too long or too loud for the film. Returns (bar per slot, cost, measures), or None when the song
    is too short for any run (the caller arranges it the general way)."""
    bars = song["bars"]
    nb = len(bars)
    N = len(slots)
    k = np.array([float(b["k"]) for b in bars], np.float64)
    en, voc, entry, intro, hitn = F["en"], F["voc"], F["entry"], F["intro"], F["hit"]
    k_top = float(np.percentile(k, 95))
    sp = np.array([s["sp"] for s in slots], np.float64)
    vcost = 6.0 * voc if has_free else np.zeros(nb)
    floor = (float(np.percentile(k, 90)) - float(P["build_floor_db"])) if P.get("build_floor_db") is not None else None
    full2 = 0.5 * en + 0.5 * np.append(en[1:], en[-1])
    has_entry = bool(entry.any())

    def lay_cost(idx, seq_part):
        """voice under voice and near-silent bars under the setup, for slots idx played as seq_part"""
        c = 0.0
        for i, b in zip(idx, seq_part):
            c += vcost[b] * sp[i]
            if k[b] < k_top - 26.0:
                c += 1.0                                    # a hole of the song itself under the words, not a quiet part
        return c

    best = None
    if kd is None:
        for a in range(0, nb - N + 1):
            seq = list(range(a, a + N))
            m = len(seq)
            head = float(np.mean(k[seq[:min(4, m)]]))
            tail = float(np.mean(k[seq[max(0, m - 4):]]))
            c = 0.6 * max(0.0, 6.0 - (tail - head))
            c += 1.0 * sum(1 for b in seq[:m // 2] if k[b] >= tail - ARC_PEAK_DB)
            c += 0.75 * sum(1 for b in seq[m // 2:] if en[b] < 0.3)
            c += lay_cost(range(N), seq)
            if best is None or c < best[1]:
                best = (seq, c)
    else:
        ks = kd - n_build                                   # setup slots 0..ks-1, build ks..kd-1, payoff and after kd..N-1
        n_post = N - kd
        for d in range(n_build, nb - n_post + 1):
            post = list(range(d, d + n_post))
            build = list(range(d - n_build, d))
            pay_db = float(np.mean(k[post[:2]]))
            c0 = 0.0
            # the peak lands on the payoff: an entrance that stays full, at the song's own top
            c0 += 4.0 * max(0.95 - full2[d], 0.0) - 0.5 * hitn[d]
            c0 += (0.0 if entry[d] else 2.0) if has_entry else 1.5 * max(0.8 - en[d], 0.0)
            c0 += 0.5 * max(0.0, k_top - 1.0 - pay_db)
            c0 += 0.75 * sum(1 for b in post if en[b] < 0.3)
            for q, b in enumerate(build):                   # the build climbs under the payoff
                c0 += 0.3 * max(0.0, k[b] - (pay_db - 2.0))
                if floor is not None and k[b] < floor:
                    c0 += 3.0
                if q and k[b] < k[build[q - 1]] - 3.0:
                    c0 += 0.5
            c0 += lay_cost(range(ks, N), build + post)
            if ks == 0:
                seq = build + post
                m = arc_measures(song, seq, slots, kd)
                c = c0 + 0.6 * max(0.0, ARC_RISE_DB - m["rise_db"])
                if best is None or c < best[1]:
                    best = (seq, c)
                continue
            taken = set(build + post)
            for a in range(0, nb - ks + 1):
                setup = list(range(a, a + ks))
                if taken.intersection(setup):
                    continue
                c = c0
                nxt = build[0] if build else d
                if setup[-1] + 1 != nxt:                    # one join into the build (or straight into the drop)
                    c += float(J[setup[-1], nxt]) if build else min(float(J[setup[-1], nxt]), 0.45)
                    if setup[0] > nxt:
                        c += 0.5                            # the setup taken from after the payoff's bars: a jump back
                head = float(np.mean(k[setup[:min(4, ks)]]))
                c += 0.6 * max(0.0, ARC_RISE_DB - (pay_db - head))
                c += 1.0 * sum(1 for b in setup if k[b] >= pay_db - ARC_PEAK_DB)
                half = ks // 2
                if half >= 2:                               # the setup itself leans forward, never louder at its start
                    c += 0.3 * max(0.0, float(np.mean(k[setup[:half]])) - float(np.mean(k[setup[half:]])) - 1.0)
                c += lay_cost(range(ks), setup)
                if best is None or c < best[1]:
                    best = (setup + build + post, c)
    if best is None:
        return None
    seq, c = best
    return seq, float(c), arc_measures(song, seq, slots, kd)


def arrange(song, marks, profile=None, arc=None):
    """Which bars of the song play where in the film, chosen by energy and novelty.

    The film opens on a strong bar (a drop or a section's first bar, never the song's intro) and keeps a full part of
    the song under it. Where the story turns the song pulls back into a build of two to four bars (the bars the song
    itself plays before an entrance when it has them), and its drop lands on the payoff: `payoff_s`, or `turn_s` when
    the film names only one. From there the fullest part plays to the last hit, which lands on the film's last
    landing. Bars a voice sings in are kept from under the film's voice while the song has others. A jump goes to a
    bar that sounds like the one that would have come next, at a phrase end, on a picture cut when one is there; the
    drop covers its own join. The tempo bends at most STRETCH_MAX, chosen per stretch so the song's beats fall on the
    picture's cuts; where whole bars do not fit, the film opens on a pickup and a short bar closes into the hit.

    arc=True (opt-in; or marks["arc"]; the workflows' story path, music_film): the song is played by its own measured
    arc instead (_arc_run): a mostly contiguous run whose quieter part sits under the setup, whose own build sits under
    the turn and whose peak lands on the payoff, never a bar twice. The default (the customer Director's bed) is
    unchanged."""
    if arc is None:
        arc = bool(marks.get("arc"))
    bars = song.get("bars") or []
    if len(bars) < 4 or not song.get("bar_s"):
        return {"ok": False, "why": "no bar grid in this song"}
    P = dict(PROFILE, **(profile or {}))
    B = float(song["bar_s"])
    beat = B / 4.0
    dur = float(marks["dur_s"])
    words = sorted(marks.get("words") or [])
    cuts = sorted(float(c) for c in marks.get("cuts") or [])
    last_word = max((w1 for _w0, w1 in words), default=0.0)
    t_end = end_hit_time(marks)
    tail = P["end_tail_beats"] * beat
    if t_end + tail > dur - P["end_gap_s"]:
        t_end = dur - P["end_gap_s"] - tail
    if t_end < 2.0 * B:
        return {"ok": False, "why": "the film is shorter than two bars of this song"}
    F = _features(song)
    en, voc, hitn, entry, intro, pre = F["en"], F["voc"], F["hit"], F["entry"], F["intro"], F["pre"]
    nb = len(bars)
    turn, payoff = marks.get("turn_s"), marks.get("payoff_s")
    drop_t = payoff if payoff is not None else turn                # the drop lands on the payoff; on the turn when it is all the film names
    build_from = turn if (payoff is not None and turn is not None and turn < payoff) else None
    lands = sorted(set(cuts) | {float(x) for x in marks.get("landings") or []})
    dl = marks.get("drop_landing")

    def _snap(t, after=None):
        """The drop on the visual turn: the sound's own turn hit when it sits within DROP_REACH_S (the workflow films:
        the hit and the drop are one landing, never a flam), else the landing or the cut the mark sits on."""
        if not marks.get("snap", True):
            return t
        if dl is not None and abs(float(dl) - t) <= DROP_REACH_S and (after is None or float(dl) - after >= B):
            return float(dl)
        if lands:
            near = min(lands, key=lambda c: abs(c - t))
            if abs(near - t) <= SNAP_S:
                return near
        return t
    if drop_t is not None:
        drop_t = _snap(drop_t, build_from)
    if drop_t is not None and not (2.0 * B <= drop_t <= t_end - 2.0 * B):
        drop_t = None                                              # no room for a build before it or a phrase after it
    drop_on = ("payoff" if payoff is not None else "turn") if drop_t is not None else None

    def _payoff_drop():
        """The payoff takes the drop, the last hit moved into the film's tail: (drop, last hit) or None."""
        p2 = _snap(payoff)
        n_after = int(math.floor((dur - P["end_gap_s"] - tail - p2) / beat + 1e-6))
        if p2 >= 2.0 * B and n_after >= 4:
            return p2, max(t_end, p2 + min(n_after, 8) * beat)
        return None
    if drop_t is None and marks.get("turn_drop") and payoff is not None and dl is not None and abs(float(dl) - payoff) <= DROP_REACH_S:
        # the sound's turn hit sits on the payoff (screen placement): the payoff takes the drop with it when the song's
        # last hit stays where the film's close lands, so the hit and the drop are one before the story's earlier turn
        got = _payoff_drop()
        if got is not None and got[1] <= t_end + 1e-6:
            drop_t, drop_on = got[0], "payoff"
    if drop_t is None and marks.get("turn_drop") and turn is not None and payoff is not None and turn < payoff:
        # A short's payoff is often its last sentence, too close to the last hit for a phrase after it. When the
        # caller handed the story's own turn (opt-in: the podcast workflow), the drop lands there and the payoff
        # keeps the last hit, so the film still has its build and drop instead of a flat bed (round1 thin-sound).
        t2 = _snap(turn)
        if 2.0 * B <= t2 <= t_end - 2.0 * B:
            drop_t, build_from, drop_on = t2, None, "turn"
    if drop_t is None and marks.get("turn_drop") and payoff is not None:
        # No turn the drop can stand on (none named or read, or too close to the start): the payoff takes the drop and
        # the last hit moves into the tail the film already has, up to the end card's gap, so a bar or two plays after
        # it. The hit moves only later, never before the last word's breath.
        got = _payoff_drop()
        if got is not None:
            (drop_t, t_end), drop_on = got, "payoff"
    has_entry = bool(entry.any())
    # ---- the film's beats: one stretch to the drop, one from it to the last hit
    spans = []
    n_build = 0
    if drop_t is None:
        got = _span_beats(t_end, beat, cuts, 0.0, True)
        n1, bs1 = got if got else (int(t_end / beat), beat)
        spans.append({"t0": t_end - n1 * bs1, "t1": t_end, "beats": n1, "beat_s": bs1})
    else:
        got = _span_beats(drop_t, beat, [c for c in cuts if c < drop_t - 0.02], 0.0, True)
        if got:
            n1, bs1 = got
        else:                                                      # a short lead: the song's own tempo, entering on its first whole beat
            bs1 = beat
            n1 = int(drop_t / bs1)
        got = _span_beats(t_end - drop_t, beat, [c for c in cuts if c > drop_t + 0.02], drop_t, False, prefer=bs1 / beat)
        if got:
            n2, bs2 = got
        else:                                                      # a short close: the tempo holds and the last hit moves to its beat
            bs2 = bs1
            n2 = max(4, int(round((t_end - drop_t) / bs2)))
            while drop_t + n2 * bs2 < last_word - 0.06 and drop_t + (n2 + 1) * bs2 + bs2 <= dur - P["end_gap_s"]:
                n2 += 1
            while n2 > 4 and drop_t + n2 * bs2 + min(tail, bs2) > dur - P["end_gap_s"]:
                n2 -= 1
            t_end = drop_t + n2 * bs2
        # the build: from the story's turn to the drop, two to four bars. It starts on a picture cut when one can be
        # reached inside the tempo's limit (the song's phrase ends where the picture's does), else on its own bar line
        want = int(round((drop_t - build_from) / (4.0 * bs1))) if build_from is not None else BUILD_BARS[0]
        want = max(BUILD_BARS[0], min(want, BUILD_BARS[1]))
        n_build = max(0, min(want, n1 // 4 - 2))
        join = None
        if n_build and cuts:
            for nbd in sorted(range(BUILD_BARS[0], BUILD_BARS[1] + 1), key=lambda x: (abs(x - want), -x)):
                if nbd > n1 // 4 - 2 or abs(nbd - want) > 1:        # a cut is worth a bar either way, never a build twice the story's
                    continue
                near = [c for c in cuts if abs((drop_t - c) / (nbd * B) - 1.0) <= STRETCH_MAX and c >= 2.0 * B]
                if near:
                    c = min(near, key=lambda x: abs((drop_t - x) / (nbd * B) - 1.0))
                    got = _span_beats(c, beat, [x for x in cuts if x < c - 0.02], 0.0, True, prefer=(drop_t - c) / (nbd * B))
                    if got:
                        join, n_build = c, nbd
                        n1, bs1 = got
                        break
        if join is not None:
            spans.append({"t0": join - n1 * bs1, "t1": join, "beats": n1, "beat_s": bs1})
            spans.append({"t0": join, "t1": drop_t, "beats": 4 * n_build, "beat_s": (drop_t - join) / (4 * n_build), "build": True})
        else:
            spans.append({"t0": drop_t - n1 * bs1, "t1": drop_t, "beats": n1, "beat_s": bs1})
        spans.append({"t0": drop_t, "t1": t_end, "beats": n2, "beat_s": bs2, "drop": True})
    # ---- slots: a bar each, a pickup at the film's start and a short bar into the last hit where whole bars do not fit
    slots = []
    for si, sp in enumerate(spans):
        n, bs, a = sp["beats"], sp["beat_s"], sp["t0"]
        r, full = n % 4, n // 4
        lead = si == 0                                             # counted back from its end: the odd beats open the film
        if lead and r:
            slots.append({"t0": a, "t1": a + r * bs, "beats": r, "part": "pickup", "span": si, "bs": bs})
            a += r * bs
        for q in range(full):
            slots.append({"t0": a + 4 * q * bs, "t1": a + 4 * (q + 1) * bs, "beats": 4, "part": "full", "span": si, "bs": bs})
        if not lead and r:
            slots.append({"t0": a + 4 * full * bs, "t1": a + n * bs, "beats": r, "part": "short", "span": si, "bs": bs})
    N = len(slots)
    kd = next((i for i, s in enumerate(slots) if spans[s["span"]].get("drop")), None) if drop_t is not None else None
    if kd is None:
        n_build = 0
    first_full = next(i for i, s in enumerate(slots) if s["part"] == "full")
    quiet = marks.get("quiet") or []
    for i, s in enumerate(slots):
        if kd is None:
            s["role"] = "open" if i == first_full else "groove"
        elif i == kd:
            s["role"] = "drop"
        elif i > kd:
            s["role"] = "post"
        elif kd - i <= n_build:
            s["role"], s["q"] = "build", kd - i
        else:
            s["role"] = "open" if i == first_full else "groove"
        s["sp"] = _spoken_share(words, max(s["t0"], 0.0), s["t1"])
        s["quiet"] = any(min(s["t1"], q1) - max(s["t0"], q0) > 0.5 * (s["t1"] - s["t0"]) for q0, q1 in quiet)
    open_s = marks.get("open_s")
    open_near = (np.array([abs(float(b["t0"]) - float(open_s)) <= 0.5 * B for b in bars], np.float64)
                 if open_s is not None else None)
    vfree = voc < VOICED
    has_free = vfree.sum() >= 4
    lowp = np.maximum(0.45 - en, 0.0) / 0.45
    INF = 1e9

    def slot_cost(i):
        s = slots[i]
        role = s["role"]
        vp = 6.0 * voc * s["sp"] if has_free else 0.0             # a sung bar under the film's own voice: two voices, whatever else the bar has
        if role == "build":
            tgt = 0.15 + 0.3 * (n_build - s["q"]) / max(n_build - 1, 1)
            # the song's own bars before an entrance are its build; else a quiet bar, never one that spends the drop
            c = 1.0 * np.abs(en - tgt) - 0.9 * (pre == s["q"]) + 1.2 * np.maximum(en - 0.6, 0.0) + 0.5 * vp
            if P.get('build_floor_db') is not None:
                # The designed hole already removes 18 dB. A near-silent source
                # bar underneath it turned a controlled payoff into a 42 dB gap.
                floor = np.percentile(F['k'],90)-float(P['build_floor_db'])
                c = np.where(F['k'] < floor, INF, c)
        elif role == "drop":                                       # an entrance that stays full: the bar it lands on and the one after
            full2 = 0.5 * en + 0.5 * np.append(en[1:], en[-1])
            c = 4.0 * np.maximum(0.95 - full2, 0.0) - 0.3 * np.clip(F["step"], 0.0, 12.0) / 12.0 - 0.5 * hitn + vp
            if has_entry:
                c = c + np.where(entry, 0.0, INF)
            else:
                c = c + 1.5 * np.maximum(0.8 - en, 0.0)
        else:
            tgt = 0.9 if role == "post" else (0.7 if kd is not None else 0.6 + 0.25 * i / max(N - 1, 1))
            if s["quiet"]:
                tgt = min(tgt, 0.2)
                c = 1.0 * np.abs(en - tgt) + vp
            else:                                                  # a full part: fuller than asked costs nothing (the build and the hole make the drop, not a thin opening)
                c = 1.0 * np.maximum(tgt - en, 0.0) + 2.5 * intro + 2.0 * lowp + vp   # never the intro, never a flat stretch
            if role == "open":
                c = c - 0.5 * (entry | (F["sec_start"] & (en >= 0.6))) - 0.25 * hitn  # the film opens on an entrance
                if open_near is not None:
                    c = c - 1.0 * open_near                    # the chooser's entry point into this song, when it named one
        return c * (s["beats"] / 4.0) if s["part"] != "full" and role not in ("drop",) else c

    sim = np.clip(np.asarray(song["sim"], np.float64), 0.0, 1.0)
    J = np.empty((nb, nb))                                      # J[i, j]: bar j after bar i when j is not the next bar
    for i in range(nb):
        nat = i + 1
        if nat >= nb:                                              # the song ran out: any restart is a jump
            J[i] = np.where(entry | F["sec_start"], 1.0, 1.6)
        else:
            J[i] = np.maximum(1.3 - 0.9 * sim[nat], JUMP_MIN)      # every join is an edit: never free, however alike the bars
        if F["windup"][i]:
            J[i] += np.where(entry, 0.0, 2.0)                      # a wind-up that lands on nothing
        lo = max(0, i - 7)
        J[i, lo:i + 1] += 0.6                                      # the same few bars again: a loop the ear catches
        J[i, i] += 1.0
    natural = np.zeros((nb, nb), bool)
    for i in range(nb - 1):
        natural[i, i + 1] = True
    cost = np.full((N, nb), INF)
    back = np.zeros((N, nb), np.int32)
    run = np.ones((N, nb), np.int32)                               # bars played in a row ending here
    cost[0] = slot_cost(0)
    cols = np.arange(nb)
    for kk in range(1, N):
        sc = slot_cost(kk)
        prev = cost[kk - 1]
        Jk = J.copy()
        if kk == kd:
            Jk[:, entry] = np.minimum(Jk[:, entry], 0.45)          # the drop covers its own join
        r = run[kk - 1]
        pen = np.where(r < MIN_SEG_BARS, 2.0, np.where(r < 4, 0.5, np.where(r % 4 == 0, -0.15, 0.0)))   # a stutter; a phrase cut short; a phrase end
        if slots[kk - 1]["part"] == "pickup":
            pen = pen + 5.0                                        # a pickup leads into its own bar
        t_join = slots[kk]["t0"]
        if cuts and min(abs(c - t_join) for c in cuts) <= CUT_TOL_S:
            pen = pen - 0.2                                        # the song changes where the picture does
        C = prev[:, None] + Jk + pen[:, None]
        C[natural] = np.broadcast_to(prev[:, None], (nb, nb))[natural]
        C[prev >= INF] = INF
        arg = np.argmin(C, axis=0)
        best = C[arg, cols]
        cost[kk] = np.where((best < INF) & (sc < INF), best + sc, INF)
        back[kk] = arg
        run[kk] = np.where(arg == cols - 1, r[arg] + 1, 1)
    # the end: the bar after the last one gives the hit; a strong downbeat or the song's own end is a button
    fin = cost[N - 1].copy()
    for j in range(nb):
        if fin[j] >= INF:
            continue
        nxt = j + 1
        if nxt < nb:
            fin[j] -= 0.15 * hitn[nxt] + (0.25 if F["sec_start"][nxt] else 0.0)
        else:
            fin[j] -= 0.3
        if run[N - 1, j] < MIN_SEG_BARS and N > 1:
            fin[j] += 1.0
    got = _arc_run(song, F, slots, kd, n_build, J, P, has_free) if arc else None
    if got is not None:
        seq, total, _m = got                                       # the story path: the song's own arc, one run, never a bar twice
    else:
        j = int(np.argmin(fin))
        if fin[j] >= INF / 2:
            return {"ok": False, "why": "no arrangement lands the drop"}
        total = float(fin[j])
        seq = [j]
        for kk in range(N - 1, 0, -1):
            j = int(back[kk, j])
            seq.append(j)
        seq.reverse()
    segs = []
    for kk, bi in enumerate(seq):
        s, b = slots[kk], bars[bi]
        bq = (b["t1"] - b["t0"]) / 4.0
        if s["part"] == "pickup":
            sa, sb = b["t1"] - s["beats"] * bq, b["t1"]
        elif s["part"] == "short":
            sa, sb = b["t0"], b["t0"] + s["beats"] * bq
        else:
            sa, sb = b["t0"], b["t1"]
        if segs and segs[-1]["bars"][1] == bi - 1 and segs[-1]["span"] == s["span"] and slots[kk - 1]["part"] != "short":
            segs[-1]["bars"][1] = bi
            segs[-1]["song"][1] = round(sb, 4)
            segs[-1]["film"][1] = round(s["t1"], 4)
        else:
            segs.append({"bars": [bi, bi], "song": [round(sa, 4), round(sb, 4)], "film": [round(s["t0"], 4), round(s["t1"], 4)], "span": s["span"],
                         "tempo": round(beat / s["bs"], 5), "role": s["role"]})
    for sg in segs:                                                # each piece at the tempo that fits its bars to its stretch of film
        sg["tempo"] = round((sg["song"][1] - sg["song"][0]) / max(sg["film"][1] - sg["film"][0], 1e-6), 5)
    last = seq[-1]
    ents = [i for i in range(nb) if entry[i]]
    hit_bar = last + 1 if last + 1 < nb else (ents[0] if ents else int(np.argmax(hitn)))
    grid = []
    for kk, s in enumerate(slots):                                 # the film's beat grid, from the bars that play
        for q in range(s["beats"]):
            t = s["t0"] + q * s["bs"]
            if -1e-6 <= t <= dur:
                grid.append({"t": round(t, 4), "down": q == 0 and s["part"] != "pickup", "bar": seq[kk]})
    on = [c for c in cuts if c < t_end and grid and min(abs(c - g["t"]) for g in grid) <= CUT_TOL_S]
    on_down = [c for c in on if any(g["down"] and abs(c - g["t"]) <= CUT_TOL_S for g in grid)]
    bs_last = spans[-1]["beat_s"]
    room = max(0.0, dur - P["end_gap_s"] - t_end - 0.02)           # the ring is cut 20 ms after its length: both fit before the end card
    tail_s = max(min(P["end_tail_beats_max"] * bs_last, room), min(bs_last, room))
    mark = turn if drop_on == "turn" else (payoff if payoff is not None else turn)
    on_arc = got is not None

    def _setup(k):                                                 # on the story's arc a quiet setup is the arc, not a flat stretch
        return (k < kd - n_build) if kd is not None else (k < N // 2)
    tempos = [beat / sp["beat_s"] for sp in spans]
    return {"ok": True, "segments": segs, "tempo": round(float(np.median(tempos)), 5), "bar_s": round(4.0 * float(np.median([sp["beat_s"] for sp in spans])), 5),
            "beat_s": round(float(np.median([sp["beat_s"] for sp in spans])), 5),
            "spans": [{"t0": round(sp["t0"], 4), "t1": round(sp["t1"], 4), "beats": sp["beats"], "beat_s": round(sp["beat_s"], 5), "tempo": round(beat / sp["beat_s"], 5)} for sp in spans],
            "end_hit_s": round(t_end, 4), "end_hit_bar": int(hit_bar), "end_tail_s": round(tail_s, 4),
            "drop_at": round(drop_t, 4) if kd is not None else None, "drop_is": ("entrance" if has_entry else "step") if kd is not None else None,
            "build_at": round(slots[kd - n_build]["t0"], 4) if kd is not None and n_build else None, "build_bars": n_build,
            "drop_on": drop_on if kd is not None else None,
            "turn_s": turn, "payoff_s": payoff, "turn_miss_s": round(drop_t - mark, 3) if kd is not None and mark is not None else None,
            "slots": N, "jumps": len(segs) - 1, "grid": grid,
            "cuts": {"n": len([c for c in cuts if c < t_end]), "on_beat": len(on), "on_down": len(on_down)},
            "bars_played": [{"bar": int(b), "role": slots[k]["role"], "e": float(en[b]), "t": round(slots[k]["t0"], 2)} for k, b in enumerate(seq)],
            "intro_bars": int(sum(1 for k, b in enumerate(seq) if intro[b] and slots[k]["role"] != "build" and not (on_arc and _setup(k)))),
            "low_bars": int(sum(1 for k, b in enumerate(seq) if en[b] < 0.3 and slots[k]["role"] != "build" and not slots[k]["quiet"] and not (on_arc and _setup(k)))),
            "arc": dict(arc_measures(song, seq, slots, kd, turn), on=on_arc),
            "voiced_under_voice": round(float(np.mean([voc[b] >= VOICED and slots[k]["sp"] > 0.3 for k, b in enumerate(seq)])), 3),
            "cost": round(total, 3)}


# ----------------------------------------------------------------- the track

def _tempo(x, factor, ffmpeg=None):
    """x (n, 2) played `factor` times faster, pitch kept (ffmpeg atempo)."""
    if abs(factor - 1.0) < 0.002 or not len(x):
        return x
    r = subprocess.run([ffmpeg or FF, "-hide_banner", "-nostdin", "-loglevel", "error", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "pipe:0",
                        "-af", f"atempo={factor:.6f}", "-f", "f32le", "-ar", str(SR), "-ac", "2", "pipe:1"],
                       input=np.asarray(x, np.float32).tobytes(), capture_output=True, timeout=900)
    y = np.frombuffer(r.stdout, np.float32).reshape(-1, 2).copy()
    return y if len(y) else x


def _lay(song_pcm, song, plan, dur):
    """The arranged song on the film's clock, before any level: (music, hit_at_sample). Pieces are joined 12 ms
    before their downbeat with a 20 ms equal-power crossfade, so a hit's attack is whole and nothing clicks."""
    n = int(round(dur * SR))
    out = np.zeros((n, 2), np.float32)
    pre = int(JOIN_S * SR)
    xf = int(XFADE_S * SR)
    bars = song["bars"]
    pieces = list(plan["segments"])
    hb = bars[plan["end_hit_bar"]]
    f_last = float(pieces[-1].get("tempo") or plan["tempo"])
    tail_s = float(plan.get("end_tail_s") or PROFILE["end_tail_beats"] * plan["beat_s"])
    pieces = pieces + [{"song": [hb["t0"], hb["t0"] + tail_s * f_last + 0.05], "film": [plan["end_hit_s"], None], "hit": True, "tempo": f_last}]
    for s in pieces:
        f = float(s.get("tempo") or plan["tempo"])
        a = max(0, int(round(s["song"][0] * SR)) - pre)
        b = min(len(song_pcm), int(round(s["song"][1] * SR)) - (0 if s.get("hit") else pre) + xf)
        seg = _tempo(song_pcm[a:b], f)
        at = int(round(s["film"][0] * SR)) - int(round(pre / f))
        if at < 0:
            seg, at = seg[-at:], 0
        seg = seg[:max(0, n - at)].copy()
        if not len(seg):
            continue
        k = min(xf, len(seg) // 2)
        if at > 0 and k:                                # equal-power into what is already there
            w = np.sin(np.linspace(0, math.pi / 2, k, dtype=np.float32))[:, None]
            seg[:k] *= w
            out[at:at + k] *= np.cos(np.linspace(0, math.pi / 2, k, dtype=np.float32))[:, None]
            out[at + k:at + len(seg)] = 0.0
        if s.get("hit"):                                # the last hit: its attack whole, then its own ring dying away
            hold = int(0.12 * SR)
            if len(seg) > hold:
                fade = len(seg) - hold
                seg[hold:] *= (np.linspace(1, 0, fade, dtype=np.float32) ** 1.6)[:, None]
        out[at:at + len(seg)] += seg
    return out, int(round(plan["end_hit_s"] * SR))


def _k_frames(x, hop):
    """K-weighted level of stereo 48 kHz in frames of `hop` samples (dB)."""
    from . import sound_signal as SS
    P = SS.power(x.mean(axis=1) if x.ndim > 1 else x)
    kl = 10 ** (SS.k_level(P) / 10)
    step = max(1, hop // SS.HOP)
    m = len(kl) // step
    return 10 * np.log10(kl[:m * step].reshape(m, step).mean(axis=1) + 1e-12) if m else np.zeros(0)


def _smooth(g, hop_s, attack_s, release_s):
    """A gain curve (dB) that falls no faster than attack and rises no faster than release (falling = ducking)."""
    out = np.empty_like(g)
    cur = g[0]
    down, up = 60.0 * hop_s / max(attack_s, 1e-3) / 6.0, 60.0 * hop_s / max(release_s, 1e-3) / 6.0
    for i, v in enumerate(g):
        if v < cur:
            cur = max(v, cur - down)
        else:
            cur = min(v, cur + up)
        out[i] = cur
    return out


def pauses(words, dur, min_gap):
    """The voice's pauses on the film's clock: [(t0, t1, where)], where = head | mid | tail. A breath shorter than
    min_gap is part of the sentence."""
    ws = sorted(words)
    if not ws:
        return [(0.0, dur, "all")]
    out = []
    if ws[0][0] >= min_gap:
        out.append((0.0, ws[0][0], "head"))
    end = ws[0][1]
    for a, b in ws[1:]:
        if a - end >= min_gap:
            out.append((end, a, "mid"))
        end = max(end, b)
    if dur - end > 0.05:
        out.append((end, dur, "tail"))
    return out


JOINT_HOLE_S = (0.5, 2.3)          # a story's breath at a section joint (the judge: 14 drops of 0.5-2.3 s over the 8 owner reels)
JOINT_DB = {"turn": 16.0, "low": 14.0, "payoff": 18.0, "close": 16.0, "cold_open": 12.0}
CLOSE_BED_DB = 9.0                 # after the close's first word the bed stays down: a dry close under the last line
VOICE_DIP_DB = 18.0                # under a real voice cut into the narration
NOTCH = (3.0, 0.8, 9.5, 0.15)      # a story's accent: -3 dB over 0.8 s, then 9.5 dB more in the last 0.15 s, back on the hit


def _story_joints(t, joints, e, P, notes):
    """dB per frame (negative) for a narrated story's joints (marks["joints"], only a story's scene carries them): at
    each the bed leaves from the last word before it and comes back on the next section's first word (0.5-2.3 s,
    12-20 dB); from the close's first word it stays down; under each real voice it dips. Without joints: nothing.
    Optional per joint (absent, every caller gets exactly the defaults): "db" (the hole's or the dip's depth),
    "bed_db" (a close's dry bed), and the kind "swell" {t, rise_s, db}: the bed rises db (8-12) over rise_s (2-3 s)
    into t and settles back in 0.3 s."""
    out = np.zeros(len(t))
    for j in joints:
        kind = str(j.get("kind") or "turn")
        if kind == "swell":
            x, rise = float(j["t"]), float(j.get("rise_s") or 2.5)
            amt = float(j.get("db") or 10.0)
            if x < 0.5 or x > e - 0.3:
                continue
            out += amt * np.clip(np.minimum((t - (x - rise)) / rise, (x + 0.3 - t) / 0.3), 0.0, 1.0)
            notes.setdefault("swells", []).append({"t": round(x, 3), "rise_s": round(rise, 2), "db": amt})
            continue
        if kind == "voice":
            a, b = float(j["t0"]), float(j["t1"])
            vdb = float(j.get("db") or VOICE_DIP_DB)
            out -= vdb * np.clip(np.minimum((t - (a - 0.08)) / 0.08, (b + 0.1 - t) / 0.12), 0.0, 1.0)
            notes.setdefault("voice_dips", []).append({"t": round(a, 3), "len_s": round(b - a, 3), "db": vdb})
            continue
        b = float(j["next_first_word_t"])
        a = float(j.get("last_word_t") if j.get("last_word_t") is not None else b - JOINT_HOLE_S[0])
        a = min(max(a, b - JOINT_HOLE_S[1]), b - JOINT_HOLE_S[0])
        if b >= e - 0.2 or a < 0.3:
            continue
        db = float(j.get("db") or JOINT_DB.get(kind, 16.0))
        out -= db * np.clip(np.minimum((t - a) / P["hole_in_s"], (b - t) / 0.03), 0.0, 1.0)
        notes["holes"].append({"t": round(a, 3), "len_s": round(b - a, 3), "db": db, "before": kind})
        if kind == "close":
            bed = float(j.get("bed_db") or CLOSE_BED_DB)
            out -= bed * np.clip(np.minimum((t - b) / 0.05, (e - 0.3 - t) / 0.3), 0.0, 1.0)
            notes["close_bed_db"] = -bed
    return out


def _story_notches(t, marks, d, e, P, notes):
    """A story's accents (the film's own sounded moments): the bed eases down 3 dB over 0.8 s, then 9.5 dB more in the
    last 0.15 s, and is back on the hit, so the hit stands out (in place of the swell other films get)."""
    ride = np.zeros(len(t))
    pre, pre_s, cut, cut_s = NOTCH
    for x in sorted(float(v) for v in marks.get("accents") or []):
        if x < 1.0 or x > e - 1.5 or (d is not None and d - P["swell_max_s"] - 0.5 < x < d + 2.0):
            continue
        w1 = np.clip((t - (x - pre_s)) / pre_s, 0.0, 1.0) * (t < x)
        w2 = np.clip((t - (x - cut_s)) / cut_s, 0.0, 1.0) * (t < x)
        ride = np.minimum(ride, -(pre * w1 + cut * w2))
        notes.setdefault("notches", []).append(round(x, 2))
    return ride


def _nearest_hole(notes, x):
    """The time of the joint hole nearest x (a story's merged drop or end hole), None when the story has none."""
    hs = [h["t"] for h in notes.get("holes") or []]
    return min(hs, key=lambda h: abs(h - x)) if hs else None


def flow(nf, hop_s, marks, plan, P, base, open_gain):
    """The level the song is ridden at through the film, dB per 10 ms frame, before the song's own loudness is read:
    (slow, fast, notes). slow: under the voice while it speaks, up in its pauses (more in a longer one, none in a
    breath), a swell into each moment the film itself sounds (marks accents), a swell through the build into the
    drop, a little more after it. fast: the song out for one beat before
    the drop and before the last hit, the hit itself over the bed for a beat. Numbers: PROFILE (the judge's stems)."""
    t = np.arange(nf) * hop_s
    dur = nf * hop_s
    slow = np.full(nf, float(base))
    notes = {"lifts": [], "holes": [], "hits": []}
    lift = np.zeros(nf)
    top = max(0.0, open_gain - base)
    for p0, p1, where in pauses(marks.get("words") or [], dur, P["lift_gap_s"]):
        L = p1 - p0
        if where in ("head", "tail", "all"):
            amt = top
        else:
            amt = float(np.interp(L, [P["lift_gap_s"], P["lift_full_s"]], [P["lift_db"], P["lift_max_db"]]))
        up = np.ones(nf) if where in ("head", "all") else np.clip((t - (p0 + P["lift_wait_s"])) / P["lift_rise_s"], 0.0, 1.0)
        dn = np.ones(nf) if where in ("tail", "all") else np.clip(((p1 - P["duck_ahead_s"]) - t) / P["lift_fall_s"], 0.0, 1.0)
        w = np.minimum(up, dn)
        if w.max() > 0.05:
            lift = np.maximum(lift, amt * w)
            if where == "mid":
                notes["lifts"].append({"t": round(p0, 2), "len_s": round(L, 2), "db": round(amt * float(w.max()), 1)})
    slow += lift
    fast = np.zeros(nf)
    if P.get("phrase_ride_s") and not marks.get("joints"):
        return slow, fast, notes
    d = plan.get("drop_at")
    e = plan["end_hit_s"]
    # A short's payoff often leaves fewer than two full bars before the close.
    # arrange() correctly keeps that phrase intact, but the semantic landing
    # must still have its swell and breath. Ride the existing bars on the exact
    # payoff word instead of silently dropping all payoff dynamics with the join.
    if d is None:
        payoff = marks.get("payoff_s")
        if payoff is not None and 1.0 <= float(payoff) <= e - 0.15:
            d = float(payoff)
            notes["payoff_flow_s"] = d
    ride = np.zeros(nf)                                             # the music rises into the film's own sounded moments and sinks after them
    for x in sorted(float(v) for v in marks.get("accents") or []):
        if x < 1.0 or x > e - 1.5 or (d is not None and d - P["swell_max_s"] - 0.5 < x < d + 2.0):
            continue
        up = np.clip((t - (x - P["accent_rise_s"])) / P["accent_rise_s"], 0.0, 1.0) ** 1.5
        dn = np.clip(1.0 - (t - x) / P["accent_fall_s"], 0.0, 1.0)
        ride = np.maximum(ride, P["accent_db"] * np.where(t < x, up, dn))
        notes.setdefault("accents", []).append(round(x, 2))
    joints = marks.get("joints")
    if joints:
        ride = _story_notches(t, marks, d, e, P, notes)          # a story's accents: a notch before each hit, never a swell
    slow += ride
    if joints:
        fast += _story_joints(t, joints, e, P, notes)
    if d is not None:
        bs = next((sp["beat_s"] for sp in plan.get("spans") or [] if abs(sp["t1"] - d) < 1e-3), plan["beat_s"])
        hole = float(np.clip(bs, P["hole_min_s"], P["hole_max_s"]))
        b0 = plan.get("build_at")
        s0 = max(d - P["swell_max_s"], b0 if b0 is not None else d - 8 * bs)
        x = np.clip((t - s0) / max(d - hole - s0, 1e-3), 0.0, 1.0)
        slow += P["swell_db"] * (x ** 1.5) * (t < d)                # the build rises into the drop
        after = np.clip(1.0 - (t - (d + bs)) / (4 * bs), 0.0, 1.0)
        slow += np.where(t >= d, P["after_drop_db"], 0.0)
        fast += np.where(t >= d, (P["hit_db"] - P["after_drop_db"]) * np.where(t < d + bs, 1.0, after), 0.0)
        h0 = d - hole
        if joints:
            # a narrated story: the arrangement's drop breathes in the nearest joint's hole, never a hole of its own
            # (the judge's one to three drops a reel)
            notes.setdefault("holes_merged", []).append({"t": round(h0, 3), "before": "drop", "into": _nearest_hole(notes, h0)})
        else:
            fast -= P["hole_db"] * np.clip(np.minimum((t - h0) / P["hole_in_s"], (d - JOIN_S - 0.004 - t) / P["hole_out_s"]), 0.0, 1.0)
            notes["holes"].append({"t": round(h0, 3), "len_s": round(hole, 3), "db": P["hole_db"], "before": "drop"})
        notes["hits"].append({"t": round(d, 3), "db": P["hit_db"], "what": "drop"})
        pay = marks.get("payoff_s")
        if plan.get("drop_on") == "turn" and pay is not None and d + 2 * bs <= float(pay) <= e - 0.15 and not joints:
            # the drop went on the turn (the payoff had no phrase after it): the payoff keeps its own breath and hit
            pay = float(pay)
            ph = min(hole, 0.5 * (pay - d - bs))
            fast -= P["hole_db"] * np.clip(np.minimum((t - (pay - ph)) / P["hole_in_s"], (pay - JOIN_S - 0.004 - t) / P["hole_out_s"]), 0.0, 1.0)
            fast += np.where((t >= pay) & (t < pay + bs), P["hit_db"] - P["after_drop_db"], 0.0)
            notes["holes"].append({"t": round(pay - ph, 3), "len_s": round(ph, 3), "db": P["hole_db"], "before": "payoff"})
            notes["hits"].append({"t": round(pay, 3), "db": P["hit_db"], "what": "payoff"})
            notes["payoff_flow_s"] = pay
    bs = (plan.get("spans") or [{"beat_s": plan["beat_s"]}])[-1]["beat_s"]
    hole = float(np.clip(bs, P["hole_min_s"], P["hole_max_s"]))
    if joints:
        notes.setdefault("holes_merged", []).append({"t": round(e - hole, 3), "before": "last hit", "into": _nearest_hole(notes, e - hole)})
    else:
        fast -= P["end_hole_db"] * np.clip(np.minimum((t - (e - hole)) / P["hole_in_s"], (e - JOIN_S - 0.004 - t) / P["hole_out_s"]), 0.0, 1.0)
        notes["holes"].append({"t": round(e - hole, 3), "len_s": round(hole, 3), "db": P["end_hole_db"], "before": "last hit"})
    slow = np.where(t >= e - JOIN_S, np.maximum(slow, open_gain + P["end_hit_db"]), slow)
    notes["hits"].append({"t": round(e, 3), "db": P["end_hit_db"], "what": "last hit"})
    return slow, fast, notes


def story_arc(marks):
    """The arc a film's music follows, {part: low | mid | high} for setup, build, payoff and close: the story's own
    (marks["story_arc"], a model's reading of its beats, music_film.story_marks) where it names a part, else
    ARC_DEFAULT."""
    got = marks.get("story_arc") or {}
    return {k: (got.get(k) if got.get(k) in ARC_DB else ARC_DEFAULT[k]) for k in ARC_PARTS}


def arc_parts(marks, plan, words, dur):
    """The story's parts on the film's clock, through its last word: [(part, t0, t1)]. The setup runs to the turn (else
    the build the arrangement played, else 4 s before the payoff), the build to the payoff, the payoff to the close
    (marks["close_s"]: where the story keeps talking after it pays off) or the last word. A film with one mark pays off
    there; a film with none is all setup. The tail after the last word belongs to the close. With no close_s read, the
    close is the voice's last phrase after the payoff (last_phrase), so a low or a high close is heard on its words."""
    last = max((b for _a, b in words), default=dur)
    pay, turn = marks.get("payoff_s"), marks.get("turn_s")
    if pay is None:
        pay, turn = turn, None
    if pay is None and plan.get("drop_at") is not None:
        pay = plan["drop_at"]
    close = marks.get("close_s")
    if pay is None:
        close = float(close) if close is not None and 1.0 < float(close) < last - 0.3 else None
        parts = [("setup", 0.0, close if close is not None else last)] + ([("close", close, last)] if close is not None else [])
        return [p for p in parts if p[2] - p[1] > 0.05]
    pay = float(pay)
    if turn is not None and 0.3 < float(turn) < pay - 0.5:
        b0 = float(turn)
    elif plan.get("build_at") is not None and 0.3 < float(plan["build_at"]) < pay - 0.5:
        b0 = float(plan["build_at"])
    else:
        b0 = max(0.0, pay - 4.0)
    close = float(close) if close is not None and pay + 1.0 < float(close) < last - 0.3 else None
    if close is None and marks.get("close_s") is None:
        close = last_phrase(words, pay + 1.0, last)
    parts = [("setup", 0.0, b0), ("build", b0, pay), ("payoff", pay, close if close is not None else max(last, pay))]
    if close is not None:
        parts.append(("close", close, last))
    return [p for p in parts if p[2] - p[1] > 0.05]


def last_phrase(words, after, last, gap_s=0.35, min_s=1.5, max_s=6.0):
    """Where the voice's last phrase starts (the word after its last pause of gap_s or more), when it starts after
    `after` and lasts min_s to max_s to the last word; else None."""
    ws = sorted(words)
    for (_a0, b0), (a1, _b1) in zip(ws[::-1][1:], ws[::-1]):
        if a1 - b0 >= gap_s and after <= a1 and min_s <= last - a1 <= max_s:
            return float(a1)
        if last - a1 > max_s:
            break
    return None


def arc_target(t, parts, arc, under_db):
    """The depth (dB under the voice) the bed's words sit at, per frame: each part at its arc's depth, the build moving
    from the setup's depth to its own (it climbs into the payoff, or pulls back when the story darkens there), the
    tail after the last word at the close's."""
    depth = {k: under_db + ARC_DB[arc[k]] for k in ARC_PARTS}
    out = np.full(len(t), depth[parts[0][0]] if parts else depth["setup"])
    end = 0.0
    for name, a, b in parts:
        sel = (t >= a) & (t < b)
        if name == "build":
            start = depth["setup"] if parts[0][0] == "setup" and a > 0.0 else depth["build"]
            out[sel] = start + (depth["build"] - start) * np.clip((t[sel] - a) / max(b - a, 1e-3), 0.0, 1.0)
        else:
            out[sel] = depth[name]
        end = max(end, b)
    out[t >= end] = depth["close"]
    return out


def _pmean_db(x, w):
    """Power mean of a dB curve over w frames, centred."""
    if w <= 1:
        return np.asarray(x, np.float64)
    return 10 * np.log10(np.convolve(10 ** (np.asarray(x, np.float64) / 10), np.ones(w) / w, mode="same") + 1e-12)


def _spread_min(x, h):
    """Each frame takes the lowest value within h frames either side (a dip covers the window it is read on)."""
    out = np.array(x, np.float64)
    for k in range(1, h + 1):
        out[:-k] = np.minimum(out[:-k], x[k:])
        out[k:] = np.minimum(out[k:], x[:-k])
    return out


def _phrase_levels(vk, speech, hop_s, quiet_db, gap_s):
    """The voice's level per frame of each phrase (a run of speech through breaths shorter than gap_s): the power
    mean of its voiced frames (above quiet_db). -120 outside speech."""
    nf = len(vk)
    out = np.full(nf, -120.0)
    on = np.array(speech[:nf], bool)
    if not on.any():
        return out
    # close the breaths
    gap = int(round(gap_s / hop_s))
    idx = np.flatnonzero(on)
    runs, s0, prev = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - prev > gap:
            runs.append((s0, prev + 1))
            s0 = i
        prev = i
    runs.append((s0, prev + 1))
    for a, b in runs:
        seg = vk[a:b]
        seg = seg[seg > quiet_db]
        if len(seg):
            out[a:b] = 10 * np.log10(np.mean(10 ** (seg / 10)) + 1e-12)
    return out


def word_gaps(vk, mk_g, words, live=None, hop_s=0.01, quiet_db=None):
    """Per word: (mid second, voice dB, music dB, voice minus music) on the K levels of the voice and of the music as
    mixed (mk + gain), each the power mean over the word's frames. A word the bed is out under (a hole) or that is
    quieter than quiet_db is left out."""
    nf = min(len(vk), len(mk_g))
    rows = []
    for a, b in words:
        i0 = int(a / hop_s)
        i1 = min(nf, max(i0 + 1, int(math.ceil(b / hop_s))))
        if i0 >= nf:
            continue
        lv_ = np.ones(i1 - i0, bool) if live is None else live[i0:i1]
        if lv_.sum() * 2 < (i1 - i0):
            continue
        V = 10 * np.log10(np.mean(10 ** (vk[i0:i1] / 10)) + 1e-12)
        M = 10 * np.log10(np.mean(10 ** (mk_g[i0:i1][lv_] / 10)) + 1e-12)
        if quiet_db is not None and V < quiet_db:
            continue
        rows.append((0.5 * (a + b), float(V), float(M), float(V - M)))
    return rows


def _part_moves(t, rows, parts, target, raise_max, edge, lo=-30.0):
    """How far each part of the story moves to its depth: (correction dB per frame, part notes). A part's words' gaps
    against the arc's depth are read on each half of the part (a median each), so the bed holds its depth through the
    part whatever the song does inside it; the change between parts falls inside `edge` before a part's first word."""
    hop_s = float(t[1] - t[0]) if len(t) > 1 else 0.01
    nf = len(t)
    knots, notes, carry = [], [], None
    for name, a, b in parts:
        err = [(tm, gap - target[min(nf - 1, int(tm / hop_s))]) for tm, _v, _m, gap in rows if a <= tm < b]
        if len(err) >= 3:
            if len(err) >= 6 and b - a >= 3.0:
                h = len(err) // 2
                v0, v1 = float(np.median([e for _x, e in err[:h]])), float(np.median([e for _x, e in err[h:]]))
                x0, x1 = float(np.median([x for x, _e in err[:h]])), float(np.median([x for x, _e in err[h:]]))
            else:
                v0 = v1 = float(np.median([e for _x, e in err]))
                x0, x1 = a, b
            v0, v1 = float(np.clip(v0, lo, raise_max)), float(np.clip(v1, lo, raise_max))
        elif carry is not None:
            v0 = v1 = carry
            x0, x1 = a, b
        else:
            notes.append({"part": name, "t": [round(a, 2), round(b, 2)], "words": len(err), "moved_db": None})
            continue
        slope = (v1 - v0) / max(x1 - x0, 1e-3)
        e0 = max(a, min((x for x, _e in err), default=a) - edge)
        knots += [(e0 + 1e-3, float(np.clip(v0 + slope * (e0 - x0), min(v0, v1), max(v0, v1)))), (x0, v0), (x1, v1),
                  (b - 1e-3, float(np.clip(v0 + slope * (b - x0), min(v0, v1), max(v0, v1))))]
        carry = v1
        notes.append({"part": name, "t": [round(a, 2), round(b, 2)], "words": len(err), "moved_db": [round(v0, 1), round(v1, 1)]})
    if not knots:
        return None, notes
    knots.sort()
    return np.interp(t, np.array([k[0] for k in knots]), np.array([k[1] for k in knots])), notes


def _resting(rows, g, floored, parts, hop_s, hole_db=6.0):
    """The words a part's depth is read on: the bed's resting level, not its designed moments. A word the engine's
    ride holds hole_db or more under the part's usual level (a hole before a hit, the end's hole) and a word the floor
    already pulled down (floored) are left out, so a part keeps its depth beside them."""
    nf = len(g)
    out = []
    for name, a, b in parts:
        sel = [r for r in rows if a <= r[0] < b]
        if not sel:
            continue
        gi = np.array([g[min(nf - 1, int(r[0] / hop_s))] for r in sel])
        fl = np.array([floored[min(nf - 1, int(r[0] / hop_s))] for r in sel])
        keep = (gi >= float(np.median(gi)) - hole_db) & (fl > -1.0)
        if keep.sum() < 3:
            keep = gi >= float(np.median(gi)) - hole_db
        out += [r for r, k in zip(sel, keep) if k]
    return out


def hold_under_words(t, mk, g, vk, live, spoken, words, marks, plan, P, v_ref, phrase_hit=None):
    """The bed held under the voice on the story's arc (profile "hold"): (gain dB per frame, notes).

    1. Each part of the story (arc_parts) is moved as a whole to its arc's depth (_part_moves: its words' gaps, voice
       minus music, K, read on each half of the part), so a song that climbs inside a part is held level and no part
       inherits the song's own crest. A quieter part comes up by at most hold_raise_max_db. The depths are the arc's
       own (ARC_DB about under_voice_db), never re-centred, so the story's contrast is what the listener hears.
    2. After the last word the close decides: a low close stays at its depth to the end (a quiet reflective close
       stays quiet, its last hit with it); a mid or high close lets the song ring out at its own level.
    3. The floor, read on the voice itself: wherever the voice speaks (its 0.4 s level within hold_active_db of its
       90th percentile, or a stamped word), the bed's 0.4 s level stays hold_floor_db under the phrase's level and
       hold_frame_db under the voice's own 0.4 s level. A phrase's level is one number, so the bed does not follow
       each word; where the floor bites, the dip covers its window and is eased in and out.
    Then 1 and 3 once more, so a part the floor pulled under its depth comes back to it, and the floor holds."""
    hop_s = float(t[1] - t[0]) if len(t) > 1 else 0.01
    nf = len(g)
    arc = story_arc(marks)
    parts = arc_parts(marks, plan, words, nf * hop_s)
    floor = float(P["hold_floor_db"])
    target = np.maximum(arc_target(t, parts, arc, P["under_voice_db"]), floor + float(P["hold_room_db"]))   # the closest part still leaves the floor room
    notes = {"arc": arc, "by": "story" if marks.get("story_arc") else "default",
             "parts": [{"part": n, "t": [round(a, 2), round(b, 2)], "depth_db": round(float(np.median(target[int(a / hop_s):max(int(a / hop_s) + 1, int(b / hop_s))])), 1)}
                       for n, a, b in parts]}
    quiet = v_ref - 20.0
    raise_max, edge = float(P["hold_raise_max_db"]), float(P["hold_edge_s"])
    last = max(b for _a, b in words)
    tail = t > last + 0.1
    w4 = max(1, int(round(0.4 / hop_s)))
    vk4 = _pmean_db(vk, w4)
    # where the voice speaks, read on the voice itself as well as on the word stamps (a word's tail, a missed word)
    heard = vk4 > -70.0
    p90 = float(np.percentile(vk4[heard], 90)) if heard.any() else v_ref
    act = vk4 >= p90 - float(P["hold_active_db"])
    speech = act | (spoken[:nf] & (vk4 > quiet))
    ph = _phrase_levels(vk, speech, hop_s, quiet, float(P["hold_phrase_gap_s"]))
    ref = np.where(speech, np.minimum(np.maximum(ph, v_ref - 12.0) - floor,
                                      np.maximum(vk4, p90 - float(P["hold_deep_db"])) - float(P["hold_frame_db"])), 99.0)
    g2 = np.array(g, np.float64)
    moved = []
    floored = np.zeros(nf)
    for rnd in range(3):
        rows = _resting(word_gaps(vk, mk + g2, words, live, hop_s, quiet_db=quiet), g, floored, parts, hop_s)
        corr, pn = _part_moves(t, rows, parts, target, raise_max if rnd == 0 else 3.0, edge, lo=-30.0 if rnd == 0 else -3.0)
        if corr is None:
            if rnd == 0:
                notes["why"] = "no word the bed plays under"
                return g, notes
            break
        if arc["close"] != "low":
            # the song rings out at its own level after the last word (the hit and its ring, as the engine laid them)
            corr = np.where(t > last, corr * np.clip(1.0 - (t - last - 0.1) / 0.4, 0.0, 1.0), corr)
        g2 = g2 + _smooth(corr, hop_s, 0.4, 0.4)
        moved.append(pn)
        if arc["close"] == "low" and tail.any():
            # a quiet close stays quiet: after the last word the bed keeps the close's depth under the voice's level
            m4 = _pmean_db(np.where(live, mk + g2, -120.0), w4)
            cap = v_ref - max(float(P["under_voice_db"]) + ARC_DB["low"], floor + float(P["hold_room_db"]))
            over = np.where(tail & live, np.clip(m4 - cap, 0.0, 40.0), 0.0)
            if over.max() > 0.05:
                g2 = g2 + _smooth(_spread_min(-over, w4 // 2), hop_s, 0.1, 0.3)
            notes["close_tail_db"] = round(float(-over.max()), 1)
        for _ in range(8):
            m4 = _pmean_db(np.where(live, mk + g2, -120.0), w4)
            over = np.where(speech, m4 - ref, 0.0)
            if over.max() <= 0.05:
                break
            cl = _smooth(_spread_min(-np.clip(over, 0.0, 40.0), int(round(P["hold_spread_s"] / hop_s))), hop_s,
                         P["hold_attack_s"], P["hold_release_s"])   # the whole window it is read on, and gently
            g2 = g2 + cl
            floored += cl
    if phrase_hit is not None and P.get("phrase_ride_s") and not marks.get("joints"):
        g2 = phrase_ride(mk, g2, spoken & live, hop_s, P["phrase_ride_s"], phrase_hit, vk, words)
    for pn in moved:
        for row in pn:
            for nt in notes["parts"]:
                if nt["part"] == row["part"]:
                    nt.setdefault("moved_db", []).append(row["moved_db"])
                    nt["words"] = row["words"]
    m4 = _pmean_db(np.where(live, mk + g2, -120.0), w4)
    after = word_gaps(vk, mk + g2, words, live, hop_s, quiet_db=quiet)
    rest = {r[0] for r in _resting(after, g, np.zeros(nf), parts, hop_s)}
    for nt in notes["parts"]:
        a, b = nt["t"]
        gp = [r[3] for r in after if a <= r[0] < b]
        gr = [r[3] for r in after if a <= r[0] < b and r[0] in rest]
        nt["word_gap_db"] = round(float(np.median(gp)), 1) if gp else None
        nt["resting_gap_db"] = round(float(np.median(gr)), 1) if gr else None      # the bed's level beside the ride's holes
    gaps = np.array([r[3] for r in after]) if after else np.zeros(0)
    notes.update(floor_db=floor, floored_frames=int((floored < -0.5).sum()), floored_max_db=round(float(-floored.min()), 1) if nf else 0.0,
                 words=len(gaps), word_gap_db=round(float(np.median(gaps)), 1) if len(gaps) else None,
                 words_over=round(float(np.mean(gaps < 0)), 3) if len(gaps) else None,
                 voiced_over=round(float(np.mean(m4[act] > vk4[act])), 3) if act.any() else None)
    return g2, notes


def phrase_ride(music_db, gain, spoken, hop_s, seconds, hit_frame, voice_db, words):
    """Follow the depth established under phrases, leaving attacks and the final ring intact.

    Measure the arranged music itself before riding it. An incoming bar can
    be much louder than the outgoing bar even at identical fader settings.
    """
    stop = min(len(gain), max(1, hit_frame))

    def mean_level(db, window_s):
        half = min(stop - 1, round(window_s / (2 * hop_s)))
        power = 10 ** (db / 10)
        padded = np.pad(power, half, mode="edge")
        return 10 * np.log10(np.convolve(padded, np.ones(2 * half + 1) / (2 * half + 1), "valid") + 1e-12)

    # The dialogue sets the resting level over several seconds. Only the
    # recording drives the shorter compensating move, independent of syllables.
    source = mean_level(music_db[:stop], 1.6)
    resting = mean_level(music_db[:stop] + gain[:stop], seconds)
    target = resting - source
    adjustment = np.clip(target - gain[:stop], -12., 8.)
    result = gain.copy()
    result[:stop] += adjustment * 0.95
    result[stop:] += adjustment[-1] * 0.95
    # Keep the calibrated level under words. Smoothing valleys must not
    # lift the entire recording beside the speaker.
    active = spoken[:stop]
    if active.any():
        before = mean_level(music_db[:stop] + gain[:stop], .4)
        after = mean_level(music_db[:stop] + result[:stop], .4)
        result += np.median(before[active]) - np.median(after[active])
    # Give softer words room without following syllables. Approach each
    # required depth on a slow ramp and return even more gradually.
    words = [(a, b) for a, b in words if 0 <= a < b and a < len(gain) * hop_s]
    rows = word_gaps(voice_db, music_db + result, words)
    if rows:
        audible = np.median([row[1] for row in rows]) - 10.
        # A consistent margin across the reel avoids turning an isolated
        # soft word into an audible swell on either side of it.
        gaps = [gap for _, level, _, gap in rows if level >= audible]
        margin = min(max(0., 0.15 - np.percentile(gaps, 3)), max(0., 6.8 - np.median(gaps)))
        correction = np.full(stop, margin)
        for (a, b), (_, level, _, gap) in zip(words, rows):
            if level >= audible and gap < 0.15:
                lo, hi = max(0, int(a / hop_s)), min(stop, math.ceil(b / hop_s))
                correction[lo:hi] = np.maximum(correction[lo:hi], 0.15 - gap)
        for i in range(stop - 2, -1, -1):
            correction[i] = max(correction[i], correction[i + 1] - 2.0 * hop_s)
        for i in range(1, stop):
            correction[i] = max(correction[i], correction[i - 1] - 1.0 * hop_s)
        result[:stop] -= correction
        result[stop:] -= correction[-1]
    return result


def track(path, song, plan, marks, voice=None, profile=None, song_pcm=None):
    """The music on the film's clock with its levels solved: (stereo float32 at 48 kHz, report). voice: the film's
    own voice at 48 kHz (mono or stereo) as the mix will carry it, for the masking check; None skips the check.

    Levels (K-weighted), fitted to the judge's music stems (PROFILE): under the voice by under_voice_db while a word
    is spoken; up in a pause of lift_gap_s or more, by more the longer it is, and down again before the next word
    (a breath gets nothing: the judge's beds do not pump); at open_db before the first word and after the last; a
    swell through the build, the song out for one beat, then the drop over the bed for a beat; the last hit with its
    ring, then nothing before the end card. Then the speech band alone is dipped where it would still sit within
    mask_db of the voice's syllables. The song's own quiet and loud parts keep their distance (quiet_max_db)."""
    P = dict(PROFILE, **(profile or {}))
    dur = float(marks["dur_s"])
    if song_pcm is None:
        song_pcm = _decode(path, SR, 2)
    music, hit_at = _lay(song_pcm, song, plan, dur)
    n = len(music)
    hop = 480                                           # 10 ms
    hop_s = hop / SR
    nf = n // hop
    words = sorted(marks.get("words") or [])
    spoken = np.zeros(nf, bool)
    for w0, w1 in words:
        spoken[max(0, int(w0 / hop_s)):min(nf, int(math.ceil(w1 / hop_s)))] = True
    # the song's own loudness where it plays, so "under the voice" means the same on any song
    mk = _k_frames(music, hop)[:nf]
    mk = np.pad(mk, (0, max(0, nf - len(mk))), constant_values=-120.0)
    live = mk > -60
    # the song's level is its loud part's (the 90th percentile of its 2 s levels): that part sits under_voice_db under
    # the voice, and a quieter part (a build) keeps its place under it, inside quiet_max_db
    w = int(2.0 / hop_s)
    pw = 10 ** (np.where(live, mk, -120.0) / 10)
    loc = 10 * np.log10(np.convolve(pw, np.ones(w) / w, mode="same") + 1e-12) if nf > w else mk
    song_lv = float(np.percentile(loc[live], 90)) if live.any() else -30.0
    # read bar by bar where the plan has bars: a build after a full part is lifted from its first beat, not once a
    # two-second window has left the part before it
    downs = sorted(g_["t"] for g_ in plan.get("grid") or [] if g_.get("down")) + [float(plan["end_hit_s"])]
    for a_, b_ in zip(downs, downs[1:]):
        i0, i1 = max(0, int(a_ / hop_s)), min(nf, int(b_ / hop_s))
        if i1 - i0 >= 10:
            loc[i0:i1] = 10 * np.log10(pw[i0:i1].mean() + 1e-12)
    base = VOICE_LUFS - P["under_voice_db"] - song_lv
    open_gain = VOICE_LUFS - P["open_db"] - song_lv
    slow, fast, notes = flow(nf, hop_s, marks, plan, P, base, open_gain)
    slow = slow + np.clip((song_lv - P["quiet_max_db"]) - loc, 0.0, 8.0) * live
    slow = _smooth(slow, hop_s, P["duck_attack_s"], P["duck_release_s"])
    hit_f = min(nf - 1, int(hit_at / hop))
    slow[hit_f:] = np.maximum(slow[hit_f:], open_gain + P["end_hit_db"])
    # the judge's ruler (FLOW.json): the voice's level while it speaks against the music's 0.4 s level under its words.
    # The whole ride is moved so the film reads under_voice_db on that ruler, whatever the song's own crest is
    v_ref = VOICE_LUFS - VOICE_SPEECH_DB
    vk = None
    if voice is not None and len(voice) and spoken.any():
        vk = _k_frames(np.asarray(voice, np.float32), hop)[:nf]
        sv = vk[spoken[:len(vk)]]
        if len(sv) > 20:
            v_ref = float(np.median(sv[sv > np.percentile(sv, 20)]))
    sp = spoken & live
    ruler = None
    if sp.sum() > 50:
        kk4 = int(0.4 / hop_s)
        m04 = 10 * np.log10(np.convolve(10 ** ((mk + slow) / 10) * live, np.ones(kk4) / kk4, mode="same") + 1e-12)
        ruler = v_ref - float(np.median(m04[sp]))
        slow = slow + (ruler - P["under_voice_db"])
        open_gain += ruler - P["under_voice_db"]
    g = slow + fast
    t = np.arange(nf) * hop_s
    held = None
    if P.get("hold") and vk is not None and len(vk) and words:
        # the story's arc under every word, and never a voiced 400 ms frame with the bed near the voice
        vk_ = np.pad(vk, (0, max(0, nf - len(vk))), constant_values=-120.0)[:nf]
        g, held = hold_under_words(t, mk, g, vk_, live, spoken, words, marks, plan, P, v_ref, phrase_hit=hit_f)
    if plan["segments"][0]["film"][0] > 0.02 or plan["segments"][0].get("role") != "open":
        g = np.where(t < 0.08, g - 30.0 * (1 - np.clip(t / 0.08, 0, 1)), g)       # an entry inside a bar comes in over 80 ms
    # the speech band alone, where the bed would still crowd the voice
    carve = np.zeros(nf)
    rep_mask = 0
    if voice is not None and len(voice) and spoken.any():
        from . import sound_signal as SS
        v = np.asarray(voice, np.float32)
        v = v.mean(axis=1) if v.ndim > 1 else v
        Pv = SS.power(v[:n])
        Pm = SS.power(music.mean(axis=1))
        band = (SS._HZ >= 300) & (SS._HZ <= 3400)
        step = hop // SS.HOP
        m = min(min(len(Pv), len(Pm)) // step, nf)

        def lv(Pp):
            e = Pp[:m * step][:, band].sum(axis=1).reshape(m, step).mean(axis=1)
            return 10 * np.log10(e + 1e-12)
        vb, mb = lv(Pv), lv(Pm) + g[:m]
        kk = 15                                         # the voice's syllable level: its loudest frame within 0.15 s
        pad = np.pad(vb, kk, mode="edge")
        vs = np.max(np.stack([pad[j:j + len(vb)] for j in range(2 * kk + 1)]), axis=0)
        need = mb - (vs - P["mask_db"])
        need[~spoken[:m]] = 0.0
        need = np.clip(need, 0.0, P["carve_max_db"])
        rep_mask = int((need > 0.5).sum())
        carve[:m] = -need
        la2 = 6
        cl = carve.copy()
        for k in range(1, la2 + 1):
            cl[:-k] = np.minimum(cl[:-k], carve[k:])
        carve = _smooth(cl, hop_s, 0.05, 0.2)
    out = _apply_gains(music, g, carve, hop)
    end_at = min(n, hit_at + int((float(plan.get("end_tail_s") or P["end_tail_beats"] * plan["beat_s"]) + 0.02) * SR))
    out[end_at:] = 0.0
    broadband_gain = 0.
    if P.get('broadband_bed_db') is not None and voice is not None and len(voice) and held is None:
        # one global gain on the broadband ruler; a bed held word by word (hold) already sits where its arc put it
        from .audibility import calibrate_bed
        out, broadband_gain = calibrate_bed(voice, out, P['broadband_bed_db'])
    kk4 = int(0.4 / hop_s)
    lvl = 10 * np.log10(np.convolve(10 ** ((mk + g) / 10) * live, np.ones(kk4) / kk4, mode="same") + 1e-12)       # the music as mixed, 0.4 s K
    rest = (~spoken) & live & (t < plan["end_hit_s"])
    s1 = np.convolve(slow, np.ones(100) / 100, mode="same") if nf > 100 else slow
    rep = {"song_k": round(song_lv, 1), "base_db": round(float(base), 1), "voice_k": round(v_ref, 1),
           "under_voice_db": round(float(v_ref - np.median(lvl[sp])), 1) if sp.any() else None,
           "in_breaths_db": round(float(v_ref - np.median(lvl[rest])), 1) if rest.any() else None,
           "payoff_flow_s": notes.get("payoff_flow_s"), "lifts": notes["lifts"], "holes": notes["holes"], "hits": notes["hits"], "accents": notes.get("accents") or [],
           # how much the ride itself moves the level at syllable rate under the words (pumping would show here)
           "ride_wobble_db": round(float(np.std((slow - s1)[sp])), 2) if sp.any() else None,
           "level_p10_p90": [round(float(np.percentile(loc[live] + g[live], 10) - np.median(loc[live] + g[live])), 1),
                             round(float(np.percentile(loc[live] + g[live], 90) - np.median(loc[live] + g[live])), 1)] if live.any() else None,
           "music_share": round(float(live[:int(dur / hop_s)].mean()), 3),
           "carved_frames": rep_mask, "carve_max_db": round(float(-carve.min()), 1), "end_hit_s": plan["end_hit_s"],
           "silent_from_s": math.ceil(end_at / SR * 1000.0) / 1000.0, "drop_at": plan.get("drop_at"), "tempo": plan["tempo"]}
    rep['broadband_gain_db'] = round(broadband_gain,2)
    if held is not None:
        rep["hold"] = held
    return out, rep


def _apply_gains(x, g_db, carve_db, hop):
    """Broadband gain per 10 ms frame, plus an extra dip of 300-3400 Hz, by overlap-add (sqrt-Hann, 2048)."""
    n = len(x)
    t = np.arange(n) / float(hop)
    idx = np.arange(len(g_db))
    gb = (10 ** (np.interp(t, idx, g_db) / 20)).astype(np.float32)
    y = x * gb[:, None]
    if not np.any(carve_db < -0.25):
        return y.astype(np.float32)
    N, H = 2048, 512
    win = np.sqrt(np.hanning(N + 1)[:N]).astype(np.float32)
    hz = np.fft.rfftfreq(N, 1.0 / SR)
    band = ((hz >= 300) & (hz <= 3400)).astype(np.float32)
    edge = np.clip(np.minimum((hz - 200) / 100.0, (4000 - hz) / 600.0), 0, 1).astype(np.float32)   # soft shoulders
    shape = np.maximum(band, edge)
    yp = np.concatenate([np.zeros((N // 2, 2), np.float32), y, np.zeros((2 * N, 2), np.float32)])
    out = np.zeros_like(yp)
    norm = np.zeros(len(yp), np.float32)
    frames = 1 + (len(yp) - N) // H
    cv = np.interp(np.arange(frames) * H / float(hop), idx, carve_db)
    w2 = (win * win)[:, None]
    for k in range(frames):
        a = k * H
        seg = yp[a:a + N]
        if cv[k] > -0.25:
            out[a:a + N] += seg * w2
        else:
            gain = 10 ** (cv[k] * shape / 20)
            for c in (0, 1):
                sp = np.fft.rfft(seg[:, c] * win) * gain
                out[a:a + N, c] += np.fft.irfft(sp, N).astype(np.float32) * win
        norm[a:a + N] += win * win
    res = out[N // 2:N // 2 + n] / np.maximum(norm[N // 2:N // 2 + n], 1e-6)[:, None]
    return res.astype(np.float32)


# ----------------------------------------------------------------- pictures on the beat

def fit_cuts(cuts_f, plan, marks, fps, max_frames=3, word_guard_s=0.3):
    """Picture cuts moved onto the song's beats, only where nobody is speaking: a cut with no word inside
    word_guard_s of it and a beat within max_frames goes to that beat (a downbeat wins a tie). Under the voice a
    cut answers the words and stays. Returns (new cuts, [{f, to, beat_s, down}])."""
    grid = plan.get("grid") or []
    if not grid:
        return list(cuts_f), []
    words = marks.get("words") or []
    gt = np.array([g["t"] for g in grid])
    out, moved, used = [], [], set()
    for f in cuts_f:
        t = f / fps
        if any(w0 - word_guard_s < t < w1 + word_guard_s for w0, w1 in words):
            out.append(f)
            continue
        d = np.abs(gt - t)
        order = np.argsort(d + np.array([0.0 if g["down"] else 0.5 / fps for g in grid]))
        j = int(order[0])
        nf = int(round(gt[j] * fps))
        if abs(nf - f) <= max_frames and nf not in used and nf != f:
            moved.append({"f": int(f), "to": nf, "beat_s": float(gt[j]), "down": bool(grid[j]["down"])})
            out.append(nf)
            used.add(nf)
        else:
            out.append(f)
            used.add(f)
    return out, moved


def sounds_on_the_drop(sounds, plan, fps):
    """The turn's own sounds land with the song's drop when the two are a few frames apart (the tempo could not bend
    far enough to put the drop on the turn): one hit to the ear, not two. Only for the mix with the song. The sound's
    own turn hit (a hit carrying `drop`, the workflow films) is found by its part, not by its frame: when the drop is
    not already on it and sits within SNAP_S of it, the hit and what lands with it move onto the drop."""
    d = plan.get("drop_at")
    if d is None:
        return list(sounds)
    own = [s for s in sounds if s.get("drop") and s.get("start_s") is not None and s.get("event_f") is not None]
    if own:
        tf = int(own[0]["event_f"])
        t = tf / float(fps)
        if abs(d - t) < 0.5 / fps or abs(d - t) > SNAP_S:
            return list(sounds)
        out = []
        for s in sounds:
            if s.get("start_s") is not None and int(s.get("event_f", -99)) == tf:
                s = dict(s, start_s=round(max(0.0, float(s["start_s"]) + (d - t)), 4), event_f=int(round(d * fps)))
            out.append(s)
        return out
    t = plan.get("payoff_s") if plan.get("payoff_s") is not None else plan.get("turn_s")
    if plan.get("drop_on") == "turn":
        t = plan.get("turn_s")
    if t is None or abs(d - t) < 0.5 / fps or abs(d - t) > 0.35:
        return list(sounds)
    tf, out = int(round(t * fps)), []
    for s in sounds:
        if abs(int(s.get("event_f", -99)) - tf) <= 3 and s.get("start_s") is not None:
            s = dict(s, start_s=round(max(0.0, float(s["start_s"]) + (d - t)), 4), event_f=int(round(d * fps)))
        out.append(s)
    return out


def marks_from_l1(l1, index=None, turn_s=None, prog_end_f=None, payoff_s=None):
    """What a compiled film gives the arrangement: its length without the end card, its words on its own clock, its
    picture cuts, the story's turn and payoff (the caller's, else the scene's own `music_marks`, else the payoff the
    sound design chose), and where the last hit lands (the last designed landing after the last word, else a breath
    after it)."""
    from . import sound as SD
    fps = float(l1["fps"])
    end_f = int(prog_end_f or l1["dur_f"])
    words = SD.words_in_programme(l1, index) or []
    words = [(a, b) for a, b in words if a < end_f / fps]
    own = l1.get("music_marks") or {}
    if turn_s is None and own.get("turn_s") is not None:
        turn_s = float(own["turn_s"])
    if payoff_s is None and own.get("payoff_s") is not None:
        payoff_s = float(own["payoff_s"])
    if turn_s is None and payoff_s is None:
        for p in (l1.get("sound_design") or {}).get("placed") or []:
            if p.get("part") == "payoff":
                turn_s = float(p["t"])
                break
    last = max((b for _a, b in words), default=None)
    ev = [e["f"] / fps for e in SD.l1_events(l1)] if l1.get("items") else []
    after = [t for t in ev if last is not None and last - 0.1 <= t <= end_f / fps - 0.7]
    cuts = sorted({round(it["f"][0] / fps, 4) for it in l1.get("items") or [] if it.get("k") == "shot" and 0 < it["f"][0] < end_f})
    placed = sorted({round(float(p["t"]), 3) for p in (l1.get("sound_design") or {}).get("placed") or [] if p.get("t") is not None and 0 < float(p["t"]) < end_f / fps})
    out = {"dur_s": end_f / fps, "words": words, "cuts": cuts, "landings": [round(t, 4) for t in ev if 0 < t < end_f / fps], "accents": placed,
           "turn_s": turn_s, "payoff_s": payoff_s, "end_hit_s": min(after) if after else None, "quiet": []}
    # the sound's own turn hit (screen placement, the workflow films: score.sound_screen): the drop lands with it
    own_turn = sorted(int(s["event_f"]) for s in l1.get("sound") or [] if s.get("drop") and s.get("layer") == "hit"
                      and s.get("event_f") is not None and 0 < int(s["event_f"]) < end_f)
    if own_turn:
        out["drop_landing"] = round(own_turn[0] / fps, 4)
    if own.get("joints"):                 # a narrated story's section joints (valmera-stories): the bed breathes at them (flow)
        out["joints"] = [dict(j) for j in own["joints"] if isinstance(j, dict)]
    return out


def to_ar(x):
    """Stereo or mono 48 kHz -> mono at sound.AR (the rate the sound checks read), by ffmpeg."""
    from . import sound as SD
    x = np.asarray(x, np.float32)
    ch = 2 if x.ndim > 1 else 1
    r = subprocess.run([FF, "-hide_banner", "-nostdin", "-loglevel", "error", "-f", "f32le", "-ar", str(SR), "-ac", str(ch), "-i", "pipe:0",
                        "-ac", "1", "-ar", str(SD.AR), "-f", "f32le", "pipe:1"], input=x.tobytes(), capture_output=True, timeout=600)
    return np.frombuffer(r.stdout, np.float32).copy()


# ----------------------------------------------------------------- one film, with the song and without

class _Plan:
    """What scene.audio.split_audio reads of a plan."""

    def __init__(self, l1, src):
        self.doc = {"spine": l1.get("spine") or [], "sound": l1.get("sound") or []}
        self.fps, self.dur = float(l1["fps"]), int(l1["dur_f"])
        self.assets = {"main": {"abs": src}} if src else {}


def voice_spans(voice, min_s=0.06, join_s=0.08):
    """Where a voice speaks, from its own level: [(t0, t1)] where its 50 ms K level is within 24 dB of its speech
    level. For a film whose words carry no times (a narrated film handed over without its index)."""
    x = np.asarray(voice, np.float32)
    if not len(x) or float(np.abs(x).max()) < 1e-5:
        return []
    k = _k_frames(x if x.ndim > 1 else np.stack([x, x], axis=1), 480)
    p = np.convolve(10 ** (k / 10), np.ones(5) / 5, mode="same")
    lv = 10 * np.log10(p + 1e-12)
    on = lv > np.percentile(lv[lv > lv.max() - 40], 80) - 24.0
    out, i, n = [], 0, len(on)
    while i < n:
        if on[i]:
            j = i
            while j < n and on[j]:
                j += 1
            if out and i * 0.01 - out[-1][1] < join_s:
                out[-1] = (out[-1][0], j * 0.01)
            elif (j - i) * 0.01 >= min_s:
                out.append((i * 0.01, j * 0.01))
            i = j
        else:
            i += 1
    return [(round(a, 3), round(b, 3)) for a, b in out]


def _source(l1, work):
    main = (l1.get("assets") or {}).get("main") or {}
    src = main.get("abs") or main.get("path")
    if src and work and not os.path.isabs(src):
        src = os.path.join(work, src)
    if not (src and os.path.exists(src)):
        src = os.path.join(work, "source.mp4") if work and os.path.exists(os.path.join(work, "source.mp4")) else None
    return src


def voice_spans_of(l1, work):
    """voice_spans of a compiled film's own voice track (the scene engine's split), [] when it has none."""
    from scene import audio as AU
    try:
        voice, _fx, _ = AU.split_audio(_Plan(dict(l1, sound=[]), _source(l1, work)))
        return voice_spans(voice)
    except Exception:  # noqa: BLE001 - a film without a readable voice has no words to ride under
        return []


def duck_spoken_words(music, voice, words):
    """Catch broadband masking on quiet words after the song's phrase and spectral rides."""
    length = min(len(music), len(voice))
    hop = SR // 100
    gain_db = np.zeros(math.ceil(length / hop) + 1)
    syllable_db = np.zeros_like(gain_db)
    if not length or not words:
        return
    # Word stamps can begin in room tone before the first voiced sample.
    # Measure that distinction against the local conversation, so a sustained
    # quieter speaker establishes their own reference.
    energy = np.mean(voice[:length].reshape(length, -1)**2, axis=1)
    cumulative = np.concatenate(([0.], np.cumsum(energy, dtype=np.float64)))
    for start, end in words:
        a, b = max(0, round(start * SR)), min(length, round(end * SR))
        if b <= a:
            continue
        # Detected speech spans can hold several words. Compare overlapping
        # syllables, keeping a short word intact and anchoring the last window
        # at the end of the span so a partial window can't ride on room tone.
        width = min(SR // 10, b - a)
        starts = np.minimum(np.arange(a, b - width + 5*hop, 5*hop), b - width)
        stops = starts + width
        speech = (cumulative[stops] - cumulative[starts]) / width
        left, right = np.maximum(0, starts-SR), np.minimum(length, stops+SR)
        local = (cumulative[right] - cumulative[left]) / (right-left)
        # A window entering room tone should release the bed. Keep the whole
        # word measurement for sustained, quieter replies between loud voices.
        whole = (cumulative[b] - cumulative[a]) / (b-a)
        limits = np.maximum(.003 ** 2, local * 10**(-HOLD["hold_deep_db"]/10))
        sounding = speech > limits
        windows = list(zip(starts[sounding], stops[sounding], speech[sounding]))
        left, right = max(0, a-SR), min(length, b+SR)
        nearby = (cumulative[right] - cumulative[left]) / (right-left)
        if whole > max(.003 ** 2, nearby * 10**(-17/10)):
            windows.append((a, b, whole))
        for lo, hi, power in windows:
            bed = np.mean(music[lo:hi] ** 2)
            if bed > 0:
                # Leave a soft syllable extra room up to the ordinary dialogue
                # target. A level word keeps its existing word-wide margin.
                below_word = max(0., 10 * math.log10(whole / power))
                margin = min(UNDER_VOICE["podcast"], HOLD["hold_floor_db"] + below_word)
                change = 10 * math.log10(power / bed) - margin
                frames = slice(lo // hop, math.ceil(hi / hop) + 1)
                # Short replies share the slow word ride across speakers. In a
                # longer span let the local dip release between syllables.
                local_dip = b - a > .4 * SR and hi - lo < b - a
                ride = syllable_db if local_dip else gain_db
                ride[frames] = np.minimum(ride[frames], change)
    if not (np.any(gain_db < 0) or np.any(syllable_db < 0)):
        return
    # Reach the required depth before a soft voice enters. Adjacent words
    # share a ride and the return takes as long as the level change needs.
    for ride, attack, release in ((gain_db, .04, .03), (syllable_db, .30, .20)):
        for i in range(len(ride)-2, -1, -1):
            ride[i] = min(ride[i], ride[i+1] + attack)
        for i in range(1, len(ride)):
            ride[i] = min(ride[i], ride[i-1] + release)
    gain_db = np.minimum(gain_db, syllable_db)
    curve = 10 ** (np.interp(np.arange(length), np.arange(len(gain_db))*hop, gain_db) / 20)
    music[:length] *= curve.reshape((length,) + (1,) * (music.ndim-1))


def card_phrase(path, start, tempo, frames, bar_s=None, ffmpeg=None):
    """Continue on the source clock. If the edit runs into a rest or EOF,
    move by whole bars to a sounding phrase before applying the brand duck."""
    pcm = _decode(path, SR, 2, ffmpeg)
    if not len(pcm):
        raise ValueError(f"No audio samples in {path}")
    # atempo consumes a little lookahead at the right edge.
    length = math.ceil(frames * tempo) + round(.08 * SR)
    hop = SR // 20
    ends = np.arange(0, len(pcm), hop)
    power = np.add.reduceat(np.einsum("ij,ij->i", pcm, pcm), ends)
    power /= 2 * np.diff(np.r_[ends, len(pcm)])
    # A recording's natural dynamics survive. Only a sustained fade/rest
    # needs another edit, never a gain boost on near-silence.
    track_power = np.percentile(power, 75)
    wanted = round(start * SR)
    if 0 <= wanted and wanted + length <= len(pcm):
        local = power[wanted//hop:math.ceil((wanted+length)/hop)]
        if local.min() > max(1e-10, np.median(local)*.05, track_power*.001):
            direct = _tempo(pcm[wanted:wanted+length], tempo, ffmpeg)
            if len(direct) >= frames:
                return direct[:frames]
    floor = track_power * .05
    alive = power > max(1e-12, floor)
    edges = np.diff(np.r_[False, alive, False].astype(int))
    runs = [(a*hop, min(b*hop, len(pcm))) for a,b in
            zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1))]
    step = max(1, round((bar_s or 2.) * SR))
    candidates = []
    for a,b in runs:
        if b-a < length:
            continue
        offset = min(max(wanted, a), b-length)
        candidates.append((1, abs(offset-wanted), offset))
        k0, k1 = math.ceil((a-wanted)/step), (b-length-wanted)//step
        if k0 <= k1:
            offset = wanted + min(max(0,k0),k1)*step
            candidates.append((0, abs(offset-wanted), offset))
    if candidates:
        offset = min(candidates)[2]
        phrase = pcm[offset:offset+length]
    else:
        # Short cue: loop its longest sounding section with a soft splice.
        a,b = max(runs or [(0,len(pcm))], key=lambda r:r[1]-r[0])
        phrase = pcm[a:b]
    played = _tempo(phrase, tempo, ffmpeg)
    if len(played) >= frames:
        return played[:frames]
    out = np.empty((frames,2), np.float32)
    n = len(played)
    out[:n] = played
    overlap = min(round(.08*SR), n//3)
    while n < frames:
        start = n-overlap
        count = min(len(played), frames-start)
        if overlap:
            ramp = np.linspace(0,1,overlap)[:,None]
            out[start:n] = out[start:n]*(1-ramp) + played[:overlap]*ramp
        out[n:start+count] = played[overlap:count]
        n = start+count
    return out


def podcast_bed(music, voice, marks, song, plan, song_path, report):
    """Set the opening against the programme voice and locate the continuation on the final song edit."""
    if any(start < 1 for start,end in marks.get("words", [])):
        first = min(SR, len(music), len(voice))
        if first:
            bed_rms = float(np.sqrt(np.mean(music[:first]**2)))
            speech_rms = float(np.sqrt(np.mean(voice[:first]**2)))
            if speech_rms > .003 and bed_rms > .0001:
                gain = min(1., speech_rms * 10**(-10/20) / bed_rms, .05 / bed_rms)
                opening_db = 20 * math.log10(gain)
                # On a short cue, reach its existing tail before the final second.
                recovery_s = min(max(1., -opening_db / 3.), max(1., len(music)/SR - 2.))
                ride = np.interp(np.arange(len(music)), [first, first+recovery_s*SR], [opening_db, 0.])
                music *= (10 ** (ride / 20))[:, None]
            report["opening_bed_dbfs"] = round(float(10*np.log10(max(1e-12,np.mean(music[:first]**2)))), 2)
    duck_spoken_words(music, voice, marks.get("words", []))
    report["podcast_bed_version"] = 2
    report["card_bed"] = card_continuation(marks, song, plan, song_path)


def card_continuation(marks, song, plan, song_path):
    """Position on the same licensed recording at the programme/card boundary."""
    tempo = plan["segments"][-1].get("tempo") or plan["tempo"]
    source_end = song["bars"][plan["end_hit_bar"]]["t0"] + (marks["dur_s"] - plan["end_hit_s"]) * tempo
    return dict(path=os.path.abspath(song_path), tempo=tempo, bar_s=song.get("bar_s"), at_s=source_end)


def mix(l1, work, song_path, film=None, out=None, vocals=None, turn_s=None, kind="stories", index=None, prog_end_f=None, payoff_s=None, supplied=True,
        sung=None, open_s=None, turn_drop=False, arc=False, story_arc=None):
    """A compiled film's sound with a song under it: the voice and the designed sounds as the engine mixes them
    (scene.audio), the song arranged and levelled here, one master at -14 LUFS. With `film` and `out` the picture is
    copied and the new sound laid on it. The film without the song is the engine's own render: nothing here touches
    it. Returns the report (the plan, the levels, the master's numbers). story_arc: the story's own arc for a profile
    that holds the bed under the words ({setup, build, payoff, close}: low | mid | high, and close_s), see story_arc()."""
    import json
    import tempfile
    from scene import audio as AU                     # the mix lives on the engine's side; the engine reads no score code
    if index is None and work and os.path.exists(os.path.join(work, "index.json")):
        index = json.load(open(os.path.join(work, "index.json")))
    src = _source(l1, work)
    marks = marks_from_l1(l1, index, turn_s=turn_s, prog_end_f=prog_end_f, payoff_s=payoff_s)
    if open_s is not None:
        marks["open_s"] = float(open_s)               # the chooser's entry point into the song (a drop, a re-entry, its top)
    if turn_drop:
        marks["turn_drop"] = True                     # the caller named the story's turn: a payoff with no room drops there (arrange)
    if arc:
        marks["arc"] = True                           # opt-in (the workflows' story path): the song played by its own arc (arrange)
    if story_arc:
        marks["story_arc"] = {k: story_arc.get(k) for k in ARC_PARTS if story_arc.get(k) in ARC_DB}
        if story_arc.get("close_s") is not None:
            marks["close_s"] = float(story_arc["close_s"])
    voice, _fx0, _ = AU.split_audio(_Plan(dict(l1, sound=[]), src))
    if not marks["words"]:                            # no word times came with the film: its voice says where it speaks
        marks["words"] = [(a, b) for a, b in voice_spans(voice) if a < marks["dur_s"]]
    song = analyse(song_path, vocals=vocals, sung=sung)
    prof = profile_for(kind)
    plan = arrange(song, marks, profile=prof)
    rep = {"song": os.path.basename(song_path), "supplied": bool(supplied), "words": len(marks["words"]),
           "marks": {k: marks.get(k) for k in ("dur_s", "turn_s", "payoff_s", "end_hit_s", "close_s", "story_arc")}, "plan": {k: v for k, v in plan.items() if k != "grid"}}
    hold = bool(prof.get("hold"))                     # the bed is held word by word on the story's arc: no global gain moves it after
    if not plan.get("ok"):
        return rep
    from . import sound as SD
    l1 = dict(l1, sound=sounds_on_the_drop(l1.get("sound") or [], plan, float(l1["fps"])))
    rep["drop_landing"] = marks.get("drop_landing")   # the sound's turn hit the drop was asked to join (screen placement)
    total = len(voice) / float(AU.SR)
    with tempfile.TemporaryDirectory(dir=os.path.dirname(os.path.abspath(out)) if out else None) as tmp:
        lv, _ = AU.level_voice(voice, tmp) if float(np.abs(voice).max()) > 1e-5 else (voice, {})
        music, mrep = track(song_path, song, plan, marks, voice=lv[:int(marks["dur_s"] * SR)], profile=prof)
        if kind == "podcast":
            podcast_bed(music, lv, marks, song, plan, song_path, rep)
        elif kind == "stories":
            rep["card_bed"] = card_continuation(marks, song, plan, song_path)
        # with the bed known, each placed sound is made to be there over the voice and the song together
        if kind == 'podcast':
            from . import audibility
            # Judge attacks against the bed the listener will actually hear.
            # Previously broadband calibration happened only AFTER the hits
            # were balanced, raising their masker without rechecking attacks.
            initial_bed_gain = 0.0
            if not hold:
                music, initial_bed_gain = audibility.calibrate_bed(lv, music, prof['broadband_bed_db'])
            rep['initial_bed_gain_db'] = initial_bed_gain
            # Normalize the existing recorded attacks before the spectral pass
            # can discard a quiet sample. This adds no events or recordings.
            l1['sound'] = audibility.present_hits(l1['sound'], lv, music,
                lambda items: AU.split_audio(_Plan(dict(l1,sound=items),src))[1], alternatives=audibility.recording_alternatives)
        # the voice as each channel plays it: ffmpeg's stereo-to-mono sums a centred voice 3 dB over either channel,
        # and every sound was levelled against the voice per channel (sound.voice_shift) (lane sfx-moments)
        lv_ar = to_ar(lv.mean(axis=1) if np.ndim(lv) == 2 else lv)
        music_ar = to_ar(music)
        l1["sound"] = SD.present_over(l1["sound"], lv_ar, music_ar, float(l1["fps"]))
        if kind == 'podcast':
            l1['sound'] = audibility.present_hits(l1['sound'], lv, music,
                lambda items: AU.split_audio(_Plan(dict(l1,sound=items),src))[1], alternatives=audibility.recording_alternatives)
            # the workflow films: the ceilings are the final level, and each sound's own level fields are read there
            l1['sound'] = SD.screen_restrain(l1['sound'], lv_ar, fps=float(l1["fps"]), bed=music_ar, words=marks["words"])
        rep["lifted_for_bed"] = sum(1 for x in l1["sound"] if x.get("lifted_for_bed_db"))
        rep['final_sounds'] = l1['sound']
        _v, fx, _ = AU.split_audio(_Plan(l1, src))
        if kind == 'podcast':
            # Save the exact post-duck components for verification against the encoded film.
            bed = np.zeros_like(fx)
            bed[:min(len(bed),len(music))] = music[:len(bed)]
            ducks = getattr(fx,'ducks',None)
            if ducks:
                gain, _ = AU.duck_curve(bed,fx,ducks,len(fx))
                bed *= gain[:,None]
            if not hold:
                bed, _ = audibility.calibrate_bed(lv,bed,prof['broadband_bed_db'])
            rep['audibility'] = audibility.measure(lv,fx,bed)
            np.savez_compressed(os.path.join(work,'mix_stems.npz'), voice=audibility.mono(lv).astype(np.float32),
                                sfx=audibility.mono(fx).astype(np.float32),bed=audibility.mono(bed).astype(np.float32))
            fx = np.asarray(fx)+bed
        else:
            fx[:len(music)] += music[:len(fx)]
        m4a = os.path.join(tmp, "song.m4a")
        rep["master"] = AU.master(voice, fx, m4a, tmp, total)
        if kind == 'podcast':
            # Inspect what survived the encoder, then rebalance the existing
            # earned events once, with one further bed correction if needed. Never add effects to meet a count.
            own = np.asarray(fx)-bed
            actual=audibility.verified_mix(lv,own,bed,_decode(m4a,SR,1))
            rep['encoded_balance_before']=actual
            rep['encoded_balance_passes']=[actual]
            for correction in range(2):
                gap=actual['bed_under_voice_db']
                if gap is None or ((hold or 5<=gap<=6) and (correction or actual['T2_per_10s']>=3.6)):
                    break
                a,b,c=actual['gains']
                if actual['fit_residual_db'] >= -10 or min(a,b,c)<=0:
                    break
                # The limiter changes its work after a balance correction, so
                # verify the result again. Only the first pass revoices existing
                # attacks; a second pass can correct the bed's remaining error.
                if not hold:            # a held bed keeps the level its arc and its words gave it
                    bed *= 10**((gap-prof['broadband_bed_db'])/20)
                if correction==0:
                    l1['sound']=audibility.present_hits(l1['sound'],lv,bed*(c/a),
                        lambda items: np.asarray(AU.split_audio(_Plan(dict(l1,sound=items),src))[1])*(b/a),
                        alternatives=audibility.recording_alternatives)
                    # the workflow films: the ceilings stay the final level after this lift too (lane sfx-moments)
                    l1['sound'] = SD.screen_restrain(l1['sound'], lv_ar, fps=float(l1["fps"]), bed=to_ar(bed) if SD.SCR.on() else None,
                                                     words=marks["words"])
                    _v,own,_=AU.split_audio(_Plan(l1,src))
                rep['master']=AU.master(voice,np.asarray(own)+bed,m4a,tmp,total)
                actual=audibility.verified_mix(lv,own,bed,_decode(m4a,SR,1))
                rep['encoded_balance_passes'].append(actual)
            rep['encoded_audibility']=actual
            rep['final_sounds']=l1['sound']
            np.savez_compressed(os.path.join(work,'mix_stems.npz'),voice=audibility.mono(lv).astype(np.float32),
                                sfx=audibility.mono(own).astype(np.float32),bed=audibility.mono(bed).astype(np.float32))
        rep["music"] = mrep
        if film and out:
            AU.mux(film, m4a, out)
            rep["out"] = out
    return rep


def mix_film(film, song_path, marks, out=None, kind="stories", vocals=None, sung=None, supplied=True, music=None):
    """A finished film's own sound with a song under it, for a film whose scene cannot give its voice apart (its
    sound was written as one file: a narrated long form, a screen film). The film's sound is its voice here: where
    it speaks is read from its level when the marks carry no words, the song is arranged and ridden against it,
    and the two are mastered to -14 LUFS on a copy of the picture. The film itself is never touched. `music`: the
    music already on the film's clock (a long film scored chapter by chapter, each with its own track): (stereo
    48 kHz, report); song_path is then only its name."""
    import tempfile
    from scene import audio as AU
    prog = _decode(film, SR, 2)
    total = len(prog) / float(SR)
    mrep = None
    marks = dict(marks)
    marks.setdefault("dur_s", total)
    n_prog = int(float(marks["dur_s"]) * SR)
    if not marks.get("words"):
        marks["words"] = [(a, b) for a, b in voice_spans(prog[:n_prog]) if a < marks["dur_s"]]
    prof = profile_for(kind)
    if music is not None:
        music, mrep = music
        plan = {"ok": True, "chapters": mrep.get("chapters")}
    else:
        song = analyse(song_path, vocals=vocals, sung=sung)
        plan = arrange(song, marks, profile=prof)
    rep = {"song": os.path.basename(str(song_path)), "supplied": bool(supplied), "words": len(marks["words"]), "from": "the film's own sound",
           "marks": {k: marks.get(k) for k in ("dur_s", "turn_s", "payoff_s", "end_hit_s", "close_s", "story_arc")}, "plan": {k: v for k, v in plan.items() if k != "grid"}}
    if not plan.get("ok"):
        return rep
    with tempfile.TemporaryDirectory(dir=os.path.dirname(os.path.abspath(out)) if out else None) as tmp:
        if mrep is None or "chapters" not in mrep:
            music, mrep = track(song_path, song, plan, marks, voice=prog[:n_prog], profile=prof)
            if kind == "podcast":
                podcast_bed(music,prog,marks,song,plan,song_path,rep)
            elif kind == "stories":
                rep["card_bed"] = card_continuation(marks, song, plan, song_path)
        if not marks["words"]:                              # nobody speaks: the music is set against the film's own level, not a voice's
            i, _tp, _ = AU.loudness_of(prog, tmp) if float(np.abs(prog).max()) > 1e-5 else (None, None, None)
            if i is not None and i > -70:
                music = music * np.float32(10 ** ((i - VOICE_LUFS) / 20))
        mixd = prog.copy()
        mixd[:len(music)] += music[:len(mixd)]
        m4a = os.path.join(tmp, "song.m4a")
        rep["master"] = AU.master(None, mixd, m4a, tmp, total)
        rep["music"] = mrep
        if out:
            AU.mux(film, m4a, out)
            rep["out"] = out
    return rep


def main(argv=None):
    """python -m score.music mix --l1 l1.json --work DIR --song SONG --film film.mp4 --out film_song.mp4 [--turn S] [--payoff S] [--kind stories]"""
    import argparse
    import json
    ap = argparse.ArgumentParser(prog="score.music")
    ap.add_argument("cmd", choices=["mix", "analyse"])
    ap.add_argument("--l1")
    ap.add_argument("--work")
    ap.add_argument("--song", required=True)
    ap.add_argument("--vocals")
    ap.add_argument("--film")
    ap.add_argument("--out")
    ap.add_argument("--turn", type=float)
    ap.add_argument("--payoff", type=float)
    ap.add_argument("--chosen", action="store_true", help="the song is Valmera's pick from the library, not a supplied one")
    ap.add_argument("--instrumental", action="store_true", help="the song is known to carry no voice")
    ap.add_argument("--report")
    ap.add_argument("--kind", default="stories")
    a = ap.parse_args(argv)
    if a.cmd == "analyse":
        s = analyse(a.song, vocals=a.vocals)
        print(json.dumps({k: v for k, v in s.items() if k != "sim"}, default=float)[:4000])
        return
    l1 = json.load(open(a.l1))
    rep = mix(l1, a.work or os.path.dirname(os.path.abspath(a.l1)), a.song, film=a.film, out=a.out, vocals=a.vocals, turn_s=a.turn, kind=a.kind,
              payoff_s=a.payoff, supplied=not a.chosen, sung=False if a.instrumental else None)
    if a.report:
        json.dump(rep, open(a.report, "w"), default=float)
    print(json.dumps(rep, default=float)[:3000])


if __name__ == "__main__":
    main()
