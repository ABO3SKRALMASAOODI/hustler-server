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


@pytest.fixture
def ready_final(tmp_path):
    run_dir = tmp_path / "run"
    call("init", "--run-dir", str(run_dir), "--run-id", "reject-test",
         "--source", "topic")
    assignment = run_dir / "assignments" / "s.json"
    write_json(assignment, {"short_id": "s", "style_lane": "fast-conversation"})
    call("add-short", "--run-dir", str(run_dir), "--short-id", "s",
         "--project-id", "123", "--title", "s", "--assignment", str(assignment))
    call("claim", "--run-dir", str(run_dir), "--short-id", "s", "--worker", "e")
    preview = run_dir / "candidates" / "preview.mp4"
    preview.write_bytes(b"preview")
    bundle = preview.with_suffix(".json")
    identity = {"run_id": "reject-test", "short_id": "s", "child_project_id": 123,
                "edl_version": 3, "style_lane": "fast-conversation"}
    write_json(bundle, {**identity, **quality_fields("fast-conversation"),
                        "preview_path": str(preview), "outstanding_job_ids": []})
    call("candidate", "--run-dir", str(run_dir), "--short-id", "s",
         "--worker", "e", "--bundle", str(bundle), "--preview", str(preview),
         "--edl-version", "3")
    qc = preview.parent / "qc.json"
    write_json(qc, {**identity, "score": 94, "verdict": "ready",
                    "active_word_caption_check": "pass",
                    "within_short_broll_uniqueness_check": "pass"})
    call("qc", "--run-dir", str(run_dir), "--short-id", "s", "--score", "94",
         "--verdict", "ready", "--report", str(qc))
    final = run_dir / "exports" / "fast-conversation__s.mp4"
    final.write_bytes(b"actual final with bad tail frames")
    receipt = final.with_suffix(".json")
    write_json(receipt, {"structuredContent": {"download_receipt": {
        "render_type": "final_export", "edl_version": 3,
        "sha256": run_state.sha256(final), "render_job_id": 456}}})
    report = preview.parent / "reject-final.json"
    write_json(report, {**identity, "verdict": "repair",
                        "final_sha256": run_state.sha256(final),
                        "final_receipt": str(receipt),
                        "observations": "Two raw-source frames before branding."})
    args = ["reject-final", "--run-dir", str(run_dir), "--short-id", "s",
            "--file", str(final), "--edl-version", "3", "--report", str(report)]
    return run_dir, final, receipt, report, args


@pytest.mark.parametrize("exporting", [False, True])
def test_reject_final_preserves_evidence_then_allows_claim(ready_final, exporting):
    run_dir, final, receipt, report, args = ready_final
    if exporting:
        call("export-start", "--run-dir", str(run_dir), "--short-id", "s",
             "--job-id", "456")
        call("job", "--run-dir", str(run_dir), "--short-id", "s",
             "--job-id", "456", "--kind", "final", "--state", "done")
    before = json.loads((run_dir / "run.json").read_text())["shorts"]["s"]
    call(*args)
    state = json.loads((run_dir / "run.json").read_text())
    item = state["shorts"]["s"]
    assert item["status"] == "repair" and item["repair_rounds"] == 1
    assert item["qc"] is None and item["export"] is None
    history = item["rejected_finals"][0]
    assert history["qc"] == before["qc"]
    assert history["qc_report"]["payload"] == json.loads(
        Path(before["qc"]["report"]).read_text())
    assert history["export"] == before["export"]
    assert history["candidate"] == before["candidate"]
    assert history["sha256"] == run_state.sha256(final)
    assert history["final_receipt"]["payload"] == json.loads(receipt.read_text())
    assert history["report"]["payload"] == json.loads(report.read_text())
    assert state["events"][-1]["type"] == "final_rejected"
    call("claim", "--run-dir", str(run_dir), "--short-id", "s", "--worker", "e2")
    after = json.loads((run_dir / "run.json").read_text())["shorts"]["s"]
    assert after["status"] == "editing" and after["edit_round"] == 2
    assert after["rejected_finals"][0] == history


@pytest.mark.parametrize("key,value", [
    ("run_id", "other"), ("short_id", "other"), ("child_project_id", 124),
    ("style_lane", "headline-conversation"), ("edl_version", 2),
    ("verdict", "ready"), ("final_sha256", "0" * 64),
])
def test_reject_final_refuses_unbound_report_without_mutation(ready_final, key, value):
    run_dir, _, _, report, args = ready_final
    payload = json.loads(report.read_text())
    payload[key] = value
    write_json(report, payload)
    before = (run_dir / "run.json").read_bytes()
    assert run_state.main(args) == 2
    assert (run_dir / "run.json").read_bytes() == before


@pytest.mark.parametrize("key", ["project_id", "child_project_id"])
def test_reject_final_binds_receipt_project_when_present(ready_final, key):
    run_dir, _, receipt, _, args = ready_final
    payload = json.loads(receipt.read_text())
    payload["structuredContent"]["download_receipt"][key] = 999
    write_json(receipt, payload)
    before = (run_dir / "run.json").read_bytes()
    assert run_state.main(args) == 2
    assert (run_dir / "run.json").read_bytes() == before


@pytest.mark.parametrize("problem", [
    "stale_candidate", "changed_final", "wrong_receipt", "preview_receipt",
    "queued", "running", "untracked_export", "repair_limit", "already_exported",
])
def test_reject_final_refuses_stale_or_active_work(ready_final, problem):
    run_dir, final, receipt, _, args = ready_final
    if problem == "changed_final":
        final.write_bytes(b"different actual media")
    elif problem in {"wrong_receipt", "preview_receipt"}:
        payload = json.loads(receipt.read_text())
        record = payload["structuredContent"]["download_receipt"]
        record.update({"sha256": "0" * 64} if problem == "wrong_receipt"
                      else {"render_type": "preview"})
        write_json(receipt, payload)
    else:
        with run_state.locked(str(run_dir)) as state:
            item = state["shorts"]["s"]
            if problem == "stale_candidate":
                item["candidate"]["edl_version"] = 4
            elif problem in {"queued", "running"}:
                item["jobs"].append({"job_id": 789, "kind": "preview", "state": problem})
            elif problem == "untracked_export":
                item.update(status="exporting", export={"job_id": 456, "state": "running"})
            elif problem == "repair_limit":
                item["repair_rounds"] = 2
            elif problem == "already_exported":
                item["status"] = "exported"
    before = (run_dir / "run.json").read_bytes()
    assert run_state.main(args) == 2
    assert (run_dir / "run.json").read_bytes() == before


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


@pytest.fixture
def delivery_evidence(tmp_path, monkeypatch):
    source = tmp_path / "original.mp4"
    source.write_bytes(b"original source fixture")
    acquisition = tmp_path / "acquisition.json"
    write_json(acquisition, {"selected_format": "137", "width": 1920, "height": 1012})
    policy = {
        "version": "delivery-quality-v1",
        "min_final_dimensions": [720, 1280],
        "target_final_dimensions": [1080, 1920],
        "min_native_short_edge": 720,
        "max_picture_upscale": 1.1,
    }
    evidence = {
        "source_path": str(source), "source_sha256": run_state.sha256(source),
        "acquisition_record": str(acquisition), "native_dimensions": [1920, 1012],
        "expected_final_dimensions": [1012, 1800],
        "picture_regions": [{"source_crop_native": [0, 0, 1100, 824],
                             "output_rect": [0, 519, 1012, 760]}],
    }
    probes = {str(source): [1920, 1012]}
    monkeypatch.setattr(run_state, "video_dimensions", lambda p: probes[str(p)])
    return policy, evidence, probes


def test_native_1012_final_passes_without_forcing_1080(tmp_path, delivery_evidence):
    policy, evidence, probes = delivery_evidence
    final = tmp_path / "final.mp4"
    final.write_bytes(b"native final")
    probes[str(final)] = [1012, 1800]
    result = run_state.measure_quality(policy, evidence, final)
    assert result["verdict"] == "pass"
    assert result["measured_dimensions"] == [1012, 1800]
    assert result["final_sha256"] == run_state.sha256(final)


def test_small_final_fails_despite_hd_source(tmp_path, delivery_evidence):
    policy, evidence, probes = delivery_evidence
    final = tmp_path / "bad-final.mp4"
    final.write_bytes(b"small canvas")
    probes[str(final)] = [338, 600]
    result = run_state.measure_quality(policy, evidence, final)
    assert "final_below_minimum" in result["violations"]
    assert "final_differs_from_expected" in result["violations"]


def test_upscaled_source_cannot_pass_native_detail_gate(delivery_evidence):
    policy, evidence, probes = delivery_evidence
    # Encoded dimensions look HD, but the acquisition/crop is only 640x338.
    evidence["native_dimensions"] = [640, 338]
    evidence["picture_regions"][0]["source_crop_native"] = [0, 0, 380, 285]
    result = run_state.measure_quality(policy, evidence)
    assert "native_source_below_minimum" in result["violations"]
    assert "expected_canvas_exceeds_native_source" in result["violations"]
    assert "picture_region_0_exceeds_native_detail" in result["violations"]


def test_oversized_crop_and_changed_source_are_rejected(delivery_evidence):
    policy, evidence, probes = delivery_evidence
    evidence["picture_regions"][0]["source_crop_native"] = [1500, 0, 1100, 824]
    with pytest.raises(run_state.StateError, match="outside its native canvas"):
        run_state.measure_quality(policy, evidence)
    evidence["source_sha256"] = "wrong"
    with pytest.raises(run_state.StateError, match="checksum"):
        run_state.measure_quality(policy, evidence)


def test_quality_policy_gates_batch_and_actual_export(tmp_path, delivery_evidence):
    policy, evidence, probes = delivery_evidence
    run_dir = tmp_path / "quality-run"
    policy_file = tmp_path / "policy.json"
    write_json(policy_file, policy)
    call("init", "--run-dir", str(run_dir), "--run-id", "quality",
         "--source", "topic", "--quality-policy", str(policy_file))
    for index in (1, 2):
        assignment = run_dir / "assignments" / f"s{index}.json"
        write_json(assignment, {"style_lane": "headline-conversation"})
        call("add-short", "--run-dir", str(run_dir), "--short-id", f"s{index}",
             "--project-id", str(index), "--title", "Story", "--assignment", str(assignment))
    call("claim", "--run-dir", str(run_dir), "--short-id", "s1", "--worker", "e1")
    claim_second = ["claim", "--run-dir", str(run_dir), "--short-id", "s2", "--worker", "e2"]
    assert run_state.main(claim_second) == 2
    preview = run_dir / "candidates" / "preview.mp4"
    preview.write_bytes(b"small draft is allowed")
    evidence_file = run_dir / "candidates" / "quality.json"
    write_json(evidence_file, evidence)
    bundle = run_dir / "candidates" / "candidate.json"
    payload = {
        "run_id": "quality", "short_id": "s1", "child_project_id": 1,
        "style_lane": "headline-conversation", "edl_version": 7,
        "preview_path": str(preview), "outstanding_job_ids": [],
        "quality_evidence": str(evidence_file), **quality_fields("headline-conversation"),
    }
    candidate_args = ["candidate", "--run-dir", str(run_dir), "--short-id", "s1",
                      "--worker", "e1", "--bundle", str(bundle), "--preview", str(preview),
                      "--edl-version", "7"]
    write_json(bundle, payload)
    assert run_state.main(candidate_args) == 2  # Missing stable-size evidence.
    payload["caption_treatment"].update(animation="none", emphasis="none",
                                         rendered_stable_size_check="pass")
    write_json(bundle, payload)
    call(*candidate_args)
    review_file = run_dir / "candidates" / "review.json"
    review = {
        "source_detail": "pass", "typography": "pass", "stable_word_size": "pass",
        "active_word_color": "pass", "reference_comparison": "pass",
        "observations": "Measured full-size comparison fixture.",
        "evidence_files": [str(preview)],
    }
    write_json(review_file, review)
    qc = run_dir / "candidates" / "qc.json"
    qc_payload = {
        "run_id": "quality", "short_id": "s1", "child_project_id": 1,
        "style_lane": "headline-conversation", "edl_version": 7,
        "verdict": "ready", "score": 100,
        "active_word_caption_check": "pass", "within_short_broll_uniqueness_check": "pass",
    }
    qc_args = ["qc", "--run-dir", str(run_dir), "--short-id", "s1",
               "--verdict", "ready", "--score", "100", "--report", str(qc)]
    write_json(qc, qc_payload)
    assert run_state.main(qc_args) == 2  # Perfect score cannot waive review.
    qc_payload["delivery_quality_review"] = str(review_file)
    write_json(qc, qc_payload)
    call(*qc_args)
    assert run_state.main(claim_second) == 2  # Preview approval is not a final pilot.
    final = run_dir / "exports" / "headline-conversation__s1.mp4"
    final.write_bytes(b"actual final")
    probes[str(final)] = [338, 600]
    export_args = ["export", "--run-dir", str(run_dir), "--short-id", "s1",
                   "--file", str(final), "--edl-version", "7", "--duration", "28",
                   "--quality-review", str(review_file)]
    assert run_state.main(export_args) == 2
    probes[str(final)] = [1012, 1800]
    assert run_state.main(export_args) == 2  # Review must bind actual final hash.
    review["final_sha256"] = run_state.sha256(final)
    write_json(review_file, review)
    call(*export_args)
    call(*claim_second)
    state = json.loads((run_dir / "run.json").read_text())
    assert state["quality_pilot"]["sha256"] == run_state.sha256(final)
    assert state["shorts"]["s1"]["export"]["quality"]["verdict"] == "pass"


def test_quality_policy_cannot_change_silently(tmp_path, delivery_evidence):
    policy, _, _ = delivery_evidence
    policy_file = tmp_path / "policy.json"
    write_json(policy_file, policy)
    record = {"quality_policy": {"file": str(policy_file),
                                "sha256": run_state.sha256(policy_file)}}
    policy["min_final_dimensions"] = [338, 600]
    write_json(policy_file, policy)
    with pytest.raises(run_state.StateError, match="changed after initialization"):
        run_state.run_quality_policy(record)
