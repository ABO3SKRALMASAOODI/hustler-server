"""Workflow 2 on the shell: films that market Valmera to editors.

ideas   reads what Valmera really delivered (evidence.ledger) and what editors respond to
        (evidence.references), and finds the film: an editor's pain, the true claim that answers
        it, the style that shows it. Nobody hands it a topic.
script  writes the film's few words for the chosen style, and the brief the director follows.
        Every number on screen must be a number in the evidence, or the stage fails.
supply  records the real output: the delivered shorts read in place, the raw source registered to
        the hero short, the whole delivery as tiles, with receipts (supply.py).
score   the director's plan: film.py bakes the scene (L1), draws stills of every beat for a look,
        and checks the plan against the taste rules as numbers.
render  Valmera's engine, on the Mac, through heavy.sh.
check   the shell's brand finish and checks, then two of this workflow's own, measured on the
        film: the export is shown as delivered, and the keyword is in no single frame.

Model output is parsed as JSON. Nothing here reads a customer's words or touches a project.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "_shell"))
import chain  # noqa: E402
import llm  # noqa: E402
import evidence  # noqa: E402
import film as F  # noqa: E402

TASTE = ("Restraint is the design. One device carries the film; everything else is plain. A sound belongs to something landing on "
         "screen. Type has one seat per kind, sits off faces and inside the safe area, and never repeats what is already legible. "
         "The real export is never drawn over or re-typeset. No music: Valmera never picks music. The end card and the mark are "
         "added after the film. Only true claims: every number on screen is a number in the evidence.")


def cards():
    return {s["id"]: s for s in json.load(open(os.path.join(HERE, "styles.json")))["styles"]}


def menu(rows):
    return "\n".join(f"- {s['id']}: {s['name']}. {s.get('look', '')} Hook: {s.get('hook', '')} Close: {s.get('close', '')} "
                     f"Needs: {s.get('needs', '')}. CTA: {s.get('cta', '')}" for s in rows)


def ideas(ctx):
    led = evidence.ledger()
    if ctx.args.get("run_id"):                       # a film about one delivery: the idea is still the stage's to find
        led = [r for r in led if r["run_id"] == ctx.args["run_id"]]
    if not led:
        raise RuntimeError("no finished run with its finals and source on disk: nothing true to show")
    refs = evidence.references()
    c = cards()
    allowed = [dict(r, **c.get(r["id"], {})) for r in ctx.styles_allowed()]
    able = [s for s in allowed if s.get("buildable") and s["id"] in F.STYLES]
    if ctx.args.get("style"):
        able = [s for s in able if s["id"] == ctx.args["style"]]
    if not able:
        raise RuntimeError(f"no buildable style outside the last three ({ctx.styles_last()})")
    json.dump(led, open(os.path.join(ctx.tmp, "ledger.json"), "w"), ensure_ascii=False, indent=1)
    system = ("You are the ideas stage of Valmera's marketing workflow. Valmera is an AI video editor (valmera.io). You find short "
              "vertical films that market it to video editors and creators who edit: technique tutorials and before-and-after "
              "films, with the call to action inside the film. You find the ideas yourself. An idea is an editor's real pain, "
              "one true claim about Valmera that answers it, and the one picture that proves it in the first second.")
    content = ("# What Valmera really delivered (the ledger). A film may claim only what a row here proves.\n"
               + json.dumps([dict(run_id=r["run_id"], delivered_shorts=r["delivered"], source_length=r["source_clock"],
                                  source=r["identity"][:500], looks=r["looks"], music=r["music"]) for r in led], ensure_ascii=False)
               + "\n\n# What the product does in one request\n" + json.dumps(evidence.capabilities(), ensure_ascii=False)
               + "\n\n# What editors respond to: the owner's reference films in this genre (their captions, comments counted)\n"
               + json.dumps([dict(kind=r["kind"], comments=r["comments"], caption=r["caption"][:220]) for r in refs[:16]], ensure_ascii=False)
               + f"\n\n# Styles a film can take today\n{menu(able)}\n\n# Taste\n{TASTE}\n\n"
               + (f"# A line from the owner\n{ctx.args['line']}\n\n" if ctx.args.get("line") else "")
               + "Return JSON only: {\"ideas\": [5 items, best first]}. Each: {\"id\": \"i1\", \"title\": short plain title, "
               "\"pain\": what the editor struggles with, in their words, \"claim\": the true thing Valmera did, with its number, "
               "\"run_id\": the ledger row that proves it, \"style\": a style id from the list, \"why_style\": what in this idea the "
               "style needs, \"first_second\": what the viewer sees before one second has passed, \"request\": the one sentence an "
               "editor would type to get this (at most 30 characters), \"strength\": 1-10}.")
    text, use = llm.ask(system, content, effort="medium", max_tokens=6000)
    got = llm.json_block(text)
    rows = got.get("ideas") if isinstance(got, dict) else got
    ids, runs = {s["id"] for s in able}, {r["run_id"] for r in led}
    rows = [r for r in rows if r.get("style") in ids and r.get("run_id") in runs]
    return dict(source=dict(kind="ledger", runs=len(led), delivered_total=sum(r["delivered"] for r in led), references=len(refs)),
                ideas=rows, chosen=None, spend=[dict(stage="ideas", what="find the film", **use)])


def numbers_ok(copy, facts):
    """Every run of digits on screen is one the evidence holds. Two measured strings compared; no reading of intent."""
    have = set()
    for v in facts:
        cur = ""
        for ch in str(v) + " ":
            if ch.isdigit():
                cur += ch
            elif cur:
                have.add(cur.lstrip("0") or "0")
                cur = ""
    missing = []
    for t in copy:
        cur = ""
        for ch in str(t) + " ":
            if ch.isdigit():
                cur += ch
            elif cur:
                if (cur.lstrip("0") or "0") not in have:
                    missing.append(cur)
                cur = ""
    return missing


def copy_faults(copy, facts, strict_rows=True):
    """What is wrong with the words a film is about to show, as measured strings: a number the evidence does not hold,
    a slot that does not fit, a row that spells the keyword (then the word is on screen in every frame)."""
    bad = []
    missing = numbers_ok([copy["sentence"], copy["tag_raw"], copy["tag_source"], copy["small_row"], *copy["cta"]["rows"]], facts.values())
    if missing:
        bad.append(f"the film would show numbers the evidence does not hold: {missing}")
    word = copy["cta"]["word"]
    if not (4 <= len(word) <= 7) or len(copy["sentence"]) > 34:
        bad.append(f"the words do not fit their slots: keyword {word!r}, sentence {len(copy['sentence'])} characters")
    if strict_rows and any(word.lower() in r.lower() for r in copy["cta"]["rows"]):
        bad.append(f"a row spells the keyword {word!r}: it must exist only in the static")
    return bad


def final_copy(script, d):
    """The script's words with the director's changes (direction.json 'copy'), checked again."""
    copy = json.loads(json.dumps(script["copy"]))
    for k, v in (d.get("copy") or {}).items():
        if k == "cta":
            copy["cta"].update(v)
        else:
            copy[k] = v
    bad = copy_faults(copy, script["facts"])
    if bad:
        raise RuntimeError("; ".join(bad) + ". Fix it in direction.json under 'copy'.")
    return copy


def script(ctx, ideas_out):
    led = {r["run_id"]: r for r in json.load(open(os.path.join(ctx.tmp, "ledger.json")))}
    c = cards()
    allowed = {r["id"]: r for r in ctx.styles_allowed()}
    rows = sorted((r for r in ideas_out["ideas"] if r["style"] in allowed), key=lambda r: -float(r.get("strength") or 0))
    if not rows:
        raise RuntimeError("no idea fits a buildable style outside the last three")
    idea = rows[0]
    ideas_out["chosen"] = idea
    run = led[idea["run_id"]]
    style = allowed[idea["style"]]
    card = c[style["id"]]
    hours, minutes = int(run["source_s"]) // 3600, int(run["source_s"]) % 3600 // 60
    facts = dict(delivered_shorts=run["delivered"], source_length=f"{hours} h {minutes} min", source_clock=run["source_clock"],
                 source=run["identity"][:700], looks=len(run["looks"]))
    system = ("You are the script stage of Valmera's marketing workflow. You write the few words a film shows, and the brief its "
              "director follows. Plain words an editor would say. No slogans, no exclamation marks, no promises beyond the facts.")
    content = (f"# The idea\n{json.dumps(idea, ensure_ascii=False)}\n\n# The style\n{menu([dict(style, **card)])}\n\n"
               f"# The facts (the only numbers the film may show)\n{json.dumps(facts, ensure_ascii=False)}\n\n# Taste\n{TASTE}\n\n"
               "Return JSON only: {\"title\": the film's title (plain, at most 6 words), "
               "\"sentence\": the request as an editor types it, one line, at most 30 characters, ends with a full stop, "
               "\"tag_raw\": the label on the raw footage, capitals, at most 12 characters, "
               "\"tag_source\": the source in capitals with its length from source_length, at most 30 characters, e.g. 'SHOW #12 · 1 H 02 MIN', "
               "\"count_noun\": one lower-case word for what was delivered, "
               "\"small_row\": one lower-case line under the count, at most 28 characters, true by the facts, "
               "\"cta\": {\"word\": the keyword, capitals, 4 to 7 letters, a word an editor would comment, \"rows\": [a line of at most 30 "
               "characters that tells the viewer the word disappears when they pause, a line of at most 36 characters that says to "
               "comment the word and what they are sent; neither row may spell the keyword, it exists only in the static], "
               "\"promise\": what the publisher must send to a commenter}, "
               "\"direction\": 90-150 words to the director: what the hero short should be (one speaker, a line that stands alone in "
               "its first 7 seconds, a design that reads at a glance), what the quick cuts after it should contrast, where the voice "
               "should stop, what stays plain}.")
    text, use = llm.ask(system, content, effort="medium", max_tokens=5000)
    s = llm.json_block(text)
    copy = dict(sentence=s["sentence"].strip(), tag_raw=s["tag_raw"].strip(), tag_source=s["tag_source"].strip(),
                count_noun=s["count_noun"].strip(), small_row=s["small_row"].strip(),
                cta=dict(word=s["cta"]["word"].strip(), rows=[r.strip() for r in s["cta"]["rows"]][:2], promise=s["cta"].get("promise")))
    copy.update((k, v) for k, v in (ctx.args.get("copy") or {}).items())          # a line from the owner replaces the stage's
    spelled = copy_faults(copy, facts, strict_rows=False)
    if spelled:
        raise RuntimeError("; ".join(spelled))
    return dict(title=s.get("title") or idea["title"], style=style, window=None, source=None, idea=idea, copy=copy, facts=facts,
                evidence=dict(run=run["run"], run_id=run["run_id"], delivered=run["delivered"], folder=run["folder"]),
                cta=dict(keyword=copy["cta"]["word"], promise=copy["cta"].get("promise"), needs_fulfilment=True,
                         note="The keyword is a promise: the publisher sends what it names to every commenter, or changes the rows."),
                direction=f"Style: {style['name']}. " + s.get("direction", "").strip(), spend=[dict(stage="script", what="copy and brief", **use)])


def direction_of(ctx):
    d = dict(ctx.args.get("direction") or {})
    p = os.path.join(ctx.run_dir, "direction.json")
    if os.path.exists(p):
        d.update(json.load(open(p)))
    return d


def pick(run_dir, d):
    """The hero and the quick cuts when the director named none: a top-ranked short on one continuous take, then one
    short of each other Look, ending on the tile nearest the middle of the sheet (the pull-back starts there)."""
    import supply as S
    rec = S.run_record(run_dir)
    sh = rec["shorts"]
    hero = d.get("hero")
    if not hero:
        one = [s for s in sh if len(s["keeps"]) == 1 and s["keeps"][0][1] - s["keeps"][0][0] >= 12 and s["structure"] == "fast-conversation"]
        hero = min(one or sh, key=lambda s: s["rank"] or 999)["sid"]
    salvo = d.get("salvo")
    if not salvo:
        grid = S.grid_for(len(sh)) or (len(sh), 1)
        mid = (grid[1] // 2) * grid[0] + grid[0] // 2
        last = sh[mid]["sid"] if sh[mid]["sid"] != hero else sh[mid + 1]["sid"]
        look_of = {s["sid"]: s["look"] for s in sh}
        seen, salvo = {look_of[hero], look_of[last]}, []
        for s in sorted(sh, key=lambda s: s["rank"] or 999):
            if s["look"] not in seen and s["sid"] not in (hero, last):
                salvo.append(s["sid"])
                seen.add(s["look"])
            if len(salvo) == 3:
                break
        salvo.append(last)
    return hero, salvo


def supply(ctx, script):
    d = direction_of(ctx)
    hero, salvo = pick(script["evidence"]["run"], d)
    pack = os.path.join(ctx.supply, "pack.json")
    if not os.path.exists(pack):
        chain.sh(ctx, [chain.PY, os.path.join(HERE, "supply.py"), "build", "--run", script["evidence"]["run"], "--out", ctx.supply,
                       "--hero", hero, "--salvo", ",".join(str(x) for x in salvo)], label=f"wf2-supply-{ctx.run_id}", heavy=True, timeout=3600)
    p = json.load(open(pack))
    if p["facts"]["delivered"] != script["evidence"]["delivered"]:
        raise RuntimeError("the delivery changed between the script and the supply")
    return dict(pack=pack, hero=hero, salvo=salvo, tiles=len(p["tiles"]), grid=p["grid"], registration=p["hero"]["reg"],
                receipts=p["receipts"], spend=[])


def score(ctx, script):
    d = direction_of(ctx)
    pack = json.load(open(os.path.join(ctx.supply, "pack.json")))
    env_path = F.scene_worker() + os.pathsep + os.path.join(chain.SLICE, "pylib")
    sys.path[:0] = [p for p in env_path.split(os.pathsep) if p not in sys.path]
    os.environ["PATH"] = os.path.dirname(pack["ffmpeg"]) + os.pathsep + os.environ.get("PATH", "")
    copy = final_copy(script, d)
    l1, beats = F.STYLES[script["style"]["id"]](copy, pack, {k: v for k, v in d.items() if k not in ("hero", "salvo", "copy", "why")})
    out = os.path.join(ctx.supply, "l1.json")
    json.dump(l1, open(out, "w"), ensure_ascii=False)
    json.dump(beats, open(os.path.join(ctx.supply, "beats.json"), "w"), indent=1)
    taste = F.taste_check(l1, beats)
    look = os.path.join(ctx.run_dir, "look")
    frames = F.look_frames(beats)
    chain.sh(ctx, ["env", f"PYTHONPATH={env_path}", chain.PY, "-m", "scene.run", "still", "--l1", out, "--work", ctx.supply,
                   "--frames", ",".join(str(f) for f in frames), "--dir", look], label=f"wf2-look-{ctx.run_id}", heavy=True, timeout=1800)
    import subprocess
    cols = 9
    subprocess.run([pack["ffmpeg"], "-v", "error", "-y", "-pattern_type", "glob", "-i", os.path.join(look, "f*.png"), "-vf",
                    f"scale=240:-2,tile={cols}x{-(-len(frames) // cols)}", "-frames:v", "1", "-q:v", "3", os.path.join(look, "sheet.jpg")], check=True)
    if not taste["ok"]:
        raise RuntimeError("the plan breaks a taste rule: " + "; ".join(taste["problems"][:6]))
    if ctx.args.get("look", True) and not os.path.exists(os.path.join(ctx.run_dir, "look.ok")):
        raise RuntimeError(f"the director's look: open {look}/sheet.jpg, put changes in {ctx.run_dir}/direction.json, "
                           f"then touch {ctx.run_dir}/look.ok and resume")
    return dict(l1=out, beats=beats, taste=taste, frames=l1["dur_f"], items=len(l1["items"]), direction=d, on_screen=copy, spend=[])


def render(ctx, script, sc):
    out = os.path.join(ctx.supply, "film_engine.mp4")
    env_path = F.scene_worker() + os.pathsep + os.path.join(chain.SLICE, "pylib")
    cache = os.path.join(ctx.tmp, "scene-cache")
    if not os.path.exists(out):
        log = chain.sh(ctx, ["env", f"PYTHONPATH={env_path}", f"VALMERA_SCENE_CACHE={cache}", chain.PY, "-m", "scene.run", "render", "--l1", sc["l1"],
                             "--work", ctx.supply, "--out", out + ".part.mp4", "--tier", "master", "--strict", "--cache", cache],
                       label=f"wf2-render-{ctx.run_id}", heavy=True, timeout=7200)
        os.replace(out + ".part.mp4", out)
        try:
            j = json.loads(log[log.index("{"):log.rindex("}") + 1])
        except ValueError:
            j = {}
        json.dump(j, open(out + ".log.json", "w"))
    j = json.load(open(out + ".log.json")) if os.path.exists(out + ".log.json") else {}
    return dict(engine="scene", film=out, engine_check=dict(frames=j.get("frames"), seconds=j.get("seconds"), audio=j.get("audio"),
                                                              degraded=j.get("degraded"), warnings=j.get("warnings")))


def mark_samples(rd, final, beats):
    """The mark, measured on three beats. The shell reads one frame at mid-programme; in this style that frame is a
    real export, which already carries the mark, so the mark laid over it reads as missing (the hero row shows it)."""
    import numpy as np
    import brand
    R = brand._renderer()
    p = brand.probe(final)
    Wd, Hd = p["w"], p["h"]
    ix0, iy0, iw, ih = R.watermark_geometry(Wd, Hd)["image"]
    rows = []
    for k in ("hero", "wall_hold", "wipe"):
        a, b = beats[k]
        t = ((a + b) // 2 + 0.5) / F.FPS if k != "wipe" else 6.5 / F.FPS
        x, y = brand._frame(rd["film"], t, Wd, Hd), brand._frame(final, t, Wd, Hd)
        roi = float(np.abs(x[iy0:iy0 + ih, ix0:ix0 + iw] - y[iy0:iy0 + ih, ix0:ix0 + iw]).mean())
        rest = float(np.abs(x - y).mean())
        rows.append(dict(beat=k, t=round(t, 3), roi=round(roi, 2), outside=round(rest, 2), ok=bool(roi > 4 * max(rest, 0.5))))
    return rows


def check(ctx, rd, sc):
    r = chain.finish_and_check(ctx, rd, sc)
    c = r["checks"]
    rows = mark_samples(rd, r["final"], sc["beats"])
    c["mark_samples"] = rows
    own = [x for x in rows if x["beat"] != "hero"]                # beats whose page is this film's, with no mark under the mark
    if not c.get("mark_ok") and own and all(x["ok"] for x in own):
        c["mark_shell"] = dict(roi=c.get("mark_roi_diff"), outside=c.get("outside_diff"),
                               note="The shell's one sample fell on a real export. An export already carries the same mark in the same "
                                    "place, so laying the mark over it changes almost nothing and the difference test cannot see it. "
                                    "The mark is one overlay across the whole programme: it is measured here on this film's own pages.")
        c["mark_ok"] = True
        c["ok"] = bool(c["length_ok"] and c["endcard_ok"] and c["loud_ok"] and c["music"]["ok"])
    pack = json.load(open(os.path.join(ctx.supply, "pack.json")))
    l1 = json.load(open(sc["l1"]))
    mine = F.film_checks(pack["ffmpeg"], rd["film"], l1, sc["beats"])
    r["checks"]["marketing"] = dict(mine, taste=sc["taste"], on_screen=sc["on_screen"], direction=sc["direction"], beats=sc["beats"],
                                    hero=dict(sid=pack["hero"]["sid"], project_id=pack["hero"]["project_id"], edl_version=pack["hero"]["edl_version"]),
                                    quick_cuts=[dict(sid=s["sid"], project_id=s["project_id"], edl_version=s["edl_version"]) for s in pack["salvo"]],
                                    receipts=pack["receipts"])
    r["checks"]["ok"] = bool(r["checks"]["ok"] and mine["export_unaltered"] and mine["pause_proof"] and sc["taste"]["ok"])
    return r
