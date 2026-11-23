"""Autonomous researched films: Easton, real archive footage and voices, SND3,
MUSIC2 per story chapter, and measured delivery gates. The shell owns checkpoints.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STORIES = os.path.join(os.path.dirname(HERE), "valmera-stories")
sys.path.insert(0, HERE)
sys.path.insert(0, STORIES)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "_shell"))
import pictures as PX  # noqa: E402  lane O's supply: narrate, pictures, faces, commons_file
import importlib.util
_spec = importlib.util.spec_from_file_location("longform_story", os.path.join(HERE, "story.py"))
story = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(story)
import ear  # noqa: E402
import delivered as DL  # noqa: E402

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


def probe(idea):
    import footage_box
    return footage_box.probe(idea)


def ideas(ctx):
    able = able_cards(ctx)
    if not able:
        raise RuntimeError(f"no buildable style outside the last three ({ctx.styles_last()})")
    card = able[0]
    avoid = [r.get("idea") for r in ledger_rows(ctx) if r.get("idea")]
    got, use = story.find_ideas(card, avoid=avoid, line=ctx.args.get("line"), delivered=DL.prompt_block(ctx.workflow))
    for i in got:
        i["style"] = card["id"]
    got.sort(key=lambda i: -float(i.get("strength") or 0))
    for i in got[:3]:
        i["supply"] = probe(i)
        ctx.log(f"probe {i.get('title')}: {i['supply']['covered']}/{len(i['supply']['subjects'])} subjects, {i['supply']['clips']} ranked footage sources")
    # Among the ideas with enough real footage, the story decides; footage only breaks a near tie (16 Nov: up to 4 points
    # of footage on a 10-point story scale let an archive-rich procedure beat a stronger story).
    got.sort(key=lambda i: (not (i.get("supply") or {}).get("ok"), -(float(i.get("strength") or 0) + min((i.get("supply") or {}).get("clips", 0), 40) / 80)))
    srcs = sorted({u for i in got for u in i.get("sources") or []})
    return dict(source=dict(kind="web research by the writer model", origin="web_search", sources=srcs[:60], searches=use.get("web_searches")),
                ideas=got, chosen=None, spend=[dict(stage="ideas", **use)]+[dict(stage='ideas',provider='openai',what='footage discovery',usd=i['supply'].get('usd',0)) for i in got if i.get('supply')])


def script(ctx, ideas_out):
    able = {s["id"]: s for s in able_cards(ctx)}
    pick = next((i for i in ideas_out["ideas"] if i.get("style") in able and (i.get("supply") or {}).get("ok")), None)
    if not pick:
        raise RuntimeError("no idea with enough real footage evidence: " + json.dumps([(i.get("title"), (i.get("supply") or {}).get("covered")) for i in ideas_out["ideas"]])[:600])
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
        sc, use = story.write(pick, dos, card, voices=card.get("voice"), notes=notes, line=ctx.args.get("line"))
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
    reg = card
    facts = {f["id"]: f for f in dos["facts"]}
    used = sorted({fid for s in sc["segments"] for fid in s.get("facts") or []})
    return dict(title=sc.get("title") or pick.get("title"), style=reg, window=None, hook_line=sc["segments"][0]["say"],
                close_line=(sc.get("close") or {}).get("line") or sc["segments"][-1]["say"], direction=f"{card['name']}: {card['look']}",
                idea=pick.get("title"), idea_full=pick, script=sc, check=rep, dossier=dos, dossier_verify=drep,
                facts_cited=[dict(id=k, claim=facts[k].get("claim"), quote=facts[k].get("quote"), url=facts[k].get("url")) for k in used if k in facts],
                words=rep["words"], spend=spend)


# ----------------------------------------------------------------- supply, score, render, check: film_long.py

def supply(ctx, script):
    import cinema
    return cinema.supply(ctx, script)


def score(ctx, script):
    import cinema
    return cinema.score(ctx, script)


def render(ctx, script, sc):
    import cinema
    return cinema.render(ctx, script, sc)


def check(ctx, rd, sc):
    import encoded_repair
    return encoded_repair.check(ctx, rd, sc)
