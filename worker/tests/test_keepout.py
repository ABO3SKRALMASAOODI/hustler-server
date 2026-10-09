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

import keepout  # noqa: E402
import motion_captions  # noqa: E402
import motion_engine  # noqa: E402
import motion_templates  # noqa: E402
import motion_tools  # noqa: E402
from schemas import (EDLValidationError, default_edl, edl_signature,  # noqa: E402
                     validate_edl)
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


def test_caption_zone_steps_around_the_graphic_and_the_face():
    graphic = (0.1, 0.648, 0.8, 0.78)
    face = keepout.face_zone((0.25, 0.2, 0.75, 0.5))
    # clear: nothing to do
    assert keepout.caption_zone(0.4, [graphic], [face]) is None
    z0, z1, y = keepout.caption_zone(0.74, [graphic], [face])
    assert z1 - z0 >= keepout.CAPTION_ZONE_MIN and z0 >= face[3] and z1 <= graphic[1]
    assert z0 + 0.04 <= y <= z1 - 0.04
    # a frame-filling face and a graphic on the band leave no room
    big = keepout.face_zone((0.0, 0.12, 1.0, 0.58))
    assert keepout.caption_zone(0.74, [(0.1, 0.62, 0.9, 0.79)], [big]) is False


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
    edl["motion"][0]["footprint"]["box"] = [0.1, 0.2]
    with pytest.raises(EDLValidationError):
        validate_edl(edl, 10.0)


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
    assert [c["w"] for c in plain] == [c["w"] for c in moved]
    for a, b in zip(plain, moved):
        if b["s"] < 4.0 and b["e"] > 2.0:
            z0, z1 = b["z"]
            assert z0 >= 0.5 and z1 <= 0.648 and z0 <= b["y"] <= z1, b
            assert b["b"] == "m"
        else:
            assert b == a                                        # outside the window: unchanged
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
    box, faces = item["footprint"]["box"], item["footprint"]["faces"]
    assert faces and box[1] >= max(f[3] for f in faces) - 0.005, (box, faces)   # below the chin
    assert not keepout.safe_issues(box, 1080, 1920), box
    track = keepout.face_track(ctx._edl, ctx.index, 1080, 1920, s, e)
    assert not keepout.assess(box, track)["hit"]


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
    a = keepout.nominal_ink("counter", spec, {"y": 0.5})
    b = keepout.nominal_ink("counter", spec, {"y": 0.6, "size": 0.5})
    assert a[1] == pytest.approx(0.425) and b[1] == pytest.approx(0.6 - 0.0375)
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
    assert not item.get("footprint")                    # captions step only around measured ink
    # clear of the face: nothing to say
    out = motion_tools.set_motion_graphic(ctx, "num")
    assert "KEEP-OUT" not in out, out


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
