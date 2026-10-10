"""Sound effects levelled against the voice they sit under (Oct 2026).

Independent judges measured what one absolute gain per sound does. The same
-14/-15 dB setting put a notification ding about 9 dB OVER the voice on the
payoff word '140' (Thiel), masking it, and left a swish 12 dB under the voice,
inaudible (Elon). The approved recordings differ by 20 dB in their own
loudness (ding_1 hits at -5.8 LUFS, click_1 at -25.2), so a suggested gain
per file cannot sit right everywhere. What matters is the recording's hit
RELATIVE TO THE VOICE AT THAT MOMENT, so that is what this module sets.

Two measurements:

* A recording's HIT loudness: the K-weighted (ITU BS.1770) loudness of its
  loudest 200 ms at 0 dB gain, stereo at 48 kHz as the renderer mixes it
  (``hit_lufs`` in sound_library/manifest.json), and the same with
  everything under 150 Hz removed (``hit_lufs_hp150``). 200 ms is roughly
  the ear's integration time for a transient; the 400 ms momentary window
  under-reads a 0.08-0.3 s click or swish by 3 dB. Measured once from the
  approved files (measure_recording) and pinned by a test; the files
  themselves are never altered.

* The VOICE at a hit: the speech-gated loudness of the program audio in a
  3 s window centred on the hit (the EBU short-term length), measured with
  ffmpeg's ebur128 over the kept source spans exactly as the renderer
  concatenates them (inserts count as silence). On a mastered (social) mix
  the renderer's dialogue leveller then moves speech to
  dialogue_level.TARGET_LUFS plus (1 - STRENGTH) of its local deviation;
  that is applied here too, with the surrounding 12 s standing in for the
  whole program's speech level (the error is 20% of the difference). The
  user's volume automation at the hit is added on top, as the renderer does.
  When the voice cannot be measured (no media here, no speech near the hit)
  the nominal leveled dialogue level stands in, and the report says so.

Role targets, dB of the hit relative to the voice (judges' brief, Oct 10):
whoosh and swish 6-10 dB under (target 8), risers 9 under, tonal and UI
hits (ding, pop, tick, click, shutter, cash, glitch) about 10 under, typing
14 under (it plays under its action), and impacts (heartbeat too) louder
only below ~150 Hz: their content above 150 Hz sits 13 dB under the voice
and the whole hit, sub included, at most 8 dB under. Calibrated against the
judged showcase cues: the Elon swish at -16 dB measures 13 dB under (judged
inaudible), the Jobs shutter at -14 dB 13.4 under (judged "audible as a
tick"), the Thiel ding at -14 dB 0.2 OVER (judged masking the payoff), the
Jobs impact at -14 dB 3 dB under in full band (judged trailer-heavy, "lower
it 6 dB").

The renderer is unchanged: an sfx item still carries one gain_db. add_sfx
and template-owned cues write the gain this module computes (an explicit
gain_db always wins) and report the relative level; the master loudnorm
moves voice and sound together, so the relation survives it.
"""

import math
import os
import shutil
import subprocess
import tempfile

import dialogue_level
import sound_library

HIT_WINDOW_S = 0.2
HP_HZ = 150.0
# BS.1770 K-weighting as two ffmpeg biquads (stage 1 high shelf, stage 2 RLB
# high-pass), so a recording measured here reads what ebur128 reads.
K_WEIGHT = ("highshelf=f=1681.97:g=4:t=q:w=0.7072,"
            "highpass=f=38.14:t=q:w=0.5003")
HP_FILTER = f"highpass=f={HP_HZ:g}:poles=2,highpass=f={HP_HZ:g}:poles=2"

# Voice measurement.
VOICE_WINDOW_S = 3.0          # short-term, centred on the hit
CONTEXT_S = 6.0               # each side: the program speech-level stand-in
MIN_SPEECH_BLOCKS = 3         # 100 ms momentary blocks inside the window
MAX_PIECES = 24               # source spans in one measurement
NOMINAL_VOICE_LUFS = dialogue_level.TARGET_LUFS

# Auto-gain bounds: never past 0 dB (a quiet recording is not pushed into
# the master's limiter) and never below the point where nothing is left.
AUTO_GAIN_MIN_DB = -45.0
AUTO_GAIN_MAX_DB = 0.0
GAIN_STEP_DB = 0.5

# target / acceptable range of the judged measure, dB relative to the voice.
# "band": "hp150" judges the content above 150 Hz; "cap" bounds the whole hit.
_TONAL = {"target": -10.0, "lo": -14.0, "hi": -6.0}
_SWEEP = {"target": -8.0, "lo": -12.5, "hi": -5.0}
# Low roles: the content above 150 Hz is placed (target, hi) and the whole
# hit, sub included, is capped (cap) and judged audible (lo) in full band —
# a heartbeat is nearly all sub, so its audibility IS the sub.
_LOW = {"target": -13.0, "hi": -9.0, "band": "hp150",
        "cap": -8.0, "cap_hot": -4.0, "lo": -16.0}
ROLE_MIX = {
    "whoosh": _SWEEP,
    "swish": _SWEEP,
    "riser": {"target": -9.0, "lo": -12.0, "hi": -5.0},
    "ding": _TONAL, "pop": _TONAL, "tick": _TONAL, "click": _TONAL,
    "shutter": _TONAL, "cash": _TONAL, "glitch": _TONAL,
    "typing": {"target": -14.0, "lo": -18.0, "hi": -9.0},
    "impact": _LOW,
    "heartbeat": _LOW,
}


def mix_role(sound_id):
    """The library role a recording is mixed by (tonal when unknown)."""
    r = sound_library.get(sound_id) or {}
    return r.get("role") if r.get("role") in ROLE_MIX else "ding"


def spec(sound_id):
    return ROLE_MIX[mix_role(sound_id)]


def hit_level(sound_id):
    """(full-band, above-150 Hz) hit loudness of a recording at 0 dB, or
    (None, None) when the manifest carries no measurement."""
    r = sound_library.get(sound_id) or {}
    k, hp = r.get("hit_lufs"), r.get("hit_lufs_hp150")
    return (float(k) if k is not None else None,
            float(hp) if hp is not None else None)


def _judged(sound_id, gain_db, voice_lufs):
    """(judged relative dB, full-band relative dB) at this gain, or None."""
    k, hp = hit_level(sound_id)
    if k is None or voice_lufs is None:
        return None
    full = k + float(gain_db) - float(voice_lufs)
    if spec(sound_id).get("band") == "hp150" and hp is not None:
        return hp + float(gain_db) - float(voice_lufs), full
    return full, full


def auto_gain(sound_id, voice_lufs, offset_db=0.0):
    """The gain that puts this recording's hit at its role target under a
    voice at voice_lufs (plus a bounded template offset), on a 0.5 dB grid.
    None when the recording has no measured hit loudness."""
    k, hp = hit_level(sound_id)
    if k is None or voice_lufs is None:
        return None
    sp = spec(sound_id)
    v = float(voice_lufs)
    if sp.get("band") == "hp150" and hp is not None:
        g = v + sp["target"] - hp
        g = min(g, v + sp["cap"] - k)          # the sub may not exceed the cap
    else:
        g = v + sp["target"] - k
    g += max(-6.0, min(2.0, float(offset_db or 0.0)))
    g = min(AUTO_GAIN_MAX_DB, max(AUTO_GAIN_MIN_DB, g))
    return round(round(g / GAIN_STEP_DB) * GAIN_STEP_DB, 1)


def judge_level(sound_id, gain_db, voice_lufs):
    """('inaudible' | 'too_hot' | None, plain sentence) for a recording at
    this gain under this voice."""
    got = _judged(sound_id, gain_db, voice_lufs)
    if got is None:
        return None, ""
    rel, full = got
    sp = spec(sound_id)
    low = sp.get("band") == "hp150"
    audible = full if low else rel
    if audible < sp["lo"]:
        return "inaudible", (
            f"{-audible:.0f} dB under the voice — masked, it will not be "
            f"heard (its role sits {-(sp['cap'] if low else sp['target']):.0f}"
            " dB under)")
    if rel > sp["hi"]:
        return "too_hot", (
            f"{_rel_words(rel)} the voice{' above 150 Hz' if low else ''} — "
            "it competes with the words (its role sits "
            f"{-sp['target']:.0f} dB under{' there' if low else ''})")
    if low and full > sp["cap_hot"]:
        return "too_hot", (
            f"its sub hit is {_rel_words(full)} the voice — trailer-heavy "
            f"under speech (the whole hit sits at most {-sp['cap']:.0f} dB "
            "under)")
    return None, ""


def _rel_words(rel):
    if rel >= 0.5:
        return f"{rel:.0f} dB over"
    if rel <= -0.5:
        return f"{-rel:.0f} dB under"
    return "level with"


def describe(sound_id, gain_db, voice):
    """'sits 8 dB under the voice (voice -20.1 LUFS short-term at the hit,
    after the dialogue leveller)'."""
    v = (voice or {}).get("lufs")
    got = _judged(sound_id, gain_db, v)
    if got is None:
        return ""
    rel, full = got
    if spec(sound_id).get("band") == "hp150":
        where = (f"sits {_rel_words(full)} the voice "
                 f"({_rel_words(rel)} it above 150 Hz)")
    else:
        where = f"sits {_rel_words(rel)} the voice"
    return f"{where} ({voice_note(voice)})"


def voice_note(voice):
    voice = voice or {}
    if voice.get("measured"):
        how = ("after the dialogue leveller" if voice.get("leveled")
               else "measured")
        return f"voice {voice['lufs']:.1f} LUFS short-term at the hit, {how}"
    return (f"voice not measured here ({voice.get('why') or 'no media'}): "
            f"levelled against the nominal {NOMINAL_VOICE_LUFS:.0f} LUFS "
            "dialogue")


def nominal(why):
    return {"lufs": NOMINAL_VOICE_LUFS, "measured": False, "leveled": False,
            "why": why}


# ── measuring a recording (offline; pinned by tests) ──────────────────────

def measure_recording(path, ffmpeg="ffmpeg"):
    """(hit_lufs, hit_lufs_hp150) of an audio file: the K-weighted loudness
    of its loudest HIT_WINDOW_S, full band and above HP_HZ."""
    import numpy as np

    def loudest(pre):
        chain = ",".join(x for x in (
            "aformat=sample_fmts=flt:sample_rates=48000:"
            "channel_layouts=stereo", pre, K_WEIGHT) if x)
        raw = subprocess.run(
            [ffmpeg, "-v", "error", "-i", path, "-af", chain,
             "-f", "f32le", "-acodec", "pcm_f32le", "-"],
            capture_output=True, check=True).stdout
        x = np.frombuffer(raw, dtype=np.float32).astype(np.float64)
        x = x.reshape(-1, 2)
        n = int(48000 * HIT_WINDOW_S)
        power = (x ** 2).sum(axis=1)
        if len(power) < n:
            power = np.pad(power, (0, n - len(power)))
        c = np.concatenate([[0.0], np.cumsum(power)])
        mean = (c[n:] - c[:-n]) / n
        return -0.691 + 10.0 * math.log10(max(float(mean.max()), 1e-12))

    return round(loudest(""), 1), round(loudest(HP_FILTER), 1)


# ── measuring the voice at a hit ──────────────────────────────────────────

def _lufs(values):
    return 10.0 * math.log10(sum(10.0 ** (v / 10.0) for v in values)
                             / len(values))


def _pieces(edl, a, b):
    """[(program_start, program_len, src_start | None, src_len)] covering
    program [a, b] in program order: each constant-rate piece of the kept
    footage (a speed span plays src_len of source in program_len), inserts
    as silence (None)."""
    from timeline import Timeline
    tl = Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                  edl.get("speed") or [])
    out = []
    for pcs, off in zip(tl.pieces, tl.offsets):
        t = off
        for ps, pe, f in pcs:
            plen = (pe - ps) / f
            s, e = max(a, t), min(b, t + plen)
            if e - s >= 0.02:
                out.append((s, e - s, ps + (s - t) * f, (e - s) * f))
            elif e - s > 1e-3:
                # a sliver plays as silence so the concat keeps its clock
                out.append((s, e - s, None, e - s))
            t += plen
    for start, dur in tl.insert_positions():
        s, e = max(a, start), min(b, start + dur)
        if e - s > 1e-3:
            out.append((s, e - s, None, e - s))
    return sorted(out, key=lambda p: p[0])


def _atempo(rate):
    """An atempo chain for a playback rate (each stage 0.5-2.0), or ''."""
    if abs(rate - 1.0) < 0.01:
        return ""
    stages = []
    while rate > 2.0:
        stages.append(2.0)
        rate /= 2.0
    while rate < 0.5:
        stages.append(0.5)
        rate /= 0.5
    stages.append(rate)
    return "".join(f"atempo={r:.5f}," for r in stages)


def _ffmpeg_bin():
    return shutil.which("ffmpeg") or "ffmpeg"


def _probe_blocks(media, pieces, timeout=60):
    """[(program_t, momentary_lufs)] over the pieces, concatenated in order,
    or None when the measurement failed."""
    if not pieces or len(pieces) > MAX_PIECES:
        return None
    args, chains = [], []
    for k, (_t, dur, src, src_len) in enumerate(pieces):
        tempo = ""
        if src is None:
            args += ["-f", "lavfi", "-t", f"{dur:.3f}",
                     "-i", "anullsrc=r=48000:cl=stereo"]
        else:
            # a speed span plays src_len of source in dur of program
            args += ["-ss", f"{src:.3f}", "-t", f"{src_len:.3f}", "-i", media]
            tempo = _atempo(src_len / dur)
        chains.append(f"[{k}:a]aresample=48000,aformat=sample_fmts=fltp:"
                      f"channel_layouts=stereo,{tempo}apad=whole_dur={dur:.3f},"
                      f"atrim=end={dur:.3f}[p{k}]")
    tmp = tempfile.mkdtemp(prefix="sfxmix-")
    path = os.path.join(tmp, "m.txt")
    try:
        graph = (";".join(chains) + ";"
                 + "".join(f"[p{k}]" for k in range(len(pieces)))
                 + f"concat=n={len(pieces)}:v=0:a=1,"
                 + dialogue_level.probe_chain(path) + "[m]")
        cmd = [_ffmpeg_bin(), "-hide_banner", "-nostats", "-v", "error",
               *args, "-filter_complex", graph, "-map", "[m]",
               "-f", "null", "-"]
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
        if proc.returncode != 0 or not os.path.exists(path):
            return None
        with open(path, encoding="utf-8", errors="replace") as fh:
            blocks = dialogue_level.parse_probe(fh.read())
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    t0 = pieces[0][0]
    return [(round(t0 + t, 3), m) for t, m in blocks]


def _volume_db(edl, tl, hit):
    """The user's volume automation at a program second (source-time
    spans), as the renderer applies it to the program audio."""
    src = tl.out_to_src(hit)
    if src is None:
        return 0.0
    g = 0.0
    for v in edl.get("volume") or []:
        try:
            if float(v["start"]) <= src < float(v["end"]):
                g += float(v.get("gain_db") or 0.0)
        except (KeyError, TypeError, ValueError):
            continue
    return g


def voice_levels(media, edl, hits, mastered=True):
    """{hit: voice} for program seconds `hits`. A voice is
    {"lufs", "raw_lufs", "measured": True, "leveled"} measured from `media`,
    or a nominal() stand-in saying why it is not. Never raises."""
    hits = sorted({round(float(h), 3) for h in hits or []})
    out = {h: nominal("no media to measure") for h in hits}
    if not hits or not media:
        return out
    try:
        from schemas import program_duration
        from timeline import Timeline
        prog = float(program_duration(edl))
        tl = Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                      edl.get("speed") or [])
    except Exception:          # noqa: BLE001
        return out
    # Hits close together share one measurement.
    groups, cur = [], []
    for h in hits:
        if cur and h - cur[0] > 2 * CONTEXT_S:
            groups.append(cur)
            cur = []
        cur.append(h)
    if cur:
        groups.append(cur)
    for grp in groups:
        a = max(0.0, grp[0] - CONTEXT_S)
        b = min(prog, grp[-1] + CONTEXT_S)
        try:
            blocks = _probe_blocks(media, _pieces(edl, a, b))
        except Exception:      # noqa: BLE001
            blocks = None
        if not blocks:
            for h in grp:
                out[h] = nominal("the program audio could not be measured")
            continue
        audible = [m for _t, m in blocks if m > dialogue_level.ABS_GATE_LUFS]
        if not audible:
            for h in grp:
                out[h] = nominal("no speech in the program audio here")
            continue
        gate = max(dialogue_level.ABS_GATE_LUFS,
                   _lufs(audible) - dialogue_level.REL_GATE_LU)
        speech = [(t, m) for t, m in blocks if m > gate]
        context = _lufs([m for _t, m in speech]) if speech else None
        for h in grp:
            near = [m for t, m in speech
                    if abs(t - h) <= VOICE_WINDOW_S / 2 + 1e-6]
            if len(near) < MIN_SPEECH_BLOCKS:
                out[h] = nominal("no speech within 1.5 s of the hit")
                continue
            local = _lufs(near)
            lev = local
            if mastered and context is not None:
                g = (dialogue_level.TARGET_LUFS - context
                     + dialogue_level.STRENGTH * (context - local))
                lev = local + min(dialogue_level.MAX_GAIN_DB,
                                  max(dialogue_level.MIN_GAIN_DB, g))
            lev += _volume_db(edl, tl, h)
            out[h] = {"lufs": round(lev, 1), "raw_lufs": round(local, 1),
                      "measured": True, "leveled": bool(mastered)}
    return out


def media_for(ctx):
    """A local/remote audio source for the main program, or (None, why)."""
    if not getattr(ctx, "has_main_video", True):
        return None, "this program has no main video"
    fn = getattr(ctx, "proxy_path", None)
    if not callable(fn):
        return None, "no media in this context"
    try:
        path = fn()
    except Exception as e:      # noqa: BLE001
        return None, f"no proxy here ({str(e)[:60]})"
    return (path, "") if path else (None, "no proxy here")


def is_mastered(ctx, edl):
    try:
        from schemas import master_loudness
        video = (getattr(ctx, "index", None) or {}).get("video") or {}
        return master_loudness(edl, video.get("width"),
                               video.get("height")) == "social"
    except Exception:          # noqa: BLE001
        return True


def voices_for(ctx, edl, hits):
    """voice_levels for a tool context: its proxy, its mastering. Nothing to
    measure (no hits) never touches the proxy — it may be a download."""
    if not hits:
        return {}
    media, why = media_for(ctx)
    if not media:
        return {round(float(h), 3): nominal(why) for h in hits or []}
    return voice_levels(media, edl, hits, mastered=is_mastered(ctx, edl))


def voice_at(voices, hit):
    return (voices or {}).get(round(float(hit), 3)) or nominal("not measured")


def level_line(sound_id, gain_db, voice, auto):
    """One MIX sentence for a write result."""
    rel = describe(sound_id, gain_db, voice)
    if not rel:
        return ""
    kind, why = judge_level(sound_id, gain_db, (voice or {}).get("lufs"))
    head = (f"MIX: {sound_id} at {gain_db:+g} dB "
            + ("(levelled automatically) " if auto else "(the gain set) ")
            + rel + ".")
    if kind:
        head += f" CHECK: {why}"
        target = auto_gain(sound_id, (voice or {}).get("lufs"))
        if not auto and target is not None:
            head += f"; its role level here is {target:+g} dB"
        head += "."
    return head


def cue_gain(sound_id, voice, declared_db):
    """A template-owned cue's gain: the role level against the voice, with
    the template's own relative intent kept as a bounded offset (its declared
    gain_db against the recording's manifest gain_db, the scale the
    templates were authored on). None when the recording is unmeasured."""
    base = (sound_library.get(sound_id) or {}).get("gain_db")
    off = (float(declared_db) - float(base)
           if declared_db is not None and base is not None else 0.0)
    return auto_gain(sound_id, (voice or {}).get("lufs"), offset_db=off)
