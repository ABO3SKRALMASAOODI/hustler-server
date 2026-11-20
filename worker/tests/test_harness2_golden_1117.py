"""Harness v2 golden replay egv2b-1117: the four root fixes.

p5873: a graded 10-bit HEVC upload exported 100% black (8-bit grade tables
read on 10-bit frames); scene transitions on one continuous keep were
refused and reported as "already so"; look(target='render') showed source
frames while the render ran and the model called a rendered dip missing.
p5744: speed rows are film seconds and the envelope measures where they
play; a find(kind='footage') link is placeable; a named music genre is
searched, and the truth is said when nothing of reference grade matches.
"""
import json
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
import gradelut  # noqa: E402
import renderer  # noqa: E402
from harness2 import (commit, envelope, find_tool, look, measure,  # noqa: E402
                      ops as ops_mod, render_tool)
from w0c_support import tool_context  # noqa: E402

HAVE_FFMPEG = bool(shutil.which("ffmpeg"))
SHOTS = [0.0, 3.2, 6.2, 8.63, 11.83, 15.46, 18.73, 21.76, 24.03, 27.0,
         31.36]


@pytest.fixture(autouse=True)
def _clean():
    commit._RECEIPTS.clear()
    yield
    commit._RECEIPTS.clear()


def _ctx(duration, keep):
    ctx, fake = tool_context({"keep": keep}, duration=duration)
    ctx.cut_sound_source = lambda: None
    return ctx, fake


# -- p5873: the black export --------------------------------------------------

def test_grade_tables_read_every_depth_on_their_8bit_scale():
    """Each baked table reads its value on the 8-bit scale it was baked on
    (step 2^(depth-8) from maxval), so no format filter is needed and an
    8-bit graph negotiates as the real filters do."""
    chain = renderer.grade_custom_chain({"temperature": 0.35, "tint": 0.05,
                                         "contrast": 0.92, "exposure": 0.12,
                                         "saturation": 0.9, "shadows": 0.16,
                                         "highlights": -0.1})
    fast = gradelut.fast_chain(chain)
    if fast == chain:
        pytest.skip("no ffmpeg to bake the tables")
    assert "format=" not in fast
    assert fast.count("st(1,pow(2,round(log(maxval/200)/log(2))))") >= 1
    assert "lt(val," not in fast
    # the step: 1 on 8-bit maxvals, 4 on 10-bit, 16 on 12-bit
    import math
    for mx, step in ((235, 1), (240, 1), (255, 1), (940, 4), (960, 4),
                     (1023, 4), (4095, 16)):
        assert 2 ** round(math.log(mx / 200) / math.log(2)) == step


@pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg")
def test_a_graded_10bit_frame_keeps_its_light(tmp_path):
    """The p5873 chain on a 10-bit frame: the fast tables must land where
    the real filters land, not near black (luma max was 23)."""
    chain = renderer.GRADE_FILTERS["warm"] + "," + \
        renderer.grade_custom_chain({"temperature": 0.35, "exposure": 0.12,
                                     "contrast": 0.92, "shadows": 0.16})
    fast = gradelut.fast_chain(chain)
    assert fast != chain

    def luma(vf):
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-f", "lavfi", "-i",
             "testsrc2=s=320x240:d=0.1,format=yuv420p10le",
             "-vf", f"{vf},signalstats,metadata=print", "-frames:v", "1",
             "-f", "null", "-"], capture_output=True, text=True, timeout=60)
        line = next(x for x in out.stderr.splitlines() if "YAVG=" in x)
        return float(line.split("YAVG=")[1])
    # both read on the 8-bit scale (eq converts to 8-bit; the tables keep
    # the frame's 10 bits)
    slow, quick = luma(chain + ",format=yuv420p"), luma(fast + ",format=yuv420p")
    assert quick > 60
    assert abs(quick - slow) < 6, (slow, quick)


# -- p5873: transitions at the shots of one continuous keep --------------------

def _shot_ctx():
    ctx, fake = _ctx(35.27, [[0.0, 35.27]])
    ctx.index["shots"] = [{"id": i + 1, "start": a,
                           "end": (SHOTS + [35.27])[i + 1]}
                          for i, a in enumerate(SHOTS)]
    return ctx, fake


def test_scene_transitions_land_on_every_shot_of_one_keep():
    ctx, _fake = _shot_ctx()
    env = commit.edit(ctx, [{"tool": "set_transitions", "args": {
        "style": "dip_black", "duration_s": 0.4, "scope": "scene"}}], "t")
    assert env.status in (envelope.DONE, envelope.DONE_WITH_REPAIRS), \
        env.text()
    edl = ctx.latest_edl()["json"]
    assert len(edl["keep"]) == 11
    assert edl["effects"]["transition"]["style"] == "dip_black"
    assert not env.changed.startswith("already so")


def test_a_verb_that_says_not_applied_is_never_already_so(monkeypatch):
    ctx, _fake = _ctx(20.0, [[0.0, 20.0]])
    monkeypatch.setattr(agent_tools, "execute",
                        lambda c, n, a: "NOT APPLIED: the EDL was NOT "
                                        "changed for a reason")
    env = commit.edit(ctx, [{"tool": "set_transitions",
                             "args": {"style": "flash"}}], "t")
    assert env.not_applied and "NOT APPLIED" in env.text()
    assert "already so" not in env.text()


# -- p5873: look at the render while it renders -------------------------------

def test_look_render_waits_for_the_render_and_reads_it(monkeypatch):
    ctx, _fake = _ctx(20.0, [[0.0, 20.0]])
    head = ctx.latest_edl()["version"]
    rows = iter([None, {"storage_key": "renders/7/v.mp4"}])
    monkeypatch.setattr(look, "_render_row", lambda c, v: next(rows))
    monkeypatch.setattr(render_tool, "start_preview",
                        lambda c, quality="draft": {
                            "handle": "job:5", "job_id": 5,
                            "version": head})
    waited = []
    monkeypatch.setattr(render_tool, "settle",
                        lambda c, j, v, s: waited.append(s) or
                        envelope.Envelope(tool="render"))
    read = []
    monkeypatch.setattr(look, "_frames_asset",
                        lambda c, k, t: read.append(k) or ["rendered"])
    env = look.look(ctx, target="render", show="frames", times=[3.2])
    assert waited and read == ["renders/7/v.mp4"]
    assert f"rendered v{head}" in env.text()


def test_look_render_still_running_says_what_the_frames_lack(monkeypatch):
    ctx, _fake = _ctx(20.0, [[0.0, 20.0]])
    head = ctx.latest_edl()["version"]
    monkeypatch.setattr(look, "_render_row", lambda c, v: None)
    monkeypatch.setattr(render_tool, "start_preview",
                        lambda c, quality="draft": {
                            "handle": "job:6", "job_id": 6,
                            "version": head})
    busy = envelope.Envelope(tool="render")
    busy.running = {"handle": "job:6", "what": "preview"}
    monkeypatch.setattr(render_tool, "settle", lambda c, j, v, s: busy)
    monkeypatch.setattr(agent_tools, "_look_at_output",
                        lambda c, t, q, overlay=None: "program frames")
    env = look.look(ctx, target="render", show="frames", times=[3.2])
    assert env.status == envelope.WORKING
    assert "NOT the render" in env.text()
    assert "transitions" in env.text()


# -- p5744: speed on the film clock -------------------------------------------

P5744_KEEP = [[0.0, 4.75], [9.25, 11.26]]


def test_speed_after_6s_of_the_film_plays_from_6s_of_the_film():
    ctx, _fake = _ctx(11.26, P5744_KEEP)
    env = commit.edit(ctx, [{"speed": [{"start": 6.0, "end": 6.76,
                                        "factor": 0.6}]}], "slow mo")
    edl = ctx.latest_edl()["json"]
    sp = edl["speed"][0]
    assert sp["start"] == pytest.approx(10.5, abs=0.02)
    assert sp["end"] == pytest.approx(11.26, abs=0.02)
    played = env.measured["speed"][0]
    assert played["film_s"][0] == pytest.approx(6.0, abs=0.02)
    assert played["film_s"][1] == pytest.approx(env.measured["program_s"],
                                                 abs=0.03)
    assert played["source_s"] == [pytest.approx(10.5, abs=0.02),
                                  pytest.approx(11.26, abs=0.02)]


def test_speed_at_source_keeps_source_seconds():
    ctx, _fake = _ctx(11.26, P5744_KEEP)
    commit.edit(ctx, [{"speed": [{"start": 10.45, "end": 11.26,
                                  "factor": 0.6, "at_source": True}]}], "s")
    sp = ctx.latest_edl()["json"]["speed"][0]
    assert (sp["start"], sp["end"]) == (pytest.approx(10.45),
                                        pytest.approx(11.26))


def test_a_film_span_across_a_cut_slows_both_sides():
    ctx, _fake = _ctx(11.26, P5744_KEEP)
    commit.edit(ctx, [{"speed": [{"start": 3.0, "end": 6.0,
                                  "factor": 0.5}]}], "s")
    spans = sorted((round(s["start"], 2), round(s["end"], 2))
                   for s in ctx.latest_edl()["json"]["speed"])
    assert spans == [(3.0, 4.75), (9.25, 10.5)]


def test_film_to_source_honours_existing_speed():
    edl = {"keep": P5744_KEEP,
           "speed": [{"id": "sp1", "start": 0.0, "end": 4.75,
                      "factor": 0.5}]}
    # 0-4.75 s of source now plays over 9.5 s of film.
    assert ops_mod.film_to_source_spans(edl, 9.5, 10.5) == [(9.25, 10.25)]
    assert measure.speed_measure(edl)[0]["film_s"] == [0.0, 9.5]


# -- p5744: a footage link is placeable ---------------------------------------

def test_place_downloads_a_footage_link_and_places_it(monkeypatch):
    ctx, fake = _ctx(11.26, P5744_KEEP)
    key = "clips/2/bubbles.mp4"

    def fetch(c, url, as_kind=None):
        assert as_kind == "clip"
        c.urls_fetched.append({"storage_key": key, "kind": "video",
                               "url": url})
        c._pending_visual_review_assets.add(key)
        return f"Downloaded as {key}"
    placed = []

    def overlay(c, asset_key, start, duration_s=None, **kw):
        placed.append((asset_key, start, duration_s, kw.get("opacity")))
        edl = dict(c.latest_edl()["json"])
        edl["overlays"] = [{"id": "ov1", "asset_key": asset_key,
                            "start": start, "duration_s": duration_s,
                            "fit": "cover"}]
        return c.write_edl(edl, "overlay")
    monkeypatch.setattr(agent_tools, "fetch_url", fetch)
    monkeypatch.setattr(agent_tools, "add_overlay", overlay)
    monkeypatch.setitem(agent_tools.TOOLS, "add_overlay",
                        (overlay,) + tuple(agent_tools.TOOLS["add_overlay"][1:]))
    env = commit.edit(ctx, [{"place": {
        "id": "https://www.youtube.com/watch?v=FAS9BW7JcFM", "at": 0,
        "dur": 6.76, "mode": "overlay", "opacity": 0.6}}], "bubbles")
    assert placed and placed[0][0] == key and placed[0][3] == 0.6
    assert "downloaded" in env.text()
    assert not env.not_applied


def test_a_link_that_cannot_download_says_why(monkeypatch):
    ctx, _fake = _ctx(11.26, P5744_KEEP)
    monkeypatch.setattr(agent_tools, "fetch_url",
                        lambda c, u, as_kind=None:
                        "Could not download that link — too long")
    env = commit.edit(ctx, [{"place": {"id": "https://example.com/x.mp4",
                                       "at": 0, "dur": 2}}], "x")
    assert env.not_applied and "too long" in env.text()


# -- p5744: a named music genre -----------------------------------------------

POOL = [{"cid": "km-future-gladiator", "title": "Future Gladiator",
         "tags": ["Timpani", "Scary music"], "bpm": 66},
        {"cid": "jm-funk", "title": "Bring back tha funk",
         "tags": ["Electronic music", "House music"], "bpm": 125}]


def test_music_find_puts_the_named_genre_first_or_says_none(monkeypatch):
    import music_choose
    ctx, _fake = _ctx(11.26, P5744_KEEP)
    monkeypatch.setattr(find_tool, "_music_pool",
                        lambda: (list(POOL), "library tracks the gate "
                                             "passed"))
    monkeypatch.setattr(music_choose, "shortlist",
                        lambda film, n=6, pool=None, used=():
                        ([{"track": t} for t in pool], {}))
    env = find_tool.find(ctx, kind="music", query=["funk", "chill lofi"])
    body = env.text()
    first = [ln for ln in body.splitlines() if ln.startswith("  lib:")][0]
    assert "jm-funk" in first
    assert env.measured["matched"] == {"funk": 1, "chill lofi": 0}
    assert "[chill lofi] no track of reference grade matches" in body
    # The CC0 lofi tracks exist and the gate held them back: said, named.
    assert "Calm Currents (CC0)" in body and "band-limited" in body
