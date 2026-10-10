"""What a short's speech offers to SHOW: the beat candidates in its words.

Owner judging (Oct 2026): the showcase shorts had dropped the graphics that
only re-typeset the captions, and in doing so left dead stretches exactly
where the argument lives — Thiel 14.5-25.0 s ("a narrow cone of progress
around the world of bits... computers, internet, mobile"), Jobs 22.5-37.2 s
("injecting some liberal arts into these computers. Let's get proportionally
spaced fonts... Let's get multiple fonts... Let's get graphics"). The lines
that most deserve a designed beat — a thesis, a spoken list or triad, a
named product or place, a number — are findable in the words alone:

- ``lists``: three or more short items separated by commas, 'and' or 'or'
  ("rockets and supersonic aviation and the Green Revolution agriculture
  and underwater cities and new medicines"; "computers, internet, mobile").
  An item is a short noun phrase (at most ITEM_MAX_CONTENT content words);
  a chunk that carries a figure belongs to a stat run, not a list.
- ``triads``: three sentences that open the same way inside TRIAD_WINDOW_S
  (anaphora: "Let's get… / Let's get… / Let's get…"); each item is what
  follows the opener up to its first function word.
- ``numbers``: spoken figures (worker/number_reveal.py reads digits and
  number words) worth a counter — digits, 10 or more, a percentage or a
  scale — and adjacent pairs said as a RANGE ("30, 40 fonts" -> 30-40).
- ``names``: proper nouns inside a sentence (a product, place, person or
  event: "LISA", "Green Revolution") — capitalised words that do not open
  a sentence and are not function words.
- ``claims``: sentences carrying a claim cue ("I think…", "it's not…",
  "the problem is…", "should", "never") — the thesis lines.

Pure functions over program words ({w, t0, t1} on one clock, in order);
no I/O. Used by the edit review (beat coverage, showable moments) and the
beat planner (suggest_motion_beats).
"""

from __future__ import annotations

import re

ITEM_MAX_CONTENT = 3          # an item is a short noun phrase
ITEM_MAX_WORDS = 5
LIST_MIN_ITEMS = 3
TRIAD_MIN = 3
TRIAD_WINDOW_S = 10.0
TRIAD_ITEM_MAX_WORDS = 4
RANGE_GAP_S = 1.2             # '30, 40': the second figure follows this fast
NUMBER_MIN = 10               # a spelled small number ('three or four years') is no stat

_SENTENCE_END = re.compile(r"[.?!…]['\"”’)]*$")
_CLAUSE_SEP = re.compile(r"[,;:]['\"”’)]*$")
_STRIP = re.compile(r"^[\"'“‘(\[]+|[\"'”’)\].,!?;:…]+$")
_SEPARATORS = frozenset(("and", "or"))
_FILLER_LEADS = (("you", "know"), ("i", "mean"), ("like",), ("um",), ("uh",),
                 ("so",), ("and",), ("but",), ("also",), ("then",))
_I_FORMS = frozenset(("i", "i'm", "i've", "i'd", "i'll", "i’m", "i’ve", "i’d", "i’ll"))
# Sentence openers too common to make an anaphora (all function words are
# excluded anyway).
_COMMON_OPENERS = frozenset(("you know", "i think", "and then", "so the", "it was",
                             "this is", "that is", "that's the", "there's a"))
CLAIM_CUES = re.compile(
    r"\b(i think|i believe|the (?:problem|reason|truth|point|key|secret|lesson|thing|"
    r"answer|question) (?:is|was)|the real\b|it'?s not|isn'?t|we need|we have to|"
    r"you have to|you need to|should|must|never|always|everyone|nobody|no one|"
    r"the most|the only|the biggest|the best|the worst)\b", re.I)


def _cc():
    import caption_carry
    return caption_carry


def norm(word):
    """A transcript word, lower-cased, edge punctuation stripped."""
    t = str(word or "").strip().lower()
    prev = None
    while t and t != prev:
        prev, t = t, _STRIP.sub("", t)
    return t.replace("’", "'")


def _content(word):
    toks = _cc().tokens(str(word or ""))
    return bool(toks) and all(_cc().is_content(t) for t in toks)


def _has_digit(word):
    return any(c.isdigit() for c in str(word or ""))


def _raw(w):
    return str(w.get("w") or "").strip()


def _quote(words):
    return " ".join(_raw(w) for w in words).strip(" ,;:")


def _item_text(words):
    return " ".join(_raw(w) for w in words).strip(" ,;:.!?…")


def sentences(words):
    """[[word, ...]] split where a word ends a sentence."""
    out, cur = [], []
    for w in words:
        cur.append(w)
        if _SENTENCE_END.search(_raw(w)):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def _strip_leads(ws):
    """An item's words without the discourse fillers and articles it opens
    with ('you know computers' -> 'computers', 'the Green Revolution')."""
    ws = list(ws)
    changed = True
    while ws and changed:
        changed = False
        toks = [norm(w.get("w")) for w in ws]
        for lead in _FILLER_LEADS:
            if tuple(toks[:len(lead)]) == lead and len(ws) > len(lead):
                ws = ws[len(lead):]
                changed = True
                break
        if not changed and ws and norm(ws[0].get("w")) in ("the", "a", "an") and len(ws) > 1:
            ws = ws[1:]
            changed = True
    return ws


def _chunks(words):
    """Comma / 'and' / 'or' separated chunks: [{"words", "sep"}] where sep
    is what ends the chunk: ',' 'and' '.' or None (the words run out)."""
    out, cur = [], []
    for w in words:
        t = norm(w.get("w"))
        raw = _raw(w)
        if t in _SEPARATORS and cur:
            out.append({"words": cur, "sep": "and"})
            cur = []
            if _SENTENCE_END.search(raw):
                out[-1]["sep"] = "."
            continue
        cur.append(w)
        if _SENTENCE_END.search(raw):
            out.append({"words": cur, "sep": "."})
            cur = []
        elif _CLAUSE_SEP.search(raw):
            out.append({"words": cur, "sep": ","})
            cur = []
    if cur:
        out.append({"words": cur, "sep": None})
    return out


# The function words a noun phrase may hold inside it ('the Green
# Revolution', 'bread and butter', 'cities of the future'); any other ('it's
# generated great', 'we built') makes the chunk a clause, not an item.
ITEM_GLUE = frozenset(("the", "a", "an", "of", "and", "&", "for", "to", "in", "on", "with"))


def _item(chunk):
    """The chunk as a list item ({"text", "t0", "t1", "words"}), or None
    when it is no short noun phrase."""
    ws = _strip_leads(chunk["words"])
    if not ws or len(ws) > ITEM_MAX_WORDS or any(_has_digit(w.get("w")) for w in ws):
        return None
    content = [w for w in ws if _content(w.get("w"))]
    if not 1 <= len(content) <= ITEM_MAX_CONTENT:
        return None
    # an item ends on its noun: trailing function words go ('in there')
    while ws and not _content(ws[-1].get("w")):
        ws = ws[:-1]
    if not ws:
        return None
    if any(not _content(w.get("w")) and norm(w.get("w")) not in ITEM_GLUE for w in ws):
        return None
    return {"text": _item_text(ws), "t0": float(ws[0]["t0"]), "t1": float(ws[-1]["t1"]),
            "words": ws}


def lists(words):
    """Spoken enumerations: [{"kind": "list", "t0", "t1", "text", "items"}]."""
    chunks = _chunks(words)
    out, i = [], 0
    while i < len(chunks):
        run = []
        j = i
        while j < len(chunks):
            it = _item(chunks[j])
            if it is None:
                break
            run.append(it)
            if chunks[j]["sep"] not in (",", "and"):
                j += 1
                break
            j += 1
        if not run:
            i += 1
            continue
        items = list(run)
        # the lead: a longer chunk whose last word is the first item
        # ('…meant computers but also rockets AND supersonic aviation…')
        if i > 0 and chunks[i - 1]["sep"] in (",", "and"):
            prev = chunks[i - 1]["words"]
            last = prev[-1] if prev else None
            if last is not None and _content(last.get("w")) and not _has_digit(last.get("w")) \
                    and _item({"words": prev, "sep": None}) is None:
                items.insert(0, {"text": _item_text([last]), "t0": float(last["t0"]),
                                 "t1": float(last["t1"]), "words": [last]})
        # the tail: a comma list whose last item has no closing comma
        # ('computers, internet, mobile, internet it's generated…')
        end = j
        if run and chunks[j - 1]["sep"] == "," and j < len(chunks):
            nxt = chunks[j]["words"]
            if len(nxt) >= 2 and _content(nxt[0].get("w")) and not _has_digit(nxt[0].get("w")) \
                    and not _content(nxt[1].get("w")):
                items.append({"text": _item_text([nxt[0]]), "t0": float(nxt[0]["t0"]),
                              "t1": float(nxt[0]["t1"]), "words": [nxt[0]]})
        # a repeated item is one item ('computers, internet, mobile, internet')
        seen, uniq = set(), []
        for it in items:
            k = it["text"].lower()
            if k not in seen:
                seen.add(k)
                uniq.append(it)
        if len(uniq) >= LIST_MIN_ITEMS:
            items = uniq
            out.append({"kind": "list", "t0": items[0]["t0"], "t1": items[-1]["t1"],
                        "text": ", ".join(it["text"] for it in items),
                        "items": [{k: v for k, v in it.items() if k != "words"} for it in items]})
        i = max(end, i + 1)
    return out


def _opener(sent):
    toks = [norm(w.get("w")) for w in sent]
    while toks and toks[0] in ("and", "so", "but", "oh", "well", "now"):
        toks = toks[1:]
        sent = sent[1:]
    if len(toks) < 3:
        return None, sent
    key = " ".join(toks[:2])
    if key in _COMMON_OPENERS or not any(_content(w.get("w")) for w in sent[:2]):
        return None, sent
    return key, sent


def triads(words):
    """Anaphora: [{"kind": "triad", "t0", "t1", "text", "opener", "items"}]."""
    sents = sentences(words)
    keyed = []
    for s in sents:
        key, body = _opener(s)
        if key:
            keyed.append((key, body))
    out, used = [], set()
    for a, (key, body) in enumerate(keyed):
        if a in used:
            continue
        group = [(a, body)]
        t_first = float(body[0]["t0"])
        for b in range(a + 1, len(keyed)):
            k2, body2 = keyed[b]
            if float(body2[0]["t0"]) - t_first > TRIAD_WINDOW_S:
                break
            if k2 == key:
                group.append((b, body2))
        if len(group) < TRIAD_MIN:
            continue
        used.update(g for g, _b in group)
        items = []
        for _g, body2 in group:
            rest = body2[2:]
            ws = []
            for w in rest:
                if ws and not _content(w.get("w")):
                    break
                if not ws and not _content(w.get("w")):
                    continue
                ws.append(w)
                if len(ws) >= TRIAD_ITEM_MAX_WORDS or _CLAUSE_SEP.search(_raw(w)) \
                        or _SENTENCE_END.search(_raw(w)):
                    break
            if ws:
                items.append({"text": _item_text(ws), "t0": float(ws[0]["t0"]),
                              "t1": float(ws[-1]["t1"]), "said_from": float(body2[0]["t0"])})
        if len(items) >= TRIAD_MIN:
            opener = " ".join(_raw(w) for w in group[0][1][:2])
            out.append({"kind": "triad", "t0": float(group[0][1][0]["t0"]),
                        "t1": float(group[-1][1][-1]["t1"]), "opener": opener,
                        "text": " / ".join(f"{opener} {it['text']}" for it in items),
                        "items": items})
    return out


def _stat(n):
    said = str(n.get("said") or "")
    if _has_digit(said):
        return True
    if re.search(r"percent|thousand|million|billion|trillion|%", said, re.I):
        return True
    return any(abs(v) >= NUMBER_MIN for v in n.get("values") or ())


def numbers(words):
    """Spoken figures worth a number beat, ranges merged:
    [{"kind": "number" | "range", "t0", "t1", "text", "values"}]."""
    try:
        import number_reveal
        found = number_reveal.spoken_numbers(words)
    except Exception:  # noqa: BLE001 — a number reader that fails reads nothing
        return []
    found = [n for n in found if _stat(n)]
    raw_by_t0 = {round(float(w["t0"]), 3): (k, w) for k, w in enumerate(words)}
    out, i = [], 0
    while i < len(found):
        n = found[i]
        nxt = found[i + 1] if i + 1 < len(found) else None
        if nxt is not None and float(nxt["t0"]) - float(n["t1"]) <= RANGE_GAP_S:
            # what is said between them: nothing after a comma, 'or', 'to'
            k0 = raw_by_t0.get(round(float(n["t0"]), 3), (None, None))[0]
            k1 = raw_by_t0.get(round(float(nxt["t0"]), 3), (None, None))[0]
            between = [norm(words[k].get("w")) for k in range(k0 + 1, k1)] \
                if k0 is not None and k1 is not None else ["?"]
            said = str(n.get("said") or "")
            lo, hi = min(n["values"]), min(nxt["values"])
            joined = [b for b in between if b not in ("", "-", "–")]
            if hi > lo and (joined in ([], ["or"], ["to"]) and
                            (joined or _CLAUSE_SEP.search(said.split()[-1] if said.split() else ""))):
                out.append({"kind": "range", "t0": float(n["t0"]), "t1": float(nxt["t1"]),
                            "text": f"{said} {' '.join(joined) + ' ' if joined else ''}"
                                    f"{nxt['said']}".strip(" ,"),
                            "values": (lo, hi)})
                i += 2
                continue
        out.append({"kind": "number", "t0": float(n["t0"]), "t1": float(n["t1"]),
                    "text": str(n.get("said") or "").strip(" ,"), "values": tuple(sorted(n["values"]))})
        i += 1
    return out


def names(words):
    """Proper nouns inside a sentence: [{"kind": "name", "t0", "t1", "text"}]."""
    out, run = [], []

    def flush():
        if run:
            out.append({"kind": "name", "t0": float(run[0]["t0"]), "t1": float(run[-1]["t1"]),
                        "text": " ".join(_raw(w).strip(" ,;:.!?…") for w in run)})
        run.clear()

    prev = None
    for w in words:
        raw = _raw(w).strip("\"'“‘(")
        t = norm(raw)
        opener = prev is None or bool(_SENTENCE_END.search(_raw(prev)))
        cap = bool(raw) and raw[0].isupper()
        proper = (cap and not opener and t not in _I_FORMS and _content(raw)
                  and not _has_digit(raw) and len(t) > 1)
        if proper:
            run.append(w)
        else:
            flush()
        if proper and (_CLAUSE_SEP.search(_raw(w)) or _SENTENCE_END.search(_raw(w))):
            flush()
        prev = w
    flush()
    return out


def claims(words):
    """Sentences carrying a claim cue: [{"kind": "claim", "t0", "t1", "text"}]."""
    out = []
    for s in sentences(words):
        text = _quote(s)
        if len(s) >= 5 and CLAIM_CUES.search(text):
            out.append({"kind": "claim", "t0": float(s[0]["t0"]), "t1": float(s[-1]["t1"]),
                        "text": text})
    return out


def candidates(words):
    """Every beat candidate in ``words``, in time order."""
    words = [w for w in words or [] if isinstance(w, dict) and w.get("w") is not None
             and w.get("t0") is not None and w.get("t1") is not None]
    out = []
    for fn in (lists, triads, numbers, names, claims):
        try:
            out += fn(words)
        except Exception as exc:  # noqa: BLE001 — a reader that fails reads nothing
            print(f"[spoken_beats] {fn.__name__} skipped: {type(exc).__name__}: {exc}", flush=True)
    out.sort(key=lambda c: (c["t0"], c["kind"]))
    return out


MAIN_MIN_CONTENT = 5


def main_line(words, skip=()):
    """The line a stretch carries: its first sentence with at least
    MAIN_MIN_CONTENT content words (a thesis is usually stated before it is
    illustrated), else the one with the most; sentences starting inside a
    ``skip`` span [(t0, t1)] (a triad's own lines) are passed over. Its quote
    and times, or None."""
    best, first = None, None
    for s in sentences(words):
        if not s or any(a - 1e-3 <= float(s[0]["t0"]) <= b + 1e-3 for a, b in skip):
            continue
        n = sum(1 for w in s if _content(w.get("w")))
        if first is None and n >= MAIN_MIN_CONTENT:
            first = s
        if best is None or n > best[0]:
            best = (n, s)
    s = first or (best[1] if best else None)
    if not s:
        return None
    return {"text": _quote(s), "t0": float(s[0]["t0"]), "t1": float(s[-1]["t1"])}
