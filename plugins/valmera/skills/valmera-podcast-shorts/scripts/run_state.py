#!/usr/bin/env python3
"""Small durable state machine for valmera-podcast-shorts v7.

This records production outcomes and outstanding Valmera jobs. It deliberately
does not issue per-call permits, expire leases, poll, or schedule work.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


VERSION = "valmera-podcast-shorts-v7"
MAX_EDITORS = 3
STAGES = [
    "created", "taste", "source", "selection", "materialization",
    "editing", "qc", "exporting", "complete",
]
TERMINAL = {"exported", "needs_user_review", "failed_technical"}
ACTIVE = {"editing"}
CHILD_STATES = {
    "queued", "editing", "candidate", "repair", "ready", "exporting",
    *TERMINAL,
}
STYLE_LANE_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
HEX_COLOR_RE = re.compile(r"#[0-9A-Fa-f]{6}\Z")
EPSILON = 0.02


class StateError(RuntimeError):
    pass


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def absolute_existing(path: str, label: str) -> Path:
    value = Path(path).expanduser().resolve()
    if not value.is_file():
        raise StateError(f"{label} does not exist: {value}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def state_path(run_dir: str) -> Path:
    return Path(run_dir).expanduser().resolve() / "run.json"


def write_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".run-", suffix=".json",
                                     dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextlib.contextmanager
def locked(run_dir: str, *, write: bool = True):
    directory = Path(run_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / ".run.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        path = directory / "run.json"
        if not path.is_file():
            raise StateError(f"run is not initialized: {directory}")
        with path.open(encoding="utf-8") as handle:
            state = json.load(handle)
        if state.get("version") != VERSION:
            raise StateError(
                f"expected {VERSION}, found {state.get('version')!r}")
        yield state
        if write:
            state["updated_at"] = now()
            write_atomic(path, state)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def child(state: dict, short_id: str) -> dict:
    try:
        value = state["shorts"][short_id]
    except KeyError as exc:
        raise StateError(f"unknown short_id: {short_id}") from exc
    if value.get("status") not in CHILD_STATES:
        raise StateError(f"invalid child status for {short_id}")
    return value


def assignment_style_lane(assignment: Path) -> str:
    try:
        with assignment.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StateError("assignment must be a readable JSON object") from exc
    lane = payload.get("style_lane") if isinstance(payload, dict) else None
    if not isinstance(lane, str) or not STYLE_LANE_RE.fullmatch(lane):
        raise StateError("assignment style_lane must be a lowercase slug")
    return lane


def finite_number(value: object, label: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StateError(f"{label} must be a number")
    number = float(value)
    if not math.isfinite(number) or number < minimum:
        raise StateError(f"{label} must be at least {minimum:g}")
    return number


def dimensions(value: object, label: str) -> list[int]:
    if not isinstance(value, list) or len(value) != 2 or any(
            isinstance(x, bool) or not isinstance(x, int) or x < 2 for x in value):
        raise StateError(f"{label} must contain two positive integer dimensions")
    return value


def read_object(path: Path, label: str) -> dict:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise StateError(f"{label} must be an object")
    return payload


def quality_policy(path: Path) -> dict:
    value = read_object(path, "quality policy")
    if value.get("version") != "delivery-quality-v1":
        raise StateError("quality policy version must be delivery-quality-v1")
    floor = dimensions(value.get("min_final_dimensions"), "minimum final")
    target = dimensions(value.get("target_final_dimensions"), "target final")
    if any(a > b for a, b in zip(floor, target)):
        raise StateError("minimum final cannot exceed target final")
    finite_number(value.get("min_native_short_edge"),
                  "min_native_short_edge", minimum=2)
    finite_number(value.get("max_picture_upscale"),
                  "max_picture_upscale", minimum=1)
    return value


def video_dimensions(path: Path) -> list[int]:
    try:
        result = subprocess.run([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "json", str(path),
        ], capture_output=True, text=True, timeout=60, check=True)
        streams = json.loads(result.stdout).get("streams", [])
        if not streams:
            raise StateError(f"no video stream in {path}")
        return dimensions([streams[0].get("width"), streams[0].get("height")],
                          "probed video")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise StateError(f"cannot probe video {path}: {exc}") from exc


def measure_quality(policy: dict, evidence: dict,
                    final: Path | None = None) -> dict:
    """Check genuine-source/crop budgets; file dimensions alone cannot prove detail."""
    source = absolute_existing(evidence.get("source_path") or "", "quality source")
    digest = sha256(source)
    if evidence.get("source_sha256") != digest:
        raise StateError("quality source checksum does not match")
    original = dimensions(evidence.get("native_dimensions"), "native source")
    probed = video_dimensions(source)
    if any(a > b for a, b in zip(original, probed)):
        raise StateError("native dimensions cannot exceed the probed source")
    provenance = absolute_existing(evidence.get("acquisition_record") or "",
                                   "source acquisition record")
    expected = dimensions(evidence.get("expected_final_dimensions"),
                          "expected final")
    actual = video_dimensions(final) if final else expected
    violations = []
    floor = policy["min_final_dimensions"]
    if any(a < b for a, b in zip(actual, floor)):
        violations.append("final_below_minimum")
    if final and actual != expected:
        violations.append("final_differs_from_expected")
    if min(original) < policy["min_native_short_edge"]:
        violations.append("native_source_below_minimum")
    if min(expected) > min(original) or max(expected) > max(original):
        violations.append("expected_canvas_exceeds_native_source")
    regions = evidence.get("picture_regions")
    if not isinstance(regions, list) or not regions:
        raise StateError("quality evidence needs picture_regions")
    measured = []
    for index, region in enumerate(regions):
        if not isinstance(region, dict):
            raise StateError("picture region must be an object")
        crop, output = region.get("source_crop_native"), region.get("output_rect")
        for label, rect, canvas in (("source crop", crop, original),
                                    ("output rectangle", output, expected)):
            if not isinstance(rect, list) or len(rect) != 4:
                raise StateError(f"{label} must be [x, y, width, height]")
            for i, number in enumerate(rect):
                finite_number(number, label, minimum=0 if i < 2 else 1)
            if any(rect[i] + rect[i + 2] > canvas[i] + EPSILON for i in (0, 1)):
                raise StateError(f"{label} extends outside its native canvas")
        upscale = max(output[2] / crop[2], output[3] / crop[3])
        if upscale > policy["max_picture_upscale"] + 1e-6:
            violations.append(f"picture_region_{index}_exceeds_native_detail")
        measured.append({"source_crop_native": crop, "output_rect": output,
                         "upscale": round(upscale, 6)})
    return {
        "verdict": "fail" if violations else "pass", "violations": violations,
        "source_path": str(source), "source_sha256": digest,
        "source_dimensions": probed, "native_dimensions": original,
        "acquisition_record": str(provenance),
        "expected_final_dimensions": expected, "measured_dimensions": actual,
        "picture_regions": measured,
        "final_path": str(final) if final else None,
        "final_sha256": sha256(final) if final else None,
    }


def run_quality_policy(state: dict) -> dict | None:
    record = state.get("quality_policy")
    if not record:
        return None  # Interrupted/historic runs do not acquire new requirements.
    path = absolute_existing(record["file"], "quality policy")
    if sha256(path) != record["sha256"]:
        raise StateError("run quality policy changed after initialization")
    return quality_policy(path)


def require_quality_pass(report: dict) -> None:
    if report["verdict"] != "pass":
        raise StateError("delivery quality blocked: " + ", ".join(report["violations"]))


def quality_review(path: Path, final_digest: str | None = None) -> dict:
    review = read_object(path, "quality review")
    for key in ("source_detail", "typography", "stable_word_size",
                "active_word_color", "reference_comparison"):
        if review.get(key) != "pass":
            raise StateError(f"delivery quality review requires {key} pass")
    if not isinstance(review.get("observations"), str) or not review["observations"].strip():
        raise StateError("quality review needs concrete visual observations")
    files = review.get("evidence_files")
    if not isinstance(files, list) or not files:
        raise StateError("quality review needs full-size visual evidence files")
    for file in files:
        absolute_existing(file, "quality review evidence")
    if final_digest is not None and review.get("final_sha256") != final_digest:
        raise StateError("quality review must bind the exact final checksum")
    return {"file": str(path), "sha256": sha256(path)}


def cmd_quality_check(args: argparse.Namespace) -> dict:
    policy = quality_policy(absolute_existing(args.policy, "quality policy"))
    evidence = read_object(absolute_existing(args.evidence, "quality evidence"),
                           "quality evidence")
    final = absolute_existing(args.final, "final") if args.final else None
    report = measure_quality(policy, evidence, final)
    if args.output:
        write_atomic(Path(args.output).expanduser().resolve(), report)
    return report


def validate_candidate_contract(payload: dict, style_lane: str) -> None:
    """Reject v7 candidates that omit the user's measurable edit rules."""
    editorial_duration = finite_number(
        payload.get("editorial_duration_s"),
        "candidate editorial_duration_s", minimum=EPSILON)

    captions = payload.get("caption_treatment")
    if not isinstance(captions, dict):
        raise StateError("candidate caption_treatment must be an object")
    if captions.get("spoken_word_highlighting") is not True:
        raise StateError("candidate captions must highlight the spoken word")
    max_words = captions.get("max_words_visible")
    if isinstance(max_words, bool) or not isinstance(max_words, int) \
            or not 1 <= max_words <= 4:
        raise StateError(
            "candidate captions may show at most four words at once")
    if not isinstance(captions.get("active_word_color"), str) \
            or not HEX_COLOR_RE.fullmatch(captions["active_word_color"]):
        raise StateError("candidate active_word_color must be #RRGGBB")
    if captions.get("rendered_active_word_check") != "pass":
        raise StateError(
            "candidate must pass a rendered active-word color check")

    shots = payload.get("broll_shots")
    if not isinstance(shots, list):
        raise StateError("candidate broll_shots must be a list")
    if payload.get("duplicate_broll_within_short") is not False:
        raise StateError(
            "candidate must declare no duplicate B-roll within the short")
    if style_lane == "hook-to-silent-montage" and not shots:
        raise StateError("hook-to-silent-montage requires B-roll shots")

    source_windows: dict[str, list[tuple[float, float]]] = {}
    for index, shot in enumerate(shots):
        label = f"candidate broll_shots[{index}]"
        if not isinstance(shot, dict):
            raise StateError(f"{label} must be an object")
        asset_key = shot.get("asset_key")
        if not isinstance(asset_key, str) or not asset_key.strip():
            raise StateError(f"{label}.asset_key must be non-empty")
        source_start = finite_number(
            shot.get("source_start_s"), f"{label}.source_start_s")
        source_end = finite_number(
            shot.get("source_end_s"), f"{label}.source_end_s")
        output_start = finite_number(
            shot.get("output_start_s"), f"{label}.output_start_s")
        output_end = finite_number(
            shot.get("output_end_s"), f"{label}.output_end_s")
        if source_end < source_start:
            raise StateError(f"{label} source window is reversed")
        if output_end <= output_start:
            raise StateError(f"{label} output window must have duration")
        if output_end > editorial_duration + EPSILON:
            raise StateError(f"{label} extends past editorial duration")
        for prior_start, prior_end in source_windows.setdefault(
                asset_key.strip(), []):
            both_stills = source_start == source_end == prior_start == prior_end
            overlap = min(source_end, prior_end) - max(source_start, prior_start)
            if both_stills or overlap > EPSILON:
                raise StateError(
                    f"candidate repeats B-roll source material for {asset_key}")
        source_windows[asset_key.strip()].append((source_start, source_end))

    if style_lane == "hook-to-silent-montage":
        if editorial_duration > 25.0 + EPSILON:
            raise StateError(
                "hook-to-silent-montage editorial duration exceeds 25 seconds")
        montage = payload.get("montage_timing")
        if not isinstance(montage, dict):
            raise StateError("hook-to-silent-montage requires montage_timing")
        start = finite_number(montage.get("start_s"), "montage start_s")
        end = finite_number(montage.get("end_s"), "montage end_s")
        duration = finite_number(
            montage.get("duration_s"), "montage duration_s", minimum=EPSILON)
        if end <= start or not math.isclose(
                end - start, duration, abs_tol=EPSILON):
            raise StateError("montage timing is internally inconsistent")
        if end > editorial_duration + EPSILON:
            raise StateError("montage extends past editorial duration")
        if duration > 15.0 + EPSILON:
            raise StateError("hook-to-silent-montage exceeds 15 seconds")


def require_status(item: dict, allowed: set[str], action: str) -> None:
    if item["status"] not in allowed:
        wanted = ", ".join(sorted(allowed))
        raise StateError(
            f"cannot {action} from {item['status']}; expected {wanted}")


def cmd_init(args: argparse.Namespace) -> dict:
    directory = Path(args.run_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("source", "taste", "assignments", "candidates", "exports"):
        (directory / name).mkdir(exist_ok=True)
    path = directory / "run.json"
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            existing = json.load(handle)
        if existing.get("version") == VERSION and \
                existing.get("run_id") == args.run_id:
            if args.quality_policy:
                requested = absolute_existing(args.quality_policy, "quality policy")
                if (existing.get("quality_policy") or {}).get("sha256") != sha256(requested):
                    raise StateError("existing run has a different or absent quality policy; use a new run")
            return existing
        raise StateError(f"run.json already exists at {path}")
    stamp = now()
    value = {
        "version": VERSION,
        "run_id": args.run_id,
        "source": args.source,
        "stage": "created",
        "max_editors": MAX_EDITORS,
        "created_at": stamp,
        "updated_at": stamp,
        "taste_profile": None,
        "shorts": {},
        "events": [{"at": stamp, "type": "run_initialized"}],
    }
    if args.quality_policy:
        policy_file = absolute_existing(args.quality_policy, "quality policy")
        quality_policy(policy_file)
        value["quality_policy"] = {
            "file": str(policy_file), "sha256": sha256(policy_file)}
    write_atomic(path, value)
    return value


def cmd_taste(args: argparse.Namespace) -> dict:
    profile = absolute_existing(args.profile, "taste profile")
    with profile.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("version") != "taste-profile-v1":
        raise StateError("taste profile version must be taste-profile-v1")
    if payload.get("approval") not in {"user", "autopilot"}:
        raise StateError("taste profile approval must be user or autopilot")
    lanes = payload.get("style_lanes")
    if not isinstance(lanes, list) or not 2 <= len(lanes) <= 5:
        raise StateError("taste profile must contain two to five style lanes")
    record = {"file": str(profile), "sha256": sha256(profile),
              "approval": payload["approval"], "recorded_at": now()}
    with locked(args.run_dir) as state:
        existing = state.get("taste_profile")
        if existing and existing.get("sha256") != record["sha256"] and \
                not args.replace:
            raise StateError(
                "run already has a different taste profile; use --replace "
                "only after explicit user revision")
        state["taste_profile"] = record
        state["events"].append({"at": now(), "type": "taste_profile",
                                "sha256": record["sha256"]})
        return record


def cmd_phase(args: argparse.Namespace) -> dict:
    with locked(args.run_dir) as state:
        current = STAGES.index(state["stage"])
        target = STAGES.index(args.stage)
        if target < current or target > current + 1:
            raise StateError(
                f"phase must advance one step: {state['stage']} -> {args.stage}")
        state["stage"] = args.stage
        state["events"].append({"at": now(), "type": "phase",
                                "stage": args.stage})
        return state


def cmd_add_short(args: argparse.Namespace) -> dict:
    assignment = absolute_existing(args.assignment, "assignment")
    style_lane = assignment_style_lane(assignment)
    with locked(args.run_dir) as state:
        if args.short_id in state["shorts"]:
            existing = state["shorts"][args.short_id]
            if int(existing["child_project_id"]) == args.project_id:
                if existing.get("style_lane") not in (None, style_lane) and \
                        existing["status"] != "queued":
                    raise StateError(
                        "style_lane cannot change after editing begins")
                if existing.get("style_lane") != style_lane:
                    state["events"].append({
                        "at": now(), "type": "short_style_reassigned",
                        "short_id": args.short_id,
                        "style_lane": style_lane,
                    })
                existing["style_lane"] = style_lane
                existing["assignment"] = str(assignment)
                existing["updated_at"] = now()
                return existing
            raise StateError(f"short_id already maps to another project")
        if any(int(item["child_project_id"]) == args.project_id
               for item in state["shorts"].values()):
            raise StateError("child project is already assigned to a short")
        item = {
            "short_id": args.short_id,
            "child_project_id": args.project_id,
            "title": args.title,
            "style_lane": style_lane,
            "assignment": str(assignment),
            "status": "queued",
            "worker": None,
            "edit_round": 0,
            "repair_rounds": 0,
            "rescue_used": False,
            "jobs": [],
            "candidate": None,
            "qc": None,
            "export": None,
            "updated_at": now(),
        }
        state["shorts"][args.short_id] = item
        state["events"].append({"at": now(), "type": "short_added",
                                "short_id": args.short_id})
        return item


def cmd_claim(args: argparse.Namespace) -> dict:
    with locked(args.run_dir) as state:
        item = child(state, args.short_id)
        require_status(item, {"queued", "repair"}, "claim")
        if run_quality_policy(state) and not state.get("quality_pilot"):
            pilot_id = state.get("quality_pilot_short_id")
            if pilot_id and pilot_id != args.short_id and \
                    state["shorts"][pilot_id]["status"] not in TERMINAL:
                raise StateError("verify the first actual final before batch editing")
            state["quality_pilot_short_id"] = args.short_id
        active = sum(1 for value in state["shorts"].values()
                     if value["status"] in ACTIVE)
        if active >= int(state.get("max_editors") or MAX_EDITORS):
            raise StateError(f"editor limit reached ({active}/{MAX_EDITORS})")
        item["status"] = "editing"
        item["worker"] = args.worker
        item["edit_round"] += 1
        item["updated_at"] = now()
        state["events"].append({"at": now(), "type": "short_claimed",
                                "short_id": args.short_id,
                                "worker": args.worker})
        return item


def cmd_job(args: argparse.Namespace) -> dict:
    with locked(args.run_dir) as state:
        item = child(state, args.short_id)
        match = next((job for job in item["jobs"]
                      if int(job["job_id"]) == args.job_id), None)
        if match is None:
            match = {"job_id": args.job_id, "kind": args.kind,
                     "state": args.state, "updated_at": now()}
            item["jobs"].append(match)
        else:
            if match["kind"] != args.kind:
                raise StateError("job kind cannot change")
            match.update(state=args.state, updated_at=now())
        item["updated_at"] = now()
        return match


def cmd_candidate(args: argparse.Namespace) -> dict:
    bundle = absolute_existing(args.bundle, "candidate bundle")
    preview = absolute_existing(args.preview, "preview")
    with bundle.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    with locked(args.run_dir) as state:
        item = child(state, args.short_id)
        require_status(item, {"editing"}, "record candidate")
        if item.get("worker") != args.worker:
            raise StateError("candidate worker does not own this short")
        expected = {
            "run_id": state["run_id"],
            "short_id": args.short_id,
            "child_project_id": item["child_project_id"],
            "edl_version": args.edl_version,
        }
        if item.get("style_lane"):
            expected["style_lane"] = item["style_lane"]
        for key, value in expected.items():
            if payload.get(key) != value:
                raise StateError(f"candidate bundle {key} does not match")
        validate_candidate_contract(payload, item["style_lane"])
        declared_path = Path(payload.get("preview_path") or "").expanduser()
        if declared_path.resolve() != preview:
            raise StateError("candidate bundle preview_path does not match")
        if payload.get("outstanding_job_ids"):
            raise StateError("candidate bundle has outstanding Valmera jobs")
        if any(job["state"] in {"queued", "running"}
               for job in item["jobs"]):
            raise StateError("candidate has outstanding Valmera jobs")
        digest = sha256(preview)
        declared = payload.get("preview_sha256")
        if declared and declared != digest:
            raise StateError("preview checksum does not match candidate bundle")
        quality = None
        policy = run_quality_policy(state)
        if policy:
            evidence_file = absolute_existing(
                payload.get("quality_evidence") or "", "quality evidence")
            evidence = read_object(evidence_file, "quality evidence")
            measured = measure_quality(policy, evidence)
            require_quality_pass(measured)
            captions = payload["caption_treatment"]
            if captions.get("animation") != "none" or \
                    captions.get("emphasis") != "none" or \
                    captions.get("rendered_stable_size_check") != "pass":
                raise StateError("quality captions require stable size and explicit no animation/emphasis")
            quality = {"evidence_file": str(evidence_file),
                       "evidence_sha256": sha256(evidence_file),
                       "measurement": measured}
        item["candidate"] = {
            "bundle": str(bundle), "preview": str(preview),
            "sha256": digest, "edl_version": args.edl_version,
            "recorded_at": now(),
        }
        if quality:
            item["candidate"]["quality"] = quality
        item["status"] = "candidate"
        item["worker"] = None
        item["updated_at"] = now()
        return item


def cmd_qc(args: argparse.Namespace) -> dict:
    report = absolute_existing(args.report, "QC report")
    with report.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    with locked(args.run_dir) as state:
        item = child(state, args.short_id)
        require_status(item, {"candidate"}, "record QC")
        expected = {
            "run_id": state["run_id"],
            "short_id": args.short_id,
            "child_project_id": item["child_project_id"],
            "edl_version": item["candidate"]["edl_version"],
            "verdict": args.verdict,
        }
        if item.get("style_lane"):
            expected["style_lane"] = item["style_lane"]
        for key, value in expected.items():
            if payload.get(key) != value:
                raise StateError(f"QC report {key} does not match")
        if float(payload.get("score", -1)) != args.score:
            raise StateError("QC report score does not match")
        if args.verdict == "ready" and args.score < 90:
            raise StateError("ready requires a QC score of at least 90")
        if args.verdict == "ready":
            if payload.get("active_word_caption_check") != "pass":
                raise StateError(
                    "ready requires an active-word caption QC pass")
            if payload.get("within_short_broll_uniqueness_check") != "pass":
                raise StateError(
                    "ready requires a within-short B-roll uniqueness QC pass")
            if item.get("style_lane") == "hook-to-silent-montage" and \
                    payload.get("style2_timing_check") != "pass":
                raise StateError("ready Style 2 requires a timing QC pass")
            if run_quality_policy(state):
                quality_review(absolute_existing(
                    payload.get("delivery_quality_review") or "", "quality review"))
        if args.verdict == "repair" and item["repair_rounds"] >= 2:
            raise StateError("two editor repair rounds are already used")
        item["qc"] = {
            "report": str(report), "score": args.score,
            "verdict": args.verdict, "recorded_at": now(),
        }
        if args.verdict == "repair":
            item["repair_rounds"] += 1
        item["status"] = args.verdict
        item["updated_at"] = now()
        return item


def cmd_rescue(args: argparse.Namespace) -> dict:
    with locked(args.run_dir) as state:
        item = child(state, args.short_id)
        require_status(item, {"candidate"}, "start coordinator rescue")
        if item.get("rescue_used"):
            raise StateError("coordinator rescue was already used")
        item["rescue_used"] = True
        item["status"] = "editing"
        item["worker"] = "coordinator"
        item["edit_round"] += 1
        item["updated_at"] = now()
        state["events"].append({"at": now(), "type": "rescue_started",
                                "short_id": args.short_id})
        return item


def cmd_exception(args: argparse.Namespace) -> dict:
    report = absolute_existing(args.report, "exception report")
    with locked(args.run_dir) as state:
        item = child(state, args.short_id)
        if item["status"] in TERMINAL:
            raise StateError("short is already terminal")
        item["status"] = args.status
        item["worker"] = None
        item["qc"] = {
            **(item.get("qc") or {}), "report": str(report),
            "verdict": args.status, "recorded_at": now(),
        }
        item["updated_at"] = now()
        state["events"].append({"at": now(), "type": "exception",
                                "short_id": args.short_id,
                                "status": args.status})
        return item


def cmd_export_start(args: argparse.Namespace) -> dict:
    with locked(args.run_dir) as state:
        item = child(state, args.short_id)
        require_status(item, {"ready"}, "start export")
        item["status"] = "exporting"
        item["export"] = {"job_id": args.job_id, "state": "running",
                          "started_at": now()}
        item["updated_at"] = now()
        return item


def cmd_reject_final(args: argparse.Namespace) -> dict:
    """Reopen a reviewed candidate after an actual downloaded final fails QC."""
    media = absolute_existing(args.file, "rejected final")
    report = absolute_existing(args.report, "final rejection report")
    payload = read_object(report, "final rejection report")
    digest = sha256(media)
    receipt_path = absolute_existing(
        payload.get("final_receipt") or "", "final receipt")
    receipt_payload = read_object(receipt_path, "final receipt")
    structured = receipt_payload.get("structuredContent")
    receipt = (structured if isinstance(structured, dict) else {}).get(
        "download_receipt", receipt_payload.get("download_receipt", receipt_payload))
    if not isinstance(receipt, dict) or receipt.get("render_type") != "final_export":
        raise StateError("final receipt must identify a final_export")
    if receipt.get("edl_version") != args.edl_version or \
            receipt.get("sha256") != digest:
        raise StateError("final receipt EDL version or SHA does not match")
    with locked(args.run_dir) as state:
        item = child(state, args.short_id)
        require_status(item, {"ready", "exporting"}, "reject final")
        candidate = item.get("candidate") or {}
        if candidate.get("edl_version") != args.edl_version:
            raise StateError("rejected final EDL version differs from candidate")
        for key in ("project_id", "child_project_id"):
            if key in receipt and receipt[key] != item["child_project_id"]:
                raise StateError(f"final receipt {key} does not match")
        expected = {
            "run_id": state["run_id"], "short_id": args.short_id,
            "child_project_id": item["child_project_id"],
            "edl_version": args.edl_version, "verdict": "repair",
            "final_sha256": digest,
        }
        if item.get("style_lane"):
            expected["style_lane"] = item["style_lane"]
        for key, value in expected.items():
            if payload.get(key) != value:
                raise StateError(f"final rejection report {key} does not match")
        if any(job["state"] in {"queued", "running"} for job in item["jobs"]):
            raise StateError("cannot reject final with queued or running jobs")
        export = item.get("export") or {}
        if export.get("state") in {"queued", "running"} and not any(
                job["job_id"] == export.get("job_id") and
                job["state"] in {"done", "failed"} for job in item["jobs"]):
            raise StateError("record the export job as terminal before rejecting final")
        if item["repair_rounds"] >= 2:
            raise StateError("two editor repair rounds are already used")
        prior_qc_report = None
        qc_path = (item.get("qc") or {}).get("report")
        if qc_path and Path(qc_path).is_file():
            prior_qc_report = {
                "file": str(Path(qc_path).resolve()),
                "sha256": sha256(Path(qc_path)),
                "payload": read_object(Path(qc_path), "prior QC report"),
            }
        stamp = now()
        item.setdefault("rejected_finals", []).append({
            "rejected_at": stamp, "previous_status": item["status"],
            "file": str(media), "sha256": digest, "bytes": media.stat().st_size,
            "edl_version": args.edl_version,
            "candidate": candidate, "qc": item.get("qc"),
            "qc_report": prior_qc_report,
            "export": item.get("export"),
            "report": {"file": str(report), "sha256": sha256(report),
                       "payload": payload},
            "final_receipt": {"file": str(receipt_path),
                              "sha256": sha256(receipt_path),
                              "payload": receipt_payload},
        })
        item["repair_rounds"] += 1
        item.update(status="repair", worker=None, qc=None, export=None,
                    updated_at=stamp)
        state["events"].append({
            "at": stamp, "type": "final_rejected", "short_id": args.short_id,
            "edl_version": args.edl_version, "sha256": digest,
        })
        return item


def cmd_export(args: argparse.Namespace) -> dict:
    media = absolute_existing(args.file, "export file")
    with locked(args.run_dir) as state:
        item = child(state, args.short_id)
        require_status(item, {"exporting", "ready"}, "record export")
        candidate = item.get("candidate") or {}
        if int(candidate.get("edl_version") or -1) != args.edl_version:
            raise StateError("export EDL version differs from approved candidate")
        style_lane = item.get("style_lane") or assignment_style_lane(
            absolute_existing(item["assignment"], "assignment"))
        if not media.name.startswith(f"{style_lane}__") or \
                media.suffix.lower() != ".mp4":
            raise StateError(
                f"export filename must start with {style_lane}__ and end in .mp4")
        quality = None
        policy = run_quality_policy(state)
        if policy:
            record = candidate.get("quality") or {}
            evidence_file = absolute_existing(
                record.get("evidence_file") or "", "candidate quality evidence")
            if sha256(evidence_file) != record.get("evidence_sha256"):
                raise StateError("candidate quality evidence changed after review")
            quality = measure_quality(policy, read_object(
                evidence_file, "quality evidence"), media)
            require_quality_pass(quality)
            review = quality_review(absolute_existing(
                args.quality_review or "", "final quality review"),
                quality["final_sha256"])
            quality["review"] = review
            if not state.get("quality_pilot"):
                state["quality_pilot"] = {
                    "short_id": args.short_id, "edl_version": args.edl_version,
                    "sha256": quality["final_sha256"], "review": review}
        item["style_lane"] = style_lane
        item["status"] = "exported"
        item["export"] = {
            **(item.get("export") or {}),
            "state": "done", "file": str(media), "sha256": sha256(media),
            "bytes": media.stat().st_size, "duration_s": args.duration,
            "edl_version": args.edl_version, "finished_at": now(),
        }
        if quality:
            item["export"]["quality"] = quality
        item["updated_at"] = now()
        return item


def summary(state: dict) -> dict:
    counts = {name: 0 for name in sorted(CHILD_STATES)}
    outstanding = []
    for item in state["shorts"].values():
        counts[item["status"]] += 1
        for job in item["jobs"]:
            if job["state"] in {"queued", "running"}:
                outstanding.append({"short_id": item["short_id"], **job})
        export = item.get("export") or {}
        if export.get("state") == "running":
            outstanding.append({"short_id": item["short_id"],
                                "job_id": export.get("job_id"),
                                "kind": "final", "state": "running"})
    return {
        "version": state["version"], "run_id": state["run_id"],
        "stage": state["stage"], "selected": len(state["shorts"]),
        "counts": {key: value for key, value in counts.items() if value},
        "outstanding_jobs": outstanding,
    }


def cmd_status(args: argparse.Namespace) -> dict:
    with locked(args.run_dir, write=False) as state:
        return summary(state)


def cmd_finalize(args: argparse.Namespace) -> dict:
    with locked(args.run_dir) as state:
        if state["stage"] != "exporting":
            raise StateError("run must be in exporting before finalize")
        if not state["shorts"]:
            raise StateError("cannot finalize a run with no selected shorts")
        nonterminal = [item["short_id"] for item in state["shorts"].values()
                       if item["status"] not in TERMINAL]
        if nonterminal:
            raise StateError("nonterminal shorts: " + ", ".join(nonterminal))
        items, exceptions = [], []
        for item in sorted(state["shorts"].values(),
                           key=lambda value: value["short_id"]):
            if item["status"] == "exported":
                exp = item["export"]
                items.append({
                    "short_id": item["short_id"],
                    "child_project_id": item["child_project_id"],
                    "edl_version": exp["edl_version"],
                    "title": item["title"],
                    "style_lane": item["style_lane"], "file": exp["file"],
                    "sha256": exp["sha256"],
                    "duration_s": exp["duration_s"], "status": "exported",
                })
                if exp.get("quality"):
                    items[-1]["delivery_quality"] = exp["quality"]
            else:
                exceptions.append({
                    "short_id": item["short_id"],
                    "child_project_id": item["child_project_id"],
                    "title": item["title"], "status": item["status"],
                    "qc": item.get("qc"),
                })
        manifest = {
            "version": "valmera-shorts-export-v1",
            "run_id": state["run_id"], "source": state["source"],
            "generated_at": now(), "items": items,
            "exceptions": exceptions,
        }
        output = Path(args.run_dir).expanduser().resolve() / \
            "exports" / "manifest.json"
        write_atomic(output, manifest)
        state["stage"] = "complete"
        state["manifest"] = str(output)
        state["events"].append({"at": now(), "type": "finalized",
                                "exported": len(items),
                                "exceptions": len(exceptions)})
        return manifest


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)

    p = commands.add_parser("init")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--quality-policy")
    p.set_defaults(func=cmd_init)

    p = commands.add_parser("phase")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--stage", required=True, choices=STAGES[1:-1])
    p.set_defaults(func=cmd_phase)

    p = commands.add_parser("taste")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--profile", required=True)
    p.add_argument("--replace", action="store_true")
    p.set_defaults(func=cmd_taste)

    p = commands.add_parser("add-short")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--short-id", required=True)
    p.add_argument("--project-id", required=True, type=int)
    p.add_argument("--title", required=True)
    p.add_argument("--assignment", required=True)
    p.set_defaults(func=cmd_add_short)

    p = commands.add_parser("claim")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--short-id", required=True)
    p.add_argument("--worker", required=True)
    p.set_defaults(func=cmd_claim)

    p = commands.add_parser("job")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--short-id", required=True)
    p.add_argument("--job-id", required=True, type=int)
    p.add_argument("--kind", required=True)
    p.add_argument("--state", required=True,
                   choices=["queued", "running", "done", "failed"])
    p.set_defaults(func=cmd_job)

    p = commands.add_parser("candidate")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--short-id", required=True)
    p.add_argument("--worker", required=True)
    p.add_argument("--bundle", required=True)
    p.add_argument("--preview", required=True)
    p.add_argument("--edl-version", required=True, type=int)
    p.set_defaults(func=cmd_candidate)

    p = commands.add_parser("qc")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--short-id", required=True)
    p.add_argument("--verdict", required=True,
                   choices=["ready", "repair", "needs_user_review",
                            "failed_technical"])
    p.add_argument("--score", required=True, type=float)
    p.add_argument("--report", required=True)
    p.set_defaults(func=cmd_qc)

    p = commands.add_parser("export-start")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--short-id", required=True)
    p.add_argument("--job-id", required=True, type=int)
    p.set_defaults(func=cmd_export_start)

    p = commands.add_parser("reject-final")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--short-id", required=True)
    p.add_argument("--file", required=True)
    p.add_argument("--edl-version", required=True, type=int)
    p.add_argument("--report", required=True)
    p.set_defaults(func=cmd_reject_final)

    p = commands.add_parser("rescue")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--short-id", required=True)
    p.set_defaults(func=cmd_rescue)

    p = commands.add_parser("exception")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--short-id", required=True)
    p.add_argument("--status", required=True,
                   choices=["needs_user_review", "failed_technical"])
    p.add_argument("--report", required=True)
    p.set_defaults(func=cmd_exception)

    p = commands.add_parser("export")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--short-id", required=True)
    p.add_argument("--file", required=True)
    p.add_argument("--edl-version", required=True, type=int)
    p.add_argument("--duration", required=True, type=float)
    p.add_argument("--quality-review")
    p.set_defaults(func=cmd_export)

    p = commands.add_parser("quality-check")
    p.add_argument("--policy", required=True)
    p.add_argument("--evidence", required=True)
    p.add_argument("--final")
    p.add_argument("--output")
    p.set_defaults(func=cmd_quality_check)

    p = commands.add_parser("status")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = commands.add_parser("finalize")
    p.add_argument("--run-dir", required=True)
    p.set_defaults(func=cmd_finalize)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        value = args.func(args)
    except (StateError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if getattr(args, "json", False) or args.command in {
            "status", "finalize", "init"}:
        print(json.dumps(value, indent=2, sort_keys=True))
    else:
        print(json.dumps(value, sort_keys=True))
    if args.command == "quality-check" and value["verdict"] != "pass":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
