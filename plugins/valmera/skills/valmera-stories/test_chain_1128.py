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
          "beats":{"hook":0,"setback":0,"turn":1,"payoff":1,"meaning":1,"coda":2},
          "beat_sheet":{"hook_word":"key","meaning_line":"Now he held their key."}}

    def measure(system, content):
        rows = json.loads(content.split("its quotes\n")[1])
        close = next(r for r in rows if r["beat"] == "close")
        assert close["line"] == 1 and close["say"] == "Now he held their key."
        assert "reuse" in close["point"][0] and "reuse" in system
        assert (close["hook_word"],close["planned_meaning"]) == ("key","Now he held their key.")
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
    again = wf3.from_spine(ctx, {"title":"The toll"}, {}, [], 0)
    assert again["script"] == draft and len(calls) == 2


def test_timing_reads_only_the_hook_and_meaning_and_keeps_their_positions(tmp_path):
    sc = {"beats":{"meaning":2}, "segments":[{"say":"A key.","lands":"key"},{"say":"He left."},
                                            {"say":"He held the key."},{"say":"The door opened."}]}
    def read(ctx, sample, out):
        assert [s["say"] for s in sample["segments"]] == ["A key.","He held the key."]
        return {"words": {"words": [{"seg":0, "w":"A", "start":0.2,"end":0.5},
                                    {"seg":0, "w":"key.","start":0.6,"end":1.1},
                                    {"seg":1, "w":"He","start":1.8,"end":2.0},
                                    {"seg":1, "w":"key.","start":3.0,"end":3.4}]}}
    result = wf3.timed(SimpleNamespace(args={}, supply=str(tmp_path)), sc, narrate=read)
    assert result["segment_indexes"] == [0,2]
    assert result["hook_land_s"] == .9 and result["close_s"] == 1.6


def test_verified_bank_quote_remains_attributed_when_the_archived_page_is_unavailable(monkeypatch):
    fact = {"id":"quote","url":"https://example.org/archive","quote":"The lenders closed the laboratory."}
    receipt = {"read":"verified bank: wayback","source":fact["url"]}
    previous = {(fact["url"],fact["quote"]):receipt}
    sc = {"facts":[fact],"segments":[{"say":fact["quote"],"facts":["quote"]}]}
    monkeypatch.setattr(writer,"check_fact",lambda f:{"id":f["id"],"found":False,"error":"HTTP 403"})
    verified = writer.verify(sc, previous)
    assert verified["ok"] and verified["facts"][0]["read"] == receipt["read"]
    assert verified["facts"][0]["source"] == fact["url"]
    monkeypatch.setattr(writer,"check_fact",lambda f:{"id":f["id"],"found":False,"missing":"quote absent"})
    assert not writer.verify(sc, previous)["ok"]
