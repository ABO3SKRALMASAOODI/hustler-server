"""The film's sound for a local render: the spine in programme order plus each placed sound, with
its peak on its event, mastered by master() here (the voice levelled as the renderer levels it,
the sounds on it at their own gains, -14 LUFS, true peak under -1 dBTP, no pumping). ffmpeg and
numpy only; the sound design that places the sounds is score/sound.py, on the plan's side.

The product's audio master is ``renderer.render_edl(audio_only=True)`` (DESIGN 4.8): a caller that
has one hands its file to ``run.render_plan(audio=path)`` and nothing here runs. This mixer serves
the owner's workflows on the Mac and the proofs, where there is no EDL render beside the engine.
"""
import os
import re
import subprocess

import numpy as np

from .faults import SceneFault

FF = os.environ.get("FFMPEG_BIN", "ffmpeg")
SOUNDS = os.environ.get("VALMERA_SOUNDS") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sound_library")
SR = 48000
TARGET_LUFS = -14.0                  # the delivered master
CEIL_DBTP, LIMIT_DBFS = -1.0, -1.6   # never over -1 dBTP after the encode; the limiter starts at -1.6 dBFS
if os.environ.get("SCENE_CEIL_DBTP"):  # a caller whose film is encoded once more after this master (the workflow shell's brand
    CEIL_DBTP = float(os.environ["SCENE_CEIL_DBTP"])       # finish joins the end card) leaves the second encode its overshoot
    LIMIT_DBFS = CEIL_DBTP - 0.6


def pcm(path, seconds=None):
    """Any file to stereo float at the mix rate. Empty when it has no audio."""
    cmd = [FF, "-hide_banner", "-nostdin", "-loglevel", "error", "-i", path, "-map", "0:a:0?", "-vn", "-ac", "2", "-ar", str(SR)]
    if seconds:
        cmd += ["-t", f"{seconds:.3f}"]
    cmd += ["-f", "f32le", "pipe:1"]
    r = subprocess.run(cmd, capture_output=True, timeout=600)
    return np.frombuffer(r.stdout, np.float32).reshape(-1, 2).copy()


def voice_layer(dry, s, fps):
    """A sound made from the film's own recorded audio. echo: the last moment before the event throws
    back into the gap after it, side to side, darker each time. swell: the moment after it, reversed,
    rising into the event. Returns (first sample, samples)."""
    at = int(round(s["event_f"] / fps * SR))
    d = max(int(float(s.get("d_s", 0.7)) * SR), 64)
    gain = 10 ** (float(s.get("db", -8)) / 20)
    if s["s"] == "swell":
        seg = dry[at:at + d][::-1].copy()
        if not len(seg):
            return at, seg
        seg *= (np.linspace(0, 1, len(seg), dtype=np.float32) ** 2)[:, None]
        return at - len(seg), seg * gain
    seg = dry[max(0, at - d):at].copy()
    if not len(seg):
        return at, seg
    fade = min(int(0.02 * SR), len(seg) // 2)
    seg[:fade] *= np.linspace(0, 1, fade, dtype=np.float32)[:, None]
    seg[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)[:, None]
    n, gap = max(int(s.get("n", 3)), 1), int(float(s.get("gap_s", 0.26)) * SR)
    out = np.zeros((len(seg) + gap * n, 2), np.float32)
    tap = seg
    for k in range(1, n + 1):
        kern = np.ones(1 + 6 * k, np.float32) / (1 + 6 * k)     # darker each throw, as a tape echo
        wet = np.stack([np.convolve(tap[:, c], kern, mode="same") for c in (0, 1)], axis=1) * (0.62 ** (k - 1))
        wet[:, 0] *= 1.0 if k % 2 else 0.45
        wet[:, 1] *= 0.45 if k % 2 else 1.0
        o = gap * (k - 1)
        out[o:o + len(wet)] += wet
    return at, out * gain


# SND3 (14 Nov): music and sound effects as one mix. A hero hit (the hook, the turn, the close: a placed sound with
# duck_db) gets room in the song under it: the song starts down DUCK_LEAD_S before the hit, sits under it through its
# body and comes back over DUCK_OUT_S. How far is read from the signal, never fixed: deep enough that the hit stands
# DUCK_OVER_DB over the song in the hit's first 150 ms, at least DUCK_MIN_DB, at most the sound's duck_db. Where the song
# makes its own room (its level in the 0.15 s before the hit DUCK_HOLE_DB or more under its level just after: the music
# engine's hole before its drop and its last hit) the song's landing and the hit are one, and the landing is only eased
# when it would cover the hit.
DUCK_LEAD_S, DUCK_IN_S, DUCK_HOLD_S, DUCK_OUT_S = 0.15, 0.06, 0.12, 0.35
DUCK_OVER_DB, DUCK_MIN_DB, DUCK_HOLE_DB = 10.0, 3.0, 8.0
# On the song's own landing (its drop, its last hit) the two are one, but the hit leads: when the landing would still
# cover the hit, the landing is eased until the hit stands DUCK_LANDING_OVER_DB over it, by DUCK_LANDING_DB at most.
DUCK_LANDING_OVER_DB, DUCK_LANDING_DB = 3.0, 4.0


class Sounds(np.ndarray):
    """The sounds track split_audio lays: a plain array to every caller, that remembers what it held (`own`) and
    where a hero hit asks for room (`ducks`: [(sample, most dB)]). Whatever a caller lays on it afterwards (a song,
    music.mix) is the bed, and master() dips that bed under the hero hits."""
    own = None
    ducks = None


def duck_curve(bed, own, ducks, n):
    """Per-sample gain for the bed (stereo arrays at SR) and a report: each hero hit's dip, read from the signal."""
    g_db = np.zeros(n, np.float32)
    rep = []
    mono_b = bed.mean(axis=1) if bed.ndim == 2 else bed
    mono_s = own.mean(axis=1) if own.ndim == 2 else own

    def lv(x, a, b):
        a, b = max(0, int(a)), min(len(x), int(b))
        return 10 * np.log10(float(np.mean(x[a:b].astype(np.float64) ** 2)) + 1e-12) if b > a else -120.0
    for at, most in ducks:
        if at <= 0 or at >= n:
            continue
        pre = lv(mono_b, at - DUCK_LEAD_S * SR, at)
        post = lv(mono_b, at, at + 0.3 * SR)
        bed_hit = lv(mono_b, at, at + 0.15 * SR)
        hit = lv(mono_s, at, at + 0.15 * SR)
        row = {"t": round(at / SR, 3), "bed_db": round(bed_hit, 1), "hit_over_bed_db": round(hit - bed_hit, 1)}
        if bed_hit < -70.0:
            rep.append(dict(row, db=0.0, why="no song under it"))
            continue
        if post - pre >= DUCK_HOLE_DB:
            depth = float(min(float(most), DUCK_LANDING_DB, DUCK_LANDING_OVER_DB - (hit - bed_hit)))
            if depth < 0.5:
                rep.append(dict(row, db=0.0, why="the song makes its own room: a hole, then its landing with the hit"))
                continue
            why = "the song's own landing with the hit, eased so the hit leads it"
        else:
            depth = float(min(float(most), max(DUCK_MIN_DB, DUCK_OVER_DB - (hit - bed_hit))))
            why = None
        t = (np.arange(n) - at) / SR
        down = np.clip((t + DUCK_LEAD_S) / DUCK_IN_S, 0.0, 1.0)
        up = np.clip(1.0 - (t - DUCK_HOLD_S) / DUCK_OUT_S, 0.0, 1.0)
        env = np.where(t < DUCK_HOLD_S, down, up) * (t > -DUCK_LEAD_S)
        g_db = np.minimum(g_db, (-depth * env).astype(np.float32))
        rep.append(dict(row, db=round(depth, 1), **({"why": why} if why else {})))
    return (10 ** (g_db / 20)).astype(np.float32), rep


def mix_audio(plan):
    """The spine in programme order, plus each sound with its peak on its event. Sample exact."""
    voice, fx, report = split_audio(plan)
    return voice + fx, report


def split_audio(plan):
    """(voice, sounds, report): the spine, and every placed sound on its own track, so the master levels the
    voice before the sounds are laid on it."""
    n = int(round(plan.dur / plan.fps * SR))
    mix = np.zeros((n, 2), np.float32)
    spine = plan.doc.get("spine") or []
    report = dict(spine=len(spine), sounds=0, skipped=[])
    if spine and plan.assets.get("main"):
        src = pcm(plan.assets["main"]["abs"])
        fade = int(0.003 * SR)
        ends = {}
        for e in spine:
            a, b = e["f"]
            at = int(round(a / plan.fps * SR))
            to = min(int(round(b / plan.fps * SR)), n)
            s0 = int(round(e["src"][0] * SR))
            seg = src[s0:s0 + (to - at)].copy()
            if len(seg) == 0:
                continue
            joined_before = ends.get(a) == round(e["src"][0], 3)
            ends[b] = round(e["src"][1], 3)
            if not joined_before and len(seg) > 2 * fade:
                seg[:fade] *= np.linspace(0, 1, fade, dtype=np.float32)[:, None]
            nxt = next((x for x in spine if x["f"][0] == b and abs(x["src"][0] - e["src"][1]) < 0.002), None)
            if nxt is None and len(seg) > 2 * fade:
                seg[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)[:, None]
            mix[at:at + len(seg)] += seg
    cache = {}
    dry = mix.copy()
    voice, mix = mix, np.zeros((n, 2), np.float32)
    ducks = []
    for s in plan.doc.get("sound") or []:
        if s.get("s") in ("echo", "swell"):
            at, smp = voice_layer(dry, s, plan.fps)
            if at < 0:
                smp, at = smp[-at:], 0
            smp = smp[:max(n - at, 0)]
            mix[at:at + len(smp)] += smp
            report["sounds"] += 1
            continue
        f = s.get("file") or (s.get("s", "") + ".flac")
        p = os.path.join(SOUNDS, os.path.basename(f))
        if not os.path.exists(p):
            report["skipped"].append(f)
            continue
        if p not in cache:
            cache[p] = pcm(p)
        smp = cache[p][int(round(float(s.get("trim_s", 0)) * SR)):]
        at = int(round(float(s["start_s"]) * SR))
        if at >= n or len(smp) == 0:
            report["skipped"].append(s.get("id"))
            continue
        if s.get("dur_s"):                     # the measured audible tail: stopped there with a 0.12 s fade, as the renderer does
            span = int(round(float(s["dur_s"]) * SR))
            smp = smp[:span].copy()
            fade = min(int(0.12 * SR), max(1, len(smp) // 3))
            smp[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)[:, None]
        smp = smp[:n - at] * (10 ** (float(s.get("db", 0)) / 20))
        mix[at:at + len(smp)] += smp
        report["sounds"] += 1
        if s.get("duck_db") and s.get("event_f") is not None:
            ducks.append((int(round((float(s["event_f"]) / plan.fps - 0.013) * SR)), float(s["duck_db"])))
    fx = mix.view(Sounds)
    fx.own, fx.ducks = mix.copy(), ducks
    return voice, fx, report


def write_audio(plan, out, tmp):
    voice, fx, report = split_audio(plan)
    try:
        report.update(master(voice, fx, out, tmp, plan.dur / plan.fps))
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as e:
        raise SceneFault("audio", str(e)[-400:])
    return report


def _parse_summary(t):
    """(integrated LUFS, true peak dBTP, LRA) from ebur128's summary."""
    def num(pat):
        m = re.findall(pat, t)
        try:
            return float(m[-1]) if m else None
        except ValueError:
            return None
    return num(r"I:\s+(-?[0-9.]+|-inf) LUFS"), num(r"Peak:\s+(-?[0-9.]+|-inf) dBFS"), num(r"LRA:\s+(-?[0-9.]+) LU")


def loudness_of(x, tmp):
    raw = os.path.join(tmp, "lv.f32")
    np.asarray(x, np.float32).tofile(raw)
    r = subprocess.run([FF, "-hide_banner", "-nostdin", "-nostats", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", raw,
                        "-af", "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True, timeout=600)
    os.remove(raw)
    return _parse_summary(r.stderr)


def level_voice(voice, tmp):
    """The voice levelled as the renderer levels it (dialogue_level: speech to -20 LUFS, a slow curve that holds
    across pauses, slew-limited), after a 70 Hz high-pass. Returns (voice, report)."""
    x = np.asarray(voice, np.float32)
    raw = os.path.join(tmp, "voice.f32")
    x.tofile(raw)
    hp = os.path.join(tmp, "voice_hp.f32")
    r = subprocess.run([FF, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "2",
                        "-i", raw, "-af", "highpass=f=70:poles=2", "-f", "f32le", hp], capture_output=True, timeout=600)
    if r.returncode == 0 and os.path.getsize(hp) == x.nbytes:
        x = np.fromfile(hp, np.float32).reshape(-1, 2)
    meta = os.path.join(tmp, "voice_m.txt")
    x.tofile(raw)
    subprocess.run([FF, "-hide_banner", "-nostdin", "-nostats", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", raw, "-af",
                    f"ebur128=metadata=1,ametadata=mode=print:key=lavfi.r128.M:file='{meta}'", "-f", "null", "-"],
                   capture_output=True, timeout=600)
    rep = {"curve": False}
    try:
        import dialogue_level as DL
        blocks = DL.parse_probe(open(meta).read())
        knots = DL.gain_knots(blocks)
    except (ImportError, OSError):
        knots = None
    for p in (raw, hp, meta):
        if os.path.exists(p):
            os.remove(p)
    if knots:
        kt = np.array([k[0] for k in knots], np.float64)
        kg = np.array([k[1] for k in knots], np.float64)
        t = np.arange(len(x)) / SR
        g = np.interp(t, kt, kg)
        x = x * (10 ** (g / 20)).astype(np.float32)[:, None]
        rep = {"curve": True, "knots": len(knots), "gain_db": [round(float(kg.min()), 1), round(float(kg.max()), 1)]}
    return x, rep


def master(voice, fx, out, tmp, dur, bitrate="256k", bed=None, ducks=None):
    """Voice levelled, sounds laid on it at their own gains, the whole to -14 LUFS by one gain, true peaks held
    under -1 dBTP by a 4x oversampled limiter (5 ms look-ahead, 50 ms release: it touches transients, never the
    voice's level), AAC. Measured after the encode: the limit comes down while the true peak is over -1 dBTP, the
    gain comes back while the limiter's work leaves the film under -14.2 LUFS, five passes at most. Returns the report.
    A song is dipped under each hero hit (duck_curve): handed apart as `bed` (fx then holds the sounds alone, ducks
    the hits' [(sample, dB)]), or laid by the caller onto split_audio's own track, which remembers what it held."""
    own = getattr(fx, "own", None)
    ducks = ducks if ducks is not None else getattr(fx, "ducks", None)
    fx = np.asarray(fx, np.float32)
    duck_rep = None
    if bed is not None:
        bed = np.asarray(bed, np.float32)
        m = min(len(bed), len(fx))
        own, fx = fx, fx.copy()
        fx[:m] += bed[:m]
    if ducks and own is not None and own.shape == fx.shape:
        laid = fx - own                                     # what the caller laid on the sounds: the song
        if float(np.abs(laid).max()) > 1e-6:
            g, duck_rep = duck_curve(laid, own, ducks, len(fx))
            fx = own + laid * g[:, None]
    v, rep = level_voice(voice, tmp) if voice is not None and np.abs(voice).max() > 1e-5 else (voice, {"curve": False})
    if duck_rep is not None:
        rep = dict(rep, ducks=duck_rep)
    mix = (v if v is not None else 0) + fx
    mix = np.asarray(mix, np.float32)
    if float(np.abs(mix).max()) < 1e-5:
        raw = os.path.join(tmp, "mix.f32")
        mix.tofile(raw)
        subprocess.run([FF, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "2",
                        "-i", raw, "-c:a", "aac", "-b:a", "128k", out], check=True)
        return dict(rep, lufs=None)
    i, _tp, _ = loudness_of(mix, tmp)
    g = TARGET_LUFS - i if i is not None and i > -70 else 0.0
    raw = os.path.join(tmp, "mix.f32")
    mix.tofile(raw)
    limit = LIMIT_DBFS
    got = (None, None, None)
    used = g
    for _ in range(5):
        lin = max(0.0625, min(1.0, 10 ** (limit / 20)))
        used = g
        af = (f"volume={g:.3f}dB,aresample=192000,alimiter=limit={lin:.5f}:attack=5:release=50:level=0:latency=1,aresample={SR},"
              f"aformat=sample_fmts=fltp:channel_layouts=stereo,apad=whole_dur={dur:.6f},atrim=0:{dur:.6f}")
        r = subprocess.run([FF, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "2",
                            "-i", raw, "-af", af, "-c:a", "aac", "-b:a", bitrate, out], capture_output=True, text=True, timeout=600)
        if r.returncode:
            raise RuntimeError(f"master encode failed: {r.stderr[-300:]}")
        m = subprocess.run([FF, "-hide_banner", "-nostdin", "-nostats", "-i", out, "-af", "ebur128=peak=true", "-f", "null", "-"],
                           capture_output=True, text=True, timeout=600)
        got = _parse_summary(m.stderr)
        if got[0] is None or got[1] is None:
            break
        if got[1] > CEIL_DBTP:                       # the codec's overshoot rides on the limited level: lower it
            limit -= max(0.25, got[1] - (CEIL_DBTP - 0.5))
        elif abs(got[0] - TARGET_LUFS) > 0.2:        # the limiter took loudness with the peaks: give it back by one gain
            g += TARGET_LUFS - got[0]
        else:
            break
    os.remove(raw)
    return dict(rep, lufs_in=round(i, 2) if i is not None else None, lufs=got[0], tp=got[1], lra=got[2], limit_dbfs=round(limit, 2),
                gain_db=round(used, 2))


def mux(video, audio, out):
    r = subprocess.run([FF, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", video, "-i", audio,
                        "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", "-movflags", "+faststart", out], capture_output=True, text=True)
    if r.returncode:
        raise SceneFault("audio", f"mux failed: {r.stderr[-300:]}")
