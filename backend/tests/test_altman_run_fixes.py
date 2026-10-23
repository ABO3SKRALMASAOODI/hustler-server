"""Altman run (w1023-altman-anAmTGcewX8, 23 Oct 2026): 31 parallel MCP
editors. Render waits held every long-wait slot, so edits and audits got the
3 s short wait and answered STILL RUNNING; the job then wrote its version and
the editor's next apply_edit_batch was refused "The edit changed" with no
word of what had changed. Pinned:

  1. A slow (render) wait never takes the slots reserved for quick calls.
  2. wait_for_job on a render waits in the slow share; on an edit, quick.
  3. A STILL RUNNING edit says it will write the next version.
  4. The version-conflict refusal names the tool calls that wrote it.
"""

import threading
import time

import routes.mcp as mcpmod
import routes.video as video


def _job(state, **payload):
    return {"id": 9, "type": "mcp_tool", "state": state, "progress": 0,
            "error": None, "result": None, "payload": payload,
            "project_id": 1}


def test_render_waits_leave_the_reserved_slots_to_quick_calls(monkeypatch):
    monkeypatch.setattr(mcpmod, "_job_row", lambda *_a: _job("running"))
    monkeypatch.setattr(mcpmod, "SHORT_WAIT_S", 0.1)
    monkeypatch.setattr(mcpmod, "_long_waits", threading.BoundedSemaphore(3))
    monkeypatch.setattr(mcpmod, "_slow_waits", threading.BoundedSemaphore(1))
    # one render is waiting: it holds the slow share's only slot
    assert mcpmod._slow_waits.acquire(blocking=False)
    assert mcpmod._long_waits.acquire(blocking=False)
    try:
        started = time.monotonic()
        mcpmod._wait(9, 1, seconds=0.6, slow=True)
        assert time.monotonic() - started < 0.4      # a second render: short
        started = time.monotonic()
        mcpmod._wait(9, 1, seconds=0.6)
        assert time.monotonic() - started >= 0.5     # an edit: the long wait
    finally:
        mcpmod._long_waits.release()
        mcpmod._slow_waits.release()
    # every slot is returned
    for sem, n in ((mcpmod._long_waits, 3), (mcpmod._slow_waits, 1)):
        got = [sem.acquire(blocking=False) for _ in range(n)]
        assert all(got)
        for _ in range(n):
            sem.release()


def test_slow_calls_and_jobs_are_classified():
    assert mcpmod._slow_call("render_preview", {"complete": True})
    assert not mcpmod._slow_call("render_preview", {"report": True})
    assert not mcpmod._slow_call("add_motion_graphic", {})
    assert not mcpmod._slow_call("keep_segments", {})
    assert mcpmod._slow_job({"type": "preview"})
    assert mcpmod._slow_job({"type": "mcp_tool", "payload": {
        "tool": "render_preview", "args": {"quality": "approval"}}})
    assert not mcpmod._slow_job({"type": "mcp_tool", "payload": {
        "tool": "set_typeset", "args": {}}})


def test_a_still_running_edit_says_it_writes_the_next_version():
    text = mcpmod._still_running(
        _job("running", tool="add_motion_graphic", mutation=True,
             before_edl_version=16), "add_motion_graphic")
    assert "wait_for_job(job_id=9)" in text
    assert "next EDL version (after v16)" in text
    report = mcpmod._still_running(
        _job("running", tool="render_preview", mutation=False,
             args={"report": True}), "render_preview")
    assert "nothing is rendered" in report


class _Cur:
    def __init__(self, rows):
        self.rows, self.sql = rows, []

    def execute(self, sql, params=None):
        self.sql.append((sql, params))

    def fetchall(self):
        return self.rows


def test_the_version_conflict_names_what_wrote_the_newer_versions():
    cur = _Cur([{"id": 98592, "tool": "set_motion_graphic", "v": 18},
                {"id": 98588, "tool": "add_motion_graphic", "v": 17}])
    text = video._versions_since(cur, 4595, 16, 18)
    assert "v16 -> v18" in text
    assert text.index("add_motion_graphic (job 98588) wrote v17") < \
        text.index("set_motion_graphic (job 98592) wrote v18")
    assert "Rebuild the batch on v18" in text
    assert cur.sql[0][1] == (4595, 16, 18)
    # nothing newer, or a failing read: the plain refusal stands
    assert video._versions_since(_Cur([]), 1, 5, 5) == ""

    class Broken(_Cur):
        def execute(self, *_a):
            raise RuntimeError("db down")
    assert video._versions_since(Broken([]), 1, 5, 7) == ""
