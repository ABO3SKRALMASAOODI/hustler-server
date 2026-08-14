"""Deterministic audio QC over a finished render — taste.py for the ears.

Round 98. Every existing audit reads the EDL or the frames; nothing ever
measured the SOUND that actually shipped, and sound is half of why an edit
reads as amateur: a mix mastered 8dB quiet, a true peak that platform
limiters will crush, three seconds of dead air where a cut dropped the
room tone. All of it is measurable in one ffmpeg pass over the file the
executor is already holding, so the render result can state it as fact.

Same contract as taste.critique: pure measurement in, plain-language
findings out, never a hard block — the agent fixes them or keeps one
deliberately and says why. Runs on the EXECUTOR (renderer.py) where the
rendered file is local; the dispatcher only ever reads the numbers.

One decode serves everything: silencedetect logs gaps to stderr while
loudnorm (print_format=json) logs integrated loudness / true peak / LRA.
Failure returns None — a render never fails over its own review.
"""

import json
import math
import re
import subprocess

# The social/streaming loudness target the renderer itself masters to when
# asked (renderer loudnorm I=-14:TP=-2.0 plus a hard ceiling). QC judges
# against the same
# numbers so the check and the fix can never disagree.
TARGET_I = -14.0
TARGET_TP = -2.0

# Louder/quieter than this from target = worth a finding. ±4 LU is far past
# taste differences — it is "phone speakers at max still quiet" territory.
LOUDNESS_SLACK_LU = 4.0

# Internal silence long enough to read as dead air, and the head/tail slack
# where silence is normal (fades, a breath before the first word).
DEAD_AIR_MIN_S = 1.5
EDGE_SLACK_S = 1.0

_SIL_START = re.compile(r"silence_start:\s*([0-9.]+)")
_SIL_END = re.compile(r"silence_end:\s*([0-9.]+)")


def _run_ffmpeg(path):
    """stderr of the single analysis pass, or None."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", path,
           "-map", "0:a:0", "-af",
           "silencedetect=n=-50dB:d=1.0,"
           "loudnorm=I=-14:TP=-2.0:LRA=11:print_format=json",
           "-f", "null", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=180)
    except Exception:
        return None
    return (proc.stderr or b"").decode("utf-8", "replace")


def _parse(stderr_text):
    """(loudnorm dict or None, [(silence_start, silence_end)])."""
    loud = None
    # loudnorm prints its JSON as the last {...} block on stderr.
    blocks = re.findall(r"\{[^{}]+\}", stderr_text)
    for b in reversed(blocks):
        if "input_i" in b:
            try:
                loud = json.loads(b)
                break
            except Exception:
                pass
    silences, start = [], None
    for line in stderr_text.splitlines():
        m = _SIL_START.search(line)
        if m:
            start = float(m.group(1))
            continue
        m = _SIL_END.search(line)
        if m and start is not None:
            silences.append((start, float(m.group(1))))
            start = None
    return loud, silences


def measure(path, duration_s=None):
    """The QC record for one rendered file, or None when analysis failed.

    {"i": integrated LUFS, "tp": true peak dBTP, "lra": loudness range,
     "silences": [[s, e], ...] internal dead-air spans,
     "findings": [plain-language strings, worst first]}
    A file with no audio stream returns findings saying exactly that.
    """
    err = _run_ffmpeg(path)
    if err is None:
        return None
    if "Stream map '0:a:0' matches no streams" in err \
            or "does not contain any stream" in err:
        return {"i": None, "tp": None, "lra": None, "silences": [],
                "findings": ["the program has NO audio track at all — "
                             "if that is not deliberate, the sound was "
                             "lost somewhere and the user will notice "
                             "before you do"]}
    loud, silences = _parse(err)
    findings = []
    i = tp = lra = None
    if loud:
        try:
            i = float(loud.get("input_i"))
            tp = float(loud.get("input_tp"))
            lra = float(loud.get("input_lra"))
        except (TypeError, ValueError):
            i = tp = lra = None
        # ffmpeg reports a perfectly silent program as ``-inf``. Python's
        # JSON encoder accepts that extension, but PostgreSQL JSON correctly
        # rejects it; the completed render then gets retried even though its
        # bytes are already valid. Preserve the useful silence verdict with a
        # finite sentinel and omit the measurements that have no finite
        # meaning. The terminal DB boundary also sanitizes defensively, but
        # keeping this record finite makes the agent's audio review truthful.
        if i is not None and not math.isfinite(i):
            i = -100.0 if i < 0 else None
        if tp is not None and not math.isfinite(tp):
            tp = None
        if lra is not None and not math.isfinite(lra):
            lra = None
    if i is not None and i < -70.0:
        findings.append("the mix is essentially SILENT (integrated "
                        f"{i:.1f} LUFS) — every audio layer is muted or "
                        "missing")
    elif i is not None:
        if i < TARGET_I - LOUDNESS_SLACK_LU:
            findings.append(
                f"the whole mix is quiet: {i:.1f} LUFS against the "
                f"{TARGET_I:.0f} social target — set_master_loudness "
                "fixes it in one call")
        elif i > TARGET_I + LOUDNESS_SLACK_LU:
            findings.append(
                f"the mix is hot: {i:.1f} LUFS against the "
                f"{TARGET_I:.0f} social target — platforms will turn it "
                "down and squash it; set_master_loudness fixes it")
        if tp is not None and tp > -0.3:
            findings.append(
                f"true peak {tp:.1f} dBTP — effectively clipping; "
                "upload transcodes will distort the loud moments "
                "(set_master_loudness caps it at -2.0)")
    dead = []
    dur = float(duration_s or 0.0)
    for s, e in silences:
        if e - s < DEAD_AIR_MIN_S:
            continue
        if s <= EDGE_SLACK_S:
            continue
        if dur and e >= dur - EDGE_SLACK_S:
            continue
        dead.append([round(s, 2), round(e, 2)])
    for s, e in dead[:3]:
        findings.append(
            f"dead air: {s:.1f}-{e:.1f}s is silent for {e - s:.1f}s — "
            "tighten the cut, or let music/room tone cover it if the "
            "pause is deliberate")
    return {"i": i, "tp": tp, "lra": lra, "silences": dead,
            "findings": findings}


def listen_windows(verify_times, duration, max_windows=2, halo_s=2.5,
                   max_len_s=8.0):
    """The spans worth LISTENING to after a render: a halo around each
    changed moment (the same verify_times the proof frames use), merged and
    clamped. When there are more candidates than the audio model can accept,
    spread them across the *whole finished mix* instead of taking the first
    N — the old behavior could hear three early SFX and miss a broken second
    half completely. Returns [(t0, t1)]."""
    dur = max(0.0, float(duration or 0.0))
    max_windows = max(0, int(max_windows or 0))
    if dur <= 0.2 or max_windows == 0:
        return []
    times = sorted(float(t) for t in (verify_times or [])
                   if 0.0 <= float(t) <= dur)
    if not times:
        return [(0.0, min(6.0, dur))]
    wins = []
    for t in times:
        s, e = max(0.0, t - halo_s), min(dur, t + halo_s)
        if wins and s <= wins[-1][1] + 0.5:
            wins[-1] = (wins[-1][0], e)
        else:
            wins.append((s, e))
    if len(wins) > max_windows:
        indexes = sorted({round(i * (len(wins) - 1) / (max_windows - 1))
                          for i in range(max_windows)}) \
            if max_windows > 1 else [len(wins) // 2]
        wins = [wins[i] for i in indexes]
    out = []
    for s, e in wins:
        out.append((round(s, 2), round(min(e, s + max_len_s), 2)))
    return out


def summary_line(qc):
    """One compact measurement line for the render note, or ''."""
    if not qc or qc.get("i") is None:
        return ""
    tp = qc.get("tp")
    return (f" Mix measured: {qc['i']:.1f} LUFS integrated"
            + (f", true peak {tp:.1f} dBTP" if tp is not None else "")
            + ".")
