import copy
import json

import numpy as np
import pytest
import source_screen
from score import preparation, perceive, picture_cuts


def sample():
    return dict(video=dict(duration=10., fps=30., width=720, height=1280, has_audio=True),
                shots=[dict(id=1, start=0., end=10.)], spatial=dict(samples=[]),
                source_screen=dict(status="verified", version=source_screen.VERSION,
                                   safe_end_s=9., source_sha256="abc"))


def prepared(monkeypatch, tmp_path):
    src = tmp_path / "proxy.mp4"
    src.write_bytes(b"same proxy bytes")
    index = sample()
    monkeypatch.setattr(preparation, "_code_key", lambda: "same-code")
    monkeypatch.setattr(picture_cuts, "refine", lambda i, p: dict(i,
        shots=[dict(id=1, start=0., end=4.), dict(id=2, start=4., end=9.)],
        director_cut_measure=dict(cuts=[4.])))
    colour = np.arange(72, dtype=np.uint8).reshape(2, 3, 4, 3)
    per = dict(samples=[dict(t=5., person=[.2, .1, .8, .9])], dur=9.)
    def measure(i, p, work):
        assert i["video"]["duration"] == 9.
        np.save(str(work) + "/colour.npy", colour)
        return per
    monkeypatch.setattr(perceive, "run", measure)
    cache = preparation.build(index, str(src), str(tmp_path / "index"))
    index["director_preparation"] = cache
    return index, src, colour, per


def test_restores_exact_measurements_with_current_transcript(monkeypatch, tmp_path):
    index, src, colour, per = prepared(monkeypatch, tmp_path)
    index["words"] = [dict(w="new", t0=1., t1=2.)]
    bounded = source_screen.first_film_index(index)
    before = copy.deepcopy(bounded)
    work = tmp_path / "film"
    restored, result = preparation.restore(bounded, str(src), str(work))
    assert bounded == before
    assert result == per
    assert restored["words"] == index["words"]
    assert restored["shots"][-1]["end"] == 9.
    assert np.array_equal(np.load(work / "colour.npy"), colour)
    assert json.loads((work / "perception.json").read_text()) == per


@pytest.mark.parametrize("change", ["spatial", "clearance", "source", "code", "unbounded", "broken"])
def test_stale_or_unverified_measurements_are_never_used(monkeypatch, tmp_path, change):
    index, src, _, _ = prepared(monkeypatch, tmp_path)
    bounded = source_screen.first_film_index(index)
    if change == "spatial": bounded["spatial"]["samples"] = [dict(t=5., faces=[[.1, .1, .5, .5]])]
    if change == "clearance": bounded["source_screen"]["safe_end_s"] = 8.
    if change == "source": src.write_bytes(b"different source")
    if change == "code": monkeypatch.setattr(preparation, "_code_key", lambda: "new-code")
    if change == "unbounded": bounded.pop("_first_film_source_guard")
    if change == "broken": bounded["director_preparation"]["colour"] = "broken"
    assert preparation.restore(bounded, str(src), str(tmp_path / "film")) is None
