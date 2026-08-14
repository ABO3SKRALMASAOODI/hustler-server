"""Finished files can be converted into fair, repeatable A/B evidence."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import benchmark_runner as runner  # noqa: E402


def test_visual_plan_is_bounded_and_covers_opening_and_closing():
    plan = runner.visual_plan(60, max_frames=12)
    assert len(plan) == 12
    assert [row["time_s"] for row in plan] == sorted(
        row["time_s"] for row in plan)
    assert plan[0]["time_s"] <= .1
    assert plan[-1]["time_s"] >= 59.9
    assert all("whole-program" in row["reason"]
               or "frame" in row["reason"] for row in plan)


def test_audio_windows_cover_long_program_and_dedupe_short_one():
    assert runner.audio_windows(60, span_s=6) == [
        (0.0, 6.0), (27.0, 33.0), (54.0, 60.0)]
    assert runner.audio_windows(4, span_s=6) == [(0.0, 4.0)]


def test_prepare_side_builds_pages_and_labeled_audio_reel(
        monkeypatch, tmp_path):
    video = tmp_path / "finished.mp4"
    video.write_bytes(b"video")
    built = []

    monkeypatch.setattr(
        runner.media, "probe",
        lambda _path: {"duration": 30.0, "has_audio": True})

    def fake_sheet(_video, path, times, **_kwargs):
        built.append(list(times))
        with open(path, "wb") as target:
            target.write(b"jpg")

    monkeypatch.setattr(runner.sheets, "build_frames_sheet", fake_sheet)

    def fake_audio(_video, path, windows):
        with open(path, "wb") as target:
            target.write(b"mp3")
        assert windows == [(0.0, 6.0), (12.0, 18.0), (24.0, 30.0)]
        return path

    monkeypatch.setattr(runner, "_audio_reel", fake_audio)
    got = runner.prepare_side(
        str(video), str(tmp_path / "evidence"), "left",
        story_text="hello", max_frames=10, page_tiles=6)

    assert len(built) == 2
    assert sum(map(len, built)) == 10
    assert len(got["visual_paths"]) == 2
    assert got["audio_label"] == "0.00-6.00s; 12.00-18.00s; 24.00-30.00s"
    assert got["story_text"] == "hello"
    assert all(os.path.exists(path) for path in got["visual_paths"])
    assert os.path.exists(got["audio_path"])


def test_manifest_paths_are_relative_to_manifest_not_process_cwd(
        monkeypatch, tmp_path):
    (tmp_path / "left.mp4").write_bytes(b"left")
    (tmp_path / "right.mp4").write_bytes(b"right")
    (tmp_path / "source.txt").write_text("source context", encoding="utf-8")
    (tmp_path / "left.txt").write_text("left story", encoding="utf-8")
    seen = []

    def fake_prepare(video_path, _out, side, **kwargs):
        seen.append((video_path, side, kwargs["story_text"]))
        return {"video_path": video_path, "story_text": kwargs["story_text"]}

    monkeypatch.setattr(runner, "prepare_side", fake_prepare)
    got = runner.prepare_manifest({"cases": [{
        "id": "one", "source_context_path": "source.txt",
        "candidate_side": "left", "opponent_kind": "previous_build",
        "left": {"video_path": "left.mp4", "story_text_path": "left.txt",
                 "build_id": "candidate", "metrics": {"wall_time_s": 20}},
        "right": {"video_path": "right.mp4", "story_text": "right story",
                  "build_id": "baseline", "metrics": {"wall_time_s": 50}},
    }]}, str(tmp_path), str(tmp_path / "out"))

    assert got["cases"][0]["source_context"] == "source context"
    assert got["cases"][0]["candidate_side"] == "left"
    assert got["cases"][0]["opponent_kind"] == "previous_build"
    assert got["cases"][0]["left"]["metrics"]["wall_time_s"] == 20
    assert got["cases"][0]["right"]["build_id"] == "baseline"
    assert seen == [
        (str((tmp_path / "left.mp4").resolve()), "left", "left story"),
        (str((tmp_path / "right.mp4").resolve()), "right", "right story"),
    ]


def test_cli_evaluate_writes_results(monkeypatch, tmp_path):
    prepared = tmp_path / "prepared.json"
    results = tmp_path / "results.json"
    prepared.write_text(json.dumps({"cases": [{"id": "x"}]}),
                        encoding="utf-8")
    monkeypatch.setattr(
        runner.editorial_benchmark, "evaluate_pair",
        lambda case: {"case_id": case["id"], "channels": {}})
    monkeypatch.setattr(
        runner.editorial_benchmark, "summarize",
        lambda rows: {"cases": len(rows)})

    assert runner.main([
        "evaluate", str(prepared), "--results", str(results)]) == 0
    saved = json.loads(results.read_text(encoding="utf-8"))
    assert saved["results"] == [{"case_id": "x", "channels": {}}]
    assert saved["summary"] == {"cases": 1}
