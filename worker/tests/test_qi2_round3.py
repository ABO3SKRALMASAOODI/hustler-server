"""QI2 (round 3 integration, 30 Oct 2026): what the merge of Q5, Q6, Q7 and
Q8 adds on top of each track's own tests.

1. The B-roll prefetch (Q8) holds the curated video search Q5's first pass
   asks for (Pexels/Pixabay only), so with production's Pexels key the
   placement takes the prefetched answers instead of standing aside, and the
   misses' "any picture" pass takes the prefetched photos.
2. Owner rule D1 on the merged tree: a grandfathered payer's First cut (no
   ``first_cut_q1``) reaches none of round 3's new modules (talk_craft,
   silent_cut, montage_cut, ending_guard, the prefetch, the progress lines),
   while an engine account's does (the check is sensitive).
"""
import threading
import time
import types

import pytest

import agent_tools
import broll_source
import first_cut
import first_cut_speed as fs
import first_cut_visual as fcv
from tools_ext import broll_tools as bt

from tests.test_first_cut_visual import _talking_chain


# ------------------------------------------- 1. prefetch x curated providers

def _cand(p, n, kind):
    return {"provider": p, "id": f"{p}:{kind}:{n}", "kind": kind,
            "title": f"sun {p} {n}", "width": 2000, "height": 3000,
            "_url": "x"}


@pytest.fixture
def lanes(monkeypatch):
    """Fake libraries that record which (provider, kind) was searched."""
    seen = []
    lock = threading.Lock()

    def lane(p):
        def search(query, kind, orientation, count):
            with lock:
                seen.append((p, kind))
            return [_cand(p, 1, kind)]
        return search
    monkeypatch.setattr(broll_source, "LANES",
                        {p: lane(p) for p in broll_source.PROVIDERS})
    monkeypatch.setattr(broll_source, "status",
                        lambda: {p: "on" for p in broll_source.PROVIDERS})
    monkeypatch.setattr(bt, "_fetch",
                        lambda ctx, c, W, H: {"cand": c, "got": {"path": "/x"}})
    fs._SLOW.clear()
    fs.clear_search_cache()
    return seen


def _wait(pf, n):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not all(
            f.done() for f in list(pf._search.values())[:n]):
        time.sleep(0.02)


def test_the_prefetch_searches_the_curated_libraries_for_video(lanes):
    pf = fs.BrollPrefetch(types.SimpleNamespace(), ["the sun"],
                          ("portrait", 1080, 1920), ("video", "photo"),
                          providers={"video": ["pixabay", "pexels"]}).start()
    _wait(pf, 2)
    ctx = types.SimpleNamespace(broll_prefetch=pf, first_cut_q1=True)
    # Q5's first pass (video, curated) and the misses' pass (every library)
    assert bt._prefetch_for(ctx, "portrait", 1080, 1920,
                            ["pexels", "pixabay"])[0] is pf
    assert bt._prefetch_for(ctx, "portrait", 1080, 1920, None)[0] is pf
    # a library set the prefetch did not search: it stands aside
    assert bt._prefetch_for(ctx, "portrait", 1080, 1920, ["nasa"])[0] is None
    video = pf.search("the sun", "video", ["pexels", "pixabay"])
    assert video is not None
    assert {c["provider"] for c in video[0]} == {"pexels", "pixabay"}
    # the misses' pass asks video of every library: not prefetched, searched
    assert pf.search("the sun", "video", None) is None
    photo = pf.search("the sun", "photo", None)
    assert photo is not None and photo[0]
    pf.close()
    video_lanes = {p for p, k in lanes if k == "video"}
    assert video_lanes == {"pexels", "pixabay"}       # no archive "video"


def test_without_curated_video_the_prefetch_is_q8s(lanes):
    pf = fs.BrollPrefetch(types.SimpleNamespace(), ["the sun"],
                          ("portrait", 1080, 1920), ("photo",)).start()
    _wait(pf, 1)
    assert pf.providers == {}
    assert pf.matches("portrait", 1080, 1920, None)
    assert not pf.matches("portrait", 1080, 1920, ["pexels"])
    assert pf.search("the sun", "photo") is not None
    pf.close()


def test_start_prefetch_passes_the_curated_set_when_video_is_live(monkeypatch):
    import stock
    made = []

    class _PF:
        def __init__(self, ctx, queries, frame, kinds, providers=None):
            made.append((kinds, providers))
            self.queries, self.frame, self.kinds = queries, frame, kinds

        def start(self):
            return self
    monkeypatch.setattr(fs, "BrollPrefetch", _PF)
    monkeypatch.setattr(fs, "predicted_frame",
                        lambda ctx, v: ("portrait", 1080, 1920))
    monkeypatch.setattr(broll_source, "status", lambda: {
        "pexels": "on", "pixabay": "off: no key", "openverse": "on",
        "wikimedia": "on", "nasa": "on"})
    monkeypatch.setattr(stock, "video_available", lambda: True)
    ctx = types.SimpleNamespace()
    fs.start_broll_prefetch(ctx, [(1, 2, "sun", "the sun")], True)
    assert made[-1] == (("video", "photo"), {"video": ["pexels"]})
    monkeypatch.setattr(stock, "video_available", lambda: False)
    fs.start_broll_prefetch(types.SimpleNamespace(),
                            [(1, 2, "sun", "the sun")], True)
    assert made[-1] == (("photo",), None)


def test_keyed_video_providers_reads_the_live_curated_libraries(monkeypatch):
    monkeypatch.setattr(broll_source, "status", lambda: {
        "pexels": "off: no key", "pixabay": "on", "openverse": "on"})
    assert fcv.keyed_video_providers() == ["pixabay"]
    monkeypatch.setattr(broll_source, "status", lambda: {"openverse": "on"})
    assert fcv.keyed_video_providers() is None


# ----------------------------------------------- 2. D1 on the merged tree

_ROUND3 = [
    ("talk_craft", ("opening_row", "closing_row", "title_choice",
                    "clear_slams", "cut_audio_pauses", "cut_phrase_repeats",
                    "burned_captions", "last_sentence_start")),
    ("silent_cut", ("look", "plan_for", "apply", "camera", "title_for")),
    ("montage_cut", ("windows", "hit_end", "framable", "fit_length",
                     "follow", "end_flash")),
    ("ending_guard", ("guard",)),
    ("first_cut_speed", ("start_broll_prefetch", "progress")),
]


def _trip(monkeypatch, record):
    import importlib
    for mod, names in _ROUND3:
        m = importlib.import_module(mod)
        for name in names:
            real = getattr(m, name)

            def hit(*a, _m=mod, _n=name, _real=real, **k):
                record.append(f"{_m}.{_n}")
                return _real(*a, **k)
            monkeypatch.setattr(m, name, hit)


def test_a_grandfathered_first_cut_reaches_no_round3_module(monkeypatch):
    record = []
    _trip(monkeypatch, record)
    names = _talking_chain(monkeypatch, q1=False)
    assert record == [], record
    # C2's chain, as QI and Q1 pinned it
    assert "punch_in_on_emphasis" in names
    assert "source_broll" not in names


def test_an_engine_first_cut_reaches_them(monkeypatch):
    record = []
    _trip(monkeypatch, record)
    _talking_chain(monkeypatch, q1=True)
    assert any(r.startswith("talk_craft.") for r in record), record


def test_progress_lines_are_engine_only(monkeypatch):
    posted = []
    monkeypatch.setattr(fs, "progress",
                        lambda *a, **k: posted.append(a[3]))
    first_cut._say(types.SimpleNamespace(first_cut_q1=False), "x", "X")
    assert posted == []
    first_cut._say(types.SimpleNamespace(first_cut_q1=True), "x", "X")
    assert posted == ["x"]


def test_a_grandfathered_source_broll_searches_the_libraries_directly():
    ctx = types.SimpleNamespace(first_cut_q1=False)
    pf, fn = bt._prefetch_for(ctx, "portrait", 1080, 1920, ["pexels"])
    assert pf is None and fn is broll_source.search
