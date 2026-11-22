"""Build a documentary on the uploaded narration's clock, one durable chapter at a time."""
import hashlib
import json
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

CHAPTER_S = 75.0


def chapters(index, max_s=CHAPTER_S):
    """Cover the whole source, splitting at observed sentence ends, never inside a word."""
    dur = float(index['video']['duration'])
    ends = sorted({float(s['t1']) for s in index.get('sentences') or []
                   if 0 < float(s['t1']) < dur})
    start, out = 0.0, []
    while dur-start > max_s*1.4:
        choices = [t for t in ends if start+max_s*.6 <= t <= start+max_s*1.4]
        if not choices:
            # No observed boundary: keep the sentence intact, even when long.
            choices = [t for t in ends if t > start+max_s]
        end = min(choices, key=lambda t: abs(t-start-max_s)) if choices else dur
        out.append([start, end]); start = end
    if dur > start:
        out.append([start, dur])
    return out


def beats(index, start, end):
    """Group complete sentences into picture beats. Pauses stay on the preceding picture."""
    sentences = [s for s in index.get("sentences", [])
                 if start <= s["t0"] < end and str(s.get("text") or "").strip()]
    if not sentences:
        sentences = [{"t0": w["t0"], "text": w.get("w") or w.get("word") or w.get("text")}
                     for w in index.get("words", []) if start <= w["t0"] < end]
    rows = []
    for sent in sentences:
        text = str(sent.get("text") or "").strip()
        if not text:
            continue
        if not rows or sent["t0"] - rows[-1]["start"] >= 6:
            rows.append({"start": sent["t0"] if rows else start, "words": []})
        rows[-1]["words"].append(text)
    for i, row in enumerate(rows):
        row["end"] = rows[i + 1]["start"] if i + 1 < len(rows) else end
        row["say"] = " ".join(row.pop("words"))
    return rows


def picture_plan(rows, goal):
    import llm
    from broll_source import _subjects_of
    from score import boards
    response = llm.ask_text(
        "Direct a documentary from these already timed beats. Choose the exact person, "
        "object, anatomical structure, company or setting the words describe. Prefer "
        "an archive image or a moving illustration for observable subjects. Give two "
        "different archive search phrases and a precise visible requirement. For an "
        "abstract beat select a short contiguous quotation from that beat for a designed "
        "card. For a number, comparison, sequence or term, also supply design: "
        "{kind:number|versus|timeline|term,title,parts:[{label,value}]}. Copy every display "
        "phrase exactly from this beat. Return a JSON array of {i,name,is,must_show,searches,quote,design}. "
        "i is the zero-based index in beats. "
        "Use empty name for a designed beat. Never infer an identity from resemblance.",
        json.dumps({"goal": goal, "beats": [r["say"] for r in rows]}, ensure_ascii=False),
        max_tokens=2200, purpose="narration_picture_plan", reasoning_effort="low")
    items = llm.extract_json_array((response or {}).get("text") or "") or []
    items = {r["i"]: r for r in items if isinstance(r, dict) and type(r.get("i")) is int}
    for i, row in enumerate(rows):
        item = items.get(i, {})
        subjects = _subjects_of({"subjects": [item]}) if isinstance(item.get("searches"), list) else []
        quote = str(item.get("quote") or "").strip()
        row["quote"] = quote if quote and quote in row["say"] and len(quote) <= 60 else ""
        design = boards.design_spec(item.get("design"))
        if design:
            labels = [design["title"]] + [p[k] for p in design["parts"] for k in ("label", "value")]
            if any(label.casefold() not in row["say"].casefold() for label in labels if label):
                design = None
        row["design"] = design
        row["subject"] = subjects[0] if subjects else None


def picture(ctx, beat, work):
    from broll_source import BrollError, designed_hit, fetch_local, find_named
    import stock
    from score import boards
    subject = beat.get("subject")
    if subject:
        # Include the moving-stock sources in the archive engine's frame check.
        library = []
        for provider, key in ((stock._pexels_search, stock.PEXELS_KEY),
                              (stock._pixabay_search, stock.PIXABAY_KEY)):
            if key:
                try:
                    library.extend(provider(subject["name"], "video", "landscape", 3))
                except Exception:
                    continue
        try:
            candidates, _ = find_named(subject, kind="video", count=4, also=library, workdir=work,
                                       deadline=time.monotonic() + 32)
        except (BrollError, OSError, RuntimeError, ValueError):
            candidates = []
        for candidate in candidates:
            try:
                got = fetch_local(candidate, work, 1920, 1080)
                if got["kind"] == "video" and got["duration_s"] < beat["end"] - beat["start"]:
                    continue
                return candidate, got
            except (BrollError, OSError, RuntimeError, ValueError):
                continue
    # A source quote remains a truthful picture when the archive or model is unavailable.
    quote = beat.get("quote") or " ".join(beat["say"].split()[:7])
    spec = beat.get("design") or boards.design_spec({"kind": "term", "parts": [{"label": quote}]})
    candidate = designed_hit(spec, "landscape")
    return candidate, fetch_local(candidate, work, 1920, 1080)


def publish(ctx, beat, item, media):
    import db
    import read_tools
    import storage
    kind = "video" if media["kind"] == "video" else "image"
    path = media["path"]
    with open(path, "rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    key = f"films/{ctx.project_id}/{digest}{os.path.splitext(path)[1]}"
    if not ctx.db.run(read_tools.asset_by_key, ctx.project_id, key):
        mime = "video/webm" if key.endswith(".webm") else "video/mp4" if kind == "video" else "image/jpeg"
        storage.upload_file(path, key, mime)
        facts = {k: item.get(k) for k in ("provider", "page_url", "credit", "license", "shows", "design")}
        ctx.db.run(db.insert_asset, ctx.project_id, "video_clip" if kind == "video" else "image_ref",
                   key, bytes_=media["bytes"], duration_s=media.get("duration_s"),
                   width=media["width"], height=media["height"], sha256=digest,
                   meta=dict(facts, mcp_film=True, filename=item.get("title") or item["id"]))
    return {"asset_key": key, "kind": kind, "at_output_s": beat["start"],
            "duration_s": beat["end"] - beat["start"], "mute": True,
            "fit": "pad_blur"}


def build(ctx, narration_asset_key=None, goal="Make a documentary from the entire narration."):
    import db
    import read_tools
    from schemas import canvas_edl
    if narration_asset_key:
        source = ctx.db.run(read_tools.asset_by_key, ctx.project_id, narration_asset_key)
    else:
        audio = ctx.db.run(read_tools.assets_by_kinds, ctx.project_id, ["music"])
        source = next((r for r in audio if not (r.get("meta") or {}).get("source")), None)
        source = source or next(iter(audio), None)
    if not source:
        return "Upload the narration with upload_start(kind='music') to build the documentary."
    index = source.get("sha256") and ctx.db.run(read_tools.get_index_by_sha, source["sha256"])
    if not index:
        answer = ctx.start_asset_index(source)
        pending = getattr(ctx, "mcp_pending_analysis", None) or {}
        if pending.get("index_job_id"):
            ctx.mcp_parked_render = {"type": "index", "job_id": pending["index_job_id"]}
        return answer
    narration = source["storage_key"]
    fingerprint = hashlib.sha256(json.dumps([goal, narration, source["sha256"]]).encode()).hexdigest()[:24]
    checkpoint = f"mcp_film:{ctx.project_id}:{fingerprint}"
    state = json.loads(ctx.db.run(db.kv_get, checkpoint) or '{"chapters":[]}')
    if state.get("film"):
        ctx.mcp_film_result = state["film"]
        return f"Documentary already built as EDL v{state['film']['edl_version']}. Preparing its export."
    ctx.model_usage = state.get("model_usage") or ctx.model_usage
    idx = index["json"]
    intervals = chapters(idx)
    for n in range(len(state["chapters"]), len(intervals)):
        start, end = intervals[n]
        rows = beats(idx, start, end)
        if not rows:
            # Silence inside a narration holds the preceding picture; preserve its full clock.
            prior = next((c[-1] for c in reversed(state["chapters"]) if c), None)
            if prior:
                prior["duration_s"] += end - start
        else:
            picture_plan(rows, goal)
        inserts = []
        with tempfile.TemporaryDirectory(prefix="narration_pictures_", dir=ctx.workdir) as work:
            from broll_source import carried

            def acquire(indexed):
                i, beat = indexed
                folder = os.path.join(work, str(i))
                os.makedirs(folder)
                return picture(ctx, beat, folder)

            with ThreadPoolExecutor(max_workers=3) as pool:
                for i, (item, media) in enumerate(pool.map(carried(acquire), enumerate(rows))):
                    insert = publish(ctx, rows[i], item, media)
                    insert["id"] = f"chapter{n+1}picture{i+1}"
                    inserts.append(insert)
        state["chapters"].append(inserts)
        state["model_usage"] = ctx.model_usage
        ctx.db.run(db.kv_put, checkpoint, json.dumps(state))
        if n + 1 < len(intervals):
            job = ctx.job
            payload = job.get("payload") or {}
            child = ctx.db.run(db.enqueue_agent_continuation, ctx.project_id, job["user_id"],
                               payload.get("root_agent_job_id") or job["id"], n + 1, payload,
                               parent_job_id=job["id"], parent_claim=job["total_claims"],
                               job_type="mcp_tool")
            ctx.mcp_parked_render = {"type": "film_chapter", "job_id": child}
            return f"Saved chapter {n+1} of {len(intervals)}. The same job continues the film."
    edl = canvas_edl("16:9")
    edl["inserts"] = [shot for chapter in state["chapters"] for shot in chapter]
    if not edl["inserts"]:
        return "The uploaded audio has no timed speech to illustrate."
    first = edl["inserts"][0]
    first["duration_s"] += first["at_output_s"]
    first["at_output_s"] = 0.0
    edl["voiceover"] = [{"id": "narration", "asset_key": narration, "start_output_s": 0}]
    written = ctx.apply_score(edl, "Built documentary pictures against the complete uploaded narration")
    latest = ctx.latest_edl()
    if not written.startswith(("EDL v", "NO CHANGE")):
        return written
    state["film"] = {"edl_version": latest["version"], "duration_s": intervals[-1][1],
                     "chapters": len(intervals), "pictures": len(edl["inserts"])}
    ctx.db.run(db.kv_put, checkpoint, json.dumps(state))
    ctx.mcp_film_result = state["film"]
    return f"Built the complete documentary as EDL v{latest['version']}. Preparing its final export."
