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

# The engines release gave Wave 4's picture verbs (depth and time, UI
# scenes, sourced B-roll) their own department: with Wave 3's world verbs
# one department passed its 12,000-byte compact budget.
DEPARTMENT = "picture"
VERBS = ("source_broll", "remove_broll")
# A cutaway whose LOOK stays this far from the hero after the world grade
# (broll_score.world_gap: mean lightness and chroma, hue aside) is not
# placed (a night aerial against a lit face). The grade keeps hues and
# moves the midtones a third of the way, so a daylight cutaway against a
# low-key interview sits around 12-18 and is a normal cut (Wave 4 review:
# the old mean-colour gate at 6 only passed pictures a full colour
# transfer had repainted).
UNMATCHABLE_DE = 25.0
# Past this gap a candidate starts losing to closer looks, other things
# equal.
LOOK_PENALTY_FROM = 12.0
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
            # overlays carry start + duration_s (no end): a cover cutaway
            # already there blocks a new one over the same seconds
            try:
                a = float(o["start"])
                b = a + float(o.get("duration_s") or 0.0)
            except (TypeError, ValueError):
                continue
            out.append((a, b))
    # spliced inserts are footage the program already cuts away to
    try:
        import timeline as tl_mod
        for blk in tl_mod.program_blocks(edl):
            if blk.get("kind") == "insert":
                out.append((float(blk["out_start"]), float(blk["out_end"])))
    except Exception:  # noqa: BLE001
        pass
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
        frames = finish_tools._frames(ctx, edl, ts)
    except Exception:  # noqa: BLE001
        return []
    # the look is what the viewer sees: the source cropped to the output
    # frame around its aim, not the whole 16:9 plate with its dark room
    try:
        import broll_fx
        _o, W, H = _frame(edl)
        fr = edl.get("frame") or {}
        aim = (float(fr.get("focus_x") or 0.5),
               float(fr.get("focus_y") or 0.5))
        return [broll_fx.crop_aspect(f, W / float(H), aim)[0]
                for f in frames]
    except Exception:  # noqa: BLE001
        return frames


def _prefetch_for(ctx, orientation, W, H, providers):
    """(the First cut's BrollPrefetch when it holds this frame, the search
    function): engine accounts search through the short-lived cache, every
    other account calls the libraries exactly as before (D1)."""
    import broll_source
    pf = getattr(ctx, "broll_prefetch", None)
    if pf is not None and not pf.matches(orientation, W, H, providers):
        print(f"[broll] prefetch frame {pf.frame} != {(orientation, W, H)}: "
              "searching as usual", flush=True)
        pf = None
    try:
        import agent_tools
        engine = bool(agent_tools.engine_on(ctx))
    except Exception:  # noqa: BLE001
        engine = False
    if engine or pf is not None:
        import first_cut_speed
        return pf, first_cut_speed.cached_search
    return pf, broll_source.search


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
    return broll_source.fetch_local(cand, work, W, H)


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


def _fetch(ctx, cand, W, H):
    """The network half of judging (parallel threads): the file, or the
    executor's acquisition with its review frames."""
    import broll_source
    try:
        return {"cand": cand, "got": _acquire(ctx, cand, W, H)}
    except broll_source.BrollError as e:
        return {"cand": cand, "error": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"cand": cand, "error": f"{type(e).__name__}: {str(e)[:120]}"}


def _judge(ctx, fetched, W, H, hero, need_s, archival):
    """The decoding and scoring half — always in the calling thread."""
    import broll_score
    import broll_source
    cand = fetched["cand"]
    if fetched.get("error"):
        return fetched
    got = fetched["got"]
    if got.get("path") and "frames" not in got:
        try:
            got = broll_source.analyse_local(got)
        except Exception as e:  # noqa: BLE001
            return {"cand": cand, "error": f"unreadable ({str(e)[:100]})"}
    still = cand.get("kind") == "photo"
    meta = {"kind": "still" if still else "video",
            "width": got.get("width"), "height": got.get("height"),
            "fps": got.get("fps"), "duration_s": got.get("duration_s"),
            "bytes": got.get("bytes"),
            "motion_profile": got.get("motion_profile")}
    fit = "frame" if (archival or cand.get("archival")) else "cover"
    res = broll_score.score(got.get("frames") or [], meta,
                            hero=hero or None, W=W, H=H, fit=fit,
                            archival=fit == "frame",
                            native=bool(got.get("native")))
    rel = float(cand.get("_rel") or 0.0)
    res["final"] = 0.55 * res["score"] + 0.45 * rel
    # a picture the match cannot bring into the camera's world (a night
    # aerial against a lit face) loses to one it can, other things equal
    dm = (res.get("measures") or {}).get("de_matched")
    if dm is not None and dm > LOOK_PENALTY_FROM:
        res["final"] -= min(0.2, 0.02 * (dm - LOOK_PENALTY_FROM))
    res["final"] = round(res["final"], 4)
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
        import depth_fx
        for r, row in enumerate(rows):
            y = 8 + r * (th + lab_h + 8)
            frame = row["frame"]
            if frame is not None:
                # tile size first: a 6000-px archive photo through the LUT
                # at full size is gigabytes of float math for a thumbnail
                frame = depth_fx.cover_crop(np.asarray(frame), tw, th)
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

    # Q8 (30 Oct): a First cut started these searches and the short lists'
    # downloads when its scout answered (first_cut_speed.BrollPrefetch); an
    # engine account's searches answer from a short-lived cache. Anything
    # the prefetch does not hold (another frame, providers, archival) runs
    # exactly as before.
    pf, search_fn = _prefetch_for(ctx, orientation, W, H, providers)

    # 1. search every moment's routes in parallel
    def find(spec):
        kinds = {"video": ["video"], "photo": ["photo"],
                 "any": ["video", "photo"]}[spec["kind"]]
        cands, errs = [], []
        for q in spec["queries"]:
            for k in kinds:
                hit = pf.search(q, k) if (pf is not None
                                          and not spec["archival"]) else None
                if hit is not None:
                    c, e = hit
                else:
                    c, e, _lanes = search_fn(
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
        # fetch in parallel (network); decode and judge here, one at a
        # time (numpy's BLAS must not be driven from several threads)
        with ThreadPoolExecutor(max_workers=min(3, len(short))) as pool:
            fetched = list(pool.map(
                lambda c: (pf.fetched(c) if pf is not None else None)
                or _fetch(ctx, c, W, H), short))
        judged = [_judge(ctx, f, W, H, hero, spec["duration_s"],
                         spec["archival"]) for f in fetched]
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
        dm = (best["res"].get("measures") or {}).get("de_matched")
        if dm is not None and dm > UNMATCHABLE_DE:
            # better no cutaway than one that breaks the world: the match
            # cannot bring it within reach of the camera's look
            misses.append((spec, f"nothing found sits in the camera's "
                                 f"world (the best stays dE {dm:.0f} away "
                                 "after the match grade)"))
            continue
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

    # the board is a picture for an agent's eyes; a First cut has none
    board = _board(ctx, rows, W, H) if rows and pf is None else None
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
