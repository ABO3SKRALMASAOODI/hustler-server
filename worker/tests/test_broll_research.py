"""B-roll is selected as a story-wide visual system, not first-hit filler."""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import agent_tools  # noqa: E402


class _Ctx:
    sight_out = False
    direct_sight = False

    def __init__(self, workdir="/tmp"):
        self.workdir = str(workdir)
        self.stock_results = {}
        self.pending_images = []

    @staticmethod
    def latest_edl():
        return {"json": {"frame": {"ratio": "9:16"}}}


def test_research_broll_covers_long_story_and_adapts_search_depth(monkeypatch):
    calls = []

    def fake_search(query, kind, orientation, count):
        calls.append((query, kind, orientation, count))
        return [
            {"id": f"{query}:{i}", "kind": kind, "provider": "test",
             "description": f"real shot for {query}", "width": 1080,
             "height": 1920, "duration_s": 7}
            for i in range(count)
        ]

    monkeypatch.setattr(agent_tools.stock, "available", lambda: True)
    monkeypatch.setattr(agent_tools.stock, "search", fake_search)
    monkeypatch.setattr(agent_tools, "_queue_broll_research_sheet",
                        lambda *args, **kwargs: 0)
    moments = [
        {"id": f"m{i}", "query": f"idea{i}",
         "purpose": f"prove beat {i}", "at": i * 2.0,
         "duration_s": 1.5}
        for i in range(1, 11)
    ]
    ctx = _Ctx()
    out = agent_tools.research_broll(ctx, moments)

    assert len(calls) == 10
    assert {call[2] for call in calls} == {"portrait"}
    assert {call[3] for call in calls} == {2}
    assert len(ctx.stock_results) == 20
    assert "m1 at output 2s" in out
    assert "m10 at output 20s" in out
    assert "Compare the sequence globally" in out


def test_visual_board_spreads_first_look_across_moments(monkeypatch, tmp_path):
    delivered = []

    def fake_download(url, local, **_kwargs):
        delivered.append(url)
        with open(local, "wb") as fh:
            fh.write(b"jpeg")

    built = {}

    def fake_sheet(frames, destination):
        built["labels"] = [label for label, _path in frames]
        with open(destination, "wb") as fh:
            fh.write(b"sheet")

    monkeypatch.setattr(agent_tools.net_fetch, "download", fake_download)
    monkeypatch.setattr(agent_tools.sheets, "build_timestamp_sheet", fake_sheet)
    ctx = _Ctx(tmp_path)
    ctx.sight_out = True
    rows = []
    for moment in ("hook", "proof", "payoff"):
        for candidate in (1, 2):
            rows.append((moment, {
                "id": f"{moment}-{candidate}",
                "_thumb": f"https://example.test/{moment}-{candidate}.jpg"}))

    count = agent_tools._queue_broll_research_sheet(ctx, rows, limit=3)

    assert count == 3
    assert built["labels"] == [
        "hook | hook-1", "proof | proof-1", "payoff | payoff-1"]
    assert len(ctx.pending_images) == 1
    assert "whole edit" not in ctx.pending_images[0][0].lower()
    assert "sequence diversity" in ctx.pending_images[0][0]


def test_query_variants_compare_semantic_routes_without_growing_board(
        monkeypatch):
    calls = []

    def fake_search(query, kind, orientation, count):
        calls.append(query)
        # The same popular result appears for every wording; the route-aware
        # merge must dedupe it and preserve different remaining treatments.
        return [
            {"id": "shared", "kind": kind, "provider": "test",
             "description": "generic shared result"},
            {"id": f"{query}-specific", "kind": kind, "provider": "test",
             "description": f"specific treatment for {query}"},
        ]

    monkeypatch.setattr(agent_tools.stock, "available", lambda: True)
    monkeypatch.setattr(agent_tools.stock, "search", fake_search)
    monkeypatch.setattr(agent_tools, "_queue_broll_research_sheet",
                        lambda *args, **kwargs: 0)
    ctx = _Ctx()
    out = agent_tools.research_broll(ctx, [{
        "id": "proof", "query": "founder using prototype",
        "query_variants": ["hands testing mobile prototype",
                           "small team product demo"],
        "purpose": "make the claim observable", "at": 4.2,
    }])

    assert set(calls) == {"founder using prototype",
                          "hands testing mobile prototype",
                          "small team product demo"}
    # One moment still returns the existing four-alternative evidence budget;
    # variants widen meaning instead of inflating the context.
    assert len(ctx.stock_results) == 4
    assert set(ctx.stock_results) == {
        "shared", "founder using prototype-specific",
        "hands testing mobile prototype-specific",
        "small team product demo-specific",
    }
    assert "VISUAL ROUTES:" in out
    assert "[route: hands testing mobile prototype]" in out


def test_long_story_preserves_beat_coverage_before_deep_query_variants(
        monkeypatch):
    calls = []

    def fake_search(query, kind, orientation, count):
        calls.append(query)
        return [{"id": query, "kind": kind, "provider": "test",
                 "description": query}]

    monkeypatch.setattr(agent_tools.stock, "available", lambda: True)
    monkeypatch.setattr(agent_tools.stock, "search", fake_search)
    monkeypatch.setattr(agent_tools, "_queue_broll_research_sheet",
                        lambda *args, **kwargs: 0)
    moments = [{"id": f"m{i}", "query": f"primary {i}",
                "query_variants": [f"detail {i}"], "purpose": "proof"}
               for i in range(10)]
    ctx = _Ctx()
    agent_tools.research_broll(ctx, moments)

    assert set(calls) == {f"primary {i}" for i in range(10)}
    assert len(ctx.stock_results) == 10


def test_broll_research_is_exposed_and_honestly_disabled(monkeypatch):
    fn, desc, props = agent_tools.TOOLS["research_broll"]
    assert fn is agent_tools.research_broll
    assert "COHERENT STORY SEQUENCE" in desc
    assert props["moments"]["items"]["required"] == ["query", "purpose"]
    assert "query_variants" in props["moments"]["items"]["properties"]
    assert agent_tools.REQUIRED_ARGS["research_broll"] == ["moments"]
    monkeypatch.setattr(agent_tools.stock, "available", lambda: False)
    assert agent_tools._tool_disabled("research_broll")
