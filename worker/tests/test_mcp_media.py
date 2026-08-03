"""watch_video's judgement calls, without an ffmpeg or a bucket.

The expensive parts of mcp_media are I/O; what can be wrong in a way nobody
notices is the arithmetic that decides WHAT to encode, and the sentence that
tells the model which clock the video it just got runs on.
"""

import pytest

import config
import mcp_exec
import mcp_media


MB = 1048576


class _Db:
    def __init__(self, asset):
        self.asset = asset

    def run(self, fn, *a):
        return self.asset


class _Ctx:
    """Just enough ToolContext for the resolve/deliver decisions. Anything
    that would actually touch ffmpeg or the bucket is a test failure here —
    these paths are the ones that must never do either."""

    project_id = 3
    workdir = "/nonexistent"
    job = {"user_id": 1}

    def __init__(self, **asset):
        row = {"storage_key": "media/3/prev.mp4", "duration_s": 60.0,
               "height": 480, "fps": 30.0, "bytes": 4 * MB}
        row.update(asset)
        self.db = _Db(row)

    def latest_edl(self):
        return {"version": 7, "json": {}}


def test_a_generous_budget_keeps_the_full_ladder_height():
    h, kbps, fps, tight = mcp_media.encode_plan(
        60, 50 * MB, src_height=1080, max_height=None, src_fps=30)
    assert h == config.MCP_VIDEO_HEIGHT
    assert not tight
    assert kbps >= 900


def test_it_never_upscales():
    """A 240p asset asked for at 540p is still 240p — scaling up invents
    detail and costs bytes for it."""
    h, _, _, _ = mcp_media.encode_plan(
        60, 50 * MB, src_height=240, max_height=None, src_fps=30)
    assert h == 240


def test_max_height_is_a_cap_the_caller_owns():
    h, _, _, _ = mcp_media.encode_plan(
        60, 50 * MB, src_height=1080, max_height=360, src_fps=30)
    assert h == 360


def test_a_tight_budget_buys_a_smaller_picture_not_a_mushy_one():
    """20 minutes into 8 MB cannot be 540p. It must step DOWN the ladder
    rather than spend the whole budget on a large broken frame."""
    big, _, _, _ = mcp_media.encode_plan(
        60, 40 * MB, src_height=1080, max_height=None, src_fps=30)
    small, _, _, _ = mcp_media.encode_plan(
        1200, 8 * MB, src_height=1080, max_height=None, src_fps=30)
    assert small < big


def test_an_impossible_budget_says_so_instead_of_pretending():
    h, kbps, _, tight = mcp_media.encode_plan(
        3600, 2 * MB, src_height=1080, max_height=None, src_fps=30)
    assert tight is True
    assert h == mcp_media.LADDER[-1][0]
    assert kbps >= mcp_media._MIN_VIDEO_KBPS   # never negative, never zero


def test_frame_rate_is_capped_but_a_slow_source_is_left_alone():
    _, _, fast, _ = mcp_media.encode_plan(
        60, 50 * MB, src_height=720, max_height=None, src_fps=60)
    _, _, slow, _ = mcp_media.encode_plan(
        60, 50 * MB, src_height=720, max_height=None, src_fps=24)
    assert fast == config.MCP_VIDEO_FPS_CAP
    assert slow == 24


def test_a_missing_source_fps_does_not_produce_a_zero_rate():
    """-r 0 is not a frame rate; it is an ffmpeg failure."""
    _, _, fps, _ = mcp_media.encode_plan(
        60, 50 * MB, src_height=720, max_height=None, src_fps=None)
    assert fps > 0


# ── the clock ────────────────────────────────────────────────────────

def test_watching_the_program_warns_that_its_seconds_are_output_seconds():
    """The one mistake this whole feature makes possible: reading a timestamp
    off the assembled program and handing it to a tool that wants source
    seconds."""
    note = mcp_media._clock_note("timeline", 0)
    assert "OUTPUT second" in note
    assert "cut_output_range" in note and "project_state" in note


def test_watching_the_footage_says_the_seconds_are_the_transcripts():
    assert "source seconds" in mcp_media._clock_note("source", 0)


def test_a_window_says_what_to_add_to_what_you_read():
    note = mcp_media._clock_note("timeline", 12.5)
    assert "12.50" in note


# ── what it costs to answer ──────────────────────────────────────────

def test_the_normal_call_re_encodes_nothing():
    """The preview render and the proxy ARE the deliverable. Touching ffmpeg
    to answer "let me watch it" would burn the dispatcher on every call for a
    file that is already 480p H.264 with audio."""
    out = mcp_media.prepare(_Ctx(), {}, 12 * MB)
    assert out["video"]["transcoded"] is False
    assert out["video"]["storage_key"] == "media/3/prev.mp4"
    assert out["video"]["inline"] is True          # 4 MB fits in a 12 MB reply


def test_a_file_too_big_to_embed_still_comes_back_as_a_link():
    out = mcp_media.prepare(_Ctx(bytes=200 * MB), {}, 12 * MB)
    assert out["video"]["transcoded"] is False
    assert out["video"]["inline"] is False
    assert "delivery=\"inline\"" in out["text"]     # ...and how to change that


def test_asking_for_the_url_takes_the_file_at_any_size():
    """delivery="url" is the caller saying a link is what it wants. The
    default's no-huge-links ceiling is not a rule to enforce against someone
    who asked."""
    huge = int(config.MCP_VIDEO_URL_MAX_MB * MB * 4)
    out = mcp_media.prepare(_Ctx(bytes=huge), {"delivery": "url"}, 12 * MB)
    assert out["video"]["transcoded"] is False
    assert out["video"]["inline"] is False


class _Reached(Exception):
    pass


def test_a_non_mp4_asset_is_never_handed_over_untouched(monkeypatch):
    """A .mov of HEVC off a phone plays in the studio and in nothing else.
    Handing a model that link and calling it "the video" gives it a file it
    cannot decode, so this is the one case that must always re-encode."""
    class _S:
        @staticmethod
        def exists(key):
            raise _Reached(key)

    monkeypatch.setattr(mcp_media, "storage", _S)
    with pytest.raises(_Reached) as e:
        mcp_media.prepare(_Ctx(storage_key="clips/3/x.mov"),
                          {"delivery": "url"}, 12 * MB)
    assert str(e.value).endswith(".mp4")     # an mp4 copy, not the .mov


# ── the surface ──────────────────────────────────────────────────────

def test_the_media_control_call_is_not_an_editor_tool():
    """__media__ is plumbing the backend calls on watch_video's behalf. If it
    ever appeared in the catalog the model would see two ways to do this, one
    of them undocumented."""
    published = {t["function"]["name"] for t in mcp_exec.catalog()["tools"]}
    assert mcp_exec.MEDIA_TOOL not in published
