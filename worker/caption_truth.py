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
           "gimme": "giveme", "dunno": "dontknow", "ok": "okay", "alright": "allright"}
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
    if len(src_tokens) == len(dst_tokens):
        return all(_canon(a) == _canon(b) or _sim(_canon(a), _canon(b)) >= MIN_SIMILARITY
                   or (len(a) == 1 and len(b) == 1 and a.isalpha() and b.isalpha())
                   # short words the ear confuses ("see" / "say")
                   or (len(a) <= 3 and len(b) <= 3 and a[:1] == b[:1])
                   for a, b in zip(src_tokens, dst_tokens))
    return _sim(s, d) >= MIN_SIMILARITY


def faithful_fix(src, dst):
    """(True, "") when `dst` only respells what `src` heard, else (False, why)."""
    a, b = _tokens(src), _tokens(dst)
    if len(_TOKEN.findall(dst or "")) != len(b):  # pragma: no cover — same regex
        return False, "it could not be read"
    if not a:
        return False, "it names no heard words"
    if not b:
        return False, "it removes the words (hide words with set_caption_mutes instead)"
    if [_canon(t) for t in a] == [_canon(t) for t in b]:
        return True, ""
    if _negations(a) != _negations(b):
        return False, "it adds or removes a negation, which flips the meaning"
    ops = difflib.SequenceMatcher(None, [_canon(t) for t in a], [_canon(t) for t in b],
                                  autojunk=False).get_opcodes()
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            continue
        sa, sb = a[i1:i2], b[j1:j2]
        if tag == "delete":
            # A stutter or a filler may collapse; a real word may not vanish.
            prev = a[i1 - 1] if i1 > 0 else None
            if all(t in _FILLERS or t == prev or (i2 < len(a) and t == a[i2]) for t in sa):
                continue
            return False, f"it drops words that were said ({' '.join(sa)!r})"
        if tag == "insert":
            return False, f"it adds words that were not said ({' '.join(sb)!r})"
        # replace
        if _alike(sa, sb):
            continue
        raw_b = _TOKEN.findall(dst or "")[j1:j2]
        if len(sa) <= 2 and len(sb) <= 2 and _acronym_or_name(" ".join(raw_b)):
            continue
        # Collapsing a stutter into the replacement ("the the the" -> "the X")
        # still counts the replacement's own words.
        return False, (f"{' '.join(sa)!r} -> {' '.join(sb)!r} is a different word, not a "
                       "respelling of what was heard")
    return True, ""


def refusal(rule, why):
    return (f"{rule.get('from')!r} -> {rule.get('to')!r} (would change what was said: {why}. "
            "A caption correction may only fix how a heard word is spelled: case, punctuation, a "
            "misspelled name or term that sounds alike, a number. To hide words, use "
            "set_caption_mutes)")
