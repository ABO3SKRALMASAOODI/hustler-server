#!/usr/bin/env python3
"""Workflow 1, one short: a podcast moment made into a film on the engines. podcast.py runs one of these per moment.

    PY=/Users/masaoodi/Documents/Valmera/.valmera/venv-worker/bin/python
    $PY wf1_short.py run --source <podcast file> --out <deliver root> --state-root <folder> \
        --args '{"moment": {...}, "words": "<words.json>", "style": "<id>", "identity": "..."}'
    $PY wf1_short.py run --resume <run_id> --state-root <folder>

ideas    the moment podcast.py found and ranked (--args moment): nothing is asked of a model here
script   one call on the moment's word-timed lines: the exact in and out on word edges, the title, the hook and close
         lines, and the real people, companies, places and events it names (what the footage engine looks for)
supply   the window cut from the source and heard locally (the shell's pack); real video of what each sentence names
         (worker/footage through the shell: YouTube and archive.org, every frame checked, 1-3 s clips, each with its
         source, channel and time range); photographs of the same things (Commons and Openverse, checked, licensed);
         the speaker's face
score    one call on the heard sentences (the words to set large, the sentences about the past, the turn, the years);
         wf1_film.build bakes the scene in the short's style and the engines finish it (pace, look, sound); the plan is
         held to the taste rules as numbers
render   the take's sound and the placed sounds as one mix, then the scene engine (worker/scene) through heavy.sh
check    the product's watermark and end card, the measured checks, and the film's music (the shell: a track chosen
         from the licensed library and cut to the turn and payoff; film.mp4 with it, film_clean.mp4 without)

Model calls are on the OpenAI rung (the shell's llm.py). Never publishes.
"""
import importlib.util
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))        # through the Claude skill link to the plugin itself
SKILLS = os.path.dirname(os.path.dirname(HERE))
SHELL = os.path.join(SKILLS, "_shell")
for p in (SHELL, os.path.join(SKILLS, "valmera-stories"), HERE):
    if p not in sys.path:
        sys.path.insert(0, p)
import brand  # noqa: E402

# every module of this run reads one worker: the one that holds this week's engines
os.environ.setdefault("VALMERA_WORKER", brand.worker_dir())
os.environ.setdefault("VALMERA_SCENE_WORKER", os.environ["VALMERA_WORKER"])
os.environ.setdefault("WRITER_PROVIDER", "openai")

import chain  # noqa: E402
import film as F  # noqa: E402
import pictures as PX  # noqa: E402
import shell  # noqa: E402
import wf1_film  # noqa: E402
import wf3  # noqa: E402


def _llm():
    """The shell's llm.py under a name of its own: the worker has an `llm` module too, and its picture check must
    find its own."""
    name = "valmera_shell_llm"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, os.path.join(SHELL, "llm.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules[name] = mod
    return sys.modules[name]


def source_log(ctx):
    src = ctx.source
    rec = dict(path=src, bytes=os.path.getsize(src))
    for k in ("source_url", "video_id", "source_title", "channel", "upload_date", "transcript"):
        if (ctx.args or {}).get(k):
            rec[k] = ctx.args[k]
    return rec


def ideas(ctx):
    m = (ctx.args or {}).get("moment")
    if not isinstance(m, dict) or m.get("t0") is None or m.get("t1") is None:
        raise RuntimeError("wf1_short makes the moment podcast.py hands it: --args '{\"moment\": {...}}'")
    return dict(source=source_log(ctx), ideas=[m], chosen=None, spend=[])


def _near(ctx, t0, t1, pad=25.0):
    words = json.load(open(ctx.args["words"]))
    return [(float(t), w) for t, w in words if t0 - pad <= float(t) <= t1 + pad]


def script(ctx, ideas_out):
    idea = ideas_out["ideas"][0]
    ideas_out["chosen"] = idea
    want = (ctx.args or {}).get("style") or wf1_film.ORDER[0]
    reg = next((s for s in shell.registry()["wf1"]["styles"] if s["id"] == want and s["id"] in wf1_film.STYLES), None)
    if reg is None:
        raise RuntimeError(f"style {want} is not one of the engine styles {list(wf1_film.STYLES)}")
    near = _near(ctx, float(idea["t0"]), float(idea["t1"]))
    system = ("You are the script stage of Valmera's podcast shorts workflow. You turn one chosen moment of a conversation "
              "into the exact clip for a vertical short. You cut on word edges, start on the first word of the thought (no "
              "throat-clearing, no 'and', 'so', 'yeah' when the next word can open), and end on the payoff with a breath "
              "of tail. The clip must stand alone for a stranger who never saw the episode.")
    system += '\n\nReturn JSON only: {"in": seconds (0.15-0.3 s before the first word), "out": seconds (0.3-0.8 s after the last word; the clip is 18-55 s), "title": the film\'s title in at most five words, a headline a stranger stops for, taken from what is said, "hook_line": the exact words heard first, "close_line": the exact last words, "speakers": number of people heard, "named": [the real people, companies, places, products and events this clip names or plainly points to, that a camera has filmed; each {"name": its usual English name, "is": person|company|place|product|event|thing, "must_show": one plain sentence on what footage of it must visibly show, as it was at the time he speaks of}; never the speaker himself as he looks today: he is on screen], "brief": two sentences on the story and its mood, for the editor who chooses the music}.'
    content = f"# The moment\n{json.dumps(idea, ensure_ascii=False)}\n\n# Who is in it\n{str((ctx.args or {}).get('identity') or '')[:1500]}\n\n# Words near the moment: seconds word\n" + ' '.join((f'{t:.2f} {w}' for (t, w) in near))
    llm = _llm()
    spend, s = [], {}
    for _ in range(2):                                   # an answer cut short or missing a field is asked for once more
        text, use = llm.ask(system, content, effort="medium", max_tokens=16000)
        spend.append(dict(stage="script", what="clip, title and what it names", **use))
        try:
            s = llm.json_block(text)
        except ValueError:
            s = {}
        if isinstance(s, dict) and all(s.get(k) not in (None, "") for k in ("in", "out", "title")):
            break
    else:
        raise RuntimeError("the script stage gave no clip")
    t0, t1 = float(s["in"]), float(s["out"])
    if not (8.0 <= t1 - t0 <= 75.0):
        raise RuntimeError(f"script window {t0}-{t1} is not a short")
    named = [r for r in s.get("named") or [] if isinstance(r, dict) and r.get("name")][:8]
    return dict(title=s.get("title") or idea.get("title"), style=reg, window=[round(t0, 2), round(t1, 2)], source=ctx.source,
                hook_line=s.get("hook_line") or idea.get("hook"), close_line=s.get("close_line") or idea.get("payoff"),
                speakers=int(s.get("speakers") or idea.get("speakers") or 1), names=[r["name"] for r in named], named=named,
                direction=f"{reg['name']}: {reg.get('look', '')} {s.get('brief') or ''}".strip(), idea=idea, spend=spend)


def supply(ctx, script):
    sup_dir = ctx.supply
    os.makedirs(sup_dir, exist_ok=True)
    os.environ["TMPDIR"] = ctx.tmp
    pk = os.path.join(sup_dir, "pack.json")
    if os.path.exists(pk):
        return json.load(open(pk))["record"]
    rec0 = chain.pack(ctx, script)                              # source.mp4 (the window), hearing.json, index.json
    idx = json.load(open(os.path.join(sup_dir, "index.json")))
    src = os.path.join(sup_dir, "source.mp4")
    sents = idx.get("sentences") or []
    if not sents:
        raise RuntimeError("nothing was heard in the window")
    words = []
    for si, s in enumerate(sents):
        for w in idx["words"][s["wi0"]:s["wi1"] + 1]:
            words.append(dict(w=w["w"], start=round(float(w["t0"]), 3), end=round(float(w["t1"]), 3), seg=si))
    dur = float(idx["video"]["duration"])
    wj = os.path.join(sup_dir, "words.json")
    json.dump(dict(voice="the take", duration=round(max(w["end"] for w in words) + 0.05, 3) if words else dur, words=words), open(wj, "w"), ensure_ascii=False)
    spend = []
    style = wf1_film.STYLES[script["style"]["id"]]
    # real video of what each sentence names: the footage engine's own planner reads the sentences (OpenAI)
    who = str((ctx.args or {}).get("identity") or "")[:400].replace("\n", " ")
    fscript = dict(title=f"{script.get('title')}. A podcast clip; the speaker is on camera already, so show what he talks about, as it was then, "
                         f"never him as he looks today. Who is in it: {who}",
                   segments=[dict(say=s["text"], pictures=[]) for s in sents],
                   named=[dict(name=r["name"], must_show=r.get("must_show") or r["name"], **{"is": r.get("is") or "thing"}) for r in script.get("named") or []])
    wf3.given_footage(ctx, os.path.join(sup_dir, "footage"))
    fw, fh = style["footage"]
    foot = chain.footage(ctx, dict(script=fscript), [round(float(s["t1"]) - float(s["t0"]) + 0.2, 2) for s in sents], W=fw, H=fh, preserve_source=True)
    if foot:
        n_yt, n_bad = refused(foot)
        ctx.log(f"footage sources: {n_yt} on YouTube, {n_bad} refused or archive-only; {foot['counts'].get('clips')} clips on "
                f"{foot['counts'].get('lines_with_footage')} of {foot['counts'].get('lines')} sentences")
    if walled(foot):                                           # nothing is cut from photographs and a clip or two: the same run continues when the wall lifts
        import shutil
        shutil.rmtree(os.path.join(sup_dir, "footage"), ignore_errors=True)
        raise RuntimeError(f"{wf1_film.WALL}: {n_bad} of {n_yt} footage sources would not open ({foot['counts'].get('clips')} clips on "
                           f"{foot['counts'].get('lines_with_footage')} of {foot['counts'].get('lines')} sentences). It lifts by itself; run the same link again later")
    pack = dict(sid=ctx.sid, words=wj, narration=src, ffmpeg=PX.FF, sounds={}, pictures={}, credits=[], footage=[], title=script.get("title"))
    subjects, by_line = [], {}
    if foot:
        fdir = os.path.join(sup_dir, "footage")
        pack["footage"] = [dict(i=ln["i"], clips=[dull(wf3.clip_facts(dict(c, path=os.path.join(fdir, c["file"])))) for c in ln["clips"]]) for ln in foot["lines"]]
        pack["credits"] += [dict(kind="footage", name=c["title"], title=c["title"], licence="footage, not licensed (the owner's decision)", credit=c["channel"],
                                 page=c["url"], time_range=c["time_range"], line=c["line"]) for c in foot["credits"]]
        subjects = [s for s in foot.get("subjects") or [] if s.get("name")]
        by_line = {int(ln["i"]): list(ln.get("subjects") or []) for ln in foot["lines"]}
        spend.append(dict(stage="supply", provider="openai", model="gpt-6-sol low + gpt-4o-transcribe", what="footage", usd=(foot.get("spend") or {}).get("usd")))
        ctx.log(f"footage: {foot['counts']['clips']} clips on {foot['counts']['lines_with_footage']} of {foot['counts']['lines']} sentences; "
                f"subjects {[s['name'] for s in subjects]}")
    # photographs of the same things, for the sentences the footage left bare
    named = [dict(name=s["name"], searches=[s["name"]], must_show=s.get("must_show") or s["name"], **{"is": s.get("kind") or "thing"})
             for s in subjects if any(s["name"] in v for v in by_line.values())][:6]
    if named:
        sys.modules.pop("llm", None)                           # the worker's own llm answers its picture check
        try:
            got, reports, usd = PX.pictures(named, os.path.join(sup_dir, "pics"), per=3)
        except Exception as e:  # noqa: BLE001 - photographs are the fallback: a film is not lost to them
            ctx.log(f"photographs skipped: {str(e)[:300]}")
            got, reports, usd = {}, [], 0.0
        spend.append(dict(stage="supply", provider="openai", model=os.environ.get("NAMED_CHECK_MODEL", "gpt-6-sol"), what="picture check", usd=usd))
        for recs in got.values():
            for r in recs:
                try:
                    r["faces"] = PX.faces(r["path"])
                except Exception:  # noqa: BLE001
                    r["faces"] = []
        pack["pictures"] = {k: v for k, v in got.items() if v}
        pack["picture_reports"] = reports
        pack["credits"] += [dict(kind="photograph", name=r["name"], title=r.get("title"), licence=r.get("licence"), credit=r.get("credit"), page=r.get("page"))
                            for v in got.values() for r in v]
    # the take: its size and the speaker's face
    still = os.path.join(sup_dir, "take.jpg")
    subprocess.run([PX.FF, "-v", "error", "-y", "-ss", f"{min(dur / 2, 3.0):.2f}", "-i", src, "-frames:v", "1", "-q:v", "3", still], check=True, timeout=120)
    v = idx["video"]
    try:
        faces = PX.faces(still)
    except Exception:  # noqa: BLE001
        faces = []
    pack["take"] = dict(path=src, w=int(v["width"]), h=int(v["height"]), fps=float(v["fps"]), dur=dur, faces=faces, still=still, shots=len(idx.get("shots") or []),
                        cuts=take_cuts(src, idx.get("shots") or [], dur, sup_dir))
    pack["lines"] = [dict(i=i, t0=s["t0"], t1=s["t1"], text=s["text"], subjects=by_line.get(i) or []) for i, s in enumerate(sents)]
    rec = dict(rec0, narration_s=dur, voice="the take", gate=None, pictures=sum(len(x) for x in pack["pictures"].values()), subjects=len(pack["pictures"]),
               footage=(dict(clips=foot["counts"]["clips"], lines=foot["counts"]["lines_with_footage"], credits=foot["credits"]) if foot else None), spend=spend)
    json.dump(dict(pack=pack, record=rec), open(pk, "w"), ensure_ascii=False, indent=1)
    return rec


def refused(foot):
    """(YouTube sources the engine tried, how many of them failed or opened only through the Wayback Machine's copy)."""
    yt = [s for s in (foot or {}).get("sources") or [] if s.get("where") in (None, "youtube")]
    return len(yt), len([s for s in yt if s.get("error") or s.get("rung") == "wayback"])


def walled(foot):
    """True when the footage engine came back with too little because YouTube would not open its sources (it takes
    a busy address for a bot, on every player at once): half of the YouTube sources it tried or more were refused,
    and what it did find covers under a third of the sentences. A film cut then would be photographs with a clip
    or two, under the bar; it is made when the wall lifts. Thin footage with the sources open is not this: the
    moment simply names little, and photographs carry it."""
    if not foot:
        return False
    n, bad = refused(foot)
    c = foot.get("counts") or {}
    return n >= 2 and bad * 2 >= n and int(c.get("lines_with_footage") or 0) * 3 < max(int(c.get("lines") or 0), 1)


def dull(c):
    """A clip whose middle frame is near black or flat (no light, nothing in focus) is marked: it is no picture, and
    the film builder leaves it out. Measured on the frame, never guessed."""
    try:
        import numpy as np
        from PIL import Image
        with Image.open(c["still"]) as im:
            g = np.asarray(im.convert("L").resize((160, 160)), dtype=np.float32) / 255.0
        c["luma"] = [round(float(g.mean()), 3), round(float(g.std()), 3)]
        c["dull"] = bool(g.mean() < 0.11 or g.std() < 0.05)
    except Exception:  # noqa: BLE001 - a clip nobody could measure plays
        c["dull"] = False
    return c


def take_cuts(src, shots, dur, sup_dir):
    """The take's own shots with the speaker's place in each: [{t0, t1, faces}]. A speaker moves, so each shot is
    looked at three times and his head's box is the box round all three; a show that cuts between cameras gets a
    framing per camera."""
    spans = [(float(s["start"]), float(s["end"])) for s in shots if float(s["end"]) - float(s["start"]) > 0.2] or [(0.0, dur)]
    out = []
    for k, (a, b) in enumerate(spans[:40]):
        seen = []
        for q in (0.15, 0.5, 0.85):
            still = os.path.join(sup_dir, f"take_{k:02d}_{int(q * 100)}.jpg")
            try:
                subprocess.run([PX.FF, "-v", "error", "-y", "-ss", f"{a + (b - a) * q:.2f}", "-i", src, "-frames:v", "1", "-q:v", "4", still], check=True, timeout=120)
                fs = PX.faces(still)
            except Exception:  # noqa: BLE001 - a shot nobody could look at is framed like the take
                fs = []
            if fs:
                seen.append(max(fs, key=lambda f: (f["box"][2] - f["box"][0]) * (f["box"][3] - f["box"][1])))
        face = []
        if seen:
            widths = sorted(f["box"][2] - f["box"][0] for f in seen)
            box = [min(f["box"][0] for f in seen), min(f["box"][1] for f in seen), max(f["box"][2] for f in seen), max(f["box"][3] for f in seen)]
            if box[2] - box[0] > 2.2 * widths[len(widths) // 2]:           # not one head moving: the middle look stands for the shot
                mid = seen[len(seen) // 2]
                box, eyes = list(mid["box"]), mid["eyes"]
            else:
                eyes = [[sum(f["eyes"][e][c] for f in seen) / len(seen) for c in (0, 1)] for e in (0, 1)]
            face = [dict(box=[round(x, 5) for x in box], eyes=[[round(x, 5) for x in e] for e in eyes], score=1.0)]
        out.append(dict(t0=round(a, 3), t1=round(b, 3), faces=face))
    return out


def direction_of(ctx, script, pack):
    """What a model reads off the heard sentences for the film builder: the words to set large, the sentences that
    speak of the past, the sentence the payoff starts on, and the years the story speaks from. Cached in the supply."""
    p = os.path.join(ctx.supply, "direction.json")
    if os.path.exists(p):
        return json.load(open(p))
    lines = pack["lines"]
    system = ("You direct the type and the finish of a vertical short cut from a podcast. You read its sentences, numbered, "
              "and answer in JSON only. You never add a word he does not say.")
    system += "\n\n" + (("\n\n"
               "Return JSON only: {"
               "\"display\": [four to eight strings to set large on screen, each on the word that says it: the figures, the "
               "names and the two or three words that carry the story; each {\"seg\": the sentence number, \"text\": one to three "
               "consecutive words exactly as he says them (a figure as digits, e.g. $5,000 or 1999), \"accent\": true for the one "
               "or two that matter most}; spread over the film, in order, never two from one sentence unless it is long], "
               "\"past_segs\": [the numbers of the sentences that tell of the past: a year gone by, how something began, what "
               "someone once did; not his opinions about today], "
               "\"turn_seg\": the number of the sentence where the payoff begins (usually the last or the one before it), "
               "\"arc\": [only when he says a calendar year aloud: {\"seg\": sentence number, \"word\": the year as he says it in "
               "digits, \"year\": the year as a number, \"stakes\": how things stand for the story's hero at that point, -1 (worst) "
               "to 1 (best)}; an empty list when no year is said]}"))
    content = ((f"# The film\n{script.get('title')}\nHook: {script.get('hook_line')}\nPayoff: {script.get('close_line')}\n\n# Sentences\n") +
               ("\n".join(f"{ln['i']}: {ln['text']}" for ln in lines)))
    llm = _llm()
    text, use = llm.ask(system, content, effort="low", max_tokens=8000)
    try:
        d = llm.json_block(text)
    except ValueError:
        d = {}
    d = d if isinstance(d, dict) else {}
    out = dict(display=[r for r in d.get("display") or [] if isinstance(r, dict)][:8],
               past_segs=[int(i) for i in d.get("past_segs") or [] if isinstance(i, int)],
               turn_seg=d.get("turn_seg") if isinstance(d.get("turn_seg"), int) else None,
               arc=[r for r in d.get("arc") or [] if isinstance(r, dict)][:6],
               spend=dict(stage="score", what="display words, past sections, turn", **use))
    json.dump(out, open(p, "w"), ensure_ascii=False, indent=1)
    return out


def _repair(l1, beats, taste):
    """A plan that breaks a taste rule is brought inside it and measured again, never delivered as it is and never a
    stop: a word or a sound the rule names is dropped, and sounds over the supply's allowance go quietest first (a
    gesture is the sounds inside one second; the film's last gesture and the one on its turn stay). Returns how many
    items were dropped."""
    ids = set()
    for pr in taste["problems"]:
        head = pr.split(" ", 2)
        ids.add(head[1] if head[0] == "sound" and len(head) > 1 else head[0])
    n0 = len(l1["items"]) + len(l1.get("sound") or [])
    l1["items"] = [it for it in l1["items"] if it["id"] not in ids]
    l1["sound"] = [s for s in l1.get("sound") or [] if s["id"] not in ids]
    over = int(taste.get("sounds") or 0) - int(taste.get("sounds_allowed") or 0)
    if over > 0:
        groups = []
        for s in sorted(l1["sound"], key=lambda s: int(s["event_f"])):
            if groups and int(s["event_f"]) - int(groups[-1][-1]["event_f"]) <= F.FPS:
                groups[-1].append(s)
            else:
                groups.append([s])
        turn = beats.get("turn_f")
        keep = {id(groups[-1])} if groups else set()
        if turn is not None and groups:
            keep.add(id(min(groups, key=lambda g: abs(int(g[0]["event_f"]) - int(turn)))))
        loose = sorted((g for g in groups if id(g) not in keep), key=lambda g: max(float(x.get("db") or 0) for x in g))
        gone = {x["id"] for g in loose[:over] for x in g}
        l1["sound"] = [s for s in l1["sound"] if s["id"] not in gone]
    return n0 - len(l1["items"]) - len(l1["sound"])


def score(ctx, script):
    pack = wf3.load_pack(ctx)
    pack["work"] = ctx.supply
    d = direction_of(ctx, script, pack)
    knobs = {k: d.get(k) for k in ("display", "past_segs", "turn_seg", "arc")}
    knobs.update({k: v for k, v in ((ctx.args or {}).get("direction") or {}).items()})
    rejected_path = os.path.join(ctx.supply, "render_rejected.json")
    if os.path.exists(rejected_path):
        knobs["drop"] = sorted(set(knobs.get("drop") or []) | set(json.load(open(rejected_path))))
    knobs.setdefault("bed", chain.wants_music(ctx) or bool((ctx.args or {}).get("song")))      # the film gets music: the sounds are designed for a film with a bed
    sys.path[:0] = [p for p in wf3.env_path().split(os.pathsep) if p not in sys.path]
    os.environ["PATH"] = os.path.dirname(pack["ffmpeg"]) + os.pathsep + os.environ.get("PATH", "")
    sc = dict(title=script.get("title"), hook_line=script.get("hook_line"), close_line=script.get("close_line"),
              segments=[dict(say=ln["text"], pictures=[n for n in ln.get("subjects") or []]) for ln in pack["lines"]])
    l1, beats = wf1_film.build(sc, pack, script["style"]["id"], knobs)
    taste = F.taste_check(l1, beats)
    dropped = 0
    if not taste["ok"]:
        dropped = _repair(l1, beats, taste)
        taste = dict(F.taste_check(l1, beats), repaired=dropped)
    out = os.path.join(ctx.supply, "l1.json")
    json.dump(l1, open(out, "w"), ensure_ascii=False)
    l1_audio = os.path.join(ctx.supply, "l1_audio.json")       # the same scene with the take's sound as its spine: what the music is cut to
    json.dump(F.audio_scene(l1, pack), open(l1_audio, "w"), ensure_ascii=False)
    json.dump(beats, open(os.path.join(ctx.supply, "beats.json"), "w"), indent=1)
    if not taste["ok"]:
        raise RuntimeError("the plan breaks a taste rule: " + "; ".join(taste["problems"][:6]))
    turn_s = round(beats["turn_f"] / F.FPS, 3) if beats.get("turn_f") is not None else None
    return dict(l1=out, l1_audio=l1_audio, turn_s=turn_s, beats={k: v for k, v in beats.items() if k not in ("look",)}, taste=taste,
                frames=l1["dur_f"], items=len(l1["items"]), direction={k: v for k, v in d.items() if k != "spend"}, spend=([d["spend"]] if d.get("spend") else []) +
                [dict(stage="score", what="picture-to-words check", **u) for u in (beats.get("placement_check") or {}).get("spend", [])])


def _render_review(ctx, rd, sc):
    """Check the composed phone frames: source approval cannot certify a crop or a tiny print."""
    from placement_check import check as placement_check
    pack = wf3.load_pack(ctx)
    script = dict(segments=[dict(say=ln["text"]) for ln in pack["lines"]])
    clock = F.Clock(pack["words"], script)
    l1 = json.load(open(sc["l1"]))
    display = [it for it in l1["items"] if it.get("k") == "word" and it.get("role") == "big"]
    rows = []
    for p in sc["beats"].get("pictures") or []:
        a, b = p.get("visible_f") or p["f"]
        words = [dict(f=it["f"], text=it["t"]) for it in display if it["f"][0] < b and it["f"][1] > a]
        frames = {a, (a+b)//2, max(a,b-1)}
        for word in words:
            frames.update(f for edge in word["f"] for f in (edge-1,edge,edge+1) if a <= f < b)
        rows.append([a, b, "clip", p["name"], dict(path=rd["film"], src=[a/F.FPS,b/F.FPS],
                    check_times=[f/F.FPS for f in sorted(frames)], display_words=words,
                    source=dict(url=p.get("source")))])
    _, report = placement_check(rows, script, clock, os.path.join(ctx.supply, "render-review"), pack["ffmpeg"])
    if not report["rejected"] and rows:
        _, audit = placement_check(rows, script, clock, os.path.join(ctx.supply, "render-review"),
                                   pack["ffmpeg"], review="transition")
        report["spend"] += audit["spend"]
        for first, second in zip(report["placements"], audit["placements"]):
            first["transition_audit"] = dict(ok=second["ok"], reason=second["reason"])
            if not second["ok"]:
                first.update(ok=False, reason=second["reason"])
        report["accepted"] = sum(r["ok"] for r in report["placements"])
        report["rejected"] = len(rows) - report["accepted"]
    return report


def render(ctx, script, sc):
    """The product repairs rejected placements before branding and music, never a delivered film."""
    history_path = os.path.join(ctx.supply, "render_reviews.json")
    history = json.load(open(history_path)) if os.path.exists(history_path) else []
    while True:
        rd = wf3.render(ctx, script, sc)
        report = _render_review(ctx, rd, sc)
        history.append(report)
        json.dump(history, open(history_path, "w"), indent=1)
        sc["beats"]["rendered_check"] = report
        if not report["rejected"]:
            json.dump(sc, open(os.path.join(ctx.run_dir, "score.json"), "w"), indent=1)
            rd["picture_review"] = history
            rd["spend"] = [dict(stage="render", what="composed picture-to-words check", **u) for h in history for u in h.get("spend", [])]
            return rd
        _rebuild_rejected(ctx, script, sc, report)


def _rebuild_rejected(ctx, script, sc, report):
    """Monotonic repair shared by the engine and exact delivery-frame gates."""
    pics = sc["beats"].get("pictures") or []
    # Every failed pass permanently removes at least one source asset.
    # The fixed supply makes this finite; keep independently approved
    # pictures instead of discarding the whole montage after three passes.
    doomed = [pics[r["id"]] for r in report["placements"] if not r["ok"]]
    dropped = [os.path.basename(p["path"]) for p in doomed]
    rejected_path = os.path.join(ctx.supply, "render_rejected.json")
    previous = json.load(open(rejected_path)) if os.path.exists(rejected_path) else []
    if not set(dropped) - set(previous):
        raise RuntimeError("composed picture repair made no progress; no film may be delivered")
    json.dump(sorted(set(previous+dropped)), open(rejected_path, "w"), indent=1)
    ctx.log(f"composed picture check: rebuilding without {len(dropped)} rejected placements")
    old_spend = sc.get("spend", [])
    rebuilt = score(ctx, script)
    rebuilt["spend"] = old_spend + [u for u in rebuilt.get("spend", []) if u.get("what") == "picture-to-words check"]
    sc.clear()
    sc.update(rebuilt)
    json.dump(sc, open(os.path.join(ctx.run_dir, "score.json"), "w"), indent=1)
    # These are this run's cached renders of the old plan, before delivery.
    for name in ("film_engine.mp4", "film_engine.mp4.log.json", "sound.m4a",
                 "film_engine_music.mp4", "music.json"):
        p = os.path.join(ctx.supply, name)
        if os.path.exists(p):
            os.remove(p)
    for name in ("final.mp4", "final_music.mp4", "final_song.mp4"):
        path = os.path.join(ctx.run_dir, name)
        if os.path.exists(path):
            os.remove(path)



def check(ctx, rd, sc):
    while True:
        r = wf3.check(ctx, rd, sc)
        final = r.get("final_music") or r["final"]
        report = _render_review(ctx, dict(film=final), sc)
        history_path = os.path.join(ctx.supply, "delivery_reviews.json")
        history = json.load(open(history_path)) if os.path.exists(history_path) else []
        history.append(report)
        json.dump(history, open(history_path, "w"), indent=1)
        if not report["rejected"]:
            sc["beats"]["delivered_check"] = report
            json.dump(sc, open(os.path.join(ctx.run_dir, "score.json"), "w"), indent=1)
            break
        script = json.load(open(os.path.join(ctx.run_dir, "script.json")))
        _rebuild_rejected(ctx, script, sc, report)
        fresh = render(ctx, script, sc)
        rd.clear()
        rd.update(fresh)
    b = sc.get("beats") or {}
    pics = b.get("pictures") or []
    secs = max(float(sc.get("frames") or 1) / F.FPS, 1.0)
    r["checks"]["podcast"] = dict(
        style=b.get("style"), layout=b.get("layout"), cutaway_share=b.get("cutaway_share"), finishes=b.get("finishes"), alien=b.get("alien"),
        pictures=len(pics), clips=len([p for p in pics if p.get("kind") == "clip"]), photographs=len([p for p in pics if p.get("kind") == "photo"]),
        new_pictures_10s=round(len(pics) / secs * 10, 2), display_words=[x.get("text") for x in b.get("display") or []],
        placement_check=b.get("placement_check"), rendered_check=b.get("rendered_check"), render_reviews=rd.get("picture_review"),
        delivered_check=b.get("delivered_check"), delivery_reviews=history,
        sources=[dict(f=p.get("f"), visible_f=p.get("visible_f"), kind=p.get("kind"), name=p.get("name"), source=p.get("source"), time_range=p.get("time_range")) for p in pics])
    r["checks"]["ok"] = bool(r["checks"].get("ok") and not (b.get("rendered_check") or {}).get("rejected"))
    return r


_load = shell.load_workflow


def _loader(name):
    return _load(os.path.realpath(__file__) if name == "wf1" else name)


def main():
    shell.load_workflow = _loader
    if "--workflow" not in sys.argv:
        sys.argv[2:2] = ["--workflow", "wf1"]
    return shell.main()


if __name__ == "__main__":
    sys.exit(main() or 0)
