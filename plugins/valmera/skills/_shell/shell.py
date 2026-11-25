#!/usr/bin/env python3
"""The workflow shell: one skeleton for the four Valmera workflow skills.

    zsh .valmera/claude_trial_env.sh $PY shell.py run --workflow wf1 --source <file> --out <deliver root>
    $PY shell.py run --resume <run_id> [--out <deliver root>]     continue a stopped run where it stopped
    $PY shell.py run ... --until script                          stop after that stage: its file is kept, nothing delivered
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
import delivered as DL  # noqa: E402
import platform_song  # noqa: E402

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
        import openai_cache
        openai_cache.workflow(self.workflow, self.run_id)
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
            if ledger_mode(ctx, final_dir) == "shell" and DL.records(final_dir):     # and recorded (a row of this run is never added twice)
                DL.append(delivered_row(ctx, script, results, final_dir), refuse=False)
            return dict(folder=final_dir, bytes=du(final_dir), already=True)
        raise RuntimeError(f"{final_dir} exists: a folder is delivered once, never added to")
    if ctx.workflow == "wf3" and chk.get("final_music") and results.get("score", {}).get("l1"):
        # A resume can enter here directly from a saved check, including an older unbound check.
        rd, sc = results["render"], results["score"]
        active_rd = jload(os.path.join(ctx.run_dir, "repair_render.json")) or rd
        active_sc = jload(os.path.join(ctx.run_dir, "repair_score.json")) or sc
        revision = (chk.get("music") or {}).get("revision") or {}
        files = dict(mix=os.path.join(ctx.supply, "film_engine_music.mp4"), receipt=os.path.join(ctx.supply, "music.json"),
                     final=chk["final_music"], clean=chk["final"])
        current = revision.get("inputs") == chain.music_inputs(ctx, active_rd, active_sc)
        if not current or any(not revision.get(k) or revision[k] != chain.file_sha(path) for k, path in files.items()):
            chk = results["check"] = load_workflow(ctx.workflow).check(ctx, rd, sc)
            jsave(os.path.join(ctx.run_dir, "check.json"), chk)
    # the shared delivered ledger (delivered.py): a source delivered before is refused before anything is copied
    # Only a folder inside the owner's delivery folders is a delivery; anywhere else the run is a test, never recorded.
    mode = ledger_mode(ctx, final_dir)
    if mode == "off" and DL.in_delivery_root(out_root):
        raise RuntimeError(f"{out_root} is a delivery folder: a run outside the delivered ledger (a test, an inner short) never writes there")
    row = delivered_row(ctx, script, results, final_dir) if mode == "shell" and DL.records(final_dir) else None
    if row:
        if row.get("remake_of") and not row.get("judged"):
            raise DL.NotJudged(f"run {ctx.run_id} re-makes delivered run {row['remake_of']} without the repeat judgement: a "
                               "remake is a test (an --out outside the delivery folders) or goes through the judgement like any idea")
        hit, sid = DL.find(row["source_ids"], ctx.workflow)
        if hit:
            raise DL.AlreadyDelivered(f"{sid} was delivered on {hit.get('delivered_at')} to {hit.get('folder')}: the exact same source is never delivered twice", hit, sid)
    part = os.path.join(out_root, f".{name}.partial")
    shutil.rmtree(part, ignore_errors=True)
    os.makedirs(part)
    music_in_final = bool(chk.get("music_in_final"))
    scored = music_in_final or bool(chk.get("final_music") and os.path.exists(chk["final_music"]))
    if scored and not music_in_final:             # an optional separate score, with its clean export
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
               rules=dict(published=False, music_picked=scored, end_card=chk["checks"].get("endcard_ok"), watermark=chk["checks"].get("mark_ok")),
               no_repeat=(results.get("ideas") or {}).get("no_repeat"))
    if scored:
        rec["music"] = dict(chk.get("music") or {}, film="film.mp4")
        if not music_in_final:
            rec["music"]["clean_export"] = "film_clean.mp4"
    if chk.get("song"):
        rec["song"] = dict(chk["song"], film="film_song.mp4", clean_export="film.mp4")
    # The song the film is posted with (owner, 15 Nov 2026): Valmera chooses it from what the judge's reels play, the
    # publisher adds it from the platform's own catalog to the film without our music bed. A film whose music is baked
    # in has no clean export to post; a song the owner handed over is his choice already.
    upload = None if music_in_final or chk.get("song") else ("film_clean.mp4" if scored else "film.mp4")
    if upload and wants_platform_song(ctx):
        pm, pspend = platform_song.choose_safely(ctx.workflow, script, (chk.get("checks") or {}).get("final_s"), upload,
                                                 state_root=ctx.state_root, log=ctx.log, film_music=chk.get("music") if scored else None)
        if pm:
            jsave(os.path.join(part, "platform_music.json"), pm)
            rec["platform_music"] = pm
            rec["spend"] = spend + pspend
            rec["spend_usd"] = round(sum(float(s.get("usd") or 0) for s in rec["spend"]), 4)
    dlog, ddir = jload(os.path.join(ctx.supply, "devices_log.json"), None), jload(os.path.join(ctx.supply, "director.json"), None)
    if dlog or ddir:                             # what the Director chose and what landed, with the signal it measured
        rec["devices"] = dict(by=(ddir or {}).get("by"), why=(ddir or {}).get("why"), repairs=len((ddir or {}).get("repairs") or []),
                              asked=len(dlog or []), landed=sum(1 for r in dlog or [] if r.get("done") and not r.get("fallback")),
                              fell_back=sum(1 for r in dlog or [] if r.get("fallback")),
                              sections=(ddir or {}).get("sections"), log=dlog,
                              measure=jload(os.path.join(ctx.supply, "devices_measure.json"), None))
    if ctx.workflow == "wf4":
        import wf4_sources
        wf4_sources.write(part, ctx.supply, results)
    jsave(os.path.join(part, "film.json"), rec)
    if row:                                      # the rename and the ledger row together, under the ledger's lock
        DL.deliver_once(row, commit=lambda: os.rename(part, final_dir))
    else:                                        # a proven "driver": the skill's driver records it where the folder lands; else a test
        os.rename(part, final_dir)
    ledger_add(ctx.state_root, ctx.workflow, dict(run_id=ctx.run_id, style=script["style"]["id"], idea=script.get("title"),
                                                  source=ctx.source, window=script.get("window"), delivered=final_dir, at=now()))
    if ctx.workflow != "selftest":
        try:                                     # the devices it laid, for the next films' Director (variety, never a refusal)
            import devices_ledger
            devices_ledger.record(ctx.workflow, ctx.run_id, final_dir, ctx.supply)
        except Exception as e:  # noqa: BLE001 - a film is delivered whatever the variety ledger says
            ctx.log(f"devices ledger not written: {str(e)[:200]}")
    return dict(folder=final_dir, bytes=du(final_dir), spend_usd=rec["spend_usd"])


def wants_platform_song(ctx):
    """Every delivered film gets its platform song unless the run says otherwise: --args '{"platform_song": false}'."""
    return (ctx.args or {}).get("platform_song", True) is not False and ctx.workflow != "selftest"


def ledger_mode(ctx, folder=None):
    """Who records this delivery in the shared delivered ledger: "shell" (here, and only when --out is one of the owner's
    delivery folders: anywhere else the run is a test), "driver" (story.py and market.py, once the folder reaches its
    final root) or "off" (a podcast's inner short: podcast.py records the podcast's one folder).
    "driver" must be proven: the driver that started this process hands it its token (delivered.DRIVER_ENV). Unproven, or
    when the folder lands in a delivery folder itself (a driver stages outside them), the shell records it as its own."""
    mode = (ctx.args or {}).get("delivered_ledger") or ("off" if "moment" in (ctx.args or {}) else "shell")
    if mode == "driver" and not DL.driver_proven(ctx.workflow):
        mode = "shell"
    if mode == "driver" and folder and DL.in_delivery_root(folder):
        mode = "shell"
    return mode


def may_deliver(ctx, out_root):
    """Whether this run can end as a recorded delivery: a driver's run, or the shell's own into a delivery folder."""
    mode = ledger_mode(ctx, out_root)
    return mode == "driver" or (mode == "shell" and DL.in_delivery_root(out_root or ""))


def delivered_row(ctx, script, results, folder):
    """The delivered ledger's row for this film: the exact source ids and the idea in its compact form."""
    ids = []
    if ctx.source and os.path.isfile(ctx.source):
        ids.append(DL.sha256_file(ctx.source))
    src = (results.get("ideas") or {}).get("source") or {}
    for k in ("url", "link", "origin_url", "webpage_url"):
        v = src.get(k) if isinstance(src, dict) else None
        if isinstance(v, str) and DL.canonical_url(v):
            ids.append(DL.canonical_url(v))
    base = script.get("idea_full") or (script.get("idea") if isinstance(script.get("idea"), dict) else None) or {}
    idea = DL.idea_of(base)
    if not idea.get("title") and isinstance(script.get("idea"), str):
        idea["title"] = script["idea"]
    idea["title"] = idea.get("title") or script.get("title")
    idea["style"] = (script.get("style") or {}).get("id") or idea.get("style")
    ideas = results.get("ideas") or {}
    remake = script.get("remake_of") or (ideas.get("source") or {}).get("remake_of") or ((ctx.args or {}).get("remake") and "remake")
    return DL.make_row(ctx.workflow, folder, ids, idea, run_id=ctx.run_id, film_title=script.get("title"),
                       remake_of=remake or None, judged=True if isinstance(ideas.get("no_repeat"), dict) else None)


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
    state.pop("stopped", None)
    if a.out:
        state["out_root"] = os.path.abspath(a.out)
    jsave(os.path.join(run_dir, "run.json"), state)
    ctx = Ctx(run_dir, state, state_root)
    sounds = chain.use_sound_set()           # the workflow films' own sound-effect set, placed by what the picture does
    if sounds:
        ctx.log(f"sound set: {sounds}")
    if swept:
        ctx.log(f"swept stopped runs: {swept}")
    wf = load_workflow(state["workflow"])
    hang = os.environ.get("SHELL_HANG_AFTER")         # the cleanup test's kill point
    results = {s: ctx.out(s) for s in state["done"]}
    defects = []
    try:
        for stage in STAGES:
            __import__("openai_budget").raise_if_stopped(lane="workflow")
            if stage in state["done"]:
                continue
            t0 = time.time()
            ctx.log(f"stage {stage}")
            if stage == "ideas":
                r = wf.ideas(ctx)
                if ctx.workflow in DL.IDEA_WORKFLOWS and (not ctx.args.get("remake") or may_deliver(ctx, state.get("out_root"))):
                    # an exact repeat of a delivered idea is taken out by a model's judgement; the next idea leads. A remake
                    # of a delivered film skips it only as a test: one that can reach a delivery folder is judged like any idea
                    r = DL.guard_ideas(ctx.workflow, r, log=ctx.log)
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
            __import__("openai_budget").raise_if_stopped(lane="workflow")
            results[stage] = r
            jsave(os.path.join(run_dir, f"{stage}.json"), r)
            state["done"].append(stage)
            state["seconds"][stage] = round(time.time() - t0, 1)
            jsave(os.path.join(run_dir, "run.json"), state)
            if getattr(a, "until", None) == stage:
                # a stage test (the script stage alone): the run stops here, kept for --resume, never delivered
                state["pid"] = None
                jsave(os.path.join(run_dir, "run.json"), state)
                ctx.log(f"until {stage}: stopped after it")
                print(json.dumps(dict(run_id=state["run_id"], until=stage, done=state["done"], run_dir=run_dir,
                                      result=os.path.join(run_dir, f"{stage}.json"), stage_seconds=state["seconds"])))
                return 0
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
    ap.add_argument("--until", choices=[s for s in STAGES if s != "deliver"],
                    help="stop after this stage (a stage test): nothing after it runs, nothing is delivered")
    a = ap.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    if a.cmd == "status":
        return status(a)
    if not a.resume and not a.out:
        ap.error("--out is required for a new run")
    return run(a)


if __name__ == "__main__":
    sys.exit(main() or 0)
