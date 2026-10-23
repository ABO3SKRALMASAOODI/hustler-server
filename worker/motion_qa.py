"""The proof loop: proof_device and audit_motion (track 1F).

ROADMAP Wave 1 / 1F; contract in worker/devices/INTERFACES_W1.md §8.

``proof(tool_ctx, edl, ids=, window=)`` renders ONLY the windows around the
devices it proves, through the existing changed-section preview path
(renderer._render_changed_sections — the ``preview_check`` job in
production, no new job kind), twice: with the devices and without them. Per
device it returns

  strip    10 frames, -3 .. +6 around the landing frame, with (top) and
           without (bottom), the landing tile outlined
  onion    the frame without the device under a red map of every pixel the
           device changed across the strip
  metrics  land_err_f, legibility (min Lc), glyph_visible, matte_flicker,
           flash_risk, duration_exact (junction integrity), collisions,
           budget, and the card's own ``verify`` entries — each
           {value, bar, pass} (qa_metrics.METRICS)

``audit(edl, index)`` is the browserless half (agent and MCP lanes): the
ledger, flash safety, each device's static rung and the restraint rules —
one punctuation family per section, no device on consecutive shots, none
inside the payoff hold — as findings.

Two renderers, one analysis:
  LocalRenderer  renderer._render_changed_sections in this process (tests,
                 refbench, any lane with ffmpeg and the source): exact
                 with/without pairs at "480p" (short edge 480) and the
                 source frame rate.
  JobRenderer    the production path: one ``preview_check`` job for the
                 current EDL version and, for the without-device reference,
                 one for the newest earlier version that lacks the devices
                 on the same timeline. Without such a version the onion and
                 the landing fall back to the strip's own frame-to-frame
                 change (``basis: temporal``) and say so.
"""

import copy
import json
import math
import os
import threading
import time

STRIP_BEFORE = 3
STRIP_AFTER = 6
# A pixel counts as changed by a device past this |delta| (8-bit, any
# channel); a frame lands when this share of it changed AND at least
# PEAK_SHARE of the strip's strongest change (a lossy proof encode carries
# a device's change a few frames on, and B-frames a frame back, as drift).
CHANGE_THR = 18
LAND_SHARE = 0.003
PEAK_SHARE = 0.25
# Proof geometry: "480p" means a 480-pixel short edge in either orientation.
PROOF_LONG_EDGE = 854
PROOF_MAX_FPS = 60.0
# Collisions: a device's ink covering this share of a detected face.
FACE_COLLISION_SHARE = 0.15
TYPE_FAMILIES = ("type_depth", "composite", "layout")
MATTE_TYPES = ("subject", "background")
_CFG_LOCK = threading.Lock()


# ── which devices a proof covers ─────────────────────────────────────────

def _canon(v):
    return json.dumps(v, sort_keys=True, separators=(",", ":"), default=str)


def device_items(edl, ids=None, window=None, previous_edl=None):
    """[{id, layer, kind, type, device, item}] the proof covers.

    ``ids`` names them (fx ids, or motion ids whose ``composite`` or
    behind-subject layer makes them devices). Without ids: the device items
    that changed since ``previous_edl``, else every device item; ``window``
    (program seconds) keeps those that touch it."""
    edl = edl or {}
    out = []
    for it in edl.get("fx") or []:
        if isinstance(it, dict) and it.get("stage") != "music_bus":
            out.append({"id": str(it.get("id")), "layer": "fx",
                        "kind": "fx", "type": it.get("type"),
                        "device": it.get("device"), "item": it})
    for m in edl.get("motion") or []:
        if not isinstance(m, dict):
            continue
        if m.get("composite") is not None or \
                m.get("layer") == "behind_subject":
            out.append({"id": str(m.get("id")), "layer": "motion",
                        "kind": ("composite" if m.get("composite")
                                 is not None else "behind"),
                        "type": "motion", "device": None, "item": m})
    if ids:
        want = [str(i) for i in ids]
        by_id = {d["id"]: d for d in out}
        return [by_id[i] for i in want if i in by_id]
    if previous_edl is not None:
        prev = {d["id"]: _canon(d["item"])
                for d in device_items(previous_edl)}
        out = [d for d in out if prev.get(d["id"]) != _canon(d["item"])]
    if window:
        a, b = float(window[0]), float(window[1])
        out = [d for d in out if _touches(d["item"], a, b)]
    return out


def _touches(item, a, b):
    if item.get("at") is not None:
        return a - 0.5 <= float(item["at"]) <= b + 0.5
    try:
        return float(item["start"]) < b and float(item["end"]) > a
    except (KeyError, TypeError, ValueError):
        return False


def ablate(edl, devs):
    """The EDL without ``devs``: fx items removed, a motion item's
    composite cleared, a behind-subject graphic removed. (A frameless
    program that loses its last fx item leaves the block clock: see
    ``same_clock``.)"""
    out = copy.deepcopy(edl)
    gone_fx = {d["id"] for d in devs if d["layer"] == "fx"}
    out["fx"] = [i for i in out.get("fx") or []
                 if str(i.get("id")) not in gone_fx]
    comp = {d["id"] for d in devs if d["kind"] == "composite"}
    behind = {d["id"] for d in devs if d["kind"] == "behind"}
    motion = []
    for m in out.get("motion") or []:
        mid = str(m.get("id"))
        if mid in behind:
            continue
        if mid in comp:
            m = dict(m, composite=None)
        motion.append(m)
    out["motion"] = motion
    return out


def _framed(edl):
    fr = (edl or {}).get("frame") or {}
    return bool(fr.get("ratio") and fr.get("ratio") != "source")


def same_clock(edl, ref):
    """Whether a reference EDL renders on the program's clock. Frame-
    addressed fx put a program on the normalized block clock (fxgraph
    needs_cfr), and so does an output frame; a frameless reference with no
    fx takes the cheap graph, whose cuts — and audio — sit up to a fraction
    of a frame elsewhere. (A 'source' frame validates to no frame at all.)"""
    if not _needs_block_clock(edl):
        return True
    return _framed(ref) or _needs_block_clock(ref)


def front_variant(edl, devs):
    """The EDL with each behind-subject graphic in ``devs`` drawn in FRONT
    of the subject (above the captions, no mask): its full ink, the
    denominator of glyph_visible."""
    ids = {d["id"] for d in devs if d["kind"] == "behind"}
    out = copy.deepcopy(edl)
    motion = []
    for m in out.get("motion") or []:
        if str(m.get("id")) in ids:
            m = dict(m, layer="above_captions")
            m.pop("behind", None)
        motion.append(m)
    out["motion"] = motion
    return out


def _needs_block_clock(edl):
    return any(isinstance(i, dict) and i.get("stage") != "music_bus"
               for i in (edl or {}).get("fx") or [])


def _pure(value):
    """``value`` with None, empty lists and empty dicts dropped at every
    depth: two stored EDLs that render the same compare equal whatever
    defaults each version's writer spelled out (a cleared composite is
    None, an absent fx layer is [])."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            v = _pure(v)
            if v is None or v == [] or v == {}:
                continue
            out[k] = v
        return out
    if isinstance(value, (list, tuple)):
        return [_pure(v) for v in value]
    return value


# ── frames ───────────────────────────────────────────────────────────────

def _fps3(fps):
    return round(float(fps or 30.0), 3)


def frame_of(t, fps):
    return int(math.floor(float(t) * _fps3(fps) + 0.5 + 1e-9))


def landing(item, fps, *, kind="fx", words=None, timeline=None, maps=None):
    """(landing_f, planned_f): the frame the device lands on
    (fxgraph.landing_frames: the compiler's own hook, else its first frame
    or its cut) and the frame its bound moment names (the bind resolved on
    this timeline; the landing itself when it carries no bind)."""
    import fxgraph
    plan = fxgraph.FxPlan(fps=fps, program_s=1e6)
    if kind == "fx":
        lf = fxgraph.landing_frames(plan, item)
        land = lf[0] if lf else frame_of(item.get("start") or 0.0, fps)
    else:
        land = frame_of(item.get("start") or 0.0, fps)
    planned = land
    bind = item.get("bind")
    if isinstance(bind, dict) and bind.get("on") and kind == "fx":
        try:
            import rhythm_types
            t = rhythm_types.resolve_bind(bind, fps=fps, timeline=timeline,
                                          words=words, maps=maps)
            if t is not None:
                planned = frame_of(t, fps)
        except Exception:  # noqa: BLE001
            pass
    return int(land), int(planned)


def extent_f(item, fps):
    """(before, after): the frames around its landing a device's proof must
    render and read — the -3..+6 strip, or a whole junction (its h frames
    each side of the cut, plus one), so its centre is read from its full
    run."""
    if (item or {}).get("stage") != "junction":
        return STRIP_BEFORE, STRIP_AFTER
    h = None
    try:
        import fxgraph
        fn = getattr(fxgraph.compiler_for(item.get("type")),
                     "junction_frames", None)
        if fn is not None:
            h = int(fn(fxgraph.FxPlan(fps=fps, program_s=1e6), item) or 0)
    except Exception:  # noqa: BLE001
        h = None
    if not h:
        try:
            d = float(item.get("duration_s") or 0.0)
        except (TypeError, ValueError):
            d = 0.0
        if d <= 0:
            try:
                import fxgraph
                d = float(fxgraph.DEFAULT_JUNCTION_S)
            except Exception:  # noqa: BLE001
                d = 0.4
        h = int(math.ceil(d * _fps3(fps) / 2.0))
    half = int(h) + 1
    return max(STRIP_BEFORE, half), max(STRIP_AFTER, half)


def proof_windows(frames, fps, program_s, pad_f=2, extents=None):
    """Program-second windows covering every [f-3-pad, f+6+pad] strip (or
    the device's own ``extents`` (before, after), aligned with ``frames``),
    merged, on the frame grid."""
    spans = []
    for k, f in enumerate(frames):
        before, after = (extents[k] if extents else
                         (STRIP_BEFORE, STRIP_AFTER))
        a = max(0, f - before - pad_f)
        b = f + after + 1 + pad_f
        spans.append([a, b])
    spans.sort()
    merged = []
    for a, b in spans:
        if merged and a <= merged[-1][1] + 2:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    f3 = _fps3(fps)
    out = []
    for a, b in merged:
        ta = round(a / f3, 3)
        tb = round(min(float(program_s), b / f3), 3)
        if tb - ta < 0.1:
            ta = max(0.0, tb - 0.1)
        out.append([ta, tb])
    return out


class Reel:
    """A rendered proof: per program window, the file and the program frame
    its first frame shows. ``frame(F)`` -> uint8 RGB or None."""

    def __init__(self, segments, fps, size=None):
        # segments: [{"a", "b", "path", "first_f", "reel_f0"}]
        self.segments = segments
        self.fps = float(fps)
        self.size = size
        self._cache = {}

    def locate(self, F):
        for s in self.segments:
            n = s.get("frames")
            if F >= s["first_f"] and (n is None or F < s["first_f"] + n):
                return s, F - s["first_f"] + s.get("reel_f0", 0)
        return None, None

    def frames(self, Fs):
        import qa_metrics
        want = {}
        for F in Fs:
            s, k = self.locate(F)
            if s is not None:
                want.setdefault(s["path"], {})[k] = F
        out = {}
        for path, ks in want.items():
            missing = [k for k in ks if (path, k) not in self._cache]
            if missing:
                idx, arr = qa_metrics.read_frames(path, frames=set(missing))
                for i, a in zip(idx, arr):
                    self._cache[(path, i)] = a
            for k, F in ks.items():
                if (path, k) in self._cache:
                    out[F] = self._cache[(path, k)]
        return out


# ── renderers ────────────────────────────────────────────────────────────

class LocalRenderer:
    """The changed-section preview path in this process
    (renderer._render_changed_sections), at a 480-pixel short edge and the
    source frame rate. ``render(edl, ranges, tag) -> (Reel, info)``."""

    lane = "local"

    def __init__(self, index, src_path, workdir, *, long_edge=PROOF_LONG_EDGE,
                 max_fps=PROOF_MAX_FPS):
        self.index = index or {}
        self.src = src_path
        self.workdir = workdir
        self.long_edge = long_edge
        self.max_fps = max_fps
        os.makedirs(workdir, exist_ok=True)

    def render(self, edl, ranges, tag):
        import config
        import qa_metrics
        import renderer
        wd = os.path.join(self.workdir, tag)
        os.makedirs(wd, exist_ok=True)
        out = os.path.join(wd, "proof.mp4")
        segs = []
        t0 = time.monotonic()
        with _CFG_LOCK:
            saved = (config.PREVIEW_MAX_LONG_EDGE, config.PREVIEW_MAX_HEIGHT,
                     config.PREVIEW_MAX_FPS)
            config.PREVIEW_MAX_LONG_EDGE = int(self.long_edge)
            config.PREVIEW_MAX_HEIGHT = 0
            config.PREVIEW_MAX_FPS = float(self.max_fps)
            try:
                pages = [ranges[i:i + 6] for i in range(0, len(ranges), 6)]
                renderer._render_changed_sections(
                    f"proof-{tag}", {"json": edl}, self.index, self.src, wd,
                    {}, out, ranges, [], raw_pages=pages,
                    proof_segments=segs)
            finally:
                (config.PREVIEW_MAX_LONG_EDGE, config.PREVIEW_MAX_HEIGHT,
                 config.PREVIEW_MAX_FPS) = saved
        wall = time.monotonic() - t0
        info = qa_metrics.probe(out)
        # the pieces carry the render rate; a concatenated reel's stream
        # rate is only an approximation of it (concat durations)
        first_piece = os.path.join(wd, "check_piece_0.mp4")
        piece = qa_metrics.probe(first_piece) if os.path.exists(
            first_piece) else info
        fps = float(piece["fps"] or info["fps"] or 30.0)
        reel_segs = []
        for i, s in enumerate(segs):
            piece = os.path.join(wd, f"check_piece_{i}.mp4")
            path = piece if os.path.exists(piece) else out
            n = None
            try:
                n = qa_metrics.probe(piece)["frames"] if path == piece \
                    else None
            except Exception:  # noqa: BLE001
                n = None
            reel_segs.append({
                "a": float(s["start"]), "b": float(s["end"]), "path": path,
                "first_f": frame_of(s["start"], fps),
                "reel_f0": 0 if path == piece else int(round(
                    float(s["reel_start"]) * fps)),
                "frames": n})
        return Reel(reel_segs, fps, (info["width"], info["height"])), {
            "path": out, "wall_s": round(wall, 2), "fps": fps,
            "width": info["width"], "height": info["height"],
            "segments": [[r["a"], r["b"]] for r in reel_segs],
            "devices": None}


def _frame_times(path):
    """The video frames' presentation times (seconds, sorted) of a file,
    from its packets (no decode), or None."""
    import subprocess
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "packet=pts_time", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=60, check=True).stdout
    except Exception:  # noqa: BLE001
        return None
    times = []
    for line in out.split():
        v = line.split(",")[0]
        try:
            times.append(float(v))
        except ValueError:
            continue
    return sorted(times) or None


def reel_segments(path, proof_segments, fps):
    """Reel segments of a CONCATENATED proof reel: where each window's
    frames start in it and how many it holds. A window piece can render a
    frame short of its duration (the concat keeps every window on its
    nominal start, leaving a gap), so counting frames from the durations
    drifts a frame per window; the packets' own times place each window
    exactly. Falls back to the durations when the times are unreadable."""
    rows = sorted(proof_segments or [], key=lambda r: float(r["reel_start"]))
    f3 = float(fps or 30.0)
    times = _frame_times(path)
    starts = None
    if times:
        base = times[0]
        starts = []
        for r in rows:
            want = float(r["reel_start"]) - 0.5 / f3
            starts.append(next((i for i, t in enumerate(times)
                                if t - base >= want), len(times)))
    segs = []
    for k, r in enumerate(rows):
        a, b = float(r["start"]), float(r["end"])
        if starts is not None:
            f0 = starts[k]
            n = (starts[k + 1] if k + 1 < len(starts) else len(times)) - f0
        else:
            f0 = int(round(float(r["reel_start"]) * f3))
            n = int(round((b - a) * f3))
        segs.append({"a": a, "b": b, "path": path,
                     "first_f": frame_of(a, f3), "reel_f0": int(f0),
                     "frames": max(0, int(n))})
    return segs


class JobRenderer:
    """Production: ``preview_check`` jobs (the existing changed-section
    proof kind) on the agent's ToolContext. ``enqueue("with", ...)`` renders
    the CURRENT version; ``enqueue("without", ...)`` renders the newest
    earlier version that is this edit minus exactly the proved devices, or
    answers (None, reason). A finished proof render of the same exact
    request (``render_signature``: EDL, ranges, deployed code) is reused, so
    asking again after "still rendering" never renders twice."""

    lane = "interactive"

    def __init__(self, ctx, *, budget_s=240.0, poll_s=1.0, devs=None,
                 expected_fps=None):
        self.ctx = ctx
        self.budget_s = float(budget_s)
        self.poll_s = float(poll_s)
        self.devs = devs or []
        self.jobs = {}
        self.expected_fps = expected_fps

    def _version_for(self, tag, edl):
        """(version, json) to render for ``tag``; ``edl`` is the CURRENT
        edit (with the devices). The reference must equal it minus the
        devices in everything that renders — a version that also differs
        in captions, graphics or the cut would read those changes as the
        device's — and must render on the same clock."""
        import db as dbx
        ctx = self.ctx
        cur = ctx.latest_edl()
        if tag == "with":
            return int(cur["version"]), cur.get("json")
        ids = {d["id"] for d in self.devs}
        target = _pure(ablate(edl or {}, self.devs))
        try:
            history = ctx.db.run(dbx.edl_history, ctx.project_id, 12)
        except Exception as e:  # noqa: BLE001
            return None, f"history unavailable ({str(e)[:80]})"
        for row in history or []:
            v = int(row["version"])
            if v >= int(cur["version"]):
                continue
            got = ctx.db.run(dbx.get_edl_version, ctx.project_id, v)
            j = (got or {}).get("json") or {}
            present = {str(i.get("id")) for i in j.get("fx") or []
                       if isinstance(i, dict)} | {
                str(m.get("id")) for m in j.get("motion") or []
                if isinstance(m, dict) and (
                    m.get("composite") is not None
                    or m.get("layer") == "behind_subject")}
            if present & ids:
                continue
            if _pure(j) != target:
                return None, (f"v{v} lacks the devices but also differs from "
                              "this edit in other ways (write the devices "
                              "in their own batch for an exact reference)")
            if not same_clock(edl or {}, j):
                return None, (f"v{v} renders on another clock (no frame, "
                              "no fx)")
            return v, j
        return None, "no earlier version without these devices"

    def _finished(self, signature):
        """The newest DONE preview_check job of this exact request that
        produced a reel, or None."""
        if not signature:
            return None

        def q(conn):
            with conn.cursor() as cur:
                cur.execute("""SELECT id FROM video_jobs
                               WHERE project_id = %s
                                 AND type = 'preview_check'
                                 AND state = 'done'
                                 AND payload->>'render_signature' = %s
                                 AND result->>'render_asset_id' IS NOT NULL
                               ORDER BY id DESC LIMIT 1""",
                            (self.ctx.project_id, signature))
                row = cur.fetchone()
                return row["id"] if row else None
        try:
            return self.ctx.db.run(q)
        except Exception:  # noqa: BLE001 — reuse is an optimisation
            return None

    def enqueue(self, tag, edl, ranges):
        import agent_tools
        import db as dbx
        version, got = self._version_for(tag, edl)
        if version is None:
            return None, got
        ctx = self.ctx
        pages = [ranges[i:i + 6] for i in range(0, len(ranges), 6)]
        flat = [p for page in pages for p in page]
        signature = agent_tools._render_signature(
            {"json": got, "version": version}, "preview_check", flat)
        done = self._finished(signature)
        if done is not None:
            self.jobs[tag] = (done, version)
            return done, None
        payload = agent_tools._child_payload(ctx, {
            "edl_version": version, "check_ranges": flat,
            "check_pages": pages, "proof_pages": len(pages),
            "source": "agent_preview_check",
            "agent_job_id": (ctx.job or {}).get("id"),
            "proof_device": tag,
            "proof_geometry": {"long_edge": PROOF_LONG_EDGE,
                               "max_fps": PROOF_MAX_FPS},
            "render_signature": signature})
        job_id, _created = ctx.db.run(
            dbx.get_or_enqueue_preview_check_job, ctx.project_id,
            ctx.job["user_id"], payload)
        self.jobs[tag] = (job_id, version)
        return job_id, None

    def _lease_check(self):
        """A proof never outlives the turn that asked for it (the same rule
        as wait_for_job)."""
        import db as dbx
        job = getattr(self.ctx, "job", None) or {}
        if job.get("total_claims") is None or job.get("id") is None:
            return
        if not self.ctx.db.run(dbx.lease_is_current, job["id"],
                               job["total_claims"]):
            raise dbx.JobLeaseLost(
                "Agent execution ended while waiting for a device proof")

    def wait(self, tag, deadline):
        """(result, None) once the render is done, else (None, why)."""
        import db as dbx
        job_id, _v = self.jobs[tag]
        while True:
            self._lease_check()
            job = self.ctx.db.run(dbx.get_job, job_id) or {}
            state = job.get("state")
            if state == "done":
                result = job.get("result") or {}
                if result.get("superseded_by"):
                    return None, (f"job {job_id} was superseded by EDL "
                                  f"v{result['superseded_by']}")
                if not result.get("render_asset_id"):
                    return None, f"job {job_id} produced no proof reel"
                return result, None
            if state == "failed":
                return None, str(job.get("error") or "proof render failed")
            if state not in ("queued", "running"):
                return None, f"job {job_id} is {state}"
            if time.time() >= deadline:
                return None, f"still rendering (job {job_id})"
            time.sleep(self.poll_s)

    def reel(self, result):
        import db as dbx
        import storage
        asset = self.ctx.db.run(dbx.get_asset, result["render_asset_id"])
        if not asset or not asset.get("storage_key"):
            raise RuntimeError(f"proof reel asset {result['render_asset_id']} "
                               "is gone")
        local = os.path.join(self.ctx.workdir,
                             f"proof_{result['render_asset_id']}.mp4")
        if not os.path.exists(local):
            storage.download_to(asset["storage_key"], local)
        import qa_metrics
        info = qa_metrics.probe(local)
        fps = float(info["fps"] or 30.0)
        if self.expected_fps and abs(fps - self.expected_fps) > 0.02:
            # a concatenated proof reel's stream rate approximates the render
            # rate; the render rate is the program's, capped for previews
            fps = self.expected_fps
        segs = reel_segments(local, result.get("proof_segments") or [],
                             fps)
        return Reel(segs, fps, (info["width"], info["height"])), {
            "path": local, "fps": fps, "width": info["width"],
            "height": info["height"],
            "segments": [[r["a"], r["b"]] for r in segs],
            "devices": result.get("devices")}


# ── analysis ─────────────────────────────────────────────────────────────

def _diff_mask(a, b, thr=CHANGE_THR):
    import numpy as np
    d = np.abs(a.astype(np.int16) - b.astype(np.int16))
    if d.ndim == 3:
        d = d.max(axis=2)
    return d > thr


def _changed_share(a, b):
    m = _diff_mask(a, b)
    return float(m.mean())


def _faces(frame):
    """[(x, y, w, h)] frontal faces on a rendered frame (OpenCV Haar), or
    None when OpenCV is unavailable."""
    try:
        import cv2
        import numpy as np
        path = os.path.join(cv2.data.haarcascades,
                            "haarcascade_frontalface_default.xml")
        det = cv2.CascadeClassifier(path)
        gray = cv2.cvtColor(np.asarray(frame), cv2.COLOR_RGB2GRAY)
        h = gray.shape[0]
        got = det.detectMultiScale(gray, 1.1, 5,
                                   minSize=(max(16, h // 20),) * 2)
        return [tuple(int(v) for v in r) for r in got]
    except Exception:  # noqa: BLE001
        return None


def _metric(name, value, bar=None, detail=None, passed=Ellipsis):
    import qa_metrics
    b = qa_metrics._norm_bar(bar, name) if (bar is not None or
                                            name in qa_metrics.DEFAULT_BARS) \
        else None
    if passed is Ellipsis:
        passed = None if (value is None or b is None) else \
            qa_metrics.check(value, b)
    return {"value": None if value is None else round(float(value), 4),
            "bar": qa_metrics.bar_text(b) if b is not None else None,
            "pass": passed, **({"detail": detail} if detail else {})}


def _card_for(dev, cards):
    return (cards or {}).get(dev.get("device")) or {}


def _family(dev, cards):
    card = _card_for(dev, cards)
    if card.get("family"):
        return card["family"]
    if dev["kind"] == "behind":
        return "type_depth"
    if dev["kind"] == "composite":
        return "composite"
    return {"accent": "punctuation", "strobe": "punctuation",
            "subject": "matte_fx", "background": "matte_fx",
            "region": "composite", "grade_window": "finish",
            "finish": "finish", "junction": "transition"}.get(
                dev.get("type"), "other")


def measure_device(dev, with_reel, without_reel, *, fps, land_f, planned_f,
                   cards=None, workdir=None, ledger=None, full_frame=None,
                   front_reel=None, ref_same_clock=True, subsampled=False):
    """One device's proof: strip and onion images and its metrics."""
    import numpy as np
    import qa_metrics
    fam = _family(dev, cards)
    item = dev["item"]
    strip_f = list(range(land_f - STRIP_BEFORE, land_f + STRIP_AFTER + 1))
    # a junction is read over its whole run (its centre is its landing)
    before, after = extent_f(item, fps)
    lo = land_f - before - 1
    want = [f for f in range(lo, land_f + after + 1) if f >= 0]
    w = with_reel.frames(want)
    wo = without_reel.frames(want + [lo - 1, want[-1] + 1]) \
        if without_reel is not None else {}
    basis = "without_device" if wo else "temporal"
    shares = {}
    for f in want:
        if f not in w:
            continue
        if wo:
            if f in wo:
                # the nearest of the reference's neighbouring frames: a
                # reference one frame off (a junction elsewhere in the same
                # proof window) reads as no change, a device as change
                shares[f] = min(_changed_share(w[f], wo[g])
                                for g in (f - 1, f, f + 1) if g in wo)
        elif f - 1 in w:
            shares[f] = _changed_share(w[f], w[f - 1])
    metrics = {}
    # landing: the first strip frame the device changes
    event_f = None
    peak = max(shares.values(), default=0.0)
    floor = max(LAND_SHARE, PEAK_SHARE * peak)
    if dev["kind"] == "fx" and item.get("stage") == "junction":
        changed = [f for f in sorted(shares) if shares[f] >= floor]
        if changed and basis == "without_device":
            # a junction is centred on its cut: its changed frames straddle
            # the cut symmetrically (duration-preserving, d/2 each side)
            span = _contiguous(changed, land_f)
            event_f = int(round((span[0] + span[-1] + 1) / 2.0)) \
                if span else None
    else:
        base = None
        if basis == "temporal":
            vals = sorted(shares.values())
            base = vals[len(vals) // 2] if vals else 0.0
        for f in sorted(shares):
            if f < land_f - STRIP_BEFORE:
                continue
            s = shares[f]
            thr = floor if base is None else max(floor, 4.0 * base)
            if s >= thr:
                event_f = f
                break
    if event_f is not None:
        lm = qa_metrics.land_err_f(events=[event_f], targets=[planned_f],
                                   search_f=STRIP_AFTER + STRIP_BEFORE)
        metrics["land_err_f"] = _metric(
            "land_err_f", lm["value"], None,
            {"event_f": event_f, "planned_f": planned_f, "basis": basis})
    elif subsampled:
        # the proof ran below the program's rate (the preview cap): a
        # device shorter than a proof frame can fall between two of them
        metrics["land_err_f"] = _metric(
            "land_err_f", None, None,
            {"planned_f": planned_f, "basis": basis,
             "why": "not visible at the proof's frame rate (below the "
                    "program's); check it on a full-rate render"},
            passed=None)
    else:
        metrics["land_err_f"] = _metric(
            "land_err_f", None, None,
            {"planned_f": planned_f, "basis": basis,
             "why": "no frame in the strip changed enough to read a "
                    "landing"}, passed=False if wo else None)
    # junction integrity: the same frames and the same audio either way
    if dev["kind"] == "fx" and item.get("stage") == "junction" and wo:
        metrics["duration_exact"] = _junction_integrity(
            with_reel, without_reel, land_f, audio=ref_same_clock)
    # changed region at the settle frame (+6) and across the strip
    settle = land_f + STRIP_AFTER
    acc = None
    for f in strip_f:
        if f in w and f in wo:
            m = _diff_mask(w[f], wo[f])
            acc = m if acc is None else (acc | m)
    # legibility of type devices: the ink is what the device added
    if fam in TYPE_FAMILIES and wo and settle in w and settle in wo:
        try:
            ink, _changed = qa_metrics.ink_mask(w[settle], wo[settle])
            if ink.any():
                lg = qa_metrics.legibility(frame=w[settle], glyph_mask=ink)
                metrics["legibility"] = _metric(
                    "legibility", lg["value"], None,
                    {"letter_h": lg["detail"].get("letter_h")})
        except Exception as e:  # noqa: BLE001
            metrics["legibility"] = _metric("legibility", None, None,
                                            {"why": str(e)[:120]})
    # glyph visibility of type behind the subject: its visible ink against
    # the same graphic drawn in front (word >= 0.65)
    if dev["kind"] == "behind" and front_reel is not None and wo and \
            settle in w and settle in wo:
        fr = front_reel.frames([settle])
        if settle in fr:
            full = _diff_mask(fr[settle], wo[settle])
            seen = _diff_mask(w[settle], wo[settle]) & full
            if full.any():
                share = float(seen.sum()) / float(full.sum())
                metrics["glyph_visible"] = _metric(
                    "glyph_visible", share, None,
                    {"ink_px": int(full.sum()), "frame": settle})
    # matte jitter: the device's own changed region over its frames
    if dev.get("type") in MATTE_TYPES and wo:
        masks = [_diff_mask(w[f], wo[f]).astype(np.float32)
                 for f in strip_f if f in w and f in wo
                 and shares.get(f, 0.0) >= floor]
        if len(masks) >= 3:
            mf = qa_metrics.matte_flicker(masks=masks)
            metrics["matte_flicker"] = _metric("matte_flicker", mf["value"])
    # flash risk on the rendered strip
    fr_frames = [w[f] for f in sorted(w)]
    if len(fr_frames) >= 2:
        fr = qa_metrics.flash_risk(frames=fr_frames, fps=fps)
        metrics["flash_risk"] = _metric("flash_risk", fr["value"])
    # collisions: ink over a face (type, region and matte devices that do
    # not cover the whole frame)
    if acc is not None and wo and land_f in wo and \
            item.get("stage") != "junction" and fam != "punctuation":
        share = float(acc.mean())
        if share < 0.6:
            faces = _faces(wo.get(settle, wo[land_f]))
            hits = []
            for (x, y, fw, fh) in faces or []:
                cover = float(acc[y:y + fh, x:x + fw].mean())
                if cover >= FACE_COLLISION_SHARE:
                    hits.append({"face": [x, y, fw, fh],
                                 "covered": round(cover, 3)})
            try:
                import keepout
                h, wd_ = acc.shape[:2]
                z = keepout.watermark_zone(wd_, h)
                zx0, zy0 = int(z[0] * wd_), int(z[1] * h)
                zx1, zy1 = int(z[2] * wd_), int(z[3] * h)
                if zx1 > zx0 and zy1 > zy0 and float(
                        acc[zy0:zy1, zx0:zx1].mean()) >= 0.05:
                    hits.append({"watermark_zone": z})
            except Exception:  # noqa: BLE001
                pass
            if faces is not None or hits:
                metrics["collisions"] = _metric(
                    "collisions", float(len(hits)), ("<=", 0),
                    {"faces": len(faces or []), "hits": hits})
    # budget: the device's ledger family against the project's intensity
    if ledger:
        try:
            import taste
            lfam = taste._CARD_LEDGER.get(fam)
            n = (ledger.get("counts") or {}).get(lfam)
            cap = (ledger.get("budgets") or {}).get(lfam)
            if lfam and n is not None:
                metrics["budget"] = _metric(
                    "budget", float(n), ("<=", cap) if cap is not None
                    else None, {"family": lfam,
                                "intensity": ledger.get("intensity")})
        except Exception:  # noqa: BLE001
            pass
    # the card's own verify list
    card = _card_for(dev, cards)
    for entry in card.get("verify") or []:
        try:
            name, op, val = qa_metrics.parse_verify(entry)
        except ValueError:
            continue
        bar = (op, val) if op else None
        if name in metrics:
            if bar is not None and metrics[name]["value"] is not None:
                metrics[name] = _metric(name, metrics[name]["value"], bar,
                                        metrics[name].get("detail"))
            continue
        if name == "region_locality" and item.get("region") and wo:
            metrics[name] = _region_locality(item, w, wo, strip_f, bar)
            continue
        metrics[name] = {"value": None, "bar": (
            qa_metrics.bar_text(bar) if bar else None), "pass": None,
            "detail": {"why": "measured by refbench, not on a proof"}}
    images = {}
    if workdir:
        try:
            images = _images(dev, w, wo, strip_f, land_f, acc, workdir,
                             metrics.get("land_err_f"))
        except Exception as e:  # noqa: BLE001
            print(f"[proof] images skipped: {e}", flush=True)
    return {"landing_f": land_f, "planned_f": planned_f, "basis": basis,
            "event_f": event_f, "metrics": metrics, **images}


def _junction_integrity(with_reel, without_reel, land_f, audio=True):
    """duration_exact over the proof piece holding the cut (the whole reel
    when the render is one file): frame count and decoded audio, with the
    junction and without it. ``audio`` False: the reference renders on
    another clock (a frameless program left without fx), so only the frame
    count is compared and the metric says so."""
    import qa_metrics
    sw, _k = with_reel.locate(land_f)
    so, _k2 = without_reel.locate(land_f)
    if sw is None or so is None:
        return _metric("duration_exact", None, None,
                       {"why": "the cut is outside the proof render"})
    try:
        r = qa_metrics.duration_exact(with_fx=sw["path"],
                                      without_fx=so["path"])
    except Exception as e:  # noqa: BLE001
        return _metric("duration_exact", None, None, {"why": str(e)[:120]})
    detail = {"audio_identical": r["detail"].get("audio_identical"),
              "frames": [r["detail"].get("frames_with"),
                         r["detail"].get("frames_without")]}
    if not audio:
        detail["audio_identical"] = None
        detail["why"] = ("audio not compared: the reference renders on "
                         "another clock (a frameless program without fx)")
        return _metric("duration_exact", r["value"], None, detail,
                       passed=(r["value"] == 0.0
                               if r["value"] is not None else None))
    return _metric("duration_exact", r["value"], None, detail,
                   passed=r["pass"])


def _contiguous(frames, near):
    """The run of consecutive frames containing (or nearest to) ``near``."""
    runs, cur = [], []
    for f in frames:
        if cur and f != cur[-1] + 1:
            runs.append(cur)
            cur = []
        cur.append(f)
    if cur:
        runs.append(cur)
    if not runs:
        return []
    return min(runs, key=lambda r: 0 if r[0] <= near <= r[-1] else
               min(abs(r[0] - near), abs(r[-1] - near)))


def _region_locality(item, w, wo, frames, bar):
    import numpy as np
    import qa_metrics
    import regions
    pairs = [(w[f], wo[f]) for f in frames if f in w and f in wo]
    if not pairs:
        return _metric("region_locality", None, bar)
    h, wd = pairs[0][0].shape[:2]
    try:
        im = regions.still(item["region"], wd, h)
        mask = np.asarray(im) > 0
    except Exception as e:  # noqa: BLE001
        return _metric("region_locality", None, bar, {"why": str(e)[:100]})
    # one pixel of feather/edge tolerance
    mask = qa_metrics._dilate(mask, 2)
    r = qa_metrics.region_locality(with_fx=[p[0] for p in pairs],
                                   without_fx=[p[1] for p in pairs],
                                   region=mask, tol=2)
    return _metric("region_locality", r["value"], bar or ("<=", 2),
                   r.get("detail"))


def _images(dev, w, wo, strip_f, land_f, acc, workdir, land_metric):
    """The strip (with / without rows) and the onion PNGs."""
    import numpy as np
    from PIL import Image, ImageDraw
    os.makedirs(workdir, exist_ok=True)
    tiles = [f for f in strip_f if f in w]
    if not tiles:
        return {}
    h0, w0 = w[tiles[0]].shape[:2]
    th = 180
    tw = max(8, int(round(w0 * th / float(h0))))
    rows = 2 if wo else 1
    label_h = 18
    sheet = Image.new("RGB", (tw * len(strip_f), rows * th + label_h + 22),
                      (16, 16, 16))
    draw = ImageDraw.Draw(sheet)
    err = (land_metric or {}).get("value")
    title = (f"{dev['id']}  landing f{land_f}"
             + (f"  err {int(err):+d}f" if isinstance(err, (int, float))
                and err == err and abs(err) != float("inf") else ""))
    draw.text((4, 3), title[:120], fill=(235, 235, 235))
    for k, f in enumerate(strip_f):
        x = k * tw
        for r, src in enumerate([w] + ([wo] if wo else [])):
            if f not in src:
                continue
            tile = Image.fromarray(src[f]).resize((tw, th))
            sheet.paste(tile, (x, 22 + r * th))
        if k == 0:
            for r, name in enumerate(["with"] + (["without"] if wo else [])):
                draw.rectangle([0, 22 + r * th, 54, 36 + r * th],
                               fill=(0, 0, 0))
                draw.text((3, 23 + r * th), name, fill=(255, 210, 60))
        rel = f - land_f
        col = (255, 80, 60) if rel == 0 else (200, 200, 200)
        draw.text((x + 4, 22 + rows * th + 2), f"{rel:+d}", fill=col)
        if rel == 0:
            draw.rectangle([x, 22, x + tw - 1, 22 + th - 1],
                           outline=(255, 80, 60), width=3)
    strip = os.path.join(workdir, f"proof_strip_{dev['id']}.png")
    sheet.save(strip)
    out = {"strip": strip}
    base = wo.get(land_f) if wo else w.get(land_f - 1)
    if base is not None:
        b = np.asarray(base).astype(np.float32)
        grey = b.mean(axis=2, keepdims=True) * 0.55
        img = np.repeat(grey, 3, axis=2)
        if acc is None and land_f in w:
            acc = _diff_mask(w[land_f], base)
        if acc is not None:
            img[acc] = img[acc] * 0.35 + np.array([255, 40, 40]) * 0.65
        onion = os.path.join(workdir, f"proof_onion_{dev['id']}.png")
        Image.fromarray(img.clip(0, 255).astype(np.uint8)).save(onion)
        out["onion"] = onion
    return out


# ── the proof ────────────────────────────────────────────────────────────

def plan_proof(edl, devs, index=None, maps=None):
    """(fps, landings {id: (landing_f, planned_f)}, windows) on the
    program clock for ``devs``."""
    import device_planner
    from timeline import Timeline
    fps = device_planner.program_fps(edl, index)
    tl = Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                  edl.get("speed") or [])
    words = (index or {}).get("words") or None
    lands = {d["id"]: landing(d["item"], fps, kind=d["kind"], words=words,
                              timeline=tl, maps=maps) for d in devs}
    ranges = proof_windows([lands[d["id"]][0] for d in devs], fps,
                           tl.out_duration,
                           extents=[extent_f(d["item"], fps) for d in devs])
    return fps, lands, ranges


def _shell(edl_version, renderer, led):
    return {"edl_version": edl_version, "render": {
        "variant": "proof480", "wall_s": 0.0,
        "lane": getattr(renderer, "lane", None), "window": None},
        "devices": [], "ledger": led, "pass": None, "notes": []}


def _ledger(edl, intensity, index):
    import taste
    try:
        return taste.ledger(edl, intensity=intensity, index=index).as_dict()
    except Exception:  # noqa: BLE001
        return None


def _cards(cards):
    if cards is not None:
        return cards
    try:
        import devices as devmod
        return devmod.load_cards()
    except Exception:  # noqa: BLE001
        return {}


def proof(tool_ctx, edl, *, ids=None, window=None, renderer=None,
          index=None, previous_edl=None, cards=None, intensity="designed",
          workdir=None, edl_version=None, maps=None):
    """Prove devices on rendered frames (INTERFACES_W1.md §8).

    Returns {edl_version, render: {variant, wall_s, lane, window},
    devices: [{id, type, device, family, rung, reason, landing_f,
    planned_f, strip, onion, metrics}], ledger, pass, notes}."""
    edl = edl or {}
    index = index if index is not None else (getattr(tool_ctx, "index", None)
                                             or {})
    cards = _cards(cards)
    devs = device_items(edl, ids=ids, window=window,
                        previous_edl=previous_edl)
    out = _shell(edl_version, renderer, _ledger(edl, intensity, index))
    if not devs:
        out["notes"].append("no device items to prove"
                            + (" in that window" if window else ""))
        return out
    fps, lands, ranges = plan_proof(edl, devs, index, maps)
    out["render"]["window"] = ranges
    if renderer is None:
        out["notes"].append("no renderer on this lane: metrics need a "
                            "proof render")
        return out
    t0 = time.monotonic()
    with_reel, winfo = _render(renderer, edl, ranges, "with")
    if with_reel is None:
        out["notes"].append(f"proof render failed: {winfo}")
        return out
    wd = workdir or getattr(tool_ctx, "workdir", None) or "."
    # Junctions and picture devices get separate references: a reference
    # that drops a junction re-cuts the window around it, so the frames of a
    # picture device sharing that window would not line up.
    for k, group in enumerate(_groups(devs)):
        tag = "without" if k == 0 else f"without{k}"
        ref = ablate(edl, group)
        clock = same_clock(edl, ref)
        if not clock:
            out["notes"].append(
                f"the reference without {', '.join(d['id'] for d in group)} "
                "renders on another clock (a frameless program left with "
                "no fx): its audio is not compared")
        wo_reel, woinfo = _render(renderer, ref,
                                  winfo.get("segments") or ranges, tag)
        if wo_reel is None:
            out["notes"].append(f"no render without {', '.join(d['id'] for d in group)} "
                                f"({woinfo}); landings and onions read the "
                                "strip's own frame-to-frame change")
        front = None
        if any(d["kind"] == "behind" for d in group):
            front, _finfo = _render(renderer, front_variant(edl, group),
                                    winfo.get("segments") or ranges,
                                    f"front{k}")
        analyse(out, edl, group, lands, fps, with_reel, winfo, wo_reel,
                cards=cards, workdir=wd, front_reel=front,
                ref_same_clock=clock)
    out["render"]["wall_s"] = round(time.monotonic() - t0, 2)
    return out


def _groups(devs):
    """[picture devices, junctions] (the non-empty ones)."""
    pic = [d for d in devs if (d["item"].get("stage") != "junction")]
    jun = [d for d in devs if d["item"].get("stage") == "junction"]
    return [g for g in (pic, jun) if g]


def analyse(out, edl, devs, lands, fps, with_reel, winfo, wo_reel, *,
            cards, workdir, front_reel=None, ref_same_clock=True):
    """Measure every device on a with/without pair of proof reels."""
    import fxgraph
    out["render"]["fps"] = winfo.get("fps")
    out["render"]["size"] = [winfo.get("width"), winfo.get("height")]
    rows = {r.get("id"): r for r in (winfo.get("devices") or [])
            if isinstance(r, dict)}
    static = {r.get("id"): r for r in fxgraph.job_report(edl, [])}
    reel_fps = float(winfo.get("fps") or fps)
    for d in devs:
        lf, pf = lands[d["id"]]
        if abs(reel_fps - fps) > 0.01:
            lf = frame_of(lf / _fps3(fps), reel_fps)
            pf = frame_of(pf / _fps3(fps), reel_fps)
        row = rows.get(d["id"]) or static.get(d["id"]) or {}
        m = measure_device(d, with_reel, wo_reel, fps=reel_fps, land_f=lf,
                           planned_f=pf, cards=cards, workdir=workdir,
                           ledger=out.get("ledger"), front_reel=front_reel,
                           ref_same_clock=ref_same_clock,
                           subsampled=reel_fps < fps - 0.5)
        rec = {"id": d["id"], "type": d["type"],
               "device": d.get("device"),
               "family": _family(d, cards),
               "rung": row.get("rung", "full" if d["kind"] != "fx"
                               else None),
               "reason": row.get("reason"),
               "landing_f": m["landing_f"], "planned_f": m["planned_f"],
               "event_f": m.get("event_f"), "basis": m["basis"],
               "strip": m.get("strip"), "onion": m.get("onion"),
               "metrics": m["metrics"]}
        out["devices"].append(rec)
    out["pass"] = verdict(out["devices"])
    return out


def verdict(records):
    """A proof's pass: False when any device failed a metric or rendered
    below full; None when none failed but a landing could not be read (no
    reference and no visible change, or a device shorter than a proof
    frame); True otherwise."""
    if not records:
        return None
    unread = False
    for rec in records:
        metrics = rec.get("metrics") or {}
        if any(v.get("pass") is False for v in metrics.values()):
            return False
        if rec.get("rung") not in (None, "full"):
            return False
        if (metrics.get("land_err_f") or {}).get("pass") is None:
            unread = True
    return None if unread else True


def _render(renderer, edl, ranges, tag):
    try:
        reel, info = renderer.render(edl, ranges, tag)
        return reel, info
    except Exception as e:  # noqa: BLE001 — a proof never fails a turn
        return None, f"{type(e).__name__}: {str(e)[:200]}"


def proof_jobs(ctx, edl, *, ids=None, window=None, index=None,
               previous_edl=None, cards=None, intensity="designed",
               budget_s=240.0, maps=None):
    """The production proof: preview_check jobs for the current version
    (and the reference without the devices), awaited up to ``budget_s``,
    then the same analysis as ``proof``. Returns the proof dict, with
    ``pending`` {job ids} when the renders are still running."""
    index = index if index is not None else (ctx.index or {})
    cards = _cards(cards)
    devs = device_items(edl, ids=ids, window=window,
                        previous_edl=previous_edl)
    version = int(ctx.latest_edl()["version"])
    jr = JobRenderer(ctx, budget_s=budget_s, devs=devs)
    out = _shell(version, jr, _ledger(edl, intensity, index))
    if not devs:
        out["notes"].append("no device items to prove")
        return out
    fps, lands, ranges = plan_proof(edl, devs, index, maps)
    out["render"]["window"] = ranges
    try:
        import config
        cap = float(getattr(config, "PREVIEW_MAX_FPS", 30.0) or 0.0)
    except Exception:  # noqa: BLE001
        cap = 30.0
    jr.expected_fps = min(fps, cap) if cap else fps
    t0 = time.monotonic()
    job_w, why = jr.enqueue("with", edl, ranges)
    if job_w is None:
        out["notes"].append(f"proof render not started: {why}")
        return out
    job_wo, why_wo = jr.enqueue("without", edl, ranges)
    deadline = time.time() + float(budget_s)
    res_w, err = jr.wait("with", deadline)
    if res_w is None:
        if str(err).startswith("still rendering"):
            out["pending"] = {"with": job_w, "without": job_wo}
        out["notes"].append(f"proof render: {err}")
        return out
    try:
        reel_w, info_w = jr.reel(res_w)
    except Exception as e:  # noqa: BLE001 — a proof never fails a turn
        out["notes"].append(f"proof render unreadable: {type(e).__name__}: "
                            f"{str(e)[:160]}")
        return out
    reel_wo = None
    if job_wo is not None:
        res_wo, err_wo = jr.wait("without", deadline)
        if res_wo is not None:
            try:
                reel_wo, _info = jr.reel(res_wo)
            except Exception as e:  # noqa: BLE001
                why_wo = f"{type(e).__name__}: {str(e)[:120]}"
        else:
            why_wo = err_wo
    if reel_wo is None:
        out["notes"].append(f"no render without the devices ({why_wo}); "
                            "landings and onions read the strip's own "
                            "frame-to-frame change")
    rate = float(info_w.get("fps") or 0.0)
    if rate and rate < fps - 0.5:
        out["notes"].append(
            f"the proof rendered at {rate:g} fps (the preview cap) against "
            f"the program's {fps:g} fps: a 1-frame device can fall between "
            "proof frames, and landings are counted in proof frames")
    out["render"]["wall_s"] = round(time.monotonic() - t0, 2)
    out["render"]["jobs"] = {k: v[0] for k, v in jr.jobs.items()}
    return analyse(out, edl, devs, lands, fps, reel_w, info_w, reel_wo,
                   cards=cards, workdir=ctx.workdir)


def summary_lines(result, limit=8):
    """The verb's text: one line per device with its landing and the
    metrics that failed or matter."""
    if not result.get("devices"):
        return "PROOF: " + ("; ".join(result.get("notes") or [])
                            or "nothing to prove")
    lines = [f"PROOF of {len(result['devices'])} device(s) on EDL "
             f"v{result.get('edl_version')}: rendered "
             f"{len(result['render'].get('window') or [])} window(s) in "
             f"{result['render'].get('wall_s')} s "
             f"({result['render'].get('variant')}) — "
             + ("PASS" if result.get("pass") else
                "NEEDS ATTENTION" if result.get("pass") is False
                else "NOT FULLY MEASURED (see the notes)")]
    for d in result["devices"][:limit]:
        bits = []
        for name, m in d["metrics"].items():
            if m.get("value") is None and m.get("pass") is None:
                continue
            v = m.get("value")
            val = "n/a" if v is None else (
                f"{v:+.0f}f" if name == "land_err_f" and v == v
                and abs(v) != float("inf") else f"{v:g}")
            flag = "" if m.get("pass") is not False else " FAIL"
            bits.append(f"{name} {val}"
                        + (f" (bar {m['bar']})" if m.get("bar") else "")
                        + flag)
        rung = d.get("rung")
        lines.append(
            f"- {d['id']} [{d.get('device') or d['type']}] lands f"
            f"{d['landing_f']} (planned f{d['planned_f']}, {d['basis']})"
            + (f", rung {rung}: {d.get('reason')}" if rung not in
               (None, "full") else "")
            + ("; " + ", ".join(bits) if bits else ""))
    for n in result.get("notes") or []:
        lines.append(f"Note: {n}")
    return "\n".join(lines)


# ── the corner mark (Gates s04, 4385 v26: 'fifteen' and 'payoff' drew
# into the free-tier mark's box; only the FINAL's picture check said so) ──

def graphics_on_mark(edl):
    """Advisory findings for every graphic whose stored footprint (the
    keep-out's measured or estimated box) lays ink on the free-tier corner
    mark's reserved box. Persistent band headlines are laid out around the
    mark and full-frame picture templates are not type: neither is judged."""
    out = []
    try:
        import caption_carry
        import keepout
        import motion_templates
        import render_qc
        for it in (edl or {}).get("motion") or []:
            if not isinstance(it, dict) or motion_templates.persistent(it) \
                    or it.get("layer") == "behind_subject" \
                    or it.get("template") in render_qc.PICTURE_TEMPLATES:
                continue
            fp = it.get("footprint") or {}
            box, ar = fp.get("box"), fp.get("ar")
            if not box or not ar or float(ar) > 0.75:
                continue            # no box, or not a portrait frame
            W, H = 1080, int(round(1080 / float(ar)))
            zone = keepout.mark_zone(W, H)
            if zone and keepout.on_mark(box, zone):
                est = " (estimated box)" if caption_carry.estimated(it) else ""
                out.append({
                    "code": "graphic_on_mark", "severity": "advisory",
                    "id": it.get("id"),
                    "message": (f"GRAPHIC ON THE CORNER MARK: '{it.get('id')}' draws from "
                                f"y {float(box[1]):.3f}{est} into the free-tier mark's box "
                                f"(top-right, down to y {zone[3]:.3f}); a free-tier final "
                                "burns the mark over it: set it lower, narrower or left of "
                                "the mark")})
    except Exception:  # noqa: BLE001 — an audit never fails over a lens
        return out
    return out


# ── audit (browserless) ─────────────────────────────────────────────────

def audit(edl, index=None, *, intensity="designed", request_text=None,
          cards=None, maps=None):
    """{ledger, flash, devices, repeats, sections, collisions, findings}
    for the EDL as written — no render, every lane."""
    import device_planner
    import devices as devmod
    import flash_safety
    import fxgraph
    import taste
    edl = edl or {}
    if cards is None:
        try:
            cards = devmod.load_cards()
        except Exception:  # noqa: BLE001
            cards = {}
    findings = []
    try:
        led = taste.ledger(edl, intensity=intensity, index=index)
        ledger = led.as_dict()
        for note in led.over_budget():
            findings.append({"code": "over_budget", "severity": "advisory",
                             "id": None, "message": note})
    except Exception as e:  # noqa: BLE001
        ledger = {"error": str(e)[:160]}
    flash = flash_safety.assess(edl, index, request_text)
    if flash.get("over_limit") and not flash.get("opted_in"):
        findings.append({"code": "flash_rate_exceeded",
                         "severity": "blocking", "id": None,
                         "message": (f"{flash['peak_per_s']} full-frame "
                                     "flashes in one second (limit "
                                     f"{flash['max_per_s']}): thin them or "
                                     "say the user asked for flashing")})
    rows = fxgraph.static_report(edl, index)
    for r in rows:
        if r.get("pending_measure"):
            # not a degrade: the matte is measured by the next render
            findings.append({"code": "device_measuring",
                             "severity": "advisory", "id": r.get("id"),
                             "message": f"{r.get('device') or r.get('id')}: "
                                        f"{r.get('reason')}"})
        elif r.get("rung") not in ("full", None):
            findings.append({"code": "device_degraded",
                             "severity": "advisory", "id": r.get("id"),
                             "message": f"{r.get('device') or r.get('id')} "
                                        f"would render at rung "
                                        f"{r.get('rung')}: {r.get('reason')}"})
    plan = device_planner.moments(edl, index or {}, maps=maps)
    items = [i for i in edl.get("fx") or [] if isinstance(i, dict)]
    repeats, sections, collisions = [], {}, []
    payoff = plan.get("payoff")
    fps = plan["fps"]
    by_dev = {}
    for it in items:
        t = it.get("start") if it.get("start") is not None else it.get("at")
        if t is None:
            continue
        t = float(t)
        card = cards.get(it.get("device")) or {}
        fam = card.get("family") or _family(
            {"kind": "fx", "type": it.get("type"), "device": None}, {})
        shot = device_planner._shot_at(plan["shots"], t)
        sec = device_planner._section_at(plan["sections"], t)
        key = it.get("device") or it.get("type")
        by_dev.setdefault(key, []).append((shot, it["id"], t))
        if fam == "punctuation":
            sections.setdefault(sec, set()).add(key)
        if payoff and fam in device_planner.HOLD_FREE and \
                payoff["t"] + 0.5 / fps < t < payoff["hold_end"]:
            findings.append({"code": "inside_payoff_hold",
                             "severity": "advisory", "id": it["id"],
                             "message": f"{it['id']} lands at {t:.2f}s, "
                                        "inside the payoff hold "
                                        f"({payoff['t']:.2f}-"
                                        f"{payoff['hold_end']:.2f}s): let "
                                        "the payoff breathe"})
        if not it.get("bind"):
            findings.append({"code": "unbound_device",
                             "severity": "advisory", "id": it["id"],
                             "message": f"{it['id']} is bound to no "
                                        "moment: re-solves (new aspect, "
                                        "song swap) cannot move it"})
        if card.get("status") == "dark" and not devmod.flag_on(card):
            findings.append({"code": "dark_device", "severity": "blocking",
                             "id": it["id"],
                             "message": f"{it['id']} uses {card['id']}, "
                                        "which is not available yet"})
    for key, uses in by_dev.items():
        shots = sorted({s for s, _i, _t in uses})
        for a, b in zip(shots, shots[1:]):
            if b == a + 1:
                ids = [i for s, i, _t in uses if s in (a, b)]
                repeats.append({"device": key, "shots": [a, b],
                                "ids": ids})
                findings.append({"code": "consecutive_shots",
                                 "severity": "advisory", "id": ids[-1],
                                 "message": f"{key} on consecutive shots "
                                            f"{a + 1} and {b + 1}: keep it "
                                            "for a deliberate callback"})
    for sec, keys in sorted(sections.items()):
        if len(keys) > 1:
            a, b = plan["sections"][sec]
            findings.append({"code": "punctuation_families",
                             "severity": "advisory", "id": None,
                             "message": f"section {a:.1f}-{b:.1f}s mixes "
                                        f"{len(keys)} punctuation families "
                                        f"({', '.join(sorted(keys))}): one "
                                        "family per section"})
    for k, a in enumerate(items):
        for b in items[k + 1:]:
            if a.get("stage") != b.get("stage") or a.get("stage") == \
                    "junction" or a.get("type") != b.get("type"):
                continue
            try:
                if float(a["start"]) < float(b["end"]) and \
                        float(b["start"]) < float(a["end"]):
                    collisions.append([a["id"], b["id"]])
            except (KeyError, TypeError, ValueError):
                continue
    for a, b in collisions:
        findings.append({"code": "overlapping_devices",
                         "severity": "advisory", "id": b,
                         "message": f"{a} and {b} overlap at the same stage"})
    # The edit's own stall before the end card (owner, 22 Oct 2026): a
    # freeze-frame, a still, a slowed tail or a fade-out in the programme's
    # last seconds. The rendered join itself is render_qc's to measure.
    try:
        import render_qc
        for note in render_qc.tail_causes(edl):
            findings.append({"code": "stall_before_end_card",
                             "severity": "advisory", "id": None,
                             "message": "STALL BEFORE END CARD: " + note})
    except Exception:  # noqa: BLE001 — an audit never fails over a lens
        pass
    findings += graphics_on_mark(edl)
    zooms = len(((edl.get("effects") or {}).get("zooms")) or [])
    sfx = len(edl.get("sfx") or [])
    return {"ledger": ledger, "flash": flash, "devices": rows,
            "repeats": repeats,
            "sections": {str(k): sorted(v) for k, v in sections.items()},
            "collisions": collisions, "payoff": (
                {"t": payoff["t"], "hold_end": payoff["hold_end"],
                 "word": payoff.get("word")} if payoff else None),
            "owner": {"zoom": zooms, "sfx": sfx,
                      "music": len(edl.get("music") or [])},
            "findings": findings}


def audit_text(result, limit=10):
    led = result.get("ledger") or {}
    lines = [f"MOTION AUDIT — ledger: {led.get('line', '?')} "
             f"(intensity {led.get('intensity', '?')}, format "
             f"{led.get('format', '?')}); flash peak "
             f"{(result.get('flash') or {}).get('peak_per_s')}/s"]
    if result.get("payoff"):
        p = result["payoff"]
        lines.append(f"Payoff: '{p.get('word')}' at {p['t']:.2f}s, hold to "
                     f"{p['hold_end']:.2f}s")
    fs = result.get("findings") or []
    if not fs:
        lines.append("No findings: every device is within budget, one "
                     "punctuation family per section, nothing in the payoff "
                     "hold.")
    for f in fs[:limit]:
        lines.append(f"- [{f['severity']}] {f['code']}"
                     + (f" ({f['id']})" if f.get("id") else "")
                     + f": {f['message']}")
    if len(fs) > limit:
        lines.append(f"(+{len(fs) - limit} more)")
    return "\n".join(lines)


# ── deterministic evidence for the visual critic (preview_critic) ───────

def critic_evidence(edl, index=None, *, proof_result=None, cards=None,
                    request_text=None):
    """[{id, time_s, metric, value, bar, pass}] deterministic rows the
    visual critic's motion findings can be corroborated by: the audit's
    flash and restraint findings (EDL-level, every lane) and the latest
    proof's failed metrics, each placed at its device's program time."""
    rows = []
    try:
        a = audit(edl, index, request_text=request_text, cards=cards)
    except Exception:  # noqa: BLE001
        a = {"findings": [], "flash": {}}
    times = {}
    for it in (edl or {}).get("fx") or []:
        if isinstance(it, dict):
            t = it.get("start") if it.get("start") is not None \
                else it.get("at")
            if t is not None:
                times[str(it.get("id"))] = float(t)
    for m in (edl or {}).get("motion") or []:
        if isinstance(m, dict) and m.get("start") is not None:
            times[str(m.get("id"))] = float(m["start"])
    for f in a.get("findings") or []:
        if f["code"] in ("flash_rate_exceeded", "inside_payoff_hold",
                         "consecutive_shots", "punctuation_families",
                         "overlapping_devices"):
            rows.append({"id": f.get("id"), "time_s": times.get(
                str(f.get("id"))), "metric": f["code"], "value": None,
                "bar": None, "pass": False, "message": f["message"]})
    for d in (proof_result or {}).get("devices") or []:
        for name, m in (d.get("metrics") or {}).items():
            if m.get("pass") is False:
                rows.append({"id": d["id"], "time_s": times.get(d["id"]),
                             "metric": name, "value": m.get("value"),
                             "bar": m.get("bar"), "pass": False})
    return rows


# ── look_at(overlay=...) (track 1F owns the parameter) ─────────────────

OVERLAYS = ("matte", "free_space", "beats",
            # Wave 3 (devices/INTERFACES_W3.md §7): faces, hands, body and
            # the world definitions, drawn by world_overlay.py
            "anchors")
_TINT = (40, 220, 255)


def _ensure_matte(ctx, src_t):
    """(chunk path, chunk t0) of the matte.person sidecar covering
    ``src_t`` when ready, else (None, status line). A look that asks for the
    matte launches the one chunk it needs (interactive priority) and never
    waits: the device write would ask for it anyway."""
    try:
        import sidecars
        import tools_ext.verify_tools as vt
        src = vt.matte_source(ctx)
        if src is None:
            return None, "no source identity for a matte on this footage"
        h = sidecars.ensure("matte.person", src,
                            [(max(0.0, src_t - 0.02), src_t + 0.02)],
                            priority="interactive")
        st = sidecars.status(h)
        if st != "ready":
            eta = sidecars.estimate_s(h)
            return None, (f"matte {st}: measuring (≈{int(eta)} s); the face "
                          "box is shown until it is ready")
        got = sidecars.fetch(h, ctx.workdir)
        for c in got:
            if c["t0"] - 1e-3 <= src_t <= c["t1"] + 1e-3:
                return c, "ready"
        return None, "matte chunk unavailable"
    except Exception as e:  # noqa: BLE001
        return None, f"matte unavailable ({type(e).__name__})"


def _mask_at(chunk, src_t, size, workdir):
    import media
    from PIL import Image
    fp = os.path.join(workdir, f"matte_{int(src_t * 1000)}.png")
    media.frame_at(chunk["path"], max(0.0, src_t - float(chunk["t0"])), fp)
    return Image.open(fp).convert("L").resize(size)


def overlay_matte(ctx, path, src_t, out_path):
    """The frame with the person matte tinted over it (or the detected face
    boxes while the matte is measuring). Returns (path, label suffix)."""
    from PIL import Image, ImageDraw
    import numpy as np
    im = Image.open(path).convert("RGB")
    chunk, status = _ensure_matte(ctx, src_t)
    if chunk is not None:
        m = _mask_at(chunk, src_t, im.size, ctx.workdir)
        a = np.asarray(m, np.float32)[..., None] / 255.0
        rgb = np.asarray(im, np.float32)
        tint = np.array(_TINT, np.float32)
        out = rgb * (1 - 0.45 * a) + tint * 0.45 * a
        edge = np.asarray(m.filter(__import__("PIL.ImageFilter",
                                              fromlist=["x"]).FIND_EDGES))
        out[edge > 60] = (255, 255, 255)
        Image.fromarray(out.clip(0, 255).astype(np.uint8)).save(
            out_path, quality=90)
        cover = float(a.mean())
        return out_path, f" [matte: person covers {cover:.0%}]"
    draw = ImageDraw.Draw(im)
    import keepout
    faces = keepout.detect_faces(path) or []
    W, H = im.size
    for box in faces:
        x0, y0, x1, y1 = box[:4] if len(box) >= 4 else box[0][:4]
        draw.rectangle([x0 * W, y0 * H, x1 * W, y1 * H], outline=_TINT,
                       width=3)
    im.save(out_path, quality=90)
    return out_path, f" [{status}]"


def free_rects(occupied, cols=10, rows=10, min_cells=6):
    """Up to three largest free rectangles (frame fractions) on a coarse
    grid where ``occupied`` boxes ([x0, y0, x1, y1] fractions) are filled."""
    grid = [[False] * cols for _ in range(rows)]
    for x0, y0, x1, y1 in occupied:
        for r in range(rows):
            for c in range(cols):
                cx0, cy0 = c / cols, r / rows
                cx1, cy1 = (c + 1) / cols, (r + 1) / rows
                if cx0 < x1 and cx1 > x0 and cy0 < y1 and cy1 > y0:
                    grid[r][c] = True
    found = []
    taken = [[False] * cols for _ in range(rows)]
    for _n in range(3):
        best = None
        for r0 in range(rows):
            for c0 in range(cols):
                for r1 in range(r0, rows):
                    for c1 in range(c0, cols):
                        if any(grid[r][c] or taken[r][c]
                               for r in range(r0, r1 + 1)
                               for c in range(c0, c1 + 1)):
                            break
                        area = (r1 - r0 + 1) * (c1 - c0 + 1)
                        if best is None or area > best[0]:
                            best = (area, r0, c0, r1, c1)
        if best is None or best[0] < min_cells:
            break
        _a, r0, c0, r1, c1 = best
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                taken[r][c] = True
        found.append([round(c0 / cols, 2), round(r0 / rows, 2),
                      round((c1 + 1) / cols, 2), round((r1 + 1) / rows, 2)])
    return found


def overlay_free_space(path, out_path, *, output=True):
    """The frame with what type must avoid shaded (faces, the watermark and
    the platform's UI bands on a vertical output) and the largest free
    rectangles outlined with their fractions."""
    from PIL import Image, ImageDraw
    import keepout
    im = Image.open(path).convert("RGB")
    W, H = im.size
    occupied, labels = [], []
    faces = keepout.detect_faces(path)
    for f in faces or []:
        b = list(f[:4]) if len(f) >= 4 else list(f[0][:4])
        try:
            b = list(keepout.face_zone(tuple(b)))   # hair, ears and chin
        except Exception:  # noqa: BLE001
            b = list(keepout.grow(b, 0.03))
        occupied.append([max(0.0, b[0]), max(0.0, b[1]), min(1.0, b[2]),
                         min(1.0, b[3])])
        labels.append("face")
    if output and keepout.portrait(W, H):
        occupied += [[0.0, 0.0, 1.0, keepout.SAFE_Y0],
                     [0.0, keepout.SAFE_Y1, 1.0, 1.0],
                     [keepout.RAIL_X1, keepout.RAIL_Y0, 1.0,
                      keepout.RAIL_Y1]]
        labels += ["top band", "bottom band", "button rail"]
        try:
            occupied.append(keepout.watermark_zone(W, H))
            labels.append("watermark")
        except Exception:  # noqa: BLE001
            pass
    over = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    for b in occupied:
        d.rectangle([b[0] * W, b[1] * H, b[2] * W, b[3] * H],
                    fill=(255, 60, 60, 70))
    rects = free_rects(occupied)
    for k, b in enumerate(rects):
        d.rectangle([b[0] * W, b[1] * H, b[2] * W - 1, b[3] * H - 1],
                    outline=(80, 255, 120, 255), width=3)
        d.text((b[0] * W + 6, b[1] * H + 4),
               f"free {k + 1}: {b[0]:.1f},{b[1]:.1f}-{b[2]:.1f},{b[3]:.1f}",
               fill=(80, 255, 120, 255))
    Image.alpha_composite(im.convert("RGBA"), over).convert("RGB").save(
        out_path, quality=90)
    face_note = ("faces unmeasured" if faces is None
                 else f"{len(faces)} face(s)")
    return out_path, (f" [free space: {face_note}; free rects "
                      + "; ".join(f"{b}" for b in rects) + "]")


def beat_rows(edl, index, *, maps=None):
    """Program-clock markers for the beats overlay: stresses, phrase ends,
    cuts, the user's beats and every device landing."""
    import device_planner
    plan = device_planner.moments(edl, index or {}, maps=maps)
    rows = []
    for m in plan["moments"]:
        k = m["kind"]
        if k in ("stress", "word.payoff", "phrase_end", "cut", "beat",
                 "downbeat", "drop", "section"):
            rows.append((m["t"], k, m.get("word")))
    for it in (edl or {}).get("fx") or []:
        t = it.get("start") if it.get("start") is not None else it.get("at")
        if t is not None:
            rows.append((float(t), "device", it.get("id")))
    return sorted(rows), plan.get("payoff")


_BEAT_COLOURS = {"stress": (255, 210, 60), "word.payoff": (255, 80, 60),
                 "phrase_end": (160, 160, 255), "cut": (255, 255, 255),
                 "beat": (80, 255, 120), "downbeat": (80, 255, 120),
                 "drop": (255, 80, 200), "section": (120, 200, 255),
                 "device": (40, 220, 255)}


def overlay_beats(path, out_path, center_t, rows, span=1.5, payoff=None):
    """A ruler under the frame: ±``span`` s of program time around
    ``center_t`` with every moment and device landing marked."""
    from PIL import Image, ImageDraw
    im = Image.open(path).convert("RGB")
    W, H = im.size
    band = max(46, H // 9)
    out = Image.new("RGB", (W, H + band), (12, 12, 12))
    out.paste(im, (0, 0))
    d = ImageDraw.Draw(out)

    def x_of(t):
        return int((t - (center_t - span)) / (2 * span) * (W - 1))
    if payoff and payoff.get("t") is not None:
        a, b = x_of(payoff["t"]), x_of(payoff["hold_end"])
        if b > 0 and a < W:
            d.rectangle([max(0, a), H + 2, min(W - 1, b), H + band - 2],
                        fill=(70, 20, 20))
            d.text((max(2, a) + 3, H + band - 26), "payoff hold",
                   fill=(255, 120, 100))
    near = []
    lane = 0
    for t, kind, what in rows:
        if abs(t - center_t) > span:
            continue
        x = x_of(t)
        col = _BEAT_COLOURS.get(kind, (200, 200, 200))
        y0 = H + (4 if kind in ("cut", "section", "device") else band // 3)
        d.line([x, y0, x, H + band - 4], fill=col, width=2)
        tag = {"word.payoff": "payoff", "phrase_end": "end",
               "device": "dev"}.get(kind, kind)
        if what and kind in ("stress", "word.payoff"):
            tag += f" {what}"
        d.text((min(W - 60, x + 3), H + 3 + 12 * (lane % 2)), tag[:18],
               fill=col)
        lane += 1
        near.append(f"{kind}{'=' + str(what) if what else ''}@{t:.2f}")
    cx = x_of(center_t)
    d.line([cx, H, cx, H + band], fill=(255, 255, 255), width=1)
    d.text((4, H + band - 14), f"{center_t - span:.1f}s", fill=(150, 150, 150))
    d.text((W - 44, H + band - 14), f"{center_t + span:.1f}s",
           fill=(150, 150, 150))
    out.save(out_path, quality=90)
    return out_path, (" [beats: " + ", ".join(near[:8]) + "]") if near \
        else " [beats: none within ±1.5 s]"


def apply_overlay(ctx, kind, path, *, src_t=None, out_t=None, edl=None,
                  stage="post", output=True, cache=None):
    """Draw ``kind`` on a copy of the frame at ``path``. ``stage`` 'raw' is
    the source frame before the output geometry (the matte is drawn there
    so the crop and zoom carry it); 'post' is the delivered frame (free
    space, beats). Returns (path, label suffix); the frame unchanged when
    the kind does not apply at this stage."""
    if kind not in OVERLAYS:
        return path, ""
    out_path = os.path.join(ctx.workdir, f"ov_{kind}_{os.path.basename(path)}"
                            .rsplit(".", 1)[0] + ".jpg")
    try:
        if kind == "matte":
            if stage != "raw" or src_t is None:
                return path, ""
            return overlay_matte(ctx, path, float(src_t), out_path)
        if stage != "post":
            return path, ""
        if kind == "free_space":
            return overlay_free_space(path, out_path, output=output)
        if kind == "anchors":
            import world_overlay
            return world_overlay.draw(ctx, path, out_path, out_t, edl)
        if kind == "beats":
            if out_t is None:
                return path, " [beats: this source moment is not in the edit]"
            cache = cache if cache is not None else {}
            if "rows" not in cache:
                import tools_ext.verify_tools as vt
                maps = vt.music_maps(ctx, edl) if edl else None
                cache["rows"], cache["payoff"] = beat_rows(
                    edl or {}, ctx.index, maps=maps)
            return overlay_beats(path, out_path, float(out_t), cache["rows"],
                                 payoff=cache.get("payoff"))
    except Exception as e:  # noqa: BLE001 — an overlay never blinds a look
        return path, f" [overlay {kind} unavailable: {type(e).__name__}]"
    return path, ""
