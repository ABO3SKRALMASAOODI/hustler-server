"""Line integrity after settle (15 Nov 2026, the independent check of the fact gate): a fact whose quote is replaced,
and every rewritten line, is judged again for support by a model; words given as someone's own must stand verbatim in a
verified quote; a rewrite that repeats another line is caught; the archive's 429s cost a page at most two short waits.

    /Users/masaoodi/Documents/Valmera/.valmera/venv-worker/bin/python -m pytest -q test_line_support.py

No network and no model: every fetch goes through a stubbed writer._get, every model call is a stand-in.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import writer  # noqa: E402
from test_facts import GOOD, LIVE, QUOTE, _ask, page, script, supported, web  # noqa: E402,F401

OLD_F9 = "And that right there, I said, 'I think I get it. I'm in.'"
NEW_F9 = "And I said, 'Well, it looks fine to me. Let's do it!'"
OLD_WORDS = "I think I get it. I'm in."
NEW_WORDS = "Well, it looks fine to me. Let's do it!"
HANKS_LINE = "As he later recalled saying, I think I get it. I'm in."


def use():
    return dict(provider="openai", model="gpt-6.1-sol", usd=0.002)


def asked(content):
    return [x["i"] for x in json.loads(content.split("each one cites\n", 1)[1])]


def judge(fn):
    """A line-support judge: fn(i, say) -> (supported, why, speech spans)."""
    def ask(system, content):
        ask.calls.append(content)
        lines = json.loads(content.split("each one cites\n", 1)[1])
        out = []
        for x in lines:
            ok, why, speech = fn(x["i"], x["say"])
            out.append({"i": x["i"], "supported": ok, "unsupported": why, "speech": speech})
        return json.dumps({"lines": out}), use()
    ask.calls = []
    return ask


def answers(*seq):
    """A model stand-in that gives these answers in order (the last one again after that)."""
    def ask(system, content):
        ask.calls.append(content)
        return json.dumps(seq[min(len(ask.calls) - 1, len(seq) - 1)]), use()
    ask.calls = []
    return ask


def hanks(declared):
    facts = [dict(id="f1", claim="Woody was mean in the early cut", quote=QUOTE, url=GOOD),
             dict(id="f9", claim="Hanks agreed to voice Woody after the pitch", quote=OLD_F9, url=LIVE)]
    s9 = dict(say=HANKS_LINE, beat="setup", facts=["f9"], show=[])
    if declared:
        s9["quotes"] = [dict(words=OLD_WORDS, who="Tom Hanks", fact="f9")]
    segs = [dict(say="Woody threw Buzz out of the window.", beat="hook", facts=["f1"], show=[]), s9,
            dict(say="The cowboy nearly sank the film.", beat="close", facts=["f1"], show=[])]
    return script(facts, segs)


def hanks_web(web):
    web.routes[GOOD] = page(QUOTE)
    web.routes[LIVE] = 403
    web.routes["https://www.hanks.example/interview"] = page("Hanks on the pitch.", NEW_F9)
    return _ask({"facts": [{"id": "f9", "sources": [{"url": "https://www.hanks.example/interview", "quote": NEW_F9}]}]})


def test_the_hanks_case_a_declared_old_quote_is_caught_even_when_the_judge_waves_it_through(web):
    """f9's quote is replaced by a different one; segment 1 still gives the OLD words as Hanks's. A lenient judge says
    supported, but the writer's own quotes field holds words no verified quote holds: the line is rewritten on the new
    quote, judged again, and stands with the new words, their source recorded."""
    research = hanks_web(web)
    rewrite = answers({"segments": [{"i": 1, "say": "He said yes on the spot. Well, it looks fine to me. Let's do it!",
                                     "facts": ["f9"], "show": [], "quotes": [{"words": NEW_WORDS, "who": "Tom Hanks", "fact": "f9"}]}]})
    lenient = judge(lambda i, say: (True, "", []))
    sc2, rep, _ = writer.settle(hanks(True), log=lambda m: None, ask_research=research, ask_rewrite=rewrite, ask_judge=lenient,
                                ask_repeats=_ask({"repeats": []}))
    assert rep["ok"] and not rep["speech_unbacked"]
    st = rep["settle"]
    assert st["replaced"][0]["old_quote"] == OLD_F9 and st["replaced"][0]["new_quote"] == NEW_F9
    first = st["lines_checked"][0]
    assert first["line"] == 1 and not first["ok"] and OLD_WORDS in first["why"]
    says = [s["say"] for s in sc2["segments"]]
    assert not any("I'm in" in s for s in says) and NEW_WORDS in says[1]
    assert sc2["segments"][1]["quotes"] == [dict(words=NEW_WORDS, who="Tom Hanks", fact="f9")]
    assert st["support_rewritten"] == [1] and len(rewrite.calls) == 1 and "replaced" in rewrite.calls[0]
    assert all("_check" not in s and "_sid" not in s for s in sc2["segments"])


def test_the_hanks_case_undeclared_speech_named_by_the_judge_is_checked_verbatim_and_dropped_when_it_persists(web):
    """No quotes field: the judge names the span the line gives as Hanks's words, and (though it calls the line
    supported) the span is in no verified quote the line cites. A rewrite that keeps the old words twice is dropped."""
    research = hanks_web(web)

    def fn(i, say):
        return True, "", ([{"words": OLD_WORDS, "who": "Tom Hanks"}] if "I'm in" in say else [])
    keep_old = answers({"segments": [{"i": 1, "say": "He later recalled it. I think I get it. I'm in.", "facts": ["f9"], "show": []}]})
    j = judge(fn)
    sc2, rep, _ = writer.settle(hanks(False), log=lambda m: None, ask_research=research, ask_rewrite=keep_old, ask_judge=j,
                                ask_repeats=_ask({"repeats": []}))
    assert rep["ok"]
    assert [s["say"] for s in sc2["segments"]] == ["Woody threw Buzz out of the window.", "The cowboy nearly sank the film."]
    st = rep["settle"]
    assert [c["ok"] for c in st["lines_checked"]] == [False, False, False] and len(keep_old.calls) == 2
    assert st["support_dropped"] and "I'm in" in st["support_dropped"][-1]["say"]
    assert [f["id"] for f in sc2["facts"]] == ["f1"] and "f9" in st["unused_dropped"]     # no line stands on the replacement


def test_a_replacement_that_verifies_but_does_not_support_the_line_is_rejected(web):
    """Re-research finds a page whose sentence is really there but says something else (a fabricated support): the
    judge finds the line unsupported; rewrites that keep the claim are refused, the line leaves the film and so does
    the replacement."""
    web.routes[GOOD] = page(QUOTE, "Tom Hanks voiced Woody in Toy Story.")
    web.routes[LIVE] = 403
    facts = [dict(id="f1", claim="the throw", quote=QUOTE, url=GOOD),
             dict(id="f2", claim="Hanks was paid fifty thousand dollars", quote="Hanks earned $50,000 for the first film.", url=LIVE)]
    segs = [dict(say="Woody threw Buzz out.", beat="hook", facts=["f1"], show=[]),
            dict(say="Hanks was paid fifty thousand dollars for it.", beat="setup", facts=["f2"], show=[]),
            dict(say="The cowboy nearly sank it.", beat="close", facts=["f1"], show=[])]
    research = _ask({"facts": [{"id": "f2", "sources": [{"url": GOOD, "quote": "Tom Hanks voiced Woody in Toy Story."}]}]})
    j = judge(lambda i, say: (False, "the fee: the quote says only that he voiced Woody", []) if "dollars" in say else (True, "", []))
    stubborn = answers({"segments": [{"i": 1, "say": "Hanks got fifty thousand dollars to voice Woody.", "facts": ["f2"], "show": []}]})
    sc2, rep, _ = writer.settle(script(facts, segs), log=lambda m: None, ask_research=research, ask_rewrite=stubborn, ask_judge=j,
                                ask_repeats=_ask({"repeats": []}))
    assert rep["ok"] and not any("dollars" in s["say"] for s in sc2["segments"])
    assert [f["id"] for f in sc2["facts"]] == ["f1"]
    assert rep["settle"]["lines_checked"][0]["why"].startswith("the fee")


def test_a_failed_judgement_counts_as_unsupported(web):
    """No answer from the judge: the line is not waved through (fail closed)."""
    research = hanks_web(web)

    def broken(system, content):
        raise RuntimeError("timeout")
    sc2, rep, _ = writer.settle(hanks(False), log=lambda m: None, ask_research=research,
                                ask_rewrite=answers({"segments": [{"i": 1, "say": None}]}), ask_judge=broken,
                                ask_repeats=_ask({"repeats": []}))
    assert rep["ok"] and len(sc2["segments"]) == 2 and not rep["settle"]["lines_checked"][0]["ok"]


def test_a_core_line_that_cannot_stand_on_its_new_quote_fails_the_story_clearly(web):
    research = hanks_web(web)
    sc = hanks(True)
    sc["segments"][1]["beat"] = "turn"
    j = judge(lambda i, say: (False, "the quote never says he agreed at once", []))
    rewrite = answers({"segments": [{"i": 1, "say": "He agreed at once.", "facts": ["f9"], "show": []}]})
    sc2, rep, _ = writer.settle(sc, log=lambda m: None, ask_research=research, ask_rewrite=rewrite, ask_judge=j)
    assert not rep["ok"] and "1" in rep["settle"]["core_unsupported"]
    assert sc2["segments"][1]["say"] == HANKS_LINE                 # the failed repair leaves the script as written
    import wf3
    assert "core lines" in wf3.script_problems(rep, sc2)[0]


def test_a_rewrite_that_repeats_another_line_is_caught_rewritten_once_and_dropped_if_it_still_repeats(web):
    web.routes[GOOD] = page(QUOTE, "The early Woody was a sarcastic bully.")
    web.routes[LIVE] = 403
    facts = [dict(id="f1", claim="the throw", quote=QUOTE, url=GOOD),
             dict(id="f2", claim="Disney's notes made him mean", quote="Disney asked for edge.", url=LIVE),
             dict(id="f3", claim="early Woody was a bully", quote="The early Woody was a sarcastic bully.", url=GOOD)]
    segs = [dict(say="Woody threw Buzz out.", beat="hook", facts=["f1"], show=[]),
            dict(say="Disney wanted edge.", beat="setup", facts=["f2"], show=[]),
            dict(say="The early Woody was a sarcastic bully.", beat="low", facts=["f3"], show=[]),
            dict(say="The cowboy nearly sank it.", beat="close", facts=["f1"], show=[])]
    dup = {"segments": [{"i": 1, "say": "Early Woody was a sarcastic bully.", "facts": ["f3"], "show": []}]}
    rewrite = answers(dup, dup)
    flags = answers({"repeats": [{"i": 1, "of": 2, "why": "same claim"}]})
    sc2, rep, _ = writer.settle(script(facts, segs), log=lambda m: None, ask_research=_ask({"facts": []}), ask_rewrite=rewrite,
                                ask_judge=supported(), ask_repeats=flags)
    assert rep["ok"] and [s["say"] for s in sc2["segments"]] == ["Woody threw Buzz out.", "The early Woody was a sarcastic bully.",
                                                                  "The cowboy nearly sank it."]
    st = rep["settle"]
    assert st["repeats"][0]["line"] == 1 and st["repeats"][0]["of"] == 2
    assert len(rewrite.calls) == 2 and "says again what line 2 says" in rewrite.calls[1] and len(flags.calls) == 2
    assert st["repeat_dropped"][-1]["why"] == "still repeats after one rewrite"


def test_a_repeat_rewritten_into_something_new_stands(web):
    web.routes[GOOD] = page(QUOTE, "The early Woody was a sarcastic bully.", "Lasseter shut the production down for two weeks.")
    web.routes[LIVE] = 403
    facts = [dict(id="f1", claim="the throw", quote=QUOTE, url=GOOD),
             dict(id="f2", claim="Disney's notes made him mean", quote="Disney asked for edge.", url=LIVE),
             dict(id="f3", claim="early Woody was a bully", quote="The early Woody was a sarcastic bully.", url=GOOD),
             dict(id="f4", claim="shutdown", quote="Lasseter shut the production down for two weeks.", url=GOOD)]
    segs = [dict(say="Woody threw Buzz out.", beat="hook", facts=["f1"], show=[]),
            dict(say="Disney wanted edge.", beat="setup", facts=["f2"], show=[]),
            dict(say="The early Woody was a sarcastic bully.", beat="low", facts=["f3"], show=[]),
            dict(say="Then the production stopped.", beat="turn", facts=["f4"], show=[])]
    rewrite = answers({"segments": [{"i": 1, "say": "Early Woody was a sarcastic bully.", "facts": ["f3"], "show": []}]},
                      {"segments": [{"i": 1, "say": "Woody threw Buzz out the window out of spite.", "facts": ["f1"], "show": []}]})
    flags = answers({"repeats": [{"i": 1, "of": 2, "why": "same claim"}]}, {"repeats": []})
    sc2, rep, _ = writer.settle(script(facts, segs), log=lambda m: None, ask_research=_ask({"facts": []}), ask_rewrite=rewrite,
                                ask_judge=supported(), ask_repeats=flags)
    assert rep["ok"] and sc2["segments"][1]["say"] == "Woody threw Buzz out the window out of spite."
    assert rep["settle"]["repeat_rewritten"] == [1] and not rep["settle"]["repeat_dropped"]


# ----------------------------------------------------------------- the archive's 429s

def test_wayback_429_is_waited_on_at_most_twice_and_ten_seconds_then_the_page_moves_on(web, monkeypatch):
    slept = []
    monkeypatch.setattr(writer, "_sleep", slept.append)
    web.routes[LIVE] = 403
    web.routes["https://archive.org/wayback/available*"] = 429
    web.routes["https://web.archive.org/*"] = 429
    rep = writer.verify(script([dict(id="f1", claim="c", quote=QUOTE, url=LIVE)], [dict(say="x", beat="hook", facts=["f1"], show=[])]))
    assert not rep["ok"]
    live_waits = slept[:1]
    archive = [u for u in web.calls if "archive.org" in u]
    assert live_waits == [writer.BACKOFF_S] and len(slept) - 1 <= 2 and sum(slept[1:]) <= 10
    assert len(archive) <= 3                                    # one ask and at most two asks again, then on


def test_wayback_snapshot_429_after_the_api_answers_is_bounded_too(web, monkeypatch):
    slept = []
    monkeypatch.setattr(writer, "_sleep", slept.append)
    web.routes[LIVE] = 403
    web.routes["https://archive.org/wayback/available*"] = json.dumps(
        {"archived_snapshots": {"closest": {"status": "200", "available": True, "timestamp": "20260101000000"}}})
    web.routes["https://web.archive.org/*"] = 429
    writer.verify(script([dict(id="f1", claim="c", quote=QUOTE, url=LIVE)], [dict(say="x", beat="hook", facts=["f1"], show=[])]))
    snaps = [u for u in web.calls if u.startswith("https://web.archive.org/")]
    assert len(slept) - 1 <= 2 and sum(slept[1:]) <= 10 and len(snaps) <= 3
    assert not any(str(writer.time.gmtime().tm_year) + "id_" in u for u in snaps)    # a refused archive is not asked for a second copy
