#!/usr/bin/env python3
"""Focused state, recovery, fencing, and concurrency tests for run_registry.py."""

from __future__ import annotations

from decimal import Decimal
import copy
import hashlib
import importlib.util
import json
import multiprocessing
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from canonical_fingerprint import canonical_json, fingerprint as canonical_fingerprint


SCRIPT = Path(__file__).with_name("run_registry.py")
ASSIGNMENT_A = "sha256:" + "a" * 64
ASSIGNMENT_B = "sha256:" + "b" * 64


def _canonical(value):
    if value is None or isinstance(value, (bool, str, Decimal)):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return {key: _canonical(item) for key, item in value.items()}


def selection_payload(stories: list[dict]) -> dict:
    clips = []
    for index, story in enumerate(stories):
        start = 10.0 + index * 25.0
        clips.append({
            "rank": story["card"], "start": start, "end": start + 20.0,
            "title": story["title"], "hook": f"Hook {story['card']}", "score": 92,
            "story": {
                "setup": "The old approach failed.",
                "development": "A new constraint changed the design.",
                "payoff": "The result finally worked.",
            },
            "opening_line": f"Hook {story['card']}",
            "closing_line": "That is when it worked.",
            "selection_reason": "Complete, non-overlapping micro-story.",
        })
    payload = {
        "schema_version": "2", "parent_project_id": 100,
        "selected_by": "coordinator", "source_youtube_video_id": "abcdefghijk",
        "source_sha256": "b" * 64, "source_duration_s": 100.0,
        "source_visual_inspection": {
            "method": "indexed_filmstrip_and_shot_pages",
            "surface": "valmera_source_index",
            "media_sha256": "b" * 64,
            "duration_s": 100.0,
            "coverage": [[0.0, 100.0]],
            "gaps": [],
            "sampled_frame_times": [0.0, 25.0, 50.0, 75.0, 100.0],
            "configured_sample_step_s": 25.0,
            "max_sample_gap_s": 25.0,
            "shot_index_exhausted": True,
            "page_cursors_exhausted": True,
            "completed_at": "2026-08-18T09:40:00Z",
        },
        "source_speech_transcript": {
            "source": "valmera_indexed_transcript",
            "media_sha256": "b" * 64,
            "duration_s": 100.0,
            "coverage": [[0.0, 100.0]],
            "gaps": [],
            "text_sha256": "sha256:" + "7" * 64,
            "completed_at": "2026-08-18T09:41:00Z",
        },
        "clips": clips, "selection_fingerprint": "sha256:" + "0" * 64,
        "coordinator_approved": True, "abstained": not clips,
        "abstain_reason": "No qualifying story" if not clips else "",
    }
    payload["selection_fingerprint"] = canonical_fingerprint(
        "selection", _canonical(payload)
    )
    return payload


ONE_SELECTION = selection_payload(
    [{"story_id": "story-one", "card": 1, "title": "Card 1"}]
)
SELECTION = ONE_SELECTION["selection_fingerprint"]


_FIXTURES: dict[str, dict] = {}


def contract_fixture(name: str) -> dict:
    if name not in _FIXTURES:
        candidates = sorted(
            (Path.home() / ".pyenv" / "versions").glob("*/bin/python"), reverse=True
        )
        python = Path(sys.executable)
        for candidate in candidates:
            if candidate.is_file() and subprocess.run(
                [str(candidate), "-c", "import jsonschema"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            ).returncode == 0:
                python = candidate
                break
        fixture_path = Path(__file__).with_name("test_contract_schemas.py")
        code = (
            "import importlib.util,json,sys;"
            "p=sys.argv[1];n=sys.argv[2];"
            "s=importlib.util.spec_from_file_location('fixtures',p);"
            "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
            "print(json.dumps(getattr(m,n)(),ensure_ascii=False))"
        )
        result = subprocess.run(
            [str(python), "-c", code, str(fixture_path), name],
            text=True, capture_output=True, check=True,
        )
        _FIXTURES[name] = json.loads(result.stdout)
    return copy.deepcopy(_FIXTURES[name])


def selection_entries(selection: dict) -> list[dict]:
    return [
        {
            "card": clip["rank"],
            "title": clip["title"],
            "approved_start_s": clip["start"],
            "approved_end_s": clip["end"],
            "seeded_child_start_s": clip["start"],
            "seeded_child_end_s": clip["end"],
            "seed_snap_reason": "none",
            "seed_range_verified_by": "authoritative_child_edl",
            "seed_range_evidence_digest": "sha256:" + "e" * 64,
            "generation_job_id": f"job-{clip['rank']}",
            "generation_failure": None,
        }
        for clip in selection["clips"]
    ]


def acquisition_checkpoint(parent: int, selection: dict) -> dict:
    return {
        "parent_project_id": parent,
        "source_youtube_video_id": selection["source_youtube_video_id"],
        "source_asset_id": 400,
        "source_sha256": selection["source_sha256"],
    }


def object_digest(value) -> str:
    return "sha256:" + hashlib.sha256(
        canonical_json(_canonical(value)).encode("utf-8")
    ).hexdigest()


def rebind_reference_profile(profile: dict, asset_id: int) -> dict:
    profile["parent_reference_asset_id"] = asset_id
    profile["postupload_visual_inspection"]["parent_reference_asset_id"] = asset_id
    profile["postupload_speech_transcript"]["parent_reference_asset_id"] = asset_id
    return profile


def bind_acquisition_artifacts(
    acquisition: dict, selection: dict, reference: dict | None
) -> dict:
    source = acquisition["source"]
    source.update({
        "youtube_video_id": selection["source_youtube_video_id"],
        "canonical_url": "https://www.youtube.com/watch?v=" + selection["source_youtube_video_id"],
        "sha256": selection["source_sha256"],
        "duration_s": selection["source_duration_s"],
        "source_visual_inspection": copy.deepcopy(selection["source_visual_inspection"]),
        "source_speech_transcript": copy.deepcopy(selection["source_speech_transcript"]),
    })
    if reference is None:
        acquisition["reference"] = None
        return acquisition
    row = acquisition["reference"]
    previsual = reference["preselection_visual_inspection"]
    pretranscript = reference["preselection_speech_transcript"]
    signal = reference["signal_analysis"]
    postvisual = reference["postupload_visual_inspection"]
    row.update({
        "asset_id": reference["parent_reference_asset_id"],
        "youtube_video_id": reference["youtube_video_id"],
        "canonical_url": "https://www.youtube.com/watch?v=" + reference["youtube_video_id"],
        "sha256": reference["reference_sha256"],
        "duration_s": reference["reference_duration_s"],
        "reference_profile_version": reference["reference_profile_version"],
        "preselection_visual_evidence_id": previsual["evidence_id"],
        "preselection_visual_inspection_digest": object_digest(previsual),
        "preselection_media_sha256": previsual["media_sha256"],
        "preselection_duration_s": previsual["duration_s"],
        "preselection_speech_transcript_digest": object_digest(pretranscript),
        "preselection_speech_evidence_id": pretranscript["evidence_id"],
        "transcript_text_sha256": pretranscript["asr"]["transcript_text_sha256"],
        "audio_pcm_sha256": pretranscript["audio_pcm_sha256"],
        "music_identity_digest": object_digest(reference["music_identity"]),
        "selection_decision_digest": object_digest(reference["selection_decision"]),
        "signal_analysis_status": signal["status"],
        "signal_analysis_evidence_id": signal["evidence_id"],
        "signal_analysis_digest": object_digest(signal),
        "postupload_visual_evidence_id": postvisual["evidence_id"],
        "postupload_visual_inspection_digest": object_digest(postvisual),
        "postupload_media_sha256": postvisual["media_sha256"],
        "postupload_duration_s": postvisual["duration_s"],
        "postupload_speech_transcript": copy.deepcopy(reference["postupload_speech_transcript"]),
    })
    return acquisition


def _race_grant(registry: str, run: str, child: int, lease: str, assignment: str,
                fingerprint: str, selection_fingerprint: str, start, queue) -> None:
    start.wait()
    command = [
        sys.executable, str(SCRIPT), "--registry", registry, "grant-lease",
        "--run-id", run, "--child-project-id", str(child), "--lease-id", lease,
        "--purpose", "edit", "--repair-round", "0",
        "--selection-fingerprint", selection_fingerprint,
        "--assignment-id", assignment,
        "--assignment-fingerprint", fingerprint,
        "--op-id", f"op-{lease}",
    ]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    queue.put((result.returncode, json.loads(result.stdout)))


def _race_registry_command(registry: str, arguments: list[str], start, queue) -> None:
    start.wait()
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--registry", registry, *arguments],
        text=True,
        capture_output=True,
        check=False,
    )
    queue.put((result.returncode, json.loads(result.stdout)))


class RegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.registry = Path(self.temp.name) / "registry.sqlite3"
        self.identities: dict[str, tuple[str, str]] = {}
        self.artifacts: dict[str, dict[str, dict | None]] = {}
        self.assignment_artifacts: dict[tuple[str, int], dict] = {}
        self.counter = 0
        self.run_cli("init", expect=0)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cli(self, *args: str, expect: int | None = None):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--registry", str(self.registry), *args],
            text=True, capture_output=True, check=False,
        )
        if expect is not None:
            self.assertEqual(result.returncode, expect, result.stderr + result.stdout)
        return result, json.loads(result.stdout)

    def create_run(self, run: str = "run-a") -> None:
        suffix = run[-1]
        thread, host = f"coord-thread-{suffix}", f"host-{suffix}"
        self.identities[run] = (thread, host)
        self.run_cli(
            "create-run", "--run-id", run, "--topic", f"topic for {run}",
            "--coordinator-thread-id", thread, "--coordinator-host-id", host,
            "--op-id", f"{run}:create", expect=0,
        )

    def run_phase(self, run: str, phase: str, data: dict,
                  checkpoint: str = "ready") -> dict:
        self.counter += 1
        thread, host = self.identities[run]
        lease_id = f"{run}:{phase}:{self.counter}"
        _, grant = self.run_cli(
            "grant-run-lease", "--run-id", run, "--lease-id", lease_id,
            "--phase", phase, "--op-id", f"{lease_id}:grant", expect=0,
        )
        generation = grant["lease_generation"]
        exact = [
            "--run-id", run, "--lease-id", lease_id,
            "--lease-generation", str(generation), "--phase", phase,
            "--thread-id", thread, "--host-id", host,
        ]
        self.run_cli("check-run-lease", *exact, expect=0)
        self.run_cli(
            "record-run-snapshot", *exact, "--checkpoint-status", checkpoint,
            "--data-json", json.dumps(data), "--op-id", f"{lease_id}:snapshot",
            expect=0,
        )
        self.run_cli(
            "close-run-lease", "--run-id", run, "--lease-id", lease_id,
            "--lease-generation", str(generation), "--phase", phase,
            "--coordinator-thread-id", thread, "--coordinator-host-id", host,
            "--action", "release", "--checkpoint-status", checkpoint,
            "--outstanding-job-ids-json", "[]", "--reason", "phase safe",
            "--op-id", f"{lease_id}:close", expect=0,
        )
        return grant

    def create_bound_run(self, run: str = "run-a", parent: int = 100,
                         selection: dict | None = None,
                         materialization: list[dict] | None = None) -> None:
        selection = copy.deepcopy(selection or ONE_SELECTION)
        selection["parent_project_id"] = parent
        selection["selection_fingerprint"] = canonical_fingerprint(
            "selection", _canonical(selection)
        )
        self.create_run(run)
        self.run_phase(run, "acquisition", acquisition_checkpoint(parent, selection))
        self.run_phase(run, "selection", selection)
        self.run_cli(
            "bind-run", "--run-id", run, "--parent-project-id", str(parent),
            "--selection-fingerprint", selection["selection_fingerprint"],
            "--op-id", f"{run}:bind", expect=0,
        )
        reference = None
        acquisition = contract_fixture("acquisition_record")
        acquisition.update({
            "run_id": run, "topic": f"topic for {run}",
            "parent_project_id": parent,
            "selected_clip_count": len(selection["clips"]),
        })
        if selection["clips"]:
            reference = contract_fixture("reference_profile")
            rebind_reference_profile(reference, parent + 500)
            self.run_phase(run, "reference", reference)
            if materialization is None:
                materialization = [
                    {**selection_entries(selection)[0], "status": "materialized",
                     "child_project_id": parent + 1}
                ]
            bind_acquisition_artifacts(acquisition, selection, reference)
            self.run_phase(run, "acquisition_record", acquisition)
            self.run_phase(
                run, "materialization",
                {"selection_fingerprint": selection["selection_fingerprint"],
                 "stories": materialization},
            )
        else:
            bind_acquisition_artifacts(acquisition, selection, None)
            acquisition.update({
                "status": "abstained",
                "abstained": True,
                "abstain_reason": selection["abstain_reason"],
                "selected_clip_count": 0,
                "reference": None,
            })
            self.run_phase(run, "acquisition_record", acquisition)
        self.artifacts[run] = {
            "selection": selection,
            "reference": reference,
            "acquisition": acquisition,
        }

    def register_child(self, run: str = "run-a", child: int = 101, card: int = 1,
                       thread: str = "child-thread-a", host: str = "host-a",
                       assignment: str = "assign-a",
                       fingerprint: str = ASSIGNMENT_A,
                       title: str | None = None,
                       assignment_status: str = "ready_for_editor",
                       task_state: str = "consumed") -> dict:
        snapshot = self.run_cli("resume", "--run-id", run, expect=0)[1]
        selection = self.artifacts[run]["selection"]
        acquisition = self.artifacts[run]["acquisition"]
        reference = self.artifacts[run]["reference"]
        materialization = next(
            row["data"] for row in snapshot["run_snapshots"]
            if row["phase"] == "materialization" and row["checkpoint_status"] == "ready"
        )
        materialized = next(row for row in materialization["stories"] if row["card"] == card)
        clip = selection["clips"][card - 1]
        artifact = contract_fixture("referenced_assignment")
        artifact.update({
            "assignment_id": assignment,
            "assignment_status": assignment_status,
            "run_id": run,
            "parent_project_id": snapshot["run"]["parent_project_id"],
            "child_project_id": child,
            "card": card,
            "title": title or clip["title"],
            "hook": clip["hook"],
            "story": copy.deepcopy(clip["story"]),
            "opening_line": clip["opening_line"],
            "closing_line": clip["closing_line"],
            "selection_reason": clip["selection_reason"],
        })
        artifact["source"].update({
            "youtube_video_id": acquisition["source"]["youtube_video_id"],
            "asset_id": acquisition["source"]["asset_id"],
            "source_duration_s": acquisition["source"]["duration_s"],
            "approved_start_s": materialized["approved_start_s"],
            "approved_end_s": materialized["approved_end_s"],
            "seeded_child_start_s": materialized["seeded_child_start_s"],
            "seeded_child_end_s": materialized["seeded_child_end_s"],
            "seed_snap_reason": materialized["seed_snap_reason"],
            "seed_range_verified_by": materialized["seed_range_verified_by"],
            "seed_range_evidence_digest": materialized["seed_range_evidence_digest"],
        })
        artifact["reference_profile_version"] = reference["reference_profile_version"]
        artifact["reference_transfer"].update({
            "parent_reference_asset_id": reference["parent_reference_asset_id"],
            "reference_sha256": reference["reference_sha256"],
            "reference_youtube_video_id": reference["youtube_video_id"],
        })
        if assignment_status == "requires_pre_mutation_recast":
            ambiguous = contract_fixture("ambiguous_assignment")
            artifact.update({
                "pre_mutation_recast_status": "pending_parent_approval",
                "candidate_slate": ambiguous["candidate_slate"],
                "blocked_reason": None,
                "blocked_evidence_ids": [],
                "story_profile": ambiguous["story_profile"],
            })
        elif assignment_status == "blocked_before_mutation":
            ambiguous = contract_fixture("ambiguous_assignment")
            artifact.update({
                "pre_mutation_recast_status": "blocked",
                "candidate_slate": ambiguous["candidate_slate"],
                "blocked_reason": "Evidence cannot support a truthful cast.",
                "blocked_evidence_ids": ["transcript:10.0-20.0"],
                "story_profile": ambiguous["story_profile"],
            })
        artifact["assignment_input_fingerprint"] = canonical_fingerprint(
            "assignment", _canonical(artifact)
        )
        self.assignment_artifacts[(run, child)] = artifact
        if task_state == "unprepared":
            return artifact
        client_request_id = f"{run}:task:{child}"
        coordinator_thread, coordinator_host = self.identities[run]
        transition = [
            "--client-request-id", client_request_id,
            "--run-id", run,
            "--child-project-id", str(child),
            "--card", str(card),
            "--assignment-id", artifact["assignment_id"],
            "--assignment-fingerprint", artifact["assignment_input_fingerprint"],
            "--coordinator-thread-id", coordinator_thread,
            "--coordinator-host-id", coordinator_host,
        ]
        self.run_cli(
            "prepare-child-task-intent",
            "--client-request-id", client_request_id,
            "--run-id", run,
            "--selection-fingerprint", snapshot["run"]["selection_fingerprint"],
            "--child-project-id", str(child),
            "--card", str(card),
            "--title", artifact["title"],
            "--assignment-id", artifact["assignment_id"],
            "--assignment-fingerprint", artifact["assignment_input_fingerprint"],
            "--assignment-json", json.dumps(artifact),
            "--coordinator-thread-id", coordinator_thread,
            "--coordinator-host-id", coordinator_host,
            "--op-id", f"{run}:task-intent:{child}",
            expect=0,
        )
        if task_state == "prepared":
            return artifact
        self.run_cli(
            "begin-child-task-create", *transition,
            "--op-id", f"{run}:task-create:{child}", expect=0,
        )
        if task_state == "dispatching":
            return artifact
        client_thread_id = f"queued-{run}-{child}"
        self.run_cli(
            "record-child-task-queued", *transition,
            "--client-thread-id", client_thread_id,
            "--op-id", f"{run}:task-queued:{child}", expect=0,
        )
        if task_state == "queued":
            return artifact
        self.run_cli(
            "resolve-child-task-intent", *transition,
            "--client-thread-id", client_thread_id,
            "--thread-id", thread, "--host-id", host,
            "--op-id", f"{run}:task-resolve:{child}", expect=0,
        )
        if task_state == "resolved":
            return artifact
        self.run_cli(
            "register-child", "--client-request-id", client_request_id,
            "--run-id", run,
            "--selection-fingerprint", snapshot["run"]["selection_fingerprint"],
            "--child-project-id", str(child), "--card", str(card),
            "--title", artifact["title"], "--thread-id", thread,
            "--host-id", host, "--assignment-id", artifact["assignment_id"],
            "--assignment-fingerprint", artifact["assignment_input_fingerprint"],
            "--assignment-json", json.dumps(artifact),
            "--op-id", f"{run}:register:{child}", expect=0,
        )
        return artifact

    def assignment_identity(self, run: str, child: int,
                            assignment: str, fingerprint: str) -> tuple[str, str]:
        artifact = self.assignment_artifacts.get((run, child))
        if artifact is None:
            return assignment, fingerprint
        return artifact["assignment_id"], artifact["assignment_input_fingerprint"]

    def recast_input(self, run: str = "run-a", child: int = 101) -> dict:
        assignment = self.assignment_artifacts[(run, child)]
        recast = contract_fixture("recast_result")
        recast.update({
            "assignment_schema_version": assignment["assignment_schema_version"],
            "taxonomy_version": assignment["taxonomy_version"],
            "prompt_version": assignment["prompt_version"],
            "treatment_contract_version": assignment["treatment_contract_version"],
            "reference_profile_version": assignment["reference_profile_version"],
            "assignment_id": assignment["assignment_id"],
            "assignment_input_fingerprint": assignment["assignment_input_fingerprint"],
            "run_id": run,
            "parent_project_id": assignment["parent_project_id"],
            "child_project_id": child,
        })
        recast["recast_input_fingerprint"] = canonical_fingerprint(
            "recast", _canonical(recast)
        )
        return recast

    def approved_recast(self, run: str = "run-a", child: int = 101) -> dict:
        recast = self.recast_input(run, child)
        approved = contract_fixture("approved_recast")
        for key in (
            "status", "approved_by", "approved_candidate_id", "approved_cast",
            "approved_treatment_delta", "contradiction_summary",
        ):
            recast[key] = copy.deepcopy(approved[key])
        return recast

    def record_approved_recast(self, run: str = "run-a", child: int = 101,
                               expect: int = 0):
        recast = self.approved_recast(run, child)
        recast_input = self.recast_input(run, child)
        thread, host = self.identities[run]
        self.run_cli(
            "record-recast-input", "--run-id", run,
            "--child-project-id", str(child),
            "--coordinator-thread-id", thread,
            "--coordinator-host-id", host,
            "--recast-json", json.dumps(recast_input),
            "--op-id", f"{run}:recast-input:{child}", expect=0,
        )
        return self.run_cli(
            "record-recast", "--run-id", run,
            "--child-project-id", str(child),
            "--coordinator-thread-id", thread,
            "--coordinator-host-id", host,
            "--approval", "approved",
            "--approved-candidate-id", recast["approved_candidate_id"],
            "--approval-reason", "Coordinator approved the evidence-backed cast.",
            "--recast-json", json.dumps(recast),
            "--op-id", f"{run}:recast:{child}", expect=expect,
        )[1]

    def grant(self, lease: str = "lease-edit-a", purpose: str = "edit",
              repair_round: int = 0, run: str = "run-a", child: int = 101,
              assignment: str = "assign-a", fingerprint: str = ASSIGNMENT_A,
              expect: int = 0):
        assignment, fingerprint = self.assignment_identity(
            run, child, assignment, fingerprint
        )
        selection = self.run_cli("resume", "--run-id", run, expect=0)[1]["run"]
        return self.run_cli(
            "grant-lease", "--run-id", run, "--child-project-id", str(child),
            "--lease-id", lease, "--purpose", purpose,
            "--repair-round", str(repair_round),
            "--selection-fingerprint", selection["selection_fingerprint"],
            "--assignment-id", assignment,
            "--assignment-fingerprint", fingerprint,
            "--op-id", f"op:{lease}", expect=expect,
        )[1]

    def auth_args(self, lease: str, generation: int, purpose: str = "edit",
                  repair_round: int = 0, run: str = "run-a", child: int = 101,
                  assignment: str = "assign-a", fingerprint: str = ASSIGNMENT_A,
                  thread: str = "child-thread-a", host: str = "host-a") -> list[str]:
        assignment, fingerprint = self.assignment_identity(
            run, child, assignment, fingerprint
        )
        selection = self.run_cli("resume", "--run-id", run, expect=0)[1]["run"]
        return [
            "--run-id", run, "--child-project-id", str(child),
            "--lease-id", lease, "--lease-generation", str(generation),
            "--purpose", purpose, "--repair-round", str(repair_round),
            "--selection-fingerprint", selection["selection_fingerprint"],
            "--assignment-id", assignment,
            "--assignment-fingerprint", fingerprint,
            "--thread-id", thread, "--host-id", host,
        ]

    def child_report(self, lease: str, generation: int, status: str,
                     purpose: str = "edit", repair_round: int = 0,
                     run: str = "run-a",
                     child: int = 101, assignment: str = "assign-a",
                     fingerprint: str = ASSIGNMENT_A) -> dict:
        assignment, fingerprint = self.assignment_identity(
            run, child, assignment, fingerprint
        )
        if status == "paused_safe":
            return {
                "checkpoint_schema_version": "valmera-safe-checkpoint-v1",
                "run_id": run, "child_project_id": child,
                "assignment_id": assignment,
                "assignment_input_fingerprint": fingerprint,
                "valmera_lease_id": lease, "status": status,
                "outstanding_job_ids": [], "reason": "safe pause",
            }
        assignment_artifact = self.assignment_artifacts[(run, child)]
        fixture_name = (
            "editor_result_after_approved_recast"
            if assignment_artifact["assignment_status"] == "requires_pre_mutation_recast"
            else "editor_result_with_reference"
        )
        report = contract_fixture(fixture_name)
        snapshot = self.run_cli("resume", "--run-id", run, expect=0)[1]
        child_row = next(row for row in snapshot["children"]
                         if row["child_project_id"] == child)
        report.update({
            "run_id": run, "parent_project_id": snapshot["run"]["parent_project_id"],
            "child_project_id": child, "card": child_row["card"],
            "assignment_id": assignment,
            "assignment_input_fingerprint": fingerprint,
            "valmera_lease_id": lease,
            "valmera_lease_generation": generation,
            "attempt_purpose": purpose,
            "repair_round": repair_round,
            "source_lineage": copy.deepcopy(assignment_artifact["source"]),
            "status": status,
        })
        transfer = assignment_artifact["reference_transfer"]
        treatment = assignment_artifact["treatment"]
        report["treatment"].update({
            "treatment_contract_version": assignment_artifact["treatment_contract_version"],
            "reference_profile_version": assignment_artifact["reference_profile_version"],
            "name": treatment["name"],
            "reference_status": transfer["status"],
            "reference_applicable": transfer["status"] in ("applicable", "partial"),
            "child_reference_asset_id": transfer["child_reference_asset_id"],
            "reference_evidence_status": (
                "complete_visual_transcript_metadata"
                if transfer["status"] in ("applicable", "partial", "inapplicable")
                else "not_supplied"
            ),
        })
        if assignment_artifact["assignment_status"] == "requires_pre_mutation_recast":
            recast = next(
                row for row in snapshot["children"]
                if row["child_project_id"] == child
            )["recast"]
            report["treatment"]["name"] = recast["approved_treatment_delta"]["name"]
        if status != "ready":
            report["issues"] = [{
                "gate": "captions", "reason_code": "other",
                "start_s": 0.0, "end_s": 1.0,
                "evidence": "Caption defect remains.",
                "required_change": "Repair the caption defect.",
            }]
        report["result_fingerprint"] = canonical_fingerprint(
            "editor-result", _canonical(report)
        )
        return report

    def qc_report(self, status: str, repair_round: int, child: int = 101,
                  assignment: str = "assign-a", fingerprint: str = ASSIGNMENT_A,
                  run: str = "run-a") -> dict:
        assignment, fingerprint = self.assignment_identity(
            run, child, assignment, fingerprint
        )
        report = contract_fixture("parent_qc")
        snapshot = self.run_cli("resume", "--run-id", run, expect=0)[1]
        child_row = next(row for row in snapshot["children"]
                         if row["child_project_id"] == child)
        report.update({
            "run_id": run,
            "child_project_id": child, "assignment_id": assignment,
            "assignment_input_fingerprint": fingerprint,
            "repair_round": repair_round, "status": status,
            "parent_project_id": snapshot["run"]["parent_project_id"],
            "card": child_row["card"],
            "editor_task_id": child_row["thread_id"],
        })
        editor = next(
            row["result"] for row in reversed(snapshot["attempt_results"])
            if row["actor"] == "child"
            and row["child_project_id"] == child
            and row["repair_round"] == repair_round
        )
        report["editor_claims"] = {
            "result_schema_version": editor["result_schema_version"],
            "result_fingerprint": editor["result_fingerprint"],
            "final_edl_version": editor["final_edl_version"],
            "preview_edl_version": editor["preview_edl_version"],
            "preview_render_provenance": copy.deepcopy(editor["preview_render_provenance"]),
            "audio": copy.deepcopy(editor["audio"]),
            "cta": copy.deepcopy(editor["cta"]),
            "render_visual_inspection": copy.deepcopy(editor["render_visual_inspection"]),
            "program_speech_transcript": copy.deepcopy(editor["program_speech_transcript"]),
            "visual_provenance_records": copy.deepcopy(
                editor["visuals"]["visual_provenance_records"]
            ),
        }
        parent_inference = copy.deepcopy(report["music"]["music_fit_inference"])
        report["music"] = copy.deepcopy(editor["audio"])
        report["music"]["music_fit_inference"] = parent_inference
        report["cta"] = copy.deepcopy(editor["cta"])
        report["source_lineage"] = copy.deepcopy(editor["source_lineage"])
        report["live_edl_version"] = editor["final_edl_version"]
        report["preview_edl_version"] = editor["preview_edl_version"]
        report["story"]["final_spoken_line"] = editor["story"]["final_spoken_line"]
        report["treatment"].update({
            "treatment_contract_version": editor["treatment"]["treatment_contract_version"],
            "reference_profile_version": editor["treatment"]["reference_profile_version"],
            "name": editor["treatment"]["name"],
            "reference_status": editor["treatment"]["reference_status"],
            "reference_applicable": editor["treatment"]["reference_applicable"],
            "child_reference_asset_id": editor["treatment"]["child_reference_asset_id"],
            "reference_evidence_status": editor["treatment"]["reference_evidence_status"],
        })
        inference = report["music"]["music_fit_inference"]
        inference_payload = {
            "preview_render_provenance": report["preview_render_provenance"],
            "render_visual_inspection": report["render_visual_inspection"],
            "program_speech_transcript": report["program_speech_transcript"],
            "music_identity": report["music"]["music_identity"],
            "candidate_metadata_shortlist": report["music"]["candidate_metadata_shortlist"],
            "selected_track_analysis": report["music"]["selected_track_analysis"],
            "edl_facts": report["music"]["edl_facts"],
            "mix_measurements": report["music"]["mix_measurements"],
            "inference": {
                key: value for key, value in inference.items()
                if key != "independent_evidence_digest"
            },
        }
        inference["independent_evidence_digest"] = object_digest(inference_payload)
        if status != "pass":
            report["violations"] = [{
                "gate": "captions", "reason_code": "other",
                "start_s": 0.0, "end_s": 1.0,
                "evidence": "Caption defect remains.",
                "required_change": "Repair the caption defect.",
            }]
        report["qc_fingerprint"] = canonical_fingerprint(
            "parent-qc", _canonical(report)
        )
        return report

    def record_child(self, lease: str, generation: int, status: str,
                     purpose: str = "edit", repair_round: int = 0,
                     run: str = "run-a", child: int = 101,
                     assignment: str = "assign-a", fingerprint: str = ASSIGNMENT_A,
                     thread: str = "child-thread-a", host: str = "host-a") -> None:
        auth = self.auth_args(
            lease, generation, purpose, repair_round, run, child, assignment,
            fingerprint, thread, host,
        )
        self.run_cli(
            "record-child-result", *auth, "--status", status,
            "--result-json", json.dumps(
                self.child_report(
                    lease, generation, status, purpose, repair_round,
                    run, child, assignment, fingerprint,
                )
            ),
            "--exclusions-json", '{"asset_ids":[77]}',
            "--op-id", f"{lease}:child-result", expect=0,
        )

    def record_qc(self, lease: str, generation: int, status: str,
                  repair_round: int = 0, run: str = "run-a", child: int = 101,
                  assignment: str = "assign-a", fingerprint: str = ASSIGNMENT_A,
                  expect: int = 0):
        thread, host = self.identities[run]
        auth = self.auth_args(
            lease, generation, "qc", repair_round, run, child, assignment,
            fingerprint, thread, host,
        )
        return self.run_cli(
            "record-qc-result", *auth, "--status", status,
            "--result-json", json.dumps(
                self.qc_report(status, repair_round, child, assignment, fingerprint, run)
            ),
            "--exclusions-json", '{"asset_ids":[77]}',
            "--op-id", f"{lease}:qc-result:{status}", expect=expect,
        )[1]

    def close(self, lease: str, generation: int, checkpoint: str, action: str,
              op: str, run: str = "run-a", child: int = 101,
              outstanding: str = "[]", expect: int = 0):
        thread, host = self.identities[run]
        return self.run_cli(
            "close-lease", "--run-id", run, "--child-project-id", str(child),
            "--lease-id", lease, "--lease-generation", str(generation),
            "--coordinator-thread-id", thread, "--coordinator-host-id", host,
            "--action", action, "--checkpoint-status", checkpoint,
            "--outstanding-job-ids-json", outstanding,
            "--reason", "safe checkpoint verified", "--op-id", op,
            expect=expect,
        )[1]

    def coordinator_result(self, run: str = "run-a") -> dict:
        snapshot = self.run_cli("resume", "--run-id", run, expect=0)[1]
        run_row = snapshot["run"]
        selection = self.artifacts[run]["selection"]
        acquisition = self.artifacts[run]["acquisition"]
        reference = self.artifacts[run]["reference"]
        result = contract_fixture("coordinator_result")
        result.update({
            "run_id": run,
            "topic": run_row["topic"],
            "parent_project_id": run_row["parent_project_id"],
            "source_youtube_video_id": acquisition["source"]["youtube_video_id"],
            "source_asset_id": acquisition["source"]["asset_id"],
            "source_sha256": acquisition["source"]["sha256"].removeprefix("sha256:"),
            "selection_fingerprint": run_row["selection_fingerprint"],
        })
        if selection["abstained"]:
            result.update({
                "reference_youtube_video_id": None,
                "reference_asset_id": None,
                "reference_sha256": None,
                "status": "abstained",
                "abstained": True,
                "abstain_reason": selection["abstain_reason"],
                "blocked_phase": None,
                "blocked_reason": None,
                "blocked_evidence": [],
                "selected_arc_count": 0,
                "accounted_arc_count": 0,
                "generated_count": 0,
                "pending_count": 0,
                "failed_generation_count": 0,
                "ready_count": 0,
                "blocked_count": 0,
                "arc_accounting": [],
            })
            return result
        result.update({
            "reference_youtube_video_id": reference["youtube_video_id"],
            "reference_asset_id": reference["parent_reference_asset_id"],
            "reference_sha256": reference["reference_sha256"].removeprefix("sha256:"),
            "abstained": False,
            "abstain_reason": None,
        })
        materialization = next(
            row["data"] for row in snapshot["run_snapshots"]
            if row["phase"] == "materialization" and row["checkpoint_status"] == "ready"
        )
        children = {row["card"]: row for row in snapshot["children"]}
        accounting = []
        ready_count = blocked_count = failed_count = pending_count = 0
        for clip, materialized in zip(selection["clips"], materialization["stories"]):
            generation_status = (
                "generated" if materialized["status"] == "materialized"
                else materialized["status"]
            )
            row = {
                "arc_id": f"{run}:arc:{clip['rank']}",
                "selection_rank": clip["rank"],
                "start_s": clip["start"],
                "end_s": clip["end"],
                "title": clip["title"],
                "generation_status": generation_status,
                "assignment_input_fingerprint": None,
                "editor_result_fingerprint": None,
                "parent_qc_fingerprint": None,
                "child_project_id": materialized["child_project_id"],
                "generation_job_id": materialized.get("generation_job_id"),
                "generation_failure": materialized.get("generation_failure"),
                "editor_status": "failed" if generation_status == "failed" else "not_started",
                "parent_qc_status": "not_run",
                "live_edl_version": None,
                "preview_edl_version": None,
                "treatment_name": None,
                "reference_adaptation_summary": None,
                "failed_gates": ["generation"] if generation_status == "failed" else [],
            }
            if generation_status == "pending":
                pending_count += 1
            elif generation_status == "failed":
                failed_count += 1
            else:
                child = children.get(clip["rank"])
                if child is None:
                    raise AssertionError("generated coordinator row lacks registered child")
                assignment = child["assignment"]
                row["assignment_input_fingerprint"] = assignment[
                    "assignment_input_fingerprint"
                ]
                row["treatment_name"] = assignment["treatment"]["name"]
                row["reference_adaptation_summary"] = (
                    "Applied the exact frozen reference-transfer decision."
                )
                if child["qc_status"] == "pass":
                    ready_count += 1
                    row["editor_status"] = "ready"
                    row["parent_qc_status"] = "pass"
                elif child["qc_status"] == "blocked":
                    blocked_count += 1
                    row["editor_status"] = "blocked"
                    row["parent_qc_status"] = "blocked"
                    row["failed_gates"] = ["render"]
                else:
                    row["editor_status"] = "in_progress"
                    row["parent_qc_status"] = "pending"
                if isinstance(child.get("qc_result"), dict):
                    row["live_edl_version"] = child["qc_result"]["live_edl_version"]
                    row["preview_edl_version"] = child["qc_result"]["preview_edl_version"]
                    row["parent_qc_fingerprint"] = child["qc_result"]["qc_fingerprint"]
                child_results = [
                    attempt["result"]
                    for attempt in snapshot["attempt_results"]
                    if attempt["actor"] == "child"
                    and attempt["child_project_id"] == child["child_project_id"]
                    and isinstance(attempt.get("result"), dict)
                    and attempt["result"].get("result_schema_version")
                    == "valmera-child-editor-result-v2"
                ]
                if child_results:
                    row["editor_result_fingerprint"] = child_results[-1][
                        "result_fingerprint"
                    ]
            accounting.append(row)
        generated_count = sum(
            row["generation_status"] == "generated" for row in accounting
        )
        if pending_count or ready_count + blocked_count != generated_count:
            status = "in_progress"
        elif failed_count or blocked_count:
            status = "partial" if ready_count else "blocked"
        else:
            status = "ready_for_studio_export"
        result.update({
            "status": status,
            "selected_arc_count": len(accounting),
            "accounted_arc_count": len(accounting),
            "generated_count": generated_count,
            "pending_count": pending_count,
            "failed_generation_count": failed_count,
            "ready_count": ready_count,
            "blocked_count": blocked_count,
            "arc_accounting": accounting,
        })
        if status == "blocked":
            if failed_count == len(accounting):
                result.update({
                    "blocked_phase": "materialization",
                    "blocked_reason": "All selected stories failed materialization.",
                    "blocked_evidence": [
                        f"card={row['selection_rank']} generation_status=failed"
                        for row in accounting if row["generation_status"] == "failed"
                    ],
                })
            else:
                result.update({
                    "blocked_phase": "child_qc",
                    "blocked_reason": "All generated children are terminally blocked.",
                    "blocked_evidence": [
                        *[
                            f"child_project_id={row['child_project_id']} "
                            "parent_qc_status=blocked"
                            for row in accounting
                            if row["parent_qc_status"] == "blocked"
                        ],
                        *[
                            f"card={row['selection_rank']} generation_status=failed"
                            for row in accounting
                            if row["generation_status"] == "failed"
                        ],
                    ],
                })
        else:
            result.update({
                "blocked_phase": None,
                "blocked_reason": None,
                "blocked_evidence": [],
            })
        return result

    def record_coordinator_result(self, run: str = "run-a", result: dict | None = None,
                                  expect: int = 0, op: str | None = None):
        thread, host = self.identities[run]
        return self.run_cli(
            "record-run-result", "--run-id", run,
            "--coordinator-thread-id", thread,
            "--coordinator-host-id", host,
            "--result-json", json.dumps(result or self.coordinator_result(run)),
            "--op-id", op or f"{run}:coordinator-result", expect=expect,
        )[1]

    def finish_pass(self, run: str, child: int, assignment: str,
                    fingerprint: str, edit_lease: str, child_thread: str,
                    host: str) -> None:
        edit = self.grant(edit_lease, "edit", 0, run, child, assignment, fingerprint)
        self.record_child(
            edit_lease, edit["lease_generation"], "ready", run=run, child=child,
            assignment=assignment, fingerprint=fingerprint,
            thread=child_thread, host=host,
        )
        self.close(
            edit_lease, edit["lease_generation"], "ready", "release",
            f"{edit_lease}:close", run, child,
        )
        qc_id = f"{edit_lease}:qc"
        qc = self.grant(qc_id, "qc", 0, run, child, assignment, fingerprint)
        self.record_qc(
            qc_id, qc["lease_generation"], "pass", 0, run, child,
            assignment, fingerprint,
        )
        self.close(
            qc_id, qc["lease_generation"], "ready", "release",
            f"{qc_id}:close", run, child,
        )

    def test_missing_old_and_unknown_database_fail_closed(self):
        missing = Path(self.temp.name) / "missing.sqlite3"
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--registry", str(missing), "audit"],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertFalse(missing.exists())

        for old_version in (1, 2):
            old = Path(self.temp.name) / f"old-v{old_version}.sqlite3"
            conn = sqlite3.connect(old)
            conn.execute("PRAGMA application_id=1448235860")
            conn.execute(f"PRAGMA user_version={old_version}")
            conn.commit()
            conn.close()
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--registry", str(old), "audit"],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn(
                f"version={old_version}", json.loads(result.stdout)["error"]
            )

    def test_empty_v3_migrates_transactionally_with_verified_backup(self):
        legacy = Path(self.temp.name) / "legacy-v3.sqlite3"
        subprocess.run(
            [sys.executable, str(SCRIPT), "--registry", str(legacy), "init"],
            text=True, capture_output=True, check=True,
        )
        conn = sqlite3.connect(legacy)
        for table, column in (
            ("runs", "coordinator_result_json"),
            ("runs", "coordinator_result_validated_digest"),
            ("children", "assignment_json"),
            ("children", "assignment_validated_digest"),
            ("children", "recast_input_json"),
            ("children", "recast_input_validated_digest"),
            ("children", "recast_json"),
            ("children", "recast_validated_digest"),
            ("children", "recast_approval_status"),
            ("children", "approved_candidate_id"),
            ("children", "pre_mutation_block_reason"),
        ):
            conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        conn.execute("DROP TABLE child_task_intents")
        conn.execute("UPDATE registry_meta SET schema_version=3 WHERE singleton=1")
        conn.execute("PRAGMA user_version=3")
        conn.commit()
        conn.close()
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--registry", str(legacy), "init"],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "migrated_v3_to_v4")
        self.assertEqual(payload["backup_verification"]["integrity_check"], "ok")
        self.assertFalse(any(payload["backup_verification"]["row_counts"].values()))
        backup = Path(payload["backup_path"])
        self.assertTrue(backup.is_file())
        conn = sqlite3.connect(backup)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 3)
        self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        conn.close()
        self.assertEqual(
            subprocess.run(
                [sys.executable, str(SCRIPT), "--registry", str(legacy), "audit"],
                text=True, capture_output=True, check=False,
            ).returncode,
            0,
        )

    def test_idempotent_replay_and_changed_request_rejected(self):
        args = [
            "create-run", "--run-id", "run-a", "--topic", "one topic",
            "--coordinator-thread-id", "coord-thread-a",
            "--coordinator-host-id", "host-a", "--op-id", "stable-op",
        ]
        first = self.run_cli(*args, expect=0)[1]
        self.assertEqual(first, self.run_cli(*args, expect=0)[1])
        args[args.index("one topic")] = "changed topic"
        self.assertEqual(self.run_cli(*args, expect=7)[1]["status"],
                         "idempotency_key_reused")

    def test_coordinator_phase_lease_is_global_and_snapshots_resume(self):
        self.create_run("run-a")
        self.create_run("run-b")
        grant = self.run_cli(
            "grant-run-lease", "--run-id", "run-a", "--lease-id", "acq-a",
            "--phase", "acquisition", "--op-id", "acq-a:grant", expect=0,
        )[1]
        busy = self.run_cli(
            "grant-run-lease", "--run-id", "run-b", "--lease-id", "acq-b",
            "--phase", "acquisition", "--op-id", "acq-b:grant", expect=4,
        )[1]
        self.assertEqual(busy["status"], "global_lease_busy")
        generation = grant["lease_generation"]
        exact = [
            "--run-id", "run-a", "--lease-id", "acq-a",
            "--lease-generation", str(generation), "--phase", "acquisition",
            "--thread-id", "coord-thread-a", "--host-id", "host-a",
        ]
        self.run_cli(
            "record-run-snapshot", *exact, "--checkpoint-status", "ready",
            "--data-json", json.dumps(acquisition_checkpoint(100, ONE_SELECTION)),
            "--op-id", "acq-a:snapshot", expect=0,
        )
        self.run_cli(
            "close-run-lease", "--run-id", "run-a", "--lease-id", "acq-a",
            "--lease-generation", str(generation), "--phase", "acquisition",
            "--coordinator-thread-id", "coord-thread-a",
            "--coordinator-host-id", "host-a", "--action", "release",
            "--checkpoint-status", "ready", "--outstanding-job-ids-json", "[]",
            "--reason", "safe", "--op-id", "acq-a:close", expect=0,
        )
        snapshot = self.run_cli("resume", "--run-id", "run-a", expect=0)[1]
        self.assertEqual(snapshot["run_snapshots"][0]["data"]["parent_project_id"], 100)

    def test_visible_task_intent_is_durable_one_shot_and_consumed_atomically(self):
        self.create_bound_run()
        assignment = self.register_child(task_state="prepared")
        client_request_id = "run-a:task:101"
        transition = [
            "--client-request-id", client_request_id,
            "--run-id", "run-a", "--child-project-id", "101", "--card", "1",
            "--assignment-id", assignment["assignment_id"],
            "--assignment-fingerprint", assignment["assignment_input_fingerprint"],
            "--coordinator-thread-id", "coord-thread-a",
            "--coordinator-host-id", "host-a",
        ]
        resumed = self.run_cli("resume", "--run-id", "run-a", expect=0)[1]
        intent = resumed["child_task_intents"][0]
        self.assertEqual(intent["state"], "prepared")
        self.assertEqual(intent["next_action"], "begin_child_task_create")
        self.assertEqual(resumed["children"], [])
        unresolved_result = self.run_cli(
            "record-run-result", "--run-id", "run-a",
            "--coordinator-thread-id", "coord-thread-a",
            "--coordinator-host-id", "host-a",
            "--result-json", "{}", "--op-id", "run-a:premature-result",
            expect=5,
        )[1]
        self.assertEqual(unresolved_result["status"], "visible_task_intents_unresolved")

        duplicate = self.run_cli(
            "prepare-child-task-intent",
            "--client-request-id", client_request_id,
            "--run-id", "run-a",
            "--selection-fingerprint", resumed["run"]["selection_fingerprint"],
            "--child-project-id", "101", "--card", "1", "--title", "Card 1",
            "--assignment-id", assignment["assignment_id"],
            "--assignment-fingerprint", assignment["assignment_input_fingerprint"],
            "--assignment-json", json.dumps(assignment),
            "--coordinator-thread-id", "coord-thread-a",
            "--coordinator-host-id", "host-a",
            "--op-id", "run-a:duplicate-task-intent", expect=4,
        )[1]
        self.assertEqual(duplicate["status"], "task_intent_replacement_refused")

        claimed = self.run_cli(
            "begin-child-task-create", *transition,
            "--op-id", "run-a:claim-visible-task", expect=0,
        )[1]
        self.assertTrue(claimed["task_marker"].startswith("VPS-"))
        resumed = self.run_cli("resume", "--run-id", "run-a", expect=0)[1]
        self.assertEqual(
            resumed["child_task_intents"][0]["next_action"],
            "reconcile_create_outcome_never_retry",
        )
        second_claim = self.run_cli(
            "begin-child-task-create", *transition,
            "--op-id", "run-a:second-create-attempt", expect=5,
        )[1]
        self.assertEqual(
            second_claim["status"], "task_create_already_claimed_never_retry"
        )

        self.run_cli(
            "record-child-task-queued", *transition,
            "--client-thread-id", "queued-run-a-101",
            "--op-id", "run-a:queue-visible-task", expect=0,
        )
        resumed = self.run_cli("resume", "--run-id", "run-a", expect=0)[1]
        self.assertEqual(resumed["child_task_intents"][0]["state"], "queued")
        self.assertEqual(
            resumed["child_task_intents"][0]["client_thread_id"],
            "queued-run-a-101",
        )
        wrong_client = self.run_cli(
            "resolve-child-task-intent", *transition,
            "--client-thread-id", "wrong-client-id",
            "--thread-id", "child-thread-a", "--host-id", "host-a",
            "--op-id", "run-a:wrong-client-resolution", expect=4,
        )[1]
        self.assertEqual(wrong_client["status"], "queued_client_thread_id_mismatch")
        self.run_cli(
            "resolve-child-task-intent", *transition,
            "--client-thread-id", "queued-run-a-101",
            "--thread-id", "child-thread-a", "--host-id", "host-a",
            "--op-id", "run-a:resolve-visible-task", expect=0,
        )
        bad_registration = self.run_cli(
            "register-child", "--client-request-id", client_request_id,
            "--run-id", "run-a",
            "--selection-fingerprint", resumed["run"]["selection_fingerprint"],
            "--child-project-id", "101", "--card", "1", "--title", "Card 1",
            "--thread-id", "wrong-thread", "--host-id", "host-a",
            "--assignment-id", assignment["assignment_id"],
            "--assignment-fingerprint", assignment["assignment_input_fingerprint"],
            "--assignment-json", json.dumps(assignment),
            "--op-id", "run-a:wrong-task-registration", expect=4,
        )[1]
        self.assertEqual(bad_registration["status"], "task_intent_identity_mismatch")
        resumed = self.run_cli("resume", "--run-id", "run-a", expect=0)[1]
        self.assertEqual(resumed["child_task_intents"][0]["state"], "resolved")
        self.assertEqual(resumed["children"], [])
        self.run_cli(
            "register-child", "--client-request-id", client_request_id,
            "--run-id", "run-a",
            "--selection-fingerprint", resumed["run"]["selection_fingerprint"],
            "--child-project-id", "101", "--card", "1", "--title", "Card 1",
            "--thread-id", "child-thread-a", "--host-id", "host-a",
            "--assignment-id", assignment["assignment_id"],
            "--assignment-fingerprint", assignment["assignment_input_fingerprint"],
            "--assignment-json", json.dumps(assignment),
            "--op-id", "run-a:consume-task-intent", expect=0,
        )
        resumed = self.run_cli("resume", "--run-id", "run-a", expect=0)[1]
        self.assertEqual(resumed["child_task_intents"][0]["state"], "consumed")
        self.assertEqual(resumed["child_task_intents"][0]["next_action"], "none")
        self.assertEqual(resumed["children"][0]["thread_id"], "child-thread-a")
        self.run_cli("audit", expect=0)

        self.create_bound_run("run-b", parent=200)
        direct_assignment = self.register_child(
            run="run-b", child=201, thread="child-thread-b", host="host-b",
            assignment="assign-b", fingerprint=ASSIGNMENT_B,
            task_state="dispatching",
        )
        direct_transition = [
            "--client-request-id", "run-b:task:201",
            "--run-id", "run-b", "--child-project-id", "201", "--card", "1",
            "--assignment-id", direct_assignment["assignment_id"],
            "--assignment-fingerprint",
            direct_assignment["assignment_input_fingerprint"],
            "--coordinator-thread-id", "coord-thread-b",
            "--coordinator-host-id", "host-b",
        ]
        self.run_cli(
            "resolve-child-task-intent", *direct_transition,
            "--thread-id", "child-thread-b", "--host-id", "host-b",
            "--op-id", "run-b:direct-task-resolution", expect=0,
        )
        direct_resume = self.run_cli("resume", "--run-id", "run-b", expect=0)[1]
        self.assertIsNone(direct_resume["child_task_intents"][0]["client_thread_id"])
        self.run_cli(
            "register-child", "--client-request-id", "run-b:task:201",
            "--run-id", "run-b",
            "--selection-fingerprint", direct_resume["run"]["selection_fingerprint"],
            "--child-project-id", "201", "--card", "1", "--title", "Card 1",
            "--thread-id", "child-thread-b", "--host-id", "host-b",
            "--assignment-id", direct_assignment["assignment_id"],
            "--assignment-fingerprint",
            direct_assignment["assignment_input_fingerprint"],
            "--assignment-json", json.dumps(direct_assignment),
            "--op-id", "run-b:consume-direct-task-intent", expect=0,
        )
        direct_resume = self.run_cli("resume", "--run-id", "run-b", expect=0)[1]
        self.assertEqual(direct_resume["child_task_intents"][0]["state"], "consumed")
        self.run_cli("audit", expect=0)

    def test_coordinator_and_child_task_roles_are_reciprocally_disjoint(self):
        self.create_bound_run()
        assignment = self.register_child(task_state="dispatching")
        transition = [
            "--client-request-id", "run-a:task:101",
            "--run-id", "run-a", "--child-project-id", "101", "--card", "1",
            "--assignment-id", assignment["assignment_id"],
            "--assignment-fingerprint",
            assignment["assignment_input_fingerprint"],
            "--coordinator-thread-id", "coord-thread-a",
            "--coordinator-host-id", "host-a",
        ]
        refused_child = self.run_cli(
            "resolve-child-task-intent", *transition,
            "--thread-id", "coord-thread-a", "--host-id", "host-a",
            "--op-id", "reject-coordinator-as-child", expect=4,
        )[1]
        self.assertEqual(
            refused_child["status"], "child_identity_is_coordinator_task"
        )

        self.run_cli(
            "resolve-child-task-intent", *transition,
            "--thread-id", "child-thread-a", "--host-id", "host-a",
            "--op-id", "resolve-real-child", expect=0,
        )
        refused_coordinator = self.run_cli(
            "create-run", "--run-id", "run-b", "--topic", "collision",
            "--coordinator-thread-id", "child-thread-a",
            "--coordinator-host-id", "host-a",
            "--op-id", "reject-child-as-coordinator", expect=4,
        )[1]
        self.assertEqual(
            refused_coordinator["status"], "coordinator_identity_is_child_task"
        )

        # Simulate a legacy/corrupt v4 file that predates the reciprocal guards.
        # Every mutator and init must fail closed, while audit/resume expose it.
        conn = sqlite3.connect(self.registry)
        conn.execute(
            "INSERT INTO runs(run_id,topic,coordinator_thread_id,"
            "coordinator_host_id,status,phase,created_at_s,updated_at_s) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                "run-corrupt", "legacy collision", "child-thread-a", "host-a",
                "active", "created", 1, 1,
            ),
        )
        conn.commit()
        conn.close()

        audit = self.run_cli("audit", expect=6)[1]
        self.assertEqual(audit["status"], "fail")
        self.assertEqual(
            audit["task_role_collisions"][0]["mapping_kind"],
            "resolved_intent",
        )
        resumed = self.run_cli("resume", "--run-id", "run-a", expect=0)[1]
        self.assertEqual(resumed["status"], "registry_task_role_collision")
        self.assertFalse(resumed["can_grant_run_lease"])
        self.assertFalse(resumed["can_grant_child_lease"])
        refused_register = self.run_cli(
            "register-child", "--client-request-id", "run-a:task:101",
            "--run-id", "run-a",
            "--selection-fingerprint", resumed["run"]["selection_fingerprint"],
            "--child-project-id", "101", "--card", "1", "--title", "Card 1",
            "--thread-id", "child-thread-a", "--host-id", "host-a",
            "--assignment-id", assignment["assignment_id"],
            "--assignment-fingerprint",
            assignment["assignment_input_fingerprint"],
            "--assignment-json", json.dumps(assignment),
            "--op-id", "register-under-role-collision", expect=6,
        )[1]
        self.assertEqual(refused_register["status"], "registry_task_role_collision")
        self.assertEqual(self.run_cli("init", expect=2)[1]["ok"], False)

    def test_create_run_vs_child_resolution_role_race_has_one_winner(self):
        self.create_bound_run()
        assignment = self.register_child(task_state="dispatching")
        resolve_args = [
            "resolve-child-task-intent",
            "--client-request-id", "run-a:task:101",
            "--run-id", "run-a", "--child-project-id", "101", "--card", "1",
            "--assignment-id", assignment["assignment_id"],
            "--assignment-fingerprint",
            assignment["assignment_input_fingerprint"],
            "--coordinator-thread-id", "coord-thread-a",
            "--coordinator-host-id", "host-a",
            "--thread-id", "race-visible-task", "--host-id", "race-host",
            "--op-id", "race-resolve-child",
        ]
        create_args = [
            "create-run", "--run-id", "run-b", "--topic", "race run",
            "--coordinator-thread-id", "race-visible-task",
            "--coordinator-host-id", "race-host",
            "--op-id", "race-create-coordinator",
        ]
        context = multiprocessing.get_context("spawn")
        start, queue = context.Event(), context.Queue()
        processes = [
            context.Process(
                target=_race_registry_command,
                args=(str(self.registry), arguments, start, queue),
            )
            for arguments in (resolve_args, create_args)
        ]
        for process in processes:
            process.start()
        start.set()
        results = [queue.get(timeout=30) for _ in processes]
        for process in processes:
            process.join(timeout=30)
            self.assertEqual(process.exitcode, 0)
        self.assertEqual(sorted(code for code, _ in results), [0, 4])
        losing_status = next(payload["status"] for code, payload in results if code == 4)
        self.assertIn(
            losing_status,
            {
                "child_identity_is_coordinator_task",
                "coordinator_identity_is_child_task",
            },
        )
        self.run_cli("audit", expect=0)

    def test_full_edit_qc_lifecycle_persists_attempt_history(self):
        self.create_bound_run()
        self.register_child()
        self.finish_pass("run-a", 101, "assign-a", ASSIGNMENT_A,
                         "edit-a", "child-thread-a", "host-a")
        self.record_coordinator_result()
        self.run_cli(
            "finalize-run", "--run-id", "run-a",
            "--coordinator-thread-id", "coord-thread-a",
            "--coordinator-host-id", "host-a", "--status", "ready",
            "--op-id", "finalize-a", expect=0,
        )
        snapshot = self.run_cli("resume", "--run-id", "run-a", expect=0)[1]
        self.assertEqual(snapshot["children"][0]["qc_status"], "pass")
        self.assertEqual([item["actor"] for item in snapshot["attempt_results"]],
                         ["child", "qc"])
        self.assertEqual(snapshot["children"][0]["exclusions"], {"asset_ids": [77]})
        self.run_cli("audit", expect=0)

    def test_exact_assignment_editor_qc_and_final_lineage_rejects_tampering(self):
        self.create_bound_run()
        assignment = self.register_child()
        snapshot = self.run_cli("resume", "--run-id", "run-a", expect=0)[1]
        self.assertEqual(snapshot["children"][0]["assignment"], assignment)
        self.assertTrue(snapshot["children"][0]["assignment_validated_digest"])

        edit = self.grant("lineage-edit")
        generation = edit["lease_generation"]
        auth = self.auth_args("lineage-edit", generation)
        tampered_editor = self.child_report(
            "lineage-edit", generation, "ready"
        )
        tampered_editor["source_lineage"]["seeded_child_end_s"] += 0.25
        self.assertIn(
            "source_lineage",
            self.run_cli(
                "record-child-result", *auth, "--status", "ready",
                "--result-json", json.dumps(tampered_editor),
                "--exclusions-json", "{}", "--op-id", "tampered-editor",
                expect=2,
            )[1]["error"],
        )
        self.record_child("lineage-edit", generation, "ready")
        self.close("lineage-edit", generation, "ready", "release", "close-lineage-edit")

        qc = self.grant("lineage-qc", "qc", 0)
        qc_generation = qc["lease_generation"]
        qc_auth = self.auth_args(
            "lineage-qc", qc_generation, "qc", 0,
            thread="coord-thread-a", host="host-a",
        )
        tampered_qc = self.qc_report("pass", 0)
        tampered_qc["editor_claims"]["final_edl_version"] += 1
        self.assertIn(
            "editor_claims",
            self.run_cli(
                "record-qc-result", *qc_auth, "--status", "pass",
                "--result-json", json.dumps(tampered_qc),
                "--exclusions-json", "{}", "--op-id", "tampered-qc",
                expect=2,
            )[1]["error"],
        )
        self.record_qc("lineage-qc", qc_generation, "pass")
        self.close("lineage-qc", qc_generation, "ready", "release", "close-lineage-qc")

        final = self.coordinator_result()
        tampered_final = copy.deepcopy(final)
        tampered_final["source_asset_id"] += 1
        self.assertIn(
            "source_asset_id",
            self.record_coordinator_result(
                result=tampered_final, expect=2, op="tampered-final"
            )["error"],
        )
        tampered_count = copy.deepcopy(final)
        tampered_count["ready_count"] = 0
        self.assertIn(
            "ready_count",
            self.record_coordinator_result(
                result=tampered_count, expect=2, op="tampered-count"
            )["error"],
        )
        self.record_coordinator_result(result=final)
        conflict = copy.deepcopy(final)
        conflict["topic"] = "replacement"
        self.assertEqual(
            self.record_coordinator_result(
                result=conflict, expect=4, op="replace-final"
            )["status"],
            "run_result_conflict",
        )

    def test_ambiguous_assignment_requires_exact_approved_recast_before_edit(self):
        self.create_bound_run()
        self.register_child(assignment_status="requires_pre_mutation_recast")
        refused = self.grant("edit-before-recast", expect=5)
        self.assertEqual(refused["status"], "pre_mutation_recast_not_approved")
        self.record_approved_recast()
        snapshot = self.run_cli("resume", "--run-id", "run-a", expect=0)[1]
        self.assertEqual(snapshot["children"][0]["recast"]["status"], "approved")
        edit = self.grant("edit-after-recast")
        self.record_child(
            "edit-after-recast", edit["lease_generation"], "ready"
        )

    def test_blocked_before_mutation_needs_no_lease_and_unblocks_fifo(self):
        selection = selection_payload([
            {"story_id": "story-one", "card": 1, "title": "Card 1"},
            {"story_id": "story-two", "card": 2, "title": "Card 2"},
        ])
        entries = selection_entries(selection)
        self.create_bound_run(
            selection=selection,
            materialization=[
                {**entries[0], "status": "materialized", "child_project_id": 101},
                {**entries[1], "status": "materialized", "child_project_id": 102},
            ],
        )
        blocked_assignment = self.register_child(
            assignment_status="blocked_before_mutation"
        )
        refused = self.grant("forbidden-blocked-edit", expect=5)
        self.assertEqual(refused["status"], "assignment_blocked_before_mutation")
        self.run_cli(
            "block-child-before-mutation", "--run-id", "run-a",
            "--child-project-id", "101",
            "--coordinator-thread-id", "coord-thread-a",
            "--coordinator-host-id", "host-a",
            "--reason", blocked_assignment["blocked_reason"],
            "--op-id", "block-before-mutation", expect=0,
        )
        self.register_child(
            child=102, card=2, thread="child-thread-two",
            assignment="assign-two", fingerprint=ASSIGNMENT_B,
        )
        snapshot = self.run_cli("resume", "--run-id", "run-a", expect=0)[1]
        self.assertEqual(snapshot["children"][0]["qc_status"], "blocked")
        self.assertEqual(snapshot["attempt_results"], [])
        allowed = self.grant(
            "edit-card-two", child=102, assignment="assign-two",
            fingerprint=ASSIGNMENT_B,
        )
        self.assertEqual(allowed["holder_thread_id"], "child-thread-two")

    def test_all_premutation_blocked_maps_to_child_qc_terminal_block(self):
        self.create_bound_run()
        assignment = self.register_child(
            assignment_status="blocked_before_mutation"
        )
        self.run_cli(
            "block-child-before-mutation", "--run-id", "run-a",
            "--child-project-id", "101",
            "--coordinator-thread-id", "coord-thread-a",
            "--coordinator-host-id", "host-a",
            "--reason", assignment["blocked_reason"],
            "--op-id", "terminal-premutation-block", expect=0,
        )
        result = self.coordinator_result()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["blocked_phase"], "child_qc")
        self.record_coordinator_result(result=result)
        self.run_cli(
            "finalize-run", "--run-id", "run-a",
            "--coordinator-thread-id", "coord-thread-a",
            "--coordinator-host-id", "host-a", "--status", "blocked",
            "--op-id", "finalize-child-qc-block", expect=0,
        )

    def test_frozen_lease_cannot_revive_and_new_generation_fences_aba(self):
        self.create_bound_run()
        self.register_child()
        first = self.grant()
        gen1 = first["lease_generation"]
        self.record_child("lease-edit-a", gen1, "paused_safe")
        self.close("lease-edit-a", gen1, "paused_safe", "freeze", "freeze-a")
        second = self.grant("lease-edit-resume", "edit", 0)
        self.assertGreater(second["lease_generation"], gen1)
        self.run_cli(
            "check-lease", *self.auth_args("lease-edit-a", gen1), expect=4
        )

    def test_parent_qc_required_and_repair_cap_becomes_blocked(self):
        self.create_bound_run()
        self.register_child()
        edit = self.grant()
        self.record_child("lease-edit-a", edit["lease_generation"], "needs_repair")
        self.close(
            "lease-edit-a", edit["lease_generation"], "paused_safe", "release",
            "close-needs-repair",
        )
        self.assertEqual(self.grant("illegal-repair", "repair", 1, expect=5)["status"],
                         "lease_transition_refused")
        qc0 = self.grant("qc-0", "qc", 0)
        self.record_qc("qc-0", qc0["lease_generation"], "repair_required", 0)
        self.close("qc-0", qc0["lease_generation"], "paused_safe", "release", "close-qc0")

        for round_number in (1, 2):
            repair_id = f"repair-{round_number}"
            repair = self.grant(repair_id, "repair", round_number)
            self.record_child(
                repair_id, repair["lease_generation"], "ready", "repair", round_number
            )
            self.close(
                repair_id, repair["lease_generation"], "ready", "release",
                f"close-{repair_id}",
            )
            qc_id = f"qc-{round_number}"
            qc = self.grant(qc_id, "qc", round_number)
            if round_number == 1:
                self.record_qc(qc_id, qc["lease_generation"], "repair_required", 1)
                self.close(
                    qc_id, qc["lease_generation"], "paused_safe", "release",
                    f"close-{qc_id}",
                )
            else:
                refused = self.record_qc(
                    qc_id, qc["lease_generation"], "repair_required", 2, expect=2
                )
                self.assertIn("repair cap reached", refused["error"])
                self.record_qc(qc_id, qc["lease_generation"], "blocked", 2)
                self.close(
                    qc_id, qc["lease_generation"], "blocked", "freeze",
                    f"close-{qc_id}",
                )

    def test_fifo_advances_visible_task_only_after_prior_parent_qc_terminal(self):
        selection = selection_payload([
            {"story_id": "story-one", "card": 1, "title": "Card 1"},
            {"story_id": "story-two", "card": 2, "title": "Card 2"},
        ])
        entries = selection_entries(selection)
        materialization = [
            {**entries[0], "status": "materialized", "child_project_id": 101},
            {**entries[1], "status": "materialized", "child_project_id": 102},
        ]
        self.create_bound_run(selection=selection, materialization=materialization)
        self.register_child()
        future = self.register_child(
            child=102, card=2, thread="child-thread-two", assignment="assign-two",
            fingerprint=ASSIGNMENT_B, task_state="unprepared",
        )
        self.assertIn(
            "prior card 1 is not terminal",
            self.run_cli(
                "prepare-child-task-intent",
                "--client-request-id", "run-a:task:102",
                "--run-id", "run-a", "--selection-fingerprint",
                self.artifacts["run-a"]["selection"]["selection_fingerprint"],
                "--child-project-id", "102", "--card", "2", "--title", "Card 2",
                "--assignment-id", future["assignment_id"],
                "--assignment-fingerprint", future["assignment_input_fingerprint"],
                "--assignment-json", json.dumps(future),
                "--coordinator-thread-id", "coord-thread-a",
                "--coordinator-host-id", "host-a",
                "--op-id", "run-a:early-task-two", expect=2,
            )[1]["error"],
        )
        self.finish_pass("run-a", 101, "assign-a", ASSIGNMENT_A,
                         "edit-one", "child-thread-a", "host-a")
        self.register_child(
            child=102, card=2, thread="child-thread-two", assignment="assign-two",
            fingerprint=ASSIGNMENT_B,
        )
        allowed = self.grant(
            "edit-two", "edit", 0, child=102, assignment="assign-two",
            fingerprint=ASSIGNMENT_B,
        )
        self.assertEqual(allowed["holder_thread_id"], "child-thread-two")

    def test_tampered_selection_and_parent_child_collision_are_rejected(self):
        self.create_run()
        self.run_phase("run-a", "acquisition", acquisition_checkpoint(100, ONE_SELECTION))
        tampered = copy.deepcopy(ONE_SELECTION)
        tampered["clips"][0]["title"] = "Changed"
        grant = self.run_cli(
            "grant-run-lease", "--run-id", "run-a", "--lease-id", "bad-selection",
            "--phase", "selection", "--op-id", "bad-selection:grant", expect=0,
        )[1]
        exact = [
            "--run-id", "run-a", "--lease-id", "bad-selection",
            "--lease-generation", str(grant["lease_generation"]),
            "--phase", "selection", "--thread-id", "coord-thread-a",
            "--host-id", "host-a",
        ]
        self.assertIn(
            "does not match",
            self.run_cli(
                "record-run-snapshot", *exact, "--checkpoint-status", "ready",
                "--data-json", json.dumps(tampered),
                "--op-id", "bad-selection:snapshot", expect=2,
            )[1]["error"],
        )
        wrong_parent = copy.deepcopy(ONE_SELECTION)
        wrong_parent["parent_project_id"] = 999
        wrong_parent["selection_fingerprint"] = canonical_fingerprint(
            "selection", _canonical(wrong_parent)
        )
        self.assertIn(
            "does not match frozen acquisition",
            self.run_cli(
                "record-run-snapshot", *exact, "--checkpoint-status", "ready",
                "--data-json", json.dumps(wrong_parent),
                "--op-id", "bad-selection:wrong-parent", expect=2,
            )[1]["error"],
        )

        other = RegistryTests(methodName="runTest")
        other.setUp()
        try:
            materialization = [
                {**selection_entries(ONE_SELECTION)[0], "status": "materialized",
                 "child_project_id": 100}
            ]
            with self.assertRaises(AssertionError) as rejected:
                other.create_bound_run(materialization=materialization)
            self.assertIn("collides with an existing parent", str(rejected.exception))
        finally:
            other.tearDown()

    def test_materialization_source_and_reference_lineage_are_frozen(self):
        self.create_run()
        self.run_phase("run-a", "acquisition", acquisition_checkpoint(100, ONE_SELECTION))
        selection = copy.deepcopy(ONE_SELECTION)
        self.run_phase("run-a", "selection", selection)
        self.run_cli(
            "bind-run", "--run-id", "run-a", "--parent-project-id", "100",
            "--selection-fingerprint", selection["selection_fingerprint"],
            "--op-id", "run-a:bind", expect=0,
        )
        reference = contract_fixture("reference_profile")
        rebind_reference_profile(reference, 600)
        self.run_phase("run-a", "reference", reference)
        acquisition = contract_fixture("acquisition_record")
        acquisition.update({
            "run_id": "run-a", "topic": "topic for run-a",
            "parent_project_id": 100, "selected_clip_count": 1,
        })
        bind_acquisition_artifacts(acquisition, selection, reference)
        refused = self.run_cli(
            "grant-run-lease", "--run-id", "run-a", "--lease-id", "mat-too-early",
            "--phase", "materialization", "--op-id", "mat-too-early:grant", expect=5,
        )[1]
        self.assertEqual(refused["status"], "acquisition_record_not_ready")
        grant = self.run_cli(
            "grant-run-lease", "--run-id", "run-a", "--lease-id", "acq-record",
            "--phase", "acquisition_record", "--op-id", "acq-record:grant", expect=0,
        )[1]
        exact = [
            "--run-id", "run-a", "--lease-id", "acq-record",
            "--lease-generation", str(grant["lease_generation"]),
            "--phase", "acquisition_record", "--thread-id", "coord-thread-a",
            "--host-id", "host-a",
        ]

        bad_source = copy.deepcopy(acquisition)
        bad_source["source"]["youtube_video_id"] = "zzzzzzzzzzz"
        bad_source["source"]["canonical_url"] = (
            "https://www.youtube.com/watch?v=zzzzzzzzzzz"
        )
        self.assertIn(
            "selection source YouTube video does not match acquisition",
            self.run_cli(
                "record-run-snapshot", *exact, "--checkpoint-status", "ready",
                "--data-json", json.dumps(bad_source),
                "--op-id", "mat-lineage:bad-source", expect=2,
            )[1]["error"],
        )
        bad_reference = copy.deepcopy(acquisition)
        bad_reference["reference"]["asset_id"] = 998
        self.assertIn(
            "reference profile asset ID does not match acquisition",
            self.run_cli(
                "record-run-snapshot", *exact, "--checkpoint-status", "ready",
                "--data-json", json.dumps(bad_reference),
                "--op-id", "mat-lineage:bad-reference", expect=2,
            )[1]["error"],
        )
        self.run_cli(
            "record-run-snapshot", *exact, "--checkpoint-status", "ready",
            "--data-json", json.dumps(acquisition),
            "--op-id", "mat-lineage:valid", expect=0,
        )
        self.run_cli(
            "close-run-lease", "--run-id", "run-a", "--lease-id", "acq-record",
            "--lease-generation", str(grant["lease_generation"]),
            "--phase", "acquisition_record",
            "--coordinator-thread-id", "coord-thread-a",
            "--coordinator-host-id", "host-a", "--action", "release",
            "--checkpoint-status", "ready", "--outstanding-job-ids-json", "[]",
            "--reason", "record frozen before materialization",
            "--op-id", "acq-record:close", expect=0,
        )
        self.run_cli(
            "grant-run-lease", "--run-id", "run-a", "--lease-id", "mat-after-record",
            "--phase", "materialization", "--op-id", "mat-after-record:grant",
            expect=0,
        )

    def test_materialization_freezes_deterministic_seed_range_and_assignment_echo(self):
        self.create_run()
        selection = copy.deepcopy(ONE_SELECTION)
        self.run_phase(
            "run-a", "acquisition", acquisition_checkpoint(100, selection)
        )
        self.run_phase("run-a", "selection", selection)
        self.run_cli(
            "bind-run", "--run-id", "run-a", "--parent-project-id", "100",
            "--selection-fingerprint", selection["selection_fingerprint"],
            "--op-id", "bind-seed-range", expect=0,
        )
        reference = contract_fixture("reference_profile")
        rebind_reference_profile(reference, 600)
        self.run_phase("run-a", "reference", reference)
        acquisition = contract_fixture("acquisition_record")
        acquisition.update({
            "run_id": "run-a", "topic": "topic for run-a",
            "parent_project_id": 100, "selected_clip_count": 1,
        })
        bind_acquisition_artifacts(acquisition, selection, reference)
        self.run_phase("run-a", "acquisition_record", acquisition)
        grant = self.run_cli(
            "grant-run-lease", "--run-id", "run-a", "--lease-id", "seed-mat",
            "--phase", "materialization", "--op-id", "seed-mat:grant", expect=0,
        )[1]
        exact = [
            "--run-id", "run-a", "--lease-id", "seed-mat",
            "--lease-generation", str(grant["lease_generation"]),
            "--phase", "materialization", "--thread-id", "coord-thread-a",
            "--host-id", "host-a",
        ]
        entry = selection_entries(selection)[0]
        entry.update({
            "status": "materialized", "child_project_id": 101,
            "seeded_child_start_s": 9.75,
            "seed_snap_reason": "none",
            "seed_range_verified_by": "authoritative_child_edl",
        })
        data = {
            "selection_fingerprint": selection["selection_fingerprint"],
            "stories": [entry],
        }
        self.assertIn(
            "word_boundary_snap",
            self.run_cli(
                "record-run-snapshot", *exact, "--checkpoint-status", "ready",
                "--data-json", json.dumps(data),
                "--op-id", "seed-mat:unexplained-drift", expect=2,
            )[1]["error"],
        )
        entry.update({
            "seed_snap_reason": "word_boundary_snap",
            "seed_range_verified_by": "audit_snap_keep_to_words",
        })
        self.run_cli(
            "record-run-snapshot", *exact, "--checkpoint-status", "ready",
            "--data-json", json.dumps(data),
            "--op-id", "seed-mat:verified-drift", expect=0,
        )
        self.run_cli(
            "close-run-lease", "--run-id", "run-a", "--lease-id", "seed-mat",
            "--lease-generation", str(grant["lease_generation"]),
            "--phase", "materialization",
            "--coordinator-thread-id", "coord-thread-a",
            "--coordinator-host-id", "host-a", "--action", "release",
            "--checkpoint-status", "ready", "--outstanding-job-ids-json", "[]",
            "--reason", "verified deterministic seed", "--op-id", "seed-mat:close",
            expect=0,
        )
        self.artifacts["run-a"] = {
            "selection": selection,
            "reference": reference,
            "acquisition": acquisition,
        }
        assignment = self.register_child()
        self.assertEqual(assignment["source"]["seeded_child_start_s"], 9.75)
        tampered = copy.deepcopy(assignment)
        tampered["source"]["seeded_child_start_s"] = 9.5
        tampered["assignment_input_fingerprint"] = canonical_fingerprint(
            "assignment", _canonical(tampered)
        )
        refused = self.run_cli(
                "register-child", "--client-request-id", "run-a:task:101",
                "--run-id", "run-a",
                "--selection-fingerprint", selection["selection_fingerprint"],
                "--child-project-id", "101", "--card", "1", "--title", "Card 1",
                "--thread-id", "child-thread-a", "--host-id", "host-a",
                "--assignment-id", tampered["assignment_id"],
                "--assignment-fingerprint", tampered["assignment_input_fingerprint"],
                "--assignment-json", json.dumps(tampered),
                "--op-id", "register-tampered-seed", expect=4,
            )[1]
        self.assertEqual(refused["status"], "task_intent_identity_mismatch")
        self.assertIn("assignment_fingerprint", refused["mismatches"])

    def test_coordinator_close_refuses_an_in_flight_call_permit(self):
        self.create_run()
        grant = self.run_cli(
            "grant-run-lease", "--run-id", "run-a", "--lease-id", "acq-call",
            "--phase", "acquisition", "--op-id", "acq-call:grant", expect=0,
        )[1]
        generation = grant["lease_generation"]
        exact = [
            "--run-id", "run-a", "--lease-id", "acq-call",
            "--lease-generation", str(generation), "--phase", "acquisition",
            "--thread-id", "coord-thread-a", "--host-id", "host-a",
        ]
        self.run_cli(
            "record-run-snapshot", *exact, "--checkpoint-status", "ready",
            "--data-json", json.dumps(acquisition_checkpoint(100, ONE_SELECTION)),
            "--op-id", "acq-call:snapshot", expect=0,
        )
        self.run_cli(
            "begin-run-call", *exact, "--call-id", "coordinator-call-1",
            "--op-id", "coordinator-call-1:begin", expect=0,
        )
        close_args = [
            "close-run-lease", "--run-id", "run-a", "--lease-id", "acq-call",
            "--lease-generation", str(generation), "--phase", "acquisition",
            "--coordinator-thread-id", "coord-thread-a",
            "--coordinator-host-id", "host-a", "--action", "release",
            "--checkpoint-status", "ready", "--outstanding-job-ids-json", "[]",
            "--reason", "safe", "--op-id", "acq-call:close-while-in-flight",
        ]
        refused = self.run_cli(*close_args, expect=5)[1]
        self.assertEqual(refused["status"], "valmera_call_in_flight")
        self.run_cli(
            "end-run-call", *exact, "--call-id", "coordinator-call-1",
            "--outstanding-job-ids-json", "[]",
            "--op-id", "coordinator-call-1:end", expect=0,
        )
        close_args[-1] = "acq-call:close-after-end"
        self.run_cli(*close_args, expect=0)

    def test_child_close_refuses_an_in_flight_call_permit(self):
        self.create_bound_run()
        self.register_child()
        grant = self.grant("edit-call")
        generation = grant["lease_generation"]
        exact = self.auth_args("edit-call", generation)
        self.record_child("edit-call", generation, "paused_safe")
        self.run_cli(
            "begin-call", *exact, "--call-id", "child-call-1",
            "--op-id", "child-call-1:begin", expect=0,
        )
        refused = self.close(
            "edit-call", generation, "paused_safe", "freeze",
            "edit-call:close-while-in-flight", expect=5,
        )
        self.assertEqual(refused["status"], "valmera_call_in_flight")
        self.run_cli(
            "end-call", *exact, "--call-id", "child-call-1",
            "--outstanding-job-ids-json", "[]",
            "--op-id", "child-call-1:end", expect=0,
        )
        self.close(
            "edit-call", generation, "paused_safe", "freeze",
            "edit-call:close-after-end", expect=0,
        )

    def test_abstained_is_terminal_and_cannot_reopen(self):
        empty = selection_payload([])
        self.create_bound_run(selection=empty)
        self.record_coordinator_result()
        self.run_cli(
            "finalize-run", "--run-id", "run-a",
            "--coordinator-thread-id", "coord-thread-a",
            "--coordinator-host-id", "host-a", "--status", "abstained",
            "--op-id", "finalize-abstained", expect=0,
        )
        result = self.run_cli(
            "finalize-run", "--run-id", "run-a",
            "--coordinator-thread-id", "coord-thread-a",
            "--coordinator-host-id", "host-a", "--status", "paused_safe",
            "--op-id", "reopen-abstained", expect=5,
        )[1]
        self.assertEqual(result["status"], "terminal_run_immutable")

    def test_blocked_before_selection_persists_exact_terminal_artifact(self):
        self.create_run()
        reason = "Source indexing failed before selection."
        evidence = ["index_job=job-7 status=failed"]
        self.run_phase(
            "run-a", "acquisition",
            {"reason": reason, "evidence": evidence},
            checkpoint="blocked",
        )
        result = contract_fixture("coordinator_result")
        result.update({
            "run_id": "run-a",
            "topic": "topic for run-a",
            "parent_project_id": None,
            "source_youtube_video_id": None,
            "source_asset_id": None,
            "source_sha256": None,
            "reference_youtube_video_id": None,
            "reference_asset_id": None,
            "reference_sha256": None,
            "selection_fingerprint": None,
            "status": "blocked_before_selection",
            "abstained": False,
            "abstain_reason": None,
            "blocked_phase": "acquisition",
            "blocked_reason": reason,
            "blocked_evidence": evidence,
            "selected_arc_count": 0,
            "accounted_arc_count": 0,
            "generated_count": 0,
            "pending_count": 0,
            "failed_generation_count": 0,
            "ready_count": 0,
            "blocked_count": 0,
            "arc_accounting": [],
        })
        self.record_coordinator_result(result=result)
        replacement = copy.deepcopy(result)
        replacement["blocked_reason"] = "replacement"
        self.assertEqual(
            self.record_coordinator_result(
                result=replacement, expect=4, op="replace-preselection-result"
            )["status"],
            "run_result_conflict",
        )
        self.run_cli(
            "finalize-run", "--run-id", "run-a",
            "--coordinator-thread-id", "coord-thread-a",
            "--coordinator-host-id", "host-a", "--status", "blocked",
            "--op-id", "finalize-preselection-block", expect=0,
        )

    def test_reference_phase_block_can_finalize_without_invented_reference_id(self):
        self.create_run()
        self.run_phase(
            "run-a", "acquisition", acquisition_checkpoint(100, ONE_SELECTION)
        )
        selection = copy.deepcopy(ONE_SELECTION)
        self.run_phase("run-a", "selection", selection)
        self.run_cli(
            "bind-run", "--run-id", "run-a", "--parent-project-id", "100",
            "--selection-fingerprint", selection["selection_fingerprint"],
            "--op-id", "bind-reference-block", expect=0,
        )
        reason = "No truthful short-form reference could be acquired."
        evidence = ["youtube_candidates=3 role_verified=0"]
        self.run_phase(
            "run-a", "reference",
            {
                "reason": reason,
                "evidence": evidence,
                "reference_youtube_video_id": None,
                "reference_asset_id": None,
                "reference_sha256": None,
            },
            checkpoint="blocked",
        )
        clip = selection["clips"][0]
        result = contract_fixture("coordinator_result")
        result.update({
            "run_id": "run-a",
            "topic": "topic for run-a",
            "parent_project_id": 100,
            "source_youtube_video_id": selection["source_youtube_video_id"],
            "source_asset_id": 400,
            "source_sha256": selection["source_sha256"],
            "reference_youtube_video_id": None,
            "reference_asset_id": None,
            "reference_sha256": None,
            "selection_fingerprint": selection["selection_fingerprint"],
            "status": "blocked",
            "abstained": False,
            "abstain_reason": None,
            "blocked_phase": "reference",
            "blocked_reason": reason,
            "blocked_evidence": evidence,
            "selected_arc_count": 1,
            "accounted_arc_count": 1,
            "generated_count": 0,
            "pending_count": 1,
            "failed_generation_count": 0,
            "ready_count": 0,
            "blocked_count": 0,
            "arc_accounting": [{
                "arc_id": "run-a:arc:1",
                "selection_rank": 1,
                "start_s": clip["start"],
                "end_s": clip["end"],
                "title": clip["title"],
                "generation_status": "pending",
                "assignment_input_fingerprint": None,
                "editor_result_fingerprint": None,
                "parent_qc_fingerprint": None,
                "child_project_id": None,
                "generation_job_id": None,
                "generation_failure": None,
                "editor_status": "not_started",
                "parent_qc_status": "not_run",
                "live_edl_version": None,
                "preview_edl_version": None,
                "treatment_name": None,
                "reference_adaptation_summary": None,
                "failed_gates": [],
            }],
        })
        self.record_coordinator_result(result=result)
        self.run_cli(
            "finalize-run", "--run-id", "run-a",
            "--coordinator-thread-id", "coord-thread-a",
            "--coordinator-host-id", "host-a", "--status", "blocked",
            "--op-id", "finalize-reference-block", expect=0,
        )

    def test_failed_materialization_cannot_finalize_ready(self):
        selection = selection_payload([
            {"story_id": "story-one", "card": 1, "title": "Card 1"},
            {"story_id": "story-two", "card": 2, "title": "Card 2"},
        ])
        entries = selection_entries(selection)
        materialization = [
            {**entries[0], "status": "materialized", "child_project_id": 101},
            {
                **entries[1],
                "status": "failed",
                "child_project_id": None,
                "seeded_child_start_s": None,
                "seeded_child_end_s": None,
                "seed_snap_reason": None,
                "seed_range_verified_by": None,
                "seed_range_evidence_digest": None,
                "generation_failure": "Valmera did not create the child project.",
            },
        ]
        self.create_bound_run(selection=selection, materialization=materialization)
        self.register_child()
        self.finish_pass("run-a", 101, "assign-a", ASSIGNMENT_A,
                         "edit-only", "child-thread-a", "host-a")
        common = [
            "--run-id", "run-a", "--coordinator-thread-id", "coord-thread-a",
            "--coordinator-host-id", "host-a",
        ]
        self.assertEqual(
            self.run_cli(
                "finalize-run", *common, "--status", "ready",
                "--op-id", "bad-ready", expect=5,
            )[1]["status"],
            "coordinator_run_result_required",
        )
        self.record_coordinator_result()
        self.assertEqual(
            self.run_cli(
                "finalize-run", *common, "--status", "ready",
                "--op-id", "bad-ready-after-result", expect=5,
            )[1]["status"],
            "coordinator_result_status_mismatch",
        )
        self.run_cli(
            "finalize-run", *common, "--status", "blocked",
            "--op-id", "good-blocked", expect=0,
        )

    def test_failed_materialization_cannot_mask_pending_child_qc(self):
        selection = selection_payload([
            {"story_id": "story-one", "card": 1, "title": "Card 1"},
            {"story_id": "story-two", "card": 2, "title": "Card 2"},
        ])
        entries = selection_entries(selection)
        materialization = [
            {**entries[0], "status": "materialized", "child_project_id": 101},
            {
                **entries[1],
                "status": "failed",
                "child_project_id": None,
                "seeded_child_start_s": None,
                "seeded_child_end_s": None,
                "seed_snap_reason": None,
                "seed_range_verified_by": None,
                "seed_range_evidence_digest": None,
                "generation_failure": "Valmera did not create the child project.",
            },
        ]
        self.create_bound_run(selection=selection, materialization=materialization)
        assignment = self.register_child(
            assignment_status="blocked_before_mutation"
        )

        premature = self.coordinator_result()
        self.assertEqual(premature["status"], "in_progress")
        premature.update({
            "status": "blocked",
            "blocked_phase": "materialization",
            "blocked_reason": "All selected stories failed materialization.",
            "blocked_evidence": ["card=2 generation_status=failed"],
        })
        refused = self.record_coordinator_result(
            result=premature,
            expect=2,
            op="premature-terminal-with-pending-qc",
        )
        # The normative contract may reject the terminal/in-progress row before
        # the registry's independent durable-state derivation gets to reject it.
        # Both layers must fail closed, and neither may persist the result.
        self.assertTrue(
            "expected in_progress" in refused["error"]
            or "invalid coordinator-run-result artifact" in refused["error"],
            refused["error"],
        )
        snapshot = self.run_cli("resume", "--run-id", "run-a", expect=0)[1]
        self.assertIsNone(snapshot["run"]["coordinator_result"])

        self.run_cli(
            "block-child-before-mutation", "--run-id", "run-a",
            "--child-project-id", "101",
            "--coordinator-thread-id", "coord-thread-a",
            "--coordinator-host-id", "host-a",
            "--reason", assignment["blocked_reason"],
            "--op-id", "mixed-generation-terminal-child-block", expect=0,
        )
        terminal = self.coordinator_result()
        self.assertEqual(terminal["status"], "blocked")
        self.assertEqual(terminal["blocked_phase"], "child_qc")
        self.assertEqual(terminal["failed_generation_count"], 1)
        self.assertEqual(terminal["blocked_count"], 1)
        self.record_coordinator_result(
            result=terminal,
            op="mixed-generation-child-qc-terminal-result",
        )

    def test_multiprocess_grant_has_exactly_one_global_winner(self):
        self.create_bound_run("run-a", 100)
        self.register_child()
        self.create_bound_run("run-b", 200)
        self.register_child(
            "run-b", 201, 1, "child-thread-b", "host-b", "assign-b", ASSIGNMENT_B
        )
        assign_a, fingerprint_a = self.assignment_identity(
            "run-a", 101, "assign-a", ASSIGNMENT_A
        )
        assign_b, fingerprint_b = self.assignment_identity(
            "run-b", 201, "assign-b", ASSIGNMENT_B
        )
        context = multiprocessing.get_context("spawn")
        start, queue = context.Event(), context.Queue()
        processes = [
            context.Process(
                target=_race_grant,
                args=(str(self.registry), "run-a", 101, "race-a", assign_a,
                      fingerprint_a,
                      self.run_cli("resume", "--run-id", "run-a", expect=0)[1]["run"]["selection_fingerprint"],
                      start, queue),
            ),
            context.Process(
                target=_race_grant,
                args=(str(self.registry), "run-b", 201, "race-b", assign_b,
                      fingerprint_b,
                      self.run_cli("resume", "--run-id", "run-b", expect=0)[1]["run"]["selection_fingerprint"],
                      start, queue),
            ),
        ]
        for process in processes:
            process.start()
        start.set()
        results = [queue.get(timeout=20) for _ in processes]
        for process in processes:
            process.join(timeout=20)
            self.assertEqual(process.exitcode, 0)
        self.assertEqual(sorted(code for code, _ in results), [0, 4])
        self.assertEqual(next(payload for code, payload in results if code == 4)["status"],
                         "global_lease_busy")


if __name__ == "__main__":
    unittest.main()
