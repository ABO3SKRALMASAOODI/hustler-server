"""Lane P (13 Nov 2026): paying customers who never got a film.

12 of the 21 paying Plus accounts had never seen a film. Three changes:
  * a paying account's upload that met the wall and was unlocked by the
    payment is admitted to the First cut (account 2941);
  * an MCP caller can ask for the First cut on any project of a paying
    account (make_first_cut), so an MCP-only plan has a film in one call;
  * the balance payload tells the Studio that the next upload becomes a film
    (first_cut_on_upload), and an MCP-only start says so.
"""
import json
import os
import sys

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import free_trial  # noqa: E402
from routes import mcp as mcpmod  # noqa: E402
from routes import video  # noqa: E402
from test_free_trial import DB, _fresh, _on  # noqa: E402


class LaneDB(DB):
    def __init__(self, *a, today_mcp=0, any_admission=False, **kw):
        super().__init__(*a, **kw)
        self.today_mcp = today_mcp
        self.any_admission = any_admission

    def execute(self, sql, params=None):
        q = " ".join(sql.split())
        if q.startswith("SELECT COUNT(*) AS n FROM projects"):
            self._row = {"n": self.today_mcp}
            return
        if q.startswith("SELECT 1 FROM projects WHERE user_id"):
            self._row = {"x": 1} if self.any_admission else None
            return
        super().execute(sql, params)


# ------------------------------------------------------- MCP make_first_cut

def test_mcp_first_cut_admits_any_project_of_a_paying_account():
    _fresh()
    db = LaneDB(stored=_on(), other_admission=True)
    got, reason = free_trial.admit_mcp_first_cut(db, 2555, 5677)
    assert reason is None and got["kind"] == "payer" and got["via"] == "mcp"
    assert db.updates == [got]
    # idempotent: an admitted project returns its admission, writes nothing
    again, reason = free_trial.admit_mcp_first_cut(db, 2555, 5677)
    assert again == got and len(db.updates) == 1


def test_mcp_first_cut_keeps_the_switch_caps_and_the_owners_account_out():
    _fresh()
    assert free_trial.admit_mcp_first_cut(LaneDB(stored=None), 2555, 1) == \
        (None, "paused")
    _fresh()
    assert free_trial.admit_mcp_first_cut(LaneDB(stored=_on()), 60, 1) == \
        (None, "excluded")
    _fresh()
    assert free_trial.admit_mcp_first_cut(
        LaneDB(stored=_on(), today_mcp=free_trial.MCP_FIRST_CUTS_PER_DAY),
        2555, 1) == (None, "daily_limit")
    _fresh()
    assert free_trial.admit_mcp_first_cut(
        LaneDB(stored=_on(), month_usd=10_000.0), 2555, 1) == (None, "paused")
    # No size rule (user 3023, 13 Nov 2026): a 10 GB source is admitted.
    _fresh()
    got, reason = free_trial.admit_mcp_first_cut(
        LaneDB(stored=_on()), 2555, 1, nbytes=10 * 1024 ** 3)
    assert reason is None and got["first_cut"] is True


def test_make_first_cut_is_a_published_session_tool():
    names = {t["name"] for t in mcpmod.SESSION_TOOLS}
    assert "make_first_cut" in names
    assert mcpmod.SESSION_IMPL["make_first_cut"] is mcpmod._t_make_first_cut
    spec = next(t for t in mcpmod.SESSION_TOOLS if t["name"] == "make_first_cut")
    assert spec["inputSchema"]["required"] == ["project_id"]
    assert spec["annotations"]["readOnlyHint"] is False
    # the guide tells the caller it exists
    assert "make_first_cut(project_id)" in mcpmod.WORKFLOW
    assert "mcp_first_cut_started" in video.CLIENT_EVENT_KINDS


class _Cur:
    """The MCP handler's reads, scripted per test."""

    def __init__(self, project=True, original=None, indexed=True,
                 first_cut_job=None, busy=False, subscribed=True):
        self.project, self.original = project, original
        self.indexed, self.first_cut_job = indexed, first_cut_job
        self.busy, self.subscribed = busy, subscribed
        self._row = None
        self.sql = []

    def execute(self, sql, params=None):
        q = " ".join(sql.split())
        self.sql.append(q)
        if "payload->>'first_cut' = 'true'" in q:
            self._row = self.first_cut_job
        elif "state IN ('queued', 'running')" in q:
            self._row = {"x": 1} if self.busy else None
        elif q.startswith("SELECT is_subscribed FROM users"):
            self._row = {"is_subscribed": self.subscribed}
        else:
            self._row = None

    def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, cur):
        self.cur = cur

    def cursor(self):
        return self.cur

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _wire(monkeypatch, cur, admit=None, enqueued=None):
    monkeypatch.setattr(mcpmod, "vdb", lambda: _Conn(cur))
    monkeypatch.setattr(mcpmod, "_project_for_user",
                        lambda c, pid, uid: {"id": pid} if cur.project else None)
    monkeypatch.setattr(mcpmod, "_active_original",
                        lambda c, pid: cur.original)
    monkeypatch.setattr(mcpmod, "_index_row",
                        lambda c, sha: {"id": 1} if cur.indexed else None)
    monkeypatch.setattr(mcpmod, "_latest_edl_version", lambda c, pid: 3)
    monkeypatch.setattr(mcpmod, "record_client_event", lambda *a, **k: True)
    monkeypatch.setattr(free_trial, "admit_mcp_first_cut",
                        admit or (lambda c, u, p, n=None: (
                            {"kind": "payer", "first_cut": True}, None)))
    jobs = enqueued if enqueued is not None else []

    def fake_enqueue(c, pid, uid, kind, payload):
        jobs.append((pid, uid, kind, payload))
        return 9001
    monkeypatch.setattr(mcpmod, "_enqueue", fake_enqueue)
    return jobs


ORIGINAL = {"id": 7, "sha256": "abc", "bytes": 1000}


def test_make_first_cut_enqueues_the_first_cut_chain(monkeypatch):
    cur = _Cur(original=ORIGINAL)
    jobs = _wire(monkeypatch, cur)
    out = mcpmod._t_make_first_cut({"user_id": 2555}, {"project_id": 5677})
    assert jobs == [(5677, 2555, "agent_turn",
                     {"first_cut": True, "trial_lane": True, "via": "mcp"})]
    assert out["structuredContent"]["job_id"] == 9001
    assert "wait_for_job(job_id=9001)" in out["content"][0]["text"]


def test_make_first_cut_never_runs_twice_or_over_an_edit(monkeypatch):
    cur = _Cur(original=ORIGINAL, first_cut_job={"id": 44, "state": "done"})
    jobs = _wire(monkeypatch, cur)
    out = mcpmod._t_make_first_cut({"user_id": 2555}, {"project_id": 5677})
    assert jobs == [] and "job 44" in out and "edl_version=3" in out
    cur = _Cur(original=ORIGINAL, busy=True)
    jobs = _wire(monkeypatch, cur)
    out = mcpmod._t_make_first_cut({"user_id": 2555}, {"project_id": 5677})
    assert jobs == [] and out["isError"] is True


def test_make_first_cut_needs_an_analyzed_video_and_a_plan(monkeypatch):
    for cur in (_Cur(original=None), _Cur(original=ORIGINAL, indexed=False),
                _Cur(original=ORIGINAL, subscribed=False),
                _Cur(project=False, original=ORIGINAL)):
        jobs = _wire(monkeypatch, cur)
        out = mcpmod._t_make_first_cut({"user_id": 2555}, {"project_id": 5})
        assert jobs == []
        assert isinstance(out, mcpmod._SessionToolError) or \
            (isinstance(out, dict) and out.get("isError") is True)


def test_make_first_cut_says_why_when_admission_is_refused(monkeypatch):
    cur = _Cur(original=ORIGINAL)
    jobs = _wire(monkeypatch, cur,
                 admit=lambda c, u, p, n=None: (None, "daily_limit"))
    out = mcpmod._t_make_first_cut({"user_id": 2555}, {"project_id": 5677})
    assert jobs == [] and out["isError"] is True
    assert "today's first cuts" in out["content"][0]["text"]


# --------------------------------------- the upload a payment unlocked (2941)

def test_an_upload_unlocked_by_a_payment_is_admitted(monkeypatch):
    calls = []
    monkeypatch.setattr(video, "_subscribe_gate_applies", lambda c, u: False)
    monkeypatch.setattr(free_trial, "admit_upload",
                        lambda c, u, p, subscribed, nbytes=None: calls.append(
                            (u, p, subscribed, nbytes)) or {"kind": "payer"})

    class Cur:
        def execute(self, sql, params=None):
            pass
    got = video._admit_unlocked_upload(Cur(), 2941, 5617,
                                       {"id": 40835, "bytes": 125346063})
    assert got == {"kind": "payer"}
    assert calls == [(2941, 5617, True, 125346063)]


def test_a_still_walled_account_is_not_admitted_by_the_unlock(monkeypatch):
    monkeypatch.setattr(video, "_subscribe_gate_applies", lambda c, u: True)
    monkeypatch.setattr(free_trial, "admit_upload",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    assert video._admit_unlocked_upload(object(), 1, 2, {"bytes": 1}) is None


def test_both_unlock_paths_try_the_admission_before_the_analysis():
    import inspect
    src = inspect.getsource(video)
    # checkout's replay and the first state poll after the payment
    import re
    calls = re.findall(r"(?m)^\s+_admit_unlocked_upload\(cur, user_id, "
                       r"project_id, original\)", src)
    assert len(calls) == 2


# ------------------------------------------- the Studio's empty start (flag)

def test_first_cut_on_upload_reads_the_same_rules():
    _fresh()
    assert free_trial.first_cut_on_upload(LaneDB(stored=_on()), 2380) is True
    _fresh()
    assert free_trial.first_cut_on_upload(
        LaneDB(stored=_on(), any_admission=True), 2380) is False
    _fresh()
    # The owner's OFF (13 Nov 2026) is the paywall for unpaid accounts; a
    # paying account's first project keeps its First cut ...
    assert free_trial.first_cut_on_upload(
        LaneDB(stored=json.dumps({"on": False})), 2380) is True
    _fresh()
    # ... unless payers_while_off is false: then OFF admits nobody.
    assert free_trial.first_cut_on_upload(LaneDB(stored=json.dumps(
        {"on": False, "payers_while_off": False})), 2380) is False
    _fresh()
    assert free_trial.first_cut_on_upload(LaneDB(stored=_on()), 60) is False
    _fresh()
    assert free_trial.first_cut_on_upload(
        LaneDB(stored=_on(payer_first_cut=False)), 2380) is False
