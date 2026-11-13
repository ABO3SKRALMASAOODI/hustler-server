"""The film's sound design (lane J, 13 Nov): one pass that gives the film's chosen picture events a sound from
the owner's library, peak on the event, and the master that sets the voice clean and level.

    apply(l1, work=None, index=None, perc=None, asks=None)   the seam: the L1 with its designed `sound`, never raises
    design(l1, ...) -> (sounds, report)                      the same, returning what it decided
    plan_sounds(events, ctx) -> (sounds, report)             the core, on picture events from any source

The master (voice levelled, sounds on it, -14 LUFS, under -1 dBTP, AAC) is scene.audio.master: the engine reads no
score code (test_the_engine_imports_no_model_code), so the mix lives on its side of the seam.

Owner rules (binding): only the approved recordings of `sound_library` (manifest, sound:<id>); nothing synthesised;
never music; never a sound on a caption. A sound answers a picture event (a new picture, a card or page landing, a
display line, a counter step, a flash on a cut, the hook and the payoff) with its PEAK on the event's frame, 13 ms
early (atlas 8.5: locked sounds peak a median -13 ms from the cut, in every class). The count follows the picture
supply and the no-bed allowance of TASTE 4.2 (taste.allow): a talking take carries one or two sounds, a film of new
pictures up to 0.9 per new picture per 10 s, never more in any 10 s than the class allows, three or four kinds of
sound in 30 s, one recording at most twice in 30 s and never twice inside 3 s. The hook and the payoff are layered
(a lead whose peak meets the hit): one gesture to the ear and to every counter. Gains are set against the voice the
mix will have (the renderer levels speech to dialogue_level's -20 LUFS first), by role, from the judged targets of
sfx_mix (Oct 10): sweeps 8 dB under the voice, tonal hits 10, risers 9, impacts low (their content above 150 Hz 13
under, the whole hit at most 8 under). Under speech a sound sits at least the class's margin under the voice. Then a
signal check: each layer is laid over the programme's own voice in 20 ms frames, and one whose speech band
(300-3400 Hz) comes within 3 dB of the voice on spoken frames is lowered to its role's floor, else dropped.

Nothing here reads what anyone asked for. When the Scout bound an ask to the sound department (a model read it),
the Director's own lines carry that ask and this pass adds nothing.
"""
import json
import math
import os
import re
import subprocess

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SOUNDS = os.environ.get("VALMERA_SOUNDS") or os.path.join(os.path.dirname(HERE), "sound_library")
FF = os.environ.get("FFMPEG_BIN", "ffmpeg")

SR = 48000                  # the mix rate
AR = 16000                  # the analysis rate
LEAD_S = 0.013              # a placed sound's peak lands this far before its frame (atlas 8.5)
VOICE_LUFS = -20.0          # dialogue_level.TARGET_LUFS: the voice every gain is set against
GAIN_MIN, GAIN_MAX = -36.0, -2.0
MERGE_S = 1.0               # sounds closer than this are one gesture (edit_delta.MERGE_S)
SPACE_S = 1.5               # two gestures never closer than this
SAME_GAP_S, SAME_WIN_S, SAME_MAX = 3.0, 30.0, 2
HOOK_S = 3.0
PAYOFF_FROM = 0.6           # the payoff is a designed landing in the film's last 40%
END_GUARD_S = 0.3           # no sound lands in the last 0.3 s (the brand's end card follows)
MASK_DB = 3.0               # a sound within 3 dB of the voice in the speech band, on a spoken frame, masks it
MASK_FRAMES = 3             # for 60 ms or more
FRAME = 320                 # 20 ms at AR
SPEECH_BAND = (300.0, 3400.0)
COUNTER_TICKS = 8
PLACED_10S = 3              # atlas 8.5: the measurable placed layer is 1-3 sounds per 10 s, whatever the transients
OWN_CUT_10S = 1             # the upload's own cuts: at most one sounded in any 10 s

# Role targets, dB of the hit (K-weighted loudest 200 ms, manifest hit_lufs) against the voice: sfx_mix.ROLE_MIX,
# judged on the showcase shorts (Oct 10). "lo" is the floor the signal check may lower a sound to.
_SWEEP = {"target": -8.0, "lo": -12.5}
_TONAL = {"target": -10.0, "lo": -14.0}
_LOW = {"target": -13.0, "cap": -8.0, "lo": -16.0, "hp": True}
ROLE = {"whoosh": _SWEEP, "swish": _SWEEP, "riser": {"target": -9.0, "lo": -12.0},
        "ding": _TONAL, "pop": _TONAL, "tick": _TONAL, "click": _TONAL, "shutter": _TONAL, "cash": _TONAL,
        "glitch": _TONAL, "typing": {"target": -14.0, "lo": -18.0}, "impact": _LOW, "heartbeat": _LOW}
LEAD_DB = -2.0              # a lead sits under its hit
COUNTER_DB = -4.0           # ticks on a counter are texture, under one hit
NO_VOICE_LIFT = 8.0         # with nothing spoken, the "voice" a sound is set against is the track 8 dB up

# What answers what. A gesture is its layers, each (recording, part); every peak lands on the event.
# Alternates keep the one-recording rules without changing the gesture's kind.
ALT = {"whoosh_soft_2": "whoosh_soft_1", "whoosh_soft_1": "whoosh_soft_2", "shutter_1": "shutter_2",
       "shutter_2": "shutter_1", "riser_3": "riser_1", "riser_1": "riser_2", "riser_2": "riser_4",
       "swish_1": "whoosh_soft_2", "pop_1": "click_2", "click_2": "click_1", "glitch_1": "glitch_2"}
KIND_RANK = {"picture": 0, "card": 1, "page": 1.5, "display": 2, "counter": 2.5, "flash": 3, "cut": 4}
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


def gain_for(sid, ref, under=None, offset=0.0):
    """The gain that puts this recording's hit at its role target against a voice at `ref` LUFS, then at least
    `under` dB under it when the voice speaks there."""
    x = library().get(sid) or {}
    sp = ROLE.get(role_of(sid), _TONAL)
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
    sp = ROLE.get(role_of(sid), _TONAL)
    return ref + sp["lo"] - float(x.get("hit_lufs", -15.0))


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


def l1_events(l1, kshots=()):
    """What lands on screen, by programme frame: [{f, kind, ...}]. A crop of the same take is not a new picture;
    a caption is never an event."""
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
    last = None
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
        last = k
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
    out = []
    for e in sorted(ev, key=lambda e: (e["f"], KIND_RANK.get(e["kind"], 9))):
        if 0 <= e["f"] < D and not any(abs(e["f"] - o["f"]) <= 2 for o in out):
            out.append(e)
        elif out and abs(e["f"] - out[-1]["f"]) <= 2 and KIND_RANK.get(e["kind"], 9) < KIND_RANK.get(out[-1]["kind"], 9):
            out[-1] = e
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


def plan_sounds(events, ctx):
    """The core. events: [{f, kind, ...}] in programme frames. ctx: fps, dur_f, existing (sound items already in the
    film), speech (bool per 20 ms frame) and voice (mono AR programme voice) or None, words (programme seconds),
    bed, prog_end_f, np10 (or None: counted from the events). Returns (sound items, report)."""
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
    al = dict(_allow(cls, np10, dur_s, bed))
    al["sounds_10s"] = min(int(al["sounds_10s"]), PLACED_10S)
    al["sounds"] = min(int(al["sounds"]), max(1, int(PLACED_10S * dur_s / 10.0 + 1e-9)))
    report = {"class": cls, "np10": round(np10, 2), "bed": bed, "allow": al["sounds"], "allow_10s": al["sounds_10s"],
              "events": len(events), "placed": [], "dropped": [], "kept_director": len(ctx.get("existing") or [])}
    existing = list(ctx.get("existing") or [])
    ex_t = [float(s.get("event_f", 0)) / fps for s in existing]
    taken = _gestures(ex_t)
    uses = [(float(s.get("event_f", 0)) / fps, s.get("s")) for s in existing]
    roles = [(float(s.get("event_f", 0)) / fps, role_of(s.get("s"))) for s in existing if s.get("s") in lib]
    speech = ctx.get("speech")
    words = ctx.get("words") or []

    def spoken(t, a=-0.1, b=0.25):
        if speech is None or not len(speech):
            return any(w0 < t + b and w1 > t + a for w0, w1 in words)
        i0, i1 = max(0, int((t + a) * AR / FRAME)), min(len(speech), int((t + b) * AR / FRAME) + 1)
        return bool(speech[i0:i1].any())

    # candidates: designed landings first, the hook and the payoff ahead of all
    cands = [e for e in events if e["f"] < end_f - int(END_GUARD_S * fps)
             and (e["f"] > 1 or e["kind"] in ("display", "card", "page", "counter"))
             and not any(abs(e["f"] / fps - t) <= 3.0 / fps for t in ex_t)]
    hook = next((e for e in cands if e["f"] < HOOK_S * fps and e["kind"] in DESIGNED), None)
    late = [e for e in cands if e["f"] >= PAYOFF_FROM * end_f and e["kind"] in ("display", "card", "page", "picture", "counter")]
    payoff = min(late, key=lambda e: (KIND_RANK[e["kind"]] > 2, -e["f"])) if late else None
    if payoff is not None and hook is not None and payoff is hook:
        payoff = None

    def rank(e):
        part = 0 if e is hook else 1 if e is payoff else 2
        return (part, 1 if spoken(e["f"] / fps) else 0, KIND_RANK.get(e["kind"], 9), e["f"])
    placed = []
    for s in existing:                       # the Director's own library sounds: same levels, same signal check
        if s.get("s") not in lib:
            continue
        t = float(s.get("event_f", 0)) / fps
        ref = ctx["ref_at"](t) if callable(ctx.get("ref_at")) else VOICE_LUFS
        was = s.get("db")
        s["db"] = gain_for(s["s"], ref, al["under_voice_db"] if spoken(t) else None)
        s["director_db"] = was
        placed.append({"t": t, "ev": {"kind": s.get("for") or "director", "f": s.get("event_f", 0)}, "part": "director",
                       "items": [dict(s, layer="hit", **({"for": "director"} if "for" not in s else {}))], "ref": ref, "director": True})
    own_cut, own_cap, own_t = 0, max(1, int(round(0.5 * al["sounds"]))), []
    for e in sorted(cands, key=rank):
        t = e["f"] / fps
        why = None
        if len(taken) >= al["sounds"]:
            why = f"the footage carries {al['sounds']} (class {cls}{', a bed' if bed else ''})"
        elif e["kind"] == "cut" and (own_cut >= own_cap or any(abs(u - t) < 10.0 for u in own_t) and OWN_CUT_10S <= 1):
            why = "the upload's own cuts already hold their share"
        elif any(abs(t - g) < SPACE_S for g in taken):
            why = "another sound is inside 1.5 s"
        elif sum(1 for g in taken if abs(g - t) < 10.0) >= al["sounds_10s"] and \
                any(sum(1 for g in taken + [t] if w <= g < w + 10.0) > al["sounds_10s"] for w in np.arange(t - 10.0, t + 0.01, 0.5)):
            why = f"more than {al['sounds_10s']} inside 10 s"
        if why:
            report["dropped"].append({"f": e["f"], "kind": e["kind"], "why": why})
            continue
        part = "hook" if e is hook else "payoff" if e is payoff else "plain"
        prev = max([g for g in taken if g < t], default=0.0)
        e = dict(e, room_s=t - prev)
        layers = []
        for sid, what in gesture(e, part):
            sid = _free(sid, t, uses)
            if sid is None or sid not in lib:
                if what == "hit":
                    layers = []
                    break
                continue
            layers.append((sid, what))
        win_roles = {r for (u, r) in roles if abs(u - t) < SAME_WIN_S / 2}
        new_roles = {role_of(s) for s, _ in layers} - win_roles
        if layers and len(win_roles) + len(new_roles) > al["roles"]:
            layers = [(s, w) for s, w in layers if w == "hit"]
            if role_of(layers[0][0]) not in win_roles and len(win_roles) + 1 > al["roles"]:
                layers = []
        if not layers:
            report["dropped"].append({"f": e["f"], "kind": e["kind"], "why": "no recording left for it inside 30 s"})
            continue
        under = al["under_voice_db"] if spoken(t) else None
        ref = ctx["ref_at"](t) if callable(ctx.get("ref_at")) else VOICE_LUFS
        steps = e.get("steps") if e["kind"] == "counter" else None
        group = []
        if steps and part != "plain":           # a counter that is the hook or the payoff: ticks, then its last value lands
            layers = [("tick_1", "hit")] + [(sid, w) for sid, w in layers if role_of(sid) == "impact"]
        for sid, what in layers:
            off = LEAD_DB if what == "lead" else 0.0
            if steps and role_of(sid) == "tick":
                for sf in steps:
                    group.append(_item(f"snd.j{len(placed)}.{len(group)}", sid, sf, fps, gain_for(sid, ref, under, COUNTER_DB), e, part, what))
            else:
                f_at = steps[-1] if steps else e["f"]
                group.append(_item(f"snd.j{len(placed)}.{len(group)}", sid, f_at, fps, gain_for(sid, ref, under, off), e, part,
                                   "accent" if steps else what))
        placed.append({"t": t, "ev": e, "part": part, "items": group, "ref": ref})
        taken.append(t)
        if e["kind"] == "cut":
            own_cut += 1
            own_t.append(t)
        for sid, _w in layers:
            uses.append((t, sid))
            roles.append((t, role_of(sid)))
    sounds, report = _signal_check(placed, ctx, report)
    sounds += [s for s in existing if s.get("s") not in lib]          # the Director's voice layers (echo, swell) as written
    sounds.sort(key=lambda s: (s["event_f"], s["id"]))
    report["sounds"] = len(sounds)
    return sounds, report


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


def _signal_check(placed, ctx, report):
    """Each layer laid over the programme's own voice: a sound that masks the speech band on spoken frames is
    lowered to its role's floor, else dropped with its gesture's leads."""
    voice, speech = ctx.get("voice"), ctx.get("speech")
    out = []
    vb = None
    if voice is not None and speech is not None and len(voice) and speech.any():
        vb = frames_db(voice * (10 ** (float(ctx.get("voice_shift_db") or 0.0) / 20)))[0]
        k = 7                                   # the voice's level over a syllable: the loudest frame within 0.15 s
        pad = np.pad(vb, k, mode="edge")
        vb = np.max(np.stack([pad[j:j + len(vb)] for j in range(2 * k + 1)]), axis=0)
    for g in placed:
        hits = [i for i in g["items"] if i["layer"] == "hit"]
        keep = []
        for it in g["items"]:
            if vb is not None and it["for"] != "counter":
                need = _masking(it, vb, speech, ctx["fps"])
                if need > 0:
                    lowest = floor_for(it["s"], g["ref"])
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
                i.pop("layer", None)
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
        report["placed"].append({"t": round(g["t"], 2), "kind": g["ev"]["kind"], "part": g["part"], "layers": lay})
    out.sort(key=lambda s: (s["event_f"], s["id"]))
    report["gestures"] = len(report["placed"])
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
    bad = over[sp & (sb > -80)]
    if (bad > 0).sum() < MASK_FRAMES:
        return 0.0
    return float(np.percentile(bad[bad > 0], 80)) + 0.5


# ----------------------------------------------------------------- the seam

def _asked(asks):
    for a in (asks or {}).values() if isinstance(asks, dict) else (asks or []):
        if isinstance(a, dict) and a.get("probe") in ("sfx_count", "music", "loudness"):
            return True
    return False


def design(l1, work=None, index=None, perc=None, asks=None, src=None, supply=None):
    """The designed sound for a compiled film: (sounds, report). The Director's own sounds stay and count first.
    supply: the governor's {np10, bed} (taste.supply) when the compiler has it; else read from the L1 and the upload."""
    fps, D = float(l1["fps"]), int(l1["dur_f"])
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
    ctx = {"fps": fps, "dur_f": D, "existing": existing, "speech": speech, "voice": voice, "voice_shift_db": shift,
           "words": words or [], "bed": bed, "ref_at": ref_at, "np10": (supply or {}).get("np10")}
    sounds, report = plan_sounds(events, ctx)
    report["speaks"] = bool(speaks)
    return sounds, report


def apply(l1, work=None, index=None, perc=None, asks=None, supply=None):
    """The seam for the compile path and the workflow shell: the L1 with its designed sound. Never raises; on any
    fault the L1 comes back as it was, with the fault in l1["sound_design"]. VALMERA_SOUND_DESIGN=0 turns it off."""
    if os.environ.get("VALMERA_SOUND_DESIGN", "1") == "0":
        return l1
    try:
        sounds, report = design(l1, work=work, index=index, perc=perc, asks=asks, supply=supply)
        l1["sound"] = sounds
        l1["sound_design"] = report
    except Exception as e:  # noqa: BLE001 — a film is never lost to its sound design
        l1["sound_design"] = {"fault": repr(e)[:300]}
    return l1


# ----------------------------------------------------------------- command line

def main(argv=None):
    """python -m score.sound design --l1 l1.json --work DIR --out l1_sound.json"""
    import argparse
    ap = argparse.ArgumentParser(prog="score.sound")
    ap.add_argument("cmd", choices=["design"])
    ap.add_argument("--l1", required=True)
    ap.add_argument("--work")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    l1 = json.load(open(a.l1))
    l1 = apply(l1, work=a.work or os.path.dirname(os.path.abspath(a.l1)))
    tmp = a.out + ".part"
    with open(tmp, "w") as fh:
        json.dump(l1, fh)
    os.replace(tmp, a.out)
    print(json.dumps(l1.get("sound_design") or {}, default=float)[:2000])


if __name__ == "__main__":
    main()
