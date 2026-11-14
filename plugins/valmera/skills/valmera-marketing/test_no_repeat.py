"""Never the exact same idea told the same way (owner, 14 Nov 2026), in workflow 2's ideas prompt and its driver.

    /Users/masaoodi/Documents/Valmera/.valmera/venv-worker/bin/python -m pytest -q test_no_repeat.py

No network and no model: the shell's subprocess is a stand-in.
"""
import importlib.util
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "_shell"))
os.environ.setdefault("MARKET_NO_REEXEC", "1")
import delivered as DL  # noqa: E402


def _driver(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("market_driver_under_test", os.path.join(HERE, "market.py"))
    market = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(market)
    state = tmp_path / "state"
    monkeypatch.setattr(market, "STATE_ROOT", str(state))
    monkeypatch.setattr(market, "environment", lambda: {})
    monkeypatch.setattr(market, "prepare_state", lambda: None)
    monkeypatch.setattr(market, "stopped_run", lambda topic: None)
    monkeypatch.setattr(market, "sources_md", lambda folder: None)
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        staged = state / "outbox" / f"20261114-wf2-reel-a-film-{len(calls):06d}"
        staged.mkdir(parents=True)
        json.dump(dict(run_id=f"r-{len(calls):06d}", workflow="wf2", style=dict(id="reel"), no_repeat=dict(checked=1),
                       script=dict(title="Show the brands", idea_full=dict(title="Show the brands", pain="p", claim="c", hook="h"))),
                  open(staged / "film.json", "w"))
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(dict(delivered=str(staged))) + "\n")
    monkeypatch.setattr(market.subprocess, "run", run)
    return market, calls


def test_the_driver_records_only_a_delivery_folder_and_a_test_never_writes_into_one(tmp_path, monkeypatch, capsys):
    root = tmp_path / "Marketing Films"
    monkeypatch.setenv("VALMERA_DELIVERY_ROOTS", str(root))
    market, calls = _driver(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["market.py", "--out", str(tmp_path / "lanes" / "SKILL-WF2")])
    assert market.main() == 0 and DL.rows() == []                            # a lane test: never recorded
    monkeypatch.setattr(sys, "argv", ["market.py", "--out", str(tmp_path / "marketing films")])
    assert market.main() == 0
    got = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    rows = DL.rows("wf2")
    assert len(rows) == 1 and rows[0]["folder"] == got["delivered"] and rows[0]["idea"]["subject"] == "p"
    for bad in (str(tmp_path / "MARKETING FILMS" / "t"), "/Users/masaoodi/Documents/Valmera/marketing films/t"):
        monkeypatch.setattr(sys, "argv", ["market.py", "--test-out", bad])
        with pytest.raises(SystemExit) as e:
            market.main()
        assert "inside a delivery folder" in str(e.value)
    monkeypatch.setattr(sys, "argv", ["market.py", "--test-out", str(tmp_path / "codex-tests" / "wf2")])
    assert market.main() == 0
    assert json.loads(calls[-1][calls[-1].index("--args") + 1])["delivered_ledger"] == "off" and len(DL.rows()) == 1


def test_the_ideas_prompt_block_carries_the_owners_rule():
    DL.append(DL.make_row("wf2", "/x/f", [], dict(title="Show the brands he names", style="archive")))
    b = DL.prompt_block("wf2")
    assert "Show the brands he names" in b and DL.TITLE_ONLY in b
    assert "Never choose the exact same idea told the same way" in b and "even under a new title or a new visual look" in b


def test_the_driver_proves_itself_to_its_shell_reads_both_flag_forms_and_refuses_an_unknown_flag(tmp_path, monkeypatch, capsys):
    """Holes 4 and 6 (15 Nov): the driver's token reaches its shell; --test-out=<dir> is a test, never topic text."""
    monkeypatch.setenv("VALMERA_DELIVERY_ROOTS", str(tmp_path / "Marketing Films"))
    market, calls = _driver(tmp_path, monkeypatch)
    monkeypatch.setattr(market, "OUT_ROOT", str(tmp_path / "Marketing Films"))
    envs, inner = [], market.subprocess.run

    def run(cmd, **kw):
        envs.append(dict(kw.get("env") or {}))
        return inner(cmd, **kw)
    monkeypatch.setattr(market.subprocess, "run", run)
    test = tmp_path / "codex-tests" / "eq"
    monkeypatch.setattr(sys, "argv", ["market.py", f"--test-out={test}", "captions", "that", "pop"])
    assert market.main() == 0
    got = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    args = json.loads(calls[-1][calls[-1].index("--args") + 1])
    assert args["delivered_ledger"] == "off" and args["line"] == "captions that pop" and DL.DRIVER_ENV not in envs[-1]
    assert got["delivered"].startswith(str(test)) and DL.rows() == [] and not (tmp_path / "Marketing Films").exists()
    for argv in (["--test_out", str(test)], ["--dry-run", "captions"], ["captions", "--out"], ["--out=x", "--test-out=y"]):
        monkeypatch.setattr(sys, "argv", ["market.py"] + argv)
        with pytest.raises(SystemExit) as e:
            market.main()
        assert json.loads(str(e.value))["failed"], argv
    assert len(calls) == 1
    monkeypatch.setattr(sys, "argv", ["market.py", "captions"])
    assert market.main() == 0
    assert envs[-1][DL.DRIVER_ENV] == f"wf2:{os.getpid()}" and json.loads(calls[-1][calls[-1].index("--args") + 1])["delivered_ledger"] == "driver"
    assert len(DL.rows("wf2")) == 1


def _proof():
    path, mods = list(sys.path), dict(sys.modules)
    spec = importlib.util.spec_from_file_location("wf_proof_no_repeat", os.path.join(HERE, "proof.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    sys.path[:] = path
    for k in [k for k in sys.modules if k not in mods]:
        del sys.modules[k]
    return m


def test_the_live_engine_carries_the_owners_rule_and_the_shared_delivered_ideas(tmp_path, monkeypatch):
    """Hole 2 (15 Nov): proof.py is the engine market.py runs (WF2_ENGINE=proof); its ideas prompt carries the owner's rule
    and the delivered ledger's ideas for wf2, the same block as wf2.py."""
    P = _proof()
    DL.append(DL.make_row("wf2", "/x/brands", [], dict(title="Show the brands he names", angle="b-roll on every brand",
                                                       beats=["he names brands", "you see them"])))
    seen = {}
    monkeypatch.setattr(P, "able_cards", lambda ctx: [dict(id="reel", look="l", hook="h")])
    monkeypatch.setattr(P, "sources", lambda: [dict(id="s1", file="/x/s1.mp4")])
    monkeypatch.setattr(P, "ledger_rows", lambda ctx: [dict(idea="Show the brands he names", source="/x/s1.mp4", window=[10, 40], style="reel")])
    monkeypatch.setattr(P, "transcript_windows", lambda s, used: [])
    monkeypatch.setattr(P, "references", lambda: [])
    monkeypatch.setattr(P, "ref_pace", lambda: {})

    def ask(system, content, **kw):
        seen["content"] = content
        return dict(ideas=[dict(title="t", source="s1", asks=["a"], style="reel")]), dict(usd=0)
    monkeypatch.setattr(P, "ask", ask)

    class Ctx:
        workflow = "wf2"
        args = {}
    P.ideas(Ctx())
    c = seen["content"]
    assert "never the same pain with the same clip" not in c
    assert "Never the exact same film twice (the owner's rule): never the same idea told the same way" in c
    assert DL.prompt_block("wf2") and DL.prompt_block("wf2") in c and "b-roll on every brand" in c
    assert "Never choose the exact same idea told the same way" in c
