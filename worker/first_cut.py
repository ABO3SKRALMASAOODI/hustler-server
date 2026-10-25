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


def _ok(result):
    """A step that ran and did not fail (a skipped step returns None)."""
    return result is not None and not _failed(result)


def url_in(text):
    match = _URL.search(str(text or ""))
    return match.group(0).rstrip(".,;:") if match else None


# --------------------------------------------------------------------------
#  Trial state, read on the worker side (the same app_kv row "free_result"
#  as backend free_trial.py, OFF by default; only the fields the worker
#  needs). Track D's trial_budgets total replaces trial_remaining_usd's sum
#  once it is merged.
# --------------------------------------------------------------------------

def _trial_settings(conn):
    out = {"on": False, "per_account_usd_cap": 1.0, "trial_requests": False}
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM app_kv WHERE key = 'free_result'")
        row = cur.fetchone()
    try:
        stored = json.loads(row["value"]) if row and row.get("value") else {}
        if isinstance(stored, dict):
            out.update(stored)
    except (TypeError, ValueError):
        pass
    return out


def project_admission(conn, project_id):
    with conn.cursor() as cur:
        cur.execute("SELECT meta FROM projects WHERE id = %s",
                    (int(project_id),))
        meta = (cur.fetchone() or {}).get("meta") or {}
    admission = meta.get("free_result")
    return admission if isinstance(admission, dict) else None


def trial_requests_on(conn):
    """Typed requests may run on the trial lane (backend free_trial.py's
    "trial_requests" switch; off until the executors hold the key)."""
    settings = _trial_settings(conn)
    return bool(settings.get("on") and settings.get("trial_requests"))


def trial_remaining_usd(conn, user_id):
    settings = _trial_settings(conn)
    if not settings.get("on"):
        return 0.0
    with conn.cursor() as cur:
        cur.execute("""SELECT COALESCE(SUM(CASE WHEN result ? 'trial_usd'
                                           THEN (result->>'trial_usd')::float
                                           ELSE 0 END), 0) AS usd
                       FROM video_jobs
                       WHERE user_id = %s AND type = 'agent_turn'
                         AND payload->>'trial_lane' = 'true'""",
                    (int(user_id),))
        used = float((cur.fetchone() or {}).get("usd") or 0.0)
    return max(0.0, float(settings.get("per_account_usd_cap") or 0) - used)


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
    if claude_lane.available():
        lines, budget = [], 90000
        for s in rows:
            line = f"[{s['id']} {float(s['t0']):.1f}-{float(s['t1']):.1f}] {s['text']}"
            budget -= len(line)
            if budget < 0:
                break
            lines.append(line)
        try:
            text, usage = claude_lane.ask_text(
                SCOUT_SYSTEM, "Transcript (sentence id, source seconds):\n"
                + "\n".join(lines), max_tokens=400, effort="low")
            cost = claude_lane.usage_cost_usd(usage, config.TRIAL_AGENT_MODEL)
            spend["usd"] += float(cost or 0.0)
            llm.record("first_cut_scout",
                       {"model": config.TRIAL_AGENT_MODEL,
                        "sentences": len(lines)},
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
    llm.set_turn_plan(config.TRIAL_PLAN, project_id=project["id"])
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
    finally:
        llm.clear_turn_plan()
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


def project_admission_safe(worker_db, project_id):
    try:
        return worker_db.run(project_admission, project_id)
    except Exception:
        return None


def credits_for_usd(usd):
    return model_prices.usd_to_credits(usd)
