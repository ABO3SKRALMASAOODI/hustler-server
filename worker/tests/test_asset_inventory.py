"""Used vs unused project files stay visible to the agent."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_prompt                                           # noqa: E402
import agent_tools                                            # noqa: E402
from schemas import default_edl                               # noqa: E402


def test_edl_used_keys_cover_inserts_overlays_and_audio():
    edl = default_edl(20.0)
    edl["inserts"] = [{"asset_key": "clips/1/a.mp4"}]
    edl["overlays"] = [{"asset_key": "clips/1/b.mp4"}]
    edl["music"] = [{"storage_key": "music/1/t.mp3"}]
    edl["voiceover"] = [{"asset_key": "music/1/vo.m4a"}]
    edl["sfx"] = [{"storage_key": "sfx/1/hit.wav"}]
    keys = agent_tools.edl_used_asset_keys(edl)
    assert keys == {
        "clips/1/a.mp4", "clips/1/b.mp4", "music/1/t.mp3",
        "music/1/vo.m4a", "sfx/1/hit.wav",
    }


class _DB:
    def __init__(self, rows):
        self.rows = rows

    def run(self, fn, *a, **k):
        if getattr(fn, "__name__", "") == "assets_by_kinds":
            kinds = a[1]
            return [r for r in self.rows if r["kind"] in kinds]
        raise AssertionError(getattr(fn, "__name__", fn))


class _Ctx:
    def __init__(self, rows, edl):
        self.project_id = 1
        self.db = _DB(rows)
        self._edl = edl
        self.workdir = tempfile.mkdtemp()

    def latest_edl(self):
        return {"version": 2, "json": self._edl}


def test_list_assets_marks_unused_files_available():
    edl = default_edl(20.0)
    edl["inserts"] = [{"asset_key": "clips/1/used.mp4"}]
    rows = [
        {"kind": "video_clip", "storage_key": "clips/1/used.mp4",
         "duration_s": 3.0, "meta": {"filename": "used.mp4"}},
        {"kind": "video_clip", "storage_key": "clips/1/spare.mp4",
         "duration_s": 4.0, "meta": {"filename": "spare.mp4"}},
    ]
    out = agent_tools.list_assets(_Ctx(rows, edl), "clip")
    assert "AVAILABLE, not in the current edit" in out
    assert "spare.mp4" in out
    assert "in the current edit" in out
    assert "do not tell the user they have no footage" in out


def test_project_state_says_unused_files_are_already_here():
    block = agent_prompt.project_state_block(
        "video", "index", "v1", [], [],
        media_lines=['  clip "x" — AVAILABLE'])
    assert "ON the timeline AND sitting unused" in block
    assert "do not ask the user to re-upload" in block
