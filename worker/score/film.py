"""The Director's First cut (BUILD P10 and R1, Director DESIGN 3.5): one seam in first_cut.run, before
video_chain.

    attempt(...) -> (steps, applied, picked) or None

None means today's chain runs, in the same job, exactly as it always has. That happens when the switch
is off (one app_kv read, nothing else), when the account or the source is out of scope, when the day's
trial pacing has no room for a film, and on any failure: a refusal, a late or empty answer, a Score
that compiles to nothing, an EDL the write path refuses. Every failure files through ops_loop intake
(client event director_fell_back) and the customer sees only the chain's result.

Scope: a trial account (a live claude_trial scope, the account rule first_cut_visual.engine_on, never
a subscriber), a video upload of at most 180 s. The film is ordinary EDL items drawn by today's
renderer (score.lower); the write runs the usual checks through ToolContext.apply_score.

    last_look(...) -> None, or the new `applied` list when the film was written again

After the film's preview lands, one model call sets six of its frames under the same six of the upload
(score.taste.look). What it names is removed and the film is rendered once more; when most frames lose
to the upload, the customer gets the plain floor film. Either files a quality ticket (client event
director_taste). A late, failed or unreadable look changes nothing: the film stands as rendered.
"""
import json
import os
import shutil
import time
import uuid

from . import flags

PREP_LEFT_S = 75.0     # what Scout, Supply and Direct need after perception and boards, or there is no film
LOOK_USD = 0.10        # one board and a short answer, reserved on top of the account's allowance like the film
LOOK_S = 30.0          # the look's own deadline
LOOK_BY_S = 300.0      # seconds from the First cut job's start: past this the film stands as rendered


class Fall(Exception):
    def __init__(self, stage, reason):
        super().__init__(f"{stage}: {reason}")
        self.stage, self.reason = stage, str(reason)[:300]


def _say(ctx, worker_db, step, label):
    try:
        import first_cut_speed
        first_cut_speed.progress(ctx, worker_db, getattr(ctx, "first_cut_session", None), step, label)
    except Exception:  # noqa: BLE001 - a status line never sinks the film
        pass


def _event(worker_db, job, kind, detail):
    try:
        import db as dbx
        worker_db.run(dbx.record_client_event, job.get("user_id"), job.get("project_id"), kind, detail)
    except Exception as exc:  # noqa: BLE001
        print(f"[director] {kind} not recorded: {str(exc)[:160]}", flush=True)


def fell(ctx, worker_db, job, stage, reason, usd=0.0, start_edl=None):
    """The film did not land. Files it (ops_loop intake), and puts the start state back if the film
    was written, so the chain starts from the upload."""
    print(f"[director] job {job.get('id')}: fell to the chain at {stage}: {str(reason)[:200]}", flush=True)
    _event(worker_db, job, "director_fell_back", {"job_id": job.get("id"), "stage": stage, "reason": str(reason)[:300],
                                                   "usd": round(float(usd or 0), 5)})
    if start_edl is not None and getattr(ctx, "director_version", None):
        try:
            ctx.apply_score(start_edl, "the First cut starts again from the upload")
        except Exception as exc:  # noqa: BLE001
            print(f"[director] start state not restored: {exc}", flush=True)
        ctx.director_version = None
    close(ctx)


def close(ctx):
    """The film's folder goes when nothing more will read it."""
    st = getattr(ctx, "director_state", None)
    ctx.director_state = None
    if st and st.get("work"):
        shutil.rmtree(st["work"], ignore_errors=True)


def look_on():
    return os.environ.get("DIRECTOR_LOOK", "1") != "0"


def last_look(ctx, worker_db, job, started, spend, trial_scope):
    """The film beside its upload, once, after its preview landed. Returns None when the film stands as rendered,
    or the reply's `applied` list when a new version was written and needs its preview. Never raises, never
    blocks: a look that is late, fails or can't be read leaves the customer the film they already have."""
    st = getattr(ctx, "director_state", None)
    if not st:
        return None
    from . import call, compiler, lower, taste
    import db as dbx
    import storage
    rec, out, reserve = {}, None, 0.0
    spent0 = trial_scope.spent_usd if trial_scope is not None else 0.0
    t0 = time.monotonic()
    try:
        if not look_on():
            rec["skipped"] = "off"
            return None
        if t0 - started > LOOK_BY_S:
            rec["skipped"] = f"late: {t0 - started:.0f} s into the job"
            return None
        preview = getattr(ctx, "last_preview", None) or {}
        asset = worker_db.run(dbx.get_asset, preview["render_asset_id"]) if preview.get("render_asset_id") else None
        key = (asset or {}).get("storage_key")
        if not key:
            rec["skipped"] = "no preview file"
            return None
        work, comp, cfg = st["work"], st["comp"], st["cfg"]
        if trial_scope is not None:
            reserve = LOOK_USD
            _allowance(worker_db, trial_scope, reserve)
        try:
            v, usd = taste.look(work, comp, storage.presign_get(key, expires=900), time.monotonic() + LOOK_S, cfg["models"]["direct"])
        except (call.Late, call.Refused) as exc:
            rec["skipped"] = f"{type(exc).__name__}: {str(exc)[:120]}"
            return None
        what = taste.verdict_of(v)
        rec.update(verdict=v, decision=what, usd=round(usd, 5), look_s=round(time.monotonic() - t0, 1))
        if what == "film":
            return None
        text = taste.floor_text(st["text"]) if what == "floor" else taste.cut_text(st["text"], v["remove"])
        comp2 = compiler.compile_score(text, work_dir=work)
        if comp2.json() == comp.json():            # it named only what the customer asked for, or the caption
            rec["decision"] = "film"
            return None
        edl, locals_, rep = lower.lower(comp2.l1, work)
        edl = _publish(ctx, worker_db, edl, locals_, st["bin_doc"], st["original_key"])
        result = ctx.apply_score(edl, "the Director's First cut, after the last look")
        if not str(result).startswith("EDL v"):
            rec["error"] = f"write: {str(result)[:200]}"
            return None
        ctx.director_first = {"edl": st["edl"], "version": getattr(ctx, "director_version", None)}
        ctx.director_version = (ctx.versions_written or [None])[-1]
        t = comp2.meter.get("taste") or {}
        rec.update(edl_version=ctx.director_version, counts=rep.get("counts"), removed=v["remove"][:24])
        _event(worker_db, job, "director_taste", {
            "job_id": job.get("id"), "decision": what, "pairs": v["pairs"], "for_upload": v["upload"], "removed": v["remove"][:24],
            "why": v["why"][:6], "cls": (t.get("supply") or {}).get("cls"), "edl_version": ctx.director_version})
        out = _applied(rep, comp2.l1, st["bin_doc"])
        return out
    except Exception as exc:  # noqa: BLE001 - the film on screen stands
        rec["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        print(f"[director] job {job.get('id')}: the last look did not finish: {rec['error']}", flush=True)
        return out
    finally:
        try:
            if trial_scope is not None:
                used = trial_scope.spent_usd - spent0
                spend["usd"] = spend.get("usd", 0.0) + used
                _allowance(worker_db, trial_scope, -max(0.0, reserve - used))
        except Exception as exc:  # noqa: BLE001
            print(f"[director] look allowance not settled: {exc}", flush=True)
        phases = dict(getattr(ctx, "first_cut_phases", None) or {})
        phases["director"] = dict(phases.get("director") or {}, look=rec)
        ctx.first_cut_phases = phases
        close(ctx)


def restore_first(ctx):
    """The version the look wrote did not render: the film that already had its preview goes back. True when written."""
    first = getattr(ctx, "director_first", None)
    ctx.director_first = None
    if not first:
        return False
    try:
        result = ctx.apply_score(first["edl"], "the Director's First cut")
    except Exception as exc:  # noqa: BLE001
        print(f"[director] first film not restored: {exc}", flush=True)
        return False
    if not str(result).startswith("EDL v"):
        return False
    ctx.director_version = (ctx.versions_written or [None])[-1]
    return True


# ------------------------------------------------------------------ spend

def _room_today(worker_db, cfg, scope):
    """The pool's daily pacing holds: a day whose trial spend would pass its cap with one more film
    falls to the chain. An internal test account spends outside the pool."""
    if scope.is_test:
        return True
    import claude_trial
    value = claude_trial.flag(worker_db)
    totals = worker_db.run(claude_trial.pool_totals)
    return totals["today"] + cfg["film_usd"] <= float(value["daily_usd_cap"]) and totals["pool"] + cfg["film_usd"] <= float(value["pool_usd_cap"])


def _allowance_tx(conn, user_id, delta):
    with conn.cursor() as cur:
        cur.execute("""UPDATE trial_budgets
                          SET cap_usd = GREATEST(cap_usd + %s, 0), updated_at = NOW(),
                              exhausted_at = CASE WHEN spent_usd < cap_usd + %s THEN NULL ELSE exhausted_at END
                        WHERE user_id = %s""", (float(delta), float(delta), int(user_id)))


def _allowance(worker_db, scope, delta):
    """The film is paid on top of the account's allowance, so its follow-ups keep all of theirs: the
    film's reserve is added before the first call and what it did not spend is taken back after."""
    if scope.is_test or not delta:
        return
    worker_db.run(_allowance_tx, scope.user_id, delta)
    scope.cap_usd = max(0.0, scope.cap_usd + delta)


# ------------------------------------------------------------------ inputs

def _asks(worker_db, session_id):
    """What the customer typed in this project so far, oldest first. The Scout reads it; nothing here does."""
    def read(conn):
        with conn.cursor() as cur:
            cur.execute("""SELECT content FROM chat_messages WHERE session_id = %s AND role = 'user'
                            ORDER BY id DESC LIMIT 3""", (session_id,))
            return cur.fetchall()
    try:
        rows = worker_db.run(read) or []
    except Exception:  # noqa: BLE001
        return []
    out = []
    for r in reversed(rows):
        text = (r.get("content") if isinstance(r, dict) else r[0]) or ""
        if text.strip():
            out.append({"text": text.strip()[:2000]})
    return out


def _acquirer(ctx, worker_db, job, work):
    """A Bin clip is fetched by the egress lane and published there; production orchestration holds no
    media bytes. A still is small and comes back for the card bakes."""
    import config
    import remote
    import storage
    import stock

    def acquire(c):
        want_w, want_h = 1080, 1920
        if config.execution_policy_for(job) == "redesign":
            if not remote.stock_acquire_available():
                return None
            got = remote.run_stock_acquire_remote(ctx.project_id, {"item": c, "want_w": want_w, "want_h": want_h, "review": False,
                                                                   "execution_policy": "redesign"}, user_id=job.get("user_id"))
            if not isinstance(got, dict) or not got.get("ok"):
                return None
            out = {"storage_key": got["storage_key"], "w": got.get("width"), "h": got.get("height"), "dur": got.get("duration_s"),
                   "fps": got.get("fps"), "bytes": got.get("bytes"), "sha256": got.get("sha256")}
            if c.get("kind") != "video":
                local = os.path.join(work, f"still_{uuid.uuid4().hex[:8]}.jpg")
                storage.download_to(got["storage_key"], local)
                out["local"] = local
            return out
        local = os.path.join(work, f"fetch_{uuid.uuid4().hex[:8]}.{'mp4' if c.get('kind') == 'video' else 'jpg'}")
        stock.download(c, local, want_w, want_h)
        import url_media
        kind = url_media.KIND_VIDEO if c.get("kind") == "video" else url_media.KIND_IMAGE
        key = url_media.storage_key(ctx.project_id, kind, local)
        storage.upload_file(local, key, url_media.content_type(local))
        out = {"storage_key": key, "w": c.get("width"), "h": c.get("height"), "dur": c.get("duration_s"), "bytes": os.path.getsize(local)}
        if c.get("kind") != "video":
            out["local"] = local
        return out
    return acquire


# ------------------------------------------------------------------ the EDL's keys

def _publish(ctx, worker_db, edl, locals_, bin_doc, original_key):
    """Every local key in the lowered EDL becomes a project storage key with its asset row."""
    import concurrent.futures as cf
    import db as dbx
    import music_gate
    import motion_tools
    import storage
    keys = {"main": original_key}
    for it in (bin_doc or {}).get("items") or []:
        keys[f"bin:{it['id']}"] = it["storage_key"]
        if not worker_db.run(dbx.asset_by_key, ctx.project_id, it["storage_key"]):
            worker_db.run(dbx.insert_asset, ctx.project_id, "video_clip" if it["kind"] == "video" else "image_ref", it["storage_key"],
                          duration_s=it.get("dur"), width=it.get("w"), height=it.get("h"), fps=it.get("fps"),
                          meta={"filename": f"{(it.get('what') or 'stock')[:60]}.{'mp4' if it['kind'] == 'video' else 'jpg'}", "stock": True,
                                "provider": it.get("provider"), "credit": it.get("author"), "license": it.get("licence"), "director": True})

    def up(k):
        p = locals_[k]
        ext = os.path.splitext(p)[1].lower()
        key = f"fetched/{ctx.project_id}/{uuid.uuid4().hex[:12]}{ext}"
        storage.upload_file(p, key, "audio/wav" if ext == ".wav" else "image/png")
        return k, key, ext, os.path.getsize(p)
    baked = [k for k in locals_ if k.startswith("today/")]
    with cf.ThreadPoolExecutor(max_workers=6) as pool:
        for k, key, ext, size in pool.map(up, baked):
            worker_db.run(dbx.insert_asset, ctx.project_id, "music" if ext == ".wav" else "image_ref", key, bytes_=size,
                          meta={"filename": os.path.basename(k), "director": True, "made_from": "the upload" if ext == ".wav" else "design"})
            keys[k] = key
            if ext == ".wav":
                music_gate.grant_sfx(ctx, key)
    for s in edl.get("sfx") or []:
        k = s["storage_key"]
        if k.startswith("sound:") and k not in keys:
            keys[k] = motion_tools.ensure_library_asset(ctx, k[6:])
            music_gate.grant_sfx(ctx, keys[k])
    missing = set()
    for lane in ("overlays", "inserts"):
        for it in edl.get(lane) or []:
            if it.get("asset_key") in keys:
                it["asset_key"] = keys[it["asset_key"]]
            else:
                missing.add(it.get("asset_key"))
    for s in edl.get("sfx") or []:
        if s["storage_key"] in keys:
            s["storage_key"] = keys[s["storage_key"]]
        else:
            missing.add(s["storage_key"])
    if missing:
        raise Fall("publish", f"no storage key for {sorted(map(str, missing))[:4]}")
    ctx.scene_keys = dict(keys)        # lane D: the plan's pictures by the same keys (engine_branch.keep_plan)
    return edl


def _applied(rep, l1, bin_doc):
    """What the reply names, from the film's own counts."""
    c = rep.get("counts") or {}
    out = []
    cuts = sum(1 for i in l1.get("items") or [] if i.get("k") == "shot")
    if cuts:
        out.append(f"{cuts} shots")
    if c.get("captions"):
        out.append("captions")
    found = len((bin_doc or {}).get("items") or [])
    if found:
        out.append(f"{found} found pictures")
    if c.get("texts") or rep.get("script_type_stills"):
        out.append("titles")
    if c.get("sfx"):
        out.append("sound on its key moments")
    return out or ["a directed cut"]


# ------------------------------------------------------------------ the film

def attempt(ctx, worker_db, job, started, spend, trial_scope, subscribed):
    cfg = flags.read(worker_db)
    payload = job.get("payload") or {}
    ok, why = flags.admit(cfg, job.get("user_id"), trial_active=trial_scope is not None, subscribed=subscribed,
                          engine_on=bool(getattr(ctx, "first_cut_q1", False)), source_s=getattr(ctx, "duration", None), url=payload.get("url"))
    if not ok:
        if why not in ("off", "not_trial", "subscribed", "url"):
            ctx.first_cut_phases = dict(getattr(ctx, "first_cut_phases", None) or {}, director={"skipped": why})
        return None
    try:
        if not _room_today(worker_db, cfg, trial_scope):
            ctx.first_cut_phases = dict(getattr(ctx, "first_cut_phases", None) or {}, director={"skipped": "daily_pacing"})
            _event(worker_db, job, "director_skipped", {"job_id": job.get("id"), "why": "daily_pacing"})
            return None
    except Exception as exc:  # noqa: BLE001 - pacing unreadable: no film on doubt
        fell(ctx, worker_db, job, "pacing", exc)
        return None
    deadline = started + cfg["deadline_s"]
    work = os.path.join(ctx.workdir, "film")
    rec = {"stages": {}}
    spent0 = trial_scope.spent_usd
    start_edl = None
    reserve = 0.0
    try:
        start_edl = (ctx.latest_edl() or {}).get("json")
        reserve = cfg["film_usd"]
        _allowance(worker_db, trial_scope, reserve)
        steps, applied, picked = _make(ctx, worker_db, job, cfg, deadline, work, rec)
        rec["usd"] = round(trial_scope.spent_usd - spent0, 5)
        spend["usd"] = spend.get("usd", 0.0) + rec["usd"]
        _event(worker_db, job, "director_film", {"job_id": job.get("id"), "usd": rec["usd"], "stages": rec["stages"],
                                                 "edl_version": getattr(ctx, "director_version", None)})
        return steps, applied, picked
    except Exception as exc:  # noqa: BLE001 - every failure falls to the chain
        usd = trial_scope.spent_usd - spent0
        rec["usd"] = round(usd, 5)
        spend["usd"] = spend.get("usd", 0.0) + usd
        stage = exc.stage if isinstance(exc, Fall) else rec.get("at", "film")
        fell(ctx, worker_db, job, stage, exc if isinstance(exc, Fall) else f"{type(exc).__name__}: {str(exc)[:240]}", usd, start_edl)
        return None
    finally:
        try:
            _allowance(worker_db, trial_scope, -max(0.0, reserve - (trial_scope.spent_usd - spent0)))
        except Exception as exc:  # noqa: BLE001
            print(f"[director] allowance not settled: {exc}", flush=True)
        ctx.first_cut_phases = dict(getattr(ctx, "first_cut_phases", None) or {}, director=rec)
        if getattr(ctx, "director_state", None) is None:        # a film that landed keeps its folder for the last look
            shutil.rmtree(work, ignore_errors=True)


def _keep_scene_plan(ctx, l1, work, rec):
    """Lane D: the plan beside the EDL just written, so the engine may draw exactly this version. Kept only
    while app_kv.director.engine is shadow or on; the render reads the switch again. Never sinks the film."""
    try:
        import engine_branch
        row = ctx.latest_edl()
        if row and row.get("version") == ctx.director_version:
            rec["scene_plan"] = engine_branch.keep_plan(ctx.project_id, row["json"], l1, getattr(ctx, "scene_keys", {}) or {}, work)
    except Exception as exc:  # noqa: BLE001
        print(f"[director] scene plan not kept: {str(exc)[:160]}", flush=True)


def _stage(rec, name, t0):
    rec["stages"][name] = round(time.monotonic() - t0, 1)


def _make(ctx, worker_db, job, cfg, deadline, work, rec):
    from . import brief, call, direct, lower, perceive, scout, supply
    import db as dbx
    os.makedirs(work, exist_ok=True)
    rec["at"] = "prep"
    t = time.monotonic()
    index = ctx.index or {}
    os.symlink(ctx.proxy_path(), os.path.join(work, "source.mp4"))
    with open(os.path.join(work, "index.json"), "w") as f:
        json.dump(index, f, ensure_ascii=False)
    original = worker_db.run(dbx.latest_asset, ctx.project_id, "original") or {}
    if not original.get("storage_key"):
        raise Fall("prep", "no original asset")
    name = ((original.get("meta") or {}).get("filename")) or ""
    sess = {"prompts": _asks(worker_db, getattr(ctx, "first_cut_session", None)), "uploads": [{"filename": name}] if name else []}
    ledger = getattr(ctx, "ask_ledger", None)
    if ledger and ledger.get("asks"):
        sess["ledger"] = [{"said": a["said"], "want": a["want"]} for a in ledger["asks"]]
    _say(ctx, worker_db, "director_read", "Reading your video")
    per = perceive.run(index, os.path.join(work, "source.mp4"), work)
    grid, _times = brief.boards_for(work, index, per)
    _stage(rec, "prep", t)
    if deadline - time.monotonic() < PREP_LEFT_S:
        raise Fall("prep", f"{deadline - time.monotonic():.0f} s left after reading the footage")

    rec["at"] = "scout"
    t = time.monotonic()
    try:
        sc = scout.run(work, index, sess, per, min(deadline - 60.0, time.monotonic() + flags.SCOUT_S), model=cfg["models"]["scout"])
    except call.Refused:
        raise
    except Exception as exc:  # noqa: BLE001 - DESIGN 3.5: scope film, no asks, the whole source
        rec["scout_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        sc = None
    _stage(rec, "scout", t)

    rec["at"] = "supply"
    t = time.monotonic()
    _say(ctx, worker_db, "director_supply", "Finding pictures for it")
    bin_doc = None
    if sc and sc.get("wishes"):
        try:
            bin_doc = supply.run(work, sc, per, _acquirer(ctx, worker_db, job, work), min(deadline - 70.0, time.monotonic() + flags.SUPPLY_S))
        except call.Refused:
            raise
        except Exception as exc:  # noqa: BLE001 - DESIGN 3.5: the upload and plates
            rec["supply_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    _stage(rec, "supply", t)

    rec["at"] = "direct"
    t = time.monotonic()
    _say(ctx, worker_db, "director_direct", "Directing your film")
    brief.build(work, index, sess, per, sc, grid)
    try:
        comp, text, usage = direct.direct(work, index, sc, deadline, model=cfg["models"]["direct"], effort=cfg["effort"]["direct"],
                                          budget_usd=cfg["film_usd"], spent_usd=0.0)
    except call.Late as exc:
        raise Fall("direct", f"late: {exc}")
    except call.Refused as exc:
        raise Fall("direct", f"refused: {exc}")
    except direct.Empty as exc:
        raise Fall("direct", f"empty: {exc}")
    with open(os.path.join(work, "score.jsonl"), "w", encoding="utf-8") as f:
        f.write(text)
    rec["direct"] = {k: usage.get(k) for k in ("rounds", "q_first", "q_repair", "repair_kept", "asks_open", "truncated")}
    m = comp.meter
    rec["meter"] = {k: m.get(k) for k in ("dur_s", "beats", "cuts10", "new_pictures10", "type_events10", "captions", "cards", "flashes", "sounds10", "ceiling")}
    rec["meter"].update(design_items10=(m.get("facts") or {}).get("design_items10"), gestures=len(m.get("gestures") or {}),
                        rejects=len(m.get("rejects") or []), findings=len(m.get("findings") or []))
    _stage(rec, "direct", t)

    rec["at"] = "lower"
    t = time.monotonic()
    edl, locals_, rep = lower.lower(comp.l1, work)
    rec["lower"] = {k: rep.get(k) for k in ("counts", "burns_as_light", "burns_as_glow", "missing_pictures", "fonts_replaced")}
    edl = _publish(ctx, worker_db, edl, locals_, bin_doc, original["storage_key"])
    _stage(rec, "lower", t)

    rec["at"] = "write"
    result = ctx.apply_score(edl, "the Director's First cut")
    if not str(result).startswith("EDL v"):
        raise Fall("write", str(result)[:300])
    ctx.director_version = (ctx.versions_written or [None])[-1]
    rec["at"] = "written"
    rec["taste"] = usage.get("taste")
    # what the last look needs once the preview is on screen (last_look closes it)
    ctx.director_state = {"work": work, "text": text, "comp": comp, "edl": edl, "bin_doc": bin_doc, "original_key": original["storage_key"], "cfg": cfg}
    if flags.engine_mode(cfg, job.get("user_id")) != "off":
        _keep_scene_plan(ctx, comp.l1, work, rec)
    applied = _applied(rep, comp.l1, bin_doc)
    steps = [{"tool": "director", "ok": True, "s": round(sum(rec["stages"].values()), 1), "note": str(result)[:160]}]
    return steps, applied, {"how": "director", "edl_version": ctx.director_version}
