"""Workflow 4 on the shell: long-form voiceover stories, 10-20 minutes, narrated, on real pictures and designed pages.

ideas   the writer finds stories on the web itself (story.find_ideas); the named things of the best ideas are probed on
        Wikimedia Commons and Openverse: an idea whose things have no free pictures cannot be made
script  the researcher builds a dossier of quoted facts (story.dossier); every quote is fetched here from its own page and
        only verified facts are kept; the writer writes chapters from those facts alone (story.write), and the script is
        measured: cited facts verified, every printed figure inside a verified quote, length inside the form
supply  the narrator (narrate tool, local, band 04, through heavy.sh, its VOICE gate with the spoken-form listener) and,
        while it speaks, the pictures (worker/broll_source's named route, checked by a vision model, licence kept) and the
        designed pages (design.py: documents from the quotes, maps on NASA's Blue Marble, timelines, charts, figures)
score   film_long.py bakes the scene (L1): chapters, the ledger, prints landing on their words, picture density from each
        chapter's pace, the camera breathing; the taste rules as numbers; stills of the beats for the look
render  the film's sound (narration and a few library sounds on their events, lane J's master: -14 LUFS, under -1 dBTP;
        never music), then Valmera's engine (worker/scene) at 1920x1080, through heavy.sh
check   the shell's brand finish (watermark, end card) and checks, then this workflow's: length 10-20 min, picture changes
        per 10 s per chapter, the longest single framing, the VOICE gate, the credits
"""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
STORIES = os.path.join(os.path.dirname(HERE), "valmera-stories")
sys.path.insert(0, HERE)
sys.path.insert(0, STORIES)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "_shell"))
import pictures as PX  # noqa: E402  lane O's supply: narrate, pictures, faces, commons_file
import story  # noqa: E402
import ear  # noqa: E402

STYLE = "the-ledger"


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
    able = [s for s in allowed if s.get("buildable")]
    if ctx.args.get("style"):
        able = [s for s in able if s["id"] == ctx.args["style"]]
    return able


def probe(idea, n=14):
    """How many free pictures the idea's named things have (unchecked candidates, 900 px or more)."""
    B = PX._worker()

    def one(nm):
        s = dict(name=nm.get("name"), searches=(nm.get("searches") or [nm.get("name")])[:3], must_show=nm.get("must_show") or nm.get("name"))
        try:
            cands, _ = B.search_named(s, kind=B.KIND_PHOTO, count=6)
        except Exception:  # noqa: BLE001 - a subject that cannot be searched counts as none
            cands = []
        big = [c for c in cands if max(int(c.get("width") or 0), int(c.get("height") or 0)) >= 900]
        return dict(name=nm.get("name"), found=len(big))

    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(one, (idea.get("named") or [])[:n]))
    covered = sum(1 for r in rows if r["found"])
    pics = sum(min(r["found"], 4) for r in rows)
    return dict(subjects=rows, covered=covered, pictures=pics, ok=covered >= max(6, int(0.6 * len(rows))))


def ideas(ctx):
    able = able_cards(ctx)
    if not able:
        raise RuntimeError(f"no buildable style outside the last three ({ctx.styles_last()})")
    card = able[0]
    avoid = [r.get("idea") for r in ledger_rows(ctx) if r.get("idea")]
    got, use = story.find_ideas(card, avoid=avoid, line=ctx.args.get("line"))
    for i in got:
        i["style"] = card["id"]
    got.sort(key=lambda i: -float(i.get("strength") or 0))
    for i in got[:3]:
        i["supply"] = probe(i)
        ctx.log(f"probe {i.get('title')}: {i['supply']['covered']}/{len(i['supply']['subjects'])} subjects, {i['supply']['pictures']} pictures")
    got.sort(key=lambda i: (not (i.get("supply") or {}).get("ok"), -(float(i.get("strength") or 0) + min((i.get("supply") or {}).get("pictures", 0), 40) / 10)))
    srcs = sorted({u for i in got for u in i.get("sources") or []})
    return dict(source=dict(kind="web research by the writer model", origin="web_search", sources=srcs[:60], searches=use.get("web_searches")),
                ideas=got, chosen=None, spend=[dict(stage="ideas", **use)])


def script(ctx, ideas_out):
    able = {s["id"]: s for s in able_cards(ctx)}
    pick = next((i for i in ideas_out["ideas"] if i.get("style") in able and (i.get("supply") or {}).get("ok")), None)
    if not pick:
        raise RuntimeError("no idea with enough free pictures: " + json.dumps([(i.get("title"), (i.get("supply") or {}).get("covered")) for i in ideas_out["ideas"]])[:600])
    card = able[pick["style"]]
    spend = []
    dp = os.path.join(ctx.run_dir, "dossier.json")
    if os.path.exists(dp):
        dj = json.load(open(dp))
        dos, drep = dj["dossier"], dj["verify"]
    else:
        raw, use = story.dossier(pick)
        spend.append(dict(stage="script", what="dossier", **{k: v for k, v in use.items() if k != "what"}))
        dos, drep = story.check_dossier(raw)
        json.dump(dict(dossier=dos, verify=drep, raw_facts=len(raw.get("facts") or [])), open(dp, "w"), ensure_ascii=False, indent=1)
    ctx.log(f"dossier: {drep['verified']}/{drep['total']} facts verified on their pages")
    if drep["verified"] < 25:
        raise RuntimeError(f"only {drep['verified']} facts verified; a long film needs at least 25")
    notes, sc, rep = ctx.args.get("notes"), None, None
    for attempt in range(3):
        sc, use = story.write(pick, dos, card, voices=card.get("voice"), notes=notes)
        spend.append(dict(stage="script", attempt=attempt, **use))
        rep = story.check_script(sc, dos)
        json.dump(dict(script=sc, check=rep), open(os.path.join(ctx.run_dir, f"script_attempt{attempt}.json"), "w"), ensure_ascii=False, indent=1)
        ctx.log(f"script attempt {attempt}: {rep['words']} words, {rep['segments']} segments, problems {rep['problems'][:4]}")
        if rep["ok"]:
            break
        notes = "\n".join(rep["problems"])
    if not rep["ok"]:
        raise RuntimeError(f"the script does not hold: {rep['problems'][:6]}")
    if ctx.args.get("ear") is not False:
        # the held script rewritten for the ear (stories/ear.py: each chapter asks its question aloud, short spoken lines,
        # the stress word placed), audited for facts by a second call, then held to the same script check
        try:
            sc2, er = ear.for_ear(sc, "long-form", log=ctx.log)
            spend += [dict(stage="ear", **u) for u in er.get("spend") or []]
            json.dump(dict(script=sc2, report=er), open(os.path.join(ctx.run_dir, "script_ear.json"), "w"), ensure_ascii=False, indent=1)
            if er.get("ok"):
                rep2 = story.check_script(sc2, dos)
                if rep2["ok"]:
                    sc, rep = sc2, rep2
                else:
                    ctx.log(f"ear: the rewrite fails the script check {rep2['problems'][:3]}; the script stands")
        except Exception as e:  # noqa: BLE001
            ctx.log(f"ear: {e!r}"[:300])
    reg = next(s for s in ctx.styles_allowed() if s["id"] == card["id"])
    facts = {f["id"]: f for f in dos["facts"]}
    used = sorted({fid for s in sc["segments"] for fid in s.get("facts") or []})
    return dict(title=sc.get("title") or pick.get("title"), style=reg, window=None, hook_line=sc["segments"][0]["say"],
                close_line=(sc.get("close") or {}).get("line") or sc["segments"][-1]["say"], direction=f"{card['name']}: {card['look']}",
                idea=pick.get("title"), idea_full=pick, script=sc, check=rep, dossier=dos, dossier_verify=drep,
                facts_cited=[dict(id=k, claim=facts[k].get("claim"), quote=facts[k].get("quote"), url=facts[k].get("url")) for k in used if k in facts],
                words=rep["words"], spend=spend)


# ----------------------------------------------------------------- supply, score, render, check: film_long.py

def supply(ctx, script):
    import film_long
    return film_long.supply(ctx, script)


def score(ctx, script):
    import film_long
    return film_long.score(ctx, script)


def render(ctx, script, sc):
    import film_long
    return film_long.render(ctx, script, sc)


def check(ctx, rd, sc):
    import film_long
    return film_long.check(ctx, rd, sc)
