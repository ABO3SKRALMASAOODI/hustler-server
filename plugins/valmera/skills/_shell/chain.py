"""The shared middle of every workflow: supply, Score, render, check. Mac engine, through heavy.sh.

Today this drives the slice (week-1106/director/slice): its tools key everything by a session id
under slice/work/<sid>, so the shell links slice/work/<sid> to the run's own supply/ folder and
removes the link when the run ends. Every byte the tools write lands inside the run folder.

Engine choice: the slice engine, until week-1112/notes/C.md names worker/scene and the package is
in the checkout; then `python -m scene.run --local` (BUILD W1). SHELL_ENGINE=slice|scene forces one.
"""
import json
import os
import shutil
import subprocess

import brand

V = "/Users/masaoodi/Documents/Valmera"
VV = f"{V}/.valmera"
SLICE = os.environ.get("SHELL_SLICE", f"{VV}/week-1106/director/slice")
HEAVY = f"{VV}/week-1106/heavy.sh"
PY = f"{VV}/venv-worker/bin/python"                       # slice tools, engine, brand
ATLAS_PY = f"{VV}/week-1106/atlas/venv/bin/python"        # mlx_whisper
CREDIT = f"{VV}/claude_trial_env.sh"                      # the credited Claude key, this process only
SOUNDS = f"{VV}/../worktrees/ops-loop-main/worker/sound_library/manifest.json"
NOTES_C = f"{VV}/week-1112/notes/C.md"


def engine_kind():
    forced = os.environ.get("SHELL_ENGINE")
    if forced:
        return forced
    scene = os.path.join(os.path.dirname(brand.worker_dir()), "worker", "scene", "run.py")
    if os.path.exists(NOTES_C) and "worker/scene" in open(NOTES_C).read() and os.path.exists(scene):
        return "scene"
    return "slice"


def sh(ctx, cmd, label=None, heavy=False, model=False, timeout=None):
    """Run one tool. heavy: through heavy.sh. model: under the credited key wrapper.
    TMPDIR and SLICE_TMP point inside the run, so a tool's scratch is deleted with the run."""
    env = dict(os.environ, TMPDIR=ctx.tmp, SLICE_TMP=ctx.tmp, PYTHONDONTWRITEBYTECODE="1",
               PATH=os.path.dirname(brand.FF) + ":" + os.environ.get("PATH", "/usr/bin:/bin"))
    if model:
        cmd = ["zsh", CREDIT] + cmd
    if heavy:
        cmd = ["zsh", HEAVY, label or f"wf-{ctx.run_id}"] + cmd
    ctx.log("run " + " ".join(os.path.basename(c) if i == 0 else c for i, c in enumerate(cmd))[:300])
    r = subprocess.run(cmd, cwd=SLICE, env=env, capture_output=True, text=True, timeout=timeout)
    ctx.log_raw(r.stdout[-4000:] + r.stderr[-4000:])
    if r.returncode != 0:
        raise RuntimeError(f"{os.path.basename(cmd[-1] if not cmd[-1].startswith('-') else cmd[0])} failed ({r.returncode}): {r.stderr.strip()[-600:]}")
    return r.stdout


def link(ctx):
    """slice/work/<sid> -> run/supply. Removed by unlink() at cleanup."""
    os.makedirs(ctx.supply, exist_ok=True)
    lk = os.path.join(SLICE, "work", ctx.sid)
    if os.path.islink(lk) and os.readlink(lk) == ctx.supply:
        return
    if os.path.lexists(lk):
        raise RuntimeError(f"slice/work/{ctx.sid} exists and is not this run's link")
    os.symlink(ctx.supply, lk)


def unlink(sid):
    lk = os.path.join(SLICE, "work", sid)
    if os.path.islink(lk):
        os.unlink(lk)


def sweep_links(live_dirs):
    """Links left by runs that died: any wf-* link in slice/work whose target is not a live run."""
    d = os.path.join(SLICE, "work")
    for n in os.listdir(d) if os.path.isdir(d) else []:
        p = os.path.join(d, n)
        if n.startswith("wf-") and os.path.islink(p) and not any(os.readlink(p).startswith(x) for x in live_dirs):
            os.unlink(p)


# ----------------------------------------------------------------- supply: the session pack

def cut(ctx, src, t0, t1, out):
    """One window of the source, frame-accurate, H.264 at its own size, audio kept."""
    subprocess.run([brand.FF, "-v", "error", "-y", "-ss", f"{t0:.3f}", "-i", src, "-t", f"{t1 - t0:.3f}",
                    "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "fast", "-crf", "16",
                    "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", out],
                   check=True, capture_output=True)


def shots_of(path, dur, thresh=0.3):
    r = subprocess.run([brand.FF, "-v", "info", "-i", path, "-vf", f"select='gt(scene,{thresh})',showinfo", "-an", "-f", "null", "-"],
                       capture_output=True, text=True)
    ts = []
    for ln in r.stderr.splitlines():
        if "pts_time:" in ln:
            try:
                ts.append(float(ln.split("pts_time:")[1].split()[0]))
            except ValueError:
                pass
    edges = [0.0] + [t for t in ts if 0.3 < t < dur - 0.3] + [dur]
    return [dict(id=i + 1, start=round(a, 3), end=round(b, 3), caption=None, thumb_key=None)
            for i, (a, b) in enumerate(zip(edges, edges[1:])) if b - a > 0.05]


def index_from_hearing(h, video, shots, speakers):
    """The product's index shape (words, sentences, shots, video) from a word-timed hearing."""
    words, sents = [], []
    for ln in h.get("lines") or []:
        if not ln.get("words"):
            continue
        i0 = len(words)
        for w in ln["words"]:
            if not w["w"]:
                continue
            words.append(dict(w=w["w"], t0=w["t0"], t1=max(w["t1"], w["t0"] + 0.02), filler=False, speaker=0, confidence=w.get("p", 1.0)))
        if len(words) > i0:
            sents.append(dict(id=f"s{len(sents) + 1}", t0=words[i0]["t0"], t1=words[-1]["t1"], wi0=i0, wi1=len(words) - 1,
                              text=" ".join(w["w"] for w in words[i0:]), speaker=0))
    return dict(video=video, words=words, sentences=sents, shots=shots, language=h.get("language") or "en",
                speakers=speakers, silences=[], moments=[], perception=None, warnings=[], version="wf-shell-1")


def pack(ctx, script):
    """supply/{source.mp4, hearing.json, index.json, session.json}: the window as a session the slice reads."""
    link(ctx)
    w = ctx.supply
    src = os.path.join(w, "source.mp4")
    t0, t1 = float(script["window"][0]), float(script["window"][1])
    if not os.path.exists(os.path.join(w, "index.json")):
        cut(ctx, script["source"], t0, t1, src + ".part.mp4")
        os.replace(src + ".part.mp4", src)
        sh(ctx, [ATLAS_PY, "hear.py", ctx.sid], label=f"wf-hear-{ctx.run_id}", heavy=True, timeout=3600)
        p = brand.probe(src)
        video = dict(fps=round(p["fps"], 3), width=p["w"], height=p["h"], duration=round(p["dur"], 3), has_audio=p["audio"], vfr_normalized=False)
        idx = index_from_hearing(json.load(open(os.path.join(w, "hearing.json"))), video, shots_of(src, p["dur"]), script.get("speakers", 1))
        sess = dict(sid=ctx.sid, project_id=None, family="workflow", title=script.get("title"), kind="workflow", mode=ctx.workflow,
                    uploads=[dict(kind="original", filename=script.get("file_name") or os.path.basename(script["source"]),
                                  duration_s=video["duration"], width=video["width"], height=video["height"], fps=video["fps"])],
                    prompts=[dict(text=script["direction"], typed_phase="before_result")], steps=[], replies=[])
        for name, obj in (("session.json", sess), ("index.json", idx)):
            json.dump(obj, open(os.path.join(w, name), "w"), ensure_ascii=False, indent=1)
    idx = json.load(open(os.path.join(w, "index.json")))
    return dict(window=[t0, t1], words=len(idx["words"]), shots=len(idx["shots"]), dur=idx["video"]["duration"])


def supply(ctx, script):
    """Perception, the Brief, the Scout's asks, pictures (the Bin), offers, the Brief again with all of it."""
    rec = pack(ctx, script)
    w, sid = ctx.supply, ctx.sid
    steps = [("perception.json", [PY, "perceive.py", sid], dict(heavy=True)),
             ("brief/manifest.json", [PY, "brief.py", "build", sid], {}),
             ("scout.json", [PY, "scout.py", sid], dict(model=True)),
             ("bin.json", [PY, "supply.py", sid], dict(model=True)),
             ("brief/offers.jpg", [PY, "offers.py", sid], dict(heavy=True))]
    for out, cmd, kw in steps:
        if not os.path.exists(os.path.join(w, out)):
            sh(ctx, cmd, label=f"wf-{os.path.basename(cmd[1])[:-3]}-{ctx.run_id}", timeout=3600, **kw)
    styles = script.get("cards")
    sh(ctx, [PY, "brief.py", "build", sid] + (["--styles", ",".join(styles)] if styles else []))
    spend = []
    sc = json.load(open(os.path.join(w, "scout.json"))).get("_usage") or {}
    if sc.get("usd") is not None:
        spend.append(dict(stage="supply", provider="anthropic", model=sc.get("model"), what="scout", usd=sc["usd"]))
    bu = json.load(open(os.path.join(w, "bin.json"))).get("usage") or {}
    if bu.get("usd") is not None:
        spend.append(dict(stage="supply", provider="anthropic", model=bu.get("model"), what="supply look", usd=bu["usd"]))
    b = json.load(open(os.path.join(w, "bin.json")))
    rec.update(bin_items=len(b.get("items") or []), credits=b.get("credits"), spend=spend)
    return rec


def score(ctx, script):
    """The Director writes the Score; the compiler turns it into L1. One run, with its repair round."""
    run = os.path.join(ctx.supply, "run1")
    if not os.path.exists(os.path.join(run, "l1.json")):
        sh(ctx, [PY, "direct.py", ctx.sid, "--run", "1", "--effort", script.get("effort", "medium"), "--proof",
                 "--note", f"workflow {ctx.workflow}, style {script['style']['id']}"], model=True, timeout=3600)
    u = json.load(open(os.path.join(run, "usage.json")))
    return dict(l1=sound(ctx, os.path.join(run, "l1.json")), usd=u.get("usd"), gate=u.get("gate"), asks_open=u.get("asks_open"),
                spend=[dict(stage="score", provider="anthropic", model=u.get("model"), what="director", usd=u.get("usd") or 0)])


def sound(ctx, l1_path):
    """The film's sound design (worker/score/sound.py, lane J): each chosen picture event gets a library sound with
    its peak on the event, inside the footage's allowance, checked against the voice. Writes l1_sound.json beside
    the Director's L1 and returns its path; the Director's own L1 when the pass cannot run (logged, never a stop)."""
    out = l1_path[:-len(".json")] + "_sound.json"
    if not os.path.exists(out):
        wk = brand.worker_dir()
        try:
            extra = ""
            if (ctx.args or {}).get("song"):                 # the owner handed this run a song: the sounds are designed for a film with a bed
                extra += " --bed"
            if (ctx.args or {}).get("turn_s") is not None:   # and the story's turn, when the run names it
                extra += f" --turn {float(ctx.args['turn_s']):.3f}"
            sh(ctx, ["zsh", "-c", f"cd '{wk}' && PYTHONPATH=. '{PY}' -m score.sound design --l1 '{l1_path}' --work '{ctx.supply}' --out '{out}'{extra}"],
               timeout=900)
        except (RuntimeError, subprocess.SubprocessError) as e:
            ctx.log(f"sound design skipped: {str(e)[:300]}")
            return l1_path
    return out


def render(ctx, script, sc):
    """The engine draws the L1 film. Always through heavy.sh."""
    out = os.path.join(ctx.supply, "film_engine.mp4")
    kind = engine_kind()
    if not os.path.exists(out):
        if kind == "scene":
            wk = brand.worker_dir()
            sh(ctx, ["zsh", "-c", f"cd {wk} && PYTHONPATH=.. {PY} -m scene.run --local --l1 {sc['l1']} --work {ctx.supply} --out {out}.part.mp4"],
               label=f"wf-render-{ctx.run_id}", heavy=True, timeout=7200)
        else:
            sh(ctx, [PY, "engine.py", "render", "--l1", sc["l1"], "--work", ctx.supply, "--out", out + ".part.mp4"],
               label=f"wf-render-{ctx.run_id}", heavy=True, timeout=7200)
        os.replace(out + ".part.mp4", out)
    chk = {}
    if kind == "slice":
        try:
            chk = json.loads(sh(ctx, [PY, "engine.py", "check", "--l1", sc["l1"], "--work", ctx.supply, "--out", out]) or "{}")
        except (RuntimeError, ValueError) as e:
            chk = dict(error=str(e)[:300])
    return dict(engine=kind, film=out, engine_check=chk)


def song_version(ctx, rd, sc):
    """The same film with the owner's song under it, when the owner handed one to this run (--args '{"song": path}').
    Valmera never picks a song: without that argument this does nothing. The song is cut to the film by
    worker/score/music.py (its drop on the turn, its last hit before the end card, under the voice), mixed with the
    film's own voice and sounds, and laid on a copy of the engine's picture. film.mp4 stays the clean export."""
    song = (ctx.args or {}).get("song")
    if not song or not os.path.exists(str(song)) or not sc.get("l1"):
        return None
    out = os.path.join(ctx.supply, "film_engine_song.mp4")
    if not os.path.exists(out):
        wk = brand.worker_dir()
        cmd = (f"cd '{wk}' && PYTHONPATH=. '{PY}' -m score.music mix --l1 '{sc['l1']}' --work '{ctx.supply}' --song '{song}' "
               f"--film '{rd['film']}' --out '{out}.part.mp4' --kind {SONG_KIND.get(ctx.workflow, 'stories')}")
        if (ctx.args or {}).get("song_vocals"):
            cmd += f" --vocals '{ctx.args['song_vocals']}'"
        if (ctx.args or {}).get("turn_s") is not None:
            cmd += f" --turn {float(ctx.args['turn_s']):.3f}"
        try:
            sh(ctx, ["zsh", "-c", cmd], label=f"wf-song-{ctx.run_id}", heavy=True, timeout=1800)
            os.replace(out + ".part.mp4", out)
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            ctx.log(f"song version skipped: {str(e)[:300]}")
            return None
    final = os.path.join(ctx.run_dir, "final_song.mp4")
    if not os.path.exists(final):
        brand.finish(out, final + ".part.mp4", ctx.tmp)
        os.replace(final + ".part.mp4", final)
    return dict(final_song=final, song=dict(file=os.path.basename(str(song)), supplied_by="owner", picked_by_valmera=False))


SONG_KIND = {"wf1": "podcast", "wf2": "marketing", "wf3": "stories", "wf4": "longform"}


def no_music(l1_path):
    """Valmera never picks music: every sound in the plan is a library effect or a synthesized one."""
    lib = {s["file"] for s in json.load(open(SOUNDS))}
    l1 = json.load(open(l1_path))
    bad = [s.get("file") for s in l1.get("sound") or [] if s.get("file") and s["file"] not in lib]
    audio_assets = [k for k, a in (l1.get("assets") or {}).items() if a.get("kind") == "audio"]
    return dict(sounds=len(l1.get("sound") or []), foreign_sounds=bad, audio_assets=audio_assets, ok=not bad and not audio_assets)


def finish_and_check(ctx, rd, sc):
    """Brand finish, then the measured checks. A failed check is reported, never a stop."""
    final = os.path.join(ctx.run_dir, "final.mp4")
    if not os.path.exists(final):
        info = brand.finish(rd["film"], final + ".part.mp4", ctx.tmp)
        os.replace(final + ".part.mp4", final)
    else:
        info = {}
    c = brand.check(rd["film"], final, ctx.tmp)
    m = no_music(sc["l1"]) if sc.get("l1") and os.path.exists(sc["l1"]) else dict(ok=True, note="no plan")
    c.update(music=m, finish=info)
    c["ok"] = bool(c["ok"] and m["ok"])
    out = dict(final=final, checks=c)
    sv = song_version(ctx, rd, sc)               # only when the owner handed this run a song; film.mp4 stays clean
    if sv:
        out.update(sv)
    return out


def cleanup_dir(path):
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path, ignore_errors=True)
