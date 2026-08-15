"""watch_video's judgement calls, without an ffmpeg or a bucket.

The expensive parts of mcp_media are I/O; what can be wrong in a way nobody
notices is the arithmetic that decides WHAT to encode, and the sentence that
tells the model which clock the video it just got runs on.
"""

import os

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
        self.pending_images = []
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


def test_render_true_joins_an_identical_inflight_preview(monkeypatch):
    """Studio self-heal and MCP watch can ask for the same immutable EDL
    together. They must pay for one full encode and both wait on its row."""
    calls = []
    rendered = {
        "storage_key": "media/3/joined.mp4", "duration_s": 60.0,
        "height": 480, "fps": 30.0, "bytes": 4 * MB,
        "meta": {"edl_version": 7},
    }

    class JoinDb:
        def __init__(self):
            self.assets = 0

        def run(self, fn, *args):
            calls.append((fn, args))
            if fn is mcp_media.dbx.find_render_asset:
                self.assets += 1
                return None if self.assets == 1 else rendered
            if fn is mcp_media.dbx.latest_render:
                return None
            if fn is mcp_media.dbx.get_or_enqueue_preview_job:
                return 77, False
            if fn is mcp_media.dbx.get_job:
                return {"id": 77, "state": "done"}
            raise AssertionError(f"unexpected DB call {fn}")

    ctx = _Ctx()
    ctx.db = JoinDb()
    monkeypatch.setattr(mcp_media.time, "sleep", lambda _seconds: None)

    asset, version, note = mcp_media._preview_for_watching(ctx, render=True)

    assert (asset, version) == (rendered, 7)
    assert "rendered just now" in note
    assert any(fn is mcp_media.dbx.get_or_enqueue_preview_job
               for fn, _args in calls)
    assert not any(fn is mcp_media.dbx.enqueue_job for fn, _args in calls)


def test_the_default_never_embeds_however_small_the_file():
    """THE BUG THIS EXISTS FOR (Aug 3 2026). Embedding whenever the file fit
    assumed a client that cannot render a video block would ignore it. Grok
    STRINGIFIED it — a 2.9 MB preview became 4 million characters of base64
    and ended the session, and the tool call reported success. Being wrong
    this way costs the whole conversation; being wrong the other way costs
    one extra argument."""
    for size in (1, 4 * MB, 11 * MB):
        out = mcp_media.prepare(_Ctx(bytes=size), {}, 12 * MB)
        assert out["video"]["inline"] is False, f"{size} bytes was embedded"
    assert "LINK BELOW IS THE VIDEO" in out["text"]


def test_embedding_is_opt_in_and_says_what_it_costs():
    out = mcp_media.prepare(_Ctx(), {"delivery": "inline"}, 12 * MB)
    assert out["video"]["inline"] is True
    # ...and the caller that did NOT opt in is told the option exists AND
    # what it does to a client that cannot decode it.
    plain = mcp_media.prepare(_Ctx(), {}, 12 * MB)["text"]
    assert "delivery=\"inline\"" in plain and "run out of context" in plain


def test_inline_on_an_oversized_file_shrinks_it_rather_than_giving_up(
        monkeypatch):
    """delivery="inline" is a request for something embeddable. A 200 MB file
    must reach the encoder — handing it back un-embedded would answer a
    different question than the one asked."""
    class _S:
        @staticmethod
        def exists(key):
            raise _Reached(key)

    monkeypatch.setattr(mcp_media, "storage", _S)
    with pytest.raises(_Reached):
        mcp_media.prepare(_Ctx(bytes=200 * MB), {"delivery": "inline"},
                          12 * MB)


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


def test_heavy_encode_is_offloaded_only_after_source_resolution(monkeypatch):
    """A timeline preview wait belongs on the warm dispatcher; Modal receives
    the exact resolved object and therefore starts at the encode boundary."""
    import remote
    sent = []
    monkeypatch.setattr(remote, "mcp_media_available", lambda: True)
    monkeypatch.setattr(
        remote, "run_mcp_media_remote",
        lambda project_id, payload, user_id=None: sent.append(
            (project_id, payload, user_id)) or {"text": "remote"})

    out = mcp_media.prepare(
        _Ctx(storage_key="clips/3/source.mov"),
        {"delivery": "url", "start": 1.0, "end": 3.0}, 12 * MB)

    assert out == {"text": "remote"}
    project_id, payload, user_id = sent[0]
    args = payload["args"]
    assert (project_id, user_id, payload["tool"]) == (3, 1, "__media__")
    assert args["_resolved_asset"]["storage_key"] == "clips/3/source.mov"
    assert "preview render of EDL v7" in args["_resolved_what"]


def test_resolved_modal_encode_never_recurses_to_remote(monkeypatch):
    import remote
    monkeypatch.setenv("EXECUTOR_PROVIDER", "modal")
    monkeypatch.setattr(
        remote, "run_mcp_media_remote",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("resolved work must encode here")))

    class _S:
        @staticmethod
        def exists(key):
            raise _Reached(key)

    monkeypatch.setattr(mcp_media, "storage", _S)
    with pytest.raises(_Reached):
        mcp_media.prepare(
            _Ctx(),
            {"delivery": "url", "start": 1.0, "end": 3.0,
             "_resolved_asset": {
                 "storage_key": "clips/3/source.mov", "bytes": 4 * MB,
                 "duration_s": 60.0, "height": 480, "fps": 30.0},
             "_resolved_what": "the resolved clip"},
            12 * MB)


def test_caller_cannot_forge_resolved_storage_key(monkeypatch):
    monkeypatch.delenv("EXECUTOR_PROVIDER", raising=False)
    out = mcp_media.prepare(
        _Ctx(),
        {"_resolved_asset": {
            "storage_key": "clips/another-user/private.mp4", "bytes": 1,
            "duration_s": 1.0, "height": 480, "fps": 30.0}},
        12 * MB)
    assert out["video"]["storage_key"] == "media/3/prev.mp4"


# ── the pictures ─────────────────────────────────────────────────────
#
# THE POINT OF THE WHOLE FEATURE (Aug 4 2026). Handing over a link made the
# model do the work itself: it downloaded the MP4, shelled out to ffmpeg,
# extracted 29 frames and built a spectrogram, to answer "what is in this".
# A tool that returns homework has not answered the question. The frames come
# back IN THE REPLY now, as pictures the model already has.

VIDEO = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "test_video.mp4")


@pytest.mark.skipif(not os.path.exists(VIDEO), reason="no fixture video")
def test_the_filmstrip_is_one_sheet_labelled_in_TIMELINE_seconds(tmp_path):
    """A tile says 14.50s and the model passes 14.5 to a tool. If the labels
    were offsets into a WINDOW instead of seconds of the timeline, every
    moment read off a windowed watch would land somewhere else."""
    ctx = _Ctx()
    ctx.workdir = str(tmp_path)
    n = mcp_media._filmstrip(ctx, VIDEO, duration=12.0, start=10.0, count=6)
    assert n == 6
    assert len(ctx.pending_images) == 1          # ONE picture, not six
    label, path = ctx.pending_images[0]
    assert os.path.getsize(path) > 0
    assert "10.00-22.00s" in label               # the window it covers
    # ...and the tiles inside it are stamped 10-22, not 0-12.
    assert mcp_media._sig(VIDEO, 10.0)


@pytest.mark.skipif(not os.path.exists(VIDEO), reason="no fixture video")
def test_a_filmstrip_is_capped_and_never_degenerate(tmp_path):
    ctx = _Ctx()
    ctx.workdir = str(tmp_path)
    assert mcp_media._filmstrip(ctx, VIDEO, 12.0, 0.0, count=999) == 20
    ctx.pending_images = []
    assert mcp_media._filmstrip(ctx, VIDEO, 12.0, 0.0, count=0) == 2


# ── the sound ────────────────────────────────────────────────────────

def test_a_short_program_gets_sound_at_full_quality():
    kbps, _ = mcp_media.audio_plan(28.7)
    assert kbps == config.MCP_AUDIO_MAX_KBPS
    # ...and the payload stays nowhere near what killed the video blob.
    assert 28.7 * kbps / 8 * 1024 < 300 * 1024


def test_a_long_window_is_refused_rather_than_made_unlistenable():
    """Silently dropping to 6 kbps, or truncating to the first 80 seconds and
    saying nothing, are both worse than "ask for a narrower window"."""
    kbps, max_s = mcp_media.audio_plan(600)
    assert kbps == 0
    assert 60 < max_s < 200            # a real number to put in the sentence


def test_the_bitrate_falls_as_the_window_grows_but_never_below_the_floor():
    long_kbps, _ = mcp_media.audio_plan(60)
    short_kbps, _ = mcp_media.audio_plan(5)
    assert short_kbps >= long_kbps
    assert long_kbps == 0 or long_kbps >= config.MCP_AUDIO_MIN_KBPS


@pytest.mark.skipif(not os.path.exists(VIDEO), reason="no fixture video")
def test_the_sound_is_the_WINDOW_not_the_whole_programme(tmp_path,
                                                         monkeypatch):
    """A window's audio starting at 0 while its frames start at 10s would put
    the model's ears and eyes in different places — the worst possible way to
    be wrong, because both look right on their own."""
    import media as _media
    made = {}

    class _S:
        @staticmethod
        def exists(key):
            return False

        @staticmethod
        def upload_file(path, key, ct):
            made["path"], made["ct"] = path, ct

    monkeypatch.setattr(mcp_media, "storage", _S)
    ctx = _Ctx()
    ctx.workdir = str(tmp_path)
    audio, note = mcp_media._audio_clip(ctx, VIDEO, duration=6.0, start=12.0)
    assert note == "" and audio and audio["mime"] == "audio/mpeg"
    assert made["ct"] == "audio/mpeg"
    assert abs(_media.probe_audio_duration(made["path"]) - 6.0) < 0.5


def test_frames_can_be_turned_off_by_the_caller():
    """A model that only wants the link — to hand to a user, say — should not
    pay for a decode it is not going to look at."""
    ctx = _Ctx()
    out = mcp_media.prepare(ctx, {"frames": False}, 12 * MB)
    assert out["video"]["storage_key"] == "media/3/prev.mp4"
    assert ctx.pending_images == []


# ── the surface ──────────────────────────────────────────────────────

def test_the_media_control_call_is_not_an_editor_tool():
    """__media__ is plumbing the backend calls on watch_video's behalf. If it
    ever appeared in the catalog the model would see two ways to do this, one
    of them undocumented."""
    published = {t["function"]["name"] for t in mcp_exec.catalog()["tools"]}
    assert mcp_exec.MEDIA_TOOL not in published
