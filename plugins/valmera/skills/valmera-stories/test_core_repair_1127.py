"""Factory fix 64 (27 Nov 2026): a Stories run in a multi-film batch moves on instead of stopping at the script.

Four runs of batch multi-1126-stories stopped at the script stage and delivered nothing:
  214460 (the vacuum): a core line outran its quotes            -> (1) restated as exactly what the quotes state
  5fbbbf (Post-it):    the same, at the final support pass      -> (1)
  4ef100 (Apollo 13):  the turn never carried its point         -> (2) the beat is written again from the bank
  c7d6b0 (Kodak):      the close never carried its point        -> (2)
and each run had only one idea, so the first dead end ended it   -> (3) the next idea, then a replacement idea.

The tests run on copies of the saved stage descriptors (fixtures_fix64/<run>.json: each run's script as it entered the stage
that stopped, its verified bank and its idea). No network, no research, no studio, no narration: every fetch is a stub that
serves the saved quotes, every model call is a stand-in.

    /Users/masaoodi/Documents/Valmera/.valmera/venv-worker/bin/python -m pytest -q test_core_repair_1127.py
"""
import copy
import json
import os
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import writer  # noqa: E402
from test_facts import page, web  # noqa: E402,F401
from test_line_support import answers, judge, use  # noqa: E402


def quiet(m):
    pass


def fixture(rid):
    return json.load(open(os.path.join(HERE, "fixtures_fix64", f"{rid}.json")))


def serve(web, *fx):
    """The saved pages: every saved fact's quote is on its url."""
    by_url = {}
    for f in fx:
        for fact in f["script"]["facts"] + f["bank"]["facts"]:
            by_url.setdefault(fact["url"], set()).add(fact["quote"])
    for url, quotes in by_url.items():
        web.routes[url] = page(*sorted(quotes))


RESTATED = {"214460": "Manufacturers refused to license the bagless design, and Dyson launched it under his own name.",
            "5fbbbf": "Three M gave free samples in Boise and asked the people who tried them if they would buy."}


def rewrite_standin(k, facts, restated, regenerated=None, beat_facts=None):
    """The rewrite model: given the repair note it says what the quotes state; given a plain restatement it answers null
    (what it did on 26 Nov: 'it found no checked quote that says it'); given the regeneration it writes the beat again."""
    def ask(system, content):
        ask.calls.append(content)
        if "The beats to write again" in content:
            beats = [dict(beat=b, say=say, facts=(beat_facts or {}).get(b) or facts, show=[]) for b, say in (regenerated or {}).items()]
            return json.dumps({"beats": beats}), use()
        if "# Repair" in content:
            return json.dumps({"segments": [{"i": k, "say": restated, "facts": facts, "show": []}]}), use()
        return json.dumps({"segments": [{"i": k, "say": None}]}), use()
    ask.calls = []
    return ask


# ----------------------------------------------------------------- (1) a core line that outruns its quotes is restated

def test_1_hold_a_close_that_outruns_its_quotes_is_restated_as_what_the_quotes_state(web):
    fx = fixture("214460")
    serve(web, fx)
    sc, bank = fx["script"], fx["bank"]
    bad = sc["segments"][14]["say"]
    assert bad.startswith("The bagless design manufacturers refused to license became")
    j = judge(lambda i, say: (False, "the quote says the design was used in the DC01, not that it became the bestseller", [])
              if say == bad else (True, "", []))
    ask = rewrite_standin(14, sc["segments"][14]["facts"], RESTATED["214460"])
    point = answers({"beats": [dict(beat=b, carried=True) for b in ("hook", "turn", "payoff", "close")]})
    held, rep, _, _ = writer.hold_spine(sc, bank, None, log=quiet, ask_judge=j, ask_rewrite=ask, ask_point=point,
                                        ask_repeats=answers({"repeats": []}), again=lambda b, c, w: (b, []))
    assert rep["ok"], rep["why"]                                         # the old engine: why == "core_unsupported"
    assert held["segments"][14]["say"] == RESTATED["214460"] and held["close_line"] == RESTATED["214460"]
    assert rep["core_repaired"][0]["before"] == bad and rep["core_repaired"][0]["after"] == RESTATED["214460"]
    assert not rep["core_unsupported"] and any("# Repair" in c for c in ask.calls)
    assert held["segments"][14]["facts"] and writer.verify(held)["ok"]       # the new line cites verified quotes only


def test_1_final_a_core_line_that_outruns_its_quotes_is_restated_at_the_final_pass(web):
    fx = fixture("5fbbbf")
    serve(web, fx)
    sc = fx["script"]
    bad = sc["segments"][12]["say"]
    assert bad.startswith("The note would peel off")
    j = judge(lambda i, say: (False, "the quotes do not say 3M used the giveaway to find out whether people would buy", [])
              if say == bad else (True, "", []))
    ask = rewrite_standin(12, sc["segments"][12]["facts"], RESTATED["5fbbbf"])
    point = answers({"lines": [{"i": 12, "kept": True}]})
    sc2, rep, _ = writer.final_support(sc, log=quiet, ask_judge=j, ask_rewrite=ask, ask_repeats=answers({"repeats": []}), ask_point=point)
    assert rep["ok"], rep["why"]
    assert sc2["segments"][12]["say"] == RESTATED["5fbbbf"] and sc2["close_line"] == RESTATED["5fbbbf"]
    assert [(r["before"], r["after"]) for r in rep["core_repaired"]] == [(bad, RESTATED["5fbbbf"])]
    assert rep["final_lines"][12]["supported"] and not rep["core_unsupported"]


def test_1_a_core_line_no_quote_can_stand_still_fails_with_the_judges_reason(web):
    """The only true impossibility: the restated line is judged unsupported as well."""
    fx = fixture("5fbbbf")
    serve(web, fx)
    sc = fx["script"]
    j = judge(lambda i, say: (False, "no quote says it", []) if i == 12 else (True, "", []))
    ask = rewrite_standin(12, sc["segments"][12]["facts"], RESTATED["5fbbbf"])
    sc2, rep, _ = writer.final_support(sc, log=quiet, ask_judge=j, ask_rewrite=ask, ask_point=answers({"lines": []}))
    assert not rep["ok"] and rep["why"] == "core_unsupported" and "no quote says it" in rep["core_unsupported"]["12"]["why"]


# ----------------------------------------------------------------- (2) a beat that does not carry the point is written again

@pytest.mark.parametrize("rid, beat, k", [("4ef100", "turn", 7), ("c7d6b0", "close", 11)])
def test_2_a_beat_that_never_carries_its_point_is_written_again_from_the_bank(web, rid, beat, k):
    fx = fixture(rid)
    serve(web, fx)
    sc, bank = fx["script"], fx["bank"]
    segs = sc["segments"]
    facts = {b: segs[line]["facts"] for b, line in writer.spine_lines(sc).items()}
    again = "THE POINT, said from the bank's own quotes."

    def point(system, content):
        rows = json.loads(content.split("its quotes\n", 1)[1])
        return json.dumps({"beats": [dict(beat=r["beat"], carried=(r["beat"] != beat or again in (r["say"] or "")),
                                          lost="it never says the collision") for r in rows]}), use()
    # the plain rewrite restates the line without winning the point back (what happened in all three steps on 26 Nov)
    def ask(system, content):
        if "The beats to write again" in content:
            return rewrite_standin(k, segs[k]["facts"], "", {beat: again}, facts)(system, content)
        return json.dumps({"segments": [{"i": g, "say": f"Still a line without the point {g}.", "facts": segs[g]["facts"], "show": []}
                                         for g in (k,)]}), use()
    held, rep, _, _ = writer.hold_spine(sc, bank, None, log=quiet, ask_judge=judge(lambda i, say: (True, "", [])), ask_rewrite=ask,
                                        ask_point=point, ask_repeats=answers({"repeats": []}), again=lambda b, c, w: (b, []))
    assert rep["ok"], (rep["why"], rep["spine_failed"])                      # the old engine: "the spine's point is not carried"
    assert held["segments"][k]["say"] == again
    assert rep["spine_regenerated"][0]["beats"] == [beat] and rep["spine_regenerated"][0]["lines"][beat] == again


def test_2_a_beat_the_bank_cannot_carry_even_written_again_is_reported_not_kept(web):
    fx = fixture("4ef100")
    serve(web, fx)
    sc, bank = fx["script"], fx["bank"]
    point = answers({"beats": [dict(beat=b, carried=(b != "turn"), lost="flat") for b in ("hook", "turn", "payoff", "close")]})
    k = writer.spine_lines(sc)["turn"]
    ask = rewrite_standin(k, sc["segments"][k]["facts"], "", {"turn": "Another flat turn."})
    held, rep, _, _ = writer.hold_spine(sc, bank, None, log=quiet, ask_judge=judge(lambda i, say: (True, "", [])), ask_rewrite=ask,
                                        ask_point=point, again=lambda b, c, w: (b, []))
    assert not rep["ok"] and "turn" in rep["why"] and not rep.get("spine_regenerated")


# ----------------------------------------------------------------- (3) the run moves to the next idea, then a replacement

def stage(web, tmp_path, monkeypatch, fixtures, hold_ok, replacements=(), final_ok=lambda t: True):
    """wf3.script on saved descriptors: the bank, the script and the idea of each fixture; hold_ok(title) says whether the
    spine of that idea holds. Returns (wf3, ctx, ideas_out, log)."""
    import wf3
    serve(web, *fixtures)
    by = {f["idea"]["title"]: f for f in fixtures}
    monkeypatch.setattr(writer, "spine_bank", lambda idea, log=print, **k: (copy.deepcopy(by[idea["title"]]["bank"]), [use()]))
    monkeypatch.setattr(writer, "write_from_bank", lambda idea, card, form, bank, voices=None, notes=None, **k:
                        (copy.deepcopy(by[idea["title"]]["script"]), use()))
    monkeypatch.setattr(writer, "hold_spine", lambda sc, bank, idea=None, log=print, again=None, **k: (
        sc, dict(ok=hold_ok(sc["title"]), why="the spine's point is not carried", verify=writer.verify(sc), spine=[]), [], bank))
    monkeypatch.setattr(writer, "final_support", lambda sc, log=print, style=None, **k: (
        sc, dict(ok=final_ok(sc["title"]), why="core_unsupported", core_unsupported={"12": {}}, verify=writer.verify(sc)), []))
    monkeypatch.setattr(wf3, "able_cards", lambda ctx: [dict(id="window", name="Window", look="framed", buildable=True),
                                                        dict(id="archive", name="Archive", look="grain", buildable=True)])
    swept = []
    monkeypatch.setattr(wf3, "replacement_ideas", lambda ctx, avoid, able, style, spend: (swept.append(list(avoid)), list(replacements))[1])
    log = []
    ctx = types.SimpleNamespace(run_dir=str(tmp_path), log=log.append, args={"ear": False, "annotate": False, "timing": False})
    ideas_out = dict(ideas=[dict(f["idea"], supply=dict(ok=True)) for f in fixtures if f not in replacements])
    return wf3, ctx, ideas_out, log, swept


def test_3_a_run_whose_only_idea_cannot_stand_takes_a_replacement_idea(web, tmp_path, monkeypatch):
    dead, good = fixture("214460"), fixture("4ef100")
    rep_idea = dict(good["idea"], supply=dict(ok=True))
    wf3, ctx, ideas_out, log, swept = stage(web, tmp_path, monkeypatch, [dead, good], lambda t: t == good["script"]["title"], [rep_idea])
    ideas_out["ideas"] = [dict(dead["idea"], supply=dict(ok=True))]
    r = wf3.script(ctx, ideas_out)                                          # the old stage: "no idea's spine stands on verified quotes"
    assert r["idea"] == good["idea"]["title"] and [t["title"] for t in r["ideas_tried"]] == [dead["idea"]["title"]]
    assert swept == [[dead["idea"]["title"]]] and any("replacement round 1" in m for m in log)


def test_3_every_idea_of_the_list_is_tried_not_only_the_first_three(web, tmp_path, monkeypatch):
    fxs = [fixture("214460"), fixture("5fbbbf"), fixture("c7d6b0"), fixture("4ef100")]
    wf3, ctx, ideas_out, log, swept = stage(web, tmp_path, monkeypatch, fxs, lambda t: t == fxs[3]["script"]["title"])
    r = wf3.script(ctx, ideas_out)
    assert r["idea"] == fxs[3]["idea"]["title"] and len(r["ideas_tried"]) == 3 and not swept


def test_3_an_idea_whose_script_fails_the_final_support_pass_gives_way_to_the_next(web, tmp_path, monkeypatch):
    first, second = fixture("5fbbbf"), fixture("c7d6b0")
    wf3, ctx, ideas_out, log, swept = stage(web, tmp_path, monkeypatch, [first, second], lambda t: True,
                                            final_ok=lambda t: t != first["script"]["title"])
    r = wf3.script(ctx, ideas_out)
    assert r["idea"] == second["idea"]["title"] and "final support pass" in r["ideas_tried"][0]["why"]


def test_3_only_when_no_idea_can_be_found_the_stage_stops_with_a_precise_reason(web, tmp_path, monkeypatch):
    dead = fixture("214460")
    wf3, ctx, ideas_out, log, swept = stage(web, tmp_path, monkeypatch, [dead], lambda t: False)
    with pytest.raises(RuntimeError, match=r"no idea's spine stands on verified quotes .*1 ideas tried, replacements searched 2 times"):
        wf3.script(ctx, ideas_out)
    assert len(swept) == wf3.REPLACEMENT_ROUNDS


def test_3_replacement_ideas_avoid_what_was_tried_and_keep_the_delivery_style(web, tmp_path, monkeypatch):
    import wf3
    seen = {}

    def find(form, cards, avoid=(), line=None, n=3, delivered=""):
        seen.update(cards=[c["id"] for c in cards], avoid=list(avoid))
        return [dict(title="New one", style="archive", subject="x"), dict(title="New two", style="window", subject="y")], dict(web_searches=1)
    monkeypatch.setattr(writer, "find_ideas", find)
    monkeypatch.setattr(wf3, "ledger_rows", lambda ctx: [dict(idea="Delivered one")])
    monkeypatch.setattr(wf3, "probe", lambda i: dict(ok=True, covered=3, pictures=6))
    monkeypatch.setattr(wf3, "spine_probe", lambda i: dict(beats=dict(hook=True), share=1.0, ok=True))
    monkeypatch.setattr(wf3.DL, "prompt_block", lambda wf: "")
    (tmp_path / "batch" / "work" / "r1").mkdir(parents=True)
    json.dump(dict(ideas=[dict(title="Sibling film", subject="sibling")]), open(tmp_path / "batch" / "plan.json", "w"))
    ctx = types.SimpleNamespace(run_dir=str(tmp_path / "batch" / "work" / "r1"), log=quiet, workflow="wf3",
                                args=dict(line=None, shared_style_root="/x", batch_previous=["Earlier"]))
    able = {"window": dict(id="window"), "archive": dict(id="archive")}
    spend = []
    out = wf3.replacement_ideas(ctx, ["Tried one"], able, "window", spend)
    assert seen["cards"] == ["window"] and {"Tried one", "Delivered one", "Earlier", "sibling"} <= set(seen["avoid"])
    assert [i["style"] for i in out] == ["window", "window"] and spend
