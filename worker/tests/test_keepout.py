"""Face and safe-area keep-out for motion graphics (worker/keepout.py).

Showcase judging: the '140' counter sat across Peter Thiel's mouth for the
whole payoff (the push-in made it worse), the 'Elon Musk' lower third ran
across his mouth and chin, and slams reached 92-97% of the frame width into
Instagram's right button rail. These tests pin the geometry (source face ->
output through the crop, focus track, zoom and picture card), the solver,
the tool integration and the caption track's step around a graphic, with
synthetic faces and with the showcase EDLs' real timings and measured faces.
"""

import json
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import caption_carry  # noqa: E402
import keepout  # noqa: E402
import motion_captions  # noqa: E402
import motion_engine  # noqa: E402
import motion_templates  # noqa: E402
import motion_tools  # noqa: E402
from schemas import default_edl, edl_signature, validate_edl  # noqa: E402
from timeline import Timeline  # noqa: E402


def _chromium_ok():
    if not motion_engine.available():
        return False
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch(args=motion_engine.CHROME_ARGS).close()
        return True
    except Exception:
        return False


CHROMIUM = _chromium_ok()
needs_browser = pytest.mark.skipif(not (CHROMIUM and shutil.which("ffmpeg")),
                                   reason="headless Chromium + ffmpeg required")


# ── the safe area and the zones ───────────────────────────────────────────

def test_safe_area_names_the_rail_margins_and_bottom_band():
    W, H = 1080, 1920
    assert keepout.safe_issues((0.1, 0.55, 0.86, 0.7), W, H) == []
    # ENOUGH at 92% of the width, in the lower half: the right button rail
    assert keepout.safe_issues((0.08, 0.55, 0.92, 0.73), W, H) == ["rail"]
    # the same width ABOVE y 0.5 is clear of the rail
    assert keepout.safe_issues((0.08, 0.2, 0.92, 0.45), W, H) == []
    assert keepout.safe_issues((0.03, 0.2, 0.97, 0.45), W, H) == ["side"]
    assert keepout.safe_issues((0.2, 0.7, 0.8, 0.86), W, H) == ["bottom"]
    # a landscape frame has no vertical-feed safe area
    assert keepout.safe_issues((0.0, 0.7, 1.0, 1.0), 1920, 1080) == []


def test_face_zone_spans_forehead_to_chin_and_clips_to_the_visible_picture():
    f = (0.3, 0.2, 0.7, 0.5)
    z = keepout.face_zone(f)
    assert z[1] < 0.2 and 0.5 < z[3] < 0.52 and z[0] < 0.3 and z[2] > 0.7
    m = keepout.mouth_zone(f)
    assert 0.3 < m[0] < m[2] < 0.7 and 0.35 < m[1] < m[3] <= 0.5
    # a face cut by a card's top edge has no zone above the card
    clipped = keepout.face_zone(f + (0.04, 0.265, 0.96, 0.685))
    assert clipped[1] == 0.265


def test_filter_faces_drops_background_faces_and_a_chest_under_the_head():
    head = ((0.3, 0.2, 0.46, 0.5), 3)
    chest = ((0.27, 0.49, 0.45, 0.8), 2)          # Thiel's arm, two cascades agree
    background = ((0.8, 0.05, 0.84, 0.1), 1)
    assert keepout.filter_faces([head, chest, background]) == [(0.3, 0.2, 0.46, 0.5)]
    # a lone unconfirmed box beside a confirmed head is dropped; two heads stay
    other = ((0.6, 0.22, 0.75, 0.5), 2)
    assert len(keepout.filter_faces([head, other])) == 2
    assert len(keepout.filter_faces([head, ((0.6, 0.22, 0.75, 0.5), 1)])) == 1


# ── geometry: source face -> output ───────────────────────────────────────

def _edl(keep=((10.0, 30.0),), frame=None, zooms=(), cards=(), dur=60.0):
    edl = default_edl(dur)
    edl["keep"] = [list(k) for k in keep]
    if frame:
        edl["frame"] = frame
    edl["effects"] = {"zooms": [dict(z) for z in zooms]}
    if cards:
        edl["effects"]["picture_cards"] = [dict(c) for c in cards]
    return validate_edl(edl, dur).model_dump()


VIDEO = {"width": 1920, "height": 1080, "duration": 60.0}


def test_geometry_maps_a_landscape_face_through_the_9x16_crop():
    edl = _edl(frame={"ratio": "9:16", "mode": "crop", "focus_x": 0.38})
    geo = keepout.Geometry(edl, VIDEO, 1080, 1920, 20.0)
    b = geo.to_output(1.0, 11.0, (0.32, 0.2, 0.48, 0.5))
    # crop width 0.3164 centred on 0.38 -> x0 0.2218
    assert b[0] == pytest.approx((0.32 - 0.2218) / 0.3164, abs=0.01)
    assert b[2] == pytest.approx((0.48 - 0.2218) / 0.3164, abs=0.01)
    assert b[1] == pytest.approx(0.2) and b[3] == pytest.approx(0.5)
    assert b[4:] == pytest.approx((0, 0, 1, 1))


def test_geometry_follows_the_zoom_at_that_program_second():
    zoom = {"id": "z1", "start": 5.0, "end": 8.0, "strength": 0.1, "mode": "punch",
            "ramp_s": 0, "cx": 0.5, "cy": 0.3}
    edl = _edl(frame={"ratio": "9:16", "mode": "crop", "focus_x": 0.38}, zooms=[zoom])
    geo = keepout.Geometry(edl, VIDEO, 1080, 1920, 20.0)
    before = geo.to_output(4.0, 14.0, (0.32, 0.2, 0.48, 0.5))
    during = geo.to_output(6.0, 16.0, (0.32, 0.2, 0.48, 0.5))
    # the punch grows the face downward from its 0.3 centre
    assert during[3] > before[3] + 0.015
    assert (during[3] - during[1]) == pytest.approx((before[3] - before[1]) * 1.1, abs=0.002)


def test_geometry_places_the_face_inside_an_active_picture_card():
    card = {"id": "c", "start": 0.0, "end": 10.0, "box": [0.04, 0.265, 0.96, 0.685],
            "fit": "crop"}
    edl = _edl(frame={"ratio": "9:16", "mode": "crop"}, cards=[card])
    video = {"width": 646, "height": 480, "duration": 60.0}
    geo = keepout.Geometry(edl, video, 1080, 1920, 20.0)
    b = geo.to_output(1.0, 11.0, (0.454, 0.216, 0.577, 0.382))
    # the card shows the 9:16 program scaled 0.92 and centre-cropped: the
    # brow line sits above the card's top edge (the judged cut-off head)
    assert b[1] == pytest.approx(0.015 + 0.92 * 0.216, abs=0.005)
    assert b[1] < 0.265 < b[3] < 0.685
    assert b[4:] == pytest.approx((0.04, 0.265, 0.96, 0.685))
    assert keepout.face_zone(b)[1] == pytest.approx(0.265)
    # after the card the face is back in the full frame
    assert geo.to_output(12.0, 22.0, (0.454, 0.216, 0.577, 0.382))[4:] == pytest.approx((0, 0, 1, 1))


def _index(samples, video=VIDEO):
    return {"video": dict(video), "spatial": {"v": 1, "samples": samples}}


def test_face_track_borrows_a_nearby_face_and_ignores_a_lone_hand():
    edl = _edl(frame={"ratio": "9:16", "mode": "crop", "focus_x": 0.38})
    head = [0.32, 0.2, 0.48, 0.5]
    samples = [{"t": 10.5, "faces": [head]}, {"t": 11.0, "faces": []},
               {"t": 11.5, "faces": [[0.27, 0.49, 0.45, 0.8]]},     # the cascade saw a hand
               {"t": 12.0, "faces": [head]}, {"t": 12.5, "faces": [head]}]
    track = keepout.face_track(edl, _index(samples), 1080, 1920, 0.4, 2.6)
    assert track and all(len(faces) == 1 for _t, faces in track)
    assert all(f[3] <= 0.51 for _t, faces in track for f in faces)   # never the hand


# ── the solver ────────────────────────────────────────────────────────────

FACE = (0.28, 0.17, 0.88, 0.53)               # Thiel's head under the push-in


def test_assess_counts_the_mouth_and_spares_type_tucked_under_the_chin():
    track = [(t / 10.0, [FACE]) for t in range(20)]
    over = keepout.assess((0.2, 0.42, 0.87, 0.58), track)
    assert over["hit"] and over["mouth"] and over["when"] == (0.0, 1.9)
    under = keepout.assess((0.2, 0.56, 0.87, 0.7), track)
    assert not under["hit"]
    # one stray second of contact is not a collision
    brief = [(t / 10.0, [FACE] if t == 3 else []) for t in range(20)]
    assert keepout.assess((0.2, 0.42, 0.87, 0.58), brief)["hit"]          # only face seen
    mostly_clear = [(t / 10.0, [FACE if t == 3 else (0.28, 0.0, 0.88, 0.2)]) for t in range(20)]
    assert not keepout.assess((0.2, 0.42, 0.87, 0.58), mostly_clear)["hit"]


def test_candidates_move_a_counter_below_the_chin_with_its_own_y():
    spec = motion_templates.spec("counter")
    zones = [keepout.face_zone(FACE)]
    mouths = [keepout.mouth_zone(FACE)]
    cands = keepout.candidates("counter", spec, {"y": 0.5, "size": 1.0},
                               (0.2, 0.42, 0.87, 0.58), [], zones, 1080, 1920,
                               mouths=mouths)
    cost, patch, box = cands[0]
    assert box[1] >= zones[0][3] + keepout.CLEARANCE - 0.005
    assert box[3] <= keepout.SAFE_Y1 and not keepout.safe_issues(box, 1080, 1920)
    assert patch.get("y", 0.5) > 0.5
    assert all(not keepout.on_face(b, zones, mouths) for _c, _p, b in cands)


def test_candidates_shift_a_wide_slam_left_of_the_rail_with_its_x():
    spec = motion_templates.spec("word_slam")
    cands = keepout.candidates("word_slam", spec, {"y": 0.64}, (0.081, 0.546, 0.915, 0.735),
                               [], [], 1080, 1920, clear_penalty=0.0)
    cost, patch, box = cands[0]
    assert "x" in patch and patch["x"] < 0.5 and "y" not in patch
    assert box[2] <= keepout.RAIL_X1 + keepout.EDGE_TOL and box[0] >= keepout.SAFE_X0 - keepout.EDGE_TOL


def test_candidates_are_empty_when_the_face_fills_the_frame():
    spec = motion_templates.spec("counter")
    face = (0.0, 0.1, 1.0, 0.75)
    zones = [keepout.face_zone(face)]
    assert keepout.candidates("counter", spec, {"y": 0.5, "size": 0.5},
                              (0.2, 0.42, 0.87, 0.58), [], zones, 1080, 1920,
                              mouths=[keepout.mouth_zone(face)]) == []


# The caption band clear of a graphic and the face is found by the ONE
# caption placement pass (caption_carry.clear_band, over keep-out face zones).
PORTRAIT_SAFE = caption_carry.safe_range(1080, 1920)


def _caption_zone(y, graphics, faces, safe=PORTRAIT_SAFE):
    """(zone_y0, zone_y1, anchor_y), None when the block is already clear,
    False when no band fits."""
    if not caption_carry.collides(graphics, y):
        return None
    band = caption_carry.clear_band(graphics, faces, safe, y)
    return (band["z"][0], band["z"][1], band["y"]) if band else False


def test_caption_zone_steps_around_the_graphic_and_the_face():
    graphic = (0.1, 0.648, 0.8, 0.78)
    face = keepout.face_zone((0.25, 0.2, 0.75, 0.5))
    # clear: nothing to do
    assert _caption_zone(0.4, [graphic], [face]) is None
    z0, z1, y = _caption_zone(0.74, [graphic], [face])
    assert z1 - z0 >= caption_carry.MIN_BAND_H and z0 >= face[3] and z1 <= graphic[1]
    assert z0 + 0.04 <= y <= z1 - 0.04
    # a frame-filling face and a graphic on the band leave no room
    big = keepout.face_zone((0.0, 0.12, 1.0, 0.58))
    assert _caption_zone(0.74, [(0.1, 0.62, 0.9, 0.79)], [big]) is False


# ── the tools ─────────────────────────────────────────────────────────────

def _probe_by_y(height=0.16, width=(0.2, 0.87), shift=0.0):
    """A stand-in probe whose ink follows the params like a centred template:
    centre y = params.y, size scales it."""
    def probe(item, W, H, fps=30.0):
        p = item.get("params") or {}
        s = float(p.get("size", 1.0))
        cy = float(p.get("y", 0.5)) + shift
        cx = (width[0] + width[1]) / 2 + (float(p["x"]) - 0.5 if "x" in p else 0.0)
        hw, hh = (width[1] - width[0]) * s / 2, height * s / 2
        box = [round(cx - hw, 4), round(cy - hh, 4), round(cx + hw, 4), round(cy + hh, 4)]
        return {"errors": [], "visible_frames": 4, "samples": 4, "bboxes": [box], "ink": [box] * 4}
    return probe


class _Ctx:
    project_id = 1
    has_main_video = True

    def __init__(self, edl, index, duration):
        self.duration = duration
        self.index = index
        self._edl = validate_edl(edl, duration).model_dump()
        self.writes = []

    def latest_edl(self):
        return {"version": len(self.writes) + 1, "json": json.loads(json.dumps(self._edl))}

    def write_edl(self, edl, description):
        self._edl = validate_edl(edl, self.duration).model_dump()
        self.writes.append(description)
        return f"EDL v{len(self.writes)} -> v{len(self.writes) + 1}: {description}"


def _thiel_like_ctx():
    edl = default_edl(60.0)
    edl["keep"] = [[10.0, 30.0]]
    edl["frame"] = {"ratio": "9:16", "mode": "crop", "focus_x": 0.38}
    head = [0.32, 0.2, 0.48, 0.5]
    samples = [{"t": 10.0 + k * 0.5, "faces": [head]} for k in range(40)]
    return _Ctx(edl, _index(samples), 60.0)


def test_add_moves_a_counter_off_the_mouth_and_records_the_footprint(monkeypatch):
    monkeypatch.setattr(motion_tools, "_probe_item", _probe_by_y())
    ctx = _thiel_like_ctx()
    out = motion_tools.add_motion_graphic(ctx, "counter", 2.0, 4.0,
                                          params={"value": "140", "y": 0.42}, id="num")
    assert out.startswith("EDL v1"), out
    assert "KEEP-OUT: it covered the speaker's mouth" in out and "below the chin" in out, out
    item = ctx.latest_edl()["json"]["motion"][0]
    assert item["params"]["y"] > 0.55
    fp = item["footprint"]
    assert fp["ar"] == pytest.approx(1080 / 1920, abs=1e-3) and fp["faces"]
    assert fp["box"][1] >= fp["faces"][0][3]                       # below the chin
    assert "Draws within" in out and f"y {fp['box'][1]:.2f}" in out
    # a deliberate design keeps its place, and says nothing about the face
    out = motion_tools.set_motion_graphic(ctx, "num", params={"y": 0.42}, allow_face_overlap=True)
    item = ctx.latest_edl()["json"]["motion"][0]
    assert item["params"]["y"] == 0.42 and item["allow_face_overlap"] is True
    assert "KEEP-OUT" not in out and "keep-out" not in out, out
    # clearing the flag lets the next write move it again
    out = motion_tools.set_motion_graphic(ctx, "num", allow_face_overlap=False)
    item = ctx.latest_edl()["json"]["motion"][0]
    assert "allow_face_overlap" not in item or not item["allow_face_overlap"]
    assert item["params"]["y"] > 0.55 and "KEEP-OUT" in out, out


def test_a_graphic_clear_of_the_face_is_left_alone(monkeypatch):
    monkeypatch.setattr(motion_tools, "_probe_item", _probe_by_y(height=0.1))
    ctx = _thiel_like_ctx()
    out = motion_tools.add_motion_graphic(ctx, "counter", 2.0, 4.0,
                                          params={"value": "140", "y": 0.66}, id="num")
    assert "KEEP-OUT" not in out and "keep-out" not in out, out
    assert ctx.latest_edl()["json"]["motion"][0]["params"]["y"] == 0.66


def test_no_clear_zone_keeps_the_placement_and_says_why(monkeypatch):
    # the probe ignores the params: no move can be verified
    monkeypatch.setattr(motion_tools, "_probe_item", lambda item, W, H, fps=30.0: {
        "errors": [], "visible_frames": 4, "samples": 4,
        "bboxes": [[0.2, 0.3, 0.87, 0.5]], "ink": [[0.2, 0.3, 0.87, 0.5]] * 4})
    ctx = _thiel_like_ctx()
    out = motion_tools.add_motion_graphic(ctx, "counter", 2.0, 4.0,
                                          params={"value": "140", "y": 0.4}, id="num")
    assert out.startswith("EDL v1"), out                           # never a rejection
    assert "NOTE (keep-out)" in out and "no clear zone" in out and "allow_face_overlap" in out, out
    assert ctx.latest_edl()["json"]["motion"][0]["params"]["y"] == 0.4


def test_behind_subject_and_pointing_callouts_are_exempt():
    spec = motion_templates.spec
    assert not keepout.applicable("counter", spec("counter"), "behind_subject")
    assert not keepout.applicable("arrow_callout", spec("arrow_callout"), "above_captions")
    assert not keepout.applicable("flash_transition", spec("flash_transition"), "above_captions")
    assert keepout.applicable("lower_third", spec("lower_third"), "above_captions")


def test_no_face_evidence_and_no_ink_change_nothing(monkeypatch):
    # an index without spatial samples and a probe without ink (older stubs)
    monkeypatch.setattr(motion_tools, "_probe_item", lambda item, W, H, fps=30.0: {
        "errors": [], "visible_frames": 4, "samples": 4, "bboxes": [[0.1, 0.3, 0.86, 0.5]]})
    edl = default_edl(10.0)
    ctx = _Ctx(edl, {"video": {"width": 1080, "height": 1920}}, 10.0)
    out = motion_tools.add_motion_graphic(ctx, "hook_title", 1.0, params={"text": "Hi"})
    assert "KEEP-OUT" not in out and "keep-out" not in out, out
    item = ctx.latest_edl()["json"]["motion"][0]
    assert item["footprint"]["faces"] == [] and item["footprint"]["box"] == [0.1, 0.3, 0.86, 0.5]


# ── schema ────────────────────────────────────────────────────────────────

def test_footprint_and_flag_validate_and_old_edls_keep_their_signature():
    edl = default_edl(10.0)
    edl["motion"] = [{"id": "m1", "template": "counter", "start": 1.0, "end": 3.0,
                      "params": {"value": "5"}}]
    once = validate_edl(json.loads(json.dumps(edl)), 10.0).model_dump()
    old = validate_edl(json.loads(json.dumps(once)), 10.0).model_dump()
    sig = edl_signature(old)
    assert edl_signature(validate_edl(json.loads(json.dumps(old)), 10.0).model_dump()) == sig
    assert '"footprint"' not in sig and '"allow_face_overlap"' not in sig
    edl["motion"][0]["footprint"] = {"box": [0.1, 0.2, 0.9, 1.3], "ar": 0.5625,
                                     "faces": [[0.2, 0.1, 0.8, 0.5]]}
    edl["motion"][0]["allow_face_overlap"] = True
    m = validate_edl(edl, 10.0).model_dump()["motion"][0]
    assert m["footprint"]["box"] == [0.1, 0.2, 0.9, 1.0] and m["allow_face_overlap"] is True
    # a measurement, never an authoring choice: an unusable one is dropped
    # (the renderer measures again), not rejected
    edl["motion"][0]["footprint"]["box"] = [0.1, 0.2]
    assert validate_edl(edl, 10.0).model_dump()["motion"][0]["footprint"] is None


def test_word_slam_x_and_width_default_to_the_historical_layout():
    params = motion_templates.normalize_params("word_slam", {"text": "*enough*"})
    assert params["x"] == 0.5 and params["width"] == 0.85
    params = motion_templates.check_params("word_slam", {"text": "*enough*", "x": 0.47, "width": 0.8})
    assert params["x"] == 0.47 and params["width"] == 0.8


@needs_browser
def test_word_slam_defaults_draw_exactly_the_old_centred_layout():
    base = {"id": "w", "template": "word_slam", "start": 0, "end": 1.5}
    old = motion_templates.check_params("word_slam", {"text": "*enough*", "y": 0.64})
    reps = motion_tools._probe_items([dict(base, params=old),
                                      dict(base, params=dict(old, x=0.47, width=0.8))], 1080, 1920)
    a, b = (keepout.settled_ink(r, motion_tools._probe_times(base)) for r in reps)
    assert (a[0] + a[2]) / 2 == pytest.approx(0.5, abs=0.01)
    assert (b[0] + b[2]) / 2 == pytest.approx(0.47, abs=0.01) and b[2] - b[0] < a[2] - a[0]


# ── captions step around a measured graphic ───────────────────────────────

WORDS = [("they", 0.30, 0.45), ("promised", 0.46, 0.80), ("us", 0.82, 0.95),
         ("flying", 1.00, 1.40), ("cars", 1.42, 1.80), ("and", 2.10, 2.20),
         ("all", 2.22, 2.35), ("we", 2.36, 2.45), ("got", 2.46, 2.60), ("was", 2.62, 2.75),
         ("140", 2.80, 3.30), ("characters.", 3.30, 3.90), ("So", 5.6, 5.7),
         ("that", 5.7, 5.8), ("is", 5.8, 5.9), ("it.", 5.9, 6.3)]


def _caption_edl(footprint=None):
    edl = default_edl(8.0)
    edl["captions"] = {"mode": "from_transcript", "design_version": 2,
                       "style": {"motion_look": "editorial"}}
    item = {"id": "num", "template": "counter", "start": 2.0, "end": 4.0,
            "params": {"value": "140"}, "mute_captions": False}
    if footprint:
        item["footprint"] = footprint
    edl["motion"] = [item]
    edl = validate_edl(edl, 8.0).model_dump()
    index = {"words": [{"w": w, "t0": s, "t1": e} for w, s, e in WORDS],
             "video": {"width": 1920, "height": 1080}}
    return edl, index, Timeline(edl["keep"])


def test_caption_cues_step_off_a_graphic_footprint_and_off_the_face():
    plain = motion_captions.cues(*_caption_edl(), canvas=(1080, 1920))
    fp = {"box": [0.1, 0.648, 0.8, 0.78], "ar": 0.5625, "faces": [[0.25, 0.2, 0.75, 0.5]]}
    moved = motion_captions.cues(*_caption_edl(fp), canvas=(1080, 1920))
    # the same words (the caption plan starts a new card where the band moves)
    assert [w for c in plain for w in c["w"]] == [w for c in moved for w in c["w"]]
    plain_at = {c["s"]: c for c in plain}
    for b in moved:
        if b["s"] < 4.0 and b["e"] > 2.0:
            z0, z1 = b["z"]
            assert z0 >= 0.5 and z1 <= 0.648 and z0 <= b["y"] <= z1, b
            assert b["b"] == "m"
        else:
            # outside the window: placed as before (a line before the graphic
            # never holds into its window)
            a = plain_at[b["s"]]
            assert (b["y"], b["b"]) == (a["y"], a["b"]) and "z" not in b and b["e"] <= a["e"]
    assert any("z" in c for c in moved)
    # a footprint measured on another canvas shape (a later 1:1 reframe) is stale
    stale = dict(fp, ar=1.0)
    assert motion_captions.cues(*_caption_edl(stale), canvas=(1080, 1920)) == plain


def test_caption_items_carry_the_zone_to_the_template(monkeypatch):
    monkeypatch.setattr(motion_engine, "available", lambda: True)
    fp = {"box": [0.1, 0.648, 0.8, 0.78], "ar": 0.5625, "faces": [[0.25, 0.2, 0.75, 0.55]]}
    items = motion_captions.items(*_caption_edl(fp), canvas=(1080, 1920))
    cues = [c for it in items for c in it["params"]["cues"]]
    assert any("z" in c for c in cues) and all(len(c["z"]) == 2 for c in cues if "z" in c)


def test_caption_cues_without_footprints_are_unchanged():
    edl, index, tl = _caption_edl()
    assert all("z" not in c for c in motion_captions.cues(edl, index, tl, canvas=(1080, 1920)))


# ── the showcase shorts: real timings, measured faces ─────────────────────
# Faces measured with keepout.detect_faces on the showcase sources at the
# items' windows (every 0.5 s); keep, frame and zooms copied from
# showcase/thiel_v3.json and elon_v4.json.

THIEL = {
    "keep": [[845.78, 853.0], [853.33, 856.83], [857.11, 865.57], [865.86, 867.2],
             [867.71, 871.29], [871.59, 874.39], [874.62, 880.56]],
    "frame": {"ratio": "9:16", "mode": "crop", "focus_x": 0.38, "focus_y": 0.5},
    "zooms": [{"id": "z7", "start": 29.9, "end": 32.84, "strength": 0.1, "mode": "ease",
               "cx": 0.5, "cy": 0.3}],
    "video": {"width": 1920, "height": 1080, "duration": 2961.47},
    "samples": [(878.18, [0.3208, 0.1963, 0.4833, 0.4852]), (878.68, [0.3208, 0.2037, 0.4917, 0.5074]),
                (879.18, [0.3438, 0.1963, 0.5146, 0.5]), (879.68, [0.3417, 0.1852, 0.5104, 0.4852]),
                (880.18, [0.3542, 0.2481, 0.4917, 0.4926])],
    "item": ("counter", 30.46, 32.84, {"value": "140", "label": "", "backdrop": "scrim",
                                       "y": 0.5, "accent": "#FFC940"}),
}
ELON = {
    "keep": [[129.25, 130.05], [130.7, 138.04], [138.04, 138.58], [138.7, 145.4],
             [145.4, 147.1], [147.25, 149.05], [149.6, 152.63], [152.63, 153.25]],
    "frame": {"ratio": "9:16", "mode": "crop", "focus_track": [
        {"t0": 120.0, "t1": 138.04, "x": 0.56, "y": 0.5}, {"t0": 138.04, "t1": 145.4, "x": 0.76, "y": 0.5},
        {"t0": 145.4, "t1": 152.63, "x": 0.4, "y": 0.5}, {"t0": 152.63, "t1": 160.0, "x": 0.57, "y": 0.5}]},
    "zooms": [{"id": "z2", "start": 0.8, "end": 8.14, "strength": 0.1, "mode": "punch",
               "ramp_s": 0, "cx": 0.5, "cy": 0.38}],
    "video": {"width": 1920, "height": 1080, "duration": 9520.46},
    "samples": [(132.3, [0.4562, 0.3074, 0.6083, 0.5778]), (132.8, [0.4542, 0.3037, 0.6104, 0.5815]),
                (133.3, [0.4521, 0.3037, 0.6188, 0.6]), (133.8, [0.45, 0.3037, 0.6083, 0.5852]),
                (134.3, [0.4562, 0.3148, 0.6, 0.5704]), (134.8, [0.4583, 0.2667, 0.6396, 0.5889]),
                (135.3, [0.4562, 0.3074, 0.6125, 0.5852])],
    "item": ("lower_third", 2.4, 5.6, {"name": "Elon Musk", "role": "on The Joe Rogan Experience",
                                       "accent": "#FF3B30", "y": 0.55}),
}


def _showcase_ctx(case):
    v = case["video"]
    edl = default_edl(v["duration"])
    edl["keep"] = case["keep"]
    edl["frame"] = case["frame"]
    edl["effects"] = {"zooms": case["zooms"]}
    index = _index([{"t": t, "faces": [b]} for t, b in case["samples"]], video=v)
    return _Ctx(edl, index, v["duration"])


@pytest.mark.parametrize("case", [THIEL, ELON], ids=["thiel_counter", "elon_lower_third"])
def test_showcase_window_faces_cover_the_judged_graphic(case):
    ctx = _showcase_ctx(case)
    template, s, e, params = case["item"]
    track = keepout.face_track(ctx._edl, ctx.index, 1080, 1920, s, e)
    assert sum(1 for _t, faces in track if faces) >= len(track) * 0.9
    # the judged placements (probed ink at y 0.5 / 0.55) sit on the mouth
    judged = {"counter": (0.196, 0.423, 0.874, 0.579), "lower_third": (0.067, 0.49, 0.678, 0.61)}
    hit = keepout.assess(judged[template], track)
    assert hit["hit"] and hit["mouth"], hit


@needs_browser
@pytest.mark.parametrize("case", [THIEL, ELON], ids=["thiel_counter", "elon_lower_third"])
def test_showcase_graphics_move_off_the_mouth_with_the_real_probe(case):
    ctx = _showcase_ctx(case)
    template, s, e, params = case["item"]
    out = motion_tools.add_motion_graphic(ctx, template, s, e, params=params, id="g")
    assert "KEEP-OUT: it covered the speaker's mouth" in out, out
    item = ctx.latest_edl()["json"]["motion"][0]
    faces = item["footprint"]["faces"]
    # the INK where it landed (the face keep-out's box)
    box = keepout.settled_ink(motion_tools._probe_item(item, 1080, 1920),
                              motion_tools._probe_times(item))
    assert faces and box[1] >= max(f[3] for f in faces) - 0.005, (box, faces)   # below the chin
    assert not keepout.safe_issues(box, 1080, 1920), box
    track = keepout.face_track(ctx._edl, ctx.index, 1080, 1920, s, e)
    assert not keepout.assess(box, track)["hit"]
    # the stored footprint is what captions keep clear of: the ink plus any
    # dense scrim core around it (COVER_ALPHA)
    cover = item["footprint"]["box"]
    assert cover[1] <= box[1] + 0.002 and cover[3] >= box[3] - 0.002, (cover, box)


@needs_browser
def test_the_probe_reports_ink_without_the_soft_scrim():
    item = {"id": "n", "template": "counter", "start": 0, "end": 2.4,
            "params": motion_templates.check_params(
                "counter", {"value": "140", "backdrop": "scrim", "y": 0.5})}
    rep = motion_tools._probe_item(item, 1080, 1920)
    vis = keepout.union(rep["bboxes"])
    ink = keepout.settled_ink(rep, motion_tools._probe_times(item))
    assert len(rep["ink"]) == 4
    # the scrim spreads the visible box across the frame; the ink is the number
    assert vis[2] - vis[0] > 0.9 and ink[2] - ink[0] < 0.75
    assert ink[1] > vis[1] + 0.05 and ink[3] < vis[3] - 0.05
    # the box captions keep clear of (stored as the footprint) adds the
    # scrim's dense core around the number, never its soft falloff
    cover = keepout.cover_box(rep, motion_tools._probe_times(item))
    assert len(rep["cover"]) == 4
    assert cover[1] <= ink[1] and cover[3] >= ink[3] and cover[1] > vis[1] + 0.05
    assert cover[2] - cover[0] < 0.8


# ── a lane without a browser: the template's nominal ink ──────────────────

def test_every_movable_template_with_a_y_has_a_nominal_ink_box():
    for name in motion_templates.names():
        sp = motion_templates.spec(name)
        if sp.get("internal") or (sp["params"].get("y") or {}).get("type") != "float":
            continue
        if keepout.applicable(name, sp, sp.get("layer")):
            assert name in keepout.NOMINAL_INK, name


def test_nominal_ink_follows_y_size_and_x():
    spec = motion_templates.spec("counter")
    a = keepout.nominal_ink("counter", spec, {"value": "140", "y": 0.5})
    b = keepout.nominal_ink("counter", spec, {"value": "140", "y": 0.6, "size": 0.5})
    assert a[1] == pytest.approx(0.419, abs=0.005) and b[1] == pytest.approx(0.6 - 0.0405, abs=0.005)
    assert (b[2] - b[0]) == pytest.approx((a[2] - a[0]) / 2)
    slam = motion_templates.spec("word_slam")
    c = keepout.nominal_ink("word_slam", slam, {"y": 0.5, "x": 0.45})
    assert c[0] == pytest.approx(0.081 - 0.05)
    assert keepout.nominal_ink("html", motion_templates.spec("html"), {}) is None


def test_without_a_browser_the_estimated_box_still_moves_it_off_the_mouth(monkeypatch):
    monkeypatch.setattr(motion_tools, "_probe_item", lambda item, W, H, fps=30.0: None)
    ctx = _thiel_like_ctx()
    out = motion_tools.add_motion_graphic(ctx, "counter", 2.0, 4.0,
                                          params={"value": "140", "y": 0.42}, id="num")
    assert "could not be pre-checked here" in out, out
    assert "KEEP-OUT (estimated)" in out and "mouth" in out, out
    item = ctx.latest_edl()["json"]["motion"][0]
    assert item["params"]["y"] > 0.55
    # the estimate is the footprint (the production lanes have no browser, and
    # the captions must step around a graphic the keep-out moved onto them)
    fp = item["footprint"]
    want = keepout.nominal_ink("counter", motion_templates.spec("counter"), item["params"])
    assert fp["box"] == pytest.approx(want, abs=1e-3) and fp["faces"]
    # moved a CLEARANCE off the face, not onto a graze of it
    assert fp["box"][1] >= fp["faces"][0][3] + keepout.CLEARANCE - 0.005, fp
    # clear of the face: nothing to say
    out = motion_tools.set_motion_graphic(ctx, "num")
    assert "KEEP-OUT" not in out, out


# The Elon showcase, browserless: the example's lower third is 0.088 tall,
# but 'on The Joe Rogan Experience' wraps to two lines (0.12). Measured real
# ink of this item at y 0.63, where the example-sized estimate had put it:
# still across his mouth.
ELON_REAL_AT_063 = (0.067, 0.569, 0.681, 0.692)


def test_estimated_lower_third_sizes_its_own_copy_and_side():
    spec = motion_templates.spec("lower_third")
    elon = keepout.nominal_ink("lower_third", spec, {"name": "Elon Musk", "y": 0.63,
                                                     "role": "on The Joe Rogan Experience"})
    assert elon[1] <= ELON_REAL_AT_063[1] + 0.005 and elon[3] >= ELON_REAL_AT_063[3] - 0.005
    # a name alone is one line about as wide as the name (probed: 0.04 x 0.37)
    rogan = keepout.nominal_ink("lower_third", spec, {"name": "Joe Rogan", "y": 0.5})
    assert rogan == pytest.approx((0.067, 0.479, 0.439, 0.521), abs=0.01)
    right = keepout.nominal_ink("lower_third", spec, {"name": "Joe Rogan", "y": 0.5, "side": "right"})
    assert right[2] == pytest.approx(1 - 0.067) and right[0] == pytest.approx(1 - 0.439, abs=0.01)


def test_without_a_browser_the_elon_lower_third_clears_the_real_mouth(monkeypatch):
    monkeypatch.setattr(motion_tools, "_probe_item", lambda item, W, H, fps=30.0: None)
    ctx = _showcase_ctx(ELON)
    template, s, e, params = ELON["item"]
    out = motion_tools.add_motion_graphic(ctx, template, s, e, params=params, id="g")
    assert "KEEP-OUT (estimated)" in out and "below the chin" in out, out
    item = ctx.latest_edl()["json"]["motion"][0]
    track = keepout.face_track(ctx._edl, ctx.index, 1080, 1920, s, e)
    # the real ink of this copy is 0.123 tall, centred on y
    y = item["params"]["y"]
    real = (0.067, y - 0.0615, 0.681, y + 0.0615)
    assert not keepout.assess(real, track)["hit"], (y, real)
    assert keepout.assess(ELON_REAL_AT_063, track)["mouth"]     # where the old estimate put it
    assert not keepout.safe_issues(item["footprint"]["box"], 1080, 1920)


def test_without_a_browser_a_name_tag_beside_the_face_stays(monkeypatch):
    monkeypatch.setattr(motion_tools, "_probe_item", lambda item, W, H, fps=30.0: None)
    ctx = _thiel_like_ctx()              # the head sits x 0.31-0.81 of the 9:16 frame
    out = motion_tools.add_motion_graphic(ctx, "lower_third", 2.0, 4.0,
                                          params={"name": "Jo", "y": 0.45}, id="tag")
    assert "KEEP-OUT" not in out, out
    assert ctx.latest_edl()["json"]["motion"][0]["params"]["y"] == 0.45
    # the same tag on the face's side moves; flipping the side is one way out
    out = motion_tools.set_motion_graphic(ctx, "tag", params={"name": "Peter Thiel",
                                                              "role": "Co-founder, PayPal"})
    assert "KEEP-OUT (estimated)" in out, out


def test_without_a_browser_slams_leave_the_button_rail(monkeypatch):
    monkeypatch.setattr(motion_tools, "_probe_item", lambda item, W, H, fps=30.0: None)
    edl = default_edl(10.0)
    ctx = _Ctx(edl, {"video": {"width": 1080, "height": 1920}}, 10.0)
    out = motion_tools.add_motion_graphic(ctx, "word_slam", 1.0, 2.5,
                                          params={"text": "*enough*", "y": 0.64}, id="w")
    assert "KEEP-OUT (estimated)" in out and "button rail" in out, out
    item = ctx.latest_edl()["json"]["motion"][0]
    assert item["params"].get("x", 0.5) < 0.5 or item["params"].get("width", 0.85) < 0.85
    assert not keepout.safe_issues(item["footprint"]["box"], 1080, 1920)
    # a template sized by its copy is not second-guessed without a probe
    out = motion_tools.add_motion_graphic(ctx, "hook_title", 3.0, 4.5,
                                          params={"text": "Hi", "y": 0.64}, id="h")
    assert "KEEP-OUT" not in out, out


def test_a_failed_keep_out_leaves_the_placement_as_written(monkeypatch):
    monkeypatch.setattr(motion_tools, "_probe_item", _probe_by_y())
    real = motion_tools._set_footprint

    def boom(*a, **k):
        raise RuntimeError("late failure")
    monkeypatch.setattr(motion_tools, "_set_footprint", boom)
    ctx = _thiel_like_ctx()
    out = motion_tools.add_motion_graphic(ctx, "counter", 2.0, 4.0,
                                          params={"value": "140", "y": 0.42}, id="num")
    assert out.startswith("EDL v1") and "KEEP-OUT" not in out, out
    item = ctx.latest_edl()["json"]["motion"][0]
    assert item["params"]["y"] == 0.42 and not item.get("footprint")
    monkeypatch.setattr(motion_tools, "_set_footprint", real)


def test_detect_faces_says_when_it_cannot_measure(tmp_path):
    assert keepout.detect_faces(str(tmp_path / "missing.jpg")) is None


def test_face_cascades_are_per_thread():
    import threading
    cv2 = keepout._cv()
    if cv2 is None:
        pytest.skip("OpenCV not installed")
    got = {}

    def grab(k):
        got[k] = keepout._cascades(cv2)
    threads = [threading.Thread(target=grab, args=(k,)) for k in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert got[0] and got[1] and got[0][0][1] is not got[1][0][1]
    assert keepout._cascades(cv2) is keepout._cascades(cv2)


def test_where_label_reads_a_chin_graze_as_below_the_chin():
    zones = [(0.1, 0.25, 0.72, 0.62)]
    assert keepout.where_label((0.07, 0.6, 0.7, 0.72), zones) == "below the chin"
    assert keepout.where_label((0.07, 0.1, 0.7, 0.26), zones) == "above the head"
    assert keepout.where_label((0.75, 0.3, 0.95, 0.5), zones) == "beside the face"


@needs_browser
def test_nominal_ink_matches_a_fresh_probe_of_each_example():
    names, items = [], []
    for name in sorted(keepout.NOMINAL_INK):
        sp = motion_templates.spec(name)
        params = motion_templates.check_params(name, dict(sp.get("example") or {}, y=0.5))
        items.append({"id": name, "template": name, "start": 0.0,
                      "end": max(float(sp.get("duration") or 3.0), 2.4), "params": params})
        names.append(name)
    reps = motion_tools._probe_items(items, 1080, 1920)
    for name, item, rep in zip(names, items, reps):
        got = keepout.settled_ink(rep, motion_tools._probe_times(item))
        want = keepout.nominal_ink(name, motion_templates.spec(name), item["params"])
        assert got and max(abs(a - b) for a, b in zip(got, want)) < 0.03, (name, got, want)


def test_an_opaque_b_roll_cutaway_hides_the_face():
    ctx = _thiel_like_ctx()
    edl = dict(ctx._edl, overlays=[
        {"id": "b1", "asset_key": "projects/1/broll.mp4", "kind": "video", "start": 2.0,
         "duration_s": 2.0, "fit": "cover"},
        {"id": "p1", "asset_key": "projects/1/pip.png", "kind": "image", "start": 5.0,
         "duration_s": 2.0}])                       # a PiP leaves the speaker on screen
    track = keepout.face_track(edl, ctx.index, 1080, 1920, 2.1, 3.9)
    assert track and all(not faces for _t, faces in track)
    assert all(faces for _t, faces in keepout.face_track(edl, ctx.index, 1080, 1920, 5.1, 6.9))


def test_a_caption_stepping_above_the_head_clears_the_hair_when_it_can():
    graphic = (0.1, 0.62, 0.8, 0.76)
    face = keepout.face_zone((0.3, 0.42, 0.7, 0.6))        # a head low in the frame
    z0, z1, y = _caption_zone(0.72, [graphic], [face])
    hair = face[1] - caption_carry.HAIR_UP * (face[3] - face[1])
    assert z1 <= hair and y <= z1 - 0.04
    # a frame-filling head leaves only the band over the hair: still used
    big = keepout.face_zone((0.1, 0.25, 0.7, 0.6))
    z0, z1, y = _caption_zone(0.72, [graphic], [big])
    assert z1 <= big[1] and z1 > big[1] - caption_carry.HAIR_UP * (big[3] - big[1])


def test_caption_zones_stay_inside_the_template_safe_band_off_portrait():
    z0, z1, _y = _caption_zone(0.8, [(0.1, 0.6, 0.9, 0.95)], [],
                               safe=caption_carry.safe_range(1920, 1080))
    assert 0.07 <= z0 and z1 <= 0.9


def test_estimated_counter_sizes_its_figure_and_label():
    spec = motion_templates.spec("counter")
    # probed at y 0.5: '3x' 0.162 tall; '3x / faster' 0.256; '1,000,000 users' 0.075
    short = keepout.nominal_ink("counter", spec, {"value": "3x", "y": 0.5})
    labelled = keepout.nominal_ink("counter", spec, {"value": "3x", "label": "faster", "y": 0.5})
    long = keepout.nominal_ink("counter", spec, {"value": "1,000,000 users", "y": 0.5})
    assert short[3] - short[1] == pytest.approx(0.162, abs=0.01)
    assert labelled[3] - labelled[1] == pytest.approx(0.256, abs=0.015)
    assert long[3] - long[1] == pytest.approx(0.075, abs=0.01)
    # the Jobs counter (probed: 0.10-0.25 at y 0.1, size 0.576): the template
    # keeps it inside y 0.08-0.80 on a 9:16 frame, and so does the estimate
    jobs = {"value": "40", "label": "fonts on the screen", "y": 0.125, "size": 0.72}
    free = keepout.nominal_ink("counter", spec, jobs)
    held = keepout.nominal_ink("counter", spec, jobs, frame=(1080, 1920))
    assert free[1] < 0.08 and held[1] == pytest.approx(0.08)
    assert held[3] - held[1] == pytest.approx(free[3] - free[1])
    assert keepout.nominal_ink("counter", spec, jobs, frame=(1920, 1080)) == free


def test_face_track_follows_the_camera_as_rendered_held_through_a_cut():
    # a punch authored to end 3 frames before a jump cut renders held to the
    # cut (renderer.camera_zooms): the face the keep-out compares is still
    # the zoomed one in those frames
    zoom = {"id": "z1", "start": 2.0, "end": 4.9, "strength": 0.2, "mode": "punch",
            "ramp_s": 0, "cx": 0.5, "cy": 0.3}
    edl = _edl(keep=((10.0, 15.0), (16.0, 30.0)),
               frame={"ratio": "9:16", "mode": "crop", "focus_x": 0.38}, zooms=[zoom])
    head = [0.32, 0.2, 0.48, 0.5]
    index = _index([{"t": 10.0 + k * 0.5, "faces": [head]} for k in range(40)],
                   video=dict(VIDEO, fps=30.0))
    track = keepout.face_track(edl, index, 1080, 1920, 4.0, 4.99)
    zoomed = [f[3] - f[1] for t, faces in track if t < 4.85 for f in faces]
    held = [f[3] - f[1] for t, faces in track if 4.91 < t < 5.0 for f in faces]
    assert zoomed and held and min(held) == pytest.approx(max(zoomed), abs=0.002)
    assert min(held) > 0.3 * 1.15


def test_geometry_maps_the_face_through_a_source_fed_card_and_a_stack():
    # a card framed from the SOURCE (layouts): the face lands where the
    # card's source rect puts it, not where the 9:16 crop had it
    card = {"id": "c", "start": 0.0, "end": 10.0, "box": [0.04, 0.28, 0.96, 0.67],
            "fit": "crop", "source": [0.2, 0.1, 0.7, 0.505]}
    edl = _edl(frame={"ratio": "9:16", "mode": "crop", "focus_x": 0.38}, cards=[card])
    geo = keepout.Geometry(edl, VIDEO, 1080, 1920, 20.0)
    face = (0.32, 0.2, 0.48, 0.5)
    b = geo.to_output(1.0, 11.0, face)
    import picture_cards
    rect = picture_cards.match_rect(card["source"], card["box"], 1920, 1080, 1080, 1920)
    kx = (0.96 - 0.04) / (rect[2] - rect[0])
    assert b[0] == pytest.approx(0.04 + (0.32 - rect[0]) * kx, abs=1e-3)
    assert b[4:] == pytest.approx(tuple(card["box"]), abs=1e-3)
    # a stack shows the speaker in one panel and the evidence in the other:
    # the face is only where its panel's source rect holds it
    stack = {"id": "s", "start": 0.0, "end": 10.0, "box": [0.08, 0.035, 0.92, 0.55],
             "fit": "crop", "panels": [
                 {"box": [0.08, 0.035, 0.92, 0.365], "source": [0.2, 0.1, 0.6, 0.6]},
                 {"box": [0.08, 0.385, 0.92, 0.55], "source": [0.55, 0.65, 0.85, 0.83]}]}
    edl = _edl(frame={"ratio": "9:16", "mode": "crop", "focus_x": 0.38}, cards=[stack])
    geo = keepout.Geometry(edl, VIDEO, 1080, 1920, 20.0)
    outs = geo.to_outputs(1.0, 11.0, face)
    assert len(outs) == 1 and outs[0][4:] == pytest.approx((0.08, 0.035, 0.92, 0.365), abs=1e-3)
    assert 0.08 <= outs[0][0] and outs[0][3] <= 0.6


def test_the_solver_prices_the_caption_band_as_the_caption_plan_sees_it():
    # the stored footprint is the COVER box (ink plus a scrim's core): the
    # obstacle the solver keeps the INK out of collides exactly when the
    # caption plan would see the cover box collide with that caption
    grow = (0.04, 0.06)                     # cover reaches past the ink
    for y_cap in (0.2, 0.5, 0.74):
        for k in range(60):
            top = 0.05 + k * 0.012
            ink = (0.2, top, 0.8, top + 0.1)
            cover = (0.2, ink[1] - grow[0], 0.8, ink[3] + grow[1])
            seen = caption_carry.collides([cover], y_cap)
            priced = keepout.inter(ink, keepout.caption_obstacle(y_cap, grow)) > 0
            assert seen == priced, (y_cap, ink)
    # a graphic outside the caption column never collides either way
    side = (0.86, 0.6, 0.95, 0.7)
    assert not caption_carry.collides([side], 0.65)
    assert keepout.inter(side, keepout.caption_obstacle(0.65)) == 0


def test_geometry_maps_the_face_through_a_re_aimed_cards_shot_rect():
    # a card that re-aims per shot (source_track): the face lands where THAT
    # shot's rect puts it
    card = {"id": "c", "start": 0.0, "end": 20.0, "box": [0.04, 0.28, 0.96, 0.67],
            "fit": "crop", "source": [0.2, 0.1, 0.7, 0.505],
            "source_track": [{"t0": 10.0, "t1": 20.0, "source": [0.2, 0.1, 0.7, 0.505]},
                             {"t0": 25.0, "t1": 35.0, "source": [0.0, 0.1, 0.5, 0.505]}]}
    edl = _edl(keep=((10.0, 20.0), (25.0, 35.0)),
               frame={"ratio": "9:16", "mode": "crop", "focus_x": 0.38}, cards=[card])
    geo = keepout.Geometry(edl, VIDEO, 1080, 1920, 20.0)
    face = (0.3, 0.2, 0.4, 0.45)
    first = geo.to_output(1.0, 11.0, face)
    second = geo.to_output(11.0, 26.0, face)
    # the second shot's rect sits 0.2 of the source further left: the same
    # source face lands further right on the card
    assert second[0] > first[0] + 0.1


def test_the_solver_leaves_the_captions_a_band_rather_than_making_them_touch():
    # the Thiel counter: moved below a big face onto the caption band, the
    # cheapest spot leaves the captions no band clear of it and the face
    # (they would touch it); priced as the caption plan sees it, the solver
    # takes a spot that leaves them one
    import motion_templates
    spec = motion_templates.spec("counter")
    params = {"value": "140", "y": 0.5, "size": 1.0}
    face = (0.25, 0.15, 0.75, 0.47)
    ink = (0.2, 0.4, 0.8, 0.6)                    # centred on y 0.5
    grow = (0.03, 0.045)                          # its scrim's core
    ys = [0.74]
    kw = dict(captions=[keepout.caption_band(y) for y in ys],
              near_captions=[keepout.caption_obstacle(y, grow) for y in ys])
    room = keepout.caption_room(ys, [face], 1080, 1920, grow)
    plain = keepout.candidates("counter", spec, params, ink, [], [face], 1080, 1920, **kw)
    priced = keepout.candidates("counter", spec, params, ink, [], [face], 1080, 1920,
                                room=room, **kw)
    assert not room(plain[0][2])
    best = priced[0]
    assert room(best[2]) and not keepout.on_face(best[2], [face], []), best
    # a spot with room costs no more than it did before
    assert any(c[1] == best[1] and abs(c[0] - best[0]) < 1e-9 for c in plain)


def test_the_watermark_zone_is_the_marks_top_left_box():
    # the free-tier mark (robot + "Edited using Valmera AI / valmera.io")
    # is a reserved zone for text placement; the judged collision: band
    # kickers at y 0.09-0.115 under the mark
    import keepout
    z = keepout.watermark_zone(1080, 1920)
    assert z[0] == pytest.approx(0.09, abs=.015)
    assert z[1] == pytest.approx(0.05, abs=.015)
    assert 0.35 < z[2] < 0.75 and 0.10 < z[3] < 0.15
    assert keepout.inter(z, [0.2, 0.09, 0.8, 0.115]) > 0
    low = keepout.watermark_zone(1080, 1920, anchor_y=400)
    assert low[1] > z[3] - .01 and low[0] == z[0]


# ── round 7: scene text is a soft keep-out ─────────────────────────────

def test_the_solver_prefers_a_spot_off_scene_text():
    """Judged (round 5): Elon's white OCCUPY print under the captions and
    the hook. A graphic the solver places takes the calm spot when a short
    move gets there; scene text is priced (SCENE_PENALTY by the share of the
    spot it covers), never forbidden, and never outweighs a face."""
    spec = motion_templates.spec("word_slam")
    params = {"y": 0.64}
    ink = (0.2, 0.62, 0.8, 0.66)                         # a one-line hook
    plain = keepout.candidates("word_slam", spec, params, ink, [], [], 1080, 1920, clear_penalty=0.0)
    assert plain[0][2] == ink                            # nothing to move for
    print_box = (0.0, 0.6, 1.0, 0.7)                     # a shirt print right under it
    priced = keepout.candidates("word_slam", spec, params, ink, [], [], 1080, 1920,
                                clear_penalty=0.0, scene=[print_box])
    moved = priced[0][2]
    assert keepout.inter(moved, print_box) == 0 and abs(moved[1] - ink[1]) < 0.1
    # a print too far to escape cheaply stays a price, not a move
    tall = (0.0, 0.3, 1.0, 0.8)
    stay = keepout.candidates("word_slam", spec, params, ink, [], [], 1080, 1920,
                              clear_penalty=0.0, scene=[tall])
    assert stay[0][2] == ink
    # and it never outweighs a face
    face = (0.25, 0.4, 0.75, 0.58)
    zones = [keepout.face_zone(face)]
    off = keepout.candidates("word_slam", spec, params, ink, [], zones, 1080, 1920,
                             scene=[(0.0, 0.65, 1.0, 0.8)])
    assert off and not keepout.on_face(off[0][2], zones)


def test_a_camera_move_rechecks_the_graphics_under_it(monkeypatch):
    """A zoom written after a graphic re-checks it against the face as the
    zoom frames it, and places it again with the write's own keep-out."""
    import motion_tools
    calls = []

    class Ctx:
        has_main_video = True
        index = {"video": {"width": 1920, "height": 1080}}
    edl = {"keep": [[0.0, 10.0]], "frame": {"ratio": "9:16", "mode": "crop"},
           "motion": [{"id": "g", "template": "word_slam", "start": 2.0, "end": 4.0, "params": {"y": 0.5},
                       "footprint": {"box": [0.15, 0.45, 0.85, 0.6]}},
                      {"id": "far", "template": "word_slam", "start": 8.0, "end": 9.0, "params": {},
                       "footprint": {"box": [0.15, 0.45, 0.85, 0.6]}}]}
    monkeypatch.setattr(keepout, "face_track", lambda e, i, W, H, a, b, measure=None:
                        [(a + 0.1 * k, [(0.3, 0.2, 0.7, 0.56)]) for k in range(10)])
    monkeypatch.setattr(motion_tools, "_face_measure", lambda ctx: None)
    monkeypatch.setattr(motion_tools, "_probe_full", lambda ctx, e, m: (None, "", None, None))

    def keep(ctx, e, m, rep):
        calls.append(m["id"])
        m["params"] = dict(m["params"], y=0.68)
        return "\nKEEP-OUT (estimated): moved below the chin (y 0.5 → 0.68).", None
    monkeypatch.setattr(motion_tools, "_keep_out", keep)
    notes = motion_tools.keep_out_under_camera(Ctx(), edl, 1.5, 5.0)
    assert calls == ["g"] and len(notes) == 1 and "'g'" in notes[0]
    assert edl["motion"][0]["params"]["y"] == 0.68 and edl["motion"][1]["params"] == {}
    # clear of the face under the move: nothing re-placed, nothing said
    monkeypatch.setattr(keepout, "face_track", lambda e, i, W, H, a, b, measure=None:
                        [(a + 0.1 * k, [(0.3, 0.05, 0.7, 0.3)]) for k in range(10)])
    calls.clear()
    assert motion_tools.keep_out_under_camera(Ctx(), edl, 1.5, 5.0) == [] and calls == []


def test_a_small_graphic_inside_a_frame_filling_face_is_on_it():
    """A graphic lying mostly inside a face zone is on the face even when it
    covers under FACE_HIT of a frame-filling zone (round 7 integration: a
    counter shrunk to size 0.4 was offered a spot on the cheek)."""
    face = (0.0, 0.1, 1.0, 0.75)
    zones = [keepout.face_zone(face)]
    small = (0.25, 0.362, 0.82, 0.498)
    assert keepout.inter(small, zones[0]) < keepout.FACE_HIT * keepout.area(zones[0])
    assert keepout.on_face(small, zones)
    assert keepout.assess(small, [(1.0, [face]), (2.0, [face]), (3.0, [face])])["hit"]
    # a graphic below the chin of an ordinary face stays clear
    ordinary = (0.35, 0.15, 0.65, 0.42)
    assert not keepout.on_face((0.2, 0.55, 0.8, 0.68), [keepout.face_zone(ordinary)])
