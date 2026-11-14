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
