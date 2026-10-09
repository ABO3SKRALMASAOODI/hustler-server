"""Editor tools for browser-rendered motion design and the built-in SFX kit.

Registered in agent_tools.TOOLS (and therefore exposed over MCP). Kept in
their own module so agent_tools does not grow further; agent_tools is
imported lazily to avoid an import cycle.

Design principles for these tools:
- One call places a finished, premium composition (a library template with
  meaningful defaults) and, by default, its synced sound cues from the kit.
- Every write proves the composition renders (a quarter-scale probe of a few
  frames) so script errors or empty output are rejected at write time, not
  discovered in a failed render.
- Owned sound cues follow their graphic: moving or removing the graphic
  moves or removes its cues.
"""

import json
import os
import re
import tempfile

import caption_carry
import config  # noqa: F401  (kept for parity with other tool modules)
import db as dbx
import keepout
import motion_engine
import motion_layer
import motion_templates
import sound_library
import storage
from schemas import subject_matte_geom

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
LAYERS = ("above_captions", "below_captions", "behind_subject")
BEHIND_MIN_S = 0.4
# The shared subject measurement's refusals, worded for a graphic
# (agent_tools._measure_subject_matte; its defaults speak about words).
_BEHIND_WORDS = {
    "clip_alt": "place the graphic on layer='above_captions' over the clip",
    "subject": "the graphic",
    "span": "its start/end window",
    "claim": "the motion graphic was added",
    "refuse_alt": ("The same graphic on layer='above_captions' (or "
                   "'below_captions') always works — offer that and say "
                   "plainly why the behind version will not."),
}


def _at():
    import agent_tools
    return agent_tools


def _program_duration(edl):
    from schemas import program_duration
    return float(program_duration(edl))


def _canvas_size(ctx, edl):
    import renderer
    if edl.get("canvas"):
        return int(edl["canvas"]["width"]), int(edl["canvas"]["height"])
    video = (getattr(ctx, "index", None) or {}).get("video") or {}
    return renderer.frame_dims(int(video.get("width") or 1920),
                               int(video.get("height") or 1080),
                               (edl.get("frame") or {}).get("ratio", "source"),
                               delivery=True)


# ── Sound library (real recordings approved by the owner) ─────────────────

SOUND_PREFIX = sound_library.REF_PREFIX


def sound_search(query, limit=6):
    """Approved library sounds matching the query words (deterministic)."""
    return [{"id": SOUND_PREFIX + r["id"], "role": r["role"], "duration_s": r["duration_s"],
             "use": r["use"], "gain_db": r["gain_db"]} for r in sound_library.search(query, limit)]


def ensure_library_asset(ctx, sound_id):
    """Project storage key for an approved library sound, uploading once."""
    row = sound_library.get(sound_id)
    if not row:
        raise ValueError(f"unknown sound '{sound_id}'. Library: "
                         f"{', '.join(r['id'] for r in sound_library.catalog())}")
    local = sound_library.path(sound_id)
    key = sound_library.asset_key(ctx.project_id, sound_id)
    existing = ctx.db.run(dbx.asset_by_key, ctx.project_id, key)
    if existing:
        return key
    try:
        present = storage.exists(key)
    except Exception:
        present = False
    if not present:
        storage.upload_file(local, key, "audio/flac")
    ctx.db.run(dbx.insert_asset, ctx.project_id, "music", key,
               bytes_=os.path.getsize(local), duration_s=row["duration_s"],
               meta={"filename": row["file"], "source": "valmera-sound-library",
                     "source_url": row["source_url"], "author": row["author"],
                     "license": row["license"],
                     "license_note": "CC0 1.0 real recording from Freesound — no attribution required",
                     "caption": row["use"], "library_sound": sound_id, "role": row["role"]})
    return key


def resolve_library_reference(ctx, storage_key):
    """(sound_dict, error) for a 'sound:<id>' reference; (None, None) otherwise."""
    if not isinstance(storage_key, str) or not storage_key.startswith(SOUND_PREFIX):
        return None, None
    sound_id = storage_key[len(SOUND_PREFIX):].strip()
    try:
        key = ensure_library_asset(ctx, sound_id)
    except ValueError as e:
        return None, f"REJECTED: {e}"
    except Exception as e:  # noqa: BLE001
        return None, (f"Could not prepare the library sound ({str(e)[:160]}). Try again; "
                      "do NOT claim a sound was added.")
    row = sound_library.get(sound_id)
    return {"name": f"{sound_id} (Valmera sound library)", "duration_s": row["duration_s"],
            "library": True, "storage_key": key, "gain_db": row["gain_db"],
            "sound_id": sound_id}, None


SOUND_POLICY = (
    "Use sound like a professional editor, never as decoration: only where something "
    "meaningful happens ON SCREEN — a designed graphic landing, a real section change or "
    "B-roll entry, the payoff, or a real-world action shown (shutter on a photo, typing under "
    "typed text, a click on a button press, a cash register on a money figure). Never on "
    "captions or on ordinary cuts inside a conversation. Sparse: at most about one sound every "
    "4-5 s (≈4-8 in a 30-45 s short), never the same sound twice within ~3 s, zero is fine "
    "when nothing earns one. Match the material, keep one family per short, place the peak on "
    "the visual frame, and mix under the voice at the suggested gain.")


def list_sound_library(ctx, role=None):
    rows = sound_library.catalog(role or None)
    if not rows:
        return "No approved sounds match." if role else "The sound library is empty on this deployment."
    return ("Valmera sound library — real recordings approved by ear (CC0, no attribution). "
            "Place with add_sfx(storage_key='sound:<id>', at=<program second it should HIT>, "
            "gain_db=<suggested>): the tool starts each recording early by its measured peak "
            "(typing starts at `at`) and stops long tails at their measured end.\n"
            + SOUND_POLICY + "\n" + "\n".join("- " + sound_library.describe(r) for r in rows))


# ── motion graphics ───────────────────────────────────────────────────────

def list_motion_templates(ctx, category=None):
    cat = motion_templates.catalog(category or None)
    if not cat:
        return ("No motion templates match." if category else
                "No motion templates are installed on this deployment.")
    lines = []
    for t in cat:
        ps = []
        for k, p in t["params"].items():
            bit = k
            if p.get("required"):
                bit += "*"
            if p["type"] == "enum":
                bit += "=" + "|".join(map(str, p.get("values") or []))
            elif "default" in p and p["type"] not in ("text", "str"):
                bit += f"={p['default']}"
            ps.append(bit)
        lines.append(f"- {t['name']} [{t['category']}, ~{t['duration']}s]: {t['description']} "
                     f"Params: {', '.join(ps)}" + (f". Sound: {', '.join(t['sfx'])}" if t["sfx"] else ""))
    return ("Motion templates (add_motion_graphic(template, start, end, params)). * = required.\n"
            + "\n".join(lines) +
            "\nOr template='html' with your own composition on the MG runtime (read_skill "
            "'motion-design' for the API).")


_JS_FLOAT = re.compile(r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)")


def _js_float(value):
    """Number the way the templates read it (JavaScript parseFloat after the first
    ',' becomes '.'): '1.2s' -> 1.2, '2,25' -> 2.25, '' / 'soon' / None -> None."""
    if value is None or isinstance(value, bool):
        return None
    m = _JS_FLOAT.match(str(value).replace(",", ".", 1))
    if not m:
        return None
    v = float(m.group(1))
    return v if v == v and abs(v) != float("inf") else None


def _cue_applies(cue, params):
    """A cue's optional "when" ({param: value or [values]}) gates it on the
    item's params, e.g. a kick only for the 'slam' entrance."""
    cond = cue.get("when")
    if not isinstance(cond, dict):
        return True
    for key, want in cond.items():
        if params.get(key) not in (want if isinstance(want, list) else [want]):
            return False
    return True


def _text_len(params, name):
    v = params.get(name)
    return len(v.strip()) if isinstance(v, str) else 0


def _cue_dur(cue, params):
    """Seconds the sound may play (None = its full length). "dur" is a number,
    or {"param", "rate", "min", "max"}: the typing time of a text param at
    `rate` characters per second, clamped."""
    d = cue.get("dur")
    if d is None:
        return None
    if isinstance(d, dict):
        n = _text_len(params, d.get("param"))
        rate = d.get("rate")
        if isinstance(rate, str):
            rate = params.get(rate)
        try:
            v = n / float(rate) if n and float(rate or 0) > 0 else float(d.get("min", 0.3))
            v = min(max(v, float(d.get("min", 0.2))), float(d.get("max", 3.0)))
        except (TypeError, ValueError):
            return None
        return round(v, 3)
    try:
        return round(float(d), 3) if float(d) > 0 else None
    except (TypeError, ValueError):
        return None


def _land_at(land, span, default):
    """A cue that follows the template's own duration-relative landing
    (``land``: clamp(span * frac + add, min, max)), e.g. counter's count
    landing min(1.0, max(0.5, 0.55 * duration))."""
    try:
        v = span * float(land.get("frac", 0.0)) + float(land.get("add", 0.0))
        if land.get("min") is not None:
            v = max(v, float(land["min"]))
        if land.get("max") is not None:
            v = min(v, float(land["max"]))
    except (TypeError, ValueError, AttributeError):
        return default
    return v


def _sfx_cues(spec, params, start, end, seed="", with_dur=False):
    """[(time, sound_id, gain_db)] for a template's declared sound roles,
    mapped onto approved library recordings (roles without one are skipped).
    A cue's time is the visual landing the sound HITS on (_apply_owned_sfx
    starts the recording early by its peak); ``land`` makes it follow a
    duration-relative landing instead of the fixed ``at``.
    A cue with ``when`` only sounds for matching params. A cue with
    ``repeat`` sounds once per entry of a list param: ``from`` +
    i*``every``; optional ``fit``/``min_every`` tighten ``every`` to
    (duration - fit)/(n - 1), ``require`` skips rows whose named field is
    blank, and ``field``/``offset`` use a row's own time. ``dur`` caps how
    long the sound plays (returned as a 4th element when with_dur)."""
    cues = []
    for n, c in enumerate(spec.get("sfx") or []):
        if not _cue_applies(c, params):
            continue
        row = sound_library.pick(c.get("kind") or "", seed=f"{seed}:{n}")
        if not row:
            continue
        gain = float(c.get("gain_db", row["gain_db"]))
        dur = _cue_dur(c, params)
        at = float(c.get("at") or 0.0)
        if isinstance(c.get("land"), dict):
            at = _land_at(c["land"], end - start, at)
        t = (end + at) if at < 0 else (start + at)
        rep = c.get("repeat")
        if rep and isinstance(params.get(rep.get("param")), list):
            entries = params[rep["param"]]
            if rep.get("require"):
                # rows the template drops (blank text) get no sound and no slot
                entries = [e for e in entries
                           if not isinstance(e, dict) or str(e.get(rep["require"]) or "").strip()]
            every = float(rep.get("every", 0.3))
            if rep.get("fit") is not None and len(entries) > 1:
                # the template tightens its cadence so every row lands before the exit
                every = min(every, max(float(rep.get("min_every", 0.22)),
                                       ((end - start) - float(rep["fit"])) / (len(entries) - 1)))
            for i, entry in enumerate(entries):
                off = float(rep.get("from", at)) + i * every
                # rows may carry their own landing time (e.g. checklist 'at')
                if rep.get("field") and isinstance(entry, dict):
                    v = _js_float(entry.get(rep["field"]))
                    if v is not None:
                        v = min(max(v, 0.0), max(0.0, (end - start) - 0.5))   # same clamp as the template
                        off = v + float(rep.get("offset", 0.0))
                cues.append((round(start + off, 3), row["id"], gain, dur))
            continue
        cues.append((round(t, 3), row["id"], gain, dur))
    cues = [c for c in cues if start - 0.01 <= c[0] <= end]
    return cues if with_dur else [c[:3] for c in cues]


def _owned_sfx_prefix(mid):
    return f"mg_{mid}_sfx"


def _apply_owned_sfx(ctx, edl, mid, cues):
    items = [s for s in (edl.get("sfx") or []) if not str(s.get("id", "")).startswith(_owned_sfx_prefix(mid))]
    prog = _program_duration(edl)
    notes = []
    for k, cue in enumerate(cues):
        t, kind, gain = cue[:3]
        dur = cue[3] if len(cue) > 3 else None
        if t > prog - 0.05:
            continue
        try:
            key = ensure_library_asset(ctx, kind)
        except Exception as e:  # noqa: BLE001
            notes.append(f"sound {kind} unavailable ({str(e)[:80]})")
            continue
        # t is the landing the sound HITS on: start early by its peak.
        pl = sound_library.place(kind, max(0.0, t), dur_s=dur)
        items.append({"id": f"{_owned_sfx_prefix(mid)}{k + 1}", "storage_key": key,
                      "at": pl["at"], "gain_db": gain,
                      "purpose": f"{kind} for motion graphic {mid}"})
        for f in ("offset_s", "dur_s"):
            if pl[f]:
                items[-1][f] = pl[f]
    edl["sfx"] = items
    return notes


def _probe_times(item):
    """Item-local seconds the write-time probe samples (the renderer's
    footprint fill samples the same ones)."""
    return motion_layer.probe_times(float(item["end"]) - float(item["start"]))


def _probe_item(item, W, H, fps=30.0):
    """Errors/visibility for an item at quarter scale, or None if the browser
    is unavailable here (the render path will still try)."""
    try:
        job = motion_templates.build_job(item, W, H, fps)
    except Exception as e:  # noqa: BLE001
        return {"errors": [str(e)[:200]], "visible_frames": 0, "samples": 0, "bboxes": []}
    try:
        return motion_engine.probe([job], [_probe_times(item)])[0]
    except motion_engine.MotionRenderError:
        return None


_PROBE_ONE = _probe_item


def _probe_items(items, W, H, fps=30.0):
    """_probe_item for several items in ONE browser session (the keep-out's
    alternatives). A replaced _probe_item (tests) is honoured item by item."""
    if _probe_item is not _PROBE_ONE:
        return [_probe_item(it, W, H, fps) for it in items]
    jobs, times, out = [], [], [None] * len(items)
    for k, it in enumerate(items):
        try:
            jobs.append((k, motion_templates.build_job(it, W, H, fps)))
            times.append(_probe_times(it))
        except Exception as e:  # noqa: BLE001
            out[k] = {"errors": [str(e)[:200]], "visible_frames": 0, "samples": 0, "bboxes": []}
    if jobs:
        try:
            reps = motion_engine.probe([j for _k, j in jobs], times)
        except motion_engine.MotionRenderError:
            reps = [None] * len(jobs)
        for (k, _j), rep in zip(jobs, reps):
            out[k] = rep
    return out


def _where(box):
    return f"\nDraws within x {box[0]:.2f}-{box[2]:.2f}, y {box[1]:.2f}-{box[3]:.2f} of the frame."


def _probe_full(ctx, edl, item):
    """(error, where-note, drawn bbox (x0, y0, x1, y1) fractions or None,
    the probe report or None)."""
    W, H = _canvas_size(ctx, edl)
    rep = _probe_item(item, W, H)
    if rep is None:
        return None, "\nNOTE: the composition could not be pre-checked here; render a preview to inspect it.", None, None
    if rep["errors"]:
        return (f"REJECTED: the composition raised a script error: {rep['errors'][0]}. "
                "Fix the HTML/params and try again."), "", None, rep
    if rep["visible_frames"] == 0:
        return ("REJECTED: the composition drew nothing visible at any sampled moment "
                "(check text/params, colors with zero alpha, or elements positioned off-frame)."), "", None, rep
    bb = rep["bboxes"]
    if bb:
        box = (min(b[0] for b in bb), min(b[1] for b in bb),
               max(b[2] for b in bb), max(b[3] for b in bb))
        return None, _where(box), box, rep
    return None, "", None, rep


def _probe_report(ctx, edl, item):
    """(error, where-note, drawn bbox (x0, y0, x1, y1) fractions or None)."""
    err, where, bbox, _rep = _probe_full(ctx, edl, item)
    return err, where, bbox


def _validate_and_probe(ctx, edl, item):
    err, where, _bbox = _probe_report(ctx, edl, item)
    return err, where


# ── behind the subject ────────────────────────────────────────────────────

def _matte_box(item, bbox):
    """(x, y, w, h) fractions the subject-coverage numbers are measured on:
    what the probe saw the composition draw, else the item's capture hint."""
    r = bbox or item.get("box")
    if not r:
        return None
    x0, y0, x1, y1 = [min(max(float(v), 0.0), 1.0) for v in r]
    if x1 - x0 < 0.01 or y1 - y0 < 0.01:
        return None
    return (x0, y0, x1 - x0, y1 - y0)


def _overlaps(items, s, e):
    return [it for it in items or []
            if float(it.get("start") or 0) < e and float(it.get("end") or 0) > s]


def _behind_report(stats, edl, item):
    """What the measurement found, in the add_text_behind voice."""
    bits = []
    if stats.get("cached"):
        bits.append("The subject mask for that exact moment was already "
                    "measured, so this cost nothing to add.")
    else:
        cov = float(stats.get("coverage") or 0.0)
        how = ("matted frame by frame by the person-matting model"
               if stats.get("engine") == "rvm" else
               "found frame by frame by the person-segmentation model"
               if stats.get("method") == "person" else
               "from a background photographed out of the shot itself")
        bits.append(
            f"MEASURED on the footage: the subject covers {cov * 100:.1f}% of "
            f"the frame on average across the window (peaking at "
            f"{float(stats.get('coverage_max') or 0) * 100:.1f}%), {how}. The "
            "graphic is drawn INTO the shot and the subject is laid back over "
            "it, so they pass in FRONT of it.")
        if stats.get("fell_back"):
            bits.append(
                "NOTE: the person model was unreachable, so this mask came "
                "from the photometric fallback — it needs a still camera and "
                "can miss a dark subject on a dark background. If the render "
                "shows the graphic over the subject, set_motion_graphic it "
                "again once the model is back.")
    tc, tw = stats.get("text_covered"), stats.get("text_width_covered")
    if tc is not None and tc < 0.02:
        bits.append(
            f"WARNING: the subject crosses only {tc * 100:.1f}% of where the "
            "graphic draws, so on screen it will read as an ordinary overlay. "
            "Tell the user, and move it (params/box) to where the subject "
            "actually is, or shift the window to when they cross it.")
    elif tw is not None and tw >= 0.45:
        bits.append(
            f"LEGIBILITY: at its peak the subject covers {tw * 100:.0f}% of "
            "the graphic's WIDTH. Right for a giant hero word the speaker "
            "stands in front of (the eye completes tall letters); wrong for "
            "copy that must be read in full. The craft fix is BIGGER type, "
            "never smaller.")
    elif tw is not None:
        bits.append(
            f"The subject crosses {tw * 100:.0f}% of the graphic's width at "
            f"most ({(tc or 0) * 100:.0f}% of its area), so the depth reads "
            "and the graphic stays legible.")
    s, e = float(item["start"]), float(item["end"])
    fx = edl.get("effects") or {}
    cards = _overlaps(fx.get("picture_cards"), s, e)
    if any(c.get("source") or c.get("panels") for c in cards):
        bits.append(
            "A footage card framed from the SOURCE is active over this "
            "window: it re-composes the footage, so the subject mask no "
            "longer lines up and the graphic renders IN FRONT of the picture "
            "there. Move it outside the card's window to keep the depth.")
    elif cards:
        bits.append(
            "A footage card is active over this window: the graphic is part "
            "of the PICTURE now, so it is framed inside the card with the "
            "footage (anything drawn outside frame.picture is cropped away). "
            "Design it for the picture area.")
    if _overlaps(fx.get("zooms"), s, e):
        bits.append("A zoom overlaps this window: the graphic lives in the "
                    "scene, so it scales with the picture.")
    bits.append(
        "It is bound to that FOOTAGE and that FRAMING: a later cut moves it "
        "with the shot and removes it if the footage is cut away. If the "
        "mask stops matching the picture — a cut lands inside the window, a "
        "speed ramp is put over its footage, set_frame changes the crop — it "
        "renders as an ordinary above-captions graphic (the edit that causes "
        "it says so; set_motion_graphic re-measures). A mask that fails to "
        "load at render time degrades the same way but only the render log "
        "records it, so never claim the depth from the EDL alone. NEXT: "
        "render_preview and check that the subject's edge reads cleanly in "
        "front of it.")
    return "\n" + "\n".join(bits)


def _attach_subject_matte(ctx, edl, item, bbox):
    """Measure (or reuse) the subject mask for a behind_subject item and
    store it on item['behind']. Returns (report, error_reply)."""
    if not getattr(ctx, "has_main_video", True):
        return None, ("REJECTED: layer='behind_subject' puts the graphic "
                      "behind the SUBJECT of a shot, and there is no main "
                      "video to find a subject in. Use layer='above_captions'.")
    s, e = float(item["start"]), float(item["end"])
    if e - s < BEHIND_MIN_S:
        return None, (f"REJECTED: the window is under {BEHIND_MIN_S}s — too "
                      "short for anyone to pass in front of a graphic.")
    behind, stats, err = _at()._measure_subject_matte(
        ctx, edl, s, e, _matte_box(item, bbox), words=_BEHIND_WORDS)
    if err:
        return None, err
    item["behind"] = behind
    return _behind_report(stats, edl, item), None


# ── face and safe-area keep-out (worker/keepout.py) ───────────────────────
# Measured exact frames per tool context: a write re-checked by a later
# set_motion_graphic (or a neighbour on the same footage) reuses them.
KEEPOUT_FRAME_CAP = 60


def _face_measure(ctx):
    """src_t -> face boxes on that exact source frame (keepout.detect_faces),
    or None when this context cannot decode the footage (the index's
    spatial samples answer instead)."""
    if not getattr(ctx, "has_main_video", True):
        return None
    try:
        proxy = ctx.proxy_path()
        workdir = ctx.workdir
    except Exception:  # noqa: BLE001 — no proxy here: index evidence only
        return None
    if not proxy or not os.path.exists(proxy) or not workdir:
        return None
    cache = getattr(ctx, "_keepout_faces", None)
    if cache is None:
        cache = {}
        try:
            setattr(ctx, "_keepout_faces", cache)
        except Exception:  # noqa: BLE001
            pass

    def measure(src_t):
        import media
        key = round(float(src_t), 2)
        if key in cache:
            return cache[key]
        if len(cache) >= KEEPOUT_FRAME_CAP:
            return None
        fp = os.path.join(workdir, f"keepout_{int(round(key * 100))}.jpg")
        try:
            media.frame_at(proxy, key, fp, width=480)
            faces = keepout.detect_faces(fp)
        except Exception:  # noqa: BLE001 — that moment falls back to the index
            faces = None
        cache[key] = faces
        return faces
    return measure


def _mutes_captions(item):
    """Does the item hide every caption in its window (mute_captions=true)?
    Unset is word-level: the captions it does not show stay on screen."""
    return caption_carry.mode(item) == caption_carry.MODE_ALL


def _caption_anchors(ctx, edl, item):
    """Usual anchor ys of the transcript captions on screen during the item
    ([] when there are none, or the item hides them all: mute_captions=true).
    The solver prices a spot on their band; word-level captions keep
    running beside the graphic."""
    caps = edl.get("captions")
    index = getattr(ctx, "index", None) or {}
    if not (isinstance(caps, dict) and caps.get("mode") == "from_transcript") \
            or _mutes_captions(item) or not index.get("words") or not edl.get("keep"):
        return []
    try:
        import captions as caplib
        from timeline import Timeline
        tl = Timeline(edl["keep"], edl.get("inserts") or [], edl.get("speed"))
        others = dict(edl, motion=[m for m in edl.get("motion") or []
                                   if m.get("id") != item.get("id")])
        words = caplib.transcript_words(others, index, tl,
                                        caplib.effective_caption_mutes(others))
        s, e = float(item["start"]), float(item["end"])
        ys = set()
        for w in words:
            if float(w["t1"]) <= s or float(w["t0"]) >= e:
                continue
            src = (float(w.get("src_t0", 0)) + float(w.get("src_t1", 0))) / 2.0
            y, _band = caption_carry.normal_place(edl, src)
            ys.add(round(float(y), 3))
        return sorted(ys)
    except Exception as ex:  # noqa: BLE001 — a lint never blocks the edit
        print(f"[motion] keep-out caption read skipped: {str(ex)[:160]}", flush=True)
        return []


def _fmt(v):
    return f"{v:g}" if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v)


def _implicit(key, sp):
    """What an absent knob means: its default, else the centre for x and
    the maximum for a size/width (word_slam's unset width is its 0.85)."""
    if "default" in sp:
        return sp["default"]
    return 0.5 if key == "x" else sp.get("max") if key in ("width", "size", "scale") else None


def _relocate(item, spec, box, zones, track, W, H, bands, off_face=True):
    """Search and verify a clear placement: (patch, real ink box, params,
    the verifying probe report) or None. Alternatives the solver cannot
    predict (another align/side) are probed first, then the cheapest
    distinct candidates are rendered in one browser session and the first
    whose REAL ink is off the faces and inside the safe area wins. A second
    round searches y over what the first one measured, plus a measured
    ladder of size steps. ``bands`` = (caption blocks, caption obstacles):
    ink on a block pays CAPTION_PENALTY; only touching an obstacle
    (keepout.caption_obstacle: where the caption plan sees the stored box
    collide) pays CAPTION_NEAR_PENALTY."""
    times = _probe_times(item)
    pspec = spec.get("params") or {}
    alts = []
    for key in keepout.HORIZONTAL_KEYS:
        p = pspec.get(key) or {}
        if p.get("type") != "enum" or item.get("box"):
            continue
        cur = (item.get("params") or {}).get(key, p.get("default"))
        alts += [{key: v} for v in p.get("values") or []
                 if v != cur and v in ("left", "center", "right")]
    variants = []
    if alts:
        reps = _probe_items([dict(item, params=dict(item["params"], **a)) for a in alts], W, H)
        for a, rep in zip(alts, reps):
            if rep and not rep.get("errors") and rep.get("visible_frames"):
                variants.append((a, keepout.settled_ink(rep, times)))
    mouths = keepout.zones_of(track, keepout.mouth_zone)
    known = []                       # measured (patch without y, box at the current y)
    ycur = keepout.num(item.get("params") or {}, "y", (pspec.get("y") or {}).get("default", 0.5))
    for rnd in range(2):
        cands = keepout.candidates(
            item["template"], spec, item.get("params") or {}, box if rnd == 0 else None,
            variants if rnd == 0 else known, zones, W, H,
            captions=bands[0], near_captions=bands[1], predict=rnd == 0,
            mouths=mouths, clear_penalty=keepout.CLEAR_PENALTY if off_face else 0.0)
        picks = keepout.distinct(cands, 6)
        if not picks:
            return None
        trials = []
        for _cost, patch, _pred in picks:
            try:
                params = motion_templates.check_params(item["template"],
                                                       dict(item["params"], **patch),
                                                       html=item.get("html"))
            except ValueError:
                params = None
            trials.append(dict(item, params=params) if params is not None else None)
        reps = iter(_probe_items([t for t in trials if t is not None], W, H))
        for (_cost, patch, _pred), trial in zip(picks, trials):
            rep = next(reps) if trial is not None else None
            if not rep or rep.get("errors") or not rep.get("visible_frames"):
                continue
            real = keepout.settled_ink(rep, times)
            if not real:
                continue
            if not keepout.safe_issues(real, W, H) and not keepout.assess(real, track)["hit"]:
                return patch, real, trial["params"], rep
            # the real box of that knob setting, for a y-only second round
            dy = float(patch.get("y", ycur)) - ycur if "y" in patch else 0.0
            known.append(({k: v for k, v in patch.items() if k != "y"},
                          keepout.shift(real, 0.0, -dy)))
        if rnd == 0:
            known += _size_ladder(item, spec, W, H, times)
    return None


def _size_ladder(item, spec, W, H, times):
    """Measured boxes for a ladder of smaller size/width values at the
    current y: a template that fits its type to the safe width keeps the
    same width over the first size steps (hook_title, marker_text), so the
    solver's scaled prediction cannot see where it starts to narrow."""
    pspec = spec.get("params") or {}
    sizer = next((k for k in ("width", "size", "scale")
                  if (pspec.get(k) or {}).get("type") == "float"), None)
    if not sizer:
        return []
    sp = pspec[sizer]
    cur = keepout.num(item.get("params") or {}, sizer, sp.get("default", sp.get("max", 1.0)))
    lo = float(sp.get("min", 0.0))
    vals = sorted({round(max(lo, cur * f), 3) for f in (0.95, 0.9, 0.85, 0.8, 0.75, 0.7, 0.65, 0.6)}
                  - {round(cur, 3)},
                  reverse=True)
    if not vals:
        return []
    reps = _probe_items([dict(item, params=dict(item["params"], **{sizer: v})) for v in vals], W, H)
    out = []
    for v, rep in zip(vals, reps):
        if rep and not rep.get("errors") and rep.get("visible_frames"):
            real = keepout.settled_ink(rep, times)
            if real:
                out.append(({sizer: v}, real))
    return out


def _set_footprint(item, box, zones, W, H, estimated=False):
    """Store the item's ONE measurement (MotionItem.footprint): the box the
    captions keep clear of at its final placement, the frame aspect it was
    measured at and the face zones of its window (caption_carry reads it).
    No box drops it: the renderer measures before it builds captions."""
    if box:
        item["footprint"] = caption_carry.make_footprint(
            keepout.rounded(box, 4), W, H, [keepout.rounded(z, 4) for z in zones],
            estimated=estimated)
    else:
        item.pop("footprint", None)


def _keep_out(ctx, edl, item, rep):
    """Compare the probed graphic with the speaker's face and the safe area,
    move it to the nearest clear zone when it collides, and record its
    footprint. Mutates ``item``; returns (report text, new where-note or
    None). Never blocks the write: anything that cannot be measured leaves
    the item as placed (and unmeasured: the renderer measures it)."""
    item.pop("footprint", None)
    if rep is not None and rep.get("errors"):
        return "", None
    placed = {k: json.loads(json.dumps(item[k])) for k in ("params", "box") if k in item}
    try:
        if rep is None:
            return _keep_out_estimated(ctx, edl, item)
        return _keep_out_inner(ctx, edl, item, rep)
    except Exception as ex:  # noqa: BLE001 — a measurement never blocks an edit
        print(f"[motion] keep-out skipped: {str(ex)[:200]}", flush=True)
        item.pop("footprint", None)
        item.update(placed)       # a half-finished move is not reported, so undo it
        return "", None


def _keep_out_estimated(ctx, edl, item):
    """The keep-out on a lane with no browser to probe the composition. The
    agent, MCP and shorts lanes ship no Chromium, so this is what production
    writes run. The face is measured as usual and compared with the
    template's NOMINAL ink (keepout.nominal_ink). A collision moves it by
    the same solver without a verifying render, to a spot a full CLEARANCE
    off the face: the estimate can be a wrapped line short (the Elon lower
    third's role ran to two lines, and a graze on the estimate still sat on
    his mouth). Templates whose width is their own knob
    (keepout.COLUMN_TEMPLATES) are kept out of the side margins and the
    button rail too. The estimated box is stored as the footprint (flagged
    ``estimated``), so the caption plan steps around the graphic here as
    well; the renderer measures the real box before it burns captions. The
    reply says the box was estimated."""
    template = item["template"]
    spec = motion_templates.spec(template)
    if not keepout.applicable(template, spec, item.get("layer")):
        return "", None
    W, H = _canvas_size(ctx, edl)
    box = keepout.nominal_ink(template, spec, item.get("params") or {}, frame=(W, H))
    if not box:
        return "", None
    s, e = float(item["start"]), float(item["end"])
    track = []
    if getattr(ctx, "has_main_video", True):
        track = keepout.face_track(edl, getattr(ctx, "index", None) or {}, W, H, s, e,
                                   measure=_face_measure(ctx))
    zones = keepout.zones_of(track)
    allow = bool(item.get("allow_face_overlap"))
    hit = keepout.assess(box, track)
    face_bad = hit["hit"] and not allow
    issues = ([i for i in keepout.safe_issues(box, W, H) if i in ("side", "rail")]
              if template in keepout.COLUMN_TEMPLATES else [])
    bands = _caption_anchors(ctx, edl, item)
    notes = []
    if face_bad or (issues and not allow):
        why = []
        what = "the speaker's mouth" if hit["mouth"] else "the speaker's face"
        if face_bad:
            w0, w1 = hit["when"]
            why.append(f"covered {what} at {w0:.2f}-{w1:.2f}s")
        why += [keepout.ISSUE_TEXT[i] for i in issues]
        kw = dict(captions=[keepout.caption_band(y) for y in bands],
                  near_captions=[keepout.caption_obstacle(y) for y in bands],
                  mouths=keepout.zones_of(track, keepout.mouth_zone),
                  clear_penalty=keepout.CLEAR_PENALTY if face_bad else 0.0)
        params0 = item.get("params") or {}
        # another side/align the estimate can draw (the lower third's side)
        variants = []
        for key in keepout.HORIZONTAL_KEYS:
            p = (spec.get("params") or {}).get(key) or {}
            if p.get("type") != "enum" or item.get("box"):
                continue
            cur = params0.get(key, p.get("default"))
            for v in p.get("values") or []:
                alt = keepout.nominal_ink(template, spec, dict(params0, **{key: v}),
                                          frame=(W, H))
                if v != cur and v in ("left", "center", "right") and alt \
                        and keepout.rounded(alt) != keepout.rounded(box):
                    variants.append(({key: v}, alt))
        cands = keepout.candidates(template, spec, params0, box, variants, zones, W, H,
                                   require_clear=face_bad, **kw)
        if face_bad and not cands:
            cands = keepout.candidates(template, spec, params0, box, variants, zones, W, H, **kw)
        moved = False
        for _cost, patch, pred in cands[:3]:
            try:
                params = motion_templates.check_params(template, dict(item["params"], **patch),
                                                       html=item.get("html"))
            except ValueError:
                continue
            pspec = spec.get("params") or {}
            changes = ", ".join(f"{k} {_fmt(params0.get(k, _implicit(k, pspec.get(k) or {})))} → "
                                f"{_fmt(params.get(k))}" for k in sorted(patch))
            item["params"] = params
            box = keepout.nominal_ink(template, spec, params, frame=(W, H)) or pred
            place = keepout.where_label(box, zones) if face_bad else ""
            notes.append(
                f"KEEP-OUT (estimated): by the template's estimated size it {' and '.join(why)}, "
                "so it moved" + (f" {place}" if place else "") + f" ({changes}). This lane "
                "cannot render the graphic, so its box was estimated from the template: check "
                "the placement in the preview."
                + (" Pass allow_face_overlap=true only for a deliberate design over the face."
                   if face_bad else ""))
            moved = True
            break
        if not moved:
            notes.append(
                f"NOTE (keep-out, estimated): by the template's estimated size it "
                f"{' and '.join(why)}, and no clear zone fits it. Make it smaller"
                + (" or move it to a moment where the face is elsewhere (or "
                   "allow_face_overlap=true if deliberate)." if face_bad else "."))
    elif allow and issues:
        notes.append("NOTE (keep-out, estimated): kept as designed (allow_face_overlap), but by "
                     "the template's estimated size it "
                     + " and ".join(keepout.ISSUE_TEXT[i] for i in issues) + ".")
    _set_footprint(item, box, zones, W, H, estimated=True)
    return "".join("\n" + n for n in notes), None


def _keep_out_inner(ctx, edl, item, rep):
    """The keep-out on a probed graphic: its INK box (motion_engine.INK_ALPHA,
    what hides a face) against the face and the safe area; the stored
    footprint is the COVER box (COVER_ALPHA, what a caption must clear) of
    the final placement's probe."""
    W, H = _canvas_size(ctx, edl)
    times = _probe_times(item)
    box = keepout.settled_ink(rep, times)
    if not box:
        # nothing opaque enough to hide a face (a light leak, a soft
        # glow): no keep-out, and the captions clear whatever it covers
        _set_footprint(item, keepout.cover_box(rep, times), [], W, H)
        return "", None
    template = item["template"]
    spec = motion_templates.spec(template)
    s, e = float(item["start"]), float(item["end"])
    track = []
    if getattr(ctx, "has_main_video", True):
        track = keepout.face_track(edl, getattr(ctx, "index", None) or {}, W, H, s, e,
                                   measure=_face_measure(ctx))
    zones = keepout.zones_of(track)
    allow = bool(item.get("allow_face_overlap"))
    hit = keepout.assess(box, track)
    issues = keepout.safe_issues(box, W, H)
    movable = keepout.applicable(template, spec, item.get("layer"))
    bands = _caption_anchors(ctx, edl, item)
    notes = []
    face_bad = hit["hit"] and not allow and item.get("layer") != "behind_subject"
    if movable and not allow and (face_bad or issues):
        why = []
        if face_bad:
            what = "the speaker's mouth" if hit["mouth"] else "the speaker's face"
            w0, w1 = hit["when"]
            why.append(f"covered {what} at {w0:.2f}-{w1:.2f}s")
        why += [keepout.ISSUE_TEXT[i] for i in issues]
        # priced exactly as the caption plan will see it: the stored COVER
        # box reaches past the ink (a scrim's core) by the same margins
        cover0 = keepout.cover_box(rep, times)
        grow = ((max(0.0, box[1] - cover0[1]), max(0.0, cover0[3] - box[3]))
                if cover0 else (0.0, 0.0))
        found = _relocate(item, spec, box, zones, track, W, H,
                          ([keepout.caption_band(y) for y in bands],
                           [keepout.caption_obstacle(y, grow) for y in bands]),
                          off_face=face_bad)
        if found:
            patch, real, params, rep = found
            old = item.get("params") or {}
            pspec = spec.get("params") or {}
            changes = ", ".join(
                f"{k} {_fmt(old.get(k, _implicit(k, pspec.get(k) or {})))} → {_fmt(params.get(k))}"
                for k in sorted(patch))
            if item.get("box"):
                dx = (real[0] + real[2] - box[0] - box[2]) / 2.0
                dy = (real[1] + real[3] - box[1] - box[3]) / 2.0
                b = item["box"]
                item["box"] = [round(min(max(v + (dx if i % 2 == 0 else dy), 0.0), 1.0), 4)
                               for i, v in enumerate(b)]
            item["params"] = params
            place = keepout.where_label(real, zones) if face_bad else ""
            notes.append(f"KEEP-OUT: it {' and '.join(why)}, so it moved"
                         + (f" {place}" if place else "") + f" ({changes})."
                         + (" Pass allow_face_overlap=true only for a deliberate design over "
                            "the face." if face_bad else ""))
            box = real
        else:
            face = (f" (it draws y {box[1]:.2f}-{box[3]:.2f}; the face spans y "
                    f"{hit['face'][1]:.2f}-{hit['face'][3]:.2f})" if face_bad and hit.get("face") else "")
            where_to = ("no clear zone fits it — above the head, below the chin or beside the "
                        "face — inside the 9:16 safe area" if face_bad else
                        "no placement its own y/x/size reach fits the 9:16 safe area")
            notes.append(
                f"NOTE (keep-out): it {' and '.join(why)}{face}, and {where_to} (6% side "
                "margins, the right 12% clear between y 0.5 and 0.85, nothing below y 0.80). "
                "It stays where it is: make it smaller or shorter"
                + (", put it on a moment where the face is elsewhere, use "
                   "layer='behind_subject' for type behind the speaker, or pass "
                   "allow_face_overlap=true if covering the face is deliberate." if face_bad
                   else " (or change its copy)."))
    elif allow and issues:
        notes.append("NOTE (keep-out): kept as designed (allow_face_overlap), but it "
                     + " and ".join(keepout.ISSUE_TEXT[i] for i in issues) + ".")
    _set_footprint(item, keepout.cover_box(rep, times) or box, zones, W, H)
    # the INK bounds (type and plates, not soft scrims) are what the safe
    # area and the face are compared with, so they are what the reply reports
    return "".join("\n" + n for n in notes), _where(box)


# ── caption integrity ─────────────────────────────────────────────────────
# Captions and graphics share one stage (worker/caption_carry.py): with
# mute_captions unset a graphic hides only the spoken words it shows and the
# rest stay captioned, moved clear of it. Showcase review found the ways a
# sound-off viewer still loses speech: an explicit whole-window mute over
# words the graphic does not carry (a '140 characters' counter over "flying
# cars and all we got was"), and a speech-replacing graphic on the caption
# band with no clear band left beside the face. A kicker typed from memory
# ("so we can deal in") contradicting the audio ("so that we can deal in")
# is the third. All are cheap transcript comparisons. They never refuse a
# write: the reply NOTEs what the viewer would miss and the fix.

_tokens = caption_carry.tokens
_FUNCTION_WORDS = caption_carry.FUNCTION_WORDS
_graphic_lines = caption_carry.graphic_lines
_said = caption_carry._said
# Below this share of the spoken content words on the graphic, a caption
# mute hides the speech rather than replacing it.
MUTE_CARRY_MIN = 0.5
# A graphic line this close to a transcript phrase (difflib ratio over word
# tokens) but not equal to it is a paraphrase of what is heard.
PARAPHRASE_RATIO = 0.75
PARAPHRASE_NEAR_S = 3.0


def _runs_said(words, gap=0.5):
    """'a b … c d' for word dicts, a break wherever speech pauses."""
    runs, cur = [], []
    for w in words:
        if cur and float(w["t0"]) - float(cur[-1]["t1"]) > gap:
            runs.append(cur)
            cur = []
        cur.append(w)
    if cur:
        runs.append(cur)
    out = " … ".join(_said(r) for r in runs)
    return out if len(out) <= 140 else out[:137].rstrip() + "…"


def _mute_note(edl, index, tl, item, carried):
    """NOTE when this item's explicit whole-window mute hides speech it does
    not carry."""
    import captions as caplib
    others = dict(edl, motion=[m for m in edl.get("motion") or []
                               if m.get("id") != item.get("id")])
    words = caplib.caption_words(others, index, tl)
    s, e = float(item["start"]), float(item["end"])
    inside = [w for w in words if s <= (float(w["t0"]) + float(w["t1"])) / 2.0 <= e]
    if not inside:
        return None
    toks = [_tokens(w["w"]) for w in inside]
    content = [t for ts in toks for t in ts if t not in _FUNCTION_WORDS] or \
        [t for ts in toks for t in ts]
    missing = [t for t in content if t not in carried]
    if len(missing) < 2 or len(missing) <= len(content) * (1.0 - MUTE_CARRY_MIN):
        return None
    runs, cur = [], []
    for w, ts in zip(inside, toks):
        if ts and not any(t in carried for t in ts):
            cur.append(w)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    gone = " … ".join(_said(r) for r in runs)
    if len(gone) > 140:
        gone = gone[:137].rstrip() + "…"
    first = next((w for w, ts in zip(inside, toks)
                  if any(t in carried and t not in _FUNCTION_WORDS for t in ts)), None)
    later = (f", or start it at {float(first['t0']):.2f}s where its own words begin "
             "so everything before stays captioned"
             if first is not None and float(first["t0"]) - s >= 0.4 else "")
    return (f"NOTE (captions): mute_captions=true hides every caption over {s:g}-{e:g}s "
            f"but this graphic carries only {len(content) - len(missing)} of the "
            f"{len(content)} words that matter in what is said there, so a sound-off "
            f"viewer never reads \"{gone}\". Leave mute_captions unset (the default): "
            f"only the words it shows leave the captions and the rest stay captioned "
            f"beside it. Or carry those exact words on it (e.g. a kicker/label built "
            f"from the transcript){later}.")


def _word_level_notes(edl, index, tl, item, canvas=None):
    """NOTEs for a graphic whose captions keep running (mute_captions unset
    or false): the words it leaves muted for want of a clear band, the
    captions it leaves overlapping it, and where the rest moved (the caption
    plan, worker/caption_carry.py, over its stored footprint)."""
    import captions as caplib
    rep = caplib.caption_plan(edl, index, tl, canvas=canvas).report.get(item.get("id"))
    if not rep:
        return []
    s, e = float(item["start"]), float(item["end"])
    box = rep.get("box")
    notes = []
    if box is None:
        # not measured here (no footprint): the render measures it before it
        # places the captions, so there is nothing true to say
        return notes
    draws = (f" (by its estimated box it draws y {box[1]:.2f}-{box[3]:.2f}; the render "
             "measures it)" if rep.get("estimated") else
             f" (it draws y {box[1]:.2f}-{box[3]:.2f})")
    if rep["muted"] and caption_carry.mode(item) == caption_carry.MODE_WORDS:
        notes.append(
            f"NOTE (captions): no caption band is clear of this graphic{draws} and the "
            f"speaker's face over {s:g}-{e:g}s, so the words it does not show are muted: "
            f"a sound-off viewer never reads \"{_runs_said(rep['muted'])}\". Move it off "
            f"the caption band (its y param, e.g. above the head or in the top band) so "
            f"those words stay captioned beside it, or carry them on it.")
    elif rep["kept"]:
        notes.append(
            f"NOTE (captions): it sits on the caption band{draws} and no band clear of it "
            f"and of the speaker's face is left over {s:g}-{e:g}s, so the captions "
            f"\"{_runs_said(rep['kept'])}\" stay on their band and touch it. Make it smaller "
            f"or move it off the caption band (its y param) so they read clear of it.")
    elif rep["placed"] and abs(rep["placed"]["y"] - rep["placed"]["normal_y"]) >= 0.05:
        where = rep["placed"]
        notes.append(
            f"Captions for the words it does not show move to y≈{where['y']:.2f} while it "
            f"is up{draws}; the words it shows leave the captions.")
    return notes


def _verbatim(g, span):
    """Does graphic token list ``g`` quote ``span`` [(token, clipped)]
    exactly, allowing it to keep or drop words a cut clipped?"""
    j = 0
    for t, clipped in span:
        if j < len(g) and t == g[j]:
            j += 1
        elif not clipped:
            return False
    return j == len(g)


def _paraphrase_notes(edl, index, tl, item, lines):
    """NOTEs for graphic lines that nearly quote nearby speech."""
    import difflib
    import captions as caplib
    s, e = float(item["start"]), float(item["end"])
    words = caplib.heard_words(edl, index, tl, s - PARAPHRASE_NEAR_S, e + PARAPHRASE_NEAR_S)
    flat = [(t, i) for i, w in enumerate(words) for t in _tokens(w["w"])]
    seq = [t for t, _i in flat]
    clip = [(t, bool(words[i].get("clipped"))) for t, i in flat]
    notes = []
    for key, text in lines:
        g = _tokens(text)
        if not 3 <= len(g) <= 16 or len(seq) < 2:
            continue
        best = (0.0, 0, 0)
        for n in range(max(2, len(g) - 2), len(g) + 3):
            for i in range(0, len(seq) - n + 1):
                if _verbatim(g, clip[i:i + n]):
                    best = (1.0, i, n)
                    break
                r = difflib.SequenceMatcher(None, g, seq[i:i + n], autojunk=False).ratio()
                if r > best[0]:
                    best = (r, i, n)
            if best[0] >= 1.0:
                break
        r, i, n = best
        if r >= 1.0 or r < PARAPHRASE_RATIO:
            continue
        span = words[flat[i][1]:flat[i + n - 1][1] + 1]
        quote = _said(span)
        notes.append(
            f"NOTE (captions): {key} \"{' '.join(text.split())}\" paraphrases what is said at "
            f"{float(span[0]['t0']):.2f}-{float(span[-1]['t1']):.2f}s (\"{quote}\"); viewers "
            f"hear one and read the other. Quote the transcript exactly, e.g. "
            f"{key}=\"{quote}\".")
    return notes


def _caption_integrity_notes(ctx, edl, item):
    """Deterministic caption-integrity NOTEs for one written motion item
    ('' when it is fine, there is no transcript, or anything goes wrong —
    a lint never blocks the edit it comments on)."""
    index = getattr(ctx, "index", None) or {}
    if not index.get("words") or not edl.get("keep"):
        return ""
    try:
        from timeline import Timeline
        tl = Timeline(edl["keep"], edl.get("inserts") or [], edl.get("speed"))
        lines = _graphic_lines(item)
        notes = []
        caps = edl.get("captions")
        if isinstance(caps, dict) and caps.get("mode") == "from_transcript":
            if caption_carry.mode(item) == caption_carry.MODE_ALL:
                carried = {t for _k, v in lines for t in _tokens(v)}
                note = _mute_note(edl, index, tl, item, carried)
                if note:
                    notes.append(note)
            else:
                notes += _word_level_notes(edl, index, tl, item,
                                           canvas=_canvas_size(ctx, edl))
        notes += _paraphrase_notes(edl, index, tl, item, lines)
    except Exception as e:  # noqa: BLE001
        print(f"[motion] caption integrity check skipped: {str(e)[:160]}", flush=True)
        return ""
    return "".join("\n" + n for n in notes)


# Sound is deliberate: templates declare sound ROLES (mapped onto the owner-
# approved real recordings in worker/sound_library), but nothing adds sound
# unless the editor asks for it on a moment that earns it.
SFX_DEFAULT = False


def add_motion_graphic(ctx, template, start, end=None, params=None, html=None,
                       layer=None, box=None, mute_captions=None, sfx=None,
                       id=None, purpose=None, allow_face_overlap=None):
    """Place a premium browser-rendered motion graphic on the program clock."""
    at = _at()
    template = str(template or "").strip()
    try:
        spec = motion_templates.spec(template)
    except ValueError as e:
        return f"REJECTED: {e}"
    if isinstance(params, str):
        try:
            params = json.loads(params) if params.strip() else {}
        except ValueError:
            return "REJECTED: params must be a JSON object."
    try:
        clean = motion_templates.check_params(template, params or {}, html=html)
    except ValueError as e:
        return f"REJECTED: {e}"
    edl = json.loads(json.dumps(ctx.latest_edl()["json"]))
    prog = _program_duration(edl)
    if prog <= 0.3:
        return "REJECTED: there is no program yet — place footage first, then add motion graphics."
    try:
        s = float(start)
        e = float(end) if end is not None else s + float(spec.get("duration") or 3.0)
    except (TypeError, ValueError):
        return "REJECTED: start/end must be program seconds (numbers)."
    req = (s, e)
    s = round(min(max(s, 0.0), max(0.0, prog - 0.2)), 3)
    e = round(min(max(e, s + 0.2), prog), 3)
    items = [dict(m) for m in (edl.get("motion") or [])]
    if id is not None:
        if not _ID_RE.match(str(id)):
            return "REJECTED: id must be 1-40 letters, digits, '_' or '-'."
        if any(m.get("id") == id for m in items):
            return f"REJECTED: a motion graphic '{id}' already exists — use set_motion_graphic to change it."
        mid = str(id)
    else:
        mid = at._next_item_id(items, "mg")
    layer = layer or spec.get("layer") or "above_captions"
    if layer not in LAYERS:
        return f"REJECTED: layer must be one of {', '.join(LAYERS)}."
    item = {"id": mid, "template": template, "start": s, "end": e, "params": clean,
            "layer": layer}
    if template == "html":
        item["html"] = html
    if box is not None:
        item["box"] = box
    if mute_captions is not None:
        item["mute_captions"] = bool(mute_captions)
    if purpose:
        item["purpose"] = " ".join(str(purpose).split())[:300]
    if allow_face_overlap:
        item["allow_face_overlap"] = True
    err, where, bbox, rep = _probe_full(ctx, edl, item)
    if err:
        return err
    keep_note, moved_where = _keep_out(ctx, edl, item, rep)
    where = moved_where or where
    behind_note = ""
    if layer == "behind_subject":
        behind_note, err = _attach_subject_matte(ctx, edl, item, bbox)
        if err:
            return err
    items.append(item)
    edl["motion"] = items
    notes = []
    cues = _sfx_cues(spec, clean, s, e, seed=mid, with_dur=True) if (SFX_DEFAULT if sfx is None else sfx) else []
    if cues:
        notes += _apply_owned_sfx(ctx, edl, mid, cues)
    clamp = ""
    if abs(req[0] - s) > 0.05 or abs(req[1] - e) > 0.05:
        clamp = f"\nCLAMPED: requested {req[0]:g}-{req[1]:g}s into this {prog:g}s program; placed at {s}-{e}s."
    sound = (f"; sound cues hitting at: {', '.join(f'{c[1]}@{c[0]:g}s' for c in cues)}" if cues else "")
    depth = " BEHIND the subject" if layer == "behind_subject" else ""
    res = ctx.write_edl(edl, f"motion graphic {template}{depth} at {s}-{e}s [{mid}]{sound}")
    if res.startswith("REJECTED"):
        return res
    return (res + where + keep_note + clamp + (f"\nNOTE: {'; '.join(notes)}" if notes else "")
            + behind_note + _caption_integrity_notes(ctx, edl, item))


def set_motion_graphic(ctx, id, start=None, end=None, params=None, html=None,
                       layer=None, box=None, mute_captions=None, sfx=None,
                       template=None, purpose=None, allow_face_overlap=None):
    """Patch one motion graphic in place (params merge; owned sounds follow)."""
    edl = json.loads(json.dumps(ctx.latest_edl()["json"]))
    items = [dict(m) for m in (edl.get("motion") or [])]
    hit = next((m for m in items if m.get("id") == id), None)
    if not hit:
        have = ", ".join(m.get("id", "?") for m in items) or "none"
        return f"REJECTED: no motion graphic '{id}'. Existing: {have}."
    old_start, old_end = float(hit["start"]), float(hit["end"])
    old_shape = json.dumps([hit.get(k) for k in
                            ("start", "end", "template", "params", "html", "box", "layer")],
                           sort_keys=True)
    if template is not None:
        try:
            motion_templates.spec(template)
        except ValueError as e:
            return f"REJECTED: {e}"
        hit["template"] = template
    if isinstance(params, str):
        try:
            params = json.loads(params) if params.strip() else {}
        except ValueError:
            return "REJECTED: params must be a JSON object."
    merged = dict(hit.get("params") or {}) if template is None else {}
    merged.update(params or {})
    if html is not None:
        hit["html"] = html
    try:
        hit["params"] = motion_templates.check_params(hit["template"], merged, html=hit.get("html"))
    except ValueError as e:
        return f"REJECTED: {e}"
    prog = _program_duration(edl)
    try:
        if start is not None:
            hit["start"] = round(min(max(float(start), 0.0), max(0.0, prog - 0.2)), 3)
        if end is not None:
            hit["end"] = round(min(max(float(end), hit["start"] + 0.2), prog), 3)
    except (TypeError, ValueError):
        return "REJECTED: start/end must be numbers."
    if hit["end"] <= hit["start"] + 0.2:
        hit["end"] = round(min(prog, hit["start"] + 0.2), 3)
    if layer is not None:
        if layer not in LAYERS:
            return f"REJECTED: layer must be one of {', '.join(LAYERS)}."
        hit["layer"] = layer
    if box is not None:
        hit["box"] = box or None
    if mute_captions is not None:
        hit["mute_captions"] = bool(mute_captions)
    if purpose is not None:
        hit["purpose"] = " ".join(str(purpose).split())[:300] or None
    if allow_face_overlap is not None:
        if allow_face_overlap:
            hit["allow_face_overlap"] = True
        else:
            hit.pop("allow_face_overlap", None)
    if hit["template"] != "html":
        hit.pop("html", None)
    err, where, bbox, rep = _probe_full(ctx, edl, hit)
    if err:
        return err
    keep_note, moved_where = _keep_out(ctx, edl, hit, rep)
    where = moved_where or where
    behind_note = ""
    if hit.get("layer") == "behind_subject":
        shape = json.dumps([hit.get(k) for k in
                            ("start", "end", "template", "params", "html", "box", "layer")],
                           sort_keys=True)
        stale = ((hit.get("behind") or {}).get("geom")
                 not in (None, subject_matte_geom(edl.get("frame"))))
        if not hit.get("behind") or shape != old_shape or stale:
            # A new window needs a new mask (it is pixels of that footage);
            # a new design re-measures how much of it the subject crosses
            # (a cache hit when the window is unchanged); a mask measured in
            # a framing set_frame has since replaced is measured again.
            behind_note, err = _attach_subject_matte(ctx, edl, hit, bbox)
            if err:
                return err
    else:
        hit.pop("behind", None)
    edl["motion"] = items
    owned = [s for s in (edl.get("sfx") or []) if str(s.get("id", "")).startswith(_owned_sfx_prefix(id))]
    notes = []
    tspec = motion_templates.spec(hit["template"])
    # A new length moves the cues that follow it (a ``land`` landing, an
    # end-relative or fitted cue): those are re-derived rather than shifted.
    span, old_span = hit["end"] - hit["start"], old_end - old_start
    respaced = (bool(owned) and abs(span - old_span) > 1e-6
                and _sfx_cues(tspec, hit["params"], 0.0, span, seed=id, with_dur=True)
                != _sfx_cues(tspec, hit["params"], 0.0, old_span, seed=id, with_dur=True))
    if sfx is True or (sfx is None and owned and (template is not None or params or respaced)):
        cues = _sfx_cues(tspec, hit["params"], hit["start"], hit["end"], seed=id, with_dur=True)
        notes += _apply_owned_sfx(ctx, edl, id, cues)
    elif sfx is False:
        edl["sfx"] = [s for s in (edl.get("sfx") or []) if s not in owned]
    elif owned and abs(hit["start"] - old_start) > 1e-6:
        d = hit["start"] - old_start
        for s in edl.get("sfx") or []:
            if s in owned:
                sound_library.retime(s, sound_library.hit_at(s) + d)
    res = ctx.write_edl(edl, f"updated motion graphic {id} ({hit['template']}) at {hit['start']}-{hit['end']}s")
    if res.startswith("REJECTED"):
        return res
    return (res + where + keep_note + (f"\nNOTE: {'; '.join(notes)}" if notes else "")
            + (behind_note or "") + _caption_integrity_notes(ctx, edl, hit))


def remove_motion_graphic(ctx, id):
    edl = json.loads(json.dumps(ctx.latest_edl()["json"]))
    items = [m for m in (edl.get("motion") or []) if m.get("id") != id]
    if len(items) == len(edl.get("motion") or []):
        have = ", ".join(m.get("id", "?") for m in edl.get("motion") or []) or "none"
        return f"REJECTED: no motion graphic '{id}'. Existing: {have}."
    edl["motion"] = items
    edl["sfx"] = [s for s in (edl.get("sfx") or []) if not str(s.get("id", "")).startswith(_owned_sfx_prefix(id))]
    return ctx.write_edl(edl, f"removed motion graphic {id} and its sound cues")


_TEMPLATE_PARAM = {"type": "string", "description": "Template name from list_motion_templates, or 'html'."}
_PARAMS_PARAM = {"type": "object", "description": "Template parameters (see list_motion_templates)."}
_ALLOW_FACE_PARAM = {"type": "boolean", "description": (
    "Keep a deliberate placement over the face (skips the keep-out move).")}
_MUTE_PARAM = {"type": "boolean", "description": (
    "Omit (default) for word-level: captions drop only the spoken words this graphic "
    "shows. true = no captions for its whole window; false = captions keep running "
    "(a number/*starred* word it shows is still not repeated).")}

TOOL_SPECS = {
    "list_motion_templates": (
        list_motion_templates,
        "READ: the premium motion-graphics library (animated titles, captions-adjacent type, "
        "counters, callouts, lists, social/UI cards, charts, transitions, textures, CTAs) with "
        "each template's purpose, params and built-in sound cues. Call once before designing "
        "motion beats.",
        {"category": {"type": "string", "description": "Optional filter: type, data, callout, social, "
                      "layout, transition, texture, cta, caption."}}),
    "add_motion_graphic": (
        add_motion_graphic,
        "Place a browser-rendered, After-Effects-grade motion graphic on the PROGRAM clock: "
        "spring/blur entrances, glow, gradients, 3D depth, masks, icon/emoji pops, counters, "
        "UI cards. One call = a finished composition, silent by default; pass sfx=true only for a "
        "moment that earns sound (its template cues map to the approved sound library). start/end are program seconds; end defaults to the "
        "template's natural duration. Cue it to the exact word/beat it amplifies (use word "
        "times from get_kept_transcript). layer='above_captions' (default for designed moments) "
        "or 'below_captions'. layer='behind_subject' is the premium depth signature: the "
        "graphic (a giant hero word, a number, a shape) is drawn INTO the shot and the "
        "speaker stays in FRONT of it — the person is measured out of the footage like "
        "add_text_behind (same rules: one continuous take, no speed ramp, >=0.4s; refused "
        "with the measured reason when there is no clear subject), and the reply reports how "
        "much of the graphic the subject crosses. Use it for 1-2 hero moments per short, with "
        "BIG type placed where the speaker's head/shoulders cross it. CAPTIONS: leave "
        "mute_captions unset — the captions drop exactly the spoken words this graphic shows "
        "(its number, slammed word, quoted kicker or rows) and keep every other spoken word, "
        "moved to a band clear of the box it draws while it is up; the reply NOTEs words it "
        "must mute because no band is clear of it and the face (then move it off the caption "
        "band). mute_captions=true hides every caption for its whole window (only when it "
        "replaces the whole spoken line); false keeps them all running (a number or *starred* "
        "word it shows is still not repeated). template='html' takes your own HTML/CSS/JS on the "
        "MG runtime in `html`. The write is rejected if the composition errors or draws nothing. "
        "FACE KEEP-OUT: the write measures where the graphic draws against the speaker's face "
        "and mouth over its window (through the crop, zooms and cards) and the 9:16 safe area "
        "(6% side margins, the right 12% clear between y 0.5 and 0.85, nothing below y 0.80); a "
        "graphic that covers the face or leaves the safe area is MOVED to the nearest clear zone "
        "with its own y (and x/align/side where it has them) — above the head, below the chin, "
        "beside the face — and the reply says what moved (KEEP-OUT) or why nothing fits (NOTE). "
        "allow_face_overlap=true keeps a deliberate design over the face.",
        {"template": _TEMPLATE_PARAM, "start": {"type": "number"}, "end": {"type": "number"},
         "params": _PARAMS_PARAM, "html": {"type": "string"},
         "layer": {"type": "string", "enum": list(LAYERS)},
         "box": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4,
                 "description": "Optional capture region hint [x0,y0,x1,y1] frame fractions."},
         "mute_captions": _MUTE_PARAM, "sfx": {"type": "boolean"},
         "id": {"type": "string"}, "purpose": {"type": "string"},
         "allow_face_overlap": _ALLOW_FACE_PARAM}),
    "set_motion_graphic": (
        set_motion_graphic,
        "Patch an existing motion graphic by id — window, params (merged into the current ones), "
        "template, layer, html or caption muting (mute_captions as in add_motion_graphic; the "
        "drawn box captions keep clear of is re-measured). Its owned sound cues move with it; sfx=true "
        "re-derives them, sfx=false removes them. A behind_subject graphic whose window "
        "changes is re-measured against its new footage. Every patch re-runs the face and "
        "safe-area keep-out (see add_motion_graphic): a y that would cover the face is moved "
        "and reported; allow_face_overlap=true keeps it (false clears that). Modify instead "
        "of removing and re-adding.",
        {"id": {"type": "string"}, "start": {"type": "number"}, "end": {"type": "number"},
         "params": _PARAMS_PARAM, "html": {"type": "string"}, "template": {"type": "string"},
         "layer": {"type": "string", "enum": list(LAYERS)},
         "box": {"type": "array", "items": {"type": "number"}},
         "mute_captions": _MUTE_PARAM, "sfx": {"type": "boolean"},
         "purpose": {"type": "string"}, "allow_face_overlap": _ALLOW_FACE_PARAM}),
    "remove_motion_graphic": (
        remove_motion_graphic,
        "Remove one motion graphic by id together with its owned sound cues.",
        {"id": {"type": "string"}}),
    "list_sound_library": (
        list_sound_library,
        "READ: Valmera's sound library — real recordings approved by ear (whooshes, swish, impact, "
        "risers, camera shutters, keyboard typing, clicks, pop, tick, ding, glitches, cash register, "
        "heartbeat) with when to use each and a suggested gain. Place with "
        "add_sfx(storage_key='sound:<id>', at=<the frame it hits>). Sound only on meaningful "
        "on-screen moments, sparse (≈ one every 4-5 s at most), never on captions.",
        {"role": {"type": "string", "description": "optional role filter, e.g. whoosh, click, riser"}}),
}
