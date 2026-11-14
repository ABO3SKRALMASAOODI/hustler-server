#!/usr/bin/env python3
"""The story narrator: Phonon 31M Easton (the owner's choice, _shell/narrate), read the way the metromedia narrators read.

    $NARRATE_PY voice.py render narration_script.json --out DIR [--band 03]
    $NARRATE_PY voice.py level-voices DIR          after narrate.py splice: each real voice at the narrator's loudness

The narrate tool reads a script line by line and finishes it (pauses, mastering, word times). The independent voice check
of 14 Nov (week-1112/lanes/RATINGS/2026-11-13-VO2.json) measured Easton read that way against ten metromedia narrators:
too many and too long pauses (joins 0.40 s against 0.18-0.26), a narrow pitch range with low restarts (film range 11.3-12.3
st against 13.8-15.3, a line starting 1.2-3.4 st over the last line's end against 5.4), flat stress (focus word +3.0 dB
against +4.25) and an over-levelled read (loudness range 1.5-2.4 LU against 3.3). This file closes those gaps on the same
engine, using the tool's own parts:

  whole sentences   each generation is one to three whole sentences of the script (never a sentence cut between two
                    generations), so the model's own prosody joins them and there are a third as many joins
  takes             six takes a generation: three at the runtime's own temperature as the owner heard it, three a little
                    freer; every word heard first, then the tool's prosody score plus the line's pitch range and how high
                    it restarts over the last kept line's end (signal only)
  joins             0.22 s between generations, 0.40 s where the story turns (the writer's beats), room tone in every pause
  focus words       the writer's one or two stressed words per line ridden to +4.3 dB over their line, measured per word
  loudness range    each line set by its beat and energy (the hook and the payoff forward, the low point back), scaled until
                    the read measures 3.3 LU (EBU R128), then -14 LUFS and under -1 dBTP

Every word keeps its time and its line (`seg` in words.json is the film script's segment). No listening model judges.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
NARR = HERE.parent / "_shell" / "narrate"
sys.path.insert(0, str(NARR))

JOIN_S = 0.22             # the references' line join: 0.25 [0.18-0.26]
BEAT_S = 0.40             # a pause that stays a pause: where the story turns
TURNS = ("turn", "low", "payoff")
MAX_WORDS = 34            # a generation: one to three whole sentences
TAKES = 6
FREE_TEMP = 0.55          # takes 3..5: a little freer than the runtime's own 0.3
FOCUS_DB = 4.3            # the stressed word over its line (references 4.25 [3.5-4.9])
LRA_AIM = (3.0, 3.6)      # LU (references 3.33 [2.57-4.32])
ROOM_DB = -46.0           # room tone under the speech level (instead of digital silence)
BEAT_DB = {"hook": 1.0, "setup": 0.0, "turn": 0.6, "low": -2.4, "payoff": 1.4, "close": -0.8}
ENERGY_DB = {"high": 0.8, "mid": 0.0, "low": -1.0}


def norm(w):
    return "".join(c for c in str(w).lower() if c.isalnum() or c == "'")


def groups_of(segments):
    """[[segment indexes]] read as one generation each: whole sentences only, a new turn starts a new one."""
    out, cur = [], []
    for i, s in enumerate(segments):
        if cur:
            prev = segments[cur[-1]]
            n = sum(len(segments[j]["text"].split()) for j in cur)
            ends = prev["text"].rstrip().endswith((".", "?", "!"))
            turn = s.get("beat") in TURNS and s.get("beat") != prev.get("beat")
            if (ends and (turn or n + len(s["text"].split()) > MAX_WORDS or len(cur) >= 3)) or n > MAX_WORDS + 16:
                out.append(cur)
                cur = []
        cur.append(i)
    if cur:
        out.append(cur)
    return out


def group_script(script, groups):
    segs = script["segments"]
    out = []
    for k, g in enumerate(groups):
        ss = [segs[i] for i in g]
        nxt = segs[groups[k + 1][0]] if k + 1 < len(groups) else None
        turn = bool(nxt and nxt.get("beat") in TURNS and nxt.get("beat") != ss[-1].get("beat"))
        emph = []
        for s in ss:
            emph += [w for w in (s.get("emphasis") or [])[:2] if w not in emph]
        seg = dict(text=" ".join(s["text"] for s in ss), emphasis=emph, pause_after=0.0 if nxt is None else (BEAT_S if turn else JOIN_S),
                   beat=ss[0].get("beat"), members=g)
        for key in ("pace", "melody", "energy", "direction"):
            if ss[0].get(key):
                seg[key] = ss[0][key]
        if len(ss) > 1 and ss[-1].get("melody"):
            seg["melody"] = ss[-1]["melody"]
        out.append(seg)
    return dict(script, segments=out)


# ----------------------------------------------------------------- the engine, with this read's takes and joins

class Read:
    def __init__(self, N, LN, PF, prosody):
        self.N, self.LN, self.PF, self.prosody = N, LN, PF, prosody
        self.last_end = None          # semitones of the last kept generation's end, against the read's running median
        self.f0s = []
        self.picks = []

    def generate_phonon(self, job, out, log, seed0=7):
        """The tool's own generator, with a freer temperature on the second half of the takes."""
        import time
        import soundfile as sf
        LN = self.LN
        tts = LN.phonon_tts()
        t0, n = time.time(), 0
        cp = out / "conditions.json"
        cond = json.loads(cp.read_text()) if cp.exists() else {}
        base = int(job.get("takes") or LN.TAKES)
        for ln in job["lines"]:
            i = int(ln["id"][1:])
            for k in range(int(ln.get("takes") or base)):
                f = out / f"{ln['id']}_t{k}.wav"
                if f.exists():
                    continue
                dd = LN.retake_dd(k, base)
                tts.set_conditions({"duration_delta": dd} if dd else {})
                temp = FREE_TEMP if (k % base) >= base // 2 else None
                kw = dict(voice=job["voice"], seed=seed0 + 101 * k + 7 * i)
                if temp:
                    kw["temperature"] = temp
                x = tts.synth(ln["text"], **kw)
                sf.write(f, np.asarray(x, np.float32), tts.sample_rate, subtype="FLOAT")
                cond[f.name] = {"duration_delta": dd, "temperature": temp or 0.3}
                n += 1
        tts.set_conditions({})
        cp.write_text(json.dumps(cond))
        log(f"phonon {job['voice']}: {n} takes in {time.time() - t0:.1f}s (stories read)")

    def ends(self, t):
        """(start, end) pitch of a take in Hz: medians of its first and last 25 voiced frames (10 ms)."""
        if "_ends" not in t:
            f0 = self.prosody.pitch_track(t["_x"], t["_sr"])
            v = f0[np.isfinite(f0)]
            t["_ends"] = (float(np.median(v[:25])), float(np.median(v[-25:]))) if len(v) >= 30 else (None, None)
        return t["_ends"]

    def choose(self, takes, kept_voices):
        """Every word heard first; then the tool's score minus drift, plus the line's pitch range and its restart."""
        PF = self.PF
        ref = float(np.median(self.f0s)) if self.f0s else None
        for t in takes:
            t["drift"] = PF.drift_penalty(t["voice"], kept_voices)
            rng = float((t.get("feat") or {}).get("range_st") or 0.0)
            bonus = 3.0 * min(max(rng - 9.0, 0.0), 6.0)
            s0, _ = self.ends(t)
            if s0 and self.last_end is not None and ref:
                reset = 12 * math.log2(s0 / ref) - self.last_end
                # the references restart 5.4 st [2.9-6.6] over the last line's end; past 6 st a join reads as a seam
                bonus += 2.5 * min(max(reset, -2.0), 5.0) - 4.0 * max(reset - 6.0, 0.0)
                t["restart_st"] = round(reset, 2)
            t["bonus"] = round(bonus, 1)
            t["net"] = round(t["score"] - t["drift"] + bonus, 1)
        best = min(takes, key=lambda t: (t["errs"], -t["net"]))
        s0, s1 = self.ends(best)
        if s0 and s1:
            self.f0s += [s0, s1]
            ref = float(np.median(self.f0s))
            self.last_end = 12 * math.log2(s1 / ref)
        self.picks.append(dict(file=best.get("file"), net=best["net"], bonus=best.get("bonus"), restart_st=best.get("restart_st"),
                               range_st=(best.get("feat") or {}).get("range_st")))
        return best

    def join_lines(self, kept, segments, sr):
        """The generations joined with this read's gaps (0.22 s, 0.40 s at a turn), the takes' own floor between."""
        LN = self.LN
        parts, bounds, chunks, t = [], [], [], 0.0
        for i, (x, s) in enumerate(zip(kept, segments)):
            chunks.append({"seg": i, "text": s["text"]})
            parts.append(x)
            t += len(x) / sr
            if i < len(kept) - 1:
                gap = float(s.get("pause_after") or JOIN_S)
                parts.append(LN.room_tone(x, kept[i + 1], sr, gap, seed=i))
                bounds.append({"after_chunk": i, "t": round(t + gap / 2, 3), "pause_set": gap})
                t += gap
        return np.concatenate(parts).astype(np.float32), bounds, chunks


def render(a):
    import narrate as N
    N.malloc_returns_memory()            # a workflow calling render from its own entry point (long-form) gets it too
    import lines as LN
    import perform as PF
    import prosody
    script = json.load(open(a.script))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    groups = groups_of(script["segments"])
    gs = group_script(script, groups)
    gp = out / "_generations.json"
    gp.write_text(json.dumps(gs, ensure_ascii=False, indent=1))
    rd = Read(N, LN, PF, prosody)
    LN.generate_phonon = rd.generate_phonon
    LN.join_lines = rd.join_lines
    PF.choose = rd.choose
    args = argparse.Namespace(script=str(gp), out=str(out), voice=None, takes=TAKES, steps=10, cfg=2.0, seed=1, pace_tol=0.10,
                              tempo_anchor=True, lufs=None, engine=None, chunk_words=None, no_master=False, band=a.band)
    N.render(args)
    finish(out, script, groups, rd.picks)


# ----------------------------------------------------------------- the finish on the mastered read

def lra(x, sr):
    """EBU R128 loudness range as the independent voice check measures it: K-weighted (pyloudnorm's filters) 3 s
    short-term loudness every 0.1 s, gated at -70 LUFS and 20 LU under the file's mean, p95 - p10."""
    import pyloudnorm
    z = np.asarray(x, np.float64)
    for f in pyloudnorm.Meter(sr)._filters.values():
        z = f.apply_filter(z)
    w, h = int(3.0 * sr), int(0.1 * sr)
    if len(z) <= w:
        return 0.0
    c = np.concatenate([[0.0], np.cumsum(z ** 2)])
    s = np.arange(0, len(z) - w, h)
    l = -0.691 + 10 * np.log10((c[s + w] - c[s]) / w + 1e-14)
    l = l[l > -70]
    ig = -0.691 + 10 * np.log10(float(np.mean(z ** 2)) + 1e-14)
    l = l[l > ig - 20]
    return float(np.percentile(l, 95) - np.percentile(l, 10)) if len(l) >= 3 else 0.0


def envelope(n, sr, spans, ramp):
    """dB per sample: each (a, b, db) span with raised-cosine ramps of `ramp` seconds, the larger where two meet."""
    g = np.zeros(n, np.float32)
    r = max(int(ramp * sr), 1)
    for a, b, db in spans:
        if not db:
            continue
        i, j = max(0, int(a * sr) - r), min(n, int(b * sr) + r)
        if j - i < 2:
            continue
        env = np.ones(j - i, np.float32)
        k = min(r, (j - i) // 2)
        if k:
            ramp_up = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, k))
            env[:k] = ramp_up
            env[-k:] = ramp_up[::-1]
        seg = env * db
        g[i:j] = np.where(np.abs(seg) > np.abs(g[i:j]), seg, g[i:j])
    return g


def remap(t, cuts):
    """A time on the read before the cuts, on the read after them. cuts: [(at, removed seconds)], in order."""
    return t - sum(r for at, r in cuts if at < t)


def tighten(x, sr, words, segs):
    """Every pause between two words set by what ends there (as the listener times words, the way the check measures):
    a sentence 0.20 s, a turn of the story 0.40 s (the writer's beats), a comma or nothing 0.08 s; only ever shorter,
    and only inside the silence itself (40 ms kept against each word). Returns (x, words, cuts)."""
    fr = int(0.01 * sr)
    k = len(x) // fr
    db = 10 * np.log10((x[:k * fr].reshape(k, fr).astype(np.float64) ** 2).mean(1) + 1e-14)
    quiet = db < np.percentile(db, 90) - 32
    turns = {i for i in range(1, len(segs)) if segs[i].get("beat") in TURNS and segs[i].get("beat") != segs[i - 1].get("beat")}
    keep, cuts = [], []
    pos = 0
    for a, b in zip(words, words[1:]):
        g = b["start"] - a["end"]
        if g < 0.12:
            continue
        if b.get("seg") != a.get("seg") and b.get("seg") in turns:
            want = BEAT_S
        elif str(a["w"]).rstrip().endswith((".", "?", "!")):
            want = 0.20
        else:
            want = 0.08
        if g <= want + 0.02:
            continue
        i0, i1 = int((a["end"] + 0.04) * 100), int((b["start"] - 0.04) * 100)
        if i1 - i0 < 3:
            continue
        q = quiet[i0:i1]
        # the longest quiet run inside the gap: the silence itself
        best, run_a = (0, 0), None
        for j, v in enumerate(list(q) + [False]):
            if v and run_a is None:
                run_a = j
            elif not v and run_a is not None:
                if j - run_a > best[1] - best[0]:
                    best = (run_a, j)
                run_a = None
        n_q = (best[1] - best[0]) / 100
        rem = min(g - want, n_q - 0.02)
        if rem < 0.03:
            continue
        mid = (i0 + (best[0] + best[1]) / 2) / 100
        ca, cb = int((mid - rem / 2) * sr), int((mid + rem / 2) * sr)
        keep.append((pos, ca))
        pos = cb
        cuts.append((mid, round((cb - ca) / sr, 4)))
    keep.append((pos, len(x)))
    xf = int(0.008 * sr)
    parts = []
    for j, (a, b) in enumerate(keep):
        seg = x[a:b].astype(np.float32).copy()
        if j and len(seg) > xf and len(parts[-1]) > xf:          # an 8 ms crossfade inside the silence
            r = np.linspace(0, 1, xf, dtype=np.float32)
            parts[-1][-xf:] = parts[-1][-xf:] * (1 - r) + seg[:xf] * r
            seg = seg[xf:]
        parts.append(seg)
    y = np.concatenate(parts) if parts else x
    for w in words:
        w["start"], w["end"] = round(remap(w["start"], cuts), 3), round(remap(w["end"], cuts), 3)
    return y.astype(np.float32), words, cuts


ARTIC_AIM = 192.0         # words a minute while speaking (the metromedia narrators: 191-197; the gate's band 175-217)


def artic(words):
    """Words a minute while speaking, as the listener times them: pauses of 0.15 s or more are not speaking."""
    if len(words) < 8:
        return None
    t = sum(w["end"] - w["start"] for w in words) + sum(g for g in (b["start"] - a["end"] for a, b in zip(words, words[1:])) if 0 < g < 0.15)
    return len(words) / max(t, 1e-3) * 60


def pace(x, sr, words, ffmpeg):
    """The read played faster, pitch kept (ffmpeg atempo, WSOLA), when it speaks slower than ARTIC_AIM; at most 1.12x.
    Word times scale with it. Returns (x, factor)."""
    import subprocess
    import tempfile
    import soundfile as sf
    a = artic(words)
    if not a or a >= ARTIC_AIM - 6:
        return x, 1.0
    f = round(min(ARTIC_AIM / a, 1.12), 4)
    with tempfile.TemporaryDirectory() as td:
        src, dst = os.path.join(td, "a.wav"), os.path.join(td, "b.wav")
        sf.write(src, x, sr, subtype="FLOAT")
        p = subprocess.run([ffmpeg, "-v", "error", "-y", "-i", src, "-af", f"atempo={f}", "-c:a", "pcm_f32le", dst], capture_output=True)
        if p.returncode or not os.path.exists(dst):
            return x, 1.0
        y, _ = sf.read(dst, dtype="float32")
    for w in words:
        w["start"], w["end"] = round(w["start"] / f, 3), round(w["end"] / f, 3)
    return y.astype(np.float32), f


LIFT_ST = {"hook": 1.3, "turn": 0.6, "payoff": 0.9, "low": -1.0, "close": -0.5}


def lift(x, sr, words, segs, ffmpeg):
    """A narrator's register follows the story: the hook, the turn and the payoff sit higher, the low point lower
    (semitones by beat, LIFT_ST), each segment shifted whole between its own silences (ffmpeg: resample, then WSOLA tempo
    back to its length, formants within 6%). Words keep their times."""
    import subprocess
    import tempfile
    import soundfile as sf
    done = []
    for si, s in enumerate(segs):
        st_ = LIFT_ST.get(s.get("beat"), 0.0)
        ws = [w for w in words if w.get("seg") == si]
        if not st_ or not ws:
            continue
        a, b = max(ws[0]["start"] - 0.05, 0.0), min(ws[-1]["end"] + 0.08, len(x) / sr)
        i, j = int(a * sr), int(b * sr)
        r = 2 ** (st_ / 12)
        with tempfile.TemporaryDirectory() as td:
            src, dst = os.path.join(td, "a.wav"), os.path.join(td, "b.wav")
            sf.write(src, x[i:j], sr, subtype="FLOAT")
            p = subprocess.run([ffmpeg, "-v", "error", "-y", "-i", src, "-af", f"asetrate={sr * r:.3f},aresample={sr},atempo={1 / r:.6f}",
                                "-c:a", "pcm_f32le", dst], capture_output=True)
            if p.returncode or not os.path.exists(dst):
                continue
            y, _ = sf.read(dst, dtype="float32")
        n = j - i
        y = np.concatenate([y, np.zeros(max(0, n - len(y)), np.float32)])[:n]
        xf = min(int(0.012 * sr), n // 4)
        rmp = np.linspace(0, 1, xf, dtype=np.float32)
        y[:xf] = x[i:i + xf] * (1 - rmp) + y[:xf] * rmp
        y[-xf:] = y[-xf:] * (1 - rmp) + x[j - xf:j] * rmp
        x = x.copy()
        x[i:j] = y
        done.append(dict(seg=si, beat=s.get("beat"), st=st_))
    return x, done


def word_db(x, sr, w):
    seg = x[int(w["start"] * sr):int(w["end"] * sr)].astype(np.float64)
    return 10 * math.log10(max(float((seg ** 2).mean()), 1e-12)) if len(seg) > 0.03 * sr else None


def finish(out, script, groups, picks=None):
    """words.json back on the film's segments; focus words to +4.3 dB; lines by beat to a 3.3 LU range; room tone; -14 LUFS."""
    import soundfile as sf
    import master as MST
    out = Path(out)
    x, sr = sf.read(out / "narration.wav", dtype="float32")
    if x.ndim > 1:
        x = x.mean(1)
    wj = json.loads((out / "words.json").read_text())
    segs = script["segments"]
    seg_of = [si for si, s in enumerate(segs) for _ in s["text"].split()]
    words = wj["words"]
    if len(seg_of) == len(words):
        for w, si in zip(words, seg_of):
            w["seg"] = si
        for p in wj.get("phrases") or []:
            p["seg"] = next((w["seg"] for w in words if abs(w["start"] - p["start"]) < 1e-3), p.get("seg"))
    rep = dict(generations=[dict(segments=g, text_words=sum(len(segs[i]["text"].split()) for i in g)) for g in groups], picks=picks or [])
    # pauses: a sentence 0.20 s, a turn 0.40 s, a comma nothing a listener counts; the times of everything after move with them
    x, words, cuts = tighten(x, sr, words, segs)
    for p in wj.get("phrases") or []:
        p["start"], p["end"] = round(remap(p["start"], cuts), 3), round(remap(p["end"], cuts), 3)
    wj["cut_points"] = [round(remap(t, cuts), 3) for t in wj.get("cut_points") or []]
    wj["duration"] = round(len(x) / sr, 2)
    rep["pauses"] = dict(cut=len(cuts), removed_s=round(sum(r for _, r in cuts), 2))
    rj = out / "render.json"
    r = json.loads(rj.read_text()) if rj.exists() else {}
    for b in r.get("boundaries") or []:            # the joins the VOICE gate measures move with the cuts
        b["t"] = round(remap(float(b["t"]), cuts), 3)
        if b.get("gap"):
            b["gap"] = [round(remap(float(g), cuts), 3) for g in b["gap"]]
    ff = os.environ.get("NARRATE_FFMPEG") or "/Users/masaoodi/Documents/Valmera/audit/subscriber-reliability-20260908/bin-arm/ffmpeg"
    # pace: a read slower than the references while speaking is brought toward them whole (WSOLA, at most 12%)
    x, f = pace(x, sr, words, ff)
    if f != 1.0:
        for p in wj.get("phrases") or []:
            p["start"], p["end"] = round(p["start"] / f, 3), round(p["end"] / f, 3)
        wj["cut_points"] = [round(t / f, 3) for t in wj.get("cut_points") or []]
        wj["duration"] = round(len(x) / sr, 2)
        for b in r.get("boundaries") or []:
            b["t"] = round(float(b["t"]) / f, 3)
            if b.get("gap"):
                b["gap"] = [round(float(g) / f, 3) for g in b["gap"]]
    rep["tempo"] = round(f, 4)
    x, lifted = lift(x, sr, words, segs, ff)
    rep["register"] = lifted
    # focus words: measured against the median word of their line, ridden up to FOCUS_DB
    spans, hits = [], []
    for si, s in enumerate(segs):
        ws = [w for w in words if w.get("seg") == si]
        lv = [(w, word_db(x, sr, w)) for w in ws]
        lv = [(w, d) for w, d in lv if d is not None]
        if len(lv) < 3:
            continue
        want = {norm(e) for e in (s.get("emphasis") or [])[:2] for e in str(e).split()}
        for w, d in lv:
            if norm(w["w"]) in want:
                rest = [d2 for w2, d2 in lv if w2 is not w]
                have = d - float(np.median(rest))
                g = min(max(FOCUS_DB - have, 0.0), 5.0)
                spans.append((w["start"] - 0.02, w["end"] + 0.03, g))
                hits.append(dict(seg=si, word=w["w"], before_db=round(have, 2), ride_db=round(g, 2)))
                want.discard(norm(w["w"]))
    x = (x * 10 ** (envelope(len(x), sr, spans, 0.025) / 20)).astype(np.float32)
    rep["focus"] = hits
    # loudness range: each line by its beat and energy, scaled until the read measures inside LRA_AIM
    base = []
    for si, s in enumerate(segs):
        ws = [w for w in words if w.get("seg") == si]
        if ws:
            base.append((ws[0]["start"] - 0.06, ws[-1]["end"] + 0.06, BEAT_DB.get(s.get("beat"), 0.0) + ENERGY_DB.get(s.get("energy"), 0.0)))
    rng = np.random.default_rng(1114)
    z = np.fft.rfft(rng.standard_normal(len(x)))
    fr = np.fft.rfftfreq(len(x), 1 / sr)
    z *= 1 / np.sqrt(np.maximum(fr, 60.0) / 60.0)         # room tone: pink above 60 Hz, steady, ROOM_DB under the speech
    z[fr > 9000] *= 0.3
    tone = np.fft.irfft(z, len(x)).astype(np.float32)
    tone *= 10 ** ((MST.LUFS + ROOM_DB) / 20) / (float(np.sqrt((tone ** 2).mean())) + 1e-12)

    def chain(k):
        # long ramps (0.8 s): the read's level moves through a line's edge, never steps at a join (the VOICE gate's join check)
        y = (x * 10 ** (envelope(len(x), sr, [(a, b, max(min(d * k, 4.5), -5.0)) for a, b, d in base], 0.8) / 20)).astype(np.float32) if k else x
        return MST.loudness_set(y + tone, sr, MST.LUFS, -1.0)

    y = chain(0.0)
    before = lra(y, sr)
    best = (abs(before - sum(LRA_AIM) / 2), 0.0, y)
    if before < LRA_AIM[0]:
        lo, hi = 0.0, 4.0
        for _ in range(8):
            k = (lo + hi) / 2
            y = chain(k)
            v = lra(y, sr)
            if abs(v - sum(LRA_AIM) / 2) < best[0]:
                best = (abs(v - sum(LRA_AIM) / 2), k, y)
            if LRA_AIM[0] <= v <= LRA_AIM[1]:
                break
            lo, hi = (k, hi) if v < LRA_AIM[0] else (lo, k)
    x = best[2]
    rep["lra"] = dict(before=round(before, 2), scale=round(best[1], 3), after=round(lra(x, sr), 2))
    rep["room_tone_db"] = ROOM_DB
    rep["lufs"] = round(MST.lufs(x, sr), 2)
    sf.write(out / "narration.wav", x, sr, subtype="PCM_24")
    (out / "words.json").write_text(json.dumps(wj, indent=1))
    r["stories_read"] = rep
    rj.write_text(json.dumps(r, indent=1, default=str))
    print(json.dumps(dict(narration=str(out / "narration.wav"), lra=rep["lra"], focus=len(hits), lufs=rep["lufs"])))


def level_voices(a):
    """Each real voice cut into the narration (narrate.py splice) brought to the narrator's own loudness around it,
    K-weighted (BS.1770), within 1 LU: an archive clip's narrow band under-reads by RMS (VO2: -3.6 LU)."""
    import soundfile as sf
    import master as MST
    out = Path(a.dir)
    rj = out / "render.json"
    vs = (json.loads(rj.read_text()) if rj.exists() else {}).get("voices") or []
    fp = out / "footage_voices.json"
    if not vs and fp.exists():
        vs = json.loads(fp.read_text())
    if not vs:
        print(json.dumps(dict(levelled=[])))
        return
    x, sr = sf.read(out / "narration.wav", dtype="float32")
    done = []
    for v in vs:
        a0, b0 = float(v["start"]), float(v["end"])
        i, j = int(a0 * sr), int(b0 * sr)
        around = np.concatenate([x[max(0, i - int(6 * sr)):max(0, i - int(0.1 * sr))], x[min(len(x), j + int(0.1 * sr)):min(len(x), j + int(6 * sr))]])
        if j - i < int(0.8 * sr) or len(around) < sr:
            continue
        want, have = MST.lufs(around, sr), MST.lufs(x[i:j], sr)
        g = max(min(want - have, 8.0), -8.0)
        if abs(g) > 0.5:
            x = (x * 10 ** (envelope(len(x), sr, [(a0, b0, g)], 0.015) / 20)).astype(np.float32)
        done.append(dict(start=a0, end=b0, narrator_lufs=round(want, 2), voice_lufs=round(have, 2), gain_db=round(g, 2)))
    x = MST.loudness_set(x, sr, MST.LUFS, -1.0)
    sf.write(out / "narration.wav", x, sr, subtype="PCM_24")
    print(json.dumps(dict(levelled=done)))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render")
    r.add_argument("script")
    r.add_argument("--out", required=True)
    r.add_argument("--band", default="03")
    f = sub.add_parser("finish", help="the finish alone, on a read already rendered from this script")
    f.add_argument("script")
    f.add_argument("--out", required=True)
    lv = sub.add_parser("level-voices")
    lv.add_argument("dir")
    a = ap.parse_args()
    if not os.environ.get("OPENAI_API_KEY"):          # the listeners' spoken form runs on OpenAI: the backend's key, never printed
        envf = Path(os.environ.get("NARRATE_ENV_FILE", "/Users/masaoodi/Documents/Valmera/hustler-server/backend/.env"))
        if envf.exists():
            for line in envf.read_text().splitlines():
                if line.startswith("OPENAI_API_KEY="):
                    os.environ["OPENAI_API_KEY"] = line.split("=", 1)[1].strip().strip('"').strip("'")
    if a.cmd == "render":
        import narrate as N
        N.malloc_returns_memory()        # before the chdir: the arguments may be relative paths
    os.chdir(NARR)
    if a.cmd == "render":
        return render(a)
    if a.cmd == "finish":
        script = json.load(open(a.script))
        return finish(a.out, script, groups_of(script["segments"]))
    return level_voices(a)


if __name__ == "__main__":
    sys.exit(main() or 0)
