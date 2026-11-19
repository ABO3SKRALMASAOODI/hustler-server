"""finish: the craft departments over the current cut, in one op
(HARNESS_V2.md 4.2; EDIT-CORE 19 Nov 2026).

    {"finish": {"departments"?: [...], "look"?: name, "intensity"?: "calm" |
                "designed" | "charged", "music"?: "auto" | "<find id>" |
                "none", "pictures"?: [{"word", "query", "depicts"?}],
                "brief"?: str}}

The rubric scores of the golden replays sat at 1.5-2.5 with every ask done:
the edits were correct but plain (a talking head, small captions, few
pictures, no designed sound). The engines that make a film look made exist
as verbs; this op runs the ones the cut lacks, on the same staged edit, as
one version:

  look      apply_look: captions in a designed system, the grade, texture
            and its transitions (a look the cut already has stays)
  captions  add_captions in a motion look, when the look did not set them
  moves     punch-ins on the stressed words of speech; on a cut without
            speech, a camera that alternates: a landing on one cut, a slow
            push across the next piece
  pictures  source_broll on the words the caller names (B-roll on the exact
            word; the caller says what each shot shows)
  music     a track of reference grade (music_choose over the reference
            pool), its best stretch, under the voice at the references' level
  sound     the designed sound (score.sound.plan_sounds) on what lands on
            screen: pictures, cards, type, camera moves, designed cuts
  master    social mastering (-14 LUFS, true peak under -1 dBTP)

Without ``departments`` it runs every department the cut lacks. Each one
lands or says why not; none stops the others. Knowledge, never a gate.
"""
import json

DEPARTMENTS = ("look", "captions", "moves", "pictures", "music", "sound",
               "master")
LOOK_FOR = {"calm": "cinematic_doc", "designed": "editorial",
            "charged": "creator_punch"}


def _program(edl):
    from schemas import program_duration
    try:
        return float(program_duration(edl) or 0.0)
    except Exception:  # noqa: BLE001
        return 0.0


def _timeline(edl):
    from timeline import Timeline
    return Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                    edl.get("speed") or [])


def program_words(stage):
    """[(start, end)] of the spoken words that survive the cut, on the film
    clock."""
    edl = stage.edl
    words = (getattr(stage, "index", None) or {}).get("words") or []
    if not words:
        return []
    try:
        tl = _timeline(edl)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for w in words:
        try:
            a, b = float(w["start"]), float(w["end"])
        except (KeyError, TypeError, ValueError):
            continue
        oa, ob = tl.src_to_out(a), tl.src_to_out(b)
        if oa is None or ob is None or ob <= oa:
            continue
        out.append((round(oa, 3), round(ob, 3)))
    return out


def speech_share(stage):
    program = _program(stage.edl)
    if program <= 0:
        return 0.0
    return min(1.0, sum(b - a for a, b in program_words(stage)) / program)


def pieces(edl):
    """[(film start, film end)] of the cut's pieces of the upload."""
    try:
        tl = _timeline(edl)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for off, length in zip(tl.offsets, tl.seg_out_len):
        out.append((round(off, 3), round(off + length, 3)))
    return out


def lacks(edl, stage):
    """The departments this cut does not have yet."""
    fx = edl.get("effects") or {}
    out = []
    if not (fx.get("look") or fx.get("grade") or fx.get("grade_custom")):
        out.append("look")
    if not edl.get("captions") and program_words(stage):
        out.append("captions")
    if not edl.get("zooms") and not edl.get("zoom_paths"):
        out.append("moves")
    if not edl.get("music"):
        out.append("music")
    if not edl.get("sfx"):
        out.append("sound")
    out.append("master")
    return out


# --------------------------------------------------------------------------
# departments

def _look(stage, v, env, index, run_verb):
    name = v.get("look") or LOOK_FOR.get(str(v.get("intensity") or ""),
                                         "editorial")
    return run_verb(stage, "apply_look", {"name": name}, env, index)


def _captions(stage, v, env, index, run_verb):
    if stage.edl.get("captions"):
        env.finding("finish: captions already set by the look")
        return True
    if not program_words(stage):
        env.finding("finish: no speech in the cut, so no captions")
        return False
    style = v.get("captions") if isinstance(v.get("captions"), dict) \
        else {"motion_look": "editorial"}
    return run_verb(stage, "add_captions", {"mode": "from_transcript",
                                            "style": style}, env, index)


def _moves(stage, v, env, index, run_verb):
    program = _program(stage.edl)
    if program <= 0:
        return False
    if speech_share(stage) >= 0.25:
        count = max(2, min(8, int(round(program / 10.0))))
        return run_verb(stage, "punch_in_on_emphasis", {"count": count},
                        env, index)
    # No speech (sport, a montage, a product): a camera that alternates
    # wide and close, a landing on one cut and a slow push across the next.
    ok = False
    for k, (a, b) in enumerate(pieces(stage.edl)):
        if b - a < 1.2:
            continue
        if k % 2 == 0:
            args = {"start": a, "end": round(min(b, a + 0.45), 3),
                    "mode": "landing", "strength": 0.14,
                    "purpose": "the new shot arrives with momentum"}
        else:
            args = {"start": round(a + 0.1, 3), "end": round(b - 0.05, 3),
                    "mode": "push_in", "strength": 0.08,
                    "purpose": "a slow push holds the eye across the shot"}
        ok = run_verb(stage, "add_zoom", args, env, index) or ok
    return ok


def _pictures(stage, v, env, index, run_verb):
    rows = v.get("pictures")
    if not isinstance(rows, list) or not rows:
        env.finding("finish: pictures need what each shot shows: "
                    "pictures=[{word, query, depicts?}] on the words that "
                    "name things (source_broll cuts each one in on its word)")
        return False
    moments = []
    for i, r in enumerate(rows[:24]):
        if not isinstance(r, dict) or not (r.get("query") or r.get("depicts")):
            continue
        m = {"id": str(r.get("id") or f"m{i + 1}"),
             "query": str(r.get("query") or r.get("depicts"))[:160]}
        for k in ("depicts", "word", "at", "purpose", "duration_s", "kind",
                  "archival"):
            if r.get(k) is not None:
                m[k] = r[k]
        moments.append(m)
    if not moments:
        return False
    return run_verb(stage, "source_broll", {"moments": moments}, env, index)


def _music(stage, v, env, index, run_verb):
    want = v.get("music", "auto")
    if str(want).strip().lower() in ("none", "off", "false", "no"):
        return False
    if stage.edl.get("music"):
        env.finding("finish: the cut already has music; it stays")
        return True
    import music_choose
    import music_gate
    if want and str(want).strip().lower() != "auto":
        from harness2 import find_tool
        key, note = find_tool.music_asset(stage, want)
        if not key:
            env.skip(index, f"finish music: {note}",
                     "find(kind='music') ids, or music 'auto'")
            return False
        choice = {"cid": str(want).split(":", 1)[-1]}
        track = music_choose.find(choice["cid"]) or {}
    else:
        program = _program(stage.edl)
        share = speech_share(stage)
        cuts = len(pieces(stage.edl)) + len(stage.edl.get("inserts") or [])
        film = {"kind": "marketing" if share < 0.15 else "podcast",
                "duration_s": round(max(program, 6.0), 1), "turns": [],
                "pace": {"cuts10": round(10.0 * max(cuts, 1)
                                         / max(program, 1.0), 2)},
                "speech_share": round(share, 3),
                "brief": str(v.get("brief") or "")[:400]}
        try:
            call = music_choose._product_call(stage)
        except Exception:  # noqa: BLE001
            call = None
        choice = music_choose.choose(
            film, call=call or (lambda *_a: {"option": 1, "why": "the "
                                "closest match to the references"}),
            ctx=stage, n=6, pool=music_choose.reference_pool())
        if not choice:
            env.finding("finish: no track of reference grade is available "
                        "on this worker")
            return False
        key, err = music_choose.ensure_project_asset(stage, choice)
        if err:
            env.skip(index, f"finish music: {err}",
                     "find(kind='music') and place another id")
            return False
        music_gate.grant(stage, key, music_choose.PROVENANCE)
        track = music_choose.find(choice["cid"]) or {}
    speech = speech_share(stage) >= 0.15
    gain = -18.0 if speech else -4.0
    if track.get("lufs") is not None:
        gain += music_choose.TARGET_LUFS - float(track["lufs"])
    args = {"storage_key": key, "gain_db": round(gain, 1),
            "fade_out_s": 1.5, "loop": bool(choice.get("loop", True)),
            "purpose": (f"Valmera's choice: \"{track.get('title')}\" by "
                        f"{track.get('author')} ({track.get('license')}). "
                        f"{choice.get('why') or ''}")[:400]}
    if speech:
        args["duck"] = True
    if choice.get("start_s"):
        args["offset_s"] = float(choice["start_s"])
    landed = run_verb(stage, "add_music", args, env, index)
    if landed:
        env.finding(f"finish music: \"{track.get('title')}\" by "
                    f"{track.get('author')} ({track.get('mood') or ''}), "
                    f"from {float(choice.get('start_s') or 0):.1f} s of the "
                    "track")
    return landed


def events(edl, fps=30.0):
    """What lands on screen, as score.sound reads a film: [{f, kind, ...}]
    on the film clock. Captions never sound; a plain cut does not either
    (only a designed one travels)."""
    ev = []

    def at(t, kind, **kw):
        try:
            f = int(round(float(t) * fps))
        except (TypeError, ValueError):
            return
        if f >= 0:
            ev.append(dict(f=f, kind=kind, **kw))
    for i in edl.get("inserts") or []:
        at(i.get("at_output_s"), "picture", moving=True,
           still=str(i.get("kind") or "").startswith("image"),
           id=i.get("id"))
    for o in edl.get("overlays") or []:
        cover = o.get("fit") == "cover" and float(o.get("scale") or 1.0) >= 0.9
        if cover:
            at(o.get("start"), "picture", moving=True, id=o.get("id"))
        else:
            s = float(o.get("scale") or 0.4)
            at(o.get("start"), "card", area=round(min(1.0, s * s), 3),
               id=o.get("id"))
    for f in edl.get("fx") or []:
        if isinstance(f, dict) and f.get("type") == "broll":
            at(f.get("start"), "picture", moving=True, id=f.get("id"))
    for t in edl.get("texts") or []:
        big = t.get("template") in ("title", "big_number", "quote", "chapter")
        at(t.get("start"), "card" if big else "display",
           area=0.3 if big else 0.05, id=t.get("id"))
    for m in edl.get("motion") or []:
        if isinstance(m, dict):
            at(m.get("start"), "card", area=0.2, id=m.get("id"))
    for z in edl.get("zooms") or []:
        if z.get("mode") in ("punch", "landing", "pulse", "shake"):
            at(z.get("start"), "move", id=z.get("id"))
    tr = ((edl.get("effects") or {}).get("transition") or {})
    if tr.get("style") not in (None, "", "none", "cut"):
        for a, _b in pieces(edl)[1:]:
            at(a, "cut", designed=True, moving=True, change=0.6)
    return sorted(ev, key=lambda e: e["f"])


def _sound(stage, v, env, index, run_verb):
    edl = stage.edl
    program = _program(edl)
    if program <= 1.0:
        return False
    fps = 30.0
    evs = events(edl, fps)
    if not evs:
        env.finding("finish sound: nothing lands on screen yet to sound "
                    "(pictures, cards, type, moves); run it after them")
        return False
    existing = []
    for s in edl.get("sfx") or []:
        key = str(s.get("storage_key") or "")
        if key.startswith("sound:"):
            existing.append({"s": key[len("sound:"):], "start_s": s.get("at"),
                             "event_f": int(float(s.get("at") or 0) * fps)})
    try:
        from score import sound as sound_mod
        sounds, report = sound_mod.plan_sounds(evs, {
            "fps": fps, "dur_f": int(program * fps), "existing": existing,
            "speech": None, "voice": None, "words": program_words(stage),
            "bed": bool(edl.get("music")), "prog_end_f": int(program * fps)})
    except Exception as exc:  # noqa: BLE001 - the other departments stand
        env.skip(index, f"finish sound: the sound design failed "
                        f"({type(exc).__name__}: {str(exc)[:160]})",
                 "sfx ops from find(kind='sfx') on the moments that land")
        return False
    ok = 0
    have = {e["s"] for e in existing}
    for s in sounds:
        if s.get("s") in have or s.get("start_s") is None:
            continue
        args = {"storage_key": f"sound:{s['s']}",
                "at": round(float(s["start_s"]), 3),
                "gain_db": round(float(s.get("db", -8.0)), 1),
                "purpose": str(s.get("for") or s.get("part") or "")[:60]}
        if s.get("trim_s"):
            args["offset_s"] = round(float(s["trim_s"]), 3)
        if s.get("dur_s"):
            args["dur_s"] = round(float(s["dur_s"]), 3)
        if run_verb(stage, "add_sfx", args, env, index):
            ok += 1
    env.finding(f"finish sound: {ok} sound(s) on what lands on screen "
                f"(class {report.get('class')}, allowance "
                f"{report.get('allow')}, {report.get('events')} events)")
    return ok > 0


def _master(stage, v, env, index, run_verb):
    return run_verb(stage, "set_master_loudness", {"enabled": True}, env,
                    index)


RUN = {d: f"_{d}" for d in DEPARTMENTS}


def op_finish(stage, value, env, index, run_verb):
    v = value if isinstance(value, dict) else {}
    asked = v.get("departments")
    if isinstance(asked, str):
        asked = [asked]
    if asked:
        todo = [d for d in DEPARTMENTS if d in asked]
        unknown = [d for d in asked if d not in DEPARTMENTS]
        if unknown:
            env.repair("finish: unknown department(s) "
                       + ", ".join(map(str, unknown)) + " left out; "
                       "departments: " + ", ".join(DEPARTMENTS))
    else:
        todo = lacks(stage.edl, stage)
        if v.get("pictures"):
            todo.append("pictures")
        todo = [d for d in DEPARTMENTS if d in todo]
    landed = []
    for dept in todo:
        try:
            if globals()[RUN[dept]](stage, v, env, index, run_verb):
                landed.append(dept)
        except Exception as exc:  # noqa: BLE001 - one department, not all
            env.skip(index, f"finish {dept} raised {type(exc).__name__}: "
                            f"{str(exc)[:200]}",
                     "the other departments landed")
    env.finding("finish: " + (", ".join(landed) or "nothing")
                + " landed" + (f" (of {', '.join(todo)})" if todo else ""))
    env.measures["finish"] = {"asked": todo, "landed": landed,
                              "brief": json.dumps(v.get("brief"))[:200]
                              if v.get("brief") else None}
    return bool(landed)
