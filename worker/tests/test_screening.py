import audio_qc
import screening


def _rich_edl():
    return {
        "keep": [[0, 5], [8, 14]],
        "inserts": [{
            "id": "insert-1", "asset_key": "media/broll.mp4",
            "kind": "video", "at_output_s": 5, "duration_s": 2,
        }],
        "overlays": [{
            "id": "overlay-1", "asset_key": "media/cutaway.mp4",
            "kind": "video", "start": 2, "duration_s": 2,
            "fit": "cover",
        }],
        "texts": [{
            "id": "text-1", "text": "HOOK", "start": 0.2, "end": 2,
            "template": "title", "entrance": "pop",
        }],
        "effects": {"zooms": [{
            "id": "zoom-1", "start": 7, "end": 10,
            "mode": "push_in", "strength": 0.25,
        }]},
        "frame": {"ratio": "9:16", "mode": "crop", "focus_track": [
            {"t0": 0, "t1": 5, "x": 0.3, "y": 0.5},
            {"t0": 8, "t1": 14, "x": 0.7, "y": 0.5},
        ]},
    }


def test_screening_plan_sees_authored_decisions_across_finished_program():
    frames = screening.plan(_rich_edl(), 13, max_frames=24, base_frames=8)

    assert len(frames) == 24
    assert frames[0]["time_s"] <= 0.1
    assert frames[-1]["time_s"] >= 12.9
    reasons = " | ".join(row["reason"] for row in frames)
    assert "B-roll 1" in reasons
    assert "insert scene 2" in reasons
    assert "title text 1" in reasons
    assert "zoom 1" in reasons
    assert "shot-specific framing 1" in reasons
    assert "shot-specific framing 2" in reasons
    assert "cut 1→2 before" in reasons and "cut 1→2 after" in reasons
    assert frames == sorted(frames, key=lambda row: row["time_s"])


def test_event_overload_is_spread_in_time_instead_of_sampling_only_opening():
    edl = {"keep": [[0, 120]], "overlays": [], "effects": {"zooms": []}}
    for i in range(30):
        at = i * 4.0
        edl["overlays"].append({
            "id": f"o{i}", "asset_key": f"media/{i}.jpg", "kind": "image",
            "start": at, "duration_s": 1.5, "fit": "cover",
        })
        edl["effects"]["zooms"].append({
            "id": f"z{i}", "start": at + 1.5, "end": at + 2.5,
            "mode": "punch", "strength": 0.2,
        })

    frames = screening.plan(edl, 120, max_frames=20, base_frames=6)
    event_times = [row["time_s"] for row in frames
                   if row["reason"] != "whole-program coverage"]

    assert len(frames) <= 20
    assert any(t < 10 for t in event_times)
    assert any(50 < t < 70 for t in event_times)
    assert any(t > 110 for t in event_times)


def test_screening_pages_keep_numbered_tile_contract():
    frames = screening.plan(_rich_edl(), 13, max_frames=19, base_frames=8)
    grouped = screening.pages(frames, page_tiles=8)

    assert [len(page) for page in grouped] == [8, 8, 3]
    description = screening.describe_page(grouped[1], 2)
    assert description.startswith("screening page 2: tile 1=")
    assert "whole-program coverage" in description or "zoom" in description


def test_explicit_text_motion_gets_an_ordered_state_sequence_in_budget():
    edl = {
        "keep": [[0, 12]],
        "texts": [{
            "id": "tx1", "text": "FOLLOW", "start": 2.0, "end": 5.0,
            "entrance": "none", "exit": "none",
            "motion": {
                "x": [{"t": 0.0, "v": -0.1},
                      {"t": 0.45, "v": 0.5, "ease": "out"}],
                "scale": [{"t": 0.0, "v": 0.7},
                          {"t": 0.25, "v": 1.08, "ease": "out"},
                          {"t": 0.55, "v": 1.0, "ease": "in_out"}],
                "opacity": [{"t": 0.0, "v": 0.0},
                            {"t": 0.18, "v": 1.0, "ease": "out"}],
            },
        }],
        "overlays": [{
            "id": "ov1", "asset_key": "proof.jpg", "kind": "image",
            "start": 8.0, "duration_s": 2.0, "fit": "cover",
        }],
    }
    frames = screening.plan(edl, 12, max_frames=20, base_frames=6)
    motion = [row for row in frames if "text motion 1 state" in row["reason"]]

    assert len(frames) <= 20
    assert len(motion) >= 5
    assert motion == sorted(motion, key=lambda row: row["time_s"])
    assert motion[0]["time_s"] < 2.1
    assert any(abs(row["time_s"] - 2.45) < 0.02 for row in motion)
    assert motion[-1]["time_s"] > 4.9
    # Motion evidence does not consume the whole screening pass.
    assert any("B-roll" in row["reason"] for row in frames)


def test_director_beat_frames_are_prioritized_and_sanitized_in_budget():
    edl = {"keep": [[0, 60]], "overlays": [], "effects": {"zooms": []}}
    for i in range(20):
        edl["overlays"].append({
            "id": f"o{i}", "asset_key": f"media/{i}.jpg", "kind": "image",
            "start": i * 2.8, "duration_s": 1.2, "fit": "cover",
        })
    extra = [
        {"time_s": 7.0, "reason": "planned beat 1 [hook]: tension"},
        {"time_s": 31.0, "reason": " planned   beat 2 [proof]: receipt "},
        {"time_s": 54.0, "reason": "planned beat 3 [resolve]: payoff"},
        {"time_s": -1, "reason": "invalid negative"},
        {"time_s": 61, "reason": "invalid late"},
        {"time_s": 12, "reason": ""},
        "not a frame",
    ]

    frames = screening.plan(edl, 60, max_frames=12, base_frames=5,
                            extra_frames=extra)
    reasons = " | ".join(row["reason"] for row in frames)

    assert len(frames) <= 12
    assert "planned beat 1 [hook]: tension" in reasons
    assert "planned beat 2 [proof]: receipt" in reasons
    assert "planned beat 3 [resolve]: payoff" in reasons
    assert "invalid" not in reasons
    assert any(row["reason"] == "whole-program coverage" for row in frames)


def test_director_frame_near_an_authored_event_keeps_both_reasons():
    edl = {"keep": [[0, 10]], "texts": [{
        "id": "t1", "text": "PROOF", "start": 4, "end": 6,
        "template": "title",
    }]}
    frames = screening.plan(
        edl, 10, max_frames=10, base_frames=4,
        extra_frames=[{"time_s": 5.02,
                       "reason": "planned beat 2 [proof]: show result"}])

    merged = [row for row in frames if "planned beat 2" in row["reason"]]
    assert len(merged) == 1
    assert "title text 1" in merged[0]["reason"]


def test_audio_listener_windows_cover_late_mix_not_first_three_events():
    windows = audio_qc.listen_windows(
        [2, 12, 22, 42, 62, 82, 112], 120, max_windows=3,
        halo_s=1, max_len_s=4)

    assert len(windows) == 3
    assert windows[0][0] <= 1.1
    assert 40 <= windows[1][0] <= 65
    assert windows[-1][0] >= 100


def test_audio_listener_zero_budget_is_honest():
    assert audio_qc.listen_windows([1, 5], 10, max_windows=0) == []
