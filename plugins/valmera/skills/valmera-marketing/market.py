#!/usr/bin/env python3
"""valmera-marketing: one command, one finished marketing film for editors.

    python3 market.py                      the run finds the editor's pain or technique by itself
    python3 market.py captions that pop    a few words: the film is about them
    python3 market.py --out <folder> ...   deliver somewhere else (default: /Users/masaoodi/Documents/Valmera/Marketing Films)

That is the whole input. The run (proof.py on the workflow shell) does everything else and can be started again with the
same words to continue where it stopped:

  ideas     the editor's pain or technique, found from what editors answered to (the marketing references) and the clips
            the owner works from; the clip, its seconds and the sentence to type
  script    REAL output first: the clip is uploaded to a local stack of the product (the Gauntlet's runner; never a
            customer's project), the sentence typed, the export downloaded; a vision model checks the export against the
            sentence and a sentence whose result is not visible is never shown; then the narration, every claim audited
            against the recording's receipts
  supply    the narrator (Phonon Easton), real footage per line, the before and the after cut in with their own sound
  score     the story reel builder at a pace set from this film's own narration against the references' medians; every
            picture checked against the exact words it sits under (gpt-6-sol, low), a misfit dropped and the cut rebuilt
  render    Valmera's engine, the shell's brand finish and the music it chooses (film.mp4) and the film without it
  deliver   once, complete: <out>/<date>-wf2-<style>-<title>-<id>/ with film.mp4, film_clean.mp4, film.json (receipts,
            credits, measures) and SOURCES.md. The run's work folder, the recording and its stack are deleted.

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
if os.path.exists(PY) and os.path.realpath(sys.executable) != os.path.realpath(PY) and not os.environ.get("MARKET_NO_REEXEC"):
    os.execv(PY, [PY, os.path.realpath(__file__)] + sys.argv[1:])

HERE = os.path.dirname(os.path.realpath(__file__))      # through the Claude Code link (codex/) too
ENV_FILE = f"{V}/hustler-server/backend/.env"
OUT_ROOT = os.environ.get("MARKET_OUT_ROOT", f"{V}/Marketing Films")
STATE_ROOT = os.environ.get("MARKET_STATE_ROOT", f"{VV}/workflow/marketing")     # its own work root: a start sweeps only its own runs
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
    env.update(WRITER_PROVIDER="openai", SHELL_LLM="openai", SHELL_STATE_ROOT=STATE_ROOT, PYTHONDONTWRITEBYTECODE="1", WF2_ENGINE="proof")
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
        with open(led, "w") as f:
            json.dump({"wf2": []}, f, ensure_ascii=False, indent=1)
    mc, smc = os.path.join(STATE_ROOT, "music_cache"), os.path.join(SHARED, "music_cache")
    if not os.path.lexists(mc) and os.path.isdir(smc):
        os.symlink(smc, mc)


def stopped_run(topic):
    """A stopped run of the same words (none: a new run)."""
    work = os.path.join(STATE_ROOT, "work")
    best = None
    for n in sorted(os.listdir(work)) if os.path.isdir(work) else []:
        st = jload(os.path.join(work, n, "run.json"), {}) or {}
        if st.get("workflow") != "wf2" or alive(st.get("pid")):
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
    rc = ((rec.get("checks") or {}).get("marketing") or {}).get("receipts") or {}
    if rc:
        out += ["", "## The recording (what Valmera really did)", "",
                f"- Typed into the Studio, {rc.get('phase')}: \"{rc.get('typed')}\"",
                f"- Upload: {(rc.get('upload') or {}).get('seconds')} s, {(rc.get('upload') or {}).get('w')}x{(rc.get('upload') or {}).get('h')}, sha256 {(rc.get('upload') or {}).get('sha256')}",
                f"- Export: {(rc.get('export') or {}).get('seconds')} s, {(rc.get('export') or {}).get('w')}x{(rc.get('export') or {}).get('h')}, sha256 {(rc.get('export') or {}).get('sha256')}",
                f"- Product: {json.dumps(rc.get('product'))}; source clip {json.dumps(rc.get('source'))}"]
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
    log = os.path.join(STATE_ROOT, f"market-{dt.datetime.now():%Y%m%d-%H%M%S}.log")
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
        mk = (rec.get("checks") or {}).get("marketing") or {}
        os.remove(log) if os.path.exists(log) else None
        print(json.dumps(dict(delivered=folder, title=(rec.get("script") or {}).get("title"), style=(rec.get("style") or {}).get("id"),
                              topic=topic, seconds=round(time.time() - t0), spend_usd=rec.get("spend_usd"), checks_ok=res.get("checks_ok"),
                              measured=st.get("measured"), pictures_fit=mk.get("pictures_fit"), typed=(mk.get("receipts") or {}).get("typed"), music=bool(rec.get("music")), work_empty=res.get("work_empty"),
                              files=sorted(os.listdir(folder))), ensure_ascii=False))
        return 0
    st = (jload(os.path.join(STATE_ROOT, "work", rid, "run.json"), {}) or {}) if rid else {}
    print(json.dumps(dict(stopped=res.get("stopped") or st.get("stopped") or dict(stage=next((s for s in ("ideas", "script", "supply", "score", "render", "check", "deliver")
                                                                                         if s not in (st.get("done") or [])), None), error="the run was interrupted"),
                          run_id=rid, topic=topic, log=log,
                          again="python3 market.py " + (topic or "")), ensure_ascii=False))
    return 2 if rid else 1


if __name__ == "__main__":
    sys.exit(main())
