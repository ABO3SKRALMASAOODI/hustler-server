"""Operator recovery is idempotent and respects newer customer work."""
import inspect
from contextlib import contextmanager
from types import SimpleNamespace as NS

from flask import Flask
import pytest
from routes import admin_video


def invoke(monkeypatch, body, *, message=None, latest=1, active=None, existing=None,
           failed=None):
    queries, enqueued = [], []
    class Cursor:
        def execute(self, sql, params=()):
            queries.append((sql, params))
            self.sql = sql
        def fetchone(self):
            sql = self.sql
            if "FROM projects" in sql:
                return {"id": 9, "user_id": 8, "chat_session_id": 7}
            if "operator_request_key" in sql:
                return existing
            if "state IN" in sql:
                return active
            if "FROM edls" in sql:
                return {"version": latest}
            if "FROM chat_messages" in sql:
                return message
            if "SELECT payload, result, state" in sql:
                return failed
            raise AssertionError(sql)
    cursor = Cursor()
    @contextmanager
    def connection():
        yield NS(cursor=lambda: cursor)
    monkeypatch.setattr(admin_video, "adb", connection)
    monkeypatch.setattr(admin_video, "_enqueue", lambda *args: enqueued.append(args[-1]) or 123)
    app = Flask(__name__)
    with app.test_request_context(json=body):
        response = inspect.unwrap(admin_video.repair_project_edit)(9)
        result, status = response if isinstance(response, tuple) else (response, 200)
        return result.get_json(), status, enqueued, queries


def test_failed_automatic_upload_can_resume_without_inventing_a_customer_message(monkeypatch):
    result, status, queued, queries = invoke(monkeypatch,
        {"failed_job_id": 12, "expected_edl_version": 1, "idempotency_key": "incident-12"},
        failed={"state": "done", "payload": {"first_cut": True}, "result": {"outcome": "blocked"}})
    assert status == 200 and result["job_id"] == 123
    assert queued[0]["first_cut"] is True and queued[0]["operator_repair"] is True
    assert "message_id" not in queued[0]
    assert queued[0]["operator_expected_message_id"] == 0
    assert "FOR UPDATE" in queries[0][0]


@pytest.mark.parametrize("changes", [
    {"latest": 2},
    {"message": {"id": 20, "meta": {"edl_version": 1}}},
    {"active": {"id": 30}},
])
def test_automatic_recovery_never_overwrites_changed_or_active_projects(monkeypatch, changes):
    _, status, queued, _ = invoke(monkeypatch,
        {"failed_job_id": 12, "expected_edl_version": 1}, **changes)
    assert status == 409 and not queued


def test_duplicate_operator_request_returns_its_existing_job(monkeypatch):
    result, status, queued, _ = invoke(monkeypatch,
        {"idempotency_key": "incident-12"}, existing={"id": 99})
    assert status == 200 and result["job_id"] == 99 and result["reused"]
    assert not queued


def test_repair_preserves_the_state_the_customer_actually_referred_to(monkeypatch):
    _, status, queued, _ = invoke(monkeypatch, {"expected_edl_version": 21},
        latest=21, message={"id": 40, "meta": {"edl_version": 17}})
    assert status == 200
    assert queued[0]["operator_preservation_version"] == 17
    assert queued[0]["operator_expected_edl_version"] == 21


def test_fulfilled_first_cut_is_not_restarted_as_failed_recovery(monkeypatch):
    _, status, queued, _ = invoke(monkeypatch, {"failed_job_id": 12, "expected_edl_version": 1},
        failed={"state": "done", "payload": {"first_cut": True}, "result": {"outcome": "fulfilled"}})
    assert status == 409 and not queued


def test_invalid_customer_baseline_cannot_queue_an_unrecoverable_job(monkeypatch):
    _, status, queued, _ = invoke(monkeypatch, {}, latest=21,
        message={"id": 40, "meta": {"edl_version": "missing"}})
    assert status == 409 and not queued
