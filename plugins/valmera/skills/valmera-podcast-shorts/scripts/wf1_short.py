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
import hashlib
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

FOOTAGE_PIXELS = "native"  # clips keep their source pixels for full-bleed framing (16 Nov); a fitted 1080x608 shelf is not reused
RESEARCH_CONTRACT = 7  # primary documents can fill a truthful still-picture gap before more video research
# Research passes a short may spend on missing documentary coverage before its film is built from every approved picture
# (owner, 16 Nov 2026: many publishable shorts per podcast). Up to 7 passes, each a full footage search plus fresh
# frame checks, made one short cost 600-1,400 model calls on 15-16 Nov and lost 3 of 10 moments outright.
RECOVERY_PASSES = 2


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
    for _ in range(1 if os.environ.get('CODEX_PROXY_LOCAL') == '1' else 2):
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
    named = [r for r in s.get("named") or [] if isinstance(r, dict) and r.get("name")]
    return dict(title=s.get("title") or idea.get("title"), style=reg, window=[round(t0, 2), round(t1, 2)], source=ctx.source,
                hook_line=s.get("hook_line") or idea.get("hook"), close_line=s.get("close_line") or idea.get("payoff"),
                speakers=int(s.get("speakers") or idea.get("speakers") or 1), names=[r["name"] for r in named], named=named,
                direction=f"{reg['name']}: {reg.get('look', '')} {s.get('brief') or ''}".strip(), idea=idea, spend=spend)


def supply(ctx, script):
    import wf1_coverage, wf1_alignment
    sup_dir = ctx.supply
    os.makedirs(sup_dir, exist_ok=True)
    os.environ["TMPDIR"] = ctx.tmp
    pk = os.path.join(sup_dir, "pack.json")
    if os.path.exists(pk):
        prior = json.load(open(pk))
        if ((prior['pack'].get('coverage') or {}).get('contract') == wf1_coverage.CONTRACT
                and prior['pack'].get('alignment_contract')==wf1_alignment.CONTRACT
                and (prior['pack'].get('platform_walled') or any(row.get('clips') for row in prior['pack'].get('footage',[])))):
            return prior['record']
        os.replace(pk,os.path.join(sup_dir,'previous-pack.json'))
    rec0 = chain.pack(ctx, script)                              # source.mp4 (the window), hearing.json, index.json
    idx = json.load(open(os.path.join(sup_dir, "index.json")))
    heard_run=_near(ctx,*script["window"],pad=2)
    idx,_=wf1_alignment.reconcile(ctx,script,idx,heard_run,_llm(),chain)
    # the run transcript's own token boundaries ('49%', 'self-critical.'), so no caption starts on '-critical'
    idx,joined=wf1_alignment.run_tokens(idx,heard_run,float(script["window"][0]))
    if joined:
        with open(os.path.join(sup_dir,"index.json"),"w") as f:json.dump(idx,f,indent=1)
    rec0.update(window=script["window"],dur=idx["video"]["duration"],words=len(idx["words"]),shots=len(idx["shots"]))
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
    spend = [dict(stage="supply",what="heard clip alignment",**idx["alignment"]["spend"])]
    style = wf1_film.STYLES[script["style"]["id"]]
    # real video of what each sentence names: the footage engine's own planner reads the sentences (OpenAI)
    who = str((ctx.args or {}).get("identity") or "")[:400].replace("\n", " ")
    import wf1_coverage
    coverage = wf1_coverage.plan(words, script.get('title'), sup_dir, _llm())
    coverage['context']=who
    import wf1_context
    primary_direction=wf1_context.direct(coverage,os.path.join(sup_dir,'referent-research'),_llm())
    windows = coverage['windows']
    # Inventory records what was said; windows specify what can truthfully be photographed.
    # Passing the inventory back as search subjects resurrects unnamed people and isolated dates.
    research = {}
    for window in windows:
        research.setdefault(window['name'],[]).append(window['must_show'])
    import wf1_primary_sources
    originals=wf1_primary_sources.retained(sup_dir)
    fscript = dict(title=f"{script.get('title')}. A podcast clip; the speaker is on camera already, so show what he talks about, as it was then, "
                         f"never him as he looks today. Who is in it: {who}",
                   segments=[dict(say=s['say'], pictures=[s['name']], visual=s['must_show']) for s in windows],
                   named=[dict(name=name,must_show=' '.join(requirements),
                               video_sources=originals.get(name,[]),
                               video_searches=list(dict.fromkeys(q for w in windows if w['name']==name for q in w.get('video_searches',[]))),
                               **{'is':'thing'}) for name,requirements in research.items()])
    direction_key=hashlib.sha256(json.dumps(fscript,sort_keys=True).encode()).hexdigest()[:16]
    footage_name = f'footage-v{wf1_coverage.CONTRACT}-{FOOTAGE_PIXELS}-{direction_key}'
    wf3.given_footage(ctx, os.path.join(sup_dir, footage_name))
    fw, fh = style["footage"]
    # This workflow uses the engine's contextual-check route, as WF3 does. Every SELECTED
    # cut gets entry/mid/exit checks against its actual words in wf1_film, then composed
    # and branded transition checks. The generic shelf verifier has no placement context
    # and incorrectly requires a montage's entire brief inside each individual 1 s shot.
    foot = chain.footage(ctx, dict(script=fscript), [round(float(s["t1"]) - float(s["t0"]), 2) for s in windows],
                         W=fw, H=fh, preserve_source=True, native_pixels=True, look_itself=True, shot_s=1.15, line_max=20, per=5,
                         strict_lines=True, motion_first=True, scout_motion=True, alloc="round", reassign_lost=True,
                         verify=False, whole_first=True, keep_sources=True, hear_s=0, out_name=footage_name)
    if foot:
        n_yt, n_bad = refused(foot)
        ctx.log(f"footage sources: {n_yt} on YouTube, {n_bad} refused or archive-only; {foot['counts'].get('clips')} clips on "
                f"{foot['counts'].get('lines_with_footage')} of {foot['counts'].get('lines')} sentences")
    platform_walled=walled(foot)
    if platform_walled:
        ctx.log('Platform footage unavailable; retain its evidence and seek public original-publisher video before stopping')
    pack = dict(platform_walled=platform_walled,sid=ctx.sid, words=wj, narration=src, ffmpeg=PX.FF, sounds={}, pictures={}, credits=[], footage=[], title=script.get("title"), coverage=coverage)
    pack.update(referent_research=primary_direction['references'],research_spend=primary_direction['spend'])
    subjects, by_line = [], {}
    if foot:
        fdir = os.path.join(sup_dir, footage_name)
        pack["footage"] = [dict(i=ln["i"], clips=[dull(wf3.clip_facts(dict(c, path=os.path.join(fdir, c["file"])))) for c in ln["clips"]]) for ln in foot["lines"]]
        pack["credits"] += [dict(kind="footage", name=c["title"], title=c["title"], licence="footage, not licensed (the owner's decision)", credit=c["channel"],
                                 page=c["url"], time_range=c["time_range"], line=c["line"]) for c in foot["credits"]]
        subjects = [s for s in foot.get("subjects") or [] if s.get("name")]
        by_line = {int(ln["i"]): list(ln.get("subjects") or []) for ln in foot["lines"]}
        for i, window in enumerate(coverage['windows']):
            window['subjects'] = by_line.get(i) or [window['name']]
        spend.append(dict(stage="supply", provider="openai", model="gpt-6-sol low + gpt-4o-transcribe", what="footage", usd=(foot.get("spend") or {}).get("usd")))
        ctx.log(f"footage: {foot['counts']['clips']} clips on {foot['counts']['lines_with_footage']} of {foot['counts']['lines']} sentences; "
                f"subjects {[s['name'] for s in subjects]}")
    # photographs of the same things, for the sentences the footage left bare
    named = [dict(name=s["name"], searches=[s["name"]], must_show=s.get("must_show") or s["name"], **{"is": s.get("kind") or "thing"})
             for s in subjects if any(s["name"] in v for v in by_line.values())]
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
    pack["alignment_contract"]=wf1_alignment.CONTRACT
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
    return len(yt), len([s for s in yt if (s.get("error") and s.get("error_stage")=="fetch") or s.get("rung") == "wayback"])


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
    """Discard technically blank frames; a dark subject still needs the model's readability check."""
    try:
        import numpy as np
        from PIL import Image
        with Image.open(c["still"]) as im:
            g = np.asarray(im.convert("L").resize((160, 160)), dtype=np.float32) / 255.0
        c["luma"] = [round(float(g.mean()), 3), round(float(g.std()), 3)]
        # A dark background is normal in astronomy and some genuine interfaces.
        # Global brightness cannot establish that their visible content is absent.
        contrast=float(np.percentile(g,99.5)-np.percentile(g,.5))
        c['luma_range']=round(contrast,3)
        c["dull"] = contrast < .04
    except Exception:  # noqa: BLE001 - a clip nobody could measure plays
        c["dull"] = False
    return c


def retained_candidates(supply, primary, subjects, revised=False):
    """The ranker may reconsider a retained original for a revised semantic brief."""
    import wf1_primary_sources
    known=wf1_primary_sources.retained(supply)
    for subject,rows in known.items():
        dest=primary.setdefault(subject,[])
        dest.extend(row for row in rows if not any(old['url']==row['url'] for old in dest))
    if revised:
        catalog={row['url']:row for rows in known.values() for row in rows}
        for subject in subjects:
            dest=primary.setdefault(subject,[])
            dest.extend(row for url,row in catalog.items() if not any(old['url']==url for old in dest))
    return primary


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


DIRECTION_CONTRACT = 2           # 19 Nov 2026 (round1): display words by story role, the story's turn apart from the payoff
ROLES = ("hook", "turn", "payoff", "figure", "name", "contrast")


def direction_of(ctx, script, pack):
    """What a model reads off the heard sentences for the film builder: the two to four words to set large, each with
    the job it does in the story (hook, turn, payoff, figure, name, contrast) and the one word of it set largest; the
    sentences that speak of the past; the sentence the payoff starts on (``turn_seg``, where the film cuts to his face);
    the sentence where the story turns (``story_turn_seg``, before the payoff: the music's drop and the sound's turn hit
    when the payoff has no room after it); and the years the story speaks from. Cached in the supply, per contract."""
    p = os.path.join(ctx.supply, "direction.json")
    if os.path.exists(p):
        got = json.load(open(p))
        if got.get("contract") == DIRECTION_CONTRACT:
            return got
    lines = pack["lines"]
    system = ("You direct the type and the finish of a vertical short cut from a podcast, the way the best story reels set "
              "their words: few, large, each doing a job in the story. You read its sentences, numbered, and answer in JSON "
              "only. You never add a word he does not say.")
    system += "\n\n" + ("Return JSON only: {"
               "\"display\": [two to four words or short phrases to set large on screen, each on the word that says it, each doing "
               "one job in the story: the hook that stops a stranger, the turn where it changes direction, the payoff, a figure "
               "that carries the stakes, a name the story hangs on, or the two sides of a contrast. Never a filler, a function "
               "word, a generic noun or a phrase that only restates the topic; fewer is better than a weak one. Each {\"seg\": "
               "the sentence number, \"text\": one to three consecutive words exactly as he says them (a figure as digits, e.g. "
               "$5,000, 49% or 1999), \"role\": one of hook|turn|payoff|figure|name|contrast, \"key\": the one word of text "
               "that carries it (set largest)}; spread over the film, in order, never two from one sentence], "
               "\"past_segs\": [the numbers of the sentences that tell of the past: a year gone by, how something began, what "
               "someone once did; not his opinions about today], "
               "\"payoff_seg\": the number of the sentence where the payoff begins (usually the last or the one before it), "
               "\"turn_seg\": the number of the sentence where the story turns, changes direction or opens its tension (a "
               "setback, a 'but', a decision, the question the payoff answers), before the payoff sentence; null when the "
               "story has no turn before its payoff, "
               "\"arc\": [only when he says a calendar year aloud: {\"seg\": sentence number, \"word\": the year as he says it in "
               "digits, \"year\": the year as a number, \"stakes\": how things stand for the story's hero at that point, -1 (worst) "
               "to 1 (best)}; an empty list when no year is said]}")
    content = ((f"# The film\n{script.get('title')}\nHook: {script.get('hook_line')}\nPayoff: {script.get('close_line')}\n\n# Sentences\n") +
               ("\n".join(f"{ln['i']}: {ln['text']}" for ln in lines)))
    llm = _llm()
    text, use = llm.ask(system, content, effort="medium", max_tokens=12000)
    try:
        d = llm.json_block(text)
    except ValueError:
        d = {}
    d = d if isinstance(d, dict) else {}
    display = []
    for r in d.get("display") or []:
        if isinstance(r, dict):
            r = dict(r, role=r.get("role") if r.get("role") in ROLES else "hook")
            r.setdefault("accent", r["role"] in ("figure", "payoff"))
            display.append(r)
    payoff = d.get("payoff_seg") if isinstance(d.get("payoff_seg"), int) else None
    turn = d.get("turn_seg") if isinstance(d.get("turn_seg"), int) else None
    if payoff is not None and turn is not None and turn >= payoff:
        turn = None                                    # a turn on or after the payoff is the payoff itself
    out = dict(contract=DIRECTION_CONTRACT, display=display[:4],
               past_segs=[int(i) for i in d.get("past_segs") or [] if isinstance(i, int)],
               turn_seg=payoff, story_turn_seg=turn,
               arc=[r for r in d.get("arc") or [] if isinstance(r, dict)][:6],
               spend=dict(stage="score", what="display words by role, past sections, turn and payoff", **use))
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


def source_signature(supply):
    """A newly available original invalidates exhausted research, not previous frame checks."""
    path=os.path.join(supply,'sources','manifest.json')
    rows=json.load(open(path)) if os.path.exists(path) else []
    documents=os.path.join(supply,'document-sources','manifest.json')
    if os.path.exists(documents):rows+=json.load(open(documents))
    available=[]
    for row in rows:
        p=row.get('path','')
        if os.path.isfile(p):
            stat=os.stat(p)
            available.append((row['url'],row.get('sha256'),stat.st_size,stat.st_mtime_ns,
                              row.get('where'),row.get('title'),row.get('channel'),row.get('subjects',[])))
    return hashlib.sha256(json.dumps(sorted(available)).encode()).hexdigest()[:12]


def motion_research(ctx, pack, shortage):
    """Change a static-only acquisition approach once; never relax a picture verdict."""
    if (pack.get('motion_requirements_checked') or not pack.get('coverage_repairs')
            or shortage.get('missing_windows')
            or shortage.get('moving_seconds',0)>=shortage.get('required_moving_seconds',0)):
        return False
    path=os.path.join(ctx.supply,'placement_checks.json')
    checks=json.load(open(path)) if os.path.isfile(path) else {}
    stills=[dict(reason=r.get('reason'),motion_reason=r.get('motion_reason'))
            for r in checks.values() if r.get('ok') and r.get('motion_ok') is False]
    if not stills:return False
    import wf1_coverage, wf1_primary_sources
    coverage=pack['coverage'];wanted=shortage['weak_windows']
    before=json.dumps(coverage['windows'],sort_keys=True)
    evidence=dict(stage='faithful pictures lack subject motion',measurements=shortage,
                  faithful_stills=stills[-24:],
                  available=[dict(i=r['i'],clips=[dict(shows=c.get('shows'),source=c.get('source'),
                    subject_motion=c.get('subject_motion')) for c in r['clips'][:12]]) for r in pack['footage']])
    def save():
        target=os.path.join(ctx.supply,'pack.json');stored=json.load(open(target));stored['pack']=pack
        with open(target,'w') as f:json.dump(stored,f,indent=1)
    # Persist admission before the call: an unchanged failed call is not retried.
    pack['motion_requirements_checked']=True;save()
    use=wf1_coverage.repair_requirements(coverage,wanted,evidence,_llm())
    pack.setdefault('research_spend',[]).append(use)
    changed=json.dumps(coverage['windows'],sort_keys=True)!=before
    if changed:pack['coverage_context_rescout_pending']=True
    save()
    if not changed:return False
    ctx.log('motion verdicts changed acquisition direction; one original-publisher pass on the revised brief')
    original=wf1_primary_sources.acquire([dict(coverage['windows'][i],i=i,context=coverage.get('context'))
        for i in wanted],os.path.join(ctx.supply,'primary-videos'),_llm())
    pack.setdefault('research_spend',[]).extend(original['spend'])
    manifest_path=os.path.join(ctx.supply,'sources','manifest.json')
    manifest=json.load(open(manifest_path)) if os.path.exists(manifest_path) else []
    found=os.path.join(ctx.supply,'primary-videos','manifest.json')
    for rec in json.load(open(found)) if os.path.exists(found) else []:
        prior=next((r for r in manifest if r['url']==rec['url']),None)
        if prior is None:manifest.append(rec)
        elif not os.path.isfile(prior.get('path','')) and os.path.isfile(rec.get('path','')):prior.update(rec)
    os.makedirs(os.path.dirname(manifest_path),exist_ok=True)
    with open(manifest_path,'w') as f:json.dump(manifest,f,indent=1)
    save()
    return True


def refill(ctx, script, pack, shortage):
    """Research missing coverage through the same engines, preserving all checked originals."""
    coverage = pack['coverage']
    windows = coverage['windows']
    wanted = shortage['weak_windows'] or list(range(len(windows)))
    if not pack.get('referent_research'):
        import wf1_context
        found=wf1_context.research(coverage,os.path.join(ctx.supply,'referent-research'),_llm())
        pack['referent_research']=found
        pack.setdefault('research_spend',[]).extend(found['spend'])
    coverage['research_context']=pack['referent_research']
    if pack.get('research_revision',0)<7:
        import wf1_coverage
        checks=os.path.join(ctx.supply,'placement_checks.json')
        evidence=dict(available=[dict(i=r['i'],clips=[dict(subject=c.get('subject'),shows=c.get('shows'),source=c.get('source')) for c in r['clips']]) for r in pack['footage']],
                      rejected=[r['reason'] for r in json.load(open(checks)).values() if not r['ok']][-60:] if os.path.exists(checks) else [])
        usage=wf1_coverage.repair_requirements(coverage,wanted,evidence,_llm())
        pack.setdefault('research_spend',[]).append(usage);pack['research_revision']=7
        stored=json.load(open(os.path.join(ctx.supply,'pack.json')));stored['pack']=pack
        with open(os.path.join(ctx.supply,'pack.json'),'w') as f:json.dump(stored,f,indent=1)
    added=0
    # A still cannot repair a motion deficit. Preserve existing documentary
    # evidence, but spend this recovery pass on moving originals first.
    motion_ready = (shortage.get('real_seconds',0)>0 and
                    shortage.get('moving_seconds',0)>=shortage.get('required_moving_seconds',.6*shortage['real_seconds']))
    if not motion_ready and motion_research(ctx,pack,shortage):
        return
    if (not motion_ready and pack.get('coverage_repairs',0)>=1
            and not pack.get('coverage_context_checked')):
        import wf1_coverage
        available=[dict(i=row['i'],clips=[dict(shows=c.get('shows'),source=c.get('source'),
                       motion_p90=c.get('motion_p90')) for c in row['clips'][:12]])
                   for row in pack['footage']]
        changed,use=wf1_coverage.expand_context(coverage,available,ctx.supply,_llm())
        pack['coverage_context_checked']=True
        if changed:pack['coverage_context_rescout_pending']=True
        pack.setdefault('research_spend',[]).append(use)
        stored=json.load(open(os.path.join(ctx.supply,'pack.json')));stored['pack']=pack
        with open(os.path.join(ctx.supply,'pack.json'),'w') as f:json.dump(stored,f,indent=1)
        if changed:
            ctx.log('director extended related story context; recheck existing pictures on the actual words')
            return
    missing_documents=(not motion_ready and pack.get('coverage_repairs',0)>=1
                       and shortage.get('missing_windows')
                       and not pack.get('missing_document_attempted'))
    if motion_ready or missing_documents:
        import wf1_documents
        context=dict(identity=coverage.get('context'),primary_research=coverage.get('research_context'),prior_errors=[e for r in pack.get('document_research',[]) for e in r.get('errors',[])][-12:])
        cached_path=os.path.join(ctx.supply,'document-sources','manifest.json')
        originals=json.load(open(cached_path)) if os.path.exists(cached_path) else []
        document_windows=shortage['missing_windows'] if missing_documents else wanted
        documents=wf1_documents.acquire([dict(windows[i],i=i,context=context) for i in document_windows],os.path.join(ctx.supply,'primary-documents'),_llm(),sources=originals)
        if missing_documents:pack['missing_document_attempted']=True
        for subject,records in documents['pictures'].items():
            dest=pack['pictures'].setdefault(subject,[]);have={r['path'] for r in dest}
            fresh=[r for r in records if r['path'] not in have];dest.extend(fresh);added+=len(fresh)
        if documents not in pack.setdefault('document_research',[]):pack['document_research'].append(documents)
        stored=json.load(open(os.path.join(ctx.supply,'pack.json')));stored['pack']=pack
        with open(os.path.join(ctx.supply,'pack.json'),'w') as f:json.dump(stored,f,indent=1)
        if added:
            pack['credits']+=documents['credits']
            stored=json.load(open(os.path.join(ctx.supply,'pack.json')));stored['pack']=pack
            with open(os.path.join(ctx.supply,'pack.json'),'w') as f:json.dump(stored,f,indent=1)
            ctx.log(f'acquired {added} primary-document crops; checking their actual words before further research')
            return
    else:
        ctx.log('moving coverage is short; prioritize original video over additional still documents')
    signature=source_signature(ctx.supply)
    previous = pack.get('coverage_repairs',0) if pack.get('research_contract')==RESEARCH_CONTRACT else 0
    recovered_sources = bool(previous and pack.get('research_sources')!=signature)
    count = max(4,int(previous)+1) if pack.get('platform_walled') else int(previous)+1
    # New original bytes merit fresh frame checks, not another cycle of the same
    # exhausted platform searches. Preserve the search budget after this rescan.
    rescout=bool(pack.get('coverage_context_rescout_pending') or recovered_sources)
    last=pack.get('coverage_last_search_report')
    if last and not rescout and count<4:
        improved=any(shortage.get(k,0)>last.get(k,0)+.01
                     for k in ('real_seconds','moving_seconds','new_pictures'))
        improved=improved or len(shortage.get('missing_windows',[]))<len(last.get('missing_windows',[]))
        if not improved:
            ctx.log('platform recovery added no measured coverage; change to original-publisher research')
            count=4
    primary={}
    manifest_path=os.path.join(ctx.supply,'sources','manifest.json')
    retained=json.load(open(manifest_path)) if os.path.exists(manifest_path) else []
    # A changed spoken window can use a different part of a verified original.
    # Source-only rescanning needs its retained identity, not another web search.
    by_url={row['url']:row for row in retained if os.path.isfile(row.get('path',''))}
    for line in pack['footage']:
        subject=windows[int(line['i'])]['name']
        for clip in line['clips']:
            source=clip.get('source') or {};url=source.get('url')
            if url not in by_url or not source.get('title') or not source.get('channel'):continue
            dest=primary.setdefault(subject,[])
            if not any(row['url']==url for row in dest):
                dest.append(dict(by_url[url],**{k:v for k,v in source.items() if k not in ('url','id','where')},
                                 where=source.get('where','youtube'),id=hashlib.sha256(url.encode()).hexdigest()))
    primary=retained_candidates(ctx.supply,primary,[windows[i]['name'] for i in wanted],
                                revised=bool(pack.get('coverage_context_rescout_pending')))
    if count == 4 and not rescout:
        import wf1_primary_sources
        original=wf1_primary_sources.acquire([dict(windows[i],i=i,context=coverage.get('context')) for i in wanted],os.path.join(ctx.supply,'primary-videos'),_llm())
        pack.setdefault('research_spend',[]).extend(original['spend'])
        for subject,rows in original['sources'].items():primary.setdefault(subject,[]).extend(rows)
        source_dir=os.path.join(ctx.supply,'sources');os.makedirs(source_dir,exist_ok=True)
        manifest_path=os.path.join(source_dir,'manifest.json')
        manifest=json.load(open(manifest_path)) if os.path.exists(manifest_path) else []
        for rec in json.load(open(os.path.join(ctx.supply,'primary-videos','manifest.json'))):
            if not any(r['url']==rec['url'] for r in manifest):manifest.append(rec)
        with open(manifest_path,'w') as f:json.dump(manifest,f,indent=1)
        if not primary:
            exhausted(ctx,pack,'no verified public publisher video found after platform research')
            return
    if count > 4 and added and not rescout:
        # Score the newly acquired original once even when video research is exhausted.
        return
    if count > 4 and not rescout:
        exhausted(ctx,pack,'documentary coverage still short after platform and original-publisher research')
        return
    name = f"footage-v{coverage['contract']}-{FOOTAGE_PIXELS}-context{RESEARCH_CONTRACT}-{signature}-repair{count}"
    if rescout:name+='-recovered-sources' if recovered_sources else '-expanded-context'
    prior = [os.path.join(ctx.supply,n,'footage.json') for n in os.listdir(ctx.supply)
             if n.startswith('footage') and os.path.isfile(os.path.join(ctx.supply,n,'footage.json'))]
    subjects = {windows[i]['name']:windows[i]['must_show'] for i in wanted}
    fscript = dict(title=script['title']+' — supplementary documentary coverage. Prefer original institutional or company films with visible action; avoid influencer promotions and unrelated banners.',
                   segments=[dict(i=i,say=w['say'],pictures=[w['name']],visual=w['must_show']) for i,w in enumerate(windows)],
                   named=[dict(name=n,must_show=show,video_sources=primary.get(n,[]),video_searches=list(dict.fromkeys(q for w in windows if w['name']==n for q in w.get('video_searches',[]))),**{'is':'thing'}) for n,show in subjects.items()])
    if rescout:name+='-'+hashlib.sha256(json.dumps(fscript,sort_keys=True).encode()).hexdigest()[:12]
    fw,fh = wf1_film.STYLES[script['style']['id']]['footage']
    ctx.log(f"research missing documentary windows {wanted}; pass {count}")
    foot = chain.footage(ctx,dict(script=fscript),[w['t1']-w['t0'] for w in windows],W=fw,H=fh,
                         preserve_source=True,native_pixels=True,look_itself=True,shot_s=1.15,line_max=20,per=4,lines=wanted,avoid=prior,
                         strict_lines=True,motion_first=True,scout_motion=True,alloc='round',reassign_lost=True,
                         verify=False,whole_first=True,keep_sources=True,hear_s=0,source_only=rescout or count==4 or bool(pack.get('platform_walled')),out_name=name)
    if not foot:
        exhausted(ctx,pack,'supplementary documentary research found nothing new')
        return
    by = {int(row['i']):row for row in pack['footage']}
    for row in foot['lines']:
        i = int(row['i'])
        dest = by.setdefault(i,dict(i=i,clips=[]))
        dest['clips'] += [dull(wf3.clip_facts(dict(c,path=os.path.join(ctx.supply,name,c['file'])))) for c in row['clips']]
    pack['footage'] = list(by.values())
    pack['credits'] += [dict(kind='footage',name=c['title'],title=c['title'],licence="footage, not licensed (the owner's decision)",
                            credit=c['channel'],page=c['url'],time_range=c['time_range'],line=c['line']) for c in foot['credits']]
    pack['coverage_repairs'] = int(previous) if rescout else count
    pack['coverage_last_search_report']=shortage
    if rescout:pack['coverage_context_rescout_pending']=False
    pack['coverage_catalog_revision']=1
    pack['research_contract'] = RESEARCH_CONTRACT
    pack['research_sources'] = source_signature(ctx.supply)
    path = os.path.join(ctx.supply,'pack.json')
    stored = json.load(open(path));stored['pack']=pack
    with open(path,'w') as f:json.dump(stored,f,indent=1)


def save_pack(ctx, pack):
    path=os.path.join(ctx.supply,'pack.json');stored=json.load(open(path));stored['pack']=pack
    with open(path,'w') as f:json.dump(stored,f,indent=1)


def exhausted(ctx, pack, why):
    """Research has nothing more to find: the film is built from every approved picture (wf1_coverage final)."""
    pack['coverage_exhausted']=why
    save_pack(ctx,pack)
    ctx.log(why+'; the film is built from every approved picture and the speaker carries the rest')


def coverage_final(pack):
    return bool(pack.get('coverage_exhausted')) or int(pack.get('coverage_recoveries') or 0)>=RECOVERY_PASSES


def score(ctx, script):
    import wf1_coverage, wf1_alignment
    pack = wf3.load_pack(ctx)
    if ((pack.get('coverage') or {}).get('contract') != wf1_coverage.CONTRACT
            or pack.get('alignment_contract')!=wf1_alignment.CONTRACT
            or (not pack.get('platform_walled') and not any(row.get('clips') for row in pack.get('footage',[])))):
        supply(ctx,script)
        pack = wf3.load_pack(ctx)
    pack["work"] = ctx.supply
    refresh=False
    if pack.get('blank_frame_contract')!=1:
        for row in pack.get('footage',[]):
            for clip in row['clips']:dull(clip)
        pack['blank_frame_contract']=1;refresh=True
    if pack.get('composed_requirement_windows') and pack.get('coverage_catalog_revision')!=1:
        pack['coverage_context_rescout_pending']=True;refresh=True
    if refresh:
        path=os.path.join(ctx.supply,'pack.json');stored=json.load(open(path));stored['pack']=pack
        with open(path,'w') as f:json.dump(stored,f,indent=1)
    if (ctx.args or {}).get('identity'):
        pack['coverage']['context']=ctx.args['identity']
    d = direction_of(ctx, script, pack)
    knobs = {k: d.get(k) for k in ("display", "past_segs", "turn_seg", "story_turn_seg", "arc")}
    knobs.update({k: v for k, v in ((ctx.args or {}).get("direction") or {}).items()})
    rejected_path = os.path.join(ctx.supply, "render_rejected.json")
    if os.path.exists(rejected_path):
        knobs["drop"] = sorted(set(knobs.get("drop") or []) | set(current_rejections(ctx.supply)))
    knobs.setdefault("bed", chain.wants_music(ctx) or bool((ctx.args or {}).get("song")))      # the film gets music: the sounds are designed for a film with a bed
    sys.path[:0] = [p for p in wf3.env_path().split(os.pathsep) if p not in sys.path]
    os.environ["PATH"] = os.path.dirname(pack["ffmpeg"]) + os.pathsep + os.environ.get("PATH", "")
    sc = dict(title=script.get("title"), hook_line=script.get("hook_line"), close_line=script.get("close_line"),
              segments=[dict(say=ln["text"], pictures=[n for n in ln.get("subjects") or []]) for ln in pack["lines"]])
    for attempt in range(RECOVERY_PASSES + 2):
        knobs["coverage_final"] = coverage_final(pack)
        try:
            l1, beats = wf1_film.build(sc, pack, script["style"]["id"], knobs)
            break
        except wf1_coverage.CoverageError as error:
            # bounded recovery, counted across resumes and rebuilds: after RECOVERY_PASSES the build is final
            pack['coverage_recoveries']=int(pack.get('coverage_recoveries') or 0)+1
            save_pack(ctx,pack)
            refill(ctx,script,pack,error.report)
            save_pack(ctx,pack)
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
    # the story's own turn, before the payoff: music_film --turn (the drop when the payoff has no room after it)
    story_turn_s = round(beats["story_turn_f"] / F.FPS, 3) if beats.get("story_turn_f") is not None else None
    return dict(l1=out, l1_audio=l1_audio, turn_s=turn_s, story_turn_s=story_turn_s, beats={k: v for k, v in beats.items() if k not in ("look",)}, taste=taste,
                frames=l1["dur_f"], items=len(l1["items"]), direction={k: v for k, v in d.items() if k != "spend"}, spend=([d["spend"]] if d.get("spend") else []) +
                [dict(u,stage='supply',what='primary documents') for research in pack.get('document_research',[]) for u in research.get('spend',[])] +
                [dict(u,stage='supply',what='documentary research revision') for u in pack.get('research_spend',[])] +
                [dict(stage="score", what="picture-to-words check", **u) for u in (beats.get("placement_check") or {}).get("spend", [])])


def _render_review(ctx, rd, sc):
    """Check the composed phone frames: source approval cannot certify a crop or a tiny print."""
    from placement_check import check as placement_check
    pack = wf3.load_pack(ctx)
    script = dict(segments=[dict(say=ln["text"]) for ln in pack["lines"]])
    clock = F.Clock(pack["words"], script)
    l1 = json.load(open(sc["l1"]))
    display = [it for it in l1["items"] if it.get("k") == "word" and it.get("role") == "big"]
    captions = [it for it in l1["items"] if it.get("k") == "word" and it.get("role") == "cap"]
    rows = []
    for p in sc["beats"].get("pictures") or []:
        a, b = p.get("visible_f") or p["f"]
        words = [dict(f=it["f"], text=it["t"]) for it in display if it["f"][0] < b and it["f"][1] > a]
        caption_words = [dict(f=it["f"], text=it["t"]) for it in captions if it["f"][0] < b and it["f"][1] > a]
        frames = {a, (a+b)//2, max(a,b-1)}
        for word in words:
            frames.update(f for edge in word["f"] for f in (edge-1,edge,edge+1) if a <= f < b)
        rows.append([a, b, "clip", p["name"], dict(path=rd["film"], src=[a/F.FPS,b/F.FPS],
                    check_times=[f/F.FPS for f in sorted(frames)], composed_fps=F.FPS, display_words=words, caption_words=caption_words,
                    illustration=p.get('illustration'),
                    source=p.get('source_evidence') or dict(url=p.get("source")))])
    # One look at the composed frames carries both the picture-to-words check and the adversarial transition audit
    # (placement_check.COMPOSED). The second pass over the same frames, after the first had passed them, cost half the
    # render stage's calls; every picture has still been checked twice before it ships: on its source frames in the
    # score stage and here, on the phone frames the viewer sees.
    _, report = placement_check(rows, script, clock, os.path.join(ctx.supply, "render-review"), pack["ffmpeg"],
                                review="composed")
    for row in report["placements"]:
        row["transition_audit"] = dict(ok=row.get("ok") is True, reason=row.get("reason", ""), review="composed")
    real=sum(p['f'][1]-p['f'][0] for p in report['placements'])/F.FPS
    moving=sum(p['f'][1]-p['f'][0] for p in report['placements'] if p.get('motion_ok') is True)/F.FPS
    report.update(real_seconds=real,moving_seconds=moving,moving_share=moving/max(real,.001))
    return report


def render_revision(sc):
    files=[sc.get('l1'),sc.get('l1_audio')]
    if not files[0]:return None
    digest=hashlib.sha256()
    for path in files:
        if path and os.path.isfile(path):digest.update(open(path,'rb').read())
    # Audio fixes must invalidate both an engine soundtrack and a cached final mix.
    worker=os.environ.get('VALMERA_WORKER')
    if worker:
        for rel in ('score/music.py','score/audibility.py','score/sound.py','score/sfx_quality.json','scene/pace.py'):
            path=os.path.join(worker,rel)
            if os.path.isfile(path):digest.update(open(path,'rb').read())
    return digest.hexdigest()


def invalidate_render(ctx):
    for name in ('film_engine.mp4','film_engine.mp4.log.json','sound.m4a',
                 'film_engine_music.mp4','music.json'):
        path=os.path.join(ctx.supply,name)
        if os.path.exists(path):os.remove(path)
    for name in ('final.mp4','final_music.mp4','final_song.mp4'):
        path=os.path.join(ctx.run_dir,name)
        if os.path.exists(path):os.remove(path)


def render(ctx, script, sc):
    """The product repairs rejected placements before branding and music, never a delivered film."""
    history_path = os.path.join(ctx.supply, "render_reviews.json")
    history = json.load(open(history_path)) if os.path.exists(history_path) else []
    # A pause can occur after persisting a rejection but before saving its rebuilt
    # score. Resume that repair before rendering the stale, already-rejected plan.
    rejected=set(current_rejections(ctx.supply))
    rejected_plan=any(p.get('path') in rejected for p in sc.get('beats',{}).get('pictures',[]))
    stale_captions=('caption_lines' in sc.get('beats',{})
                    and sc['beats'].get('caption_contract')!=1)
    if rejected_plan or stale_captions:
        if history and rejected_plan:
            _repair_window_boundaries(ctx,[r for r in history[-1]['placements'] if not r['ok']])
        previous_spend=sc.get('spend',[])
        rebuilt=score(ctx,script)
        rebuilt['spend']=previous_spend+[u for u in rebuilt.get('spend',[]) if u not in previous_spend]
        sc.clear();sc.update(rebuilt)
        json.dump(sc,open(os.path.join(ctx.run_dir,'score.json'),'w'),indent=1)
        invalidate_render(ctx)
    while True:
        revision=render_revision(sc)
        marker=os.path.join(ctx.supply,'render_revision.json')
        previous=json.load(open(marker)) if os.path.exists(marker) else None
        if revision and revision!=previous:invalidate_render(ctx)
        rd = wf3.render(ctx, script, sc)
        if revision:json.dump(revision,open(marker,'w'))
        report = _render_review(ctx, rd, sc)
        history.append(report)
        json.dump(history, open(history_path, "w"), indent=1)
        sc["beats"]["rendered_check"] = report
        if not report["rejected"] and report["moving_share"]<.6 and _rebuild_motion(ctx,script,sc,report):
            continue
        if not report["rejected"]:
            json.dump(sc, open(os.path.join(ctx.run_dir, "score.json"), "w"), indent=1)
            rd["picture_review"] = history
            rd["spend"] = [dict(stage="render", what="composed picture-to-words check", **u) for h in history for u in h.get("spend", [])]
            return rd
        _rebuild_rejected(ctx, script, sc, report)


def _rebuild_motion(ctx, script, sc, report):
    """Keep faithful stills, but never recertify their card motion as footage. One research pass for more moving
    footage per short; after it (or when nothing new is still), the film stands on its approved pictures and the
    record keeps its measured moving share. Returns True when the film was rebuilt."""
    pack=wf3.load_pack(ctx)
    pics=sc['beats'].get('pictures') or []
    stills={pics[row['id']]['path'] for row in report['placements'] if row.get('motion_ok') is not True}
    previous=set(pack.get('composed_stills') or [])
    if not stills-previous or pack.get('motion_recovered'):
        ctx.log(f"moving share {report.get('moving_share',0):.2f} after recovery; the film keeps its approved stills")
        return False
    pack['motion_recovered']=True
    pack['composed_stills']=sorted(previous|stills)
    path=os.path.join(ctx.supply,'pack.json');stored=json.load(open(path));stored['pack']=pack
    json.dump(stored,open(path,'w'),indent=1)
    ctx.log('final frames require more subject motion; retaining truthful stills and researching footage')
    old_spend=sc.get('spend',[])
    rebuilt=score(ctx,script)
    rebuilt['spend']=old_spend+[u for u in rebuilt.get('spend',[]) if u not in old_spend]
    sc.clear();sc.update(rebuilt)
    json.dump(sc,open(os.path.join(ctx.run_dir,'score.json'),'w'),indent=1)
    invalidate_render(ctx)
    return True


def current_rejections(supply):
    from placement_check import CONTRACT
    path = os.path.join(supply, "render_rejected.json")
    marker = os.path.join(supply, "render_rejected_contract.json")
    # A changed frame/layout contract requires fresh source AND composed checks;
    # it never grants approval to a formerly rejected asset.
    if not os.path.exists(marker) or json.load(open(marker)) != CONTRACT:
        return []
    return json.load(open(path)) if os.path.exists(path) else []


def _repair_window_boundaries(ctx, rejected):
    import wf1_coverage
    pack=wf3.load_pack(ctx)
    wf1_coverage.repair_boundaries(pack['coverage'],rejected,_llm())
    coverage=pack['coverage'];windows=coverage['windows']
    reviewed=set(pack.get('composed_requirement_windows',[]))
    wanted=set()
    for row in rejected:
        bounds=row.get('f') or []
        if len(bounds)!=2:continue
        mid=(sum(bounds)/2-3)/30
        wanted.update(i for i,w in enumerate(windows) if w['t0']<=mid<w['t1'] and i not in reviewed)
    if wanted:
        before=json.dumps(windows,sort_keys=True)
        usage=wf1_coverage.repair_requirements(coverage,sorted(wanted),
            dict(stage='composed-frame rejection',rejected=rejected),_llm())
        pack.setdefault('research_spend',[]).append(usage)
        pack['composed_requirement_windows']=sorted(reviewed|wanted)
        if json.dumps(windows,sort_keys=True)!=before:
            pack['coverage_context_rescout_pending']=True
            ctx.log('composed feedback revised acquisition requirements; recheck retained originals on the unchanged words')
    path=os.path.join(ctx.supply,'pack.json');stored=json.load(open(path));stored['pack']=pack
    json.dump(stored,open(path,'w'),indent=1)
    json.dump(pack['coverage'],open(os.path.join(ctx.supply,'coverage.json'),'w'),indent=1)


def _rebuild_rejected(ctx, script, sc, report):
    """Monotonic repair shared by the engine and exact delivery-frame gates."""
    pics = sc["beats"].get("pictures") or []
    # Every failed pass permanently removes at least one source asset.
    # The fixed supply makes this finite; keep independently approved
    # pictures instead of discarding the whole montage after three passes.
    doomed = [pics[r["id"]] for r in report["placements"] if not r["ok"]]
    dropped = [p["path"] for p in doomed]
    rejected_path = os.path.join(ctx.supply, "render_rejected.json")
    previous = current_rejections(ctx.supply)
    if not set(dropped) - set(previous):
        raise RuntimeError("composed picture repair made no progress; no film may be delivered")
    json.dump(sorted(set(previous+dropped)), open(rejected_path, "w"), indent=1)
    from placement_check import CONTRACT
    json.dump(CONTRACT, open(os.path.join(ctx.supply,"render_rejected_contract.json"),"w"))
    ctx.log(f"composed picture check: rebuilding without {len(dropped)} rejected placements")
    # Replacing a correct subject with another picture of that subject cannot
    # repair a cut that arrives after the next named object. Let the director
    # correct that boundary; all rejected assets remain rejected regardless.
    rejected=[r for r in report['placements'] if not r['ok']]
    _repair_window_boundaries(ctx,rejected)
    old_spend = sc.get("spend", [])
    rebuilt = score(ctx, script)
    rebuilt["spend"] = old_spend + [u for u in rebuilt.get("spend", []) if u not in old_spend]
    sc.clear()
    sc.update(rebuilt)
    json.dump(sc, open(os.path.join(ctx.run_dir, "score.json"), "w"), indent=1)
    invalidate_render(ctx)




def check(ctx, rd, sc):
    # The render stage already checked every placement on the composed phone frames (picture-to-words and the
    # transition audit) and repaired what failed. The brand finish and the music change no programme frame: they add
    # the mark, the end card and the sound. A second review of the same frames cost 50-350 model calls a short on
    # 16 Nov and found nothing the render review had not; the delivered record carries that review.
    r = wf3.check(ctx, rd, sc)
    b = sc.get("beats") or {}
    history = [dict(b["rendered_check"], frames="the render review's composed frames; the delivered programme is the same")] \
        if b.get("rendered_check") else []
    if history:
        b["delivered_check"] = history[-1]
        json.dump(sc, open(os.path.join(ctx.run_dir, "score.json"), "w"), indent=1)
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
    audit=(ctx.args or {}).get('audit_out')
    if audit:retain_audit(ctx,audit)
    return r


def retain_audit(ctx,audit):
    """Retain the actual final coverage ruler, including researched context extensions."""
    import shutil
    os.makedirs(audit,exist_ok=True)
    for name in ('pack.json','words.json','index.json','l1.json','l1_audio.json','music.json','direction.json',
                 'beats.json','mix_stems.npz','render_reviews.json','delivery_reviews.json'):
        path=os.path.join(ctx.supply,name)
        if os.path.isfile(path):shutil.copy2(path,os.path.join(audit,name))
    # coverage.json is the initial planning cache; acquisition can subsequently
    # expand timed windows in pack.json. The shorter initial ruler would inflate
    # new-pictures-per-second in an independent final-film audit.
    with open(os.path.join(audit,'coverage.json'),'w') as f:
        json.dump(wf3.load_pack(ctx)['coverage'],f,indent=1)
    for name in ('script.json','score.json'):
        shutil.copy2(os.path.join(ctx.run_dir,name),os.path.join(audit,name))


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
