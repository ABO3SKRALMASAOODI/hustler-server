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
            if first and last and float(last["t1"]) > float(first["t0"]):
                start, end = _fit_span(rows, first, last, complete=q1)
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
        if card:
            # a graphic insert: the picture cuts to a flat card with the
            # line in huge type while the voice runs on (the references'
            # chapter and stat cards; C2 judge: "B-roll or graphic inserts")
            if not _ok(_run(ctx, steps, "add_vector_graphic", {
                    "kind": "rectangle", "start": start, "end": end,
                    "x": 0.5, "y": 0.5, "width": 1.0, "height": 1.0,
                    "color": BEAT_CARD, "opacity": 1.0}, started)):
                card = False
                params["role"] = "condensed"
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
            cards = getattr(ctx, "first_cut_cards", None)
            if isinstance(cards, list):
                # Q1: a card covers the picture; a slam over the face keeps
                # the wide framing (a cut-in pushed the chin into it)
                cards.append((start, end))
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


def montage_scout(ctx, windows, spend, title_only=False):
    """(windows, title) after the trial model's look at one frame of every
    picked window: the poster title (1-3 words, faithful to what is
    visible), the payoff shot to end on, the broken or off-topic shots to
    drop. The windows unchanged and no title without the lane or a usable
    answer."""
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
    parts = []
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
        parts.append({"type": "text", "text": f"Shot {n}:"})
        parts.append({"type": "image_url", "image_url": {
            "url": "data:image/jpeg;base64," + base64.b64encode(jpg).decode()}})
    if len(parts) < (6 if title_only else 8):
        return windows, None
    try:
        resp = claude_lane.create(
            model, [{"role": "system", "content": _MONTAGE_TITLE_SYSTEM},
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
    if end >= max(4, int(0.6 * len(windows))):
        keep = [w for n, w in enumerate(windows, 1) if n <= end and n not in drop]
        if len(keep) >= 4:
            out = keep
            a, b = out[-1]
            out[-1] = [a, round(max(b, a + MONTAGE_CLOSE_S), 2)]
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


def video_chain(ctx, worker_db, started, spend):
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
    _f = _faces(ctx.index)
    montage = (not speech and duration >= MONTAGE_MIN_SOURCE_S
               and (not _f or _f["share"] < 0.35)
               and not _screen_like(ctx.index))
    if montage:
        # Silent footage (an event, gameplay, b-roll): the references cut
        # it to its strongest moving moments (recipe D), never the whole take
        curve = _motion_curve(ctx)
        windows = montage_windows(curve, (ctx.index or {}).get("shots"),
                                  duration) if curve else None
        title = None
        if windows:
            windows, title = montage_scout(ctx, windows, spend)
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
    elif duration > config.FIRST_CUT_SHORT_SOURCE_S:
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
        shape_ends(ctx, steps, started)
    faces = _faces(ctx.index)
    talking = bool(speech and faces and faces["share"] >= 0.5)
    if q1 and speech and not talking:
        import first_cut_visual as fcv
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
            args = {"ratio": "9:16"}
            if not speech and not faces:
                args["mode"] = "crop"
            if _ok(_run(ctx, steps, "auto_reframe", args, started)):
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


TOUR_STEPS = [{"do": "wait", "seconds": 0.8}, {"do": "tour", "by": 3, "seconds": 18}]
DEMO_ZOOM = 0.25           # showcase push-ins on a phone-layout capture
DEMO_TITLE_S = 2.6         # the opening title over the live hero
DEMO_END_PUSH_S = 2.2      # the last seconds: a slow push, never a held page


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


def _site_hook(rec, url):
    """The site's name and its own promise (its <h1>), as the hook."""
    from urllib.parse import urlparse
    title = str((rec or {}).get("page_title") or "")
    brand = re.split(r"\s+[|\-–—:·]\s+", title)[0].strip() if title else ""
    if not brand or len(brand) > 24:
        host = urlparse(url).hostname or ""
        brand = host.replace("www.", "").split(".")[0].capitalize()
    h1 = " ".join(str((rec or {}).get("h1") or "").split())
    if not brand:
        return None
    # the site's name as a two-family poster ("MEET" heavy sans, the name in
    # italic serif), never its <h1>: the hero shows that headline itself, so
    # the title doubled it (C2 judge, five runs)
    return f"Meet / *{brand}*"


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
        out["prefer"] = " ".join(words)[:600]
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


def tour_steps(hints):
    tour = {"do": "tour", "by": 3,
            "seconds": min(max(float((hints or {}).get("seconds") or 18), 12.0), 60.0)}
    if (hints or {}).get("mode") == "scroll":
        tour["mode"] = "scroll"
    if (hints or {}).get("seconds", 0) >= 35:
        tour["by"] = 3
    if (hints or {}).get("prefer"):
        tour["prefer"] = hints["prefer"]
    return [{"do": "wait", "seconds": 0.8}, tour]


def url_chain(ctx, worker_db, url, started, text=None):
    steps, applied = [], []
    hints = url_hints(text)
    tour = tour_steps(hints) if text else TOUR_STEPS
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
    if key:
        # 0.25, not 0.4: the phone layout's text runs edge to edge, and a
        # deeper push cut its lines at both sides (C2 judge: "our video with
        # AI.", "pad video")
        if _ok(_run(ctx, steps, "showcase_demo",
                    {"asset_key": key, "zoom_strength": DEMO_ZOOM}, started)):
            applied.append("placed as a vertical demo")
            try:
                import media_cache
                local = media_cache.lease(key, ctx.workdir, "capture.mp4")
            except Exception:
                local = None
            for a, b in reversed(blank_spans(local) if local else []):
                _run(ctx, steps, "cut_output_range",
                     {"start": a, "end": b}, started)
            try:
                import schemas
                prog = float(schemas.program_duration(ctx.latest_edl()["json"]))
            except Exception:
                prog = 0.0
            if prog > DEMO_END_PUSH_S + 6:
                _run(ctx, steps, "add_zoom", {
                    "start": round(prog - DEMO_END_PUSH_S, 2), "end": round(prog, 2),
                    "strength": 0.12, "mode": "push_in",
                    "purpose": "the ending keeps moving (no held page before "
                               "the end card)"}, started)
            hook = _site_hook(rec, url)
            if hook:
                # The opening is ONE reading task: the site dims under a
                # poster title, then comes alive (the judge saw the title
                # collide with the site's own headline at every placement)
                # over the live hero on its own soft scrim (C2 judge: a
                # full-frame dim read as "a title on black", a hard band as
                # "a black rectangle")
                if _ok(_run(ctx, steps, "add_motion_graphic", {
                        "template": "hook_title", "start": 0.0, "end": DEMO_TITLE_S,
                        "params": dict(hook_params(hook, 0.46, True),
                                       size=1.6, scrim=0.95),
                        "mute_captions": True,
                        "purpose": "the opening title: the site and its "
                                   "promise"}, started)):
                    applied.append("a title")
    return steps, applied


def _summary(applied, duration_s, url=None):
    length = f"{int(round(duration_s))}-second " if duration_s else ""
    if url:
        return f"a {length}vertical demo of {url}"
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
                    and int(ctx.request_previews or 0) < config.FIRST_CUT_MAX_RENDERS:
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
        "renders": int(ctx.request_previews or 0),
        "model_usd": round(spend["usd"], 5)}}
    follow_up = None
    if delivered and url and is_brief(_message_text(worker_db, payload.get("message_id"))):
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
                           "renders": int(ctx.request_previews or 0),
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


def project_admission_safe(worker_db, project_id):
    try:
        return worker_db.run(project_admission, project_id)
    except Exception:
        return None


def credits_for_usd(usd):
    return model_prices.usd_to_credits(usd)
