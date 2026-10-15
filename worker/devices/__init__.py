"""Device contract: every creative device is a card (ROADMAP A3).

Track 0C. The constants below ARE the card contract other tracks write cards
against (worker/devices/INTERFACES.md §5, pinned by the round lead); the
functions are the runtime half of it:

  load_cards()          {id: card} from worker/devices/cards/<id>.json (each
                        compiled from its <id>.yaml by devices/compiler.py, so
                        no lane needs PyYAML at runtime)
  lane_features(lane)   what a lane can run: chromium, onnx.*, whisper,
                        demucs and ffmpeg:<filter>; ``None`` is this process
  resolve(card_id, ...) the rung one device renders at on this render, and why
                        (full -> reduced -> substitute -> omit): a creative
                        device may degrade, it never fails a render
  proposable(card, asked)  the owner gate on planners: a card that adds a zoom
                        or a sound is never proposed unless the user asked
  report_row(...)       one row of the render report's ``devices`` block

Cards live in worker/devices/cards/, one device per file, owned by the track
that ships the device. Nothing here imports the worker's heavy modules at
import time: the agent, MCP and Shorts lanes (no browser, no models) resolve
devices too.
"""

import functools
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess

CARDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cards")

# Required card fields, in the order a card is written.
CARD_REQUIRED = ("id", "family", "verb", "stage", "params", "needs",
                 "binds_to", "budget", "owner_flags", "rungs", "cost",
                 "exemplars", "verify", "status")
CARD_OPTIONAL = ("fx_type", "never", "description", "skill", "flag")

STATUSES = ("live", "beta", "dark")
INTENSITIES = ("calm", "designed", "charged")
OWNER_FLAGS = ("adds_sound", "adds_zoom", "flash")
# Rungs, best first. A card lists the subset it supports, always ending in
# "omit"; a substitute rung names the device it falls back to.
RUNGS = ("full", "reduced", "substitute", "omit")
FAMILIES = ("punctuation", "matte_fx", "type_depth", "transition", "camera",
            "sfx", "composite", "finish", "rhythm", "layout", "world")
# Stages a card may declare: the EDL.fx stages plus the layers devices that
# do not compile through fxgraph already write (motion graphics, texts).
STAGES = ("world", "screen", "pre_captions", "over_type", "junction",
          "music_bus", "motion", "texts")
# Moment kinds a card may bind to; "beat.kick" style dotted refinements of
# these are allowed (cls of a RhythmMap onset).
BINDS_TO = ("word", "word.payoff", "syllable", "stress", "phrase_end",
            "beat", "beat.kick", "beat.snare", "downbeat", "onset", "cut",
            "section", "drop", "gap", "time")
# Lane features a card's needs.lane_features may name.
LANE_FEATURES = ("chromium", "onnx.rvm", "onnx.u2net", "onnx.yunet",
                 "demucs", "whisper")   # plus "ffmpeg:<filter>"

# ── 0C additions (not part of the pinned enumerations above) ────────────

# Substitutes every lane can draw without a sidecar, a model or a browser
# (ROADMAP §9: fill -> full-frame flash at 0.6; poster -> colour-field card;
# glow -> halation; text-behind -> face-safe slam; LayoutDoc -> phrase_build
# -> libass approximation; tracking -> static placement; occlusion -> type in
# front). A substitute rung names one of these or another card id.
BUILTIN_SUBSTITUTES = ("full_frame_flash", "colour_field_card", "halation",
                       "face_safe_slam", "phrase_build", "libass_text",
                       "static_placement", "type_in_front",
                       # Wave 1 (INTERFACES_W1.md §3.6): subject_hold and
                       # matte_cut -> a white flash at the cut; subject
                       # isolate -> the hue key alone (no matte)
                       "flash_junction", "hue_key_only")

# Wave 1 card-id prefixes, one owner each (INTERFACES_W1.md §3.6): a track
# names its cards <prefix>_<what>, so parallel tracks never collide.
CARD_PREFIXES = {"type": "1A", "subject": "1B", "background": "1B",
                 "composite": "1C", "region": "1C", "accent": "1D",
                 "strobe": "1D", "junction": "1D", "grade": "1E",
                 "finish": "1E", "variant": "1E",
                 # Wave 3 (devices/INTERFACES_W3.md §8)
                 "anchor": "3A", "object": "3B", "plane": "3C",
                 "element": "3D", "light": "3D", "layout": "3E"}

# Sidecar kinds a card may need (INTERFACES.md §2.1; reserved names only).
SIDECAR_KINDS = ("matte.person", "faces.yunet", "rhythm.music",
                 "rhythm.speech", "embed.siglip2", "embed.dinov2",
                 "landmarks.face", "landmarks.hand", "landmarks.pose",
                 "track.points", "camera.motion", "objects.sam2",
                 "depth.small", "planes.h", "plate.clean", "ocr.text")

# The model feature each sidecar kind is produced with (for the
# model_missing fault and lane checks).
SIDECAR_FEATURE = {"matte.person": ("onnx.rvm", "onnx.u2net"),
                   "faces.yunet": ("onnx.yunet",)}

# The ffmpeg filters every production lane runs (ROADMAP §2; AT-0B-4 proves
# them on the static 8.1 pin). Named lanes advertise these; the local lane
# measures its own ffmpeg.
FFMPEG_FILTERS = ("xfade", "maskedmerge", "alphamerge", "alphaextract",
                  "hsvhold", "colorhold", "pseudocolor", "lut3d", "lagfun",
                  "displace", "varblur", "guided", "photosensitivity",
                  "morpho", "remap", "dblur", "swaprect", "shufflepixels",
                  "v360", "lenscorrection", "huesaturation", "threshold",
                  "backgroundkey", "scdet", "negate", "overlay", "geq")

# Production lanes (ROADMAP §2, inventory §1.1). The vision lane is dark
# (INTERFACES.md §2.6): its row exists so a resolver can reason about it,
# never because anything routes there.
LANES = ("interactive", "batch", "agent", "mcp", "shorts", "vision",
         "local")
_LANE_TABLE = {
    "interactive": ("chromium",),
    "batch": ("chromium", "onnx.rvm", "onnx.u2net", "whisper", "demucs"),
    "agent": (),
    "mcp": (),
    "shorts": (),
    "vision": ("onnx.rvm", "onnx.u2net"),
}

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,47}$")


# ── cards ───────────────────────────────────────────────────────────────

def _read_cards(cards_dir):
    out = {}
    try:
        names = sorted(os.listdir(cards_dir))
    except OSError:
        return out
    for fname in names:
        if not fname.endswith(".json") or fname.startswith((".", "_")):
            continue
        with open(os.path.join(cards_dir, fname), encoding="utf-8") as f:
            card = json.load(f)
        if not isinstance(card, dict) or card.get("id") != fname[:-5]:
            raise ValueError(f"devices/cards/{fname}: id must equal the "
                             "file name")
        out[card["id"]] = card
    return out


@functools.lru_cache(maxsize=4)
def _cached_cards(cards_dir):
    from . import schema
    cards = _read_cards(cards_dir)
    errors = []
    for card_id, card in cards.items():
        errors += [f"{card_id}: {e}" for e in schema.validate(card, cards)]
    if errors:
        raise ValueError("invalid device cards: " + "; ".join(errors[:8]))
    return cards


def load_cards(cards_dir=None):
    """{card id: card dict}, validated (a bad card fails at load, so CI
    catches it). Cached per directory; callers must not mutate the dicts."""
    return _cached_cards(os.path.abspath(cards_dir or CARDS_DIR))


def card(card_id, cards=None):
    return (cards if cards is not None else load_cards()).get(card_id)


def flag_on(card_):
    """Whether a card's optional config flag is switched on (a dark or beta
    card's gate): ``config.<FLAG>`` when the worker config is importable,
    else the environment variable of the same name."""
    name = (card_ or {}).get("flag")
    if not name:
        return False
    try:
        import config
        if hasattr(config, name):
            return bool(getattr(config, name))
    except Exception:  # noqa: BLE001 — config unavailable: env decides
        pass
    return os.getenv(name, "0").strip().lower() in ("1", "true", "yes", "on")


def proposable(card_, asked=()):
    """(bool, reason): may a planner, look or format pack propose this card?

    Owner rules: zooms and sounds are optional, never defaults — a card that
    adds a zoom or a sound is proposed only when the user asked for that
    ("zoom" / "sfx" in ``asked``); a dark card is never proposed unless its
    flag is on. Flash cards are proposable; their instances pass through
    flash_safety."""
    asked = {str(a).lower() for a in (asked or ())}
    flags = (card_ or {}).get("owner_flags") or {}
    if card_.get("status") == "dark" and not flag_on(card_):
        return False, "dark"
    if flags.get("adds_zoom") and not asked & {"zoom", "camera"}:
        return False, "adds a zoom the user did not ask for"
    if flags.get("adds_sound") and not asked & {"sfx", "sound"}:
        return False, "adds a sound the user did not ask for"
    return True, "ok"


# ── lanes ───────────────────────────────────────────────────────────────

@functools.lru_cache(maxsize=1)
def _local_ffmpeg_filters():
    exe = shutil.which("ffmpeg")
    if not exe:
        return frozenset()
    try:
        out = subprocess.run([exe, "-hide_banner", "-filters"],
                             capture_output=True, text=True, timeout=20,
                             check=False).stdout
    except Exception:  # noqa: BLE001
        return frozenset()
    names = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and re.fullmatch(r"[A-Z.|]{2,4}", parts[0]):
            names.add(parts[1])
    return frozenset(names)


def _yunet_baked():
    """0A bakes YuNet into every image (INTERFACES.md §1, 0A): its model
    manifest in worker/models/ is the build's statement that it ships."""
    models = os.path.join(os.path.dirname(CARDS_DIR), os.pardir, "models")
    try:
        return any(n.startswith("yunet") and n.endswith(".json")
                   for n in os.listdir(os.path.abspath(models)))
    except OSError:
        return False


@functools.lru_cache(maxsize=1)
def _local_features():
    """This process's own features, measured once (faults apply on top)."""
    feats = set()
    try:
        import config
        if os.path.exists(config.MATTE_RVM_MODEL):
            feats.add("onnx.rvm")
        if os.path.exists(config.MATTE_SEG_MODEL):
            feats.add("onnx.u2net")
    except Exception:  # noqa: BLE001
        pass
    models = os.path.abspath(os.path.join(os.path.dirname(CARDS_DIR),
                                          os.pardir, "models"))
    try:
        if any(n.startswith("yunet") and n.endswith(".onnx")
               for n in os.listdir(models)):
            feats.add("onnx.yunet")
    except OSError:
        pass
    for mod, feat in (("faster_whisper", "whisper"), ("demucs", "demucs")):
        try:
            if importlib.util.find_spec(mod) is not None:
                feats.add(feat)
        except (ImportError, ValueError):
            pass
    try:
        import motion_engine
        if motion_engine.available():
            feats.add("chromium")
    except Exception:  # noqa: BLE001
        pass
    feats |= {"ffmpeg:" + f for f in _local_ffmpeg_filters()}
    return frozenset(feats)


def _apply_faults(feats):
    """The model_missing and ffmpeg_filter_missing faults take the same path
    as the real absence: the feature is simply not there. A bare fault name
    removes every feature of its kind; a ':<detail>' removes that one."""
    try:
        import faultinject
    except Exception:  # noqa: BLE001
        return feats
    if not faultinject.active():
        return feats
    out = set(feats)
    models = faultinject.details("model_missing")
    if models is None:
        out -= {f for f in out if f.startswith("onnx.")}
    else:
        for kind in models:
            out -= set(SIDECAR_FEATURE.get(kind, ()))
            out.discard(kind)
    filters = faultinject.details("ffmpeg_filter_missing")
    if filters is None:
        out -= {f for f in out if f.startswith("ffmpeg:")}
    else:
        out -= {"ffmpeg:" + f for f in filters}
    return out


def lane_features(lane=None):
    """The set of features a lane offers: "chromium", "onnx.rvm",
    "onnx.u2net", "onnx.yunet", "whisper", "demucs" and "ffmpeg:<filter>".

    A named production lane answers from the deployment facts (ROADMAP §2);
    ``None`` or "local" measures this process. Injected faults remove what
    they simulate missing."""
    if lane in (None, "local"):
        feats = set(_local_features())
    else:
        if lane not in _LANE_TABLE:
            raise ValueError(f"unknown lane {lane!r}; lanes: {LANES}")
        feats = set(_LANE_TABLE[lane])
        feats |= {"ffmpeg:" + f for f in FFMPEG_FILTERS}
        if _yunet_baked():
            feats.add("onnx.yunet")
    return _apply_faults(feats)


# ── cost ────────────────────────────────────────────────────────────────

COST_MODEL_PATH = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "cost_model.json")


@functools.lru_cache(maxsize=1)
def cost_model():
    """worker/cost_model.json: lane speeds, the baseline render model and
    the render budgets the resolver spends against."""
    with open(COST_MODEL_PATH, encoding="utf-8") as f:
        return json.load(f)


def _lane_cost_key(lane):
    """Which calibrated cost column a lane reads."""
    if lane in ("batch", "vision"):
        return "batch"
    if lane in (None, "local"):
        return "local"
    return "interactive"


def estimate_ms(card_, lane, frames, rung="full", params=None):
    """Predicted added render wall time (ms) of one device instance.

    A card's own ``cost`` is calibrated per lane class; a lane it does not
    list scales the interactive figure by the lane speeds in the cost
    model. ``reduced`` costs the model's reduced factor (half-resolution
    blurs, 15 fps masks); a substitute costs the substitute's own figure;
    ``omit`` costs nothing."""
    if rung == "omit":
        return 0.0
    model = cost_model()
    cost = (card_ or {}).get("cost") or {}
    per = cost.get("ms_per_frame") or {}
    key = _lane_cost_key(lane)
    if key in per:
        ms_frame = float(per[key])
    elif per:
        base_key = "interactive" if "interactive" in per else sorted(per)[0]
        speeds = model["lanes"]
        ms_frame = float(per[base_key]) * (
            speeds[key]["ms_scale"]
            / speeds[_lane_cost_key(base_key)]["ms_scale"])
    else:
        fam = (card_ or {}).get("family")
        ms_frame = float(model["family_ms_per_frame"].get(fam, 5.0)) * \
            model["lanes"][key]["ms_scale"]
    fixed = float(cost.get("fixed_ms", model["default_fixed_ms"]))
    total = fixed + ms_frame * max(0, int(frames or 0))
    if rung == "reduced":
        total *= float(model["reduced_factor"])
    elif rung == "substitute":
        total = float(model["substitute_fixed_ms"]) + \
            float(model["substitute_ms_per_frame"]) * max(0, int(frames or 0))
    return round(total, 1)


def baseline_ms(program_s, width, height, *, preview=True, lane="local",
                motion_items=0, captions=False, inserts=0, crop=False):
    """Predicted render cost (CPU-ms) of a render with no devices — the
    denominator of the preview budget (≤ +30%) and of the final budget.
    ``width``/``height`` are the OUTPUT size the render encodes; ``crop``
    is a reframed (non-16:9) program. CPU time, not wall time: it is what
    transfers between machines (a lane's wall time is CPU / its
    ``parallel``). Coefficients are fitted on measured local renders by
    tests/w0c_costbench.py (AT-0C-2); production lanes scale them by
    ``ms_scale`` (cost_model.json)."""
    m = cost_model()
    b = m["baseline"]["preview" if preview else "final"]
    prog = max(0.0, float(program_s))
    ms = (b["fixed_ms"] + b["ms_per_s"] * prog
          + b["ms_per_mpix_s"] * (width * height / 1e6) * prog
          + (b["crop_ms_per_s"] * prog if crop else 0.0)
          + (b["captions_ms_per_s"] * prog if captions else 0.0)
          + (b["motion_launch_ms"] if motion_items else 0.0)
          + b["ms_per_motion_item"] * motion_items
          + b["ms_per_insert"] * inserts)
    return round(ms * m["lanes"][_lane_cost_key(lane)]["ms_scale"], 1)


def wall_ms(cpu_ms, lane="local"):
    """A lane's wall time for ``cpu_ms`` of render work."""
    lane_row = cost_model()["lanes"][_lane_cost_key(lane)]
    return round(float(cpu_ms) / float(lane_row.get("parallel", 1.0)), 1)


def render_budget_ms(baseline, *, preview=True, program_s=60.0,
                     lane="local"):
    """How much added device cost (CPU-ms) a render may spend: previews
    ≤ +30% of the baseline; finals up to the 4-minute wall p95 for 60 s
    (scaled to the program length and converted to the lane's CPU-ms)."""
    b = cost_model()["budgets"]
    if preview:
        return round(baseline * float(b["preview_overhead"]), 1)
    lane_row = cost_model()["lanes"][_lane_cost_key(lane)]
    cap_wall = float(b["final_p95_s"]) * 1000.0 * max(
        0.25, float(program_s) / float(b["final_ref_program_s"]))
    cap = cap_wall * float(lane_row.get("parallel", 1.0))
    return round(max(0.0, cap - baseline), 1)


# ── resolver ────────────────────────────────────────────────────────────

def _rung_name(rung):
    if isinstance(rung, str):
        return rung
    for key in RUNGS:
        if key in rung:
            return key
    return "omit"


def _sidecar_state(sidecar_status, kind):
    if callable(sidecar_status):
        try:
            return str(sidecar_status(kind) or "absent")
        except Exception:  # noqa: BLE001
            return "failed"
    return str((sidecar_status or {}).get(kind) or "absent")


def _missing(card_, lane, sidecar_status, feats):
    """[reason, ...] why the FULL rung cannot run here."""
    needs = card_.get("needs") or {}
    out = []
    for feat in needs.get("lane_features") or ():
        if feat not in feats:
            out.append(f"lane lacks {feat}")
    for kind in needs.get("sidecars") or ():
        state = _sidecar_state(sidecar_status, kind)
        if state != "ready":
            out.append(f"{kind} {state}")
    return out


def _omit(reason):
    return {"rung": "omit", "reason": str(reason)[:200], "substitute": None,
            "params": {}, "est_ms": 0.0}


def resolve_detail(card_id, *, lane, sidecar_status, budget_ms=None,
                   frames=0, cards=None, _depth=0):
    """The full resolver answer: {rung, reason, substitute, params, est_ms}.

    Rungs are tried best-first. ``full`` needs every listed lane feature
    and every sidecar ready and must fit ``budget_ms``; ``reduced`` needs
    the same inputs at the reduced cost; a ``substitute`` rung resolves the
    named card (or a built-in substitute, which needs nothing) at its own
    cost; ``omit`` always resolves. Nothing raises: an unknown card, a dark
    card, a broken card, an unreadable cost model or a budget of zero all
    come back as ``omit`` with the reason (a creative device may degrade;
    it never fails a render)."""
    try:
        return _resolve_detail(card_id, lane=lane,
                               sidecar_status=sidecar_status,
                               budget_ms=budget_ms, frames=frames,
                               cards=cards, _depth=_depth)
    except Exception as e:  # noqa: BLE001 — never fail the render
        return _omit(f"resolver error: {type(e).__name__}: {e}")


def _resolve_detail(card_id, *, lane, sidecar_status, budget_ms=None,
                    frames=0, cards=None, _depth=0):
    try:
        cards = cards if cards is not None else load_cards()
    except Exception as e:  # noqa: BLE001 — a bad card set never fails a render
        return {"rung": "omit", "reason": f"cards unavailable: {e}"[:200],
                "substitute": None, "params": {}, "est_ms": 0.0}
    card_ = cards.get(card_id)
    if card_ is None:
        return {"rung": "omit", "reason": "unknown device",
                "substitute": None, "params": {}, "est_ms": 0.0}
    if card_.get("status") == "dark" and not flag_on(card_):
        return {"rung": "omit", "reason": "dark", "substitute": None,
                "params": {}, "est_ms": 0.0}
    try:
        feats = lane_features(lane)
    except ValueError as e:
        return {"rung": "omit", "reason": str(e)[:200], "substitute": None,
                "params": {}, "est_ms": 0.0}
    missing = _missing(card_, lane, sidecar_status, feats)
    budget = math.inf if budget_ms is None else float(budget_ms)
    why = []
    for rung in card_.get("rungs") or ("omit",):
        name = _rung_name(rung)
        params = dict(rung) if isinstance(rung, dict) else {}
        if name in ("full", "reduced"):
            params.pop(name, None)
            if missing:
                why.append(", ".join(missing))
                continue
            est = estimate_ms(card_, lane, frames, name)
            if est > budget:
                why.append(f"{name} {est:.0f} ms over the {budget:.0f} ms "
                           "budget")
                continue
            return {"rung": name,
                    "reason": "ok" if name == "full" else "; ".join(why),
                    "substitute": None, "params": params, "est_ms": est}
        if name == "substitute":
            sub = params.pop("substitute", None)
            if sub in BUILTIN_SUBSTITUTES:
                est = estimate_ms(card_, lane, frames, "substitute")
                if est <= budget:
                    return {"rung": "substitute",
                            "reason": "; ".join(why) + f" -> {sub}",
                            "substitute": sub, "params": params,
                            "est_ms": est}
                why.append(f"{sub} over budget")
                continue
            if sub in cards and _depth < 2:
                inner = _resolve_detail(sub, lane=lane,
                                       sidecar_status=sidecar_status,
                                       budget_ms=budget_ms, frames=frames,
                                       cards=cards, _depth=_depth + 1)
                if inner["rung"] in ("full", "reduced"):
                    return {"rung": "substitute",
                            "reason": "; ".join(why) + f" -> {sub}",
                            "substitute": sub,
                            "params": {**inner["params"], **params},
                            "est_ms": inner["est_ms"]}
                why.append(f"{sub}: {inner['reason']}")
                continue
            why.append(f"unknown substitute {sub}")
            continue
        if name == "omit":
            break
    return {"rung": "omit", "reason": "; ".join(w for w in why if w)
            or "omitted", "substitute": None, "params": {}, "est_ms": 0.0}


def resolve(card_id, *, lane, sidecar_status, budget_ms=None, frames=0):
    """(rung, reason) for one device on this render (INTERFACES.md §6.3).
    ``frames`` (keyword, optional) is the instance's length for the cost
    check; see resolve_detail for the full answer."""
    d = resolve_detail(card_id, lane=lane, sidecar_status=sidecar_status,
                       budget_ms=budget_ms, frames=frames)
    return d["rung"], d["reason"]


def report_row(item_id, card_id, stage, detail, family=None):
    """One row of the render report's ``devices`` block (§6.3)."""
    if not isinstance(card_id, str):
        card_id = None
    if family is None and card_id:
        try:
            family = (card(card_id) or {}).get("family")
        except Exception:  # noqa: BLE001 — a report row never raises
            family = None
    reason = detail.get("reason") or ""
    if detail.get("substitute") and detail["substitute"] not in reason:
        reason = (reason + f" -> {detail['substitute']}").strip()
    return {"id": str(item_id), "device": card_id, "family": family,
            "stage": stage, "rung": detail.get("rung", "omit"),
            "reason": reason, "est_ms": float(detail.get("est_ms") or 0.0)}


def plan_rows(items, *, lane, sidecar_status, budget_ms=None, fps=30.0,
              cards=None):
    """Report rows for a list of fx-like items ({id, device, stage, start,
    end}), spending ``budget_ms`` across them in program order: each device
    resolves against what the earlier ones left. fxgraph.prepare may call
    this; the tests and the fault suite do."""
    try:
        cards = cards if cards is not None else load_cards()
    except Exception as e:  # noqa: BLE001 — a bad card set omits, never fails
        cards, card_error = {}, f"cards unavailable: {e}"
    else:
        card_error = None
    try:
        left = None if budget_ms is None else float(budget_ms)
    except (TypeError, ValueError):
        left = None
    rows = []
    for it in items or ():
        it = it if isinstance(it, dict) else {}
        dev = it.get("device")
        try:
            start, end = it.get("start"), it.get("end")
            frames = (max(1, round(float(end) * fps)
                          - round(float(start) * fps))
                      if start is not None and end is not None else 1)
        except (TypeError, ValueError):
            frames = 1
        d = (_omit(card_error) if card_error else
             resolve_detail(dev, lane=lane, sidecar_status=sidecar_status,
                            budget_ms=left, frames=frames, cards=cards))
        if left is not None:
            left = max(0.0, left - float(d.get("est_ms") or 0.0))
        fam = (cards.get(dev) or {}).get("family") \
            if dev and isinstance(dev, str) else None
        rows.append(report_row(it.get("id"), dev, it.get("stage"), d,
                               family=fam))
    return rows


def degraded(rows):
    """The rows a final may not ship without acknowledgement: any rung
    other than ``full`` (the DEGRADED gate, quality_verifier)."""
    return [r for r in rows or () if isinstance(r, dict)
            and r.get("rung") != "full"]


def valid_id(card_id):
    return bool(_ID_RE.match(str(card_id or "")))
