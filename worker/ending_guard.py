"""A narrated cut ends on a complete sentence over a live picture (Q6, 30 Oct 2026).

The explainer fixture (u4899, a narrated 3D animation of a pipe repair) was
judged 5.0 and 7.5 on the same code: its First cut ended on "It's connected to
the existing pipe network with a fitting piece." while the source animation
faded to an empty grey frame between two scenes, so the last second of the
program was a caption fading over nothing ("the narration stops on 'a fitting
piece' over an empty grey frame", a hard fault in one judgement, "end on the
last live shot" in every other). The sentence was complete; the picture was
not.

The guard runs after the First cut's keep is final (pauses and fillers cut,
the ends shaped) and before the muted live tail. It reads the program's last
spoken word, the sentence it belongs to, and the source picture over that
sentence's end and the live tail after it. When the sentence is complete and
the picture is live, nothing changes. Otherwise the ending moves to the
nearest sentence end that is both a complete thought and live: forward first
(the narration's own next sentence, its pauses cut like the rest), within
MAX_GROW_S and the short-form ceiling; else back to an earlier one.

"Empty" is measured, not guessed: a frame whose fine detail (mean absolute
Laplacian of a 96-px-wide luma frame, 0-255 scale) is under BLANK_DETAIL, or
whose luma is one flat colour. u4899's grey fade measures 1.4-3.1; its live
scenes 13-24; the Primus Line logo on grey 3.7-4.1; a sky with a rocket 6.7.

WHO GETS IT: the First cut's engine accounts (trial and new subscribers), for
narration over a picture without a speaker's face (explainers, screens,
vlogs over scenery). Talking heads never reach it.
"""
import subprocess

BLANK_DETAIL = 3.2          # mean |Laplacian| under this: an empty frame
BLANK_FLAT = 6.0            # luma std under this: one flat colour
PROBE_BEFORE_S = 0.3        # the picture is read from just before the last word ends
TAIL_S = 0.45               # ... to the live tail after it (first_cut_visual.TAIL_S)
NEXT_GAP_S = 0.08           # never into the next word
MAX_GROW_S = 12.0           # an ending moves forward at most this much program
PROG_CEILING_S = 62.0       # and never past this (unless the cut was longer)
MIN_PROG_S = 12.0           # an ending moves back only while this much remains
PAUSE_SPLIT_S = 0.4         # pauses inside an extension are cut like the rest
PAD_S = 0.08


def _w(w, k):
    v = w.get(k)
    if v is None:
        v = w.get({"t0": "s", "t1": "e"}[k])
    return float(v)


def frame_detail(path, a, b, fps=10.0, width=96):
    """[(t, detail, flat_std)] for the source frames in [a, b]."""
    import numpy as np
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30).stdout.strip().split(",")
        h = int(round(width * int(probe[1]) / int(probe[0])))
        h += h % 2
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{max(0.0, a):.3f}",
             "-t", f"{max(0.15, b - a):.3f}", "-i", path, "-an", "-vf",
             f"fps={fps},scale={width}:{h},format=gray", "-f", "rawvideo",
             "-pix_fmt", "gray", "-"], capture_output=True, timeout=60).stdout
    except Exception:
        return []
    n = len(raw) // (width * h)
    if n < 1:
        return []
    frames = np.frombuffer(raw[:n * width * h], np.uint8).reshape(
        n, h, width).astype(np.float32)
    out = []
    for k in range(n):
        f = frames[k]
        lap = np.abs(4 * f[1:-1, 1:-1] - f[:-2, 1:-1] - f[2:, 1:-1]
                     - f[1:-1, :-2] - f[1:-1, 2:]).mean()
        out.append((a + k / fps, float(lap), float(f.std())))
    return out


def is_blank(detail, flat):
    return detail < BLANK_DETAIL or flat < BLANK_FLAT


def live(path, a, b):
    """True when no source frame in [a, b] is empty (None when unreadable)."""
    got = frame_detail(path, a, b)
    if not got:
        return None
    return not any(is_blank(d, s) for _t, d, s in got)


def ends_thought(row):
    text = str((row or {}).get("text") or "").strip()
    return text[-1:] in ".!?" and len(text.split()) >= 3


def whole_sentences(rows):
    """The index's rows joined into whole sentences. The index caps a row at
    a length (transcribe.MAX_SENTENCE_S), so one spoken sentence can be two
    rows: u4899's "The rehabilitated pipe thus has a new service life of at
    least" + "50 years." A row that does not end on . ! or ? runs on into the
    next; the last rows without an end stay one open sentence."""
    out, cur = [], []
    for r in sorted(rows, key=lambda r: float(r["t0"])):
        cur.append(r)
        if str(r.get("text") or "").strip()[-1:] in ".!?":
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return [{"id": c[-1].get("id"), "t0": float(c[0]["t0"]),
             "t1": float(c[-1]["t1"]),
             "text": " ".join(str(x.get("text") or "").strip() for x in c)}
            for c in out]


def last_spoken(words, keep):
    """The last word whose onset is inside the program, or None."""
    spoken = [w for w in words
              if any(a - 1e-3 <= _w(w, "t0") < b for a, b in keep)]
    return spoken[-1] if spoken else None


def sentence_of(rows, t):
    for r in rows:
        if float(r["t0"]) - 1e-3 <= t < float(r["t1"]) + 0.05:
            return r
    return None


def end_at(row, words, duration):
    """Where a program ending on ``row`` stops: its last word plus the live
    tail, never into the next word or past the source."""
    t1 = float(row["t1"])
    later = [_w(w, "t0") for w in words if _w(w, "t0") >= t1 - 1e-3]
    limit = min([float(duration)] + [t - NEXT_GAP_S for t in later[:1]])
    return round(max(t1 + 0.05, min(t1 + TAIL_S, limit)), 3)


def extend(keep, words, last_t1, t_to):
    """``keep`` with its last segment run on past the last spoken word (which
    ends at ``last_t1``) to ``t_to``, the pauses over PAUSE_SPLIT_S inside the
    run-on (the one after that word included) cut out like the rest."""
    out = [list(k) for k in keep]
    inside = sorted((w for w in words if last_t1 - 0.02 <= _w(w, "t0") < t_to),
                    key=lambda w: _w(w, "t0"))
    cur = out[-1]
    prev_end = last_t1
    for w in inside:
        a, b = _w(w, "t0"), _w(w, "t1")
        if a - prev_end > PAUSE_SPLIT_S:
            cur[1] = round(max(cur[0] + 0.05, prev_end + PAD_S), 3)
            cur = [round(a - PAD_S, 3), round(b, 3)]
            out.append(cur)
        prev_end = max(prev_end, b)
        cur[1] = max(cur[1], round(b, 3))
    cur[1] = round(max(cur[1], t_to), 3)
    return out


def shorten(keep, t_end):
    """``keep`` ending at source second ``t_end``."""
    out = []
    for a, b in keep:
        if a >= t_end - 0.05:
            break
        out.append([a, min(b, t_end)])
    return out


def choose(rows, words, keep, duration, prog, is_live):
    """(new_keep, why) or (None, why): the ending decision, with ``is_live``
    (source a, b) -> bool|None the picture test."""
    if not keep or not words or not rows:
        return None, "nothing to read"
    rows = whole_sentences(rows)
    lw = last_spoken(words, keep)
    if lw is None:
        return None, "no speech in the program"
    row = sentence_of(rows, _w(lw, "t0"))
    complete = bool(row) and ends_thought(row) and _w(lw, "t1") >= float(row["t1"]) - 0.05
    e1 = float(keep[-1][1])
    probe_end = max(e1, _w(lw, "t1") + TAIL_S)
    here = is_live(max(0.0, _w(lw, "t1") - PROBE_BEFORE_S), min(duration, probe_end))
    if complete and here is not False:
        return None, "complete and live"
    start = float(keep[0][0])
    lw_t1 = _w(lw, "t1")
    forward = [r for r in rows if ends_thought(r) and float(r["t1"]) > lw_t1 + 0.05]
    for r in forward:
        stop = end_at(r, words, duration)
        grow = stop - e1
        if grow > MAX_GROW_S or prog + grow > max(PROG_CEILING_S, prog):
            break
        ok = is_live(float(r["t1"]) - PROBE_BEFORE_S, min(duration, float(r["t1"]) + TAIL_S))
        if ok:
            return extend(keep, words, lw_t1, stop), f"forward to {r.get('id')}"
    backward = [r for r in rows if ends_thought(r)
                and float(r["t1"]) <= lw_t1 + 0.05
                and (r is not row or not complete)]
    for r in reversed(backward):
        stop = end_at(r, words, duration)
        new = shorten(keep, stop)
        if not new or sum(b - a for a, b in new) < MIN_PROG_S:
            break
        ok = is_live(float(r["t1"]) - PROBE_BEFORE_S, min(duration, float(r["t1"]) + TAIL_S))
        if ok:
            return new, f"back to {r.get('id')}"
    return None, "no complete, live ending within reach"


def guard(ctx, steps, started, run):
    """Move the ending when it is incomplete or over an empty picture.
    Returns the keep_segments result or None (no change)."""
    import schemas
    try:
        edl = ctx.latest_edl()["json"]
        keep = [[float(a), float(b)] for a, b in edl.get("keep") or []]
        prog = float(schemas.program_duration(edl))
        path = ctx.proxy_path()
    except Exception:
        return None
    index = ctx.index or {}
    words = [w for w in index.get("words") or []
             if isinstance(w, dict) and w.get("w") is not None
             and (w.get("t0") is not None or w.get("s") is not None)]
    rows = [r for r in index.get("sentences") or []
            if r.get("t0") is not None and r.get("t1") is not None]
    duration = float(ctx.duration or (keep[-1][1] if keep else 0))
    new, why = choose(rows, words, keep, duration, prog,
                      lambda a, b: live(path, a, b))
    print(f"[first_cut] ending guard: {why}", flush=True)
    if not new or new == keep:
        return None
    return run(ctx, steps, "keep_segments", {"segments": new,
                                             "snap_to_words": False}, started)
