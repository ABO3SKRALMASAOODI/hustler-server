"""suggest_devices v1: timed, budgeted, bound device candidates (track 1F).

ROADMAP Wave 1 / 1F; contract in worker/devices/INTERFACES_W1.md §8.

The planner reads the CURRENT program and answers one question: which
creative devices does this cut earn, and where exactly? It never writes. Each
candidate carries the exact ``apply_edit_batch`` operation that writes it, so
the agent (or an MCP client whose tool list predates Wave 1) writes the ones
it agrees with in ONE batch.

The moment model
----------------
Every candidate is bound to a moment (ROADMAP A4: devices store intent):

  stress      the prosody grid's stress peaks (prosody_grid, speech clock)
  word        a word the rhetoric tagger marks: payload, negation, number,
              contrast, emotion, profanity
  word.payoff the payoff: the strongest payload word, with a hold after it
              in which no punctuation lands
  phrase_end  intonation-phrase ends (sentence ends weigh more)
  cut         program cuts (block joins and shot changes inside a keep)
  section     section starts (speech: sentence groups; a user track: its
              sections)
  beat, downbeat, onset, drop, gap   the perception grid of a track the USER
              supplied (no planner ever adds music to get a clock)

``suggest_motion_beats`` (motion_planner.plan) is folded in: its number,
contrast, list, name and hero-word beats become tagged word moments. The
rhetoric tagger is lexical on every lane; the verb adds an LLM pass cached
per transcript when a model is available (``llm_tags``).

Restraint as code
-----------------
* owner rules: ``devices.proposable`` (no zoom or sound unless asked; dark
  cards never), flash safety (``flash_safety.plan``: at most 3 full-frame
  flashes in any second unless the user opted in);
* budgets: the card's own budget at the intensity (per_short, min_gap_s,
  per_section, per_s) and the ledger family budget (taste.ledger);
* one punctuation family per section; no device on consecutive shots; no
  punctuation, transition or finish inside the payoff hold;
* matte availability: a matte device on footage with no measured person is
  proposed only with its "measuring" note and a lower rank; a lane that
  cannot draw a card's full rung at all is never proposed.

Pure: no database, no network, no model. The verb (tools_ext/verify_tools)
gathers the inputs.
"""

import copy
import hashlib
import json
import math
import re

VERSION = 1
MAX_CANDIDATES = 8
INTENSITIES = ("calm", "designed", "charged")
DEFAULT_INTENSITY = "designed"
RHETORIC_TAGS = ("payload", "negation", "number", "contrast", "emotion",
                 "profanity")
PICTURE_STAGES = ("world", "screen", "pre_captions", "over_type")
# A moment must be at least this strong to earn a device at an intensity.
MIN_STRENGTH = {"calm": 0.6, "designed": 0.4, "charged": 0.25}
# What each rhetoric tag adds to a word's strength.
TAG_WEIGHTS = {"payload": 0.25, "number": 0.2, "negation": 0.15,
               "contrast": 0.15, "emotion": 0.1, "profanity": 0.1}
# The payoff holds this long after its word (seconds): no punctuation,
# transition or finish lands inside it (ROADMAP 1D skill).
PAYOFF_HOLD_S = (0.8, 1.5)
# Placement keys a card's ``params`` may carry besides item field names
# (INTERFACES.md §5's own example card carries ``frames``).
PLANNER_KEYS = ("frames", "duration_s", "span", "offset_f", "min_s", "max_s")
SPANS = ("frames", "phrase", "sentence", "section", "shot", "payoff_hold",
         "setup")
# span "setup": a window that ENDS on its moment (a mono section opening to
# colour ON the turn) runs back at least this long, at most this long or
# this share of the program.
SETUP_MIN_S, SETUP_MAX_S, SETUP_SHARE = 1.2, 6.0, 0.4
# Families whose devices are pointlike punctuation (one family per section).
PUNCTUATION = ("punctuation",)
# Families that never land inside the payoff hold.
HOLD_FREE = ("punctuation", "transition", "finish")
# Families that need the person matte.
MATTE_FAMILIES = ("matte_fx", "type_depth")
# A same-card pick is re-ranked by this factor per earlier pick (variety).
REPEAT_DECAY = 0.75
# Moments considered per plan (strongest first): bounds the search.
MAX_MOMENTS = 240
# Two proposed devices never land closer than this (seconds): one device per
# moment — the others that fit it ride along as alternatives.
DEVICE_SPACING_S = {"calm": 2.0, "designed": 1.0, "charged": 0.5}
# ROADMAP §3 default budgets, the moments a family may take per format pack:
# a talking head with no track (C) punctuates only cuts and the turn of the
# argument (a contrast, a negation, the payoff's onset); content-born
# transitions sit on scene changes; on a user track (B) punctuation follows
# the track.
FORMAT_MOMENTS = {
    "C": {"punctuation": ("cut", "turn"), "transition": ("cut",)},
    "A": {"transition": ("cut",)},
    "B": {"punctuation": ("beat", "downbeat", "onset", "drop", "cut",
                          "turn"), "transition": ("cut",)},
    "F": {"transition": ("cut",)},
}
TURN_TAGS = ("contrast", "negation")


def format_allows(fmt, family, moment):
    """May a ``family`` device land on ``moment`` in format pack ``fmt``?"""
    rule = FORMAT_MOMENTS.get(fmt, {}).get(family)
    if not rule:
        return True
    kind = moment["kind"]
    if "turn" in rule and (set(moment.get("tags") or ()) & set(TURN_TAGS)
                           or kind == "word.payoff"):
        return True
    return kind in rule or kind.split(".", 1)[0] in rule
_SENTENCE_END = re.compile(r"[.?!…]['\"”’)]*$")
_STRIP = "\"'“”‘’.,!?;:…()[]"


# ── rhetoric: lexical tagger (every lane) ────────────────────────────────

NEGATIONS = frozenset("""
not no never nothing nobody none neither nor nowhere cannot can't won't
don't doesn't didn't isn't wasn't aren't weren't couldn't shouldn't
wouldn't haven't hasn't hadn't ain't
""".split())
CONTRASTS = frozenset("""
but however instead yet although though whereas versus vs unlike
nevertheless nonetheless rather
""".split())
EMOTION = frozenset("""
love hate fear afraid scared terrified angry furious crazy insane amazing
incredible unbelievable shocking terrible horrible awful beautiful
brilliant genius stupid ridiculous absurd insane wild huge massive
dangerous scary heartbreaking devastating thrilled excited obsessed
desperate miserable gorgeous magic magical evil miracle disaster
catastrophe nightmare dream
""".split())
PROFANITY = frozenset("""
fuck fucking fucked shit shitty damn goddamn hell bullshit crap ass
bastard bitch
""".split())
_NUMBER_WORDS = frozenset("""
ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen
nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred
thousand million billion trillion percent
""".split())
_CONTRAST_PHRASES = re.compile(
    r"\b(rather than|instead of|compared to|as opposed to|more than|less "
    r"than|better than|worse than)\b", re.I)


def norm(word):
    t = str(word or "").strip().lower().strip(_STRIP)
    return t.replace("’", "'")


_QUESTION_WORDS = frozenset("when where why how what who whom".split())


def weak_word(word):
    """A word the prosody grid may hear as stressed that no device should
    land on by itself: a contraction that is not a negation (you've, we'll,
    let's) or a bare question word. Rhetoric tags override this (a negation
    or a contrast is never weak)."""
    tok = norm(word)
    if not tok:
        return True
    if "'" in tok:
        return not (tok.endswith("n't") or tok == "cannot")
    return tok in _QUESTION_WORDS


def _content(tok):
    try:
        import prosody_grid
        return prosody_grid.is_candidate(tok)
    except Exception:  # noqa: BLE001
        return bool(tok) and len(tok) > 3


def lexical_tags(words):
    """{position: set(tags)} for ``words`` (a list of {w, ...}) by lexicon:
    negations, contrast connectives, spoken figures, strong affect,
    profanity, and the payload (the last content word of a claim
    sentence and of a sentence that carries a number or a negation)."""
    out = {}

    def tag(i, t):
        out.setdefault(i, set()).add(t)
    toks = [norm(w.get("w")) for w in words]
    for i, tok in enumerate(toks):
        raw = str(words[i].get("w") or "")
        if not any(c.isalnum() for c in tok):
            # a bare symbol ('%', '$') is part of the figure said next to it
            if i and ("%" in raw or "$" in raw):
                tag(i - 1, "number")
            continue
        if tok in NEGATIONS or tok.endswith("n't"):
            tag(i, "negation")
        if tok in CONTRASTS:
            tag(i, "contrast")
        if any(c.isdigit() for c in tok) or tok in _NUMBER_WORDS \
                or "%" in raw or "$" in raw:
            tag(i, "number")
        if tok in EMOTION:
            tag(i, "emotion")
        if tok in PROFANITY or "*" in raw:
            tag(i, "profanity")
    text = " ".join(str(w.get("w") or "") for w in words)
    for m in _CONTRAST_PHRASES.finditer(text):
        # the word the phrase starts on
        pos = len(text[:m.start()].split())
        if pos < len(words):
            tag(pos, "contrast")
    # payload: per sentence, the content word that carries it
    try:
        import spoken_beats
        claim_cue = spoken_beats.CLAIM_CUES
    except Exception:  # noqa: BLE001
        claim_cue = None
    start = 0
    for i in range(len(words) + 1):
        if i < len(words) and not _SENTENCE_END.search(
                str(words[i].get("w") or "")):
            continue
        end = min(i, len(words) - 1)
        span = range(start, end + 1)
        sent = " ".join(str(words[k].get("w") or "") for k in span)
        heavy = any(out.get(k, set()) & {"number", "negation", "contrast"}
                    for k in span)
        claim = bool(claim_cue and claim_cue.search(sent))
        if (heavy or claim) and len(span) >= 3:
            nums = [k for k in span if "number" in out.get(k, set())]
            if nums:
                pick = nums[-1]
            else:
                content = [k for k in span if _content(toks[k])]
                pick = content[-1] if content else None
            if pick is not None:
                tag(pick, "payload")
        start = i + 1
    return out


# ── rhetoric: LLM tagger (the verb calls it; cached per transcript) ──────

RHETORIC_SYSTEM = (
    "You tag the rhetoric of a spoken transcript for a video editor. Words "
    "are numbered. Return ONLY a JSON object whose keys are payload, "
    "negation, number, contrast, emotion and profanity, each a list of word "
    "numbers. payload = the 1-2 words per sentence that carry its point "
    "(the claim's key noun, the number with its noun, the word a speaker "
    "lands on); at most one in four sentences carries none. negation = "
    "words that flip a claim. number = spoken figures. contrast = the "
    "turn words and the second side of a contrast. emotion = strongly "
    "charged words. profanity = profanity. Never invent words; omit a key "
    "with no words.")
LLM_MAX_WORDS = 700
_RHETORIC_CACHE = {}
_RHETORIC_CACHE_MAX = 64


def transcript_key(words):
    """Cache key of a transcript slice: its words and centisecond times."""
    h = hashlib.sha256()
    for w in words:
        h.update(f"{w.get('w')}|{float(w.get('t0', 0)):.2f}|"
                 f"{float(w.get('t1', 0)):.2f};".encode("utf-8"))
    return h.hexdigest()[:32]


def llm_prompt(words):
    rows = [f"{i}:{str(w.get('w') or '').strip()}"
            for i, w in enumerate(words[:LLM_MAX_WORDS])]
    return " ".join(rows)


def parse_llm_tags(text, n):
    """{position: set(tags)} from the model's JSON; {} on anything else."""
    if not isinstance(text, str):
        return {}
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except (ValueError, TypeError):
        return {}
    out = {}
    if not isinstance(data, dict):
        return {}
    for tag, idx in data.items():
        if tag not in RHETORIC_TAGS or not isinstance(idx, list):
            continue
        for i in idx[:200]:
            try:
                i = int(i)
            except (TypeError, ValueError):
                continue
            if 0 <= i < n:
                out.setdefault(i, set()).add(tag)
    return out


def llm_tags(words, ask, *, cache_get=None, cache_put=None):
    """The model's rhetoric tags for ``words`` ({position: set}), cached per
    transcript (in process, plus ``cache_get``/``cache_put`` for a durable
    store). ``ask(system, user) -> text | None``. {} when unavailable."""
    if not words or ask is None:
        return {}
    key = transcript_key(words)
    hit = _RHETORIC_CACHE.get(key)
    if hit is None and cache_get is not None:
        try:
            raw = cache_get(key)
            if raw:
                hit = {int(k): set(v) for k, v in json.loads(raw).items()}
        except Exception:  # noqa: BLE001
            hit = None
    if hit is not None:
        _RHETORIC_CACHE[key] = hit
        return hit
    try:
        text = ask(RHETORIC_SYSTEM, llm_prompt(words))
    except Exception:  # noqa: BLE001 — the lexical tagger stands alone
        text = None
    tags = parse_llm_tags(text, min(len(words), LLM_MAX_WORDS))
    if not tags:
        return {}
    if len(_RHETORIC_CACHE) >= _RHETORIC_CACHE_MAX:
        _RHETORIC_CACHE.pop(next(iter(_RHETORIC_CACHE)))
    _RHETORIC_CACHE[key] = tags
    if cache_put is not None:
        try:
            cache_put(key, json.dumps({str(k): sorted(v)
                                       for k, v in tags.items()}))
        except Exception:  # noqa: BLE001
            pass
    return tags


def merge_tags(*maps):
    out = {}
    for m in maps:
        for k, v in (m or {}).items():
            out.setdefault(int(k), set()).update(v)
    return out


# ── the program ──────────────────────────────────────────────────────────

def _timeline(edl):
    from timeline import Timeline
    return Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                    edl.get("speed") or [])


def program_fps(edl, index=None):
    canvas = (edl or {}).get("canvas") or {}
    if canvas.get("fps"):
        return float(canvas["fps"])
    v = (index or {}).get("video") or {}
    try:
        return float(v.get("fps") or 30.0)
    except (TypeError, ValueError):
        return 30.0


def frame_of(t, fps):
    return int(math.floor(float(t) * round(float(fps), 3) + 0.5 + 1e-9))


def time_of(frame, fps):
    """The centisecond EDL time that names program frame ``frame``
    (INTERFACES.md §4.1: a 1-frame device writes round(k / fps, 2))."""
    return round(frame / round(float(fps), 3), 2)


def program_words(edl, index, tl=None):
    """Kept transcript words on the program clock with their SOURCE index:
    [{i, w, t0, t1, s0, s1}] in program order. Fillers are left out."""
    tl = tl or _timeline(edl)
    out = []
    for i, w in enumerate((index or {}).get("words") or []):
        if not isinstance(w, dict) or w.get("filler"):
            continue
        try:
            s0, s1 = float(w["t0"]), float(w["t1"])
        except (KeyError, TypeError, ValueError):
            continue
        mid = tl.src_to_out((s0 + s1) / 2.0)
        if mid is None:
            continue
        o0 = tl.src_to_out(s0)
        o1 = tl.src_to_out(s1)
        half = (s1 - s0) / 2.0
        t0 = o0 if o0 is not None else max(0.0, mid - half)
        t1 = o1 if o1 is not None else mid + half
        out.append({"i": i, "w": w.get("w"), "t0": float(t0),
                    "t1": float(max(t0, t1)), "s0": s0, "s1": s1,
                    "p": w.get("p")})
    out.sort(key=lambda r: r["t0"])
    return out


def _speech_onsets(edl, index, tl):
    """The prosody grid's onsets on the program clock (stress, phrase_end,
    pause_end) and its phrase sections, plus the source-word index of each
    word onset. Never raises."""
    try:
        import prosody_grid
        import rhythm_types
        words = [w for w in (index or {}).get("words") or []
                 if isinstance(w, dict) and not w.get("filler")]
        keep = edl.get("keep") or []
        if not words or not keep:
            return {"onsets": [], "sections": [], "gaps": []}
        lo = min(float(a) for a, _b in keep)
        hi = max(float(b) for _a, b in keep)
        rmap = prosody_grid.build(words, (index or {}).get("perception"),
                                  src_range=(lo, hi))
        return rhythm_types.to_program(rmap, tl)
    except Exception as e:  # noqa: BLE001
        print(f"[planner] prosody grid skipped: {type(e).__name__}: {e}",
              flush=True)
        return {"onsets": [], "sections": [], "gaps": []}


def program_cuts(edl, index, tl=None):
    """[{t, ref, kind, shot_change}] for every cut inside the program:
    block joins (footage/insert) and shot changes inside a kept segment.
    ``ref`` is the incoming block's SOURCE second (a footage cut: the bind
    ref) or None (an insert edge: bound by time)."""
    import timeline as tlmod
    tl = tl or _timeline(edl)
    out = []
    try:
        blocks = tlmod.program_blocks(edl)
    except Exception:  # noqa: BLE001
        blocks = []
    starts = []
    for sh in (index or {}).get("shots") or []:
        try:
            starts.append(float(sh.get("start", sh.get("t0"))))
        except (TypeError, ValueError, AttributeError):
            continue
    starts.sort()
    for prev, b in zip(blocks, blocks[1:]):
        # a join of two footage blocks with no indexed camera change between
        # the outgoing frame and the incoming one is a JUMP cut inside one
        # take: a hard cut stays a hard cut (no content-born transition)
        jump = False
        if prev.get("kind") == "footage" and b.get("kind") == "footage" \
                and isinstance((index or {}).get("shots"), list):
            a_ = float(prev.get("src_end") or 0.0)
            b_ = float(b.get("src_start") or 0.0)
            lo_, hi_ = min(a_, b_), max(a_, b_)
            jump = not any(lo_ - 0.05 < c <= hi_ + 0.05 for c in starts)
        out.append({"t": float(b["out_start"]),
                    "ref": (float(b["src_start"]) if b["kind"] == "footage"
                            else None),
                    "kind": "join", "shot_change": not jump})
    for sh in (index or {}).get("shots") or []:
        try:
            s = float(sh.get("start", sh.get("t0")))
        except (TypeError, ValueError, AttributeError):
            continue
        o = tl.src_to_out(s)
        if o is None or o <= 0.05:
            continue
        rng = tl.seg_program_range(s)
        if rng and abs(o - rng[0]) < 0.05:
            continue                  # a join already
        out.append({"t": float(o), "ref": s, "kind": "shot",
                    "shot_change": True})
    out.sort(key=lambda c: c["t"])
    dedup = []
    for c in out:
        if dedup and abs(c["t"] - dedup[-1]["t"]) < 0.05:
            continue
        dedup.append(c)
    return dedup


def program_shots(cuts, program_s):
    """[(t0, t1)] program shot spans between consecutive cuts."""
    edges = [0.0] + [c["t"] for c in cuts] + [float(program_s)]
    return [(edges[k], edges[k + 1]) for k in range(len(edges) - 1)
            if edges[k + 1] > edges[k]]


def _shot_at(shots, t):
    for k, (a, b) in enumerate(shots):
        if a - 1e-6 <= t < b - 1e-6:
            return k
    return max(0, len(shots) - 1)


def program_sections(words, cuts, program_s, music_sections=None,
                     min_s=2.5):
    """[(t0, t1)] sections: a user track's own sections when it has them,
    else sentence groups split at sentence ends with a pause (or a cut),
    merged until each lasts ``min_s``."""
    if music_sections:
        out = [(float(s["t0"]), float(s["t1"])) for s in music_sections
               if float(s["t1"]) > float(s["t0"])]
        if out:
            return out
    bounds = []
    for k, w in enumerate(words):
        if not _SENTENCE_END.search(str(w.get("w") or "")):
            continue
        nxt = words[k + 1]["t0"] if k + 1 < len(words) else program_s
        if nxt - w["t1"] >= 0.25:
            bounds.append((w["t1"] + nxt) / 2.0)
    bounds += [c["t"] for c in cuts if c["kind"] == "join"]
    bounds = sorted(b for b in set(round(b, 3) for b in bounds)
                    if 0.0 < b < program_s)
    edges = [0.0] + bounds + [float(program_s)]
    out = []
    for k in range(len(edges) - 1):
        a, b = edges[k], edges[k + 1]
        if out and (b - a < min_s or out[-1][1] - out[-1][0] < min_s):
            out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    if len(out) > 1 and out[-1][1] - out[-1][0] < min_s:
        last = out.pop()
        out[-1] = (out[-1][0], last[1])
    return out


def _section_at(sections, t):
    for k, (a, b) in enumerate(sections):
        if a - 1e-6 <= t < b - 1e-6:
            return k
    return max(0, len(sections) - 1)


# ── moments ──────────────────────────────────────────────────────────────

def moments(edl, index, *, rhetoric=None, maps=None, fps=None):
    """The moment model of the current program.

    Returns {program_s, fps, moments, payoff, sections, shots, cuts, words}
    where each moment is {t, kind, strength, bind, tags, word, phrase,
    shot, section}. ``rhetoric`` is extra {source word index: set(tags)}
    (the LLM tagger); ``maps`` is {music id: PROGRAM-clock RhythmMap} of
    tracks the user supplied."""
    edl = edl or {}
    tl = _timeline(edl)
    program_s = float(tl.out_duration)
    fps = float(fps or program_fps(edl, index))
    words = program_words(edl, index, tl)
    cuts = program_cuts(edl, index, tl)
    shots = program_shots(cuts, program_s)
    music_secs = None
    for _mid, rmap in sorted((maps or {}).items()):
        if rmap.get("sections"):
            music_secs = rmap["sections"]
            break
    sections = program_sections(words, cuts, program_s, music_secs)
    lex = lexical_tags(words)
    tags_by_src = merge_tags({words[k]["i"]: v for k, v in lex.items()},
                             rhetoric or {})
    # motion_planner beats (suggest_motion_beats) as tags
    try:
        import motion_planner
        sheet = motion_planner.plan(edl, index or {}, density="balanced")
        beat_tag = {"number": "number", "number_cluster": "number",
                    "contrast": "contrast", "hero_word": "payload",
                    "list": "payload", "triad": "payload", "name": "payload"}
        for b in sheet.get("beats") or []:
            tag = beat_tag.get(b.get("kind"))
            if not tag:
                continue
            w = _beat_word(words, b, tag)
            if w is not None:
                tags_by_src.setdefault(w["i"], set()).add(tag)
    except Exception as e:  # noqa: BLE001
        print(f"[planner] beat sheet skipped: {type(e).__name__}: {e}",
              flush=True)
    speech = _speech_onsets(edl, index, tl)
    by_prog_t = {round(w["t0"], 3): w for w in words}
    stress = {}
    out = []

    def word_for(t):
        return by_prog_t.get(round(t, 3)) or _word_near(words, t, 0.02)

    def phrase_of(t):
        for s in speech.get("sections") or []:
            if s["t0"] - 1e-3 <= t <= s["t1"] + 1e-3:
                return (float(s["t0"]), float(s["t1"]))
        return None

    for o in speech.get("onsets") or []:
        if o["cls"] == "stress":
            w = word_for(o["t"])
            if w is not None:
                stress[w["i"]] = float(o.get("strength") or 0.5)
    for w in words:
        tags = tags_by_src.get(w["i"], set())
        base = stress.get(w["i"])
        if base is not None and not tags and weak_word(w["w"]):
            # heard as stressed, but a pronoun contraction or a question
            # word carries no point of its own: no device lands on it
            base = None
        if base is None and not tags:
            continue
        strength = (base if base is not None else 0.35) + sum(
            TAG_WEIGHTS[t] for t in tags if t in TAG_WEIGHTS)
        kind = "stress" if base is not None else "word"
        out.append({"t": w["t0"], "kind": kind,
                    "strength": round(min(1.0, strength), 3),
                    "bind": {"on": kind, "ref": w["i"], "rhythm": "speech"},
                    "tags": sorted(tags), "word": norm(w["w"]),
                    "word_raw": str(w["w"] or "").strip(_STRIP),
                    "src_i": w["i"], "phrase": phrase_of(w["t0"])})
    for o in speech.get("onsets") or []:
        if o["cls"] != "phrase_end":
            continue
        w = _word_ending(words, o["t"])
        if w is None:
            continue
        out.append({"t": float(o["t"]), "kind": "phrase_end",
                    "strength": round(0.25 + 0.3 * float(
                        o.get("strength") or 0.6), 3),
                    "bind": {"on": "phrase_end", "ref": w["i"],
                             "rhythm": "speech"},
                    "tags": [], "word": norm(w["w"]), "src_i": w["i"],
                    "phrase": phrase_of(w["t0"])})
    for g_i, g in enumerate(speech.get("gaps") or []):
        if float(g["t1"]) - float(g["t0"]) >= 0.6:
            out.append({"t": float(g["t0"]), "kind": "gap", "strength": 0.4,
                        "bind": {"on": "gap", "ref": g_i, "rhythm": "speech"},
                        "tags": [], "word": None, "phrase": None})
    starts = {round(w["t0"], 2) for k, w in enumerate(words)
              if k == 0 or _SENTENCE_END.search(str(words[k - 1]["w"] or ""))}
    for c in cuts:
        new_idea = any(-0.1 < s - c["t"] < 0.6 for s in starts)
        bind = ({"on": "cut", "ref": round(c["ref"], 3)}
                if c["ref"] is not None else {"on": "time",
                                              "ref": round(c["t"], 2)})
        out.append({"t": c["t"], "kind": "cut",
                    "strength": 0.6 if new_idea else 0.45, "bind": bind,
                    "tags": ["new_idea"] if new_idea else [], "word": None,
                    "phrase": None, "cut_kind": c["kind"],
                    "jump": not c.get("shot_change", True)})
    for k, (a, _b) in enumerate(sections[1:], start=1):
        out.append({"t": a, "kind": "section", "strength": 0.45,
                    "bind": {"on": "time", "ref": round(a, 2)},
                    "tags": [], "word": None, "phrase": None,
                    "section_i": k})
    for mid, rmap in sorted((maps or {}).items()):
        out += _music_moments(mid, rmap)
    out = [m for m in out if 0.0 <= m["t"] < program_s - 0.5 / fps]
    payoff = _payoff(out, words, program_s, fps,
                     declared=declared_payoff(edl))
    if payoff is not None:
        for m in out:
            if m is payoff["moment"]:
                m["kind_extra"] = "payoff"
                m["strength"] = 1.0
        out.append({**payoff["moment"], "kind": "word.payoff",
                    "strength": 1.0, "payoff": True})
    for m in out:
        m["shot"] = _shot_at(shots, m["t"])
        m["section"] = _section_at(sections, m["t"])
    out.sort(key=lambda m: (-m["strength"], m["t"]))
    out = out[:MAX_MOMENTS]
    out.sort(key=lambda m: (m["t"], m["kind"]))
    return {"program_s": program_s, "fps": fps, "moments": out,
            "payoff": payoff, "sections": sections, "shots": shots,
            "cuts": cuts, "words": words}


def _beat_word(words, beat, tag):
    """The spoken word a beat-sheet beat is about: a counter starts on its
    lead-in, so a number beat names the first figure said within 0.8 s
    after its start; anything else the word nearest its start."""
    at = float(beat["at"])
    if tag == "number":
        for w in words:
            if at - 0.05 <= w["t0"] <= at + 0.8:
                tok = norm(w["w"])
                if any(c.isdigit() for c in tok) or tok in _NUMBER_WORDS:
                    return w
    return _word_near(words, at, 0.6)


def _word_near(words, t, tol):
    best = None
    for w in words:
        d = abs(w["t0"] - t)
        if d <= tol and (best is None or d < abs(best["t0"] - t)):
            best = w
    return best


def _word_ending(words, t):
    best = None
    for w in words:
        d = abs(w["t1"] - t)
        if d <= 0.05 and (best is None or d < abs(best["t1"] - t)):
            best = w
    return best


def _music_moments(mid, rmap):
    out = []
    for i, t in enumerate(rmap.get("downbeats") or []):
        out.append({"t": float(t), "kind": "downbeat", "strength": 0.7,
                    "bind": {"on": "downbeat", "ref": i, "rhythm": mid},
                    "tags": [], "word": None, "phrase": None})
    for i, t in enumerate(rmap.get("beats") or []):
        out.append({"t": float(t), "kind": "beat", "strength": 0.45,
                    "bind": {"on": "beat", "ref": i, "rhythm": mid},
                    "tags": [], "word": None, "phrase": None})
    for i, t in enumerate(rmap.get("drops") or []):
        out.append({"t": float(t), "kind": "drop", "strength": 1.0,
                    "bind": {"on": "drop", "ref": i, "rhythm": mid},
                    "tags": [], "word": None, "phrase": None})
    for i, o in enumerate(rmap.get("onsets") or []):
        cls = str(o.get("cls") or "transient")
        out.append({"t": float(o["t"]), "kind": f"onset.{cls}",
                    "strength": round(0.3 + 0.4 * float(
                        o.get("strength") or 0.5), 3),
                    "bind": {"on": "onset", "ref": i, "rhythm": mid},
                    "tags": [], "word": None, "phrase": None})
    return out


# Where the story lands: a short's payoff is late (the line the clip was cut
# for), a figure or the far side of a contrast, in the last sentence.
PAYOFF_LATE_W = 0.45
PAYOFF_BONUS = {"number": 0.15, "after_contrast": 0.1, "last_sentence": 0.15}


def declared_payoff(edl):
    """(start, end) of the payoff lockup the edit itself declares — a
    motion item with tier 'payoff', id 'payoff'/'payoff*', or a purpose
    that starts 'payoff' (render_qc reads the payoff the same way) — or
    None. The editor's payoff is the story's landing: devices that land
    on "the payoff" land on it, not on a louder figure earlier on."""
    best = None
    for m in (edl or {}).get("motion") or []:
        if not isinstance(m, dict):
            continue
        params = m.get("params") or {}
        mid = str(m.get("id") or "")
        rank = (0 if (params.get("tier") or m.get("tier")) == "payoff" else
                1 if mid == "payoff" else
                2 if mid.startswith("payoff") else
                3 if str(m.get("purpose") or "").lower().startswith("payoff")
                else None)
        if rank is None:
            continue
        try:
            span = (float(m["start"]), float(m["end"]))
        except (KeyError, TypeError, ValueError):
            continue
        if best is None or (rank, -span[0]) < (best[0], -best[1][0]):
            best = (rank, span)
    return best[1] if best else None


def _payoff(ms, words, program_s, fps, declared=None):
    """The payoff: the payload word where the story lands (strength, how
    late it is, a figure, the far side of a contrast, the last sentence);
    its hold runs PAYOFF_HOLD_S past the word's phrase end. When the edit
    declares its payoff lockup (``declared``: its window) the word is
    chosen inside that window."""
    pool = [m for m in ms if m["kind"] in ("stress", "word")
            and "payload" in (m.get("tags") or [])]
    if not pool:
        pool = [m for m in ms if m["kind"] == "stress"]
    if declared:
        a, b = declared
        inside = [m for m in ms if m["kind"] in ("stress", "word")
                  and a - 0.35 <= m["t"] <= b]
        pref = [m for m in inside if m in pool] or inside
        if pref:
            pool = pref
    if not pool:
        return None
    last_start = 0.0
    for k in range(len(words) - 1):
        if _SENTENCE_END.search(str(words[k]["w"] or "")) and \
                k + 1 < len(words) - 2:
            last_start = words[k + 1]["t0"]
    contrasts = [m["t"] for m in ms if "contrast" in (m.get("tags") or [])
                 or "negation" in (m.get("tags") or [])]

    def score(m):
        tags = set(m.get("tags") or ())
        sc = m["strength"] + PAYOFF_LATE_W * (m["t"] / max(1e-6, program_s))
        if "number" in tags:
            sc += PAYOFF_BONUS["number"]
        if any(0.3 < m["t"] - c < 6.0 for c in contrasts):
            sc += PAYOFF_BONUS["after_contrast"]
        if m["t"] >= last_start:
            sc += PAYOFF_BONUS["last_sentence"]
        return sc
    best = max(pool, key=lambda m: (score(m), m["t"]))
    w = next((x for x in words if x["i"] == best.get("src_i")), None)
    end_word = w["t1"] if w else best["t"] + 0.3
    phrase = best.get("phrase")
    end = max(end_word, phrase[1] if phrase else end_word)
    hold = min(PAYOFF_HOLD_S[1], max(PAYOFF_HOLD_S[0], end - best["t"]))
    return {"t": best["t"], "hold_end": round(min(program_s, end + hold), 3),
            "word": best.get("word"), "moment": best}


# ── cards → candidates ──────────────────────────────────────────────────

# ── what the track cards cannot say (Wave 1 integration review) ──────────
# A card says WHAT a device is and the moments it may bind to. The cards of
# the five device tracks carry no placement, so every sustained device — a
# grey world, a poster, a finish, a grade section, a word behind the head —
# was proposed as a 2-frame flicker; and every card scored alike, so on a
# talking head the alphabet put composites and finishes first (a CRT on a
# jump cut, a split tone for 0.07 s, a difference blend on the payoff, a
# ghost stroke on a graphic nothing hides). CARD_TASTE is the planner's
# half, kept in one place:
#
#   place     the window (PLANNER_KEYS) when the card names none
#   binds_to  the moments the device earns, replacing the card's list
#   values    item field defaults (first choice) the planner writes
#   prior     how readily a premium editor reaches for it on speech
#             (multiplies the moment's score; 0 = never proposed
#             automatically: it needs the editor's intent — a region, a
#             file, footage for the letters)
#   gate      a metaphor the words must carry (METAPHOR_WORDS) — a finish
#             is "the inside of the machine", "a screen", "the past",
#             never a texture
#   host      which motion graphic a composite may modify
CARD_TASTE = {
    # a silhouette flash is the payoff's or the turn's (or a real cut's on a
    # user track), never any stressed word
    "subject_fill_flash": {"prior": 1.0, "binds_to": [
        "word.payoff", "word.contrast", "word.negation", "beat.kick",
        "downbeat", "cut"]},
    "subject_negate": {"prior": 0.75},
    "subject_poster": {"place": {"span": "payoff_hold", "min_s": 1.0,
                                 "max_s": 2.5}, "prior": 0.9},
    "subject_isolate": {"place": {"span": "payoff_hold", "min_s": 1.5,
                                  "max_s": 3.5}, "prior": 0.9},
    "subject_glow": {"place": {"span": "phrase", "min_s": 0.8,
                               "max_s": 2.0}, "prior": 0.6},
    "subject_outline": {"place": {"span": "phrase", "min_s": 0.8,
                                  "max_s": 2.0}, "prior": 0.5},
    "subject_exposure": {"place": {"span": "phrase", "min_s": 0.8,
                                   "max_s": 2.5}, "prior": 0.45},
    "subject_echo": {"place": {"span": "phrase", "min_s": 0.5,
                               "max_s": 1.2}, "prior": 0.5,
                     "gate": "speed"},
    "background_swap": {"place": {"span": "phrase", "min_s": 1.0,
                                  "max_s": 2.5}, "prior": 0.35},
    "background_blur": {"place": {"span": "phrase", "min_s": 1.0,
                                  "max_s": 3.0}, "prior": 0.4},
    "background_strobe": {"place": {"duration_s": 1.0}, "prior": 0.2},
    "background_subject_hold": {"place": {"duration_s": 0.8},
                                "prior": 0.25},
    # the hero word behind the head is a word worth setting: the payoff, a
    # payload, a number, an emotion or the turn (never a bare stress)
    "type_behind_graphic": {"place": {"span": "payoff_hold", "min_s": 1.0,
                                      "max_s": 2.2}, "prior": 1.0,
                            "binds_to": ["word.payoff", "word.payload",
                                         "word.number", "word.emotion",
                                         "word.contrast"]},
    "type_behind_text": {"place": {"span": "payoff_hold", "min_s": 1.0,
                                   "max_s": 2.2}, "prior": 0.85,
                         "binds_to": ["word.payoff", "word.payload",
                                      "word.number", "word.emotion"]},
    # one B&W setup per short (two read as a grade, not a moment)
    "grade_section": {"place": {"span": "setup"}, "prior": 0.85,
                      "binds_to": ["word.contrast", "word.negation",
                                   "section", "drop"],
                      "budget": {"calm": {"per_short": 1},
                                 "designed": {"per_short": 1},
                                 "charged": {"per_short": 2}}},
    "grade_split_tone": {"place": {"span": "section"}, "prior": 0.25},
    "grade_isolate_hue": {"place": {"span": "section"}, "prior": 0.0},
    "grade_lut_import": {"prior": 0.0},
    "grade_shot_match": {"prior": 0.0},
    "finish_crt": {"place": {"span": "shot", "min_s": 1.0, "max_s": 4.0},
                   "prior": 0.5, "gate": "screen"},
    "finish_lcd": {"place": {"span": "shot", "min_s": 1.0, "max_s": 4.0},
                   "prior": 0.5, "gate": "screen"},
    "finish_led": {"place": {"span": "shot", "min_s": 1.0, "max_s": 4.0},
                   "prior": 0.4, "gate": "screen"},
    "finish_film": {"place": {"span": "phrase", "min_s": 1.0,
                              "max_s": 3.0}, "prior": 0.45, "gate": "past"},
    "finish_halftone": {"place": {"span": "phrase", "min_s": 1.0,
                                  "max_s": 2.5}, "prior": 0.4,
                        "gate": "print"},
    "finish_xerox": {"place": {"span": "phrase", "min_s": 1.0,
                               "max_s": 2.5}, "prior": 0.35,
                     "gate": "print"},
    "finish_thermal": {"place": {"duration_s": 0.6}, "prior": 0.55,
                       "gate": "heat"},
    "finish_xray": {"place": {"duration_s": 0.6}, "prior": 0.5,
                    "gate": "inside"},
    "finish_night_vision": {"place": {"duration_s": 0.8}, "prior": 0.4,
                            "gate": "night"},
    "finish_blueprint": {"place": {"duration_s": 0.6}, "prior": 0.5,
                         "gate": "mechanism"},
    "finish_line_art": {"place": {"duration_s": 0.6}, "prior": 0.4,
                        "gate": "mechanism"},
    "finish_solarise": {"place": {"duration_s": 0.6}, "prior": 0.3,
                        "gate": "icon"},
    "finish_posterise": {"place": {"duration_s": 0.6}, "prior": 0.3,
                         "gate": "icon"},
    "finish_kaleidoscope": {"place": {"duration_s": 0.8}, "prior": 0.35,
                            "gate": "mind"},
    "finish_band_tear": {"place": {"frames": 3}, "prior": 0.35,
                         "gate": "glitch"},
    "finish_pixel_stretch": {"place": {"frames": 4}, "prior": 0.35,
                             "gate": "glitch"},
    "composite_ghost": {"prior": 0.9, "host": "behind"},
    "composite_light": {"prior": 0.6, "host": "light"},
    "composite_blend": {"prior": 0.45, "host": "type",
                        "values": {"blend": "auto"}},
    "composite_fill": {"prior": 0.0},
    "composite_window": {"prior": 0.0},
    "region_material": {"prior": 0.0},
    "region_soften": {"prior": 0.0},
    "region_tone": {"prior": 0.0},
    "strobe_frames": {"prior": 0.0},
    "variant_set": {"prior": 0.0},
    "accent_negative": {"prior": 0.8},
    "accent_white_hit": {"prior": 0.7},
    "accent_colour_hit": {"prior": 0.55},
    "accent_threshold": {"prior": 0.5},
    "accent_mono": {"prior": 0.5},
    "accent_posterize": {"prior": 0.45},
    "accent_box_invert": {"prior": 0.45},
    "accent_rgb_slice": {"prior": 0.4},
    "accent_mirror": {"prior": 0.35},
    "accent_hue_step": {"prior": 0.35},
}
# A content-born transition is THE device of a real camera or scene change
# (jump cuts never take one), so it outranks a punctuation frame there.
FAMILY_PRIOR = {"type_depth": 0.9, "matte_fx": 0.6, "punctuation": 0.5,
                "transition": 0.85, "finish": 0.3, "composite": 0.4}
# The words that carry a finish's metaphor (a moment's own word or one said
# within METAPHOR_REACH_S after it). "glitch" also takes a hard cut.
METAPHOR_WORDS = {
    "heat": frozenset("""heat hot warm warmer warming burn burning burned fire
        fires flame flames energy power powerful engine engines fuel sun solar
        body bodies blood temperature thermal infrared radiation nuclear
        reactor explode explosion rocket rockets launch plasma""".split()),
    "inside": frozenset("""inside internal body bodies bone bones skeleton
        brain organ organs hidden beneath underneath under secret truth
        really scan x-ray xray cancer cell cells""".split()),
    "mechanism": frozenset("""how works mechanism system systems built build
        building design designed engineering engineer engineers code
        software model models machine machines factory factories robot robots
        architecture structure blueprint plan plans circuit chip chips
        algorithm network""".split()),
    "screen": frozenset("""screen screens tv television computer computers
        monitor display phone phones iphone internet online video videos
        broadcast pixel pixels digital app apps game games gaming
        youtube""".split()),
    "past": frozenset("""past history historical years ago old older back
        then decade decades century remember memory memories childhood
        grandfather grandmother vintage nostalgia once""".split()),
    "print": frozenset("""newspaper newspapers news print printed paper
        papers magazine headline headlines press propaganda poster
        posters""".split()),
    "night": frozenset("""night dark darkness spy spies surveillance
        military war soldier soldiers hunt hunting watch watching
        watched""".split()),
    "icon": frozenset("""icon iconic art artist artists pop culture famous
        fame celebrity legend legendary""".split()),
    "mind": frozenset("""dream dreams mind minds psychedelic infinite
        infinity trip imagine imagination universe consciousness
        reality""".split()),
    "speed": frozenset("""fast faster fastest speed speeds rapid rapidly
        quick quickly accelerate accelerating acceleration velocity move
        moving moves motion run running rush race racing sprint orbit
        momentum""".split()),
    "glitch": frozenset("""glitch broken break breaks crash crashed error
        errors bug bugs hack hacked chaos chaotic""".split()),
}
METAPHOR_REACH_S = 1.5
# Content-born transitions per short at an intensity (all junction styles
# together: a short with a different wipe on every cut reads as a template).
TRANSITION_CAP = {"calm": 1, "designed": 2, "charged": 3}
# No flash device lands this early: the hook's first second is the frame
# the viewer decides on (recipes keep negatives out of it too).
HOOK_FLASH_FREE_S = 1.0
# How many devices a program earns at an intensity (per second of program;
# never fewer than 2): a 24 s talking head is offered 6 at "designed", not
# 8 — "two well-placed devices beat ten" — and a candidate scoring under
# SCORE_FLOOR of the best one is a filler, not a choice.
DENSITY_PER_S = {"calm": 1 / 8.0, "designed": 1 / 4.0, "charged": 1 / 2.5}
SCORE_FLOOR = 0.3
# Templates a light composite is for (compositor.LIGHT_TEMPLATES).
LIGHT_HOSTS = ("flash_transition", "light_leak", "film_burn")
# A blend never touches the lockups the short is built on, or captions.
BLEND_NEVER_TIERS = ("hook", "payoff")


def card_taste(card):
    """(card with the planner's taste merged, prior, gate, host) —
    CARD_TASTE over the track's card (its own placement keys win)."""
    t = CARD_TASTE.get(card.get("id")) or {}
    prior = t.get("prior")
    if prior is None:
        prior = FAMILY_PRIOR.get(card.get("family"), 0.5)
    if not (t.get("place") or t.get("binds_to") or t.get("values")
            or t.get("budget")):
        return card, float(prior), t.get("gate"), t.get("host")
    out = dict(card)
    if t.get("budget"):
        out["budget"] = dict(card.get("budget") or {}, **t["budget"])
    params = dict(card.get("params") or {})
    if t.get("place") and not any(k in params for k in PLANNER_KEYS):
        params.update(t["place"])
    for k, v in (t.get("values") or {}).items():
        cur = params.get(k)
        if isinstance(cur, list):
            params[k] = [v] + [x for x in cur if x != v]
        else:
            params[k] = v
    out["params"] = params
    if t.get("binds_to"):
        out["binds_to"] = list(t["binds_to"])
    return out, float(prior), t.get("gate"), t.get("host")


def metaphor_ok(gate, moment, plan):
    """Does the moment carry the metaphor ``gate`` names? (a word moment:
    its own word; a cut or section: a word said within METAPHOR_REACH_S
    after it; a glitch also takes a hard cut)."""
    if not gate:
        return True
    vocab = METAPHOR_WORDS.get(gate) or frozenset()
    if gate == "glitch" and moment.get("kind") == "cut":
        return True
    if moment.get("word"):
        # a word moment carries it itself (the device lands ON the word)
        return norm(moment.get("word")) in vocab
    t = float(moment["t"])
    for w in plan.get("words") or ():
        t0 = float(w.get("t0", -1.0))
        if t - 0.05 <= t0 <= t + METAPHOR_REACH_S and \
                norm(w.get("w")) in vocab:
            return True
    return False


def _type_on_screen(edl, t, payoff=False):
    """Is a type lockup of the edit already on screen around program
    second ``t``? (light graphics and the caption track are not type
    lockups; on the payoff word the payoff's own lockup may be the one the
    planner moves behind the speaker, so only OTHER lockups count)."""
    for m in (edl or {}).get("motion") or []:
        if not isinstance(m, dict) or m.get("template") in LIGHT_HOSTS:
            continue
        if m.get("_synthetic") or m.get("template") == "caption_motion" or str(m.get("id") or "") \
                .startswith("__captions_"):
            continue
        params = m.get("params") or {}
        if payoff and (params.get("tier") == "payoff"
                       or str(m.get("id") or "").startswith("payoff")):
            continue
        try:
            a, b = float(m["start"]), float(m["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if a - 0.3 < t < b:
            return True
    return False


# A flat colour the planner lays under type (a silhouette fill, a poster
# field, a solid background) keeps at least this WCAG contrast ratio with
# every lockup ink on screen over its window. A white silhouette under a
# white-and-yellow payoff lockup washed the payoff out on the very frame it
# landed (W1-S1 Thiel, "140"); a black silhouette makes the same hit and
# the type pops.
INK_CONTRAST_MIN = 3.0
INK_KEYS = ("color", "accent", "left_color", "right_color", "highlight")


def _lum(hexv):
    v = str(hexv or "").strip().lstrip("#")
    if len(v) != 6:
        return None
    try:
        rgb = [int(v[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    except ValueError:
        return None
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
           for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast_ratio(a, b):
    la, lb = _lum(a), _lum(b)
    if la is None or lb is None:
        return 21.0
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _resolve_hex(value, edl):
    v = str(value or "").strip().lower()
    if v in ("white", "black"):
        return "#ffffff" if v == "white" else "#000000"
    if v == "accent":
        try:
            import subject_fx
            return subject_fx.accent_hex(edl)
        except Exception:  # noqa: BLE001 — the house accent
            return "#ffe14d"
    return v if v.startswith("#") else None


def lockup_inks(edl, t0, t1):
    """#rrggbb inks of the type lockups on screen over [t0, t1) (light
    graphics and the caption track excluded)."""
    out = []
    for m in (edl or {}).get("motion") or []:
        if not isinstance(m, dict) or m.get("template") in LIGHT_HOSTS:
            continue
        if m.get("_synthetic") or m.get("template") == "caption_motion" \
                or str(m.get("id") or "").startswith("__captions_"):
            continue
        try:
            a, b = float(m["start"]), float(m["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if b <= t0 or a >= t1:
            continue
        params = m.get("params") or {}
        for k in INK_KEYS:
            h = _resolve_hex(params.get(k), edl)
            if h:
                out.append(h)
        if not any(params.get(k) for k in INK_KEYS):
            out.append("#ffffff")             # templates default to white
    return out


def ink_safe(edl, t0, t1, choices):
    """The first of ``choices`` (FxColour values, preference order) that
    every lockup ink over [t0, t1) reads on (INK_CONTRAST_MIN), else the
    one with the best worst-case contrast; ``choices[0]`` when no lockup is
    on screen."""
    choices = [c for c in choices or () if c]
    if not choices:
        return None
    inks = lockup_inks(edl, t0, t1)
    if not inks:
        return choices[0]
    best, best_c = choices[0], -1.0
    for c in choices:
        h = _resolve_hex(c, edl)
        if h is None:
            continue
        worst = min(contrast_ratio(h, ink) for ink in inks)
        if worst >= INK_CONTRAST_MIN:
            return c
        if worst > best_c:
            best, best_c = c, worst
    return best


def host_ok(host, item):
    """May a composite card of host kind ``host`` modify motion ``item``?"""
    if not host:
        return True
    if host == "behind":
        return item.get("layer") == "behind_subject"
    if host == "light":
        return item.get("template") in LIGHT_HOSTS
    if host == "type":
        if item.get("template") in LIGHT_HOSTS or \
                item.get("layer") == "behind_subject":
            return False
        params = item.get("params") or {}
        ident = " ".join(str(x or "").lower() for x in (
            item.get("id"), params.get("tier"), params.get("purpose")))
        if any(t in ident for t in BLEND_NEVER_TIERS + ("caption",)):
            return False
        return bool(params.get("text") or params.get("lines"))
    return True


def _matches(card, moment):
    """(ok, fit): does the moment kind fit one of the card's binds_to?
    ``fit`` prefers earlier entries."""
    binds = list(card.get("binds_to") or ())
    kind = moment["kind"]
    tags = set(moment.get("tags") or ())
    for k, b in enumerate(binds):
        base, _, refine = b.partition(".")
        ok = False
        if b == "word.payoff":
            ok = kind == "word.payoff"
        elif base == "word":
            ok = kind in ("word", "stress") and (
                not refine or refine in tags)
        elif base == "stress":
            # a negation or a contrast word is accented for meaning
            # (prosody_grid.ACCENTABLE) even when the grid did not pick it
            ok = kind == "stress" or (kind == "word" and bool(
                tags & {"negation", "contrast"}))
        elif base == "beat":
            ok = kind == "beat" or (refine and kind == f"onset.{refine}")
        elif base == "onset":
            ok = kind.startswith("onset") and (
                not refine or kind == f"onset.{refine}")
        elif base == "time":
            ok = kind not in ("word.payoff",)
        else:
            ok = kind == base
        if ok:
            return True, 1.0 - 0.08 * k
    return False, 0.0


def card_values(card):
    """(field values, placement) from a card's params: list values are the
    planner's choices, first = default (INTERFACES_W1.md §3.6)."""
    vals, place = {}, {}
    for k, v in (card.get("params") or {}).items():
        v0 = v[0] if isinstance(v, list) and v else v
        (place if k in PLANNER_KEYS else vals)[k] = v0
    return vals, place


def _family_ledger(card):
    import taste
    return taste._CARD_LEDGER.get(card.get("family"))


def _flash(card):
    return bool((card.get("owner_flags") or {}).get("flash"))


def _needs_matte(card):
    return "matte.person" in ((card.get("needs") or {}).get("sidecars")
                              or ())


def _span(place, moment, plan, fps, card):
    """(start, end) of a picture-stage item placed on ``moment``."""
    f0 = frame_of(moment["t"], fps) + int(place.get("offset_f") or 0)
    f0 = max(0, f0)
    start = time_of(f0, fps)
    span = place.get("span") or ("frames" if place.get("frames") or
                                 not place.get("duration_s") else None)
    if span == "setup":
        # the window ENDS on the moment (a mono setup whose colour lands ON
        # the turn word: the grade's ramp_out_f ends on that frame) and
        # starts where the setup does: its section's start when that leaves
        # SETUP_MIN_S, never more than SETUP_MAX_S or SETUP_SHARE of the
        # program before it (B&W is a device, never most of the short)
        sec = plan["sections"][moment["section"]] if plan.get(
            "sections") else (0.0, moment["t"])
        reach = min(SETUP_MAX_S, SETUP_SHARE * float(plan["program_s"]))
        a = max(0.0, start - reach)
        if start - float(sec[0]) >= SETUP_MIN_S:
            a = max(a, float(sec[0]))
        pay = plan.get("payoff")
        if pay and a < float(pay["t"]) < start - 0.05:
            # never grey over the payoff: the setup starts after its hold
            a = max(a, float(pay.get("hold_end") or pay["t"]))
        a = time_of(frame_of(a, fps), fps)
        if start - a < SETUP_MIN_S:
            return start, start             # no room: build_op refuses
        return a, start
    min_s = float(place.get("min_s") or 0.5)
    if place.get("duration_s"):
        end = start + float(place["duration_s"])
    elif span == "phrase":
        ph = moment.get("phrase")
        end = max(ph[1] if ph else start, start + min_s)
    elif span == "sentence" or span == "section":
        a, b = plan["sections"][moment["section"]]
        end = max(b, start + 0.5)
    elif span == "shot":
        a, b = plan["shots"][moment["shot"]]
        end = max(b, start + 0.5)
    elif span == "payoff_hold" and plan.get("payoff") and \
            moment.get("kind") == "word.payoff":
        end = max(plan["payoff"]["hold_end"], start + 0.5)
    elif span == "payoff_hold":
        # off the payoff a hold-length device holds its own phrase, briefly
        ph = moment.get("phrase")
        end = min(start + 2.5, max(ph[1] if ph else start, start + 1.2))
    else:
        n = int(place.get("frames") or (1 if card.get("family")
                                        in PUNCTUATION else 2))
        return start, time_of(f0 + max(1, n), fps)
    if place.get("max_s"):
        end = min(end, start + float(place["max_s"]))
    end = min(float(plan["program_s"]), end)
    end = time_of(max(f0 + 1, frame_of(end, fps)), fps)
    return start, end


def _type_text(moment, plan):
    """The words a type device sets: the payoff/stress word with its
    neighbours, as said (never invented copy), upper-cased."""
    words = plan["words"]
    k = next((n for n, w in enumerate(words) if w["i"] == moment.get(
        "src_i")), None)
    if k is None:
        return None
    pick = [words[k]]
    for j in (k + 1, k + 2):
        if j < len(words) and "number" in (moment.get("tags") or []) and \
                words[j]["t0"] - pick[-1]["t1"] < 0.3 and len(pick) < 3:
            pick.append(words[j])
            if any(c.isalnum() for c in str(words[j]["w"])):
                break
    text = " ".join(str(w["w"] or "").strip(_STRIP) for w in pick).strip()
    text = re.sub(r"\s+([%])", r"\1", text)
    return text.upper() or None


def build_op(card, moment, plan, *, item_id, fps, edl):
    """(op, item, at) for one card on one moment, or (None, None, reason)."""
    vals, place = card_values(card)
    stage = card.get("stage")
    bind = dict(moment["bind"])
    if place.get("offset_f"):
        bind["offset_f"] = int(place["offset_f"])
    if card.get("fx_type"):
        item = {"id": item_id, "type": card["fx_type"], "stage": stage,
                **vals, "device": card["id"], "bind": bind}
        if stage == "junction":
            if moment["kind"] != "cut":
                return None, None, "a junction lands on a cut"
            at = time_of(frame_of(moment["t"], fps), fps)
            item["at"] = at
            return ({"action": "upsert", "layer": "fx", "id": item_id,
                     "value": item}, item, at)
        if stage not in PICTURE_STAGES:
            return None, None, f"stage {stage} is not planned"
        start, end = _span(place, moment, plan, fps, card)
        if end <= start:
            return None, None, ("no room for its setup before the moment"
                                if place.get("span") == "setup" else
                                "no room before the program end")
        item["start"], item["end"] = start, end
        # a flat colour under type keeps the type readable (ink_safe)
        key = ("field" if item.get("kind") == "poster" else "colour"
               if item.get("kind") in ("fill", "solid") else None)
        if key and item.get(key) is not None:
            opts = (card.get("params") or {}).get(key)
            opts = list(opts) if isinstance(opts, list) else [item[key]]
            if item[key] not in opts:
                opts.insert(0, item[key])
            item[key] = ink_safe(edl, start, end, opts)
        return ({"action": "upsert", "layer": "fx", "id": item_id,
                 "value": item}, item, start)
    if stage == "motion" and card.get("verb") == "set_composite":
        # a composite device on the motion graphic that sits on the moment
        host = (CARD_TASTE.get(card.get("id")) or {}).get("host")
        hit = None
        for m in edl.get("motion") or []:
            try:
                if float(m["start"]) - 0.2 <= moment["t"] <= float(m["end"]) \
                        and not m.get("composite") and host_ok(host, m):
                    hit = m
                    break
            except (KeyError, TypeError, ValueError):
                continue
        if hit is None:
            return None, None, "no plain motion graphic on that moment"
        op = {"action": "upsert", "layer": "motion", "id": hit["id"],
              "value": {"id": hit["id"], "composite": dict(vals)}}
        return op, op["value"], float(hit["start"])
    if stage == "motion":
        text = _type_text(moment, plan)
        if not text:
            return None, None, "no spoken words to set"
        start, end = _span(place, moment, plan, fps, card)
        params = dict(vals.pop("params", None) or {})
        params.setdefault("text", text)
        item = {"id": item_id, **vals, "start": start, "end": end,
                "params": params}
        return ({"action": "upsert", "layer": "motion", "id": item_id,
                 "value": item}, item, start)
    return None, None, f"cards writing {stage} are not planned"


# A window this long is a sustained device (a grey world, a poster, a
# finish, a word behind the head); shorter ones are hits.
SUSTAINED_S = 0.5


def _point_card(card):
    """Is this card a hit (a punctuation frame, a flash, a junction) rather
    than a sustained window?"""
    if card.get("family") in PUNCTUATION + ("transition",):
        return True
    _vals, place = card_values(card)
    return not (place.get("span") or place.get("duration_s"))


def _overlaps_same(item, cid, chosen, existing):
    """Does ``item`` overlap a window it must not share? Two windows of the
    same card never overlap (two B&W setups laid over each other read as a
    mistake), and two SUSTAINED devices never stack (a poster inside a grey
    setup is two looks at once); a hit (a flash, a negative) may land
    inside a sustained window — that is the payoff flash in a grey world."""
    try:
        a, b = float(item["start"]), float(item["end"])
    except (KeyError, TypeError, ValueError):
        return False
    long_ = b - a >= SUSTAINED_S
    others = [(c["device"], c["op"]["value"]) for c in chosen
              if c["op"].get("layer") == "fx"] + [
        (e["device"], e["item"]) for e in existing]
    for dev, o in others:
        try:
            oa, ob = float(o["start"]), float(o["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (oa < b and a < ob):
            continue
        if dev == cid or (long_ and ob - oa >= SUSTAINED_S):
            return True
    return False


def _existing(edl, cards):
    """[{id, device, card, family, t, flash, layer}] devices in the EDL."""
    out = []
    for it in edl.get("fx") or []:
        if not isinstance(it, dict):
            continue
        card = cards.get(it.get("device")) or {}
        t = it.get("start") if it.get("start") is not None else it.get("at")
        try:
            hit = it.get("at") is None and float(it["end"]) - float(
                it["start"]) < SUSTAINED_S
        except (KeyError, TypeError, ValueError):
            hit = False
        out.append({"id": it.get("id"), "device": it.get("device"),
                    "card": card, "family": card.get("family"),
                    "type": it.get("type"), "hit": hit,
                    "t": float(t) if t is not None else None,
                    "flash": _flash(card) if card else it.get("type") in (
                        "accent", "strobe"),
                    "layer": "fx", "item": it})
    return out


def _flash_events(edl, chosen, extra, fps, opted_in):
    """(kept, thinned) through flash_safety with the EDL's own flashes
    weighted above any candidate (a plan never thins the user's edit)."""
    import flash_safety
    events = []
    for e in flash_safety.flash_events(edl):
        events.append({"t": e["t"], "strength": 9.0, "id": e.get("id")})
    for c in chosen + [extra]:
        for t in c["flash_t"]:
            events.append({"t": t, "strength": c["score"],
                           "id": c["candidate_id"]})
    return flash_safety.plan(events, fps, opted_in=opted_in)


def _flash_times(item):
    try:
        import flash_safety
        got = flash_safety.member_flash_times(item)
    except Exception:  # noqa: BLE001
        got = None
    if got is not None:
        return got
    t = item.get("start") if item.get("start") is not None else item.get("at")
    return [float(t)] if t is not None else []


def suggest(edl, index, *, intensity=DEFAULT_INTENSITY, window=None,
            families=None, asked=(), max_n=MAX_CANDIDATES, cards=None,
            rhetoric=None, maps=None, matte=None, weights=None,
            lane="interactive", sidecar_status=None, duration=None,
            media_keys=None, request_text=None, validate=True):
    """<= ``max_n`` ranked candidates (INTERFACES_W1.md §8).

    Each candidate: {device, family, verb, op, at, bind, reason, exemplar,
    est_ms, owner_flags, rank} plus ``detail`` {score, moment, rung,
    notes}. ``op`` is the exact apply_edit_batch operation; the planner
    never writes. ``weights`` is preference_memory's {family or card id:
    factor}; ``matte`` {status, estimate_s, person}; ``sidecar_status``
    {kind: status} for the resolver; ``asked`` what the user asked for
    (zoom, sfx, flash)."""
    import devices
    if intensity not in INTENSITIES:
        raise ValueError(f"intensity is one of {INTENSITIES}")
    edl = copy.deepcopy(edl or {})
    cards = cards if cards is not None else devices.load_cards()
    max_n = max(1, min(MAX_CANDIDATES, int(max_n or MAX_CANDIDATES)))
    asked = {str(a).lower() for a in (asked or ())}
    plan = moments(edl, index, rhetoric=rhetoric, maps=maps)
    max_n = min(max_n, max(2, int(math.ceil(
        float(plan["program_s"]) * DENSITY_PER_S.get(intensity, 0.25)))))
    fps = plan["fps"]
    lo, hi = (window or (0.0, plan["program_s"]))
    lo, hi = float(lo or 0.0), float(hi if hi is not None
                                     else plan["program_s"])
    fam_filter = {str(f) for f in (families or ())}
    weights = weights or {}
    opted_in = bool(asked & {"flash", "flashing", "strobe", "flash_optin"})
    try:
        import flash_safety
        opted_in = opted_in or flash_safety.opted_in(request_text)
    except Exception:  # noqa: BLE001
        pass
    matte = matte or {}
    sidecar_status = dict(sidecar_status or {})
    if matte.get("status") in ("ready", "partial"):
        sidecar_status.setdefault("matte.person", "ready")
    floor = MIN_STRENGTH[intensity]
    existing = _existing(edl, cards)
    used_ids = {str(i.get("id")) for i in edl.get("fx") or []
                if isinstance(i, dict)} | {
        str(m.get("id")) for m in edl.get("motion") or []
        if isinstance(m, dict)}
    import taste
    try:
        led = taste.ledger(edl, intensity=intensity, index=index)
        fam_budget = dict(led.budgets)
        fam_count = dict(led.counts)
        fmt = led.fmt
    except Exception:  # noqa: BLE001
        fam_budget, fam_count, fmt = {}, {}, "C"
    pairs = []
    for cid, card in sorted(cards.items()):
        ok, why = devices.proposable(card, asked)
        if not ok:
            continue
        card, prior, gate, _host = card_taste(card)
        if prior <= 0.0:
            continue               # needs the editor's intent (CARD_TASTE)
        if fam_filter and card.get("family") not in fam_filter and \
                (_family_ledger(card) or "") not in fam_filter:
            continue
        if card.get("family") in MATTE_FAMILIES and \
                matte.get("person") is False:
            continue
        rung = devices.resolve_detail(cid, lane=lane,
                                      sidecar_status=sidecar_status,
                                      frames=2, cards=cards)
        notes = []
        rung_factor = 1.0
        pending = [k for k in ((card.get("needs") or {}).get("sidecars")
                               or ()) if sidecar_status.get(k) != "ready"]
        if rung["rung"] == "omit":
            if not pending or rung["reason"] == "dark":
                continue
            rung_factor = 0.6
            notes.append(f"{', '.join(pending)} not measured yet: measuring "
                         f"(≈{int(matte.get('estimate_s') or 60)} s); "
                         "until then this device is left out of previews")
        elif rung["rung"] == "substitute":
            rung_factor = 0.75 if pending else 0.6
            if rung.get("substitute") == "face_safe_slam" and pending:
                # written through apply_edit_batch (this op) a word behind
                # the head stores no face-safe placement: a preview drawn
                # before the matte is measured leaves it OUT (and says so)
                notes.append(
                    f"{', '.join(pending)} is measuring (≈"
                    f"{int(matte.get('estimate_s') or 60)} s): a preview "
                    "before then leaves the word out and says so; preview "
                    "once the ANALYSIS line reads the matte ready")
            else:
                notes.append(f"renders as {rung.get('substitute')} until "
                             + (", ".join(pending) + " is measured (≈"
                                f"{int(matte.get('estimate_s') or 60)} s)"
                                if pending else rung["reason"]))
        elif rung["rung"] == "reduced":
            rung_factor = 0.85
            notes.append(f"reduced rung: {rung['reason']}")
        pref = float(weights.get(cid, 1.0)) * float(
            weights.get(card.get("family"), 1.0))
        if pref <= 0.0:
            continue
        pref *= prior
        for m in plan["moments"]:
            if not lo - 1e-6 <= m["t"] < hi:
                continue
            fits, fit = _matches(card, m)
            if not fits or m["strength"] < floor or \
                    not format_allows(fmt, card.get("family"), m) or \
                    not metaphor_ok(gate, m, plan):
                continue
            if m.get("jump") and card.get("family") == "transition":
                continue           # a jump cut inside one take stays hard
            if _flash(card) and m["t"] < HOOK_FLASH_FREE_S:
                continue           # the hook's first second is never a flash
            if card.get("family") == "type_depth" and _type_on_screen(
                    edl, m["t"], m.get("kind") == "word.payoff"):
                continue           # one lockup at a time: no word on a word
            score = m["strength"] * fit * pref * rung_factor
            pairs.append({"card": card, "moment": m, "score": score,
                          "rung": rung, "notes": notes})
    pairs.sort(key=lambda p: (-p["score"], p["moment"]["t"],
                              p["card"]["id"]))
    if pairs:
        top = pairs[0]["score"]
        pairs = [p for p in pairs if p["score"] >= SCORE_FLOOR * top]
    chosen = []
    per_card = {}
    sec_punct = {}
    shot_cards = {}
    payoff = plan.get("payoff")
    for e in existing:
        if e["t"] is None or not e["card"]:
            continue
        per_card.setdefault(e["device"], []).append(e["t"])
        sec = _section_at(plan["sections"], e["t"])
        if e["family"] in PUNCTUATION:
            sec_punct.setdefault(sec, e["device"])
        shot_cards.setdefault(e["device"], set()).add(
            _shot_at(plan["shots"], e["t"]))
    media_keys = media_keys if media_keys is not None else set()
    rejected = []
    tried = 0
    while pairs and len(chosen) < max_n and tried < 400:
        tried += 1
        # variety: a card's later picks rank lower
        best_k = max(range(len(pairs)), key=lambda k: (
            pairs[k]["score"] * REPEAT_DECAY ** len(
                [c for c in chosen if c["device"] == pairs[k]["card"]["id"]]),
            -pairs[k]["moment"]["t"]))
        p = pairs.pop(best_k)
        card, m = p["card"], p["moment"]
        cid = card["id"]
        fam = card.get("family")
        budget = (card.get("budget") or {}).get(intensity) or {}
        times = per_card.get(cid, [])
        if budget.get("per_short") is not None and \
                len(times) + 1 > budget["per_short"]:
            continue
        if budget.get("min_gap_s") is not None and any(
                abs(m["t"] - t) < float(budget["min_gap_s"]) for t in times):
            continue
        if budget.get("per_s") is not None and len(times) + 1 > max(
                1, int(float(budget["per_s"]) * plan["program_s"])):
            continue
        if budget.get("per_section") is not None:
            in_sec = [t for t in times
                      if _section_at(plan["sections"], t) == m["section"]]
            if len(in_sec) + 1 > budget["per_section"]:
                continue
        lfam = _family_ledger(card)
        cap = fam_budget.get(lfam)
        if cap is not None and fam_count.get(lfam, 0) + 1 > cap:
            continue
        if fam in PUNCTUATION:
            have = sec_punct.get(m["section"])
            if have is not None and have != cid:
                continue           # one punctuation family per section
        if fam == "transition" and len(
                [c for c in chosen if c["family"] == "transition"]) + len(
                [e for e in existing if e["family"] == "transition"
                 or e.get("type") == "junction"]) >= \
                TRANSITION_CAP.get(intensity, 2):
            continue               # a wipe on every cut reads as a template
        shots_used = shot_cards.get(cid, set())
        if (m["shot"] - 1) in shots_used or (m["shot"] + 1) in shots_used:
            continue               # no device on consecutive shots
        if any(e["t"] is not None and e.get("hit")
               and abs(e["t"] - m["t"]) < DEVICE_SPACING_S[intensity]
               for e in existing) and _point_card(card):
            continue               # that moment already has its hit
        near = [c for c in chosen if abs(c["moment"]["t"] - m["t"])
                < DEVICE_SPACING_S[intensity]]
        if near:
            # one device per moment: a card that fits the same moment is an
            # alternative to the stronger one already there
            host = near[0]
            fits_budget = (budget.get("per_short") is None or
                           len(times) + 1 <= budget["per_short"]) and (
                cap is None or lfam == _family_ledger(host["card"]) or
                fam_count.get(lfam, 0) + 1 <= cap)
            if abs(host["moment"]["t"] - m["t"]) < 0.05 and fits_budget and \
                    cid not in [a["device"] for a in host["alternatives"]] \
                    and cid != host["device"] and \
                    len(host["alternatives"]) < 3:
                aid = _unique_id(cid, frame_of(m["t"], fps), used_ids)
                aop, _ai, _at = build_op(card, m, plan, item_id=aid, fps=fps,
                                         edl=edl)
                if aop is not None:
                    try:
                        if validate:
                            _dry_run(edl, [c for c in chosen
                                           if c is not host], aop, duration,
                                     media_keys)
                        host["alternatives"].append({"device": cid,
                                                     "op": aop})
                        used_ids.add(aid)
                    except (ValueError, TypeError):
                        pass
            continue
        if payoff and fam in HOLD_FREE and \
                payoff["t"] + 0.5 / fps < m["t"] < payoff["hold_end"]:
            continue               # none inside the payoff hold
        fid = _unique_id(cid, frame_of(m["t"], fps), used_ids)
        op, item, at = build_op(card, m, plan, item_id=fid, fps=fps, edl=edl)
        if op is None:
            continue
        if _overlaps_same(item, cid, chosen, existing):
            continue               # one window of a device at a time
        cand = {"device": cid, "family": fam, "verb": card.get("verb"),
                "op": op, "at": at, "bind": m["bind"],
                "candidate_id": fid, "score": p["score"],
                "flash_t": _flash_times(item) if _flash(card) else []}
        if cand["flash_t"]:
            _kept, thinned = _flash_events(edl, chosen, cand, fps, opted_in)
            if any(t.get("id") == fid for t in thinned):
                continue
        if validate:
            try:
                _dry_run(edl, chosen, op, duration, media_keys)
            except (ValueError, TypeError) as e:
                if cid not in {r[0] for r in rejected}:
                    # once per card: a card whose params the write path
                    # refuses is a card bug, visible in the worker log
                    print(f"[planner] {cid} candidate refused by "
                          f"apply_batch: {str(e)[:160]}", flush=True)
                rejected.append((cid, str(e)[:160]))
                continue
        cand.update({"moment": m, "notes": p["notes"], "rung": p["rung"],
                     "card": card, "alternatives": []})
        chosen.append(cand)
        used_ids.add(fid)
        per_card.setdefault(cid, []).append(m["t"])
        if lfam is not None:
            fam_count[lfam] = fam_count.get(lfam, 0) + 1
        if fam in PUNCTUATION:
            sec_punct.setdefault(m["section"], cid)
        shot_cards.setdefault(cid, set()).add(m["shot"])
    chosen.sort(key=lambda c: -c["score"])
    out = []
    for rank, c in enumerate(chosen, start=1):
        card, m = c["card"], c["moment"]
        frames = _op_frames(c["op"], fps)
        try:
            est = devices.estimate_ms(card, lane, frames,
                                      c["rung"]["rung"]
                                      if c["rung"]["rung"] != "omit"
                                      else "full")
        except Exception:  # noqa: BLE001
            est = None
        out.append({
            "device": c["device"], "family": c["family"], "verb": c["verb"],
            "op": c["op"], "at": c["at"], "bind": c["bind"],
            "reason": _reason(card, m, plan, c["notes"]),
            "exemplar": (card.get("exemplars") or [None])[0],
            "est_ms": est,
            "owner_flags": dict(card.get("owner_flags") or {}),
            "rank": rank,
            "detail": {"score": round(c["score"], 3), "moment": m["kind"],
                       "strength": m["strength"], "word": m.get("word"),
                       "rung": c["rung"]["rung"],
                       "substitute": c["rung"].get("substitute"),
                       "notes": c["notes"],
                       "alternatives": c["alternatives"]}})
    return out


def _dry_run(edl, chosen, op, duration, media_keys):
    """apply_batch the chosen ops plus this one on a copy: a candidate the
    write path would refuse is never proposed."""
    import edit_batch
    ops = [c["op"] for c in chosen] + [op]
    allowed = set(media_keys or ()) | set(edit_batch.media_keys(edl))
    edit_batch.apply_batch(edl, ops, duration, allowed)


def _op_frames(op, fps):
    v = op.get("value") or {}
    if v.get("start") is not None and v.get("end") is not None:
        return max(1, frame_of(v["end"], fps) - frame_of(v["start"], fps))
    if v.get("at") is not None:
        return max(2, int(round(float(v.get("duration_s") or 0.4) * fps)))
    return 1


def _unique_id(card_id, frame, used):
    base = f"{card_id}_f{frame}"[:76]
    fid, n = base, 1
    while fid in used:
        n += 1
        fid = f"{base}_{n}"[:80]
    return fid


def _reason(card, m, plan, notes):
    what = (card.get("description") or card["id"]).rstrip(".")
    kind = {"word.payoff": "the payoff", "stress": "a stressed word",
            "word": "a loaded word", "phrase_end": "a phrase end",
            "cut": "a cut", "section": "a section change",
            "gap": "a pause", "downbeat": "a downbeat",
            "beat": "a beat", "drop": "the drop"}.get(
                m["kind"], m["kind"].replace("onset.", "a ") + " onset")
    word = f" '{m['word_raw'] if m.get('word_raw') else m['word']}'" \
        if m.get("word") else ""
    tags = [t for t in m.get("tags") or () if t in RHETORIC_TAGS
            and t != "payload"]
    why = f" ({', '.join(tags)})" if tags else ""
    text = f"{what} — on {kind}{word} at {m['t']:.2f}s{why}"
    if notes:
        text += "; " + "; ".join(notes)
    return text[:300]


# ── ranking what is already in the edit (set_intensity) ─────────────────

def rank_items(edl, index, *, cards=None, maps=None, rhetoric=None,
               plan=None):
    """{fx id: score 0..1}: how strongly each device item in the EDL is
    earned — the strength of the moment it is bound to (or the nearest
    moment within two frames when it carries no bind)."""
    import devices
    cards = cards if cards is not None else devices.load_cards()
    plan = plan or moments(edl, index, rhetoric=rhetoric, maps=maps)
    fps = plan["fps"]
    by_bind = {}
    for m in plan["moments"]:
        key = json.dumps(m["bind"], sort_keys=True)
        by_bind[key] = max(by_bind.get(key, 0.0), m["strength"])
    out = {}
    for it in edl.get("fx") or []:
        if not isinstance(it, dict):
            continue
        t = it.get("start") if it.get("start") is not None else it.get("at")
        score = None
        b = it.get("bind")
        if isinstance(b, dict):
            key = json.dumps({k: v for k, v in b.items()
                              if k in ("on", "ref", "rhythm")
                              and v is not None}, sort_keys=True)
            for mkey, s in by_bind.items():
                mb = json.loads(mkey)
                if all(mb.get(k) == v for k, v in json.loads(key).items()):
                    score = max(score or 0.0, s)
        if score is None and t is not None:
            near = [m["strength"] for m in plan["moments"]
                    if abs(m["t"] - float(t)) <= 2.0 / fps]
            score = max(near) if near else 0.1
        out[str(it.get("id"))] = round(float(score or 0.1), 3)
    return out


def trim_plan(edl, index, level, *, cards=None, maps=None, rhetoric=None,
              request_text=None, opted_in=None):
    """The device items to REMOVE so the edit fits ``level`` (calm,
    designed, charged): over a card's budget (per_short, then min_gap_s),
    over a ledger family budget, or past flash safety. Lowest-ranked go
    first. [{id, layer, device, family, score, reason}] in removal order."""
    import devices
    import flash_safety
    import taste
    if level not in INTENSITIES:
        raise ValueError(f"intensity is one of {INTENSITIES}")
    cards = cards if cards is not None else devices.load_cards()
    plan = moments(edl, index, rhetoric=rhetoric, maps=maps)
    scores = rank_items(edl, index, cards=cards, plan=plan)
    items = [i for i in edl.get("fx") or [] if isinstance(i, dict)]
    gone, why = [], {}

    def drop(it, reason):
        if it["id"] not in why:
            why[it["id"]] = reason
            gone.append(it)

    def alive():
        return [i for i in items if i["id"] not in why]
    # 1. per-card budgets
    by_card = {}
    for it in items:
        if it.get("device") in cards:
            by_card.setdefault(it["device"], []).append(it)
    for cid, its in sorted(by_card.items()):
        budget = (cards[cid].get("budget") or {}).get(level) or {}
        ranked = sorted(its, key=lambda i: (scores.get(i["id"], 0.1),
                                            -_t(i)))
        cap = budget.get("per_short")
        if cap is not None and len(ranked) > cap:
            for it in ranked[:len(ranked) - int(cap)]:
                drop(it, f"{cid}: over its {level} budget of {int(cap)} "
                         "per short")
        gap = budget.get("min_gap_s")
        if gap:
            keep = []
            for it in sorted([i for i in its if i["id"] not in why],
                             key=lambda i: -scores.get(i["id"], 0.1)):
                if any(abs(_t(it) - _t(k)) < float(gap) for k in keep):
                    drop(it, f"{cid}: closer than {gap:g} s to a stronger "
                             f"one at {level}")
                else:
                    keep.append(it)
    # 2. ledger family budgets
    try:
        tmp = dict(edl, fx=alive())
        led = taste.ledger(tmp, intensity=level, index=index)
        for fam, cap in led.budgets.items():
            if cap is None or fam in taste.OPTIONAL_FAMILIES:
                continue
            fam_items = [i for i in alive()
                         if _ledger_family(i, cards) == fam]
            over = len(fam_items) - int(cap)
            if over > 0:
                for it in sorted(fam_items, key=lambda i: (
                        scores.get(i["id"], 0.1), -_t(i)))[:over]:
                    drop(it, f"{fam}: over the {level} ledger budget of "
                             f"{int(cap)}")
    except Exception as e:  # noqa: BLE001
        print(f"[planner] ledger trim skipped: {e}", flush=True)
    # 3. flash safety (a calmer level never keeps an earlier opt-in)
    optin = bool(opted_in) if opted_in is not None else \
        flash_safety.opted_in(request_text)
    if level != "charged" or not optin:
        # the edit's own flashes outside the fx layer (flash transitions,
        # flash stylize, flash templates) count toward every second but are
        # not this dial's to remove: the device flashes thin around them
        events = [{"t": e["t"], "strength": 9.0, "id": None}
                  for e in flash_safety.flash_events(dict(edl, fx=[]),
                                                     index)]
        for it in alive():
            card = cards.get(it.get("device")) or {}
            flag = _flash(card) if card else it.get("type") in (
                "accent", "strobe")
            if not flag:
                continue
            for t in _flash_times(it):
                events.append({"t": t, "strength": scores.get(it["id"], 0.1),
                               "id": it["id"]})
        _kept, thinned = flash_safety.plan(events, plan["fps"],
                                           opted_in=False)
        for e in thinned:
            if e.get("id") is None:
                continue
            it = next((i for i in items if i["id"] == e["id"]), None)
            if it is not None:
                drop(it, "flash safety: more than "
                         f"{flash_safety.default_max_per_s()} full-frame "
                         "flashes in one second")
    return [{"id": it["id"], "layer": "fx", "device": it.get("device"),
             "family": (cards.get(it.get("device")) or {}).get("family"),
             "score": scores.get(it["id"], 0.1), "reason": why[it["id"]]}
            for it in sorted(gone, key=lambda i: scores.get(i["id"], 0.1))]


def _t(it):
    t = it.get("start") if it.get("start") is not None else it.get("at")
    return float(t) if t is not None else 0.0


def _ledger_family(it, cards):
    import taste
    card = cards.get(it.get("device")) or {}
    return (taste._CARD_LEDGER.get(card.get("family"))
            or taste._FX_TYPE_LEDGER.get(it.get("type")))


# ── text for the agent ───────────────────────────────────────────────────

def format_candidates(cands, *, intensity=DEFAULT_INTENSITY, ledger=None,
                      empty_reason=None):
    """The verb's reply: one line per candidate (rank, time, device, why,
    cost) and the exact ops in one JSON block, ready for ONE
    apply_edit_batch."""
    if not cands:
        return ("NO DEVICE EARNS A PLACE here at intensity "
                f"'{intensity}'" + (f": {empty_reason}" if empty_reason
                                    else "") + ". A clean cut with "
                "captions is a fine answer; zero devices is allowed.")
    lines = [f"{len(cands)} device candidate(s) at intensity '{intensity}' "
             "(ranked; none written — pick the ones that serve the story "
             "and write them in ONE apply_edit_batch with the ops below):"]
    for c in cands:
        est = (f", ≈{c['est_ms'] / 1000:.1f} s render"
               if c.get("est_ms") else "")
        alts = [a["device"] for a in (c.get("detail") or {}).get(
            "alternatives") or []]
        lines.append(f"{c['rank']}. {c['at']:.2f}s {c['device']} "
                     f"[{c['family']}] — {c['reason']} (like "
                     f"{c['exemplar']}{est})"
                     + (f" — or instead: {', '.join(alts)}" if alts else ""))
    if ledger:
        lines.append(f"Ledger now: {ledger}")
    lines.append("OPS: " + json.dumps([c["op"] for c in cands],
                                      separators=(",", ":")))
    alt_ops = {a["device"]: a["op"] for c in cands
               for a in (c.get("detail") or {}).get("alternatives") or []}
    if alt_ops:
        lines.append("ALTERNATIVE OPS (swap one in for the device on the "
                     "same moment, never both): "
                     + json.dumps(alt_ops, separators=(",", ":")))
    lines.append("Then proof_device(ids=[...]) shows each landing frame "
                 "by frame; remove anything that does not land.")
    return "\n".join(lines)
