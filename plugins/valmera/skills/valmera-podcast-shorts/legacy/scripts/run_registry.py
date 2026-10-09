#!/usr/bin/env python3
"""Durable, fenced run/task/Valmera-lease registry for podcast shorts."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
from urllib.parse import quote
import uuid



SCHEMA_VERSION = 4
APPLICATION_ID = 0x56525354  # VRST
FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$", re.ASCII)
ID_RE = re.compile(r"^[^\s\x00-\x1f\x7f]{1,240}$", re.ASCII)
RUN_STATUSES = ("active", "paused_safe", "ready", "blocked", "abstained")
CHILD_STATUSES = (
    "queued", "editing", "ready", "needs_repair", "paused_safe", "blocked"
)
QC_STATUSES = ("pending", "pass", "repair_required", "blocked")
LEASE_PURPOSES = ("edit", "repair", "qc")
RUN_LEASE_PHASES = (
    "acquisition",
    "selection",
    "reference",
    "acquisition_record",
    "materialization",
)
LEASE_STATES = ("live", "frozen", "released", "revoked")
CHECKPOINT_STATUSES = ("ready", "blocked", "paused_safe")
MAX_REPAIR_ROUNDS = 2
VALIDATOR_PATH = Path(__file__).with_name("validate_contract.py")


def default_registry_path() -> Path:
    root = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    return root / "state" / "valmera-podcast-shorts" / "run-registry.sqlite3"


def _emit(payload: dict) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _request_hash(command: str, payload: dict) -> str:
    encoded = json.dumps(
        {"command": command, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _required_id(name: str, value: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise ValueError(
            f"{name} must be a non-empty opaque ID of at most 240 characters "
            "without whitespace or control characters"
        )
    return value


def _required_text(name: str, value: str, limit: int = 2000) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    normalized = value.strip()
    if not normalized or len(normalized) > limit or any(
        ord(char) < 32 and char not in "\n\t" for char in normalized
    ):
        raise ValueError(f"{name} must be non-empty text of at most {limit} characters")
    return normalized


def _fingerprint(name: str, value: str) -> str:
    normalized = value.lower()
    if not FINGERPRINT_RE.fullmatch(normalized):
        raise ValueError(f"{name} must be sha256: followed by 64 lowercase hex digits")
    return normalized


def _positive_int(name: str, value: int) -> int:
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _nonnegative_int(name: str, value: int) -> int:
    if not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _finite_nonnegative_number(name: str, value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite non-negative number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return normalized


def _json_value(name: str, raw: str, expected_type: type):
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} is not valid JSON: {exc.msg}") from exc
    if not isinstance(value, expected_type):
        raise ValueError(f"{name} must decode to {expected_type.__name__}")
    return value


def _json_text(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _validate_normative(
    schema: str, data: dict, against: list[dict] | tuple[dict, ...] | None = None
) -> str:
    with tempfile.TemporaryDirectory(prefix="valmera-contract-") as temp_dir:
        command = [
            sys.executable,
            str(VALIDATOR_PATH),
            "--schema",
            schema,
            "--input",
            "-",
        ]
        for index, upstream in enumerate(against or []):
            upstream_path = Path(temp_dir) / f"against-{index}.json"
            upstream_path.write_text(
                json.dumps(upstream, ensure_ascii=False, allow_nan=False),
                encoding="utf-8",
            )
            command.extend(("--against", str(upstream_path)))
        result = subprocess.run(
            command,
            input=json.dumps(data, ensure_ascii=False, allow_nan=False),
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )
    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip() or "unknown validator failure"
        raise ValueError(f"invalid {schema} artifact: {details}")
    return _artifact_digest(data)


def _artifact_digest(data: dict) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _normalized_sha256(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("SHA-256 identity must be text")
    normalized = value.lower()
    if normalized.startswith("sha256:"):
        normalized = normalized[7:]
    if not re.fullmatch(r"[0-9a-f]{64}", normalized, re.ASCII):
        raise ValueError("SHA-256 identity must contain exactly 64 hexadecimal digits")
    return normalized


def _db_time(conn: sqlite3.Connection) -> int:
    return int(
        conn.execute("SELECT CAST(strftime('%s','now') AS INTEGER)").fetchone()[0]
    )


def _configure(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")


def _connect(path: Path, create: bool = False) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    if not create and not resolved.exists():
        raise ValueError(
            f"Registry does not exist: {resolved}. Run init once; "
            "refusing to recreate it silently"
        )
    resolved.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    resolved.parent.chmod(0o700)
    mode = "rwc" if create else "rw"
    uri = f"file:{quote(str(resolved), safe='/')}?mode={mode}"
    conn = sqlite3.connect(uri, uri=True, timeout=10.0, isolation_level=None)
    _configure(conn)
    return conn


def _validate_schema(conn: sqlite3.Connection) -> None:
    app_id = int(conn.execute("PRAGMA application_id").fetchone()[0])
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if app_id != APPLICATION_ID or version != SCHEMA_VERSION:
        raise ValueError(
            f"Unrecognized registry schema (application_id={app_id}, "
            f"version={version}); refusing automatic replacement or migration"
        )
    row = conn.execute(
        "SELECT schema_version FROM registry_meta WHERE singleton=1"
    ).fetchone()
    if row is None or int(row["schema_version"]) != SCHEMA_VERSION:
        raise ValueError("Registry metadata is absent or inconsistent")
    required_columns = {
        "runs": {
            "coordinator_result_json",
            "coordinator_result_validated_digest",
        },
        "child_task_intents": {
            "client_request_id",
            "task_marker",
            "run_id",
            "parent_project_id",
            "selection_fingerprint",
            "child_project_id",
            "card",
            "title",
            "assignment_id",
            "assignment_fingerprint",
            "assignment_json",
            "assignment_validated_digest",
            "state",
            "client_thread_id",
            "thread_id",
            "host_id",
            "create_started_at_s",
            "queued_at_s",
            "resolved_at_s",
            "consumed_at_s",
        },
        "children": {
            "assignment_json",
            "assignment_validated_digest",
            "recast_input_json",
            "recast_input_validated_digest",
            "recast_json",
            "recast_validated_digest",
            "recast_approval_status",
            "approved_candidate_id",
            "pre_mutation_block_reason",
        },
    }
    for table, required in required_columns.items():
        actual = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        missing = required - actual
        if missing:
            raise ValueError(
                f"Registry schema v{SCHEMA_VERSION} is missing {table} columns: "
                + ", ".join(sorted(missing))
            )


SCHEMA_STATEMENTS = (
    """
    CREATE TABLE registry_meta (
      singleton INTEGER PRIMARY KEY CHECK(singleton=1),
      schema_version INTEGER NOT NULL,
      registry_instance_id TEXT NOT NULL UNIQUE,
      next_lease_generation INTEGER NOT NULL CHECK(next_lease_generation >= 0),
      created_at_s INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE runs (
      run_id TEXT PRIMARY KEY,
      topic TEXT NOT NULL,
      coordinator_thread_id TEXT NOT NULL,
      coordinator_host_id TEXT NOT NULL,
      parent_project_id INTEGER UNIQUE,
      selection_fingerprint TEXT,
      coordinator_result_json TEXT,
      coordinator_result_validated_digest TEXT,
      status TEXT NOT NULL CHECK(status IN
        ('active','paused_safe','ready','blocked','abstained')),
      phase TEXT NOT NULL CHECK(phase IN
        ('created','acquisition','selection','reference','acquisition_record','materialization',
         'editing','qc','complete')),
      created_at_s INTEGER NOT NULL,
      updated_at_s INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE children (
      child_project_id INTEGER PRIMARY KEY CHECK(child_project_id > 0),
      run_id TEXT NOT NULL REFERENCES runs(run_id),
      card INTEGER NOT NULL CHECK(card > 0),
      title TEXT NOT NULL,
      thread_id TEXT NOT NULL,
      host_id TEXT NOT NULL,
      assignment_id TEXT NOT NULL UNIQUE,
      assignment_fingerprint TEXT NOT NULL,
      assignment_json TEXT NOT NULL,
      assignment_validated_digest TEXT NOT NULL,
      recast_input_json TEXT,
      recast_input_validated_digest TEXT,
      recast_json TEXT,
      recast_validated_digest TEXT,
      recast_approval_status TEXT NOT NULL CHECK(recast_approval_status IN
        ('not_required','pending_parent_approval','approved','blocked')),
      approved_candidate_id TEXT,
      pre_mutation_block_reason TEXT,
      child_status TEXT NOT NULL CHECK(child_status IN
        ('queued','editing','ready','needs_repair','paused_safe','blocked')),
      qc_status TEXT NOT NULL CHECK(qc_status IN
        ('pending','pass','repair_required','blocked')),
      repair_round INTEGER NOT NULL CHECK(repair_round >= 0),
      task_state TEXT NOT NULL CHECK(task_state IN ('live','frozen')),
      child_result_json TEXT,
      qc_result_json TEXT,
      exclusions_json TEXT NOT NULL,
      created_at_s INTEGER NOT NULL,
      updated_at_s INTEGER NOT NULL,
      UNIQUE(run_id, card),
      UNIQUE(host_id, thread_id)
    )
    """,
    """
    CREATE TABLE child_task_intents (
      client_request_id TEXT PRIMARY KEY,
      task_marker TEXT NOT NULL UNIQUE,
      run_id TEXT NOT NULL REFERENCES runs(run_id),
      parent_project_id INTEGER NOT NULL CHECK(parent_project_id > 0),
      selection_fingerprint TEXT NOT NULL,
      child_project_id INTEGER NOT NULL UNIQUE CHECK(child_project_id > 0),
      card INTEGER NOT NULL CHECK(card > 0),
      title TEXT NOT NULL,
      assignment_id TEXT NOT NULL UNIQUE,
      assignment_fingerprint TEXT NOT NULL,
      assignment_json TEXT NOT NULL,
      assignment_validated_digest TEXT NOT NULL,
      state TEXT NOT NULL CHECK(state IN
        ('prepared','dispatching','queued','resolved','consumed')),
      client_thread_id TEXT UNIQUE,
      thread_id TEXT,
      host_id TEXT,
      prepared_at_s INTEGER NOT NULL,
      create_started_at_s INTEGER,
      queued_at_s INTEGER,
      resolved_at_s INTEGER,
      consumed_at_s INTEGER,
      updated_at_s INTEGER NOT NULL,
      UNIQUE(run_id, card),
      CHECK((thread_id IS NULL) = (host_id IS NULL)),
      CHECK((client_thread_id IS NULL) = (queued_at_s IS NULL)),
      CHECK(
        (state='prepared' AND create_started_at_s IS NULL AND
         client_thread_id IS NULL AND thread_id IS NULL AND
         resolved_at_s IS NULL AND consumed_at_s IS NULL) OR
        (state='dispatching' AND create_started_at_s IS NOT NULL AND
         client_thread_id IS NULL AND thread_id IS NULL AND
         resolved_at_s IS NULL AND consumed_at_s IS NULL) OR
        (state='queued' AND create_started_at_s IS NOT NULL AND
         client_thread_id IS NOT NULL AND thread_id IS NULL AND
         resolved_at_s IS NULL AND consumed_at_s IS NULL) OR
        (state='resolved' AND create_started_at_s IS NOT NULL AND
         thread_id IS NOT NULL AND resolved_at_s IS NOT NULL AND
         consumed_at_s IS NULL) OR
        (state='consumed' AND create_started_at_s IS NOT NULL AND
         thread_id IS NOT NULL AND resolved_at_s IS NOT NULL AND
         consumed_at_s IS NOT NULL)
      )
    )
    """,
    """
    CREATE UNIQUE INDEX one_resolved_visible_task_per_host
    ON child_task_intents(host_id,thread_id) WHERE thread_id IS NOT NULL
    """,
    """
    CREATE TABLE leases (
      lease_generation INTEGER PRIMARY KEY CHECK(lease_generation > 0),
      lease_id TEXT NOT NULL UNIQUE,
      run_id TEXT NOT NULL REFERENCES runs(run_id),
      child_project_id INTEGER REFERENCES children(child_project_id),
      assignment_id TEXT,
      assignment_fingerprint TEXT,
      selection_fingerprint TEXT,
      purpose TEXT NOT NULL CHECK(purpose IN ('coordinator','edit','repair','qc')),
      phase TEXT CHECK(phase IS NULL OR phase IN
        ('acquisition','selection','reference','acquisition_record','materialization')),
      repair_round INTEGER NOT NULL CHECK(repair_round >= 0),
      holder_kind TEXT NOT NULL CHECK(holder_kind IN ('child','coordinator')),
      holder_thread_id TEXT NOT NULL,
      holder_host_id TEXT NOT NULL,
      state TEXT NOT NULL CHECK(state IN ('live','frozen','released','revoked')),
      checkpoint_status TEXT CHECK(checkpoint_status IS NULL OR checkpoint_status IN
        ('ready','blocked','paused_safe')),
      outstanding_job_ids_json TEXT,
      close_reason TEXT,
      created_at_s INTEGER NOT NULL,
      updated_at_s INTEGER NOT NULL,
      closed_at_s INTEGER
    )
    """,
    """
    CREATE TABLE run_snapshots (
      snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
      run_id TEXT NOT NULL REFERENCES runs(run_id),
      phase TEXT NOT NULL CHECK(phase IN
        ('acquisition','selection','reference','acquisition_record','materialization')),
      lease_generation INTEGER NOT NULL UNIQUE REFERENCES leases(lease_generation),
      checkpoint_status TEXT NOT NULL CHECK(checkpoint_status IN
        ('ready','blocked','paused_safe')),
      data_json TEXT NOT NULL,
      artifact_schema TEXT,
      validated_digest TEXT,
      created_at_s INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE attempt_results (
      result_id INTEGER PRIMARY KEY AUTOINCREMENT,
      run_id TEXT NOT NULL REFERENCES runs(run_id),
      child_project_id INTEGER NOT NULL REFERENCES children(child_project_id),
      lease_generation INTEGER NOT NULL REFERENCES leases(lease_generation),
      actor TEXT NOT NULL CHECK(actor IN ('child','qc')),
      repair_round INTEGER NOT NULL CHECK(repair_round >= 0),
      status TEXT NOT NULL,
      result_json TEXT NOT NULL,
      exclusions_json TEXT NOT NULL,
      validated_digest TEXT NOT NULL,
      created_at_s INTEGER NOT NULL,
      UNIQUE(lease_generation, actor)
    )
    """,
    """
    CREATE UNIQUE INDEX one_global_live_valmera_lease
    ON leases((1)) WHERE state='live'
    """,
    """
    CREATE TABLE call_permits (
      call_id TEXT PRIMARY KEY,
      lease_generation INTEGER NOT NULL REFERENCES leases(lease_generation),
      run_id TEXT NOT NULL REFERENCES runs(run_id),
      child_project_id INTEGER REFERENCES children(child_project_id),
      holder_thread_id TEXT NOT NULL,
      holder_host_id TEXT NOT NULL,
      state TEXT NOT NULL CHECK(state IN ('in_flight','ended')),
      outstanding_job_ids_json TEXT,
      started_at_s INTEGER NOT NULL,
      ended_at_s INTEGER
    )
    """,
    """
    CREATE UNIQUE INDEX one_global_in_flight_valmera_call
    ON call_permits((1)) WHERE state='in_flight'
    """,
    """
    CREATE TABLE operations (
      op_id TEXT PRIMARY KEY,
      command TEXT NOT NULL,
      request_hash TEXT NOT NULL,
      response_json TEXT NOT NULL,
      exit_code INTEGER NOT NULL,
      created_at_s INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE events (
      event_id INTEGER PRIMARY KEY AUTOINCREMENT,
      event TEXT NOT NULL,
      run_id TEXT,
      child_project_id INTEGER,
      lease_generation INTEGER,
      at_s INTEGER NOT NULL,
      details_json TEXT NOT NULL
    )
    """,
)


def _validate_empty_v3_for_migration(conn: sqlite3.Connection) -> None:
    app_id = int(conn.execute("PRAGMA application_id").fetchone()[0])
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if app_id != APPLICATION_ID or version != 3:
        raise ValueError("Only the recognized schema-v3 registry can be migrated")
    required_tables = {
        "registry_meta",
        "runs",
        "children",
        "leases",
        "run_snapshots",
        "attempt_results",
        "call_permits",
        "operations",
        "events",
    }
    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"
        )
    }
    if not required_tables.issubset(tables):
        raise ValueError("Schema-v3 registry is missing required tables; refusing migration")
    meta = conn.execute(
        "SELECT schema_version FROM registry_meta WHERE singleton=1"
    ).fetchone()
    if meta is None or int(meta["schema_version"]) != 3:
        raise ValueError("Schema-v3 registry metadata is inconsistent")
    nonempty = {
        table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in required_tables - {"registry_meta"}
    }
    nonempty = {table: count for table, count in nonempty.items() if count}
    if nonempty:
        raise ValueError(
            "Schema-v3 migration is supported only for an empty workflow registry; "
            f"found rows: {nonempty}"
        )
    child_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(children)")
    }
    run_columns = {row["name"] for row in conn.execute("PRAGMA table_info(runs)")}
    if (
        "assignment_json" in child_columns
        or "coordinator_result_json" in run_columns
        or "child_task_intents" in tables
    ):
        raise ValueError("Schema-v3 registry has unexpected v4 shape")


def _backup_v3_registry(conn: sqlite3.Connection, path: Path) -> Path:
    resolved = path.expanduser().resolve()
    now = _db_time(conn)
    backup = resolved.with_name(f"{resolved.name}.v3-backup-{now}.sqlite3")
    if backup.exists():
        backup = resolved.with_name(
            f"{resolved.name}.v3-backup-{now}-{uuid.uuid4().hex[:8]}.sqlite3"
        )
    target = sqlite3.connect(str(backup))
    try:
        conn.backup(target)
    finally:
        target.close()
    backup.chmod(0o600)
    return backup


def _verify_v3_backup(path: Path) -> dict:
    backup = sqlite3.connect(
        f"file:{quote(str(path), safe='/')}?mode=ro&immutable=1", uri=True
    )
    backup.row_factory = sqlite3.Row
    try:
        integrity = backup.execute("PRAGMA integrity_check").fetchone()[0]
        app_id = int(backup.execute("PRAGMA application_id").fetchone()[0])
        version = int(backup.execute("PRAGMA user_version").fetchone()[0])
        meta = backup.execute(
            "SELECT schema_version FROM registry_meta WHERE singleton=1"
        ).fetchone()
        counts = {
            table: int(backup.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "runs", "children", "leases", "run_snapshots", "attempt_results",
                "call_permits", "operations", "events",
            )
        }
        if (
            integrity != "ok"
            or app_id != APPLICATION_ID
            or version != 3
            or meta is None
            or int(meta["schema_version"]) != 3
            or any(counts.values())
        ):
            raise ValueError("schema-v3 backup verification failed")
        return {"integrity_check": integrity, "row_counts": counts}
    finally:
        backup.close()


def _migrate_empty_v3_to_v4(conn: sqlite3.Connection) -> None:
    _validate_empty_v3_for_migration(conn)
    statements = (
        "ALTER TABLE runs ADD COLUMN coordinator_result_json TEXT",
        "ALTER TABLE runs ADD COLUMN coordinator_result_validated_digest TEXT",
        "ALTER TABLE children ADD COLUMN assignment_json TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE children ADD COLUMN assignment_validated_digest TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE children ADD COLUMN recast_input_json TEXT",
        "ALTER TABLE children ADD COLUMN recast_input_validated_digest TEXT",
        "ALTER TABLE children ADD COLUMN recast_json TEXT",
        "ALTER TABLE children ADD COLUMN recast_validated_digest TEXT",
        "ALTER TABLE children ADD COLUMN recast_approval_status TEXT NOT NULL "
        "DEFAULT 'not_required' CHECK(recast_approval_status IN "
        "('not_required','pending_parent_approval','approved','blocked'))",
        "ALTER TABLE children ADD COLUMN approved_candidate_id TEXT",
        "ALTER TABLE children ADD COLUMN pre_mutation_block_reason TEXT",
    )
    for statement in statements:
        conn.execute(statement)
    conn.execute(
        """
        CREATE TABLE child_task_intents (
          client_request_id TEXT PRIMARY KEY,
          task_marker TEXT NOT NULL UNIQUE,
          run_id TEXT NOT NULL REFERENCES runs(run_id),
          parent_project_id INTEGER NOT NULL CHECK(parent_project_id > 0),
          selection_fingerprint TEXT NOT NULL,
          child_project_id INTEGER NOT NULL UNIQUE CHECK(child_project_id > 0),
          card INTEGER NOT NULL CHECK(card > 0),
          title TEXT NOT NULL,
          assignment_id TEXT NOT NULL UNIQUE,
          assignment_fingerprint TEXT NOT NULL,
          assignment_json TEXT NOT NULL,
          assignment_validated_digest TEXT NOT NULL,
          state TEXT NOT NULL CHECK(state IN
            ('prepared','dispatching','queued','resolved','consumed')),
          client_thread_id TEXT UNIQUE,
          thread_id TEXT,
          host_id TEXT,
          prepared_at_s INTEGER NOT NULL,
          create_started_at_s INTEGER,
          queued_at_s INTEGER,
          resolved_at_s INTEGER,
          consumed_at_s INTEGER,
          updated_at_s INTEGER NOT NULL,
          UNIQUE(run_id, card),
          CHECK((thread_id IS NULL) = (host_id IS NULL)),
          CHECK((client_thread_id IS NULL) = (queued_at_s IS NULL)),
          CHECK(
            (state='prepared' AND create_started_at_s IS NULL AND
             client_thread_id IS NULL AND thread_id IS NULL AND
             resolved_at_s IS NULL AND consumed_at_s IS NULL) OR
            (state='dispatching' AND create_started_at_s IS NOT NULL AND
             client_thread_id IS NULL AND thread_id IS NULL AND
             resolved_at_s IS NULL AND consumed_at_s IS NULL) OR
            (state='queued' AND create_started_at_s IS NOT NULL AND
             client_thread_id IS NOT NULL AND thread_id IS NULL AND
             resolved_at_s IS NULL AND consumed_at_s IS NULL) OR
            (state='resolved' AND create_started_at_s IS NOT NULL AND
             thread_id IS NOT NULL AND resolved_at_s IS NOT NULL AND
             consumed_at_s IS NULL) OR
            (state='consumed' AND create_started_at_s IS NOT NULL AND
             thread_id IS NOT NULL AND resolved_at_s IS NOT NULL AND
             consumed_at_s IS NOT NULL)
          )
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX one_resolved_visible_task_per_host "
        "ON child_task_intents(host_id,thread_id) WHERE thread_id IS NOT NULL"
    )
    conn.execute(
        "UPDATE registry_meta SET schema_version=? WHERE singleton=1",
        (SCHEMA_VERSION,),
    )
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def init_registry(args: argparse.Namespace, path: Path) -> int:
    conn = _connect(path, create=True)
    try:
        names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        }
        created = False
        migrated = False
        backup_path = None
        backup_verification = None
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if names and version == 3:
            _validate_empty_v3_for_migration(conn)
            backup_path = _backup_v3_registry(conn, path)
            backup_verification = _verify_v3_backup(backup_path)
            conn.execute("BEGIN EXCLUSIVE")
            _migrate_empty_v3_to_v4(conn)
            _validate_schema(conn)
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("migrated schema-v4 registry failed integrity_check")
            conn.commit()
            migrated = True
        elif names:
            conn.execute("BEGIN EXCLUSIVE")
            _validate_schema(conn)
            role_collisions = _task_role_collisions(conn)
            if role_collisions:
                raise ValueError(
                    "registry contains coordinator/child task identity collisions: "
                    + _json_text(role_collisions)
                )
            conn.commit()
        else:
            conn.execute("BEGIN EXCLUSIVE")
            if (
                int(conn.execute("PRAGMA application_id").fetchone()[0]) != 0
                or int(conn.execute("PRAGMA user_version").fetchone()[0]) != 0
            ):
                raise ValueError("Empty registry file has unknown SQLite metadata")
            for statement in SCHEMA_STATEMENTS:
                conn.execute(statement)
            now = _db_time(conn)
            instance = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO registry_meta VALUES(1,?,?,?,?)",
                (SCHEMA_VERSION, instance, 0, now),
            )
            conn.execute(f"PRAGMA application_id={APPLICATION_ID}")
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            created = True
            conn.commit()
        path.expanduser().resolve().chmod(0o600)
        row = conn.execute(
            "SELECT * FROM registry_meta WHERE singleton=1"
        ).fetchone()
        _emit(
            {
                "ok": True,
                "status": (
                    "initialized" if created else "migrated_v3_to_v4" if migrated else "existing"
                ),
                "registry": str(path.expanduser().resolve()),
                "backup_path": str(backup_path) if backup_path is not None else None,
                "backup_verification": backup_verification,
                "registry_instance_id": row["registry_instance_id"],
                "schema_version": row["schema_version"],
            }
        )
        return 0
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()


def _mutate(path: Path, command: str, op_id: str, request: dict, action) -> int:
    op_id = _required_id("op_id", op_id)
    conn = _connect(path)
    try:
        _validate_schema(conn)
        digest = _request_hash(command, request)
        conn.execute("BEGIN IMMEDIATE")
        role_collisions = _task_role_collisions(conn)
        if role_collisions:
            conn.rollback()
            _emit(
                {
                    "ok": False,
                    "status": "registry_task_role_collision",
                    "task_role_collisions": role_collisions,
                }
            )
            return 6
        prior = conn.execute(
            "SELECT * FROM operations WHERE op_id=?", (op_id,)
        ).fetchone()
        if prior is not None:
            if prior["request_hash"] != digest or prior["command"] != command:
                conn.rollback()
                _emit(
                    {
                        "ok": False,
                        "status": "idempotency_key_reused",
                        "op_id": op_id,
                    }
                )
                return 7
            conn.rollback()
            print(prior["response_json"])
            return int(prior["exit_code"])

        now = _db_time(conn)
        response, code, events = action(conn, now)
        encoded = _json_text(response)
        conn.execute(
            "INSERT INTO operations VALUES(?,?,?,?,?,?)",
            (op_id, command, digest, encoded, code, now),
        )
        for event in events:
            conn.execute(
                "INSERT INTO events(event,run_id,child_project_id,lease_generation,"
                "at_s,details_json) VALUES(?,?,?,?,?,?)",
                (
                    event["event"],
                    event.get("run_id"),
                    event.get("child_project_id"),
                    event.get("lease_generation"),
                    now,
                    _json_text(event.get("details", {})),
                ),
            )
        conn.commit()
        print(encoded)
        return code
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()


def _run(conn: sqlite3.Connection, run_id: str):
    return conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()


def _child(conn: sqlite3.Connection, child_project_id: int):
    return conn.execute(
        "SELECT * FROM children WHERE child_project_id=?", (child_project_id,)
    ).fetchone()


def _task_intent(conn: sqlite3.Connection, client_request_id: str):
    return conn.execute(
        "SELECT * FROM child_task_intents WHERE client_request_id=?",
        (client_request_id,),
    ).fetchone()


def _task_role_collisions(conn: sqlite3.Connection) -> list[dict]:
    """Return coordinator identities also assigned to a visible child task."""
    rows = conn.execute(
        "SELECT r.run_id AS coordinator_run_id,r.coordinator_thread_id AS thread_id,"
        "r.coordinator_host_id AS host_id,c.run_id AS child_run_id,"
        "c.child_project_id,NULL AS client_request_id,'child' AS mapping_kind "
        "FROM runs r JOIN children c ON c.host_id=r.coordinator_host_id AND "
        "c.thread_id=r.coordinator_thread_id UNION ALL "
        "SELECT r.run_id AS coordinator_run_id,r.coordinator_thread_id AS thread_id,"
        "r.coordinator_host_id AS host_id,i.run_id AS child_run_id,"
        "i.child_project_id,i.client_request_id,'resolved_intent' AS mapping_kind "
        "FROM runs r JOIN child_task_intents i ON i.host_id=r.coordinator_host_id "
        "AND i.thread_id=r.coordinator_thread_id WHERE i.state='resolved' "
        "ORDER BY coordinator_run_id,child_project_id,mapping_kind"
    ).fetchall()
    return [dict(row) for row in rows]


def _child_mapping_for_task_identity(
    conn: sqlite3.Connection, host_id: str, thread_id: str
) -> dict | None:
    child = conn.execute(
        "SELECT run_id AS child_run_id,child_project_id,NULL AS client_request_id,"
        "'child' AS mapping_kind FROM children WHERE host_id=? AND thread_id=?",
        (host_id, thread_id),
    ).fetchone()
    if child is not None:
        return dict(child)
    intent = conn.execute(
        "SELECT run_id AS child_run_id,child_project_id,client_request_id,"
        "'resolved_intent' AS mapping_kind FROM child_task_intents WHERE "
        "host_id=? AND thread_id=? AND state='resolved'",
        (host_id, thread_id),
    ).fetchone()
    return dict(intent) if intent is not None else None


def _coordinator_mapping_for_task_identity(
    conn: sqlite3.Connection, host_id: str, thread_id: str
) -> dict | None:
    row = conn.execute(
        "SELECT run_id AS coordinator_run_id FROM runs WHERE "
        "coordinator_host_id=? AND coordinator_thread_id=? ORDER BY run_id LIMIT 1",
        (host_id, thread_id),
    ).fetchone()
    return dict(row) if row is not None else None


def _task_marker(client_request_id: str) -> str:
    digest = hashlib.sha256(client_request_id.encode("utf-8")).hexdigest()[:24]
    return f"VPS-{digest}"


def _active_lease(conn: sqlite3.Connection):
    return conn.execute("SELECT * FROM leases WHERE state='live'").fetchone()


def _next_generation(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT next_lease_generation FROM registry_meta WHERE singleton=1"
    ).fetchone()
    generation = int(row["next_lease_generation"]) + 1
    conn.execute(
        "UPDATE registry_meta SET next_lease_generation=? WHERE singleton=1",
        (generation,),
    )
    return generation


def _latest_snapshot(conn: sqlite3.Connection, run_id: str, phase: str):
    return conn.execute(
        "SELECT * FROM run_snapshots WHERE run_id=? AND phase=? "
        "ORDER BY snapshot_id DESC LIMIT 1",
        (run_id, phase),
    ).fetchone()


def _trusted_snapshot_data(snapshot: sqlite3.Row, artifact_schema: str) -> dict:
    if snapshot["artifact_schema"] != artifact_schema or not snapshot["validated_digest"]:
        raise ValueError(
            f"snapshot {snapshot['snapshot_id']} lacks trusted {artifact_schema} validation"
        )
    data = json.loads(snapshot["data_json"])
    if _artifact_digest(data) != snapshot["validated_digest"]:
        raise ValueError(f"snapshot {snapshot['snapshot_id']} digest mismatch")
    return data


def _trusted_child_artifact(
    child: sqlite3.Row, json_column: str, digest_column: str, label: str
) -> dict:
    raw = child[json_column]
    digest = child[digest_column]
    if raw is None or digest is None:
        raise ValueError(f"child {child['child_project_id']} lacks trusted {label}")
    data = json.loads(raw)
    if _artifact_digest(data) != digest:
        raise ValueError(
            f"child {child['child_project_id']} {label} digest mismatch"
        )
    return data


def _source_acquisition_checkpoint(data: dict) -> dict:
    required = {
        "parent_project_id",
        "source_youtube_video_id",
        "source_asset_id",
        "source_sha256",
    }
    if set(data) != required:
        raise ValueError(
            "ready acquisition checkpoint must contain exactly "
            + ", ".join(sorted(required))
        )
    return {
        "parent_project_id": _positive_int(
            "acquisition.parent_project_id", data["parent_project_id"]
        ),
        "source_youtube_video_id": _required_id(
            "acquisition.source_youtube_video_id", data["source_youtube_video_id"]
        ),
        "source_asset_id": _positive_int(
            "acquisition.source_asset_id", data["source_asset_id"]
        ),
        "source_sha256": _normalized_sha256(data["source_sha256"]),
    }


def _selection_manifest(data: dict, validate: bool = True) -> tuple[str, list[dict], bool]:
    if validate:
        _validate_normative("selection", data)
    fingerprint = _fingerprint(
        "selection_snapshot.selection_fingerprint", data["selection_fingerprint"]
    )
    clips = data["clips"]
    abstained = data["abstained"]
    normalized = []
    cards = []
    for index, raw in enumerate(clips):
        card = _positive_int(f"selection clips[{index}].rank", raw["rank"])
        title = _required_text(f"selection clips[{index}].title", raw["title"], 1000)
        approved_start_s = _finite_nonnegative_number(
            f"selection clips[{index}].start", raw["start"]
        )
        approved_end_s = _finite_nonnegative_number(
            f"selection clips[{index}].end", raw["end"]
        )
        if approved_end_s <= approved_start_s:
            raise ValueError(f"selection clips[{index}] must have end after start")
        cards.append(card)
        normalized.append({
            "card": card,
            "title": title,
            "approved_start_s": approved_start_s,
            "approved_end_s": approved_end_s,
        })
    if cards != list(range(1, len(cards) + 1)):
        raise ValueError("selection stories must be in immutable FIFO card order 1..N")
    return fingerprint, normalized, abstained


def _validate_acquisition_lineage(
    acquisition_record: dict,
    selection_data: dict,
    reference_data: dict | None,
    source_acquisition_data: dict,
    run_identity: dict | sqlite3.Row,
    validate: bool = True,
) -> None:
    selection_fingerprint, selected, abstained = _selection_manifest(
        selection_data, validate=validate
    )
    if validate:
        upstreams = [selection_data]
        if reference_data is not None:
            upstreams.append(reference_data)
        _validate_normative("acquisition-record", acquisition_record, against=upstreams)
    if (
        acquisition_record.get("parent_project_id") != selection_data.get("parent_project_id")
        or acquisition_record.get("selected_clip_count") != len(selected)
    ):
        raise ValueError("acquisition record does not match frozen selection")
    source = acquisition_record.get("source")
    acquired = _source_acquisition_checkpoint(source_acquisition_data)
    if not isinstance(source, dict) or (
        source.get("youtube_video_id")
        != selection_data.get("source_youtube_video_id")
        or source.get("youtube_video_id") != acquired["source_youtube_video_id"]
        or source.get("asset_id") != acquired["source_asset_id"]
        or _normalized_sha256(source.get("sha256"))
        != _normalized_sha256(selection_data.get("source_sha256"))
        or _normalized_sha256(source.get("sha256")) != acquired["source_sha256"]
        or source.get("duration_s") != selection_data.get("source_duration_s")
        or source.get("source_visual_inspection")
        != selection_data.get("source_visual_inspection")
        or source.get("source_speech_transcript")
        != selection_data.get("source_speech_transcript")
    ):
        raise ValueError("acquisition source does not match frozen selection evidence")
    if (
        acquisition_record.get("run_id") != run_identity["run_id"]
        or acquisition_record.get("topic") != run_identity["topic"]
        or acquisition_record.get("parent_project_id")
        != run_identity["parent_project_id"]
    ):
        raise ValueError("acquisition record does not match bound run identity")
    reference = acquisition_record.get("reference")
    if abstained:
        if reference is not None or reference_data is not None:
            raise ValueError("abstained acquisition must not contain a reference")
    elif reference_data is None or not isinstance(reference, dict) or (
        reference.get("asset_id") != reference_data.get("parent_reference_asset_id")
        or reference.get("youtube_video_id") != reference_data.get("youtube_video_id")
        or _normalized_sha256(reference.get("sha256"))
        != _normalized_sha256(reference_data.get("reference_sha256"))
    ):
        raise ValueError("acquisition reference does not match frozen reference profile")


def _materialization_manifest(
    data: dict, selection_data: dict, validate: bool = True
) -> list[dict]:
    selection_fingerprint, selected, abstained = _selection_manifest(
        selection_data, validate=validate
    )
    if abstained:
        raise ValueError("an abstained selection cannot be materialized")
    fingerprint = _fingerprint(
        "materialization_snapshot.selection_fingerprint",
        data.get("selection_fingerprint"),
    )
    if fingerprint != selection_fingerprint:
        raise ValueError("materialization fingerprint does not match frozen selection")
    if set(data) != {"selection_fingerprint", "stories"}:
        raise ValueError(
            "materialization snapshot must contain only selection_fingerprint and stories"
        )
    stories = data.get("stories")
    if not isinstance(stories, list) or len(stories) != len(selected):
        raise ValueError("materialization stories must cover every selected story exactly once")
    normalized = []
    child_ids = set()
    exact_story_fields = {
        "card",
        "title",
        "approved_start_s",
        "approved_end_s",
        "status",
        "child_project_id",
        "seeded_child_start_s",
        "seeded_child_end_s",
        "seed_snap_reason",
        "seed_range_verified_by",
        "seed_range_evidence_digest",
        "generation_job_id",
        "generation_failure",
    }
    for index, (raw, expected) in enumerate(zip(stories, selected)):
        if not isinstance(raw, dict):
            raise ValueError(f"materialization stories[{index}] must be an object")
        if set(raw) != exact_story_fields:
            missing = sorted(exact_story_fields - set(raw))
            extra = sorted(set(raw) - exact_story_fields)
            raise ValueError(
                f"materialization stories[{index}] fields mismatch; "
                f"missing={missing}, extra={extra}"
            )
        identity = {
            "card": raw.get("card"),
            "title": raw.get("title"),
            "approved_start_s": raw.get("approved_start_s"),
            "approved_end_s": raw.get("approved_end_s"),
        }
        if identity != expected:
            raise ValueError(
                f"materialization stories[{index}] does not match frozen FIFO selection/range"
            )
        status = raw.get("status")
        if status not in ("materialized", "failed", "pending"):
            raise ValueError("materialization status must be materialized, failed, or pending")
        child_project_id = raw.get("child_project_id")
        seeded_start = raw.get("seeded_child_start_s")
        seeded_end = raw.get("seeded_child_end_s")
        snap_reason = raw.get("seed_snap_reason")
        seed_verified_by = raw.get("seed_range_verified_by")
        seed_evidence_digest = raw.get("seed_range_evidence_digest")
        generation_job_id = raw.get("generation_job_id")
        generation_failure = raw.get("generation_failure")
        if generation_job_id is not None and (
            isinstance(generation_job_id, bool)
            or not isinstance(generation_job_id, (str, int))
            or (isinstance(generation_job_id, str) and not generation_job_id.strip())
        ):
            raise ValueError("generation_job_id must be a non-empty string, integer, or null")
        if status == "materialized":
            if generation_job_id is None:
                raise ValueError("materialized story requires generation_job_id")
            child_project_id = _positive_int(
                f"materialization stories[{index}].child_project_id", child_project_id
            )
            if child_project_id in child_ids:
                raise ValueError("materialized child_project_id values must be unique")
            child_ids.add(child_project_id)
            seeded_start = _finite_nonnegative_number(
                f"materialization stories[{index}].seeded_child_start_s", seeded_start
            )
            seeded_end = _finite_nonnegative_number(
                f"materialization stories[{index}].seeded_child_end_s", seeded_end
            )
            if seeded_end <= seeded_start:
                raise ValueError("materialized seeded child range must have end after start")
            if round(seeded_start, 2) != seeded_start or round(seeded_end, 2) != seeded_end:
                raise ValueError("materialized seeded child range must use 2-decimal semantics")
            if seeded_end > float(selection_data["source_duration_s"]):
                raise ValueError("materialized seeded child range exceeds source duration")
            start_drift = abs(seeded_start - expected["approved_start_s"])
            end_drift = abs(seeded_end - expected["approved_end_s"])
            drifted = start_drift > 1e-9 or end_drift > 1e-9
            if drifted and snap_reason != "word_boundary_snap":
                raise ValueError(
                    "materialized seeded range drift requires seed_snap_reason=word_boundary_snap"
                )
            if not drifted and snap_reason != "none":
                raise ValueError("an exact seeded range must use seed_snap_reason=none")
            if seed_verified_by not in (
                "authoritative_child_edl",
                "audit_snap_keep_to_words",
            ):
                raise ValueError("materialized seeded range lacks authoritative verification")
            if drifted and seed_verified_by != "audit_snap_keep_to_words":
                raise ValueError(
                    "seeded range drift must be deterministically verified by audit_snap_keep_to_words"
                )
            seed_evidence_digest = _fingerprint(
                f"materialization stories[{index}].seed_range_evidence_digest",
                seed_evidence_digest,
            )
            if generation_failure is not None:
                raise ValueError("materialized story generation_failure must be null")
        elif child_project_id is not None:
            raise ValueError("only materialized stories may have a child_project_id")
        elif any(value is not None for value in (
            seeded_start,
            seeded_end,
            snap_reason,
            seed_verified_by,
            seed_evidence_digest,
        )):
            raise ValueError(
                "only materialized stories may have seeded range or snap evidence"
            )
        if status == "failed":
            generation_failure = _required_text(
                f"materialization stories[{index}].generation_failure",
                generation_failure,
                2000,
            )
        elif generation_failure is not None:
            raise ValueError("only failed materialization may carry generation_failure")
        normalized.append(
            {
                **expected,
                "status": status,
                "child_project_id": child_project_id,
                "seeded_child_start_s": seeded_start,
                "seeded_child_end_s": seeded_end,
                "seed_snap_reason": snap_reason,
                "seed_range_verified_by": seed_verified_by,
                "seed_range_evidence_digest": seed_evidence_digest,
                "generation_job_id": generation_job_id,
                "generation_failure": generation_failure,
            }
        )
    return normalized


def _fifo_edit_gate(
    conn: sqlite3.Connection, run_id: str, child_project_id: int, card: int
) -> tuple[bool, str]:
    selection = _latest_snapshot(conn, run_id, "selection")
    materialization = _latest_snapshot(conn, run_id, "materialization")
    if (
        selection is None
        or materialization is None
        or selection["checkpoint_status"] != "ready"
        or materialization["checkpoint_status"] != "ready"
    ):
        return False, "frozen_materialization_missing"
    selection_data = _trusted_snapshot_data(selection, "selection")
    materialization_data = _trusted_snapshot_data(
        materialization, "materialization-v1"
    )
    manifest = _materialization_manifest(
        materialization_data, selection_data, validate=False
    )
    current = next((item for item in manifest if item["card"] == card), None)
    if current is None or (
        current["status"] != "materialized"
        or current["child_project_id"] != child_project_id
    ):
        return False, "current_child_not_materialized"
    for item in manifest:
        if item["card"] >= card:
            break
        if item["status"] == "failed":
            continue
        if item["status"] != "materialized":
            return False, f"prior_card_{item['card']}_not_terminal"
        prior = _child(conn, item["child_project_id"])
        if prior is None or prior["qc_status"] not in ("pass", "blocked"):
            return False, f"prior_card_{item['card']}_not_parent_qc_terminal"
    return True, "fifo_ready"


def create_run(args: argparse.Namespace, path: Path) -> int:
    request = {
        "run_id": _required_id("run_id", args.run_id),
        "topic": _required_text("topic", args.topic),
        "coordinator_thread_id": _required_id(
            "coordinator_thread_id", args.coordinator_thread_id
        ),
        "coordinator_host_id": _required_id(
            "coordinator_host_id", args.coordinator_host_id
        ),
    }

    def action(conn, now):
        old = _run(conn, request["run_id"])
        if old is not None:
            same = all(old[key] == value for key, value in request.items())
            if same:
                return (
                    {"ok": True, "status": "already_created", **request},
                    0,
                    [],
                )
            return (
                {"ok": False, "status": "run_id_conflict", "run_id": request["run_id"]},
                4,
                [],
            )
        child_mapping = _child_mapping_for_task_identity(
            conn,
            request["coordinator_host_id"],
            request["coordinator_thread_id"],
        )
        if child_mapping is not None:
            return (
                {
                    "ok": False,
                    "status": "coordinator_identity_is_child_task",
                    **child_mapping,
                },
                4,
                [],
            )
        conn.execute(
            "INSERT INTO runs(run_id,topic,coordinator_thread_id,coordinator_host_id,"
            "status,phase,created_at_s,updated_at_s) VALUES(?,?,?,?,?,?,?,?)",
            (
                request["run_id"],
                request["topic"],
                request["coordinator_thread_id"],
                request["coordinator_host_id"],
                "active",
                "created",
                now,
                now,
            ),
        )
        event = {"event": "run_created", "run_id": request["run_id"]}
        return ({"ok": True, "status": "created", **request}, 0, [event])

    return _mutate(path, "create-run", args.op_id, request, action)


def grant_run_lease(args: argparse.Namespace, path: Path) -> int:
    request = {
        "run_id": _required_id("run_id", args.run_id),
        "lease_id": _required_id("lease_id", args.lease_id),
        "phase": args.phase,
    }

    def action(conn, now):
        run = _run(conn, request["run_id"])
        if run is None:
            return ({"ok": False, "status": "run_not_found"}, 3, [])
        if run["status"] in ("ready", "blocked", "abstained"):
            return ({"ok": False, "status": "run_terminal"}, 5, [])
        if run["coordinator_result_json"] is not None:
            return ({"ok": False, "status": "run_result_frozen_pending_finalization"}, 5, [])
        blocked_phase = conn.execute(
            "SELECT phase FROM run_snapshots WHERE run_id=? "
            "AND checkpoint_status='blocked' ORDER BY snapshot_id DESC LIMIT 1",
            (request["run_id"],),
        ).fetchone()
        if blocked_phase is not None:
            return (
                {
                    "ok": False,
                    "status": "run_blocked_pending_finalization",
                    "blocked_phase": blocked_phase["phase"],
                },
                5,
                [],
            )
        prior_phase = _latest_snapshot(conn, request["run_id"], request["phase"])
        if prior_phase is not None and prior_phase["checkpoint_status"] == "ready":
            return ({"ok": False, "status": "phase_already_frozen_ready"}, 5, [])
        if request["phase"] in ("acquisition", "selection") and (
            run["parent_project_id"] is not None or run["selection_fingerprint"] is not None
        ):
            return ({"ok": False, "status": "bound_run_cannot_reopen_phase"}, 5, [])
        old = conn.execute(
            "SELECT state,lease_generation FROM leases WHERE lease_id=?",
            (request["lease_id"],),
        ).fetchone()
        if old is not None:
            return (
                {
                    "ok": False,
                    "status": "lease_id_already_used",
                    "lease_generation": old["lease_generation"],
                    "state": old["state"],
                },
                4,
                [],
            )
        active = _active_lease(conn)
        if active is not None:
            return (
                {
                    "ok": False,
                    "status": "global_lease_busy",
                    "active_lease": {
                        "lease_id": active["lease_id"],
                        "lease_generation": active["lease_generation"],
                        "run_id": active["run_id"],
                        "child_project_id": active["child_project_id"],
                        "phase": active["phase"],
                    },
                },
                4,
                [],
            )
        if request["phase"] in ("selection", "reference"):
            acquisition = _latest_snapshot(conn, request["run_id"], "acquisition")
            if acquisition is None or acquisition["checkpoint_status"] != "ready":
                return ({"ok": False, "status": "acquisition_snapshot_not_ready"}, 5, [])
        if request["phase"] == "reference":
            selection = _latest_snapshot(conn, request["run_id"], "selection")
            if selection is None or selection["checkpoint_status"] != "ready":
                return ({"ok": False, "status": "selection_snapshot_not_ready"}, 5, [])
            selection_data = _trusted_snapshot_data(selection, "selection")
            _, reference_stories, reference_abstained = _selection_manifest(
                selection_data, validate=False
            )
            if reference_abstained or not reference_stories:
                return ({"ok": False, "status": "nothing_to_reference"}, 5, [])
        if request["phase"] in ("acquisition_record", "materialization"):
            if run["parent_project_id"] is None or run["selection_fingerprint"] is None:
                return ({"ok": False, "status": "run_not_bound"}, 5, [])
            selection = _latest_snapshot(conn, request["run_id"], "selection")
            if selection is None or selection["checkpoint_status"] != "ready":
                return ({"ok": False, "status": "selection_snapshot_not_ready"}, 5, [])
            selection_data = _trusted_snapshot_data(selection, "selection")
            _, stories, abstained = _selection_manifest(selection_data, validate=False)
            if request["phase"] == "materialization" and (abstained or not stories):
                return ({"ok": False, "status": "nothing_to_materialize"}, 5, [])
            if not abstained:
                reference = _latest_snapshot(conn, request["run_id"], "reference")
                if reference is None or reference["checkpoint_status"] != "ready":
                    return ({"ok": False, "status": "reference_snapshot_not_ready"}, 5, [])
                _trusted_snapshot_data(reference, "reference-profile")
            if request["phase"] == "materialization":
                acquisition_record = _latest_snapshot(
                    conn, request["run_id"], "acquisition_record"
                )
                if (
                    acquisition_record is None
                    or acquisition_record["checkpoint_status"] != "ready"
                ):
                    return (
                        {"ok": False, "status": "acquisition_record_not_ready"},
                        5,
                        [],
                    )
                _trusted_snapshot_data(acquisition_record, "acquisition-record")
        generation = _next_generation(conn)
        conn.execute(
            "INSERT INTO leases(lease_generation,lease_id,run_id,child_project_id,"
            "assignment_id,assignment_fingerprint,selection_fingerprint,purpose,phase,"
            "repair_round,holder_kind,holder_thread_id,holder_host_id,state,"
            "created_at_s,updated_at_s) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                generation,
                request["lease_id"],
                request["run_id"],
                None,
                None,
                None,
                run["selection_fingerprint"],
                "coordinator",
                request["phase"],
                0,
                "coordinator",
                run["coordinator_thread_id"],
                run["coordinator_host_id"],
                "live",
                now,
                now,
            ),
        )
        conn.execute(
            "UPDATE runs SET status='active',phase=?,updated_at_s=? WHERE run_id=?",
            (request["phase"], now, request["run_id"]),
        )
        event = {
            "event": "run_lease_granted",
            "run_id": request["run_id"],
            "lease_generation": generation,
            "details": {"lease_id": request["lease_id"], "phase": request["phase"]},
        }
        return (
            {
                "ok": True,
                "status": "granted",
                "lease_id": request["lease_id"],
                "lease_generation": generation,
                "purpose": "coordinator",
                "phase": request["phase"],
                "holder_kind": "coordinator",
                "holder_thread_id": run["coordinator_thread_id"],
                "holder_host_id": run["coordinator_host_id"],
            },
            0,
            [event],
        )

    return _mutate(path, "grant-run-lease", args.op_id, request, action)


def _run_lease_request(args: argparse.Namespace) -> dict:
    return {
        "run_id": _required_id("run_id", args.run_id),
        "lease_id": _required_id("lease_id", args.lease_id),
        "lease_generation": _positive_int("lease_generation", args.lease_generation),
        "phase": args.phase,
        "thread_id": _required_id("thread_id", args.thread_id),
        "host_id": _required_id("host_id", args.host_id),
    }


def _run_lease_authorization(conn: sqlite3.Connection, request: dict):
    lease = conn.execute(
        "SELECT * FROM leases WHERE lease_id=? AND lease_generation=?",
        (request["lease_id"], request["lease_generation"]),
    ).fetchone()
    if lease is None:
        return False, ["lease_not_found"], None
    reasons = []
    expected = {
        "state": "live",
        "run_id": request["run_id"],
        "child_project_id": None,
        "purpose": "coordinator",
        "phase": request["phase"],
        "holder_kind": "coordinator",
        "holder_thread_id": request["thread_id"],
        "holder_host_id": request["host_id"],
    }
    for key, value in expected.items():
        if lease[key] != value:
            reasons.append(f"{key}_mismatch")
    run = _run(conn, request["run_id"])
    if run is None:
        reasons.append("run_missing")
    elif (
        run["coordinator_thread_id"] != request["thread_id"]
        or run["coordinator_host_id"] != request["host_id"]
    ):
        reasons.append("coordinator_mapping_changed")
    return not reasons, reasons, lease


def check_run_lease(args: argparse.Namespace, path: Path) -> int:
    request = _run_lease_request(args)
    conn = _connect(path)
    try:
        _validate_schema(conn)
        conn.execute("BEGIN")
        authorized, reasons, lease = _run_lease_authorization(conn, request)
        response = {
            "ok": authorized,
            "authorized": authorized,
            "status": "authorized" if authorized else "denied",
            "lease_id": request["lease_id"],
            "lease_generation": request["lease_generation"],
            "state": lease["state"] if lease is not None else "missing",
            "reasons": reasons,
        }
        conn.commit()
        _emit(response)
        return 0 if authorized else 4
    finally:
        if conn.in_transaction:
            conn.rollback()
        conn.close()


def _begin_call(
    path: Path,
    command: str,
    op_id: str,
    auth: dict,
    call_id: str,
    authorizer,
) -> int:
    request = {**auth, "call_id": _required_id("call_id", call_id)}

    def action(conn, now):
        authorized, reasons, lease = authorizer(conn, auth)
        if not authorized:
            return ({"ok": False, "status": "lease_denied", "reasons": reasons}, 4, [])
        old = conn.execute(
            "SELECT state,lease_generation FROM call_permits WHERE call_id=?",
            (request["call_id"],),
        ).fetchone()
        if old is not None:
            return (
                {
                    "ok": False,
                    "status": "call_id_already_used",
                    "state": old["state"],
                    "lease_generation": old["lease_generation"],
                },
                4,
                [],
            )
        active = conn.execute(
            "SELECT call_id,lease_generation FROM call_permits "
            "WHERE state='in_flight'"
        ).fetchone()
        if active is not None:
            return (
                {
                    "ok": False,
                    "status": "global_call_in_flight",
                    "active_call_id": active["call_id"],
                    "active_lease_generation": active["lease_generation"],
                },
                4,
                [],
            )
        conn.execute(
            "INSERT INTO call_permits(call_id,lease_generation,run_id,"
            "child_project_id,holder_thread_id,holder_host_id,state,started_at_s) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                request["call_id"],
                auth["lease_generation"],
                auth["run_id"],
                lease["child_project_id"],
                lease["holder_thread_id"],
                lease["holder_host_id"],
                "in_flight",
                now,
            ),
        )
        event = {
            "event": "valmera_call_begun",
            "run_id": auth["run_id"],
            "child_project_id": lease["child_project_id"],
            "lease_generation": auth["lease_generation"],
            "details": {"call_id": request["call_id"]},
        }
        return (
            {
                "ok": True,
                "authorized": True,
                "status": "call_permit_granted",
                "call_id": request["call_id"],
                "lease_generation": auth["lease_generation"],
            },
            0,
            [event],
        )

    return _mutate(path, command, op_id, request, action)


def _end_call(
    path: Path,
    command: str,
    op_id: str,
    auth: dict,
    call_id: str,
    outstanding_job_ids_json: str,
    authorizer,
) -> int:
    outstanding = _json_value(
        "outstanding_job_ids_json", outstanding_job_ids_json, list
    )
    request = {
        **auth,
        "call_id": _required_id("call_id", call_id),
        "outstanding_job_ids": outstanding,
    }

    def action(conn, now):
        authorized, reasons, lease = authorizer(conn, auth)
        if not authorized:
            return ({"ok": False, "status": "lease_denied", "reasons": reasons}, 4, [])
        permit = conn.execute(
            "SELECT * FROM call_permits WHERE call_id=?", (request["call_id"],)
        ).fetchone()
        if permit is None:
            return ({"ok": False, "status": "call_permit_not_found"}, 3, [])
        if (
            permit["state"] != "in_flight"
            or permit["lease_generation"] != auth["lease_generation"]
            or permit["run_id"] != auth["run_id"]
            or permit["child_project_id"] != lease["child_project_id"]
            or permit["holder_thread_id"] != lease["holder_thread_id"]
            or permit["holder_host_id"] != lease["holder_host_id"]
        ):
            return ({"ok": False, "status": "stale_or_mismatched_call_permit"}, 4, [])
        encoded_jobs = _json_text(outstanding)
        conn.execute(
            "UPDATE call_permits SET state='ended',outstanding_job_ids_json=?,"
            "ended_at_s=? WHERE call_id=?",
            (encoded_jobs, now, request["call_id"]),
        )
        conn.execute(
            "UPDATE leases SET outstanding_job_ids_json=?,updated_at_s=? "
            "WHERE lease_generation=?",
            (encoded_jobs, now, auth["lease_generation"]),
        )
        event = {
            "event": "valmera_call_ended",
            "run_id": auth["run_id"],
            "child_project_id": lease["child_project_id"],
            "lease_generation": auth["lease_generation"],
            "details": {
                "call_id": request["call_id"],
                "outstanding_job_ids": outstanding,
            },
        }
        return (
            {
                "ok": True,
                "status": "call_permit_ended",
                "call_id": request["call_id"],
                "lease_generation": auth["lease_generation"],
                "outstanding_job_ids": outstanding,
            },
            0,
            [event],
        )

    return _mutate(path, command, op_id, request, action)


def begin_run_call(args: argparse.Namespace, path: Path) -> int:
    auth = _run_lease_request(args)
    return _begin_call(
        path,
        "begin-run-call",
        args.op_id,
        auth,
        args.call_id,
        _run_lease_authorization,
    )


def end_run_call(args: argparse.Namespace, path: Path) -> int:
    auth = _run_lease_request(args)
    return _end_call(
        path,
        "end-run-call",
        args.op_id,
        auth,
        args.call_id,
        args.outstanding_job_ids_json,
        _run_lease_authorization,
    )


def record_run_snapshot(args: argparse.Namespace, path: Path) -> int:
    auth = _run_lease_request(args)
    data = _json_value("data_json", args.data_json, dict)
    request = {
        **auth,
        "checkpoint_status": args.checkpoint_status,
        "data": data,
    }

    def action(conn, now):
        authorized, reasons, lease = _run_lease_authorization(conn, auth)
        if not authorized:
            return ({"ok": False, "status": "lease_denied", "reasons": reasons}, 4, [])
        if conn.execute(
            "SELECT 1 FROM run_snapshots WHERE lease_generation=?",
            (auth["lease_generation"],),
        ).fetchone() is not None:
            return ({"ok": False, "status": "lease_snapshot_already_recorded"}, 4, [])
        checkpoint_ready = request["checkpoint_status"] == "ready"
        artifact_schema = "safe-checkpoint-v1"
        validated_digest = "sha256:" + hashlib.sha256(
            json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if not checkpoint_ready:
            _required_text("non-ready snapshot reason", data.get("reason"), 2000)
            if request["checkpoint_status"] == "blocked":
                evidence = data.get("evidence")
                if not isinstance(evidence, list) or not evidence:
                    raise ValueError("blocked phase snapshot requires non-empty evidence")
                for index, value in enumerate(evidence):
                    _required_text(f"blocked snapshot evidence[{index}]", value, 2000)
        if auth["phase"] == "acquisition" and checkpoint_ready:
            _source_acquisition_checkpoint(data)
            artifact_schema = "source-acquisition-checkpoint-v1"
        elif auth["phase"] == "selection" and checkpoint_ready:
            artifact_schema = "selection"
            validated_digest = _validate_normative("selection", data)
            _selection_manifest(data, validate=False)
            acquisition = _latest_snapshot(conn, auth["run_id"], "acquisition")
            acquisition_data = _trusted_snapshot_data(
                acquisition, "source-acquisition-checkpoint-v1"
            )
            acquired = _source_acquisition_checkpoint(acquisition_data)
            if (
                data.get("parent_project_id") != acquired["parent_project_id"]
                or data.get("source_youtube_video_id")
                != acquired["source_youtube_video_id"]
                or _normalized_sha256(data.get("source_sha256"))
                != acquired["source_sha256"]
            ):
                raise ValueError(
                    "selection parent/source identity does not match frozen acquisition"
                )
        elif auth["phase"] == "acquisition_record" and checkpoint_ready:
            selection = _latest_snapshot(conn, auth["run_id"], "selection")
            if selection is None or selection["checkpoint_status"] != "ready":
                return ({"ok": False, "status": "selection_snapshot_not_ready"}, 5, [])
            selection_data = _trusted_snapshot_data(selection, "selection")
            _, selected, abstained = _selection_manifest(selection_data, validate=False)
            reference_data = None
            upstreams = [selection_data]
            if not abstained:
                reference = _latest_snapshot(conn, auth["run_id"], "reference")
                if reference is None or reference["checkpoint_status"] != "ready":
                    return ({"ok": False, "status": "reference_snapshot_not_ready"}, 5, [])
                reference_data = _trusted_snapshot_data(reference, "reference-profile")
                upstreams.append(reference_data)
            run = _run(conn, auth["run_id"])
            source_acquisition = _latest_snapshot(
                conn, auth["run_id"], "acquisition"
            )
            if (
                source_acquisition is None
                or source_acquisition["checkpoint_status"] != "ready"
            ):
                return ({"ok": False, "status": "acquisition_snapshot_not_ready"}, 5, [])
            source_acquisition_data = _trusted_snapshot_data(
                source_acquisition, "source-acquisition-checkpoint-v1"
            )
            validated_digest = _validate_normative(
                "acquisition-record", data, against=upstreams
            )
            _validate_acquisition_lineage(
                data,
                selection_data,
                reference_data=reference_data,
                source_acquisition_data=source_acquisition_data,
                run_identity=run,
                validate=False,
            )
            artifact_schema = "acquisition-record"
        elif auth["phase"] == "materialization" and checkpoint_ready:
            selection = _latest_snapshot(conn, auth["run_id"], "selection")
            if selection is None or selection["checkpoint_status"] != "ready":
                return ({"ok": False, "status": "selection_snapshot_not_ready"}, 5, [])
            selection_data = _trusted_snapshot_data(selection, "selection")
            acquisition_record = _latest_snapshot(
                conn, auth["run_id"], "acquisition_record"
            )
            if (
                acquisition_record is None
                or acquisition_record["checkpoint_status"] != "ready"
            ):
                return ({"ok": False, "status": "acquisition_record_not_ready"}, 5, [])
            _trusted_snapshot_data(acquisition_record, "acquisition-record")
            materialized = _materialization_manifest(
                data, selection_data, validate=False
            )
            for item in materialized:
                if item["status"] != "materialized":
                    continue
                project_id = item["child_project_id"]
                parent_collision = conn.execute(
                    "SELECT run_id FROM runs WHERE parent_project_id=?",
                    (project_id,),
                ).fetchone()
                child_collision = _child(conn, project_id)
                if parent_collision is not None or child_collision is not None:
                    raise ValueError(
                        "materialized child_project_id collides with an existing "
                        "parent or child project role"
                    )
            if any(
                item["status"] == "pending" for item in materialized
            ):
                raise ValueError("a ready materialization snapshot may not contain pending stories")
            artifact_schema = "materialization-v1"
        elif auth["phase"] == "reference" and checkpoint_ready:
            artifact_schema = "reference-profile"
            validated_digest = _validate_normative("reference-profile", data)
        conn.execute(
            "INSERT INTO run_snapshots(run_id,phase,lease_generation,checkpoint_status,"
            "data_json,artifact_schema,validated_digest,created_at_s) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                auth["run_id"],
                auth["phase"],
                auth["lease_generation"],
                request["checkpoint_status"],
                _json_text(data),
                artifact_schema,
                validated_digest,
                now,
            ),
        )
        event = {
            "event": "run_snapshot_recorded",
            "run_id": auth["run_id"],
            "lease_generation": auth["lease_generation"],
            "details": {
                "phase": auth["phase"],
                "checkpoint_status": request["checkpoint_status"],
            },
        }
        return (
            {
                "ok": True,
                "status": "run_snapshot_recorded",
                "phase": auth["phase"],
                "checkpoint_status": request["checkpoint_status"],
            },
            0,
            [event],
        )

    return _mutate(path, "record-run-snapshot", args.op_id, request, action)


def close_run_lease(args: argparse.Namespace, path: Path) -> int:
    outstanding = _json_value(
        "outstanding_job_ids_json", args.outstanding_job_ids_json, list
    )
    request = {
        "run_id": _required_id("run_id", args.run_id),
        "lease_id": _required_id("lease_id", args.lease_id),
        "lease_generation": _positive_int("lease_generation", args.lease_generation),
        "phase": args.phase,
        "coordinator_thread_id": _required_id(
            "coordinator_thread_id", args.coordinator_thread_id
        ),
        "coordinator_host_id": _required_id(
            "coordinator_host_id", args.coordinator_host_id
        ),
        "action": args.action,
        "checkpoint_status": args.checkpoint_status,
        "outstanding_job_ids": outstanding,
        "reason": _required_text("reason", args.reason, 2000),
    }

    def action(conn, now):
        auth = {
            "run_id": request["run_id"],
            "lease_id": request["lease_id"],
            "lease_generation": request["lease_generation"],
            "phase": request["phase"],
            "thread_id": request["coordinator_thread_id"],
            "host_id": request["coordinator_host_id"],
        }
        authorized, reasons, lease = _run_lease_authorization(conn, auth)
        if not authorized:
            return ({"ok": False, "status": "lease_denied", "reasons": reasons}, 4, [])
        in_flight = conn.execute(
            "SELECT call_id FROM call_permits WHERE lease_generation=? "
            "AND state='in_flight'",
            (request["lease_generation"],),
        ).fetchone()
        if in_flight is not None:
            return (
                {
                    "ok": False,
                    "status": "valmera_call_in_flight",
                    "call_id": in_flight["call_id"],
                },
                5,
                [],
            )
        known_jobs = (
            json.loads(lease["outstanding_job_ids_json"])
            if lease["outstanding_job_ids_json"] is not None
            else []
        )
        if outstanding or known_jobs:
            effective_jobs = outstanding or known_jobs
            conn.execute(
                "UPDATE leases SET outstanding_job_ids_json=?,updated_at_s=? "
                "WHERE lease_generation=?",
                (_json_text(effective_jobs), now, request["lease_generation"]),
            )
            return (
                {
                    "ok": False,
                    "status": "outstanding_jobs_present",
                    "outstanding_job_ids": effective_jobs,
                },
                5,
                [],
            )
        snapshot = conn.execute(
            "SELECT * FROM run_snapshots WHERE lease_generation=?",
            (request["lease_generation"],),
        ).fetchone()
        if (
            snapshot is None
            or snapshot["phase"] != request["phase"]
            or snapshot["checkpoint_status"] != request["checkpoint_status"]
        ):
            return ({"ok": False, "status": "safe_snapshot_missing_or_mismatched"}, 5, [])
        terminal_state = {
            "release": "released",
            "freeze": "frozen",
            "revoke": "revoked",
        }[request["action"]]
        conn.execute(
            "UPDATE leases SET state=?,checkpoint_status=?,outstanding_job_ids_json='[]',"
            "close_reason=?,updated_at_s=?,closed_at_s=? WHERE lease_generation=?",
            (
                terminal_state,
                request["checkpoint_status"],
                request["reason"],
                now,
                now,
                request["lease_generation"],
            ),
        )
        if request["checkpoint_status"] in ("blocked", "paused_safe"):
            run_status, run_phase = "paused_safe", request["phase"]
        else:
            run_status, run_phase = "active", request["phase"]
        conn.execute(
            "UPDATE runs SET status=?,phase=?,updated_at_s=? WHERE run_id=?",
            (run_status, run_phase, now, request["run_id"]),
        )
        event = {
            "event": f"run_lease_{terminal_state}",
            "run_id": request["run_id"],
            "lease_generation": request["lease_generation"],
            "details": {
                "phase": request["phase"],
                "checkpoint_status": request["checkpoint_status"],
                "reason": request["reason"],
            },
        }
        return (
            {
                "ok": True,
                "status": terminal_state,
                "lease_id": request["lease_id"],
                "lease_generation": request["lease_generation"],
                "phase": request["phase"],
                "checkpoint_status": request["checkpoint_status"],
                "outstanding_job_ids": [],
            },
            0,
            [event],
        )

    return _mutate(path, "close-run-lease", args.op_id, request, action)


def bind_run(args: argparse.Namespace, path: Path) -> int:
    request = {
        "run_id": _required_id("run_id", args.run_id),
        "parent_project_id": _positive_int("parent_project_id", args.parent_project_id),
        "selection_fingerprint": _fingerprint(
            "selection_fingerprint", args.selection_fingerprint
        ),
    }

    def action(conn, now):
        row = _run(conn, request["run_id"])
        if row is None:
            return ({"ok": False, "status": "run_not_found"}, 3, [])
        if row["status"] in ("ready", "blocked", "abstained"):
            return ({"ok": False, "status": "run_terminal"}, 5, [])
        if row["coordinator_result_json"] is not None:
            return ({"ok": False, "status": "run_result_frozen_pending_finalization"}, 5, [])
        if row["parent_project_id"] is not None:
            same = (
                row["parent_project_id"] == request["parent_project_id"]
                and row["selection_fingerprint"] == request["selection_fingerprint"]
            )
            return (
                {
                    "ok": same,
                    "status": "already_bound" if same else "run_binding_conflict",
                    "run_id": request["run_id"],
                    "parent_project_id": row["parent_project_id"],
                    "selection_fingerprint": row["selection_fingerprint"],
                },
                0 if same else 4,
                [],
            )
        duplicate = conn.execute(
            "SELECT run_id FROM runs WHERE parent_project_id=?",
            (request["parent_project_id"],),
        ).fetchone()
        if duplicate is not None:
            return (
                {
                    "ok": False,
                    "status": "parent_already_bound",
                    "other_run_id": duplicate["run_id"],
                },
                4,
                [],
            )
        child_collision = _child(conn, request["parent_project_id"])
        if child_collision is not None:
            return (
                {
                    "ok": False,
                    "status": "parent_project_is_registered_child",
                    "child_run_id": child_collision["run_id"],
                },
                4,
                [],
            )
        acquisition = _latest_snapshot(conn, request["run_id"], "acquisition")
        selection = _latest_snapshot(conn, request["run_id"], "selection")
        if acquisition is None or acquisition["checkpoint_status"] != "ready":
            return ({"ok": False, "status": "acquisition_snapshot_not_ready"}, 5, [])
        acquisition_data = _trusted_snapshot_data(
            acquisition, "source-acquisition-checkpoint-v1"
        )
        if acquisition_data.get("parent_project_id") != request["parent_project_id"]:
            return ({"ok": False, "status": "acquisition_parent_mismatch"}, 4, [])
        if selection is None or selection["checkpoint_status"] != "ready":
            return ({"ok": False, "status": "selection_snapshot_not_ready"}, 5, [])
        selection_data = _trusted_snapshot_data(selection, "selection")
        frozen_fingerprint, _, _ = _selection_manifest(selection_data, validate=False)
        if frozen_fingerprint != request["selection_fingerprint"]:
            return ({"ok": False, "status": "selection_snapshot_mismatch"}, 4, [])
        if selection_data.get("parent_project_id") != request["parent_project_id"]:
            return ({"ok": False, "status": "selection_parent_mismatch"}, 4, [])
        conn.execute(
            "UPDATE runs SET parent_project_id=?,selection_fingerprint=?,updated_at_s=? "
            "WHERE run_id=?",
            (
                request["parent_project_id"],
                request["selection_fingerprint"],
                now,
                request["run_id"],
            ),
        )
        event = {
            "event": "run_bound",
            "run_id": request["run_id"],
            "details": {
                "parent_project_id": request["parent_project_id"],
                "selection_fingerprint": request["selection_fingerprint"],
            },
        }
        return ({"ok": True, "status": "bound", **request}, 0, [event])

    return _mutate(path, "bind-run", args.op_id, request, action)


def _validated_task_assignment_context(
    conn: sqlite3.Connection, request: dict, run: sqlite3.Row
) -> tuple[str, str]:
    materialization = _latest_snapshot(conn, request["run_id"], "materialization")
    selection = _latest_snapshot(conn, request["run_id"], "selection")
    if (
        materialization is None
        or materialization["checkpoint_status"] != "ready"
        or selection is None
        or selection["checkpoint_status"] != "ready"
    ):
        raise ValueError("materialization snapshot is not ready for task intent")
    selection_data = _trusted_snapshot_data(selection, "selection")
    materialized = _materialization_manifest(
        _trusted_snapshot_data(materialization, "materialization-v1"),
        selection_data,
        validate=False,
    )
    expected = next(
        (item for item in materialized if item["card"] == request["card"]), None
    )
    if expected is None or (
        expected["status"] != "materialized"
        or expected["child_project_id"] != request["child_project_id"]
        or expected["title"] != request["title"]
    ):
        raise ValueError("task intent child is not in frozen materialization")
    for prior in materialized:
        if prior["card"] >= request["card"]:
            break
        if prior["status"] == "failed":
            continue
        prior_child = _child(conn, prior["child_project_id"])
        if prior_child is None or prior_child["qc_status"] not in ("pass", "blocked"):
            raise ValueError(
                f"task intent violates FIFO: prior card {prior['card']} is not terminal"
            )
    acquisition_record = _latest_snapshot(
        conn, request["run_id"], "acquisition_record"
    )
    reference = _latest_snapshot(conn, request["run_id"], "reference")
    if (
        acquisition_record is None
        or acquisition_record["checkpoint_status"] != "ready"
        or reference is None
        or reference["checkpoint_status"] != "ready"
    ):
        raise ValueError("assignment lineage is not ready for task intent")
    acquisition_data = _trusted_snapshot_data(
        acquisition_record, "acquisition-record"
    )
    reference_data = _trusted_snapshot_data(reference, "reference-profile")
    assignment_digest = _validate_normative(
        "child-assignment",
        request["assignment"],
        against=[selection_data, acquisition_data, reference_data],
    )
    assignment_expected = {
        "assignment_id": request["assignment_id"],
        "assignment_input_fingerprint": request["assignment_fingerprint"],
        "run_id": request["run_id"],
        "parent_project_id": run["parent_project_id"],
        "child_project_id": request["child_project_id"],
        "card": request["card"],
        "title": request["title"],
    }
    mismatches = [
        key
        for key, value in assignment_expected.items()
        if request["assignment"].get(key) != value
    ]
    if mismatches:
        raise ValueError(
            "task intent assignment disagrees with frozen identity fields: "
            + ", ".join(mismatches)
        )
    assignment_source_expected = {
        "youtube_video_id": acquisition_data["source"]["youtube_video_id"],
        "asset_id": acquisition_data["source"]["asset_id"],
        "source_duration_s": acquisition_data["source"]["duration_s"],
        "approved_start_s": expected["approved_start_s"],
        "approved_end_s": expected["approved_end_s"],
        "seeded_child_start_s": expected["seeded_child_start_s"],
        "seeded_child_end_s": expected["seeded_child_end_s"],
        "seed_snap_reason": expected["seed_snap_reason"],
        "seed_range_verified_by": expected["seed_range_verified_by"],
        "seed_range_evidence_digest": expected["seed_range_evidence_digest"],
    }
    source = request["assignment"].get("source")
    source_mismatches = [
        key
        for key, value in assignment_source_expected.items()
        if not isinstance(source, dict) or source.get(key) != value
    ]
    if source_mismatches:
        raise ValueError(
            "task intent assignment source disagrees with acquisition/materialization: "
            + ", ".join(source_mismatches)
        )
    assignment_status = request["assignment"].get("assignment_status")
    assignment_recast_status = request["assignment"].get(
        "pre_mutation_recast_status"
    )
    if assignment_status == "ready_for_editor":
        if assignment_recast_status != "not_required":
            raise ValueError("ready assignment must not require a recast")
        registry_recast_status = "not_required"
    elif assignment_status == "requires_pre_mutation_recast":
        if assignment_recast_status != "pending_parent_approval":
            raise ValueError("ambiguous assignment must await parent recast approval")
        registry_recast_status = "pending_parent_approval"
    elif assignment_status == "blocked_before_mutation":
        if assignment_recast_status != "blocked":
            raise ValueError("blocked assignment must carry blocked recast status")
        registry_recast_status = "blocked"
    else:
        raise ValueError("unsupported child assignment status")
    return assignment_digest, registry_recast_status


def prepare_child_task_intent(args: argparse.Namespace, path: Path) -> int:
    assignment = _json_value("assignment_json", args.assignment_json, dict)
    request = {
        "client_request_id": _required_id(
            "client_request_id", args.client_request_id
        ),
        "run_id": _required_id("run_id", args.run_id),
        "selection_fingerprint": _fingerprint(
            "selection_fingerprint", args.selection_fingerprint
        ),
        "child_project_id": _positive_int("child_project_id", args.child_project_id),
        "card": _positive_int("card", args.card),
        "title": _required_text("title", args.title, 1000),
        "assignment_id": _required_id("assignment_id", args.assignment_id),
        "assignment_fingerprint": _fingerprint(
            "assignment_fingerprint", args.assignment_fingerprint
        ),
        "assignment": assignment,
        "coordinator_thread_id": _required_id(
            "coordinator_thread_id", args.coordinator_thread_id
        ),
        "coordinator_host_id": _required_id(
            "coordinator_host_id", args.coordinator_host_id
        ),
    }

    def action(conn, now):
        run = _run(conn, request["run_id"])
        if run is None:
            return ({"ok": False, "status": "run_not_found"}, 3, [])
        if (
            run["coordinator_thread_id"] != request["coordinator_thread_id"]
            or run["coordinator_host_id"] != request["coordinator_host_id"]
        ):
            return ({"ok": False, "status": "coordinator_identity_mismatch"}, 4, [])
        if run["parent_project_id"] is None or run["selection_fingerprint"] is None:
            return ({"ok": False, "status": "run_not_bound"}, 5, [])
        if run["selection_fingerprint"] != request["selection_fingerprint"]:
            return ({"ok": False, "status": "stale_selection_fingerprint"}, 4, [])
        if run["status"] in ("ready", "blocked", "abstained"):
            return ({"ok": False, "status": "run_terminal"}, 5, [])
        if run["coordinator_result_json"] is not None:
            return ({"ok": False, "status": "run_result_frozen_pending_finalization"}, 5, [])
        if _latest_blocked_phase(conn, request["run_id"]) is not None:
            return ({"ok": False, "status": "blocked_phase_pending_finalization"}, 5, [])
        if _active_lease(conn) is not None:
            return ({"ok": False, "status": "global_live_lease_exists"}, 5, [])
        if _child(conn, request["child_project_id"]) is not None:
            return ({"ok": False, "status": "child_already_registered"}, 4, [])
        parent_collision = conn.execute(
            "SELECT run_id FROM runs WHERE parent_project_id=?",
            (request["child_project_id"],),
        ).fetchone()
        if parent_collision is not None:
            return (
                {
                    "ok": False,
                    "status": "child_project_is_registered_parent",
                    "parent_run_id": parent_collision["run_id"],
                },
                4,
                [],
            )
        collision = conn.execute(
            "SELECT client_request_id FROM child_task_intents WHERE "
            "client_request_id=? OR child_project_id=? OR assignment_id=? OR "
            "(run_id=? AND card=?)",
            (
                request["client_request_id"],
                request["child_project_id"],
                request["assignment_id"],
                request["run_id"],
                request["card"],
            ),
        ).fetchone()
        if collision is not None:
            return (
                {
                    "ok": False,
                    "status": "task_intent_replacement_refused",
                    "existing_client_request_id": collision["client_request_id"],
                },
                4,
                [],
            )
        assignment_digest, _ = _validated_task_assignment_context(
            conn, request, run
        )
        marker = _task_marker(request["client_request_id"])
        conn.execute(
            "INSERT INTO child_task_intents(client_request_id,task_marker,run_id,"
            "parent_project_id,selection_fingerprint,child_project_id,card,title,"
            "assignment_id,assignment_fingerprint,assignment_json,"
            "assignment_validated_digest,state,prepared_at_s,updated_at_s) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                request["client_request_id"],
                marker,
                request["run_id"],
                run["parent_project_id"],
                request["selection_fingerprint"],
                request["child_project_id"],
                request["card"],
                request["title"],
                request["assignment_id"],
                request["assignment_fingerprint"],
                _json_text(request["assignment"]),
                assignment_digest,
                "prepared",
                now,
                now,
            ),
        )
        event = {
            "event": "child_task_intent_prepared",
            "run_id": request["run_id"],
            "child_project_id": request["child_project_id"],
            "details": {
                "client_request_id": request["client_request_id"],
                "task_marker": marker,
                "assignment_id": request["assignment_id"],
                "assignment_validated_digest": assignment_digest,
            },
        }
        return (
            {
                "ok": True,
                "status": "task_intent_prepared",
                "client_request_id": request["client_request_id"],
                "task_marker": marker,
                "next_action": "begin_child_task_create",
                "assignment_validated_digest": assignment_digest,
            },
            0,
            [event],
        )

    return _mutate(path, "prepare-child-task-intent", args.op_id, request, action)


def _task_intent_transition_request(args: argparse.Namespace) -> dict:
    return {
        "client_request_id": _required_id(
            "client_request_id", args.client_request_id
        ),
        "run_id": _required_id("run_id", args.run_id),
        "child_project_id": _positive_int("child_project_id", args.child_project_id),
        "card": _positive_int("card", args.card),
        "assignment_id": _required_id("assignment_id", args.assignment_id),
        "assignment_fingerprint": _fingerprint(
            "assignment_fingerprint", args.assignment_fingerprint
        ),
        "coordinator_thread_id": _required_id(
            "coordinator_thread_id", args.coordinator_thread_id
        ),
        "coordinator_host_id": _required_id(
            "coordinator_host_id", args.coordinator_host_id
        ),
    }


def _authorize_task_intent_transition(
    conn: sqlite3.Connection, request: dict
) -> tuple[sqlite3.Row | None, sqlite3.Row | None, list[str]]:
    run = _run(conn, request["run_id"])
    intent = _task_intent(conn, request["client_request_id"])
    reasons = []
    if run is None:
        reasons.append("run_not_found")
    elif (
        run["coordinator_thread_id"] != request["coordinator_thread_id"]
        or run["coordinator_host_id"] != request["coordinator_host_id"]
    ):
        reasons.append("coordinator_identity_mismatch")
    if intent is None:
        reasons.append("task_intent_not_found")
    else:
        expected = {
            "run_id": request["run_id"],
            "child_project_id": request["child_project_id"],
            "card": request["card"],
            "assignment_id": request["assignment_id"],
            "assignment_fingerprint": request["assignment_fingerprint"],
        }
        reasons.extend(
            f"{key}_mismatch"
            for key, value in expected.items()
            if intent[key] != value
        )
    return run, intent, reasons


def begin_child_task_create(args: argparse.Namespace, path: Path) -> int:
    request = _task_intent_transition_request(args)

    def action(conn, now):
        run, intent, reasons = _authorize_task_intent_transition(conn, request)
        if reasons:
            return ({"ok": False, "status": "task_intent_denied", "reasons": reasons}, 4, [])
        if run["status"] in ("ready", "blocked", "abstained") or run[
            "coordinator_result_json"
        ] is not None:
            return ({"ok": False, "status": "run_frozen_or_terminal"}, 5, [])
        if _active_lease(conn) is not None:
            return ({"ok": False, "status": "global_live_lease_exists"}, 5, [])
        if intent["state"] != "prepared":
            return (
                {
                    "ok": False,
                    "status": "task_create_already_claimed_never_retry",
                    "intent_state": intent["state"],
                    "task_marker": intent["task_marker"],
                },
                5,
                [],
            )
        conn.execute(
            "UPDATE child_task_intents SET state='dispatching',create_started_at_s=?,"
            "updated_at_s=? WHERE client_request_id=?",
            (now, now, request["client_request_id"]),
        )
        event = {
            "event": "child_task_create_claimed",
            "run_id": request["run_id"],
            "child_project_id": request["child_project_id"],
            "details": {
                "client_request_id": request["client_request_id"],
                "task_marker": intent["task_marker"],
            },
        }
        return (
            {
                "ok": True,
                "status": "task_create_claimed",
                "client_request_id": request["client_request_id"],
                "task_marker": intent["task_marker"],
                "next_action": "call_create_thread_exactly_once",
            },
            0,
            [event],
        )

    return _mutate(path, "begin-child-task-create", args.op_id, request, action)


def record_child_task_queued(args: argparse.Namespace, path: Path) -> int:
    request = {
        **_task_intent_transition_request(args),
        "client_thread_id": _required_id("client_thread_id", args.client_thread_id),
    }

    def action(conn, now):
        _, intent, reasons = _authorize_task_intent_transition(conn, request)
        if reasons:
            return ({"ok": False, "status": "task_intent_denied", "reasons": reasons}, 4, [])
        if intent["state"] == "queued":
            same = intent["client_thread_id"] == request["client_thread_id"]
            return (
                {
                    "ok": same,
                    "status": "task_client_id_already_recorded" if same else "task_client_id_conflict",
                    "client_thread_id": intent["client_thread_id"],
                },
                0 if same else 4,
                [],
            )
        if intent["state"] != "dispatching":
            return ({"ok": False, "status": "task_intent_not_dispatching"}, 5, [])
        collision = conn.execute(
            "SELECT client_request_id FROM child_task_intents WHERE "
            "client_thread_id=? AND client_request_id!=?",
            (request["client_thread_id"], request["client_request_id"]),
        ).fetchone()
        if collision is not None:
            return ({"ok": False, "status": "client_thread_id_collision"}, 4, [])
        conn.execute(
            "UPDATE child_task_intents SET state='queued',client_thread_id=?,"
            "queued_at_s=?,updated_at_s=? WHERE client_request_id=?",
            (request["client_thread_id"], now, now, request["client_request_id"]),
        )
        event = {
            "event": "child_task_queued",
            "run_id": request["run_id"],
            "child_project_id": request["child_project_id"],
            "details": {
                "client_request_id": request["client_request_id"],
                "client_thread_id": request["client_thread_id"],
            },
        }
        return (
            {
                "ok": True,
                "status": "task_client_id_recorded",
                "client_thread_id": request["client_thread_id"],
                "next_action": "resolve_client_thread",
            },
            0,
            [event],
        )

    return _mutate(path, "record-child-task-queued", args.op_id, request, action)


def resolve_child_task_intent(args: argparse.Namespace, path: Path) -> int:
    client_thread_id = args.client_thread_id
    if client_thread_id is not None:
        client_thread_id = _required_id("client_thread_id", client_thread_id)
    request = {
        **_task_intent_transition_request(args),
        "client_thread_id": client_thread_id,
        "thread_id": _required_id("thread_id", args.thread_id),
        "host_id": _required_id("host_id", args.host_id),
    }

    def action(conn, now):
        _, intent, reasons = _authorize_task_intent_transition(conn, request)
        if reasons:
            return ({"ok": False, "status": "task_intent_denied", "reasons": reasons}, 4, [])
        coordinator_mapping = _coordinator_mapping_for_task_identity(
            conn, request["host_id"], request["thread_id"]
        )
        if coordinator_mapping is not None:
            return (
                {
                    "ok": False,
                    "status": "child_identity_is_coordinator_task",
                    **coordinator_mapping,
                },
                4,
                [],
            )
        if intent["state"] in ("resolved", "consumed"):
            same = (
                intent["thread_id"] == request["thread_id"]
                and intent["host_id"] == request["host_id"]
                and intent["client_thread_id"] == request["client_thread_id"]
            )
            return (
                {
                    "ok": same,
                    "status": "task_already_resolved" if same else "task_resolution_conflict",
                    "intent_state": intent["state"],
                },
                0 if same else 4,
                [],
            )
        if intent["state"] == "dispatching":
            if request["client_thread_id"] is not None:
                return (
                    {"ok": False, "status": "direct_resolution_must_omit_client_thread_id"},
                    4,
                    [],
                )
        elif intent["state"] == "queued":
            if request["client_thread_id"] != intent["client_thread_id"]:
                return ({"ok": False, "status": "queued_client_thread_id_mismatch"}, 4, [])
        else:
            return ({"ok": False, "status": "task_create_not_claimed"}, 5, [])
        child_collision = conn.execute(
            "SELECT child_project_id FROM children WHERE host_id=? AND thread_id=?",
            (request["host_id"], request["thread_id"]),
        ).fetchone()
        intent_collision = conn.execute(
            "SELECT client_request_id FROM child_task_intents WHERE host_id=? AND "
            "thread_id=? AND client_request_id!=?",
            (request["host_id"], request["thread_id"], request["client_request_id"]),
        ).fetchone()
        if child_collision is not None or intent_collision is not None:
            return ({"ok": False, "status": "visible_task_identity_collision"}, 4, [])
        conn.execute(
            "UPDATE child_task_intents SET state='resolved',thread_id=?,host_id=?,"
            "resolved_at_s=?,updated_at_s=? WHERE client_request_id=?",
            (
                request["thread_id"],
                request["host_id"],
                now,
                now,
                request["client_request_id"],
            ),
        )
        event = {
            "event": "child_task_resolved",
            "run_id": request["run_id"],
            "child_project_id": request["child_project_id"],
            "details": {
                "client_request_id": request["client_request_id"],
                "client_thread_id": request["client_thread_id"],
                "thread_id": request["thread_id"],
                "host_id": request["host_id"],
            },
        }
        return (
            {
                "ok": True,
                "status": "task_intent_resolved",
                "thread_id": request["thread_id"],
                "host_id": request["host_id"],
                "next_action": "register_child",
            },
            0,
            [event],
        )

    return _mutate(path, "resolve-child-task-intent", args.op_id, request, action)


def register_child(args: argparse.Namespace, path: Path) -> int:
    assignment = _json_value("assignment_json", args.assignment_json, dict)
    request = {
        "run_id": _required_id("run_id", args.run_id),
        "selection_fingerprint": _fingerprint(
            "selection_fingerprint", args.selection_fingerprint
        ),
        "child_project_id": _positive_int("child_project_id", args.child_project_id),
        "card": _positive_int("card", args.card),
        "title": _required_text("title", args.title, 1000),
        "thread_id": _required_id("thread_id", args.thread_id),
        "host_id": _required_id("host_id", args.host_id),
        "assignment_id": _required_id("assignment_id", args.assignment_id),
        "assignment_fingerprint": _fingerprint(
            "assignment_fingerprint", args.assignment_fingerprint
        ),
        "client_request_id": _required_id(
            "client_request_id", args.client_request_id
        ),
        "assignment": assignment,
    }

    def action(conn, now):
        run = _run(conn, request["run_id"])
        if run is None:
            return ({"ok": False, "status": "run_not_found"}, 3, [])
        if run["parent_project_id"] is None or run["selection_fingerprint"] is None:
            return ({"ok": False, "status": "run_not_bound"}, 5, [])
        if run["selection_fingerprint"] != request["selection_fingerprint"]:
            return ({"ok": False, "status": "stale_selection_fingerprint"}, 4, [])
        if run["status"] in ("ready", "blocked", "abstained"):
            return ({"ok": False, "status": "run_terminal"}, 5, [])
        if run["coordinator_result_json"] is not None:
            return ({"ok": False, "status": "run_result_frozen_pending_finalization"}, 5, [])
        coordinator_mapping = _coordinator_mapping_for_task_identity(
            conn, request["host_id"], request["thread_id"]
        )
        if coordinator_mapping is not None:
            return (
                {
                    "ok": False,
                    "status": "child_identity_is_coordinator_task",
                    **coordinator_mapping,
                },
                4,
                [],
            )
        parent_collision = conn.execute(
            "SELECT run_id FROM runs WHERE parent_project_id=?",
            (request["child_project_id"],),
        ).fetchone()
        if parent_collision is not None:
            return (
                {
                    "ok": False,
                    "status": "child_project_is_registered_parent",
                    "parent_run_id": parent_collision["run_id"],
                },
                4,
                [],
            )
        intent = _task_intent(conn, request["client_request_id"])
        if intent is None:
            return ({"ok": False, "status": "task_intent_not_recorded"}, 5, [])
        intent_expected = {
            "run_id": request["run_id"],
            "parent_project_id": run["parent_project_id"],
            "selection_fingerprint": request["selection_fingerprint"],
            "child_project_id": request["child_project_id"],
            "card": request["card"],
            "title": request["title"],
            "assignment_id": request["assignment_id"],
            "assignment_fingerprint": request["assignment_fingerprint"],
            "assignment_json": _json_text(request["assignment"]),
        }
        intent_mismatches = [
            key for key, value in intent_expected.items() if intent[key] != value
        ]
        if intent_mismatches:
            return (
                {
                    "ok": False,
                    "status": "task_intent_identity_mismatch",
                    "mismatches": intent_mismatches,
                },
                4,
                [],
            )
        if intent["state"] not in ("resolved", "consumed"):
            return (
                {
                    "ok": False,
                    "status": "task_intent_not_resolved",
                    "intent_state": intent["state"],
                },
                5,
                [],
            )
        task_identity_mismatches = [
            key
            for key in ("thread_id", "host_id")
            if intent[key] != request[key]
        ]
        if task_identity_mismatches:
            return (
                {
                    "ok": False,
                    "status": "task_intent_identity_mismatch",
                    "mismatches": task_identity_mismatches,
                },
                4,
                [],
            )
        materialization = _latest_snapshot(conn, request["run_id"], "materialization")
        selection = _latest_snapshot(conn, request["run_id"], "selection")
        if (
            materialization is None
            or materialization["checkpoint_status"] != "ready"
            or selection is None
            or selection["checkpoint_status"] != "ready"
        ):
            return ({"ok": False, "status": "materialization_snapshot_not_ready"}, 5, [])
        selection_data = _trusted_snapshot_data(selection, "selection")
        materialization_data = _trusted_snapshot_data(
            materialization, "materialization-v1"
        )
        materialized = _materialization_manifest(
            materialization_data, selection_data, validate=False
        )
        expected = next(
            (item for item in materialized if item["card"] == request["card"]), None
        )
        if expected is None or (
            expected["status"] != "materialized"
            or expected["child_project_id"] != request["child_project_id"]
            or expected["title"] != request["title"]
        ):
            return ({"ok": False, "status": "child_not_in_frozen_materialization"}, 4, [])
        acquisition_record = _latest_snapshot(
            conn, request["run_id"], "acquisition_record"
        )
        reference = _latest_snapshot(conn, request["run_id"], "reference")
        if (
            acquisition_record is None
            or acquisition_record["checkpoint_status"] != "ready"
            or reference is None
            or reference["checkpoint_status"] != "ready"
        ):
            return ({"ok": False, "status": "assignment_lineage_not_ready"}, 5, [])
        acquisition_data = _trusted_snapshot_data(
            acquisition_record, "acquisition-record"
        )
        reference_data = _trusted_snapshot_data(reference, "reference-profile")
        assignment_digest = _validate_normative(
            "child-assignment",
            request["assignment"],
            against=[selection_data, acquisition_data, reference_data],
        )
        if intent["assignment_validated_digest"] != assignment_digest:
            raise ValueError(
                "resolved task intent assignment digest does not match revalidation"
            )
        assignment_expected = {
            "assignment_id": request["assignment_id"],
            "assignment_input_fingerprint": request["assignment_fingerprint"],
            "run_id": request["run_id"],
            "parent_project_id": run["parent_project_id"],
            "child_project_id": request["child_project_id"],
            "card": request["card"],
            "title": request["title"],
        }
        mismatches = [
            key
            for key, value in assignment_expected.items()
            if request["assignment"].get(key) != value
        ]
        if mismatches:
            raise ValueError(
                "assignment JSON disagrees with CLI/frozen identity fields: "
                + ", ".join(mismatches)
            )
        assignment_source_expected = {
            "youtube_video_id": acquisition_data["source"]["youtube_video_id"],
            "asset_id": acquisition_data["source"]["asset_id"],
            "source_duration_s": acquisition_data["source"]["duration_s"],
            "approved_start_s": expected["approved_start_s"],
            "approved_end_s": expected["approved_end_s"],
            "seeded_child_start_s": expected["seeded_child_start_s"],
            "seeded_child_end_s": expected["seeded_child_end_s"],
            "seed_snap_reason": expected["seed_snap_reason"],
            "seed_range_verified_by": expected["seed_range_verified_by"],
            "seed_range_evidence_digest": expected["seed_range_evidence_digest"],
        }
        source = request["assignment"].get("source")
        source_mismatches = [
            key
            for key, value in assignment_source_expected.items()
            if not isinstance(source, dict) or source.get(key) != value
        ]
        if source_mismatches:
            raise ValueError(
                "assignment source disagrees with acquisition/materialization: "
                + ", ".join(source_mismatches)
            )
        assignment_status = request["assignment"].get("assignment_status")
        assignment_recast_status = request["assignment"].get(
            "pre_mutation_recast_status"
        )
        if assignment_status == "ready_for_editor":
            if assignment_recast_status != "not_required":
                raise ValueError(
                    "a pre-approved assignment requires separately persisted recast evidence"
                )
            registry_recast_status = "not_required"
        elif assignment_status == "requires_pre_mutation_recast":
            if assignment_recast_status != "pending_parent_approval":
                raise ValueError("ambiguous assignment must await parent recast approval")
            registry_recast_status = "pending_parent_approval"
        elif assignment_status == "blocked_before_mutation":
            if assignment_recast_status != "blocked":
                raise ValueError("blocked assignment must carry blocked recast status")
            registry_recast_status = "blocked"
        else:
            raise ValueError("unsupported child assignment status")
        old = _child(conn, request["child_project_id"])
        compare_keys = (
            "run_id", "card", "title", "thread_id", "host_id", "assignment_id",
            "assignment_fingerprint",
        )
        if old is not None:
            same = all(old[key] == request[key] for key in compare_keys) and (
                old["assignment_json"] == _json_text(request["assignment"])
                and old["assignment_validated_digest"] == assignment_digest
                and intent["state"] == "consumed"
            )
            return (
                {
                    "ok": same,
                    "status": "already_registered" if same else "child_mapping_conflict",
                    "child_project_id": request["child_project_id"],
                },
                0 if same else 4,
                [],
            )
        if intent["state"] != "resolved":
            return ({"ok": False, "status": "consumed_intent_missing_child"}, 6, [])
        collision = conn.execute(
            "SELECT child_project_id FROM children WHERE assignment_id=? OR "
            "(host_id=? AND thread_id=?) OR (run_id=? AND card=?)",
            (
                request["assignment_id"],
                request["host_id"],
                request["thread_id"],
                request["run_id"],
                request["card"],
            ),
        ).fetchone()
        if collision is not None:
            return (
                {
                    "ok": False,
                    "status": "child_identity_collision",
                    "conflicting_child_project_id": collision["child_project_id"],
                },
                4,
                [],
            )
        conn.execute(
            "INSERT INTO children(child_project_id,run_id,card,title,thread_id,host_id,"
            "assignment_id,assignment_fingerprint,assignment_json,"
            "assignment_validated_digest,recast_approval_status,child_status,qc_status,"
            "repair_round,task_state,exclusions_json,created_at_s,updated_at_s) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                request["child_project_id"],
                request["run_id"],
                request["card"],
                request["title"],
                request["thread_id"],
                request["host_id"],
                request["assignment_id"],
                request["assignment_fingerprint"],
                _json_text(request["assignment"]),
                assignment_digest,
                registry_recast_status,
                "queued",
                "pending",
                0,
                "frozen",
                "{}",
                now,
                now,
            ),
        )
        conn.execute(
            "UPDATE child_task_intents SET state='consumed',consumed_at_s=?,"
            "updated_at_s=? WHERE client_request_id=? AND state='resolved'",
            (now, now, request["client_request_id"]),
        )
        event = {
            "event": "child_registered",
            "run_id": request["run_id"],
            "child_project_id": request["child_project_id"],
            "details": {
                **{key: request[key] for key in compare_keys},
                "client_request_id": request["client_request_id"],
            },
        }
        return (
            {
                "ok": True,
                "status": "registered",
                **{key: request[key] for key in compare_keys},
                "client_request_id": request["client_request_id"],
                "assignment_validated_digest": assignment_digest,
                "recast_approval_status": registry_recast_status,
            },
            0,
            [event],
        )

    return _mutate(path, "register-child", args.op_id, request, action)


def _recast_identity(run: sqlite3.Row, child: sqlite3.Row) -> dict:
    return {
        "assignment_id": child["assignment_id"],
        "assignment_input_fingerprint": child["assignment_fingerprint"],
        "run_id": run["run_id"],
        "parent_project_id": run["parent_project_id"],
        "child_project_id": child["child_project_id"],
    }


def record_recast_input(args: argparse.Namespace, path: Path) -> int:
    recast = _json_value("recast_json", args.recast_json, dict)
    request = {
        "run_id": _required_id("run_id", args.run_id),
        "child_project_id": _positive_int("child_project_id", args.child_project_id),
        "coordinator_thread_id": _required_id(
            "coordinator_thread_id", args.coordinator_thread_id
        ),
        "coordinator_host_id": _required_id(
            "coordinator_host_id", args.coordinator_host_id
        ),
        "recast": recast,
    }

    def action(conn, now):
        run = _run(conn, request["run_id"])
        child = _child(conn, request["child_project_id"])
        if run is None or child is None or child["run_id"] != request["run_id"]:
            return ({"ok": False, "status": "run_or_child_not_found"}, 3, [])
        if run["status"] in ("ready", "blocked", "abstained"):
            return ({"ok": False, "status": "run_terminal"}, 5, [])
        if run["coordinator_result_json"] is not None:
            return ({"ok": False, "status": "run_result_frozen_pending_finalization"}, 5, [])
        if (
            run["coordinator_thread_id"] != request["coordinator_thread_id"]
            or run["coordinator_host_id"] != request["coordinator_host_id"]
        ):
            return ({"ok": False, "status": "coordinator_identity_mismatch"}, 4, [])
        assignment = _trusted_child_artifact(
            child, "assignment_json", "assignment_validated_digest", "child assignment"
        )
        if assignment.get("assignment_status") != "requires_pre_mutation_recast":
            return ({"ok": False, "status": "assignment_does_not_require_recast"}, 5, [])
        if request["recast"].get("status") != "awaiting_parent_approval":
            raise ValueError("recast input must be the awaiting_parent_approval artifact")
        digest = _validate_normative(
            "pre-mutation-recast", request["recast"], against=[assignment]
        )
        expected = _recast_identity(run, child)
        mismatches = [
            key for key, value in expected.items()
            if request["recast"].get(key) != value
        ]
        if mismatches:
            raise ValueError("recast input identity mismatch: " + ", ".join(mismatches))
        if child["recast_input_json"] is not None:
            same = (
                child["recast_input_json"] == _json_text(request["recast"])
                and child["recast_input_validated_digest"] == digest
            )
            return (
                {"ok": same, "status": "recast_input_already_recorded" if same else "recast_input_conflict"},
                0 if same else 4,
                [],
            )
        conn.execute(
            "UPDATE children SET recast_input_json=?,recast_input_validated_digest=?,"
            "updated_at_s=? WHERE child_project_id=?",
            (_json_text(request["recast"]), digest, now, request["child_project_id"]),
        )
        event = {
            "event": "pre_mutation_recast_input_recorded",
            "run_id": request["run_id"],
            "child_project_id": request["child_project_id"],
            "details": {
                "recast_input_fingerprint": request["recast"].get(
                    "recast_input_fingerprint"
                ),
                "validated_digest": digest,
            },
        }
        return (
            {"ok": True, "status": "recast_input_recorded", "validated_digest": digest},
            0,
            [event],
        )

    return _mutate(path, "record-recast-input", args.op_id, request, action)


def record_recast(args: argparse.Namespace, path: Path) -> int:
    recast = _json_value("recast_json", args.recast_json, dict)
    approved_candidate_id = args.approved_candidate_id
    if approved_candidate_id is not None:
        approved_candidate_id = _required_id(
            "approved_candidate_id", approved_candidate_id
        )
    request = {
        "run_id": _required_id("run_id", args.run_id),
        "child_project_id": _positive_int("child_project_id", args.child_project_id),
        "coordinator_thread_id": _required_id(
            "coordinator_thread_id", args.coordinator_thread_id
        ),
        "coordinator_host_id": _required_id(
            "coordinator_host_id", args.coordinator_host_id
        ),
        "approval": args.approval,
        "approved_candidate_id": approved_candidate_id,
        "approval_reason": _required_text("approval_reason", args.approval_reason, 2000),
        "recast": recast,
    }

    def action(conn, now):
        run = _run(conn, request["run_id"])
        child = _child(conn, request["child_project_id"])
        if run is None or child is None or child["run_id"] != request["run_id"]:
            return ({"ok": False, "status": "run_or_child_not_found"}, 3, [])
        if run["status"] in ("ready", "blocked", "abstained"):
            return ({"ok": False, "status": "run_terminal"}, 5, [])
        if run["coordinator_result_json"] is not None:
            return ({"ok": False, "status": "run_result_frozen_pending_finalization"}, 5, [])
        if (
            run["coordinator_thread_id"] != request["coordinator_thread_id"]
            or run["coordinator_host_id"] != request["coordinator_host_id"]
        ):
            return ({"ok": False, "status": "coordinator_identity_mismatch"}, 4, [])
        assignment = _trusted_child_artifact(
            child,
            "assignment_json",
            "assignment_validated_digest",
            "child assignment",
        )
        if assignment.get("assignment_status") != "requires_pre_mutation_recast":
            return ({"ok": False, "status": "assignment_does_not_require_recast"}, 5, [])
        if child["recast_input_json"] is None:
            return ({"ok": False, "status": "recast_input_not_recorded"}, 5, [])
        recast_input = _trusted_child_artifact(
            child,
            "recast_input_json",
            "recast_input_validated_digest",
            "pre-mutation recast input",
        )
        recast_digest = _validate_normative(
            "pre-mutation-recast", request["recast"], against=[assignment]
        )
        expected = _recast_identity(run, child)
        mismatches = [
            key
            for key, value in expected.items()
            if request["recast"].get(key) != value
        ]
        if mismatches:
            raise ValueError(
                "recast JSON disagrees with stored assignment: " + ", ".join(mismatches)
            )
        mutable_decision_fields = {
            "status",
            "approved_by",
            "approved_candidate_id",
            "approved_cast",
            "approved_treatment_delta",
            "contradiction_summary",
        }
        frozen_input = {
            key: value for key, value in recast_input.items()
            if key not in mutable_decision_fields
        }
        frozen_decision = {
            key: value for key, value in request["recast"].items()
            if key not in mutable_decision_fields
        }
        if frozen_decision != frozen_input:
            raise ValueError("recast decision mutates the frozen recast assessment input")
        candidate_ids = {
            row.get("candidate_id")
            for row in request["recast"].get("candidate_assessments", [])
            if isinstance(row, dict)
        }
        if request["approval"] == "approved":
            if request["recast"].get("status") != "approved":
                raise ValueError("approved registry decision requires an approved recast artifact")
            if request["recast"].get("approved_by") != "coordinator":
                raise ValueError("approved recast must name coordinator as approver")
            if request["approved_candidate_id"] not in candidate_ids:
                raise ValueError("approved_candidate_id must identify an assessed candidate")
            if request["recast"].get("approved_candidate_id") != request[
                "approved_candidate_id"
            ]:
                raise ValueError("CLI approved_candidate_id must match the exact recast artifact")
            registry_status = "approved"
            child_status, qc_status = child["child_status"], child["qc_status"]
            block_reason = None
        else:
            if request["approved_candidate_id"] is not None:
                raise ValueError("blocked recast approval may not select a candidate")
            if request["recast"].get("status") != "blocked":
                raise ValueError("blocked registry decision requires a blocked recast artifact")
            if request["approval_reason"] != request["recast"].get(
                "contradiction_summary"
            ):
                raise ValueError("blocked approval reason must match recast contradiction_summary")
            registry_status = "blocked"
            child_status, qc_status = "blocked", "blocked"
            block_reason = request["approval_reason"]
        if child["recast_json"] is not None:
            same = (
                child["recast_json"] == _json_text(request["recast"])
                and child["recast_validated_digest"] == recast_digest
                and child["recast_approval_status"] == registry_status
                and child["approved_candidate_id"] == request["approved_candidate_id"]
            )
            return (
                {
                    "ok": same,
                    "status": "recast_already_recorded" if same else "recast_conflict",
                },
                0 if same else 4,
                [],
            )
        conn.execute(
            "UPDATE children SET recast_json=?,recast_validated_digest=?,"
            "recast_approval_status=?,approved_candidate_id=?,"
            "pre_mutation_block_reason=?,child_status=?,qc_status=?,updated_at_s=? "
            "WHERE child_project_id=?",
            (
                _json_text(request["recast"]),
                recast_digest,
                registry_status,
                request["approved_candidate_id"],
                block_reason,
                child_status,
                qc_status,
                now,
                request["child_project_id"],
            ),
        )
        event = {
            "event": "pre_mutation_recast_recorded",
            "run_id": request["run_id"],
            "child_project_id": request["child_project_id"],
            "details": {
                "approval": registry_status,
                "approved_candidate_id": request["approved_candidate_id"],
                "approval_reason": request["approval_reason"],
            },
        }
        return (
            {
                "ok": True,
                "status": "recast_recorded",
                "recast_approval_status": registry_status,
                "approved_candidate_id": request["approved_candidate_id"],
                "recast_validated_digest": recast_digest,
            },
            0,
            [event],
        )

    return _mutate(path, "record-recast", args.op_id, request, action)


def block_child_before_mutation(args: argparse.Namespace, path: Path) -> int:
    request = {
        "run_id": _required_id("run_id", args.run_id),
        "child_project_id": _positive_int("child_project_id", args.child_project_id),
        "coordinator_thread_id": _required_id(
            "coordinator_thread_id", args.coordinator_thread_id
        ),
        "coordinator_host_id": _required_id(
            "coordinator_host_id", args.coordinator_host_id
        ),
        "reason": _required_text("reason", args.reason, 2000),
    }

    def action(conn, now):
        run = _run(conn, request["run_id"])
        child = _child(conn, request["child_project_id"])
        if run is None or child is None or child["run_id"] != request["run_id"]:
            return ({"ok": False, "status": "run_or_child_not_found"}, 3, [])
        if run["status"] in ("ready", "blocked", "abstained"):
            return ({"ok": False, "status": "run_terminal"}, 5, [])
        if run["coordinator_result_json"] is not None:
            return ({"ok": False, "status": "run_result_frozen_pending_finalization"}, 5, [])
        if (
            run["coordinator_thread_id"] != request["coordinator_thread_id"]
            or run["coordinator_host_id"] != request["coordinator_host_id"]
        ):
            return ({"ok": False, "status": "coordinator_identity_mismatch"}, 4, [])
        assignment = _trusted_child_artifact(
            child,
            "assignment_json",
            "assignment_validated_digest",
            "child assignment",
        )
        if assignment.get("assignment_status") != "blocked_before_mutation":
            return ({"ok": False, "status": "assignment_not_blocked_before_mutation"}, 5, [])
        if request["reason"] != assignment.get("blocked_reason"):
            return ({"ok": False, "status": "block_reason_mismatch"}, 4, [])
        live = conn.execute(
            "SELECT lease_id FROM leases WHERE child_project_id=? AND state='live'",
            (request["child_project_id"],),
        ).fetchone()
        if live is not None:
            return ({"ok": False, "status": "child_has_live_lease"}, 5, [])
        if child["child_status"] == "blocked" and child["qc_status"] == "blocked":
            same = child["pre_mutation_block_reason"] == request["reason"]
            return (
                {"ok": same, "status": "already_blocked" if same else "block_conflict"},
                0 if same else 4,
                [],
            )
        conn.execute(
            "UPDATE children SET child_status='blocked',qc_status='blocked',"
            "task_state='frozen',pre_mutation_block_reason=?,updated_at_s=? "
            "WHERE child_project_id=?",
            (request["reason"], now, request["child_project_id"]),
        )
        event = {
            "event": "child_blocked_before_mutation",
            "run_id": request["run_id"],
            "child_project_id": request["child_project_id"],
            "details": {"reason": request["reason"]},
        }
        return (
            {"ok": True, "status": "blocked_before_mutation", "reason": request["reason"]},
            0,
            [event],
        )

    return _mutate(
        path, "block-child-before-mutation", args.op_id, request, action
    )


def _latest_child_lease(conn: sqlite3.Connection, child_project_id: int):
    return conn.execute(
        "SELECT * FROM leases WHERE child_project_id=? "
        "ORDER BY lease_generation DESC LIMIT 1",
        (child_project_id,),
    ).fetchone()


def grant_lease(args: argparse.Namespace, path: Path) -> int:
    request = {
        "run_id": _required_id("run_id", args.run_id),
        "child_project_id": _positive_int("child_project_id", args.child_project_id),
        "lease_id": _required_id("lease_id", args.lease_id),
        "purpose": args.purpose,
        "repair_round": _nonnegative_int("repair_round", args.repair_round),
        "selection_fingerprint": _fingerprint(
            "selection_fingerprint", args.selection_fingerprint
        ),
        "assignment_id": _required_id("assignment_id", args.assignment_id),
        "assignment_fingerprint": _fingerprint(
            "assignment_fingerprint", args.assignment_fingerprint
        ),
    }

    def action(conn, now):
        run = _run(conn, request["run_id"])
        child = _child(conn, request["child_project_id"])
        if run is None or child is None or child["run_id"] != request["run_id"]:
            return ({"ok": False, "status": "run_or_child_not_found"}, 3, [])
        if run["status"] in ("ready", "blocked", "abstained"):
            return ({"ok": False, "status": "run_terminal"}, 5, [])
        if run["coordinator_result_json"] is not None:
            return ({"ok": False, "status": "run_result_frozen_pending_finalization"}, 5, [])
        exact = (
            run["selection_fingerprint"] == request["selection_fingerprint"]
            and child["assignment_id"] == request["assignment_id"]
            and child["assignment_fingerprint"] == request["assignment_fingerprint"]
        )
        if not exact:
            return ({"ok": False, "status": "assignment_or_selection_mismatch"}, 4, [])
        assignment = _trusted_child_artifact(
            child,
            "assignment_json",
            "assignment_validated_digest",
            "child assignment",
        )
        if assignment.get("assignment_status") == "blocked_before_mutation":
            return ({"ok": False, "status": "assignment_blocked_before_mutation"}, 5, [])
        if request["purpose"] in ("edit", "repair") and (
            assignment.get("assignment_status") == "requires_pre_mutation_recast"
            and child["recast_approval_status"] != "approved"
        ):
            return (
                {
                    "ok": False,
                    "status": "pre_mutation_recast_not_approved",
                    "recast_approval_status": child["recast_approval_status"],
                },
                5,
                [],
            )
        existing_id = conn.execute(
            "SELECT state,lease_generation FROM leases WHERE lease_id=?",
            (request["lease_id"],),
        ).fetchone()
        if existing_id is not None:
            return (
                {
                    "ok": False,
                    "status": "lease_id_already_used",
                    "lease_generation": existing_id["lease_generation"],
                    "state": existing_id["state"],
                },
                4,
                [],
            )
        active = _active_lease(conn)
        if active is not None:
            return (
                {
                    "ok": False,
                    "status": "global_lease_busy",
                    "active_lease": {
                        "lease_id": active["lease_id"],
                        "lease_generation": active["lease_generation"],
                        "run_id": active["run_id"],
                        "child_project_id": active["child_project_id"],
                        "phase": active["phase"],
                    },
                },
                4,
                [],
            )

        purpose = request["purpose"]
        round_number = request["repair_round"]
        latest = _latest_child_lease(conn, request["child_project_id"])
        allowed = False
        if purpose == "edit":
            fifo_ok, fifo_reason = _fifo_edit_gate(
                conn,
                request["run_id"],
                request["child_project_id"],
                child["card"],
            )
            if not fifo_ok:
                return (
                    {"ok": False, "status": "fifo_grant_refused", "reason": fifo_reason},
                    5,
                    [],
                )
            allowed = (
                round_number == 0
                and child["repair_round"] == 0
                and child["qc_status"] == "pending"
                and child["child_status"] in ("queued", "paused_safe")
            )
        elif purpose == "repair":
            next_repair = (
                child["child_status"] == "needs_repair"
                and child["qc_status"] == "repair_required"
                and round_number == child["repair_round"] + 1
            )
            resume_repair = (
                child["child_status"] == "paused_safe"
                and latest is not None
                and latest["purpose"] == "repair"
                and round_number == child["repair_round"]
            )
            allowed = (next_repair or resume_repair) and round_number <= MAX_REPAIR_ROUNDS
        elif purpose == "qc":
            allowed = (
                child["child_status"] in ("ready", "needs_repair", "blocked")
                and child["qc_status"] == "pending"
                and round_number == child["repair_round"]
            )
        if not allowed:
            return (
                {
                    "ok": False,
                    "status": "lease_transition_refused",
                    "child_status": child["child_status"],
                    "qc_status": child["qc_status"],
                    "current_repair_round": child["repair_round"],
                },
                5,
                [],
            )

        generation = _next_generation(conn)
        if purpose == "qc":
            holder_kind = "coordinator"
            holder_thread_id = run["coordinator_thread_id"]
            holder_host_id = run["coordinator_host_id"]
        else:
            holder_kind = "child"
            holder_thread_id = child["thread_id"]
            holder_host_id = child["host_id"]
        conn.execute(
            "INSERT INTO leases(lease_generation,lease_id,run_id,child_project_id,"
            "assignment_id,assignment_fingerprint,selection_fingerprint,purpose,"
            "repair_round,holder_kind,holder_thread_id,holder_host_id,state,"
            "created_at_s,updated_at_s) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                generation,
                request["lease_id"],
                request["run_id"],
                request["child_project_id"],
                request["assignment_id"],
                request["assignment_fingerprint"],
                request["selection_fingerprint"],
                purpose,
                round_number,
                holder_kind,
                holder_thread_id,
                holder_host_id,
                "live",
                now,
                now,
            ),
        )
        if purpose in ("edit", "repair"):
            conn.execute(
                "UPDATE children SET child_status='editing',repair_round=?,"
                "task_state='live',updated_at_s=? WHERE child_project_id=?",
                (round_number, now, request["child_project_id"]),
            )
            if purpose == "repair" and next_repair:
                conn.execute(
                    "UPDATE children SET child_result_json=NULL,qc_result_json=NULL "
                    "WHERE child_project_id=?",
                    (request["child_project_id"],),
                )
        conn.execute(
            "UPDATE runs SET status='active',phase=?,updated_at_s=? WHERE run_id=?",
            ("qc" if purpose == "qc" else "editing", now, request["run_id"]),
        )
        event = {
            "event": "lease_granted",
            "run_id": request["run_id"],
            "child_project_id": request["child_project_id"],
            "lease_generation": generation,
            "details": {
                "lease_id": request["lease_id"],
                "purpose": purpose,
                "repair_round": round_number,
                "holder_kind": holder_kind,
                "holder_thread_id": holder_thread_id,
                "holder_host_id": holder_host_id,
            },
        }
        return (
            {
                "ok": True,
                "status": "granted",
                "lease_id": request["lease_id"],
                "lease_generation": generation,
                "purpose": purpose,
                "repair_round": round_number,
                "holder_kind": holder_kind,
                "holder_thread_id": holder_thread_id,
                "holder_host_id": holder_host_id,
            },
            0,
            [event],
        )

    return _mutate(path, "grant-lease", args.op_id, request, action)


def _lease_check_request(args: argparse.Namespace) -> dict:
    return {
        "run_id": _required_id("run_id", args.run_id),
        "child_project_id": _positive_int("child_project_id", args.child_project_id),
        "lease_id": _required_id("lease_id", args.lease_id),
        "lease_generation": _positive_int(
            "lease_generation", args.lease_generation
        ),
        "purpose": args.purpose,
        "repair_round": _nonnegative_int("repair_round", args.repair_round),
        "selection_fingerprint": _fingerprint(
            "selection_fingerprint", args.selection_fingerprint
        ),
        "assignment_id": _required_id("assignment_id", args.assignment_id),
        "assignment_fingerprint": _fingerprint(
            "assignment_fingerprint", args.assignment_fingerprint
        ),
        "thread_id": _required_id("thread_id", args.thread_id),
        "host_id": _required_id("host_id", args.host_id),
    }


def _authorization(conn: sqlite3.Connection, request: dict) -> tuple[bool, list[str], object]:
    lease = conn.execute(
        "SELECT * FROM leases WHERE lease_id=? AND lease_generation=?",
        (request["lease_id"], request["lease_generation"]),
    ).fetchone()
    if lease is None:
        return False, ["lease_not_found"], None
    reasons = []
    expected = {
        "state": "live",
        "run_id": request["run_id"],
        "child_project_id": request["child_project_id"],
        "purpose": request["purpose"],
        "repair_round": request["repair_round"],
        "selection_fingerprint": request["selection_fingerprint"],
        "assignment_id": request["assignment_id"],
        "assignment_fingerprint": request["assignment_fingerprint"],
        "holder_thread_id": request["thread_id"],
        "holder_host_id": request["host_id"],
    }
    for key, value in expected.items():
        if lease[key] != value:
            reasons.append(f"{key}_mismatch")
    run = _run(conn, request["run_id"])
    child = _child(conn, request["child_project_id"])
    if run is None or child is None or child["run_id"] != request["run_id"]:
        reasons.append("mapping_missing")
    else:
        if run["selection_fingerprint"] != request["selection_fingerprint"]:
            reasons.append("run_selection_changed")
        if child["assignment_id"] != request["assignment_id"]:
            reasons.append("child_assignment_changed")
        if child["assignment_fingerprint"] != request["assignment_fingerprint"]:
            reasons.append("child_fingerprint_changed")
        expected_kind = "coordinator" if request["purpose"] == "qc" else "child"
        if lease["holder_kind"] != expected_kind:
            reasons.append("holder_kind_mismatch")
    return not reasons, reasons, lease


def check_lease(args: argparse.Namespace, path: Path) -> int:
    request = _lease_check_request(args)
    conn = _connect(path)
    try:
        _validate_schema(conn)
        conn.execute("BEGIN")
        authorized, reasons, lease = _authorization(conn, request)
        response = {
            "ok": authorized,
            "authorized": authorized,
            "status": "authorized" if authorized else "denied",
            "lease_id": request["lease_id"],
            "lease_generation": request["lease_generation"],
            "state": lease["state"] if lease is not None else "missing",
            "reasons": reasons,
        }
        conn.commit()
        _emit(response)
        return 0 if authorized else 4
    finally:
        if conn.in_transaction:
            conn.rollback()
        conn.close()


def begin_call(args: argparse.Namespace, path: Path) -> int:
    auth = _lease_check_request(args)
    return _begin_call(
        path, "begin-call", args.op_id, auth, args.call_id, _authorization
    )


def end_call(args: argparse.Namespace, path: Path) -> int:
    auth = _lease_check_request(args)
    return _end_call(
        path,
        "end-call",
        args.op_id,
        auth,
        args.call_id,
        args.outstanding_job_ids_json,
        _authorization,
    )


def _validate_child_report(
    result: dict,
    auth: dict,
    status: str,
    assignment: dict,
    approved_recast: dict | None,
) -> str:
    if status == "paused_safe":
        if result.get("checkpoint_schema_version") != "valmera-safe-checkpoint-v1":
            raise ValueError(
                "paused_safe requires checkpoint_schema_version valmera-safe-checkpoint-v1"
            )
        digest = "sha256:" + hashlib.sha256(
            json.dumps(result, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")).encode()
        ).hexdigest()
    else:
        upstreams = [assignment]
        if approved_recast is not None:
            upstreams.append(approved_recast)
        digest = _validate_normative("editor-result", result, against=upstreams)
    expected = {
        "run_id": auth["run_id"],
        "child_project_id": auth["child_project_id"],
        "assignment_id": auth["assignment_id"],
        "assignment_input_fingerprint": auth["assignment_fingerprint"],
        "valmera_lease_id": auth["lease_id"],
        "status": status,
    }
    if status != "paused_safe":
        expected.update({
            "valmera_lease_generation": auth["lease_generation"],
            "attempt_purpose": auth["purpose"],
            "repair_round": auth["repair_round"],
        })
    missing = set(expected) - set(result)
    if missing:
        raise ValueError(f"child result is missing identity fields: {sorted(missing)}")
    mismatches = [key for key, value in expected.items() if result.get(key) != value]
    if mismatches:
        raise ValueError(f"child result identity/status mismatch: {mismatches}")
    jobs = result.get("outstanding_job_ids")
    if jobs != []:
        raise ValueError("child result must contain outstanding_job_ids: []")
    return digest


def _validate_qc_report(
    result: dict, auth: dict, status: str, editor_result: dict
) -> str:
    digest = _validate_normative("parent-qc", result, against=[editor_result])
    expected = {
        "child_project_id": auth["child_project_id"],
        "assignment_id": auth["assignment_id"],
        "assignment_input_fingerprint": auth["assignment_fingerprint"],
        "repair_round": auth["repair_round"],
        "status": status,
    }
    missing = set(expected) - set(result)
    if missing:
        raise ValueError(f"QC result is missing identity fields: {sorted(missing)}")
    mismatches = [key for key, value in expected.items() if result.get(key) != value]
    if mismatches:
        raise ValueError(f"QC result identity/status mismatch: {mismatches}")
    if status == "pass":
        live = result.get("live_edl_version")
        preview = result.get("preview_edl_version")
        if type(live) is not int or live <= 0 or type(preview) is not int or preview != live:
            raise ValueError("QC pass requires equal positive live and preview EDL versions")
        if result.get("preview_current") is not True:
            raise ValueError("QC pass requires preview_current: true")
        provenance = result.get("preview_render_provenance")
        receipt = provenance.get("preview_receipt") if isinstance(provenance, dict) else None
        if not isinstance(receipt, dict) or (
            receipt.get("audio_model_review") is not False
            or receipt.get("listen_keys_count") != 0
            or receipt.get("listen_clips_count") != 0
            or receipt.get("audio_reviewer_findings_count") != 0
        ):
            raise ValueError(
                "QC pass requires a current deterministic-only MCP preview receipt"
            )
        transcript = result.get("program_speech_transcript")
        if not isinstance(transcript, dict) or (
            transcript.get("status") != "complete"
            or transcript.get("source") != "render_asr"
            or transcript.get("render_edl_version") != live
            or transcript.get("processing_gaps") != []
        ):
            raise ValueError(
                "QC pass requires complete full-stream ASR bound to the current preview"
            )
        if result.get("violations") != []:
            raise ValueError("QC pass requires violations: []")
    return digest


def record_child_result(args: argparse.Namespace, path: Path) -> int:
    auth = _lease_check_request(args)
    result = _json_value("result_json", args.result_json, dict)
    exclusions = _json_value("exclusions_json", args.exclusions_json, dict)
    status = args.status
    request = {**auth, "status": status, "result": result, "exclusions": exclusions}

    def action(conn, now):
        authorized, reasons, lease = _authorization(conn, auth)
        if not authorized:
            return (
                {"ok": False, "status": "lease_denied", "reasons": reasons},
                4,
                [],
            )
        if lease["holder_kind"] != "child" or lease["purpose"] not in ("edit", "repair"):
            return ({"ok": False, "status": "child_lease_required"}, 5, [])
        child = _child(conn, auth["child_project_id"])
        run = _run(conn, auth["run_id"])
        assignment = _trusted_child_artifact(
            child,
            "assignment_json",
            "assignment_validated_digest",
            "child assignment",
        )
        approved_recast = None
        if assignment.get("assignment_status") == "requires_pre_mutation_recast":
            if child["recast_approval_status"] != "approved":
                return ({"ok": False, "status": "pre_mutation_recast_not_approved"}, 5, [])
            approved_recast = _trusted_child_artifact(
                child,
                "recast_json",
                "recast_validated_digest",
                "approved pre-mutation recast",
            )
        validated_digest = _validate_child_report(
            result, auth, status, assignment, approved_recast
        )
        if status != "paused_safe" and (
            result.get("parent_project_id") != run["parent_project_id"]
            or result.get("card") != child["card"]
        ):
            return ({"ok": False, "status": "child_result_project_or_card_mismatch"}, 4, [])
        if status == "ready":
            child_status, qc_status = "ready", "pending"
        elif status == "needs_repair":
            child_status, qc_status = "needs_repair", "pending"
        elif status == "blocked":
            child_status, qc_status = "blocked", "pending"
        else:
            child_status, qc_status = "paused_safe", child["qc_status"]
        conn.execute(
            "UPDATE children SET child_status=?,qc_status=?,child_result_json=?,"
            "exclusions_json=?,updated_at_s=? "
            "WHERE child_project_id=?",
            (
                child_status,
                qc_status,
                _json_text(result),
                _json_text(exclusions),
                now,
                auth["child_project_id"],
            ),
        )
        conn.execute(
            "INSERT INTO attempt_results(run_id,child_project_id,lease_generation,actor,"
            "repair_round,status,result_json,exclusions_json,validated_digest,created_at_s) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                auth["run_id"],
                auth["child_project_id"],
                auth["lease_generation"],
                "child",
                auth["repair_round"],
                status,
                _json_text(result),
                _json_text(exclusions),
                validated_digest,
                now,
            ),
        )
        event = {
            "event": "child_result_recorded",
            "run_id": auth["run_id"],
            "child_project_id": auth["child_project_id"],
            "lease_generation": auth["lease_generation"],
            "details": {
                "status": status,
                "repair_round": auth["repair_round"],
                "exclusions": exclusions,
            },
        }
        return (
            {
                "ok": True,
                "status": "child_result_recorded",
                "child_status": child_status,
                "qc_status": qc_status,
                "repair_round": auth["repair_round"],
            },
            0,
            [event],
        )

    return _mutate(path, "record-child-result", args.op_id, request, action)


def record_qc_result(args: argparse.Namespace, path: Path) -> int:
    auth = _lease_check_request(args)
    result = _json_value("result_json", args.result_json, dict)
    exclusions = _json_value("exclusions_json", args.exclusions_json, dict)
    status = args.status
    if status == "repair_required" and auth["repair_round"] >= MAX_REPAIR_ROUNDS:
        raise ValueError(
            "repair cap reached; record the fully reviewed unresolved child as blocked"
        )
    request = {**auth, "status": status, "result": result, "exclusions": exclusions}

    def action(conn, now):
        authorized, reasons, lease = _authorization(conn, auth)
        if not authorized:
            return (
                {"ok": False, "status": "lease_denied", "reasons": reasons},
                4,
                [],
            )
        if lease["holder_kind"] != "coordinator" or lease["purpose"] != "qc":
            return ({"ok": False, "status": "coordinator_qc_lease_required"}, 5, [])
        child = _child(conn, auth["child_project_id"])
        editor_attempt = conn.execute(
            "SELECT result_json,validated_digest FROM attempt_results "
            "WHERE child_project_id=? AND actor='child' AND repair_round=? "
            "ORDER BY result_id DESC LIMIT 1",
            (auth["child_project_id"], auth["repair_round"]),
        ).fetchone()
        if editor_attempt is None:
            return ({"ok": False, "status": "stored_editor_result_missing"}, 5, [])
        editor_result = json.loads(editor_attempt["result_json"])
        if _artifact_digest(editor_result) != editor_attempt["validated_digest"]:
            raise ValueError("stored editor result digest mismatch")
        validated_digest = _validate_qc_report(
            result, auth, status, editor_result
        )
        if result.get("editor_task_id") != child["thread_id"]:
            return ({"ok": False, "status": "qc_editor_task_mismatch"}, 4, [])
        run = _run(conn, auth["run_id"])
        if (
            result.get("run_id") != auth["run_id"]
            or result.get("parent_project_id") != run["parent_project_id"]
            or result.get("card") != child["card"]
            or result.get("editor_task_id") != child["thread_id"]
        ):
            return ({"ok": False, "status": "qc_project_or_task_mismatch"}, 4, [])
        if status == "pass":
            child_status, qc_status = "ready", "pass"
        elif status == "repair_required":
            child_status, qc_status = "needs_repair", "repair_required"
        else:
            child_status, qc_status = "blocked", "blocked"
        conn.execute(
            "UPDATE children SET child_status=?,qc_status=?,qc_result_json=?,"
            "exclusions_json=?,task_state='frozen',updated_at_s=? "
            "WHERE child_project_id=?",
            (
                child_status,
                qc_status,
                _json_text(result),
                _json_text(exclusions),
                now,
                auth["child_project_id"],
            ),
        )
        conn.execute(
            "INSERT INTO attempt_results(run_id,child_project_id,lease_generation,actor,"
            "repair_round,status,result_json,exclusions_json,validated_digest,created_at_s) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                auth["run_id"],
                auth["child_project_id"],
                auth["lease_generation"],
                "qc",
                auth["repair_round"],
                status,
                _json_text(result),
                _json_text(exclusions),
                validated_digest,
                now,
            ),
        )
        event = {
            "event": "qc_result_recorded",
            "run_id": auth["run_id"],
            "child_project_id": auth["child_project_id"],
            "lease_generation": auth["lease_generation"],
            "details": {
                "status": status,
                "repair_round": auth["repair_round"],
                "exclusions": exclusions,
            },
        }
        return (
            {
                "ok": True,
                "status": "qc_result_recorded",
                "child_status": child_status,
                "qc_status": qc_status,
                "repair_round": auth["repair_round"],
            },
            0,
            [event],
        )

    return _mutate(path, "record-qc-result", args.op_id, request, action)


def close_lease(args: argparse.Namespace, path: Path) -> int:
    outstanding = _json_value(
        "outstanding_job_ids_json", args.outstanding_job_ids_json, list
    )
    request = {
        "run_id": _required_id("run_id", args.run_id),
        "child_project_id": _positive_int("child_project_id", args.child_project_id),
        "lease_id": _required_id("lease_id", args.lease_id),
        "lease_generation": _positive_int(
            "lease_generation", args.lease_generation
        ),
        "coordinator_thread_id": _required_id(
            "coordinator_thread_id", args.coordinator_thread_id
        ),
        "coordinator_host_id": _required_id(
            "coordinator_host_id", args.coordinator_host_id
        ),
        "action": args.action,
        "checkpoint_status": args.checkpoint_status,
        "outstanding_job_ids": outstanding,
        "reason": _required_text("reason", args.reason, 2000),
    }

    def action(conn, now):
        run = _run(conn, request["run_id"])
        child = _child(conn, request["child_project_id"])
        if run is None or child is None or child["run_id"] != request["run_id"]:
            return ({"ok": False, "status": "run_or_child_not_found"}, 3, [])
        if (
            run["coordinator_thread_id"] != request["coordinator_thread_id"]
            or run["coordinator_host_id"] != request["coordinator_host_id"]
        ):
            return ({"ok": False, "status": "coordinator_identity_mismatch"}, 4, [])
        lease = conn.execute(
            "SELECT * FROM leases WHERE lease_id=? AND lease_generation=?",
            (request["lease_id"], request["lease_generation"]),
        ).fetchone()
        if lease is None:
            return ({"ok": False, "status": "lease_not_found"}, 3, [])
        if (
            lease["state"] != "live"
            or lease["run_id"] != request["run_id"]
            or lease["child_project_id"] != request["child_project_id"]
        ):
            return (
                {
                    "ok": False,
                    "status": "stale_or_mismatched_lease",
                    "lease_state": lease["state"],
                },
                4,
                [],
            )
        in_flight = conn.execute(
            "SELECT call_id FROM call_permits WHERE lease_generation=? "
            "AND state='in_flight'",
            (request["lease_generation"],),
        ).fetchone()
        if in_flight is not None:
            return (
                {
                    "ok": False,
                    "status": "valmera_call_in_flight",
                    "call_id": in_flight["call_id"],
                },
                5,
                [],
            )
        known_jobs = (
            json.loads(lease["outstanding_job_ids_json"])
            if lease["outstanding_job_ids_json"] is not None
            else []
        )
        if outstanding or known_jobs:
            effective_jobs = outstanding or known_jobs
            conn.execute(
                "UPDATE leases SET outstanding_job_ids_json=?,updated_at_s=? "
                "WHERE lease_generation=?",
                (_json_text(effective_jobs), now, request["lease_generation"]),
            )
            return (
                {
                    "ok": False,
                    "status": "outstanding_jobs_present",
                    "outstanding_job_ids": effective_jobs,
                },
                5,
                [],
            )

        checkpoint = request["checkpoint_status"]
        if lease["purpose"] == "qc":
            compatible = (
                (checkpoint == "ready" and child["qc_status"] == "pass")
                or (checkpoint == "blocked" and child["qc_status"] == "blocked")
                or (
                    checkpoint == "paused_safe"
                    and child["qc_status"] == "repair_required"
                )
            )
        else:
            compatible = (
                (checkpoint == "ready" and child["child_status"] == "ready")
                or (checkpoint == "blocked" and child["child_status"] == "blocked")
                or (
                    checkpoint == "paused_safe"
                    and child["child_status"] in ("paused_safe", "needs_repair")
                )
            )
        if not compatible:
            return (
                {
                    "ok": False,
                    "status": "unsafe_checkpoint",
                    "lease_purpose": lease["purpose"],
                    "child_status": child["child_status"],
                    "qc_status": child["qc_status"],
                    "requested_checkpoint": checkpoint,
                },
                5,
                [],
            )

        terminal_state = {
            "release": "released",
            "freeze": "frozen",
            "revoke": "revoked",
        }[request["action"]]
        conn.execute(
            "UPDATE leases SET state=?,checkpoint_status=?,"
            "outstanding_job_ids_json='[]',close_reason=?,updated_at_s=?,closed_at_s=? "
            "WHERE lease_generation=?",
            (
                terminal_state,
                checkpoint,
                request["reason"],
                now,
                now,
                request["lease_generation"],
            ),
        )
        conn.execute(
            "UPDATE children SET task_state='frozen',updated_at_s=? "
            "WHERE child_project_id=?",
            (now, request["child_project_id"]),
        )
        event = {
            "event": f"lease_{terminal_state}",
            "run_id": request["run_id"],
            "child_project_id": request["child_project_id"],
            "lease_generation": request["lease_generation"],
            "details": {
                "checkpoint_status": checkpoint,
                "reason": request["reason"],
            },
        }
        return (
            {
                "ok": True,
                "status": terminal_state,
                "lease_id": request["lease_id"],
                "lease_generation": request["lease_generation"],
                "checkpoint_status": checkpoint,
                "outstanding_job_ids": [],
            },
            0,
            [event],
        )

    return _mutate(path, "close-lease", args.op_id, request, action)


def _trusted_run_result(run: sqlite3.Row) -> dict:
    raw = run["coordinator_result_json"]
    digest = run["coordinator_result_validated_digest"]
    if raw is None or digest is None:
        raise ValueError("coordinator run result is not persisted")
    data = json.loads(raw)
    if _artifact_digest(data) != digest:
        raise ValueError("coordinator run result digest mismatch")
    return data


def _latest_blocked_phase(conn: sqlite3.Connection, run_id: str):
    return conn.execute(
        "SELECT * FROM run_snapshots WHERE run_id=? AND checkpoint_status='blocked' "
        "ORDER BY snapshot_id DESC LIMIT 1",
        (run_id,),
    ).fetchone()


def record_run_result(args: argparse.Namespace, path: Path) -> int:
    result = _json_value("result_json", args.result_json, dict)
    request = {
        "run_id": _required_id("run_id", args.run_id),
        "coordinator_thread_id": _required_id(
            "coordinator_thread_id", args.coordinator_thread_id
        ),
        "coordinator_host_id": _required_id(
            "coordinator_host_id", args.coordinator_host_id
        ),
        "result": result,
    }

    def action(conn, now):
        run = _run(conn, request["run_id"])
        if run is None:
            return ({"ok": False, "status": "run_not_found"}, 3, [])
        if (
            run["coordinator_thread_id"] != request["coordinator_thread_id"]
            or run["coordinator_host_id"] != request["coordinator_host_id"]
        ):
            return ({"ok": False, "status": "coordinator_identity_mismatch"}, 4, [])
        if _active_lease(conn) is not None:
            return ({"ok": False, "status": "global_live_lease_exists"}, 5, [])
        open_intents = conn.execute(
            "SELECT client_request_id,state,child_project_id,card FROM "
            "child_task_intents WHERE run_id=? AND state!='consumed' ORDER BY card",
            (request["run_id"],),
        ).fetchall()
        if open_intents:
            return (
                {
                    "ok": False,
                    "status": "visible_task_intents_unresolved",
                    "task_intents": [dict(row) for row in open_intents],
                },
                5,
                [],
            )
        if run["coordinator_result_json"] is not None:
            old = _trusted_run_result(run)
            same = old == request["result"]
            return (
                {
                    "ok": same,
                    "status": "run_result_already_recorded" if same else "run_result_conflict",
                    "validated_digest": run["coordinator_result_validated_digest"],
                },
                0 if same else 4,
                [],
            )
        blocked_phase = _latest_blocked_phase(conn, request["run_id"])
        result_status = request["result"].get("status")
        if result_status not in (
            "ready_for_studio_export",
            "partial",
            "blocked",
            "abstained",
            "blocked_before_selection",
        ):
            raise ValueError("only a terminal coordinator run result may be persisted")
        before_selection = result_status == "blocked_before_selection"
        selection = _latest_snapshot(conn, request["run_id"], "selection")
        selection_ready = selection is not None and selection["checkpoint_status"] == "ready"
        if before_selection:
            if selection_ready:
                raise ValueError("blocked_before_selection result is invalid after selection")
            if blocked_phase is None or blocked_phase["phase"] not in (
                "acquisition",
                "selection",
            ):
                raise ValueError(
                    "blocked_before_selection requires a frozen blocked acquisition/selection checkpoint"
                )
            validated_digest = _validate_normative(
                "coordinator-run-result", request["result"]
            )
            blocked_data = json.loads(blocked_phase["data_json"])
            expected_identity = {
                "run_id": run["run_id"],
                "topic": run["topic"],
                "blocked_phase": blocked_phase["phase"],
                "blocked_reason": blocked_data["reason"],
                "blocked_evidence": blocked_data["evidence"],
            }
            acquisition = _latest_snapshot(conn, request["run_id"], "acquisition")
            acquired = None
            if acquisition is not None and acquisition["checkpoint_status"] == "ready":
                acquired = _source_acquisition_checkpoint(
                    _trusted_snapshot_data(
                        acquisition, "source-acquisition-checkpoint-v1"
                    )
                )
            expected_identity.update({
                "parent_project_id": acquired["parent_project_id"] if acquired else None,
                "source_youtube_video_id": (
                    acquired["source_youtube_video_id"] if acquired else None
                ),
                "source_asset_id": acquired["source_asset_id"] if acquired else None,
                "source_sha256": acquired["source_sha256"] if acquired else None,
            })
            mismatches = [
                key
                for key, value in expected_identity.items()
                if request["result"].get(key) != value
            ]
            if mismatches:
                raise ValueError(
                    "blocked-before-selection result identity mismatch: "
                    + ", ".join(mismatches)
                )
        else:
            if not selection_ready:
                return ({"ok": False, "status": "selection_snapshot_not_ready"}, 5, [])
            selection_data = _trusted_snapshot_data(selection, "selection")
            _, selected, abstained = _selection_manifest(
                selection_data, validate=False
            )
            source_acquisition_snapshot = _latest_snapshot(
                conn, request["run_id"], "acquisition"
            )
            if (
                source_acquisition_snapshot is None
                or source_acquisition_snapshot["checkpoint_status"] != "ready"
            ):
                raise ValueError("coordinator result lacks frozen source acquisition")
            acquired = _source_acquisition_checkpoint(
                _trusted_snapshot_data(
                    source_acquisition_snapshot,
                    "source-acquisition-checkpoint-v1",
                )
            )
            acquisition_snapshot = _latest_snapshot(
                conn, request["run_id"], "acquisition_record"
            )
            acquisition_data = None
            if (
                acquisition_snapshot is not None
                and acquisition_snapshot["checkpoint_status"] == "ready"
            ):
                acquisition_data = _trusted_snapshot_data(
                    acquisition_snapshot, "acquisition-record"
                )
            if acquisition_data is None and (
                abstained
                or blocked_phase is None
                or blocked_phase["phase"] not in (
                    "reference",
                    "acquisition_record",
                )
            ):
                raise ValueError(
                    "post-selection coordinator result requires a frozen acquisition record"
                )
            reference_data = None
            if not abstained:
                reference_snapshot = _latest_snapshot(
                    conn, request["run_id"], "reference"
                )
                if (
                    reference_snapshot is not None
                    and reference_snapshot["checkpoint_status"] == "ready"
                ):
                    reference_data = _trusted_snapshot_data(
                        reference_snapshot, "reference-profile"
                    )
                if reference_data is None and (
                    blocked_phase is None or blocked_phase["phase"] != "reference"
                ):
                    raise ValueError(
                        "non-abstained run result lacks a frozen reference identity"
                    )
            upstreams = [selection_data]
            if acquisition_data is not None:
                upstreams.append(acquisition_data)
            if reference_data is not None:
                upstreams.append(reference_data)
            validated_digest = _validate_normative(
                "coordinator-run-result", request["result"], against=upstreams
            )
            source = acquisition_data["source"] if acquisition_data else {
                "youtube_video_id": acquired["source_youtube_video_id"],
                "asset_id": acquired["source_asset_id"],
                "sha256": acquired["source_sha256"],
            }
            reference_identity = (
                acquisition_data.get("reference") if acquisition_data else None
            )
            if reference_identity is None and reference_data is not None:
                reference_identity = {
                    "youtube_video_id": reference_data["youtube_video_id"],
                    "asset_id": reference_data["parent_reference_asset_id"],
                    "sha256": reference_data["reference_sha256"],
                }
            if reference_identity is None and blocked_phase is not None:
                blocked_data = json.loads(blocked_phase["data_json"])
                reference_identity = {
                    "youtube_video_id": blocked_data.get(
                        "reference_youtube_video_id"
                    ),
                    "asset_id": blocked_data.get("reference_asset_id"),
                    "sha256": blocked_data.get("reference_sha256"),
                }
            expected_identity = {
                "run_id": run["run_id"],
                "topic": run["topic"],
                "parent_project_id": acquired["parent_project_id"],
                "source_youtube_video_id": source["youtube_video_id"],
                "source_asset_id": source["asset_id"],
                "source_sha256": _normalized_sha256(source["sha256"]),
                "reference_youtube_video_id": (
                    reference_identity["youtube_video_id"]
                    if isinstance(reference_identity, dict) else None
                ),
                "reference_asset_id": (
                    reference_identity["asset_id"]
                    if isinstance(reference_identity, dict) else None
                ),
                "reference_sha256": (
                    _normalized_sha256(reference_identity["sha256"])
                    if isinstance(reference_identity, dict)
                    and reference_identity.get("sha256") is not None
                    else None
                ),
                "selection_fingerprint": run["selection_fingerprint"],
            }
            mismatches = [
                key
                for key, value in expected_identity.items()
                if request["result"].get(key) != value
            ]
            if mismatches:
                raise ValueError(
                    "coordinator result disagrees with frozen run identity: "
                    + ", ".join(mismatches)
                )
            children = conn.execute(
                "SELECT * FROM children WHERE run_id=? ORDER BY card",
                (request["run_id"],),
            ).fetchall()
            children_by_card = {row["card"]: row for row in children}
            if abstained:
                if acquisition_data is None or acquisition_data.get("abstained") is not True:
                    raise ValueError("abstained run requires frozen abstained acquisition record")
                manifest = []
                derived_status = "abstained"
                abstained_counts = {
                    "selected_arc_count": 0,
                    "accounted_arc_count": 0,
                    "generated_count": 0,
                    "pending_count": 0,
                    "failed_generation_count": 0,
                    "ready_count": 0,
                    "blocked_count": 0,
                    "arc_accounting": [],
                }
                mismatches = [
                    key for key, value in abstained_counts.items()
                    if request["result"].get(key) != value
                ]
                if mismatches:
                    raise ValueError(
                        "abstained coordinator result accounting mismatch: "
                        + ", ".join(mismatches)
                    )
            else:
                materialization = _latest_snapshot(
                    conn, request["run_id"], "materialization"
                )
                if (
                    materialization is not None
                    and materialization["checkpoint_status"] == "ready"
                ):
                    manifest = _materialization_manifest(
                        _trusted_snapshot_data(materialization, "materialization-v1"),
                        selection_data,
                        validate=False,
                    )
                elif blocked_phase is not None and blocked_phase["phase"] in (
                    "reference",
                    "acquisition_record",
                    "materialization",
                ):
                    blocked_data = json.loads(blocked_phase["data_json"])
                    pre_materialization_block = blocked_phase["phase"] in (
                        "reference", "acquisition_record"
                    )
                    manifest = [
                        {
                            **item,
                            "status": "pending" if pre_materialization_block else "failed",
                            "child_project_id": None,
                            "generation_job_id": blocked_data.get("job_id"),
                            "generation_failure": (
                                None if pre_materialization_block else blocked_data["reason"]
                            ),
                        }
                        for item in selected
                    ]
                else:
                    raise ValueError(
                        "terminal coordinator result requires ready materialization or a blocked phase"
                    )
                ready_count = sum(
                    row["qc_status"] == "pass" for row in children
                )
                blocked_count = sum(
                    row["qc_status"] == "blocked" for row in children
                )
                generated_count = sum(
                    item["status"] == "materialized" for item in manifest
                )
                failed_count = sum(item["status"] == "failed" for item in manifest)
                pending_count = sum(item["status"] == "pending" for item in manifest)
                if blocked_phase is not None and blocked_phase["phase"] in (
                    "reference", "acquisition_record", "materialization"
                ):
                    derived_status = "blocked"
                elif pending_count:
                    derived_status = "in_progress"
                elif (
                    len(children) != generated_count
                    or ready_count + blocked_count != generated_count
                ):
                    # A failed materialization must never hide a generated child that
                    # has not completed independent parent QC.  Terminal accounting
                    # is legal only after every generated child is pass or blocked.
                    derived_status = "in_progress"
                elif failed_count or blocked_count:
                    derived_status = "partial" if ready_count else "blocked"
                elif ready_count == len(selected) and len(children) == len(selected):
                    derived_status = "ready_for_studio_export"
                else:
                    derived_status = "in_progress"
                expected_counts = {
                    "selected_arc_count": len(selected),
                    "accounted_arc_count": len(selected),
                    "generated_count": generated_count,
                    "pending_count": pending_count,
                    "failed_generation_count": failed_count,
                    "ready_count": ready_count,
                    "blocked_count": blocked_count,
                }
                count_mismatches = [
                    key
                    for key, value in expected_counts.items()
                    if request["result"].get(key) != value
                ]
                if count_mismatches:
                    raise ValueError(
                        "coordinator result count mismatch: " + ", ".join(count_mismatches)
                    )
                result_rows = {
                    row.get("selection_rank"): row
                    for row in request["result"].get("arc_accounting", [])
                    if isinstance(row, dict)
                }
                for item in manifest:
                    row = result_rows.get(item["card"])
                    if row is None:
                        raise ValueError(
                            f"coordinator result lacks accounting for card {item['card']}"
                        )
                    expected_generation = (
                        "generated" if item["status"] == "materialized" else item["status"]
                    )
                    expected_generation_identity = {
                        "selection_rank": item["card"],
                        "start_s": item["approved_start_s"],
                        "end_s": item["approved_end_s"],
                        "title": item["title"],
                        "generation_status": expected_generation,
                        "child_project_id": item["child_project_id"],
                        "generation_job_id": item.get("generation_job_id"),
                        "generation_failure": item.get("generation_failure"),
                    }
                    generation_mismatches = [
                        key for key, value in expected_generation_identity.items()
                        if row.get(key) != value
                    ]
                    if (
                        generation_mismatches
                    ):
                        raise ValueError(
                            f"coordinator result generation identity mismatch for card "
                            f"{item['card']}: {', '.join(generation_mismatches)}"
                        )
                    if item["status"] == "materialized":
                        child = children_by_card.get(item["card"])
                        if child is None or child["child_project_id"] != item["child_project_id"]:
                            raise ValueError(
                                f"coordinator result references unregistered card {item['card']}"
                            )
                        expected_editor = (
                            "ready" if child["qc_status"] == "pass" else "blocked"
                            if child["qc_status"] == "blocked" else "in_progress"
                        )
                        expected_qc = (
                            "pass" if child["qc_status"] == "pass" else "blocked"
                            if child["qc_status"] == "blocked" else "pending"
                        )
                        assignment = _trusted_child_artifact(
                            child,
                            "assignment_json",
                            "assignment_validated_digest",
                            "child assignment",
                        )
                        if row.get("treatment_name") != assignment["treatment"]["name"]:
                            raise ValueError(
                                f"coordinator result treatment mismatch for card {item['card']}"
                            )
                        qc_attempt = conn.execute(
                            "SELECT result_json,validated_digest FROM attempt_results "
                            "WHERE child_project_id=? AND actor='qc' AND repair_round=? "
                            "ORDER BY result_id DESC LIMIT 1",
                            (child["child_project_id"], child["repair_round"]),
                        ).fetchone()
                        expected_live = expected_preview = None
                        if qc_attempt is not None:
                            qc_artifact = json.loads(qc_attempt["result_json"])
                            if _artifact_digest(qc_artifact) != qc_attempt["validated_digest"]:
                                raise ValueError("stored parent QC result digest mismatch")
                            expected_live = qc_artifact.get("live_edl_version")
                            expected_preview = qc_artifact.get("preview_edl_version")
                        if (
                            row.get("editor_status") != expected_editor
                            or row.get("parent_qc_status") != expected_qc
                            or row.get("live_edl_version") != expected_live
                            or row.get("preview_edl_version") != expected_preview
                        ):
                            raise ValueError(
                                f"coordinator result child/QC status mismatch for card {item['card']}"
                            )
                    elif item["status"] == "failed":
                        if (
                            row.get("editor_status") != "failed"
                            or row.get("parent_qc_status") != "not_run"
                            or "generation" not in row.get("failed_gates", [])
                        ):
                            raise ValueError(
                                f"coordinator failed-generation accounting mismatch for card {item['card']}"
                            )
                    elif (
                        row.get("editor_status") != "not_started"
                        or row.get("parent_qc_status") != "not_run"
                        or row.get("failed_gates") != []
                    ):
                        raise ValueError(
                            f"coordinator pending-generation accounting mismatch for card {item['card']}"
                        )
                if derived_status == "blocked":
                    if blocked_phase is not None and blocked_phase["phase"] in (
                        "reference",
                        "acquisition_record",
                        "materialization",
                    ):
                        blocked_data = json.loads(blocked_phase["data_json"])
                        expected_block = {
                            "blocked_phase": blocked_phase["phase"],
                            "blocked_reason": blocked_data["reason"],
                            "blocked_evidence": blocked_data["evidence"],
                        }
                    elif failed_count == len(selected):
                        expected_block = {
                            "blocked_phase": "materialization",
                            "blocked_reason": "All selected stories failed materialization.",
                            "blocked_evidence": [
                                f"card={item['card']} generation_status=failed"
                                for item in manifest if item["status"] == "failed"
                            ],
                        }
                    else:
                        expected_block = {
                            "blocked_phase": "child_qc",
                            "blocked_reason": "All generated children are terminally blocked.",
                            "blocked_evidence": [
                                *[
                                    f"child_project_id={row['child_project_id']} "
                                    "parent_qc_status=blocked"
                                    for row in children if row["qc_status"] == "blocked"
                                ],
                                *[
                                    f"card={item['card']} generation_status=failed"
                                    for item in manifest if item["status"] == "failed"
                                ],
                            ],
                        }
                    block_mismatches = [
                        key for key, value in expected_block.items()
                        if request["result"].get(key) != value
                    ]
                    if block_mismatches:
                        raise ValueError(
                            "coordinator result blocked evidence mismatch: "
                            + ", ".join(block_mismatches)
                        )
            if request["result"].get("status") != derived_status:
                raise ValueError(
                    "coordinator result status mismatch: expected " + derived_status
                )
        conn.execute(
            "UPDATE runs SET coordinator_result_json=?,"
            "coordinator_result_validated_digest=?,updated_at_s=? WHERE run_id=?",
            (
                _json_text(request["result"]),
                validated_digest,
                now,
                request["run_id"],
            ),
        )
        event = {
            "event": "coordinator_run_result_recorded",
            "run_id": request["run_id"],
            "details": {
                "status": result_status,
                "validated_digest": validated_digest,
            },
        }
        return (
            {
                "ok": True,
                "status": "coordinator_run_result_recorded",
                "result_status": result_status,
                "validated_digest": validated_digest,
            },
            0,
            [event],
        )

    return _mutate(path, "record-run-result", args.op_id, request, action)


def finalize_run(args: argparse.Namespace, path: Path) -> int:
    request = {
        "run_id": _required_id("run_id", args.run_id),
        "coordinator_thread_id": _required_id(
            "coordinator_thread_id", args.coordinator_thread_id
        ),
        "coordinator_host_id": _required_id(
            "coordinator_host_id", args.coordinator_host_id
        ),
        "status": args.status,
    }

    def action(conn, now):
        run = _run(conn, request["run_id"])
        if run is None:
            return ({"ok": False, "status": "run_not_found"}, 3, [])
        if (
            run["coordinator_thread_id"] != request["coordinator_thread_id"]
            or run["coordinator_host_id"] != request["coordinator_host_id"]
        ):
            return ({"ok": False, "status": "coordinator_identity_mismatch"}, 4, [])
        if run["status"] in ("ready", "blocked", "abstained"):
            same = run["status"] == request["status"]
            return (
                {
                    "ok": same,
                    "status": "already_finalized" if same else "terminal_run_immutable",
                    "run_status": run["status"],
                },
                0 if same else 5,
                [],
            )
        active = _active_lease(conn)
        if active is not None:
            return (
                {
                    "ok": False,
                    "status": "global_live_lease_exists",
                    "lease_id": active["lease_id"],
                    "lease_generation": active["lease_generation"],
                    "active_run_id": active["run_id"],
                },
                5,
                [],
            )
        inconsistent = conn.execute(
            "SELECT child_project_id FROM children WHERE run_id=? AND task_state='live'",
            (request["run_id"],),
        ).fetchall()
        if inconsistent:
            return (
                {
                    "ok": False,
                    "status": "live_task_without_live_lease",
                    "child_project_ids": [row["child_project_id"] for row in inconsistent],
                },
                5,
                [],
            )
        target = request["status"]
        coordinator_result = None
        if target in ("ready", "blocked", "abstained"):
            if run["coordinator_result_json"] is None:
                return ({"ok": False, "status": "coordinator_run_result_required"}, 5, [])
            coordinator_result = _trusted_run_result(run)
            result_to_registry = {
                "ready_for_studio_export": "ready",
                "partial": "blocked",
                "blocked": "blocked",
                "blocked_before_selection": "blocked",
                "abstained": "abstained",
            }
            mapped = result_to_registry.get(coordinator_result.get("status"))
            if mapped != target:
                return (
                    {
                        "ok": False,
                        "status": "coordinator_result_status_mismatch",
                        "result_status": coordinator_result.get("status"),
                        "required_registry_status": mapped,
                    },
                    5,
                    [],
                )
            if coordinator_result.get("status") == "blocked_before_selection":
                blocked_phase = _latest_blocked_phase(conn, request["run_id"])
                if (
                    target != "blocked"
                    or blocked_phase is None
                    or blocked_phase["phase"] not in ("acquisition", "selection")
                ):
                    return (
                        {"ok": False, "status": "blocked_phase_result_mismatch"},
                        5,
                        [],
                    )
                conn.execute(
                    "UPDATE runs SET status='blocked',phase='complete',updated_at_s=? "
                    "WHERE run_id=?",
                    (now, request["run_id"]),
                )
                event = {
                    "event": "run_status_set",
                    "run_id": request["run_id"],
                    "details": {"status": "blocked"},
                }
                return (
                    {"ok": True, "status": "run_status_set", "run_status": "blocked"},
                    0,
                    [event],
                )
            if target == "blocked" and coordinator_result.get("status") in (
                "partial",
                "blocked",
            ):
                conn.execute(
                    "UPDATE runs SET status='blocked',phase='complete',updated_at_s=? "
                    "WHERE run_id=?",
                    (now, request["run_id"]),
                )
                event = {
                    "event": "run_status_set",
                    "run_id": request["run_id"],
                    "details": {"status": "blocked"},
                }
                return (
                    {"ok": True, "status": "run_status_set", "run_status": "blocked"},
                    0,
                    [event],
                )
        children = conn.execute(
            "SELECT child_project_id,card,title,qc_status FROM children WHERE run_id=?",
            (request["run_id"],),
        ).fetchall()
        selection = _latest_snapshot(conn, request["run_id"], "selection")
        if selection is None or selection["checkpoint_status"] != "ready":
            return ({"ok": False, "status": "selection_snapshot_not_ready"}, 5, [])
        selection_data = _trusted_snapshot_data(selection, "selection")
        _, selected, abstained = _selection_manifest(selection_data, validate=False)
        if target != "paused_safe" and (
            run["parent_project_id"] is None or run["selection_fingerprint"] is None
        ):
            return ({"ok": False, "status": "run_not_bound"}, 5, [])
        materialized = []
        if selected:
            materialization = _latest_snapshot(conn, request["run_id"], "materialization")
            if materialization is None or materialization["checkpoint_status"] != "ready":
                if target != "paused_safe":
                    return (
                        {"ok": False, "status": "materialization_snapshot_not_ready"},
                        5,
                        [],
                    )
            else:
                materialization_data = _trusted_snapshot_data(
                    materialization, "materialization-v1"
                )
                materialized = _materialization_manifest(
                    materialization_data, selection_data, validate=False
                )
        children_by_card = {row["card"]: row for row in children}
        coverage_ok = bool(materialized) and len(children) == sum(
            item["status"] == "materialized" for item in materialized
        )
        if coverage_ok:
            for item in materialized:
                row = children_by_card.get(item["card"])
                if item["status"] == "materialized":
                    if row is None or (
                        row["child_project_id"] != item["child_project_id"]
                        or row["title"] != item["title"]
                    ):
                        coverage_ok = False
                        break
                elif row is not None:
                    coverage_ok = False
                    break
        failed_materialization = any(
            item["status"] == "failed" for item in materialized
        )
        pending_materialization = any(
            item["status"] == "pending" for item in materialized
        )
        if target == "abstained":
            allowed = abstained and not selected and not children
        elif target == "ready":
            allowed = (
                not abstained
                and coverage_ok
                and not failed_materialization
                and not pending_materialization
                and all(row["qc_status"] == "pass" for row in children)
            )
        elif target == "blocked":
            allowed = (
                not abstained
                and coverage_ok
                and not pending_materialization
                and all(row["qc_status"] in ("pass", "blocked") for row in children)
                and (
                    failed_materialization
                    or any(row["qc_status"] == "blocked" for row in children)
                )
            )
        else:
            allowed = True
        if not allowed:
            return (
                {
                    "ok": False,
                    "status": "run_finalization_refused",
                    "requested_status": target,
                    "children": [dict(row) for row in children],
                    "selected_count": len(selected),
                    "materialization": materialized,
                    "coverage_ok": coverage_ok,
                },
                5,
                [],
            )
        conn.execute(
            "UPDATE runs SET status=?,phase=?,updated_at_s=? WHERE run_id=?",
            (
                target,
                "complete" if target in ("ready", "blocked", "abstained") else run["phase"],
                now,
                request["run_id"],
            ),
        )
        event = {
            "event": "run_status_set",
            "run_id": request["run_id"],
            "details": {"status": target},
        }
        return (
            {"ok": True, "status": "run_status_set", "run_status": target},
            0,
            [event],
        )

    return _mutate(path, "finalize-run", args.op_id, request, action)


def _decoded_child(row: sqlite3.Row) -> dict:
    result = dict(row)
    for key in (
        "assignment_json",
        "recast_input_json",
        "recast_json",
        "child_result_json",
        "qc_result_json",
        "exclusions_json",
    ):
        raw = result.pop(key)
        result[key.removesuffix("_json")] = json.loads(raw) if raw is not None else None
    return result


def _decoded_lease(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    result = dict(row)
    raw = result.pop("outstanding_job_ids_json")
    result["outstanding_job_ids"] = json.loads(raw) if raw is not None else None
    return result


def _decoded_call_permit(row: sqlite3.Row) -> dict:
    result = dict(row)
    raw = result.pop("outstanding_job_ids_json")
    result["outstanding_job_ids"] = json.loads(raw) if raw is not None else None
    return result


def _decoded_task_intent(row: sqlite3.Row) -> dict:
    result = dict(row)
    result["assignment"] = json.loads(result.pop("assignment_json"))
    result["next_action"] = {
        "prepared": "begin_child_task_create",
        "dispatching": "reconcile_create_outcome_never_retry",
        "queued": "resolve_client_thread",
        "resolved": "register_child",
        "consumed": "none",
    }[result["state"]]
    return result


def resume(args: argparse.Namespace, path: Path) -> int:
    run_id = _required_id("run_id", args.run_id)
    conn = _connect(path)
    try:
        _validate_schema(conn)
        conn.execute("BEGIN")
        run = _run(conn, run_id)
        if run is None:
            conn.commit()
            _emit({"ok": False, "status": "run_not_found", "run_id": run_id})
            return 3
        children = conn.execute(
            "SELECT * FROM children WHERE run_id=? ORDER BY card,child_project_id",
            (run_id,),
        ).fetchall()
        task_intents = conn.execute(
            "SELECT * FROM child_task_intents WHERE run_id=? ORDER BY card,child_project_id",
            (run_id,),
        ).fetchall()
        live = conn.execute("SELECT * FROM leases WHERE state='live'").fetchone()
        frozen = conn.execute(
            "SELECT * FROM leases WHERE run_id=? AND state='frozen' "
            "ORDER BY lease_generation DESC",
            (run_id,),
        ).fetchall()
        snapshots = conn.execute(
            "SELECT * FROM run_snapshots WHERE run_id=? ORDER BY snapshot_id",
            (run_id,),
        ).fetchall()
        attempts = conn.execute(
            "SELECT * FROM attempt_results WHERE run_id=? ORDER BY result_id",
            (run_id,),
        ).fetchall()
        call_permits = conn.execute(
            "SELECT * FROM call_permits WHERE run_id=? ORDER BY started_at_s,call_id",
            (run_id,),
        ).fetchall()
        blocked_phase = next(
            (
                row
                for row in reversed(snapshots)
                if row["checkpoint_status"] == "blocked"
            ),
            None,
        )
        frozen_for_finalization = (
            blocked_phase is not None or run["coordinator_result_json"] is not None
        )
        task_role_collisions = _task_role_collisions(conn)
        can_grant_run = (
            live is None
            and not frozen_for_finalization
            and not task_role_collisions
            and run["status"] not in ("ready", "blocked", "abstained")
        )
        can_grant_child = (
            live is None
            and not frozen_for_finalization
            and not task_role_collisions
            and run["status"] not in ("ready", "blocked", "abstained")
            and run["parent_project_id"] is not None
            and run["selection_fingerprint"] is not None
        )
        decoded_snapshots = []
        for row in snapshots:
            item = dict(row)
            item["data"] = json.loads(item.pop("data_json"))
            decoded_snapshots.append(item)
        decoded_attempts = []
        for row in attempts:
            item = dict(row)
            item["result"] = json.loads(item.pop("result_json"))
            item["exclusions"] = json.loads(item.pop("exclusions_json"))
            decoded_attempts.append(item)
        decoded_run = dict(run)
        raw_run_result = decoded_run.pop("coordinator_result_json")
        decoded_run["coordinator_result"] = (
            json.loads(raw_run_result) if raw_run_result is not None else None
        )
        response = {
            "ok": True,
            "status": (
                "registry_task_role_collision"
                if task_role_collisions
                else "live_lease_requires_reconciliation"
                if live is not None
                else "run_result_frozen_pending_finalization"
                if run["coordinator_result_json"] is not None
                else "blocked_pending_finalization"
                if blocked_phase is not None
                else run["status"]
                if run["status"] in ("ready", "blocked", "abstained")
                else "resumable"
            ),
            "can_grant_run_lease": can_grant_run,
            "can_grant_child_lease": can_grant_child,
            "run": decoded_run,
            "run_snapshots": decoded_snapshots,
            "child_task_intents": [
                _decoded_task_intent(row) for row in task_intents
            ],
            "children": [_decoded_child(row) for row in children],
            "attempt_results": decoded_attempts,
            "call_permits": [_decoded_call_permit(row) for row in call_permits],
            "global_live_lease": _decoded_lease(live),
            "frozen_leases": [_decoded_lease(row) for row in frozen],
            "task_role_collisions": task_role_collisions,
        }
        conn.commit()
        _emit(response)
        return 0
    finally:
        if conn.in_transaction:
            conn.rollback()
        conn.close()


def audit(args: argparse.Namespace, path: Path) -> int:
    conn = _connect(path)
    try:
        _validate_schema(conn)
        conn.execute("BEGIN")
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = [tuple(row) for row in conn.execute("PRAGMA foreign_key_check")]
        live = conn.execute(
            "SELECT lease_id,lease_generation,run_id,child_project_id,purpose,"
            "holder_thread_id,holder_host_id FROM leases WHERE state='live'"
        ).fetchall()
        in_flight_calls = conn.execute(
            "SELECT call_id,lease_generation,run_id,child_project_id "
            "FROM call_permits WHERE state='in_flight'"
        ).fetchall()
        invalid_in_flight_calls = conn.execute(
            "SELECT p.call_id,p.lease_generation,p.run_id,p.child_project_id,"
            "l.state AS lease_state FROM call_permits p "
            "LEFT JOIN leases l ON l.lease_generation=p.lease_generation "
            "WHERE p.state='in_flight' AND (l.lease_generation IS NULL OR "
            "l.state!='live' OR l.run_id!=p.run_id OR "
            "l.child_project_id IS NOT p.child_project_id)"
        ).fetchall()
        inconsistent_tasks = conn.execute(
            "SELECT child_project_id,task_state,child_status FROM children "
            "WHERE task_state='live' AND child_project_id NOT IN "
            "(SELECT child_project_id FROM leases WHERE state='live' "
            "AND holder_kind='child')"
        ).fetchall()
        reverse_task_mismatch = conn.execute(
            "SELECT l.child_project_id,l.lease_generation,c.task_state FROM leases l "
            "JOIN children c ON c.child_project_id=l.child_project_id "
            "WHERE l.state='live' AND l.holder_kind='child' AND c.task_state!='live'"
        ).fetchall()
        project_role_collisions = conn.execute(
            "SELECT r.run_id,c.run_id AS child_run_id,r.parent_project_id "
            "FROM runs r JOIN children c ON c.child_project_id=r.parent_project_id"
        ).fetchall()
        task_role_collisions = _task_role_collisions(conn)
        task_intent_mapping_errors = conn.execute(
            "SELECT i.client_request_id,i.state,i.run_id,i.child_project_id,i.card,"
            "c.child_project_id AS registered_child_project_id FROM child_task_intents i "
            "LEFT JOIN children c ON c.child_project_id=i.child_project_id WHERE "
            "(i.state='consumed' AND (c.child_project_id IS NULL OR c.run_id!=i.run_id OR "
            "c.card!=i.card OR c.title!=i.title OR c.assignment_id!=i.assignment_id OR "
            "c.assignment_fingerprint!=i.assignment_fingerprint OR "
            "c.assignment_json!=i.assignment_json OR "
            "c.assignment_validated_digest!=i.assignment_validated_digest OR "
            "c.thread_id!=i.thread_id OR c.host_id!=i.host_id)) OR "
            "(i.state!='consumed' AND c.child_project_id IS NOT NULL)"
        ).fetchall()
        children_without_consumed_intents = conn.execute(
            "SELECT c.child_project_id,c.run_id,c.card FROM children c LEFT JOIN "
            "child_task_intents i ON i.child_project_id=c.child_project_id AND "
            "i.state='consumed' WHERE i.client_request_id IS NULL"
        ).fetchall()
        terminal_open_task_intents = conn.execute(
            "SELECT i.client_request_id,i.run_id,i.state FROM child_task_intents i "
            "JOIN runs r ON r.run_id=i.run_id WHERE r.status IN "
            "('ready','blocked','abstained') AND i.state!='consumed'"
        ).fetchall()
        terminal_phase_errors = conn.execute(
            "SELECT run_id,status,phase FROM runs WHERE status IN "
            "('ready','blocked','abstained') AND phase!='complete'"
        ).fetchall()
        terminal_result_errors = conn.execute(
            "SELECT run_id,status FROM runs WHERE status IN "
            "('ready','blocked','abstained') AND "
            "(coordinator_result_json IS NULL OR coordinator_result_validated_digest IS NULL)"
        ).fetchall()
        artifact_digest_errors = []
        for row in conn.execute(
            "SELECT run_id,coordinator_result_json AS artifact_json,"
            "coordinator_result_validated_digest AS digest FROM runs "
            "WHERE coordinator_result_json IS NOT NULL"
        ):
            if row["digest"] is None or _artifact_digest(
                json.loads(row["artifact_json"])
            ) != row["digest"]:
                artifact_digest_errors.append(
                    {"kind": "coordinator_result", "id": row["run_id"]}
                )
        for row in conn.execute(
            "SELECT child_project_id,assignment_json,assignment_validated_digest,"
            "recast_input_json,recast_input_validated_digest,recast_json,"
            "recast_validated_digest FROM children"
        ):
            for label, json_key, digest_key in (
                ("assignment", "assignment_json", "assignment_validated_digest"),
                ("recast_input", "recast_input_json", "recast_input_validated_digest"),
                ("recast_decision", "recast_json", "recast_validated_digest"),
            ):
                raw, digest = row[json_key], row[digest_key]
                if raw is None and digest is None:
                    continue
                if raw is None or digest is None or _artifact_digest(json.loads(raw)) != digest:
                    artifact_digest_errors.append({
                        "kind": label, "id": row["child_project_id"]
                    })
        for row in conn.execute(
            "SELECT client_request_id,assignment_json,assignment_validated_digest "
            "FROM child_task_intents"
        ):
            if _artifact_digest(json.loads(row["assignment_json"])) != row[
                "assignment_validated_digest"
            ]:
                artifact_digest_errors.append({
                    "kind": "task_intent_assignment",
                    "id": row["client_request_id"],
                })
        for row in conn.execute(
            "SELECT result_id,result_json,validated_digest FROM attempt_results"
        ):
            if _artifact_digest(json.loads(row["result_json"])) != row["validated_digest"]:
                artifact_digest_errors.append({
                    "kind": "attempt_result", "id": row["result_id"]
                })
        okay = (
            integrity == "ok"
            and not foreign_keys
            and len(live) <= 1
            and len(in_flight_calls) <= 1
            and not invalid_in_flight_calls
            and not inconsistent_tasks
            and not reverse_task_mismatch
            and not project_role_collisions
            and not task_role_collisions
            and not task_intent_mapping_errors
            and not children_without_consumed_intents
            and not terminal_open_task_intents
            and not terminal_phase_errors
            and not terminal_result_errors
            and not artifact_digest_errors
        )
        meta = conn.execute("SELECT * FROM registry_meta WHERE singleton=1").fetchone()
        response = {
            "ok": okay,
            "status": "pass" if okay else "fail",
            "integrity_check": integrity,
            "foreign_key_errors": foreign_keys,
            "global_live_lease_count": len(live),
            "global_live_lease": dict(live[0]) if live else None,
            "global_in_flight_call_count": len(in_flight_calls),
            "global_in_flight_call": (
                dict(in_flight_calls[0]) if in_flight_calls else None
            ),
            "invalid_in_flight_calls": [dict(row) for row in invalid_in_flight_calls],
            "inconsistent_live_tasks": [dict(row) for row in inconsistent_tasks],
            "live_child_lease_task_mismatches": [
                dict(row) for row in reverse_task_mismatch
            ],
            "project_role_collisions": [dict(row) for row in project_role_collisions],
            "task_role_collisions": task_role_collisions,
            "task_intent_mapping_errors": [
                dict(row) for row in task_intent_mapping_errors
            ],
            "children_without_consumed_task_intents": [
                dict(row) for row in children_without_consumed_intents
            ],
            "terminal_open_task_intents": [
                dict(row) for row in terminal_open_task_intents
            ],
            "terminal_phase_errors": [dict(row) for row in terminal_phase_errors],
            "terminal_result_errors": [dict(row) for row in terminal_result_errors],
            "artifact_digest_errors": artifact_digest_errors,
            "registry_instance_id": meta["registry_instance_id"],
            "next_lease_generation": meta["next_lease_generation"],
        }
        conn.commit()
        _emit(response)
        return 0 if okay else 6
    finally:
        if conn.in_transaction:
            conn.rollback()
        conn.close()


def _add_lease_identity(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--child-project-id", required=True, type=int)
    parser.add_argument("--lease-id", required=True)
    parser.add_argument("--lease-generation", required=True, type=int)
    parser.add_argument("--purpose", required=True, choices=LEASE_PURPOSES)
    parser.add_argument("--repair-round", required=True, type=int)
    parser.add_argument("--selection-fingerprint", required=True)
    parser.add_argument("--assignment-id", required=True)
    parser.add_argument("--assignment-fingerprint", required=True)
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("--host-id", required=True)


def _add_task_intent_transition_identity(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--client-request-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--child-project-id", required=True, type=int)
    parser.add_argument("--card", required=True, type=int)
    parser.add_argument("--assignment-id", required=True)
    parser.add_argument("--assignment-fingerprint", required=True)
    parser.add_argument("--coordinator-thread-id", required=True)
    parser.add_argument("--coordinator-host-id", required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=default_registry_path())
    sub = parser.add_subparsers(dest="command", required=True)

    init_cmd = sub.add_parser("init")
    init_cmd.set_defaults(handler=init_registry)

    create_cmd = sub.add_parser("create-run")
    create_cmd.add_argument("--run-id", required=True)
    create_cmd.add_argument("--topic", required=True)
    create_cmd.add_argument("--coordinator-thread-id", required=True)
    create_cmd.add_argument("--coordinator-host-id", required=True)
    create_cmd.add_argument("--op-id", required=True)
    create_cmd.set_defaults(handler=create_run)

    grant_run_cmd = sub.add_parser("grant-run-lease")
    grant_run_cmd.add_argument("--run-id", required=True)
    grant_run_cmd.add_argument("--lease-id", required=True)
    grant_run_cmd.add_argument("--phase", required=True, choices=RUN_LEASE_PHASES)
    grant_run_cmd.add_argument("--op-id", required=True)
    grant_run_cmd.set_defaults(handler=grant_run_lease)

    check_run_cmd = sub.add_parser("check-run-lease")
    check_run_cmd.add_argument("--run-id", required=True)
    check_run_cmd.add_argument("--lease-id", required=True)
    check_run_cmd.add_argument("--lease-generation", required=True, type=int)
    check_run_cmd.add_argument("--phase", required=True, choices=RUN_LEASE_PHASES)
    check_run_cmd.add_argument("--thread-id", required=True)
    check_run_cmd.add_argument("--host-id", required=True)
    check_run_cmd.set_defaults(handler=check_run_lease)

    begin_run_call_cmd = sub.add_parser("begin-run-call")
    begin_run_call_cmd.add_argument("--run-id", required=True)
    begin_run_call_cmd.add_argument("--lease-id", required=True)
    begin_run_call_cmd.add_argument("--lease-generation", required=True, type=int)
    begin_run_call_cmd.add_argument(
        "--phase", required=True, choices=RUN_LEASE_PHASES
    )
    begin_run_call_cmd.add_argument("--thread-id", required=True)
    begin_run_call_cmd.add_argument("--host-id", required=True)
    begin_run_call_cmd.add_argument("--call-id", required=True)
    begin_run_call_cmd.add_argument("--op-id", required=True)
    begin_run_call_cmd.set_defaults(handler=begin_run_call)

    end_run_call_cmd = sub.add_parser("end-run-call")
    end_run_call_cmd.add_argument("--run-id", required=True)
    end_run_call_cmd.add_argument("--lease-id", required=True)
    end_run_call_cmd.add_argument("--lease-generation", required=True, type=int)
    end_run_call_cmd.add_argument(
        "--phase", required=True, choices=RUN_LEASE_PHASES
    )
    end_run_call_cmd.add_argument("--thread-id", required=True)
    end_run_call_cmd.add_argument("--host-id", required=True)
    end_run_call_cmd.add_argument("--call-id", required=True)
    end_run_call_cmd.add_argument("--outstanding-job-ids-json", required=True)
    end_run_call_cmd.add_argument("--op-id", required=True)
    end_run_call_cmd.set_defaults(handler=end_run_call)

    snapshot_cmd = sub.add_parser("record-run-snapshot")
    snapshot_cmd.add_argument("--run-id", required=True)
    snapshot_cmd.add_argument("--lease-id", required=True)
    snapshot_cmd.add_argument("--lease-generation", required=True, type=int)
    snapshot_cmd.add_argument("--phase", required=True, choices=RUN_LEASE_PHASES)
    snapshot_cmd.add_argument("--thread-id", required=True)
    snapshot_cmd.add_argument("--host-id", required=True)
    snapshot_cmd.add_argument(
        "--checkpoint-status", required=True, choices=CHECKPOINT_STATUSES
    )
    snapshot_cmd.add_argument("--data-json", required=True)
    snapshot_cmd.add_argument("--op-id", required=True)
    snapshot_cmd.set_defaults(handler=record_run_snapshot)

    close_run_cmd = sub.add_parser("close-run-lease")
    close_run_cmd.add_argument("--run-id", required=True)
    close_run_cmd.add_argument("--lease-id", required=True)
    close_run_cmd.add_argument("--lease-generation", required=True, type=int)
    close_run_cmd.add_argument("--phase", required=True, choices=RUN_LEASE_PHASES)
    close_run_cmd.add_argument("--coordinator-thread-id", required=True)
    close_run_cmd.add_argument("--coordinator-host-id", required=True)
    close_run_cmd.add_argument(
        "--action", required=True, choices=("release", "freeze", "revoke")
    )
    close_run_cmd.add_argument(
        "--checkpoint-status", required=True, choices=CHECKPOINT_STATUSES
    )
    close_run_cmd.add_argument("--outstanding-job-ids-json", required=True)
    close_run_cmd.add_argument("--reason", required=True)
    close_run_cmd.add_argument("--op-id", required=True)
    close_run_cmd.set_defaults(handler=close_run_lease)

    bind_cmd = sub.add_parser("bind-run")
    bind_cmd.add_argument("--run-id", required=True)
    bind_cmd.add_argument("--parent-project-id", required=True, type=int)
    bind_cmd.add_argument("--selection-fingerprint", required=True)
    bind_cmd.add_argument("--op-id", required=True)
    bind_cmd.set_defaults(handler=bind_run)

    prepare_task_cmd = sub.add_parser("prepare-child-task-intent")
    prepare_task_cmd.add_argument("--client-request-id", required=True)
    prepare_task_cmd.add_argument("--run-id", required=True)
    prepare_task_cmd.add_argument("--selection-fingerprint", required=True)
    prepare_task_cmd.add_argument("--child-project-id", required=True, type=int)
    prepare_task_cmd.add_argument("--card", required=True, type=int)
    prepare_task_cmd.add_argument("--title", required=True)
    prepare_task_cmd.add_argument("--assignment-id", required=True)
    prepare_task_cmd.add_argument("--assignment-fingerprint", required=True)
    prepare_task_cmd.add_argument("--assignment-json", required=True)
    prepare_task_cmd.add_argument("--coordinator-thread-id", required=True)
    prepare_task_cmd.add_argument("--coordinator-host-id", required=True)
    prepare_task_cmd.add_argument("--op-id", required=True)
    prepare_task_cmd.set_defaults(handler=prepare_child_task_intent)

    begin_task_cmd = sub.add_parser("begin-child-task-create")
    _add_task_intent_transition_identity(begin_task_cmd)
    begin_task_cmd.add_argument("--op-id", required=True)
    begin_task_cmd.set_defaults(handler=begin_child_task_create)

    queued_task_cmd = sub.add_parser("record-child-task-queued")
    _add_task_intent_transition_identity(queued_task_cmd)
    queued_task_cmd.add_argument("--client-thread-id", required=True)
    queued_task_cmd.add_argument("--op-id", required=True)
    queued_task_cmd.set_defaults(handler=record_child_task_queued)

    resolve_task_cmd = sub.add_parser("resolve-child-task-intent")
    _add_task_intent_transition_identity(resolve_task_cmd)
    resolve_task_cmd.add_argument("--client-thread-id")
    resolve_task_cmd.add_argument("--thread-id", required=True)
    resolve_task_cmd.add_argument("--host-id", required=True)
    resolve_task_cmd.add_argument("--op-id", required=True)
    resolve_task_cmd.set_defaults(handler=resolve_child_task_intent)

    child_cmd = sub.add_parser("register-child")
    child_cmd.add_argument("--client-request-id", required=True)
    child_cmd.add_argument("--run-id", required=True)
    child_cmd.add_argument("--selection-fingerprint", required=True)
    child_cmd.add_argument("--child-project-id", required=True, type=int)
    child_cmd.add_argument("--card", required=True, type=int)
    child_cmd.add_argument("--title", required=True)
    child_cmd.add_argument("--thread-id", required=True)
    child_cmd.add_argument("--host-id", required=True)
    child_cmd.add_argument("--assignment-id", required=True)
    child_cmd.add_argument("--assignment-fingerprint", required=True)
    child_cmd.add_argument("--assignment-json", required=True)
    child_cmd.add_argument("--op-id", required=True)
    child_cmd.set_defaults(handler=register_child)

    recast_input_cmd = sub.add_parser("record-recast-input")
    recast_input_cmd.add_argument("--run-id", required=True)
    recast_input_cmd.add_argument("--child-project-id", required=True, type=int)
    recast_input_cmd.add_argument("--coordinator-thread-id", required=True)
    recast_input_cmd.add_argument("--coordinator-host-id", required=True)
    recast_input_cmd.add_argument("--recast-json", required=True)
    recast_input_cmd.add_argument("--op-id", required=True)
    recast_input_cmd.set_defaults(handler=record_recast_input)

    recast_cmd = sub.add_parser("record-recast")
    recast_cmd.add_argument("--run-id", required=True)
    recast_cmd.add_argument("--child-project-id", required=True, type=int)
    recast_cmd.add_argument("--coordinator-thread-id", required=True)
    recast_cmd.add_argument("--coordinator-host-id", required=True)
    recast_cmd.add_argument("--approval", required=True, choices=("approved", "blocked"))
    recast_cmd.add_argument("--approved-candidate-id")
    recast_cmd.add_argument("--approval-reason", required=True)
    recast_cmd.add_argument("--recast-json", required=True)
    recast_cmd.add_argument("--op-id", required=True)
    recast_cmd.set_defaults(handler=record_recast)

    block_child_cmd = sub.add_parser("block-child-before-mutation")
    block_child_cmd.add_argument("--run-id", required=True)
    block_child_cmd.add_argument("--child-project-id", required=True, type=int)
    block_child_cmd.add_argument("--coordinator-thread-id", required=True)
    block_child_cmd.add_argument("--coordinator-host-id", required=True)
    block_child_cmd.add_argument("--reason", required=True)
    block_child_cmd.add_argument("--op-id", required=True)
    block_child_cmd.set_defaults(handler=block_child_before_mutation)

    grant_cmd = sub.add_parser("grant-lease")
    grant_cmd.add_argument("--run-id", required=True)
    grant_cmd.add_argument("--child-project-id", required=True, type=int)
    grant_cmd.add_argument("--lease-id", required=True)
    grant_cmd.add_argument("--purpose", required=True, choices=LEASE_PURPOSES)
    grant_cmd.add_argument("--repair-round", required=True, type=int)
    grant_cmd.add_argument("--selection-fingerprint", required=True)
    grant_cmd.add_argument("--assignment-id", required=True)
    grant_cmd.add_argument("--assignment-fingerprint", required=True)
    grant_cmd.add_argument("--op-id", required=True)
    grant_cmd.set_defaults(handler=grant_lease)

    check_cmd = sub.add_parser("check-lease")
    _add_lease_identity(check_cmd)
    check_cmd.set_defaults(handler=check_lease)

    begin_call_cmd = sub.add_parser("begin-call")
    _add_lease_identity(begin_call_cmd)
    begin_call_cmd.add_argument("--call-id", required=True)
    begin_call_cmd.add_argument("--op-id", required=True)
    begin_call_cmd.set_defaults(handler=begin_call)

    end_call_cmd = sub.add_parser("end-call")
    _add_lease_identity(end_call_cmd)
    end_call_cmd.add_argument("--call-id", required=True)
    end_call_cmd.add_argument("--outstanding-job-ids-json", required=True)
    end_call_cmd.add_argument("--op-id", required=True)
    end_call_cmd.set_defaults(handler=end_call)

    child_result_cmd = sub.add_parser("record-child-result")
    _add_lease_identity(child_result_cmd)
    child_result_cmd.add_argument(
        "--status", required=True,
        choices=("ready", "needs_repair", "blocked", "paused_safe")
    )
    child_result_cmd.add_argument("--result-json", required=True)
    child_result_cmd.add_argument("--exclusions-json", required=True)
    child_result_cmd.add_argument("--op-id", required=True)
    child_result_cmd.set_defaults(handler=record_child_result)

    qc_result_cmd = sub.add_parser("record-qc-result")
    _add_lease_identity(qc_result_cmd)
    qc_result_cmd.add_argument(
        "--status", required=True, choices=("pass", "repair_required", "blocked")
    )
    qc_result_cmd.add_argument("--result-json", required=True)
    qc_result_cmd.add_argument("--exclusions-json", required=True)
    qc_result_cmd.add_argument("--op-id", required=True)
    qc_result_cmd.set_defaults(handler=record_qc_result)

    close_cmd = sub.add_parser("close-lease")
    close_cmd.add_argument("--run-id", required=True)
    close_cmd.add_argument("--child-project-id", required=True, type=int)
    close_cmd.add_argument("--lease-id", required=True)
    close_cmd.add_argument("--lease-generation", required=True, type=int)
    close_cmd.add_argument("--coordinator-thread-id", required=True)
    close_cmd.add_argument("--coordinator-host-id", required=True)
    close_cmd.add_argument("--action", required=True, choices=("release", "freeze", "revoke"))
    close_cmd.add_argument(
        "--checkpoint-status", required=True, choices=CHECKPOINT_STATUSES
    )
    close_cmd.add_argument("--outstanding-job-ids-json", required=True)
    close_cmd.add_argument("--reason", required=True)
    close_cmd.add_argument("--op-id", required=True)
    close_cmd.set_defaults(handler=close_lease)

    run_result_cmd = sub.add_parser("record-run-result")
    run_result_cmd.add_argument("--run-id", required=True)
    run_result_cmd.add_argument("--coordinator-thread-id", required=True)
    run_result_cmd.add_argument("--coordinator-host-id", required=True)
    run_result_cmd.add_argument("--result-json", required=True)
    run_result_cmd.add_argument("--op-id", required=True)
    run_result_cmd.set_defaults(handler=record_run_result)

    finalize_cmd = sub.add_parser("finalize-run")
    finalize_cmd.add_argument("--run-id", required=True)
    finalize_cmd.add_argument("--coordinator-thread-id", required=True)
    finalize_cmd.add_argument("--coordinator-host-id", required=True)
    finalize_cmd.add_argument(
        "--status", required=True,
        choices=("ready", "blocked", "abstained", "paused_safe")
    )
    finalize_cmd.add_argument("--op-id", required=True)
    finalize_cmd.set_defaults(handler=finalize_run)

    resume_cmd = sub.add_parser("resume")
    resume_cmd.add_argument("--run-id", required=True)
    resume_cmd.set_defaults(handler=resume)

    audit_cmd = sub.add_parser("audit")
    audit_cmd.set_defaults(handler=audit)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args, args.registry))
    except (ValueError, sqlite3.Error, OSError) as exc:
        _emit({"ok": False, "status": "error", "error": str(exc)})
        return 2


if __name__ == "__main__":
    sys.exit(main())
