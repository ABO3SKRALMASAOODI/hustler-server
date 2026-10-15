"""Department ``direction`` (Wave 2, track 2E): recall over the footage.

  find_shots          shots in the main video and the project's clips: like
                      a moment (same framing on another day), by light
                      (night / day), by shape (circles), by face size, or by
                      words the storyboard saw
  suggest_match_cuts  a cut sequence that rhymes: faces conformed onto one
                      eye line (the accelerating face montage), circles
                      chained centre to centre, the same framing across
                      days; written as cropped inserts when asked
  pick_still          the frame to freeze for a poster: sharp in its
                      window, the lockup box clear, no face under it
  plan_loop           the last beat before the end card rhymes with the
                      first frame (a loop on the feed); written when asked

All descriptors are model-free (library.py, ``embed.classic``) and cached
per clip; every lane runs them. Semantic text recall (SigLIP) waits for the
vision lane; until then a word query reads the storyboard captions.
"""

import json

DEPARTMENT = "direction"
VERBS = ("find_shots", "suggest_match_cuts", "pick_still", "plan_loop")
CLIP_KINDS = ("video_clip", "generated_video", "image_ref")
MAIN = "main"


def _latest(ctx):
    return json.loads(json.dumps(ctx.latest_edl()["json"]))


def _tl(edl):
    from timeline import Timeline
    return Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                    edl.get("speed") or [])


def _canvas(ctx, edl):
    try:
        import motion_tools
        return motion_tools._canvas_size(ctx, edl)
    except Exception:  # noqa: BLE001
        return 1080, 1920


MAIN_WINDOW_S = 600.0
MAIN_MARGIN_S = 30.0


def _program_window(ctx):
    """Where in the main video to describe: from a little before the
    program's first kept second (a podcast short is a window deep inside
    a two-hour source; describing the file's first ten minutes found
    nothing there: plan_loop 'does not open on described footage')."""
    try:
        keep = (ctx.latest_edl()["json"] or {}).get("keep") or []
        first = min(float(k[0]) for k in keep)
        last = max(float(k[1]) for k in keep)
    except Exception:  # noqa: BLE001 — the file's start, as before
        return 0.0
    if last <= MAIN_WINDOW_S - MAIN_MARGIN_S:
        return 0.0                      # it is all in the first window
    return round(max(0.0, first - MAIN_MARGIN_S), 1)


def _main_doc(ctx):
    """(library doc, source size, main asset key) of the main video."""
    import library
    if not getattr(ctx, "has_main_video", True):
        return None, (0, 0), None
    path = ctx.proxy_path()
    vid = (getattr(ctx, "index", None) or {}).get("video") or {}
    doc = library.clip_doc(path, vid.get("sha256") or None,
                           start=_program_window(ctx))
    key = None
    try:
        import db as dbx
        orig = ctx.db.run(dbx.latest_asset, ctx.project_id, "original")
        key = (orig or {}).get("storage_key")
    except Exception:  # noqa: BLE001
        key = None
    return doc, (int(vid.get("width") or 1920), int(vid.get("height") or
                                                     1080)), key


def entries(ctx, keys=None, main=True):
    """(library entries [{key, t0, t1, mid, motion, desc, cluster}],
    sizes {key: (w, h)}, main asset key)."""
    import agent_tools
    import db as dbx
    import library
    docs, sizes = {}, {}
    rows = ctx.db.run(dbx.assets_by_kinds, ctx.project_id,
                      list(CLIP_KINDS)) or []
    if keys:
        rows = [r for r in rows if r.get("storage_key") in set(keys)]
    for r in rows[:60]:
        try:
            asset, _e = agent_tools._resolve_media_asset(
                ctx, r["storage_key"], CLIP_KINDS)
            path = agent_tools._asset_local_path(ctx, asset)
            docs[r["storage_key"]] = library.clip_doc(path,
                                                      asset.get("sha256"))
            sizes[r["storage_key"]] = (int(asset.get("width") or 1920),
                                       int(asset.get("height") or 1080))
        except Exception:  # noqa: BLE001 — that clip is skipped
            continue
    main_key = None
    if main:
        try:
            doc, size, main_key = _main_doc(ctx)
            if doc:
                docs[MAIN] = doc
                sizes[MAIN] = size
        except Exception:  # noqa: BLE001
            pass
    return flatten(docs), sizes, main_key


def _paths(ctx):
    """{library key: local path} for the clips (and the main video)."""
    import agent_tools
    import db as dbx
    out = {}
    try:
        rows = ctx.db.run(dbx.assets_by_kinds, ctx.project_id,
                          list(CLIP_KINDS)) or []
        for r in rows[:60]:
            asset, _e = agent_tools._resolve_media_asset(
                ctx, r["storage_key"], CLIP_KINDS)
            if asset:
                out[r["storage_key"]] = agent_tools._asset_local_path(
                    ctx, asset)
    except Exception:  # noqa: BLE001
        pass
    try:
        out[MAIN] = ctx.proxy_path()
    except Exception:  # noqa: BLE001
        pass
    return out


def flatten(docs):
    import library
    out = []
    for key, doc in docs.items():
        for sh in doc.get("shots") or []:
            out.append({"key": key, "t0": sh["t0"], "t1": sh["t1"],
                        "mid": sh["mid"], "motion": sh.get("motion", 0.0),
                        "desc": sh.get("desc") or {}})
    library.cluster(out)
    return out


def _where(e, tl):
    if e["key"] == MAIN:
        p = tl.src_to_out(e["mid"])
        return (f"main video {e['t0']:.1f}-{e['t1']:.1f}s" +
                (f" (program {p:.1f}s)" if p is not None else " (cut)"))
    return f"{e['key']} {e['t0']:.1f}-{e['t1']:.1f}s"


def _ref_entry(ctx, edl, ents, like):
    """The entry a 'like' names: program seconds, or 'key@seconds'."""
    tl = _tl(edl)
    s = str(like)
    if "@" in s:
        key, t = s.rsplit("@", 1)
        t = float(t)
    else:
        key, t = MAIN, tl.out_to_src(float(s))
        if t is None:
            return None
    best = [e for e in ents if e["key"] == key and e["t0"] - 0.05 <= t <=
            e["t1"] + 0.05]
    return best[0] if best else None


def find_shots(ctx, like=None, query=None, night=None, circles=None,
               faces=None, k=5):
    """READ: shots by example, light, shape, faces or words."""
    import story_match
    edl = _latest(ctx)
    ents, sizes, _mk = entries(ctx)
    if not ents:
        return "No footage is described yet (no main video or clips)."
    tl = _tl(edl)
    k = max(1, min(12, int(k or 5)))
    lines = []
    if like is not None:
        ref = _ref_entry(ctx, edl, ents, like)
        if ref is None:
            return f"REJECTED: no shot at '{like}'."
        hits = story_match.same_framing(ref["desc"], ents, k=k,
                                        exclude_key=(ref["key"], ref["mid"]))
        lines.append(f"Framed like {_where(ref, tl)}:")
        lines += [f"  {s:.2f}  {_where(e, tl)}" for s, e in hits]
    pool = ents
    if night is not None:
        pool = [e for e in pool if bool(e["desc"].get("night")) == bool(
            night)]
    if circles:
        pool = [e for e in pool if e["desc"].get("circles")]
    if faces in ("close", "medium", "wide", "none"):
        import library
        pool = [e for e in pool if (library.scale_of(e["desc"]) == faces if
                                    faces != "none" else
                                    not e["desc"].get("faces"))]
    if query:
        sb = ((getattr(ctx, "index", None) or {}).get("storyboard") or
              {}).get("frames") or []
        words = [w.lower() for w in str(query).split() if len(w) > 2]
        hits = []
        for fr in sb:
            cap = str(fr.get("caption") or fr.get("text") or "").lower()
            n = sum(1 for w in words if w in cap)
            if n:
                hits.append((n, fr))
        hits.sort(key=lambda h: -h[0])
        lines.append(f"Storyboard frames that mention '{query}':")
        lines += [f"  main video {float(fr.get('t', 0)):.1f}s: "
                  f"{str(fr.get('caption') or fr.get('text'))[:90]}"
                  for _n, fr in hits[:k]] or ["  none (semantic recall "
                                              "waits for the vision lane)"]
    if night is not None or circles or faces:
        lines.append(f"{len(pool)} shot(s) match:")
        lines += [f"  {_where(e, tl)}" +
                  (" [night]" if e['desc'].get('night') else "") +
                  (f" circles {len(e['desc']['circles'])}"
                   if e["desc"].get("circles") else "")
                  for e in pool[:k]]
    if not lines:
        return ("Give like (program seconds or key@seconds), night, "
                "circles, faces (close|medium|wide|none) or query.")
    return "\n".join(lines)


def _seq_durations(n, accelerate=True, first=0.6, last=0.24):
    if n <= 1 or not accelerate:
        return [first] * n
    r = (last / first) ** (1.0 / (n - 1))
    return [round(first * r ** k, 2) for k in range(n)]


def suggest_match_cuts(ctx, kind="face_aligned", n=6, at=None,
                       accelerate=True, write=False):
    """A rhyming cut sequence; written as cropped inserts when asked."""
    import story_match
    from tools_ext import _write
    edl = _latest(ctx)
    W, H = _canvas(ctx, edl)
    ar = W / float(H)
    ents, sizes, main_key = entries(ctx)
    n = max(3, min(12, int(n or 6)))
    if kind == "face_aligned":
        seq = story_match.face_aligned(ents, sizes, ar, n=2 * n,
                                       one_per_key=False)
        # re-measure the chosen faces at full detail, then conform again
        import library
        paths = _paths(ctx)
        picked = [e for e, _c in seq]
        for e in picked:
            if e["key"] in paths:
                library.refine_eyes(e, paths[e["key"]])
        seq = story_match.face_aligned(picked, sizes, ar, n=n)
    elif kind == "shape_chain":
        seq = story_match.shape_chain(ents, sizes, ar, n=n)
    elif kind == "same_framing":
        if not ents:
            seq = []
        else:
            ref = ents[0]
            seq = [(e, None) for _s, e in story_match.same_framing(
                ref["desc"], ents, k=n)]
    else:
        return ("REJECTED: kind is face_aligned, shape_chain or "
                "same_framing.")
    if len(seq) < 3:
        return (f"No {kind.replace('_', ' ')} sequence in this footage "
                f"(found {len(seq)}; three make a rhyme).")
    tl = _tl(edl)
    durs = _seq_durations(len(seq), accelerate)
    lines = [f"{kind.replace('_', ' ')}: {len(seq)} shots" +
             (", accelerating " + "/".join(f"{d:g}" for d in durs) + " s"
              if accelerate else "")]
    for (e, crop), d in zip(seq, durs):
        lines.append(f"  {_where(e, tl)} crop {crop}")
    if not write:
        return "\n".join(lines) + ("\nwrite=true splices them at 'at' "
                                   "(program seconds; snapped to a cut).")
    prog = float(tl.out_duration or 0.0)
    try:
        at_p = prog if at is None else float(at)
    except (TypeError, ValueError):
        return "REJECTED: at is program seconds."
    import timeline as tlmod
    bounds = [0.0] + list(tlmod.keep_boundaries(edl.get("keep") or [],
                                                edl.get("speed") or []))
    pre = min(bounds, key=lambda b: abs(b - at_p)) if bounds else 0.0
    ops = []
    for k, ((e, crop), d) in enumerate(zip(seq, durs)):
        key = main_key if e["key"] == MAIN else e["key"]
        if not key:
            continue
        iid = f"mc{k + 1}"
        item = {"id": iid, "asset_key": key, "kind": "video",
                "at_output_s": round(pre, 2), "duration_s": max(0.2, d),
                "source_start_s": round(max(0.0, e["mid"] - d / 2), 2),
                "mute": True}
        if crop:
            item["crop"] = crop
        ops.append({"action": "upsert", "layer": "inserts", "id": iid,
                    "value": item})
    return _write.commit(ctx, ops, f"{kind} sequence: {len(ops)} cropped "
                         f"shots at {pre:.2f}s") + "\n" + "\n".join(lines)


def pick_still(ctx, start, end, box=None):
    """READ: the frame to freeze for a poster in a program window."""
    import stills
    edl = _latest(ctx)
    tl = _tl(edl)
    try:
        a, b = tl.out_to_src(float(start)), tl.out_to_src(float(end))
    except (TypeError, ValueError):
        return "REJECTED: start and end are program seconds."
    if a is None or b is None or b <= a:
        return "REJECTED: that window is not one stretch of kept footage."
    if isinstance(box, str):
        try:
            box = json.loads(box)
        except ValueError:
            box = None
    if box is not None and not (isinstance(box, list) and len(box) == 4):
        return "REJECTED: box is [x0, y0, x1, y1] frame fractions."
    try:
        r = stills.pick(ctx.proxy_path(), a, b, box)
    except Exception as e:  # noqa: BLE001
        return f"Could not read the footage ({str(e)[:120]})."
    p = tl.src_to_out(r["t"])
    return (f"Freeze at program {p:.2f}s (source {r['t']:.2f}s): sharper "
            f"than {r['sharp_pct'] * 100:.0f}% of the {r['n']} frames in the "
            f"window" + (f"; the box is {r['clutter'] * 100:.0f}% busy and "
                         f"{'clear of' if not r['face_overlap'] else 'ON'} "
                         "the face" if box else "") +
            ". add_freeze_frame there, then set the poster type in the box.")


def plan_loop(ctx, write=False, min_sim=None):
    """The last beat before the end card rhymes with the first frame."""
    import loops
    import timeline as tlmod
    from tools_ext import _write
    edl = _latest(ctx)
    tl = _tl(edl)
    ents, _sizes, main_key = entries(ctx)
    if not ents:
        return "No footage is described yet."
    first_src = tl.out_to_src(0.05)
    opening = next((e for e in ents if e["key"] == MAIN and first_src is
                    not None and e["t0"] - 0.05 <= first_src <= e["t1"]),
                   None)
    if opening is None:
        return "The program does not open on described footage."
    hits = loops.rhyme(opening["desc"], ents,
                       exclude=(MAIN, opening["t0"], opening["t1"]),
                       min_sim=float(min_sim or loops.MIN_RHYME))
    if not hits:
        return ("No shot rhymes with the opening frame closely enough "
                f"(classic similarity < {loops.MIN_RHYME}); a loop would "
                "not read as one.")
    sim, e = hits[0]
    if e["key"] == MAIN and not main_key:
        return "The rhyme is in the main video but its original is missing."
    bounds = list(tlmod.keep_boundaries(edl.get("keep") or [],
                                        edl.get("speed") or []))
    end_b = bounds[-1] if bounds else float(tl.out_duration or 0.0)
    ins = loops.reprise_insert(dict(e, key=main_key if e["key"] == MAIN
                                    else e["key"]), tl.out_duration, end_b)
    ins["mute"] = True
    line = (f"loop rhyme: closes on {_where(e, tl)} (similarity {sim:.2f} "
            "to the first frame), before the end card")
    if not write:
        return line + "\nwrite=true adds it."
    return _write.commit(ctx, [{"action": "upsert", "layer": "inserts",
                                "id": ins["id"], "value": ins}], line)


TOOL_SPECS = {
    "find_shots": (
        find_shots,
        "READ: recall shots in the main video and the project's clips: like "
        "a moment (program seconds or key@seconds: the same framing on "
        "another day), night true/false, circles, faces close|medium|wide|"
        "none, or a word query over the storyboard.",
        {"like": {"type": "string"}, "query": {"type": "string"},
         "night": {"type": "boolean"}, "circles": {"type": "boolean"},
         "faces": {"type": "string", "enum": ["close", "medium", "wide",
                                              "none"]},
         "k": {"type": "integer", "minimum": 1, "maximum": 12}}),
    "suggest_match_cuts": (
        suggest_match_cuts,
        "WRITE when write=true (else READ): a rhyming sequence — "
        "face_aligned (eyes conformed onto one line, accelerating), "
        "shape_chain (circles centre to centre), same_framing — as cropped, "
        "muted inserts spliced at 'at' (program seconds, snapped to a cut).",
        {"kind": {"type": "string", "enum": ["face_aligned", "shape_chain",
                                             "same_framing"]},
         "n": {"type": "integer", "minimum": 3, "maximum": 12},
         "at": {"type": "number"}, "accelerate": {"type": "boolean"},
         "write": {"type": "boolean"}}),
    "pick_still": (
        pick_still,
        "READ: the frame to freeze for a poster in a program window: sharp "
        "in its window, the lockup box ([x0,y0,x1,y1]) clear and off the "
        "face.",
        {"start": {"type": "number"}, "end": {"type": "number"},
         "box": {"type": "array", "items": {"type": "number"}}}),
    "plan_loop": (
        plan_loop,
        "WRITE when write=true (else READ): close the program before the end "
        "card on a shot that rhymes with its first frame (a loop on the "
        "feed).",
        {"write": {"type": "boolean"}, "min_sim": {"type": "number"}}),
}
REQUIRED_ARGS = {"pick_still": ["start", "end"]}
WRITE_TOOLS = {"suggest_match_cuts", "plan_loop"}
COMPACT = {
    "find_shots": "READ: recall shots by example (same framing other day), "
                  "night/day, circles, face size, or storyboard words.",
    "suggest_match_cuts": "face-aligned / circle-chain / same-framing "
                          "sequence as cropped inserts (write=true).",
    "pick_still": "READ: sharpest freeze frame in a window with a clear, "
                  "face-free lockup box.",
    "plan_loop": "loop rhyme before the end card on a shot matching the "
                 "first frame (write=true).",
}
