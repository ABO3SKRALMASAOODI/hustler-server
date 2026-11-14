"""The script stage's fact gate: strict on every fact, but a page that will not open or a quote that is absent costs one
claim, never the film (15 Nov 2026, the Woody run that died at the gate after four attempts).

    /Users/masaoodi/Documents/Valmera/.valmera/venv-worker/bin/python -m pytest -q test_facts.py

No network and no model: every fetch goes through a stubbed writer._get, every model call is a stand-in.
"""
import gzip
import json
import os
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "_shell"))
os.environ.setdefault("STORY_NO_REEXEC", "1")
import writer  # noqa: E402

LIVE = "https://studylib.example/doc/woody"
WIKI = "https://en.wikipedia.org/wiki/Toy_Story"
GOOD = "https://www.pixar.example/toy-story-history"
QUOTE = "In the early version Woody threw Buzz out the window out of spite."


def page(*paras):
    return "<html><head><script>var q = 'not this';</script></head><body>" + "".join(f"<p>{p}</p>" for p in paras) + "</body></html>"


@pytest.fixture
def web(monkeypatch):
    """A stub web: {url: html | int status | callable}; every request is recorded in web.calls."""
    routes, calls = {}, []

    def get(url, timeout):
        calls.append(url)
        for k, v in routes.items():
            if url == k or (k.endswith("*") and url.startswith(k[:-1])):
                v = v(url) if callable(v) else v
                if isinstance(v, int):
                    raise writer.FetchError(f"HTTP Error {v}", status=v)
                if isinstance(v, bytes):
                    return writer._decompress(v, "").decode("utf-8")
                return v
        raise writer.FetchError("HTTP Error 404", status=404)
    monkeypatch.setattr(writer, "_get", get)
    monkeypatch.setattr(writer, "_sleep", lambda s: None)

    def no_model(*a, **k):
        raise AssertionError("no model call in these tests: every call is a stand-in")
    monkeypatch.setattr(writer, "ask_openai", no_model)
    for name in ("_PAGES", "TRIED", "_VIA", "FETCHED"):        # every test reads its own pages
        monkeypatch.setattr(writer, name, {})
    return types.SimpleNamespace(routes=routes, calls=calls)


def script(facts, segs):
    return dict(title="The window", facts=facts, segments=segs, hook_line=segs[0]["say"], close_line=segs[-1]["say"])


# ----------------------------------------------------------------- the fetch: retry, Wikipedia's API, the Wayback copy

def test_a_403_is_asked_again_then_read_from_the_wayback_copy(web):
    """The live page refuses twice (a 403, then the retry after a backoff); the Wayback Machine's latest copy (its raw
    gzip bytes, as id_ serves them) holds the quote: the fact verifies, and the row lists every fetch with its status."""
    web.routes[LIVE] = 403
    web.routes["https://archive.org/wayback/available*"] = json.dumps(
        {"archived_snapshots": {"closest": {"status": "200", "available": True, "timestamp": "20260101000000"}}})
    web.routes[f"https://web.archive.org/web/20260101000000id_/{LIVE}"] = gzip.compress(page("Toy Story notes.", QUOTE).encode())
    sc = script([dict(id="f1", claim="Woody threw Buzz out", quote=QUOTE, url=LIVE)],
                [dict(say="Woody threw Buzz out of the window.", beat="hook", facts=["f1"], show=[])])
    rep = writer.verify(sc)
    assert rep["ok"] and rep["facts"][0]["read"] == "wayback"
    assert [(f["via"], f["status"]) for f in rep["facts"][0]["fetches"]] == [("live", 403), ("live", 403), ("wayback_api", 200), ("wayback", 200)]
    assert web.calls.count(LIVE) == 2                       # one retry, not a storm


def test_wikipedia_is_read_through_its_own_api_when_the_page_refuses(web):
    web.routes[WIKI] = 429
    web.routes["https://en.wikipedia.org/api/rest_v1/page/html/Toy_Story"] = page("Pixar screened the first half of the film "
                                                                                  "for Disney executives on November 19, 1993.")
    sc = script([dict(id="f1", claim="the screening", quote="Pixar screened the first half of the film for Disney executives on "
                                                             "November 19, 1993.", url=WIKI)],
                [dict(say="November nineteenth.", beat="hook", facts=["f1"], show=["1993"])])
    rep = writer.verify(sc)
    assert rep["ok"] and rep["facts"][0]["read"] == "wikipedia_rest" and not rep["numbers_unbacked"]
    assert not any("archive.org" in u for u in web.calls)   # Wikipedia's own door first


def test_quote_presence_is_measured_on_readable_text_not_markup(web):
    web.routes[GOOD] = ("<html><body><p>In the early version <b>Woody</b> threw <a href='/b'>Buzz</a> out the window out of spite."
                        "</p><script>var s = 'Woody kissed Buzz on the moon';</script></body></html>")
    ok = dict(id="f1", claim="c", quote=QUOTE, url=GOOD)
    hidden = dict(id="f2", claim="c", quote="Woody kissed Buzz on the moon", url=GOOD)
    rep = writer.verify(script([ok, hidden], [dict(say="x", beat="hook", facts=["f1", "f2"], show=[])]))
    assert [r["found"] for r in rep["facts"]] == [True, False]


def test_another_copy_the_writer_cited_carries_the_quote(web):
    web.routes[LIVE] = 403
    web.routes[GOOD] = page(QUOTE)
    rep = writer.verify(script([dict(id="f1", claim="c", quote=QUOTE, url=LIVE, also=[GOOD])],
                               [dict(say="x", beat="hook", facts=["f1"], show=[])]))
    assert rep["ok"] and rep["facts"][0]["read"] == "also:here" and rep["facts"][0]["source"] == GOOD


# ----------------------------------------------------------------- re-research, rewrite, and the core

def _ask(answer):
    calls = []

    def ask(system, content):
        calls.append(content)
        return json.dumps(answer), dict(provider="openai", model="gpt-6.1-sol", usd=0.01)
    ask.calls = calls
    return ask


def supported():
    """A line-support judge that finds every line it is asked about supported, naming no quoted speech."""
    def ask(system, content):
        ask.calls.append(content)
        lines = json.loads(content.split("each one cites\n", 1)[1])
        return json.dumps({"lines": [{"i": x["i"], "supported": True, "unsupported": "", "speech": []} for x in lines]}), \
            dict(provider="openai", model="gpt-6.1-sol", usd=0.002)
    ask.calls = []
    return ask


def test_an_absent_quote_is_researched_again_and_the_new_source_verifies(web):
    web.routes[LIVE] = page("A page about something else entirely.")
    web.routes[GOOD] = page("Toy Story history.", QUOTE)
    sc = script([dict(id="f1", claim="Woody threw Buzz out", quote=QUOTE, url=LIVE)],
                [dict(say="Woody threw Buzz out of the window.", beat="hook", facts=["f1"], show=[])])
    research = _ask({"facts": [{"id": "f1", "sources": [{"url": LIVE, "quote": QUOTE}, {"url": GOOD, "quote": QUOTE}]}]})
    rewrite = _ask({})
    judge = supported()
    sc2, rep, spend = writer.settle(sc, log=lambda m: None, ask_research=research, ask_rewrite=rewrite, ask_judge=judge)
    assert rep["ok"] and [{k: r[k] for k in ("id", "old", "new", "read")} for r in rep["settle"]["replaced"]] == \
        [dict(id="f1", old=LIVE, new=GOOD, read="here")]
    f = sc2["facts"][0]
    assert f["url"] == GOOD and f["replaced"]["url"] == LIVE and "not in the page" in f["replaced"]["why"]
    assert len(research.calls) == 1 and not rewrite.calls and len(spend) == 2
    assert len(judge.calls) == 1 and rep["settle"]["lines_checked"][0]["ok"]     # the line on the new quote was judged
    assert sc2["segments"] == sc["segments"]                # the words stand


def test_a_side_claim_that_no_page_states_is_rewritten_out_and_the_rest_verifies(web):
    web.routes[GOOD] = page(QUOTE, "Toy Story was released in 1995.")
    web.routes[LIVE] = 403
    facts = [dict(id="f1", claim="the throw", quote=QUOTE, url=GOOD),
             dict(id="f2", claim="a featurette preserves the early cut", quote="The featurette preserves the early cut.", url=LIVE),
             dict(id="f3", claim="released 1995", quote="Toy Story was released in 1995.", url=GOOD)]
    segs = [dict(say="Woody threw Buzz out.", beat="hook", facts=["f1"], show=[]),
            dict(say="A featurette still shows that cut.", beat="setup", facts=["f2"], show=[dict(text="FEATURETTE", role="key")]),
            dict(say="Nineteen ninety-five.", beat="payoff", facts=["f3"], show=[dict(text="1995", role="figure")]),
            dict(say="The cut that nearly ended it.", beat="close", facts=["f1", "f2"], show=[])]
    research = _ask({"facts": [{"id": "f2", "sources": []}]})
    rewrite = _ask({"segments": [{"i": 1, "say": None}, {"i": 3, "say": "The cowboy who nearly ended it.", "show": [], "facts": ["f1"]}]})
    sc2, rep, spend = writer.settle(script(facts, segs), log=lambda m: None, ask_research=research, ask_rewrite=rewrite,
                                    ask_judge=supported(), ask_repeats=_ask({"repeats": []}))
    assert rep["ok"] and rep["verified"] == rep["total"] == 2
    st = rep["settle"]
    assert st["facts_dropped"] == ["f2"] and st["dropped_segments"] == [1] and st["rewritten"] == [3] and not st["core_unverified"]
    assert [s["say"] for s in sc2["segments"]] == ["Woody threw Buzz out.", "Nineteen ninety-five.", "The cowboy who nearly ended it."]
    assert sc2["close_line"] == "The cowboy who nearly ended it." and all("f2" not in s["facts"] for s in sc2["segments"])
    assert "f2" in rewrite.calls[0] and "[1, 3]" in rewrite.calls[0]


def test_a_rewrite_that_cites_an_unchecked_fact_drops_the_segment(web):
    web.routes[GOOD] = page(QUOTE)
    web.routes[LIVE] = 403
    facts = [dict(id="f1", claim="c", quote=QUOTE, url=GOOD), dict(id="f2", claim="d", quote="Nothing here.", url=LIVE)]
    segs = [dict(say="Woody threw Buzz out.", beat="hook", facts=["f1"], show=[]),
            dict(say="Side line.", beat="setup", facts=["f2"], show=[])]
    sc2, rep, _ = writer.settle(script(facts, segs), log=lambda m: None, ask_research=_ask({"facts": []}),
                                ask_rewrite=_ask({"segments": [{"i": 1, "say": "Still says it.", "facts": ["f2"]}]}))
    assert rep["ok"] and [s["say"] for s in sc2["segments"]] == ["Woody threw Buzz out."]


def test_a_core_claim_that_cannot_be_verified_stops_the_stage_clearly(web, tmp_path):
    import wf3
    web.routes[LIVE] = 403
    facts = [dict(id="f1", claim="Woody threw Buzz out on purpose", quote=QUOTE, url=LIVE)]
    segs = [dict(say="Woody threw Buzz out.", beat="hook", facts=["f1"], show=[])] + \
        [dict(say=" ".join(["word"] * 20), beat="setup", facts=[], show=[]) for _ in range(9)]
    research = _ask({"facts": [{"id": "f1", "sources": []}]})
    rewrite = _ask({"segments": [{"i": 0, "say": "Something else.", "facts": []}]})
    sc2, rep, _ = writer.settle(script(facts, segs), log=lambda m: None, ask_research=research, ask_rewrite=rewrite)
    assert not rep["ok"] and rep["settle"]["core_unverified"] == ["f1"]       # a hook restated on nothing checked does not stand
    assert "403" in rep["settle"]["core_why"]["f1"]["why"] and rep["settle"]["core_why"]["f1"]["beats"] == ["hook"]
    assert sc2["segments"][0]["say"] == "Woody threw Buzz out."                # and the script is not changed by the failed repair
    ctx = types.SimpleNamespace(run_dir=str(tmp_path), args={}, log=lambda m: None)
    writes = []

    def write(notes):
        writes.append(notes)
        return script(facts, segs), dict(provider="openai", usd=0.0)
    with pytest.raises(RuntimeError, match="core claims cannot be verified.*Woody threw Buzz out on purpose"):
        wf3.write_checked(ctx, {}, {}, write=write,
                          settle=lambda s: writer.settle(s, log=lambda m: None, ask_research=research, ask_rewrite=rewrite))
    assert len(writes) == wf3.SCRIPT_TRIES and "core claims" in writes[1]


def test_a_payoff_restated_on_its_checked_facts_stands_and_is_never_dropped(web):
    """The payoff cites a table-row figure no page states as quoted and a checked article that carries the same point:
    it is restated on the checked fact; a rewrite that drops a payoff is refused (the story would lose its core)."""
    web.routes[GOOD] = page(QUOTE, "Toy Story opened at number one with $29.1 million over the weekend.")
    web.routes[LIVE] = page("Nothing about the weekend.")
    facts = [dict(id="f1", claim="the throw", quote=QUOTE, url=GOOD),
             dict(id="f2", claim="opening weekend exact", quote="The weekend earned $29,140,617 exactly.", url=LIVE),
             dict(id="f3", claim="opening weekend", quote="Toy Story opened at number one with $29.1 million over the weekend.", url=GOOD)]
    segs = [dict(say="Woody threw Buzz out.", beat="hook", facts=["f1"], show=[]),
            dict(say="Twenty-nine million, one hundred forty thousand dollars.", beat="payoff", facts=["f2", "f3"],
                 show=[dict(text="$29,140,617", role="figure")])]
    restate = _ask({"segments": [{"i": 1, "say": "Twenty-nine point one million dollars in one weekend.", "facts": ["f3"],
                                  "show": [{"text": "$29.1M", "role": "figure"}]}]})
    sc2, rep, _ = writer.settle(script(facts, segs), log=lambda m: None, ask_research=_ask({"facts": []}), ask_rewrite=restate,
                                ask_judge=supported(), ask_repeats=_ask({"repeats": []}))
    assert rep["ok"] and rep["settle"]["rewritten"] == [1] and sc2["segments"][1]["facts"] == ["f3"]
    assert sc2["segments"][1]["show"] == [dict(text="$29.1M", role="figure")] and not rep["numbers_unbacked"]
    dropped = _ask({"segments": [{"i": 1, "say": None}]})
    sc3, rep3, _ = writer.settle(script(facts, segs), log=lambda m: None, ask_research=_ask({"facts": []}), ask_rewrite=dropped)
    assert not rep3["ok"] and rep3["settle"]["core_unverified"] == ["f2"] and len(sc3["segments"]) == 2


def test_a_quote_copied_off_a_table_row_is_found_letter_for_letter(web):
    web.routes[GOOD] = ("<table><tr><td>Nov 24-26</td><td>Thanksgiving 3-day</td><td>1</td><td>$29,140,617</td></tr></table>")
    rep = writer.verify(script([dict(id="f1", claim="c", quote="Nov 24-26Thanksgiving 3-day 1 $29,140,617", url=GOOD),
                                dict(id="f2", claim="c", quote="Nov 24-26Thanksgiving 3-day 1 $29,140,618", url=GOOD)],
                               [dict(say="x", beat="hook", facts=["f1", "f2"], show=[])]))
    assert [r["found"] for r in rep["facts"]] == [True, False]


def test_a_core_claim_that_re_research_rescues_lets_the_stage_pass(web, tmp_path):
    import wf3
    web.routes[LIVE] = 403
    web.routes[GOOD] = page(QUOTE)
    facts = [dict(id="f1", claim="Woody threw Buzz out", quote=QUOTE, url=LIVE)]
    segs = [dict(say=" ".join(["word"] * 18), beat=b, facts=["f1"] if b == "hook" else [], show=[]) for b in ["hook"] + ["setup"] * 10]
    research = _ask({"facts": [{"id": "f1", "sources": [{"url": GOOD, "quote": QUOTE}]}]})
    ctx = types.SimpleNamespace(run_dir=str(tmp_path), args={}, log=lambda m: None)
    sc, rep, spend = wf3.write_checked(ctx, {}, {}, write=lambda n: (script(facts, segs), dict(provider="openai", usd=0.0)),
                                       settle=lambda s: writer.settle(s, log=lambda m: None, ask_research=research, ask_rewrite=_ask({}),
                                                                      ask_judge=supported()))
    assert rep["ok"] and sc["facts"][0]["url"] == GOOD and len(spend) == 3


# ----------------------------------------------------------------- figures on screen

def test_a_list_ordinal_is_not_a_figure_but_a_rank_is(web):
    web.routes[GOOD] = page("Toy Story was the highest grossing domestic film of 1995.")
    facts = [dict(id="f1", claim="top film", quote="Toy Story was the highest grossing domestic film of 1995.", url=GOOD)]
    segs = [dict(say="Three things.", beat="hook", facts=["f1"], show=[dict(text="1995", role="figure")]),
            dict(say="One.", beat="setup", facts=[], show=[dict(text="1.", role="ordinal")]),
            dict(say="Two.", beat="setup", facts=[], show=[dict(text="2.", role="ordinal")]),
            dict(say="Three.", beat="payoff", facts=["f1"], show=[dict(text="3.", role="ordinal")])]
    assert writer.verify(script(facts, segs))["ok"]          # 1., 2., 3. number the film's own list
    lone = json.loads(json.dumps(segs[:2]))                  # a lone "1." is no list: it is read as a figure
    assert writer.verify(script(facts, lone))["numbers_unbacked"] == ["1"]
    rank = [dict(segs[0], show=[dict(text="1995 · #1 DOMESTIC", role="figure")])]
    assert writer.verify(script(facts, rank))["numbers_unbacked"] == ["1"]
    dressed = [dict(segs[0], show=[dict(text="#1", role="ordinal")]), dict(segs[1], show=[dict(text="#2 2020", role="ordinal")])]
    assert writer.verify(script(facts, dressed))["numbers_unbacked"] == ["1", "2", "2020"]


def test_an_unbacked_number_leaves_the_screen_not_the_film(web):
    web.routes[GOOD] = page("Toy Story was the highest grossing domestic film of 1995.")
    facts = [dict(id="f1", claim="top film", quote="Toy Story was the highest grossing domestic film of 1995.", url=GOOD)]
    segs = [dict(say="It topped the year.", beat="payoff", facts=["f1"],
                 show=[dict(text="1995 · #1 DOMESTIC", role="figure"), dict(text="1995", role="figure")])]
    sc2, rep, _ = writer.settle(script(facts, segs), log=lambda m: None, ask_research=_ask({}), ask_rewrite=_ask({}))
    assert rep["ok"] and sc2["segments"][0]["show"] == [dict(text="1995", role="figure")]
    assert rep["settle"]["display_dropped"] == [dict(segment=0, text="1995 · #1 DOMESTIC")]
