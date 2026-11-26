"""Make a podcast-compatible collection using each workflow's ordinary film stages."""
import concurrent.futures
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import uuid

import delivered as DL
import shell

SKILLS = Path(__file__).resolve().parent.parent
CONFIG = {"wf3": ("Stories", "valmera-stories", "wf3.py", 6),
          "wf2": ("Marketing", "valmera-marketing", "proof.py", 5)}


def workflow(wf):
    _, skill, module, _ = CONFIG[wf]
    directory = SKILLS / skill
    sys.path.insert(0, str(directory))
    return shell.load_workflow(str(directory / module))


def discover(wf, root, batch, topic, count, previous=()):
    result = batch / (f"ideas-after-{len(previous)}.json" if previous else "plan.json")
    if result.is_file():
        return shell.jload(result)
    import openai_budget
    openai_budget.raise_if_stopped(lane="workflow")
    ctx = shell.Ctx(str(batch), dict(run_id=batch.name, workflow=wf,
                                     args=dict(line=topic, batch_candidates=count + 3, batch_previous=list(previous))), str(root))
    engine = workflow(wf)
    ideas = DL.guard_ideas(wf, engine.ideas(ctx), log=ctx.log)
    ideas["topic"] = topic
    shell.jsave(str(result), ideas)
    return ideas


def candidates(plan, wf):
    """Keep one film per named subject or editing pain, in the discovery ranking."""
    seen = set()
    for idea in plan["ideas"]:
        if wf == "wf3" and not (idea.get("supply") or {}).get("ok"):
            continue
        key = idea.get("pain") if wf == "wf2" else idea.get("subject")
        key = " ".join(str(key or idea["title"]).casefold().split())
        if key not in seen:
            yield idea
            seen.add(key)


def film_id(batch, index):
    return batch.name + "-" + hashlib.sha256(f"{batch.name}/{index}".encode()).hexdigest()[:6]


_ENGINE = None


def start_worker(wf):
    global _ENGINE
    _ENGINE = workflow(wf)
    import warm_film_tools
    warm_film_tools.enabled = wf == "wf3"


def recorded_cut(engine, options, directory, batch):
    """Cut the same Studio session in its native format when a narrated picture doesn't carry its words."""
    state = shell.jload(directory / "run.json")
    retry = batch / "recuts" / state["run_id"]
    if not (retry / "run.json").exists():
        checked = shell.jload(directory / "check.json")["checks"]
        script = shell.jload(directory / "script.json")
        replacement = engine.recorded_recut(script, checked)
        if not replacement:
            return
        retry.mkdir(parents=True, exist_ok=True)
        shell.jsave(str(retry / "ideas.json"), shell.jload(directory / "ideas.json"))
        shell.jsave(str(retry / "script.json"), replacement)
        shell.jsave(str(retry / "run.json"), dict(state, pid=None, done=["ideas", "script"]))
    options.work_root = str(retry.parent)


def finished(folder):
    return bool(shell.jload(Path(folder) / "film.json")["checks"]["ok"])


def make_film(wf, root, batch, index, idea, until_script=False):
    rid = film_id(batch, index)
    if until_script and "script" in (shell.jload(batch / "work" / rid / "run.json") or {}).get("done", []):
        return None
    # A coordinator can restart while a child still owns a recording or a render.
    with DL._locked(str(batch / "locks" / rid)):
        wait_for_editor(batch, rid)
        return edit_film(wf, root, batch, index, idea, until_script)


def wait_for_editor(batch, rid):
    for work in ("work", "recuts"):
        state_path = batch / work / rid / "run.json"
        while True:
            state = shell.jload(state_path) or {}
            pid = state.get("pid")
            if pid == os.getpid() or not shell.alive(pid):
                break
            if next((batch / "masters" / rid).glob("*/film.json"), None):
                return
            time.sleep(5)


def edit_film(wf, root, batch, index, idea, until_script):
    from contextlib import redirect_stdout, redirect_stderr
    from types import SimpleNamespace
    rid = film_id(batch, index)
    os.environ.update(CODEX_PROXY_PROOF=rid, VALMERA_RUN=rid, VALMERA_FILM_BATCH=batch.name)
    directory = batch / "work" / rid
    out = batch / "masters" / rid
    ready = next(out.glob("*/film.json"), None)
    if ready:
        return str(ready.parent)
    directory.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    # In-flight films have their own view of the preceding films in collection order.
    # The shared ledger receives the actual finished styles when the collection lands.
    local = batch / "styles" / rid
    local.mkdir(parents=True, exist_ok=True)
    cache = root / "music_cache"
    cache.mkdir(exist_ok=True)
    if not (local / "music_cache").exists():
        (local / "music_cache").symlink_to(cache, target_is_directory=True)
    planned = shell.jload(batch / "styles.json")
    if planned:
        shell.jsave(str(local / "ledger.json"), {wf: planned[str(index)]})
    else:
        local = root
    state_path = directory / "run.json"
    if not state_path.exists():
        plan = shell.jload(batch / "plan.json")
        args = dict(line=plan.get("topic"), look=False, delivered_ledger="off",
                    music_ledger=str(batch / "music-used.json"), shared_style_root=str(root))
        state = dict(run_id=rid, workflow=wf, source=None, out_root=str(out), args=args,
                     started=shell.now(), done=["ideas"], seconds={})
        ctx = shell.Ctx(str(directory), state, str(local))
        styles = _ENGINE.able_cards(ctx)
        style = next((s for s in styles if s["id"] == idea.get("style")), styles[index % len(styles)])
        shell.jsave(str(directory / "ideas.json"), dict(plan, ideas=[dict(idea, style=style["id"])],
                                                       chosen=idea["title"], spend=[]))
        shell.jsave(str(state_path), state)
    else:
        state = shell.jload(state_path)
        if "script" not in state["done"] and not shell.alive(state.get("pid")):
            if wf == "wf2" and (state.get("stopped") or {}).get("stage") == "script" and captured_session(directory, idea):
                state["args"]["recover_recording_cut"] = state["stopped"]["error"]
                shell.jsave(str(state_path), state)
            original = shell.jload(directory / "ideas.json")
            original["ideas"][0]["style"] = idea["style"]
            shell.jsave(str(directory / "ideas.json"), original)
    if until_script and "script" in shell.jload(state_path)["done"]:
        return None
    options = SimpleNamespace(state_root=str(local), work_root=str(directory.parent), resume=rid,
                              out=str(out), engine=_ENGINE, until="script" if until_script else None)
    with (batch / (rid + ".log")).open("a", buffering=1) as log, redirect_stdout(log), redirect_stderr(log):
        for attempt in range(2):
            if wf == "wf2" and not until_script:
                options.work_root = str(directory.parent)
                options.until = "check"
                if "check" not in shell.jload(state_path)["done"] and shell.run(options):
                    stopped = shell.jload(Path(options.work_root) / rid / "run.json")["stopped"]
                    continue
                recorded_cut(_ENGINE, options, directory, batch)
                options.until = None
            if shell.run(options) == 0:
                if until_script:
                    return None
                return str(next(out.glob("*/film.json")).parent)
            stopped = shell.jload(Path(options.work_root) / rid / "run.json")["stopped"]
            if attempt == 0 and stopped["stage"] == "script":
                # A candidate whose facts or recording could not support the film gives way to a reserve.
                break
        raise RuntimeError(f"{rid} stopped in {stopped['stage']}: {stopped['error']}")


def delivery_styles(wf, root, batch, items, target):
    path = batch / "delivery-styles.json"
    saved = shell.jload(path)
    if saved:
        return saved
    previous = (shell.jload(root / "ledger.json") or {}).get(wf, [])
    past = [r for r in previous if not r.get("run_id", "").startswith(batch.name + "-")][-3:]
    permitted = [s["id"] for s in workflow(wf).cards().values() if s.get("buildable") and s.get("offer", True)]
    order = []
    for i in range(target):
        available = [style for style in permitted if style not in {r["style"] for r in past}]
        preferred = items[i][1].get("style") if i < len(items) else None
        style = preferred if preferred in available else available[i % len(available)]
        order.append(dict(style=style, previous=past))
        past = (past + [dict(style=style)])[-3:]
    shell.jsave(str(path), order)
    return order


def captured_session(directory, idea):
    evidence = (shell.jload(directory / "script_state.json") or {}).get("i0") or {}
    return bool(evidence.get("ok") and Path(evidence.get("record") or "").is_file()
                and idea.get("device") != "triptych")


def build_wave(wf, root, batch, target, slots, tried, recorded_only=False):
    plan = shell.jload(batch / "plan.json")
    items = list(enumerate(candidates(plan, wf)))
    order = delivery_styles(wf, root, batch, items, target)
    style_plan = batch / "styles.json"
    snapshots = shell.jload(style_plan) or {}
    films = {}
    errors = (shell.jload(batch / "results.json") or {}).get("errors", {})

    def place(folder, preferred=None):
        record = shell.jload(Path(folder) / "film.json")
        style = record["style"]["id"]
        matches = [n for n in range(target) if n not in films and order[n]["style"] == style]
        if record["checks"]["ok"] and matches:
            films[preferred if preferred in matches else matches[0]] = str(folder)

    todo = []
    for i, idea in items:
        rid = film_id(batch, i)
        saved = next((batch / "masters" / rid).glob("*/film.json"), None)
        if saved:
            place(saved.parent)
            continue
        if i in tried:
            continue
        directory = batch / "work" / rid
        state = shell.jload(directory / "run.json") or {}
        if recorded_only and "script" not in state.get("done", []) and not captured_session(directory, idea):
            continue
        if state.get("stopped"):
            errors[str(i)] = state["stopped"]["error"]
        fixed = None
        if "script" in state.get("done", []) or shell.alive(state.get("pid")):
            script = shell.jload(directory / "script.json") or {}
            scripted = (shell.jload(directory / "ideas.json") or {}).get("ideas", [])
            fixed = script.get("style", {}).get("id") or (scripted[0]["style"] if scripted else None)
        todo.append((i, idea, fixed))
    todo.sort(key=lambda row: str(row[0]) in errors)
    config = dict(initializer=start_worker, initargs=(wf,))
    with (concurrent.futures.ProcessPoolExecutor(max_workers=slots, **config) as writers,
          concurrent.futures.ProcessPoolExecutor(max_workers=slots, **config) as editors):
        pending = {}
        while len(films) < target:
            writing = sum(stage == "script" for _, _, stage, _ in pending.values())
            # Keep writing and recording while completed scripts move through supply, score and render.
            occupied = set(films) | {pos for _, _, _, pos in pending.values()}
            for pos in (n for n in range(target) if n not in occupied):
                if writing >= slots or len(pending) >= 2 * slots:
                    break
                needed = order[pos]
                job = next((j for j in todo if j[2] == needed["style"]), None)
                if job is None:
                    job = next((j for j in todo if j[2] is None), None)
                if job is None:
                    continue
                todo.remove(job)
                i, idea, fixed = job
                tried.add(i)
                if not fixed:
                    snapshots[str(i)] = needed["previous"]
                    shell.jsave(str(style_plan), snapshots)
                idea = dict(idea, style=needed["style"])
                future = writers.submit(make_film, wf, root, batch, i, idea, True)
                pending[future] = (i, idea, "script", pos)
                writing += 1
            if not pending:
                break
            done, _ = concurrent.futures.wait(pending, return_when=concurrent.futures.FIRST_COMPLETED)
            for task in done:
                i, idea, stage, pos = pending.pop(task)
                try:
                    result = task.result()
                    if stage == "script" and result is None:
                        future = editors.submit(make_film, wf, root, batch, i, idea)
                        pending[future] = (i, idea, "finish", pos)
                    elif finished(result):
                        place(result, pos)
                        errors.pop(str(i), None)
                    else:
                        errors[str(i)] = "The private cut still has measured defects; continuing with the next source."
                except Exception as exc:
                    errors[str(i)] = str(exc)
    shell.jsave(str(batch / "results.json"), dict(films=films, errors=errors))
    return [Path(films[i]) for i in sorted(films)]


def build(wf, root, batch, target, slots):
    tried = set()
    while True:
        films = build_wave(wf, root, batch, target, slots, tried)
        if len(films) >= target:
            return films
        plan = shell.jload(batch / "plan.json")
        before = len(list(candidates(plan, wf)))
        previous = [i.get("subject") or i.get("pain") or i["title"] for i in plan["ideas"]]
        more = discover(wf, root, batch, plan.get("topic"), target - len(films), previous)
        plan["ideas"].extend(more["ideas"])
        plan["spend"].extend(more.get("spend", []))
        shell.jsave(str(batch / "plan.json"), plan)
        if len(list(candidates(plan, wf))) == before:
            # No new usable source in this search. Preserve that search and already completed work for resume.
            raise RuntimeError(f"Discovery returned only previously tried subjects or unavailable sources; see {batch}/plan.json")


def cost(batch, tags, films, calls_path=None, started=None):
    if started is None:
        started = float((batch / "started").read_text())
    path = Path(calls_path or Path(shell.V) / "tools/codex-proxy/calls.jsonl")
    calls, requests, models = 0, 0, set()
    if path.exists():
        with path.open() as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("run") in tags and row.get("ts", started) >= started:
                    requests += 1
                    calls += int(row.get("codex_calls") or 0)
                    models.add(row.get("model") or "unknown")
    seconds = round(time.time() - started, 1)
    return dict(wall_seconds=seconds, seconds_per_film=round(seconds / films, 1),
                proxy_calls=calls if models else None, proxy_calls_per_film=round(calls / films, 2) if models else None,
                proxy_requests=requests if models else None, models=sorted(models))


def safe_name(value, length):
    name = "".join(" " if c in '/:\\' or ord(c) < 32 else c for c in str(value))
    return " ".join(name.split())[:length].strip(". ") or "Film"


def copy_master(src, dst):
    try:
        os.link(src, dst)
    except OSError:
        shutil.copyfile(src, dst)


def handoff(wf, batch, folders, out, sidecar, topic):
    out.mkdir(parents=True, exist_ok=True)
    date = dt.datetime.fromtimestamp(float((batch / "started").read_text()))
    title = safe_name(topic or date.strftime("%B %d, %Y"), 64)
    name = f"{CONFIG[wf][0]} - {title} ({date.year}) - {len(folders)} Shorts"
    final = out / name
    def owned(folder):
        manifest = shell.jload(folder / "shorts.json", {}) or {}
        return manifest.get("batch_id") == batch.name
    if final.exists() and not owned(final):
        final = out / (name + " - " + batch.name)
    part = out / ("." + batch.name + ".partial")
    shutil.rmtree(part, ignore_errors=True)
    part.mkdir()
    (part / "clean").mkdir()
    (part / "records").mkdir()
    listing, rows, sources, songs = [], [], [], []
    for n, folder in enumerate(folders, 1):
        sidecar(str(folder))
        rec = shell.jload(folder / "film.json")
        title = rec["script"]["title"]
        filename = f"{n:02d} - {safe_name(title, 70)}.mp4"
        clean = folder / "film_clean.mp4"
        copy_master(folder / "film.mp4", part / filename)
        copy_master(clean if clean.exists() else folder / "film.mp4", part / "clean" / filename)
        record = f"records/{n:02d}.json"
        copy_master(folder / "film.json", part / record)
        source = (folder / "SOURCES.md").read_text()
        sources.append(f"## {n:02d} - {title}\n\n{source}")
        item = dict(n=n, file=filename, clean="clean/" + filename, record=record, title=title,
                    style=rec["style"]["id"], seconds=rec["checks"].get("final_s"), checks_ok=rec["checks"]["ok"],
                    hook=rec["script"].get("hook_line"), close=rec["script"].get("close_line"),
                    end_card=rec["checks"].get("endcard_ok"), watermark=rec["checks"].get("mark_ok"),
                    lufs=rec["checks"].get("lufs"), music=rec.get("music"), spend_usd=rec.get("spend_usd"))
        if rec.get("platform_music"):
            song = dict(rec["platform_music"], upload=item["clean"])
            item["platform_music"] = song
            songs.append(dict(song, n=n))
        for extra in ("STORY.md", "film_song.mp4"):
            if (folder / extra).is_file():
                copy_master(folder / extra, part / "records" / f"{n:02d}-{extra}")
        listing.append(item)
        rows.append(DL.row_from_film(str(folder), wf, folder=str(final), seeded=False))
    tags = {batch.name} | {film_id(batch, i) for i in range(len(shell.jload(batch / "plan.json")["ideas"]))}
    metrics = cost(batch, tags, len(folders))
    manifest = dict(contract=1, workflow=CONFIG[wf][1], batch_id=batch.name, published=False,
                    delivered=shell.now(), shorts=listing, cost=metrics)
    shell.jsave(str(part / "shorts.json"), manifest)
    if songs:
        shell.jsave(str(part / "platform_music.json"), dict(contract=1, how=songs[0].get("how"), shorts=songs))
    (part / "SOURCES.md").write_text("\n\n".join(sources))
    (part / "README.md").write_text(f"# {name}\n\n" + "\n".join(x["file"] for x in listing) + "\n")
    DL.deliver_collection(rows, part, final, batch.name)
    shutil.rmtree(part, ignore_errors=True)
    return dict(delivered=str(final), films=len(listing), topic=topic, cost=shell.jload(final / "shorts.json")["cost"])


def produce(wf, root, batch, topic, out, sidecar):
    import subprocess
    _, _, _, count = CONFIG[wf]
    os.environ.update(CODEX_PROXY_PROOF=batch.name, VALMERA_RUN=batch.name)
    discover(wf, root, batch, topic, count)
    films = []
    if wf == "wf2" and (batch / "masters").exists():
        # A captured session can finish from its own files while another lane uses the recording stack.
        films = build_wave(wf, root, batch, count, 2, set(), recorded_only=True)
    if wf == "wf2" and len(films) < count:
        command = [sys.executable, __file__, "studio", wf, str(root), str(batch)]
        if os.environ.get("GAUNTLET_LOCK_HELD") != "1":
            command = ["zsh", shell.V + "/ops-loop/tools/gauntlet_lock.sh", *command]
        subprocess.run(command, check=True)
        results = shell.jload(batch / "results.json")["films"]
        films = [Path(results[i]) for i in sorted(results, key=int)]
    elif wf != "wf2":
        films = build(wf, root, batch, count, 2)
    report = handoff(wf, batch, films, out, sidecar, topic)
    for folder in films:
        rec = shell.jload(folder / "film.json")
        shell.ledger_add(str(root), wf, dict(run_id=rec["run_id"], style=rec["style"]["id"],
                                            idea=rec["script"]["title"], window=rec.get("window"),
                                            delivered=report["delivered"], at=shell.now()))
    return report


def studio_build(root, batch):
    engine = workflow("wf2")
    runner = engine.G + "/runner"
    sys.path.insert(0, runner)
    try:
        import stack
    finally:
        # The replay runner has its own score.py. Spawn film editors with the ordinary worker module path.
        sys.path.remove(runner)
    run = "wf2-" + batch.name
    settings_file = batch / "stack-settings.json"
    shell.jsave(str(settings_file), engine.record_settings(batch.name))
    settings = stack.load_settings(str(settings_file))
    location = Path(stack.run_dir(run))
    try:
        os.environ["VALMERA_BATCH_STUDIO"] = run
        os.environ["VALMERA_BATCH_SETTINGS"] = str(settings_file)
        os.environ["CODEX_PROXY_PROOF"] = batch.name
        build("wf2", root, batch, CONFIG["wf2"][3], settings["slots"]["agent"])
    finally:
        if (location / "stack.json").exists():
            stack.down(run, drop_db=True)
            shutil.rmtree(location)


def run_driver(wf, root, topic, out, sidecar):
    root, out = Path(root), Path(out)
    key = hashlib.sha256(json.dumps([topic, str(out.resolve())]).encode()).hexdigest()[:16]
    parent = root / "batches"
    parent.mkdir(exist_ok=True)
    pointer = parent / (key + ".json")
    with DL._locked(str(pointer)):
        data = shell.jload(pointer) or {}
        batch = parent / (data.get("id") or f"{wf}-{dt.datetime.now():%m%d%H%M}-{uuid.uuid4().hex[:8]}")
        batch.mkdir(exist_ok=True)
        shell.jsave(str(pointer), dict(id=batch.name))
        if not (batch / "started").exists():
            (batch / "started").write_text(str(time.time()))
        report = produce(wf, root, batch, topic, out, sidecar)
        for child in (batch / "work").glob("*"):
            link = Path(shell.chain.SLICE) / "work" / ("wf-" + child.name)
            if link.is_symlink() and link.resolve() == (child / "supply").resolve():
                link.unlink()
        pointer.unlink()
        shutil.rmtree(batch)
        print(json.dumps(report))
    return 0


if __name__ == "__main__" and sys.argv[1:2] == ["studio"]:
    studio_build(Path(sys.argv[3]), Path(sys.argv[4]))
