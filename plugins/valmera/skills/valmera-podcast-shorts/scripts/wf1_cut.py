"""Workflow 1 as a footage cut: a podcast moment the workflow already found, made again on the engines.

    PY=/Users/masaoodi/Documents/Valmera/.valmera/venv-worker/bin/python
    $PY run_cut.py run --source <podcast file> --out <deliver root> --args '{"again": "<a delivered wf1 folder>"}'
    $PY run_cut.py run --resume <run_id>

No model writes or directs anything here: the moment, its window, title and lines come from the delivered film's
own record. What a model reads is the footage engine's (worker/footage, OpenAI): which real people, companies and
places each sentence names, and whether a frame shows them.

ideas    the delivered film's moment (--args again), read from its film.json
script   its window, title, hook and close, in the style `footage-cut`
supply   the window cut from the source and heard locally (the shell's pack); real video of what each sentence names
         (worker/footage through the shell: YouTube and archive.org, every frame checked, 1-3 s clips, credited);
         photographs of the same things (Wikimedia Commons and Openverse, checked, licensed); the speaker's face
score    film.podcast_cut (valmera-stories/film.py) bakes the scene: the take as spine, cutaways on the clauses that
         name things, captions inked word by word, the title on the seat, a label once per thing; then the engines
         (pace, look, sound). The look sheet is drawn; the run stops for the director's look unless look is false
render, check   as the stories workflow: the engine's own mix of the take's sound and the placed sounds, the scene
         engine (the box image through SHELL_RENDER_CMD), the brand finish, the owner's song on a copy when one is
         handed to the run. Never publishes, never picks music.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.dirname(os.path.dirname(HERE))
for p in (os.path.join(SKILLS, "_shell"), os.path.join(SKILLS, "valmera-stories")):
    if p not in sys.path:
        sys.path.insert(0, p)
import chain  # noqa: E402
import film as F  # noqa: E402
import pictures as PX  # noqa: E402
import wf1  # noqa: E402
import wf3  # noqa: E402

# wf1 reads the shell's own llm.py; the worker has an llm module of its own, and its OpenAI rung (score.call_openai)
# imports it by that name. Left in sys.modules, the shell's would answer for it and every picture check would fail.
sys.modules.pop("llm", None)

STYLE = "footage-cut"


def _record(ctx):
    folder = (ctx.args or {}).get("again")
    if not folder:
        raise RuntimeError("the footage cut re-makes a delivered moment: --args '{\"again\": \"<delivered wf1 folder>\"}'")
    rec = json.load(open(os.path.join(folder, "film.json")))
    if os.path.abspath((rec.get("source") or {}).get("path") or "") != os.path.abspath(ctx.source):
        raise RuntimeError("--args again: that film was made from another source")
    return rec, folder


def ideas(ctx):
    rec, folder = _record(ctx)
    w = rec.get("window") or [rec["idea"].get("t0"), rec["idea"].get("t1")]
    idea = dict(rec["idea"], t0=float(w[0]), t1=float(w[1]), styles=[dict(id=STYLE, why="the engines' cut of a moment already delivered")],
                again=dict(folder=folder, run_id=rec.get("run_id"), was=(rec.get("style") or {}).get("id")))
    return dict(source=dict(wf1.source_log(ctx.source), transcript="the delivered film's window, heard again locally"), ideas=[idea], chosen=None,
                again=idea["again"], spend=[])


def script(ctx, ideas_out):
    rec, _ = _record(ctx)
    old = rec.get("script") or {}
    idea = ideas_out["ideas"][0]
    ideas_out["chosen"] = idea
    reg = next((s for s in ctx.styles_allowed() if s["id"] == STYLE), None)
    if reg is None:
        raise RuntimeError(f"style {STYLE} is one of the last three, or not in the registry")
    return dict(title=old.get("title") or idea.get("title"), style=reg, window=[float(idea["t0"]), float(idea["t1"])], source=ctx.source,
                hook_line=old.get("hook_line") or idea.get("hook"), close_line=old.get("close_line") or idea.get("payoff"),
                speakers=old.get("speakers") or idea.get("speakers") or 1, names=idea.get("names") or [],
                direction=f"{reg['name']}: {reg.get('look', '')}", idea=idea, spend=[])


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
    words = []
    for si, s in enumerate(sents):
        for w in idx["words"][s["wi0"]:s["wi1"] + 1]:
            words.append(dict(w=w["w"], start=round(float(w["t0"]), 3), end=round(float(w["t1"]), 3), seg=si))
    dur = float(idx["video"]["duration"])
    wj = os.path.join(sup_dir, "words.json")
    json.dump(dict(voice="the take", duration=round(max(w["end"] for w in words) + 0.05, 3) if words else dur, words=words), open(wj, "w"), ensure_ascii=False)
    spend = []
    # real video of what each sentence names: the footage engine's own planner reads the sentences (OpenAI)
    who = wf1.identity_text(ctx.source)[:400].replace("\n", " ")
    fscript = dict(title=f"{script.get('title')}. A podcast clip; the speaker is on camera already, so show what he talks about, as it was then, "
                         f"never him as he looks today. Who is in it: {who}",
                   segments=[dict(say=s["text"], pictures=[]) for s in sents], named=[])
    wf3.given_footage(ctx, os.path.join(sup_dir, "footage"))
    # cut wide, as shot (1080 x 608): a podcast's cutaways play in a window over the take, so an archive's small
    # copy (the Wayback Machine's 360p) is never blown up to fill the phone
    foot = chain.footage(ctx, dict(script=fscript), [round(float(s["t1"]) - float(s["t0"]) + 0.2, 2) for s in sents], W=1080, H=608)
    pack = dict(sid=ctx.sid, words=wj, narration=src, ffmpeg=PX.FF, sounds={}, pictures={}, credits=[], footage=[], title=script.get("title"))
    subjects, by_line = [], {}
    if foot:
        fdir = os.path.join(sup_dir, "footage")
        pack["footage"] = [dict(i=ln["i"], clips=[wf3.clip_facts(dict(c, path=os.path.join(fdir, c["file"]))) for c in ln["clips"]]) for ln in foot["lines"]]
        pack["credits"] += [dict(name=c["title"], title=c["title"], licence="footage, not licensed (owner's decision)", credit=c["channel"], page=c["url"],
                                 time_range=c["time_range"], line=c["line"]) for c in foot["credits"]]
        subjects = [s for s in foot.get("subjects") or [] if s.get("name")]
        by_line = {int(ln["i"]): list(ln.get("subjects") or []) for ln in foot["lines"]}
        spend.append(dict(stage="supply", provider="openai", model="gpt-6-sol low + gpt-4o-transcribe", what="footage", usd=(foot.get("spend") or {}).get("usd")))
        ctx.log(f"footage: {foot['counts']['clips']} clips on {foot['counts']['lines_with_footage']} of {foot['counts']['lines']} sentences; "
                f"subjects {[s['name'] for s in subjects]}")
    # photographs of the same things, for the sentences the footage left bare
    named = [dict(name=s["name"], searches=[s["name"]], must_show=s.get("must_show") or s["name"], **{"is": s.get("kind") or "thing"})
             for s in subjects if any(s["name"] in v for v in by_line.values())][:6]
    if named:
        got, reports, usd = PX.pictures(named, os.path.join(sup_dir, "pics"), per=3)
        B = PX._worker()
        spend.append(dict(stage="supply", provider="openai" if str(B.NAMED_CHECK_MODEL).startswith("gpt") else "anthropic", model=B.NAMED_CHECK_MODEL,
                          what="picture check", usd=usd))
        for recs in got.values():
            for r in recs:
                try:
                    r["faces"] = PX.faces(r["path"])
                except Exception:  # noqa: BLE001
                    r["faces"] = []
        pack["pictures"] = {k: v for k, v in got.items() if v}
        pack["picture_reports"] = reports
        pack["credits"] += [dict(name=r["name"], title=r.get("title"), licence=r.get("licence"), credit=r.get("credit"), page=r.get("page"))
                            for v in got.values() for r in v]
    # the take: its size and the speaker's face
    import subprocess
    still = os.path.join(sup_dir, "take.jpg")
    subprocess.run([PX.FF, "-v", "error", "-y", "-ss", f"{min(dur / 2, 3.0):.2f}", "-i", src, "-frames:v", "1", "-q:v", "3", still], check=True, timeout=120)
    v = idx["video"]
    try:
        faces = PX.faces(still)
    except Exception:  # noqa: BLE001
        faces = []
    pack["take"] = dict(path=src, w=int(v["width"]), h=int(v["height"]), fps=float(v["fps"]), dur=dur, faces=faces, still=still, shots=len(idx.get("shots") or []))
    pack["lines"] = [dict(i=i, t0=s["t0"], t1=s["t1"], text=s["text"], subjects=by_line.get(i) or []) for i, s in enumerate(sents)]
    rec = dict(rec0, narration_s=dur, voice="the take", gate=None, pictures=sum(len(x) for x in pack["pictures"].values()), subjects=len(pack["pictures"]),
               footage=(dict(clips=foot["counts"]["clips"], lines=foot["counts"]["lines_with_footage"], credits=foot["credits"]) if foot else None), spend=spend)
    json.dump(dict(pack=pack, record=rec), open(pk, "w"), ensure_ascii=False, indent=1)
    return rec


def score(ctx, script):
    """The stories workflow's score stage on this film's data: the sentences as segments, each with the things it names."""
    pack = wf3.load_pack(ctx)
    sc = dict(title=script.get("title"), hook_line=script.get("hook_line"), close_line=script.get("close_line"),
              segments=[dict(say=ln["text"], pictures=[n for n in ln.get("subjects") or []]) for ln in pack["lines"]])
    return wf3.score(ctx, dict(script, script=sc))


render = wf3.render
check = wf3.check
