"""Craft for an upload whose changes of picture are baked into one file.

p5520: a 348 s slideshow, one MP4, 90 pictures. Eleven customer messages asked
for dissolves, a filmstrip and blurred fill; 123 versions later every photo
change was still a hard cut. These pin what has to stay true: the map measures
every change of picture, one tool call solves a whole film, length and sound
never move, and many windows no longer cost windows times frames.
"""

import copy
import json
import math
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import agent_loop                                          # noqa: E402
import agent_tools                                         # noqa: E402
import camera                                              # noqa: E402
import media                                               # noqa: E402
import photo_craft                                         # noqa: E402
import picture_map                                         # noqa: E402
import renderer                                            # noqa: E402
import schemas                                             # noqa: E402
import stitch                                              # noqa: E402
import timeline                                            # noqa: E402
import tools_ext                                           # noqa: E402
from tools_ext import photo_tools as F                     # noqa: E402
from tools_ext import punctuation_tools as P               # noqa: E402

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None,
                                  reason="ffmpeg")


# ── the map ──────────────────────────────────────────────────────────────

def test_a_cut_lands_on_the_keep_grid_before_its_first_frame():
    for n in (139, 140, 141, 1000, 2543):
        grid = picture_map.cut_grid(n, 30.0)
        assert round(grid, 2) == grid
        assert (n - 1) / 30.0 < grid <= n / 30.0 + 1e-9
        assert renderer.first_frame_at(grid, 30.0) == n
    assert renderer.first_frame_at(picture_map.cut_grid(77, 59.94),
                                   59.94) == 77


def test_cuts_on_stills_on_motion_and_a_flash_that_comes_back():
    d = [0.0] * 200
    d[50] = 14.0                 # a close pair of photos, on stills
    d[120] = 30.0
    assert picture_map.find_cuts(d, d) == [50, 120]
    busy = [6.0 + (i % 3) for i in range(200)]
    busy[100] = 60.0
    busy[20] = 12.0              # a fast move, no cut
    assert picture_map.find_cuts(busy, busy) == [100]
    whip = [0.5] * 100           # a baked whip climbs into its real cut
    whip[50:55] = [4.0, 13.0, 24.0, 49.0, 124.0]
    assert picture_map.find_cuts(whip, whip) == [54]
    flash = [0.0] * 100
    flash[40] = 50.0
    flash[41] = 50.0
    away = list(flash)
    away[40] = away[41] = 0.5    # two frames on it is the old picture again
    assert picture_map.find_cuts(flash, away) == []
    repeats = [0.0 if i % 2 else 5.0 for i in range(200)]  # a variable rate
    repeats[100] = 60.0
    assert picture_map.find_cuts(repeats, repeats) == [100]
    blended = [0.0] * 100
    blended[60] = 20.0
    blended[61] = 20.0           # a mixed frame on the join is one change
    assert picture_map.find_cuts(blended, blended) == [61]


def _frame(W=64, H=36, x0=0, x1=None, y0=0, y1=None, bar=16, level=120):
    f = np.full((H, W), bar, np.uint8)
    x1 = W if x1 is None else x1
    y1 = H if y1 is None else y1
    rng = np.random.default_rng(3)
    f[y0:y1, x0:x1] = rng.integers(40, 200, (y1 - y0, x1 - x0))
    return f


def test_rectangles_read_pillars_letterbox_and_a_small_inset_photo():
    f = _frame(x0=18, x1=46)
    assert picture_map.find_rect(f, f) == pytest.approx(
        [18 / 64, 0.0, 46 / 64, 1.0], abs=1e-4)
    f = _frame(y0=6, y1=30)
    assert picture_map.find_rect(f, f) == pytest.approx(
        [0.0, 6 / 36, 1.0, 30 / 36], abs=1e-4)
    f = _frame(x0=22, x1=42, y0=5, y1=31)
    assert picture_map.find_rect(f, f) == pytest.approx(
        [22 / 64, 5 / 36, 42 / 64, 31 / 36], abs=1e-4)
    f = _frame()
    assert picture_map.find_rect(f, f) == [0.0, 0.0, 1.0, 1.0]


def test_a_dark_edge_of_the_photo_beside_a_bar_stays_the_photo():
    f = _frame(x0=18, x1=46)
    f[:, 18:24] = 16             # the photo's own black side
    assert picture_map.find_rect(f, f)[0] == pytest.approx(18 / 64, abs=1e-4)


def test_a_photo_flat_at_one_end_has_no_bars():
    f = _frame()
    f[:6] = 235                  # a white sky: flat, but only at the top
    assert picture_map.find_rect(f, f) == [0.0, 0.0, 1.0, 1.0]


def test_exposing_changes_no_frame():
    keep, added = picture_map.expose([[0.0, 10.0], [12.0, 20.0]],
                                     [4.63, 9.97, 11.0, 15.0])
    assert keep == [[0.0, 4.63], [4.63, 10.0], [12.0, 15.0], [15.0, 20.0]]
    assert added == 2           # 9.97 would leave a span under 0.05 s
    assert [tuple(k) for k in picture_map.expose([[0.0, 5.0]], [])[0]] \
        == [(0.0, 5.0)]


def test_the_last_span_gets_no_frame_the_source_lacks():
    grid = picture_map.cut_grid(10430, 30.0)
    assert grid == 347.66
    keep, _n = picture_map.expose([[0.0, 347.67]], [4.63],
                                  last_frame_end=grid)
    assert keep == [[0.0, 4.63], [4.63, 347.66]]
    assert renderer.first_frame_at(keep[-1][1], 30.0) == 10430
    keep, _n = picture_map.expose([[0.0, 100.0]], [4.63], last_frame_end=grid)
    assert keep[-1][1] == 100.0


# ── the tools ────────────────────────────────────────────────────────────

MAP = {"v": picture_map.VERSION, "fps": 30.0, "frames": 1200, "duration": 40.0,
       "width": 1920, "height": 1080,
       "cuts": [4.63, 9.4, 14.66, 20.0, 24.96, 30.0, 35.0],
       "shots": []}
_marks = [0.0] + MAP["cuts"] + [40.0]
for _k, (_a, _b) in enumerate(zip(_marks, _marks[1:])):
    MAP["shots"].append({"start": _a, "end": _b, "still": True,
                         "bars": bool(_k % 2),
                         "rect": ([0.3, 0.0, 0.7, 1.0] if _k % 2
                                  else [0.0, 0.0, 1.0, 1.0])})


class _DB:
    def run(self, fn, *a):
        return set()


class Ctx:
    def __init__(self, edl=None, pm=MAP, shots=None):
        self.db, self.project_id, self.duration = _DB(), 1, 40.0
        self.index = {"words": [], "video": {"fps": 30.0, "duration": 40.0},
                      "shots": shots or []}
        self.user_message = ""
        self._asset_perception = {}
        self.version = 3
        self._picture_map = pm
        self.edl = schemas.validate_edl(
            copy.deepcopy(edl or {"keep": [[0.0, 40.0]]}), 40.0).model_dump()
        self.written = []

    def latest_edl(self):
        return {"version": self.version, "json": copy.deepcopy(self.edl)}

    def write_edl(self, edl, desc):
        self.edl = schemas.validate_edl(copy.deepcopy(edl), 40.0).model_dump()
        self.written.append(desc)
        self.version += 1
        return f"EDL v{self.version - 1} -> v{self.version}: {desc}"


def _junctions(ctx):
    return [i for i in ctx.edl["fx"] if i["type"] == "junction"]


def test_one_call_dissolves_every_picture_of_one_kept_span():
    ctx = Ctx()
    out = P.set_junction(ctx, "dissolve", duration=1.0)
    assert out.startswith("EDL")
    assert len(ctx.written) == 1
    assert [j["at"] for j in _junctions(ctx)] == MAP["cuts"]
    assert all(j["hold"] and j["duration_s"] == 1.0 for j in _junctions(ctx))
    assert len(ctx.edl["keep"]) == len(MAP["cuts"]) + 1
    tl = timeline.Timeline(ctx.edl["keep"], [], [])
    assert tl.out_duration == pytest.approx(40.0)


def test_a_cut_the_index_missed_gets_its_transition():
    shots = [{"start": s["start"], "end": s["end"]} for s in MAP["shots"][:3]]
    ctx = Ctx(shots=shots)          # 20 s onward: one long indexed "shot"
    P.set_junction(ctx, "dissolve", cuts="scene")
    assert [j["at"] for j in _junctions(ctx)] == MAP["cuts"]


def test_a_cut_only_the_index_saw_is_a_cut_too():
    ctx = Ctx(shots=[{"start": 0.0, "end": 2.57},
                     {"start": 2.57, "end": 4.6},    # dark footage: map blind
                     {"start": 4.6, "end": 40.0}])
    cuts = P._baked_cuts(ctx)
    assert 2.56 in cuts and 4.63 in cuts and 4.6 not in cuts
    assert renderer.first_frame_at(2.56, 30.0) == 77


def test_a_range_a_scope_and_the_names_a_model_reaches_for():
    ctx = Ctx()
    P.set_junction(ctx, "crossfade", start=0, end=15, scope="every_cut",
                   duration=1.5)
    assert [(j["at"], j["style"]) for j in _junctions(ctx)] == [
        (4.63, "dissolve"), (9.4, "dissolve"), (14.66, "dissolve")]
    P.set_junction(ctx, "slide_right", start=15, end=26, duration=0.8)
    P.set_junction(ctx, "dip_black", start=26, end=40)
    by = {j["at"]: j for j in _junctions(ctx)}
    assert by[20.0]["style"] == "slide" and by[20.0]["direction"] == "right"
    assert by[30.0]["style"] == "fade_black"


def test_a_follow_up_changes_or_removes_them_by_name():
    ctx = Ctx()
    P.set_junction(ctx, "dissolve")
    P.set_junction(ctx, "slide", start=19, end=26)
    assert sum(j["style"] == "slide" for j in _junctions(ctx)) == 2
    assert len(_junctions(ctx)) == len(MAP["cuts"])
    out = P.set_junction(ctx, "none", only="slide")
    assert "removed 2" in out
    assert {j["style"] for j in _junctions(ctx)} == {"dissolve"}
    P.set_junction(ctx, "none", start=0, end=10)
    assert [j["at"] for j in _junctions(ctx)] == [14.66, 30.0, 35.0]
    assert "NO CHANGE" in P.set_junction(ctx, "none", start=0, end=10)
    P.set_junction(ctx, "none")
    assert not _junctions(ctx)


def test_set_transitions_routes_what_shows_both_pictures():
    ctx = Ctx()
    out = agent_tools.set_transitions(ctx, "dissolve", duration_s=1.0,
                                      scope="every_cut")
    assert out.startswith("EDL")
    assert len(_junctions(ctx)) == len(MAP["cuts"])
    assert not (ctx.edl["effects"] or {}).get("transition")
    assert agent_tools.set_transitions(ctx, "none").startswith("EDL")
    assert not _junctions(ctx)


def test_filmstrip_solves_its_pictures_in_one_call():
    ctx = Ctx()
    out = F.add_filmstrip(ctx, 10.0, 25.0)
    assert out.startswith("EDL")
    item = next(i for i in ctx.edl["fx"] if i["type"] == "filmstrip")
    assert item["start"] == 10.0 and item["end"] == 24.96
    ts = [p["t"] for p in item["photos"]]
    assert ts == sorted(ts) and len(ts) == 3
    assert item["photos"][1]["rect"]["x0"] == pytest.approx(0.3)
    assert "needs two" in F.add_filmstrip(ctx, 15.0, 19.0)
    F.add_filmstrip(ctx, 10.0, 25.0, direction="right")
    strips = [i for i in ctx.edl["fx"] if i["type"] == "filmstrip"]
    assert len(strips) == 1 and strips[0]["direction"] == "right"
    assert F.remove_filmstrip(ctx).startswith("EDL")
    assert "NO CHANGE" in F.remove_filmstrip(ctx)


def test_filmstrip_skips_shards_of_black_and_takes_a_fullest_frame():
    pm = copy.deepcopy(MAP)
    pm["shots"][2]["flat"] = True
    pm["shots"][3]["best"] = 16.0
    ctx = Ctx(pm=pm)
    F.add_filmstrip(ctx, 5.0, 30.0)
    item = next(i for i in ctx.edl["fx"] if i["type"] == "filmstrip")
    ts = [p["t"] for p in item["photos"]]
    assert 16.0 in ts
    assert not any(9.4 <= t < 14.66 for t in ts)


def test_filmstrip_follows_its_pictures_through_a_later_cut():
    ctx = Ctx()
    F.add_filmstrip(ctx, 10.0, 25.0)
    edl = copy.deepcopy(ctx.edl)
    old = timeline.Timeline(edl["keep"], [], [])
    new = timeline.Timeline([[0.0, 5.0], [8.0, 40.0]], [], [])
    edl["keep"] = [[0.0, 5.0], [8.0, 40.0]]
    timeline._remap_fx(edl, old, new)
    item = next(i for i in edl["fx"] if i["type"] == "filmstrip")
    assert item["start"] == pytest.approx(7.0)


def test_a_piece_of_the_run_keeps_the_runs_clock():
    item = {"id": "fs1", "type": "filmstrip", "stage": "world",
            "start": 10.0, "end": 20.0, "photos": []}
    got = stitch._window_fx([item], 12.0, 15.0, 30.0, set())[0]
    assert got["run"] == [-2.0, 8.0]
    assert got["start"] == 0.0 and got["end"] == 3.0


def test_fill_takes_only_pictures_with_bars_and_comes_off():
    ctx = Ctx()
    out = F.set_photo_fill(ctx)
    assert out.startswith("EDL")
    item = next(i for i in ctx.edl["fx"] if i["type"] == "photo_fill")
    assert [s["t0"] for s in item["spans"]] == [
        s["start"] for s in MAP["shots"] if s["bars"]]
    F.set_photo_fill(ctx, start=0, end=15, mode="none")
    item = next(i for i in ctx.edl["fx"] if i["type"] == "photo_fill")
    assert min(s["t0"] for s in item["spans"]) >= 14.66
    assert F.set_photo_fill(ctx, mode="none").startswith("EDL")
    assert not ctx.edl["fx"]
    assert "NO CHANGE" in F.set_photo_fill(ctx, mode="none")


def test_a_wide_photo_in_a_tall_frame_is_filled_too():
    ctx = Ctx({"keep": [[0.0, 40.0]], "frame": {"ratio": "9:16",
                                                "mode": "crop"}})
    F.set_photo_fill(ctx)
    item = next(i for i in ctx.edl["fx"] if i["type"] == "photo_fill")
    assert len(item["spans"]) == len(MAP["shots"])


def test_one_picture_by_at():
    ctx = Ctx()
    F.set_photo_fill(ctx, at=6.0)
    item = next(i for i in ctx.edl["fx"] if i["type"] == "photo_fill")
    assert [(s["t0"], s["t1"]) for s in item["spans"]] == [(4.63, 9.4)]


def test_the_first_page_offers_them_and_crossfades_exist():
    text = agent_loop.capabilities_block()
    assert "true crossfades" not in text
    head = text[:900]
    for name in ("set_junction", "add_filmstrip", "set_photo_fill"):
        assert name in head
    assert {"set_junction", "add_filmstrip", "set_photo_fill",
            "remove_filmstrip"} <= agent_tools.TOOL_DOMAINS["motion"]


def test_no_measurement_refuses_honestly_and_junctions_use_the_index():
    ctx = Ctx(pm={}, shots=[{"start": 0.0, "end": 5.0},
                            {"start": 5.0, "end": 40.0}])
    ctx.project_id = None
    assert "could not be measured" in F.add_filmstrip(ctx, 0, 30)
    assert "could not be measured" in F.set_photo_fill(ctx)
    assert P.set_junction(ctx, "dissolve").startswith("EDL")
    assert [j["at"] for j in _junctions(ctx)] == [5.0]


# ── the camera across a transition ───────────────────────────────────────

def _scale(zooms, t):
    return 1.0 + sum(float(camera.zoom_terms(z, t, float(z["start"]),
                                             float(z["end"]), False).z)
                     for z in zooms)


def test_zooms_on_a_dissolve_ease_across_it_with_no_step():
    zooms = [{"id": "a", "mode": "ease", "strength": 0.08, "start": 0.0,
              "end": 5.0},
             {"id": "b", "mode": "push_in", "strength": 0.06, "start": 5.0,
              "end": 10.0},
             {"id": "c", "mode": "punch", "strength": 0.1, "start": 10.0,
              "end": 14.0}]
    soft = [(5.0, 0.5), (10.0, 0.5)]
    held = camera.hold_through_cuts(zooms, [5.0, 10.0], 30.0, 20.0, soft=soft)
    assert held[0]["end"] == 5.5 and held[1]["start"] == 4.5
    worst = max(abs(_scale(held, (k + 1) / 30.0) - _scale(held, k / 30.0))
                for k in range(120, 330))
    assert worst < 0.005
    hard = camera.hold_through_cuts(zooms, [5.0, 10.0], 30.0, 20.0)
    assert max(abs(_scale(hard, (k + 1) / 30.0) - _scale(hard, k / 30.0))
               for k in range(120, 330)) > 0.05


def test_a_zoom_that_spans_a_dissolve_is_left_alone():
    z = {"id": "a", "mode": "ease", "strength": 0.05, "start": 0.0,
         "end": 17.0}
    out = camera.hold_through_cuts([z], [5.0], 30.0, 20.0, soft=[(5.0, 0.5)])
    assert out[0] is z


def test_transition_windows_are_capped_like_the_render():
    edl = {"keep": [[0.0, 2.0], [2.0, 10.0]],
           "fx": [{"type": "junction", "at": 2.0, "duration_s": 2.0}]}
    tl = timeline.Timeline(edl["keep"], [], [])
    assert renderer.transition_windows(edl, tl) == [(2.0, 1.0)]


# ── the renderer ─────────────────────────────────────────────────────────

def _make_source(path, W=320, H=180, each=2.0, colours=("red", "lime", "blue",
                                                        "yellow", "white",
                                                        "cyan", "magenta")):
    """Stills on black pillars with a tone under them, baked into one file."""
    n = len(colours)
    inputs, chain = [], []
    for i, c in enumerate(colours):
        inputs += ["-f", "lavfi", "-t", str(each), "-i",
                   f"color=c={c}:s={W // 2}x{H}:r=30"]
        chain.append(f"[{i}:v]pad={W}:{H}:{W // 4}:0:black,format=yuv420p,"
                     f"setsar=1[v{i}]")
    chain.append("".join(f"[v{i}]" for i in range(n))
                 + f"concat=n={n}:v=1:a=0[v]")
    inputs += ["-f", "lavfi", "-t", str(n * each), "-i",
               "sine=frequency=440:sample_rate=48000"]
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *inputs,
                    "-filter_complex", ";".join(chain), "-map", "[v]",
                    "-map", f"{n}:a", "-c:v", "libx264", "-preset",
                    "ultrafast", "-crf", "12", "-c:a", "aac", "-shortest",
                    str(path)], check=True)
    return n * each


def _rgb(path, t, W=None):
    raw = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss",
                          f"{t:.4f}", "-i", str(path), "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         stdout=subprocess.PIPE, check=True).stdout
    info = media.probe(str(path))
    return np.frombuffer(raw, np.uint8).reshape(info["height"], info["width"],
                                                3).astype(np.int32)


@pytest.fixture(scope="module")
def film(tmp_path_factory):
    d = tmp_path_factory.mktemp("film")
    src = d / "slideshow.mp4"
    dur = _make_source(src)
    pm = picture_map.measure(str(src))
    return {"src": str(src), "dur": dur, "map": pm, "dir": d}


def _solve(film, calls, base=None):
    pm = film["map"]

    class C(Ctx):
        pass
    ctx = C.__new__(C)
    ctx.db, ctx.project_id, ctx.duration = _DB(), 1, film["dur"]
    ctx.index = {"words": [], "video": {"fps": 30.0, "duration": film["dur"]},
                 "shots": []}
    ctx.user_message = ""
    ctx._asset_perception = {}
    ctx.version = 1
    ctx._picture_map = pm
    ctx.edl = schemas.validate_edl(
        copy.deepcopy(base or {"keep": [[0.0, film["dur"]]]}),
        film["dur"]).model_dump()
    ctx.written = []

    def write_edl(edl, desc):
        ctx.edl = schemas.validate_edl(copy.deepcopy(edl),
                                       film["dur"]).model_dump()
        ctx.version += 1
        return f"EDL v{ctx.version - 1} -> v{ctx.version}: {desc}"
    ctx.write_edl = write_edl
    for fn, kw in calls:
        out = fn(ctx, **kw)
        assert out.startswith("EDL"), out
    return ctx.edl


def _render(film, edl, name, preview=True):
    out = film["dir"] / name
    wd = film["dir"] / (name + ".wd")
    wd.mkdir(exist_ok=True)
    d = renderer.render_edl(edl, {"words": [], "video": {"duration":
                                                         film["dur"]}},
                            film["src"], str(out), str(wd), preview=preview)
    return out, d


@needs_ffmpeg
def test_the_map_of_a_real_file(film):
    pm = film["map"]
    assert pm["cuts"] == pytest.approx([2.0, 4.0, 6.0, 8.0, 10.0, 12.0],
                                       abs=0.011)
    assert all(s["still"] for s in pm["shots"])
    r = pm["shots"][1]["rect"]
    assert r[0] == pytest.approx(0.25, abs=0.01)
    assert r[2] == pytest.approx(0.75, abs=0.01)


@needs_ffmpeg
def test_dissolves_on_every_baked_cut_keep_length_and_sound(film):
    edl = _solve(film, [(P.set_junction, {"style": "dissolve",
                                          "duration": 1.0})])
    assert len(edl["fx"]) == 6
    out, d = _render(film, edl, "dissolve.mp4")
    info = media.probe(str(out))
    assert d == pytest.approx(film["dur"], abs=0.04)
    frames = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v", "-count_packets",
         "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0",
         str(out)], stdout=subprocess.PIPE, text=True).stdout.strip()
    assert int(frames.split(",")[0]) == int(round(film["dur"] * 30))
    assert info["has_audio"]
    mid = _rgb(out, 2.0)[90, 160]           # red into lime, half way
    assert mid[0] > 70 and mid[1] > 70
    assert _rgb(out, 1.3)[90, 160][1] < 25   # all the outgoing picture
    assert _rgb(out, 2.7)[90, 160][0] < 25   # all the incoming picture


@needs_ffmpeg
def test_moving_footage_keeps_moving_through_a_dissolve(tmp_path):
    src = tmp_path / "moving.mp4"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
         "testsrc2=s=320x180:r=30:d=4", "-f", "lavfi", "-i",
         "testsrc=s=320x180:r=30:d=4", "-filter_complex",
         "[0:v]setsar=1[a];[1:v]setsar=1[b];[a][b]concat=n=2:v=1:a=0[v]",
         "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast", "-crf",
         "14", str(src)], check=True)
    film = {"src": str(src), "dur": 8.0, "map": {
        "cuts": [4.0], "fps": 30.0, "shots": [], "width": 320, "height": 180},
        "dir": tmp_path}
    edl = _solve(film, [(P.set_junction, {"style": "dissolve",
                                          "duration": 1.0})])
    out, _d = _render(film, edl, "moved.mp4")
    frames = [_rgb(out, 3.5 + k / 30.0) for k in range(0, 30, 2)]
    still = sum(np.abs(a - b).mean() < 0.3 for a, b in zip(frames, frames[1:]))
    assert still == 0                       # nothing in the window stops


@needs_ffmpeg
def test_a_window_series_is_woven_not_overlaid(film):
    edl = _solve(film, [(P.set_junction, {"style": "slide"})])
    captured = {}
    real = renderer.build_filtergraph

    def spy(*a, **kw):
        out = real(*a, **kw)
        captured["graph"] = out
        return out
    renderer.build_filtergraph = spy
    try:
        _render(film, edl, "slide.mp4")
    finally:
        renderer.build_filtergraph = real
    assert "tpad=start=" not in json.dumps(captured["graph"], default=str)


@needs_ffmpeg
def test_slide_and_fade_through_black_render(film):
    edl = _solve(film, [(P.set_junction, {"style": "slide", "duration": 1.0,
                                          "direction": "left"})])
    out, _d = _render(film, edl, "slide2.mp4")
    half = _rgb(out, 2.0)
    assert (half[90, 120] != half[90, 280]).any()   # two pictures side by side
    edl = _solve(film, [(P.set_junction, {"style": "fade_black",
                                          "duration": 1.0})])
    out, _d = _render(film, edl, "black.mp4")
    assert _rgb(out, 2.0)[90, 160].max() < 60


@needs_ffmpeg
def test_blurred_fill_replaces_the_bars_and_leaves_the_picture(film):
    edl = _solve(film, [(F.set_photo_fill, {})])
    out, _d = _render(film, edl, "fill.mp4")
    bare = _rgb(film["src"], 1.0)
    got = _rgb(out, 1.0)
    H, W = got.shape[:2]
    assert got[H // 2, W // 16].sum() > 150         # no black bar left
    assert abs(got[H // 2, W // 2] - bare[90, 160]).max() < 14


@needs_ffmpeg
def test_filmstrip_shows_two_pictures_and_keeps_length(film):
    edl = _solve(film, [(F.add_filmstrip, {"start": 2.0, "end": 8.0})])
    out, d = _render(film, edl, "strip.mp4")
    assert d == pytest.approx(film["dur"], abs=0.04)
    frame = _rgb(out, 4.0)
    H, W = frame.shape[:2]
    row = frame[H // 2]
    # at least two of the source's pictures across one row
    hues = {tuple((row[x] // 100).tolist()) for x in range(0, W, 8)}
    assert len([h for h in hues if max(h) >= 1]) >= 2
    # the picture before and after the range is the source's
    assert abs(_rgb(out, 0.5)[H // 2, W // 2]
               - _rgb(film["src"], 0.5)[90, 160]).max() < 14


def test_strip_position_is_eased_and_exact():
    assert photo_craft._run_position(0.0) == 0.0
    assert photo_craft._run_position(1.0) == pytest.approx(1.0)
    us = [i / 100 for i in range(101)]
    ps = [photo_craft._run_position(u) for u in us]
    assert all(b >= a for a, b in zip(ps, ps[1:]))
    assert ps[50] == pytest.approx(0.5)


def test_rect_pixels_stay_even_and_inside_the_bars():
    x, y, w, h = photo_craft.rect_pixels([0.3, 0.0, 0.7, 1.0], 1920, 1080)
    assert x % 2 == 0 and w % 2 == 0 and h % 2 == 0
    assert x > 0.3 * 1920 and x + w < 0.7 * 1920
    assert (y, h) == (0, 1080)
    assert photo_craft.rect_pixels([0.0, 0.0, 1.0, 1.0], 1920, 1080) == \
        (0, 0, 1920, 1080)


def test_a_join_of_touching_spans_never_reads_a_handle():
    keep = [(0.0, 4.0), (4.0, 8.0), (9.0, 12.0)]
    assert renderer._joined_in_source(("seg", 0, 4.0), ("seg", 1, 4.0), keep)
    assert not renderer._joined_in_source(("seg", 1, 4.0), ("seg", 2, 3.0),
                                          keep)


def test_budget_never_drops_a_transition(tmp_path):
    import fxgraph
    edl = schemas.validate_edl(
        {"keep": [[float(i), float(i + 1)] for i in range(39)],
         "fx": [{"id": f"jn{i}", "type": "junction", "stage": "junction",
                 "at": float(i), "style": "dissolve", "duration_s": 0.4,
                 "device": "junction_dissolve",
                 "bind": {"on": "cut", "ref": float(i)}}
                for i in range(1, 39)]}, 40.0).model_dump()
    tl = timeline.Timeline(edl["keep"], [], [])
    plan = fxgraph.FxPlan(W=854, H=480, fps=30, preview=True, tl=tl,
                          program_s=tl.out_duration, workdir=str(tmp_path))
    plan.budget_ms = 0.0
    for it in edl["fx"]:
        fxgraph._prepare_item(plan, it)
    assert len(plan.junction_items) == 38


@needs_ffmpeg
def test_a_stitched_piece_renders_the_same_strip_fill_and_dissolve(film):
    edl = _solve(film, [(P.set_junction, {"style": "dissolve",
                                          "duration": 1.0}),
                        (F.set_photo_fill, {}),
                        (F.add_filmstrip, {"start": 4.0, "end": 10.0})])
    tl = timeline.Timeline(edl["keep"], [], [])
    whole, _d = _render(film, edl, "whole.mp4")
    w0, w1 = 6.0, 12.5
    piece = stitch.window_edl(schemas.validate_edl(
        copy.deepcopy(edl), film["dur"]).model_dump(), tl, w0, w1,
        keep_audio=True, fps=30.0)
    strip = next(i for i in piece["fx"] if i["type"] == "filmstrip")
    assert strip["run"] == [-2.0, 4.0]
    wd = film["dir"] / "piece.wd"
    wd.mkdir(exist_ok=True)
    out = film["dir"] / "piece.mp4"
    renderer.render_edl(piece, {"words": [], "video": {"duration":
                                                       film["dur"]}},
                        film["src"], str(out), str(wd), preview=True,
                        render_fragment=True)
    for t in (7.0, 9.0, 11.0):
        a, b = _rgb(whole, t), _rgb(out, t - w0)
        assert np.abs(a - b).mean() < 10, t


def test_a_graph_of_many_scalers_stays_under_the_process_thread_limit():
    small = "[a]scale=2:2[b];" * 10
    assert renderer._filter_thread_args(small) == []
    many = "[a]scale=2:2[b];" * 600
    args = renderer._filter_thread_args(many)
    assert args[0] == "-filter_complex_threads"
    assert int(args[1]) * 600 <= renderer.FILTER_THREAD_BUDGET
    held = "[a]scale=2:2:threads=1[b];" * 600
    assert renderer._filter_thread_args(held) == []


def test_a_picture_that_stops_short_fails_the_render(monkeypatch):
    monkeypatch.setattr(media, "probe", lambda p: {"video_duration": 315.0,
                                                   "duration": 347.7})
    with pytest.raises(media.MediaError, match="stops at 315"):
        renderer._require_whole_picture("x.mp4", 347.7)
    renderer._require_whole_picture("x.mp4", 315.5)


class _AssetDB:
    def __init__(self, meta=None):
        self.meta, self.saved = meta or {}, []

    def run(self, fn, *a):
        if getattr(fn, "__name__", "") == "latest_asset":
            return {"id": 9, "storage_key": "proxy/1.mp4", "meta": self.meta}
        if getattr(fn, "__name__", "") == "update_asset_meta":
            self.saved.append(a)
        return None


class _MapCtx:
    project_id = 1
    job = {"user_id": 2}

    def __init__(self, db):
        self.db = db


def test_the_map_is_measured_once_and_kept_on_the_asset(monkeypatch):
    import remote
    calls = []

    def run(pid, payload, user_id=None):
        calls.append(payload)
        return {"keys": [], "picture_map": MAP}
    monkeypatch.setattr(remote, "frames_available", lambda: True)
    monkeypatch.setattr(remote, "run_frames_remote", run)
    monkeypatch.setattr(agent_tools, "_touch_job_heartbeat", lambda c: None)
    db = _AssetDB()
    ctx = _MapCtx(db)
    assert F.picture_map_of(ctx) is MAP
    assert calls[0]["picture_map"] and calls[0]["storage_key"] == "proxy/1.mp4"
    assert db.saved[0][1] == {"picture_map": MAP}
    F.picture_map_of(ctx)
    assert len(calls) == 1                  # held on the context
    assert F.picture_map_of(_MapCtx(_AssetDB({"picture_map": MAP}))) == MAP
    assert len(calls) == 1                  # kept on the asset


def test_a_failed_measurement_is_no_map_and_no_crash(monkeypatch):
    import remote

    def boom(*a, **k):
        raise RuntimeError("executor down")
    monkeypatch.setattr(remote, "frames_available", lambda: True)
    monkeypatch.setattr(remote, "run_frames_remote", boom)
    monkeypatch.setattr(agent_tools, "_touch_job_heartbeat", lambda c: None)
    ctx = _MapCtx(_AssetDB())
    assert F.picture_map_of(ctx) is None
    assert picture_map.run_on("/no/such/file.mp4") is None


def test_the_audio_pruner_reads_a_concat_that_leads_a_chain():
    graph = ("[0:v]split[x][y];[0:a]asplit[p][q];"
             "[x][y]concat=n=2:v=1:a=0,settb=AVTB,setpts='round(N/30/TB)'[w];"
             "[w][p][w2][q]concat=n=2:v=1:a=1[vc][ac]")
    graph = graph.replace("[0:v]split[x][y]", "[0:v]split=3[x][y][w2]")
    out = renderer._prune_graph_to_audio(graph, target="ac")
    assert "[vc]" not in out and "[ac]" in out
