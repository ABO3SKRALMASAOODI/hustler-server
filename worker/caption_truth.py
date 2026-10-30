"""Captions never change what was said (R2 hallucination audit, 30 Oct 2026).

A caption correction exists to fix how a heard word is SPELLED: case,
punctuation, a name or term the transcript misspelled ("Jeremis" ->
"Jeremias", "Bolterra's" -> "Voltaire's", "ai" -> "AI", "three" -> "3").
The audit found corrections in live edits that rewrote the speech itself:

  "recover the the the the the first stage" -> "recover the Falcon 9 rocket's reusable first stage"
  "a spade being a space sparing civilization" -> "us finally becoming a true spacefaring civilization"
  "we're gonna" -> "We won't"

The first two put words in the speaker's mouth; the third flips the meaning.
faithful_fix() accepts a correction only when every changed word plausibly
respells what was heard (sounds/looks alike, a number, an acronym or a single
letter), stutters may collapse, and no content word is added or removed.
"""
import difflib
import re

_NUM = {"zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
        "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11",
        "twelve": "12", "thirteen": "13", "fourteen": "14", "fifteen": "15", "sixteen": "16",
        "seventeen": "17", "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30",
        "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80",
        "ninety": "90", "hundred": "100", "thousand": "1000", "million": "1000000",
        "billion": "1000000000", "percent": "%", "first": "1st", "second": "2nd", "third": "3rd"}
_FILLERS = {"um", "uh", "erm", "er", "ah", "hmm", "mm", "uhm", "umm"}
# Spoken forms and their written spellings: the same sounds, so a correction
# between them respells what was heard ("coached ya" -> "coached you").
_SPOKEN = {"ya": "you", "yah": "you", "ye": "you", "u": "you", "yer": "your", "em": "them",
           "cuz": "because", "cause": "because", "coz": "because", "til": "until",
           "till": "until", "tho": "though", "thru": "through", "nothin": "nothing",
           "somethin": "something", "gonna": "goingto", "wanna": "wantto", "gotta": "gotto",
           "kinda": "kindof", "sorta": "sortof", "outta": "outof", "lemme": "letme",
           "gimme": "giveme", "dunno": "dontknow", "ok": "okay", "alright": "allright",
           "x": "times"}
# Short words a transcript drops or doubles; restoring or removing one is
# repairing the transcript, not rewriting the speaker.
_FUNCTION = {"a", "an", "the", "i", "you", "we", "he", "she", "it", "they", "me", "us", "them",
             "and", "but", "or", "so", "of", "to", "in", "on", "at", "for", "with", "that",
             "this", "is", "are", "was", "be", "do", "did", "have", "has", "had", "will",
             "would", "can", "could", "just", "like", "yeah", "yes", "oh", "well", "now",
             "then", "there", "it's", "that's", "i'm", "we're", "you're", "what's", "our",
             "your", "my", "his", "her", "their", "its", "not"}
_SYMBOLS = {"%", "&", "$", "+"}
_NEGATIONS = {"not", "no", "never", "nothing", "nobody", "none", "nor", "cannot", "neither",
              "dont", "cant", "wont", "isnt", "arent", "wasnt", "werent", "doesnt", "didnt",
              "hasnt", "havent", "hadnt", "shouldnt", "wouldnt", "couldnt", "aint", "mustnt",
              "neednt"}
_TOKEN = re.compile(r"[^\W_]+(?:['’][^\W_]+)*|%", re.UNICODE)
MIN_SIMILARITY = 0.5


def _tokens(text):
    return [t.lower().replace("’", "'") for t in _TOKEN.findall(text or "")]


def _canon(tok):
    t = tok.replace("'", "")
    return _SPOKEN.get(t, _NUM.get(t, t))


def _negations(tokens):
    return sum(1 for t in tokens if t.replace("'", "") in _NEGATIONS or t.endswith("n't"))


def _sim(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()


_VOWELS = re.compile(r"[aeiouyh']")
_SOUND = str.maketrans({"c": "k", "q": "k", "z": "s", "v": "f", "w": "f", "b": "p", "d": "t",
                        "g": "k", "j": "k", "x": "ks", "m": "n"})


def _skeleton(text):
    """The consonant sound of a phrase: what the ear gets right even when the
    transcript spells it wrong ("wax in vain" / "wax and wane", "in London" /
    "and landing" both keep it)."""
    t = re.sub(r"[^a-z]", "", (text or "").lower())
    t = _VOWELS.sub("", t[:1]) + _VOWELS.sub("", t[1:]) if t else ""
    t = t.translate(_SOUND)
    return re.sub(r"(.)\1+", r"\1", t)


def _sounds_alike(a, b):
    sa, sb = _skeleton(a), _skeleton(b)
    if not sa or not sb:
        return False
    return sa == sb or _sim(sa, sb) >= 0.6


def _latin(text):
    return bool(re.search(r"[A-Za-z]", text or "")) and not re.search(r"[^\x00-\x7f\u00c0-\u024f\s\W\d]", text or "")


def _acronym_or_name(raw_dst):
    """A destination that is an acronym or brand ("AI", "DOGE", "cuDNN",
    "GPT-4"): ASR spells these unpredictably, so a dissimilar respelling is
    still the same heard word."""
    words = [w for w in re.split(r"\s+", raw_dst.strip()) if w]
    if not words or len(words) > 2:
        return False
    for w in words:
        core = re.sub(r"[^\w-]", "", w)
        if not core:
            return False
        if not (core.isupper() and len(core) <= 6
                or re.search(r"[a-z][A-Z]", core) or re.search(r"\d", core)):
            return False
    return True


def _alike(src_tokens, dst_tokens):
    s = "".join(_canon(t) for t in src_tokens)
    d = "".join(_canon(t) for t in dst_tokens)
    if s == d:
        return True
    # a mishearing keeps the sound: compare the consonant skeletons
    if _sounds_alike(" ".join(_canon(t) for t in src_tokens), " ".join(_canon(t) for t in dst_tokens)):
        return True
    # a name written in another script (an Arabic transcript of "Mercedes")
    if len(src_tokens) <= 2 and len(dst_tokens) <= 3 and not any(
            re.search(r"[a-z]", t) for t in src_tokens) and all(re.search(r"[a-z]", t) for t in dst_tokens):
        return True
    if len(src_tokens) == len(dst_tokens):
        return all(_canon(a) == _canon(b) or _sim(_canon(a), _canon(b)) >= MIN_SIMILARITY
                   or (len(a) == 1 and len(b) == 1 and a.isalpha() and b.isalpha())
                   # short words the ear confuses ("see" / "say")
                   or (len(a) <= 3 and len(b) <= 3 and a[:1] == b[:1])
                   for a, b in zip(src_tokens, dst_tokens))
    return _sim(s, d) >= MIN_SIMILARITY


def faithful_fix(src, dst, asr_unsure=False):
    """(True, "") when `dst` only respells what `src` heard, else (False, why).

    Hiding words (an empty replacement) and dropping words never put words in
    the speaker's mouth, so they pass. `asr_unsure`: the transcript itself
    marked these words low-confidence, so a correction that sounds unlike
    them may still be the real words; added content and negation flips are
    refused either way."""
    a, b = _tokens(src), _tokens(dst)
    if len(_TOKEN.findall(dst or "")) != len(b):  # pragma: no cover — same regex
        return False, "it could not be read"
    if not a:
        return False, "it names no heard words"
    if not b:
        return True, ""
    if [_canon(t) for t in a] == [_canon(t) for t in b]:
        return True, ""
    if bool(_negations(a)) != bool(_negations(b)):
        # presence, not count: "not not" -> "not" is a stutter
        return False, "it adds or removes a negation, which flips the meaning"
    ops = difflib.SequenceMatcher(None, [_canon(t) for t in a], [_canon(t) for t in b],
                                  autojunk=False).get_opcodes()
    added_content = 0
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            continue
        sa, sb = a[i1:i2], b[j1:j2]
        if tag == "delete":
            # A stutter ("this is this is"), a filler or one dropped short
            # word may collapse; a run of real words may not vanish.
            n = len(sa)
            before, after = a[max(0, i1 - n):i1], a[i2:i2 + n]
            if sa == before or sa == after or all(t in _FILLERS for t in sa) or (
                    n == 1 and sa[0] in _FUNCTION):
                continue
            # Dropping words hides speech; it never invents it.
            continue
        if tag == "insert":
            # A transcript drops short words ("do see" -> "do you see"); a
            # correction may restore those, never new content words.
            content = [t for t in sb if t not in _FUNCTION and t not in _SYMBOLS]
            added_content += len(content)
            if content and (len(content) > 1 or added_content > 1):
                return False, f"it adds words that were not said ({' '.join(content)!r})"
            continue
        # replace
        if _alike(sa, sb):
            continue
        # number and symbol formatting ("90,000,000" -> "$90M", "R and D" -> "R&D")
        da, db = re.sub(r"\D", "", " ".join(sa)), re.sub(r"\D", "", " ".join(sb))
        if da and db and da.rstrip("0")[:2] == db.rstrip("0")[:2]:
            continue
        if all(t in _FUNCTION or t in _SYMBOLS for t in sa + sb):
            continue
        raw_b = _TOKEN.findall(dst or "")[j1:j2]
        if len(sa) <= 2 and len(sb) <= 2 and _acronym_or_name(" ".join(raw_b)):
            continue
        if asr_unsure and len([t for t in sb if t not in _FUNCTION]) <= len(sa) + 1:
            continue
        # Collapsing a stutter into the replacement ("the the the" -> "the X")
        # still counts the replacement's own words.
        return False, (f"{' '.join(sa)!r} -> {' '.join(sb)!r} is a different word, not a "
                       "respelling of what was heard")
    return True, ""


UNSURE_P = 0.85


def asr_unsure(index, rule):
    """True when every place the transcript holds rule['from'] has a word
    the recogniser itself was unsure of (confidence under UNSURE_P): a
    correction there may be restoring the real words. Without confidences,
    False."""
    words = [w for w in (index or {}).get("words") or [] if isinstance(w, dict)]
    src = [_canon(t) for t in _tokens(rule.get("from"))]
    if not words or not src:
        return False
    norm = [_canon(t) for w in words for t in (_tokens(w.get("w")) or [""])[:1]]
    found = False
    for i in range(len(norm) - len(src) + 1):
        if norm[i:i + len(src)] != src:
            continue
        span = words[i:i + len(src)]
        if rule.get("start") is not None and float(span[0].get("t0") or 0) < float(rule["start"]) - .001:
            continue
        if rule.get("end") is not None and float(span[-1].get("t1") or 0) > float(rule["end"]) + .001:
            continue
        found = True
        ps = [w.get("p") for w in span if isinstance(w.get("p"), (int, float))]
        if not ps or min(ps) >= UNSURE_P:
            return False
    return found


def refusal(rule, why):
    return (f"{rule.get('from')!r} -> {rule.get('to')!r} (would change what was said: {why}. "
            "A caption correction may only fix how a heard word is spelled: case, punctuation, a "
            "misspelled name or term that sounds alike, a number. If the transcript "
            "misheard words it was sure of, re-check the audio before changing them)")
