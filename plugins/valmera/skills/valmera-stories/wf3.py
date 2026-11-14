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
            sc, ann = annotate_script(ctx, sc, prev.get("idea_full"), spend)
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
        sc, ann = annotate_script(ctx, sc, pick, spend)
    reg = registry_row(card)
    return dict(title=sc.get("title") or pick.get("title"), style=reg, window=None, hook_line=sc.get("hook_line"),
                close_line=sc.get("close_line"), direction=f"{card['name']}: {card['look']}", idea=pick.get("title"),
                idea_full=pick, script=sc, verify=rep, words=writer.words_of(sc), annotate=ann, spend=spend)


def needs_annotate(sc):
    """A script without the picture brief or the display anchors (a delivered film from before 14 Nov)."""
    segs = sc.get("segments") or []
    return any(not s.get("see") or any(isinstance(x, str) or not isinstance(x, dict) or "word_i" not in x for x in s.get("show") or [])
               or not s.get("phrases") for s in segs)


def annotate_script(ctx, sc, idea_full, spend):
    """The final script's picture brief, devices, display anchors and caption phrases (writer.annotate, OpenAI only).
    The words never change. A failure keeps the script as it stands and is recorded. --args '{"annotate": false}' skips it."""
    if ctx.args.get("annotate") is False:
        return sc, None
    base = writer.verify(sc)            # pages read once in this process: a second read is the cache

    def held(s):
        r = writer.verify(s)            # the annotation can add figures; it can never touch a fact
        return dict(r, ok=bool(r["facts"]) and not r["numbers_unbacked"] and set(r["facts_unverified"]) <= set(base["facts_unverified"]))
    try:
        sc2, rep = writer.annotate(sc, idea_full, verify_fn=held, log=ctx.log)
    except Exception as e:  # noqa: BLE001
        ctx.log(f"annotate: {e!r}"[:300])
        return sc, dict(ok=False, error=repr(e)[:300])
    spend += [dict(stage="script", **u) for u in rep.get("spend") or []]
    json.dump(dict(script=sc2, report=rep), open(os.path.join(ctx.run_dir, "script_annotate.json"), "w"), ensure_ascii=False, indent=1)
    ctx.log(f"annotate: ok {rep['ok']}, devices {rep['device_share_words']} of the words, problems {len(rep['problems'])}")
    if not rep["verify"].get("ok"):
        ctx.log("annotate: the annotated script does not verify; the script stands as it was")
        return sc, dict(rep, kept=False)
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
    merged = dict(recs[0], lines=rows, merged_from=srcs,
                  counts=dict(lines=len(rows), lines_with_footage=sum(1 for r in rows if r["clips"]), clips=sum(len(r["clips"]) for r in rows)),
                  credits=[dict(line=r["i"], url=c["source"]["url"], channel=c["source"].get("channel"), title=c["source"].get("title"),
                                time_range=c.get("time_range")) for r in rows for c in r["clips"]],
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
        got, reports, usd = PX.pictures(named, os.path.join(sup_dir, "pics"), per=8 if sid in R.STYLES else 4 if sid == "character-sheet" else 2)
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
        missing = [n["name"] for n in named if not got.get(n["name"])]
        if len(pack["pictures"]) < 2:
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
    foot = chain.footage(ctx, script, durs, anchors=anchors, **reel_kw)     # real video per line
    if foot:
        fdir = os.path.join(sup_dir, "footage")
        pack["footage"] = [dict(i=ln["i"], clips=[clip_facts(dict(c, path=os.path.join(fdir, c["file"]))) for c in ln["clips"]]) for ln in foot["lines"]]
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
    """Each clip's content motion, its inner cuts (reel.clip_motion: signals) and its take group (reel.take_groups)."""
    clips = [c for ln in pack.get("footage") or [] for c in ln["clips"]]
    for c in clips:
        if "motion" not in c:
            m = R.clip_motion(c["path"], PX.FF, landscape=int(c.get("w") or 0) > int(c.get("h") or 1)) or {}
            c["motion"] = dict(p90=m.get("p90", 0.0), curve=m.get("curve") or [], global_px=m.get("global_px", 0.0))
            c["cuts"] = m.get("cuts") or []
    groups = R.take_groups(clips)
    for c in clips:
        c["take_group"] = groups.get(c["path"], c["path"])


def fit_report(pack, sc, durs):
    """Per line: the distinct fitting pictures it has against the ceil(seconds / PICTURE_S) it wants."""
    fits = pack.get("fit") or {}
    groups = {c["path"]: c.get("take_group") or c["path"] for ln in pack.get("footage") or [] for c in ln["clips"]}
    rows = []
    for i in range(len(sc["segments"])):
        have = {groups.get(p, p) for p, lines in fits.items() if i in lines}
        need = max(1, int(-(-float(durs[i] if i < len(durs) else 1.0) // PICTURE_S)))
        rows.append(dict(i=i, have=len(have), need=need))
    return rows


def fit_supply(ctx, script, pack, durs, spend, anchors, reel_kw):
    """Every picture of the supply judged against the lines it may carry (fit.verdicts, OpenAI vision): pack["fit"] is
    what the cutter may put on each line. A line with fewer distinct fitting pictures than it wants gets ONE top-up
    footage call (those lines only, never cutting again what the first call cut), merged per line and judged too."""
    sc = script["script"]
    if not all(s_.get("see") for s_ in sc["segments"]):
        ctx.log("fit: the script has no picture brief on every line; the cut runs without verdicts")
        return
    measure_clips(pack)
    cache = os.path.join(ctx.supply, "fit.json")
    table, usd, calls = FIT.verdicts(FIT.candidates(pack), sc, cache_path=cache, work=os.path.join(ctx.tmp, "fit"), log=ctx.log)
    pack["fit"], pack["fit_verdicts"] = FIT.fits(table), table
    rows = fit_report(pack, sc, durs)
    short = [r["i"] for r in rows if r["have"] < r["need"]]
    topup = None
    if short and "footage" in pack and os.environ.get("VALMERA_FOOTAGE", "1") != "0":
        first = os.path.join(ctx.supply, "footage", "footage.json")
        foot2 = chain.footage(ctx, script, durs, anchors=anchors, out_name="footage2", lines=short, avoid=[first], **reel_kw)
        if foot2:
            fdir = os.path.join(ctx.supply, "footage2")
            by_i = {int(ln["i"]): ln for ln in pack["footage"]}
            added = 0
            for ln in foot2.get("lines") or []:
                row = by_i.setdefault(int(ln["i"]), dict(i=int(ln["i"]), clips=[]))
                for c in ln.get("clips") or []:
                    row["clips"].append(clip_facts(dict(c, path=os.path.join(fdir, c["file"]))))
                    added += 1
            pack["footage"] = [by_i[k] for k in sorted(by_i)]
            pack["credits"] = (pack.get("credits") or []) + [dict(name=c["title"], title=c["title"], licence="footage, not licensed (owner's decision)",
                                                                  credit=c["channel"], page=c["url"], time_range=c["time_range"], line=c["line"])
                                                             for c in foot2.get("credits") or []]
            spend.append(dict(stage="supply", provider="openai", model="gpt-6-sol low + gpt-4o-transcribe", what="footage top-up",
                              usd=(foot2.get("spend") or {}).get("usd") or 0))
            for ln in pack["footage"]:
                for c in ln["clips"]:
                    if c["path"] not in (pack.get("talking") or {}):
                        t, score, still = R.talking(c, PX.FF, windows=True)
                        pack.setdefault("talking", {})[c["path"]] = dict(talking=t, score=score, still=still)
            measure_clips(pack)
            t2, u2, c2 = FIT.verdicts(FIT.candidates(pack), sc, cache_path=cache, work=os.path.join(ctx.tmp, "fit"), log=ctx.log)
            usd, calls = usd + u2, calls + c2
            pack["fit"], pack["fit_verdicts"] = FIT.fits(t2), t2
            rows2 = fit_report(pack, sc, durs)
            topup = dict(lines=short, clips=added, still_short=[r["i"] for r in rows2 if r["have"] < r["need"]])
            rows = rows2
    spend.append(dict(stage="supply", provider="openai", model=FIT.MODEL, what="picture check", usd=round(usd, 4), calls=calls))
    pack["fit_report"] = dict(lines=rows, topup=topup)
    n_ok = sum(len(v) for v in pack["fit"].values())
    ctx.log(f"fit: {n_ok} (picture, line) pairs usable; lines short of pictures {[r['i'] for r in rows if r['have'] < r['need']]}"
            + (f"; top-up {topup}" if topup else ""))


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
    knobs.setdefault("bed", bool(ctx.args.get("song")) or chain.wants_music(ctx))       # the film gets music: the sounds are designed for a film with a bed
    sys.path[:0] = [p for p in env_path().split(os.pathsep) if p not in sys.path]
    os.environ["PATH"] = os.path.dirname(pack["ffmpeg"]) + os.pathsep + os.environ.get("PATH", "")
    pack.setdefault("work", os.path.join(ctx.supply, "finish"))
    build = BUILD[script["style"]["id"]]
    l1, beats = build(sc, pack, knobs)
    taste = F.taste_check(l1, beats)
    if not taste["ok"] and script["style"]["id"] in R.STYLES and not ctx.args.get("look", True):
        # no director stops this run: the plan is built again without the display words, then without collages
        for safer in (dict(display=False), dict(display=False, collage=0.0)):
            ctx.log("taste: " + "; ".join(taste["problems"][:4]) + f"; built again with {safer}")
            l1, beats = build(sc, pack, dict(knobs, **safer))
            taste = F.taste_check(l1, beats)
            if taste["ok"]:
                break
    if script["style"]["id"] in R.STYLES and pack.get("fit") is not None:
        l1, beats, taste = stories_gates(ctx, sc, pack, build, knobs, l1, beats, taste)
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
        joints = joints_of(sc, pack)
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


def joints_of(sc, pack):
    """[{t, kind, last_word_t, next_first_word_t}] at the story's section joints (the writer's beats: the turn, the low
    point, the payoff, the close), on the programme's clock from the narration's own words; and {kind: voice, t0, t1}
    for each real voice cut in, where the bed dips under it."""
    clock = F.Clock(pack["words"], sc)
    out = []
    for i, s_ in enumerate(sc["segments"]):
        kind = s_.get("beat")
        if i == 0 or kind not in ("turn", "low", "payoff", "close"):
            continue
        prev, cur = clock.seg_words(i - 1), clock.seg_words(i)
        if not prev or not cur:
            continue
        out.append(dict(t=round(cur[0]["t0"], 3), kind=kind, last_word_t=round(prev[-1]["t1"], 3), next_first_word_t=round(cur[0]["t0"], 3), seg=i))
    for v in pack.get("voices") or []:
        if v and v.get("start") is not None:
            out.append(dict(t=round(float(v["start"]) + F.LEAD_F / F.FPS, 3), kind="voice", t0=round(float(v["start"]) + F.LEAD_F / F.FPS, 3),
                            t1=round(float(v["end"]) + F.LEAD_F / F.FPS, 3)))
    return out


AUDIT_ROUNDS = 3
BW_SHARE = (0.04, 0.35)      # black and white on this share of shots, measured on the plan; outside it the devices are halved once
REPEAT_MAX = 0.05            # repeats by distinct take


def planned_shots(beats, pack, clock):
    """The plan's base pictures as the audit reads them: each with the words spoken under it and its source's text."""
    meta = {}
    for c in FIT.candidates(pack):
        meta[c["path"]] = c
    out = []
    for k, p in enumerate(beats.get("pictures") or []):
        if p.get("kind") not in ("clip", "photo") or p.get("line") is None:
            continue
        c = meta.get(p["path"]) or {}
        out.append(dict(key=f"{os.path.basename(p['path'])}@{int(p['line'])}", path=p["path"], kind=p["kind"], line=int(p["line"]), f=p["f"],
                        src=p.get("src"), seconds=c.get("seconds"), meta=c.get("meta") or {},
                        words=FIT.words_under(clock.words, p["f"][0], p["f"][1], F.FPS)))
    return out


def stories_gates(ctx, sc, pack, build, knobs, l1, beats, taste):
    """The story reel's own gates on the plan (stories only; WF2 builds on this module without them):
    audit    every planned base shot judged against the words under it and its segment's brief (fit.audit, OpenAI
             vision); a failing shot is dropped on that line only ("file@line") and the plan built again, at most
             AUDIT_ROUNDS times
    colour   black and white on BW_SHARE of shots, else the writer's devices halved once
    repeats  at most REPEAT_MAX of shots repeat a take (counted by distinct take): over it the plan breaks a taste rule"""
    clock = F.Clock(pack["words"], sc)
    drop = list(knobs.get("drop") or [])
    rounds, failed, usd = [], [], 0.0
    if ctx.args.get("audit", True) is not False:
        for rnd in range(AUDIT_ROUNDS):
            shots = planned_shots(beats, pack, clock)
            res, u = FIT.audit(shots, sc, work=os.path.join(ctx.tmp, "audit"), cache_path=os.path.join(ctx.supply, "audit.json"), log=ctx.log)
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
        whole = json.load(open(os.path.join(ctx.supply, "pack.json")))
        whole["record"].setdefault("spend", []).append(dict(stage="score", provider="openai", model=FIT.MODEL, what="shot audit", usd=round(usd, 4)))
        json.dump(whole, open(os.path.join(ctx.supply, "pack.json"), "w"), ensure_ascii=False, indent=1)
    base = [p for p in beats.get("pictures") or [] if p.get("kind") in ("clip", "photo", "designed")]
    bw = sum(1 for p in base if p.get("finish") in ("mono", "gate")) / max(len(base), 1)
    colour = dict(bw_shot_share_plan=round(bw, 3), rebuilt=False)
    if bw > BW_SHARE[1]:
        knobs = dict(knobs, device_budget=0.5)
        l1, beats = build(sc, pack, knobs)
        taste = F.taste_check(l1, beats)
        base = [p for p in beats.get("pictures") or [] if p.get("kind") in ("clip", "photo", "designed")]
        colour.update(rebuilt=True, bw_shot_share_after=round(sum(1 for p in base if p.get("finish") in ("mono", "gate")) / max(len(base), 1), 3))
        ctx.log(f"colour: black and white on {bw:.0%} of shots; built again with the devices halved")
    m = beats.setdefault("measured", {})
    m["audit"] = dict(rounds=rounds, failed=failed[:60], usd=round(usd, 4))
    m["colour_plan"] = colour
    if float(m.get("repeat_share") or 0) > REPEAT_MAX:
        taste = dict(taste, ok=False, problems=list(taste.get("problems") or []) + [f"{m['repeat_share']:.0%} of shots repeat a take (at most {REPEAT_MAX:.0%})"])
    if beats.get("display_dropped") and any(x.get("role") == "figure" for x in beats["display_dropped"]):
        taste = dict(taste, ok=False, problems=list(taste.get("problems") or []) + ["the payoff's figure was not placed: " + json.dumps(beats["display_dropped"])[:200]])
    return l1, beats, taste


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


def check(ctx, rd, sc):
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
    if ctx.workflow == "wf3" and pack.get("fit") is not None:
        # the story reel's own measures on the rendered programme (WF2 calls this check without them)
        fc = film_checks(ctx, rd["film"], sc, pack)
        c["stories"].update(fc)
        c["ok"] = bool(c["ok"] and fc["picture_fit"]["fail_s"] == 0)
    return r


def voices_record(ctx, pack):
    """The real voices this film tried, placed and why any was not."""
    tried = []
    for name in ("footage", "footage2"):
        try:
            tried += json.load(open(os.path.join(ctx.supply, name, "footage.json"))).get("voices") or []
        except (OSError, ValueError):
            pass
    placed = [v for v in pack.get("voices") or [] if v]
    rej = [str(v.get("why") or v.get("reason") or "not placed") for v in tried if v.get("file") not in {p.get("file") for p in placed}]
    return dict(tried=len(tried), placed=len(placed), rejected=rej[:10])


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


def film_checks(ctx, film, sc, pack):
    """On the rendered programme, per shot: three frames judged against the words under it (fit.audit), and the
    middle frame's colourfulness. picture_fit {fail_s, fail_share, shots}; colour {bw_shot_share, colourfulness_median,
    colour_turn_step, void_share on print frames}."""
    beats = sc["beats"]
    script_ = json.load(open(os.path.join(ctx.run_dir, "script.json")))["script"] if os.path.exists(os.path.join(ctx.run_dir, "script.json")) else None
    sc_ = script_ or {"segments": []}
    clock = F.Clock(pack["words"], sc_)
    work = os.path.join(ctx.tmp, "filmcheck")
    shots, cols = [], []
    for k, p in enumerate(beats.get("pictures") or []):
        if p.get("kind") not in ("clip", "photo", "designed") or p.get("line") is None:
            continue
        f0, f1 = p["f"]
        if f1 - f0 < 2:
            continue
        sh = dict(key=f"s{k}", path=film, kind="clip", line=int(p["line"]), f=p["f"], src=[f0 / F.FPS, f1 / F.FPS], seconds=None,
                  meta=dict(title=p.get("shows"), source=p.get("source")), words=FIT.words_under(clock.words, f0, f1, F.FPS))
        try:
            sh["frames"] = FIT.frames_of(sh, work)
        except Exception:  # noqa: BLE001
            continue
        c_, void = colourfulness(sh["frames"][1])
        cols.append(dict(f=p["f"], c=round(c_, 1), void=round(void, 3), mode=p.get("mode"), kind=p["kind"]))
        if p["kind"] != "designed":
            shots.append(sh)
    res, usd = FIT.audit(shots, sc_, work=work, log=ctx.log) if sc_["segments"] else ([], 0.0)
    bad = [r for r in res if not r["fit"]]
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
    prints = [x["void"] for x in cols if x.get("mode") == "print"]
    import shutil
    shutil.rmtree(work, ignore_errors=True)
    rec = json.load(open(os.path.join(ctx.supply, "pack.json")))
    rec["record"].setdefault("spend", []).append(dict(stage="check", provider="openai", model=FIT.MODEL, what="film picture check", usd=round(usd, 4)))
    json.dump(rec, open(os.path.join(ctx.supply, "pack.json"), "w"), ensure_ascii=False, indent=1)
    return dict(picture_fit=dict(fail_s=fail_s, fail_share=round(fail_s / max(total_s, 1e-6), 3), usd=round(usd, 4),
                                 shots=[dict(key=r["key"], f=r["f"], line=r["line"], fit=r["fit"], why=r.get("why"), shows=r.get("shows")) for r in res]),
                colour=dict(bw_shot_share=round(sum(1 for v in vals if v < 8) / max(len(vals), 1), 3),
                            colourfulness_median=vals[len(vals) // 2] if vals else None, colour_turn_step=step,
                            void_share=round(sum(prints) / len(prints), 3) if prints else None))
