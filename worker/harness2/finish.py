"""finish: the craft departments over the current cut, in one op
(HARNESS_V2.md 4.2; EDIT-CORE 19 Nov 2026).

    {"finish": {"departments"?: [...], "look"?: name, "intensity"?: "calm" |
                "designed" | "charged", "music"?: "auto" | "<find id>" |
                "none", "pictures"?: [{"word", "query", "depicts"?}],
                "sport"?: true, "moves"?: "alternate", "captions"?: false,
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
            speech (or "moves": "alternate"), a camera that alternates: a
            landing on one cut, a slow push across the next piece
  speed     slow motion on the measured peaks of action, speed-ups on the
            quiet stretches (activity: picture motion and sound hits)
  pictures  source_broll on the words the caller names (B-roll on the exact
            word; the caller says what each shot shows)
  music     a track of reference grade (music_choose over the reference
            pool), its best stretch, under the voice at the references' level
  beats     the cuts slid onto the song's beats (beat_align_cuts)
  sound     the designed sound (score.sound.plan_sounds) on what lands on
            screen: pictures, cards, type, camera moves, designed cuts
  master    social mastering (-14 LUFS, true peak under -1 dBTP)

Without ``departments`` it runs every department the cut lacks. Each one
lands or says why not; none stops the others. Knowledge, never a gate.
"""
import json

DEPARTMENTS = ("look", "captions", "moves", "speed", "pictures", "music",
               "beats", "sound", "master")
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


def zooms_of(edl):
    """Every camera move of the cut: the effects' zooms (where the EDL keeps
    them) and any top-level ones."""
    eff = edl.get("effects") or {}
    return [z for z in list(eff.get("zooms") or []) + list(
        edl.get("zooms") or []) if isinstance(z, dict)]


def lacks(edl, stage):
    """The departments this cut does not have yet."""
    fx = edl.get("effects") or {}
    out = []
    if not (fx.get("look") or fx.get("grade") or fx.get("grade_custom")):
        out.append("look")
    if not edl.get("captions") and program_words(stage):
        out.append("captions")
    if not zooms_of(edl):
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
    if v.get("moves") != "alternate" and speech_share(stage) >= 0.25:
        count = max(2, min(8, int(round(program / 10.0))))
        return run_verb(stage, "punch_in_on_emphasis", {"count": count},
                        env, index)
    # No speech, or "moves": "alternate" (sport, a montage, a product): a
    # camera that alternates wide and close, a landing on one cut and a slow
    # push across the next (p5728 asked for exactly this and got one push).
    ok = False
    busy = [(float(z.get("start") or 0), float(z.get("end") or 0))
            for z in zooms_of(stage.edl)]
    for k, (a, b) in enumerate(pieces(stage.edl)):
        if b - a < 1.2 or any(b > x and a < y for x, y in busy):
            continue
        if k % 2 == 1:
            # the digital close-up: in on the cut, held, out on the next cut
            args = {"start": a, "end": b, "mode": "punch", "strength": 0.3,
                    "purpose": "a second, closer camera on this piece"}
        else:
            # the wide, alive: a slow push across the piece
            args = {"start": round(a + 0.05, 3), "end": round(b - 0.05, 3),
                    "mode": "push_in", "strength": 0.06,
                    "purpose": "the wide holds the eye with a slow push"}
        ok = run_verb(stage, "add_zoom", args, env, index) or ok
    return ok


def _speed(stage, v, env, index, run_verb):
    """Slow motion on the measured peaks and speed-ups on the measured
    lulls, from the footage's own activity (picture motion and sound hits,
    signal only): the expressive moment of the busiest pieces eases down to
    0.5x for about a second, and the quietest stretch of the others runs at
    1.6x. p5728 asked for both and got one 0.8 s slow-motion."""
    import activity
    from harness2 import ops as ops_mod
    track, why = ops_mod.ENGINES.activity_track(stage)
    if not track:
        env.finding(f"finish speed: no activity measure ({why}); speed "
                    "rows on the moments you see work the same way")
        return False
    sc, fps, _used = activity.score(track)
    sc = [float(x) for x in sc]
    keep = [list(map(float, k)) for k in stage.edl.get("keep") or []]
    taken = [(float(sp["start"]), float(sp["end"]))
             for sp in stage.edl.get("speed") or [] if isinstance(sp, dict)]

    def window(a, b):
        i0, i1 = max(0, int(a * fps)), min(len(sc), int(b * fps) + 1)
        return [(i / fps, sc[i]) for i in range(i0, i1)]

    peaks, lulls = [], []
    for a, b in keep:
        if b - a < 2.0:
            continue
        w = window(a + 0.3, b - 0.3)
        if not w:
            continue
        t_peak, v_peak = max(w, key=lambda x: x[1])
        peaks.append((v_peak, max(a, t_peak - 0.45), min(b, t_peak + 0.45)))
        # the quietest 1.5 s inside the piece
        span = int(1.5 * fps)
        if len(w) > span + 2:
            best = min(range(len(w) - span),
                       key=lambda k: sum(x[1] for x in w[k:k + span]))
            lulls.append((sum(x[1] for x in w[best:best + span]) / span,
                          w[best][0], w[best][0] + 1.5))
    program = _program(stage.edl)
    n = max(1, int(round(program / 12.0)))
    slow = sorted(peaks, reverse=True)[:n]
    fast = sorted(lulls)[:n]

    def free(a, b):
        return not any(b > x + 0.05 and a < y - 0.05 for x, y in taken)
    ok = 0
    for _v, a, b in slow:
        if b - a < 0.6 or not free(a, b):
            continue
        row = {"ramp": True, "hold": 0.5}
        landed = ops_mod._ramp_on_source(stage, row, 0.5, a, b, env, index) \
            or run_verb(stage, "set_speed", {"start": a, "end": b,
                                             "factor": 0.6}, env, index)
        if landed:
            taken.append((a, b))
            ok += 1
    for _v, a, b in fast:
        if not free(a, b):
            continue
        if run_verb(stage, "set_speed", {"start": round(a, 3),
                                         "end": round(b, 3), "factor": 1.6},
                    env, index):
            taken.append((a, b))
            ok += 1
    env.finding(f"finish speed: {ok} speed change(s): slow motion on the "
                "busiest moments, speed-ups on the quietest stretches "
                "(measured activity)")
    return ok > 0


def _beats(stage, v, env, index, run_verb):
    """The cuts slide onto the beats of the song the viewer hears
    (beat_align_cuts on the music, never the footage's own rhythm)."""
    if not stage.edl.get("music"):
        env.finding("finish beats: no music yet; the music department "
                    "runs first")
        return False
    return run_verb(stage, "beat_align_cuts", {"source": "music"}, env,
                    index)


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
    for z in zooms_of(edl):
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
        if v.get("moves") == "alternate" or v.get("sport"):
            # action and sport: the camera alternates, speed follows the
            # action, the cuts land on the song's beats; the type and the
            # look stay as the person set them (p5728 asked for no
            # subtitles, and a look system draws captions)
            todo = [d for d in todo if d not in ("look", "captions")]
            todo += ["moves", "speed", "beats"]
        todo = [d for d in DEPARTMENTS if d in todo]
    if v.get("captions") is False:
        todo = [d for d in todo if d != "captions"]
    landed = []
    for dept in todo:
        if dept == "captions" and v.get("captions") is False:
            continue
        try:
            if globals()[RUN[dept]](stage, v, env, index, run_verb):
                landed.append(dept)
        except Exception as exc:  # noqa: BLE001 - one department, not all
            env.skip(index, f"finish {dept} raised {type(exc).__name__}: "
                            f"{str(exc)[:200]}",
                     "the other departments landed")
    if v.get("captions") is False and stage.edl.get("captions"):
        # the person excluded captions: none stay, a look's included
        if run_verb(stage, "add_captions", {"mode": "off"}, env, index):
            landed.append("captions off")
    env.finding("finish: " + (", ".join(landed) or "nothing")
                + " landed" + (f" (of {', '.join(todo)})" if todo else ""))
    env.measures["finish"] = {"asked": todo, "landed": landed,
                              "brief": json.dumps(v.get("brief"))[:200]
                              if v.get("brief") else None}
    return bool(landed)
