"""Verify first, then write from the bank (15 Nov 2026, the independent check of dcbe553e): every final line stood on
verified quotes, but the stories lost their spine. The Dyson hook "Hoover rejected James Dyson, then had to pay for
copying his invention" ended as "James Dyson. Hoover deliberately copied a fundamental part of his patented designs":
no rejection, no reversal, no price. The close was dropped twice; a point rewrite failed with no reason; a bare "1983"
passed as a quote; a span named from the quote, not the line, passed as speech.

    /Users/masaoodi/Documents/Valmera/.valmera/venv-worker/bin/python -m pytest -q test_spine.py

No network and no model: every fetch goes through a stubbed writer._get, every model call is a stand-in. The stand-ins
decide by the test's own strings; the product decides by a model.
"""
import json
import os
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import writer  # noqa: E402
from test_facts import page, script, supported, web  # noqa: E402,F401
from test_line_support import answers, judge, use  # noqa: E402

WIKI = "https://en.wikipedia.org/wiki/James_Dyson"
COMPANY = "https://en.wikipedia.org/wiki/Dyson_(company)"
PARL = "https://publications.parliament.example/dyson-evidence"
REJECT = "Manufacturing companies such as Hoover did not want to license the design."
COPIED = ("In 1999, Dyson sued Hoover (UK) for patent infringement and the High Court ruled that Hoover had deliberately copied "
          "a fundamental part of his patented designs in making its Triple Vortex bagless vacuum cleaner range.")
PAID = "Hoover agreed to pay damages of £4 million."
ACCEPTED = "Dyson accepted a compensatory payment of £4 million instead of damages (plus costs)."
CLOG = "He became frustrated with his Hoover Junior's diminishing performance as the dust bag kept clogging."
FLAT = "James Dyson. Hoover deliberately copied a fundamental part of his patented designs."
SHARP = "Hoover would not license James Dyson's design. Then it paid four million pounds for copying it."

IDEA = dict(title="Dyson's patent fight with Hoover", style="window",
            hook="Hoover rejected James Dyson, then had to pay for copying his invention.",
            story="Hoover would not license Dyson's bagless design. The High Court found Hoover copied it. Hoover paid four million pounds.",
            turn="The company that turned him down launched a bagless rival that copied his patent.",
            payoff="Hoover agreed to pay four million pounds in damages.",
            sources=[WIKI, COMPANY],
            spine=[dict(beat="hook", claim="Hoover turned down Dyson's bagless design", source=dict(url=PARL, quote="1983")),
                   dict(beat="hook", claim="Hoover later paid Dyson for copying it", source=dict(url=PARL, quote=ACCEPTED)),
                   dict(beat="turn", claim="The High Court ruled that Hoover deliberately copied Dyson's patented design",
                        source=dict(url=WIKI, quote=COPIED)),
                   dict(beat="payoff", claim="Hoover paid four million pounds", source=dict(url=PARL, quote=ACCEPTED)),
                   dict(beat="close", claim="The company that turned the design down paid for copying it",
                        source=dict(url=PARL, quote=ACCEPTED))])

# what each claim needs to be stated: the quotes that together state it (the stand-in judge's own knowledge)
NEEDS = {"c1": [REJECT], "c2": [PAID, COPIED], "c3": [COPIED], "c4": [PAID], "c5": [REJECT, PAID, COPIED]}


def quiet(m):
    pass


def dyson_web(web):
    web.routes[WIKI] = page("James Dyson is a British inventor.", CLOG, COPIED, PAID)
    web.routes[COMPANY] = page("Dyson is a technology company.", REJECT)
    web.routes[PARL] = page("Evidence to the committee.", ACCEPTED, "Thursday 4th October 2001")


def claims_judge(needs=NEEDS):
    """The spine judge stand-in: a claim is stated when every quote it needs is in the verified bank."""
    def ask(system, content):
        ask.calls.append(content)
        claims = json.loads(content.split("# The spine's claims\n", 1)[1].split("\n\n# Verified quotes", 1)[0])
        quotes = json.loads(content.split("word for word on its page)\n", 1)[1])
        by_quote = {q["quote"]: q["id"] for q in quotes}
        out = []
        for c in claims:
            need = needs.get(c["id"], [])
            have = [by_quote[q] for q in need if q in by_quote]
            ok = bool(need) and len(have) == len(need)
            out.append(dict(id=c["id"], stated=ok, by=have if ok else [], as_stated=c["claim"] if ok else "",
                            missing="" if ok else "no quote names Hoover as the payer" if PAID in need else "no quote states it"))
        return json.dumps({"claims": out}), use()
    ask.calls = []
    return ask


def copier(*per_call):
    """The page-copy stand-in: the sentences to copy on each call, by page url."""
    def ask(system, content):
        ask.calls.append(content)
        urls = [line.split(": ", 1)[1] for line in content.split("\n") if line.startswith("# Page ")]
        want = per_call[min(len(ask.calls) - 1, len(per_call) - 1)]
        out = [dict(page=urls.index(u), quote=q, claims=[], source="Wikipedia", speaker=None, when=None)
               for u, qs in want.items() if u in urls for q in qs]
        return json.dumps({"quotes": out}), use()
    ask.calls = []
    return ask


def researcher(*rounds):
    """The search stand-in: per call, {claim id: [(url, quote)]}; it reports two new queries a claim."""
    def ask(system, content):
        ask.calls.append((system, content))
        rows = json.loads(content.split(" of 3)\n", 1)[1].split("\n\n# Verified already", 1)[0])
        got = rounds[min(len(ask.calls) - 1, len(rounds) - 1)]
        n = len(ask.calls)
        return json.dumps({"claims": [dict(id=r["id"], queries=[f"{r['id']} query {n}a", f"{r['id']} query {n}b"],
                                           sources=[dict(url=u, quote=q, source="Wikipedia", speaker=None, when="2002")
                                                    for u, q in got.get(r["id"], [])])
                                      for r in rows]}), dict(use(), queries=[f"api query {n}"])
    ask.calls = []
    return ask


def dyson_bank(web):
    dyson_web(web)
    research = researcher({"c1": [(COMPANY, REJECT)], "c2": [(WIKI, PAID), (PARL, "Hoover paid Dyson four million pounds.")],
                           "c4": [(WIKI, PAID)], "c5": [(WIKI, PAID)]})
    copy = copier({WIKI: [CLOG], PARL: ["Thursday 4th October 2001"]}, {})
    j = claims_judge()
    bank, spend = writer.spine_bank(IDEA, log=quiet, ask_research=research, ask_extract=copy, ask_judge=j)
    return bank, spend, research, copy, j


def fid(bank, quote):
    return next(f["id"] for f in bank["facts"] if f["quote"] == quote)


# ----------------------------------------------------------------- 1. verify first

def test_dyson_the_spine_is_verified_before_a_word_is_written(web):
    """The idea's own evidence verifies the court ruling but not who paid ("Dyson accepted £4 million" names no payer)
    and offers a bare "1983" for the rejection. The judge says so; one search round, primary sources first, finds
    "Hoover agreed to pay damages of £4 million" and the refusal to license; every spine claim is then stated."""
    bank, spend, research, copy, j = dyson_bank(web)
    assert bank["ok"], bank["why"]
    by = {c["id"]: c for c in bank["claims"]}
    assert all(c["stated"] for c in bank["claims"])
    assert fid(bank, PAID) in by["c4"]["by"] and fid(bank, REJECT) in by["c1"]["by"]
    assert set(by["c5"]["by"]) == {fid(bank, REJECT), fid(bank, PAID), fid(bank, COPIED)}       # rejected, copied, paid
    thin = [x for x in bank["failed"] if x["quote"] in ("1983", "Thursday 4th October 2001")]
    assert len(thin) == 2 and all("bare number or date" in x["why"] for x in thin)
    assert any(x["quote"].startswith("Hoover paid Dyson four") and "not in the page" in x["why"] for x in bank["failed"])
    assert len(research.calls) == 1 and "primary sources first" in research.calls[0][0]
    asked = json.loads(research.calls[0][1].split(" of 3)\n", 1)[1].split("\n\n# Verified already", 1)[0])
    assert sorted(r["id"] for r in asked) == ["c1", "c2", "c4", "c5"]                              # only what is not stated yet
    assert "Hoover as the payer" in next(r["why_not_yet"] for r in asked if r["id"] == "c4")
    assert by["c4"]["queries"] == ["c4 query 1a", "c4 query 1b"] and bank["rounds"][0]["queries"] == ["api query 1"]
    assert all("queries" not in u for u in spend)                                                 # kept in the bank, not the spend rows
    assert CLOG in [f["quote"] for f in bank["facts"]] and all(writer.thin_quote(f["quote"]) is None for f in bank["facts"])


def test_a_spine_claim_is_searched_three_ways_before_the_idea_is_let_go(web):
    dyson_web(web)
    research = researcher({})                         # nothing anywhere says Hoover turned him down
    bank, _ = writer.spine_bank(IDEA, log=quiet, ask_research=research, ask_extract=copier({}),
                                ask_judge=claims_judge(dict(NEEDS, c1=["Hoover refused Dyson in 1983 at a meeting."])))
    assert not bank["ok"] and len(research.calls) == writer.SPINE_ROUNDS == 3
    tiers = [system for system, _ in research.calls]
    assert "primary sources first" in tiers[0] and "written at the time" in tiers[1] and "any readable page" in tiers[2]
    third = json.loads(research.calls[2][1].split(" of 3)\n", 1)[1].split("\n\n# Verified already", 1)[0])
    c1 = next(r for r in third if r["id"] == "c1")
    assert c1["tried"] == ["c1 query 1a", "c1 query 1b", "c1 query 2a", "c1 query 2b"]          # never the same query twice
    assert "hook: Hoover turned down Dyson's bagless design" in bank["why"] and "3 search rounds" in bank["why"]
    assert next(c for c in bank["claims"] if c["id"] == "c1")["rounds"] == 3


# ----------------------------------------------------------------- 2. write from the bank, hold the spine

def bank_script(bank, hook=FLAT):
    i = lambda q: fid(bank, q)  # noqa: E731
    segs = [dict(say=hook, beat="hook", facts=[i(COPIED)], show=[]),
            dict(say="His Hoover Junior kept clogging with dust.", beat="setup", facts=[i(CLOG)], show=[]),
            dict(say="But Hoover did not want to license the design.", beat="low", facts=[i(REJECT)], show=[]),
            dict(say="Then the High Court ruled Hoover had deliberately copied it.", beat="turn", facts=[i(COPIED)], show=[]),
            dict(say="Hoover agreed to pay four million pounds.", beat="payoff", facts=[i(PAID)],
                 show=[dict(text="£4 million", role="figure")]),
            dict(say="The company that would not license his design paid for copying it.", beat="close",
                 facts=[i(REJECT), i(COPIED), i(PAID)], show=[])]
    return dict(title="Hoover paid", voice="atlas", segments=segs, named=[])


def bank_writer(hook=FLAT):
    def ask(system, content):
        ask.calls.append((system, content))
        facts = json.loads(content.split("# The bank: the only facts this film may state\n", 1)[1].split("\n\n", 1)[0])
        bank = dict(facts=[dict(f, url="x") for f in facts])
        return json.dumps(bank_script(bank, hook)), use()
    ask.calls = []
    return ask


def spine_judge(flat=lambda beat, say: say.startswith("James Dyson. Hoover deliberately")):
    def ask(system, content):
        ask.calls.append(content)
        rows = json.loads(content.split("its quotes\n", 1)[1])
        return json.dumps({"beats": [dict(beat=r["beat"], carried=not flat(r["beat"], r["say"]),
                                          lost="no rejection, no reversal, no price" if flat(r["beat"], r["say"]) else "")
                                     for r in rows]}), use()
    ask.calls = []
    return ask


def test_dyson_written_from_the_bank_the_hook_keeps_rejected_then_paid(web):
    """The writer gets only the verified bank and the spine; its first hook is the flat legal line. The spine judge
    says it lost the rejection and the price; the hook is rewritten on the bank's quotes and keeps "rejected, then
    paid". Then the ear flattens it again (the 15 Nov failure): the final pass compares it with the held line and the
    spine's point and wins it back."""
    bank, *_ = dyson_bank(web)
    w = bank_writer()
    sc, _ = writer.write_from_bank(IDEA, dict(id="window", name="Window", look="framed"), "short", bank, ask=w)
    system, content = w.calls[0]
    assert "ONLY from the bank" in system and "the rejection AND the price" in system
    assert PAID in content and REJECT in content and "1983" not in content.split("# The bank", 1)[1]
    assert [f["id"] for f in sc["facts"]] == [f["id"] for f in bank["facts"]] and sc["spine"] == writer.spine_view(bank)
    assert writer.verify(sc)["ok"]

    sj = spine_judge()
    fix = answers({"segments": [{"i": 0, "say": SHARP, "facts": [fid(bank, REJECT), fid(bank, PAID), fid(bank, COPIED)], "show": []}]})
    held, rep, spend, bank2 = writer.hold_spine(sc, bank, IDEA, log=quiet, ask_judge=supported(), ask_rewrite=fix, ask_point=sj)
    assert rep["ok"], rep["why"]
    hook = held["segments"][0]["say"]
    assert hook == SHARP and "would not license" in hook and "paid four million pounds" in hook
    assert "must carry the hook's point" in fix.calls[0] and "Hoover turned down Dyson's bagless design" in fix.calls[0]
    assert rep["spine_rewritten"] == [0] and [s["beats"]["hook"]["carried"] for s in rep["spine"]] == [False, True]
    assert held["segments"][0]["say_first"] == SHARP                 # the held words are what later point checks compare with
    assert len(held["segments"]) == 6 and held["segments"][-1]["beat"] == "close"

    flat = json.loads(json.dumps(held))
    flat["segments"][0]["say"], flat["segments"][0]["say_written"] = FLAT, SHARP     # the ear flattened it again
    flat["segments"][0]["facts"] = [fid(bank, COPIED)]
    point = answers({"lines": [{"i": 0, "kept": False, "lost": "the rejection and the price"}]}, {"lines": [{"i": 0, "kept": True}]})
    back = answers({"segments": [{"i": 0, "say": SHARP, "facts": [fid(bank, REJECT), fid(bank, PAID), fid(bank, COPIED)], "show": []}]})
    final, frep, _ = writer.final_support(flat, log=quiet, ask_judge=supported(), ask_rewrite=back,
                                          ask_repeats=answers({"repeats": []}), ask_point=point)
    assert frep["ok"] and final["segments"][0]["say"] == SHARP
    rows = json.loads(point.calls[0].split("as first written, and now\n", 1)[1])
    assert rows[0]["first"] == SHARP and rows[0]["point"] == ["Hoover turned down Dyson's bagless design",
                                                              "Hoover later paid Dyson for copying it"]
    assert "Hoover later paid Dyson for copying it" in back.calls[0]                 # the rewrite is told the spine's point
    close = writer.spine_points(held)[5]
    assert [p["claim"] for p in close] == [writer.REFRAME.format("Hoover turned down Dyson's bagless design"),
                                           writer.REFRAME.format("Hoover later paid Dyson for copying it"),
                                           "The company that turned the design down paid for copying it"]     # the reframe leads
    assert "reframes the hook" in sj.calls[0] and IDEA["hook"] in sj.calls[0]       # the judge holds the close to a reframe


def test_when_the_bank_cannot_carry_the_point_its_claim_is_searched_again_before_any_flat_line(web):
    bank, *_ = dyson_bank(web)
    sc, _ = writer.write_from_bank(IDEA, dict(id="window"), "short", bank, ask=bank_writer())
    none = answers({"segments": [{"i": 0, "say": None}]},
                   {"segments": [{"i": 0, "say": SHARP, "facts": [fid(bank, REJECT), fid(bank, PAID), fid(bank, COPIED)], "show": []}]})
    again = []

    def research_again(b, cids, why):
        again.append((cids, why))
        return b, [use()]
    held, rep, _, _ = writer.hold_spine(sc, bank, IDEA, log=quiet, ask_judge=supported(), ask_rewrite=none, ask_point=spine_judge(),
                                        again=research_again)
    assert rep["ok"] and held["segments"][0]["say"] == SHARP
    assert again and sorted(again[0][0]) == ["c1", "c2"] and "could not carry it" in again[0][1]["c1"]
    assert rep["spine_failed"][0]["why"] == "the rewrite answered null: it found no checked quote that says it"
    assert rep["researched"][0]["claims"] == ["c1", "c2"]


def test_a_close_that_ends_on_a_moral_is_rewritten_to_reframe_the_hook(web):
    bank, *_ = dyson_bank(web)
    sc, _ = writer.write_from_bank(IDEA, dict(id="window"), "short", bank, ask=bank_writer(SHARP))
    sc["segments"][5]["say"] = "The case showed that copying could be stopped."
    moral = spine_judge(lambda beat, say: beat == "close" and "copying could be stopped" in say)
    reframe = "The design Hoover would not license cost it four million pounds."
    fix = answers({"segments": [{"i": 5, "say": reframe, "facts": [fid(bank, REJECT), fid(bank, PAID)], "show": []}]})
    held, rep, _, _ = writer.hold_spine(sc, bank, IDEA, log=quiet, ask_judge=supported(), ask_rewrite=fix, ask_point=moral)
    assert rep["ok"] and held["segments"][5]["say"] == reframe and held["close_line"] == reframe
    assert "must carry the close's point" in fix.calls[0] and "reframes the hook" in fix.calls[0]


def test_a_close_claim_no_page_states_never_costs_a_verified_story_the_close_reframes_the_hook(web):
    """Nintendo, 15 Nov: hook, turn and payoff verified; the close's own quote stood behind a page that now refuses the
    script. The close reframes the hook, so it stands on the hook's verified facts (recorded); a missing hook still
    fails the spine."""
    dyson_web(web)
    needs = dict(NEEDS, c5=["Hoover said sorry to Dyson in a letter of apology."])
    bank, _ = writer.spine_bank(IDEA, log=quiet, ask_research=researcher({"c1": [(COMPANY, REJECT)], "c2": [(WIKI, PAID)], "c4": [(WIKI, PAID)]}),
                                ask_extract=copier({WIKI: [CLOG]}, {}), ask_judge=claims_judge(needs))
    assert bank["ok"] and bank["close_from_hook"][0]["claim"].startswith("The company that turned")
    assert [x["beat"] for x in writer.spine_view(bank)] == ["hook", "hook", "turn", "payoff"]
    sc, _ = writer.write_from_bank(IDEA, dict(id="window"), "short", bank, ask=bank_writer(SHARP))
    assert [p["claim"] for p in writer.spine_points(sc)[5]] == [writer.REFRAME.format("Hoover turned down Dyson's bagless design"),
                                                                writer.REFRAME.format("Hoover later paid Dyson for copying it")]
    sj = spine_judge(lambda beat, say: False)
    writer.judge_spine(sc, ask=sj)
    assert [r["beat"] for r in json.loads(sj.calls[0].split("its quotes\n", 1)[1])][-1] == "close"   # still judged as a reframe
    no_hook = dict(NEEDS, c1=["Hoover refused Dyson in 1983 at a meeting."], c5=["Hoover said sorry to Dyson."])
    bank2, _ = writer.spine_bank(IDEA, log=quiet, ask_research=researcher({}), ask_extract=copier({}), ask_judge=claims_judge(no_hook))
    assert not bank2["ok"] and "hook:" in bank2["why"] and not bank2.get("close_from_hook")


def test_the_bank_cap_never_blocks_a_quote_found_for_a_spine_claim(web, monkeypatch):
    dyson_web(web)
    monkeypatch.setattr(writer, "BANK_MAX", 1)
    bank = dict(facts=[], failed=[])
    writer._take(bank, [dict(url=WIKI, quote=CLOG, **{"for": []}), dict(url=WIKI, quote=COPIED, **{"for": []}),
                        dict(url=WIKI, quote=PAID, **{"for": ["c4"]})], "x", quiet)
    assert [f["quote"] for f in bank["facts"]] == [CLOG, PAID] and bank["over_cap"] == 1


def test_a_spine_that_stays_flat_is_not_kept(web):
    bank, *_ = dyson_bank(web)
    sc, _ = writer.write_from_bank(IDEA, dict(id="window"), "short", bank, ask=bank_writer())
    stubborn = answers({"segments": [{"i": 0, "say": FLAT, "facts": [fid(bank, COPIED)], "show": []}]})
    held, rep, _, _ = writer.hold_spine(sc, bank, IDEA, log=quiet, ask_judge=supported(), ask_rewrite=stubborn, ask_point=spine_judge(),
                                        again=lambda b, c, w: (b, []))
    assert not rep["ok"] and "hook" in rep["why"] and "no rejection" in rep["why"]


# ----------------------------------------------------------------- 3. the close is core

def closing(web):
    web.routes["https://www.pixar.example/toy-story-history"] = page(REJECT, PAID, COPIED)
    facts = [dict(id="f1", claim="r", quote=REJECT, url="https://www.pixar.example/toy-story-history"),
             dict(id="f2", claim="p", quote=PAID, url="https://www.pixar.example/toy-story-history"),
             dict(id="f3", claim="c", quote=COPIED, url="https://www.pixar.example/toy-story-history")]
    segs = [dict(say="Hoover would not license the design.", beat="hook", facts=["f1"], show=[]),
            dict(say="The High Court ruled Hoover had copied it.", beat="turn", facts=["f3"], show=[]),
            dict(say="Hoover agreed to pay four million pounds.", beat="payoff", facts=["f2"], show=[]),
            dict(say="Hoover paid for its rejection twice over.", beat="close", facts=["f1", "f2"], show=[])]
    return script(facts, segs)


def test_the_close_is_restated_never_silently_dropped(web):
    sc = closing(web)
    j = judge(lambda i, say: (False, "twice over: no quote", []) if "twice" in say else (True, "", []))
    fix = answers({"segments": [{"i": 3, "say": "The company that would not license it paid for copying it.", "facts": ["f1", "f2", "f3"]}]})
    sc2, rep, _ = writer.final_support(sc, log=quiet, ask_judge=j, ask_rewrite=fix, ask_repeats=answers({"repeats": []}),
                                       ask_point=answers({"lines": [{"i": 3, "kept": True}]}))
    assert rep["ok"] and len(sc2["segments"]) == 4 and sc2["close_line"].startswith("The company that would not license")
    assert 3 in writer.core_segments(sc) and "Core segments" in fix.calls[0] and "[3]" in fix.calls[0].split("never drop these", 1)[1]
    stubborn = answers({"segments": [{"i": 3, "say": "Hoover paid twice over.", "facts": ["f2"]}]})
    sc3, rep3, _ = writer.final_support(sc, log=quiet, ask_judge=j, ask_rewrite=stubborn, ask_point=answers({"lines": []}))
    assert not rep3["ok"] and rep3["why"] == "core_unsupported" and "3" in rep3["core_unsupported"]       # fails loudly
    assert not rep3["support_dropped"]


def test_the_last_line_is_the_close_when_none_is_marked(web):
    sc = closing(web)
    sc["segments"][3]["beat"] = "setup"
    assert writer.core_segments(sc) == {0, 1, 2, 3} and writer.spine_lines(sc)["close"] == 3


# ----------------------------------------------------------------- 4. a quote is a meaningful fragment; a line cites what it uses

def test_a_bare_number_or_date_is_never_a_verified_quote(web):
    web.routes[WIKI] = page("Born 1947.", "1983", "On 13th October 2000.", COPIED)
    facts = [dict(id="f1", claim="year", quote="1983", url=WIKI), dict(id="f2", claim="date", quote="on 13th October 2000.", url=WIKI),
             dict(id="f3", claim="ruling", quote=COPIED, url=WIKI)]
    rep = writer.verify(script(facts, [dict(say="In two thousand the court ruled.", beat="hook", facts=["f1", "f2", "f3"], show=[])]))
    assert [r["found"] for r in rep["facts"]] == [False, False, True] and rep["facts_unverified"] == ["f1", "f2"]
    assert writer.thin_quote("Thursday 4th October 2001") and writer.thin_quote("The appeal should be dismissed.") is None
    assert writer.thin_quote("He then created 5,127 prototypes.") is None


def test_a_line_keeps_citing_only_the_facts_it_uses(web):
    web.routes[WIKI] = page(COPIED, "Dyson was born in Cromer, Norfolk, in 1947.")
    facts = [dict(id="f1", claim="ruling", quote=COPIED, url=WIKI), dict(id="f2", claim="born", quote="Dyson was born in Cromer, Norfolk, in 1947.", url=WIKI)]
    sc = script(facts, [dict(say="The High Court ruled that Hoover had copied him.", beat="hook", facts=["f1", "f2"], show=[]),
                        dict(say="Dyson was born in Cromer.", beat="close", facts=["f2"], show=[])])

    def ask(system, content):
        lines = json.loads(content.split("each one cites\n", 1)[1])
        return json.dumps({"lines": [{"i": x["i"], "supported": True, "speech": [], "uses": ["f1"] if x["i"] == 0 else ["f2"]}
                                     for x in lines]}), use()
    sc2, rep, _ = writer.final_support(sc, log=quiet, ask_judge=ask)
    assert rep["ok"] and sc2["segments"][0]["facts"] == ["f1"] and sc2["segments"][1]["facts"] == ["f2"]
    assert rep["uncited"] == [dict(stage="final", sid=0, say=sc["segments"][0]["say"], removed=["f2"])]


def test_threads_asking_one_page_fetch_it_once_and_a_refusal_never_overwrites_it(web):
    """A site that answers the first request and refuses the next with a 403 (15 Nov, the VGC page): six threads
    verifying quotes of that page all read the one fetched copy."""
    import threading
    import time as _t
    served = []

    def once(url):
        served.append(url)
        _t.sleep(0.05)
        return page(REJECT, PAID) if len(served) == 1 else 403
    web.routes[COMPANY] = once
    bank = dict(facts=[], failed=[])
    writer._take(bank, [dict(url=COMPANY, quote=q, **{"for": ["c1"]}) for q in (REJECT, PAID) * 3], "idea", quiet)
    assert len(served) == 1 and [f["quote"] for f in bank["facts"]] == [REJECT, PAID] and not bank["failed"]
    assert isinstance(writer._PAGES[COMPANY], str) and threading.active_count() >= 1


# ----------------------------------------------------------------- 5. speech: in the line, and that person's

def test_speech_named_from_the_quote_but_not_in_the_line_does_not_stand(web):
    """The hole: the judge named "I'm in" (the quote's words), the line says "I'm all in". The span must stand in the
    LINE and verbatim in a cited quote: it does not, so the line is unsupported whatever the verdict."""
    seg = dict(say="He said, I'm all in.", facts=["f1"])
    assert "not in the line" in writer.speech_backed("I'm in", seg, {"f1": "I'm in"})
    assert "no verified quote" in writer.speech_backed("I'm all in", seg, {"f1": "I'm in"})
    web.routes[WIKI] = page("And I said, I'm in.")
    sc = script([dict(id="f1", claim="Hanks agreed", quote="And I said, I'm in.", url=WIKI)],
                [dict(say="He said, I'm all in.", beat="hook", facts=["f1"], show=[])])
    got, _ = writer.judge_support(sc, [0], ask=judge(lambda i, say: (True, "", [{"words": "I'm in", "who": "Tom Hanks"}])))
    assert not got[0]["ok"] and "not in the line" in got[0]["bad_speech"][0]["why"]


def test_speech_must_be_in_a_quote_of_that_person(web):
    q = "Katzenberg told them, let's make it edgier."
    seg = dict(say="Hanks told them, let's make it edgier.", facts=["f1"])
    assert "Jeffrey Katzenberg" in writer.speech_backed("let's make it edgier", seg, {"f1": q}, who="Tom Hanks",
                                                        speakers={"f1": "Jeffrey Katzenberg"})
    assert writer.speech_backed("let's make it edgier", seg, {"f1": q}, who="Katzenberg", speakers={"f1": "Jeffrey Katzenberg"}) is None
    assert writer.speech_backed("let's make it edgier", seg, {"f1": q}, who="Tom Hanks", speakers={}) is None   # unknown: not a mismatch
    assert writer.same_person("Mr Dyson", "Sir James Dyson") and not writer.same_person("Tom Hanks", "Tim Allen")


# ----------------------------------------------------------------- 6. a rejected point rewrite says why

def test_a_rejected_point_rewrite_records_its_reason(web):
    sc = closing(web)
    sc["segments"][0]["say"], sc["segments"][0]["say_written"] = "Hoover is a vacuum company.", "Hoover would not license the design."
    sc["segments"][0]["facts"] = ["f1"]
    null = answers({"segments": [{"i": 0, "say": None}]})
    sc2, rep, _ = writer.final_support(sc, log=quiet, ask_judge=supported(), ask_rewrite=null,
                                       ask_point=answers({"lines": [{"i": 0, "kept": False, "lost": "the refusal"}]}))
    assert rep["point_kept_old"] == [0] and rep["point_failed"][0]["why"] == "the rewrite answered null: it found no checked quote that says it"
    bad = answers({"segments": [{"i": 0, "say": "Hoover laughed Dyson out of the room.", "facts": ["f1"]}]})
    j = judge(lambda i, say: (False, "laughed: no quote", []) if "laughed" in say else (True, "", []))
    sc3, rep3, _ = writer.final_support(sc, log=quiet, ask_judge=j, ask_rewrite=bad,
                                        ask_point=answers({"lines": [{"i": 0, "kept": False, "lost": "the refusal"}]}))
    assert "do not stand on their quotes: laughed: no quote" in rep3["point_failed"][0]["why"]


# ----------------------------------------------------------------- 7. the ideas: a cheap spine probe, and the next idea

def test_the_ideas_probe_tests_spine_verifiability_by_fetching_only(web):
    import wf3
    dyson_web(web)
    p = wf3.spine_probe(IDEA)
    assert p["beats"] == dict(hook=True, turn=True, payoff=True, close=True) and p["ok"]
    assert not p["claims"][0]["verified"] and "bare number" in p["claims"][0]["why"]      # the "1983" evidence is refused
    blind = dict(IDEA, spine=[dict(x, source=dict(url=PARL, quote="A sentence that is on no page at all.")) for x in IDEA["spine"]])
    q = wf3.spine_probe(blind)
    assert q["share"] == 0 and not q["ok"]
    a = dict(IDEA, strength=9, supply=dict(ok=True, pictures=8), spine_probe=q)
    b = dict(IDEA, strength=8, supply=dict(ok=True, pictures=8), spine_probe=p)
    assert sorted([a, b], key=wf3.idea_rank)[0] is b                     # a verified spine outranks a point of strength


def stage(web, tmp_path, monkeypatch, banks):
    import wf3
    calls = []

    def bank_of(idea, log=print, **k):
        calls.append(idea["title"])
        return banks[idea["title"]]()
    real_write = writer.write_from_bank
    monkeypatch.setattr(writer, "spine_bank", bank_of)
    monkeypatch.setattr(writer, "write_from_bank",
                        lambda idea, card, form, bank, voices=None, notes=None: real_write(idea, card, form, bank, ask=bank_writer(SHARP)))
    monkeypatch.setattr(writer, "hold_spine", lambda sc, bank, idea=None, log=print, again=None, **k: (sc, dict(ok=True, verify=writer.verify(sc), spine=[]), [], bank))
    monkeypatch.setattr(writer, "final_support", lambda sc, log=print, style=None, **k: (sc, dict(ok=True, verify=writer.verify(sc)), []))
    monkeypatch.setattr(wf3, "able_cards", lambda ctx: [dict(id="window", name="Window", look="framed", buildable=True)])
    ctx = types.SimpleNamespace(run_dir=str(tmp_path), log=quiet, args={"ear": False, "annotate": False})
    ideas_out = dict(ideas=[dict(title="A dead end", style="window", supply=dict(ok=True)), dict(IDEA, supply=dict(ok=True))])
    return wf3, ctx, ideas_out, calls


def test_the_stage_takes_the_next_idea_when_a_spine_cannot_be_verified(web, tmp_path, monkeypatch):
    dead = dict(idea="A dead end", claims=[dict(id="c1", beat="hook", claim="x", stated=False, missing="no page", queries=["q"] * 6,
                                                rounds=3)], facts=[], ok=False,
                why="no readable page states these spine claims after 3 search rounds: hook: x")
    good = dyson_bank(web)[:2]                       # researched before writer.spine_bank is stood in for
    wf3, ctx, ideas_out, calls = stage(web, tmp_path, monkeypatch, {"A dead end": lambda: (dead, [use()]), IDEA["title"]: lambda: good})
    r = wf3.script(ctx, ideas_out)
    assert calls == ["A dead end", IDEA["title"]] and r["idea"] == IDEA["title"]
    assert r["ideas_tried"][0]["title"] == "A dead end" and "3 search rounds" in r["ideas_tried"][0]["why"]
    assert r["script"]["segments"][0]["say"] == SHARP and r["spine"]["beats"]["hook"]["say"] == SHARP
    assert {q["quote"] for q in r["spine"]["beats"]["payoff"]["quotes"]} == {PAID}
    assert os.path.exists(tmp_path / "spine0.json") and os.path.exists(tmp_path / "spine1.json")
    assert [s["stage"] for s in r["spend"]][:2] == ["spine", "spine"]


def test_the_stage_stops_clearly_when_no_idea_has_a_verified_spine(web, tmp_path, monkeypatch):
    dead = lambda: (dict(idea="x", claims=[], facts=[], ok=False, why="no readable page states these spine claims"), [])  # noqa: E731
    wf3, ctx, ideas_out, calls = stage(web, tmp_path, monkeypatch, {"A dead end": dead, IDEA["title"]: dead})
    with pytest.raises(RuntimeError, match="no idea's spine stands on verified quotes"):
        wf3.script(ctx, ideas_out)
    assert len(calls) == 2
