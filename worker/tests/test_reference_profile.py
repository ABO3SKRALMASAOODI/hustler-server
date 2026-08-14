import reference_profile


def _index():
    shots = [{"start": i * 5.0, "end": (i + 1) * 5.0}
             for i in range(8)]
    words = [{"w": "word", "t0": i * 0.5, "t1": i * 0.5 + 0.25}
             for i in range(40)]
    samples = []
    for i in range(8):
        samples.append({
            "t": i * 5.0 + 0.5,
            "faces": [[0.2, 0.1, 0.6, 0.7]] if i < 6 else [],
            "text": [[0.2, 0.7, 0.8, 0.78]] if i % 2 == 0 else [],
            "dense_ui": i == 7,
        })
    return {
        "video": {"duration": 40.0, "width": 1080, "height": 1920},
        "shots": shots,
        "words": words,
        "speakers": 1,
        "language": "en",
        "perception": {
            "bpm": 120, "bpm_conf": 0.92,
            "beats": [i * 0.5 for i in range(81)],
            "energy": [-24] * 10 + [-21] * 10 + [-16] * 10 + [-20] * 10,
        },
        "motion": {
            "analyzed_window_s": 30.0, "intensity": "moderate",
            "mean_frame_change": 4.2, "p90_frame_change": 8.5,
            "freeze_share": 0.1, "abrupt_changes_per_10s": 1.8,
        },
        "spatial": {"samples": samples},
    }


def test_reference_profile_measures_relationships_not_a_style_label():
    profile = reference_profile.from_index(_index())

    assert profile["duration_s"] == 40
    assert profile["aspect"]["ratio"] == 0.562
    assert profile["rhythm"]["shots"] == 8
    assert profile["rhythm"]["cuts"] == 7
    assert profile["rhythm"]["shot_median_s"] == 5
    assert profile["rhythm"]["median_by_quarter_s"] == [5, 5, 5, 5]
    assert profile["music_relation"]["bpm"] == 120
    assert profile["music_relation"]["relationship"] == \
        "strong_phrase_or_grid_lock"
    assert profile["energy"]["shape"] == "late_peak_then_release"
    assert profile["motion"]["intensity"] == "moderate"
    assert profile["speech"]["words"] == 40
    assert profile["composition"]["face_presence"] == .75
    assert profile["composition"]["dense_ui_presence"] == .125


def test_reference_description_warns_against_copying_raw_counts():
    text = reference_profile.describe(reference_profile.from_index(_index()))

    assert "transfer its relationships and hierarchy" in text
    assert "do not blindly copy raw counts" in text
    assert "p10/median/p90" in text
    assert "strong_phrase_or_grid_lock" in text
    assert "120 BPM" in text
    assert "late_peak_then_release" in text
    assert "attached reference filmstrip" in text


def test_reference_profile_degrades_honestly_without_measurements():
    profile = reference_profile.from_index({
        "video": {"duration": 12.0, "width": 1920, "height": 1080}})
    text = reference_profile.describe(profile)

    assert profile["music_relation"]["relationship"] == "not_measured"
    assert profile["energy"]["shape"] == "not_measured"
    assert "motion=not measured" in text
    assert "0%" in text
