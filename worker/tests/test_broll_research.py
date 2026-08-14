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


def test_in_house_board_cast_annotates_winner_and_abstention(
        monkeypatch, tmp_path):
    def fake_download(_url, local, **_kwargs):
        with open(local, "wb") as fh:
            fh.write(b"jpeg")

    def fake_sheet(_frames, destination):
        with open(destination, "wb") as fh:
            fh.write(b"sheet")

    report = {
        "selections": [
            {"moment_id": "hook", "decision": "use",
             "candidate_id": "broll:1:hook:pexels:1", "confidence": .9,
             "visible_evidence": "specific product in a real hand",
             "sequence_reason": "grounds the opening", "concern": ""},
            {"moment_id": "proof", "decision": "none",
             "candidate_id": None, "confidence": .85,
             "visible_evidence": "generic office wallpaper",
             "sequence_reason": "speaker is stronger", "concern": ""},
        ],
        "unjudged_moments": [],
        "sequence": {"coherence": "strong", "evidence": "specific then face"},
        "duplicate_selections": [],
    }
    monkeypatch.setattr(agent_tools.net_fetch, "download", fake_download)
    monkeypatch.setattr(agent_tools.sheets, "build_timestamp_sheet", fake_sheet)
    monkeypatch.setattr(agent_tools, "_deliver_frames",
                        lambda *_args, **_kwargs: "board queued")
    monkeypatch.setattr(agent_tools.llm, "vision_available", lambda: True)
    monkeypatch.setattr(agent_tools.llm, "agent_sees", lambda _model: True)
    monkeypatch.setattr(agent_tools.broll_judge, "review",
                        lambda *_args, **_kwargs: report)
    ctx = _Ctx(tmp_path)
    ctx.direct_sight = True
    ctx.sight_out = False
    ctx.agent_model = "vision-model"
    ctx.edit_plan = {}
    hook = {
        "id": "broll:1:hook:pexels:1", "_provider_result_id": "pexels:1",
        "_thumb": "https://example.test/hook.jpg", "provider": "pexels",
        "kind": "video", "description": "product",
        "_broll_moment": {"id": "hook", "purpose": "show product"},
    }
    proof = {
        "id": "broll:2:proof:pexels:2", "_provider_result_id": "pexels:2",
        "_thumb": "https://example.test/proof.jpg", "provider": "pexels",
        "kind": "video", "description": "office",
        "_broll_moment": {"id": "proof", "purpose": "show result"},
    }

    count = agent_tools._queue_broll_research_sheet(
        ctx, [("hook", hook), ("proof", proof)])

    assert count == 2
    assert hook["_broll_cast"]["candidate_id"] == hook["id"]
    assert proof["_broll_cast_abstain"]["decision"] == "none"
    assert "KEEP BASE PICTURE / NO B-ROLL" in ctx._last_broll_board_cast
    assert ctx.editing_metrics["broll_sequence_casts"] == 1
    assert ctx.editing_metrics["broll_moments_abstained"] == 1
    traces = ctx.editing_metrics["editorial_decisions"]
    assert len(traces) == 2
    assert traces[0]["kind"] == "broll_cast"
    assert traces[0]["moment_id"] == "hook"
    assert traces[0]["candidate_ids"] == [hook["id"]]
    assert traces[0]["decision"] == "use"
    assert traces[0]["candidate_id"] == hook["id"]
    assert traces[0]["source"] == "independent_vision"
    assert traces[1]["moment_id"] == "proof"
    assert traces[1]["decision"] == "none"


def test_mcp_board_does_not_buy_duplicate_internal_cast(monkeypatch, tmp_path):
    def fake_download(_url, local, **_kwargs):
        with open(local, "wb") as fh:
            fh.write(b"jpeg")

    def fake_sheet(_frames, destination):
        with open(destination, "wb") as fh:
            fh.write(b"sheet")

    monkeypatch.setattr(agent_tools.net_fetch, "download", fake_download)
    monkeypatch.setattr(agent_tools.sheets, "build_timestamp_sheet", fake_sheet)
    monkeypatch.setattr(agent_tools, "_deliver_frames",
                        lambda *_args, **_kwargs: "board queued")
    monkeypatch.setattr(agent_tools.llm, "vision_available", lambda: True)
    monkeypatch.setattr(
        agent_tools.broll_judge, "review",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("MCP caller already judges returned pixels")))
    ctx = _Ctx(tmp_path)
    ctx.sight_out = True
    hit = {"id": "broll:1:m:pexels:1", "_thumb": "https://x.test/a.jpg",
           "_broll_moment": {"id": "m", "purpose": "proof"}}

    assert agent_tools._queue_broll_research_sheet(ctx, [("m", hit)]) == 1
    assert ctx._last_broll_board_cast is None


def test_actual_download_review_rejects_before_placement(
        monkeypatch, tmp_path):
    def fake_frame(_path, at, destination, width=None):
        with open(destination, "wb") as fh:
            fh.write(f"frame {at}".encode())

    report = {
        "decision": "reject", "confidence": .93,
        "visible_evidence": "download shows a generic office, not the product",
        "useful_part": "none", "concerns": ["subject mismatch"],
    }
    monkeypatch.setattr(agent_tools.media, "frame_at", fake_frame)
    monkeypatch.setattr(
        agent_tools.motion_judge, "analyze_video",
        lambda *_args, **_kwargs: {
            "intensity": "gentle", "analyzed_window_s": 8,
            "freeze_share": .1, "blank_share": 0, "abrupt_changes": 0})
    monkeypatch.setattr(agent_tools, "_deliver_frames",
                        lambda *_args, **_kwargs: "actual frames queued")
    monkeypatch.setattr(agent_tools.llm, "vision_available", lambda: True)
    monkeypatch.setattr(agent_tools.llm, "agent_sees", lambda _model: True)
    monkeypatch.setattr(agent_tools.broll_judge, "review_rendition",
                        lambda *_args, **_kwargs: report)
    ctx = _Ctx(tmp_path)
    ctx.direct_sight = True
    ctx.agent_model = "vision-model"

    count = agent_tools._queue_download_review(
        ctx, "downloaded.mp4", agent_tools.url_media.KIND_VIDEO,
        duration_s=8.0, label="product clip",
        review_context={"purpose": "show the product working"})

    assert count == 4
    assert ctx._last_download_rendition_judgment is report
    assert ctx.editing_metrics["broll_renditions_reviewed"] == 1
    assert ctx.editing_metrics["broll_renditions_rejected"] == 1
    assert "Do not place this rendition" in ctx._last_download_review_text


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
    assert {row["_provider_result_id"] for row in ctx.stock_results.values()} == {
        "shared", "founder using prototype-specific",
        "hands testing mobile prototype-specific",
        "small team product demo-specific",
    }
    assert all(key.startswith("broll:1:proof:")
               for key in ctx.stock_results)
    assert "VISUAL ROUTES:" in out
    assert "[route: hands testing mobile prototype]" in out


def test_same_provider_shot_keeps_distinct_story_provenance(monkeypatch):
    """A shared popular result must not let the last moment overwrite first."""
    def fake_search(query, kind, orientation, count):
        return [{"id": "pexels:video:42", "kind": kind,
                 "provider": "pexels", "description": "specific shot"}]

    monkeypatch.setattr(agent_tools.stock, "available", lambda: True)
    monkeypatch.setattr(agent_tools.stock, "search", fake_search)
    monkeypatch.setattr(agent_tools, "_queue_broll_research_sheet",
                        lambda *args, **kwargs: 0)
    ctx = _Ctx()
    out = agent_tools.research_broll(ctx, [
        {"id": "problem", "query": "frustrated customer",
         "purpose": "make the pain observable", "at": 2.0},
        {"id": "payoff", "query": "relieved customer",
         "purpose": "show the emotional release", "at": 8.0},
    ])

    assert len(ctx.stock_results) == 2
    rows = list(ctx.stock_results.values())
    assert {row["_broll_moment"]["id"] for row in rows} == {
        "problem", "payoff"}
    assert {row["_broll_moment"]["purpose"] for row in rows} == {
        "make the pain observable", "show the emotional release"}
    assert len({row["id"] for row in rows}) == 2
    assert all(row["_provider_result_id"] == "pexels:video:42"
               for row in rows)
    assert "broll:1:problem:pexels:video:42" in out
    assert "broll:2:payoff:pexels:video:42" in out


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
