"""Ground the editor's reply in what the turn actually did (R2, 30 Oct 2026).

The honesty guard in agent_loop catches the coarse lies: a change claimed on
a turn that wrote nothing, a render claimed with no preview. The audit of 30
Oct (round3/R2_JOURNEYS_TRUTH.md) found the finer ones it let through on
turns that DID write: "I added a vibrant grade and two zooms" when only the
captions changed, "the export is ready" from an editor that cannot export,
and a speaker "quoted" with words the transcript does not hold.

Everything here is pure (EDL dicts and strings in, strings out) so the
backend, the MCP layer and tests can use it without a worker context:

  edl_changes(before, after)        what changed, layer by layer, in words
  change_list_text(changes)         one line for the turn facts / the reply
  effect_violations(draft, b, a)    effects a claim names that did not change
  export_violations(draft)          export/download claims (the editor cannot export)
  quote_violations(draft, index)    "quoted" speech the transcript does not hold
  drop_sentences(draft, sentences)  the draft without the unbacked sentences
"""
import difflib
import json
import re

# ── What changed ─────────────────────────────────────────────────────────

_SUBKEYS = {
    "grade": [("effects", "grade"), ("effects", "grade_custom"), ("effects", "lut"), ("fx", None)],
    # a zoom-punch transition is a zoom too ("switched every cut to zoom_punch")
    "zoom": [("effects", "zooms"), ("effects", "zoom_paths"), ("effects", "punch_ins"), ("motion", None),
             ("fx", None), ("effects", "transition"), ("effects", "transitions")],
    "transition": [("effects", "transition"), ("effects", "transitions"), ("effects", "junctions")],
    # a title, caption, clip or track can fade in/out on its own
    "fade": [("effects", "fade_in_s"), ("effects", "fade_out_s"), ("texts", None), ("captions", None),
             ("overlays", None), ("inserts", None), ("music", None), ("voiceover", None), ("motion", None)],
    # "subtitle text" and title cards live in texts as often as in captions
    "captions": [("captions", None), ("caption_mutes", None), ("texts", None), ("vectors", None),
                 ("motion", None)],
    "text": [("texts", None), ("vectors", None), ("captions", None), ("overlays", None), ("motion", None)],
    "music": [("music", None), ("stem_mix", None)],
    "sfx": [("sfx", None)],
    "voiceover": [("voiceover", None)],
    "broll": [("inserts", None), ("overlays", None), ("patches", None)],
    "speed": [("speed", None)],
    "frame": [("frame", None), ("canvas", None)],
    "loudness": [("master", None), ("volume", None), ("music", None), ("stem_mix", None)],
    "cuts": [("keep", None), ("split_keep_boundaries", None), ("inserts", None), ("speed", None)],
}


def _get(edl, key, sub):
    if not isinstance(edl, dict):
        return None
    v = edl.get(key)
    if sub is None:
        return v
    return v.get(sub) if isinstance(v, dict) else None


def _same(a, b):
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


def layer_changed(before, after, layer):
    """True when any part of the EDL that carries `layer` differs."""
    return any(not _same(_get(before, k, s), _get(after, k, s)) for k, s in _SUBKEYS[layer])


def _kept_seconds(keep):
    total = 0.0
    for k in keep or []:
        try:
            if isinstance(k, dict):
                total += float(k.get("end", 0)) - float(k.get("start", 0))
            elif isinstance(k, (list, tuple)) and len(k) >= 2:
                total += float(k[1]) - float(k[0])
        except (TypeError, ValueError):
            continue
    return total


def _count(v):
    return len(v) if isinstance(v, list) else (1 if v else 0)


def _plural(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


def edl_changes(before, after):
    """[{"layer", "text"}] in plain words, from the EDL diff alone."""
    if not isinstance(after, dict):
        return []
    before = before if isinstance(before, dict) else {}
    out = []

    def add(layer, text):
        out.append({"layer": layer, "text": text})

    if not _same(before.get("keep"), after.get("keep")):
        b, a = _kept_seconds(before.get("keep")), _kept_seconds(after.get("keep"))
        if a < b - 0.05:
            add("cuts", f"cut {b - a:.1f}s (kept {a:.1f}s of the source)")
        elif a > b + 0.05:
            add("cuts", f"restored {a - b:.1f}s (kept {a:.1f}s of the source)")
        else:
            add("cuts", "re-timed the kept sections")
    bc, ac = before.get("captions"), after.get("captions")
    if not _same(bc, ac):
        on = bool(ac) and not (isinstance(ac, dict) and ac.get("mode") in (None, "off", "none")
                               and not ac.get("items"))
        was = bool(bc) and not (isinstance(bc, dict) and bc.get("mode") in (None, "off", "none")
                                and not bc.get("items"))
        if isinstance(ac, list):
            add("text", f"set {_plural(len(ac), 'on-screen text line')} as captions")
        elif on and not was:
            add("captions", "turned the captions on")
        elif was and not on:
            add("captions", "turned the captions off")
        elif isinstance(ac, dict) and isinstance(bc, dict):
            if not _same(bc.get("style"), ac.get("style")):
                st = ac.get("style") if isinstance(ac.get("style"), dict) else {}
                name = st.get("motion_look") or st.get("preset") or ""
                add("captions", "restyled the captions" + (f" ({name})" if name else ""))
            if not _same(bc.get("corrections"), ac.get("corrections")) \
                    or not _same(bc.get("text_fixes"), ac.get("text_fixes")):
                add("captions", "corrected caption spellings")
            if not _same({k: v for k, v in bc.items() if k not in ("style", "corrections", "text_fixes")},
                         {k: v for k, v in ac.items() if k not in ("style", "corrections", "text_fixes")}):
                add("captions", "changed how the captions are grouped or placed")
    if not _same(before.get("caption_mutes"), after.get("caption_mutes")):
        add("captions", "changed which caption words are hidden")
    be = before.get("effects") if isinstance(before.get("effects"), dict) else {}
    ae = after.get("effects") if isinstance(after.get("effects"), dict) else {}
    if not _same(be.get("grade"), ae.get("grade")) or not _same(be.get("grade_custom"), ae.get("grade_custom")):
        g = ae.get("grade")
        add("grade", f"set the colour grade to {g}" if g and g not in ("none", "off")
            else ("set a custom colour grade" if ae.get("grade_custom") else "removed the colour grade"))
    for key, word in (("zooms", "zoom"), ("zoom_paths", "zoom path")):
        nb, na = _count(be.get(key)), _count(ae.get(key))
        if nb != na:
            add("zoom", f"{'added' if na > nb else 'removed'} {_plural(abs(na - nb), word)}")
        elif not _same(be.get(key), ae.get(key)):
            add("zoom", f"changed the {word}s")
    if not _same(be.get("transition"), ae.get("transition")) or not _same(be.get("transitions"), ae.get("transitions")):
        t = ae.get("transition") if isinstance(ae.get("transition"), dict) else {}
        style = t.get("style")
        add("transition", f"set the transitions to {style}" if style and style not in ("none", "cut")
            else "removed the transitions")
    if not _same(be.get("fade_in_s"), ae.get("fade_in_s")) or not _same(be.get("fade_out_s"), ae.get("fade_out_s")):
        add("fade", "changed the fades")
    if not _same(before.get("fx"), after.get("fx")):
        add("grade", "changed the look effects")
    if not _same(before.get("motion"), after.get("motion")):
        add("text", "changed the motion graphics")
    for key, layer, word in (("music", "music", "music track"), ("sfx", "sfx", "sound effect"),
                             ("voiceover", "voiceover", "voiceover"), ("inserts", "broll", "insert"),
                             ("overlays", "broll", "overlay"), ("texts", "text", "title"),
                             ("vectors", "text", "graphic"), ("speed", "speed", "speed change")):
        nb, na = _count(before.get(key)), _count(after.get(key))
        if na > nb:
            add(layer, f"added {_plural(na - nb, word)}")
        elif na < nb:
            add(layer, f"removed {_plural(nb - na, word)}")
        elif not _same(before.get(key), after.get(key)):
            add(layer, f"changed the {word}s")
    if not _same(before.get("frame"), after.get("frame")) or not _same(before.get("canvas"), after.get("canvas")):
        f = after.get("frame") if isinstance(after.get("frame"), dict) else {}
        add("frame", f"set the frame to {f.get('ratio')}" if f.get("ratio") else "changed the frame")
    if not _same(before.get("master"), after.get("master")) or not _same(before.get("volume"), after.get("volume")):
        add("loudness", "changed the loudness or volume")
    if not _same(before.get("stem_mix"), after.get("stem_mix")):
        add("loudness", "changed the voice/music balance")
    if not _same(before.get("source_clean"), after.get("source_clean")):
        add("broll", "changed the cleaned-up regions of the picture")
    return out


def change_list_text(changes):
    if not changes:
        return "nothing in the edit changed"
    seen, items = set(), []
    for c in changes:
        if c["text"] not in seen:
            seen.add(c["text"])
            items.append(c["text"])
    return "; ".join(items)


# ── Effect claims ────────────────────────────────────────────────────────

_EFFECT_WORDS = [
    ("captions", r"captions?|subtitles?|karaoke"),
    ("text", r"titles?|title.?cards?|lower.?thirds?|callouts?|headlines?|on-?screen text|text overlays?|hook text"),
    ("music", r"music(?! video)|soundtrack|background track|a song|the song"),
    ("sfx", r"sound.?effects?|sfx|whoosh(?:es)?|swooshes|risers?|impact (?:sounds?|hits?)|sub.?drops?"),
    ("voiceover", r"voice.?overs?|narration"),
    ("grade", r"colou?r.?grad(?:e|ing)|grade|luts?|colou?r correction|film grain|vignettes?"),
    ("zoom", r"zooms?|zoom.?ins?|punch.?ins?|push.?ins?|ken.?burns"),
    ("transition", r"transitions?|cross.?fades?|dissolves?|whip.?pans?|dips? to black"),
    ("fade", r"fade.?(?:in|out)s?|fades? (?:in|out)|fade to black"),
    ("broll", r"b.?roll|cutaways?|stock (?:footage|clips?|videos?)|picture.?in.?picture"),
    ("speed", r"slow.?mo(?:tion)?|sped.?up|speed.?ramps?"),
    ("frame", r"9:16|16:9|1:1|4:5|vertical (?:crop|format|frame)|aspect ratio"),
    ("loudness", r"loudness|mastered|-1[0-9] lufs|normali[sz]ed"),
]
_EFFECT_RX = [(layer, re.compile(r"(?i)\b(?:" + words + r")\b")) for layer, words in _EFFECT_WORDS]
# A first-person past/perfect write: "I added", "I've applied", "added a ...",
# "has been added". Offers, plans and negations are excluded separately.
_CLAIM_VERB = re.compile(
    r"(?i)(?:\b(?:i|we)(?:'ve|’ve| have)?\s+(?:also\s+|now\s+|just\s+|then\s+|went ahead and\s+)?"
    r"|^\W*|\b(?:has|have) been\s+)"
    r"(added|applied|inserted|placed|put|laid|overlaid|layered|included|created|built|set|turned on|"
    r"styled|graded|synced|scored|zoomed|punched|faded|sped|slowed|switched|swapped|replaced|"
    r"updated|changed|adjusted|made|gave|mastered|normali[sz]ed|boosted|lowered|raised)\b")
_STATE_WORDS = re.compile(
    r"(?i)\b(?:kept|keep|keeps|still|already|left|remains?|unchanged|untouched|as before|as it was|"
    r"existing|original|previous(?:ly)?|earlier|from before|carried over|stays?|same)\b")
_NOT_A_CLAIM = re.compile(
    r"(?i)\b(?:can|could|would|will|shall|should|might|may|want|if|once|when you|let me|"
    r"i'?ll|i’ll|i'?d|i’d|plan|next|option|suggest|recommend|try|tried|attempt(?:ed)?|"
    r"couldn'?t|couldn’t|can'?t|can’t|cannot|didn'?t|didn’t|did not|wasn'?t|wasn’t|not|no|never|"
    r"failed|unable|without)\b|\?")
_REMOVAL = re.compile(r"(?i)\b(?:removed|deleted|dropped|took out|turned off|stripped|cleared)\b")
_NEW_SUBJECT = re.compile(
    r"(?i)^\s*(?:which|that|where|while|because|so|but|it|this|these|they|the (?:cut|video|edit|clip|footage)"
    r"|its)\b|\b(?:is|are|was|were|ends?|starts?|uses?|looks?|shows?|plays?|runs?)\b")
_SENT = re.compile(r"(?<=[.!?…])\s+|\n+")
_CLAUSE = re.compile(r"(?i),\s*|;\s*|\s+(?:and|plus|then|with)\s+|\s+[—–-]\s+")


def sentences(text):
    return [s.strip() for s in _SENT.split(text or "") if s and s.strip()]


_CURLY = str.maketrans({"\u2018": "'", "\u2019": "'", "\u02bc": "'", "\u201c": '"', "\u201d": '"'})


def straight(text):
    return (text or "").translate(_CURLY)


def _claimed_layers(sentence):
    """Layers a sentence claims to have added/applied/changed this turn."""
    sentence = straight(sentence)
    if _NOT_A_CLAIM.search(sentence):
        return set()
    out = set()
    verb_live = False
    for clause in _CLAUSE.split(sentence):
        if not clause.strip():
            continue
        if _STATE_WORDS.search(clause) or _REMOVAL.search(clause):
            # "kept the captions", "the music stays": a state, not this
            # turn's work, and the list that follows it is that state too.
            # A removal is not an "added" claim either.
            verb_live = False
            continue
        if _CLAIM_VERB.search(clause):
            verb_live = True
        elif _NEW_SUBJECT.search(clause):
            # "..., which ends on the poster title": a description, not the
            # next item of the claimed list
            verb_live = False
        if not verb_live:
            continue
        for layer, rx in _EFFECT_RX:
            if rx.search(clause):
                out.add(layer)
    return out


def effect_violations(draft, before, after):
    """[(sentence, layer)] for effects a claim names that the EDL did not
    change this turn. Only meaningful on a turn that wrote a version: a
    zero-write turn is policed whole by agent_loop's EDIT_CLAIM guard."""
    if not isinstance(after, dict):
        return []
    found = []
    for sent in sentences(draft):
        for layer in sorted(_claimed_layers(sent)):
            if not layer_changed(before or {}, after, layer):
                found.append((sent, layer))
    return found


# ── Export claims (the in-app editor has no export tool) ─────────────────

EXPORT_CLAIM = re.compile(
    r"(?i)(\b(?:i|we)(?:'ve|’ve| have)?\s+(?:just\s+|now\s+|also\s+)?exported\b"
    r"|\b(?:the|your)\s+(?:final\s+)?export\s+(?:is|has|was)\s+(?:now\s+)?(?:ready|done|finished|complete)\b"
    r"|\b(?:exported|final)\s+(?:video|file|mp4)\s+is\s+(?:now\s+)?ready\s+(?:to|for)\s+download\b"
    r"|\bdownload\s+(?:it|the\s+(?:final\s+)?(?:video|file|mp4|export))\s+(?:below|here|now)\b)")


def export_violations(draft):
    out = []
    for sent in sentences(draft):
        test = straight(sent)
        m = EXPORT_CLAIM.search(test)
        if m and not re.search(r"(?i)\b(?:can|could|when|once|if|ready to export|to export it)\b", test[:m.start()]):
            out.append((sent, m.group(0)))
    return out


# ── Quoted speech ────────────────────────────────────────────────────────

_QUOTE = re.compile(r"[“\"]([^”\"\n]{6,240})[”\"]")
_SPEECH = re.compile(
    r"(?i)\b(?:says?|said|saying|asks?|asked|line|quote[sd]?|words?|spoken|speaks?|tells?|told|"
    r"opens? (?:on|with)|ends? (?:on|with)|closes? (?:on|with)|you hear|he|she|they|speaker|host|guest)\b")
_WORD = re.compile(r"[a-z0-9']+")


def _norm_words(text):
    return _WORD.findall((text or "").lower().replace("’", "'"))


def transcript_words(index):
    if not isinstance(index, dict):
        return []
    words = index.get("words")
    if isinstance(words, list) and words:
        return _norm_words(" ".join(str((w or {}).get("w", "")) for w in words if isinstance(w, dict)))
    return _norm_words(" ".join(str((s or {}).get("text", "")) for s in index.get("sentences") or []
                                if isinstance(s, dict)))


def quote_in_transcript(quote, words, min_ratio=0.8):
    q = _norm_words(quote)
    if len(q) < 3 or not words:
        return True
    n = len(q)
    joined_q = " ".join(q)
    first = set(q[:2])
    for i in range(0, max(1, len(words) - n + 1)):
        if words[i] not in first and (i + 1 >= len(words) or words[i + 1] not in first):
            continue
        window = " ".join(words[i:i + n + 2])
        if difflib.SequenceMatcher(None, joined_q, window[:len(joined_q) + 12]).ratio() >= min_ratio:
            return True
    return False


def quote_violations(draft, index, edl=None):
    """[(sentence, quote)] for speech quoted in the reply that the transcript
    does not hold. Text the edit itself carries (titles, caption items,
    corrections) is the editor's own writing, not a quote, and is skipped."""
    words = transcript_words(index)
    if not words:
        return []
    own = _norm_words(json.dumps([(edl or {}).get("texts"), (edl or {}).get("captions"),
                                  (edl or {}).get("vectors"), (edl or {}).get("motion")], default=str))
    own_text = " ".join(own)
    out = []
    for sent in sentences(draft):
        if not _SPEECH.search(sent):
            continue
        for m in _QUOTE.finditer(sent):
            q = m.group(1)
            if len(_norm_words(q)) < 3:
                continue
            if " ".join(_norm_words(q)) in own_text:
                continue
            if not quote_in_transcript(q, words):
                out.append((sent, q))
    return out


# ── Repair ───────────────────────────────────────────────────────────────

def drop_sentences(draft, bad):
    """The draft without the sentences in `bad` (exact sentence strings)."""
    bad = {b.strip() for b in bad if b}
    if not bad:
        return draft
    paragraphs = []
    for para in re.split(r"\n\s*\n", draft or ""):
        kept = [s for s in sentences(para) if s.strip() not in bad]
        if kept:
            paragraphs.append(" ".join(kept))
    return "\n\n".join(paragraphs)
