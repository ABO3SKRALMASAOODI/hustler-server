"""An MCP render_preview no longer waits for its render inside the MCP
container (cost review, 25 Oct 2026).

The MCP lane is billed by the awake second at 4 GiB, and its render_preview
used to poll Postgres there for the whole encode while the interactive lane
rendered: 126 of the lane's 210 busy hours in the week to 25 Oct. The MCP
server now asks for a short wait (payload ``render_park_s``); a render still
going PARKS the call (the row names the render job), the server waits on that
job in its web process, then requeues the same row with
``adopt_render_job_id`` and the second run reviews the finished render
exactly as before. What is pinned here, worker side:

  1. a parked complete preview or proof enqueues once and names its job;
  2. a resumed call adopts that job instead of enqueueing another;
  3. a job that is not the exact same work is never adopted;
  4. without the MCP server's request nothing parks (Studio, agents, old
     servers keep the normal wait);
  5. the parked row is a plain success, unaccounted, and the arming never
     outlives the call on the cached context.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_tools                                      # noqa: E402
import mcp_exec                                         # noqa: E402


class _Db:
    def __init__(self, jobs=None, enqueue=(77, True), check_enqueue=(88, True)):
        self.calls = []
        self.jobs = dict(jobs or {})
        self.enqueue = enqueue
        self.check_enqueue = check_enqueue

    def run(self, fn, *args):
        self.calls.append((fn, args))
        if fn is agent_tools.dbx.get_or_enqueue_preview_job:
            return self.enqueue
        if fn is agent_tools.dbx.get_or_enqueue_preview_check_job:
            return self.check_enqueue
        if fn is agent_tools.dbx.get_job:
            return self.jobs.get(int(args[0]))
        raise AssertionError(f"unexpected db call {fn}")

    def names(self):
        return [fn.__name__ for fn, _args in self.calls]


def _ctx(db, version=2, **extra):
    class Ctx:
        autorendering = False
        rendered_versions = set()
        failed_preview_versions = {}
        last_preview = None
        last_preview_failure = None
        spec_preview_jobs = {}
        spec_preview_check_jobs = {}
        checked_versions = set()
        _proof_ranges_by_version = {}
        project_id = 12
        project = {"id": 12}
        job = {"id": 99, "user_id": 5, "type": "mcp_tool"}
        audio_model_review = False
        edit_plan = None
        has_main_video = True

        @staticmethod
        def latest_edl():
            return {"version": version, "json": {}}

    ctx = Ctx()
    ctx.db = db
    ctx.rendered_versions = set()
    ctx.failed_preview_versions = {}
    ctx.spec_preview_jobs = {}
    ctx.spec_preview_check_jobs = {}
    ctx.checked_versions = set()
    for key, value in extra.items():
        setattr(ctx, key, value)
    return ctx


def _quiet(monkeypatch, ranges=None):
    monkeypatch.setattr(agent_tools, "_verify_plan_for", lambda *_a: None)
    monkeypatch.setattr(agent_tools, "_sequence_screening_frames",
                        lambda *_a: [])
    monkeypatch.setattr(agent_tools, "_change_check_ranges",
                        lambda *_a: (ranges or [], None))


def test_a_parked_complete_preview_enqueues_once_and_names_its_job(
        monkeypatch):
    _quiet(monkeypatch)
    db = _Db()
    ctx = _ctx(db, mcp_render_park_s=0.0)
    out = agent_tools.render_preview(ctx, complete=True, quality="approval")
    assert out.startswith("RENDER QUEUED:")
    assert "render job 77" in out and "EDL v2" in out
    assert db.names() == ["get_or_enqueue_preview_job"]
    assert ctx.mcp_parked_render == {"job_id": 77, "type": "preview",
                                     "edl_version": 2, "quality": "approval"}
    # nothing was rendered or reviewed yet
    assert ctx.rendered_versions == set()


def test_a_parked_result_is_a_plain_success_not_a_prerequisite(monkeypatch):
    _quiet(monkeypatch)
    ctx = _ctx(_Db(), mcp_render_park_s=0.0)
    out = agent_tools.render_preview(ctx, complete=True)
    contract = mcp_exec._tool_result_contract(ctx, out)
    assert contract["is_error"] is False
    assert contract["tool_outcome"]["status"] == "success"
    assert "PREREQUISITE" not in out.upper()


def test_a_resumed_call_adopts_the_parked_render(monkeypatch):
    _quiet(monkeypatch)
    parked = {"id": 77, "type": "preview", "project_id": 12, "state": "failed",
              "error": "invalid filtergraph",
              "payload": {"edl_version": 2, "quality": "approval",
                          "audio_model_review": False},
              "result": {"failure": {"error": "invalid filtergraph",
                                     "kind": "invalid_edl",
                                     "agent_repairable": True}}}
    db = _Db(jobs={77: parked})
    ctx = _ctx(db, mcp_adopt_render_job_id=77)
    out = agent_tools.render_preview(ctx, complete=True, quality="approval")
    # adopted: looked up and polled, never enqueued again
    assert "get_or_enqueue_preview_job" not in db.names()
    assert db.names() == ["get_job", "get_job"]
    assert "invalid filtergraph" in out
    assert ctx.failed_preview_versions[2]["agent_repairable"] is True


def test_a_render_of_other_work_is_never_adopted(monkeypatch):
    _quiet(monkeypatch)
    other = {"id": 77, "type": "preview", "project_id": 12, "state": "done",
             "payload": {"edl_version": 2, "quality": "draft",
                         "audio_model_review": False}, "result": {}}
    for job in (dict(other, project_id=13),
                dict(other, payload=dict(other["payload"], edl_version=1)),
                dict(other, type="final"),
                other):                       # draft != approval
        db = _Db(jobs={77: job})
        ctx = _ctx(db, mcp_adopt_render_job_id=77, mcp_render_park_s=0.0)
        out = agent_tools.render_preview(ctx, complete=True,
                                         quality="approval")
        assert db.names() == ["get_job", "get_or_enqueue_preview_job"], job
        assert out.startswith("RENDER QUEUED:")


def test_a_parked_proof_names_its_preview_check_job(monkeypatch):
    _quiet(monkeypatch, ranges=[[1.0, 3.0]])
    db = _Db()
    ctx = _ctx(db, mcp_render_park_s=0.0)
    out = agent_tools.render_preview(ctx)
    assert out.startswith("RENDER QUEUED:")
    assert "changed-section proof" in out and "render job 88" in out
    assert db.names() == ["get_or_enqueue_preview_check_job"]
    assert ctx.mcp_parked_render == {"job_id": 88, "type": "preview_check",
                                     "edl_version": 2}
    assert ctx.spec_preview_check_jobs == {2: 88}


def test_a_resumed_proof_adopts_only_the_same_ranges(monkeypatch):
    _quiet(monkeypatch, ranges=[[1.0, 3.0]])
    requested = [pair for page in agent_tools._proof_pages([[1.0, 3.0]])
                 for pair in page]
    job = {"id": 88, "type": "preview_check", "project_id": 12,
           "state": "failed", "error": "bad proof",
           "payload": {"edl_version": 2, "check_ranges": requested},
           "result": {"failure": {"error": "bad proof",
                                  "agent_repairable": True}}}
    db = _Db(jobs={88: job})
    ctx = _ctx(db, mcp_adopt_render_job_id=88)
    out = agent_tools.render_preview(ctx)
    assert "get_or_enqueue_preview_check_job" not in db.names()
    assert "bad proof" in out
    wider = dict(job, payload={"edl_version": 2,
                               "check_ranges": [[0.0, 9.0]]})
    db = _Db(jobs={88: wider})
    ctx = _ctx(db, mcp_adopt_render_job_id=88, mcp_render_park_s=0.0)
    agent_tools.render_preview(ctx)
    assert "get_or_enqueue_preview_check_job" in db.names()


def test_without_the_servers_request_nothing_parks(monkeypatch):
    _quiet(monkeypatch)
    db = _Db()
    ctx = _ctx(db)
    # the terminal honesty pass keeps its own (zero) wait and its own text
    out = agent_tools.render_preview(ctx, complete=True, _wait_timeout_s=0)
    assert out.startswith("PREREQUISITE: complete preview")
    assert getattr(ctx, "mcp_parked_render", None) is None
    assert agent_tools._mcp_render_park_s(ctx) is None


def test_the_park_window_is_bounded():
    ctx = _ctx(_Db(), mcp_render_park_s=900)
    assert agent_tools._mcp_render_park_s(ctx) == 30.0
    ctx.mcp_render_park_s = "soon"
    assert agent_tools._mcp_render_park_s(ctx) is None


def test_arming_applies_to_render_preview_only_and_never_lingers():
    ctx = _ctx(_Db())
    mcp_exec._arm_render_park(ctx, "render_preview",
                              {"render_park_s": 3, "adopt_render_job_id": 77})
    assert ctx.mcp_render_park_s == 3.0 and ctx.mcp_adopt_render_job_id == 77
    mcp_exec._arm_render_park(ctx, "look_at",
                              {"render_park_s": 3, "adopt_render_job_id": 77})
    assert ctx.mcp_render_park_s is None and ctx.mcp_adopt_render_job_id is None
    mcp_exec._arm_render_park(ctx, "render_preview",
                              {"render_park_s": True,
                               "adopt_render_job_id": "77"})
    assert ctx.mcp_render_park_s is None and ctx.mcp_adopt_render_job_id is None
    ctx.mcp_parked_render = {"job_id": 77}
    mcp_exec._disarm_render_park(ctx)
    assert ctx.mcp_parked_render is None


def test_the_parked_row_is_unaccounted_and_names_the_render():
    ctx = _ctx(_Db())
    ctx.plan = "mcp_connect"
    out = mcp_exec._parked_result(
        ctx, "RENDER QUEUED: the complete preview of EDL v2 is render job 77.",
        2, {"job_id": 77, "type": "preview", "edl_version": 2})
    assert out["parked_render"]["job_id"] == 77
    assert out["is_error"] is False
    # job_completion.finalize_success accounts only metered_contract rows:
    # the run that answers is the one that is metered, as before
    assert "metered_contract" not in out
    assert out["edl_changed"] is False
