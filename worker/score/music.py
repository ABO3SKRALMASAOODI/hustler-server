"""The film's music (sound engine lane, 13 Nov): a song somebody else chose, cut to the film.

Valmera never picks a song. This engine takes one the owner or the customer supplied and does an editor's work
on it: read it (rhythm.analyze: beats, downbeats, sections, drops, gaps; here: one bar grid, each bar's level,
low end and voice, how alike bars are), re-cut it at bar lines so its drop lands on the film's turn and its last
hit on the film's last landing (arrange), keep it under the voice and let it up in the voice's breaths, ride it
into the turn, end on the hit and stop before the brand's end card (track), and move the picture's free cuts onto
its beats where nobody is speaking (fit_cuts). The film without the song is always written beside it.

    song = analyse(path)                                  the song's map (a few seconds per minute of song)
    plan = arrange(song, marks)                           which bars play where: [{song: [a, b], film: [a, b]}]
    music, report = track(path, song, plan, marks, voice) stereo 48 kHz on the film's clock, levels solved
    cuts, moved = fit_cuts(cuts, plan, marks, fps)        picture cuts on beats, only where no word is near
    report = mix(l1, work, song_path, film=, out=)        a compiled film with the song under it (the clean film is the engine's)
    marks_from_l1(l1, index, turn_s=None)                 what a compiled film gives the arrangement

marks: {dur_s (the programme, without the end card), words [(t0, t1)], turn_s (the story's turn or payoff landing,
or None), end_hit_s (where the last hit lands, or None: after the last word), quiet [(t0, t1)] (held low)}.

Numbers are the references' (lanes/S study on Demucs stems, 13 Nov; PROFILE below). numpy, ffmpeg and the rhythm
engine only. Nothing here reads a title, a tag or a request.
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
VOICED = 0.2                       # a bar whose voice holds this share of its 250-4000 Hz is sung in

# What the references do with a bed, measured on stems (lanes/S study, 132 films, 13 Nov; score/sound_profiles.GROUPS,
# MUSIC, ENDS). K-weighted, speech frames. The bed there includes the placed sounds, so each number here sits a step
# quieter than its median: stories 5.8 dB under the voice (4.0-8.8), marketing 7.8, podcast clips 12.0, long form 14.8;
# the bed comes up 1.3-2.6 dB in a breath (4.4 in the owner's picks); the voice's return steps it down 2 dB in stories.
PROFILE = {
    "under_voice_db": 7.5,         # the bed sits this far under the voice while it speaks (see UNDER_VOICE)
    "lift_db": 2.5,                # and comes up this much in a breath of 0.45 s or more
    "open_db": 3.0,                # under the voice's level when nobody has spoken yet, or after the last word
    "quiet_max_db": 4.0,           # a quiet part of the song plays at most this far under the song's own level
    "ride_db": 2.5,                # the swell over the last two beats into the turn
    "after_turn_db": 1.5,          # the bed stays this much higher after its drop
    "duck_attack_s": 0.12, "duck_release_s": 0.30,
    "gap_s": 0.45,
    "mask_db": 6.0,                # the bed's speech band stays at least this far under the voice's syllables
    "carve_max_db": 6.0,           # by a dip of the speech band alone, never more than this
    "end_tail_beats": 1.0,         # the last hit rings one beat, then nothing (references: the last hit 0.9-1.0 s before the end)
    "end_gap_s": 0.08,             # silence before the end card
}
UNDER_VOICE = {"stories": 7.5, "marketing": 8.5, "podcast": 12.0, "longform": 15.0}    # under_voice_db by the kind of film


def profile_for(kind):
    """The PROFILE for a kind of film (stories | marketing | podcast | longform)."""
    return dict(PROFILE, under_voice_db=UNDER_VOICE.get(kind, PROFILE["under_voice_db"]))


# ----------------------------------------------------------------- the song's map

def _decode(path, rate, ch):
    r = subprocess.run([FF, "-hide_banner", "-nostdin", "-loglevel", "error", "-i", path, "-map", "0:a:0", "-vn", "-ac", str(ch),
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


def analyse(path, rmap=None, vocals=None):
    """The song's map: {dur_s, bpm, beat_s, bar_s, steady, bars: [{i, t0, t1, k, low, voc, e, sec, drop, hit}],
    sim (bars x bars), drops, gaps, sections}. bars are whole bars on one grid. voc is how much of a bar is a
    lead voice: the share of its 2-8 kHz (where consonants live, and where a second voice fights a narrator) that
    its most alike bars do not explain, read on the song's vocal stem when there is one (vocals: a file from the
    separator, stems.py), else on the song itself. A looped sample explains itself, sung or not, and a filtered
    one has nothing up there; a verse does."""
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
    return {"dur_s": round(dur, 3), "bpm": rmap.get("bpm"), "beat_s": round(bar_s / 4.0, 5), "bar_s": round(bar_s, 5),
            "steady": bool(steady), "bars": bars, "sim": np.round(sim, 3).tolist(), "drops": drops,
            "gaps": rmap.get("gaps") or [], "sections": secs, "confidence": rmap.get("confidence")}


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


def arrange(song, marks, profile=None):
    """Which bars of the song play where in the film. Counted back from the last hit in whole bars; the turn, when
    the film has one, takes a drop of the song on its frame, with the tempo eased up to 4% so both land. Bars the
    song's voice sings in are kept from under the film's voice while the song has others; a jump goes to a bar that
    sounds like the one that would have come next, or into a drop."""
    bars = song.get("bars") or []
    if len(bars) < 4 or not song.get("bar_s"):
        return {"ok": False, "why": "no bar grid in this song"}
    P = dict(PROFILE, **(profile or {}))
    B = float(song["bar_s"])
    beat = B / 4.0
    dur = float(marks["dur_s"])
    words = sorted(marks.get("words") or [])
    t_end = end_hit_time(marks)
    tail = P["end_tail_beats"] * beat
    if t_end + tail > dur - P["end_gap_s"]:
        t_end = dur - P["end_gap_s"] - tail
    turn = marks.get("turn_s")
    drop_bars = [b["i"] for b in bars if b["drop"]]
    if not drop_bars:                                  # no drop: the bar where the song steps up the most
        steps = [(bars[i]["k"] - bars[i - 1]["k"], i) for i in range(1, len(bars)) if bars[i].get("sec_start")]
        if steps and max(steps)[0] >= 3.0:
            drop_bars = [max(steps)[1]]
    n_post, Bs = None, B
    if turn is not None and drop_bars and 2.0 * B <= t_end - turn:
        span = t_end - turn
        n_post = max(MIN_SEG_BARS, int(round(span / B)))
        Bs = span / n_post
        if abs(Bs / B - 1.0) > STRETCH_MAX:            # the tempo would bend too far: keep it and take the nearest bar line
            Bs = B * (1.0 + math.copysign(STRETCH_MAX, Bs / B - 1.0))
    elif turn is not None and not drop_bars:
        turn = None
    N = int(math.ceil(t_end / Bs - 1e-6))
    if N < 1:
        return {"ok": False, "why": "the film is shorter than a bar of this song"}
    kd = N - n_post if n_post is not None else None
    if kd is not None and kd < 0:
        kd, n_post = None, None
    nb = len(bars)
    sim = np.asarray(song["sim"], np.float64)
    voc = np.array([b["voc"] for b in bars])
    en = np.array([b["e"] for b in bars])
    hit = np.array([b["hit"] for b in bars])
    vfree = voc < VOICED
    has_free = vfree.sum() >= 4
    quiet = marks.get("quiet") or []
    # each film slot: spoken share, the energy the story wants there
    slot = []
    for kk in range(N):
        a, b = t_end - (N - kk) * Bs, t_end - (N - kk - 1) * Bs
        sp = _spoken_share(words, max(a, 0.0), b)
        if kd is None:
            tgt = 0.35 + 0.35 * kk / max(N - 1, 1)
        elif kk < kd:
            tgt = 0.1 + 0.2 * kk / max(kd, 1)
        else:
            tgt = 0.85
        if any(min(b, q1) - max(a, q0) > 0.5 * Bs for q0, q1 in quiet):
            tgt = min(tgt, 0.2)
        slot.append((sp, tgt))
    INF = 1e9
    cost = np.full((N, nb), INF)
    back = np.zeros((N, nb), np.int32)
    run = np.ones((N, nb), np.int32)                   # bars played in a row ending here

    def slot_cost(kk):
        sp, tgt = slot[kk]
        c = 1.2 * np.abs(en - tgt) + (3.0 * voc * sp if has_free else 0.0)
        if kd is not None and kk == kd:
            m = np.full(nb, INF)
            m[drop_bars] = 0.0
            c = c + m
        if kd is not None and kk < kd:                 # a drop before the turn spends it, and so does the drop's own level
            c = c + np.where([b["drop"] for b in bars], 2.5, 0.0) + 2.0 * np.maximum(en - 0.5, 0.0)
        return c
    jump = 1.3 - 0.9 * np.clip(sim, 0, 1)              # jump[i_next_natural, j]: how unlike j is to what would have come
    cost[0] = slot_cost(0)
    for kk in range(1, N):
        sc = slot_cost(kk)
        for j in range(nb):
            if sc[j] >= INF:
                continue
            best, arg, rr = INF, -1, 1
            for i in range(nb):
                c0 = cost[kk - 1, i]
                if c0 >= INF:
                    continue
                if j == i + 1:
                    tr = 0.0
                else:
                    nat = i + 1
                    if nat >= nb:                       # the song ran out: any restart is a jump
                        tr = 1.0 if bars[j].get("sec_start") or bars[j]["drop"] else 1.6
                    else:
                        tr = float(jump[nat, j])
                        if bars[j]["drop"] and kd is not None and kk == kd:
                            tr = min(tr, 0.45)          # the drop covers its own join
                    if run[kk - 1, i] < MIN_SEG_BARS:
                        tr += 2.0
                    if bars[i].get("windup") and not bars[j]["drop"]:
                        tr += 2.0                       # a wind-up that lands on nothing
                c = c0 + tr
                if c < best:
                    best, arg = c, i
                    rr = run[kk - 1, i] + 1 if j == i + 1 else 1
            cost[kk, j] = best + sc[j]
            back[kk, j] = arg
            run[kk, j] = rr
    # the end: the bar after the last one gives the hit; a strong downbeat or the song's own end is a button
    fin = cost[N - 1].copy()
    for j in range(nb):
        if fin[j] >= INF:
            continue
        nxt = j + 1
        if nxt < nb:
            fin[j] -= 0.15 * min(max(hit[nxt], 0.0), 12.0) / 12.0 + (0.25 if bars[nxt].get("sec_start") else 0.0)
        else:
            fin[j] -= 0.3
        if run[N - 1, j] < MIN_SEG_BARS and N > 1:
            fin[j] += 1.0
    j = int(np.argmin(fin))
    if fin[j] >= INF / 2:
        return {"ok": False, "why": "no arrangement lands the drop on the turn"}
    seq = [j]
    for kk in range(N - 1, 0, -1):
        j = int(back[kk, j])
        seq.append(j)
    seq.reverse()
    segs = []
    for kk, bi in enumerate(seq):
        fa, fb = t_end - (N - kk) * Bs, t_end - (N - kk - 1) * Bs
        if segs and segs[-1]["bars"][1] == bi - 1:
            segs[-1]["bars"][1] = bi
            segs[-1]["song"][1] = bars[bi]["t1"]
            segs[-1]["film"][1] = round(fb, 4)
        else:
            segs.append({"bars": [bi, bi], "song": [bars[bi]["t0"], bars[bi]["t1"]], "film": [round(fa, 4), round(fb, 4)]})
    last = seq[-1]
    hit_bar = last + 1 if last + 1 < nb else (drop_bars[0] if drop_bars else 0)
    drop_at = round(t_end - n_post * Bs, 4) if kd is not None else None
    grid = []
    for s in segs:                                     # the film's beat grid, from the bars that play
        nbar = s["bars"][1] - s["bars"][0] + 1
        for q in range(nbar * 4):
            t = s["film"][0] + q * Bs / 4.0
            if -1e-6 <= t <= dur:
                grid.append({"t": round(t, 4), "down": q % 4 == 0, "bar": s["bars"][0] + q // 4})
    return {"ok": True, "segments": segs, "tempo": round(B / Bs, 5), "bar_s": round(Bs, 5), "beat_s": round(Bs / 4.0, 5),
            "end_hit_s": round(t_end, 4), "end_hit_bar": int(hit_bar), "drop_at": drop_at,
            "turn_s": turn, "turn_miss_s": round(drop_at - turn, 3) if drop_at is not None and turn is not None else None,
            "slots": N, "jumps": len(segs) - 1, "grid": grid,
            "voiced_under_voice": round(float(np.mean([voc[b] >= VOICED and slot[k][0] > 0.3 for k, b in enumerate(seq)])), 3),
            "cost": round(float(fin[seq[-1]]), 3)}


# ----------------------------------------------------------------- the track

def _tempo(x, factor):
    """x (n, 2) played `factor` times faster, pitch kept (ffmpeg atempo)."""
    if abs(factor - 1.0) < 0.002 or not len(x):
        return x
    r = subprocess.run([FF, "-hide_banner", "-nostdin", "-loglevel", "error", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "pipe:0",
                        "-af", f"atempo={factor:.6f}", "-f", "f32le", "-ar", str(SR), "-ac", "2", "pipe:1"],
                       input=np.asarray(x, np.float32).tobytes(), capture_output=True, timeout=900)
    y = np.frombuffer(r.stdout, np.float32).reshape(-1, 2).copy()
    return y if len(y) else x


def _lay(song_pcm, song, plan, dur):
    """The arranged song on the film's clock, before any level: (music, hit_at_sample)."""
    n = int(round(dur * SR))
    out = np.zeros((n, 2), np.float32)
    f = float(plan["tempo"])
    pre = int(JOIN_S * SR)
    xf = int(XFADE_S * SR)
    bars = song["bars"]
    pieces = list(plan["segments"])
    hb = bars[plan["end_hit_bar"]]
    beat_song = (hb["t1"] - hb["t0"]) / 4.0
    pieces = pieces + [{"song": [hb["t0"], hb["t0"] + beat_song * PROFILE["end_tail_beats"] + 0.05], "film": [plan["end_hit_s"], None], "hit": True}]
    for s in pieces:
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
        if s.get("hit"):                                # the last hit: its attack whole, then gone inside its beat
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


def track(path, song, plan, marks, voice=None, profile=None, song_pcm=None):
    """The music on the film's clock with its levels solved: (stereo float32 at 48 kHz, report). voice: the film's
    own voice at 48 kHz (mono or stereo) as the mix will carry it, for the masking check; None skips the check.

    Levels (K-weighted): under the voice by under_voice_db while a word is spoken, up by lift_db in a breath of
    gap_s or more (down again before the next word starts), at open_db before the first word and after the last,
    a swell of ride_db over the last two beats into the turn and after_turn_db more after it, then the speech band
    alone dipped where it would still sit within mask_db of the voice's syllables."""
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
    # breaths shorter than gap_s are part of the sentence
    gap = int(P["gap_s"] / hop_s)
    i = 0
    talk = spoken.copy()
    while i < nf:
        if not talk[i]:
            j = i
            while j < nf and not talk[j]:
                j += 1
            if i > 0 and j < nf and j - i < gap:
                talk[i:j] = True
            i = j
        else:
            i += 1
    first = int(words[0][0] / hop_s) if words else nf
    last = int(math.ceil(words[-1][1] / hop_s)) if words else 0
    # the song's own loudness where it plays, so "under the voice" means the same on any song
    mk = _k_frames(music, hop)[:nf]
    mk = np.pad(mk, (0, max(0, nf - len(mk))), constant_values=-120.0)
    live = mk > -60
    # the song's level is its loud part's (the 90th percentile of its 2 s levels): that part sits under_voice_db under
    # the voice, and a quieter part (a filtered intro) keeps its place under it, inside quiet_max_db
    w = int(2.0 / hop_s)
    pw = 10 ** (np.where(live, mk, -120.0) / 10)
    loc = 10 * np.log10(np.convolve(pw, np.ones(w) / w, mode="same") + 1e-12) if nf > w else mk
    song_lv = float(np.percentile(loc[live], 90)) if live.any() else -30.0
    base = VOICE_LUFS - P["under_voice_db"] - song_lv
    g = np.full(nf, base)
    g += np.clip((song_lv - P["quiet_max_db"]) - loc, 0.0, 8.0) * live
    g[~talk] += P["lift_db"]
    g[:first] = VOICE_LUFS - P["open_db"] - song_lv
    g[last:] = VOICE_LUFS - P["open_db"] - song_lv
    # look ahead: the bed is down before the word starts
    la = int(P["duck_attack_s"] / hop_s)
    gl = g.copy()
    for k in range(1, la + 1):
        gl[:-k] = np.minimum(gl[:-k], g[k:])
    g = _smooth(gl, hop_s, P["duck_attack_s"], P["duck_release_s"])
    beat = float(plan["beat_s"])
    if plan.get("drop_at") is not None:
        d = plan["drop_at"]
        t = np.arange(nf) * hop_s
        up = np.clip((t - (d - 2 * beat)) / (2 * beat), 0, 1) * (t < d)
        hold = (t >= d) & (t < d + beat)
        ease = np.clip(1 - (t - (d + beat)) / (4 * beat), 0, 1) * (t >= d + beat)
        g += P["ride_db"] * (up + hold + ease)
        g[t >= d] += P["after_turn_db"]
    # the entry and the end: in from nothing over a quarter second when it starts inside a bar, the hit whole
    t = np.arange(nf) * hop_s
    starts_on_bar = abs(plan["segments"][0]["film"][0]) < 0.02
    if not starts_on_bar:
        g = np.where(t < 0.25, g - 30.0 * (1 - np.clip(t / 0.25, 0, 1)), g)
    hit_f = int(hit_at / hop)
    g[hit_f:] = np.maximum(g[hit_f:], VOICE_LUFS - P["open_db"] - song_lv)
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
    end_at = min(n, hit_at + int((P["end_tail_beats"] * beat + 0.02) * SR))
    out[end_at:] = 0.0
    spoken_f = spoken[:len(g)]
    rep = {"song_k": round(song_lv, 1), "base_db": round(float(base), 1),
           "under_voice_db": round(float(VOICE_LUFS - (np.median((mk + g)[spoken_f & live[:len(g)]]) if (spoken_f & live[:len(g)]).any() else VOICE_LUFS)), 1),
           "in_breaths_db": round(float(VOICE_LUFS - np.median((mk + g)[(~talk) & live])), 1) if ((~talk) & live).any() else None,
           "carved_frames": rep_mask, "carve_max_db": round(float(-carve.min()), 1), "end_hit_s": plan["end_hit_s"],
           "silent_from_s": math.ceil(end_at / SR * 1000.0) / 1000.0, "drop_at": plan.get("drop_at"), "tempo": plan["tempo"]}
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
    far enough to put the drop on the turn): one hit to the ear, not two. Only for the mix with the song."""
    d, t = plan.get("drop_at"), plan.get("turn_s")
    if d is None or t is None or abs(d - t) < 0.5 / fps or abs(d - t) > 0.35:
        return list(sounds)
    tf, out = int(round(t * fps)), []
    for s in sounds:
        if abs(int(s.get("event_f", -99)) - tf) <= 3 and s.get("start_s") is not None:
            s = dict(s, start_s=round(max(0.0, float(s["start_s"]) + (d - t)), 4), event_f=int(round(d * fps)))
        out.append(s)
    return out


def marks_from_l1(l1, index=None, turn_s=None, prog_end_f=None):
    """What a compiled film gives the arrangement: its length without the end card, its words on its own clock,
    its turn (the payoff the sound design chose when nobody names one), and where the last hit lands (the last
    designed landing after the last word, else a breath after it)."""
    from . import sound as SD
    fps = float(l1["fps"])
    end_f = int(prog_end_f or l1["dur_f"])
    words = SD.words_in_programme(l1, index) or []
    words = [(a, b) for a, b in words if a < end_f / fps]
    if turn_s is None:
        for p in (l1.get("sound_design") or {}).get("placed") or []:
            if p.get("part") == "payoff":
                turn_s = float(p["t"])
    last = max((b for _a, b in words), default=None)
    ev = [e["f"] / fps for e in SD.l1_events(l1)] if l1.get("items") else []
    after = [t for t in ev if last is not None and last - 0.1 <= t <= end_f / fps - 0.7]
    return {"dur_s": end_f / fps, "words": words, "turn_s": turn_s, "end_hit_s": min(after) if after else None, "quiet": []}


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


def mix(l1, work, song_path, film=None, out=None, vocals=None, turn_s=None, kind="stories", index=None, prog_end_f=None):
    """A compiled film's sound with the supplied song under it: the voice and the designed sounds as the engine
    mixes them (scene.audio), the song arranged and levelled here, one master at -14 LUFS. With `film` and `out`
    the picture is copied and the new sound laid on it. The film without the song is the engine's own render:
    nothing here touches it. Returns the report (the plan, the levels, the master's numbers)."""
    import json
    import tempfile
    from scene import audio as AU                     # the mix lives on the engine's side; the engine reads no score code
    if index is None and work and os.path.exists(os.path.join(work, "index.json")):
        index = json.load(open(os.path.join(work, "index.json")))
    main = (l1.get("assets") or {}).get("main") or {}
    src = main.get("abs") or main.get("path")
    if src and work and not os.path.isabs(src):
        src = os.path.join(work, src)
    if not (src and os.path.exists(src)):
        src = os.path.join(work, "source.mp4") if work and os.path.exists(os.path.join(work, "source.mp4")) else None
    marks = marks_from_l1(l1, index, turn_s=turn_s, prog_end_f=prog_end_f)
    song = analyse(song_path, vocals=vocals)
    plan = arrange(song, marks)
    rep = {"song": os.path.basename(song_path), "supplied": True, "marks": {k: marks[k] for k in ("dur_s", "turn_s", "end_hit_s")},
           "plan": {k: v for k, v in plan.items() if k != "grid"}}
    if not plan.get("ok"):
        return rep
    from . import sound as SD
    l1 = dict(l1, sound=sounds_on_the_drop(l1.get("sound") or [], plan, float(l1["fps"])))
    voice, _fx0, _ = AU.split_audio(_Plan(dict(l1, sound=[]), src))
    total = len(voice) / float(AU.SR)
    with tempfile.TemporaryDirectory(dir=os.path.dirname(os.path.abspath(out)) if out else None) as tmp:
        lv, _ = AU.level_voice(voice, tmp) if float(np.abs(voice).max()) > 1e-5 else (voice, {})
        music, mrep = track(song_path, song, plan, marks, voice=lv[:int(marks["dur_s"] * SR)], profile=profile_for(kind))
        # with the bed known, each placed sound is made to be there over the voice and the song together
        l1["sound"] = SD.present_over(l1["sound"], to_ar(lv), to_ar(music), float(l1["fps"]))
        rep["lifted_for_bed"] = sum(1 for x in l1["sound"] if x.get("lifted_for_bed_db"))
        _v, fx, _ = AU.split_audio(_Plan(l1, src))
        fx[:len(music)] += music[:len(fx)]
        m4a = os.path.join(tmp, "song.m4a")
        rep["master"] = AU.master(voice, fx, m4a, tmp, total)
        rep["music"] = mrep
        if film and out:
            AU.mux(film, m4a, out)
            rep["out"] = out
    return rep


def main(argv=None):
    """python -m score.music mix --l1 l1.json --work DIR --song SONG --film film.mp4 --out film_song.mp4 [--turn S] [--kind stories]"""
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
    ap.add_argument("--kind", default="stories")
    a = ap.parse_args(argv)
    if a.cmd == "analyse":
        s = analyse(a.song, vocals=a.vocals)
        print(json.dumps({k: v for k, v in s.items() if k != "sim"}, default=float)[:4000])
        return
    l1 = json.load(open(a.l1))
    rep = mix(l1, a.work or os.path.dirname(os.path.abspath(a.l1)), a.song, film=a.film, out=a.out, vocals=a.vocals, turn_s=a.turn, kind=a.kind)
    print(json.dumps(rep, default=float)[:3000])


if __name__ == "__main__":
    main()
