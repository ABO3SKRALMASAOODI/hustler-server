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
