"""The advisory "earn its place" review of one short-form EDL.

Independent judges kept finding the same editorial misses in the owner's
showcase shorts, and every one of them is visible in the EDL plus the index:

- HOOK. A generic question ("WHERE DID PROGRESS GO?") instead of the clip's
  strongest line or statistic; a hook that spends the word a later graphic
  slams (GARBAGE in the hook, then the same GARBAGE at 13 s, so the real
  moment lands flat); an opening on the tail of the previous word, on a
  disfluency ("if somebody was like,"), on a jump cut inside the first
  1.5 s, or on a frame where the speaker is turned away.
- GRAPHIC BUDGET. Every graphic must add something the captions cannot say
  (a number, a contrast, an identification, evidence, an image). Seven
  graphics over 64% of a 33 s short, in five type roles and two accent
  colours, dilute the three that mattered; a stack that re-typesets the
  transcript (a spoken list as rows of text, a typewriter of the words just
  heard) adds nothing.
- BEAT COVERAGE (the budget's complement). Dropping the graphics that only
  re-typeset the captions left dead stretches exactly where the argument
  lives: Thiel 14.5-25.0 s over "a narrow cone of progress around the world
  of bits… computers, internet, mobile", Jobs 22.5-37.2 s over "injecting
  some liberal arts into these computers. Let's get… Let's get… Let's
  get…". Every thesis line, spoken list or triad, named product or place
  and number gets a designed beat that ADDS information (a contrast, an
  accumulating list, an identification, a number, evidence, an image):
  about one hero beat per 6-8 s in the body, never more (the budget) and
  never a body stretch past ~8 s — ~6 s when it holds a list, triad, name
  or number — with none. A zoom or a sound is not a beat.
- SHOWABLE MOMENTS. A concrete-noun list, a named product or place and a
  statistic are candidates for real imagery (research_broll / search_stock
  where listed, 0.3-0.6 s per item on its onset); re-typesetting the heard
  words is the logged fallback. A spoken list set as four or more slams
  replacing each other in one slot reads mechanical (one accumulating
  list_build instead), and a list item shows its noun ('UNDERWATER
  CITIES', never a lone 'UNDERWATER').
- HOOK TIER. The hook title is a headline: its main line at least ~7% of
  the frame height (word_slam tier='hook', which also owns its zone: the
  captions wait until it exits) or a headline band — never type no bigger
  than the captions, and never a live caption stacked under it (Thiel's
  3%-high hook caps with '1960s technology' fading in beneath them).
- PAYOFF. The punchline needs air before the end card (0.8-1.5 s after the
  last word: the source's own tail, else a held frame over room tone —
  add_freeze_frame audio_mode='hold'; a big payoff number ~2 s on screen)
  and its number locked up with its noun ('140 / CHARACTERS').
- IDENTIFY. 'Lisa:' in script reads as a dialogue label, not Apple's 1983
  computer; a broadcast lower third over a famous face is a second text
  system where the hook kicker or the headline band already names them.
- TRIM. Sentences that collide across a cut (no breath at the boundary), an
  article dropped inside a clause.
- RESTRAINT (owner rule). Zooms and sound effects are optional, never rules:
  a barely visible push is neither a move nor a steady frame. Nothing here
  ever asks for a zoom or a sound; its zoom notes only offer removal.
- FRAME. A designed short left full-bleed with no committed grade (a taste
  call, offered with a card or letterbox as the alternative, never a
  default; a plain clip the user only asked to caption or trim is theirs).

Layout bands (the persistent headline, a set_editorial_graphic headline,
any text over 80% of the runtime) are not graphics and never count against
the budget, but their words are the hook's words. The checks never flag the
guidance's own devices: a kicker naming the speaker and year, an image's
identifying caption, an accumulating list_build of a spoken list (or up to
three big words for its items), a stutter cut, a requested CTA after the
payoff.

Every note is ADVISORY ("keep if intentional"): ``review`` returns notes with
a concrete fix each and never blocks a render, completion or export.
quality_verifier folds them into one advisory finding, so they reach the
editor exactly like the other verification advisories (the render result's
VERIFICATION ADVISORIES and the finishing preflight).

Pure functions over the EDL dict and the index dict; no I/O, no network.
``python edit_review.py <spec.json>`` prints the notes for a stored spec
({"edl": {...}, "index": <dict or path>}).
"""

from __future__ import annotations

import math
import re

from timeline import Timeline, Voice


REVIEW_VERSION = 1

# Vertical/square programs up to this length are short-form (taste.REEL_MAX_S).
SHORT_FORM_MAX_S = 120.0
SHORT_FORM_MIN_S = 5.0

# ── hook ──────────────────────────────────────────────────────────────────
HOOK_WINDOW_S = 1.5          # hook text by 1.5 s; no jump cut inside it
HOOK_GRAPHIC_START_S = 1.5   # a graphic starting by then IS the hook
HOOK_CUTS_WINDOW_S = 3.0     # at most one cut in the first 3 s
HOOK_FACE_LOOK_S = 1.0       # the opening frames the face check reads
FRAGMENT_MIN_S = 0.03        # a kept word tail shorter than this is a graze

# ── graphic budget ────────────────────────────────────────────────────────
GRAPHIC_EVERY_S = 6.0        # at most one designed graphic per ~6-8 s
SERIES_GAP_S = 1.5           # same-template graphics this close are one run
                             # (a stat run, one big word per spoken list
                             # item: items are often said 1-1.5 s apart);
                             # the hook is never part of a run
MAX_COVERAGE = 0.50          # at most ~50% of the runtime under graphics
MAX_TYPE_ROLES = 3
MAX_ACCENTS = 1
RESTATE_MIN_CONTENT = 3      # content words a graphic needs to "restate"
RESTATE_SHARE = 0.85         # share of them heard around its window
RESTATE_PAD_S = 2.0
# Oct 2026 (Diamandis run): a short starred lockup of heard words still
# restates them. The s09 editor called its three Headline Pro band lockups
# 'the Look's own'; the reviewer answered "EARN ITS PLACE is right", and the
# s07/s08 reviews killed on the same re-typeset (test_p2_template_tool_defects).

# ── beat coverage (the budget's complement) ──────────────────────────────
DEAD_GAP_S = 8.0             # a body stretch this long with no designed beat
ARGUMENT_GAP_S = 6.0         # ... or this long when it holds a list, triad,
                             # name or number (spoken_beats)
LAYOUT_BEAT_S = 2.0          # a picture card's entrance reads as a beat this long
GAP_NOTE_MAX = 3
SLAM_RUN_MAX = 3             # identical slams replacing each other in one slot
# The accumulating list: the typographic device for a spoken list or triad
# (items stack on their onsets as noun phrases). Real imagery comes first.
LIST_TEMPLATES = frozenset(("list_build",))
IMAGE_TEMPLATES = frozenset(("image_card", "photo_stack"))

# ── hook tier ─────────────────────────────────────────────────────────────
HOOK_MIN_FS = 0.07           # the hook's main line, share of the frame height
HOOK_FS_SLACK = 0.004        # the estimate's error
# word_slam's em per character (caps, tracking included) by role, measured on
# the bundled fonts; a mixed-case serif/script line runs a little narrower
SLAM_EM = {"grotesk": 0.64, "condensed": 0.46, "serif": 0.47, "script": 0.64}
SLAM_LH = {"grotesk": 0.88, "condensed": 0.9, "serif": 0.92, "script": 1.05}
SLAM_TWO_LINES_H = 0.32      # word_slam's height budget for 2+ / 1 lines
SLAM_ONE_LINE_H = 0.25

# ── payoff and ending ─────────────────────────────────────────────────────
# Judges (round 7): Thiel's punchline got 0.5 s and Elon's laugh 0.55 s;
# never cut to the end card less than 0.8 s after the last payoff word.
PAYOFF_HOLD_MIN_S = 0.8
PAYOFF_HOLD_MAX_S = 1.5
PAYOFF_HOLD_TARGET_S = 1.0    # what a fix aims for
PAYOFF_NUMBER_HOLD_S = 2.0   # a big payoff number on screen before the end card
DEAD_TAIL_S = 3.0
REACTION_MIN_S = 1.0
PAYOFF_ZONE = 0.2            # the payoff graphic reaches into the last 20%

# ── trim rhythm ───────────────────────────────────────────────────────────
SENTENCE_PAUSE_MIN_S = 0.15

# ── restraint ─────────────────────────────────────────────────────────────
ZOOM_VISIBLE_STRENGTH = 0.10  # a slow push below 1.10x barely reads
ZOOM_SLOW_MIN_S = 1.5
FOLLOW_MOVE_MIN = 0.02        # a follow pan that moves the crop this much

# Templates whose job is information the captions cannot carry (a number, a
# contrast, an identification, evidence, a UI artefact): never "restating".
INFO_TEMPLATES = frozenset((
    "counter", "stat_card", "bar_compare", "line_chart", "progress_ring",
    "timeline_steps", "versus_split", "image_card", "photo_stack",
    "notification", "chat_bubbles", "search_bar", "post_card",
    "arrow_callout", "circle_highlight", "focus_spotlight", "checklist",
    "lower_third", "chapter_title", "emoji_pop", "comment_cta",
    "follow_cta", "save_cta")) | LIST_TEMPLATES
# Lines that annotate a graphic rather than state its claim (a kicker
# naming the speaker and year, an image's identifying caption or chip).
SIDE_KEYS = frozenset(("kicker", "label", "role", "sub", "left_sub",
                       "right_sub", "name", "handle", "attribution",
                       "caption", "chip", "eyebrow", "lead"))
# Disfluencies that must not open a short.
FILLERS = frozenset(("um", "uh", "uhm", "umm", "er", "erm", "ah", "hmm",
                     "mm", "mhm"))
# Words a cut may not silently drop inside a clause.
CLAUSE_WORDS = frozenset((
    "a", "an", "the", "of", "to", "in", "on", "at", "for", "with", "from",
    "by", "is", "are", "was", "were", "has", "have", "had", "be"))
WH_WORDS = frozenset(("where", "what", "why", "how", "who", "when", "which"))
VERTICAL_RATIOS = frozenset(("9:16", "4:5", "1:1", "3:4", "2:3"))

_STAR_RE = re.compile(r"\*([^*]+)\*")
_SENTENCE_END = re.compile(r"[.?!…]['\"”’)]*$")
_CLAUSE_END = re.compile(r"[.?!…,;:]['\"”’)]*$")
# One capitalised word alone on a line, ending in a colon ('Lisa:'): it reads
# as a speaker label. A full name ('Steve Jobs:') IS a speaker label.
_LABEL_LINE = re.compile(r"^\s*\*?([A-Z][\w'’.\-]*)\*?\s*:\s*$")
# Editorial label words that legitimately end in a colon ('Lesson: ship
# faster', 'Myth:', 'Step:'): a device, not a mislabelled name.
LABEL_WORDS = frozenset((
    "note", "tip", "tips", "rule", "rules", "lesson", "lessons", "fact",
    "facts", "step", "myth", "truth", "reality", "answer", "question",
    "problem", "solution", "result", "results", "before", "after", "today",
    "then", "now", "why", "how", "what", "spoiler", "warning", "hint", "fix",
    "goal", "plan", "bonus", "update", "breaking", "q", "a", "pro", "pros",
    "con", "cons", "yes", "no", "example", "quote", "source", "context",
    "verdict", "takeaway", "summary", "tldr", "next", "first", "second",
    "third", "last", "finally", "reminder", "key", "secret", "mistake",
    "hack", "idea", "point", "claim", "reason", "proof", "data",
    "study", "stat", "stats", "translation", "meanwhile", "plot", "twist"))
# A speaker label in front of a headline or hook ('Peter Thiel: …'): the
# name is attribution, not part of the claim the hook makes.
_SPEAKER_PREFIX = re.compile(
    r"^\s*(?:[A-Z][\w'’.\-]*\s+){0,3}[A-Z][\w'’.\-]*\s*:\s*(?:/\s*)?(?=\S)")
_EDITORIAL_ID = re.compile(r"^(eg_.+?__\d+)_\d+$")

# Importance: rank 1 are the judges' major misses, 3 the finishing notes.
# Within a rank, ORDER decides which notes lead the one-line summary.
RANKS = {
    "hook_generic_question": 1, "hook_spends_hero_word": 1,
    "transcript_list": 1, "graphic_budget": 1, "dead_stretch": 1,
    "hook_opens_on_fragment": 2, "hook_jump_cut": 2, "hook_small": 2,
    "hook_shares_zone": 2,
    "payoff_hold": 2, "payoff_number_hold": 2,
    "payoff_number_without_noun": 2, "restates_captions": 2,
    "showable_moment": 2, "list_as_slams": 2, "list_item_without_noun": 2,
    "number_not_as_said": 2,
    "label_reads_as_dialogue": 2, "lower_third_redundant": 2,
    "hook_no_face": 2, "repeated_hero_word": 2, "type_roles": 2,
    "accent_colours": 2, "frame_uncommitted": 2,
    "sentence_collision": 3, "clause_word_dropped": 3,
    "reaction_button_short": 3, "zoom_barely_visible": 3,
    "zoom_with_follow_pan": 3,
}
ORDER = {code: k for k, code in enumerate(RANKS)}


# ── small helpers ─────────────────────────────────────────────────────────

def _f(value, default=0.0):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _cc():
    import caption_carry
    return caption_carry


def _spec(template):
    if not template or template == "html":
        return {}
    try:
        import motion_templates
        return motion_templates.spec(template) or {}
    except Exception:  # noqa: BLE001 — an unknown template is just unknown
        return {}


def _plain(text):
    return re.sub(r"\s+", " ", _STAR_RE.sub(r"\1", str(text or ""))).strip()


def _quote(text, n=48):
    text = _plain(text).replace("\n", " / ")
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def _note(code, at, message, fix, evidence=None, rank=None):
    return {"code": code, "rank": int(rank or RANKS.get(code, 3)),
            "at": None if at is None else round(float(at), 2),
            "message": message, "fix": fix, "evidence": evidence or {}}


def _duration(edl):
    try:
        from schemas import program_duration
        return float(program_duration(edl) or 0.0)
    except Exception:  # noqa: BLE001
        keep = edl.get("keep") or []
        return sum(max(0.0, _f(b) - _f(a)) for a, b in keep)


def short_form(edl, index=None, duration=None):
    """Is this a vertical/square program of SHORT_FORM_MAX_S or less?"""
    dur = _duration(edl) if duration is None else float(duration)
    if not SHORT_FORM_MIN_S <= dur <= SHORT_FORM_MAX_S:
        return False
    frame = edl.get("frame") if isinstance(edl.get("frame"), dict) else {}
    ratio = str((frame or {}).get("ratio") or "source")
    if ratio in VERTICAL_RATIOS:
        return True
    canvas = edl.get("canvas") if isinstance(edl.get("canvas"), dict) else {}
    cw, ch = _f((canvas or {}).get("width")), _f((canvas or {}).get("height"))
    if cw > 0 and ch > 0:
        return ch >= cw
    if ratio == "source":
        video = (index or {}).get("video") or {}
        w, h = _f(video.get("width")), _f(video.get("height"))
        return w > 0 and h >= w
    return False


# ── the designed moments ──────────────────────────────────────────────────

def _motion_lines(item):
    try:
        return list(_cc().graphic_lines(item))
    except Exception:  # noqa: BLE001
        lines = []
        for key, val in (item.get("params") or {}).items():
            if isinstance(val, str) and key not in ("color", "accent"):
                lines.append((key, val))
        return lines


def _layers(edl, duration=None):
    """(moments, bands) of the program's designed text and graphics.

    Moments are the designed graphic moments, in start order: [{"id",
    "kind", "template", "category", "start", "end", "lines", "item"}].
    Bands are the layout layers that carry text for (nearly) the whole
    program — the persistent headline template, a set_editorial_graphic
    headline, any text layer over 80% of the runtime — as [{"id", "start",
    "end", "lines"}]: layout, not graphics, so they are never budgeted, but
    their words still count for the hook. Caption tracks, transition and
    texture layers are neither."""
    dur = _duration(edl) if duration is None else float(duration)
    out, bands = [], []
    for item in edl.get("motion") or []:
        if not isinstance(item, dict):
            continue
        tpl = str(item.get("template") or "")
        spec = _spec(tpl)
        category = str(spec.get("category") or ("custom" if tpl == "html" else ""))
        if tpl.startswith("caption") or category in ("caption", "transition"):
            continue
        start = _f(item.get("start"))
        end = _f(item.get("end"), dur) if item.get("end") is not None else dur
        if spec.get("persistent") or (
                dur > 0 and end - start >= 0.8 * dur and start < dur - 0.05):
            lines = _motion_lines(item)
            if lines:
                bands.append({"id": str(item.get("id") or tpl), "start": start,
                              "end": min(end, dur) if dur else end,
                              "lines": lines})
            continue
        if dur > 0 and start >= dur - 0.05:
            continue
        out.append({"id": str(item.get("id") or tpl), "kind": "motion",
                    "template": tpl, "category": category,
                    "start": start, "end": min(end, dur) if dur else end,
                    "lines": _motion_lines(item), "item": item})
    groups, order = {}, []
    for item in edl.get("texts") or []:
        if not isinstance(item, dict) or not str(item.get("text") or "").strip():
            continue
        start, end = _f(item.get("start")), _f(item.get("end"))
        key = (round(start, 2), round(end, 2))
        text = str(item["text"])
        # set_editorial_graphic writes one text layer per WORD
        # ('eg_<id>__<row>_<word>'): a row reads as one line
        row = _EDITORIAL_ID.match(str(item.get("id") or ""))
        row = row.group(1) if row else None
        group = groups.get(key)
        if group is None:          # a title + subtitle pair is one moment
            tpl = "text:" + str(item.get("template") or "title")
            group = groups[key] = {
                "id": str(item.get("id") or tpl), "kind": "text",
                "template": tpl, "category": "type", "start": start,
                "end": end, "lines": [], "item": item, "_row": None}
            order.append(key)
        if row is not None and row == group["_row"] and group["lines"]:
            group["lines"][-1] = ("text", group["lines"][-1][1] + " " + text)
        else:
            group["lines"].append(("text", text))
        group["_row"] = row
    for key in order:
        m = groups[key]
        m.pop("_row", None)
        if dur > 0 and m["end"] - m["start"] >= 0.8 * dur \
                and m["start"] < dur - 0.05:
            bands.append({"id": m["id"], "start": m["start"],
                          "end": min(m["end"], dur), "lines": m["lines"]})
            continue
        if dur > 0 and m["start"] >= dur - 0.05:
            continue
        out.append(m)
    out.sort(key=lambda m: (m["start"], m["end"]))
    bands.sort(key=lambda b: b["start"])
    return out, bands


def moments(edl, duration=None):
    """The program's designed graphic moments, in start order (see
    ``_layers``); headline bands and other whole-program layers are not
    moments."""
    return _layers(edl, duration)[0]


def bands(edl, duration=None):
    """The whole-program text bands (headline layers), see ``_layers``."""
    return _layers(edl, duration)[1]


def headline(edl):
    for item in edl.get("motion") or []:
        if isinstance(item, dict) and _spec(item.get("template")).get("persistent"):
            return item
    return None


def _tokens(text):
    return _cc().tokens(text)


def _content(tokens):
    cc = _cc()
    return [t for t in tokens if cc.is_content(t)]


def shown(lines, value_keys=("value",)):
    """(content tokens in order, hero tokens) of a moment's lines. Hero:
    numbers, *starred* words and a counter's value — or, with nothing
    starred, the one content word of a single-word claim (an unstarred slam).
    Kickers, labels, captions and other side lines are never hero words —
    not even their numbers ('Steve Jobs, 1983' in a kicker and 'Apple Lisa,
    1983' in an image caption identify, they do not slam)."""
    seq, hero, starred_any = [], set(), False
    main = []
    for key, text in lines:
        toks = _tokens(text)
        seq += [t for t in _content(toks)]
        if key in value_keys:
            hero.update(_content(toks))
        if key in SIDE_KEYS:
            continue
        hero.update(t for t in toks if any(c.isdigit() for c in t))
        for starred in _STAR_RE.findall(str(text)):
            starred_any = True
            hero.update(_content(_tokens(starred)))
        main += _content(toks)
    if not starred_any and len(set(main)) == 1:
        hero.update(main)
    return seq, hero


def _claim(text):
    """A hook or headline line without its speaker label ('Peter Thiel: …')."""
    return _SPEAKER_PREFIX.sub("", _plain(text), count=1)


def _main_tokens(lines):
    """Content tokens of a moment's claim lines (no side lines, no speaker
    label): what a hook or headline actually shows the viewer."""
    out = []
    for key, text in lines:
        if key not in SIDE_KEYS:
            out += _content(_tokens(_claim(text)))
    return out


def _main_text(m):
    return " / ".join(text for key, text in m["lines"] if key not in SIDE_KEYS)


# ── program words ─────────────────────────────────────────────────────────

class _Program:
    def __init__(self, edl, index):
        self.keep = [(_f(a), _f(b)) for a, b in (edl.get("keep") or [])]
        self.tl = Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                           edl.get("speed") or [])
        self.duration = _duration(edl)
        lo = min((a for a, _b in self.keep), default=0.0) - 2.0
        hi = max((b for _a, b in self.keep), default=0.0) + 2.0
        # Only the words around the kept spans matter: a long source's
        # transcript is otherwise mapped word by word on every review.
        self.words = [w for w in ((index or {}).get("words") or [])
                      if _get(w, "w") is not None
                      and _f(_get(w, "t1")) >= lo and _f(_get(w, "t0")) <= hi]
        self.kept = []
        if self.words and self.keep:
            try:
                voice = Voice.from_index(index)
            except Exception:  # noqa: BLE001
                voice = None
            self.kept = self.tl.kept_words(self.words, rescue=True, voice=voice)

    def joins(self, covered=False):
        """[(i, program_t, src_end, src_start)] for every real cut between
        kept spans (a split at the same source time is no cut). A cut with
        an insert spliced at it (B-roll, a clip) is left out unless
        ``covered``: the viewer never sees the two takes meet."""
        out = []
        pre = 0.0
        ins_at = [at for at, _d in (getattr(self.tl, "ins", None) or [])]
        for i in range(len(self.keep) - 1):
            if i < len(self.tl.seg_out_len):
                pre += self.tl.seg_out_len[i]
            a_end, b_start = self.keep[i][1], self.keep[i + 1][0]
            if abs(b_start - a_end) < 0.02:
                continue
            t = self.tl.offsets[i + 1] if i + 1 < len(self.tl.offsets) else None
            if t is None:
                continue
            if not covered and any(abs(at - pre) < 0.05 for at in ins_at):
                continue
            out.append((i, float(t), a_end, b_start))
        return out

    def words_between(self, lo, hi):
        return [w for w in self.kept if lo - 1e-6 <= _f(w["t0"]) <= hi + 1e-6]


def _shot_at(index, t):
    for shot in (index or {}).get("shots") or []:
        a, b = _f(_get(shot, "start"), None), _f(_get(shot, "end"), None)
        if a is None or b is None:
            continue
        if a - 1e-6 <= t < b + 1e-6:
            return _get(shot, "id", a)
    return None


# ── HOOK ──────────────────────────────────────────────────────────────────

def _hook_moments(ms):
    return [m for m in ms if m["start"] <= HOOK_GRAPHIC_START_S + 1e-6
            and m["template"] not in ("lower_third", "text:lower_third")]


def _hook_bands(bands):
    return [b for b in bands if b["start"] <= HOOK_GRAPHIC_START_S + 1e-6]


def _hook_question_notes(hook_ms, bands):
    for m in list(hook_ms) + _hook_bands(bands):
        # 'Peter Thiel: Where did progress go?' is still a generic question:
        # the speaker label is attribution, not something the question names
        text = _claim(_main_text(m))
        if not text.rstrip(" .…\"'”’").endswith("?"):
            continue
        if any(c.isdigit() for c in text) or _names_something(text):
            continue
        first = (_tokens(text) or [""])[0]
        generic = first in WH_WORDS
        what = ("a generic question" if generic
                else "a question with no number or claim of its own")
        return [_note(
            "hook_generic_question", m["start"],
            (f"The hook '{_quote(text)}' is {what}: it promises nothing "
             "specific and withholds the clip's strongest line."),
            ("Write the hook from the clip's strongest line or statistic — a "
             "specific claim or number the ending pays off, set up without "
             "printing the punchline. Keep a question only when it names "
             "something specific."),
            {"id": m["id"], "text": text}, rank=1 if generic else 2)]
    return []


def _names_something(text):
    """Does a (mixed-case) question name someone or something — a
    capitalised word past the first ('Why did Apple kill the Lisa?')? An
    all-caps line cannot say, so it names nothing here."""
    letters = [c for c in text if c.isalpha()]
    if not letters or all(c.isupper() for c in letters):
        return False
    words = re.findall(r"[A-Za-z][\w'’]*", text)
    return any(w[0].isupper() and w not in ("I", "I'm", "I’m", "I've", "I'd")
               for w in words[1:])


def _hero_repeat_notes(ms, hook_ms, bands):
    notes = []
    hook_ids = {m["id"] for m in hook_ms}
    hook_tokens = {}
    # What the opening SHOWS: the hook graphics' and an opening headline
    # band's claim words. A kicker naming the speaker and year is not a
    # spent word.
    for m in list(hook_ms) + _hook_bands(bands):
        for tok in _main_tokens(m["lines"]):
            hook_tokens.setdefault(tok, m)
    earlier = []                  # (moment, hero tokens) of later graphics
    for band in bands:
        if band["start"] <= HOOK_GRAPHIC_START_S + 1e-6:
            continue              # already read as the hook
        hero = shown(band["lines"])[1]
        if hero:
            earlier.append((dict(band, template="headline"), hero))
    spent, repeated = [], []
    for m in ms:
        if m["id"] in hook_ids or m["template"] in (
                "lower_third", "text:lower_third"):
            continue
        hero = shown(m["lines"])[1]
        hit = sorted(t for t in hero if t in hook_tokens)
        if hit:
            spent.append((m, hit, hook_tokens[hit[0]]))
        for prev, prev_hero in earlier:
            both = sorted(hero & prev_hero)
            if both:
                repeated.append((prev, m, both))
                break
        earlier.append((m, hero))
    if spent:
        m, hit, hook = spent[0]
        word = hit[0].upper()
        notes.append(_note(
            "hook_spends_hero_word", m["start"],
            (f"The hook ({hook['start']:.1f}-{hook['end']:.1f}s) already shows "
             f"'{word}', the hero word of '{m['id']}' at {m['start']:.2f}s — the "
             "hook spends it, so the real moment lands as a repeat."
             + (f" (+{len(spent) - 1} more)" if len(spent) > 1 else "")),
            ("Rewrite the hook as a claim that sets the moment up without its "
             "word, or transform the later instance (a quote card with the "
             "attribution and year, the evidence on screen) instead of the "
             "same slam."),
            {"hook": hook["id"], "graphic": m["id"], "words": hit}))
    if repeated:
        prev, m, both = repeated[0]
        notes.append(_note(
            "repeated_hero_word", m["start"],
            (f"'{both[0].upper()}' is the hero word of '{prev['id']}' "
             f"({prev['start']:.1f}s) and again of '{m['id']}' "
             f"({m['start']:.1f}s): the second one adds nothing."),
            ("Keep one; a deliberate callback must transform the word (struck "
             "through, recoloured, completed by the payoff), never repeat the "
             "same treatment."),
            {"first": prev["id"], "second": m["id"], "words": both}))
    return notes


def _hook_open_notes(edl, index, prog):
    notes = []
    words = prog.words
    if words and prog.keep:
        try:
            import audit
            hit = audit.word_at_boundary(words, prog.keep[0][0])
        except Exception:  # noqa: BLE001
            hit = None
        if hit and _f(hit["t1"]) - prog.keep[0][0] >= FRAGMENT_MIN_S:
            notes.append(_note(
                "hook_opens_on_fragment", 0.0,
                (f"The program opens on the tail of '{hit['w']}' "
                 f"({hit['t0']:.2f}-{hit['t1']:.2f}s source): frame 0 plays "
                 "a word fragment before the first real word."),
                (f"Start the first keep at the next word's onset (about "
                 f"{hit['t1']:.2f}s source) so the short opens on a clean word."),
                {"word": hit["w"], "keep_start": prog.keep[0][0]}))
        if not notes:
            opening = [w for w in prog.kept if _f(w["t0"]) < HOOK_WINDOW_S]
            for k, w in enumerate(opening):
                raw = str(w["w"])
                tok = re.sub(r"[^\w']", "", raw.lower())
                nxt = opening[k + 1] if k + 1 < len(opening) else None
                pair = (tok + " " + re.sub(r"[^\w']", "", str(nxt["w"]).lower())
                        if nxt else "")
                if tok in FILLERS or pair in ("you know", "i mean") or (
                        tok == "like" and raw.rstrip().endswith(",")):
                    said = " ".join(str(x["w"]) for x in opening[:k + 2])
                    notes.append(_note(
                        "hook_opens_on_fragment", _f(w["t0"]),
                        (f"The hook opens on a disfluency ('{_quote(said, 40)}' "
                         f"at {_f(w['t0']):.2f}s)."),
                        ("Start on a clean clause — the first full sentence "
                         "that states the idea — or trim the filler with its "
                         "pause so the first words read as one thought."),
                        {"word": raw}))
                    break
    cuts = []
    for i, t, a_end, b_start in prog.joins():
        if t >= HOOK_CUTS_WINDOW_S:
            break
        a_shot, b_shot = _shot_at(index, a_end - 0.01), _shot_at(index, b_start + 0.01)
        if a_shot is not None and b_shot is not None and a_shot != b_shot:
            continue                     # a camera change, not a jump cut
        cuts.append(round(t, 2))
    early = [t for t in cuts if t < HOOK_WINDOW_S]
    if early or len(cuts) >= 2:
        where = ", ".join(f"{t:.2f}s" for t in cuts)
        notes.append(_note(
            "hook_jump_cut", (early or cuts)[0],
            (f"{len(cuts)} jump cut(s) in the first {HOOK_CUTS_WINDOW_S:.0f} s "
             f"({where})" + (" — one lands inside the hook's first 1.5 s"
                             if early else "")
             + ": the opening stutters before the promise is made."),
            ("Let the hook play as one take: start at a later clean onset, or "
             "restore the pause the cut removed (restore_range). Keep at most "
             "one cut in the first 3 s; a bare jump cut later on is fine."),
            {"cuts": cuts}))
    samples = ((index or {}).get("spatial") or {}).get("samples") or []
    if samples and prog.keep:
        k0 = prog.keep[0][0]
        k0_end = min(prog.keep[0][1], k0 + HOOK_FACE_LOOK_S)
        opening = [s for s in samples if k0 - 1e-6 <= _f(_get(s, "t")) <= k0_end]
        if opening and not any(_get(s, "faces") for s in opening):
            later = []
            for s in samples:
                t = _f(_get(s, "t"))
                if _get(s, "faces") and t > k0_end:
                    out_t = prog.tl.src_to_out(t)
                    if out_t is not None:
                        later.append(out_t)
            if later:
                first = min(later)
                notes.append(_note(
                    "hook_no_face", 0.0,
                    (f"No face is detected on the opening frames (0-"
                     f"{k0_end - k0:.1f}s); the speaker's face is first found "
                     f"at {first:.1f}s — the hook may open in profile or "
                     "turned away."),
                    ("look_at the first frames; start where the speaker faces "
                     "camera (a later clean onset) so frame 0, the thumbnail, "
                     "is a face, not a profile."),
                    {"first_face_s": round(first, 2)}))
    return notes


# ── GRAPHIC BUDGET ────────────────────────────────────────────────────────

def _series(ms, solo=()):
    """Moments with same-template runs (a stat run, one big word per list
    item) merged into one. Moments whose id is in ``solo`` (the hook) stand
    alone: a hook slam followed by a list run is two moments."""
    out = []
    for m in ms:
        if out and out[-1]["template"] == m["template"] and \
                m["id"] not in solo and \
                not any(i in solo for i in out[-1]["ids"]) and \
                m["start"] - out[-1]["end"] <= SERIES_GAP_S:
            out[-1] = dict(out[-1], end=max(out[-1]["end"], m["end"]),
                           ids=out[-1]["ids"] + [m["id"]])
            continue
        out.append(dict(m, ids=[m["id"]]))
    return out


def _coverage(ms, duration):
    spans = sorted((max(0.0, m["start"]), min(duration, m["end"])) for m in ms
                   if m["end"] > m["start"])
    total, cur_a, cur_b = 0.0, None, None
    for a, b in spans:
        if cur_b is None or a > cur_b:
            if cur_b is not None:
                total += cur_b - cur_a
            cur_a, cur_b = a, b
        else:
            cur_b = max(cur_b, b)
    if cur_b is not None:
        total += cur_b - cur_a
    return total


def _payoff_moment(ms, duration):
    ms = [m for m in ms if m.get("category") != "cta"]
    if not ms or duration <= 0:
        return None
    last = max(ms, key=lambda m: (m["start"], m["end"]))
    if last["end"] >= duration * (1.0 - PAYOFF_ZONE) - 1e-6:
        return last
    return None


def _is_list(m, prog):
    """A spoken enumeration set as text: three or more short items, each
    separated by a comma or 'and/or' in its own text or in the speech."""
    texts = [text for key, text in m["lines"] if key not in SIDE_KEYS]
    joined = " / ".join(texts)
    items = [p for p in re.split(r"\s*(?:[,.;]|/|\n)\s*", joined) if _content(_tokens(p))]
    if len(texts) == 1 and len(items) >= 3 and all(
            len(_content(_tokens(p))) <= 3 for p in items):
        return True
    rows = [t for t in texts if _content(_tokens(t))]
    if len(rows) < 3 or any(len(_content(_tokens(r))) > 3 for r in rows):
        return False
    cc = _cc()
    for r in rows:
        toks = _tokens(r)
        if not toks or not cc.is_content(toks[0]) or not cc.is_content(toks[-1]):
            return False
    spoken = prog.words_between(m["start"] - RESTATE_PAD_S, m["end"] + RESTATE_PAD_S)
    if not spoken:
        return False
    norm = [(_tokens(str(w["w"])) or [""])[-1] for w in spoken]
    raw = [str(w["w"]) for w in spoken]
    separated, pos = 0, 0
    for r in rows[:-1]:
        last = _content(_tokens(r))[-1]
        try:
            k = norm.index(last, pos)
        except ValueError:
            continue
        pos = k + 1
        nxt = norm[k + 1] if k + 1 < len(norm) else ""
        if raw[k].rstrip().endswith((",", ";")) or nxt in ("and", "or"):
            separated += 1
    return separated >= max(2, len(rows) - 2)


def _restate_notes(ms, prog, hook_ids, payoff):
    if not prog.kept:
        return [], set()
    restated, lists = [], []
    for m in ms:
        if m["id"] in hook_ids or (payoff is not None and m is payoff):
            continue
        tpl = m["template"]
        if tpl in INFO_TEMPLATES or tpl.startswith("text:lower_third"):
            continue
        seq = shown(m["lines"])[0]
        content = set(seq)
        if len(content) < RESTATE_MIN_CONTENT:
            continue
        if any(any(c.isdigit() for c in t) for t in content):
            continue
        heard = set()
        for w in prog.words_between(m["start"] - RESTATE_PAD_S,
                                    m["end"] + RESTATE_PAD_S):
            heard.update(_tokens(str(w["w"])))
        share = len(content & heard) / float(len(content))
        if share < RESTATE_SHARE:
            continue
        (lists if _is_list(m, prog) else restated).append(m)
    notes = []
    if lists:
        first = lists[0]
        items = "; ".join(f"'{m['id']}' {m['start']:.1f}s ('{_quote(_main_text(m), 40)}')"
                          for m in lists[:3])
        notes.append(_note(
            "transcript_list", first["start"],
            (f"A spoken list is set as a stack of the words being heard — "
             f"{items}. It adds nothing the captions don't, at the most "
             "showable moment of the clip."),
            ("Show the items instead: one semantic visual insert per item "
             "(a real photo or clip via search_stock or research_broll where "
             "listed, placed with image_card, photo_stack or add_overlay, "
             "about 0.3-0.6 s each on its onset); when nothing truly shows "
             "them, one accumulating list_build of noun phrases (the items "
             "stack on their onsets and persist, the newest accented)."),
            {"ids": [m["id"] for m in lists]}))
    if restated:
        first = restated[0]
        items = "; ".join(f"'{m['id']}' {m['start']:.1f}s ('{_quote(_main_text(m), 40)}')"
                          for m in restated[:3])
        notes.append(_note(
            "restates_captions", first["start"],
            (f"{len(restated)} graphic(s) only re-typeset words the viewer is "
             f"already hearing — {items}."),
            ("Drop it and let the captions carry the line, or replace it with "
             "what the captions cannot say: a number, a contrast, an "
             "identification, evidence or an image."),
            {"ids": [m["id"] for m in restated]}))
    return notes, {m["id"] for m in lists + restated}


def _budget_notes(ms, duration, drop_first, hook_ids=()):
    if duration <= 0 or not ms:
        return []
    runs = _series(ms, solo=hook_ids)
    allowed = max(2, int(math.ceil(duration / GRAPHIC_EVERY_S - 1e-9)))
    covered = _coverage(ms, duration)
    share = covered / duration
    over_count = len(runs) > allowed
    over_cover = share > MAX_COVERAGE + 1e-9
    if not (over_count or over_cover):
        return []
    parts = []
    if over_count:
        parts.append(f"{len(runs)} designed graphics in {duration:.1f}s (one "
                     f"every {duration / len(runs):.1f}s; the budget is about one "
                     f"per 6-8 s, {allowed} here)")
    if over_cover:
        parts.append(f"graphics cover {share:.0%} of the runtime "
                     f"({covered:.1f} of {duration:.1f}s; keep it under ~50%)")
    candidates = [m for m in runs if any(i in drop_first for i in m["ids"])]
    hint = (" First candidates to drop: " + ", ".join(
        f"'{m['id']}' {m['start']:.1f}s" for m in candidates[:3])
        + " (they restate the captions).") if candidates else ""
    return [_note(
        "graphic_budget", None,
        "; ".join(parts) + ": the graphics that add meaning get diluted.",
        ("Keep the graphics that add a number, a contrast, an identification, "
         "evidence or an image and let the captions carry the rest." + hint),
        {"graphics": len(runs), "allowed": allowed,
         "coverage": round(share, 3)})]


# ── BEAT COVERAGE (the budget's complement) ───────────────────────────────

def _sb():
    import spoken_beats
    return spoken_beats


def _overlay_spans(edl):
    out = []
    for ov in edl.get("overlays") or []:
        if not isinstance(ov, dict):
            continue
        a = _f(ov.get("start"), None)
        d = _f(ov.get("duration_s"), None)
        if a is None or d is None or d <= 0:
            continue
        out.append({"start": a, "end": a + d, "kind": "image",
                    "id": str(ov.get("id") or "overlay")})
    return out


def _insert_spans(prog):
    """Spliced inserts as real imagery — never a payoff hold
    (timeline.is_hold_insert): that is the last composed frame held over
    room tone, the ending's air, not an image and not a beat."""
    out = []
    try:
        import timeline as _tlm
        items = sorted(getattr(prog.tl, "items", None) or [], key=_tlm._ins_sort_key)
        for k, (a, d) in enumerate(prog.tl.insert_positions()):
            if k < len(items) and _tlm.is_hold_insert(items[k]):
                continue
            if d > 0:
                out.append({"start": float(a), "end": float(a) + float(d),
                            "kind": "image", "id": f"insert{k}"})
    except Exception:  # noqa: BLE001 — a timeline without inserts has none
        pass
    return out


_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".mp4", ".mov", ".webm")


def _html_image(m):
    """An authored html graphic that places a project image or clip (an
    asset_* param): s09's NASA photo of Mars beside the payoff was 'the name
    Mars set as the heard words' (Oct 2026)."""
    if m.get("template") != "html":
        return False
    params = (m.get("item") or {}).get("params") or {}
    return any(str(k).startswith("asset_") and str(v).lower().endswith(_IMAGE_EXT)
               for k, v in params.items())


def _imagery(edl, ms, prog):
    """The program's real-imagery spans: cover or picture-in-picture
    overlays, spliced inserts, the image card templates and authored html
    graphics placing a project image."""
    out = _overlay_spans(edl) + _insert_spans(prog)
    out += [{"start": m["start"], "end": m["end"], "kind": "image", "id": m["id"]}
            for m in ms if m["template"] in IMAGE_TEMPLATES or _html_image(m)]
    return out


def _beat_spans(edl, ms, prog, duration):
    """Every designed beat of the body as [{"start", "end"}]: the designed
    moments (bands are layout, never beats), real imagery, and a picture
    card's entrance (a layout change; a card that holds the whole program is
    the frame, not a beat). Zooms are not beats: a camera move adds no
    information, and nothing here may be satisfied by one."""
    spans = [{"start": m["start"], "end": m["end"]} for m in ms]
    spans += [{"start": s["start"], "end": s["end"]} for s in _imagery(edl, ms, prog)]
    # a renderer-native vector graphic (an arrow, a ring, an underline on
    # the evidence) is a designed beat too
    for v in edl.get("vectors") or []:
        if isinstance(v, dict):
            a, b = _f(v.get("start"), None), _f(v.get("end"), None)
            if a is not None and b is not None and b > a:
                spans.append({"start": a, "end": b})
    fx = edl.get("effects") or {}
    for c in fx.get("picture_cards") or [] if isinstance(fx, dict) else []:
        if not isinstance(c, dict):
            continue
        a, b = _f(c.get("start"), None), _f(c.get("end"), duration)
        if a is None or b <= a:
            continue
        if duration > 0 and b - a >= 0.8 * duration:
            continue
        spans.append({"start": a, "end": min(b, a + LAYOUT_BEAT_S)})
    return sorted(spans, key=lambda s: (s["start"], s["end"]))


def _gaps(spans, lo, hi):
    """Empty stretches of [lo, hi] that no span covers."""
    out, cur = [], lo
    for s in spans:
        if s["end"] <= cur:
            continue
        if s["start"] > cur:
            out.append((cur, min(s["start"], hi)))
        cur = max(cur, s["end"])
        if cur >= hi:
            break
    if cur < hi:
        out.append((cur, hi))
    return [(a, b) for a, b in out if b - a > 1e-6]


STRONG = ("list", "triad", "number", "range", "name")


def _describe(cands, words):
    """What a stretch carries, quoted: its lists, triads, numbers, names,
    then a claim (or its main line)."""
    sb = _sb()
    bits = []
    for c in cands:
        if c["kind"] == "list":
            bits.append(f"the spoken list '{_quote(c['text'], 60)}'")
        elif c["kind"] == "triad":
            bits.append(f"the triad '{_quote(c['text'], 72)}'")
        elif c["kind"] in ("number", "range"):
            bits.append(f"the number '{c['text']}'")
        elif c["kind"] == "name":
            bits.append(f"the name '{c['text']}'")
    claim = next((c for c in cands if c["kind"] == "claim"), None)
    line = claim or sb.main_line(words, skip=[(c["t0"], c["t1"]) for c in cands
                                              if c["kind"] == "triad"])
    if line and not any(c["kind"] in ("list", "triad") and c["t0"] >= line["t0"] - 1e-3
                        and c["t1"] <= line["t1"] + 1e-3 and len(cands) > 1 for c in cands):
        bits.insert(0, f"the line '{_quote(line['text'], 84)}'")
    return bits


def _beat_ideas(cands):
    """A concrete beat per kind of candidate the stretch holds — every one
    adds information; none is a camera move or a sound."""
    out = []
    kinds = {c["kind"] for c in cands}
    for c in cands:
        if c["kind"] == "list" and "list" not in [o[0] for o in out]:
            seen = []
            for i in c.get("items", []):
                if i["text"].upper() not in seen:
                    seen.append(i["text"].upper())
            items = " / ".join(seen[:5])
            out.append(("list", f"an accumulating list_build of its items as noun phrases on "
                                f"their onsets ('{_quote(items, 60)}'), or one real image per "
                                "item (research_broll or search_stock where listed), 0.3-0.6 s "
                                "each on its onset"))
        elif c["kind"] == "triad" and "triad" not in [o[0] for o in out]:
            out.append(("triad", f"an accumulating three-row list_build on '{c['opener']}…' "
                                 "(each row on its onset, the newest accented)"))
        elif c["kind"] in ("number", "range") and "number" not in [o[0] for o in out]:
            span = (f"value '{int(c['values'][0]) if float(c['values'][0]).is_integer() else c['values'][0]}"
                    f"–{int(c['values'][1]) if float(c['values'][1]).is_integer() else c['values'][1]}' "
                    "shows the spoken range") if c["kind"] == "range" else "landing ON the word"
            out.append(("number", f"a counter on '{c['text']}' ({span})"))
        elif c["kind"] == "name" and "name" not in [o[0] for o in out]:
            out.append(("name", f"identify '{c['text']}' (what it is, and a real photo in an "
                                "image_card where one exists)"))
    if "claim" in kinds or not (kinds & set(STRONG)):
        out.append(("claim", "a contrast that gives the line its structure (A vs B, rhyming "
                             "with an earlier device), or the evidence it rests on"))
    return [text for _k, text in out]


def _dead_stretch_notes(edl, ms, prog, duration, hook_ms):
    if duration <= 0 or not prog.kept:
        return []
    hook_end = max([m["end"] for m in hook_ms] + [HOOK_WINDOW_S])
    spans = _beat_spans(edl, ms, prog, duration)
    cands = _sb().candidates(prog.kept)
    found = []
    for a, b in _gaps(spans, hook_end, duration):
        length = b - a
        if length <= ARGUMENT_GAP_S + 1e-6:
            continue
        words = prog.words_between(a, b)
        if not words:
            continue
        inside = [c for c in cands if a - 0.05 <= c["t0"] < b]
        strong = [c for c in inside if c["kind"] in STRONG]
        if length <= DEAD_GAP_S + 1e-6 and not strong:
            continue
        found.append((a, b, inside, strong, words))
    if not found:
        return []
    parts, ideas = [], []
    for a, b, inside, _strong, words in found[:GAP_NOTE_MAX]:
        what = _describe(inside, words)
        parts.append(f"{b - a:.1f} s ({a:.2f}-{b:.2f}s)"
                     + (" over " + " and ".join(what[:3]) if what else ""))
        for idea in _beat_ideas(inside):
            if idea not in ideas:
                ideas.append(idea)
    weighty = any(strong or any(c["kind"] == "claim" for c in inside)
                  for _a, _b, inside, strong, _w in found)
    a0 = found[0][0]
    return [_note(
        "dead_stretch", a0,
        ("The body goes " + "; ".join(parts) + " with no designed beat — the "
         "middle sags exactly where the argument lives."
         + (f" (+{len(found) - GAP_NOTE_MAX} more)" if len(found) > GAP_NOTE_MAX else "")),
        ("Give it a designed beat that adds information: " + "; ".join(ideas[:4])
         + ". About one hero beat every 6-8 s in the body, never more (the "
         "budget) — a zoom or a sound is not a beat, and captions alone carry a "
         "stretch only by choice."),
        {"gaps": [[round(a, 2), round(b, 2)] for a, b, *_r in found]},
        rank=1 if weighty else 2)]


# ── SHOWABLE MOMENTS ──────────────────────────────────────────────────────

def _overlaps(a0, a1, spans, pad=0.3):
    return [s for s in spans if s["start"] < a1 + pad and s["end"] > a0 - pad]


def _typeset_by(c, ms):
    """The type moments that set a candidate's heard words, or []."""
    if c["kind"] in ("list", "triad"):
        toks = set()
        for it in c.get("items") or []:
            toks.update(_content(_tokens(it["text"])))
    else:
        toks = set(_content(_tokens(c["text"])))
    out = []
    for m in _overlaps(c["t0"], c["t1"], ms):
        if m["template"] in IMAGE_TEMPLATES:
            continue
        if toks & set(shown(m["lines"])[0]):
            out.append(m)
    return out


def _showable_notes(edl, ms, prog, dead_gaps=()):
    if not prog.kept:
        return []
    imagery = _imagery(edl, ms, prog)
    typeset, bare = [], []
    cands = _sb().candidates(prog.kept)
    groups = [(c["t0"], c["t1"]) for c in cands if c["kind"] in ("list", "triad")]
    for c in cands:
        if c["kind"] not in ("list", "triad", "name", "number", "range"):
            continue
        if c["kind"] == "name" and any(a - 1e-3 <= c["t0"] <= b + 1e-3 for a, b in groups):
            continue              # a name inside a list is one of its items
        if _overlaps(c["t0"], c["t1"], imagery):
            continue
        by = _typeset_by(c, ms)
        if c["kind"] in ("number", "range"):
            # a number set as a graphic is information already; one with
            # nothing on screen is the dead-stretch note's
            continue
        if by:
            typeset.append((c, by))
        elif not any(a - 0.05 <= c["t0"] < b for a, b in dead_gaps):
            bare.append(c)
    if not (typeset or bare):
        return []
    what = []
    for c, by in typeset[:3]:
        label = "spoken list" if c["kind"] == "list" else (
            "triad" if c["kind"] == "triad" else "name")
        what.append(f"the {label} '{_quote(c['text'], 56)}' ({c['t0']:.1f}s) is set as the "
                    f"heard words ('{by[0]['id']}')")
    for c in bare[:max(0, 3 - len(what))]:
        label = "spoken list" if c["kind"] == "list" else (
            "triad" if c["kind"] == "triad" else "name")
        what.append(f"the {label} '{_quote(c['text'], 56)}' ({c['t0']:.1f}s) is left to the captions")
    return [_note(
        "showable_moment", (typeset[0][0] if typeset else bare[0])["t0"],
        "Showable moments carried only by type: " + "; ".join(what) + ".",
        ("Show them: one real photo or clip per list item, 0.3-0.6 s each on its "
         "onset (research_broll with one moment per item at its program time, or "
         "search_stock where listed; add_stock_media, then a cover overlay or an "
         "image_card / photo_stack run), a named product or place as a 1.5-2 s "
         "image with an identifying label, a statistic as the evidence it comes "
         "from. When retrieval finds nothing that truly shows it, keep the type — "
         "one accumulating list_build of noun phrases — and say so in the reply."),
        {"typeset": [c["text"] for c, _b in typeset], "bare": [c["text"] for c in bare]},
        # a list re-typeset as slams or rows is the judged miss; one set as
        # the accumulating list (the logged fallback), a name its graphic
        # already identifies, or one left to the captions, is a suggestion
        rank=2 if any(c["kind"] in ("list", "triad") and
                      any(m["template"] not in LIST_TEMPLATES for m in by)
                      for c, by in typeset) else 3)]


def _slam_list_notes(ms, prog, hook_ids):
    """A spoken list set as more than SLAM_RUN_MAX identical slams replacing
    each other in one slot, and list items shown without their noun."""
    notes = []
    runs = [r for r in _series(ms, solo=hook_ids)
            if r["template"] == "word_slam" and len(r["ids"]) > SLAM_RUN_MAX]
    if runs:
        r = runs[0]
        notes.append(_note(
            "list_as_slams", r["start"],
            (f"{len(r['ids'])} word slams replace each other in one slot "
             f"({r['start']:.1f}-{r['end']:.1f}s: {', '.join(r['ids'][:6])}) — the "
             "pile-up of the list is lost and the run reads mechanical."),
            ("Build one accumulating list instead (list_build: the items stack on "
             "their onsets as noun phrases, the newest accented, and the block "
             "clears on the next beat) — or show the items as images; keep a run "
             f"of identical slams to {SLAM_RUN_MAX}."),
            {"ids": r["ids"]}))
    # list items: the slams of a run of 3+ and every list_build row
    items = []
    by_id = {m["id"]: m for m in ms}
    for r in _series(ms, solo=hook_ids):
        if r["template"] == "word_slam" and len(r["ids"]) >= 3:
            for i in r["ids"]:
                m = by_id.get(i)
                if m:
                    items += [(m, text) for key, text in m["lines"] if key not in SIDE_KEYS]
    for m in ms:
        if m["template"] in LIST_TEMPLATES:
            items += [(m, text) for key, text in m["lines"] if key not in SIDE_KEYS]
    lone = []
    for m, text in items:
        toks = _content(_tokens(text))
        if not toks:
            continue
        last = toks[-1]
        spoken = prog.words_between(m["start"] - 1.0, m["end"] + 0.5)
        for k, w in enumerate(spoken):
            if (_tokens(str(w["w"])) or [""])[-1] != last:
                continue
            nxt = spoken[k + 1] if k + 1 < len(spoken) else None
            raw = str(w["w"]).rstrip()
            if nxt is None or raw.endswith((",", ".", ";", ":", "?", "!", "…")):
                break
            ntoks = _tokens(str(nxt["w"]))
            if ntoks and _content(ntoks) and _f(nxt["t0"]) - _f(w["t1"]) < 0.4 \
                    and not any(c.isdigit() for c in ntoks[0]):
                lone.append((m, _plain(text), f"{_plain(text)} {str(nxt['w']).strip(' ,.;:')}"))
            break
    if lone:
        listed = "; ".join(f"'{a.upper()}' ({m['id']}, said '{b}')" for m, a, b in lone[:3])
        notes.append(_note(
            "list_item_without_noun", lone[0][0]["start"],
            f"List items shown without their noun — {listed}: a lone modifier reads as an adjective.",
            ("Set noun phrases: " + ", ".join(f"'{b.upper()}'" for _m, _a, b in lone[:3])
             + " — the item the speaker names, not its first word."),
            {"items": [b for _m, _a, b in lone]}))
    return notes


def _figures(m):
    """The numbers a moment's main lines show ({float})."""
    out = set()
    for key, text in m["lines"]:
        if key in SIDE_KEYS:
            continue
        for tok in re.findall(r"\d[\d,]*(?:\.\d+)?", str(text)):
            try:
                out.add(float(tok.replace(",", "")))
            except ValueError:
                pass
    return out


def _number_as_said_notes(ms, prog):
    """A spoken range ('30, 40 fonts') shown as one of its ends: the caption
    shows 30 just before the graphic says 40 (judged as a contradiction)."""
    if not prog.kept:
        return []
    for c in _sb().numbers(prog.kept):
        if c["kind"] != "range":
            continue
        lo, hi = c["values"]
        for m in _overlaps(c["t0"], c["t1"], ms, pad=0.6):
            figs = _figures(m)
            if not figs or (any(_close(f, lo) for f in figs) and any(_close(f, hi) for f in figs)):
                continue
            if not any(_close(f, lo) or _close(f, hi) for f in figs):
                continue
            shows = next(f for f in figs if _close(f, lo) or _close(f, hi))
            rng = f"{_num(lo)}–{_num(hi)}"
            return [_note(
                "number_not_as_said", m["start"],
                (f"'{m['id']}' shows {_num(shows)} where the speaker says '{c['text']}' — "
                 f"the caption reads one figure while the graphic says the other."),
                (f"Show the number as said: a counter with value '{rng}' (each end "
                 "lands on its own spoken onset), or both figures in the lockup."),
                {"id": m["id"], "said": c["text"], "range": [lo, hi]})]
    return []


def _close(a, b):
    return abs(float(a) - float(b)) <= 1e-6 * max(1.0, abs(float(b)))


def _num(v):
    v = float(v)
    return str(int(v)) if v.is_integer() else f"{v:g}"


# ── HOOK TIER ─────────────────────────────────────────────────────────────

def _slam_main_fs(item, aspect=9 / 16.0):
    """Estimated font size of a display word_slam's lines as a share of the
    frame height (they share one size), mirroring the template's fit: the
    widest line fills ``width``, inside a height budget per line count; a
    single line of several words is stacked when that sets it clearly
    bigger. None for the other tiers (hook, payoff and hero are sized by
    their tier) and for an empty text."""
    p = item.get("params") or {}
    if str(p.get("tier") or "display") != "display":
        return None
    role = str(p.get("role") or "grotesk")
    em = SLAM_EM.get(role, SLAM_EM["grotesk"])
    lh = SLAM_LH.get(role, SLAM_LH["grotesk"])
    width = _f(p.get("width"), 0.85) or 0.85
    lines = [_plain(ln) for ln in re.split(r"\s*(?:/|\n)\s*", str(p.get("text") or ""))
             if _plain(ln)]
    if not lines:
        return None

    def fit(group):
        widest = max(len(x) for x in group) or 1
        budget = (SLAM_ONE_LINE_H if len(group) == 1 else SLAM_TWO_LINES_H) / (len(group) * lh)
        return min(width * aspect / (widest * em), budget)
    if len(lines) == 1 and len(lines[0].split()) >= 2:
        words = lines[0].split()
        cands = [[lines[0]]] + [[" ".join(words[:a]), " ".join(words[a:])]
                                for a in range(1, len(words))]
        best = max(cands, key=lambda g: fit(g) / (1.3 ** (len(g) - 1)))
        return fit(best)
    return fit(lines)


def _aspect(edl, index=None):
    """The program frame's width / height."""
    frame = edl.get("frame") if isinstance(edl.get("frame"), dict) else {}
    ratio = str((frame or {}).get("ratio") or "source")
    m = re.fullmatch(r"(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)", ratio)
    if m and float(m.group(2)) > 0:
        return float(m.group(1)) / float(m.group(2))
    canvas = edl.get("canvas") if isinstance(edl.get("canvas"), dict) else {}
    cw, ch = _f((canvas or {}).get("width")), _f((canvas or {}).get("height"))
    if cw > 0 and ch > 0:
        return cw / ch
    video = (index or {}).get("video") or {}
    w, h = _f(video.get("width")), _f(video.get("height"))
    return w / h if w > 0 and h > 0 else 9 / 16.0


def _captions_under(edl, m, prog):
    """The heard words captioned while hook moment ``m`` is up (words it
    does not show, outside any caption mute), quoted, or ''."""
    caps = edl.get("captions")
    if not (isinstance(caps, dict) and caps.get("mode") == "from_transcript") or not prog.kept:
        return ""
    mutes = []
    for raw in edl.get("caption_mutes") or []:
        try:
            mutes.append((float(raw[0]), float(raw[1])))
        except (IndexError, TypeError, ValueError):
            continue
    printed = set(_tokens(" ".join(t for _k, t in m["lines"])))
    heard = []
    for w in prog.words_between(m["start"], m["end"]):
        mid = (_f(w["t0"]) + _f(w["t1"])) / 2.0
        if not m["start"] <= mid <= m["end"] or any(a <= mid <= b for a, b in mutes):
            continue
        toks = _content(_tokens(str(w["w"])))
        if toks and not set(toks) <= printed:
            heard.append(str(w["w"]).strip())
    return " ".join(heard)


def _hook_size_notes(edl, hook_ms, bands, aspect=9 / 16.0, prog=None):
    """The hook tier: a hook title is a headline (main line 7%+ of the frame
    height) that owns its zone — no live caption stacked under it. One note
    for the first display slam that is too small or shares its zone."""
    if any(b["start"] <= HOOK_GRAPHIC_START_S + 1e-6 for b in bands):
        return []                 # a headline band is the hook's zone
    for m in hook_ms:
        if m["kind"] != "motion" or m["template"] != "word_slam":
            continue
        fs = _slam_main_fs(m["item"], aspect)
        if fs is None:
            # the hook tier (and payoff/hero) is sized by its tier; a hook
            # tier opted out of its zone (mute_captions=false) can still have
            # the live caption stacked under it
            import captions as caplib
            item = m["item"]
            if str((item.get("params") or {}).get("tier") or "") != "hook" \
                    or caplib.hook_owns_zone(item) or prog is None:
                continue
            fs, small, under = None, False, _captions_under(edl, m, prog)
            if not under:
                continue
        else:
            small = fs < HOOK_MIN_FS - HOOK_FS_SLACK
            under = _captions_under(edl, m, prog) if prog is not None else ""
        if not (small or under):
            continue
        said = (f" while the captions run '{_quote(under, 36)}' under it — two sentences "
                "at once") if under else ""
        if small:
            return [_note(
                "hook_small", m["start"],
                (f"The hook '{_quote(_main_text(m), 40)}' sets its main line at about "
                 f"{fs:.1%} of the frame height — no bigger than the captions, so the "
                 "first second has two reading tasks of one size" + said + "."),
                ("Make it a headline: word_slam tier='hook' sets the main line (the "
                 f"*starred* words) at {HOOK_MIN_FS:.0%}+ of the frame height with the "
                 "lead-in small above it and owns its zone — the captions wait until it "
                 "exits, never stacked under it; or set the claim in a headline band. "
                 "Keep it to 2-4 words on the main line."),
                {"id": m["id"], "main_fs": round(fs, 3), "captions_under": under})]
        return [_note(
            "hook_shares_zone", m["start"],
            (f"The hook '{_quote(_main_text(m), 40)}' is up{said}: the first seconds "
             "have two reading tasks."),
            ("Let the hook own its zone: word_slam tier='hook' (the captions wait until "
             "it exits), or a headline band; never a live caption stacked under it."),
            {"id": m["id"], "captions_under": under})]
    return []


def _family(name):
    """A free font name's type role, for a text the motion templates do not
    type (add_text): motion_look's own names first (FAMILY_ROLE), then a
    keyword guess."""
    import motion_look
    s = str(name or "").strip().lower()
    if not s:
        return None
    r = motion_look._role(s)
    if r:
        return r
    if "mono" in s or "jetbrains" in s or s == "code":
        return "mono"
    if any(k in s for k in ("script", "pinyon", "great vibes", "yellowtail")):
        return "script"
    if any(k in s for k in ("caveat", "hand")):
        return "hand"
    if any(k in s for k in ("condensed", "anton", "bebas", "oswald", "impact")):
        return "condensed"
    if "sans" not in s and any(k in s for k in (
            "serif", "playfair", "bodoni", "instrument", "didone", "garamond",
            "italic")):
        return "serif"
    if any(k in s for k in ("grotesk", "grotesque", "sans", "inter", "manrope",
                            "montserrat", "poppins", "archivo", "unbounded",
                            "helvetica", "syne", "jakarta", "display")):
        return "grotesk"
    return None


def _roles_of(m):
    """The type roles one moment sets. A motion graphic's are the motion
    write's own (motion_look.item_roles: what the template draws for its
    params and defaults), so the review and the write's NOTE (look) count
    the same roles; a text's is its font's (_family)."""
    import motion_look
    item = m["item"]
    if m["kind"] == "text":
        fam = _family(item.get("font"))
        return [fam] if fam else []
    try:
        return list(motion_look.item_roles(item))
    except Exception:  # noqa: BLE001 — an odd item sets no role
        return []


def _accent_of(m):
    """The accent colour one moment visibly wears: a text's accent_color; a
    motion graphic's as the write reads it (motion_look.item_accent: drawn
    only where the template draws it — a starred word, an accent row, a
    payoff lockup, a template that always wears it — its default included)."""
    import motion_look
    item = m["item"]
    if m["kind"] == "text":
        c = item.get("accent_color")
        return c.upper() if isinstance(c, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", c) else None
    try:
        return motion_look.item_accent(item)
    except Exception:  # noqa: BLE001
        return None


def _type_notes(edl, ms):
    import motion_look
    notes = []
    fams, who = {}, {}
    for m in ms:
        for fam in _roles_of(m):
            fams.setdefault(fam, m)
            who.setdefault(fam, []).append(m["id"])
    if len(fams) > MAX_TYPE_ROLES:
        listed = ", ".join(f"{fam} ({', '.join(dict.fromkeys(who[fam]))})"
                           for fam in sorted(fams))
        notes.append(_note(
            "type_roles", None,
            (f"The graphics use {len(fams)} type roles — {listed}; a short "
             f"reads as one system with at most {MAX_TYPE_ROLES}."),
            ("Re-set the graphics to one backbone (a grotesk) plus one accent "
             "role (serif italic, script or condensed) and, if needed, one "
             "for numbers; vary size, not typeface."),
            {"roles": sorted(fams)}))
    accents = {}
    for m in ms:
        colour = _accent_of(m)
        if colour:
            accents.setdefault(colour, m["id"])
    # the captions' highlight is the short's accent only when it has a hue
    # (motion_look.caption_accent: a white highlight over grey captions is
    # no second accent)
    cap = motion_look.caption_accent(edl)
    if cap and accents:
        accents.setdefault(cap, "captions")
    if len(accents) > MAX_ACCENTS:
        listed = ", ".join(f"{c} ({who})" for c, who in accents.items())
        notes.append(_note(
            "accent_colours", None,
            f"{len(accents)} accent colours are in play — {listed}.",
            ("Pass one accent colour to every graphic's accent (and the "
             "captions' highlight) so the short reads as one design system."),
            {"accents": sorted(accents)}))
    return notes


def _identify_notes(ms, hook_ms):
    notes = []
    hook_ids = {m["id"] for m in hook_ms}
    for m in ms:
        if m["id"] in hook_ids:
            continue
        for key, text in m["lines"]:
            for line in re.split(r"\s*(?:/|\n)\s*", str(text)):
                hit = _LABEL_LINE.match(line)
                if hit and hit.group(1).lower() not in LABEL_WORDS:
                    name = hit.group(1)
                    notes.append(_note(
                        "label_reads_as_dialogue", m["start"],
                        (f"'{name}:' on its own line in '{m['id']}' "
                         f"({m['start']:.1f}s) reads as a dialogue label or a "
                         "person's name, not as what it is."),
                        (f"Identify it — what '{name}' is (maker, product, "
                         "year), e.g. a small label under the word — and "
                         "consider showing it (a real photo via search_stock "
                         "in an image_card). Never a product name with a "
                         "colon."),
                        {"id": m["id"], "name": name}))
                    return notes
    return notes


def _lower_third_notes(ms, hook_ms, bands):
    thirds = [m for m in ms if m["template"] in ("lower_third", "text:lower_third")]
    if not thirds or not (hook_ms or bands):
        return []
    m = thirds[0]
    text = " / ".join(t for _k, t in m["lines"])
    return [_note(
        "lower_third_redundant", m["start"],
        (f"A broadcast lower third ('{_quote(text)}', {m['start']:.1f}-"
         f"{m['end']:.1f}s) adds a second text system over the speaker while "
         "a hook or headline is already on screen."),
        ("Fold the verified name (plus role or year) into the hook's kicker "
         "or the headline band and remove the lower third; a famous speaker "
         "needs no broadcast name strap."),
        {"id": m["id"]})]


# ── PAYOFF ────────────────────────────────────────────────────────────────

def _tail_filled(ms, prog, t0):
    """Is the program after ``t0`` (the last word) mostly carried by a
    designed moment (a requested CTA, the payoff lockup) or a spliced clip?
    Then it is not a dead tail."""
    dur = prog.duration
    if dur - t0 <= 0:
        return True
    spans = [{"start": max(t0, m["start"]), "end": m["end"]}
             for m in ms if m["end"] > t0]
    try:
        spans += [{"start": max(t0, a), "end": a + d}
                  for a, d in prog.tl.insert_positions() if a + d > t0]
    except Exception:  # noqa: BLE001
        pass
    return _coverage(spans, dur) >= 0.5 * (dur - t0)


def _reaction_shot(edl, index, prog):
    a, prev_end = prog.keep[-1][0], prog.keep[-2][1]
    if abs(a - prev_end) >= 0.02:
        return True
    before, after = _shot_at(index, a - 0.01), _shot_at(index, a + 0.01)
    if before is not None and after is not None and before != after:
        return True
    frame = edl.get("frame") if isinstance(edl.get("frame"), dict) else {}
    return any(abs(_f(_get(span, "t0"), -1.0) - a) < 0.05
               for span in (frame or {}).get("focus_track") or [])


def _end_hold_s(edl, prog):
    """Seconds of payoff hold (schemas.InsertItem.hold) spliced at the end
    of the footage: the last kept frame held over room tone."""
    try:
        end = float(sum(prog.tl.seg_out_len))
    except Exception:  # noqa: BLE001
        return 0.0
    out = 0.0
    for item in edl.get("inserts") or []:
        if isinstance(item, dict) and item.get("kind") == "image" and \
                isinstance(item.get("hold"), dict) and \
                abs(_f(item.get("at_output_s"), -1.0) - end) < 0.02:
            out += _f(item.get("duration_s"))
    return out


def _payoff_notes(prog, payoff, ms=(), index=None, edl=None):
    notes = []
    dur = prog.duration
    if prog.kept and dur > 0:
        last = max(prog.kept, key=lambda w: _f(w["t1"]))
        hold = dur - _f(last["t1"])
        if hold < PAYOFF_HOLD_MIN_S - 1e-6:
            src_end = _f(last.get("src_t1"), None)
            keep_end = prog.keep[-1][1] if prog.keep else None
            after = [w for w in prog.words
                     if src_end is not None and _f(_get(w, "t0")) >= src_end - 1e-3]
            nxt = min(after, key=lambda w: _f(_get(w, "t0"))) if after else None
            src_dur = _f(((index or {}).get("video") or {}).get("duration"))
            want = PAYOFF_HOLD_TARGET_S - hold       # seconds of air to add
            # the source's own tail first (a reaction, the speaker's
            # natural pause), then a held frame for whatever it lacks
            if keep_end is None:
                room = 0.0
            elif nxt is not None:
                room = max(0.0, _f(_get(nxt, "t0")) - 0.05 - keep_end)
            elif src_dur > 0:
                room = max(0.0, src_dur - 0.05 - keep_end)
            else:
                room = want
            ext = round(min(room, want), 2)
            if ext < 0.1:
                ext = 0.0                       # too little to restore
            rest = round(max(0.0, want - ext), 2)
            hold_call = (f"add_freeze_frame(at_output_s={dur + ext:.2f}, "
                         f"duration_s={max(0.3, rest):.1f}, "
                         "audio_mode='hold')")
            if ext and rest < 0.1:
                fix = (f"Hold {PAYOFF_HOLD_MIN_S:g}-{PAYOFF_HOLD_MAX_S:g} s "
                       "after the last word: extend the last keep to about "
                       f"{keep_end + ext:.2f}s source ("
                       + (f"the pause before '{_get(nxt, 'w')}' allows it"
                          if nxt is not None else "the speaker's natural "
                          "tail or reaction")
                       + "; restore_range).")
            elif ext:
                fix = (f"Hold {PAYOFF_HOLD_MIN_S:g}-{PAYOFF_HOLD_MAX_S:g} s "
                       f"after the last word: extend the last keep to about "
                       f"{keep_end + ext:.2f}s source (restore_range; "
                       + (f"'{_get(nxt, 'w')}' follows" if nxt is not None
                          else "the source ends")
                       + f"), then hold the frame for the rest: {hold_call} "
                       "— the composed frame over room tone, any payoff "
                       "graphic held over it.")
            elif nxt is not None:
                fix = (f"The speaker runs on into '{_get(nxt, 'w')}' "
                       f"{_f(_get(nxt, 't0')) - (src_end or keep_end):.2f}s "
                       f"after '{last['w']}', so the source has no longer "
                       f"tail: hold the frame instead — {hold_call} (the "
                       "composed frame over the source's room tone, the "
                       "last words fading into it; the payoff graphic holds "
                       "over it), or end on an earlier line that leaves a "
                       "pause.")
            else:
                fix = (f"The source itself ends {max(0.0, src_dur - (src_end or keep_end or 0.0)):.2f}s "
                       f"after '{last['w']}', so there is no tail to "
                       f"restore: hold the frame — {hold_call} — or end on "
                       "an earlier line that leaves a pause.")
            notes.append(_note(
                "payoff_hold", _f(last["t1"]),
                (f"The payoff gets {max(0.0, hold):.2f}s after '{last['w']}' "
                 "before the end card — the punchline has no air."),
                fix, {"hold_s": round(hold, 2), "last_word": last["w"]}))
        elif hold > DEAD_TAIL_S and not _tail_filled(ms, prog, _f(last["t1"])):
            notes.append(_note(
                "payoff_hold", _f(last["t1"]),
                (f"{hold:.1f}s run on after the last word ('{last['w']}') "
                 "before the end card — a dead tail after the payoff."),
                (f"Trim the tail to {PAYOFF_HOLD_MIN_S:g}-{PAYOFF_HOLD_MAX_S:g} s "
                 "after the last word (a reaction may take ~1.5 s)."),
                {"hold_s": round(hold, 2), "last_word": last["w"]}))
        # A closing reaction is its own shot: a separate take after a real
        # cut, a camera change, or the frame re-aimed at the listener. A
        # keep merely split at the same source time is the line's own tail.
        if len(prog.keep) >= 2 and _reaction_shot(edl or {}, index or {}, prog):
            a, b = prog.keep[-1]
            inside = [w for w in prog.words
                      if a < (_f(_get(w, "t0")) + _f(_get(w, "t1"))) / 2.0 < b]
            # a payoff hold after it (add_freeze_frame audio_mode='hold') is
            # the reaction held on its own last frame: it counts
            length = (b - a) + _end_hold_s(edl or {}, prog)
            if not inside and length < REACTION_MIN_S - 1e-6:
                notes.append(_note(
                    "reaction_button_short", dur - length,
                    (f"The closing reaction lasts {length:.2f}s — too short to "
                     "read as a reaction before the end card."),
                    ("Give the reaction 1.0-1.5 s (extend the last keep, "
                     "or hold its last frame over room tone where the "
                     "source runs into dialogue: add_freeze_frame "
                     "audio_mode='hold'), framed like that speaker's earlier "
                     "shot, or end on the line instead."),
                    {"seconds": round(length, 2)}))
    if payoff is not None and dur > 0:
        notes += _payoff_number_hold(payoff, dur)
    if payoff is not None and payoff["template"] in (
            "counter", "word_slam", "stat_card", "text:big_number"):
        seq = shown(payoff["lines"])[0]
        if seq and all(any(c.isdigit() for c in t) for t in seq):
            notes.append(_note(
                "payoff_number_without_noun", payoff["start"],
                (f"The payoff graphic '{payoff['id']}' ({payoff['start']:.1f}s) "
                 f"shows '{_quote(_main_text(payoff), 20)}' alone — a number "
                 "without its noun."),
                ("Lock the number and its noun together ('140 / CHARACTERS') as "
                 "the short's largest accented lockup, face-safe; where the "
                 "story set up a device earlier (a then-vs-now split), rhyme "
                 "the payoff with it."),
                {"id": payoff["id"]}))
    return notes


def _landing(m):
    """Program second a payoff graphic lands (a counter on its 'land', a
    slam entrance 0.2 s in, anything else on its start)."""
    item = m.get("item") or {}
    p = item.get("params") or {}
    if m["template"] == "counter":
        try:
            import number_reveal
            return m["start"] + number_reveal.counter_landing(p, m["end"] - m["start"])
        except Exception:  # noqa: BLE001
            return m["start"]
    if m["template"] == "word_slam" and p.get("entrance") == "slam":
        return m["start"] + 0.2
    return m["start"]


def _payoff_number_hold(payoff, dur):
    """A big payoff number needs about PAYOFF_NUMBER_HOLD_S on screen before
    the end card (judged: Thiel's '140 / CHARACTERS' got 1.41 s)."""
    if not any(any(c.isdigit() for c in t) for t in shown(payoff["lines"])[1]):
        return []
    landed = _landing(payoff)
    held = dur - landed
    if held >= PAYOFF_NUMBER_HOLD_S - 1e-6:
        return []
    return [_note(
        "payoff_number_hold", landed,
        (f"The payoff number '{_quote(_main_text(payoff), 28)}' is on screen "
         f"{max(0.0, held):.2f}s before the end card — a big number needs about "
         f"{PAYOFF_NUMBER_HOLD_S:g} s to land."),
        (f"Hold it about {PAYOFF_NUMBER_HOLD_S:g} s: keep it landing on its word "
         "and extend the last keep into the speaker's tail (restore_range), or "
         "hold the closing frame under it before the end card (add_freeze_frame "
         "audio_mode='hold')."),
        {"id": payoff["id"], "held_s": round(held, 2)})]


# ── TRIM RHYTHM ───────────────────────────────────────────────────────────

def _norm(word):
    return re.sub(r"[^\w']", "", str(word or "").lower())


def _trim_notes(prog):
    if not prog.kept:
        return []
    collisions, dropped = [], []
    rescued = [(str(w["w"]), _f(w.get("src_t0"))) for w in prog.kept
               if w.get("heard")]
    for i, t, a_end, b_start in prog.joins(covered=True):
        before = [w for w in prog.kept if _f(w["t1"]) <= t + 1e-3]
        after = [w for w in prog.kept if _f(w["t0"]) >= t - 1e-3]
        if not before or not after:
            continue
        prev, nxt = before[-1], after[0]
        gap = _f(nxt["t0"]) - _f(prev["t1"])
        if _SENTENCE_END.search(str(prev["w"]).strip()) and \
                gap < SENTENCE_PAUSE_MIN_S - 1e-6:
            collisions.append((t, prev["w"], nxt["w"], gap))
        removed = []
        for w in prog.words:
            w0, w1 = _f(_get(w, "t0")), _f(_get(w, "t1"))
            if a_end < (w0 + w1) / 2.0 < b_start and not any(
                    tok == str(_get(w, "w")) and w0 - 1e-3 <= s0 <= w1 + 1e-3
                    for tok, s0 in rescued):
                removed.append(str(_get(w, "w")))
        toks = [_norm(r) for r in removed]
        # A stutter or restart cut ('of the [the] world', '[to] to go')
        # removes a repeat of the words either side: nothing is lost.
        n = len(toks)
        kept_before = [_norm(w["w"]) for w in before[-n:]] if n else []
        kept_after = [_norm(w["w"]) for w in after[:n]] if n else []
        repeat = bool(n) and (toks == kept_before or toks == kept_after)
        if 1 <= n <= 2 and not repeat \
                and all(tk in CLAUSE_WORDS for tk in toks) \
                and not _CLAUSE_END.search(str(prev["w"]).strip()) \
                and not _CLAUSE_END.search(removed[-1].strip()):
            dropped.append((t, prev["w"], " ".join(removed), nxt["w"]))
    notes = []
    if collisions:
        listed = "; ".join(f"{t:.2f}s '{a} | {b}' ({max(0.0, g) * 1000:.0f} ms)"
                           for t, a, b, g in collisions[:3])
        notes.append(_note(
            "sentence_collision", collisions[0][0],
            f"Sentences collide across {len(collisions)} cut(s) with no breath — {listed}.",
            ("Keep 150-250 ms at sentence boundaries (more before the payoff "
             "line): move the cut a little into the pause."),
            {"joins": [round(c[0], 2) for c in collisions]}))
    if dropped:
        listed = "; ".join(f"{t:.2f}s '{a} [{w}] {b}'" for t, a, w, b in dropped[:3])
        notes.append(_note(
            "clause_word_dropped", dropped[0][0],
            f"A cut drops a word inside a clause — {listed}.",
            ("Restore the word (restore_range) or move the cut to the clause "
             "boundary; trims remove connectors and pauses, never the "
             "articles that hold a phrase together."),
            {"joins": [round(d[0], 2) for d in dropped]}))
    return notes


# ── RESTRAINT (zooms are optional; these notes only ever offer removal) ──

def _zoom_notes(edl, prog):
    notes = []
    zooms = [z for z in ((edl.get("effects") or {}).get("zooms") or [])
             if isinstance(z, dict)]
    slow = []
    for z in zooms:
        mode = str(z.get("mode") or "punch")
        start, end = _f(z.get("start")), _f(z.get("end"))
        strength = _f(z.get("strength"))
        if mode in ("ease", "push_in", "pull_out") and end - start >= ZOOM_SLOW_MIN_S \
                and 0 < strength < ZOOM_VISIBLE_STRENGTH - 1e-9:
            slow.append((z, start, end, strength))
    if slow:
        z, start, end, strength = slow[0]
        notes.append(_note(
            "zoom_barely_visible", start,
            (f"Zoom {z.get('id') or '?'} is a {1 + strength:.2f}x {z.get('mode')} "
             f"over {end - start:.1f}s — barely visible: neither a clear move "
             "nor a steady frame."),
            ("Remove it (a steady frame is the default). Keep a camera move "
             "only where a specific moment clearly earns one."),
            {"ids": [s[0].get("id") for s in slow]}))
    follow = ((edl.get("frame") or {}) if isinstance(edl.get("frame"), dict)
              else {}).get("follow") or []
    moves = []
    for span in follow:
        keys = sorted((k for k in (_get(span, "k") or []) if len(k) >= 3),
                      key=lambda k: _f(k[0]))
        for a, b in zip(keys, keys[1:]):
            if abs(_f(b[1]) - _f(a[1])) >= FOLLOW_MOVE_MIN or \
                    abs(_f(b[2]) - _f(a[2])) >= FOLLOW_MOVE_MIN:
                for lo, hi in prog.tl.span_to_out(_f(a[0]), _f(b[0])):
                    moves.append((lo, hi, _f(a[1]), _f(b[1])))
    for z in zooms:
        start, end = _f(z.get("start")), _f(z.get("end"))
        for lo, hi, x0, x1 in moves:
            if min(end, hi) - max(start, lo) >= 0.2:
                notes.append(_note(
                    "zoom_with_follow_pan", max(start, lo),
                    (f"Zoom {z.get('id') or '?'} ({start:.1f}-{end:.1f}s) and a "
                     f"follow pan (x {x0:.3f}→{x1:.3f} at {lo:.1f}-{hi:.1f}s) run "
                     "together — two camera moves stacked read as drift."),
                    ("Remove the zoom, or keep the frame still under it; never "
                     "stack a push with a follow pan."),
                    {"zoom": z.get("id")}))
                return notes
    return notes


# ── FRAME ─────────────────────────────────────────────────────────────────

def _frame_notes(edl, index, duration, designed=True):
    # Only a designed short (graphics or a headline band) is held to the
    # premium frame-and-grade standard: a plain clip the user only asked to
    # caption or trim is theirs ("do only what the user asked").
    if not designed:
        return []
    fx = edl.get("effects") or {}
    if fx.get("grade") or fx.get("grade_custom") or fx.get("custom"):
        return []
    if edl.get("canvas"):
        return []
    frame = edl.get("frame") if isinstance(edl.get("frame"), dict) else {}
    if str((frame or {}).get("mode") or "crop") != "crop":
        return []
    picture = (frame or {}).get("picture")
    if picture and [round(_f(v), 3) for v in picture] != [0.0, 0.0, 1.0, 1.0]:
        return []                      # letterbox: a committed frame
    cards = [c for c in (fx.get("picture_cards") or []) if isinstance(c, dict)]
    carded = _coverage([{"start": _f(c.get("start")),
                         "end": _f(c.get("end"), duration)} for c in cards],
                       duration) if duration > 0 else 0.0
    if duration <= 0 or carded / duration >= 0.5:
        return []
    plate = ""
    samples = ((index or {}).get("spatial") or {}).get("samples") or []
    lumas = [_f(_get(s, "mean_luma"), None) for s in samples]
    lumas = [v for v in lumas if v is not None]
    edges = [_f(_get(s, "edge_density"), None) for s in samples]
    edges = [v for v in edges if v is not None]
    if lumas and sum(lumas) / len(lumas) >= 150:
        plate = " on a bright, washed-out plate"
    elif edges and sum(edges) / len(edges) >= 0.07:
        plate = " on a busy plate"
    return [_note(
        "frame_uncommitted", None,
        (f"The short is full-bleed{plate} with no committed grade for "
         f"{1 - carded / duration:.0%} of its runtime."),
        ("Commit to a grade (apply_look or set_color_grade). When the plate "
         "is busy or washed out, a card on a textured canvas or a letterbox "
         "with a headline band frames it; full-bleed only when the plate is "
         "clean — a taste call, not a default."),
        {"full_bleed_share": round(1 - carded / duration, 3)})]


# ── the review ────────────────────────────────────────────────────────────

# A user who asked for the thing a note questions has decided it: the request
# only ever SUPPRESSES a note, never raises one (taste.critique's rule).
ASKED = {
    "lower_third_redundant": ("lower third", "lower_third", "name strap",
                              "name tag", "name card"),
    "zoom_barely_visible": ("zoom", "push in", "push-in", "punch in",
                            "punch-in", "ken burns"),
    "zoom_with_follow_pan": ("zoom", "push in", "push-in", "pan"),
    "frame_uncommitted": ("full-bleed", "full bleed", "no grade", "ungraded",
                          "no colour grade", "no color grade", "natural colour",
                          "natural color"),
    "hook_generic_question": ("question",),
    "graphic_budget": ("more graphics", "lots of graphics", "every word",
                       "dense", "busy"),
    "transcript_list": ("list",),
    "dead_stretch": ("minimal", "fewer graphics", "less graphics", "no graphics",
                     "only captions", "captions only", "just captions"),
    "showable_moment": ("no b-roll", "no broll", "no stock", "no images",
                        "no pictures", "text only", "type only"),
    "list_as_slams": ("word slams", "one word at a time"),
    "hook_small": ("small hook", "subtle hook", "small title", "subtle title"),
    "type_roles": ("fonts", "typefaces", "font mix", "mixed type"),
    "accent_colours": ("accent colours", "accent colors", "two colours",
                       "two colors", "colourful", "colorful"),
}


def _asked(code, ask):
    return any(re.search(r"\b" + re.escape(k) + r"\b", ask)
               for k in ASKED.get(code, ()))


def review(edl, index=None, force=False, request_text=None):
    """Advisory notes for one EDL, most important first. Each note:
    {"code", "rank" (1 = most important), "at" (program s or None),
    "message", "fix", "evidence"}. Empty unless the program is a
    vertical/square short of SHORT_FORM_MAX_S or less (``force`` skips that
    gate). ``request_text`` (the user's own words) only suppresses notes on
    what they asked for. Never raises on odd data: a check that cannot read
    its inputs is skipped."""
    if not isinstance(edl, dict) or not edl.get("keep"):
        return []
    index = index if isinstance(index, dict) else {}
    duration = _duration(edl)
    if not force and not short_form(edl, index, duration):
        return []
    prog = _Program(edl, index)
    ms, bands_ = _layers(edl, duration)
    hook_ms = _hook_moments(ms)
    hook_ids = {m["id"] for m in hook_ms}
    payoff = _payoff_moment(ms, duration)
    notes = []
    checks = (
        lambda: _hook_question_notes(hook_ms, bands_),
        lambda: _hero_repeat_notes(ms, hook_ms, bands_),
        lambda: _hook_open_notes(edl, index, prog),
        lambda: _payoff_notes(prog, payoff, ms, index, edl),
        lambda: _identify_notes(ms, hook_ms),
        lambda: _lower_third_notes(ms, hook_ms, bands_),
        lambda: _type_notes(edl, ms),
        lambda: _trim_notes(prog),
        lambda: _zoom_notes(edl, prog),
        lambda: _frame_notes(edl, index, duration,
                             designed=bool(ms or bands_)),
    )
    restated_ids = set()
    try:
        found, restated_ids = _restate_notes(ms, prog, hook_ids, payoff)
        notes += found
    except Exception as exc:  # noqa: BLE001 — advisory: skip, never break
        print(f"[edit_review] restate check skipped: {type(exc).__name__}",
              flush=True)
    try:
        notes += _budget_notes(ms, duration, restated_ids, hook_ids)
    except Exception as exc:  # noqa: BLE001
        print(f"[edit_review] budget check skipped: {type(exc).__name__}",
              flush=True)
    # beat coverage and showable moments hold a DESIGNED short to its own
    # standard; a plain clip the user only asked to caption or trim is theirs
    designed = bool(ms or bands_)
    dead_gaps = []
    if designed:
        try:
            found = _dead_stretch_notes(edl, ms, prog, duration, hook_ms)
            notes += found
            dead_gaps = [tuple(g) for n in found for g in n["evidence"]["gaps"]]
        except Exception as exc:  # noqa: BLE001
            print(f"[edit_review] beat coverage skipped: {type(exc).__name__}: {exc}",
                  flush=True)
    checks = checks + (
        lambda: _showable_notes(edl, ms, prog, dead_gaps) if designed else [],
        lambda: _slam_list_notes(ms, prog, hook_ids),
        lambda: _number_as_said_notes(ms, prog),
        lambda: _hook_size_notes(edl, hook_ms, bands_, _aspect(edl, index), prog),
    )
    for check in checks:
        try:
            notes += check()
        except Exception as exc:  # noqa: BLE001
            print(f"[edit_review] check skipped: {type(exc).__name__}: {exc}",
                  flush=True)
    ask = str(request_text or "").lower()
    if ask:
        notes = [n for n in notes if not _asked(n["code"], ask)]
    notes.sort(key=lambda n: (n["rank"], ORDER.get(n["code"], 99),
                              n["at"] if n["at"] is not None else 1e9))
    return notes


def _first_sentence(text):
    hit = re.match(r"(.+?[.!?])(?:\s|$)", str(text or ""))
    return hit.group(1) if hit else str(text or "")


def note_line(note, short=False):
    fix = _first_sentence(note["fix"]) if short else note["fix"]
    return f"{note['message']} Fix: {fix}"


def summary(notes, limit=3):
    """One advisory paragraph: the top ``limit`` notes, each with the first
    sentence of its fix, then the codes of the rest (the verification
    record's evidence keeps every note with its full fix)."""
    if not notes:
        return ""
    head = notes[:limit]
    text = (f"EARN ITS PLACE ({len(notes)} note{'s' if len(notes) != 1 else ''}"
            "; every graphic must add what the captions cannot): "
            + " ".join(f"{k}) {note_line(n, short=True)}"
                       for k, n in enumerate(head, 1)))
    rest = notes[limit:]
    if rest:
        text += (f" (+{len(rest)} more: "
                 + ", ".join(dict.fromkeys(n["code"] for n in rest)) + ")")
    return text


def _main(argv):
    import json
    import sys
    if len(argv) < 2:
        print("usage: edit_review.py <spec.json> [index.json]", file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as fh:
        spec = json.load(fh)
    edl = spec.get("edl", spec)
    index = argv[2] if len(argv) > 2 else spec.get("index") or {}
    if isinstance(index, str):
        with open(index, encoding="utf-8") as fh:
            index = json.load(fh)
    notes = review(edl, index, force=True)
    for note in notes:
        at = f"@{note['at']:.2f}s " if note.get("at") is not None else ""
        print(f"[{note['rank']}] {note['code']} {at}{note_line(note)}")
    print("\nSUMMARY: " + summary(notes))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv))
