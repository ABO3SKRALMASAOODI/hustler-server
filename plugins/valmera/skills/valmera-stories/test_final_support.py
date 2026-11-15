"""The final support pass (15 Nov 2026, the second independent check of the fact gate): the ear rewrote every line after
settle and nothing judged them again, so the delivered script said what no quote holds ("Pixar had never made a movie
this long"). Now every line is judged on its verified quotes after the ear, quoted speech is matched word for word (never
a substring), a span the judge copied loosely fails closed, and a core line that lost its point is rewritten once more.

    /Users/masaoodi/Documents/Valmera/.valmera/venv-worker/bin/python -m pytest -q test_final_support.py

No network and no model: every fetch goes through a stubbed writer._get, every model call is a stand-in.
"""
import json
import os
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import writer  # noqa: E402
import ear  # noqa: E402
from test_facts import GOOD, page, script, supported, web  # noqa: E402,F401
from test_line_support import answers, judge, use  # noqa: E402

INTERESTED = "Hanks told the studio, I'm interested in the part, and waited."
SAID_IN = "And I said, I'm in."
FIRST_FEATURE = "Toy Story was Pixar's first feature film."
CHANGES = "Lasseter's team went back to make the necessary changes."
HALT = "Katzenberg and Schneider halted production on Toy Story."
EDGE = "Katzenberg pushed for an edgier Woody."


def quiet(m):
    pass


def pages(web, *quotes):
    web.routes[GOOD] = page(*quotes)


def fact(i, q):
    return dict(id=i, claim=q, quote=q, url=GOOD)


# ----------------------------------------------------------------- defect A: word-bounded verbatim matching

def test_defect_a_a_quoted_span_must_match_whole_words_never_a_substring():
    seg = dict(say="He told them, I'm in.", facts=["f1"])
    assert writer.speech_backed("I'm in", seg, {"f1": INTERESTED})            # "I'm in" is not in "I'm interested in"
    assert writer.speech_backed("I'm in", seg, {"f1": SAID_IN}) is None
    assert writer.speech_backed("I’M IN", seg, {"f1": SAID_IN}) is None       # case and the curly apostrophe are one form
    assert not writer.has_words("I'm interested in the part", "I'm in")
    assert not writer.has_words("the cartoon", "car")
    assert writer.has_words("Ｃafé ﬁlm, I’m in.", "café film I'm in")          # Unicode-normalised


def test_defect_a_a_line_whose_quoted_words_only_prefix_a_quote_is_unsupported_whatever_the_judge_says(web):
    pages(web, INTERESTED, FIRST_FEATURE)
    sc = script([fact("f1", INTERESTED), fact("f2", FIRST_FEATURE)],
                [dict(say="Toy Story was Pixar's first feature.", beat="hook", facts=["f2"], show=[]),
                 dict(say="Hanks said it plainly. I'm in.", beat="setup", facts=["f1"], show=[],
                      quotes=[dict(words="I'm in", who="Tom Hanks", fact="f1")])])
    got, _ = writer.judge_support(sc, [0, 1], ask=judge(lambda i, say: (True, "", [])))
    assert got[0]["ok"] and not got[1]["ok"] and "I'm in" in got[1]["why"]
    assert writer.verify(sc)["speech_unbacked"][0]["words"] == "I'm in"


# ----------------------------------------------------------------- defect B: a loosely copied span fails closed

def test_defect_b_a_span_the_judge_copied_loosely_makes_the_line_unsupported(web):
    """The quote holds "I'm in"; the judge calls the line supported but copies the span as "Im in". That span stands
    verbatim in no verified quote, so the line counts as unsupported (fail closed), whatever the verdict says."""
    pages(web, SAID_IN)
    sc = script([fact("f1", SAID_IN)], [dict(say="He said, I'm in.", beat="hook", facts=["f1"], show=[])])
    loose = judge(lambda i, say: (True, "", [{"words": "Im in", "who": "Tom Hanks"}]))
    got, _ = writer.judge_support(sc, [0], ask=loose)
    assert not got[0]["ok"] and got[0]["bad_speech"][0]["words"] == "Im in"
    exact = judge(lambda i, say: (True, "", [{"words": "I'm in", "who": "Tom Hanks"}]))
    assert writer.judge_support(sc, [0], ask=exact)[0][0]["ok"]


# ----------------------------------------------------------------- the final pass after the ear

def woody(web):
    pages(web, FIRST_FEATURE, CHANGES, HALT, EDGE)
    facts = [fact("f1", FIRST_FEATURE), fact("f2", CHANGES), fact("f3", HALT), fact("f4", EDGE)]
    segs = [dict(say="Katzenberg wanted an edgier Woody. Then he shut Toy Story down.", beat="hook", facts=["f4", "f3"], show=[]),
            dict(say="It was Pixar's first feature film.", beat="setup", facts=["f1"], show=[]),
            dict(say="So the team went back to make the necessary changes.", beat="turn", facts=["f2"], show=[]),
            dict(say="Toy Story was Pixar's first feature.", beat="close", facts=["f1"], show=[])]
    return script(facts, segs)


def ear_model(rewrites, changed=()):
    """writer.ask_openai stand-in for the ear: its rewrite (by segment index) and its audit."""
    def ask(system, content, **k):
        ask.calls.append((k.get("what"), k.get("model"), content))
        if k.get("what") == "ear":
            segs = json.loads(content.split("# The script, segment by segment\n", 1)[1].split("\n\nNever say", 1)[0])
            out = []
            for v in segs:
                say = rewrites.get(v["i"], v["say"])
                out.append(dict(i=v["i"], say=say, emphasis=[say.split()[-1].strip(".")], pause_after=0.22, melody="falling",
                                pace="steady", direction="even"))
            return json.dumps({"segments": out}), use()
        return json.dumps({"changed": [{"i": i, "why": "x"} for i in changed]}), use()
    ask.calls = []
    return ask


def test_an_ear_rewrite_that_adds_a_fact_is_caught_by_the_final_pass(web, monkeypatch):
    """The ear turns "It was Pixar's first feature film." into "Pixar had never made a movie this long." and its own
    auditor waves it through. The final pass judges EVERY line in one call, finds the added claim, rewrites the line on
    its quote in the ear's style, judges it again, and the final script holds no claim the quotes do not state."""
    sc = woody(web)
    model = ear_model({1: "Pixar had never made a movie this long."})
    monkeypatch.setattr(writer, "ask_openai", model)
    sc_ear, er = ear.for_ear(sc, "story", log=quiet)
    assert er["ok"] and sc_ear["segments"][1]["say"] == "Pixar had never made a movie this long."
    ear_prompt = model.calls[0][2]
    assert model.calls[0][1] == ear.EAR_MODEL and FIRST_FEATURE in ear_prompt and "its_quotes" in ear_prompt
    assert FIRST_FEATURE in model.calls[1][2]                     # the auditor reads the quotes too

    j = judge(lambda i, say: (False, "the quote says only first feature, never 'this long'", []) if "this long" in say
              else (True, "", []))
    fix = answers({"segments": [{"i": 1, "say": "It was Pixar's very first feature film.", "facts": ["f1"], "show": []}]})
    sc2, rep, spend = writer.final_support(sc_ear, log=quiet, ask_judge=j, ask_rewrite=fix, ask_repeats=answers({"repeats": []}),
                                           ask_point=answers({"lines": []}), style=ear.KEEP_STYLE)
    assert rep["ok"] and rep["verify"]["ok"]
    says = [s["say"] for s in sc2["segments"]]
    assert not any("this long" in s for s in says) and says[1] == "It was Pixar's very first feature film."
    first = json.loads(j.calls[0].split("each one cites\n", 1)[1])
    assert [x["i"] for x in first] == [0, 1, 2, 3]                  # every line, one batched call
    assert ear.KEEP_STYLE in fix.calls[0]                           # the restatement keeps the ear's voice
    assert all(x["supported"] for x in rep["final_lines"]) and [x["restated"] for x in rep["final_lines"]] == [False, True, False, False]
    assert sc2["segments"][1]["emphasis"] == []                     # the old stress word ("long") is not in the new line
    assert all(k not in s for s in sc2["segments"] for k in ("_sid", "_check", "_origin"))
    assert rep["usd"] > 0 and spend


def test_a_line_still_unsupported_after_two_rewrites_is_dropped_and_a_core_line_fails_the_pass(web):
    sc = woody(web)
    sc["segments"][2]["say"] = "So they went back to rewrite the story."
    j = judge(lambda i, say: (False, "the quote says make the necessary changes", []) if "rewrite" in say else (True, "", []))
    stubborn = answers({"segments": [{"i": 2, "say": "So they went back to rewrite it all.", "facts": ["f2"], "show": []}]})
    sc2, rep, _ = writer.final_support(sc, log=quiet, ask_judge=j, ask_rewrite=stubborn, ask_point=answers({"lines": []}))
    assert not rep["ok"] and rep["why"] == "core_unsupported" and "2" in rep["core_unsupported"]     # a turn line
    sc["segments"][2]["beat"] = "setup"
    sc2, rep, _ = writer.final_support(sc, log=quiet, ask_judge=j, ask_rewrite=stubborn, ask_repeats=answers({"repeats": []}),
                                       ask_point=answers({"lines": []}))
    assert rep["ok"] and len(sc2["segments"]) == 3 and rep["support_dropped"][0]["say"].startswith("So they went back")
    assert [c["round"] for c in rep["lines_checked"] if c["sid"] == 2] == [0, 1, 2]


def test_the_wrong_actor_is_caught_and_restated_on_the_names_the_quote_gives(web):
    sc = woody(web)
    sc["segments"][0]["say"] = "Disney wanted an edgier Woody. Then it shut Toy Story down."
    j = judge(lambda i, say: (False, "the quotes name Katzenberg and Schneider, not Disney", []) if "Disney" in say else (True, "", []))
    fix = answers({"segments": [{"i": 0, "say": "Katzenberg wanted an edgier Woody. Then he shut Toy Story down.",
                                 "facts": ["f4", "f3"], "show": []}]})
    sc2, rep, _ = writer.final_support(sc, log=quiet, ask_judge=j, ask_rewrite=fix, ask_repeats=answers({"repeats": []}),
                                       ask_point=answers({"lines": [{"i": 0, "kept": True}]}))
    assert rep["ok"] and sc2["segments"][0]["say"].startswith("Katzenberg") and sc2["hook_line"] == sc2["segments"][0]["say"]


# ----------------------------------------------------------------- the hook keeps its point

def flat_hook(web):
    sc = woody(web)
    sc["segments"][0]["say"] = "Katzenberg asked for changes to Woody, and work on the film paused."
    sc["segments"][0]["say_written"] = "Katzenberg wanted an edgier Woody. Then he shut Toy Story down."
    return sc


def test_a_hook_that_lost_its_point_in_the_rewrite_is_rewritten_once_to_win_it_back(web):
    sc = flat_hook(web)
    point = answers({"lines": [{"i": 0, "kept": False, "lost": "the collision: wanted edge, then the shutdown"}]},
                    {"lines": [{"i": 0, "kept": True, "lost": ""}]})
    back = "Katzenberg wanted an edgier Woody. Then Toy Story was halted."
    fix = answers({"segments": [{"i": 0, "say": back, "facts": ["f4", "f3"], "show": []}]})
    j = supported()
    sc2, rep, _ = writer.final_support(sc, log=quiet, ask_judge=j, ask_rewrite=fix, ask_repeats=answers({"repeats": []}),
                                       ask_point=point)
    assert rep["ok"] and sc2["segments"][0]["say"] == back
    assert "Katzenberg wanted an edgier Woody. Then he shut Toy Story down." in point.calls[0]
    assert "it lost the point" in fix.calls[0] and "the collision" in fix.calls[0]
    assert [p["kept"] for p in rep["point"]] == [False, True] and rep["point_rewritten"] == [0]
    assert len(j.calls) == 2 and json.loads(j.calls[1].split("each one cites\n", 1)[1])[0]["say"] == back   # judged for support


def test_a_point_rewrite_that_is_not_supported_is_not_kept_and_the_held_line_comes_back(web):
    """The new words do not stand: they are not kept, the reason is recorded, and the line the spine was held on (its
    first words, which still stand on their quotes) comes back instead of the flat one (15 Nov, the spine check)."""
    sc = flat_hook(web)
    point = answers({"lines": [{"i": 0, "kept": False, "lost": "the shutdown"}]})
    fix = answers({"segments": [{"i": 0, "say": "Disney killed Toy Story overnight.", "facts": ["f3"], "show": []}]})
    j = judge(lambda i, say: (False, "Disney, overnight: no quote", []) if "Disney" in say else (True, "", []))
    sc2, rep, _ = writer.final_support(sc, log=quiet, ask_judge=j, ask_rewrite=fix, ask_point=point)
    assert rep["ok"] and sc2["segments"][0]["say"] == "Katzenberg wanted an edgier Woody. Then he shut Toy Story down."
    assert rep["point_kept_old"] == [0] and rep["point_restored"] == [0] and all(x["supported"] for x in rep["final_lines"])
    assert "do not stand on their quotes: Disney, overnight" in rep["point_failed"][0]["why"]
    strict = judge(lambda i, say: (False, "no quote", []) if "Disney" in say or "edgier" in say else (True, "", []))
    sc3, rep3, _ = writer.final_support(sc, log=quiet, ask_judge=strict, ask_rewrite=fix, ask_point=point)
    assert rep3["ok"] and sc3["segments"][0]["say"] == "Katzenberg asked for changes to Woody, and work on the film paused."
    assert rep3["point_kept_old"] == [0] and not rep3["point_restored"]          # the held line does not stand either


def test_an_unchanged_core_line_is_not_sent_to_the_point_judge(web):
    sc = woody(web)
    point = answers({"lines": []})
    sc2, rep, _ = writer.final_support(sc, log=quiet, ask_judge=supported(), ask_point=point)
    assert rep["ok"] and not point.calls and [s["say"] for s in sc2["segments"]] == [s["say"] for s in sc["segments"]]


# ----------------------------------------------------------------- the stage

def test_the_stage_fails_when_the_final_pass_does_not_stand_and_freezes_the_words_when_it_does(web, tmp_path):
    import wf3
    sc = woody(web)
    ctx = types.SimpleNamespace(run_dir=str(tmp_path), log=quiet, args={})
    bad = lambda sc_: (sc_, dict(ok=False, why="core_unsupported", core_unsupported={"0": {}}), [use()])  # noqa: E731
    with pytest.raises(RuntimeError, match="final support"):
        wf3.final_gate(ctx, sc, dict(ok=True), [], final=bad)
    spend = []
    sc2, rep = wf3.final_gate(ctx, sc, dict(ok=True, settle=dict(replaced=[])), spend,
                              final=lambda sc_: writer.final_support(sc_, log=quiet, ask_judge=supported()))
    assert rep["ok"] and rep["settle"]["final"]["ok"] and spend[0]["stage"] == "final-support"
    assert json.load(open(tmp_path / "script_final.json"))["report"]["final_lines"][0]["supported"]
