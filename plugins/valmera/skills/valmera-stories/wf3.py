"""Workflow 3 on the shell: topic and founder stories, narrated, on real pictures.

ideas   the writer finds stories on the web itself (writer.find_ideas), then each idea's pictures are probed on
        Wikimedia Commons and Openverse: an idea whose named things have no free photographs cannot be made
script  the writer writes the best idea in its style, every fact with a quote and its URL; each quote is fetched
        here from its own page and every number on screen must stand in a verified quote, or the stage fails
supply  the narrator (narrate tool, local, through heavy.sh) and the pictures (worker/broll_source: Commons and
        Openverse, checked by a vision model against what each must show, licence kept), and real video per line
        (worker/footage through _shell/chain.footage: YouTube and archive.org, transcribed and checked on OpenAI,
        1-3 s clips with their source, channel and time range in the credits); faces found in each
score   film.py bakes the scene (L1), draws stills of the beats for the director's look, checks the plan against
        the taste rules as numbers
render  the film's sound as one file (narration and the library's few sounds; never music), then Valmera's engine
        (worker/scene), through heavy.sh
check   the shell's brand finish and checks, then this workflow's: the mark on several frames, the credits
"""
import json
import math
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "_shell"))
import chain  # noqa: E402
import ear  # noqa: E402
import film as F  # noqa: E402
import fit as FIT  # noqa: E402
import pictures as PX  # noqa: E402
import reel as R  # noqa: E402
import writer  # noqa: E402

FORM = "short"
# every buildable style: the story reels (reel.py, the judge's cut) and the first three (film.py), which a run takes only
# when asked for by name (--args '{"style": ...}'): they left text-only stretches and one photograph on screen too long
BUILD = dict(F.STYLES, **{k: R.builder(k) for k in R.STYLES})
ALIEN_ALLOWED = ("rhyme", "arc")
REEL_SHOT_S, REEL_LINE_MAX, REEL_PER = 0.45, 10, 5     # a story reel asks the footage engine for a clip every 0.45 s of a line
PICTURE_S = 0.5           # a line wants a distinct fitting picture about every half second (the judge: median shot 0.49 s)


def cards():
    return {s["id"]: s for s in json.load(open(os.path.join(HERE, "styles.json")))["styles"]}


def registry_row(card):
    """The style as the shell's ledger records it."""
    return dict(id=card["id"], name=card.get("name", card["id"]), spine=card.get("spine") or [], pace=card.get("pace") or [13, 19],
                engine=card.get("engine", "stories"))


def ledger_rows(ctx):
    try:
        return json.load(open(os.path.join(ctx.state_root, "ledger.json"))).get(ctx.workflow, [])
    except (OSError, ValueError):
        return []


def styles_in_flight(ctx):
    """The styles of this workflow's other live runs in the same work root: two films made at once never share a style."""
    out, work = set(), os.path.join(ctx.state_root, "work")
    for n in os.listdir(work) if os.path.isdir(work) else []:
        if n == ctx.run_id:
            continue
        try:
            st = json.load(open(os.path.join(work, n, "run.json")))
            os.kill(int(st.get("pid")), 0)
            sid = ((json.load(open(os.path.join(work, n, "script.json"))).get("style")) or {}).get("id")
        except (OSError, ValueError, TypeError, AttributeError):
            continue
        if sid:
            out.add(sid)
    return out


def able_cards(ctx):
    """This workflow's own styles (styles.json) that are built, offered (or asked for by name), and not one of the last
    three delivered (the shell's ledger)."""
    last = set(ctx.styles_last()) | styles_in_flight(ctx)
    want = ctx.args.get("style")
    able = [s for s in cards().values() if s.get("buildable") and s["id"] in BUILD and s["id"] not in last
            and (s.get("offer", True) or s["id"] == want)]
    if want:
        able = [s for s in able if s["id"] == want]
    return able


def probe(idea):
    """How many free-licence pictures the idea's named things have (unchecked candidates), and for One Photograph,
    whether its one photograph is on Commons under a free licence."""
    B = PX._worker()
    rows, total = [], 0
    for n in (idea.get("named") or [])[:8]:
        s = dict(name=n.get("name"), searches=(n.get("searches") or [n.get("name")])[:3], must_show=n.get("must_show") or n.get("name"))
        try:
            cands, _ = B.search_named(s, kind=B.KIND_PHOTO, count=6)
        except Exception:  # noqa: BLE001
            cands = []
        big = [c for c in cands if max(int(c.get("width") or 0), int(c.get("height") or 0)) >= 900]
        rows.append(dict(name=n.get("name"), found=len(big)))
        total += min(len(big), 4)
    photo = None
    if idea.get("style") == "one-photograph":
        try:
            f = (idea.get("photo") or {}).get("file") or ""
            import urllib.parse
            import urllib.request
            q = urllib.parse.urlencode(dict(action="query", titles=f if f.startswith("File:") else "File:" + f, prop="imageinfo",
                                            iiprop="size|extmetadata", format="json"))
            d = json.load(urllib.request.urlopen(urllib.request.Request("https://commons.wikimedia.org/w/api.php?" + q,
                                                                        headers={"User-Agent": PX.UA}), timeout=20))
            pg = next(iter(d["query"]["pages"].values()))
            ii = (pg.get("imageinfo") or [{}])[0]
            lic = B.canon_licence(((ii.get("extmetadata") or {}).get("LicenseShortName") or {}).get("value"))
            photo = dict(file=f, w=ii.get("width"), h=ii.get("height"), licence=lic[0] if lic else None,
                         ok=bool(lic) and min(int(ii.get("width") or 0), int(ii.get("height") or 0)) >= 1400)
        except Exception as e:  # noqa: BLE001
            photo = dict(file=(idea.get("photo") or {}).get("file"), ok=False, error=str(e)[:120])
    covered = sum(1 for r in rows if r["found"])
    ok = (photo or {}).get("ok", True) and covered >= max(2, int(0.6 * len(rows)))
    return dict(subjects=rows, covered=covered, pictures=total, photo=photo, ok=bool(ok))


def remake_of(ctx):
    """A delivered film's record when this run re-makes it (--args '{"remake": "<its folder or film.json>"}'): the
    same story and script on today's engines. No model writes anything: the facts are fetched and checked again."""
    p = ctx.args.get("remake")
    if not p:
        return None
    p = os.path.join(p, "film.json") if os.path.isdir(p) else p
    return json.load(open(p))


def ideas(ctx):
    old = remake_of(ctx)
    if old:
        idea = dict((old.get("script") or {}).get("idea_full") or {}, supply=dict(ok=True, note="re-made from a delivered film"))
        return dict(source=dict(old.get("source") or {}, remake_of=old.get("run_id")), ideas=[idea], chosen=idea.get("title"), spend=[])
    able = able_cards(ctx)
    if not able:
        raise RuntimeError(f"no buildable style outside the last three ({ctx.styles_last()})")
    avoid = [r.get("idea") for r in ledger_rows(ctx) if r.get("idea")]
    got, use = writer.find_ideas(FORM, able, avoid=avoid, line=ctx.args.get("line"))
    for i in got[:6]:
        i["supply"] = probe(i)
    got.sort(key=lambda i: (not i.get("supply", {}).get("ok"), -(float(i.get("strength") or 0) + min(i.get("supply", {}).get("pictures", 0), 16) / 8)))
    srcs = sorted({u for i in got for u in i.get("sources") or []})
    return dict(source=dict(kind="web research by the writer model", origin="web_search + web_fetch", sources=srcs[:40],
                            searches=use.get("web_searches"), fetches=use.get("web_fetches")),
                ideas=got, chosen=None, spend=[dict(stage="ideas", **use)])


def script(ctx, ideas_out):
    old = remake_of(ctx)
    if old:
        prev = old["script"]
        sc = prev["script"]
        rep = writer.verify(sc)
        if not rep["ok"] and (prev.get("verify") or {}).get("ok") and all(r.get("error") for r in rep["facts"] if not r["found"]):
            # every miss is a page that would not open today: the delivered film's own check stands, and says so
            rep = dict(prev["verify"], reused="pages unreachable at the re-make: " + ", ".join(str(r["id"]) for r in rep["facts"] if not r["found"]))
        if not rep["ok"]:
            raise RuntimeError(f"the re-made script's facts do not verify: {json.dumps(rep)[:600]}")
        sid = (old.get("style") or {}).get("id")
        card = cards().get(sid, {"id": sid})
        reg = registry_row(card)
        spend, ann = [], None
        if sid in R.STYLES and needs_annotate(sc):
            # the delivered script lacks the picture brief and the anchors: they are written on its own words, which stay
            # as they are, so the narration is reused and the new cut still gets its briefs
            sc, ann = annotate_script(ctx, sc, prev.get("idea_full"), spend, reel=True)
        return dict(title=sc.get("title") or prev.get("title"), style=reg, window=None, hook_line=sc.get("hook_line"),
                    close_line=sc.get("close_line"), direction=f"{card.get('name', sid)}: {card.get('look', '')}", idea=prev.get("title"),
                    idea_full=prev.get("idea_full"), script=sc, verify=rep, words=writer.words_of(sc), remake_of=old.get("run_id"),
                    annotate=ann, spend=spend)
    able = {s["id"]: s for s in able_cards(ctx)}
    pick = next((i for i in ideas_out["ideas"] if i.get("style") in able and (i.get("supply") or {}).get("ok")), None)
    if not pick:
        raise RuntimeError("no idea with a buildable style and enough free pictures: " +
                           json.dumps([(i.get("title"), i.get("style"), (i.get("supply") or {}).get("covered")) for i in ideas_out["ideas"]])[:600])
    card = able[pick["style"]]
    spend, notes, rep, sc = [], ctx.args.get("notes"), None, None
    for attempt in range(4):
        sc, use = writer.write(pick, card, FORM, voices=card.get("voice"), notes=notes)
        spend.append(dict(stage="script", attempt=attempt, **use))
        rep = writer.verify(sc)
        json.dump(dict(script=sc, verify=rep), open(os.path.join(ctx.run_dir, f"script_attempt{attempt}.json"), "w"), ensure_ascii=False, indent=1)
        n_words = writer.words_of(sc)
        lo, hi = writer.FORMS[FORM]["words"]
        problems = []
        if rep["facts_unverified"]:
            why = {r["id"]: (r.get("error") or "the quote is not on the page") + f" ({r['url']})" for r in rep["facts"] if r["id"] in rep["facts_unverified"]}
            good = sorted({r["url"] for r in rep["facts"] if r["found"]})
            problems.append(f"these facts did not verify: {json.dumps(why)}; quote a readable HTML page word for word (read it with web_fetch "
                            "first; never a PDF), or use another source, or drop the fact and every sentence and number that rests on it. "
                            f"These pages were read and checked here, so their quotes verify: {json.dumps(good)}; Wikipedia always reads")
        if rep["numbers_unbacked"]:
            problems.append(f"these numbers are on screen but in no verified quote: {rep['numbers_unbacked']}")
        if not lo * 0.85 <= n_words <= hi * 1.15:
            problems.append(f"{n_words} spoken words; the form wants {lo}-{hi}")
        if not problems:
            break
        notes = "\n".join(problems)
        ctx.log("script repair: " + notes[:400])
    if rep is None or not rep["ok"]:
        raise RuntimeError(f"the script's facts do not verify: {json.dumps(rep)[:600]}")
    sc, rep = for_the_ear(ctx, sc, rep, spend)
    ann = None
    if card["id"] in R.STYLES:
        sc, ann = annotate_script(ctx, sc, pick, spend, reel=True)
    reg = registry_row(card)
    return dict(title=sc.get("title") or pick.get("title"), style=reg, window=None, hook_line=sc.get("hook_line"),
                close_line=sc.get("close_line"), direction=f"{card['name']}: {card['look']}", idea=pick.get("title"),
                idea_full=pick, script=sc, verify=rep, words=writer.words_of(sc), annotate=ann, spend=spend)


def needs_annotate(sc):
    """A script without the picture brief or the display anchors (a delivered film from before 14 Nov), or with a brief
    from before round 2 (no photo search per item, no list reading): it is annotated again on its own words."""
    segs = sc.get("segments") or []
    return any(not s.get("see") or any(isinstance(x, str) or not isinstance(x, dict) or "word_i" not in x for x in s.get("show") or [])
               or not s.get("phrases") or "list" not in s or any(isinstance(x, dict) and "search" not in x for x in s.get("see") or [])
               for s in segs)


def annotate_script(ctx, sc, idea_full, spend, reel=True):
    """The final script's picture brief, devices, display anchors and caption phrases (writer.annotate, OpenAI only).
    The words never change. --args '{"annotate": false}' skips it (and a story reel then cannot be cut by its verdicts).

    For a story reel (reel=True) the cut is never made without a per-line brief (the rated film df043a got its payoff and
    close wrong on the path that kept a script without one): a failed or non-verifying annotation is repaired, never
    kept as it was
      1. one more writer.annotate call with the problems fed back;
      2. when the annotated script does not verify, only items annotate ADDED to show can be the cause (see, phrases,
         finish and word_i touch no fact): they are dropped and the rest is kept;
      3. every segment still without a brief gets its own call (writer.annotate_segment, pinned to OpenAI);
      4. a segment still without one stops the script stage."""
    if ctx.args.get("annotate") is False:
        return sc, None
    base = writer.verify(sc)            # pages read once in this process: a second read is the cache

    def held(s):
        r = writer.verify(s)            # the annotation can add figures; it can never touch a fact
        return dict(r, ok=bool(r["facts"]) and not r["numbers_unbacked"] and set(r["facts_unverified"]) <= set(base["facts_unverified"]))
    sc2, rep, err = None, None, None
    for attempt in range(2 if reel else 1):
        try:
            sc2, rep = writer.annotate(sc2 or sc, idea_full, verify_fn=held, log=ctx.log, problems=(rep or {}).get("problems") or None)
        except Exception as e:  # noqa: BLE001
            err = repr(e)[:300]
            ctx.log(f"annotate: {e!r}"[:300])
            continue
        spend += [dict(stage="script", **u) for u in rep.get("spend") or []]
        if rep["ok"]:
            break
        ctx.log(f"annotate attempt {attempt}: ok {rep['ok']}, problems {len(rep['problems'])}")
    if sc2 is None:
        if reel:
            sc2, rep = json.loads(json.dumps(sc)), dict(ok=False, problems=[f"annotate failed: {err}"], verify=base, device_share_words=None)
        else:
            return sc, dict(ok=False, error=err)
    if not rep["verify"].get("ok"):
        if not reel:
            ctx.log("annotate: the annotated script does not verify; the script stands as it was")
            return sc, dict({k: v for k, v in rep.items() if k != "spend"}, kept=False)
        # the facts stand on the script's own show items: the brief, phrases, finishes and anchors are laid on them
        keep = json.loads(json.dumps(sc))
        for s0, s1 in zip(keep["segments"], sc2["segments"]):
            for k in ("see", "pictures", "phrases", "finish", "finish_rank", "list", "list_items"):
                if k in s1:
                    s0[k] = s1[k]
            by_text = {str(x.get("text")): x for x in s1.get("show") or [] if isinstance(x, dict)}
            show = []
            for x in s0.get("show") or []:
                x = dict(x) if isinstance(x, dict) else dict(text=str(x))
                m = by_text.get(str(x.get("text"))) or {}
                x.update({q: m[q] for q in ("role", "word_i", "big", "small") if q in m})
                show.append(x)
            s0["show"] = show
        if "colour_turn" in sc2:
            keep["colour_turn"] = sc2["colour_turn"]
        sc2 = keep
        rep = dict(rep, verify=held(sc2), kept_without_added=True)
        if not rep["verify"].get("ok"):
            raise RuntimeError(f"the annotated script does not verify even on its own show items: {json.dumps(rep['verify'])[:400]}")
    if reel:
        names = {n.get("name") for n in sc2.get("named") or [] if n.get("name")}
        for k, s_ in enumerate(sc2["segments"]):
            if s_.get("see"):
                continue
            try:
                see, use = writer.annotate_segment(sc2, k, idea_full)
                spend.append(dict(stage="script", **use))
            except Exception as e:  # noqa: BLE001
                ctx.log(f"annotate segment {k}: {e!r}"[:200])
                see = []
            see = [x for x in see if x.get("thing") in names]
            if see:
                s_["see"] = see
                s_["pictures"] = list(dict.fromkeys(x["thing"] for x in see))
        lack = [k for k, s_ in enumerate(sc2["segments"]) if not s_.get("see")]
        if lack:
            raise RuntimeError(f"no picture brief for segments {lack}: a story reel is never cut without one")
        rep["problems"] = [p for p in rep.get("problems") or [] if "no valid see item" not in p]
    json.dump(dict(script=sc2, report=rep), open(os.path.join(ctx.run_dir, "script_annotate.json"), "w"), ensure_ascii=False, indent=1)
    ctx.log(f"annotate: ok {rep['ok']}, devices {rep.get('device_share_words')} of the words, problems {len(rep.get('problems') or [])}")
    return sc2, dict({k: v for k, v in rep.items() if k != "spend"}, kept=True)


def for_the_ear(ctx, sc, rep, spend):
    """The checked script rewritten for the ear by a model (ear.py: short spoken lines, the stress word placed, a real
    turn, the film's question), audited for facts by a second call and checked again on its pages. Any failure keeps the
    script as written. --args '{"ear": false}' skips it."""
    if ctx.args.get("ear") is False:
        return sc, rep
    try:
        sc2, er = ear.for_ear(sc, "story", log=ctx.log)
    except Exception as e:  # noqa: BLE001
        ctx.log(f"ear: {e!r}"[:300])
        return sc, rep
    spend += [dict(stage="ear", **u) for u in er.get("spend") or []]
    json.dump(dict(script=sc2, report=er), open(os.path.join(ctx.run_dir, "script_ear.json"), "w"), ensure_ascii=False, indent=1)
    if not er.get("ok"):
        return sc, rep
    rep2 = writer.verify(sc2)
    if not rep2["ok"]:
        ctx.log("ear: the rewrite does not verify; the script stands as written")
        return sc, rep
    return sc2, rep2


# ----------------------------------------------------------------- supply

WHO_SYSTEM = ("You place a film's camera inside one archive photograph for its editor. You get the photograph with numbered boxes on "
              "the faces a detector found, the photograph's own description from Wikimedia Commons (which often says who stands where), "
              "the people the film names with where the writer believes they are, and the places the camera must go. For a person, give "
              "their face box only when the description's position, or something plainly visible, places them there; never guess from a "
              "face. For anything else (an object, a group, a part of the scene), give the region of the photograph that shows it. When "
              "it cannot be placed, answer null.")


def photo_targets(ctx, photo, sc):
    """{target: {"face": n} | {"box": [x0, y0, x1, y1]} | None} for every place the script's camera goes, its marks and its
    portrait, read by a model on the photograph with its faces numbered and its own Commons description."""
    import base64
    from PIL import Image, ImageDraw, ImageFont
    names = []
    for s_ in sc["segments"]:
        for k in ("look", "mark"):
            v = s_.get(k)
            if v and v != "whole" and v not in names:
                names.append(v)
    por = (sc.get("portrait") or {}).get("name")
    if por and por not in names:
        names.append(por)
    im = Image.open(photo["path"]).convert("RGB")
    k = min(1.0, 2400 / max(im.size))
    im = im.resize((int(im.size[0] * k), int(im.size[1] * k)))
    dr = ImageDraw.Draw(im)
    try:
        fnt = ImageFont.truetype(F._type().find_font("InterDisplay-Bold.ttf"), max(14, int(im.size[0] / 90)))
    except Exception:  # noqa: BLE001
        fnt = None
    for i, f in enumerate(photo["faces"]):
        x0, y0, x1, y1 = [v * s for v, s in zip(f["box"], (im.size[0], im.size[1], im.size[0], im.size[1]))]
        dr.rectangle([x0, y0, x1, y1], outline=(255, 40, 40), width=2)
        dr.text((x0, y1 + 2), str(i), fill=(255, 255, 0), font=fnt, stroke_width=2, stroke_fill=(0, 0, 0))
    # details of this photograph that the writer cited on Commons, found inside it by correlation (signal only)
    details = []
    main_title = (photo.get("title") or "").replace(" ", "_").replace("File:", "")
    import urllib.parse
    for f_ in sc.get("facts") or []:
        u = str(f_.get("url") or "")
        if "commons.wikimedia.org/wiki/File:" not in u:
            continue
        title = urllib.parse.unquote(u.split("/wiki/", 1)[1]).replace("_", " ")
        if title.replace(" ", "_").replace("File:", "") == main_title or title in [x["title"] for x in details]:
            continue
        try:
            rec = PX.commons_file(title, os.path.join(ctx.supply, "photo", f"detail{len(details)}"), want_w=1600)
            box, score = PX.locate_detail(photo["path"], rec["path"])
        except Exception as e:  # noqa: BLE001 - a detail that cannot be read or found is left out
            ctx.log(f"detail {title}: {str(e)[:120]}")
            continue
        ctx.log(f"detail {title}: box {box} score {score}")
        if box and score >= 0.45 and box[2] - box[0] <= 0.6:          # a detail, not another print of the whole
            details.append(dict(title=title, description=rec.get("description", "")[:400], box=box, score=score))
            x0, y0, x1, y1 = [v * s_ for v, s_ in zip(box, (im.size[0], im.size[1], im.size[0], im.size[1]))]
            dr.rectangle([x0, y0, x1, y1], outline=(40, 160, 255), width=3)
            dr.text((x0, y0 - max(14, int(im.size[0] / 90)) - 2), f"D{len(details) - 1}", fill=(40, 160, 255), font=fnt,
                    stroke_width=2, stroke_fill=(0, 0, 0))
    photo["details"] = details
    p = os.path.join(ctx.tmp, "who.jpg")
    im.save(p, quality=88)
    data = base64.standard_b64encode(open(p, "rb").read()).decode()
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}},
               {"type": "text", "text": f"Description from Commons: {photo.get('description', '')[:1600]}\n\nThe photograph, as the writer "
                                        f"describes it: {(sc.get('photo') or {}).get('about', '')}\n\nPeople it names: "
                                        f"{json.dumps((sc.get('photo') or {}).get('people') or [], ensure_ascii=False)}\n\nPlaces the camera goes: "
                                        f"{json.dumps(names, ensure_ascii=False)}\n\nDetails of this photograph published on Commons, found "
                                        f"inside it by image correlation and drawn in blue as D0, D1...: "
                                        f"{json.dumps([dict(d=i, title=x['title'], description=x['description'], box=x['box']) for i, x in enumerate(details)], ensure_ascii=False)}"
                                        f"\n\nThere are {len(photo['faces'])} face boxes, numbered from 0. "
                                        "Regions are fractions of the photograph's width and height, [x0, y0, x1, y1], tight around what must be "
                                        "seen. Answer JSON only: {\"targets\": {each place exactly as written: {\"face\": box number} or "
                                        "{\"box\": [x0, y0, x1, y1]} or {\"detail\": D number} or null}, \"why\": {place: a few words}}"}]
    text, use = writer.ask(WHO_SYSTEM, content, effort="high", max_tokens=8000, what="camera targets")
    got = writer.json_block(text)
    out = {}
    for n in names:
        v = (got.get("targets") or {}).get(n)
        if isinstance(v, dict) and isinstance(v.get("face"), (int, float)) and 0 <= int(v["face"]) < len(photo["faces"]):
            out[n] = {"face": int(v["face"])}
        elif isinstance(v, dict) and isinstance(v.get("detail"), (int, float)) and 0 <= int(v["detail"]) < len(details):
            out[n] = {"box": details[int(v["detail"])]["box"], "detail": details[int(v["detail"])]["title"]}
        elif isinstance(v, dict) and isinstance(v.get("box"), list) and len(v["box"]) == 4:
            x0, y0, x1, y1 = [min(max(float(q), 0.0), 1.0) for q in v["box"]]
            out[n] = {"box": [x0, y0, x1, y1]} if x1 - x0 > 0.005 and y1 - y0 > 0.005 else None
        else:
            out[n] = None
    return out, got.get("why") or {}, use


def portrait_of(ctx, photo, sc, sup_dir):
    """X2's picture: the portrait's face region redrawn as its spoken sentence in tiny type."""
    por = sc.get("portrait") or {}
    t = (photo.get("targets") or {}).get(por.get("name")) or {}
    if "face" in t:
        b = photo["faces"][t["face"]]["box"]
    elif "box" in t:
        b = list(t["box"])
        if (b[3] - b[1]) * photo["h"] > 1.6 * (b[2] - b[0]) * photo["w"]:      # a standing figure: its head and shoulders
            b[3] = b[1] + (b[2] - b[0]) * photo["w"] / photo["h"] * 1.1
    else:
        return None
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    half = max(b[2] - b[0], (b[3] - b[1]) * photo["h"] / photo["w"]) * 0.95
    hh = half * photo["w"] / photo["h"]
    reg = [max(cx - half, 0), max(cy - hh * 0.85, 0), min(cx + half, 1), min(cy + hh * 1.15, 1)]
    si = int(por.get("segment", 0))
    sentence = sc["segments"][si]["say"] if si < len(sc["segments"]) else sc["segments"][0]["say"]
    path, rows = F.microtext_image(photo["path"], reg, sentence, os.path.join(sup_dir, "photo", "micro.jpg"))
    row_y = min(max((cy - reg[1]) / max(reg[3] - reg[1], 1e-6), 0.2), 0.8)
    return dict(path=path, size=3000, region=reg, row_y=row_y)


def given_narration(ctx, sc, out):
    """A narration the narrate tool already rendered for this very script (--args '{"narration": "<its folder>"}'): the
    read is reused when its words are the script's words, one for one; otherwise the run narrates afresh."""
    src = ctx.args.get("narration")
    if not src or os.path.exists(os.path.join(out, "words.json")):
        return
    import shutil
    try:
        # a narration with a real voice cut in is reused as the narrator read it (the run cuts its own voices in again)
        wsrc = os.path.join(src, "words_narrator.json") if os.path.exists(os.path.join(src, "words_narrator.json")) else os.path.join(src, "words.json")
        got = [F.norm_word(w["w"]) for w in json.load(open(wsrc))["words"]]
        want = [F.norm_word(x) for s in sc["segments"] for x in str(s["say"]).split()]
        if [g for g in got if g] != [x for x in want if x]:
            ctx.log(f"the given narration is another script's ({len(got)} words against {len(want)}): narrating afresh")
            return
        os.makedirs(out, exist_ok=True)
        spliced = os.path.exists(os.path.join(src, "narration_narrator.wav"))
        for n in ("narration.wav", "words.json", "check.json"):
            m = {"narration.wav": "narration_narrator.wav", "words.json": "words_narrator.json"}.get(n, n) if spliced else n
            if os.path.exists(os.path.join(src, m)):
                shutil.copyfile(os.path.join(src, m), os.path.join(out, n))
        ctx.log(f"narration reused from {src}")
    except (OSError, ValueError, KeyError) as e:
        ctx.log(f"the given narration could not be read ({str(e)[:120]}): narrating afresh")


def photo_searches(sc, named):
    """The photograph searches of a story reel: one per line's brief item, the archivist's search writer.annotate wrote
    for that thing at its story time (see.search, e.g. "John Lasseter 1995"), gathered under the thing's name; a thing
    no brief item searches keeps the writer's own searches. A thing whose faithful pictures exist only in the work's own
    footage (writer.annotate's true_pictures "footage": an animated film's characters) is not searched at all: its lines
    are the footage engine's. The must_show the picture check reads is the line's own must_show followed by its as_of,
    so a toy, a ride, a sculpture or a later year is refused at search time. Pictures are then gated per line by fit."""
    named = [n for n in named if n.get("true_pictures") != "footage"]
    by = {n.get("name"): n for n in named}
    out, seen = [], set()
    for k, s_ in enumerate(sc["segments"]):
        for x in s_.get("see") or []:
            n = by.get(x.get("thing"))
            q = x.get("search")
            if not n or not q or (n["name"], q.lower()) in seen:
                continue
            seen.add((n["name"], q.lower()))
            must = " ".join(str(v).strip() for v in (x.get("must_show") or n.get("must_show") or n["name"], x.get("as_of")) if v and str(v).strip())
            out.append(dict(name=n["name"], key=n["name"], searches=[q], must_show=must, search_for=k, **{"is": n.get("is", "thing")}))
    searched = {o["name"] for o in out}
    return out + [n for n in named if n.get("name") not in searched]


def given_footage(ctx, out):
    """Footage the engine already found for this script (--args '{"footage_from": ["<footage folder>", ...]}', each a
    worker/footage record with its clips): merged per line, the first folder's clips first, so a re-make does not
    fetch the same sources again. Every clip keeps its source, channel and time range for the credits."""
    srcs = [p for p in ctx.args.get("footage_from") or [] if os.path.exists(os.path.join(p, "footage.json"))]
    if not srcs or os.path.exists(os.path.join(out, "footage.json")):
        return
    import shutil
    os.makedirs(os.path.join(out, "clips"), exist_ok=True)
    recs = [json.load(open(os.path.join(p, "footage.json"))) for p in srcs]
    lines = {}
    for p, r in zip(srcs, recs):
        for ln in r.get("lines") or []:
            row = lines.setdefault(int(ln["i"]), dict(i=int(ln["i"]), say=ln.get("say"), show=ln.get("show"), dur=ln.get("dur"), want=ln.get("want"), clips=[]))
            for c in ln.get("clips") or []:
                dst = os.path.join(out, c["file"])
                if os.path.exists(os.path.join(p, c["file"])) and not os.path.exists(dst):
                    shutil.copyfile(os.path.join(p, c["file"]), dst)
                    row["clips"].append(c)
    rows = [lines[k] for k in sorted(lines)]
    # the real voices come with their clips (a remake that dropped them had none to cut in): each voice's sound and
    # picture copied beside the clips, the first record's voice for a line first
    voices, after = [], set()
    for p, r in zip(srcs, recs):
        for v in r.get("voices") or []:
            if v.get("after") in after or not v.get("wav") or not os.path.exists(os.path.join(p, v["wav"])):
                continue
            for k in ("wav", "file"):
                if v.get(k) and os.path.exists(os.path.join(p, v[k])):
                    dst = os.path.join(out, v[k])
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    if not os.path.exists(dst):
                        shutil.copyfile(os.path.join(p, v[k]), dst)
            voices.append(v)
            after.add(v.get("after"))
    merged = dict(recs[0], lines=rows, merged_from=srcs, voices=voices,
                  counts=dict(lines=len(rows), lines_with_footage=sum(1 for r in rows if r["clips"]), clips=sum(len(r["clips"]) for r in rows)),
                  credits=[dict(line=r["i"], url=c["source"]["url"], channel=c["source"].get("channel"), title=c["source"].get("title"),
                                time_range=c.get("time_range")) for r in rows for c in r["clips"]]
                          + [dict(line=v.get("after"), url=(v.get("source") or {}).get("url"), channel=(v.get("source") or {}).get("channel"),
                                  title=(v.get("source") or {}).get("title"), time_range=v.get("time_range"), voice=v.get("person")) for v in voices],
                  spend=dict(usd=round(sum(float((r.get("spend") or {}).get("usd") or 0) for r in recs), 4)))
    json.dump(merged, open(os.path.join(out, "footage.json"), "w"), ensure_ascii=False, indent=1)
    ctx.log(f"footage reused: {merged['counts']['clips']} clips on {merged['counts']['lines_with_footage']} lines from {len(srcs)} records")


def clip_facts(c):
    """What the pacing driver and the look need of a clip: its size, a still from its middle, the faces on it."""
    import subprocess
    try:
        r = subprocess.run([PX.FP, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,nb_frames,r_frame_rate", "-of", "json",
                            c["path"]], capture_output=True, text=True, timeout=60)
        st = json.loads(r.stdout)["streams"][0]
        c["w"], c["h"] = int(st["width"]), int(st["height"])
        num, den = (st.get("r_frame_rate") or "30/1").split("/")
        c["fps"] = float(num) / max(float(den), 1.0)
        if st.get("nb_frames"):
            c["seconds"] = round(min(float(c.get("seconds") or 1e9), int(st["nb_frames"]) / c["fps"]), 3)
        still = c["path"][:-4] + ".jpg"
        if not os.path.exists(still):
            subprocess.run([PX.FF, "-v", "error", "-y", "-ss", f"{float(c['seconds']) / 2:.3f}", "-i", c["path"], "-frames:v", "1", "-q:v", "3", still],
                           check=True, timeout=60)
        c["still"] = still
        c["faces"] = PX.faces(still)
    except Exception:  # noqa: BLE001 - a clip without facts still plays: the driver frames it whole
        c.setdefault("faces", [])
    return c


def supply(ctx, script):
    sc = script["script"]
    sid = script["style"]["id"]
    sup_dir = ctx.supply
    os.makedirs(sup_dir, exist_ok=True)
    tempfile.tempdir = ctx.tmp
    os.environ["TMPDIR"] = ctx.tmp
    pk = os.path.join(sup_dir, "pack.json")
    if os.path.exists(pk):
        return json.load(open(pk))["record"]
    spend = []
    band = writer.FORMS[FORM]["band"]
    env = dict(os.environ, TMPDIR=ctx.tmp)
    given_narration(ctx, sc, os.path.join(sup_dir, "narration"))
    # the stories read (voice.py): Phonon Easton, whole sentences a generation, takes chosen for range and restarts,
    # tight joins, focus words forward, a loudness range like the references'
    nar = PX.narrate(writer.narration_script(sc, band), os.path.join(sup_dir, "narration"), band=band, label=f"wf3-narrate-{ctx.run_id}", env=env,
                     reader=os.path.join(HERE, "voice.py"))
    ctx.log(f"narration {nar['duration']} s, gate {nar['gate']}")
    pack = dict(sid=ctx.sid, words=nar["words"], narration=nar["wav"], ffmpeg=PX.FF, sounds=PX.sound_facts(), pictures={}, credits=[],
                work=os.path.join(sup_dir, "finish"))
    used = set()
    for s in sc["segments"]:
        used.update(s.get("pictures") or [])
        if (s.get("window") or {}).get("subject"):
            used.add(s["window"]["subject"])
    named = [n for n in sc.get("named") or [] if n.get("name") in used]
    if sid != "one-photograph" and named:
        subj = photo_searches(sc, named) if sid in R.STYLES else named
        got, reports, usd = PX.pictures(subj, os.path.join(sup_dir, "pics"), per=3 if sid in R.STYLES else 4 if sid == "character-sheet" else 2) \
            if subj else ({}, [], 0.0)
        B = PX._worker()          # the check's own model (gpt-6-sol on the OpenAI rung since 13 Nov; its fallback is named when it ran)
        spend.append(dict(stage="supply", provider="openai" if str(B.NAMED_CHECK_MODEL).startswith("gpt") else "anthropic",
                          model=B.NAMED_CHECK_MODEL, what="picture check", usd=usd))
        for name, recs in got.items():
            for r in recs:
                try:
                    r["faces"] = PX.faces(r["path"])
                except Exception:  # noqa: BLE001
                    r["faces"] = []
        pack["pictures"] = {k: v for k, v in got.items() if v}
        pack["picture_reports"] = reports
        pack["credits"] = [dict(name=r["name"], title=r.get("title"), licence=r.get("licence"), credit=r.get("credit"), page=r.get("page"))
                           for v in got.values() for r in v]
        searched = {x.get("key") or x["name"] for x in subj}
        missing = [n["name"] for n in named if n["name"] in searched and not got.get(n["name"])]
        if len(pack["pictures"]) < min(2, len(searched)):
            raise RuntimeError(f"only {len(pack['pictures'])} named things have free pictures; missing {missing}")
        pack["missing"] = missing
    if sid == "one-photograph":
        ph = PX.commons_file(sc["photo"]["file"], os.path.join(sup_dir, "photo"))
        ph["faces"] = PX.faces(ph["path"], score=0.55)       # people in profile find none: the places the camera goes still do
        targets, why, use = photo_targets(ctx, ph, sc)
        spend.append(dict(stage="supply", **use))
        ph["targets"], ph["targets_why"] = targets, why
        if len(ph["faces"]) < 2 and sum(1 for t in targets.values() if t) < 2:
            raise RuntimeError(f"the photograph has {len(ph['faces'])} faces and {sum(1 for t in targets.values() if t)} placed targets")
        pack["photo"] = ph
        pack["credits"] = [dict(name=ph["title"], title=ph["title"], licence=ph["licence"], credit=ph.get("credit"), page=ph.get("page"))]
        pack["microtext"] = portrait_of(ctx, ph, sc, sup_dir)
    words_all = json.load(open(nar["words"]))["words"]
    anchors = {k: [r["path"] for r in v if r.get("path")] for k, v in (pack.get("pictures") or {}).items()}
    given_footage(ctx, os.path.join(sup_dir, "footage"))
    durs = chain.seg_durs(words_all, len(sc["segments"]))
    reel_kw = dict(preserve_source=True, shot_s=REEL_SHOT_S, line_max=REEL_LINE_MAX, per=REEL_PER) if sid in R.STYLES else {}
    # a story reel's first call already searches each line's named moment (moment_script) and the people's own voices
    first = moment_script(script) if sid in R.STYLES else script
    foot = chain.footage(ctx, first, durs, anchors=anchors, **footage_kw(reel_kw, want_voices=True))     # real video per line
    if foot:
        tag_things(foot, first)
    if foot:
        fdir = os.path.join(sup_dir, "footage")
        pack["footage"] = [dict(i=ln["i"], clips=[clip_facts(dict(c, path=os.path.join(fdir, c["file"]))) for c in ln["clips"]]) for ln in foot["lines"]]
        pack["voices_tried"] = foot.get("voices_tried")
        pack["credits"] = pack["credits"] + [dict(name=c["title"], title=c["title"], licence="footage, not licensed (owner's decision)",
                                                  credit=c["channel"], page=c["url"], time_range=c["time_range"], line=c["line"])
                                             for c in foot["credits"]]
        spend.append(dict(stage="supply", provider="openai", model="gpt-6-sol low + gpt-4o-transcribe", what="footage",
                          usd=foot["spend"]["usd"]))
        ctx.log(f"footage: {foot['counts']['clips']} clips on {foot['counts']['lines_with_footage']} of {foot['counts']['lines']} lines")
        # a person speaking on camera never plays under the narrator: measured on each clip's own frames (reel.talking)
        talk = {}
        for ln in pack["footage"]:
            for c in ln["clips"]:
                t, score, still = R.talking(c, PX.FF, windows=True)
                talk[c["path"]] = dict(talking=t, score=score, still=still)
        pack["talking"] = talk
        ctx.log(f"talking on camera: {sum(1 for v in talk.values() if v['talking'])} of {len(talk)} clips kept from under the narrator")
    if foot and foot.get("voices"):
        # the person the film is about, in their own voice, after the line it belongs to (footage voice.py finds and cuts
        # it; narrate realvoice.py puts it into the narration; the film pins its picture over those seconds)
        try:
            import subprocess
            r = subprocess.run([PX.NARRATE_PY, PX.NARRATE, "splice", os.path.join(sup_dir, "narration"), os.path.join(sup_dir, "footage")],
                               cwd=os.path.dirname(PX.NARRATE), capture_output=True, text=True, timeout=600, env=env)
            placed = json.loads(r.stdout.strip().splitlines()[-1]).get("voices") if r.returncode == 0 and r.stdout.strip() else []
            if placed:
                try:        # each real voice at the narrator's own loudness (K-weighted, within 1 LU)
                    json.dump(placed, open(os.path.join(sup_dir, "narration", "footage_voices.json"), "w"))
                    lv = subprocess.run([PX.NARRATE_PY, os.path.join(HERE, "voice.py"), "level-voices", os.path.join(sup_dir, "narration")],
                                        capture_output=True, text=True, timeout=600, env=env)
                    ctx.log(f"real voices levelled: {(lv.stdout or lv.stderr).strip().splitlines()[-1][:300] if (lv.stdout or lv.stderr).strip() else lv.returncode}")
                except Exception as e:  # noqa: BLE001 the voices as spliced
                    ctx.log(f"real voices not levelled: {e!r}"[:200])
                pack["voices"] = [dict(p, clip=clip_facts(dict(path=p["file"], file=os.path.basename(p["file"]), subject=p["person"],
                                                               seconds=round(p["end"] - p["start"], 3))) if p.get("file") else None)
                                  for p in placed]
                wj = json.load(open(nar["words"]))
                words_all, nar["duration"] = wj["words"], wj["duration"]
                ctx.log("real voices: " + "; ".join(f"{p['person']} after line {p['after']} ({p['end'] - p['start']:.1f} s)" for p in placed))
            elif r.returncode:
                ctx.log(f"real voices not spliced: {(r.stderr or '')[-300:]}")
        except Exception as e:  # noqa: BLE001 the narrator alone, as before
            ctx.log(f"real voices: {e!r}"[:300])
    if sid in R.STYLES and ctx.args.get("fit", True) is not False:
        fit_supply(ctx, script, pack, durs, spend, anchors, reel_kw)
    elif sid in R.STYLES:
        pack.setdefault("defects", []).append("fit skipped by --args fit:false: the cut has no per-line picture verdicts")
    if sid == "whole-page":
        w = words_all
        loud, pitch = F.voice_measures(nar["wav"], w, PX.FF)
        pack["loudness"], pack["pitch"] = loud, pitch
    rec = dict(narration_s=nar["duration"], voice=nar.get("voice"), gate=nar["gate"], pictures=sum(len(v) for v in pack["pictures"].values()),
               subjects=len(pack["pictures"]), missing=pack.get("missing"), spend=spend,
               footage=(dict(clips=foot["counts"]["clips"], lines=foot["counts"]["lines_with_footage"], credits=foot["credits"]) if foot else None))
    json.dump(dict(pack=pack, record=rec), open(pk, "w"), ensure_ascii=False, indent=1)
    return rec


def measure_clips(pack):
    """Each clip's content motion, its inner cuts (reel.clip_motion: signals), its take group (reel.take_groups) and each
    take's colourfulness (Hasler-Suesstrunk on the take's middle frame, take_chroma: under 8 reads as black and white);
    each photograph's colourfulness. Signals only, no model."""
    clips = [c for ln in pack.get("footage") or [] for c in ln["clips"]]
    work = tempfile.mkdtemp(prefix="chroma-")
    for c in clips:
        if "motion" not in c:
            m = R.clip_motion(c["path"], PX.FF, landscape=int(c.get("w") or 0) > int(c.get("h") or 1)) or {}
            c["motion"] = dict(p90=m.get("p90", 0.0), curve=m.get("curve") or [], global_px=m.get("global_px", 0.0))
            c["cuts"] = m.get("cuts") or []
        if "take_chroma" not in c:
            vals = []
            for k, (t0, t1) in enumerate(FIT.takes_of(dict(c, kind="clip"))):
                try:
                    jpg = FIT._frame(c["path"], (t0 + t1) / 2, os.path.join(work, f"c{abs(hash(c['path'])) % 10 ** 8}_{k}.jpg"), 270)
                    vals.append(round(colourfulness(jpg)[0], 1))
                except Exception:  # noqa: BLE001 - a take that cannot be read is not counted as grey
                    vals.append(None)
            c["take_chroma"] = vals
    for c in clips:
        if "texture" not in c and c.get("still") and os.path.exists(str(c.get("still"))):
            try:
                c["texture"] = round(R.texture(c["still"]), 2)      # a signal: a flat take is played as a driven card
            except Exception:  # noqa: BLE001
                c["texture"] = None
    for recs in (pack.get("pictures") or {}).values():
        for r in recs:
            if "chroma" not in r:
                try:
                    r["chroma"] = round(colourfulness(FIT._frame(r["path"], None, os.path.join(work, "p.jpg"), 270))[0], 1)
                except Exception:  # noqa: BLE001
                    r["chroma"] = None
    import shutil
    shutil.rmtree(work, ignore_errors=True)
    groups = R.take_groups(clips, PX.FF)            # across all sources, before fit_report: the report and the cut count the same groups
    for c in clips:
        c["take_group"] = groups.get(c["path"], c["path"])


DEMAND_K = 1.25           # a line's demand: this many distinct fitting takes for each shot the cut will draw on it
MOVING_SHARE = 0.65       # of a line's demand that must be moving takes (the judge: 69% of shots are moving video)
TOPUP_CALLS = 4           # top-ups continue while the summed deficit falls (kept sources make them cheap)
MOMENT_GRADE = 4          # a line whose brief names a moment needs a fitting take of this grade or more
GLIMPSE_MAX_F = 3         # a planned shot this short is a glimpse of the incoming take: it asks for no take of its own
JUDGE_MEDIAN_S = 0.49     # the judge's median shot: the demand without the narration's words (a test's pack)


def _grade_of(pack, path, ti, line):
    """The verdict's grade for (picture, take, line) from pack["fit_verdicts"] (0 when there is none)."""
    row = ((pack.get("fit_verdicts") or {}).get(path) or {})
    lv = row.get(ti, row.get(str(ti))) or {}
    v = lv.get(line, lv.get(str(line))) if isinstance(lv, dict) else None
    try:
        return int((v or {}).get("grade") or 0)
    except (TypeError, ValueError):
        return 0


def _units(pack):
    """{unit: dict(lines, moving, kind, grades)}: every distinct fitting take group of the supply (a clip's take by its
    take group and take index, a photograph by its path), with the lines its verdicts carry, whether its played span
    moves and its best verdict grade on each of those lines."""
    clips = {c["path"]: c for ln in pack.get("footage") or [] for c in ln["clips"]}
    units = {}
    for p, row in (pack.get("fit") or {}).items():
        c = clips.get(p)
        rows = row.items() if isinstance(row, dict) else [(0, row)]
        for ti, lines in rows:
            if not lines:
                continue
            ti = int(ti)
            if c is not None:
                takes = FIT.takes_of(dict(c, kind="clip"))
                take = takes[ti] if ti < len(takes) else (0.0, float(c.get("seconds") or 0))
                u = ("clip", c.get("take_group") or p, ti)
                mv = R.take_motion(c, take) >= R.MOTION_FLOOR
            else:
                u, mv = ("photo", p, 0), False
            row_u = units.setdefault(u, dict(lines=set(), moving=mv, kind=u[0], grades={}))
            row_u["lines"] |= {int(x) for x in lines}
            row_u["moving"] = row_u["moving"] or mv
            for x in lines:
                row_u["grades"][int(x)] = max(row_u["grades"].get(int(x), 0), _grade_of(pack, p, ti, int(x)))
    return units


def match_units(units, demand):
    """{line: [units]}: a one-to-one matching of distinct take groups to lines, each line up to its demand (Kuhn's
    augmenting paths over the lines' slots), so a take that fits three lines counts once. Moving takes are tried first."""
    slot_of = {}
    lines_of = {u: sorted(set(units[u]["lines"]) & set(demand)) for u in units}

    def place(u, seen):
        for ln in lines_of[u]:
            for k in range(demand[ln]):
                sl = (ln, k)
                if sl in seen:
                    continue
                seen.add(sl)
                if sl not in slot_of or place(slot_of[sl], seen):
                    slot_of[sl] = u
                    return True
        return False
    for u in sorted(units, key=lambda u: (not units[u]["moving"], len(lines_of[u]), str(u))):
        place(u, set())
    out = {i: [] for i in demand}
    for (ln, _), u in sorted(slot_of.items(), key=lambda q: (q[0][0], q[0][1])):
        out[ln].append(u)
    return out


def line_demand(pack, sc, durs, seed=1114, tail_f=15):
    """{line: distinct fitting takes the cut needs}: ceil(DEMAND_K x (the shots reel.shot_plan draws on the line, on the
    cut's own Clock, f_end and seed, less its planned glimpses of GLIMPSE_MAX_F frames or fewer)). Without the
    narration's words (a pack under test), the line's seconds at the judge's median shot."""
    n = len(sc["segments"])
    try:
        clock = F.Clock(pack["words"], sc)
        f_end = F.Clock.f(clock.dur) + int(tail_f)
        plan = R.shot_plan(sc, clock, f_end, seed)
        return {i: max(1, int(math.ceil(DEMAND_K * sum(1 for x in plan.get(i) or [] if x > GLIMPSE_MAX_F)))) for i in range(n)}
    except Exception:  # noqa: BLE001 - no words to plan on
        return {i: max(1, int(math.ceil(DEMAND_K * float(durs[i] if i < len(durs) and durs[i] else 1.0) / JUDGE_MEDIAN_S))) for i in range(n)}


def still_only_line(seg):
    """Every brief item of the line is still by nature (writer.annotate's still_only): no motion is asked of its supply."""
    see = [x for x in seg.get("see") or [] if isinstance(x, dict)]
    return bool(see) and all(x.get("still_only") is True for x in see)


def moment_line(seg):
    return any(isinstance(x, dict) and x.get("moment") for x in seg.get("see") or [])


def fit_report(pack, sc, durs):
    """Per line: demand (line_demand: from the cut's own shot plan), have (distinct fitting take groups matched one to
    one to lines, match_units), moving (matched takes whose played span moves), moment_ok (a line whose brief names a
    moment has a fitting take of MOMENT_GRADE or more) and short (have under demand, moving under MOVING_SHARE of demand
    unless every brief item is still_only, the moment not yet found, or a person in its brief with no moving take)."""
    units = _units(pack)
    n = len(sc["segments"])
    demand = line_demand(pack, sc, durs)
    slots = match_units(units, demand)
    people = {x.get("name") for x in sc.get("named") or [] if str(x.get("is") or "").lower() == "person"}
    rows = []
    for i in range(n):
        seg = sc["segments"][i]
        have = slots.get(i) or []
        mov = sum(1 for u in have if units[u]["moving"])
        still_ok = still_only_line(seg)
        named_people = {x.get("thing") for x in seg.get("see") or []} & people
        person_still = bool(named_people) and not still_ok and \
            not any(units[u]["moving"] and i in units[u]["lines"] for u in units if units[u]["kind"] == "clip")
        moment_ok = None
        if moment_line(seg):
            moment_ok = any(i in units[u]["lines"] and units[u]["grades"].get(i, 0) >= MOMENT_GRADE for u in units)
        mov_short = 0 if still_ok else max(int(math.ceil(MOVING_SHARE * demand[i])) - mov, 0)
        short = len(have) < demand[i] or mov_short > 0 or person_still or moment_ok is False
        rows.append(dict(i=i, need=demand[i], have=len(have), moving=mov, deficit=max(demand[i] - len(have), 0) + mov_short +
                         (1 if moment_ok is False else 0), person_without_moving=person_still, moment_ok=moment_ok, still_only=still_ok, short=short))
    return rows


def moment_cover(rows):
    """The share of moment lines with a fitting take of MOMENT_GRADE or more (None when no line names a moment)."""
    m = [r for r in rows if r.get("moment_ok") is not None]
    return round(sum(1 for r in m if r["moment_ok"]) / len(m), 3) if m else None


def topup_script(script, short, moments_only=False):
    """The script a top-up footage call reads: each short line's pictures are its brief's moments, named with
    writer.annotate's own moment and as_of strings, so the engine's per-subject searches become per-moment searches in
    the version the line means (no change to the engine's planner). moments_only: only the brief items that name a
    moment are expanded (the first call's moment_script); the line keeps its other pictures."""
    sc = json.loads(json.dumps(script["script"]))
    named = {n.get("name"): n for n in sc.get("named") or []}
    extra = []
    for i in short:
        s_ = sc["segments"][i]
        pics = []
        for x in s_.get("see") or []:
            if moments_only and not x.get("moment"):
                continue
            base = named.get(x.get("thing")) or {}
            nm = x.get("moment") or f"{x.get('thing')} ({x.get('as_of')})"
            pics.append(nm)
            if nm not in named and nm not in [e["name"] for e in extra]:
                extra.append(dict(name=nm, **{"is": base.get("is") or "thing"}, must_show=x.get("must_show") or base.get("must_show"),
                                  searches=[q for q in (x.get("moment"), f"{x.get('thing')} {x.get('as_of')}", x.get("search")) if q][:3],
                                  thing=x.get("thing"), as_of=x.get("as_of")))
        s_["pictures"] = pics + [p for p in s_.get("pictures") or [] if p not in pics]
    sc["named"] = (sc.get("named") or []) + extra
    return dict(script, script=sc)


def moment_script(script):
    """The first footage call's script: topup_script's per-moment expansion on every line whose brief names a moment (so
    the finished film's own scene is searched for from the first call, not only in a top-up), and the voice subjects
    (voice_subjects)."""
    sc = script["script"]
    lines = [i for i, s_ in enumerate(sc["segments"]) if any(isinstance(x, dict) and x.get("moment") for x in s_.get("see") or [])]
    out = topup_script(script, lines, moments_only=True) if lines else json.loads(json.dumps(script))
    return voice_subjects(out)


def voice_subjects(script):
    """Explicit searches for real voices, added to the named list (plain joins of the writer's own fields): each named
    person the film shows, "<name> interview <as_of>" (the story time the brief gives that person first); the film's
    first thing that exists faithfully only in its own footage (true_pictures "footage"), else the first that has
    footage at all, "<name> trailer". Each row carries thing (the named thing it is of), so its clips are judged for
    every line that names that thing (fit.judged_lines)."""
    sc = script["script"]
    shown = {x.get("thing") for s_ in sc["segments"] for x in s_.get("see") or [] if isinstance(x, dict)}
    as_of = {}
    for s_ in sc["segments"]:
        for x in s_.get("see") or []:
            if isinstance(x, dict) and x.get("thing") and x.get("as_of"):
                as_of.setdefault(x["thing"], str(x["as_of"]))
    named = [n for n in sc.get("named") or [] if n.get("name") in shown and not n.get("thing")]
    have = {n.get("name") for n in sc.get("named") or []}
    extra = []
    for n in named:
        if str(n.get("is") or "").lower() != "person":
            continue
        q = " ".join(x for x in (n["name"], "interview", as_of.get(n["name"], "")) if x).strip()
        nm = f"{n['name']} interview"
        if nm not in have:
            extra.append(dict(name=nm, **{"is": "person"}, must_show=f"{n['name']} speaking on camera", thing=n["name"],
                              as_of=as_of.get(n["name"]), video_searches=[q]))
    works = [n for n in named if str(n.get("is") or "").lower() not in ("person", "place")]
    work = next((n for n in works if n.get("true_pictures") == "footage"), None) or next((n for n in works if n.get("true_pictures") == "both"), None)
    if work and f"{work['name']} trailer" not in have:
        extra.append(dict(name=f"{work['name']} trailer", **{"is": work.get("is") or "thing"}, must_show=work.get("must_show") or work["name"],
                          thing=work["name"], as_of=as_of.get(work["name"]), video_searches=[f"{work['name']} trailer"]))
    sc = dict(sc, named=list(sc.get("named") or []) + extra)
    return dict(script, script=sc)


def tag_things(foot, script):
    """Each clip found for a moment or voice subject carries the named thing that subject is of (c["thing"]), so a
    window-fall clip found for one line is judged for every line that names that thing (fit.judged_lines)."""
    thing_of = {n.get("name"): n.get("thing") for n in (script.get("script") or {}).get("named") or [] if n.get("thing")}
    for ln in foot.get("lines") or []:
        for c in ln.get("clips") or []:
            if c.get("subject") in thing_of and not c.get("thing"):
                c["thing"] = thing_of[c["subject"]]
    return foot


FOOTAGE_FLAGS = dict(strict_lines=True, motion_first=True, alloc="round", reassign_lost=True, verify=False, whole_first=True, keep_sources=True)


def footage_kw(reel_kw, **more):
    """The footage engine's opt-in flags for a story reel (worker/footage: strict lines, motion first, round-robin
    allocation, lost cuts replaced, the engine's verify pass off because every take is judged per line by fit, each
    short source fetched once whole and kept for the film's later calls), passed only when this shell's chain.footage
    takes them. more: a call's own extra flags (want_voices), on the same terms."""
    import inspect
    try:
        ok = set(inspect.signature(chain.footage).parameters)
    except (TypeError, ValueError):
        ok = set()
    return dict(reel_kw, **{k: v for k, v in dict(FOOTAGE_FLAGS, **more).items() if k in ok}) if reel_kw else {}


def judge_supply(ctx, sc, pack):
    """Every take of the supply identified once and judged per (take, line) (fit.verdicts): pack["fit"], ["fit_verdicts"]
    and ["identity"]. Returns (usd, calls)."""
    table, u, c, ids = FIT.verdicts(FIT.candidates(pack), sc, cache_path=os.path.join(ctx.supply, "fit.json"), work=os.path.join(ctx.tmp, "fit"),
                                    log=ctx.log)
    pack["fit"], pack["fit_verdicts"], pack["identity"] = FIT.fits(table), table, ids
    return u, c


def topup_once(ctx, script, pack, durs, short, spend, name, anchors, reel_kw, line_max=None):
    """One footage top-up for the short lines (topup_script: per-moment searches), never cutting again what an earlier
    record holds; merged per line, measured and judged. line_max: the most clips a line asks for (the largest real
    deficit). Returns the clips it added (0 when the engine did not run)."""
    if line_max:
        reel_kw = dict(reel_kw, line_max=max(int(line_max), 1))
    records = [os.path.join(ctx.supply, n, "footage.json") for n in sorted(os.listdir(ctx.supply))
               if n.startswith("footage") and os.path.exists(os.path.join(ctx.supply, n, "footage.json"))]
    tscript = topup_script(script, short)
    foot2 = chain.footage(ctx, tscript, durs, anchors=anchors, out_name=name, lines=short, avoid=records, **footage_kw(reel_kw))
    if not foot2:
        return 0
    tag_things(foot2, tscript)
    fdir = os.path.join(ctx.supply, name)
    by_i = {int(ln["i"]): ln for ln in pack.get("footage") or []}
    added = 0
    for ln in foot2.get("lines") or []:
        row = by_i.setdefault(int(ln["i"]), dict(i=int(ln["i"]), clips=[]))
        for c in ln.get("clips") or []:
            row["clips"].append(clip_facts(dict(c, path=os.path.join(fdir, c["file"]))))
            added += 1
    pack["footage"] = [by_i[q] for q in sorted(by_i)]
    pack["credits"] = (pack.get("credits") or []) + [dict(name=c["title"], title=c["title"], licence="footage, not licensed (owner's decision)",
                                                          credit=c["channel"], page=c["url"], time_range=c["time_range"], line=c["line"])
                                                     for c in foot2.get("credits") or []]
    spend.append(dict(stage="supply", provider="openai", model="gpt-6-sol low + gpt-4o-transcribe", what=f"footage top-up ({name})",
                      usd=(foot2.get("spend") or {}).get("usd") or 0))
    for ln in pack["footage"]:
        for c in ln["clips"]:
            if c["path"] not in (pack.get("talking") or {}):
                t, score, still = R.talking(c, PX.FF, windows=True)
                pack.setdefault("talking", {})[c["path"]] = dict(talking=t, score=score, still=still)
    measure_clips(pack)
    u, calls = judge_supply(ctx, script["script"], pack)
    spend.append(dict(stage="supply", provider="openai", model=FIT.MODEL, what=f"picture identity and check ({name})", usd=round(u, 4), calls=calls))
    return added


def fit_supply(ctx, script, pack, durs, spend, anchors, reel_kw):
    """Every picture of the supply identified once per take and judged against the lines it may carry (judge_supply,
    OpenAI vision): pack["fit"] is what the cutter may put on each line. Lines whose supply is short of the cut's demand
    (fit_report) get up to TOPUP_CALLS top-up footage calls written per short line (topup_script), until the summed
    deficit is 0. A story reel without a brief on every line stops here: the cut is never made without per-line
    verdicts."""
    sc = script["script"]
    if not all(s_.get("see") for s_ in sc["segments"]):
        raise RuntimeError("fit: the script has no picture brief on segments "
                           f"{[k for k, s_ in enumerate(sc['segments']) if not s_.get('see')]}; a story reel is never cut without per-line verdicts")
    measure_clips(pack)
    usd, calls = judge_supply(ctx, sc, pack)
    spend.append(dict(stage="supply", provider="openai", model=FIT.MODEL, what="picture identity and check", usd=round(usd, 4), calls=calls))
    rows = fit_report(pack, sc, durs)
    topups = []
    for k in range(TOPUP_CALLS):
        short = [r["i"] for r in rows if r["short"]]
        if not short or "footage" not in pack or os.environ.get("VALMERA_FOOTAGE", "1") == "0":
            break
        before = sum(r["deficit"] for r in rows)
        ask_max = max([r["deficit"] for r in rows if r["short"]] + [1])
        added = topup_once(ctx, script, pack, durs, short, spend, f"footage{k + 2}", anchors, reel_kw, line_max=ask_max)
        rows = fit_report(pack, sc, durs)
        after = sum(r["deficit"] for r in rows)
        topups.append(dict(lines=short, line_max=ask_max, clips=added, deficit_before=before, deficit_after=after))
        if added == 0 or after == 0 or after >= before:           # continue only while the summed deficit falls
            break
    photos = [r["path"] for v in (pack.get("pictures") or {}).values() for r in v]
    usable_photos = [p for p in photos if FIT.lines_fit((pack.get("fit") or {}).get(p))]
    per_thing = {}
    for name, recs in (pack.get("pictures") or {}).items():
        ps = [r["path"] for r in recs]
        ok_ = [p for p in ps if FIT.lines_fit((pack.get("fit") or {}).get(p))]
        per_thing[name] = dict(downloaded=len(ps), usable=len(ok_), usable_share=round(len(ok_) / max(len(ps), 1), 3))
    n_units = len(_units(pack))
    pack["fit_report"] = dict(lines=rows, topup=dict(calls=topups, still_short=[r["i"] for r in rows if r["short"]]) if topups else None,
                              deficit=sum(r["deficit"] for r in rows), fitting_take_groups=n_units, moment_cover=moment_cover(rows),
                              photos=dict(downloaded=len(photos), usable=len(usable_photos),
                                          usable_share=round(len(usable_photos) / max(len(photos), 1), 3), per_thing=per_thing))
    ctx.log(f"fit: {n_units} distinct fitting take groups; lines short {[r['i'] for r in rows if r['short']]}; "
            f"deficit {pack['fit_report']['deficit']}; photos usable {len(usable_photos)} of {len(photos)}"
            + (f"; top-ups {topups}" if topups else ""))


def supply_evidence(ctx, pack):
    """What the supply was, kept in the delivered record without media: each footage record's summary (and what look
    passed), the fit table, the identities and the fit report, so a lost picture can be traced."""
    recs = []
    for n in sorted(os.listdir(ctx.supply)) if os.path.isdir(ctx.supply) else []:
        fj = os.path.join(ctx.supply, n, "footage.json")
        if not n.startswith("footage") or not os.path.exists(fj):
            continue
        try:
            r = json.load(open(fj))
        except ValueError:
            continue
        lk = os.path.join(ctx.supply, n, "looked.json")
        looked = None
        if os.path.exists(lk):
            try:
                rows = json.load(open(lk))
                looked = dict(frames=len(rows), passed=sum(1 for x in rows if x.get("ok") and x.get("clean") and (x.get("grade") or 0) >= 3))
            except (ValueError, TypeError, AttributeError):
                looked = None
        recs.append(dict(record=n, counts=r.get("counts"), sources=[{k: s.get(k) for k in ("url", "title", "error", "rung")} for s in r.get("sources") or []][:40],
                         lines=[dict(i=ln.get("i"), want=ln.get("want"), clips=len(ln.get("clips") or []), lost=ln.get("lost"), replaced=ln.get("replaced"))
                                for ln in r.get("lines") or []], looked=looked))
    return dict(footage=recs, fit=pack.get("fit"), identity=pack.get("identity"), fit_report=pack.get("fit_report"),
                moment_cover=(pack.get("fit_report") or {}).get("moment_cover"),
                sources_opened_share=_opened_share(recs))


def _opened_share(recs):
    """sources opened / sources tried over the film's footage records (the first call's and its top-ups')."""
    tried = sum(int(((r.get("counts") or {}).get("sources_tried")) or 0) for r in recs)
    opened = sum(int(((r.get("counts") or {}).get("sources_opened")) or 0) for r in recs)
    return round(opened / tried, 3) if tried else None


def load_pack(ctx):
    return json.load(open(os.path.join(ctx.supply, "pack.json")))["pack"]


# ----------------------------------------------------------------- score, render, check

def direction_of(ctx):
    d = dict(ctx.args.get("direction") or {})
    p = os.path.join(ctx.run_dir, "direction.json")
    if os.path.exists(p):
        d.update(json.load(open(p)))
    return d


def env_path():
    return F.scene_worker() + os.pathsep + os.path.join(chain.SLICE, "pylib")


def score(ctx, script):
    pack = load_pack(ctx)
    d = direction_of(ctx)
    sc = json.loads(json.dumps(script["script"]))
    for k, v in (d.get("script") or {}).items():          # the director's hand on the script's data; facts are checked again
        sc[k] = v
    if script["style"]["id"] == "one-photograph":
        pk = os.path.join(ctx.supply, "pack.json")
        whole = json.load(open(pk))
        changed = False
        if "targets" not in pack["photo"] or ctx.args.get("retarget") or d.get("retarget"):
            targets, why, use = photo_targets(ctx, pack["photo"], sc)
            pack["photo"]["targets"], pack["photo"]["targets_why"] = targets, why
            whole["record"].setdefault("spend", []).append(dict(stage="score", **use))
            changed = True
        for n, t in (d.get("targets") or {}).items():       # the director places what the model could not
            pack["photo"]["targets"][n] = t
            changed = True
        por = sc.get("portrait") or {}
        want = [por.get("name"), por.get("segment")]
        if changed or (pack.get("microtext") or {}).get("for") != want:
            mt = portrait_of(ctx, pack["photo"], sc, ctx.supply)
            pack["microtext"] = dict(mt, **{"for": want}) if mt else None
            changed = True
        if changed:
            whole["pack"] = pack
            json.dump(whole, open(pk, "w"), ensure_ascii=False, indent=1)
    knobs = {k: v for k, v in d.items() if k not in ("script", "why", "targets", "retarget", "alien")}
    if render_drop(ctx):                                  # the picture gate's repair: the render's failing pictures go (check)
        knobs["drop"] = list(knobs.get("drop") or []) + [x for x in render_drop(ctx) if x not in (knobs.get("drop") or [])]
    knobs.setdefault("bed", bool(ctx.args.get("song")) or chain.wants_music(ctx))       # the film gets music: the sounds are designed for a film with a bed
    reel = script["style"]["id"] in R.STYLES
    if reel and ctx.args.get("fit", True) is not False:
        knobs.setdefault("require_fit", True)          # a story reel is never cut by cut_story (reel.build raises without verdicts)
        anchors_ok(sc)
    sys.path[:0] = [p for p in env_path().split(os.pathsep) if p not in sys.path]
    os.environ["PATH"] = os.path.dirname(pack["ffmpeg"]) + os.pathsep + os.environ.get("PATH", "")
    pack.setdefault("work", os.path.join(ctx.supply, "finish"))
    build = BUILD[script["style"]["id"]]
    l1, beats = build(sc, pack, knobs)
    taste = F.taste_check(l1, beats)
    if not taste["ok"] and reel:
        # the offending device goes, never every display word or collage of the film
        l1, beats, taste = drop_offenders(ctx, l1, beats, taste)
    if reel and pack.get("fit") is not None:
        l1, beats, taste = stories_gates(ctx, sc, pack, build, knobs, l1, beats, taste, script_rec=script)
    if d.get("script"):
        rep = writer.verify(sc)
        if not rep["ok"]:
            raise RuntimeError(f"the director's change breaks the facts: {rep['numbers_unbacked']} {rep['facts_unverified']}")
    if d.get("alien"):                     # the direction's asks for the engine's alien bank (chain.alien_bank lays them in at the render)
        # the independent verdict of 14 Nov (week-1112/notes/S.md): diecut and answer never, lamp not yet; rhyme and arc only
        l1["alien"] = [a for a in d["alien"] if isinstance(a, dict) and a.get("dev") in ALIEN_ALLOWED]
    out = os.path.join(ctx.supply, "l1.json")
    json.dump(l1, open(out, "w"), ensure_ascii=False)
    l1_audio = os.path.join(ctx.supply, "l1_audio.json")       # the same scene with the narration as its spine: what the song is cut to
    aud = F.audio_scene(l1, pack)
    if script["style"]["id"] in R.STYLES:
        # the story's joints, where the bed breathes (worker/score/music.flow reads them from the scene's music_marks)
        joints = joints_of(sc, pack, beats)
        json.dump(joints, open(os.path.join(ctx.supply, "joints.json"), "w"), indent=1)
        aud["music_marks"] = dict(aud.get("music_marks") or {}, joints=joints)
    json.dump(aud, open(l1_audio, "w"), ensure_ascii=False)
    json.dump(beats, open(os.path.join(ctx.supply, "beats.json"), "w"), indent=1)
    look = os.path.join(ctx.run_dir, "look")
    if ctx.args.get("look", True) is False:
        # one command, no stop: the look is the engines' and the checks'; nothing is drawn for a director who is not there
        if not taste["ok"]:
            raise RuntimeError("the plan breaks a taste rule: " + "; ".join(taste["problems"][:6]))
        turn_s = round(beats["turn_f"] / F.FPS, 3) if beats.get("turn_f") is not None else None
        return dict(l1=out, l1_audio=l1_audio, turn_s=turn_s, beats={k: v for k, v in beats.items() if k not in ("look",)}, taste=taste,
                    frames=l1["dur_f"], items=len(l1["items"]), direction=d, spend=[])
    frames = sorted({int(f) for f in beats["look"] if 0 <= int(f) < l1["dur_f"]})
    for n in os.listdir(look) if os.path.isdir(look) else []:      # a look before this one: its stills are of another plan
        if n.endswith(".png"):
            os.remove(os.path.join(look, n))
    chain.sh(ctx, ["env", f"PYTHONPATH={env_path()}", chain.PY, "-m", "scene.run", "still", "--l1", out, "--work", ctx.supply,
                   "--frames", ",".join(str(f) for f in frames), "--dir", look], label=f"wf3-look-{ctx.run_id}", heavy=True, timeout=1800)
    import subprocess
    cols = 8
    subprocess.run([pack["ffmpeg"], "-v", "error", "-y", "-pattern_type", "glob", "-i", os.path.join(look, "f*.png"), "-vf",
                    f"scale=270:-2,tile={cols}x{-(-len(frames) // cols)}:padding=4", "-frames:v", "1", "-q:v", "3", os.path.join(look, "sheet.jpg")], check=True)
    if not taste["ok"]:
        raise RuntimeError("the plan breaks a taste rule: " + "; ".join(taste["problems"][:6]))
    if ctx.args.get("look", True) and not os.path.exists(os.path.join(ctx.run_dir, "look.ok")):
        raise RuntimeError(f"the director's look: open {look}/sheet.jpg, put changes in {ctx.run_dir}/direction.json, "
                           f"then touch {ctx.run_dir}/look.ok and resume")
    turn_s = round(beats["turn_f"] / F.FPS, 3) if beats.get("turn_f") is not None else None
    return dict(l1=out, l1_audio=l1_audio, turn_s=turn_s, beats={k: v for k, v in beats.items() if k not in ("look",)}, taste=taste,
                frames=l1["dur_f"], items=len(l1["items"]), direction=d, spend=[])


def anchors_ok(sc):
    """Every figure and name a story reel prints lands on its spoken word (writer.annotate's word_i, repaired once
    there): an item without a valid anchor stops the score stage, never placed by spelling."""
    bad = []
    for i, s_ in enumerate(sc.get("segments") or []):
        n = len(str(s_.get("say") or "").split())
        for x in s_.get("show") or []:
            if isinstance(x, dict) and x.get("role") in ("figure", "name"):
                wi = x.get("word_i")
                if not (isinstance(wi, int) and not isinstance(wi, bool) and 0 <= wi < n):
                    bad.append(f"segment {i}: {x.get('text')!r} ({x.get('role')}) has no word_i")
    if bad:
        raise RuntimeError("display items without their spoken word: " + "; ".join(bad[:8]))


def drop_offenders(ctx, l1, beats, taste):
    """A taste failure on a story reel drops the offending items only (taste_check names them): the words outside the
    safe area or under the reading floor, the sounds with nothing landing. The rest of the film's type stays."""
    words, sounds = set(taste.get("bad_words") or []), set(taste.get("bad_sounds") or [])
    if not words and not sounds:
        return l1, beats, taste
    ctx.log("taste: " + "; ".join(taste["problems"][:4]) + f"; dropped {len(words)} words and {len(sounds)} sounds")
    gone = [dict(text=it.get("t"), f=it.get("f")) for it in l1["items"] if it["id"] in words]
    l1 = dict(l1, items=[it for it in l1["items"] if it["id"] not in words], sound=[x for x in l1.get("sound") or [] if x["id"] not in sounds])
    beats = dict(beats, taste_dropped=gone)
    return l1, beats, F.taste_check(l1, beats)


JOINT_HOLE_DB = 22.0      # a joint's hole and a short real voice's dip (the judge's drops: 10-24 dB)
VOICE_LONG_S, VOICE_LONG_DB = 2.3, 12.0     # a real voice longer than this keeps the bed about 12 dB under it, not out
CLOSE_BED_DB = 20.0       # a dry close: the bed 20 dB down from the close's first word
SWELL_DB, SWELL_RISE_S = 10.0, 2.5
SWELLS_MAX = 4
HOLES_MAX = 3             # the judge's drops a reel: one to three
HOLE_RANK = ("turn", "close", "payoff", "low")
SWELL_GAP_S = 0.6         # a swell rises in a narrator gap of this length or more, never over the words


def narrator_gaps(words, min_s=SWELL_GAP_S):
    """[(a, b)]: the gaps between the narrator's words of min_s seconds or more."""
    ws = sorted(words, key=lambda w: w["t0"])
    return [(p["t1"], q["t0"]) for p, q in zip(ws, ws[1:]) if q["t0"] - p["t1"] >= min_s]


def joints_of(sc, pack, beats=None):
    """[{t, kind, last_word_t, next_first_word_t, db}] at the story's section joints (the writer's beats), at most
    HOLES_MAX, ranked turn, close, payoff, then low, on the programme's clock from the narration's own words. The hole
    starts at the first word of the previous section's last caption phrase (writer.annotate's phrase spans), so the bed
    is out for 0.8-2.3 s and comes back on the next section's first word; the close keeps the bed CLOSE_BED_DB down.
    {kind: voice, t0, t1, db} for each real voice cut in: a short one (VOICE_LONG_S or less) gets the full cut, a longer
    one keeps the bed VOICE_LONG_DB under it. {kind: swell, t, rise_s, db}: only a type page at a joint and a hole's
    exit earn one, each placed at the end of a narrator gap of SWELL_GAP_S or more near it, rising no longer than the
    gap, at most SWELLS_MAX, four seconds apart."""
    clock = F.Clock(pack["words"], sc)
    cands = []
    for i, s_ in enumerate(sc["segments"]):
        kind = s_.get("beat")
        if i == 0 or kind not in HOLE_RANK:
            continue
        prev, cur = clock.seg_words(i - 1), clock.seg_words(i)
        if not prev or not cur:
            continue
        ph = sc["segments"][i - 1].get("phrases") or []
        start = prev[-1]["t1"]
        if ph and isinstance(ph[-1], list) and len(ph[-1]) == 2 and 0 <= int(ph[-1][0]) < len(prev):
            start = prev[int(ph[-1][0])]["t0"]
        b = cur[0]["t0"]
        start = min(max(start, b - 2.3), b - 0.8)
        j = dict(t=round(b, 3), kind=kind, last_word_t=round(start, 3), next_first_word_t=round(b, 3), seg=i, db=JOINT_HOLE_DB)
        if kind == "close":
            j["bed_db"] = CLOSE_BED_DB
        cands.append(j)
    keep = sorted(cands, key=lambda j: (HOLE_RANK.index(j["kind"]), j["seg"]))[:HOLES_MAX]
    out = sorted(keep, key=lambda j: j["t"])
    holes = [j["next_first_word_t"] for j in out]
    for v in pack.get("voices") or []:
        if v and v.get("start") is not None:
            a = round(float(v["start"]) + F.LEAD_F / F.FPS, 3)
            b = round(float(v["end"]) + F.LEAD_F / F.FPS, 3)
            out.append(dict(t=a, kind="voice", t0=a, t1=b, db=JOINT_HOLE_DB if b - a <= VOICE_LONG_S else VOICE_LONG_DB))
    gaps = narrator_gaps(clock.words)
    marks = []
    for p in (beats or {}).get("pictures") or []:
        if p.get("kind") == "designed" and not str(p.get("tier") or "").startswith("reframe"):
            x = p["f"][0] / F.FPS
            if any(abs(x - j["next_first_word_t"]) <= 1.5 for j in cands):            # a type page at a joint
                near = [g for g in gaps if abs(g[1] - x) <= 1.5]
                if near:
                    marks.append(min(near, key=lambda g: abs(g[1] - x)))
    for h in holes:
        if any(abs(h - j["next_first_word_t"]) < 0.01 for j in out if j.get("kind") == "close"):
            continue                                                                # the close stays dry
        after = [g for g in gaps if h < g[1] <= h + 4.0]
        if after:
            marks.append(after[0])
    swells = []
    for a, b in sorted(set(marks), key=lambda g: g[1]):
        if len(swells) >= SWELLS_MAX or any(abs(b - y) < 4.0 for y, _ in swells) or any(0 <= h - b < 1.0 for h in holes):
            continue
        swells.append((b, min(SWELL_RISE_S, b - a)))
    out += [dict(t=round(x, 3), kind="swell", rise_s=round(r, 3), db=SWELL_DB) for x, r in swells]
    return out


AUDIT_ROUNDS = 3
BW_SHARE = (0.04, 0.35)      # black and white on this share of shots, measured on the plan; outside it the devices are halved once
REPEAT_MAX = 0.05            # repeats by distinct take
CUTS_10S = (15.5, 19.6)      # the pace gate on the plan: cuts a 10 s
LONG_SHOTS_MAX = 0.024       # shots of 2 s or more: at most this share


def planned_shots(beats, pack, clock):
    """The plan's base pictures as the audit reads them: each its SOURCE window (path, the played src, the crop), the
    words spoken under it, its source's text and its take's frozen identity."""
    meta = {}
    for c in FIT.candidates(pack):
        meta[c["path"]] = c
    ids = pack.get("identity") or {}
    out = []
    for k, p in enumerate(beats.get("pictures") or []):
        if p.get("kind") not in ("clip", "photo") or p.get("line") is None:
            continue
        c = meta.get(p["path"]) or {}
        tb = p.get("take_bounds") or ((0.0, 0.0) if p["kind"] == "photo" else None)
        out.append(dict(key=f"{os.path.basename(p['path'])}@{int(p['line'])}", path=p["path"], kind=p["kind"], line=int(p["line"]), f=p["f"],
                        src=p.get("src"), crop=p.get("crop"), seconds=c.get("seconds"), meta=c.get("meta") or {},
                        identity=ids.get(FIT.take_key(p["path"], tb)) if tb is not None else None,
                        words=FIT.words_under(clock.words, p["f"][0], p["f"][1], F.FPS)))
    return out


BLEED_MIN = 0.70             # full-frame pictures (bleed, held, pushed, cut-in) on this share of base shots at least
FULL_MODES = ("bleed", "held", "pushed", "cutin")


def bleed_share(beats):
    """(full-frame share, print + window share) of the plan's base shots, from beats.pictures' modes."""
    base = [p for p in beats.get("pictures") or [] if p.get("kind") in ("clip", "photo", "designed")]
    n = max(len(base), 1)
    return (round(sum(1 for p in base if p.get("mode") in FULL_MODES) / n, 3),
            round(sum(1 for p in base if p.get("mode") in ("print", "window")) / n, 3))


def plan_bw(beats):
    """Black and white on the plan by measure: a shot under a device finish, or whose own take reads grey (colourfulness
    under 8)."""
    base = [p for p in beats.get("pictures") or [] if p.get("kind") in ("clip", "photo", "designed")]
    bw = sum(1 for p in base if p.get("finish") in ("mono", "gate") or (p.get("chroma") is not None and float(p["chroma"]) < 8))
    return round(bw / max(len(base), 1), 3)


def pace_of(beats):
    """The plan's pace: cuts a 10 s, the share of shots of 2 s or more, the median shot, each line's shortfall."""
    pics = sorted((p for p in beats.get("pictures") or [] if p.get("kind") in ("clip", "photo", "designed")), key=lambda p: p["f"][0])
    lens = [p["f"][1] - p["f"][0] for p in pics]
    secs = max((beats.get("voice_f") or [0, 1])[1] / F.FPS, 1e-6)
    m = beats.get("measured") or {}
    short = {k: v for k, v in (m.get("shortfall_s") or {}).items() if v}
    return dict(cuts_10s=round(len(lens) / secs * 10, 2), long_share=round(sum(1 for n in lens if n >= 2 * F.FPS) / max(len(lens), 1), 3),
                median_s=round(sorted(lens)[len(lens) // 2] / F.FPS, 3) if lens else None, shortfall=short)


def stories_gates(ctx, sc, pack, build, knobs, l1, beats, taste, script_rec=None):
    """The story reel's own gates on the plan (stories only; WF2 builds on this module without them):
    audit    every planned base shot's SOURCE window judged against the words under it and its segment's brief, with
             its take's frozen identity (fit.audit, OpenAI vision); a failing shot is dropped on that line only
             ("file@line") and the plan built again, at most AUDIT_ROUNDS times; a shot still failing after them is
             replaced by its own line's fill (the cut may then use only what the audit passed there), never kept
    colour   black and white by measure (device finishes and grey takes) on BW_SHARE of shots, else the writer's
             devices lowered once
    motion   no static shot on the plan (reel.motion_of): a static shot breaks the plan
    repeats  at most REPEAT_MAX of shots repeat a take (counted by distinct take)
    figures  every figure annotate planned is placed (as display type or a designed card)
    pace     CUTS_10S cuts a 10 s, at most LONG_SHOTS_MAX of shots 2 s or more, no line short: else the short lines get a
             footage top-up (script_rec given) and one rebuild; what remains is a recorded defect"""
    clock = F.Clock(pack["words"], sc)
    drop = list(knobs.get("drop") or [])
    rounds, failed, usd = [], [], 0.0
    defects = []

    def audit_round():
        shots = planned_shots(beats, pack, clock)
        res, u = FIT.audit(shots, sc, work=os.path.join(ctx.tmp, "audit"), cache_path=os.path.join(ctx.supply, "audit.json"), log=ctx.log)
        return res, u
    if ctx.args.get("audit", True) is not False:
        bad = []
        for rnd in range(AUDIT_ROUNDS):
            res, u = audit_round()
            usd += u
            bad = [r for r in res if not r["fit"]]
            rounds.append(dict(round=rnd, shots=len(res), failed=len(bad), fail_s=round(sum((r["f"][1] - r["f"][0]) for r in bad) / F.FPS, 2)))
            if not bad:
                break
            failed += [dict(key=r["key"], why=r.get("why"), shows=r.get("shows")) for r in bad]
            drop += [r["key"] for r in bad]
            knobs = dict(knobs, drop=drop)
            l1, beats = build(sc, pack, knobs)
            taste = F.taste_check(l1, beats)
        if bad and rounds[-1]["failed"]:
            # the last round still failed: those lines get the per-moment top-up first (new takes judged for them), the
            # plan is built again and audited; only then may the cut use on them only what the audit passed there (and
            # its own fill), so no failing shot is kept
            res, u = audit_round()
            usd += u
            bad = [r for r in res if not r["fit"]]
            if bad and script_rec is not None and ctx.args.get("topup", True) is not False:
                lines_bad = sorted({int(r["line"]) for r in bad})
                try:
                    tp = topup_from_score(ctx, script_rec, pack, lines_bad)
                except Exception as e:  # noqa: BLE001 - the top-up is a repair; the fill still follows
                    ctx.log(f"audit top-up: {e!r}"[:300])
                    tp = dict(error=repr(e)[:200])
                if tp.get("clips"):
                    l1, beats = build(sc, pack, knobs)
                    taste = F.taste_check(l1, beats)
                    res, u = audit_round()
                    usd += u
                    bad = [r for r in res if not r["fit"]]
                    rounds.append(dict(round="topup", lines=lines_bad, clips=tp.get("clips"), shots=len(res), failed=len(bad),
                                       fail_s=round(sum((r["f"][1] - r["f"][0]) for r in bad) / F.FPS, 2)))
                    if bad:
                        failed += [dict(key=r["key"], why=r.get("why"), shows=r.get("shows")) for r in bad]
                        drop += [r["key"] for r in bad]
                        knobs = dict(knobs, drop=drop)
                else:
                    rounds.append(dict(round="topup", lines=lines_bad, clips=0, error=tp.get("error")))
            if bad:
                only = {}
                for r in res:
                    if r["fit"]:
                        only.setdefault(int(r["line"]), set()).add(os.path.basename(r["path"]))
                for r in bad:
                    only.setdefault(int(r["line"]), set())
                knobs = dict(knobs, drop=drop + [r["key"] for r in bad], only_ok={k: sorted(v) for k, v in only.items()})
                l1, beats = build(sc, pack, knobs)
                taste = F.taste_check(l1, beats)
                res, u = audit_round()
                usd += u
                bad = [r for r in res if not r["fit"]]
                rounds.append(dict(round="fill", shots=len(res), failed=len(bad), fail_s=round(sum((r["f"][1] - r["f"][0]) for r in bad) / F.FPS, 2)))
                if bad:
                    defects.append(f"{len(bad)} shots still fail the audit after their lines' fill")
        whole = json.load(open(os.path.join(ctx.supply, "pack.json")))
        whole["record"].setdefault("spend", []).append(dict(stage="score", provider="openai", model=FIT.MODEL, what="shot audit", usd=round(usd, 4)))
        json.dump(whole, open(os.path.join(ctx.supply, "pack.json"), "w"), ensure_ascii=False, indent=1)
    # ---- pace: short lines, and lines whose fill (designed cards and reframes) is over FILL_SHARE of their own frames,
    # get a top-up and one rebuild before the cut is final
    pc = pace_of(beats)
    pace_fail = not (CUTS_10S[0] <= pc["cuts_10s"] <= CUTS_10S[1]) or pc["long_share"] > LONG_SHOTS_MAX or pc["shortfall"]
    fill_over = sorted(int(k) for k in ((beats.get("measured") or {}).get("fill_over_lines") or {}))
    topped = None
    want_lines = sorted({int(k) for k in pc["shortfall"]} | set(fill_over)) if (pace_fail and pc["shortfall"]) or fill_over else []
    if want_lines and script_rec is not None and ctx.args.get("topup", True) is not False:
        try:
            topped = topup_from_score(ctx, script_rec, pack, want_lines)
        except Exception as e:  # noqa: BLE001 - the top-up is a repair; its failure is recorded, the plan stands
            ctx.log(f"pace top-up: {e!r}"[:300])
            topped = dict(error=repr(e)[:200])
        if topped and topped.get("clips"):
            l1, beats = build(sc, pack, knobs)
            taste = F.taste_check(l1, beats)
            pc = pace_of(beats)
            pace_fail = not (CUTS_10S[0] <= pc["cuts_10s"] <= CUTS_10S[1]) or pc["long_share"] > LONG_SHOTS_MAX or pc["shortfall"]
    if pace_fail:
        defects.append(f"pace off the bar: {json.dumps(pc)[:300]}")
    # ---- layout: full-bleed pictures on BLEED_MIN of the shots, else built once more with windows and collages halved
    full, layered = bleed_share(beats)
    layout = dict(bleed_share=full, print_window_share=layered, rebuilt=False)
    if full < BLEED_MIN and any(p.get("kind") in ("clip", "photo") for p in beats.get("pictures") or []):
        knobs = dict(knobs, window_share=0.5 * float(knobs.get("window_share", R.WINDOW_SHARE)), collage_k=0.5 * float(knobs.get("collage_k", 1.0)))
        l1, beats = build(sc, pack, knobs)
        taste = F.taste_check(l1, beats)
        layout.update(rebuilt=True, bleed_share_after=bleed_share(beats)[0], print_window_share_after=bleed_share(beats)[1])
        if bleed_share(beats)[0] < BLEED_MIN:
            defects.append(f"full-frame pictures on {bleed_share(beats)[0]:.0%} of shots (at least {BLEED_MIN:.0%})")
    # ---- display density on the plan: under DISPLAY_SHARE's floor, the writer adds anchored items once and the plan is
    # built again; what stays short is a recorded defect
    density = None
    try:
        typ = type_measures(l1, beats, sc)
        density = dict(before=typ["display_shot_share"])
        if typ["display_shot_share"] < DISPLAY_SHARE[0] and ctx.args.get("density", True) is not False:
            lines = uncovered_lines(beats)
            sc2, added, use = density_repair(sc, lines)
            density.update(lines=lines, added=added, usd=(use or {}).get("usd"))
            if added:
                sc.clear()
                sc.update(sc2)
                l1, beats = build(sc, pack, knobs)
                taste = F.taste_check(l1, beats)
                density["after"] = type_measures(l1, beats, sc)["display_shot_share"]
            if float(density.get("after", density["before"])) < DISPLAY_SHARE[0]:
                defects.append(f"display type on {float(density.get('after', density['before'])):.0%} of planned shots after the repair")
    except Exception as e:  # noqa: BLE001 - the repair is one call; its failure is recorded, the plan stands
        density = dict(error=repr(e)[:200])
    # ---- colour by measure
    bw = plan_bw(beats)
    colour = dict(bw_shot_share_plan=bw, rebuilt=False, intrinsic_grey_share=(beats.get("measured") or {}).get("intrinsic_grey_share"))
    if not BW_SHARE[0] <= bw <= BW_SHARE[1]:
        if bw > BW_SHARE[1]:
            knobs = dict(knobs, device_budget=0.5 * float(knobs.get("device_budget", 1.0)))
            l1, beats = build(sc, pack, knobs)
            taste = F.taste_check(l1, beats)
            colour.update(rebuilt=True, bw_shot_share_after=plan_bw(beats))
            ctx.log(f"colour: black and white on {bw:.0%} of shots; built again with the devices lowered")
        if not BW_SHARE[0] <= plan_bw(beats) <= BW_SHARE[1]:
            defects.append(f"black and white on {plan_bw(beats):.0%} of planned shots (bar {BW_SHARE[0]:.0%}-{BW_SHARE[1]:.0%})")
    # ---- repeats: a take group shown twice is denied on its later line and the plan built once more
    rep_fix = None
    if float((beats.get("measured") or {}).get("repeat_share") or 0) > REPEAT_MAX:
        again = repeated_keys(beats, pack)
        if again:
            drop = list(knobs.get("drop") or []) + [k for k in again if k not in (knobs.get("drop") or [])]
            knobs = dict(knobs, drop=drop)
            before = (beats.get("measured") or {}).get("repeat_share")
            l1, beats = build(sc, pack, knobs)
            taste = F.taste_check(l1, beats)
            rep_fix = dict(denied=again[:30], before=before, after=(beats.get("measured") or {}).get("repeat_share"))
    m = beats.setdefault("measured", {})
    m["repeat_rebuild"] = rep_fix
    m["audit"] = dict(rounds=rounds, failed=failed[:60], usd=round(usd, 4))
    m["colour_plan"] = colour
    m["layout_plan"] = layout
    m["display_density"] = density
    m["pace_plan"] = dict(pc, topup=topped)
    problems = list(taste.get("problems") or [])
    if float(m.get("repeat_share") or 0) > REPEAT_MAX:
        problems.append(f"{m['repeat_share']:.0%} of shots repeat a take (at most {REPEAT_MAX:.0%})")
    if int(m.get("static_shots") or 0) > 0:
        problems.append(f"{m['static_shots']} static shots ({m.get('static_s')} s) on the plan: every shot moves by content or camera")
    planned = [str(x.get("text")) for s_ in sc["segments"] for x in s_.get("show") or [] if isinstance(x, dict) and x.get("role") == "figure"]
    placed = {str(x.get("text")).upper() for x in beats.get("display") or []} | {str(x).upper() for x in beats.get("display_as_card") or []} | \
             {str(p.get("shows") or "").upper() for p in beats.get("pictures") or [] if p.get("kind") == "designed"}
    unplaced = [t for t in planned if t.upper() not in placed and not any(t.upper() in q for q in placed)]
    if unplaced:
        problems.append("figures planned but not placed: " + json.dumps(unplaced)[:200])
    m["figures"] = dict(planned=len(planned), placed=len(planned) - len(unplaced), unplaced=unplaced)
    m["defects"] = defects
    taste = dict(taste, ok=not problems, problems=problems)
    return l1, beats, taste


DENSITY_SYSTEM = """You are the picture editor of a narrated vertical story film cut like the best documentary reels. The script is final; you never change a word. The film needs more large display type: a key word, a name or a short chapter title set big over the pictures on about one shot in six.

For the segments you are given (each with its words numbered from 0 and the display items it already has), add up to two display items each, only where the words carry them: {"i": segment, "text": exactly the spoken word or words as printed (a key word, a person's name, a two-to-four-word chapter title on a segment that opens a section), "role": "key"|"name"|"chapter", "word_i": the index of the spoken word it lands on}. Never a number or a figure. Never repeat an item the segment already has.

Answer with one JSON object only: {"items": [...]}"""


def uncovered_lines(beats, n=4):
    """The lines with the most base shots that carry no display type over them, most first."""
    big = [d["f"] for d in beats.get("display") or []]
    cnt = {}
    for p in beats.get("pictures") or []:
        if p.get("kind") in ("clip", "photo") and p.get("spoken_line") is not None and \
                not any(a < p["f"][1] and p["f"][0] < b for a, b in big):
            cnt[int(p["spoken_line"])] = cnt.get(int(p["spoken_line"]), 0) + 1
    return [k for k, _ in sorted(cnt.items(), key=lambda q: (-q[1], q[0]))[:n]]


def density_repair(sc, lines, ask=None, verify_fn=None):
    """(script with the added items, [added], usage): one OpenAI call (writer.ANNOTATE_MODEL) asks for word-anchored
    display items on the given lines; an item is kept only when its role and word_i are valid, its text is new to the
    segment and the script still verifies with it (no number may stand outside a verified quote)."""
    ask = ask or (lambda system, content: writer.ask_openai(system, content, effort="low", max_tokens=4000, what="display density",
                                                            model=writer.ANNOTATE_MODEL))
    # an item that prints no number cannot put a number outside a verified quote: the digit rule below is its check
    # against the facts; verify_fn may add the full page check
    verify_fn = verify_fn or (lambda s_: {"ok": True})
    rows = []
    for i in lines:
        s_ = sc["segments"][i]
        ws = str(s_.get("say") or "").split()
        rows.append(f"{i}. beat={s_.get('beat')} has={json.dumps([x.get('text') for x in s_.get('show') or [] if isinstance(x, dict)], ensure_ascii=False)}\n   words: "
                    + " ".join(f"{j}:{w}" for j, w in enumerate(ws)))
    text, use = ask(DENSITY_SYSTEM, "# Segments\n" + "\n".join(rows))
    try:
        got = writer.json_block(text).get("items") or []
    except ValueError:
        return sc, [], use
    out = json.loads(json.dumps(sc))
    added = []
    for x in got:
        if not isinstance(x, dict) or x.get("i") not in lines or x.get("role") not in ("key", "name", "chapter"):
            continue
        seg = out["segments"][x["i"]]
        n = len(str(seg.get("say") or "").split())
        t_ = str(x.get("text") or "").strip()
        wi = x.get("word_i")
        if not t_ or not (isinstance(wi, int) and not isinstance(wi, bool) and 0 <= wi < n) or writer.digits(t_) or \
                t_ in [str(y.get("text")) for y in seg.get("show") or [] if isinstance(y, dict)] or len(seg.get("show") or []) >= 3:
            continue
        trial = json.loads(json.dumps(out))
        trial["segments"][x["i"]].setdefault("show", []).append(dict(text=t_, role=x["role"], word_i=wi))
        if verify_fn(trial).get("ok"):
            out = trial
            added.append(dict(i=x["i"], text=t_, role=x["role"]))
    return out, added, use


def repeated_keys(beats, pack):
    """The later showings of a take group already shown ("file@line", never a glimpse's take playing on or a same-take
    cut-in): what a rebuild denies on those lines so the repeat goes."""
    group = {c["path"]: c.get("take_group") or c["path"] for ln in pack.get("footage") or [] for c in ln.get("clips") or []}
    seen, last, out = set(), None, []
    for p in sorted(beats.get("pictures") or [], key=lambda q: q["f"][0]):
        if p.get("kind") not in ("clip", "photo") or p.get("line") is None:
            continue
        k = (group.get(p["path"], p["path"]), p.get("take")) if p["kind"] == "clip" else (p["path"], None)
        if p.get("cont") and k == last:
            continue
        if k in seen and p.get("tier") not in ("reframe",):
            out.append(f"{os.path.basename(p['path'])}@{int(p['line'])}")
        seen.add(k)
        last = k
    return list(dict.fromkeys(out))


def topup_from_score(ctx, script_rec, pack, lines):
    """The pace gate's repair: the short lines' footage top-up (topup_once) run from the score stage, the pack saved."""
    words = pack["words"] if isinstance(pack["words"], dict) else json.load(open(pack["words"]))
    durs = chain.seg_durs(words["words"], len(script_rec["script"]["segments"]))
    anchors = {k: [r["path"] for r in v if r.get("path")] for k, v in (pack.get("pictures") or {}).items()}
    k = 2 + sum(1 for n in os.listdir(ctx.supply) if n.startswith("footage") and os.path.isdir(os.path.join(ctx.supply, n)))
    spend = []
    added = topup_once(ctx, script_rec, pack, durs, lines, spend, f"footage{k}", anchors,
                       dict(preserve_source=True, shot_s=REEL_SHOT_S, line_max=REEL_LINE_MAX, per=REEL_PER))
    whole = json.load(open(os.path.join(ctx.supply, "pack.json")))
    whole["pack"] = pack
    whole["record"].setdefault("spend", []).extend(spend)
    json.dump(whole, open(os.path.join(ctx.supply, "pack.json"), "w"), ensure_ascii=False, indent=1)
    return dict(lines=lines, clips=added)


def render(ctx, script, sc):
    out = os.path.join(ctx.supply, "film_engine.mp4")
    cache = os.path.join(ctx.tmp, "scene-cache")
    l1 = json.load(open(sc["l1"]))
    pack = load_pack(ctx)
    sound = os.path.join(ctx.supply, "sound.m4a")
    mix = F.audio_master(pack["ffmpeg"], l1, pack["narration"], sound, ctx.tmp) if not os.path.exists(sound) else {}
    settle = os.environ.get("SHELL_RENDER_CMD")       # a renderer in place of the Mac engine: the box image's, where a film is settled
    if not os.path.exists(out):
        scene = chain.alien_bank(ctx, sc["l1"])          # the scene to draw: the alien bank's asks laid out first, when it carries any
        if settle:
            log = chain.sh(ctx, ["zsh", settle, scene, ctx.supply, sound, out + ".part.mp4"], label=f"wf3-render-{ctx.run_id}", heavy=True, timeout=7200)
            j = {}
            bl = os.path.join(ctx.supply, "box", "film.mp4.log.json")
            if os.path.exists(bl):
                try:
                    j = json.load(open(bl))
                except ValueError:
                    j = {}
            try:
                j["settled_on"] = json.loads(log[log.rindex("{"):log.rindex("}") + 1])
            except ValueError:
                j["settled_on"] = dict(cmd=os.path.basename(settle))
        else:
            log = chain.sh(ctx, ["env", f"PYTHONPATH={env_path()}", f"VALMERA_SCENE_CACHE={cache}", chain.PY, "-m", "scene.run", "render", "--l1", scene,
                                 "--work", ctx.supply, "--out", out + ".part.mp4", "--tier", "master", "--strict", "--cache", cache, "--audio", sound],
                           label=f"wf3-render-{ctx.run_id}", heavy=True, timeout=7200)
            try:
                j = json.loads(log[log.index("{"):log.rindex("}") + 1])
            except ValueError:
                j = {}
        os.replace(out + ".part.mp4", out)
        json.dump(dict(j, mix=mix), open(out + ".log.json", "w"))
    j = json.load(open(out + ".log.json")) if os.path.exists(out + ".log.json") else {}
    return dict(engine="scene", film=out, engine_check=dict(frames=j.get("frames"), seconds=j.get("seconds"), audio=j.get("mix"),
                                                              degraded=j.get("degraded"), warnings=j.get("warnings"), settled_on=j.get("settled_on")))


def mark_samples(prog, final, n=6):
    """The mark measured on several frames of the programme: where one frame moves fast, its one difference cannot tell."""
    import numpy as np
    import brand
    R = brand._renderer()
    p = brand.probe(final)
    Wd, Hd = p["w"], p["h"]
    ix0, iy0, iw, ih = R.watermark_geometry(Wd, Hd)["image"]
    q = brand.probe(prog)
    dur = q["frames"] / round(q["fps"]) if q["frames"] else q["dur"]
    rows = []
    for k in range(n):
        t = dur * (k + 0.5) / n
        x, y = brand._frame(prog, t, Wd, Hd), brand._frame(final, t, Wd, Hd)
        roi = float(np.abs(x[iy0:iy0 + ih, ix0:ix0 + iw] - y[iy0:iy0 + ih, ix0:ix0 + iw]).mean())
        rest = float(np.abs(x - y).mean())
        rows.append(dict(t=round(t, 2), roi=round(roi, 2), outside=round(rest, 2), ok=bool(roi > 4 * max(rest, 0.5))))
    return rows


def caption_contrast(film, l1, ffmpeg):
    """Measured on the engine's film: for each caption, the tone of the frame around it at its middle frame. White ink
    on a median over 0.6, or dark ink on one under 0.4, is a caption that does not read (TASTE P4, h3)."""
    import subprocess
    import numpy as np
    caps = [i for i in l1["items"] if i["k"] == "word" and i["v"] in ("cap", "capd") and i["t"].strip() not in (".",)]
    bad, rows = 0, 0
    for it in caps[::2]:
        f = (it["f"][0] + it["f"][1]) // 2
        r = subprocess.run([ffmpeg, "-v", "error", "-ss", f"{(f + 0.5) / F.FPS:.3f}", "-i", film, "-frames:v", "1", "-vf", "scale=270:480",
                            "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True)
        g = np.frombuffer(r.stdout, np.uint8)[:270 * 480].reshape(480, 270) / 255.0
        y = float(it["y"][-1] if isinstance(it["y"], list) else it["y"])
        band = g[max(int((y - 0.045) * 480), 0):int((y + 0.012) * 480), 54:216]
        if band.size == 0:
            continue
        med = float(np.median(band))
        rows += 1
        if (it["v"] == "cap" and med > 0.6) or (it["v"] == "capd" and med < 0.4):
            bad += 1
    return dict(captions_sampled=rows, unreadable=bad, ok=rows == 0 or bad / rows <= 0.05)


RENDER_DROP = "render_drop.json"       # the render's failing pictures (file@line), merged into the score's drop knob


def render_drop(ctx):
    p = os.path.join(ctx.supply, RENDER_DROP)
    try:
        v = json.load(open(p))
        return [str(x) for x in v.get("drop") or []]
    except (OSError, ValueError, AttributeError):
        return []


def clear_render(ctx):
    """The render's cached outputs, removed so render() and the finish draw the plan again."""
    sup = ctx.supply
    for p in (os.path.join(sup, "film_engine.mp4"), os.path.join(sup, "film_engine.mp4.log.json"), os.path.join(sup, "sound.m4a"),
              os.path.join(sup, "l1.alien.json"), os.path.join(sup, "film_engine_music.mp4"), os.path.join(sup, "music.json"),
              os.path.join(sup, "film_engine_song.mp4"), os.path.join(sup, "song.json"), os.path.join(ctx.run_dir, "final.mp4")):
        if os.path.exists(p):
            os.remove(p)


def check(ctx, rd, sc):
    """The shell's finish and checks, then the story reel's own (check_once). The picture gate (wf3 only): a render
    whose pictures fail their words (picture_fit.fail_s > 0) has its failing pictures written to supply/render_drop.json
    (file@line), the score built again with them dropped (score() merges the file), drawn again and checked once more;
    still failing, the run stops before deliver (resumable), so no film with a wrong picture is delivered. A fill
    overflow is a recorded defect, not a stop: a designed card is not a wrong picture."""
    r, fc = check_once(ctx, rd, sc)
    if ctx.workflow != "wf3" or fc is None or not fc["picture_fit"]["fail_s"] > 0 or ctx.args.get("picture_gate") is False:
        return r
    marker = os.path.join(ctx.supply, "render_repair.json")
    if not os.path.exists(marker):
        bad = [x["drop"] for x in fc["picture_fit"]["shots"] if not x.get("fit") and x.get("drop")]
        json.dump(dict(drop=sorted(set(render_drop(ctx)) | set(bad))), open(os.path.join(ctx.supply, RENDER_DROP), "w"), indent=1)
        json.dump(dict(fail_s=fc["picture_fit"]["fail_s"], dropped=bad), open(marker, "w"), indent=1)
        ctx.log(f"picture gate: {fc['picture_fit']['fail_s']} s of pictures fail their words; dropping {len(bad)} and drawing again")
        script_rec = json.load(open(os.path.join(ctx.run_dir, "script.json")))
        clear_render(ctx)
        sc = score(ctx, script_rec)
        rd = render(ctx, script_rec, sc)
        r, fc = check_once(ctx, rd, sc)
        r["checks"].setdefault("stories", {})["picture_gate"] = dict(repaired=True, first=json.load(open(marker)), after=fc["picture_fit"]["fail_s"])
    if fc["picture_fit"]["fail_s"] > 0:
        raise RuntimeError(f"picture gate: {fc['picture_fit']['fail_s']} s of pictures still fail their words after the repair: "
                           + json.dumps([dict(line=x["line"], why=x.get("why")) for x in fc["picture_fit"]["shots"] if not x.get("fit")])[:400])
    return r


def check_once(ctx, rd, sc):
    r = chain.finish_and_check(ctx, rd, sc)
    c = r["checks"]
    rows = mark_samples(rd["film"], r["final"])
    c["mark_samples"] = rows
    if not c.get("mark_ok") and sum(x["ok"] for x in rows) >= len(rows) - 1:
        c["mark_shell"] = dict(roi=c.get("mark_roi_diff"), outside=c.get("outside_diff"),
                               note="the shell's one sample sat on a moving frame; the mark is one overlay across the programme, measured here on six")
        c["mark_ok"] = True
        c["ok"] = bool(c["length_ok"] and c["endcard_ok"] and c["loud_ok"] and c["music"]["ok"])
    pack = load_pack(ctx)
    c["stories"] = dict(taste=sc["taste"], credits=pack.get("credits"), narration=dict(voice=None, gate=None),
                        pictures=sc["beats"].get("pictures"), missing_pictures=pack.get("missing"),
                        # how the film moves and sounds, as the engines measured it, and where it was drawn
                        pace=sc["beats"].get("pace"), sound_design=sc["beats"].get("sound_design"), turn_s=sc.get("turn_s"),
                        render=rd.get("engine_check"), measured=sc["beats"].get("measured"), style=sc["beats"].get("style"))
    rec = json.load(open(os.path.join(ctx.supply, "pack.json")))["record"]
    c["stories"]["narration"] = dict(seconds=rec.get("narration_s"), voice=rec.get("voice"), gate=rec.get("gate"))
    c["stories"]["caption_contrast"] = caption_contrast(rd["film"], json.load(open(sc["l1"])), pack["ffmpeg"])
    c["ok"] = bool(c["ok"] and sc["taste"]["ok"] and c["stories"]["caption_contrast"]["ok"])
    c["stories"]["voices"] = voices_record(ctx, pack)
    if ctx.workflow == "wf3":
        # the story reel's own measures on the rendered programme, always (WF2 calls this check without them); a film
        # cut without per-line verdicts never passes
        fc = film_checks(ctx, rd["film"], sc, pack, music=r.get("final_music"), clean=r.get("final"))
        c["stories"].update(fc)
        c["stories"]["supply"] = supply_evidence(ctx, pack)
        defects = list(fc.get("defects") or []) + list(pack.get("defects") or []) + list(((sc["beats"].get("measured") or {}).get("defects")) or [])
        if pack.get("fit") is None:
            defects.append("no per-line picture verdicts: the film was not cut by its verdicts")
        if c["stories"]["voices"].get("placed", 0) == 0:
            # tried or not: a story film with no real voice is short of the judge (every critic reel cuts one in)
            defects.append("no real voice placed (" + f"{c['stories']['voices'].get('tried', 0)} tried): "
                           + "; ".join(c["stories"]["voices"].get("rejected") or ["nothing was tried"])[:300])
        c["stories"]["defects"] = defects
        c["ok"] = bool(c["ok"] and pack.get("fit") is not None and fc["picture_fit"]["fail_s"] == 0 and fc["gates_ok"])
        return r, fc
    return r, None


def voices_record(ctx, pack):
    """The real voices this film tried, placed and why any was not: every candidate the footage engine considered
    (footage.json voices_tried, each with its reason) and the voices it cut that the narration did not place."""
    tried, found = [], []
    for name in sorted(os.listdir(ctx.supply)) if os.path.isdir(ctx.supply) else []:
        if not name.startswith("footage"):
            continue
        try:
            r = json.load(open(os.path.join(ctx.supply, name, "footage.json")))
        except (OSError, ValueError):
            continue
        tried += [v for v in r.get("voices_tried") or [] if isinstance(v, dict)]
        found += [v for v in r.get("voices") or [] if isinstance(v, dict)]
    placed = [v for v in pack.get("voices") or [] if v]
    pf = {p.get("file") for p in placed}
    rej = [f"{v.get('person') or '?'} after {v.get('after')}: {v.get('reason')}" for v in tried if v.get("reason")]
    rej += [f"{v.get('person')} after {v.get('after')}: cut but not placed in the narration" for v in found if v.get("file") not in pf]
    cands = [v for v in tried if v.get("person") or v.get("source")]
    return dict(tried=len(cands) if tried else len(found), placed=len(placed), found=len(found), rejected=rej[:12],
                tried_list=[dict(person=v.get("person"), after=v.get("after"), reason=v.get("reason"),
                                 source=(v.get("source") or {}).get("title")) for v in tried][:12])


def colourfulness(jpg):
    """Hasler and Suesstrunk's colourfulness of one frame (the teardown's measure: under 8 reads as black and white)."""
    import numpy as np
    from PIL import Image
    with Image.open(jpg) as im:
        a = np.asarray(im.convert("RGB"), np.float32)
    rg = a[..., 0] - a[..., 1]
    yb = 0.5 * (a[..., 0] + a[..., 1]) - a[..., 2]
    lum = 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]
    return float(np.hypot(rg.std(), yb.std()) + 0.3 * np.hypot(rg.mean(), yb.mean())), float((lum < 30).mean())


MOTION_CONTENT_MIN = 0.65        # content motion on this share of shot time at least (the judge: 69% of shots)
COLOUR_MEDIAN_MIN = 15.0         # colourfulness median over shots (the judge: about 24)
VOID_MAX = 0.2                   # near-black share on print frames
DISPLAY_SHARE = (0.15, 0.25)     # shots with display type over them
CAP_MIN_PX = 108                 # a figure's cap height
CAPTION_LAG = (0.11, 0.20)       # caption ink after the voice, median seconds
MUSIC_SHARE = (0.81, 0.95)       # the bed within 10 dB of its speaking-time median
DROPS = (1, 3)


def _gray(path, ffmpeg, fps, w=270, h=480, t0=None, dur=None):
    import numpy as np
    cmd = [ffmpeg, "-v", "error"] + (["-ss", f"{t0:.3f}"] if t0 is not None else []) + ["-i", path] + (["-t", f"{dur:.3f}"] if dur else []) + \
        ["-vf", f"fps={fps},scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    import subprocess
    out = subprocess.run(cmd, capture_output=True, timeout=1800).stdout
    n = len(out) // (w * h)
    return np.frombuffer(out[:n * w * h], np.uint8).reshape(n, h, w)


def film_motion(film, pictures, ffmpeg, fps=30.0):
    """The rendered programme's motion per planned shot, the way the rater measures it: Farneback flow at 270x480 on
    three and more frame pairs inside the shot (pairs across a cut or a flash, mean luma change over 40, left out), the
    global similarity fit by RANSAC (its zoom or pan over the shot, 2% and more, is a camera move) and the 90th percentile
    of what is left (1.0 px and more is content motion). {content_moving_share, camera_moving_share, static_shots, static_s}."""
    import cv2
    import numpy as np
    fr = _gray(film, ffmpeg, fps)
    if len(fr) < 2:
        return dict(content_moving_share=None, camera_moving_share=None, static_shots=None, static_s=None, shots=0)
    ys, xs = np.mgrid[8:fr.shape[1]:16, 8:fr.shape[2]:16]
    pts = np.stack([xs.ravel(), ys.ravel()], 1).astype(np.float32)
    base = [p for p in pictures or [] if p.get("kind") in ("clip", "photo", "designed")]
    tot = con = cam = 0
    static = []
    for p in base:
        f0, f1 = int(p["f"][0]), int(p["f"][1])
        a, b = f0 + 1, min(f1 - 1, len(fr) - 1)
        if b - a < 1:
            continue
        idx = sorted({int(round(q)) for q in np.linspace(a, b - 1, num=min(6, b - a))})
        res, zoom, pan = [], 1.0, np.zeros(2)
        for k in idx:
            x, y = fr[k], fr[k + 1]
            if float(np.abs(x.astype(np.float32) - y.astype(np.float32)).mean()) > 40:
                continue
            flow = cv2.calcOpticalFlowFarneback(x, y, None, 0.5, 3, 15, 3, 5, 1.2, 0)
            d = flow[pts[:, 1].astype(int), pts[:, 0].astype(int)]
            M, _ = cv2.estimateAffinePartial2D(pts, pts + d, method=cv2.RANSAC, ransacReprojThreshold=1.0)
            if M is None:
                res.append(float(np.percentile(np.linalg.norm(d, axis=1), 90)))
                continue
            pred = pts @ M[:, :2].T + M[:, 2] - pts
            res.append(float(np.percentile(np.linalg.norm(d - pred, axis=1), 90)))
            step = len(idx) and (b - a) / max(len(idx), 1)
            zoom *= float(np.sqrt(abs(np.linalg.det(M[:, :2])))) ** step
            pan += M[:, 2] * step
        if not res:
            continue
        n = f1 - f0
        content = float(np.median(res)) >= R.MOTION_FLOOR
        camera = abs(zoom - 1.0) >= R.RIGID_MOVE or float(np.max(np.abs(pan) / np.array([fr.shape[2], fr.shape[1]]))) >= R.RIGID_MOVE
        tot += n
        con += n if content else 0
        cam += n if camera else 0
        if not content and not camera:
            static.append(dict(f=[f0, f1], kind=p["kind"], mode=p.get("mode"), residual=round(float(np.median(res)), 2), zoom=round(zoom - 1, 4)))
    return dict(content_moving_share=round(con / max(tot, 1), 3), camera_moving_share=round(cam / max(tot, 1), 3), static_shots=len(static),
                static_s=round(sum(x["f"][1] - x["f"][0] for x in static) / F.FPS, 2), static_list=static[:20], shots=len(base))


FLIPS_10S = (0.3, 3.6)           # polarity flips a 10 s (the teardown: frame luma correlation -0.3 or lower with the frame before)
WHITE_10S = (0.5, 1.9)           # white frames a 10 s (luma 225 and more, spread 25 or less)
VISIBLE_CUTS_MIN = 15.5          # visible cuts a 10 s, by ffmpeg's scene score (the rater's method)
HASH_REPEAT_MAX = 0.05           # shots whose middle frame repeats an earlier shot's (perceptual hash), time-continuous cut-ins aside


def flips_and_whites(frames):
    """(polarity flips, white frames) in a sequence of grey frames (the teardown's definitions): a flip is a frame whose
    luma correlates -0.3 or lower with the frame before; a white frame has mean luma 225 and more and a spread of 25 or less."""
    import numpy as np
    flips = whites = 0
    prev = None
    for fr in frames:
        a = np.asarray(fr, np.float32).ravel()
        if a.mean() >= 225 and a.std() <= 25:
            whites += 1
        if prev is not None and a.std() > 1e-3 and prev.std() > 1e-3:
            c = float(np.mean((a - a.mean()) * (prev - prev.mean())) / (a.std() * prev.std()))
            if c <= -0.3:
                flips += 1
        prev = a
    return flips, whites


def ahash(fr):
    """A 64-bit average hash of a grey frame."""
    import numpy as np
    a = np.asarray(fr, np.float32)
    h, w = a.shape
    a = a[:h - h % 8, :w - w % 8].reshape(8, (h - h % 8) // 8, 8, (w - w % 8) // 8).mean(axis=(1, 3))
    return (a > a.mean()).ravel()


def hash_repeats(frames, pictures, fps=30.0):
    """The share of base shots whose middle frame's hash lies within 6 bits of an earlier shot's; a shot that continues
    the take before it in time (a glimpse's take playing on, a same-take cut-in) is not a repeat."""
    import numpy as np
    base = sorted((p for p in pictures or [] if p.get("kind") in ("clip", "photo") and p["f"][1] - p["f"][0] >= 2), key=lambda q: q["f"][0])
    seen, rep = [], 0
    for p in base:
        k = (p["f"][0] + p["f"][1]) // 2
        if k >= len(frames):
            continue
        h = ahash(frames[k])
        if not p.get("cont") and any(int(np.sum(h != q)) <= 6 for q in seen):
            rep += 1
        seen.append(h)
    return round(rep / max(len(base), 1), 3)


def visible_cuts(film, ffmpeg, thresh=0.3):
    """Cuts a viewer sees, by ffmpeg's scene score over the programme (the rater's method): their count."""
    import re
    import subprocess
    r = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-i", film, "-an", "-vf", f"scale=270:-2,select='gt(scene,{thresh})',showinfo",
                        "-f", "null", "-"], capture_output=True, text=True, timeout=1800)
    return len(re.findall(r"pts_time:", r.stderr or ""))


def punctuation_measures(film, ffmpeg, pictures, voice_s):
    """The film's punctuation as the teardown measures it on the render: flips and white frames a 10 s, visible cuts a
    10 s and the hash repeat share."""
    frames = _gray(film, ffmpeg, 30.0, w=90, h=160)
    flips, whites = flips_and_whites(frames)
    secs = max(float(voice_s or len(frames) / 30.0), 1e-6)
    try:
        vc = visible_cuts(film, ffmpeg)
    except Exception:  # noqa: BLE001
        vc = None
    return dict(flips_10s=round(flips / secs * 10, 2), white_10s=round(whites / secs * 10, 2), flips=flips, white_frames=whites,
                visible_cuts_10s=round(vc / secs * 10, 2) if vc is not None else None, hash_repeat_share=hash_repeats(frames, pictures))


def type_measures(l1, beats, sc):
    """The film's display type as planned: the share of shots with display type over them, the figures placed against
    those annotate planned, the type pages (designed cards) per section joint, the cap height of each figure (the
    engine's font metrics at its fitted size and its resting scale) and how long it holds, and the captions' ink lag."""
    Rl = F.Ruler(l1["voices"])
    big = [it for it in l1["items"] if it.get("k") == "word" and it.get("role") == "big"]
    base = [p for p in beats.get("pictures") or [] if p.get("kind") in ("clip", "photo", "designed")]
    with_type = sum(1 for p in base if p["kind"] == "designed" or any(w["f"][0] < p["f"][1] and p["f"][0] < w["f"][1] for w in big))
    figs = []
    for s_ in sc.get("segments") or []:
        for x in s_.get("show") or []:
            if isinstance(x, dict) and x.get("role") == "figure":
                want = str(x.get("big") or x.get("text") or "").upper()
                its = [w for w in big if str(w.get("t") or "").upper() == want or (want and want in str(w.get("t") or "").upper())]
                if its:
                    w = max(its, key=lambda q: q["f"][1] - q["f"][0])
                    s = w.get("s", 1)
                    s = float(s[-1] if isinstance(s, list) else s or 1)
                    figs.append(dict(text=x.get("text"), cap_px=round(Rl.cap(w["v"]) * s, 1), held_s=round((w["f"][1] - w["f"][0]) / F.FPS, 2)))
                else:
                    figs.append(dict(text=x.get("text"), cap_px=None, held_s=0.0))
    joints = sum(1 for i, s_ in enumerate(sc.get("segments") or []) if i and s_.get("beat") in ("turn", "low", "payoff"))
    pages = sum(1 for p in base if p["kind"] == "designed" and not p.get("fallback"))
    lags = (beats.get("measured") or {}).get("caption_lag_f") or []
    lag = sorted(lags)[len(lags) // 2] / F.FPS if lags else None
    return dict(display_shot_share=round(with_type / max(len(base), 1), 3), figures=figs,
                figures_planned=len(figs), figures_placed=sum(1 for f in figs if f["cap_px"]), type_pages=pages, joints=joints,
                caption_lag_median_s=round(lag, 3) if lag is not None else None)


def _audio(path, ffmpeg, sr=16000):
    import subprocess
    import numpy as np
    out = subprocess.run([ffmpeg, "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"], capture_output=True, timeout=600).stdout
    return np.frombuffer(out, np.float32)


def sound_measures(music, clean, ffmpeg, l1, joints=(), sr=16000):
    """The bed as the rater estimates it (the film with music less 0.845 x the film without), in 100 ms frames:
    music_runtime_share (the bed within 10 dB of its median while the voice speaks), the drops (bed 10 dB and more under
    that median for 0.3-3 s: count, length, depth, and whether one comes back within 0.1 s of a section's first word),
    the swells (rises of 6 dB and more within 3 s), the bed against the voice, and for each placed sound its 50 ms peak
    over the 2 s of bed around it."""
    import numpy as np
    a, b = _audio(music, ffmpeg, sr), _audio(clean, ffmpeg, sr)
    n = min(len(a), len(b))
    if n < sr:
        return None
    bed, voice = a[:n] - 0.845 * b[:n], b[:n]
    hop = sr // 10

    def db(x):
        k = len(x) // hop
        r = np.sqrt(np.mean(x[:k * hop].reshape(k, hop) ** 2, axis=1) + 1e-12)
        return 20 * np.log10(r + 1e-9)
    bd, vd = db(bed), db(voice)
    speak = vd > np.percentile(vd, 60) - 6
    if not speak.any():
        return None
    med = float(np.median(bd[speak]))
    share = float(np.mean(np.abs(bd - med) <= 10))
    low = bd < med - 10
    drops, k = [], 0
    while k < len(low):
        if low[k]:
            j = k
            while j < len(low) and low[j]:
                j += 1
            L = (j - k) / 10
            if 0.3 <= L <= 3.0:
                drops.append(dict(t=round(k / 10, 2), len_s=round(L, 2), depth_db=round(med - float(np.min(bd[k:j])), 1)))
            k = j
        else:
            k += 1
    firsts = [float(j.get("next_first_word_t")) for j in joints or [] if j.get("next_first_word_t") is not None]
    reentry = any(any(abs(d["t"] + d["len_s"] - x) <= 0.1 for x in firsts) for d in drops)
    swells, last = [], -10
    for k in range(30, len(bd)):
        rise = float(bd[k] - np.min(bd[k - 30:k]))
        if rise >= 6 and k - last > 30:
            swells.append(dict(t=round(k / 10, 2), db=round(rise, 1)))
            last = k
    hits = []
    for s_ in l1.get("sound") or []:
        t = float(s_.get("event_f") or 0) / F.FPS
        i0, i1 = int(max(t - 0.025, 0) * sr), int((t + 0.025) * sr)
        j0, j1 = int(max(t - 1.0, 0) * sr), int(min(t + 1.0, n / sr) * sr)
        if i1 >= n or j1 - j0 < hop:
            continue
        pk = 20 * np.log10(float(np.max(np.abs(voice[i0:i1]))) + 1e-9)
        bl = 20 * np.log10(float(np.sqrt(np.mean(bed[j0:j1] ** 2))) + 1e-9)
        hits.append(round(pk - bl, 1))
    return dict(music_runtime_share=round(share, 3), bed_median_db=round(med, 1), bed_under_voice_db=round(float(np.median(vd[speak])) - med, 1),
                drops=dict(count=len(drops), list=drops[:8], reentry_on_word=reentry), swells=dict(count=len(swells), median_db=round(float(np.median([x["db"] for x in swells])), 1) if swells else None),
                sfx_over_bed_db=dict(n=len(hits), median=sorted(hits)[len(hits) // 2] if hits else None))


def film_checks(ctx, film, sc, pack, music=None, clean=None):
    """The story reel's measures on the rendered programme and their gates:
    picture_fit  each picture's played SOURCE window (path, src from beats.pictures, its source's text and its take's
                 frozen identity) judged against the words under it (fit.audit); on the render only whether the subject
                 still reads in the film's crop or print (fit.readable); fail_s counts a shot failing either
    colour       per shot, the middle frame's colourfulness: bw_shot_share (under 8), median, void share on prints
    motion       film_motion (Farneback per planned shot): static shots and the content-moving share
    type         type_measures; sound: sound_measures on the film with music against the film without
    gates_ok is false, and each failure a defect, when a measure is out of its band."""
    beats = sc["beats"]
    script_ = json.load(open(os.path.join(ctx.run_dir, "script.json")))["script"] if os.path.exists(os.path.join(ctx.run_dir, "script.json")) else None
    sc_ = script_ or {"segments": []}
    clock = F.Clock(pack["words"], sc_)
    work = os.path.join(ctx.tmp, "filmcheck")
    os.makedirs(work, exist_ok=True)
    ids = pack.get("identity") or {}
    meta = {c["path"]: c for c in FIT.candidates(pack)}
    shots, cols, reads = [], [], []
    for k, p in enumerate(beats.get("pictures") or []):
        if p.get("kind") not in ("clip", "photo", "designed") or p.get("line") is None:
            continue
        f0, f1 = p["f"]
        if f1 - f0 < 2:
            continue
        try:
            mid = FIT._frame(film, ((f0 + f1) / 2) / F.FPS, os.path.join(work, f"m{k}.jpg"), 360)
        except Exception:  # noqa: BLE001
            continue
        c_, void = colourfulness(mid)
        cols.append(dict(f=p["f"], c=round(c_, 1), void=round(void, 3), mode=p.get("mode"), kind=p["kind"]))
        if p["kind"] == "designed" or not os.path.exists(str(p.get("path"))):
            continue
        tb = p.get("take_bounds") or ((0.0, 0.0) if p["kind"] == "photo" else None)
        idn = ids.get(FIT.take_key(p["path"], tb)) if tb is not None else None
        m = meta.get(p["path"]) or {}
        shots.append(dict(key=f"s{k}", drop=f"{os.path.basename(p['path'])}@{int(p['line'])}", path=p["path"], kind=p["kind"], line=int(p["line"]),
                          f=p["f"], src=p.get("src"), seconds=m.get("seconds"),
                          meta=m.get("meta") or dict(title=p.get("shows"), source=p.get("source")), identity=idn,
                          words=FIT.words_under(clock.words, f0, f1, F.FPS)))
        reads.append(dict(key=f"s{k}", frame=mid, identity=idn, meta=m.get("meta")))
    res, usd = FIT.audit(shots, sc_, work=work, cache_path=os.path.join(ctx.supply, "audit.json"), log=ctx.log) if sc_["segments"] else ([], 0.0)
    rd_, u2 = FIT.readable(reads, work=work, log=ctx.log) if reads else ({}, 0.0)
    usd += u2
    for r in res:
        rr = rd_.get(r["key"])
        r["readable"] = None if rr is None else rr["readable"]
        if rr is not None and not rr["readable"]:
            r["fit"] = False
            r["why"] = f"not readable in the film: {rr.get('why')}"
    bad = [r for r in res if not r["fit"]]
    drop_of = {x["key"]: x["drop"] for x in shots}
    fail_s = round(sum(r["f"][1] - r["f"][0] for r in bad) / F.FPS, 2)
    total_s = sum(r["f"][1] - r["f"][0] for r in res) / F.FPS
    vals = sorted(x["c"] for x in cols)
    m = beats.get("measured") or {}
    ramp = m.get("colour_turn_ramp_f")
    step = None
    if ramp is not None:
        try:
            a_ = FIT._frame(film, max(ramp - 1, 0) / F.FPS, os.path.join(work, "ramp_a.jpg"), 720)
            b_ = FIT._frame(film, (ramp + 8) / F.FPS, os.path.join(work, "ramp_b.jpg"), 720)
            step = [round(colourfulness(a_)[0], 1), round(colourfulness(b_)[0], 1)]
        except Exception:  # noqa: BLE001
            step = None
    prints = [x["void"] for x in cols if x.get("mode") in ("print", "window")]        # every framed picture's ground
    colour = dict(bw_shot_share=round(sum(1 for v in vals if v < 8) / max(len(vals), 1), 3),
                  colourfulness_median=vals[len(vals) // 2] if vals else None, colour_turn_step=step,
                  void_share=round(sum(prints) / len(prints), 3) if prints else None,
                  print_share=round(sum(1 for x in cols if x.get("mode") == "print") / max(len(cols), 1), 3),
                  print_window_share=round(sum(1 for x in cols if x.get("mode") in ("print", "window")) / max(len(cols), 1), 3),
                  bleed_share=bleed_share(beats)[0])
    try:
        motion = film_motion(film, beats.get("pictures") or [], pack["ffmpeg"])
    except Exception as e:  # noqa: BLE001 - an unmeasured film does not pass
        motion = dict(error=repr(e)[:200], static_shots=None, content_moving_share=None)
    try:
        punct = punctuation_measures(film, pack["ffmpeg"], beats.get("pictures") or [], ((beats.get("voice_f") or [0, 0])[1]) / F.FPS)
    except Exception as e:  # noqa: BLE001
        punct = dict(error=repr(e)[:200])
    l1 = json.load(open(sc["l1"])) if sc.get("l1") and os.path.exists(sc["l1"]) else {"items": [], "voices": {}}
    try:
        typ = type_measures(l1, beats, sc_)
    except Exception as e:  # noqa: BLE001
        typ = dict(error=repr(e)[:200])
    sound = None
    if music and clean and os.path.exists(str(music)) and os.path.exists(str(clean)):
        try:
            joints = json.load(open(os.path.join(ctx.supply, "joints.json"))) if os.path.exists(os.path.join(ctx.supply, "joints.json")) else []
            sound = sound_measures(music, clean, pack["ffmpeg"], l1, joints)
        except Exception as e:  # noqa: BLE001
            sound = dict(error=repr(e)[:200])
    import shutil
    shutil.rmtree(work, ignore_errors=True)
    rec = json.load(open(os.path.join(ctx.supply, "pack.json")))
    rec["record"].setdefault("spend", []).append(dict(stage="check", provider="openai", model=FIT.MODEL, what="film picture check", usd=round(usd, 4)))
    json.dump(rec, open(os.path.join(ctx.supply, "pack.json"), "w"), ensure_ascii=False, indent=1)
    defects = []
    if fail_s > 0:
        defects.append(f"pictures under words they do not carry or that do not read: {fail_s} s")
    if not (BW_SHARE[0] <= colour["bw_shot_share"] <= BW_SHARE[1]):
        defects.append(f"black and white on {colour['bw_shot_share']:.0%} of shots (bar {BW_SHARE[0]:.0%}-{BW_SHARE[1]:.0%})")
    if colour["colourfulness_median"] is None or colour["colourfulness_median"] < COLOUR_MEDIAN_MIN:
        defects.append(f"colourfulness median {colour['colourfulness_median']} (at least {COLOUR_MEDIAN_MIN})")
    if colour["void_share"] is not None and colour["void_share"] > VOID_MAX:
        defects.append(f"print frames {colour['void_share']:.0%} near-black (at most {VOID_MAX:.0%})")
    if motion.get("static_shots") is None or motion["static_shots"] > 0:
        defects.append(f"{motion.get('static_shots')} static shots in the film ({motion.get('static_s')} s)")
    if motion.get("content_moving_share") is None or motion["content_moving_share"] < MOTION_CONTENT_MIN:
        defects.append(f"content motion on {motion.get('content_moving_share')} of shot time (at least {MOTION_CONTENT_MIN})")
    if "error" not in punct:
        if not FLIPS_10S[0] <= punct["flips_10s"] <= FLIPS_10S[1]:
            defects.append(f"{punct['flips_10s']} polarity flips a 10 s (bar {FLIPS_10S[0]}-{FLIPS_10S[1]})")
        if not WHITE_10S[0] <= punct["white_10s"] <= WHITE_10S[1]:
            defects.append(f"{punct['white_10s']} white frames a 10 s (bar {WHITE_10S[0]}-{WHITE_10S[1]})")
        if punct.get("visible_cuts_10s") is not None and punct["visible_cuts_10s"] < VISIBLE_CUTS_MIN:
            defects.append(f"{punct['visible_cuts_10s']} visible cuts a 10 s (at least {VISIBLE_CUTS_MIN})")
        if punct["hash_repeat_share"] > HASH_REPEAT_MAX:
            defects.append(f"{punct['hash_repeat_share']:.0%} of shots repeat an earlier picture (at most {HASH_REPEAT_MAX:.0%})")
    type_ok = True
    if "error" not in typ:
        if not (DISPLAY_SHARE[0] <= typ["display_shot_share"] <= DISPLAY_SHARE[1]):
            defects.append(f"display type on {typ['display_shot_share']:.0%} of shots (bar {DISPLAY_SHARE[0]:.0%}-{DISPLAY_SHARE[1]:.0%})")
            type_ok = False
        for f_ in typ["figures"]:
            if not f_["cap_px"] or f_["cap_px"] < CAP_MIN_PX or f_["held_s"] < 1.5:
                defects.append(f"figure {f_['text']!r}: cap {f_['cap_px']} px, held {f_['held_s']} s (at least {CAP_MIN_PX} px, 1.5 s)")
                type_ok = False
        lag = typ.get("caption_lag_median_s")
        if lag is not None and not (CAPTION_LAG[0] <= lag <= CAPTION_LAG[1]):
            defects.append(f"caption ink lag {lag} s (bar {CAPTION_LAG[0]}-{CAPTION_LAG[1]} s)")
            type_ok = False
    sound_ok = True
    if sound and "error" not in sound:
        if not (MUSIC_SHARE[0] <= sound["music_runtime_share"] <= MUSIC_SHARE[1]):
            defects.append(f"music under {sound['music_runtime_share']:.0%} of the film (bar {MUSIC_SHARE[0]:.0%}-{MUSIC_SHARE[1]:.0%})")
            sound_ok = False
        if not (DROPS[0] <= sound["drops"]["count"] <= DROPS[1]):
            defects.append(f"{sound['drops']['count']} drops in the bed (bar {DROPS[0]}-{DROPS[1]})")
            sound_ok = False
    gates_ok = not defects
    return dict(picture_fit=dict(fail_s=fail_s, fail_share=round(fail_s / max(total_s, 1e-6), 3), usd=round(usd, 4),
                                 shots=[dict(key=r["key"], drop=drop_of.get(r["key"]), f=r["f"], line=r["line"], fit=r["fit"], readable=r.get("readable"),
                                             why=r.get("why"), shows=r.get("shows")) for r in res]),
                colour=colour, motion=motion, punctuation=punct, type=dict(typ, ok=type_ok), sound=dict(sound or {}, ok=sound_ok, measured=bool(sound)),
                defects=defects, gates_ok=gates_ok)
