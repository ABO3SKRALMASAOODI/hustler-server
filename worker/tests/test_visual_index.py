import os

from PIL import Image

import visual_index


def _image(path, color, mark=False):
    image = Image.new("RGB", (320, 180), color)
    if mark:
        for x in range(40, 100):
            for y in range(30, 80):
                image.putpixel((x, y), (255, 255, 255))
    image.save(path, "JPEG")


def test_candidate_plan_includes_editorial_boundaries():
    rows = visual_index._candidate_times(
        12,
        [{"id": 1, "start": 0, "end": 5},
         {"id": 2, "start": 5, "end": 12}],
        {"change_times_s": [7.25]},
        {"samples": [{"t": 2, "faces": [], "text": []},
                     {"t": 6, "faces": [[.1, .1, .3, .4]], "text": []}]},
        [{"t0": 8, "t1": 10}],
    )
    reasons = {reason for _at, why in rows for reason in why}
    assert "asset_start" in reasons
    assert "asset_end" in reasons
    assert "shot_1_midpoint" in reasons
    assert "shot_2_start" in reasons
    assert "measured_motion_change" in reasons
    assert "face_text_layout_change" in reasons
    assert "transcript_aligned" in reasons


def test_near_duplicate_frames_collapse_without_losing_coverage(tmp_path):
    first = os.path.join(tmp_path, "first.jpg")
    repeat = os.path.join(tmp_path, "repeat.jpg")
    distinct = os.path.join(tmp_path, "distinct.jpg")
    _image(first, (30, 40, 50), mark=True)
    _image(repeat, (30, 40, 50), mark=True)
    _image(distinct, (180, 20, 20), mark=False)
    candidates = [(1.0, ["shot_1_midpoint"]),
                  (5.0, ["transcript_aligned"]),
                  (9.0, ["shot_3_midpoint"])]
    shots = [{"id": 1, "start": 0, "end": 3},
             {"id": 2, "start": 4, "end": 6},
             {"id": 3, "start": 8, "end": 10}]
    rows = visual_index._cluster_candidates(
        candidates, {0: first, 1: repeat, 2: distinct}, shots, {}, {})
    assert len(rows) == 2
    repeated = next(row for row in rows
                    if row["sample_count_collapsed"] == 2)
    assert repeated["shot_ids"] == [1, 2]
    assert repeated["covered_ranges"] == [[0.0, 3.0], [4.0, 6.0]]
    assert "transcript_aligned" in repeated["selection_reasons"]


def test_contact_sheet_labels_stable_evidence_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(visual_index.config, "VISUAL_SHEET_FRAMES", 2)
    rows = []
    for index, color in enumerate(((20, 20, 20), (200, 200, 200)), 1):
        path = os.path.join(tmp_path, f"{index}.jpg")
        _image(path, color)
        rows.append({"evidence_id": f"ve_{index}", "source_clock": f"0:0{index}.00",
                     "frame_hash": str(index) * 64, "_path": path,
                     "covered_time_range": [index, index + 1]})
    first = visual_index._build_sheets(rows, os.path.join(tmp_path, "a"))
    second = visual_index._build_sheets(rows, os.path.join(tmp_path, "b"))
    assert first[0]["digest"] == second[0]["digest"]
    assert first[0]["evidence_ids"] == ["ve_1", "ve_2"]
    assert os.path.exists(first[0]["path"])


def test_compact_storyboard_retains_reopen_coordinates():
    text = visual_index.compact_text({
        "version": 1, "distinct_clusters": 1, "duplicates_collapsed": 8,
        "semantic_status": "complete",
        "evidence": [{"evidence_id": "ve_abc", "source_clock": "0:12.50",
                      "covered_ranges": [[10, 14]], "shot_ids": [3],
                      "semantic_description": "A presenter points at UI."}],
    })
    assert "ve_abc" in text
    assert "SOURCE 0:12.50" in text
    assert "0:10.00-0:14.00" in text
    assert "A presenter points at UI" in text
