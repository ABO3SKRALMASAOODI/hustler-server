"""Lane B (12 Nov): named people, places, brands and events get real, checked pictures.

p5591 asked for "image or video of oscar piastrin in mclaren" and got six Pexels lookalikes. A model
names the subject, Commons and Openverse are searched by that name, and a vision check keeps only
pictures that show it. A picture the check did not pass is never offered; none found says so.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import broll_source  # noqa: E402
import stock  # noqa: E402
from score import supply  # noqa: E402

PIASTRI = {"name": "Oscar Piastri", "is": "person", "searches": ["Oscar Piastri McLaren", "Oscar Piastri"],
           "must_show": "Oscar Piastri himself, or his own McLaren car on track."}


def commons(n, w=6000):
    return {"provider": "wikimedia", "kind": "photo", "id": f"wikimedia:photo:{n}", "width": w, "height": 4000,
            "title": f"2025 Singapore GP - McLaren - Oscar Piastri - FP{n}", "license": "CC BY-SA 4.0", "credit": "Liauzh",
            "_thumb": f"https://upload.wikimedia.org/wikipedia/commons/thumb/c/c7/F{n}.jpg/640px-F{n}.jpg",
            "_url": f"https://upload.wikimedia.org/wikipedia/commons/c/c7/F{n}.jpg"}


def pexels(n):
    return {"provider": "pexels", "kind": "video", "id": f"pexels:video:{n}", "width": 1080, "height": 1920,
            "description": "race track corner", "_thumb": "https://images.pexels.com/x.jpg", "_files": []}


def test_subjects_are_read_from_the_models_answer_only():
    got = broll_source._subjects_of({"subjects": [{"name": " Oscar  Piastri ", "is": "person", "searches": ["Oscar Piastri McLaren", ""],
                                                   "must_show": "him or his car"}, {"name": ""}, "junk"]})
    assert got == [{"name": "Oscar Piastri", "is": "person", "searches": ["Oscar Piastri McLaren"], "must_show": "him or his car"}]
    assert broll_source._subjects_of({"subjects": []}) == []


def test_a_cut_off_check_answer_keeps_its_complete_lines():
    txt = '{"fit":[{"n":0,"ok":true,"shows":"car","grade":4},{"n":1,"ok":false,"shows":"crowd","grade":1},{"n":2,"ok":tr'
    assert [o["n"] for o in broll_source._objects(txt)] == [0, 1]


def test_commons_original_is_fetched_at_a_rendition_step_never_upscaled():
    assert broll_source._sized_url(commons(1)) == \
        "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c7/F1.jpg/1920px-F1.jpg"
    small = commons(2, w=1600)
    assert broll_source._sized_url(small) == small["_url"]


def test_only_checked_pictures_pass_and_lookalikes_face_the_same_check(monkeypatch):
    monkeypatch.setattr(broll_source, "search_named", lambda *a, **k: ([commons(1), commons(2)], []))
    seen = {}

    def check(subject, cands, workdir=None, budget_s=None):
        seen["ids"] = [c["id"] for c in cands]
        kept = [dict(cands[1], named=subject["name"], shows="McLaren 81 on track", _grade=4)]
        return kept, [dict(id=c["id"], ok=c is cands[1]) for c in cands], 0.002
    monkeypatch.setattr(broll_source, "check_named", check)
    kept, rep = broll_source.find_named(PIASTRI, "video", 3, also=[pexels(9)])
    assert seen["ids"] == ["wikimedia:photo:1", "wikimedia:photo:2", "pexels:video:9"]
    assert [k["id"] for k in kept] == ["wikimedia:photo:2"]
    assert kept[0]["description"].startswith("SHOWS Oscar Piastri")
    assert "Liauzh" in kept[0]["license_note"] and "CC BY-SA" in kept[0]["license_note"]
    assert rep["usd"] == 0.002 and "say" not in rep


def test_no_check_answer_offers_nothing_and_says_why(monkeypatch):
    monkeypatch.setattr(broll_source, "search_named", lambda *a, **k: ([commons(1)], []))

    def down(*a, **k):
        raise RuntimeError("lane closed")
    monkeypatch.setattr(broll_source, "check_named", down)
    kept, rep = broll_source.find_named(PIASTRI)
    assert kept == [] and "none is offered" in rep["say"]


def test_nothing_passing_is_said_honestly(monkeypatch):
    monkeypatch.setattr(broll_source, "search_named", lambda *a, **k: ([commons(1)], []))
    monkeypatch.setattr(broll_source, "check_named", lambda *a, **k: ([], [dict(id="x", ok=False)], 0.001))
    kept, rep = broll_source.find_named(PIASTRI)
    assert kept == [] and "Oscar Piastri" in rep["say"]


def test_stock_search_routes_a_named_query_through_the_check(monkeypatch):
    monkeypatch.setattr(stock, "available", lambda: True)
    monkeypatch.setattr(stock, "video_available", lambda: True)
    monkeypatch.setattr(stock, "PEXELS_KEY", "k")
    monkeypatch.setattr(stock, "PIXABAY_KEY", "")
    monkeypatch.setattr(stock, "_pexels_search", lambda *a: [pexels(1)])
    monkeypatch.setattr(stock, "NAMED_ROUTE", True)
    monkeypatch.setattr(broll_source, "name_subjects", lambda q: ([PIASTRI], 0.0001))
    got = {}

    def find(sub, kind, count, also=(), **k):
        got["also"] = [a["id"] for a in also]
        return [dict(commons(3), named=sub["name"], description="SHOWS Oscar Piastri: car")], {"usd": 0.001}
    monkeypatch.setattr(broll_source, "find_named", find)
    hits = stock.search("oscar piastrin in mclaren", kind="video", orientation="portrait", count=6)
    assert [h["id"] for h in hits] == ["wikimedia:photo:3"]
    assert got["also"] == ["pexels:video:1"]

    monkeypatch.setattr(broll_source, "find_named", lambda *a, **k: ([], {"say": "None of the 9 pictures found for Oscar Piastri passed."}))
    with pytest.raises(stock.NamedNotFound) as e:
        stock.search("oscar piastrin in mclaren", kind="video", orientation="portrait", count=6)
    assert "Oscar Piastri" in str(e.value)


def test_stock_search_without_a_named_subject_is_unchanged(monkeypatch):
    monkeypatch.setattr(stock, "available", lambda: True)
    monkeypatch.setattr(stock, "video_available", lambda: True)
    monkeypatch.setattr(stock, "PEXELS_KEY", "k")
    monkeypatch.setattr(stock, "PIXABAY_KEY", "")
    monkeypatch.setattr(stock, "_pexels_search", lambda *a: [pexels(1)])
    monkeypatch.setattr(broll_source, "name_subjects", lambda q: ([], 0.0001))
    monkeypatch.setattr(broll_source, "find_named", lambda *a, **k: pytest.fail("no named route for an unnamed query"))
    assert [h["id"] for h in stock.search("busy city street", kind="video", orientation="portrait")] == ["pexels:video:1"]


def test_naming_down_falls_back_to_the_libraries(monkeypatch):
    def down(q):
        raise RuntimeError("no lane")
    monkeypatch.setattr(broll_source, "name_subjects", down)
    assert stock._named_subjects("oscar piastri") == []


def test_reframe_keeps_the_output_shape_inside_the_source_and_under_the_zoom_cap():
    W, H = 1080, 1920
    c = supply.reframe([0.45, 0.6, 0.55, 0.68], W, H)
    x0, y0, x1, y1 = c
    assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1
    assert abs(((x1 - x0) * W) / ((y1 - y0) * H) - 9 / 16) < 0.01
    assert (y1 - y0) >= 1 / supply.OWN_ZOOM_MAX - 0.002
    assert x0 <= 0.45 and x1 >= 0.55 and y0 <= 0.6 and y1 >= 0.68
    land = supply.reframe([0.8, 0.1, 0.99, 0.3], 1920, 1080)
    assert land[2] <= 1.0 and abs(((land[2] - land[0]) * 1920) / ((land[3] - land[1]) * 1080) - 9 / 16) < 0.01
    assert supply.reframe("junk", W, H) is None and supply.reframe([0.5, 0.5, 0.5, 0.5], W, H) is None


def test_openverse_licence_slugs_get_their_class():
    assert supply.licence_class({"provider": "openverse", "license": "by-sa-4.0"})[:2] == ("B", "CC BY-SA 4.0")
    assert supply.licence_class({"provider": "openverse", "license": "cc0-1.0"})[0] == "A"
    assert supply.licence_class({"provider": "openverse", "license": "by-nc-2.0"})[0] is None
    assert supply.licence_class({"provider": "wikimedia", "license": "CC BY 4.0"})[0] == "B"
    assert supply.licence_class({"provider": "pixabay", "license": "Pixabay Content License"})[0] is None
