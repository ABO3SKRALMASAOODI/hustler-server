"""Workflow 2 on the shell: films that market Valmera to editors.

ideas   reads what the product really did (evidence.rows: studio recordings of a clip, a typed sentence and the
        export that came back) and what editors respond to (evidence.references), and finds the film: an editor's
        pain, the true claim that answers it, the row that proves it, the style that shows it. Nobody hands it a topic.
script  writes the film's few words for the chosen style and the brief its director follows. The typed sentences
        are the recordings' own, never rewritten; every number on screen is one the row holds, or the stage fails.
supply  packs the recordings a film needs, with receipts (supply.py studio).
score   the director's plan: film.py bakes the scene (L1), draws stills of every beat for a look, and checks the
        plan against the taste rules as numbers.
render  the film's sound as one file, then Valmera's engine, on the Mac, through heavy.sh.
check   the shell's brand finish and checks, then this workflow's own, measured on the film.

Model output is parsed as JSON. Nothing here reads a customer's words or touches a customer's project.
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

# what the script stage writes for each style: the JSON it is asked for, and the most characters each slot holds
SLOTS = {
    "reading-wipe": (
        '"line": the claim in two or three plain words that stands under the upload and the film side by side, ends with a full stop, '
        '"cta": {"word": the keyword, capitals, 4 to 7 letters, a word an editor would comment, "rows": [a line that tells the viewer the word '
        'disappears when they pause, a line that says to comment the word and what they are sent; neither row may spell the keyword, it exists '
        'only in the static], "promise": what the publisher must send to a commenter}',
        {"line": 16, "cta.rows.0": 30, "cta.rows.1": 36}),
    "one-clip-three-films": (
        '"line1": the first of two short lines under the three films, ends with a full stop, "line2": the second, ends with a full stop, '
        '"cta": {"rows": [a question asking which of the three the viewer would post, a line telling them to comment its number: 1, 2 or 3], '
        '"promise": null}',
        {"line1": 12, "line2": 18, "cta.rows.0": 26, "cta.rows.1": 30}),
    "edits-itself": (
        '"tag_ask": the label over the typed sentence, capitals, "receipt_head": the head of the edit\'s receipt, capitals, '
        '"receipt_total": the receipt\'s last line, plain words, no number, '
        '"cta": {"word": a keyword, capitals, 4 to 7 letters, "rows": [a line that says to comment the keyword and carries it, '
        'a line that says what they are sent], "promise": what the publisher must send to a commenter}',
        {"tag_ask": 14, "receipt_head": 16, "receipt_total": 22, "cta.rows.0": 24, "cta.rows.1": 30}),
}


def cards():
    return {s["id"]: s for s in json.load(open(os.path.join(HERE, "styles.json")))["styles"]}


def menu(rows):
    return "\n".join(f"- {s['id']}: {s['name']}. {s.get('look', '')} Hook: {s.get('hook', '')} Close: {s.get('close', '')} "
                     f"Needs: {s.get('needs', '')}. CTA: {s.get('cta', '')}" for s in rows)


def ideas(ctx):
    rows = evidence.rows()
    if ctx.args.get("row_id"):                       # a film about one row: the idea is still the stage's to find
        rows = [r for r in rows if r["row_id"] == ctx.args["row_id"]]
    c = cards()
    allowed = [dict(r, **c.get(r["id"], {})) for r in ctx.styles_allowed()]
    able = [s for s in allowed if s.get("buildable") and s["id"] in F.STYLES]
    if ctx.args.get("style"):
        able = [s for s in able if s["id"] == ctx.args["style"]]
    ids = {s["id"] for s in able}
    rows = [r for r in rows if set(r["fits"]) & ids]
    if not able or not rows:
        raise RuntimeError(f"nothing true to show: {len(able)} buildable styles outside the last three ({ctx.styles_last()}), {len(rows)} recorded rows "
                           "that fit one. Record with studio.py")
    json.dump(rows, open(os.path.join(ctx.tmp, "rows.json"), "w"), ensure_ascii=False, indent=1)
    refs = evidence.references()
    system = ("You are the ideas stage of Valmera's marketing workflow. Valmera is an AI video editor (valmera.io): a clip is uploaded, a "
              "sentence is typed, an edit comes back. You find short vertical films that market it to video editors and creators who edit: "
              "technique films and before-and-after films, with the call to action inside the film. You find the ideas yourself. An idea is an "
              "editor's real pain, one true claim about Valmera that answers it, and the one picture that proves it in the first second.")
    content = ("# What the product really did (recorded sessions). A film may claim only what a row here proves, and a row can carry only the "
               "styles in its `fits`.\n" + json.dumps(rows, ensure_ascii=False)
               + "\n\n# What the product does\n" + json.dumps(evidence.capabilities(), ensure_ascii=False)
               + "\n\n# What editors respond to: the owner's reference films in this genre (their captions, comments counted)\n"
               + json.dumps([dict(kind=r["kind"], comments=r["comments"], caption=r["caption"][:220]) for r in refs[:16]], ensure_ascii=False)
               + f"\n\n# Styles a film can take today\n{menu(able)}\n\n# Taste\n{TASTE}\n\n"
               + (f"# A line from the owner\n{ctx.args['line']}\n\n" if ctx.args.get("line") else "")
               + "Return JSON only: {\"ideas\": [up to 5 items, best first]}. Each: {\"id\": \"i1\", \"title\": short plain title, "
               "\"pain\": what the editor struggles with, in their words, \"claim\": the true thing the product did in that row, with its number, "
               "\"row_id\": the row that proves it, \"style\": a style id from that row's `fits`, \"records\": for a row with more than three "
               "recordings, the three whose films differ most, else the row's records, \"why_style\": what in this row the style needs, "
               "\"first_second\": what the viewer sees before one second has passed, \"strength\": 1-10}.")
    text, use = llm.ask(system, content, effort="medium", max_tokens=6000)
    got = llm.json_block(text)
    got = got.get("ideas") if isinstance(got, dict) else got
    by = {r["row_id"]: r for r in rows}
    keep = []
    for r in got or []:
        row = by.get(r.get("row_id"))
        if not row or r.get("style") not in ids or r["style"] not in row["fits"]:
            continue
        recs = [x for x in (r.get("records") or []) if x in row["records"]]
        r["records"] = recs if len(recs) == (3 if r["style"] == "one-clip-three-films" else 1) else row["records"][:3 if r["style"] == "one-clip-three-films" else 1]
        keep.append(r)
    return dict(source=dict(kind="studio recordings", rows=len(rows), recordings=len({x for r in rows for x in r["records"]}), references=len(refs)),
                ideas=keep, chosen=None, spend=[dict(stage="ideas", what="find the film", **use)])


def digits(values):
    out = set()
    for v in values:
        cur = ""
        for ch in str(v) + " ":
            if ch.isdigit():
                cur += ch
            elif cur:
                out.add(cur.lstrip("0") or "0")
                cur = ""
    return out


def numbers_ok(shown, facts):
    """Every run of digits on screen is one the evidence holds. Measured strings compared; no reading of intent."""
    have = digits(facts)
    return sorted({n for t in shown for n in digits([t]) if n not in have})


def dig(d, path):
    for k in path.split("."):
        d = d[int(k)] if isinstance(d, list) else d[k]
    return d


def built(style, row, recs):
    """The strings a film shows that are facts: built here from the row, never written by a model."""
    if style == "reading-wipe":
        res = row["result"]
        bits = [res["export_ratio"], f"{res['pictures']} pictures" if res["pictures"] > 1 else ("1 picture" if res["pictures"] else None),
                "captions" if res["captions"] else None, f"{res['cuts']} cuts" if res["cuts"] > 1 else None]
        return dict(tag_raw=f"RAW · {res['upload_ratio']} · {res['upload_length']}", facts_row=" · ".join([b for b in bits if b][:3]))
    if style == "edits-itself":
        ch = row["change"]
        n, mv = ch["pictures_added"], ch.get("pictures_moved_with_the_cut") or 0
        rows = [("sentences typed", "1"), ("cut from the talk", f"{ch['seconds_removed']:.1f} s")]
        if n:
            rows.append(("pictures added", str(n)))
        if mv:
            rows.append(("pictures moved with it", str(mv)))
        rows += [("length", f"{ch['before_length']} to {ch['after_length']}"), ("edit versions", f"v{ch['before_version']} to v{ch['after_version']}")]
        changes = ["\u2212 " + f"{ch['seconds_removed']:.1f} s", f"+ {n} picture{'s' if n != 1 else ''}" if n else None,
                   f"{mv} picture{'s' if mv != 1 else ''} follow{'s' if mv == 1 else ''}" if mv else None]
        return dict(changes=[c for c in changes if c][:2], receipt_rows=rows)
    return {}


def script(ctx, ideas_out):
    rows = {r["row_id"]: r for r in json.load(open(os.path.join(ctx.tmp, "rows.json")))}
    c = cards()
    allowed = {r["id"]: r for r in ctx.styles_allowed()}
    ranked = sorted((r for r in ideas_out["ideas"] if r["style"] in allowed), key=lambda r: -float(r.get("strength") or 0))
    if not ranked:
        raise RuntimeError("no idea fits a buildable style outside the last three")
    idea = ranked[0]
    ideas_out["chosen"] = idea
    row = rows[idea["row_id"]]
    style = allowed[idea["style"]]
    sid = style["id"]
    slots, limits = SLOTS[sid]
    recs = idea["records"]
    sentences = [row["sentence"]] if "sentence" in row else [x["sentence"] for x in row["sentences"] if x["record"] in recs]
    fixed = built(sid, row, [row])
    system = ("You are the script stage of Valmera's marketing workflow. You write the few words a film shows, and the brief its "
              "director follows. Plain words an editor would say. No slogans, no exclamation marks, no promises beyond the facts.")
    ask = (f"# The idea\n{json.dumps(idea, ensure_ascii=False)}\n\n# The style\n{menu([dict(style, **c[sid])])}\n\n"
           f"# The row (the only facts and numbers the film may show)\n{json.dumps(row, ensure_ascii=False)}\n\n"
           f"# Already on screen, word for word (do not repeat them)\nThe typed sentences: {json.dumps(sentences, ensure_ascii=False)}\n"
           f"Facts drawn by the film itself: {json.dumps(fixed, ensure_ascii=False)}\n\n# Taste\n{TASTE}\n\n"
           "Return JSON only: {\"title\": the film's title (plain, at most 6 words), " + slots + ", "
           "\"direction\": 80-130 words to the director: which seconds of the export to show and why, where the voice should start and stop, "
           "what stays plain}. Most characters per slot: " + json.dumps(limits) + ".")
    spend, s, note = [], None, ""
    for _ in range(3):
        text, use = llm.ask(system, ask + note, effort="medium", max_tokens=5000)
        spend.append(dict(stage="script", what="copy and brief", **use))
        s = llm.json_block(text)
        over = {k: len(str(dig(s, k))) for k, n in limits.items() if len(str(dig(s, k)).strip()) > n}
        word = (s.get("cta") or {}).get("word")
        if word is not None and not (4 <= len(word.strip()) <= 7):
            over["cta.word"] = len(word)
        if not over:
            break
        note = f"\n\nYour last answer did not fit its slots (characters used): {json.dumps(over)}. Answer again, shorter."
    else:
        raise RuntimeError(f"the words do not fit their slots after three tries: {over}")
    cta = dict(rows=[str(r).strip() for r in s["cta"]["rows"]][:2])
    if s["cta"].get("word"):
        cta["word"] = s["cta"]["word"].strip().upper()
    copy = dict(fixed, cta=cta)
    if sid == "reading-wipe":
        copy["line"] = s["line"].strip()
    elif sid == "one-clip-three-films":
        copy.update(line1=s["line1"].strip(), line2=s["line2"].strip())
    else:
        copy.update(tag_ask=s["tag_ask"].strip(), changes=[x for x in fixed["changes"] if x],
                    receipt=dict(head=s["receipt_head"].strip(), rows=fixed["receipt_rows"], total=s["receipt_total"].strip()))
        copy.pop("receipt_rows", None)
    copy.update((k, v) for k, v in (ctx.args.get("copy") or {}).items())          # a line from the owner replaces the stage's
    needs = bool(s["cta"].get("promise"))
    return dict(title=s.get("title") or idea["title"], style=style, window=None, source=None, idea=idea, copy=copy, sentences=sentences,
                facts=row, evidence=dict(row_id=row["row_id"], records=recs),
                cta=dict(keyword=cta.get("word"), rows=cta["rows"], promise=s["cta"].get("promise"), needs_fulfilment=needs,
                         note=("The keyword is a promise: the publisher sends what it names to every commenter, or changes the rows." if needs
                               else "The close asks a question; nothing is promised.")),
                direction=f"Style: {style['name']}. " + str(s.get("direction", "")).strip(), spend=spend)


def direction_of(ctx):
    d = dict(ctx.args.get("direction") or {})
    p = os.path.join(ctx.run_dir, "direction.json")
    if os.path.exists(p):
        d.update(json.load(open(p)))
    return d


def supply(ctx, script):
    d = direction_of(ctx)
    recs = d.get("records") or script["evidence"]["records"]
    pack = os.path.join(ctx.supply, "pack.json")
    if not os.path.exists(pack):
        chain.sh(ctx, [chain.PY, os.path.join(HERE, "supply.py"), "studio", "--style", script["style"]["id"], "--records", ",".join(recs),
                       "--out", ctx.supply], label=f"wf2-supply-{ctx.run_id}", heavy=True, timeout=3600)
    p = json.load(open(pack))
    return dict(pack=pack, records=[r["sid"] for r in p["records"]], receipts=p["receipts"], spend=[])


def default_window(pack, take_s):
    """The first stretch of the upload, after its opening fifth, that all three films keep whole: found from the EDLs."""
    tls = [F.program(json.load(open(r["edl"])))[0] for r in pack["records"]]
    dur = pack["records"][0]["upload"]["dur"]
    t = dur * 0.2
    while t + take_s < dur - 0.3:
        try:
            if all(len(F.runs(tl, tl.src_to_out(t), tl.src_to_out(t) + take_s)) == 1 and tl.seg_program_range(t) for tl in tls):
                return [round(t, 2), round(t + take_s, 2)]
        except (ValueError, TypeError):
            pass
        t += 0.2
    raise RuntimeError(f"no {take_s} s of the upload is kept whole by all three films")


def plan(ctx, script):
    """(l1, beats, direction) of this run's film: the same call for the look and the render."""
    d = direction_of(ctx)
    pack = json.load(open(os.path.join(ctx.supply, "pack.json")))
    env_path = F.scene_worker() + os.pathsep + os.path.join(chain.SLICE, "pylib")
    sys.path[:0] = [p for p in env_path.split(os.pathsep) if p not in sys.path]
    os.environ["PATH"] = os.path.dirname(pack["ffmpeg"]) + os.pathsep + os.environ.get("PATH", "")
    copy = json.loads(json.dumps(script["copy"]))
    for k, v in (d.get("copy") or {}).items():           # the director's hand on the script's words; checked like the rest
        if isinstance(v, dict) and isinstance(copy.get(k), dict):
            copy[k].update(v)
        else:
            copy[k] = v
    knobs = {k: v for k, v in d.items() if k not in ("records", "copy", "why", "alien")}
    sid = script["style"]["id"]
    if sid == "one-clip-three-films" and not knobs.get("window_s"):
        knobs["window_s"] = default_window(pack, float(knobs.get("take_s", 4.4)))
    l1, beats = F.STYLES[sid](copy, pack, knobs)
    if d.get("alien"):                     # the direction's asks for the engine's alien bank (chain.alien_bank lays them in at the render)
        l1["alien"] = d["alien"]
    return l1, beats, d, pack, env_path


def score(ctx, script):
    l1, beats, d, pack, env_path = plan(ctx, script)
    bad = []
    missing = numbers_ok(beats["on_screen"], [json.dumps(script["facts"], ensure_ascii=False), *script["sentences"], "01 02 03 1 2 3"])
    if missing:
        bad.append(f"the film would show numbers the evidence does not hold: {missing}")
    word = (script["copy"].get("cta") or {}).get("word")
    if beats.get("cta_kind") == "static" and word and any(word.lower() in str(r).lower() for r in beats["on_screen"]):
        bad.append(f"a row spells the keyword {word!r}: it must exist only in the static")
    for s in script["sentences"]:
        if s not in beats["on_screen"] and " ".join(s.split()) not in [" ".join(str(x).split()) for x in beats["on_screen"]]:
            bad.append(f"a typed sentence is not shown word for word: {s!r}")
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
    if bad:
        raise RuntimeError("; ".join(bad) + ". Fix it in direction.json under 'copy'.")
    if not taste["ok"]:
        raise RuntimeError("the plan breaks a taste rule: " + "; ".join(taste["problems"][:6]))
    if ctx.args.get("look", True) and not os.path.exists(os.path.join(ctx.run_dir, "look.ok")):
        raise RuntimeError(f"the director's look: open {look}/sheet.jpg, put changes in {ctx.run_dir}/direction.json, "
                           f"then touch {ctx.run_dir}/look.ok and resume")
    return dict(l1=out, beats=beats, taste=taste, frames=l1["dur_f"], items=len(l1["items"]), direction=d, on_screen=beats["on_screen"], spend=[])


def render(ctx, script, sc):
    out = os.path.join(ctx.supply, "film_engine.mp4")
    env_path = F.scene_worker() + os.pathsep + os.path.join(chain.SLICE, "pylib")
    cache = os.path.join(ctx.tmp, "scene-cache")
    l1 = json.load(open(sc["l1"]))
    pack = json.load(open(os.path.join(ctx.supply, "pack.json")))
    sound = os.path.join(ctx.supply, "sound.m4a")
    mix = F.audio_master(pack["ffmpeg"], l1, sound, ctx.tmp) if not os.path.exists(sound) else {}
    if not os.path.exists(out):
        log = chain.sh(ctx, ["env", f"PYTHONPATH={env_path}", f"VALMERA_SCENE_CACHE={cache}", chain.PY, "-m", "scene.run", "render", "--l1", chain.alien_bank(ctx, sc["l1"]),
                             "--work", ctx.supply, "--out", out + ".part.mp4", "--tier", "master", "--strict", "--cache", cache, "--audio", sound],
                       label=f"wf2-render-{ctx.run_id}", heavy=True, timeout=7200)
        os.replace(out + ".part.mp4", out)
        try:
            j = json.loads(log[log.index("{"):log.rindex("}") + 1])
        except ValueError:
            j = {}
        json.dump(dict(j, mix=mix), open(out + ".log.json", "w"))
    j = json.load(open(out + ".log.json")) if os.path.exists(out + ".log.json") else {}
    return dict(engine="scene", film=out, engine_check=dict(frames=j.get("frames"), seconds=j.get("seconds"), audio=j.get("mix"),
                                                              degraded=j.get("degraded"), warnings=j.get("warnings")))


def mark_samples(rd, final, beats):
    """The mark, measured in the middle of each stretch of the film that is its own page. The shell reads one frame at
    mid-programme; when that frame is static, or a picture that changes fast, its one difference cannot tell."""
    import numpy as np
    import brand
    R = brand._renderer()
    p = brand.probe(final)
    Wd, Hd = p["w"], p["h"]
    ix0, iy0, iw, ih = R.watermark_geometry(Wd, Hd)["image"]
    rows = []
    for a, b in beats.get("own_pages") or []:
        t = ((a + b) // 2 + 0.5) / F.FPS
        x, y = brand._frame(rd["film"], t, Wd, Hd), brand._frame(final, t, Wd, Hd)
        roi = float(np.abs(x[iy0:iy0 + ih, ix0:ix0 + iw] - y[iy0:iy0 + ih, ix0:ix0 + iw]).mean())
        rest = float(np.abs(x - y).mean())
        rows.append(dict(frames=[a, b], t=round(t, 3), roi=round(roi, 2), outside=round(rest, 2), ok=bool(roi > 4 * max(rest, 0.5))))
    return rows


def check(ctx, rd, sc):
    r = chain.finish_and_check(ctx, rd, sc)
    c = r["checks"]
    rows = mark_samples(rd, r["final"], sc["beats"])
    c["mark_samples"] = rows
    if not c.get("mark_ok") and rows and all(x["ok"] for x in rows):
        c["mark_shell"] = dict(roi=c.get("mark_roi_diff"), outside=c.get("outside_diff"),
                               note="The shell's one sample did not tell. The mark is one overlay across the whole programme: it is measured "
                                    "here on this film's own pages.")
        c["mark_ok"] = True
        c["ok"] = bool(c["length_ok"] and c["endcard_ok"] and c["loud_ok"] and c["music"]["ok"])
    pack = json.load(open(os.path.join(ctx.supply, "pack.json")))
    l1 = json.load(open(sc["l1"]))
    mine = F.film_checks(pack["ffmpeg"], rd["film"], l1, sc["beats"])
    beats = {k: v for k, v in sc["beats"].items() if k not in ("look", "on_screen")}
    c["marketing"] = dict(mine, taste=sc["taste"], on_screen=sc["on_screen"], direction=sc["direction"], beats=beats,
                          recordings=[dict(sid=x["sid"], sentence=x["sentence"], product_commit=x["be_sha"], export_sha256=x["export"]["sha256"],
                                           edl_version=x["export"]["edl_version"], facts=x["facts"]) for x in pack["records"]],
                          receipts=pack["receipts"])
    c["ok"] = bool(c["ok"] and mine["ok"] and sc["taste"]["ok"])
    return r
