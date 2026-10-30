"""The trial reply: true, short and warm (Q8, 30 Oct 2026).

A trial account's reply is the moment it decides whether the editor is worth
paying for. Two things went wrong in the harness replays (148 Haiku trial
replies, round2 + round3 harness databases, .valmera/week-1029/round3/
Q8_SPEED_TRIAL_UX_TRUTH.md):

  * claims the turn's facts do not back: "a 15.6-second cut" for a 27-second
    preview, "I added a warm grade" when the grade did not change, "the final
    preview passed" on a turn that rendered nothing, "ready to download" on an
    account that cannot export without a plan, a "quoted" line the speaker
    never said;
  * replies that read like a review log: picture-QC findings, "two things
    remain", "I used up my preview renders", flagged spans with timestamps,
    LUFS, version numbers, apologies.

``finish`` runs on the trial lane's final reply, after the honesty guard
(agent_loop._enforce_honesty) and before the reply is posted:

  1. every sentence is checked against the turn's facts (the EDL at the
     turn's start and now, the preview of the latest version, the
     transcript): an effect claimed that did not change (R2's
     reply_grounding, the same classes as the platform audit), an export or
     download claim, a quote the transcript does not hold, a length or shape
     that is not the video's, a render or a check that did not happen. The
     sentence is dropped;
  2. sentences that are the editor's own process (QC, findings, versions,
     the preview budget, apologies) are dropped, and at most one honest
     limitation sentence is kept;
  3. what is left becomes: what changed (the draft's own words when they
     survive, else the change list from the EDL diff), where to look, and one
     useful next step (the draft's own offer when it has one).

Pure functions over a ``Facts`` record, so the replay set and the tests run
the exact code the worker runs. Only the trial lane calls it
(agent_loop gates on ctx.claude_trial): grandfathered subscribers' replies
are untouched (D1).
"""
import json
import re
from dataclasses import dataclass, field

import reply_grounding as rg

MAX_SENTENCES = 4          # what changed, where to look, one next step (+1)
MAX_WORDS = 75


@dataclass
class Facts:
    before: dict = field(default_factory=dict)   # EDL json at the turn's start
    after: dict = field(default_factory=dict)    # EDL json now
    wrote: bool = False                          # a new version this turn
    previewed: bool = False                      # the latest version has a render
    duration_s: float = None                     # that render's length
    frame: str = None                            # "vertical", "landscape", "square"
    index: dict = field(default_factory=dict)    # transcript (words)
    user_text: str = ""
    checked: bool = False                        # a verification passed this turn


# ── sentence classes ─────────────────────────────────────────────────────

_INTERNAL = re.compile(
    r"(?i)(\bpicture[- ]?qc\b|\bqc\b|\bverif(?:y|ied|ication)\b|\breview(?:er)?s?\b"
    r"|\bnotes? (?:i|we) (?:did not|didn't)|\bflag(?:s|ged)?\b|\bfindings?\b"
    r"|\bfalse positive|\badvisor(?:y|ies)\b|\blufs\b|\bmid-?word\b|\bedl\b"
    r"|\bkeep (?:layer|segments?)\b|\btool\b|\bpreview (?:limit|budget|renders?)\b"
    r"|\brender (?:limit|budget)\b|\bused up\b|\bthe (?:checks?|check came back)\b"
    r"|\bpicture check|\bcaption check|\bstory check|\bthree-frame|\bspeculative"
    r"|\bv\d+\b|\bversion \d+\b|\(system|\[system|\bcorner mark\b|\bfree-tier\b"
    r"|\bhero frame|\bfocus_|\bset_frame\b|\bsource rect|\bcrop_|\bpad_blur\b"
    r"|\bimprove it\b|\bpress\b|\bexport this version\b|\bnot a blocker\b|\bfree plan\b"
    r"|\bwarnings?\b|\bnotes? remain|\bmeasured depth\b|\bpreview (?:shows|confirms)\b"
    r"|\bthe (?:render|preview)'s\b)")
_APOLOGY = re.compile(r"(?i)\b(sorry|apologi[sz]e|unfortunately|i'?m afraid)\b")
_LIMIT = re.compile(
    r"(?i)(\b(?:i|we) (?:can't|cannot|couldn't|could not|wasn't able to|was not able to|"
    r"am unable to|didn't (?:fix|change|get|add|manage)|did not (?:fix|change|get|add|manage)|"
    r"haven't (?:fixed|changed)|left (?:it|them|those|both|that)\b)"
    r"|\b(?:remains?|still) (?:open|off|in|there|tight)\b|\bthings? (?:remain|are still)\b"
    r"|\bisn't (?:possible|supported|clear)\b|\bnot (?:possible|supported)\b"
    r"|\bdoesn't (?:support|allow)\b|\bno room\b|\bbe aware\b|\bone issue\b)")
_OFFER = re.compile(
    r"(?i)(\?\s*$|\b(?:want me to|would you like|i can (?:also )?|if you want|if you'd like|"
    r"say (?:the word|if)|tell me|let me know|next,? (?:i|try|you)|try asking|ask me)\b)")
_PLACE = re.compile(r"(?i)\b(playing|on the right|the player|preview (?:on the right|shows|is playing))\b")

# a claim about the video's length: "a 36-second cut", "now runs 25.6 seconds",
# "is 18.5 s long", "at 51.6 seconds" when it names the whole ("ready at")
_LENGTH = re.compile(
    r"(?i)(?:\b(?:is|runs?|running|now|ready at|comes in at|down to|cut to|trimmed to|lasts?)\s+(?:about\s+|around\s+|roughly\s+|just\s+|under\s+|exactly\s+|precisely\s+|only\s+|nearly\s+|almost\s+|over\s+)?"
    r"(\d{1,3}(?:\.\d+)?)\s*(?:-\s*)?(?:seconds?|secs?|s)\b(?!\s*(?:in|into|of|before|after|mark|later|earlier|and|to|–|-))"
    r"|\b(?:a|an|the|your)\s+(?:tight\s+|clean\s+|new\s+|full\s+)?(\d{1,3}(?:\.\d+)?)\s*-?\s*(?:second|sec|s)\b"
    r"(?=[\s,-]+(?:\w+[\s,-]+)?(?:cut|clip|video|short|reel|version|edit|highlight|piece|demo|vertical|"
    r"landscape|talking|9:16|program|story|montage|tour)))")
_VERTICAL = re.compile(r"(?i)\b(vertical|9:16|portrait)\b")
_LANDSCAPE = re.compile(r"(?i)\b(landscape|16:9|widescreen)\b")
_RENDER_CLAIM = re.compile(
    r"(?i)\b((?:new|fresh|updated|latest|final|complete|rendered)\s+(?:preview|render)"
    r"|preview (?:is |was )?(?:ready|rendered|updated|refreshed)|re-?rendered|rendered (?:it|a|the)"
    r"|previewed below|preview below)\b")
_CHECK_CLAIM = re.compile(
    r"(?i)\b(passed|passes|checks? out|came back clean|looks? clean|is clean|verified|"
    r"i (?:checked|looked at|reviewed|watched|screened)|the frames show|found the \w+ clean|"
    r"check found|confirms?)\b")
_TRIAL_EXPORT = re.compile(
    r"(?i)\b(download (?:it|the (?:video|file|mp4|final))(?! with a plan)|ready to (?:download|export|post|upload)"
    r"|(?:i|we)(?:'ve| have)? exported|exported (?:it|the)|export (?:is|was) (?:ready|done|complete)"
    r"|full-quality (?:mp4|file) is ready)\b")


_DENY = re.compile(
    r"(?i)(\bnothing (?:was |has been )?changed\b|\bno changes? (?:were|was|have been) made\b"
    r"|\b(?:i )?(?:didn't|did not|haven't|have not) (?:change|modify|touch|edit)(?:d)? "
    r"(?:the |your )?(?:edl|edit|video|anything|cut)\b|\b(?:edit|cut|video) (?:is|remains) unchanged\b"
    r"|\bupgrade to start editing\b|\bnot changed (?:the )?(?:edit|video)\b)")
_RENDER_DENY = re.compile(
    r"(?i)\b(?:i )?(?:didn't|did not|haven't|have not|couldn't|could not)\s+(?:yet\s+)?"
    r"(?:render(?:ed)?|re-?render(?:ed)?|made a (?:new )?preview)\b")
# the trial turn's own note ("keep the hook title, the slams and the framing")
# told the user as their own instruction
_ATTRIBUTED = re.compile(
    r"(?i)\b(as you asked|as instructed|per your (?:trial )?(?:setup|instructions?|request)|"
    r"this turn's instructions|(?:were|was) set to stay|(?:were|was) locked|locked as part of|"
    r"your trial setup|as you wanted|you told me (?:to )?(?:keep|leave)|as requested|"
    r"the (?:moment|part|line|clip|section|bit) you (?:chose|picked|selected))\b")
_CAPABILITY = re.compile(
    r"(?i)(\bcanvas is fixed\b|\b(?:can't|cannot|couldn't|could not) (?:export|make|deliver|render) "
    r"(?:a |the )?(?:16:9|landscape|4:3|square)|\bonly (?:supports?|does|makes) 9:16\b"
    r"|\b(?:can't|cannot) change the (?:frame|aspect))")
# Valmera never picks music: an offer to choose a track for the user
_MUSIC_PICK = re.compile(
    "(?i)(\\b(?:choose|pick|select|escolh\\w*|elig\\w*|escoger)\\b[^.]{0,80}"
    "\\b(?:music|tracks?|songs?|soundtrack|trilhas?|faixas?|m\u00fasicas?|canci\u00f3n)\\b"
    "|\\b(?:music|tracks?|trilhas?|faixas?)\\b[^.]{0,60}\\b(?:for you|para voc\u00ea|cc0)\\b)")
_SNAKE = re.compile(r"\b[a-z]+_[a-z][a-z_]*\b")
_NAME = re.compile(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-z]{2,})\s+([A-Z][a-z]{2,})\b")
_NAME_OK = frozenset("""valmera studio first cut end card hook title pro plus max
    the this your our it its i and big type ken burns""".split())


def _invented_name(sent, facts):
    """A two-word proper name ("Sam Altman") of which a word appears nowhere
    in the transcript, the edit's own text or the user's words."""
    words = set(rg.transcript_words(facts.index or {}))
    if len(words) < 30:
        return None
    after = facts.after or {}
    # the edit's own words, its cutaways' titles and credits included (a
    # stock photo of "the Kansas City skyline" is named by its own title)
    own = set(rg._norm_words(json.dumps([after.get("texts"), after.get("motion"),
                                         after.get("vectors"), after.get("captions"),
                                         after.get("fx"), after.get("overlays")],
                                        default=str)))
    own |= set(rg._norm_words(facts.user_text or ""))
    for m in _NAME.finditer(sent):
        pair = [m.group(1).lower(), m.group(2).lower()]
        if any(w in _NAME_OK for w in pair):
            continue
        if any(w not in words and w not in own for w in pair):
            return m.group(0)
    return None


def sentences(text):
    out = []
    for para in re.split(r"\n\s*\n", (text or "").strip()):
        for line in para.split("\n"):
            line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", line.strip())
            line = re.sub(r"\*\*([^*]+)\*\*:?", r"\1", line)
            if not line:
                continue
            # a sentence ending inside its closing quote ('... "intelligence."
            # The hook ...') is two sentences too
            line = re.sub(r'([.!?\u2026]["\u201d\u2019)])\s+(?=[A-Z\u00c0-\u00dd"\u201c])', "\\1\n", line)
            for part in line.split("\n"):
                out.extend(s.strip() for s in rg.sentences(part) if s.strip())
    return out


_MARK = re.compile(r'["\u201c\u201d]')


def quotes(sent):
    """The quoted spans of a sentence, the marks paired in order (R2's
    pattern paired the closing mark of a short quote with the next opening
    one: ' and ending on ' read as a quote)."""
    marks = [m.start() for m in _MARK.finditer(sent)]
    return [sent[a + 1:b] for a, b in zip(marks[0::2], marks[1::2])]


def _quote_backed(q, words, own):
    toks = rg._norm_words(q)
    if len(toks) < 3:
        return True
    if " ".join(toks) in own:
        return True              # the edit's own title or caption text
    if rg.quote_in_transcript(q, words):
        return True
    # a stutter quoted with commas ("let me, let me" for "let me let let
    # me"): every word inside one short stretch of the transcript
    n = len(toks) + 3
    need = set(toks)
    for i in range(0, max(1, len(words) - n + 1)):
        if need <= set(words[i:i + n]):
            return True
    return False


def quote_unbacked(sent, facts):
    if not rg._SPEECH.search(sent):
        return None
    words = rg.transcript_words(facts.index or {})
    if not words:
        return None
    after = facts.after or {}
    own = " ".join(rg._norm_words(json.dumps(
        [after.get("texts"), after.get("captions"), after.get("vectors"),
         after.get("motion")], default=str)))
    for q in quotes(sent):
        if not _quote_backed(q, words, own):
            return q
    return None


_YEAR = re.compile(r"\b(19\d\d|20\d\d)\b")


def kept_words(facts):
    """The transcript words the edit keeps (source seconds inside its keep
    spans), lower-cased; all of them when the EDL has no spans."""
    words = [w for w in (facts.index or {}).get("words") or [] if isinstance(w, dict)]
    spans = []
    for k in (facts.after or {}).get("keep") or []:
        try:
            a, b = (k[0], k[1]) if isinstance(k, (list, tuple)) else (k["start"], k["end"])
            spans.append((float(a), float(b)))
        except (TypeError, ValueError, KeyError, IndexError):
            continue
    out = []
    for w in words:
        try:
            t = float(w.get("t0", w.get("s")))
        except (TypeError, ValueError):
            t = None
        if not spans or t is None or any(a - 0.05 <= t < b for a, b in spans):
            out.extend(rg._norm_words(str(w.get("w") or "")))
    return out


def year_unbacked(sent, facts):
    """A year the reply puts in the video that the video never says or
    shows (q8d talk10best: "the 2015 bet" on a cut whose card reads 2005 and
    whose kept speech never says 2015; 2015 was in the part left out)."""
    years = _YEAR.findall(sent)
    if not years:
        return None
    words = kept_words(facts)
    if len(words) < 30:
        return None
    after = facts.after or {}
    own = " ".join(rg._norm_words(json.dumps(
        [after.get("texts"), after.get("motion"), after.get("vectors"), after.get("fx")],
        default=str)))
    have = set(words)
    for y in years:
        if y not in have and y not in own:
            return y
    return None


def _length_claims(sent):
    vals = []
    for m in _LENGTH.finditer(rg.straight(sent)):
        v = m.group(1) or m.group(2)
        try:
            vals.append(float(v))
        except (TypeError, ValueError):
            continue
    return vals


def unbacked(sent, facts):
    """The reason this sentence is not backed by the turn's facts, or None."""
    s = rg.straight(sent)
    if _TRIAL_EXPORT.search(s) or rg.export_violations(s):
        return "export"
    if facts.wrote and _DENY.search(s):
        return "deny"
    if facts.previewed and _RENDER_DENY.search(s):
        return "render_deny"
    if _ATTRIBUTED.search(s) and not re.search(
            r"(?i)\b(keep|leave|don't|do not|same)\b", facts.user_text or ""):
        return "attributed"
    if _CAPABILITY.search(s):
        return "capability"
    if _invented_name(s, facts):
        return "name"
    if facts.duration_s and not rg._NOT_A_CLAIM.search(s):
        for v in _length_claims(s):
            # timestamps inside the video are not lengths: only a value near
            # the length scale is read as one (a 40 s cut is never "4 s")
            if v >= 0.3 * facts.duration_s and \
                    abs(v - facts.duration_s) > max(1.5, 0.08 * facts.duration_s):
                return "length"
    if facts.frame == "landscape" and _VERTICAL.search(s) and not \
            re.search(r"(?i)\b(not|never|instead of|rather than|isn't|stays?|kept)\b", s):
        return "shape"
    if facts.frame == "vertical" and _LANDSCAPE.search(s) and not \
            re.search(r"(?i)\b(source|original|from|was|not|instead of|rather than)\b", s):
        return "shape"
    if not facts.previewed and (_RENDER_CLAIM.search(s) or _PLACE.search(s)):
        return "render"
    layers = rg._claimed_layers(s)
    if layers:
        # a layer the user asked to CHANGE ("make the captions bigger"); a
        # request that only names it ("a reel with captions") is met by
        # its presence
        asked = {layer for layer, rx in rg._EFFECT_RX
                 if rx.search(rg.straight(facts.user_text or ""))} \
            if _CHANGE_ASK.search(facts.user_text or "") else set()
        for layer in sorted(layers):
            if not layer_present(facts.after or {}, layer):
                return f"absent:{layer}"
            changed = facts.wrote and rg.layer_changed(
                facts.before or {}, facts.after or {}, layer)
            if not changed and layer in asked:
                # "I made the captions bigger" for "make the captions
                # bigger" when the captions did not change this turn
                return f"unchanged:{layer}"
            # a layer the video has, unchanged, that the user did not ask
            # about: the editor describing its own earlier work ("I've made
            # it a vertical short" over the First cut's 9:16), not a lie
    if not facts.wrote and re.search(
            r"(?i)\b(?:i(?:'ve| have)?|we(?:'ve| have)?)\s+(?:also\s+|now\s+|just\s+)?"
            r"(?:cut|trimmed|removed|added|moved|cropped|reframed|tightened|shortened|"
            r"extended|restyled|changed|made|set|applied|replaced|swapped|fixed)\b", s) \
            and not rg._NOT_A_CLAIM.search(s):
        return "change"
    why = removal_unbacked(s, facts)
    if why:
        return why
    if year_unbacked(s, facts):
        return "year"
    if quote_unbacked(s, facts):
        return "quote"
    return None


_REMOVED = re.compile(
    r"(?i)(?:\b(?:i|we)(?:'ve| have)?\s+(?:also\s+|now\s+|just\s+)?|^\W*)"
    r"(cut|trimmed|removed|shortened|tightened|dropped|took out|deleted)\b(?P<obj>[^.;]{0,80})")
_OBJ_LAYER = (("zoom", r"zooms?|punch|push"), ("transition", r"transitions?|fades?|dissolves?"),
              ("text", r"titles?|slams?|cards?|graphics?|text"),
              ("captions", r"captions?|subtitles?"), ("music", r"music|songs?|soundtrack"),
              ("sfx", r"sound effects?|sfx|whoosh"), ("broll", r"b-?roll|cutaways?|photos?"))
_STATE_ONLY = re.compile(r"(?i)\b(already|first cut|earlier|before this)\b")


def removal_unbacked(sent, facts):
    """'Removed the first second.' on a turn whose cut did not change (R2's
    audit: only the titles changed): a first-person cut or removal must show
    in the layer it names (the cut itself, a zoom, a title, a transition)."""
    m = _REMOVED.search(sent)
    if not m or rg._NOT_A_CLAIM.search(sent) or _STATE_ONLY.search(sent):
        return None
    if not facts.wrote:
        return "change"
    obj = m.group("obj") or ""
    if re.match(r"(?i)^\s*(?:this|it|the (?:video|footage|talk|clip|interview|take|recording)"
                r"|(?:it )?down|(?:it )?(?:in)?to an? \d)\b", obj):
        # "I cut this to a 52-second short": the editor describing the
        # deliverable (its length is checked on its own), not this turn's cut
        return None
    layer = next((ly for ly, rx in _OBJ_LAYER
                  if re.search(r"(?i)\b(?:" + rx + r")\b", obj)), "cuts")
    if not rg.layer_changed(facts.before or {}, facts.after or {}, layer):
        return f"unchanged:{layer}"
    return None


_PRESENT = {
    "captions": ("captions", "texts"), "text": ("texts", "motion", "vectors", "captions"),
    "music": ("music",), "sfx": ("sfx",), "voiceover": ("voiceover",),
    "broll": ("inserts", "overlays", "fx", "patches"), "speed": ("speed",),
    "frame": ("frame", "canvas"), "loudness": ("master", "volume"),
    "transition": ("effects.transition", "effects.transitions", "effects.junctions"),
    "fade": ("effects.fade_in_s", "effects.fade_out_s", "motion", "texts"),
    "zoom": ("effects.zooms", "effects.zoom_paths", "effects.punch_ins", "motion", "fx",
             "frame.follow", "frame.focus_track"),
    "grade": ("effects.grade", "effects.grade_custom", "effects.lut", "fx"),
    "cuts": ("keep",),
}


def _on(v):
    if isinstance(v, dict):
        if v.get("mode") in ("off", "none") and not v.get("items"):
            return False
        return bool(v)
    if isinstance(v, str):
        return v not in ("", "none", "off", "cut")
    return bool(v)


def layer_present(edl, layer):
    """True when the video carries ``layer`` at all (a vertical source needs
    no frame to be vertical; "mastered" is the 9:16 default)."""
    if layer in ("frame", "loudness"):
        return True
    for path in _PRESENT.get(layer, ()):
        cur = edl
        for part in path.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
        if _on(cur):
            return True
    return False


_CHANGE_ASK = re.compile(
    r"(?i)\b(bigger|smaller|larger|bolder|thinner|thicker|higher|lower|brighter|darker|"
    r"louder|quieter|softer|faster|slower|shorter|longer|wider|narrower|colou?r|red|blue|"
    r"green|yellow|white|black|orange|purple|pink|gold|font|style|change|move|replace|"
    r"swap|remove|less|more|different|instead)\b")


def process_talk(sent, facts):
    """A sentence about the editor's own checking ("the final preview passed
    picture QC", "I checked the frames"): not something the user can see,
    and on the trial lane often not even run. Dropped, never counted as a
    false claim (the replays cannot tell whether a check ran)."""
    s = rg.straight(sent)
    return bool(_CHECK_CLAIM.search(s) and re.search(
        r"(?i)\b(preview|render|frames?|check|qc|review|picture|framing|crop|face|mix)\b", s)
        and not facts.checked)


_STAMP = re.compile(r"(?i)\b(?:at|around|near|about|from|by|to)?\s*\d{1,3}(?:\.\d+)?\s*"
                    r"(?:[-\u2013]\s*\d{1,3}(?:\.\d+)?\s*)?s\b|\b\d+:\d\d\b")
_DEFECT = re.compile(
    r"(?i)\b(edge|covers?|covering|hidden|hides|jump ?cuts?|flash(?:es)?|no caption|clip(?:s|ped)?|"
    r"tight|pops?|orphan|stutter|collision|overlaps?|bars?|blank|frozen|holds? still|"
    r"too close|cut off|mid-sentence|late|early|sags?|no designed beat|uncaptioned|"
    r"top|bottom|left|right|head sits|nears?)\b")
_LIST = re.compile(r"(?i)\b(two|three|four|several|a few|some) (?:things|items|issues|notes|"
                   r"spots|points|problems)\b|\bstill to improve\b|\bi'?d still (?:improve|change|fix)\b"
                   r"|\bopen items?\b|\bwhat i would still\b|\bstill off\b|\blimitations?\b"
                   r"|\bstill open\b|\bfalls? short\b|\bdoes not (?:yet )?meet\b")


def review_note(sent):
    """A reviewer's note about a defect at a moment of the video ("the face
    nears the left edge at 42.2 s"), or a list of what is still off: the
    review panel's business, not the reply's."""
    s = rg.straight(sent)
    return bool((_STAMP.search(s) and _DEFECT.search(s)) or _LIST.search(s))


def classify(sent, facts):
    why = unbacked(sent, facts)
    if why:
        return "unbacked", why
    s = rg.straight(sent)
    if _MUSIC_PICK.search(s):
        return "music_pick", None
    if _INTERNAL.search(s) or _SNAKE.search(s) or process_talk(sent, facts) \
            or re.search(r"(?i)\btrial note\b", s):
        return "internal", None
    if review_note(sent):
        return "review_note", None
    if _APOLOGY.search(s):
        return "apology", None
    if _OFFER.search(s) and not re.search(r"(?i)\b(i've|i have|i made|i cut|i added)\b", s):
        return "offer", None
    if _LIMIT.search(s):
        return "limit", None
    return "keep", None


# ── what changed, in the user's words ────────────────────────────────────

_FRIENDLY = (
    (re.compile(r"^cut ([\d.]+)s"), lambda m: f"tightened it by {float(m.group(1)):.0f} seconds"
     if float(m.group(1)) >= 1 else "tightened the cuts"),
    (re.compile(r"^restored ([\d.]+)s"), lambda m: f"brought back {float(m.group(1)):.0f} seconds"
     if float(m.group(1)) >= 1 else "eased the cuts"),
    (re.compile(r"^re-timed"), lambda m: "re-timed the cuts"),
    (re.compile(r"^turned the captions on"), lambda m: "added captions"),
    (re.compile(r"^turned the captions off"), lambda m: "took the captions off"),
    (re.compile(r"^restyled the captions"), lambda m: "restyled the captions"),
    (re.compile(r"^corrected caption"), lambda m: "fixed caption spellings"),
    (re.compile(r"^changed how the captions"), lambda m: "regrouped the captions"),
    (re.compile(r"^set the colou?r grade to (\w+)"), lambda m: f"gave it a {m.group(1)} colour grade"),
    (re.compile(r"^set a custom colou?r grade"), lambda m: "gave it a custom colour grade"),
    (re.compile(r"^added (\d+) zooms?"), lambda m: "added a push-in" if m.group(1) == "1"
     else f"added {m.group(1)} push-ins"),
    (re.compile(r"^removed (\d+) zooms?"), lambda m: "removed a zoom" if m.group(1) == "1"
     else f"removed {m.group(1)} zooms"),
    (re.compile(r"^added (\d+) (?:titles?|graphics?)"), lambda m: "added on-screen text"),
    (re.compile(r"^changed the motion graphics"), lambda m: "updated the titles"),
    (re.compile(r"^added (\d+) (?:inserts?|overlays?)"), lambda m: "added B-roll"),
    (re.compile(r"^set the frame to ([\d:]+)"), lambda m: f"reframed it to {m.group(1)}"),
    (re.compile(r"^set the transitions to (\w+)"), lambda m: "added transitions"),
    (re.compile(r"^changed the loudness"), lambda m: "levelled the sound"),
    (re.compile(r"^added (\d+) sound effects?"), lambda m: "added sound effects"),
    (re.compile(r"^added (\d+) music tracks?"), lambda m: "added your music"),
    (re.compile(r"^changed the look effects"), lambda m: "added B-roll" ),
)


def friendly_changes(before, after):
    items = []
    for c in rg.edl_changes(before or {}, after or {}):
        for rx, fn in _FRIENDLY:
            m = rx.match(c["text"])
            if m:
                phrase = fn(m)
                if phrase not in items:
                    items.append(phrase)
                break
    return items


def _join(items):
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def _shape_words(facts):
    bits = []
    if facts.duration_s:
        bits.append(f"{int(round(facts.duration_s))}-second")
    if facts.frame == "vertical":
        bits.append("vertical")
    return " ".join(bits)


def suggestion(facts):
    """One useful next step, from what the edit has and has not got."""
    after = facts.after or {}
    caps = after.get("captions")
    text = (facts.user_text or "").lower()
    if len(rg.transcript_words(facts.index or {})) < 8:
        # a site demo or a silent montage: nothing to caption
        if not (after.get("texts") or after.get("motion")):
            return "Want a title over the opening next?"
        return "Want it shorter, or a different title on it next?"
    if not caps or (isinstance(caps, dict) and caps.get("mode") in (None, "off", "none")
                    and not caps.get("items")):
        return "Want captions on it next?"
    if "caption" in text:
        return "Want a different caption colour, or a tighter cut next?"
    if not (after.get("texts") or after.get("motion")):
        return "Want a title over the opening next?"
    return "Want it tighter, or the captions in another colour next?"


_EN = frozenset("the a an and is it it's your you to of in on with now this that "
                "for are was cut video".split())
_LOCAL = {
    "pt": {"place": "Já está tocando no player.",
           "next": "Quer que eu deixe mais curto, ou mude a cor das legendas?"},
    "es": {"place": "Ya se reproduce en el reproductor.",
           "next": "¿Quieres que lo acorte o que cambie el color de los subtítulos?"},
}
_PT = frozenset("o a os as um uma de do da que não com para em é está vídeo corte".split())
_ES = frozenset("el la los las un una de del que no con para en es está vídeo corte".split())


def language(text):
    """'en', 'pt', 'es' or None (another language: nothing is added)."""
    words = re.findall(r"[a-záéíóúâêôãõçñ']+", (text or "").lower())
    if len(words) < 4:
        return "en"
    share = lambda vocab: sum(w in vocab for w in words) / float(len(words))
    en, pt, es = share(_EN), share(_PT), share(_ES)
    if en >= 0.12 or en >= max(pt, es):
        return "en"
    if pt >= es and pt >= 0.12:
        return "pt"
    if es >= 0.12:
        return "es"
    return None


def _an(text):
    return re.sub(r"\ba (?=(?:8|11|18|80|8\d)\b|(?:8|11|18)\d?-)", "an ", text)


_SUMMARY = re.compile(r"(?i)\b(cut|video|short|reel|highlight|clip|edit|demo|piece|version|"
                      r"footage|take)\b.*\b(is|now|ready|done|finished|runs?|made|built)\b|"
                      r"\b(?:is|it's|now)\b.{0,30}\b(cut|video|short|reel|highlight|clip|edit|demo|piece)\b|"
                      r"\b(done|ready|finished|i(?:'ve| have)? (?:cut|made|built|trimmed|turned|cleaned))\b")


# QI2 (30 Oct): pixel sizes and "1080p"-style resolutions. The preview the
# trial user watches is a 480p draft or a 720p approval render and the export
# is the source's size, so any such number in a reply is wrong for one of
# them (the long talk's reply: "a landscape 1920x1080 cut" over a 1280x720
# preview; both judges called it a false claim).
_PIXELS = re.compile(
    r"\s*(?:\(\s*)?\b(?:\d{3,4}\s?[x\u00d7]\s?\d{3,4}|\d{3,4}p)\b(?:\s*\))?",
    re.I)


# "on the right" is wrong on a phone, where the player sits above the chat
# (Q8 moved the First cut's message to "in the player"; the model still wrote
# "It's playing in the player on the right", QI2 matrix vlog r3)
_RIGHT = re.compile(r"(?i)\b(?:(?<=player )|(?<=preview ))on the right\b|"
                    r"\b(playing|is) on the right\b")


def _right(m):
    return f"{m.group(1)} in the player" if m.group(1) else ""


# an offer that only points back at other lines ("Those are the next fixes I
# can make", "These are things I can change"): never a next step of its own
_ORPHAN_OFFER = re.compile(r"(?i)^(those|these|they|both|either)\b")


def scrub_pixels(text):
    """(text without pixel sizes or "NNNNp" resolutions, and with "on the
    right" put as "in the player", how many removed)."""
    out, n = _PIXELS.subn("", text or "")
    out, r = _RIGHT.subn(_right, out)
    n += r
    if n:
        out = re.sub(r"[ \t]{2,}", " ", out)
        out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    return out, n


def finish(draft, facts):
    """(reply, report): the reply the trial user reads, and what was dropped
    and why (kept in the job result for the admin)."""
    report = {"dropped": [], "kept": 0}
    draft, pixels = scrub_pixels(draft)
    if pixels:
        report["dropped"].append({"why": "pixels", "sentence": f"{pixels} size(s)"})
    kept, offers, limits = [], [], []
    prev_dropped = False
    for sent in sentences(draft):
        kind, why = classify(sent, facts)
        prev_dropped_before = prev_dropped
        if kind in ("keep", "limit") and prev_dropped and re.match(
                r"(?i)^(they|these|those|this|that|both|either|it also)\b", sent):
            kind, why = "orphan", None
        prev_dropped = kind not in ("keep", "limit", "offer")
        if kind == "unbacked":
            report["dropped"].append({"why": why, "sentence": sent[:200]})
        elif kind == "offer":
            if _ORPHAN_OFFER.match(sent) or (prev_dropped_before and re.match(
                    r"(?i)^(this|that|it also)\b", sent)):
                # QI2: an offer that points back at dropped lines ("Those are
                # the next fixes I can make if you want them." after a review
                # note was dropped, QI2 matrix talk10 r3)
                report["dropped"].append({"why": "orphan", "sentence": sent[:200]})
            elif not _INTERNAL.search(rg.straight(sent)) and not review_note(sent) \
                    and not sent.rstrip().endswith(":"):
                offers.append(sent)
            else:
                report["dropped"].append({"why": "review_note", "sentence": sent[:200]})
        elif kind == "limit":
            limits.append(sent)
        elif kind == "keep":
            if sent.rstrip().endswith(":") or re.match(
                    r"(?i)^(this removes|separately|which|both|either)\b", sent):
                # a list's heading or a line about a dropped line
                report["dropped"].append({"why": "orphan", "sentence": sent[:200]})
            else:
                kept.append(sent)
        else:
            report["dropped"].append({"why": kind, "sentence": sent[:200]})
    for sent in limits[1:]:
        report["dropped"].append({"why": "limit", "sentence": sent[:200]})
    lang = language(draft)
    local = _LOCAL.get(lang or "", {})
    changes = friendly_changes(facts.before, facts.after) if facts.wrote else []
    # the lead: the draft's own summary of the video, else the EDL diff
    lead = next((k for k in kept if _SUMMARY.search(k)), None)
    rest = [k for k in kept if k is not lead]
    if lead is None and rest and lang != "en":
        lead, rest = rest[0], rest[1:]
    if lead is None:
        shape = _shape_words(facts)
        if facts.wrote and changes:
            lead = (f"Done: I {_join(changes[:3])}"
                    + (f", and it's now a {shape} cut." if shape else "."))
        elif facts.wrote:
            lead = "Done: your edit is updated" + (
                f", now a {shape} cut." if shape else ".")
        else:
            lead = ("Your cut stays as it is" + (f", a {shape} edit" if shape else "")
                    + ": it already does what you asked.")
        if lang != "en":
            lead = None if not rest else rest.pop(0)
    parts = [lead] if lead else []
    place = None
    if facts.previewed and not any(_PLACE.search(x) for x in parts + rest[:1]):
        place = "It's playing in the player." if lang == "en" else local.get("place")
    nxt = offers[0] if offers else (suggestion(facts) if lang == "en"
                                    else local.get("next"))
    budget = MAX_WORDS - sum(len(x.split()) for x in parts + [place or "", nxt or ""])
    for extra in rest[:1] + limits[:1]:
        if len(extra.split()) <= budget:
            parts.append(extra)
            budget -= len(extra.split())
        else:
            report["dropped"].append({"why": "length", "sentence": extra[:200]})
    for extra in rest[1:]:
        report["dropped"].append({"why": "length", "sentence": extra[:200]})
    if place:
        parts.append(place)
    if nxt:
        parts.append(nxt)
    report["kept"] = len(kept)
    return _an(" ".join(p for p in parts if p).strip()), report


# ── the worker's side ────────────────────────────────────────────────────

def facts_from_ctx(ctx, user_text=""):
    try:
        latest = ctx.latest_edl() or {}
    except Exception:  # noqa: BLE001
        latest = {}
    start = getattr(ctx, "turn_start_edl", None) or {}
    preview = getattr(ctx, "last_preview", None) or {}
    previewed = bool(preview) and str(preview.get("edl_version")) == str(latest.get("version"))
    after = latest.get("json") or {}
    frame = None
    try:
        import trial_turn
        words = trial_turn.shape_words(ctx) or ""
        frame = ("vertical" if words.startswith("vertical") else
                 "landscape" if words.startswith("landscape") else
                 "square" if words.startswith("square") else None)
    except Exception:  # noqa: BLE001
        frame = None
    dur = None
    if previewed:
        try:
            dur = float(preview.get("duration_s") or 0) or None
        except (TypeError, ValueError):
            dur = None
    if dur is None:
        try:
            import schemas
            dur = float(schemas.program_duration(after) or 0) or None
        except Exception:  # noqa: BLE001
            dur = None
    records = getattr(ctx, "verification_records", None) or {}
    rec = records.get(latest.get("version")) or {}
    return Facts(before=start.get("json") or {}, after=after,
                 wrote=bool(getattr(ctx, "versions_written", None)),
                 previewed=previewed, duration_s=dur, frame=frame,
                 index=getattr(ctx, "index", None) or {}, user_text=user_text,
                 checked=bool(rec) and not rec.get("unresolved_findings"))


def finish_for_ctx(ctx, draft, user_text=""):
    """finish() for a live trial turn; fail-open to the draft."""
    try:
        text, report = finish(draft, facts_from_ctx(ctx, user_text))
    except Exception as exc:  # noqa: BLE001 — never lose the reply
        print(f"[trial_reply] skipped: {type(exc).__name__}: {exc}", flush=True)
        return draft, None
    if report["dropped"]:
        print(f"[trial_reply] dropped {len(report['dropped'])}: "
              f"{[d['why'] for d in report['dropped']]}", flush=True)
    return text or draft, report
