"""Story, direction, sound and typesetting for the editor already in the turn."""
import hashlib
import json
import math


def _edit(ctx, ops, note):
    from harness2 import commit
    return commit.edit(ctx, ops, note=note).text()


def plan_piece(ctx, brief="", start=0, end=None, keep=None):
    """Read the source and the surviving script. The editor can return its cut."""
    import agent_tools
    import read_tools
    from tools_ext import format_tools
    if keep:
        return _edit(ctx, [{"keep": keep}], brief or "Cut the selected story beats")
    evidence = agent_tools.get_editorial_map(ctx, start=start, end=end, limit=24)
    script = read_tools.get_kept_transcript(ctx)
    from harness2.measure import program_clock
    clock = program_clock(ctx.latest_edl()["json"])
    picture = ""
    if clock.out_duration > 0 and agent_tools._can_receive_images(ctx):
        times = [clock.out_duration * part for part in (0.03, 0.23, 0.46, 0.69, 0.94)]
        picture = agent_tools.look_at(ctx, output_times=times)
    beats = format_tools.suggest_story(ctx, pack="none")
    return (f"Brief: {brief}\nSource time:\n{evidence}\nCurrent cut:\n{script}\n"
            f"Picture evidence:\n{picture}\nStory proposals:\n{beats}\n"
            "Build a script from what happens or is said. Open on the first meaningful action "
            "or clause, keep what makes the turn intelligible, and leave room for the payoff. "
            "Choose the pictures that show the things the footage names. Use source intervals "
            "in keep to apply your selection.")


def direct(ctx, moments=None, family=None):
    from scene import palette
    if not moments:
        if family:
            return palette.menu(families=[family])
        palette._discover()
        families = {}
        for name, entry in sorted(palette.DEVICES.items()):
            families.setdefault(entry["family"], []).append(name)
        return "\n".join(f"{name}: {', '.join(names)}" for name, names in families.items())
    return _edit(ctx, [{"direct_moments": moments}], "Direct the current cut")


def direct_moments(stage, moments, env, index):
    """Upsert a named device per beat on the latest cut, including on retries."""
    from harness2.measure import program_clock
    from scene import palette
    edl = stage.snapshot()
    length = program_clock(edl).out_duration
    by_id = {m["id"]: m for m in edl.get("direction") or []}
    for moment in moments:
        if not isinstance(moment, dict):
            continue
        key = str(moment.get("id") or "").strip()
        if moment.get("remove"):
            by_id.pop(key, None)
            continue
        try:
            a, b = float(moment["start"]), float(moment["end"])
            if not math.isfinite(a) or not math.isfinite(b) or length <= 0:
                raise ValueError
        except (KeyError, ValueError, TypeError):
            env.finding(f"Moment {key or index} needs a start and end on the current cut")
            continue
        a = min(max(0., a), max(0., length-1/30))
        b = min(length, max(a+1/30, b))
        device = palette.resolve(moment.get("device") or "spotlight")
        key = key or f"beat-{a:.3f}-{device}"
        by_id[key] = {"id": key, "device": device, "start": a, "end": b,
                      "text": moment.get("text"), "color": moment.get("color"), "role": moment.get("role"),
                      "controls": {k: value for k, value in (moment.get("controls") or {}).items()
                                   if k in CONTROLS}}
    edl["direction"] = list(by_id.values())
    stage.write_edl(edl, "Place palette devices on the directed moments")
    env.measures["direction"] = [(m["id"], m["device"], m["start"], m["end"])
                                 for m in by_id.values()]
    return True


def score(ctx, brief="", music=True, sounds=True, kind=None):
    departments = [d for d, enabled in (("music", music), ("sound", sounds)) if enabled]
    if not departments:
        return "The score is as it was."
    return _edit(ctx, [{"finish": {"departments": departments, "brief": brief,
                                   "kind": kind, "workflow_pool": True}}],
                 "Score the cut to its picture and speech")


def dress(ctx, words=None, captions=None):
    ops = []
    if captions is not None:
        ops.append({"tool": "add_captions", "args": {"mode": "from_transcript" if captions else "off"}})
    for row in words or []:
        if not isinstance(row, dict):
            continue
        args = {k: row[k] for k in ("id", "text", "start", "end", "voice", "position", "ink")
                if row.get(k) is not None}
        if not args.get("id"):
            stamp = json.dumps([args.get("start"), args.get("text")], ensure_ascii=False)
            args["id"] = "display-" + hashlib.sha256(stamp.encode()).hexdigest()[:12]
        ops.append({"tool": "add_typeset", "args": args})
    return _edit(ctx, ops, "Set display words and captions") if ops else "Pass display words or a captions choice."


S = {"type": "string"}
N = {"type": "number"}
B = {"type": "boolean"}


CONTROLS = {"amount": N, "width": N, "px": N, "angle": N, "lift": N, "count": N,
            "from": N, "to": N, "seat": S, "surface": S, "follow": S, "mode": S}


def rows(properties):
    return {"type": "array", "items": {"type": "object", "properties": properties}}


TOOLS = {
    "plan_piece": (plan_piece, "Plan a story from the customer's brief, footage and timed words. Read the story beats, "
                   "then optionally apply your chosen keep list of source [start,end] pairs.",
                   {"brief": S, "start": N, "end": N, "keep": {"type": "array", "items": {"type": "array", "items": N}}}),
    "direct": (direct, "Direct the current cut. Read the workflow palette with no moments (or a family such as glitch, "
               "type or surface). Place devices on story events in output seconds. Reuse an id to revise a beat.",
               {"family": S, "moments": rows({"id": S, "device": S, "start": N, "end": N, "text": S, "color": S, "remove": B,
                                               "role": {"type": "string", "enum": ["hook", "build", "turn", "payoff", "close"]},
                                               "controls": {"type": "object", "properties": CONTROLS}})}),
    "score": (score, "Choose a reference-grade music bed for the brief and design sound on actual visual landings. "
              "Choose music and sounds independently. Run after directing the picture.",
              {"brief": S, "music": B, "sounds": B, "kind": {"type": "string", "enum": ["story", "marketing", "podcast", "longform"]}}),
    "dress": (dress, "Set authored display phrases on the cut with the typesetting and caption layout engines. "
              "Use output seconds, reuse ids for revisions, and set captions only when desired.",
              {"words": rows({"id": S, "text": S, "start": N, "end": N, "voice": S, "position": S, "ink": S}),
               "captions": B}),
}
