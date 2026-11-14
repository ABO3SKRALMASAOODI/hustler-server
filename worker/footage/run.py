"""find(): a script's lines in, clips of real footage per line out, with their credits.

    PYTHONPATH=worker python -m footage.run --script script.json --out <dir> [--w 1080 --h 1920]

script.json: {"title", "segments": [{"say", "pictures", "dur"?}], "named": [{"name", "is", "must_show"}]}
(the WF3 writer's script shape). Writes <out>/footage.json, <out>/clips/*.mp4 and <out>/sheets/*.jpg
(the frames the check saw). Every fetched byte is in a work folder that is deleted at the end.
"""
import argparse
import concurrent.futures as cf
import json
import math
import os
import shutil
import tempfile
import threading
import time

from . import cut, fetch, hear, look, motion, plan, search, shots, voice

MAX_SOURCES = 16
WHOLE_UNDER_S = 600.0      # a caller that reads only a source's head still fetches a source this short whole (no section cut)
HEAR_MAX_S = 480.0         # a source's first 8 minutes are heard; its shots are read to PROXY_MAX_S
PER_SUBJECT = 3
WORDS_PER_S = 2.8
SHOT_S = 1.5               # the cut's pace: a new shot about every 1.5 s. The references' real-video shots run a median
                           # 0.63 s (p90 2.1 s; lanes/footage/study): the Director trims, the supply gives 1-3 s to trim from


def _line_dur(ln):
    if ln.get("dur"):
        return float(ln["dur"])
    return max(1.2, len(str(ln.get("say") or "").split()) / WORDS_PER_S + float(ln.get("pause_after") or 0.2))


def _open(src, work, log, hear_s=None, read_s=None):
    """Proxy, shots and transcript of one source; the error on the source when it could not be read. hear_s: seconds
    heard (0: none, the shots alone). read_s: seconds of the source read."""
    d = os.path.join(work, "src", src["key"])
    t0 = time.monotonic()
    hear_s = HEAR_MAX_S if hear_s is None else float(hear_s)
    read_max = float(read_s) if read_s else fetch.PROXY_MAX_S
    try:
        # a short source is fetched whole even when only its head is read: yt-dlp cuts a section with ffmpeg on the
        # resolved stream, which YouTube refuses from a proxy route more often than not (the box, 14 Nov: 2 of 8)
        whole = bool(read_s) and src.get("duration") and float(src["duration"]) <= WHOLE_UNDER_S
        p, rung = fetch.proxy(src["url"], d, src.get("duration"), max_s=WHOLE_UNDER_S if whole else read_max)
        info = shots.probe(p)
        upto = min(info["dur"], read_max)
        src.update(proxy=p, rung=rung, dur_read=round(upto, 1), w=info["w"], h=info["h"])
        src["shots"] = shots.cuts(p, upto)
        if info.get("audio") and hear_s > 0:
            src["transcript"], src["hear_usd"] = hear.transcribe(p, min(upto, hear_s))
        else:
            src["transcript"], src["hear_usd"] = [], 0.0
        log(f"source {src['key']} {src['title'][:50]!r}: {len(src['shots'])} shots, {len(src['transcript'])} pieces heard, "
            f"{upto:.0f} s read, {time.monotonic() - t0:.0f} s")
    except Exception as e:  # noqa: BLE001 the source is out; the others carry its subject
        src["error"] = str(e)[:240]
        log(f"source {src['key']} failed: {src['error'][:160]}")
    return src


def person_anchors(sub, work, most=3, searches=None):
    """([photo paths], usd): Commons photographs of the person that broll_source's check passed. searches:
    the writer's own Commons searches for that name when it gave them (a bare "Fred Smith" finds a bassist)."""
    try:
        import broll_source
        import net_fetch
        subject = {"name": sub["name"], "is": "person", "searches": list(searches or [])[:3] + [sub["name"]], "must_show": sub["must_show"]}
        cands, _ = broll_source.search_named(subject, broll_source.KIND_PHOTO, 8)
        kept, _, usd = broll_source.check_named(subject, cands, workdir=work, budget_s=60)
    except Exception:  # noqa: BLE001 no anchor: identity falls back to the faces that recur across sources
        return [], 0.0
    out = []
    os.makedirs(os.path.join(work, "anchors"), exist_ok=True)
    for k, c in enumerate(kept[:most]):
        p = os.path.join(work, "anchors", f"{_key(sub['name'])}_{k}.jpg")
        try:
            net_fetch.download(broll_source._sized_url(c) or c.get("_thumb"), p, max_bytes=16 << 20, timeout_s=20)
            out.append(p)
        except Exception:  # noqa: BLE001
            continue
    return out, float(usd or 0.0)


def _key(s):
    return "".join(ch if ch.isalnum() else "_" for ch in str(s))[:32]


def _same(a, b):
    """One film uploaded twice (a YouTube copy of an archive.org reel): the same title, letters only."""
    def key(s):
        return "".join(ch for ch in str(s).casefold() if ch.isalnum())
    return key(a) == key(b) and len(key(a)) > 12


def _title_q(title, words=6):
    """A title as an archive.org search: its first words, letters and digits only."""
    ws = ["".join(ch for ch in w if ch.isalnum()) for w in str(title).split()]
    return " ".join(w for w in ws if w)[:120] if len([w for w in ws if w]) <= words else " ".join([w for w in ws if w][:words])


def _twin(a, b, n=28):
    """Two listings of one film: their titles open with the same letters (libraries cut a long title differently)."""
    def key(s):
        return "".join(ch for ch in str(s).casefold() if ch.isalnum())
    ka, kb = key(a), key(b)
    return len(ka) >= 16 and len(kb) >= 16 and ka[:n] == kb[:n]


def line_shots(dur, shot_s=None, line_max=None):
    """(clips a line asks for, each one's window in seconds). Without shot_s: the engine's own pace, a clip about every
    SHOT_S (at most four a line), each dur / n long. With shot_s (a caller that cuts faster): ceil(dur / shot_s) + 2,
    at most line_max (10), each window clamp(2 * shot_s, cut.MIN_S, 1.6) s, so a clip holds its most moving part."""
    if shot_s:
        n = max(1, min(int(line_max or 10), math.ceil(dur / float(shot_s)) + 2))
        return n, min(max(2 * float(shot_s), cut.MIN_S), 1.6)
    n = max(1, min(4, round(dur / SHOT_S)))
    return n, dur / n


def find(lines, named, about, out_dir, W=1080, H=1920, log=print, max_sources=MAX_SOURCES, keep_work=False, anchors=None, avoid=None,
         hear_s=None, read_s=None, per=None, want_voices=None, preserve_source=False, downloaded_only=False, shot_s=None, line_max=None,
         strict_lines=False, motion_first=False, alloc=None, reassign_lost=False):
    """downloaded_only: cut the scanned rendition without reopening remote streams.
    avoid: [{"url", "time_range"}] already cut for this film by an earlier run: those seconds of a source are not cut again.
    hear_s, read_s, per: a caller with a deadline (a first film, score.film) hears less or nothing of each source, reads
    less of it and opens fewer sources a subject; None keeps the engine's own (HEAR_MAX_S, fetch.PROXY_MAX_S, PER_SUBJECT).
    shot_s, line_max: a caller that cuts faster than SHOT_S (a story reel: a picture every 0.45 s) asks for
    min(line_max, ceil(dur / shot_s) + 2) clips a line, each window clamp(2 * shot_s, cut.MIN_S, 1.6) s; None keeps
    min(4, round(dur / SHOT_S)) clips of dur / n s.
    Opt-in (a story reel; all off by default, so every other caller cuts exactly as before):
    strict_lines   a frame fills line i only when the look named i among its lines (never by its subject alone)
    motion_first   each source shot's content motion is measured on the proxy (footage.motion): short shots (0.3 s and
                   more) are looked at, moving shots first; a slot ranks (moving, named for the line, grade); the window
                   sits on the shot's most moving span; every clip records motion_p90
    alloc="round"  the lines take one pick each a round, the scarcest line first
    reassign_lost  a pick whose cut fails marks its source dead; the lines that lost picks are assigned again and cut"""
    per = int(per) if per else PER_SUBJECT
    t_all = time.monotonic()
    os.makedirs(os.path.join(out_dir, "clips"), exist_ok=True)
    os.makedirs(os.path.join(out_dir, "sheets"), exist_ok=True)
    work = tempfile.mkdtemp(prefix="footage-", dir=os.environ.get("FOOTAGE_WORK") or out_dir)
    spend = dict(plan=0.0, rank=0.0, moments=0.0, look=0.0, hear=0.0)
    lines = [dict(ln, i=ln.get("i", k)) for k, ln in enumerate(lines)]
    try:
        # 1. what to search for
        pl, spend["plan"] = plan.plan(lines, named, about)
        subjects = pl["subjects"]
        shows = {ln["i"]: ln for ln in pl["lines"]}
        log(f"plan: {len(subjects)} subjects, {sum(len(s['searches']) for s in subjects)} searches")
        # 2. search
        flat = [(si, q) for si, s in enumerate(subjects) for q in s["searches"]]
        found = search.run([q for _, q in flat])
        hits = {}
        for k, (si, _) in enumerate(flat):
            have = {h["id"] for h in hits.get(si, [])}
            hits.setdefault(si, []).extend(h for h in found.get(k) or [] if h["id"] not in have)
        # 3. which sources to open. A caller with a deadline is offered the short sources first: they open whole and fast
        if read_s:
            hits = {si: ([h for h in hs if h.get("duration") and float(h["duration"]) <= WHOLE_UNDER_S] or hs) for si, hs in hits.items()}
        kept, spend["rank"] = plan.rank(subjects, hits, per=per)
        sources, by_id = [], {}
        for si, h, why in kept:
            if h["id"] in by_id:
                by_id[h["id"]]["subjects"].append(si)
                continue
            if len(sources) >= max_sources:
                continue
            twin = next((x for x in sources if _same(x["title"], h["title"])), None)
            if twin:
                twin["subjects"].append(si)
                by_id[h["id"]] = twin
                continue
            src = dict(h, key=f"s{len(sources)}", subjects=[si], why=why)
            by_id[h["id"]] = src
            sources.append(src)
        for s in sources:
            if not s.get("channel") and s.get("where") == "youtube":
                s["channel"] = search.channel_of(s["url"])
        log(f"rank: {len(sources)} sources to open of {sum(len(v) for v in hits.values())} found")
        # Named clips are checked for visible attribution after the final crop.
        # No face recognition or reference-face comparison is used.
        anchors = dict(anchors or {})
        # 4. open them: proxy, shots, transcript
        with cf.ThreadPoolExecutor(max_workers=4) as pool:
            sources = list(pool.map(lambda s: _open(s, work, log, hear_s, read_s), sources))
        # 4b. what YouTube would not open (it takes a machine for a bot, a video is gone) is looked for on archive.org:
        #     the same film under its own title first (the film libraries upload to both), then, for a subject none
        #     of whose sources opened, that subject's own searches among archive.org's moving images
        more = []
        failed = [s for s in sources if s.get("error") and s.get("where") == "youtube"]
        if failed:
            tw = search.run([dict(where="archive", q=_title_q(s["title"])) for s in failed])
            for k, s in enumerate(failed):
                h = next((h for h in tw.get(k) or [] if _twin(h["title"], s["title"]) and h["id"] not in by_id), None)
                if h:
                    src = dict(h, key=f"s{len(sources) + len(more)}", subjects=list(s["subjects"]), why="the same film on archive.org")
                    by_id[h["id"]] = src
                    more.append(src)
        dead = [si for si in range(len(subjects)) if not any(si in s["subjects"] and not s.get("error") and s.get("shots") for s in sources)
                and not any(si in s["subjects"] for s in more)]
        if dead:
            # Archive metadata often names the subject without the interview,
            # date and channel terms in a YouTube query. Keep the model's exact
            # canonical subject within the five-query budget; rank and visual
            # checks still decide whether any result can be used.
            flat2 = [(si, dict(where="archive", q=q)) for si in dead
                     for q in list(dict.fromkeys([subjects[si]["name"]] +
                                                 [s["q"] for s in subjects[si]["searches"]]))[:5]]
            found2 = search.run([q for _, q in flat2])
            hits2 = {}
            for k, (si, _) in enumerate(flat2):
                have = {h["id"] for h in hits2.get(si, [])} | set(by_id)
                hits2.setdefault(si, []).extend(h for h in found2.get(k) or [] if h["id"] not in have)
            kept2, ru = plan.rank(subjects, hits2, per=per) if any(hits2.values()) else ([], 0.0)
            spend["rank"] += ru
            for si, h, why in kept2:
                if h["id"] in by_id:
                    by_id[h["id"]]["subjects"].append(si)
                    continue
                src = dict(h, key=f"s{len(sources) + len(more)}", subjects=[si], why=why)
                by_id[h["id"]] = src
                more.append(src)
        if failed or dead:
            log(f"archive round: {len(failed)} sources would not open, {len(dead)} subjects had none; {len(more)} archive sources to open")
        if more:
            with cf.ThreadPoolExecutor(max_workers=4) as pool:
                sources += list(pool.map(lambda s: _open(s, work, log, hear_s, read_s), more))
        spend["hear"] = round(sum(s.get("hear_usd") or 0.0 for s in sources), 5)
        live = [s for s in sources if not s.get("error") and s.get("shots")]
        # 5. moments and the picture check, per subject
        def per_subject(si):
            sub = subjects[si]
            mine = [s for s in live if si in s["subjects"]]
            if not mine:
                return [], 0.0, 0.0
            need = [dict(ln, show=(shows.get(ln["i"]) or {}).get("show")) for ln in lines
                    if sub["name"] in ((shows.get(ln["i"]) or {}).get("subjects") or []) or sub["name"] in (ln.get("pictures") or [])] or \
                   [dict(ln, show=(shows.get(ln["i"]) or {}).get("show")) for ln in lines]
            local = [dict(title=s["title"], channel=s.get("channel"), transcript=s.get("transcript")) for s in mine]
            try:
                ms, mu = look.moments(sub, need, local)
            except Exception as e:  # noqa: BLE001 no reading: the shots alone
                ms, mu = [], 0.0
                log(f"moments for {sub['name']} failed: {str(e)[:120]}")
            cands = []
            for k, s in enumerate(mine):
                said = [m["t"] for m in ms if m["src"] == k]
                if motion_first:
                    mo = motion.of_shots(s.get("proxy"), s["shots"], log=log)
                    times = look.pick_times(s["shots"], said, moving={sh for sh, v in mo.items() if v["p90"] >= motion.FLOOR}, min_s=0.3)
                else:
                    times = look.pick_times(s["shots"], said)
                fr = shots.frames(s["proxy"], times, os.path.join(work, "frames", s["key"]))
                for t, jpg in sorted(fr.items()):
                    cands.append(dict(src=k, t=t, jpg=jpg, said=look.said_at(s.get("transcript"), t), source=s["key"]))
            try:
                checked, lu = look.check(sub, need, cands, local, work)
            except Exception as e:  # noqa: BLE001 unchecked is never cut
                checked, lu = [], 0.0
                log(f"look for {sub['name']} failed: {str(e)[:120]}")
            for c in checked:
                c["subject"] = sub["name"]
                c["source"] = mine[c["src"]]["key"]
            return checked, mu, lu
        pool_frames = []
        with cf.ThreadPoolExecutor(max_workers=4) as pool:
            for checked, mu, lu in pool.map(per_subject, range(len(subjects))):
                pool_frames += checked
                spend["moments"] += mu
                spend["look"] += lu
        for sp in os.listdir(work):
            if sp.startswith("sheet_"):
                shutil.copyfile(os.path.join(work, sp), os.path.join(out_dir, "sheets", sp))
        good = [c for c in pool_frames if c["ok"] and c["clean"] and c["grade"] >= 3]
        log(f"look: {len(pool_frames)} frames checked, {len(good)} pass")
        look.dump([{k: v for k, v in c.items() if k not in ("jpg",)} for c in pool_frames], os.path.join(out_dir, "looked.json"))
        # 6. assign clips to lines: the best frame for each slot, its window inside its shot never
        #    overlapping a window already cut (a long interview take can carry several lines)
        src_of = {s["key"]: s for s in live}
        taken = {}
        for av in avoid or []:
            k = next((s["key"] for s in live if s["url"] == av.get("url")), None)
            if k and av.get("time_range"):
                taken.setdefault(k, []).append((float(av["time_range"][0]), float(av["time_range"][1])))
        motion_of = {}
        if motion_first:
            for s in live:
                motion_of[s["key"]] = motion.of_shots(s.get("proxy"), s["shots"], log=log)
        plan_rows = assign(lines, good, src_of, shows, taken, shot_s=shot_s, line_max=line_max, strict=strict_lines,
                           motion_of=motion_of if motion_first else None, alloc=alloc)
        # 7. cut: resolve each used source's stream once, cut, measure, grade to one look, finish
        need_src = sorted({p["source"] for r in plan_rows for p in r["picks"]})
        def resolve(k):
            if downloaded_only:
                return k, None
            try:
                return k, fetch.streams(src_of[k]["url"])
            except Exception as e:  # noqa: BLE001 the cut falls back to yt-dlp's own section download
                log(f"stream of {k} not resolved: {str(e)[:120]}")
                return k, None
        with cf.ThreadPoolExecutor(max_workers=4) as pool:
            stream_of = dict(pool.map(resolve, need_src))
        jobs = [(r, p) for r in plan_rows for p in r["picks"]]
        whole_of, whole_lock = {}, threading.Lock()

        def grab(job):
            r, p = job
            s = src_of[p["source"]]
            t0, t1 = p["window"]
            raw = os.path.join(work, "raw", f"l{r['i']:02d}_{p['source']}_{int(t0 * 100)}.mp4")
            os.makedirs(os.path.dirname(raw), exist_ok=True)
            def whole():
                if s.get("duration") and float(s["duration"]) <= WHOLE_UNDER_S and s.get("where") == "youtube":
                    with whole_lock:                         # one whole fetch a source, whoever asks first
                        if p["source"] not in whole_of:
                            try:
                                whole_of[p["source"]] = fetch.whole(s["url"], os.path.join(work, "whole", p["source"]))
                                log(f"source {p['source']} fetched whole for its cuts")
                            except Exception as e2:  # noqa: BLE001
                                whole_of[p["source"]] = None
                                log(f"source {p['source']} would not come whole either: {str(e2)[:120]}")
                    return whole_of.get(p["source"])
                return None
            try:
                if whole_of.get(p["source"]):
                    _, how = cut.local(whole_of[p["source"]], t0, t1, raw)
                else:
                    _, how = cut.source(s, stream_of.get(p["source"]), t0, t1, raw, whole=whole, downloaded_only=downloaded_only)
                p.update(raw=raw, t0=t0, t1=t1, how=how, stats=cut.measure(raw))
            except Exception as e:  # noqa: BLE001 the line keeps its other clips
                p["error"] = str(e)[:200]
                log(f"cut of {p['source']} at {t0:.1f} s failed: {p['error'][:140]}")
            return p
        with cf.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(grab, jobs))
        if reassign_lost:
            # a cut that failed (a bot wall, a timeout) marks its source dead for cutting; the lines that lost picks are
            # assigned again over the frames left (other sources, archive twins) and cut once more
            dead = {p["source"] for _, p in jobs if p.get("error")}
            lost = {r["i"]: sum(1 for p in r["picks"] if p.get("error")) for r in plan_rows}
            again = [ln for ln in lines if lost.get(ln["i"])]
            if dead and again:
                for r in plan_rows:
                    r["picks"] = [p for p in r["picks"] if not p.get("error")]
                    r["lost"] = lost.get(r["i"], 0)
                for k_, w_ in [(p["source"], p["window"]) for r in plan_rows for p in r["picks"]]:
                    taken.setdefault(k_, []).append(tuple(w_))
                more = assign([dict(ln, want_n=lost[ln["i"]]) for ln in again], [c for c in good if c["source"] not in dead], src_of, shows, taken,
                              shot_s=shot_s, line_max=line_max, strict=strict_lines, motion_of=motion_of if motion_first else None, alloc=alloc)
                need2 = sorted({p["source"] for r in more for p in r["picks"]} - set(stream_of))
                with cf.ThreadPoolExecutor(max_workers=4) as pool:
                    stream_of.update(dict(pool.map(resolve, need2)))
                jobs2 = []
                by_i = {r["i"]: r for r in plan_rows}
                for r2 in more:
                    row = by_i[r2["i"]]
                    row["replaced"] = len(r2["picks"])
                    row["picks"] += r2["picks"]
                    jobs2 += [(row, p) for p in r2["picks"]]
                with cf.ThreadPoolExecutor(max_workers=4) as pool:
                    list(pool.map(grab, jobs2))
                jobs = [(r, p) for r in plan_rows for p in r["picks"]]
                log(f"reassigned: {sum(lost.values())} lost picks on {len(again)} lines, {sum(r.get('replaced', 0) for r in plan_rows)} replaced")
        tgt = cut.target([p["stats"] for _, p in jobs if p.get("stats")])

        def finish(job):
            r, p = job
            if not p.get("raw"):
                return
            p["eq"] = cut.eq_for(p["stats"], tgt)
            name = f"l{r['i']:02d}_{len([q for q in r['picks'] if q.get('file')]):d}_{p['source']}_{int(p['t0'] * 100)}.mp4"
            out = os.path.join(out_dir, "clips", name)
            try:
                cut.finish(p["raw"], out, W, H, p.get("x", 0.5), p["eq"], trim=p.get("trim") or (0.0, 0.0), preserve_source=preserve_source)
                p["file"] = os.path.relpath(out, out_dir)
            except Exception as e:  # noqa: BLE001
                p["error"] = str(e)[:200]
        with cf.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(finish, jobs))
        # Verify each finished cut, not the source video's title or a single scout frame.
        from . import verify
        subject_by_name = {s["name"]: s for s in subjects}
        def verify_cut(job):
            _, p = job
            if not p.get("file"):
                return 0.0
            sub = subject_by_name.get(p["subject"])
            if sub is None:
                os.remove(os.path.join(out_dir, p.pop("file")))
                return 0.0
            record, usd = verify.clip(os.path.join(out_dir, p["file"]), sub,
                                      p["t1"] - p["t0"])
            p["verification"] = record
            if not record["ok"]:
                log(f"clip rejected for {p['subject']}: {record.get('reason')}")
                os.remove(os.path.join(out_dir, p.pop("file")))
            return usd
        with cf.ThreadPoolExecutor(max_workers=2) as pool:
            spend["look"] += sum(pool.map(verify_cut, jobs))
        # 7b. real voices: the person the film is about, heard saying a sentence that belongs after a line (voice.py)
        voices = []
        # a caller that hears nothing (the first film's wishes) has no sentence to cut in: voices only when asked or heard
        if (want_voices if want_voices is not None else hear_s != 0.0) and os.environ.get("FOOTAGE_VOICES", "1") != "0":
            try:
                voices, spend["voices"] = voice.find_voices(lines, subjects, live, anchors, out_dir, W, H, stream_of=stream_of, log=log)
            except Exception as e:  # noqa: BLE001 the narrator alone, as before
                log(f"voices failed: {str(e)[:160]}")
        # 8. the record
        out_lines = []
        for r in plan_rows:
            clips = []
            for p in r["picks"]:
                if not p.get("file"):
                    continue
                s = src_of[p["source"]]
                clips.append(dict(verification=p.get("verification"), file=p["file"], seconds=round(p["t1"] - p["t0"], 2), subject=p["subject"], shows=p["shows"], grade=p["grade"],
                                  source=dict(url=s["url"], title=s["title"], channel=s.get("channel"), where=s["where"]),
                                  time_range=[p["t0"], p["t1"]], frame_at=p["t"], x=p.get("x"), eq=p.get("eq"), trim=p.get("trim"),
                                  how=p.get("how"), sheet=p.get("sheet"), **({"motion_p90": p["motion_p90"]} if "motion_p90" in p else {})))
            out_lines.append(dict(i=r["i"], say=r["say"], show=r["show"], dur=r["dur"], want=r["want"], clips=clips,
                                  subjects=(shows.get(r["i"]) or {}).get("subjects") or [],      # what the planner read the line as naming
                                  **({"lost": r.get("lost", 0), "replaced": r.get("replaced", 0)} if reassign_lost else {})))
        spend = {k: round(v, 5) for k, v in spend.items()}
        res = dict(engine="worker/footage", model=dict(look="gpt-6-sol low", hear=hear.MODEL), about=about, frame=[W, H], preserve_source=bool(preserve_source), grade_target=tgt,
                   subjects=subjects, lines=out_lines, voices=voices,
                   sources=[{k: s.get(k) for k in ("key", "url", "title", "channel", "where", "duration", "subjects", "why", "rung", "dur_read", "error")}
                            | dict(shots=len(s.get("shots") or []), heard=len(s.get("transcript") or [])) for s in sources],
                   counts=dict(lines=len(out_lines), lines_with_footage=sum(1 for r in out_lines if r["clips"]),
                               clips=sum(len(r["clips"]) for r in out_lines), frames_checked=len(pool_frames), frames_passed=len(good),
                               sources_opened=len(live), sources_tried=len(sources)),
                   credits=[dict(line=r["i"], url=c["source"]["url"], channel=c["source"]["channel"], title=c["source"]["title"],
                                 time_range=c["time_range"]) for r in out_lines for c in r["clips"]]
                           + [dict(line=v["after"], url=v["source"]["url"], channel=v["source"]["channel"], title=v["source"]["title"],
                                   time_range=v["time_range"], voice=v["person"]) for v in voices],
                   spend=dict(spend, usd=round(sum(spend.values()), 4)), seconds=round(time.monotonic() - t_all, 1),
                   rights="Not licensed: owner's decision. Customers: switch off (footage/switch.py).")
        with open(os.path.join(out_dir, "footage.json"), "w") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        return res
    finally:
        if not keep_work:
            shutil.rmtree(work, ignore_errors=True)


def assign(lines, good, src_of, shows, taken, shot_s=None, line_max=None, strict=False, motion_of=None, alloc=None):
    """Step 6, clips to lines: [{"i", "say", "show", "dur", "want", "picks"}]. Each slot takes the best frame whose window
    inside its shot overlaps no window already cut (`taken`, updated). Defaults: line by line, a frame named for the line
    first, then frames of its subjects (strict: only frames named for it); score = grade with penalties for the same
    source and the same take. motion_of ({source key: {(a, b): {"p90", "peak"}}}): a slot ranks (moving, score), shots of
    0.25 s and more are admitted, the window sits on the shot's most moving span and the pick records motion_p90.
    alloc="round": one pick a line a round, the scarcest line first. A line's "want_n" caps its picks (a reassignment)."""
    last_src, shot_uses = None, {}
    rows, plan_rows = {}, []
    meta = {}
    for ln in lines:
        dur = _line_dur(ln)
        n, win_s = line_shots(dur, shot_s, line_max)
        if ln.get("want_n"):
            n = min(n, int(ln["want_n"]))
        names = set((shows.get(ln["i"]) or {}).get("subjects") or []) | set(ln.get("pictures") or [])
        meta[ln["i"]] = (n, win_s, names)
        rows[ln["i"]] = dict(i=ln["i"], say=ln.get("say"), show=(shows.get(ln["i"]) or {}).get("show"), dur=round(dur, 2), want=n, picks=[])
    min_len = 0.25 if motion_of is not None else 0.6

    def best_for(ln):
        nonlocal last_src
        n, win_s, names = meta[ln["i"]]
        chosen = rows[ln["i"]]["picks"]
        best, best_score = None, None
        for c in good:
            s = src_of[c["source"]]
            shot = shots.shot_at(s["shots"], c["t"])
            if shot[1] - shot[0] < min_len:
                continue
            mo = ((motion_of or {}).get(c["source"]) or {}).get(tuple(shot)) if motion_of is not None else None
            w = cut.window(shot, c["t"], win_s, peak=(mo or {}).get("peak")) if mo else cut.window(shot, c["t"], win_s)
            if any(w[0] < b + 0.3 and a < w[1] + 0.3 for a, b in taken.get(c["source"], [])):
                continue
            if ln["i"] in c["lines"]:
                score = c["grade"] + 1.0
            elif strict:
                continue
            elif c["subject"] in names and not c["lines"]:
                score = c["grade"] - 0.5
            elif c["subject"] in names:
                score = c["grade"] - 1.0
            else:
                continue
            if c["source"] == last_src:
                score -= 0.7
            if any(x["source"] == c["source"] for x in chosen):
                score -= 0.6
            if any(x["source"] == c["source"] and x["shot"] == shot for x in chosen):
                score -= 2.0            # the same take twice in one line reads as one picture held
            score -= 0.8 * shot_uses.get((c["source"], shot[0]), 0)
            key = (1 if (mo or {}).get("p90", 0.0) >= motion.FLOOR else 0, score) if motion_of is not None else score
            if best_score is None or key > best_score:
                best = dict(c, shot=shot, window=w, **({"motion_p90": round(float(mo["p90"]), 3)} if mo else {}))
                best_score = key
        if not best:
            return False
        taken.setdefault(best["source"], []).append(best["window"])
        shot_uses[(best["source"], best["shot"][0])] = shot_uses.get((best["source"], best["shot"][0]), 0) + 1
        last_src = best["source"]
        chosen.append(best)
        return True
    if alloc == "round":
        def eligible(ln):
            return sum(1 for c in good if ln["i"] in c["lines"] or (not strict and c["subject"] in meta[ln["i"]][2]))
        order = sorted(lines, key=lambda ln: (eligible(ln) / max(meta[ln["i"]][0], 1), ln["i"]))
        open_ = {ln["i"] for ln in lines}
        while open_:
            for ln in order:
                if ln["i"] not in open_:
                    continue
                if len(rows[ln["i"]]["picks"]) >= meta[ln["i"]][0] or not best_for(ln):
                    open_.discard(ln["i"])
    else:
        for ln in lines:
            for _ in range(meta[ln["i"]][0]):
                if not best_for(ln):
                    break
    for ln in lines:
        plan_rows.append(rows[ln["i"]])
    return plan_rows


def lines_of(script):
    """A WF3 script's segments as lines (with each one's spoken length when the narration timed it)."""
    sc = script
    while "segments" not in sc and isinstance(sc.get("script"), dict):     # a film.json, a script.json or the writer's own
        sc = sc["script"]
    out = []
    for k, s in enumerate(sc.get("segments") or []):
        ln = dict(i=int(s.get("i", k)), say=s.get("say"), pictures=s.get("pictures") or [], dur=s.get("dur"), pause_after=s.get("pause_after"))
        for key in ("see", "era", "beat"):            # the writer's picture brief, when the script carries one
            if s.get(key) is not None:
                ln[key] = s[key]
        out.append(ln)
    return out, sc.get("named") or [], sc.get("title") or ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--w", type=int, default=1080)
    ap.add_argument("--h", type=int, default=1920)
    ap.add_argument("--preserve-source", action="store_true", help="Keep the full source aspect and pixels for a fitted workflow layout")
    ap.add_argument("--timing", help="json: [seconds per line] from the narration")
    ap.add_argument("--anchors", help="json: {person's name: [photo paths checked as that person]}")
    ap.add_argument("--avoid", nargs="*", default=[], help="footage.json records of earlier runs for this film: their clips are not cut again")
    ap.add_argument("--shot-s", type=float, default=None, help="the caller's shot length: more, shorter clips a line (find's shot_s)")
    ap.add_argument("--line-max", type=int, default=None, help="at most this many clips a line with --shot-s")
    ap.add_argument("--per", type=int, default=None, help="sources opened a subject (find's per)")
    ap.add_argument("--lines", default=None, help="comma-separated line numbers: only these lines are searched (a top-up)")
    ap.add_argument("--strict-lines", action="store_true", help="a frame fills a line only when the look named that line")
    ap.add_argument("--motion-first", action="store_true", help="moving shots first, windows on their motion peak")
    ap.add_argument("--alloc", default=None, choices=["round"], help="round: one pick a line a round, the scarcest line first")
    ap.add_argument("--reassign-lost", action="store_true", help="lines whose cuts failed are assigned again and cut")
    a = ap.parse_args()
    lines, named, title = lines_of(json.load(open(a.script)))
    if a.timing:
        t = json.load(open(a.timing))
        for ln, d in zip(lines, t.get("durs") if isinstance(t, dict) else t):
            ln["dur"] = d
    if a.lines:                       # a top-up: only these lines, each keeping its own number
        keep = {int(x) for x in str(a.lines).split(",") if x.strip().isdigit()}
        lines = [ln for ln in lines if int(ln["i"]) in keep]
    from . import seam
    anchors = json.load(open(a.anchors)) if a.anchors else None
    avoid = [dict(url=c["source"]["url"], time_range=c["time_range"]) for p in a.avoid for ln in json.load(open(p)).get("lines") or []
             for c in ln.get("clips") or []]
    res = seam.for_lines(lines, named, title, a.out, a.w, a.h, scope="workflow", log=lambda m: print("[footage] " + m, flush=True),
                         anchors=anchors, avoid=avoid, preserve_source=a.preserve_source,
                         **{k: v for k, v in dict(shot_s=a.shot_s, line_max=a.line_max, per=a.per, strict_lines=a.strict_lines,
                                                  motion_first=a.motion_first, alloc=a.alloc, reassign_lost=a.reassign_lost).items() if v})
    if res is None:
        print(json.dumps(dict(off="VALMERA_FOOTAGE=0")))
        return
    print(json.dumps(dict(counts=res["counts"], spend=res["spend"], seconds=res["seconds"])))


if __name__ == "__main__":
    main()
