"""The concierge never offers a generation tool the editor no longer has.

Ticket #123 (2 Nov 2026): a new Pro customer (user 2742, project 5345) put
five product photos on a blank canvas and asked "can Valmera generate
genuinely moving video clips from these five photos?". The concierge's FACTS
said "RIGHT NOW, with no video, you can: generate short video clips from a
description, or animate a still image into a moving clip", because FAL_KEY is
set on Render, so it answered "Yes—I can generate genuinely moving clips" and
started a turn. The editor has no generate_video (worker/agent_tools.py pops
it, with generate_image, from the live catalog), so the turn ended blocked
and had to retract the promise. The backend gates now mirror that list.

    cd backend && python -m pytest tests/test_concierge_retired_generation.py -q
"""

import ast
import os
import sys
import types

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes import video as v                                # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _worker_retired_tools():
    """The names in agent_tools.py's `for _retired_tool in (...)` loop."""
    src = open(os.path.join(REPO, "worker", "agent_tools.py")).read()
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.For) and isinstance(node.target, ast.Name)
                and node.target.id == "_retired_tool"):
            return {e.value for e in node.iter.elts
                    if isinstance(e, ast.Constant)}
    raise AssertionError("retired-tool loop not found in worker/agent_tools.py")


def test_backend_mirror_matches_the_worker_retired_generation_tools():
    retired = _worker_retired_tools()
    for tool in ("generate_video", "generate_image"):
        assert (tool in retired) == (tool in v._RETIRED_GENERATION_TOOLS), tool


def _system_prompt(monkeypatch, stage="no_video"):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("FAL_KEY", "fal-test")
    monkeypatch.setenv("VIDEO_PROVIDER", "fal")
    monkeypatch.setenv("IMAGE_API_KEY", "img-test")
    seen = []

    def create(**kw):
        seen.append(kw["messages"][0]["content"])
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(
                content='{"act": false, "reply": "No."}'))],
            usage=types.SimpleNamespace(prompt_tokens=1, completion_tokens=1))

    monkeypatch.setattr(v, "_concierge_client", types.SimpleNamespace(
        chat=types.SimpleNamespace(
            completions=types.SimpleNamespace(create=create))))
    v._concierge_reply(stage, [{"role": "user", "content":
                                "Can Valmera generate genuinely moving video "
                                "clips from these five photos?"}], [],
                       can_act=stage == "no_video")
    return seen[0]


def test_configured_video_matches_restored_worker_tool(monkeypatch):
    """Configured reference animation is live; retired image generation is not."""
    monkeypatch.delenv('FAL_KEY', raising=False)
    assert not v._video_gen_enabled()
    assert not v._image_gen_enabled()
    system = _system_prompt(monkeypatch)
    assert v._video_gen_enabled()
    assert "animate a still image into a moving clip" in system
    assert "generate images from a text description" not in system
    assert "canNOT generate moving VIDEO footage" not in system


def test_ready_project_list_matches_available_video(monkeypatch):
    system = _system_prompt(monkeypatch, stage="ready")
    assert "generate short AI video clips" in system
    assert "canNOT: generate footage or images from nothing" not in system
