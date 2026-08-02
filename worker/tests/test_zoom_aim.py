"""Round 72 — aimed zooms that actually frame their subject.

Motivated by a real launch-video session: "zoom into the agent message"
became add_zoom(cx=0.13, cy=0.48, strength=0.65) for a chat message that sat
at x 0.04-0.27, y 0.70-0.86 of the frame — the render showed the message
clipped at the frame edge while the shot centred on empty UI, the visual
self-check said "looks clean", and the user filed it as "look at the zoom
its doing to the message". The pin semantics make that outcome inevitable
for an edge subject (the aimed point HOLDS its screen position; it never
slides to centre), so the fix is not a better guess — it is doing the
viewport math for the agent and letting it see the result:

  * add_zoom rect=[x0,y0,x1,y1] solves strength + pin centre so the region
    is provably inside the rendered viewport;
  * renderer.zoom_state_at / fit_fractions are python mirrors of the emitted
    zoompan + fit chains — what look_at(output_times) crops frames through,
    so a zoom's framing is visible BEFORE a render;
  * sheets.overlay_coord_grid burns a tenths grid on every delivered look
    frame, so an aim is read off labels, not estimated.

Run:  python -m pytest tests/test_zoom_aim.py -q     (from worker/)
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_tools                                            # noqa: E402
import renderer                                               # noqa: E402
import sheets                                                 # noqa: E402
import travel                                                 # noqa: E402
from schemas import default_edl, validate_edl                 # noqa: E402
from timeline import Timeline                                 # noqa: E402

SRC = 354.6

# The real message box, measured off the launch video's rendered frame.
MSG_RECT = [0.038, 0.700, 0.266, 0.861]


class _Ctx:
    def __init__(self, edl, duration=SRC):
        self.project_id = 1
        self.duration = duration
        self.index = {"video": {"duration": duration, "width": 3840,
                                "height": 2160, "fps": 30},
                      "words": [], "sentences": []}
        self.written = []
        self._edl = validate_edl(edl, duration).model_dump()

    def latest_edl(self):
        return {"version": len(self.written) + 1, "json": self._edl}

    def write_edl(self, edl, desc):
        self._edl = validate_edl(dict(edl), self.duration).model_dump()
        self.written.append(desc)
        return f"EDL v{len(self.written)}: {desc}"


def _session_edl():
    """The launch-video shape: 3.55s of footage + a long insert, so the
    program comfortably covers the 7.5-9.5s zoom window."""
    e = default_edl(SRC)
    e["keep"] = [[113.7, 117.25]]
    e["inserts"] = [{"id": "ins2", "asset_key": "clips/1/rec.mov",
                     "kind": "video", "at_output_s": 3.55,
                     "duration_s": 14.62, "source_start_s": 0.0}]
    return e


def _zoom(ctx):
    return ctx.latest_edl()["json"]["effects"]["zooms"][-1]


def _viewport(z, cx, cy):
    vx0 = (1.0 - 1.0 / z) * cx
    vy0 = (1.0 - 1.0 / z) * cy
    return vx0, vy0, vx0 + 1.0 / z, vy0 + 1.0 / z


# ---------------------------------------------------------------- rect ----

def test_rect_frames_the_launch_video_message():
    """THE regression: the rect that produced the bad shot must end up
    provably inside the rendered viewport at full strength."""
    ctx = _Ctx(_session_edl())
    res = agent_tools.add_zoom(ctx, 7.5, 9.5, mode="ease", rect=MSG_RECT)
    assert res.startswith("EDL v"), res
    zm = _zoom(ctx)
    # box is 0.228 wide -> fit with margin solves 3.54x (under the cap now)
    assert abs(zm["strength"] - 2.54) < 1e-6
    assert abs(zm["cx"] - 0.015) < 0.002
    assert abs(zm["cy"] - 0.891) < 0.002
    assert zm["rect"] == [round(v, 3) for v in MSG_RECT]
    # mid-window, the eased zoom is at full strength; the message's box must
    # sit entirely inside the viewport the renderer will show
    z, cx, cy = renderer.zoom_state_at([zm], 8.5, 18.17)
    assert abs(z - 3.54) < 1e-6
    vx0, vy0, vx1, vy1 = _viewport(z, cx, cy)
    assert vx0 <= MSG_RECT[0] and vx1 >= MSG_RECT[2]
    assert vy0 <= MSG_RECT[1] and vy1 >= MSG_RECT[3]
    # and the reply states the achieved shot, not just the request
    assert "on screen the region lands" in res


def test_rect_keeps_explicit_strength_and_reports_a_misfit():
    ctx = _Ctx(_session_edl())
    res = agent_tools.add_zoom(ctx, 7.5, 9.5, strength=0.2,
                               rect=[0.0, 0.0, 0.9, 0.9])
    assert _zoom(ctx)["strength"] == 0.2         # honored, not overridden
    assert "does NOT fully fit" in res
    ctx2 = _Ctx(_session_edl())
    res2 = agent_tools.add_zoom(ctx2, 7.5, 9.5, strength=0.3,
                                rect=[0.0, 0.0, 0.1, 0.1])
    assert _zoom(ctx2)["strength"] == 0.3
    assert "does NOT fully fit" not in res2      # 0.1 box fits a 1.3x window


def test_rect_rejections():
    ctx = _Ctx(_session_edl())
    assert "REJECTED" in agent_tools.add_zoom(
        ctx, 7.5, 9.5, cx=0.2, rect=MSG_RECT)            # two answers
    assert "REJECTED" in agent_tools.add_zoom(
        ctx, 7.5, 9.5, rect=[0.5, 0.5, 0.4, 0.6])        # inverted
    assert "REJECTED" in agent_tools.add_zoom(
        ctx, 7.5, 9.5, mode="follow", rect=MSG_RECT)     # follow aims by path
    assert not ctx.written


def test_plain_cxcy_still_pins_and_the_reply_says_so():
    """cx/cy semantics are untouched (old EDLs and cached renders depend on
    them) — but the reply now states what pinning means and points at rect."""
    ctx = _Ctx(_session_edl())
    res = agent_tools.add_zoom(ctx, 7.5, 9.5, strength=0.65, mode="ease",
                               cx=0.13, cy=0.48)
    zm = _zoom(ctx)
    assert zm["cx"] == 0.13 and zm["cy"] == 0.48 and zm["strength"] == 0.65
    assert not zm.get("rect")
    assert "HOLDS its screen position" in res and "rect=[x0,y0,x1,y1]" in res


def test_default_strength_without_rect_is_unchanged():
    ctx = _Ctx(_session_edl())
    agent_tools.add_zoom(ctx, 7.5, 9.5)
    assert _zoom(ctx)["strength"] == 0.15        # the round-67 default


def test_strength_cap_is_now_4_5():
    """Round 76: excluding a NEIGHBOURING chat bubble from a close-up
    needs ~4.2 (bubbles sit ~0.01 apart). 4.5 (5.5x) is the ceiling."""
    ctx = _Ctx(_session_edl())
    agent_tools.add_zoom(ctx, 7.5, 9.5, strength=9.0)
    assert _zoom(ctx)["strength"] == 4.5
    ctx2 = _Ctx(_session_edl())
    agent_tools.add_zoom(ctx2, 7.5, 9.5, strength=2.0, cx=0.0, cy=0.9)
    assert _zoom(ctx2)["strength"] == 2.0        # far under the ceiling


# ------------------------------------------------- zoom_state_at mirror ----

def test_zoom_state_at_mirrors_the_static_modes():
    punch = {"id": "z1", "start": 2.0, "end": 4.0, "strength": 0.5,
             "cx": 0.8, "cy": 0.5}
    z, cx, cy = renderer.zoom_state_at([punch], 3.0, 20.0)
    assert abs(z - 1.5) < 1e-9 and abs(cx - 0.8) < 1e-9 and cy == 0.5
    assert renderer.zoom_state_at([punch], 4.0, 20.0)[0] == 1.5   # inclusive
    assert renderer.zoom_state_at([punch], 4.01, 20.0) == (1.0, 0.5, 0.5)
    ease = {"id": "z2", "start": 7.5, "end": 9.5, "strength": 1.2,
            "mode": "ease"}
    assert renderer.zoom_state_at([ease], 7.5, 20.0)[0] == 1.0    # ramp edge
    assert abs(renderer.zoom_state_at([ease], 7.7, 20.0)[0] - 1.6) < 1e-9
    assert abs(renderer.zoom_state_at([ease], 8.5, 20.0)[0] - 2.2) < 1e-9
    push = {"id": "z3", "start": 0.0, "end": 4.0, "strength": 0.8,
            "mode": "push_in"}
    assert abs(renderer.zoom_state_at([push], 1.0, 20.0)[0] - 1.2) < 1e-9
    pull = dict(push, mode="pull_out")
    assert abs(renderer.zoom_state_at([pull], 1.0, 20.0)[0] - 1.6) < 1e-9


def test_zoom_state_at_adds_overlapping_terms_and_clamps_centre():
    a = {"id": "z1", "start": 0.0, "end": 10.0, "strength": 0.4, "cx": 0.9}
    b = {"id": "z2", "start": 0.0, "end": 10.0, "strength": 0.3, "cx": 0.9}
    z, cx, _cy = renderer.zoom_state_at([a, b], 5.0, 20.0)
    assert abs(z - 1.7) < 1e-9
    assert cx == 1.0            # 0.5 + 0.4 + 0.4 clamps like zoompan's crop


def test_zoom_state_at_mirrors_the_travelling_shapes():
    pts, err = travel.waypoints_to_path(
        [{"t": 2.0, "cx": 0.2, "cy": 0.5, "strength": 0.0},
         {"t": 4.0, "cx": 0.8, "cy": 0.5, "strength": 0.8},
         {"t": 6.0, "cx": 0.5, "cy": 0.5, "strength": 0.0}],
        2.0, 6.0, with_strength=True)
    assert not err
    path = {"id": "zp1", "start": 2.0, "end": 6.0, "strength": 0.25,
            "mode": "path", "path": pts, "ease": "cubic_in_out"}
    z, cx, _ = renderer.zoom_state_at([path], 4.0, 20.0)
    assert abs(z - 1.8) < 1e-6 and abs(cx - 0.8) < 1e-6   # settles at the kf
    z, cx, _ = renderer.zoom_state_at([path], 3.0, 20.0)
    assert abs(z - 1.4) < 1e-6 and abs(cx - 0.5) < 1e-6   # eased midpoint
    follow = {"id": "zf1", "start": 10.0, "end": 14.0, "strength": 0.4,
              "mode": "follow",
              "path": [{"f": 0.0, "cx": 0.2, "cy": 0.5},
                       {"f": 1.0, "cx": 0.8, "cy": 0.5}]}
    assert renderer.zoom_state_at([follow], 10.0, 20.0)[0] == 1.0
    z, cx, _ = renderer.zoom_state_at([follow], 13.0, 20.0)
    assert abs(z - 1.4) < 1e-9
    assert abs(cx - 0.65) < 1e-6          # legacy linear interpolation


# ------------------------------------------------------ fit_fractions ----

def test_fit_fractions_mirrors_the_fit_chains():
    # a 16:10-ish Mac recording cover-cropped into a 16:9 canvas loses its
    # top and bottom edges symmetrically
    kind, x0, y0, x1, y1 = renderer.fit_fractions(3456, 2234, 3840, 2160)
    assert kind == "crop" and x0 == 0.0 and x1 == 1.0
    fh = (3456 / 2234) / (3840 / 2160)
    assert abs((y1 - y0) - fh) < 1e-9 and abs(y0 - (1 - fh) / 2) < 1e-9
    # a wide source loses its sides; focus_x drags the crop window and the
    # clamp keeps it inside the frame, exactly like the clip() expressions
    kind, x0, _y0, x1, _y1 = renderer.fit_fractions(
        2350, 1000, 1920, 1080, "crop", (0.95, None))
    fw = (1920 / 1080) / 2.35
    assert kind == "crop" and abs(x1 - 1.0) < 1e-9 and abs((x1 - x0) - fw) < 1e-9
    # pad letterboxes the whole frame with symmetric bars
    kind, x0, y0, x1, y1 = renderer.fit_fractions(1080, 1920, 3840, 2160,
                                                  "pad")
    assert kind == "pad" and y0 == 0.0 and y1 == 1.0
    fw = (1080 / 1920) / (3840 / 2160)
    assert abs((x1 - x0) - fw) < 1e-9 and abs(x0 - (1 - fw) / 2) < 1e-9
    # same aspect: identity, whatever the mode
    assert renderer.fit_fractions(1920, 1080, 3840, 2160) == \
        ("crop", 0.0, 0.0, 1.0, 1.0)
    assert renderer.fit_fractions(1920, 1080, 3840, 2160, "pad") == \
        ("pad", 0.0, 0.0, 1.0, 1.0)


# ------------------------------------------- the visible end-to-end tie ----

def test_look_geometry_shows_the_framed_message():
    """Draw the message as a white box on a black frame, run the exact
    (fit + zoom) step look_at(output_times) runs with the zoom the rect
    solver wrote — the box must end up large and on screen, and the label
    must say a zoom was applied."""
    from PIL import Image

    ctx = _Ctx(_session_edl())
    agent_tools.add_zoom(ctx, 7.5, 9.5, mode="ease", rect=MSG_RECT)
    zm = _zoom(ctx)
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "frame.jpg")
        img = Image.new("RGB", (640, 360), (0, 0, 0))
        px = img.load()
        for x in range(int(MSG_RECT[0] * 640), int(MSG_RECT[2] * 640)):
            for y in range(int(MSG_RECT[1] * 360), int(MSG_RECT[3] * 360)):
                px[x, y] = (255, 255, 255)
        img.save(src, "JPEG", quality=95)
        out, suffix = agent_tools._fit_and_zoom_frame(
            td, 0, src, 8.5, (3840, 2160), "crop", None, [zm], 18.17, False)
        assert "3.54x zoom" in suffix
        assert out != src
        res = Image.open(out).convert("L")
        # the box centre lands dead centre at the 3.5x fit ...
        assert res.load()[int(0.50 * 640), int(0.50 * 360)] > 200
        # ... and the far corner, which the old pinned zoom filled with the
        # subject-less UI, is not where the box is
        assert res.load()[int(0.95 * 640), int(0.05 * 360)] < 60


def test_zoom_path_keyframes_take_rects():
    """Round 75 — the launch-video choreography: hold on one message, glide
    to the prompt, ease out. Each keyframe names the THING; the shared
    solver derives the pin — the arithmetic the agent used to hand-derive
    (and get wrong) for every travelling zoom between edge subjects."""
    ctx = _Ctx(_session_edl())
    prompt_rect = [0.19, 0.79, 0.30, 0.87]
    res = agent_tools.add_zoom_path(ctx, [
        {"t": 6.6, "rect": MSG_RECT, "strength": 0},
        {"t": 7.4, "rect": MSG_RECT},
        {"t": 10.8, "rect": prompt_rect},
        {"t": 12.0, "rect": prompt_rect, "strength": 0},
    ])
    assert res.startswith("EDL v"), res
    zp = ctx.latest_edl()["json"]["effects"]["zooms"][-1]
    pts = zp["path"]
    # rect keyframes with no strength solve to each rect's own fit (the
    # message 2.54, the smaller prompt caps at 4.5); explicit 0 stays 0
    assert [p["s"] for p in pts] == [0.0, 2.54, 4.5, 0.0]
    assert abs(pts[0]["cx"] - 0.015) < 0.002
    assert abs(pts[0]["cy"] - 0.891) < 0.002
    assert abs(pts[2]["cx"] - 0.188) < 0.002
    assert abs(pts[2]["cy"] - 0.903) < 0.002
    # at the prompt keyframe the rendered viewport contains the prompt
    z, cx, cy = renderer.zoom_state_at([zp], 10.8, 18.17)
    assert abs(z - 5.5) < 1e-6
    vx0, vy0, vx1, vy1 = _viewport(z, cx, cy)
    assert vx0 <= prompt_rect[0] and vx1 >= prompt_rect[2]
    assert vy0 <= prompt_rect[1] and vy1 >= prompt_rect[3]


def test_zoom_path_rect_rejections():
    ctx = _Ctx(_session_edl())
    r = agent_tools.add_zoom_path(ctx, [
        {"t": 1, "rect": MSG_RECT, "cx": 0.5, "cy": 0.5},
        {"t": 2, "rect": MSG_RECT}])
    assert "both rect and cx/cy" in r
    r = agent_tools.add_zoom_path(ctx, [
        {"t": 1, "rect": [0.5, 0.5, 0.4, 0.6]},
        {"t": 2, "rect": MSG_RECT}])
    assert r.startswith("REJECTED: keyframes[0]:")
    assert not ctx.written


def test_taste_adjacent_zooms_across_a_cut_are_deliberate():
    """Round 75: the audit's back-to-back-pushes rule made the agent DELETE
    a zoom the user explicitly asked to keep — the pair straddled a scene
    cut, where cut-plus-punch is a standard deliberate move. Same pair on
    continuous footage still fires."""
    import taste
    index = {"words": [{"w": f"w{i}", "t0": 0.5 + i * 0.4,
                        "t1": 0.8 + i * 0.4} for i in range(30)],
             "video": {"width": 1080, "height": 1920},
             "shots": [{"t0": 0.0, "t1": 15.0}]}
    zooms = [{"id": "z1", "start": 7.6, "end": 9.97, "strength": 0.15,
              "mode": "ease", "cx": 0.0, "cy": 1.0},
             {"id": "z2", "start": 10.0, "end": 12.0, "strength": 0.15,
              "mode": "ease", "cx": 0.0, "cy": 1.0}]
    keep = [[0.0, 10.0]]
    ins = [{"id": "i1", "asset_key": "clips/1/a.mov", "kind": "video",
            "at_output_s": 10.0, "duration_s": 5.0}]
    edl = {"keep": keep, "inserts": ins,
           "effects": {"zooms": [dict(z) for z in zooms]},
           "captions": {"mode": "from_transcript"}}
    f = taste.critique(edl, index, Timeline(keep, ins, []), 1080, 1920, "")
    assert not any("fight each other" in x for x in f)
    edl2 = {"keep": [[0.0, 15.0]], "inserts": [],
            "effects": {"zooms": [dict(z) for z in zooms]},
            "captions": {"mode": "from_transcript"}}
    f2 = taste.critique(edl2, index, Timeline([[0.0, 15.0]], [], []),
                        1080, 1920, "")
    assert any("fight each other" in x for x in f2)


def test_grid_overlay_is_visible_and_size_preserving():
    from PIL import Image

    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "black.jpg")
        Image.new("RGB", (640, 360), (0, 0, 0)).save(src, "JPEG")
        dst = sheets.overlay_coord_grid(src, os.path.join(td, "grid.jpg"))
        g = Image.open(dst)
        assert g.size == (640, 360)
        lum = g.convert("L").load()
        # the brighter midline at x=0.5 stands out against untouched black
        assert lum[320, 200] > lum[300, 200] + 20
        assert lum[300, 180] > lum[300, 200] + 10   # y midline too
