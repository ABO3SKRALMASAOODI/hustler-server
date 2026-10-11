"""The source layout: a call window composited over a broadcast frame,
measured once on the parent source and inherited by every short.

The Diamandis run (Oct 10 2026, 0 of 9 shorts shipped): Elon Musk joined by
PHONE — a portrait FaceTime window (status bar, call buttons, Peter's
self-view in a corner) over a black graphic. auto_reframe fitted the whole
16:9 frame, 'auto' cards framed a wide shot of the phone, and children were
cut with no frame at all. These tests pin the measurement on synthetic
frames (no codec, no detector: faces are given), the card/crop geometry on
the run's measured numbers, and the tools that read it.
"""
import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")

import agent_tools
import source_layout as sl
from schemas import Frame, default_edl, validate_edl

W, H = 640, 360
WIN = (244, 14, 396, 346)            # the call window, work pixels
PIP = (252, 54, 290, 116)            # the host's self-view inside it


def _call_frame(seed, dark_right=False):
    """A broadcast frame: a black graphic (grid + a logo) with a portrait
    call composited on it — status bar, call buttons and a self-view that
    hold still while the call's picture changes."""
    rng = np.random.default_rng(seed)
    g = np.zeros((H, W), np.uint8)
    g[::32, :] = 60
    g[:, ::32] = 60
    g[30:50, 480:600] = 230                       # a burned-in logo
    x0, y0, x1, y1 = WIN
    # the call's picture: smooth blocks that change every frame
    blocks = rng.integers(90, 200, size=(12, 6)).astype(np.uint8)
    pic = np.kron(blocks, np.ones((28, 26), np.uint8))[:y1 - y0, :x1 - x0]
    g[y0:y1, x0:x1] = pic
    if dark_right:
        # a dark shirt against the black backdrop: no right side to see
        g[y0 + 120:y1, x1 - 30:x1] = 4
        g[y1 - 40:y1, x0:x1] = 4
    # status bar with icons
    g[y0:y0 + 12, x0:x1] = 225
    g[y0 + 3:y0 + 9, x0 + 8:x0 + 30] = 30
    g[y0 + 3:y0 + 9, x1 - 30:x1 - 8] = 30
    # call buttons
    yy, xx = np.mgrid[0:H, 0:W]
    for cx in (x0 + 20, x0 + 58, x0 + 96, x1 - 20):
        g[(yy - (y1 - 26)) ** 2 + (xx - cx) ** 2 <= 64] = 240
    # the self-view: a picture of its own inside a white outline
    px0, py0, px1, py1 = PIP
    g[py0:py1, px0:px1] = rng.integers(40, 160, size=(py1 - py0, px1 - px0))
    g[py0:py0 + 2, px0:px1] = 245
    g[py1 - 2:py1, px0:px1] = 245
    g[py0:py1, px0:px0 + 2] = 245
    g[py0:py1, px1 - 2:px1] = 245
    return g


def _face():
    x0, y0, x1, y1 = WIN
    return [round((x0 + 40) / W, 4), round((y0 + 120) / H, 4),
            round((x0 + 120) / W, 4), round((y0 + 220) / H, 4)]


def _rows(frames, faces):
    rows = []
    for t, (g, f) in enumerate(zip(frames, faces)):
        lines = sl._lines(g)
        rows.append({"t": float(t), "g": g, "lines": lines, "faces": f,
                     "cands": sl._window_cands(g, lines)})
    return rows


def test_a_call_window_its_chrome_and_the_self_view_are_measured():
    frames = [_call_frame(k, dark_right=(k % 3 == 2)) for k in range(9)]
    rows = _rows(frames, [[_face()] for _ in frames])
    wins = sl._find_windows(rows)
    assert len(wins) == 1
    win = wins[0]
    want = [WIN[0] / W, WIN[1] / H, (WIN[2] + 1) / W, WIN[3] / H]
    assert max(abs(a - b) for a, b in zip(win["rect"], want)) < .012
    # the frames whose dark shirt hides a side are recognised by the
    # unchanging graphic around the window
    assert all(r["win"] is win for r in rows)
    sl._chrome(rows, win)
    y0, h = WIN[1] / H, (WIN[3] - WIN[1]) / H
    assert y0 + .02 < win["chrome_top"] < y0 + .12 * h
    assert y0 + .80 * h < win["chrome_bottom"] < WIN[3] / H
    pip = [PIP[0] / W, PIP[1] / H, PIP[2] / W, PIP[3] / H]
    assert win["pip"] is not None
    assert max(abs(a - b) for a, b in zip(win["pip"], pip)) < .015
    win["head"] = sl._head(rows, win)
    assert sl._inside(_face(), [win["head"][0] - .001, win["head"][1] - .2,
                                win["head"][2] + .001, win["head"][3] + .001])


def test_an_ordinary_camera_frame_has_no_call_window():
    rng = np.random.default_rng(3)
    frames = []
    for k in range(8):
        blocks = rng.integers(20, 230, size=(9, 16)).astype(np.uint8)
        frames.append(np.kron(blocks, np.ones((40, 40), np.uint8))[:H, :W])
    rows = _rows(frames, [[[.4, .3, .55, .6]] for _ in frames])
    assert sl._find_windows(rows) == []
    spans = sl._spans(rows, [{"start": 0, "end": 4}, {"start": 4, "end": 8}], 8)
    assert [s["kind"] for s in spans] == ["camera", "camera"]
    assert all(s["win"] is None for s in spans)


def test_a_window_needs_the_speaking_face_inside_it():
    # the same composite, but no face measured inside: a picture frame on a
    # wall is not a call
    frames = [_call_frame(k) for k in range(6)]
    rows = _rows(frames, [[] for _ in frames])
    assert sl._find_windows(rows) == []


def test_samples_cover_every_shot_and_are_capped():
    shots = [{"start": 0, "end": 5}, {"start": 5, "end": 5.5},
             {"start": 5.5, "end": 40}, {"start": 40, "end": 400}]
    ts = sl.sample_times(400, shots)
    assert any(0 < t < 5 for t in ts) and not any(5 <= t < 5.5 for t in ts)
    assert sum(1 for t in ts if 40 < t < 400) > sum(1 for t in ts if t < 40)
    assert ts == sorted(ts) and 0 not in ts
    many = [{"start": i, "end": i + 2} for i in range(0, 1000, 2)]
    assert len(sl.sample_times(1000, many)) == sl.MAX_SAMPLES
    assert len(sl.sample_times(30, [])) >= 4


# ── the run's measured layout ─────────────────────────────────────────────

CALL = {"id": "call1", "rect": [0.3812, 0.0389, 0.6188, 0.9611],
        "kind": "call", "seen": 62, "secs": 1302.1, "chrome_top": 0.0995,
        "chrome_bottom": 0.8394, "pip": [0.3866, 0.1127, 0.4493, 0.3679],
        "face": [0.43, 0.36, 0.6148, 0.74],
        "head": [0.395, 0.2212, 0.6148, 0.7792], "measured": 62}
SPANS = [
    {"t0": 0.0, "t1": 28.43, "kind": "call", "win": "call1",
     "faces": [[0.44, 0.38, 0.63, 0.72]]},
    {"t0": 28.43, "t1": 32.0, "kind": "other", "win": None, "faces": []},
    {"t0": 32.0, "t1": 43.41, "kind": "camera", "win": None,
     "faces": [[0.43, 0.17, 0.53, 0.35]]},
    {"t0": 43.41, "t1": 121.75, "kind": "call", "win": "call1",
     "faces": [[0.43, 0.37, 0.63, 0.72]]},
    {"t0": 121.75, "t1": 138.67, "kind": "camera", "win": None,
     "faces": [[0.42, 0.18, 0.54, 0.39], [0.67, 0.31, 0.75, 0.44]]},
]
LAYOUT = {"v": sl.LAYOUT_VERSION, "w": 1920, "h": 1080, "samples": 160,
          "windows": [CALL], "spans": SPANS}
CAMERA = {"v": sl.LAYOUT_VERSION, "w": 1920, "h": 1080, "samples": 40,
          "windows": [], "spans": [
              {"t0": 0.0, "t1": 60.0, "kind": "camera", "win": None,
               "faces": [[0.40, 0.25, 0.55, 0.52]]},
              {"t0": 60.0, "t1": 140.0, "kind": "camera", "win": None,
               "faces": [[0.60, 0.20, 0.72, 0.45]]}]}


def test_the_largest_card_at_2x_without_a_band_meets_the_floor():
    c = sl.card_plan(CALL, 1920, 1080)
    assert c["k"] == 2.0 and c["area"] >= sl.PICTURE_FLOOR
    x0, y0, x1, y1 = c["box"]
    assert 0 <= x0 and x1 <= 1 and y0 >= sl.CARD_TOP - 1e-3 and \
        y1 <= sl.CARD_BOTTOM + 1e-3
    U = sl.usable(CALL)
    assert sl._inside(c["source"], U, 1e-3)
    assert c["head_ok"] is True
    # the source rect starts below the self-view's top: it must be erased
    assert c["pip"] == "cut"
    # the box's pixels are the source's at exactly 2x
    assert abs((x1 - x0) * 1080 / ((c["source"][2] - c["source"][0]) * 1920)
               - 2.0) < .02


def test_a_headline_band_costs_the_floor_and_full_bleed_needs_more_than_2x():
    band = sl.card_plan(CALL, 1920, 1080, top=sl.CARD_TOP_BAND)
    # the band leaves too little height: the floor is missed even at 2x,
    # and the close-up keeps the face whole by trimming the crown
    assert band["area"] < sl.PICTURE_FLOOR and band["k"] == 2.0
    assert band["head_ok"] is True and band["head_whole"] is False
    fb = sl.full_bleed(CALL, 1920, 1080)
    assert not fb["viable"] and fb["k"] > 2.0
    pc = sl.plain_crop(CALL, 1920, 1080)
    assert pc["whole"] and abs(pc["k"] - 1.78) < .01
    assert abs(pc["x"] - .5) < .01 and .1 < pc["margin"] < .15


def test_an_editors_box_narrows_rather_than_enlarge_past_2x():
    box = [0.06, 0.175, 0.94, 0.794]
    nbox, rect, k, ok, _pip = sl.card_for_box(CALL, 1920, 1080, box)
    assert k <= 2.0 + 1e-6 and ok is True
    assert nbox[2] - nbox[0] < box[2] - box[0]
    assert abs((nbox[1] + nbox[3]) - (box[1] + box[3])) < 1e-3
    assert sl._inside(rect, sl.usable(CALL), 1e-3)
    # the rect has the box's pixel aspect
    a_box = (nbox[2] - nbox[0]) * 1080 / ((nbox[3] - nbox[1]) * 1920)
    a_rect = (rect[2] - rect[0]) * 1920 / ((rect[3] - rect[1]) * 1080)
    assert abs(a_box - a_rect) < .02


def test_a_call_short_is_framed_on_the_call_and_every_face_kept_whole():
    keep = [[10.0, 28.0], [33.0, 40.0], [50.0, 90.0]]
    frame, why = sl.frame_for(LAYOUT, keep, "9:16")
    Frame.model_validate(frame)
    assert frame["mode"] == "crop" and abs(frame["focus_x"] - .5) < .01
    assert "call layout" in why
    track = frame["focus_track"]
    calls = [sp for sp in track if sp["t0"] < 28 or sp["t0"] >= 43]
    assert calls and all(abs(sp["x"] - .5) < .01 for sp in calls)
    # a stage shot: a host no detector sees may walk it — fitted whole
    cam = next(sp for sp in track if 32 <= sp["t0"] < 43.41)
    assert cam["mode"] == "pad_blur" and "x" not in cam
    assert "1 other shot is fitted whole" in why
    # the spans stay inside the kept footage's reach
    assert min(sp["t0"] for sp in track) >= 9.5
    assert max(sp["t1"] for sp in track) <= 90.5


def test_a_face_no_crop_can_keep_whole_is_fitted():
    lay = dict(CAMERA, spans=[{"t0": 0.0, "t1": 60.0, "kind": "camera",
                               "win": None, "faces": [[.30, .3, .45, .6],
                                                      [.40, .2, .80, .7]]}])
    frame, why = sl.frame_for(lay, [[5.0, 50.0]], "9:16")
    assert frame == {"ratio": "9:16", "mode": "pad_blur"}
    assert "fitted" in why


def test_an_ordinary_short_gets_one_aim_and_the_editor_measures_the_rest():
    frame, why = sl.frame_for(CAMERA, [[10.0, 40.0]], "9:16")
    Frame.model_validate(frame)
    assert frame["mode"] == "crop" and "focus_track" not in frame
    assert abs(frame["focus_x"] - .475) < .02
    two_shot = [[121.0, 138.0]]
    frame, _ = sl.frame_for(LAYOUT, two_shot, "9:16")
    # the second face stays wholly outside the crop
    cw = sl.crop_width(1920, 1080)
    assert frame["focus_x"] + cw / 2 <= .67 + 1e-3
    assert sl.frame_for(None, [[0.0, 9.0]])[0] == {"ratio": "9:16",
                                                   "mode": "pad_blur"}


def test_the_report_names_the_call_its_chrome_the_self_view_and_the_cards():
    text = sl.report(LAYOUT)
    assert text.startswith("SOURCE LAYOUT")
    for want in ("CALL window", "status bar down to y 0.10",
                 "call buttons from y 0.84", "self-view", "erase_region",
                 "Largest card at <= 2x", "not viable", "1.78x"):
        assert want in text, want
    short = sl.report(LAYOUT, keep=[[20.0, 36.0]],
                      to_program=lambda t: t - 20.0)
    assert "Not the call" in short and "8.4-12.0s (other)" in short
    assert "start=20.00, end=28.43" in short
    assert "no call window" in sl.report(CAMERA)
    assert "CALL" not in sl.report(CAMERA)
    assert "shows no call window" in sl.report(LAYOUT, keep=[[122.0, 130.0]])
    assert sl.child_line(LAYOUT, [[50.0, 90.0]]).startswith("100% call window")
    assert sl.child_line(CAMERA, [[0.0, 50.0]]) == ""


# ── the tools ─────────────────────────────────────────────────────────────

SRC = 200.0


class _Ctx:
    def __init__(self, layout, keep=((50.0, 60.0), (70.0, 80.0)), frame=True):
        self.has_main_video = True
        self.workdir = None
        self.duration = SRC
        self.project = {"id": 7, "parent_project_id": 3}
        self.project_id = 7
        self.index = {"video": {"width": 1920, "height": 1080, "fps": 30,
                                "duration": SRC},
                      "source_layout": layout}
        e = default_edl(SRC)
        e["keep"] = [list(k) for k in keep]
        if frame:
            e["frame"] = {"ratio": "9:16", "mode": "crop"}
        self._edl = validate_edl(e, SRC).model_dump()
        self.written = []

    def latest_edl(self):
        return {"json": self._edl, "version": len(self.written) + 1}

    def write_edl(self, edl, desc):
        self._edl = validate_edl(dict(edl), SRC).model_dump()
        self.written.append(desc)
        return f"EDL v{len(self.written) + 1}: {desc}"

    def proxy_path(self):
        raise RuntimeError("no proxy in this test")


def test_an_auto_card_on_a_call_shows_the_calls_picture():
    ctx = _Ctx(LAYOUT)
    res = agent_tools.set_picture_card(ctx, "c", 0, 9,
                                       box=[0.06, 0.175, 0.94, 0.794])
    card = ctx._edl["effects"]["picture_cards"][0]
    assert "CALL LAYOUT" in res and card["fit"] == "crop"
    assert sl._inside(card["source"], sl.usable(CALL), 2e-3)
    w_box = (card["box"][2] - card["box"][0]) * 1080
    w_src = (card["source"][2] - card["source"][0]) * 1920
    assert w_box / w_src <= 2.0 + 1e-2
    assert "CUT by this card" in res and "erase_region" in res
    assert not card.get("follow") and not card.get("source_track")


def test_an_auto_card_with_no_box_takes_the_largest_call_card():
    ctx = _Ctx(LAYOUT)
    res = agent_tools.set_picture_card(ctx, "c", 0, 9)
    card = ctx._edl["effects"]["picture_cards"][0]
    plan = sl.card_plan(CALL, 1920, 1080)
    assert "no box given" in res
    assert max(abs(a - b) for a, b in zip(card["box"], plan["box"])) < 2e-3


def test_an_erased_self_view_is_not_asked_for_again():
    ctx = _Ctx(LAYOUT)
    p = CALL["pip"]
    ctx._edl["patches"] = [{"id": "pa1", "asset_key": "patches/7/x.mp4",
                            "fp": "x", "src_start": 49.0, "src_end": 81.0,
                            "regions": [{"id": "er1", "x": p[0] - .002,
                                         "y": p[1] - .002,
                                         "w": p[2] - p[0] + .004,
                                         "h": p[3] - p[1] + .004,
                                         "fill": "box", "start": 49.0,
                                         "end": 81.0, "kind": None}]}]
    res = agent_tools.set_picture_card(ctx, "c", 0, 9,
                                       box=[0.06, 0.175, 0.94, 0.794])
    assert "CALL LAYOUT" in res and "CUT by this card" not in res


def test_an_auto_card_on_an_ordinary_source_is_unchanged():
    ctx = _Ctx(CAMERA, keep=((10.0, 20.0), (25.0, 35.0)))
    res = agent_tools.set_picture_card(ctx, "c", 0, 9,
                                       box=[.04, .1, .96, .5])
    assert "CALL LAYOUT" not in res


def test_auto_reframe_on_a_call_crops_on_the_call_per_shot():
    ctx = _Ctx(LAYOUT, keep=((20.0, 28.0), (33.0, 40.0), (50.0, 60.0)),
               frame=False)
    res = agent_tools.auto_reframe(ctx, "9:16")
    frame = ctx._edl["frame"]
    assert frame["ratio"] == "9:16" and frame["mode"] == "crop"
    assert abs(frame["focus_x"] - .5) < .01 and frame["focus_track"]
    assert "MEASURED SOURCE LAYOUT" in res and "Largest card" in res
    assert not frame.get("follow")


def test_auto_reframe_on_an_ordinary_source_does_not_take_the_layout_path():
    ctx = _Ctx(CAMERA, frame=False)
    assert agent_tools._layout_reframe(ctx, "9:16", "auto") is None
    assert agent_tools._layout_reframe(_Ctx(LAYOUT), "16:9", "auto") is None
    assert agent_tools._layout_reframe(_Ctx(LAYOUT), "9:16", "pad_blur") is None
    assert agent_tools._layout_reframe(_Ctx(LAYOUT), "9:16", "crop") is None


def test_the_report_reaches_a_short_in_program_seconds():
    ctx = _Ctx(LAYOUT, keep=((20.0, 36.0),))
    text = agent_tools._layout_report(ctx)
    assert "SOURCE LAYOUT" in text and "8.4-12.0s (other)" in text
    assert agent_tools._layout_report(_Ctx(None)) == ""


def test_a_short_is_seeded_with_a_frame_once():
    import shorts
    ctx = _Ctx(LAYOUT, keep=((50.0, 90.0),), frame=False)
    version, note = shorts._seed_frame(ctx)
    frame = ctx._edl["frame"]
    assert frame["ratio"] == "9:16" and frame["mode"] == "crop"
    assert "call layout" in note and version == 2
    assert shorts._seed_frame(ctx) is None
    # no layout measured: fitted, never none
    ctx = _Ctx(None, frame=False)
    shorts._seed_frame(ctx)
    assert ctx._edl["frame"]["ratio"] == "9:16"
    assert ctx._edl["frame"]["mode"] == "pad_blur"


def test_a_stored_layout_is_read_not_measured_again():
    import shorts
    index = {"source_layout": LAYOUT}
    assert shorts._source_layout_for(None, {"id": 1}, {}, {}, index,
                                     "/nonexistent") is LAYOUT

    class _Db:
        def run(self, *a, **k):
            raise AssertionError("must not touch the database")
    row = {"json": {"source_layout": LAYOUT}, "video_sha256": "x"}
    assert sl.get_or_compute_for_index(_Db(), None, row, "/nope") is LAYOUT


def test_a_shot_with_a_face_in_a_quarter_of_its_samples_is_a_camera_shot():
    rows = [{"t": float(t), "faces": [[.4, .3, .5, .5]] if t % 4 == 0 else [],
             "win": None} for t in range(8)]
    spans = sl._spans(rows, [{"start": 0, "end": 8}], 8)
    assert spans[0]["kind"] == "camera"
    rows = [{"t": float(t), "faces": [], "win": None} for t in range(8)]
    spans = sl._spans(rows, [{"start": 0, "end": 8}], 8)
    assert spans[0]["kind"] == "other"
    # the index's own face track is a second look at the same shot
    sl.add_spatial(spans, {"samples": [{"t": 1.0, "faces": [[.4, .3, .5, .5]]},
                                       {"t": 5.0, "faces": []},
                                       {"t": 20.0, "faces": [[.1, .1, .2, .2]]}]})
    assert spans[0]["kind"] == "camera" and spans[0]["faces"] == [[.4, .3, .5, .5]]


def test_footage_no_sample_reached_borrows_the_shots_beside_it():
    lay = dict(CAMERA, spans=[{"t0": 0.0, "t1": 20.0, "kind": "camera",
                               "win": None, "faces": [[.40, .25, .55, .52]]},
                              {"t0": 120.0, "t1": 130.0, "kind": "camera",
                               "win": None, "faces": [[.60, .2, .72, .45]]}])
    frame, _why = sl.frame_for(lay, [[30.0, 45.0]], "9:16")
    assert frame["mode"] == "crop" and abs(frame["focus_x"] - .475) < .02
    # nothing measured within reach: fitted, never a guessed crop
    assert sl.frame_for(lay, [[60.0, 70.0]])[0]["mode"] == "pad_blur"


def test_a_short_clip_is_sampled_lightly():
    assert len(sl.sample_times(60.0, [{"start": 0, "end": 60}])) <= 30
    assert len(sl.sample_times(6.0, [{"start": 0, "end": 6}])) >= 1


def test_an_ordinary_source_adds_nothing_where_agents_read_every_turn():
    ctx = _Ctx(CAMERA, keep=((10.0, 20.0),))
    assert agent_tools._layout_report(ctx, calls_only=True) == ""
    assert "no call window" in agent_tools._layout_report(ctx)
    assert "CALL window" in agent_tools._layout_report(_Ctx(LAYOUT),
                                                      calls_only=True)


def test_a_low_resolution_short_starts_as_a_window_not_a_smeared_crop():
    lay = dict(CAMERA, w=646, h=480)
    frame, why = sl.frame_for(lay, [[10.0, 40.0]], "9:16", 646, 480)
    assert frame == {"ratio": "9:16", "mode": "pad"} and "window" in why
    # a 720p 16:9 podcast still fills the phone (the fit is a thin strip)
    frame, _ = sl.frame_for(dict(CAMERA, w=1280, h=720), [[10.0, 40.0]],
                            "9:16", 1280, 720)
    assert frame["mode"] == "crop"


# ── review fixes (Oct 11 2026) ────────────────────────────────────────────

def test_one_aim_that_leaves_a_shots_speaker_out_is_fitted_instead():
    # a two-camera podcast: the host's close-up frames him left, the
    # guest's frames her right; an aim on either leaves the other's shots
    # with nobody in the crop (palantir-karp 560-584 s, a real seed)
    lay = dict(CAMERA, spans=[
        {"t0": 0.0, "t1": 14.0, "kind": "camera", "win": None,
         "faces": [[.45, .09, .62, .38]]},
        {"t0": 14.0, "t1": 20.0, "kind": "camera", "win": None,
         "faces": [[.79, .32, .89, .50]]},
        {"t0": 20.0, "t1": 29.0, "kind": "camera", "win": None,
         "faces": [[.49, .19, .62, .42]]}])
    frame, why = sl.frame_for(lay, [[0.0, 29.0]], "9:16")
    assert frame == {"ratio": "9:16", "mode": "pad_blur"}
    assert "each shot's speaker" in why
    # a shot the short only grazes (under a second) does not decide it
    frame, _ = sl.frame_for(lay, [[0.0, 14.5]], "9:16")
    assert frame["mode"] == "crop"


def test_a_call_shorts_unsampled_shot_beside_a_stage_shot_is_fitted():
    # a long source samples about every other shot: the seconds no sample
    # covers would take the frame's base aim (cropped on the call), and a
    # stage wide there can cut the host in half
    spans = [dict(s) for s in SPANS]
    spans[3] = dict(spans[3], t0=50.0)          # 43.41-50 unsampled
    lay = dict(LAYOUT, spans=spans)
    frame, _why = sl.frame_for(lay, [[30.0, 90.0]], "9:16")
    Frame.model_validate(frame)
    gap = [r for r in frame["focus_track"]
           if r["t0"] <= 45.0 < r["t1"]]
    assert gap and gap[0]["mode"] == "pad_blur"
    # an unsampled shot with the call cropped on both sides stays the call
    spans = [dict(s) for s in SPANS[:1]] + [
        {"t0": 40.0, "t1": 121.75, "kind": "call", "win": "call1",
         "faces": [[0.43, 0.37, 0.63, 0.72]]}]
    frame, _ = sl.frame_for(dict(LAYOUT, spans=spans), [[10.0, 60.0]])
    Frame.model_validate(frame)
    assert all(r["mode"] == "crop" for r in frame["focus_track"])
    assert not any(r["t0"] <= 35.0 < r["t1"] for r in frame["focus_track"])


def test_a_square_or_landscape_call_card_never_cuts_the_guests_face():
    # the Looks' 1:1 cards on the measured call cut the forehead; a
    # landscape card cut half the face — the box narrows until it is whole
    for box in ([0, 0.21, 1, 0.773], [0.04, 0.12, 0.96, 0.637],
                [0.04, 0.3, 0.96, 0.6]):
        nbox, rect, k, ok, _pip = sl.card_for_box(CALL, 1920, 1080, box)
        assert ok is True and sl._inside(CALL["face"], rect, .002), box
        assert k <= sl.UPSCALE_CAP + 1e-6
        assert nbox[1] >= box[1] - 1e-3 and nbox[3] <= box[3] + 1e-3
        assert nbox[2] - nbox[0] < box[2] - box[0]
        a_box = (nbox[2] - nbox[0]) * 1080 / ((nbox[3] - nbox[1]) * 1920)
        a_rect = (rect[2] - rect[0]) * 1920 / ((rect[3] - rect[1]) * 1080)
        assert abs(a_box - a_rect) < .02
    ctx = _Ctx(LAYOUT)
    res = agent_tools.set_picture_card(ctx, "c", 0, 9,
                                       box=[0.04, 0.3, 0.96, 0.6])
    assert "face stays whole" in res


def test_an_unusable_layout_still_seeds_a_fitted_frame():
    import shorts
    broken = dict(LAYOUT, windows=[dict(CALL, rect=None)])
    ctx = _Ctx(broken, keep=((50.0, 90.0),), frame=False)
    version, note = shorts._seed_frame(ctx)
    assert ctx._edl["frame"]["ratio"] == "9:16"
    assert ctx._edl["frame"]["mode"] == "pad_blur" and "unusable" in note
