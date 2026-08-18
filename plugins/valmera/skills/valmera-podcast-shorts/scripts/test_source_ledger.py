#!/usr/bin/env python3
"""Focused tests for source_ledger.py."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

import source_ledger


SCRIPT = Path(__file__).with_name("source_ledger.py")
VIDEO_ID = "dQw4w9WgXcQ"
REFERENCE_ID = "9bZkp7q19f0"
OTHER_REFERENCE_ID = "M7lc1UVf-VE"
SHA256 = "0123456789abcdef" * 4

V1_SCHEMA = """
CREATE TABLE ledger_meta (
  singleton INTEGER PRIMARY KEY CHECK(singleton=1),
  schema_version INTEGER NOT NULL,
  ledger_instance_id TEXT NOT NULL UNIQUE,
  created_at_s INTEGER NOT NULL
);
CREATE TABLE videos (
  video_id TEXT PRIMARY KEY,
  canonical_url TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN
    ('reserved','uploading','reconcile_required','uploaded','committed','released')),
  role TEXT NOT NULL CHECK(role IN ('source','reference')),
  topic TEXT NOT NULL,
  run_id TEXT NOT NULL,
  reservation_id TEXT NOT NULL,
  fence INTEGER NOT NULL CHECK(fence > 0),
  title TEXT NOT NULL DEFAULT '',
  parent_project_id INTEGER,
  asset_id INTEGER,
  sha256 TEXT,
  external_op_id TEXT,
  storage_key TEXT,
  remote_job_id TEXT,
  reason TEXT,
  created_at_s INTEGER NOT NULL,
  updated_at_s INTEGER NOT NULL
);
CREATE TABLE operations (
  op_id TEXT PRIMARY KEY,
  command TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  response_json TEXT NOT NULL,
  exit_code INTEGER NOT NULL,
  created_at_s INTEGER NOT NULL
);
CREATE TABLE events (
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
);
"""


class NormalizerTests(unittest.TestCase):
    def test_accepted_forms_collapse_to_one_id(self):
        accepted = [
            VIDEO_ID,
            f"  {VIDEO_ID}\n",
            f"https://www.youtube.com/watch?v={VIDEO_ID}",
            f"http://youtube.com/watch/?feature=share&v={VIDEO_ID}#t=42",
            f"https://m.youtube.com/watch?v={VIDEO_ID}",
            f"https://music.youtube.com/watch?v={VIDEO_ID}&list=PL123",
            f"https://www.youtube.com/shorts/{VIDEO_ID}/?si=abc",
            f"https://youtu.be/{VIDEO_ID}?si=abc&list=PL123#t=20",
            f"https://youtube.com/live/{VIDEO_ID}?feature=share",
            f"https://www.youtube.com/embed/{VIDEO_ID}?start=42",
            f"https://www.youtube-nocookie.com/embed/{VIDEO_ID}",
        ]
        self.assertEqual({source_ledger.youtube_video_id(value) for value in accepted},
                         {VIDEO_ID})

    def test_ambiguous_or_non_video_forms_are_rejected(self):
        rejected = [
            "videoseries", "live_stream", "youtu.be/dQw4w9WgXcQ",
            f"ftp://youtube.com/watch?v={VIDEO_ID}",
            f"https://evil.youtube.com/watch?v={VIDEO_ID}",
            f"https://evil@youtube.com/watch?v={VIDEO_ID}",
            f"https://youtube.com:443/watch?v={VIDEO_ID}",
            f"https://youtube.com/v/{VIDEO_ID}",
            f"https://youtu.be/{VIDEO_ID}/extra",
            f"https://youtube.com/watch?v={VIDEO_ID}&v={VIDEO_ID}",
            f"https://youtube.com/watch?v=%64Qw4w9WgXcQ",
            f"https://youtube.com/shorts/{VIDEO_ID}?v={VIDEO_ID}",
            "https://youtube.com/playlist?list=PL123",
            "https://youtube.com/embed/videoseries?list=PL123",
        ]
        for value in rejected:
            with self.subTest(value=value), self.assertRaises(ValueError):
                source_ledger.youtube_video_id(value)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = Path(self.temp.name) / "ledger.sqlite3"
        result = self.run_cli("init")
        self.assertEqual(result.returncode, 0, result.stderr)

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--ledger", str(self.ledger), *args],
            text=True, capture_output=True, check=False)

    def reserve(self, run="run-a", reservation="res-a", op="op-reserve",
                role="source", video=VIDEO_ID, parent=None):
        command = [
            "reserve", video, "--role", role, "--topic", "test",
            "--run-id", run, "--reservation-id", reservation,
            "--op-id", op, "--title", "Source"]
        if parent is not None:
            command.extend(("--parent-project-id", str(parent)))
        return self.run_cli(*command)

    def begin_project(self, run="run-a", reservation="res-a", fence="1",
                      op="op-project-intent", external="create-project-a"):
        return self.run_cli(
            "begin-project", VIDEO_ID, "--run-id", run,
            "--reservation-id", reservation, "--fence", fence, "--op-id", op,
            "--external-op-id", external,
            "--project-title", f"Source [valmera-op:{external}]")

    def mark_project(self, run="run-a", reservation="res-a", fence="1",
                     op="op-project-result", external="create-project-a",
                     parent="123"):
        return self.run_cli(
            "mark-project", VIDEO_ID, "--run-id", run,
            "--reservation-id", reservation, "--fence", fence, "--op-id", op,
            "--external-op-id", external, "--parent-project-id", parent)

    def commit_source(self):
        self.assertEqual(self.reserve().returncode, 0)
        self.assertEqual(self.begin_project().returncode, 0)
        self.assertEqual(self.mark_project().returncode, 0)
        self.assertEqual(self.run_cli(
            "begin-upload", VIDEO_ID, "--run-id", "run-a",
            "--reservation-id", "res-a", "--fence", "1",
            "--op-id", "op-source-begin-upload", "--parent-project-id", "123",
            "--external-op-id", "upload-source-a").returncode, 0)
        self.assertEqual(self.run_cli(
            "mark-uploaded", VIDEO_ID, "--run-id", "run-a",
            "--reservation-id", "res-a", "--fence", "1",
            "--op-id", "op-source-uploaded", "--parent-project-id", "123",
            "--asset-id", "456", "--sha256", SHA256).returncode, 0)
        self.assertEqual(self.run_cli(
            "commit", VIDEO_ID, "--run-id", "run-a",
            "--reservation-id", "res-a", "--fence", "1",
            "--op-id", "op-source-commit", "--asset-id", "456",
            "--sha256", SHA256).returncode, 0)

    def test_idempotency_key_and_duplicate_gate(self):
        first = self.reserve()
        replay = self.reserve()
        duplicate = self.reserve(run="run-b", reservation="res-b", op="op-other")
        self.assertEqual(first.returncode, 0)
        self.assertEqual(replay.returncode, 0)
        self.assertEqual(first.stdout, replay.stdout)
        self.assertEqual(duplicate.returncode, 3)

        altered = self.run_cli(
            "reserve", VIDEO_ID, "--role", "source", "--topic", "changed",
            "--run-id", "run-a", "--reservation-id", "res-a",
            "--op-id", "op-reserve", "--title", "Source")
        self.assertEqual(altered.returncode, 7)

    def test_source_reserve_replays_pre_parent_binding_operation_hash(self):
        old_request = {
            "video_id": VIDEO_ID,
            "role": "source",
            "topic": "test",
            "run_id": "run-a",
            "reservation_id": "res-a",
            "title": "Source",
        }
        prior_response = json.dumps(
            {"ok": True, "status": "reserved", "legacy_replay": True},
            sort_keys=True, separators=(",", ":"))
        conn = sqlite3.connect(self.ledger)
        conn.execute(
            "INSERT INTO operations VALUES(?,?,?,?,?,?)",
            ("op-pre-binding-source", "reserve",
             source_ledger._request_hash("reserve", old_request),
             prior_response, 0, 100))
        conn.commit()
        conn.close()

        replay = self.reserve(op="op-pre-binding-source")
        self.assertEqual(replay.returncode, 0, replay.stderr)
        self.assertEqual(replay.stdout.strip(), prior_response)

    def test_release_reuse_increments_fence_and_fences_stale_owner(self):
        self.assertEqual(self.reserve().returncode, 0)
        released = self.run_cli(
            "release", VIDEO_ID, "--run-id", "run-a", "--reservation-id", "res-a",
            "--fence", "1", "--op-id", "op-release", "--reason", "pre-upload failure")
        self.assertEqual(released.returncode, 0)
        next_claim = self.reserve(run="run-b", reservation="res-b", op="op-next")
        self.assertEqual(next_claim.returncode, 0)
        self.assertEqual(json.loads(next_claim.stdout)["fence"], 2)

        stale = self.run_cli(
            "begin-project", VIDEO_ID, "--run-id", "run-a",
            "--reservation-id", "res-a", "--fence", "1", "--op-id", "op-stale",
            "--external-op-id", "create-project-stale",
            "--project-title", "Source [valmera-op:create-project-stale]")
        self.assertEqual(stale.returncode, 5)

    def test_source_requires_durable_project_intent_and_recorded_parent(self):
        self.assertEqual(self.reserve().returncode, 0)
        direct_upload = self.run_cli(
            "begin-upload", VIDEO_ID, "--run-id", "run-a",
            "--reservation-id", "res-a", "--fence", "1",
            "--op-id", "op-direct-upload", "--parent-project-id", "123",
            "--external-op-id", "upload-a")
        self.assertEqual(direct_upload.returncode, 5)

        intent = self.begin_project()
        replay = self.begin_project()
        self.assertEqual(intent.returncode, 0)
        self.assertEqual(intent.stdout, replay.stdout)
        changed = self.begin_project(op="op-project-intent", external="changed")
        self.assertEqual(changed.returncode, 7)
        self.assertEqual(self.mark_project().returncode, 0)
        same_parent = self.mark_project(op="op-project-result-reconciled")
        self.assertEqual(same_parent.returncode, 0)
        self.assertTrue(json.loads(same_parent.stdout)["idempotent"])
        conflicting_parent = self.mark_project(
            op="op-conflicting-project-result", parent="124")
        self.assertEqual(conflicting_parent.returncode, 6)

        wrong_parent = self.run_cli(
            "begin-upload", VIDEO_ID, "--run-id", "run-a",
            "--reservation-id", "res-a", "--fence", "1",
            "--op-id", "op-wrong-parent", "--parent-project-id", "124",
            "--external-op-id", "upload-a")
        self.assertEqual(wrong_parent.returncode, 5)
        self.assertEqual(self.run_cli(
            "begin-upload", VIDEO_ID, "--run-id", "run-a",
            "--reservation-id", "res-a", "--fence", "1",
            "--op-id", "op-begin", "--parent-project-id", "123",
            "--external-op-id", "upload-a").returncode, 0)

    def test_lost_create_response_reconciles_before_upload(self):
        self.assertEqual(self.reserve().returncode, 0)
        self.assertEqual(self.begin_project().returncode, 0)
        ambiguous = self.run_cli(
            "mark-ambiguous", VIDEO_ID, "--run-id", "run-a",
            "--reservation-id", "res-a", "--fence", "1",
            "--op-id", "op-project-ambiguous",
            "--reason", "create_project response lost")
        self.assertEqual(ambiguous.returncode, 0)
        record = json.loads(ambiguous.stdout)["record"]
        self.assertEqual(record["state"], "reconcile_required")
        self.assertEqual(record["reconcile_stage"], "project")
        cannot_skip_project = self.run_cli(
            "mark-uploaded", VIDEO_ID, "--run-id", "run-a",
            "--reservation-id", "res-a", "--fence", "1",
            "--op-id", "op-skip-project", "--parent-project-id", "123",
            "--asset-id", "456", "--sha256", SHA256)
        self.assertEqual(cannot_skip_project.returncode, 5)

        no_force = self.run_cli(
            "release", VIDEO_ID, "--run-id", "run-a",
            "--reservation-id", "res-a", "--fence", "1",
            "--op-id", "op-unsafe-release", "--reason", "assume no project")
        self.assertEqual(no_force.returncode, 6)
        no_proof = self.run_cli(
            "release", VIDEO_ID, "--run-id", "run-a",
            "--reservation-id", "res-a", "--fence", "1",
            "--op-id", "op-unproved-release", "--reason", "assume no project",
            "--force")
        self.assertEqual(no_proof.returncode, 2)

        reconciled = self.mark_project(op="op-reconciled-project")
        self.assertEqual(reconciled.returncode, 0, reconciled.stderr)
        self.assertEqual(json.loads(reconciled.stdout)["record"]["state"],
                         "parent_created")
        self.assertEqual(self.run_cli(
            "begin-upload", VIDEO_ID, "--run-id", "run-a",
            "--reservation-id", "res-a", "--fence", "1",
            "--op-id", "op-upload-after-reconcile", "--parent-project-id", "123",
            "--external-op-id", "upload-a").returncode, 0)

    def test_authoritative_no_project_release_still_fences_stale_result(self):
        self.assertEqual(self.reserve().returncode, 0)
        self.assertEqual(self.begin_project().returncode, 0)
        self.assertEqual(self.run_cli(
            "mark-ambiguous", VIDEO_ID, "--run-id", "run-a",
            "--reservation-id", "res-a", "--fence", "1",
            "--op-id", "op-ambiguous", "--reason", "response lost").returncode, 0)
        released = self.run_cli(
            "release", VIDEO_ID, "--run-id", "run-a",
            "--reservation-id", "res-a", "--fence", "1",
            "--op-id", "op-proved-release", "--reason", "no matching project",
            "--force", "--authoritative-no-side-effect-proof",
            "list_projects snapshot 2026-08-18T12:00:00Z: marker absent")
        self.assertEqual(released.returncode, 0, released.stderr)
        next_claim = self.reserve(run="run-b", reservation="res-b", op="op-next")
        self.assertEqual(next_claim.returncode, 0)
        self.assertEqual(json.loads(next_claim.stdout)["fence"], 2)
        self.assertEqual(self.begin_project(
            run="run-b", reservation="res-b", fence="2", op="op-next-intent",
            external="create-project-b").returncode, 0)
        stale_result = self.mark_project(op="op-stale-result")
        self.assertEqual(stale_result.returncode, 5)

    def test_reference_requires_same_run_committed_source_parent(self):
        before_source = self.reserve(
            role="reference", video=REFERENCE_ID, reservation="res-ref",
            op="op-ref-before-source", parent=123)
        self.assertEqual(before_source.returncode, 5)
        self.assertEqual(json.loads(before_source.stdout)["status"],
                         "reference_source_not_committed")

        self.commit_source()

        wrong_parent = self.reserve(
            role="reference", video=REFERENCE_ID, reservation="res-ref",
            op="op-ref-wrong-parent", parent=999)
        self.assertEqual(wrong_parent.returncode, 6)
        self.assertEqual(json.loads(wrong_parent.stdout)["status"],
                         "reference_parent_mismatch")

        wrong_run = self.reserve(
            run="run-b", role="reference", video=OTHER_REFERENCE_ID,
            reservation="res-ref-b", op="op-ref-wrong-run", parent=123)
        self.assertEqual(wrong_run.returncode, 5)
        self.assertEqual(json.loads(wrong_run.stdout)["status"],
                         "reference_source_not_committed")

        reserved = self.reserve(
            role="reference", video=REFERENCE_ID, reservation="res-ref",
            op="op-ref-reserve", parent=123)
        self.assertEqual(reserved.returncode, 0, reserved.stderr)
        self.assertEqual(json.loads(reserved.stdout)["record"]["parent_project_id"],
                         123)

        cannot_create = self.run_cli(
            "begin-project", REFERENCE_ID, "--run-id", "run-a",
            "--reservation-id", "res-ref", "--fence", "1",
            "--op-id", "op-reference-create", "--external-op-id", "create-ref",
            "--project-title", "Reference [valmera-op:create-ref]")
        self.assertEqual(cannot_create.returncode, 5)

        mismatched_upload = self.run_cli(
            "begin-upload", REFERENCE_ID, "--run-id", "run-a",
            "--reservation-id", "res-ref", "--fence", "1",
            "--op-id", "op-ref-upload-wrong-parent", "--parent-project-id", "999",
            "--external-op-id", "reference-upload-a")
        self.assertEqual(mismatched_upload.returncode, 6)
        self.assertEqual(json.loads(mismatched_upload.stdout)["status"],
                         "reference_parent_mismatch")

        correct_upload = self.run_cli(
            "begin-upload", REFERENCE_ID, "--run-id", "run-a",
            "--reservation-id", "res-ref", "--fence", "1",
            "--op-id", "op-ref-upload", "--parent-project-id", "123",
            "--external-op-id", "reference-upload-a")
        self.assertEqual(correct_upload.returncode, 0, correct_upload.stderr)
        uploaded = self.run_cli(
            "mark-uploaded", REFERENCE_ID, "--run-id", "run-a",
            "--reservation-id", "res-ref", "--fence", "1",
            "--op-id", "op-ref-uploaded", "--parent-project-id", "123",
            "--asset-id", "457", "--sha256", SHA256)
        self.assertEqual(uploaded.returncode, 0, uploaded.stderr)
        committed = self.run_cli(
            "commit", REFERENCE_ID, "--run-id", "run-a",
            "--reservation-id", "res-ref", "--fence", "1",
            "--op-id", "op-ref-commit", "--asset-id", "457",
            "--sha256", SHA256)
        self.assertEqual(committed.returncode, 0, committed.stderr)

    def test_reference_rejected_while_source_is_only_uploaded(self):
        self.assertEqual(self.reserve().returncode, 0)
        self.assertEqual(self.begin_project().returncode, 0)
        self.assertEqual(self.mark_project().returncode, 0)
        self.assertEqual(self.run_cli(
            "begin-upload", VIDEO_ID, "--run-id", "run-a",
            "--reservation-id", "res-a", "--fence", "1",
            "--op-id", "op-source-begin-upload", "--parent-project-id", "123",
            "--external-op-id", "upload-source-a").returncode, 0)
        self.assertEqual(self.run_cli(
            "mark-uploaded", VIDEO_ID, "--run-id", "run-a",
            "--reservation-id", "res-a", "--fence", "1",
            "--op-id", "op-source-uploaded", "--parent-project-id", "123",
            "--asset-id", "456", "--sha256", SHA256).returncode, 0)

        reference = self.reserve(
            role="reference", video=REFERENCE_ID, reservation="res-ref",
            op="op-ref-before-commit", parent=123)
        self.assertEqual(reference.returncode, 5)
        self.assertEqual(json.loads(reference.stdout)["status"],
                         "reference_source_not_committed")

    def test_upload_lost_response_reconciles_only_as_upload(self):
        self.assertEqual(self.reserve().returncode, 0)
        self.assertEqual(self.begin_project().returncode, 0)
        self.assertEqual(self.mark_project().returncode, 0)
        self.assertEqual(self.run_cli(
            "begin-upload", VIDEO_ID, "--run-id", "run-a",
            "--reservation-id", "res-a", "--fence", "1", "--op-id", "op-begin",
            "--parent-project-id", "123", "--external-op-id", "upload-a"
        ).returncode, 0)
        ambiguous = self.run_cli(
            "mark-ambiguous", VIDEO_ID, "--run-id", "run-a",
            "--reservation-id", "res-a", "--fence", "1",
            "--op-id", "op-upload-ambiguous", "--reason", "upload response lost")
        self.assertEqual(ambiguous.returncode, 0)
        self.assertEqual(json.loads(ambiguous.stdout)["record"]["reconcile_stage"],
                         "upload")
        uploaded = self.run_cli(
            "mark-uploaded", VIDEO_ID, "--run-id", "run-a",
            "--reservation-id", "res-a", "--fence", "1",
            "--op-id", "op-upload-reconciled", "--parent-project-id", "123",
            "--asset-id", "456", "--sha256", SHA256)
        self.assertEqual(uploaded.returncode, 0)

    def test_uploaded_commit_is_permanent(self):
        self.assertEqual(self.reserve().returncode, 0)
        self.assertEqual(self.begin_project().returncode, 0)
        self.assertEqual(self.mark_project().returncode, 0)
        self.assertEqual(self.run_cli(
            "begin-upload", VIDEO_ID, "--run-id", "run-a",
            "--reservation-id", "res-a", "--fence", "1", "--op-id", "op-begin",
            "--parent-project-id", "123", "--external-op-id", "upload-a").returncode, 0)
        self.assertEqual(self.run_cli(
            "mark-uploaded", VIDEO_ID, "--run-id", "run-a",
            "--reservation-id", "res-a", "--fence", "1", "--op-id", "op-uploaded",
            "--parent-project-id", "123", "--asset-id", "456", "--sha256", SHA256,
            "--storage-key", "originals/123/source.mp4", "--remote-job-id", "99").returncode, 0)
        self.assertEqual(self.run_cli(
            "commit", VIDEO_ID, "--run-id", "run-a", "--reservation-id", "res-a",
            "--fence", "1", "--op-id", "op-commit", "--asset-id", "456",
            "--sha256", SHA256).returncode, 0)
        self.assertEqual(self.reserve(run="run-b", reservation="res-b", op="op-new").returncode, 3)
        self.assertEqual(self.run_cli(
            "release", VIDEO_ID, "--run-id", "run-a", "--reservation-id", "res-a",
            "--fence", "1", "--op-id", "op-bad-release", "--reason", "no",
            "--force", "--authoritative-no-side-effect-proof",
            "even asserted proof cannot release committed media").returncode, 6)

    def test_concurrent_reservation_has_one_winner(self):
        commands = []
        for index in range(12):
            commands.append(subprocess.Popen(
                [sys.executable, str(SCRIPT), "--ledger", str(self.ledger),
                 "reserve", "9bZkp7q19f0", "--role", "source",
                 "--topic", "race", "--run-id", f"run-{index}",
                 "--reservation-id", f"res-{index}", "--op-id", f"op-{index}"],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE))
        results = [process.communicate() + (process.returncode,) for process in commands]
        self.assertEqual(sum(code == 0 for _, _, code in results), 1, results)
        self.assertEqual(sum(code == 3 for _, _, code in results), 11, results)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = Path(self.temp.name) / "ledger.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--ledger", str(self.ledger), *args],
            text=True, capture_output=True, check=False)

    def make_v1(self):
        conn = sqlite3.connect(self.ledger)
        conn.executescript(V1_SCHEMA)
        conn.execute("INSERT INTO ledger_meta VALUES(1,1,'ledger-v1-instance',100)")
        conn.execute(f"PRAGMA application_id={source_ledger.APPLICATION_ID}")
        conn.execute("PRAGMA user_version=1")
        conn.execute(
            """INSERT INTO videos VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (VIDEO_ID, source_ledger.canonical_url(VIDEO_ID), "committed", "source",
             "test", "run-a", "res-a", 1, "Source", 123, 456, SHA256,
             "upload-source-a", "originals/123/source.mp4", "99", None, 100, 110))
        conn.execute(
            """INSERT INTO videos VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("9bZkp7q19f0", source_ledger.canonical_url("9bZkp7q19f0"),
             "reconcile_required", "reference", "test", "run-a", "res-ref", 1,
             "Reference", 123, None, None, "upload-reference-a", None, None,
             "response lost", 101, 111))
        conn.execute(
            "INSERT INTO operations VALUES(?,?,?,?,?,?)",
            ("op-old", "reserve", "abc", '{"ok":true}', 0, 100))
        conn.execute(
            "INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?)",
            (1, VIDEO_ID, "commit", "uploaded", "committed", "res-a", 1,
             "run-a", 110, '{"asset_id":456}'))
        conn.commit()
        conn.close()

    def test_recognized_v1_migrates_with_verified_backup_and_preserves_rows(self):
        self.make_v1()
        migrated = self.run_cli("init")
        self.assertEqual(migrated.returncode, 0, migrated.stderr)
        payload = json.loads(migrated.stdout)
        self.assertEqual(payload["status"], "migrated_v1_to_v2")
        self.assertEqual(payload["schema_version"], 2)
        backup = Path(payload["backup"])
        self.assertTrue(backup.is_file())

        backup_conn = sqlite3.connect(backup)
        self.assertEqual(backup_conn.execute("PRAGMA user_version").fetchone()[0], 1)
        self.assertEqual(backup_conn.execute(
            "SELECT external_op_id FROM videos WHERE video_id=?", (VIDEO_ID,)
        ).fetchone()[0], "upload-source-a")
        self.assertEqual(backup_conn.execute(
            "SELECT COUNT(*) FROM operations").fetchone()[0], 1)
        backup_conn.close()

        conn = sqlite3.connect(self.ledger)
        conn.row_factory = sqlite3.Row
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 2)
        self.assertEqual(conn.execute(
            "SELECT schema_version FROM ledger_meta").fetchone()[0], 2)
        source = conn.execute(
            "SELECT * FROM videos WHERE video_id=?", (VIDEO_ID,)).fetchone()
        reference = conn.execute(
            "SELECT * FROM videos WHERE video_id='9bZkp7q19f0'").fetchone()
        self.assertEqual(source["state"], "committed")
        self.assertEqual(source["upload_external_op_id"], "upload-source-a")
        self.assertIsNone(source["project_external_op_id"])
        self.assertEqual(reference["state"], "reconcile_required")
        self.assertEqual(reference["reconcile_stage"], "upload")
        self.assertEqual(reference["upload_external_op_id"], "upload-reference-a")
        self.assertEqual(conn.execute(
            "SELECT response_json FROM operations WHERE op_id='op-old'"
        ).fetchone()[0], '{"ok":true}')
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
        conn.close()

        existing = self.run_cli("init")
        self.assertEqual(existing.returncode, 0, existing.stderr)
        self.assertEqual(json.loads(existing.stdout)["status"], "existing")

    def test_migrated_unbound_reference_binds_only_to_committed_source_parent(self):
        self.make_v1()
        conn = sqlite3.connect(self.ledger)
        conn.execute(
            """INSERT INTO videos VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (OTHER_REFERENCE_ID,
             source_ledger.canonical_url(OTHER_REFERENCE_ID),
             "reserved", "reference", "test", "run-a", "res-unbound", 1,
             "Legacy reference", None, None, None, None, None, None, None,
             102, 112))
        conn.commit()
        conn.close()

        migrated = self.run_cli("init")
        self.assertEqual(migrated.returncode, 0, migrated.stderr)
        begun = self.run_cli(
            "begin-upload", OTHER_REFERENCE_ID, "--run-id", "run-a",
            "--reservation-id", "res-unbound", "--fence", "1",
            "--op-id", "op-bind-legacy-reference",
            "--parent-project-id", "123",
            "--external-op-id", "upload-legacy-reference")
        self.assertEqual(begun.returncode, 0, begun.stderr)
        record = json.loads(begun.stdout)["record"]
        self.assertEqual(record["state"], "uploading")
        self.assertEqual(record["parent_project_id"], 123)

    def test_migrated_reference_cannot_continue_on_another_parent(self):
        self.make_v1()
        conn = sqlite3.connect(self.ledger)
        conn.execute(
            "UPDATE videos SET parent_project_id=999 WHERE video_id=?",
            (REFERENCE_ID,))
        conn.commit()
        conn.close()

        migrated = self.run_cli("init")
        self.assertEqual(migrated.returncode, 0, migrated.stderr)
        uploaded = self.run_cli(
            "mark-uploaded", REFERENCE_ID, "--run-id", "run-a",
            "--reservation-id", "res-ref", "--fence", "1",
            "--op-id", "op-invalid-legacy-reference-result",
            "--parent-project-id", "999", "--asset-id", "777",
            "--sha256", SHA256)
        self.assertEqual(uploaded.returncode, 6)
        self.assertEqual(json.loads(uploaded.stdout)["status"],
                         "reference_parent_mismatch")

    def test_unknown_schema_is_refused_without_migration_or_backup(self):
        conn = sqlite3.connect(self.ledger)
        conn.execute(f"PRAGMA application_id={source_ledger.APPLICATION_ID}")
        conn.execute("PRAGMA user_version=99")
        conn.commit()
        conn.close()
        refused = self.run_cli("init")
        self.assertEqual(refused.returncode, 2)
        conn = sqlite3.connect(self.ledger)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 99)
        conn.close()
        self.assertEqual(list(Path(self.temp.name).glob("*.v1-backup-*.sqlite3")), [])

    def test_failed_v1_migration_rolls_back_and_keeps_verified_backup(self):
        self.make_v1()
        conn = sqlite3.connect(self.ledger)
        conn.execute(
            """INSERT INTO videos VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("M7lc1UVf-VE", source_ledger.canonical_url("M7lc1UVf-VE"),
             "committed", "source", "test", "run-b", "res-b", 1,
             "Conflicting source", 123, 789, SHA256, "upload-source-b",
             "originals/123/other.mp4", "100", None, 102, 112))
        conn.commit()
        conn.close()

        failed = self.run_cli("init")
        self.assertEqual(failed.returncode, 2)
        conn = sqlite3.connect(self.ledger)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0], 3)
        self.assertEqual(
            tuple(row[1] for row in conn.execute("PRAGMA table_info(videos)")),
            source_ledger.V1_COLUMNS["videos"])
        conn.close()
        backups = list(Path(self.temp.name).glob("*.v1-backup-*.sqlite3"))
        self.assertEqual(len(backups), 1)
        backup_conn = sqlite3.connect(backups[0])
        self.assertEqual(backup_conn.execute("PRAGMA user_version").fetchone()[0], 1)
        self.assertEqual(backup_conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0], 3)
        backup_conn.close()


if __name__ == "__main__":
    unittest.main()
