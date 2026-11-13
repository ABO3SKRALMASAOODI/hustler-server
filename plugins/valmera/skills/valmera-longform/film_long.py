"""Workflow 4's film: supply, the scene (L1) for Valmera's engine, the sound, render and check. Style: The Ledger.

The clock is the narration (words.json: every spoken word timed). Everything is baked here and the engine draws numbers:

  chapters   each chapter opens on a dark page with its number, title and question, spoken over; its pace (slow,
             steady, brisk, from the writer) sets how often a new picture lands: 1.6, 2.2 or 3.0 a 10 s, and no single
             framing holds past MAX_HOLD_S without something new landing in it (shared gap 2, measured in `beats`)
  the desk   archive photographs land as taped prints on one paper desk, documents as clipped pages carrying the
             source's words; the camera breathes into the desk while it fills; a photograph that can fill the frame
             takes it at the story's beats (hook, turn, low, payoff), with a slow push
  designed   maps (the camera travels from the first place to the last), timelines, charts, figures, spoken quotes
  the ledger the story's stakes on a card in the top-left corner, ticking on the word that changes them (lane O's tick);
             at the close it fills the frame and balances
  captions   one seat, low centre, a few words at a time, ink from what is under them
  sound      the narrator, and library sounds only where something lands (class L: at most 1.2 a 10 s, two roles in any
             30 s, under the voice by 12 dB), mastered by lane J's scene.audio.master (-14 LUFS, under -1 dBTP). Never music

Taste (week-1112/TASTE.md, class L) is checked as numbers on the plan.
"""
import json
import os
import random
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
STORIES = os.path.join(os.path.dirname(HERE), "valmera-stories")
SHELL = os.path.join(os.path.dirname(HERE), "_shell")
for p in (HERE, STORIES, SHELL):
    if p not in sys.path:
        sys.path.insert(0, p)
import chain  # noqa: E402
import design  # noqa: E402
import film as F  # noqa: E402  lane O: Clock, Ruler, curves, cover_crop, focus_of, tick_text
import pictures as PX  # noqa: E402
import story  # noqa: E402

FPS, W, H = 30, 1920, 1080
LEAD_F = F.LEAD_F
RATE = {"slow": 1.6, "steady": 2.2, "brisk": 3.0}        # new pictures a 10 s by chapter pace (atlas 1.2, WF4 1.9-3.5)
MIN_GAP_S = {"slow": 2.4, "steady": 1.7, "brisk": 1.2}
MAX_HOLD_S = 7.0                                          # WF4's p90 shot is 8.6 s; nothing holds past this unmoved
SAFE = (0.04, 0.05, 0.96, 0.95)
NASA_WANT = 7                                             # a subject with fewer pictures asks NASA's archive for more
WORLD = "File:Whole world - land and oceans 12000.jpg"   # NASA Blue Marble, public domain, equirectangular
PAL = dict(paper0="#EAE3D3", paper1="#DED5C2", ink="#1C1B19", ink_soft="#6E685E", red="#C8372D", night0="#171615",
           night1="#0E0D0C", cream="#F0E9DA", tape="#E9DFC2")
PRINT_GRADE = dict(lift=0.02, gamma=1.0, gain=0.97, sat=0.0, contrast=1.08, temp=0.03, tint=0)
BLEED_GRADE = dict(lift=0.0, gamma=1.0, gain=0.92, sat=0.25, contrast=1.06, temp=0.02, tint=0)


def r4(x):
    return round(float(x), 5)


def r4l(v):
    return [r4(q) for q in v] if isinstance(v, list) else r4(v)


# ----------------------------------------------------------------- supply

def _named_list(sc, dos):
    used = []
    for s in sc["segments"]:
        for n in s.get("pictures") or []:
            if n not in used:
                used.append(n)
    by = {n.get("name"): n for n in (dos.get("named") or []) + (sc.get("named") or [])}
    out = []
    for n in used:
        d = dict(by.get(n) or dict(name=n, searches=[n], must_show=n))
        if d.get("era"):
            d["must_show"] = f"{d.get('must_show') or n} (a real photograph or document from {d['era']})"
        out.append(d)
    return out


def _designs(ctx, sc, dos, world, out_dir):
    facts = {f["id"]: f for f in dos.get("facts") or []}
    places = {p["name"]: p for p in dos.get("places") or [] if p.get("lat") is not None and p.get("lon") is not None}
    made = {}
    for i, s in enumerate(sc["segments"]):
        d = s.get("design") or {}
        k = d.get("kind")
        p = os.path.join(out_dir, f"d{i:03d}_{k}.png")
        try:
            if k == "document" and d.get("fact") in facts:
                f = facts[d["fact"]]
                rec = design.document(f["quote"], d.get("title") or f.get("claim") or "", f.get("url"), d.get("mark"), f.get("year"), p, seed=i)
            elif k == "map" and world:
                pl = [places[n] for n in d.get("places") or [] if n in places]
                if not pl:
                    continue
                rec = design.map_page(world["path"], pl, d.get("route"), d.get("label"), p, seed=i)
            elif k == "timeline" and d.get("points"):
                rec = design.timeline(d["points"], p, seed=i)
            elif k == "chart" and d.get("bars"):
                rec = design.chart(d.get("title") or "", d["bars"], p, seed=i)
            elif k == "number" and d.get("print"):
                rec = design.number(d["print"], d.get("label"), p, seed=i, dark=(s.get("beat") in ("turn", "low")))
            elif k == "quote" and d.get("text"):
                rec = design.quote(d["text"], d.get("who"), p, seed=i)
            else:
                continue
        except Exception as e:  # noqa: BLE001 - a page that cannot be made is a gap in the film, logged
            ctx.log(f"design {i} {k}: {str(e)[:160]}")
            continue
        made[str(i)] = dict(rec, kind=k, seg=i)
    return made


def fetch_world(ctx, sup):
    """NASA's Blue Marble (24000 x 12000, public domain) for the maps. PIL's guard against huge images is lifted for this
    one known file only."""
    from PIL import Image
    guard = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = None
    try:
        return PX.commons_file(WORLD, os.path.join(sup, "world"), want_w=30000)
    except Exception as e:  # noqa: BLE001 - without the world map the film has no maps; logged
        ctx.log(f"world map: {str(e)[:160]}")
        return None
    finally:
        Image.MAX_IMAGE_PIXELS = guard


def ensure_maps(ctx, script, pack):
    """A supply that lost the world map (a download or decode failure) gets it at score, and the map pages it lacked."""
    sc, dos = script["script"], script["dossier"]
    wants = [str(i) for i, s in enumerate(sc["segments"]) if (s.get("design") or {}).get("kind") == "map"]
    redo = {str(x) for x in (direction_of(ctx).get("redesign") or [])}      # the director's hand: pages drawn again
    if not redo and (not wants or all(w in (pack.get("designs") or {}) for w in wants)):
        return pack
    world = pack.get("world") or fetch_world(ctx, ctx.supply)
    if not world:
        return pack
    keep = set(wants) | redo
    made = _designs(ctx, dict(sc, segments=[s if str(i) in keep and (str(i) in redo or str(i) not in (pack.get("designs") or {}))
                                            else dict(s, design=None) for i, s in enumerate(sc["segments"])]),
                    dos, world, os.path.join(ctx.supply, "design"))
    pk = os.path.join(ctx.supply, "pack.json")
    whole = json.load(open(pk))
    whole["pack"]["designs"] = dict(whole["pack"].get("designs") or {}, **made)
    whole["pack"]["world"] = world
    if not any(c.get("name") == "world map" for c in whole["pack"].get("credits") or []):
        whole["pack"]["credits"].append(dict(name="world map", title=world.get("title"), licence=world.get("licence"), credit=world.get("credit"), page=world.get("page")))
    whole["record"]["designs"] = len(whole["pack"]["designs"])
    json.dump(whole, open(pk, "w"), ensure_ascii=False, indent=1)
    ctx.log(f"maps recovered at score: {sorted(made)}")
    return whole["pack"]


def nasa_top_up(named, have, out, want=4, min_px=900):
    """Subjects with fewer than `want` pictures get NASA's image archive (public domain, the worker's NASA lane): its hits
    face the same vision check as Commons' (broll_source.check_named), then are downloaded at their large rendition.
    Returns ({name: [records]}, usd)."""
    w = PX.worker_dir()
    if sys.path[0] != w:                       # the worker's own modules (its llm) before the shell's of the same name
        sys.path.insert(0, w)
    m = sys.modules.get("llm")
    if m is not None and not str(getattr(m, "__file__", "")).startswith(w):
        del sys.modules["llm"]
    B = PX._worker()
    from concurrent.futures import ThreadPoolExecutor
    os.makedirs(out, exist_ok=True)

    def one(subj):
        n = subj["name"]
        if len(have.get(n) or []) >= want:
            return n, [], 0.0
        try:
            cands = B._nasa((subj.get("searches") or [n])[0], B.KIND_PHOTO, None, 8)
        except Exception:  # noqa: BLE001 - the archive did not answer: this subject keeps what it has
            return n, [], 0.0
        seen = {r.get("title") for r in have.get(n) or []}
        cands = [c for c in cands if c.get("title") not in seen][:10]
        if not cands:
            return n, [], 0.0
        s = dict(name=n, searches=(subj.get("searches") or [n])[:3], must_show=subj.get("must_show") or n, **{"is": subj.get("is", "thing")})
        try:
            kept, _rep, usd = B.check_named(s, cands, out)
        except Exception as e:  # noqa: BLE001 - unchecked is never shown
            print(f"nasa check {n}: {str(e)[:160]}", file=sys.stderr)
            return n, [], 0.0
        recs = []
        for c in kept:
            if len(recs) + len(have.get(n) or []) >= want + 1:
                break
            try:
                c = B.nasa_renditions(dict(c))
                got = B.fetch_local(c, os.path.join(out, "pics"), 1920, 1080)
            except Exception as e:  # noqa: BLE001
                print(f"nasa download {n}: {str(e)[:160]}", file=sys.stderr)
                continue
            if max(int(got.get("width") or 0), int(got.get("height") or 0)) < min_px:
                continue
            recs.append(PX._record(c, got, n))
        return n, recs, float(usd or 0)

    res, usd = {}, 0.0
    with ThreadPoolExecutor(max_workers=3) as pool:
        for n, recs, u in pool.map(one, named):
            usd += u
            if recs:
                res[n] = recs
    return res, round(usd, 4)


def ensure_pictures(ctx, script, pack):
    """A supply made before the NASA top-up existed gets it at score (once; the pack records it)."""
    want = int(NASA_WANT)
    if int(pack.get("nasa_topped") or 0) >= want:
        return pack
    named = _named_list(script["script"], script["dossier"])
    add, usd = nasa_top_up(named, pack.get("pictures") or {}, os.path.join(ctx.supply, "nasa"), want=want)
    pk = os.path.join(ctx.supply, "pack.json")
    whole = json.load(open(pk))
    P_ = whole["pack"]
    for n, recs in add.items():
        for r in recs:
            try:
                r["faces"] = PX.faces(r["path"])
            except Exception:  # noqa: BLE001
                r["faces"] = []
        P_.setdefault("pictures", {}).setdefault(n, []).extend(recs)
        P_.setdefault("credits", []).extend(dict(name=r["name"], title=r.get("title"), licence=r.get("licence"), credit=r.get("credit"),
                                                  page=r.get("page")) for r in recs)
    P_["missing"] = [x["name"] for x in named if not P_["pictures"].get(x["name"])]
    P_["nasa_topped"] = want
    whole["record"]["pictures"] = sum(len(v) for v in P_["pictures"].values())
    whole["record"]["subjects"] = len(P_["pictures"])
    whole["record"].setdefault("spend", []).append(dict(stage="score", provider="openai", model=os.environ.get("NAMED_PICTURE_MODEL", "?"),
                                                        what="NASA picture check", usd=usd))
    json.dump(whole, open(pk, "w"), ensure_ascii=False, indent=1)
    ctx.log(f"NASA top-up: {sum(len(v) for v in add.values())} pictures for {len(add)} subjects (${usd})")
    return P_


def supply(ctx, script):
    sc, dos = script["script"], script["dossier"]
    sup = ctx.supply
    os.makedirs(sup, exist_ok=True)
    tempfile.tempdir = ctx.tmp
    os.environ["TMPDIR"] = ctx.tmp
    pk = os.path.join(sup, "pack.json")
    if os.path.exists(pk):
        return json.load(open(pk))["record"]
    from concurrent.futures import ThreadPoolExecutor
    env = dict(os.environ, TMPDIR=ctx.tmp)
    named = _named_list(sc, dos)
    pool = ThreadPoolExecutor(max_workers=2)
    nar_job = pool.submit(PX.narrate, story.narration_script(sc), os.path.join(sup, "narration"), "04", f"wf4-narrate-{ctx.run_id}", env, 14400)
    pj = os.path.join(sup, "pictures.json")
    if os.path.exists(pj):                      # a resumed supply keeps the pictures it already found and checked
        _pj = json.load(open(pj))
        from concurrent.futures import Future
        pics_job = Future()
        pics_job.set_result((_pj["got"], _pj["reports"], _pj["usd"]))
    else:
        pics_job = pool.submit(PX.pictures, named, os.path.join(sup, "pics"), 5)
    world = fetch_world(ctx, sup)
    designs = _designs(ctx, sc, dos, world, os.path.join(sup, "design"))
    ctx.log(f"designed pages: {len(designs)}")
    got, reports, usd = pics_job.result()
    if not os.path.exists(pj):
        json.dump(dict(got=got, reports=reports, usd=usd), open(pj, "w"), ensure_ascii=False)
    for name, recs in got.items():
        for r in recs:
            try:
                r["faces"] = PX.faces(r["path"])
            except Exception:  # noqa: BLE001
                r["faces"] = []
    pics = {k: v for k, v in got.items() if v}
    ctx.log(f"pictures: {sum(len(v) for v in pics.values())} for {len(pics)} of {len(named)} subjects")
    nar = nar_job.result()
    ctx.log(f"narration {nar['duration']} s, gate {json.dumps(nar['gate'])[:300]}")
    credits = [dict(name=r["name"], title=r.get("title"), licence=r.get("licence"), credit=r.get("credit"), page=r.get("page"))
               for v in pics.values() for r in v]
    if world:
        credits.append(dict(name="world map", title=world.get("title"), licence=world.get("licence"), credit=world.get("credit"), page=world.get("page")))
    pack = dict(sid=ctx.sid, words=nar["words"], narration=nar["wav"], ffmpeg=PX.FF, sounds=PX.sound_facts(SOUNDS), pictures=pics, designs=designs,
        world=world, credits=credits, picture_reports=reports, missing=[n["name"] for n in named if not pics.get(n["name"])])
    rec = dict(narration_s=nar["duration"], voice=nar.get("voice"), gate=nar["gate"], pictures=sum(len(v) for v in pics.values()),
               subjects=len(pics), designs=len(designs), missing=pack["missing"],
               spend=[dict(stage="supply", provider="openai", model=os.environ.get("NAMED_PICTURE_MODEL", "claude-haiku-5-5"), what="picture check", usd=usd)])
    json.dump(dict(pack=pack, record=rec), open(pk, "w"), ensure_ascii=False, indent=1)
    pool.shutdown(wait=False)
    return rec


def load_pack(ctx):
    return json.load(open(os.path.join(ctx.supply, "pack.json")))["pack"]


# ----------------------------------------------------------------- the plan (16:9)

class Plan(F.Plan):
    """Lane O's plan with the frame of a long film."""

    def word(self, f, t, v, x, y, ink="#FFFFFF", op=1.0, align="l", note=True, **kw):
        if note:
            self.shown.append(t)
        if align != "l" and not isinstance(x, list):
            wd = self.R.width(v, t) / W
            x = x - wd if align == "r" else x - wd / 2
        it = dict(id=self.nid("w"), k="word", f=[int(f[0]), int(f[1])], t=t, v=v, x=r4l(x), y=r4l(y), ink=ink, op=r4l(op), role="big", on=int(f[0]))
        it.update(kw)
        self.items.append(it)
        return it

    def done(self, sid, dur, seed, film):
        order_k = {"shot": 0, "join": 1, "card": 2, "flash": 3, "word": 4}
        self.items.sort(key=lambda it: (it["f"][0], order_k[it["k"]], it.get("z", 0), it["id"]))
        return dict(l1=1, sid=sid, seed=seed, fps=FPS, frame=[W, H], dur_f=int(dur), assets=self.assets, spine=[], voices=self.voices,
                    film=film, items=self.items, sound=self.sounds)


def word_frame(clock, seg, name):
    """The frame of the first word of segment `seg` that belongs to `name` (its longest word first), or None."""
    toks = sorted([t for t in str(name).replace("-", " ").split() if len(F.norm_word(t)) >= 3], key=lambda t: -len(t))
    best = None
    for t in toks:
        k = F.norm_word(t)
        for w in clock.seg_words(seg):
            if F.norm_word(w["w"]) == k or (len(k) >= 5 and k in F.norm_word(w["w"])):
                f = F.Clock.f(w["t0"])
                best = f if best is None else min(best, f)
                break
    return best


def bleed_fit(rec):
    """Can this photograph fill 16:9 (upscale at most 2.4x, its people kept whole)?"""
    (fx, fy), _ = F.focus_of(rec)
    crop = F.cover_crop(rec["w"], rec["h"], W / H, (fx, fy), (0.5, 0.42))
    up = W / max((crop[2] - crop[0]) * rec["w"], 1)
    if up > 2.4:
        return False
    if rec.get("faces"):
        xs = [x for fc in rec["faces"] for x in (fc["box"][0], fc["box"][2])]
        if max(xs) - min(xs) > (crop[2] - crop[0]) * 1.02:
            return False
    return rec["w"] >= rec["h"] * 1.15


def schedule(sc, clock, pics, designs, f_end):
    """Every picture event of the film, from each chapter's pace: [(f, kind, payload, seg)]. A named photograph lands on
    the word that names it; a segment's designed page on its first word; the rest fill the segment at the chapter's
    rate, and no gap passes MAX_HOLD_S."""
    chapters = {c["id"]: c for c in sc.get("chapters") or []}
    used, ev = {}, []
    seg_n = len(sc["segments"])

    def next_rec(name):
        lst = pics.get(name) or []
        if not lst:
            return None
        k = used.get(name, 0)
        used[name] = k + 1
        return lst[k % len(lst)]

    first_of_chapter = {}
    for i, s in enumerate(sc["segments"]):
        first_of_chapter.setdefault(s.get("chapter"), i)
    for i, s in enumerate(sc["segments"]):
        a = F.Clock.f(clock.segs[i][0]) if i else 0
        b = F.Clock.f(clock.segs[i + 1][0]) if i + 1 < seg_n else f_end
        pace = (chapters.get(s.get("chapter")) or {}).get("pace") or s.get("pace") or "steady"
        pace = pace if pace in RATE else "steady"
        span = (b - a) / FPS
        want = max(1, int(round(span * RATE[pace] / 10)))
        mine = []
        if str(i) in designs:
            mine.append([a + 2, "design", designs[str(i)]])
        for n in s.get("pictures") or []:
            if not pics.get(n):
                continue
            f = word_frame(clock, i, n)
            mine.append([f if f is not None else None, "photo", n])
        # fill to the pace: other photographs of the segment's subjects, then of the chapter's
        subj = [n for n in s.get("pictures") or [] if pics.get(n)]
        if not subj:
            ch = s.get("chapter")
            subj = [n for j, s2 in enumerate(sc["segments"]) if s2.get("chapter") == ch for n in s2.get("pictures") or [] if pics.get(n)]
        k = 0
        while len(mine) < want and subj and k < 12:
            mine.append([None, "photo", subj[k % len(subj)]])
            k += 1
        # times: placed ones keep their word; the others share the free time evenly
        placed = sorted(m[0] for m in mine if m[0] is not None)
        free = [m for m in mine if m[0] is None]
        if free:
            slots = [a + int((b - a) * (j + 0.5) / (len(free) + len(placed) + 0.5)) for j in range(len(free) + len(placed))]
            slots = [x for x in slots if all(abs(x - p) > 20 for p in placed)][:len(free)]
            while len(slots) < len(free):
                slots.append(a + int((b - a) * (len(slots) + 1) / (len(free) + 2)))
            for m, x in zip(free, sorted(slots)):
                m[0] = x
        mine.sort(key=lambda m: m[0])
        gap = int(MIN_GAP_S[pace] * FPS)
        kept = []
        for m in mine:
            m[0] = min(max(m[0], a), b - 6)
            if kept and m[0] - kept[-1][0] < gap:
                if m[1] == "design" or (kept[-1][1] == "photo" and m[1] == "photo" and kept[-1][0] == a):
                    kept[-1] = m
                continue
            kept.append(m)
        for m in kept:
            payload = designs[str(i)] if m[1] == "design" else next_rec(m[2])
            if payload is None:
                continue
            ev.append(dict(f=int(m[0]), kind=m[1], name=m[2] if m[1] == "photo" else None, rec=payload, seg=i, pace=pace,
                           beat=s.get("beat"), chapter=s.get("chapter")))
    ev.sort(key=lambda e: e["f"])
    # a photograph shown again inside 45 s reads as a loop: another of the subject's, else another of the chapter's,
    # else the event goes and the hold rule below gives the picture on screen a move instead
    last, keep = {}, []
    by_ch = {}
    for s in sc["segments"]:
        for n in s.get("pictures") or []:
            if pics.get(n):
                by_ch.setdefault(s.get("chapter"), [])
                if n not in by_ch[s.get("chapter")]:
                    by_ch[s.get("chapter")].append(n)
    fresh = 45 * FPS
    for e in ev:
        if e["kind"] == "photo":
            if e["f"] - last.get(e["rec"]["path"], -10 ** 9) < fresh:
                pool = [(e["name"], r) for r in pics.get(e["name"]) or []] + \
                       [(n, r) for n in by_ch.get(e["chapter"], []) if n != e["name"] for r in pics.get(n) or []]
                alt = next(((n, r) for n, r in pool if e["f"] - last.get(r["path"], -10 ** 9) >= fresh), None)
                if not alt:
                    continue
                e = dict(e, name=alt[0], rec=alt[1])
            last[e["rec"]["path"]] = e["f"]
        keep.append(e)
    ev = keep
    # no framing past MAX_HOLD_S: a long gap gets a move inside the picture on screen ("detail"), or the subject's next print
    out = []
    for j, e in enumerate(ev):
        out.append(e)
        nxt = ev[j + 1]["f"] if j + 1 < len(ev) else f_end
        hold = MAX_HOLD_S * FPS
        t = e["f"]
        while nxt - t > hold:
            t += int(hold * 0.62) if nxt - t > hold * 1.4 else int((nxt - t) / 2)
            out.append(dict(f=t, kind="detail", name=e.get("name"), rec=e["rec"], seg=e["seg"], pace=e["pace"], beat=None,
                            chapter=e["chapter"], of=e["kind"]))
    return out, first_of_chapter


def build(sc, sup, direction=None):
    d = dict(seed=1113, bleed_every=3, cap_px=36)
    d.update(direction or {})
    rnd = random.Random(int(d["seed"]))
    clock = F.Clock(sup["words"], sc)
    voices = {
        "cap": {"font": "InterDisplay-Bold.ttf", "px": d["cap_px"], "track": 0, "shadow": {"a": 0.7}},
        "capd": {"font": "InterDisplay-Bold.ttf", "px": d["cap_px"], "track": 0},
        "chn": {"font": "InterDisplay-Bold.ttf", "px": 30, "track": 10},
        "cht": {"font": "Anton-Regular.ttf", "px": 150, "track": 0},
        "chq": {"font": "InstrumentSerif-Italic.ttf", "px": 58, "track": 0},
        "lgt": {"font": "InterDisplay-Bold.ttf", "px": 24, "track": 8},
        "lgl": {"font": "InterDisplay-Bold.ttf", "px": 26, "track": 4},
        "lgv": {"font": "Anton-Regular.ttf", "px": 38, "track": 0},
        "prm": {"font": "InstrumentSerif-Italic.ttf", "px": 88, "track": 0},
        "big": {"font": "Anton-Regular.ttf", "px": 92, "track": 0},
        "bal": {"font": "InstrumentSerif-Italic.ttf", "px": 64, "track": 0},
        "tag": {"font": "Anton-Regular.ttf", "px": 40, "track": 2},
    }
    lib = dict(sup["sounds"])
    missing = [k for k in SOUNDS if k not in lib]
    if missing:
        try:
            lib.update(PX.sound_facts(tuple(missing)))
        except Exception:  # noqa: BLE001 - without the alternates a third use of a recording is dropped instead
            pass
    P = Plan(voices, lib)
    pics = {k: v for k, v in sup["pictures"].items() if v}
    drop = set(d.get("drop") or [])
    pics = {k: [r for r in v if r.get("title") not in drop] for k, v in pics.items()}
    pics = {k: v for k, v in pics.items() if v}
    keys = {}

    def key_of(rec):
        if rec["path"] not in keys:
            keys[rec["path"]] = P.image(f"p{len(keys):03d}", rec)
        return keys[rec["path"]]

    f_voice_end = F.Clock.f(clock.dur)
    n_seg = len(sc["segments"])
    f_close = F.Clock.f(clock.segs[n_seg - 1][0]) - 4          # the ledger page takes the last segment
    f_end = f_voice_end + 54
    events, first_of = schedule(sc, clock, pics, sup.get("designs") or {}, f_close)
    chapters = sc.get("chapters") or []
    ch_idx = {c["id"]: k for k, c in enumerate(chapters)}
    # chapter cards: chapters 2.. at their first segment; chapter 1 after its hook and promise
    cards = []
    for c in chapters:
        i = first_of.get(c["id"])
        if i is None:
            continue
        if ch_idx[c["id"]] == 0:
            i = next((j for j in range(i, n_seg) if sc["segments"][j].get("chapter") == c["id"]
                      and sc["segments"][j].get("beat") not in ("hook", "promise")), None)
            if i is None or i == 0:
                continue
        s0 = F.Clock.f(clock.segs[i][0])
        prev_end = F.Clock.f(clock.segs[i - 1][1]) if i else 0
        a = max(prev_end + 4, s0 - 18)
        cards.append(dict(a=a, b=s0 + 84, chapter=c, n=ch_idx[c["id"]] + 1, seg=i))
    promise_seg = next((j for j, s in enumerate(sc["segments"]) if s.get("beat") == "promise"), None)
    if promise_seg is None:
        promise_seg = next((j for j in range(n_seg) if F.Clock.f(clock.segs[j][0]) >= 12 * FPS), 1)
    pa = F.Clock.f(clock.segs[promise_seg][0]) - 3
    promise = dict(a=pa, b=min(pa + int(4.2 * FPS), F.Clock.f(clock.segs[promise_seg][1]) + 12), text=sc.get("promise") or "")
    blocks = [(c["a"], c["b"], "chapter", c) for c in cards] + [(promise["a"], promise["b"], "promise", promise)]
    blocks.sort(key=lambda x: x[0])

    def in_block(f):
        return next((bl for bl in blocks if bl[0] <= f < bl[1]), None)

    # events that fall inside a chapter card or the promise move to its end, or go
    ev2 = []
    for e in events:
        bl = in_block(e["f"])
        if bl:
            e = dict(e, f=bl[1])
        if e["f"] >= f_close - 20:
            continue
        nb = next((bl[0] for bl in blocks if bl[0] > e["f"]), None)
        if nb is not None and nb - e["f"] < 36:       # a picture that would hold under 1.2 s before a page: it waits
            continue
        if ev2 and e["f"] - ev2[-1]["f"] < 24:
            continue
        ev2.append(e)
    events = ev2
    # ---- the timeline of bases: blocks, then events between them
    timeline = []
    marks = sorted([(e["f"], "ev", e) for e in events] + [(bl[0], "bl", bl) for bl in blocks])
    for j, (f0, typ, x) in enumerate(marks):
        f1 = marks[j + 1][0] if j + 1 < len(marks) else f_close
        if typ == "bl":
            f1 = x[1]
            nxt = marks[j + 1][0] if j + 1 < len(marks) else f_close
            if nxt > f1:      # something resumes after the block: the next event starts at the block's end
                pass
        timeline.append([f0, max(f1, f0 + 1), typ, x])
    if timeline:
        timeline[0][0] = 0
    # the base must cover every frame: after a block, the last event's picture resumes until the next mark
    fixed = []
    last_ev = None
    for j, (f0, f1, typ, x) in enumerate(timeline):
        nxt = timeline[j + 1][0] if j + 1 < len(timeline) else f_close
        if typ == "bl":
            fixed.append([f0, min(f1, nxt), typ, x])
            if nxt > f1:
                fixed.append([f1, nxt, "ev", dict(last_ev or events[0], kind=(last_ev or events[0])["kind"], resumed=True)])
        else:
            fixed.append([f0, nxt, typ, x])
            last_ev = x
    timeline = [t for t in fixed if t[1] > t[0]]
    # ---- presentation: full bleed at beats, else prints on the desk; designs by kind
    pictures, cut_pts, desk_run = [], [], []
    bleeds_since = 0
    run_cards = []          # cards on the current desk: (item, slot)
    slots = [(0.33, 0.56), (0.69, 0.53), (0.31, 0.53), (0.71, 0.57)]     # left and right: two on the desk, side by side
    desk = dict(a=None, items=[], slot=0)

    def finalize(it, end_f):
        """A print's frames, from its landing to end_f: it rises 7% into place and settles (12 frames, expo), while the
        camera breathes into the desk (0.25% a second about the frame's centre, at most 4%)."""
        a0 = desk["a"]
        it["f"][1] = end_f
        m = end_f - it["f"][0]
        x0, y0, s0, r0 = it.pop("_x"), it.pop("_y"), it.pop("_s"), it.pop("_rot")
        e12 = F.expo_out(12)
        xs, ys, ss, rs, ops = [], [], [], [], []
        settled = it["f"][0] == 0                  # frame 0 is the hook's picture already in place, never mid-landing
        for k in range(m):
            z = 1.0 + min(0.04, 0.0025 * (it["f"][0] + k - a0) / FPS)
            q = 1.0 if settled else (e12[k] if k < 12 else 1.0)
            yk = y0 + 0.07 * (1 - q)
            xs.append(r4(0.5 + (x0 - 0.5) * z))
            ys.append(r4(0.5 + (yk - 0.5) * z))
            ss.append(r4(s0 * z * (1.05 - 0.05 * q)))
            rs.append(r4(r0 + 2.5 * (1 - q)))
            ops.append(1.0 if settled else r4(min(1.0, (k + 1) / 5)))
        it["x"], it["y"], it["s"], it["rot"], it["op"] = xs, ys, ss, rs, ops
        tag = it.pop("_tag", None)
        if tag and m > 24:
            # the subject's name, once, under its first print: it rides the print as the camera breathes
            tx0 = x0 - it["w"] / 2 + 0.004
            ty0 = y0 + it["h"] / 2 + 0.052
            k0 = 12
            txs = [r4(0.5 + (tx0 - 0.5) * (1.0 + min(0.04, 0.0025 * (it["f"][0] + k - a0) / FPS))) for k in range(k0, m)]
            tys = [r4(0.5 + (ty0 - 0.5) * (1.0 + min(0.04, 0.0025 * (it["f"][0] + k - a0) / FPS))) for k in range(k0, m)]
            if max(tys) < SAFE[3] - 0.07:
                P.word([it["f"][0] + k0, end_f], tag, "tag", txs, tys, PAL["red"], [r4(min(1.0, (k + 1) / 5)) for k in range(m - k0)], z=45)
        tp = it.pop("_tape", None)
        if tp is not None:
            if m > 7:
                tp["f"] = [it["f"][0] + 6, end_f]
                tp["x"] = xs[6:]
                tp["y"] = [r4(yv - it["h"] * sv / 2 + 0.004) for yv, sv in zip(ys[6:], ss[6:])]
                tp["s"] = [r4(z_) for z_ in ss[6:]]
            else:
                P.items.remove(tp)

    def close_desk(end_f):
        if desk["a"] is None:
            return
        P.page([desk["a"], end_f], PAL["paper0"], PAL["paper1"], grain=0.05)
        for it, _ in desk["items"]:
            finalize(it, end_f)
        desk.update(a=None, items=[], slot=0)

    tagged = set()

    def land_card(f0, key, rec, box_h, kind, tape=True, rot=None, name=None):
        pw, ph = rec["w"], rec["h"]
        aspect = pw / ph
        h = box_h if aspect < 1.25 else min(box_h, 0.50 * W / H / aspect)
        w_ = h * aspect * H / W
        if w_ > 0.40:
            w_ = 0.40
            h = w_ * W / H / aspect
        sx, sy = slots[desk["slot"] % len(slots)]
        desk["slot"] += 1
        rot = rnd.uniform(-3.2, 3.2) if rot is None else rot
        it = P.card([f0, f0 + 1], key, [sx - w_ / 2, sy - h / 2, sx + w_ / 2, sy + h / 2], z=1 + desk["slot"],
                    radius=0.0, shadow=dict(a=0.42, blur=0.012, dx=0.002, dy=0.008))
        if kind == "photo":
            it["grade"] = PRINT_GRADE
            it["border"] = dict(w=0.006, c="#F6F2E8")
        it["_x"], it["_y"], it["_s"], it["_rot"] = sx, sy, 1.0, rot
        if tape and kind == "photo":
            it["_tape"] = P.card([f0 + 6, f0 + 7], "plate", [sx - 0.04, sy - 0.012, sx + 0.04, sy + 0.012], z=30 + desk["slot"],
                                 plate=dict(c0=PAL["tape"], c1=PAL["tape"], grain=0.08), rot=rnd.uniform(-7, 7), op=0.86)
        if kind == "photo" and name and name not in tagged and len(name) <= 28:
            it["_tag"] = name.upper()
            tagged.add(name)
        desk["items"].append((it, f0))
        if len(desk["items"]) > 2:          # more than two on the desk: the oldest goes, so nothing covers a page
            old, _ = desk["items"].pop(0)
            finalize(old, f0 + 8)
        return it

    for idx, (f0, f1, typ, x) in enumerate(timeline):
        if typ == "bl":
            close_desk(f0)
            kind, data = x[2], x[3]
            if kind == "chapter":
                P.page([f0, f1], PAL["night0"], PAL["night1"], grain=0.06)
                c = data["chapter"]
                n = f1 - f0
                e = F.expo_out(14)
                ys = [r4(0.47 + 0.02 * (1 - (e[k] if k < 14 else 1))) for k in range(n)]
                ops = [r4(min(1.0, (k + 1) / 8)) for k in range(n)]
                P.word([f0 + 2, f1], f"CHAPTER {data['n']} / {len(chapters)}", "chn", 0.5, 0.30, PAL["red"], 0.95, align="c", z=5)
                title = str(c.get("title") or "").upper()
                vt = f"cht{data['n']}"
                voices[vt] = dict(voices["cht"])
                voices[vt]["px"] = P.R.fit_px(vt, title, 0.84 * W, 150)
                P.word([f0, f1], title, vt, 0.5, ys, PAL["cream"], ops, align="c", z=5)
                q = str(c.get("question") or "")
                vq = f"chq{data['n']}"
                voices[vq] = dict(voices["chq"])
                voices[vq]["px"] = P.R.fit_px(vq, q, 0.80 * W, 58)
                if q and n > 14:
                    P.word([f0 + 12, f1], q, vq, 0.5, 0.62, PAL["cream"], [r4(min(0.92, (k + 1) / 10)) for k in range(n - 12)], align="c", z=5)
                P.events.append(f0)
                pictures.append(dict(f=[f0, f1], kind="chapter", text=title))
                is_turn = any(s.get("beat") == "turn" for s in sc["segments"] if s.get("chapter") == c["id"])
                P.hit("impact_1" if is_turn else "whoosh_soft_2", f0, -15.0 if is_turn else -20.0)
            else:
                P.page([f0, f1], PAL["paper0"], PAL["paper1"], grain=0.05)
                text = data["text"]
                voices["prm"]["px"] = P.R.fit_px("prm", text, 0.84 * W, 88)
                n = f1 - f0
                e = F.expo_out(16)
                P.word([f0, f1], text, "prm", 0.5, [r4(0.50 + 0.015 * (1 - (e[k] if k < 16 else 1))) for k in range(n)], PAL["ink"],
                       [r4(min(1.0, (k + 1) / 9)) for k in range(n)], align="c", z=5)
                wd = P.R.width("prm", text) / W
                P.block([f0 + 10, f1], [0.5 - wd / 2, 0.535, 0.5 - wd / 2 + wd * 0.999, 0.541], PAL["red"], z=5,
                        op=[r4(min(1.0, (k + 1) / 6)) for k in range(n - 10)])
                P.events.append(f0)
                pictures.append(dict(f=[f0, f1], kind="promise", text=text))
            cut_pts.append(f0)
            continue
        e = x
        kind = e["kind"]
        rec = e["rec"]
        if kind == "detail":
            # a move inside what is on screen: the desk's last print comes forward, or the full-bleed photo reframes
            if desk["a"] is not None and desk["items"]:
                it, _ = desk["items"][-1]
                pictures.append(dict(f=[f0, f1], kind="detail", of="print"))
                P.events.append(f0)
                # the next of the subject's prints joins the desk if it has one, else the desk holds and breathes
                lst = pics.get(e.get("name")) or []
                alt = next((r for r in lst if r["path"] not in [i_["pic"] for i_, _ in desk["items"]] and r["path"] != rec.get("path")), None)
                if alt:
                    land_card(f0, key_of(alt), alt, 0.60, "photo")
                    pictures[-1]["kind"] = "photo"
                    pictures[-1]["path"] = alt["path"]
                continue
            if e.get("of") == "photo" and timeline and P.items:
                rec2 = rec
                (fx, fy), _ = F.focus_of(rec2)
                crop = F.cover_crop(rec2["w"], rec2["h"], W / H, (fx, fy), (0.5, 0.42))
                cw, ch = (crop[2] - crop[0]) * 0.80, (crop[3] - crop[1]) * 0.80
                c2 = [min(max(fx - cw / 2, 0), 1 - cw), min(max(fy - ch * 0.45, 0), 1 - ch)]
                c2 = [c2[0], c2[1], c2[0] + cw, c2[1] + ch]
                n = f1 - f0
                P.shot([f0, f1], key_of(rec2), F.push(c2, n, 0.03, (fx, fy)), grade=BLEED_GRADE)
                pictures.append(dict(f=[f0, f1], kind="detail", of="bleed", path=rec2["path"]))
                P.events.append(f0)
                cut_pts.append(f0)
                continue
            if e.get("of") == "design":
                n = f1 - f0
                P.shot([f0, f1], key_of(rec), F.push([0.06, 0.06, 0.94, 0.94], n, 0.03, (0.5, 0.5)))
                pictures.append(dict(f=[f0, f1], kind="detail", of="design"))
                P.events.append(f0)
                cut_pts.append(f0)
                continue
        if kind == "photo":
            beat = e.get("beat")
            want_bleed = bleed_fit(rec) and (beat in ("hook", "turn", "low", "payoff", "close") or bleeds_since >= int(d["bleed_every"]))
            if want_bleed:
                close_desk(f0)
                (fx, fy), _ = F.focus_of(rec)
                crop = F.cover_crop(rec["w"], rec["h"], W / H, (fx, fy), (0.5, 0.42))
                n = f1 - f0
                amount = min(0.06, 0.015 * n / FPS)
                P.shot([f0, f1], key_of(rec), F.push(crop, n, amount, (fx, fy)), grade=BLEED_GRADE)
                pictures.append(dict(f=[f0, f1], kind="photo", mode="bleed", name=e.get("name"), path=rec["path"],
                                     upscale=r4(W / max((crop[2] - crop[0]) * rec["w"], 1))))
                bleeds_since = 0
                cut_pts.append(f0)
                P.events.append(f0)
                P.hit("whoosh_soft_1", f0, -25.0)
                continue
            if desk["a"] is None:
                desk["a"] = f0
                cut_pts.append(f0)
            if True:
                land_card(f0, key_of(rec), rec, 0.60, "photo", name=e.get("name"))
                pictures.append(dict(f=[f0, f1], kind="photo", mode="print", name=e.get("name"), path=rec["path"]))
                P.events.append(f0)
                if f0 > 0:
                    P.hit("shutter_2", f0 + 2, -27.0)       # the print meets the desk two frames in
                bleeds_since += 1
            continue
        if kind == "design":
            dk = rec.get("kind")
            if dk == "document":
                if desk["a"] is None:
                    desk["a"] = f0
                    cut_pts.append(f0)
                if True:
                    land_card(f0, key_of(rec), rec, 0.80, "document", tape=False, rot=rnd.uniform(-2, 2))
                    pictures.append(dict(f=[f0, f1], kind="design", design="document", path=rec["path"]))
                    P.events.append(f0)
                    P.hit("whoosh_soft_1", f0, -26.0)
                continue
            close_desk(f0)
            n = f1 - f0
            if dk == "map" and rec.get("points"):
                ptsv = list(rec["points"].values())
                (ax, ay), (bx, by) = ptsv[0], ptsv[-1]
                cw = 0.62
                ch = cw
                path = []
                ez = F.ease_io(n)
                for k in range(n):
                    q = ez[k]
                    cx = ax + (bx - ax) * q
                    cy = ay + (by - ay) * q
                    x0 = min(max(cx - cw / 2, 0), 1 - cw)
                    y0 = min(max(cy - ch / 2, 0), 1 - ch)
                    path.append([r4(x0), r4(y0), r4(x0 + cw), r4(y0 + ch)])
                if len(ptsv) == 1 or e.get("resumed"):
                    path = F.push([0.0, 0.0, 1.0, 1.0], n, 0.05, ptsv[0])
                P.shot([f0, f1], key_of(rec), path)
                P.hit("whoosh_soft_1", f0, -22.0)
            else:
                P.shot([f0, f1], key_of(rec), F.push([0.0, 0.0, 1.0, 1.0], n, 0.035, (0.5, 0.5)))
            pictures.append(dict(f=[f0, f1], kind="design", design=dk, path=rec["path"]))
            P.events.append(f0)
            cut_pts.append(f0)
            if dk != "map":
                P.hit("whoosh_soft_1", f0, -25.0)
            continue
    close_desk(f_close)
    # ---- the close: the ledger fills the frame and balances
    led = sc.get("ledger") or {}
    rows = led.get("rows") or []
    ticks = []
    for i, s in enumerate(sc["segments"]):
        for t in s.get("ticks") or []:
            f = word_frame(clock, i, t.get("word") or "")
            ticks.append(dict(t, f=f if f is not None else F.Clock.f(clock.segs[i][0]) + 6, seg=i, beat=s.get("beat")))
    ticks.sort(key=lambda t: t["f"])
    P.page([f_close, f_end], PAL["paper0"], PAL["paper1"], grain=0.05)
    pictures.append(dict(f=[f_close, f_end], kind="ledger", text="balance"))
    P.events.append(f_close)
    title = str(led.get("title") or "LEDGER").upper()
    P.word([f_close + 2, f_end], title, "big", 0.5, 0.20, PAL["red"], [r4(min(1, (k + 1) / 8)) for k in range(f_end - f_close - 2)], align="c", z=5)
    row_y0 = 0.33
    gap_y = min(0.12, 0.40 / max(len(rows), 1))
    final_vals = {}
    for r in rows:
        v = str(r.get("start"))
        for t in ticks:
            if t.get("row") == r["id"]:
                v = str(t.get("to"))
        final_vals[r["id"]] = v
    for k, r in enumerate(rows):
        a = f_close + 8 + 6 * k
        y = row_y0 + gap_y * (k + 1)
        P.word([a, f_end], str(r.get("label") or "").upper(), "lgl", 0.22, y, PAL["ink_soft"], [r4(min(1, (q + 1) / 6)) for q in range(f_end - a)], z=5)
        P.word([a, f_end], final_vals[r["id"]], "big", 0.78, y + 0.012, PAL["ink"], [r4(min(1, (q + 1) / 6)) for q in range(f_end - a)], align="r", z=5)
    rule_y = row_y0 + gap_y * (len(rows) + 1) - 0.035
    a = f_close + 8 + 6 * len(rows) + 10
    n = f_end - a
    if n > 4:
        grow = F.expo_out(14)
        for dy in (0.0, 0.012):
            P.block([a, f_end], [0.22, rule_y + dy, 0.78, rule_y + dy + 0.004], PAL["ink"], z=5,
                    s=[r4(grow[q] if q < 14 else 1.0) for q in range(n)])
        P.hit("impact_1", a, -15.0)
        P.events.append(a)
        bal = str((sc.get("close") or {}).get("balance") or "")
        if bal:
            voices["bal"]["px"] = P.R.fit_px("bal", bal, 0.80 * W, 64)
            P.word([a + 10, f_end], bal, "bal", 0.5, rule_y + 0.11, PAL["red"], [r4(min(1, (q + 1) / 10)) for q in range(f_end - a - 10)], align="c", z=5)
    # ---- the ledger card, top left, through the film (hidden on chapter and promise pages)
    if rows:
        lx0, ly0 = 0.025, 0.045
        lw, row_h = 0.215, 0.052
        lh = 0.05 + row_h * len(rows)
        spans, cur = [], 0
        for bl in sorted(blocks):
            if bl[0] > cur:
                spans.append((cur, bl[0]))
            cur = max(cur, bl[1])
        if cur < f_close:
            spans.append((cur, f_close))
        low_seg = next((i for i, s in enumerate(sc["segments"]) if s.get("beat") == "low"), None)
        low_f = F.Clock.f(clock.segs[low_seg][0]) if low_seg is not None else None
        for (sa, sb) in spans:
            if sb - sa < 6:
                continue
            P.card([sa, sb], "plate", [lx0, ly0, lx0 + lw, ly0 + lh], z=60, plate=dict(c0=PAL["cream"], c1=PAL["paper0"], grain=0.06),
                   shadow=dict(a=0.35, blur=0.008, dx=0.0, dy=0.004), border=dict(w=0.0012, c=PAL["ink"]))
            P.word([sa, sb], title, "lgt", lx0 + 0.014, ly0 + 0.034, PAL["red"], 1.0, z=61, note=False)
            for k, r in enumerate(rows):
                yb = ly0 + 0.05 + row_h * k + row_h * 0.66
                P.word([sa, sb], str(r.get("label") or "").upper(), "lgl", lx0 + 0.014, yb, PAL["ink_soft"], 1.0, z=61, note=False)
                vals = [(0, str(r.get("start")))] + [(t["f"], str(t.get("to"))) for t in ticks if t.get("row") == r["id"]]
                for j, (vf, text) in enumerate(vals):
                    vf2 = vals[j + 1][0] if j + 1 < len(vals) else 10 ** 9
                    a_, b_ = max(vf, sa), min(vf2, sb)
                    if b_ - a_ < 1:
                        continue
                    prev = vals[j - 1][1] if j else None
                    ink = PAL["ink"]
                    if low_f is not None and vf <= low_f < vf2 and j:
                        ink = PAL["red"]
                    if prev is not None and vf >= sa:
                        nt = min(8, b_ - a_)
                        for q in range(nt):
                            P.word([a_ + q, a_ + q + 1], F.tick_text(prev, text, F.expo_out(8)[q]), "lgv", lx0 + lw - 0.014, yb + 0.004, PAL["red"], 1.0,
                                   align="r", z=62, note=False)
                        a_ += nt
                        if vf >= sa and j:
                            P.hit("tick_1", vf, -24.0)
                            P.events.append(vf)
                    if b_ > a_:
                        P.word([a_, b_], text, "lgv", lx0 + lw - 0.014, yb + 0.004, ink, 1.0, align="r", z=62, note=False)
    # ---- captions: one seat, low centre; ink from what is under them
    phrases = clock.phrases(max_chars=42, max_words=7, gap=0.3)
    cap_y = 0.925

    def base_at(f):
        for it in P.items:
            if it["k"] == "shot" and it["f"][0] <= f < it["f"][1]:
                return it
        return None

    lum_cache = {}

    def lum_under(it, f):
        """Mean tone (0-1) of the band under the caption seat, read from the picture through its crop on that frame."""
        a_ = P.assets.get(it.get("pic"))
        if not a_:
            return None
        crop = it.get("crop")
        if crop and isinstance(crop[0], (list, tuple)):
            crop = crop[min(max(f - it["f"][0], 0), len(crop) - 1)]
        crop = crop or [0, 0, 1, 1]
        if a_["path"] not in lum_cache:
            from PIL import Image
            import numpy as np
            with Image.open(a_["path"]) as im_:
                lum_cache[a_["path"]] = np.asarray(im_.convert("L").resize((320, max(2, int(320 * im_.size[1] / im_.size[0])))), np.float32) / 255.0
        g = lum_cache[a_["path"]]
        x0 = int((crop[0] + 0.25 * (crop[2] - crop[0])) * g.shape[1])
        x1 = int((crop[0] + 0.75 * (crop[2] - crop[0])) * g.shape[1])
        y0 = int((crop[1] + 0.86 * (crop[3] - crop[1])) * g.shape[0])
        y1 = int((crop[1] + 0.96 * (crop[3] - crop[1])) * g.shape[0])
        return float(g[y0:max(y1, y0 + 1), x0:max(x1, x0 + 1)].mean())

    bases = sorted([it for it in P.items if it["k"] == "shot"], key=lambda it: it["f"][0])

    def ink_for(base, f):
        if base.get("pic") == "plate":
            if str((base.get("plate") or {}).get("c0", "")).upper() != PAL["paper0"].upper():
                return None                             # the chapter page speaks with its own type
            return "light"
        lu = lum_under(base, f)
        return "light" if (lu is not None and lu > 0.55 and base.get("grade") is None) else "dark"

    for i, (t0, t1, text, ws) in enumerate(phrases):
        a, b = F.Clock.f(t0) - 1, F.Clock.f(t1)
        if i + 1 < len(phrases):
            b = min(b, F.Clock.f(phrases[i + 1][0]) - 1)
        b = min(b, f_close)
        if b - a < 4 or a >= f_close:
            continue
        a = max(a, 0)
        # one caption, its ink from each picture under it: it changes ink on the cut, never sits wrong on a picture
        for base in bases:
            x0_, x1_ = max(a, base["f"][0]), min(b, base["f"][1])
            if x1_ - x0_ < 1:
                continue
            ink = ink_for(base, (x0_ + x1_) // 2)
            if ink is None:
                continue
            if ink == "light":
                P.word([x0_, x1_], text, "capd", 0.5, cap_y, PAL["ink"], 1.0, align="c", note=False, z=50)
            else:
                # white on a photograph sits on a soft pool of its own width (TASTE P4: flip, then a pool)
                wd = P.R.width("cap", text) / W
                ch_ = P.R.cap("cap") / H
                P.block([x0_, x1_], [0.5 - wd / 2 - 0.014, cap_y - ch_ - 0.016, 0.5 + wd / 2 + 0.014, cap_y + 0.014], "#0B0A09", z=49,
                        op=0.52, radius=0.006)
                P.word([x0_, x1_], text, "cap", 0.5, cap_y, "#FFFFFF", 1.0, align="c", note=False, z=50)
    # ---- sound: class L thinning (TASTE 4.2): 1.2 a 10 s, 2.5 s apart, at most two roles in any 30 s
    P.sounds = thin_sounds(P.sounds, f_end, P.lib)
    film = dict(grain=0.04, vignette=0.22, over_type=False, grade=dict(lift=0, gamma=1, gain=1, sat=1, contrast=1, temp=0, tint=0))
    l1 = P.done(sup.get("sid", "wf4"), f_end, int(d["seed"]), film)
    beats = dict(dur_f=f_end, voice_f=[LEAD_F, f_voice_end], close_f=f_close, chapters=[dict(n=c["n"], f=[c["a"], c["b"]], title=c["chapter"].get("title")) for c in cards],
                 promise_f=[promise["a"], promise["b"]], cuts=sorted(set(cut_pts)), events=sorted(set(P.events) | set(cut_pts)), pictures=pictures,
                 on_screen=P.shown, caption_y=cap_y,
                 look=sorted({0, 20, promise["a"] + 30, *[c["a"] + 30 for c in cards], *[p_["f"][0] + 20 for p_ in pictures[::9]], f_close + 60, f_end - 2}))
    return l1, beats


ROLE = {"tick_1": "tick", "whoosh_soft_1": "whoosh", "whoosh_soft_2": "whoosh", "shutter_1": "shutter", "shutter_2": "shutter",
        "impact_1": "impact", "typing_1": "typing"}
PRIORITY = {"impact_1": 0, "whoosh_soft_2": 1, "tick_1": 2, "shutter_2": 3, "shutter_1": 3, "whoosh_soft_1": 4}
ALT = {"whoosh_soft_1": "whoosh_soft_2", "whoosh_soft_2": "whoosh_soft_1", "shutter_2": "shutter_1", "shutter_1": "shutter_2"}
# a role at most this often in any 30 s: a shutter on every print would be the SFX spam TASTE calls cringe
ROLE_CAP = {"shutter": 1, "whoosh": 2, "tick": 3, "impact": 1}
SOUNDS = ("tick_1", "whoosh_soft_1", "whoosh_soft_2", "shutter_1", "shutter_2", "impact_1")


def thin_sounds(sounds, dur_f, lib=None, rate=1.0, gap_s=2.5):
    """Class L (TASTE 4.2): at most `rate` a 10 s, 2.5 s apart, at most two roles and one recording twice in any 30 s
    (a third use takes the role's other recording, lane J's alternates, else it goes). Kept by priority: the turn's
    impact, chapter pages, ledger ticks, prints, pages."""
    allow = max(1, int(rate * dur_f / FPS / 10))
    order = sorted(sounds, key=lambda s: (PRIORITY.get(s["s"], 9), s["event_f"]))
    kept = []
    win = 15 * FPS
    for s in order:
        if len(kept) >= allow:
            break
        if any(abs(s["event_f"] - k["event_f"]) < gap_s * FPS for k in kept):
            continue
        near = [k for k in kept if abs(k["event_f"] - s["event_f"]) < win]
        if len({ROLE.get(k["s"]) for k in near} | {ROLE.get(s["s"])}) > 2:
            continue
        if sum(1 for k in near if ROLE.get(k["s"]) == ROLE.get(s["s"])) >= ROLE_CAP.get(ROLE.get(s["s"]), 2):
            continue
        if sum(1 for k in near if k["s"] == s["s"]) >= 2:
            alt = ALT.get(s["s"])
            if not alt or not lib or alt not in lib or sum(1 for k in near if k["s"] == alt) >= 2:
                continue
            target = float(s["db"]) + float(lib[s["s"]]["max_db"])
            m = lib[alt]
            start = s["event_f"] / FPS - m["peak_s"]
            s = dict(s, s=alt, file=m["file"], start_s=r4(max(start, 0.0)), trim_s=r4(max(-start, 0.0)),
                     db=round(target - m["max_db"], 2), peak_s=m["peak_s"])
        kept.append(s)
    return sorted(kept, key=lambda s: s["event_f"])


# ----------------------------------------------------------------- measured on the plan

def density(beats, sc, clock_segs=None):
    """Shared gap 2, measured: new pictures a 10 s per chapter, and the longest stretch with nothing new landing."""
    ev = sorted({p["f"][0] for p in beats["pictures"]} | set(beats.get("ticks") or []))
    dur = beats["dur_f"]
    gaps = [b - a for a, b in zip([0] + ev, ev + [beats["close_f"]])]
    rows = []
    chs = beats.get("chapters") or []
    for k, c in enumerate(chs):
        a = c["f"][0]
        b = chs[k + 1]["f"][0] if k + 1 < len(chs) else beats["close_f"]
        n = sum(1 for e in ev if a <= e < b)
        g = [y - x for x, y in zip([a] + [e for e in ev if a <= e < b], [e for e in ev if a <= e < b] + [b])]
        rows.append(dict(chapter=c["n"], title=c.get("title"), seconds=round((b - a) / FPS, 1), events_10s=round(n / max((b - a) / FPS, 1e-6) * 10, 2),
                         longest_hold_s=round(max(g) / FPS, 2) if g else None))
    pics = beats["pictures"]
    per = {}
    for p in pics:
        per.setdefault(p.get("kind"), 0)
        per[p.get("kind")] += 1
    return dict(events=len(ev), events_10s=round(len(ev) / (dur / FPS) * 10, 2), longest_hold_s=round(max(gaps) / FPS, 2) if gaps else None,
                holds_over_9s=sum(1 for g in gaps if g > 9 * FPS), by_kind=per, chapters=rows,
                distinct_pictures=len({p.get("path") for p in pics if p.get("path")}))


def taste_check(l1, beats):
    R = F.Ruler(l1["voices"])
    bad = []
    try:
        import brand
        geo = brand._renderer().watermark_geometry(W, H)
        ix, iy, iw, ih = geo["image"]
        mark = (ix / W, iy / H, (ix + iw) / W, (iy + ih) / H)
    except Exception:  # noqa: BLE001
        mark = (0.80, 0.02, 0.98, 0.12)
    for it in [i for i in l1["items"] if i["k"] == "word"]:
        v = l1["voices"][it["v"]]
        x = it["x"][-1] if isinstance(it["x"], list) else it["x"]
        y = it["y"][-1] if isinstance(it["y"], list) else it["y"]
        s = (it.get("s", 1)[-1] if isinstance(it.get("s", 1), list) else it.get("s", 1)) or 1
        x1, y0 = x + R.width(it["v"], it["t"]) * s / W, y - R.cap(it["v"]) * s / H
        if it["f"][1] - it["f"][0] <= 1:
            continue
        if x < SAFE[0] - 2e-3 or x1 > SAFE[2] + 2e-3 or y0 < SAFE[1] - 2e-3 or y > SAFE[3] + 2e-3:
            bad.append(f"{it['id']} '{it['t'][:20]}': outside the safe area")
        if x < mark[2] and x1 > mark[0] and y0 < mark[3] and y > mark[1]:
            bad.append(f"{it['id']} '{it['t'][:20]}': in the watermark's keep-out")
        if float(v["px"]) * s < 22:
            bad.append(f"{it['id']} '{it['t'][:20]}': {float(v['px']) * s:.0f} px is under the reading floor")
    events = set(beats.get("events") or [])
    for s in l1.get("sound") or []:
        if not events or min(abs(s["event_f"] - e) for e in events) > 3:
            bad.append(f"sound {s['id']} at frame {s['event_f']} has nothing landing with it")
    secs = l1["dur_f"] / FPS
    n_s = len(l1.get("sound") or [])
    allow = max(1, int(1.2 * secs / 10))
    if n_s > allow:
        bad.append(f"{n_s} sounds where class L allows {allow}")
    return dict(ok=not bad, problems=bad[:20], sounds=n_s, sounds_allowed=allow, sounds_10s=round(n_s / secs * 10, 2))


# ----------------------------------------------------------------- score, render, check

def env_path():
    return F.scene_worker() + os.pathsep + os.path.join(chain.SLICE, "pylib")


def direction_of(ctx):
    d = dict(ctx.args.get("direction") or {})
    p = os.path.join(ctx.run_dir, "direction.json")
    if os.path.exists(p):
        d.update(json.load(open(p)))
    return d


def score(ctx, script):
    pack = ensure_pictures(ctx, script, ensure_maps(ctx, script, load_pack(ctx)))
    sc = script["script"]
    d = direction_of(ctx)
    sys.path[:0] = [p for p in env_path().split(os.pathsep) if p not in sys.path]
    l1, beats = build(sc, pack, d)
    out = os.path.join(ctx.supply, "l1.json")
    json.dump(l1, open(out, "w"), ensure_ascii=False)
    json.dump(beats, open(os.path.join(ctx.supply, "beats.json"), "w"), indent=1)
    taste = taste_check(l1, beats)
    dens = density(beats, sc)
    ctx.log(f"plan: {l1['dur_f'] / FPS:.1f} s, {len(l1['items'])} items, density {json.dumps(dens)}, taste {taste['ok']} {taste['problems'][:3]}")
    look = os.path.join(ctx.run_dir, "look")
    frames = sorted({int(f) for f in beats["look"] if 0 <= int(f) < l1["dur_f"]})
    chain.sh(ctx, ["env", f"PYTHONPATH={env_path()}", chain.PY, "-m", "scene.run", "still", "--l1", out, "--work", ctx.supply,
                   "--frames", ",".join(str(f) for f in frames), "--dir", look], label=f"wf4-look-{ctx.run_id}", heavy=True, timeout=3600)
    cols = 6
    subprocess.run([pack["ffmpeg"], "-v", "error", "-y", "-pattern_type", "glob", "-i", os.path.join(look, "f*.png"), "-vf",
                    f"scale=480:-2,tile={cols}x{-(-len(frames) // cols)}:padding=4", "-frames:v", "1", "-q:v", "3", os.path.join(look, "sheet.jpg")], check=True)
    if not taste["ok"]:
        raise RuntimeError("the plan breaks a taste rule: " + "; ".join(taste["problems"][:6]))
    if ctx.args.get("look", True) and not os.path.exists(os.path.join(ctx.run_dir, "look.ok")):
        raise RuntimeError(f"the director's look: open {look}/sheet.jpg, put changes in {ctx.run_dir}/direction.json, "
                           f"then touch {ctx.run_dir}/look.ok and resume")
    return dict(l1=out, beats={k: v for k, v in beats.items() if k not in ("look", "on_screen")}, taste=taste, density=dens,
                frames=l1["dur_f"], items=len(l1["items"]), direction=d, spend=[])


def level_chunks(v, sr, chunks, tol_db=3.0, max_db=12.0):
    """The narration's chunks at one level: each chunk's speech level (20 ms frames within 30 dB of its loudest) against
    the median of all; a chunk off by more than tol_db is brought back by one gain applied from the middle of the
    silence before it to the middle of the silence after it, with 40 ms ramps inside those silences. Signal only.
    Returns (v, [{chunk, db, gain_db}])."""
    import numpy as np
    n = int(0.02 * sr)
    dbs = []
    for c in chunks:
        a, b = int(float(c.get("start", 0)) * sr), int(float(c.get("end", 0)) * sr)
        x = v[a:b, 0] if v.ndim == 2 else v[a:b]
        k = len(x) // n
        if k < 5:
            dbs.append(None)
            continue
        e = 10 * np.log10((x[:k * n].reshape(k, n) ** 2).mean(1) + 1e-12)
        sp = e[e > e.max() - 30]
        dbs.append(float(10 * np.log10(np.mean(10 ** (sp / 10)))))
    good = [d for d in dbs if d is not None]
    if len(good) < 3:
        return v, []
    med = float(np.median(good))
    fixes = []
    ramp = int(0.04 * sr)
    for i, (c, d) in enumerate(zip(chunks, dbs)):
        if d is None or abs(d - med) <= tol_db:
            continue
        g = float(np.clip(med - d, -max_db, max_db))
        a = (float(chunks[i - 1]["end"]) + float(c["start"])) / 2 if i else 0.0
        b = (float(c["end"]) + float(chunks[i + 1]["start"])) / 2 if i + 1 < len(chunks) else len(v) / sr
        ia, ib = int(a * sr), min(int(b * sr), len(v))
        if ib - ia < 2 * ramp:
            continue
        env = np.ones(ib - ia, np.float32) * (10 ** (g / 20))
        env[:ramp] = np.linspace(1.0, 10 ** (g / 20), ramp)
        env[-ramp:] = np.linspace(10 ** (g / 20), 1.0, ramp)
        v[ia:ib] = v[ia:ib] * (env[:, None] if v.ndim == 2 else env)
        fixes.append(dict(chunk=i, at_s=round(a, 2), db=round(d, 1), median_db=round(med, 1), gain_db=round(g, 1)))
    return v, fixes


def audio_master(ctx, l1, narration_wav):
    """The film's sound as one file: the narration from frame LEAD_F, each library sound with its peak on its event, then
    lane J's master (voice levelled, -14 LUFS, true peak under -1 dBTP). No music, no bed."""
    import numpy as np
    sys.path[:0] = [p for p in env_path().split(os.pathsep) if p not in sys.path]
    os.environ["PATH"] = os.path.dirname(PX.FF) + os.pathsep + os.environ.get("PATH", "")
    from scene import audio as A
    sr = A.SR
    dur = l1["dur_f"] / FPS
    n = int(round(dur * sr))
    voice = np.zeros((n, 2), np.float32)
    v = A.pcm(narration_wav)
    fixes = []
    rj = os.path.join(os.path.dirname(narration_wav), "render.json")
    if os.path.exists(rj):
        v, fixes = level_chunks(v, sr, json.load(open(rj)).get("chunks") or [])
    at = int(round(LEAD_F / FPS * sr))
    v = v[:max(n - at, 0)]
    voice[at:at + len(v)] += v
    fx = np.zeros((n, 2), np.float32)
    lib_dir = os.path.join(F.scene_worker(), "sound_library")
    for s in l1.get("sound") or []:
        smp = A.pcm(os.path.join(lib_dir, os.path.basename(s["file"])))[int(round(float(s.get("trim_s", 0)) * sr)):]
        a = int(round(float(s["start_s"]) * sr))
        if a >= n or not len(smp):
            continue
        smp = smp[:n - a] * (10 ** (float(s.get("db", 0)) / 20))
        fx[a:a + len(smp)] += smp
    out = os.path.join(ctx.supply, "sound.m4a")
    rep = A.master(voice, fx, out, ctx.tmp, dur)
    return out, dict({k: v for k, v in (rep or {}).items() if k != "curve"}, chunk_levels=fixes)


def render(ctx, script, sc):
    out = os.path.join(ctx.supply, "film_engine.mp4")
    cache = os.path.join(ctx.tmp, "scene-cache")
    l1 = json.load(open(sc["l1"]))
    pack = load_pack(ctx)
    sound = os.path.join(ctx.supply, "sound.m4a")
    mix = {}
    if not os.path.exists(sound):
        sound, mix = audio_master(ctx, l1, pack["narration"])
    if not os.path.exists(out):
        log = chain.sh(ctx, ["env", f"PYTHONPATH={env_path()}", f"VALMERA_SCENE_CACHE={cache}", chain.PY, "-m", "scene.run", "render", "--l1", sc["l1"],
                             "--work", ctx.supply, "--out", out + ".part.mp4", "--tier", ctx.args.get("tier", "master"), "--strict", "--cache", cache,
                             "--audio", sound], label=f"wf4-render-{ctx.run_id}", heavy=True, timeout=6 * 3600)
        os.replace(out + ".part.mp4", out)
        try:
            j = json.loads(log[log.index("{"):log.rindex("}") + 1])
        except ValueError:
            j = {}
        json.dump(dict(j, mix=mix), open(out + ".log.json", "w"))
    j = json.load(open(out + ".log.json")) if os.path.exists(out + ".log.json") else {}
    return dict(engine="scene", film=out, engine_check=dict(frames=j.get("frames"), seconds=j.get("seconds"), audio=j.get("mix"),
                                                              degraded=j.get("degraded"), warnings=j.get("warnings")))


def check(ctx, rd, sc):
    import wf3   # lane O's mark samples on several frames
    r = chain.finish_and_check(ctx, rd, sc)
    c = r["checks"]
    rows = wf3.mark_samples(rd["film"], r["final"], n=10)
    c["mark_samples"] = rows
    if not c.get("mark_ok") and sum(x["ok"] for x in rows) >= len(rows) - 1:
        c["mark_shell"] = dict(roi=c.get("mark_roi_diff"), outside=c.get("outside_diff"), note="the shell's one sample sat on a moving frame")
        c["mark_ok"] = True
    pack = load_pack(ctx)
    rec = json.load(open(os.path.join(ctx.supply, "pack.json")))["record"]
    import brand
    pr = brand.probe(rd["film"])
    prog_s = round(pr["frames"] / round(pr["fps"]) if pr["frames"] else pr["dur"], 2)
    c["longform"] = dict(programme_s=prog_s, length_ok=600 <= prog_s <= 1200, density=sc.get("density"), taste=sc.get("taste"),
                         narration=dict(seconds=rec.get("narration_s"), voice=rec.get("voice"), gate=rec.get("gate")),
                         pictures=rec.get("pictures"), subjects=rec.get("subjects"), designs=rec.get("designs"),
                         missing_pictures=pack.get("missing"), credits=pack.get("credits"))
    c["ok"] = bool(c["length_ok"] and c["mark_ok"] and c["endcard_ok"] and c["loud_ok"] and c["music"]["ok"]
                   and c["longform"]["length_ok"] and (sc.get("taste") or {}).get("ok"))
    return r
