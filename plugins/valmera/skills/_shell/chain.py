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


def _run_alive(target):
    """True when a link's target is the supply folder of a run whose owner still lives (any state root)."""
    try:
        st = json.load(open(os.path.join(os.path.dirname(target.rstrip("/")), "run.json")))
        os.kill(int(st.get("pid")), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def sweep_links(live_dirs):
    """Links left by runs that died: a wf-* link in slice/work whose target is not a live run. A run under another
    state root (a podcast run keeps one per short, and other workflows run beside it) is live while its owner is."""
    d = os.path.join(SLICE, "work")
    for n in os.listdir(d) if os.path.isdir(d) else []:
        p = os.path.join(d, n)
        if n.startswith("wf-") and os.path.islink(p) and not any(os.readlink(p).startswith(x) for x in live_dirs) and not _run_alive(os.readlink(p)):
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


FOOTAGE_YTDLP = f"{VV}/tools/ytdlp-venv/bin/python"      # yt-dlp newer than the image's pin (YouTube's player moves)
FOOTAGE_DENO = f"{VV}/tools/deno"                          # the JS runtime yt-dlp's challenge solver needs
FOOTAGE_POT = f"{VV}/tools/bgutil-pot/server"              # the PO-token script yt-dlp's bgutil plugin runs
ENV_FILE = f"{V}/hustler-server/backend/.env"


def seg_durs(words, n):
    """Each segment's spoken length, its trailing pause included, from narrate's words.json rows (seg, start, end)."""
    starts, ends = {}, {}
    for w in words:
        s = w.get("seg")
        if isinstance(s, int):
            starts[s] = min(starts.get(s, w["start"]), w["start"])
            ends[s] = max(ends.get(s, w["end"]), w["end"])
    out = []
    for i in range(n):
        if i not in starts:
            out.append(None)
            continue
        nxt = min((starts[j] for j in starts if j > i), default=ends[i])
        out.append(round(max(nxt, ends[i]) - starts[i], 2))
    return out


def footage(ctx, script, durs=None, W=1080, H=1920, anchors=None):
    """Real footage per line (worker/footage, through footage.seam's workflow scope): supply/footage/ with
    footage.json (clips per line, each with its source URL, channel and time range for the credits) and
    clips/*.mp4. None when VALMERA_FOOTAGE=0 or when the engine could not run (logged, never a stop).
    OpenAI only: the key is read from the backend .env into this one process's environment, never printed."""
    if os.environ.get("VALMERA_FOOTAGE", "1") == "0":
        return None
    out = os.path.join(ctx.supply, "footage")
    rec = os.path.join(out, "footage.json")
    if os.path.exists(rec):
        return json.load(open(rec))
    os.makedirs(out, exist_ok=True)
    sp = os.path.join(ctx.run_dir, "footage_script.json")
    json.dump(script, open(sp, "w"), ensure_ascii=False)
    cmd = [PY, "-m", "footage.run", "--script", sp, "--out", out, "--w", str(W), "--h", str(H)]
    if durs:
        tp = os.path.join(ctx.run_dir, "footage_timing.json")
        json.dump(dict(durs=durs), open(tp, "w"))
        cmd += ["--timing", tp]
    if anchors:                                   # photographs the picture check passed: a person's clips keep that face
        ap = os.path.join(ctx.run_dir, "footage_anchors.json")
        json.dump(anchors, open(ap, "w"))
        cmd += ["--anchors", ap]
    env = dict(os.environ, TMPDIR=ctx.tmp, FOOTAGE_WORK=ctx.tmp, PYTHONPATH=brand.worker_dir(), PYTHONDONTWRITEBYTECODE="1",
               FOOTAGE_FFMPEG=brand.FF, PATH=os.path.dirname(brand.FF) + ":" + os.environ.get("PATH", "/usr/bin:/bin"))
    if os.path.exists(FOOTAGE_YTDLP):
        env["FOOTAGE_YTDLP"] = FOOTAGE_YTDLP
    if os.path.isdir(FOOTAGE_DENO):
        env["FOOTAGE_PATH"] = FOOTAGE_DENO
    if os.path.isdir(FOOTAGE_POT):                # PO tokens, the product's first rung against YouTube's bot wall (worker/ytaccess.pot_args)
        env.setdefault("YTDLP_POT_SERVER_HOME", FOOTAGE_POT)
    if not env.get("OPENAI_API_KEY") and os.path.exists(ENV_FILE):
        for line in open(ENV_FILE):
            if line.startswith("OPENAI_API_KEY="):
                env["OPENAI_API_KEY"] = line.split("=", 1)[1].strip().strip('"').strip("'")
    ctx.log("run footage.run " + " ".join(cmd[3:])[:300])
    try:
        r = subprocess.run(cmd, cwd=brand.worker_dir(), env=env, capture_output=True, text=True, timeout=3600)
        ctx.log_raw(r.stdout[-4000:] + r.stderr[-2000:])
    except subprocess.SubprocessError as e:
        ctx.log(f"footage skipped: {str(e)[:300]}")
        return None
    if r.returncode != 0 or not os.path.exists(rec):
        ctx.log(f"footage skipped: exit {r.returncode}")
        return None
    return json.load(open(rec))


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
    """The film's sound design (worker/score/sound.py): each chosen picture event gets the library recording the
    references would use there (owner, 13 Nov 2026: the references judge the kinds and the amount), its peak on the
    event, at the references' level under the voice, spread over the whole film, checked against the voice. Writes
    l1_sound.json beside the Director's L1 and returns its path; the Director's own L1 when the pass cannot run
    (logged, never a stop)."""
    out = l1_path[:-len(".json")] + "_sound.json"
    if not os.path.exists(out):
        wk = brand.worker_dir()
        try:
            extra = ""
            if (ctx.args or {}).get("song") or wants_music(ctx):     # the film gets music: the sounds are designed for a film with a bed
                extra += " --bed"
            if (ctx.args or {}).get("turn_s") is not None:   # and the story's turn, when the run names it
                extra += f" --turn {float(ctx.args['turn_s']):.3f}"
            sh(ctx, ["zsh", "-c", f"cd '{wk}' && PYTHONPATH=. '{PY}' -m score.sound design --l1 '{l1_path}' --work '{ctx.supply}' --out '{out}'{extra}"],
               timeout=900)
        except (RuntimeError, subprocess.SubprocessError) as e:
            ctx.log(f"sound design skipped: {str(e)[:300]}")
            return l1_path
    return out


def alien_bank(ctx, l1_path):
    """A scene that asks for devices of the engine's alien bank (worker/scene/alien: rhyme, diecut, lamp, arc,
    answer) is laid out before the engine draws it. The asks ride the scene under ``alien``: a style or a
    direction writes them, or the Director's ``alien`` gesture. Returns the path of the scene to draw (the same
    path when it asks for nothing). A device that cannot stand on this film leaves it as it was and says why in
    ``alien_bank.json``; it is never the reason a film does not render."""
    try:
        asks = json.load(open(l1_path)).get("alien")
    except (OSError, ValueError):
        return l1_path
    if not asks or engine_kind() != "scene":
        return l1_path
    out = os.path.splitext(l1_path)[0] + ".alien.json"
    if not os.path.exists(out):
        wk = brand.worker_dir()
        sh(ctx, ["zsh", "-c", f"cd {wk} && PYTHONPATH=..:{SLICE}/pylib {PY} -m scene.alien.run apply --l1 {l1_path} --work {ctx.supply} "
                              f"--out {out}.part --log {os.path.join(ctx.supply, 'alien_bank.json')}"],
           label=f"wf-alien-{ctx.run_id}", heavy=True, timeout=1800)
        os.replace(out + ".part", out)
    return out


def render(ctx, script, sc):
    """The engine draws the L1 film. Always through heavy.sh."""
    out = os.path.join(ctx.supply, "film_engine.mp4")
    kind = engine_kind()
    if not os.path.exists(out):
        if kind == "scene":
            wk = brand.worker_dir()
            sh(ctx, ["zsh", "-c", f"cd {wk} && PYTHONPATH=.. {PY} -m scene.run --local --l1 {alien_bank(ctx, sc['l1'])} --work {ctx.supply} --out {out}.part.mp4"],
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


def wants_music(ctx):
    """Every film gets music (owner, 13 Nov 2026: Valmera chooses it the way the references do) unless the run says
    otherwise: --args '{"music": false}'."""
    return (ctx.args or {}).get("music", True) is not False and ctx.workflow != "selftest"


def music_version(ctx, rd, sc):
    """The same film with music under it, on a copy of the engine's picture (worker/music_film.py: the story's turn
    and payoff read from the film's own lines, the part of the song chosen by the engine, its level ridden under
    every word, a hit before the end card). With a song the owner handed to the run (--args '{"song": path}') that
    song plays and the film is delivered as film_song.mp4 beside the clean film.mp4 (a test song is never the film).
    Otherwise Valmera chooses a track from its licensed library: that film is film.mp4, with its credit line in
    film.json, and the export without music is film_clean.mp4. A fault here never stops a run: the clean film is
    delivered and the reason is logged."""
    song = (ctx.args or {}).get("song")
    if song and not os.path.exists(str(song)):
        song = None
    if not sc.get("l1") or not (song or wants_music(ctx)):
        return None
    tag = "song" if song else "music"
    out = os.path.join(ctx.supply, f"film_engine_{tag}.mp4")
    report = os.path.join(ctx.supply, f"{tag}.json")
    if not (os.path.exists(out) and os.path.exists(report)):
        wk = brand.worker_dir()
        brief = ""
        try:
            sj = json.load(open(os.path.join(ctx.run_dir, "script.json")))
            brief = " ".join(str(sj.get(k) or "") for k in ("title", "hook_line", "direction"))[:500]
        except (OSError, ValueError):
            pass
        bf = os.path.join(ctx.supply, f"{tag}_brief.txt")
        open(bf, "w", encoding="utf-8").write(brief)
        # a narrated film's voice is not an upload: its workflow hands the scene with the narration as its spine (l1_audio)
        cmd = (f"cd '{wk}' && export OPENAI_API_KEY=\"${{OPENAI_API_KEY:-$(grep -E '^OPENAI_API_KEY=' '{ENV_FILE}' | tail -1 | cut -d= -f2- | tr -d '\"')}}\" && "
               f"PYTHONPATH=. FFMPEG_BIN='{brand.FF}' SCENE_CEIL_DBTP=-2.0 SKIP_DB_INIT=1 '{PY}' -m music_film score --l1 '{sc.get('l1_audio') or sc['l1']}' "
               f"--work '{ctx.supply}' --film '{rd['film']}' --out '{out}.part.mp4' --kind {SONG_KIND.get(ctx.workflow, 'stories')} "
               f"--cache '{os.path.join(ctx.state_root, 'music_cache')}' --report '{report}' --brief \"$(cat '{bf}')\"")    # the publishers' own files, sha256-checked, kept between runs
        if song:
            cmd += f" --song '{song}'"
            if (ctx.args or {}).get("song_vocals"):
                cmd += f" --vocals '{ctx.args['song_vocals']}'"
        turn = (ctx.args or {}).get("turn_s", sc.get("turn_s"))       # the run's own word first, else the landing the score named: the drop goes on it
        if turn is not None:
            cmd += f" --payoff {float(turn):.3f}"
        # films made together (a podcast's shorts) share a list of the tracks already taken, so no two play the same one:
        # --args '{"music_ledger": "<file>"}'. The choice is made under the file's lock, one film at a time.
        ledger, lock, used = (ctx.args or {}).get("music_ledger") if not song else None, None, []
        try:
            if ledger:
                import fcntl
                lock = open(ledger + ".lock", "a")
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    used = [str(x) for x in json.load(open(ledger))]
                except (OSError, ValueError):
                    used = []
            if used:                                     # the same call as the command line, with the tracks to leave out
                aj, hp = os.path.join(ctx.supply, f"{tag}_args.json"), os.path.join(ctx.supply, f"{tag}_used.py")
                json.dump(dict(l1=sc.get("l1_audio") or sc["l1"], work=ctx.supply, film=rd["film"], out=out + ".part.mp4", kind=SONG_KIND.get(ctx.workflow, "stories"),
                               cache=os.path.join(ctx.state_root, "music_cache"), report=report, brief=brief,
                               payoff=float(turn) if turn is not None else None, used=used), open(aj, "w"), ensure_ascii=False)
                open(hp, "w").write(MUSIC_USED)
                cmd = cmd[:cmd.index(" -m music_film score ")] + f" '{hp}' '{aj}'"
            sh(ctx, ["zsh", "-c", cmd], label=f"wf-{tag}-{ctx.run_id}", heavy=True, timeout=1800)
            os.replace(out + ".part.mp4", out)
            if ledger:
                try:
                    cid = (json.load(open(report)).get("choice") or {}).get("cid")
                    if cid and str(cid) not in used:
                        json.dump(used + [str(cid)], open(ledger, "w"))
                except (OSError, ValueError):
                    pass
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            ctx.log(f"{tag} version skipped: {str(e)[:300]}")
            return None
        finally:
            if lock:
                lock.close()
    try:
        rep = json.load(open(report))
    except (OSError, ValueError):
        rep = {}
    final = os.path.join(ctx.run_dir, f"final_{tag}.mp4")
    if not os.path.exists(final):
        try:
            brand.finish(out, final + ".part.mp4", ctx.tmp)
            os.replace(final + ".part.mp4", final)
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:     # the clean film is delivered either way
            ctx.log(f"{tag} version not finished: {str(e)[:300]}")
            return None
    mix = rep.get("mix") or {}
    plan, levels = mix.get("plan") or {}, mix.get("music") or {}
    does = dict(marks=rep.get("marks"), drop_at=plan.get("drop_at"), build_at=plan.get("build_at"), end_hit_s=plan.get("end_hit_s"), jumps=plan.get("jumps"),
                tempo=plan.get("tempo"), cuts=plan.get("cuts"), under_voice_db=levels.get("under_voice_db"), lifts=len(levels.get("lifts") or []),
                accents=len(levels.get("accents") or []), lufs=(mix.get("master") or {}).get("lufs"))
    if rep.get("chapters"):                      # a long film: a track a chapter, each opening on an entrance and closing on a hit
        does["chapters"] = [dict(film=c.get("film"), title=(c.get("choice") or {}).get("title"), end_hit_s=c.get("end_hit_s"), why=c.get("why")) for c in rep["chapters"]]
    spend = [dict(stage="check", provider="openai", model="gpt-6.1-sol", what="music: story marks and the pick", usd=float(rep.get("spend_usd") or 0))]
    if song:
        return dict(final_song=final, song=dict(file=os.path.basename(str(song)), supplied_by="owner", picked_by_valmera=False, does=does), spend=spend)
    ch = rep.get("choice") or {}
    try:
        chk = brand.check(out, final, ctx.tmp)
    except (RuntimeError, OSError, subprocess.SubprocessError) as e:
        ctx.log(f"music version not checked: {str(e)[:300]}")
        return None
    if not (chk.get("length_ok") and chk.get("endcard_ok")):
        ctx.log(f"music version not delivered: its finish does not hold ({ {k: chk.get(k) for k in ('length_ok', 'endcard_ok', 'mark_ok', 'lufs')} })")
        return None
    music = dict(picked_by_valmera=True, title=ch.get("title"), author=ch.get("author"), license=ch.get("license"), credit=ch.get("credit"),
                 page_url=ch.get("page_url"), cid=ch.get("cid"), why=ch.get("why"), chosen_by=ch.get("chosen_by"), tried=rep.get("tried"), does=does,
                 tracks=ch.get("tracks"),
                 checks={k: chk.get(k) for k in ("length_ok", "endcard_ok", "mark_ok", "lufs", "loud_ok")}, removable=True,
                 note="CC-BY: the credit line goes wherever the film is published; film_clean.mp4 is the same film without music")
    return dict(final_music=final, music=music, spend=spend)


SONG_KIND = {"wf1": "podcast", "wf2": "marketing", "wf3": "stories", "wf4": "longform"}
# music_film's own command line, plus the tracks a run of films has already taken (its `used`)
MUSIC_USED = '''import json
import sys

import music_film as M

a = json.load(open(sys.argv[1]))
try:
    rep = M.score(json.load(open(a["l1"])), a["work"], film=a["film"], out=a["out"], kind=a["kind"], payoff_s=a.get("payoff"), cache=a["cache"],
                  brief=a["brief"], call=M.openai_call(), used=a["used"])
except Exception as ex:  # noqa: BLE001 - the clean film is delivered either way; the reason is on file
    rep = {"ok": False, "why": f"{type(ex).__name__}: {str(ex)[:300]}"}
json.dump(rep, open(a["report"], "w", encoding="utf-8"), default=float, ensure_ascii=False, indent=1)
'''


def sound_manifest():
    """The sound library of the worker this run designs and draws with (its sounds are chosen from that library, so a
    checkout that lags it would call a new recording foreign); the ops checkout only when that worker has none."""
    p = os.path.join(brand.worker_dir(), "sound_library", "manifest.json")
    return p if os.path.exists(p) else SOUNDS


def no_music(l1_path):
    """Every sound in the plan is a recording of the library (approved by the owner's ear or by reference match):
    nothing foreign, nothing synthesized, and no audio asset outside the library. The plan itself carries no music: the
    film's music is laid on a copy after the render (music_version), so the clean export is exactly this plan."""
    lib = {s["file"] for s in json.load(open(sound_manifest()))}
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
    mv = music_version(ctx, rd, sc)              # the film's music: Valmera's choice, or the song the owner handed this run
    if mv:
        out.update(mv)
    return out


def cleanup_dir(path):
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path, ignore_errors=True)
