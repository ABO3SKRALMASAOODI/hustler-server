import os

from PIL import Image

import config
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
    assert "0:10.00-0:14.00" in text
    assert "A presenter points at UI" in text


def _p0_115_storyboard(n=1149, duration=4373.0):
    """The shape of project 5312's index (P0 #115): a 73-minute school report
    with 1,149 distinct clusters, most with a one-sentence description."""
    rows = []
    for i in range(n):
        t = duration * i / (n - 1)
        rows.append({
            "evidence_id": f"ve_{i:016x}", "source_clock": visual_index._clock(t),
            "covered_ranges": [[t, min(duration, t + 4.0)]], "shot_ids": [1 + i * 147 // (n - 1)],
            "semantic_description": (None if i % 2 else
                                     "Two people in red tops walk apart on a shaded "
                                     "tree-lined road toward the camera."),
            "faces": [], "text_geometry": [[0, 0, 1, 1]] * 4, "motion_state": "high"})
    return {"version": 1, "distinct_clusters": n, "duplicates_collapsed": 566,
            "semantic_status": "partial_raw_contact_sheets_required", "evidence": rows}


def test_p0_115_a_long_source_digest_is_bounded_and_spans_the_whole_clock():
    sb = _p0_115_storyboard()
    full = len(visual_index.compact_text(sb, max_chars=0))
    assert full > 50000                      # the unbounded digest that refused the request
    text = visual_index.compact_text(sb)
    assert len(text) <= config.STORYBOARD_DIGEST_MAX_CHARS
    assert text.startswith("VISUAL STORYBOARD v1: 1149 distinct clusters")
    assert "more clusters left out of this digest" in text and "look_at" in text
    kept = [line for line in text.splitlines() if line.startswith("- ")]
    assert kept[0].startswith("- " + visual_index._clock(0.0))
    assert kept[-1].startswith("- " + visual_index._clock(4373.0))
    assert 100 < len(kept) < 1149


def test_a_normal_storyboard_digest_is_unchanged():
    sb = _p0_115_storyboard(n=89, duration=600.0)
    text = visual_index.compact_text(sb)
    assert text == visual_index.compact_text(sb, max_chars=0)
    assert "left out" not in text and text.count("\n- ") == 45
    assert "44 sampled moments carry no description" in text


# ── #861: analysis spend is proportionate on long sources ──

def _semantic_world(monkeypatch, clusters):
    import json

    import llm
    import storage
    evidence = [{"evidence_id": f"ve_{i:05d}", "frame_hash": f"{i:064x}",
                 "representative_t": i * 1.9,
                 "covered_time_range": [i * 1.9, i * 1.9 + 1], "_path": ""}
                for i in range(clusters)]

    def sheets(rows, _out):
        per = int(config.VISUAL_SHEET_FRAMES)
        return [{"path": f"s{k}", "digest": f"d{k}",
                 "evidence_ids": [r["evidence_id"] for r in rows[k:k + per]],
                 "t0": 0, "t1": 1} for k in range(0, len(rows), per)]

    calls = []

    def ask(prompt, paths, **_kw):
        calls.append(len(paths))
        return json.dumps([{"evidence_id": i, "semantic_description": "seen"}
                           for i in json.loads(prompt)])

    monkeypatch.setattr(visual_index, "_build_sheets", sheets)
    monkeypatch.setattr(visual_index, "_semantic_prompt",
                        lambda rows: json.dumps([r["evidence_id"] for r in rows]))
    monkeypatch.setattr(visual_index, "_write_cached_semantics", lambda r, w: None)
    monkeypatch.setattr(storage, "exists", lambda key: False)
    monkeypatch.setattr(llm, "vision_available", lambda: True)
    monkeypatch.setattr(llm, "ask_vision", ask)
    monkeypatch.setattr(config, "VISION_CAPTION_PARALLELISM", 1)
    return evidence, sheets(evidence, None), calls


def test_861_a_long_source_describes_an_even_sample_not_every_cluster(
        monkeypatch, tmp_path):
    """2 h 39 min (p5543): about 4,900 clusters, 102 calls, $0.38 of Haiku."""
    evidence, sheets, calls = _semantic_world(monkeypatch, 4900)
    status = visual_index._caption_semantics(evidence, sheets, str(tmp_path))
    described = [r for r in evidence if r.get("semantic_description")]
    assert status == "sampled"
    assert len(calls) <= 20 and sum(calls) <= 160
    assert len(described) == config.VISUAL_SEMANTIC_MAX_CLUSTERS
    # spread over the whole clock, first and last moments included
    assert described[0] is evidence[0] and described[-1] is evidence[-1]
    gaps = [b["representative_t"] - a["representative_t"]
            for a, b in zip(described, described[1:])]
    assert max(gaps) < 3 * (evidence[-1]["representative_t"] / len(described))
    text = visual_index.compact_text({"version": 1, "semantic_status": status,
                                      "evidence": evidence})
    assert "look_at" in text


def test_a_short_clip_still_describes_every_cluster(monkeypatch, tmp_path):
    evidence, sheets, calls = _semantic_world(monkeypatch, 300)
    status = visual_index._caption_semantics(evidence, sheets, str(tmp_path))
    assert status == "complete"
    assert all(r.get("semantic_description") for r in evidence)
    assert len(calls) == -(-len(sheets) // config.VISUAL_SEMANTIC_SHEETS_PER_CALL)
