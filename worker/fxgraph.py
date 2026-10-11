"""FX graph seams: where EDL.fx items compile into the render graph.

Track 0B (ROADMAP Wave 0; contract in worker/devices/INTERFACES.md §4.4).

The renderer calls six hooks at fixed points of ``build_filtergraph`` (graph
order, every one BEFORE the aspect bars, the screen-frame plate, the
watermark and the end card — owner rule 4):

  world         after grade, stylize and custom chains; before the behind-
                subject composite and the camera (rides zooms)
  screen        after the camera and screen takeovers, before overlays
  pre_captions  after picture cards and ``below_captions`` motion graphics
  over_type     after ``above_captions`` motion graphics
  junction      at each block-pair cut, with source-handle streams
  music_bus     on each user music item, after its fit and fades, before
                the mix

**No items, no filters.** With nothing at a stage a hook appends nothing and
returns its label unchanged — the md5-identity guarantee (AT-0B-1) and why a
render without fx is byte-for-byte the render it always was.

**A device can degrade; it can never fail a render** (ROADMAP A3). Every
decision that can go wrong is taken in ``prepare`` (unknown compiler, a
filter this ffmpeg lacks, a device resolver that says ``omit``, a precompute
that runs out of memory) and becomes a ``devices`` report row instead of an
exception; a compiler that raises while emitting is rolled back and reported;
and the renderer re-runs a failed encode once without fx (``without_fx``).

Compilers
---------
Each fx type names its compiler on its schema class, as a string so the
pydantic-only ``fx_schemas`` package stays import-free::

    COMPILER: ClassVar[str] = "<worker module>[:<attribute>]"

The object it resolves to (a module or an instance) provides:

  FILTERS                    ffmpeg filters it emits (checked per lane)
  prepare(plan, item)        optional: precompute, allocate inputs with
                             ``plan.add_input``; return None (full) or
                             (rung, reason) to degrade the item
  stage(ctx, items) -> label         picture stages
  junction(ctx, items) -> label|None the junction stage (see apply_junction)
  music_bus(ctx, items) -> label     the music bus
  cost_ms(plan, item)        optional: estimated ms for the report
  junction_frames(plan, item) optional: frames each side of a cut
  KEEPS_FRAME_TAGS           True when its picture stages only composite
                             through fxprims (window / region_fx / blend /
                             track_matte) or timeline-enabled filters, which
                             keep each entering frame and its metadata (the
                             picture cards' layout tags). Default False: the
                             renderer then wraps the stage on card programs
                             (renderer._keep_layout_tags), as it does for
                             stylize's frame-rebuilding kinds.

``ctx`` (``StageCtx``) carries the graph list and the plan helpers; window
math and composites come from ``fxprims`` (window, blend, region_fx,
track_matte, mask_upsample).

Masks
-----
``mask(plan, label, space)`` returns a fresh gray program-mask pad, or None
(absent: black, inert — the consumer takes its substitute rung). Providers
register by label prefix (``register_mask``); ``person`` resolves through
``program_matte``, a stub returning None until track 1A replaces it.
"""

import contextvars
import importlib
import json
import os
import re
import subprocess
import threading

import faultinject
import fxprims
import schemas

FX_STAGES = schemas.FX_STAGES
PICTURE_STAGES = tuple(s for s in FX_STAGES
                       if s not in ("junction", "music_bus"))

# ROADMAP §2 "ffmpeg filters relied on" — AT-0B-4 pins every one of these on
# the production static ffmpeg 8.1 (tests/test_w0b_ffmpeg.py runs in CI on
# the exact BtbN n8.1 build the images install, and fails the build when one
# is missing). Wave 1+ devices compile to these.
REQUIRED_FILTERS = (
    "xfade", "maskedmerge", "alphamerge", "alphaextract", "hsvhold",
    "colorhold", "pseudocolor", "lut3d", "lagfun", "displace", "varblur",
    "guided", "photosensitivity", "morpho", "remap", "dblur", "swaprect",
    "shufflepixels", "v360", "lenscorrection", "huesaturation", "threshold",
    "backgroundkey", "scdet")
# Bitstream filters and the options a device needs on them (1D datamosh:
# ``-bsf:v noise=drop=...`` drops the incoming keyframe).
REQUIRED_BSF_OPTIONS = {"noise": ("drop",)}

# Rough cost for the report when a compiler gives none: ms per output frame
# per megapixel (a 1-frame negate on 1080p measures ~1.6 ms, §2).
DEFAULT_MS_PER_MPX_FRAME = 1.0
# Junction half-length when neither the item nor its compiler names one.
DEFAULT_JUNCTION_S = 0.2
# A junction item matches a render cut within this many frames.
JUNCTION_MATCH_FRAMES = 1.5

RUNGS = ("full", "reduced", "substitute", "omit")


# ------------------------------------------------------------------ ffmpeg
_FILTER_CACHE = {}
_FILTER_LOCK = threading.Lock()
_FILTER_LINE = re.compile(r"^\s([T.])([S.])\s+(\S+)\s+\S+->\S+")


def _ffmpeg():
    return os.environ.get("FFMPEG_BIN") or "ffmpeg"


def _filter_table(ffmpeg=None):
    """{filter name: supports timeline} for this ffmpeg; {} when it cannot
    be asked (then nothing is "available" and every device degrades)."""
    exe = ffmpeg or _ffmpeg()
    with _FILTER_LOCK:
        hit = _FILTER_CACHE.get(exe)
        if hit is not None:
            return hit
        table = {}
        try:
            out = subprocess.run([exe, "-hide_banner", "-filters"],
                                 capture_output=True, text=True, timeout=30,
                                 check=False).stdout
            for line in out.splitlines():
                m = _FILTER_LINE.match(line)
                if m:
                    table[m.group(3)] = m.group(1) == "T"
        except Exception:  # noqa: BLE001 — absent ffmpeg reads as "nothing"
            table = {}
        _FILTER_CACHE[exe] = table
        return table


def available_filters(ffmpeg=None):
    return frozenset(_filter_table(ffmpeg))


def timeline_filters(ffmpeg=None):
    return frozenset(k for k, v in _filter_table(ffmpeg).items() if v)


def filter_missing(name, ffmpeg=None):
    """Is ``name`` unusable here? The fault ``ffmpeg_filter_missing:<name>``
    takes exactly this path (INTERFACES.md §6.2)."""
    if faultinject.fault(f"ffmpeg_filter_missing:{name}"):
        return True
    return name not in _filter_table(ffmpeg)


def missing_filters(names, ffmpeg=None):
    return [n for n in dict.fromkeys(names) if filter_missing(n, ffmpeg)]


def bsf_options(name, ffmpeg=None):
    """Option names of a bitstream filter, or None when it is absent."""
    try:
        out = subprocess.run([ffmpeg or _ffmpeg(), "-hide_banner", "-h",
                              f"bsf={name}"], capture_output=True, text=True,
                             timeout=30, check=False).stdout
    except Exception:  # noqa: BLE001
        return None
    if f"Bit stream filter {name}" not in out and "AVOptions" not in out:
        return None
    return set(re.findall(r"^\s+-([a-z0-9_]+)\s", out, flags=re.M))


def check_required(ffmpeg=None):
    """[problem, ...] for this ffmpeg against the pinned set (AT-0B-4).
    Empty means every ROADMAP §2 filter and bsf option is present."""
    problems = [f"filter {n} missing" for n in REQUIRED_FILTERS
                + fxprims.PRIMITIVE_FILTERS
                if n not in _filter_table(ffmpeg)]
    for bsf, opts in REQUIRED_BSF_OPTIONS.items():
        have = bsf_options(bsf, ffmpeg)
        if have is None:
            problems.append(f"bitstream filter {bsf} missing")
            continue
        problems += [f"bsf {bsf} has no option {o}" for o in opts
                     if o not in have]
    return problems


# ------------------------------------------------------------------ lanes
def lane():
    """The lane this process renders on, in device-card terms
    (``devices.LANES``). Cloudflare runs the interactive (standard-3) and
    batch (standard-4) containers in the same ``executor`` role; the
    container profile it passes says which."""
    try:
        import config
        role = (config.WORKER_ROLE or "").strip().lower()
    except Exception:  # noqa: BLE001
        role = os.environ.get("WORKER_ROLE", "worker").strip().lower()
    if role == "executor":
        profile = os.environ.get("CLOUDFLARE_CONTAINER_PROFILE", "")
        return "batch" if profile.strip().lower() == "standard-4" \
            else "interactive"
    return {"batch_executor": "batch", "agent_executor": "agent",
            "mcp_executor": "mcp", "shorts_executor": "shorts"}.get(
                role, "local")


# ------------------------------------------------------------------ report
# Per-render collector (renderer._run_render_job installs one, exactly like
# motion_layer's warning sink): every prepare / degrade appends rows here.
_REPORT_SINK = contextvars.ContextVar("fx_report_sink", default=None)
REPORT_KEYS = ("id", "device", "family", "stage", "rung", "reason", "est_ms")


def collect(sink):
    """Also record this context's device rows into ``sink`` (a list);
    returns the token for stop_collecting."""
    return _REPORT_SINK.set(sink)


def stop_collecting(token):
    _REPORT_SINK.reset(token)


def _emit(rows):
    sink = _REPORT_SINK.get()
    if sink is not None:
        sink.extend(dict(r) for r in rows)


def merge_report(rows):
    """One row per fx id, the LAST decision winning, in first-seen order —
    a render that degraded an item after prepare reports the degrade."""
    order, last = [], {}
    for r in rows or []:
        rid = r.get("id")
        if rid not in last:
            order.append(rid)
        last[rid] = {k: r.get(k) for k in REPORT_KEYS}
    return [last[i] for i in order]


def _card(device):
    if not device:
        return None
    try:
        import devices
        return (devices.load_cards() or {}).get(device)
    except Exception:  # noqa: BLE001 — the report must never fail a render
        return None


def _row(item, rung, reason, est_ms=None):
    card = _card(item.get("device"))
    return {"id": item.get("id"), "device": item.get("device"),
            "family": (card or {}).get("family"),
            "stage": item.get("stage"), "rung": rung, "reason": reason,
            "est_ms": None if est_ms is None else round(float(est_ms), 1)}


def note_dropped(edl_dict, dropped_ids):
    """Report rows for items the render path stripped (version skew: this
    lane does not know their type) — rung ``omit``. Returns the rows."""
    by_id = {str(i.get("id")): i for i in (edl_dict or {}).get("fx") or []
             if isinstance(i, dict)}
    rows = []
    for fid in dropped_ids or []:
        it = by_id.get(str(fid)) or {"id": fid}
        rows.append(_row(it, "omit",
                         f"this render lane does not know fx type "
                         f"{it.get('type')!r} (executor version skew)"))
    _emit(rows)
    return rows


def job_report(edl_dict, collected, previous=None, previous_edl=None):
    """The ``devices`` block for a render result: every fx item of the
    rendered EDL, with the decisions the render actually took (``collected``)
    over what the frames it reused say for items it never compiled.

    A stitched preview re-encodes only changed windows and stream-copies the
    rest from the previous preview: an item that render compiled, unchanged
    since (``previous_edl``), keeps the previous preview's row
    (``previous``, its ``devices`` block) — those are the very frames
    shipped. Anything else gets the static decision."""
    items = [i for i in (edl_dict or {}).get("fx") or [] if isinstance(i, dict)]
    if not items and not collected:
        return []
    got = {r.get("id"): r for r in merge_report(collected)}
    prev_rows = {r.get("id"): r for r in previous or []
                 if isinstance(r, dict)}
    prev_items = {i.get("id"): i for i in (previous_edl or {}).get("fx") or []
                  if isinstance(i, dict)}
    rows = []
    for it in items:
        iid = it.get("id")
        if iid in got:
            rows.append(got.pop(iid))
        elif iid in prev_rows and prev_items.get(iid) == it:
            rows.append({k: prev_rows[iid].get(k) for k in REPORT_KEYS})
        else:
            rung, reason = _static_rung(it)
            rows.append({k: v for k, v in _row(it, rung, reason).items()})
    rows.extend(got.values())
    return rows


# ------------------------------------------------------------------ plan
class FxPlan:
    """What one render does with its fx items: per-stage items, extra ffmpeg
    inputs, and the report rows (``notes``)."""

    def __init__(self, *, W=None, H=None, fps=30.0, preview=True,
                 workdir=None, program_s=0.0, tl=None, source=None):
        self.W, self.H = W, H
        self.fps = float(fps or 30.0)
        self.fps3 = fxprims.fps3(self.fps)
        self.preview = bool(preview)
        self.workdir = workdir
        self.program_s = float(program_s or 0.0)
        self.n_frames = fxprims.frame_at(self.program_s, self.fps)
        self.tl = tl
        self.source = source
        self.by_stage = {s: [] for s in PICTURE_STAGES}
        self.junction_items = []
        self.bus = {}
        self.inputs = []            # [(key, args)]
        self._input_idx = {}
        self.notes = []
        self.rungs = {}
        # The resolver's full answer per device item ({rung, reason,
        # substitute, params, est_ms}; devices.resolve_detail): a compiler
        # draws a substitute rung from ``ctx.device(item)``.
        self.devices = {}
        # CPU-ms the render's devices may still add (devices.render_budget_ms,
        # spent in program order); None: no cap (cost model unreadable).
        self.budget_ms = None
        # Wave 1 (INTERFACES_W1.md §1): {(label, space): [item ids]} — the
        # program mattes this render's compilers asked for in prepare
        # (request_matte). The renderer builds a matte chain only when
        # asked: no request, no mask filters (md5 identity).
        self.matte_requests = {}
        self._uid = 0
        self._compilers = {}

    # -- state -----------------------------------------------------------
    @property
    def empty(self):
        return not (any(self.by_stage.values()) or self.junction_items
                    or any(self.bus.values()))

    @property
    def needs_cfr(self):
        """Picture or junction items address PROGRAM FRAMES (enable on n),
        so the program must run on the normalized block clock: CFR at the
        render rate from frame 0, every cut on the frame its Timeline names
        (the renderer forces do_norm, as zooms do)."""
        return bool(any(self.by_stage.values()) or self.junction_items)

    def uid(self, prefix="fx"):
        self._uid += 1
        return f"{prefix}{self._uid}"

    # -- inputs ----------------------------------------------------------
    def add_input(self, key, args):
        """Ask for one more ffmpeg input (a precomputed mask clip, a map, a
        still); ``input_label(key)`` names its video stream once bound."""
        if key not in dict(self.inputs):
            self.inputs.append((key, list(args)))
        return key

    def bind_inputs(self, extra_inputs, next_idx):
        """Append the plan's inputs to the renderer's input args; returns the
        next free index."""
        for key, args in self.inputs:
            extra_inputs += args
            self._input_idx[key] = next_idx
            next_idx += 1
        return next_idx

    def input_label(self, key, stream="v"):
        idx = self._input_idx.get(key)
        return None if idx is None else f"{idx}:{stream}"

    # -- report ----------------------------------------------------------
    def note(self, item, rung, reason, est_ms=None):
        row = _row(item, rung, reason, est_ms)
        self.notes.append(row)
        self.rungs[item.get("id")] = rung
        _emit([row])
        return row

    def rung(self, item):
        return self.rungs.get(item.get("id"), "full")

    def report(self):
        return merge_report(self.notes)


def empty_plan(**kw):
    return FxPlan(**kw)


_MEMBER_CACHE = {}


def _member_classes():
    key = tuple(schemas.FX_MEMBERS)
    hit = _MEMBER_CACHE.get(key)
    if hit is None:
        hit = {cls.model_fields["type"].default: cls
               for cls in schemas.FX_MEMBERS}
        _MEMBER_CACHE.clear()
        _MEMBER_CACHE[key] = hit
    return hit


def compiler_for(fx_type):
    """The compiler object for an fx type, or None (degrade, never raise)."""
    cls = _member_classes().get(fx_type)
    spec = getattr(cls, "COMPILER", None) if cls else None
    if not spec:
        return None
    mod_name, _, attr = str(spec).partition(":")
    try:
        mod = importlib.import_module(mod_name)
        return getattr(mod, attr) if attr else mod
    except Exception:  # noqa: BLE001
        return None


def _static_rung(item):
    """The rung a render would take before graph geometry is known."""
    comp = compiler_for(item.get("type"))
    if comp is None:
        return "omit", f"no compiler for fx type {item.get('type')!r} here"
    missing = missing_filters(getattr(comp, "FILTERS", ()) or ())
    if missing:
        return "omit", f"this ffmpeg lacks {', '.join(missing)}"
    if item.get("device"):
        return _device_rung(item, None)
    return "full", "ok"


def _device_detail(item, sidecar_status, *, frames=0, budget_ms=None):
    """devices.resolve_detail for one item (INTERFACES.md §6.3): the rung
    from sidecar status, this lane's features and the item's predicted cost
    against what the render's budget has left. Never raises."""
    try:
        import devices
        d = devices.resolve_detail(item["device"], lane=lane(),
                                   sidecar_status=sidecar_status or {},
                                   budget_ms=budget_ms, frames=frames)
    except Exception as e:  # noqa: BLE001
        return {"rung": "omit", "reason": f"device resolver failed "
                f"({str(e)[:120]})", "substitute": None, "params": {},
                "est_ms": 0.0}
    if not isinstance(d, dict) or d.get("rung") not in RUNGS:
        return {"rung": "omit", "reason": f"device resolver returned "
                f"{(d or {}).get('rung') if isinstance(d, dict) else d!r}",
                "substitute": None, "params": {}, "est_ms": 0.0}
    return d


def _device_rung(item, sidecar_status):
    d = _device_detail(item, sidecar_status)
    return d["rung"], d.get("reason") or d["rung"]


def _item_frames(item, fps):
    try:
        if item.get("start") is not None and item.get("end") is not None:
            f0, f1 = fxprims.frame_span(item["start"], item["end"], fps)
            return max(1, f1 - f0)
        if item.get("at") is not None:
            return 2 * fxprims.frame_at(DEFAULT_JUNCTION_S, fps)
    except Exception:  # noqa: BLE001
        pass
    return 1


def _device_budget(plan, edl):
    """CPU-ms this render's devices may add (devices.render_budget_ms):
    previews at most +30% over their no-device baseline, finals up to the
    4-minute p95 for 60 s. None (no cap) when the cost model is unreadable."""
    try:
        import devices
        ln = lane()
        W, H = int(plan.W or 1080), int(plan.H or 1920)
        frame = (edl or {}).get("frame") or {}
        base = devices.baseline_ms(
            plan.program_s, W, H, preview=plan.preview, lane=ln,
            motion_items=len((edl or {}).get("motion") or []),
            captions=bool((edl or {}).get("captions")),
            inserts=len((edl or {}).get("inserts") or []),
            crop=bool(frame.get("ratio") and frame.get("ratio") != "16:9"))
        return float(devices.render_budget_ms(
            base, preview=plan.preview, program_s=plan.program_s, lane=ln))
    except Exception:  # noqa: BLE001 — a cost model never fails a render
        return None


def _sidecar_status(item, source, plan):
    """{kind: status} for the sidecars this item's card needs, on this
    render's source window. Previews never wait; finals wait at most the
    pinned 90 s (INTERFACES.md §2.3). Absent everything without a source."""
    card = _card(item.get("device")) or {}
    kinds = list(((card.get("needs") or {}).get("sidecars")) or [])
    if not kinds:
        return {}
    if source is None:
        return {k: "absent" for k in kinds}
    out = {}
    try:
        import sidecars
        ranges = _source_ranges(item, plan)
        for kind in kinds:
            h = sidecars.ensure(kind, source, ranges, priority="interactive")
            sidecars.wait([h], 0 if plan.preview else 90)
            out[kind] = sidecars.status(h)
    except Exception:  # noqa: BLE001
        out.update({k: "failed" for k in kinds if k not in out})
    return out


def _source_ranges(item, plan):
    tl = plan.tl
    if tl is None:
        return []
    a = b = None
    if item.get("start") is not None and item.get("end") is not None:
        a, b = tl.out_to_src(item["start"]), tl.out_to_src(item["end"])
    elif item.get("at") is not None:
        t = float(item["at"])
        a, b = tl.out_to_src(max(0.0, t - 1.0)), tl.out_to_src(t + 1.0)
    if a is not None and b is not None and b > a:
        return [(a, b)]
    return []


def _est_ms(comp, plan, item):
    fn = getattr(comp, "cost_ms", None)
    if fn is not None:
        try:
            return float(fn(plan, item))
        except Exception:  # noqa: BLE001
            pass
    mpx = (float(plan.W or 1080) * float(plan.H or 1920)) / 1e6
    if item.get("stage") in PICTURE_STAGES and item.get("start") is not None:
        f0, f1 = fxprims.frame_span(item["start"], item["end"], plan.fps)
        return (f1 - f0) * mpx * DEFAULT_MS_PER_MPX_FRAME
    if item.get("stage") == "junction":
        return 2 * fxprims.frame_at(DEFAULT_JUNCTION_S, plan.fps) * mpx \
            * DEFAULT_MS_PER_MPX_FRAME
    return 0.0


def prepare(edl, *, tl, W, H, fps, preview, workdir, source=None):
    """Resolve rungs and allocate inputs for this render's fx items.

    ``edl`` is the validated dump the renderer draws (already through
    ``schemas.strip_unknown_fx``). Returns an FxPlan — empty when there are
    no items, and then every hook is a no-op. Never raises for an item: a
    problem becomes that item's ``omit`` row."""
    plan = FxPlan(W=W, H=H, fps=fps, preview=preview, workdir=workdir,
                  program_s=(getattr(tl, "out_duration", None) or 0.0),
                  tl=tl, source=source)
    items = [i for i in (edl or {}).get("fx") or [] if isinstance(i, dict)]
    if any(i.get("device") for i in items):
        plan.budget_ms = _device_budget(plan, edl)
    for item in items:
        try:
            _prepare_item(plan, item)
        except Exception as e:  # noqa: BLE001 — degrade, never fail
            plan.note(item, "omit", f"could not be prepared "
                      f"({type(e).__name__}: {str(e)[:120]})")
    return plan


def _prepare_item(plan, item):
    comp = compiler_for(item.get("type"))
    if comp is None:
        plan.note(item, "omit",
                  f"no compiler for fx type {item.get('type')!r} here")
        return
    missing = missing_filters(getattr(comp, "FILTERS", ()) or ())
    if missing:
        plan.note(item, "omit", f"this ffmpeg lacks {', '.join(missing)}")
        return
    rung, reason = "full", "ok"
    if item.get("device"):
        d = _device_detail(item, _sidecar_status(item, plan.source, plan),
                           frames=_item_frames(item, plan.fps),
                           budget_ms=plan.budget_ms)
        rung, reason = d["rung"], d.get("reason") or d["rung"]
        plan.devices[item.get("id")] = d
        if rung == "omit":
            plan.note(item, "omit", reason)
            return
        if plan.budget_ms is not None:
            plan.budget_ms = max(0.0, plan.budget_ms
                                 - float(d.get("est_ms") or 0.0))
    stage = item.get("stage")
    if stage in PICTURE_STAGES:
        if item.get("start") is None or item.get("end") is None \
                or float(item["start"]) >= plan.program_s - 1e-6:
            plan.note(item, "omit", "its window is outside this program")
            return
    elif stage == "music_bus" and not item.get("music_id"):
        plan.note(item, "omit", "names no music item")
        return
    hook = getattr(comp, "prepare", None)
    if hook is not None:
        got = hook(plan, item)
        if got:
            rung, reason = got
            if rung not in RUNGS:
                rung, reason = "omit", f"compiler returned rung {rung!r}"
            if rung == "omit":
                plan.note(item, "omit", reason)
                return
    plan._compilers[item.get("type")] = comp
    if stage in PICTURE_STAGES:
        plan.by_stage[stage].append(item)
    elif stage == "junction":
        plan.junction_items.append(item)
    elif stage == "music_bus":
        plan.bus.setdefault(item["music_id"], []).append(item)
    est = _est_ms(comp, plan, item)
    card_est = (plan.devices.get(item.get("id")) or {}).get("est_ms")
    if card_est and getattr(comp, "cost_ms", None) is None:
        est = float(card_est)          # the card's calibrated cost model
    plan.note(item, rung, reason, est)


# ------------------------------------------------------------------ hooks
class StageCtx:
    """What a compiler sees while it emits one stage's items."""

    def __init__(self, plan, stage, parts, label, **extra):
        self.plan, self.stage, self.parts, self.label = \
            plan, stage, parts, label
        self.W, self.H, self.fps = plan.W, plan.H, plan.fps
        self.fps3 = plan.fps3
        self.preview = plan.preview
        self.n_frames = plan.n_frames
        self._mask_tails = []
        for k, v in extra.items():
            setattr(self, k, v)

    def uid(self, prefix="fx"):
        return self.plan.uid(prefix)

    def frames(self, item):
        """[f0, f1) program frames of a picture item (exclusive end)."""
        return fxprims.frame_span(item["start"], item["end"], self.fps)

    def rung(self, item):
        return self.plan.rung(item)

    def device(self, item):
        """The resolver's answer for a device item ({rung, reason,
        substitute, params, est_ms}), or None for an item with no device."""
        return self.plan.devices.get(item.get("id"))

    def note(self, item, rung, reason):
        return self.plan.note(item, rung, reason)

    def input_label(self, key, stream="v"):
        return self.plan.input_label(key, stream)

    def mask(self, label, space=None):
        return mask(self.plan, label,
                    space or ("world" if self.stage == "world"
                              else "screen"), ctx=self)


def _compile_groups(plan, items):
    groups = []
    for it in items:
        comp = plan._compilers.get(it.get("type"))
        for g in groups:
            if g[0] is comp:
                g[1].append(it)
                break
        else:
            groups.append((comp, [it]))
    return groups


def _rollback(parts, mark, plan, items, err):
    del parts[mark:]
    for it in items:
        plan.note(it, "omit", f"compile failed ({type(err).__name__}: "
                  f"{str(err)[:120]})")


def stage_keeps_frame_tags(stage, plan):
    """Do all of this stage's compilers keep each entering frame (and its
    metadata)? See KEEPS_FRAME_TAGS above."""
    if plan is None:
        return True
    return all(getattr(plan._compilers.get(it.get("type")),
                       "KEEPS_FRAME_TAGS", False)
               for it in plan.by_stage.get(stage) or [])


def apply_stage(stage, parts, vlabel, plan):
    """Compile this stage's items onto ``vlabel``; returns the new label.
    No plan or no items: appends nothing, returns ``vlabel``."""
    if plan is None or stage not in PICTURE_STAGES:
        return vlabel
    items = plan.by_stage.get(stage) or []
    if not items:
        return vlabel
    for comp, group in _compile_groups(plan, items):
        mark = len(parts)
        ctx = StageCtx(plan, stage, parts, vlabel)
        try:
            new = comp.stage(ctx, group)
            _close_masks(ctx)
        except Exception as e:  # noqa: BLE001 — degrade the group
            _rollback(parts, mark, plan, group, e)
            continue
        vlabel = new or vlabel
    return vlabel


def junction_specs(plan, cuts):
    """Match junction items to the render's cuts.

    ``cuts``: [(k, cut_frame, frames_before, frames_after)] — one per real
    cut between render block k and k+1 (block-clock frame counts of the two
    blocks). Returns [{"k", "frame", "h", "items"}]; ``h`` frames each side
    (at most half of either block, so neighbouring windows never overlap).
    Items with no cut within ±1.5 frames, or on blocks too short for a
    window, are reported ``omit``."""
    if plan is None or not plan.junction_items:
        return []
    specs = {}
    for it in plan.junction_items:
        f = float(it["at"]) * plan.fps3
        best = None
        for k, cf, nb, na in cuts:
            d = abs(cf - f)
            if d <= JUNCTION_MATCH_FRAMES and (best is None or d < best[0]):
                best = (d, k, cf, nb, na)
        if best is None:
            plan.note(it, "omit", f"no cut at {float(it['at']):g}s in this "
                      "render")
            continue
        _d, k, cf, nb, na = best
        comp = plan._compilers.get(it.get("type"))
        want = None
        fn = getattr(comp, "junction_frames", None)
        if fn is not None:
            try:
                want = int(fn(plan, it))
            except Exception:  # noqa: BLE001
                want = None
        if not want:
            want = max(1, fxprims.frame_at(DEFAULT_JUNCTION_S, plan.fps))
        cap = min(int(nb), int(na)) // 2
        h = min(int(want), cap)
        if h < 1:
            plan.note(it, "omit", "the shots either side of the cut are too "
                      "short for a junction window")
            continue
        spec = specs.setdefault(k, {"k": k, "frame": int(cf), "h": 0,
                                    "items": [], "nb": nb, "na": na})
        spec["items"].append(it)
        spec["h"] = max(spec["h"], h)
    return [specs[k] for k in sorted(specs)]


def apply_junction(parts, a_label, b_label, junction, plan):
    """Compile one cut's junction items.

    ``a_label``: the outgoing side, exactly ``2h`` frames — the outgoing
    block's last h frames, then h frames of its footage past the out-point
    (its source handle; the edge frame frozen when there is none).
    ``b_label``: the incoming side, ``2h`` frames — h frames of its footage
    before the in-point (or its first frame frozen), then its first h frames.
    Both W x H, the render rate, yuv420p, timestamps from 0.
    ``junction``: {"k", "frame" (cut frame), "h", "items", "a_handle",
    "b_handle" ("source" | "freeze")}.

    Returns a label of exactly 2h frames that REPLACES program frames
    [frame - h, frame + h) — or None: nothing to do here (the existing
    transition path keeps the cut). Both inputs are consumed either way.
    One compiler owns a cut; a second device at the same cut is omitted."""
    if plan is None:
        return None
    items = list(junction.get("items") or [])
    if not items:
        parts.append(f"[{a_label}]nullsink")
        parts.append(f"[{b_label}]nullsink")
        return None
    groups = _compile_groups(plan, items)
    comp, group = groups[0]
    for _c, other in groups[1:]:
        for it in other:
            plan.note(it, "omit", "another junction effect already owns "
                      "this cut")
    mark = len(parts)
    ctx = StageCtx(plan, "junction", parts, None, a_label=a_label,
                   b_label=b_label, junction=junction,
                   h=int(junction["h"]))
    try:
        out = comp.junction(ctx, group)
        _close_masks(ctx)
    except Exception as e:  # noqa: BLE001
        _rollback(parts, mark, plan, group, e)
        out = None
    if out is None:
        parts.append(f"[{a_label}]nullsink")
        parts.append(f"[{b_label}]nullsink")
    return out


def junction_overlay(parts, vlabel, windows, plan):
    """Lay compiled junction windows over the concatenated program.
    ``windows``: [(cut_frame, h, label)]. Each label (2h frames from 0) is
    re-padded to the program clock and replaces frames [cut-h, cut+h)."""
    for cut, h, label in windows:
        uid = plan.uid("jx")
        f0 = int(cut) - int(h)
        parts.append(f"[{label}]format=yuv420p"
                     f"{fxprims.repad(f0, plan.fps)}[{uid}p]")
        parts.append(f"[{vlabel}][{uid}p]"
                     f"{fxprims.composite_overlay(parts, vlabel)}:"
                     f"enable='{fxprims.enable_expr([(f0, f0 + 2 * h)])}'"
                     f"[{uid}o]")
        vlabel = f"{uid}o"
    return vlabel


def apply_music_bus(parts, alabel, music_id, plan):
    """Compile the bus items of one music item onto its stream (program
    clock, after the item's own fit and fades, before the mix)."""
    if plan is None:
        return alabel
    items = plan.bus.get(music_id) or []
    if not items:
        return alabel
    for comp, group in _compile_groups(plan, items):
        mark = len(parts)
        ctx = StageCtx(plan, "music_bus", parts, alabel, music_id=music_id)
        try:
            alabel = comp.music_bus(ctx, group) or alabel
        except Exception as e:  # noqa: BLE001
            _rollback(parts, mark, plan, group, e)
    return alabel


# ------------------------------------------------------------------ masks
_MASK_PROVIDERS = {}


def register_mask(prefix, provider):
    """``provider(plan, label, space, ctx) -> pad | None`` for labels
    ``<prefix>`` or ``<prefix>:<arg>`` (INTERFACES.md §4.5). Each call must
    return a pad nobody else consumes."""
    _MASK_PROVIDERS[prefix] = provider


def mask(plan, label, space=None, ctx=None):
    """A gray program-mask pad for ``label`` (``<label>[@world|@screen]``),
    or None — absent, which every consumer treats as black (inert) and its
    substitute rung. Never raises."""
    if plan is None or not label:
        return None
    name, _, at = str(label).partition("@")
    space = at or space or "screen"
    prefix = name.split(":", 1)[0]
    provider = _MASK_PROVIDERS.get(prefix)
    if provider is None and prefix in MASK_PEERS:
        provider = peer_fn(MASK_PEERS[prefix])
    if provider is None:
        return None
    try:
        return provider(plan, name, space, ctx)
    except Exception:  # noqa: BLE001
        return None


def _close_masks(ctx):
    for tail in ctx._mask_tails:
        ctx.parts.append(f"[{tail}]nullsink")
    ctx._mask_tails = []


# ── Wave 1: the program matte API (INTERFACES_W1.md §1) ─────────────────
# Function-level owner: track 1A (request_matte, matte_info, program_matte,
# handle_matte, _person_mask). The stubs below are the pinned signatures;
# every consumer treats an absent matte as its substitute rung.

MATTE_STATUSES = ("ready", "partial", "absent")


def matte_info(plan, label="person"):
    """What the program matte for ``label`` will be on this render, without
    building it. A dict (shape pinned):

      label    "person" (or "face")
      status   ready (every window measured) | partial (some windows black,
               inert) | absent
      source   "rvm" | "u2net" | "photometric" | None
      reason   one human line (the report's reason for a degrade)
      windows  [{"t0", "t1", "status", "source", "flicker", "edge_chatter",
                 "ok"}] in PROGRAM seconds; ``ok`` False = chunk QA over the
               bar: crisp devices (fill, outline, poster) demote to soft
               ones (glow) there
      qa       {"flicker", "edge_chatter"} program-wide maxima, or {}

    Track 1A (worker/matte_track.py): the ``matte.person`` sidecar chunks
    under the windows this render's consumers asked for, read (and, on a
    render that may launch, requested) through sidecars.py. A plan with no
    timeline or source identity — and every plan while the program matte is
    off — reads absent."""
    try:
        import matte_track
        pm = matte_track.for_plan(plan)
        if pm is not None:
            return pm.info(label)
        return matte_track.absent_info(label)
    except Exception:  # noqa: BLE001 — a matte question never fails a render
        return {"label": label, "status": "absent", "source": None,
                "reason": "no program matte on this render",
                "windows": [], "qa": {}}


def request_matte(plan, item, label="person", space="world"):
    """A compiler's ``prepare`` asks for the program matte (``label`` in
    ``space``: world = the picture's geometry before the camera, screen =
    after it). Records the request so the renderer builds the chain, and
    returns ``matte_info`` for the rung decision. Never raises."""
    try:
        if plan is not None:
            key = (str(label), "screen" if space == "screen" else "world")
            plan.matte_requests.setdefault(key, []).append(
                (item or {}).get("id"))
            import matte_track
            pm = matte_track.for_plan(plan)
            if pm is not None:
                pm.want_item(item, str(label).split(":", 1)[0],
                             "screen" if space == "screen" else "world")
        return matte_info(plan, label)
    except Exception:  # noqa: BLE001 — a matte question never fails a render
        return {"label": label, "status": "absent", "source": None,
                "reason": "matte request failed", "windows": [], "qa": {}}


def program_matte(plan, space="world"):
    """The person matte in program space (``space`` world: the picture's
    geometry before the camera; screen: after it), as a FRESH gray pad the
    caller consumes, or None (absent: 1B, 1C and 1D take their substitute
    rung). Built by the renderer from source-space ``matte.person`` sidecars
    through the picture's own per-block chain (worker/matte_track.py) — only
    on a render whose consumers asked in prepare (``request_matte``)."""
    try:
        import matte_track
        pm = matte_track.for_plan(plan, create=False)
        if pm is None or not pm.active:
            return None
        return pm.pad("screen" if space == "screen" else "world", "person")
    except Exception:  # noqa: BLE001
        return None


def handle_matte(ctx, side):
    """At the ``junction`` stage: the person matte of one side's 2h-frame
    handle stream (``side`` "a" = outgoing, "b" = incoming), measured on the
    handle's own SOURCE frames (frames past an out-point are not in the
    program matte), as a fresh gray pad, or None (1D's ``matte_cut`` takes
    its substitute rung). The renderer hands each junction a builder
    (``ctx.junction["matte"]``) that carries the sidecar frames under the
    handle through the handle's own normalize chain."""
    try:
        build = ((getattr(ctx, "junction", None) or {}).get("matte")
                 if ctx is not None else None)
        if build is None or side not in ("a", "b"):
            return None
        return build(side)
    except Exception:  # noqa: BLE001
        return None


def _person_mask(plan, name, space, ctx):
    return program_matte(plan, space)


register_mask("person", _person_mask)

# Mask providers that live in track modules, resolved on first use so a
# render that never asks never imports them (and a module that does not
# exist yet reads as "absent"). INTERFACES_W1.md §1.3.
MASK_PEERS = {
    "face": "matte_track:face_mask",       # 1A: faces.yunet soft ellipses
    "glyph": "compositor:glyph_mask",      # 1C: a motion item's clip alpha
    "region": "regions:region_mask",       # 1C: an fx item's FxRegion
    "hue": "gradewin:hue_mask",            # 1E: hue key (hue_key())
}


def peer(spec, *args, default=None, **kwargs):
    """Call a function another track owns, by ``"module:function"``.
    Returns ``default`` when the module or function does not exist (yet) or
    the call raises: a cross-track call can degrade a device, never fail a
    render. The pinned peer functions are listed in INTERFACES_W1.md §1."""
    fn = peer_fn(spec)
    if fn is None:
        return default
    try:
        return fn(*args, **kwargs)
    except Exception as e:  # noqa: BLE001
        print(f"[fx] peer {spec} failed: {type(e).__name__}: "
              f"{str(e)[:160]}", flush=True)
        return default


def peer_fn(spec):
    """The callable named by ``"module:function"``, or None."""
    mod_name, _, attr = str(spec or "").partition(":")
    if not mod_name or not attr:
        return None
    try:
        mod = importlib.import_module(mod_name)
    except Exception:  # noqa: BLE001
        return None
    fn = getattr(mod, attr, None)
    return fn if callable(fn) else None


def landing_frames(plan, item):
    """The program frames a device LANDS on (proof strips, land_err_f):
    the compiler's optional ``landing(plan, item) -> [frame]`` hook, else the
    item's first frame (picture stages) or its cut frame (junction).
    [] when it lands nowhere (music_bus). Never raises."""
    comp = compiler_for((item or {}).get("type"))
    fn = getattr(comp, "landing", None)
    if fn is not None:
        try:
            return [int(f) for f in fn(plan, item)]
        except Exception:  # noqa: BLE001
            pass
    fps = getattr(plan, "fps", None) or 30.0
    try:
        if item.get("stage") == "junction" and item.get("at") is not None:
            return [fxprims.frame_at(float(item["at"]), fps)]
        if item.get("start") is not None:
            return [fxprims.frame_at(float(item["start"]), fps)]
    except Exception:  # noqa: BLE001
        pass
    return []


# ------------------------------------------------------------------ fallback
def without_fx(plan, err):
    """The plan to re-render with after an encode failed with fx in it: an
    empty plan, every item reported ``omit`` with the failure. The renderer
    re-runs once with it — a device can degrade, never fail a render."""
    if plan is None or plan.empty:
        return plan
    first = (str(err).strip().splitlines() or [""])[-1][:160]
    items = ([it for its in plan.by_stage.values() for it in its]
             + plan.junction_items
             + [it for its in plan.bus.values() for it in its])
    fresh = FxPlan(W=plan.W, H=plan.H, fps=plan.fps, preview=plan.preview,
                   workdir=plan.workdir, program_s=plan.program_s,
                   tl=plan.tl, source=plan.source)
    for it in items:
        fresh.note(it, "omit", f"the fx graph failed on this lane "
                   f"({first}); rendered without it")
    return fresh


# ------------------------------------------------------------------ probe
class _Probe:
    """Compiler for the engine-only ``fx_probe`` type (fx_schemas/core.py).
    Boring on purpose: identity, negate, a rect negate, a screen blend, a
    dissolve over the handles, a bus gain — enough to prove each seam
    bit-exact and each primitive correct."""

    FILTERS = ("split", "trim", "setpts", "tpad", "overlay", "format",
               "negate", "null", "crop", "alphamerge", "blend", "xfade",
               "concat", "nullsink", "volume", "anull", "scale")
    # fxprims composites and timeline negate only: frames keep their tags
    KEEPS_FRAME_TAGS = True

    def prepare(self, plan, item):
        op = item.get("op") or "noop"
        if op == "matte_view":
            return ("omit", "no person matte on this render (program_matte "
                    "is a stub until 1A)")
        if op == "region_negate":
            return self._precompute_rect(plan, item)
        return None

    @staticmethod
    def _precompute_rect(plan, item):
        """A gray W x H mask still with the rect white, looped as an input
        (precomputed mask clip: ROADMAP §2's 0.38 CPU-s path)."""
        if faultinject.fault("oom:fx_precompute"):
            raise MemoryError("fx precompute was OOM-killed")
        if not plan.workdir or not plan.W or not plan.H:
            return ("omit", "no workdir/geometry for the mask")
        from PIL import Image, ImageDraw
        box = fxprims.rect_box(item["rect"], plan.W, plan.H)
        if box is None:
            return ("omit", "the rect is smaller than a pixel pair")
        x, y, w, h = box
        im = Image.new("L", (int(plan.W), int(plan.H)), 0)
        ImageDraw.Draw(im).rectangle([x, y, x + w - 1, y + h - 1], fill=255)
        path = os.path.join(plan.workdir, f"fxmask_{item['id']}.png")
        im.save(path)
        plan.add_input(f"probe_mask:{item['id']}", [
            "-loop", "1", "-framerate", f"{plan.fps3:.3f}",
            "-t", f"{plan.program_s + 1.0:.3f}", "-i", path])
        return None

    def junction_frames(self, plan, item):
        return item.get("frames") or None

    def stage(self, ctx, items):
        label = ctx.label
        negs = [it for it in items if it.get("op") == "negate"]
        if negs:
            label = fxprims.window(ctx.parts, label, ctx.uid("pn"),
                                   [ctx.frames(it) for it in negs], "negate",
                                   uid=ctx.uid("pnw"), timeline=True,
                                   fps=ctx.fps)
        for it in items:
            op = it.get("op") or "noop"
            spans = [ctx.frames(it)]
            u = ctx.uid("pb")
            if op == "noop":
                label = fxprims.window(ctx.parts, label, f"{u}o", spans,
                                       "null", uid=u, timeline=False,
                                       fps=ctx.fps)
            elif op == "negate_window":
                label = fxprims.window(ctx.parts, label, f"{u}o", spans,
                                       "negate", uid=u, timeline=False,
                                       fps=ctx.fps)
            elif op == "region_negate":
                m = ctx.input_label(f"probe_mask:{it['id']}")
                if m is None:
                    raise ValueError("the mask input was never bound")
                box = fxprims.rect_box(it["rect"], ctx.W, ctx.H)
                label = fxprims.region_fx(ctx.parts, label, f"{u}o", m,
                                          "negate", spans, uid=u,
                                          fps=ctx.fps, bbox=box)
            elif op == "blend_screen":
                box = fxprims.rect_box(it["rect"], ctx.W, ctx.H)
                ctx.parts.append(f"[{label}]split[{u}p][{u}q]")
                label = fxprims.blend(ctx.parts, f"{u}p", f"{u}q", f"{u}o",
                                      spans, uid=u, mode="screen",
                                      fps=ctx.fps, opacity=0.5, bbox=box)
        return label

    def junction(self, ctx, items):
        it = items[0]
        h, u, p = ctx.h, ctx.uid("pj"), ctx.parts
        if (it.get("op") or "noop") == "dissolve":
            d = 2 * h / ctx.fps3
            p.append(f"[{ctx.a_label}][{ctx.b_label}]xfade=transition=fade"
                     f":duration={d:.6f}:offset=0,trim=end_frame={2 * h},"
                     f"setpts=PTS-STARTPTS[{u}o]")
        else:
            # identity: the program's own frames, rebuilt from the handles
            p.append(f"[{ctx.a_label}]trim=end_frame={h}[{u}a]")
            p.append(f"[{ctx.b_label}]trim=start_frame={h},"
                     f"setpts=PTS-STARTPTS[{u}b]")
            p.append(f"[{u}a][{u}b]concat=n=2:v=1:a=0[{u}o]")
        return f"{u}o"

    def music_bus(self, ctx, items):
        label = ctx.label
        for it in items:
            u = ctx.uid("pm")
            if (it.get("op") or "noop") == "gain":
                g = float(it.get("gain_db") or 0.0)
                en = ""
                if it.get("start") is not None and it.get("end") is not None:
                    en = (f":enable='between(t,{float(it['start']):.3f},"
                          f"{float(it['end']):.3f})'")
                ctx.parts.append(f"[{label}]volume={g:.2f}dB{en}[{u}]")
            else:
                ctx.parts.append(f"[{label}]anull[{u}]")
            label = u
        return label


PROBE = _Probe()


def describe_plan(plan):
    """One log line: what each stage compiles (debugging aid)."""
    if plan is None or plan.empty:
        return "fx: none"
    bits = [f"{s}={len(v)}" for s, v in plan.by_stage.items() if v]
    if plan.junction_items:
        bits.append(f"junction={len(plan.junction_items)}")
    if plan.bus:
        bits.append(f"music_bus={sum(len(v) for v in plan.bus.values())}")
    omitted = [r["id"] for r in plan.report() if r["rung"] == "omit"]
    if omitted:
        bits.append(f"omitted={json.dumps(omitted)}")
    return "fx: " + " ".join(bits)
