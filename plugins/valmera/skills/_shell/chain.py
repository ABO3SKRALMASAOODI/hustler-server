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
MODELS_DIR = f"{VV}/models"                               # the palette's signal models (RVM, YuNet, Depth Anything V2), this Mac only
TORCH_PY = f"{VV}/venv-narrate/bin/python"                # a Python with torch, for the palette's depth child


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
    env = dict(os.environ, VALMERA_SKILL=ctx.workflow, VALMERA_RUN=ctx.run_id, TMPDIR=ctx.tmp, SLICE_TMP=ctx.tmp, PYTHONDONTWRITEBYTECODE="1",
               PATH=os.path.dirname(brand.FF) + ":" + os.environ.get("PATH", "/usr/bin:/bin"))
    if cmd and cmd[0] == ATLAS_PY:
        # The MLX interpreter must not import worker/coverage.py as numba's
        # third-party coverage package. Preserve other paths, including the
        # local subscription network guard, instead of disabling site startup.
        worker=os.path.realpath(brand.worker_dir())
        env['PYTHONPATH']=os.pathsep.join(p for p in env.get('PYTHONPATH','').split(os.pathsep)
                                        if p and os.path.realpath(p)!=worker)
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

def cut(ctx, src, t0, t1, out, fade_at=None):
    """One window of the source, frame-accurate, H.264 at its own size, audio kept. fade_at (seconds into the cut):
    the sound fades out there over 0.08 s and stays silent to the end while the picture runs on (a clip's breath
    after its last word, when the source's next word would start inside it)."""
    af = ["-af", f"afade=t=out:st={max(float(fade_at), 0.0):.3f}:d=0.08"] if fade_at is not None else []
    subprocess.run([brand.FF, "-v", "error", "-y", "-ss", f"{t0:.3f}", "-i", src, "-t", f"{t1 - t0:.3f}",
                    "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "fast", "-crf", "16",
                    "-pix_fmt", "yuv420p", "-r", "30"] + af + ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", out],
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


def external_hearing(ctx, src, duration):
    """Optional isolated local-ASR transport; bind its word times to these bytes."""
    import hashlib
    import math
    import shlex
    command=os.environ.get('SHELL_HEAR_CMD')
    if not command:return False
    with open(src,'rb') as media:digest=hashlib.file_digest(media,'sha256').hexdigest()
    saved=os.path.join(ctx.supply,'hearing.json')
    prior=json.load(open(saved)) if os.path.exists(saved) else {}
    if (prior.get('transport') or {}).get('source_sha256')==digest:
        response=dict(source_sha256=digest,hearing=prior,engine=prior['transport'].get('engine'))
    else:
        request=dict(source=os.path.abspath(src),source_sha256=digest,duration_s=duration)
        result=subprocess.run(shlex.split(command),input=json.dumps(request),capture_output=True,text=True,timeout=900)
        if result.returncode:raise RuntimeError('external local transcription failed: '+result.stderr[-400:])
        response=json.loads(result.stdout)
    if response.get('source_sha256')!=digest:raise ValueError('transcription source hash mismatch')
    hearing=response.get('hearing') or {}
    previous=-1.
    for line in hearing.get('lines') or []:
        for word in line.get('words') or []:
            a,b=float(word['t0']),float(word['t1'])
            if not word.get('w') or not all(math.isfinite(t) for t in (a,b)) or a<0 or b<a or b>duration+.1 or a<previous:
                raise ValueError('invalid external word timing')
            previous=a
    if previous<0:raise ValueError('external transcription returned no timed words')
    hearing['transport']=dict(source_sha256=digest,engine=response.get('engine'))
    with open(os.path.join(ctx.supply,'hearing.json'),'w') as f:json.dump(hearing,f,ensure_ascii=False)
    return True


def pack(ctx, script):
    """supply/{source.mp4, hearing.json, index.json, session.json}: the window as a session the slice reads."""
    link(ctx)
    w = ctx.supply
    src = os.path.join(w, "source.mp4")
    t0, t1 = float(script["window"][0]), float(script["window"][1])
    if not os.path.exists(os.path.join(w, "index.json")):
        cut(ctx, script["source"], t0, t1, src + ".part.mp4")
        os.replace(src + ".part.mp4", src)
        p = brand.probe(src)
        if not external_hearing(ctx,src,p["dur"]):
            sh(ctx, [ATLAS_PY, "hear.py", ctx.sid], label=f"wf-hear-{ctx.run_id}", heavy=True, timeout=3600)
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
MUSIC_SET = f"{VV}/models/music_ref"                      # the reference music set's folder (index.json + audio), this Mac only
SET_ENV = "VALMERA_MUSIC_SET"                              # worker/music_choose reads the set from this folder


def use_music_set():
    """Point every music chooser this run starts at the reference music set (VALMERA_MUSIC_SET), so the set is chosen
    first on every route: the shell's own command, the SHELL_MUSIC_CMD adapter and the long form's chapter music,
    which inherit this process's environment. An explicit setting wins; without the set's index nothing changes and
    the choosers fall back to the library tracks that passed the gate. Returns the folder in use, or None."""
    cur = os.environ.get(SET_ENV)
    if cur:
        return cur
    if os.path.isfile(os.path.join(MUSIC_SET, "index.json")):
        os.environ[SET_ENV] = MUSIC_SET
        return MUSIC_SET
    return None


SOUND_SET = f"{VV}/models/sfx_ref"                        # the workflow films' sound-effect set (sfx_set.py), this Mac only
SOUND_ENV = "VALMERA_SOUNDS"                               # score.sound and scene.audio read their library from this folder
PLACEMENT_ENV = "VALMERA_SFX_PLACEMENT"                    # score.sound_screen: every sound answers what the picture does


def use_sound_set():
    """Point the run's sound design and its mixer at the workflow films' sound-effect set (VALMERA_SOUNDS) and place
    every sound by what the picture does (VALMERA_SFX_PLACEMENT=screen, worker/score/sound_screen.py). The film build,
    the shell's own sound pass and the render inherit this process's environment. An explicit setting wins; without
    the set's manifest nothing changes and the product library answers as before. Returns the folder in use, or None.
    A customer's film never passes here: the editor keeps the product library and its placement."""
    cur = os.environ.get(SOUND_ENV)
    if cur:
        os.environ.setdefault(PLACEMENT_ENV, "screen")
        return cur
    if not os.path.isfile(os.path.join(SOUND_SET, "manifest.json")):
        return None
    os.environ[SOUND_ENV] = SOUND_SET
    os.environ.setdefault(PLACEMENT_ENV, "screen")
    import sys
    for name in ("score.sound", "scene.audio"):              # a module already loaded read its library folder at import
        m = sys.modules.get(name)
        if m is not None and hasattr(m, "SOUNDS"):
            m.SOUNDS = SOUND_SET
            getattr(m, "_lib", {}).clear()
    return SOUND_SET


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


def footage(ctx, script, durs=None, W=1080, H=1920, anchors=None, preserve_source=False, shot_s=None, line_max=None, per=None,
            out_name="footage", lines=None, avoid=None, strict_lines=False, motion_first=False, alloc=None, reassign_lost=False,
            verify=None, whole_first=False, keep_sources=False, want_voices=False, scout_motion=False, hear_s=None, source_only=False,
            native_pixels=False, look_itself=False):
    """Real footage per line (worker/footage, through footage.seam's workflow scope): supply/footage/ with
    footage.json (clips per line, each with its source URL, channel and time range for the credits) and
    clips/*.mp4. None when VALMERA_FOOTAGE=0 or when the engine could not run (logged, never a stop).
    OpenAI only: the key is read from the backend .env into this one process's environment, never printed.
    shot_s, line_max, per: a caller that cuts faster asks for more, shorter clips a line (footage.run --shot-s,
    --line-max, --per); lines, avoid, out_name: a top-up for some lines only, never cutting again what an earlier
    record (footage.json) holds, into its own folder. strict_lines, motion_first, alloc ("round"), reassign_lost: the
    engine's opt-in flags (a story reel), added to the command only when given. Absent, the call is exactly as before.
    verify=False: the engine's per-clip verify pass is off (FOOTAGE_VERIFY=0; the caller gates every take by its own
    per-line verdicts). whole_first: short sources fetched once, whole with sound (--whole-first). keep_sources: each
    whole source is kept in <supply>/sources with a manifest (FOOTAGE_KEEP_SOURCES), and the manifest's rows are handed
    back as FOOTAGE_LOCAL_SOURCES, so a top-up or a resumed remake never fetches a kept source again; the folder lives in
    the run's supply, which the shell deletes with the run. want_voices: real voices looked for (--want-voices).
    native_pixels (with preserve_source; a caller that frames clips full bleed itself, the podcast shorts): each clip
    keeps its source's own pixels (FOOTAGE_NATIVE_PIXELS=1) instead of being fitted inside the portrait frame.
    look_itself: the engine's look passes only frames of the subject itself or the line's own action, never a studio
    talking about it (FOOTAGE_LOOK_ITSELF=1)."""
    if os.environ.get("VALMERA_FOOTAGE", "1") == "0":
        return None
    out = os.path.join(ctx.supply, out_name)
    rec = os.path.join(out, "footage.json")
    if os.path.exists(rec):
        return json.load(open(rec))
    os.makedirs(out, exist_ok=True)
    sp = os.path.join(ctx.run_dir, "footage_script.json")
    json.dump(script, open(sp, "w"), ensure_ascii=False)
    cmd = [PY, "-m", "footage.run", "--script", sp, "--out", out, "--w", str(W), "--h", str(H)]
    if preserve_source:
        cmd += ["--preserve-source"]
    if shot_s:
        cmd += ["--shot-s", f"{float(shot_s):g}"]
    if line_max:
        cmd += ["--line-max", str(int(line_max))]
    if per:
        cmd += ["--per", str(int(per))]
    if lines:
        cmd += ["--lines", ",".join(str(int(x)) for x in lines)]
    if avoid:
        cmd += ["--avoid"] + [str(x) for x in avoid]
    if strict_lines:
        cmd += ["--strict-lines"]
    if motion_first:
        cmd += ["--motion-first"]
    if scout_motion:
        cmd += ["--scout-motion"]
    if source_only:
        cmd += ["--source-only"]
    if alloc:
        cmd += ["--alloc", str(alloc)]
    if reassign_lost:
        cmd += ["--reassign-lost"]
    if whole_first:
        cmd += ["--whole-first"]
    if hear_s is not None:
        cmd += ["--hear-seconds",str(float(hear_s))]
    if want_voices:
        cmd += ["--want-voices"]
    if durs:
        tp = os.path.join(ctx.run_dir, "footage_timing.json")
        json.dump(dict(durs=durs), open(tp, "w"))
        cmd += ["--timing", tp]
    if anchors:                                   # photographs the picture check passed: a person's clips keep that face
        ap = os.path.join(ctx.run_dir, "footage_anchors.json")
        json.dump(anchors, open(ap, "w"))
        cmd += ["--anchors", ap]
    env = dict(os.environ, VALMERA_SKILL=ctx.workflow, VALMERA_RUN=ctx.run_id, TMPDIR=ctx.tmp, FOOTAGE_WORK=ctx.tmp, PYTHONPATH=brand.worker_dir(), PYTHONDONTWRITEBYTECODE="1",
               FOOTAGE_FFMPEG=brand.FF, PATH=os.path.dirname(brand.FF) + ":" + os.environ.get("PATH", "/usr/bin:/bin"))
    if os.path.exists(FOOTAGE_YTDLP):
        env["FOOTAGE_YTDLP"] = FOOTAGE_YTDLP
    if os.path.isdir(FOOTAGE_DENO):
        env["FOOTAGE_PATH"] = FOOTAGE_DENO
    if os.path.isdir(FOOTAGE_POT):                # PO tokens, the product's first rung against YouTube's bot wall (worker/ytaccess.pot_args)
        env.setdefault("YTDLP_POT_SERVER_HOME", FOOTAGE_POT)
    env.update(footage_env(ctx, verify=verify, keep_sources=keep_sources))
    if native_pixels and preserve_source:
        env["FOOTAGE_NATIVE_PIXELS"] = "1"
    if look_itself:
        env["FOOTAGE_LOOK_ITSELF"] = "1"
    if not env.get("OPENAI_API_KEY") and os.path.exists(ENV_FILE):
        for line in open(ENV_FILE):
            if line.startswith("OPENAI_API_KEY="):
                env["OPENAI_API_KEY"] = line.split("=", 1)[1].strip().strip('"').strip("'")
    ctx.log("run footage.run " + " ".join(cmd[3:])[:300])
    try:
        r = subprocess.run(cmd, cwd=brand.worker_dir(), env=env, capture_output=True, text=True, timeout=3600)
        ctx.log_raw(r.stdout[-4000:] + r.stderr[-2000:])
    except subprocess.SubprocessError as e:
        if os.environ.get('CODEX_PROXY_LOCAL')=='1':
            raise RuntimeError('footage acquisition interrupted; preserve its originals and inspect the failure before resuming') from e
        ctx.log(f"footage skipped: {str(e)[:300]}")
        return None
    if r.returncode != 0 or not os.path.exists(rec):
        if os.environ.get('CODEX_PROXY_LOCAL')=='1':
            raise RuntimeError(f'footage acquisition stopped ({r.returncode}); inspect its logged error before spending quota on replacement research')
        ctx.log(f"footage skipped: exit {r.returncode}")
        return None
    return json.load(open(rec))


def footage_env(ctx, verify=None, keep_sources=False):
    """The footage engine's environment for chain.footage's opt-in kwargs: FOOTAGE_VERIFY=0 when verify is False;
    FOOTAGE_KEEP_SOURCES (<supply>/sources) and FOOTAGE_LOCAL_SOURCES (that folder's manifest rows whose files exist)
    when keep_sources. Paths and URLs only, never a key."""
    env = {}
    if verify is False:
        env["FOOTAGE_VERIFY"] = "0"
    if keep_sources:
        keep = os.path.join(ctx.supply, "sources")
        os.makedirs(keep, exist_ok=True)
        env["FOOTAGE_KEEP_SOURCES"] = keep
        rows = []
        try:
            got = json.load(open(os.path.join(keep, "manifest.json")))
            rows = [dict(url=r["url"], path=r["path"]) for r in got if isinstance(r, dict) and r.get("url") and r.get("path") and os.path.isfile(r["path"])]
        except (OSError, ValueError, TypeError, KeyError):
            rows = []
        env["FOOTAGE_LOCAL_SOURCES"] = json.dumps(rows)
    return env


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


def model_env():
    """The palette's signal models on this Mac, for the worker, which reads their place from its environment only
    (VALMERA_MODELS, VALMERA_TORCH_PY): an explicit setting wins. A shell fragment of assignments."""
    env = {"VALMERA_MODELS": os.environ.get("VALMERA_MODELS") or MODELS_DIR}
    torch_py = os.environ.get("VALMERA_TORCH_PY") or (TORCH_PY if os.path.exists(TORCH_PY) else "")
    if torch_py:
        env["VALMERA_TORCH_PY"] = torch_py
    return " ".join(f"{k}='{v}'" for k, v in env.items())


def lay_devices(ctx, l1_path):
    """The devices a scene asks for are laid out before the engine draws it (worker/scene/palette: the Director's
    per-moment asks under ``devices``, and the alien bank's asks under ``alien``). Every render path of every workflow
    calls this, so whatever the engine can draw is reachable from all four. Returns the path of the scene to draw (the
    same path when it asks for nothing). An ask that cannot stand on this film runs its device's fallback chain; what
    landed for each ask is written to ``devices_log.json`` (and ``alien_bank.json`` for the older field). A fault of
    the palette itself is logged and the film is drawn as scored: devices are never the reason a film does not render."""
    try:
        doc = json.load(open(l1_path))
    except (OSError, ValueError):
        return l1_path
    if not (doc.get("devices") or doc.get("alien")) or engine_kind() != "scene":
        return l1_path
    import glob
    import hashlib
    # Picture composition hears the original speech spine. The later SFX mix is independent.
    shared = doc.get("workflow_text_layout") and getattr(ctx,"workflow",None) == "wf1"
    sound_fields = ("sound","sound_design")
    source = {k:v for k,v in doc.items() if k not in (*sound_fields,"device_events","story_landings")} if shared else doc
    h = hashlib.sha256(json.dumps(source,sort_keys=True).encode())
    wk = brand.worker_dir()
    if shared:
        from placement_check import file_digest
        for entries in ((doc.get("assets") or {}).values(), doc.get("pool") or []):
            for record in entries:
                path = os.path.join(ctx.supply,record.get("path") or "")
                if os.path.isfile(path):
                    h.update(file_digest(path).encode())
        models = os.environ.get("VALMERA_MODELS") or MODELS_DIR
        for variable,name in (("MATTE_RVM_MODEL","rvm_mobilenetv3_fp32.onnx"),
                              ("YUNET_MODEL","face_detection_yunet_2023mar.onnx")):
            path = os.environ.get(variable) or os.path.join(models,name)
            h.update((file_digest(path) if os.path.isfile(path) else "missing").encode())
    for src in sorted(glob.glob(os.path.join(wk, "scene", "palette", "*.py")) +
                      glob.glob(os.path.join(wk, "scene", "alien", "*.py"))):
        h.update(open(src, "rb").read())
    key = h.hexdigest()
    out = os.path.splitext(l1_path)[0] + f".devices.{key[:12]}.json"
    log = os.path.join(ctx.supply, "devices_log.json")
    memo = os.path.join(ctx.supply,"picture-devices-"+key+".json")
    if shared and os.path.isfile(memo):
        with open(memo) as f:
            cached = json.load(f)
        if all(os.path.isfile(path) and file_digest(path)==digest for path,digest in cached["derived"].items()):
            composed = dict(cached["scene"])
            for field in sound_fields:
                if field in doc:
                    composed[field] = doc[field]
                else:
                    composed.pop(field,None)
            with open(out+".part","w") as f:
                json.dump(composed,f)
            os.replace(out+".part",out)
            with open(log,"w") as f:
                json.dump(cached["log"],f)
            return out
    if shared or not os.path.exists(out):
        src = l1_path
        mod = "scene.palette.run"
        if not os.path.isdir(os.path.join(wk, "scene", "palette")):
            # a worker that has not shipped the palette yet lays the alien bank (scene.alien, on every worker since 19 Nov):
            # the Director's asks for the bank's own devices are handed to it, the rest wait for the palette's release
            src, mod = alien_only(doc, l1_path), "scene.alien.run"
        try:
            sh(ctx, ["zsh", "-c", f"cd '{wk}' && {model_env()} PYTHONPATH=..:{SLICE}/pylib FFMPEG_BIN='{brand.FF}' '{PY}' -m {mod} apply "
                                  f"--l1 '{src}' --work '{ctx.supply}' --out '{out}.part' --log '{log}'"],
               label=f"wf-devices-{ctx.run_id}", heavy=True, timeout=3600)
            os.replace(out + ".part", out)
        except (RuntimeError, subprocess.SubprocessError, OSError) as e:
            ctx.log(f"devices could not be laid, the film is drawn as scored: {str(e)[:300]}")
            return l1_path
        if shared and os.path.isfile(log):
            with open(out) as f:
                composed = json.load(f)
            with open(log) as f:
                landings = json.load(f)
            derived = {}
            for aid,asset in composed.get("assets",{}).items():
                if asset == doc.get("assets",{}).get(aid):
                    continue
                path = os.path.join(ctx.supply,asset.get("path") or "")
                if os.path.isfile(path):
                    derived[path] = file_digest(path)
            with open(memo+".part","w") as f:
                json.dump(dict(scene=composed,log=landings,derived=derived),f)
            os.replace(memo+".part",memo)
        if doc.get("alien"):
            try:
                rows = json.load(open(log))
                json.dump([r for r in rows if r.get("dev") in ("rhyme", "diecut", "lamp", "arc", "answer", "arc_stakes")
                           or str(r.get("asked")) in ("rhyme", "diecut", "lamp", "arc", "answer")],
                          open(os.path.join(ctx.supply, "alien_bank.json"), "w"), indent=1)
            except (OSError, ValueError):
                pass
    return out


alien_bank = lay_devices          # the older name: every caller now lays the whole palette

ALIEN_BANK = ("rhyme", "diecut", "lamp", "arc", "answer")


def alien_only(doc, l1_path):
    """The scene for a worker without the palette: its asks for the alien bank's devices (Director's ``devices`` with
    their [a, z] span, and the older ``alien`` field), and no ``devices`` field. Written beside the scene."""
    asks = [dict(a) for a in doc.get("alien") or [] if isinstance(a, dict)]
    for a in doc.get("devices") or []:
        if not isinstance(a, dict) or str(a.get("dev")) not in ALIEN_BANK:
            continue
        a = dict(a)
        if not isinstance(a.get("at"), (list, tuple)):
            f = a.get("f")
            at = a.get("at")
            if isinstance(f, (list, tuple)) and len(f) >= 2:
                a["at"] = [int(f[0]), int(f[1])]
            elif isinstance(at, (int, float)):
                u = a.get("until")
                a["at"] = [int(at), int(u) if isinstance(u, (int, float)) and u > at else int(at) + 30]
            elif isinstance(f, (int, float)):
                a["at"] = [int(f), int(f) + 30]
        if isinstance(a.get("at"), (list, tuple)):
            asks.append(a)
    out = dict({k: v for k, v in doc.items() if k not in ("devices", "alien")}, alien=asks)
    path = os.path.splitext(l1_path)[0] + ".bank.json"
    with open(path, "w") as f:
        json.dump(out, f)
    return path


def hear_devices(ctx, l1, beats, replan, name="l1_heard.json"):
    """The film's sound design hears the devices its Director chose. The scene's asks are laid now (lay_devices: the
    same palette, its matte and depth cached in the supply for the render), the landings the palette reports
    (``device_events``: frame, kind, strength) are copied onto ``l1`` and ``beats`` (the taste check's events), and the
    sound design is planned again on them: ``replan(l1) -> (sounds, report)`` with the film's own turn and words
    (score.sound reads ``device_events``: a tear sounds as a glitch, a world swapped behind the speaker as a landing,
    a soft change not at all). A reel's own device sounds (``device: true``) stay. No device makes a sound itself.
    Returns a record; on any fault the film keeps the sound it has."""
    if not (l1.get("devices") or l1.get("alien")) or engine_kind() != "scene":
        return {}
    try:
        path = os.path.join(ctx.supply, name)
        json.dump({k: v for k, v in l1.items() if k != "device_events"}, open(path, "w"), ensure_ascii=False)
        laid = lay_devices(ctx, path)
        if laid == path:
            return dict(heard=0, why="the devices were not laid")
        composed = json.load(open(laid))
        if "story_cues" in l1:
            l1["story_landings"] = composed.get("story_landings") or []
        ev = [e for e in composed.get("device_events") or [] if isinstance(e, dict) and isinstance(e.get("f"), (int, float))]
    except Exception as e:  # noqa: BLE001 - the film keeps its sound
        ctx.log(f"sound on the devices skipped: {str(e)[:300]}")
        return dict(fault=str(e)[:300])
    if not ev and not l1.get("story_landings") and not l1.get("picture_bound_sound"):  # nothing landed that the sound could hear (a worker without the palette): the first plan stands
        return dict(events=0)
    l1["device_events"] = ev
    if beats is not None:
        beats["device_events"] = ev
        beats["events"] = sorted(set(int(f) for f in beats.get("events") or []) | {int(e["f"]) for e in ev})
    try:
        sounds, rep = replan(composed if l1.get("picture_bound_sound") else l1)
    except Exception as e:  # noqa: BLE001
        sounds, rep = [], {"fault": repr(e)[:300]}
    if rep.get("fault"):
        ctx.log(f"sound on the devices kept the first plan: {rep['fault'][:300]}")
        return dict(events=len(ev), fault=rep["fault"][:300])
    own = [s for s in l1.get("sound") or [] if s.get("device")]
    sounds = [s for s in sounds if not any(abs(int(s.get("event_f", -99)) - int(o.get("event_f", -99))) <= 2 for o in own)]
    l1["sound"] = sorted(own + list(sounds), key=lambda s: (int(s.get("event_f", 0)), str(s.get("id"))))
    l1["sound_design"] = rep
    if beats is not None:
        beats["sound_design"] = rep
    on = {int(e["f"]) for e in ev}
    heard = sorted({int(s["event_f"]) for s in sounds if any(abs(int(s.get("event_f", -99)) - f) <= 2 for f in on)})
    return dict(events=len(ev), sounds=len(l1["sound"]), on_devices=len(heard), frames=heard)


def measure_devices(ctx, film, scene_path):
    """What the drawn film did, by signal, beside the bar (worker/scene/palette/measure.py): cuts, flips, white and
    black frames, the 3 s contour, layers, the device sequence's distance to the recent films. Written to
    ``devices_measure.json`` (film.json carries it). A record only: never a veto, never a stop."""
    try:
        import devices_ledger
        wk = brand.worker_dir()
        sh(ctx, ["zsh", "-c", f"cd '{wk}' && PYTHONPATH=.. FFMPEG_BIN='{brand.FF}' '{PY}' -m scene.palette.measure --film '{film}' "
                              f"--l1 '{scene_path}' --log '{os.path.join(ctx.supply, 'devices_log.json')}' "
                              f"--ledger '{devices_ledger.path()}' --out '{os.path.join(ctx.supply, 'devices_measure.json')}'"], timeout=900)
    except Exception as e:  # noqa: BLE001 - a measure is never the reason a film is not delivered
        getattr(ctx, "log", print)(f"devices measure not written: {str(e)[:200]}")


def render(ctx, script, sc):
    """The engine draws the L1 film. Always through heavy.sh."""
    out = os.path.join(ctx.supply, "film_engine.mp4")
    kind = engine_kind()
    if not os.path.exists(out):
        if kind == "scene":
            wk = brand.worker_dir()
            scene = lay_devices(ctx, sc['l1'])
            sh(ctx, ["zsh", "-c", f"cd {wk} && PYTHONPATH=.. {PY} -m scene.run --local --l1 {scene} --work {ctx.supply} --out {out}.part.mp4"],
               label=f"wf-render-{ctx.run_id}", heavy=True, timeout=7200)
            os.replace(out + ".part.mp4", out)
            measure_devices(ctx, out, scene)
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
    use_music_set()
    tag = "song" if song else "music"
    out = os.path.join(ctx.supply, f"film_engine_{tag}.mp4")
    report = os.path.join(ctx.supply, f"{tag}.json")
    if not (os.path.exists(out) and os.path.exists(report)):
        wk = brand.worker_dir()
        brief = ""
        try:
            sj = json.load(open(os.path.join(ctx.run_dir, "script.json")))
            # the whole direction: its music line is its last sentence (two Cuban shorts, 20 Nov 2026, lost it at 700)
            brief = " ".join(str(sj.get(k) or "") for k in ("title", "hook_line", "direction"))[:1800]
            names = [str(n.get("name") if isinstance(n, dict) else n) for n in (sj.get("names") or [])][:6]
            if names:                                   # who the film is about: the music is chosen for the person too
                brief = f"About: {', '.join(names)}. {brief}"
        except (OSError, ValueError):
            pass
        bf = os.path.join(ctx.supply, f"{tag}_brief.txt")
        open(bf, "w", encoding="utf-8").write(brief)
        # a narrated film's voice is not an upload: its workflow hands the scene with the narration as its spine (l1_audio)
        # the reference music set (owner's Mac only, never in the repository): chosen first; without it the library
        # tracks that passed the signal gate (worker/music_choose.workflow_pools); a failed track never plays
        cmd = (f"cd '{wk}' && export VALMERA_MUSIC_SET=\"${{VALMERA_MUSIC_SET:-{MUSIC_SET}}}\" && export OPENAI_API_KEY=\"${{OPENAI_API_KEY:-$(grep -E '^OPENAI_API_KEY=' '{ENV_FILE}' | tail -1 | cut -d= -f2- | tr -d '\"')}}\" && "
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
        # the story's own turn before the payoff (a podcast short's direction names it): handed with the payoff, the
        # music drops on it when the payoff sits too close to the last hit for a phrase after it (score.music.arrange)
        story_turn = sc.get("story_turn_s") if turn is not None else None
        if story_turn is not None and float(story_turn) < float(turn) - 1.0:
            cmd += f" --turn {float(story_turn):.3f}"
        else:
            story_turn = None
        # the workflow's own opt-in (the podcast shorts: score() returns turn_drop with every payoff): a payoff with no
        # room after it still drops, on the turn named here, else the one music_film reads from the lines, else on the
        # payoff with the last hit moved into the film's tail. Without it the arrangement is today's.
        turn_drop = bool(sc.get("turn_drop")) and turn is not None
        if turn_drop:
            cmd += " --turn-drop"
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
                               payoff=float(turn) if turn is not None else None, turn=float(story_turn) if story_turn is not None else None, turn_drop=turn_drop,
                               used=used), open(aj, "w"), ensure_ascii=False)
                open(hp, "w").write(MUSIC_USED)
                cmd = cmd[:cmd.index(" -m music_film score ")] + f" '{hp}' '{aj}'"
            adapter = os.environ.get("SHELL_MUSIC_CMD")
            if adapter:
                aj = os.path.join(ctx.supply, f"{tag}_args.json")
                json.dump(dict(l1=sc.get("l1_audio") or sc["l1"], work=ctx.supply, film=rd["film"], out=out + ".part.mp4",
                               kind=SONG_KIND.get(ctx.workflow, "stories"), cache=os.path.join(ctx.state_root, "music_cache"), report=report,
                               brief=brief, payoff=float(turn) if turn is not None else None, turn=float(story_turn) if story_turn is not None else None, turn_drop=turn_drop, used=used,
                               song=song, vocals=(ctx.args or {}).get("song_vocals")), open(aj, "w"), ensure_ascii=False)
                sh(ctx, ["zsh", adapter, aj], label=f"wf-{tag}-{ctx.run_id}",
                   heavy=os.environ.get("SHELL_MUSIC_REMOTE") != "1", timeout=1800)
            else:
                sh(ctx, ["zsh", "-c", cmd], label=f"wf-{tag}-{ctx.run_id}", heavy=True, timeout=1800)
            rep = json.load(open(report)) if os.path.exists(report) else {}
            if not rep.get("ok") or not os.path.exists(out + ".part.mp4"):
                raise RuntimeError(json.dumps({k: rep.get(k) for k in ("why", "tried")}, ensure_ascii=False)[:600])
            if ledger:
                from shell import jsave
                cid = (rep.get("choice") or {}).get("cid")
                if cid and str(cid) not in used:
                    jsave(ledger, used + [str(cid)])
            os.replace(out + ".part.mp4", out)
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
            bed = (rep.get("mix") or {}).get("card_bed")
            if bed and not os.path.isfile(bed["path"]):
                # A remote mix can name its own music cache. Resolve the same licensed recording locally.
                library = use_music_set() or MUSIC_SET
                choice = rep.get("choice") or {}
                if song:
                    bed = dict(bed,path=song)
                elif choice.get("cid") and os.path.isfile(os.path.join(library,"index.json")):
                    with open(os.path.join(library,"index.json")) as f:
                        track = next((r for r in json.load(f)["tracks"] if r["cid"] == choice["cid"]),None)
                    if track:
                        bed = dict(bed,path=os.path.join(library,track["file"]))
            if bed and ctx.workflow == "wf3":
                bed = dict(bed, speech_room_db=9.)
            brand.finish(out, final + ".part.mp4", ctx.tmp, card_bed=bed,
                         last_word_s=last_word(ctx, sc), **end_accent(ctx, sc))
            os.replace(final + ".part.mp4", final)
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:     # the clean film is delivered either way
            ctx.log(f"{tag} version not finished: {str(e)[:300]}")
            return None
    mix = rep.get("mix") or {}
    plan, levels = mix.get("plan") or {}, mix.get("music") or {}
    does = dict(marks=rep.get("marks"), drop_at=plan.get("drop_at"), build_at=plan.get("build_at"), end_hit_s=plan.get("end_hit_s"), jumps=plan.get("jumps"),
                tempo=plan.get("tempo"), cuts=plan.get("cuts"), payoff_flow_s=levels.get("payoff_flow_s"),
                holes=levels.get("holes"), hits=levels.get("hits"), under_voice_db=levels.get("under_voice_db"), lifts=len(levels.get("lifts") or []),
                accents=len(levels.get("accents") or []), lufs=(mix.get("master") or {}).get("lufs"))
    if "opening_bed_dbfs" in mix:
        does["opening_bed_dbfs"] = mix["opening_bed_dbfs"]
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
    if mix.get("card_bed"):
        tail = brand.tail_shape(final, chk["programme_s"], sc.get("last_word_s"), chk["outro_s"])
        does["encoded_card_bed"] = {k:tail[k] for k in ("card_last250_dbfs","card_quarters_dbfs")}
        clean = os.path.join(ctx.run_dir, "final.mp4")
        if os.path.isfile(clean):
            does["encoded_card_bed"].update(brand.encoded_card_score(final, clean, chk["programme_s"], chk["outro_s"]))
    music = dict(picked_by_valmera=True, title=ch.get("title"), author=ch.get("author"), license=ch.get("license"), credit=ch.get("credit"),
                 page_url=ch.get("page_url"), cid=ch.get("cid"), why=ch.get("why"), chosen_by=ch.get("chosen_by"), tried=rep.get("tried"), does=does,
                 tracks=ch.get("tracks"), pool=ch.get("pool"), entry=ch.get("entry"),
                 checks={k: chk.get(k) for k in ("length_ok", "endcard_ok", "mark_ok", "lufs", "lra", "true_peak_dbtp", "loud_ok")}, removable=True,
                 note="CC-BY: the credit line goes wherever the film is published; film_clean.mp4 is the same film without music")
    return dict(final_music=final, music=music, spend=spend)


SONG_KIND = {"wf1": "podcast", "wf2": "marketing", "wf3": "stories", "wf4": "longform"}
# music_film's own command line, plus the tracks a run of films has already taken (its `used`)
MUSIC_USED = '''import json
import sys

import music_film as M

a = json.load(open(sys.argv[1]))
try:
    rep = M.score(json.load(open(a["l1"])), a["work"], film=a["film"], out=a["out"], kind=a["kind"], payoff_s=a.get("payoff"), turn_s=a.get("turn"), cache=a["cache"],
                  brief=a["brief"], call=M.openai_call(), used=a["used"], turn_drop=bool(a.get("turn_drop")))
except Exception as ex:  # noqa: BLE001 - the clean film is delivered either way; the reason is on file
    rep = {"ok": False, "why": f"{type(ex).__name__}: {str(ex)[:300]}"}
json.dump(rep, open(a["report"], "w", encoding="utf-8"), default=float, ensure_ascii=False, indent=1)
'''


def sound_manifest():
    """The sound library of the worker this run designs and draws with (its sounds are chosen from that library, so a
    checkout that lags it would call a new recording foreign); the ops checkout only when that worker has none. The
    workflow films' own set (use_sound_set) when the run uses it."""
    own = os.environ.get(SOUND_ENV)
    if own and os.path.isfile(os.path.join(own, "manifest.json")):
        return os.path.join(own, "manifest.json")
    p = os.path.join(brand.worker_dir(), "sound_library", "manifest.json")
    return p if os.path.exists(p) else SOUNDS


def no_music(l1_path):
    """Every sound in the plan is a recording of the library (approved by the owner's ear or by reference match):
    nothing foreign, nothing synthesized, and no audio asset outside the library. The plan itself carries no music: the
    film's music is laid on a copy after the render (music_version), so the clean export is exactly this plan."""
    lib = {s["file"] for s in json.load(open(sound_manifest()))}
    own = os.path.join(brand.worker_dir(), "sound_library", "manifest.json")
    if os.environ.get(SOUND_ENV) and os.path.exists(own):
        # a run switched to the workflow set keeps what its plan placed on the product library before the switch: the
        # mixer plays a recording the set does not carry from the product library (scene.audio), so it is not foreign
        try:
            lib |= {s["file"] for s in json.load(open(own))}
        except (OSError, ValueError, KeyError, TypeError):
            pass
    l1 = json.load(open(l1_path))
    bad = [s.get("file") for s in l1.get("sound") or [] if s.get("file") and s["file"] not in lib]
    audio_assets = [k for k, a in (l1.get("assets") or {}).items() if a.get("kind") == "audio"]
    return dict(sounds=len(l1.get("sound") or []), foreign_sounds=bad, audio_assets=audio_assets, ok=not bad and not audio_assets)


STING_ON = False      # owner, 15 Nov: off until the owner hears the reference-matched sting (lane STING-REF demo)


def end_accent(ctx, sc):
    """The brand finish's end accent (a sting on the card's reveal, the film's tail ringing under the card) for the
    workflows that close on the product's card as a signature: podcast shorts, stories, long-form. The marketing film
    sells Valmera inside the film and designs its own close, so it keeps the plain finish. `used`: the library
    recordings the film's own sound design already plays, so the sting is never one of them."""
    # OFF (owner, 15 Nov): the first sting (impact_10, chosen by a "rings 1.4 s or more" rule) sounded like a rock fall
    # under the card. brand.pick_sting now chooses against the reference reels' own end and transition sounds (lane
    # STING-REF, never a flagged recording); the owner hears it before it goes on. Until then every workflow keeps the
    # plain finish.
    if getattr(ctx, "workflow", None) == "wf2" or not STING_ON:
        return dict(accent=False)
    used = []
    try:
        used = [s["file"] for s in json.load(open(sc["l1"])).get("sound") or [] if s.get("file")]
    except (KeyError, TypeError, OSError, ValueError):
        pass
    return dict(accent=True, used=used)


def last_word(ctx, sc):
    """The film's last word (programme seconds): the score's own word times when it carries them (a take that runs on
    past its last word has room tone the voice track would read as speech), else its voice track (the narration's
    spine for a narrated film). The brand finish holds the sound about 1.2 s past it, then lets it fall as the
    references' music falls."""
    if sc.get("last_word_s") is not None:
        return float(sc["last_word_s"])
    l1 = sc.get("l1_audio") or sc.get("l1")
    if not l1 or not os.path.exists(l1):
        return None
    return brand.last_word(l1, ctx.supply)


def finish_and_check(ctx, rd, sc):
    """Brand finish, then the measured checks. A failed check is reported, never a stop."""
    final = os.path.join(ctx.run_dir, "final.mp4")
    if not os.path.exists(final):
        info = brand.finish(rd["film"], final + ".part.mp4", ctx.tmp, last_word_s=last_word(ctx, sc), **end_accent(ctx, sc))
        os.replace(final + ".part.mp4", final)
    else:
        info = {}
    c = brand.check(rd["film"], final, ctx.tmp)
    if not info:
        # A resumed run still needs the measured programme boundary. Returning
        # an empty finish record makes downstream audits treat the card as
        # speech time (or fail), despite the cached film being valid.
        info = dict(programme_s=c["programme_s"], outro_s=c["outro_s"], reused=True)
    m = no_music(sc["l1"]) if sc.get("l1") and os.path.exists(sc["l1"]) else dict(ok=True, note="no plan")
    c.update(music=m, finish=info)
    c["ok"] = bool(c["ok"] and m["ok"])
    out = dict(final=final, checks=c)
    mv = music_version(ctx, rd, sc)              # the film's music: Valmera's choice, or the song the owner handed this run
    if not mv and ctx.workflow == "wf1" and sc.get("l1") and wants_music(ctx):
        ctx.log("Resuming the podcast music finish from the current score")
        mv = music_version(ctx, rd, sc)
    if mv:
        out.update(mv)
    elif ctx.workflow == "wf3" and (ctx.args or {}).get("one_command") and wants_music(ctx):
        report = os.path.join(ctx.supply, "music.json")
        try:
            reason = json.load(open(report))
        except (OSError, ValueError):
            reason = {"why": "music generation produced no report"}
        raise RuntimeError("required story music was not delivered: " +
                           json.dumps({k: reason.get(k) for k in ("why", "tried")}, ensure_ascii=False)[:500])
    return out


def cleanup_dir(path):
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path, ignore_errors=True)
