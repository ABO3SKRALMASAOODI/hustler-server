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
import os
from pathlib import Path
import re
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
        item["candidate"] = {
            "bundle": str(bundle), "preview": str(preview),
            "sha256": digest, "edl_version": args.edl_version,
            "recorded_at": now(),
        }
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
        item["style_lane"] = style_lane
        item["status"] = "exported"
        item["export"] = {
            **(item.get("export") or {}),
            "state": "done", "file": str(media), "sha256": sha256(media),
            "bytes": media.stat().st_size, "duration_s": args.duration,
            "edl_version": args.edl_version, "finished_at": now(),
        }
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
    p.set_defaults(func=cmd_export)

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
