"""realvoice.splice: a voice with after = -1 opens the narration (a marketing film's built hook, 20 Nov), the cut-ins
between lines keep their places. Offline: soundfile is replaced by an in-memory table of arrays."""
import importlib.util
import json
import os
import sys
import types

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SR = 48000


def _fake_soundfile(store):
    sf = types.ModuleType("soundfile")
    sf.read = lambda path, dtype="float32", always_2d=False: (store[str(path)], SR)
    sf.write = lambda path, x, sr, subtype=None: store.__setitem__(str(path), np.asarray(x, np.float32))
    return sf


def _load():
    spec = importlib.util.spec_from_file_location("realvoice_test", os.path.join(HERE, "realvoice.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_a_voice_after_minus_one_opens_the_film(tmp_path, monkeypatch):
    store = {}
    monkeypatch.setitem(sys.modules, "soundfile", _fake_soundfile(store))
    rv = _load()
    nd, fd = tmp_path / "narr", tmp_path / "foot"
    nd.mkdir()
    (fd / "product").mkdir(parents=True)
    rng = np.random.default_rng(1)
    store[str(nd / "narration_narrator.wav")] = (0.2 * rng.standard_normal(4 * SR)).astype(np.float32)     # the narrator alone
    (nd / "narration_narrator.wav").write_bytes(b"")
    words = [dict(w="one", start=0.1, end=0.5, seg=0), dict(w="two", start=1.2, end=1.6, seg=1),
             dict(w="three", start=2.4, end=2.9, seg=2)]
    for name in ("words.json", "words_narrator.json"):
        (nd / name).write_text(json.dumps(dict(duration=4.0, words=words)))
    for name in ("hook", "after"):
        store[str(fd / "product" / f"{name}.wav")] = (0.1 * rng.standard_normal(2 * SR)).astype(np.float32)
    voices = [dict(after=-1, person="hook", wav="product/hook.wav", file="product/hook.mp4"),
              dict(after=1, person="after", wav="product/after.wav", file="product/after.mp4")]
    placed = rv.splice(str(nd), voices, str(fd))
    by = {p["person"]: p for p in placed}
    assert set(by) == {"hook", "after"}
    assert by["hook"]["start"] == 0.0 and abs(by["hook"]["end"] - 2.0) < 1e-3     # from the film's first second
    out = json.loads((nd / "words.json").read_text())
    first = out["words"][0]
    assert first["w"] == "one" and first["start"] >= by["hook"]["end"] + 0.3       # a breath, then the narrator
    assert by["after"]["start"] > first["end"]                                     # the line cut-in moved with the opening
    assert by["after"]["start"] < [w for w in out["words"] if w["w"] == "three"][0]["start"]
    assert len(store[str(nd / "narration.wav")]) > 4 * SR + 4 * SR - 10            # both voices are in the sound
