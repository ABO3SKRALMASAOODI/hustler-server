"""render(): eyes on the real result, never blocking (HARNESS_V2.md 2.5).

- changes: the changed-section proof (agent_tools._run_changed_preview_check).
- full: the complete preview, queued like render_preview queues it; what
  comes back is the screening pages, duration, LUFS, true peak,
  midword_audit and the render checks as numbers.
- stills: chosen frames; slides: a .pptx (python-pptx) with a PDF fallback.
- Long work answers ``working`` with a handle and an assembled-program sheet
  at once; ``wait(handle)`` settles it.
- No critics are called: the model sees the pixels itself.
"""
import time

import agent_tools
import db as dbx
import read_tools
from score import asks

from harness2 import envelope
from harness2 import measure as measure_mod

APP_WAIT_S = 45.0
SHEET_TILES = 16


def handle_of(job_id):
    return f"job:{int(job_id)}"


def job_of(handle):
    text = str(handle or "").strip()
    if text.startswith("job:"):
        text = text[4:]
    try:
        return int(text)
    except ValueError:
        return None


def start_preview(ctx, quality="draft"):
    """Queue (or adopt) the complete preview of the head version."""
    row = ctx.latest_edl()
    version = int(row["version"])
    found = None
    try:
        found = ctx.db.run(read_tools.find_render_asset, ctx.project_id,
                           "preview", version)
    except Exception:  # noqa: BLE001
        found = None
    if found:
        return {"handle": None, "job_id": None, "version": version,
                "asset": found}
    payload = agent_tools._child_payload(ctx, {
        "edl_version": version, "source": "agent_preview",
        "quality": quality, "agent_job_id": (ctx.job or {}).get("id"),
        "audio_model_review": False, "harness": "v2",
        "render_signature": agent_tools._render_signature(
            row, "preview", audio_model_review=False, quality=quality)})
    job_id, _created = ctx.db.run(dbx.get_or_enqueue_preview_job,
                                  ctx.project_id, ctx.job["user_id"],
                                  payload)
    try:
        ctx.spec_preview_jobs[version] = job_id
    except (AttributeError, TypeError):
        pass
    return {"handle": handle_of(job_id), "job_id": job_id,
            "version": version}


def _assembled_sheet(ctx):
    """Program frames across the film, from source, right now."""
    from harness2 import look
    length = look._program_length(ctx)
    if length <= 0:
        return None
    times = look._span_times(0.0, length, tiles=SHEET_TILES)
    return look._frames_at(ctx, "program", None, times, [],
                           label="assembled from source")


def _result_env(ctx, result, version, tool="render"):
    """The envelope of a finished preview job."""
    ctx.last_preview = result
    try:
        ctx.rendered_versions.add(int(version))
    except (AttributeError, TypeError, ValueError):
        pass
    env = envelope.Envelope(tool=tool, version=version)
    qc = result.get("audio_qc") if isinstance(result.get("audio_qc"),
                                              dict) else {}
    env.measured = {"version": version,
                    "duration_s": result.get("duration_s"),
                    "lufs": qc.get("integrated_lufs"),
                    "true_peak": qc.get("true_peak_dbtp",
                                        qc.get("true_peak")),
                    "midword_audit": (result.get("midword_audit") or [])[:12],
                    "render_qc": result.get("render_qc")
                    or result.get("picture_qc")}
    try:
        for path, label in asks.frame_pages(ctx, result, limit=3):
            env.image(label, path)
    except Exception as exc:  # noqa: BLE001 - pixels are best-effort
        env.finding(f"screening pages not read ({str(exc)[:120]})")
    env.body = (f"preview v{version} rendered: "
                f"{measure_mod.clock_text(result.get('duration_s') or 0)}")
    return env


def settle(ctx, job_id, version, wait_s):
    """Wait up to ``wait_s`` on a preview job: its envelope, or progress."""
    deadline = time.monotonic() + max(0.0, float(wait_s or 0.0))
    job = None
    while True:
        job = ctx.db.run(dbx.get_job, job_id)
        state = (job or {}).get("state")
        if state == "done":
            return _result_env(ctx, (job or {}).get("result") or {}, version)
        if state == "failed":
            env = envelope.Envelope(tool="render", version=version)
            env.failed = True
            env.skip(0, f"the preview of v{version} failed: "
                        f"{str((job or {}).get('error') or '')[:300]}",
                     "render(scope='full') again; the edit is saved")
            return env
        if time.monotonic() >= deadline:
            break
        agent_tools._touch_job_heartbeat(ctx)
        time.sleep(min(3.0, max(0.2, deadline - time.monotonic())))
    progress = (job or {}).get("progress")
    env = envelope.Envelope(tool="render", version=version)
    env.running = {"handle": handle_of(job_id), "what": f"preview v{version}",
                   "progress": (f"{progress}%" if progress is not None
                                else (job or {}).get("state") or "queued")}
    return env


def render(ctx, scope="changes", final=False, output="film", times=None,
           slides=None, quality=None, wait_s=None):
    wait_s = APP_WAIT_S if wait_s is None else float(wait_s)
    output = str(output or "film")
    if output == "slides":
        from harness2 import slides as slides_mod
        return slides_mod.render_slides(ctx, slides or [], times)
    if output == "stills":
        return _stills(ctx, times)
    row = ctx.latest_edl()
    version = int(row["version"])
    if final:
        return _final(ctx, version, wait_s)
    if str(scope or "changes") == "changes":
        env = _changes(ctx, row)
        if env is not None:
            return env
    started = start_preview(ctx, quality="approval"
                            if quality == "approval" else "draft")
    if started.get("asset") is not None:
        meta = started["asset"].get("meta") or {}
        result = dict(meta, duration_s=started["asset"].get("duration_s"),
                      storage_key=started["asset"].get("storage_key"),
                      edl_version=version)
        return _result_env(ctx, result, version)
    env = settle(ctx, started["job_id"], version, wait_s)
    if env.running:
        sheet = _assembled_sheet(ctx)
        if sheet is not None:
            env.see.extend(sheet.see)
            env.body = ("the render is running; this sheet is the cut "
                        "assembled from source frames now")
    return env


def _changes(ctx, row):
    """The changed-section proof, or None when nothing changed to prove."""
    try:
        plan = agent_tools._verify_plan_for(ctx, row)
        ranges, _baseline = agent_tools._change_check_ranges(ctx, row, plan)
    except Exception as exc:  # noqa: BLE001 - the full preview instead
        print(f"[harness2] changed-section plan failed: {exc}", flush=True)
        return None
    if not ranges:
        return None
    text = agent_tools._run_changed_preview_check(ctx, row, plan, ranges)
    env = envelope.Envelope(tool="render", version=int(row["version"]))
    env.body = _facts_only(text)
    for label, path in list(getattr(ctx, "pending_images", None) or []):
        env.image(label, path)
    ctx.pending_images = []
    env.measured = {"version": int(row["version"]),
                    "changed_ranges": [[round(a, 2), round(b, 2)]
                                       for a, b in ranges][:24]}
    return env


def _facts_only(text):
    keep = []
    for line in str(text or "").splitlines():
        s = line.strip()
        if s.startswith(("NEXT:", "Next:", "Do NOT", "Do not", "Tip:")):
            continue
        keep.append(line)
    return "\n".join(keep)[:3000]


def _final(ctx, version, wait_s):
    """In the app the customer exports with the Export button; the model's
    part is a rendered, checked version for it to export. Over MCP the
    export is the export_final session tool (the backend's gate and holds)."""
    env = render(ctx, scope="full", wait_s=wait_s)
    env.finding(f"v{version} is the version an export takes: the Export "
                "button in Studio, or export_final over MCP")
    return env


def _stills(ctx, times):
    """Frames of the head version as it renders: look(target='render')
    starts the render when there is none and waits on it, and only a render
    still running says its frames are assembled from source. Stills of an
    unrendered version were taken from source frames, which show no
    transition, junction dip, grain or drawn layer: p5873 v2c's model read
    the 0.4 s dips it had just made as hard cuts and told the person they
    were unconfirmed."""
    from harness2 import look
    env = look.look(ctx, target="render", show="frames",
                    times=times or [0.5])
    env.tool = "render"
    return env
