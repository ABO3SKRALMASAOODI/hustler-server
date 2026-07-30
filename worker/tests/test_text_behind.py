"""Round 60 — words BEHIND the moving subject, pinned against real pixels.

"add a title behind me walking." Every part of the text stack already existed;
what did not was anything that knows which pixels are the person. So the shot is
split into a background photographed out of itself (the per-pixel median) and
whatever differs from it, and the renderer draws the words on its own picture and
lays that difference back over them.

Two claims, and both are pixel claims, so both are checked against a rendered
file rather than a filtergraph string:

  1. The subject is IN FRONT. Where the subject is, the frame shows the subject —
     not the text — while the text is visible either side of them in the same
     frame. A composite that merely faded the text would fail this.
  2. Nothing else changed. Outside the window the picture is the shot, and inside
     it the background away from both text and subject is untouched.

Plus the measurements that decide whether the effect is offered at all: a moving
camera and an empty room are both REFUSALS, because a matte on a moving camera
covers the whole frame (the title vanishes) and a matte on an empty room covers
nothing (the user is told their words are behind someone who is not there).

Run:  python -m pytest tests/test_text_behind.py -q        (from worker/)
"""
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import numpy as np                                            # noqa: E402
import pytest                                                 # noqa: E402

import graphics                                               # noqa: E402
import matte                                                  # noqa: E402
import renderer                                               # noqa: E402
import timeline as tl_mod                                     # noqa: E402
from schemas import validate_edl                              # noqa: E402
from timeline import Timeline                                 # noqa: E402

HAVE_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not present")


def _have_libass():
    """A dev Mac's ffmpeg is routinely built WITHOUT libass, and the executor's
    is not — the round-41 watermark and round-44 RTL work both ran into it. The
    depth composite is the part worth pinning here and it has nothing to do with
    text shaping, so when libass is missing the text layer is stood in for by a
    drawbox of the same shape. The composite under test is identical either way:
    split -> draw something -> alphamerge the mask -> overlay."""
    if not HAVE_FFMPEG:
        return False
    p = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                       capture_output=True)
    return b" subtitles " in p.stdout


HAVE_LIBASS = _have_libass()

W, H, FPS = 640, 360, 30
SHOT_S = 6.0
# The subject: a bright bar that walks left to right across the middle of the
# frame, over a static textured background. That is exactly the case the feature
# exists for, reduced to something a test can assert about.
BAR_W = 90
TEXT_WIN = (1.0, 4.0)


def _bg():
    """A static background with real detail — a flat colour would let a broken
    matte pass by accident (everything differs from a flat plate equally)."""
    rng = np.random.default_rng(11)
    img = np.zeros((H, W, 3), np.uint8)
    img[:, :, 0] = 90
    img[:, :, 1] = 70
    img[:, :, 2] = 55
    for k in range(0, W, 40):                       # vertical "wall" banding
        img[:, k:k + 18] = (110, 88, 66)
    img = np.clip(img.astype(np.int16)
                  + rng.integers(-6, 7, (H, W, 3)), 0, 255).astype(np.uint8)
    return img


def _frame(i, n):
    img = _bg().copy()
    x = int((i / max(1, n - 1)) * (W - BAR_W))
    img[H // 3:2 * H // 3, x:x + BAR_W] = (250, 250, 250)     # the subject
    return img


def _encode(path, frames, fps=FPS):
    p = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
         "-s", f"{W}x{H}", "-r", str(fps), "-i", "pipe:0",
         "-f", "lavfi", "-i",
         f"sine=frequency=440:duration={len(frames) / fps:.3f}",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "8",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE)
    for f in frames:
        p.stdin.write(np.ascontiguousarray(f).tobytes())
    p.stdin.close()
    err = p.stderr.read().decode("utf-8", "replace")
    assert p.wait() == 0, err


@pytest.fixture(scope="module")
def workdir():
    d = tempfile.mkdtemp(prefix="round60_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(scope="module")
def shot(workdir):
    path = os.path.join(workdir, "shot.mp4")
    n = int(SHOT_S * FPS)
    _encode(path, [_frame(i, n) for i in range(n)])
    return path


@pytest.fixture(scope="module")
def still(workdir):
    """The same background with nothing moving in it."""
    path = os.path.join(workdir, "still.mp4")
    _encode(path, [_bg() for _ in range(int(3.0 * FPS))])
    return path


@pytest.fixture(scope="module")
def panning(workdir):
    """A camera that MOVES. The texture is NOISE, not the banded wall: a
    repeating pattern slid sideways still matches itself every period, and a
    fixture that accidentally matches its own plate would let a broken refusal
    pass."""
    path = os.path.join(workdir, "pan.mp4")
    n = int(3.0 * FPS)
    rng = np.random.default_rng(3)
    base = rng.integers(20, 230, (H, W + n * 4, 3), dtype=np.uint8)
    frames = [np.ascontiguousarray(base[:, i * 4:i * 4 + W]) for i in range(n)]
    _encode(path, frames)
    return path


# ------------------------------------------------------------------ #
#  1. The measurement, which is what decides whether this is offered  #
# ------------------------------------------------------------------ #

@needs_ffmpeg
def test_a_walking_subject_is_measured(shot, workdir):
    out = os.path.join(workdir, "m_ok.mp4")
    res = matte.measure_and_build(shot, out, 1.0, 3.0, width=W, height=H,
                                  box=(0.2, 0.4, 0.6, 0.2))
    assert res["ok"], res
    assert os.path.exists(out)
    # The bar is 90 of 640 wide and a third of the height: ~4.7% of the frame.
    assert 0.02 < res["coverage"] < 0.12, res
    assert res["frames"] > 60
    # It crosses the text band, which is what makes the effect visible at all
    assert res["text_covered"] > 0.05, res


@needs_ffmpeg
def test_a_moving_camera_is_refused_and_writes_nothing(panning, workdir):
    """The refusal that matters most. On a pan, everything differs from the
    plate, so the matte would cover the frame and the title would simply never
    appear — the user would see nothing and report the feature as broken."""
    out = os.path.join(workdir, "m_pan.mp4")
    res = matte.measure_and_build(panning, out, 0.2, 2.5, width=W, height=H)
    assert not res["ok"]
    assert "camera is moving" in res["why"]
    assert not os.path.exists(out), "a refused measurement must leave no file"


@needs_ffmpeg
def test_an_empty_room_is_refused(still, workdir):
    out = os.path.join(workdir, "m_still.mp4")
    res = matte.measure_and_build(still, out, 0.2, 2.5, width=W, height=H)
    assert not res["ok"]
    assert "nothing moves" in res["why"]
    assert not os.path.exists(out)


@needs_ffmpeg
def test_an_over_long_window_is_refused_before_any_decoding(shot, workdir):
    out = os.path.join(workdir, "m_long.mp4")
    res = matte.measure_and_build(shot, out, 0.0, matte.MAX_WINDOW_S + 1,
                                  width=W, height=H)
    assert not res["ok"] and "does not finish inside one edit turn" in res["why"]
    assert not os.path.exists(out)


# ------------------------------------------------------------------ #
#  2. The composite, in rendered pixels                               #
# ------------------------------------------------------------------ #

def _edl(mask_key="mask.mp4", win=TEXT_WIN):
    return {
        "keep": [[0.0, SHOT_S]],
        "texts": [{"id": "tx1", "text": "BEHIND", "start": win[0],
                   "end": win[1], "template": "title", "size_scale": 1.6,
                   "behind": {"asset_key": mask_key, "src_start": win[0],
                              "src_end": win[1], "fp": "testfp"}}],
    }


# Where the stand-in "words" are drawn when libass is missing: a band across
# the rows the subject walks through, so the two genuinely overlap.
STAND_IN = (int(W * 0.15), int(H * 0.40), int(W * 0.70), int(H * 0.20))


def _render(src, mask_path, edl_dict, out, workdir, win=TEXT_WIN):
    edl = validate_edl(dict(edl_dict), SHOT_S).model_dump()
    tl = Timeline(edl["keep"], [], [])
    ass = graphics.build_gfx_ass(dict(edl, texts=edl["texts"]),
                                 tl.out_duration,
                                 os.path.join(workdir, "behind.ass"),
                                 play_res=(W, H))
    assert ass, "the text layer produced no ASS to burn"
    graph = renderer.build_filtergraph(
        edl, SHOT_S, True, tl, None, [], {"video": {"duration": SHOT_S}},
        preview=False, W=W, H=H, fps=float(FPS),
        behind_inputs=[(1, {"ass": ass}, win)])
    if not HAVE_LIBASS:
        x, y, bw, bh = STAND_IN
        graph = graph.replace(
            f"subtitles=filename='{ass}':fontsdir='{renderer.caplib.FONTS_DIR}'",
            f"drawbox=x={x}:y={y}:w={bw}:h={bh}:color=magenta@1:t=fill"
            f":enable='between(t,{win[0]:.3f},{win[1]:.3f})'")
        assert "drawbox" in graph, "the stand-in did not replace the text burn"
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", src, "-i", mask_path,
           "-filter_complex", graph, "-map", "[vout]", "-map", "[aout]",
           "-c:v", "libx264", "-preset", "ultrafast", "-crf", "8",
           "-pix_fmt", "yuv420p", "-c:a", "aac", out]
    r = subprocess.run(cmd, capture_output=True)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")[-3000:]
    return out


def _frame_at(path, t):
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.4f}", "-i", path,
         "-frames:v", "1", "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"],
        capture_output=True)
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")
    buf = p.stdout
    assert len(buf) >= W * H * 3, f"short frame at {t}s"
    return np.frombuffer(buf[:W * H * 3], np.uint8).reshape(H, W, 3)


def _subject_cols(src_frame):
    """Which columns the subject occupies in the SOURCE at this instant."""
    band = slice(H // 3 + 6, 2 * H // 3 - 6)
    f = src_frame[band].astype(np.int16)
    cols = (f.min(axis=2) > 200).any(axis=0)
    return band, cols


def _changed(render_frame, src_frame, thresh=30):
    """Which pixels the composite changed. Colour-agnostic on purpose: the words
    are white type under libass and a magenta box in the stand-in, and the claim
    under test is about WHERE the picture changed, not what colour it went."""
    d = np.abs(render_frame.astype(np.int16) - src_frame.astype(np.int16))
    return d.max(axis=2) > thresh


@pytest.fixture(scope="module")
def rendered(shot, workdir):
    mask = os.path.join(workdir, "mask.mp4")
    res = matte.measure_and_build(shot, mask, TEXT_WIN[0],
                                  TEXT_WIN[1] - TEXT_WIN[0],
                                  width=W, height=H)
    assert res["ok"], res
    out = os.path.join(workdir, "out.mp4")
    return _render(shot, mask, _edl(), out, workdir)


@needs_ffmpeg
def test_the_subject_is_in_front_of_the_words(rendered, shot):
    """THE claim, and the only one that separates this from an ordinary title.

    At the middle of the window: the words are on screen (the picture changed
    somewhere), and the columns the subject occupies are UNCHANGED — the words
    did not print onto the person. A composite that drew the text on top would
    change those columns most of all, because that is exactly where the two
    overlap."""
    t = (TEXT_WIN[0] + TEXT_WIN[1]) / 2.0
    frame, src = _frame_at(rendered, t), _frame_at(shot, t)
    band, cols = _subject_cols(src)
    assert cols.any(), "the fixture has no subject at that instant"
    x0, x1 = int(np.argmax(cols)), int(W - np.argmax(cols[::-1]))
    changed = _changed(frame, src)
    # 1. the words are there at all
    assert changed.sum() > 400, "nothing was drawn — no text layer reached the frame"
    # 2. and NOT on the subject. Inset by 8px: the mask is feathered, so its
    #    outermost pixels are a deliberate blend rather than a hard claim.
    on_subject = changed[band, x0 + 8:x1 - 8]
    assert on_subject.mean() < 0.02, (
        f"{on_subject.mean() * 100:.1f}% of the subject's own pixels were "
        "overprinted — the text is in FRONT of them, not behind")
    # 3. ...while the same rows either side of the subject DID change, which is
    #    what proves the words cross the subject's path rather than missing it.
    beside = changed[band].copy()
    beside[:, max(0, x0 - 8):min(W, x1 + 8)] = False
    assert beside.sum() > 200, \
        "the words never reach the subject's band — the test proves nothing"


@needs_ffmpeg
@pytest.mark.parametrize("frac", [0.08, 0.5, 0.92])
def test_the_subject_stays_protected_ACROSS_the_window(rendered, shot, frac):
    """Alignment, not just presence. The subject walks the width of the frame
    during the window, so a mask that was a fraction of a second out of step
    would protect the wrong columns — near the start it would be ahead of the
    person and near the end behind them."""
    t = TEXT_WIN[0] + (TEXT_WIN[1] - TEXT_WIN[0]) * frac
    frame, src = _frame_at(rendered, t), _frame_at(shot, t)
    band, cols = _subject_cols(src)
    assert cols.any()
    x0, x1 = int(np.argmax(cols)), int(W - np.argmax(cols[::-1]))
    on_subject = _changed(frame, src)[band, x0 + 8:x1 - 8]
    assert on_subject.mean() < 0.03, (
        f"at {frac:.0%} through the window, {on_subject.mean() * 100:.1f}% of "
        "the subject was overprinted — the mask is out of step with the shot")


@needs_ffmpeg
def test_the_control_case_fails_the_same_assertion(shot, workdir):
    """The negative control, without which none of the above proves anything.

    Burn the SAME words with no mask composite — an ordinary front title — and
    the subject's own pixels must change. If this passed, the assertions above
    would be measuring nothing."""
    edl = validate_edl(_edl(), SHOT_S).model_dump()
    tl = Timeline(edl["keep"], [], [])
    x, y, bw, bh = STAND_IN
    ass = graphics.build_gfx_ass(dict(edl, texts=edl["texts"]),
                                 tl.out_duration,
                                 os.path.join(workdir, "front.ass"),
                                 play_res=(W, H))
    if HAVE_LIBASS:
        layer = (f"subtitles=filename='{ass}'"
                 f":fontsdir='{renderer.caplib.FONTS_DIR}'")
    else:
        layer = (f"drawbox=x={x}:y={y}:w={bw}:h={bh}:color=magenta@1:t=fill"
                 f":enable='between(t,{TEXT_WIN[0]:.3f},{TEXT_WIN[1]:.3f})'")
    out = os.path.join(workdir, "front.mp4")
    r = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", shot,
         "-filter_complex", f"[0:v]{layer}[vout]",
         "-map", "[vout]", "-c:v", "libx264", "-preset", "ultrafast",
         "-crf", "8", "-pix_fmt", "yuv420p", "-an", out],
        capture_output=True)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")[-2000:]
    t = (TEXT_WIN[0] + TEXT_WIN[1]) / 2.0
    frame, src = _frame_at(out, t), _frame_at(shot, t)
    band, cols = _subject_cols(src)
    x0, x1 = int(np.argmax(cols)), int(W - np.argmax(cols[::-1]))
    on_subject = _changed(frame, src)[band, x0 + 8:x1 - 8]
    assert on_subject.mean() > 0.10, (
        "a plain front title did not print on the subject either, so the "
        "behind-test's assertion cannot distinguish the two")


@needs_ffmpeg
def test_the_shot_is_untouched_outside_the_window(rendered, shot):
    for t in (0.3, SHOT_S - 0.4):
        a = _frame_at(rendered, t).astype(np.int16)
        b = _frame_at(shot, t).astype(np.int16)
        assert float(np.mean(np.abs(a - b))) < 3.0, \
            f"the picture changed at {t}s, outside the text's window"


@needs_ffmpeg
def test_the_background_away_from_the_words_is_untouched(rendered, shot):
    """The mask is padded to the whole programme with BLACK (alpha 0) so
    alphamerge has a frame to pair with every frame of the picture. If that
    padding were white — or if the mask were mis-timed — the subject layer would
    print the shot over itself somewhere it should not."""
    t = (TEXT_WIN[0] + TEXT_WIN[1]) / 2.0
    a = _frame_at(rendered, t).astype(np.int16)
    b = _frame_at(shot, t).astype(np.int16)
    corner = (slice(0, 40), slice(0, 40))          # far from title and subject
    assert float(np.mean(np.abs(a[corner] - b[corner]))) < 3.0


# ------------------------------------------------------------------ #
#  3. It belongs to its FOOTAGE                                       #
# ------------------------------------------------------------------ #

def test_a_behind_text_follows_a_cut_made_later():
    """Content-anchored, like a zoom. Trim 2s off the front and the words move
    2s earlier with the footage the mask was measured on — clamped in program
    time they would slide off their own matte and cut the subject out of a
    different second of video."""
    edl = validate_edl(_edl(win=(3.0, 5.0)), SHOT_S).model_dump()
    old = Timeline([[0.0, SHOT_S]], [], [])
    new = Timeline([[2.0, SHOT_S]], [], [])
    edl["keep"] = [[2.0, SHOT_S]]
    notes = tl_mod.remap_program_items(edl, old, new)
    assert edl["texts"][0]["start"] == 1.0
    assert edl["texts"][0]["end"] == 3.0
    assert any("behind the same subject" in n for n in notes), notes
    # the mask itself is NOT rewritten: it is source-anchored by construction
    assert edl["texts"][0]["behind"]["src_start"] == 3.0


def test_a_behind_text_dies_with_its_footage():
    edl = validate_edl(_edl(win=(1.0, 3.0)), SHOT_S).model_dump()
    old = Timeline([[0.0, SHOT_S]], [], [])
    new = Timeline([[4.0, SHOT_S]], [], [])
    edl["keep"] = [[4.0, SHOT_S]]
    notes = tl_mod.remap_program_items(edl, old, new)
    assert edl["texts"] == []
    assert any("no longer in the edit" in n for n in notes), notes


def test_an_ordinary_text_is_still_program_anchored():
    """The other half of the same policy: a plain title covers a span of the
    EDIT, so it must NOT be dragged through the source."""
    e = _edl()
    e["texts"][0].pop("behind")
    edl = validate_edl(e, SHOT_S).model_dump()
    old = Timeline([[0.0, SHOT_S]], [], [])
    new = Timeline([[2.0, SHOT_S]], [], [])
    edl["keep"] = [[2.0, SHOT_S]]
    tl_mod.remap_program_items(edl, old, new)
    assert edl["texts"][0]["start"] == 1.0    # unchanged program position


# ------------------------------------------------------------------ #
#  4. Schema + graph invariants                                       #
# ------------------------------------------------------------------ #

def test_an_untouched_text_hashes_exactly_as_before():
    """`behind` is absent on every text ever written, and _sig_canon drops
    nested None keys — so no stored EDL re-renders because this field exists."""
    from schemas import edl_signature
    a = {"keep": [[0.0, SHOT_S]],
         "texts": [{"id": "tx1", "text": "hi", "start": 1.0, "end": 2.0}]}
    sig = edl_signature(validate_edl(dict(a), SHOT_S).model_dump())
    assert "behind" not in sig


def test_the_behind_stage_forces_normalized_frames():
    """alphamerge pairs a WxH mask with the picture per pixel, so the cheap
    single-source graph (which never normalizes) must not be taken."""
    edl = validate_edl(_edl(), SHOT_S).model_dump()
    tl = Timeline(edl["keep"], [], [])
    graph = renderer.build_filtergraph(
        edl, SHOT_S, True, tl, None, [], {}, preview=False, W=W, H=H,
        fps=float(FPS), behind_inputs=[(1, {"ass": "/tmp/x.ass"}, TEXT_WIN)])
    assert "alphamerge" in graph
    assert f"scale={W}:{H}" in graph
    # the mask is padded to the whole programme, from t=0
    assert "tpad=start_duration=1.000" in graph


# ------------------------------------------------------------------ #
#  5. The tool itself, on real footage                                #
# ------------------------------------------------------------------ #

import agent_tools                                             # noqa: E402
from schemas import default_edl                                # noqa: E402


class _Ctx:
    def __init__(self, proxy, workdir, duration=SHOT_S):
        self.project_id = 1
        self.workdir = workdir
        self.duration = duration
        self.index = {"video": {"duration": duration, "width": W,
                                "height": H, "fps": FPS},
                      "words": [], "sentences": []}
        self.has_main_video = True
        self._orig_sha = "sha-for-test"
        self._proxy = proxy
        self.written = []
        self._edl = default_edl(duration)

    def proxy_path(self):
        return self._proxy

    def latest_edl(self):
        return {"version": len(self.written) + 1, "json": self._edl}

    def write_edl(self, edl, desc):
        # Validate exactly as the real ToolContext does, or a tool test can pass
        # while writing an EDL the renderer would reject.
        self._edl = validate_edl(dict(edl), self.duration).model_dump()
        self.written.append(desc)
        return f"EDL v{len(self.written)}: {desc}"


@pytest.fixture
def uploads(monkeypatch):
    put = []
    monkeypatch.setattr(agent_tools.storage, "exists", lambda k: False)
    monkeypatch.setattr(agent_tools.storage, "upload_file",
                        lambda path, key, ct: put.append((key, path)))
    return put


@needs_ffmpeg
def test_the_tool_writes_a_behind_text_and_uploads_its_mask(shot, workdir,
                                                            uploads):
    ctx = _Ctx(shot, workdir)
    res = agent_tools.add_text_behind(ctx, "BEHIND ME", 1.0, duration_s=2.5,
                                      size_scale=1.4)
    assert res.startswith("EDL v"), res
    tx = ctx._edl["texts"][0]
    assert tx["behind"]["asset_key"].startswith("matte/1/")
    assert tx["behind"]["src_start"] == 1.0
    assert uploads and uploads[0][0] == tx["behind"]["asset_key"]
    # the reply carries the MEASUREMENT, not an assurance
    assert "MEASURED on the footage" in res
    assert "%" in res


@needs_ffmpeg
def test_the_tool_refuses_on_a_moving_camera_and_writes_nothing(panning,
                                                                workdir,
                                                                uploads):
    """The refusal a user will actually hit, through the tool rather than the
    measurement: nothing is written, nothing is uploaded, and the reply tells
    the agent what to offer instead."""
    ctx = _Ctx(panning, workdir, duration=3.0)
    res = agent_tools.add_text_behind(ctx, "NOPE", 0.2, duration_s=2.0)
    assert res.startswith("REJECTED"), res
    assert "camera is moving" in res
    assert "add_text" in res              # the honest alternative is named
    assert ctx.written == [] and uploads == []


@needs_ffmpeg
def test_the_tool_refuses_a_window_that_crosses_a_cut(shot, workdir, uploads):
    ctx = _Ctx(shot, workdir)
    ctx._edl = validate_edl(
        {"keep": [[0.0, 2.0], [4.0, SHOT_S]]}, SHOT_S).model_dump()
    res = agent_tools.add_text_behind(ctx, "X", 1.0, duration_s=2.0)
    assert res.startswith("REJECTED") and "CUT inside" in res
    assert ctx.written == [] and uploads == []


@needs_ffmpeg
def test_the_tool_refuses_over_a_speed_ramp(shot, workdir, uploads):
    ctx = _Ctx(shot, workdir)
    ctx._edl = validate_edl(
        {"keep": [[0.0, SHOT_S]],
         "speed": [{"id": "sp1", "start": 0.5, "end": 3.0, "factor": 2.0}]},
        SHOT_S).model_dump()
    res = agent_tools.add_text_behind(ctx, "X", 0.6, duration_s=1.0)
    assert res.startswith("REJECTED") and "speed ramp" in res
    assert ctx.written == [] and uploads == []


@needs_ffmpeg
def test_the_second_ask_over_the_same_moment_reuses_the_mask(shot, workdir,
                                                             monkeypatch):
    """Re-wording a title over the same window must not re-measure: the
    fingerprint covers the footage and the geometry, not the words."""
    seen = {}
    monkeypatch.setattr(agent_tools.storage, "exists",
                        lambda k: k in seen)
    monkeypatch.setattr(agent_tools.storage, "upload_file",
                        lambda path, key, ct: seen.setdefault(key, path))
    ctx = _Ctx(shot, workdir)
    assert agent_tools.add_text_behind(ctx, "FIRST", 1.0,
                                      duration_s=2.0).startswith("EDL v")
    assert len(seen) == 1
    res = agent_tools.add_text_behind(ctx, "SECOND", 1.0, duration_s=2.0)
    assert res.startswith("EDL v"), res
    assert len(seen) == 1, "the mask was measured and uploaded twice"
    assert "already measured" in res
    keys = {t["behind"]["asset_key"] for t in ctx._edl["texts"]}
    assert len(keys) == 1


@needs_ffmpeg
def test_a_reframed_project_measures_the_mask_in_the_OUTPUT_frame(shot,
                                                                  workdir,
                                                                  uploads):
    """A 9:16 project throws away 44% of a 16:9 shot's width. A mask measured on
    the whole frame would be a subject cut out of the wrong part of the picture,
    so the measurement goes through renderer.frame_fit_filter — the same crop
    the picture itself gets, including the focus point."""
    ctx = _Ctx(shot, workdir)
    ctx._edl = validate_edl(
        {"keep": [[0.0, SHOT_S]],
         "frame": {"ratio": "9:16", "mode": "crop", "focus_x": 0.3}},
        SHOT_S).model_dump()
    fit, mw, mh = agent_tools._matte_geometry(ctx, ctx._edl)
    assert "crop=" in fit and "clip(iw*0.3000" in fit
    assert abs((mw / mh) - (9 / 16)) < 0.02, (mw, mh)
    res = agent_tools.add_text_behind(ctx, "TALL", 1.0, duration_s=2.0)
    assert res.startswith("EDL v"), res


def test_the_tool_refuses_without_a_main_video(workdir):
    ctx = _Ctx("unused.mp4", workdir)
    ctx.has_main_video = False
    res = agent_tools.add_text_behind(ctx, "X", 1.0)
    assert res.startswith("REJECTED") and "no main video" in res


@needs_ffmpeg
def test_render_edl_end_to_end_puts_the_mask_on_the_right_input(shot, workdir,
                                                                monkeypatch):
    """The wiring the graph test cannot reach: render_edl builds the ffmpeg
    input LIST, and the mask's index has to be the position it actually ends up
    in. An off-by-one there aims alphamerge at the end card or at a music track
    — and it would still render, just wrongly."""
    mask = os.path.join(workdir, "e2e_mask.mp4")
    res = matte.measure_and_build(shot, mask, TEXT_WIN[0],
                                  TEXT_WIN[1] - TEXT_WIN[0],
                                  width=W, height=H)
    assert res["ok"], res
    monkeypatch.setattr(renderer.storage, "download_to",
                        lambda key, local: shutil.copyfile(mask, local))
    # A behind text AND an end card AND a watermark, so the mask is not simply
    # the last input by luck.
    edl = _edl(mask_key="matte/1/x.mp4")
    out = os.path.join(workdir, "e2e.mp4")
    real, seen = renderer.build_filtergraph, {}
    x, y, bw, bh = STAND_IN

    def _patched(*a, **kw):
        g = real(*a, **kw)
        seen["graph"] = g
        if HAVE_LIBASS:
            return g
        # No libass here: swap the burn for a drawbox, exactly as _render does.
        import re as _re
        return _re.sub(
            r"subtitles=filename='[^']*behind_0\.ass':fontsdir='[^']*'",
            f"drawbox=x={x}:y={y}:w={bw}:h={bh}:color=magenta@1:t=fill"
            f":enable='between(t,{TEXT_WIN[0]:.3f},{TEXT_WIN[1]:.3f})'", g)
    monkeypatch.setattr(renderer, "build_filtergraph", _patched)
    dur = renderer.render_edl(edl, {"video": {"duration": SHOT_S}, "words": [],
                                   "sentences": []},
                              shot, out, workdir, preview=True)
    assert dur and abs(dur - SHOT_S) < 1.5, dur
    # The composite really was in the graph render_edl built (not a fallback
    # that happened to look right).
    assert "alphamerge" in seen["graph"]
    t = (TEXT_WIN[0] + TEXT_WIN[1]) / 2.0
    frame, src = _frame_at(out, t), _frame_at(shot, t)
    band, cols = _subject_cols(src)
    x0, x1 = int(np.argmax(cols)), int(W - np.argmax(cols[::-1]))
    changed = _changed(frame, src, thresh=40)
    assert changed.sum() > 300, "nothing was drawn through the real render path"
    assert changed[band, x0 + 10:x1 - 10].mean() < 0.05, \
        "the subject was overprinted — the mask did not reach alphamerge"


def _strip_libass(graph):
    """Drop every libass burn from a graph, for a dev box built without it.
    Used where the claim under test is that the render SUCCEEDS, not what it
    draws."""
    import re as _re
    return _re.sub(r"subtitles=filename='[^']*'(?::fontsdir='[^']*')?",
                   "null", graph)


@needs_ffmpeg
def test_render_edl_degrades_to_a_plain_title_when_the_mask_is_missing(
        shot, workdir, monkeypatch):
    """A mask object that will not download must not fail the render. Losing the
    depth is a disappointment; losing the export is a broken product — and the
    graph must come out with no alphamerge in it at all, not with a dangling
    input."""
    monkeypatch.setattr(
        renderer.storage, "download_to",
        lambda key, local: (_ for _ in ()).throw(RuntimeError("no such object")))
    seen = {}
    real = renderer.build_filtergraph

    def _capture(*a, **kw):
        g = real(*a, **kw)
        seen["graph"] = g
        return g if HAVE_LIBASS else _strip_libass(g)
    monkeypatch.setattr(renderer, "build_filtergraph", _capture)
    out = os.path.join(workdir, "degraded.mp4")
    dur = renderer.render_edl(_edl(mask_key="matte/1/gone.mp4"),
                              {"video": {"duration": SHOT_S}, "words": [],
                               "sentences": []},
                              shot, out, workdir, preview=True)
    assert dur and abs(dur - SHOT_S) < 1.5
    assert "alphamerge" not in seen["graph"]
    assert os.path.getsize(out) > 1000


@needs_ffmpeg
def test_exposure_breathing_does_not_fool_the_matte(workdir):
    """Round 62, project 246: a dark handheld iPhone shot whose auto-exposure
    breathes as the subject crosses a lamp. v1's fixed global threshold read
    the breathing as subject (a band across the frame — the title vanished
    behind nobody) while the dark-jacket subject sat UNDER the threshold and
    was overprinted. Both failure modes are pinned here at once: coverage must
    stay near the subject's true footprint (no over-mask) AND actually find
    the subject (no under-mask). The under-mask half also guards the noise
    map's own bias correction — global drift measured as per-pixel noise rode
    the threshold to its ceiling and cost 88% of the subject."""
    w, h, fps, dur = 480, 270, 24, 4.0
    sw, sh = 50, 120
    rng = np.random.default_rng(7)
    room = rng.integers(20, 60, (h, w, 3), np.uint8)
    src = os.path.join(workdir, "drift.mp4")
    n = int(dur * fps)
    enc = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt",
         "bgr24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "pipe:0",
         "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", src],
        stdin=subprocess.PIPE)
    for i in range(n):
        t = i / float(n - 1)
        bias = int(round(24 * np.sin(2 * np.pi * t * 1.3)))
        f = np.clip(room.astype(np.int16) + bias, 0, 255).astype(np.uint8)
        x0, y0 = int((w - sw) * t), (h - sh) // 2
        f[y0:y0 + sh, x0:x0 + sw] = rng.integers(35, 75, (sh, sw, 3),
                                                 np.uint8)
        enc.stdin.write(f.tobytes())
    enc.stdin.close()
    assert enc.wait() == 0
    out = os.path.join(workdir, "drift_mask.mp4")
    res = matte.measure_and_build(src, out, 0.0, dur, fps=float(fps))
    assert res["ok"], res.get("why")
    true_cov = sw * sh / float(w * h)
    assert res["coverage"] < 3.0 * true_cov, \
        f"over-masking: {res['coverage']} vs true {true_cov}"
    assert res["coverage"] > 0.5 * true_cov, \
        f"under-masking: {res['coverage']} vs true {true_cov}"
    assert res["moving_frames"] >= n * 0.9


def test_no_behind_text_changes_nothing_in_the_graph():
    edl = validate_edl({"keep": [[0.0, SHOT_S]]}, SHOT_S).model_dump()
    tl = Timeline(edl["keep"], [], [])
    plain = renderer.build_filtergraph(edl, SHOT_S, True, tl, None, [], {},
                                       preview=False, W=W, H=H, fps=float(FPS))
    same = renderer.build_filtergraph(edl, SHOT_S, True, tl, None, [], {},
                                      preview=False, W=W, H=H, fps=float(FPS),
                                      behind_inputs=[])
    assert plain == same
    assert "alphamerge" not in plain
