"""MCP server side of the parked render_preview (cost review, 25 Oct 2026).

The worker parks a render_preview whose render is still going (it used to
poll for minutes inside the 4 GiB MCP container); this server waits on the
render job in the web process and requeues the same row to adopt it. The
caller's contract does not change: one synchronous render_preview, or
STILL RUNNING naming THIS row for wait_for_job. Pinned here:

  1. render_preview asks the worker to park; report=true and other tools
     do not, and MCP_RENDER_PARK=0 turns it off;
  2. a parked row is followed to the resumed run's answer, within one
     budget, and requeued exactly once;
  3. a render still running when the budget ends answers as the parked row
     still running, with the render's progress;
  4. rows that never park are returned exactly as _wait returns them;
  5. the requeue is atomic, hands the attempt back and arms adoption.
"""

import contextlib
import time

import routes.mcp as mcpmod


def _parked_row(render_id=77):
    return {"id": 5, "type": "mcp_tool", "state": "done", "progress": 100,
            "project_id": 3, "error": None,
            "payload": {"tool": "render_preview", "args": {"complete": True},
                        "render_park_s": 3},
            "result": {"text": "RENDER QUEUED: ...",
                       "parked_render": {"job_id": render_id,
                                         "type": "preview",
                                         "edl_version": 4}}}


def test_render_preview_asks_to_park_and_nothing_else_does(monkeypatch):
    monkeypatch.setattr(mcpmod, "RENDER_PARK", True)
    assert mcpmod._parks_render("render_preview", {"complete": True})
    assert mcpmod._parks_render("render_preview", {})
    assert not mcpmod._parks_render("render_preview", {"report": True})
    assert not mcpmod._parks_render("look_at", {})
    assert not mcpmod._parks_render("watch_video", {})
    monkeypatch.setattr(mcpmod, "RENDER_PARK", False)
    assert not mcpmod._parks_render("render_preview", {"complete": True})
    assert 0 < mcpmod.RENDER_PARK_S <= 30


def test_a_parked_row_answers_with_its_resumed_run(monkeypatch):
    answered = {"id": 5, "type": "mcp_tool", "state": "done",
                "result": {"text": "Preview v4 rendered: 31.2s"}}
    rows = {5: [_parked_row(), answered], 77: [{"id": 77, "type": "preview",
                                               "state": "done",
                                               "progress": 100}]}
    waits, resumed = [], []

    def fake_wait(job_id, user_id, seconds=None, slow=None):
        waits.append((job_id, slow))
        return rows[job_id].pop(0)

    monkeypatch.setattr(mcpmod, "_wait", fake_wait)
    monkeypatch.setattr(mcpmod, "_resume_parked",
                        lambda job_id, user_id, render: resumed.append(
                            (job_id, user_id, render)) or True)
    row = mcpmod._settle(5, 60, 100)
    assert row is answered
    assert resumed == [(5, 60, 77)]
    # the render is waited on in the slow share, here, not in a container
    assert waits == [(5, None), (77, True), (5, None)]


def test_a_render_still_running_answers_as_this_row_running(monkeypatch):
    rows = {5: _parked_row(), 77: {"id": 77, "type": "preview",
                                   "state": "running", "progress": 42}}
    monkeypatch.setattr(mcpmod, "_wait",
                        lambda job_id, *a, **k: rows[job_id])
    monkeypatch.setattr(mcpmod, "_resume_parked",
                        lambda *a: (_ for _ in ()).throw(
                            AssertionError("requeued while rendering")))
    row = mcpmod._settle(5, 60, 0.01)
    assert row["id"] == 5 and row["state"] == "running"
    assert row["progress"] == 42
    text = mcpmod._still_running(row, "render_preview")
    assert "wait_for_job(job_id=5)" in text and "42%" in text


def test_a_failed_render_still_resumes_so_the_worker_reports_it(monkeypatch):
    failed_answer = {"id": 5, "type": "mcp_tool", "state": "done",
                     "result": {"text": "CORRECTION_NEEDED: preview render "
                                        "FAILED ..."}}
    rows = {5: [_parked_row(), failed_answer],
            77: [{"id": 77, "type": "preview", "state": "failed"}]}
    monkeypatch.setattr(mcpmod, "_wait",
                        lambda job_id, *a, **k: rows[job_id].pop(0))
    resumed = []
    monkeypatch.setattr(mcpmod, "_resume_parked",
                        lambda *a: resumed.append(a) or True)
    assert mcpmod._settle(5, 60, 50) is failed_answer
    assert len(resumed) == 1


def test_rows_that_never_park_are_untouched(monkeypatch):
    plain = {"id": 9, "type": "mcp_tool", "state": "done",
             "result": {"text": "12 sentences."}}
    running = {"id": 9, "type": "mcp_tool", "state": "running"}
    render = {"id": 9, "type": "preview", "state": "done",
              "result": {"parked_render": {"job_id": 1}}}   # not mcp_tool
    for row in (plain, running, render, None):
        monkeypatch.setattr(mcpmod, "_wait", lambda *a, _r=row, **k: _r)
        monkeypatch.setattr(mcpmod, "_resume_parked",
                            lambda *a: (_ for _ in ()).throw(
                                AssertionError("not parked")))
        assert mcpmod._settle(9, 60) is row
    bad = dict(_parked_row(), result={"parked_render": {"job_id": True}})
    assert mcpmod._parked_render(bad) is None


class _Cur:
    def __init__(self, log, rowcount):
        self.log, self.rowcount = log, rowcount

    def execute(self, sql, params=()):
        self.log.append((" ".join(sql.split()), params))


def test_the_requeue_is_atomic_and_hands_the_attempt_back(monkeypatch):
    log = []

    class Conn:
        def __init__(self, rowcount):
            self.rowcount = rowcount

        def cursor(self):
            return _Cur(log, self.rowcount)

    for rowcount, expected in ((1, True), (0, False)):
        @contextlib.contextmanager
        def fake_vdb(_rc=rowcount):
            yield Conn(_rc)

        monkeypatch.setattr(mcpmod, "vdb", fake_vdb)
        assert mcpmod._resume_parked(5, 60, 77) is expected
    sql, params = log[0]
    assert sql.startswith("UPDATE video_jobs SET state = 'queued'")
    assert "attempts = GREATEST(attempts - 1, 0)" in sql
    assert "payload - 'render_park_s'" in sql
    assert "'adopt_render_job_id'" in sql
    # only the row that parked on exactly this render, still done
    assert "AND state = 'done'" in sql
    assert "result->'parked_render'->>'job_id' = %s" in sql
    assert "type = 'mcp_tool'" in sql and "user_id = %s" in sql
    assert params == (77, 5, 60, "77")


def test_the_enqueued_render_preview_carries_the_park_request(monkeypatch):
    enqueued = []

    class Cur:
        def execute(self, sql, params=()):
            pass

        def fetchone(self):
            return {"version": 4}

    class Conn:
        def cursor(self):
            return Cur()

    @contextlib.contextmanager
    def fake_vdb():
        yield Conn()

    monkeypatch.setattr(mcpmod, "vdb", fake_vdb)
    monkeypatch.setattr(mcpmod, "RENDER_PARK", True)
    monkeypatch.setattr(mcpmod, "_project_for_user",
                        lambda cur, pid, uid: {"id": pid, "title": "P"})
    monkeypatch.setattr(mcpmod, "_catalog", lambda: {"write_tools": []})
    monkeypatch.setattr(mcpmod, "_index_ready", lambda cur, pid: (True, {}))
    monkeypatch.setattr(mcpmod, "read_metadata", lambda *a: None)
    monkeypatch.setattr(mcpmod.mcp_fast_reads, "eligible", lambda name: False)
    monkeypatch.setattr(mcpmod, "_enqueue",
                        lambda cur, pid, uid, jtype, payload:
                        enqueued.append(payload) or 5)
    monkeypatch.setattr(mcpmod, "_settle", lambda *a, **k: {
        "id": 5, "type": "mcp_tool", "state": "done",
        "result": {"text": "Preview v4 rendered."}})
    for name, args in (("render_preview", {"complete": True}),
                       ("render_preview", {"report": True}),
                       ("look_at", {"times": [1]})):
        mcpmod._run_tool_job({"user_id": 60}, name, args, project_id=3)
    assert enqueued[0]["render_park_s"] == mcpmod.RENDER_PARK_S
    assert "render_park_s" not in enqueued[1]
    assert "render_park_s" not in enqueued[2]
    assert enqueued[0]["mutation"] is False
