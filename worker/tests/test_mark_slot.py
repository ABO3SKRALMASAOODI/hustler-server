"""The v7 BRAND SLOT (owner, 24 Oct 2026).

The owner's reference, David Senra's Shorts with the "PRESENTED BY ramp"
sponsor mark, puts the mark at 12.5-17% of the height, ~7% in from the right:
the first clear band under the Shorts/TikTok/Reels top controls (they cover
~2.5-9% of the frame, ~10.5% on a tall phone). Valmera's v6 mark sat 6% down,
under those controls. v7 moves the same drawing into a FIXED slot on every
vertical-feed final -- ink top at 12.5%, right edge 9.5% in (v3's cover-crop
rule) -- and the layouts move around it, never the mark.

Pinned here: the slot itself; every layout line that keeps clear of it reads
the one zone (keepout.watermark_zone): the band planner, the kicker beside
the mark, the card rows, the stack and archival windows, the HUD, captions,
graphics and the picture floor; an EDL laid out around the v6 corner keeps
the corner on re-export (no collision); and a rendered final carries the
mark in the slot with the 5 s end card md5-identical to a clean final's.
"""
import os
import shutil
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import agent_tools  # noqa: E402
import band_type  # noqa: E402
import caption_place  # noqa: E402
import config  # noqa: E402
import keepout  # noqa: E402
import motion_templates  # noqa: E402
import motion_tools  # noqa: E402
import picture_cards  # noqa: E402
import render_qc  # noqa: E402
import renderer  # noqa: E402
import source_layout  # noqa: E402

W, H = 1080, 1920
FFMPEG = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg")


# ── the slot ─────────────────────────────────────────────────────────────

def test_the_slot_sits_in_the_first_clear_band_under_the_feeds_controls():
    g = renderer.watermark_geometry(W, H)
    z = keepout.watermark_zone(W, H)
    assert g["top"] == round(H * config.WATERMARK_SLOT_TOP_FRAC) == 240   # 12.5%
    # clear of the tallest top controls (Reels / TikTok on a tall phone, ~10.5%)
    assert z[1] > 0.105
    # the right edge clears the feeds' ~9% cover crop (v3's rule), close to
    # Senra's 7%
    assert 0.09 <= 1 - g["right"] / W <= 0.10
    # well above the right rails (from ~45% down) and the captions
    assert z[3] < 0.2
    # the same drawing as v6, only placed lower: the version busts the cache
    assert config.WATERMARK_VERSION == 7


def test_the_backend_mirror_carries_the_slot_version():
    src = open(os.path.join(HERE, "..", "..", "backend", "routes", "video.py")).read()
    assert f"WATERMARK_VERSION = {config.WATERMARK_VERSION}" in src


# ── every layout line reads the one zone ─────────────────────────────────

def test_the_band_starts_under_the_slot_and_the_kicker_shares_its_row():
    z = keepout.watermark_zone(W, H)
    assert motion_tools.band_top(W, H) == pytest.approx(z[3]) == keepout.mark_floor(W, H)
    strip = band_type.mark_strip(W, H, top=motion_tools.feed_top(W, H))
    assert strip[1] == pytest.approx(z[1]) and strip[3] == pytest.approx(z[3])
    p = motion_templates.check_params("headline", {"text": "AI is a *manufacturing* industry",
                                                   "kicker": "JENSEN HUANG"})
    ok, ky = band_type.kicker_beside_fits(p, W, H, strip)
    assert ok and ky == pytest.approx((z[1] + z[3]) / 2, abs=1e-3)   # centred on the mark
    box = band_type.kicker_box(dict(p, kicker_y=ky), W, H)
    assert box[2] < z[0] and not keepout.on_mark(box, z)


def test_the_card_rows_stack_and_archival_windows_sit_under_the_slot():
    top = keepout.card_top(W, H)
    band_top = keepout.card_top(W, H, band=0.10)          # a two-line claim
    assert top == pytest.approx(keepout.mark_floor(W, H) + keepout.CARD_GAP)
    assert source_layout.CARD_TOP >= top - 1e-3 and source_layout.CARD_TOP - top < 0.002
    assert source_layout.CARD_TOP_BAND >= band_top - 1e-3
    assert source_layout.CARD_TOP_BAND - band_top < 0.002
    assert picture_cards.ARCHIVAL_CARD_BOX[1] >= band_top - 1e-3
    assert agent_tools.STACK_SPEAKER_BOX[1] >= top - 1e-3
    z = keepout.watermark_zone(W, H)
    for box in (picture_cards.ARCHIVAL_CARD_BOX, agent_tools.STACK_SPEAKER_BOX,
                agent_tools.STACK_SCREEN_BOX):
        assert not keepout._crosses(box, z), box


# The podcast-shorts skill's card rows (references/looks.md, Card geometry)
ROWS = {
    "no band, 4:5 at 81%": [0.095, 0.205, 0.905, 0.795],
    "band + full width": [0.0, 0.305, 1.0, 0.794],
    "1:1 at 93%, Editorial Serif": [0.035, 0.205, 0.965, 0.728],
}


@pytest.mark.parametrize("name", sorted(ROWS))
def test_the_skill_rows_clear_the_slot_and_meet_the_floor(name):
    box = ROWS[name]
    z = keepout.watermark_zone(W, H)
    assert keepout.inter(box, z) == 0
    assert box[1] >= keepout.card_top(W, H) - 1e-3
    assert (box[2] - box[0]) * (box[3] - box[1]) >= render_qc.PICTURE_FLOOR - 1e-3
    assert render_qc.PICTURE_FLOOR == source_layout.PICTURE_FLOOR == motion_tools.CARD_FLOOR
    looks = open(os.path.join(HERE, "..", "..", "plugins", "valmera", "skills",
                              "valmera-podcast-shorts", "references", "looks.md")).read()
    assert "[" + ", ".join(f"{v:g}" if v not in (0.0, 1.0) else str(int(v)) for v in box) \
        + "]" in looks, name


def test_the_4x5_card_and_the_mark_share_one_right_edge():
    g = renderer.watermark_geometry(W, H)
    assert abs(g["right"] / W - ROWS["no band, 4:5 at 81%"][2]) < 0.002


def test_captions_hud_and_fallbacks_read_the_same_zone():
    z = keepout.watermark_zone(W, H)
    assert caption_place.watermark_box(W, H) == pytest.approx(tuple(z))
    assert keepout.hud_mark_params({}, W, H)["mark"] == [str(v) for v in z]
    # the fallbacks a lane uses when it cannot compute the zone are the zone
    lt = open(os.path.join(HERE, "..", "tools_ext", "layout_tools.py")).read()
    hud = open(os.path.join(HERE, "..", "motion", "templates", "hud.html")).read()
    fb = [round(v, 3) for v in (z[0], z[1], z[2], z[3])]
    for src in (lt, hud):  # (layoutdoc: below)
        i = src.index("[0.559, 0.115, 0.915, 0.193]")
        vals = [float(v) for v in src[i + 1:src.index("]", i)].split(",")]
        assert vals == pytest.approx(fb, abs=0.002)
    assert "y 0.115-0.192" in keepout.ISSUE_TEXT["mark"]


def test_a_graphic_set_on_the_slot_is_moved_off_it():
    z = keepout.mark_zone(W, H)
    slam = (0.24, 0.12, 0.76, 0.24)                    # a hook set high, on the slot
    assert keepout.on_mark(slam, z)
    spec = motion_templates.spec("word_slam")
    params = motion_templates.check_params("word_slam", {"text": "One *sensor*",
                                                         "width": 0.52, "y": 0.18})
    cands = keepout.candidates("word_slam", spec, params, slam, [], [], W, H, mark=z)
    assert cands and all(not keepout.on_mark(b, z) for _c, _p, b in cands)


# ── an EDL laid out around the v6 corner keeps the corner ────────────────

def _headline(y, h, **kw):
    return {"id": "hl", "template": "headline", "start": 0.0, "end": 30.0,
            "params": dict({"text": "Confident on *'27 compute*", "y": y, "height": h}, **kw)}


def _card(box):
    return {"effects": {"picture_cards": [{"id": "card", "start": 0.0, "end": 30.0,
                                           "box": box}]}}


LEGACY = {
    "v6 band headline (y 0.128-0.228)": {"motion": [_headline(0.178, 0.10)]},
    "v6 no-band 4:5 card (top 0.13)": _card([0.095, 0.13, 0.905, 0.795]),
    "v6 Editorial 1:1 (top 0.135)": _card([0.04, 0.135, 0.96, 0.652]),
    "a graphic stored on the slot": {"motion": [{
        "id": "slam", "template": "word_slam", "start": 1.0, "end": 2.0,
        "params": {"text": "x"}, "footprint": {"box": [0.2, 0.12, 0.8, 0.26],
                                               "ar": round(W / H, 4)}}]},
}
V7 = {
    "band headline under the slot": {"motion": [_headline(
        0.2423, 0.10, kicker="ELON MUSK", kicker_y=0.1537)], **_card([0.0, 0.305, 1.0, 0.794])},
    "4:5 card": _card(ROWS["no band, 4:5 at 81%"]),
    "full-bleed, a graphic under the slot": {"motion": [{
        "id": "slam", "template": "word_slam", "start": 1.0, "end": 2.0,
        "params": {"text": "x"}, "footprint": {"box": [0.2, 0.30, 0.8, 0.42],
                                               "ar": round(W / H, 4)}}]},
    "a graphic behind the speaker": {"motion": [{
        "id": "tb", "template": "word_slam", "start": 1.0, "end": 2.0,
        "layer": "behind_subject", "params": {"text": "x"},
        "footprint": {"box": [0.2, 0.12, 0.8, 0.26], "ar": round(W / H, 4)}}]},
    "nothing": {},
}


@pytest.mark.parametrize("name", sorted(LEGACY))
def test_a_legacy_layout_keeps_the_v6_corner(name):
    edl = LEGACY[name]
    assert keepout.slot_occupied(edl, W, H)
    anchor = renderer.watermark_anchor_y(edl, 1920, 1080, W, H)
    assert anchor == renderer.WATERMARK_CORNER
    g = renderer.watermark_geometry(W, H, anchor)
    assert g["ink"] == pytest.approx([610, 115, 972, 225], abs=2)          # v6, exact
    # the brand checks read the same corner the overlay is laid at
    zone = keepout.watermark_zone(W, H, anchor)
    assert zone[3] == pytest.approx(0.1272, abs=1e-3)


@pytest.mark.parametrize("name", sorted(V7))
def test_a_v7_layout_takes_the_slot(name):
    edl = V7[name]
    assert keepout.slot_occupied(edl, W, H) == ""
    assert renderer.watermark_anchor_y(edl, 1920, 1080, W, H) is None


def test_other_ratios_never_ask_for_the_corner():
    edl = LEGACY["v6 band headline (y 0.128-0.228)"]
    for w, h in ((1920, 1080), (1080, 1080), (1080, 1350)):
        assert renderer.watermark_anchor_y(edl, 1920, 1080, w, h) is None
        assert renderer.watermark_geometry(w, h) == renderer.watermark_geometry(
            w, h, renderer.WATERMARK_CORNER)


def test_the_admin_positions_follow_the_slot():
    lower = renderer.watermark_anchor_y({}, 1920, 1080, W, H, {"lower": True})
    assert lower == 240 + 96                               # the slot + 5% of H
    g = renderer.watermark_geometry(W, H, lower)
    assert g["right"] == renderer.watermark_geometry(W, H)["right"]
    scene = renderer.watermark_anchor_y({"frame": {"ratio": "9:16", "mode": "pad_blur"}},
                                        1920, 1080, W, H, {"scene_top": True})
    fit = renderer.fit_fractions(1920, 1080, W, H, "pad_blur")
    assert fit[2] * H < scene < fit[4] * H
    # scene-top on a crop falls back to the frame rule: the slot, or the
    # corner for a legacy layout
    crop = dict(LEGACY["v6 no-band 4:5 card (top 0.13)"], frame={"ratio": "9:16", "mode": "crop"})
    assert renderer.watermark_anchor_y(crop, 1920, 1080, W, H,
                                       {"scene_top": True}) == renderer.WATERMARK_CORNER


# ── a rendered final: the mark in the slot, the end card untouched ───────

@FFMPEG
def test_a_final_burns_the_slot_and_leaves_the_end_card_byte_identical(tmp_path):
    import w0b_probe
    from test_w0b_render import IDX, MUSIC_KEY, _base, _yuv
    src = w0b_probe.make_source(str(tmp_path / "src25.mp4"), rate="25")
    mus = w0b_probe.make_tone(str(tmp_path / "music.m4a"))
    base = _base()
    legacy = dict(base, **_card([0.095, 0.13, 0.905, 0.795]))
    legacy["effects"]["picture_cards"][0]["end"] = 8.0

    def case(name, edl, wm):
        return {"name": name, "src": src, "idx": IDX, "edl": edl, "want_wm": wm,
                "asset_locals": {MUSIC_KEY: mus}, "dump_frames": [60]}
    old = os.environ.get("OUTRO_ON_PREVIEW")
    os.environ["OUTRO_ON_PREVIEW"] = "1"
    try:
        res = w0b_probe.run([case("clean", base, False), case("marked", base, True),
                             case("legacy_clean", legacy, False),
                             case("legacy_marked", legacy, True)],
                            str(tmp_path / "out"))
    finally:
        if old is None:
            os.environ.pop("OUTRO_ON_PREVIEW", None)
        else:
            os.environ["OUTRO_ON_PREVIEW"] = old
    for k in ("clean", "marked", "legacy_clean", "legacy_marked"):
        assert res[k]["error"] is None, (k, res[k]["error"])
    a, b = res["clean"], res["marked"]
    assert len(a["frames"]) == len(b["frames"]) == 200 + 125
    # the 5 s end card: md5-identical frame for frame, marked or not
    assert a["frames"][200:] == b["frames"][200:]
    assert res["legacy_clean"]["frames"][200:] == res["legacy_marked"]["frames"][200:]
    w, h = 270, 480                                       # the preview geometry
    slot = renderer.watermark_opaque_mask(w, h)
    corner = renderer.watermark_opaque_mask(w, h, renderer.WATERMARK_CORNER)
    assert slot.sum() > 40 and corner.sum() > 40 and not (slot & corner).any()
    for clean, marked, here, there in (("clean", "marked", slot, corner),
                                       ("legacy_clean", "legacy_marked", corner, slot)):
        y0 = _yuv(res[clean]["dumps"]["60"], w, h)[0].astype(float)
        y1 = _yuv(res[marked]["dumps"]["60"], w, h)[0].astype(float)
        # the mark's opaque type and robot read white where it is laid ...
        assert np.median(y1[here]) >= 200 and np.median(y1[here]) > np.median(y0[here]) + 30
        # ... and nothing of it lands on the other place (encoding noise only)
        assert np.abs(y1[there] - y0[there]).mean() < 8, (marked, np.abs(y1[there] - y0[there]).mean())


def test_the_picture_check_names_what_holds_the_slot():
    """A legacy layout's free-tier export keeps the v6 corner; the PICTURE
    CHECK says what holds the slot, so the next edit hands it back."""
    from schemas import validate_edl
    edl = dict(LEGACY["v6 no-band 4:5 card (top 0.13)"], keep=[[0.0, 8.0]],
               frame={"ratio": "9:16", "mode": "crop"})
    edl["effects"]["picture_cards"][0]["end"] = 8.0
    edl = validate_edl(edl, 8.0).model_dump()
    index = {"video": {"width": 1920, "height": 1080, "fps": 30.0}, "words": []}
    anchor = renderer.watermark_anchor_y(edl, 1920, 1080, W, H)
    p = render_qc.plan(edl, index, W=W, H=H, fps=30.0, want_wm=True, wm_anchor_y=anchor)
    assert p["wm_slot"]["what"] == "picture card 'card'"
    assert p["watermark"]["y"] == renderer.watermark_geometry(W, H, anchor)["ry"]
    line = render_qc.advice({"wm_slot": p["wm_slot"]})[0]
    assert line.startswith("CORNER MARK IN THE OLD CORNER") and "never move the mark" in line
    # a v7 layout: the slot, nothing to say
    edl2 = validate_edl(dict(V7["4:5 card"], keep=[[0.0, 8.0]],
                             frame={"ratio": "9:16", "mode": "crop"},
                             effects={"picture_cards": [dict(
                                 V7["4:5 card"]["effects"]["picture_cards"][0], end=8.0)]}),
                        8.0).model_dump()
    p2 = render_qc.plan(edl2, index, W=W, H=H, fps=30.0, want_wm=True,
                        wm_anchor_y=renderer.watermark_anchor_y(edl2, 1920, 1080, W, H))
    assert p2["wm_slot"] is None
    assert not any(a.startswith("CORNER MARK") for a in render_qc.advice({}))


# ── a card write is told when its edge runs through the slot ─────────────

def test_a_card_write_is_told_when_it_crosses_the_slot():
    import test_picture_layouts as T
    ctx = T._Ctx(1920, 1080)
    out = agent_tools.set_picture_card(ctx, "c", 0, 9, box=[0.0, 0.12, 1.0, 0.72], fit="crop")
    assert out.startswith("EDL v"), out
    assert "NOTE (corner mark): card 'c' runs its edge through the corner mark's brand slot" in out
    assert f"top at y {keepout.card_top(W, H):.3f} or lower" in out
    # the v7 rows (and a full-width card with the mark inside its picture) hear nothing
    for box in (ROWS["band + full width"], ROWS["no band, 4:5 at 81%"], [0.0, 0.10, 1.0, 0.70]):
        ctx = T._Ctx(1920, 1080)
        out = agent_tools.set_picture_card(ctx, "c", 0, 9, box=box, fit="crop")
        assert out.startswith("EDL v") and "NOTE (corner mark)" not in out, (box, out)
    # an unchanged card is not told again; a 16:9 frame has no slot
    edl = {"effects": {"picture_cards": [{"id": "c", "box": [0.0, 0.12, 1.0, 0.72]}]}}
    assert keepout.card_slot_notes(edl, W, H, before=edl) == []
    assert keepout.card_slot_notes(edl, 1920, 1080) == []
    assert len(keepout.card_slot_notes(edl, W, H)) == 1


def test_render_time_captions_keep_to_the_mark_the_render_burns():
    """Captions are placed at render time: a legacy layout's render burns
    the v6 corner, so its captions keep the room that layout was made with;
    a v7 layout's captions keep off the slot."""
    legacy = LEGACY["v6 no-band 4:5 card (top 0.13)"]
    corner = keepout.watermark_zone(W, H, renderer.WATERMARK_CORNER)
    assert caption_place.watermark_box(W, H, legacy) == pytest.approx(tuple(corner))
    assert caption_place.watermark_box(W, H, V7["4:5 card"]) == pytest.approx(
        tuple(keepout.watermark_zone(W, H)))
    # the typeset layout's fallback zone is the slot's too
    src = open(os.path.join(HERE, "..", "typeset", "layoutdoc.py")).read()
    assert "(0.559, 0.115, 0.915, 0.193)" in src
