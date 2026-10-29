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
SCOUT_SYSTEM = (
    "You pick the single best moment of a long video for one vertical short. "
    "Choose ONE continuous run of whole sentences, 20 to 60 seconds long, "
    "that stands on its own: the first sentence hooks a new viewer (a "
    "complete claim or question, never one starting with \"and\", \"so\" "
    "or a filler), the last one lands a payoff (an answer, a twist, a strong "
    "line). Prefer a concrete story or claim over small talk, intros and "
    "sponsor reads. " + _HOOK_RULE +
    "Answer with JSON only, copying the sentence ids exactly as written "
    "(for example \"s12\"): {\"start_id\": \"<id>\", \"end_id\": \"<id>\", "
    "\"title\": \"<5-8 word title>\", \"hook\": \"<2-5 words>\"}.")
TRIM_SYSTEM = (
    "You trim the two ends of a short video for a vertical reel and keep "
    "everything in between. START: the first sentence a new viewer should "
    "hear. Skip greetings, mic checks (\"is this recording?\"), false "
    "starts and a fragment that begins mid-sentence; otherwise keep the very "
    "first sentence. END: the line that lands the point (a payoff, an "
    "answer, a strong claim), keeping the result 20 to 60 seconds long when "
    "the video is longer; drop what follows it when that is thanks, "
    "chit-chat, a sign-off, a trailing fragment or garbled speech. "
    + _HOOK_RULE +
    "Answer with JSON only, copying the sentence ids exactly as written: "
    "{\"start_id\": \"<id>\", \"end_id\": \"<id>\", \"hook\": \"<2-5 words>\"}.")


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


def _fit_span(rows, first, last, low=20.0, high=60.0):
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
            messages = [{"role": "system", "content": SCOUT_SYSTEM},
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
            llm.record("first_cut_scout",
                       {"model": model, "sentences": len(lines)},
                       {"content": text}, usage)
            match = re.search(r"\{.*\}", text or "", re.S)
            pick = json.loads(match.group(0)) if match else {}
            first = by_id.get(str(pick.get("start_id")))
            last = by_id.get(str(pick.get("end_id")))
            if first and last and float(last["t1"]) > float(first["t0"]):
                start, end = _fit_span(rows, first, last)
                if 12.0 <= end - start <= 75.0:
                    ctx.first_cut_hook = clean_hook(pick.get("hook"),
                                                    ctx.index)
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
PAUSE_CUT_S = 0.35         # every pause over this inside speech is cut
HOOK_S = 2.6               # the hook title's time on screen
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
            resp = claude_lane.create(
                model, [{"role": "system", "content": TRIM_SYSTEM},
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
        except Exception as exc:  # noqa: BLE001 — the rules are the fallback
            print(f"[first_cut] trim scout failed: {type(exc).__name__}: "
                  f"{str(exc)[:200]}", flush=True)
    end_row = _complete_end(rows, first, last)
    if float(end_row["t1"]) - float(first["t0"]) >= min(8.0, 0.5 * duration):
        last = end_row
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


def chest_y(faces, bands=None):
    """The caption and hook band on a vertical talking head: just under the
    face (references: y 0.50-0.70), clear of the platform's bottom UI."""
    if not faces:
        return None
    bottom = faces["bottom"]
    if bands:
        bottom = (bottom - bands[0]) / max(1e-6, bands[1] - bands[0])
    return round(min(max(bottom + 0.10, 0.60), 0.80), 3)


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
    # cut, which the picture QC reads as a one-frame pop
    params = {"text": text, "font": "Anton", "uppercase": True,
              "treatment": "serif", "accent": "#FFFFFF", "entrance": "slam",
              "exit": "blur", "size": 1.3 if vertical else 0.9,
              "scrim": 0.35}
    if y is not None:
        params["y"] = y
    return params


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


def _speech(index):
    return len((index or {}).get("words") or []) >= 8


def video_chain(ctx, worker_db, started, spend):
    steps, applied = [], []
    duration = float(ctx.duration or 0)
    picked = None
    speech = _speech(ctx.index)
    ctx.first_cut_hook = None
    if duration > config.FIRST_CUT_SHORT_SOURCE_S:
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
        args = {"min_silence_s": PAUSE_CUT_S, "padding_s": 0.1}
        if _talk_density(ctx) >= 1.2:
            # Narration over room tone or street sound is still speech with
            # pauses (the vlog fixture: every gap "non-quiet", nothing cut);
            # music and game audio never carry this many words a second.
            args["allow_nonquiet"] = True
        if _ok(_run(ctx, steps, "cut_silences", args, started)):
            applied.append("pauses trimmed")
        if _ok(_run(ctx, steps, "remove_filler_words", {}, started)):
            applied.append("filler words removed")
        shape_ends(ctx, steps, started)
    faces = _faces(ctx.index)
    talking = bool(speech and faces and faces["share"] >= 0.5)
    vertical_source = _orientation(ctx.index) == "vertical"
    vertical_out = vertical_source
    if not vertical_source:
        if _screen_like(ctx.index) and not talking:
            # Screen text at 9:16 is unreadable (bake-off vfr runs); the
            # references keep tutorials landscape (R07, R11).
            applied.append("the screen's own shape kept so its text reads")
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
    bands = None
    if vertical_out and talking:
        res, bands = deband(ctx, steps, started)
        if not _ok(res):
            bands = None
        else:
            applied.append("the source's bars cropped")
    if talking and len(_keep(ctx) or []) > 1:
        _run(ctx, steps, "conceal_jump_cuts", {}, started)
    if talking and not bands:
        # (never on top of the band crop: stacked on its 1.4x zoom a punch
        # pushed the chin out of frame, "FACE AT THE BOTTOM EDGE", C1)
        # The references change the picture every 2-3 s; on a single take
        # the cheapest honest event is a punch on a stressed word, kept
        # sparse (about one per 9 s; the owner: restraint, never one per
        # cut).
        prog = sum(e - s for s, e in (_keep(ctx) or []))
        if prog >= 12:
            _run(ctx, steps, "punch_in_on_emphasis",
                 {"count": max(2, int(prog // 9))}, started)
    y = chest_y(faces, bands) if (talking and vertical_out) else None
    hook, hook_end = getattr(ctx, "first_cut_hook", None), HOOK_S
    if talking:
        opening = spoken_opening(ctx)
        hook, hook_end = opening if opening else (None, HOOK_S)
    if hook:
        args = {"template": "hook_title", "start": 0.0, "end": hook_end,
                "params": hook_params(hook, y, vertical_out),
                "purpose": "the opening line as a poster title (references: "
                           "a poster-scale title by 0.3 s)"}
        if _ok(_run(ctx, steps, "add_motion_graphic", args, started)):
            applied.append("a hook title")
    if speech:
        style = {"preset": "reels", "motion_look": "editorial",
                 "size_scale": 1.2}
        if y is not None:
            style["anchor_y"] = y
        if _ok(_run(ctx, steps, "add_captions",
                    {"mode": "from_transcript", "style": style,
                     "max_words_per_caption": 3},
                    started)):
            applied.append("captions")
    return steps, applied, picked


TOUR_STEPS = [{"do": "wait", "seconds": 0.8}, {"do": "tour", "by": 2}]


def _site_hook(rec, url):
    """The site's name and its own promise (its <h1>), as the hook."""
    from urllib.parse import urlparse
    title = str((rec or {}).get("page_title") or "")
    brand = re.split(r"\s+[|\-–—:·]\s+", title)[0].strip() if title else ""
    if not brand or len(brand) > 24:
        host = urlparse(url).hostname or ""
        brand = host.replace("www.", "").split(".")[0].capitalize()
    h1 = " ".join(str((rec or {}).get("h1") or "").split())
    words = [w for w in h1.split() if w]
    if not brand:
        return None
    line = " ".join(words[:6]) if 2 <= len(words) <= 9 else ""
    return f"*{brand}*" + (f" / {line}" if line else "")


def url_chain(ctx, worker_db, url, started):
    steps, applied = [], []
    result = _run(ctx, steps, "record_website_demo",
                  {"url": url, "orientation": "portrait",
                   "steps": TOUR_STEPS}, started)
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
        if _ok(_run(ctx, steps, "showcase_demo",
                    {"asset_key": key, "zoom_strength": 0.4}, started)):
            applied.append("placed as a vertical demo")
            hook = _site_hook(rec, url)
            if hook and _ok(_run(ctx, steps, "add_motion_graphic", {
                    "template": "hook_title", "start": 0.0, "end": HOOK_S,
                    # under the site's own hero headline (top third on a
                    # phone layout), on a stronger scrim: the judge saw the
                    # title collide with the page's headline at y 0.36
                    "params": dict(hook_params(hook, 0.66, True), scrim=0.6),
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
    llm.set_turn_plan(plan if subscribed else "", project_id=project["id"])
    try:
        if url:
            steps, applied = url_chain(ctx, worker_db, url, started)
            picked = None
        else:
            steps, applied, picked = video_chain(ctx, worker_db, started,
                                                 spend)
        render = None
        if ctx.versions_written:
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
    if delivered:
        duration = float(preview.get("duration_s") or 0)
        summary = _summary(applied, duration, url)
        admission = project_admission_safe(worker_db, project["id"])
        trial = bool(admission and admission.get("kind") == "trial")
        text = (f"Here's your first cut: {summary}. It's playing on the "
                "right."
                + " Tell me what to change."
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
