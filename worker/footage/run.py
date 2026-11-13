"""find(): a script's lines in, clips of real footage per line out, with their credits.

    PYTHONPATH=worker python -m footage.run --script script.json --out <dir> [--w 1080 --h 1920]

script.json: {"title", "segments": [{"say", "pictures", "dur"?}], "named": [{"name", "is", "must_show"}]}
(the WF3 writer's script shape). Writes <out>/footage.json, <out>/clips/*.mp4 and <out>/sheets/*.jpg
(the frames the check saw). Every fetched byte is in a work folder that is deleted at the end.
"""
import argparse
import concurrent.futures as cf
import json
import os
import shutil
import tempfile
import time

from . import cut, fetch, hear, identity, look, plan, search, shots

MAX_SOURCES = 16
HEAR_MAX_S = 480.0         # a source's first 8 minutes are heard; its shots are read to PROXY_MAX_S
PER_SUBJECT = 3
WORDS_PER_S = 2.8
SHOT_S = 1.5               # the cut's pace: a new shot about every 1.5 s. The references' real-video shots run a median
                           # 0.63 s (p90 2.1 s; lanes/footage/study): the Director trims, the supply gives 1-3 s to trim from


def _line_dur(ln):
    if ln.get("dur"):
        return float(ln["dur"])
    return max(1.2, len(str(ln.get("say") or "").split()) / WORDS_PER_S + float(ln.get("pause_after") or 0.2))


def _open(src, work, log):
    """Proxy, shots and transcript of one source; the error on the source when it could not be read."""
    d = os.path.join(work, "src", src["key"])
    t0 = time.monotonic()
    try:
        p, rung = fetch.proxy(src["url"], d, src.get("duration"))
        info = shots.probe(p)
        upto = min(info["dur"], fetch.PROXY_MAX_S)
        src.update(proxy=p, rung=rung, dur_read=round(upto, 1), w=info["w"], h=info["h"])
        src["shots"] = shots.cuts(p, upto)
        if info.get("audio"):
            src["transcript"], src["hear_usd"] = hear.transcribe(p, min(upto, HEAR_MAX_S))
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


def find(lines, named, about, out_dir, W=1080, H=1920, log=print, max_sources=MAX_SOURCES, keep_work=False, anchors=None, avoid=None):
    """anchors: {person's name: [photo paths already checked as that person]} for the identity step.
    avoid: [{"url", "time_range"}] already cut for this film by an earlier run: those seconds of a source are not cut again."""
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
        # 3. which sources to open
        kept, spend["rank"] = plan.rank(subjects, hits, per=PER_SUBJECT)
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
        # 3b. a person's face to hold the clips to: photographs the product's picture check already
        #     passed as that person (broll_source: Commons, gpt-6-sol), unless the caller gave its own
        anchors = dict(anchors or {})
        for sub in subjects:
            if sub.get("kind") == "person" and not anchors.get(sub["name"]):
                given = next((n.get("searches") for n in named or [] if n.get("name") == sub["name"]), None)
                anchors[sub["name"]], au = person_anchors(sub, work, searches=given)
                spend["look"] += au
                log(f"anchors for {sub['name']}: {len(anchors[sub['name']])} checked photographs")
        # 4. open them: proxy, shots, transcript
        with cf.ThreadPoolExecutor(max_workers=4) as pool:
            sources = list(pool.map(lambda s: _open(s, work, log), sources))
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
            flat2 = [(si, dict(where="archive", q=q["q"])) for si in dead for q in subjects[si]["searches"]]
            found2 = search.run([q for _, q in flat2])
            hits2 = {}
            for k, (si, _) in enumerate(flat2):
                have = {h["id"] for h in hits2.get(si, [])} | set(by_id)
                hits2.setdefault(si, []).extend(h for h in found2.get(k) or [] if h["id"] not in have)
            kept2, ru = plan.rank(subjects, hits2, per=PER_SUBJECT) if any(hits2.values()) else ([], 0.0)
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
                sources += list(pool.map(lambda s: _open(s, work, log), more))
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
            if sub.get("kind") == "person":
                try:
                    checked, rep = identity.keep_person(checked, {k: s["proxy"] for k, s in enumerate(mine)}, work,
                                                        (anchors or {}).get(sub["name"]) or ())
                except Exception as e:  # noqa: BLE001 the look's verdict stands, said in the log
                    rep = dict(identity=f"failed: {str(e)[:120]}")
                log(f"identity for {sub['name']}: {rep}")
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
        taken, last_src, plan_rows, shot_uses = {}, None, [], {}
        for av in avoid or []:
            k = next((s["key"] for s in live if s["url"] == av.get("url")), None)
            if k and av.get("time_range"):
                taken.setdefault(k, []).append((float(av["time_range"][0]), float(av["time_range"][1])))
        for ln in lines:
            dur = _line_dur(ln)
            n = max(1, min(4, round(dur / SHOT_S)))
            names = set((shows.get(ln["i"]) or {}).get("subjects") or []) | set(ln.get("pictures") or [])
            chosen = []
            for _ in range(n):
                best, best_score = None, None
                for c in good:
                    s = src_of[c["source"]]
                    shot = shots.shot_at(s["shots"], c["t"])
                    if shot[1] - shot[0] < 0.6:
                        continue
                    w = cut.window(shot, c["t"], dur / n)
                    if any(w[0] < b + 0.3 and a < w[1] + 0.3 for a, b in taken.get(c["source"], [])):
                        continue
                    if ln["i"] in c["lines"]:
                        score = c["grade"] + 1.0
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
                    if best_score is None or score > best_score:
                        best, best_score = dict(c, shot=shot, window=w), score
                if not best:
                    break
                taken.setdefault(best["source"], []).append(best["window"])
                shot_uses[(best["source"], best["shot"][0])] = shot_uses.get((best["source"], best["shot"][0]), 0) + 1
                last_src = best["source"]
                chosen.append(best)
            plan_rows.append(dict(i=ln["i"], say=ln.get("say"), show=(shows.get(ln["i"]) or {}).get("show"), dur=round(dur, 2), want=n, picks=chosen))
        # 7. cut: resolve each used source's stream once, cut, measure, grade to one look, finish
        need_src = sorted({p["source"] for r in plan_rows for p in r["picks"]})
        def resolve(k):
            try:
                return k, fetch.streams(src_of[k]["url"])
            except Exception as e:  # noqa: BLE001 the cut falls back to yt-dlp's own section download
                log(f"stream of {k} not resolved: {str(e)[:120]}")
                return k, None
        with cf.ThreadPoolExecutor(max_workers=4) as pool:
            stream_of = dict(pool.map(resolve, need_src))
        jobs = [(r, p) for r in plan_rows for p in r["picks"]]

        def grab(job):
            r, p = job
            s = src_of[p["source"]]
            t0, t1 = p["window"]
            raw = os.path.join(work, "raw", f"l{r['i']:02d}_{p['source']}_{int(t0 * 100)}.mp4")
            os.makedirs(os.path.dirname(raw), exist_ok=True)
            try:
                _, how = cut.grab(stream_of.get(p["source"]), s["url"], t0, t1, raw)
                p.update(raw=raw, t0=t0, t1=t1, how=how, stats=cut.measure(raw))
            except Exception as e:  # noqa: BLE001 the line keeps its other clips
                p["error"] = str(e)[:200]
            return p
        with cf.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(grab, jobs))
        tgt = cut.target([p["stats"] for _, p in jobs if p.get("stats")])

        def finish(job):
            r, p = job
            if not p.get("raw"):
                return
            p["eq"] = cut.eq_for(p["stats"], tgt)
            name = f"l{r['i']:02d}_{len([q for q in r['picks'] if q.get('file')]):d}_{p['source']}_{int(p['t0'] * 100)}.mp4"
            out = os.path.join(out_dir, "clips", name)
            try:
                cut.finish(p["raw"], out, W, H, p.get("x", 0.5), p["eq"], trim=p.get("trim") or (0.0, 0.0))
                p["file"] = os.path.relpath(out, out_dir)
            except Exception as e:  # noqa: BLE001
                p["error"] = str(e)[:200]
        with cf.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(finish, jobs))
        # 8. the record
        out_lines = []
        for r in plan_rows:
            clips = []
            for p in r["picks"]:
                if not p.get("file"):
                    continue
                s = src_of[p["source"]]
                clips.append(dict(file=p["file"], seconds=round(p["t1"] - p["t0"], 2), subject=p["subject"], shows=p["shows"], grade=p["grade"],
                                  source=dict(url=s["url"], title=s["title"], channel=s.get("channel"), where=s["where"]),
                                  time_range=[p["t0"], p["t1"]], frame_at=p["t"], x=p.get("x"), eq=p.get("eq"), trim=p.get("trim"),
                                  how=p.get("how"), sheet=p.get("sheet")))
            out_lines.append(dict(i=r["i"], say=r["say"], show=r["show"], dur=r["dur"], want=r["want"], clips=clips,
                                  subjects=(shows.get(r["i"]) or {}).get("subjects") or []))      # what the planner read the line as naming
        spend = {k: round(v, 5) for k, v in spend.items()}
        res = dict(engine="worker/footage", model=dict(look="gpt-6-sol low", hear=hear.MODEL), about=about, frame=[W, H], grade_target=tgt,
                   subjects=subjects, lines=out_lines,
                   sources=[{k: s.get(k) for k in ("key", "url", "title", "channel", "where", "duration", "subjects", "why", "rung", "dur_read", "error")}
                            | dict(shots=len(s.get("shots") or []), heard=len(s.get("transcript") or [])) for s in sources],
                   counts=dict(lines=len(out_lines), lines_with_footage=sum(1 for r in out_lines if r["clips"]),
                               clips=sum(len(r["clips"]) for r in out_lines), frames_checked=len(pool_frames), frames_passed=len(good),
                               sources_opened=len(live), sources_tried=len(sources)),
                   credits=[dict(line=r["i"], url=c["source"]["url"], channel=c["source"]["channel"], title=c["source"]["title"],
                                 time_range=c["time_range"]) for r in out_lines for c in r["clips"]],
                   spend=dict(spend, usd=round(sum(spend.values()), 4)), seconds=round(time.monotonic() - t_all, 1),
                   rights="Not licensed: owner's decision. Customers: switch off (footage/switch.py).")
        with open(os.path.join(out_dir, "footage.json"), "w") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        return res
    finally:
        if not keep_work:
            shutil.rmtree(work, ignore_errors=True)


def lines_of(script):
    """A WF3 script's segments as lines (with each one's spoken length when the narration timed it)."""
    sc = script
    while "segments" not in sc and isinstance(sc.get("script"), dict):     # a film.json, a script.json or the writer's own
        sc = sc["script"]
    return [dict(i=int(s.get("i", k)), say=s.get("say"), pictures=s.get("pictures") or [], dur=s.get("dur"), pause_after=s.get("pause_after"))
            for k, s in enumerate(sc.get("segments") or [])], sc.get("named") or [], sc.get("title") or ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--w", type=int, default=1080)
    ap.add_argument("--h", type=int, default=1920)
    ap.add_argument("--timing", help="json: [seconds per line] from the narration")
    ap.add_argument("--anchors", help="json: {person's name: [photo paths checked as that person]}")
    ap.add_argument("--avoid", nargs="*", default=[], help="footage.json records of earlier runs for this film: their clips are not cut again")
    a = ap.parse_args()
    lines, named, title = lines_of(json.load(open(a.script)))
    if a.timing:
        t = json.load(open(a.timing))
        for ln, d in zip(lines, t.get("durs") if isinstance(t, dict) else t):
            ln["dur"] = d
    from . import seam
    anchors = json.load(open(a.anchors)) if a.anchors else None
    avoid = [dict(url=c["source"]["url"], time_range=c["time_range"]) for p in a.avoid for ln in json.load(open(p)).get("lines") or []
             for c in ln.get("clips") or []]
    res = seam.for_lines(lines, named, title, a.out, a.w, a.h, scope="workflow", log=lambda m: print("[footage] " + m, flush=True),
                         anchors=anchors, avoid=avoid)
    if res is None:
        print(json.dumps(dict(off="VALMERA_FOOTAGE=0")))
        return
    print(json.dumps(dict(counts=res["counts"], spend=res["spend"], seconds=res["seconds"])))


if __name__ == "__main__":
    main()
