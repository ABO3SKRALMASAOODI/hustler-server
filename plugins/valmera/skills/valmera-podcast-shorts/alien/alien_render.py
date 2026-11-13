"""The render stage of a podcast short whose style has alien passes.

    pass 1   the engine draws the Director's pictures (shots, cards) with the film's mixed sound
    passes   each alien pass of the style works on that film, in order (passes.py), under heavy.sh
    pass 2   the engine lays the type and the flashes over the result; pass 1's sound is joined back

Every step writes one file inside the run's supply folder (``alien/``) and a step whose file exists is
not redone, so a stopped render resumes where it stopped. Placement (TASTE P2) is repaired on the plan
before anything is drawn, and measured again after: the film carries its own numbers in ``film.json``.
"""
import json
import os
import subprocess

import l1pass as L
import sidecars as S

HERE = os.path.dirname(os.path.abspath(__file__))
PLAN_PASSES = ("footnotes",)


def _engine(ctx, chain, l1_path, out, label, audio=True):
    if os.path.exists(out):
        return
    kind = chain.engine_kind()
    if kind == "scene":
        import brand
        wk = brand.worker_dir()
        au = "" if audio else " --no-audio"
        chain.sh(ctx, ["zsh", "-c", f"cd {wk} && PYTHONPATH=..:{chain.SLICE}/pylib {chain.PY} -m scene.run render --l1 {l1_path} --work {ctx.supply} "
                                      f"--out {out}.part.mp4 --tier master{au}"], label=label, heavy=True, timeout=7200)
    else:
        chain.sh(ctx, [chain.PY, "engine.py", "render", "--l1", l1_path, "--work", ctx.supply, "--out", out + ".part.mp4"]
                 + ([] if audio else ["--no-audio"]), label=label, heavy=True, timeout=7200)
    os.replace(out + ".part.mp4", out)


def _sound(ctx, chain, l1, adir, fallback):
    """The sound design of the finished plan (worker/score/sound.py, week-1112/notes/J.md): every picture event the
    plan now holds is offered to it, with no sound of ours in the way. When the pass is not in the checkout or
    cannot run, the plan keeps the two sounds earned.py placed by the owner's rule, and the record says which."""
    import brand
    wk = brand.worker_dir()
    src, out = os.path.join(adir, "l1_plan.json"), os.path.join(adir, "l1_sound.json")
    if not os.path.exists(os.path.join(wk, "score", "sound.py")):
        return dict(fallback or {}, by="earned (the sound pass is not in this checkout)")
    if not os.path.exists(out):
        json.dump(dict(l1, sound=[]), open(src, "w"), ensure_ascii=False)
        try:
            chain.sh(ctx, ["zsh", "-c", f"cd '{wk}' && PYTHONPATH=. '{chain.PY}' -m score.sound design --l1 '{src}' --work '{ctx.supply}' --out '{out}'"],
                     timeout=900)
        except (RuntimeError, subprocess.SubprocessError) as e:
            ctx.log(f"sound design skipped: {str(e)[:300]}")
            return dict(fallback or {}, by="earned (the sound pass could not run)")
    got = json.load(open(out))
    rep = got.get("sound_design") or {}
    if rep.get("fault") or rep.get("no_signal"):
        return dict(fallback or {}, by="earned (the sound pass placed nothing)", sound_design=rep)
    l1["sound"] = got.get("sound") or []
    return dict(by="score.sound", placed=len(l1["sound"]), cls=rep.get("class"), np10=rep.get("np10"), allow=rep.get("allow"),
                events=[dict(f=s_.get("event_f"), s=s_.get("s"), db=s_.get("db")) for s_ in l1["sound"]], dropped=rep.get("dropped"))


def render(ctx, script, sc, chain):
    adir = os.path.join(ctx.supply, "alien")
    os.makedirs(adir, exist_ok=True)
    out = os.path.join(ctx.supply, "film_engine.mp4")
    l1 = json.load(open(sc["l1"]))
    passes = list(script.get("passes") or [])
    plan = {}
    ep = os.path.join(ctx.supply, "earned", "earned.json")
    if os.path.exists(ep):                              # the story's pictures, the hook, his own footage closer, motion, sound
        import earned
        plan["earned"] = earned.apply(l1, script, json.load(open(ep)), json.load(open(os.path.join(ctx.supply, "index.json"))),
                                      os.path.join(ctx.supply, "source.mp4"), json.load(open(chain.SOUNDS)),
                                      own="second_camera" not in passes, panel_low=False, work=ctx.supply)
    if "footnotes" in passes:                           # a plan pass: type added to the plan, drawn with the type
        import footnotes
        fp = os.path.join(adir, "footnotes.json")
        notes = json.load(open(fp))["footnotes"] if os.path.exists(fp) else []
        plan["footnotes"] = footnotes.apply(l1, notes, hold=[tuple(d["frames"]) for d in (plan.get("earned") or {}).get("designs") or []])
        plan["footnotes"]["ok"] = True
    passes = [p for p in passes if p not in PLAN_PASSES]
    look = script.get("look")
    film = l1.setdefault("film", {})
    graded = any(abs(float((film.get("grade") or {}).get(k, v)) - v) > 1e-6 for k, v in L.IDENTITY.items())
    if look and (not graded or "earned" in plan):       # the style's look: one grade for the take and every found picture
        film["grade"] = dict(look["grade"])
        for k in ("grain", "vignette", "halation", "bloom"):
            if look.get(k) is not None:
                film[k] = look[k]
        for it in l1["items"]:                          # a shot the Director graded apart joins the film's look
            if it.get("k") == "shot" and it.get("pic") == "main":
                it.pop("grade", None)
        plan["look"] = dict(applied=look, ok=True)
    moved = L.repair_placement(l1)
    if "earned" in plan:                                # the film's sound: lane J's pass on the finished plan, where the events are
        plan["earned"]["sound"] = _sound(ctx, chain, l1, adir, plan["earned"].get("sound"))
    p1, _ = L.split(l1, "alien/lit.mp4")
    paths = {k: os.path.join(adir, k + ".json") for k in ("l1", "p1", "p2")}
    for k, obj in (("l1", l1), ("p1", p1)):
        json.dump(obj, open(paths[k], "w"), ensure_ascii=False)
    pass1 = os.path.join(adir, "pass1.mp4")
    _engine(ctx, chain, paths["p1"], pass1, f"wf-alien-p1-{ctx.run_id}")
    cur, measures = pass1, dict(plan)
    if "second_camera" in passes and not os.path.exists(os.path.join(adir, "depth.npy")):
        import brand
        chain.sh(ctx, [chain.ATLAS_PY, os.path.join(HERE, "geom_atlas.py"), "depth", pass1, adir, brand.worker_dir(), paths["l1"]],
                 label=f"wf-alien-depth-{ctx.run_id}", heavy=True, timeout=3600)
    for name in passes:
        nxt = os.path.join(adir, f"{name}.mp4")
        mp = os.path.join(adir, f"measure_{name}.json")
        if not (os.path.exists(nxt) and os.path.exists(mp)):
            chain.sh(ctx, [chain.PY, os.path.join(HERE, "passes.py"), name, "--film", cur, "--l1", paths["l1"], "--dir", adir, "--out", nxt],
                     label=f"wf-alien-{name}-{ctx.run_id}", heavy=True, timeout=7200)
        measures[name] = json.load(open(mp))
        cur = nxt
    lit = os.path.join(adir, "lit.mp4")
    if not os.path.exists(lit):
        os.link(cur, lit)
    tp = os.path.join(adir, "track.npy")                                # faces on the picture the type will stand on
    if not os.path.exists(tp):
        import numpy as np
        n = int(l1["dur_f"])
        np.save(tp, S.track(S.faces(pass1, n), L.cuts(l1), n))
    import numpy as np
    off_faces = L.repair_faces(l1, np.load(tp))
    split_caps = L.split_captions_at(l1, L.picture_cuts(l1)) if "earned" in plan else 0
    mids = {f for ws in L.groups(l1).values() for f in L.sample_frames(min(w["f"][0] for w in ws), max(w["f"][1] for w in ws))}
    pooled = L.repair_contrast(l1, S.gray_frames(lit, mids))            # measured on the picture the type will stand on
    _, p2 = L.split(l1, "alien/lit.mp4")
    json.dump(p2, open(paths["p2"], "w"), ensure_ascii=False)
    pass2 = os.path.join(adir, "pass2.mp4")
    _engine(ctx, chain, paths["p2"], pass2, f"wf-alien-p2-{ctx.run_id}", audio=False)
    if not os.path.exists(out):
        subprocess.run([S.FF, "-nostdin", "-v", "error", "-y", "-i", pass2, "-i", pass1, "-map", "0:v:0", "-map", "1:a:0", "-c", "copy",
                        "-shortest", "-movflags", "+faststart", out + ".part.mp4"], check=True, capture_output=True)
        os.replace(out + ".part.mp4", out)
    bad = L.outside_safe(l1)
    pictures = len(L.picture_shots(l1)) + len(L.cards(l1))
    measures.pop("earned", None)
    alien = dict(passes=list(script.get("passes") or []), measures=measures, earned=plan.get("earned"), placement=dict(moved=moved, outside_safe=bad, safe_area=list(L.SAFE), pooled=pooled, off_faces=off_faces, captions_split_at_cuts=split_caps),
                 new_pictures=pictures, sounds=len(l1.get("sound") or []),
                 ok=bool(all(m.get("ok", True) for m in measures.values()) and not bad and (plan.get("earned") or {}).get("ok", True)))
    return dict(engine=chain.engine_kind() + "+alien", film=out, engine_check=dict(skipped="the alien passes change the picture by design"),
                alien=alien, plan=paths["l1"])
