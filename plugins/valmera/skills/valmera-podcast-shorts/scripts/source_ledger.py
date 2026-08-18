#!/usr/bin/env python3
"""Fenced SQLite ledger for YouTube source/reference allocation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import unquote_plus, urlsplit
import uuid


SCHEMA_VERSION = 2
APPLICATION_ID = 0x564C5352  # VLSR
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$", re.ASCII)
SHA256_RE = re.compile(r"^[a-fA-F0-9]{64}$", re.ASCII)
HEX = frozenset("0123456789abcdefABCDEF")
ROLES = ("source", "reference")


def default_ledger_path() -> Path:
    root = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    return root / "state" / "valmera-podcast-shorts" / "source-ledger.sqlite3"


def _valid_percent_escapes(text: str) -> bool:
    for pos, char in enumerate(text):
        if char == "%" and (pos + 2 >= len(text)
                            or text[pos + 1] not in HEX
                            or text[pos + 2] not in HEX):
            return False
    return True


def _query_pairs(query: str) -> list[tuple[str, str, str, str]]:
    if ";" in query or not _valid_percent_escapes(query):
        raise ValueError("Ambiguous or malformed YouTube query string")
    out = []
    if not query:
        return out
    for item in query.split("&"):
        raw_key, sep, raw_value = item.partition("=")
        if not sep:
            raw_value = ""
        out.append((raw_key, raw_value,
                    unquote_plus(raw_key), unquote_plus(raw_value)))
    return out


def youtube_video_id(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("YouTube identity must be a URL or video-ID string")
    if len(value) > 4096:
        raise ValueError("YouTube identity is too long")
    raw = value.strip(" \t\r\n\f\v")
    if (not raw or "\\" in raw
            or any(ord(char) < 32 or char.isspace() for char in raw)):
        raise ValueError("YouTube identity contains invalid whitespace or controls")
    if VIDEO_ID_RE.fullmatch(raw):
        if raw in ("videoseries", "live_stream"):
            raise ValueError("YouTube playlist/live sentinels are not video IDs")
        return raw

    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        raise ValueError("Use an absolute http(s) YouTube URL or a raw video ID")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("YouTube URLs may not contain user information")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Malformed YouTube URL port") from exc
    if port is not None:
        raise ValueError("YouTube URLs may not contain an explicit port")
    host = (parsed.hostname or "").lower()
    if not host or host.endswith(".") or "%" in parsed.path:
        raise ValueError("Malformed YouTube host or encoded path")

    allowed_youtube = {
        "youtube.com", "www.youtube.com", "m.youtube.com",
        "music.youtube.com",
    }
    allowed_nocookie = {"youtube-nocookie.com", "www.youtube-nocookie.com"}
    pairs = _query_pairs(parsed.query)
    path = parsed.path
    if "//" in path or "/./" in path or "/../" in path:
        raise ValueError("Ambiguous YouTube path")
    if path != "/" and path.endswith("/"):
        path = path[:-1]

    candidate = ""
    if host == "youtu.be":
        pieces = [piece for piece in path.split("/") if piece]
        if len(pieces) != 1:
            raise ValueError("A youtu.be URL must contain exactly one video ID")
        candidate = pieces[0]
        if any(decoded_key == "v" for _, _, decoded_key, _ in pairs):
            raise ValueError("Path-based YouTube URL may not also contain v=")
    elif host in allowed_youtube:
        if path == "/watch":
            v_pairs = [pair for pair in pairs if pair[2] == "v"]
            if len(v_pairs) != 1:
                raise ValueError("A watch URL must contain exactly one v parameter")
            raw_key, raw_value, _, decoded_value = v_pairs[0]
            if raw_key != "v" or raw_value != decoded_value:
                raise ValueError("The v parameter must be a literal video ID")
            candidate = decoded_value
        else:
            pieces = [piece for piece in path.split("/") if piece]
            if len(pieces) != 2 or pieces[0] not in ("shorts", "live", "embed"):
                raise ValueError("Unsupported YouTube video URL route")
            if any(decoded_key == "v" for _, _, decoded_key, _ in pairs):
                raise ValueError("Path-based YouTube URL may not also contain v=")
            candidate = pieces[1]
    elif host in allowed_nocookie:
        pieces = [piece for piece in path.split("/") if piece]
        if len(pieces) != 2 or pieces[0] != "embed":
            raise ValueError("youtube-nocookie URLs must use /embed/VIDEO_ID")
        if any(decoded_key == "v" for _, _, decoded_key, _ in pairs):
            raise ValueError("Path-based YouTube URL may not also contain v=")
        candidate = pieces[1]
    else:
        raise ValueError("URL is not on an accepted YouTube host")

    if not VIDEO_ID_RE.fullmatch(candidate) or candidate in ("videoseries", "live_stream"):
        raise ValueError("URL does not contain one valid YouTube video ID")
    return candidate


def canonical_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def _emit(payload: dict) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _request_hash(command: str, payload: dict) -> str:
    raw = json.dumps({"command": command, "payload": payload},
                     sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _db_time(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT CAST(strftime('%s','now') AS INTEGER)").fetchone()[0])


def _configure(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")


def _validate_schema(conn: sqlite3.Connection) -> None:
    app_id = int(conn.execute("PRAGMA application_id").fetchone()[0])
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if app_id != APPLICATION_ID or version != SCHEMA_VERSION:
        raise ValueError(
            f"Unrecognized ledger schema (application_id={app_id}, version={version}); "
            "refusing to replace or migrate it automatically")
    meta = conn.execute(
        "SELECT schema_version FROM ledger_meta WHERE singleton=1").fetchone()
    if meta is None or int(meta[0]) != SCHEMA_VERSION:
        raise ValueError("Ledger metadata version does not match the SQLite schema version")


def _open(path: Path, create: bool = False) -> sqlite3.Connection:
    path = path.expanduser().resolve()
    if not path.exists() and not create:
        raise ValueError(f"Ledger does not exist: {path}. Run init once; refusing to recreate silently")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    conn = sqlite3.connect(path, timeout=10.0, isolation_level=None)
    _configure(conn)
    return conn


LEDGER_META_SCHEMA = """CREATE TABLE ledger_meta (
  singleton INTEGER PRIMARY KEY CHECK(singleton=1),
  schema_version INTEGER NOT NULL,
  ledger_instance_id TEXT NOT NULL UNIQUE,
  created_at_s INTEGER NOT NULL
)"""

VIDEO_TABLE_SCHEMA = """CREATE TABLE videos (
  video_id TEXT PRIMARY KEY,
  canonical_url TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN
    ('reserved','project_creating','parent_created','uploading',
     'reconcile_required','uploaded','committed','released')),
  role TEXT NOT NULL CHECK(role IN ('source','reference')),
  topic TEXT NOT NULL,
  run_id TEXT NOT NULL,
  reservation_id TEXT NOT NULL,
  fence INTEGER NOT NULL CHECK(fence > 0),
  title TEXT NOT NULL DEFAULT '',
  project_title TEXT,
  parent_project_id INTEGER,
  asset_id INTEGER,
  sha256 TEXT,
  project_external_op_id TEXT,
  upload_external_op_id TEXT,
  reconcile_stage TEXT CHECK(reconcile_stage IS NULL OR
                             reconcile_stage IN ('project','upload')),
  storage_key TEXT,
  remote_job_id TEXT,
  reason TEXT,
  created_at_s INTEGER NOT NULL,
  updated_at_s INTEGER NOT NULL,
  CHECK(role='source' OR state NOT IN ('project_creating','parent_created')),
  CHECK(state NOT IN ('parent_created','uploading','uploaded','committed')
        OR parent_project_id IS NOT NULL),
  CHECK(state!='project_creating' OR
        (role='source' AND project_title IS NOT NULL
         AND project_external_op_id IS NOT NULL AND parent_project_id IS NULL)),
  CHECK(state!='uploading' OR upload_external_op_id IS NOT NULL),
  CHECK((state='reconcile_required' AND reconcile_stage IS NOT NULL) OR
        (state!='reconcile_required' AND reconcile_stage IS NULL)),
  CHECK(reconcile_stage IS NULL OR
        (reconcile_stage='project' AND role='source' AND parent_project_id IS NULL) OR
        (reconcile_stage='upload' AND parent_project_id IS NOT NULL))
)"""

VIDEO_INDEX_SCHEMA = """CREATE UNIQUE INDEX one_source_per_parent
  ON videos(parent_project_id)
  WHERE role='source' AND state!='released' AND parent_project_id IS NOT NULL"""

OPERATIONS_SCHEMA = """CREATE TABLE operations (
  op_id TEXT PRIMARY KEY,
  command TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  response_json TEXT NOT NULL,
  exit_code INTEGER NOT NULL,
  created_at_s INTEGER NOT NULL
)"""

EVENTS_SCHEMA = """CREATE TABLE events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  video_id TEXT NOT NULL,
  event TEXT NOT NULL,
  from_state TEXT,
  to_state TEXT NOT NULL,
  reservation_id TEXT NOT NULL,
  fence INTEGER NOT NULL,
  run_id TEXT NOT NULL,
  at_s INTEGER NOT NULL,
  details_json TEXT NOT NULL
)"""

SCHEMA_STATEMENTS = (
    LEDGER_META_SCHEMA,
    VIDEO_TABLE_SCHEMA,
    VIDEO_INDEX_SCHEMA,
    OPERATIONS_SCHEMA,
    EVENTS_SCHEMA,
)

V1_COLUMNS = {
    "ledger_meta": (
        "singleton", "schema_version", "ledger_instance_id", "created_at_s"),
    "videos": (
        "video_id", "canonical_url", "state", "role", "topic", "run_id",
        "reservation_id", "fence", "title", "parent_project_id", "asset_id",
        "sha256", "external_op_id", "storage_key", "remote_job_id", "reason",
        "created_at_s", "updated_at_s"),
    "operations": (
        "op_id", "command", "request_hash", "response_json", "exit_code",
        "created_at_s"),
    "events": (
        "event_id", "video_id", "event", "from_state", "to_state",
        "reservation_id", "fence", "run_id", "at_s", "details_json"),
}


def _assert_recognized_v1(conn: sqlite3.Connection) -> None:
    app_id = int(conn.execute("PRAGMA application_id").fetchone()[0])
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if app_id != APPLICATION_ID or version != 1:
        raise ValueError(
            f"Unrecognized ledger schema (application_id={app_id}, version={version}); "
            "only the exact v1 ledger can be migrated")
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    }
    if tables != set(V1_COLUMNS):
        raise ValueError(f"Ledger v1 table set is not recognized: {sorted(tables)}")
    for table, expected in V1_COLUMNS.items():
        actual = tuple(row[1] for row in conn.execute(f"PRAGMA table_info({table})"))
        if actual != expected:
            raise ValueError(f"Ledger v1 columns are not recognized for table {table}")
    meta = conn.execute(
        "SELECT schema_version FROM ledger_meta WHERE singleton=1").fetchall()
    if len(meta) != 1 or int(meta[0][0]) != 1:
        raise ValueError("Ledger v1 metadata is missing or inconsistent")
    invalid = int(conn.execute(
        """SELECT COUNT(*) FROM videos
           WHERE role NOT IN ('source','reference')
              OR state NOT IN ('reserved','uploading','reconcile_required',
                               'uploaded','committed','released')
              OR (state IN ('uploading','reconcile_required','uploaded','committed')
                  AND parent_project_id IS NULL)""").fetchone()[0])
    if invalid:
        raise ValueError(f"Ledger v1 contains {invalid} rows that cannot be migrated safely")
    if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise ValueError("Ledger v1 failed SQLite integrity_check")


def _backup_v1_locked(path: Path, lock_conn: sqlite3.Connection) -> Path:
    now = _db_time(lock_conn)
    backup_path = path.with_name(
        f"{path.name}.v1-backup-{now}-{uuid.uuid4().hex[:8]}.sqlite3")
    source = sqlite3.connect(path, timeout=10.0, isolation_level=None)
    destination = sqlite3.connect(backup_path, timeout=10.0, isolation_level=None)
    try:
        source.execute("PRAGMA query_only=ON")
        source.execute("PRAGMA busy_timeout=10000")
        destination.execute("PRAGMA synchronous=FULL")
        source.backup(destination)
        destination.commit()
        _assert_recognized_v1(destination)
        for table in V1_COLUMNS:
            source_count = int(source.execute(
                f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            backup_count = int(destination.execute(
                f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            if source_count != backup_count:
                raise ValueError(f"Ledger v1 backup row count mismatch for {table}")
    except Exception:
        destination.close()
        source.close()
        backup_path.unlink(missing_ok=True)
        raise
    else:
        destination.close()
        source.close()
    backup_path.chmod(0o600)
    return backup_path


def _migrate_v1(conn: sqlite3.Connection, path: Path) -> Path:
    conn.execute("BEGIN IMMEDIATE")
    backup_path = None
    try:
        _assert_recognized_v1(conn)
        backup_path = _backup_v1_locked(path, conn)
        before = {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in V1_COLUMNS
        }
        conn.execute(VIDEO_TABLE_SCHEMA.replace(
            "CREATE TABLE videos (", "CREATE TABLE videos_v2 (", 1))
        conn.execute("""INSERT INTO videos_v2(
                      video_id,canonical_url,state,role,topic,run_id,reservation_id,
                      fence,title,project_title,parent_project_id,asset_id,sha256,
                      project_external_op_id,upload_external_op_id,reconcile_stage,
                      storage_key,remote_job_id,reason,created_at_s,updated_at_s)
                      SELECT video_id,canonical_url,state,role,topic,run_id,
                      reservation_id,fence,title,NULL,parent_project_id,asset_id,
                      sha256,NULL,external_op_id,
                      CASE WHEN state='reconcile_required' THEN 'upload' ELSE NULL END,
                      storage_key,remote_job_id,reason,created_at_s,updated_at_s
                      FROM videos""")
        conn.execute("DROP TABLE videos")
        conn.execute("ALTER TABLE videos_v2 RENAME TO videos")
        conn.execute(VIDEO_INDEX_SCHEMA)
        conn.execute("UPDATE ledger_meta SET schema_version=? WHERE singleton=1",
                     (SCHEMA_VERSION,))
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        after = {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in V1_COLUMNS
        }
        if before != after:
            raise ValueError("Ledger migration row counts changed unexpectedly")
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Migrated ledger failed SQLite integrity_check")
        conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    if backup_path is None:
        raise ValueError("Ledger migration did not create its required backup")
    _validate_schema(conn)
    return backup_path


def init_ledger(args: argparse.Namespace, path: Path) -> int:
    existed = path.expanduser().resolve().exists()
    conn = _open(path, create=True)
    migration_backup = None
    try:
        if existed:
            app_id = int(conn.execute("PRAGMA application_id").fetchone()[0])
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if app_id == APPLICATION_ID and version == 1:
                migration_backup = _migrate_v1(conn, path.expanduser().resolve())
            else:
                _validate_schema(conn)
        else:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in SCHEMA_STATEMENTS:
                    conn.execute(statement)
                now = _db_time(conn)
                instance = str(uuid.uuid4())
                conn.execute("INSERT INTO ledger_meta VALUES(1,?,?,?)",
                             (SCHEMA_VERSION, instance, now))
                conn.execute(f"PRAGMA application_id={APPLICATION_ID}")
                conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                conn.commit()
            except Exception:
                if conn.in_transaction:
                    conn.rollback()
                raise
            path.expanduser().resolve().chmod(0o600)
        row = conn.execute("SELECT * FROM ledger_meta WHERE singleton=1").fetchone()
        if not existed:
            status = "initialized"
        elif migration_backup is not None:
            status = "migrated_v1_to_v2"
        else:
            status = "existing"
        response = {"ok": True, "status": status,
                    "ledger": str(path.expanduser().resolve()),
                    "ledger_instance_id": row["ledger_instance_id"],
                    "schema_version": row["schema_version"]}
        if migration_backup is not None:
            response["backup"] = str(migration_backup)
        _emit(response)
        return 0
    finally:
        conn.close()


def _row(conn: sqlite3.Connection, video_id: str):
    return conn.execute("SELECT * FROM videos WHERE video_id=?", (video_id,)).fetchone()


def _json_row(row) -> dict | None:
    return dict(row) if row is not None else None


def _reference_parent_gate(conn: sqlite3.Connection, run_id: str,
                           parent_project_id: int) -> tuple[str | None, dict | None]:
    """Validate the one parent a reference is allowed to target.

    References are subordinate to a run's committed source.  Checking this in
    the same BEGIN IMMEDIATE transaction as reserve/begin-upload closes the
    check-then-act gap and prevents a reference from being attached to an
    arbitrary project that merely happens to exist.
    """
    sources = conn.execute(
        """SELECT * FROM videos
           WHERE role='source' AND run_id=? AND state='committed'
           ORDER BY video_id""",
        (run_id,)).fetchall()
    if not sources:
        return "reference_source_not_committed", None
    if len(sources) != 1:
        return "reference_source_ambiguous", {
            "committed_source_video_ids": [row["video_id"] for row in sources]
        }
    source = sources[0]
    expected = source["parent_project_id"]
    if expected is None:
        return "reference_source_missing_parent", _json_row(source)
    if int(expected) != int(parent_project_id):
        return "reference_parent_mismatch", {
            "expected_parent_project_id": int(expected),
            "supplied_parent_project_id": int(parent_project_id),
            "source_video_id": source["video_id"],
        }
    return None, _json_row(source)


def _run_mutation(path: Path, command: str, op_id: str, request: dict, action) -> int:
    if not op_id or len(op_id) > 240:
        raise ValueError("--op-id must be a stable non-empty key of at most 240 characters")
    conn = _open(path)
    try:
        _validate_schema(conn)
        digest = _request_hash(command, request)
        conn.execute("BEGIN IMMEDIATE")
        prior = conn.execute("SELECT * FROM operations WHERE op_id=?", (op_id,)).fetchone()
        if prior:
            if prior["request_hash"] != digest:
                conn.rollback()
                _emit({"ok": False, "status": "idempotency_key_reused",
                       "op_id": op_id})
                return 7
            conn.rollback()
            print(prior["response_json"])
            return int(prior["exit_code"])

        now = _db_time(conn)
        response, code, event = action(conn, now)
        encoded = json.dumps(response, sort_keys=True, separators=(",", ":"))
        conn.execute("INSERT INTO operations VALUES(?,?,?,?,?,?)",
                     (op_id, command, digest, encoded, code, now))
        if event:
            conn.execute(
                "INSERT INTO events(video_id,event,from_state,to_state,reservation_id,"
                "fence,run_id,at_s,details_json) VALUES(?,?,?,?,?,?,?,?,?)",
                (event["video_id"], event["event"], event.get("from_state"),
                 event["to_state"], event["reservation_id"], event["fence"],
                 event["run_id"], now,
                 json.dumps(event.get("details", {}), sort_keys=True,
                            separators=(",", ":"))))
        conn.commit()
        print(encoded)
        return code
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()


def reserve(args: argparse.Namespace, path: Path) -> int:
    video_id = youtube_video_id(args.video)
    if args.role == "reference":
        if args.parent_project_id is None or args.parent_project_id <= 0:
            raise ValueError(
                "A reference reservation requires a positive --parent-project-id")
    elif args.parent_project_id is not None:
        raise ValueError("--parent-project-id is valid only with --role reference")
    request = {"video_id": video_id, "role": args.role, "topic": args.topic,
               "run_id": args.run_id, "reservation_id": args.reservation_id,
               "title": args.title}
    # Preserve the exact pre-binding request hash for source reservations so
    # an in-flight source operation from schema v2 still replays safely.
    if args.role == "reference":
        request["parent_project_id"] = args.parent_project_id

    def action(conn, now):
        if args.role == "reference":
            failure, source = _reference_parent_gate(
                conn, args.run_id, args.parent_project_id)
            if failure:
                code = 5 if failure == "reference_source_not_committed" else 6
                return ({"ok": False, "status": failure,
                         "video_id": video_id, "source": source}, code, None)
        old = _row(conn, video_id)
        if old and old["state"] != "released":
            return ({"ok": False, "status": "duplicate", "video_id": video_id,
                     "record": _json_row(old)}, 3, None)
        fence = (int(old["fence"]) + 1) if old else 1
        created = int(old["created_at_s"]) if old else now
        values = (video_id, canonical_url(video_id), "reserved", args.role,
                  args.topic.strip(), args.run_id, args.reservation_id, fence,
                  args.title.strip(), None, args.parent_project_id, None, None,
                  None, None, None,
                  None, None, None, created, now)
        conn.execute("""INSERT INTO videos VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                      ON CONFLICT(video_id) DO UPDATE SET
                      canonical_url=excluded.canonical_url,state=excluded.state,
                      role=excluded.role,topic=excluded.topic,run_id=excluded.run_id,
                      reservation_id=excluded.reservation_id,fence=excluded.fence,
                      title=excluded.title,project_title=NULL,
                      parent_project_id=excluded.parent_project_id,asset_id=NULL,
                      sha256=NULL,project_external_op_id=NULL,
                      upload_external_op_id=NULL,reconcile_stage=NULL,
                      storage_key=NULL,remote_job_id=NULL,reason=NULL,
                      updated_at_s=excluded.updated_at_s""",
                     values)
        row = _row(conn, video_id)
        response = {"ok": True, "status": "reserved", "video_id": video_id,
                    "reservation_id": args.reservation_id, "fence": fence,
                    "record": _json_row(row)}
        event = {"video_id": video_id, "event": "reserve",
                 "from_state": old["state"] if old else None,
                 "to_state": "reserved", "reservation_id": args.reservation_id,
                 "fence": fence, "run_id": args.run_id,
                 "details": {"role": args.role, "topic": args.topic.strip(),
                             "parent_project_id": args.parent_project_id}}
        return response, 0, event

    return _run_mutation(path, "reserve", args.op_id, request, action)


def _owned(row, args) -> bool:
    return bool(row and row["reservation_id"] == args.reservation_id
                and int(row["fence"]) == int(args.fence)
                and row["run_id"] == args.run_id)


def _external_op_id(value: str) -> str:
    marker = value.strip()
    if not marker or len(marker) > 240:
        raise ValueError(
            "--external-op-id must be a stable non-empty marker of at most 240 characters")
    return marker


def begin_project(args: argparse.Namespace, path: Path) -> int:
    video_id = youtube_video_id(args.video)
    external_op_id = _external_op_id(args.external_op_id)
    project_title = args.project_title.strip()
    if not project_title or external_op_id not in project_title:
        raise ValueError(
            "--project-title must be non-empty and contain the exact external operation marker")
    request = {"video_id": video_id, "run_id": args.run_id,
               "reservation_id": args.reservation_id, "fence": args.fence,
               "external_op_id": external_op_id, "project_title": project_title}

    def action(conn, now):
        old = _row(conn, video_id)
        if (not _owned(old, args) or old["state"] != "reserved"
                or old["role"] != "source"):
            return ({"ok": False, "status": "stale_or_invalid_source_reservation",
                     "video_id": video_id, "record": _json_row(old)}, 5, None)
        conn.execute("""UPDATE videos SET state='project_creating',project_title=?,
                      project_external_op_id=?,updated_at_s=? WHERE video_id=?""",
                     (project_title, external_op_id, now, video_id))
        event = {"video_id": video_id, "event": "begin_project",
                 "from_state": "reserved", "to_state": "project_creating",
                 "reservation_id": args.reservation_id, "fence": args.fence,
                 "run_id": args.run_id,
                 "details": {"external_op_id": external_op_id,
                             "project_title": project_title}}
        return ({"ok": True, "status": "project_creating", "video_id": video_id,
                 "record": _json_row(_row(conn, video_id))}, 0, event)

    return _run_mutation(path, "begin-project", args.op_id, request, action)


def mark_project(args: argparse.Namespace, path: Path) -> int:
    video_id = youtube_video_id(args.video)
    external_op_id = _external_op_id(args.external_op_id)
    if args.parent_project_id <= 0:
        raise ValueError("--parent-project-id must be positive")
    request = {"video_id": video_id, "run_id": args.run_id,
               "reservation_id": args.reservation_id, "fence": args.fence,
               "parent_project_id": args.parent_project_id,
               "external_op_id": external_op_id}

    def action(conn, now):
        old = _row(conn, video_id)
        if not _owned(old, args) or old["role"] != "source":
            return ({"ok": False, "status": "stale_or_invalid_source_reservation",
                     "video_id": video_id, "record": _json_row(old)}, 5, None)
        if old["project_external_op_id"] != external_op_id:
            return ({"ok": False, "status": "project_operation_mismatch",
                     "video_id": video_id, "record": _json_row(old)}, 6, None)
        bound = conn.execute(
            """SELECT * FROM videos WHERE role='source' AND state!='released'
               AND parent_project_id=? AND video_id!=?""",
            (args.parent_project_id, video_id)).fetchone()
        if bound:
            return ({"ok": False, "status": "parent_project_already_bound",
                     "video_id": video_id, "conflict": _json_row(bound)}, 6, None)
        if old["state"] == "parent_created":
            if old["parent_project_id"] == args.parent_project_id:
                return ({"ok": True, "status": "parent_created", "idempotent": True,
                         "video_id": video_id, "record": _json_row(old)}, 0, None)
            return ({"ok": False, "status": "conflicting_parent_project",
                     "video_id": video_id, "record": _json_row(old)}, 6, None)
        valid = old["state"] == "project_creating" or (
            old["state"] == "reconcile_required"
            and old["reconcile_stage"] == "project")
        if not valid:
            return ({"ok": False, "status": "project_result_not_expected",
                     "video_id": video_id, "record": _json_row(old)}, 5, None)
        conn.execute("""UPDATE videos SET state='parent_created',parent_project_id=?,
                      reconcile_stage=NULL,reason=NULL,updated_at_s=? WHERE video_id=?""",
                     (args.parent_project_id, now, video_id))
        event = {"video_id": video_id, "event": "mark_project",
                 "from_state": old["state"], "to_state": "parent_created",
                 "reservation_id": args.reservation_id, "fence": args.fence,
                 "run_id": args.run_id,
                 "details": {"parent_project_id": args.parent_project_id,
                             "external_op_id": external_op_id}}
        return ({"ok": True, "status": "parent_created", "idempotent": False,
                 "video_id": video_id,
                 "record": _json_row(_row(conn, video_id))}, 0, event)

    return _run_mutation(path, "mark-project", args.op_id, request, action)


def begin_upload(args: argparse.Namespace, path: Path) -> int:
    video_id = youtube_video_id(args.video)
    external_op_id = _external_op_id(args.external_op_id)
    if args.parent_project_id <= 0:
        raise ValueError("--parent-project-id must be positive")
    request = {"video_id": video_id, "run_id": args.run_id,
               "reservation_id": args.reservation_id, "fence": args.fence,
               "parent_project_id": args.parent_project_id,
               "external_op_id": external_op_id}

    def action(conn, now):
        old = _row(conn, video_id)
        if not _owned(old, args):
            return ({"ok": False, "status": "stale_or_invalid_reservation",
                     "video_id": video_id, "record": _json_row(old)}, 5, None)
        if old["role"] == "source":
            valid = (old["state"] == "parent_created"
                     and old["parent_project_id"] == args.parent_project_id)
        else:
            failure, source = _reference_parent_gate(
                conn, args.run_id, args.parent_project_id)
            if failure:
                code = 5 if failure == "reference_source_not_committed" else 6
                return ({"ok": False, "status": failure,
                         "video_id": video_id, "record": _json_row(old),
                         "source": source}, code, None)
            # A NULL binding is accepted only for a reservation migrated from
            # the pre-binding schema.  This transition atomically adopts the
            # already-validated committed source parent.  A non-NULL mismatch
            # is always rejected.
            bound_parent = old["parent_project_id"]
            valid = (old["role"] == "reference" and old["state"] == "reserved"
                     and (bound_parent is None
                          or int(bound_parent) == args.parent_project_id))
            if old["state"] == "reserved" and bound_parent is not None \
                    and int(bound_parent) != args.parent_project_id:
                return ({"ok": False, "status": "reference_parent_mismatch",
                         "video_id": video_id, "record": _json_row(old),
                         "source": source}, 6, None)
        if not valid:
            return ({"ok": False, "status": "upload_precondition_failed",
                     "video_id": video_id, "record": _json_row(old)}, 5, None)
        conn.execute("""UPDATE videos SET state='uploading',parent_project_id=?,
                      upload_external_op_id=?,reconcile_stage=NULL,reason=NULL,
                      updated_at_s=? WHERE video_id=?""",
                     (args.parent_project_id, external_op_id, now, video_id))
        row = _row(conn, video_id)
        event = {"video_id": video_id, "event": "begin_upload",
                 "from_state": old["state"], "to_state": "uploading",
                 "reservation_id": args.reservation_id, "fence": args.fence,
                 "run_id": args.run_id,
                 "details": {"parent_project_id": args.parent_project_id,
                             "external_op_id": external_op_id}}
        return ({"ok": True, "status": "uploading", "video_id": video_id,
                 "record": _json_row(row)}, 0, event)

    return _run_mutation(path, "begin-upload", args.op_id, request, action)


def mark_ambiguous(args: argparse.Namespace, path: Path) -> int:
    video_id = youtube_video_id(args.video)
    reason = args.reason.strip()
    if not reason:
        raise ValueError("--reason must be non-empty")
    request = {"video_id": video_id, "run_id": args.run_id,
               "reservation_id": args.reservation_id, "fence": args.fence,
               "reason": reason}

    def action(conn, now):
        old = _row(conn, video_id)
        if not _owned(old, args) or old["state"] not in (
                "project_creating", "uploading"):
            return ({"ok": False, "status": "stale_or_invalid_reservation",
                     "video_id": video_id, "record": _json_row(old)}, 5, None)
        stage = "project" if old["state"] == "project_creating" else "upload"
        conn.execute("UPDATE videos SET state='reconcile_required',reason=?,updated_at_s=? "
                     ",reconcile_stage=? WHERE video_id=?",
                     (reason, now, stage, video_id))
        event = {"video_id": video_id, "event": "mark_ambiguous",
                 "from_state": old["state"], "to_state": "reconcile_required",
                 "reservation_id": args.reservation_id, "fence": args.fence,
                 "run_id": args.run_id,
                 "details": {"reason": reason, "stage": stage}}
        return ({"ok": True, "status": "reconcile_required", "video_id": video_id,
                 "record": _json_row(_row(conn, video_id))}, 0, event)

    return _run_mutation(path, "mark-ambiguous", args.op_id, request, action)


def mark_uploaded(args: argparse.Namespace, path: Path) -> int:
    video_id = youtube_video_id(args.video)
    if not SHA256_RE.fullmatch(args.sha256):
        raise ValueError("--sha256 must be the downloaded file's 64-character SHA-256")
    request = {"video_id": video_id, "run_id": args.run_id,
               "reservation_id": args.reservation_id, "fence": args.fence,
               "parent_project_id": args.parent_project_id,
               "asset_id": args.asset_id, "sha256": args.sha256.lower(),
               "storage_key": args.storage_key, "remote_job_id": args.remote_job_id}

    def action(conn, now):
        old = _row(conn, video_id)
        valid_state = old and (old["state"] == "uploading" or (
            old["state"] == "reconcile_required"
            and old["reconcile_stage"] == "upload"))
        if not _owned(old, args) or not valid_state:
            return ({"ok": False, "status": "stale_or_invalid_reservation",
                     "video_id": video_id, "record": _json_row(old)}, 5, None)
        if old["role"] == "reference":
            failure, source = _reference_parent_gate(
                conn, args.run_id, args.parent_project_id)
            if failure:
                code = 5 if failure == "reference_source_not_committed" else 6
                return ({"ok": False, "status": failure,
                         "video_id": video_id, "record": _json_row(old),
                         "source": source}, code, None)
        if old["parent_project_id"] != args.parent_project_id:
            return ({"ok": False, "status": "project_mismatch",
                     "video_id": video_id, "record": _json_row(old)}, 6, None)
        conn.execute("""UPDATE videos SET state='uploaded',parent_project_id=?,asset_id=?,
                      sha256=?,storage_key=?,remote_job_id=?,reconcile_stage=NULL,
                      reason=NULL,updated_at_s=?
                      WHERE video_id=?""",
                     (args.parent_project_id, args.asset_id, args.sha256.lower(),
                      args.storage_key or None, args.remote_job_id or None, now,
                      video_id))
        event = {"video_id": video_id, "event": "mark_uploaded",
                 "from_state": old["state"], "to_state": "uploaded",
                 "reservation_id": args.reservation_id, "fence": args.fence,
                 "run_id": args.run_id,
                 "details": {"parent_project_id": args.parent_project_id,
                             "asset_id": args.asset_id}}
        return ({"ok": True, "status": "uploaded", "video_id": video_id,
                 "record": _json_row(_row(conn, video_id))}, 0, event)

    return _run_mutation(path, "mark-uploaded", args.op_id, request, action)


def commit_video(args: argparse.Namespace, path: Path) -> int:
    video_id = youtube_video_id(args.video)
    if not SHA256_RE.fullmatch(args.sha256):
        raise ValueError("--sha256 must be a 64-character SHA-256")
    request = {"video_id": video_id, "run_id": args.run_id,
               "reservation_id": args.reservation_id, "fence": args.fence,
               "asset_id": args.asset_id, "sha256": args.sha256.lower()}

    def action(conn, now):
        old = _row(conn, video_id)
        if old and old["role"] == "reference":
            if old["parent_project_id"] is None:
                return ({"ok": False, "status": "reference_parent_missing",
                         "video_id": video_id, "record": _json_row(old)}, 6, None)
            failure, source = _reference_parent_gate(
                conn, args.run_id, int(old["parent_project_id"]))
            if failure:
                code = 5 if failure == "reference_source_not_committed" else 6
                return ({"ok": False, "status": failure,
                         "video_id": video_id, "record": _json_row(old),
                         "source": source}, code, None)
        if (old and old["state"] == "committed" and _owned(old, args)
                and old["asset_id"] == args.asset_id
                and old["sha256"] == args.sha256.lower()):
            return ({"ok": True, "status": "committed", "idempotent": True,
                     "video_id": video_id, "record": _json_row(old)}, 0, None)
        if (not _owned(old, args) or old["state"] != "uploaded"
                or old["asset_id"] != args.asset_id
                or old["sha256"] != args.sha256.lower()):
            return ({"ok": False, "status": "stale_or_metadata_mismatch",
                     "video_id": video_id, "record": _json_row(old)}, 6, None)
        conn.execute("UPDATE videos SET state='committed',updated_at_s=? WHERE video_id=?",
                     (now, video_id))
        event = {"video_id": video_id, "event": "commit",
                 "from_state": "uploaded", "to_state": "committed",
                 "reservation_id": args.reservation_id, "fence": args.fence,
                 "run_id": args.run_id,
                 "details": {"asset_id": args.asset_id}}
        return ({"ok": True, "status": "committed", "idempotent": False,
                 "video_id": video_id, "record": _json_row(_row(conn, video_id))}, 0, event)

    return _run_mutation(path, "commit", args.op_id, request, action)


def release(args: argparse.Namespace, path: Path) -> int:
    video_id = youtube_video_id(args.video)
    reason = args.reason.strip()
    if not reason:
        raise ValueError("--reason must be non-empty")
    proof = args.authoritative_no_side_effect_proof.strip()
    if args.force and not proof:
        raise ValueError(
            "--force requires --authoritative-no-side-effect-proof with durable evidence")
    request = {"video_id": video_id, "run_id": args.run_id,
               "reservation_id": args.reservation_id, "fence": args.fence,
               "reason": reason, "force": args.force,
               "authoritative_no_side_effect_proof": proof}

    def action(conn, now):
        old = _row(conn, video_id)
        if not _owned(old, args):
            return ({"ok": False, "status": "stale_or_invalid_reservation",
                     "video_id": video_id, "record": _json_row(old)}, 5, None)
        allowed = old["state"] == "reserved" or (
            args.force and proof and old["state"] in
            ("project_creating", "parent_created", "uploading",
             "reconcile_required"))
        if not allowed:
            return ({"ok": False, "status": "release_refused",
                     "video_id": video_id, "record": _json_row(old)}, 6, None)
        conn.execute("""UPDATE videos SET state='released',reason=?,
                      reconcile_stage=NULL,updated_at_s=? WHERE video_id=?""",
                     (reason, now, video_id))
        event = {"video_id": video_id, "event": "release",
                 "from_state": old["state"], "to_state": "released",
                 "reservation_id": args.reservation_id, "fence": args.fence,
                 "run_id": args.run_id,
                 "details": {"reason": reason, "force": args.force,
                             "authoritative_no_side_effect_proof": proof}}
        return ({"ok": True, "status": "released", "video_id": video_id,
                 "record": _json_row(_row(conn, video_id))}, 0, event)

    return _run_mutation(path, "release", args.op_id, request, action)


def check(args: argparse.Namespace, path: Path) -> int:
    video_id = youtube_video_id(args.video)
    conn = _open(path)
    try:
        _validate_schema(conn)
        row = _row(conn, video_id)
        status = "available" if row is None or row["state"] == "released" else row["state"]
        _emit({"ok": True, "status": status, "video_id": video_id,
               "canonical_url": canonical_url(video_id), "record": _json_row(row)})
        return 0
    finally:
        conn.close()


def list_videos(_: argparse.Namespace, path: Path) -> int:
    conn = _open(path)
    try:
        _validate_schema(conn)
        rows = conn.execute("SELECT * FROM videos ORDER BY video_id").fetchall()
        meta = conn.execute("SELECT * FROM ledger_meta WHERE singleton=1").fetchone()
        _emit({"ok": True, "ledger": str(path.expanduser().resolve()),
               "ledger_instance_id": meta["ledger_instance_id"],
               "videos": [_json_row(row) for row in rows]})
        return 0
    finally:
        conn.close()


def audit(args: argparse.Namespace, path: Path) -> int:
    conn = _open(path)
    try:
        _validate_schema(conn)
        if args.video:
            video_id = youtube_video_id(args.video)
            rows = conn.execute("SELECT * FROM events WHERE video_id=? ORDER BY event_id",
                                (video_id,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM events ORDER BY event_id").fetchall()
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        _emit({"ok": integrity == "ok", "integrity": integrity,
               "events": [_json_row(row) for row in rows]})
        return 0 if integrity == "ok" else 8
    finally:
        conn.close()


def _add_owner(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("video")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--reservation-id", required=True)
    parser.add_argument("--fence", type=int, required=True)
    parser.add_argument("--op-id", required=True)


def build_parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--ledger", type=Path, default=default_ledger_path())
    sub = cli.add_subparsers(dest="command", required=True)

    init_cmd = sub.add_parser("init")
    init_cmd.set_defaults(handler=init_ledger)

    reserve_cmd = sub.add_parser("reserve")
    reserve_cmd.add_argument("video")
    reserve_cmd.add_argument("--role", choices=ROLES, required=True)
    reserve_cmd.add_argument("--topic", required=True)
    reserve_cmd.add_argument("--run-id", required=True)
    reserve_cmd.add_argument("--reservation-id", required=True)
    reserve_cmd.add_argument("--op-id", required=True)
    reserve_cmd.add_argument("--parent-project-id", type=int)
    reserve_cmd.add_argument("--title", default="")
    reserve_cmd.set_defaults(handler=reserve)

    begin_project_cmd = sub.add_parser("begin-project")
    _add_owner(begin_project_cmd)
    begin_project_cmd.add_argument("--external-op-id", required=True)
    begin_project_cmd.add_argument("--project-title", required=True)
    begin_project_cmd.set_defaults(handler=begin_project)

    project_cmd = sub.add_parser("mark-project")
    _add_owner(project_cmd)
    project_cmd.add_argument("--parent-project-id", type=int, required=True)
    project_cmd.add_argument("--external-op-id", required=True)
    project_cmd.set_defaults(handler=mark_project)

    begin_cmd = sub.add_parser("begin-upload")
    _add_owner(begin_cmd)
    begin_cmd.add_argument("--parent-project-id", type=int, required=True)
    begin_cmd.add_argument("--external-op-id", required=True)
    begin_cmd.set_defaults(handler=begin_upload)

    ambiguous_cmd = sub.add_parser("mark-ambiguous")
    _add_owner(ambiguous_cmd)
    ambiguous_cmd.add_argument("--reason", required=True)
    ambiguous_cmd.set_defaults(handler=mark_ambiguous)

    uploaded_cmd = sub.add_parser("mark-uploaded")
    _add_owner(uploaded_cmd)
    uploaded_cmd.add_argument("--parent-project-id", type=int, required=True)
    uploaded_cmd.add_argument("--asset-id", type=int, required=True)
    uploaded_cmd.add_argument("--sha256", required=True)
    uploaded_cmd.add_argument("--storage-key", default="")
    uploaded_cmd.add_argument("--remote-job-id", default="")
    uploaded_cmd.set_defaults(handler=mark_uploaded)

    commit_cmd = sub.add_parser("commit")
    _add_owner(commit_cmd)
    commit_cmd.add_argument("--asset-id", type=int, required=True)
    commit_cmd.add_argument("--sha256", required=True)
    commit_cmd.set_defaults(handler=commit_video)

    release_cmd = sub.add_parser("release")
    _add_owner(release_cmd)
    release_cmd.add_argument("--reason", required=True)
    release_cmd.add_argument("--force", action="store_true")
    release_cmd.add_argument("--authoritative-no-side-effect-proof", default="")
    release_cmd.set_defaults(handler=release)

    check_cmd = sub.add_parser("check")
    check_cmd.add_argument("video")
    check_cmd.set_defaults(handler=check)

    list_cmd = sub.add_parser("list")
    list_cmd.set_defaults(handler=list_videos)

    audit_cmd = sub.add_parser("audit")
    audit_cmd.add_argument("video", nargs="?")
    audit_cmd.set_defaults(handler=audit)
    return cli


def main() -> int:
    args = build_parser().parse_args()
    path = args.ledger.expanduser().resolve()
    try:
        return args.handler(args, path)
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)},
                         sort_keys=True, separators=(",", ":")), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
