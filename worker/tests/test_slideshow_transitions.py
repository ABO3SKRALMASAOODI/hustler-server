"""One uploaded file with its cuts inside it (a photo slideshow, a finished
video): the customer asks for dissolves, film-strip slides and fades between
the photos (ticket #123, request 137396, project 5520).

Every attempt failed on our side. set_junction saw "no cut to decorate", the
transitions tool spread its few transitions over the whole video instead of
the stretch asked for, and a broad "preserve the photo order" froze the very
lane the customer was asking to change.
"""

import copy
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import agent_tools                                         # noqa: E402
import hooks                                               # noqa: E402
import request_contract as rc                              # noqa: E402
import schemas                                             # noqa: E402
import scope_guard                                         # noqa: E402
import tools_ext                                           # noqa: E402,F401
from tools_ext import punctuation_tools as P               # noqa: E402
from test_w1d_tools import Ctx                             # noqa: E402

SHOTS = [{"id": i, "start": float(i * 4), "end": float(i * 4 + 4)}
         for i in range(5)]
ONE_FILE = {"keep": [[0.0, 20.0]]}


def _ctx():
    ctx = Ctx(ONE_FILE)
    ctx.index["shots"] = copy.deepcopy(SHOTS)
    return ctx


def _junctions(ctx):
    return [i for i in ctx.edl.get("fx") or [] if i.get("type") == "junction"]


def test_a_dissolve_lands_on_the_cuts_inside_one_uploaded_file():
    ctx = _ctx()
    out = P.set_junction(ctx, "dissolve", duration=0.6)
    assert out.startswith("EDL"), out
    assert [j["at"] for j in _junctions(ctx)] == [4.0, 8.0, 12.0, 16.0]
    assert len(ctx.edl["keep"]) == 5 and ctx.edl["keep"][-1][1] == 20.0


def test_a_named_cut_and_a_named_stretch_inside_one_file():
    ctx = _ctx()
    assert P.set_junction(ctx, "slide_left", at=[8.0]).startswith("EDL")
    assert [j["at"] for j in _junctions(ctx)] == [8.0]
    ctx = _ctx()
    assert P.set_junction(ctx, "dissolve", start=5, end=13).startswith("EDL")
    assert [j["at"] for j in _junctions(ctx)] == [8.0, 12.0]


def test_a_file_with_no_shot_change_still_says_so():
    ctx = Ctx(ONE_FILE)
    assert "no cut to decorate" in P.set_junction(ctx, "dissolve")


def test_the_junction_survives_the_keep_change_that_exposed_its_cut():
    ctx = _ctx()
    P.set_junction(ctx, "dissolve", at=[8.0])
    edl = copy.deepcopy(ctx.edl)
    kept, notes = hooks.on_keep_change(edl, copy.deepcopy(edl))
    assert [i["at"] for i in kept["fx"]] == [8.0] and not notes


def test_a_slide_is_a_valid_edl_junction():
    ctx = _ctx()
    P.set_junction(ctx, "slide_right", at=[4.0])
    schemas.validate_edl(copy.deepcopy(ctx.edl), 20.0)


class _Tx:
    """set_transitions on the tool context the agent has."""

    def __init__(self, keep, shots, dur):
        self.index = {"shots": shots, "video": {"duration": dur, "fps": 25.0}}
        self.edl = schemas.validate_edl({"keep": keep}, dur).model_dump()
        self.written = []

    def latest_edl(self):
        return {"version": 1, "json": copy.deepcopy(self.edl)}

    def write_edl(self, edl, desc):
        self.edl = edl
        self.written.append(desc)
        return f"EDL v1 -> v2: {desc}"


def _slideshow(n=80, each=4.0):
    shots = [{"id": i, "start": i * each, "end": (i + 1) * each}
             for i in range(n)]
    return _Tx([[0.0, n * each]], shots, n * each)


def test_transitions_cover_the_named_stretch_not_the_whole_video():
    ctx = _slideshow()
    out = agent_tools.set_transitions(ctx, "dip_black", 0.4, start=0, end=54)
    assert out.startswith("EDL"), out
    picked = ctx.edl["effects"]["transition"]["junctions"]
    assert len(picked) == 13                       # 4, 8 ... 52
    assert picked == list(range(13))


def test_a_film_strip_reaches_the_named_stretch_from_the_transitions_tool():
    ctx = _ctx()
    out = agent_tools.set_transitions(ctx, "slide_left", 0.8, start=5, end=13)
    assert out.startswith("EDL"), out
    assert [(j["at"], j["style"]) for j in _junctions(ctx)] == [
        (8.0, "slide_left"), (12.0, "slide_left")]


def test_every_photo_change_stays_available_to_the_customer():
    ctx = _slideshow()
    out = agent_tools.set_transitions(ctx, "dip_black", 0.4)
    assert out.startswith("NOT APPLIED")
    assert "max_transitions=79" in out and "start=" in out
    assert agent_tools.set_transitions(
        ctx, "dip_black", 0.4, max_transitions=79).startswith("EDL")


def test_exposing_a_files_own_cuts_changes_no_preserved_cut_or_timing():
    before = schemas.validate_edl(dict(ONE_FILE), 20.0).model_dump()
    after = copy.deepcopy(before)
    after["keep"] = [[0.0, 4.0], [4.0, 8.0], [8.0, 20.0]]
    assert scope_guard.preservation_violations(
        before, after, protected={"cuts", "timeline_timing"}) == []
    moved = copy.deepcopy(before)
    moved["keep"] = [[0.0, 4.0], [5.0, 20.0]]
    assert scope_guard.preservation_violations(
        before, moved, protected={"cuts"}) == ["picture cuts"]


def _contract(rows, changed):
    from types import SimpleNamespace as NS
    ctx = NS(verification_request="smooth out the transitions",
             user_message="smooth out the transitions",
             request_contract_required=True)
    ctx.semantic_contract = {
        "schema_version": 1, "request_digest": rc.digest(ctx), "mode": "edit",
        "output_count": 1, "timing_policy": "preserve",
        "music_requested": False, "requirements": rows,
        "changed_lanes": changed}
    return ctx


def test_a_change_the_customer_asks_for_is_not_frozen_by_a_broader_preserve():
    ctx = _contract([
        {"id": "r1", "action": "change", "lanes": [],
         "description": "Smooth the transitions", "quote": "q"},
        {"id": "r2", "action": "preserve",
         "lanes": ["effects", "music", "cuts"],
         "description": "Keep everything else", "quote": "q"}], ["effects"])
    assert rc.protected_lanes(ctx) == {"music", "cuts", "timeline_timing"}


def test_the_timing_policy_is_not_undone_by_a_change():
    ctx = _contract([{"id": "r1", "action": "change", "lanes": [],
                      "description": "d", "quote": "q"}],
                    ["timeline_timing"])
    assert rc.protected_lanes(ctx) == {"timeline_timing"}


def test_the_compiled_contract_asks_which_lanes_the_changes_write():
    schema = rc._compile_schema({0: "smooth the transitions"})
    assert "changed_lanes" in schema["required"]
    assert "effects" in schema["properties"]["changed_lanes"]["items"]["enum"]


def test_a_contract_compiled_before_this_release_still_reads():
    ctx = _contract([{"id": "r1", "action": "preserve", "lanes": ["grade"],
                      "description": "d", "quote": "q"}], None)
    assert "grade" in rc.protected_lanes(ctx)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
@pytest.mark.parametrize("style,moves", [("dissolve", False),
                                         ("slide_left", True)])
def test_the_two_photos_really_meet_on_screen(style, moves, tmp_path,
                                              monkeypatch):
    """A rendered program: the outgoing photo's frame is held, so the middle
    of the window is a blend (dissolve) or both photos side by side (slide),
    never the hard cut the customer complained about."""
    from w1d_support import IDX, frames, lossless, render
    src = str(tmp_path / "slides.mp4")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "color=c=0xE0E0E0:s=320x180:r=25:d=2",
         "-f", "lavfi", "-i", "color=c=0x202020:s=320x180:r=25:d=2",
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=4",
         "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
         "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-qp", "0",
         "-pix_fmt", "yuv420p", "-t", "4", src], check=True)
    lossless(monkeypatch)
    edl = {"keep": [[0.0, 2.0], [2.0, 4.0]], "captions": {"enabled": False},
           "fx": [{"id": "j1", "type": "junction", "stage": "junction",
                   "at": 2.0, "style": style, "duration_s": 1.0}]}
    out = str(tmp_path / "out.mp4")
    idx = dict(IDX, shots=[{"id": 0, "start": 0.0, "end": 2.0},
                           {"id": 1, "start": 2.0, "end": 4.0}])
    dur, rep = render(edl, src, out, idx=idx, duration=4.0)
    assert dur == pytest.approx(4.0, abs=0.08)
    assert rep and rep[0]["rung"] in ("full", "reduced"), rep
    luma, _ = frames(out)
    first, last, mid = (luma[i].astype(float) for i in (5, -5, 50))
    if moves:                        # both photos on screen at once
        half = mid.shape[1] // 2
        assert abs(mid[:, :half].mean() - mid[:, half:].mean()) > 80
    else:                            # a true blend of the two
        assert first.mean() - 60 > mid.mean() > last.mean() + 60
        assert mid.std() < 12


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_every_asked_dissolve_is_drawn_past_the_render_budget(tmp_path,
                                                              monkeypatch):
    """The budget paces extras. It left out 51 of 82 dissolves, silently."""
    import devices
    from w1d_support import IDX, lossless, make_montage, render
    src = make_montage(str(tmp_path / "montage.mp4"), w=320, h=180)
    lossless(monkeypatch)
    monkeypatch.setattr(devices, "render_budget_ms", lambda *a, **k: 0.0)
    edl = {"keep": [[0.0, 3.0], [3.0, 6.0], [6.0, 9.0], [9.0, 12.0]],
           "captions": {"enabled": False},
           "fx": [{"id": f"j{i}", "type": "junction", "stage": "junction",
                   "device": "junction_dissolve", "at": t, "style": "dissolve",
                   "duration_s": 0.5} for i, t in enumerate((3.0, 6.0, 9.0))]}
    idx = dict(IDX, shots=[{"id": i, "start": i * 3.0, "end": i * 3.0 + 3.0}
                           for i in range(4)])
    _, rep = render(edl, src, str(tmp_path / "out.mp4"), idx=idx)
    assert len(rep) == 3 and {r["rung"] for r in rep} == {"full"}, rep
