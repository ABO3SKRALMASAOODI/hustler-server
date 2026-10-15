"""Track 4E: the same edit in another language (ROADMAP 4E;
INTERFACES_W4.md §5.2).

``retypeset(edl, index, lang, translation=None, srt=None, mapping=None)``
re-typesets an edit from what the USER supplies — never a machine
translation:

  translation  one translated line per spoken phrase (a list in phrase
               order, or {source phrase: translation}); each phrase keeps
               its own window, its words are spread over the phrase's
               spoken span by syllable weight
  srt          subtitle cues (the user's timing): each cue's text replaces
               the words spoken inside the cue
  mapping      {on-screen text: translation} for motion graphics and texts

Captions change through ``captions.corrections`` (scoped to each phrase's
window: the karaoke clock stays the speech's), graphics through their own
text params; no window, cut, device or timing moves — the choreography is
the original's. ``phrases(edl, index)`` lists the phrases a translation is
written against.
"""

import copy
import re

MAX_CORRECTIONS = 80
PAUSE_S = 0.35
_VOWELS = re.compile(r"[aeiouyäöüàâéèêëîïôûùœæåø]+", re.I)


def phrases(edl, index):
    """[{i, text, words, t0, t1}] of the kept transcript on the program
    clock, split at sentence ends and pauses."""
    import device_planner
    ws = device_planner.program_words(edl, index or {})
    out, cur = [], []
    for k, w in enumerate(ws):
        cur.append(w)
        end = bool(re.search(r"[.!?;:]$", str(w["w"] or "").strip()))
        gap = (ws[k + 1]["t0"] - w["t1"]) if k + 1 < len(ws) else 9.0
        if end or gap >= PAUSE_S or len(cur) >= 14:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return [{"i": i, "text": " ".join(str(w["w"]) for w in p),
             "words": [str(w["w"]) for w in p], "t0": round(p[0]["t0"], 3),
             "t1": round(p[-1]["t1"], 3)} for i, p in enumerate(out)]


def syllables(word):
    return max(1, len(_VOWELS.findall(str(word))))


def _parse_srt(text):
    cues = []
    for block in re.split(r"\n\s*\n", str(text or "").strip()):
        lines = [ln for ln in block.strip().splitlines() if ln.strip()]
        tl = next((ln for ln in lines if "-->" in ln), None)
        if not tl:
            continue
        a, b = [x.strip() for x in tl.split("-->")]

        def sec(x):
            h, m, s = x.replace(",", ".").split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
        body = " ".join(lines[lines.index(tl) + 1:]).strip()
        if body:
            cues.append({"t0": sec(a), "t1": sec(b), "text": body})
    return cues


def _corrections(phr, target):
    """Scoped corrections replacing one phrase's words with ``target``.
    The captions engine spreads unequal word counts evenly over the
    phrase's span; syllable weight is honoured by splitting the phrase into
    runs whose spans match the target words' weights."""
    src = phr["words"]
    tgt = str(target).split()
    if not src or not tgt:
        return []
    # one correction per phrase keeps the line a single caption unit
    return [{"from": " ".join(src), "to": " ".join(tgt),
             "start": max(0.0, phr["t0"] - 0.01), "end": phr["t1"] + 0.01}]


def _swap_texts(edl, mapping):
    """Replace on-screen strings by the user's mapping; returns count."""
    if not mapping:
        return 0
    import motion_templates
    n = 0
    low = {str(k).strip().lower(): v for k, v in mapping.items()}

    def tr(v):
        nonlocal n
        if isinstance(v, str) and v.strip().lower() in low:
            n += 1
            return low[v.strip().lower()]
        return v
    for m in edl.get("motion") or []:
        if not isinstance(m, dict):
            continue
        try:
            spec = motion_templates.spec(m.get("template") or "")
        except ValueError:
            spec = {"params": {}}
        for key, val in list((m.get("params") or {}).items()):
            kind = ((spec.get("params") or {}).get(key) or {}).get("type",
                                                                   "str")
            if kind in ("str", "text"):
                m["params"][key] = tr(val)
            elif kind == "list" and isinstance(val, list):
                m["params"][key] = [tr(x) for x in val]
            elif kind == "rows" and isinstance(val, list):
                m["params"][key] = [{f: tr(x) for f, x in r.items()}
                                    if isinstance(r, dict) else r
                                    for r in val]
        for k in ("footprint", "reading", "series"):
            m.pop(k, None)               # measured for the old words
    for t in edl.get("texts") or []:
        if isinstance(t, dict) and isinstance(t.get("text"), str):
            t["text"] = tr(t["text"])
    return n


def retypeset(edl, index, lang, translation=None, srt=None, mapping=None):
    """(edl, notes). See the module doc."""
    if not lang or not re.fullmatch(r"[a-z]{2}(-[A-Za-z]{2})?", str(lang)):
        raise ValueError("lang is a language code such as 'de' or 'pt-BR'")
    if translation is None and srt is None and not mapping:
        raise ValueError("give the user's translation (per phrase), an SRT "
                         "or a text mapping — Valmera never machine-"
                         "translates")
    e = copy.deepcopy(edl or {})
    notes = []
    phr = phrases(e, index)
    rules = []
    if translation is not None:
        if isinstance(translation, dict):
            by = {re.sub(r"\W+", " ", k).strip().lower(): v
                  for k, v in translation.items()}
            for p in phr:
                key = re.sub(r"\W+", " ", p["text"]).strip().lower()
                if key in by:
                    rules += _corrections(p, by[key])
        else:
            lines = [str(x) for x in translation]
            if len(lines) != len(phr):
                raise ValueError(f"the translation has {len(lines)} lines; "
                                 f"the edit has {len(phr)} spoken phrases "
                                 "(phrases() lists them)")
            for p, line in zip(phr, lines):
                rules += _corrections(p, line)
    if srt is not None:
        for cue in _parse_srt(srt):
            inside = [p for p in phr if p["t0"] >= cue["t0"] - 0.15
                      and p["t1"] <= cue["t1"] + 0.15]
            if inside:
                whole = {"words": [w for p in inside for w in p["words"]],
                         "t0": inside[0]["t0"], "t1": inside[-1]["t1"]}
                rules += _corrections(whole, cue["text"])
    if len(rules) > MAX_CORRECTIONS:
        raise ValueError(f"{len(rules)} phrases: at most {MAX_CORRECTIONS} "
                         "per edit — retypeset a shorter edit")
    caps = e.get("captions")
    if rules:
        if not isinstance(caps, dict):
            raise ValueError("this edit has no word-timed captions to "
                             "retypeset")
        caps["corrections"] = rules
        caps.pop("placement_track", None)
        notes.append(f"captions: {len(rules)} phrase(s) in {lang}, each on "
                     "its own spoken window")
    n = _swap_texts(e, mapping)
    if n:
        notes.append(f"{n} on-screen text(s) swapped from the mapping")
    return e, notes
