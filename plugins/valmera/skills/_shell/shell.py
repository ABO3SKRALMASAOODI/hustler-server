#!/usr/bin/env python3
"""The workflow shell: one skeleton for the four Valmera workflow skills.

    zsh .valmera/claude_trial_env.sh $PY shell.py run --workflow wf1 --source <file> --out <deliver root>
    $PY shell.py run --resume <run_id> [--out <deliver root>]     continue a stopped run where it stopped
    $PY shell.py status                                          runs in the work root, ledger tail

Stages, each writing one file into the run folder (README.md has the contract):
  ideas -> script -> supply -> score -> render -> check -> deliver -> cleanup

A workflow is a module with ideas(ctx) and script(ctx, ideas) (and, when it differs from the shared
chain, supply/score/render). The shell owns everything else: the style registry and ledger, the
Mac engine through heavy.sh, the brand finish, delivering once, and deleting the work folder.
Never publishes. The end card and the watermark stay. Every film gets music Valmera chooses from its licensed library
(chain.music_version); the same film without it is delivered beside it.
"""
import argparse
import datetime as dt
import importlib.util
import json
import os
import shutil
import signal
import sys
import time
import traceback
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import chain  # noqa: E402

V = "/Users/masaoodi/Documents/Valmera/.valmera"
STATE_ROOT = os.environ.get("SHELL_STATE_ROOT", f"{V}/workflow")
STAGES = ["ideas", "script", "supply", "score", "render", "check", "deliver"]
CONTRACT = 1


# ----------------------------------------------------------------- small helpers

def now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def jload(p, default=None):
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def jsave(p, obj):
    """Atomic: a killed run never leaves half a stage file."""
    tmp = p + ".part"
    with open(tmp, "w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def du(path):
    total = 0
    if not os.path.exists(path):
        return 0
    if os.path.isfile(path):
        return os.path.getsize(path)
    for root, dirs, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            if not os.path.islink(fp):
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
    return total


def alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def slug(s, n=48):
    out = "".join(c.lower() if c.isalnum() else "-" for c in str(s))
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-")[:n].strip("-") or "film"


# ----------------------------------------------------------------- styles and the ledger

def registry():
    return jload(os.path.join(HERE, "styles.json"))


def ledger_path(state_root):
    return os.path.join(state_root, "ledger.json")


def last_styles(state_root, workflow, n=3):
    rows = (jload(ledger_path(state_root), {}) or {}).get(workflow, [])
    return [r["style"] for r in rows[-n:]]


def allowed_styles(state_root, workflow):
    """The workflow's styles minus its last three delivered ones, registry order."""
    ban = set(last_styles(state_root, workflow))
    return [s for s in registry()[workflow]["styles"] if s["id"] not in ban]


def ledger_add(state_root, workflow, row):
    p = ledger_path(state_root)
    d = jload(p, {}) or {}
    d.setdefault(workflow, []).append(row)
    d[workflow] = d[workflow][-30:]
    jsave(p, d)


def used_windows(state_root, workflow, source):
    rows = (jload(ledger_path(state_root), {}) or {}).get(workflow, [])
    return [dict(window=r.get("window"), title=r.get("idea")) for r in rows if r.get("source") == source]


# ----------------------------------------------------------------- the run

class Ctx:
    """What every stage sees. Paths only inside the run folder, plus the deliver root."""

    def __init__(self, run_dir, state, state_root):
        self.run_dir = run_dir
        self.run_id = state["run_id"]
        self.workflow = state["workflow"]
        self.args = state.get("args", {})
        self.source = state.get("source")
        self.state_root = state_root
        self.sid = f"wf-{self.run_id}"
        self.supply = os.path.join(run_dir, "supply")
        self.tmp = os.path.join(run_dir, "tmp")
        os.makedirs(self.tmp, exist_ok=True)
        self._log = os.path.join(run_dir, "log.txt")

    def log(self, msg):
        line = f"{now()} {msg}"
        with open(self._log, "a") as f:
            f.write(line + "\n")
        print(line, flush=True)

    def log_raw(self, text):
        if text.strip():
            with open(self._log, "a") as f:
                f.write(text.rstrip() + "\n")

    def out(self, stage):
        return jload(os.path.join(self.run_dir, f"{stage}.json"))

    def styles_allowed(self):
        return allowed_styles(self.state_root, self.workflow)

    def styles_last(self):
        return last_styles(self.state_root, self.workflow)

    def windows_used(self):
        return used_windows(self.state_root, self.workflow, self.source)


def load_workflow(name):
    path = name if name.endswith(".py") else os.path.join(HERE, f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"wf_{slug(name)}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sweep(work_root, state_root):
    """Every start: a run folder whose owner is dead is deleted, with its slice link. Live runs stay."""
    gone = []
    os.makedirs(work_root, exist_ok=True)
    live = []
    for n in sorted(os.listdir(work_root)):
        p = os.path.join(work_root, n)
        st = jload(os.path.join(p, "run.json"), {}) or {}
        if os.path.isdir(p) and alive(st.get("pid")) and st.get("pid") != os.getpid():
            live.append(p)
            continue
        if os.path.isdir(p) and not os.path.islink(p):
            shutil.rmtree(p, ignore_errors=True)
        elif os.path.lexists(p):
            os.unlink(p)
        chain.unlink(f"wf-{n}")
        gone.append(n)
    chain.sweep_links(live)
    return gone


def deliver(ctx, state, out_root, results):
    """One folder, complete, written once: <out_root>/<name>/{film.mp4, film.json}. Never overwritten."""
    script, chk = results["script"], results["check"]
    name = state.get("deliver_name") or f"{dt.date.today():%Y%m%d}-{ctx.workflow}-{script['style']['id']}-{slug(script.get('title'))}-{ctx.run_id[-6:]}"
    final_dir = os.path.join(out_root, name)
    if os.path.exists(final_dir):
        rec = jload(os.path.join(final_dir, "film.json"), {}) or {}
        if rec.get("run_id") == ctx.run_id:      # killed after the rename: it was delivered
            return dict(folder=final_dir, bytes=du(final_dir), already=True)
        raise RuntimeError(f"{final_dir} exists: a folder is delivered once, never added to")
    part = os.path.join(out_root, f".{name}.partial")
    shutil.rmtree(part, ignore_errors=True)
    os.makedirs(part)
    scored = bool(chk.get("final_music") and os.path.exists(chk["final_music"]))
    if scored:                                   # the film with the music Valmera chose for it; the same film without music beside it
        shutil.copyfile(chk["final_music"], os.path.join(part, "film.mp4"))
        shutil.copyfile(chk["final"], os.path.join(part, "film_clean.mp4"))
    else:
        shutil.copyfile(chk["final"], os.path.join(part, "film.mp4"))
    if chk.get("final_song") and os.path.exists(chk["final_song"]):      # the owner's song under the same film; film.mp4 is the clean export
        shutil.copyfile(chk["final_song"], os.path.join(part, "film_song.mp4"))
    spend = [s for r in results.values() if isinstance(r, dict) for s in r.get("spend") or []]
    rec = dict(contract=CONTRACT, run_id=ctx.run_id, workflow=ctx.workflow, delivered=now(),
               idea=script.get("idea") or results["ideas"].get("chosen"),
               source=results["ideas"].get("source"), style=script["style"], styles_banned=script.get("styles_banned"),
               window=script.get("window"), script=dict((k, v) for k, v in script.items() if k not in ("style", "idea")),
               engine=results["render"].get("engine"), checks=chk["checks"], stage_seconds=state.get("seconds"),
               spend=spend, spend_usd=round(sum(float(s.get("usd") or 0) for s in spend), 4),
               rules=dict(published=False, music_picked=scored, end_card=chk["checks"].get("endcard_ok"), watermark=chk["checks"].get("mark_ok")))
    if scored:
        rec["music"] = dict(chk.get("music") or {}, film="film.mp4", clean_export="film_clean.mp4")
    if chk.get("song"):
        rec["song"] = dict(chk["song"], film="film_song.mp4", clean_export="film.mp4")
    jsave(os.path.join(part, "film.json"), rec)
    os.rename(part, final_dir)
    ledger_add(ctx.state_root, ctx.workflow, dict(run_id=ctx.run_id, style=script["style"]["id"], idea=script.get("title"),
                                                  source=ctx.source, window=script.get("window"), delivered=final_dir, at=now()))
    return dict(folder=final_dir, bytes=du(final_dir), spend_usd=rec["spend_usd"])


def run(a):
    state_root = a.state_root
    work_root = os.path.join(state_root, "work")
    os.makedirs(work_root, exist_ok=True)
    before = du(work_root)
    if a.resume:
        run_dir = os.path.join(work_root, a.resume)
        state = jload(os.path.join(run_dir, "run.json"))
        if not state:
            raise SystemExit(f"no stopped run {a.resume} in {work_root}")
        if alive(state.get("pid")) and state["pid"] != os.getpid():
            raise SystemExit(f"run {a.resume} is still running (pid {state['pid']})")
        swept = []
    else:
        swept = sweep(work_root, state_root)
        rid = f"{dt.datetime.now():%m%d%H%M}-{uuid.uuid4().hex[:6]}"
        run_dir = os.path.join(work_root, rid)
        os.makedirs(run_dir)
        state = dict(run_id=rid, workflow=a.workflow, source=os.path.abspath(a.source) if a.source else None,
                     out_root=os.path.abspath(a.out), args=json.loads(a.args or "{}"), started=now(), done=[], seconds={})
    state["pid"] = os.getpid()
    if a.out:
        state["out_root"] = os.path.abspath(a.out)
    jsave(os.path.join(run_dir, "run.json"), state)
    ctx = Ctx(run_dir, state, state_root)
    if swept:
        ctx.log(f"swept stopped runs: {swept}")
    wf = load_workflow(state["workflow"])
    hang = os.environ.get("SHELL_HANG_AFTER")         # the cleanup test's kill point
    results = {s: ctx.out(s) for s in state["done"]}
    defects = []
    try:
        for stage in STAGES:
            if stage in state["done"]:
                continue
            t0 = time.time()
            ctx.log(f"stage {stage}")
            if stage == "ideas":
                r = wf.ideas(ctx)
            elif stage == "script":
                r = wf.script(ctx, results["ideas"])
                r["styles_banned"] = ctx.styles_last()
                if r["style"]["id"] in r["styles_banned"]:
                    raise RuntimeError(f"style {r['style']['id']} repeats one of the last three")
            elif stage == "supply":
                r = getattr(wf, "supply", chain.supply)(ctx, results["script"])
            elif stage == "score":
                r = getattr(wf, "score", chain.score)(ctx, results["script"])
            elif stage == "render":
                r = getattr(wf, "render", chain.render)(ctx, results["script"], results["score"])
            elif stage == "check":
                r = getattr(wf, "check", chain.finish_and_check)(ctx, results["render"], results["score"])
                if not r["checks"].get("ok"):
                    defects.append(dict(stage="check", checks=r["checks"]))
            elif stage == "deliver":
                r = deliver(ctx, state, state["out_root"], results)
            results[stage] = r
            jsave(os.path.join(run_dir, f"{stage}.json"), r)
            state["done"].append(stage)
            state["seconds"][stage] = round(time.time() - t0, 1)
            jsave(os.path.join(run_dir, "run.json"), state)
            if hang == stage:
                ctx.log(f"hanging after {stage} (test)")
                while True:
                    time.sleep(60)
    except Exception as e:  # noqa: BLE001 - a stopped run is kept for --resume and reported
        ctx.log(f"STOPPED in {stage}: {e}\n{traceback.format_exc()[-1500:]}")
        state["pid"] = None
        state["stopped"] = dict(stage=stage, error=str(e)[:600], at=now())
        jsave(os.path.join(run_dir, "run.json"), state)
        print(json.dumps(dict(run_id=state["run_id"], stopped=state["stopped"], resume=f"shell.py run --resume {state['run_id']}")))
        return 2
    # cleanup: the work folder of this run goes, all of it, and the test below fails the run if it didn't
    delivered = results["deliver"]
    chain.unlink(ctx.sid)
    shutil.rmtree(run_dir, ignore_errors=True)
    left = [n for n in os.listdir(work_root) if n == state["run_id"]]
    link_left = os.path.lexists(os.path.join(chain.SLICE, "work", ctx.sid))
    after = du(work_root)
    report = dict(run_id=state["run_id"], delivered=delivered["folder"], delivered_bytes=delivered["bytes"],
                  work_before=before, work_after=after, work_empty=not left and not link_left and not os.path.exists(run_dir),
                  checks_ok=results["check"]["checks"].get("ok"), defects=defects, spend_usd=delivered.get("spend_usd"),
                  stage_seconds=state["seconds"])
    print(json.dumps(report))
    if not report["work_empty"]:
        print(f"FAIL: the work folder is not empty after delivery: {left} link={link_left} {before}->{after} bytes", file=sys.stderr)
        return 3
    return 0


def status(a):
    work_root = os.path.join(a.state_root, "work")
    for n in sorted(os.listdir(work_root)) if os.path.isdir(work_root) else []:
        st = jload(os.path.join(work_root, n, "run.json"), {}) or {}
        print(n, st.get("workflow"), "live" if alive(st.get("pid")) else "stopped", st.get("done"), (st.get("stopped") or {}).get("error", "")[:120])
    led = jload(ledger_path(a.state_root), {}) or {}
    for wf, rows in led.items():
        print(wf, "last styles:", [r["style"] for r in rows[-3:]])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "status"])
    ap.add_argument("--workflow", default="wf1")
    ap.add_argument("--source")
    ap.add_argument("--out")
    ap.add_argument("--resume")
    ap.add_argument("--args", help="JSON handed to the workflow, e.g. a line from the owner")
    ap.add_argument("--state-root", default=STATE_ROOT)
    a = ap.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    if a.cmd == "status":
        return status(a)
    if not a.resume and not a.out:
        ap.error("--out is required for a new run")
    return run(a)


if __name__ == "__main__":
    sys.exit(main() or 0)
