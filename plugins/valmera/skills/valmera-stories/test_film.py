"""The three story styles on synthetic supply: each plan is one the engine accepts, compiles, and the taste check
passes; the writer's checks compare measured strings. No network, no model, no narration.

    PY=/Users/masaoodi/Documents/Valmera/.valmera/venv-worker/bin/python
    $PY -m pytest -q test_film.py
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import film as F  # noqa: E402
import writer  # noqa: E402


def words_for(script):
    out, t = [], 0.1
    for i, s in enumerate(script["segments"]):
        for w in s["say"].split():
            out.append(dict(w=w, start=round(t, 3), end=round(t + 0.26, 3), seg=i, chunk=i))
            t += 0.34
        t += float(s.get("pause_after", 0.3))
    return dict(voice="atlas", duration=round(t + 0.2, 2), words=out)


def picture(tmp, name, w, h, rgb):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (w, h), rgb)
    dr = ImageDraw.Draw(im)
    for k in range(0, w, 60):
        dr.line([(k, 0), (w - k, h)], fill=(255 - rgb[0], 128, rgb[2]), width=4)
    p = os.path.join(tmp, f"{name}.jpg")
    im.save(p)
    return dict(name=name, path=p, w=w, h=h, faces=[], licence="Public domain", credit="test")


SAYS = ["In nineteen seventy three the company had five thousand dollars left.", "Its founder took all of it to Las Vegas.",
        "He played blackjack through the night.", "By morning he had twenty seven thousand dollars.",
        "It paid the fuel bill on Monday.", "Today the company moves millions of parcels a day."]


def base_script(style):
    segs = [dict(say=s, show=[], beat="setup", pictures=["Founder" if i % 2 == 0 else "Jet"], melody="falling", pause_after=0.3, facts=["f1"])
            for i, s in enumerate(SAYS)]
    segs[0]["beat"] = "hook"
    segs[2]["beat"] = "low"
    sc = dict(title="The night in Las Vegas", voice="atlas", segments=segs,
              named=[dict(name="Founder", is_="person"), dict(name="Jet", is_="thing")],
              facts=[dict(id="f1", claim="x", quote="He won $27,000 from $5,000 in 1973.", url="https://example.org")])
    if style == "character-sheet":
        segs[0]["show"] = [dict(text="1973", word="nineteen")]
        sc["stats"] = [dict(id="cash", label="CASH", start="$5,000", number=5000, max=27000), dict(id="runway", label="RUNWAY", start="3 days", number=3, max=7)]
        segs[2]["ticks"] = [dict(stat="cash", to="$1,000", number=1000, word="blackjack")]
        segs[3]["ticks"] = [dict(stat="cash", to="$27,000", number=27000, word="twenty")]
        sc["overflow"] = dict(stat="cash", to="$27,000", number=27000, word="millions")
    if style == "whole-page":
        segs[1]["window"] = dict(subject="Founder", line="took all of it")
        segs[4]["window"] = dict(subject="Jet", line="the fuel bill")
    if style == "one-photograph":
        for i, s in enumerate(segs):
            s["look"] = ["whole", "A", "B", "C", "A", "whole"][i]
            s["mark"] = {1: "A", 2: "B", 3: "C"}.get(i)
        segs[-1]["show"] = ["3 of 4"]
        sc["portrait"] = dict(name="A", segment=4)
    return sc


@pytest.fixture()
def supply(tmp_path):
    pics = {"Founder": [picture(str(tmp_path), "f1", 1200, 1600, (90, 60, 40)), picture(str(tmp_path), "f2", 1600, 1067, (40, 60, 90))],
            "Jet": [picture(str(tmp_path), "j1", 2000, 1333, (60, 90, 60))]}
    return dict(sid="t", pictures=pics, sounds={}, ffmpeg="ffmpeg", tmp=str(tmp_path))


@pytest.mark.parametrize("style", ["character-sheet", "whole-page", "one-photograph"])
def test_style_compiles_and_passes_taste(style, supply, tmp_path):
    sc = base_script(style)
    sup = dict(supply, words=words_for(sc))
    if style == "one-photograph":
        ph = picture(str(tmp_path), "photo", 3000, 2100, (120, 110, 100))
        ph["faces"] = [dict(box=[0.2 + 0.2 * k, 0.4, 0.26 + 0.2 * k, 0.48], eyes=[[0.22 + 0.2 * k, 0.43], [0.24 + 0.2 * k, 0.43]], score=0.9)
                       for k in range(4)]
        ph["targets"] = {"A": {"face": 0}, "B": {"face": 1}, "C": {"box": [0.6, 0.38, 0.7, 0.5]}}
        sup["photo"] = ph
        path, rows = F.microtext_image(ph["path"], [0.15, 0.3, 0.35, 0.6], SAYS[4], str(tmp_path / "micro.jpg"), size=600, pitch=20)
        sup["microtext"] = dict(path=path, size=600, region=[0.15, 0.3, 0.35, 0.6], row_y=0.45)
    l1, beats = F.STYLES[style](sc, sup, {})
    F._type()
    import scene
    ok, why = scene.eligible(l1)
    assert ok, why
    plan = scene.compile_scene(l1, str(tmp_path))
    assert plan.dur == l1["dur_f"]
    t = F.taste_check(l1, beats)
    assert t["ok"], t["problems"]
    assert beats["dur_f"] > beats["voice_f"][1]
    if style == "character-sheet":
        # every stat change ticks on its own frame, and the full stop of a chosen sentence becomes the cut
        assert len([s for s in l1["sound"] if s["s"] == "tick_1"]) <= 2
        assert any(i["k"] == "word" and i["t"] == "." and i.get("fill") for i in l1["items"])
    if style == "whole-page":
        words = [i for i in l1["items"] if i["k"] == "word" and i.get("z") == 3]
        assert len(words) == len(sup["words"]["words"])
        assert min(words[0]["s"]) < 0.6               # the pull-back to the whole page
    if style == "one-photograph":
        assert beats["pieces"] == 4 and beats["marks"] == ["A", "B", "C"]
        assert beats["x2"] and beats["x2"]["segment"] == 4


def test_cover_crop_keeps_aspect_and_bounds():
    c = F.cover_crop(3000, 2000, 1080 / 1920, (0.9, 0.5))
    assert abs((c[2] - c[0]) * 3000 / ((c[3] - c[1]) * 2000) - 1080 / 1920) < 1e-6
    assert 0 <= c[0] and c[2] <= 1
    p = F.push(c, 60, 0.05, (0.5, 0.5))
    assert abs((p[-1][2] - p[-1][0]) / (c[2] - c[0]) - 0.95) < 0.01


def test_verify_compares_measured_strings(monkeypatch):
    pages = {"https://a": writer.norm("In 1973 he turned $5,000 into $27,000 at blackjack in Las Vegas, the story goes.")}
    monkeypatch.setattr(writer, "page_text", lambda u, timeout=20: pages[u])
    sc = dict(segments=[dict(say="x", show=["$27,000"], facts=["f1"])],
              facts=[dict(id="f1", quote="he turned $5,000 into $27,000 at blackjack", url="https://a")])
    assert writer.verify(sc)["ok"]
    sc["segments"][0]["show"] = ["$32,000"]
    r = writer.verify(sc)
    assert not r["ok"] and r["numbers_unbacked"] == ["32000"]
    sc["segments"][0]["show"] = []
    sc["facts"][0]["quote"] = "he lost everything at roulette"
    assert writer.verify(sc)["facts_unverified"] == ["f1"]


def test_narration_script_carries_direction_not_digits():
    sc = base_script("whole-page")
    n = writer.narration_script(sc, "03")
    assert n["band"] == "03" and n["segments"][-1]["pause_after"] == 0.0
    assert all(not any(ch.isdigit() for ch in s["text"]) for s in n["segments"])
    json.dumps(n)


def test_one_photograph_without_faces(supply, tmp_path):
    """People in profile: no face is found, and the photograph is assembled from a grid and its own named regions."""
    sc = base_script("one-photograph")
    sup = dict(supply, words=words_for(sc))
    ph = picture(str(tmp_path), "photo", 3000, 2100, (120, 110, 100))
    ph["faces"] = []
    ph["targets"] = {"A": {"box": [0.05, 0.1, 0.2, 0.8]}, "B": {"box": [0.7, 0.2, 0.85, 0.75]}, "C": {"box": [0.3, 0.2, 0.6, 0.5]}}
    sup["photo"] = ph
    l1, beats = F.STYLES["one-photograph"](sc, sup, {})
    assert beats["pieces"] == 12 + 3 and beats["marks"] == ["A", "B", "C"]
    assert F.taste_check(l1, beats)["ok"]


# ----------------------------------------------------------------- the cut: footage, pace, captions, display words

def test_a_printed_figure_is_found_on_the_words_that_say_it():
    assert ["five", "thousand"] in F.spoken_forms("$5,000")
    assert ["nineteen", "seventythree"] in F.spoken_forms("1973")
    assert F.spoken_forms("ONE MORE WEEK") == [["one", "more", "week"]]
    script = dict(segments=[dict(say="They carried one hundred and eighty-six packages in nineteen seventy-three."),
                            dict(say="He won twenty-seven thousand dollars.")])
    clock = F.Clock(words_for(script), script)
    t186, n = F.said_at(clock, 0, "186")
    assert n == 3 and abs(t186 - clock.word_t(0, "one")) < 1e-6                 # the filler "and" is stepped over
    assert abs(F.said_at(clock, 0, "1973")[0] - clock.word_t(0, "nineteen")) < 1e-6
    assert abs(F.said_at(clock, 1, "$27,000")[0] - clock.word_t(1, "twenty-seven")) < 1e-6
    assert F.said_at(clock, 1, "Memphis") is None


def test_words_the_aligner_gave_one_span_get_their_own_moments():
    script = dict(segments=[dict(say="carried one hundred and eighty-six packages")])
    w = [dict(w="carried", start=0.8, end=1.06, seg=0), dict(w="one", start=1.06, end=2.04, seg=0), dict(w="hundred", start=1.06, end=2.04, seg=0),
         dict(w="and", start=2.04, end=1.06, seg=0), dict(w="eighty-six", start=1.06, end=2.04, seg=0), dict(w="packages", start=2.04, end=2.34, seg=0)]
    clock = F.Clock(dict(duration=2.5, words=w), script)
    ts = [(x["t0"], x["t1"]) for x in clock.words]
    assert all(b > a for a, b in ts)                                            # every word lasts
    assert all(ts[i + 1][0] >= ts[i][1] - 1e-6 for i in range(len(ts) - 1))     # in order, none on top of another
    assert abs(ts[1][0] - (1.06 + F.LEAD_F / F.FPS)) < 1e-6 and abs(ts[4][1] - (2.04 + F.LEAD_F / F.FPS)) < 1e-6


def test_supply_shots_real_footage_first_and_a_new_picture_about_every_second(tmp_path):
    script = dict(segments=[dict(say="Fred flew his company's last five thousand dollars out to the desert. Then Las Vegas took him in for the night.",
                                 pictures=["Fred", "Las Vegas"]),
                            dict(say="He took it all to the blackjack table and he sat there until the morning came.", pictures=["Table"])])
    clock = F.Clock(words_for(script), script)
    pics = {"Fred": [picture(str(tmp_path), "f1", 1600, 1200, (90, 80, 70)), picture(str(tmp_path), "f2", 1600, 1200, (60, 80, 70))],
            "Las Vegas": [picture(str(tmp_path), "v1", 1600, 1200, (20, 30, 90))], "Table": [picture(str(tmp_path), "t1", 1600, 1200, (10, 90, 30))]}

    def clip(n, subject, s, face_w=0.0):
        return dict(path=str(tmp_path / f"{n}.mp4"), file=f"clips/{n}.mp4", seconds=s, subject=subject, w=1080, h=1920,
                    faces=[dict(box=[0.3, 0.3, 0.3 + face_w, 0.6], eyes=[[0.4, 0.4], [0.5, 0.4]])] if face_w else [])
    footage = {0: [clip("a", "Fred", 1.4, 0.5), clip("b", "Fred", 1.2, 0.2)] + [clip(n, "Las Vegas", 1.6) for n in "cdeghijk"]}
    f_end = F.Clock.f(clock.dur)
    tl = F.supply_shots(script, clock, pics, footage, f_end, stress=[], aim_s=1.0)
    assert tl[0][0] == 0 and tl[-1][1] == f_end
    assert all(a[1] == b[0] for a, b in zip(tl, tl[1:]))                        # one picture at a time, no gap
    assert tl[0][2] == "clip" and tl[0][4]["path"].endswith("b.mp4")            # the wider clip opens; the close-up waits
    for f0, f1, kind, name, rec in tl:
        if kind == "clip":
            assert (f1 - f0) / F.FPS <= rec["seconds"] / 0.75 + 0.04            # a clip never held past its length (a quarter slow at most)
            assert 0 <= rec["src"][0] < rec["src"][1] <= rec["seconds"] + 1e-6
    vegas = F.Clock.f(clock.word_t(0, "Then")) - 1
    first_vegas = next(r for r in tl if r[3] == "Las Vegas")
    assert abs(first_vegas[0] - vegas) <= 2                                      # the place arrives with the clause that names it
    assert all(a[4]["path"] != b[4]["path"] for a, b in zip(tl, tl[1:]) if a[2] == b[2] == "photo" and len(pics[a[3]]) > 1)
    # the line after Las Vegas has one photograph of its own: a Vegas clip nobody played leads it in
    line2 = [r for r in tl if r[0] >= F.Clock.f(clock.segs[1][0]) - 1]
    assert line2[0][2] == "clip" and line2[0][3] == "Las Vegas" and line2[-1][3] == "Table"
    holds = sorted((r[1] - r[0]) / F.FPS for r in tl)
    assert holds[len(holds) // 2] <= 1.6


def test_captions_ink_word_by_word_and_yield_the_seat_to_a_display_word():
    script = dict(segments=[dict(say="In nineteen seventy-three he moved the company to Memphis for good.", show=["1973", "Memphis"])])
    clock = F.Clock(words_for(script), script)
    voices = {"cap": {"font": "InterDisplay-Bold.ttf", "px": 56, "track": 0}, "dsp": {"font": "InterDisplay-Black.ttf", "px": 170, "track": -2}}
    P = F.Plan(voices, {})
    shown = F.display_words(P, clock, script, "dsp", 0.627, F.Clock.f(clock.dur), gap_s=1.0)
    assert [t for _, _, t in shown] == ["1973", "Memphis"]
    assert abs(shown[0][0] - (F.Clock.f(clock.word_t(0, "nineteen")) - 1)) <= 1
    F.caption_build(P, clock, "cap", 0.615, hold=[(a - 1, b) for a, b, _ in shown])
    caps = [i for i in P.items if i["k"] == "word" and i.get("role") == "cap"]
    big = [i for i in P.items if i["k"] == "word" and i.get("role") == "big"]
    assert caps and len(big) == 2
    for c in caps:                                                              # never a caption while a display word has the seat
        assert not any(b["f"][0] <= f < b["f"][1] for b in big for f in range(c["f"][0], c["f"][1]))
    line = [c for c in caps if c["f"][1] == caps[0]["f"][1]]
    assert all(x["x"] < y["x"] for x, y in zip(line, line[1:])) and all(x["f"][0] <= y["f"][0] for x, y in zip(line, line[1:]))


def test_a_head_is_slid_clear_of_the_sheet_when_the_picture_has_room():
    face = [0.40, 0.30, 0.20, 0.20, 0.38]
    it = dict(id="sh0001", k="shot", f=[0, 3], pic="p0", crop=[[0.2, 0.2, 0.8, 0.9]] * 3)
    moved = F.clear_of([it], {"p0": dict(faces=[face])}, [0.05, 0.105, 0.46, 0.24], {"sh0001"})
    assert moved == 1
    c = it["crop"][0]
    top = (face[1] - 0.28 * face[3] - c[1]) / (c[3] - c[1])
    left = (face[0] - c[0]) / (c[2] - c[0])
    assert top >= 0.24 - 1e-3 or left >= 0.46 - 1e-3
    assert abs((c[2] - c[0]) - 0.6) < 1e-4 and abs((c[3] - c[1]) - 0.7) < 1e-4  # the framing keeps its size: it slides


def test_a_podcast_cut_keeps_the_take_as_spine_and_cuts_away_on_what_is_named(tmp_path):
    """The footage cut on synthetic supply: the engine accepts the scene, every frame has one base picture, the
    hook and the payoff are the speaker, wide footage plays in a window over itself, and taste passes."""
    from PIL import Image
    script = dict(title="The Deal He Passed On", segments=[
        dict(say="So it was natural for him to come to me.", pictures=[]),
        dict(say="He showed me UberCab, and I thought it was a great idea for the city.", pictures=["UberCab"]),
        dict(say="Your biggest challenge is going to be the taxi commissions in every town.", pictures=["UberCab"]),
        dict(say="Never came back to me.", pictures=["UberCab"])])
    wj = words_for(script)
    take = str(tmp_path / "take.jpg")
    Image.new("RGB", (1920, 1080), (30, 30, 34)).save(take)
    clips = []
    for n in "abc":
        p = str(tmp_path / f"{n}.jpg")                        # a still stands in for a clip file: the plan only needs its path to exist
        Image.new("RGB", (1080, 608), (90, 120, 60)).save(p)
        clips.append(dict(path=p, file=f"clips/{n}.mp4", seconds=1.5, subject="UberCab", w=1080, h=608, faces=[], shows="the app"))
    sup = dict(sid="t", words=wj, narration=str(tmp_path / "none.wav"), ffmpeg="ffmpeg", pictures={"UberCab": [picture(str(tmp_path), "u1", 1600, 1200, (80, 80, 120))]},
               footage=[dict(i=1, clips=clips[:2]), dict(i=2, clips=clips[2:])],
               take=dict(path=take, w=1920, h=1080, fps=30.0, dur=wj["duration"], faces=[dict(box=[0.60, 0.2, 0.74, 0.5], eyes=[[0.64, 0.3], [0.70, 0.3]]),
                                                                                   dict(box=[0.30, 0.5, 0.33, 0.56], eyes=[[0.31, 0.52], [0.32, 0.52]])]))
    l1, beats = F.podcast_cut(script, sup, {})
    cov = [0] * l1["dur_f"]
    for it in l1["items"]:
        if it["k"] == "shot":
            for f in range(it["f"][0], it["f"][1]):
                cov[f] += 1
    assert set(cov) == {1}                                                   # one base picture on every frame
    shots = sorted((i for i in l1["items"] if i["k"] == "shot"), key=lambda i: i["f"][0])
    assert shots[0]["pic"] == "main" and shots[-1]["pic"] == "main"          # the hook and the payoff are his
    assert all(i["pic"] == "main" for i in shots if i["f"][1] > beats["turn_f"] - 4)   # from the turn on, only him
    wins = [p for p in beats["pictures"] if p.get("mode") == "window"]
    assert wins and all(p["f"][0] >= int(1.2 * F.FPS) for p in beats["pictures"])
    cards = [i for i in l1["items"] if i["k"] == "card" and i.get("pic") != "plate" and i.get("src")]
    assert len(cards) == len(wins) and all(0.19 < c["y"] - c["h"] / 2 and c["y"] + c["h"] / 2 < beats["caption_y"] - 0.03 for c in cards)
    assert 0.15 <= beats["cutaway_share"] <= 0.7
    assert [x["text"] for x in beats["labels"]] == ["UberCab"]              # a named thing is labelled once
    assert F.taste_check(l1, beats)["ok"], F.taste_check(l1, beats)["problems"]


def test_pin_voices_gives_the_real_voice_its_own_picture(tmp_path):
    """The seconds a real voice speaks show its own clip from its first frame; the pictures around it are trimmed."""
    clip = tmp_path / "voice.mp4"
    clip.write_bytes(b"x")
    tl = [[0, 60, "photo", "A", {"path": "a.jpg"}], [60, 120, "clip", "B", {"path": "b.mp4", "src": [0.0, 2.0], "seconds": 4.0}],
          [120, 200, "photo", "C", {"path": "c.jpg"}]]
    v = dict(start=1.0, end=3.5, person="Fred Smith", clip=dict(path=str(clip), seconds=2.5))
    out = F.pin_voices(tl, [v])
    f0, f1 = F.Clock.f(1.0 + F.LEAD_F / F.FPS), F.Clock.f(3.5 + F.LEAD_F / F.FPS)
    pinned = [r for r in out if r[4].get("voice")]
    assert len(pinned) == 1 and pinned[0][:2] == [f0, f1] and pinned[0][4]["src"][0] == 0.0
    assert all(r[1] <= f0 or r[0] >= f1 for r in out if not r[4].get("voice"))
    after = next(r for r in out if r[0] == f1)
    assert after[3] == "B" and after[4]["src"][0] > 0.0          # the clip it cut into resumes where it would have been
    assert F.pin_voices(tl, [dict(v, clip=dict(path=str(tmp_path / "missing.mp4"), seconds=2.5))]) == tl


def test_ear_never_lets_a_rewrite_say_a_sentence_twice():
    import ear
    old = [dict(say="Sixty-three seconds into launch, the warning came."),
           dict(say="What could one thin shield take with it? Skylab launched in May.")]
    new = [dict(say="What could one thin shield take with it? Sixty-three seconds in, the warning came."),
           dict(say="What could one thin shield take with it? Skylab launched in May.")]
    assert ear.repeated(new, old, [0, 1]) == {0: "what could one thin shield take with it"}
    assert ear.repeated(old, old, [0, 1]) == {}


# ----------------------------------------------------------------- writer.annotate on a stubbed model

def _ann_script():
    return dict(title="t", named=[dict(name="Toy Story", **{"is": "thing"}), dict(name="John Lasseter", **{"is": "person"})],
                facts=[dict(id="f1", claim="c", quote="It made $362 million.", url="u")],
                segments=[dict(say="Woody almost killed Toy Story.", beat="hook", show=["nearly"]),
                          dict(say="John Lasseter was the director.", beat="low", show=[]),
                          dict(say="It made three hundred sixty two million dollars.", beat="payoff", show=["$362 million"])])


def _ann_answer(bad=False):
    segs = [dict(i=0, see=[dict(thing="Toy Story", as_of="1993 reel", must_show="the reel", moment=None)], finish="gate", finish_rank=1,
                 show=[dict(text="nearly", role="key", word_i=9 if bad else 1)], phrases=[[0, 1, 2], [3, 4]] if not bad else [[0, 2], [3, 4]]),
            dict(i=1, see=[dict(thing="John Lasseter", as_of="1993", must_show="him", moment=None)], finish="bw", finish_rank=2,
                 show=[dict(text="John Lasseter", role="name", word_i=0)], phrases=[[0, 1], [2, 3, 4]]),
            dict(i=2, see=[dict(thing="Toy Story", as_of="1995 release", must_show="the film", moment=None),
                           dict(thing="Not Named", as_of="x", must_show="x")], finish="bw", finish_rank=3,
                 show=[dict(text="$362 million", role="figure", word_i=2)], phrases=[[0, 1], [2, 3, 4, 5, 6], [7]])]
    return dict(colour_turn=2, segments=segs)


def _stub(answers):
    calls = []

    def ask(system, content):
        calls.append(content)
        return json.dumps(answers[min(len(calls) - 1, len(answers) - 1)]), dict(provider="openai", usd=0.0)
    return ask, calls


def _ok_verify(s):
    return dict(ok=True, numbers_unbacked=[], facts_unverified=[], facts=[1])


def test_annotate_fills_fields_and_keeps_say_byte_identical():
    sc = _ann_script()
    ask, calls = _stub([_ann_answer()])
    out, rep = writer.annotate(sc, ask=ask, verify_fn=_ok_verify, log=lambda m: None)
    assert len(calls) == 1 and rep["ok"], rep["problems"]
    assert [s["say"] for s in out["segments"]] == [s["say"] for s in sc["segments"]]
    assert all(s["see"] for s in out["segments"]) and out["colour_turn"] == 2
    assert out["segments"][2]["see"] == [dict(thing="Toy Story", as_of="1995 release", must_show="the film", moment=None)]   # not-named dropped
    assert out["segments"][0]["show"] == [dict(text="nearly", role="key", word_i=1)]
    assert out["segments"][1]["show"][0]["word_i"] == 0 and out["segments"][1]["show"][0]["role"] == "name"     # an added name item
    assert out["segments"][0]["phrases"] == [[0, 3], [3, 5]]
    assert out["segments"][2]["finish"] == "none"            # the payoff is always colour
    assert out["segments"][1]["finish"] == "none" and out["segments"][1]["finish_cut"] == "bw"     # 5 of 16 words: over the budget


def test_annotate_rejects_invalid_anchor_and_span_then_asks_again_or_drops():
    sc = _ann_script()
    ask, calls = _stub([_ann_answer(bad=True), _ann_answer()])
    out, rep = writer.annotate(sc, ask=ask, verify_fn=_ok_verify, log=lambda m: None)
    assert len(calls) == 2 and "problems" in calls[1]          # the repair names what was wrong
    assert out["segments"][0]["show"][0]["word_i"] == 1 and out["segments"][0]["phrases"] == [[0, 3], [3, 5]]
    ask, calls = _stub([_ann_answer(bad=True)])
    out, rep = writer.annotate(sc, ask=ask, verify_fn=_ok_verify, log=lambda m: None)
    assert len(calls) == 2 and not rep["ok"]
    assert "word_i" not in out["segments"][0]["show"][0] and "phrases" not in out["segments"][0]
    assert [s["say"] for s in out["segments"]] == [s["say"] for s in sc["segments"]]


def test_annotate_takes_out_added_items_that_do_not_verify():
    sc = _ann_script()
    a = _ann_answer()
    a["segments"][1]["show"].append(dict(text="1999", role="key", word_i=1))
    ask, _ = _stub([a])
    seen = []

    def vf(s):
        bad = any("1999" == x.get("text") for g in s["segments"] for x in g.get("show") or [])
        seen.append(bad)
        return dict(ok=not bad, numbers_unbacked=["1999"] if bad else [], facts_unverified=[], facts=[1])
    out, rep = writer.annotate(sc, ask=ask, verify_fn=vf, log=lambda m: None)
    assert seen == [True, False] and rep["verify"]["ok"]
    assert all(x["text"] != "1999" for g in out["segments"] for x in g["show"])
    assert out["segments"][2]["show"] == [dict(text="$362 million", role="figure", word_i=2)]   # given items stay


def test_device_budget_keeps_best_ranked_within_a_quarter():
    sc = dict(colour_turn=3, segments=[dict(say="a b c d", finish="bw", finish_rank=2), dict(say="a b c d", finish="gate", finish_rank=1),
                                       dict(say="a b c d", finish="crt", finish_rank=3), dict(say="a b c d", finish="bw", finish_rank=1),
                                       dict(say="a b c d", beat="payoff", finish="bw"), dict(say="a b c d", finish="none")])
    share = writer.device_budget(sc)
    assert [s["finish"] for s in sc["segments"]] == ["none", "gate", "none", "none", "none", "none"]
    assert share == round(4 / 24, 3)


def test_shown_strings_read_the_text_of_dict_items():
    sc = dict(segments=[dict(say="x", show=[dict(text="$5 million", role="figure", word_i=12)])])
    assert writer.shown_strings(sc) == ["$5 million"]
