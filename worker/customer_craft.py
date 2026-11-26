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
    "plan_piece": (plan_piece, "Re-cut the piece by story: pass keep=[[source start, source end], ...] in playing order to "
                   "apply a new selection, or call it without keep to read the footage, words and story proposals. "
                   "A creative request arrives with its plan already applied; use this to change that cut.",
                   {"brief": S, "start": N, "end": N, "keep": {"type": "array", "items": {"type": "array", "items": N}}}),
    "direct": (direct, "Place palette devices (glitch hits, type runs, colour blooms, negatives, tears, freezes, "
               "chapters and more) on story beats of the current cut, in output seconds; reuse a beat's id to "
               "revise it, remove=true to drop it. Call with no moments for the palette, or a family such as glitch, "
               "type or surface.",
               {"family": S, "moments": rows({"id": S, "device": S, "start": N, "end": N, "text": S, "color": S, "remove": B,
                                               "role": {"type": "string", "enum": ["hook", "build", "turn", "payoff", "close"]},
                                               "controls": {"type": "object", "properties": CONTROLS}})}),
    "score": (score, "Score the cut: a reference-grade music bed chosen for the brief and the directed beats, and "
              "sound design on real visual landings. Choose music and sounds independently; run it after the picture changes.",
              {"brief": S, "music": B, "sounds": B, "kind": {"type": "string", "enum": ["story", "marketing", "podcast", "longform"]}}),
    "dress": (dress, "Set the words the viewer should remember as poster-scale display type on the cut (output "
              "seconds, reuse ids to revise) and turn captions on or off. "
              "Each phrase is solved for the frame by the typesetting and caption layout engines.",
              {"words": rows({"id": S, "text": S, "start": N, "end": N, "voice": S, "position": S, "ink": S}),
               "captions": B}),
}


# ------------------------------------------------------------------ plan first (round 2, 26 Nov)
# Round 1 offered these tools and the editor never called them (gate-1123: 0 calls on every replay).
# When the ask ledger's model reads the request as an edited piece, the harness now makes the
# piece's plan before the editor's first step and applies it through these same tools. The editor
# then looks at the result and refines. A plain operation never comes here.

FOOTAGE_ONLY = {"pictures", "picture", "still", "clip"}   # needs the footage alone cannot meet

PIECE_RULES = """You are the director of an edited short piece made from a customer's own footage. You read their \
request, their footage (frames, timed words, shots, sound) and write the piece's plan, which is applied exactly as you \
write it: the cut, the directed beats, the display words, captions and the score. An editor then renders it, looks at \
it and refines. Answer only by calling record_piece_plan.

The bar is the best short-form editors alive: a viewer who has seen thousands of reels says "I've never seen an edit \
like that". Every device is punctuation bound to a real moment of THIS footage (the first meaningful action or line, \
the turn, a cut between ideas, the payoff, a beat of the music already in the film), never decoration and never on \
every cut. Rhythm comes from contrast: hits against holds. A vulnerable or emotional line is held clean.

story: what the piece is, its opening, its turn and its payoff, in three or four sentences, from what really happens \
or is said. Never invent numbers, quotes, names or claims.

cut: new when the request needs its own selection or order of the footage (a short or reel from a longer upload, a \
tighter story, a montage of the best moments); current when the film's present cut already carries the story (keep \
what the customer asked to keep: their length, their song, their order). keep (cut new only): the source intervals \
in playing order, each clause or action whole, opening on the first meaningful action or line; [] for current.

beats: 2-8 moments, each one device from the palette below at a story role. Times are on the clock the evidence \
names. A hit lasts 2-12 frames; a held device (a negative stretch, a kept hue, a chapter) its stretch. text only for \
devices that set type, and then the speaker's own words or a plain label. color only when the device takes one.

words: 0-4 lines the viewer must remember (the hook, the turn, the payoff), set as poster type for the time they are \
said; never a re-typeset of every line, never a spoken list as rows.

captions: on for speech the viewer must follow, off for wordless or music-led footage, as_is when the film already \
has the captions it needs.

music / sounds: a music bed only when the piece is not already carried by a song or a voice that must stand alone, \
and never when the customer said no music; it must lift the piece (goosebumps, never cringe). sounds only on visual \
landings: a device hit, a reveal, a real action on screen. sound_brief: the feeling and the moments, in one sentence.

The customer's words set the scope: a style they named, a length, what to keep, what not to touch."""


def piece_palette():
    """Palette cards the footage alone can carry (the menu the Director reads), one per line."""
    from scene import palette
    palette._discover()
    rows = []
    for name in sorted(palette.DEVICES):
        entry = palette.DEVICES[name]
        if FOOTAGE_ONLY & set(entry.get("needs") or ()):
            continue
        rows.append(f"[{entry['family']}] {palette.card(name)}")
    return "\n".join(rows)


_SYSTEM = {}


def piece_system():
    if "text" not in _SYSTEM:
        _SYSTEM["text"] = PIECE_RULES + "\n\nTHE PALETTE (device, its fields, what it does):\n" + piece_palette()
    return _SYSTEM["text"]


def _num(): return {"type": "number"}


PIECE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["story", "cut", "keep", "beats", "words", "captions", "music", "sounds", "sound_brief"],
    "properties": {
        "story": {"type": "string"},
        "cut": {"type": "string", "enum": ["current", "new"]},
        "keep": {"type": "array", "items": {"type": "array", "items": _num()}},
        "beats": {"type": "array", "maxItems": 10, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["role", "start", "end", "what", "device", "text", "color"],
            "properties": {"role": {"type": "string", "enum": ["hook", "build", "turn", "payoff", "close"]},
                           "start": _num(), "end": _num(), "what": {"type": "string"},
                           "device": {"type": "string"}, "text": {"type": "string"}, "color": {"type": "string"}}}},
        "words": {"type": "array", "maxItems": 6, "items": {
            "type": "object", "additionalProperties": False, "required": ["text", "start", "end"],
            "properties": {"text": {"type": "string"}, "start": _num(), "end": _num()}}},
        "captions": {"type": "string", "enum": ["on", "off", "as_is"]},
        "music": {"type": "boolean"},
        "sounds": {"type": "boolean"},
        "sound_brief": {"type": "string"},
    },
}


def _phrases(words, limit_chars=24000):
    """Timed words as phrase lines on the source clock, broken at pauses."""
    lines, group = [], []

    def flush():
        if group:
            lines.append(f"[{group[0]['t0']:.2f}-{group[-1]['t1']:.2f}] " + " ".join(str(w['w']) for w in group))
            group.clear()
    for w in words or []:
        if not isinstance(w, dict) or w.get("t0") is None:
            continue
        if group and (float(w["t0"]) - float(group[-1]["t1"]) > 0.6 or len(group) >= 14):
            flush()
        group.append(w)
    flush()
    text = "\n".join(lines)
    if len(text) > limit_chars:
        # A long upload keeps its shape: every line, each cut short.
        text = "\n".join(line[:max(40, limit_chars // max(1, len(lines)))] for line in lines)[:limit_chars]
    return text


def _sheet(ctx, times):
    """One low-resolution labelled sheet of the footage at these source seconds, or None."""
    import os
    import uuid
    import media
    import sheets
    try:
        path = ctx.proxy_path()
    except Exception:  # noqa: BLE001 - the original is the other copy
        path = None
    if not path:
        try:
            import agent_tools
            path = agent_tools._original_local(ctx)
        except Exception:  # noqa: BLE001
            return None
    frames = []
    for i, t in enumerate(times):
        fp = os.path.join(ctx.workdir, f"plan_{i}_{uuid.uuid4().hex[:6]}.jpg")
        try:
            media.frame_at(path, t, fp, width=480)
            frames.append((f"{t:.1f}s", fp))
        except Exception:  # noqa: BLE001 - one frame missing leaves the others
            continue
    if not frames:
        return None
    sheet = os.path.join(ctx.workdir, f"plan_sheet_{uuid.uuid4().hex[:8]}.jpg")
    sheets.build_timestamp_sheet(frames, sheet)
    try:
        from PIL import Image
        img = Image.open(sheet)
        img.thumbnail((1280, 1280))
        img.convert("RGB").save(sheet, "JPEG", quality=72)
    except Exception:  # noqa: BLE001 - the full sheet still serves
        pass
    return sheet


def evidence(ctx, edl, ledger, request):
    """The plan's input, kept small: the ask, timed words, shots, sound and one low-res sheet."""
    import llm
    from score import asks
    index = getattr(ctx, "index", None) or {}
    clock = program_clock_of(edl)
    duration = float((index.get("video") or {}).get("duration") or getattr(ctx, "duration", 0) or 0)
    film = clock.out_duration
    keep = edl.get("keep") or []
    on_source = bool(keep)
    shots = [f"[{float(s['start']):.1f}-{float(s['end']):.1f}] "
             + str((s.get("caption") or {}).get("text") or (s.get("caption") or {}).get("summary") or "")[:90]
             for s in (index.get("shots") or [])[:80] if isinstance(s, dict) and "start" in s]
    asks_text = "\n".join(f"- {a.get('said')}: {a.get('want')}" for a in (ledger or {}).get("asks") or [])
    lines = [
        "The customer's request: " + json.dumps(str(request or "")[:4000], ensure_ascii=False),
        "What they asked, one ask a line:\n" + (asks_text or "-"),
        (f"CLOCK: source seconds of the upload ({duration:.1f} s long). The current film keeps "
         + ", ".join(f"[{a:.1f}, {b:.1f}]" for a, b in keep[:40]) + f" ({film:.1f} s)."
         if on_source else
         f"CLOCK: seconds of the current film ({film:.1f} s); it is assembled from clips, so cut stays current."),
        "Frame: " + str(((edl.get("frame") if isinstance(edl.get("frame"), dict) else {}) or {}).get("ratio") or "source"),
        "The film's sound, layer by layer:\n" + "\n".join(asks.sound_layers(edl, index)),
        "Timed words (" + ("source" if on_source else "film") + " clock):\n"
        + (_phrases(index.get("words") if on_source else clock.kept_words(index.get("words") or []))
           or "no speech"),
        "Shots:\n" + ("\n".join(shots) or "none listed"),
    ]
    span = duration if on_source else film
    times = [span * (i + 0.5) / 8 for i in range(8)] if span > 0 else []
    if not on_source:
        times = [t for t in (clock.out_to_src(x) for x in times) if t is not None]
    content = [{"type": "text", "text": "\n\n".join(lines)}]
    sheet = _sheet(ctx, times) if times else None
    if sheet:
        content += [{"type": "text", "text": "The footage at these source seconds:"}, llm.image_part(sheet)]
    return content, on_source


def program_clock_of(edl):
    from timeline import Timeline
    return Timeline(edl.get("keep") or [], edl.get("inserts") or [], edl.get("speed") or [])


def _to_film(clock, a, b, on_source):
    """A beat's [a, b] on the clock it was planned on, as film seconds; None when the cut dropped it."""
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(a) and math.isfinite(b)):
        return None
    if b < a:
        a, b = b, a
    if not on_source:
        return a, max(b, a + 1 / 30)
    x, y = clock.src_to_out(a), clock.src_to_out(b)
    if x is None and y is None:
        return None
    if x is None:
        x = max(0., y - (b - a))
    if y is None or y <= x:
        y = x + max(1 / 30, b - a)
    return x, y


def _keep_list(value, duration):
    out = []
    for row in value or []:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        try:
            a, b = float(row[0]), float(row[1])
        except (TypeError, ValueError):
            continue
        a, b = max(0., min(a, b)), min(duration or max(a, b), max(a, b))
        if b - a >= 0.2:
            out.append([round(a, 3), round(b, 3)])
    return out


def plan_first(ctx, ledger, request, record=None):
    """Make the piece's plan and apply it with plan_piece, direct, dress and score. Returns the
    editor's note (what was planned, what each step did). Never raises: whatever a step could not
    apply is named in the note and the editor carries on from what was applied."""
    from score import asks
    record = record or (lambda *a, **k: None)
    edl = ctx.latest_edl()["json"]
    content, on_source = evidence(ctx, edl, ledger, request)
    value, usage, err = None, {}, None
    for attempt in (1, 2):
        try:
            value, usage = asks._call(ctx, "record_piece_plan", PIECE_SCHEMA, piece_system(), content,
                                      max_tokens=6000, model=getattr(ctx, "agent_model", None))
            break
        except Exception as exc:  # noqa: BLE001 - asked once more, then the editor plans itself
            err = f"{type(exc).__name__}: {str(exc)[:200]}"
    if not value:
        return ("\n\nPIECE PLAN: the plan step did not answer (" + str(err) + "). Make the piece yourself: "
                "plan_piece for the story and cut, direct the beats, dress the words, score it, then render and look.")
    done, missed = [], []

    def step(name, label, args, call):
        """One craft call; a failure is named for the editor and the other steps go on."""
        try:
            res = call()
        except Exception as exc:  # noqa: BLE001 - the editor repairs this step itself
            res = f"REJECTED: {type(exc).__name__}: {str(exc)[:200]}"
        record(name, args, res)
        (missed if str(res).startswith("REJECTED") else done).append(f"{label}: {str(res)[:240]}")
    duration = float(getattr(ctx, "duration", 0) or 0)
    keep = _keep_list(value.get("keep"), duration) if on_source and value.get("cut") == "new" else []
    if keep:
        step("plan_piece", "cut", {"keep": keep},
             lambda: plan_piece(ctx, brief="Cut the piece by its story", keep=keep))
    clock = program_clock_of(ctx.latest_edl()["json"])
    moments = []
    for n, beat in enumerate(value.get("beats") or [], 1):
        span = _to_film(clock, beat.get("start"), beat.get("end"), on_source)
        if span is None:
            missed.append(f"beat {n} ({beat.get('what')}) fell outside the cut")
            continue
        moments.append({"id": f"{beat.get('role') or 'beat'}-{n}", "device": beat.get("device") or "white",
                        "role": beat.get("role"), "start": round(span[0], 3), "end": round(span[1], 3),
                        "text": beat.get("text") or None, "color": beat.get("color") or None})
    if moments:
        step("direct", "direction", {"moments": moments}, lambda: direct(ctx, moments=moments))
    words = []
    for n, row in enumerate(value.get("words") or [], 1):
        span = _to_film(clock, row.get("start"), row.get("end"), on_source)
        if span and str(row.get("text") or "").strip():
            words.append({"id": f"plan-word-{n}", "text": row["text"], "start": round(span[0], 3),
                          "end": round(span[1], 3)})
    captions = {"on": True, "off": False}.get(value.get("captions"))
    if words or captions is not None:
        step("dress", "words", {"words": words, "captions": captions},
             lambda: dress(ctx, words=words, captions=captions))
    if value.get("music") or value.get("sounds"):
        step("score", "score", {"music": bool(value.get("music")), "sounds": bool(value.get("sounds")),
                                "brief": value.get("sound_brief")},
             lambda: score(ctx, brief=value.get("sound_brief") or "", music=bool(value.get("music")),
                           sounds=bool(value.get("sounds"))))
    ctx.piece_plan = {"plan": value, "moments": moments, "words": words, "usage": usage}
    version = ctx.latest_edl()["version"]
    beats = "\n".join(f"  {m['id']} {m['device']} {m['start']:.2f}-{m['end']:.2f} s" for m in moments)
    return ("\n\nPIECE PLAN, made before your first step and already applied (EDL v" + str(version) + "):\n"
            + "Story: " + str(value.get("story") or "")[:900] + "\nDirected beats (film seconds):\n"
            + (beats or "  none") + "\nDisplay words: " + (", ".join(w["text"] for w in words) or "none")
            + f"\nCaptions: {value.get('captions')}; music: {value.get('music')}; sounds: {value.get('sounds')}"
            + "\nApplied: " + ("; ".join(done) or "nothing") + ("\nNot applied: " + "; ".join(missed) if missed else "")
            + "\nNow render_preview, LOOK at the frames and measures, and refine every beat that does not land: "
              "revise a beat with direct (same id), the words with dress, the sound with score, the cut with "
              "plan_piece(keep=...). Do any ask the plan left. Your reply says what the film now is, as it really is.")


# What the editor reads on a stable tool list (agent_tools._COMPACT_CONTRACTS): the whole contract.
COMPACT = {name: " ".join(str(spec[1]).split()) for name, spec in TOOLS.items()}
