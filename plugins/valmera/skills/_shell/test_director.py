"""The Director (director.py): a bad answer is repaired, never refused; a failed provider still gives the film devices;
the devices ledger keeps what each film laid."""
import json

import devices_ledger as DLG
import director as DR

SENTENCES = [dict(i=0, text="AI is not smart."), dict(i=1, text="Ask four AIs and you get four answers."),
             dict(i=2, text="I trust the seeing eye dog before the AI.")]


def rows():
    out, t = [], 0.0
    for s in SENTENCES:
        for w in s["text"].split():
            out.append(dict(w=w, t0=t, t1=t + 0.3, seg=s["i"]))
            t += 0.35
        t += 0.5
    return out


STRESS = [(0.75, 0.9), (2.6, 0.6), (5.1, 0.8), (7.0, 0.7)]


class Ask:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, system, content, **kw):
        self.calls.append(dict(content=content, **kw))
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return (a if isinstance(a, str) else json.dumps(a)), dict(provider="t", model="t", usd=0.01)


def test_an_unsaid_word_is_snapped_and_an_unknown_device_kept_for_the_palette(tmp_path):
    ans = dict(beats=[dict(role="hook", w=99, word="smrt", energy="strobe"), dict(role="payoff", seg=2, word="dog", energy="hold")],
               sections=[dict(segs=[0], surface="thermal", layout="grid", energy="strobe"), dict(segs=[2], surface="colour", layout="nope")],
               asks=[dict(dev="neg", at=dict(w=3, word="smart."), args=dict(run=3)), dict(dev="glitter-bomb", at=dict(seg=1, word="answerz")),
                     dict(dev="white", at=dict(w=4), until=dict(w=1))],
               display=[dict(seg=0, text="not smart", role="hook", key="smart")], payoff_seg=2, turn_seg=1)
    ask = Ask(ans)
    d = DR.direct(str(tmp_path), SENTENCES, rows(), STRESS, ask=ask, recent=[], dur_s=12.0)
    assert d["contract"] == DR.CONTRACT and d["by"] == "model"
    assert d["beats"][0]["word"] == "smart." and d["beats"][0]["w"] == 3       # 'smrt' at a word that does not exist: snapped
    assert d["asks"][1]["at"]["word"] == "answers." and d["asks"][1]["dev"] == "glitter-bomb"
    assert "until" not in d["asks"][2]                                          # an until before its start is dropped, the ask kept
    assert [s["segs"] for s in d["sections"]] == [[0], [1], [2]] or sorted(i for s in d["sections"] for i in s["segs"]) == [0, 1, 2]
    assert d["sections"][0]["layout"] == "grid" and any("layout" in r for r in d["repairs"])
    assert d["turn_seg"] == 2 and d["story_turn_seg"] == 1
    assert json.load(open(tmp_path / "director.json"))["asks"] == d["asks"]
    assert DR.direct(str(tmp_path), SENTENCES, rows(), STRESS, ask=ask) == json.load(open(tmp_path / "director.json"))
    assert len(ask.calls) == 1


def test_an_answer_without_json_is_asked_again_with_the_error(tmp_path):
    ask = Ask("I think the film should feel fast.", dict(asks=[dict(dev="tint", at=dict(w=0))], beats=[], sections=[]))
    d = DR.direct(str(tmp_path), SENTENCES, rows(), STRESS, ask=ask, recent=[])
    assert len(ask.calls) == 2 and "could not be read as JSON" in ask.calls[1]["content"][-1]["text"]
    assert d["by"] == "model" and d["asks"][0]["dev"] == "tint"
    assert any("no JSON" in r for r in d["repairs"])


def test_a_failed_provider_still_gives_the_film_devices(tmp_path):
    ask = Ask(RuntimeError("insufficient_quota"))
    d = DR.direct(str(tmp_path), SENTENCES, rows(), STRESS, ask=ask, recent=["wf1 2026-11-19: white@hook, neg@turn"])
    assert d["by"] == "signal" and any("provider failed" in r for r in d["repairs"])
    assert len(d["asks"]) >= 5 and d["beats"][0]["role"] == "hook"
    assert {"black", "white"} <= {a["dev"] for a in d["asks"]}                   # the payoff is punctuated
    assert d["asks"][0]["at"]["w"] == 0                                          # a device on screen at frame 0
    asks, _ = DR.palette_asks(d, lambda w: 10 * w, lambda i: (i * 100, i * 100 + 90), None, 400)
    assert asks and all(isinstance(a["f"], (int, list)) for a in asks)


def test_the_signal_director_varies_with_the_seed_and_the_ledger():
    sents, words = DR.numbered(SENTENCES, rows())
    hot = DR.stress_words(words, STRESS)
    a = DR.signal_director(sents, words, hot, seed=1, recent=[])
    b = DR.signal_director(sents, words, hot, seed=1, recent=[])
    assert a == b
    seqs = {tuple(x["dev"] for x in DR.signal_director(sents, words, hot, seed=s, recent=[])["asks"]) for s in range(8)}
    assert len(seqs) > 1


def test_palette_asks_spans_sections_and_the_arc():
    d = dict(asks=[dict(dev="finish", at=dict(w=2, seg=0, word="not"), args=dict(surface="gate"), pics=["P1"]),
                   dict(dev="diecut", at=dict(w=3, seg=0, word="smart."), args={}, pics=[])],
             sections=[dict(segs=[1], surface="crt"), dict(segs=[2], surface="mono")],
             arc=[dict(w=5, year=1999, stakes=-0.4)])
    asks, notes = DR.palette_asks(d, lambda w: 10 * w, lambda i: (i * 100, i * 100 + 90), {"P1": "p01"}.get, 400)
    by = {a["id"]: a for a in asks}
    by.update({a["dev"]: a for a in asks if a["dev"] != "finish"})
    by["finish"] = asks[0]
    assert by["finish"]["f"] == [20, 90] and by["finish"]["pool"] == ["p01"]
    assert by["diecut"]["at"] == 30 and by["diecut"]["t"] == "smart."
    assert any(a["dev"] == "finish" and a.get("surface") == "crt" and a["f"] == [100, 190] for a in asks)
    assert not any(a.get("surface") == "mono" for a in asks)                      # mono is the builder's own black and white
    assert by["arc"]["marks"] == [dict(f=50, stakes=-0.4, year=1999)]


def test_around_moves_asks_clear_of_a_hero_device():
    asks = [dict(dev="white", f=105), dict(dev="finish", f=[150, 200]), dict(dev="neg", f=20)]
    out = DR.around(asks, [[100, 160]], 400)
    assert [a["f"] for a in out] == [161, [161, 211], 20]


def test_the_ledger_keeps_each_films_sequence(tmp_path, monkeypatch):
    monkeypatch.setenv("VALMERA_DEVICES_LEDGER", str(tmp_path / "devices.jsonl"))
    sup = tmp_path / "supply"
    sup.mkdir()
    json.dump([dict(id="d0", dev="neg", asked="neg", beat=0, f=[0, 1], done=True, landed="neg"),
               dict(id="d1", dev="white", asked="white", beat=1, f=[40, 43], done=True, landed="white"),
               dict(id="d2", dev="rhyme", asked="rhyme", beat=None, f=[60, 62], done=True, landed="neg_split", fallback=True)],
              open(sup / "devices_log.json", "w"))
    json.dump(dict(beats=[dict(role="hook"), dict(role="payoff")], sections=[dict(surface="gate"), dict(surface="colour")], by="model"),
              open(sup / "director.json", "w"))
    row = DLG.record("wf1", "r1", "/films/a", str(sup))
    assert row["seq"] == ["neg@hook", "white@payoff", "neg_split"] and row["surfaces"] == ["gate"]
    assert DLG.recent(5) == ["wf1 " + row["at"][:10] + ": neg@hook, white@payoff, neg_split; surfaces gate"]
