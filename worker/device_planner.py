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
PLANNER_KEYS = ("frames", "duration_s", "span", "offset_f")
SPANS = ("frames", "phrase", "sentence", "section", "shot", "payoff_hold")
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
    for b in blocks[1:]:
        out.append({"t": float(b["out_start"]),
                    "ref": (float(b["src_start"]) if b["kind"] == "footage"
                            else None),
                    "kind": "join", "shot_change": True})
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
                    "phrase": None, "cut_kind": c["kind"]})
    for k, (a, _b) in enumerate(sections[1:], start=1):
        out.append({"t": a, "kind": "section", "strength": 0.45,
                    "bind": {"on": "time", "ref": round(a, 2)},
                    "tags": [], "word": None, "phrase": None,
                    "section_i": k})
    for mid, rmap in sorted((maps or {}).items()):
        out += _music_moments(mid, rmap)
    out = [m for m in out if 0.0 <= m["t"] < program_s - 0.5 / fps]
    payoff = _payoff(out, words, program_s, fps)
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


def _payoff(ms, words, program_s, fps):
    """The payoff: the payload word where the story lands (strength, how
    late it is, a figure, the far side of a contrast, the last sentence);
    its hold runs PAYOFF_HOLD_S past the word's phrase end."""
    pool = [m for m in ms if m["kind"] in ("stress", "word")
            and "payload" in (m.get("tags") or [])]
    if not pool:
        pool = [m for m in ms if m["kind"] == "stress"]
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
    if place.get("duration_s"):
        end = start + float(place["duration_s"])
    elif span == "phrase" and moment.get("phrase"):
        end = max(moment["phrase"][1], start + 0.5)
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
            return None, None, "no room before the program end"
        item["start"], item["end"] = start, end
        return ({"action": "upsert", "layer": "fx", "id": item_id,
                 "value": item}, item, start)
    if stage == "motion" and card.get("verb") == "set_composite":
        # a composite device on the motion graphic that sits on the moment
        hit = None
        for m in edl.get("motion") or []:
            try:
                if float(m["start"]) - 0.2 <= moment["t"] <= float(m["end"]):
                    hit = m
                    break
            except (KeyError, TypeError, ValueError):
                continue
        if hit is None or hit.get("composite"):
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


def _existing(edl, cards):
    """[{id, device, card, family, t, flash, layer}] devices in the EDL."""
    out = []
    for it in edl.get("fx") or []:
        if not isinstance(it, dict):
            continue
        card = cards.get(it.get("device")) or {}
        t = it.get("start") if it.get("start") is not None else it.get("at")
        out.append({"id": it.get("id"), "device": it.get("device"),
                    "card": card, "family": card.get("family"),
                    "type": it.get("type"),
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
        for m in plan["moments"]:
            if not lo - 1e-6 <= m["t"] < hi:
                continue
            fits, fit = _matches(card, m)
            if not fits or m["strength"] < floor or \
                    not format_allows(fmt, card.get("family"), m):
                continue
            score = m["strength"] * fit * pref * rung_factor
            pairs.append({"card": card, "moment": m, "score": score,
                          "rung": rung, "notes": notes})
    pairs.sort(key=lambda p: (-p["score"], p["moment"]["t"],
                              p["card"]["id"]))
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
        shots_used = shot_cards.get(cid, set())
        if (m["shot"] - 1) in shots_used or (m["shot"] + 1) in shots_used:
            continue               # no device on consecutive shots
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
        events = []
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
