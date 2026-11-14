#!/usr/bin/env python3
"""valmera-stories: one command, one finished story film.

    python3 story.py                      the run finds a story worth telling by itself
    python3 story.py Claude Shannon       a name, a company or a topic: the run finds the story inside it
    python3 story.py --out <folder> ...   deliver somewhere else (default: /Users/masaoodi/Documents/Valmera/Story Films)

That is the whole input. The run does everything else and can be started again with the same words to continue where
it stopped:

  ideas     the writer model reads the web for true stories (about the words given, or of its own choosing), each with
            the people, places and events a camera filmed; every idea's pictures are probed; a style is chosen for it
            from the story reels (never one of the last three delivered)
  script    written for the ear with every fact quoted from its page; each quote fetched here and found on its page
  supply    the narrator (Phonon 31M Easton, voice.py), real footage per line (worker/footage), free photographs of what
            it names (Commons, Openverse), real voices cut in where the person said it; a person speaking on camera never
            plays under the narrator
  score     the story reel (reel.py): a new picture about every 0.6 s, never the same picture twice, collages, flash
            frames, finishes by section, display words, captions word by word, the judge's camera moves and the
            references' sound design; checked against the taste rules
  render    Valmera's engine, then the shell's brand finish (watermark, end card), the film's music chosen from the
            licensed library (film.mp4) and the same film without it (film_clean.mp4)
  deliver   once, complete: <out>/<date>-wf3-<style>-<title>-<id>/ with film.mp4, film_clean.mp4, film.json (every fact,
            quote and URL, every clip and photograph with its source and licence, the music credit, the measures) and
            SOURCES.md. The run's work folder is deleted.

Never publishes. Model calls run on OpenAI (the key is read from the backend .env, never printed); nothing is spent on
Anthropic. Exit 0 delivered, 2 stopped (run the same command again), 1 failed. The last line of stdout is one JSON object.
"""
import datetime as dt
import json
import os
import subprocess
import sys
import time

V = "/Users/masaoodi/Documents/Valmera"
VV = f"{V}/.valmera"
PY = f"{VV}/venv-worker/bin/python"
if os.path.exists(PY) and os.path.realpath(sys.executable) != os.path.realpath(PY) and not os.environ.get("STORY_NO_REEXEC"):
    os.execv(PY, [PY, os.path.realpath(__file__)] + sys.argv[1:])     # through the Claude skill link to the plugin itself

HERE = os.path.dirname(os.path.realpath(__file__))        # through the Claude skill link to the plugin itself
ENV_FILE = f"{V}/hustler-server/backend/.env"
OUT_ROOT = os.environ.get("STORY_OUT_ROOT", f"{V}/Story Films")
STATE_ROOT = os.environ.get("STORY_STATE_ROOT", f"{VV}/workflow/stories")     # its own work root: a start sweeps only its own runs
SHARED = f"{VV}/workflow"
TRIES = 3


def jload(p, default=None):
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def engines_worker():
    """The worker with this week's engines: the checkout beside this plugin when it has them, else the synced main
    checkout (the installed copy under hustler-server sits beside an older worker; the Codex cache has none)."""
    d = HERE
    for _ in range(6):
        d = os.path.dirname(d)
        w = os.path.join(d, "worker")
        if all(os.path.exists(os.path.join(w, n)) for n in ("broll_source.py", "scene/run.py", "footage/run.py", "music_film.py")):
            return w
    return f"{V}/worktrees/skill-main/worker"


def environment():
    """OpenAI for every model call, Anthropic off; the key read from the backend .env into this process, never printed."""
    env = dict(os.environ)
    w = engines_worker()
    env.setdefault("VALMERA_WORKER", w)
    env.setdefault("VALMERA_SCENE_WORKER", w)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_TRIAL_KEY", None)
    env.update(WRITER_PROVIDER="openai", SHELL_LLM="openai", SHELL_STATE_ROOT=STATE_ROOT, PYTHONDONTWRITEBYTECODE="1")
    if not env.get("OPENAI_API_KEY") and os.path.exists(ENV_FILE):
        for line in open(ENV_FILE):
            if line.startswith("OPENAI_API_KEY="):
                env["OPENAI_API_KEY"] = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not env.get("OPENAI_API_KEY"):
        raise SystemExit(json.dumps(dict(failed="no OPENAI_API_KEY in the environment or the backend .env")))
    return env


def prepare_state():
    """The stories' own state root: the ledger starts with the workflow's delivered stories (never told twice, the last
    three styles rested), and the licensed music cache is the shared one."""
    os.makedirs(os.path.join(STATE_ROOT, "work"), exist_ok=True)
    led = os.path.join(STATE_ROOT, "ledger.json")
    if not os.path.exists(led):
        shared = (jload(os.path.join(SHARED, "ledger.json"), {}) or {}).get("wf3", [])
        with open(led, "w") as f:
            json.dump({"wf3": shared}, f, ensure_ascii=False, indent=1)
    mc, smc = os.path.join(STATE_ROOT, "music_cache"), os.path.join(SHARED, "music_cache")
    if not os.path.lexists(mc) and os.path.isdir(smc):
        os.symlink(smc, mc)


def stopped_run(topic):
    """A stopped run of the same words (none: a new run)."""
    work = os.path.join(STATE_ROOT, "work")
    best = None
    for n in sorted(os.listdir(work)) if os.path.isdir(work) else []:
        st = jload(os.path.join(work, n, "run.json"), {}) or {}
        if st.get("workflow") != "wf3" or alive(st.get("pid")):
            continue
        if (st.get("args") or {}).get("line") == (topic or None) and (st.get("args") or {}).get("one_command"):
            best = n
    return best


def last_json(text):
    for line in reversed((text or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return {}


def sources_md(folder):
    """SOURCES.md beside the film: every fact's page, every clip and photograph with its source and licence, the music."""
    rec = jload(os.path.join(folder, "film.json"), {}) or {}
    sc = (rec.get("script") or {}).get("script") or {}
    st = (rec.get("checks") or {}).get("stories") or {}
    out = [f"# {sc.get('title') or rec.get('idea')}", "", f"Style: {(rec.get('style') or {}).get('name')}. Delivered {rec.get('delivered')}.", "",
           "## Facts (each quote fetched and found on its page)", ""]
    for f in sc.get("facts") or []:
        out.append(f"- {f.get('claim')} - \"{f.get('quote')}\" - {f.get('url')}")
    out += ["", "## Pictures and footage (paste into the post's caption when publishing)", ""]
    seen = set()
    for c in st.get("credits") or []:
        k = (c.get("page"), tuple(c.get("time_range") or []))
        if k in seen:
            continue
        seen.add(k)
        tr = f" {c['time_range'][0]:.1f}-{c['time_range'][1]:.1f} s" if c.get("time_range") else ""
        out.append(f"- {c.get('title') or c.get('name')} ({c.get('credit') or ''}; {c.get('licence') or ''}){tr}: {c.get('page')}")
    mu = rec.get("music") or {}
    if mu:
        out += ["", "## Music", "", f"- {mu.get('credit') or mu.get('title') or json.dumps(mu)[:300]}"]
    with open(os.path.join(folder, "SOURCES.md"), "w") as f:
        f.write("\n".join(out) + "\n")


def main():
    args = sys.argv[1:]
    out_root = OUT_ROOT
    if "--out" in args:
        k = args.index("--out")
        out_root = os.path.abspath(args[k + 1])
        args = args[:k] + args[k + 2:]
    topic = " ".join(args).strip() or None
    env = environment()
    prepare_state()
    os.makedirs(out_root, exist_ok=True)
    run_py = [PY, os.path.join(HERE, "run.py"), "run"]
    outbox = os.path.join(STATE_ROOT, "outbox")         # the shell delivers here; the folder moves to out_root whole, once
    os.makedirs(outbox, exist_ok=True)
    t0 = time.time()
    rid = stopped_run(topic)
    log = os.path.join(STATE_ROOT, f"story-{dt.datetime.now():%Y%m%d-%H%M%S}.log")
    res, last_err = {}, None
    for attempt in range(TRIES + 1):
        cmd = run_py + (["--resume", rid, "--out", outbox] if rid else
                        ["--out", outbox, "--args", json.dumps(dict(line=topic, look=False, one_command=True), ensure_ascii=False)])
        with open(log, "a") as lf:
            r = subprocess.run(cmd, cwd=HERE, env=env, stdout=subprocess.PIPE, stderr=lf, text=True)
            lf.write(r.stdout)
        res = last_json(r.stdout)
        if r.returncode == 0 and res.get("delivered"):
            break
        if r.returncode == 2 and res.get("run_id"):
            rid = res["run_id"]
            err = (res.get("stopped") or {}).get("error")
            print(f"stopped in {(res.get('stopped') or {}).get('stage')}: {str(err)[:200]}; continuing", file=sys.stderr, flush=True)
            if err == last_err and attempt >= 1:
                break                      # the same stop twice: report it, the same command continues later
            last_err = err
            time.sleep(20)
            continue
        rid = rid or stopped_run(topic)            # killed without a word (a signal, a closed laptop): the same words continue it
        break
    if res.get("delivered"):
        staged = res["delivered"]
        try:
            sources_md(staged)
        except Exception as e:  # noqa: BLE001 - the film is delivered; its sources are in film.json
            print(f"SOURCES.md not written: {e!r}", file=sys.stderr)
        folder = os.path.join(out_root, os.path.basename(staged))
        if os.path.exists(folder):
            raise SystemExit(json.dumps(dict(failed=f"{folder} exists: a folder is delivered once", staged=staged)))
        os.rename(staged, folder)                         # complete, in one step: nothing is added after this
        rec = jload(os.path.join(folder, "film.json"), {}) or {}
        st = (rec.get("checks") or {}).get("stories") or {}
        os.remove(log) if os.path.exists(log) else None
        print(json.dumps(dict(delivered=folder, title=(rec.get("script") or {}).get("title"), style=(rec.get("style") or {}).get("id"),
                              topic=topic, seconds=round(time.time() - t0), spend_usd=rec.get("spend_usd"), checks_ok=res.get("checks_ok"),
                              measured=st.get("measured"), music=bool(rec.get("music")), work_empty=res.get("work_empty"),
                              files=sorted(os.listdir(folder))), ensure_ascii=False))
        return 0
    st = (jload(os.path.join(STATE_ROOT, "work", rid, "run.json"), {}) or {}) if rid else {}
    print(json.dumps(dict(stopped=res.get("stopped") or st.get("stopped") or dict(stage=next((s for s in ("ideas", "script", "supply", "score", "render", "check", "deliver")
                                                                                         if s not in (st.get("done") or [])), None), error="the run was interrupted"),
                          run_id=rid, topic=topic, log=log,
                          again="python3 story.py " + (topic or "")), ensure_ascii=False))
    return 2 if rid else 1


if __name__ == "__main__":
    sys.exit(main())
