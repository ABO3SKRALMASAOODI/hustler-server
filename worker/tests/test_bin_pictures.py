"""Lane B round 2 (12 Nov): the Bin offers only pictures that show the very thing said.

The independent rating found 12 of 22 films with no new pictures, and the last look removed a found
picture on 5 of 7 films as "a library picture of something loosely like it". The Bin's pictures now
face the last look's own question first; the words' own thing is searched by name; where the words
carry a number, two sides, steps or dates, a designed picture is made of them; and the upload's own
moments come from other times, re-framed, and reach the Brief.
"""
import json
import os
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import broll_source  # noqa: E402
import stock  # noqa: E402
from score import boards, brief, supply  # noqa: E402

NUMBER = {"kind": "number", "title": "", "parts": [{"value": "150 years", "label": "before she can breed"}]}


def test_a_spec_is_drawn_only_from_what_it_holds_and_a_bad_one_is_refused():
    assert boards.design_spec({"kind": "slogan", "parts": [{"value": "x"}]}) is None
    assert boards.design_spec({"kind": "versus", "parts": [{"value": "1"}]}) is None
    assert boards.design_spec({"kind": "bars", "parts": [{"label": "a"}, {"label": "b"}]}) is None   # bars need numbers
    sp = boards.design_spec(dict(NUMBER, accent="#C2080D"))
    assert sp["kind"] == "number" and sp["parts"][0]["value"] == "150 years" and sp["accent"] == "#C2080D"
    im = boards.designed(sp, 1080, 1920)
    assert im.size == (1080, 1920)
    lower = im.crop((0, int(1920 * 0.78), 1080, 1920)).convert("L")
    assert lower.getextrema()[1] < 60          # the caption's quarter stays quiet: no type there


def test_the_same_spec_draws_the_same_picture_on_any_worker(tmp_path):
    hit = broll_source.designed_hit(dict(boards.design_spec(NUMBER), what="age at first breeding"))
    assert hit["provider"] == "designed" and hit["id"].startswith("designed:") and hit["description"].startswith("DESIGNED number")
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    stock.download(dict(hit), str(a), 1080, 1920)
    stock.download(json.loads(json.dumps(hit)), str(b), 1080, 1920)     # a payload that crossed to the egress lane
    assert a.read_bytes() == b.read_bytes() and broll_source.image_kind(str(a)) == "jpeg"
    assert supply.licence_class(hit)[0] == "A"


def test_the_words_are_read_by_a_model_and_a_design_with_glyphs_the_font_lacks_is_dropped(monkeypatch):
    answer = {"moments": [{"m": 0, "thing": "fishing nets on a boat deck", "design": None},
                          {"m": 1, "thing": "", "design": dict(NUMBER, what="age")},
                          {"m": 2, "thing": "", "design": {"kind": "number", "parts": [{"value": "١٥٠", "label": "سنة"}]}}]}
    seen = {}

    def fake(model, system, content, **kw):
        seen.update(model=model, purpose=kw["purpose"], text=content[0]["text"])
        return NS(text=json.dumps(answer), usd=0.0004)
    from score import call
    monkeypatch.setattr(call, "stream", fake)
    got, usd = broll_source.read_moments(["caught in the nets", "she breeds at 150 years", "تتكاثر بعد ١٥٠ سنة"])
    assert got[0] == {"thing": "fishing nets on a boat deck", "design": None}
    assert got[1]["design"]["parts"][0]["value"] == "150 years" and got[2]["design"] is None
    assert seen["purpose"] == "designed_picture" and "moment 2:" in seen["text"] and usd == 0.0004


def test_a_designed_picture_stays_only_where_no_real_picture_did(tmp_path, monkeypatch):
    """One look decides for found and designed together; a moment that kept a real picture drops its design."""
    from PIL import Image
    os.makedirs(tmp_path / "bin", exist_ok=True)
    (tmp_path / "index.json").write_text(json.dumps({"words": [{"w": f"w{i}", "t0": i * 0.4, "t1": i * 0.4 + 0.3} for i in range(40)]}))
    scout = {"wishes": [{"w": "0-6", "want": "the nets", "q": ["fishing nets"]}, {"w": "10-16", "want": "her age", "q": ["old shark"]}]}

    def fake_search(stock_, broll, wish):
        return ([{"id": f"pexels:photo:{wish['q'][0]}", "provider": "pexels", "kind": "photo", "_thumb": "u", "width": 1080, "height": 1920}], [], 0.1)
    monkeypatch.setattr(supply, "search_wish", fake_search)
    monkeypatch.setattr(supply, "search_thing", lambda *a, **k: [])
    monkeypatch.setattr(supply, "name_wishes", lambda work, wishes: ([[] for _ in wishes], 0.0))
    monkeypatch.setattr(supply, "own_moments", lambda *a, **k: ([], 0.0))
    monkeypatch.setattr(supply, "reads_for", lambda work, wishes, per: ([{"thing": "fishing nets", "design": dict(boards.design_spec(NUMBER), what="a")},
                                                                         {"thing": "", "design": dict(boards.design_spec(NUMBER), what="b")}], 0.0))

    def stills(cands, tmp):
        for c in cands:
            c["_img"] = Image.new("RGB", (60, 100), (90, 90, 90))
        return cands
    monkeypatch.setattr(supply, "fetch_stills", stills)
    monkeypatch.setattr(supply, "look", lambda *a, **k: ([{"c": 0, "wish": 0, "what": "nets on a deck"}, {"c": 1, "wish": 1, "what": "a shark"}], 0.0))
    monkeypatch.setattr(supply, "read_pictures", lambda work, rows: None)

    def check(work, items, wishes, deadline):
        keep = [it for it in items if not (it.get("provider") != "designed" and it["wish"] == 1)]   # the stock shark loses
        return keep, 0.01, []
    monkeypatch.setattr(supply, "check_fit", check)
    out = supply.run(str(tmp_path), scout, {"accents": []}, lambda c: {"storage_key": f"k/{c['id']}", "w": 1080, "h": 1920}, 1e12)
    by = {(it["wish"], it.get("provider")) for it in out["items"]}
    assert by == {(0, "pexels"), (1, "designed")}
    d = next(it for it in out["items"] if it.get("provider") == "designed")
    assert d["design"]["kind"] == "number" and os.path.exists(tmp_path / d["path"]) and d["class"] == "A"
    assert out["usage"]["counts"]["offered"] == 4 and out["usage"]["counts"]["kept"] == 2 and out["usage"]["counts"]["designed"] == 1


def test_an_own_moment_is_another_moment_re_framed_never_the_same_instant_or_the_whole_frame(tmp_path, monkeypatch):
    (tmp_path / "index.json").write_text(json.dumps({"words": [{"w": f"w{i}", "t0": i * 0.5, "t1": i * 0.5 + 0.4} for i in range(40)]}))
    (tmp_path / "source.mp4").write_bytes(b"x")
    monkeypatch.setattr(supply, "own_sheet", lambda src, times, W, H: boards.Image.new("RGB", (100, 100)))
    wishes = [{"w": "0-3", "want": "the belt", "own": True}]          # lands at 0.0 s; frame times are 0.62 + 1.25k
    answer = {"own": [{"m": 0, "f": 0, "box": [0.3, 0.3, 0.6, 0.5], "what": "same instant"},
                      {"m": 0, "f": 8, "box": [0.0, 0.0, 1.0, 1.0], "what": "whole frame"},
                      {"m": 0, "f": 9, "box": [0.3, 0.4, 0.6, 0.55], "what": "the red light panel"}]}
    seen = {}

    def fake(model, system, content, **kw):
        seen["text"] = content[-1]["text"]
        return NS(text=json.dumps(answer), usd=0.0003)
    monkeypatch.setattr(supply.call, "stream", fake)
    rows, usd = supply.own_moments(str(tmp_path), wishes, {"dur": 20.0, "W": 1080, "H": 1920}, 1e12)
    assert [r["what"] for r in rows] == ["the red light panel"]
    assert rows[0]["crop"][3] - rows[0]["crop"][1] <= 0.87 and "at 0.0s" in seen["text"]


def test_the_brief_puts_the_uploads_own_moments_and_named_pictures_in_front_of_the_director(tmp_path):
    b = {"items": [{"id": 0, "kind": "image", "w": 1920, "h": 1280, "what": "Oscar Piastri: McLaren on track", "named": "Oscar Piastri",
                    "seen": [{"t": 0.0, "text": [], "faces": [[0.4, 0.2, 0.5, 0.3]]}]},
                   {"id": 1, "kind": "image", "w": 1080, "h": 1920, "what": "designed number: age", "design": {"kind": "number"}, "seen": []}],
         "own_lines": ['{"src":"main@1.69-4.09","crop":[0.2,0.25,0.8,0.75]}: red light panel (for w1)'], "plates": []}
    (tmp_path / "bin.json").write_text(json.dumps(b))
    lines, board = brief.bin_part(str(tmp_path))
    text = "\n".join(lines)
    assert "Shows Oscar Piastri, as the words name" in text and "stranger" not in text
    assert "Designed from the words said there" in text
    assert '{"src":"main@1.69-4.09","crop":[0.2,0.25,0.8,0.75]}: red light panel (for w1)' in text


def test_pick_times_passes_over_a_shot_with_no_allowance():
    """p5554 (45 s, no words): a shot the allowance passed over divided by zero and the Director fell."""
    import numpy as np  # noqa: F401
    per = {"dur": 45.72, "shots": [{"t": [i * 3.81, (i + 1) * 3.81]} for i in range(12)], "events": []}
    try:
        times = brief.pick_times(per, 9)
    except ZeroDivisionError:  # pragma: no cover - the defect
        raise AssertionError("pick_times divided by zero")
    assert len(times) >= 1
