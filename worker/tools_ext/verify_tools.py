"""Department ``verify``: track 1F's verbs (INTERFACES_W1.md §4).

  suggest_devices  READ: <= 8 ranked device candidates bound to the moments
                   that earn them, each with the exact apply_edit_batch op
                   (device_planner.suggest)
  set_intensity    WRITE: the project's tone dial (calm | designed |
                   charged); removes the lowest-ranked device items over the
                   new budget through tools_ext._write.commit and stores the
                   level in preference_memory (never in the EDL)
  proof_device     READ: renders only the windows around the devices it
                   proves (the changed-section preview path, with and
                   without them) and returns per-device strips, onions and
                   metrics (motion_qa.proof)
  audit_motion     READ, browserless: ledger, flash safety, static rungs and
                   the restraint rules (motion_qa.audit)

Write verbs commit through ``tools_ext._write.commit`` (edit_batch.apply_batch),
so a verb and apply_edit_batch validate the same items identically. Every
verb works on the agent, MCP and Shorts lanes (no browser, no model): the
proof renders on the existing ``preview_check`` job. 1F also edits the shared
skills and agent_prompt.py at wave close-out.
"""

DEPARTMENT = "verify"

# Pinned verb names (INTERFACES_W1.md §4). Renaming one is a round-lead
# change: 1F's planner, the skills and the refbench agent cases name them.
VERBS = ("suggest_devices", "set_intensity", "proof_device", "audit_motion")

LEVELS = ("calm", "designed", "charged")
_ASKED = ("zoom", "sfx", "flash")


# ── inputs the planner needs, gathered from the project ─────────────────

def _kv(ctx):
    import db as dbx

    def get(key):
        return ctx.db.run(dbx.kv_get, key)

    def put(key, value):
        return ctx.db.run(dbx.kv_put, key, value)
    return get, put


def project_intensity(ctx):
    import preference_memory
    get, _put = _kv(ctx)
    try:
        return preference_memory.read_device_prefs(
            get, ctx.project_id)["intensity"]
    except Exception:  # noqa: BLE001
        return preference_memory.DEFAULT_DEVICE_INTENSITY


def _removal_weights(ctx):
    import devices
    import preference_memory
    try:
        cards = devices.load_cards()
        versions = ctx.db.run(preference_memory.recent_edls, ctx.project_id)
        counts = preference_memory.removal_counts(
            versions, lambda c: (cards.get(c) or {}).get("family"))
        return preference_memory.removal_weights(counts), \
            preference_memory.removal_note(counts)
    except Exception as e:  # noqa: BLE001
        print(f"[suggest_devices] removal memory skipped: {e}", flush=True)
        return {}, ""


def _rhetoric(ctx, edl):
    """The LLM rhetoric tags for the kept transcript (cached per transcript
    in process and in app_kv), or {} when off or unavailable."""
    import config
    if not getattr(config, "W1F_RHETORIC_LLM", False):
        return {}
    try:
        import device_planner
        import llm
        words = device_planner.program_words(edl, ctx.index or {})
        if len(words) < 6:
            return {}
        get, put = _kv(ctx)

        def ask(system, user):
            got = llm.ask_text(system, user, max_tokens=900, temperature=0.0,
                               purpose="device_rhetoric")
            return (got or {}).get("text")
        tags = device_planner.llm_tags(
            words, ask,
            cache_get=lambda k: get(f"rhetoric:{k}"),
            cache_put=lambda k, v: put(f"rhetoric:{k}", v))
        # positions in the kept word list -> source word indices
        return {words[k]["i"]: v for k, v in tags.items() if k < len(words)}
    except Exception as e:  # noqa: BLE001
        print(f"[suggest_devices] rhetoric tagger skipped: {e}", flush=True)
        return {}


def music_maps(ctx, edl):
    """{music id: PROGRAM-clock RhythmMap} for tracks the USER supplied
    (non-legacy provenance), from each asset's perception beat grid."""
    import db as dbx
    import rhythm_types
    from timeline import Timeline
    out = {}
    tl = Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                  edl.get("speed") or [])
    for m in edl.get("music") or []:
        if not isinstance(m, dict) or m.get("mute") or not m.get("id"):
            continue
        if m.get("provenance") in (None, "legacy"):
            continue
        try:
            asset = ctx.db.run(dbx.asset_by_key, ctx.project_id,
                               m.get("storage_key"))
            per = ((asset or {}).get("meta") or {}).get("perception")
            if not per:
                continue
            rmap = rhythm_types.from_beat_grid(per, ref={
                "storage_key": m.get("storage_key"), "sha256": None,
                "music_id": m["id"]})
            out[m["id"]] = rhythm_types.to_program(
                rmap, tl, m, track_dur=(asset or {}).get("duration_s"))
        except Exception as e:  # noqa: BLE001
            print(f"[suggest_devices] music map skipped: {e}", flush=True)
    return out


def matte_source(ctx):
    """The SourceRef matte.person sidecars of this project's main video are
    keyed and launched on (sidecars.prefetch_top_shorts' convention: the
    index's sha and duration, the proxy's key and geometry), or None."""
    import db as dbx
    import sidecars
    index = ctx.index or {}
    sha = sidecars.index_source_sha(index)
    video = index.get("video") or {}
    if not sha or not video.get("duration"):
        return None
    proxy = ctx.db.run(dbx.latest_asset, ctx.project_id, "proxy")
    if not proxy or not proxy.get("storage_key"):
        return None
    info = {"duration": video.get("duration"),
            "fps": proxy.get("fps") or video.get("fps"),
            "width": proxy.get("width") or video.get("width"),
            "height": proxy.get("height") or video.get("height")}
    return sidecars.source_from(sha, proxy["storage_key"], info,
                                ctx.project_id, index.get("shots"))


def warm_matte(ctx, edl, cands):
    """Start measuring the person matte under the matte candidates (the
    windows they would draw on), at interactive priority, never waiting —
    the "measuring" their reason promises. A batch written from these ops
    launches nothing itself (INTERFACES_W1.md §1.7), so without this the
    first preview would be the first to ask, and draw the substitute.
    Returns the number of source ranges asked for (0: none, or no store)."""
    import device_planner
    from timeline import Timeline
    want = [c for c in cands or []
            if c.get("family") in device_planner.MATTE_FAMILIES]
    if not want:
        return 0
    try:
        import sidecars
        src = matte_source(ctx)
        if src is None:
            return 0
        tl = Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                      edl.get("speed") or [])
        ranges = []
        for c in want:
            v = (c.get("op") or {}).get("value") or {}
            a = v.get("start", c.get("at"))
            b = v.get("end", a)
            if a is None:
                continue
            pts = [p for p in (tl.out_to_src(float(a)),
                               tl.out_to_src(max(float(a),
                                                 float(b) - 0.01)))
                   if p is not None]
            if pts:
                ranges.append((max(0.0, min(pts) - 0.02), max(pts) + 0.02))
        if not ranges:
            return 0
        sidecars.ensure("matte.person", src, ranges, priority="interactive")
        return len(ranges)
    except Exception as e:  # noqa: BLE001 — a suggestion never fails on it
        print(f"[suggest_devices] matte warm-up skipped: {e}", flush=True)
        return 0


def matte_state(ctx, edl):
    """{status, estimate_s, person} for the program's kept footage:
    ready / partial / pending / absent from the matte.person sidecars over
    the kept ranges; person False when the index saw no face anywhere."""
    out = {"status": "absent", "estimate_s": 60, "person": None}
    try:
        import sidecars
        index = ctx.index or {}
        sha = sidecars.index_source_sha(index)
        keep = [(float(a), float(b)) for a, b in edl.get("keep") or []]
        if not sha or not keep:
            return out
        shots = [float(s.get("start")) for s in index.get("shots") or []
                 if isinstance(s, dict) and s.get("start") is not None]
        dur = float((index.get("video") or {}).get("duration") or 0.0)
        ready = sidecars.ready_chunks("matte.person", sha, shots, dur, keep)
        total = sidecars.chunks_for(shots, dur, keep)
        if total and len(ready) >= len(total):
            out["status"] = "ready"
        elif ready:
            out["status"] = "partial"
        else:
            out["status"] = "absent"
        summ = sidecars.summary(sha).get("matte.person") or {}
        if out["status"] != "ready" and summ.get("pending"):
            out["status"] = "pending"
        out["estimate_s"] = int(max(20, 2.5 * sum(b - a for a, b in keep)))
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:120]
    try:
        spatial = (ctx.index or {}).get("spatial") or {}
        faces = spatial.get("faces") if isinstance(spatial, dict) else None
        if isinstance(faces, list) and len(faces) == 0 and \
                spatial.get("measured"):
            out["person"] = False
    except Exception:  # noqa: BLE001
        pass
    return out


# Owner rule: a zoom or a sound is proposed only when the user ASKED for one.
# "no zooms", "don't zoom", "remove the whoosh", "without sound effects" name
# the thing to refuse it: a mention preceded by a negation or a removal verb
# in the same clause is not an ask.
_ZOOM_ASK = r"(zoom|punch[- ]?in|push[- ]?in)\w*"
_SFX_ASK = r"(sound[- ]?effects?|sfx|whoosh\w*|swoosh\w*)"
_REFUSAL = (r"\b(no|not|never|don'?t|do not|without|stop|remove|delete|drop|"
            r"cut|kill|lose|less|fewer|zero|nothing|avoid|skip|minus)\b")


def _asks_for(text, pattern):
    import re
    for m in re.finditer(r"\b" + pattern, text, re.I):
        clause = re.split(r"[.!?;,]|\b(?:but|and then|then)\b",
                          text[:m.start()], flags=re.I)[-1]
        if not re.search(_REFUSAL, clause, re.I):
            return True
    return False


def _asked(ctx, asked):
    out = {str(a).lower() for a in (asked or ()) if str(a).lower() in _ASKED}
    text = str(getattr(ctx, "user_message", "") or "")
    try:
        import flash_safety
        if flash_safety.opted_in(text):
            out.add("flash")
    except Exception:  # noqa: BLE001
        pass
    if _asks_for(text, _ZOOM_ASK):
        out.add("zoom")
    if _asks_for(text, _SFX_ASK):
        out.add("sfx")
    return out


# ── the verbs ────────────────────────────────────────────────────────────

def suggest_devices(ctx, start=None, end=None, families=None, intensity=None,
                    asked=None, max_candidates=None):
    """READ: ranked device candidates for the current program."""
    import device_planner
    import edit_batch
    import taste
    if not getattr(ctx, "has_main_video", True):
        return ("REJECTED: devices bind to spoken words, cuts and beats of "
                "a main video; this canvas program has none.")
    edl = ctx.latest_edl()["json"]
    level = intensity or project_intensity(ctx)
    if level not in LEVELS:
        return f"REJECTED: intensity is one of {', '.join(LEVELS)}."
    window = None
    if start is not None or end is not None:
        try:
            window = (float(start or 0.0), float(end) if end is not None
                      else None)
        except (TypeError, ValueError):
            return "REJECTED: start and end are program seconds."
    weights, learned = _removal_weights(ctx)
    try:
        keys = ctx.db.run(edit_batch.project_media_keys, ctx.project_id)
    except Exception:  # noqa: BLE001
        keys = {}
    matte = matte_state(ctx, edl)
    try:
        # freshness across the batch (owner, 15 Oct: "more and better, not
        # only the same"): the devices the sibling shorts already carry
        import tools_ext.recipe_tools as _rt
        batch = device_planner.batch_usage(_rt.sibling_edls(ctx))
    except Exception:  # noqa: BLE001 — freshness is advice
        batch = {}
    try:
        cands = device_planner.suggest(
            edl, ctx.index or {}, intensity=level, window=window,
            families=families, asked=_asked(ctx, asked),
            batch=batch, seed=getattr(ctx, "project_id", None),
            max_n=max_candidates or device_planner.MAX_CANDIDATES,
            rhetoric=_rhetoric(ctx, edl), maps=music_maps(ctx, edl),
            matte=matte, weights=weights,
            duration=ctx.duration or None, media_keys=set(keys or ()),
            request_text=getattr(ctx, "user_message", None))
    except ValueError as e:
        return f"REJECTED: {e}"
    except Exception as e:  # noqa: BLE001
        return f"Could not plan devices ({type(e).__name__}: {str(e)[:160]})."
    ctx._last_device_candidates = cands
    if matte.get("status") != "ready":
        warm_matte(ctx, edl, cands)
    try:
        line = taste.ledger(edl, intensity=level, index=ctx.index).line()
    except Exception:  # noqa: BLE001
        line = None
    import devices
    empty = None
    if not cands:
        try:
            empty = ("no live device card is registered yet"
                     if not devices.load_cards() else
                     "no moment clears the bar, or the budgets are spent")
        except Exception:  # noqa: BLE001
            empty = None
    text = device_planner.format_candidates(cands, intensity=level,
                                            ledger=line, empty_reason=empty)
    return text + (f"\n{learned}" if learned else "")


def set_intensity(ctx, level):
    """WRITE: the project's tone dial; trims what is over the new budget."""
    import device_planner
    import flash_safety
    import preference_memory
    import taste
    from tools_ext import _write
    level = str(level or "").strip().lower()
    if level not in LEVELS:
        return f"REJECTED: level is one of {', '.join(LEVELS)}."
    row = ctx.latest_edl()
    edl = row["json"]
    try:
        # A calmer level drops flashes past flash safety even when the user
        # once asked to strobe. Charged is a step UP: it never removes a
        # flash the edit already carries (it never adds one either; the
        # export's flash gate still judges the edit as written).
        removals = device_planner.trim_plan(
            edl, ctx.index or {}, level, maps=music_maps(ctx, edl),
            request_text=getattr(ctx, "user_message", None),
            opted_in=level == "charged")
    except Exception as e:  # noqa: BLE001
        return f"Could not plan the trim ({type(e).__name__}: {str(e)[:160]})."
    _get, put = _kv(ctx)
    try:
        preference_memory.write_device_prefs(put, ctx.project_id, level)
        stored = True
    except Exception as e:  # noqa: BLE001
        print(f"[set_intensity] level not stored: {e}", flush=True)
        stored = False
    note = ("" if stored else " (the level could not be stored; it applies "
            "to this trim only)")
    if not removals:
        try:
            line = taste.ledger(edl, intensity=level, index=ctx.index).line()
        except Exception:  # noqa: BLE001
            line = "?"
        return (f"Intensity set to {level} for this project{note}. Nothing "
                f"is over the {level} budget; the edit is unchanged "
                f"(ledger {line}).")
    ops = [_write.remove_fx(r["id"]) for r in removals]
    ids = ", ".join(r["id"] for r in removals)
    why = "; ".join(sorted({r["reason"] for r in removals}))[:300]
    summary = (f"set_intensity {level}: removed {len(removals)} device(s) "
               f"[{ids}] — {why}")
    out = _write.commit(ctx, ops, summary)
    if out.startswith("REJECTED"):
        return out
    try:
        after = ctx.latest_edl()["json"]
        fl = flash_safety.assess(after, ctx.index,
                                 getattr(ctx, "user_message", None))
        out += (f"\nDiff: removed {ids}. Flash peak now {fl['peak_per_s']}/s "
                f"(limit {fl['max_per_s']}).{note}")
    except Exception:  # noqa: BLE001
        out += f"\nDiff: removed {ids}.{note}"
    return out


def proof_device(ctx, ids=None, start=None, end=None):
    """READ: render and measure the devices' landings, frame by frame."""
    import db as dbx
    import motion_qa
    row = ctx.latest_edl()
    edl = row["json"]
    window = None
    if start is not None or end is not None:
        try:
            window = (float(start or 0.0),
                      float(end) if end is not None else 1e9)
        except (TypeError, ValueError):
            return "REJECTED: start and end are program seconds."
    if isinstance(ids, str):
        ids = [i.strip() for i in ids.split(",") if i.strip()]
    previous = None
    if not ids:
        try:
            prev = ctx.db.run(dbx.previous_edl_version, ctx.project_id,
                              row["version"])
            previous = (prev or {}).get("json")
        except Exception:  # noqa: BLE001
            previous = None
    devs = motion_qa.device_items(edl, ids=ids, window=window,
                                  previous_edl=previous)
    if ids:
        missing = sorted(set(map(str, ids)) - {d["id"] for d in devs})
        if missing:
            return ("REJECTED: no device item with id "
                    + ", ".join(missing) + " in the current EDL (fx items, "
                    "motion items with a composite or behind the subject).")
    if not devs:
        return ("Nothing to prove: no device item changed since the last "
                "version" + (" in that window" if window else "")
                + ". Pass ids=[...] to prove specific items.")
    import config
    result = motion_qa.proof_jobs(
        ctx, edl, ids=[d["id"] for d in devs], index=ctx.index,
        intensity=project_intensity(ctx), maps=music_maps(ctx, edl),
        budget_s=float(getattr(config, "W1F_PROOF_WAIT_S", 240.0)))
    ctx.last_device_proof = result
    if result.get("pending"):
        jobs = result["pending"]
        return (f"PREREQUISITE: the proof render is still running (job "
                f"{jobs.get('with')}). Call wait_for_job(job_id="
                f"{jobs.get('with')}), then proof_device again with the same "
                "ids (the finished render is reused).")
    text = motion_qa.summary_lines(result)
    _deliver(ctx, result)
    return text


def _deliver(ctx, result):
    """Queue each device's strip and onion for the editor's own eyes (the
    in-house loop with a seeing model, or an MCP client)."""
    try:
        import llm
        sees = getattr(ctx, "sight_out", False) or (
            getattr(ctx, "direct_sight", False)
            and llm.agent_sees(ctx.agent_model))
    except Exception:  # noqa: BLE001
        sees = False
    if not sees:
        return
    # two images per device; the text lines cover the rest
    for d in (result.get("devices") or [])[:4]:
        for key, what in (("strip", "frames -3..+6, with (top) / without "
                                    "(bottom)"),
                          ("onion", "pixels the device changed (red)")):
            if d.get(key):
                ctx.pending_images.append(
                    (f"proof {d['id']} — {what}", d[key]))


def audit_motion(ctx):
    """READ: the device ledger and restraint audit, no render."""
    import motion_qa
    edl = ctx.latest_edl()["json"]
    try:
        result = motion_qa.audit(edl, ctx.index or {},
                                 intensity=project_intensity(ctx),
                                 request_text=getattr(ctx, "user_message",
                                                      None),
                                 maps=music_maps(ctx, edl))
    except Exception as e:  # noqa: BLE001
        return f"Could not audit ({type(e).__name__}: {str(e)[:160]})."
    ctx.last_motion_audit = result
    return motion_qa.audit_text(result)


TOOL_SPECS = {
    "suggest_devices": (
        suggest_devices,
        "READ: up to 8 ranked creative-device candidates for the CURRENT "
        "program (silhouette flashes, punctuation frames, junctions, grade "
        "windows, finishes, type behind the subject...), each bound to the "
        "moment that earns it (a stressed word, the payoff, a cut, a "
        "section, a beat of the user's own track) and checked against the "
        "intensity budget, flash safety and the restraint rules. Each comes "
        "with the exact apply_edit_batch operation that writes it: pick the "
        "ones that serve the story and write them in ONE apply_edit_batch, "
        "then proof_device them. Never proposes a zoom or a sound unless "
        "asked; zero candidates is a fine answer.",
        {"start": {"type": "number"}, "end": {"type": "number"},
         "families": {"type": "array", "items": {"type": "string"},
                      "description": "limit to card families, e.g. matte_fx, "
                                     "punctuation, transition, finish"},
         "intensity": {"type": "string", "enum": list(LEVELS),
                       "description": "default: the project's set_intensity"},
         "asked": {"type": "array", "items": {"type": "string",
                                              "enum": list(_ASKED)},
                   "description": "what the user explicitly asked for"},
         "max_candidates": {"type": "integer", "minimum": 1,
                            "maximum": 8}}),
    "set_intensity": (
        set_intensity,
        "Set the project's tone dial: calm, designed (default) or charged. "
        "It decides how many devices each family may hold; lowering it "
        "removes the lowest-ranked device items over the new budget (and "
        "any flashes past flash safety) in one write that lists their ids. "
        "'less flashy', 'calmer', 'tone it down' = a step down; 'more "
        "energy' = a step up (it never adds anything by itself).",
        {"level": {"type": "string", "enum": list(LEVELS)}}),
    "proof_device": (
        proof_device,
        "READ: prove devices on rendered frames. Renders only the windows "
        "around them (with and without them) and returns, per device, a "
        "10-frame strip from -3 to +6 frames around its landing, an onion "
        "of the pixels it changed, and metrics: landing error in frames, "
        "legibility, matte jitter, flash risk, junction integrity, face "
        "collisions and budget. ids: the fx or motion ids to prove (default: "
        "the devices changed by the last write). Remove or move anything "
        "that does not land.",
        {"ids": {"type": "array", "items": {"type": "string"}},
         "start": {"type": "number"}, "end": {"type": "number"}}),
    "audit_motion": (
        audit_motion,
        "READ, no render: the device ledger against the project's intensity, "
        "flash safety, each device's expected rung, and the restraint rules "
        "(one punctuation family per section, no device on consecutive "
        "shots, nothing inside the payoff hold) as findings.",
        {}),
}

REQUIRED_ARGS = {"set_intensity": ["level"]}
WRITE_TOOLS = {"set_intensity"}
COMPACT = {
    "suggest_devices": (
        "READ: <=8 ranked device candidates bound to the moments that earn "
        "them (stress, payoff, cut, section, the user's beats), budgeted and "
        "flash-safe; each carries its exact apply_edit_batch op. Write the "
        "ones you keep in ONE batch, then proof_device. Never a zoom or "
        "sound unless asked; zero is fine."),
    "set_intensity": (
        "Tone dial calm|designed|charged for this project (not the EDL). "
        "Lowering removes the lowest-ranked devices over the new budget in "
        "one write listing their ids; 'less flashy' = a step down."),
    "proof_device": (
        "READ: renders only the windows around ids (default: last write's "
        "devices), with and without them: per device a -3..+6 frame strip, "
        "an onion and metrics (landing error, legibility, matte jitter, "
        "flash risk, junction integrity, collisions, budget)."),
    "audit_motion": (
        "READ, no render: device ledger, flash safety, expected rungs, and "
        "restraint findings (one punctuation family per section, no repeats "
        "on consecutive shots, nothing in the payoff hold)."),
}
