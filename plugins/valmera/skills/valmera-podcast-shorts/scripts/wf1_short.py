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
import time

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
DIRECTION_CONTRACT = wf1_film._director().CONTRACT
import wf3  # noqa: E402

FOOTAGE_PIXELS = "native"  # clips keep their source pixels for full-bleed framing (16 Nov); a fitted 1080x608 shelf is not reused
RESEARCH_CONTRACT = 7  # primary documents can fill a truthful still-picture gap before more video research
# Research passes a short may spend on missing documentary coverage before its film is built from every approved picture
# (owner, 16 Nov 2026: many publishable shorts per podcast). Up to 7 passes, each a full footage search plus fresh
# frame checks, made one short cost 600-1,400 model calls on 15-16 Nov and lost 3 of 10 moments outright.
RECOVERY_PASSES = 2
# The delivered programme measured on its pixels (measure_cuts) feeds supply: a stretch with no cut longer than this,
# or fewer cuts than this a 10 s, sends the windows under it back to research once a short (never a gate: the film is
# delivered whatever the second measure says). Round 1, 19 Nov: holds of 34 s and 15 s, and a film with no cut at all.
RESEARCH_HOLD_S = 6.0
RESEARCH_CUTS_10S = 4.0
# Below each coverage window's own depiction, up to this many lower rungs the plan names (wf1_coverage.plan): a window
# research left empty takes the next rung before the final build (19 Nov: m03 ran 34 s on the speaker once its windows'
# pictures were rejected, m10 22 s with no cutaway).
LADDER_RUNGS = 3


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
              "of tail. The clip must stand alone for a stranger who never saw the episode. The moment's anchors are measured: "
              "the clip opens on the hook sentence's first content word and closes on the payoff's last word; one continuous "
              "window, never an interior stretch that loses either.")
    system += '\n\nReturn JSON only: {"in": seconds (0.15-0.3 s before the first word), "out": seconds (0.3-0.8 s after the last word; the clip is 18-55 s), "title": the film\'s title in at most five words, a headline a stranger stops for, written from the words between your in and out only: it promises nothing the clip does not deliver (a count or a list it names is whole inside the clip), "hook_line": the exact words heard first, "close_line": the exact last words, "speakers": number of people heard, "named": [the real people, companies, places, products and events this clip names or plainly points to, that a camera has filmed; each {"name": its usual English name, "is": person|company|place|product|event|thing, "must_show": one plain sentence on what footage of it must visibly show, as it was at the time he speaks of}; never this conversation itself, which is on screen already; when he tells of something he did himself, that filmed moment of him (a livestream, a keynote, his training, an event he took part in) is an event to name like any other], "brief": two sentences on the story and its mood, for the editor who chooses the music}.'
    system += (" Include claims for any nested speech or forecast: [{start,end,speaker,subject,event,report}]. "
               "Use absolute episode seconds bounding just that claim. speaker is the person actually heard "
               "(use The host or The guest when the name is uncertain); subject is whose statement is described "
               "(the same person for a firsthand forecast). event names the original occasion if known. "
               "report is a short card line (aim for 6–12 words). A separate byline names the reporter: omit that "
               "name from the line. Name the subject, use conditional language for a personal forecast, and preserve "
               "uncertainty, timing and scope. For estimates, include stance (discussed_estimate when a speaker questions or cites an estimate, "
               "personal_estimate for their own assessment). A hypothetical with two linked figures is one thought: keep the "
               "quantity, consequence and uncertainty together in one concise report. A challenge to an estimate can be a "
               "plain question on the card. Distinguish apparent precision from evidence. In a first-person assessment "
               "let the byline identify its author. Describe both the stakes and how the "
               "delivery shifts into the payoff in the music brief. Include speaking_turns:[{start,end,speaker}] on the same episode clock "
               "for quoting the interview itself. Distinguish the host from the guest using the identity and dialogue.")
    anchors = (f"# The moment's anchors, measured on the words\nThe hook opens at {float(idea['t0']):.2f} s: {idea.get('hook') or ''}\n"
               f"The payoff closes at {float(idea['t1']):.2f} s: {idea.get('payoff') or ''}\n"
               f"Measured length: {float(idea['t1']) - float(idea['t0']):.1f} s\n\n")
    content = f"# The moment\n{json.dumps(idea, ensure_ascii=False)}\n\n{anchors}# Who is in it\n{str((ctx.args or {}).get('identity') or '')[:1500]}\n\n# Words near the moment: seconds word\n" + ' '.join((f'{t:.2f} {w}' for (t, w) in near))
    llm = _llm()
    spend, s, note = [], {}, ""
    for _ in range(2 if os.environ.get('CODEX_PROXY_LOCAL') == '1' else 3):
        text, use = llm.ask(system, content + note, effort="medium", max_tokens=16000)
        spend.append(dict(stage="script", what="clip, title and what it names", **use))
        try:
            s = llm.json_block(text)
        except ValueError:
            s = {}
        if not (isinstance(s, dict) and all(s.get(k) not in (None, "") for k in ("in", "out", "title"))):
            note = "\n\nYour last answer was not the JSON asked for."
            continue
        try:
            span = float(s["out"]) - float(s["in"])
        except (TypeError, ValueError):
            note = "\n\nin and out must be numbers of seconds."
            continue
        if 8.0 <= span <= 75.0:
            break
        # measured, handed back: the same model chooses again with the length it gave (no clamp, no stop)
        note = (f"\n\nYour clip {s['in']}-{s['out']} runs {span:.1f} s; a short is 18-55 s. Choose one continuous window inside "
                "the moment that keeps a complete hook and a complete payoff.")
    else:
        # the model gave no short window: the moment's own measured anchors are the clip
        if not (isinstance(s, dict) and s.get("title")):
            s = dict(title=idea.get("title"), hook_line=idea.get("hook"), close_line=idea.get("payoff"))
        s.update({"in": round(max(float(idea["t0"]) - 0.2, 0.0), 2), "out": round(float(idea["t1"]) + 0.5, 2)})
    t0, t1 = float(s["in"]), float(s["out"])
    named = [r for r in s.get("named") or [] if isinstance(r, dict) and r.get("name")]
    return dict(title=s.get("title") or idea.get("title"), style=reg, window=[round(t0, 2), round(t1, 2)], source=ctx.source,
                hook_line=s.get("hook_line") or idea.get("hook"), close_line=s.get("close_line") or idea.get("payoff"),
                speaking_turns=[t for t in s.get("speaking_turns") or [] if isinstance(t, dict)],
                claims=[c for c in s.get("claims") or [] if isinstance(c, dict)],
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
        current = ((prior['pack'].get('coverage') or {}).get('contract') == wf1_coverage.CONTRACT
                   and prior['pack']['coverage'].get('story') == wf1_coverage.story_context(script)
                   and prior['pack'].get('alignment_contract')==wf1_alignment.CONTRACT)
        if current and prior['pack'].get('platform_walled'):
            # A wall left this short waiting: the exact sources YouTube refused are fetched again (nothing is searched
            # or asked anew) and cut; a walled pack is never final.
            return recover_wall(ctx, script, prior['pack'], prior['record'], waits=WALL_RESUME_S)
        if current:
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
    words = wf1_alignment.caption_words(idx)
    dur = float(idx["video"]["duration"])
    wj = os.path.join(sup_dir, "words.json")
    # the voice runs the whole take: the breath the clip keeps after its last word is heard, not cut at the word
    json.dump(dict(voice="the take", duration=round(dur, 3) if words else dur, words=words), open(wj, "w"), ensure_ascii=False)
    spend = [dict(stage="supply",what="heard clip alignment",**idx["alignment"]["spend"])]
    style = wf1_film.STYLES[script["style"]["id"]]
    # who speaks in each of the show's camera shots, by signal: the take frames the speaker, and the stretches the
    # show's camera holds a listener are pictures the director plans like any other
    cuts = take_cuts(src, idx.get("shots") or [], dur, sup_dir, turns=caption_turns(ctx, script["window"], dur))
    # real video of what each sentence names: the footage engine's own planner reads the sentences (OpenAI)
    who = str((ctx.args or {}).get("identity") or "")[:400].replace("\n", " ")
    import wf1_coverage
    # The script stage read the whole moment and resolved what it names, each with the event-level picture it needs
    # (script.named: "Alexandr Wang", "Mark Zuckerberg eating watermelon on a Meta livestream"). They are the
    # director's subjects from the start, for the plan and for the referent research alike.
    import wf1_source_frame
    frames = wf1_source_frame.samples(src, idx.get("shots"), dur, sup_dir, PX.FF)
    coverage = wf1_coverage.plan(words, script.get('title'), sup_dir, _llm(), named=script.get('named'),
                                 off_camera=off_camera(cuts, words), frames=frames, story=wf1_coverage.story_context(script),
                                 speaking_turns=local_turns(script))
    coverage['claims'] = script.get('claims') or []
    original = coverage.get('source_reading') or {}
    coverage['windows'] = [w for i,w in enumerate(coverage['windows']) if i not in original.get('replaces',[])]
    if original:
        coverage['source_reading'] = dict(original,replaces=[])  # the pack now numbers external windows only
    if original.get('spend'):
        spend.append(dict(stage="supply",what="interview source composition",**original['spend']))
    coverage['context']=who
    import wf1_context
    primary_direction = (wf1_context.direct(coverage, os.path.join(sup_dir, 'referent-research'), _llm())
                         if coverage['windows'] else dict(references={}, spend=[]))
    windows = coverage['windows']
    # Inventory records what was said; windows specify what can truthfully be photographed.
    # Passing the inventory back as search subjects resurrects unnamed people and isolated dates.
    research = {}
    for window in windows:
        research.setdefault(window['name'],[]).append(window['must_show'])
    import wf1_primary_sources
    originals=wf1_primary_sources.retained(sup_dir)
    fscript = dict(title=footage_title(script.get('title'), who, coverage),
                   segments=[dict(say=s['say'], pictures=[s['name']], visual=s['must_show']) for s in windows],
                   named=[dict(name=name,must_show=' '.join(requirements),
                               video_sources=originals.get(name,[]),
                               video_searches=list(dict.fromkeys(q for w in windows if w['name']==name for q in w.get('video_searches',[]))),
                               **{'is':'thing'}) for name,requirements in research.items()])
    direction_key=hashlib.sha256(json.dumps(fscript,sort_keys=True).encode()).hexdigest()[:16]
    footage_name = f'footage-v{wf1_coverage.CONTRACT}-{FOOTAGE_PIXELS}-{direction_key}'
    wf3.given_footage(ctx, os.path.join(sup_dir, footage_name))
    fw, fh = wf1_film.FOOTAGE_FRAME                       # clips fill the phone in every style: fetched at its full size
    # This workflow uses the engine's contextual-check route, as WF3 does. Every SELECTED
    # cut gets entry/mid/exit checks against its actual words in wf1_film, then composed
    # and branded transition checks. The generic shelf verifier has no placement context
    # and incorrectly requires a montage's entire brief inside each individual 1 s shot.
    foot = chain.footage(ctx, dict(script=fscript), [round(float(s["t1"]) - float(s["t0"]), 2) for s in windows],
                         W=fw, H=fh, preserve_source=True, native_pixels=True, look_itself=True, shot_s=1.15, line_max=20, per=5,
                         strict_lines=True, motion_first=True, scout_motion=True, alloc="round", reassign_lost=True,
                         verify=False, whole_first=True, keep_sources=True, hear_s=0, out_name=footage_name) if windows else None
    if foot:
        n_yt, n_bad = refused(foot)
        ctx.log(f"footage sources: {n_yt} on YouTube, {n_bad} refused or archive-only; {foot['counts'].get('clips')} clips on "
                f"{foot['counts'].get('lines_with_footage')} of {foot['counts'].get('lines')} sentences")
    platform_walled=walled(foot)
    if platform_walled:
        ctx.log('YouTube refused most footage sources (its wall): the exact refused sources are fetched again, '
                'with backoff, on every route before the film is built')
    pack = dict(platform_walled=platform_walled,sid=ctx.sid, words=wj, narration=src, ffmpeg=PX.FF, sounds={}, pictures={}, credits=[], footage=[], title=script.get("title"), coverage=coverage)
    if platform_walled:
        pack['wall'] = dict(sources=refused(foot)[0], refused=refused_rows(foot), since=time.time())
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
    import wf1_speaker
    pack["take"] = dict(path=src, w=int(v["width"]), h=int(v["height"]), fps=float(v["fps"]), dur=dur, faces=faces, still=still, shots=len(idx.get("shots") or []),
                        cuts=[{k: c[k] for k in c if k != "measures"} for c in cuts], on_camera=wf1_speaker.on_camera(cuts))
    pack["lines"] = [dict(i=i, t0=s["t0"], t1=s["t1"], text=s["text"], subjects=by_line.get(i) or []) for i, s in enumerate(sents)]
    rec = dict(rec0, narration_s=dur, voice="the take", gate=None, pictures=sum(len(x) for x in pack["pictures"].values()), subjects=len(pack["pictures"]),
               footage=(dict(clips=foot["counts"]["clips"], lines=foot["counts"]["lines_with_footage"], credits=foot["credits"]) if foot else None), spend=spend)
    json.dump(dict(pack=pack, record=rec), open(pk, "w"), ensure_ascii=False, indent=1)
    if platform_walled:
        return recover_wall(ctx, script, pack, rec)
    return rec


# Retry exact refused URLs before completing the short on the interview and available evidence.
WALL_BACKOFF_S = (120, 300)
WALL_RESUME_S = (0,)


def footage_title(title, who, coverage=None):
    """What the footage engine is told the film is. The current interview take is on screen already, so it is never a
    cutaway; the speaker's own filmed past (a livestream, a keynote, an event he took part in) is archive like any
    other and is wanted when the words tell of it."""
    story = (coverage or {}).get("story") or {}
    meaning = story.get("meaning") or story.get("brief")
    context = f" Story: {meaning}. Acquire only the supplied external windows." if meaning else ""
    return (f"{title}. A podcast clip; its own interview take is on screen already, so never show this conversation; "
            f"show what he talks about as it was then. When he tells of something he did himself (a livestream, a "
            f"keynote, his training, an event he took part in), that filmed moment of him then is archive and is "
            f"wanted. Who is in it: {who}{context}")


def refused_rows(foot):
    """The refused YouTube sources of one footage record, exactly as the search found them: url, title, channel,
    duration and the subjects (window names) they were found for."""
    names = [s.get("name") for s in (foot or {}).get("subjects") or []]
    out = []
    for s in (foot or {}).get("sources") or []:
        if s.get("where") not in (None, "youtube") or not s.get("url"):
            continue
        if not ((s.get("error") and s.get("error_stage") == "fetch" and wall_error(s["error"])) or s.get("rung") == "wayback"):
            continue
        subjects = [names[i] for i in s.get("subjects") or [] if isinstance(i, int) and 0 <= i < len(names) and names[i]]
        out.append(dict(url=s["url"], title=s.get("title"), channel=s.get("channel"), where="youtube",
                        duration=s.get("duration"), subjects=subjects, error=str(s.get("error") or s.get("rung"))[:240]))
    return out


def wall_state(ctx, pack):
    """The wall this short waits on: {sources: YouTube sources of the pass that met it, refused: [rows]}. A pack written
    before the rows were kept has them read back from its footage records."""
    wall = pack.get("wall")
    if isinstance(wall, dict) and isinstance(wall.get("refused"), list):
        return wall
    import glob
    n, rows, seen = 0, [], set()
    for path in sorted(glob.glob(os.path.join(ctx.supply, "footage*", "footage.json"))):
        try:
            foot = json.load(open(path))
        except (OSError, ValueError):
            continue
        if not walled(foot):
            continue
        n += refused(foot)[0]
        for row in refused_rows(foot):
            if row["url"] not in seen:
                seen.add(row["url"])
                rows.append(row)
    return dict(sources=n, refused=rows, since=time.time())


def has_footage(pack):
    return any(row.get('clips') for row in pack.get('footage') or [])


def still_walled(wall):
    n, bad = int(wall.get("sources") or 0), len(wall.get("refused") or [])
    return n >= 2 and bad * 2 >= n


def merge_footage(ctx, pack, foot, name):
    """A footage record's clips and credits joined to the pack's, line by line (a line is a coverage window)."""
    by = {int(row['i']): row for row in pack['footage']}
    for row in foot.get('lines') or []:
        i = int(row['i'])
        dest = by.setdefault(i, dict(i=i, clips=[]))
        dest['clips'] += [dull(wf3.clip_facts(dict(c, path=os.path.join(ctx.supply, name, c['file'])))) for c in row['clips']]
    pack['footage'] = sorted(by.values(), key=lambda r: int(r['i']))
    pack['credits'] += [dict(kind='footage', name=c['title'], title=c['title'], licence="footage, not licensed (the owner's decision)",
                             credit=c['channel'], page=c['url'], time_range=c['time_range'], line=c['line']) for c in foot.get('credits') or []]


def reopened_footage(ctx, script, pack, opened):
    """The reopened originals cut like any other source: the footage engine's source-only pass over exactly them (no
    new search), with its frame and motion checks, for the windows they were found for. Returns the clips added."""
    import wf1_primary_sources
    coverage = pack['coverage']
    windows = coverage['windows']
    urls = {r['url'] for r in opened}
    names = {n for r in opened for n in r.get('subjects') or []}
    wanted = [i for i, w in enumerate(windows) if w['name'] in names or names & set(w.get('subjects') or [])] or list(range(len(windows)))
    kept = {n: [r for r in rows if r['url'] in urls] for n, rows in wf1_primary_sources.retained(ctx.supply).items()}
    every = list({r['url']: r for rows in kept.values() for r in rows}.values())
    if not every:
        return 0
    named = []
    for n in dict.fromkeys(windows[i]['name'] for i in wanted):
        named.append(dict(name=n, must_show=' '.join(dict.fromkeys(windows[i]['must_show'] for i in wanted if windows[i]['name'] == n)),
                          video_sources=kept.get(n) or every, video_searches=[], **{'is': 'thing'}))
    fscript = dict(title=footage_title(script.get('title'), str(coverage.get('context') or '')[:400], coverage),
                   segments=[dict(i=i, say=w['say'], pictures=[w['name']], visual=w['must_show']) for i, w in enumerate(windows)],
                   named=named)
    name = (f"footage-v{coverage['contract']}-{FOOTAGE_PIXELS}-reopened-"
            + hashlib.sha256(json.dumps([sorted(urls), wanted], sort_keys=True).encode()).hexdigest()[:12])
    if name in (pack.get('merged_footage') or []):
        return 0
    prior = [os.path.join(ctx.supply, n, 'footage.json') for n in os.listdir(ctx.supply)
             if n.startswith('footage') and os.path.isfile(os.path.join(ctx.supply, n, 'footage.json'))]
    fw, fh = wf1_film.STYLES[script['style']['id']]['footage']
    foot = chain.footage(ctx, dict(script=fscript), [w['t1'] - w['t0'] for w in windows], W=fw, H=fh,
                         preserve_source=True, native_pixels=True, look_itself=True, shot_s=1.15, line_max=20, per=4, lines=wanted,
                         avoid=prior, strict_lines=True, motion_first=True, scout_motion=True, alloc='round', reassign_lost=True,
                         verify=False, whole_first=True, keep_sources=True, hear_s=0, source_only=True, out_name=name)
    if not foot:
        return 0
    merge_footage(ctx, pack, foot, name)
    pack.setdefault('merged_footage', []).append(name)
    return int((foot.get('counts') or {}).get('clips') or 0)


def recover_wall(ctx, script, pack, rec=None, waits=None, sleep=None):
    """Retry the refused originals, retain their history, and continue on the take after exhaustion."""
    import wf1_primary_sources
    wall = wall_state(ctx, pack)
    keep = os.path.join(ctx.supply, 'sources')
    added, gone = 0, []
    for wait in (WALL_BACKOFF_S if waits is None else waits):
        if not still_walled(wall):
            break
        if wait:
            ctx.log(f"{len(wall['refused'])} of {wall['sources']} footage sources still refused; trying them again in {wait:.0f} s")
            (sleep or time.sleep)(wait)
        opened, still, lost = wf1_primary_sources.reopen(wall['refused'], keep, log=ctx.log)
        if opened:
            added += reopened_footage(ctx, script, pack, opened)
        if lost and not added and not has_footage(pack):
            # Nothing has opened or been cut for this short: a source sorted as gone does not lift the wall (any yt-dlp
            # words the wall makes that the sort does not know would otherwise empty it and leave a film with no
            # footage). Those rows wait and are fetched again with the rest.
            still, lost = still + lost, []
        wall['refused'] = still
        gone += lost
        wall['sources'] = max(0, int(wall['sources']) - len(lost))          # a video that is gone is not waited for
        pack['wall'] = wall
        save_pack(ctx, pack)
    pack['platform_walled'] = still_walled(wall)
    pack['wall'] = wall
    if gone:
        pack.setdefault('gone_sources', []).extend(gone)
    save_pack(ctx, pack)
    if pack['platform_walled']:
        pack['coverage_exhausted'] = 'Source routes exhausted; use available verified pictures and the original camera'
        pack['platform_walled'] = False
        save_pack(ctx, pack)
        ctx.log(pack['coverage_exhausted'])
    else:
        ctx.log(f"the wall lifted for this short: {added} clips cut from the sources it had refused")
    path = os.path.join(ctx.supply, 'pack.json')
    stored = json.load(open(path))
    rec = dict(rec or stored.get('record') or {})
    if added:
        clips = sum(len(r.get('clips') or []) for r in pack['footage'])
        rec['footage'] = dict(rec.get('footage') or {}, clips=clips, lines=sum(1 for r in pack['footage'] if r.get('clips')))
        stored['record'] = rec
        stored['pack'] = pack
        with open(path, 'w') as f:
            json.dump(stored, f, indent=1)
    return rec


def refused(foot):
    """(YouTube sources the engine tried, how many of them YouTube refused (its wall: the bot check, a bare 403, a rate
    limit, a stalled player) or opened only through the Wayback Machine's copy). A video that is gone or private is
    not refused: it is not waited for."""
    yt = [s for s in (foot or {}).get("sources") or [] if s.get("where") in (None, "youtube")]
    return len(yt), len([s for s in yt if (s.get("error") and s.get("error_stage")=="fetch" and wall_error(s["error"]))
                         or s.get("rung") == "wayback"])


def wall_error(text):
    """yt-dlp's words for YouTube's wall, read off the YouTube rungs' part of the tool's error text: the one rule,
    wf1_primary_sources.wall_error. "Sign in to confirm your age" is an age gate, not the wall."""
    import wf1_primary_sources
    return wf1_primary_sources.wall_error(text)


def walled(foot):
    """True when YouTube would not open the footage engine's sources (it takes a busy address for a bot, on every player
    at once): half of the YouTube sources it tried or more were refused. Decided on the refused share alone (19 Nov:
    m07 had 14 of 16 refused and 3 of 5 sentences covered, ran a full new search under the wall and lost every clip).
    The refused sources are then fetched again (recover_wall)."""
    if not foot:
        return False
    n, bad = refused(foot)
    return n >= 2 and bad * 2 >= n


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


def caption_turns(ctx, window, dur):
    """[(t0, t1)] of the take that one person speaks, from the source captions' own speaker changes (a caption line
    opens with '>>' where the speaker changes); one turn when they mark none."""
    try:
        near = _near(ctx, float(window[0]), float(window[1]), pad=0.0)
    except (KeyError, OSError, ValueError, TypeError):
        return None
    marks = sorted({round(t - float(window[0]), 3) for t, w in near if str(w).lstrip().startswith(">>") and 0.0 < t - float(window[0]) < dur})
    edges = [0.0] + marks + [float(dur) + 1.0]
    return [(a, b) for a, b in zip(edges, edges[1:]) if b > a]


def off_camera(cuts, words, min_s=0.6):
    """The take's stretches whose voice no face on screen speaks (the show's camera on a listener), as the words
    they carry: [{first, last, t0, t1}] (word ids of words.json, take seconds)."""
    import wf1_speaker
    out = []
    for a, b in wf1_speaker.listener_spans(cuts, min_s=min_s):
        ids = [i for i, w in enumerate(words) if a <= (float(w["start"]) + float(w["end"])) / 2 < b]
        if ids:
            out.append(dict(first=ids[0], last=ids[-1], t0=round(a, 3), t1=round(b, 3)))
    return out


def take_cuts(src, shots, dur, sup_dir, turns=None):
    """The take's own shots, each with who speaks in it (wf1_speaker: faces tracked at 10 a second, each mouth's
    motion against the voice): [{t0, t1, faces, spans, wide}]. faces is the speaker's tracked box, the framing; spans
    say who carries each stretch (speaker, listener, quiet). The full reading is kept in speaker.json. A take the
    reader cannot open is framed the old way: three looks a shot, the biggest face."""
    spans = [(float(s["start"]), float(s["end"])) for s in shots if float(s["end"]) - float(s["start"]) > 0.2] or [(0.0, dur)]
    try:
        import wf1_speaker
        from placement_check import file_digest
        key = [file_digest(src),spans,turns,file_digest(wf1_speaker.__file__)]
        key = hashlib.sha256(json.dumps(key,sort_keys=True).encode()).hexdigest()
        cached = os.path.join(sup_dir, "speaker.json")
        if os.path.isfile(cached):
            with open(cached) as f:
                saved = json.load(f)
            if saved.get("key") == key:
                return saved["cuts"]
        cuts = wf1_speaker.read_take(PX.FF, src, spans, dur, turns=turns)
        json.dump(dict(key=key, turns=turns, cuts=cuts), open(os.path.join(sup_dir, "speaker.json"), "w"), indent=1)
        return cuts
    except Exception as e:  # noqa: BLE001 - the speaker reading is a framing aid: the take is framed by its faces
        print(f"speaker reading failed, faces only: {str(e)[:300]}", file=sys.stderr)
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


ROLES = ("hook", "turn", "payoff", "figure", "name", "contrast")


def _word_rows(pack, sc):
    """The film's spoken words in order on its own clock (seconds), each with its sentence: the Director's addresses."""
    try:
        clock = F.Clock(pack["words"], sc)
        return [dict(w=w["w"], t0=w["t0"], t1=w["t1"], seg=w["seg"]) for w in clock.words], clock.dur
    except (KeyError, OSError, ValueError, TypeError):
        rows, t = [], 0.0                    # no aligned words: the sentences' own words, evenly in time
        for ln in pack.get("lines") or []:
            for w in str(ln.get("text") or "").split():
                rows.append(dict(w=w, t0=t, t1=t + 0.3, seg=int(ln["i"])))
                t += 0.32
        return rows, t


def _num(x):
    """A measured number, whatever shape the record keeps it in (a value, or a list of values: their mean); None if none."""
    try:
        if isinstance(x, (list, tuple)):
            xs = [float(v) for v in x if isinstance(v, (int, float))]
            return round(sum(xs) / len(xs), 2) if xs else None
        return round(float(x), 2) if x is not None else None
    except (TypeError, ValueError):
        return None


def director_pictures(pack, most=36):
    """The supply's pictures for the Director's contact sheet: [{"id", "path", "label", "kind", ...}] with what signal
    measured of each (motion, faces, luma), and {id: path}."""
    out, seen = [], set()
    for ln in pack.get("footage") or []:
        for c in ln.get("clips") or []:
            p = c.get("path")
            if not p or p in seen or c.get("dull"):
                continue
            seen.add(p)
            out.append(dict(id=f"P{len(out)}", path=c.get("still") or p, src=p, kind="clip", seg=int(ln.get("i", 0)),
                            label=str(c.get("shows") or c.get("subject") or "")[:60], motion=_num(c.get("motion_p90")),
                            faces=len(c.get("faces") or []), luma=_num(c.get("luma"))))
    for name, recs in (pack.get("pictures") or {}).items():
        for r in recs:
            p = r.get("path")
            if not p or p in seen:
                continue
            seen.add(p)
            out.append(dict(id=f"P{len(out)}", path=p, src=p, kind="document" if r.get("provider") == "primary-document" else "photo",
                            label=str(r.get("shows") or r.get("title") or name)[:60], faces=len(r.get("faces") or [])))
    out = out[:most]
    return out, {p["id"]: p["src"] for p in out}


def local_turns(script):
    start = script['window'][0]
    return [dict(speaker=t['speaker'], start=round(t['start']-start,3), end=round(t['end']-start,3))
            for t in script.get('speaking_turns') or []]


def direction_of(ctx, script, pack):
    """The film's Director (DIRECTOR_CONTRACT 4, _shell/director.py): one model call that reads the heard words with the
    voice's stress, the supply's pictures on one contact sheet, the take and the style, and the device sequences of the
    films delivered lately, and chooses for every moment which devices of the engine's palette land on which word:
    beats, sections (surface, layout, energy) and stacked asks. It also gives the older fields the builder reads: the
    display words by role, ranked with alternates; the sentences that tell of the past; the payoff sentence (``turn_seg``,
    where the film cuts to his face); the story's own turn (``story_turn_seg``); and the years the story speaks from.
    A provider that fails gives the signal director's devices. Cached in the supply (director.json)."""
    DR = wf1_film._director()
    sc = dict(segments=[dict(say=ln["text"]) for ln in pack.get("lines") or []])
    rows, dur = _word_rows(pack, sc)
    try:
        stress = [(f / F.FPS, s) for f, s in F.voice_stress(pack)] if pack.get("narration") else []
    except Exception:  # noqa: BLE001 - no stress read: the Director reads the words alone
        stress = []
    pics, ids = director_pictures(pack)
    st = wf1_film.STYLES.get(((script or {}).get("style") or {}).get("id") or "", {})
    take = pack.get("take") or {}
    llm = _llm()
    from placement_check import file_digest
    import wf1_display
    editorial = dict(writing=wf1_display.WRITING, material=wf1_display.editorial(ctx, script, pack))
    intent = hashlib.sha256(json.dumps([DR.CONTRACT,editorial,rows,stress,st,script.get("title"),script.get("direction"),
                                         script.get("hook_line"),script.get("close_line"),take.get("cuts")],
                                        sort_keys=True).encode()).hexdigest()
    choice_path = os.path.join(ctx.supply, "direction_dependencies.json")
    if os.path.isfile(choice_path):
        with open(choice_path) as f:
            prior = json.load(f)
        available = set(ids.values())
        writer_record = shell.jload(os.path.join(ctx.supply, "director.json"), {})
        same_score = writer_record.get("key") == prior["direction"].get("key") and writer_record.get("contract") == DR.CONTRACT
        if same_score and prior["intent"] == intent and all(path in available and os.path.isfile(path) and file_digest(path)==sha
                                             for path,sha in prior["assets"].items()):
            return prior["direction"]
    d = DR.direct(ctx.supply, [dict(i=int(ln["i"]), text=ln["text"]) for ln in pack.get("lines") or []], rows, stress, pics,
                  take=dict(on_camera=take.get("on_camera"), shots=take.get("shots"), faces=len(take.get("faces") or []),
                            matte="made on demand for devices that need it (neg_world, words behind him)") if take else None,
                  style=dict(name=st.get("name"), accent=st.get("accent"), font=(st.get("dsp") or {}).get("font"),
                             upper=st.get("upper")) if st else None,
                  workflow="podcast short", dur_s=dur, title=(script or {}).get("title"), hook=(script or {}).get("hook_line"),
                  payoff=(script or {}).get("close_line"), editorial=editorial, ask=llm.ask, log=getattr(ctx, "log", None))
    d = wf1_display.bind(d, editorial["material"], rows)
    d["pictures"] = ids
    if d.get("by") == "model":
        refs = {pid for ask in d.get("asks", []) for pid in ask.get("pics", [])}
        refs.update(pic for beat in d.get("beats", []) for side in ("before", "after")
                    if (pic := (beat.get("sound_partner") or {}).get(side)) and pic != "take")
        if not refs.issubset(ids):
            return d
        used = {ids[pid] for pid in refs}
        assets = {path:file_digest(path) for path in used if os.path.isfile(path)}
        if set(assets) == used:
            with open(choice_path+".part","w") as f:
                json.dump(dict(intent=intent,assets=assets,direction=d),f)
            os.replace(choice_path+".part",choice_path)
    return d


def seat_brand_words(l1):
    alien = os.path.join(os.path.dirname(HERE),"alien")
    if alien not in sys.path:
        sys.path.insert(0,alien)
    import l1pass
    moved = []
    # Device words also take the brand seat; the general safe rectangle includes the mark.
    words = [it for it in l1["items"] if it["k"] == "word"]
    if not words:
        return moved
    ruler = F.Ruler(l1["voices"])
    shifts = {}
    for word in words:
        x, y, size = (F._rest(word.get(k, v)) for k, v in (("x", 0), ("y", 0), ("s", 1)))
        right = x + ruler.width(word["v"], word["t"]) * size / F.W
        top = y - ruler.cap(word["v"]) * size / F.H
        left, upper, end, bottom = F.MARK
        if x < end and right > left and top < bottom and y > upper:
            group = word.get("chunk") or word["id"]
            shifts[group] = max(shifts.get(group, 0), bottom + .008 - top)
    for word in words:
        dy = shifts.get(word.get("chunk") or word["id"])
        if dy:
            word["y"] = l1pass._shift(word["y"], dy)
            moved.append(dict(id=word["id"], dy=dy))
    return moved


def _repair(l1, beats, taste):
    """A plan that breaks a taste rule is brought inside it and measured again, never delivered as it is and never a
    stop: a word or a sound the rule names is dropped, and sounds over the supply's allowance go quietest first (a
    gesture is the sounds inside one second; the film's last gesture and the one on its turn stay). Returns how many
    items were dropped."""
    alien = os.path.join(os.path.dirname(HERE), "alien")
    if alien not in sys.path:
        sys.path.insert(0, alien)
    import l1pass
    moved = l1pass.repair_placement(l1)                 # type outside the safe area moves inside, never dropped for it
    moved.extend(seat_brand_words(l1))
    if moved:
        beats.setdefault("taste_moved", []).extend(moved)
        taste = F.taste_check(l1, beats)
        if taste["ok"]:
            return 0
    ids = set()
    for pr in taste["problems"]:
        head = pr.split(" ", 2)
        ids.add(head[1] if head[0] == "sound" and len(head) > 1 else head[0])
    n0 = len(l1["items"]) + len(l1.get("sound") or [])
    l1["items"] = [it for it in l1["items"] if it["k"] == "word" or it["id"] not in ids]
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
    wanted = [i for i in (shortage['weak_windows'] or range(len(windows))) if not windows[i].get('on_take')]
    if not wanted:
        return
    if not pack.get('referent_research'):
        import wf1_context
        found=wf1_context.research(coverage,os.path.join(ctx.supply,'referent-research'),_llm())
        pack['referent_research']=found
        pack.setdefault('research_spend',[]).extend(found['spend'])
    coverage['research_context']=pack['referent_research']
    if pack.get('research_revision',0)<7:
        import wf1_coverage
        # What was found, never why a check said no: rejection reasons handed to the director came back as exclusions in
        # the brief (19 Nov m04, m05), and every brief rewrite re-judged pictures already approved. The brief stays a
        # search brief; the picture check chooses among what the search finds.
        evidence=dict(available=[dict(i=r['i'],clips=[dict(subject=c.get('subject'),shows=c.get('shows'),source=c.get('source')) for c in r['clips']]) for r in pack['footage']])
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
    missing_documents=(not motion_ready
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
    count = int(previous)+1
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
    fscript = dict(title=footage_title(script['title'], coverage.get('context') or '', coverage)+' — supplementary documentary coverage. Prefer original institutional or company films with visible action; avoid influencer promotions and unrelated banners.',
                   segments=[dict(i=i,say=w['say'],pictures=[w['name']],visual=w['must_show']) for i,w in enumerate(windows)],
                   named=[dict(name=n,must_show=show,video_sources=primary.get(n,[]),video_searches=list(dict.fromkeys(q for w in windows if w['name']==n for q in w.get('video_searches',[]))),**{'is':'thing'}) for n,show in subjects.items()])
    if rescout:name+='-'+hashlib.sha256(json.dumps(fscript,sort_keys=True).encode()).hexdigest()[:12]
    fw,fh = wf1_film.FOOTAGE_FRAME
    ctx.log(f"research missing documentary windows {wanted}; pass {count}")
    foot = chain.footage(ctx,dict(script=fscript),[w['t1']-w['t0'] for w in windows],W=fw,H=fh,
                         preserve_source=True,native_pixels=True,look_itself=True,shot_s=1.15,line_max=20,per=4,lines=wanted,avoid=prior,
                         strict_lines=True,motion_first=True,scout_motion=True,alloc='round',reassign_lost=True,
                         verify=False,whole_first=True,keep_sources=True,hear_s=0,source_only=rescout or count==4,out_name=name)
    if not foot:
        exhausted(ctx,pack,'supplementary documentary research found nothing new')
        return
    merge_footage(ctx,pack,foot,name)
    pack['coverage_repairs'] = int(previous) if rescout else count
    pack['coverage_last_search_report']=shortage
    if rescout:pack['coverage_context_rescout_pending']=False
    pack['coverage_catalog_revision']=1
    pack['research_contract'] = RESEARCH_CONTRACT
    pack['research_sources'] = source_signature(ctx.supply)
    path = os.path.join(ctx.supply,'pack.json')
    stored = json.load(open(path));stored['pack']=pack
    with open(path,'w') as f:json.dump(stored,f,indent=1)
    meet_wall(ctx,script,pack,foot)


def meet_wall(ctx, script, pack, foot):
    """A research pass that met YouTube's wall recovers like the first one: its refused sources are fetched again with
    backoff (recover_wall), and the short waits (wf1_film.WALL) rather than being built short of them."""
    if not walled(foot):
        return
    ctx.log('YouTube refused most sources of this research pass (its wall): fetching them again before the film is built')
    pack['platform_walled'] = True
    pack['wall'] = dict(sources=refused(foot)[0], refused=refused_rows(foot), since=time.time())
    save_pack(ctx, pack)
    recover_wall(ctx, script, pack)


def save_pack(ctx, pack):
    path=os.path.join(ctx.supply,'pack.json');stored=json.load(open(path));stored['pack']=pack
    with open(path,'w') as f:json.dump(stored,f,indent=1)


def exhausted(ctx, pack, why):
    """Research has nothing more to find: the film is built from every approved picture (wf1_coverage final)."""
    pack['coverage_exhausted']=why
    save_pack(ctx,pack)
    ctx.log(why+'; the film is built from every approved picture and the speaker carries the rest')


def research_spent(pack):
    return bool(pack.get('coverage_exhausted')) or int(pack.get('coverage_recoveries') or 0)>=RECOVERY_PASSES


def ladder_open(pack):
    """A window the last build left empty still has a lower depiction rung to acquire from."""
    windows = (pack.get('coverage') or {}).get('windows') or []
    return any(isinstance(i, int) and 0 <= i < len(windows)
               and int(windows[i].get('rung') or 0) < len(windows[i].get('depictions') or [])
               for i in pack.get('coverage_missing') or [])


def coverage_final(pack):
    """The build is final (it ships with what it has) once research is spent AND no empty window has a rung left."""
    return research_spent(pack) and not ladder_open(pack)


def descend(ctx, script, pack, missing):
    """The windows research left empty go one rung down the depiction ladder the plan gave them (the event itself, then
    the named person's or company's own archive, then the place or product, then related moving context), and footage
    and photographs are acquired on that rung at once, so the slot is filled before the final build rather than shipped
    empty. Returns True when a rung was taken."""
    windows = pack['coverage']['windows']
    lower = [i for i in missing if isinstance(i, int) and 0 <= i < len(windows)
             and int(windows[i].get('rung') or 0) < len(windows[i].get('depictions') or [])]
    if not lower:
        return False
    for i in lower:
        w = windows[i]
        k = int(w.get('rung') or 0)
        step = w['depictions'][k]
        w.setdefault('ladder', []).append(dict(name=w['name'], must_show=w['must_show']))
        w.update(name=step['name'], must_show=step['must_show'], video_searches=list(step.get('video_searches') or []),
                 subjects=[step['name']], rung=k + 1)
    save_pack(ctx, pack)
    ctx.log('windows left empty go down their depiction ladder: '
            + '; '.join(f"{i} -> {windows[i]['name']} (rung {windows[i]['rung']})" for i in lower))
    name = (f"footage-v{pack['coverage']['contract']}-{FOOTAGE_PIXELS}-rung-"
            + hashlib.sha256(json.dumps([[i, windows[i]['name'], windows[i]['must_show']] for i in lower]).encode()).hexdigest()[:12])
    prior = [os.path.join(ctx.supply, n, 'footage.json') for n in os.listdir(ctx.supply)
             if n.startswith('footage') and os.path.isfile(os.path.join(ctx.supply, n, 'footage.json'))]
    subjects = {}
    for i in lower:
        subjects.setdefault(windows[i]['name'], []).append(windows[i]['must_show'])
    fscript = dict(title=footage_title(script.get('title'), str(pack['coverage'].get('context') or '')[:400], pack['coverage']),
                   segments=[dict(i=i, say=w['say'], pictures=[w['name']], visual=w['must_show']) for i, w in enumerate(windows)],
                   named=[dict(name=n, must_show=' '.join(dict.fromkeys(shows)), video_sources=[],
                               video_searches=list(dict.fromkeys(q for i in lower if windows[i]['name'] == n for q in windows[i].get('video_searches', []))),
                               **{'is': 'thing'}) for n, shows in subjects.items()])
    fw, fh = wf1_film.STYLES[script['style']['id']]['footage']
    foot = chain.footage(ctx, dict(script=fscript), [w['t1'] - w['t0'] for w in windows], W=fw, H=fh,
                         preserve_source=True, native_pixels=True, look_itself=True, shot_s=1.15, line_max=20, per=4, lines=lower,
                         avoid=prior, strict_lines=True, motion_first=True, scout_motion=True, alloc='round', reassign_lost=True,
                         verify=False, whole_first=True, keep_sources=True, hear_s=0, source_only=False, out_name=name)
    if foot and name not in (pack.get('merged_footage') or []):
        merge_footage(ctx, pack, foot, name)
        pack.setdefault('merged_footage', []).append(name)
    want = [dict(name=n, searches=[n], must_show=' '.join(dict.fromkeys(shows)), **{'is': 'thing'})
            for n, shows in subjects.items() if not pack['pictures'].get(n)]
    if want:
        sys.modules.pop("llm", None)                           # the worker's own llm answers its picture check
        try:
            got, reports, _ = PX.pictures(want, os.path.join(ctx.supply, "pics"), per=3)
        except Exception as e:  # noqa: BLE001 - photographs are the fallback: a rung is not lost to them
            ctx.log(f"photographs for the lower rung skipped: {str(e)[:300]}")
            got, reports = {}, []
        for n, recs in got.items():
            for r in recs:
                try:
                    r["faces"] = PX.faces(r["path"])
                except Exception:  # noqa: BLE001
                    r["faces"] = []
            if recs:
                pack['pictures'][n] = recs
                pack['credits'] += [dict(kind="photograph", name=r["name"], title=r.get("title"), licence=r.get("licence"),
                                         credit=r.get("credit"), page=r.get("page")) for r in recs]
    save_pack(ctx, pack)
    if foot:
        meet_wall(ctx, script, pack, foot)
    return True


def film_direction(d):
    """Carry the Director's authored fields into the picture and sound composers."""
    knobs = {k: d.get(k) for k in ("display", "display_facts", "claims", "past_segs", "turn_seg", "story_turn_seg", "arc")}
    if d.get("beats") or d.get("sections") or d.get("asks"):
        knobs["director"] = {k: d.get(k) for k in ("beats", "sections", "asks", "arc", "pictures", "by", "seed")}
    return knobs


def score(ctx, script):
    import wf1_coverage, wf1_alignment
    pack = wf3.load_pack(ctx)
    if ((pack.get('coverage') or {}).get('contract') != wf1_coverage.CONTRACT
            or pack['coverage'].get('story') != wf1_coverage.story_context(script)
            or pack.get('alignment_contract')!=wf1_alignment.CONTRACT
            or pack.get('platform_walled')):
        supply(ctx,script)
        pack = wf3.load_pack(ctx)
    pack["work"] = ctx.supply
    refresh=False
    coverage = pack['coverage']
    # A resumed supply already has its word plan and candidates. Keep their window
    # indices and read only the original take when the pack predates source framing.
    previous = coverage.get('source_reading') or {}
    needs_source = 'authored_shots' not in previous or (previous.get('inserts') and 'speaker_inserts' not in previous)
    if needs_source and os.path.isfile(os.path.join(ctx.supply,'index.json')):
        import wf1_source_frame
        idx = json.load(open(os.path.join(ctx.supply,'index.json')))
        take = pack['take']
        frames = wf1_source_frame.samples(take['path'],idx['shots'],take['dur'],ctx.supply,pack['ffmpeg'])
        coverage['speaking_turns'] = local_turns(script)
        reading = wf1_source_frame.read(coverage,frames,_llm(),ctx.supply)
        coverage['source_reading'] = reading
        for i in reading['replaces']:
            coverage['windows'][i]['on_take'] = True
        refresh = True
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
    knobs = film_direction(d)
    knobs.update({k: v for k, v in ((ctx.args or {}).get("direction") or {}).items()})
    # The composed review's findings, each local to its window: a contradiction gives that window's slot to its next
    # candidate, a crop through a face is framed again (never a global, permanent drop: round 1, 19 Nov).
    found = current_rejections(ctx.supply)
    knobs["reject"] = [r for r in found if r.get("fault") != "reframe"]
    knobs["reframe"] = [r for r in found if r.get("fault") == "reframe"]
    knobs.setdefault("bed", chain.wants_music(ctx) or bool((ctx.args or {}).get("song")))      # the film gets music: the sounds are designed for a film with a bed
    sys.path[:0] = [p for p in wf3.env_path().split(os.pathsep) if p not in sys.path]
    os.environ["PATH"] = os.path.dirname(pack["ffmpeg"]) + os.pathsep + os.environ.get("PATH", "")
    sc = dict(title=script.get("title"), hook_line=script.get("hook_line"), close_line=script.get("close_line"),
              source_credit=(ctx.args or {}).get("channel"), source_url=(ctx.args or {}).get("source_url"),
              source_start=(script.get("window") or [0])[0], speaking_turns=script.get("speaking_turns", []),
              segments=[dict(say=ln["text"], pictures=[n for n in ln.get("subjects") or []]) for ln in pack["lines"]])
    for attempt in range(RECOVERY_PASSES + 3 + LADDER_RUNGS):
        knobs["coverage_final"] = coverage_final(pack) or bool(pack.get("coverage_recoveries"))
        try:
            l1, beats = wf1_film.build(sc, pack, script["style"]["id"], knobs)
            break
        except wf1_coverage.CoverageError as error:
            pack['coverage_missing']=list(error.report.get('missing_windows') or [])
            if research_spent(pack):
                # research is spent: a window still empty takes its next depiction rung (acquired at once); with no
                # rung left the next build is final and ships what was approved
                descend(ctx,script,pack,pack['coverage_missing'])
                save_pack(ctx,pack)
                continue
            # bounded recovery, counted across resumes and rebuilds: after RECOVERY_PASSES the ladder, then the final
            pack['coverage_recoveries']=int(pack.get('coverage_recoveries') or 0)+1
            save_pack(ctx,pack)
            try:
                refill(ctx,script,pack,error.report)
            except RuntimeError as stop:
                if wf1_film.WALL in str(stop):                 # the wall is not this pass's fault: the pass is not spent
                    pack['coverage_recoveries']=int(pack.get('coverage_recoveries') or 0)-1
                    save_pack(ctx,pack)
                raise
            save_pack(ctx,pack)
    else:
        knobs["coverage_final"] = True
        l1, beats = wf1_film.build(sc, pack, script["style"]["id"], knobs)
    early = seat_brand_words(l1)
    if early:
        beats.setdefault("taste_moved",[]).extend(early)
    shown = beats.get("pictures") or []
    l1["pool"] = wf1_coverage.device_pool(pack, beats.get("placement_check"), work=ctx.supply,
                                          ffmpeg=pack.get("ffmpeg") or "ffmpeg",
                                          matched={p.get("origin") or p["path"] for p in shown})
    l1["documentary_cuts"] = [{k: p.get(k) for k in
                               ("f", "pic", "path", "source_slice", "time_range", "source_evidence", "name_super")}
                              for p in shown]
    l1["documentary_lead_f"] = wf1_film.LEAD_F
    if l1.get("devices") or l1.get("alien"):
        # the sound design hears the Director's devices: laid now on the scene the render draws (one base picture on
        # every frame first), their landings planned again with the take's own turn and words, before the taste check
        # counts what lands with every sound
        healed = wf1_film.heal_base(l1)
        if healed:
            beats["base_healed"] = healed
        beats["devices_heard"] = chain.hear_devices(ctx, l1, beats, lambda x: F.replan_sound(x, pack))
    taste = F.taste_check(l1, beats)
    dropped = 0
    if not taste["ok"]:
        dropped = _repair(l1, beats, taste)
        taste = dict(F.taste_check(l1, beats), repaired=dropped)
    healed = wf1_film.heal_base(l1)                          # one base picture on every frame, the renderer's rule
    if healed:
        beats["base_healed"] = (beats.get("base_healed") or []) + healed if isinstance(healed, list) else healed
    out = os.path.join(ctx.supply, "l1.json")
    json.dump(l1, open(out, "w"), ensure_ascii=False)
    l1_audio = os.path.join(ctx.supply, "l1_audio.json")       # the same scene with the take's sound as its spine: what the music is cut to
    json.dump(F.audio_scene(l1, pack), open(l1_audio, "w"), ensure_ascii=False)
    json.dump(beats, open(os.path.join(ctx.supply, "beats.json"), "w"), indent=1)
    if not taste["ok"]:                                       # repaired as far as it goes: recorded, never a lost short
        beats["taste_unresolved"] = taste["problems"][:20]
        json.dump(beats, open(os.path.join(ctx.supply, "beats.json"), "w"), indent=1)
        ctx.log("taste rules still unmet after repair (recorded): " + "; ".join(taste["problems"][:4]))
    turn_s = round(beats["turn_f"] / F.FPS, 3) if beats.get("turn_f") is not None else None
    # the last word by the words' own times (programme seconds): the brand finish falls the take's breath after it
    last_word_s = round(beats["voice_f"][1] / F.FPS, 3) if beats.get("voice_f") else None
    # the story's own turn, before the payoff: music_film --turn (the drop when the payoff has no room after it).
    # turn_drop is this workflow's opt-in, sent with every payoff: a short's payoff is often its last sentence, and
    # the drop then lands on the turn (this one, else the one music_film reads from the lines) or the last hit moves
    # into the film's tail, so no short keeps a flat bed (round1 thin-sound)
    story_turn_s = round(beats["story_turn_f"] / F.FPS, 3) if beats.get("story_turn_f") is not None else None
    return dict(l1=out, l1_audio=l1_audio, turn_s=turn_s, last_word_s=last_word_s, story_turn_s=story_turn_s, turn_drop=turn_s is not None, beats={k: v for k, v in beats.items() if k not in ("look",)}, taste=taste,
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
    display = [it for it in l1["items"] if it.get("k") == "word" and it.get("role") in ("big", "stat")]      # the film's own type: display words and name supers
    captions = [it for it in l1["items"] if it.get("k") == "word" and it.get("role") == "cap"]
    rows = []
    for p in sc["beats"].get("pictures") or []:
        a, b = p.get("visible_f") or p["f"]
        words = [dict(f=[max(a,it["f"][0]),min(b,it["f"][1])], text=it["t"]) for it in display if it["f"][0] < b and it["f"][1] > a]
        caption_words = [dict(f=[max(a,it["f"][0]),min(b,it["f"][1])], text=it["t"]) for it in captions if it["f"][0] < b and it["f"][1] > a]
        frames = {a, (a+b)//2, max(a,b-1)}
        for word in words:
            frames.update(f for edge in word["f"] for f in (edge-1,edge,edge+1) if a <= f < b)
        rows.append([a, b, "clip", p["name"], dict(path=rd["film"], src=[a/F.FPS,b/F.FPS],
                    check_times=[f/F.FPS for f in sorted(frames)], composed_fps=F.FPS, display_words=words, caption_words=caption_words,
                    clause=p.get("clause"), clause_t=p.get("clause_t"),
                    source=p.get('source_evidence') or dict(url=p.get("source")))])
    # One look at the composed frames: what composing changed (a crop through a face, a subject outside the frame, a
    # display word or leftover text contradicting the picture; placement_check.COMPOSED). The picture's fit to its
    # words was chosen on its source frames in the score stage and is not judged again here. Unchanged frames reuse
    # their verdicts, so a rebuild looks only at the pictures whose composed frames changed.
    _, report = placement_check(rows, script, clock, os.path.join(ctx.supply, "render-review"), pack["ffmpeg"],
                                review="composed")
    for row in report["placements"]:
        row["transition_audit"] = dict(ok=row.get("ok") is True, reason=row.get("reason", ""), review="composed")
    real=sum(p['f'][1]-p['f'][0] for p in report['placements'])/F.FPS
    moving=sum(p['f'][1]-p['f'][0] for p in report['placements'] if p.get('motion_ok') is True)/F.FPS
    report.update(real_seconds=real,moving_seconds=moving,moving_share=moving/max(real,.001))
    return report


def render_revision(sc):
    files=list(dict.fromkeys([sc.get('l1'),sc.get('l1_audio'),sc.get('composed_l1')]))
    if not files[0]:return None
    digest=hashlib.sha256()
    for path in files:
        if path and os.path.isfile(path):digest.update(open(path,'rb').read())
    from placement_check import file_digest
    try:
        with open(files[0]) as f:
            scene = json.load(f)
    except (ValueError,OSError):
        scene = {}
    for asset in (scene.get("assets") or {}).values():
        path = os.path.join(os.path.dirname(files[0]), asset.get("path",""))
        if os.path.isfile(path):
            digest.update(file_digest(path).encode())
    # Audio fixes must invalidate both an engine soundtrack and a cached final mix.
    worker=os.environ.get('VALMERA_WORKER')
    if worker:
        for rel in ('score/music.py','score/audibility.py','score/sound.py','score/sfx_quality.json','scene/pace.py','scene/compile.py','scene/runtime.py','scene/text_layout.py','scene/run.py',
                    'scene/palette/layout.py','scene/palette/rhythm.py','scene/palette/__init__.py','scene/palette/behind.py',
                    'scene/palette/matte.py','scene/palette/type.py','scene/palette/sense.py','scene/palette/punct.py'):
            path=os.path.join(worker,rel)
            if os.path.isfile(path):digest.update(open(path,'rb').read())
    digest.update(file_digest(brand.__file__).encode())
    return digest.hexdigest()


def invalidate_render(ctx):
    # the palette's laid scene (chain.alien_bank) is drawn from the l1 the score just rewrote: it goes with the render
    for name in ('film_engine.mp4','film_engine.mp4.log.json','sound.m4a',
                 'film_engine_music.mp4','music.json',
                 'l1.alien.json','l1.alien.json.part','l1.alien.json.key','alien_bank.json'):
        path=os.path.join(ctx.supply,name)
        if os.path.exists(path):os.remove(path)
    for name in ('final.mp4','final_music.mp4','final_song.mp4'):
        path=os.path.join(ctx.run_dir,name)
        if os.path.exists(path):os.remove(path)


def prepare_picture_mix(ctx, sc):
    """Use the very same composed scene for the programme mix and the picture renderer."""
    if not sc.get("l1"):
        return
    path = sc["l1"]
    with open(path) as handle:
        base = json.load(handle)
    if "story_cues" not in base:
        return
    F._engines()
    from score import sound as sound_engine
    scene_path = chain.lay_devices(ctx, path)
    with open(scene_path) as handle:
        scene = json.load(handle)
    if scene_path == path and (base.get("devices") or base.get("alien")):
        # This attempt drew the plain picture. Receipts from the earlier layout
        # in the score cannot describe it.
        scene.pop("composed_events", None)
        scene.pop("device_log", None)
        scene.pop("device_events", None)
    scene["picture_bound_sound"] = True
    bindings = sound_engine.story_partners(scene)
    report = base.get("sound_design") or {}
    sounds = base.get("sound") or []
    if report.get("picture_bindings") != bindings:
        fresh, revised = F.replan_sound(scene, wf3.load_pack(ctx))
        if revised.get("fault") and report.get("replan"):
            revised["replan"] = report["replan"]
        report = revised
        sounds = [s for s in sounds if s.get("device")] + fresh
    # A resumed score can still have an unbound hit in its audio sidecar.
    sc["beats"]["sound_design"] = report
    for target in dict.fromkeys([path, scene_path, sc.get("l1_audio")]):
        if not target:
            continue
        with open(target) as handle:
            doc = json.load(handle)
        if doc.get("sound") != sounds or doc.get("sound_design") != report:
            doc.update(sound=sounds, sound_design=report)
            with open(target+".part","w") as handle:
                json.dump(doc,handle,ensure_ascii=False)
            os.replace(target+".part",target)
    sc["composed_l1"] = scene_path


def render(ctx, script, sc):
    """The product repairs rejected placements before branding and music, never a delivered film."""
    history_path = os.path.join(ctx.supply, "render_reviews.json")
    history = json.load(open(history_path)) if os.path.exists(history_path) else []
    # A pause can occur after persisting a rejection but before saving its rebuilt
    # score. Resume that repair before rendering the stale, already-rejected plan.
    found=current_rejections(ctx.supply)
    rejected_plan=any(unrepaired(p,found) for p in sc.get('beats',{}).get('pictures',[]))
    stale_captions=('caption_lines' in sc.get('beats',{})
                    and sc['beats'].get('caption_contract')!=9)
    if rejected_plan or stale_captions:
        if history and rejected_plan:
            pictures = sc.get("beats", {}).get("pictures", [])
            _repair_window_boundaries(ctx, [r for r in history[-1]["placements"] if not r["ok"]
                and 0 <= r["id"] < len(pictures) and pictures[r["id"]].get("mode") != "source-insert"
                and (r.get("fault") != "reframe" or not can_widen_source(pictures[r["id"]]))])
        previous_spend=sc.get('spend',[])
        rebuilt=score(ctx,script)
        rebuilt['spend']=previous_spend+[u for u in rebuilt.get('spend',[]) if u not in previous_spend]
        sc.clear();sc.update(rebuilt)
        json.dump(sc,open(os.path.join(ctx.run_dir,'score.json'),'w'),indent=1)
        invalidate_render(ctx)
    while True:
        prepare_picture_mix(ctx, sc)
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
        changed = False
        if os.path.isfile(os.path.join(ctx.supply,"pack.json")):
            pack = wf3.load_pack(ctx)
            passed = {}
            for row in report.get("placements", []):
                if not row.get("ok") or row.get("unchecked"):
                    continue
                pic = sc["beats"]["pictures"][row["id"]]
                path = pic.get("origin") or pic["path"]
                if os.path.isfile(path):
                    from placement_check import file_digest
                    passed.setdefault(str(pic.get("window")), []).append(
                        dict(path=path, sha256=file_digest(path), f=pic["f"], src=pic.get("source_slice"),
                             clause=pic.get("clause"), clause_t=pic.get("clause_t"), source=pic.get("source"),
                             motion_ok=row.get("motion_ok")))
            coverage=pack.setdefault("coverage", {})
            for window,older in coverage.get("passed_placements",{}).items():
                rows=passed.setdefault(window,[])
                identities={(r["sha256"],tuple(r["f"])) for r in rows}
                for row in older:
                    identity=(row.get("sha256"),tuple(row.get("f",[])))
                    if row.get("motion_ok") and identity not in identities:
                        rows.append(row)
                        identities.add(identity)
            coverage["passed_placements"] = passed
            coverage.pop("motion_windows",None)
            save_pack(ctx,pack)
        if report["rejected"]:
            changed = _rebuild_rejected(ctx, script, sc, report, defer=True)
        if report["moving_share"] < .6:
            changed = _rebuild_motion(ctx, script, sc, report, defer=True) or changed
        changed = _rebuild_holds(ctx, script, sc, rd, defer=True) or changed
        if changed:
            wanted = sc["beats"].pop("research_windows", [])
            if wanted:
                pack = wf3.load_pack(ctx)
                shortage = dict(sc["beats"].get("coverage") or {})
                shortage.update(weak_windows=sorted(set(wanted)), real_seconds=report.get("real_seconds",0),
                                moving_seconds=report.get("moving_seconds",0),
                                required_moving_seconds=.6*report.get("real_seconds",0))
                shortage.setdefault("missing_windows",[])
                refill(ctx,script,pack,shortage)
                save_pack(ctx,pack)
            _rescore(ctx,script,sc)
            continue
        else:
            if report["rejected"]:
                report["unrepaired"] = [r["id"] for r in report["placements"] if not r.get("ok")]
                json.dump(history, open(history_path, "w"), indent=1)
            json.dump(sc, open(os.path.join(ctx.run_dir, "score.json"), "w"), indent=1)
            rd["picture_review"] = history
            rd["spend"] = [dict(stage="render", what="composed picture-to-words check", **u) for h in history for u in h.get("spend", [])]
            return rd


def _rebuild_motion(ctx, script, sc, report, defer=False):
    """Keep faithful stills, but never recertify their card motion as footage. One research pass for more moving
    footage per short; after it (or when nothing new is still), the film stands on its approved pictures and the
    record keeps its measured moving share. Returns True when the film was rebuilt."""
    pack=wf3.load_pack(ctx)
    pics=sc['beats'].get('pictures') or []
    stills={pics[row['id']]['path'] for row in report['placements'] if row.get('motion_ok') is not True
            and pics[row['id']].get('mode') != 'source-insert'}
    previous=set(pack.get('composed_stills') or [])
    weak = sorted({pics[row['id']].get('window') for row in report['placements']
                   if row.get('ok') and row.get('motion_ok') is False and isinstance(pics[row['id']].get('window'),int)})
    if pack.get("motion_recovered"):
        # A later boundary change can expose a static slice after acquisition already succeeded.
        # Try the moving originals already in this window once on its revised words.
        coverage=pack.get("coverage") or {}
        revised={u["i"] for review in coverage.get("boundary_reviews",[]) for u in review.get("updates",[])}
        tried=pack.setdefault("local_motion_repairs",[])
        local=[]
        for i in sorted(revised.intersection(weak)):
            w=coverage["windows"][i]
            key=[i,w["first"],w["last"]]
            if key in tried:
                continue
            pool=next((r["clips"] for r in pack.get("footage",[]) if r["i"]==i),[])
            if any(c.get("path") not in stills|previous and c.get("motion_p90",0)>0 for c in pool):
                local.append(i)
                tried.append(key)
        if local:
            coverage["motion_windows"]=local
            pack["composed_stills"]=sorted(previous|stills)
            save_pack(ctx,pack)
            ctx.log(f"recompose changed windows {local} from retained moving source; no new acquisition")
            if not defer:_rescore(ctx,script,sc)
            return True
    if not stills-previous or pack.get('motion_recovered'):
        ctx.log(f"moving share {report.get('moving_share',0):.2f} after recovery; the film keeps its approved stills")
        return False
    pack['motion_recovered']=True
    if weak and defer:
        sc['beats'].setdefault('research_windows',[]).extend(weak)
        pack.setdefault('coverage',{})['motion_windows'] = weak
    pack['composed_stills']=sorted(previous|stills)
    path=os.path.join(ctx.supply,'pack.json');stored=json.load(open(path));stored['pack']=pack
    json.dump(stored,open(path,'w'),indent=1)
    ctx.log('final frames require more subject motion; retaining truthful stills and researching footage')
    if not defer:
        _rescore(ctx,script,sc)
    return True


def _rescore(ctx, script, sc):
    measured = sc.get("beats", {}).get("measured_before_research")
    old_spend = sc.get("spend", [])
    rebuilt = score(ctx, script)
    rebuilt["spend"] = old_spend + [u for u in rebuilt.get("spend", []) if u not in old_spend]
    if measured:
        rebuilt.setdefault("beats", {})["measured_before_research"] = measured
    sc.clear()
    sc.update(rebuilt)
    json.dump(sc, open(os.path.join(ctx.run_dir, "score.json"), "w"), indent=1)
    invalidate_render(ctx)


def hold_windows(measured, coverage, pictures, fps=None, lead=None):
    """The windows the measured film leaves short: every window a hold longer than RESEARCH_HOLD_S overlaps, and when
    the film cuts less than RESEARCH_CUTS_10S a 10 s, every window holding fewer pictures than its clause wants (one a
    1.25 s, wf1_coverage.proposals)."""
    import math
    fps = fps or F.FPS
    lead = F.LEAD_F if lead is None else lead
    out = set()
    for i, w in enumerate(coverage.get("windows") or []):
        if w.get('on_take'):
            continue
        a, b = (round(w["t0"]*fps)+lead)/fps, (round(w["t1"]*fps)+lead)/fps
        placed = [p for p in pictures if p.get("window") == i]
        meaningful = min(1.4,b-a)
        if any(min(h1,b)-max(h0,a) >= meaningful for h0,h1 in measured.get("holds") or []):
            out.add(i)
        elif float(measured.get("cuts_per_10s") or 0) < RESEARCH_CUTS_10S:
            timed = sorted((max(a,p["f"][0]/fps), min(b,p["f"][1]/fps)) for p in placed if p.get("f"))
            if timed:
                # An adjacent speaker hold does not reopen a window already covered by verified pictures.
                end = a
                gap = 0
                for start,stop in timed:
                    gap = max(gap,start-end)
                    end = max(end,stop)
                if max(gap,b-end) >= meaningful:
                    out.add(i)
            elif len(placed) < max(1, math.ceil((w["t1"]-w["t0"])*.8)):
                out.add(i)
    return sorted(out)


def _rebuild_holds(ctx, script, sc, rd, defer=False):
    """Feed the rendered programme's measured cuts back to supply: the windows under a long hold, or under too few
    cuts, are researched once a short through refill and the film rebuilt. Returns True when it was rebuilt; after the
    one pass (or with nothing to research) the film stands, its measure on record."""
    film = rd.get("film")
    if not film or not os.path.isfile(film):
        return False
    measured = rd.get("measured_cuts") or measure_cuts(film, PX.FF)
    rd["measured_cuts"] = measured
    if not measured:
        return False
    pack = wf3.load_pack(ctx)
    if pack.get("holds_researched"):
        return False
    sc.setdefault("beats", {})["measured_before_research"] = measured
    weak = hold_windows(measured, pack.get("coverage") or {}, sc["beats"].get("pictures") or [])
    if not weak:
        return False
    pack["holds_researched"] = True
    save_pack(ctx, pack)
    ctx.log(f"the rendered film holds {measured['longest_hold_s']} s and cuts {measured['cuts_per_10s']} a 10 s: "
            f"research windows {weak} once more")
    report = dict(real_seconds=0, moving_seconds=0, required_moving_seconds=0, new_pictures=0, missing_windows=[])
    report.update(sc["beats"].get("coverage") or {})
    report["weak_windows"] = weak
    if defer:
        sc["beats"].setdefault("research_windows",[]).extend(weak)
    else:
        refill(ctx, script, pack, report)
        save_pack(ctx, pack)
        _rescore(ctx,script,sc)
    return True


def current_rejections(supply):
    """The composed review's findings in force: [{window, path, fault, reason}], each local to the window it was found
    in (window None: a picture placed outside every window). fault 'reframe': the picture is framed again; otherwise
    that window's slot goes to its next candidate. A changed frame/layout contract requires fresh source AND composed
    checks; it never grants approval to a formerly rejected asset."""
    from placement_check import CONTRACT
    path = os.path.join(supply, "render_rejected.json")
    marker = os.path.join(supply, "render_rejected_contract.json")
    if not os.path.exists(marker) or json.load(open(marker)) != CONTRACT:
        return []
    rows = json.load(open(path)) if os.path.exists(path) else []
    rows=[r if isinstance(r, dict) else dict(window=None,path=r,fault="contradiction") for r in rows]
    path=os.path.join(supply,"pack.json")
    if not os.path.isfile(path):
        return rows
    with open(path) as f:
        windows=(json.load(f).get("pack",{}).get("coverage") or {}).get("windows",[])
    def applies(row):
        i,span=row.get("window"),row.get("f")
        if not isinstance(i,int) or not 0<=i<len(windows) or not span or not row.get("clause_t"):
            return True
        w=windows[i]
        if row["clause_t"]==[w["t0"],w["t1"]]:
            return True
        return span[1]>round(w["t0"]*F.FPS)+wf1_film.LEAD_F and span[0]<round(w["t1"]*F.FPS)+wf1_film.LEAD_F
    return [r for r in rows if applies(r)]


def unrepaired(picture, found):
    """True when a planned picture still carries a composed finding its plan has not repaired: a contradiction in its
    own window, a reframe it was not yet framed for, or a window given to the speaker. Findings are keyed on the
    candidate (origin), never on a copy derived from it."""
    own = {picture.get("origin"), picture.get("path")} - {None}
    for r in found:
        if r.get("fault") == "speaker":
            if r.get("window") is not None and r.get("window") == picture.get("window"):
                return True
            continue
        if (r.get("origin") or r.get("path")) not in own or r.get("window") not in (None, picture.get("source_slot") or picture.get("window")):
            continue
        if r.get("fault") != "reframe" or not picture.get("reframed"):
            return True
    return False


def _repair_window_boundaries(ctx, rejected):
    """A picture that contradicted its words because its window ran into the next subject's words is a timing fault:
    the director moves that boundary (wf1_coverage.repair_boundaries). The findings never become the window's brief:
    repair_requirements is not asked (19 Nov m04, m05: the director copied the checker's objections into must_show as
    exclusions, and the narrower brief re-judged pictures already approved). A boundary answer that cannot be applied
    leaves the boundaries as they are; the picture's own repair goes on."""
    import wf1_coverage
    if not rejected:
        return
    pack=wf3.load_pack(ctx)
    try:
        wf1_coverage.repair_boundaries(pack['coverage'],rejected,_llm())
    except (RuntimeError, ValueError, KeyError, TypeError) as error:
        ctx.log(f'boundary repair not applied: {str(error)[:200]}')
        return
    path=os.path.join(ctx.supply,'pack.json');stored=json.load(open(path));stored['pack']=pack
    json.dump(stored,open(path,'w'),indent=1)
    json.dump(pack['coverage'],open(os.path.join(ctx.supply,'coverage.json'),'w'),indent=1)


def can_widen_source(picture):
    return picture.get("source_crop") != [0,0,1,1] and not picture.get("reframed")


def _rebuild_rejected(ctx, script, sc, report, defer=False):
    """Repair what the composed review found, where it found it, and rebuild. Returns True when the film was rebuilt.

    Each finding climbs one step of a ladder, keyed on the candidate (origin: a copy the check cut clear of text is the
    same candidate): a crop through a face or a subject outside the frame is framed again first (fault 'reframe'); a
    contradiction, or a second finding on a picture already framed again, gives that one window's slot to its next
    candidate ('contradiction'); a finding that still comes back on that window gives the window to the speaker
    ('speaker'). The picture stays eligible in every other window (round 1, 19 Nov: one composed 'no' removed a picture
    from every window for good, and six of twelve films ended with no cutaway). A finding with no step left is recorded
    on the film and the film is delivered (never lost to its repair: adversarial review, 20 Nov)."""
    pics = sc["beats"].get("pictures") or []
    previous = current_rejections(ctx.supply)
    before = {(r.get("window"), r.get("origin") or r.get("path"), r.get("fault")) for r in previous}
    added = []
    boundary_findings = []

    def has(key):
        return key in before or any((a["window"], a["path"], a["fault"]) == key for a in added)
    for r in report["placements"]:
        if r.get("ok"):
            continue
        p = pics[r["id"]]
        window, origin = p.get("source_slot") or p.get("window"), p.get("origin") or p["path"]
        # If the whole checked source is already on screen, widening its crop is
        # a no-op. Review the source and clause together before spending another render.
        if r.get("fault", "reframe") == "reframe" and can_widen_source(p) and not has((window, origin, "reframe")):
            step = (window, origin, "reframe")
        elif not has((window, origin, "contradiction")):
            step = (window, origin, "contradiction")
        elif window is not None and not has((window, None, "speaker")):
            step = (window, None, "speaker")
        else:
            continue
        if step[2] != "reframe" and p.get("mode") != "source-insert":
            boundary_findings.append(r)
        timing = {k:p[k] for k in ("f","clause_t") if p.get(k)}
        added.append(dict(window=step[0], path=step[1], fault=step[2], placed=p["path"],
                          reason=str(r.get("reason") or "")[:240], **timing))
    if not added:
        ctx.log("composed picture check: every finding has had each repair; the film is delivered with them on record")
        return False
    rejected_path = os.path.join(ctx.supply, "render_rejected.json")
    json.dump(previous + added, open(rejected_path, "w"), indent=1)
    from placement_check import CONTRACT
    json.dump(CONTRACT, open(os.path.join(ctx.supply,"render_rejected_contract.json"),"w"))
    ctx.log(f"composed picture check: {sum(a['fault'] == 'reframe' for a in added)} framed again, "
            f"{sum(a['fault'] == 'contradiction' for a in added)} windows given their next picture, "
            f"{sum(a['fault'] == 'speaker' for a in added)} given to the speaker")
    # A contradiction that comes from a window running into the next subject's words is a timing fault: the director
    # moves that boundary. Reframes need no boundary.
    _repair_window_boundaries(ctx, boundary_findings)
    if not defer:
        _rescore(ctx,script,sc)
    return True


def measure_cuts(path, ffmpeg, threshold=0.3, hold_s=None):
    """The delivered film's picture changes, measured on its pixels (ffmpeg scene scores at 160 px wide): the number of
    cuts, cuts per 10 s, the longest stretch without one and every stretch longer than hold_s (RESEARCH_HOLD_S), in
    seconds. It feeds research (_rebuild_holds) and the record; never a gate."""
    hold_s = RESEARCH_HOLD_S if hold_s is None else hold_s
    import re
    try:
        r = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-i", path, "-vf",
                            f"scale=160:-2,select='gt(scene,{threshold})',showinfo", "-an", "-f", "null", "-"],
                           capture_output=True, text=True, timeout=600)
        d = subprocess.run([ffmpeg, "-hide_banner", "-i", path], capture_output=True, text=True, timeout=60).stderr
    except (OSError, subprocess.SubprocessError):
        return None
    times = [float(t) for t in re.findall(r"pts_time:([0-9.]+)", r.stderr)]
    m = re.search(r"Duration: (\d+):(\d+):([0-9.]+)", d)
    if not m:
        return None
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    edges = [0.0] + times + [dur]
    return dict(cuts=len(times), cuts_per_10s=round(len(times) * 10 / max(dur, 0.01), 2),
                longest_hold_s=round(max(b - a for a, b in zip(edges, edges[1:])), 2), seconds=round(dur, 2), threshold=threshold,
                holds=[[round(a, 2), round(b, 2)] for a, b in zip(edges, edges[1:]) if b - a > hold_s])


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
        display_facts=b.get("display_facts", []), placement_check=b.get("placement_check"), rendered_check=b.get("rendered_check"), render_reviews=rd.get("picture_review"),
        delivered_check=b.get("delivered_check"), delivery_reviews=history,
        sources=[dict(f=p.get("f"), visible_f=p.get("visible_f"), kind=p.get("kind"), name=p.get("name"), source=p.get("source"), time_range=p.get("time_range"),
                      mode=p.get("mode"), window=p.get("window"), w=p.get("w"), h=p.get("h"), rung=p.get("rung"), how=p.get("how"),
                      source_size=p.get("source_size"), upscaled_from=p.get("upscaled_from"), reframed=p.get("reframed"),
                      origin=p.get("origin"), name_super=p.get("name_super"), attribution=p.get("attribution"),
                      pic=p.get("pic"), source_slice=p.get("source_slice"), source_evidence=p.get("source_evidence"))
                 for p in pics], labels=b.get("labels") or [],
        quoted_source=(b.get("coverage") or {}).get("quoted_source",[]),
        authored_source=(b.get("coverage") or {}).get("authored_source",[]),
        speaker_panels=(b.get("coverage") or {}).get("speaker_panels",[]))
    # Measured on the delivered programme's pixels: how often the picture changes and the longest hold (a record that
    # shows where a film runs on the speaker alone; never a gate).
    film = rd.get("film")
    if film and os.path.isfile(film):
        r["checks"]["podcast"]["measured"] = rd.get("measured_cuts") or measure_cuts(film, PX.FF)
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
