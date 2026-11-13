"""Workflow 3 on the shell: topic and founder stories, narrated, on real pictures.

ideas   the writer finds stories on the web itself (writer.find_ideas), then each idea's pictures are probed on
        Wikimedia Commons and Openverse: an idea whose named things have no free photographs cannot be made
script  the writer writes the best idea in its style, every fact with a quote and its URL; each quote is fetched
        here from its own page and every number on screen must stand in a verified quote, or the stage fails
supply  the narrator (narrate tool, local, through heavy.sh) and the pictures (worker/broll_source: Commons and
        Openverse, checked by a vision model against what each must show, licence kept); faces found in each
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
import film as F  # noqa: E402
import pictures as PX  # noqa: E402
import writer  # noqa: E402

FORM = "short"


def cards():
    return {s["id"]: s for s in json.load(open(os.path.join(HERE, "styles.json")))["styles"]}


def ledger_rows(ctx):
    try:
        return json.load(open(os.path.join(ctx.state_root, "ledger.json"))).get(ctx.workflow, [])
    except (OSError, ValueError):
        return []


def able_cards(ctx):
    c = cards()
    allowed = [dict(r, **c.get(r["id"], {})) for r in ctx.styles_allowed()]
    able = [s for s in allowed if s.get("buildable") and s["id"] in F.STYLES]
    if ctx.args.get("style"):
        able = [s for s in able if s["id"] == ctx.args["style"]]
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


def ideas(ctx):
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
    able = {s["id"]: s for s in able_cards(ctx)}
    pick = next((i for i in ideas_out["ideas"] if i.get("style") in able and (i.get("supply") or {}).get("ok")), None)
    if not pick:
        raise RuntimeError("no idea with a buildable style and enough free pictures: " +
                           json.dumps([(i.get("title"), i.get("style"), (i.get("supply") or {}).get("covered")) for i in ideas_out["ideas"]])[:600])
    card = able[pick["style"]]
    spend, notes, rep, sc = [], ctx.args.get("notes"), None, None
    for attempt in range(2):
        sc, use = writer.write(pick, card, FORM, voices=card.get("voice"), notes=notes)
        spend.append(dict(stage="script", attempt=attempt, **use))
        rep = writer.verify(sc)
        n_words = writer.words_of(sc)
        lo, hi = writer.FORMS[FORM]["words"]
        problems = []
        if rep["facts_unverified"]:
            problems.append(f"these facts' quotes were not found on their pages when fetched: {rep['facts_unverified']}; "
                            "quote the page word for word, or use another source, or drop the fact")
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
    reg = next(s for s in ctx.styles_allowed() if s["id"] == card["id"])
    return dict(title=sc.get("title") or pick.get("title"), style=reg, window=None, hook_line=sc.get("hook_line"),
                close_line=sc.get("close_line"), direction=f"{card['name']}: {card['look']}", idea=pick.get("title"),
                idea_full=pick, script=sc, verify=rep, words=writer.words_of(sc), spend=spend)


# ----------------------------------------------------------------- supply

WHO_SYSTEM = ("You identify people in an archive photograph for a film editor. You get the photograph with numbered boxes on "
              "the faces a detector found, the photograph's own description from Wikimedia Commons (which often lists who stands "
              "where, row by row), and the names a film needs. Match each name to a box only when the description's row and "
              "position, or something plainly visible, places them there. Never guess from a face: when it is not clear, answer null.")


def who_is_who(ctx, photo, people):
    """{name: face index or None}, from the description's own positions, read by a model on the numbered photograph."""
    import base64
    from PIL import Image, ImageDraw, ImageFont
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
    p = os.path.join(ctx.tmp, "who.jpg")
    im.save(p, quality=88)
    data = base64.standard_b64encode(open(p, "rb").read()).decode()
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}},
               {"type": "text", "text": f"Description from Commons: {photo.get('description', '')[:1400]}\n\nPeople the film names, with where the "
                                        f"writer believes they are: {json.dumps(people, ensure_ascii=False)}\n\nThere are {len(photo['faces'])} boxes, numbered "
                                        "0 upwards, rows top to bottom and left to right. Answer JSON only: {\"who\": {name: box number or null}, "
                                        "\"why\": {name: a few words}}"}]
    text, use = writer.ask(WHO_SYSTEM, content, effort="high", max_tokens=6000, what="who is who")
    got = writer.json_block(text)
    who = {}
    for n, v in (got.get("who") or {}).items():
        who[n] = int(v) if isinstance(v, (int, float)) and 0 <= int(v) < len(photo["faces"]) else None
    return who, got.get("why") or {}, use


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
    nar = PX.narrate(writer.narration_script(sc, band), os.path.join(sup_dir, "narration"), band=band, label=f"wf3-narrate-{ctx.run_id}", env=env)
    ctx.log(f"narration {nar['duration']} s, gate {nar['gate']}")
    pack = dict(sid=ctx.sid, words=nar["words"], narration=nar["wav"], ffmpeg=PX.FF, sounds=PX.sound_facts(), pictures={}, credits=[])
    used = set()
    for s in sc["segments"]:
        used.update(s.get("pictures") or [])
        if (s.get("window") or {}).get("subject"):
            used.add(s["window"]["subject"])
    named = [n for n in sc.get("named") or [] if n.get("name") in used]
    if sid != "one-photograph" and named:
        got, reports, usd = PX.pictures(named, os.path.join(sup_dir, "pics"), per=4 if sid == "character-sheet" else 2)
        spend.append(dict(stage="supply", provider="anthropic", model="claude-haiku-5-5", what="picture check", usd=usd))
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
        ph["faces"] = PX.faces(ph["path"], score=0.55)
        if len(ph["faces"]) < 2:
            raise RuntimeError(f"the photograph has {len(ph['faces'])} faces a detector can find")
        who, why, use = who_is_who(ctx, ph, sc["photo"].get("people") or [])
        spend.append(dict(stage="supply", **use))
        ph["who"], ph["who_why"] = who, why
        pack["photo"] = ph
        pack["credits"] = [dict(name=ph["title"], title=ph["title"], licence=ph["licence"], credit=ph.get("credit"), page=ph.get("page"))]
        por = sc.get("portrait") or {}
        fi = who.get(por.get("name"))
        if fi is not None:
            b = ph["faces"][fi]["box"]
            cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            half = (b[2] - b[0]) * 0.95
            hh = half * ph["w"] / ph["h"]
            reg = [max(cx - half, 0), max(cy - hh * 0.85, 0), min(cx + half, 1), min(cy + hh * 1.15, 1)]
            si = int(por.get("segment", 0))
            sentence = sc["segments"][si]["say"] if si < len(sc["segments"]) else sc["segments"][0]["say"]
            path, rows = F.microtext_image(ph["path"], reg, sentence, os.path.join(sup_dir, "photo", "micro.jpg"))
            ey = (ph["faces"][fi]["eyes"][0][1] + ph["faces"][fi]["eyes"][1][1]) / 2
            row_y = min(max((ey - reg[1]) / max(reg[3] - reg[1], 1e-6), 0.2), 0.8)
            pack["microtext"] = dict(path=path, size=3000, region=reg, row_y=row_y)
    if sid == "whole-page":
        w = json.load(open(nar["words"]))["words"]
        loud, pitch = F.voice_measures(nar["wav"], w, PX.FF)
        pack["loudness"], pack["pitch"] = loud, pitch
    rec = dict(narration_s=nar["duration"], voice=nar.get("voice"), gate=nar["gate"], pictures=sum(len(v) for v in pack["pictures"].values()),
               subjects=len(pack["pictures"]), missing=pack.get("missing"), spend=spend)
    json.dump(dict(pack=pack, record=rec), open(pk, "w"), ensure_ascii=False, indent=1)
    return rec


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
    knobs = {k: v for k, v in d.items() if k not in ("script", "why")}
    sys.path[:0] = [p for p in env_path().split(os.pathsep) if p not in sys.path]
    os.environ["PATH"] = os.path.dirname(pack["ffmpeg"]) + os.pathsep + os.environ.get("PATH", "")
    l1, beats = F.STYLES[script["style"]["id"]](sc, pack, knobs)
    if d.get("script"):
        rep = writer.verify(sc)
        if not rep["ok"]:
            raise RuntimeError(f"the director's change breaks the facts: {rep['numbers_unbacked']} {rep['facts_unverified']}")
    out = os.path.join(ctx.supply, "l1.json")
    json.dump(l1, open(out, "w"), ensure_ascii=False)
    json.dump(beats, open(os.path.join(ctx.supply, "beats.json"), "w"), indent=1)
    taste = F.taste_check(l1, beats)
    look = os.path.join(ctx.run_dir, "look")
    frames = sorted({int(f) for f in beats["look"] if 0 <= int(f) < l1["dur_f"]})
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
    return dict(l1=out, beats={k: v for k, v in beats.items() if k not in ("look",)}, taste=taste, frames=l1["dur_f"], items=len(l1["items"]),
                direction=d, spend=[])


def render(ctx, script, sc):
    out = os.path.join(ctx.supply, "film_engine.mp4")
    cache = os.path.join(ctx.tmp, "scene-cache")
    l1 = json.load(open(sc["l1"]))
    pack = load_pack(ctx)
    sound = os.path.join(ctx.supply, "sound.m4a")
    mix = F.audio_master(pack["ffmpeg"], l1, pack["narration"], sound, ctx.tmp) if not os.path.exists(sound) else {}
    if not os.path.exists(out):
        log = chain.sh(ctx, ["env", f"PYTHONPATH={env_path()}", f"VALMERA_SCENE_CACHE={cache}", chain.PY, "-m", "scene.run", "render", "--l1", sc["l1"],
                             "--work", ctx.supply, "--out", out + ".part.mp4", "--tier", "master", "--strict", "--cache", cache, "--audio", sound],
                       label=f"wf3-render-{ctx.run_id}", heavy=True, timeout=7200)
        os.replace(out + ".part.mp4", out)
        try:
            j = json.loads(log[log.index("{"):log.rindex("}") + 1])
        except ValueError:
            j = {}
        json.dump(dict(j, mix=mix), open(out + ".log.json", "w"))
    j = json.load(open(out + ".log.json")) if os.path.exists(out + ".log.json") else {}
    return dict(engine="scene", film=out, engine_check=dict(frames=j.get("frames"), seconds=j.get("seconds"), audio=j.get("mix"),
                                                              degraded=j.get("degraded"), warnings=j.get("warnings")))


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
                        pictures=sc["beats"].get("pictures"), missing_pictures=pack.get("missing"))
    rec = json.load(open(os.path.join(ctx.supply, "pack.json")))["record"]
    c["stories"]["narration"] = dict(seconds=rec.get("narration_s"), voice=rec.get("voice"), gate=rec.get("gate"))
    c["ok"] = bool(c["ok"] and sc["taste"]["ok"])
    return r
