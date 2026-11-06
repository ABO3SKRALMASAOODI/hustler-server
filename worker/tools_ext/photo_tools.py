"""Department ``picture``: craft for an upload that is already a montage.

  add_filmstrip     over a range, the pictures run past horizontally
  remove_filmstrip  by id, or all of them
  set_photo_fill    tall photo in a wide frame, wide photo in a tall frame:
                    a blurred, enlarged copy of itself instead of bars

One call per ask: the tool reads the picture map (picture_map.py, measured
once on the proxy and kept on the asset) and solves every picture itself, so
a model never loops over 83 photos. Transitions between the pictures are
set_junction (tools_ext/punctuation_tools.py), which reads the same map.
"""

DEPARTMENT = "picture"
FILL_ITEM_ID = "photo_fill"
STRIP_MIN_SHOT_S = 0.4          # under this a shot is a montage's shard


def _r(msg):
    return f"REJECTED: {msg}"


def picture_map_of(ctx):
    """The source's picture map, or None when none can be measured: held on
    the context, kept on the proxy asset's row, measured on the executor once
    (the same synchronous frames job look_at uses), locally only on a
    deployment with no executor."""
    held = getattr(ctx, "_picture_map", None)
    if held is not None:
        return held or None
    import picture_map
    found = None
    try:
        found = _stored_map(ctx, picture_map)
        if found is None:
            found = _measure_map(ctx)
    except Exception as ex:  # noqa: BLE001 — the index's shots stand in
        print(f"[picture_map] project {getattr(ctx, 'project_id', None)}: "
              f"{type(ex).__name__}: {ex}", flush=True)
    try:
        ctx._picture_map = found or {}
    except Exception:  # noqa: BLE001
        pass
    return found


def _proxy_row(ctx):
    db = getattr(ctx, "db", None)
    if db is None or getattr(ctx, "project_id", None) is None:
        return None
    import db as dbx
    return db.run(dbx.latest_asset, ctx.project_id, "proxy")


def _stored_map(ctx, picture_map):
    row = _proxy_row(ctx)
    meta = (row or {}).get("meta") if isinstance(row, dict) else None
    got = (meta or {}).get("picture_map") if isinstance(meta, dict) else None
    if isinstance(got, dict) and got.get("v") == picture_map.VERSION:
        return got
    return None


def _measure_map(ctx):
    row = _proxy_row(ctx)
    key = (row or {}).get("storage_key") if isinstance(row, dict) else None
    if not key:
        return None
    import agent_tools
    import db as dbx
    import picture_map
    import remote
    import storage
    agent_tools._touch_job_heartbeat(ctx)
    if remote.frames_available():
        got = remote.run_frames_remote(
            ctx.project_id, {"storage_key": key, "times": [0.0], "width": 64,
                             "picture_map": True},
            user_id=ctx.job.get("user_id")) or {}
        try:
            storage.delete_keys([k for k in (got.get("keys") or []) if k])
        except Exception:  # noqa: BLE001 — scratch objects
            pass
        found = got.get("picture_map")
    else:
        found = picture_map.run_on(ctx.proxy_path())
    if found:
        try:
            ctx.db.run(dbx.update_asset_meta, row["id"],
                       {"picture_map": found})
        except Exception as ex:  # noqa: BLE001 — measured again next time
            print(f"[picture_map] not kept: {ex}", flush=True)
    return found


def _edl(ctx):
    return dict(ctx.latest_edl()["json"])


def _timeline(edl):
    from timeline import Timeline
    return Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                    edl.get("speed") or [])


def _rect(row):
    r = row.get("rect") or [0.0, 0.0, 1.0, 1.0]
    return {"x0": r[0], "y0": r[1], "x1": r[2], "y1": r[3]}


def _shots_in(edl, shots, start, end, whole_range=True):
    """[(row, program start, program end, kept source a, kept source b)] for
    every picture the program shows inside [start, end), in program order.
    One row per showing: a picture kept twice is two pictures. A picture with
    only a sliver in the range is not in it (``whole_range`` False takes any
    picture the range touches: at= names a moment of one)."""
    import timeline
    out = []
    for b in timeline.program_blocks(edl):
        if b.get("kind") != "footage":
            continue
        s0, s1 = float(b["src_start"]), float(b["src_end"])
        o0, o1 = float(b["out_start"]), float(b["out_end"])
        rate = (s1 - s0) / max(o1 - o0, 1e-6)
        for row in shots:
            a = max(s0, float(row["start"]))
            z = min(s1, float(row["end"]))
            if z - a < 0.05:
                continue
            p0 = o0 + (a - s0) / rate
            p1 = o0 + (z - s0) / rate
            shown = min(p1, end) - max(p0, start)
            if shown < (min(0.5, (p1 - p0) / 2.0) if whole_range else 0.01):
                continue
            if out and out[-1][0] is row and abs(out[-1][2] - p0) < 0.02:
                out[-1] = (row, out[-1][1], p1, out[-1][3], z)
            else:
                out.append((row, p0, p1, a, z))
    return out


def _range(start, end, program):
    try:
        lo = 0.0 if start is None else max(0.0, float(start))
        hi = program if end is None else min(program, float(end))
    except (TypeError, ValueError):
        return None
    return (lo, hi) if hi > lo else None


def _taken_id(edl, base):
    have = {str(i.get("id")) for i in edl.get("fx") or []
            if isinstance(i, dict)}
    k = 1
    while f"{base}{k}" in have:
        k += 1
    return f"{base}{k}"


def _moment(row):
    """The source second of a picture's own frame: the map's fullest sample
    when the program keeps it, else the middle of what it keeps."""
    best = row[0].get("best")
    if best is not None and row[3] <= float(best) < row[4]:
        return round(float(best), 3)
    return round((row[3] + row[4]) / 2.0, 3)


def add_filmstrip(ctx, start, end, direction="left", height=None,
                  backdrop="blur", gap=None):
    edl = _edl(ctx)
    tl = _timeline(edl)
    program = float(tl.out_duration)
    span = _range(start, end, program)
    if span is None:
        return _r(f"start and end are program seconds inside 0-{program:g}.")
    lo, hi = span
    pm = picture_map_of(ctx)
    if not pm:
        return _r("the pictures of this upload could not be measured just "
                  "now; nothing changed. Try again, or use insert_media with "
                  "the customer's own photos.")
    rows = _shots_in(edl, pm["shots"], lo, hi)
    pictures = [r for r in rows if not r[0].get("flat")
                and r[4] - r[3] >= STRIP_MIN_SHOT_S] or rows
    if len(pictures) < 2:
        return _r(f"{lo:g}-{hi:g}s holds {len(pictures)} picture: a filmstrip "
                  "needs two or more. Widen the range.")
    photos = [{"t": _moment(r), "rect": _rect(r[0])} for r in pictures]
    # A range that starts or ends a beat off a change of picture starts on
    # it: the strip then replaces whole pictures.
    a = rows[0][1] if abs(rows[0][1] - lo) < 0.5 else lo
    b = rows[-1][2] if abs(rows[-1][2] - hi) < 0.5 else hi
    item = {"id": _taken_id(edl, "fs"), "type": "filmstrip", "stage": "world",
            "start": round(a, 2), "end": round(min(b, program), 2),
            "photos": photos, "direction": direction or "left",
            "backdrop": backdrop or "blur"}
    if height is not None:
        item["height"] = float(height)
    if gap is not None:
        item["gap"] = float(gap)
    import tools_ext._write as w
    # A new strip over an old one is a change of that strip.
    old = [i["id"] for i in edl.get("fx") or [] if isinstance(i, dict)
           and i.get("type") == "filmstrip" and float(i["start"]) < item["end"]
           and item["start"] < float(i["end"])]
    out = w.commit(ctx, [w.remove_fx(i) for i in old] + [w.upsert_fx(item)],
                   f"add_filmstrip: {len(photos)} pictures run {item['direction']} "
                   f"over {item['start']:g}-{item['end']:g}s, each its own "
                   "source frame, whole, in its own colour and aspect. "
                   "Length and sound unchanged")
    if out.startswith("REJECTED"):
        return out
    return (f"{out}\nid: {item['id']}. A render shows it (the Studio's live "
            "draft cannot draw it). Calling add_filmstrip over the same "
            "range changes it; remove_filmstrip takes it off.")


def remove_filmstrip(ctx, ids="all"):
    edl = _edl(ctx)
    mine = [str(i.get("id")) for i in edl.get("fx") or []
            if isinstance(i, dict) and i.get("type") == "filmstrip"]
    if ids in (None, "all", ["all"]):
        wanted = mine
    else:
        wanted = [str(i) for i in ([ids] if isinstance(ids, str) else ids)]
    missing = [i for i in wanted if i not in mine]
    if missing:
        return _r(f"{', '.join(missing)} is not a filmstrip here "
                  f"(filmstrips: {', '.join(mine) or 'none'}).")
    if not wanted:
        return ("NO CHANGE: this edit has no filmstrip. Do NOT tell the user "
                "you changed anything.")
    import tools_ext._write as w
    return w.commit(ctx, [w.remove_fx(i) for i in wanted],
                    f"remove_filmstrip: {', '.join(wanted)}")


def _frame_aspect(edl, pm):
    """Width over height of the delivered frame."""
    ratio = (edl.get("frame") or {}).get("ratio")
    if isinstance(ratio, str) and ":" in ratio:
        try:
            a, b = ratio.split(":", 1)
            return float(a) / float(b)
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    return float(pm["width"]) / float(pm["height"])


def _needs_fill(row, aspect, pm):
    x0, y0, x1, y1 = row.get("rect") or (0.0, 0.0, 1.0, 1.0)
    w = (x1 - x0) * float(pm["width"])
    h = (y1 - y0) * float(pm["height"])
    return h > 0 and abs(w / h / aspect - 1.0) > 0.02


def set_photo_fill(ctx, start=None, end=None, at=None, mode="blur", blur=None,
                   dim=None):
    edl = _edl(ctx)
    tl = _timeline(edl)
    program = float(tl.out_duration)
    pm = picture_map_of(ctx)
    if not pm:
        return _r("the pictures of this upload could not be measured just "
                  "now; nothing changed. Try again in a moment.")
    if at is not None:
        try:
            start, end = float(at), float(at) + 0.05
        except (TypeError, ValueError):
            return _r("at is one program second, on the picture to fill.")
    span = _range(start, end, program)
    if span is None:
        return _r(f"start and end are program seconds inside 0-{program:g}.")
    mode = str(mode or "blur").strip().lower()
    if mode not in ("blur", "none"):
        return _r("mode is 'blur', or 'none' to take the fill off.")
    current = next((i for i in edl.get("fx") or [] if isinstance(i, dict)
                    and i.get("type") == "photo_fill"), None)
    held = list((current or {}).get("spans") or [])
    rows = _shots_in(edl, pm["shots"], *span, whole_range=at is None)
    aspect = _frame_aspect(edl, pm)

    def covers(sp):
        return any(float(sp["t0"]) < r[4] and r[3] < float(sp["t1"])
                   for r in rows)

    spans = [sp for sp in held if not covers(sp)]
    added = 0
    if mode == "blur":
        for row, _p0, _p1, a, z in rows:
            if _needs_fill(row, aspect, pm):
                spans.append({"t0": round(a, 3), "t1": round(z, 3),
                              "rect": _rect(row),
                              "still": bool(row.get("still"))})
                added += 1
        if not added and not spans:
            return ("NO CHANGE: every picture in that range already fills "
                    "the frame, so there are no bars to fill. Do NOT tell "
                    "the user you changed anything.")
    elif len(spans) == len(held):
        return ("NO CHANGE: no picture in that range has a blurred fill. Do "
                "NOT tell the user you changed anything.")
    import tools_ext._write as w
    ops = []
    if not spans:
        ops.append(w.remove_fx(current["id"]))
        summary = "set_photo_fill: blurred fill removed; the bars are back"
    else:
        spans.sort(key=lambda sp: float(sp["t0"]))
        item = {"id": FILL_ITEM_ID, "type": "photo_fill", "stage": "world",
                "start": 0.0, "end": round(program, 2), "spans": spans}
        for key, value, old in (("blur", blur, (current or {}).get("blur")),
                                ("dim", dim, (current or {}).get("dim"))):
            if value is not None:
                item[key] = float(value)
            elif old is not None:
                item[key] = old
        ops.append(w.upsert_fx(item))
        summary = (f"set_photo_fill: {added} picture(s) in {span[0]:g}-"
                   f"{span[1]:g}s on a blurred, enlarged copy of themselves "
                   f"instead of bars ({len(spans)} in all)"
                   if mode == "blur" else
                   f"set_photo_fill: removed in {span[0]:g}-{span[1]:g}s "
                   f"({len(spans)} still filled)")
    out = w.commit(ctx, ops, summary)
    if out.startswith("REJECTED"):
        return out
    return out + ("\nA render shows it (the Studio's live draft cannot draw "
                  "it). Remove with set_photo_fill(mode='none').")


TOOL_SPECS = {
    "add_filmstrip": (
        add_filmstrip,
        "A filmstrip over a range of the program: the upload's pictures run "
        "past horizontally, two or more in view, each its own source frame, "
        "whole, in its original colour and aspect, on a soft blurred backdrop "
        "(or black). One call solves every picture in the range, baked into "
        "one file or not; length and sound never move. start/end: program "
        "seconds. direction: left|right. height: 0.3-1 of the frame. "
        "backdrop: blur|black. remove_filmstrip takes it off.",
        {"start": {"type": "number"}, "end": {"type": "number"},
         "direction": {"type": "string", "enum": ["left", "right"]},
         "height": {"type": "number", "minimum": 0.3, "maximum": 1},
         "backdrop": {"type": "string", "enum": ["blur", "black"]},
         "gap": {"type": "number", "minimum": 0, "maximum": 0.2}}),
    "remove_filmstrip": (
        remove_filmstrip,
        "Remove a filmstrip by id (get_edl fx), or every one ('all', the "
        "default).",
        {"ids": {"type": ["array", "string"], "items": {"type": "string"}}}),
    "set_photo_fill": (
        set_photo_fill,
        "Blurred fill: every picture with bars, whether baked into the "
        "upload (a tall photo in a wide slideshow) or made by the frame (a "
        "wide photo in a 9:16 frame), sits fitted and whole over a blurred, "
        "enlarged copy of itself. One call: the whole film by default, or "
        "start/end (program seconds), or at= one picture. blur: 0.01-0.2 of "
        "the frame. dim: 0-0.9. mode='none' removes it, whole or in a range. "
        "Pictures with no bars stay as they are.",
        {"start": {"type": "number"}, "end": {"type": "number"},
         "at": {"type": "number"},
         "mode": {"type": "string", "enum": ["blur", "none"]},
         "blur": {"type": "number", "minimum": 0.01, "maximum": 0.2},
         "dim": {"type": "number", "minimum": 0, "maximum": 0.9}}),
}

REQUIRED_ARGS = {"add_filmstrip": ["start", "end"]}

WRITE_TOOLS = {"add_filmstrip", "remove_filmstrip", "set_photo_fill"}

COMPACT = {
    "add_filmstrip": (
        "Filmstrip over start-end: the upload's own pictures run past, two "
        "or more in view, original colour and aspect, every picture solved "
        "in one call. direction left|right, height, backdrop blur|black. "
        "Length and sound never move."),
    "remove_filmstrip": "Remove a filmstrip by ids, or 'all'.",
    "set_photo_fill": (
        "Pictures with bars (tall photo in a wide frame, wide in a tall one) "
        "on a blurred enlarged copy of themselves. One call: whole film, or "
        "start/end, or at=. mode='none' removes it."),
}
