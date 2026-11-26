"""p6311 (26 Nov 2026): a customer asked to key the green screen around the
presenter, the candles and the props and composite a new background. The
product had no key at all; the agent said "no green found" and the ask
ended not_done. These tests hold the capability that now exists:

* the backdrop is measured on the footage (colour, share, key width), and a
  night sky or foliage in an already-composited picture is not a screen;
* effects.key validates and renders: the screen is gone from the exported
  pixels, the subject keeps its colour, the background shows where the
  screen was (solid, gradient), on the source clock with several keep
  stretches;
* the chroma_key tool writes the measured key and reports the screen share
  before and after its own dry run; footage with no screen still gets the
  new background, behind the person cut-out.
"""
import os
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


def _rgb_at(path, t, x, y, size=320):
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", path, "-frames:v", "1",
         "-vf", f"scale={size}:{size},format=rgb24", "-f", "rawvideo", "-"],
        capture_output=True, check=True)
    a = np.frombuffer(r.stdout, np.uint8).reshape(size, size, 3)
    return a[y - 3:y + 4, x - 3:x + 4].reshape(-1, 3).mean(0)


def test_measure_reads_a_green_screen():
    f = np.zeros((100, 100, 3), np.uint8)
    f[:] = (31, 184, 74)
    f[30:80, 35:65] = (217, 160, 122)
    m = ck.measure_frames([f, f])
    assert m["screen"] == "green" and m["keyable"]
    assert 0.6 < m["share"] < 0.9
    key = [int(m["colour"][i:i + 2], 16) for i in (1, 3, 5)]
    assert abs(key[1] - 184) <= 3


def test_measure_does_not_take_a_night_sky_for_a_blue_screen():
    # p6311's frames: an already-keyed presenter over city photographs; the
    # night sky measured as "blue" on 9% of the picture at chroma 0.106.
    f = np.zeros((100, 100, 3), np.uint8)
    f[:] = (120, 110, 100)
    f[:40] = (30, 58, 126)
    m = ck.measure_frames([f])
    assert not m["keyable"]
    assert m["chroma"] < ck.SCREEN_MIN_CHROMA


def test_key_item_validates_inside_effects():
    edl = schemas.default_edl(4.0)
    edl["effects"] = {"key": {"id": "key1", "colour": "#1FB84A",
                              "similarity": 0.1, "background": {
                                  "kind": "gradient", "colour": "#102040",
                                  "colour2": "#000000"}}}
    out = schemas.validate_edl(edl, 4.0).model_dump()
    assert out["effects"]["key"]["colour"] == "#1fb84a"
    assert out["effects"]["key"]["background"]["kind"] == "gradient"
    edl["effects"]["key"]["background"] = {"kind": "solid", "colour": "green"}
    with pytest.raises(Exception):
        schemas.validate_edl(edl, 4.0)


@needs_ffmpeg
@pytest.mark.parametrize("bg", [
    {"kind": "solid", "colour": "#2030a0"},
    {"kind": "gradient", "colour": "#2030a0", "colour2": "#2030a0"},
])
def test_render_replaces_the_screen_and_keeps_the_subject(tmp_path, bg):
    from renderer import render_edl
    src = _green_screen(str(tmp_path / "gs.mp4"))
    edl = schemas.default_edl(4.0)
    # two keep stretches: the bounded per-cluster reads must not bypass it
    edl["keep"] = [[0.0, 1.5], [2.5, 4.0]]
    edl["frame"] = {"ratio": "source", "mode": "crop"}
    edl["effects"] = {"key": {"id": "key1", "colour": "#1fb84a",
                              "similarity": 0.1, "blend": 0.06,
                              "despill": 0.7, "spill": "green",
                              "background": bg}}
    edl = schemas.validate_edl(edl, 4.0).model_dump()
    idx = {"video": {"duration": 4.0, "width": 320, "height": 320,
                     "fps": 25.0}, "words": [], "sentences": [],
           "silences": []}
    out = str(tmp_path / "out.mp4")
    render_edl(edl, idx, src, out, str(tmp_path), preview=True)
    before = ck.sample_frames(src, [0.5, 2.8])
    after = ck.sample_frames(out, [0.5, 2.0])
    share_before = ck.screen_share(before, "green", "#1fb84a", 0.1)
    share_after = ck.screen_share(after, "green", "#1fb84a", 0.1)
    assert share_before > 0.5
    assert share_after < 0.005
    corner = _rgb_at(out, 0.5, 20, 20)
    assert corner[2] > 120 and corner[1] < 90      # the new blue background
    subject = _rgb_at(out, 0.5, 155, 180)
    assert subject[0] > 170 and subject[1] > 120   # skin stays skin


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


@needs_ffmpeg
def test_tool_writes_a_measured_key_and_reports_before_after(tmp_path):
    from tools_ext import key_tools
    src = _green_screen(str(tmp_path / "gs.mp4"))
    ctx = _Ctx(str(tmp_path), src)
    out = key_tools.chroma_key(ctx, background="solid", colour="#2030a0")
    assert out.startswith("EDL v"), out
    key = ctx.written["effects"]["key"]
    assert key["spill"] == "green"
    assert key["background"] == {"kind": "solid", "colour": "#2030a0",
                                 "colour2": None, "asset_key": None}
    nums = [float(x) for x in
            __import__("re").findall(r"([0-9.]+)% (?:of the picture before|after)", out)]
    assert len(nums) == 2 and nums[0] > 50 and nums[1] < 0.5, out
    off = key_tools.chroma_key(ctx, remove=True)
    assert off.startswith("EDL v")
    assert not (ctx.written.get("effects") or {}).get("key")


@needs_ffmpeg
def test_tool_without_a_screen_lays_the_background_behind_the_person(
        tmp_path, monkeypatch):
    from tools_ext import key_tools, subject_tools
    src = str(tmp_path / "plain.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "color=c=0x806040:s=320x320:d=3:r=25",
         "-vf", "drawbox=x=0:y=0:w=320:h=110:color=0x1e3a7e@1:t=fill",
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
    assert "no green or blue screen to key" in out
    assert "person cut-out" in out
