"""Temporal candidate evidence distinguishes movement from attractive stills."""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import motion_judge                                           # noqa: E402


def test_identical_frames_are_reported_as_frozen_not_motion():
    frames = np.full((20, 45, 80), 90, dtype=np.uint8)
    got = motion_judge.profile_frames(frames, sample_fps=2.0)

    assert got["intensity"] == "frozen_or_nearly_static"
    assert got["freeze_share"] == 1.0
    assert got["abrupt_changes"] == 0


def test_slow_spatial_drift_is_gentle_continuous_motion():
    base = np.tile(np.linspace(20, 220, 80, dtype=np.uint8), (45, 1))
    frames = np.stack([np.roll(base, i, axis=1) for i in range(24)])
    got = motion_judge.profile_frames(frames, sample_fps=3.0)

    assert got["intensity"] in ("gentle", "moderate")
    assert got["freeze_share"] < 0.2
    assert got["abrupt_changes"] == 0


def test_a_single_edit_is_not_mislabeled_as_camera_motion():
    a = np.full((10, 45, 80), 45, dtype=np.uint8)
    b = np.full((10, 45, 80), 205, dtype=np.uint8)
    got = motion_judge.profile_frames(np.concatenate([a, b]), 2.0)

    assert got["abrupt_changes"] == 1
    assert got["intensity"] == "frozen_or_nearly_static"
    assert 4.5 <= got["change_times_s"][0] <= 5.5


def test_black_or_blank_material_is_explicit_evidence():
    frames = np.zeros((12, 45, 80), dtype=np.uint8)
    got = motion_judge.profile_frames(frames, sample_fps=2.0)

    assert got["blank_share"] == 1.0
    assert "100% blank/black" in motion_judge.describe(got)
    assert "not continuous playback" in motion_judge.describe(got)


def test_long_video_budget_is_distributed_from_opening_to_ending():
    windows = motion_judge.plan_windows(600.0)

    assert len(windows) == motion_judge.MAX_SECTIONS
    assert windows[0][0] == 0.0
    assert windows[-1][0] + windows[-1][1] == 600.0
    assert sum(window for _start, window in windows) == \
        motion_judge.MAX_WINDOW_S


def test_distributed_sections_do_not_create_fake_cross_window_cuts():
    sections = [
        {"start_s": 0.0, "end_s": 5.0, "analyzed_window_s": 5.0,
         "sample_fps": 2.0, "samples": 10, "intensity": "gentle",
         "mean_frame_change": 2.0, "p90_frame_change": 4.0,
         "freeze_share": .2, "static_share": .4, "blank_share": 0,
         "abrupt_changes": 1, "change_times_s": [2.0]},
        {"start_s": 95.0, "end_s": 100.0, "analyzed_window_s": 5.0,
         "sample_fps": 2.0, "samples": 10, "intensity": "moderate",
         "mean_frame_change": 5.0, "p90_frame_change": 9.0,
         "freeze_share": .1, "static_share": .2, "blank_share": 0,
         "abrupt_changes": 1, "change_times_s": [97.0]},
    ]
    got = motion_judge.combine_profiles(sections, 100.0)

    assert got["sampling_mode"] == "distributed_windows"
    assert got["abrupt_changes"] == 2
    assert got["change_times_s"] == [2.0, 97.0]
    assert got["analyzed_window_s"] == 10.0
    assert got["source_coverage_s"] == 100.0
    assert len(got["sections"]) == 2


def test_analyzer_seeks_across_runtime_under_one_sample_budget(monkeypatch):
    seen = []

    def fake_decode(_path, start, window, fps, budget):
        seen.append((start, window, fps, budget))
        # Moving gradient avoids the frozen classifier without real ffmpeg.
        base = np.tile(np.arange(80, dtype=np.uint8), (45, 1))
        return np.stack([np.roll(base, i, axis=1)
                         for i in range(max(2, budget))])

    monkeypatch.setattr(motion_judge, "_decode_window", fake_decode)
    got = motion_judge.analyze_video("unused.mp4", duration_s=300.0)

    assert len(seen) == motion_judge.MAX_SECTIONS
    assert seen[0][0] == 0.0
    assert seen[-1][0] > 290.0
    assert sum(row[3] for row in seen) <= motion_judge.MAX_SAMPLES
    assert got["samples"] <= motion_judge.MAX_SAMPLES
    assert "distributed windows over 300s" in motion_judge.describe(got)


def test_legacy_long_profile_is_labeled_opening_only():
    old = {"analyzed_window_s": 30.0, "intensity": "gentle",
           "freeze_share": .2, "blank_share": 0,
           "abrupt_changes": 1}

    text = motion_judge.describe(old, source_duration_s=600.0)

    assert "opening only of 600s" in text
    assert "later motion is not measured" in text


def test_lazy_upgrade_persists_only_the_motion_sidecar(monkeypatch):
    upgraded = {"version": motion_judge.MOTION_PROFILE_VERSION,
                "sampling_mode": "distributed_windows", "sections": []}
    monkeypatch.setattr(motion_judge, "analyze_video",
                        lambda path, duration_s=None: upgraded)

    class WorkerDb:
        def __init__(self):
            self.calls = []

        def run(self, fn, *args):
            self.calls.append((fn.__name__, args))

    class Dbx:
        @staticmethod
        def set_index_motion(*_args):
            pass

    db = WorkerDb()
    got = motion_judge.get_or_compute_for_index(
        db, Dbx, {"video_sha256": "sha", "pipeline_version": 10,
                  "json": {"video": {"duration": 600},
                           "motion": {"version": 1}}}, "proxy.mp4")

    assert got is upgraded
    assert db.calls == [("set_index_motion", ("sha", upgraded, 10))]
