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
        words = [i for i in l1["items"] if i["k"] == "word" and isinstance(i["op"], list)]
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
