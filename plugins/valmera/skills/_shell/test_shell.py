"""The cleanup test (BUILD W0's gate), on the shell's self-test workflow.

    /Users/masaoodi/Documents/Valmera/.valmera/venv-worker/bin/python -m pytest -q test_shell.py

1. A run delivers one folder, and disk after equals disk before plus the delivered folder, within 1%.
2. The same after kill -9 midway, a resume and a delivery.
3. A kill with no resume, then a new run's start: the old folder and its slice link are gone.
4. A folder is delivered once. A run never repeats any of its workflow's last three styles.
"""
import json
import os
import signal
import subprocess
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


def env_for(tmp, hang=None):
    e = dict(os.environ, SHELL_STATE_ROOT=str(tmp / "state"), SHELL_SLICE=str(tmp / "slice"))
    e.pop("SHELL_HANG_AFTER", None)
    if hang:
        e["SHELL_HANG_AFTER"] = hang
    return e


def du(p):
    t = 0
    for root, _, files in os.walk(p):
        for f in files:
            fp = os.path.join(root, f)
            if not os.path.islink(fp):
                t += os.path.getsize(fp)
    return t


def measured(tmp):
    """Disk the shell may touch: its work root, the deliver root and the slice's work folder."""
    return du(tmp / "state" / "work") + du(tmp / "out") + du(tmp / "slice" / "work")


@pytest.fixture
def tmp(tmp_path):
    (tmp_path / "slice" / "work").mkdir(parents=True)
    (tmp_path / "out").mkdir()
    (tmp_path / "state" / "work").mkdir(parents=True)
    (tmp_path / "slice" / "work" / "p0000").mkdir()          # someone else's session: never touched
    (tmp_path / "slice" / "work" / "p0000" / "keep.txt").write_text("x" * 1000)
    return tmp_path


def shell(tmp, *args, hang=None, wait=True):
    cmd = [PY, os.path.join(HERE, "shell.py"), "run"] + list(args)
    if wait:
        return subprocess.run(cmd, env=env_for(tmp, hang), capture_output=True, text=True, timeout=300)
    return subprocess.Popen(cmd, env=env_for(tmp, hang), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def new_run(tmp, **kw):
    return shell(tmp, "--workflow", "selftest", "--source", "/dev/null", "--out", str(tmp / "out"), **kw)


def kill_after(tmp, stage):
    p = new_run(tmp, hang=stage, wait=False)
    work = tmp / "state" / "work"
    for _ in range(600):
        runs = [d for d in work.iterdir() if (d / "run.json").exists()] if work.exists() else []
        if runs and stage in json.loads((runs[0] / "run.json").read_text()).get("done", []):
            break
        time.sleep(0.1)
    else:
        p.kill()
        raise AssertionError("the run never reached " + stage)
    time.sleep(0.3)
    os.kill(p.pid, signal.SIGKILL)
    p.wait()
    return runs[0].name


def delivered(tmp):
    return [d for d in (tmp / "out").iterdir() if not d.name.startswith(".")]


def assert_clean(tmp, before, r):
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    rep = json.loads(r.stdout.strip().splitlines()[-1])
    assert rep["work_empty"] is True
    assert list((tmp / "state" / "work").iterdir()) == []
    assert [n for n in os.listdir(tmp / "slice" / "work") if n.startswith("wf-")] == []
    assert (tmp / "slice" / "work" / "p0000" / "keep.txt").exists()
    out = delivered(tmp)
    assert len(out) == 1 and sorted(os.listdir(out[0])) == ["film.json", "film.mp4"]
    assert not [d for d in (tmp / "out").iterdir() if d.name.startswith(".")]
    after = measured(tmp)
    want = before + du(out[0])
    assert abs(after - want) <= 0.01 * want, (before, after, du(out[0]))
    rec = json.loads((out[0] / "film.json").read_text())
    assert rec["rules"]["published"] is False and rec["rules"]["music_picked"] is False
    assert rec["checks"]["endcard_ok"] and rec["checks"]["mark_ok"] and rec["checks"]["length_ok"]
    return rec


def test_clean_run(tmp):
    before = measured(tmp)
    assert_clean(tmp, before, new_run(tmp))


def test_kill_resume(tmp):
    before = measured(tmp)
    rid = kill_after(tmp, "supply")
    assert (tmp / "state" / "work" / rid).exists()               # a stopped run is kept for resume
    assert measured(tmp) > before + 5 * 1024 * 1024               # and it really made a mess
    r = shell(tmp, "--resume", rid)
    rec = assert_clean(tmp, before, r)
    assert rec["run_id"] == rid


def test_kill_then_new_run_sweeps(tmp):
    before = measured(tmp)
    rid = kill_after(tmp, "render")
    assert os.path.islink(tmp / "slice" / "work" / f"wf-{rid}")
    r = new_run(tmp)
    rec = assert_clean(tmp, before, r)
    assert rec["run_id"] != rid
    assert not (tmp / "state" / "work" / rid).exists()


def test_deliver_once_and_styles(tmp):
    before = measured(tmp)
    assert_clean(tmp, before, new_run(tmp))
    sys.path.insert(0, HERE)
    import shell as sh_mod
    led = tmp / "state" / "ledger.json"
    rows = json.loads(led.read_text())["selftest"]
    assert len(rows) == 1
    # a second run with the same delivered name must refuse to add to the folder
    name = delivered(tmp)[0].name
    rec = json.loads((delivered(tmp)[0] / "film.json").read_text())
    fake = sh_mod.Ctx(str(tmp / "x"), dict(run_id="other", workflow="selftest"), str(tmp / "state"))
    with pytest.raises(RuntimeError):
        sh_mod.deliver(fake, dict(deliver_name=name), str(tmp / "out"),
                       dict(script=dict(style=rec["style"], title="t"), check=dict(final="/dev/null", checks={}), ideas={}, render={}))
    # the last three delivered styles are never offered
    d = {"selftest": [dict(style=s) for s in ["t1", "t2", "t3", "t4"]]}
    led.write_text(json.dumps(d))
    allowed = [s["id"] for s in sh_mod.allowed_styles(str(tmp / "state"), "selftest")]
    assert allowed == ["t1", "t5"]
    r = new_run(tmp)
    assert r.returncode == 0, r.stderr[-1500:]
    rows = json.loads(led.read_text())["selftest"]
    assert rows[-1]["style"] == "t1" and rows[-1]["style"] not in [x["style"] for x in rows[-4:-1]]


def test_a_scored_film_is_the_film_and_the_clean_one_sits_beside_it(tmp_path):
    """Every film gets music (owner, 13 Nov): the film Valmera scored is film.mp4, with its track's credit in film.json,
    and the same film without music is film_clean.mp4. A song the owner handed over stays a separate file."""
    import chain
    import shell as sh_mod
    for n, b in (("clean.mp4", b"clean"), ("music.mp4", b"music"), ("song.mp4", b"song")):
        (tmp_path / n).write_bytes(b)
    (tmp_path / "state").mkdir()
    ctx = sh_mod.Ctx(str(tmp_path / "run"), dict(run_id="r1-abcdef", workflow="wf3"), str(tmp_path / "state"))
    style = dict(id="s1")
    res = dict(script=dict(style=style, title="A film"), ideas={}, render={},
               check=dict(final=str(tmp_path / "clean.mp4"), checks={}, final_music=str(tmp_path / "music.mp4"),
                          music=dict(title="T", author="A", license="CC-BY-4.0", credit="\"T\" by A (CC BY 4.0)"), spend=[dict(stage="check", usd=0.01)]))
    d = sh_mod.deliver(ctx, {}, str(tmp_path / "out"), res)
    folder = d["folder"]
    assert open(os.path.join(folder, "film.mp4"), "rb").read() == b"music" and open(os.path.join(folder, "film_clean.mp4"), "rb").read() == b"clean"
    rec = json.load(open(os.path.join(folder, "film.json")))
    assert rec["rules"]["music_picked"] is True and rec["music"]["credit"].startswith("\"T\"") and rec["music"]["clean_export"] == "film_clean.mp4"
    assert rec["spend_usd"] == 0.01
    ctx2 = sh_mod.Ctx(str(tmp_path / "run2"), dict(run_id="r2-abcdef", workflow="wf3"), str(tmp_path / "state"))
    res2 = dict(script=dict(style=style, title="Another film"), ideas={}, render={},
                check=dict(final=str(tmp_path / "clean.mp4"), checks={}, final_song=str(tmp_path / "song.mp4"), song=dict(file="x.wav", supplied_by="owner")))
    f2 = sh_mod.deliver(ctx2, {}, str(tmp_path / "out"), res2)["folder"]
    assert open(os.path.join(f2, "film.mp4"), "rb").read() == b"clean" and open(os.path.join(f2, "film_song.mp4"), "rb").read() == b"song"
    assert json.load(open(os.path.join(f2, "film.json")))["rules"]["music_picked"] is False and not os.path.exists(os.path.join(f2, "film_clean.mp4"))
    # music is every film's unless the run says no; the shell's own self-test never asks for it
    assert chain.wants_music(ctx) and not chain.wants_music(sh_mod.Ctx(str(tmp_path / "run3"), dict(run_id="r3", workflow="wf3", args=dict(music=False)), str(tmp_path / "state")))
    assert not chain.wants_music(sh_mod.Ctx(str(tmp_path / "run4"), dict(run_id="r4", workflow="selftest"), str(tmp_path / "state")))



def test_footage_passes_the_engines_opt_in_flags_only_when_given(tmp_path, monkeypatch):
    """Round 2 (valmera-stories): strict lines, motion first, round-robin allocation and lost-cut reassignment reach
    footage.run only when a caller asks; without them the command is exactly as before."""
    import types
    import chain
    seen = []

    def run(cmd, **kw):
        seen.append(cmd)
        out = cmd[cmd.index("--out") + 1]
        open(os.path.join(out, "footage.json"), "w").write("{}")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(chain.subprocess, "run", run)
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    for d in ("run", "sup", "tmp"):
        (tmp_path / d).mkdir()
    ctx = types.SimpleNamespace(supply=str(tmp_path / "sup"), run_dir=str(tmp_path / "run"), tmp=str(tmp_path / "tmp"), workflow="wf3", run_id="r",
                                log=lambda m: None, log_raw=lambda m: None)
    chain.footage(ctx, dict(script={}), out_name="a")
    chain.footage(ctx, dict(script={}), out_name="b", strict_lines=True, motion_first=True, alloc="round", reassign_lost=True)
    assert not any(f in seen[0] for f in ("--strict-lines", "--motion-first", "--alloc", "--reassign-lost"))
    assert all(f in seen[1] for f in ("--strict-lines", "--motion-first", "--reassign-lost")) and seen[1][seen[1].index("--alloc") + 1] == "round"


def test_footage_keeps_sources_skips_verify_and_logs_no_secret(tmp_path, monkeypatch):
    """Round 3 (valmera-stories): verify=False, whole_first and keep_sources put FOOTAGE_VERIFY=0, --whole-first,
    FOOTAGE_KEEP_SOURCES (<supply>/sources) and FOOTAGE_LOCAL_SOURCES (that manifest's rows) into the engine's
    environment; without them none is set; nothing logged carries the key."""
    import types
    import chain
    seen, logs = [], []

    def run(cmd, **kw):
        seen.append((cmd, kw.get("env") or {}))
        out = cmd[cmd.index("--out") + 1]
        open(os.path.join(out, "footage.json"), "w").write("{}")
        return types.SimpleNamespace(returncode=0, stdout="done", stderr="")
    monkeypatch.setattr(chain.subprocess, "run", run)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-value-123")
    for v in ("FOOTAGE_VERIFY", "FOOTAGE_KEEP_SOURCES", "FOOTAGE_LOCAL_SOURCES"):
        monkeypatch.delenv(v, raising=False)
    for d in ("run", "sup", "tmp"):
        (tmp_path / d).mkdir()
    keep = tmp_path / "sup" / "sources"
    keep.mkdir()
    kept = keep / "a.mp4"
    kept.write_bytes(b"v" * 4000)
    (keep / "manifest.json").write_text(json.dumps([dict(url="https://y/1", path=str(kept), rung="default"),
                                                    dict(url="https://y/2", path=str(keep / "gone.mp4"), rung="default")]))
    ctx = types.SimpleNamespace(supply=str(tmp_path / "sup"), run_dir=str(tmp_path / "run"), tmp=str(tmp_path / "tmp"), workflow="wf3", run_id="r",
                                log=logs.append, log_raw=logs.append)
    chain.footage(ctx, dict(script={}), out_name="a")
    chain.footage(ctx, dict(script={}), out_name="b", verify=False, whole_first=True, keep_sources=True)
    plain, opt = seen
    assert not any(k in plain[1] for k in ("FOOTAGE_VERIFY", "FOOTAGE_KEEP_SOURCES", "FOOTAGE_LOCAL_SOURCES")) and "--whole-first" not in plain[0]
    assert "--whole-first" in opt[0] and opt[1]["FOOTAGE_VERIFY"] == "0" and opt[1]["FOOTAGE_KEEP_SOURCES"] == str(keep)
    assert json.loads(opt[1]["FOOTAGE_LOCAL_SOURCES"]) == [dict(url="https://y/1", path=str(kept))]       # a missing file is not claimed
    assert not any("sk-secret-value-123" in str(m) for m in logs)
