import copy
import json

import pytest

from score import source_guard


def test_compiler_keeps_supplied_detail_through_guard_and_lowering(tmp_path):
    from score import compiler, lower
    from tests.test_score_taste import film

    _, index, _ = scene()
    index.update(words=[], sentences=[])
    index["video"].update(fps=30, has_audio=False)
    view = {"id": 2, "src": "main@3.00-4.50", "at": 3.5,
            "crop": [.72, .10, .878203125, .60],
            "box": [.74, .2, .86, .5], "what": "the preview"}
    (tmp_path / "bin.json").write_text(json.dumps({"own": [view], "items": []}))
    (tmp_path / "index.json").write_text(json.dumps(index))
    text = film([{"k": "b", "id": "b01", "at": "@0.0..@1.0",
                  "job": "show the preview", "pic": "view:2"}])
    result = compiler.compile_score(text, index=index, work_dir=str(tmp_path),
                                    bin_items={}, perception={})
    shot = next(i for i in result.l1["items"] if i.get("view") == "2")
    observed = next(r for r in result.meter["source_guard"] if r["id"] == shot["id"])
    assert observed["reason"] == "named_detail_crop"
    edl, _, _ = lower.lower(result.l1, str(tmp_path))
    overlay = next(o for o in edl["overlays"] if o.get("source_crop"))
    rect = overlay["source_crop"]
    assert rect[0] <= .74 < .86 <= rect[2]
    assert rect[1] <= .2 < .5 <= rect[3]
    assert rect[0] > .5  # The selected preview, not the salient desktop centre.


def scene():
    # A screen preview at the right; saliency lies on central desktop chrome.
    crop = [.72, .10, .878203125, .60]
    film = {"frame": [720, 1280], "items": [{"id": "preview", "pic": "main",
            "view": "2", "src": [3, 4], "crop": [crop, crop]}]}
    index = {"_first_film_source_guard": True,
             "video": {"width": 1920, "height": 1080, "duration": 8},
             "shots": [{"start": 0, "end": 8}],
             "spatial": {"samples": [{"t": 3.5, "focus": [.5, .5]}]}}
    views = {"2": {"t0": 3, "t1": 4.5, "box": [.74, .2, .86, .5]}}
    return film, index, views


def test_named_screen_detail_is_not_replaced_by_desktop_saliency():
    film, index, views = scene()
    crop = copy.deepcopy(film["items"][0]["crop"])
    report = source_guard.apply(film, index, {}, views=views)
    assert film["items"][0]["crop"] == crop
    assert report[0]["reason"] == "named_detail_crop"


@pytest.mark.parametrize("fault", ["unknown", "time", "cut", "missing_detail", "outside", "aspect", "face"])
def test_invalid_detail_evidence_still_uses_local_safe_framing(fault):
    film, index, views = scene()
    item = film["items"][0]
    if fault == "unknown":
        item["view"] = "absent"
    elif fault == "time":
        views["2"]["t0"] = 3.5
    elif fault == "cut":
        index["shots"] = [{"start": 0, "end": 3.2}, {"start": 3.2, "end": 8}]
    elif fault == "missing_detail":
        views["2"]["box"] = [.8, .2, .92, .5]
    elif fault == "outside":
        item["crop"][1] = [.72, .1, 1.1, .6]
    elif fault == "aspect":
        item["crop"][1] = [.72, .1, .95, .6]
    elif fault == "face":
        index["spatial"]["samples"][0]["faces"] = [[.85, .2, .95, .5]]
    report = source_guard.apply(film, index, {}, views=views)
    assert report[0]["reason"] != "named_detail_crop"
    assert all(0 <= c[0] < c[2] <= 1 and 0 <= c[1] < c[3] <= 1
               for c in item["crop"])
