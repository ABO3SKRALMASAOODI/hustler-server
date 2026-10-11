"""Matte Stack v2: the person matte in PROGRAM space (Wave 1, track 1A).

ROADMAP Wave 1 §1A; contract in worker/devices/INTERFACES_W1.md §1.

WHAT CHANGES. Until Wave 1 a subject mask was measured per item, on one
continuous window, in the output framing of the moment it was written
(matte.measure_and_build). Anything that moved the framing afterwards — the
default face-FOLLOWING crop, a cut inside the window, a speed ramp, a
picture card, set_frame — made that mask a cut-out of the wrong pixels, so
the renderer gave up and drew the words in front (motion_layer.behind_why).
The biggest product defect in the roadmap: text-behind switched itself off
under the default crop.

Now the matte is measured ONCE, in SOURCE space, by the ``matte.person``
sidecar (sidecars.py: RVM on the uncropped 960x540 proxy, chunked on the
shot grid, recurrent state reset at every cut) and the renderer carries it
through THE SAME per-block chain as the picture:

    chunks on the source clock (each frame stamped on the source frame it
      was measured on; black where no chunk is ready: inert)
    -> firmed (the v11 S-curve: a doubted limb is body, not translucency)
    -> per render block: the picture's own trim, speed pieces, normalize /
       follow window / card source rect, block tail (frame-exact)
    -> concat in program order (inserts black, payoff holds the held frame)
    -> the post-concat geometry the picture gets (zoom_punch, whip, shake)
    -> WORLD space  (the picture before the camera; rides every zoom)
    -> the camera chain                -> SCREEN space (screen-locked)

so a reframe, a cut, a ramp or a card never re-measures and never misses
(ROADMAP A1). Missing chunks are black: a device there takes its substitute
rung, every other window keeps its matte.

WHO ASKS. A compiler's prepare calls ``fxgraph.request_matte(plan, item,
label, space)``; behind-subject texts and graphics whose stored mask no
longer fits (and items written without one: ``PROGRAM_KEY``) ask through
the renderer. Nothing asks: nothing is built (md5 identity).

WHAT IS NOT HERE. Pixels (fxgraph / the compilers), the sidecar job
(analysis_jobs.py), the store (sidecars.py). Faces: ``face_mask`` draws
the faces.yunet boxes as soft ellipses in source space and sends them
through the same chain; ``face_box`` maps a face to program fractions.

THROUGH APPLY_EDIT_BATCH (an MCP client that cached its tool list before
Wave 1; edit_batch fills the program-matte SubjectMatte, the renderer draws
it — the words in front, and saying so, while the matte is measuring):

  a graphic behind the speaker
    {"action": "upsert", "layer": "motion", "id": "m1",
     "value": {"id": "m1", "layer": "behind_subject"}}
  words behind the speaker
    {"action": "upsert", "layer": "texts", "id": "t9", "value": {
     "id": "t9", "text": "FLYING CARS", "start": 2.0, "end": 9.0,
     "template": "title", "size_scale": 2.4,
     "behind": {"asset_key": "sidecar:matte.person"}}}
  screen-locked under a push (track 1C's composite slot)
    {"action": "upsert", "layer": "motion", "id": "m1",
     "value": {"id": "m1", "layer": "behind_subject",
               "composite": {"stage": "screen"}}}
  back in front / gone
    {"action": "upsert", "layer": "motion", "id": "m1",
     "value": {"id": "m1", "layer": "above_captions"}}
    {"action": "upsert", "layer": "texts", "id": "t9",
     "value": {"id": "t9", "behind": null}}
    {"action": "remove", "layer": "texts", "id": "t9"}
"""

import contextvars
import json
import math
import os
import subprocess
import time

# ── the ship switch (INTERFACES_W1.md §0.4) ─────────────────────────────
# 1A has no fx member module: this flag switches the program matte on for
# the renderer, the behind-subject verbs and apply_edit_batch. The preview
# switch (VALMERA_FX_PREVIEW=1 or "1a") turns it on in tests and refbench.
LIVE = True

# A SubjectMatte that names no measured clip but the PROGRAM matte: what a
# behind-subject item written over a cut, a follow crop, a ramp or a card
# (or by an MCP client through apply_edit_batch, which cannot measure)
# stores. edit_batch.PROGRAM_MATTE_KEY is the same literal (a test pins it):
# that module is loaded by the backend by path and imports nothing here.
PROGRAM_KEY = "sidecar:matte.person"
PROGRAM_FP = "program-matte"
PROGRAM_METHOD = "program"

LABELS = ("person", "face")
# Filters the program-matte chain emits (the renderer checks this lane has
# every one before it builds it; fxgraph.filter_missing, the
# ffmpeg_filter_missing fault). The picture's own geometry filters (crop,
# scale, pad, perspective, zoompan, overlay, dblur) are the picture's.
FILTERS = ("interleave", "lut", "alphamerge", "gblur", "overlay", "trim",
           "setpts", "tpad", "concat", "color", "split", "format", "scale",
           "nullsink", "null", "fps")
SPACES = ("world", "screen")

# Chunk QA bars (the manifest's ``qa``: analysis_jobs._MatteQA, a 12 px band
# at 960 px). A chunk over either bar is not ``ok``: crisp devices (fill,
# outline, poster) demote to soft ones (glow) in its window. Calibrated on
# the VFL windows (tests/test_w1a_matte.py documents the numbers): a clean
# RVM matte of a talking speaker measures 0.030-0.042 flicker and <=0.00004
# chatter in this band; the photometric fallback and u2net on dark-on-dark
# footage measure 0.07+ and 0.003+.
QA_FLICKER_OK = 0.06
QA_CHATTER_OK = 0.002

# How long a final waits for its matte before degrading (A1: finals at most
# 90 s; previews never wait).
FINAL_WAIT_S = 90.0


def _config():
    try:
        import config
        return config
    except Exception:  # noqa: BLE001 — the backend loads pure modules only
        return None


def live():
    """Is the program matte on in this process?"""
    cfg = _config()
    if cfg is not None and getattr(cfg, "PROGRAM_MATTE_OFF", False):
        return False
    try:
        from fx_schemas import live as _live
        return bool(_live(LIVE, "1a"))
    except Exception:  # noqa: BLE001
        return bool(LIVE)


def sidecar_params():
    """The ``matte.person`` params every Wave 1 reader and writer keys with.
    None keeps Wave 0's keys (sidecars.py defaults). Set by config: the
    measured speed option (ROADMAP 1A design 5) is ``downsample_ratio`` —
    RVM's coarse pass at 0.375 of the 960 px proxy instead of 0.533."""
    cfg = _config()
    ds = getattr(cfg, "MATTE_DOWNSAMPLE_RATIO", None) if cfg else None
    if ds:
        return {"downsample_ratio": round(float(ds), 4)}
    return None


# ── geometry helpers ────────────────────────────────────────────────────

def program_to_source(tl, t0, t1):
    """SOURCE spans [(a, b)] that play inside program window [t0, t1]
    (speed pieces mapped, inserts skipped, in program order)."""
    out = []
    if tl is None:
        return out
    t0, t1 = float(t0), float(t1)
    for (s, e), pcs, off, L in zip(tl.segs, tl.pieces, tl.offsets,
                                   tl.seg_out_len):
        lo, hi = max(t0, off), min(t1, off + L)
        if hi - lo <= 1e-4:
            continue
        acc = off
        for ps, pe, f in pcs:
            plen = (pe - ps) / f
            a, b = max(lo, acc), min(hi, acc + plen)
            if b - a > 1e-4:
                out.append((round(ps + (a - acc) * f, 4),
                            round(ps + (b - acc) * f, 4)))
            acc += plen
    return out


def merge(spans, gap=1e-3):
    out = []
    for a, b in sorted((float(a), float(b)) for a, b in spans if b > a):
        if out and a <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


# ── the render's source (who may LAUNCH sidecars from a render) ─────────
# _run_render_job knows the project's proxy (the file a sidecar job reads);
# render_edl does not. It sets this for the duration of a render so the
# first render that needs a window requests it (INTERFACES_W1.md §1.7).
RENDER_LAUNCH = contextvars.ContextVar("matte_track_launch", default=None)
# The ProgramMatte of the render in progress (render_edl sets it before
# fxgraph.prepare, so compilers' request_matte reach it).
CURRENT = contextvars.ContextVar("matte_track_current", default=None)


def can_launch():
    """May this process start sidecar jobs? Where the Cloudflare sidecar
    lane is reachable (the agent, MCP and executor containers route child
    calls through the Worker to the batch lane), or on a single development
    box with no remote compute at all (the job runs in a child process
    here). Never anywhere else: a lane without the models would only write
    model_missing markers over the windows it asked for."""
    try:
        import config
        import remote
        if remote.sidecar_available():
            return True
        remote_any = (config.REMOTE_EXECUTOR_URL
                      or config.MODAL_EXECUTOR_ENABLED
                      or (config.CLOUDFLARE_EXECUTOR_ENABLED
                          and config.CLOUDFLARE_EXECUTOR_URL))
        role = str(getattr(config, "WORKER_ROLE", "") or "").lower()
        return not remote_any and role not in ("executor", "batch_executor",
                                               "agent_executor",
                                               "mcp_executor",
                                               "shorts_executor")
    except Exception:  # noqa: BLE001
        return False


def launch_source(sha, proxy, index, project_id=None):
    """A SourceRef that can LAUNCH sidecar jobs: the original's sha, the
    proxy the job reads, the index's duration and shot grid (the grid every
    reader keys on). None when anything is missing, or when this process
    may not launch (can_launch)."""
    if not can_launch():
        return None
    try:
        import sidecars
        video = (index or {}).get("video") or {}
        meta = (proxy or {}).get("meta") or {}
        info = {"duration": video.get("duration")
                or (proxy or {}).get("duration_s"),
                "fps": (proxy or {}).get("fps") or meta.get("fps")
                or video.get("fps"),
                "width": (proxy or {}).get("width") or meta.get("width"),
                "height": (proxy or {}).get("height") or meta.get("height")}
        key = (proxy or {}).get("storage_key")
        if not sha or not key:
            return None
        src = sidecars.source_from(sha, key, info, project_id,
                                   (index or {}).get("shots"))
        return src if sidecars._launchable(src) else None
    except Exception:  # noqa: BLE001
        return None


def read_source(index):
    """A read-only SourceRef from the index (sha from its artifact keys,
    duration and shots): what a render keys its chunks with."""
    try:
        import sidecars
        video = (index or {}).get("video") or {}
        # the artifact keys name it; a fixture index (refbench) names it
        sha = sidecars.index_source_sha(index) or video.get("sha256")
        if not sha or not video.get("duration"):
            return None
        return sidecars.source_from(sha, None, video, None,
                                    (index or {}).get("shots"))
    except Exception:  # noqa: BLE001
        return None


# ── lazy splits (a label consumed by however many asked) ───────────────

class Tap:
    """A label handed out to any number of consumers: each ``take`` names
    one more output, ``close`` emits the split (or a nullsink when nobody
    took it). Filtergraph text order does not matter, so the split can be
    written after its consumers."""

    def __init__(self, label):
        self.label = label
        self.outs = []
        self.closed = False

    def take(self):
        out = f"{self.label}t{len(self.outs)}"
        self.outs.append(out)
        return out

    def close(self, parts):
        if self.closed:
            return
        self.closed = True
        n = len(self.outs)
        if n == 0:
            parts.append(f"[{self.label}]nullsink")
        elif n == 1:
            parts.append(f"[{self.label}]null[{self.outs[0]}]")
        else:
            parts.append(f"[{self.label}]split={n}"
                         + "".join(f"[{o}]" for o in self.outs))


# ── the S-curves (v11 firming; the output edge) ─────────────────────────
# matte.py v11: a doubted limb is body. RVM holds a dark arm on a dark room
# at alpha ~0.5; composited as-is the words show THROUGH the arm at half
# strength ("my body is being eaten by a word"). The same pointwise curve,
# as a 256-entry lut on the source-resolution chunks (the connectivity half
# of v11 needs a frame-level pass and stays with the per-item matte).
FIRM_LO, FIRM_HI = 0.15, 0.55


def curve_expr(lo, hi):
    """lut expression: smoothstep of val over [lo, hi] (0..1 of 255)."""
    a, b = 255.0 * float(lo), 255.0 * float(hi)
    t = f"clip((val-{a:.3f})/{b - a:.3f},0,1)"
    return f"255*{t}*{t}*(3-2*{t})"


EDGE_DEFAULT = "0.2,0.8"
# Behind-subject composites pull the matte's edge this far inside the
# subject (px per 1080 px of frame height, config MATTE_BEHIND_SHRINK): a
# 540p matte enlarged 3-4x carries a pixel or two of the wall beyond the
# subject's edge, and laid over the type that reads as a light rim round
# the head. Measured on AT-1A-1's final (tools/at1a_matte.py; mean
# |render - glyph| on the type within 3 px outside the subject, and the
# composite edge's static flicker): no inset 20.1 / 0.026; 1.0 px soft
# 5.0 / 0.027; 1.5 px soft 4.3 / 0.037; three 3x3 erosions 3.9 / 0.050 —
# a min filter amplifies the edge's frame-to-frame noise, a blurred
# threshold averages it.
BEHIND_SHRINK_PER_1080 = 1.0


def _shrink_per_1080():
    cfg = _config()
    per = getattr(cfg, "MATTE_BEHIND_SHRINK", BEHIND_SHRINK_PER_1080) \
        if cfg else BEHIND_SHRINK_PER_1080
    try:
        return min(4.0, max(0.0, float(per)))
    except (TypeError, ValueError):
        return 0.0


def behind_inset_px(height):
    """How far (px, fractional) a behind composite at ``height`` rows pulls
    the matte inside the subject; 0 below half a pixel."""
    d = _shrink_per_1080() * float(height or 0) / 1080.0
    return d if d >= 0.5 else 0.0


def behind_inset(height):
    """The filter suffix that insets the matte for a behind composite
    ("" = none): a soft inset — blurred by sigma = d, then a smoothstep
    over 0.76-0.92, whose middle sits one sigma inside a hard edge
    (Phi(1) = 0.84) — the contour moves in by ~d and stays a ~0.7 d ramp."""
    d = behind_inset_px(height)
    if not d:
        return ""
    return f",gblur=sigma={d:.2f},lut=c0='{curve_expr(0.76, 0.92)}'"


def edge_lut():
    """The output-resolution edge curve (config MATTE_EDGE: "lo,hi" or
    off): after the 3-4x enlargement a 540p matte's edge is a 3-4 px ramp;
    a mild smoothstep across its middle sets the contour where the ramp's
    midline is, a pixel or so wide — crisp like a hand roto, anti-aliased,
    and it cannot move the edge."""
    cfg = _config()
    raw = getattr(cfg, "MATTE_EDGE", EDGE_DEFAULT) if cfg else EDGE_DEFAULT
    if str(raw).strip().lower() in ("", "off", "0", "none"):
        return None
    try:
        lo, hi = (float(v) for v in str(raw).split(","))
        if 0.0 <= lo < hi <= 1.0:
            return curve_expr(lo, hi)
    except (TypeError, ValueError):
        pass
    return None


# ── the program matte of one render ─────────────────────────────────────

def _status_rank(st):
    return {"ready": 0, "pending": 1, "failed": 2, "absent": 3}.get(st, 3)


class ProgramMatte:
    """The person matte (and face mask) of one render, in program space.

    Request side (prepare time): ``want`` program windows; ``info`` says
    what they will get (INTERFACES_W1.md §1.1 MatteInfo). Emission side
    (the renderer): ``materialize`` binds the ready chunk files as inputs;
    ``source_tap`` is the source-clock stream every render block trims, as
    the picture's blocks trim the source; ``set_world`` / ``build_screen``
    hold the program-space results; ``pad`` hands a fresh copy to each
    consumer; ``close`` ends every unconsumed branch."""

    def __init__(self, tl, source, *, preview=True, workdir=None,
                 launch=None, final_wait_s=FINAL_WAIT_S, params=None):
        self.tl = tl
        self.source = source
        self.launch = launch
        self.preview = bool(preview)
        self.workdir = workdir
        self.final_wait_s = 0.0 if preview else max(0.0, float(final_wait_s))
        self.params = sidecar_params() if params is None else params
        self.wants = []            # [(t0, t1, item id, label, space)]
        self.labels = set()
        self.spaces = set()
        self._chunks = {}          # (t0, t1) -> {"key", "status", "meta", ...}
        self._handles = []
        self._t_start = time.monotonic()
        self.files = []            # [{key, t0, t1, path, meta}] (person)
        self.face_files = []       # [{t0, t1, path, fps, w, h}]
        self.plan = None
        self._src_tap = None
        self._face_tap = None
        self.world = {}            # label -> Tap (world space)
        self.screen = {}           # label -> Tap (screen space)
        self.extra_taps = []       # the renderer's (matte blocks)
        self.notes = []
        self.mw = self.mh = None   # chunk geometry
        self.fm = None
        # seconds from the sidecar clock (the proxy's own, -ss relative to
        # its start) to the clock the render's trims read: the container's
        # start when the main input is read with -copyts, else 0
        self.clock_offset = 0.0

    # -- requests ----------------------------------------------------------
    def want(self, t0, t1, item_id=None, label="person", space="world"):
        """A consumer needs the matte over program [t0, t1]."""
        try:
            t0, t1 = float(t0), float(t1)
        except (TypeError, ValueError):
            return
        if t1 - t0 <= 1e-4:
            return
        base = str(label or "person").split(":", 1)[0]
        self.wants.append((t0, t1, item_id, base, space))
        self.labels.add(base)
        self.spaces.add("screen" if space == "screen" else "world")
        self._resolve()

    def want_item(self, item, label="person", space="world"):
        it = item or {}
        a, b = it.get("start"), it.get("end")
        if (a is None or b is None) and it.get("at") is not None:
            # a junction item: the cut and a second either side (its handles)
            a, b = float(it["at"]) - 1.0, float(it["at"]) + 1.0
        if a is None or b is None:
            return
        self.want(max(0.0, float(a)), float(b), it.get("id"), label, space)

    def src_ranges(self, label="person"):
        spans = []
        for t0, t1, _i, lab, _sp in self.wants:
            if lab == label:
                spans += program_to_source(self.tl, t0, t1)
        return merge(spans)

    # -- sidecar status ----------------------------------------------------
    def _resolve(self):
        """Statuses for every chunk the wants touch (launching what nobody
        has when this render may launch), waiting on a final up to the
        render's shared deadline."""
        if self.source is None:
            return
        try:
            import sidecars
        except Exception:  # noqa: BLE001
            return
        ranges = self.src_ranges("person")
        if not ranges:
            return
        spans = sidecars.chunks_for(self.source.shots,
                                    self.source.duration_s, ranges)
        todo = [s for s in spans if s not in self._chunks]
        if not todo:
            return
        try:
            if self.launch is not None:
                h = sidecars.ensure("matte.person", self.launch,
                                    [(a, b) for a, b in todo],
                                    priority="interactive",
                                    params=self.params)
            else:
                h = read_handle("matte.person", self.source, todo,
                                self.params)
            self._handles.append(h)
            left = self.final_wait_s - (time.monotonic() - self._t_start)
            if left > 0:
                sidecars.wait([h], left)
            states = sidecars._states(h, fresh=left > 0)
            for c in h.chunks:
                st, m, failed = states.get(c.key, ("absent", None, None))
                fb = None
                if st != "ready" and failed and failed.get("fallback"):
                    store = sidecars._store()
                    fb = sidecars._fallback_manifest(store, h, failed) \
                        if store is not None else None
                self._chunks[(c.t0, c.t1)] = {
                    "key": c.key, "status": "ready" if (st == "ready" or fb)
                    else st, "meta": m or fb, "fallback": bool(fb),
                    "handle": h, "chunk": c}
        except Exception as e:  # noqa: BLE001 — a matte question never fails
            self.notes.append(f"matte status unreadable ({str(e)[:120]})")
            for s in todo:
                self._chunks.setdefault(s, {"key": None, "status": "absent",
                                            "meta": None, "fallback": False})

    def chunk_rows(self, ranges=None):
        """[((t0, t1), row)] for the chunks over ``ranges`` (SOURCE)."""
        rows = sorted(self._chunks.items())
        if ranges is None:
            return rows
        return [(k, r) for k, r in rows
                if any(k[1] > a + 1e-6 and k[0] < b - 1e-6
                       for a, b in ranges)]

    def covers(self, t0, t1, label="person"):
        """Is every chunk under program [t0, t1] ready?"""
        if label == "face":
            return bool(self.face_files)
        rng = program_to_source(self.tl, t0, t1)
        rows = self.chunk_rows(rng)
        return bool(rows) and all(r["status"] == "ready" for _k, r in rows)

    # -- MatteInfo (INTERFACES_W1.md §1.1, pinned shape) -------------------
    def info(self, label="person"):
        base = str(label or "person").split(":", 1)[0]
        if base == "face":
            return self._face_info(label)
        if self.source is None:
            return absent_info(label, "no source identity on this render "
                               "(the index names no source)")
        ranges = self.src_ranges("person")
        rows = self.chunk_rows(ranges)
        if not rows:
            return absent_info(label, "no window asked for the matte")
        windows, sources, fl, ch = [], set(), [], []
        for (a, b), r in rows:
            m = r.get("meta") or {}
            qa = m.get("qa") or {}
            src = None
            if r["status"] == "ready":
                src = "u2net" if r.get("fallback") or str(
                    m.get("model_id", "")).startswith("u2net") else "rvm"
                sources.add(src)
            f, c = qa.get("flicker"), qa.get("edge_chatter")
            if isinstance(f, (int, float)):
                fl.append(float(f))
            if isinstance(c, (int, float)):
                ch.append(float(c))
            ok = (r["status"] == "ready"
                  and (f is None or float(f) <= QA_FLICKER_OK)
                  and (c is None or float(c) <= QA_CHATTER_OK))
            for p0, p1 in self.tl.span_to_out(a, b) if self.tl else []:
                windows.append({"t0": round(p0, 3), "t1": round(p1, 3),
                                "status": r["status"], "source": src,
                                "flicker": f, "edge_chatter": c, "ok": ok})
        windows.sort(key=lambda w: w["t0"])
        n_ready = sum(1 for _k, r in rows if r["status"] == "ready")
        status = ("ready" if n_ready == len(rows) else
                  "partial" if n_ready else "absent")
        source = ("rvm" if sources == {"rvm"} else
                  "u2net" if sources == {"u2net"} else
                  "rvm" if "rvm" in sources else None)
        if status == "ready":
            reason = ("measured" if source == "rvm" else
                      "measured by the fallback person model (u2net)")
            if any(not w["ok"] for w in windows):
                reason += "; some windows are over the matte QA bar"
        else:
            pend = sum(1 for _k, r in rows if r["status"] == "pending")
            fail = sum(1 for _k, r in rows if r["status"] == "failed")
            bits = []
            if pend:
                est = self.estimate_s()
                bits.append(f"{pend} window(s) still measuring"
                            + (f" (~{est:.0f} s)" if est else ""))
            if fail:
                bits.append(f"{fail} window(s) failed to measure")
            absent = len(rows) - n_ready - pend - fail
            if absent:
                bits.append(f"{absent} window(s) not measured yet")
            reason = "person matte " + ("partial: " if n_ready else
                                        "absent: ") + ", ".join(bits)
        return {"label": label, "status": status, "source": source,
                "reason": reason, "windows": windows,
                "qa": ({"flicker": round(max(fl), 5) if fl else None,
                        "edge_chatter": round(max(ch), 6) if ch else None}
                       if (fl or ch) else {})}

    def estimate_s(self):
        try:
            import sidecars
            return max([sidecars.estimate_s(h) for h in self._handles]
                       or [0.0])
        except Exception:  # noqa: BLE001
            return 0.0

    def _face_info(self, label):
        """The face mask's MatteInfo: ready where faces.yunet chunks are
        measured over the wanted windows (a status read; the ellipses are
        drawn at materialize)."""
        if self.source is None:
            return absent_info(label, "no source identity on this render")
        try:
            import sidecars
            ranges = []
            for t0, t1, _i, lab, _sp in self.wants:
                if lab == "face":
                    ranges += program_to_source(self.tl, t0, t1)
            ranges = merge(ranges)
            spans = sidecars.chunks_for(self.source.shots,
                                        self.source.duration_s, ranges)
            ready = sidecars.ready_chunks(
                "faces.yunet", self.source.sha256, self.source.shots,
                self.source.duration_s, ranges)
        except Exception:  # noqa: BLE001
            spans, ready = [], []
        if spans and len(ready) == len(spans) or self.face_files:
            return {"label": label, "status": "ready", "source": "yunet",
                    "reason": "face boxes from faces.yunet",
                    "windows": [], "qa": {}}
        if ready:
            return {"label": label, "status": "partial", "source": "yunet",
                    "reason": "faces.yunet measured over part of the window",
                    "windows": [], "qa": {}}
        return absent_info(label, "no ready faces.yunet sidecar covers the "
                           "window")

    # -- materialize: bind the ready chunk files as inputs -----------------
    def materialize(self, plan):
        """Fetch the ready chunks (person) and draw the face stream; add
        them to ``plan``'s inputs. Returns True when there is anything to
        build. Never raises."""
        self.plan = plan
        if self.source is None or not self.wants:
            return False
        dest = os.path.join(self.workdir or "/tmp", "pmatte")
        if "person" in self.labels:
            try:
                self._fetch_person(dest)
            except Exception as e:  # noqa: BLE001
                self.notes.append(f"matte chunks unreadable "
                                  f"({str(e)[:120]})")
                self.files = []
        if "face" in self.labels:
            try:
                self._draw_faces(dest)
            except Exception as e:  # noqa: BLE001
                self.notes.append(f"face mask not drawn ({str(e)[:120]})")
                self.face_files = []
        for f in self.files:
            plan.add_input(f"pm:{f['key']}", ["-i", f["path"]])
        for k, f in enumerate(self.face_files):
            plan.add_input(f"pmface:{k}", ["-i", f["path"]])
        return bool(self.files or self.face_files)

    def _fetch_person(self, dest):
        import sidecars
        out, seen = [], set()
        for (a, b), r in self.chunk_rows(self.src_ranges("person")):
            if r["status"] != "ready" or r.get("handle") is None:
                continue
            h = r["handle"]
            sub = sidecars.Handle(kind=h.kind, source_sha=h.source_sha,
                                  chunks=(r["chunk"],), priority=h.priority,
                                  params=h.params)
            for f in sidecars.fetch(sub, dest):
                if f["key"] in seen:
                    continue
                seen.add(f["key"])
                out.append(f)
        out.sort(key=lambda f: f["t0"])
        # one chunk geometry per source (a chunk at another size or rate —
        # a mid-deploy lane — is left out: black there, never misaligned)
        geo = None
        kept = []
        for f in out:
            m = f.get("meta") or {}
            g = (int(m.get("width") or 0), int(m.get("height") or 0),
                 round(float(m.get("fps") or 0), 3))
            if not all(g):
                continue
            geo = geo or g
            if g == geo:
                kept.append(f)
            else:
                self.notes.append(f"matte chunk {f['key'][:10]} at "
                                  f"{g} left out (not {geo})")
        self.files = kept
        if geo:
            self.mw, self.mh, self.fm = geo

    # -- emission ----------------------------------------------------------
    @property
    def active(self):
        return bool(self.files or self.face_files) and self.plan is not None

    def begin_graph(self):
        """Forget every label of a previous graph: the renderer builds its
        graph more than once per render (the dialogue leveler measures one,
        the encode runs another, a failed encode rebuilds), and each graph
        must emit its own matte chain."""
        self._src_tap = None
        self._face_tap = None
        self.world = {}
        self.screen = {}
        self.extra_taps = []

    def streams(self):
        """[(label, tap-maker)] of the masks this render builds."""
        out = []
        if self.files:
            out.append("person")
        if self.face_files:
            out.append("face")
        return out

    def source_tap(self, parts, label, cover, grid):
        """A fresh label of ``label``'s mask on the SOURCE clock.

        cover: [(s, e)] source spans every render block trims (the stream
        holds frames over all of them: black where no chunk is ready).
        grid: (origin, fps) of the source picture's frame grid: every mask
        frame is stamped with the source frame it was measured on, so a
        block trim selects exactly the frames the picture's trim selects."""
        tap = self._src_tap if label == "person" else self._face_tap
        if tap is None:
            tap = self._emit_source(parts, label, cover, grid)
            if label == "person":
                self._src_tap = tap
            else:
                self._face_tap = tap
        return tap.take()

    def _emit_source(self, parts, label, cover, grid):
        """The stream behind ``source_tap``: every chunk's frames stamped on
        the picture's frame grid (frame j of a chunk from t0 at rate fm was
        measured on the source frame nearest t0 + j / fm, so it carries that
        frame's time), black frames on every grid slot no chunk fills over
        the span the blocks trim, interleaved by time, firmed."""
        origin, sf = float(grid[0] or 0.0), float(grid[1] or 30.0)
        dt = float(self.clock_offset or 0.0)
        if label == "person":
            files = [(f["t0"], int((f.get("meta") or {}).get("frames") or 0),
                      f"pm:{f['key']}",
                      float((f.get("meta") or {}).get("fps") or sf))
                     for f in self.files]
            w, h = self.mw, self.mh
            curve = curve_expr(FIRM_LO, FIRM_HI)
        else:
            files = [(f["t0"], int(round((f["t1"] - f["t0"]) * f["fps"])),
                      f"pmface:{k}", float(f["fps"]))
                     for k, f in enumerate(self.face_files)]
            w, h = self.face_files[0]["w"], self.face_files[0]["h"]
            curve = None
        u = "pmf" if label == "face" else "pms"

        def slot(t):
            return int(math.floor((t - origin) * sf + 0.5))
        lo = min(float(a) for a, _b in cover) - 1.0
        hi = max(float(b) for _a, b in cover) + 1.0
        lo_k = max(slot(max(0.0, lo)), 0)
        hi_k = slot(hi)
        pieces = []                # (first slot, last slot, label)
        for k, (t0, frames, key, fm) in enumerate(files):
            lab = self.plan.input_label(key)
            if lab is None or frames <= 0:
                continue
            t0 = t0 + dt                       # onto the render's trim clock
            first = slot(t0)
            last = slot(t0 + (frames - 1) / fm)
            if last < lo_k or first > hi_k:
                continue
            # rounded to the link's time base, never truncated (a
            # truncated N/fps/TB stamps some frames a tick early, and a
            # 1/fps time base makes that tick a whole frame)
            ts = (f"floor(({origin:.6f}+floor(({t0:.6f}+N/{fm:.6f}"
                  f"-{origin:.6f})*{sf:.6f}+0.5)/{sf:.6f})/TB+0.5)")
            parts.append(f"[{lab}]format=gray,setsar=1,trim=end_frame="
                         f"{frames},setpts='{ts}'[{u}c{k}]")
            pieces.append((first, last, f"{u}c{k}"))
        # black on every grid slot of [lo, hi] no chunk fills
        gaps, at = [], lo_k
        for first, last, _l in sorted(pieces):
            if first > at:
                gaps.append((at, first - 1))
            at = max(at, last + 1)
        if hi_k >= at:
            gaps.append((at, hi_k))
        for k, (g0, g1) in enumerate(gaps):
            n = g1 - g0 + 1
            parts.append(f"color=c=black:s={w}x{h}:r={sf:.6f}:"
                         f"d={(n + 2) / sf:.6f},format=gray,setsar=1,"
                         f"trim=end_frame={n},"
                         f"setpts='floor(({origin:.6f}+({g0}+N)/{sf:.6f})"
                         f"/TB+0.5)'"
                         f"[{u}g{k}]")
            pieces.append((g0, g1, f"{u}g{k}"))
        labels = [p[2] for p in sorted(pieces)]
        firm = f",lut=c0='{curve}'" if curve else ""
        if len(labels) == 1:
            parts.append(f"[{labels[0]}]null{firm}[{u}src]")
        else:
            parts.append("".join(f"[{x}]" for x in labels)
                         + f"interleave=nb_inputs={len(labels)}{firm}"
                         f"[{u}src]")
        return Tap(f"{u}src")

    def set_world(self, label, out_label):
        self.world[label] = Tap(out_label)

    def dims(self, label="person"):
        """(w, h) of a stream's source-clock frames."""
        if label == "face" and self.face_files:
            return (self.face_files[0]["w"], self.face_files[0]["h"])
        return (self.mw, self.mh)

    def build_screen(self, parts, chain, force=False):
        """The screen-space copies: ``chain(in, out) -> [parts]`` is the
        camera chain (the same shots as the picture's). Built for a label
        when a consumer asked for screen space (or ``force``)."""
        for label, tap in list(self.world.items()):
            if label in self.screen:
                continue
            if not (force or "screen" in self.spaces or label == "face"):
                continue
            src = tap.take()
            out = f"pm{label[0]}scr"
            parts.extend(chain(src, out))
            self.screen[label] = Tap(out)

    def pad(self, space="world", label="person"):
        """A fresh program-space pad, or None (absent: the consumer's
        substitute rung)."""
        taps = self.screen if space == "screen" else self.world
        tap = taps.get(label)
        if tap is None and space == "screen":
            tap = self.world.get(label)          # no camera: the same frames
        return tap.take() if tap is not None and not tap.closed else None

    def close(self, parts):
        for tap in (list(self.extra_taps) + list(self.world.values())
                    + list(self.screen.values())):
            tap.close(parts)
        for tap in (self._src_tap, self._face_tap):
            if tap is not None:
                tap.close(parts)

    # -- faces -------------------------------------------------------------
    def _draw_faces(self, dest):
        """The faces.yunet boxes over the wanted windows as soft ellipses on
        the source clock (one gray clip; the person chain carries it)."""
        import sidecars
        ranges = []
        for t0, t1, _i, lab, _sp in self.wants:
            if lab == "face":
                ranges += program_to_source(self.tl, t0, t1)
        ranges = merge(ranges)
        if not ranges:
            return
        src = self.source
        rows = sidecars.ready_chunks("faces.yunet", src.sha256, src.shots,
                                     src.duration_s, ranges)
        samples = []
        for _c, m in rows:
            data = sidecars.read_json_chunk("faces.yunet", src.sha256, m) \
                or {}
            for s in data.get("samples") or []:
                samples.append((float(s.get("t", 0.0)),
                                [f[:4] for f in s.get("faces") or []
                                 if len(f) >= 4]))
        if not samples:
            return
        samples.sort(key=lambda s: s[0])
        lo, hi = ranges[0][0], ranges[-1][1]
        fps = min(30.0, float(src.fps or 30.0))
        w = 480
        h = max(2, int(round(w * float(src.height or 9) /
                             float(src.width or 16) / 2.0)) * 2) \
            if src.width and src.height else 270
        os.makedirs(dest, exist_ok=True)
        path = os.path.join(dest, f"faces_{int(lo * 1000)}_{int(hi * 1000)}"
                            f".mp4")
        draw_face_clip(path, samples, lo, hi, fps, w, h, src.shots)
        self.face_files = [{"t0": lo, "t1": hi, "path": path, "fps": fps,
                            "w": w, "h": h}]


def read_handle(kind, source, spans, params=None):
    """A sidecars.Handle over grid ``spans`` that launches nothing (a
    render that may not launch reads status only)."""
    import sidecars
    sp = sidecars.spec(kind)
    resolved = sidecars.resolve_params(kind, params)
    chunks = tuple(sidecars.ChunkRef(sidecars.sidecar_key(
        kind, source.sha256, sp["model_id"], sp["model_version"], resolved,
        span), *span) for span in spans)
    return sidecars.Handle(kind=kind, source_sha=source.sha256,
                           chunks=chunks, priority="interactive",
                           params=sidecars.canonical_params(resolved))


def absent_info(label="person", reason="no program matte on this render"):
    return {"label": label, "status": "absent", "source": None,
            "reason": reason, "windows": [], "qa": {}}


def draw_face_clip(path, samples, lo, hi, fps, w, h, shots=(),
                   grow=1.25, feather=0.18):
    """Soft ellipses over the face boxes, linearly interpolated between the
    10 fps samples (never across a shot start), as a gray H.264 clip whose
    frame j sits at source lo + j / fps."""
    import numpy as np
    try:
        import cv2
    except Exception:  # noqa: BLE001
        cv2 = None
    starts = sorted(float(s) for s in shots or ())
    n = max(1, int(round((hi - lo) * fps)))
    times = [s[0] for s in samples]
    import bisect
    enc = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "gray",
         "-s", f"{w}x{h}", "-r", f"{fps:.6f}", "-i", "pipe:0",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
         "-pix_fmt", "yuv420p", "-an", path],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    try:
        for j in range(n):
            t = lo + j / fps
            shot = max([0.0] + [s for s in starts if s <= t + 1e-6])
            k = bisect.bisect_right(times, t)
            a = samples[k - 1] if k > 0 else None
            b = samples[k] if k < len(samples) else None
            if a is not None and a[0] < shot - 1e-3:
                a = None
            if b is not None and b[0] < shot - 1e-3:
                b = None
            boxes = []
            if a and b and len(a[1]) == len(b[1]) and b[0] - a[0] <= 0.35:
                u = (t - a[0]) / max(1e-6, b[0] - a[0])
                boxes = [[pa + (pb - pa) * u for pa, pb in zip(fa, fb)]
                         for fa, fb in zip(sorted(a[1]), sorted(b[1]))]
            elif a and t - a[0] <= 0.35:
                boxes = a[1]
            elif b and b[0] - t <= 0.35:
                boxes = b[1]
            m = np.zeros((h, w), np.float32)
            for x0, y0, x1, y1 in boxes:
                cx, cy = (x0 + x1) / 2.0 * w, (y0 + y1) / 2.0 * h
                rx, ry = max(1.0, (x1 - x0) / 2.0 * w * grow), \
                    max(1.0, (y1 - y0) / 2.0 * h * grow)
                d = np.sqrt(((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2)
                m = np.maximum(m, np.clip((1.0 - d) / feather + 0.5, 0, 1))
            enc.stdin.write((m * 255.0 + 0.5).astype(np.uint8).tobytes())
    finally:
        enc.stdin.close()
        enc.wait()
    del cv2


# ── the plan glue (fxgraph's 1A functions call these) ───────────────────

def for_plan(plan, create=True):
    """The ProgramMatte of ``plan``: its own, the render's (CURRENT), or —
    for a plan that knows its timeline and source — a new one. None when
    the program matte is off or the plan cannot have one."""
    if plan is None:
        return None
    pm = getattr(plan, "pm", None)
    if pm is not None:
        return pm
    if not live():
        return None
    pm = CURRENT.get()
    if pm is None and create and getattr(plan, "tl", None) is not None \
            and getattr(plan, "source", None) is not None:
        pm = ProgramMatte(plan.tl, plan.source, preview=plan.preview,
                          workdir=plan.workdir)
    if pm is not None:
        plan.pm = pm
    return pm


# ── face_mask / face_box (INTERFACES_W1.md §1.3, §1.5) ──────────────────

def face_mask(plan, name, space, ctx):
    """Mask provider for ``face`` / ``face:<index>`` (§1.3): the faces as
    soft ellipses in program space, or None. The index form is not split
    out yet: every face (documented in the report)."""
    pm = for_plan(plan, create=False)
    if pm is None:
        return None
    return pm.pad(space or "screen", "face")


def face_box(plan, t, index=None):
    """[x0, y0, x1, y1] (WORLD-space program fractions) of the face at
    program second ``t`` — the largest, or the ``index``-th from the left —
    or None. Static crops, pad, the frame's picture rect and follow crops
    are mapped exactly; inside a picture card: None."""
    try:
        import sidecars
        tl = getattr(plan, "tl", None)
        src = getattr(plan, "source", None)
        edl = getattr(plan, "edl", None)
        if tl is None or src is None:
            return None
        st = tl.out_to_src(float(t))
        if st is None:
            return None
        faces = sidecars.faces_near(src.sha256, src.shots, src.duration_s,
                                    st, near_s=0.35)
        if not faces:
            return None
        boxes = [b for b, _s in faces]
        if index is None:
            box = max(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
        else:
            boxes.sort(key=lambda b: b[0])
            if not 0 <= int(index) < len(boxes):
                return None
            box = boxes[int(index)]
        return map_box(edl, tl, st, box, getattr(plan, "W", None),
                       getattr(plan, "H", None), src)
    except Exception:  # noqa: BLE001
        return None


def map_box(edl, tl, src_t, box, W, H, src):
    """A SOURCE-fraction box at source second ``src_t`` in world-space
    program fractions (the picture's crop / pad / follow geometry)."""
    import follow
    import picture_cards
    import renderer
    if edl is None or not W or not H:
        return None
    fr = edl.get("frame") or {}
    if picture_cards.overlaps_source_card(
            edl, tl.span_to_out(src_t, src_t + 0.04) or []):
        return None
    sw, sh = float(src.width or 0), float(src.height or 0)
    if not sw or not sh:
        return None
    x, y, pw, ph = renderer.picture_pixels(int(W), int(H), fr.get("picture"))
    mode = (fr.get("mode") or "crop") if fr.get("ratio") not in (
        None, "source") else "crop"
    focus = None
    if fr.get("focus_x") is not None or fr.get("focus_y") is not None:
        focus = (fr.get("focus_x"), fr.get("focus_y"))
    spans = follow.frame_spans(edl)
    span = follow.span_at(spans, src_t) if spans and mode == "crop" else None
    if span is not None:
        c = follow.centre_at(span, src_t)
        focus = c
    if mode in ("pad", "pad_blur"):
        k = min(pw / sw, ph / sh)
        ox, oy = x + (pw - sw * k) / 2.0, y + (ph - sh * k) / 2.0
    else:
        k = max(pw / sw, ph / sh)
        fx = 0.5 if not focus or focus[0] is None else float(focus[0])
        fy = 0.5 if not focus or focus[1] is None else float(focus[1])
        cx = min(max(fx * sw * k - pw / 2.0, 0.0), sw * k - pw)
        cy = min(max(fy * sh * k - ph / 2.0, 0.0), sh * k - ph)
        ox, oy = x - cx, y - cy
    x0, y0, x1, y1 = box
    out = [(ox + x0 * sw * k) / W, (oy + y0 * sh * k) / H,
           (ox + x1 * sw * k) / W, (oy + y1 * sh * k) / H]
    out = [round(min(max(v, 0.0), 1.0), 4) for v in out]
    if out[2] - out[0] <= 0 or out[3] - out[1] <= 0:
        return None
    return out


# ── project_state's matte_status (1F wires it; INTERFACES_W1.md §6.3) ───

def matte_status(edl, index, source=None):
    """{"status", "windows", "reason"} of the person matte over every
    behind-subject item and matte device of ``edl`` — what the agent sees in
    project_state (read-only: a status read, never a launch)."""
    try:
        from timeline import Timeline
        src = source or read_source(index)
        tl = Timeline(edl["keep"], edl.get("inserts") or [],
                      edl.get("speed") or [])
        pm = ProgramMatte(tl, src, preview=True)
        for it in needs_program_matte(edl, tl):
            pm.want(it[0], it[1], it[2])
        if not pm.wants:
            return {"status": "unused", "windows": [],
                    "reason": "no item uses the person matte"}
        info = pm.info("person")
        return {"status": info["status"], "windows": info["windows"],
                "reason": info["reason"]}
    except Exception as e:  # noqa: BLE001
        return {"status": "unknown", "windows": [],
                "reason": f"matte status unreadable ({str(e)[:120]})"}


# Matte-reading fx types whose windows count for matte_status (the fx
# compilers ask themselves at render time; this is the read-only view).
MATTE_FX_TYPES = ("subject", "background")


def is_program(behind):
    return isinstance(behind, dict) and behind.get("asset_key") == PROGRAM_KEY


def program_behind(tl, start, end, frame=None):
    """The SubjectMatte of an item that renders through the program matte:
    PROGRAM_KEY instead of a measured clip, the SOURCE span under its
    window (content anchoring, like every behind item) and no framing
    stamp (the matte is re-solved for every framing). None when no main
    footage plays in the window."""
    spans = program_to_source(tl, start, end)
    if not spans:
        return None
    a = round(min(s for s, _e in spans), 2)
    b = round(max(e for _s, e in spans), 2)
    if b - a < 0.2:
        b = round(a + 0.2, 2)
    return {"asset_key": PROGRAM_KEY, "src_start": a, "src_end": b,
            "fp": PROGRAM_FP, "method": PROGRAM_METHOD}


def stored_mask_why(edl, tl, item, geom_now=None):
    """Why the MEASURED mask clip a behind-subject item stores cannot
    composite in this program (None when it can, the download aside): the
    Wave 0 renderer's rules — the clip is one continuous 1x piece of its
    source span in the framing it was measured in. An item that names the
    program matte has no clip."""
    import follow
    import picture_cards
    try:
        from schemas import subject_matte_geom
    except ImportError:
        from worker_schemas import subject_matte_geom
    b = item.get("behind") or {}
    if not b:
        return "it carries no subject mask"
    if is_program(b):
        return "it renders through the program matte"
    a_src, b_src = float(b["src_start"]), float(b["src_end"])
    pieces = tl.span_to_out(a_src, b_src)
    if not pieces:
        return "its footage is no longer in the edit"
    if len(pieces) > 1:
        return "a cut now falls inside its window"
    ramp = tl.ramp_over(a_src, b_src)
    if ramp:
        # The mask is one 1x clip of source frames; a ramp shortens (or
        # stretches) that footage's program window, so the trimmed mask
        # would slide off the subject.
        return f"speed ramp {ramp[0]} now covers its footage"
    if geom_now is None:
        geom_now = subject_matte_geom(edl.get("frame"))
    if b.get("geom") and b["geom"] != geom_now:
        return "the framing changed since its mask was measured"
    if picture_cards.overlaps_source_card(edl, pieces):
        return "a source-fed picture card re-frames its footage"
    if follow.moves_during(edl, [(a_src, b_src)]):
        return "the crop follows the speaker across its footage"
    return None


def needs_program_matte(edl, tl, geom_now=None):
    """[(t0, t1, id, why)] of the behind-subject texts and graphics that
    render through the PROGRAM matte: items written with PROGRAM_KEY, and
    items whose stored mask no longer fits (stored_mask_why) — the ones
    Wave 0 drew in front."""
    out = []
    for layer in ("texts", "motion"):
        for it in (edl or {}).get(layer) or []:
            if not isinstance(it, dict) or not it.get("behind"):
                continue
            if layer == "motion" and it.get("layer") != "behind_subject":
                continue
            why = stored_mask_why(edl, tl, it, geom_now)
            if why:
                out.append((float(it["start"]), float(it["end"]),
                            it.get("id"), why))
    for it in (edl or {}).get("fx") or []:
        if isinstance(it, dict) and it.get("type") in MATTE_FX_TYPES \
                and it.get("start") is not None:
            out.append((float(it["start"]), float(it["end"]), it.get("id"),
                        None))
    return out
