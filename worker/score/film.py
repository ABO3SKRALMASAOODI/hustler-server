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

Never below today's first cut (13 Nov, lane K): a film that compiles to the upload again but for its colour is not
written (`bare`), and a film the last look leaves bare, or most of whose frames lose for no device it can name, is
taken back; in both cases the chain runs in the same job. `taste.against` is the same critic for a project that already holds a first film:
the new film replaces it only when the critic does not find it worse.
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


def bare(comp):
    """Is this film the upload again but for its colour? Nothing stood that a viewer would call an edit: no picture
    added, no cut moved, type on under an eighth of it. Such a film is never delivered: today's first cut is
    (p5582, 13 Nov: a 3 s product hold came back with a grade and nothing else, below the film it replaced)."""
    return bool(((comp.meter or {}).get("facts") or {}).get("bare"))


def fell(ctx, worker_db, job, stage, reason, usd=0.0, start_edl=None):
    """The film did not land. Files it (ops_loop intake), and puts the start state back if the film
    was written, so the chain starts from the upload."""
    why = getattr(ctx, "director_fell_why", None)
    if why:                 # the last look sent the film back: first_cut reports it as a render that is not on screen
        stage, reason, ctx.director_fell_why = "look", why, None
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


UPGRADE_BY_S = 540.0   # seconds from the First cut job's start: footage that is not there by then is not waited for (the box, 14 Nov: 397 s for ten clips)
UPGRADE_POLL_S = 2.0   # how often the wait asks whether the customer typed something


def close(ctx):
    """The film's folder goes when nothing more will read it. A film whose real footage is still being fetched keeps
    it for `upgrade`: the quick film is the customer's now, and the footage joins it when it arrives."""
    st = getattr(ctx, "director_state", None)
    ctx.director_state = None
    box = (st or {}).get("footage")
    if st and box is not None and st.get("acquire") and not st.get("footage_joined") and getattr(ctx, "director_version", None) is not None \
            and (box.get("offers") or (box.get("thread") is not None and box["thread"].is_alive())):
        ctx.director_upgrade = st
        return
    if st and st.get("work"):
        shutil.rmtree(st["work"], ignore_errors=True)


def _bed_again(ctx, work, comp, text, about=""):
    """A film written again at another length (the look took a beat out, footage came in) is not the film its bed was
    cut for: lane MUSIC2's arranged track would give way to the plain bed. The track is cut for this film instead.
    Nothing happens when the length held, which is nearly always; never raises."""
    try:
        from . import bed, compiler
        doc = bed.chosen(work)
        arr = (doc or {}).get("arranged")
        if not arr:
            return
        prog = float(comp.l1["dur_f"]) / float(comp.l1["fps"])
        if abs(float(arr.get("dur_s") or 0.0) - prog) <= 0.15:
            return
        bed.settle(work, comp, getattr(ctx, "index", None) or {}, compiler.parse_score(text)[0], about)
    except Exception as exc:  # noqa: BLE001 - the plain bed plays
        print(f"[director] bed not cut again: {type(exc).__name__}: {str(exc)[:160]}", flush=True)


def _request_waiting(conn, project_id, job_id):
    """A request of the customer's is queued behind this job (one editor a project: it runs when this job ends)."""
    with conn.cursor() as cur:
        cur.execute("""SELECT 1 FROM video_jobs WHERE project_id = %s AND id <> %s AND state = 'queued'
                          AND type IN ('agent_turn', 'first_cut') LIMIT 1""", (int(project_id), int(job_id)))
        return cur.fetchone() is not None


def upgrade(ctx, worker_db, job, started):
    """The quick film, then the upgraded one (round six). The footage engine takes minutes and the first film has a
    deadline, so the film went out on the pictures Supply found. Here, after the customer has that film and its
    reply, the job waits for the engine (to UPGRADE_BY_S from its start, and not one poll longer once the customer
    asks for anything), sets the clips in place of the stills of the same subjects (footage_into: the same cuts, the
    same lengths, nothing else changed, no model) and writes that as the next version. Returns {"clips", "named",
    "edl_version", "applied"} when a version was written (the caller renders it and says so), else None. Never
    raises; the film on screen stands on every other path. Only an account the footage switch opens gets here."""
    st = getattr(ctx, "director_upgrade", None)
    ctx.director_upgrade = None
    if not st:
        return None
    from . import compiler, lower
    rec, out = {}, None
    work, box = st["work"], st["footage"]
    try:
        th = box.get("thread")
        by = started + UPGRADE_BY_S
        while th is not None and th.is_alive() and time.monotonic() < by:
            th.join(UPGRADE_POLL_S)
            try:
                if worker_db.run(_request_waiting, ctx.project_id, job.get("id")):
                    rec["skipped"] = "the customer asked for something: their request runs now"
                    return None
            except Exception:  # noqa: BLE001 - unread: the wait goes on to its own deadline
                pass
        rec["waited_s"] = round(time.monotonic() - started, 1)
        if "offers" not in box:
            rec["skipped"] = f"the footage was not there {UPGRADE_BY_S:.0f} s into the job"
            return None
        row = ctx.latest_edl()
        if not row or row.get("version") != getattr(ctx, "director_version", None):
            rec["skipped"] = "the film on screen is no longer the one this footage was found for"
            return None
        full = footage_into(work, st["text"], box, st.get("acquire"))
        if full is None:
            rec["skipped"] = box.get("error") or "no clip of a subject the film shows a still of"
            return None
        text, rows = full
        comp2 = compiler.compile_score(text, work_dir=work)
        if bare(comp2) or comp2.json() == st["comp"].json():
            rec["skipped"] = "the footage changed nothing on screen"
            return None
        bin_doc = dict(st.get("bin_doc") or {}, items=list((st.get("bin_doc") or {}).get("items") or []) + rows)
        _bed_again(ctx, work, comp2, text, st.get("about") or "")
        edl, locals_, rep = lower.lower(comp2.l1, work)
        edl = _publish(ctx, worker_db, edl, locals_, bin_doc, st["original_key"])
        result = ctx.apply_score(edl, "the Director's First cut, with real footage")
        if not str(result).startswith("EDL v"):
            rec["error"] = f"write: {str(result)[:200]}"
            return None
        ctx.director_version = (ctx.versions_written or [None])[-1]
        named = []
        for r in rows:
            if r.get("named") and r["named"] not in named:
                named.append(r["named"])
        out = {"clips": len(rows), "named": named[:4], "edl_version": ctx.director_version, "applied": _applied(rep, comp2.l1, bin_doc), "was": row.get("json"),
               "sources": sorted({str(r.get("page")) for r in rows})[:8]}
        rec.update({k: out[k] for k in ("clips", "named", "edl_version", "sources")}, engine_s=box.get("seconds"), usd=box.get("usd"))
        _event(worker_db, job, "director_footage", {"job_id": job.get("id"), "clips": len(rows), "edl_version": ctx.director_version,
                                                    "engine_s": box.get("seconds"), "sources": out["sources"]})
        return out
    except Exception as exc:  # noqa: BLE001 - the quick film stands
        rec["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        print(f"[director] job {job.get('id')}: the footage did not join the film: {rec['error']}", flush=True)
        return None
    finally:
        phases = dict(getattr(ctx, "first_cut_phases", None) or {})
        phases["director"] = dict(phases.get("director") or {}, footage_later=rec)
        ctx.first_cut_phases = phases
        shutil.rmtree(work, ignore_errors=True)


def look_on():
    return os.environ.get("DIRECTOR_LOOK", "1") != "0"


def last_look(ctx, worker_db, job, started, spend, trial_scope, proof=None):
    """The film beside its upload, once, after its preview landed. Returns None when the film stands as rendered,
    or the reply's `applied` list when a new version was written and needs its preview. Never raises, never
    blocks: a look that is late, fails or can't be read leaves the customer the film they already have.

    proof: (signed url, {programme second: reel second}) of a low-resolution proof of the look's moments, rendered
    beside the film's one full render (SPEED, 14 Nov: first_cut._director_early_look). The look then reads the proof
    and what it names is removed before the film's full render, never after it."""
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
        if proof is not None:
            film_url, film_at = proof
            rec["proof"] = {"pairs": len(film_at)}
        else:
            preview = getattr(ctx, "last_preview", None) or {}
            asset = worker_db.run(dbx.get_asset, preview["render_asset_id"]) if preview.get("render_asset_id") else None
            key = (asset or {}).get("storage_key")
            if not key:
                rec["skipped"] = "no preview file"
                return None
            film_url, film_at = storage.presign_get(key, expires=900), None
        work, comp, cfg = st["work"], st["comp"], st["cfg"]
        if trial_scope is not None:
            reserve = LOOK_USD
            _allowance(worker_db, trial_scope, reserve)
        try:
            v, usd = taste.look(work, comp, film_url, time.monotonic() + LOOK_S, cfg["models"]["direct"],
                               **({"proof": film_at} if film_at is not None else {}))
        except (call.Late, call.Refused) as exc:
            rec["skipped"] = f"{type(exc).__name__}: {str(exc)[:120]}"
            return None
        what = taste.verdict_of(v)
        rec.update(verdict=v, decision=what, usd=round(usd, 5), look_s=round(time.monotonic() - t0, 1))
        v = v or {"pairs": 0, "upload": 0, "remove": [], "why": []}        # no verdict: the film stands, and footage may still join it
        # The full film: real footage that arrived while the quick film was written and rendered takes the place of
        # the stills of the same subjects, in the same render the look may ask for. Never waited for.
        full = None
        if what != "floor":
            try:
                full = footage_into(work, st["text"], st.get("footage"), st.get("acquire"))
            except Exception as exc:  # noqa: BLE001 - the quick film stands
                rec["footage_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        if full is not None:
            st["text"], rows = full
            st["footage_joined"] = True
            st["bin_doc"] = dict(st.get("bin_doc") or {}, items=list((st.get("bin_doc") or {}).get("items") or []) + rows)
            rec["footage"] = {"clips": len(rows), "seconds": (st.get("footage") or {}).get("seconds"),
                              "sources": sorted({str(r.get("page")) for r in rows})[:8]}
        elif st.get("footage") is not None:
            fb = st["footage"]
            rec["footage"] = {"clips": 0, "why": fb.get("error") or ("not there yet" if "offers" not in fb else "none found, or no still of its subject in the film"),
                              "report": [r for r in fb.get("report") or [] if isinstance(r, dict) and any(k.startswith("footage") for k in r)][:3]}
        if what == "film" and full is None:
            return None
        # What the look named goes, at the floor too: every frame it judged is then the upload's own or better, and
        # what stood on the others stays. Only when nothing it can name explains a floor, or what is left is the
        # upload again, is the film taken back: the start state is written, first_cut finds the film is not on
        # screen and runs today's chain in the same job. The plain floor film is no longer anyone's first film
        # (rated under the cut it replaced, 13 Nov).
        comp2 = None
        if v["remove"] and what != "film":
            text = taste.cut_text(st["text"], v["remove"])
            comp2 = compiler.compile_score(text, work_dir=work)
            if comp2.json() == comp.json():            # it named only what the customer asked for, or the caption
                comp2 = None
        elif full is not None:
            comp2 = compiler.compile_score(st["text"], work_dir=work)
            if comp2.json() == comp.json():
                comp2 = None
        if comp2 is None and what != "floor":
            rec["decision"] = "film"
            return None
        if full is not None and comp2 is not None:
            rec["decision"] = what = "full" if what == "film" else what
        if comp2 is None or bare(comp2):
            start = st.get("start_edl")
            back = ctx.apply_score(start, "the First cut starts again from the upload") if start is not None else ""
            if str(back).startswith("EDL v"):
                ctx.director_version = None
                ctx.director_fell_why = f"the last look: {v['upload']} of {v['pairs']} frames for the upload, " + ("nothing it could name" if comp2 is None else "nothing left but the upload")
                rec["decision"] = "chain"
                _event(worker_db, job, "director_taste", {"job_id": job.get("id"), "decision": "chain", "pairs": v["pairs"], "for_upload": v["upload"],
                                                         "removed": v["remove"][:24], "why": v["why"][:6]})
                return None
            text = taste.floor_text(st["text"])       # the start state could not be written: the plain film, as before
            comp2 = compiler.compile_score(text, work_dir=work)
        _bed_again(ctx, work, comp2, text if v["remove"] and what != "film" else st["text"], st.get("about") or "")
        edl, locals_, rep = lower.lower(comp2.l1, work)
        edl = _publish(ctx, worker_db, edl, locals_, st["bin_doc"], st["original_key"])
        result = ctx.apply_score(edl, "the Director's First cut, after the last look")
        if not str(result).startswith("EDL v"):
            rec["error"] = f"write: {str(result)[:200]}"
            return None
        ctx.director_first = {"edl": st["edl"], "version": getattr(ctx, "director_version", None)}
        ctx.director_version = (ctx.versions_written or [None])[-1]
        if v["remove"] and what != "film":          # what `upgrade` builds on is the film as the look left it
            st["text"], st["comp"] = text, comp2
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
        storage.upload_file(p, key, {".wav": "audio/wav", ".mp4": "video/mp4"}.get(ext, "image/png"))
        return k, key, ext, os.path.getsize(p)
    baked = [k for k in locals_ if k.startswith("today/")]
    with cf.ThreadPoolExecutor(max_workers=6) as pool:
        for k, key, ext, size in pool.map(up, baked):
            if ext == ".mp4":
                # a found still baked as a clip of its own frames (score.reel): a video asset with its length, so a tool
                # that reads the row later places it as the clip it is, never as an image
                try:
                    with open(locals_[k] + ".json") as f:
                        clip = json.load(f)
                except (OSError, ValueError):
                    clip = {}
                worker_db.run(dbx.insert_asset, ctx.project_id, "video_clip", key, bytes_=size, duration_s=clip.get("dur"), width=clip.get("w"),
                              height=clip.get("h"), fps=clip.get("fps"),
                              meta={"filename": os.path.basename(k), "director": True, "made_from": "a found still, moving", "finish": clip.get("finish")})
                keys[k] = key
                continue
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
    _publish_bed(ctx, worker_db, edl)
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


BED_WAIT_S = 10.0          # how long the write waits for a chosen track to reach the project; past it the film goes out dry


def _publish_bed(ctx, worker_db, edl):
    """The bed's local key becomes the project's own music asset (the library copy, with its licence and credit). A
    track that is not in storage in time, or any fault, takes the bed and its drop out of this film: it is written
    dry, exactly as before round five, and never waits on music."""
    music = edl.get("music") or []
    if not music:
        return
    from . import bed
    st = getattr(ctx, "director_state", None) or getattr(ctx, "director_bed", None) or {}
    work = st.get("work")
    doc = bed.chosen(work) if work else None
    key, err = None, "no chosen bed on file"
    if doc:
        box = {}

        def run():
            try:
                box["got"] = bed.project_asset(ctx, worker_db, doc)
            except Exception as exc:  # noqa: BLE001
                box["got"] = (None, f"{type(exc).__name__}: {str(exc)[:160]}")
        import threading
        th = threading.Thread(target=run, name="film-bed-asset", daemon=True)
        th.start()
        th.join(BED_WAIT_S)
        key, err = box.get("got") or (None, f"the track was not in the project after {BED_WAIT_S:.0f} s")
    if key:
        for m in music:
            m["storage_key"] = key
        return
    print(f"[director] the film goes out without its bed: {str(err)[:200]}", flush=True)
    edl["music"] = []
    edl["fx"] = [f for f in edl.get("fx") or [] if f.get("stage") != "music_bus"]
    if work:
        bed.unset(work, err)
    ctx.director_bed_fell = str(err)[:200]


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
    if rep.get("bed"):
        out.append("a music bed you can keep or remove")
    return out or ["a directed cut"]


# ------------------------------------------------------------------ real footage: the quick film, then the full one

def footage_start(job, work, sc, per):
    """Real footage of the people, places and events the film names (lane FOOT's engine, worker/footage), found on
    a thread beside Supply, Direct and the first render. The engine takes minutes and the first film has a deadline,
    so the film is written without waiting for it (the quick film). When its clips are there by the time the last
    look has read the film, they take the place of the stills of the same subjects and the film is rendered once
    more (the full film): `footage_into`. Only for an account the footage switch opens (app_kv footage, the owner's
    decision; off by default, and then nothing here runs). Returns the box the thread fills, or None."""
    try:
        import footage.seam as seam
        import footage.switch as switch
        uid = job.get("user_id")
        if not switch.allowed("customer", uid, seam.customer_cfg()):
            return None
        wishes = [dict(w) for w in (sc or {}).get("wishes") or [] if isinstance(w, dict) and not w.get("mine")]
        if not wishes:
            return None
        import threading
        import broll_source
        from . import supply
        box = {"t0": time.monotonic(), "wishes": wishes, "report": []}
        about = str((sc or {}).get("about") or "")
        W, H = (1080, 1920) if int((per or {}).get("H") or 16) >= int((per or {}).get("W") or 9) else (1920, 1080)

        def run():
            try:
                subjects, usd = supply.name_wishes(work, wishes, about)
                box["usd"] = float(usd or 0.0)
                if any(subjects):
                    box["offers"] = seam.bin_offers(work, wishes, subjects, time.monotonic() + 3600.0, box["report"], about, uid, W, H)
                else:
                    box["offers"] = []
            except Exception as exc:  # noqa: BLE001 - the film stands on what Supply found
                box["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
                box.setdefault("offers", [])
            box["seconds"] = round(time.monotonic() - box["t0"], 1)
        th = threading.Thread(target=broll_source.carried(run), name="film-footage", daemon=True)
        th.start()
        box["thread"] = th
        return box
    except Exception as exc:  # noqa: BLE001
        print(f"[director] footage not started: {type(exc).__name__}: {str(exc)[:160]}", flush=True)
        return None


def footage_into(work, text, box, acquire, most=8):
    """The Score with real footage in it, or None when there is none to add. Each clip the engine cut (checked there:
    it shows its subject, and a person's clips are held to that person's face) joins the Bin with its source and
    credit, and takes the place of one still of the same moment in the film: the same cut, the same length, now a
    moving picture of the real thing. A moment the film showed no picture for gets its clip cut in on its word
    (_place_clips). Nothing else in the Score changes and no model is called. Returns (score text, [new Bin rows])."""
    from . import compiler, direct
    offers = [c for c in (box or {}).get("offers") or [] if isinstance(c, dict) and c.get("kind") == "video"]
    if not offers:
        return None
    try:
        with open(os.path.join(work, "bin.json"), encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    items = doc.get("items") or []
    wishes = box.get("wishes") or []
    # A clip takes the place of a still of the same subject (the Bin names it in `named`), or of the same moment (the
    # Bin names the words it lands on in `for`): the film opens on a photograph of the driver, the clip is that driver.
    def name_of(x):
        return "n:" + str(x).strip().casefold() if x else None
    by_for = {}
    for c in offers:
        w = wishes[c["_wish"]] if isinstance(c.get("_wish"), int) and 0 <= c["_wish"] < len(wishes) else {}
        moment = "f:" + (f"w{w.get('w')}" if w.get("w") else str(w.get("at")))
        for key in (name_of(c.get("named")), moment):
            if key and c not in by_for.setdefault(key, []):
                by_for[key].append(c)
    stills = {}
    for it in items:
        if it.get("provider") == "footage" or it.get("kind") == "video":
            continue                                   # a clip that already moves keeps its place
        key = next((k for k in (name_of(it.get("named")), "f:" + str(it.get("for"))) if k in by_for), None)
        if key:
            stills.setdefault(key, []).append(f"bin:{it['id']}")
    lines, _bad = compiler.parse_score(text)
    lines = [direct.clean(o) for o in lines]
    used, rows, swapped, took = {}, [], 0, {}

    def clip_for(key):
        """The next clip of this moment as a Bin row, acquired once."""
        pool = by_for.get(key) or []
        n = used.get(key, 0)
        while n < len(pool) and pool[n].get("_row") is None and not _reads(pool[n].get("_img")):
            pool[n]["_row"] = False                    # a frame that is mostly black, or flat: the cover crop missed what the clip shows
            n += 1
        if n >= len(pool):
            used[key] = n
            return None                                # every clip of this subject is in the film once: the next still stays a still
        c = pool[n]
        used[key] = n + 1
        if c.get("_row") is None:
            if len(rows) >= most:
                return None
            got = acquire(c)
            if not got or not got.get("storage_key"):
                c["_row"] = False
                return None
            iid = len(items) + len(rows)
            rel = f"bin/{iid}.jpg"
            try:
                c["_img"].convert("RGB").save(os.path.join(work, rel), quality=90)
            except Exception:  # noqa: BLE001 - a clip with no frame to stand for it is not cut in
                c["_row"] = False
                return None
            c["_row"] = dict(id=iid, kind="video", path=rel, w=got.get("w") or c.get("width"), h=got.get("h") or c.get("height"), storage_key=got["storage_key"],
                             dur=round(float(got.get("dur") or c.get("duration_s") or 0), 2), fps=got.get("fps"),
                             what=f"{c.get('named')}: {c.get('shows') or ''}".strip(": "), wish=None, **{"for": key[2:], "class": "F"},
                             licence=c.get("license"), licence_url=c.get("page_url"), provider="footage", author=c.get("credit"), page=c.get("page_url"),
                             named=c.get("named"), shows=c.get("shows"), footage=c.get("footage"))
            rows.append(c["_row"])
        return f"bin:{c['_row']['id']}" if c["_row"] else None

    def swap(ref):
        nonlocal swapped
        name = ref if isinstance(ref, str) else ref.get("src") if isinstance(ref, dict) else None
        if not isinstance(name, str):
            return ref
        head = name.split("@")[0]
        key = next((k for k, v in stills.items() if head in v), None)
        if key is None:
            return ref
        if head not in took:                           # a still that comes back (a callback) comes back as the same clip
            took[head] = clip_for(key)
        new = took[head]
        if new is None:
            return ref
        swapped += 1
        # the framing, the move and the finish were written for the still (an old photograph in black and white): the clip is another picture
        return new if isinstance(ref, str) else dict({k: v for k, v in ref.items() if k not in ("crop", "zoom", "cam", "treat")}, src=new)
    for o in lines:
        if o.get("k") == "b" and o.get("pic") is not None:
            o["pic"] = [swap(r) for r in o["pic"]] if isinstance(o["pic"], list) else swap(o["pic"])
        elif o.get("k") == "pics" and isinstance(o.get("of"), list):
            o["of"] = [swap(r) for r in o["of"]]
    placed = 0
    try:                 # a moment the film showed no picture for (Supply found none in time): its clip is cut in on its word
        def bin_now():      # the compiles below read the Bin from the folder: the clips that joined so far are in it
            with open(os.path.join(work, "bin.json"), "w") as f:
                json.dump(dict(doc, items=items + rows), f, ensure_ascii=False, indent=1)
        placed = _place_clips(work, lines, wishes, offers, by_for, used, clip_for, name_of, rows, bin_now)
    except Exception as exc:  # noqa: BLE001 - the clips that took a still's place stand
        print(f"[director] footage not cut in on its words: {type(exc).__name__}: {str(exc)[:160]}", flush=True)
    if not (swapped or placed) or not rows:
        return None
    doc["items"] = items + rows
    doc["credits"] = list(doc.get("credits") or []) + [f"{r['what']}: {r['author'] or 'footage'}, {r['licence']}, {r['page']}" for r in rows]
    with open(os.path.join(work, "bin.json"), "w") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    return direct.dump(lines), rows


def _reads(img):
    """A clip's frame, as it was cut to the film's shape, shows something: under three fifths of it is near black and
    it is not one flat tone. A clip of a person cut from a wide source can come out as the dark wall beside them
    (p5591, 14 Nov: an elbow at the edge of a black frame, and the critic set the film under its upload for it)."""
    try:
        import numpy as np
        a = np.asarray(img.convert("L").resize((48, 80)), dtype=np.float32)
        return float((a < 31.0).mean()) < 0.6 and float(a.std()) >= 8.0
    except Exception:  # noqa: BLE001 - unread: the engine's own check stands
        return True


INSERT_F = 36          # a clip cut in on a moment the film showed no picture for plays this long (1.2 s)
INSERT_GAP_F = 54      # and no two of them land closer than this
INSERT_MOST = 6


def _place_clips(work, lines, wishes, offers, by_for, used, clip_for, name_of, rows, bin_now):
    """Real footage for the moments the film showed no picture for. The Scout wished a picture on those words, no
    library had one, and the film was written on the take alone there; the engine has since cut a clip of that very
    subject. The clip is cut in on the wish's first word, over the take, for INSERT_F frames: the beat's own `cut`
    and `pic` are rewritten so the take plays to the word, the clip plays, the take comes back, and the voice runs
    on under it. Never a beat whose shots another line names (an id would move), never two closer than
    INSERT_GAP_F, never inside the first half second. The Score is compiled before and after: a film that does not
    compile, or comes out another length, keeps none of them. Returns how many were cut in."""
    import copy
    import re
    from . import compiler, direct
    with open(os.path.join(work, "index.json"), encoding="utf-8") as f:
        words = (json.load(f) or {}).get("words") or []
    text0 = direct.dump(lines)
    bin_now()
    comp = compiler.compile_score(text0, work_dir=work)
    l1 = comp.l1
    fps = float(l1["fps"])
    shots = sorted((i for i in l1["items"] if i["k"] == "shot"), key=lambda i: i["f"][0])
    snapshot, n_rows = copy.deepcopy(lines), len(rows)
    by_id = {o.get("id"): o for o in lines if o.get("k") == "b"}

    def moment_s(w):
        ref = w.get("w")
        if isinstance(ref, int) or (isinstance(ref, str) and ref.strip()):
            first = str(ref).split("-")[0].strip().lstrip("w")
            if first.isdigit() and int(first) < len(words):
                return float(words[int(first)].get("t0", words[int(first)].get("start", 0.0)) or 0.0)
        return float(w["at"]) if isinstance(w.get("at"), (int, float)) and not isinstance(w.get("at"), bool) else None

    def prog_f(t):
        for sp in l1.get("spine") or []:
            if sp["src"][0] <= t < sp["src"][1]:
                return sp["f"][0] + int(round((t - sp["src"][0]) * fps))
        return None

    todo = []
    for wi, w in enumerate(wishes):
        pool = [c for c in offers if c.get("_wish") == wi]
        if not pool:
            continue
        keys = [k for k in (name_of(pool[0].get("named")), "f:" + (f"w{w.get('w')}" if w.get("w") else str(w.get("at")))) if k in by_for]
        if not keys or any(used.get(k) for k in keys):
            continue                                   # its subject is on screen already, in a still's place
        t = moment_s(w)
        f = prog_f(t) if t is not None else None
        if f is not None:
            todo.append((f, keys[0]))
    placed, last_f, beats_done = 0, -10 ** 9, set()
    for f, key in sorted(todo):
        if placed >= INSERT_MOST or f < fps // 2 or f - last_f < INSERT_GAP_F:
            continue
        shot = next((x for x in shots if x["f"][0] <= f < x["f"][1]), None)
        m = re.match(r"^([A-Za-z][\w\-]*)\.s(\d+)", str((shot or {}).get("id") or ""))      # the Score's own ids: beat, then its picture's index
        if not m or shot.get("pic") != "main" or shot.get("view") is not None or shot.get("treat"):
            continue
        bid, k_at = m.group(1), int(m.group(2))
        o = by_id.get(bid)
        if o is None or bid in beats_done or o.get("f") is not None or f"{bid}.s" in text0:
            continue
        groups = {}
        for x in shots:
            mm = re.match(r"^([A-Za-z][\w\-]*)\.s(\d+)", str(x.get("id") or ""))
            if mm and mm.group(1) == bid:
                g = groups.setdefault(int(mm.group(2)), [x["f"][0], x["f"][1]])
                g[0], g[1] = min(g[0], x["f"][0]), max(g[1], x["f"][1])
        order = sorted(groups)
        if order != list(range(len(order))) or any(groups[a][1] != groups[b][0] for a, b in zip(order, order[1:])):
            continue
        pics = o.get("pic")
        pics = pics if isinstance(pics, list) else [pics if pics is not None else "main"]
        a, z = groups[k_at]
        before = f - a
        if before < 10:
            before = 0
        room = z - a - before
        ln = INSERT_F if room >= INSERT_F + 10 else room if room >= 20 else 0
        if not ln:
            continue
        new = clip_for(key)
        if new is None:
            continue
        cut, pic = [], []
        for k in order:
            ref, n = pics[k % len(pics)], groups[k][1] - groups[k][0]
            if k != k_at:
                cut.append(n)
                pic.append(ref)
                continue
            if before:
                cut.append(before)
                pic.append(ref)
            cut.append(ln)
            pic.append(new)
            if room - ln > 0:
                cut.append(room - ln)
                pic.append(ref)
        cut[-1] = 999                                  # the last picture takes what is left, as the Score's own lists do
        o["cut"], o["pic"] = cut, pic
        beats_done.add(bid)
        placed, last_f = placed + 1, f
    if not placed:
        return 0
    try:
        bin_now()
        comp2 = compiler.compile_score(direct.dump(lines), work_dir=work)
        ok = comp2.l1["dur_f"] == l1["dur_f"] and not compiler.validate_l1(comp2.l1)
    except Exception:  # noqa: BLE001
        ok = False
    if not ok:                                         # the film as it was: no clip is cut in, and none stays in the Bin for it
        lines[:] = snapshot
        for r in rows[n_rows:]:
            for c in offers:
                if c.get("_row") is r:
                    c["_row"] = None
        del rows[n_rows:]
        bin_now()
        return 0
    return placed


# ------------------------------------------------------------------ the film

class _Payer:
    """A paying account's first film (round six): no trial budget row and no pool. What its calls cost is the
    company's and is counted here from score.call, so the job record carries it as a trial film's does."""
    is_test = True          # nothing here reads or writes trial_budgets

    def __init__(self, user_id):
        self.user_id, self.spent_usd, self.cap_usd = user_id, 0.0, 0.0


def attempt(ctx, worker_db, job, started, spend, trial_scope, subscribed):
    cfg = flags.read(worker_db)
    payload = job.get("payload") or {}
    # a paying account's automatic First cut, on the Claude lane (first_cut.run set its model): while the owner's
    # trial is off these are the only first films a customer gets, and the last look is their never-below guard too
    payer = bool(subscribed and trial_scope is None and getattr(ctx, "first_cut_model", None))
    ok, why = flags.admit(cfg, job.get("user_id"), trial_active=trial_scope is not None, subscribed=subscribed,
                          engine_on=bool(getattr(ctx, "first_cut_q1", False)), source_s=getattr(ctx, "duration", None), url=payload.get("url"),
                          payer_first=payer)
    if not ok:
        if why not in ("off", "not_trial", "subscribed", "url"):
            ctx.first_cut_phases = dict(getattr(ctx, "first_cut_phases", None) or {}, director={"skipped": why})
        return None
    if payer:
        from . import call
        trial_scope = _Payer(job.get("user_id"))
        with call._tally_lock:
            call.TALLIES.append(trial_scope)
        try:
            return _attempt(ctx, worker_db, job, started, spend, trial_scope, cfg, payer=True)
        finally:
            with call._tally_lock:
                if trial_scope in call.TALLIES:
                    call.TALLIES.remove(trial_scope)
    return _attempt(ctx, worker_db, job, started, spend, trial_scope, cfg)


def _attempt(ctx, worker_db, job, started, spend, trial_scope, cfg, payer=False):
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
    if payer:
        rec["payer"] = True
    spent0 = trial_scope.spent_usd
    start_edl = None
    reserve = 0.0
    try:
        start_edl = (ctx.latest_edl() or {}).get("json")
        reserve = cfg["film_usd"]
        _allowance(worker_db, trial_scope, reserve)
        steps, applied, picked = _make(ctx, worker_db, job, cfg, deadline, work, rec)
        if getattr(ctx, "director_state", None):
            ctx.director_state["start_edl"] = start_edl        # what the last look puts back when the film loses to its upload
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

    foot = footage_start(job, work, sc, per) if sc is not None else None      # None unless the footage switch opens this account
    rec["at"] = "supply"
    t = time.monotonic()
    _say(ctx, worker_db, "director_supply", "Finding pictures for it")
    bed_th = None
    try:                     # the film's bed is chosen beside Supply, and its audio starts on its way into storage (score.bed)
        from . import bed
        ctx.director_bed = {"work": work}
        if bed.available():
            bed_th = bed.start(work, sc, per, index, min(deadline - 80.0, time.monotonic() + 12.0), cfg["models"]["scout"], fetch=bed.fetch_library)
    except Exception as exc:  # noqa: BLE001 - no bed, the film is written dry
        rec["bed_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    bin_doc = None
    if sc is not None:       # with no wish too: the hook picture of a talking film and a wordless upload's own details are found here
        try:
            bin_doc = supply.run(work, sc, per, _acquirer(ctx, worker_db, job, work), min(deadline - 70.0, time.monotonic() + flags.SUPPLY_S))
        except call.Refused:
            raise
        except Exception as exc:  # noqa: BLE001 - DESIGN 3.5: the upload and plates
            rec["supply_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    _stage(rec, "supply", t)

    if bed_th is not None:
        bed_th.join(max(0.0, min(3.0, deadline - 75.0 - time.monotonic())))      # long done by now; a late pick only means this film is dry
        if bed_th.is_alive():
            from . import bed
            bed.unset(work, "the pick was late")
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

    if bare(comp):
        raise Fall("direct", "nothing stood: the film is the upload again but for its colour")
    rec["at"] = "lower"
    t = time.monotonic()
    try:                     # the stretch of the chosen track, its level and its drop, on the film as compiled
        from . import bed, compiler
        got = bed.settle(work, comp, index, compiler.parse_score(text)[0], str((sc or {}).get("about") or ""))
        rec["bed"] = {k: got.get(k) for k in ("cid", "title", "kind", "gain_db", "offset_s", "turn_s", "chosen_by")} if got else None
    except Exception as exc:  # noqa: BLE001
        rec["bed_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    edl, locals_, rep = lower.lower(comp.l1, work)
    rec["lower"] = {k: rep.get(k) for k in ("counts", "burns_as_light", "burns_as_glow", "missing_pictures", "fonts_replaced", "bed", "bed_error")}
    edl = _publish(ctx, worker_db, edl, locals_, bin_doc, original["storage_key"])
    if getattr(ctx, "director_bed_fell", None):
        rec["bed_fell"], ctx.director_bed_fell = ctx.director_bed_fell, None
        rep.pop("bed", None)
    _stage(rec, "lower", t)

    rec["at"] = "write"
    result = ctx.apply_score(edl, "the Director's First cut")
    if not str(result).startswith("EDL v"):
        raise Fall("write", str(result)[:300])
    ctx.director_version = (ctx.versions_written or [None])[-1]
    rec["at"] = "written"
    rec["taste"] = usage.get("taste")
    # what the last look needs once the preview is on screen (last_look closes it)
    ctx.director_state = {"work": work, "text": text, "comp": comp, "edl": edl, "bin_doc": bin_doc, "original_key": original["storage_key"], "cfg": cfg,
                          "about": str((sc or {}).get("about") or ""),
                          "footage": foot, "acquire": _acquirer(ctx, worker_db, job, work) if foot else None}
    if flags.engine_mode(cfg, job.get("user_id")) != "off":
        _keep_scene_plan(ctx, comp.l1, work, rec)
    applied = _applied(rep, comp.l1, bin_doc)
    steps = [{"tool": "director", "ok": True, "s": round(sum(rec["stages"].values()), 1), "note": str(result)[:160]}]
    return steps, applied, {"how": "director", "edl_version": ctx.director_version}
