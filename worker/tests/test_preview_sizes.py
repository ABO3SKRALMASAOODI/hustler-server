"""F5: previews customers can read. A Studio request's complete preview is the
approval render, up to 720x1280 where the source allows, and an account that
has not paid never gets more than that: previews carry no mark, so their size
is what protects the paywall."""
import json
import shutil
import subprocess
from types import SimpleNamespace

import numpy as np
import pytest

import agent_tools
import config
import renderer
from schemas import validate_edl


def size(src_w, src_h, quality, paid=False, ratio=None):
    W, H = renderer.frame_dims(src_w, src_h, ratio)
    quality_token = renderer._PREVIEW_QUALITY.set(quality)
    paid_token = renderer._PREVIEW_PAID.set(paid)
    try:
        return renderer.preview_geometry(W, H, 30.0)[:2]
    finally:
        renderer._PREVIEW_QUALITY.reset(quality_token)
        renderer._PREVIEW_PAID.reset(paid_token)


# The render-size table. (source, ratio, quality, paid) -> the file's frame.
TABLE = [
    # the draft stays what it was
    ((1080, 1920), None, "draft", False, (270, 480)),
    ((1920, 1080), None, "draft", False, (854, 480)),
    ((1920, 1080), "9:16", "draft", False, (270, 480)),
    ((576, 1024), None, "draft", False, (270, 480)),
    # approval reaches 720x1280 where the source allows
    ((1080, 1920), None, "approval", False, (720, 1280)),
    ((2160, 3840), None, "approval", False, (720, 1280)),
    ((1440, 2560), None, "approval", False, (720, 1280)),
    ((720, 1280), None, "approval", False, (720, 1280)),
    ((1920, 1080), "9:16", "approval", False, (720, 1280)),
    ((1280, 720), "9:16", "approval", False, (720, 1280)),
    ((1920, 1080), None, "approval", False, (1280, 720)),
    ((3840, 2160), None, "approval", False, (1280, 720)),
    # and never invents pixels the source does not have
    ((576, 1024), None, "approval", False, (576, 1024)),
    ((478, 850), None, "approval", False, (478, 850)),
    ((640, 480), None, "approval", False, (640, 480)),
    # a square or 4:5 frame on an unpaid account stops at the 720 short edge
    ((1080, 1080), None, "approval", False, (720, 720)),
    ((1440, 1440), None, "approval", False, (720, 720)),
    ((1080, 1350), None, "approval", False, (720, 900)),
    ((1920, 1080), "1:1", "approval", False, (720, 720)),
    ((1920, 1080), "4:5", "approval", False, (720, 900)),
    # a subscriber's preview keeps exactly the size it had
    ((1080, 1080), None, "approval", True, (1080, 1080)),
    ((1440, 1440), None, "approval", True, (1280, 1280)),
    ((1080, 1350), None, "approval", True, (1024, 1280)),
    ((1080, 1920), None, "approval", True, (720, 1280)),
    ((3840, 2160), None, "approval", True, (1280, 720)),
]


@pytest.mark.parametrize("src, ratio, quality, paid, want", TABLE)
def test_the_render_size_table(src, ratio, quality, paid, want):
    assert size(*src, quality, paid, ratio) == want


@pytest.mark.parametrize("src", [
    (w, h) for w in (320, 478, 540, 576, 640, 720, 960, 1024, 1080, 1168, 1280,
                     1440, 1920, 2160, 2560, 3840, 4096)
    for h in (240, 360, 480, 576, 720, 850, 1024, 1080, 1280, 1440, 1920, 2030,
              2160, 2560, 3840)])
@pytest.mark.parametrize("ratio", [None, "source", "9:16", "16:9", "1:1", "4:5"])
def test_an_unpaid_account_never_gets_past_720x1280(src, ratio):
    w, h = size(*src, "approval", False, ratio)
    assert min(w, h) <= 720 and max(w, h) <= 1280
    assert w % 2 == 0 and h % 2 == 0


def test_the_paywall_frame_is_the_default_until_the_job_has_read_the_account():
    """A path that never asks gets the smaller frame, not the paywall's."""
    assert renderer._PREVIEW_PAID.get() is False
    token = renderer._PREVIEW_QUALITY.set("approval")
    try:
        assert renderer.preview_geometry(1080, 1080, 30.0)[:2] == (720, 720)
    finally:
        renderer._PREVIEW_QUALITY.reset(token)


def test_a_preview_is_never_spliced_onto_a_render_of_another_size(monkeypatch):
    monkeypatch.setattr(renderer.media, "probe", lambda p: dict(
        width=1080, height=1080, fps=30))
    edl = {"keep": [[0, 5]]}
    token = renderer._PREVIEW_QUALITY.set("approval")
    try:
        same = dict(width=720, height=720)
        assert renderer._same_preview_frame(same, edl, "src.mp4")
        # the account paid since: this preview is drawn 1080 wide
        paid = renderer._PREVIEW_PAID.set(True)
        try:
            assert not renderer._same_preview_frame(same, edl, "src.mp4")
        finally:
            renderer._PREVIEW_PAID.reset(paid)
        assert renderer._same_preview_frame({}, edl, "src.mp4")
    finally:
        renderer._PREVIEW_QUALITY.reset(token)


# ── the picture encode ─────────────────────────────────────────────────────

def encode(quality):
    token = renderer._PREVIEW_QUALITY.set(quality)
    try:
        return renderer.preview_video_encode(), renderer.preview_encode_stamp()
    finally:
        renderer._PREVIEW_QUALITY.reset(token)


def test_the_approval_render_keeps_its_deblocking_and_no_b_frames():
    args, stamp = encode("approval")
    assert args[args.index("-preset") + 1] == "superfast"
    assert args[args.index("-crf") + 1] == "23"
    assert args[args.index("-bf") + 1] == "0"      # stitches splice at keyframes
    assert stamp == "superfast:23"


def test_the_draft_encode_is_untouched():
    args, stamp = encode("draft")
    assert args[args.index("-preset") + 1] == "ultrafast"
    assert args[args.index("-crf") + 1] == "27"
    assert stamp == renderer.PREVIEW_ENCODE_BEFORE_STAMPS


# ── which renders are the approval render ──────────────────────────────────

def turn(seconds=60.0, **extra):
    row = {"version": 2, "json": {"keep": [[0.0, float(seconds)]]}}
    return SimpleNamespace(latest_edl=lambda: row, **extra)


def test_a_studio_requests_complete_preview_is_the_approval_render():
    ctx = turn(studio_request=True)
    assert agent_tools._deliverable_quality(ctx, "draft", True) == "approval"


def test_its_changed_section_proof_stays_a_draft():
    """Nobody watches it, and it is what keeps iterations cheap. Turning it
    into an approval render would make every look a full render."""
    ctx = turn(studio_request=True)
    assert agent_tools._deliverable_quality(ctx, "draft") == "draft"
    assert agent_tools._deliverable_quality(ctx, "draft", False) == "draft"


def test_a_long_programme_keeps_the_draft_and_its_wait():
    limit = config.TYPED_APPROVAL_MAX_S
    assert agent_tools._deliverable_quality(
        turn(limit, studio_request=True), "draft", True) == "approval"
    assert agent_tools._deliverable_quality(
        turn(limit + 1, studio_request=True), "draft", True) == "draft"


@pytest.mark.parametrize("ctx", [turn(), turn(studio_request=False)],
                         ids=["no flag", "mcp and First cut"])
def test_other_surfaces_keep_the_quality_they_ask_for(ctx):
    assert agent_tools._deliverable_quality(ctx, "draft", True) == "draft"
    assert agent_tools._deliverable_quality(ctx, "approval", True) == "approval"


def test_an_approval_asked_for_on_a_long_programme_is_kept():
    ctx = turn(3600.0, studio_request=True)
    assert agent_tools._deliverable_quality(ctx, "approval", True) == "approval"


def test_a_programme_that_cannot_be_read_is_not_held_to_the_draft():
    ctx = SimpleNamespace(latest_edl=lambda: {"json": None},
                          studio_request=True)
    assert agent_tools._programme_seconds(ctx.latest_edl()) == 0.0
    assert agent_tools._deliverable_quality(ctx, "draft", True) == "approval"


HEAVY = {"video": {"width": 2160, "height": 3840, "fps": 50.0}}
LIGHT = {"video": {"width": 1080, "height": 1920, "fps": 30.0}}


@pytest.mark.parametrize("shape, keep, heavy", [
    ((1080, 1920, 30.0), [[0, 30]], False),
    ((1920, 1080, 60.0), [[0, 60]], False),
    ((1080, 1920, 30.0), [[0, 120]], False),     # 7.5 gigapixels
    ((1080, 1920, 30.0), [[0, 130]], True),
    ((3840, 2160, 30.0), [[0, 16]], False),      # p5372
    ((3840, 2160, 30.0), [[0, 40]], True),
    ((2160, 3840, 50.0), [[0, 52]], True),       # p5385
    # a 30 s programme kept across 20 minutes decodes the 20 minutes
    ((1920, 1080, 30.0), [[0, 15], [1185, 1200]], True),
    ((1920, 1080, 30.0), [[100, 115], [130, 145]], False),
])
def test_what_counts_as_too_much_source_for_a_typed_wait(shape, keep, heavy):
    w, h, fps = shape
    row = {"json": {"keep": keep}}
    ctx = SimpleNamespace(index={"video": {"width": w, "height": h, "fps": fps}},
                          latest_edl=lambda: row)
    assert agent_tools._source_outruns_a_typed_wait(ctx) is heavy


def test_a_canvas_project_has_no_source_to_outrun_the_wait():
    canvas = {"json": {"keep": []}}
    ask = agent_tools._source_outruns_a_typed_wait
    assert not ask(SimpleNamespace(index=LIGHT, latest_edl=lambda: canvas))
    assert not ask(SimpleNamespace(index={}, latest_edl=lambda: canvas))
    assert not ask(SimpleNamespace(index=None, latest_edl=lambda: canvas))
    assert not ask(SimpleNamespace(latest_edl=lambda: canvas))


def test_a_heavy_source_ends_its_request_on_the_draft():
    ctx = turn(studio_request=True, index=HEAVY)
    assert agent_tools._deliverable_quality(ctx, "draft", True) == "draft"
    # whatever the editor asks: a replay of p5385 spent 7.7 minutes on three
    # approval renders the model requested of a 4K clip
    assert agent_tools._deliverable_quality(ctx, "approval", True) == "draft"


def test_a_long_form_cut_is_still_the_deliverable_on_a_heavy_source():
    ctx = turn(studio_request=True, index=HEAVY, approval_previews=True)
    assert agent_tools._deliverable_quality(ctx, "draft", True) == "approval"


class _Sharper:
    def __init__(self):
        self.jobs = []

    def run(self, fn, *args, **kwargs):
        assert fn.__name__ == "get_or_enqueue_preview_job"
        self.jobs.append(args[2])
        return 92, True


def sharpening(seconds=60.0, index=HEAVY, quality="draft", version=2, **extra):
    ctx = turn(seconds, studio_request=True, index=index, db=_Sharper(),
               project_id=1, job={"id": 5, "user_id": 2},
               last_preview={"quality": quality, "edl_version": version},
               **extra)
    return ctx


def test_its_approval_render_follows_behind_the_reply():
    ctx = sharpening()
    assert agent_tools.queue_sharper_preview(ctx) == 92
    job = ctx.db.jobs[0]
    assert job["quality"] == "approval" and job["edl_version"] == 2
    assert job["sharpens_draft"] is True
    assert job["render_signature"]


def test_it_is_queued_once_for_a_version():
    ctx = sharpening()
    agent_tools.queue_sharper_preview(ctx)
    assert agent_tools.queue_sharper_preview(ctx) is None
    assert len(ctx.db.jobs) == 1


@pytest.mark.parametrize("ctx", [
    sharpening(index=LIGHT),                    # delivered at approval anyway
    sharpening(quality="approval"),             # nothing to sharpen
    sharpening(version=1),                      # a newer version has no preview
    sharpening(config.TYPED_APPROVAL_MAX_S + 1),   # a long film keeps its draft
    turn(studio_request=False, index=HEAVY, db=_Sharper(),   # MCP, First cut
         last_preview={"quality": "draft", "edl_version": 2}),
    turn(studio_request=True, index=HEAVY, db=_Sharper(), last_preview=None),
], ids=["light", "approval", "stale", "long", "not studio", "no preview"])
def test_nothing_else_gets_a_second_render(ctx):
    assert agent_tools.queue_sharper_preview(ctx) is None
    assert ctx.db.jobs == []


def test_it_waits_for_the_request_to_end():
    """Queued at every reply attempt it was retired half done by the next
    version, twice in one replay of p5385."""
    import inspect
    import agent_loop
    finish = inspect.getsource(agent_loop._auto_render_if_needed)
    assert "queue_sharper_preview" not in finish
    job = inspect.getsource(agent_loop.run_agent_job)
    settled = job.index("_claude_trial_settle(ctx, result)")
    assert job.index("queue_sharper_preview(ctx)") > settled


def test_failing_to_queue_it_never_touches_the_delivered_draft():
    ctx = sharpening()

    def boom(fn, *a, **k):
        raise RuntimeError("database went away")

    ctx.db.run = boom
    assert agent_tools.queue_sharper_preview(ctx) is None


class _Db:
    """Enough of a turn's database for render_preview to enqueue its job."""

    def __init__(self):
        self.enqueued = []

    def run(self, fn, *args, **kwargs):
        name = fn.__name__
        if name == "get_or_enqueue_preview_job":
            self.enqueued.append(args[2])
            return 91, True
        if name == "get_job":
            return {"state": "failed", "error": "stop here", "result": {}}
        return None


@pytest.fixture
def rendering(monkeypatch):
    """render_preview up to the job it enqueues."""
    import render_dependencies
    monkeypatch.setattr(render_dependencies, "before_preview",
                        lambda *a, **k: None)
    monkeypatch.setattr(agent_tools, "_verify_plan_for", lambda *a, **k: [])
    monkeypatch.setattr(agent_tools, "_mcp_adopted_render", lambda *a, **k: None)
    monkeypatch.setattr(agent_tools, "_prefetch_story_review", lambda *a, **k: None)
    monkeypatch.setattr(agent_tools, "_child_payload", lambda ctx, p: p)
    monkeypatch.setattr(agent_tools, "_sequence_screening_frames",
                        lambda *a, **k: None)
    monkeypatch.setattr(agent_tools, "_audio_model_review_enabled",
                        lambda ctx: False)

    def run(**ctx_fields):
        db = _Db()
        ctx = turn(db=db, project_id=1, job={"id": 5, "user_id": 2},
                   rendered_versions=set(), failed_preview_versions={},
                   last_preview=None, spec_preview_jobs={}, sight_out=True,
                   **ctx_fields)
        return ctx, db

    return run


def _enqueued(ctx, db, **kwargs):
    try:
        agent_tools.render_preview(ctx, _wait_timeout_s=0, **kwargs)
    except Exception:  # noqa: BLE001 — only the enqueued job matters here
        pass
    return db.enqueued[0]["quality"]


def test_render_preview_enqueues_the_approval_render_for_a_complete_preview(
        rendering):
    ctx, db = rendering(studio_request=True)
    assert _enqueued(ctx, db, complete=True) == "approval"


def test_the_automatic_render_at_the_end_of_a_turn_is_the_approval_render(
        rendering):
    ctx, db = rendering(studio_request=True, autorendering=True)
    assert _enqueued(ctx, db) == "approval"


def test_an_editors_approval_of_a_heavy_source_is_a_complete_draft(rendering):
    """Not the changed-section proof: it asked for something to watch."""
    ctx, db = rendering(studio_request=True, index=HEAVY)
    assert _enqueued(ctx, db, quality="approval") == "draft"


def test_an_mcp_approval_is_still_what_it_asked_for(rendering):
    ctx, db = rendering(studio_request=False, index=HEAVY)
    assert _enqueued(ctx, db, quality="approval") == "approval"


def test_an_mcp_complete_preview_stays_the_draft_it_asked_for(rendering):
    ctx, db = rendering(studio_request=False)
    assert _enqueued(ctx, db, complete=True) == "draft"


# ── a typed turn's preview is spliced from approval pieces ─────────────────

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _frames(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames,profile,width,height",
         "-of", "json", path], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)["streams"][0]


def _gray(path, t):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(t), "-i", path, "-frames:v", "1",
         "-vf", "format=gray", "-f", "rawvideo", "-"], capture_output=True)
    return np.frombuffer(raw.stdout, np.uint8).astype(np.float32)


@pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg")
def test_an_approval_preview_stitches_to_what_a_full_render_draws(
        tmp_path, monkeypatch):
    src = str(tmp_path / "src.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=20",
         "-f", "lavfi", "-i", "sine=f=220:r=48000:d=20", "-t", "20",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", src],
        check=True)
    index = {"video": {"duration": 20.0, "width": 640, "height": 360,
                       "fps": 30.0},
             "words": [], "sentences": [], "silences": []}

    def edl(keep):
        return validate_edl({"keep": keep}, 20.0).model_dump()

    before = edl([[0.0, 8.0], [10.0, 18.0]])
    after = edl([[0.0, 5.0], [6.5, 8.0], [10.0, 18.0]])
    token = renderer._PREVIEW_QUALITY.set("approval")
    try:
        first, full = str(tmp_path / "a.mp4"), str(tmp_path / "b.mp4")
        renderer.render_edl(before, index, src, first, str(tmp_path), preview=True)
        renderer.render_edl(after, index, src, full, str(tmp_path), preview=True)
        monkeypatch.setattr(renderer, "_cached_source",
                            lambda key: first if key == "prev.mp4" else None)
        stitched = str(tmp_path / "s.mp4")
        seconds = renderer._stitched_preview(
            0, {"version": 2, "json": after}, {"version": 1, "json": before},
            {"storage_key": "prev.mp4"}, index, src, str(tmp_path), {}, stitched)
    finally:
        renderer._PREVIEW_QUALITY.reset(token)
    assert seconds is not None
    got, want = _frames(stitched), _frames(full)
    assert got["profile"] == want["profile"] != "Constrained Baseline"
    assert abs(int(got["nb_read_frames"]) - int(want["nb_read_frames"])) <= 1
    for t in (2.0, 7.5, 10.0, 14.2):
        a, b = _gray(stitched, t), _gray(full, t)
        assert a.size == b.size and a.size
        mse = float(np.mean((a - b) ** 2))
        assert mse < 65.0, f"{t}s differs from the full render"     # PSNR 30
