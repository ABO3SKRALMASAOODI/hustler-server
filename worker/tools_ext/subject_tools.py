"""Department ``compositing``: track 1B's verbs (INTERFACES_W1.md §4).

``add_subject_effect``, ``set_background`` and ``remove_subject_effect``
SOLVE their arguments into apply_edit_batch operations on the ``fx`` layer
and commit them through ``tools_ext._write.commit`` (edit_batch.apply_batch),
so a verb and a stale MCP client's apply_edit_batch validate the same items
identically: every authoring rule lives in ``fx_schemas/subject.py``.

What a verb adds over a batch: moments (``on=stress|word|phrase_end|cut|
beat|downbeat|onset``) resolved on the speech prosody grid or the beat grid
of a track the USER supplied, written with their ``bind``; flash safety on
automatic picks; the device card id; the person-matte sidecar requested
(never waited for); colour roles kept as roles. Its skill is
``skills/subject-fx.md`` (verbs AND the exact apply_edit_batch operations).

Browserless lanes: nothing here renders, opens a browser or runs a model.
"""

import math

DEPARTMENT = "compositing"

# Pinned verb names (INTERFACES_W1.md §4). Renaming one is a round-lead
# change: 1F's planner, the skills and the refbench agent cases name them.
VERBS = ("add_subject_effect", "set_background", "remove_subject_effect")

# Device card per kind (devices/cards/<id>.yaml).
SUBJECT_CARDS = {"fill": "subject_fill_flash", "poster": "subject_poster",
                 "glow": "subject_glow", "outline": "subject_outline",
                 "negate": "subject_negate", "exposure": "subject_exposure",
                 "isolate": "subject_isolate", "echo": "subject_echo"}
BACKGROUND_CARDS = {"hold": "background_subject_hold",
                    "strobe": "background_strobe",
                    "blur": "background_blur",
                    "swap": "background_swap"}
# Moments a verb can solve, and the clock each reads.
SPEECH_MOMENTS = ("stress", "word", "phrase_end")
MUSIC_MOMENTS = ("beat", "downbeat", "onset")
MOMENTS = SPEECH_MOMENTS + ("cut",) + MUSIC_MOMENTS
# Frames a moment-bound device lasts when ``frames`` is not given.
DEFAULT_FRAMES = {"fill": 1, "negate": 2, "poster": 4, "exposure": 4,
                  "glow": 8, "outline": 8, "isolate": 12, "echo": 12}
DEFAULT_FILL_DECAY_F = 2
# Automatic picks: how many moments, how far apart (seconds) at least.
DEFAULT_COUNT = 3
MIN_GAP_S = {"fill": 1.0, "negate": 1.0, "poster": 1.5}
DEFAULT_MIN_GAP_S = 1.5
MAX_ITEMS = 24


# ------------------------------------------------------------------ helpers
def _num(v, what):
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be a number of seconds")
    if not math.isfinite(x):
        raise ValueError(f"{what} must be finite")
    return x


def _fps(ctx):
    try:
        return float(((ctx.index or {}).get("video") or {}).get("fps")
                     or 30.0)
    except (TypeError, ValueError):
        return 30.0


def _timeline(edl):
    from timeline import Timeline
    return Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                    edl.get("speed") or [])


def _program_s(edl, tl):
    try:
        from schemas import program_duration
        return float(program_duration(edl) or tl.out_duration)
    except Exception:  # noqa: BLE001
        return float(tl.out_duration)


def _frame(t, fps):
    return int(math.floor(float(t) * round(fps, 3) + 0.5 + 1e-9))


def _t(frame, fps):
    return round(frame / round(fps, 3), 2)


def _span(frame, n, fps):
    """(start, end) of n frames from program frame ``frame`` (end
    exclusive, centiseconds: INTERFACES.md §4.1)."""
    a = _t(frame, fps)
    b = _t(frame + max(1, int(n)), fps)
    if b <= a:
        b = round(a + 0.01, 2)
    return a, b


def _next_ids(edl, prefix, n):
    used = {str(i.get("id")) for i in edl.get("fx") or []
            if isinstance(i, dict)}
    out, k = [], 1
    while len(out) < n:
        cand = f"{prefix}{k}"
        if cand not in used:
            out.append(cand)
            used.add(cand)
        k += 1
    return out


def _source(value):
    """An FxSource from a dict or a shorthand: a media key, a SOURCE
    second, 'next' / 'previous', or a colour ('#rrggbb' / white / black /
    accent)."""
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {"kind": "source", "src_t": float(value)}
    v = str(value or "").strip()
    if v in ("next", "previous", "program"):
        return {"kind": v}
    if v.startswith("#") or v in ("white", "black", "accent"):
        return {"kind": "colour", "colour": v}
    if v:
        return {"kind": "asset", "asset_key": v}
    raise ValueError("a source is a media key, a source second, next, "
                     "previous, a colour or an FxSource object")


# ------------------------------------------------------------------ moments
def _speech_moments(ctx, tl, on):
    """[(program t, bind ref, strength)] for word / stress / phrase_end."""
    import prosody_grid
    words = (ctx.index or {}).get("words") or []
    if not words:
        return []
    perc = None
    try:
        import agent_tools
        perc = agent_tools._get_perception(ctx)
    except Exception:  # noqa: BLE001 — duration scores stand in
        perc = None
    rmap = prosody_grid.build(words, perc)
    by_t0 = {}
    for i, w in enumerate(words):
        try:
            by_t0.setdefault(round(float(w["t0"]), 4), i)
        except (KeyError, TypeError, ValueError):
            continue
    out = []
    if on == "phrase_end":
        by_t1 = {}
        for i, w in enumerate(words):
            try:
                by_t1.setdefault(round(float(w["t1"]), 4), i)
            except (KeyError, TypeError, ValueError):
                continue
        for o in rmap.get("onsets") or []:
            if o["cls"] != "phrase_end":
                continue
            i = by_t1.get(round(float(o["t"]), 4))
            t = tl.src_to_out(float(o["t"]) - 1e-4)
            if i is not None and t is not None:
                out.append((t + 1e-4, i, float(o.get("strength") or 0.5)))
        return out
    cls = "stress" if on == "stress" else "word"
    for o in rmap.get("onsets") or []:
        if o["cls"] != cls:
            continue
        i = by_t0.get(round(float(o["t"]), 4))
        t = tl.src_to_out(float(o["t"]))
        if i is not None and t is not None:
            out.append((t, i, float(o.get("strength") or 0.5)))
    return out


def _cut_moments(ctx, edl, tl):
    """[(program t, source second of the incoming shot, 1.0)]: joins
    between kept spans and shot changes inside them (index shots)."""
    out, seen = [], set()
    segs = list(getattr(tl, "segs", []) or [])
    for i, off in enumerate(list(getattr(tl, "offsets", []) or [])[1:], 1):
        if i < len(segs):
            key = round(off, 2)
            if key not in seen:
                seen.add(key)
                out.append((float(off), round(segs[i][0], 3), 1.0))
    for s in (ctx.index or {}).get("shots") or []:
        try:
            src = float(s.get("start") if isinstance(s, dict) else s)
        except (TypeError, ValueError):
            continue
        if src <= 0:
            continue
        t = tl.src_to_out(src)
        if t is None or t <= 0:
            continue
        key = round(t, 2)
        if key not in seen:
            seen.add(key)
            out.append((t, round(src, 3), 1.0))
    return sorted(out)


def _music_moments(ctx, edl, tl, on):
    """[(program t, list index, strength)] on the beat grid of the first
    music item the USER supplied. Never adds music: no track, no clock."""
    import perception
    import rhythm_types
    items = [m for m in edl.get("music") or []
             if isinstance(m, dict) and not m.get("mute")
             and m.get("storage_key")]
    if not items:
        raise ValueError(f"on={on} reads the beat of a track you added; "
                         "this edit has no music (use on=stress, word, "
                         "phrase_end or cut: the speech clock)")
    item = items[0]
    cache = getattr(ctx, "_asset_perception", None)
    if cache is None:
        cache = {}
        setattr(ctx, "_asset_perception", cache)
    p = cache.get(item["storage_key"])
    if p is None:
        import agent_tools
        import db as dbx
        asset = ctx.db.run(dbx.asset_by_key, ctx.project_id,
                           item["storage_key"])
        if not asset:
            raise ValueError("the music in this edit is not a project asset "
                             "any more")
        p = perception.get_or_compute_for_asset(
            ctx.db, dbx, asset, agent_tools._asset_local_path(ctx, asset))
        cache[item["storage_key"]] = p
    rmap = rhythm_types.from_beat_grid(p, ref={"music_id": item.get("id")})
    prog = rhythm_types.to_program(rmap, tl, music_item=item,
                                   track_dur=p.get("duration"))
    if on == "onset":
        return [(o["t"], i, float(o.get("strength") or 0.5))
                for i, o in enumerate(prog.get("onsets") or [])], item
    seq = prog.get("beats" if on == "beat" else "downbeats") or []
    return [(t, i, 1.0) for i, t in enumerate(seq)], item


def _pick(moments, lo, hi, count, min_gap):
    """The strongest ``count`` moments inside [lo, hi), at least
    ``min_gap`` apart (stronger first, earlier on ties), in time order."""
    inside = [m for m in moments if lo - 1e-6 <= m[0] < hi - 1e-6]
    order = sorted(inside, key=lambda m: (-m[2], m[0]))
    kept = []
    for m in order:
        if all(abs(m[0] - k[0]) >= min_gap - 1e-6 for k in kept):
            kept.append(m)
        if count and len(kept) >= count:
            break
    return sorted(kept)


# ------------------------------------------------------------------ gates
def _gate(ctx, fx_type):
    """REJECTED text when this edit cannot carry the type, else None."""
    if not getattr(ctx, "has_main_video", False):
        return ("REJECTED: subject effects need footage with a person; this "
                "program has no main video.")
    try:
        import remote
        ok = remote.executor_supports(f"fx:{fx_type}")
    except Exception:  # noqa: BLE001 — unknown is not "no"
        ok = None
    if ok is False:
        return (f"REJECTED: the render service does not draw {fx_type} "
                "effects yet (it is older than this editor). Nothing was "
                "written; try again after the next release.")
    return None


def _ensure_matte(ctx, edl, tl, windows):
    """Ask for the person-matte sidecar over the items' source windows
    (never waits). Returns a short note for the reply, or ''."""
    try:
        import db as dbx
        import fxgraph
        import sidecars
        original = ctx.db.run(dbx.latest_asset, ctx.project_id, "original")
        proxy = ctx.db.run(dbx.latest_asset, ctx.project_id, "proxy")
        if not original or not proxy or not original.get("sha256"):
            return ""
        # Track 1A's program matte keys and launches its chunks: when it is
        # here, ask through its own helpers, so this request warms exactly
        # the chunks a render reads (its matte.person params) and launches
        # only where a lane may (never model_missing markers from a lane
        # without the models).
        launch = fxgraph.peer_fn("matte_track:launch_source")
        params = None
        if launch is not None:
            src = launch(original["sha256"], proxy, ctx.index, ctx.project_id)
            if src is None:
                return ""
            params = fxgraph.peer("matte_track:sidecar_params", default=None)
        else:
            video = (ctx.index or {}).get("video") or {}
            meta = proxy.get("meta") or {}
            # as sidecars.prefetch_top_shorts builds it: the index's
            # duration (the chunk grid), the proxy's own geometry
            info = {"duration": video.get("duration")
                    or proxy.get("duration_s"),
                    "fps": proxy.get("fps") or meta.get("fps")
                    or video.get("fps"),
                    "width": proxy.get("width") or meta.get("width"),
                    "height": proxy.get("height") or meta.get("height")}
            src = sidecars.source_from(original["sha256"],
                                       proxy["storage_key"], info,
                                       ctx.project_id,
                                       (ctx.index or {}).get("shots"))
        ranges = []
        for a, b in windows:
            sa, sb = tl.out_to_src(a), tl.out_to_src(max(a, b - 0.01))
            if sa is not None and sb is not None:
                ranges.append((min(sa, sb), max(sa, sb) + 0.05))
        if not ranges:
            return ""
        h = sidecars.ensure("matte.person", src, ranges,
                            priority="interactive", params=params)
        st = sidecars.status(h)
        if st == "ready":
            return ""
        est = sidecars.estimate_s(h)
        return (f"Measuring the subject for this (≈{max(1, int(est))} s): "
                "previews until then show the stand-in (a full-frame flash "
                "for a fill, a colour card for a poster, a halation for a "
                "glow) or skip the effect; finals wait up to 90 s for it.")
    except Exception:  # noqa: BLE001 — analysis never fails a write
        return ""


def _flash_thin(ctx, items, fps, edl=None):
    """Automatic picks keep <= 3 full-frame flashes per second unless the
    user asked for flashing, counting the flashes ALREADY in the edit (a
    flash transition, an accent): only the new items can be dropped.
    Returns (kept items, dropped ids)."""
    import flash_safety
    import schemas
    cls = {c.model_fields["type"].default: c for c in schemas.FX_MEMBERS}
    new_ids = {it["id"] for it in items}
    events = []
    try:
        for e in flash_safety.flash_events(edl or {}, getattr(ctx, "index",
                                                              None)):
            if e.get("id") not in new_ids:
                events.append({"t": e["t"], "id": None, "strength": 1e9})
    except Exception:  # noqa: BLE001 — a reading never blocks a write
        pass
    for it in items:
        c = cls.get(it["type"])
        try:
            times = c.model_validate(it).flash_times() if c else []
        except Exception:  # noqa: BLE001 — validation reports it later
            times = []
        for t in times or []:
            events.append({"t": t, "id": it["id"],
                           "strength": float(it.get("_strength", 1.0))})
    if not any(e["id"] for e in events):
        return items, []
    _kept, thinned = flash_safety.plan(
        events, fps, opted_in=flash_safety.opted_in(
            getattr(ctx, "user_message", "")))
    drop = {e["id"] for e in thinned if e["id"]}
    return [it for it in items if it["id"] not in drop], sorted(drop)


# ------------------------------------------------------------------ verbs
def add_subject_effect(ctx, kind, start=None, end=None, on=None, count=None,
                       frames=None, colour=None, scope=None, intensity=None,
                       region=None, hue=None, echo=None, style=None,
                       per=None):
    """WRITE: one subject effect over [start, end), or one per moment."""
    from tools_ext import _write
    gate = _gate(ctx, "subject")
    if gate:
        return gate
    kind = str(kind or "").strip().lower()
    if kind not in SUBJECT_CARDS:
        return ("REJECTED: kind is one of " + ", ".join(SUBJECT_CARDS)
                + ".")
    style = dict(style or {})
    row = ctx.latest_edl()
    edl = row["json"]
    tl = _timeline(edl)
    fps = _fps(ctx)
    prog_s = _program_s(edl, tl)
    try:
        lo = 0.0 if start is None else _num(start, "start")
        hi = prog_s if end is None else _num(end, "end")
        base = _subject_fields(kind, colour, scope, intensity, region, hue,
                               echo, style)
    except ValueError as e:
        return f"REJECTED: {e}"
    items, notes = [], []
    if on:
        on = str(on).strip().lower()
        if on not in MOMENTS:
            return "REJECTED: on is one of " + ", ".join(MOMENTS) + "."
        try:
            if on in SPEECH_MOMENTS:
                moments, rhythm = _speech_moments(ctx, tl, on), "speech"
            elif on == "cut":
                moments, rhythm = _cut_moments(ctx, edl, tl), None
            else:
                moments, mitem = _music_moments(ctx, edl, tl, on)
                rhythm = mitem.get("id")
        except ValueError as e:
            return f"REJECTED: {e}"
        gap = MIN_GAP_S.get(kind, DEFAULT_MIN_GAP_S)
        if per == "shot":
            # the strongest ``count`` moments of EVERY shot (one sentence,
            # every shot of a montage)
            n = max(1, min(MAX_ITEMS, int(count) if count else 1))
            edges = [lo] + [t for t, _r, _s in _cut_moments(ctx, edl, tl)
                            if lo < t < hi] + [hi]
            picks, words_for = [], None
            for a, b in zip(edges, edges[1:]):
                got = _pick(moments, a, b, n, gap)
                if not got and on == "stress":
                    # a shot with no stress peak: its strongest word
                    if words_for is None:
                        words_for = _speech_moments(ctx, tl, "word")
                    got = _pick(words_for, a, b, n, gap)
                picks += got
            picks = sorted(picks)[:MAX_ITEMS]
            n = n * (len(edges) - 1)
        elif per is not None:
            return "REJECTED: per is 'shot' (or omitted)."
        else:
            n = int(count) if count else DEFAULT_COUNT
            n = max(1, min(MAX_ITEMS, n))
            picks = _pick(moments, lo, hi, n, gap)
        if not picks:
            return (f"REJECTED: no {on} moment between {lo:g}s and {hi:g}s "
                    "of the program. Widen start/end or pick other "
                    "moments.")
        nf = int(frames) if frames else DEFAULT_FRAMES[kind]
        if kind == "fill" and "decay_f" not in base:
            base["decay_f"] = DEFAULT_FILL_DECAY_F
        nf += int(base.get("decay_f") or 0) if kind == "fill" else 0
        ids = _next_ids(edl, "sx", len(picks))
        for fid, (t, ref, strength) in zip(ids, picks):
            f = _frame(t, fps)
            a, b = _span(f, nf, fps)
            if b > prog_s:
                continue
            bind = {"on": on, "ref": ref}
            if rhythm:
                bind["rhythm"] = rhythm
            items.append(dict(base, id=fid, start=a, end=b, bind=bind,
                              _strength=strength))
        if len(picks) < n:
            notes.append(f"{len(picks)} of {n} moments found (at least "
                         f"{gap:g}s apart"
                         + (", per shot" if per == "shot" else "") + ").")
    else:
        if start is None:
            return ("REJECTED: give start (and end or frames), or on=<moment>"
                    " with count.")
        f0 = _frame(lo, fps)
        if end is None or frames:
            nf = int(frames) if frames else DEFAULT_FRAMES[kind]
            if kind == "fill":
                base.setdefault("decay_f", DEFAULT_FILL_DECAY_F)
                nf += int(base.get("decay_f") or 0)
            a, b = _span(f0, nf, fps)
        else:
            a, b = round(lo, 2), round(hi, 2)
        items.append(dict(base, id=_next_ids(edl, "sx", 1)[0], start=a,
                          end=b))
    for it in items:
        if it.get("kind") == "isolate":
            note = _solve_hue(ctx, it)
            if note and note not in notes:
                notes.append(note)
    items, thinned = _flash_thin(ctx, items, fps, edl)
    if thinned:
        notes.append(f"flash safety kept {len(items)}; dropped "
                     + ", ".join(thinned) + " (more than 3 flashes in a "
                     "second; the user did not ask for flashing).")
    for it in items:
        it.pop("_strength", None)
    if not items:
        return "REJECTED: nothing left to write after flash safety."
    ops = [_write.upsert_fx(it) for it in items]
    what = f"{len(items)} subject {kind} effect" + ("s" if len(items) > 1
                                                    else "")
    summary = (f"added {what} ({', '.join(i['id'] for i in items)}"
               + (f" on {on}" if on else "") + ")")
    out = _write.commit(ctx, ops, summary)
    if out.startswith("EDL"):
        note = _ensure_matte(ctx, edl, tl, [(i["start"], i["end"])
                                            for i in items])
        if note:
            notes.append(note)
    return out + ("\n" + "\n".join(notes) if notes else "")


def _subject_fields(kind, colour, scope, intensity, region, hue, echo,
                    style):
    """The item fields a kind takes (validation itself is the schema's)."""
    it = {"type": "subject", "stage": style.pop("stage", None) or "world",
          "kind": kind, "device": SUBJECT_CARDS[kind]}
    if scope:
        it["scope"] = str(scope)
    if intensity is not None:
        it["intensity"] = float(intensity)
    if region is not None:
        it["region"] = region
    if kind == "poster":
        it["field"] = colour or "accent"
        sil = style.pop("silhouette", None)
        if sil:
            it["keep_footage"] = False
            it["colour"] = sil
    elif colour is not None:
        it["colour"] = colour
    elif kind in ("fill", "glow", "outline"):
        it["colour"] = "white"
    if kind == "isolate" and hue is not None:
        h = str(hue).strip().lower()
        it["hue"] = h
        if style.get("hue_op"):
            it["hue_op"] = style.pop("hue_op")
    elif hue is not None:
        raise ValueError("hue belongs to kind isolate")
    if kind == "echo":
        e = dict(echo or {})
        e.setdefault("mode", "clone")
        e.setdefault("offsets_f", [4, 8])
        it["echo"] = e
    elif echo is not None:
        raise ValueError("echo belongs to kind echo")
    for k in ("radius", "width_px", "ev", "saturation", "decay_f",
              "field", "keep_footage", "colour"):
        if k in style:
            it[k] = style.pop(k)
    if style:
        raise ValueError("unknown style key(s): " + ", ".join(sorted(style)))
    return it


def _solve_hue(ctx, it):
    """'auto' becomes the subject's dominant hue at WRITE time (track 1E's
    ``gradewin:auto_hue``); without it the subject alone keeps colour."""
    if it.get("hue") != "auto":
        return None
    try:
        import fxgraph
        got = fxgraph.peer("gradewin:auto_hue", ctx, it["start"], it["end"],
                           default=None)
    except Exception:  # noqa: BLE001
        got = None
    if got:
        it["hue"] = str(got).lower()
        return None
    it.pop("hue", None)
    it.pop("hue_op", None)
    return ("the subject's hue could not be measured on this lane: the "
            "subject keeps all its colour, the world goes mono.")


def set_background(ctx, kind, start, end, colour=None, colour2=None,
                   source=None, sources=None, hold_subject=None,
                   hold_t=None, style=None):
    """WRITE: the background behind the subject over [start, end)."""
    from tools_ext import _write
    gate = _gate(ctx, "background")
    if gate:
        return gate
    kind = str(kind or "").strip().lower()
    style = dict(style or {})
    row = ctx.latest_edl()
    edl = row["json"]
    tl = _timeline(edl)
    fps = _fps(ctx)
    try:
        a = round(_num(start, "start"), 2)
        b = round(_num(end, "end"), 2)
        it = {"id": _next_ids(edl, "bg", 1)[0], "type": "background",
              "stage": style.pop("stage", None) or "world", "kind": kind,
              "start": a, "end": b}
        if colour is not None:
            it["colour"] = colour
        if colour2 is not None:
            it["colour2"] = colour2
        if source is not None:
            it["source"] = _source(source)
        if sources is not None:
            it["sources"] = [_source(s) for s in sources]
        if hold_subject:
            it["hold_subject"] = True
            if hold_t is not None:
                it["hold_t"] = round(_num(hold_t, "hold_t"), 2)
        for k in ("angle", "radius", "hold_f"):
            if k in style:
                it[k] = style.pop(k)
        if style:
            raise ValueError("unknown style key(s): "
                             + ", ".join(sorted(style)))
    except ValueError as e:
        return f"REJECTED: {e}"
    it["device"] = (BACKGROUND_CARDS["hold"] if it.get("hold_subject")
                    else BACKGROUND_CARDS["strobe"] if kind == "strobe"
                    else BACKGROUND_CARDS["blur"]
                    if kind in ("blur", "radial_blur")
                    else BACKGROUND_CARDS["swap"])
    notes = []
    _kept, thinned = _flash_thin(ctx, [it], fps, edl)
    if thinned:
        return ("REJECTED: that plate strobe flashes more than 3 times a "
                "second and the user did not ask for flashing. Use a longer "
                "hold (hold_f) or fewer plates, or ask the user.")
    held = f", subject held at {it.get('hold_t', a):g}s" \
        if it.get("hold_subject") else ""
    out = _write.commit(ctx, [_write.upsert_fx(it)],
                        f"set the background to {kind} {a:g}-{b:g}s"
                        f"{held} ({it['id']})")
    if out.startswith("EDL"):
        note = _ensure_matte(ctx, edl, tl, [(a, b)])
        if note:
            notes.append(note)
    return out + ("\n" + "\n".join(notes) if notes else "")


def remove_subject_effect(ctx, ids):
    """WRITE: remove subject effects and backgrounds by id."""
    from tools_ext import _write
    if isinstance(ids, str):
        ids = [ids]
    ids = [str(i) for i in ids or []]
    if not ids:
        return "REJECTED: name the ids to remove (get_edl lists them)."
    edl = ctx.latest_edl()["json"]
    have = {str(i.get("id")): i.get("type") for i in edl.get("fx") or []
            if isinstance(i, dict)}
    bad = [i for i in ids if have.get(i) not in ("subject", "background")]
    if bad:
        return ("REJECTED: not subject effects or backgrounds in this edit: "
                + ", ".join(bad) + ". get_edl lists the fx ids.")
    return _write.commit(ctx, [_write.remove_fx(i) for i in ids],
                         "removed " + ", ".join(ids))


# ------------------------------------------------------------------ specs
_REGION = {"type": "object", "description": "FxRegion: limit to a "
           "rect/ellipse/polygon/glyph/face/card"}

TOOL_SPECS = {
    "add_subject_effect": (
        add_subject_effect,
        "Effects bounded by the person cut-out (the subject matte). kind: "
        "fill (subject flat colour 1-2 frames, decays), poster (subject on "
        "a flat field: colour = field, style.silhouette draws the subject "
        "flat), glow (light around the subject), outline (sticker stroke), "
        "negate / exposure / isolate (scope subject or background; isolate "
        "keeps colour on scope, mono elsewhere, hue 'auto'|#hex joins a "
        "hue key), echo (clones from k frames back). Time it with "
        "start+end, start+frames, or on=stress|word|phrase_end|cut (speech "
        "clock) or beat|downbeat|onset (a track the user added) with "
        "count (per='shot': count on every shot). Earns its place on payoffs, structural beats and section "
        "changes, never decoration. No sound, no zoom. Without a measured "
        "matte previews show a stand-in.",
        {"kind": {"type": "string", "enum": list(SUBJECT_CARDS)},
         "start": {"type": "number"}, "end": {"type": "number"},
         "on": {"type": "string", "enum": list(MOMENTS)},
         "count": {"type": "integer"}, "per": {"type": "string",
                                               "enum": ["shot"]},
         "frames": {"type": "integer"},
         "colour": {"type": "string",
                    "description": "white|black|accent|#rrggbb"},
         "scope": {"type": "string", "enum": ["subject", "background"]},
         "intensity": {"type": "number"},
         "region": _REGION,
         "hue": {"type": "string", "description": "isolate: auto|#rrggbb"},
         "echo": {"type": "object", "description": "{mode: echo|clone|twin"
                  "|trail, offsets_f: [4, 8], opacity, decay, tint}"},
         "style": {"type": "object", "description": "radius, width_px, ev,"
                   " saturation, decay_f, silhouette, hue_op, stage "
                   "(world|screen)"}}),
    "set_background": (
        set_background,
        "Replace the background behind the subject over [start, end): "
        "solid / gradient (colour, colour2, style.angle), blur / "
        "radial_blur (style.radius), source (a media key, a source second, "
        "next or previous shot) or strobe (sources swapped every "
        "style.hold_f frames). hold_subject freezes the subject cut-out at "
        "hold_t while the background swaps. Flash-safety applies to "
        "strobes. No sound, no zoom.",
        {"kind": {"type": "string", "enum": ["solid", "gradient", "blur",
                                              "radial_blur", "source",
                                              "strobe"]},
         "start": {"type": "number"}, "end": {"type": "number"},
         "colour": {"type": "string"}, "colour2": {"type": "string"},
         "source": {"description": "media key | source second | next | "
                    "previous | FxSource"},
         "sources": {"type": "array", "items": {}},
         "hold_subject": {"type": "boolean"},
         "hold_t": {"type": "number"},
         "style": {"type": "object", "description": "angle, radius, "
                   "hold_f, stage"}}),
    "remove_subject_effect": (
        remove_subject_effect,
        "Remove subject effects and backgrounds by fx id.",
        {"ids": {"type": "array", "items": {"type": "string"}}}),
}

REQUIRED_ARGS = {"add_subject_effect": ["kind"],
                 "set_background": ["kind", "start", "end"],
                 "remove_subject_effect": ["ids"]}

WRITE_TOOLS = {"add_subject_effect", "set_background",
               "remove_subject_effect"}

COMPACT = {
    "add_subject_effect": (
        "Subject-matte effects: kind fill|poster|glow|outline|negate|"
        "exposure|isolate|echo; start+end, start+frames, or on=stress|word|"
        "phrase_end|cut|beat|downbeat|onset + count. Payoffs and section "
        "changes only. read_skill subject-fx."),
    "set_background": (
        "Background behind the subject: solid|gradient|blur|radial_blur|"
        "source|strobe over [start, end); hold_subject freezes the subject."),
    "remove_subject_effect": "Remove subject effects/backgrounds by ids.",
}
