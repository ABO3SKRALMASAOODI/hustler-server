"""The scene engine as the renderer of a Director film, with today's renderer behind it (lane D, 12 Nov).

A Director film keeps its scene plan (the Score's L1, ``worker/scene``) in storage beside the EDL it
lowered to, keyed by that EDL's signature: ``scene/<project>/<sha>/plan.json``. Any later change to
the EDL changes the signature, so the engine only ever draws the exact EDL the Director wrote; every
other version renders as today.

At render time (``renderer._run_render_job``), ``app_kv.director.engine`` decides:

* ``off``: nothing here runs past one row read.
* ``on``: the engine draws the plan, today's ffmpeg finish adds watermark, end card, loudness and the
  variant's encode (a pass-through EDL through ``render_edl``), and the result is verified like any
  render. Any fault (``scene.SceneFault``, a crash, a failed verification, a timeout) files a ticket
  (``client_events`` ``director_fell_back``, stage ``engine``, which the ops intake files as P1) and
  the same job renders the same EDL on today's renderer. The customer never sees the fault.
* ``shadow``: today's renderer delivers. After the asset is visible, a niced side thread draws and
  finishes the same plan into a scratch folder, verifies it, uploads the film beside the plan
  (never as an asset), and records timings; a fault files the same ticket.

A breaker in each process stops trying the engine for 30 minutes after 3 faults from 2 projects in
10 minutes. Nothing here reads a customer's words.
"""
import collections
import contextvars
import hashlib
import json
import os
import shutil
import tempfile
import threading
import time

PLAN_VERSION = 1
STALL_S = 20.0
ENGINE_TIER = "inter"          # the engine's own encode is an intermediate: today's finish re-encodes it (lane C round 2)
BREAKER_N, BREAKER_PROJECTS, BREAKER_WINDOW_S, BREAKER_HOLD_S = 3, 2, 600.0, 1800.0
SHADOW_MAX_S = 600.0

_faults = collections.deque(maxlen=16)          # (monotonic, project_id)
_tripped_until = [0.0]
_shadow_busy = threading.Lock()


# ------------------------------------------------------------------ the plan in storage

def plan_base(project_id, edl_json):
    from schemas import edl_signature
    sig = hashlib.sha256(edl_signature(edl_json).encode("utf-8")).hexdigest()[:32]
    return f"scene/{int(project_id)}/{sig}"


def _safe(aid):
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in str(aid))[:60] or "asset"


def keep_plan(project_id, edl_json, l1, keys, work, storage=None):
    """After the Director's write: store the plan beside the EDL it lowered to. ``keys`` maps the plan's
    asset ids to project storage keys (score.film._publish); an asset with no key but a file in ``work``
    is uploaded beside the plan. Returns the plan's base key. Never raises into the Director."""
    try:
        if storage is None:
            import storage
        base = plan_base(project_id, edl_json)
        plan = json.loads(json.dumps(l1))
        for aid, a in (plan.get("assets") or {}).items():
            ext = os.path.splitext(str(a.get("path") or ""))[1].lower() or (".mp4" if a.get("kind") == "video" else ".png")
            key = keys.get(aid)
            if not key and aid != "main":
                local = a.get("path") if os.path.isabs(str(a.get("path"))) else os.path.join(work, str(a.get("path") or ""))
                if os.path.isfile(local):
                    key = f"{base}/{_safe(aid)}{ext}"
                    storage.upload_file(local, key, "video/mp4" if ext in (".mp4", ".mov", ".m4v") else
                                        "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png")
            a["storage_key"] = key
            a["path"] = f"a/{_safe(aid)}{ext}"
        fd, tmp = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"v": PLAN_VERSION, "l1": plan}, f, ensure_ascii=False, separators=(",", ":"))
        try:
            storage.upload_file(tmp, f"{base}/plan.json", "application/json")
        finally:
            os.unlink(tmp)
        return base
    except Exception as exc:  # noqa: BLE001 - the film is written; the engine just won't draw it
        print(f"[engine] plan not kept for project {project_id}: {type(exc).__name__}: {str(exc)[:200]}", flush=True)
        return None


# ------------------------------------------------------------------ the switch

class Prepared:
    def __init__(self, mode, base, project_id, user_id, job_id):
        self.mode, self.base = mode, base
        self.project_id, self.user_id, self.job_id = project_id, user_id, job_id
        self.info = {"mode": mode}


def _tripped():
    return time.monotonic() < _tripped_until[0]


def _note_fault(project_id):
    now = time.monotonic()
    _faults.append((now, project_id))
    recent = [(t, p) for t, p in _faults if now - t <= BREAKER_WINDOW_S]
    if len(recent) >= BREAKER_N and len({p for _, p in recent}) >= BREAKER_PROJECTS:
        _tripped_until[0] = now + BREAKER_HOLD_S
        print(f"[engine] breaker tripped: {len(recent)} faults from {len({p for _, p in recent})} projects in "
              f"{BREAKER_WINDOW_S:.0f} s; today's renderer only for {BREAKER_HOLD_S:.0f} s", flush=True)


def prepare(worker_db, job, edl_json, proof_only=False, storage=None):
    """None, or what this render's engine branch needs. Never raises: anything wrong means today's renderer."""
    if proof_only or not isinstance(edl_json, dict):
        return None
    try:
        from score import flags
        cfg = flags.read(worker_db)
        mode = flags.engine_mode(cfg, job.get("user_id"))
        if mode == "off":
            return None
        if mode == "on" and _tripped():
            mode = "shadow"
        if storage is None:
            import storage
        base = plan_base(job["project_id"], edl_json)
        if not storage.exists(f"{base}/plan.json"):
            return None
        return Prepared(mode, base, job["project_id"], job.get("user_id"), job.get("id"))
    except Exception as exc:  # noqa: BLE001
        print(f"[engine] switch not read ({type(exc).__name__}: {str(exc)[:160]}): today's renderer", flush=True)
        return None


def file_fault(worker_db, prep, exc, stage="engine"):
    """One ticket per fault, through the intake's director_fell_back reader (P1: the customer got a film)."""
    code = getattr(exc, "code", None) or type(exc).__name__
    detail = getattr(exc, "detail", None) or str(exc)
    reason = f"{code}: {str(detail)[:240]}"
    print(f"[engine] job {prep.job_id}: {prep.mode} fault at {stage}: {reason}", flush=True)
    _note_fault(prep.project_id)
    try:
        import db as dbx
        worker_db.run(dbx.record_client_event, prep.user_id, prep.project_id, "director_fell_back",
                      {"job_id": prep.job_id, "stage": stage, "reason": reason, "engine_mode": prep.mode,
                       "plan": prep.base, **{k: v for k, v in prep.info.items() if k in ("variant", "draw_s", "finish_s")}})
    except Exception as e:  # noqa: BLE001
        print(f"[engine] fault not recorded: {str(e)[:160]}", flush=True)


# ------------------------------------------------------------------ drawing

def sized(l1, w, h):
    """The plan at an output size. L1 geometry is fractional; a voice's px is pixels at the output frame."""
    l1 = json.loads(json.dumps(l1))
    W0, H0 = [int(x) for x in (l1.get("frame") or [1080, 1920])]
    if (int(w), int(h)) == (W0, H0):
        return l1
    k = float(w) / float(W0)
    l1["frame"] = [int(w), int(h)]
    for v in (l1.get("voices") or {}).values():
        if isinstance(v, dict) and v.get("px"):
            v["px"] = round(float(v["px"]) * k, 3)
    return l1


def materialize(prep, src_local, sdir, storage=None):
    """The plan and its pictures in ``sdir``. ``main`` is the render's own source (proxy or original)."""
    if storage is None:
        import storage
    if not src_local or not os.path.isfile(src_local):
        from scene import SceneFault
        raise SceneFault("ineligible", "the source is not a local file")
    os.makedirs(os.path.join(sdir, "a"), exist_ok=True)
    pj = os.path.join(sdir, "plan.json")
    storage.download_to(f"{prep.base}/plan.json", pj, check_capacity=False)
    doc = json.load(open(pj, encoding="utf-8"))
    l1 = doc["l1"]
    for aid, a in (l1.get("assets") or {}).items():
        dst = os.path.join(sdir, a["path"])
        if aid == "main":
            os.symlink(os.path.abspath(src_local), dst)
        elif a.get("storage_key"):
            storage.download_to(a["storage_key"], dst, check_capacity=False)
    return l1


def target_size(l1, variant):
    import renderer
    W0, H0 = [int(x) for x in (l1.get("frame") or [1080, 1920])]
    if variant == "preview":
        w, h, _fps = renderer.preview_geometry(W0, H0, float(l1.get("fps") or 30))
        return w, h
    return W0, H0


def draw(prep, worker_db, *, src_local, out_local, variant, finish, verify, sdir, timeout_s=None, scene=None):
    """Draw the plan, finish it through today's ffmpeg brand step, verify. Returns the output duration,
    or None after filing the fault (the caller then renders the same EDL on today's renderer)."""
    t0 = time.monotonic()
    prep.info["variant"] = variant
    try:
        if scene is None:
            import scene
        l1 = materialize(prep, src_local, sdir)
        w, h = target_size(l1, variant)
        l1 = sized(l1, w, h)
        film_s = float(l1.get("dur_f") or 0) / float(l1.get("fps") or 30)
        engine_out = os.path.join(sdir, "engine.mp4")
        t1 = time.monotonic()
        # The shared segment cache (VALMERA_SCENE_CACHE): scene writes a segment inside the cache and renames
        # it there, so another mount is fine (lane C round 2) and an unchanged film draws nothing again.
        log = scene.render(l1, sdir, engine_out, tier=ENGINE_TIER, audio=True, strict=True, stall_s=STALL_S,
                           timeout_s=timeout_s if timeout_s is not None else 90.0 + 3.0 * film_s)
        t2 = time.monotonic()
        prep.info.update(fetch_s=round(t1 - t0, 2), draw_s=round(t2 - t1, 2), film_s=round(film_s, 2), size=[w, h],
                         frames=log.get("frames"), stage_ms=log.get("stage_ms"), gl=log.get("gl"))
        dur = finish(engine_out, out_local)
        prep.info["finish_s"] = round(time.monotonic() - t2, 2)
        verify(out_local, dur)
        prep.info["total_s"] = round(time.monotonic() - t0, 2)
        print(f"[engine] job {prep.job_id}: drew {variant} {w}x{h} {film_s:.1f} s film in {prep.info['draw_s']} s, "
              f"finish {prep.info['finish_s']} s", flush=True)
        return dur
    except Exception as exc:  # noqa: BLE001 - every fault falls back; none reaches the customer
        prep.info["fault"] = f"{getattr(exc, 'code', type(exc).__name__)}"
        file_fault(worker_db, prep, exc)
        try:
            os.unlink(out_local)
        except OSError:
            pass
        return None


def pass_through(engine_out):
    """The EDL that carries an engine film through today's finish untouched: one keep, nothing else."""
    import media
    return {"keep": [[0.0, round(float(media.duration_of(engine_out)), 3)]]}


# ------------------------------------------------------------------ shadow

def shadow(prep, worker_db, *, src_local, variant, finish_factory, verify_factory, storage=None):
    """After today's film is visible: draw the same plan on the side, niced, in a daemon thread that owns
    its folder. Never blocks the job, never raises, one at a time per process."""
    if prep is None or prep.mode != "shadow" or not src_local or not os.path.isfile(src_local):
        return None
    if not _shadow_busy.acquire(blocking=False):
        print(f"[engine] job {prep.job_id}: shadow skipped, one already running here", flush=True)
        return None
    try:
        root = os.environ.get("VALMERA_SCENE_SHADOW_DIR") or tempfile.gettempdir()
        sdir = tempfile.mkdtemp(prefix=f"scene_shadow_{prep.job_id}_", dir=root)
        src = os.path.join(sdir, "source" + os.path.splitext(src_local)[1])
        try:
            os.link(src_local, src)
        except OSError:
            shutil.copyfile(src_local, src)
    except Exception as exc:  # noqa: BLE001
        _shadow_busy.release()
        print(f"[engine] job {prep.job_id}: shadow not started: {str(exc)[:160]}", flush=True)
        return None
    ctx = contextvars.copy_context()

    def run():
        try:
            try:
                os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 19)    # this thread and its children only
            except (AttributeError, OSError):
                pass
            out = os.path.join(sdir, "shadow.mp4")
            dur = draw(prep, worker_db, src_local=src, out_local=out, variant=variant, finish=finish_factory(sdir),
                       verify=verify_factory(src), sdir=os.path.join(sdir, "w"), timeout_s=SHADOW_MAX_S)
            if dur is not None:
                try:
                    st = storage
                    if st is None:
                        import storage as st
                    st.upload_file(out, f"{prep.base}/shadow_{variant}.mp4", "video/mp4")
                    prep.info["film_key"] = f"{prep.base}/shadow_{variant}.mp4"
                except Exception as exc:  # noqa: BLE001
                    print(f"[engine] shadow film not uploaded: {str(exc)[:160]}", flush=True)
                try:
                    import db as dbx
                    worker_db.run(dbx.record_client_event, prep.user_id, prep.project_id, "engine_shadow",
                                  {"job_id": prep.job_id, "duration_s": dur, **prep.info})
                except Exception as exc:  # noqa: BLE001
                    print(f"[engine] shadow not recorded: {str(exc)[:160]}", flush=True)
            print(f"[engine] shadow {json.dumps(prep.info, default=str)[:600]}", flush=True)
        finally:
            shutil.rmtree(sdir, ignore_errors=True)
            _shadow_busy.release()

    t = threading.Thread(target=lambda: ctx.run(run), name=f"scene-shadow-{prep.job_id}", daemon=True)
    t.start()
    return t
