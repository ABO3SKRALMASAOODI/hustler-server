import json
import time

import batch_films as batch
import delivered as DL


def test_numbered_collection_recovers_after_rename_and_records_each_film(tmp_path, monkeypatch):
    out = tmp_path / "Story Films"
    monkeypatch.setenv("VALMERA_DELIVERY_ROOTS", str(out))
    run = tmp_path / "run"
    run.mkdir()
    (run / "started").write_text(str(time.time()))
    (run / "plan.json").write_text('{"ideas":[{},{}]}')
    folders = []
    for i in range(2):
        film = run / str(i)
        film.mkdir()
        rec = dict(workflow="wf3", run_id=f"r{i}", checks=dict(ok=True), style=dict(id="archive"),
                   script=dict(title=f"Film {i}", idea_full=dict(subject=f"Subject {i}")),
                   platform_music=dict(song="song", how="catalog"), no_repeat=dict(checked=2))
        (film / "film.json").write_text(json.dumps(rec))
        (film / "film.mp4").write_bytes(bytes([i, 1]))
        (film / "film_clean.mp4").write_bytes(bytes([i, 2]))
        (film / "SOURCES.md").write_text(f"Credits {i}")
        folders.append(film)
    def handoff():
        return batch.handoff("wf3", run, folders, out, lambda folder: None, None)
    from pathlib import Path
    final = Path(handoff()["delivered"])
    manifest = json.loads((final / "shorts.json").read_text())
    for i, row in enumerate(manifest["shorts"]):
        assert (final / row["file"]).read_bytes() == bytes([i, 1])
        assert (final / row["clean"]).read_bytes() == bytes([i, 2])
        assert (final / row["record"]).is_file()
    assert len(DL.rows("wf3")) == 2
    ledger = Path(DL.path())
    ledger.write_text(ledger.read_text().splitlines()[0] + "\n")
    assert Path(handoff()["delivered"]) == final
    assert [r["run_id"] for r in DL.rows("wf3")] == ["r0", "r1"]


def test_proxy_cost_counts_internal_calls_across_workers_and_cache_hits(tmp_path):
    (tmp_path / "started").write_text(str(time.time() - 100))
    calls = tmp_path / "calls.jsonl"
    calls.write_text("\n".join(json.dumps(dict(run=run, codex_calls=n, model="sol")) for run, n in
                               [("plan", 2), ("child", 3), ("other", 20), ("child", 0)]))
    cost = batch.cost(tmp_path, {"plan", "child"}, 2, calls)
    assert cost["proxy_calls_per_film"] == 2.5
    assert 50 <= cost["seconds_per_film"] < 51


def test_warm_engines_reuse_one_process_and_each_films_environment(tmp_path):
    import os
    import warm_film_tools as warm
    package = tmp_path / "footage"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "run.py").write_text("import os\ndef main(): print(os.getpid(), os.environ['VALMERA_RUN'])")
    (tmp_path / "music_film.py").write_text(
        "import os\ndef openai_call(): return None\n"
        "def score(scene, work, **kwargs): return dict(pid=os.getpid(), run=os.environ['VALMERA_RUN'])")
    scene = tmp_path / "l1.json"
    scene.write_text("{}")
    spec, report = tmp_path / "args.json", tmp_path / "receipt.json"
    spec.write_text(json.dumps(dict(l1=str(scene), work="", film="", out="", kind="stories", cache="", turn_drop=False,
                                    brief="", used=[], report=str(report))))
    log = tmp_path / "log.txt"
    # The production slot wrapper also uses zsh and nice before invoking the client.
    wrapper = tmp_path / "slot.sh"
    wrapper.write_text('nice -n 10 "$@"\n')
    try:
        for run in ("first", "second"):
            result = warm.call("footage", [], worker=str(tmp_path), env=dict(os.environ, VALMERA_RUN=run),
                               log=log, timeout=10)
            assert result.returncode == 0, result.stderr
        result = warm.call("music", [str(spec)], worker=str(tmp_path), env=dict(os.environ, VALMERA_RUN="third"),
                           log=log, timeout=10, heavy=["zsh", str(wrapper)])
        assert result.returncode == 0, result.stderr
        receipt = json.loads(report.read_text())
        assert log.read_text().splitlines() == [f"{receipt['pid']} first", f"{receipt['pid']} second"]
        assert receipt["run"] == "third"
    finally:
        warm.close()


def test_scripts_continue_during_finishing_and_use_a_distinct_reserve(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from types import SimpleNamespace
    run = tmp_path / "batch"
    run.mkdir()
    ideas = [dict(title=f"Film {i}", subject=f"Subject {i}", supply=dict(ok=True), style="archive") for i in range(8)]
    (run / "plan.json").write_text(json.dumps(dict(ideas=[ideas[0], *ideas], spend=[])))
    writers_filled_collection = Event()
    def make(wf, root, batch_dir, index, idea, until_script=False):
        if until_script:
            if index >= 6:
                writers_filled_collection.set()
            if index == 1:
                raise RuntimeError("unavailable source")
            return None
        assert writers_filled_collection.wait(timeout=5)
        folder = batch_dir / idea["title"]
        folder.mkdir()
        (folder / "film.json").write_text(json.dumps(dict(checks=dict(ok=index != 2), style=dict(id=idea["style"]))))
        return str(folder)
    engine = SimpleNamespace(cards=lambda: {i:dict(id=i, buildable=True) for i in "abcd"})
    monkeypatch.setattr(batch, "workflow", lambda wf: engine)
    monkeypatch.setattr(batch, "make_film", make)
    monkeypatch.setattr(batch.concurrent.futures, "ProcessPoolExecutor", ThreadPoolExecutor)
    films = batch.build("wf3", tmp_path, run, 6, 2)
    assert {p.name for p in films} == {"Film 0", "Film 3", "Film 4", "Film 5", "Film 6", "Film 7"}
    ordered = [json.loads((p / "film.json").read_text())["style"]["id"] for p in films]
    assert all(style not in ordered[max(0, i - 3):i] for i, style in enumerate(ordered))
    assert "unavailable source" in json.loads((run / "results.json").read_text())["errors"]["1"]


def test_public_drivers_enter_batch_with_parsed_topic_and_destination(tmp_path, monkeypatch):
    import importlib.util
    import os
    import sys
    for wf, folder, filename in (("wf3", "valmera-stories", "story"), ("wf2", "valmera-marketing", "market")):
        monkeypatch.setenv(filename.upper() + "_NO_REEXEC", "1")
        spec = importlib.util.spec_from_file_location(filename + "_batch_test", batch.SKILLS / folder / (filename + ".py"))
        driver = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(driver)
        monkeypatch.setattr(driver, "prepare_state", lambda: None)
        monkeypatch.setattr(driver, "environment", lambda: dict(os.environ))
        monkeypatch.setattr(driver, "stopped_run", lambda topic: None)
        if wf == "wf2":
            monkeypatch.setattr(driver, "start_proxy", lambda env: None)
        received = []
        monkeypatch.setattr(batch, "run_driver", lambda *args: received.append(args) or 0)
        test = str(tmp_path / filename)
        monkeypatch.setattr(sys, "argv", [filename, "one", "--test-out", test, "theme"])
        assert driver.main() == 0
        assert received == [(wf, driver.STATE_ROOT, "one theme", test, driver.sources_md)]


def test_replacements_continue_from_finished_films_across_research_rounds(tmp_path, monkeypatch):
    def idea(i):
        return dict(subject=str(i), title=str(i), supply=dict(ok=True))
    (tmp_path / "plan.json").write_text(json.dumps(dict(ideas=[idea(0)], spend=[])))
    waves = []
    def wave(wf, root, run, target, slots, tried):
        waves.append(len(tried))
        tried.add(len(waves))
        return [tmp_path / str(i) for i in range(len(waves))]
    monkeypatch.setattr(batch, "build_wave", wave)
    monkeypatch.setattr(batch, "discover", lambda wf, root, run, topic, count, previous:
                        dict(ideas=[idea(len(previous))], spend=[]))
    assert len(batch.build("wf3", tmp_path, tmp_path, 3, 2)) == 3
    assert waves == [0, 1, 2]


def test_restarted_coordinator_waits_for_the_existing_render(tmp_path, monkeypatch):
    rid = "batch-child"
    work = tmp_path / "work" / rid
    work.mkdir(parents=True)
    state = work / "run.json"
    state.write_text(json.dumps({"pid": 1234}))
    monkeypatch.setattr(batch.shell, "alive", lambda pid: pid == 1234)
    waits = []
    def complete(seconds):
        assert not waits
        waits.append(seconds)
        state.unlink()
    monkeypatch.setattr(batch.time, "sleep", complete)
    batch.wait_for_editor(tmp_path, rid)
    assert len(waits) == 1


def test_running_media_keeps_progress_and_cancels_an_idle_process(tmp_path):
    import os
    import sys
    import subprocess
    import pytest
    import warm_film_tools as warm
    command = [sys.executable, "-c", "import time,pathlib; p=pathlib.Path('take'); "
               "p.touch(); time.sleep(.35); p.touch(); time.sleep(.35); p.touch(); time.sleep(.35)"]
    options = dict(cwd=tmp_path, env=dict(os.environ), progress_root=tmp_path, timeout=.55)
    assert warm.run_media(command, **options).returncode == 0
    with pytest.raises(subprocess.TimeoutExpired):
        warm.run_media([sys.executable, "-c", "import time; time.sleep(15)"], **options)


def test_batch_hearing_falls_back_to_the_requested_worker_listener(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import batch_hearing as h
    monkeypatch.setattr(h, "PYTHON", tmp_path / "python")
    monkeypatch.setattr(h, "MODEL", tmp_path)
    h.PYTHON.touch()
    (tmp_path / "config.json").touch()
    monkeypatch.setenv("VALMERA_BATCH_HEARING_LOCK", str(tmp_path / "hearing.lock"))
    monkeypatch.delenv("FOOTAGE_HEAR_PROVIDER", raising=False)
    def unavailable(*args):
        raise RuntimeError("GPU unavailable")
    monkeypatch.setattr(h, "read_local", unavailable)
    listener = SimpleNamespace(transcribe=lambda path, dur, workers: (path, dur, workers))
    h.install(listener)
    h.install(listener)
    assert listener.transcribe("source", 5, 2) == ("source", 5, 2)


def test_resume_recovers_only_complete_sources_with_original_provenance(tmp_path, monkeypatch):
    import chain
    root = tmp_path / "footage-test/src"
    for url, n in (("source-a", 1), ("source-a", 2), ("truncated", 3)):
        folder = root / str(n)
        folder.mkdir(parents=True)
        (folder / "whole.mp4").write_bytes(b"x")
        (folder / "meta.info.json").write_text(json.dumps(dict(webpage_url=url, duration=20 if n == 3 else 30)))
    monkeypatch.setattr(chain.subprocess, "check_output", lambda *a, **kw:
                        json.dumps(dict(streams=[dict(codec_type="video")], format=dict(duration="30.1"))).encode())
    rows = list(chain.resumed_sources(tmp_path, set()))
    assert [row["url"] for row in rows] == ["source-a"]
