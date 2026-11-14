"""Typed-film adapter for the Director. First-film size admission is not used here.

The ask ledger owns scope. This adapter changes only how an admitted film
request is executed: longer films get the existing segmented pass; clips
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
        return self.parent.write_edl(source_clock(edl, self.start, self.original_key),
                                     "Director: typed film")


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
             "stages": rec.get("stages"), "version": view.director_version}
    trace["supply"] = rec.get("supply_trace")
    box = view.director_footage or {}
    trace["footage"] = {k: box.get(k) for k in
        ("subjects", "error", "seconds", "report", "word_check")}
    print("[typed-craft] " + json.dumps(trace, ensure_ascii=False, default=str), flush=True)


def run(ctx, worker_db, job, session_id, cfg):
    from . import film, flags, patch
    import db as dbx
    import quality_verifier
    if not cfg.get("director", False):
        return {"fell": "typed Director is disabled"}
    dcfg = copy.deepcopy(flags.read(worker_db))
    if dcfg.get("mode") != "on":
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
        if selections is None:
            result = patch.throughout(ctx, ctx.ask_ledger,
                quality_verifier.request_text_for(ctx), cfg.get("pass_s", 600.0))
            result["path"] = "segmented_whole_film"
            result["selection_usage"] = selection_usage
            return result
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
        try:
            with open(os.path.join(work, "film", "bin.json")) as f:
                bin_ = json.load(f)
            rec["supply_trace"] = {"usage": bin_.get("usage"), "wishes": [
                {k: r[k] for k in ("wish", "want", "own", "mine", "hits", "bin",
                    "named", "named_found", "named_checked", "check", "dropped", "footage") if k in r}
                for r in bin_.get("wishes") or []]}
        except (OSError, ValueError):
            pass
        upgraded = _finish_footage(view, worker_db, job)
        if upgraded:
            applied = upgraded["applied"]
        return {"version": view.director_version, "applied": applied,
                "path": "podcast_story_director" if selections else "whole_film_director",
                "source_window": [a, b], "remaining_clips": (selections or [])[1:],
                "selection_usage": selection_usage, "stages": rec["stages"],
                "s": round(time.monotonic() - started, 1)}
    finally:
        if view is not None:
            _trace(view, work, rec, job)
            _clean(view, work)
        else:
            shutil.rmtree(work, ignore_errors=True)
