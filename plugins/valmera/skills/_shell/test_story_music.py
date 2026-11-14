import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import chain


def test_one_command_story_retains_checkpoint_when_required_music_is_missing(tmp_path, monkeypatch):
    (tmp_path / "final.mp4").write_bytes(b"clean export")
    (tmp_path / "music.json").write_text(json.dumps({"ok": False, "why": "library unavailable", "tried": []}))
    ctx = SimpleNamespace(run_dir=str(tmp_path), supply=str(tmp_path), tmp=str(tmp_path),
                          workflow="wf3", args={"one_command": True})
    monkeypatch.setattr(chain.brand, "check", lambda *args: {"ok": True})
    monkeypatch.setattr(chain, "music_version", lambda *args: None)
    monkeypatch.setattr(chain, "wants_music", lambda *args: True)
    with pytest.raises(RuntimeError, match="required story music.*library unavailable"):
        chain.finish_and_check(ctx, {"film": "engine.mp4"}, {})
    assert (tmp_path / "final.mp4").exists()


def test_successful_music_delivery_is_returned(tmp_path, monkeypatch):
    (tmp_path / "final.mp4").write_bytes(b"clean export")
    ctx = SimpleNamespace(run_dir=str(tmp_path), supply=str(tmp_path), tmp=str(tmp_path),
                          workflow="wf3", args={"one_command": True})
    monkeypatch.setattr(chain.brand, "check", lambda *args: {"ok": True})
    monkeypatch.setattr(chain, "music_version", lambda *args: {"final_music": "music.mp4"})
    assert chain.finish_and_check(ctx, {"film": "engine.mp4"}, {})["final_music"] == "music.mp4"
