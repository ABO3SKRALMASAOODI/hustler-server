"""A designed open, middle and close for a single-shot clip (TRIAL_QUALITY_BUILD, 31 Oct 2026).

"Make it cinematic" on one static 10-60 s shot came back as a grade and
nothing else (users 2665, 2668, 2675; RS_ENGINE section 3, RS_HONESTY_BROLL:
"one look on a 10-60 s single shot has no hook beat, no ending beat and no
visual events; the judge caps those at about 5"). The reference playbook
(.valmera/week-1029/REFERENCE_PLAYBOOK.md section 2) says what a finished short
of one camera does instead:

* OPEN: a poster title whole on frame 0, held 2.5-3.5 s (9 of 9 hooked
  references): the clip's own first line when it is a short complete line,
  else a short line the speech repeats ("Happy Birthday!"), else the index's
  own description of the shot (2-3 words). Never invented, never a right-to-
  left script (the poster font breaks its joining), never a low-confidence
  transcript.
* MIDDLE: the camera changes every 2-4 s, landing on the clip's own beats
  (sentence and clause ends when it speaks; an even 3 s rhythm when it does
  not): cut-ins between the wide frame and a closer one (a held punch, hard
  cut back out), alternating close and medium so no framing repeats twice in
  a row. One framing never holds longer than about 4 s. No zoom effect on
  every beat: half the beats are the wide frame itself.
* CLOSE: the last 2-2.6 s on live picture, a slow push-in to the end (never a
  freeze, a slow-motion hold or a fade; brand rule).

Deterministic: no model call. Who gets it: the free trial and new
subscribers' Claude lane, a request that asks for a cinematic look (and does
not limit the change to the colours or keep the content unchanged), on a
program of 8-90 s that is one shot (at most 3 shots, or one shot holding 60%
of it), whose edit has no opening title, zooms or B-roll of its own yet. It
runs once per request, right before the request's first preview.
"""
import re

CINEMATIC = re.compile(
    r"\bc[iy]n[ae]?m+[ae]?t+[io]?c\w*|\bcin[ae]ma\w*|\bfilmic\b|\bmovie[\s-]?like\b"
    r"|\blike\s+a\s+(?:movie|film)\b|\bdramatic\b", re.I)
LIMITED = re.compile(
    r"\b(?:only|just)\s+(?:the\s+|a\s+)?(?:colou?r(?:s|ing)?|grad(?:e|ing)|look|lut|tones?)\b"
    r"|\b(?:content|footage|video|everything(?:\s+else)?)\s+(?:stays?\s+|remains?\s+|kept\s+|left\s+)?"
    r"(?:unchanged|the\s+same|as\s+(?:it\s+)?is|untouched)\b"
    r"|\bdon'?t\s+(?:change|touch|cut|edit|add)\b|\bdo\s+not\s+(?:change|touch|cut|edit|add)\b"
    r"|\bkeep\s+(?:the\s+)?(?:content|everything|it)\s+(?:the\s+same|unchanged|as\s+is)\b"
    r"|\bnothing\s+else\b", re.I)
NO_TEXT = re.compile(
    r"\bno\s+(?:text|titles?|captions?|subtitles?|words|writing)\b|\bwithout\s+(?:any\s+)?(?:text|titles?)\b"
    r"|\bdon'?t\s+(?:add|put)\s+(?:any\s+)?(?:text|titles?)\b", re.I)
NO_ZOOM = re.compile(r"\bno\s+zoom|\bwithout\s+(?:any\s+)?zoom|\bdon'?t\s+zoom|\bno\s+punch", re.I)
_RTL = re.compile(r"[֐-ࣿיִ-﷿ﹰ-﻿]")

MIN_PROG_S, MAX_PROG_S = 8.0, 90.0
MAX_SHOTS = 3
DOMINANT_SHARE = 0.6
HOOK_MIN_S, HOOK_MAX_S, HOOK_DEFAULT_S = 2.6, 3.4, 2.9
BEAT_S, BEAT_MIN_S, BEAT_MAX_S = 3.0, 2.0, 4.0
END_MIN_S, END_MAX_S = 2.0, 2.6
CLOSE, MEDIUM, PUSH = 0.16, 0.09, 0.07
TITLE_MAX_WORDS = 7
MIN_CONF = 0.6


def asks_for_it(text):
    """Does this request ask for a cinematic look, without limiting the
    change to the colours or keeping the content as it is?"""
    t = " ".join(str(text or "").split())
    return bool(t) and bool(CINEMATIC.search(t)) and not LIMITED.search(t)


def _shots_in(index, keep):
    shots = [s for s in (index or {}).get("shots") or [] if isinstance(s, dict)]
    out = []
    for s in shots:
        try:
            a, b = float(s.get("start")), float(s.get("end"))
        except (TypeError, ValueError):
            continue
        kept = sum(max(0.0, min(b, e) - max(a, s0)) for s0, e in keep)
        if kept > 0.05:
            out.append(kept)
    return out


def single_shot(index, keep, prog):
    """Is the program one shot (or nearly)?"""
    if not (MIN_PROG_S <= prog <= MAX_PROG_S):
        return False
    kept = _shots_in(index, keep)
    if not kept:
        return True
    return len(kept) <= MAX_SHOTS or max(kept) >= DOMINANT_SHARE * prog


def _clean(text):
    t = " ".join(str(text or "").replace("　", " ").split())
    return t.strip(" -—–,;:")


def _conf(words, s):
    try:
        i0, i1 = int(s.get("wi0")), int(s.get("wi1"))
    except (TypeError, ValueError):
        return None
    cs = [float(w.get("confidence")) for w in words[i0:i1 + 1]
          if isinstance(w, dict) and isinstance(w.get("confidence"), (int, float))]
    return sum(cs) / len(cs) if cs else None


def _usable_line(text, conf):
    t = _clean(text)
    n = len(t.split())
    if not t or n > TITLE_MAX_WORDS or _RTL.search(t):
        return None
    if conf is not None and conf < MIN_CONF:
        return None
    if re.search(r"[぀-ヿ㐀-鿿]", t) and len(t) > 16:
        return None
    return t


def _description_title(index):
    """2-3 words naming the shot from the index's own description ("A wooden
    boardwalk with railings ..." -> "Wooden boardwalk"), or None."""
    vs = (index or {}).get("visual_storyboard") or {}
    for ch in vs.get("chapters") or []:
        d = str((ch or {}).get("description") or "").strip()
        if not d:
            continue
        head = re.split(r"\s+(?:with|in|on|at|of|and|under|over|near|by|while|that|which|where|from|against|beside|during|through|as|is|are)\s+|[,.;:(]",
                        d, maxsplit=1)[0]
        words = [w for w in head.split() if w]
        while words and words[0].lower() in ("a", "an", "the", "this", "two", "one", "some", "close-up", "closeup", "wide"):
            words = words[1:]
        if not words or any(not re.match(r"^[A-Za-z][A-Za-z'\-]*$", w) for w in words):
            continue
        words = words[-3:]
        if len(words) == 1 and len(words[0]) < 4:
            continue
        return " ".join(w.capitalize() if i == 0 else w.lower() for i, w in enumerate(words))
    return None


def hook_text(index, keep):
    """(text, kind) for the opening title, or (None, None). Pure."""
    index = index or {}
    words = [w for w in index.get("words") or [] if isinstance(w, dict)]
    sents = [s for s in index.get("sentences") or [] if isinstance(s, dict)]
    start = float(keep[0][0]) if keep else 0.0

    def kept(s):
        try:
            a, b = float(s.get("t0")), float(s.get("t1"))
        except (TypeError, ValueError):
            return False
        return any(k0 - 0.05 <= a and b <= k1 + 0.05 for k0, k1 in keep)

    sents = [s for s in sents if kept(s)]
    if sents:
        first = sents[0]
        try:
            opens = float(first.get("t0")) - start <= 1.0
        except (TypeError, ValueError):
            opens = False
        line = _usable_line(first.get("text"), _conf(words, first))
        if opens and line and len(line.split()) >= 2:
            return line, "first_line"
        counts = {}
        for s in sents:
            ln = _usable_line(s.get("text"), _conf(words, s))
            if ln and len(ln.split()) <= 5:
                key = ln.casefold().strip("!?. ")
                counts.setdefault(key, [0, ln])
                counts[key][0] += 1
        if counts:
            n, ln = max(counts.values(), key=lambda v: v[0])
            if n >= 2 or len(sents) == 1:
                return ln.rstrip(".").strip(), "spoken_line"
    desc = _description_title(index)
    if desc:
        return desc, "shot"
    return None, None


def _program_words(edl, index):
    try:
        from timeline import Timeline
        tl = Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                      edl.get("speed") or [])
        return tl.kept_words([w for w in (index or {}).get("words") or []
                              if isinstance(w, dict) and "t0" in w and "t1" in w])
    except Exception:  # noqa: BLE001 - the even rhythm when unmappable
        return []


def beats(prog, start, end_at, program_words=None, sentences=None):
    """Cut-in times in (start, end_at): about every BEAT_S, each landing on
    the nearest sentence / clause end of the program's speech within the
    2-4 s window, else on the even rhythm. Pure."""
    events = []
    if program_words:
        import sentence_snap
        for a, b, kind in sentence_snap.boundaries(program_words, sentences):
            if kind in ("sentence", "clause") and b > a - 1e-6:
                events.append(round(min(b, a + 0.12), 2))
    out, t = [], float(start)
    while True:
        lo, hi, aim = t + BEAT_MIN_S, t + BEAT_MAX_S, t + BEAT_S
        if lo >= end_at - 0.6:
            break
        near = [e for e in events if lo <= e <= min(hi, end_at - 0.6)]
        nxt = min(near, key=lambda e: abs(e - aim)) if near else min(aim, end_at)
        if end_at - nxt < BEAT_MIN_S * 0.75:
            break
        out.append(round(nxt, 2))
        t = nxt
    if end_at - t > BEAT_MAX_S:
        # never a framing held past ~4 s before the ending beat
        out.append(round((t + end_at) / 2.0, 2))
    return out


def plan(edl, index, text):
    """{'hook': {...} | None, 'cutins': [...], 'ending': {...} | None,
    'why': str} or None when the clip does not get a designed structure.
    Pure; the EDL is read, never written."""
    edl = edl or {}
    if not asks_for_it(text):
        return None
    import schemas
    try:
        prog = float(schemas.program_duration(edl))
    except Exception:  # noqa: BLE001
        return None
    keep = [[float(a), float(b)] for a, b in edl.get("keep") or []]
    if not single_shot(index, keep, prog):
        return None
    import followup_recipes as fr
    ln = fr.lanes(edl)
    has_title = bool(ln["title"])
    has_zooms = any(ln["zooms"].values())
    broll = ln["broll"]
    if broll["inserts"] or broll["overlays"] or broll["fx"]:
        return None
    hook = None
    hook_end = 0.0
    if not has_title and not NO_TEXT.search(text or ""):
        line, kind = hook_text(index, keep)
        if line:
            hook_end = HOOK_DEFAULT_S
            if kind == "first_line":
                pw = _program_words(edl, index)
                ends = [float(w["t1"]) for w in pw if float(w["t0"]) < HOOK_MAX_S]
                if ends:
                    hook_end = max(HOOK_MIN_S, min(HOOK_MAX_S, ends[-1] + 0.25))
            hook = {"text": line, "kind": kind, "start": 0.0,
                    "end": round(min(hook_end, prog - 1.0), 2)}
            hook_end = hook["end"]
    cutins, ending = [], None
    if not has_zooms and not NO_ZOOM.search(text or ""):
        end_len = max(END_MIN_S, min(END_MAX_S, prog * 0.07))
        end_at = round(prog - end_len, 2)
        first = hook_end if hook else 0.0
        ts = beats(prog, first, end_at, _program_words(edl, index),
                   None)
        if not hook and (not ts or ts[0] > BEAT_MAX_S):
            ts = [round(min(BEAT_S, end_at - BEAT_MIN_S), 2)] + ts
        # the segments between beats: the first one after the title is a
        # cut-in (the first visual change at 2.5-3.5 s), then wide, medium,
        # wide, close ... (half the beats are the wide frame itself)
        marks = [first] + ts + [end_at]
        pattern = ((CLOSE, None, MEDIUM, None) if hook
                   else (None, CLOSE, None, MEDIUM))
        for i in range(len(marks) - 1):
            a, b = marks[i], marks[i + 1]
            st = pattern[i % len(pattern)]
            if st is None or b - a < 1.2:
                continue
            cutins.append({"start": round(a, 2), "end": round(b, 2),
                           "strength": st})
        ending = {"start": end_at, "end": round(prog, 2), "strength": PUSH}
    if not hook and not cutins and not ending:
        return None
    why = ("a single-shot clip asked to look cinematic: "
           + ", ".join(x for x in (
               f'an opening title "{hook["text"]}" on frame 0' if hook else "",
               f"{len(cutins)} cut-ins on its beats" if cutins else "",
               "a slow push-in to the end on live picture" if ending else "")
               if x))
    return {"hook": hook, "cutins": cutins, "ending": ending,
            "prog": round(prog, 2), "why": why}


def _vertical(edl):
    frame = (edl or {}).get("frame") or {}
    ratio = str((frame.get("ratio") if isinstance(frame, dict) else "") or "9:16")
    return ratio in ("9:16", "4:5", "3:4")


def apply(ctx, text, run):
    """Write the plan with the editor's own tools. ``run(name, args)`` runs
    one tool and returns its result text. Returns the line for the tool
    result ('' when nothing was written)."""
    try:
        edl = ctx.latest_edl()["json"]
    except Exception:  # noqa: BLE001
        return ""
    p = plan(edl, getattr(ctx, "index", None) or {}, text)
    if not p:
        return ""
    import first_cut
    done = []
    if p["hook"]:
        params = first_cut.hook_params(p["hook"]["text"], None, _vertical(edl))
        params["entrance"] = "cut"      # whole on frame 0
        res = run("add_motion_graphic", {
            "template": "hook_title", "start": p["hook"]["start"],
            "end": p["hook"]["end"], "params": params, "mute_captions": True,
            "purpose": "the opening title of a single-shot cinematic clip "
                       "(references: a poster title whole by 0.3 s, held "
                       "2.5-3.5 s)"})
        if str(res or "").startswith("EDL v"):
            done.append(f'the opening title "{p["hook"]["text"]}" '
                        f'(0-{p["hook"]["end"]:g}s)')
    n = 0
    for c in p["cutins"]:
        res = run("add_zoom", {
            "start": c["start"], "end": c["end"], "strength": c["strength"],
            "mode": "punch",
            "purpose": "a cut-in on the clip's beat: the camera changes "
                       "every 2-4 s on a single shot"})
        if str(res or "").startswith("EDL v"):
            n += 1
    if n:
        done.append(f"{n} cut-ins on its beats")
    if p["ending"]:
        e = p["ending"]
        res = run("add_zoom", {
            "start": e["start"], "end": e["end"], "strength": e["strength"],
            "mode": "push_in",
            "purpose": "the ending beat: a slow push-in on live picture to "
                       "the end (never a freeze)"})
        if str(res or "").startswith("EDL v"):
            done.append(f"a slow push-in over the last {e['end'] - e['start']:.1f}s")
    if not done:
        return ""
    return ("\nDESIGNED STRUCTURE (single shot, cinematic ask): added "
            + "; ".join(done) + ". These are part of the look the user asked "
            "for: mention them in one short clause of the reply.")
