"""Word roles for the typesetter: who whispers, who shouts (ROADMAP 2A).

A role decides a word's rung on the size ladder, its face and its colour:

  payload    the word the line is about (shouts)
  number     a figure (shouts; tabular)
  negation   not / never / no (shouts, often the accent)
  accent     the ONE colour accent: *starred* words, ``emphasis``
  emotion    affect words (may take the italic/script voice)
  quote      a quoted phrase
  profanity  set like the payload, never softened
  content    other content words (speaks)
  connector  function words (whispers)

The lexical tagger is the 1F rhetoric tagger (device_planner.lexical_tags:
same lexicon, every lane); the word's spoken duration and the prosody
grid's content test add the stress a reading would hear. Pure functions.
"""

import math
import re

ROLES = ("payload", "number", "negation", "accent", "emotion", "quote",
         "profanity", "content", "connector")
# Roles set at the hero rung of the ladder.
HERO_ROLES = ("payload", "number", "negation", "accent", "profanity")
_STAR = re.compile(r"\*([^*]+)\*")
_STRIP = "\"'“”‘’.,!?;:…()[]"


_GLUE_PREV = re.compile(r"^(?:[%‰°!?.,;:…)\]”’»]+|-[^\W\d_][\w'’-]*)$")
_GLUE_NEXT = re.compile(r"^[$€£¥(\[“‘«#]+$")


def _spans(raws):
    """Groups of whitespace-word indices printed as ONE word: a transcript's
    detached sign or suffix ("26 %", "non -player", "$ 5") joins its
    neighbour, the way it was said and the way a designer sets it."""
    groups = []
    pending = []
    for k, raw in enumerate(raws):
        bare = raw.strip("*")
        if not bare:
            continue
        if _GLUE_NEXT.match(bare):
            pending.append(k)
            continue
        if groups and not pending and _GLUE_PREV.match(bare):
            groups[-1].append(k)
            continue
        groups.append(pending + [k])
        pending = []
    if pending:
        if groups:
            groups[-1].extend(pending)
        else:
            groups.append(pending)
    return groups


def clean_text(text):
    """A typeset text with detached signs and suffixes joined to their word
    ("scored 26 % better" -> "scored 26% better"): the printed words and
    the words the captions' reading maps are then the same split."""
    raws = str(text or "").replace("\n", " ").split()
    return " ".join("".join(raws[k] for k in g) for g in _spans(raws))


def spans(text):
    """Per printed word of ``tokens(text)``, the whitespace-word indices it
    joins (a reading row is timed per whitespace word)."""
    return _spans(str(text or "").replace("\n", " ").split())


def tokens(text):
    """[(printed word, starred)] for a typeset text: whitespace-split,
    ``*accent*`` stars removed (a star pair may wrap several words), a
    detached sign or suffix joined to its word (``spans``)."""
    raws = str(text or "").replace("\n", " ").split()
    out, starred = [], False
    for g in _spans(raws):
        word, star_any = "", False
        for k in g:
            raw = raws[k]
            open_ = raw.startswith("*")
            close = raw.endswith("*") and len(raw) > 1
            if open_:
                starred = True
            star_any = star_any or starred or (open_ and close)
            word += raw.strip("*")
            if close or (open_ and close):
                starred = False
        if word:
            out.append((word, star_any))
    return out


def norm(word):
    return str(word or "").strip().lower().strip(_STRIP).replace("’", "'")


def _function(tok):
    try:
        import prosody_grid
        return tok in prosody_grid.FUNCTION_WORDS or not \
            prosody_grid.is_candidate(tok)
    except Exception:  # noqa: BLE001
        return len(tok) <= 3


def _acronym(w):
    letters = [c for c in str(w) if c.isalpha()]
    return len(letters) >= 2 and all(c.isupper() for c in letters)


def assign(words, durations=None, emphasis=None, quote=False,
           hero_share=0.0):
    """Roles for printed ``words`` [(text, starred)]. ``durations`` (s per
    word, None when unsaid) adds spoken stress: the longest content word of
    the phrase is its payload when the lexicon names none. ``emphasis`` is a
    list of words the caller wants on the accent."""
    try:
        import device_planner
        tags = device_planner.lexical_tags([{"w": w} for w, _s in words])
    except Exception:  # noqa: BLE001
        tags = {}
    emph = {norm(e) for e in (emphasis or []) if norm(e)}
    roles = []
    for i, (w, starred) in enumerate(words):
        tok = norm(w)
        t = tags.get(i, set())
        if starred or (tok and tok in emph):
            roles.append("accent")
        elif "profanity" in t:
            roles.append("profanity")
        elif "number" in t:
            roles.append("number")
        elif "negation" in t:
            roles.append("negation")
        elif "payload" in t:
            roles.append("payload")
        elif quote:
            roles.append("quote")
        elif "emotion" in t:
            roles.append("emotion")
        elif not tok or (_function(tok) and not _acronym(w)):
            roles.append("connector")
        else:
            roles.append("content")
    heroes = [i for i, r in enumerate(roles) if r in HERO_ROLES]
    if not heroes:
        # the spoken stress decides: the longest content word, else the
        # longest word of the phrase
        cands = [i for i, r in enumerate(roles) if r in ("content", "emotion",
                                                          "quote")]
        if durations and cands:
            pick = max(cands, key=lambda i: ((durations[i] or 0.0)
                                             / max(1, len(norm(words[i][0])))
                                             * 0.3 + (durations[i] or 0.0),
                                             len(words[i][0])))
        elif cands:
            pick = max(cands, key=lambda i: (len(norm(words[i][0])), -i))
        else:
            pick = max(range(len(words)), key=lambda i: len(words[i][0])) \
                if words else None
        if pick is not None:
            roles[pick] = "payload"
    # a voice that sets most content words large (a magazine lockup)
    # promotes the strongest content words to the hero rung too
    cands = [i for i, r in enumerate(roles) if r in ("content", "emotion")]
    want = int(math.ceil(hero_share * len(cands))) if cands else 0
    if want:
        def strength(i):
            d = (durations[i] or 0.0) if durations else 0.0
            return (d + 0.04 * len(norm(words[i][0])), -i)
        for i in sorted(cands, key=strength, reverse=True)[:want]:
            roles[i] = "payload"
    return roles


def is_hero(role):
    return role in HERO_ROLES
