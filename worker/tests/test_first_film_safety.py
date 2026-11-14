import copy
import json
import pytest

import source_screen
from score import source_guard


def test_screen_samples_accept_the_live_scene_detector_shot_type():
    from schemas import Shot
    typed = [Shot(id=1, start=0., end=10.17), Shot(id=2, start=10.17, end=14.17)]
    times = source_screen.sample_times(14.167, 30., typed)
    assert times == source_screen.sample_times(14.167, 30., [s.model_dump() for s in typed])
    assert any(9.9 <= t < 10.1 for t in times) and all(0 <= t < 14.167 for t in times)


def test_final_frame_seek_cannot_round_past_the_last_decodable_frame():
    times = source_screen.sample_times(35.4, 30, [{"start": 0, "end": 35.4}])
    assert times[-1] == 35.366
    assert float(f"{times[-1]:.3f}") <= 1061 / 30


def test_semantic_tail_cut_stops_at_last_clear_frame_not_detected_shot_edge():
    verdict = {"reviewed_all_frames": True, "branding_screen_present": True,
               "last_content_frame_id": 1, "first_branding_frame_id": 2}
    result = source_screen.interpret(json.dumps(verdict), [0, 9.9, 10.1, 14], 14.2, 30)
    assert result["safe_end_s"] == 9.9
    assert result["safe_end_s"] < 10.1


@pytest.mark.parametrize("answer", [{}, {"reviewed_all_frames": False},
    {"reviewed_all_frames": True, "branding_screen_present": True,
     "last_content_frame_id": 3, "first_branding_frame_id": 2}])
def test_incomplete_screen_evidence_never_admits_first_film(answer):
    with pytest.raises(ValueError):
        source_screen.interpret(json.dumps(answer), [0, 8, 10, 14], 14.2, 30)


def test_private_view_does_not_edit_original_index_and_render_blocks_fallback_tail():
    evidence = {"status": "verified", "version": 1, "safe_end_s": 9.9, "source_sha256": "sha"}
    index = {"video": {"duration": 14.2}, "source_screen": evidence,
             "shots": [{"start": 0, "end": 10.1}, {"start": 10.1, "end": 14.2}]}
    before = copy.deepcopy(index)
    bounded = source_screen.first_film_index(index)
    assert index == before and bounded["video"]["duration"] == 9.9
    assert bounded["shots"] == [{"start": 0, "end": 9.9}]
    source_screen.check_render({"keep": [[0, 9.9]]}, evidence, "original", "sha")
    with pytest.raises(ValueError):
        source_screen.check_render({"keep": [[0, 10.1]]}, evidence, "original", "sha")
    with pytest.raises(ValueError):
        source_screen.check_render({"overlays": [{"asset_key": "original",
            "source_start_s": 9.5, "duration_s": .5}]}, evidence, "original", "sha")


def _scene():
    return ({"frame": [720, 1280], "items": [{"id": "quay", "pic": "main",
             "src": [6.6, 8.], "crop": [.15, 0, .65, 1]}]},
            {"_first_film_source_guard": True, "video": {"width": 1920,
             "height": 1080, "duration": 20}, "shots": [{"start": 0, "end": 6.52},
             {"start": 6.52, "end": 12.85}]})


def test_frame_clearance_rounds_inward_to_the_edl_clock():
    index = {"video": {"duration": 14.2}, "source_screen": {
        "status": "verified", "version": 1, "safe_end_s": 9.866667}}
    assert source_screen.first_film_index(index)["video"]["duration"] == 9.86


def test_full_source_clearance_preserves_a_last_word_at_fractional_eof():
    index = {"video": {"duration": 3.582}, "words": [{"w": "end", "t0": 3, "t1": 3.582}],
             "source_screen": {"status": "verified", "version": 1,
                               "safe_end_s": 3.582, "source_duration_s": 3.582}}
    bounded = source_screen.first_film_index(index)
    assert bounded["words"] == index["words"] and bounded["video"]["duration"] == 3.582


def test_neighbouring_shot_face_does_not_authorize_quay_crop():
    film, index = _scene()
    rows = source_guard.apply(film, index, {"samples": [{"t": 6.5, "face": [.3, .2, .5, .4]}]})
    c = film["items"][0]["crop"]
    assert c[0] <= 0 and c[1] <= 0 and c[2] >= 1 and c[3] >= 1
    assert rows[0]["reason"] == "full_frame_missing_subject_boxes"


def test_subject_fully_outside_crop_still_counts_and_all_camera_frames_are_safe():
    film, index = _scene()
    film["items"][0]["crop"] = [[.15, 0, .65, 1], [.2, 0, .7, 1]]
    rows = source_guard.apply(film, index, {"samples": [{"t": 7.5, "person": [.8, .1, .95, .9]}]})
    assert all(c[0] <= 0 and c[2] >= 1 for c in film["items"][0]["crop"])
    assert rows[0]["reason"] == "full_frame_subject_outside_crop"


def test_existing_edits_outside_first_film_unchanged():
    film, index = _scene()
    del index["_first_film_source_guard"]
    before = copy.deepcopy(film)
    assert source_guard.apply(film, index, {}) == [] and film == before


def test_safe_source_cutaway_is_contained_not_recropped_by_overlay_cover(tmp_path):
    from score import lower
    from types import SimpleNamespace
    film, index = _scene()
    film.update(fps=30, spine=[], assets={"main": {"kind": "video", "path": "source.mp4",
                                               "w": 1920, "h": 1080}})
    film["items"][0].update(k="shot", f=[0, 42])
    source_guard.apply(film, index, {})
    bake = SimpleNamespace(locals={}, plate=lambda *a: "backing.png")
    overlays, *_ = lower.lower_pictures(film, str(tmp_path), bake, {})
    foreground = next(o for o in overlays if o["asset_key"] == "main")
    assert not foreground.get("fit") and not foreground.get("source_crop")
    assert foreground["scale"] == 1 and foreground["x"] == foreground["y"] == .5
    assert overlays[0]["asset_key"] == "backing.png"
    # Publication normalizes by (z_index, start, id), not insertion order.
    # The backing must still sit below the source after that real boundary.
    from schemas import default_edl, validate_edl
    edl = default_edl(14.2)
    edl["overlays"] = overlays
    normalized = validate_edl(edl, 14.2).model_dump()
    assert [o["asset_key"] for o in normalized["overlays"]] == ["backing.png", "main"]
