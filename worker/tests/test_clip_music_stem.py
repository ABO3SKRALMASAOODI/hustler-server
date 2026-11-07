"""Ticket #386: the music of a linked reference clip, without its speaker.

Project 5329, 3 Nov 2026: the customer linked a reference video and asked for
its background music under their own video. add_music on the clip takes the
whole soundtrack, voice included, and separate_music works only on the main
video, so the editor told the customer the music could not be taken.
extract_audio(part='music') separates the clip's sound and keeps one side.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import pytest                                                   # noqa: E402

import agent_tools                                              # noqa: E402
import db as dbx                                                # noqa: E402
import music_gate                                               # noqa: E402

CLIP = {"id": 7, "kind": "video_clip", "storage_key": "fetched/5/b.mp4",
        "sha256": "abc", "duration_s": 266.0, "meta": {"filename": "b.mp4"}}
FULL = {"id": 8, "kind": "music", "storage_key": "music/5/full.m4a",
        "duration_s": 266.0, "meta": {"filename": "b (audio).m4a"}}


class _Db:
    def __init__(self, cached=None):
        self.cached = cached
        self.inserted = []
        self.lookups = []

    def run(self, fn, *args, **kw):
        if fn is dbx.asset_by_key:
            return CLIP
        if fn is dbx.extracted_audio_asset:
            self.lookups.append(args)
            return self.cached
        if fn is dbx.insert_asset:
            self.inserted.append((args, kw))
            return 41
        raise AssertionError(getattr(fn, "__name__", fn))


def _ctx(db):
    ctx = agent_tools.ToolContext.__new__(agent_tools.ToolContext)
    ctx.db, ctx.project_id = db, 5
    ctx.project, ctx.job = {"user_id": 3}, {"id": 1, "user_id": 3}
    ctx.index, ctx.audio_extracted = {}, []
    return ctx


@pytest.fixture
def stems(monkeypatch):
    calls = []
    monkeypatch.setattr(agent_tools, "_stems_supported", lambda: True)
    monkeypatch.setattr(agent_tools, "_audio_from_clip",
                        lambda ctx, asset: (FULL, "", None))
    monkeypatch.setattr(agent_tools.storage, "exists", lambda key: False)

    def run(project_id, payload, user_id=None):
        calls.append(payload)
        return {"ok": True}
    monkeypatch.setattr(agent_tools.remote, "run_stems_remote", run)
    return calls


def test_part_music_keeps_the_clips_music_without_its_speaker(stems):
    db = _Db()
    out = agent_tools.extract_audio(_ctx(db), CLIP["storage_key"], part="music")
    assert "storage_key=stems/abc/accomp.m4a" in out
    assert stems == [{"src_key": FULL["storage_key"],
                      "vocals_key": "stems/abc/vocals.m4a",
                      "accomp_key": "stems/abc/accomp.m4a"}]
    (args, kw), = db.inserted
    assert args[1:] == ("music", "stems/abc/accomp.m4a")
    meta = kw["meta"]
    assert meta["stem"] == "music" and meta["extracted_from_video"]
    # The music gate traces it back to the clip the customer linked.
    assert music_gate.extracted_from({"meta": meta}) == CLIP["storage_key"]


def test_part_voice_keeps_the_speech(stems):
    db = _Db()
    out = agent_tools.extract_audio(_ctx(db), CLIP["storage_key"], part="voice")
    assert "storage_key=stems/abc/vocals.m4a" in out


def test_a_separated_side_is_reused_not_separated_again(stems):
    cached = {"id": 9, "kind": "music", "storage_key": "stems/abc/accomp.m4a",
              "duration_s": 266.0, "meta": {"stem": "music"}}
    db = _Db(cached=cached)
    out = agent_tools.extract_audio(_ctx(db), CLIP["storage_key"], part="music")
    assert "stems/abc/accomp.m4a" in out and stems == [] and not db.inserted
    assert db.lookups[0][-1] == "music"


def test_no_separation_on_this_build_is_said_plainly(monkeypatch):
    monkeypatch.setattr(agent_tools, "_stems_supported", lambda: False)
    out = agent_tools.extract_audio(_ctx(_Db()), CLIP["storage_key"],
                                    part="music")
    assert "isn't available" in out and "part='all'" in out


def test_the_whole_soundtrack_cache_never_returns_a_separated_side():
    """extracted_audio_asset's default is the whole soundtrack: a clip whose
    music was separated must not hand add_music the music-only file when the
    whole sound was asked for (and the reverse)."""
    import inspect
    src = inspect.getsource(dbx.extracted_audio_asset)
    assert "coalesce(meta->>'stem', '') = %s" in src
    assert inspect.signature(dbx.extracted_audio_asset).parameters[
        "stem"].default == ""


def test_the_tool_schema_offers_the_part():
    _fn, _desc, params = agent_tools.TOOLS["extract_audio"]
    assert params["part"]["enum"] == ["all", "music", "voice"]
