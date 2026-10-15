"""Department ``direction`` (Wave 2, track 2F): the agent layer, v2.

  suggest_story      up to 8 story-bound proposals for THIS edit: type
                     lockups in voices new to the project, a callback rhyme,
                     a colour turn on the payoff (B&W as a device), the
                     user's drop on the payoff, a montage, a loop rhyme, and
                     Wave 1 punctuation; each says what is new about it
  apply_format_pack  a genre pack (A manifesto, B trend desk, C typographic
                     portrait, H lyric film; auto = the user's words, else
                     the measured family, else none) applied as ONE batch
                     of the proposals it ranks; verbs that need the footage
                     solved are listed as the next calls
  analyze_reference  "make it like this": a reel the user pasted, read for
                     its device classes in order (never its marks) and
                     mapped onto this edit's moments
  render_variants    three type voices for one lockup as stills on the
                     real frame, in seconds, for the user to pick

Packs and proposals are seeded by the story, so two shorts never get the
same plan; the user's words always win over a pack.
"""

import json
import os

DEPARTMENT = "direction"
# Proposals apply_format_pack writes when the agent picks none
APPLY_N = {"calm": 2, "designed": 4, "charged": 6}
VERBS = ("suggest_story", "apply_format_pack", "analyze_reference",
         "render_variants")


def _latest(ctx):
    return json.loads(json.dumps(ctx.latest_edl()["json"]))


def _tl(edl):
    from timeline import Timeline
    return Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                    edl.get("speed") or [])


def _pack(ctx, edl, pack):
    import formats
    if pack in (None, "", "auto"):
        p, why = formats.select(getattr(ctx, "index", None), edl,
                                getattr(ctx, "user_message", "") or "")
        return p, why
    if str(pack).lower() == "none":
        return None, "no pack (composed freely)"
    try:
        return formats.load(pack), "named"
    except KeyError as e:
        return False, str(e)


def _music_maps(ctx, edl):
    """{music id: PROGRAM-clock map} of the user's tracks already analysed
    in this turn (analyze_music / plan_to_music); none analysed = none."""
    try:
        import rhythm_types
        from tools_ext import montage_tools
        out = {}
        for m in montage_tools._user_music(edl):
            rm = montage_tools._MAPS.get(m.get("storage_key"))
            if rm:
                out[m["id"]] = rhythm_types.to_program(
                    rm, _tl(edl), m, rm.get("duration"))
        return out
    except Exception:  # noqa: BLE001
        return {}


def _plan(ctx, edl, intensity, pack, max_n=8):
    import creative
    import story_planner
    return story_planner.suggest(
        edl, getattr(ctx, "index", None) or {}, intensity=intensity,
        pack=pack, user_text=getattr(ctx, "user_message", "") or "",
        maps=_music_maps(ctx, edl), max_n=max_n,
        batch=creative.batch_ledger(ctx))


def _solve_ops(ctx, edl, cands):
    """The candidates' operations, typeset lockups SOLVED the way
    add_typeset solves one (faces measured, an 'auto' voice picked, the
    family varied, the lockup before it ended, a line no layout fits
    dropped) in program order on a working copy, so each sees the ones
    before it: (ops, applied candidates, notes)."""
    import copy
    import device_planner
    from tools_ext import typeset_tools
    work = copy.deepcopy(edl)
    pw = device_planner.program_words(work, getattr(ctx, "index", None)
                                      or {}, _tl(work))
    ops, used, notes = [], [], []
    order = sorted((c for c in cands if c.get("op") or c.get("ops")),
                   key=lambda c: float(c.get("at") or 0.0))
    for c in order:
        cops = list(c.get("ops") or []) or [c["op"]]
        out = []
        for op in cops:
            val = op.get("value") or {}
            if op.get("layer") == "motion" and op.get("action") == "upsert" \
                    and val.get("template") == "typeset":
                solved, why = typeset_tools.solve_planned(ctx, work, op, pw)
                if not solved:
                    notes.append(f"{c['device']}@{float(c.get('at') or 0):.1f}s "
                                 f"not set: {why}")
                    out = None
                    break
                out += solved
            else:
                out.append(op)
        if not out:
            continue
        for op in out:
            _apply_local(work, op)
        ops += out
        used.append(c)
    return ops, used, notes


def _apply_local(edl, op):
    """A working copy's view of an item upsert (what the batch will do)."""
    layer = op.get("layer")
    if op.get("action") != "upsert" or not isinstance(edl.get(layer, []),
                                                      list):
        return
    items = edl.setdefault(layer, [])
    at = next((i for i, it in enumerate(items)
               if it.get("id") == op.get("id")), None)
    val = dict(items[at] if at is not None else {}, **(op.get("value") or {}))
    if at is None:
        items.append(val)
    else:
        items[at] = val


def _line(k, c):
    what = (c.get("call") or {}).get("verb") or (c.get("op") or {}).get(
        "layer", "")
    return (f"{k}. [{c['beat']}] {c['device']} at {float(c.get('at') or 0):.2f}s"
            f" via {what}: {c.get('reason')} — new: {c.get('invention')}"
            f" (like {c.get('exemplar')})")


def suggest_story(ctx, intensity="designed", pack="auto", max_n=8):
    """READ: story-bound proposals for this edit."""
    edl = _latest(ctx)
    p, why = _pack(ctx, edl, pack)
    if p is False:
        return f"REJECTED: {why}"
    try:
        cands = _plan(ctx, edl, intensity, p, max_n)
    except ValueError as e:
        return f"REJECTED: {e}"
    if not cands:
        return "Nothing to propose: the program has no words or moments yet."
    ctx._w2_story = {"cands": cands, "pack": (p or {}).get("id")}
    head = (f"Pack {p['id']} ({p['name']}): {why}." if p else
            f"No pack: {why}.")
    pk = (p or {}).get("id") or "none"
    return "\n".join([head] + [_line(k + 1, c) for k, c in
                               enumerate(cands)] +
                     [f"apply_format_pack(pack='{pk}', intensity="
                      f"'{intensity}', picks=[...]) writes the chosen ones "
                      "in one batch (pass the same pack and intensity: the "
                      "numbers are this list's); 'via <verb>' ones are "
                      "calls. Pick what serves THIS story — fewer is fine."])


def apply_format_pack(ctx, pack="auto", picks=None, intensity="designed"):
    """WRITE: the pack's ranked proposals (or the picked ones) in one
    batch; footage-solved devices are returned as the next calls."""
    from tools_ext import _write
    edl = _latest(ctx)
    p, why = _pack(ctx, edl, pack)
    if p is False:
        return f"REJECTED: {why}"
    stored = getattr(ctx, "_w2_story", None)
    cands = (stored or {}).get("cands") if picks else None
    if not cands:
        cands = _plan(ctx, edl, intensity, p)
    left = []
    if picks:
        try:
            idx = [int(i) - 1 for i in (picks if isinstance(picks, list)
                                         else [picks])]
        except (TypeError, ValueError):
            return "REJECTED: picks are proposal numbers from suggest_story."
        cands = [cands[i] for i in idx if 0 <= i < len(cands)]
    else:
        # no picks: restraint by default — the best few for the intensity
        # (a talking short usually earns one or two devices beside its
        # lockups); the rest stay proposals the agent can still pick
        n = APPLY_N.get(intensity, 4)
        cands, left = cands[:n], cands[n:]
    calls = [c["call"] for c in cands
             if c.get("call") and not (c.get("op") or c.get("ops"))]
    ops, used, notes = _solve_ops(ctx, edl, cands)
    if not ops and not calls:
        return "NO CHANGE — nothing to apply." + (
            " " + "; ".join(notes) + "." if notes else "")
    res = "NO CHANGE — every pick is a call."
    if ops:
        name = (p or {}).get("name", "story")
        res = _write.commit(ctx, ops, f"{name} plan: " + ", ".join(
            f"{c['device']}@{float(c.get('at') or 0):.1f}s" for c in used))
        if res.startswith("REJECTED"):
            return res
    if notes:
        res += "\nNOTE: " + "; ".join(notes) + "."
    if left:
        res += ("\nNot applied (restraint; pick them by number from "
                "suggest_story if this story earns them): " + ", ".join(
                    f"{c['device']}@{float(c.get('at') or 0):.1f}s"
                    for c in left) + ".")
    if calls:
        res += "\nNext calls (they solve against the footage): " + "; ".join(
            f"{c['verb']}({json.dumps(c.get('args') or {})})" for c in calls)
    return res


def analyze_reference(ctx, asset_key):
    """READ: a pasted reel's device classes, mapped onto this edit."""
    import agent_tools
    import device_planner
    import reference_devices
    asset, err = agent_tools._resolve_media_asset(
        ctx, asset_key, ("video_clip", "generated_video"))
    if err:
        return err
    try:
        path = agent_tools._asset_local_path(ctx, asset)
        ref = reference_devices.analyze(path)
    except Exception as e:  # noqa: BLE001
        return f"Could not read the reel ({str(e)[:140]})."
    edl = _latest(ctx)
    plan = device_planner.moments(edl, getattr(ctx, "index", None) or {})
    moves = reference_devices.transfer(ref["sequence"], plan)
    lines = [f"Reel: {ref['duration_s']}s, {ref['shots']} shots (ASL "
             f"{ref['asl_s']}s); device classes in order: " +
             " -> ".join(ref["classes"]) or "none found"]
    if ref["flashing"]:
        lines.append("The reel strobes/flashes past 3 a second: matching it "
                     "is the user's opt-in (the export is flagged "
                     "contains_flashing).")
    lines.append("Mapped onto this edit (classes only — never its text, "
                 "logos, handles or footage):")
    lines += [f"  {m['at']:.2f}-{m['end']:.2f}s {m['cls']} -> {m['card']} "
              f"({m['verb']})" + (f" on the {m['moment']}" if m['moment']
                                  else "") for m in moves[:16]]
    return "\n".join(lines)


def render_variants(ctx, id=None, n=3, axis="voice"):
    """Three type voices for one lockup as stills on the real frame."""
    import variants_edit
    import motion_tools
    from typeset import layoutdoc
    edl = _latest(ctx)
    try:
        br = variants_edit.branches(edl, axis=axis, n=n, item_id=id)
    except ValueError as e:
        return f"REJECTED: {e}"
    W, H = motion_tools._canvas_size(ctx, edl)
    item = next(m for m in edl.get("motion") or []
                if m.get("template") == "typeset" and (id is None or
                                                       m.get("id") == id))
    mid = (float(item["start"]) + float(item["end"])) / 2.0
    frame = _program_frame(ctx, edl, mid, W, H)
    try:
        import library
        faces = [tuple(d["box"]) for d in (library.yunet_faces(frame) or [])]
    except Exception:  # noqa: BLE001 — solved without a face then
        faces = []
    images, labels = [], []
    # more candidates than asked: a voice whose faces do not fit this frame
    # falls back to the plainest one, and a sheet that labels that as the
    # voice asked for misleads the pick (W2 review: 'techno_crt' showed
    # whisper_shout)
    want = max(2, min(4, int(n or 3)))
    from typeset import voices as V
    cands = [v for v, _e in br] + [
        v for v in variants_edit.distinct_voices(
            str((item.get("params") or {}).get("text", "")),
            min(len(V.VOICES), want + 6))
        if v not in [x for x, _e in br]]
    for voice in cands:
        it = json.loads(json.dumps(item))
        it["params"] = dict(it.get("params") or {}, voice=voice,
                            layout="auto")
        doc = layoutdoc.solve_item(it, W, H, faces=faces)
        rep = doc.get("solver_report") or {}
        if not doc.get("runs") or rep.get("voice_fallback") or \
                doc.get("voice") != voice:
            continue
        images.append(variants_edit.raster(doc, frame))
        labels.append(f"{voice} ({doc.get('family')})")
        if len(images) >= want:
            break
    if not images:
        return "No voice fits this frame for that line."
    out = os.path.join(getattr(ctx, "workdir", None) or "/tmp",
                       f"variants_{item['id']}.jpg")
    variants_edit.sheet(images, labels, out)
    if hasattr(ctx, "pending_images"):
        ctx.pending_images.append((f"TYPE VOICES for '{item['params']['text']}'"
                                   f" — {', '.join(labels)}", out))
    return (f"{len(labels)} voices that fit this frame, on the real frame "
            "(stills; materials and motion render with the pick): "
            + "; ".join(labels) +
            f". Apply one with set_typeset(id='{item['id']}', voice=...).")


def _program_frame(ctx, edl, t, W, H):
    """The program picture at program second ``t``, cover-cropped to the
    canvas (a preview plate; the render frames it properly)."""
    import numpy as np
    from PIL import Image
    import library
    src = _tl(edl).out_to_src(t)
    try:
        f = library.frame_at(ctx.proxy_path(), src or 0.0, 960)
    except Exception:  # noqa: BLE001
        return np.full((H, W, 3), 40, np.uint8)
    img = Image.fromarray(f)
    k = max(W / img.width, H / img.height)
    img = img.resize((int(img.width * k + 0.5), int(img.height * k + 0.5)))
    x0, y0 = (img.width - W) // 2, (img.height - H) // 2
    return np.asarray(img.crop((x0, y0, x0 + W, y0 + H)))


TOOL_SPECS = {
    "suggest_story": (
        suggest_story,
        "READ: up to 8 proposals bound to THIS story's beats (hook, turn, "
        "payoff, callback, loop): type lockups in voices new to the project, "
        "a callback rhyme, a colour turn on the payoff (B&W only as a "
        "device), the user's drop on the payoff, a montage, a loop rhyme, "
        "Wave 1 punctuation; each with what is new about it. pack auto|A|B|"
        "C|H|none (the user's words win).",
        {"intensity": {"type": "string", "enum": ["calm", "designed",
                                                  "charged"]},
         "pack": {"type": "string", "enum": ["auto", "A", "B", "C", "H",
                                             "none"]},
         "max_n": {"type": "integer", "minimum": 1, "maximum": 8}}),
    "apply_format_pack": (
        apply_format_pack,
        "WRITE: the pack's proposals (or picks=[numbers] from "
        "suggest_story) in ONE batch; devices that must be solved against "
        "the footage (montage, drop on the payoff, loop) come back as the "
        "next calls.",
        {"pack": {"type": "string", "enum": ["auto", "A", "B", "C", "H",
                                             "none"]},
         "picks": {"type": "array", "items": {"type": "integer"}},
         "intensity": {"type": "string", "enum": ["calm", "designed",
                                                  "charged"]}}),
    "analyze_reference": (
        analyze_reference,
        "READ: 'make it like this' — a reel the user uploaded, read for its "
        "device classes in order (negatives, flashes, B&W with a kept "
        "colour, strobes, type on black, fast cuts) and mapped onto this "
        "edit's moments; never its text, logos or footage.",
        {"asset_key": {"type": "string"}}),
    "render_variants": (
        render_variants,
        "WRITE-free: three type voices for one typeset lockup as stills on "
        "the real frame, attached for you to show the user; apply the pick "
        "with set_typeset.",
        {"id": {"type": "string"},
         "n": {"type": "integer", "minimum": 2, "maximum": 4},
         "axis": {"type": "string", "enum": ["voice"]}}),
}
REQUIRED_ARGS = {"analyze_reference": ["asset_key"]}
WRITE_TOOLS = {"apply_format_pack"}
COMPACT = {
    "suggest_story": "READ: <=8 proposals bound to this story's beats with "
                     "what is new about each; pack auto|A|B|C|H|none.",
    "apply_format_pack": "WRITE: a genre pack's proposals (or picks) in one "
                         "batch; footage-solved ones returned as calls.",
    "analyze_reference": "READ: a pasted reel's device classes in order, "
                         "mapped onto this edit (never its marks).",
    "render_variants": "three type voices for a lockup as stills on the "
                       "real frame.",
}
