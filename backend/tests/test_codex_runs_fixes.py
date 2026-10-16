"""Backend replies the owner's Codex podcast runs (13-16 Oct 2026) tripped on.

Triage: .valmera/engines/codex_runs_defects.md in the owner's workspace.
"""
import contextlib
import copy
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes import mcp as mcpmod  # noqa: E402
from routes import video  # noqa: E402
from video_services import direct_edits  # noqa: E402


class _Cur:
    """Answers by SQL fragment; records every statement."""

    def __init__(self, answers):
        self.answers, self.sql, self.rows = answers, [], []

    def cursor(self):
        return self

    def execute(self, sql, args=()):
        q = " ".join(sql.split())
        self.sql.append((q, args))
        self.rows = []
        for key, rows in self.answers.items():
            if key in q:
                self.rows = copy.deepcopy(rows)
                break

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


def _batch():
    return dict(base_version=1, operation_id="edit-batch-0123456789", operations=[
        dict(action="set", layer="keep", value=[[1, 6]])])


def test_a_read_only_job_never_holds_the_edit_and_a_holder_is_named(monkeypatch):
    """klRb0 s16/s18/s22 (3725, 3716, 3728, 15 Oct): apply_edit_batch was
    refused 'Another editor is working on this project' while the only job on
    the project was the agent's own render_preview (mutation=false); on
    Zuckerberg s03 (3614) a look_at whose heartbeat had died held it."""
    cur = _Cur({"FROM video_jobs WHERE project_id": [{"id": 70319}]})

    @contextlib.contextmanager
    def connect():
        yield cur
    monkeypatch.setattr(video, "vdb", connect)
    monkeypatch.setattr(video, "_project_for_user", lambda *_: dict(chat_session_id=12))
    monkeypatch.setattr(video, "read_receipt", lambda *a: None)
    monkeypatch.setattr(video, "_subscribe_gate_applies", lambda *_: False)
    monkeypatch.setattr(video, "_latest_edl", lambda *_: dict(
        version=1, json=video.wschemas.default_edl(20)))
    result, status = video.apply_edit_batch_core(1, 2, _batch())
    assert status == 409 and result["blocking_job_id"] == 70319
    assert "job 70319" in result["error"] and "wait_for_job(job_id=70319)" in result["error"]
    lock_sql = next(q for q, _a in cur.sql if "FROM video_jobs WHERE project_id" in q)
    assert "payload->>'mutation'" in lock_sql and "heartbeat_at" in lock_sql
    assert next(a for q, a in cur.sql if q is lock_sql)[1] == video._EDITOR_LOCK_STALE_S


@pytest.mark.parametrize("state,expect", [
    ("running", "is job 69124 (running, 40%) and its MP4 is not ready yet"),
    ("queued", "wait_for_job(job_id=69124)"),
    ("failed", "failed: Operator ended stalled export"),
])
def test_download_url_names_the_export_it_is_waiting_for(monkeypatch, state, expect):
    """Zuckerberg s22 (3633 v13, 14 Oct), PG (3591 v10), Jensen (3744 v14,
    3757 v11): 'No final has been rendered yet — call export_final' while
    the export job for that very version was running."""
    cur = _Cur({"type = 'final'": [{
        "id": 69124, "state": state, "progress": 40, "v": "13",
        "error": "Operator ended stalled export after 30 minutes"}]})

    @contextlib.contextmanager
    def connect():
        yield cur
    monkeypatch.setattr(mcpmod, "vdb", connect)
    monkeypatch.setattr(mcpmod, "_project_for_user", lambda *_: {"id": 3633, "title": "t"})
    result = mcpmod._t_download_url({"user_id": 1}, {"project_id": 3633, "kind": "final",
                                                     "edl_version": 13})
    assert isinstance(result, mcpmod._SessionToolError)
    text = str(result)
    assert expect in text
    if state != "failed":
        assert "Do not submit another export" in text
    final_sql = next(a for q, a in cur.sql if "type = 'final'" in q)
    assert final_sql == [3633, "13"]


def test_download_url_without_any_export_still_says_export_final(monkeypatch):
    cur = _Cur({})

    @contextlib.contextmanager
    def connect():
        yield cur
    monkeypatch.setattr(mcpmod, "vdb", connect)
    monkeypatch.setattr(mcpmod, "_project_for_user", lambda *_: {"id": 3, "title": "t"})
    result = mcpmod._t_download_url({"user_id": 1}, {"project_id": 3, "kind": "final"})
    assert "No final has been rendered yet" in str(result)


def test_an_operation_id_rejection_states_the_rule():
    """klRb0 s06 (3709): 's06-serif-label' (15 characters) was refused as
    'Invalid edit operation ID.' with no rule."""
    with pytest.raises(ValueError) as err:
        direct_edits.command_identity({"operation_id": "s06-serif-label"})
    assert "16-80" in str(err.value) and "15 characters" in str(err.value)


def test_too_many_jobs_names_the_jobs_holding_the_slots():
    """Jensen coordinator (3811, 16 Oct): upload_finish 'Too many jobs
    running. Wait for one to finish.' twice; it asked for list_jobs and
    cancel_job tools that do not exist."""
    cur = _Cur({"FROM video_jobs WHERE user_id": [
        {"id": 71300, "type": "mcp_tool", "state": "running", "project_id": 3755,
         "tool": "render_preview"},
        {"id": 71301, "type": "mcp_tool", "state": "queued", "project_id": 3762,
         "tool": "look_at"}]})
    text, first = video._busy_jobs_text(cur, 7)
    assert first == 71300
    assert "job 71300 render_preview on project 3755, running" in text
    assert "job 71301 look_at on project 3762, queued" in text
    assert video._busy_jobs_text(_Cur({}), 7) == ("", None)
