"""Typed-film adapter for the Director. First-film size admission is not used here.

The ask ledger owns scope. This adapter changes only how an admitted film
request is executed: whole films use the Director without upload admission; clips
are selected with the podcast story contract before the Director reads a
bounded source window. All writes use the typed request's normal guards.
"""
import copy
import json
import os
import shutil
import subprocess
import tempfile
import time

WINDOW_S = 600.0


def window_index(index, start, end):
    """A private index on the window's clock, without stale full-source tiles."""
    out = {"video": dict(index.get("video") or {}, duration=end - start),
           "language": index.get("language"), "speakers": index.get("speakers", 1)}
    words = index.get("words") or []
    selected = [i for i, w in enumerate(words)
                if float(w["t0"]) >= start - 0.001 and float(w["t1"]) <= end + 0.001]
    positions = {old: new for new, old in enumerate(selected)}
    out["words"] = [dict(words[i], t0=max(0.0, words[i]["t0"] - start),
                         t1=min(end - start, words[i]["t1"] - start)) for i in selected]
    out["sentences"] = []
    for s in index.get("sentences") or []:
        kept = [positions[i] for i in range(s["wi0"], s["wi1"] + 1) if i in positions]
        if kept:
            out["sentences"].append(dict(s, id=len(out["sentences"]) + 1,
                wi0=kept[0], wi1=kept[-1], t0=out["words"][kept[0]]["t0"],
                t1=out["words"][kept[-1]]["t1"]))
    out["shots"] = [dict(s, start=max(start, s["start"]) - start,
                         end=min(end, s["end"]) - start)
                    for s in index.get("shots") or []
                    if min(end, s["end"]) > max(start, s["start"])]
    out["spatial"] = dict(index.get("spatial") or {})
    out["spatial"]["samples"] = [dict(s, t=s["t"] - start)
        for s in (index.get("spatial") or {}).get("samples") or [] if start <= s["t"] < end]
    return out


def source_clock(edl, start, original_key):
    """Only source clocks move. Titles, effects and sound use program time."""
    out = copy.deepcopy(edl)
    out["keep"] = [[a + start, b + start] for a, b in out.get("keep") or []]
    captions = out.get("captions")
    items = captions if isinstance(captions, list) else (captions or {}).get("items", [])
    for c in items:
        c["start"] += start
        c["end"] += start
    for lane in ("overlays", "inserts"):
        for item in out.get(lane) or []:
            if item.get("asset_key") == original_key:
                item["source_start_s"] = float(item.get("source_start_s") or 0) + start
    return out


class WindowContext:
    """Delegate product operations; keep the Director's private state local.

    In particular, apply_score must not use the first-film bypass of the
    request contract. The original context validates against the full source.
    """
    def __init__(self, ctx, index, proxy, start, original_key, session_id):
        self.parent = ctx
        self.index, self.duration = index, index["video"]["duration"]
        self.proxy, self.start, self.original_key = proxy, start, original_key
        self.first_cut_session = session_id
        self.director_version = None
        self.director_state = None
        self.director_upgrade = None
        self.director_bed = None
        self.director_footage = None
        self.director_bed_thread = None
        self.director_execution = None
        self.director_typed = True

    def __getattr__(self, name):
        return getattr(self.parent, name)

    def __setattr__(self, name, value):
        if name in {"parent", "index", "duration", "proxy", "start", "original_key",
                    "first_cut_session", "scene_keys"} or name.startswith("director_"):
            object.__setattr__(self, name, value)
        else:
            setattr(self.parent, name, value)

    def proxy_path(self):
        return self.proxy

    def apply_score(self, edl, description):
        return write_film(self.parent, source_clock(edl, self.start, self.original_key))

    def director_recover_supply(self, **kwargs):
        return recover_supply(self, **kwargs)

    def director_recover_write(self, **kwargs):
        return recover_write(self, **kwargs)

    def director_prepare_edl(self, edl, l1):
        """Declare the existing canvas representation for a picture-only montage.

        A wordless Score can have no source-audio spine: Lower already supplies
        its full-duration insert and picture overlays. No pixels, timing or
        request permissions change here; normal typed write guards still run.
        """
        if edl.get("keep") or edl.get("canvas") or not edl.get("inserts"):
            return edl
        out = copy.deepcopy(edl)
        out["canvas"] = {"width": int(l1["frame"][0]), "height": int(l1["frame"][1]),
                         "fps": float(l1["fps"]), "bg_color": "#000000"}
        out["frame"] = {"ratio": "source", "mode": "crop"}
        return out


def recover_write(ctx, *, work, cfg, index, sc, bin_doc, original_key,
                  worker_db, comp, text, refusal, rec):
    """One Director revision from the real write rejection, with all guards intact.

    Source checks happen after compilation and can reject an otherwise valid
    Score. Keep the checked pictures and ask the Director to repair its plan,
    rather than discarding the entire craft pass at this boundary.
    """
    from . import brief, call, compiler, direct, film, lower
    import db as dbx
    receipt = {"refusal": str(refusal), "result": "not written"}
    rec["write_recovery"] = receipt
    try:
        system, content = brief.load(work)
        current = ctx.parent.latest_edl()["json"]
        prompt = (
            "The normal typed-edit write guard rejected this Score. Revise the Score "
            "using that source evidence; keep the original customer request, duration, "
            "story, picture constraints and all necessary source events. Restore any "
            "distinct action the guard says you removed. Do not claim evidence you have "
            "not seen, relabel a rejected deletion, or trade away an ask to pass the guard. "
            "Some earlier cut batches may already be saved; the current source keep is "
            "below. Return complete replacement JSONL lines (same IDs replace; del removes), "
            "using the Score's local source clock. The guard will check the revised film again.\n"
            f"Source-clock offset: {ctx.start}\nCurrent keep, full-source seconds: "
            + json.dumps(current.get("keep")) + "\nWrite rejection:\n" + str(refusal))
        answer = call.stream(cfg["models"]["direct"], system,
            content + [{"type": "text", "text": "Rejected Score:\n" + text},
                       {"type": "text", "text": prompt}],
            max_tokens=direct.REPAIR_TOKENS, effort=cfg["effort"]["direct"],
            purpose="director_write_repair", deadline=time.monotonic() + 90)
        receipt["usd"] = answer.usd
        lines, _ = compiler.parse_score(text)
        revised, bad = direct.merge(lines, answer.text)
        revised_text = direct.dump(revised)
        if bad or revised_text == direct.dump(lines):
            receipt["result"] = "no valid revision"
            return None
        candidate = compiler.compile_score(revised_text, work_dir=work)
        if film.bare(candidate):
            receipt["result"] = "revision is bare"
            return None
        # A write repair cannot trade away asks or compiler validity for a cut.
        _, original_open, _ = direct.ask_facts(lines, comp, index, sc)
        _, revised_open, _ = direct.ask_facts(revised, candidate, index, sc)
        if not set(revised_open).issubset(original_open) or compiler.validate_l1(candidate.l1):
            receipt["result"] = "revision loses asks or is invalid"
            return None
        film._one_mix(ctx, work, candidate)
        edl, locals_, rep = lower.lower(candidate.l1, work)
        edl = film._publish(ctx, worker_db, edl, locals_, bin_doc, original_key)
        edl = ctx.director_prepare_edl(edl, candidate.l1)
        result = ctx.apply_score(edl, "Director: revised from source evidence")
        receipt["result"] = str(result)
        if not str(result).startswith("EDL v"):
            return None
        with open(os.path.join(work, "score.jsonl"), "w", encoding="utf-8") as f:
            f.write(revised_text)
        return candidate, revised_text, edl, rep, result
    except (call.Refused, dbx.JobLeaseLost):
        raise
    except Exception as exc:
        receipt["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        return None


def recover_supply(ctx, *, job, cfg, deadline, work, index, sess, per,
                   sc, bin_doc, foot, rec, acquire):
    """Reconsider an empty picture plan once, without relaxing its checks.

    A typed film can afford to hear why its requested pictures failed. The
    Scout proposes alternatives; Supply still decides what actually fits.
    First films do not call this hook. Existing usable pictures are retained.
    """
    from . import call, film, scout, supply
    current = (sc, bin_doc, foot)
    if not sc or not isinstance(bin_doc, dict) or bin_doc.get("items") or bin_doc.get("own"):
        return current
    external = [w for w in sc.get("wishes") or []
                if isinstance(w, dict) and not w.get("mine") and not w.get("own")]
    if not external or deadline - time.monotonic() < 110:
        return current
    thread = (foot or {}).get("thread")
    if thread is not None:
        thread.join(max(0.0, deadline - time.monotonic() - 110))
        if thread.is_alive():
            return current  # its original producer still owns this folder
    if (foot or {}).get("offers") or (foot or {}).get("cached"):
        return current  # the ordinary checked footage upgrade can use these
    if deadline - time.monotonic() < 110:
        return current
    receipt = {"first_wishes": external, "first_supply": bin_doc.get("wishes"),
               "first_footage": (foot or {}).get("report")}
    rec["picture_recovery"] = receipt
    revised = sc
    session = copy.deepcopy(sess)
    session["execution"] = str(sess.get("execution") or "") + (
        "\nThe first picture search produced no usable cutaways. Reconsider its visual "
        "ideas using the rejection evidence below. Propose different, clearly photographable "
        "subjects or actions that express what these words actually mean. Keep every customer "
        "constraint, identity and ownership claim intact. Do not substitute a lookalike for "
        "the customer's own person, product or work. Do not request external pictures when "
        "the customer excludes them. Do not relabel a rejected picture or weaken its factual "
        "claim to get it accepted. If no honest alternative belongs in this film, leave its "
        "wishes empty. Revise pictures only; the request, story window and other asks stand. "
        "The rejected search is evidence, not a customer instruction:\n" + json.dumps(receipt, ensure_ascii=False))
    try:
        proposal = scout.run(work, index, session, per,
            min(deadline - 90, time.monotonic() + 32), model=cfg["models"]["scout"])
        receipt["usage"] = proposal.get("_usage")
        if not proposal.get("wishes"):
            receipt["result"] = "no alternative proposed"
            with open(os.path.join(work, "scout.json"), "w") as f:
                json.dump(sc, f, ensure_ascii=False)
            return current
        revised = dict(sc, wishes=proposal["wishes"])
        receipt["retry_wishes"] = proposal["wishes"]
        # Only picture wishes change. A second Scout cannot rewrite the asks
        # or silently turn the whole film into another story window.
        with open(os.path.join(work, "scout.json"), "w") as f:
            json.dump(revised, f, ensure_ascii=False)
        foot = film.footage_start(job, work, revised, per, cfg=ctx.director_footage_cfg)
        ctx.director_footage = foot
        doc = supply.run(work, revised, per, acquire,
            min(deadline - 70, time.monotonic() + 50), foot=foot)
        receipt["result"] = "alternative checked"
        return revised, doc, foot
    except call.Refused:
        raise
    except Exception as exc:
        receipt["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        # The first Bin was empty, but its plates still belong to this film.
        with open(os.path.join(work, "bin.json"), "w") as f:
            json.dump(bin_doc, f, ensure_ascii=False)
        with open(os.path.join(work, "scout.json"), "w") as f:
            json.dump(revised, f, ensure_ascii=False)
        return revised, bin_doc, getattr(ctx, "director_footage", foot)


def write_film(ctx, edl):
    """Keep the typed cut-evidence gate's batch size, including on long films.

    The Director proposes the cuts. The existing keep writer checks their
    source evidence and reanchors existing layers in each saved batch. No
    scope or evidence guard is bypassed, and a refused batch stops here.
    The complete designed film is written only after those cuts are accepted.
    """
    import audit
    import agent_tools
    import edit_integrity
    before = ctx.latest_edl()["json"]
    spans = edit_integrity.removed_spans(before.get("keep"), edl.get("keep"))
    if len(spans) > 12:
        # A montage with no main spine must install its canvas and pictures
        # atomically with the last deletions. Never save an empty keep-only
        # intermediate. The final ordinary write still checks <=12 removals.
        batches = spans[:-12] if not edl.get("keep") and edl.get("canvas") else spans
        for offset in range(0, len(batches), 12):
            current = ctx.latest_edl()["json"]
            keep = [list(s) for s in audit.subtract_spans(
                current.get("keep") or [], batches[offset:offset + 12])]
            result = agent_tools._write_keep(ctx, keep,
                "Director: source-checked cut batch", snap_to_words=False)
            if not str(result).startswith("EDL v"):
                return result
    return ctx.write_edl(edl, "Director: typed film")


def _selection(ctx):
    """Model interpretation, using the podcast engine's story contract.

    All timed sentences are supplied; there is no keyword shortlist and no
    chronological truncation that makes the end of a class unreachable.
    """
    import quality_verifier
    import shorts
    from . import asks
    schema = {"type": "object", "additionalProperties": False,
        "required": ["mode", "clips"], "properties": {
            "mode": {"type": "string", "enum": ["whole", "clips"]},
            "clips": {"type": "array", "items": {"type": "object",
                "additionalProperties": False,
                "required": ["start", "end", "title", "hook"],
                "properties": {"start": {"type": "number"}, "end": {"type": "number"},
                    "title": {"type": "string"}, "hook": {"type": "string"}}}}}}
    system = shorts._PLAN_SYSTEM + "\n" + (
        "For this typed request return the supplied structured schema. mode whole means "
        "the customer wants work on the full film, including removing downtime throughout "
        "a match; return no clips then. mode clips means excerpts from the source. "
        "Respect their requested length, topic and count. Include all selected clips, "
        "with complete sentence boundaries and their own setup and payoff. Do not turn "
        "a whole-film request into a short. Earlier selections are supplied so requests "
        "for another option can choose a different story.")
    index = ctx.index or {}
    # Size the serializer for the full index, rather than its usual display cap.
    transcript = shorts._transcript_block(index, max_chars=max(180000, len(json.dumps(index)) * 2))
    content = json.dumps({"request": quality_verifier.request_text_for(ctx),
        "asks": (getattr(ctx, "ask_ledger", None) or {}).get("asks"),
        "history": getattr(ctx, "request_contract_history", None),
        "current_keep": ctx.latest_edl()["json"].get("keep"),
        "duration": ctx.duration}, ensure_ascii=False) + "\nTRANSCRIPT:\n" + transcript
    value, usage = asks._call(ctx, "typed_story_selection", schema, system, content, max_tokens=4000)
    if value.get("mode") == "whole":
        return None, usage
    if value.get("mode") != "clips" or not value.get("clips"):
        raise ValueError("story selection returned no complete clip")
    clips = value["clips"]
    for c in clips:
        a, b = float(c["start"]), float(c["end"])
        if not (0 <= a < b <= ctx.duration and b - a <= WINDOW_S):
            raise ValueError("selected story is outside the source or needs a longer film pass")
        # Validate, do not silently clamp a model's broken range or its payoff.
        c["start"], c["end"] = a, b
    return clips, usage


def _clean(view, work):
    """No deleting a work folder while its footage producer still writes it."""
    state = view.director_state or {}
    foot = state.get("footage") or getattr(view, "director_footage", None) or {}
    for thread in (foot.get("thread"), getattr(view, "director_bed_thread", None)):
        if thread is not None:
            thread.join()
    view.director_state = None
    shutil.rmtree(work, ignore_errors=True)


def _finish_footage(view, worker_db, job):
    """Consume the library's pending footage before its work folder is removed.

    First films have a later upgrade phase. A typed call has no such phase,
    so waiting only during cleanup would discard the pictures it fetched.
    The existing upgrade still checks the active version and writes through
    WindowContext's normal typed-request guards.
    """
    from . import film
    state = view.director_state or {}
    thread = (state.get("footage") or {}).get("thread")
    if thread is not None:
        thread.join()
    film.close(view)
    return film.upgrade(view, worker_db, job, time.monotonic())


def footage_config(worker_db):
    """Typed craft uses the owner's customer-footage permission.

    The old first-film rollout defaults to off when there is no row. That
    rollout is not admission for an already-authorized typed film. An
    explicit operator hold still wins, and an unreadable switch fails closed.
    """
    from footage import switch
    try:
        raw = worker_db.run(switch.read_row)
    except Exception:
        return dict(switch.DEFAULTS)
    return {"mode": "on", "test_user_ids": []} if raw is None else switch.parse(raw)


def _trace(view, work, rec, job):
    """Keep the supply decision after temporary media is deleted; no URLs."""
    trace = {"job": job.get("id"), "footage_config": view.director_footage_cfg,
             "stages": rec.get("stages"), "version": view.director_version,
             "at": rec.get("at"), "failure": rec.get("failure"),
             "meter": rec.get("meter"), "lower": rec.get("lower"),
             "picture_recovery": rec.get("picture_recovery"),
             "write_recovery": rec.get("write_recovery")}
    _record_supply(work, rec)
    trace["supply"] = rec.get("supply_trace")
    box = view.director_footage or {}
    trace["footage"] = {k: box.get(k) for k in
        ("subjects", "error", "seconds", "report", "word_check")}
    print("[typed-craft] " + json.dumps(trace, ensure_ascii=False, default=str), flush=True)


def _record_supply(work, rec):
    try:
        with open(os.path.join(work, "film", "bin.json")) as f:
            bin_ = json.load(f)
        rec["supply_trace"] = {"usage": bin_.get("usage"), "wishes": [
            {k: r[k] for k in ("wish", "want", "own", "mine", "hits", "bin",
                "named", "named_found", "named_checked", "check", "dropped", "footage") if k in r}
            for r in bin_.get("wishes") or []]}
    except (OSError, ValueError):
        pass


def run(ctx, worker_db, job, session_id, cfg):
    from . import film, flags
    import db as dbx
    import quality_verifier
    if not cfg.get("director", False):
        return {"fell": "typed Director is disabled"}
    dcfg = copy.deepcopy(flags.read(worker_db))
    if dcfg.get("mode") != "on" and job.get("user_id") not in (dcfg.get("test_user_ids") or []):
        return {"fell": "the Director is off (app_kv.director)"}
    source_s = float(getattr(ctx, "duration", 0) or 0)
    if not getattr(ctx, "has_main_video", False) or source_s <= 0:
        return {"fell": "no upload to direct"}
    started = time.monotonic()
    selections, selection_usage = (None, None)
    child_clip = ((getattr(ctx, "project", None) or {}).get("meta") or {}).get("clip")
    if (getattr(ctx, "project", None) or {}).get("parent_project_id") and child_clip:
        selections = [child_clip]
    elif source_s > WINDOW_S:
        selections, selection_usage = _selection(ctx)
        if selections is not None:
            import shorts
            result = shorts.start_typed_craft(ctx, selections,
                quality_verifier.request_text_for(ctx))
            result["selection_usage"] = selection_usage
            return result
    # Both switches admitted this request. The first-film account admission
    # and 180-second cap stay confined to first films.
    model = getattr(ctx, "agent_model", None) or "gpt-6.1-sol"
    dcfg["models"] = {"scout": model, "direct": model}
    # A source-window scene plan uses a different clock; ordinary lowered
    # EDL is the supported typed path and preserves the product renderer.
    dcfg["engine"] = "off"
    dcfg["engine_user_ids"] = []
    original = worker_db.run(dbx.latest_asset, ctx.project_id, "original") or {}
    if not original.get("storage_key"):
        return {"fell": "no original asset"}
    selected = (selections or [{"start": 0.0, "end": source_s}])[0]
    a, b = selected["start"], selected["end"]
    work = tempfile.mkdtemp(prefix="typed-film-", dir=ctx.workdir)
    view = None
    rec = {"stages": {}}
    try:
        proxy = ctx.proxy_path()
        index = ctx.index
        if selections:
            target = os.path.join(work, "window.mp4")
            subprocess.run([os.environ.get("FFMPEG_BIN", "ffmpeg"), "-nostdin", "-v", "error",
                "-threads", "2", "-ss", str(a), "-i", proxy, "-t", str(b-a),
                "-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264", "-preset", "ultrafast",
                "-crf", "18", "-c:a", "aac", "-y", target], check=True, capture_output=True, timeout=180)
            proxy, index = target, window_index(index, a, b)
        view = WindowContext(ctx, index, proxy, a, original["storage_key"], session_id)
        view.director_footage_cfg = footage_config(worker_db)
        if selections:
            view.director_execution = (
                f"The podcast story selector chose source seconds {a:g}-{b:g}. "
                f"This planning proxy and index use local seconds 0-{b-a:g}; the write adapter "
                "restores source timestamps. Keep this complete story and its payoff. "
                f"Selected hook: {selected['hook']}. For a social excerpt the default deliverable "
                "is 9:16 with word-by-word captions and a readable hook. Explicit customer "
                "constraints override those defaults. This is execution context, not a new ask.")
        _steps, applied, _picked = film._make(view, worker_db, job, dcfg,
            time.monotonic() + cfg.get("film_s", 220.0), os.path.join(work, "film"), rec)
        _record_supply(work, rec)
        upgraded = _finish_footage(view, worker_db, job)
        if upgraded:
            applied = upgraded["applied"]
        return {"version": view.director_version, "applied": applied,
                "path": "podcast_story_director" if selections else "whole_film_director",
                "source_window": [a, b], "remaining_clips": (selections or [])[1:],
                "selection_usage": selection_usage, "stages": rec["stages"],
                "s": round(time.monotonic() - started, 1)}
    except Exception as exc:
        rec["failure"] = f"{type(exc).__name__}: {str(exc)[:500]}"
        raise
    finally:
        if view is not None:
            _trace(view, work, rec, job)
            _clean(view, work)
        else:
            shutil.rmtree(work, ignore_errors=True)
