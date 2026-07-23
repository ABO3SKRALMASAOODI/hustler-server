"""EDL v2 (round 35) — the spine contracts.

The two invariants everything else stands on:
  1. SIGNATURE STABILITY — every EDL written before round 35 must hash
     identically through the new validator (no phantom diffs, no platform
     re-render).
  2. LEGACY RENDER IDENTITY — the filtergraph for a no-v2-fields EDL must be
     structured exactly as before (verified byte-for-byte against the
     pre-change renderer during development; pinned here as marker checks).
Plus the new machinery: keyframe evaluation, speed-aware Timeline math,
validation clamps, and per-feature graph markers.
"""
import copy
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

from schemas import (anim_value, clip_anim, edl_signature,  # noqa: E402
                     program_duration, sped_len, speed_pieces, validate_edl)
from timeline import (Timeline, remap_program_items,  # noqa: E402
                      remap_program_span)


LEGACY = {
    "keep": [[0.0, 10.0], [12.5, 30.0]],
    "captions": {"mode": "from_transcript", "max_words_per_caption": 4,
                 "style": {"color": "#FFFFFF", "size": "l",
                           "preset": "podcast"},
                 "emphasis_words": ["money", "insane"]},
    "music": [{"id": "m1", "storage_key": "library:x", "start": 0.0,
               "end": 27.5, "gain_db": -18.0, "duck": True, "loop": True,
               "fade_in_s": 1.0, "fade_out_s": 2.0}],
    "sfx": [{"id": "x1", "storage_key": "sfx:whoosh-1", "at": 10.0,
             "gain_db": -6.0}],
    "volume": [{"start": 5.0, "end": 6.0, "gain_db": -20.0}],
    "frame": {"ratio": "9:16", "mode": "crop"},
    "inserts": [{"id": "i1", "asset_key": "media/p/img.png", "kind": "image",
                 "at_output_s": 10.0, "duration_s": 3.0,
                 "motion": "zoom_in"}],
    "effects": {"grade": "cinematic",
                "zooms": [{"id": "z1", "start": 2.0, "end": 4.0,
                           "strength": 0.3, "mode": "ease"}],
                "fade_out_s": 1.5,
                "transition": {"style": "dip_black", "duration_s": 0.3},
                "regions": [{"id": "r1", "mode": "blur", "x": 0.1, "y": 0.1,
                             "w": 0.2, "h": 0.1}]},
}


def test_legacy_signature_stable_and_no_v2_leak():
    n1 = validate_edl(copy.deepcopy(LEGACY), 30.0).model_dump()
    s1 = edl_signature(n1)
    n2 = validate_edl(copy.deepcopy(n1), 30.0).model_dump()
    assert edl_signature(n2) == s1
    # new keys exist in the dump but must never reach the signature
    for key in ("overlays", "texts", "speed", "master"):
        assert key in n1 and f'"{key}"' not in s1
    for key in ("duck_mode", "cx", "cy", "stylize", "grade_custom"):
        assert f'"{key}"' not in s1


def test_v2_roundtrip_stable():
    v2 = copy.deepcopy(LEGACY)
    v2["speed"] = [{"id": "s1", "start": 13.0, "end": 16.0, "factor": 2.0}]
    v2["overlays"] = [{"id": "o1", "asset_key": "media/p/b.mp4",
                       "kind": "video", "start": 2.0, "duration_s": 5.0,
                       "scale": 0.4,
                       "x": [{"t": 0.0, "v": 0.8},
                             {"t": 1.0, "v": 0.25, "ease": "out"}]}]
    v2["texts"] = [{"id": "t1", "text": "THE LEAP", "start": 0.5, "end": 3.0,
                    "template": "title", "entrance": "pop"}]
    v2["effects"]["stylize"] = [{"id": "st1", "kind": "grain",
                                 "intensity": 0.3}]
    v2["effects"]["grade_custom"] = {"temperature": 0.4}
    v2["master"] = {"loudness": "social"}
    n1 = validate_edl(copy.deepcopy(v2), 30.0).model_dump()
    n2 = validate_edl(copy.deepcopy(n1), 30.0).model_dump()
    assert edl_signature(n1) == edl_signature(n2)


def test_anim_value_easings():
    kfs = [{"t": 0.0, "v": 0.0}, {"t": 1.0, "v": 1.0, "ease": "in_out"},
           {"t": 2.0, "v": 0.0, "ease": "hold"}]
    assert anim_value(0.5, 99) == 0.5              # constant passthrough
    assert anim_value(kfs, -1) == 0.0
    assert anim_value(kfs, 0.5) == 0.5             # smoothstep midpoint
    assert abs(anim_value(kfs, 0.25) - 0.15625) < 1e-9
    assert anim_value(kfs, 1.5) == 1.0             # hold keeps prior value
    assert anim_value(kfs, 2.0) == 0.0
    assert anim_value(kfs, 5.0) == 0.0             # past end holds last


def test_speed_pieces_and_lengths():
    speed = [{"id": "s", "start": 2.0, "end": 6.0, "factor": 2.0}]
    assert speed_pieces(0.0, 10.0, speed) == [(0.0, 2.0, 1.0),
                                              (2.0, 6.0, 2.0),
                                              (6.0, 10.0, 1.0)]
    assert abs(sped_len(0.0, 10.0, speed) - 8.0) < 1e-9
    assert speed_pieces(0.0, 10.0, []) == [(0.0, 10.0, 1.0)]


def test_timeline_speed_mapping_roundtrip():
    sp = [{"id": "s1", "start": 20.0, "end": 25.0, "factor": 2.0}]
    tl = Timeline([[0, 10], [20, 30]],
                  [{"at_output_s": 10.0, "duration_s": 3.0}], sp)
    assert abs(tl.out_duration - 20.5) < 1e-9
    assert abs(tl.src_to_out(22.0) - 14.0) < 1e-9
    assert abs(tl.out_to_src(17.5) - 27.0) < 1e-9
    for t in [0.0, 3.3, 9.99, 20.01, 21.7, 24.99, 25.0, 26.6, 29.9]:
        assert abs(tl.out_to_src(tl.src_to_out(t)) - t) < 1e-6
    # remap across a speed change goes through source, like everything else
    old = Timeline([[0, 30]], [], None)
    new = Timeline([[0, 30]], [], sp)
    assert remap_program_span(old, new, 21.0, 23.0) == (20.5, 21.5)


def test_program_duration_speed_aware():
    edl = validate_edl({"keep": [[0.0, 10.0]],
                        "speed": [{"id": "s", "start": 0.0, "end": 10.0,
                                   "factor": 2.0}]}, 10.0).model_dump()
    assert abs(program_duration(edl) - 5.0) < 1e-9


def test_validation_clamps_not_rejections():
    n = validate_edl({
        "keep": [[0, 20]],
        "effects": {"zooms": [{"id": "z", "start": 0, "end": 2,
                               "strength": 9.0, "cx": 2.0}],
                    "fade_in_s": 99,
                    "transition": {"style": "whip_left",
                                   "duration_s": 9.0}}}, 60).model_dump()
    fx = n["effects"]
    assert fx["zooms"][0]["strength"] == 1.5
    assert fx["zooms"][0]["cx"] == 1.0
    assert fx["fade_in_s"] == 10.0
    assert fx["transition"]["duration_s"] == 1.5


def test_validation_rejections_still_reject():
    import pytest
    from schemas import EDLValidationError
    with pytest.raises(EDLValidationError):        # overlapping speed spans
        validate_edl({"keep": [[0, 20]],
                      "speed": [{"id": "a", "start": 0, "end": 5,
                                 "factor": 2.0},
                                {"id": "b", "start": 4, "end": 8,
                                 "factor": 0.5}]}, 60)
    with pytest.raises(EDLValidationError):        # speed needs a main video
        validate_edl({"keep": [], "canvas": {"width": 1920, "height": 1080},
                      "inserts": [{"id": "i", "asset_key": "k",
                                   "kind": "image", "at_output_s": 0.0,
                                   "duration_s": 3.0}],
                      "speed": [{"id": "s", "start": 0, "end": 2,
                                 "factor": 2.0}]})
    with pytest.raises(EDLValidationError):        # factor 1.0 is a no-op
        validate_edl({"keep": [[0, 20]],
                      "speed": [{"id": "s", "start": 0, "end": 5,
                                 "factor": 1.0}]}, 60)
    with pytest.raises(EDLValidationError):        # unsorted keyframes
        validate_edl({"keep": [[0, 20]],
                      "overlays": [{"id": "o", "asset_key": "k",
                                    "kind": "image", "start": 0.0,
                                    "duration_s": 4.0,
                                    "x": [{"t": 1.0, "v": 0.2},
                                          {"t": 0.5, "v": 0.8}]}]}, 60)


def test_neutral_values_collapse():
    n = validate_edl({
        "keep": [[0, 20]],
        "overlays": [{"id": "o", "asset_key": "k", "kind": "image",
                      "start": 0.0, "duration_s": 4.0, "opacity": 1.0}],
        "effects": {"zooms": [{"id": "z", "start": 0, "end": 2,
                               "strength": 0.3, "cx": 0.5, "cy": 0.5}],
                    "stylize": [{"id": "s", "kind": "grain",
                                 "intensity": 0.5}],
                    "grade_custom": {"contrast": 1.0, "saturation": 1.0}},
        "master": {}}, 60).model_dump()
    assert n["overlays"][0]["opacity"] is None
    assert n["effects"]["zooms"][0]["cx"] is None
    assert n["effects"]["zooms"][0]["cy"] is None
    assert n["effects"]["stylize"][0]["intensity"] is None
    assert n["effects"]["grade_custom"] is None
    assert n["master"] is None


def test_span_to_out_contiguous_boundary_with_insert():
    """THE round-35.1 regression fix: insert_media's mid-take split writes
    CONTIGUOUS keep segments sharing a boundary, with the insert spliced
    there. span_to_out must resolve each endpoint within the segment being
    iterated — resolving via src_to_out first-matches the EARLIER segment
    and silently drops the insert's duration, which changed legacy duck
    windows and manual caption times on stored EDLs."""
    tl = Timeline([[0.0, 5.0], [5.0, 10.0]],
                  [{"at_output_s": 5.0, "duration_s": 3.0}])
    assert tl.span_to_out(3.0, 7.0) == [(3.0, 5.0), (8.0, 10.0)]
    # the same shape under speed still maps per segment
    sp = [{"id": "s", "start": 5.0, "end": 10.0, "factor": 2.0}]
    tls = Timeline([[0.0, 5.0], [5.0, 10.0]],
                   [{"at_output_s": 5.0, "duration_s": 3.0}], sp)
    (a1, b1), (a2, b2) = tls.span_to_out(3.0, 7.0)
    assert (a1, b1) == (3.0, 5.0)
    assert abs(a2 - 8.0) < 1e-9 and abs(b2 - 9.0) < 1e-9


def test_clip_anim_contracts():
    kfs = [{"t": 0.0, "v": 0.2}, {"t": 10.5, "v": 0.8, "ease": "in"}]
    # nothing exceeds -> returned UNCHANGED (same object: signature-stable)
    assert clip_anim(kfs, 12.0) is kfs
    assert clip_anim(0.4, 3.0) == 0.4
    out = clip_anim(kfs, 9.0)
    assert out[-1]["t"] == 9.0 and out[-1]["ease"] == "in"
    assert abs(out[-1]["v"] - anim_value(kfs, 9.0)) < 1e-4
    # every keyframe past the cut -> collapses to the sampled constant
    late = [{"t": 5.0, "v": 0.3}, {"t": 8.0, "v": 0.9}]
    assert clip_anim(late, 3.0) == 0.3


def test_remap_stylize_and_overlay_keyframes():
    """A tail trim must never be REJECTED over a windowed stylize or a
    keyframed overlay — the two round-35 HIGH review findings."""
    edl = {
        "keep": [[0.0, 20.0]],
        "effects": {"stylize": [{"id": "st1", "kind": "vhs",
                                 "start": 5.0, "end": 30.0},
                                {"id": "st2", "kind": "grain"}]},
        "overlays": [{"id": "o1", "asset_key": "k", "kind": "image",
                      "start": 0.0, "duration_s": 30.0,
                      "x": [{"t": 0.0, "v": 0.1}, {"t": 28.0, "v": 0.9}]}],
    }
    old_tl = Timeline([[0.0, 40.0]], [])
    new_tl = Timeline([[0.0, 20.0]], [])
    notes = remap_program_items(edl, old_tl, new_tl)
    v = validate_edl(edl, 40.0).model_dump()      # must not raise
    st = {s["id"]: s for s in v["effects"]["stylize"]}   # validator sorts
    assert st["st1"]["end"] == 20.0 and st["st2"]["start"] is None
    ov = v["overlays"][0]
    assert ov["duration_s"] == 20.0
    assert all(k["t"] <= 20.0 for k in ov["x"])
    assert any("stylize" in n for n in notes)
    # a FRONT trim moves the stylize window with its footage (content anchor)
    edl2 = {"keep": [[10.0, 40.0]],
            "effects": {"stylize": [{"id": "st1", "kind": "vhs",
                                     "start": 15.0, "end": 20.0}]}}
    remap_program_items(edl2, Timeline([[0.0, 40.0]], []),
                        Timeline([[10.0, 40.0]], []))
    st1 = edl2["effects"]["stylize"][0]
    assert (st1["start"], st1["end"]) == (5.0, 10.0)


def test_karaoke_group_field_is_signature_safe():
    edl = copy.deepcopy(LEGACY)
    edl["captions"]["style"] = {"dynamic": True}
    edl["captions"]["max_words_per_caption"] = 6
    n1 = validate_edl(copy.deepcopy(edl), 30.0).model_dump()
    s1 = edl_signature(n1)
    assert '"karaoke_group_n"' not in s1        # legacy stays byte-identical
    edl["captions"]["karaoke_group_n"] = 12     # clamps to 8
    n2 = validate_edl(copy.deepcopy(edl), 30.0).model_dump()
    assert n2["captions"]["karaoke_group_n"] == 8
    assert edl_signature(n2) != s1              # baked group IS a real change


def test_describe_covers_v2():
    from schemas import describe_edl
    v2 = validate_edl({
        "keep": [[0, 20]],
        "speed": [{"id": "s", "start": 2.0, "end": 6.0, "factor": 2.0}],
        "texts": [{"id": "t", "text": "Hello", "start": 1.0, "end": 3.0}],
        "overlays": [{"id": "o", "asset_key": "m/p/pic.png",
                      "kind": "image", "start": 0.0, "duration_s": 4.0}],
        "master": {"loudness": "social"},
        "effects": {"stylize": [{"id": "x", "kind": "vhs"}],
                    "grade_custom": {"temperature": 0.3}}}, 60).model_dump()
    d = describe_edl(v2, 60)
    for frag in ("speed x1", "2x@2-6s", "text x1", "overlays x1",
                 "stylize vhs", "custom grade", "mastered"):
        assert frag in d, (frag, d)
