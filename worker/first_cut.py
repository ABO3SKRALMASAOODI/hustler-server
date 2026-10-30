"""First cut: one finished result, made automatically, deterministically.

PLAN.md P2 as corrected by C2.4 and owner decision D2 (25 Oct 2026): on an
admitted project's first upload (or a "Record my website" URL) Valmera makes
one finished vertical result with no agent loop. The in-app loop's median is
13.6 min (p90 78.6); this chain is a fixed list of the editor's own tools:

    video ≤ 90 s:  (speech) ONE scout call trims the opening to the first
                   sentence a viewer should hear and the ending to the last
                   complete one, and writes the hook title (a deterministic
                   trim without the lane); cut_silences 0.4 s,
                   remove_filler_words, the ends shaped (no leading
                   "And/um", 0.5 s of live picture after the last word);
                   auto_reframe 9:16 (non-vertical, never a screen
                   recording), the source's own static bands cropped,
                   alternating framing steps on visible jump cuts, the hook
                   title over the first 2.6 s, captions at the chest
    video > 90 s:  the scout picks the best 20-60 s story in the first
                   30 min (a deterministic speech-density pick without it),
                   keep_segments to it, then the same chain
    URL:           record_website_demo with the automatic tour (points at
                   the call to action, opens the main sections, ends live),
                   showcase_demo, the site's name and promise as the hook
                   title, one approval render

The craft targets are the owner's references
(.valmera/week-1029/REFERENCE_PLAYBOOK.md, 29 Oct 2026).

Caps: FIRST_CUT_MAX_S of work (8 min), FIRST_CUT_MAX_RENDERS renders, the
first FIRST_CUT_MAX_SOURCE_S (30 min) read by the scout. When time runs short
the remaining edits are skipped and what is already applied is rendered: the
fallback is the clean cut. Nothing is charged (the user did not ask for it);
the model spend is recorded as ``trial_usd`` against the trial budget.

The job is an ``agent_turn`` row with payload ``first_cut: true`` (the job
type constraint is not widened), enqueued by the index completion
(indexer._finish_setup) or by the backend for a URL. claim_job already holds
an agent turn behind its project's index.
"""
import json
import os
import re
import time

import agent_tools
import config
import db as dbx
import llm
import model_prices

_URL = re.compile(r"https?://[^\s<>\"')]+", re.I)
_FAILED = ("REJECTED", "CORRECTION", "FAILED", "TRANSIENT", "UNAVAILABLE",
           "UNSAFE", "PREREQUISITE", "COULD NOT", "REQUEST BUDGET",
           # not a failure, but nothing was applied, so it is never claimed
           "NO CHANGE")
# Leave this much of the time cap for the one render.
_RENDER_RESERVE_S = 150.0


def _failed(result):
    head = str(result or "").lstrip().upper()
    return head.startswith(_FAILED)


# A step that ran but changed nothing ("No filler words ... were found, so
# nothing was removed") is never claimed in the reply.
_NOTHING = re.compile(r"\bnothing (?:was|is) (?:removed|changed|cut|applied)\b"
                      r"|^no [a-z ]+ (?:were|was) found\b", re.I)


def _ok(result):
    """A step that ran, did not fail and changed something (a skipped step
    returns None)."""
    return (result is not None and not _failed(result)
            and not _NOTHING.search(str(result).strip()))


_CORNER = re.compile(r"GRAPHIC ON THE CORNER MARK: '([^']+)'")


def _clear_corner_mark(ctx, steps, render):
    """The render's PICTURE QC named a graphic drawn into the corner mark's
    box (the brand slot stays clear, always). Remove those items; returns
    True when something was removed and the cut needs one more render."""
    ids = list(dict.fromkeys(_CORNER.findall(str(render or ""))))
    removed = False
    typesets = [i for i in ids if i.startswith("ts")]
    if typesets:
        removed |= _ok(_run(ctx, steps, "remove_typeset", {"ids": typesets},
                            time.monotonic()))
    for gid in ids:
        if gid.startswith("mg"):
            removed |= _ok(_run(ctx, steps, "remove_motion_graphic",
                                {"id": gid}, time.monotonic()))
        elif gid.startswith("t") and not gid.startswith("ts"):
            removed |= _ok(_run(ctx, steps, "remove_text", {"id": gid},
                                time.monotonic()))
    return removed


def url_in(text):
    match = _URL.search(str(text or ""))
    return match.group(0).rstrip(".,;:") if match else None


# --------------------------------------------------------------------------
#  Trial state, read on the worker side. The admission is track B's
#  (backend/free_trial.py writes projects.meta.free_result); the budget,
#  the lane and the credit switch are track D's (claude_trial / claude_lane /
#  claude_credit): one running total, trial_budgets.
# --------------------------------------------------------------------------

def project_admission(conn, project_id):
    with conn.cursor() as cur:
        cur.execute("SELECT meta FROM projects WHERE id = %s",
                    (int(project_id),))
        meta = (cur.fetchone() or {}).get("meta") or {}
    admission = meta.get("free_result")
    return admission if isinstance(admission, dict) else None


def trial_requests_on(conn, user_id=None):
    """Typed requests may run on the trial lane: app_kv free_result is on (or
    this is an internal test account), its ``trial_requests`` kill switch is
    not false, and the automatic credit switch is on."""
    import claude_trial
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM app_kv WHERE key = 'free_result'")
        row = cur.fetchone()
    raw = (row.get("value") if isinstance(row, dict) else row[0]) if row else None
    try:
        data = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    flag = claude_trial.parse_flag(data)
    try:
        is_test = user_id is not None and int(user_id) in flag["test_user_ids"]
    except (TypeError, ValueError):
        is_test = False
    if not (flag["on"] or is_test) or data.get("trial_requests", True) is False:
        return False
    import claude_credit
    return bool(claude_credit.status(conn).get("on"))


def trial_remaining_usd(conn, user_id):
    """What the account's trial budget has left (trial_budgets, track D).
    No grant yet means the whole per-account cap is still ahead of it."""
    import claude_trial
    if not claude_trial.tables_ready(conn):
        return 0.0
    row = claude_trial.get_budget(conn, user_id)
    if row is None:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM app_kv WHERE key = 'free_result'")
            kv = cur.fetchone()
        raw = (kv.get("value") if isinstance(kv, dict) else kv[0]) if kv else None
        return float(claude_trial.parse_flag(raw)["per_account_usd_cap"])
    cap = float((row.get("cap_usd") if isinstance(row, dict) else row[1]) or 0)
    spent = float((row.get("spent_usd") if isinstance(row, dict) else row[2]) or 0)
    return max(0.0, cap - spent)


def enqueue(conn, project_id, user_id, extra=None):
    """One First-cut job per project (idempotent)."""
    with conn.cursor() as cur:
        cur.execute("""SELECT id FROM video_jobs
                       WHERE project_id = %s AND type = 'agent_turn'
                         AND payload->>'first_cut' = 'true'
                       LIMIT 1""", (int(project_id),))
        row = cur.fetchone()
        if row:
            return None
    payload = {"first_cut": True, "trial_lane": True}
    payload.update(extra or {})
    return dbx.enqueue_job(conn, project_id, user_id, "agent_turn", payload)


# --------------------------------------------------------------------------
#  The long-source scout
# --------------------------------------------------------------------------

_HOOK_RULE = (
    "The hook is the opening title a top creator would put on screen for "
    "the first 2-3 seconds: 2 to 5 words, faithful to what is said (no "
    "invented claims, numbers or names, never a negation the speaker did not "
    "say, numbers written as words exactly as spoken), with exactly ONE "
    "word wrapped in *stars* for the accent, e.g. "
    "\"The *real* cost of AI\". ")
# C2 (29 Oct): the references change the picture every 2-3 s; on a single
# take the cheapest faithful event is the spoken key phrase slammed in big
# type (R09, R10, R14). The scout names them in the same call.
_BEATS_RULE = (
    "BEATS: up to 4 phrases from inside your span (not its first sentence) "
    "that a top editor would slam on screen in huge type as they are said: "
    "a spoken number with its noun, a named thing, or the word the point "
    "turns on. Each is 1 to 3 CONSECUTIVE words copied EXACTLY as written "
    "in one sentence, the beats spread over the span about 5 seconds apart "
    "(give 3 or 4 when the span allows). ")
SCOUT_SYSTEM = (
    "You pick the single best moment of a long video for one vertical short. "
    "Choose ONE continuous run of whole sentences, 20 to 60 seconds long, "
    "that stands on its own: the first sentence hooks a new viewer (a "
    "complete claim or question, never one starting with \"and\", \"so\" "
    "or a filler), the last one lands a payoff (an answer, a twist, a strong "
    "line). Prefer a concrete story or claim over small talk, intros and "
    "sponsor reads. " + _HOOK_RULE + _BEATS_RULE +
    "Answer with JSON only, copying the sentence ids exactly as written "
    "(for example \"s12\"): {\"start_id\": \"<id>\", \"end_id\": \"<id>\", "
    "\"title\": \"<5-8 word title>\", \"hook\": \"<2-5 words>\", "
    "\"beats\": [{\"id\": \"<sentence id>\", \"words\": \"<1-3 words>\"}]}.")
TRIM_SYSTEM = (
    "You trim the two ends of a short video for a vertical reel and keep "
    "everything in between. START: the first sentence a new viewer should "
    "hear. Skip greetings, mic checks (\"is this recording?\"), false "
    "starts and a fragment that begins mid-sentence; otherwise keep the very "
    "first sentence. END: the line that lands the point (a payoff, an "
    "answer, a strong claim), keeping the result 20 to 60 seconds long when "
    "the video is longer; drop what follows it when that is thanks, "
    "chit-chat, a sign-off, a trailing fragment or garbled speech. "
    + _HOOK_RULE + _BEATS_RULE +
    "Answer with JSON only, copying the sentence ids exactly as written: "
    "{\"start_id\": \"<id>\", \"end_id\": \"<id>\", \"hook\": \"<2-5 words>\", "
    "\"beats\": [{\"id\": \"<sentence id>\", \"words\": \"<1-3 words>\"}]}.")


def _with_broll(system):
    """Q1 (30 Oct): the same scout call also names the B-roll moments
    (first_cut_visual.BROLL_RULE) for trial accounts and new subscribers;
    a grandfathered payer's scout prompt is unchanged."""
    import first_cut_visual as fcv
    head, sep, tail = system.partition("Answer with JSON only")
    tail = tail.replace("}]}.", "}]" + fcv.BROLL_JSON + "}.", 1)
    return head + fcv.OPENING_RULE + fcv.ENDING_RULE + fcv.BROLL_RULE + sep + tail


def _sentences(index, limit_s):
    return [s for s in index.get("sentences") or []
            if s.get("t0") is not None and float(s["t0"]) < limit_s]


def _density_pick(index, limit_s):
    """Deterministic fallback: the 30-55 s run of whole sentences with the
    most words per second, skipping the first 20 s of intro when possible."""
    rows = _sentences(index, limit_s)
    best, best_score = None, -1.0
    for i, first in enumerate(rows):
        words, t0 = 0, float(first["t0"])
        for last in rows[i:]:
            words += len(str(last.get("text") or "").split())
            span = float(last["t1"]) - t0
            if span > 55:
                break
            if span >= 30:
                score = words / span - (0.4 if t0 < 20 else 0.0)
                if score > best_score:
                    best, best_score = (t0, float(last["t1"])), score
    return best


def _ends_thought(row):
    text = str(row.get("text") or "").strip()
    return text[-1:] in ".!?" and len(text.split()) >= 3


def _fit_span(rows, first, last, low=20.0, high=60.0, complete=False):
    """Keep the scout's hook (its first sentence) and bring the span into
    20-60 s on whole sentences: a too-short pick runs on to the following
    sentences, a too-long one ends earlier (E2E 25 Oct: Haiku often picks a
    10 s quote)."""
    order = [str(s["id"]) for s in rows]
    i, j = order.index(str(first["id"])), order.index(str(last["id"]))
    start = float(rows[i]["t0"])
    while j + 1 < len(rows) and float(rows[j]["t1"]) - start < low \
            and float(rows[j + 1]["t1"]) - start <= high:
        j += 1
    while j > i and float(rows[j]["t1"]) - start > high:
        j -= 1
    if complete and not _ends_thought(rows[j]):
        # Q1 (30 Oct): never end on a fragment. The run-on above reached
        # "So I think it, I actually, when I was a student at" (talk10best,
        # a hard fault): run on to the end of that thought, or walk back
        # to the last one that ends.
        k = j
        while k + 1 < len(rows) and not _ends_thought(rows[k]) \
                and float(rows[k + 1]["t1"]) - start <= high:
            k += 1
        if _ends_thought(rows[k]):
            j = k
        else:
            back = [m for m in range(j, i - 1, -1) if _ends_thought(rows[m])]
            if back and float(rows[back[0]]["t1"]) - start >= 12.0:
                j = back[0]
    return start, float(rows[j]["t1"])


def scout_range(ctx, worker_db, spend):
    """(start_s, end_s, title, how) for a long source."""
    limit = min(float(ctx.duration or 0), config.FIRST_CUT_MAX_SOURCE_S)
    rows = _sentences(ctx.index, limit)
    if not rows:
        end = min(float(ctx.duration or 0), 45.0)
        return 0.0, end, None, "opening"
    by_id = {}
    for s in rows:
        # Index ids are "s12"; a model may answer "s12", 12 or "12".
        by_id[str(s["id"])] = s
        digits = re.sub(r"\D", "", str(s["id"]))
        if digits:
            by_id.setdefault(digits, s)
    import claude_lane
    model = getattr(ctx, "first_cut_model", None)
    if model and claude_lane.available():
        lines, budget = [], 90000
        for s in rows:
            line = f"[{s['id']} {float(s['t0']):.1f}-{float(s['t1']):.1f}] {s['text']}"
            budget -= len(line)
            if budget < 0:
                break
            lines.append(line)
        try:
            q1 = bool(getattr(ctx, "first_cut_q1", False))
            messages = [{"role": "system", "content":
                         _with_broll(SCOUT_SYSTEM) if q1 else SCOUT_SYSTEM},
                        {"role": "user", "content":
                         "Transcript (sentence id, source seconds):\n"
                         + "\n".join(lines)}]
            # thinking is on by default on the lane's models: a small
            # max_tokens can be spent before any answer (C1: an empty pick)
            resp = claude_lane.create(model, messages, max_tokens=4000,
                                      effort="low")
            text = resp.choices[0].message.content or ""
            usage = resp.usage
            spend["usd"] += float(getattr(usage, "provider_cost_usd", 0) or 0)
            print(f"[first_cut] scout pick: {text.strip()[:300]}", flush=True)
            llm.record("first_cut_scout",
                       {"model": model, "sentences": len(lines)},
                       {"content": text}, usage)
            match = re.search(r"\{.*\}", text or "", re.S)
            pick = json.loads(match.group(0)) if match else {}
            first = by_id.get(str(pick.get("start_id")))
            last = by_id.get(str(pick.get("end_id")))
            if first and q1:
                whole_start = true_sentence_start(rows, first)
                if last is None or float(last["t1"]) - float(
                        whole_start["t0"]) <= 75.0:
                    first = whole_start
            if first and last and float(last["t1"]) > float(first["t0"]):
                start, end = _fit_span(rows, first, last, complete=q1)
                if q1 and 12.0 <= end - start <= 75.0:
                    # QB: the model picks the strongest of the last few
                    # sentence ends as the closing line (judges, every
                    # talking head: "trails off on 'this and that'", "stops
                    # on a setup line"); one small call, the span's own end
                    # stands without a usable answer
                    import longform
                    closed = longform.pick_ending(ctx, rows, [[start, end]],
                                                  spend)
                    if closed[-1][1] - start >= 12.0:
                        end = closed[-1][1]
                if 12.0 <= end - start <= 75.0:
                    ctx.first_cut_hook = clean_hook(pick.get("hook"),
                                                    ctx.index)
                    ctx.first_cut_beats = beat_spans(ctx.index, by_id,
                                                     pick.get("beats"))
                    if q1:
                        import first_cut_visual as fcv
                        ctx.first_cut_broll = fcv.broll_spans(
                            ctx.index, by_id, pick.get("broll"))
                    return start, end, pick.get("title"), "scout"
        except Exception as exc:  # noqa: BLE001 — the fallback is the point
            print(f"[first_cut] scout failed: {type(exc).__name__}: "
                  f"{str(exc)[:200]}", flush=True)
    picked = _density_pick(ctx.index, limit)
    if picked:
        return picked[0], picked[1], None, "density"
    return 0.0, min(float(ctx.duration or 0), 45.0), None, "opening"


# --------------------------------------------------------------------------
#  Craft defaults (REFERENCE_PLAYBOOK.md, 29 Oct 2026)
# --------------------------------------------------------------------------

# Never the first word a viewer hears: the references open on a complete
# claim or question at 0.0 s (8 of 8 speech references).
LEAD_WORDS = frozenset((
    "and", "but", "so", "um", "umm", "uh", "uhm", "er", "erm", "ah", "okay",
    "ok", "well", "yeah", "yes", "like", "also", "alright", "right", "anyway",
    "anyways", "now"))
# Opening sentences a viewer should never start on.
_JUNK_OPENING = re.compile(
    r"^\W*(?:(?:okay|ok|alright|all right|so|um|uh|hi|hey|hello)\W*)*"
    r"(?:is (?:this|it) (?:recording|on)|can you (?:hear|see) me|"
    r"testing|test test|cool|hi (?:everyone|guys|all)|hey (?:everyone|guys)|"
    r"hello (?:everyone|guys)|welcome back|let'?s (?:get started|go))?\W*$",
    re.I)
TAIL_LIVE_S = 0.5          # live picture after the last word (refs 0.2-0.7)
TAIL_NEXT_GAP_S = 0.08     # never into the next word
PAUSE_CUT_S = 0.30         # every pause over this inside speech is cut
PAUSE_PAD_S = 0.08         # what each side of a cut pause keeps
HOOK_S = 3.0               # the hook title's time on screen (refs 2.5-3.5)
HOOK_MIN_S = 2.9           # a spoken-line hook holds at least this
HOOK_MAX_S = 3.3           # and at most this
CAPTION_SCALE = 1.45       # chest captions (judge: "about 30% bigger", C2)
CAPTION_WORDS = 2          # 1-2 words at a time (references: one word)
BEAT_GAP_S = 4.5           # slams at least this far apart
BEAT_MAX = 5
BEAT_ACCENT = "#FFD23F"    # the one accent colour of the slams
BEAT_CARD = "#0E1A33"      # every other slam cuts to a flat card (R10, R14)
_FILLERS = frozenset(("um", "umm", "uh", "uhh", "uhm", "er", "erm", "ah",
                      "ahh", "hmm"))


def _wt(w, k):
    v = w.get(k)
    if v is None:
        v = w.get({"t0": "s", "t1": "e"}[k])
    return float(v)


def _norm(word):
    return re.sub(r"[^a-z']", "", str(word or "").lower())


def clean_hook(text, index=None, most_words=6, most_chars=48):
    """The scout's hook, or None when it is not a faithful 2-6 word title:
    numbers must be ones the speaker said, one *accent* at most."""
    t = " ".join(w for w in str(text or "").replace("\n", " ").split()
                 if _norm(w.strip("*")) not in _FILLERS).strip(" .\"'")
    if not t:
        return None
    words = t.replace("*", "").split()
    if not 2 <= len(words) <= most_words or len(t) > most_chars:
        return None
    if words[0][:1].islower() and _norm(words[0]) in _CONTINUATION:
        # QI (30 Oct): "will be roughly one *millionth* only" opened a
        # phone First cut; both judges: "opens mid-sentence" (hard fault)
        return None
    said = " ".join(str(w.get("w") or "") for w in
                    (index or {}).get("words") or []).lower()
    for num in re.findall(r"\d[\d,.]*", t):
        if said and num.strip(",.") not in said:
            return None
    if t.count("*") not in (0, 2):
        t = t.replace("*", "")
    if "*" not in t:
        # the accent goes on the longest word
        longest = max(reversed(words), key=len)     # the last, on a tie
        t = t.replace(longest, f"*{longest}*", 1)
    return t


# Words that continue a sentence (an auxiliary, a preposition, a relative)
# and never start a title when written in lower case.
_CONTINUATION = frozenset((
    "will", "would", "be", "is", "are", "was", "were", "been", "being",
    "to", "of", "for", "with", "that", "which", "than", "as", "by", "from",
    "into", "onto", "about"))


def true_sentence_start(rows, first):
    """The row a span should open on: ``first`` walked back to where the
    speaker's sentence begins. The transcript splits a long sentence into
    rows; a row that starts in lower case after a row with no terminal mark
    is the middle of a sentence (QI, 30 Oct: a phone First cut opened on
    "you will be roughly one millionth only…", rows s2-s4)."""
    try:
        i = rows.index(first)
    except ValueError:
        return first
    while i > 0:
        text = str(rows[i].get("text") or "").lstrip(" \"'\u201c(")
        prev = str(rows[i - 1].get("text") or "").rstrip(" \"'\u201d)")
        if text[:1].islower() and prev and prev[-1] not in ".?!\u2026":
            i -= 1
            continue
        break
    return rows[i]


def _opening_hook(rows):
    """Deterministic hook: up to five words of the opening sentence, past
    its lead words."""
    if not rows:
        return None
    words = [w for w in str(rows[0].get("text") or "").split()]
    while words and _norm(words[0]) in LEAD_WORDS:
        words.pop(0)
    words = [w.strip(",.;:!?") for w in words if w.strip(",.;:!?")]
    if len(words) < 2:
        return None
    pick = words[:5] if len(words) > 6 else words
    return clean_hook(" ".join(pick))


def _trim_pick(rows, duration):
    """Deterministic trim for a short source: skip opening sentences that a
    viewer should never start on (a mic check, a greeting, a fragment that
    begins mid-sentence at the very start), keep the rest."""
    i = 0
    while i < len(rows) - 1:
        s = rows[i]
        text = str(s.get("text") or "").strip()
        fragment = float(s.get("t0") or 0) < 0.4 and text[:1].islower()
        if _JUNK_OPENING.match(text) or fragment:
            i += 1
            continue
        break
    j = len(rows) - 1
    if j > i and str(rows[j].get("text") or "").strip()[-1:] not in ".!?" \
            and float(rows[j]["t1"]) > duration - 0.6:
        j -= 1
    return rows[i], rows[j]


def _complete_end(rows, first, last):
    """The pick's last sentence, walked back to one that ends a thought: a
    sentence closing on . ! or ? with at least four words (the transcript's
    own punctuation; "beyond so" is a fragment). Never before ``first``."""
    order = [str(r["id"]) for r in rows]
    try:
        i, j = order.index(str(first["id"])), order.index(str(last["id"]))
    except ValueError:
        return last
    for k in range(j, i - 1, -1):
        text = str(rows[k].get("text") or "").strip()
        if text[-1:] in ".!?" and len(text.split()) >= 4:
            return rows[k]
    return last


def trim_range(ctx, worker_db, spend):
    """(start_s, end_s, how) for a short speech source; also sets
    ctx.first_cut_hook. One small scout call on the trial lane, the
    deterministic trim without it."""
    rows = _sentences(ctx.index, float(ctx.duration or 0) + 1)
    duration = float(ctx.duration or 0)
    if not rows:
        return 0.0, duration, "none"
    first, last = _trim_pick(rows, duration)
    how = "rules"
    hook = None
    import claude_lane
    model = getattr(ctx, "first_cut_model", None)
    if model and claude_lane.available():
        by_id = {str(r["id"]): r for r in rows}
        for r in rows:
            by_id.setdefault(re.sub(r"\D", "", str(r["id"])), r)
        lines = [f"[{r['id']} {float(r['t0']):.1f}-{float(r['t1']):.1f}] "
                 f"{r['text']}" for r in rows]
        try:
            q1 = bool(getattr(ctx, "first_cut_q1", False))
            resp = claude_lane.create(
                model, [{"role": "system", "content":
                         _with_broll(TRIM_SYSTEM) if q1 else TRIM_SYSTEM},
                        {"role": "user", "content":
                         "Transcript (sentence id, source seconds):\n"
                         + "\n".join(lines)}],
                max_tokens=4000, effort="low")
            text = resp.choices[0].message.content or ""
            spend["usd"] += float(getattr(resp.usage, "provider_cost_usd", 0)
                                  or 0)
            llm.record("first_cut_trim", {"model": model,
                                          "sentences": len(lines)},
                       {"content": text}, resp.usage)
            print(f"[first_cut] trim pick: {text.strip()[:240]}", flush=True)
            match = re.search(r"\{.*\}", text, re.S)
            pick = json.loads(match.group(0)) if match else {}
            a = by_id.get(str(pick.get("start_id")))
            b = by_id.get(str(pick.get("end_id")))
            if a and b and float(b["t1"]) - float(a["t0"]) >= min(
                    8.0, 0.5 * duration):
                if getattr(ctx, "first_cut_q1", False):
                    # QB: the span opens where the speaker's sentence begins
                    # (scout_range does this since QI e792bae8; this short
                    # source's trim did not: a phone First cut opened on
                    # "you will be roughly one millionth only", the hook
                    # title was dropped as a fragment and the reply claimed
                    # one)
                    a = true_sentence_start(rows, a)
                first, last, how = a, b, "scout"
            hook = clean_hook(pick.get("hook"), ctx.index)
            ctx.first_cut_beats = beat_spans(ctx.index, by_id,
                                             pick.get("beats"))
            if q1:
                import first_cut_visual as fcv
                ctx.first_cut_broll = fcv.broll_spans(ctx.index, by_id,
                                                      pick.get("broll"))
        except Exception as exc:  # noqa: BLE001 — the rules are the fallback
            print(f"[first_cut] trim scout failed: {type(exc).__name__}: "
                  f"{str(exc)[:200]}", flush=True)
    end_row = _complete_end(rows, first, last)
    if float(end_row["t1"]) - float(first["t0"]) >= min(8.0, 0.5 * duration):
        last = end_row
    f = _faces(ctx.index)
    if getattr(ctx, "first_cut_q1", False) and (not f or f["share"] < 0.35) \
            and not _screen_like(ctx.index):
        import first_cut_visual as fcv
        last = fcv.conclusion_end(rows, first, last, _ends_thought)
    ctx.first_cut_hook = hook or _opening_hook(
        [r for r in rows if float(r["t0"]) >= float(first["t0"]) - 1e-3])
    return float(first["t0"]), float(last["t1"]), how


def _keep(ctx):
    try:
        return [list(map(float, k)) for k in
                ctx.latest_edl()["json"].get("keep") or []]
    except Exception:
        return None


def _talk_density(ctx):
    """Words per second over the kept program (0 when unknown)."""
    keep = _keep(ctx)
    words = (ctx.index or {}).get("words") or []
    if not keep or not words:
        return 0.0
    span = sum(max(0.0, e - s) for s, e in keep)
    n = sum(1 for w in words if any(s <= _wt(w, "t0") < e for s, e in keep))
    return n / span if span > 0 else 0.0


# QB (30 Oct): a word said twice in a row ("is is deep", "work and And") is a
# stutter: the captions printed it and the judges marked it in 2 of 2
# judgements of the 10-minute talk. Only short function words, never an
# emphasis a speaker means ("very very", "no no", "that that").
STUTTER_WORDS = frozenset((
    "i", "a", "the", "and", "to", "of", "in", "is", "it", "we", "you", "he",
    "she", "they", "at", "for", "but", "or", "on", "was", "are", "be", "as",
    "with", "my", "our", "your", "if", "by", "an", "from", "have", "has"))
STUTTER_GAP_S = 0.4


def stutter_spans(words, keep):
    """[(t0, t1)] source spans of the FIRST of two identical short words in a
    row inside the kept program, the span running up to the second word's
    start."""
    spans = []
    for a, b in zip(words, words[1:]):
        if a.get("w") is None or b.get("w") is None:
            continue
        na, nb = _norm(a["w"]), _norm(b["w"])
        if not na or na != nb or na not in STUTTER_WORDS:
            continue
        try:
            a0, b0 = _wt(a, "t0"), _wt(b, "t0")
            a1 = _wt(a, "t1")
        except (KeyError, TypeError, ValueError):
            continue
        if b0 - a1 >= STUTTER_GAP_S or b0 <= a0:
            continue
        if any(s <= a0 and b0 <= e for s, e in keep):
            spans.append((a0, round(b0 - 0.01, 3)))
    return spans


def drop_stutters(ctx, steps, started):
    """Cut each stutter out of the kept program (one keep write), so the
    audio and the captions both carry the word once. None when there is none."""
    keep = _keep(ctx)
    words = (ctx.index or {}).get("words") or []
    if not keep or not words:
        return None
    spans = stutter_spans(words, keep)
    if not spans:
        return None
    new = []
    for s, e in keep:
        cur = s
        for a, b in sorted(x for x in spans if s <= x[0] and x[1] <= e):
            if a - cur >= 0.2:
                new.append([round(cur, 3), round(a, 3)])
            cur = max(cur, b)
        if e - cur >= 0.2:
            new.append([round(cur, 3), round(e, 3)])
    if not new or new == [list(map(float, k)) for k in keep]:
        return None
    return _run(ctx, steps, "keep_segments",
                {"segments": new, "snap_to_words": False}, started)


def shape_ends(ctx, steps, started):
    """The opening starts ON its first real word (no lead silence, no
    "And/um/so" first), and the ending runs TAIL_LIVE_S of live picture past
    the last word (never into the next one). One keep write; None when
    nothing changed or nothing could be read."""
    keep = _keep(ctx)
    words = [w for w in (ctx.index or {}).get("words") or []
             if w.get("w") is not None]
    if not keep or not words:
        return None
    new = [list(k) for k in keep]
    s0, e0 = new[0]
    inside = [w for w in words if s0 - 0.05 <= _wt(w, "t0") < e0]
    k = 0
    while k < len(inside) - 2 and _norm(inside[k]["w"]) in LEAD_WORDS:
        k += 1
    if inside:
        first = inside[k]
        # never inside the word before it (a dropped "so" ends where "this"
        # starts: the QC heard its tail as a mid-word open, C1 29 Oct)
        before = [_wt(w, "t1") for w in words if _wt(w, "t1") <= _wt(first, "t0") + 1e-3]
        floor = max([s0] + before[-1:])
        new[0][0] = round(max(floor, _wt(first, "t0") - 0.04), 3)
    s1, e1 = new[-1]
    spoken = [w for w in words if s1 <= _wt(w, "t0") < e1]
    if spoken:
        last_end = _wt(spoken[-1], "t1")
        later = [_wt(w, "t0") for w in words if _wt(w, "t0") >= e1 - 1e-3]
        limit = min([float(ctx.duration or e1)] +
                    [t - TAIL_NEXT_GAP_S for t in later[:1]])
        want = min(last_end + TAIL_LIVE_S, limit)
        if want > e1 + 0.02:
            new[-1][1] = round(want, 3)
    if new == keep or new[0][1] - new[0][0] < 0.3:
        return None
    return _run(ctx, steps, "keep_segments",
                {"segments": new, "snap_to_words": False}, started)


def _faces(index):
    """(median bottom, median width, share of samples) of the largest face
    per spatial sample, in source fractions; None without faces."""
    samples = ((index or {}).get("spatial") or {}).get("samples") or []
    boxes = []
    for smp in samples:
        faces = [f for f in smp.get("faces") or [] if len(f) >= 4]
        if faces:
            boxes.append(max(faces, key=lambda b: (b[2] - b[0]) * (b[3] - b[1])))
    if not samples or not boxes:
        return None
    boxes.sort(key=lambda b: b[3])
    bottom = boxes[len(boxes) // 2][3]
    widths = sorted(b[2] - b[0] for b in boxes)
    return {"bottom": float(bottom), "width": float(widths[len(widths) // 2]),
            "share": len(boxes) / float(len(samples))}


def _screen_like(index):
    """A screen recording: most samples are dense UI or text, with no face."""
    samples = ((index or {}).get("spatial") or {}).get("samples") or []
    if not samples:
        return False
    ui = sum(1 for s in samples if s.get("dense_ui") or len(s.get("text") or []) >= 3)
    faces = sum(1 for s in samples if s.get("faces"))
    return ui >= 0.6 * len(samples) and faces <= 0.15 * len(samples)


def static_bands(frames):
    """[top, bottom] (fractions) of the picture between the source's own
    static bands, or None. ``frames``: grey frames (n, h, w) sampled across
    the video. A band is the run of rows from the top (or bottom) edge that
    never changes while the picture beside it does (temporal spread under
    15% of the picture's), ending on a straight full-width edge: a
    letterbox, or a phone-recorded event's decorative bars (the bake-off
    phone clip, whose bars carry a static gold swoosh). Both bands must
    exist, each 3-32% of the height, around at least 40% of picture."""
    import numpy as np
    f = np.asarray(frames, dtype=np.float32)
    if f.ndim != 3 or f.shape[0] < 4:
        return None
    n, h, w = f.shape
    tstd = f.std(axis=0).mean(axis=1)                      # per row
    edge = np.median(np.abs(f[:, 1:, :] - f[:, :-1, :]), axis=0)
    full = (edge > 8.0).mean(axis=1)                        # share of columns
    mid = np.sort(tstd[int(0.3 * h):int(0.7 * h)])
    med = float(mid[len(mid) // 2]) if len(mid) else 0.0
    if med < 3.0:
        return None                                         # a still picture
    thr = max(1.5, 0.15 * med)

    def extent(rows):
        k = 0
        for y in rows:
            if tstd[y] > thr:
                break
            k += 1
        return k
    top = extent(range(h))
    bot = extent(range(h - 1, -1, -1))
    lo, hi = 0.03 * h, 0.32 * h
    if not (lo <= top <= hi and lo <= bot <= hi):
        return None

    def straight(y):
        ys = [v for v in range(y - 3, y + 2) if 0 <= v < h - 1]
        return bool(ys) and float(full[ys].max()) >= 0.55
    if not (straight(top) and straight(h - bot - 1)):
        return None
    t, b = top / h, (h - bot) / h
    if b - t < 0.4:
        return None
    return [round(t, 4), round(b, 4)]


def _band_frames(ctx, n=12, width=96):
    """Grey frames sampled across the proxy for static_bands, or None."""
    import subprocess
    import numpy as np
    try:
        path = ctx.proxy_path()
    except Exception:
        return None
    video = (ctx.index or {}).get("video") or {}
    try:
        h = int(round(width * float(video["height"]) / float(video["width"])))
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None
    h += h % 2
    dur = float(ctx.duration or 0)
    out = []
    for k in range(n):
        t = dur * (k + 0.5) / n
        try:
            raw = subprocess.run(
                ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{t:.2f}",
                 "-i", path, "-frames:v", "1", "-vf",
                 f"scale={width}:{h},format=gray", "-f", "rawvideo",
                 "-pix_fmt", "gray", "-"],
                capture_output=True, timeout=30).stdout
        except Exception:
            continue
        if len(raw) == width * h:
            out.append(np.frombuffer(raw, np.uint8).reshape(h, width))
    return out if len(out) >= 6 else None


def deband(ctx, steps, started):
    """Crop the source's own static bands (letterbox, decorative bars): one
    static camera zoom over the whole program that maps the live picture's
    rows onto the full frame. Returns (result, [top, bottom]) or (None,
    None)."""
    frames = _band_frames(ctx)
    bands = static_bands(frames) if frames else None
    if not bands:
        return None, None
    top, bottom = bands
    span = bottom - top
    k = 1.0 / span
    cy = top / max(1e-6, 1.0 - span)
    try:
        import schemas
        prog = float(schemas.program_duration(ctx.latest_edl()["json"]))
    except Exception:
        return None, None
    res = _run(ctx, steps, "add_zoom",
               {"start": 0.0, "end": round(prog, 2), "strength": round(k - 1.0, 3),
                "mode": "punch", "ramp_s": 0.0, "cx": 0.5,
                "cy": round(min(max(cy, 0.0), 1.0), 3),
                "purpose": "crop the source's own static bands "
                           f"(rows {top:.2f}-{bottom:.2f} are the picture)"},
               started)
    return res, bands


def screen_focus(ctx, steps, started):
    """A screen recording kept in its own shape is cropped to its content:
    the column its text actually occupies (the index's text boxes, 10th-90th
    percentile) fills the frame, so the text reads (playbook 2.8: screens
    are zoomed to the area being discussed, never tiny and whole; C2 judge:
    "the code and headings fill the frame"). One static camera zoom, like
    the band crop. None when the text already spans the frame."""
    samples = ((ctx.index or {}).get("spatial") or {}).get("samples") or []
    boxes = [b for smp in samples for b in (smp.get("text") or []) if len(b) >= 4]
    if len(boxes) < 8:
        return None
    n = len(boxes)
    x0 = sorted(float(b[0]) for b in boxes)[int(0.1 * n)]
    x1 = sorted(float(b[2]) for b in boxes)[int(0.9 * n) - 1]
    y0 = sorted(float(b[1]) for b in boxes)[int(0.1 * n)]
    width = min(1.0, (x1 - x0) + 0.05)
    if width > 0.8 or width <= 0.2:
        return None
    k = min(1.0 / width, 1.55)
    w = 1.0 / k
    left = min(max((x0 + x1) / 2.0 - w / 2.0, 0.0), 1.0 - w)
    top = min(max(y0 - 0.04, 0.0), 1.0 - w)
    try:
        import schemas
        prog = float(schemas.program_duration(ctx.latest_edl()["json"]))
    except Exception:
        return None
    return _run(ctx, steps, "add_zoom",
                {"start": 0.0, "end": round(prog, 2), "strength": round(k - 1.0, 3),
                 "mode": "punch", "ramp_s": 0.0,
                 "cx": round(left / max(1e-6, 1.0 - w), 3),
                 "cy": round(top / max(1e-6, 1.0 - w), 3),
                 "purpose": "crop the screen to its content column "
                            f"(x {x0:.2f}-{x1:.2f}) so the text reads"},
                started)


def chest_y(faces, bands=None):
    """The caption and hook band on a vertical talking head: just under the
    face (references: y 0.50-0.70), clear of the platform's bottom UI."""
    if not faces:
        return None
    bottom = faces["bottom"]
    if bands:
        bottom = (bottom - bands[0]) / max(1e-6, bands[1] - bands[0])
    # C2 judge: "raise the caption baseline to about y 0.60-0.65"
    return round(min(max(bottom + 0.08, 0.58), 0.70), 3)


DISCOURSE_OPENERS = frozenset((("you", "know"), ("i", "mean"), ("you", "see"),
                               ("kind", "of"), ("sort", "of")))
_TAIL_STOP = frozenset((
    "the", "a", "an", "of", "to", "and", "in", "on", "for", "with", "by",
    "our", "your", "my", "their", "is", "are", "was", "were", "be", "that",
    "this", "it", "if", "so", "but", "or", "at", "as", "from"))


def spoken_opening(ctx, most_s=2.6, most_words=7):
    """(text, end_s) of the words heard first in the PROGRAM, as the hook:
    the references set the opening line itself in poster type (R02, R13,
    R14), so the title IS what the viewer hears and every heard word reaches
    the screen once (the captions carry the rest). Up to ``most_words``
    words that start before ``most_s``, ending on a sentence end when there
    is one; the accent on the last longest word. None when no words."""
    keep = _keep(ctx)
    words = [w for w in (ctx.index or {}).get("words") or []
             if w.get("w") is not None]
    if not keep or not words:
        return None
    from timeline import Timeline
    try:
        tl = Timeline(keep, [], None)
    except Exception:
        return None
    picked = []
    for w in words:
        a = tl.src_to_out(_wt(w, "t0"))
        b = tl.src_to_out(_wt(w, "t1"))
        if a is None or b is None:
            continue
        if a >= most_s + 0.8 or len(picked) >= 10:
            break
        picked.append((str(w["w"]).strip(), a, b))
    while picked and _norm(picked[0][0]) in LEAD_WORDS and len(picked) > 2:
        picked.pop(0)
    if getattr(ctx, "first_cut_q1", False):
        # QB: "you know" / "I mean" is filler, never the title (the 10-minute
        # talk's long-form cut opened on "YOU KNOW", a hard fault)
        while len(picked) > 2 and tuple(_norm(p[0]) for p in picked[:2]) in \
                DISCOURSE_OPENERS:
            picked = picked[2:]
            while picked and _norm(picked[0][0]) in LEAD_WORDS and len(picked) > 2:
                picked.pop(0)
    if len(picked) < 2:
        return None
    # The title takes a whole clause when one ends within nine words: the
    # captions then carry nothing of its phrase while it is up (C1: the
    # rest of a cut phrase, "of the sun's", landed under the title).
    clause = next((k for k, p in enumerate(picked[:9])
                   if p[0].endswith((",", ".", ";", ":", "!", "?")) and k >= 1),
                  None)
    if clause is not None:
        picked = picked[:clause + 1]
    else:
        picked = [p for p in picked if p[1] < most_s][:6]
        while len(picked) > 2 and _norm(picked[-1][0]) in _TAIL_STOP:
            picked.pop()
    text = " ".join(p[0].strip(",;:") for p in picked)
    text = clean_hook(text, most_words=9, most_chars=64) or None
    if not text:
        return None
    # off as its last word ends: speech runs on, and the next words would
    # otherwise be carried to another band while the title still holds
    end = min(max(picked[-1][2] + 0.08, 1.2), 3.2)
    return text, round(end, 2)


def hook_params(text, y=None, vertical=True):
    # slam: the title is whole by ~0.3 s (references); blur out, never a
    # cut, which the picture QC reads as a one-frame pop. A 1-3 word title
    # sets bigger (references: 60-90% of the width; C2: "ROCKET RETURNS"
    # spanned 45% at 1.3)
    words = len(str(text or "").replace("/", " ").split())
    size = (1.6 if words <= 3 else 1.3) if vertical else (1.2 if words <= 3 else 0.9)
    # pop: every word is whole by ~0.25 s (C2 judge: the slam's stagger
    # showed "WA WORKING" mid-animation at 0.3 s, the mask "half-formed")
    params = {"text": text, "font": "Anton", "uppercase": True,
              "treatment": "serif", "accent": "#FFFFFF", "entrance": "pop",
              "exit": "blur", "size": size,
              "scrim": 0.35}
    if y is not None:
        params["y"] = y
    return params


_NUMBERISH = re.compile(
    r"^(?:\d[\d,.]*(?:%|x|k|m)?|hundred|thousand|million|millions|billion|"
    r"billions|trillion|trillionth|millionth|billionth|thousandth|hundredth|"
    r"percent|twice|double|triple|half|zero|"
    r"first|one|two|three|four|five|six|seven|eight|nine|ten|twenty|"
    r"thirty|forty|fifty|hundreds|thousands)$")


def _tok(word):
    """A word for matching: lower case, letters, digits and apostrophes
    (_norm drops digits, which turned the beat "30 feet" into "feet")."""
    return re.sub(r"[^a-z0-9'%]", "", str(word or "").lower())


def _phrase_words(index, row, phrase):
    """The index words of ``phrase`` (1-3 consecutive words, compared
    without punctuation or case) inside sentence ``row``; None when the
    phrase is not said there word for word."""
    toks = [_tok(t) for t in str(phrase or "").replace("*", " ").split()]
    toks = [t for t in toks if t]
    if not 1 <= len(toks) <= 4:
        return None
    t0, t1 = float(row["t0"]) - 0.05, float(row["t1"]) + 0.05
    words = [w for w in (index or {}).get("words") or []
             if w.get("w") is not None and t0 <= _wt(w, "t0") <= t1]
    norm = [_tok(w["w"]) for w in words]
    for i in range(len(words) - len(toks) + 1):
        if norm[i:i + len(toks)] == toks:
            return words[i:i + len(toks)]
    return None


def beat_spans(index, by_id, beats):
    """[(src_t0, src_t1, text)] for the scout's beats that are said word
    for word in their sentence (anything else is dropped: a slam is the
    speaker's own words, never a paraphrase)."""
    out = []
    for beat in (beats or [])[:6]:
        if not isinstance(beat, dict):
            continue
        row = by_id.get(str(beat.get("id")))
        if row is None:
            row = by_id.get(re.sub(r"\D", "", str(beat.get("id"))))
        if row is None:
            continue
        found = _phrase_words(index, row, beat.get("words"))
        if not found:
            continue
        text = " ".join(str(w["w"]).strip(" ,.;:!?\"'") for w in found)
        out.append((_wt(found[0], "t0"), _wt(found[-1], "t1"), text))
    return out


def number_beats(index):
    """Deterministic beats without the scout: a spoken number with the word
    before or after it ("a trillionth", "50 percent")."""
    words = [w for w in (index or {}).get("words") or [] if w.get("w") is not None]
    out = []
    for i, w in enumerate(words):
        if not _NUMBERISH.match(_tok(w["w"]) or "-"):
            continue
        if _tok(w["w"]) in ("one", "first", "two") and i + 1 < len(words) \
                and not _NUMBERISH.match(_tok(words[i + 1]["w"]) or "-"):
            continue    # "one of", "first time": not a figure
        group = [w]
        if i + 1 < len(words) and _norm(words[i + 1]["w"]) not in _TAIL_STOP \
                and _wt(words[i + 1], "t0") - _wt(w, "t1") < 0.3:
            group.append(words[i + 1])
        elif i > 0 and _norm(words[i - 1]["w"]) in ("a", "an", "the") \
                and _wt(w, "t0") - _wt(words[i - 1], "t1") < 0.3:
            group.insert(0, words[i - 1])
        text = " ".join(str(x["w"]).strip(" ,.;:!?\"'") for x in group)
        out.append((_wt(group[0], "t0"), _wt(group[-1], "t1"), text))
    return out


def _star(text):
    words = text.split()
    if len(words) == 1:
        return f"*{words[0]}*"
    nums = [x for x in words if _NUMBERISH.match(_tok(x) or "-")]
    num = max(nums, key=len) if nums else None
    content = [x for x in words if _norm(x) not in _TAIL_STOP]
    pick = num or (max(reversed(content), key=len) if content else words[-1])
    return re.sub(r"(?<!\S)" + re.escape(pick) + r"(?!\S)", f"*{pick}*",
                  text, count=1)


def _card_to_slam(ctx, steps, started, card_id, start, end):
    """The card under a slam lasts exactly as long as the slam the tool
    actually placed. add_motion_graphic may shorten a slam (a cut or the
    program's end), and the card then showed empty for the difference: QI,
    30 Oct, vlog First cut, slam 13.90-15.19 s over a card to 15.33 s, judged
    "an empty navy frame around 15.2 s" (C2 and Q3 saw the same)."""
    edl = ctx.latest_edl()["json"]
    slam = None
    for m in edl.get("motion") or []:
        if m.get("template") == "word_slam" and \
                abs(float(m.get("start") or 0) - start) < 0.3:
            slam = m
    if not slam:
        return
    s0, s1 = float(slam.get("start") or start), float(slam.get("end") or end)
    if abs(s0 - start) > 0.02 or abs(s1 - end) > 0.02:
        _run(ctx, steps, "set_vector_graphic",
             {"id": card_id, "start": round(s0, 3), "end": round(s1, 3)},
             started)


def beat_slams(ctx, steps, started, beats, hook_end, y, avoid=None):
    """Slam the beats on screen as they are said (word_slam, display tier:
    huge type, the spoken words, captions drop exactly those words). At
    least BEAT_GAP_S apart, clear of the hook and the last second, two type
    families alternating, one accent. Returns how many landed."""
    keep = _keep(ctx)
    if not keep or not beats:
        return 0
    from timeline import Timeline
    try:
        tl = Timeline(keep, [], None)
        import schemas
        prog = float(schemas.program_duration(ctx.latest_edl()["json"]))
    except Exception:
        return 0
    placed, earliest = 0, hook_end + 0.6
    print(f"[first_cut] beats: {[(round(a, 1), t) for a, _b, t in beats][:8]}",
          flush=True)
    for t0, t1, text in sorted(beats):
        if placed >= BEAT_MAX:
            break
        a, b = tl.src_to_out(t0), tl.src_to_out(t1)
        if a is None or b is None or b < a:
            continue
        start = round(max(0.0, a - 0.2), 2)
        end = round(min(max(b + 0.45, start + 1.0), start + 1.6), 2)
        if start < earliest or end > prog - 0.8:
            continue
        if any(start < b + 0.3 and end > a - 0.3 for a, b in avoid or []):
            continue        # a cutaway already shows this moment (Q1)
        card = placed % 2 == 1
        params = {"text": _star(text), "tier": "display", "entrance": "pop",
                  "exit": "cut", "accent": BEAT_ACCENT, "width": 0.82,
                  "role": "serif" if card else "condensed"}
        card_id = None
        if card:
            # a graphic insert: the picture cuts to a flat card with the
            # line in huge type while the voice runs on (the references'
            # chapter and stat cards; C2 judge: "B-roll or graphic inserts")
            before = {v.get("id") for v in
                      ctx.latest_edl()["json"].get("vectors") or []}
            if not _ok(_run(ctx, steps, "add_vector_graphic", {
                    "kind": "rectangle", "start": start, "end": end,
                    "x": 0.5, "y": 0.5, "width": 1.0, "height": 1.0,
                    "color": BEAT_CARD, "opacity": 1.0}, started)):
                card = False
                params["role"] = "condensed"
            else:
                card_id = next((v.get("id") for v in
                                ctx.latest_edl()["json"].get("vectors") or []
                                if v.get("id") not in before), None)
        if card:
            params["y"] = 0.5
        elif y is not None:
            params["y"] = y
        res = _run(ctx, steps, "add_motion_graphic",
                   {"template": "word_slam", "start": start, "end": end,
                    "params": params,
                    "purpose": "the spoken key phrase slammed as it is said "
                               "(references: a visual event every 2-3 s)"},
                   started)
        if _ok(res):
            placed += 1
            earliest = start + BEAT_GAP_S
            if card_id:
                _card_to_slam(ctx, steps, started, card_id, start, end)
            cards = getattr(ctx, "first_cut_cards", None)
            if isinstance(cards, list):
                # Q1: a card covers the picture; a slam over the face keeps
                # the wide framing (a cut-in pushed the chin into it)
                cards.append((start, end))
        elif card_id:
            # Q3 (30 Oct): the slam was refused after its card landed; an
            # empty navy card is a hole in the picture (C2 vlog judges:
            # "remove or fill the empty dark-navy frame at 15.2 s")
            _run(ctx, steps, "remove_vector_graphic", {"id": card_id}, started)
    return placed


# --------------------------------------------------------------------------
#  Silent footage: a highlight montage (playbook recipe D, C2 29 Oct)
# --------------------------------------------------------------------------

MONTAGE_MIN_SOURCE_S = 20.0     # shorter silent clips keep their whole take
MONTAGE_TARGET_S = 13.0         # references: 7-15 s; up to ~30 for an event
MONTAGE_SHOT_S = 0.5            # the body's shots (references: 0.2-0.8 s)
MONTAGE_OPEN_S = 0.8            # the first change lands by 1 s (the title
                                # holds over the cuts)
MONTAGE_CLOSE_S = 1.0           # the payoff shot ends on live motion
_MONTAGE_TITLE_SYSTEM = (
    "The numbered frames are the shots of a highlight reel cut from one "
    "video, in order. 1) TITLE: the poster title a top editor would put "
    "over its opening: 1 to 3 words naming what is visibly happening "
    "(\"LAUNCH *DAY*\", \"Game *winner*\"), nothing you cannot see, no brand "
    "or name unless legible on screen, exactly one word in *stars*. 2) END: "
    "the number of the shot the reel should END on: its payoff (the "
    "landing, the goal, the reveal), late in the order; later shots are "
    "dropped. 3) DROP: the numbers of shots that are broken (heavy "
    "pixelation, blur, black), tiny or off-topic. Answer with JSON only: "
    "{\"title\": \"<1-3 words>\", \"end\": <n>, \"drop\": [<n>, ...]}.")


def _motion_curve(ctx, fps=4.0, width=64):
    """(times, frame change, mean luma) over the whole proxy at ``fps``."""
    import subprocess
    import numpy as np
    try:
        path = ctx.proxy_path()
        video = (ctx.index or {}).get("video") or {}
        h = int(round(width * float(video["height"]) / float(video["width"])))
    except Exception:
        return None
    h = max(2, h + h % 2)
    try:
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-nostdin", "-i", path, "-an", "-vf",
             f"fps={fps},scale={width}:{h},format=gray", "-f", "rawvideo",
             "-pix_fmt", "gray", "-"], capture_output=True, timeout=180).stdout
    except Exception:
        return None
    n = len(raw) // (width * h)
    if n < 8:
        return None
    frames = np.frombuffer(raw[:n * width * h], np.uint8).reshape(
        n, h, width).astype(np.float32)
    diff = np.zeros(n, np.float32)
    diff[1:] = np.abs(frames[1:] - frames[:-1]).mean(axis=(1, 2))
    luma = frames.mean(axis=(1, 2))
    times = np.arange(n, dtype=np.float32) / fps
    return times, diff, luma


def montage_windows(curve, shots, duration, target=MONTAGE_TARGET_S):
    """[[start, end]] source windows for a highlight: the strongest moving
    moment of each shot (long shots in 3 s pieces), never a black or frozen
    one, the best ones until ``target`` seconds, in source order; the first
    window long enough to read the title, the last ending on motion."""
    import numpy as np
    times, diff, luma = curve
    spans = []
    for k, sh in enumerate(shots or []):
        try:
            a, b = float(sh["start"]), float(sh["end"])
        except (KeyError, TypeError, ValueError):
            continue
        # a long shot is read in 3 s pieces, but gives ONE window (C2
        # judge: four adjacent windows of one shot made a 4 s static hold)
        while b - a > 3.2:
            spans.append((a, a + 3.0, k))
            a += 3.0
        spans.append((a, b, k))
    if not spans:
        t = 0.0
        while t < duration - 0.5:
            spans.append((t, min(duration, t + 3.0), int(t // 3)))
            t += 3.0
    cands = []
    for a, b, shot in spans:
        if b - a < 0.45:
            continue
        sel = (times >= a) & (times < b)
        if sel.sum() < 2:
            continue
        if float(np.median(luma[sel])) < 14.0:
            continue                    # black or nearly black
        d = diff[sel]
        score = float(np.percentile(d, 75))
        if score < 0.6:
            continue                    # a frozen or static stretch
        peak = float(times[sel][int(np.argmax(d))])
        span = min(b - a, MONTAGE_SHOT_S)
        w0 = min(max(peak - 0.4 * span, a), b - span)
        cands.append({"score": score, "w": [w0, w0 + span], "span": (a, b),
                      "shot": shot})
    if len(cands) < 4:
        return None
    cands.sort(key=lambda c: -c["score"])
    chosen, total, used = [], 0.0, set()
    for c in cands:
        if total >= target or len(chosen) >= 28:
            break
        if c["shot"] in used:
            continue
        used.add(c["shot"])
        chosen.append(c)
        total += c["w"][1] - c["w"][0]
    chosen.sort(key=lambda c: c["w"][0])
    first, last = chosen[0], chosen[-1]
    a, b = first["span"]
    first["w"] = [first["w"][0], min(b, first["w"][0] + MONTAGE_OPEN_S)]
    if first["w"][1] - first["w"][0] < MONTAGE_OPEN_S - 0.05:
        first["w"][0] = max(a, first["w"][1] - MONTAGE_OPEN_S)
    a, b = last["span"]
    last["w"] = [max(a, min(last["w"][0], b - MONTAGE_CLOSE_S)),
                 min(b, max(last["w"][1], last["w"][0] + MONTAGE_CLOSE_S))]
    out = []
    for c in chosen:
        w0, w1 = round(c["w"][0], 2), round(c["w"][1], 2)
        if out and w0 <= out[-1][1] + 0.05:
            w0 = round(out[-1][1] + 0.05, 2)     # never one long hold
        if w1 - w0 >= 0.3:
            out.append([w0, w1])
    return out if len(out) >= 4 else None


def montage_scout(ctx, windows, spend, title_only=False, boxes=False):
    """(windows, title) after the trial model's look at one frame of every
    picked window: the poster title (1-3 words, faithful to what is
    visible), the payoff shot to end on, the broken or off-topic shots to
    drop. The windows unchanged and no title without the lane or a usable
    answer. ``boxes`` (Q6, engine accounts' montages): the same look also
    boxes each shot's subject (ctx.first_cut_montage_boxes, for the crop),
    the payoff must show the subject, and the payoff window is left to
    montage_cut.hit_end (no 1 s hold)."""
    import base64
    import subprocess
    import claude_lane
    model = getattr(ctx, "first_cut_model", None)
    if not model or not claude_lane.available():
        return windows, None
    try:
        path = ctx.proxy_path()
    except Exception:
        return windows, None
    parts, sizes = [], {}
    for n, (a, b) in enumerate(windows, 1):
        try:
            jpg = subprocess.run(
                ["ffmpeg", "-v", "error", "-nostdin", "-ss",
                 f"{(a + b) / 2:.2f}", "-i", path, "-frames:v", "1", "-vf",
                 "scale=256:-2", "-f", "image2", "-c:v", "mjpeg", "-q:v", "6",
                 "-"], capture_output=True, timeout=30).stdout
        except Exception:
            jpg = b""
        if not jpg:
            continue
        if boxes:
            import first_cut_visual as fcv
            sizes[n] = fcv._jpeg_size(jpg) or (256, 144)
            parts.append({"type": "text", "text":
                          f"Shot {n} ({sizes[n][0]}x{sizes[n][1]} px):"})
        else:
            parts.append({"type": "text", "text": f"Shot {n}:"})
        parts.append({"type": "image_url", "image_url": {
            "url": "data:image/jpeg;base64," + base64.b64encode(jpg).decode()}})
    if len(parts) < (6 if title_only else 8):
        return windows, None
    try:
        if boxes:
            import montage_cut
            system = montage_cut.SCOUT_SYSTEM
        else:
            system = _MONTAGE_TITLE_SYSTEM
        resp = claude_lane.create(
            model, [{"role": "system", "content": system},
                    {"role": "user", "content": parts}],
            max_tokens=3000, effort="low")
        text = resp.choices[0].message.content or ""
        spend["usd"] += float(getattr(resp.usage, "provider_cost_usd", 0) or 0)
        llm.record("first_cut_montage_scout", {"model": model,
                                                "shots": len(windows)},
                   {"content": text}, resp.usage)
        print(f"[first_cut] montage scout: {text.strip()[:240]}", flush=True)
        match = re.search(r"\{.*\}", text, re.S)
        pick = json.loads(match.group(0)) if match else {}
    except Exception as exc:  # noqa: BLE001 — no title is the fallback
        print(f"[first_cut] montage scout failed: {type(exc).__name__}: "
              f"{str(exc)[:160]}", flush=True)
        return windows, None
    out = list(windows)
    try:
        end = 0 if title_only else int(pick.get("end") or 0)
    except (TypeError, ValueError):
        end = 0
    drop = set()
    for d in pick.get("drop") or []:
        try:
            drop.add(int(d))
        except (TypeError, ValueError):
            continue
    kept_n = list(range(1, len(windows) + 1))
    if end >= max(4, int(0.6 * len(windows))):
        keep_n = [n for n in kept_n if n <= end and n not in drop]
        if len(keep_n) >= 4:
            kept_n = keep_n
            out = [windows[n - 1] for n in kept_n]
            if not boxes:
                a, b = out[-1]
                out[-1] = [a, round(max(b, a + MONTAGE_CLOSE_S), 2)]
    if boxes:
        import montage_cut
        got = montage_cut.parse_boxes(pick.get("boxes"), sizes)
        ctx.first_cut_montage_boxes = [(list(windows[n - 1]), got.get(n))
                                       for n in kept_n]
    t = " ".join(str(pick.get("title") or "").split()).strip(" .\"'")
    words = t.replace("*", "").split()
    if not 1 <= len(words) <= 3 or len(t) > 32 or re.search(r"\d", t):
        return out, None
    if t.count("*") != 2:
        t = t.replace("*", "")
        longest = max(reversed(t.split()), key=len)
        t = t.replace(longest, f"*{longest}*", 1)
    return out, t


# --------------------------------------------------------------------------
#  The chain
# --------------------------------------------------------------------------

def _run(ctx, steps, name, args, started):
    elapsed = time.monotonic() - started
    if elapsed > config.FIRST_CUT_MAX_S - _RENDER_RESERVE_S:
        steps.append({"tool": name, "skipped": "time"})
        return None
    t0 = time.monotonic()
    try:
        result = agent_tools.execute(ctx, name, args)
    except Exception as exc:  # noqa: BLE001 — one step never sinks the cut
        result = f"FAILED: {type(exc).__name__}: {str(exc)[:200]}"
    steps.append({"tool": name, "ok": not _failed(result),
                  "s": round(time.monotonic() - t0, 1),
                  "note": str(result or "")[:160]})
    return result


def _orientation(index):
    video = (index or {}).get("video") or {}
    try:
        w, h = float(video.get("width") or 0), float(video.get("height") or 0)
    except (TypeError, ValueError):
        return "unknown"
    if not w or not h:
        return "unknown"
    return "vertical" if h / w >= 1.6 else "other"


def _fitted(ctx):
    """The output frame fits the picture over bands (pad / pad_blur)."""
    try:
        frame = ctx.latest_edl()["json"].get("frame") or {}
    except Exception:
        return False
    return str(frame.get("mode") or "") in ("pad", "pad_blur")


def _clean_word(w):
    """A transcribed token that reads as a word in some script: letters
    (and their combining marks) with inner apostrophes or hyphens, not one
    character repeated ("ʰʰʰʰ"), no modifier-letter runs."""
    import unicodedata
    t = str(w or "").strip().strip(".,!?;:…\"'“”«»")
    if not t or len(t) > 24:
        return False
    letters = 0
    for ch in t:
        cat = unicodedata.category(ch)
        if cat in ("Lu", "Ll", "Lt", "Lo"):
            letters += 1
        elif cat.startswith("M") or ch in "'’-":
            continue
        else:
            return False
    return letters >= 1 and (len(t) <= 2 or len(set(t.lower())) > 1)


def _speech(index):
    """Real speech: at least 8 words, most of the longer tokens words (C2: a
    music promo's song transcribed as "ʰʰʰʰ", "ლ" was trimmed to a 2 s
    fragment and captioned with garbage glyphs). One-character tokens count
    for neither side."""
    words = [str(w.get("w") or "").strip() for w in (index or {}).get("words") or []]
    if len(words) < 8:
        return False
    longer = [w for w in words if len(w.strip(".,!?;:…")) >= 2]
    if not longer:
        return False
    return sum(1 for w in longer if _clean_word(w)) >= 0.6 * len(longer)


def _music_piece(index, duration, robust=False):
    """An already-finished piece over a song (a music promo, an animated
    invitation, a lyric edit): the audio has a beat and what is heard is
    sung or not speech at all (fewer than 1.2 words a second). The First
    cut keeps it whole (C2: a 39 s wedding invitation was trimmed to 21 s,
    ending before the names and the date)."""
    perception = (index or {}).get("perception") or {}
    try:
        bpm = float(perception.get("bpm") or 0)
    except (TypeError, ValueError):
        bpm = 0.0
    if bpm <= 0 and not perception.get("beats"):
        return False
    words = (index or {}).get("words") or []
    if not _speech(index):
        return bool(words)
    if robust:
        # Q1 (30 Oct): SUNG, measured on the voice itself (held words, an
        # unsure transcript, a chorus). The words-per-second rule below also
        # kept a slow narration over a music bed whole and uncaptioned.
        import first_cut_visual as fcv
        return float(duration or 0) <= 75.0 and fcv.sung(index)
    # sung, not spoken: slow words over a beat in a short piece (C2: a 100 s
    # narrated vlog with long pauses over room noise is NOT one)
    return float(duration or 0) <= 60.0 and \
        len(words) / max(1.0, float(duration or 0)) < 1.2


def _faceless_reframe_args(speech, faces, picked):
    """auto_reframe's arguments for footage that is not a screen and not a
    vertical talking head. Silent footage with no face fills the frame
    (crop), like the references' montages. QI (30 Oct): a highlight
    montage crops even when the face detector finds a face in some shots
    (a crowd, a control room); the montage test already found faces
    incidental (share < 0.35). With mode auto the game fixture came back
    small between blurred bands on the box harness (C2's Mac run found no
    face and cropped): judged 8.25 -> 6.25."""
    args = {"ratio": "9:16"}
    if not speech and (not faces or (picked or {}).get("how") == "motion"):
        args["mode"] = "crop"
    return args


def video_chain(ctx, worker_db, started, spend, whole=False):
    """The First cut's chain. whole=True (the trial turn's clean-up of the
    whole take, trial_turn.whole_cleanup): no best-moment pick, the source's
    own ends trimmed (a mic check, a trailing fragment) and the rest kept."""
    steps, applied = [], []
    duration = float(ctx.duration or 0)
    picked = None
    speech = _speech(ctx.index)
    ctx.first_cut_hook = None
    ctx.first_cut_beats = None
    # Q1 (30 Oct, first_cut_visual): trial accounts and new subscribers only
    q1 = bool(getattr(ctx, "first_cut_q1", False))
    ctx.first_cut_broll = None
    ctx.first_cut_cards = []
    early_bands = None
    if q1 and _speech(ctx.index) and _orientation(ctx.index) == "vertical" \
            and not _music_piece(ctx.index, duration, robust=True):
        f0 = _faces(ctx.index)
        if f0 and f0["share"] >= 0.5:
            # Q1: the band crop is written while the program is the whole
            # source, so its zoom spans every second of it; written after
            # the trim, a trial request that restored earlier footage fell
            # outside it and showed the bars (judges: "a small, banded
            # layout on the first frame and the last 0.5 s", twice)
            res, early_bands = deband(ctx, steps, started)
            if not _ok(res):
                early_bands = None
    if _music_piece(ctx.index, duration, robust=q1) and duration <= 120:
        # a finished piece over a song: whole, unchanged in time, no title
        # or captions over its own design; only a non-vertical frame is
        # fitted to 9:16 (crop for footage, never bands around a speaker)
        applied.append("kept whole (it already plays as a finished piece "
                       "over its song)")
        if _orientation(ctx.index) != "vertical" and not _screen_like(ctx.index):
            if _ok(_run(ctx, steps, "auto_reframe", {"ratio": "9:16"}, started)):
                applied.append("reframed to 9:16")
        return steps, applied, {"how": "finished_piece"}
    short_silent = False
    _f = _faces(ctx.index)
    montage = (not speech and duration >= MONTAGE_MIN_SOURCE_S
               and (not _f or _f["share"] < 0.35)
               and not _screen_like(ctx.index))
    if montage:
        # Silent footage (an event, gameplay, b-roll): the references cut
        # it to its strongest moving moments (recipe D), never the whole take
        curve = _motion_curve(ctx)
        shots = (ctx.index or {}).get("shots")
        if curve and q1:
            # Q6: the references' rhythm (0.3-0.5 s shots, several moments
            # of a long shot), opening on a strong moment, ending on a hit
            import montage_cut
            windows = montage_cut.windows(curve, shots, duration)
        else:
            windows = montage_windows(curve, shots, duration) if curve else None
        title = None
        if windows:
            windows, title = montage_scout(ctx, windows, spend, boxes=q1)
        if windows and curve and q1:
            windows = montage_cut.hit_end(curve, windows, shots, duration)
            windows = montage_cut.framable(
                windows, getattr(ctx, "first_cut_montage_boxes", None),
                montage_cut.crop_share(ctx.index))
            windows = montage_cut.fit_length(windows, shots, duration)
        if windows and _ok(_run(ctx, steps, "keep_segments",
                                {"segments": windows, "snap_to_words": False},
                                started)):
            applied.append("its strongest moments cut into a highlight")
            picked = {"how": "motion", "shots": len(windows),
                      "s": round(sum(b - a for a, b in windows), 2)}
            ctx.first_cut_hook = title
    elif not speech and duration >= 3.0:
        # a short silent clip keeps its whole take and gets a poster title
        # from the trial model's look (C2: a 15 s gym clip and a 17 s walk
        # in the snow got "I couldn't finish an automatic first cut")
        step = duration / 6.0
        _w, title = montage_scout(ctx, [[k * step, (k + 1) * step]
                                        for k in range(6)], spend,
                                  title_only=True)
        ctx.first_cut_hook = title
        short_silent = True
    elif duration > config.FIRST_CUT_SHORT_SOURCE_S and not whole:
        start, end, title, how = scout_range(ctx, worker_db, spend)
        picked = {"start_s": round(start, 2), "end_s": round(end, 2),
                  "title": title, "how": how}
        if not getattr(ctx, "first_cut_hook", None):
            ctx.first_cut_hook = _opening_hook(
                [r for r in _sentences(ctx.index, end)
                 if float(r["t0"]) >= start - 1e-3])
        result = _run(ctx, steps, "keep_segments",
                      {"segments": [[start, end]], "snap_to_words": True},
                      started)
        if _ok(result):
            applied.append("best moment")
    elif speech:
        start, end, how = trim_range(ctx, worker_db, spend)
        if whole:
            # the scout still names the hook and the beats; the span is the
            # whole take minus its junk ends
            rows = _sentences(ctx.index, duration + 1)
            if rows:
                first, last = _trim_pick(rows, duration)
                start, end, how = float(first["t0"]), float(last["t1"]), "whole"
        picked = {"start_s": round(start, 2), "end_s": round(end, 2),
                  "how": how}
        if start > 0.3 or end < duration - 0.6:
            if _ok(_run(ctx, steps, "keep_segments",
                        {"segments": [[start, end]], "snap_to_words": True},
                        started)):
                applied.append("a clean opening and ending")
    if speech:
        args = {"min_silence_s": PAUSE_CUT_S, "padding_s": PAUSE_PAD_S}
        if _talk_density(ctx) >= 1.2:
            # Narration over room tone or street sound is still speech with
            # pauses (the vlog fixture: every gap "non-quiet", nothing cut);
            # music and game audio never carry this many words a second.
            args["allow_nonquiet"] = True
        elif q1:
            import first_cut_visual as fcv
            if fcv.spoken_slowly(ctx.index):
                # Q1: a slow narration over a bed (music, room): its long
                # pauses are not quiet, and they are still dead air
                args.update(allow_nonquiet=True, min_silence_s=0.45)
        if _ok(_run(ctx, steps, "cut_silences", args, started)):
            applied.append("pauses trimmed")
        if _ok(_run(ctx, steps, "remove_filler_words", {}, started)):
            applied.append("filler words removed")
        if q1 and _ok(drop_stutters(ctx, steps, started)):
            applied.append("stutters cut")
        shape_ends(ctx, steps, started)
    faces = _faces(ctx.index)
    talking = bool(speech and faces and faces["share"] >= 0.5)
    if q1 and speech and not talking:
        import first_cut_visual as fcv
        # Q6: the last sentence is complete and its picture live (u4899
        # ended on "a fitting piece" over the source's empty grey fade)
        import ending_guard
        ending_guard.guard(ctx, steps, started, _run)
        fcv.muted_tail(ctx, steps, started, _run)
    vertical_source = _orientation(ctx.index) == "vertical"
    vertical_out = vertical_source
    screen_kept = False
    if not vertical_source:
        if _screen_like(ctx.index) and not talking:
            # Screen text at 9:16 is unreadable (bake-off vfr runs); the
            # references keep tutorials landscape (R07, R11).
            applied.append("the screen's own shape kept so its text reads")
            screen_kept = True
            if q1:
                pass    # the travelling camera comes after the hook (below)
            elif _ok(screen_focus(ctx, steps, started)):
                applied.append("pushed in on its content")
        else:
            # Footage with no face and no speech (a montage, an event, a
            # landscape) fills the frame like the references' montages
            # (R01, R04): auto mode would fit it small over blurred bands.
            args = _faceless_reframe_args(speech, faces, picked)
            if q1 and (picked or {}).get("how") == "motion":
                # Q6: a montage's crop sits on each shot's subject as the
                # scout boxed it (it was the frame's centre: no face to find)
                import montage_cut
                if montage_cut.follow(ctx, steps, started, _run):
                    applied.append("reframed to 9:16 on the subject")
                    vertical_out = True
            elif _ok(_run(ctx, steps, "auto_reframe", args, started)):
                applied.append("reframed to 9:16")
                vertical_out = True
                if q1 and speech and not talking and _fitted(ctx):
                    # Q1: a narrated landscape animation sat small between
                    # blurred bands (u4899): crop it, following the action
                    import first_cut_visual as fcv
                    if fcv.action_follow(ctx, steps, started, _run):
                        applied.append("the crop follows the action")
    if speech and (q1 or not vertical_out):
        # Q1: every speech cut is mastered (talk10best measured -15.6 LUFS)
        # 9:16 is mastered by default; a kept landscape/screen shape is not
        # (C2: the screen recording measured -17.1 LUFS; references -14)
        _run(ctx, steps, "set_master_loudness", {"enabled": True}, started)
    bands = early_bands
    if early_bands:
        applied.append("the source's bars cropped")
    elif vertical_out and talking:
        res, bands = deband(ctx, steps, started)
        if not _ok(res):
            bands = None
        else:
            applied.append("the source's bars cropped")
    if talking and len(_keep(ctx) or []) > 1 and not q1:
        _run(ctx, steps, "conceal_jump_cuts", {}, started)
    if talking and not q1:
        # The references change the picture every 2-3 s; on a single take
        # the honest events are a punch on a stressed word and the key
        # phrase slammed in type (beat_slams), kept sparse (a punch about
        # every 7 s; the owner: restraint, never one per cut). On top of the
        # band crop the punch is shallow: stacked on its 1.4x zoom a full
        # punch pushed the chin out of frame (C1).
        prog = sum(e - s for s, e in (_keep(ctx) or []))
        if prog >= 12:
            args = {"count": max(2, int(prog // 7))}
            if bands:
                args["strength"] = 0.08
            _run(ctx, steps, "punch_in_on_emphasis", args, started)
    y = chest_y(faces, bands) if (talking and vertical_out) else None
    hook, hook_end = getattr(ctx, "first_cut_hook", None), HOOK_S
    hook_y, mute_under = y, True
    subject = []
    silent_beats = None
    if q1 and short_silent and vertical_source and not screen_kept:
        # QB: one look at the subject of a short silent clip: where the title
        # may sit without crossing it, and (below) the camera that follows it
        import short_clip
        import silent_cut
        if duration >= silent_cut.SILENT_CUT_MIN_S:
            # Q6: a longer one is cut into its beats (jump cuts on its clean,
            # strong moments) before the title and the camera land
            looks = silent_cut.look(ctx, duration, spend)
            segs = silent_cut.plan_for(ctx, looks, duration)
            if segs and silent_cut.apply(ctx, steps, started, _run, looks,
                                         segs):
                silent_beats = (looks, segs)
                applied.append("cut into its strongest moments")
                if hook:
                    hook_y = silent_cut.title_for(ctx, looks, segs)
            elif looks:
                subject = [(t, b) for t, b, ok, _p in looks if ok and b]
        if not silent_beats and not subject:
            subject = short_clip.whole_looks(ctx, spend)
        if subject and hook:
            hook_y = short_clip.title_y(subject)
    # (Q1 tried the scout's promise above the head: the band above a
    # vertical talking head is the brand corner mark's (y 0.115-0.192), and
    # the corner-mark guard removed every such title, scoring hook 2.5-4.
    # The spoken line stays the title, at the chest; the scout's opening
    # rule keeps it a self-contained claim.)
    if talking:
        opening = spoken_opening(ctx)
        hook, hook_end = opening if opening else (None, HOOK_S)
        # the references hold the opening title 2.5-3.5 s (C2: it left at
        # 1.6 s on a fast speaker); it owns its zone, the captions wait
        hook_end = round(min(max(hook_end + 0.15, HOOK_MIN_S), HOOK_MAX_S), 2)
    if hook:
        params = hook_params(hook, hook_y, vertical_out)
        if q1 and screen_kept:
            # judges: the title on the busy docs page was low-contrast
            params["scrim"] = 0.85
        args = {"template": "hook_title", "start": 0.0, "end": hook_end,
                "params": params,
                # one reading task in the first seconds (captions.
                # hook_owns_zone): no caption stacked under the title
                "mute_captions": mute_under,
                "purpose": "the opening line as a poster title (references: "
                           "a poster-scale title by 0.3 s, held 2.5-3.5 s)"}
        if _ok(_run(ctx, steps, "add_motion_graphic", args, started)):
            applied.append("a hook title")
    else:
        hook_end = 0.0
    if silent_beats:
        # Q6: each beat framed on the subject, an eased push-in under the
        # title, the end beat a snap in to the hero framing with one flash
        import silent_cut
        if _ok(silent_cut.camera(ctx, steps, started, _run, *silent_beats)):
            applied.append("a camera on the subject that ends on a "
                           "punch-in")
    elif subject:
        # QB: a short silent clip is not "raw footage with a title": a
        # camera finds its subject, pushes in, follows it and ends moving
        import short_clip
        if _ok(short_clip.camera(ctx, steps, started, _run, spend,
                                 samples=subject)):
            applied.append("a camera that follows the subject")
    cutaways = []
    if q1 and talking:
        # Q1: B-roll on the concrete things the speaker names, first (the
        # slams and cards keep clear of it)
        import first_cut_visual as fcv
        moments = getattr(ctx, "first_cut_broll", None) or []
        if moments:
            cutaways = fcv.place_broll(ctx, steps, started, _run, moments,
                                       max(hook_end, HOOK_S), spend)
            if cutaways:
                applied.append("b-roll of what is said")
    if q1 and screen_kept:
        import first_cut_visual as fcv
        if _ok(fcv.screen_follow(ctx, steps, started, _run, spend,
                                 max(hook_end, 1.5))):
            applied.append("a camera that follows what is discussed")
    if speech and not screen_kept:
        beats = list(getattr(ctx, "first_cut_beats", None) or [])
        if len(beats) < 3:
            # the scout's beats first, the spoken figures after them
            beats += [b for b in number_beats(ctx.index)
                      if all(abs(b[0] - a[0]) > 2.0 for a in beats)]
        if beat_slams(ctx, steps, started, beats, hook_end, y,
                      avoid=cutaways):
            applied.append("key lines in big type")
    if q1 and talking:
        import first_cut_visual as fcv
        busy = sorted(cutaways + list(ctx.first_cut_cards or []))
        aim = fcv.face_aim(ctx.index, bands, vertical_source)
        if fcv.cut_ins(ctx, steps, started, _run, busy, aim,
                       max(hook_end, 1.0), bands=bool(bands)):
            applied.append("a closer camera at the turns")
        if len(_keep(ctx) or []) > 1:
            _run(ctx, steps, "conceal_jump_cuts", {}, started)
    elif q1 and speech and not screen_kept and vertical_out:
        # Q1: a narrated animation's long shots get the same rhythm, softer
        # (judges: "one cut every 4.5 s across long camera moves")
        import first_cut_visual as fcv
        if fcv.cut_ins(ctx, steps, started, _run,
                       sorted(ctx.first_cut_cards or []), (0.5, 0.5),
                       max(hook_end, 1.0), levels=fcv.CUT_IN_SOFT):
            applied.append("closer framings at the turns")
    if q1 and bands:
        import first_cut_visual as fcv
        fcv.refresh_band_crop(ctx, steps, started, _run)
    if q1 and (picked or {}).get("how") == "motion":
        # Q6: the montage ends on its hit with one white flash (R01)
        import montage_cut
        montage_cut.end_flash(ctx, steps, started, _run)
    if speech:
        style = {"preset": "reels", "motion_look": "editorial",
                 "size_scale": CAPTION_SCALE}
        words_per = CAPTION_WORDS
        if q1:
            import first_cut_visual as fcv
            style, words_per = fcv.caption_style(
                style, talking=talking, screen=screen_kept,
                vertical=vertical_out)
        if y is not None:
            style["anchor_y"] = y
        if _ok(_run(ctx, steps, "add_captions",
                    {"mode": "from_transcript", "style": style,
                     "max_words_per_caption": words_per},
                    started)):
            applied.append("captions")
            if q1:
                import first_cut_visual as fcv
                fcv.drop_tail_words(ctx, steps, started, _run)
    return steps, applied, picked


TOUR_STEPS = [{"do": "wait", "seconds": 0.8}, {"do": "tour", "by": 2, "seconds": 18}]
DEMO_ZOOM = 0.25           # showcase push-ins on a phone-layout capture
DEMO_TITLE_S = 2.5         # the opening title over the hero (references 2.5-3.5;
                           # the judge: "the first visual change by ~2-2.5 s")
DEMO_END_PUSH_S = 2.2      # the last seconds: a slow push, never a held page
# The tour's beats as push-ins (Q2, 30 Oct 2026). Each beat gets ONE eased
# push that frames the thing pointed at (a section's heading, a card, the
# closing call to action) whole, and lets go before the page moves on: never a
# path that glides sideways from one card to the next (the judge, every
# website run), never a push that cuts the words it is about at the frame edge.
DEMO_PUSH_RAMP_S = 0.6     # in and out: eased, never a snap
DEMO_PUSH_MIN = 0.08
DEMO_PUSH_MAX = {"look": 0.22, "hover": 0.22, "toggle": 0.22, "finale": 0.26}
DEMO_PUSH_MIN_S = 1.1      # a beat shorter than this gets no push
DEMO_PUSH_HOLD_S = 3.2     # the longest a push holds before letting go
# The title's opening: the hero is blurred under it, so the site's own
# headline never collides with it (the judge, every website run: "dim or blur
# the hero headline under MEET VALMERA"), then sharpens as the title leaves.
DEMO_TITLE_BLUR = ((0.0, 2.3, 1.0), (0.0, 2.3, 0.97), (2.3, 2.45, 0.4))
# (the first two stack into a deep blur; their intensities differ because an
# identical second call is refused as a replay; the title has left before it
# sharpens:
# the judge saw the leaving title over the sharp headline at 2.6 s)
DEMO_CHAPTERS = 4          # section names set over the page, at most
DEMO_SIGNOFF_S = 1.6       # a top-to-bottom tour ends on the site's address


def blank_spans(path, fps=10.0, width=48, least_s=0.2):
    """[[start, end]] seconds of a capture where the picture is one flat
    colour (a page loading white or black between pages; C2 judge: "a blank
    white screen around 4.8-5.6 s"). Never the first or last half second."""
    import subprocess
    import numpy as np
    try:
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-nostdin", "-i", path, "-an", "-vf",
             f"fps={fps},scale={width}:-2,format=gray", "-f", "rawvideo",
             "-pix_fmt", "gray", "-"], capture_output=True, timeout=120).stdout
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30).stdout.strip().split(",")
        h = int(round(width * int(probe[1]) / int(probe[0])))
        h += h % 2
    except Exception:
        return []
    n = len(raw) // (width * h)
    if n < 10:
        return []
    frames = np.frombuffer(raw[:n * width * h], np.uint8).reshape(n, h, width)
    flat = frames.reshape(n, -1).std(axis=1) < 6.0
    # QB: a page loading under its own header (the bar and the logo are
    # drawn, the body is still white or black) is blank too (klosmo, judge:
    # "a white loading flash at ~8 s"): the body, below the header, is one
    # flat bright or dark colour
    body = frames[:, int(0.18 * h):int(0.95 * h), :].reshape(n, -1)
    mean = body.mean(axis=1)
    flat |= (body.std(axis=1) < 3.0) & ((mean > 235) | (mean < 22))
    dur = n / fps
    out, k = [], 0
    while k < n:
        if not flat[k]:
            k += 1
            continue
        j = k
        while j < n and flat[j]:
            j += 1
        a, b = k / fps, j / fps
        if b - a >= least_s and a > 0.5 and b < dur - 0.5:
            out.append([round(a, 2), round(b, 2)])
        k = j
    return out


def cut_spans(blank, events, least_s=0.8):
    """[[start, end]] capture seconds to cut: the blank frames of a page
    loading and the stale spans the recorder noted (the old page held while
    a slow site loads the next, kind 'stale'), merged, in time order. A stale
    span shorter than ``least_s`` is left alone."""
    spans = [list(map(float, s)) for s in blank or []]
    for e in events or []:
        if e.get("kind") == "stale" and e.get("end") is not None:
            a, b = float(e["t"]), float(e["end"])
            if b - a >= least_s:
                spans.append([a, b])
    spans.sort()
    out = []
    for a, b in spans:
        if out and a <= out[-1][1] + 0.05:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [[round(a, 2), round(b, 2)] for a, b in out]


def _brand(rec, url):
    """The site's own name: its og:site_name, else the part of its page
    title that names the host ("Pricing | Cal.com" is Cal.com, not
    "Pricing"; Q2 judge: "MEET PRICING"), else the host's name."""
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    label = host.split(".")[0]
    site = " ".join(str((rec or {}).get("site_name") or "").split())
    if site and len(site) <= 34:
        return site
    title = str((rec or {}).get("page_title") or "")
    parts = [p.strip() for p in re.split(r"\s+[|\-–—:·•]\s+", title) if p.strip()]
    squash = lambda t: re.sub(r"[^a-z0-9]", "", t.lower())  # noqa: E731
    for part in parts:
        if label and squash(label) in squash(part) and len(part) <= 24:
            return part
    if parts and len(parts[0]) <= 34 and len(parts[0].split()) <= 4:
        # "Sparkle & Loop - Premium Artificial Jewellery Store": the name
        # leads the title (the host squashes it to "sparklenloop")
        return parts[0]
    return label.capitalize() if label else ""


# "Meet" in the request's own language (the judge on a Portuguese brief: "make
# the hook Portuguese, e.g. CONHEÇA o PhMath"). The language is read from the
# request's common words; English when unsure.
_MEET = {"pt": "Conheça", "es": "Conoce", "fr": "Découvrez", "de": "Entdecke",
         "it": "Scopri", "nl": "Ontdek", "tr": "Tanışın"}
_LANG_WORDS = {
    "pt": ("você", "vídeo", "seções", "com", "para", "uma", "não", "em", "formato", "aproximadamente", "e", "os", "as", "do", "da"),
    "es": ("el", "la", "los", "las", "una", "para", "con", "video", "sección", "secciones", "y", "del", "segundos", "por"),
    "fr": ("le", "la", "les", "une", "pour", "avec", "vidéo", "et", "des", "du", "secondes", "sur"),
    "de": ("der", "die", "das", "und", "mit", "für", "ein", "eine", "sekunden", "seite", "von"),
    "it": ("il", "lo", "gli", "una", "per", "con", "e", "della", "secondi", "sito"),
    "nl": ("de", "het", "een", "voor", "met", "en", "van", "seconden"),
    "tr": ("ve", "bir", "için", "ile", "saniye", "sitesi"),
}


def request_lang(text):
    """'pt', 'es', ... when the request is clearly in that language, else
    None (English, or too short to tell)."""
    words = re.findall(r"[^\W\d_]+", re.sub(r"https?://\S+", " ", str(text or "")).lower())
    if len(words) < 6:
        return None
    eng = sum(w in ("the", "and", "of", "a", "to", "with", "for", "my", "make", "video", "record") for w in words)
    best, score = None, 0
    for lang, stop in _LANG_WORDS.items():
        n = sum(w in stop for w in words)
        if n > score:
            best, score = lang, n
    return best if score >= 3 and score > eng else None


def _site_hook(rec, url, lang=None):
    """The opening title: "Meet / *Brand*" (heavy sans plus italic serif; the
    request's language for "Meet"), or, for a page the request points at
    ("the pricing page"), "Brand / *Pricing*". A long name sets on two serif
    lines so it stays poster-sized (the judge: "Decentralization Research
    Center" was set too small). Never the site's <h1>: the hero shows that
    headline itself, so the old title doubled it (C2 judge, five runs)."""
    from urllib.parse import urlparse
    brand = _brand(rec, url)
    if not brand:
        return None
    meet = _MEET.get(lang or "", "Meet")
    words = brand.split()
    if len(brand) > 16 and len(words) >= 2:
        half = max(1, len(words) // 2 + (len(words) % 2 and len(words[0]) < 8))
        return (f"{meet} / *{' '.join(words[:half])}* / "
                f"*{' '.join(words[half:])}*")
    path = [p for p in (urlparse(url).path or "").split("/") if p]
    page = re.sub(r"[-_]+", " ", path[-1]).strip() if path else ""
    page = re.sub(r"\.(html?|php)$", "", page, flags=re.I)
    if page and page.lower() not in ("index", "home", "en") and \
            1 <= len(page.split()) <= 2 and page.replace(" ", "").isalpha():
        return f"{brand} / *{page.title()}*"
    return f"{meet} / *{brand}*"


def demo_chapters(events, prog, title_s=DEMO_TITLE_S, most=None):
    """[(start, end, label, n)] the tour's sections, each opening on its name
    for about a second over the softened page (the judge, four of six runs:
    "1-3 word section labels in the hook's type pairing"): the window from
    the section beat to its heading's beat, at most DEMO_CHAPTERS, never
    over the opening title."""
    out = []
    evs = list(events or [])
    for k, e in enumerate(evs):
        if e.get("kind") != "section" or not str(e.get("label") or "").strip():
            continue
        t = float(e.get("t") or 0)
        if t < title_s + 0.5 or len(out) >= (most or DEMO_CHAPTERS):
            continue
        nxt = [float(x.get("t") or 0) for x in evs[k + 1:]
               if x.get("kind") in ("look", "hover", "toggle", "scroll", "nav",
                                    "click", "finale")]
        end = min(nxt[0] if nxt else t + 1.0, t + 1.2, prog - 0.5)
        if end - t < 0.6:
            continue
        label = " ".join(str(e["label"]).split())[:28]
        out.append((round(t, 2), round(end, 2), label, len(out) + 1))
    return out


def chapter_title(label, n=None):
    """The section's own name (the site's words) in the hook's two families:
    "Stripe for / *platforms*", "*Pricing*". No numbers: a blank page-load
    cut can drop a chapter, and the judge read a lone "02" (and a lone "01")
    as a broken system."""
    words = " ".join(re.sub(r"[*/]", " ", str(label or "")).split()).split()
    if not words:
        return ""
    if len(words) == 1:
        return f"*{words[0]}*"
    return " ".join(words[:-1]) + f" / *{words[-1]}*"


def demo_pushes(events, prog, vertical=True, title_s=DEMO_TITLE_S,
                finale_hold=True):
    """[{start, end, strength, rect, mode, ramp_s, purpose}] one eased
    push-in per beat of a tour's event track (look / hover / finale): from
    the beat until just before the page moves again, framing the beat's
    element whole. The finale's is a slow push_in to the end. Capture time is
    program time (the capture is placed at 0)."""
    # the page moving again, or the next beat starting, ends a push
    moves = sorted(float(e["t"]) for e in events or []
                   if e.get("kind") in ("scroll", "nav", "click", "finale",
                                        "section"))
    # the first push may start as the title leaves (the hero's call to
    # action, pressed right after the reveal)
    out, last_end = [], title_s - 0.3
    for e in events or []:
        kind = e.get("kind")
        if kind not in DEMO_PUSH_MAX or "x" not in e:
            continue
        t = float(e.get("t") or 0)
        if t < title_s - 0.1:
            continue
        s = max(t - 0.05, last_end + 0.3)
        if kind == "finale" and finale_hold:
            end = prog
        else:
            nxt = [m for m in moves if m > t + 0.05]
            end = min((nxt[0] - 0.08) if nxt else prog, s + DEMO_PUSH_HOLD_S)
        end = min(end, prog)
        if end - s < (0.9 if kind == "finale" else DEMO_PUSH_MIN_S):
            continue
        box = e.get("box")
        if box and len(box) == 4:
            x0, y0, x1, y1 = (float(v) for v in box)
            hb = e.get("head_box")
            if kind == "finale" and hb and len(hb) == 4:
                # the closing block's heading stays whole in the push too
                x0, y0 = min(x0, float(hb[0])), min(y0, float(hb[1]))
                x1, y1 = max(x1, float(hb[2])), max(y1, float(hb[3]))
        else:
            # a beat with no element: the whole width stays in frame (Q2
            # judge: a push on a bare point "cuts the text's left edge")
            x0, x1 = 0.03, 0.97
            y0 = max(0.0, float(e["y"]) - 0.12)
            y1 = min(1.0, float(e["y"]) + 0.12)
        small = (x1 - x0) * (y1 - y0) < 0.03 and kind in ("hover", "toggle")
        if kind == "finale" or (vertical and (x1 - x0 > 0.55 or small)):
            # a call to action's row (two buttons side by side), a wide
            # block, or a button off to one side: framed symmetrically about
            # the centre, so the page's left-aligned headings never lose
            # their first letters (the judge: "'he Focus Zone', 'OOR 01'")
            x0, x1 = min(x0, 1.0 - x1), max(x1, 1.0 - x0)
        elif vertical and kind == "look":
            # a heading: framed from the page's own left margin
            x0 = min(x0, 0.03)
        mx, my = (0.02, 0.05) if vertical else (0.04, 0.06)
        rect = [max(0.0, x0 - mx), max(0.0, y0 - my),
                min(1.0, x1 + mx), min(1.0, y1 + my)]
        rw, rh = rect[2] - rect[0], rect[3] - rect[1]
        fit = 1.0 / max(rw, rh, 0.05) - 1.0
        st = round(min(max(fit, DEMO_PUSH_MIN), DEMO_PUSH_MAX[kind]), 2)
        if fit < DEMO_PUSH_MIN - 0.03:
            # the element already fills the frame: a push would cut it
            continue
        push = {"start": round(s, 2), "end": round(end, 2), "strength": st,
                "rect": [round(v, 3) for v in rect],
                # the finale eases in over half a second and HOLDS to the
                # end (the judge: "a push-in onto Start now, held live",
                # not "a 3 s slow drift")
                "mode": "punch" if kind == "finale" else "ease",
                "ramp_s": 0.5 if kind == "finale" else
                round(min(DEMO_PUSH_RAMP_S, (end - s) / 3.0), 2),
                "purpose": {"look": "the section's own heading",
                            "hover": "the thing the tour points at",
                            "toggle": "the control the tour clicks",
                            "finale": "the closing call to action, live to "
                                      "the end"}[kind]}
        out.append(push)
        last_end = end
    return out


_SCROLL_ASK = re.compile(r"\bscroll", re.I)
_WHOLE_PAGE = re.compile(r"top to bottom|from the top|to the bottom|whole page|entire page|"
                         r"full page|de cima|hasta abajo|до низу", re.I)
_LANDSCAPE = re.compile(r"\b(landscape|horizontal|16:9|widescreen)\b", re.I)
_SECONDS = re.compile(r"(\d{2,3})\s*[- ]?\s*(?:seconds?|secs?|segundos?|s\b)", re.I)


def url_hints(text):
    """What a website request asks for, read deterministically from its
    words (C2, 29 Oct: real requests carry them; the First cut ignored
    them): the orientation, a top-to-bottom scroll instead of clicks into
    sections, a longer piece, and words the tour should prefer among the
    site's own links. {} for none."""
    text = str(text or "")
    out = {}
    land = _LANDSCAPE.search(text)
    port = re.search(r"9\s*:\s*16|vertical|portrait", text, re.I)
    if land and (not port or land.start() < port.start()):
        # the first shape named is the one asked for ("a 16:9 promo, 9:16
        # optional"; C2 real request)
        out["orientation"] = "landscape"
    if _SCROLL_ASK.search(text) and _WHOLE_PAGE.search(text):
        out["mode"] = "scroll"
    secs = [int(m.group(1)) for m in _SECONDS.finditer(text)]
    if secs and 10 <= secs[0] <= 180:
        out["seconds"] = secs[0]
    words = re.sub(r"https?://\S+", " ", text)
    words = [w for w in re.findall(r"[^\W\d_]{4,}", words)]
    if words:
        out["prefer"] = " ".join(words)[:1600]
    return out


_TILE = re.compile(r"^\s*record my website\b.*polished showcase", re.I | re.S)


def is_brief(text):
    """A website request with its own creative brief (sections to open, a
    length, an ending), not the tile's template or a bare URL: the First cut
    shows a demo at once and the trial agent then works through the brief
    (C2: real requests scored 3.5-5 as "not what the user asked")."""
    text = str(text or "")
    if not text or _TILE.search(text):
        return False
    words = re.sub(r"https?://\S+", " ", text).split()
    return len(words) >= 20


_RTL = re.compile(r"[\u0590-\u08FF]")
_BRAND_BG = (("dark green", "#0E2A1E"), ("forest green", "#1F3B2D"),
             ("burgundy", "#4A0E1E"), ("navy", "#0E1A33"),
             ("dark blue", "#0E1A33"), ("black", "#0A0A0A"))


def brand_colors(text):
    """(background, text, accent) of a closing card from the colours the
    brief names (dark green, burgundy, gold...): the first dark colour named
    is the ground, the cream is the type, gold the accent when named."""
    low = str(text or "").lower()
    found = sorted((low.find(name), hex_) for name, hex_ in _BRAND_BG
                   if name in low)
    bg = found[0][1] if found else BEAT_CARD
    return bg, "#F3E9D2", "#D4A94F" if "gold" in low else None


def rtl_closing_card(ctx, steps, started, phrase, domain, text):
    """The brief's quoted closing line in a right-to-left script (Arabic,
    Hebrew) as a plain title card in the brief's own colours, big, with the
    site's address under it. The title's per-word animation breaks such
    letters' joining, so the line is never set in the poster title; left to
    the model it came out tiny and static for 7 s (the judge: "a tiny
    motionless text card", osai.ie)."""
    import schemas
    try:
        at = float(schemas.program_duration(ctx.latest_edl()["json"])) - 0.01
    except Exception:
        return None
    bg, fg, accent = brand_colors(text)
    at = round(max(at, 0.5), 2)
    args = {"text": phrase, "at_output_s": at,
            "duration_s": 2.6, "template": "title", "bg_color": bg,
            "color": fg, "size_scale": 1.4, "entrance": "fade", "exit": "none"}
    if accent:
        args["accent_color"] = accent
    res = _run(ctx, steps, "add_title_card", args, started)
    if domain and _ok(res):
        # the address under it as its own line: the card's subtitle slot
        # stacks by measuring the title, and a large right-to-left line
        # came out with the address burned through its middle (QB, 30 Oct)
        _run(ctx, steps, "add_text", {
            "text": domain, "start": round(at + 0.3, 2),
            "end": round(at + 2.6, 2), "template": "subtitle", "x": 0.5,
            "y": 0.68, "color": accent or fg, "entrance": "fade",
            "exit": "none", "mute_captions": True}, started)
    return res


_QUOTED = re.compile(r"[\"“”«»„]([^\"“”«»„]{2,48})[\"“”«»„]")
_END_WORDS = re.compile(r"\b(end|ending|ends|final|finish|close|closing|call to action|cta|"
                        r"termin|finaliz|encerr|conclu|termine|acabe)", re.I)


def closing_line(text):
    """The closing line a brief asks for in its own words, quoted ("a final
    “Discover KLOSMO” call to action", "End with ... the phrase "قصتك تنتمي
    إلى هنا" and OSAI.ie"), or None. Only a quoted phrase in a sentence about
    the ending: never invented."""
    text = str(text or "")
    for sentence in re.split(r"(?<=[.!?\n])\s+", text):
        if not _END_WORDS.search(sentence):
            continue
        m = _QUOTED.search(sentence)
        if m:
            phrase = " ".join(m.group(1).split()).strip(" .,:;")
            if phrase and not phrase.lower().startswith("http"):
                return phrase
    return None


def closing_title(phrase, domain=None):
    """The closing line as the title's two families: the phrase in the
    heavy sans with its last word in the italic serif, the site's address
    under it in the serif when the brief names it."""
    words = re.sub(r"[*/]", " ", str(phrase or "")).split()
    if not words:
        return None
    if re.search(r"[\u0590-\u08FF]", " ".join(words)):
        # a right-to-left line (Arabic, Hebrew): the title's per-word
        # animation breaks its letter joining (the judge: "renders as broken
        # glyphs"), so the First cut leaves it to a plain text card
        return None
    if domain:
        return " ".join(words) + f" / *{domain}*"
    if len(words) == 1:
        return f"*{words[0]}*"
    return " ".join(words[:-1]) + f" / *{words[-1]}*"


def tour_sections(seconds):
    """How many section pages a tour of this length opens: about one per
    5-6 s after the hero and the finale (a 60 s brief names five pages; C2:
    "only 2 of 3 sections" in 40 s)."""
    secs = float(seconds or 18)
    return 2 if secs <= 22 else 3 if secs <= 34 else 4 if secs <= 46 else 5


def tour_steps(hints, brief=False):
    secs = min(max(float((hints or {}).get("seconds") or 18), 12.0), 60.0)
    if brief and secs >= 30:
        # "about 40 seconds" with a page, a tool page or two and a finale:
        # a tenth more time keeps every page the brief names in the cut
        # (phmath: the second named tool was the one that did not fit)
        secs = min(round(secs * 1.1, 1), 60.0)
    tour = {"do": "tour", "by": tour_sections(secs), "seconds": secs}
    if (hints or {}).get("mode") == "scroll":
        tour["mode"] = "scroll"
    if (hints or {}).get("prefer"):
        tour["prefer"] = hints["prefer"]
    if brief:
        # QB: a brief's own words guide the tour (brief_tour): its named
        # pages and the cards on them by word stem, the time shared by them
        tour["brief"] = True
    return [{"do": "wait", "seconds": 0.8}, tour]


def url_chain(ctx, worker_db, url, started, text=None):
    steps, applied = [], []
    hints = url_hints(text)
    brief = is_brief(text)
    tour = tour_steps(hints, brief=brief) if text else TOUR_STEPS
    result = _run(ctx, steps, "record_website_demo",
                  {"url": url, "orientation": hints.get("orientation", "portrait"),
                   "steps": tour}, started)
    key, rec = None, None
    for asset in reversed(getattr(ctx, "web_recordings", None) or []):
        key = (asset.get("storage_key") if isinstance(asset, dict)
               else getattr(asset, "storage_key", None)) or key
        if key:
            rec = asset if isinstance(asset, dict) else None
            break
    if not key:
        match = re.search(r"storage_key=(\S+?\.mp4)", str(result or ""))
        key = match.group(1) if match else None
    if _ok(result):
        applied.append("website recorded")
    vertical = hints.get("orientation", "portrait") != "landscape"
    if key:
        # placed whole, with no zooms of its own: the push-ins come from the
        # tour's beats below (showcase_demo's travelling zoom glided sideways
        # from one card to the next)
        if _ok(_run(ctx, steps, "showcase_demo",
                    {"asset_key": key, "zooms": False}, started)):
            applied.append("placed as a vertical demo" if vertical
                           else "placed as a landscape demo")
            try:
                import schemas
                prog = float(schemas.program_duration(ctx.latest_edl()["json"]))
            except Exception:
                prog = 0.0
            events = list((rec or {}).get("events") or [])
            # a top-to-bottom tour that could not end on a call to action
            # ends on the site's name and address instead; a brief that
            # quotes its own closing line ends on that line
            from urllib.parse import urlparse
            domain = (urlparse(url).hostname or "").removeprefix("www.")
            asked_end = closing_line(text)
            end_title = None
            if asked_end:
                named = bool(domain) and domain.lower() in str(text).lower()
                end_title = closing_title(asked_end, domain if named else None)
            elif hints.get("mode") == "scroll" and not any(
                    e.get("kind") == "finale" and e.get("cta") for e in events):
                end_title = f"{_brand(rec, url)} / *{domain}*" if domain else None
            signoff = bool(end_title) and prog > 10
            pushes = demo_pushes(events, prog - (DEMO_SIGNOFF_S if signoff else 0.0),
                                 vertical) if prog else []
            for push in pushes:
                _run(ctx, steps, "add_zoom", dict(push), started)
            if pushes:
                applied.append("push-ins on each section")
            chapters = demo_chapters(events, prog,
                                     most=7 if brief else None) if prog else []
            if not signoff and not any(p["mode"] == "punch" for p in pushes) and \
                    prog > DEMO_END_PUSH_S + 6 and not any(
                        p["end"] > prog - DEMO_END_PUSH_S - 0.25 for p in pushes):
                _run(ctx, steps, "add_zoom", {
                    "start": round(prog - DEMO_END_PUSH_S, 2), "end": round(prog, 2),
                    "strength": 0.12, "mode": "push_in",
                    "purpose": "the ending keeps moving (no held page before "
                               "the end card)"}, started)
            # each section opens on its own name for about a second, over
            # the page softened the way the opening title is (never over
            # the site's own words)
            for a, b, label, n in chapters:
                # QB: a landscape page is dark more often than not (osai.ie),
                # and a name over a fully blurred dark page read as "a
                # near-empty dark interstitial" (judge, 4 of 4): a brief's
                # landscape chapters blur the page once, lightly, so the
                # page stays recognisable under its name
                soft = brief and not vertical
                for k in ((0.7,) if soft else (1.0, 0.97)):
                    _run(ctx, steps, "add_stylize", {
                        "kind": "dream_blur", "start": a,
                        "end": round(b - 0.12, 2), "intensity": k}, started)
                _run(ctx, steps, "add_stylize", {
                    "kind": "dream_blur", "start": round(b - 0.12, 2),
                    "end": b, "intensity": 0.3 if soft else 0.4}, started)
                if _ok(_run(ctx, steps, "add_motion_graphic", {
                        "template": "hook_title", "start": a, "end": b,
                        "params": dict(hook_params(chapter_title(label),
                                                   0.46, vertical),
                                       size=1.4 if vertical else 1.0,
                                       scrim=0.5),
                        "mute_captions": True,
                        "purpose": "the section's name"}, started)):
                    if "section names" not in applied:
                        applied.append("section names")
            if signoff:
                # the closing line over the last beat, the page softened
                # under it the way the opening title's is (the judge: "a
                # short brand line like sparklenloop.com as the closing
                # beat"; a brief's own quoted "Discover KLOSMO")
                a = round(prog - DEMO_SIGNOFF_S, 2)
                for k in (1.0, 0.97):
                    _run(ctx, steps, "add_stylize", {
                        "kind": "dream_blur", "start": a, "end": round(prog, 2),
                        "intensity": k}, started)
                if _ok(_run(ctx, steps, "add_motion_graphic", {
                        "template": "hook_title", "start": a, "end": round(prog, 2),
                        "params": dict(hook_params(end_title, 0.46, vertical),
                                       size=1.1 if vertical else 0.85,
                                       scrim=0.5, exit="none"),
                        "mute_captions": True,
                        "purpose": "the closing line"}, started)):
                    applied.append("the closing line")
            try:
                import media_cache
                local = media_cache.lease(key, ctx.workdir, "capture.mp4")
            except Exception:
                local = None
            # after everything placed in capture time: cutting a blank span
            # re-anchors the pushes, the names and the sign-off with it
            for a, b in reversed(cut_spans(
                    blank_spans(local) if local else [], events)):
                _run(ctx, steps, "cut_output_range",
                     {"start": a, "end": b}, started)
            hook = _site_hook(rec, url, request_lang(text))
            if hook:
                # ONE reading task at the opening: the hero blurs under the
                # poster title and sharpens as it leaves, so the site's own
                # headline never collides with it (C2 judge, every website
                # run; a full-frame dim read as "a title on black")
                for a, b, k in DEMO_TITLE_BLUR:
                    _run(ctx, steps, "add_stylize", {
                        "kind": "dream_blur", "start": a, "end": b,
                        "intensity": k}, started)
                if _ok(_run(ctx, steps, "add_motion_graphic", {
                        "template": "hook_title", "start": 0.0, "end": DEMO_TITLE_S,
                        "params": dict(hook_params(hook, 0.46, vertical),
                                       scrim=0.6),
                        "mute_captions": True,
                        "purpose": "the opening title: the site's name"},
                        started)):
                    applied.append("a title")
            if asked_end and end_title is None and _RTL.search(asked_end) \
                    and brief:
                # QB: a right-to-left closing line is a big plain card
                named_dom = bool(domain) and domain.lower() in str(text).lower()
                if _ok(rtl_closing_card(ctx, steps, started, asked_end,
                                        domain if named_dom else None, text)):
                    applied.append("the closing line")
    return steps, applied


def _summary(applied, duration_s, url=None):
    length = f"{int(round(duration_s))}-second " if duration_s else ""
    if url:
        shape = "" if any("landscape" in p for p in applied) else "vertical "
        return f"a {length}{shape}demo of {url}"
    parts = [p for p in applied if p != "a format pack"]
    detail = (", ".join(parts[:-1]) + " and " + parts[-1]) if len(parts) > 1 \
        else (parts[0] if parts else "")
    shape = "" if any("own shape" in p for p in applied) else "vertical "
    return f"a {length}{shape}version" + (f": {detail}" if detail else "")


def _message_text(worker_db, message_id):
    """The request's own words (the website tile's message), or None."""
    if not message_id:
        return None

    def read(conn):
        with conn.cursor() as cur:
            cur.execute("SELECT content FROM chat_messages WHERE id = %s",
                        (int(message_id),))
            row = cur.fetchone()
        if not row:
            return None
        return row.get("content") if isinstance(row, dict) else row[0]
    try:
        return worker_db.run(read)
    except Exception:
        return None


def run(worker_db, job, project, index_row):
    """Run one First cut. Returns the agent_turn result dict."""
    payload = job.get("payload") or {}
    started = time.monotonic()
    session_id = project["chat_session_id"]
    workdir = os.path.join(config.TMP_DIR, f"first_cut_{job['id']}")
    os.makedirs(workdir, exist_ok=True)
    ctx = agent_tools.ToolContext(worker_db, job, project,
                                  index_row["json"] if index_row else None,
                                  workdir)
    # Deterministic: no paid second-opinion critics or listening model, no
    # images queued for an agent (MCP's own settings), a render budget.
    ctx.sight_out = True
    ctx.audio_model_review = False
    ctx.request_preview_cap = max(1, config.FIRST_CUT_MAX_RENDERS)
    ctx.user_message = "First cut"
    ctx.credit_budget = None
    try:
        start_edl = ctx.latest_edl()
    except Exception:
        start_edl = None
    ctx.turn_start_edl = start_edl
    spend = {"usd": 0.0}
    url = payload.get("url")
    try:
        worker_db.run(dbx.record_client_event, job["user_id"],
                      job["project_id"], "free_result_started",
                      {"job_id": job["id"], "source": "url" if url else "video",
                       "source_s": round(float(ctx.duration or 0), 1)})
    except Exception:
        pass
    # The lane for the one model call (the long-source scout). A trial
    # account's First cut runs inside its trial budget on Claude (track D's
    # scope, metered into trial_budgets); a payer's scout is a metered Claude
    # call too while the credit switch is on. Otherwise the deterministic
    # pick. The turn plan stays the account's own: no tool in this chain may
    # resolve a paid lane for an account without a plan.
    import claude_trial
    try:
        subscribed, plan, _trialing = worker_db.run(dbx.user_billing,
                                                    job["user_id"])
    except Exception:
        subscribed, plan = False, "free"
    trial_scope, recorder = None, None
    decision = claude_trial.resolve(worker_db, job.get("user_id"), subscribed,
                                    project_id=project["id"], via="first_cut")
    if decision.kind == claude_trial.Decision.ACTIVE:
        trial_scope = decision.scope
        trial_scope.job_id = job.get("id")
        ctx.first_cut_model = trial_scope.model
        recorder = llm.get_recorder()
        if recorder is None:
            recorder = _ScopeRecorder()
            llm.set_recorder(recorder)
        claude_trial.activate(trial_scope, recorder)
    elif subscribed and claude_trial.credit_status(worker_db).get("on"):
        ctx.first_cut_model = claude_trial.flag(worker_db)["model"]
    # Q1 (30 Oct): the visual-variety devices reach trial accounts and NEW
    # subscribers only; a grandfathered payer's First cut is unchanged (D1)
    try:
        import first_cut_visual
        ctx.first_cut_q1 = bool(worker_db.run(
            first_cut_visual.engine_on, job.get("user_id"), subscribed))
    except Exception:
        ctx.first_cut_q1 = False
    llm.set_turn_plan(plan if subscribed else "", project_id=project["id"])
    try:
        if url:
            steps, applied = url_chain(ctx, worker_db, url, started,
                                       _message_text(worker_db,
                                                     payload.get("message_id")))
            picked = None
        else:
            steps, applied, picked = video_chain(ctx, worker_db, started,
                                                 spend)
        render = None
        keep_whole = bool(picked and picked.get("how") == "finished_piece")
        if ctx.versions_written or keep_whole:
            render = agent_tools.execute(
                ctx, "render_preview", {"quality": "approval"})
            steps.append({"tool": "render_preview",
                          "ok": not _failed(render),
                          "note": str(render or "")[:160]})
            if _clear_corner_mark(ctx, steps, render) \
                    and _renders_done(steps) < config.FIRST_CUT_MAX_RENDERS:
                render = agent_tools.execute(
                    ctx, "render_preview", {"quality": "approval"})
                steps.append({"tool": "render_preview",
                              "ok": not _failed(render),
                              "note": str(render or "")[:160]})
    finally:
        llm.clear_turn_plan()
        if trial_scope is not None:
            claude_trial.deactivate(recorder)
            if isinstance(recorder, _ScopeRecorder) and \
                    llm.get_recorder() is recorder:
                llm.set_recorder(None)
    elapsed = round(time.monotonic() - started, 1)
    try:
        latest = ctx.latest_edl()
    except Exception:
        latest = None
    preview = getattr(ctx, "last_preview", None) or {}
    delivered = bool(latest and preview
                     and preview.get("edl_version") == latest.get("version"))
    meta = {"kind": "first_cut", "first_cut": {
        "steps": steps, "elapsed_s": elapsed, "picked": picked,
        "renders": _renders_done(steps),
        "model_usd": round(spend["usd"], 5)}}
    follow_up = None
    if delivered and url and config.FIRST_CUT_BRIEF_FOLLOW_UP and \
            is_brief(_message_text(worker_db, payload.get("message_id"))):
        try:
            follow_up = worker_db.run(dbx.enqueue_job, job["project_id"],
                                      job["user_id"], "agent_turn",
                                      {"message_id": int(payload["message_id"]),
                                       "after_first_cut": True})
        except Exception as exc:  # noqa: BLE001 — the First cut stands alone
            print(f"[first_cut] brief follow-up not queued: {exc}", flush=True)
    if delivered:
        duration = float(preview.get("duration_s") or 0)
        summary = _summary(applied, duration, url)
        admission = project_admission_safe(worker_db, project["id"])
        trial = bool(admission and admission.get("kind") == "trial")
        text = (f"Here's your first cut: {summary}. It's playing on the "
                "right."
                + (" Now I'm working through the rest of your brief."
                   if follow_up else " Tell me what to change.")
                + (" To download it, choose a plan." if trial else ""))
        # Deterministic and unreviewed by critics: "unchecked", but the
        # version is deliverable (the export gate for a trial account is the
        # plan, D2; a payer can export it).
        meta.update({"edl_version": latest["version"], "preview": preview,
                     "quality_status": "unchecked", "quality_findings": [],
                     "export_ready": True, "outcome": "fulfilled"})
        worker_db.run(dbx.add_message, session_id, "assistant", text, meta)
        try:
            worker_db.run(dbx.record_client_event, job["user_id"],
                          job["project_id"], "free_result_delivered",
                          {"job_id": job["id"], "elapsed_s": elapsed,
                           "duration_s": duration,
                           "renders": _renders_done(steps),
                           "model_usd": round(spend["usd"], 5),
                           "source": "url" if url else "video"})
        except Exception:
            pass
    else:
        text = ("I couldn't finish an automatic first cut of this one. "
                "Tell me what you'd like and I'll work on it.")
        meta["outcome"] = "blocked"
        worker_db.run(dbx.add_message, session_id, "assistant", text, meta)
    return {"status": "first_cut", "outcome": "fulfilled" if delivered
            else "blocked", "billable": False,
            "edl_version": (latest or {}).get("version"),
            "first_cut": meta["first_cut"],
            "trial_usd": round(spend["usd"], 6),
            "credits_charged": 0.0}


class _ScopeRecorder:
    """A no-op llm recorder that carries the trial scope to pool threads."""

    def __init__(self):
        self.claude_trial_scope = None

    def __call__(self, purpose, request, response, usage):
        return None


def _renders_done(steps):
    """Preview renders this First cut ran, from its own step log.

    It read ``ctx.request_previews``, a per-request preview counter that
    main removed on 25 Oct (19489348: requests work until fulfilled, no
    preview cap). After the trial branch merged main, every First cut died
    on that missing attribute (T1 harness, 30 Oct); the step log is the
    First cut's own record and needs nothing from ToolContext."""
    return sum(1 for step in steps or ()
               if isinstance(step, dict) and step.get("tool") == "render_preview")


def project_admission_safe(worker_db, project_id):
    try:
        return worker_db.run(project_admission, project_id)
    except Exception:
        return None


def credits_for_usd(usd):
    return model_prices.usd_to_credits(usd)
