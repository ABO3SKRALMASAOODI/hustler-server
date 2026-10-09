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
    key = f"sfx/{ctx.project_id}/lib-{sound_id}-{row['sha'][:10]}.flac"
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
            "library": True, "storage_key": key, "gain_db": row["gain_db"]}, None


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
            "Place with add_sfx(storage_key='sound:<id>', at=<program seconds>, gain_db=<suggested>).\n"
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


def _sfx_cues(spec, params, start, end, seed=""):
    """[(time, sound_id, gain_db)] for a template's declared sound roles,
    mapped onto approved library recordings (roles without one are skipped).
    A cue with ``repeat`` sounds once per entry of a list param: ``from`` +
    i*``every``; optional ``fit``/``min_every`` tighten ``every`` to
    (duration - fit)/(n - 1), ``require`` skips rows whose named field is
    blank, and ``field``/``offset`` use a row's own time."""
    cues = []
    for n, c in enumerate(spec.get("sfx") or []):
        row = sound_library.pick(c.get("kind") or "", seed=f"{seed}:{n}")
        if not row:
            continue
        gain = float(c.get("gain_db", row["gain_db"]))
        at = float(c.get("at") or 0.0)
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
                cues.append((round(start + off, 3), row["id"], gain))
            continue
        cues.append((round(t, 3), row["id"], gain))
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
            key = ensure_library_asset(ctx, kind)
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


def _probe_report(ctx, edl, item):
    """(error, where-note, drawn bbox (x0, y0, x1, y1) fractions or None)."""
    W, H = _canvas_size(ctx, edl)
    rep = _probe_item(item, W, H)
    if rep is None:
        return None, "\nNOTE: the composition could not be pre-checked here; render a preview to inspect it.", None
    if rep["errors"]:
        return (f"REJECTED: the composition raised a script error: {rep['errors'][0]}. "
                "Fix the HTML/params and try again."), "", None
    if rep["visible_frames"] == 0:
        return ("REJECTED: the composition drew nothing visible at any sampled moment "
                "(check text/params, colors with zero alpha, or elements positioned off-frame)."), "", None
    bb = rep["bboxes"]
    if bb:
        x0 = min(b[0] for b in bb); y0 = min(b[1] for b in bb)
        x1 = max(b[2] for b in bb); y1 = max(b[3] for b in bb)
        return None, f"\nDraws within x {x0:.2f}-{x1:.2f}, y {y0:.2f}-{y1:.2f} of the frame.", (x0, y0, x1, y1)
    return None, "", None


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
    if _overlaps(fx.get("picture_cards"), s, e):
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


# Sound is deliberate: templates declare sound ROLES (mapped onto the owner-
# approved real recordings in worker/sound_library), but nothing adds sound
# unless the editor asks for it on a moment that earns it.
SFX_DEFAULT = False


def add_motion_graphic(ctx, template, start, end=None, params=None, html=None,
                       layer=None, box=None, mute_captions=None, sfx=None,
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
    err, where, bbox = _probe_report(ctx, edl, item)
    if err:
        return err
    behind_note = ""
    if layer == "behind_subject":
        behind_note, err = _attach_subject_matte(ctx, edl, item, bbox)
        if err:
            return err
    items.append(item)
    edl["motion"] = items
    notes = []
    cues = _sfx_cues(spec, clean, s, e, seed=mid) if (SFX_DEFAULT if sfx is None else sfx) else []
    if cues:
        notes += _apply_owned_sfx(ctx, edl, mid, cues)
    clamp = ""
    if abs(req[0] - s) > 0.05 or abs(req[1] - e) > 0.05:
        clamp = f"\nCLAMPED: requested {req[0]:g}-{req[1]:g}s into this {prog:g}s program; placed at {s}-{e}s."
    sound = (f"; sound cues: {', '.join(f'{k}@{t:g}s' for t, k, _g in cues)}" if cues else "")
    depth = " BEHIND the subject" if layer == "behind_subject" else ""
    res = ctx.write_edl(edl, f"motion graphic {template}{depth} at {s}-{e}s [{mid}]{sound}")
    if res.startswith("REJECTED"):
        return res
    return (res + where + clamp + (f"\nNOTE: {'; '.join(notes)}" if notes else "")
            + behind_note)


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
    if hit["template"] != "html":
        hit.pop("html", None)
    err, where, bbox = _probe_report(ctx, edl, hit)
    if err:
        return err
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
    if sfx is True or (sfx is None and owned and (template is not None or params)):
        cues = _sfx_cues(motion_templates.spec(hit["template"]), hit["params"], hit["start"], hit["end"], seed=id)
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
    return (res + where + (f"\nNOTE: {'; '.join(notes)}" if notes else "")
            + (behind_note or ""))


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
        "BIG type placed where the speaker's head/shoulders cross it. Templates that replace "
        "the spoken words set mute_captions; pass "
        "mute_captions explicitly to override. template='html' takes your own HTML/CSS/JS on the "
        "MG runtime in `html`. The write is rejected if the composition errors or draws nothing.",
        {"template": _TEMPLATE_PARAM, "start": {"type": "number"}, "end": {"type": "number"},
         "params": _PARAMS_PARAM, "html": {"type": "string"},
         "layer": {"type": "string", "enum": list(LAYERS)},
         "box": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4,
                 "description": "Optional capture region hint [x0,y0,x1,y1] frame fractions."},
         "mute_captions": {"type": "boolean"}, "sfx": {"type": "boolean"},
         "id": {"type": "string"}, "purpose": {"type": "string"}}),
    "set_motion_graphic": (
        set_motion_graphic,
        "Patch an existing motion graphic by id — window, params (merged into the current ones), "
        "template, layer, html or caption muting. Its owned sound cues move with it; sfx=true "
        "re-derives them, sfx=false removes them. A behind_subject graphic whose window "
        "changes is re-measured against its new footage. Modify instead of removing and "
        "re-adding.",
        {"id": {"type": "string"}, "start": {"type": "number"}, "end": {"type": "number"},
         "params": _PARAMS_PARAM, "html": {"type": "string"}, "template": {"type": "string"},
         "layer": {"type": "string", "enum": list(LAYERS)},
         "box": {"type": "array", "items": {"type": "number"}},
         "mute_captions": {"type": "boolean"}, "sfx": {"type": "boolean"},
         "purpose": {"type": "string"}}),
    "remove_motion_graphic": (
        remove_motion_graphic,
        "Remove one motion graphic by id together with its owned sound cues.",
        {"id": {"type": "string"}}),
    "list_sound_library": (
        list_sound_library,
        "READ: Valmera's sound library — real recordings approved by ear (whooshes, swish, impact, "
        "risers, camera shutters, keyboard typing, clicks, pop, tick, ding, glitches, cash register, "
        "heartbeat) with when to use each and a suggested gain. Place with "
        "add_sfx(storage_key='sound:<id>', at=...). Sound only on meaningful on-screen moments, sparse "
        "(≈ one every 4-5 s at most), never on captions.",
        {"role": {"type": "string", "description": "optional role filter, e.g. whoosh, click, riser"}}),
}
