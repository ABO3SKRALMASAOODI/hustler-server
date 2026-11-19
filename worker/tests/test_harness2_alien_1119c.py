"""Harness v2 round three, 19 Nov 2026: the rater's v2b-1119 losses.

p5873: "warm, sun-touched" measured less warm than the upload (whites and
mid-tones unmoved by temperature 0.4, a saturation cut taking warmth out).
grade_custom.temperature leans only the darkest third under ffmpeg's
colorbalance; v2's temperature is now grade_custom.warmth, the whole
picture's white balance.

p5744: "Calm current" and "Bubbles" are the library's titles; v2 heard a
calmer look and floating bubbles. The turn's project state names the library
tracks the request's words are the title of (the catalogue's own search).

p5760: the joined film of ten clips stayed 16:9 ({"frame": {"ratio":
"9:16"}} went to set_frame as text) and had no captions.
"""
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
if WORKER not in sys.path:
    sys.path.insert(0, WORKER)

import agent_tools  # noqa: E402
import music_choose  # noqa: E402
import renderer  # noqa: E402
from harness2 import commit, find_tool, loop, ops as ops_mod  # noqa: E402
from w0c_support import tool_context  # noqa: E402

GOLDEN_OTHER_ASKS = (
    "make it more elevated. rich textures and subtle grain. colors should "
    "feel warm, sun-touched and slightly nostalgic., with creamy neutrals, "
    "earthy tones and restrained moments of intensity. the finish should "
    "subtly evoke film rather than digital perfection.",
    "edit the transitions to make it look more elevated",
    "Watch the entire broadcast. Find the 10 funniest and most unexpected "
    "moments: mistakes, failures, and intense reactions. Divide them into "
    "vertical clips lasting 30-60 seconds. Come up with an appealing title "
    "for each clip so you can use it in short films.",
    "6 sec ke baad slow motion create karo add music",
    "Background noise ko mute kar do.",
)


@pytest.fixture(autouse=True)
def _clean():
    commit._RECEIPTS.clear()
    music_choose._CACHE.clear()
    yield
    commit._RECEIPTS.clear()
    music_choose._CACHE.clear()


def _ctx(duration, keep):
    ctx, fake = tool_context({"keep": keep}, duration=duration)
    ctx.cut_sound_source = lambda: None
    return ctx, fake


def _grey(chain, value):
    """(r, g, b) of a flat grey patch through ``chain``."""
    hexv = f"{value:02x}" * 3
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
         f"color=c=0x{hexv}:s=16x16", "-frames:v", "1", "-vf",
         f"format=yuv444p,{chain},format=rgb24", "-f", "rawvideo", "-"],
        capture_output=True, timeout=30).stdout
    return tuple(out[:3])


# -- p5873: warmth is the whole picture's white balance -------------------------

@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg")
def test_warmth_warms_mid_grey_and_whites_and_keeps_black():
    chain = renderer.grade_custom_chain({"warmth": 0.6})
    for v in (128, 200):
        r, g, b = _grey(chain, v)
        assert r >= g > b and r - b >= 6, (v, r, g, b)
    r, g, b = _grey(chain, 0)
    assert max(r, g, b) <= 2
    cool = renderer.grade_custom_chain({"warmth": -0.6})
    r, g, b = _grey(cool, 160)
    assert b > r


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg")
def test_temperature_alone_leaves_mid_grey_where_it_was():
    """Why warmth exists: the shadow lean the Director's look relies on."""
    r, g, b = _grey(renderer.grade_custom_chain({"temperature": 1.0}), 160)
    assert abs(r - b) <= 2


def test_neutral_warmth_adds_nothing():
    assert renderer.warmth_curves(0) == ""
    assert renderer.warmth_curves(None) == ""
    assert renderer.grade_custom_chain({"warmth": 0.0}) == ""


def test_set_grade_custom_stores_warmth():
    ctx, _fake = _ctx(20.0, [[0.0, 20.0]])
    out = agent_tools.set_grade_custom(ctx, warmth=0.5, saturation=-0.05)
    assert out.startswith("EDL v")
    gc = ctx.latest_edl()["json"]["effects"]["grade_custom"]
    assert gc["warmth"] == pytest.approx(0.5)
    assert gc["saturation"] == pytest.approx(0.95)


def test_grade_temperature_lands_as_warmth():
    ctx, _fake = _ctx(20.0, [[0.0, 20.0]])
    env = commit.edit(ctx, [{"grade": {"custom": {"temperature": 0.4,
                                                  "exposure": 0.1}}}], "warm")
    gc = ctx.latest_edl()["json"]["effects"]["grade_custom"]
    assert gc["warmth"] == pytest.approx(0.4)
    assert not gc.get("temperature")
    assert any("written as warmth" in r for r in env.repairs)


def test_the_tool_door_reads_temperature_the_same_way():
    ctx, _fake = _ctx(20.0, [[0.0, 20.0]])
    commit.edit(ctx, [{"tool": "set_grade_custom",
                       "args": {"temperature": 0.3}}], "warm")
    gc = ctx.latest_edl()["json"]["effects"]["grade_custom"]
    assert gc["warmth"] == pytest.approx(0.3)


def test_a_look_given_as_controls_is_those_controls(monkeypatch):
    """p5744 v2b: {"grade": {"look": {...}}} raised inside apply_look."""
    ctx, _fake = _ctx(20.0, [[0.0, 20.0]])
    seen = []
    real = agent_tools.execute

    def execute(c, name, args):
        seen.append(name)
        return real(c, name, args)
    monkeypatch.setattr(agent_tools, "execute", execute)
    env = commit.edit(ctx, [{"grade": {"look": {"contrast": 1.01,
                                                "saturation": 1.0}}}], "calm")
    assert "apply_look" not in seen and "set_grade_custom" in seen
    assert not env.engine_errors


def test_the_grade_op_and_kernel_say_temperature_warms_the_picture():
    from harness2 import schema_ops
    assert "whole picture's white" in schema_ops.describe("ops")
    assert "whole picture's white balance" in loop.kernel_text()


# -- p5744: a request that is a library title ------------------------------------

def test_the_library_titles_the_request_names():
    calm = find_tool.named_tracks("Calm current")
    assert calm and calm[0]["id"] == "cc0:chill-calm-currents-lofi-relax-calm"
    bub = find_tool.named_tracks("Bubbles")
    assert bub and bub[0]["id"] == "cc0:chill-bubbles-lofi-bright-relaxed"


def test_other_golden_asks_name_no_track():
    for ask in GOLDEN_OTHER_ASKS:
        assert find_tool.named_tracks(ask) == [], ask


def test_the_turn_state_carries_the_named_track():
    ctx, _fake = _ctx(11.26, [[0.0, 11.26]])

    class NoDb:
        def run(self, *a, **k):
            raise RuntimeError("no db")
    msgs = loop.initial_messages(ctx, NoDb(), 11,
                                 {"id": 5, "content": "Calm current"})
    last = msgs[-1]["content"]
    assert "cc0:chill-calm-currents-lofi-relax-calm" in last
    assert last.rstrip().endswith("Calm current")
    plain = loop.initial_messages(ctx, NoDb(), 11,
                                  {"id": 6, "content": GOLDEN_OTHER_ASKS[0]})
    assert "title of a library track" not in plain[-1]["content"]


# -- p5760: the joined film is vertical and captioned ------------------------------

def test_a_frame_given_as_an_object_is_its_ratio():
    assert ops_mod._clip_ratio({"ratio": "9:16"}) == "9:16"
    assert ops_mod._clip_ratio("source") == "source"
    assert ops_mod._clip_ratio(None) == "9:16"
    assert ops_mod._clip_ratio("{'ratio': '9:16'}") == "9:16"


def test_the_joined_clips_are_framed_and_captioned(monkeypatch):
    ctx, _fake = _ctx(600.0, [[0.0, 600.0]])
    ctx.index["words"] = [{"w": "hi", "t0": 12.0, "t1": 12.4}]
    monkeypatch.setattr(ops_mod.Engines, "make_shorts",
                        staticmethod(lambda c, clips, style_note=None,
                                     deliver=None: "Started job 9."))
    calls = []
    real = agent_tools.execute

    def execute(c, name, args):
        calls.append((name, args))
        if name in ("add_text", "add_captions"):
            return "NO CHANGE while staging"
        return real(c, name, args)
    monkeypatch.setattr(agent_tools, "execute", execute)
    env = commit.edit(ctx, [{"clips": {"frame": {"ratio": "9:16"}, "items": [
        {"range": [10, 50], "title": "One"},
        {"range": [100, 140], "title": "Two"}]}}], "clips")
    assert ctx.latest_edl()["json"]["frame"]["ratio"] == "9:16"
    assert not any(n == "set_frame" for n, _a in calls)
    caps = [a for n, a in calls if n == "add_captions"]
    assert caps and caps[0]["mode"] == "from_transcript"
    assert any("framed 9:16" in f for f in env.findings)


def test_junctions_are_listed_at_their_cuts():
    """p5873 v2c: nine fade_black junctions read "junction x9 (0.0-0.0 s)"
    and the model told the person the dips may not have applied."""
    from harness2 import measure
    edl = {"keep": [[0.0, 35.27]], "fx": [
        {"type": "junction", "at": 3.2, "duration_s": 0.4,
         "style": "fade_black"},
        {"type": "junction", "at": 6.2, "duration_s": 0.4,
         "style": "fade_black"}]}
    line = next(x for x in measure.on_screen(edl) if x.startswith("devices"))
    assert "at the cuts 3.2, 6.2 s" in line and "0.0-0.0" not in line


# -- round four (v2c-1119) --------------------------------------------------------

def test_gain_keeps_black_and_rolls_off_white():
    chain = renderer.gain_curves(0.8)
    assert chain.startswith("curves=master='0.0000/0.0000")
    pts = [tuple(map(float, p.split("/"))) for p in
           chain.split("'")[1].split()]
    ys = [y for _x, y in pts]
    assert ys == sorted(ys) and ys[-1] <= 1.0 and ys[8] > pts[8][0]
    assert renderer.gain_curves(0) == ""


def test_grade_exposure_lands_as_gain():
    ctx, _fake = _ctx(20.0, [[0.0, 20.0]])
    env = commit.edit(ctx, [{"grade": {"custom": {"exposure": 0.3}}}], "light")
    gc = ctx.latest_edl()["json"]["effects"]["grade_custom"]
    assert gc["gain"] == pytest.approx(0.3) and not gc.get("exposure")
    assert any("written as gain" in r for r in env.repairs)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg")
def test_gain_lifts_mid_grey_and_leaves_black():
    chain = renderer.grade_custom_chain({"gain": 0.8})
    assert max(_grey(chain, 0)) <= 2
    assert _grey(chain, 100)[0] > 125


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg")
def test_a_vignette_darkens_the_edges_not_the_film():
    """0.25 took a third of a 9:16 picture's light (p5744 v2c L 50 -> 31)."""
    import numpy as np
    vf = renderer.vignette_filter(0.25, 1080 / 1920)
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
         "color=c=0xb4b4b4:s=270x480", "-frames:v", "1", "-vf",
         f"format=yuv420p,{vf},format=gray", "-f", "rawvideo", "-"],
        capture_output=True, timeout=30).stdout
    x = np.frombuffer(raw, np.uint8).reshape(480, 270) / 180.0
    assert x.mean() > 0.9 and x[240, 135] > 0.98 and x[2, 2] < 0.9


def test_stills_look_at_the_render(monkeypatch):
    from harness2 import look, render_tool
    seen = {}

    def fake_look(ctx, **kw):
        seen.update(kw)
        from harness2 import envelope
        return envelope.Envelope(tool="look")
    monkeypatch.setattr(look, "look", fake_look)
    render_tool.render(object(), output="stills", times=[3.1])
    assert seen["target"] == "render" and seen["times"] == [3.1]


def test_the_first_cuts_leftovers_are_named():
    from harness2 import measure
    first = {"keep": [[0, 11]], "frame": {"ratio": "9:16", "mode": "crop"},
             "overlays": [{"id": "ov.b01.s0"}, {"id": "ov.b02.s0"}],
             "texts": [{"id": "tx.b01.big.0"}]}
    now = {"keep": [[0, 11]], "frame": {"ratio": "9:16", "mode": "crop"},
           "overlays": [{"id": "ov.b01.s0"}], "texts": [{"id": "tx9"}]}
    lines = measure.first_cut_left(now, first)
    assert any("ov.b01.s0" in x for x in lines)
    assert not any("tx.b01" in x for x in lines)
    assert any(x.startswith("frame: 9:16 crop") for x in lines)
    assert measure.first_cut_left(now, {}) == []


def test_frame_source_gives_the_uploads_frame_back():
    ctx, _fake = _ctx(20.0, [[0.0, 20.0]])
    commit.edit(ctx, [{"frame": {"ratio": "9:16", "mode": "crop"}}], "v")
    commit.edit(ctx, [{"frame": {"ratio": "source"}}], "src")
    fr = ctx.latest_edl()["json"].get("frame") or {}
    assert fr.get("ratio") in (None, "source")


# -- round five (v2d-1119) ---------------------------------------------------------

def test_a_measured_duck_dips_smoothly_over_the_words():
    vf = renderer.measured_duck([(1.3, 2.0), (5.0, 6.0)], -27.0)
    assert vf.startswith("volume=volume='pow(10,(-27.00*(max(")
    assert vf.endswith(":eval=frame")


def test_the_bed_is_seated_under_the_uploads_words(monkeypatch, tmp_path):
    """p5744 v2d: the bed sat 25-27 dB over the upload's one "Yeah"."""
    import noise_clean
    import storage
    ctx, _fake = _ctx(11.26, [[0.0, 11.26]])
    ctx.index["words"] = [{"w": "Yeah", "t0": 1.3, "t1": 2.0}]
    ctx.proxy_path = lambda: str(tmp_path / "proxy.mp4")
    ctx.workdir = str(tmp_path)
    monkeypatch.setattr(storage, "download_to",
                        lambda key, local: open(local, "w").close())
    monkeypatch.setattr(noise_clean, "level_db",
                        lambda path, a=None, b=None:
                        -40.0 if "proxy" in path else -14.0)
    from harness2 import stage as stage_mod
    st = stage_mod.Stage(ctx, {"keep": [[0.0, 11.26]], "music": [
        {"id": "mus1", "storage_key": "music/2/library-bubbles.mp3",
         "start": 0.0, "end": 11.26, "gain_db": -16.0, "duck": True,
         "duck_mode": "smooth"}]}, 1)
    from harness2 import envelope
    env = envelope.Envelope(tool="edit")
    ops_mod._seat_bed(st, env)
    m = st.edl["music"][0]
    # bed -30 against words at -40: dip 25 so the words sit 15 dB over it
    assert m["duck_db"] == pytest.approx(-25.0) and m["duck_mode"] is None
    assert any("seated under the upload's own words" in f
               for f in env.findings)


def test_a_grade_look_is_colour_only(monkeypatch):
    ctx, _fake = _ctx(20.0, [[0.0, 20.0]])
    seen = []
    real = agent_tools.execute

    def execute(c, name, args):
        seen.append(name)
        return real(c, name, args)
    monkeypatch.setattr(agent_tools, "execute", execute)
    commit.edit(ctx, [{"grade": {"look": "cinematic_doc"}}], "chill")
    assert "apply_look" not in seen and "set_color_grade" in seen
    edl = ctx.latest_edl()["json"]
    assert not edl.get("captions")
    assert (edl.get("effects") or {}).get("grade") == "cinematic"


def test_slow_motion_to_the_end_eases_in_and_holds():
    import ramps
    shape = ramps.curve_fn([(0.0, 1.0), (0.15, 0.5), (1.0, 0.5)])
    assert shape(1.0) == pytest.approx(0.5) and shape(0.0) == pytest.approx(1.0)
    import inspect
    from tools_ext import montage_tools
    assert "ease_out" in inspect.signature(montage_tools.set_speed_ramp).parameters


def test_request_reading_has_the_sound_layers():
    from score.asks import sound_layers
    lines = sound_layers({"music": [{"id": "mus1", "storage_key":
                                     "music/2/library-bubbles.mp3",
                                     "gain_db": -6,
                                     "provenance": "user_picked_library"}]},
                         {"words": [{"w": "Yeah", "t0": 1.3, "t1": 2.0}]})
    assert "room tone and background noise" in lines[0]
    assert "the person picked this track" in lines[1]
    assert "not the recording's background noise" in lines[1]
