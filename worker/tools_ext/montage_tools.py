"""Department ``rhythm`` (Wave 2 track 2D): cutting to the user's track.

  plan_to_music   a montage over a program window, cut to the user's track
                  (or to the speech when there is none): beat slots, modes
                  per section (strobe / pulse / hold), cuts one frame before
                  the kick, no cluster repeat within 6, scale alternating,
                  call-and-response or density arcs with type; ONE batch,
                  its intent recorded so a song swap re-plans it
  shape_music     the user's track bent to the story: the drop lands on the
                  payoff (a bar-aligned edit of the music item's own fields),
                  a held breath before it, a low-passed intro that opens on
                  it, stutters on a hit
  set_speed_ramp  an eased slow-motion swing (1.0 -> low -> 1.0) as
                  contiguous speed spans; no duplicated frames by default
  animate_still   archive stills that MOVE: a cover image overlay becomes a
                  moving still (Ken Burns push / pull / pan / rise / drift
                  toward a face it finds), an image insert gets a camera move

Agents never choose music: every verb that reads a track refuses when the
user supplied none (plan_to_music falls back to the speech clock).
"""

import json
import os

DEPARTMENT = "rhythm"
VERBS = ("plan_to_music", "shape_music", "set_speed_ramp", "animate_still")
CLIP_KINDS = ("video_clip", "generated_video")
IMAGE_KINDS = ("image_ref",)
_MAPS = {}                       # storage_key -> track RhythmMap (media)
_MAPS_MAX = 8


def _latest(ctx):
    return json.loads(json.dumps(ctx.latest_edl()["json"]))


def _tl(edl):
    from timeline import Timeline
    return Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                    edl.get("speed") or [])


def _fps(ctx, edl):
    import device_planner
    return device_planner.program_fps(edl, getattr(ctx, "index", None))


def _user_music(edl, music_id=None):
    from tools_ext import rhythm_tools
    items = rhythm_tools._user_music(edl)
    if music_id:
        items = [m for m in items if m.get("id") == music_id]
    return items


def _remember(key, rmap):
    _MAPS[key] = rmap
    while len(_MAPS) > _MAPS_MAX:
        _MAPS.pop(next(iter(_MAPS)))


def _track_map(ctx, item):
    """The RhythmMap (media clock) of a music item's track."""
    key = item.get("storage_key")
    if key in _MAPS:
        return _MAPS[key]
    from tools_ext import rhythm_tools
    import rhythm
    asset, path = rhythm_tools._music_asset(ctx, item)
    rmap = rhythm.map_for(path, asset.get("sha256"), asset.get("duration_s"),
                          ref={"storage_key": key,
                               "sha256": asset.get("sha256"),
                               "music_id": item.get("id")})
    _remember(key, rmap)
    return rmap


def _speech_map(ctx, edl):
    import device_planner
    return device_planner._speech_onsets(edl, getattr(ctx, "index", None),
                                         _tl(edl))


def _clock(ctx, edl, music_id=None, clock="auto"):
    """(program-clock map, music item | None, label) or (None, None, err)."""
    import rhythm_types
    items = _user_music(edl, music_id)
    if clock == "speech" or not items:
        if music_id and not items:
            return None, None, ("REJECTED: no music item '" + str(music_id) +
                                "' the user supplied in this edit.")
        rm = _speech_map(ctx, edl)
        if not [o for o in rm.get("onsets") or []
                if o.get("cls") == "stress"]:
            return None, None, ("REJECTED: there is no user track and no "
                                "measurable speech stress to cut to.")
        return rm, None, "speech"
    item = items[0]
    try:
        rmap = _track_map(ctx, item)
    except Exception as e:  # noqa: BLE001
        return None, None, (f"REJECTED: the track could not be analysed "
                            f"({str(e)[:140]}).")
    if not rmap.get("beats"):
        return None, None, ("REJECTED: no confident beat grid in the user's "
                            "track (rubato or beatless); cut to the speech "
                            "(clock='speech') or place the cuts by hand.")
    prog = rhythm_types.to_program(rmap, _tl(edl), item,
                                   rmap.get("duration"))
    return prog, item, item.get("id")


# ── the shot pool ────────────────────────────────────────────────────────

def _project_assets(ctx, kinds):
    import db as dbx
    return ctx.db.run(dbx.assets_by_kinds, ctx.project_id, list(kinds)) or []


def pool_from_assets(rows, library=None):
    """[{id, asset_key, kind, t0, t1, cluster, scale, score}] from asset
    rows. ``library`` ({storage_key: [shot dicts]}, 2E) splits clips into
    shots with clusters and scales; without it a clip is one shot, its own
    cluster."""
    out = []
    for r in rows:
        key = r.get("storage_key")
        if not key:
            continue
        kind = "image" if r.get("kind") in IMAGE_KINDS else "video"
        shots = (library or {}).get(key)
        if shots:
            for k, sh in enumerate(shots):
                out.append({"id": f"{key}#{k}", "asset_key": key,
                            "kind": kind, "t0": float(sh.get("t0", 0.0)),
                            "t1": float(sh.get("t1", 0.0)),
                            "cluster": sh.get("cluster", key),
                            "scale": sh.get("scale"),
                            "score": float(sh.get("score", 0.5))})
            continue
        dur = float(r.get("duration_s") or (6.0 if kind == "image" else 0))
        if kind == "video" and dur < 0.5:
            continue
        pad = 0.15 if dur > 1.0 else 0.0
        meta = r.get("meta") or {}
        out.append({"id": key, "asset_key": key, "kind": kind,
                    "t0": pad, "t1": max(pad, dur - pad) if kind == "video"
                    else 600.0, "cluster": meta.get("cluster") or key,
                    "scale": meta.get("scale"),
                    "score": float(meta.get("score") or 0.5)})
    return out


def _library(ctx, keys):
    """2E's shot library for these clips, when it is there."""
    try:
        import library
        return library.shots_for(ctx, keys) or {}
    except Exception:  # noqa: BLE001 — a clip is one shot then
        return {}


def pack_pool(pool, budget=2600):
    """The pool as a compact record (fits an arrange record's 4 KB)."""
    out = []
    size = 2
    for sh in pool:
        row = [sh["asset_key"], sh["kind"][0], round(sh["t0"], 2),
               round(sh["t1"], 2), sh.get("cluster") if sh.get("cluster")
               != sh["asset_key"] else 0, sh.get("scale") or 0,
               round(float(sh.get("score", 0.5)), 2)]
        n = len(json.dumps(row)) + 1
        if size + n > budget:
            break
        out.append(row)
        size += n
    return out


def unpack_pool(rows):
    out = []
    for k, r in enumerate(rows or []):
        key, kd, t0, t1, cl, sc, v = (list(r) + [0] * 7)[:7]
        out.append({"id": f"{key}#{k}", "asset_key": key,
                    "kind": "image" if kd == "i" else "video",
                    "t0": float(t0), "t1": float(t1),
                    "cluster": cl or key, "scale": sc or None,
                    "score": float(v or 0.5)})
    return out


# ── moving stills ────────────────────────────────────────────────────────

STILL_PATHS = ("push_in", "pull_out", "pan_left", "pan_right", "rise",
               "fall", "drift")


def still_look(img_w, img_h, face, W, H, k=0):
    """Path params for a still on a W x H canvas: a face pulls the move to
    it; a panorama pans, a tall scan rises; otherwise the moves rotate so
    consecutive stills never share one (range, not a house move)."""
    p = {}
    ar = (img_w / float(img_h)) if img_w and img_h else W / float(H)
    car = W / float(H)
    if face is not None:
        cx, cy = (face[0] + face[2]) / 2.0, (face[1] + face[3]) / 2.0
        p.update(path=("push_in", "pull_out")[k % 2], focus_x=round(cx, 3),
                 focus_y=round(cy, 3), amount=0.14, ease="in_out")
    elif ar > car * 1.6:
        p.update(path=("pan_right", "pan_left")[k % 2], amount=0.22,
                 ease="linear")
    elif ar < car / 1.6:
        p.update(path=("rise", "fall")[k % 2], amount=0.2, ease="linear")
    else:
        p.update(path=("push_in", "drift", "pull_out", "pan_left")[k % 4],
                 amount=0.1, ease="in_out")
    if ar < 0.8 * car or ar > 1.25 * car:
        # a shape far from the frame's shows whole over its own soft copy
        p["fit"] = "contain_blur" if face is None else "cover"
    return p


def _image_face(path):
    """(face box fractions | None, w, h) of an image file."""
    try:
        import cv2
        import follow
        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            return None, 0, 0
        h, w = img.shape[:2]
        found = follow.detect(img)
        if not found:
            return None, w, h
        box = found[0][0]
        return (box, w, h)
    except Exception:  # noqa: BLE001
        return None, 0, 0


# ── plan_to_music ────────────────────────────────────────────────────────

def _program_words(ctx, edl):
    import device_planner
    return device_planner.program_words(edl, getattr(ctx, "index", None),
                                        _tl(edl))


# quiet voices that need a picture under them (or a long hold) to read;
# type ANSWERING b-roll on black is poster-scale
ON_BLACK_AVOID = ("hairline_wide", "quote_window", "lamp_karaoke",
                  "marker_note", "whisper_shout")
SLAM_UNDER_S = 0.9              # a slot this short lands its type on the hit


def _type_item(ctx, edl, text, s, e, mid, voice="auto", on_black=False):
    from tools_ext import typeset_tools
    return typeset_tools.build_item(
        ctx, edl, text, s, e, mid, voice=voice,
        faces=[] if on_black else None,
        avoid=ON_BLACK_AVOID if on_black else (),
        verb="cut" if e - s < SLAM_UNDER_S else "auto")


# words that carry no picture on their own (a slot of only these is b-roll)
LOW_VALUE = frozenset("""
lot lots thing things stuff kind sort way ways get gets got getting make
makes made say says said tell tells told know knows knew think thinks
thought mean means meant really just like actually basically pretty very
going gonna want wanted need needs use used today guess look looks yeah
""".split())


def slot_text(ws, max_words=4):
    """The words a type slot sets: the slot's spoken words trimmed of the
    function words at either end ("of a tell that" -> "tell"), at most
    ``max_words``; '' when nothing in it carries meaning (that slot is
    b-roll instead)."""
    import device_planner
    toks = [str(w.get("w") or "") for w in ws]

    def content(t):
        n = device_planner.norm(t)
        return device_planner._content(n) and not \
            device_planner.weak_word(t) and n not in LOW_VALUE
    i, j = 0, len(toks)
    while i < j and not content(toks[i]):
        i += 1
    while j > i and not content(toks[j - 1]):
        j -= 1
    core = toks[i:j]
    if len(core) > max_words:
        # the longest run of content words wins
        best = max(range(len(core)), key=lambda k: sum(
            1 for t in core[k:k + max_words] if content(t)))
        core = core[best:best + max_words]
        while core and not content(core[-1]):
            core.pop()
    return " ".join(core).strip(" ,;:")


def _opted_in(ctx):
    import flash_safety
    return flash_safety.opted_in(getattr(ctx, "user_message", "") or "")


def plan(ctx, edl, start, end, *, music_id=None, shots=None, modes=None,
         lead_f=1, on="auto", style="broll", seed=0, every=1, prefix=None,
         clock="auto", pool=None):
    """(operations, report dict) or (None, error line). Pure of writes."""
    import montage
    tl = _tl(edl)
    prog = float(tl.out_duration or 0.0)
    try:
        s = 0.0 if start is None else float(start)
        e = prog if end is None else float(end)
    except (TypeError, ValueError):
        return None, "REJECTED: start and end are program seconds."
    s, e = max(0.0, s), min(prog, e)
    if e - s < 1.0:
        return None, "REJECTED: a montage needs at least 1 s of program."
    if not montage.LEAD_RANGE[0] <= int(lead_f) <= montage.LEAD_RANGE[1]:
        return None, "REJECTED: lead_f is 0, 1 or 2 frames before the hit."
    if style not in montage.STYLES:
        return None, f"REJECTED: style is one of {montage.STYLES}."
    rmap, item, label = _clock(ctx, edl, music_id, clock)
    if rmap is None:
        return None, label
    fps = _fps(ctx, edl)
    if on in (None, "", "auto"):
        on = "stress" if item is None else (
            "kick" if any(o.get("cls") == "kick"
                          for o in rmap.get("onsets") or []) else "beat")
    if item is None:
        modes = dict(modes or {}, phrase=(modes or {}).get("phrase",
                                                           "pulse"))
    sl = montage.slots(rmap, s, e, fps=fps, lead_f=int(lead_f), modes=modes,
                       on=on, every=max(1, int(every or 1)))
    if not sl:
        return None, ("REJECTED: no beat or stress marks in that window to "
                      "cut on.")
    if pool is None:
        rows = _project_assets(ctx, CLIP_KINDS + IMAGE_KINDS)
        if shots not in (None, "", "all", ["all"]):
            want = set([shots] if isinstance(shots, str) else shots)
            rows = [r for r in rows if r.get("storage_key") in want
                    or str(r.get("id")) in want]
        lib = _library(ctx, [r.get("storage_key") for r in rows])
        pool = pool_from_assets(rows, lib)
    if not pool:
        return None, ("REJECTED: no clips or images in this project to cut "
                      "— upload or fetch b-roll first (find_footage).")
    # the plan always runs on the pool exactly as its record stores it
    # (best shots first), so a re-plan or a song swap is deterministic
    pool = unpack_pool(pack_pool(sorted(pool, key=lambda sh: -float(
        sh.get("score", 0.5)))))
    opted = _opted_in(ctx)
    notes = []
    if not opted and any(x["mode"] == "strobe" for x in sl):
        for x in sl:
            if x["mode"] == "strobe":
                x["mode"] = "pulse"
        notes.append("strobe sections cut as pulses: the user did not ask "
                     "for strobing (flash safety)")
        sl = montage.slots(rmap, s, e, fps=fps, lead_f=int(lead_f),
                           modes={k: ("pulse" if v == "strobe" else v)
                                  for k, v in dict(montage.SECTION_MODE,
                                                   **(modes or {})).items()},
                           on=on, every=max(1, int(every or 1)))
    rl = montage.roles(sl, style, rmap.get("sections"))
    words = _program_words(ctx, edl) if style != "broll" else []
    prefix = prefix or _prefix(edl)
    broll_slots = [x for x, r in zip(sl, rl) if r in ("broll", "type_over")
                   and x["mode"] != "strobe"]
    asg = montage.assign(broll_slots, pool, seed=seed)
    by_slot = {id(a[0]): a for a in asg}
    ops, k, n_cut, still_k = [], 0, 0, 0
    W, H = _canvas(ctx, edl)
    work = dict(edl)
    seq_voice = None
    for x, r in zip(sl, rl):
        k += 1
        oid = f"{prefix}_{k}"
        if x["mode"] == "strobe":
            fx = montage.strobe_item(x, oid, fps, pool)
            ops.append({"action": "upsert", "layer": "fx", "id": oid,
                        "value": fx})
            n_cut += 1
            continue
        text = ""
        if r in ("type_black", "type_over"):
            ws = [w for w in words if x["start"] - 0.05 <= w["t0"] <
                  x["end"] - 0.05]
            text = slot_text(ws)
            if not text:
                r = "broll"
        if r in ("broll", "type_over"):
            a = by_slot.get(id(x))
            if a is None:
                a = (montage.assign([x], pool, seed=seed + k) or [None])[0]
            if a is None:
                continue
            _sl, sh, src_t = a
            sp = None
            if sh["kind"] == "image":
                # the moves rotate (animate_still re-aims one at a face)
                sp = still_look(0, 0, None, W, H, still_k)
                still_k += 1
            layer, val = montage.broll_value(x, sh, src_t, oid, sp)
            ops.append({"action": "upsert", "layer": layer, "id": oid,
                        "value": val})
        if r == "type_black":
            card = montage.black_card(x, oid + "c", fps)
            ops.append({"action": "upsert", "layer": "fx", "id": oid + "c",
                        "value": card})
        if r in ("type_black", "type_over") and text:
            # one voice carries the whole call-and-response (the answers
            # read as one speaker); the layouts vary slot to slot
            ti, why = _type_item(ctx, work, text, x["start"], x["end"],
                                 oid + "t", voice=seq_voice or "auto",
                                 on_black=(r == "type_black"))
            if ti is not None:
                seq_voice = seq_voice or (ti.get("params") or {}).get(
                    "voice")
                ops.append({"action": "upsert", "layer": "motion",
                            "id": oid + "t", "value": ti})
                # the next slot's voice sees this one (range, not repeats)
                work["motion"] = list(work.get("motion") or []) + [ti]
            else:
                notes.append(f"slot {k}: type could not be set ({why})")
        n_cut += 1
    if item is not None:
        ops.append({"action": "upsert", "layer": "fx", "id": prefix + "_in",
                    "value": montage.intent(prefix, item["id"], {
                        "t0": round(s, 3), "t1": round(e, 3),
                        "lead_f": int(lead_f), "on": on, "style": style,
                        "seed": int(seed), "every": int(every or 1),
                        "modes": modes or {},
                        "pool": pack_pool(pool)})})
    if len(ops) > montage.MAX_OPS:
        return None, (f"REJECTED: that montage is {len(ops)} edits (the "
                      f"batch limit is {montage.MAX_OPS}); plan a shorter "
                      "window, or cut every 2nd beat (every=2).")
    rep = {"slots": sl, "roles": rl, "assign": asg, "on": on,
           "clock": label, "fps": fps, "notes": notes, "prefix": prefix,
           "cuts": n_cut, "pool": len(pool)}
    return ops, rep


def _prefix(edl):
    used = {str(i.get("id") or "") for layer in ("overlays", "motion", "fx")
            for i in edl.get(layer) or []}
    k = 1
    while any(u == f"mt{k}" or u.startswith(f"mt{k}_") for u in used):
        k += 1
    return f"mt{k}"


def _canvas(ctx, edl):
    try:
        import motion_tools
        return motion_tools._canvas_size(ctx, edl)
    except Exception:  # noqa: BLE001
        return 1080, 1920


def _replace_ops(edl, prefix):
    import montage
    return [{"action": "remove", "layer": layer, "id": iid}
            for layer, iid in montage.owned(edl, prefix)]


def plan_to_music(ctx, start=None, end=None, shots=None, modes=None,
                  lead_f=1, on="auto", style="broll", every=1, seed=0,
                  music_id=None, clock="auto", replan=None, dry_run=False):
    """A montage cut to the user's track (or the speech), one batch."""
    edl = _latest(ctx)
    pool = None
    prefix = None
    if replan:
        it = next((i for i in edl.get("fx") or [] if i.get("id") ==
                   f"{replan}_in" and i.get("type") == "arrange"), None)
        if it is None:
            return f"REJECTED: no montage '{replan}' recorded in this edit."
        p = it.get("params") or {}
        start, end = p.get("t0"), p.get("t1")
        lead_f, on, style = p.get("lead_f", 1), p.get("on", "auto"), \
            p.get("style", "broll")
        seed, every, modes = p.get("seed", 0), p.get("every", 1), \
            p.get("modes") or None
        music_id = it.get("music_id")
        pool = unpack_pool(p.get("pool"))
        prefix = replan
    if isinstance(modes, str):
        try:
            modes = json.loads(modes)
        except ValueError:
            return "REJECTED: modes is {section label: strobe|pulse|hold}."
    ops, rep = plan(ctx, edl, start, end, music_id=music_id, shots=shots,
                    modes=modes, lead_f=lead_f, on=on, style=style,
                    seed=seed, every=every, prefix=prefix, clock=clock,
                    pool=pool)
    if ops is None:
        return rep
    if replan:
        # items the new plan writes again are replaced in place; only the
        # ones it no longer needs are removed
        again = {(o["layer"], o["id"]) for o in ops}
        ops = [o for o in _replace_ops(edl, replan)
               if (o["layer"], o["id"]) not in again] + ops
        if len(ops) > 64:
            return ("REJECTED: the re-plan is more than one batch (64 "
                    "edits); remove the montage and plan it again.")
    head = _plan_text(rep)
    if dry_run:
        return "DRY RUN (nothing written):\n" + head
    from tools_ext import _write
    res = _write.commit(ctx, ops, f"plan_to_music {rep['prefix']}: "
                        f"{rep['cuts']} cuts on the {rep['on']} "
                        f"({rep['clock']})")
    if res.startswith(("REJECTED", "NO CHANGE")):
        return res
    return res + "\n" + head


def _plan_text(rep):
    import montage
    sl = rep["slots"]
    modes = {}
    for x in sl:
        modes[x["mode"]] = modes.get(x["mode"], 0) + 1
    bad = montage.repeats_within(rep["assign"])
    lines = [f"montage {rep['prefix']}: {rep['cuts']} cuts on the "
             f"{rep['on']} ({rep['clock']} clock), each 1 frame before "
             f"its hit; modes " + ", ".join(f"{k} x{v}" for k, v in
                                           modes.items()) +
             f"; pool {rep['pool']} shots" +
             (f"; {len(bad)} cluster repeat(s) inside 6 cuts (the pool is "
              "too small to avoid them)" if bad else "; no cluster repeats "
              "within 6 cuts")]
    for x, r in list(zip(sl, rep["roles"]))[:10]:
        lines.append(f"  {x['start']:.2f}-{x['end']:.2f}s {x['mode']}"
                     f" ({r}) on {x['onset']:.2f}s")
    if len(sl) > 10:
        lines.append(f"  ... {len(sl) - 10} more")
    lines += [f"note: {n}" for n in rep["notes"]]
    return "\n".join(lines)


# ── the swap re-solver ───────────────────────────────────────────────────

def _swap_resolver(edl, intents, new_music_id):
    """Re-plan b-roll montages on the swapped track (hooks 'music_swap').
    Downloads and analyses the new track once (no ctx at hook time)."""
    import montage
    import rhythm
    import rhythm_types
    import storage
    import tempfile
    notes = []
    item = next((m for m in edl.get("music") or [] if m.get("id") ==
                 new_music_id), None)
    if item is None:
        return edl, notes
    key = item.get("storage_key")
    rmap = _MAPS.get(key)
    if rmap is None:
        d = tempfile.mkdtemp(prefix="w2swap_")
        local = os.path.join(d, "track" + os.path.splitext(key)[1])
        storage.download_to(key, local)
        rmap = rhythm.map_for(local, ref={"storage_key": key,
                                          "music_id": new_music_id})
        _remember(key, rmap)
    tl = _tl(edl)
    prog = rhythm_types.to_program(rmap, tl, item, rmap.get("duration"))
    fps = float(((edl.get("canvas") or {}).get("fps")) or 30.0)
    for it in intents:
        p = it.get("params") or {}
        prefix = p.get("prefix")
        if p.get("style", "broll") != "broll":
            notes.append(f"montage {prefix}: type slots re-plan with "
                         f"plan_to_music(replan='{prefix}')")
            continue
        pool = unpack_pool(p.get("pool"))
        sl = montage.slots(prog, p.get("t0", 0.0), p.get("t1", 1e9),
                           fps=fps, lead_f=p.get("lead_f", 1),
                           modes={k: ("pulse" if v == "strobe" else v)
                                  for k, v in dict(montage.SECTION_MODE,
                                                   **(p.get("modes") or {})
                                                   ).items()},
                           on=p.get("on", "kick"), every=p.get("every", 1))
        asg = montage.assign(sl, pool, seed=p.get("seed", 0))
        owned = set(iid for _l, iid in montage.owned(edl, prefix))
        for layer in ("overlays", "motion", "fx"):
            edl[layer] = [i for i in edl.get(layer) or []
                          if i.get("id") not in owned]
        for k, (x, sh, src_t) in enumerate(asg, 1):
            layer, val = montage.broll_value(x, sh, src_t, f"{prefix}_{k}")
            edl.setdefault(layer, []).append(val)
        edl.setdefault("fx", []).append(
            montage.intent(prefix, new_music_id, {k: v for k, v in p.items()
                                                  if k != "prefix"}))
        notes.append(f"montage {prefix} re-cut on the new track: {len(asg)} "
                     "cuts, 1 frame before each hit")
    return edl, notes


def _install_swap():
    try:
        import hooks
        import montage
        montage.set_swap_resolver(_swap_resolver)
        hooks.register("music_swap", montage.on_swap)
    except Exception:  # noqa: BLE001 — the montage then re-plans by verb
        pass


_install_swap()


# ── shape_music ──────────────────────────────────────────────────────────

def _payoff_time(ctx, edl, payoff):
    """Program seconds of the payoff: a number, or the onset of a phrase
    said in the edit."""
    try:
        return float(payoff), None
    except (TypeError, ValueError):
        pass
    from tools_ext import typeset_tools
    pw = _program_words(ctx, edl)
    hit = typeset_tools.match_words(str(payoff), pw)
    if not hit:
        return None, (f"REJECTED: '{payoff}' is not said in this edit; give "
                      "the payoff as program seconds.")
    return float(hit[0]["t0"]), None


def shape_music(ctx, payoff=None, music_id=None, hold_s=None,
                intro_filter=None, stutter_at=None, stutter_slice="1/16",
                stutter_beats=1, dry_run=False):
    """Fit the user's track to the story and shape its bus."""
    import arrangement
    import rhythm_types
    edl = _latest(ctx)
    items = _user_music(edl, music_id)
    if not items:
        from tools_ext import rhythm_tools
        return rhythm_tools._no_track()
    item = items[0]
    try:
        rmap = _track_map(ctx, item)
    except Exception as e:  # noqa: BLE001
        return f"REJECTED: the track could not be analysed ({str(e)[:140]})."
    if not rmap.get("downbeats") or float(rmap.get(
            "downbeat_confidence", rmap.get("confidence", 0))) < \
            rhythm_types.DOWNBEAT_MIN_CONF * 0.5:
        return ("REJECTED: no confident bar grid in this track, so a "
                "bar-aligned edit would land off the bar; shape it by hand "
                "(set_music_fit) or pick the drop moment by ear.")
    tl = _tl(edl)
    prog = float(tl.out_duration or 0.0)
    fps = _fps(ctx, edl)
    ops, lines = [], []
    fields = dict(item)
    pay = None
    if payoff is not None:
        pay, err = _payoff_time(ctx, edl, payoff)
        if err:
            return err
        f, rep = arrangement.fit(item, rmap, pay, prog, fps=fps)
        if f is None:
            return f"REJECTED: {rep}."
        fields.update(f)
        ops.append({"action": "upsert", "layer": "music", "id": item["id"],
                    "value": {"id": item["id"], **f}})
        lines.append(rep)
    drops = arrangement.drop_on_program(fields, rmap)
    drop_p = pay if pay is not None else next(
        (d for d in drops if fields.get("start", 0) <= d <= prog), None)
    mid = item["id"]
    base = f"ar_{mid}"[:30]
    if hold_s not in (None, 0, "0") and drop_p is not None:
        a, b = arrangement.hold_before(drop_p, float(hold_s), fps)
        ops.append({"action": "upsert", "layer": "fx", "id": base + "_h",
                    "value": {"id": base + "_h", "type": "arrange",
                              "stage": "music_bus", "kind": "hold",
                              "music_id": mid, "start": a, "end": b}})
        lines.append(f"held silence {a:.2f}-{b:.2f}s, the drop on {b:.2f}s")
    if intro_filter and drop_p is not None:
        a = max(float(fields.get("start") or 0.0), 0.0)
        if drop_p - a >= 0.3:
            ops.append({"action": "upsert", "layer": "fx",
                        "id": base + "_f",
                        "value": {"id": base + "_f", "type": "arrange",
                                  "stage": "music_bus", "kind": "filter",
                                  "filter": intro_filter, "music_id": mid,
                                  "start": round(a, 3),
                                  "end": round(round(drop_p * fps) / fps,
                                               4)}})
            lines.append(f"{intro_filter} intro {a:.2f}s opening exactly "
                         f"on the drop {drop_p:.2f}s")
    if stutter_at is not None:
        try:
            st = float(stutter_at)
        except (TypeError, ValueError):
            return "REJECTED: stutter_at is program seconds."
        beat = 60.0 / float(rmap.get("bpm") or 120.0)
        ops.append({"action": "upsert", "layer": "fx", "id": base + "_s",
                    "value": {"id": base + "_s", "type": "arrange",
                              "stage": "music_bus", "kind": "stutter",
                              "music_id": mid, "slice": stutter_slice,
                              "start": round(st, 3),
                              "end": round(st + beat * max(1, int(
                                  stutter_beats)), 3)}})
        lines.append(f"{stutter_slice} stutter {st:.2f}s for "
                     f"{stutter_beats} beat(s)")
    if not ops:
        return ("NO CHANGE — name a payoff (program seconds or the words), "
                "a hold_s, an intro_filter or a stutter_at.")
    ops.append({"action": "upsert", "layer": "fx", "id": base + "_fit",
                "value": {"id": base + "_fit", "type": "arrange",
                          "stage": "music_bus", "kind": "fit_intent",
                          "music_id": mid,
                          "params": {"payoff_t": pay, "hold_s": hold_s,
                                     "intro_filter": intro_filter}}})
    text = "; ".join(lines)
    if dry_run:
        return "DRY RUN (nothing written): " + text
    from tools_ext import _write
    res = _write.commit(ctx, ops, "shape_music: " + text)
    return res


# ── set_speed_ramp ───────────────────────────────────────────────────────

def _insert_fps(ctx, item):
    try:
        import agent_tools
        asset, _err = agent_tools._resolve_media_asset(
            ctx, item["asset_key"], CLIP_KINDS)
        if asset and asset.get("fps"):
            return float(asset["fps"])
    except Exception:  # noqa: BLE001
        pass
    return None


def ramp_insert_pieces(item, spans_rep, fps_out):
    """An insert split into constant-rate pieces (``split_parent`` keeps it
    one clip): each piece consumes its source window at its rate, the next
    starting exactly where the last one ended."""
    spans, _rep = spans_rep
    ss = float(item.get("source_start_s") or 0.0)
    total = float(item["duration_s"]) * float(item.get("rate") or 1.0)
    steps = []
    cur = ss
    for sp in spans:
        if sp["start"] > cur + 1e-6:
            steps.append((cur, sp["start"], 1.0))
        steps.append((sp["start"], sp["end"], sp["factor"]))
        cur = sp["end"]
    if cur < ss + total - 1e-6:
        steps.append((cur, ss + total, 1.0))
    out = []
    src = ss
    pid = item.get("split_parent") or item["id"]
    for k, (a, b, f) in enumerate(steps):
        # whole output frames per piece: the joins never drop or repeat one
        n = max(1, int(round((b - a) / f * fps_out)))
        dur = round(n / fps_out, 3)
        piece = {k2: v for k2, v in item.items()
                 if k2 not in ("id", "duration_s", "source_start_s", "rate")}
        piece.update(id=item["id"] if k == 0 else f"{item['id']}~{k}",
                     duration_s=dur, source_start_s=round(src, 3),
                     split_parent=pid)
        if abs(f - 1.0) >= 0.01:
            piece["rate"] = round(f, 4)
        out.append(piece)
        src += dur * f
    return out


def _ramp_insert(ctx, ins_id, low_f, hold, curve, allow_duplicates):
    import agent_tools
    import ramps
    from timeline import Timeline
    prev = ctx.latest_edl()
    edl = json.loads(json.dumps(prev["json"]))
    items = edl.get("inserts") or []
    hit = next((i for i in items if i.get("id") == ins_id), None)
    if hit is None or hit.get("kind") != "video":
        return f"REJECTED: no video insert '{ins_id}' in this edit."
    if hit.get("rate") not in (None, 1, 1.0):
        return (f"REJECTED: insert '{ins_id}' already plays at "
                f"{hit['rate']}x; set_insert_window rate 1 first.")
    fps_out = _fps(ctx, edl)
    fps_src = _insert_fps(ctx, hit) or fps_out
    ss = float(hit.get("source_start_s") or 0.0)
    dur = float(hit["duration_s"])
    try:
        sr = ramps.plan(ss, ss + dur, ramps.swing(low_f, hold, curve),
                        fps_src=fps_src, fps_out=fps_out,
                        prefix=ins_id + "_",
                        allow_duplicates=bool(allow_duplicates))
    except ValueError as err:
        return f"REJECTED: {err}"
    if not sr[0]:
        return "NO CHANGE — that curve stays at 1.0x on this clip."
    pieces = ramp_insert_pieces(hit, sr, fps_out)
    at = items.index(hit)
    edl["inserts"] = items[:at] + pieces + items[at + 1:]
    old_tl = Timeline(prev["json"]["keep"], prev["json"].get("inserts") or [],
                      prev["json"].get("speed") or [])
    new_tl = Timeline(edl["keep"], edl["inserts"], edl.get("speed") or [])
    notes = agent_tools._remap_program_items(edl, old_tl, new_tl)
    rep = sr[1]
    res = ctx.write_edl(edl, f"speed ramp on insert {ins_id}: 1.0 -> "
                        f"{min(rep['factors']):g} -> 1.0 in {len(pieces)} "
                        "pieces")
    if not res.startswith("EDL v"):
        return res
    res += (f"\nProgram length: {old_tl.out_duration:.2f}s -> "
            f"{new_tl.out_duration:.2f}s; the {fps_src:g} fps clip shows a "
            f"fresh frame on every output frame down to {rep['floor']:g}x")
    if rep["clamped"]:
        res += f" (the low was held at {rep['floor']:g}x)"
    if notes:
        res += "\n" + "\n".join(notes)
    return res


def set_speed_ramp(ctx, start=None, end=None, low=0.5, hold=0.3,
                   curve="ease_in_out", allow_duplicates=False, id=None,
                   insert=None):
    """An eased speed swing over a program window as contiguous spans (or
    over a whole b-roll insert as constant-rate pieces)."""
    import ramps
    if insert:
        try:
            return _ramp_insert(ctx, str(insert), float(low), float(hold),
                                curve, allow_duplicates)
        except (TypeError, ValueError) as err:
            return f"REJECTED: {err}"
    if start is None or end is None:
        return "REJECTED: give start and end (program seconds), or insert."
    if not getattr(ctx, "has_main_video", True):
        return ("REJECTED: speed ramps address SOURCE time and need a main "
                "video.")
    edl = _latest(ctx)
    tl = _tl(edl)
    try:
        s, e = float(start), float(end)
        low_f = float(low)
    except (TypeError, ValueError):
        return "REJECTED: start, end and low are numbers."
    if curve not in ramps.CURVES:
        return f"REJECTED: curve is one of {ramps.CURVES}."
    a, b = tl.out_to_src(s), tl.out_to_src(max(s, e - 1e-3))
    if a is None or b is None:
        return "REJECTED: that window is not on kept footage."
    seg = tl.seg_program_range(a)
    if seg and e > seg[1] + 0.02:
        return ("REJECTED: a ramp stays inside one shot; that window crosses "
                f"a cut at {seg[1]:.2f}s.")
    vid = (getattr(ctx, "index", None) or {}).get("video") or {}
    fps_src = float(vid.get("fps") or 30.0)
    fps_out = _fps(ctx, edl)
    prefix = str(id or "rp")
    try:
        spans, rep = ramps.plan(a, b, ramps.swing(low_f, float(hold), curve),
                                fps_src=fps_src, fps_out=fps_out,
                                prefix=prefix + "_",
                                allow_duplicates=bool(allow_duplicates))
    except ValueError as err:
        return f"REJECTED: {err}"
    if not spans:
        return "NO CHANGE — that curve stays at 1.0x."
    old = [sp for sp in edl.get("speed") or []
           if not str(sp.get("id", "")).startswith(prefix + "_")]
    clash = ramps.fits(old, spans)
    if clash:
        return ("REJECTED: the ramp overlaps speed span(s) " +
                ", ".join(map(str, clash)) + "; remove_speed them first.")
    prev = ctx.latest_edl()
    new = dict(prev["json"])
    new["speed"] = sorted(old + spans, key=lambda x: float(x["start"]))
    import agent_tools
    desc = (f"speed ramp {prefix}: 1.0 -> {min(rep['factors']):g} -> 1.0 "
            f"over source {a:.2f}-{b:.2f}s in {rep['steps']} steps")
    res = agent_tools._write_speed(ctx, prev, new, desc,
                                   warn_slow=rep["dup_frames"] > 0)
    if not res.startswith("EDL v"):
        return res
    tail = (f"\nfactors {rep['factors']}; the {fps_src:g} fps source shows a "
            f"fresh frame on every output frame down to {rep['floor']:g}x")
    if rep["clamped"]:
        tail += (f" — the low was held at {rep['floor']:g}x so no frame "
                 "repeats (allow_duplicates=true goes lower, visibly "
                 "stepping)")
    elif rep["dup_frames"]:
        tail += f"; {rep['dup_frames']} output frames repeat"
    return res + tail


# ── animate_still ────────────────────────────────────────────────────────

INSERT_MOVES = {"push_in": "zoom_in", "pull_out": "zoom_out",
                "pan_left": "pan_left", "pan_right": "pan_right"}


def animate_still(ctx, id, path="auto", focus_x=None, focus_y=None,
                  amount=None, ease=None, fit=None, treatment=None):
    """Make a still that sits dead on screen move."""
    edl = _latest(ctx)
    W, H = _canvas(ctx, edl)
    ov = next((o for o in edl.get("overlays") or [] if o.get("id") == id),
              None)
    ins = next((o for o in edl.get("inserts") or [] if o.get("id") == id),
               None)
    mo = next((m for m in edl.get("motion") or [] if m.get("id") == id
               and m.get("template") == "still_motion"), None)
    if path not in ("auto",) + STILL_PATHS:
        return f"REJECTED: path is auto or one of {STILL_PATHS}."
    if ins is not None:
        if ins.get("kind") != "image":
            return (f"REJECTED: insert '{id}' is a clip; it already moves. "
                    "animate_still is for photos.")
        move = INSERT_MOVES.get(path if path != "auto" else "push_in")
        if move is None:
            return (f"REJECTED: an image insert moves by push_in, pull_out, "
                    "pan_left or pan_right.")
        from tools_ext import _write
        return _write.commit(ctx, [{"action": "upsert", "layer": "inserts",
                                    "id": id, "value": {"id": id,
                                                        "motion": move}}],
                             f"animate_still: insert {id} {move}")
    src = ov or mo
    if src is None:
        return (f"REJECTED: no image overlay, image insert or moving still "
                f"'{id}' in this edit.")
    if ov is not None and ov.get("kind") != "image":
        return (f"REJECTED: overlay '{id}' is a clip; it already moves.")
    if ov is not None and ov.get("fit") not in ("cover", "picture"):
        return (f"REJECTED: overlay '{id}' is a picture-in-picture; move it "
                "with set_overlay_motion (x, y, scale curves).")
    key = ov["asset_key"] if ov else (mo.get("params") or {}).get("image")
    p = {}
    if path == "auto":
        face, iw, ih = None, 0, 0
        try:
            import agent_tools
            asset, err = agent_tools._resolve_media_asset(ctx, key,
                                                         IMAGE_KINDS)
            if asset is not None:
                face, iw, ih = _image_face(
                    agent_tools._asset_local_path(ctx, asset))
        except Exception:  # noqa: BLE001 — no face then
            pass
        k = sum(1 for m in edl.get("motion") or []
                if m.get("template") == "still_motion")
        p.update(still_look(iw, ih, face, W, H, k))
    else:
        p["path"] = path
    for name, v in (("focus_x", focus_x), ("focus_y", focus_y),
                    ("amount", amount), ("ease", ease), ("fit", fit),
                    ("treatment", treatment)):
        if v not in (None, ""):
            p[name] = v
    import motion_templates
    if mo is not None:
        params = dict(mo.get("params") or {}, **p)
    else:
        params = dict(p, image=key)
    try:
        params = motion_templates.check_params("still_motion", params)
    except ValueError as err:
        return f"REJECTED: {err}"
    from tools_ext import _write
    if mo is not None:
        ops = [{"action": "upsert", "layer": "motion", "id": id,
                "value": {"id": id, "params": params}}]
    else:
        start = float(ov["start"])
        endt = round(start + float(ov["duration_s"]), 2)
        ops = [{"action": "remove", "layer": "overlays", "id": id},
               {"action": "upsert", "layer": "motion", "id": id,
                "value": {"id": id, "template": "still_motion",
                          "start": start, "end": endt, "params": params,
                          "layer": "below_captions", "mute_captions": False,
                          "purpose": "moving still (was a static cover "
                                     "overlay)"}}]
    what = params.get("path")
    foc = (f" toward ({params['focus_x']:.2f}, {params['focus_y']:.2f})"
           if what in ("push_in", "pull_out") else "")
    return _write.commit(ctx, ops, f"animate_still {id}: {what}{foc}, "
                         f"amount {params.get('amount')}, "
                         f"{params.get('fit')}")


TOOL_SPECS = {
    "plan_to_music": (
        plan_to_music,
        "WRITE: a montage over a program window cut to the user's track (or "
        "the speech stresses when there is no track): one cut per beat slot, "
        "1 frame before the kick, modes per section (strobe only when the "
        "user asked for flashing; pulse; hold >= 1.5 s), no shot cluster "
        "within 6 cuts, scale alternating, cuts inside long clips. style "
        "call_response alternates type on black with b-roll on the hits; arc "
        "runs words on black -> words over picture -> picture. Stills become "
        "moving stills. One batch; replan='mt1' re-plans a recorded montage "
        "(a song swap does it itself). dry_run shows the slots.",
        {"start": {"type": "number"}, "end": {"type": "number"},
         "shots": {"type": "array", "items": {"type": "string"},
                   "description": "asset keys; omit = every clip/image"},
         "modes": {"type": "object",
                   "description": "{section label: strobe|pulse|hold}"},
         "lead_f": {"type": "integer", "minimum": 0, "maximum": 2},
         "on": {"type": "string", "enum": ["auto", "kick", "beat",
                                           "downbeat", "snare", "stress"]},
         "style": {"type": "string", "enum": ["broll", "call_response",
                                              "arc"]},
         "every": {"type": "integer", "minimum": 1, "maximum": 8},
         "seed": {"type": "integer"},
         "music_id": {"type": "string"},
         "clock": {"type": "string", "enum": ["auto", "speech"]},
         "replan": {"type": "string"},
         "dry_run": {"type": "boolean"}}),
    "shape_music": (
        shape_music,
        "WRITE: bend the user's track to the story: payoff (program seconds "
        "or the words) solves the music item's offset/start/end/fades so "
        "the DROP lands on it and the bed ends on a bar line; hold_s holds "
        "silence before it; intro_filter lowpass|highpass opens exactly on "
        "the drop; stutter_at slices a hit. User tracks only.",
        {"payoff": {"type": "string"}, "music_id": {"type": "string"},
         "hold_s": {"type": "number", "minimum": 0, "maximum": 2},
         "intro_filter": {"type": "string", "enum": ["lowpass",
                                                     "highpass"]},
         "stutter_at": {"type": "number"},
         "stutter_slice": {"type": "string", "enum": ["1/8", "1/16"]},
         "stutter_beats": {"type": "integer", "minimum": 1, "maximum": 4},
         "dry_run": {"type": "boolean"}}),
    "set_speed_ramp": (
        set_speed_ramp,
        "WRITE: an eased slow-motion swing over a program window inside one "
        "shot: 1.0 -> low -> 1.0 (hold = share spent at the low) as <= 12 "
        "contiguous speed spans; or over a whole video insert (insert=id) "
        "as constant-rate pieces. The low is held where the source still "
        "shows a fresh frame each output frame (a 60p clip in a 30p program "
        "reaches 0.5x) unless allow_duplicates. Speech under it is "
        "stretched (pitch kept).",
        {"start": {"type": "number"}, "end": {"type": "number"},
         "low": {"type": "number", "minimum": 0.25, "maximum": 0.95},
         "hold": {"type": "number", "minimum": 0, "maximum": 0.8},
         "curve": {"type": "string", "enum": list(("ease_in_out", "ease_in",
                                                   "ease_out", "linear"))},
         "allow_duplicates": {"type": "boolean"},
         "id": {"type": "string"}, "insert": {"type": "string"}}),
    "animate_still": (
        animate_still,
        "WRITE: a photo that sits dead still on screen moves: a cover image "
        "overlay becomes a moving still (push/pull toward a face it finds, "
        "pan a panorama, rise a tall scan, drift), an image insert gets a "
        "camera move, a moving still is re-aimed.",
        {"id": {"type": "string"},
         "path": {"type": "string", "enum": ["auto"] + list(STILL_PATHS)},
         "focus_x": {"type": "number", "minimum": 0, "maximum": 1},
         "focus_y": {"type": "number", "minimum": 0, "maximum": 1},
         "amount": {"type": "number", "minimum": 0.03, "maximum": 0.4},
         "ease": {"type": "string", "enum": ["in_out", "out", "linear",
                                            "settle"]},
         "fit": {"type": "string", "enum": ["cover", "contain_blur",
                                           "contain"]},
         "treatment": {"type": "string", "enum": ["none", "archive"]}}),
}
REQUIRED_ARGS = {"animate_still": ["id"]}
WRITE_TOOLS = {"plan_to_music", "shape_music", "set_speed_ramp",
               "animate_still"}
COMPACT = {
    "plan_to_music": "WRITE: montage cut to the user's track (else speech "
                     "stresses): cuts 1 frame before each kick, strobe/"
                     "pulse/hold per section, no cluster within 6, "
                     "call_response/arc type styles; one batch; replan.",
    "shape_music": "WRITE: user track fitted so its drop lands on the payoff "
                   "(bar-aligned), held silence before, low-pass intro "
                   "opening on the drop, stutters.",
    "set_speed_ramp": "WRITE: eased slow-mo swing 1.0->low->1.0 as speed "
                      "spans, no duplicated frames by default.",
    "animate_still": "WRITE: make a static photo move (Ken Burns toward a "
                     "face, pan, rise, drift).",
}
