"""Free-text search over the bundled music catalog — and, more importantly,
the ability to return NOTHING.

THE BUG THIS FIXES
list_music_library(mood=...) took an 8-value enum. An unknown mood was
REJECTED back to the same 8 buckets, and every accepted mood returned tracks.
So the tool had no representation for "the catalog cannot serve this request":
a miss was literally indistinguishable from a hit. The agent could not
disclose a gap it had no channel to perceive, which is why no amount of prompt
wording ever fixed the silent substitution — "epic movie-trailer music"
resolved to whichever three tracks carry mood='cinematic' and got reported as
a fulfilment. The churn was a schema defect, not a behavioural one.

WHAT A "MATCH" MEANS HERE
Deterministic and explainable, never a model judgement call:

  * A query is reduced to terms; each term is expanded through a small,
    explicit synonym map into catalog vocabulary (moods and measured tags).
  * A track SCORES on the terms it can be defended against — its mood, its
    title, its author, and the tags derived from measured audio features
    (music/features.json).
  * A track is VETOED outright by its `not_for` list. The veto is absolute
    and beats any score. This is the half that matters: a flat-dynamics 78 BPM
    lofi track carries not_for=[..., "epic", "trailer", "orchestral", ...],
    so it can never surface for a trailer request no matter how many other
    words happen to overlap.
  * Terms nothing in the catalog can speak to are reported back as
    `unmatched`, so the caller can say WHICH part of the request it could not
    serve rather than a generic apology.

The bias is deliberately toward reporting a miss. A false miss costs one
sentence ("the library has nothing like that — I can compose one"); a false
match costs the customer.

features.json is OPTIONAL. Without it search still works on mood/title/author
and simply has no vetoes, which degrades toward the old behaviour instead of
crashing — the same contract bundled_library.py uses for a missing manifest.
"""

import json
import os
import re

import music_library

_FEATURES_PATH = os.path.join(music_library.MUSIC_DIR, "features.json")

# Words that carry no discriminating power in a music request. Kept short:
# an over-eager stoplist silently deletes the part of the request that
# mattered.
_STOP = {
    "a", "an", "the", "some", "any", "of", "for", "with", "and", "or", "to",
    "in", "on", "at", "it", "its", "this", "that", "please", "add", "put",
    "but", "just", "really", "very", "more", "bit",
    "make", "want", "need", "like", "sounds", "sound", "sounding", "music",
    "track", "song", "audio", "background", "bed", "something", "me", "my",
    "video", "clip", "kind", "type", "vibe", "feel", "feeling", "style",
}

# User vocabulary -> catalog vocabulary. Explicit and auditable on purpose:
# every entry is a claim that the catalog can genuinely serve the left-hand
# word, and a wrong entry here is exactly the silent substitution this module
# exists to stop. Terms NOT listed and not present in a track's tags simply
# fail to match, which is the honest outcome.
_SYNONYMS = {
    "chill": ["chill", "lofi", "relaxed", "calm"],
    "lofi": ["lofi", "chill"],
    "lo-fi": ["lofi", "chill"],
    "relaxed": ["chill", "calm"],
    "calm": ["chill", "calm", "ambient"],
    "mellow": ["chill", "calm"],
    "study": ["lofi", "chill"],
    "sad": ["sad", "reflective", "melancholy"],
    "emotional": ["reflective", "sad"],
    "reflective": ["reflective", "sad"],
    "happy": ["upbeat", "bright"],
    "fun": ["upbeat", "bright"],
    "energetic": ["upbeat", "energetic"],
    "upbeat": ["upbeat", "energetic", "bright"],
    "bright": ["bright", "upbeat"],
    "corporate": ["corporate"],
    "business": ["corporate"],
    "professional": ["corporate"],
    "ambient": ["ambient", "atmospheric"],
    "atmospheric": ["ambient", "atmospheric"],
    "background": ["ambient"],
    "cinematic": ["cinematic"],
    "film": ["cinematic"],
    "movie": ["cinematic"],
    "dramatic": ["dramatic"],
    "tense": ["dramatic"],
    "dark": ["dramatic", "dark"],
    "hiphop": ["hiphop"],
    "hip": ["hiphop"],
    "rap": ["hiphop"],
    "beat": ["hiphop"],
    "boom": ["hiphop"],
    "inspiring": ["inspiring", "uplifting"],
    "uplifting": ["inspiring", "uplifting"],
    "motivational": ["inspiring", "uplifting"],
    "hopeful": ["inspiring", "uplifting"],
    "guitar": ["guitar", "acoustic"],
    "acoustic": ["acoustic", "guitar"],
    "piano": ["piano"],
    "retro": ["retro", "80s"],
    "80s": ["retro", "80s"],
    "synth": ["synth", "retro"],
    "slow": ["slow"],
    "fast": ["fast"],
}


def _load_features():
    """Measured per-track descriptors, keyed by slug. Missing file => {}."""
    try:
        with open(_FEATURES_PATH) as f:
            raw = json.load(f)
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


FEATURES = _load_features()


# Words that flip the sense of the attribute that follows them. Without this
# the veto INVERTS: `not_for` is the list of things a track cannot be, so an
# attribute the user is EXCLUDING ("nothing dark") vetoed exactly the tracks
# that satisfy the exclusion, while the tracks carrying it as a positive tag
# scored on it and rose to the top. "nothing dark" returned the darkest beats
# in the catalog.
_NEGATORS = {"no", "not", "nothing", "none", "without", "avoid", "minus",
             "except", "non", "isn't", "isnt", "dont", "don't", "doesn't",
             "doesnt", "never", "less", "anything"}

# Unicode-aware: [a-z0-9'] silently dropped every character outside ASCII, so
# a request in Arabic, Russian, Japanese, Hindi or Thai — or one that is
# mostly emoji — reduced to ZERO terms. search() then took the "just add
# music" branch and can_serve() returned vacuously True, which turned the
# entire substitution check off for those users.
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


def terms(query, with_negated=False):
    """Query string -> meaningful lowercase terms, order preserved.

    with_negated=True returns (positive, negated): a negator claims the next
    meaningful word, so "nothing dark" yields ([], ["dark"])."""
    words = _WORD_RE.findall((query or "").lower())
    out, neg, pending = [], [], False
    for w in words:
        if w in _NEGATORS:
            pending = True
            continue
        if w in _STOP or len(w) < 2:
            continue
        target = neg if pending else out
        pending = False
        if w not in target:
            target.append(w)
    return (out, neg) if with_negated else out


def _expand(term):
    """A term plus its catalog-vocabulary synonyms."""
    return set(_SYNONYMS.get(term, [])) | {term}


def _haystack(track):
    """Everything a track can be honestly matched on."""
    f = FEATURES.get(track.get("slug"), {})
    bits = [track.get("title", ""), track.get("mood", ""),
            track.get("author", ""), track.get("slug", "")]
    bits.extend(f.get("tags") or [])
    return set(_WORD_RE.findall(" ".join(bits).lower()))


def _vetoes(track):
    """Request phrases this track must never match, from measured features.

    Each not_for entry is kept as a TOKEN SET and fires only when the query
    supplies EVERY one of its tokens. Splitting entries into loose tokens —
    the obvious implementation, and the one written first — made
    "epic-cinematic" leak a bare `cinematic` veto onto all 24 tracks, so the
    three genuinely cinematic-scored tracks became unreachable by the word
    that actually describes them. A compound veto is a claim about a
    COMBINATION ("epic" AND "cinematic"), not about either word alone.
    """
    f = FEATURES.get(track.get("slug"), {})
    out = []
    for v in (f.get("not_for") or []):
        toks = set(_WORD_RE.findall(str(v).lower()))
        if toks:
            out.append(toks)
    return out


def _vetoed(track, query_tokens):
    return any(toks <= query_tokens for toks in _vetoes(track))


# A track losing more than this when summed to mono is demoted in ranking.
# 1.5 dB is roughly where the level drop stops being inaudible on a phone.
MONO_LOSS_DEMOTE_DB = 1.5


def _mono_penalty(track):
    """0 for a mono-safe track, 1 for one that measurably collapses.
    Deliberately a coarse two-bucket sort key, not a continuous score: the
    measurement supports "this loses level on a phone speaker", and ordering
    tracks by tenths of a decibel would be reading precision into it that
    the number does not carry."""
    st = (FEATURES.get(track.get("slug"), {}) or {}).get("stereo") or {}
    try:
        return 1 if abs(float(st.get("mono_sum_loss_db") or 0.0)) \
            > MONO_LOSS_DEMOTE_DB else 0
    except (TypeError, ValueError):
        return 0


def search(query, limit=8):
    """Search the catalog. Returns (hits, report).

    hits   — [{track, score, hit_terms}], best first, vetoed tracks removed.
    report — {'terms': [...], 'unmatched': [...], 'vetoed': n,
              'matched': bool}

    `matched` is False when NOTHING in the catalog can honestly answer the
    query. That is the whole point of this module: a caller that never sees
    False cannot tell the user the truth.
    """
    qterms, negated = terms(query, with_negated=True)
    rep = {"terms": qterms, "negated": negated, "unmatched": [],
           "vetoed": 0, "matched": False}
    if not qterms and not negated:
        # No usable terms: this is a "just add music" request, not a specific
        # one. Everything is fair game and the caller picks by mood.
        return ([{"track": t, "score": 0, "hit_terms": []}
                 for t in music_library.CATALOG][:limit],
                dict(rep, matched=True))

    # The query's full vocabulary, synonyms included — a veto on "cinematic"
    # must fire for a request that said "movie". NEGATED terms are excluded:
    # feeding "dark" from "nothing dark" into the veto set would knock out
    # every track that is already not dark.
    qtokens = set()
    for term in qterms:
        qtokens |= _expand(term)
    negtokens = set()
    for term in negated:
        negtokens |= _expand(term)

    scored, hit_any = [], set()
    for t in music_library.CATALOG:
        hay = _haystack(t)
        if _vetoed(t, qtokens):
            rep["vetoed"] += 1
            continue
        # An excluded attribute drops any track that HAS it — the mirror of
        # the positive path, and the direction the user actually meant.
        if negtokens & hay:
            rep["vetoed"] += 1
            continue
        hits = [term for term in qterms if _expand(term) & hay]
        hit_any |= set(hits)
        if hits or not qterms:
            # `not qterms` means the request was PURELY an exclusion
            # ("nothing dark"). Everything that survived the negative filter
            # is a legitimate answer; without this the loop scores nothing
            # and a satisfiable request reports NO MATCH.
            # Longer overlap wins; ties break on the shorter title so the
            # ordering is stable and does not depend on catalog order.
            scored.append({"track": t, "score": len(hits), "hit_terms": hits})

    # Rank: overlap first, then MONO SAFETY, then a stable title tiebreak.
    #
    # The mono term is measured, not aesthetic. Three tracks are phase-
    # inverted between channels and lose real level when summed —
    # cinematic-after-midnight by 4.6 dB — and most of what this product
    # exports is watched on a phone's single speaker, which sums to mono.
    # Before this, that track ranked FIRST for "cinematic": the catalog's
    # most fragile bed was its default answer. Tracks are demoted, never
    # excluded: a 4.6 dB dip is a bad default, not a broken file, and the
    # user may still want it.
    scored.sort(key=lambda x: (-x["score"], _mono_penalty(x["track"]),
                               len(x["track"].get("title", ""))))
    rep["unmatched"] = [t for t in qterms if t not in hit_any]
    rep["matched"] = bool(scored)
    return scored[:limit], rep


def can_serve(query, storage_key):
    """Would this specific track have come back for this request?

    The honesty layer's load-bearing question, and the reason it is asked at
    WRITE time rather than at search time: an agent that searched for
    something reasonable and then added a different track would otherwise
    launder the substitution past any check anchored to the search.

    Returns (True, None) for anything that is not a bundled library track —
    a user's own upload or a generated track is by definition what was asked
    for, and this module has nothing to say about it.
    """
    if not music_library.is_library_ref(storage_key):
        return True, None
    track = music_library.resolve(storage_key)
    if not track:
        return True, None
    hits, rep = search(query, limit=len(music_library.CATALOG) or 1)
    # A purely negative request ("nothing dark") carries no positive terms but
    # is still a real constraint, so it must not short-circuit to "servable".
    if not rep["terms"] and not rep.get("negated"):
        return True, None
    for h in hits:
        if h["track"].get("slug") == track.get("slug"):
            return True, None
    return False, track


def summarize_catalog():
    """What the library actually IS, in one honest line.

    Written for the moment the agent has to say "I don't have that": a
    concrete description beats a generic apology, and it stops the model
    guessing at the catalog's contents."""
    if not music_library.CATALOG:
        return "The built-in music library is empty in this deployment."
    moods = sorted({t.get("mood") for t in music_library.CATALOG if t.get("mood")})
    return (f"The built-in library is {len(music_library.CATALOG)} CC0 tracks "
            f"({', '.join(moods)}) — mostly bedroom-produced lofi, power-pop "
            "and light acoustic. It has no orchestral/trailer scoring, no "
            "trap or drill, and no vocals.")
