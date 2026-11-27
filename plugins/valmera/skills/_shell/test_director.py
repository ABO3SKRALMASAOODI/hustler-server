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
    assert d["payoff_seg"] == 2 and d["turn_seg"] == d["story_turn_seg"] == 1
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
    assert d["asks"] == []
    assert {"hook", "payoff"} <= {beat["role"] for beat in d["beats"]}


def test_seed_and_device_ledger_do_not_change_the_story():
    sents, words = DR.numbered(SENTENCES, rows())
    hot = DR.stress_words(words, STRESS)
    plain = DR.signal_director(sents, words, hot, seed=1)
    recent = DR.signal_director(sents, words, hot, seed=7, recent=["grid, neg, chapter"])
    assert plain == recent


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
    assert by["diecut"]["at"] == [30, 90] and by["diecut"]["t"] == "smart."
    assert any(a["dev"] == "finish" and a.get("surface") == "crt" and a["f"] == [100, 190] for a in asks)
    assert not any(a.get("surface") == "mono" for a in asks)                      # mono is the builder's own black and white
    assert by["arc"]["marks"] == [dict(f=50, stakes=-0.4, year=1999)]
    ans, _ = DR.palette_asks(dict(asks=[dict(dev="answer", at=dict(w=4, seg=1, word="Ask"), args={}, pics=[])]),
                             lambda w: 10 * w, None, None, 400)
    assert ans[0]["t"] == "Ask"                                                   # its diecut fallback has its word


def test_hero_device_performs_the_asks_on_its_frames():
    asks = [dict(dev="white", f=105), dict(dev="finish", f=[150, 200]), dict(dev="neg", f=20)]
    performed = []
    out = DR.around(asks, [[100, 160]], 400, performed)
    assert [a["f"] for a in out] == [20]
    assert [a["at"] for a in performed] == [105, 150]


def test_around_keeps_entire_device_out_of_verified_studio_titles():
    spans = [[0, 84], [500, 659], [701, 747], [767, 812], [846, 983]]
    asks = [dict(dev="typewriter", f=[528, 700]), dict(dev="split", f=[566, 700]),
            dict(dev="match_pos", f=[396, 505], cuts=[396, 410]), dict(dev="chapter", f=718)]
    placed = DR.around(asks, spans, 983)
    assert [a["f"] for a in placed] == [[396, 500]]
    assert placed[0]["bound"] == 500
    assert placed[0]["cuts"] == [396, 410]


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


# --------------------------------------------------------------------- word-numbered fields reach the devices as frames (review 20 Nov)

def _scene(tmp_path, n_words=30, per_word=9):
    """A take of solid colour with stills beside it, and its words every ``per_word`` frames from frame 30."""
    from PIL import Image
    pics, assets = [], {}
    for k, g in enumerate((30, 90, 150, 210, 60, 120)):
        p = tmp_path / f"p{k}.png"
        Image.new("RGB", (480, 640), (g, g, 255 - g)).save(p)
        assets[f"p{k}"] = dict(kind="image", path=str(p), w=480, h=640)
        pics.append(f"p{k}")
    dur = 30 + n_words * per_word + 30
    items = [dict(id="s0", k="shot", f=[0, dur // 2], pic="p0"), dict(id="s1", k="shot", f=[dur // 2, dur], pic="p1")]
    l1 = dict(l1=1, sid="t", seed=3, fps=30, frame=[540, 960], dur_f=dur, assets=assets, spine=[], voices={}, film={}, items=items, sound=[])
    return l1, (lambda w: 30 + w * per_word if 0 <= w < n_words else None)


def test_a_directors_contour_grid_collage_and_callback_land_on_the_frames_of_their_words(tmp_path):
    pal = DR._palette()
    if pal is None:                    # a worker that has not shipped the palette yet: the asks wait for its release
        import pytest
        pytest.skip("the worker has no scene.palette")
    assert "steps[]" in pal.word_fields("grid") and "sections[].at" in pal.word_fields("contour")
    l1, frame_of = _scene(tmp_path)
    words = [dict(n=i, w=f"w{i}", seg=i // 10) for i in range(30)]
    d = dict(asks=[
        # words 2-8 strobe, 8-12 hold: the section's frames, not frames 2-8 at the start of the film
        dict(dev="contour", at=dict(w=2, seg=0, word="w2"), args=dict(sections=[dict(at=[2, 7], energy="strobe"), dict(at=[8, 12], energy="hold")],
                                                                      stress=[[4, 0.9], 6]), pics=[]),
        dict(dev="grid", at=dict(w=14, seg=1, word="w14"), args=dict(steps=[14, 15, 16, 17, 18, 19]), pics=["P1", "P2"]),
        dict(dev="collage", at=dict(w=21, seg=2, word="w21"), args=dict(steps=[21, 23, 25, 27]), pics=["P3"]),
        dict(dev="callback", at=dict(w=27, seg=2, word="w27"), until=dict(w=29, seg=2, word="w29"), args={"from": [0, 3]}, pics=[])],
        sections=[])
    asks, notes = DR.palette_asks(d, frame_of, lambda seg: (frame_of(seg * 10), frame_of(min(seg * 10 + 9, 29)) + 9),
                                  {"P1": "p2", "P2": "p3", "P3": "p4"}.get, l1["dur_f"])
    by = {a["dev"]: a for a in asks}
    c = by["contour"]
    assert c["sections"][0]["at"] == [frame_of(2), frame_of(8)] and c["sections"][1]["at"] == [frame_of(8), frame_of(13)]
    assert c["stress"] == [[frame_of(4), 0.9], frame_of(6)] and c["f"][1] >= frame_of(13)
    g = by["grid"]
    assert g["steps"] == [frame_of(w) for w in range(14, 20)] and g["pool"] == ["p2", "p3"] and g["f"][1] > frame_of(19)
    assert by["collage"]["steps"] == [frame_of(w) for w in (21, 23, 25, 27)] and by["callback"]["from"] == [frame_of(0), frame_of(4)]
    out, log = pal.apply(l1, str(tmp_path), asks)
    assert all(r["done"] and not r["fallback"] for r in log), [(r["dev"], r.get("tried")) for r in log]
    tiles = sorted((i for i in out["items"] if str(i["id"]).startswith("dr1.t")), key=lambda i: i["f"][0])
    assert [t["f"][0] for t in tiles] == [frame_of(w) for w in range(14, 20)]          # one tile per word, on its word
    prints = sorted((i for i in out["items"] if str(i["id"]).startswith("dr2.c")), key=lambda i: i["f"][0])
    assert [p["f"][0] for p in prints] == [frame_of(w) for w in (21, 23, 25, 27)]
    strobe = [i for i in out["items"] if i["k"] == "shot" and str(i["id"]).startswith("dr0.s0")]
    assert strobe and min(s["f"][0] for s in strobe) == frame_of(2) and max(s["f"][1] for s in strobe) == frame_of(8)
    assert all(s["f"][0] >= frame_of(2) for s in strobe)                                # never the film's first frames


def test_stress_alone_keeps_the_composed_image():
    text = ["We started with nothing at all.", "Then the first shop opened in a small town by the river.",
            "Every year we added one more shop and one more city.", "Some years it nearly broke us.",
            "My father worked every single day of every single week.", "Now we all live within walking distance."]
    sl = [dict(i=i, text=t) for i, t in enumerate(text)]
    rw, t = [], 0.0
    for s_ in sl:
        for w in s_["text"].split():
            rw.append(dict(w=w, t0=t, t1=t + 0.3, seg=s_["i"]))
            t += 0.36
        t += 0.4
    sents, words = DR.numbered(sl, rw)
    hot = [w["n"] for w in words if w["n"] % 3 == 1]
    direction = DR.signal_director(sents, words, hot)
    assert direction["asks"] == [] and direction["sections"] == []
    assert all(beat["word"] == words[beat["w"]]["w"] for beat in direction["beats"])


def test_the_cache_is_keyed_on_the_words_it_read(tmp_path):
    first = dict(asks=[dict(dev="neg", at=dict(w=3, word="smart."))], beats=[], sections=[])
    again = dict(asks=[dict(dev="tint", at=dict(w=1, word="is"))], beats=[], sections=[])
    ask = Ask(first, again)
    d1 = DR.direct(str(tmp_path), SENTENCES, rows(), STRESS, ask=ask, recent=[])
    assert d1["key"] and DR.direct(str(tmp_path), SENTENCES, rows(), STRESS, ask=ask, recent=[])["key"] == d1["key"]
    assert len(ask.calls) == 1                                       # same words: the cached answer
    rescored = [dict(i=0, text="AI is not smart at all."), SENTENCES[1], SENTENCES[2]]
    out, t = [], 0.0
    for s in rescored:
        for w in s["text"].split():
            out.append(dict(w=w, t0=t, t1=t + 0.3, seg=s["i"]))
            t += 0.35
    d2 = DR.direct(str(tmp_path), rescored, out, STRESS, ask=ask, recent=[])
    assert len(ask.calls) == 2 and d2["key"] != d1["key"]           # the lines were scored again: directed again
    assert d2["asks"][0]["dev"] == "tint"


def test_a_provider_failure_is_asked_again_on_resume(tmp_path):
    ask = Ask(RuntimeError("overloaded"))
    d1 = DR.direct(str(tmp_path), SENTENCES, rows(), STRESS, ask=ask, recent=[])
    assert d1["by"] == "signal" and d1["retry"] and d1["beats"]
    ask2 = Ask(RuntimeError("still overloaded"))                    # fails again: the signal answer it had is kept
    d2 = DR.direct(str(tmp_path), SENTENCES, rows(), STRESS, ask=ask2, recent=[])
    assert len(ask2.calls) == 1 and d2["by"] == "signal" and d2["asks"] == d1["asks"] and d2["retry"]
    good = dict(asks=[dict(dev="lamp", at=dict(w=3, word="smart."))], beats=[], sections=[])
    ask3 = Ask(good)
    d3 = DR.direct(str(tmp_path), SENTENCES, rows(), STRESS, ask=ask3, recent=[])
    assert len(ask3.calls) == 1 and d3["by"] == "model" and not d3["retry"] and d3["asks"][0]["dev"] == "lamp"
    ask4 = Ask()
    assert DR.direct(str(tmp_path), SENTENCES, rows(), STRESS, ask=ask4, recent=[])["by"] == "model" and not ask4.calls


def test_the_sheet_says_what_the_material_can_hold(tmp_path):
    from PIL import Image, ImageDraw
    pics = []
    for k in range(2):
        p = tmp_path / f"r{k}.png"
        im = Image.new("RGB", (360, 640), (30, 30, 30))
        ImageDraw.Draw(im).ellipse([90, 210, 270, 390], fill=(240, 230, 210))
        im.save(p)
        pics.append(dict(id=f"P{k}", path=str(p), kind="photo"))
    ask = Ask(dict(asks=[], beats=[], sections=[]))
    d = DR.direct(str(tmp_path), SENTENCES, rows(), STRESS, pictures=pics, take=dict(on_camera=True), ask=ask, recent=[])
    text = ask.calls[0]["content"][0]["text"]
    assert "What this material can hold" in text and "round shapes: 2 of the pictures" in text
    assert "use most of the palette" not in DR.SYSTEM and "10 to 30" not in DR.SYSTEM
    assert d["can"]
    if DR._palette() is not None:      # the palette's own cards (a worker without it shows the alien bank's menu)
        assert "rhyme (needs rounds" in text                          # information for the model, not a gate
        assert "lift 0-0.05 frame widths" in text                     # the cards carry their units


def test_devices_on_the_speaker_hold_the_take_and_cutaways_step_round_them():
    asks = [dict(dev="lamp", id="dr0", f=[30, 60], at=[30, 60], t="AI"), dict(dev="neg_world", id="dr1", f=[200, 240]),
            dict(dev="white", id="dr2", f=100), dict(dev="spotlight", id="dr3", f=[230, 260])]
    spans = DR.take_spans(asks, dur_f=400)
    assert spans == [[29, 70], [199, 260]]
    ins = [(0, 50, "clip", "a", dict(src=[10.0, 11.6667])), (90, 120, "photo", "b", {}), (180, 300, "clip", "c", dict(src=[0.0, 4.0]))]
    rows_, cleared = DR.hold_take(ins, spans)
    assert [(r[0], r[1]) for r in rows_] == [(0, 29), (90, 120), (180, 199), (260, 300)]
    assert rows_[0][4]["src"] == [10.0, round(10.0 + 29 / 30, 4)]   # a clip cut keeps its own speed
    assert rows_[3][4]["src"] == [round(80 / 30, 4), 4.0]
    assert cleared == 21 + 61


def test_a_word_number_written_as_text_becomes_the_words_said_there(tmp_path):
    """20 Nov rating: ladders printed '3 4 5 11' and type pages '0 1 2 3' where the answer put word numbers in text fields."""
    sents, words = DR.numbered(SENTENCES, rows())
    ans = dict(asks=[dict(dev="ladder", at=dict(w=4, word="Ask"), args=dict(words=[dict(t=5, w="whisper"), dict(t=6, w="whisper"), dict(t=11, w="shout")])),
                     dict(dev="type_run", at=dict(w=4, word="Ask"), until=dict(w=7, word="and"), args=dict(words=[4, "5", 6])),
                     dict(dev="verb", at=dict(w=3, word="smart."), args=dict(t=3, verb="punch")),
                     dict(dev="diecut", at=dict(w=5, word="four"), args=dict(t="four AIs")),
                     dict(dev="staircase", at=dict(w=4, word="Ask"), args=dict(lines=[[4, 6]]))])
    d, rep = DR.repair(ans, sents, words)
    a = d["asks"]
    assert [r["t"] for r in a[0]["args"]["words"]] == ["four", "AIs", "answers"] and a[0]["args"]["words"][2]["w"] == "shout"
    assert a[1]["args"]["words"] == ["Ask", "four", "AIs"]
    assert a[2]["args"]["t"] == "smart"
    assert a[3]["args"]["t"] == "four AIs"                                       # text stays text
    assert a[4]["args"]["lines"] == ["Ask four AIs"]
    assert sum("word number" in r or "word numbers" in r for r in rep) >= 4


def test_a_figure_he_says_stays_the_figure():
    sents = [dict(i=0, text="I got fired after 20 minutes on the floor of the store.")]
    rws = [dict(w=w, t0=0.3 * k, t1=0.3 * k + 0.25, seg=0) for k, w in enumerate(sents[0]["text"].split())]
    s, words = DR.numbered(sents, rws)
    d, _ = DR.repair(dict(asks=[dict(dev="diecut", at=dict(w=4, word="20"), args=dict(t="20")),
                                dict(dev="verb", at=dict(w=4, word="20"), args=dict(t=7))]), s, words)
    assert d["asks"][0]["args"]["t"] == "20"                                     # said in its sentence: the figure itself
    assert d["asks"][1]["args"]["t"] == "the"                                    # word 7 is 'the'


def test_a_device_that_sets_his_words_and_got_none_sets_the_words_said():
    sents, words = DR.numbered(SENTENCES, rows())
    ans = dict(asks=[dict(dev="type_run", at=dict(w=4, word="Ask"), until=dict(w=6, word="AIs")),
                     dict(dev="verb", at=dict(w=3, word="smart.")),
                     dict(dev="ladder", at=dict(w=12, word="I")),
                     dict(dev="white", at=dict(w=2, word="not"))])
    d, rep = DR.repair(ans, sents, words)
    a = d["asks"]
    assert a[0]["args"]["t"] == "Ask four AIs"
    assert a[1]["args"]["t"] == "smart"
    assert a[2]["args"]["t"] == "I trust the seeing"                              # its word and the next ones of its sentence
    assert "t" not in a[3]["args"]                                               # a flash sets no words
    assert sum("set no words" in r for r in rep) == 3


def test_a_counters_figure_word_lands_on_its_frame():
    """A counter's lands is a word: read as a frame it drew the count in the first second (five Cuban films, 20 Nov)."""
    d = dict(asks=[dict(dev="counter", at=dict(w=20, seg=1, word="1,200"), args=dict(at=20, lands=21, to=1200, fmt="{n} MILES", **{"from":0})),
                   dict(dev="staircase", at=dict(w=20, seg=1, word="x"), args=dict(lines=[dict(t="1,200", at=21), dict(t="MILES", at=22)]))])
    asks, _ = DR.palette_asks(d, lambda w: 10 * w, lambda i: (i * 100, i * 100 + 90), None, 400)
    # A flat figure first shows on its spoken word: never ahead of it, never fictional values on the way.
    assert "at" not in asks[0] and (asks[0]["f"][0] if isinstance(asks[0]["f"], list) else asks[0]["f"]) == 200
    assert asks[0]["lands"] == 200 and asks[0]["from"] == asks[0]["to"] == 1200 and asks[0]["run"] == 0
    assert [x["at"] for x in asks[1]["lines"]] == [210, 220]


def test_a_counter_over_a_sourced_progression_keeps_its_counted_start():
    """'Count between two sourced endpoints': a progression bound from the sources (wf1_display.bind) keeps its from."""
    facts = [dict(source=dict(url="https://example.com"), relation="Members", value="1,200",
                  progression={"from": 300, "to": 1200})]
    d = dict(asks=[dict(dev="counter", at=dict(w=20, seg=1, word="300"),
                        args=dict(lands=24, to=1200, fmt="{n}", facts=facts, **{"from": 300}))])
    asks, _ = DR.palette_asks(d, lambda w: 10 * w, lambda i: (i * 100, i * 100 + 90), None, 400)
    assert asks[0]["from"] == 300 and asks[0]["to"] == 1200 and "run" not in asks[0]
    assert (asks[0]["f"][0] if isinstance(asks[0]["f"], list) else asks[0]["f"]) == 200


def test_the_strobe_floor_leaves_one_and_two_frame_shots_to_punctuation():
    lo, hi = DR.energy_band("strobe")["hold"]
    assert lo * 30 >= 3.9 and hi <= 0.6


def test_only_the_directors_explicit_accent_reaches_the_word_clock():
    sentences, words = DR.numbered(SENTENCES, rows())
    result, _ = DR.repair(dict(beats=[
        dict(w=0, role="hook"),
        dict(w=3, role="turn", sound="air", sound_reason="the reveal replaces the rehearsal"),
    ]), sentences, words)
    assert DR.sound_cues(result, lambda w: 11*w) == [
        dict(f=33, role="turn", sound="air", why="the reveal replaces the rehearsal")]


def test_m09_sound_partner_tracks_pictures_from_sheet_to_scene():
    sentences, words = DR.numbered(SENTENCES, rows())
    result, _ = DR.repair(dict(beats=[dict(w=3,role="contrast",sound="impact",sound_reason="Underbody reveal",
                                     sound_partner=dict(before="P7",after="P4",device="slice"))]),sentences,words)
    cue, = DR.sound_cues(result,lambda w:347, picture_keys={"P7":"explorer","P4":"chassis"})
    assert cue["beat"] == 0 and cue["partner"] == dict(before="explorer",after="chassis",device="slice")


def test_marketing_uses_the_existing_performance_at_its_addressed_word():
    import director as dr
    typed = "Make a vertical short with separate titles for the people and dollars."
    asks = [dict(id="dr3",dev="brackets",t="units",f=355),
            dict(id="dr5",dev="typewriter",t=typed,f=[528,622]),
            dict(id="dr6",dev="split",f=[559,622]),
            dict(id="dr10",dev="loop",f=961),
            dict(id="next",dev="name",f=300)]
    left, represented = dr.recorded_type(asks, [dict(f=[351,380],text="BOTH UNITS")],
                            [dict(f=[529,659],text=typed)],
                            [dict(frames=[500,659],device="timeline"),dict(frames=[846,983],device="cta")])
    assert [a["id"] for a in left] == ["next"]
    assert represented[0]["text"] == "BOTH UNITS"
    assert [r["owner"] for r in represented[1:]] == ["timeline", "timeline", "cta"]
    assert [r["at"] for r in represented] == [355, 528, 559, 961]
