"""The backend's MCP fast reads reply exactly as the queued call would.

worker/tests/test_fast_reads_parity.py proves the fast path's stored result
equals the queued executor's stored result, byte for byte, on one fixture
database. This file proves the other half: routes/mcp.py turns a fast result
into the same tools/call reply as the queued job row carrying that result,
never enqueues a job it answered, keeps auth/project scoping and the
analysis gate in front of it, and queues exactly as before whenever the fast
path declines.
"""

import json

import pytest

import routes.mcp as mcpmod
from tests.test_mcp import (CATALOG, DB, STATIC_TOKEN, rpc,  # noqa: F401
                            client, stub_db)
from video_services import mcp_fast_reads


# A stored (JSONB-ordered) read result as both paths produce it.
STORED = mcp_fast_reads.stored_form({
    "text": "0.40-0.58 so [S0]\n1.48-1.69 the [S0] [confidence=0.630, REVIEW]",
    "edl_version": 6, "edl_changed": False, "metered_contract": True,
    "is_error": False,
    "tool_outcome": {"status": "success", "message": "0.40-0.58 so",
                     "retryable": False, "idempotent": True,
                     "state_changed": False, "affected_ranges": [],
                     "evidence": {}, "safe_fallback": None,
                     "prerequisite_tool": None,
                     "corrected_argument_guidance": None},
    "visual_evidence": {"available_before": 0, "created_this_call": 0,
                        "publish_attempts": 0, "published_this_call": 0,
                        "remaining": 0},
    "preview": {"render_type": "complete_preview", "render_job_id": 900,
                "quality": "draft", "sha256": "f" * 64, "edl_version": 6,
                "duration_s": 126.75, "audio_model_review": False,
                "asset_id": 55},
})


def _call(client, name="get_transcript", **arguments):
    return rpc(client, "tools/call", STATIC_TOKEN, {
        "name": name, "arguments": {"project_id": 3, **arguments}})


@pytest.fixture
def ready(monkeypatch):
    monkeypatch.setattr(mcpmod, "_index_ready", lambda cur, pid: (True, None))


def test_a_fast_read_replies_exactly_like_its_queued_job(client, ready,
                                                         monkeypatch):
    # Queued: the fast path declines, the job row carries the result.
    monkeypatch.setattr(mcp_fast_reads, "answer", lambda *a, **k: None)
    DB["job_result"] = json.loads(json.dumps(STORED))
    queued = _call(client, start=0)
    assert len(DB["enqueued"]) == 1
    # Fast: the same stored result, answered in the backend.
    DB["enqueued"].clear()
    seen = []
    monkeypatch.setattr(mcp_fast_reads, "answer",
                        lambda conn, uid, project, pid, name, args, started=None:
                        seen.append((uid, pid, name, args)) or
                        json.loads(json.dumps(STORED)))
    fast = _call(client, start=0)
    assert DB["enqueued"] == []
    assert seen == [(60, 3, "get_transcript", {"start": 0})]
    assert fast.get_data() == queued.get_data()
    result = fast.get_json()["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["preview"]["render_job_id"] == 900
    assert result["structuredContent"]["visual_evidence"][
        "delivery_status"] == "not_requested"


def test_a_declined_fast_read_is_queued_exactly_as_before(client, ready,
                                                          monkeypatch):
    monkeypatch.setattr(mcp_fast_reads, "answer", lambda *a, **k: None)
    result = _call(client, start=5).get_json()["result"]
    assert "12 sentences." in result["content"][0]["text"]
    assert DB["enqueued"] and DB["enqueued"][0]["tool"] == "get_transcript"
    assert DB["enqueued"][0]["args"] == {"start": 5}
    assert DB["enqueued"][0]["mutation"] is False


def test_writes_and_media_tools_never_take_the_fast_path(client, ready,
                                                         monkeypatch):
    monkeypatch.setattr(mcp_fast_reads, "answer", lambda *a, **k: pytest.fail(
        "a write must be queued"))
    _call(client, name="make_shorts", clips=[])
    assert DB["enqueued"] and DB["enqueued"][0]["tool"] == "make_shorts"
    assert not mcp_fast_reads.eligible("look_at")
    assert not mcp_fast_reads.eligible("render_preview")


def test_another_accounts_project_never_reaches_the_fast_path(client, ready,
                                                              monkeypatch):
    monkeypatch.setattr(mcpmod, "_project_for_user", lambda cur, pid, uid: None)
    monkeypatch.setattr(mcp_fast_reads, "answer", lambda *a, **k: pytest.fail(
        "project scoping comes first"))
    result = _call(client).get_json()["result"]
    assert result["isError"] is True
    assert "does not exist on this account" in result["content"][0]["text"]


def test_an_unanalyzed_project_keeps_the_analysis_gate(client, monkeypatch):
    monkeypatch.setattr(mcpmod, "_index_ready", lambda cur, pid: (
        False, {"id": 44, "state": "failed", "progress": 0, "active": False}))
    monkeypatch.setattr(mcp_fast_reads, "answer", lambda *a, **k: pytest.fail(
        "the analysis gate comes first"))
    result = _call(client).get_json()["result"]
    assert result["isError"] is True
    assert "No editing job was started" in result["content"][0]["text"]


def test_the_fast_path_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(mcp_fast_reads, "ENABLED", False)
    assert not mcp_fast_reads.eligible("get_words")
    assert mcp_fast_reads.answer(None, 60, {}, 3, "get_words", {}) is None


def test_a_broken_worker_checkout_disables_the_fast_path_not_the_call(
        monkeypatch):
    monkeypatch.setattr(mcp_fast_reads, "_load_failed", [])
    monkeypatch.setattr(mcp_fast_reads, "read_tools", lambda: (_ for _ in ()).throw(
        ImportError("worker/read_tools.py missing")))
    assert mcp_fast_reads.eligible("get_words") is False
    assert mcp_fast_reads._load_failed


def test_a_failing_fast_read_rolls_back_to_its_savepoint(monkeypatch):
    statements = []

    class Cur:
        def execute(self, sql, params=()):
            statements.append(" ".join(sql.split()))

    class Conn:
        def cursor(self):
            return Cur()

    monkeypatch.setattr(mcp_fast_reads, "_answer",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("canceling statement due to timeout")))
    assert mcp_fast_reads.answer(Conn(), 60, {"id": 3}, 3, "get_words",
                                 {}) is None
    assert statements == ["SAVEPOINT mcp_fast_read",
                          "SET LOCAL statement_timeout = %s",
                          "ROLLBACK TO SAVEPOINT mcp_fast_read",
                          "RELEASE SAVEPOINT mcp_fast_read"]


def test_a_busy_process_queues_instead_of_waiting(monkeypatch):
    class Busy:
        def acquire(self, timeout=None):
            return False

        def release(self):
            raise AssertionError("never acquired")

    monkeypatch.setattr(mcp_fast_reads, "_running", Busy())
    assert mcp_fast_reads.answer(object(), 60, {"id": 3}, 3, "get_words",
                                 {}) is None


def test_the_backend_loads_the_shared_worker_code_without_heavy_modules():
    import os
    import subprocess
    import sys
    tools = mcp_fast_reads.read_tools()
    assert tools.__name__ == "worker_read_tools"
    assert set(tools.FAST_READ_TOOLS) >= {"get_words", "get_transcript",
                                          "get_kept_transcript", "get_edl"}
    # A fresh backend process: the fast path adds no media/model stack.
    probe = subprocess.run([sys.executable, "-c", (
        "import sys, routes.mcp\n"
        "from video_services import mcp_fast_reads as f\n"
        "assert f.eligible('get_words')\n"
        "print(sorted(m for m in ('cv2', 'numpy', 'PIL', 'torch', "
        "'playwright', 'openai') if m in sys.modules))")],
        capture_output=True, text=True, timeout=120,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        env=dict(os.environ, SKIP_DB_INIT="1",
                 DATABASE_URL="postgresql://stub/stub"))
    assert probe.returncode == 0, probe.stderr[-2000:]
    assert probe.stdout.strip().splitlines()[-1] == "[]"
