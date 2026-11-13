"""The film's sound for a local render: the spine in programme order plus each placed sound, with
its peak on its event, mastered to -14 LUFS. ffmpeg and numpy only.

The product's audio master is ``renderer.render_edl(audio_only=True)`` (DESIGN 4.8): a caller that
has one hands its file to ``run.render_plan(audio=path)`` and nothing here runs. This mixer serves
the owner's workflows on the Mac and the proofs, where there is no EDL render beside the engine.
"""
import json
import os
import subprocess

import numpy as np

from .faults import SceneFault

FF = os.environ.get("FFMPEG_BIN", "ffmpeg")
SOUNDS = os.environ.get("VALMERA_SOUNDS") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sound_library")
SR = 48000


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


def mix_audio(plan):
    """The spine in programme order, plus each sound with its peak on its event. Sample exact."""
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
        smp = smp[:n - at] * (10 ** (float(s.get("db", 0)) / 20))
        mix[at:at + len(smp)] += smp
        report["sounds"] += 1
    return mix, report


def write_audio(plan, out, tmp):
    mix, report = mix_audio(plan)
    raw = os.path.join(tmp, "mix.f32")
    mix.tofile(raw)
    dur = plan.dur / plan.fps
    src = ["-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", raw]
    if float(np.abs(mix).max()) < 1e-5:          # nothing to normalise: a silent track of the exact length
        subprocess.run([FF, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *src, "-c:a", "aac", "-b:a", "128k", out], check=True)
        report.update(lufs=None)
        return report
    target = "I=-14:TP=-1.5:LRA=11"
    m = subprocess.run([FF, "-hide_banner", "-nostdin", "-y", *src, "-af", f"loudnorm={target}:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=600)
    t = m.stderr
    try:
        j = json.loads(t[t.rindex("{"):t.rindex("}") + 1])
    except ValueError:
        raise SceneFault("audio", t[-300:])
    af = (f"loudnorm={target}:measured_I={j['input_i']}:measured_TP={j['input_tp']}:measured_LRA={j['input_lra']}:"
          f"measured_thresh={j['input_thresh']}:offset={j['target_offset']}:linear=true:print_format=json,"
          f"aresample={SR},apad=whole_dur={dur:.6f},atrim=0:{dur:.6f}")
    r = subprocess.run([FF, "-hide_banner", "-nostdin", "-y", *src, "-af", af, "-c:a", "aac", "-b:a", "256k", out],
                       capture_output=True, text=True, timeout=600)
    if r.returncode:
        raise SceneFault("audio", r.stderr[-400:])
    t = r.stderr
    j2 = json.loads(t[t.rindex("{"):t.rindex("}") + 1])
    report.update(lufs_in=float(j["input_i"]), lufs=float(j2["output_i"]), tp=float(j2["output_tp"]), norm=j2.get("normalization_type"))
    return report


def mux(video, audio, out):
    r = subprocess.run([FF, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", video, "-i", audio,
                        "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", "-movflags", "+faststart", out], capture_output=True, text=True)
    if r.returncode:
        raise SceneFault("audio", f"mux failed: {r.stderr[-300:]}")
