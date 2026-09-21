import json
from pathlib import Path

import pytest

import run_state


def call(*args):
    assert run_state.main(list(args)) == 0


def write_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def advance_to_exporting(run_dir: Path):
    for stage in run_state.STAGES[1:-1]:
        call("phase", "--run-dir", str(run_dir), "--stage", stage)


def quality_fields(style_lane: str, *, editorial_duration_s: float = 23.0):
    value = {
        "editorial_duration_s": editorial_duration_s,
        "caption_treatment": {
            "spoken_word_highlighting": True,
            "active_word_color": "#FFD54A",
            "max_words_visible": 4,
            "rendered_active_word_check": "pass",
        },
        "broll_shots": [],
        "duplicate_broll_within_short": False,
    }
    if style_lane == "hook-to-silent-montage":
        value["montage_timing"] = {
            "start_s": 8.0, "end_s": 23.0, "duration_s": 15.0,
        }
        value["broll_shots"] = [
            {
                "asset_key": "subject-early", "source_start_s": 0.0,
                "source_end_s": 2.0, "output_start_s": 8.0,
                "output_end_s": 10.0,
            },
            {
                "asset_key": "subject-later", "source_start_s": 4.0,
                "source_end_s": 6.0, "output_start_s": 10.0,
                "output_end_s": 12.0,
            },
        ]
    return value


def test_happy_path_and_manifest(tmp_path):
    run_dir = tmp_path / "run"
    call("init", "--run-dir", str(run_dir), "--run-id", "r1",
         "--source", "https://example.test/podcast")
    assignment = run_dir / "assignments" / "short-01.json"
    write_json(assignment, {
        "short_id": "short-01", "style_lane": "hook-to-silent-montage",
    })
    call("add-short", "--run-dir", str(run_dir), "--short-id", "short-01",
         "--project-id", "123", "--title", "A real story",
         "--assignment", str(assignment))
    call("claim", "--run-dir", str(run_dir), "--short-id", "short-01",
         "--worker", "editor-1")
    preview = run_dir / "candidates" / "short-01" / "preview.mp4"
    preview.parent.mkdir(parents=True)
    preview.write_bytes(b"preview")
    bundle = preview.parent / "candidate.json"
    write_json(bundle, {
        "run_id": "r1", "short_id": "short-01", "child_project_id": 123,
        "style_lane": "fast-conversation",
        "edl_version": 7, "preview_path": str(preview.resolve()),
        "outstanding_job_ids": [],
        **quality_fields("fast-conversation"),
    })
    assert run_state.main([
        "candidate", "--run-dir", str(run_dir), "--short-id", "short-01",
        "--worker", "editor-1", "--bundle", str(bundle), "--preview",
        str(preview), "--edl-version", "7",
    ]) == 2
    write_json(bundle, {
        "run_id": "r1", "short_id": "short-01", "child_project_id": 123,
        "style_lane": "hook-to-silent-montage",
        "edl_version": 7, "preview_path": str(preview.resolve()),
        "outstanding_job_ids": [],
        **quality_fields("hook-to-silent-montage"),
    })
    call("candidate", "--run-dir", str(run_dir), "--short-id", "short-01",
         "--worker", "editor-1", "--bundle", str(bundle), "--preview",
         str(preview), "--edl-version", "7")
    report = preview.parent / "coordinator-qc.json"
    write_json(report, {
        "run_id": "r1", "short_id": "short-01", "child_project_id": 123,
        "style_lane": "hook-to-silent-montage",
        "edl_version": 7, "verdict": "ready", "score": 94,
        "active_word_caption_check": "pass",
        "within_short_broll_uniqueness_check": "pass",
        "style2_timing_check": "pass",
    })
    call("qc", "--run-dir", str(run_dir), "--short-id", "short-01",
         "--verdict", "ready", "--score", "94", "--report", str(report))
    wrong_name = run_dir / "exports" / "01.mp4"
    wrong_name.write_bytes(b"final")
    assert run_state.main([
        "export", "--run-dir", str(run_dir), "--short-id", "short-01",
        "--file", str(wrong_name), "--edl-version", "7",
        "--duration", "42.0",
    ]) == 2
    final = run_dir / "exports" / \
        "hook-to-silent-montage__01__project-123__edl-v7.mp4"
    final.write_bytes(b"final")
    call("export", "--run-dir", str(run_dir), "--short-id", "short-01",
         "--file", str(final), "--edl-version", "7", "--duration", "42.0")
    advance_to_exporting(run_dir)
    call("finalize", "--run-dir", str(run_dir))
    manifest = json.loads((run_dir / "exports" / "manifest.json").read_text())
    assert manifest["items"][0]["child_project_id"] == 123
    assert manifest["items"][0]["style_lane"] == "hook-to-silent-montage"
    assert manifest["items"][0]["file"] == str(final.resolve())
    assert manifest["exceptions"] == []


def test_fourth_parallel_editor_is_rejected(tmp_path):
    run_dir = tmp_path / "run"
    call("init", "--run-dir", str(run_dir), "--run-id", "r2",
         "--source", "topic")
    for index in range(4):
        short_id = f"short-{index}"
        assignment = run_dir / "assignments" / f"{short_id}.json"
        write_json(assignment, {
            "short_id": short_id, "style_lane": "fast-conversation",
        })
        call("add-short", "--run-dir", str(run_dir), "--short-id", short_id,
             "--project-id", str(200 + index), "--title", short_id,
             "--assignment", str(assignment))
    for index in range(3):
        call("claim", "--run-dir", str(run_dir), "--short-id",
             f"short-{index}", "--worker", f"editor-{index}")
    assert run_state.main([
        "claim", "--run-dir", str(run_dir), "--short-id", "short-3",
        "--worker", "editor-3",
    ]) == 2


def test_ready_requires_high_score(tmp_path):
    run_dir = tmp_path / "run"
    call("init", "--run-dir", str(run_dir), "--run-id", "r3",
         "--source", "topic")
    assignment = run_dir / "assignments" / "s.json"
    write_json(assignment, {
        "short_id": "s", "style_lane": "headline-conversation",
    })
    call("add-short", "--run-dir", str(run_dir), "--short-id", "s",
         "--project-id", "1", "--title", "s", "--assignment", str(assignment))
    call("claim", "--run-dir", str(run_dir), "--short-id", "s",
         "--worker", "e")
    preview = run_dir / "candidates" / "s" / "p.mp4"
    preview.parent.mkdir(parents=True)
    preview.write_bytes(b"x")
    bundle = preview.parent / "candidate.json"
    write_json(bundle, {
        "run_id": "r3", "short_id": "s", "child_project_id": 1,
        "style_lane": "headline-conversation",
        "edl_version": 2, "preview_path": str(preview.resolve()),
        "outstanding_job_ids": [],
        **quality_fields("headline-conversation"),
    })
    call("candidate", "--run-dir", str(run_dir), "--short-id", "s",
         "--worker", "e", "--bundle", str(bundle), "--preview", str(preview),
         "--edl-version", "2")
    report = preview.parent / "qc.json"
    write_json(report, {
        "run_id": "r3", "short_id": "s", "child_project_id": 1,
        "style_lane": "headline-conversation",
        "edl_version": 2, "verdict": "ready", "score": 89,
    })
    assert run_state.main([
        "qc", "--run-dir", str(run_dir), "--short-id", "s",
        "--verdict", "ready", "--score", "89", "--report", str(report),
    ]) == 2
    write_json(report, {
        "run_id": "r3", "short_id": "s", "child_project_id": 1,
        "style_lane": "headline-conversation",
        "edl_version": 2, "verdict": "ready", "score": 94,
    })
    assert run_state.main([
        "qc", "--run-dir", str(run_dir), "--short-id", "s",
        "--verdict", "ready", "--score", "94", "--report", str(report),
    ]) == 2
    report_payload = json.loads(report.read_text())
    report_payload.update({
        "active_word_caption_check": "pass",
        "within_short_broll_uniqueness_check": "pass",
    })
    write_json(report, report_payload)
    call("qc", "--run-dir", str(run_dir), "--short-id", "s",
         "--verdict", "ready", "--score", "94", "--report", str(report))


def test_add_short_rejects_assignment_without_style_lane(tmp_path):
    run_dir = tmp_path / "run"
    call("init", "--run-dir", str(run_dir), "--run-id", "r5",
         "--source", "topic")
    assignment = run_dir / "assignments" / "s.json"
    write_json(assignment, {"short_id": "s"})
    assert run_state.main([
        "add-short", "--run-dir", str(run_dir), "--short-id", "s",
        "--project-id", "1", "--title", "s", "--assignment",
        str(assignment),
    ]) == 2
    assert json.loads((run_dir / "run.json").read_text())["shorts"] == {}


def test_style_reassignment_allowed_only_while_queued(tmp_path):
    run_dir = tmp_path / "run"
    call("init", "--run-dir", str(run_dir), "--run-id", "r6",
         "--source", "topic")
    assignment = run_dir / "assignments" / "s.json"
    write_json(assignment, {
        "short_id": "s", "style_lane": "headline-conversation",
    })
    args = ["add-short", "--run-dir", str(run_dir), "--short-id", "s",
            "--project-id", "1", "--title", "s", "--assignment",
            str(assignment)]
    assert run_state.main(args) == 0
    write_json(assignment, {
        "short_id": "s", "style_lane": "fast-conversation",
    })
    assert run_state.main(args) == 0
    call("claim", "--run-dir", str(run_dir), "--short-id", "s",
         "--worker", "e")
    write_json(assignment, {
        "short_id": "s", "style_lane": "hook-to-silent-montage",
    })
    assert run_state.main(args) == 2
    state = json.loads((run_dir / "run.json").read_text())
    assert state["shorts"]["s"]["style_lane"] == "fast-conversation"


def test_taste_profile_is_checksummed_and_not_silently_replaced(tmp_path):
    run_dir = tmp_path / "run"
    call("init", "--run-dir", str(run_dir), "--run-id", "r4",
         "--source", "topic")
    profile = run_dir / "taste" / "taste-profile.json"
    write_json(profile, {
        "version": "taste-profile-v1", "approval": "user",
        "style_lanes": [{"id": "a"}, {"id": "b"}],
    })
    call("taste", "--run-dir", str(run_dir), "--profile", str(profile))
    first = json.loads((run_dir / "run.json").read_text())["taste_profile"]
    write_json(profile, {
        "version": "taste-profile-v1", "approval": "autopilot",
        "style_lanes": [{"id": "a"}, {"id": "b"}],
    })
    assert run_state.main([
        "taste", "--run-dir", str(run_dir), "--profile", str(profile),
    ]) == 2
    assert json.loads((run_dir / "run.json").read_text())[
        "taste_profile"] == first


def test_style2_candidate_timing_limits_are_enforced():
    payload = quality_fields("hook-to-silent-montage")
    run_state.validate_candidate_contract(payload, "hook-to-silent-montage")

    too_long = quality_fields(
        "hook-to-silent-montage", editorial_duration_s=25.1)
    too_long["montage_timing"] = {
        "start_s": 10.0, "end_s": 25.1, "duration_s": 15.1,
    }
    with pytest.raises(run_state.StateError, match="editorial duration"):
        run_state.validate_candidate_contract(
            too_long, "hook-to-silent-montage")

    montage_too_long = quality_fields("hook-to-silent-montage")
    montage_too_long["montage_timing"] = {
        "start_s": 7.9, "end_s": 23.0, "duration_s": 15.1,
    }
    with pytest.raises(run_state.StateError, match="exceeds 15 seconds"):
        run_state.validate_candidate_contract(
            montage_too_long, "hook-to-silent-montage")


def test_candidate_requires_rendered_active_word_caption_coloring():
    payload = quality_fields("headline-conversation")
    payload["caption_treatment"]["max_words_visible"] = 5
    with pytest.raises(run_state.StateError, match="at most four words"):
        run_state.validate_candidate_contract(payload, "headline-conversation")

    payload = quality_fields("headline-conversation")
    payload["caption_treatment"]["rendered_active_word_check"] = "fail"
    with pytest.raises(run_state.StateError, match="rendered active-word"):
        run_state.validate_candidate_contract(payload, "headline-conversation")


def test_candidate_rejects_repeated_broll_source_window():
    payload = quality_fields("fast-conversation")
    payload["broll_shots"] = [
        {
            "asset_key": "launch", "source_start_s": 3.0,
            "source_end_s": 5.0, "output_start_s": 2.0,
            "output_end_s": 4.0,
        },
        {
            "asset_key": "launch", "source_start_s": 4.5,
            "source_end_s": 6.0, "output_start_s": 9.0,
            "output_end_s": 10.5,
        },
    ]
    with pytest.raises(run_state.StateError, match="repeats B-roll"):
        run_state.validate_candidate_contract(payload, "fast-conversation")
