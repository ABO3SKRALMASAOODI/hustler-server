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

import config  # noqa: F401  (kept for parity with other tool modules)
import db as dbx
import motion_engine
import motion_templates
import sfx_kit
import storage

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
KIT_PREFIX = "kit:"
DEFAULT_KIT_GAIN_DB = -3.0


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


# ── SFX kit ────────────────────────────────────────────────────────────────

def kit_search(query, limit=6):
    """Kit sounds whose name/use matches the query words (deterministic)."""
    q = [w for w in re.split(r"[^a-z0-9]+", str(query or "").lower()) if w]
    syn = {"woosh": "whoosh", "swoosh": "whoosh", "transition": "whoosh", "boom": "impact",
           "hit": "impact", "thud": "impact", "bubble": "pop", "button": "click",
           "keyboard": "typing", "type": "typing", "bell": "ding", "success": "chime",
           "money": "coin", "cash": "coin", "camera": "shutter", "photo": "shutter",
           "build": "riser", "tension": "riser", "bass": "sub_drop", "drop": "sub_drop",
           "error": "glitch", "digital": "glitch", "slide": "swipe", "message": "notification",
           "ping": "notification", "counter": "tick", "clock": "tick", "slam": "kick", "punch": "kick"}
    stop = {"a", "an", "and", "in", "on", "of", "the", "for", "to", "with", "sound", "sfx", "effect"}
    q = [syn.get(w, w) for w in q if w not in stop and len(w) > 2]
    scored = []
    for kind, (dur, use) in sfx_kit.KINDS.items():
        kind_words = set(kind.split("_"))
        hay_words = kind_words | set(re.split(r"[^a-z0-9]+", use.lower()))
        score = sum(2 if (w in kind_words or w == kind) else 1 for w in q
                    if w in hay_words or w == kind)
        if score:
            scored.append((score, kind, dur, use))
    scored.sort(key=lambda r: (-r[0], r[1]))
    return [{"id": KIT_PREFIX + k, "kind": k, "duration_s": d, "use": u}
            for _s, k, d, u in scored[:limit]]


def ensure_kit_asset(ctx, kind):
    """Project storage key for a kit sound, synthesizing/uploading once."""
    if kind not in sfx_kit.KINDS:
        raise ValueError(f"unknown kit sound '{kind}'. Kit: {', '.join(sorted(sfx_kit.KINDS))}")
    key = f"sfx/{ctx.project_id}/kit-{kind}-{sfx_kit.fingerprint(kind)}.wav"
    existing = ctx.db.run(dbx.asset_by_key, ctx.project_id, key)
    if existing:
        return key
    d = os.path.join(tempfile.gettempdir(), "valmera-sfx-kit", sfx_kit.KIT_VERSION)
    os.makedirs(d, exist_ok=True)
    local = os.path.join(d, f"{kind}.wav")
    if not os.path.exists(local):
        tmp = local + f".{os.getpid()}.tmp"
        sfx_kit.render(kind, tmp)
        os.replace(tmp, local)
    try:
        present = storage.exists(key)
    except Exception:
        present = False
    if not present:
        storage.upload_file(local, key, "audio/wav")
    dur = sfx_kit.KINDS[kind][0]
    ctx.db.run(dbx.insert_asset, ctx.project_id, "music", key,
               bytes_=os.path.getsize(local), duration_s=dur,
               meta={"filename": f"{kind}.wav", "source": "valmera-sfx-kit",
                     "license": "Valmera original (synthesized, no third-party rights)",
                     "license_note": "Valmera built-in sound kit — free to use in any edit",
                     "caption": sfx_kit.KINDS[kind][1], "kit_kind": kind,
                     "kit_version": sfx_kit.KIT_VERSION})
    return key


def resolve_kit_reference(ctx, storage_key):
    """(sound_dict, error) for a 'kit:<kind>' reference; (None, None) otherwise."""
    if not isinstance(storage_key, str) or not storage_key.startswith(KIT_PREFIX):
        return None, None
    kind = storage_key[len(KIT_PREFIX):].strip()
    try:
        key = ensure_kit_asset(ctx, kind)
    except ValueError as e:
        return None, f"REJECTED: {e}"
    except Exception as e:  # noqa: BLE001
        return None, (f"Could not prepare the kit sound ({str(e)[:160]}). Try again; "
                      "do NOT claim a sound was added.")
    return {"name": f"{kind} (Valmera kit)", "duration_s": sfx_kit.KINDS[kind][0],
            "library": True, "storage_key": key}, None


def list_sfx_kit(ctx):
    rows = [f"- kit:{k} ({d:g}s) — {u}" for k, (d, u) in sfx_kit.KINDS.items()]
    return ("Valmera built-in sound kit (always available, no licence terms). Place one with "
            "add_sfx(storage_key='kit:<kind>', at=<program seconds>). Cue the sound where the "
            "motion LANDS; layer a riser into an impact for a big reveal; keep UI ticks/pops "
            "quiet (gain -8 to -12 dB).\n" + "\n".join(rows))


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


def _sfx_cues(spec, params, start, end):
    cues = []
    for c in spec.get("sfx") or []:
        kind = c.get("kind")
        if kind not in sfx_kit.KINDS:
            continue
        at = float(c.get("at") or 0.0)
        t = (end + at) if at < 0 else (start + at)
        rep = c.get("repeat")
        if rep and isinstance(params.get(rep.get("param")), list):
            n = len(params[rep["param"]])
            for i in range(n):
                cues.append((round(start + float(rep.get("from", at)) + i * float(rep.get("every", 0.3)), 3),
                             kind, float(c.get("gain_db", DEFAULT_KIT_GAIN_DB))))
            continue
        cues.append((round(t, 3), kind, float(c.get("gain_db", DEFAULT_KIT_GAIN_DB))))
    return [(t, k, g) for t, k, g in cues if start - 0.01 <= t <= end]


def _owned_sfx_prefix(mid):
    return f"mg_{mid}_sfx"


def _apply_owned_sfx(ctx, edl, mid, cues):
    items = [s for s in (edl.get("sfx") or []) if not str(s.get("id", "")).startswith(_owned_sfx_prefix(mid))]
    prog = _program_duration(edl)
    notes = []
    for k, (t, kind, gain) in enumerate(cues):
        if t > prog - 0.05:
            continue
        try:
            key = ensure_kit_asset(ctx, kind)
        except Exception as e:  # noqa: BLE001
            notes.append(f"sound {kind} unavailable ({str(e)[:80]})")
            continue
        items.append({"id": f"{_owned_sfx_prefix(mid)}{k + 1}", "storage_key": key,
                      "at": max(0.0, round(t, 3)), "gain_db": gain,
                      "purpose": f"{kind} for motion graphic {mid}"})
    edl["sfx"] = items
    return notes


def _probe_item(item, W, H, fps=30.0):
    """Errors/visibility for an item at quarter scale, or None if the browser
    is unavailable here (the render path will still try)."""
    try:
        job = motion_templates.build_job(item, W, H, fps)
    except Exception as e:  # noqa: BLE001
        return {"errors": [str(e)[:200]], "visible_frames": 0, "samples": 0, "bboxes": []}
    span = float(item["end"]) - float(item["start"])
    times = [round(span * f, 3) for f in (0.12, 0.35, 0.6, 0.9)]
    try:
        return motion_engine.probe([job], [times])[0]
    except motion_engine.MotionRenderError:
        return None


def _validate_and_probe(ctx, edl, item):
    W, H = _canvas_size(ctx, edl)
    rep = _probe_item(item, W, H)
    if rep is None:
        return None, "\nNOTE: the composition could not be pre-checked here; render a preview to inspect it."
    if rep["errors"]:
        return (f"REJECTED: the composition raised a script error: {rep['errors'][0]}. "
                "Fix the HTML/params and try again."), ""
    if rep["visible_frames"] == 0:
        return ("REJECTED: the composition drew nothing visible at any sampled moment "
                "(check text/params, colors with zero alpha, or elements positioned off-frame)."), ""
    bb = rep["bboxes"]
    if bb:
        x0 = min(b[0] for b in bb); y0 = min(b[1] for b in bb)
        x1 = max(b[2] for b in bb); y1 = max(b[3] for b in bb)
        return None, f"\nDraws within x {x0:.2f}-{x1:.2f}, y {y0:.2f}-{y1:.2f} of the frame."
    return None, ""


def add_motion_graphic(ctx, template, start, end=None, params=None, html=None,
                       layer=None, box=None, mute_captions=None, sfx=True,
                       id=None, purpose=None):
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
    item = {"id": mid, "template": template, "start": s, "end": e, "params": clean,
            "layer": layer or spec.get("layer") or "above_captions"}
    if template == "html":
        item["html"] = html
    if box is not None:
        item["box"] = box
    if mute_captions is not None:
        item["mute_captions"] = bool(mute_captions)
    if purpose:
        item["purpose"] = " ".join(str(purpose).split())[:300]
    err, where = _validate_and_probe(ctx, edl, item)
    if err:
        return err
    items.append(item)
    edl["motion"] = items
    notes = []
    cues = _sfx_cues(spec, clean, s, e) if sfx else []
    if cues:
        notes += _apply_owned_sfx(ctx, edl, mid, cues)
    clamp = ""
    if abs(req[0] - s) > 0.05 or abs(req[1] - e) > 0.05:
        clamp = f"\nCLAMPED: requested {req[0]:g}-{req[1]:g}s into this {prog:g}s program; placed at {s}-{e}s."
    sound = (f"; sound cues: {', '.join(f'{k}@{t:g}s' for t, k, _g in cues)}" if cues else "")
    res = ctx.write_edl(edl, f"motion graphic {template} at {s}-{e}s [{mid}]{sound}")
    if res.startswith("REJECTED"):
        return res
    return res + where + clamp + (f"\nNOTE: {'; '.join(notes)}" if notes else "")


def set_motion_graphic(ctx, id, start=None, end=None, params=None, html=None,
                       layer=None, box=None, mute_captions=None, sfx=None,
                       template=None, purpose=None):
    """Patch one motion graphic in place (params merge; owned sounds follow)."""
    edl = json.loads(json.dumps(ctx.latest_edl()["json"]))
    items = [dict(m) for m in (edl.get("motion") or [])]
    hit = next((m for m in items if m.get("id") == id), None)
    if not hit:
        have = ", ".join(m.get("id", "?") for m in items) or "none"
        return f"REJECTED: no motion graphic '{id}'. Existing: {have}."
    old_start = float(hit["start"])
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
        hit["layer"] = layer
    if box is not None:
        hit["box"] = box or None
    if mute_captions is not None:
        hit["mute_captions"] = bool(mute_captions)
    if purpose is not None:
        hit["purpose"] = " ".join(str(purpose).split())[:300] or None
    if hit["template"] != "html":
        hit.pop("html", None)
    err, where = _validate_and_probe(ctx, edl, hit)
    if err:
        return err
    edl["motion"] = items
    owned = [s for s in (edl.get("sfx") or []) if str(s.get("id", "")).startswith(_owned_sfx_prefix(id))]
    notes = []
    if sfx is True or (sfx is None and owned and (template is not None or params)):
        cues = _sfx_cues(motion_templates.spec(hit["template"]), hit["params"], hit["start"], hit["end"])
        notes += _apply_owned_sfx(ctx, edl, id, cues)
    elif sfx is False:
        edl["sfx"] = [s for s in (edl.get("sfx") or []) if s not in owned]
    elif owned and abs(hit["start"] - old_start) > 1e-6:
        d = hit["start"] - old_start
        for s in edl.get("sfx") or []:
            if s in owned:
                s["at"] = round(max(0.0, float(s["at"]) + d), 3)
    res = ctx.write_edl(edl, f"updated motion graphic {id} ({hit['template']}) at {hit['start']}-{hit['end']}s")
    if res.startswith("REJECTED"):
        return res
    return res + where + (f"\nNOTE: {'; '.join(notes)}" if notes else "")


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
        "UI cards. One call = a finished composition plus its synced kit sound cues "
        "(sfx=false to place it silently). start/end are program seconds; end defaults to the "
        "template's natural duration. Cue it to the exact word/beat it amplifies (use word "
        "times from get_kept_transcript). layer='above_captions' (default for designed moments) "
        "or 'below_captions'. Templates that replace the spoken words set mute_captions; pass "
        "mute_captions explicitly to override. template='html' takes your own HTML/CSS/JS on the "
        "MG runtime in `html`. The write is rejected if the composition errors or draws nothing.",
        {"template": _TEMPLATE_PARAM, "start": {"type": "number"}, "end": {"type": "number"},
         "params": _PARAMS_PARAM, "html": {"type": "string"},
         "layer": {"type": "string", "enum": ["above_captions", "below_captions"]},
         "box": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4,
                 "description": "Optional capture region hint [x0,y0,x1,y1] frame fractions."},
         "mute_captions": {"type": "boolean"}, "sfx": {"type": "boolean"},
         "id": {"type": "string"}, "purpose": {"type": "string"}}),
    "set_motion_graphic": (
        set_motion_graphic,
        "Patch an existing motion graphic by id — window, params (merged into the current ones), "
        "template, layer, html or caption muting. Its owned sound cues move with it; sfx=true "
        "re-derives them, sfx=false removes them. Modify instead of removing and re-adding.",
        {"id": {"type": "string"}, "start": {"type": "number"}, "end": {"type": "number"},
         "params": _PARAMS_PARAM, "html": {"type": "string"}, "template": {"type": "string"},
         "layer": {"type": "string", "enum": ["above_captions", "below_captions"]},
         "box": {"type": "array", "items": {"type": "number"}},
         "mute_captions": {"type": "boolean"}, "sfx": {"type": "boolean"},
         "purpose": {"type": "string"}}),
    "remove_motion_graphic": (
        remove_motion_graphic,
        "Remove one motion graphic by id together with its owned sound cues.",
        {"id": {"type": "string"}}),
    "list_sfx_kit": (
        list_sfx_kit,
        "READ: Valmera's built-in sound-design kit (whooshes, swooshes, pops, clicks, ticks, kicks, "
        "dings, chimes, notification, coin, risers, impacts, sub drop, glitch, shutter, typing). "
        "Always available and licence-free; place with add_sfx(storage_key='kit:<kind>', at=...).",
        {}),
}
