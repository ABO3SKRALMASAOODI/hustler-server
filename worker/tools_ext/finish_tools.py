"""Department ``compositing``: track 1E's verbs (INTERFACES_W1.md §4).

Grade windows, colour isolation, LUT import, shot match, picture finishes
and the Variant Forge. Every write verb SOLVES its arguments (an 'auto'
hue, a shot-match cube, the Look's brand hue) into apply_edit_batch
operations and commits them through ``tools_ext._write.commit``, so a verb
and a stale MCP client's ``apply_edit_batch`` validate the very same items
(``fx_schemas/finish.py``). Skill: ``skills/finishing.md`` (it lists the
exact batch operations for every device here).

Owner rules: nothing here adds music, a sound or a zoom; a short
luminance-flipping finish counts as a flash (``FinishFx.flash_times``) and
the export's flash gate judges it.
"""

import hashlib
import json
import os

DEPARTMENT = "compositing"

# Pinned verb names (INTERFACES_W1.md §4). Renaming one is a round-lead
# change: 1F's planner, the skills and the refbench agent cases name them.
VERBS = ("set_grade_window", "isolate_colour", "import_lut", "match_grade",
         "add_finish", "make_variant_set", "remove_finish")

FINISH_KINDS = ("lcd", "crt", "led", "halftone", "xerox", "band_tear",
                "film", "thermal", "xray", "blueprint", "line_art",
                "solarise", "posterise", "kaleidoscope", "pixel_stretch",
                "night_vision")
GRADE_FIELDS = ("saturation", "ev", "contrast", "temperature", "tint",
                "split_tone", "protect", "ramp_in_f", "ramp_out_f", "region")
LUT_MAX_BYTES = 16 * 1024 * 1024
# shot match: frames sampled from the section and around the hero frame
MATCH_FRAMES, HERO_FRAMES = 5, 3
MATCH_LIMIT_LAB = 40.0


# ── shared helpers ──────────────────────────────────────────────────────

def _edl(ctx):
    row = ctx.latest_edl()
    return row["json"], row["version"]


def _program_s(edl):
    import schemas
    try:
        return float(schemas.program_duration(edl) or 0.0)
    except Exception:  # noqa: BLE001
        return 0.0


def _num(v, name):
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number of seconds")


def _window(edl, start, end):
    """[start, end) program seconds, defaulting to the whole program."""
    prog = _program_s(edl)
    s = 0.0 if start is None else _num(start, "start")
    e = prog if end is None else _num(end, "end")
    s, e = round(max(0.0, s), 2), round(min(prog, e), 2)
    if e - s < 0.01:
        raise ValueError(f"the window {s:g}-{e:g}s is empty (the program "
                         f"is {prog:.2f}s)")
    return s, e


def _fps(ctx):
    try:
        return float(((ctx.index or {}).get("video") or {}).get("fps")
                     or 30.0)
    except (TypeError, ValueError, AttributeError):
        return 30.0


def _render_rates(fps):
    """The frame rates this edit renders at: the final's (the source rate,
    at most 60) first, then the preview's (capped by PREVIEW_MAX_FPS)."""
    final = max(1.0, min(float(fps or 30.0), 60.0))
    try:
        import config
        cap = float(getattr(config, "PREVIEW_MAX_FPS", 30.0) or 0.0)
    except Exception:  # noqa: BLE001
        cap = 30.0
    preview = min(final, cap) if cap else final
    return (final,) if abs(preview - final) < 1e-6 else (final, preview)


def _cut_time(cut, rates):
    """The centisecond an fx edge is stored at so it lands ON the cut's
    first frame (renderer.first_frame_at, ceil) where an fx window's frame
    is round(t * fps): on the final's rate, and on the preview's too
    whenever one centisecond serves both. (Rounding the frame time UP to
    the centisecond, as before, overshot by up to 0.6 frame at 59.94 fps:
    a fifth of cuts started a frame late, and 30 fps previews of 50 / 59.94
    sources missed by a frame on a third.)"""
    import math
    import fxprims
    import renderer
    want = [(f, renderer.first_frame_at(cut, f)) for f in rates]
    k0 = want[0][1] / fxprims.fps3(want[0][0])
    best = None
    for c in range(int(math.floor(cut * 100)) - 4,
                   int(math.ceil(cut * 100)) + 8):
        t = c / 100.0
        hits = [fxprims.frame_at(t, f) == k for f, k in want]
        score = (hits[0], sum(hits), -abs(t - k0))
        if best is None or score > best[0]:
            best = (score, t)
    return best[1]


def _snap_to_cuts(edl, s, e, fps):
    """Edges within 1.5 frames of a cut land ON the cut's first frame.

    The render starts each block on first_frame_at(T) (ceil), while an fx
    window's frames are round(t * fps): a section written at the cut's
    exact program second (7.99 at 29.97 -> frame 239.46) would start a
    frame BEFORE the shot changes. Snapped (``_cut_time``), it starts on
    it, on the final and the preview alike."""
    try:
        from timeline import Timeline
        tl = Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                      edl.get("speed") or [])
        cuts = [float(o) for o in tl.offsets[1:]]
    except Exception:  # noqa: BLE001 — snapping is a refinement
        return s, e
    rates = _render_rates(fps)

    def snap(t):
        for c in cuts:
            if abs(t - c) * rates[0] <= 1.5:
                try:
                    return _cut_time(c, rates)
                except Exception:  # noqa: BLE001
                    return t
        return t
    return round(snap(s), 2), round(snap(e), 2)


def _obj(v, name):
    if v is None or isinstance(v, (dict, list)):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            raise ValueError(f"{name} must be an object")
    raise ValueError(f"{name} must be an object")


def _new_id(edl, prefix):
    used = {str(i.get("id")) for i in edl.get("fx") or []
            if isinstance(i, dict)}
    n = 1
    while f"{prefix}{n}" in used:
        n += 1
    return f"{prefix}{n}"


def _accent(edl):
    """The Look's accent (the captions' highlight colour), the brand hue,
    or None."""
    cap = edl.get("captions")
    style = (cap.get("style") or {}) if isinstance(cap, dict) else {}
    c = str(style.get("highlight_color") or "").strip().lower()
    if len(c) == 7 and c.startswith("#"):
        return c
    return None


def _unavailable(fx_type):
    """'UNAVAILABLE ...' when the render service answers and cannot draw
    this fx type; '' otherwise (unknown is not "no")."""
    try:
        import remote
        if remote.executor_supports(f"fx:{fx_type}") is False:
            return (f"UNAVAILABLE: the render service does not draw "
                    f"{fx_type} yet (a deploy is rolling out); nothing was "
                    "written.")
    except Exception:  # noqa: BLE001
        pass
    return ""


def _commit(ctx, ops, summary):
    from tools_ext import _write
    return _write.commit(ctx, ops, summary)


def _upsert(item):
    from tools_ext import _write
    return _write.upsert_fx({k: v for k, v in item.items()
                             if v is not None})


def _rejected(msg):
    return f"REJECTED: {msg}"


# ── set_grade_window ────────────────────────────────────────────────────

def set_grade_window(ctx, start=None, end=None, saturation=None, ev=None,
                     contrast=None, temperature=None, tint=None,
                     split_tone=None, protect=None, ramp_in_f=None,
                     ramp_out_f=None, region=None, stage=None, id=None):
    edl, _v = _edl(ctx)
    try:
        s, e = _window(edl, start, end)
        s, e = _snap_to_cuts(edl, s, e, _fps(ctx))
        split_tone = _obj(split_tone, "split_tone")
        region = _obj(region, "region")
    except ValueError as x:
        return _rejected(str(x))
    if isinstance(protect, str):
        protect = [p.strip() for p in protect.split(",") if p.strip()]
    contrast, c_note = _contrast_factor(contrast)
    acc = _accent(edl)
    if isinstance(split_tone, dict) and acc:
        # 'accent' is the Look's accent: solved now, stored as its hex
        split_tone = {k: (acc if v == "accent" else v)
                      for k, v in split_tone.items()}
    item = {"id": id or _new_id(edl, "gw"), "type": "grade_window",
            "stage": stage or "world", "start": s, "end": e,
            "saturation": saturation, "ev": ev, "contrast": contrast,
            "temperature": temperature, "tint": tint,
            "split_tone": split_tone, "protect": protect or None,
            "ramp_in_f": ramp_in_f or 0, "ramp_out_f": ramp_out_f or 0,
            "region": region,
            "device": "grade_split_tone" if split_tone else "grade_section"}
    if protect and "brand" in protect:
        item["brand_hue"] = _accent(edl)
    gone = _unavailable("grade_window")
    if gone:
        return gone
    return _commit(ctx, [_upsert(item)], f"grade window {item['id']} "
                   f"{s:g}-{e:g}s") + c_note


# Contrast is a FACTOR (1.0 leaves it; the schema floor is 0.25). Earnings
# run (w1023): contrast 0.1 (4566 job 96714, 4570 job 98438) and -0.08
# (4578 job 96468) were refused by the schema — a change written as a delta
# (+10%, -8%). A value under the floor can only mean that: read as 1 + it.
CONTRAST_FLOOR = 0.25


def _contrast_factor(v):
    try:
        f = float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return v, ""
    if f is None or f >= CONTRAST_FLOOR or not -0.75 < f:
        return v, ""
    g = round(1.0 + f, 3)
    return g, (f"\nNOTE: contrast is a factor (1.0 = unchanged): {f:g} was "
               f"read as a change of {f:+g}, contrast {g:g}.")


# ── isolate_colour ──────────────────────────────────────────────────────

def isolate_colour(ctx, hue="auto", keep="hue", start=None, end=None,
                   tolerance=None, softness=None, keep_skin=False):
    edl, _v = _edl(ctx)
    try:
        s, e = _window(edl, start, end)
        s, e = _snap_to_cuts(edl, s, e, _fps(ctx))
    except ValueError as x:
        return _rejected(str(x))
    keep = (keep or "hue").strip().lower()
    if keep not in ("hue", "subject", "both"):
        return _rejected("keep is hue, subject or both")
    h = str(hue or "auto").strip().lower()
    solved = ""
    if keep in ("hue", "both") and (h == "auto" or tolerance is None):
        # the hue (when auto) and the key's width, solved from this window
        import gradewin
        got, tol, soft = gradewin.auto_key(ctx, s, e,
                                           None if h == "auto" else h)
        if h == "auto":
            if not got:
                return _rejected("no clear colour to keep in that window; "
                                 "name the hue as #rrggbb")
            h = got
            solved = f" (auto: {h}"
        if tolerance is None and tol is not None:
            tolerance, softness = tol, (softness if softness is not None
                                        else soft)
            solved += (", " if solved else " (") + \
                f"key +-{tolerance * 180:.0f} deg"
        solved += ")" if solved else ""
    import schemas
    if keep in ("subject", "both"):
        # keeping the SUBJECT is track 1B's subject isolate
        # (INTERFACES_W1 §3.7); written only where that type exists
        if "subject" not in schemas.FX_TYPES:
            return ("UNAVAILABLE: keeping the subject in colour needs the "
                    "subject effects, which this server does not have yet; "
                    "isolate_colour(keep='hue') keeps a colour now.")
        item = {"id": _new_id(edl, "sx"), "type": "subject",
                "stage": "world", "start": s, "end": e, "kind": "isolate",
                "hue": None if keep == "subject" else h,
                "hue_op": None if keep == "subject" else "union"}
        return _commit(ctx, [_upsert(item)], f"subject kept in colour "
                       f"{s:g}-{e:g}s{solved}")
    iso = {"hue": h}
    if tolerance is not None:
        iso["tolerance"] = tolerance
    if softness is not None:
        iso["softness"] = softness
    item = {"id": _new_id(edl, "gw"), "type": "grade_window",
            "stage": "world", "start": s, "end": e, "isolate": iso,
            "protect": ["skin"] if keep_skin else None,
            "device": "grade_isolate_hue"}
    gone = _unavailable("grade_window")
    if gone:
        return gone
    return _commit(ctx, [_upsert(item)], f"kept {h} in colour, the rest "
                   f"mono, {s:g}-{e:g}s{solved}")


# ── LUT assets ──────────────────────────────────────────────────────────

def _store_lut(ctx, text, origin, meta=None):
    """Upload canonical .cube text as a project asset; returns its key.
    LUTs are 'sheet' assets (internal artefacts, never in the media tray;
    assets.kind has no 'lut' and Wave 1 applies no migration)."""
    import db as dbx
    import storage
    sha = hashlib.sha256(text.encode()).hexdigest()
    key = f"generated/{ctx.project_id}/lut-{sha[:16]}.cube"
    path = os.path.join(ctx.workdir, f"lut-{sha[:16]}.cube")
    with open(path, "w") as f:
        f.write(text)
    storage.upload_file(path, key, "text/plain")
    try:
        import edit_batch
        if key in (ctx.db.run(edit_batch.project_media_keys,
                              ctx.project_id) or ()):
            return key            # the same cube again: one asset row
    except Exception:  # noqa: BLE001
        pass
    ctx.db.run(dbx.insert_asset, ctx.project_id, "sheet", key,
               bytes_=os.path.getsize(path), sha256=sha,
               meta=dict({"filename": os.path.basename(key), "lut": True,
                          "origin": origin, "generated": True},
                         **(meta or {})))
    return key


def _read_lut_source(ctx, asset_key=None, url=None):
    """(lutlib.Lut, raw text, source label) from a project asset or a URL;
    ValueError with the reason."""
    import lutlib
    local = os.path.join(ctx.workdir, "import_" + hashlib.sha256(
        str(asset_key or url).encode()).hexdigest()[:12] + ".cube")
    if asset_key:
        import edit_batch
        import storage
        keys = ctx.db.run(edit_batch.project_media_keys, ctx.project_id)
        if asset_key not in keys:
            raise ValueError("that asset is not in this project")
        size = storage.object_bytes(asset_key)
        if size is not None and size > LUT_MAX_BYTES:
            # a video named by mistake: refused before a byte is fetched
            raise ValueError("that asset is larger than 16 MB, so it is not "
                             "a .cube LUT")
        storage.download_to(asset_key, local)
        src = asset_key
    elif url:
        import net_fetch
        try:
            net_fetch.download(url, local, max_bytes=LUT_MAX_BYTES,
                               timeout_s=30)
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"could not fetch the LUT ({str(e)[:160]})")
        src = url
    else:
        raise ValueError("give asset_key (a .cube in this project) or url")
    with open(local, "rb") as f:
        raw = f.read(LUT_MAX_BYTES + 1)
    if len(raw) > LUT_MAX_BYTES:
        raise ValueError("the LUT file is larger than 16 MB")
    try:
        lut = lutlib.parse_cube(raw)
    except lutlib.LutError as e:
        raise ValueError(f"not a usable .cube: {e}")
    return lut, src


def import_lut(ctx, asset_key=None, url=None, start=None, end=None,
               strength=1.0):
    import lutlib
    edl, _v = _edl(ctx)
    try:
        s, e = _window(edl, start, end)
        s, e = _snap_to_cuts(edl, s, e, _fps(ctx))
        lut, src = _read_lut_source(ctx, asset_key, url)
    except ValueError as x:
        return _rejected(str(x))
    text = lutlib.cube_text(lut, title=lut.title or "imported")
    key = _store_lut(ctx, text, "import", {"source": str(src)[:300],
                                           "size": lut.size})
    item = {"id": _new_id(edl, "gw"), "type": "grade_window",
            "stage": "world", "start": s, "end": e,
            "lut": {"asset_key": key, "strength": float(strength
                                                       if strength is not None
                                                       else 1.0),
                    "origin": "import"},
            "device": "grade_lut_import"}
    gone = _unavailable("grade_window")
    if gone:
        return gone
    return _commit(ctx, [_upsert(item)], f"LUT {lut.size}^3 "
                   f"({lut.source_kind}) applied {s:g}-{e:g}s")


# ── match_grade ─────────────────────────────────────────────────────────

def _frames(ctx, edl, times):
    import numpy as np
    from PIL import Image
    import media
    from timeline import Timeline
    tl = Timeline(edl.get("keep") or [[0, ctx.duration]],
                  edl.get("inserts") or [], edl.get("speed") or [])
    out = []
    proxy = ctx.proxy_path()
    for i, t in enumerate(times):
        src = tl.out_to_src(t)
        if src is None:
            continue
        dst = os.path.join(ctx.workdir, f"match_{i}_{t:.2f}.jpg")
        media.frame_at(proxy, src, dst)
        out.append(np.asarray(Image.open(dst).convert("RGB")))
    return out


def match_grade(ctx, hero_t, start=None, end=None, strength=1.0,
                method="mkl"):
    import numpy as np
    import lutlib
    edl, _v = _edl(ctx)
    if not getattr(ctx, "has_main_video", False):
        return _rejected("shot match needs the main video")
    try:
        s, e = _window(edl, start, end)
        # a matched section is a shot: its edges land ON the cut frames,
        # never a frame of the neighbouring shot in the matched colour
        s, e = _snap_to_cuts(edl, s, e, _fps(ctx))
        h = _num(hero_t, "hero_t")
    except ValueError as x:
        return _rejected(str(x))
    if s <= h < e:
        return _rejected("hero_t is the look to match, so it must sit "
                         "outside the section being matched")
    if method not in ("mkl", "reinhard"):
        return _rejected("method is mkl or reinhard")
    prog = _program_s(edl)
    span = e - s
    tgt = _frames(ctx, edl, [s + span * (k + 0.5) / MATCH_FRAMES
                             for k in range(MATCH_FRAMES)])
    lo, hi = _shot_of(edl, h, prog)
    hero = _frames(ctx, edl, [min(hi - 0.01, max(lo, h + d))
                              for d in (-0.2, 0.0, 0.2)][:HERO_FRAMES])
    if not tgt or not hero:
        return _rejected("could not read frames to match")
    src_px = np.concatenate([f.reshape(-1, 3) for f in tgt])
    ref_px = np.concatenate([f.reshape(-1, 3) for f in hero])
    lut = lutlib.match_lut(src_px, ref_px, method=method,
                           limit=MATCH_LIMIT_LAB)
    before = _mean_lab_gap(src_px, ref_px)
    after = _mean_lab_gap(lutlib.apply(lut, src_px / 255.0) * 255.0, ref_px)
    text = lutlib.cube_text(lut, title=f"match {method} to {h:.2f}s")
    key = _store_lut(ctx, text, "match", {"hero_t": h, "method": method})
    item = {"id": _new_id(edl, "gw"), "type": "grade_window",
            "stage": "world", "start": s, "end": e,
            "lut": {"asset_key": key, "strength": float(strength
                                                       if strength is not None
                                                       else 1.0),
                    "origin": "match", "match_t": round(h, 2)},
            "device": "grade_shot_match"}
    gone = _unavailable("grade_window")
    if gone:
        return gone
    return _commit(ctx, [_upsert(item)], f"matched {s:g}-{e:g}s to the "
                   f"frame at {h:g}s ({method}; mean colour gap dE "
                   f"{before:.1f} -> {after:.1f})")


def _shot_of(edl, t, prog):
    """[a, b) program seconds of the shot holding ``t`` (cut to cut): the
    hero's samples never reach into the next camera."""
    try:
        from timeline import Timeline
        tl = Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                      edl.get("speed") or [])
        edges = sorted({0.0, float(prog)} |
                       {float(o) for o in tl.offsets[1:]})
    except Exception:  # noqa: BLE001
        return 0.0, float(prog)
    a = max([x for x in edges if x <= t] or [0.0])
    b = min([x for x in edges if x > t] or [float(prog)])
    return a, b


def _mean_lab_gap(a, b):
    import numpy as np
    import lutlib
    la = lutlib.rgb_to_lab(np.asarray(a, float).reshape(-1, 3) / 255.0)
    lb = lutlib.rgb_to_lab(np.asarray(b, float).reshape(-1, 3) / 255.0)
    return float(lutlib.delta_e2000(la.mean(axis=0), lb.mean(axis=0)))


# ── add_finish ──────────────────────────────────────────────────────────

def add_finish(ctx, kind, start=None, end=None, strength=None, scale=None,
               palette=None, matte_weighted=False, region=None, stage=None,
               origin=None, direction=None, ramp_in_f=None, ramp_out_f=None,
               id=None):
    edl, _v = _edl(ctx)
    kind = str(kind or "").strip().lower()
    if kind not in FINISH_KINDS:
        return _rejected("kind is one of " + ", ".join(FINISH_KINDS))
    try:
        s, e = _window(edl, start, end)
        s, e = _snap_to_cuts(edl, s, e, _fps(ctx))
        region = _obj(region, "region")
    except ValueError as x:
        return _rejected(str(x))
    item = {"id": id or _new_id(edl, f"fi_{kind}_"), "type": "finish",
            "stage": stage or "pre_captions", "start": s, "end": e,
            "kind": kind, "strength": strength, "scale": scale,
            "palette": palette, "matte_weighted": bool(matte_weighted),
            "region": region, "origin": origin, "direction": direction,
            "ramp_in_f": ramp_in_f or 0, "ramp_out_f": ramp_out_f or 0,
            "device": f"finish_{kind}"}
    gone = _unavailable("finish")
    if gone:
        return gone
    return _commit(ctx, [_upsert(item)], f"{kind} finish {item['id']} "
                   f"{s:g}-{e:g}s")


# ── make_variant_set ────────────────────────────────────────────────────

def make_variant_set(ctx, t, kinds=None):
    import variants
    edl, _v = _edl(ctx)
    if not getattr(ctx, "has_main_video", False):
        return _rejected("variants are made from the main video")
    try:
        pt = _num(t, "t")
        ks = variants.check_kinds(kinds)
    except ValueError as x:
        return _rejected(str(x))
    from timeline import Timeline
    tl = Timeline(edl.get("keep") or [[0, ctx.duration]],
                  edl.get("inserts") or [], edl.get("speed") or [])
    src = tl.out_to_src(min(max(0.0, pt), max(0.0, _program_s(edl) - 0.01)))
    if src is None:
        return _rejected(f"{pt:g}s is not on the main video (an insert?)")
    try:
        keys = variants.make_variant_set(ctx, src, ks)
    except ValueError as x:
        return _rejected(str(x))
    except Exception as x:  # noqa: BLE001
        return f"FAILED: could not make the variants ({str(x)[:200]})"
    st = getattr(ctx, "_last_variant_stats", {}) or {}
    lines = [f"Made {len(keys)} registered variant stills of the frame at "
             f"{pt:g}s (source {src:.2f}s, {st.get('origin', 'proxy')} "
             f"frame, {st.get('W')}x{st.get('H')}):"]
    lines += [f"  {k}: {key}" for k, key in zip(ks, keys)]
    lines.append("Project images, registered pixel for pixel: a strobe "
                 "or a fill names them by asset_key. Nothing was placed.")
    return "\n".join(lines)


# ── remove_finish ───────────────────────────────────────────────────────

def remove_finish(ctx, ids):
    from tools_ext import _write
    edl, _v = _edl(ctx)
    if isinstance(ids, str):
        ids = [i.strip() for i in ids.split(",") if i.strip()]
    ids = list(dict.fromkeys(str(i) for i in ids or []))
    if not ids:
        return _rejected("name the ids to remove")
    have = {str(i.get("id")): i.get("type") for i in edl.get("fx") or []
            if isinstance(i, dict)}
    bad = [i for i in ids if have.get(i) not in ("grade_window", "finish")]
    if bad:
        return _rejected(f"not a grade window or finish here: "
                         f"{', '.join(bad)}")
    return _commit(ctx, [_write.remove_fx(i) for i in ids],
                   f"removed {', '.join(ids)}")


# ── registration ────────────────────────────────────────────────────────

_T = {"type": "number"}
_S = {"type": "number", "description": "program s"}
_RAMP = {"type": "integer", "description": "frames 0-48"}
_REGION = {"type": "object", "description": "FxRegion: {shape: rect|"
           "ellipse|polygon|glyph|face|card, box|points|ref}"}

TOOL_SPECS = {
    "set_grade_window": (
        set_grade_window,
        "Grade a section of the program [start, end): saturation (0 = "
        "mono), ev, contrast, temperature, tint, split_tone {shadows, "
        "highlights, balance}, protect [skin, brand] (those colours keep "
        "their source chroma). ramp_in_f / ramp_out_f ease it over whole "
        "frames: mono until a drop that lands IN colour = end on the drop "
        "frame with ramp_out_f 3. Optional region (FxRegion). Pass id to "
        "replace one. Bit-exact outside the window.",
        {"start": _S, "end": _S, "saturation": _T, "ev": _T, "contrast": _T,
         "temperature": _T, "tint": _T,
         "split_tone": {"type": "object"},
         "protect": {"type": "array", "items": {"type": "string",
                                                "enum": ["skin", "brand"]}},
         "ramp_in_f": _RAMP, "ramp_out_f": _RAMP, "region": _REGION,
         "stage": {"type": "string", "enum": ["world", "screen"]},
         "id": {"type": "string"}}),
    "isolate_colour": (
        isolate_colour,
        "Keep one colour and send the rest of the picture to mono. "
        "keep='hue' keeps that hue everywhere (hue 'auto' = the subject's "
        "dominant colour, solved now; or #rrggbb); keep='subject' keeps the "
        "person in colour and 'both' the person plus the hue (subject "
        "effects). keep_skin keeps faces natural. Window defaults to the "
        "whole program.",
        {"hue": {"type": "string"}, "keep": {"type": "string",
                                             "enum": ["hue", "subject",
                                                      "both"]},
         "start": _S, "end": _S, "tolerance": _T, "softness": _T,
         "keep_skin": {"type": "boolean"}}),
    "import_lut": (
        import_lut,
        "Apply a .cube LUT (up to 65^3, validated) from a project asset "
        "or a URL over [start, end) at strength 0-1.",
        {"asset_key": {"type": "string"}, "url": {"type": "string"},
         "start": _S, "end": _S, "strength": _T}),
    "match_grade": (
        match_grade,
        "Match the colour of section [start, end) to the hero frame at "
        "hero_t (outside it): a shot-match LUT (mkl or reinhard) baked to "
        "33^3. Reports the colour gap before and after.",
        {"hero_t": _S, "start": _S, "end": _S, "strength": _T,
         "method": {"type": "string", "enum": ["mkl", "reinhard"]}}),
    "add_finish": (
        add_finish,
        "A picture finish over [start, end): lcd/crt/led re-photography, "
        "halftone, xerox, band_tear, film, or a live variant (thermal, "
        "xray, blueprint, line_art, solarise, posterise, kaleidoscope, "
        "pixel_stretch, night_vision). Variants are metaphors (mechanism, "
        "inside the body, data), never decoration. matte_weighted makes "
        "the person hot (thermal) or clips the X-ray to them. stage "
        "over_type finishes the type too. Short luminance flips count as "
        "flashes.",
        {"kind": {"type": "string", "enum": list(FINISH_KINDS)},
         "start": _S, "end": _S, "strength": _T, "scale": _T,
         "palette": {"type": "string"}, "matte_weighted": {"type":
                                                           "boolean"},
         "region": _REGION,
         "stage": {"type": "string", "enum": ["world", "screen",
                                              "pre_captions", "over_type"]},
         "origin": _T, "direction": {"type": "string",
                                     "enum": ["left", "right", "up",
                                              "down"]},
         "ramp_in_f": _RAMP, "ramp_out_f": _RAMP, "id": {"type": "string"}}),
    "make_variant_set": (
        make_variant_set,
        "Registered variant STILLS of the main video's frame at program "
        "second t (default photo, negative, line, blueprint, thermal; also "
        "xray, solarise, posterise, kaleidoscope, pixel_stretch, "
        "night_vision, halftone; up to 6) as project images for a strobe "
        "or a fill. Places nothing.",
        {"t": _S, "kinds": {"type": "array", "items": {"type": "string"}}}),
    "remove_finish": (
        remove_finish,
        "Remove grade windows and finishes by id.",
        {"ids": {"type": "array", "items": {"type": "string"}}}),
}

REQUIRED_ARGS = {"add_finish": ["kind"], "match_grade": ["hero_t"],
                 "make_variant_set": ["t"], "remove_finish": ["ids"]}
WRITE_TOOLS = {"set_grade_window", "isolate_colour", "import_lut",
               "match_grade", "add_finish", "remove_finish"}
COMPACT = {
    "set_grade_window": "Grade [start,end): saturation 0=mono, ev, "
                        "contrast, temperature, tint, split_tone, protect "
                        "[skin,brand]; ramp_out_f 3 + end on a drop = "
                        "colour lands on it.",
    "isolate_colour": "Keep one colour, rest mono: keep hue|subject|both, "
                      "hue auto|#rrggbb.",
    "add_finish": "Finish [start,end): lcd crt led halftone xerox "
                  "band_tear film thermal xray blueprint line_art solarise "
                  "posterise kaleidoscope pixel_stretch night_vision. "
                  "Metaphor, not decoration.",
    "make_variant_set": "Registered variant stills of the frame at t "
                        "(photo negative line blueprint thermal ...) for "
                        "strobes; places nothing.",
}
