"""p6311 (26 Nov 2026): a customer asked to remove the green screen around
the presenter, the candles and the props: "clean edges, no green spill,
composite it over a new background (I will say which if needed)". The
upload was a greeting keyed earlier over city pictures, with a green line
round the presenter, the flags and the fir sprigs and a green disc round
the candle flame. These tests hold what the product now does:

* a screen behind the subject is recognised at any colour strength (dim,
  unevenly lit, vignetted), while a sky or a wall in a few frames of a
  composite is not taken for one;
* effects.key renders: the screen is gone from the exported pixels, the
  subject keeps its colour, a green prop away from the screen keeps its
  green (despill only on the band next to the screen);
* footage keyed before gets its leftover cleaned (``mode="fringe"``): the
  green line and the green disc are gone, the footage's own background and
  the subject are untouched, and nothing is laid behind a person cut-out;
* the dry run measures the key's real transparency (over black and over
  white), so a key that leaves screen is widened and one that eats the
  subject is narrowed, and both numbers are reported;
* a background picture that cannot be read keys onto the neutral gradient,
  never black.
"""
import re
import shutil
import subprocess

import numpy as np
import pytest

import chroma_key as ck
import schemas

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="no ffmpeg")

GREEN = "0x1fb84a"
SKIN = "0xd9a07a"
SKIN_RGB = (217, 160, 122)


def _green_screen(path, dur=4.0, size=320):
    """A lit green backdrop with a skin-toned subject that moves, plus a
    soft green spill line on the subject's edge."""
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"color=c={GREEN}:s={size}x{size}:d={dur}:r=25",
         "-f", "lavfi", "-i", f"sine=frequency=220:duration={dur}",
         "-vf", (f"drawbox=x=100+15*sin(t*3):y=80:w=110:h=200:"
                 f"color={SKIN}@1:t=fill,"
                 f"drawbox=x=98+15*sin(t*3):y=78:w=114:h=204:"
                 f"color=0x8fb878@1:t=2,noise=alls=5:allf=t"),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-shortest", path], check=True)
    return path


def _frames_video(path, frames, fps=25):
    h, w = frames[0].shape[:2]
    p = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{w}x{h}", "-r", str(fps), "-i", "-", "-c:v", "libx264",
         "-crf", "12", "-pix_fmt", "yuv420p", path], stdin=subprocess.PIPE)
    for f in frames:
        p.stdin.write(np.clip(f, 0, 255).astype(np.uint8).tobytes())
    p.stdin.close()
    assert p.wait() == 0
    return path


def _screen_frames(rgb, vignette=0.85, size=240, n=50, prop=None):
    """A (dim, vignetted) screen, a skin subject that moves, a white candle
    in front of the screen and optionally a coloured prop inside the
    subject's silhouette (held in its hands, away from the screen)."""
    yy, xx = np.mgrid[0:size, 0:size] / (size - 1.0)
    r2 = (xx - .5) ** 2 + (yy - .5) ** 2
    v = 1.0 - vignette * (r2 / r2.max())
    rng = np.random.default_rng(1)
    out = []
    for i in range(n):
        f = np.ones((size, size, 3)) * np.array(rgb, float) * v[..., None]
        cx = .5 + .04 * np.sin(i / 8.0)
        f[((xx - cx) / .17) ** 2 + ((yy - .42) / .26) ** 2 < 1] = SKIN_RGB
        f[(yy > .62) & (abs(xx - cx) < .22)] = (230, 90, 40)
        f[(yy > .7) & (xx > .06) & (xx < .16)] = (240, 240, 240)
        if prop:
            f[(yy > .7) & (yy < .9) & (abs(xx - cx) < .08)] = prop
        out.append(f + rng.normal(0, 3, f.shape))
    return out


def _keyed_before_frames(size=240, n=50):
    """p6311's kind of upload: a presenter keyed earlier over a red city
    picture, with the old screen's green left as a 1-2 px line round her
    and a green disc round a candle flame."""
    yy, xx = np.mgrid[0:size, 0:size]
    rng = np.random.default_rng(2)
    out = []
    for i in range(n):
        f = np.zeros((size, size, 3))
        f[...] = (200, 40, 50)
        f[:, :, 0] += 30 * np.sin(xx / 9.0)                 # some texture
        cx = 110 + 4 * np.sin(i / 8.0)
        d = np.sqrt(((xx - cx) / 40.0) ** 2 + ((yy - 110) / 60.0) ** 2)
        f[d < 1.06] = (122, 150, 92)                        # leftover line
        f[d < 1.0] = SKIN_RGB
        disc = np.hypot(xx - 200, yy - 60)
        f[disc < 12] = (90, 142, 66)                        # green disc
        f[disc < 4] = (255, 230, 150)                       # flame
        f[(xx > 196) & (xx < 204) & (yy > 64) & (yy < 140)] = (235, 235, 235)
        out.append(f + rng.normal(0, 2, f.shape))
    return out


def _rgb_at(path, t, x, y, size=320):
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", path, "-frames:v", "1",
         "-vf", f"scale={size}:{size},format=rgb24", "-f", "rawvideo", "-"],
        capture_output=True, check=True)
    a = np.frombuffer(r.stdout, np.uint8).reshape(size, size, 3)
    return a[y - 3:y + 4, x - 3:x + 4].reshape(-1, 3).mean(0)


def _synth(rgb, vignette, n=8, size=200):
    return [np.clip(f, 0, 255).astype(np.uint8)
            for f in _screen_frames(rgb, vignette, size, n)]


# ── measurement ─────────────────────────────────────────────────────────────

def test_measure_reads_a_green_screen():
    f = np.zeros((100, 100, 3), np.uint8)
    f[:] = (31, 184, 74)
    f[30:80, 35:65] = (217, 160, 122)
    m = ck.measure_frames([f, f])
    assert m["screen"] == "green" and m["keyable"]
    assert 0.6 < m["share"] < 0.9
    key = [int(m["colour"][i:i + 2], 16) for i in (1, 3, 5)]
    assert abs(key[1] - 184) <= 3


@pytest.mark.parametrize("rgb", [(80, 170, 90), (74, 165, 88), (60, 140, 70),
                                 (100, 180, 100)])
@pytest.mark.parametrize("vignette", [0.0, 0.6])
def test_dim_and_unevenly_lit_screens_are_keyable(rgb, vignette):
    # Review of 26 Nov: these measured colour strength 0.10-0.11 (0.08 when
    # vignetted) and a fixed 0.13 floor sent them to the person cut-out,
    # losing the props, with "the footage was keyed before" as the reason.
    m = ck.measure_frames(_synth(rgb, vignette))
    assert m["screen"] == "green" and m["keyable"], m
    assert m["chroma"] < 0.13


def test_a_composite_background_is_not_a_screen():
    # p6311's frames: a keyed presenter over changing city photographs. The
    # bluish sky covers 8% of the picture and changes colour from scene to
    # scene: no screen stands behind her.
    frames = []
    for i, sky in enumerate([(30, 58, 126), (51, 72, 108), (30, 77, 140),
                             (40, 96, 179), (56, 58, 86), (63, 63, 89),
                             (38, 97, 179), (60, 74, 116)]):
        f = np.zeros((100, 100, 3), np.uint8)
        f[:] = (150, 120, 110)
        f[:9 + i % 3] = sky
        frames.append(f)
    m = ck.measure_frames(frames)
    assert not m["keyable"], m


def test_the_leftover_of_an_earlier_key_is_read_as_a_fringe():
    frames = [np.clip(f, 0, 255).astype(np.uint8)
              for f in _keyed_before_frames(n=6)]
    m = ck.measure_frames(frames)
    assert not m["keyable"]
    fr = m["fringe"]
    assert fr and fr["screen"] == "green", m
    assert fr["lines"] >= ck.FRINGE_LINES
    assert ck.FRINGE_EXCESS_MIN <= fr["excess"] <= ck.FRINGE_EXCESS_MAX


def test_a_sky_area_is_not_read_as_a_fringe():
    f = np.zeros((120, 120, 3), np.uint8)
    f[:] = (150, 120, 110)
    f[:50] = (60, 90, 170)                    # a broad blue area, not lines
    assert ck.measure_fringe([f, f]) is None


def test_key_item_validates_inside_effects():
    edl = schemas.default_edl(4.0)
    edl["effects"] = {"key": {"id": "key1", "colour": "#1FB84A",
                              "similarity": 0.1, "background": {
                                  "kind": "gradient", "colour": "#102040",
                                  "colour2": "#000000"}}}
    out = schemas.validate_edl(edl, 4.0).model_dump()
    assert out["effects"]["key"]["colour"] == "#1fb84a"
    assert out["effects"]["key"]["mode"] == "key"
    assert out["effects"]["key"]["background"]["kind"] == "gradient"
    edl["effects"]["key"]["background"] = {"kind": "solid", "colour": "green"}
    with pytest.raises(Exception):
        schemas.validate_edl(edl, 4.0)
    edl["effects"] = {"key": {"id": "key1", "mode": "fringe",
                              "spill": "green", "excess": 23.5}}
    out = schemas.validate_edl(edl, 4.0).model_dump()
    assert out["effects"]["key"]["background"] is None
    edl["effects"] = {"key": {"id": "key1", "colour": "#1fb84a"}}
    with pytest.raises(Exception):               # a key needs a background
        schemas.validate_edl(edl, 4.0)


def test_a_background_that_cannot_be_read_keys_onto_the_gradient(tmp_path):
    edl = {"effects": {"key": {"id": "key1", "colour": "#1fb84a",
                               "background": {"kind": "asset",
                                              "asset_key": "u/1/bg.png"}}}}
    calls = []

    def fetch(k):
        calls.append(k)
        raise OSError("storage 503")
    args, item = ck.input_args(edl, str(tmp_path), 64, 64, fetch)
    assert len(calls) == 2                       # tried again
    assert item["background"]["kind"] == "gradient"
    assert "could not be read" in item["fallback"]
    assert args and args[0] == "-i" and args[1].endswith(".png")


# ── render ──────────────────────────────────────────────────────────────────

def _render(tmp_path, src, edl_effects, dur, size, keep=None):
    from renderer import render_edl
    edl = schemas.default_edl(dur)
    edl["keep"] = keep or [[0.0, dur]]
    edl["frame"] = {"ratio": "source", "mode": "crop"}
    edl["effects"] = edl_effects
    edl = schemas.validate_edl(edl, dur).model_dump()
    idx = {"video": {"duration": dur, "width": size, "height": size,
                     "fps": 25.0}, "words": [], "sentences": [],
           "silences": []}
    out = str(tmp_path / "out.mp4")
    render_edl(edl, idx, src, out, str(tmp_path), preview=True)
    return out


@needs_ffmpeg
@pytest.mark.parametrize("bg", [
    {"kind": "solid", "colour": "#2030a0"},
    {"kind": "gradient", "colour": "#2030a0", "colour2": "#2030a0"},
])
def test_render_replaces_the_screen_and_keeps_the_subject(tmp_path, bg):
    src = _green_screen(str(tmp_path / "gs.mp4"))
    # two keep stretches: the bounded per-cluster reads must not bypass it
    out = _render(tmp_path, src, {"key": {
        "id": "key1", "colour": "#1fb84a", "similarity": 0.1, "blend": 0.06,
        "despill": 0.7, "spill": "green", "background": bg}}, 4.0, 320,
        keep=[[0.0, 1.5], [2.5, 4.0]])
    before = ck.sample_frames(src, [0.5, 2.8])
    after = ck.sample_frames(out, [0.5, 2.0])
    assert ck.screen_share(before, "green", "#1fb84a", 0.1) > 0.5
    assert ck.screen_share(after, "green", "#1fb84a", 0.1) < 0.005
    corner = _rgb_at(out, 0.5, 20, 20)
    assert corner[2] > 120 and corner[1] < 90      # the new blue background
    subject = _rgb_at(out, 0.5, 155, 180)
    assert subject[0] > 170 and subject[1] > 120   # skin stays skin


@needs_ffmpeg
def test_a_green_prop_away_from_the_screen_keeps_its_green(tmp_path):
    # Review of 26 Nov: despill ran on the whole foreground, so a green prop
    # the subject holds (an olive plant) went grey. It now runs only on the
    # band of the foreground next to the screen.
    size = 240
    src = _frames_video(str(tmp_path / "prop.mp4"),
                        _screen_frames((0, 177, 64), 0.3, size, 50,
                                       prop=(110, 140, 40)))
    reading = ck.measure_frames(ck.sample_frames(src, [0.5, 1.0], size))
    out = _render(tmp_path, src, {"key": {
        "id": "key1", "colour": reading["colour"], "similarity": 0.12,
        "blend": 0.04, "despill": 1.0, "spill": "green",
        "background": {"kind": "solid", "colour": "#2030a0"}}}, 2.0, size)
    prop = _rgb_at(out, 0.0, size // 2, int(size * .8), size)
    # despilled it would read about (110, 75, 40)
    assert abs(prop - np.array([110, 140, 40])).max() < 12, prop


@needs_ffmpeg
def test_render_cleans_the_leftover_of_an_earlier_key(tmp_path):
    size = 240
    frames = _keyed_before_frames(size, 50)
    src = _frames_video(str(tmp_path / "kb.mp4"), frames)
    fr = ck.measure_frames(ck.sample_frames(src, [0.4, 1.2], size))["fringe"]
    out = _render(tmp_path, src, {"key": {
        "id": "key1", "mode": "fringe", "spill": "green",
        "colour": fr["colour"], "excess": fr["excess"]}}, 2.0, size)
    b = ck.sample_frames(src, [0.0], size)[0]
    a = ck.sample_frames(out, [0.0], size)[0]
    tinted_b = ck._lead_mask(b, "green", ck.FRINGE_LEAD, ck.FRINGE_MIN).mean()
    tinted_a = ck._lead_mask(a, "green", ck.FRINGE_LEAD, ck.FRINGE_MIN).mean()
    assert tinted_b > 0.005 and tinted_a < tinted_b * 0.1, (tinted_b, tinted_a)
    disc = a[48:56, 210:214].reshape(-1, 3).mean(0)      # the old green disc
    assert disc[0] > disc[1] + 40, disc                  # now the red city
    far = (slice(170, 230), slice(10, 50))               # background stays
    assert np.abs(a[far].astype(int) - b[far].astype(int)).mean() < 6
    face = a[100:120, 100:120].reshape(-1, 3).mean(0)    # skin stays skin
    assert abs(face[0] - SKIN_RGB[0]) < 15 and abs(face[1] - SKIN_RGB[1]) < 15


# ── the tool ────────────────────────────────────────────────────────────────

class _Ctx:
    def __init__(self, d, src, duration=4.0):
        self.workdir = d
        self.duration = duration
        self.has_main_video = True
        self.project_id = 1
        self._src = src
        self._edl = {"version": 3, "json": schemas.default_edl(duration)}
        self.written = None

    def proxy_path(self):
        return self._src

    def latest_edl(self):
        return self._edl

    def write_edl(self, edl, desc):
        self.written = schemas.validate_edl(edl, self.duration).model_dump()
        self._edl = {"version": self._edl["version"] + 1,
                     "json": self.written}
        return f"EDL v3 -> v4: {desc}"


def _nums(out):
    m = re.search(r"after the key ([0-9.]+)% of it still shows and "
                  r"([0-9.]+)% of the subject", out)
    assert m, out
    return float(m.group(1)), float(m.group(2))


@needs_ffmpeg
def test_tool_writes_a_measured_key_and_reports_before_after(tmp_path):
    from tools_ext import key_tools
    src = _green_screen(str(tmp_path / "gs.mp4"))
    ctx = _Ctx(str(tmp_path), src)
    out = key_tools.chroma_key(ctx, background="solid", colour="#2030a0")
    assert out.startswith("EDL v"), out
    key = ctx.written["effects"]["key"]
    assert key["mode"] == "key" and key["spill"] == "green"
    assert key["background"] == {"kind": "solid", "colour": "#2030a0",
                                 "colour2": None, "asset_key": None}
    left, lost = _nums(out)
    assert left < 0.5 and lost < 1.0, out
    off = key_tools.chroma_key(ctx, remove=True)
    assert off.startswith("EDL v")
    assert not (ctx.written.get("effects") or {}).get("key")


@needs_ffmpeg
def test_a_dim_vignetted_screen_is_keyed_with_the_props(tmp_path):
    from tools_ext import key_tools
    src = _frames_video(str(tmp_path / "dim.mp4"),
                        _screen_frames((60, 140, 70), 0.85, 240, 50))
    ctx = _Ctx(str(tmp_path), src, duration=2.0)
    out = key_tools.chroma_key(ctx, background="solid", colour="#2030a0")
    key = ctx.written["effects"]["key"]
    assert key["mode"] == "key", out
    left, lost = _nums(out)
    assert left <= key_tools.RESIDUAL_OK * 100 and lost <= 1.0, out


@needs_ffmpeg
def test_the_dry_run_sees_the_screen_a_narrow_key_leaves(tmp_path):
    # Review of 26 Nov: the old "after" counted only pixels inside the
    # key's own similarity, so it read ~0 for any key. The transparency
    # measured over black and white sees the dark corners a narrow key
    # leaves, and the tool widens the key until they go.
    from tools_ext import key_tools
    src = _frames_video(str(tmp_path / "vig.mp4"),
                        _screen_frames((0, 177, 64), 0.85, 240, 50))
    times = [0.3, 0.9, 1.5]
    reading = ck.measure_frames(ck.sample_frames(src, times, 240))
    narrow = dict(id="k", mode="key", colour=reading["colour"],
                  similarity=0.03, blend=0.01, despill=0.6, spill="green",
                  background={"kind": "solid", "colour": "#000000"})
    assert ck.dry_run(src, narrow, times)["left"] > 0.02
    ctx = _Ctx(str(tmp_path), src, duration=2.0)
    out = key_tools.chroma_key(ctx, similarity=None)
    left, lost = _nums(out)
    assert left <= key_tools.RESIDUAL_OK * 100 and lost <= 1.0, out


@needs_ffmpeg
def test_a_key_too_wide_for_the_subject_is_narrowed(tmp_path):
    from tools_ext import key_tools
    src = _frames_video(str(tmp_path / "wide.mp4"),
                        _screen_frames((0, 177, 64), 0.3, 240, 50))
    ctx = _Ctx(str(tmp_path), src, duration=2.0)
    out = key_tools.chroma_key(ctx, similarity=0.35)
    assert "narrowed" in out, out
    left, lost = _nums(out)
    assert lost <= key_tools.SUBJECT_LOSS_OK * 100, out
    assert ctx.written["effects"]["key"]["similarity"] < 0.35


@needs_ffmpeg
def test_footage_keyed_before_gets_its_leftover_cleaned(tmp_path,
                                                        monkeypatch):
    # p6311 itself: no screen left, a green line and disc. The props and
    # the footage's own background stay; nothing goes behind a cut-out.
    from tools_ext import key_tools, subject_tools
    calls = []
    monkeypatch.setattr(subject_tools, "set_background",
                        lambda *a, **k: calls.append(a) or "EDL v3 -> v4")
    src = _frames_video(str(tmp_path / "kb.mp4"), _keyed_before_frames())
    ctx = _Ctx(str(tmp_path), src, duration=2.0)
    out = key_tools.chroma_key(ctx)
    key = ctx.written["effects"]["key"]
    assert key["mode"] == "fringe" and key["spill"] == "green", out
    assert not calls
    m = re.search(r"([0-9.]+)% of the picture read green-tinted round the "
                  r"edges before, ([0-9.]+)% after", out)
    assert m and float(m.group(2)) < float(m.group(1)) * 0.2, out
    assert "keyed before" in out


@needs_ffmpeg
def test_a_named_background_on_keyed_footage_reports_a_failed_cutout(
        tmp_path, monkeypatch):
    # Review of 26 Nov: a REJECTED from set_background was followed by "the
    # new background is laid", so the agent told the user it was done.
    from tools_ext import key_tools, subject_tools
    monkeypatch.setattr(
        subject_tools, "set_background",
        lambda *a, **k: "REJECTED: the render service does not draw it")
    src = _frames_video(str(tmp_path / "kb.mp4"), _keyed_before_frames())
    ctx = _Ctx(str(tmp_path), src, duration=2.0)
    out = key_tools.chroma_key(ctx, background="solid", colour="#102040")
    assert "could not be laid" in out and "REJECTED" in out
    assert "is laid behind the person cut-out" not in out
    assert ctx.written["effects"]["key"]["mode"] == "fringe"


@needs_ffmpeg
def test_plain_footage_with_a_named_background_uses_the_cutout(
        tmp_path, monkeypatch):
    from tools_ext import key_tools, subject_tools
    src = str(tmp_path / "plain.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "color=c=0x806040:s=320x320:d=3:r=25",
         "-vf", "drawbox=x=0:y=0:w=320:h=110:color=0x5a4a3a@1:t=fill",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", src], check=True)
    calls = []

    def fake(ctx, kind, start, end, **kw):
        calls.append((kind, start, end, kw))
        return "EDL v3 -> v4: set the background"
    monkeypatch.setattr(subject_tools, "set_background", fake)
    ctx = _Ctx(str(tmp_path), src, duration=3.0)
    out = key_tools.chroma_key(ctx, background="gradient", colour="#102040",
                               colour2="#000000")
    assert calls and calls[0][0] == "gradient"
    assert calls[0][1] == 0.0 and calls[0][2] == pytest.approx(3.0, abs=0.05)
    assert "person cut-out" in out
    plain = _Ctx(str(tmp_path), src, duration=3.0)
    assert key_tools.chroma_key(plain).startswith("NO CHANGE")
