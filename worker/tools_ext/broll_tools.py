"""Department ``world``: track 4C's verbs (INTERFACES_W4.md §4).

  source_broll   WRITE: the B-roll director. For each story moment (a
                 query, the word or second it lands on, what it proves) it
                 searches every live library in parallel (Pexels, Pixabay,
                 Openverse, Wikimedia Commons, NASA, the Internet Archive
                 for archival), drops what the licence forbids, downloads a
                 short list, judges each candidate like a cinematographer
                 (broll_score: resolution after the vertical crop, whether
                 the subject survives it, motion, depth of field, range,
                 palette distance to the hero frame, watermarks, starved
                 encodes, posed eyes-to-camera stock) and cuts the winner
                 in as a ``broll`` fx item: matched to the hero's colour
                 (a .cube), grain and softness matched, photos moving in
                 2.5D, the licence ledger line on the item. ``place=False``
                 researches only. With no library live it says so and hands
                 back a shoot list.
  remove_broll   WRITE: remove cutaways by id.

``research_broll``, ``search_stock`` and ``add_stock_media`` stay as the
manual lane; this verb uses their provider lanes (stock.py) and their
acquisition job (``stock_acquire`` on the redesign policy) internally.
Nothing here adds music, a sound or a zoom.
"""

import json
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor

DEPARTMENT = "world"
VERBS = ("source_broll", "remove_broll")
MAX_MOMENTS = 8
SHORTLIST = 3
DEFAULT_SPAN = 2.4
MIN_SPAN, MAX_SPAN = 1.0, 6.0
HOOK_S = 0.8                 # the face establishes first: no cutaway before
MOVES = ("push_in", "drift_left", "rise", "drift_right", "pull_out",
         "orbit_left", "fall", "orbit_right")


def _rejected(msg):
    return f"REJECTED: {msg}"


def _edl(ctx):
    row = ctx.latest_edl()
    return row["json"], row["version"]


def _program_s(edl):
    import schemas
    try:
        return float(schemas.program_duration(edl) or 0.0)
    except Exception:  # noqa: BLE001
        return 0.0


def _frame(edl):
    ratio = ((edl.get("frame") or {}).get("ratio")
             or (edl.get("canvas") or {}).get("ratio") or "")
    return {"9:16": ("portrait", 1080, 1920), "1:1": ("square", 1080, 1080),
            "4:5": ("portrait", 1080, 1350), "4:3": ("landscape", 1440,
                                                      1080)}.get(
        ratio, ("landscape", 1920, 1080))


def _obj(v, name):
    if v is None or isinstance(v, (dict, list)):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            pass
    raise ValueError(f"{name} must be JSON")


def _new_id(edl, prefix, taken=()):
    used = {str(i.get("id")) for i in edl.get("fx") or []
            if isinstance(i, dict)} | set(taken)
    n = 1
    while f"{prefix}{n}" in used:
        n += 1
    return f"{prefix}{n}"


def _unavailable():
    try:
        import remote
        if remote.executor_supports("fx:broll") is False:
            return ("UNAVAILABLE: the render service does not draw cutaways "
                    "yet (a deploy is rolling out); nothing was written.")
    except Exception:  # noqa: BLE001
        pass
    return ""


def _windows_taken(edl):
    out = []
    for it in edl.get("fx") or []:
        if isinstance(it, dict) and it.get("type") in ("broll", "depth") \
                and it.get("stage") == "screen" and it.get("start") is not \
                None:
            out.append((float(it["start"]), float(it["end"])))
    for o in edl.get("overlays") or []:
        if isinstance(o, dict) and (o.get("fit") == "cover" or o.get(
                "full_frame")) and o.get("start") is not None:
            out.append((float(o["start"]), float(o.get("end") or 0)))
    return out


def _find_word(edl, index, word, near=None):
    """(program onset, source index, text) of a spoken word or phrase in
    the kept transcript (numbers are words here, never an index), nearest
    ``near`` when it repeats; None when absent."""
    import device_planner
    words = device_planner.program_words(edl, index or {})
    toks = re.findall(r"[a-z0-9']+", str(word).lower())
    if not toks:
        return None
    hits = []
    for j, w in enumerate(words):
        run = [re.sub(r"[^a-z0-9']", "", str(x["w"] or "").lower())
               for x in words[j:j + len(toks)]]
        if run == toks:
            hits.append(w)
    if not hits:
        return None
    if near is not None:
        try:
            hits.sort(key=lambda w: abs(w["t0"] - float(near)))
        except (TypeError, ValueError):
            pass
    return hits[0]["t0"], hits[0]["i"], hits[0]["w"]


def _moment_window(ctx, edl, m, prog, taken):
    """(start, end, bind) for one moment, or raise ValueError."""
    dur = m.get("duration_s")
    try:
        dur = float(dur) if dur is not None else DEFAULT_SPAN
    except (TypeError, ValueError):
        raise ValueError("duration_s must be seconds")
    dur = max(MIN_SPAN, min(MAX_SPAN, dur))
    bind = None
    if m.get("word") not in (None, ""):
        got = _find_word(edl, getattr(ctx, "index", None) or {}, m["word"],
                         m.get("at"))
        if not got:
            raise ValueError(f"the word '{m['word']}' is not in the kept "
                             "transcript")
        start = float(got[0])
        bind = {"on": "word", "ref": int(got[1]), "rhythm": "speech"}
    elif m.get("at") is not None:
        try:
            start = float(m["at"])
        except (TypeError, ValueError):
            raise ValueError("at must be program seconds")
    else:
        raise ValueError("name the moment by word or at")
    start = max(HOOK_S, start)
    end = min(prog, start + dur)
    if end - start < MIN_SPAN:
        raise ValueError(f"no room for a cutaway at {start:g}s (the program "
                         f"ends at {prog:g}s)")
    for a, b in taken:
        if start < b - 0.01 and end > a + 0.01:
            raise ValueError(f"{start:g}-{end:g}s overlaps a cutaway already "
                             f"at {a:g}-{b:g}s")
    return round(start, 2), round(end, 2), bind


def _hero(ctx, edl, start, end):
    """RGB frames of the camera around the moment (the look to match)."""
    try:
        from tools_ext import finish_tools
        prog = _program_s(edl)
        ts = [max(0.0, start - 0.4), min(prog - 0.05, end + 0.4)]
        return finish_tools._frames(ctx, edl, ts)
    except Exception:  # noqa: BLE001
        return []


def _dedupe(cands):
    seen, out = set(), []
    for c in cands:
        k = re.sub(r"[^a-z0-9]", "", str(c.get("title") or c["id"]).lower())
        if k in seen:
            continue
        seen.add(k)
        out.append(c)
    return out


def _interleave(cands, n):
    """Top ``n`` by metadata rank, at most two from one library (one
    library's house style never fills the board)."""
    out, per = [], {}
    for c in cands:
        p = c.get("provider")
        if per.get(p, 0) >= 2:
            continue
        per[p] = per.get(p, 0) + 1
        out.append(c)
        if len(out) >= n:
            break
    return out


def _acquire(ctx, cand, W, H):
    """{path?, storage_key?, sha256?, frames, native, ...} for a candidate:
    the executor's acquisition job on the redesign policy, else a local
    download (single-box / dev lane)."""
    import broll_source
    import config
    policy = config.execution_policy_for(getattr(ctx, "job", None) or {})
    if cand.get("provider") == "nasa":
        broll_source.nasa_renditions(cand)
    elif cand.get("provider") == "archive":
        broll_source.archive_renditions(cand)
    if cand.get("kind") == "video" and not cand.get("_files") and not \
            cand.get("_local_path"):
        raise broll_source.BrollError("no downloadable rendition")
    if cand.get("kind") == "photo" and not cand.get("_url") and not \
            cand.get("_local_path"):
        raise broll_source.BrollError("no downloadable image")
    if policy == "redesign":
        return _acquire_remote(ctx, cand, W, H)
    work = os.path.join(ctx.workdir, f"broll_{uuid.uuid4().hex[:8]}")
    return broll_source.acquire_local(cand, work, W, H)


def _acquire_remote(ctx, cand, W, H):
    import numpy as np
    from PIL import Image
    import broll_source
    import remote
    import storage
    if not remote.stock_acquire_available():
        raise broll_source.BrollError("no media executor for acquisition")
    item = {k: v for k, v in cand.items() if not k.startswith("_")
            or k in ("_url", "_files", "_thumb")}
    item["kind"] = "video" if cand.get("kind") == "video" else "photo"
    got = remote.run_stock_acquire_remote(
        ctx.project_id, {"item": item, "want_w": W, "want_h": H,
                         "review": True,
                         "execution_policy": "redesign"},
        user_id=(getattr(ctx, "job", None) or {}).get("user_id"))
    if not isinstance(got, dict) or not got.get("ok"):
        raise broll_source.BrollError(str((got or {}).get("error")
                                          or "acquisition failed")[:160])
    frames = []
    keys = [k for k in got.get("review_keys") or [] if k]
    try:
        for k in keys:
            local = os.path.join(ctx.workdir,
                                 f"brr_{uuid.uuid4().hex[:8]}.jpg")
            try:
                storage.download_to(k, local)
                frames.append(np.asarray(Image.open(local).convert("RGB")))
            except Exception:  # noqa: BLE001
                continue
    finally:
        try:
            storage.delete_keys(keys)
        except Exception:  # noqa: BLE001
            pass
    return {"storage_key": got["storage_key"], "sha256": got.get("sha256"),
            "bytes": got.get("bytes"), "kind": cand.get("kind"),
            "width": got.get("width"), "height": got.get("height"),
            "fps": got.get("fps"), "duration_s": got.get("duration_s"),
            "frames": frames, "native": False,
            "motion_profile": got.get("motion_profile")}


def _offset(got, need_s):
    """Where in a clip the cutaway starts: around its best-looking sample
    (the review frames sit at 10/37/63/90%), leaving the window inside."""
    import broll_score
    dur = float(got.get("duration_s") or 0.0)
    fr = got.get("frames") or []
    if dur <= need_s + 0.2 or not fr:
        return 0.0, dur < need_s
    q = []
    for f in fr:
        g = broll_score._gray(broll_score._small(f))
        dr = broll_score.dyn_range(g)[0]
        q.append(dr + 0.0002 * min(4000.0, broll_score.sharpness(g)))
    k = int(max(range(len(q)), key=lambda i: q[i]))
    t = dur * (0.1, 0.37, 0.63, 0.9)[k] if len(fr) == 4 else dur * 0.5
    off = max(0.0, min(dur - need_s - 0.1, t - need_s * 0.35))
    return round(off, 2), False


def _judge(ctx, cand, W, H, hero, need_s, archival):
    import broll_score
    import broll_source
    try:
        got = _acquire(ctx, cand, W, H)
    except broll_source.BrollError as e:
        return {"cand": cand, "error": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"cand": cand, "error": f"{type(e).__name__}: {str(e)[:120]}"}
    still = cand.get("kind") == "photo"
    meta = {"kind": "still" if still else "video",
            "width": got.get("width"), "height": got.get("height"),
            "fps": got.get("fps"), "duration_s": got.get("duration_s"),
            "bytes": got.get("bytes"),
            "motion_profile": got.get("motion_profile")}
    fit = "frame" if (archival or cand.get("archival")) else "cover"
    res = broll_score.score(got.get("frames") or [], meta, hero=hero or None,
                            W=W, H=H, fit=fit, archival=fit == "frame",
                            native=bool(got.get("native")))
    rel = float(cand.get("_rel") or 0.0)
    res["final"] = round(0.55 * res["score"] + 0.45 * rel, 4)
    return {"cand": cand, "got": got, "res": res, "fit": fit}


def _register(ctx, cand, got, moment):
    """Put the chosen file in the project (asset row + storage); returns
    the asset dict {storage_key, sha256, width, height, kind}."""
    import hashlib
    import db as dbx
    import edit_batch
    import storage
    import url_media
    still = cand.get("kind") == "photo"
    kind = url_media.KIND_IMAGE if still else url_media.KIND_VIDEO
    key, sha = got.get("storage_key"), got.get("sha256")
    if not key:
        path = got["path"]
        with open(path, "rb") as f:
            sha = hashlib.sha256(f.read()).hexdigest()
        ext = os.path.splitext(path)[1].lower() or (".jpg" if still
                                                     else ".mp4")
        key = f"fetched/{ctx.project_id}/broll/{sha[:32]}{ext}"
        storage.upload_file(path, key, url_media.content_type(path))
    keys = ctx.db.run(edit_batch.project_media_keys, ctx.project_id) or {}
    if key not in keys:
        import broll_source
        cr = broll_source.credit(cand)
        ctx.db.run(dbx.insert_asset, ctx.project_id, kind, key,
                   bytes_=got.get("bytes"),
                   duration_s=None if still else got.get("duration_s"),
                   width=got.get("width"), height=got.get("height"),
                   fps=None if still else got.get("fps"), sha256=sha,
                   meta={"filename": (str(cand.get("title") or "b-roll")
                                      [:60] + (".jpg" if still else ".mp4")),
                         "stock": True, "broll": True,
                         "provider": cand.get("provider"),
                         "stock_id": cand.get("id"),
                         "credit": cr.get("author"),
                         "license": cr.get("license"),
                         "license_url": cr.get("license_url"),
                         "page_url": cr.get("page_url"),
                         "source_url": got.get("source_url")
                         or cand.get("source_url"),
                         "description": cand.get("description"),
                         "motion_profile": got.get("motion_profile"),
                         "broll_moment": moment})
    return {"storage_key": key, "sha256": sha, "width": got.get("width"),
            "height": got.get("height"), "kind": kind}


def _board(ctx, rows, W, H):
    """hero | chosen (as fetched) | chosen after the match grade, per
    moment; returns a JPEG path or None."""
    try:
        import numpy as np
        from PIL import Image, ImageDraw
        import lutlib
        tw = 220
        th = int(tw * H / float(W))
        if th > 400:
            th, tw = 400, int(400 * W / float(H))
        lab_h = 34
        cols = 3
        sheet = Image.new("RGB", (cols * (tw + 8) + 8,
                                  len(rows) * (th + lab_h + 8) + 8),
                          (18, 18, 20))
        d = ImageDraw.Draw(sheet)

        def fit(img):
            import depth_fx
            return Image.fromarray(depth_fx.cover_crop(
                np.asarray(img), tw, th))
        for r, row in enumerate(rows):
            y = 8 + r * (th + lab_h + 8)
            frame = row["frame"]
            after = frame
            if row.get("lut") is not None:
                after = (np.clip(lutlib.apply(row["lut"], frame / 255.0),
                                 0, 1) * 255).astype(np.uint8)
            tiles = [row.get("hero"), frame, after]
            labels = ["hero", f"{row['provider']} ({row['license']})"[:34],
                      f"matched dE {row.get('de_after')}"]
            for c, (img, lab) in enumerate(zip(tiles, labels)):
                x = 8 + c * (tw + 8)
                if img is not None:
                    sheet.paste(fit(img), (x, y))
                d.text((x, y + th + 3), lab, fill=(230, 230, 230))
                if c == 0:
                    d.text((x, y + th + 17), row["moment"][:34],
                           fill=(233, 180, 76))
        out = os.path.join(ctx.workdir,
                           f"broll_board_{uuid.uuid4().hex[:6]}.jpg")
        sheet.save(out, quality=88)
        return out
    except Exception:  # noqa: BLE001
        return None


def source_broll(ctx, moments=None, max=None, providers=None, place=True):
    # ``max`` is the tool's argument name; the work happens where the
    # builtin is not shadowed
    return _source_broll(ctx, moments, max, providers, place)


def _source_broll(ctx, moments, limit, providers, place):
    import broll_source
    import lutlib
    edl, version = _edl(ctx)
    try:
        moments = _obj(moments, "moments")
        providers = _obj(providers, "providers")
    except ValueError as e:
        return _rejected(str(e))
    if not isinstance(moments, list) or not moments:
        return _rejected("name the story moments: moments=[{query, word or "
                         "at, purpose?, duration_s?, kind?, archival?}] — "
                         "one concrete visual per moment (what the words "
                         "PROVE, not their topic)")
    if len(moments) > MAX_MOMENTS:
        return _rejected(f"at most {MAX_MOMENTS} moments per call")
    cap = len(moments)
    if limit is not None:
        try:
            cap = int(limit)
        except (TypeError, ValueError):
            return _rejected("max must be a count")
    if providers is not None and (not isinstance(providers, list) or any(
            p not in broll_source.PROVIDERS for p in providers)):
        return _rejected("providers is a list of: "
                         + ", ".join(broll_source.PROVIDERS))
    gone = _unavailable() if place else ""
    if gone:
        return gone
    orientation, W, H = _frame(edl)
    prog = _program_s(edl)
    specs, errors, taken = [], [], list(_windows_taken(edl))
    for i, m in enumerate(moments, 1):
        if not isinstance(m, dict) or not str(m.get("query") or "").strip():
            errors.append(f"moment {i}: needs a concrete query")
            continue
        mid = str(m.get("id") or f"m{i}")[:24]
        try:
            s, e, bind = _moment_window(ctx, edl, m, prog, taken)
        except ValueError as x:
            errors.append(f"{mid}: {x}")
            continue
        taken.append((s, e))
        kind = str(m.get("kind") or "any").lower()
        if kind not in ("video", "photo", "any"):
            errors.append(f"{mid}: kind is video, photo or any")
            continue
        qs = [str(m["query"]).strip()[:120]] + [
            str(q).strip()[:120] for q in (m.get("query_variants") or [])
            if str(q).strip()][:2]
        specs.append({"id": mid, "queries": qs, "kind": kind,
                      "purpose": str(m.get("purpose") or "")[:160],
                      "archival": bool(m.get("archival")), "start": s,
                      "end": e, "bind": bind,
                      "query": qs[0], "duration_s": e - s})
    if not specs:
        return _rejected("no usable moment. " + "; ".join(errors))

    st = broll_source.status()
    live = [p for p, v in st.items() if v == "on" and (
        providers is None or p in providers)]
    if not live:
        offs = "; ".join(f"{p} {v[5:]}" for p, v in st.items()
                         if v != "on")
        return ("B-ROLL OFF: no footage library is live on this deployment "
                f"({offs}). Nothing was searched or written. SHOOT LIST — "
                "film these and upload them, then place them with "
                "add_overlay or source_broll:\n"
                + "\n".join("  " + x for x in broll_source.shoot_list(
                    specs, orientation)))

    # 1. search every moment's routes in parallel
    def find(spec):
        kinds = {"video": ["video"], "photo": ["photo"],
                 "any": ["video", "photo"]}[spec["kind"]]
        cands, errs = [], []
        for q in spec["queries"]:
            for k in kinds:
                c, e, _lanes = broll_source.search(
                    q, k, orientation, count=4, providers=providers,
                    archival=spec["archival"])
                cands += c
                errs += e
        ranked = broll_source.prerank(_dedupe(cands), spec["queries"], W, H,
                                      need_s=spec["duration_s"],
                                      archival=spec["archival"])
        return spec, ranked, errs
    with ThreadPoolExecutor(max_workers=min(4, len(specs))) as pool:
        found = list(pool.map(find, specs))

    # 2. acquire + judge a short list per moment
    used = {str(((i.get("credit") or {}).get("page_url")) or "")
            for i in edl.get("fx") or [] if isinstance(i, dict)
            and i.get("type") == "broll"}
    plans, misses, rejected_n = [], [], {}
    for spec, ranked, errs in found:
        hero = _hero(ctx, edl, spec["start"], spec["end"])
        short = _interleave([c for c in ranked
                             if c.get("page_url") not in used], SHORTLIST)
        if not short:
            misses.append((spec, "no licensed match" + (
                f" ({'; '.join(errs)[:120]})" if errs else "")))
            continue
        with ThreadPoolExecutor(max_workers=min(3, len(short))) as pool:
            judged = list(pool.map(
                lambda c: _judge(ctx, c, W, H, hero, spec["duration_s"],
                                 spec["archival"]), short))
        debug = getattr(ctx, "_broll_debug", None)
        if isinstance(debug, list):
            for j in judged:
                debug.append({
                    "moment": spec["id"], "id": j["cand"].get("id"),
                    "provider": j["cand"].get("provider"),
                    "title": j["cand"].get("title"),
                    "rel": j["cand"].get("_rel"), "error": j.get("error"),
                    "reject": (j.get("res") or {}).get("reject"),
                    "score": (j.get("res") or {}).get("score"),
                    "measures": {k: v for k, v in ((j.get("res") or {})
                                 .get("measures") or {}).items()
                                 if k != "parts"}})
        ok = []
        for j in judged:
            if j.get("error"):
                rejected_n["unavailable"] = rejected_n.get("unavailable",
                                                           0) + 1
                continue
            if j["res"]["reject"]:
                for r in j["res"]["reject"]:
                    key = r.split(" (")[0]
                    rejected_n[key] = rejected_n.get(key, 0) + 1
                continue
            ok.append(j)
        if not ok:
            misses.append((spec, "every candidate failed the cinematic "
                                 "check"))
            continue
        best = max(ok, key=lambda j: j["res"]["final"])
        used.add(best["cand"].get("page_url"))
        plans.append((spec, best, hero))
    plans = plans[:max(0, cap)]

    # 3. place
    lines, ops, ids, rows = [], [], [], []
    for n, (spec, best, hero) in enumerate(plans):
        cand, got, res = best["cand"], best["got"], best["res"]
        still = cand.get("kind") == "photo"
        m = res["measures"]
        cr = broll_source.credit(dict(cand, source_url=got.get(
            "source_url") or cand.get("source_url")))
        note = (f"{spec['id']} {spec['start']:g}-{spec['end']:g}s — "
                f"{cand['provider']} {'photo' if still else 'clip'} "
                f"'{str(cand.get('title') or '')[:60]}' ({cr['license']}"
                + (f", by {cr['author'][:40]}" if cr.get("author") else "")
                + f"); score {res['score']:.2f}, on-topic "
                f"{float(cand.get('_rel') or 0):.2f}, crop "
                f"{m.get('crop_survival')}, dE to hero {m.get('de_hero')}"
                f" -> {m.get('de_matched')}")
        lines.append(note)
        rows.append({"moment": spec["query"], "frame": (got.get("frames")
                                                        or [None])[0],
                     "hero": (hero or [None])[0], "lut": res.get("lut"),
                     "provider": cand["provider"], "license": cr["license"],
                     "de_after": m.get("de_matched")})
        if not place:
            continue
        try:
            asset = _register(ctx, cand, got, {
                "query": spec["query"], "purpose": spec["purpose"],
                "at": spec["start"], "duration_s": spec["duration_s"]})
        except Exception as e:  # noqa: BLE001
            lines[-1] += f" — NOT PLACED (could not store it: {str(e)[:80]})"
            continue
        match = None
        if res.get("lut") is not None and (m.get("de_hero") or 0) > 2.5:
            try:
                from tools_ext import finish_tools
                text = lutlib.cube_text(res["lut"], title=f"broll match "
                                        f"{spec['id']}")
                lk = finish_tools._store_lut(ctx, text, "broll_match", {
                    "asset": asset["storage_key"]})
                match = {"asset_key": lk, "strength": 1.0,
                         "hero_t": round(max(0.0, spec["start"] - 0.4), 2),
                         "de_before": m.get("de_hero"),
                         "de_after": m.get("de_matched")}
            except Exception:  # noqa: BLE001
                match = None
        off, loop = (0.0, False) if still else _offset(
            got, spec["duration_s"])
        fid = _new_id(edl, "br", ids)
        ids.append(fid)
        fit = best["fit"]
        item = {"id": fid, "type": "broll", "stage": "screen",
                "start": spec["start"], "end": spec["end"],
                "source": {"kind": "asset",
                           "asset_key": asset["storage_key"],
                           "offset_s": off, "loop": bool(loop)},
                "media": "still" if still else "video", "fit": fit,
                "focus_x": res["focus_x"], "focus_y": res["focus_y"],
                "credit": cr, "moment": (spec["purpose"] or spec["query"])
                [:160],
                "device": ("broll_archival" if fit == "frame" else
                           "broll_still" if still else "broll_cutaway")}
        if still:
            item["move"] = {"path": MOVES[(n + len(edl.get("fx") or []))
                                          % len(MOVES)],
                            "amount": 0.3, "ease": "in_out"}
        if match:
            item["match"] = match
        if res.get("grain"):
            item["grain"] = res["grain"]
        elif fit == "frame":
            item["grain"] = 0.35
        if res.get("soften"):
            item["soften"] = res["soften"]
        if spec.get("bind"):
            item["bind"] = spec["bind"]
        from tools_ext import _write
        ops.append(_write.upsert_fx({k: v for k, v in item.items()
                                     if v is not None}))
        if still:
            try:
                from tools_ext import depth_tools
                depth_tools._ensure_depth(ctx, edl, [], asset=asset)
            except Exception:  # noqa: BLE001
                pass

    board = _board(ctx, rows, W, H) if rows else None
    if board and isinstance(getattr(ctx, "pending_images", None), list):
        ctx.pending_images.append((
            "B-ROLL BOARD — hero | as fetched | after the match grade",
            board))
    tail = []
    if rejected_n:
        tail.append("rejected on sight: " + ", ".join(
            f"{v}x {k}" for k, v in sorted(rejected_n.items())))
    offs = [p for p, v in st.items() if v != "on"]
    if offs:
        tail.append("libraries off here: " + ", ".join(
            f"{p} ({st[p][5:]})" for p in offs))
    if misses:
        tail.append("NOT FOUND — SHOOT LIST:\n" + "\n".join(
            "  " + x + f" [{why}]" for (spec, why), x in zip(
                misses, broll_source.shoot_list([s for s, _ in misses],
                                                orientation))))
    if errors:
        tail.append("skipped: " + "; ".join(errors)[:300])
    body = "\n".join("  " + x for x in lines) or "  (nothing chosen)"
    if not place or not ops:
        return ("B-ROLL RESEARCH (nothing written):\n" + body
                + ("\n" + "\n".join(tail) if tail else ""))
    from tools_ext import _write
    res = _write.commit(ctx, ops, f"source_broll: {len(ops)} cutaway(s)")
    if str(res).startswith("REJECTED"):
        return res
    import broll_fx
    led = broll_fx.ledger(ctx.latest_edl()["json"])
    return (f"{res}\nCUTAWAYS (captions and speech continue over them; "
            "photos move in 2.5D once their depth is measured, Ken Burns "
            "until then):\n" + body
            + "\nLICENCE LEDGER (credits for the description):\n"
            + "\n".join(f"  {r['id']}: {r['line']}" for r in led)
            + ("\n" + "\n".join(tail) if tail else ""))


def remove_broll(ctx, ids):
    edl, _v = _edl(ctx)
    if isinstance(ids, str):
        ids = [x.strip() for x in ids.split(",") if x.strip()]
    if not isinstance(ids, list) or not ids:
        return _rejected("ids is a list of cutaway ids")
    have = {str(i.get("id")) for i in edl.get("fx") or []
            if isinstance(i, dict) and i.get("type") == "broll"}
    missing = [i for i in ids if str(i) not in have]
    if missing:
        return _rejected("no cutaway with id " + ", ".join(map(str, missing))
                         + (f" (cutaways: {', '.join(sorted(have))})"
                            if have else " (this edit has none)"))
    from tools_ext import _write
    ops = [{"action": "remove", "layer": "fx", "id": str(i)} for i in ids]
    return _write.commit(ctx, ops, f"removed cutaway(s) {', '.join(ids)}")


_MOMENT = {"type": "object", "properties": {
    "id": {"type": "string"},
    "query": {"type": "string", "description": "what the shot SHOWS"},
    "query_variants": {"type": "array", "items": {"type": "string"}},
    "purpose": {"type": "string"},
    "word": {"type": "string", "description": "cut in on this word"},
    "at": {"type": "number"},
    "duration_s": {"type": "number"},
    "kind": {"type": "string", "enum": ["video", "photo", "any"]},
    "archival": {"type": "boolean"}}, "required": ["query"]}

TOOL_SPECS = {
    "source_broll": (
        source_broll,
        "WRITE: the B-roll director. moments=[{query (what the shot SHOWS), "
        "word or at, purpose, duration_s, kind video|photo|any, archival}] "
        "-> searches Pexels, Pixabay, Openverse, Wikimedia Commons, NASA "
        "(and the Internet Archive when archival) in parallel, keeps only "
        "licences that allow commercial use and edits, judges a short list "
        "on the real files (vertical-crop survival, resolution, motion, "
        "depth of field, range, colour distance to your hero frame, "
        "watermarks, starved encodes, posed stock) and cuts the winner in "
        "as a broll fx item: graded to match the camera, grain matched, "
        "photos moving in 2.5D, its licence line on the item. Speech and "
        "captions continue over it. place=false researches only. No "
        "library live: says so and returns a shoot list.",
        {"moments": {"type": "array", "items": _MOMENT},
         "max": {"type": "integer"},
         "providers": {"type": "array", "items": {"type": "string"}},
         "place": {"type": "boolean"}}),
    "remove_broll": (
        remove_broll,
        "WRITE: remove cutaways (broll fx items) by id; the exact previous "
        "picture returns.",
        {"ids": {"type": "array", "items": {"type": "string"}}}),
}
REQUIRED_ARGS = {"source_broll": ["moments"], "remove_broll": ["ids"]}
WRITE_TOOLS = {"source_broll", "remove_broll"}
COMPACT = {
    "source_broll": "WRITE: B-roll director: moments [{query, word|at, "
                    "purpose}] -> licensed, judged, colour-matched "
                    "cutaways (photos move); place=false researches.",
    "remove_broll": "WRITE: remove cutaways by id.",
}
