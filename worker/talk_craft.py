"""Talking heads and long talks at the references' level (Q5, 30 Oct 2026).

What the calibrated judge kept saying about the trial branch's talking heads
(rubric v2, phone / phonehook / talk10best 7.5-7.9, the long talk 6.0):

  * the opening is a dependent clause or a vague fragment ("Since we're a
    trillionth of the sun's energy...", "THE ONE THING WE KNEW"): the
    references open on a complete claim or question at 0.0 s;
  * the ending lands on a list item or a trailing aside ("longevity being,
    you know, certainly one of them") instead of the payoff;
  * inner pauses of 0.45-0.85 s remain: whisper's words abut, so the speech
    gaps the pause cut reads hide the real silences the viewer hears;
  * a key-line slam sat over the mouth on a landscape talk cropped to 9:16.

This module holds the text rules (an opening a new viewer can follow, an
ending that lands) and the audio pause cut. Everything here runs only for
engine accounts (trial accounts and NEW subscribers: ``ctx.first_cut_q1``)
or on the trial lane; a grandfathered subscriber's First cut never reaches it
(owner rule D1; tests/test_q5_talk.py).
"""
import re
import subprocess

# --------------------------------------------------------------------------
#  The opening: a complete claim or question with no context needed
# --------------------------------------------------------------------------

# A sentence that opens on one of these is a dependent clause: the claim it
# leans on comes later or came before ("Since we're a trillionth of...").
SUBORDINATORS = frozenset((
    "since", "because", "cause", "cuz", "if", "unless", "when", "whenever",
    "although", "though", "while", "whilst", "whereas", "until", "till",
    "after", "before", "once", "whether", "as"))
# Lower-case continuations and relatives: never the first word heard.
CONTINUATIONS = frozenset((
    "which", "who", "whom", "whose", "where", "that", "than", "to", "of",
    "for", "with", "by", "from", "into", "about", "be", "been", "being",
    "will", "would", "is", "are", "was", "were"))
# Lead words the cut drops before the first word anyway (first_cut.shape_ends)
LEADS = frozenset((
    "and", "but", "so", "um", "umm", "uh", "uhm", "er", "erm", "ah", "okay",
    "ok", "well", "yeah", "yes", "like", "also", "alright", "right",
    "anyway", "anyways", "now", "oh"))
DISCOURSE = (("you", "know"), ("i", "mean"), ("you", "see"), ("kind", "of"),
             ("sort", "of"))


def _toks(text):
    return [t for t in (re.sub(r"[^a-z0-9']", "", w.lower())
                        for w in str(text or "").split()) if t]


def content_words(text):
    """The sentence's words past the lead words and discourse markers the
    cut drops (``first_cut.shape_ends`` / ``spoken_opening``)."""
    toks = _toks(text)
    changed = True
    while changed and toks:
        changed = False
        while toks and toks[0] in LEADS:
            toks.pop(0)
            changed = True
        if len(toks) >= 2 and (toks[0], toks[1]) in DISCOURSE:
            toks = toks[2:]
            changed = True
    return toks


def self_contained(text, least_words=4):
    """True when a new viewer can follow the sentence with no context: a
    complete claim or question, not a dependent clause, a continuation or
    a fragment. Judged on the words heard after the cut's lead words."""
    raw = str(text or "").strip().lstrip(" \"'“(")
    if not raw or raw[:1].islower():
        return False                       # the middle of a sentence
    toks = content_words(raw)
    if len(toks) < least_words:
        return False
    if toks[0] in SUBORDINATORS or toks[0] in CONTINUATIONS:
        return False
    if len(toks) > 2 and toks[0] == "i" and toks[1] in HEDGES \
            and toks[2] in SUBORDINATORS:
        return False                       # "I think if you're like..."
    if raw.rstrip()[-1:] not in ".?!…" and len(toks) < 8:
        return False                       # a trailing fragment
    return True


HEDGES = frozenset(("think", "guess", "believe", "feel", "mean", "suppose"))


def opening_row(rows, first, last, least_s=12.0):
    """The row a span should open on: ``first`` when it is a self-contained
    sentence start, else the next sentence start in the span that is, as
    long as ``least_s`` of the span stays. ``first`` when none qualifies."""
    try:
        i, j = rows.index(first), rows.index(last)
    except ValueError:
        return first
    end = float(last["t1"])
    for k in range(i, j + 1):
        r = rows[k]
        if end - float(r["t0"]) < least_s:
            break
        if k > 0 and k != i:
            prev = str(rows[k - 1].get("text") or "").rstrip(" \"'”)")
            if prev and prev[-1] not in ".?!…":
                continue                   # a row inside a running sentence
        if self_contained(r.get("text")):
            return r
    return first


# --------------------------------------------------------------------------
#  The ending: the payoff, never a list item or a trailing aside
# --------------------------------------------------------------------------

_TRAILING = re.compile(
    r"(?:\bone of (?:them|those|these|it)|\band so on|\band so forth|"
    r"\band (?:stuff|things|everything|all that|whatnot)|\bthis and that|"
    r"\bor (?:whatever|something|anything)|\bet cetera|\betc|"
    r"\b(?:kind|sort) of thing|\byou know|\bi guess|\bi think|\bor so|"
    r"\band stuff like that|\blike that)\W*$", re.I)
_SMALL_TALK = re.compile(
    r"^\W*(?:yes|yeah|yep|no|nope|right|exactly|totally|sure|thanks?|"
    r"thank you|okay|ok|cool|great|wow|haha|ha|hmm)\b", re.I)


def trailing(text):
    """A closing line a viewer hears as trailing off: a list item ("X being
    one of them"), an aside ("you know", "or whatever"), a question, small
    talk, or too short to land anything."""
    t = " ".join(str(text or "").split())
    if not t:
        return True
    if t[-1:] == "?":
        return True
    toks = _toks(t)
    if len(toks) < 4:
        return True
    if _SMALL_TALK.match(t) and len(toks) < 7:
        return True
    return bool(_TRAILING.search(t))


def lands(text):
    """A sentence that can close a talking piece: it ends (. or !), is not
    trailing, and is a complete thought."""
    t = " ".join(str(text or "").split())
    return bool(t) and t[-1:] in ".!" and not trailing(t)


def _ends(text):
    t = str(text or "").rstrip(" \"'\u201d)")
    return bool(t) and t[-1] in ".?!\u2026"


def sentence_text(rows, m):
    """The whole sentence row ``m`` closes: the transcript splits a long
    sentence into rows ("...you can possibly" / "think of."), so the rows
    before it that do not end a sentence are joined to it."""
    k = m
    while k > 0 and not _ends(rows[k - 1].get("text")):
        k -= 1
    return " ".join(str(r.get("text") or "").strip() for r in rows[k:m + 1])


def breath_after(index, rows, m, least_s=0.25):
    """Whether the speaker pauses after row ``m``: a gap to the next row,
    or a measured silence (the index's waveform silences) starting near
    its end (whisper's rows often abut where the audio has a pause)."""
    if m + 1 >= len(rows):
        return True
    end = float(rows[m]["t1"])
    if float(rows[m + 1]["t0"]) - end >= least_s:
        return True
    for a, b in (index or {}).get("silences") or []:
        try:
            a, b = float(a), float(b)
        except (TypeError, ValueError):
            continue
        if a - 0.6 <= end <= b + 0.1 and b - max(a, end - 0.6) >= least_s:
            return True
    return False


def closing_row(rows, first, last, least_s=12.0, breath=None):
    """The row a span should end on: ``last`` when its sentence lands,
    else the latest earlier row whose sentence lands (and, given
    ``breath(m)``, is followed by a breath) while ``least_s`` of the span
    stays. ``last`` when none."""
    try:
        i, j = rows.index(first), rows.index(last)
    except ValueError:
        return last
    start = float(first["t0"])

    def ok(m):
        if not _ends(rows[m].get("text")):
            return False
        return lands(sentence_text(rows, m)) and (breath is None or breath(m))
    if ok(j):
        return last
    # a pick that ends inside a running sentence ("...you can possibly")
    # first runs on to where that sentence ends, a few rows at most
    if not _ends(rows[j].get("text")):
        for k in range(j + 1, min(len(rows), j + 4)):
            if float(rows[k]["t1"]) - float(rows[j]["t1"]) > 8.0:
                break
            if _ends(rows[k].get("text")):
                if ok(k):
                    return rows[k]
                break
    for m in range(j - 1, i - 1, -1):
        if float(rows[m]["t1"]) - start < least_s:
            break
        if ok(m):
            return rows[m]
    return last


# --------------------------------------------------------------------------
#  Prompt rules (the scout's own pick, before the guards above)
# --------------------------------------------------------------------------

OPENING_RULE = (
    "OPENING: the first sentence must work as a hook ON ITS OWN: a complete "
    "claim or a direct question a stranger understands with no context. "
    "Never start on a sentence that opens with a subordinate clause "
    "(\"Since...\", \"Because...\", \"If...\", \"When...\", \"As...\"), on "
    "a fragment, or on a line that leans on what came before (\"this\", "
    "\"that\", \"it\" with nothing to point at); start LATER and drop the "
    "setup when the first lines are not such a hook. Prefer a short, "
    "striking first sentence. ")
ENDING_RULE = (
    "ENDING: end on the PAYOFF sentence: the answer, the conclusion, the "
    "strongest claim, the line a viewer would quote. Never end on a list "
    "item or an example tacked on after the point (\"X being one of "
    "them\"), an aside (\"you know\", \"or whatever\", \"this and that\"), "
    "a question, a thank-you or small talk: end EARLIER, on the payoff, "
    "even when the speaker keeps talking. ")
BROLL_DENSITY_RULE = (
    "On a talking head name a B-roll moment for about every 3 seconds of "
    "the span (the references cut away to footage for 30-60% of a talking "
    "head): every concrete noun, place, person, object or action is a "
    "candidate, and a clear visual metaphor for an abstract line counts "
    "(\"magnificent\" -> \"a city skyline at night\"). ")
HOOK_RULE = (
    "HOOK TITLE: the 2-5 word poster title is itself a self-contained "
    "claim or topic a stranger gets at a glance (\"AI will *solve* "
    "everything\", \"A *million* times more power\"), never a dependent "
    "clause, never vague (\"the one thing we knew\"). ")


# --------------------------------------------------------------------------
#  Pauses the viewer hears (audio), not only the gaps between words
# --------------------------------------------------------------------------

PAUSE_MAX_S = 0.36         # a silence longer than this is cut (judge: > 0.4)
PAUSE_KEEP_S = 0.16        # what a cut pause keeps (a breath)
LEAD_KEEP_S = 0.03         # silence before the first word (refs: 0.0 s)
TAIL_MAX_S = 0.6           # live tail after the last word (refs 0.2-0.7 s)
TAIL_KEEP_S = 0.45
FRAME_S = 0.02             # analysis hop
MASTER_LUFS = -14.5        # the program is mastered to about this
JUDGE_SILENCE_DB = -32.0   # the judge: silencedetect at -32 dB on the master
MARGIN_DB = 1.5            # a little stricter than the judge's own line
VOICED_DB = 12.0           # this far over the line is the voice itself
ONSET_BACK_S = 0.3         # an opening reaches back this far for its sound
_RATE = 16000


def _audio(path, timeout=240):
    """Mono 16 kHz float samples of the source's audio, or None."""
    import numpy as np
    try:
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-nostdin", "-i", path, "-vn", "-ac", "1",
             "-ar", str(_RATE), "-f", "s16le", "-"], capture_output=True,
            timeout=timeout).stdout
    except Exception:
        return None
    a = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    return a if len(a) > _RATE else None


def frame_peaks(samples):
    """dBFS of each FRAME_S frame's PEAK (silencedetect compares samples,
    not an average, so a peak is the comparable measure)."""
    import numpy as np
    hop = int(_RATE * FRAME_S)
    n = len(samples) // hop
    a = np.abs(samples[:n * hop].reshape(n, hop)).max(axis=1)
    return 20.0 * np.log10(a + 1e-9)


def loudness(samples):
    """Integrated loudness estimate (LUFS-like, ungated RMS of the speech
    frames: the loudest 60%), enough to place the judge's silence line."""
    import numpy as np
    hop = int(_RATE * 0.4)
    n = len(samples) // hop
    if n < 3:
        return None
    blocks = samples[:n * hop].reshape(n, hop)
    ms = (blocks * blocks).mean(axis=1)
    ms.sort()
    top = ms[int(0.4 * n):]
    return float(10.0 * np.log10(top.mean() + 1e-12) - 0.691)


def silence_line(samples):
    """The source dBFS under which a frame is silence ON THE MASTER: the
    judge's -32 dB moved by the mastering gain."""
    lufs = loudness(samples)
    if lufs is None:
        return None
    return JUDGE_SILENCE_DB - (MASTER_LUFS - lufs) + MARGIN_DB


def program_cuts(keep, quiet, frame_s=FRAME_S, least_s=PAUSE_MAX_S,
                 keep_s=PAUSE_KEEP_S, lead_s=LEAD_KEEP_S, tail_max=TAIL_MAX_S,
                 tail_keep=TAIL_KEEP_S, loud=None):
    """The new keep list once every silence the VIEWER hears is cut: on the
    program's own timeline (a pause that spans a cut, the tail of one kept
    piece plus the head of the next, counts as one), each silent run longer
    than ``least_s`` keeps ``keep_s`` (its middle goes), the opening keeps
    ``lead_s`` before the first sound and the ending at most ``tail_max``
    (else ``tail_keep``). ``quiet(src_frame_index) -> bool``."""
    frames = []                         # program order: source frame index
    for s, e in keep:
        a, b = int(round(s / frame_s)), int(round(e / frame_s))
        frames.extend(range(a, max(a, b)))
    n = len(frames)
    if n < 10:
        return [list(map(float, k)) for k in keep]
    q = [quiet(f) for f in frames]
    drop = [False] * n
    # the speech itself: before its first clearly voiced frame is the lead
    # (room noise blips included), after its last one the tail
    voiced = [bool(loud(f)) if loud else not q[j] for j, f in enumerate(frames)]
    first = next((j for j in range(n) if voiced[j]), None)
    last = next((j for j in range(n - 1, -1, -1) if voiced[j]), None)
    if first is None:
        return [list(map(float, x)) for x in keep]
    for j in range(0, max(0, first - int(round(lead_s / frame_s)))):
        drop[j] = True
    if (n - 1 - last) * frame_s > tail_max:
        for j in range(last + 1 + int(round(tail_keep / frame_s)), n):
            drop[j] = True
    for j in range(0, first):
        q[j] = False
    for j in range(last + 1, n):
        q[j] = False
    k = 0
    while k < n:
        if not q[k]:
            k += 1
            continue
        r0 = k
        while k < n and q[k]:
            k += 1
        r1 = k
        span = (r1 - r0) * frame_s
        if span > least_s:
            half = int(round(keep_s / 2.0 / frame_s))
            for j in range(r0 + half, r1 - half):
                drop[j] = True
    out = []
    for j, f in enumerate(frames):
        if drop[j]:
            continue
        if out and f == out[-1][1]:
            out[-1][1] = f + 1
        else:
            out.append([f, f + 1])
    # pieces shorter than 0.08 s are noise in a keep list: fold them away
    out = [[a * frame_s, b * frame_s] for a, b in out if (b - a) * frame_s >= 0.08]
    return [[round(a, 3), round(b, 3)] for a, b in out]


def cut_audio_pauses(ctx, steps, started, run):
    """Cut every silence the viewer would hear as a pause (over PAUSE_MAX_S,
    including one that spans a cut) down to a breath, the silence before the
    first word to LEAD_KEEP_S and a tail over TAIL_MAX_S to TAIL_KEEP_S,
    measured on the source's audio against the judge's own line (-32 dB on
    the master). One keep write with exact times (every cut lies in measured
    silence, so no word is clipped). None when nothing changes."""
    try:
        edl = ctx.latest_edl()["json"]
        keep = [[float(a), float(b)] for a, b in edl.get("keep") or []]
        path = ctx.proxy_path()
    except Exception:
        return None
    if not keep or not path:
        return None
    cache = getattr(ctx, "_q5_audio", None)
    if cache is None:
        samples = _audio(path)
        if samples is None:
            return None
        line = silence_line(samples)
        cache = (frame_peaks(samples), line)
        try:
            ctx._q5_audio = cache
        except Exception:
            pass
    peaks, line = cache
    if line is None:
        return None
    n = len(peaks)

    def quiet(f):
        return 0 <= f < n and peaks[f] < line

    def loud(f):
        return 0 <= f < n and peaks[f] >= line + VOICED_DB
    # an opening that clips its first sound (whisper's word starts late:
    # "start 17.79 clips the onset of 'what'") reaches back to the silence
    # before it, at most ONSET_BACK_S
    f0 = int(round(keep[0][0] / FRAME_S))
    back = 0
    while back < int(ONSET_BACK_S / FRAME_S) and f0 - back - 1 >= 0 \
            and not quiet(f0 - back - 1):
        back += 1
    if back and f0 - back - 1 >= 0 and quiet(f0 - back - 1):
        keep = [list(k) for k in keep]
        keep[0][0] = round((f0 - back) * FRAME_S, 3)
    new = program_cuts(keep, quiet, loud=loud)
    removed = sum(b - a for a, b in keep) - sum(b - a for a, b in new)
    print(f"[talk_craft] audio pauses: line {line:.1f} dBFS, "
          f"{removed:.2f}s of silence cut, opening reached back "
          f"{back * FRAME_S:.2f}s", flush=True)
    if not new or (removed < 0.05 and not back):
        return None
    return run(ctx, steps, "keep_segments",
               {"segments": new, "snap_to_words": False}, started)


# --------------------------------------------------------------------------
#  The hook title on a talking head
# --------------------------------------------------------------------------

def _title_ok(text):
    """A poster title that reads as a claim or a topic on its own: not a
    dependent clause, a continuation or a dangling function word."""
    toks = content_words(str(text or "").replace("*", ""))
    if len(toks) < 2:
        return False
    if toks[0] in SUBORDINATORS or toks[0] in CONTINUATIONS:
        return False
    return toks[-1] not in _DANGLING


_DANGLING = frozenset((
    "the", "a", "an", "of", "to", "and", "in", "on", "for", "with", "by",
    "our", "your", "my", "their", "is", "are", "was", "were", "be", "that",
    "this", "it", "if", "so", "but", "or", "at", "as", "from", "using",
    "about", "than", "very", "really", "just"))


def title_choice(opening, clause, scout_hook, hold_s=3.0, more=()):
    """(text, end_s) of the opening title: the spoken opening when it is a
    whole clause that reads on its own (the references set the line heard
    as poster type); else the scout's 2-5 word hook when it does (a topic
    title, R10's "HOOK STACKING" over "Have you ever heard of hook
    stacking?"), held ``hold_s``; else the spoken opening as it was."""
    if opening and clause and _title_ok(opening[0]) and \
            self_contained(opening[0] + ".", least_words=2):
        return opening
    for alt in [scout_hook] + list(more or ()):
        if alt and _title_ok(alt):
            print(f"[talk_craft] title: {alt!r} (spoken opening "
                  f"{opening[0] if opening else None!r})", flush=True)
            return alt, hold_s
    if opening and clause and _title_ok(opening[0]):
        return opening
    # the first words of a longer clause are a fragment ("I THINK IF
    # YOU'RE LIKE", judged vague): no title beats a fragment title
    return None


# --------------------------------------------------------------------------
#  Slams clear of the mouth (measured on the window, through the crop)
# --------------------------------------------------------------------------

SLAM_GAP = 0.035           # the slam's ink starts this far under the chin
SAFE_BOTTOM = 0.80         # the 9:16 platform safe area's floor
SLAM_MIN_SCALE = 0.6       # a slam is never set smaller than this


def _on_card(edl, a, b):
    for v in edl.get("vectors") or []:
        try:
            if float(v.get("width") or 0) >= 0.99 and float(v.get("height") or 0) >= 0.99 \
                    and float(v.get("opacity") or 1) >= 0.95 \
                    and float(v["start"]) <= a + 0.05 and float(v["end"]) >= b - 0.05:
                return True
        except (KeyError, TypeError, ValueError):
            continue
    return False


def chin_line(ctx, edl, a, b):
    """The lowest chin (output y fraction) of the faces seen over program
    window [a, b], measured on exact source frames through the crop and
    the camera (keepout.face_track); None when no face is seen."""
    import keepout
    import motion_tools
    try:
        W, H = motion_tools._canvas_size(ctx, edl)
        track = keepout.face_track(edl, ctx.index or {}, W, H, a, b,
                                   measure=motion_tools._face_measure(ctx))
    except Exception:  # noqa: BLE001
        return None
    chins = [max(float(f[3]) for f in faces) for _t, faces in track if faces]
    if not chins:
        return None
    chins.sort()
    # the 90th percentile: a nod is seen, a single bad detection is not
    return chins[min(len(chins) - 1, int(0.9 * len(chins)))]


def _good(res):
    """A tool result that changed the edit (first_cut._ok; the trial turn's
    Steps.run returns None on failure)."""
    import first_cut
    return first_cut._ok(res)


def clear_slams(ctx, steps, started, run, cards=True):
    """Every word slam on the face's own picture (not on a card) starts
    SLAM_GAP under the chin over its whole window: moved down, else set
    smaller, else (``cards``) put on a full-frame card, else removed.
    Returns [(id, what)]."""
    out = []
    try:
        edl = ctx.latest_edl()["json"]
    except Exception:
        return out
    slams = [m for m in edl.get("motion") or [] if isinstance(m, dict)
             and m.get("template") == "word_slam" and m.get("start") is not None]
    for m in slams:
        try:
            edl = ctx.latest_edl()["json"]
            a, b = float(m["start"]), float(m["end"])
        except Exception:
            continue
        if _on_card(edl, a, b):
            continue
        cur = next((x for x in edl.get("motion") or [] if x.get("id") == m.get("id")), None)
        box = ((cur or {}).get("footprint") or {}).get("box")
        if not cur or not box:
            continue
        chin = chin_line(ctx, edl, a, b)
        if chin is None:
            continue
        top, bot = float(box[1]), float(box[3])
        if top >= chin + SLAM_GAP:
            continue
        params = dict(cur.get("params") or {})
        y = float(params.get("y") or (top + bot) / 2.0)
        h = bot - top
        want_top = chin + SLAM_GAP
        what = None
        if want_top + h <= SAFE_BOTTOM:
            res = run(ctx, steps, "set_motion_graphic", {
                "id": cur["id"], "params": {"y": round(y + (want_top - top), 3)},
                "purpose": "the slam clear of the mouth"}, started)
            what = "lowered" if _good(res) else None
        else:
            scale = (SAFE_BOTTOM - want_top) / max(h, 1e-6)
            if scale >= SLAM_MIN_SCALE:
                width = float(params.get("width") or 0.85) * scale
                ny = want_top + h * scale / 2.0 + (y - (top + bot) / 2.0) * scale
                res = run(ctx, steps, "set_motion_graphic", {
                    "id": cur["id"], "params": {"y": round(ny, 3),
                                                "width": round(width, 3)},
                    "purpose": "the slam clear of the mouth"}, started)
                what = "smaller" if _good(res) else None
        if what:
            edl2 = ctx.latest_edl()["json"]
            now = next((x for x in edl2.get("motion") or [] if x.get("id") == cur["id"]), None)
            nb = ((now or {}).get("footprint") or {}).get("box")
            if nb and float(nb[1]) < chin + SLAM_GAP - 0.01:
                what = None            # the keep-out put it back: not clear
        if not what and cards:
            from first_cut import BEAT_CARD
            vres = run(ctx, steps, "add_vector_graphic", {
                "kind": "rectangle", "start": round(a, 3), "end": round(b, 3),
                "x": 0.5, "y": 0.5, "width": 1.0, "height": 1.0,
                "color": BEAT_CARD, "opacity": 1.0}, started)
            if _good(vres):
                run(ctx, steps, "set_motion_graphic", {
                    "id": cur["id"], "params": {"y": 0.5, "role": "serif"},
                    "allow_face_overlap": True,
                    "purpose": "the slam on its own card"}, started)
                what = "carded"
        if not what:
            if _good(run(ctx, steps, "remove_motion_graphic", {"id": cur["id"]},
                         started)):
                what = "removed"
        if what:
            out.append((cur["id"], what))
    if out:
        print(f"[talk_craft] slams clear of the mouth: {out}", flush=True)
    return out


# --------------------------------------------------------------------------
#  A long talk in its own shape: a camera that changes every ~3 s
# --------------------------------------------------------------------------

LONG_CUT_TARGET_S = 3.0     # a framing lasts about this long (refs 2-3 s)
LONG_CUT_MAX_S = 4.5
LONG_LEVELS = (0.25, 0.42)  # medium 1.25x, close 1.42x (wide is the take)


def _shot_bounds(index, tl):
    """Program seconds where the SOURCE cuts to another shot (a zoom pinned
    on one shot's face must not carry across it)."""
    out = []
    for sh in (index or {}).get("shots") or []:
        try:
            t = tl.src_to_out(float(sh["start"]))
        except (KeyError, TypeError, ValueError):
            continue
        if t is not None:
            out.append(float(t))
    return sorted(out)


def largest_face(faces):
    faces = [f for f in faces or [] if len(f) >= 4]
    if not faces:
        return None
    return max(faces, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))


def landscape_cut_ins(ctx, steps, started, run, busy, hook_end,
                      target=LONG_CUT_TARGET_S, hi=LONG_CUT_MAX_S,
                      levels=LONG_LEVELS):
    """A closer camera on the same take at phrase turns, every ~3 s, on a
    LANDSCAPE program: each close framing is aimed at the face measured on
    that exact source frame (a two-person source shows a different face per
    shot; the index's 8 s samples miss most of them), never carried across a
    source shot change, and skipped where no face is seen. Hard cuts in and
    out (ramp 0), medium and close alternating. Returns how many landed."""
    import device_planner
    import first_cut_visual as fcv
    import motion_tools
    import schemas
    from timeline import Timeline
    try:
        edl = ctx.latest_edl()["json"]
        prog = float(schemas.program_duration(edl))
        tl = Timeline(edl.get("keep") or [], [], None)
        words = device_planner.program_words(edl, ctx.index or {}, tl)
    except Exception:
        return 0
    if not words or prog < 8:
        return 0
    measure = motion_tools._face_measure(ctx, cache_attr="_q5_faces", cap=400)
    bounds = _shot_bounds(ctx.index, tl)
    turns = fcv.phrase_points(words) + [round(b, 3) for _a, b in busy]
    onsets = [round(float(w["t0"]) - 0.02, 3) for w in words]
    points = sorted(set(turns + onsets))
    segs = fcv.plan_cut_ins(points, busy, max(hook_end, 1.0), prog,
                            target=target, hi=hi, preferred=turns)
    n = tried = 0
    for a, b, close in segs:
        if not close:
            continue
        inside = [t for t in bounds if a + 0.05 < t < b - 0.05]
        if inside:
            b = inside[0]
        if b - a < 1.2:
            continue
        src = tl.out_to_src((a + b) / 2.0)
        if src is None:
            continue
        tried += 1
        face = largest_face(measure(src) if measure else None)
        if face is None:
            continue
        cx = round(min(max((face[0] + face[2]) / 2.0, 0.2), 0.8), 3)
        cy = round(min(max((face[1] + face[3]) / 2.0, 0.25), 0.65), 3)
        res = run(ctx, steps, "add_zoom", {
            "start": round(a, 2), "end": round(b, 2),
            "strength": levels[n % len(levels)], "mode": "punch",
            "ramp_s": 0.0, "cx": cx, "cy": cy,
            "purpose": "a closer camera on the same take, cut in on a phrase "
                       "turn (references: the framing changes every 2-3 s)"},
            started)
        if _good(res):
            n += 1
    print(f"[talk_craft] landscape cut-ins: {n} of {tried} close framings "
          f"({len(segs)} segments)", flush=True)
    return n


CHAPTER_S = 1.5             # a chapter card's time on screen


def chapter_cards(ctx, steps, started, run, starts, titles, hook_end,
                  avoid=()):
    """A chapter card where each kept stretch of a long talk begins (after
    the first, which the hook title opens): a full-frame card with the
    stretch's 2-4 word title in the slams' type (references: "Step 1",
    "no. 1" chapter devices), over the speaker's first words, which run
    on. ``starts``: source seconds; returns the program windows placed."""
    import first_cut
    from timeline import Timeline
    try:
        keep = ctx.latest_edl()["json"].get("keep") or []
        tl = Timeline(keep, [], None)
    except Exception:
        return []
    placed = []
    for src_t, title in zip(starts, titles):
        text = " ".join(str(title or "").split())
        if not text or len(text.replace("*", "").split()) > 5:
            continue
        a = tl.src_to_out(float(src_t) + 0.01)
        if a is None:
            # the stretch's first words were trimmed (a lead silence, a
            # filler): the card opens where its kept speech starts
            nxt = [float(x) for x, _y in keep if float(src_t) <= float(x) <= float(src_t) + 3.0]
            a = tl.src_to_out(nxt[0] + 0.01) if nxt else None
        if a is None or a < hook_end + 2.0:
            continue
        a = round(max(0.0, a - 0.01), 2)
        b = round(a + CHAPTER_S, 2)
        if any(a < y + 0.3 and b > x - 0.3 for x, y in list(avoid) + placed):
            continue
        if "*" not in text:
            text = first_cut._star(text)
        before = {v.get("id") for v in
                  ctx.latest_edl()["json"].get("vectors") or []}
        if not _good(run(ctx, steps, "add_vector_graphic", {
                "kind": "rectangle", "start": a, "end": b, "x": 0.5, "y": 0.5,
                "width": 1.0, "height": 1.0, "color": first_cut.BEAT_CARD,
                "opacity": 1.0}, started)):
            continue
        card_id = next((v.get("id") for v in
                        ctx.latest_edl()["json"].get("vectors") or []
                        if v.get("id") not in before), None)
        res = run(ctx, steps, "add_motion_graphic", {
            "template": "word_slam", "start": a, "end": b,
            "params": {"text": text, "tier": "display", "entrance": "pop",
                       "exit": "cut", "accent": first_cut.BEAT_ACCENT,
                       "width": 0.7, "role": "serif", "y": 0.5},
            "allow_face_overlap": True,
            "purpose": "a chapter card where the next part of the talk begins"},
            started)
        if _good(res):
            placed.append((a, b))
        elif card_id:
            run(ctx, steps, "remove_vector_graphic", {"id": card_id}, started)
    return placed


PAYOFF_FACE_S = 4.0        # the closing seconds stay on the speaker


def last_sentence_start(ctx):
    """Program second where the closing sentence begins (the payoff stays
    on the speaker's face: no cutaway over it), or None."""
    import device_planner
    try:
        words = device_planner.program_words(ctx.latest_edl()["json"],
                                             ctx.index or {})
    except Exception:
        return None
    if len(words) < 4:
        return None
    start = None
    for k, w in enumerate(words[:-1]):
        if k == 0 or _ends(words[k - 1].get("w")):
            start = float(w["t0"])
    return start


# --------------------------------------------------------------------------
#  A source that already carries captions
# --------------------------------------------------------------------------

BURNED_SHARE = 0.5          # samples with centred caption-like text


def burned_captions(index):
    """True when the source already has captions burned in (an OpusClip or
    CapCut export): a wide line of text centred across the frame, in the
    caption zone, in most of the index's spatial samples. Adding running
    captions or slams on top doubles them (the judges' hard fault)."""
    samples = ((index or {}).get("spatial") or {}).get("samples") or []
    if len(samples) < 6:
        return False

    def caption_like(b):
        try:
            x0, y0, x1, y1 = (float(v) for v in b[:4])
        except (TypeError, ValueError):
            return False
        return (x1 - x0) >= 0.25 and abs((x0 + x1) / 2.0 - 0.5) <= 0.08 \
            and 0.35 <= (y0 + y1) / 2.0 <= 0.9
    hits = sum(1 for s in samples
               if any(caption_like(b) for b in s.get("text") or []))
    return hits / float(len(samples)) >= BURNED_SHARE


# --------------------------------------------------------------------------
#  A phrase said twice ("I think I think")
# --------------------------------------------------------------------------

PHRASE_GAP_S = 1.2


def phrase_repeats(words, keep, most=3):
    """[(t0, t1)] source spans of the FIRST of two identical 2-3 word runs
    said back to back ("I think I think", "it was it was"), inside the kept
    program; the span runs up to the repeat's start. First-cut stutters
    (one short word twice) are first_cut.stutter_spans'."""
    toks = []
    for w in words or []:
        try:
            toks.append((re.sub(r"[^a-z0-9']", "", str(w.get("w") or "").lower()),
                         float(w["t0"]), float(w["t1"])))
        except (KeyError, TypeError, ValueError):
            continue
    out = []
    k = 0
    while k < len(toks):
        hit = None
        for n in range(most, 1, -1):
            a, b = toks[k:k + n], toks[k + n:k + 2 * n]
            if len(b) < n or not all(x[0] for x in a):
                continue
            if [x[0] for x in a] == [x[0] for x in b] \
                    and b[0][1] - a[-1][2] < PHRASE_GAP_S:
                hit = (a[0][1], round(b[0][1] - 0.01, 3), n)
                break
        if hit and any(s <= hit[0] and hit[1] <= e for s, e in keep):
            out.append(hit[:2])
            k += hit[2]
        else:
            k += 1
    return out


def cut_phrase_repeats(ctx, steps, started, run):
    """One keep write cutting each repeated phrase once; None when none."""
    try:
        keep = [[float(a), float(b)] for a, b in ctx.latest_edl()["json"].get("keep") or []]
    except Exception:
        return None
    spans = phrase_repeats((ctx.index or {}).get("words") or [], keep)
    if not spans:
        return None
    new = []
    for s0, e0 in keep:
        cur = s0
        for a, b in sorted(x for x in spans if s0 <= x[0] and x[1] <= e0):
            if a - cur >= 0.08:
                new.append([round(cur, 3), round(a, 3)])
            cur = max(cur, b)
        if e0 - cur >= 0.08:
            new.append([round(cur, 3), round(e0, 3)])
    if not new or new == keep:
        return None
    print(f"[talk_craft] phrase repeats cut: {len(spans)}", flush=True)
    return run(ctx, steps, "keep_segments",
               {"segments": new, "snap_to_words": False}, started)
