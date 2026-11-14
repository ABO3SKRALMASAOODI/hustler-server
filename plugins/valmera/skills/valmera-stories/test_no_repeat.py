"""Never the exact same idea told the same way (owner, 14 Nov 2026), in workflow 3's ideas prompt and its driver.

    /Users/masaoodi/Documents/Valmera/.valmera/venv-worker/bin/python -m pytest -q test_no_repeat.py

No network and no model: the writer's web call and the shell's subprocess are stand-ins.
"""
import importlib.util
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "_shell"))
os.environ.setdefault("STORY_NO_REEXEC", "1")
import delivered as DL  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_ideas_prompt_carries_the_owners_rule_not_a_subject_ban(monkeypatch):
    import writer
    seen = {}

    def ask_web(system, content, **kw):
        seen["content"] = content
        return '{"ideas": []}', {}
    monkeypatch.setattr(writer, "ask_web", ask_web)
    DL.append(DL.make_row("wf3", "/x/woody", [], dict(title="The Woody who nearly killed Toy Story", style="archive")))
    writer.find_ideas("short", [dict(id="reel", name="Reel", look="l", hook="h", close="c", needs="n")],
                      avoid=["The Woody who nearly killed Toy Story"], delivered=DL.prompt_block("wf3"))
    c = seen["content"]
    assert "never repeat a subject" not in c
    assert "never the exact same idea told the same way" in c and "A new angle or a new version of the same subject is welcome" in c
    assert "The Woody who nearly killed Toy Story" in c and DL.TITLE_ONLY in c


def _driver(tmp_path, monkeypatch):
    """story.py with the shell replaced: the 'shell' stages a finished film folder and prints its JSON line."""
    story = _load("story_driver_under_test", os.path.join(HERE, "story.py"))
    state = tmp_path / "state"
    monkeypatch.setattr(story, "STATE_ROOT", str(state))
    monkeypatch.setattr(story, "environment", lambda: {})
    monkeypatch.setattr(story, "prepare_state", lambda: None)
    monkeypatch.setattr(story, "stopped_run", lambda topic: None)
    monkeypatch.setattr(story, "sources_md", lambda folder: None)
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        args = json.loads(cmd[cmd.index("--args") + 1])
        staged = state / "outbox" / f"20261114-wf3-reel-a-film-{len(calls):06d}"
        staged.mkdir(parents=True)
        json.dump(dict(run_id=f"r-{len(calls):06d}", workflow="wf3", style=dict(id="reel"), no_repeat=dict(checked=1),
                       script=dict(title="A film", idea_full=dict(title="A film", hook="h", story="s"))), open(staged / "film.json", "w"))
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(dict(delivered=str(staged), args=args)) + "\n")
    monkeypatch.setattr(story.subprocess, "run", run)
    return story, calls


def test_the_driver_records_only_a_delivery_folder_and_a_test_never_writes_into_one(tmp_path, monkeypatch, capsys):
    root = tmp_path / "Story Films"
    monkeypatch.setenv("VALMERA_DELIVERY_ROOTS", str(root))
    story, calls = _driver(tmp_path, monkeypatch)
    # --out outside the delivery folders (a lane test that forgot --test-out): delivered there, never recorded
    monkeypatch.setattr(sys, "argv", ["story.py", "--out", str(tmp_path / "lanes" / "SKILL-WF3"), "Pixar"])
    assert story.main() == 0 and DL.rows() == []
    # the delivery folder under another spelling: recorded where it lands
    monkeypatch.setattr(sys, "argv", ["story.py", "--out", str(tmp_path / "STORY FILMS"), "Pixar"])
    assert story.main() == 0
    got = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    rows = DL.rows("wf3")
    assert len(rows) == 1 and rows[0]["folder"] == got["delivered"] and rows[0]["idea"]["beats"] == ["h", "s"]
    # --test-out inside another spelling of a delivery folder is refused before the shell is started
    for bad in (str(tmp_path / "story films" / "t"), "/Users/masaoodi/Documents/Valmera/story films/t"):
        monkeypatch.setattr(sys, "argv", ["story.py", "--test-out", bad, "Pixar"])
        with pytest.raises(SystemExit) as e:
            story.main()
        assert "inside a delivery folder" in str(e.value)
    assert len(calls) == 2 and len(DL.rows()) == 1
    # a test run: the shell is told it is a test and nothing is recorded
    monkeypatch.setattr(sys, "argv", ["story.py", "--test-out", str(tmp_path / "codex-tests" / "r3-film-out"), "Pixar"])
    assert story.main() == 0
    assert json.loads(calls[-1][calls[-1].index("--args") + 1])["delivered_ledger"] == "off" and len(DL.rows()) == 1


def test_a_remake_without_the_judgement_is_refused_by_the_driver(tmp_path, monkeypatch):
    root = tmp_path / "Story Films"
    monkeypatch.setenv("VALMERA_DELIVERY_ROOTS", str(root))
    staged = tmp_path / "outbox" / "20261114-wf3-archive-woody-df043a"
    staged.mkdir(parents=True)
    json.dump(dict(run_id="11141815-df043a", workflow="wf3", style=dict(id="archive"),
                   script=dict(remake_of="11141632-33f4b8", idea_full=dict(title="The Woody who nearly killed Toy Story"))),
              open(staged / "film.json", "w"))
    final = root / staged.name
    with pytest.raises(DL.NotJudged):
        DL.deliver_once(DL.row_from_film(str(staged), "wf3", folder=str(final)), commit=lambda: os.rename(staged, final))
    assert staged.is_dir() and not final.exists() and DL.rows() == []


def _with_env(story, calls):
    """The stand-in shell, recording the environment each shell is started with."""
    envs, inner = [], story.subprocess.run

    def run(cmd, **kw):
        envs.append(dict(kw.get("env") or {}))
        return inner(cmd, **kw)
    story.subprocess.run = run
    return envs


def test_the_driver_proves_itself_to_its_shell_and_a_test_does_not(tmp_path, monkeypatch, capsys):
    """Hole 4 (15 Nov): the shell leaves the ledger to the driver only with the driver's token (its workflow and pid)."""
    monkeypatch.setenv("VALMERA_DELIVERY_ROOTS", str(tmp_path / "Story Films"))
    story, calls = _driver(tmp_path, monkeypatch)
    envs = _with_env(story, calls)
    monkeypatch.setattr(sys, "argv", ["story.py", "--out", str(tmp_path / "Story Films"), "Pixar"])
    assert story.main() == 0
    assert envs[-1][DL.DRIVER_ENV] == f"wf3:{os.getpid()}" and json.loads(calls[-1][calls[-1].index("--args") + 1])["delivered_ledger"] == "driver"
    monkeypatch.setattr(sys, "argv", ["story.py", "--test-out", str(tmp_path / "codex-tests" / "t"), "Pixar"])
    assert story.main() == 0
    assert DL.DRIVER_ENV not in envs[-1] and len(DL.rows()) == 1


def test_the_driver_reads_both_flag_forms_and_refuses_an_unknown_flag(tmp_path, monkeypatch, capsys):
    """Hole 6 (15 Nov): "--test-out=/path" became part of the topic and the run delivered."""
    monkeypatch.setenv("VALMERA_DELIVERY_ROOTS", str(tmp_path / "Story Films"))
    story, calls = _driver(tmp_path, monkeypatch)
    monkeypatch.setattr(story, "OUT_ROOT", str(tmp_path / "Story Films"))
    test = tmp_path / "codex-tests" / "eq"
    monkeypatch.setattr(sys, "argv", ["story.py", "Claude", f"--test-out={test}", "Shannon"])
    assert story.main() == 0
    got = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    args = json.loads(calls[-1][calls[-1].index("--args") + 1])
    assert args["delivered_ledger"] == "off" and args["line"] == "Claude Shannon" and got["topic"] == "Claude Shannon"
    assert got["delivered"].startswith(str(test)) and DL.rows() == [] and not (tmp_path / "Story Films").exists()
    for argv in (["--test_out", str(test), "Pixar"], ["--testout=" + str(test), "Pixar"], ["--dry", "Pixar"], ["Pixar", "--test"]):
        monkeypatch.setattr(sys, "argv", ["story.py"] + argv)
        with pytest.raises(SystemExit) as e:
            story.main()
        assert "the only flags are --out" in json.loads(str(e.value))["failed"], argv
    assert len(calls) == 1 and DL.rows() == []
    monkeypatch.setattr(sys, "argv", ["story.py", f"--test-out={tmp_path / 'story films' / 't'}", "Pixar"])
    with pytest.raises(SystemExit) as e:
        story.main()
    assert "inside a delivery folder" in str(e.value)
