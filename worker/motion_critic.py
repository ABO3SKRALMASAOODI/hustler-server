"""The motion critic: judge a rendered short against the references' craft
and say exactly what to fix (track 4F; ROADMAP Wave 4 "motion critic and
the all-genre loop", worker/devices/INTERFACES_W4.md §6).

The 41 reference reels the owner rates 10/10 are made of rhythm, type in a
hierarchy, restraint and devices that land on moments. This critic reads a
render the way a demanding editor would, with numbers, and hands the agent
a ranked list of concrete fixes — each with the exact apply_edit_batch
operation when one exists — so a mid-tier model can loop: render ->
critique_short -> fix -> render.

Lenses (each finding: {code, lens, severity, t, id, message, fix, op?}):

  brand      the Valmera end card and the free-tier corner mark are there
             and unchanged (render_qc's exact-geometry correlations)
  flash      Harding-style flashes on the RENDERED frames (photosens); the
             fix is the flash-safe plan (downbeat hits kept)
  colour     black-and-white is a DEVICE, never a look: B&W picture over
             most of the short without the user asking is a finding
  rhythm     visual events (cuts, punctuation, graphic and device
             entrances) per second against the genre's reference band;
             stretches where only captions move
  words      cuts inside a word, an opening mid-sentence, a payoff that
             does not breathe
  type       the type ladder (render_qc's measured cap heights when the
             render carries them): payoff the largest lockup, captions
             readable, a real ladder
  restraint  motion_qa.audit (ledger, payoff hold, consecutive shots, one
             punctuation family per section), graphics covering most of the
             runtime, owner rules (zoom, sound, music provenance)
  faces      a graphic's measured box over a face (and its chin) on the
             rendered frames
  motion     words that land late on screen, mattes that flicker, repeated
             frames in slowed windows
  range      invention, not a house style: the same device set as the
             project's previous shorts; strong moments with nothing on
             them (<= 3 fresh, bound suggestions)

Severity: ``blocking`` (owner rules, brand, flash safety, a heard word cut
in half), ``fix`` (craft below the bar), ``advisory`` (taste). The score is
a guide, not a gate: 10 minus the weighted findings.

Browserless and model-free: ffmpeg decode + numpy (+ YuNet faces when the
lane has the 0.23 MB model, Haar otherwise). Every lane runs it.
"""

import math
import os
import re
import time

CRITIC_VERSION = 1
SEVERITIES = ("blocking", "fix", "advisory")
LENSES = ("brand", "flash", "colour", "rhythm", "words", "type",
          "restraint", "faces", "motion", "range")
_WEIGHT = {"blocking": 2.0, "fix": 0.7, "advisory": 0.2}

# Feature raster for the whole-render pass (refsig's detectors): a 96-px
# wide decode keeps a 60 s 1080x1920 final under ~100 MB on a small lane.
FEAT_W = 96

# Craft bands aggregated from the internal refbench corpus (the owner's 41
# reference reels, refsig v4 signatures, 15 Oct 2026): per genre, the
# quartiles (q1, median, q3) of visual events per second (cuts plus
# punctuation hits) and of 1-3-frame punctuation hits per second. Aggregate
# statistics only; no per-reference data is stored here.
GENRE_BANDS = {
    "A": {"events_per_s": (1.06, 1.21, 1.79), "punct_per_s": (0.0, 0.0, 0.13)},
    "B": {"events_per_s": (2.40, 2.51, 3.49), "punct_per_s": (0.0, 0.40, 1.0)},
    "C": {"events_per_s": (0.06, 0.25, 0.47), "punct_per_s": (0.0, 0.0, 0.0)},
    "D": {"events_per_s": (0.78, 0.86, 0.86), "punct_per_s": (0.02, 0.04, 0.07)},
    "E": {"events_per_s": (0.20, 0.21, 0.64), "punct_per_s": (0.02, 0.04, 0.07)},
    "F": {"events_per_s": (1.99, 2.72, 3.90), "punct_per_s": (0.52, 0.76, 0.95)},
    "G": {"events_per_s": (0.74, 1.40, 2.51), "punct_per_s": (0.0, 0.15, 0.47)},
    "H": {"events_per_s": (0.62, 0.71, 1.05), "punct_per_s": (0.08, 0.13, 0.27)},
    "I": {"events_per_s": (0.90, 1.04, 1.20), "punct_per_s": (0.0, 0.0, 0.05)},
    "J": {"events_per_s": (1.0, 1.25, 1.5), "punct_per_s": (0.2, 0.42, 0.6)},
}
# Longest stretch (s) where nothing but captions moves, per genre family:
# speech-led talking heads breathe longer; everything else is denser.
DEAD_S = {"C": 4.5, "A": 2.5, "D": 3.0, "E": 3.5, "I": 3.5}
DEAD_S_DEFAULT = 2.5
# B&W picture share above which B&W reads as a LOOK, not a device.
BW_LOOK_SHARE = 0.6
_BW_ASKED = re.compile(r"\b(black[- ]and[- ]white|b&w|b/w|monochrome|mono\b|"
                       r"greyscale|grayscale|noir|desaturat\w*)", re.I)
_BW_REFUSED = re.compile(r"\b(no|not|without|less|avoid|don'?t)\b[^.?!]{0,24}"
                         r"\b(black[- ]and[- ]white|b&w|monochrome|noir)", re.I)
# Graphics covering more than this share of the runtime read as a wall.
GRAPHICS_SHARE = 0.55
# Payoff hold after the last word (s).
PAYOFF_HOLD_S = 0.8
# A graphic box over this share of a face (chin band included) collides.
FACE_SHARE = 0.15
# The detector box's bottom vs the real chin: YuNet's box already reaches
# the chin; Haar's stops short of it (JRE 2054 run: 0.03 of the frame).
CHIN_EXTEND = {"yunet": 0.08, "haar": 0.0}
# Late words: frames a word may land after its spoken onset.
LATE_FRAMES = 2
# Matte flicker: up-down toggles inside a matte device's window, relative
# to the same shot without it, and absolute.
FLICKER_RATIO = 2.5
FLICKER_ABS = 0.02
# Repeated frames inside a slowed window.
REPEAT_SHARE = 0.3


# ── small helpers ────────────────────────────────────────────────────────

def _f(sev, lens, code, message, *, fix=None, t=None, id=None, op=None,
       ops=None, measure=None):
    out = {"code": code, "lens": lens, "severity": sev, "message": message,
           "fix": fix}
    if t is not None:
        out["t"] = round(float(t), 2)
    if id is not None:
        out["id"] = str(id)
    if op is not None:
        out["op"] = op
    if ops:
        out["ops"] = ops
    if measure is not None:
        out["measure"] = measure
    return out


def _program_s(edl):
    import schemas
    try:
        return float(schemas.program_duration(edl or {}) or 0.0)
    except Exception:  # noqa: BLE001
        return 0.0


def _asked_bw(request_text):
    t = str(request_text or "")
    return bool(_BW_ASKED.search(t)) and not _BW_REFUSED.search(t)


def genre_of(edl, index=None, fmt=None):
    """The format pack the edit is judged against: the explicit ``fmt``,
    else taste.infer_format (a user track makes it a trend desk B,
    otherwise a talking head C)."""
    if fmt:
        return str(fmt).upper()[:1]
    try:
        import taste
        return taste.infer_format(edl, index)
    except Exception:  # noqa: BLE001
        return "C"


class _View:
    """refsig.Feats truncated to the program frames (the end card is not
    judged as content)."""

    def __init__(self, F, n):
        self.__dict__.update(F.__dict__)
        n = max(0, min(int(n), F.n))
        self.n = n
        for k in ("frames",):
            setattr(self, k, F.frames[:n])
        for k in ("thumb", "gray", "grad", "hist", "comp", "luma_mean",
                  "white_frac", "black_frac", "chroma_p75", "midtone_frac",
                  "texture", "dom_hue", "dom_share", "chroma_p98"):
            v = getattr(F, k, None)
            if v is not None:
                setattr(self, k, v[:n])

    def t(self, f):
        return round(f / self.fps, 3)


def features(path, program_s=None, feat_w=FEAT_W):
    """(view, info): the render decoded once at ``feat_w`` (refsig.Feats),
    cut to the program frames."""
    import qa_metrics
    import refsig
    info = qa_metrics.probe(path)
    F = refsig.Feats(path, feat_w=feat_w)
    n = F.n
    if program_s:
        n = min(F.n, int(round(float(program_s) * F.fps)))
    return _View(F, n), info


# ── lenses ───────────────────────────────────────────────────────────────

def lens_flash(view, edl, index, request_text, maps=None):
    import photosens
    res = photosens.analyse(view.frames, view.fps,
                            opted_in=_flash_opted(request_text))
    out = []
    if res["over_limit"] and not res["opted_in"]:
        plan = photosens.safe_plan(edl, index, request_text=request_text,
                                   fps=view.fps, maps=maps)
        spans = ", ".join(f"{o['t0']:.2f}-{o['t1']:.2f}s ({o['count']}/s)"
                          for o in res["over"][:3])
        keep = (f", keeping {plan['kept_downbeats']} downbeat hits of your "
                "track" if plan.get("downbeats") else "")
        out.append(_f(
            "blocking", "flash", "flash_rate",
            f"{res['peak_per_s']} full-frame flashes in one second on the "
            f"render ({spans}); the limit is {res['max_per_s']}/s unless the "
            "user asked for strobing.",
            fix=(f"thin to the flash-safe plan: remove "
                 f"{len(plan['ops'])} flash item(s){keep} (peak "
                 f"{plan['peak_before']}/s -> {plan['peak_after']}/s on the "
                 "edit)" if plan["ops"] else
                 "the render flashes where the edit has no flash device "
                 "(a cut rhythm or the footage itself): space the cuts or "
                 "soften the brightest hits"),
            t=res["over"][0]["t0"] if res["over"] else None,
            ops=plan["ops"] or None))
    return out, res


def _flash_opted(request_text):
    try:
        import flash_safety
        return flash_safety.opted_in(request_text)
    except Exception:  # noqa: BLE001
        return False


def lens_colour(view, edl, index, request_text, payoff=None):
    """B&W as a device: share of the program's PICTURE frames that are
    black and white (type-on-black cards and frames kept in one colour are
    not a B&W look)."""
    import numpy as np
    import refsig
    out = []
    frames, und, secs = refsig.bw(view)
    tob, _d = refsig.type_on_black(view)
    tob = set(tob)
    bw = set(frames) - tob
    # a section that keeps one saturated colour is the "one colour kept"
    # device, not a flat look
    kept = set()
    for a, b in secs:
        if np.nanmedian(view.chroma_p98[a:b + 1]) >= 40.0:
            kept.update(range(a, b + 1))
    flat = bw - kept
    decided = max(1, view.n - len(set(und)) - len(tob))
    share = len(flat) / float(decided)
    measure = {"bw_share": round(share, 3),
               "bw_sections": [[view.t(a), view.t(b + 1)] for a, b in secs],
               "colour_kept_frames": len(kept)}
    if share >= BW_LOOK_SHARE and not _asked_bw(request_text):
        cause, ops = _bw_cause(edl, payoff)
        out.append(_f(
            "fix", "colour", "bw_as_look",
            f"{100 * share:.0f}% of the picture is black and white{cause}: "
            "in the references B&W is a device (a section that lands IN "
            "colour on the drop, one colour kept, a silhouette hold, a "
            "negative on a hit), never a flat look over the whole short.",
            fix=("make it a device: keep mono only up to the turn and land "
                 "colour on the payoff (grade window ending on the payoff "
                 "word, ramp_out_f 3), or keep the subject in colour "
                 "(isolate_colour keep='subject')"),
            ops=ops or None, measure=measure))
    return out, measure


def _bw_cause(edl, payoff):
    """(' (from …)', ops that turn the look into a device)."""
    fx = (edl or {}).get("effects") or {}
    prog = _program_s(edl)
    if fx.get("grade") == "bw":
        new = dict(fx)
        new["grade"] = None
        ops = [{"action": "set", "layer": "effects", "value": new}]
        end = (payoff or {}).get("t")
        if end and 1.0 < end < prog:
            ops.append({"action": "upsert", "layer": "fx", "id": "gw_mono_turn",
                        "value": {"id": "gw_mono_turn", "type": "grade_window",
                                  "stage": "world", "start": 0.0,
                                  "end": round(float(end), 2),
                                  "saturation": 0.0, "ramp_out_f": 3,
                                  "device": "grade_section",
                                  "purpose": "B&W until the payoff, colour "
                                             "lands on it"}})
        return " (the whole-program 'bw' grade preset)", ops
    for it in (edl or {}).get("fx") or []:
        if not isinstance(it, dict) or it.get("type") != "grade_window":
            continue
        try:
            span = float(it["end"]) - float(it["start"])
        except (KeyError, TypeError, ValueError):
            continue
        if (it.get("saturation") is not None and float(it["saturation"])
                <= 0.15 and not it.get("isolate") and span >= 0.5 * prog):
            end = (payoff or {}).get("t")
            op = None
            if end and float(it["start"]) + 1.0 < end < float(it["end"]):
                op = {"action": "upsert", "layer": "fx", "id": it["id"],
                      "value": {"id": it["id"], "end": round(float(end), 2),
                                "ramp_out_f": 3}}
            return (f" (grade window {it['id']} desaturates "
                    f"{float(it['start']):g}-{float(it['end']):g}s)",
                    [op] if op else [])
    return "", []


def lens_rhythm(view, edl, index, plan, genre):
    """Visual events per second against the genre band, and the stretches
    where nothing but captions moves while someone speaks."""
    import refsig
    out = []
    neg = refsig.negatives(view)
    wf = refsig.white_flashes(view, neg=neg)
    cf = refsig.colour_flashes(view, neg=neg)
    punct = set(neg) | set(wf) | set(cf)
    cuts = refsig.cuts(view, punct=punct)
    events_f = refsig.punctuation_events(view, cuts, punct)
    prog = view.n / view.fps if view.fps else 0.0
    ev = sorted({round(f / view.fps, 2) for f in events_f})
    # designed moments the edit adds without changing the whole frame
    for m in (edl or {}).get("motion") or []:
        if isinstance(m, dict) and m.get("start") is not None and \
                not _persistent(m):
            ev.append(round(float(m["start"]), 2))
    for it in (edl or {}).get("fx") or []:
        if isinstance(it, dict):
            t = it.get("start") if it.get("start") is not None else \
                it.get("at")
            if t is not None:
                ev.append(round(float(t), 2))
    for o in (edl or {}).get("overlays") or []:
        if isinstance(o, dict) and o.get("start") is not None:
            ev.append(round(float(o["start"]), 2))
            try:
                ev.append(round(float(o["start"]) + float(o["duration_s"]), 2))
            except (KeyError, TypeError, ValueError):
                pass
    ev = sorted(set(t for t in ev if 0.0 <= t <= prog))
    n_punct = len(refsig._runs(sorted(punct)))
    rate = len(ev) / prog if prog > 0 else 0.0
    band = GENRE_BANDS.get(genre) or GENRE_BANDS["C"]
    q1, med, q3 = band["events_per_s"]
    measure = {"events": len(ev), "events_per_s": round(rate, 3),
               "cuts": len(cuts), "punctuation": n_punct,
               "asl_s": round(prog / (len(cuts) + 1), 2) if prog else None,
               "genre": genre, "band_events_per_s": [q1, med, q3]}
    if rate < 0.5 * q1 and q1 > 0.3:
        out.append(_f(
            "advisory", "rhythm", "slow_cadence",
            f"{rate:.2f} visual events per second against {med:.2f} in the "
            f"genre-{genre} references (quartiles {q1:.2f}-{q3:.2f}).",
            fix="earn more changes from the story: a cutaway on a named "
                "noun, a designed moment on each turn, tighter cuts on "
                "the setup", measure=measure))
    # dead stretches: speech continues, nothing designed is on screen and
    # nothing changes for longer than the genre allows
    words = plan.get("words") or []
    dead_s = DEAD_S.get(genre, DEAD_S_DEFAULT)
    busy = _designed_spans(edl, prog)
    edges = [0.0] + ev + [prog]
    for a, b in zip(edges, edges[1:]):
        for x, y in _gaps(a, b, busy):
            if y - x < dead_s:
                continue
            inside = [w for w in words if x + 0.2 <= w["t0"] < y - 0.2]
            if len(inside) < 4:
                continue                       # a pause or a hold: fine
            best = _strongest_word(plan, x, y) or max(
                inside, key=lambda w: len(str(w.get("w") or "")))
            word = str(best.get("word_raw") or best.get("w") or "").strip()
            t = float(best.get("t") if best.get("t") is not None
                      else best.get("t0"))
            out.append(_f(
                "fix", "rhythm", "dead_stretch",
                f"{x:.1f}-{y:.1f}s: {y - x:.1f}s where only captions move "
                "while he keeps talking.",
                fix=(f"give '{word}' at {t:.2f}s a designed moment (a "
                     "lockup, a subject device, a cutaway on what he names) "
                     "or tighten the cut — never a zoom just to fill it"),
                t=x))
    return out, measure


def _designed_spans(edl, prog):
    """Program spans where something designed is on screen: graphics (not
    the caption track or a persistent band), cutaways, inserts, fx
    windows and picture cards."""
    spans = []
    e = edl or {}
    for m in e.get("motion") or []:
        if isinstance(m, dict) and m.get("template") != "caption_motion" \
                and not _persistent(m):
            try:
                spans.append((float(m["start"]), float(m["end"])))
            except (KeyError, TypeError, ValueError):
                pass
    for o in e.get("overlays") or []:
        try:
            a = float(o["start"])
            spans.append((a, a + float(o["duration_s"])))
        except (KeyError, TypeError, ValueError):
            pass
    for it in e.get("fx") or []:
        try:
            if it.get("start") is not None and it.get("end") is not None:
                spans.append((float(it["start"]), float(it["end"])))
        except (AttributeError, TypeError, ValueError):
            pass
    try:
        import timeline as tl_mod
        for b in tl_mod.program_blocks(e):
            if b.get("kind") == "insert":
                spans.append((float(b["out_start"]), float(b["out_end"])))
    except Exception:  # noqa: BLE001
        pass
    for c in ((e.get("effects") or {}).get("picture_cards") or []):
        try:
            spans.append((float(c["start"]), float(c["end"])))
        except (KeyError, TypeError, ValueError):
            pass
    return sorted((max(0.0, a), min(prog, b)) for a, b in spans if b > a)


def _gaps(a, b, spans):
    """Sub-intervals of [a, b) not covered by ``spans``."""
    out, cur = [], a
    for x, y in spans:
        if y <= cur or x >= b:
            continue
        if x > cur:
            out.append((cur, min(x, b)))
        cur = max(cur, y)
        if cur >= b:
            break
    if cur < b:
        out.append((cur, b))
    return out


def _persistent(m):
    try:
        import motion_templates
        return bool(motion_templates.persistent(m))
    except Exception:  # noqa: BLE001
        return False


def _strongest_word(plan, a, b):
    best = None
    for m in plan.get("moments") or []:
        if m.get("kind") not in ("stress", "word") or not (a < m["t"] < b):
            continue
        if best is None or m.get("strength", 0) > best.get("strength", 0):
            best = m
    return best


def audio_rms(path, sr=8000):
    """(rms dBFS per 10 ms frame, frame_s) of the render's audio (mono,
    ``sr`` Hz), or (None, None) without audio."""
    import subprocess
    import numpy as np
    try:
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-nostdin", "-i", path, "-map", "0:a:0",
             "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
            capture_output=True, timeout=120).stdout
    except Exception:  # noqa: BLE001
        return None, None
    if not raw:
        return None, None
    x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    hop = sr // 100
    n = len(x) // hop
    if n < 2:
        return None, None
    fr = x[:n * hop].reshape(n, hop)
    rms = np.sqrt((fr * fr).mean(axis=1) + 1e-12)
    return 20 * np.log10(rms + 1e-9), 0.01


def _level(db, frame_s, t0, t1):
    if db is None:
        return None
    a, b = int(max(0.0, t0) / frame_s), int(max(0.0, t1) / frame_s)
    seg = db[a:max(a + 1, b)]
    return float(seg.max()) if len(seg) else None


# A join is audible mid-sound when both sides are this loud (the master
# sits around -14 LUFS; a pause reads well under -40 dBFS).
JOIN_LOUD_DB = -32.0
JOIN_WIN_S = 0.03


def lens_words(edl, index, plan, program_s, path=None):
    """Cuts that split a word (the ASR span straddles the cut AND the
    render's audio is loud on both sides of the join), an opening
    mid-sentence, the payoff hold."""
    out = []
    words = [w for w in (index or {}).get("words") or []
             if isinstance(w, dict) and not w.get("filler")]
    keep = (edl or {}).get("keep") or []
    db, fs = audio_rms(path) if path else (None, None)
    if words and len(keep) > 1:
        try:
            import timeline as tl_mod
            blocks = [b for b in tl_mod.program_blocks(edl)]
        except Exception:  # noqa: BLE001
            blocks = []
        for prev, nxt in zip(blocks, blocks[1:]):
            if prev.get("kind") != "footage" or nxt.get("kind") != "footage":
                continue
            out_pt, in_pt = float(prev["src_end"]), float(nxt["src_start"])
            t = float(nxt["out_start"])
            straddle = None
            for edge, side in ((out_pt, "out"), (in_pt, "in")):
                w = next((w for w in words
                          if float(w["t0"]) + 0.03 < edge < float(w["t1"])
                          - 0.03), None)
                if w is not None:
                    straddle = (w, edge, side)
                    break
            if straddle is None:
                continue
            lb = _level(db, fs, t - JOIN_WIN_S, t)
            la = _level(db, fs, t, t + JOIN_WIN_S)
            if lb is None or la is None or lb < JOIN_LOUD_DB or \
                    la < JOIN_LOUD_DB:
                continue           # silent on one side: a clean cut
            w, edge, side = straddle
            k = next((i for i, kk in enumerate(keep)
                      if abs(float(kk[1 if side == "out" else 0]) - edge)
                      < 0.011), None)
            snap = float(w["t1"]) if side == "out" else float(w["t0"])
            op = None
            if k is not None:
                new = [list(x) for x in keep]
                new[k][1 if side == "out" else 0] = round(
                    snap + (0.02 if side == "out" else -0.02), 2)
                if all(b - a >= 0.2 for a, b in new) and all(
                        new[i][1] <= new[i + 1][0]
                        for i in range(len(new) - 1)):
                    op = {"action": "set", "layer": "keep", "value": new}
            out.append(_f(
                "blocking", "words", "cut_mid_word",
                f"the cut at {t:.2f}s splits '{w.get('w')}' (source "
                f"{float(w['t0']):.2f}-{float(w['t1']):.2f}s): sound runs "
                f"through the join ({lb:.0f} / {la:.0f} dBFS), an audible "
                "fragment.",
                fix=f"move the {side}-point to the word's "
                    f"{'end' if side == 'out' else 'start'} ({snap:.2f}s)",
                t=t, op=op))
        first = keep[0][0]
        prev = [w for w in words if float(w["t1"]) <= float(first) + 0.02]
        nxt = [w for w in words if float(w["t0"]) >= float(first) - 0.02]
        if prev and nxt:
            p = prev[-1]
            gap = float(nxt[0]["t0"]) - float(p["t1"])
            if gap < 0.25 and not re.search(r"[.?!]$", str(p.get("w") or "")):
                out.append(_f(
                    "fix", "words", "opens_mid_sentence",
                    f"the short opens on '{nxt[0].get('w')}' {gap:.2f}s after "
                    f"'{p.get('w')}': mid-sentence, the hook lands without "
                    "its start.",
                    fix="start on the sentence's first word, or open on a "
                        "line that stands alone", t=0.0))
    pw = plan.get("words") or []
    if pw and program_s:
        hold = program_s - max(float(w["t1"]) for w in pw)
        if 0 < hold < PAYOFF_HOLD_S - 1e-3:
            out.append(_f(
                "fix", "words", "payoff_hold_short",
                f"{hold:.2f}s after the last word before the end card "
                f"(<{PAYOFF_HOLD_S}s): the payoff does not breathe.",
                fix=f"hold {PAYOFF_HOLD_S - hold + 0.05:.2f}s more on the "
                    "last frame (extend the final keep into the pause, or a "
                    "freeze hold)", t=program_s))
    return out


def lens_type(report):
    """The type ladder from the render's own measurements (render_qc type
    caps), when the render carries them."""
    out = []
    qc = (report or {}).get("picture_qc") or (report or {}).get("qc") or {}
    caps = ((qc.get("type") or {}).get("caps")) or {}
    measure = {}
    if caps.get("captions") is not None:
        measure["captions_cap"] = caps["captions"]
        if float(caps["captions"]) < 0.025:
            out.append(_f("fix", "type", "type_too_small",
                          f"captions stand {100 * caps['captions']:.1f}% of "
                          "the frame height (< 2.5%): unreadable on a phone.",
                          fix="raise the caption size"))
    po = caps.get("payoff")
    if po and len(po) == 4:
        pid, pc, big, bc = po
        measure["payoff"] = [pid, pc]
        if big != pid and pc < bc - 1e-4:
            out.append(_f("fix", "type", "payoff_not_largest",
                          f"the payoff '{pid}' ({100 * pc:.1f}%) is smaller "
                          f"than '{big}' ({100 * bc:.1f}%).",
                          fix=f"make '{pid}' the largest lockup, or step "
                              f"'{big}' down", id=pid))
    hook = caps.get("hook")
    if hook and caps.get("captions") and hook[1] and \
            float(hook[1]) < 1.2 * float(caps["captions"]):
        out.append(_f("advisory", "type", "flat_ladder",
                      f"the hook '{hook[0]}' ({100 * hook[1]:.1f}%) barely "
                      "out-sizes the captions: no ladder.",
                      fix="a real whisper/shout ladder: the hook at least 2x "
                          "the captions", id=hook[0]))
    return out, measure


def lens_restraint(edl, index, request_text, intensity, maps=None):
    out = []
    try:
        import motion_qa
        a = motion_qa.audit(edl, index, intensity=intensity,
                            request_text=request_text, maps=maps)
        for f in a.get("findings") or []:
            if f["code"] == "flash_rate_exceeded":
                continue          # the flash lens measures the render
            sev = "blocking" if f.get("severity") == "blocking" else \
                "advisory"
            out.append(_f(sev, "restraint", f["code"], f["message"],
                          id=f.get("id")))
    except Exception as e:  # noqa: BLE001
        out.append(_f("advisory", "restraint", "audit_unavailable",
                      f"the motion audit could not run ({str(e)[:100]})"))
    prog = _program_s(edl)
    spans = []
    for m in (edl or {}).get("motion") or []:
        if not isinstance(m, dict) or _persistent(m):
            continue
        if m.get("template") == "caption_motion":
            continue
        try:
            spans.append((float(m["start"]), float(m["end"])))
        except (KeyError, TypeError, ValueError):
            continue
    covered = _union(spans)
    share = covered / prog if prog else 0.0
    if share > GRAPHICS_SHARE:
        out.append(_f("advisory", "restraint", "graphics_wall",
                      f"graphics are on screen {100 * share:.0f}% of the "
                      f"runtime (guide ~50%): the face and the words stop "
                      "carrying the story.",
                      fix="cut the weakest graphic or shorten the holds; "
                          "let the speaker breathe between designed moments"))
    asked = str(request_text or "").lower()
    zooms = ((edl or {}).get("effects") or {}).get("zooms") or []
    if zooms and not re.search(r"\b(zoom|push|punch)", asked):
        out.append(_f("advisory", "restraint", "owner_rule",
                      f"{len(zooms)} zoom(s) the user did not ask for: zooms "
                      "are optional and only where a moment earns one.",
                      fix="keep only a zoom a specific moment needs"))
    for m in (edl or {}).get("music") or []:
        prov = (m or {}).get("provenance")
        if isinstance(m, dict) and prov not in (
                "user_upload", "user_named", "user_picked_library", "legacy",
                None):
            out.append(_f("blocking", "restraint", "owner_rule",
                          f"music {m.get('id')} has provenance {prov!r}: "
                          "agents never choose music.", id=m.get("id")))
    return out, {"graphics_share": round(share, 3)}


def _union(spans):
    total, cur = 0.0, None
    for a, b in sorted(spans):
        if cur is None or a > cur[1]:
            if cur:
                total += cur[1] - cur[0]
            cur = [a, b]
        else:
            cur[1] = max(cur[1], b)
    if cur:
        total += cur[1] - cur[0]
    return total


# ── faces ────────────────────────────────────────────────────────────────

_DETECTOR = {}


def _face_detector(w, h):
    """YuNet (the baked 0.23 MB model) or Haar; None when neither."""
    import cv2
    key = (w, h)
    if key in _DETECTOR:
        return _DETECTOR[key]
    det = None
    path = os.environ.get("YUNET_MODEL")
    if not path:
        try:
            import config
            path = getattr(config, "YUNET_MODEL", None)
        except Exception:  # noqa: BLE001
            path = None
    if not path or not os.path.exists(path):
        here = os.path.dirname(os.path.abspath(__file__))
        for cand in (os.path.join(here, "models",
                                  "face_detection_yunet_2023mar.onnx"),
                     "/opt/models/face_detection_yunet_2023mar.onnx"):
            if os.path.exists(cand):
                path = cand
                break
    if path and os.path.exists(path):
        try:
            det = ("yunet", cv2.FaceDetectorYN.create(path, "", (w, h), 0.6,
                                                      0.3, 50))
        except Exception:  # noqa: BLE001
            det = None
    if det is None:
        try:
            casc = cv2.CascadeClassifier(
                cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
            if not casc.empty():
                det = ("haar", casc)
        except Exception:  # noqa: BLE001
            det = None
    _DETECTOR[key] = det
    return det


def faces_on(frame_rgb):
    """[[x0, y0, x1, y1, kind]] face boxes (frame fractions; ``kind`` the
    detector) on one RGB frame."""
    import cv2
    h, w = frame_rgb.shape[:2]
    det = _face_detector(w, h)
    if det is None:
        return []
    kind, d = det
    bgr = cv2.cvtColor(frame_rgb.astype("uint8"), cv2.COLOR_RGB2BGR)
    out = []
    if kind == "yunet":
        _ok, faces = d.detect(bgr)
        for row in faces if faces is not None else []:
            x, y, fw, fh = (float(v) for v in row[:4])
            out.append([x / w, y / h, (x + fw) / w, (y + fh) / h, kind])
    else:
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        for x, y, fw, fh in d.detectMultiScale(gray, 1.15, 5,
                                               minSize=(w // 14, w // 14)):
            out.append([x / w, y / h, (x + fw) / w, (y + fh) / h, kind])
    return out


def _box_share(face, box):
    fx0, fy0, fx1, fy1 = face[:4]
    kind = face[4] if len(face) > 4 else "haar"
    fy1 = fy1 + CHIN_EXTEND.get(kind, 0.18) * (fy1 - fy0)
    bx0, by0, bx1, by1 = box
    ix = max(0.0, min(fx1, bx1) - max(fx0, bx0))
    iy = max(0.0, min(fy1, by1) - max(fy0, by0))
    area = max(1e-9, (fx1 - fx0) * (fy1 - fy0))
    return ix * iy / area


def _item_box(m, W, H):
    try:
        import caption_carry
        box = caption_carry.footprint_box(m, caption_carry.frame_ar(W, H))
        if box:
            return [float(v) for v in box]
    except Exception:  # noqa: BLE001
        pass
    fp = (m or {}).get("footprint") or {}
    if fp.get("box"):
        return [float(v) for v in fp["box"]]
    b = (m or {}).get("box")
    if b:
        return [float(v) for v in b]
    try:
        # never measured (a batch-written item): the template's nominal ink
        import keepout
        import motion_templates
        tpl = (m or {}).get("template") or ""
        est = keepout.nominal_ink(tpl, motion_templates.spec(tpl),
                                  (m or {}).get("params") or {},
                                  frame=(W, H))
        return [float(v) for v in est] if est else None
    except Exception:  # noqa: BLE001
        return None


def lens_faces(path, edl, info, sample_fps=4.0, budget_s=20.0):
    """A graphic's box over a face (chin band included) on rendered
    frames. Items that deliberately overlap the face (allow_face_overlap,
    behind the subject) are not judged."""
    import numpy as np
    import qa_metrics
    out = []
    items = []
    W, H = info.get("width") or 1080, info.get("height") or 1920
    for m in (edl or {}).get("motion") or []:
        if not isinstance(m, dict) or m.get("allow_face_overlap") or \
                m.get("layer") == "behind_subject" or _persistent(m):
            continue
        box = _item_box(m, W, H)
        if box and m.get("start") is not None:
            items.append((m, box))
    if not items:
        return out
    fps = float(info.get("fps") or 30.0)
    want = set()
    for m, _b in items:
        a, b = float(m["start"]), float(m["end"])
        k = a + 0.25
        while k < b - 0.05:
            want.add(int(round(k * fps)))
            k += 1.0 / sample_fps
    if not want:
        return out
    sw = 360
    sh = int(round(H * sw / float(W) / 2)) * 2
    deadline = time.monotonic() + budget_s
    idx, frames = qa_metrics.read_frames(path, size=(sw, sh), frames=want)
    hits = {}
    for f, img in zip(idx, frames):
        if time.monotonic() > deadline:
            break
        t = f / fps
        faces = faces_on(np.asarray(img))
        for m, box in items:
            if not (float(m["start"]) <= t < float(m["end"])):
                continue
            for face in faces:
                if (face[2] - face[0]) < 0.06:
                    continue
                if _box_share(face, box) >= FACE_SHARE:
                    hits.setdefault(m["id"], []).append(round(t, 2))
                    break
    for mid, ts in hits.items():
        if len(ts) < 2:
            continue
        out.append(_f("fix", "faces", "type_on_face",
                      f"graphic {mid} sits on the speaker's face or chin at "
                      f"{ts[0]:.2f}-{ts[-1]:.2f}s ({len(ts)} sampled "
                      "frames).",
                      fix="move it clear of the face (the chin is part of "
                          "the face), shrink it, or put it behind him "
                          "deliberately",
                      t=ts[0], id=mid))
    return out


# ── motion: late words, flicker, repeated frames ────────────────────────

_TOK = re.compile(r"[a-z0-9]+")


def _tokens(text):
    return _TOK.findall(str(text or "").lower().replace("*", ""))


def _item_text(m):
    p = (m or {}).get("params") or {}
    bits = []
    for k in ("text", "value", "title", "label", "headline", "word"):
        if isinstance(p.get(k), str):
            bits.append(p[k])
    for k in ("rows", "items", "lines"):
        v = p.get(k)
        if isinstance(v, list):
            for r in v:
                if isinstance(r, str):
                    bits.append(r)
                elif isinstance(r, dict):
                    bits += [str(x) for x in r.values() if isinstance(x, str)]
    return " ".join(bits)


def landing_targets(edl, words, reach_s=1.2):
    """[(item, spoken onset t, word)] — the first printed word of each
    text graphic matched to its spoken word near the item's start."""
    out = []
    for m in (edl or {}).get("motion") or []:
        if not isinstance(m, dict) or _persistent(m) or \
                m.get("template") in ("caption_motion",):
            continue
        toks = _tokens(_item_text(m))
        toks = [t for t in toks if len(t) > 2 or t.isdigit()]
        if not toks:
            continue
        a = float(m["start"])
        near = [w for w in words if a - reach_s <= w["t0"] <= a + reach_s]
        hit = None
        for w in near:
            if _tokens(w.get("w"))[:1] == toks[:1]:
                hit = w
                break
        if hit is not None:
            out.append((m, float(hit["t0"]), hit.get("w")))
    return out


def _box_px(box, w, h):
    x0, y0, x1, y1 = box
    return (max(0, int(x0 * w)), max(0, int(y0 * h)),
            min(w, int(math.ceil(x1 * w))), min(h, int(math.ceil(y1 * h))))


def visual_onset(path, info, box, t_expect, *, before=0.4, after=0.9,
                 width=270):
    """The program second where the picture inside ``box`` first changes
    like a reveal near ``t_expect`` (a change well above the box's own
    motion before it), or None."""
    import numpy as np
    import qa_metrics
    fps = float(info.get("fps") or 30.0)
    W, H = info.get("width") or 1080, info.get("height") or 1920
    h = int(round(H * width / float(W) / 2)) * 2
    f0 = max(0, int(round((t_expect - before) * fps)))
    f1 = int(round((t_expect + after) * fps))
    idx, frames = qa_metrics.read_frames(path, size=(width, h), gray=True,
                                         frames=set(range(f0, f1 + 1)))
    if len(frames) < 4:
        return None
    x0, y0, x1, y1 = _box_px(box, width, h)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    crop = [np.asarray(f, np.float32)[y0:y1, x0:x1] for f in frames]
    d = [float(np.abs(crop[i] - crop[i - 1]).mean())
         for i in range(1, len(crop))]
    k_exp = int(round(t_expect * fps)) - idx[0]
    base = d[:max(1, k_exp - 2)]
    floor = float(np.median(base)) if base else 0.0
    peak = max(d) if d else 0.0
    thr = max(2.5, 4.0 * floor, 0.5 * peak)
    if peak < max(2.5, 4.0 * floor):
        return None
    for i, v in enumerate(d):
        if idx[i + 1] / fps < t_expect - before + 1e-6:
            continue
        if v >= thr:
            return idx[i + 1] / fps
    return None


def lens_late_words(path, info, edl, plan, budget_s=20.0):
    out = []
    fps = float(info.get("fps") or 30.0)
    W, H = info.get("width") or 1080, info.get("height") or 1920
    deadline = time.monotonic() + budget_s
    for m, t_word, word in landing_targets(edl, plan.get("words") or []):
        if time.monotonic() > deadline:
            break
        box = _item_box(m, W, H)
        if not box:
            continue
        seen = visual_onset(path, info, box, t_word)
        if seen is None:
            continue
        late = int(round((seen - t_word) * fps))
        if late > LATE_FRAMES:
            out.append(_f(
                "fix", "motion", "late_word",
                f"'{word}' is said at {t_word:.2f}s but graphic {m['id']} "
                f"lands {late} frames later ({seen:.2f}s).",
                fix=f"start {m['id']} on the word: move it {late} frames "
                    "earlier (its landing should sit on the onset, a frame "
                    "early at most)",
                t=t_word, id=m["id"],
                op={"action": "upsert", "layer": "motion", "id": m["id"],
                    "value": {"id": m["id"],
                              "start": round(float(m["start"]) - late / fps,
                                             2),
                              "end": round(float(m["end"]) - late / fps,
                                           2)}}))
    return out


MATTE_KINDS_FLICKER = ("glow", "outline", "poster", "isolate", "echo",
                       "exposure", "negate")


def toggle_share(frames, thr=12.0, block=24, span=6, min_toggles=2):
    """Per window of ``span`` frames, the share of pixels that toggle
    (an up-down or down-up step pair, both steps past ``thr``) at least
    ``min_toggles`` times, in the WORST block of ``block`` px. An edge or a
    line passing a pixel toggles it once; a chattering matte toggles it
    again and again — the ROADMAP's edge_chatter, measured on the render.
    Flicker lives on edges, so a frame-wide share would drown it in still
    picture; the comparison with the same shot without the device
    (lens_flicker) sets grain aside."""
    import numpy as np
    if len(frames) < span:
        return []
    st = np.stack([np.asarray(f, np.float32) for f in frames])
    d = np.diff(st, axis=0)
    tog = ((d[:-1] * d[1:] < 0) & (np.abs(d[:-1]) > thr)
           & (np.abs(d[1:]) > thr))
    out = []
    n = span - 2
    for i in range(0, tog.shape[0] - n + 1):
        cnt = tog[i:i + n].sum(axis=0) >= min_toggles
        h, w = cnt.shape
        hb, wb = max(1, h // block), max(1, w // block)
        t = cnt[:hb * block, :wb * block].reshape(hb, block, wb, block)
        out.append(float(t.mean(axis=(1, 3)).max()))
    return out


def lens_flicker(path, info, edl, budget_s=20.0, width=540):
    """Matte devices whose edges flicker: up-down toggles inside the
    device's window against the same shot just before (or after) it."""
    import numpy as np
    import qa_metrics
    out = []
    fps = float(info.get("fps") or 30.0)
    W, H = info.get("width") or 1080, info.get("height") or 1920
    width = int(min(width, W))
    width -= width % 2
    h = int(round(H * width / float(W) / 2)) * 2
    prog = _program_s(edl)
    deadline = time.monotonic() + budget_s
    for it in (edl or {}).get("fx") or []:
        if time.monotonic() > deadline:
            break
        if not isinstance(it, dict) or it.get("type") not in (
                "subject", "background", "depth"):
            continue
        if it.get("type") == "subject" and it.get("kind") not in \
                MATTE_KINDS_FLICKER:
            continue
        try:
            a, b = float(it["start"]), float(it["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if b - a < 0.5:
            continue
        ref = (max(0.0, a - min(1.5, b - a)), a) if a >= 0.6 else \
            (b, min(prog, b + min(1.5, b - a)))
        if ref[1] - ref[0] < 0.4:
            continue
        rng = set(range(int(round(a * fps)) + 1, int(round(b * fps)) - 1))
        rng_ref = set(range(int(round(ref[0] * fps)),
                            int(round(ref[1] * fps))))
        idx, frames = qa_metrics.read_frames(path, size=(width, h),
                                             gray=True, frames=rng | rng_ref)
        fin = [f for i, f in zip(idx, frames) if i in rng]
        fref = [f for i, f in zip(idx, frames) if i in rng_ref]
        if len(fin) < 4 or len(fref) < 4:
            continue
        inside = float(np.median(toggle_share(fin)))
        base = float(np.median(toggle_share(fref)))
        if inside >= FLICKER_ABS and inside >= FLICKER_RATIO * max(base,
                                                                   1e-4):
            out.append(_f(
                "fix", "motion", "matte_flicker",
                f"{it['id']} ({it.get('kind') or it.get('type')}) "
                f"flickers: {100 * inside:.1f}% of its edge pixels toggle "
                f"again and again frame to frame, against {100 * base:.1f}% "
                "in the same shot without it.",
                fix="use a softer device on this shot (glow over outline), "
                    "shorten it to the stable part, or move it to a shot "
                    "with a cleaner matte",
                t=a, id=it["id"],
                measure={"toggle_inside": round(inside, 4),
                         "toggle_ref": round(base, 4)}))
    return out


def _slowed_windows(edl):
    """Program windows of speed spans slower than 0.9x."""
    try:
        from timeline import Timeline
        e = edl or {}
        tl = Timeline(e.get("keep") or [], e.get("inserts") or [],
                      e.get("speed") or [])
    except Exception:  # noqa: BLE001
        return []
    out = []
    for sp in (edl or {}).get("speed") or []:
        try:
            if float(sp["factor"]) >= 0.9:
                continue
            a = tl.src_to_out(float(sp["start"]) + 0.01)
            b = tl.src_to_out(float(sp["end"]) - 0.01)
        except (KeyError, TypeError, ValueError):
            continue
        if a is not None and b is not None and b - a > 0.3:
            out.append((a, b, float(sp["factor"]), sp.get("id")))
    return out


def lens_repeats(view, edl):
    """Repeated frames in slowed windows (a slow-motion tail that shows the
    same picture twice), unless a time interpolation covers them."""
    import numpy as np
    out = []
    interp = [(float(i["start"]), float(i["end"]))
              for i in (edl or {}).get("fx") or []
              if isinstance(i, dict) and i.get("type") == "time"
              and i.get("kind") == "interpolate" and i.get("start") is not None]
    for a, b, factor, sid in _slowed_windows(edl):
        if any(x <= a + 0.05 and b - 0.05 <= y for x, y in interp):
            continue
        f0, f1 = int(round(a * view.fps)), int(round(b * view.fps))
        g = view.gray[f0:f1]
        if len(g) < 6:
            continue
        d = np.abs(np.diff(g.astype(np.float32), axis=0)).mean(axis=(1, 2))
        moving = d > 0.8
        if moving.sum() < 2:
            continue
        rep = float((d < 0.25).mean())
        if rep >= REPEAT_SHARE:
            out.append(_f(
                "fix", "motion", "repeated_frames",
                f"{a:.2f}-{b:.2f}s at {factor:g}x repeats "
                f"{100 * rep:.0f}% of its frames: the slow motion stutters.",
                fix="interpolate the slowed window (add_time_effect "
                    "kind='interpolate'), or keep it at >= 0.75x",
                t=a, id=sid,
                op={"action": "upsert", "layer": "fx", "id": f"tm_interp_{sid}",
                    "value": {"id": f"tm_interp_{sid}", "type": "time",
                              "stage": "world", "start": round(a, 2),
                              "end": round(b, 2), "kind": "interpolate",
                              "device": "time_interpolate"}}))
    return out


# ── range: invention, not a house style ─────────────────────────────────

def device_signature(edl):
    """The set of devices an edit uses: fx device cards (or type:kind),
    motion templates, a look name, overlay cutaways."""
    sig = set()
    e = edl or {}
    for it in e.get("fx") or []:
        if isinstance(it, dict):
            sig.add(it.get("device") or f"{it.get('type')}:{it.get('kind')}")
    for m in e.get("motion") or []:
        if isinstance(m, dict) and m.get("template") not in (
                "caption_motion",):
            sig.add(f"mg:{m.get('template')}")
    fx = e.get("effects") or {}
    if fx.get("grade"):
        sig.add(f"grade:{fx['grade']}")
    for s in fx.get("stylize") or []:
        if isinstance(s, dict):
            sig.add(f"stylize:{s.get('kind')}")
    if fx.get("picture_cards"):
        sig.add("layout:card")
    if any(isinstance(o, dict) and o.get("fit") == "cover"
           for o in e.get("overlays") or []):
        sig.add("broll:cutaway")
    return sig


# Devices that widen the range, keyed by the moment tags that earn them.
# Each is a starting idea bound to the moment, not a recipe.
FRESH_IDEAS = (
    ("number", "add_ui_scene", "a UI proof of the number (a counter that "
     "lands on the spoken figure, the data card it comes from)"),
    ("number", "add_motion_graphic", "the number as the picture: a to-scale "
     "comparison or a fill that grows to the figure"),
    ("contrast", "add_depth_effect", "a rack focus that pulls from him to "
     "the world on the turn word (kind='rack_focus')"),
    ("negation", "set_grade_window", "the world drains to mono and colour "
     "lands back on the answer (a grade window ending on the payoff)"),
    ("payoff", "add_subject_effect", "the speaker becomes the graphic on "
     "the payoff: a poster on the accent field, held through the hold"),
    ("payoff", "add_depth_effect", "freeze the payoff frame and drift "
     "through it in 2.5D (kind='parallax' on the payoff frame)"),
    ("name", "source_broll", "a moving archive still or a cinematic "
     "cutaway on the named thing (graded to his shot)"),
    ("emotion", "add_time_effect", "an echo trail on the gesture that "
     "carries the feeling (kind='echo', on a real movement only)"),
    (None, "add_type_window", "the hero word as a window onto the next "
     "shot (letters cut into the picture)"),
)


def lens_range(edl, plan, previous=None, max_ideas=3):
    """House style (the same device set as earlier shorts of this project)
    and strong moments with nothing on them, with fresh, bound ideas."""
    out = []
    mine = device_signature(edl)
    prev = [device_signature(p) for p in previous or [] if p]
    same = []
    for k, s in enumerate(prev):
        if mine and s:
            j = len(mine & s) / float(len(mine | s))
            if j >= 0.7:
                same.append(k)
    if len(same) >= 2 or (prev and len(same) == len(prev) and same):
        shared = sorted(set.intersection(mine, *[prev[k] for k in same]))
        out.append(_f(
            "advisory", "range", "house_style",
            f"this short re-uses the device set of {len(same)} earlier "
            f"short(s) ({', '.join(shared[:5])}): a house style, not "
            "invention.",
            fix="keep what serves this story and give its strongest moment "
                "a device none of the earlier shorts used (see the ideas)"))
    busy = []
    for m in (edl or {}).get("motion") or []:
        if isinstance(m, dict) and m.get("start") is not None and \
                m.get("template") != "caption_motion" and not _persistent(m):
            busy.append((float(m["start"]) - 0.5, float(m["end"]) + 0.3))
    for it in (edl or {}).get("fx") or []:
        if isinstance(it, dict):
            t0 = it.get("start") if it.get("start") is not None else \
                it.get("at")
            t1 = it.get("end") if it.get("end") is not None else t0
            if t0 is not None:
                busy.append((float(t0) - 0.5, float(t1) + 0.3))
    for o in (edl or {}).get("overlays") or []:
        if isinstance(o, dict) and o.get("start") is not None:
            busy.append((float(o["start"]) - 0.3,
                         float(o["start"]) + float(o.get("duration_s") or 0)))
    ideas, used_verbs = [], set()
    moments = sorted(plan.get("moments") or [],
                     key=lambda m: -float(m.get("strength") or 0))
    payoff = plan.get("payoff") or {}
    for m in moments:
        if len(ideas) >= max_ideas:
            break
        t = float(m["t"])
        if any(a <= t <= b for a, b in busy):
            continue
        tags = set(m.get("tags") or [])
        if payoff and abs(t - float(payoff.get("t") or -9)) < 0.05:
            tags.add("payoff")
        if m.get("kind") not in ("stress", "word") and "payoff" not in tags:
            continue
        for tag, verb, idea in FRESH_IDEAS:
            if verb in used_verbs or (tag and tag not in tags):
                continue
            if any(v.split(":")[0] == verb for v in mine):
                continue
            word = m.get("word_raw") or m.get("word") or ""
            ideas.append({"t": round(t, 2), "word": word, "verb": verb,
                          "idea": idea, "bind": m.get("bind")})
            used_verbs.add(verb)
            break
    for k, idea in enumerate(ideas):
        out.append(_f(
            "advisory", "range", "untapped_moment",
            f"'{idea['word']}' at {idea['t']:.2f}s is one of the strongest "
            "moments and nothing marks it.",
            fix=f"{idea['idea']} — {idea['verb']}, bound to that word "
                "(only if it serves the story; zero is a fine answer)",
            t=idea["t"]))
    return out, {"devices": sorted(mine), "ideas": ideas}


# ── brand ────────────────────────────────────────────────────────────────

def lens_brand(path, edl, index, info, *, final, want_wm, budget_s=30.0,
               report=None):
    """The end card and the corner mark, by render_qc's exact geometry
    (the render's own picture check when it carries the scores)."""
    out = []
    if not final:
        return out, {"checked": False}
    qc = (report or {}).get("picture_qc") or {}
    if isinstance(qc.get("endcard"), (int, float)) or \
            isinstance(qc.get("watermark"), (int, float)):
        import render_qc
        ec, rb = qc.get("endcard"), qc.get("watermark")
        ec = float(ec) if isinstance(ec, (int, float)) else None
        rb = float(rb) if isinstance(rb, (int, float)) else None
        return _brand_findings(ec, rb, render_qc), {
            "checked": True, "endcard": ec, "watermark": rb,
            "from": "render picture check"}
    try:
        import config
        import render_qc
        import renderer
        W, H = info.get("width"), info.get("height")
        fps = float(info.get("fps") or 30.0)
        outro = float(getattr(config, "OUTRO_DURATION_S", 5.0) or 5.0) \
            if renderer.endcard_path() else 0.0
        p = render_qc.plan(edl, index, W=W, H=H, fps=fps, outro_s=outro,
                           want_wm=want_wm)
        deadline = time.monotonic() + budget_s
        ec = render_qc.endcard_match(path, p, deadline) if outro else None
        rb = render_qc.robot_match(path, p, deadline) if want_wm else None
    except Exception as e:  # noqa: BLE001
        return out, {"checked": False, "error": str(e)[:120]}
    return _brand_findings(ec, rb, render_qc), {"checked": True,
                                               "endcard": ec, "watermark": rb}


def _brand_findings(ec, rb, render_qc):
    out = []
    if ec is not None and ec < render_qc.ENDCARD_MIN_NCC:
        out.append(_f("blocking", "brand", "brand_endcard",
                      f"the Valmera end card does not match ({ec:.2f} "
                      "correlation): it must play exactly as rendered.",
                      fix="remove whatever draws over or replaces the last "
                          "5 s; every creative layer ends before the card"))
    if rb is not None and rb < render_qc.ROBOT_MIN_NCC:
        out.append(_f("blocking", "brand", "brand_watermark",
                      f"the free-tier corner mark reads {rb:.2f} (< "
                      f"{render_qc.ROBOT_MIN_NCC}): it must stay untouched.",
                      fix="nothing may cover or alter the corner mark"))
    return out


# ── the critique ─────────────────────────────────────────────────────────

def critique(path, edl, index=None, *, request_text=None, report=None,
             previous=None, fmt=None, intensity="designed", final=None,
             want_wm=False, maps=None, deep=True, board_dir=None):
    """Judge the render at ``path`` of ``edl``. Returns {version, score,
    genre, findings (ranked), measures, ideas, strengths, board, wall_s}.
    Never raises for a lens: a lens that fails is reported, the others
    still judge."""
    t_start = time.monotonic()
    import device_planner
    edl = edl or {}
    index = index or {}
    program_s = _program_s(edl)
    genre = genre_of(edl, index, fmt)
    findings, measures, errors = [], {"genre": genre}, []
    try:
        plan = device_planner.moments(edl, index, maps=maps)
    except Exception as e:  # noqa: BLE001
        plan = {"words": [], "moments": [], "payoff": None}
        errors.append(f"moments: {str(e)[:100]}")
    payoff = plan.get("payoff")
    try:
        view, info = features(path, program_s)
    except Exception as e:  # noqa: BLE001
        return {"version": CRITIC_VERSION, "score": None, "genre": genre,
                "findings": [_f("blocking", "brand", "unreadable",
                                f"the render could not be decoded "
                                f"({str(e)[:120]})")],
                "measures": measures, "ideas": [], "strengths": [],
                "board": None, "errors": [str(e)[:160]],
                "wall_s": round(time.monotonic() - t_start, 2)}
    dur = float(info.get("duration") or 0.0)
    if final is None:
        final = dur >= program_s + 3.0
    measures["program_s"] = round(program_s, 2)
    measures["render"] = {"w": info.get("width"), "h": info.get("height"),
                          "fps": info.get("fps"),
                          "duration_s": round(dur, 2), "final": bool(final)}

    def run(name, fn, *a, **k):
        try:
            return fn(*a, **k)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {type(e).__name__}: {str(e)[:120]}")
            return None

    got = run("flash", lens_flash, view, edl, index, request_text, maps)
    if got:
        findings += got[0]
        measures["flash"] = {k: got[1].get(k) for k in (
            "peak_per_s", "red_peak_per_s", "over", "max_per_s")}
    got = run("colour", lens_colour, view, edl, index, request_text, payoff)
    if got:
        findings += got[0]
        measures["colour"] = got[1]
    got = run("rhythm", lens_rhythm, view, edl, index, plan, genre)
    if got:
        findings += got[0]
        measures["rhythm"] = got[1]
    got = run("words", lens_words, edl, index, plan, program_s, path)
    findings += got or []
    got = run("type", lens_type, report)
    if got:
        findings += got[0]
        measures["type"] = got[1]
    got = run("restraint", lens_restraint, edl, index, request_text,
              intensity, maps)
    if got:
        findings += got[0]
        measures["restraint"] = got[1]
    got = run("repeats", lens_repeats, view, edl)
    findings += got or []
    if deep:
        findings += run("faces", lens_faces, path, edl, info) or []
        findings += run("late_words", lens_late_words, path, info, edl,
                        plan) or []
        findings += run("flicker", lens_flicker, path, info, edl) or []
    got = run("range", lens_range, edl, plan, previous)
    ideas = []
    if got:
        findings += got[0]
        measures["range"] = {"devices": got[1]["devices"]}
        ideas = got[1]["ideas"]
    got = run("brand", lens_brand, path, edl, index, info, final=final,
              want_wm=want_wm, report=report)
    if got:
        findings += got[0]
        measures["brand"] = got[1]
    order = {s: i for i, s in enumerate(SEVERITIES)}
    findings.sort(key=lambda f: (order.get(f["severity"], 9),
                                 f.get("t") if f.get("t") is not None
                                 else 1e9))
    score = 10.0 - sum(_WEIGHT.get(f["severity"], 0.2) for f in findings)
    score = round(max(0.0, min(10.0, score)), 1)
    strengths = _strengths(findings, measures)
    out = {"version": CRITIC_VERSION, "score": score, "genre": genre,
           "findings": findings, "measures": measures, "ideas": ideas,
           "strengths": strengths, "errors": errors,
           "wall_s": round(time.monotonic() - t_start, 2), "board": None}
    if board_dir:
        out["board"] = run("board", board, path, view, out, board_dir)
    return out


def _strengths(findings, measures):
    codes = {f["code"] for f in findings}
    out = []
    fl = measures.get("flash") or {}
    if "flash_rate" not in codes and fl.get("peak_per_s") is not None:
        out.append(f"flash-safe ({fl['peak_per_s']}/s peak)")
    col = measures.get("colour") or {}
    if "bw_as_look" not in codes and col.get("bw_sections"):
        out.append("B&W used as a device, not a look")
    if not codes & {"cut_mid_word", "opens_mid_sentence"}:
        out.append("clean word-safe cuts")
    if "dead_stretch" not in codes:
        out.append("no dead stretch")
    br = measures.get("brand") or {}
    if br.get("checked") and not codes & {"brand_endcard", "brand_watermark"}:
        out.append("brand intact")
    return out


def critique_text(res, limit=10):
    """The agent-facing reply."""
    if not res:
        return "CRITIC: no result."
    fs = res.get("findings") or []
    by = {s: sum(1 for f in fs if f["severity"] == s) for s in SEVERITIES}
    m = res.get("measures") or {}
    rh = m.get("rhythm") or {}
    lines = [f"CRITIC v{res.get('version')} — score {res.get('score')}/10 "
             f"(genre {res.get('genre')}): {by['blocking']} blocking, "
             f"{by['fix']} to fix, {by['advisory']} advisory."]
    bits = []
    if rh:
        bits.append(f"{rh.get('events_per_s')} events/s (refs "
                    f"{rh.get('band_events_per_s', [None, None])[1]})")
    col = m.get("colour") or {}
    if col:
        bits.append(f"B&W {100 * float(col.get('bw_share') or 0):.0f}%")
    fl = m.get("flash") or {}
    if fl:
        bits.append(f"flash peak {fl.get('peak_per_s')}/s")
    rs = m.get("restraint") or {}
    if rs:
        bits.append(f"graphics {100 * float(rs.get('graphics_share') or 0):.0f}"
                    "% of runtime")
    if bits:
        lines.append("MEASURES: " + "; ".join(bits) + ".")
    for f in fs[:limit]:
        where = f" @{f['t']:.2f}s" if f.get("t") is not None else ""
        who = f" [{f['id']}]" if f.get("id") else ""
        op = " (op attached)" if f.get("op") or f.get("ops") else ""
        lines.append(f"- [{f['severity']}] {f['code']}{who}{where}: "
                     f"{f['message']}"
                     + (f" FIX: {f['fix']}{op}" if f.get("fix") else ""))
    if len(fs) > limit:
        lines.append(f"(+{len(fs) - limit} more)")
    if res.get("strengths"):
        lines.append("WORKING: " + ", ".join(res["strengths"]) + ".")
    if res.get("board"):
        lines.append(f"BOARD: {res['board']}")
    return "\n".join(lines)


# ── the board ────────────────────────────────────────────────────────────

def board(path, view, res, out_dir, cols=6, rows=2):
    """A contact sheet of the program (12 frames) over a timeline strip:
    cuts (white), visual events, flash windows (red), B&W sections (grey),
    findings (amber ticks). Returns the PNG path."""
    import numpy as np
    from PIL import Image, ImageDraw
    import qa_metrics
    os.makedirs(out_dir, exist_ok=True)
    n = max(1, view.n)
    picks = [int((k + 0.5) * n / (cols * rows)) for k in range(cols * rows)]
    info = qa_metrics.probe(path)
    W, H = info.get("width") or 1080, info.get("height") or 1920
    tw = 180
    th = int(round(H * tw / float(W)))
    idx, frames = qa_metrics.read_frames(path, size=(tw, th),
                                         frames=set(picks))
    sheet_w = cols * tw
    strip_h = 70
    im = Image.new("RGB", (sheet_w, rows * (th + 18) + strip_h + 10),
                   (16, 16, 18))
    dr = ImageDraw.Draw(im)
    for k, (f, fr) in enumerate(zip(idx, frames)):
        x, y = (k % cols) * tw, (k // cols) * (th + 18)
        im.paste(Image.fromarray(np.asarray(fr).astype("uint8")), (x, y))
        dr.text((x + 4, y + th + 2), f"{f / view.fps:5.2f}s",
                fill=(220, 220, 220))
    y0 = rows * (th + 18) + 4
    prog = n / view.fps

    def X(t):
        return int(4 + (sheet_w - 8) * max(0.0, min(1.0, t / max(prog,
                                                                  1e-6))))
    dr.rectangle([4, y0, sheet_w - 4, y0 + strip_h - 4], outline=(80, 80, 80))
    col = (res.get("measures") or {}).get("colour") or {}
    for a, b in col.get("bw_sections") or []:
        dr.rectangle([X(a), y0 + 2, X(b), y0 + 14], fill=(120, 120, 120))
    for o in ((res.get("measures") or {}).get("flash") or {}).get("over") or []:
        dr.rectangle([X(o["t0"]), y0 + 16, X(o["t1"]), y0 + 28],
                     fill=(220, 40, 40))
    for f in res.get("findings") or []:
        if f.get("t") is not None:
            c = {"blocking": (255, 60, 60), "fix": (255, 190, 40)}.get(
                f["severity"], (120, 170, 255))
            dr.line([X(f["t"]), y0 + 30, X(f["t"]), y0 + strip_h - 6],
                    fill=c, width=2)
    dr.text((8, y0 + strip_h - 18),
            f"score {res.get('score')}  genre {res.get('genre')}  "
            f"{len(res.get('findings') or [])} findings", fill=(240, 240, 240))
    name = os.path.splitext(os.path.basename(path))[0][:60]
    out = os.path.join(out_dir, f"critic_{name}.png")
    im.save(out)
    return out
