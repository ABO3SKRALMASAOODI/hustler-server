"""Department ``world``: track 4B's verbs (INTERFACES_W4.md §4).

  add_ui_scene     WRITE: an explainer told through interface — glass cards,
                   a three-state karaoke pill, comment threads, dashboards
                   (counters landing on the spoken number, bars from the
                   user's data), editor windows and media slots playing real
                   recordings — as persistent objects that morph between
                   scenes and open as irises from the element that caused
                   them. Solved here (ui_scene.solve: layout, morphs, iris
                   origins, counters, karaoke) into ONE motion item
                   (template ``ui_scene``) plus a ``ui_media`` fx item per
                   media slot.
  remove_ui_scene  WRITE: remove UI scenes (and their slot footage) by id.

Figures are never invented: a number on screen is bound to the user's
``data``, said in the kept transcript, or written in the user's request
(``ui_scene.check_numbers``); anything else is refused with its name.
Nothing here adds music, a sound or a zoom.
"""

import json
import re

DEPARTMENT = "world"
VERBS = ("add_ui_scene", "remove_ui_scene")


def _rejected(msg):
    return f"REJECTED: {msg}"


def _edl(ctx):
    row = ctx.latest_edl()
    return row["json"], row["version"]


def _obj(v, name):
    if v is None or isinstance(v, (dict, list)):
        return v
    if isinstance(v, str):
        s = v.strip()
        if s.startswith(("{", "[")):
            try:
                return json.loads(s)
            except ValueError:
                raise ValueError(f"{name} is not valid JSON")
        return v
    raise ValueError(f"{name} must be JSON")


def _design(edl):
    import motion_engine
    ratio = ((edl.get("frame") or {}).get("ratio")
             or (edl.get("canvas") or {}).get("ratio") or "16:9")
    out = {"9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350),
           "4:3": (1440, 1080)}.get(ratio, (1920, 1080))
    return motion_engine.design_size(*out)


def _new_id(items, prefix):
    used = {str(i.get("id")) for i in items if isinstance(i, dict)}
    n = 1
    while f"{prefix}{n}" in used:
        n += 1
    return f"{prefix}{n}"


def _program_s(edl):
    import schemas
    try:
        return float(schemas.program_duration(edl) or 0.0)
    except Exception:  # noqa: BLE001
        return 0.0


def add_ui_scene(ctx, scenes, start=None, end=None, data=None, theme="dark",
                 backdrop="solid", media=None, accent=None, id=None):
    import device_planner
    import ui_scene
    from tools_ext import _write
    from tools_ext.broll_tools import _find_word
    edl, _v = _edl(ctx)
    try:
        scenes = _obj(scenes, "scenes")
        media = _obj(media, "media") or {}
        data = _obj(data, "data")
    except ValueError as e:
        return _rejected(str(e))
    if not isinstance(scenes, list) or not scenes:
        return _rejected("scenes is a list of {at or word, layout, objects}")
    if theme not in ("dark", "light"):
        return _rejected("theme is dark or light")
    if backdrop not in ("none", "dim", "solid"):
        return _rejected("backdrop is none, dim or solid")
    idx = getattr(ctx, "index", None) or {}
    prog = _program_s(edl)
    # the window: given, or from the first scene's word to the program end
    try:
        anchors = []
        for k, sc in enumerate(scenes):
            if not isinstance(sc, dict):
                raise ValueError(f"scene {k + 1} is not an object")
            if sc.get("word"):
                got = _find_word(edl, idx, sc["word"], sc.get("near"))
                if not got:
                    raise ValueError(f"scene {k + 1}: '{sc['word']}' is not "
                                     "in the kept transcript")
                anchors.append(float(got[0]))
            else:
                anchors.append(None)
        s = float(start) if start is not None else (
            anchors[0] if anchors[0] is not None else None)
        if s is None:
            raise ValueError("give start (program seconds) or anchor the "
                             "first scene on a word")
        e = float(end) if end is not None else min(prog, s + 8.0)
    except (TypeError, ValueError) as x:
        return _rejected(str(x))
    if not (0 <= s < e <= prog + 1e-6):
        return _rejected(f"the window {s:g}-{e:g}s is outside the program "
                         f"(0-{prog:g}s)")
    solved = []
    for sc, a in zip(scenes, anchors):
        sc = json.loads(json.dumps(sc))
        if a is not None:
            sc["at"] = round(a - s, 3)
        solved.append(sc)
    words = [{"w": w["w"], "t0": round(w["t0"] - s, 3)}
             for w in device_planner.program_words(edl, idx)
             if s - 0.05 <= w["t0"] < e]
    try:
        ui_scene.bind_objects(solved, data)
        bad = ui_scene.check_numbers(
            solved, device_planner.program_words(edl, idx),
            getattr(ctx, "user_message", None))
        if bad:
            return _rejected(bad)
        W, H = _design(edl)
        graph = ui_scene.solve(solved, e - s, W, H, words=words, theme=theme,
                               accent=accent)
    except ValueError as x:
        return _rejected(str(x))
    slots = ui_scene.media_slots(graph)
    if slots:
        try:
            import remote
            if remote.executor_supports("fx:ui_media") is False:
                return ("UNAVAILABLE: the render service does not draw "
                        "footage in UI slots yet (a deploy is rolling "
                        "out); nothing was written.")
        except Exception:  # noqa: BLE001
            pass
    missing = [sl for sl in slots if sl not in media]
    if missing:
        return _rejected("media slot(s) " + ", ".join(missing) + " need "
                         "footage: media={slot: {asset_key | src_t, "
                         "offset_s}}")
    mid = str(id or _new_id(edl.get("motion") or [], "ui"))
    item = {"id": mid, "template": "ui_scene", "start": round(s, 2),
            "end": round(e, 2), "layer": "below_captions",
            "params": {"graph": graph, "shown": ui_scene.shown_texts(graph),
                       "backdrop": backdrop,
                       # what the graph was solved from: another aspect
                       # re-solves the layout instead of scaling pixels
                       "spec": {"scenes": solved, "words": words,
                                "theme": theme, "accent": accent}},
            "purpose": "UI explainer (add_ui_scene)"}
    ops = [{"action": "upsert", "layer": "motion", "id": mid,
            "value": item}]
    taken = list(edl.get("fx") or [])
    for sl in slots:
        m = media[sl]
        if not isinstance(m, dict):
            return _rejected(f"media.{sl} is {{asset_key | src_t, "
                             "offset_s}}")
        if m.get("asset_key"):
            src = {"kind": "asset", "asset_key": m["asset_key"],
                   "offset_s": float(m.get("offset_s") or 0.0)}
        elif m.get("src_t") is not None:
            src = {"kind": "source", "src_t": float(m["src_t"])}
        else:
            return _rejected(f"media.{sl} needs asset_key or src_t")
        fid = _new_id(taken, "um")
        fx = {"id": fid, "type": "ui_media", "stage": "screen",
              "start": round(s, 2), "end": round(e, 2), "motion_id": mid,
              "slot": sl, "source": src, "fit": m.get("fit") or "cover",
              "device": "ui_media_slot"}
        taken.append(fx)
        ops.append(_write.upsert_fx(fx))
    res = _write.commit(ctx, ops, f"add_ui_scene {mid}: {len(scenes)} "
                        f"scene(s), {len(graph['objects'])} object(s)")
    if str(res).startswith("REJECTED"):
        return res
    notes = []
    persist = [o["id"] for o in graph["objects"]
               if any(sg[12] == "morph" for sg in o["segs"])]
    if persist:
        notes.append("persistent (morph, never cut): "
                     + ", ".join(persist[:8]))
    if graph["irises"]:
        notes.append("irises: " + ", ".join(
            f"{i['id']} from {i['from']} at {i['t'] + s:.2f}s"
            for i in graph["irises"]))
    counts = [(o["id"], o["count"]) for o in graph["objects"]
              if o.get("count")]
    for oid, c in counts:
        how = ("ON the spoken number" if c["landed_on_word"] else
               "0.5 s after it shows (the number is said as it appears: "
               "start the scene a word earlier to land ON it)"
               if c.get("said") else
               "0.9 s after it shows (the number is not said near it)")
        notes.append(f"{oid} counts to {o_fmt(c)} landing {how} at "
                     f"{c['t1'] + s:.2f}s")
    return res + ("\n" + "\n".join("  " + n for n in notes) if notes
                  else "")


def o_fmt(c):
    return str(c.get("fmt") or c.get("to"))


def remove_ui_scene(ctx, ids):
    from tools_ext import _write
    edl, _v = _edl(ctx)
    if isinstance(ids, str):
        ids = [x.strip() for x in ids.split(",") if x.strip()]
    if not isinstance(ids, list) or not ids:
        return _rejected("ids is a list of UI scene ids")
    have = {str(m.get("id")) for m in edl.get("motion") or []
            if isinstance(m, dict) and m.get("template") == "ui_scene"}
    missing = [i for i in ids if str(i) not in have]
    if missing:
        return _rejected("no UI scene with id " + ", ".join(map(str, missing))
                         + (f" (UI scenes: {', '.join(sorted(have))})"
                            if have else " (this edit has none)"))
    ops = []
    for f in edl.get("fx") or []:
        if isinstance(f, dict) and f.get("type") == "ui_media" and \
                str(f.get("motion_id")) in {str(i) for i in ids}:
            ops.append(_write.remove_fx(f["id"]))
    ops += [{"action": "remove", "layer": "motion", "id": str(i)}
            for i in ids]
    return _write.commit(ctx, ops, f"removed UI scene(s) {', '.join(ids)}")


_OBJ = {"type": "object", "properties": {
    "id": {"type": "string", "description": "same id in two scenes = the "
           "same object (it morphs)"},
    "kind": {"type": "string", "enum": ["card", "pill", "comment", "metric",
                                        "bars", "window", "media", "text"]},
    "title": {"type": "string"}, "body": {"type": "string"},
    "text": {"type": "string"}, "label": {"type": "string"},
    "name": {"type": "string"}, "value": {"type": "string"},
    "delta": {"type": "string"},
    "state": {"type": "string", "enum": ["idle", "live", "done"]},
    "from": {"type": "string", "description": "iris open from this object"},
    "bind": {"description": "a data name, or {row, col, key?}"},
    "rows": {"type": "array", "items": {"type": "object"}},
    "rows_from": {"type": "object"},
    "lines": {"type": "array", "items": {"type": "string"}},
    "aspect": {"type": "number"}}, "required": ["id", "kind"]}

TOOL_SPECS = {
    "add_ui_scene": (
        add_ui_scene,
        "WRITE: an explainer told through interface. scenes=[{at (s into "
        "the item) or word, layout stack|grid|focus|thread|row, focus?, "
        "objects:[{id, kind card|pill|comment|metric|bars|window|media|"
        "text, ...}]}]. The SAME id in consecutive scenes is one object "
        "that morphs; from=<id> opens a new object as an iris from it. "
        "pill state idle|live|done lights its words as they are said; a "
        "metric counts to its value landing on the spoken number. Numbers "
        "come only from data (CSV or {name: number}; bind) or the words — "
        "never invented. media={slot: {asset_key|src_t, offset_s}} plays "
        "real recordings in media slots. backdrop solid|dim|none.",
        {"scenes": {"type": "array", "items": {"type": "object"},
                    "description": "[{at|word, layout, focus?, objects: "
                    "[{id, kind, title|body|text|label|name|value|delta|"
                    "state|from|bind|rows|rows_from|lines|aspect}]}]"},
         "start": {"type": "number"}, "end": {"type": "number"},
         "data": {"description": "CSV text, rows or {name: number}"},
         "theme": {"type": "string", "enum": ["dark", "light"]},
         "backdrop": {"type": "string", "enum": ["solid", "dim", "none"]},
         "media": {"type": "object"}, "accent": {"type": "string"},
         "id": {"type": "string"}}),
    "remove_ui_scene": (
        remove_ui_scene,
        "WRITE: remove UI scenes (and the footage in their slots) by id.",
        {"ids": {"type": "array", "items": {"type": "string"}}}),
}
REQUIRED_ARGS = {"add_ui_scene": ["scenes"], "remove_ui_scene": ["ids"]}
WRITE_TOOLS = {"add_ui_scene", "remove_ui_scene"}
COMPACT = {
    "add_ui_scene": "WRITE: UI explainer: scenes of persistent objects "
                    "(cards, pill, comments, metrics, bars, windows, media) "
                    "that morph and iris; numbers from data or words only.",
    "remove_ui_scene": "WRITE: remove UI scenes by id.",
}
del re
