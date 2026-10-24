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
# Payoff hold after the last word (s): the owner's 0.6-1.2 s of live footage
# (review.md). It was 0.8, stricter than the review, and asked the Gates
# run's editors to extend tails that were already in range (22 Oct).
PAYOFF_HOLD_S = 0.6
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
        # a grade the edit applied is a fix; footage that is itself near
        # monochrome (a grey factory, a white robot) is advice
        sev = "fix" if cause else "advisory"
        if not cause:
            cause = (" (the footage itself is near-monochrome — no grade "
                     "made it so)")
        out.append(_f(
            sev, "colour", "bw_as_look",
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


# A B&W section the critic proposes: the build into the payoff, never
# more than this share of the program (or this many seconds) — a section,
# well under BW_LOOK_SHARE, so the fix is a device and is never flagged
# again as a look.
BW_SECTION_SHARE = 0.35
BW_SECTION_MAX_S = 8.0


def _bw_section(end, prog, floor=0.0):
    """(start, end) of a mono section that lands colour ON ``end``."""
    span = min(BW_SECTION_SHARE * prog, BW_SECTION_MAX_S)
    return round(max(float(floor), float(end) - span), 2), \
        round(float(end), 2)


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
            a, b = _bw_section(end, prog)
            ops.append({"action": "upsert", "layer": "fx", "id": "gw_mono_turn",
                        "value": {"id": "gw_mono_turn", "type": "grade_window",
                                  "stage": "world", "start": a, "end": b,
                                  "saturation": 0.0, "ramp_out_f": 3,
                                  "device": "grade_section",
                                  "purpose": "a B&W build into the payoff; "
                                             "colour lands on it"}})
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
            if end and float(it["start"]) + 1.0 < end <= float(it["end"]):
                a, b = _bw_section(end, prog, floor=float(it["start"]))
                op = {"action": "upsert", "layer": "fx", "id": it["id"],
                      "value": {"id": it["id"], "start": a, "end": b,
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


def _sound_safe_edge(w, edge, kind, keep, words, level, index):
    """(edge, measured) for a keep edge the transcript puts inside word
    ``w``: (False, True) when the source's sound is quiet there
    (audit.edge_verdicts: the transcript's timing is off, no mid-word cut);
    the sound-safe edge measured on the source; else the edge the keep
    tools fall back to without the sound (cut_audio.place_edge, padded past
    the word's transcript edge)."""
    import audit
    import cut_audio
    if level is not None:
        try:
            fps = float(((index or {}).get("video") or {}).get("fps") or 30.0)
            v = audit.edge_verdicts(
                [{"boundary": float(edge), "kind": kind,
                  "word": w.get("w"), "t0": float(w["t0"]),
                  "t1": float(w["t1"])}],
                level, 1.0 / max(fps, 1.0), keep, words)[0]
            if v.get("db") is not None:
                if not v["loud"]:
                    return False, True
                if v.get("safe") is not None:
                    return round(float(v["safe"]), 2), True
        except Exception:  # noqa: BLE001 — fall back to the transcript
            pass
    got = cut_audio.place_edge(kind, float(edge), words, None)
    t = round(float(got["t"]), 2)
    if abs(t - float(edge)) < 0.005:
        t = round(float(w["t1"]) + 0.02 if kind == "end"
                  else float(w["t0"]) - 0.02, 2)
    return t, False


# A join is audible mid-sound when both sides are this loud (the master
# sits around -14 LUFS; a pause reads well under -40 dBFS).
JOIN_LOUD_DB = -32.0
JOIN_WIN_S = 0.03


def lens_words(edl, index, plan, program_s, path=None, cut_level=None):
    """Cuts that split a word (the ASR span straddles the cut AND the
    render's audio is loud on both sides of the join), an opening
    mid-sentence, the payoff hold.

    ``cut_level``: the SOURCE's sound at the keep's edges (cut_audio.
    source_levels). With it a join is judged and repaired by the one rule
    the keep tools and the render's MID-WORD AUDIT use (audit.
    edge_verdicts): an edge whose source sound is quiet is no mid-word cut,
    and the op moves a loud one to the sound-safe edge, not to the
    transcript's word edge (which runs 0.05-0.25 s off the sound: the op's
    edge was flagged again by the next audit, Diamandis 4416-4425)."""
    import audit
    duck = audit.ducked_spans(edl)
    out = []
    words = [w for w in (index or {}).get("words") or []
             if isinstance(w, dict) and not w.get("filler")]
    keep = (edl or {}).get("keep") or []
    db, fs = audio_rms(path) if path else (None, None)
    if words and keep:
        # one span or many: the joins (none for one span), the opening and
        # the ending are judged (a single-span short skipped the opening
        # check and never had its ending read)
        try:
            import timeline as tl_mod
            blocks = [b for b in tl_mod.program_blocks(edl)]
        except Exception:  # noqa: BLE001
            blocks = []
        for prev, nxt in zip(blocks, blocks[1:]):
            if prev.get("kind") != "footage" or nxt.get("kind") != "footage":
                continue
            out_pt, in_pt = float(prev["src_end"]), float(nxt["src_start"])
            if abs(out_pt - in_pt) < 0.011:
                continue      # one continuous run split for a camera: no cut
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
            kind = "end" if side == "out" else "start"
            kept_part = (float(w["t0"]), edge) if kind == "end" \
                else (edge, float(w["t1"]))
            if audit.silenced(kept_part[0], kept_part[1], duck):
                continue      # set_volume silences what the cut keeps
            snap, measured = _sound_safe_edge(w, edge, kind, keep, words,
                                              cut_level, index)
            if snap is False:
                continue      # the source is quiet there: no mid-word cut
            k = next((i for i, kk in enumerate(keep)
                      if abs(float(kk[1 if side == "out" else 0]) - edge)
                      < 0.011), None)
            op = None
            if k is not None:
                new = [list(x) for x in keep]
                new[k][1 if side == "out" else 0] = snap
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
                fix=(f"move the {side}-point to {snap:.2f}s, where the "
                     "source's sound is quiet (measured — the same edge the "
                     "keep tools and the render's MID-WORD AUDIT name)"
                     if measured else
                     f"move the {side}-point past the word's "
                     f"{'end' if side == 'out' else 'start'} ({snap:.2f}s: "
                     "its transcript edge, padded as the keep tools pad it "
                     "without the sound; check_speech it)"),
                t=t, op=op))
        # the last frame: a short that ends inside a word ends on a
        # fragment ("Ye-" before the end card)
        last = float(keep[-1][1])
        w_end = next((w for w in words if float(w["t0"]) + 0.03 < last
                      < float(w["t1"]) - 0.03), None)
        # The ASR word's end runs 0.1-0.5 s late on many sources: the
        # render's own sound at the cut decides (Oct 22 Kamath run: lv 3803,
        # pd 3804 'consciousness', kd 3805 'thoughts.' ended in the pause
        # after the word, and the offered op cut the payoff word away).
        t_last = next((float(b["out_end"]) for b in reversed(blocks)
                       if b.get("kind") == "footage"
                       and abs(float(b.get("src_end", -1)) - last) < 0.011),
                      None)
        if w_end is not None and t_last is not None:
            lv = _level(db, fs, t_last - JOIN_WIN_S - 0.01, t_last - 0.01)
            if lv is not None and lv < JOIN_LOUD_DB:
                w_end = None              # the word has ended: a clean end
        if w_end is not None:
            t1 = float(w_end["t1"])
            nxt = next((w for w in words if float(w["t0"]) >= t1 - 0.01
                        and w is not w_end), None)
            prv = [w for w in words if float(w["t1"]) <= float(w_end["t0"])
                   + 0.01 and w is not w_end]
            # a word that opens a new sentence is a stray start; one that
            # carries the sentence on is usually the payoff
            opens = not prv or bool(re.search(
                r"[.?!]$", str(prv[-1].get("w") or ""))) or \
                float(w_end["t0"]) - float(prv[-1]["t1"]) >= 0.4
            finish = round(t1 + 0.04, 2)
            new = [list(x) for x in keep]
            if not opens and (nxt is None
                              or float(nxt["t0"]) >= finish + 0.06):
                # finish the word: the line's last word is its payoff
                new[-1][1] = finish
                fix = (f"finish the word: end the last span at {finish:.2f}s "
                       "(its end in the transcript; check the sound) — or "
                       "before it, if the thought ends earlier")
            else:
                new[-1][1] = round(float(w_end["t0"]) - 0.02, 2)
                fix = (f"end the last span before the word "
                       f"({new[-1][1]:.2f}s) — or after it, if the thought "
                       "needs it")
            op = None
            if new[-1][1] - float(keep[-1][0]) >= 0.2:
                op = {"action": "set", "layer": "keep", "value": new}
            out.append(_f(
                "fix", "words", "ends_mid_word",
                f"the short ends inside '{w_end.get('w')}' (source "
                f"{float(w_end['t0']):.2f}-{t1:.2f}s) and the sound is still "
                "on at the cut: the last thing heard before the end card is "
                "a fragment.",
                fix=fix, t=program_s, op=op))
        first = keep[0][0]
        prev = [w for w in words if float(w["t1"]) <= float(first) + 0.02]
        nxt = [w for w in words if float(w["t0"]) >= float(first) - 0.02]
        if prev and nxt:
            p = prev[-1]
            gap = float(nxt[0]["t0"]) - float(p["t1"])
            if gap < 0.25 and not re.search(r"[.?!]$", str(p.get("w") or "")) \
                    and not _restarts(prev, nxt):
                out.append(_f(
                    "fix", "words", "opens_mid_sentence",
                    f"the short opens on '{nxt[0].get('w')}' {gap:.2f}s after "
                    f"'{p.get('w')}': mid-sentence, the hook lands without "
                    "its start.",
                    fix="start on the sentence's first word, or open on a "
                        "line that stands alone", t=0.0))
    out += _negation_findings(edl, index)
    pw = plan.get("words") or []
    if duck:
        # a word set_volume silences is not the last word heard
        pw = [w for w in pw if not audit.silenced(w.get("s0"), w.get("s1"),
                                                  duck)]
    if pw and program_s:
        last_w = max(pw, key=lambda w: float(w["t1"]))
        hold = program_s - float(last_w["t1"])
        if 0 < hold < PAYOFF_HOLD_S - 1e-3:
            out.append(_payoff_hold_finding(edl, index, last_w, hold,
                                            program_s))
    return out


# The live tail a short-of-hold ending is extended to (inside the owner's
# 0.6-1.2 s), and the margin kept before the source's next word.
PAYOFF_TARGET_S = 0.8
NEXT_WORD_MARGIN_S = 0.05


def _word_key(w):
    return re.sub(r"[^\w']", "", str((w or {}).get("w") or "")
                  .replace("’", "'")).casefold()


def _restarts(before, after):
    """Does the short open on the speaker restarting the phrase said just
    before the cut ('I think | I think failure is bad', 'Three | three
    quite interesting')? The restart is where the sentence starts again:
    no mid-sentence opening (CHM 2013 run, 23 Oct 2026: 4500 and 4505 were
    told they open mid-sentence on such a restart)."""
    a = [_word_key(w) for w in before[-3:]]
    b = [_word_key(w) for w in after[:3]]
    return any(len(a) >= k and len(b) >= k and a[-k:] == b[:k] and all(a[-k:])
               for k in (3, 2, 1))


def _negation_findings(edl, index):
    """caption_carry.hidden_negations as critic findings: a negation the
    screen drops while the rest of its sentence shows reads as the opposite
    claim (CHM 2013, 4505 v31: "it's good." for "I don't think it's
    good"). Where a hook-tier title's caption zone hides it and the hook
    can end before the sentence, the op ends it there."""
    try:
        import caption_carry
        import timeline as tl_mod
        tl = tl_mod.Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                             edl.get("speed") or [])
        negs = caption_carry.hidden_negations(edl, index, tl)
    except Exception:  # noqa: BLE001 — a lens never breaks the critique
        return []
    out = []
    for g in negs:
        op = None
        m = re.match(r"end '([^']+)' at ([0-9.]+)s", g.get("fix") or "")
        if m and m.group(1) == g.get("owner"):
            op = {"action": "upsert", "layer": "motion", "id": m.group(1),
                  "value": {"id": m.group(1), "end": float(m.group(2))}}
        out.append(_f(
            "blocking", "words", "negation_hidden",
            f"said \"{g['said']}\" but the screen shows \"{g['reads']}\" "
            f"without '{g['negation']}': it reads as the opposite claim "
            f"({g['cause'].split(' — ', 1)[-1]}).",
            fix=g["fix"], t=g["at"], id=g.get("owner"), op=op))
    return out


def _source_tail_room(edl, index, last_w):
    """Seconds of source after the last heard word before the next word
    starts (any word, a filler too), when the programme ends on the footage
    that word is in; None when that cannot be read."""
    keep = (edl or {}).get("keep") or []
    try:
        s0, s1 = float(last_w["s0"]), float(last_w["s1"])
        k0, k1 = float(keep[-1][0]), float(keep[-1][1])
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    if not (k0 - 0.01 <= s1 <= k1 + 0.05):
        return None
    try:
        import timeline as tl_mod
        blocks = tl_mod.program_blocks(edl)
        tail = blocks[-1] if blocks else None
        if not tail or tail.get("kind") != "footage" or \
                abs(float(tail.get("src_end", -1)) - k1) > 0.011:
            return None
    except Exception:  # noqa: BLE001
        return None
    later = [float(w["t0"]) for w in (index or {}).get("words") or []
             if isinstance(w, dict) and w.get("t0") is not None
             and float(w["t0"]) > s0 + 0.01 and float(w["t0"]) >= s1 - 0.02]
    if later:
        return max(0.0, min(later) - s1)
    try:
        dur = float(((index or {}).get("video") or {}).get("duration"))
        return max(0.0, dur - s1)
    except (TypeError, ValueError):
        return None


def _payoff_hold_finding(edl, index, last_w, hold, program_s):
    """The payoff holds 0.6-1.2 s of live footage after the last word, or
    the short ends on its last word (the owner's ending rule, 22 Oct). Where
    the source pauses long enough, the op extends the final keep into that
    pause; where the next word follows at once there is no live tail to
    give, and ending on the last word is the rule's other half — an
    advisory, not a fix (Altman run, 23 Oct: s01, s09, s21, s22 and s23
    were asked again and again to 'extend the keep' into a next word that
    started 0.0-0.4 s after the last one)."""
    room = _source_tail_room(edl, index, last_w)
    s1 = float(last_w.get("s1") or 0.0)
    if room is not None and room - NEXT_WORD_MARGIN_S < PAYOFF_HOLD_S:
        return _f(
            "advisory", "words", "payoff_hold_short",
            f"{hold:.2f}s after the last word before the end card, and the "
            f"source's next word starts {room:.2f}s after it: there is no "
            "live tail to hold, so the short ends on its last word.",
            fix="accept it (the ending rule: a 0.6-1.2 s live hold, or end "
                "on the last word); never a freeze-frame, a slowed tail or "
                "a still before the end card. Do not extend the keep into "
                "the next word.", t=program_s)
    op = None
    fix = (f"hold {PAYOFF_HOLD_S - hold + 0.05:.2f}s more on LIVE footage: "
           "extend the final keep into the pause after the word (the layers "
           "that ran to the end follow it); with no live tail, end on the "
           "last word — never a freeze-frame or a slowed tail before the "
           "end card")
    if room is not None:
        keep = [list(k) for k in (edl or {}).get("keep") or []]
        target = min(PAYOFF_TARGET_S, room - NEXT_WORD_MARGIN_S)
        new_end = round(s1 + target, 2)
        if keep and new_end > float(keep[-1][1]) + 0.01:
            keep[-1][1] = new_end
            op = {"action": "set", "layer": "keep", "value": keep}
            fix = (f"extend the final keep to {new_end:.2f}s: the source "
                   f"pauses {room:.2f}s after the word, so the payoff holds "
                   f"{target:.2f}s of live footage (the layers that ran to "
                   "the end follow it) — never a freeze-frame or a slowed "
                   "tail before the end card")
    return _f("fix", "words", "payoff_hold_short",
              f"{hold:.2f}s after the last word before the end card "
              f"(<{PAYOFF_HOLD_S}s): the payoff does not breathe.",
              fix=fix, t=program_s, op=op)


def lens_type(report):
    """The type ladder from the render's own measurements (render_qc type
    caps), when the render carries them."""
    out = []
    qc = (report or {}).get("picture_qc") or (report or {}).get("qc") or {}
    caps = ((qc.get("type") or {}).get("caps")) or {}
    measure = {}
    if caps.get("captions") is not None:
        measure["captions_cap"] = caps["captions"]
        # judged at the reading size: a stack cue's small lines, not its
        # one emphasised word (Earnings 4562 v36: 6.0% measured, ~2% read)
        read = caps.get("captions_read")
        if read is not None:
            measure["captions_read_cap"] = read
        small = float(read if read is not None else caps["captions"])
        if small < 0.025:
            out.append(_f("fix", "type", "type_too_small",
                          f"captions read at {100 * small:.1f}% of the frame "
                          "height (< 2.5%)"
                          + (f" (their largest line {100 * caps['captions']:.1f}%)"
                             if read is not None else "")
                          + ": unreadable on a phone.",
                          fix="raise the caption size (set_caption_style "
                              "size_scale)"))
    po = caps.get("payoff")
    if po and len(po) == 4:
        pid, pc, big, bc = po
        measure["payoff"] = [pid, pc]
        if big != pid and pc < bc - 1e-4:
            out.append(_f("fix", "type", "payoff_not_largest",
                          f"the payoff '{pid}' ({100 * pc:.1f}%) is smaller "
                          f"than '{big}' ({100 * bc:.1f}%).",
                          fix=f"make '{pid}' the largest lockup (a long "
                              "single word is width-bound: role='condensed' "
                              f"or a shorter word), or step '{big}' down",
                          id=pid))
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
        near = [w for w in words if a - reach_s <= w["t0"] <= a + reach_s
                and _tokens(w.get("w"))[:1] == toks[:1]]
        # the occurrence nearest the graphic's start, not the first in
        # reach (Kamath run: x1 3796 and pc 3800 payoff slams were keyed
        # to an earlier saying of their word)
        hit = min(near, key=lambda w: abs(float(w["t0"]) - a)) if near \
            else None
        if hit is not None:
            out.append((m, float(hit["t0"]), hit.get("w")))
    return out


def _box_px(box, w, h):
    x0, y0, x1, y1 = box
    return (max(0, int(x0 * w)), max(0, int(y0 * h)),
            min(w, int(math.ceil(x1 * w))), min(h, int(math.ceil(y1 * h))))


def visual_onset(path, info, box, t_expect, *, before=0.4, after=0.9,
                 width=270, lo=None, hi=None):
    """The program second where the picture inside ``box`` first changes
    like a reveal near ``t_expect`` (a change well above the box's own
    motion before it), or None. ``lo``/``hi`` (program seconds) bound where
    the reveal may be: the graphic's own entrance, never its exit or a later
    word's reveal (the strongest change in the box is often one of those,
    and the old half-of-the-peak rule then named it the landing)."""
    import numpy as np
    import qa_metrics
    fps = float(info.get("fps") or 30.0)
    W, H = info.get("width") or 1080, info.get("height") or 1920
    h = int(round(H * width / float(W) / 2)) * 2
    lo_t = t_expect - before if lo is None else max(t_expect - before,
                                                     float(lo))
    hi_t = t_expect + after if hi is None else min(t_expect + after,
                                                    float(hi))
    if hi_t - lo_t < 2.0 / fps:
        return None
    f0 = max(0, int(round((t_expect - before) * fps)))
    f1 = int(round(hi_t * fps))
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
    # the box's own motion before the word (and before the window opens)
    k_exp = int(round(min(t_expect, lo_t + 2.0 / fps) * fps)) - idx[0]
    base = d[:max(1, k_exp - 2)]
    floor = float(np.median(base)) if base else 0.0
    window = [(i, v) for i, v in enumerate(d)
              if lo_t - 1e-6 <= idx[i + 1] / fps <= hi_t + 1e-6]
    peak = max((v for _i, v in window), default=0.0)
    thr = max(2.5, 4.0 * floor, 0.5 * peak)
    if peak < max(2.5, 4.0 * floor):
        return None
    for i, v in window:
        if v >= thr:
            return idx[i + 1] / fps
    return None


def _reveal_times(m):
    """Program seconds at which a word-timed item draws its printed words
    (a lockup's rows, a typeset's or marker_text's words on their spoken
    onsets), sorted; [] for an item that draws whole at its start."""
    try:
        import caption_carry
        r = caption_carry.lockup_reveals(m) or caption_carry.onset_reveals(m)
        base = float(m["start"]) - float(m.get("phase_s") or 0.0)
        return sorted(base + float(x) for x in r or [])
    except Exception:  # noqa: BLE001 — the item then draws at its start
        return []


# A graphic whose word appears this long before the word is said was on
# screen when it was said: nothing of it can land late on that word.
ONSCREEN_BEFORE_S = 0.4
# A landing is the first strong change within this long of the moment the
# word is drawn (an entrance: pop, slam, a reveal's rise); a change later
# than that is the box's own picture (a gesture, a later word, the exit).
ENTRANCE_S = 0.3


def landing_window(m, t_word, fps):
    """(lo, hi) program seconds in which the graphic draws the matched word,
    or None when it was already on screen when the word was said. lo is the
    frame before the word appears (its start, or for a word-timed item the
    reveal that draws it: the last one by the word's onset, else the first,
    which is then late); hi
    stops ENTRANCE_S after that, before the next word's reveal and before
    the exit, so none of those (nor a hand moving through the box later) is
    read as this word's landing (Altman run, 23 Oct: 54 late_word findings
    on 26 finals, the typesets' later emphasis words, the slams' exit cuts
    and a gesture named as late landings, and their ops moved the graphics
    off their words)."""
    fr = 1.0 / float(fps or 30.0)
    start, end = float(m["start"]), float(m["end"])
    reveals = _reveal_times(m)
    appear, nxt = start, None
    if reveals:
        by_word = [r for r in reveals if r <= t_word + LATE_FRAMES / 30.0]
        appear = by_word[-1] if by_word else reveals[0]
        later = [r for r in reveals if r > appear + 2 * fr]
        nxt = later[0] if later else None
    if appear < t_word - ONSCREEN_BEFORE_S:
        return None
    lo = appear - fr
    hi = min(end - 2 * fr, appear + ENTRANCE_S)
    if nxt is not None:
        hi = min(hi, nxt - fr)
    if hi - lo < 3 * fr:
        return None
    return lo, hi


def _program_cuts(edl, index):
    """Program seconds where the picture cuts: the timeline's own joins and
    insert edges, and the indexed camera cuts inside the kept footage."""
    import timeline as tl_mod
    try:
        tl = tl_mod.Timeline((edl or {}).get("keep") or [],
                             (edl or {}).get("inserts") or [],
                             (edl or {}).get("speed") or [])
    except Exception:  # noqa: BLE001
        return []
    cuts = set(tl_mod.cut_points(tl))
    for shot in (index or {}).get("shots") or []:
        try:
            t = tl.src_to_out(float(shot["start"]))
        except (KeyError, TypeError, ValueError):
            continue
        if t is not None and t > 1e-3:
            cuts.add(round(t, 3))
    return sorted(cuts)


# A change in a graphic's box this close to a picture cut is the footage
# cutting under it, not the graphic landing.
CUT_ONSET_FRAMES = 2.5


_FAST_ENTRANCES = ("pop", "slam", "spring")


def _fast_entrances(m):
    """The template's own entrances that land at once (the critic named
    'cut', which no template's entrance takes: add_motion_graphic refused
    word_slam entrance='cut', 4586 in the Altman run)."""
    try:
        import motion_templates
        p = (motion_templates.spec(m.get("template") or "").get("params")
             or {}).get("entrance") or {}
        vals = list(p.get("values") or [])
    except Exception:  # noqa: BLE001
        return []
    cur = ((m.get("params") or {}).get("entrance"))
    return [e for e in _FAST_ENTRANCES if e in vals and e != cur]


def _as_rendered(edl, index):
    """The EDL with each word-timed item's reading recomputed from the
    speech, as the render recomputes it (motion_layer): a stored reading
    can be stale after a re-cut, and its reveals would put the landing
    window on the wrong frames. The stored EDL is never changed."""
    if not index or not (edl or {}).get("motion"):
        return edl
    try:
        import copy
        import caption_carry
        import timeline as tl_mod
        timed = dict(edl, motion=copy.deepcopy(edl.get("motion")))
        tl = tl_mod.Timeline(timed.get("keep") or [],
                             timed.get("inserts") or [],
                             timed.get("speed") or [])
        caption_carry.attach_readings(timed, index, tl)
        return timed
    except Exception:  # noqa: BLE001 — the stored readings stand
        return edl


def lens_late_words(path, info, edl, plan, budget_s=20.0, index=None):
    out = []
    fps = float(info.get("fps") or 30.0)
    W, H = info.get("width") or 1080, info.get("height") or 1920
    deadline = time.monotonic() + budget_s
    cuts = _program_cuts(edl, index)
    timed = _as_rendered(edl, index)
    for m, t_word, word in landing_targets(timed, plan.get("words") or []):
        if time.monotonic() > deadline:
            break
        box = _item_box(m, W, H)
        if not box:
            continue
        try:
            window = landing_window(m, t_word, fps)
        except (KeyError, TypeError, ValueError):
            continue
        if window is None:
            continue      # on screen before the word was said
        seen = visual_onset(path, info, box, t_word, lo=window[0],
                            hi=window[1])
        if seen is None:
            continue
        if any(abs(seen - c) <= CUT_ONSET_FRAMES / fps for c in cuts):
            continue      # the picture cut under the box (Kamath gk 3798)
        late = int(round((seen - t_word) * fps))
        # LATE_FRAMES is counted at 30 fps: a 59.94 fps final is held to
        # the same 67 ms as its 30 fps preview
        if seen - t_word > LATE_FRAMES / 30.0 + 1e-6 and late > LATE_FRAMES:
            start = round(float(m["start"]) - late / fps, 2)
            op, fix = None, (
                f"start {m['id']} on the word: move it {late} frames "
                "earlier (its landing should sit on the onset, a frame "
                "early at most)")
            if start >= 0.0:
                # only the start moves: the end is where its words finish,
                # and an end moved with it cut the last word short or
                # unmuted it (4592 ts_not, 4599 ts_local)
                op = {"action": "upsert", "layer": "motion", "id": m["id"],
                      "value": {"id": m["id"], "start": start}}
            else:
                # it already opens the short (kd 3805: the op asked for a
                # start at -0.64 s): only a faster entrance lands it sooner
                fast = _fast_entrances(m)
                fix = (f"{m['id']} already starts at {float(m['start']):g}s, "
                       "so it cannot move earlier: "
                       + (f"a faster entrance ({' or '.join(repr(e) for e in fast)}) "
                          "lands it on the word" if fast else
                          "it lands as fast as its template draws; open on "
                          "the word itself or accept it"))
            out.append(_f(
                "fix", "motion", "late_word",
                f"'{word}' is said at {t_word:.2f}s but graphic {m['id']} "
                f"lands {late} frames later ({seen:.2f}s).",
                fix=fix, t=t_word, id=m["id"], op=op))
    return out


MATTE_KINDS_FLICKER = ("glow", "outline", "poster", "isolate", "echo",
                       "exposure", "negate")


def toggle_share(frames, thr=12.0, block=24, span=6, min_toggles=2,
                 ignore=None):
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
    if ignore is not None:
        # a talking face toggles its own pixels (lips, eyes, a nod): that
        # is the speaker, not the device's edge
        tog &= ~np.asarray(ignore, bool)[None]
    out = []
    n = span - 2
    for i in range(0, tog.shape[0] - n + 1):
        cnt = tog[i:i + n].sum(axis=0) >= min_toggles
        h, w = cnt.shape
        hb, wb = max(1, h // block), max(1, w // block)
        t = cnt[:hb * block, :wb * block].reshape(hb, block, wb, block)
        out.append(float(t.mean(axis=(1, 3)).max()))
    return out


def _face_ignore(path, f_in, f_ref, w, h):
    """A (h, w) bool mask of the speaker's face boxes (expanded) on two
    frames of the render, or None: the face's own motion is set aside
    when edges are judged for flicker (a matte or depth edge flickers on
    the outline, not on the lips)."""
    import numpy as np
    import qa_metrics
    try:
        idx, frames = qa_metrics.read_frames(path, size=(w, h),
                                             frames={int(f_in), int(f_ref)})
    except Exception:  # noqa: BLE001
        return None
    m = np.zeros((h, w), bool)
    for f in frames:
        for x0, y0, x1, y1, _k in faces_on(np.asarray(f)):
            if x1 - x0 < 0.06:
                continue
            cx, bw, bh = (x0 + x1) / 2.0, (x1 - x0) * 0.6, (y1 - y0)
            a0, a1 = int(max(0.0, cx - bw) * w), int(min(1.0, cx + bw) * w)
            b0 = int(max(0.0, y0 - 0.15 * bh) * h)
            b1 = int(min(1.0, y1 + 0.35 * bh) * h)
            m[b0:b1, a0:a1] = True
    return m if m.any() else None


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
        # the same shot on BOTH sides of the window: a speaker who
        # gestures more inside the window than in the calm second before
        # it is not a flickering device (measured: a dof window toggled
        # less than the same window rendered without it)
        span = min(1.5, b - a)
        refs = [r for r in ((max(0.0, a - span), a),
                            (b, min(prog, b + span))) if r[1] - r[0] >= 0.4]
        if not refs:
            continue
        rng = set(range(int(round(a * fps)) + 1, int(round(b * fps)) - 1))
        rngs = [set(range(int(round(x * fps)), int(round(y * fps))))
                for x, y in refs]
        idx, frames = qa_metrics.read_frames(
            path, size=(width, h), gray=True,
            frames=rng.union(*rngs))
        fin = [f for i, f in zip(idx, frames) if i in rng]
        frefs = [[f for i, f in zip(idx, frames) if i in r] for r in rngs]
        frefs = [x for x in frefs if len(x) >= 4]
        if len(fin) < 4 or not frefs:
            continue
        ign = _face_ignore(path, sorted(rng)[len(rng) // 2],
                           sorted(rngs[0])[len(rngs[0]) // 2], width, h)
        inside = float(np.median(toggle_share(fin, ignore=ign)))
        base = max(float(np.median(toggle_share(x, ignore=ign)))
                   for x in frefs)
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


# Devices that widen the range, keyed by the moment tags that earn them:
# (tag, verb, the device signature it writes, the idea). Each is a starting
# idea bound to the moment, never a recipe: an idea whose device this short
# or one of the project's earlier shorts already uses is passed over, so a
# run of shorts is pushed toward fresh vocabulary, not one house style.
FRESH_IDEAS = (
    ("number", "add_ui_scene", "mg:ui_scene", "a UI proof of the number (a "
     "counter that lands on the spoken figure, the data card it comes "
     "from)"),
    ("number", "add_motion_graphic", "mg:", "the number as the picture: a "
     "to-scale comparison or a fill that grows to the figure"),
    ("contrast", "add_depth_effect", "depth_rack_focus", "a rack focus that "
     "pulls from him to the world on the turn word (kind='rack_focus')"),
    ("contrast", "add_subject_effect", "subject_isolate", "the world drops "
     "away on the turn: he stays in colour, the room goes mono for the "
     "phrase (kind='isolate')"),
    ("negation", "set_grade_window", "grade_section", "the world drains to "
     "mono and colour lands back on the answer (a grade window ending on "
     "the payoff)"),
    ("negation", "add_accent", "accent_negative", "one negative frame ON the "
     "'not' — a hit, not a strobe (kind='negative')"),
    ("payoff", "add_subject_effect", "subject_poster", "the speaker becomes "
     "the graphic on the payoff: a poster on the accent field, held "
     "through the hold"),
    ("payoff", "add_depth_effect", "depth_parallax", "freeze the frame after "
     "the payoff's last word and drift through it in 2.5D during the pause "
     "(kind='parallax')"),
    ("payload", "add_type_window", "composite_window", "the hero word as a "
     "window onto the next shot (letters cut into the picture)"),
    ("payload", "source_broll", "broll_", "a cutaway that PROVES the line: "
     "the named thing on film or an archive still that moves (graded to "
     "his shot)"),
    ("emotion", "add_time_effect", "time_echo", "an echo trail on the "
     "gesture that carries the feeling (kind='echo', on a real movement "
     "only)"),
    ("emotion", "add_subject_effect", "subject_glow", "he glows against a "
     "darkened room for the line that costs him something (kind='glow')"),
    (None, "add_type_window", "composite_window", "the hero word as a window "
     "onto the next shot (letters cut into the picture)"),
    (None, "add_depth_effect", "depth_point_cloud", "the figure as data for "
     "one beat: a LiDAR-style scan of him, then the real shot "
     "(kind='point_cloud', 1-1.5 s, in a pause)"),
)


def _uses(sigs, prefix):
    return any(str(x).startswith(prefix) for x in sigs or ())


def lens_range(edl, plan, previous=None, max_ideas=3):
    """House style (the same device set as earlier shorts of this project)
    and strong moments with nothing on them, with fresh, bound ideas."""
    out = []
    mine = device_signature(edl)
    prev = [device_signature(p) for p in previous or [] if p]
    seen = set().union(*prev) if prev else set()
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
    ideas, used, used_verbs = [], set(), set()
    moments = sorted(plan.get("moments") or [],
                     key=lambda m: -float(m.get("strength") or 0))
    payoff = plan.get("payoff") or {}

    def pick(tags, allow_seen):
        for tag, verb, sig, idea in FRESH_IDEAS:
            if sig in used or verb in used_verbs or (tag and tag not in
                                                         tags):
                continue
            if _uses(mine, sig) or (not allow_seen and _uses(seen, sig)):
                continue
            return verb, sig, idea
        return None
    for m in moments:
        if len(ideas) >= max_ideas:
            break
        t = float(m["t"])
        if any(a <= t <= b for a, b in busy):
            continue
        if any(abs(t - i["t"]) < 0.4 for i in ideas):
            continue                  # one idea per moment
        tags = set(m.get("tags") or [])
        if payoff and abs(t - float(payoff.get("t") or -9)) < 0.05:
            tags.add("payoff")
        if m.get("kind") not in ("stress", "word") and "payoff" not in tags:
            continue
        # fresh first: a device neither this short nor the run has used
        got = pick(tags, False) or pick(tags, True)
        if got is None:
            continue
        verb, sig, idea = got
        word = m.get("word_raw") or m.get("word") or ""
        ideas.append({"t": round(t, 2), "word": word, "verb": verb,
                      "idea": idea, "bind": m.get("bind"),
                      "fresh_for_run": not _uses(seen, sig)})
        used.add(sig)
        used_verbs.add(verb)
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


def lens_ending(path, edl, info, *, final, report=None, budget_s=10.0):
    """STALL BEFORE END CARD (owner, 22 Oct 2026): black or a held picture
    where the programme hands over to the card — render_qc.boundary_stall,
    read from the render's own picture check when it carries it, else
    measured here (about two seconds of 64 px frames)."""
    import render_qc
    qc = (report or {}).get("picture_qc") or {}
    rows = qc.get("stall")
    if rows is None:
        import config
        import renderer
        from timeline import Timeline
        e = edl or {}
        fps = float(info.get("fps") or 30.0)
        tl = Timeline(e.get("keep") or [], e.get("inserts") or [],
                      e.get("speed"))
        prog = renderer.program_render_s(tl, fps)
        outro = float(getattr(config, "OUTRO_DURATION_S", 5.0) or 0.0)
        p = {"program_s": prog, "fps": fps, "W": int(info["width"]),
             "H": int(info["height"]),
             "endcard": ({"s": outro} if final and renderer.endcard_path()
                         else None),
             "tail": render_qc.tail_plan(e, tl, prog)}
        rows = render_qc.boundary_stall(path, p,
                                        time.monotonic() + budget_s)
    out = []
    for kind, a, b, code, words in rows or []:
        sev = ("blocking" if code == "join" else
               "advisory" if code in ("fade_out", "footage") else "fix")
        out.append(_f(sev, "motion", "stall_before_end_card",
                      f"STALL BEFORE END CARD {a:.2f}-{b:.2f}s: "
                      f"{b - a:.2f}s of "
                      f"{'black' if kind == 'black' else 'a held picture'} "
                      "where the programme hands over to the end card.",
                      fix=words, t=a))
    return out, {"stall": rows}


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
             want_wm=False, maps=None, deep=True, board_dir=None,
             cut_level=None):
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
    got = run("words", lens_words, edl, index, plan, program_s, path,
              cut_level)
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
                        plan, index=index) or []
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
    got = run("ending", lens_ending, path, edl, info, final=final,
              report=report)
    if got:
        findings += got[0]
        measures["ending"] = got[1]
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
    if res.get("errors"):
        # a lens that could not run is not a pass: say which
        lines.append("NOT CHECKED (a lens failed; judge these by eye): "
                     + "; ".join(str(e)[:80] for e in res["errors"][:4]))
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
