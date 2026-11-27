import copy
import json
from types import SimpleNamespace

import wf3
import writer


def test_chain_support_can_use_the_preceding_lines_bank_fact():
    script = {"facts": [{"id":"cost", "quote":"They could not afford the toll."},
                        {"id":"route", "quote":"They took the old road to avoid the toll."}],
              "segments": [{"say":"The toll was too steep.", "facts":["cost"]},
                           {"say":"So they took the old road.", "facts":["route"]}]}
    before = copy.deepcopy(script)

    def check(system, content):
        bank = json.loads(content.split("# Verified bank")[1].split("\n", 1)[1].split("\n# Lines")[0])
        assert bank["cost"] == script["facts"][0]["quote"]
        return json.dumps({"lines":[{"i":1, "supported":True, "uses":["route"]}]}), {}

    result, _ = writer.judge_support(script, [1], ask=check,
                                      quotes_by_id={f["id"]: f["quote"] for f in script["facts"]})
    assert result[1]["ok"] and script == before


def test_spine_measures_the_meaning_callback_even_when_a_coda_follows():
    sc = {"segments":[{"say":"They refused him a key."}, {"say":"Now he held their key."},
                      {"say":"He opened the door."}],
          "spine":[{"beat":"hook","claim":"The key changed hands","by":[]}],
          "beats":{"hook":0,"setback":0,"turn":1,"payoff":1,"meaning":1,"coda":2}}

    def measure(system, content):
        rows = json.loads(content.split("its quotes\n")[1])
        close = next(r for r in rows if r["beat"] == "close")
        assert close["line"] == 1 and close["say"] == "Now he held their key."
        assert "reuse" in close["point"][0] and "reuse" in system
        return json.dumps({"beats":[{"beat":"close","carried":True}]}), {}

    report, _ = writer.judge_spine(sc, measure)
    assert report["close"] == {"carried":True, "lost":"", "line":1}


def test_measurements_share_one_draft_and_only_writer_revises(tmp_path, monkeypatch):
    import story_chain
    bank = {"ok":True, "facts":[{"id":"why","quote":"The cost forced a decision.","url":"test"}], "claims":[]}
    sheet = {"chain":"Cost forced a decision.","sheet":[]}
    draft = {"segments":[{"say":"Because the cost rose, she left."}]}
    calls = []
    monkeypatch.setattr(writer, "spine_bank", lambda *a, **kw: (bank, []))
    monkeypatch.setattr(story_chain, "plan", lambda *args: (sheet, {}))

    def write(*args, notes, sheet):
        calls.append((notes,sheet))
        return copy.deepcopy(draft), {}

    monkeypatch.setattr(writer, "write_from_bank", write)
    monkeypatch.setattr(writer, "verify", lambda sc, **kw: {"ok":True})
    monkeypatch.setattr(wf3, "timed", lambda ctx, sc: {"hook_land_s":4.2, "close_s":6})
    monkeypatch.setattr(writer, "judge_support", lambda *a: ({0:{"ok":False,"why":"missing attribution"}}, {}))
    monkeypatch.setattr(writer, "judge_repeats", lambda *a, **kw: ([{"i":0,"why":"echo"}], {}))
    monkeypatch.setattr(writer, "judge_spine", lambda *a: ({"close":{"carried":True}}, {}))
    ctx = SimpleNamespace(run_dir=str(tmp_path), args={}, log=lambda m: None)
    got = wf3.from_spine(ctx, {"title":"The toll"}, {}, [], 0)
    assert got["ok"] and got["script"] == draft
    assert len(calls) == 2 and calls[0] == (None,sheet)
    revision = json.loads(calls[1][0])
    assert revision["draft"] == draft and calls[1][1] == sheet
    assert set(revision["measurements"]) == {"support","repeats","spine","timing","facts"}
    assert revision["measurements"]["timing"]["close_s"] == 6
