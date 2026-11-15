"""A long visual-event hunt is one bounded search, not dozens of turns."""

import json
import os
import shutil
import sys

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import agent_tools  # noqa: E402


class _Ctx:
    def __init__(self, workdir, keys):
        self.workdir = str(workdir)
        self.duration = 32.0
        self.index = {
            "video": {"duration": 32.0},
            "tile_step_s": 1.0,
            "tile_keys": keys,
        }
        self.job = {}
        self.editing_metrics = {}


def test_visual_search_scans_all_tiles_in_bounded_batches_and_caches(
        monkeypatch, tmp_path):
    source = tmp_path / "tile.jpg"
    Image.new("RGB", (64, 64), "navy").save(source)
    keys = [f"tiles/1/tile_{i:03d}.jpg" for i in range(8)]
    ctx = _Ctx(tmp_path, keys)
    calls = []

    def download(_key, local):
        shutil.copyfile(source, local)

    def ask(_prompt, _paths, **kwargs):
        names = kwargs["image_names"]
        calls.append(list(names))
        first = int(os.path.basename(names[0]).split("_")[1].split(".")[0])
        # First labeled frame inside the first tile in this batch.
        return json.dumps([{
            "time": first * 4 + 0.5,
            "confidence": "high",
            "evidence": "goalkeeper visibly blocks the ball",
        }])

    monkeypatch.setattr(agent_tools.storage, "download_to", download)
    monkeypatch.setattr(agent_tools.llm, "vision_available", lambda: True)
    monkeypatch.setattr(agent_tools.llm, "ask_vision", ask)
    monkeypatch.setattr(agent_tools.llm, "get_recorder", lambda: None)

    result = agent_tools.find_visual_moments(
        ctx, "goalkeeper saves the shot", max_results=5)
    assert result.startswith("VISUAL SEARCH")
    assert "@0.50s" in result and "@16.50s" in result
    assert len(calls) == 2
    assert sum(len(batch) for batch in calls) == len(keys)
    assert ctx.editing_metrics["visual_moment_search_tiles"] == 8

    again = agent_tools.find_visual_moments(
        ctx, "goalkeeper saves the shot", max_results=5)
    assert again.startswith("UNCHANGED VISUAL SEARCH")
    assert len(calls) == 2


def test_visual_search_loads_with_story_instead_of_every_fresh_dispatch():
    assert "find_visual_moments" not in agent_tools.planning_tool_names()
    ctx = type("Ctx", (), {
        "edit_plan": None,
        "_loaded_tool_domains": {"story"},
        "_loaded_tool_names": set(),
    })()
    assert "find_visual_moments" in agent_tools.compact_tool_names(ctx)


def _tiles(monkeypatch, tmp_path, n=8):
    source = tmp_path / "tile.jpg"
    Image.new("RGB", (64, 64), "navy").save(source)
    keys = [f"tiles/1/tile_{i:03d}.jpg" for i in range(n)]
    monkeypatch.setattr(agent_tools.storage, "download_to",
                        lambda _key, local: shutil.copyfile(source, local))
    monkeypatch.setattr(agent_tools.llm, "vision_available", lambda: True)
    monkeypatch.setattr(agent_tools.llm, "get_recorder", lambda: None)
    return _Ctx(tmp_path, keys)


def test_no_model_looking_is_never_reported_as_no_match(monkeypatch, tmp_path):
    """#1278: with OpenAI out the look returned None and the customer was told
    "No coarse sampled match ... all 8 indexed tiles scanned"."""
    ctx = _tiles(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(agent_tools.llm, "ask_vision",
                        lambda *a, **kw: calls.append(1) and None)
    result = agent_tools.find_visual_moments(ctx, "a goal is scored")
    assert result.startswith("TRANSIENT_FAILURE: visual moment search could not run")
    assert "none of the 8 indexed tiles" in result
    assert "No coarse sampled match" not in result and "scanned" not in result
    import tool_outcome
    assert tool_outcome.from_legacy(result).status == "transient_failure"
    again = agent_tools.find_visual_moments(ctx, "a goal is scored")
    assert not again.startswith("UNCHANGED")          # not cached: the next call looks again
    assert len(calls) == 4


def test_an_unreadable_answer_is_not_a_look(monkeypatch, tmp_path):
    ctx = _tiles(monkeypatch, tmp_path)
    monkeypatch.setattr(agent_tools.llm, "ask_vision", lambda *a, **kw: "I cannot see it.")
    assert agent_tools.find_visual_moments(ctx, "a goal").startswith("TRANSIENT_FAILURE")


def test_a_partial_look_says_which_tiles_were_not_searched(monkeypatch, tmp_path):
    ctx = _tiles(monkeypatch, tmp_path)

    def ask(_prompt, _paths, **kwargs):
        first = kwargs["image_names"][0]
        return "[]" if first.endswith("tile_000.jpg") else None
    monkeypatch.setattr(agent_tools.llm, "ask_vision", ask)
    result = agent_tools.find_visual_moments(ctx, "a goal")
    assert result.startswith("No coarse sampled match")
    assert "4 of 8 indexed tiles scanned" in result
    assert "4 of the 8 tiles could NOT be looked at" in result
    assert "all 8" not in result
    again = agent_tools.find_visual_moments(ctx, "a goal")
    assert not again.startswith("UNCHANGED")


def test_an_answered_empty_scan_is_still_a_true_no_match(monkeypatch, tmp_path):
    ctx = _tiles(monkeypatch, tmp_path)
    monkeypatch.setattr(agent_tools.llm, "ask_vision", lambda *a, **kw: "[]")
    result = agent_tools.find_visual_moments(ctx, "a goal")
    assert result.startswith("No coarse sampled match")
    assert "all 8 indexed tiles scanned" in result and "NOT be looked" not in result


def test_a_partial_look_with_candidates_is_still_a_success(monkeypatch, tmp_path):
    ctx = _tiles(monkeypatch, tmp_path)

    def ask(_prompt, _paths, **kwargs):
        if kwargs["image_names"][0].endswith("tile_000.jpg"):
            return json.dumps([{"time": 0.5, "confidence": "high", "evidence": "goal"}])
        return None
    monkeypatch.setattr(agent_tools.llm, "ask_vision", ask)
    result = agent_tools.find_visual_moments(ctx, "a goal")
    assert result.startswith("VISUAL SEARCH 'a goal' — 1 coarse SOURCE candidate(s) from 4")
    assert "4 of the 8 tiles could NOT be looked at" in result
    import tool_outcome
    assert tool_outcome.from_legacy(result).status == "success"
