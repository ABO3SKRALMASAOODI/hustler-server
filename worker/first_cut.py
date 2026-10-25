"""First cut: one finished result, made automatically, deterministically.

PLAN.md P2 as corrected by C2.4 and owner decision D2 (25 Oct 2026): on an
admitted project's first upload (or a "Record my website" URL) Valmera makes
one finished vertical result with no agent loop. The in-app loop's median is
13.6 min (p90 78.6); this chain is a fixed list of the editor's own tools:

    video ≤ 90 s:  cut_silences, remove_filler_words (speech only),
                   auto_reframe 9:16 (non-vertical sources),
                   apply_format_pack (calm), add_captions (speech only),
                   one approval render
    video > 90 s:  ONE scout call picks the best 20-60 s story in the first
                   30 min (Claude, the trial lane; a deterministic
                   speech-density pick without it), keep_segments to it, then
                   the same chain
    URL:           record_website_demo (portrait, a steady top-to-bottom
                   scroll), showcase_demo, one approval render

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

DEFAULT_SCROLL_STEPS = [
    {"do": "wait", "seconds": 1.2},
    {"do": "scroll", "by": 700, "seconds": 1.4},
    {"do": "scroll", "by": 700, "seconds": 1.4},
    {"do": "scroll", "by": 700, "seconds": 1.4},
    {"do": "scroll", "by": 700, "seconds": 1.4},
    {"do": "scroll", "by": 700, "seconds": 1.4},
    {"do": "scroll", "by": 700, "seconds": 1.6},
]
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

SCOUT_SYSTEM = (
    "You pick the single best moment of a long video for one vertical short. "
    "Choose ONE continuous run of whole sentences, 20 to 60 seconds long, "
    "that stands on its own: the first sentence hooks a new viewer, the last "
    "one lands a payoff (an answer, a twist, a strong line). Prefer a "
    "concrete story or claim over small talk, intros and sponsor reads. "
    "Answer with JSON only, copying the sentence ids exactly as written "
    "(for example \"s12\"): {\"start_id\": \"<id>\", \"end_id\": \"<id>\", "
    "\"title\": \"<5-8 word title>\"}.")


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
            resp = claude_lane.create(model, messages, max_tokens=400,
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
                    return start, end, pick.get("title"), "scout"
        except Exception as exc:  # noqa: BLE001 — the fallback is the point
            print(f"[first_cut] scout failed: {type(exc).__name__}: "
                  f"{str(exc)[:200]}", flush=True)
    picked = _density_pick(ctx.index, limit)
    if picked:
        return picked[0], picked[1], None, "density"
    return 0.0, min(float(ctx.duration or 0), 45.0), None, "opening"


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
    if duration > config.FIRST_CUT_SHORT_SOURCE_S:
        start, end, title, how = scout_range(ctx, worker_db, spend)
        picked = {"start_s": round(start, 2), "end_s": round(end, 2),
                  "title": title, "how": how}
        result = _run(ctx, steps, "keep_segments",
                      {"segments": [[start, end]], "snap_to_words": True},
                      started)
        if _ok(result):
            applied.append("best moment")
    if _speech(ctx.index):
        if _ok(_run(ctx, steps, "cut_silences", {}, started)):
            applied.append("pauses trimmed")
        if _ok(_run(ctx, steps, "remove_filler_words", {}, started)):
            applied.append("filler words removed")
    if _orientation(ctx.index) != "vertical":
        if _ok(_run(ctx, steps, "auto_reframe", {"ratio": "9:16"}, started)):
            applied.append("reframed to 9:16")
    # The pack's lockups go in BEFORE the captions so caption placement is
    # measured around them (E2E 25 Oct: pack-after-captions left two heard
    # words with no clear caption band, pack-first left one).
    if _ok(_run(ctx, steps, "apply_format_pack",
                {"pack": "auto", "intensity": "calm"}, started)):
        applied.append("a format pack")
    if _speech(ctx.index):
        # An explicit style skips the caption judge's vision call: the First
        # cut uses no model on the paid wallet (D2). reels + editorial is what
        # the judge chose on all 8 E2E fixtures (25 Oct).
        if _ok(_run(ctx, steps, "add_captions",
                    {"mode": "from_transcript",
                     "style": {"preset": "reels", "motion_look": "editorial"}},
                    started)):
            applied.append("captions")
    return steps, applied, picked


def url_chain(ctx, worker_db, url, started):
    steps, applied = [], []
    result = _run(ctx, steps, "record_website_demo",
                  {"url": url, "orientation": "portrait",
                   "steps": DEFAULT_SCROLL_STEPS}, started)
    key = None
    for asset in reversed(getattr(ctx, "web_recordings", None) or []):
        key = (asset.get("storage_key") if isinstance(asset, dict)
               else getattr(asset, "storage_key", None)) or key
        if key:
            break
    if not key:
        match = re.search(r"storage_key=(\S+?\.mp4)", str(result or ""))
        key = match.group(1) if match else None
    if _ok(result):
        applied.append("website recorded")
    if key:
        if _ok(_run(ctx, steps, "showcase_demo",
                    {"asset_key": key, "zoom_strength": 0.4}, started)):
            # A scroll-only capture has no clicks to zoom on (E2E 25 Oct).
            applied.append("placed as a vertical demo")
    return steps, applied


def _summary(applied, duration_s, url=None):
    length = f"{int(round(duration_s))}-second " if duration_s else ""
    if url:
        return f"a {length}vertical demo of {url}"
    parts = [p for p in applied if p != "a format pack"]
    detail = (", ".join(parts[:-1]) + " and " + parts[-1]) if len(parts) > 1 \
        else (parts[0] if parts else "")
    return f"a {length}vertical version" + (f": {detail}" if detail else "")


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
