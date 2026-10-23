"""A logical turn stopped for making no progress does not bill its stagnant
slices (studio project 2592, user 1970, 22 Oct 2026: jobs 88346 -> 88597 ->
88833 -> 89122; the first slice cut v2->v8, the next three circled the same
open finding for ~110 minutes, and the turn charged 110.86 credits)."""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_loop  # noqa: E402
import db  # noqa: E402
import job_completion  # noqa: E402
import model_prices  # noqa: E402


def test_the_2592_chain_leaves_its_three_stagnant_slices_unbilled():
    state = {}
    # 88346 ended "productive work remains": the run restarts (empty)
    state["stagnant_job_ids"] = agent_loop._stagnant_job_ids(
        state, 88346, False)
    assert state["stagnant_job_ids"] == []
    # 88597 and 88833 ended "recovering unresolved work"
    state["stagnant_job_ids"] = agent_loop._stagnant_job_ids(
        state, 88597, True)
    state["stagnant_job_ids"] = agent_loop._stagnant_job_ids(
        state, 88833, True)
    # a platform drain in between says nothing either way
    assert agent_loop._stagnant_job_ids(state, 88900, None) == [88597, 88833]
    # 89122 stopped: "no longer making progress"
    assert agent_loop._stagnant_job_ids(state, 89122, True) == [
        88597, 88833, 89122]
    # progress after a stagnant slice restarts the run
    assert agent_loop._stagnant_job_ids(state, 90000, False) == []


def test_the_blocked_stops_return_and_disclose_their_unbilled_slices():
    src = inspect.getsource(agent_loop)
    assert src.count('result["unbilled_job_ids"] = unbilled') == 2
    assert '"billable": billable, "unbilled_job_ids": unbilled}' in src
    assert src.count("_UNBILLED_NOTE") >= 3
    k = src.index('"empty model response recovery", fingerprint, repeats,')
    assert "stagnant=no_progress" in src[k:k + 120]
    assert "not _semantic_progressed(" in src[k - 400:k]
    assert "not charged" in agent_loop._UNBILLED_NOTE
    # the boundary that found no progress marks its slice stagnant; the one
    # that found progress restarts the run
    assert '"recovering unresolved work",' in src
    i = src.index('"recovering unresolved work",')
    assert "stagnant=True" in src[i:i + 300]
    j = src.index('else "productive work remains",')
    assert "stagnant=False" in src[j:j + 200]


class _Cursor:
    def __init__(self, shared):
        self.shared = shared
        self._row = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        self.shared.statements.append((normalized, params))
        if "FROM llm_calls WHERE job_id" in normalized:
            excluded = set(params[2])
            rows = [r for r in self.shared.calls if r["job"] not in excluded]
            self._row = {
                "n": len(rows),
                "tin": sum(r["tin"] for r in rows),
                "tout": sum(r["tout"] for r in rows),
                "token_cost": sum(r["usd"] for r in rows),
                "n_images": 0, "gen_cost": 0}
            return
        if "AS compute_cost FROM video_jobs" in normalized:
            excluded, excluded_text = set(params[2]), set(params[3])
            self._row = {"compute_cost": sum(
                usd for jid, parent, usd in self.shared.compute
                if jid not in excluded and str(parent) not in excluded_text)}
            return
        if "FROM users WHERE id = %s FOR UPDATE" in normalized:
            self._row = {"credits_daily": 0.0, "credits_bonus": 0.0,
                         "credits_monthly": 1000.0}
            return
        if normalized.startswith("SELECT pg_advisory_xact_lock"):
            self._row = {}
            return
        if normalized.startswith("SELECT credits_used FROM job_credits"):
            self._row = None
            return
        if normalized.startswith("INSERT INTO job_credits"):
            self.shared.claimed = float(params[4])
            self._row = {"credits_used": float(params[4])}
            return
        if normalized.startswith("UPDATE users"):
            self.shared.debited = float(params[2])
            self._row = None
            return
        raise AssertionError(normalized)

    def fetchone(self):
        return self._row


class _Shared:
    def __init__(self, calls, compute):
        self.calls, self.compute = calls, compute
        self.statements, self.claimed, self.debited = [], None, None


class _Conn:
    def __init__(self, shared):
        self.shared = shared

    def cursor(self):
        return _Cursor(self.shared)


def _chain():
    # model USD per slice and the renders each slice started
    calls = [{"job": 88346, "tin": 2_752_682, "tout": 12_823, "usd": 0.06},
             {"job": 88597, "tin": 4_535_592, "tout": 3_824, "usd": 0.09},
             {"job": 88833, "tin": 4_786_569, "tout": 14_892, "usd": 0.10},
             {"job": 89122, "tin": 719_595, "tout": 3_094, "usd": 0.02}]
    compute = [(88346, None, 0.0246), (88434, 88346, 0.0483),
               (89154, 89122, 0.1445), (89245, 89122, 0.0491)]
    return calls, compute


def test_charge_meters_only_the_slices_that_moved_the_edit():
    calls, compute = _chain()
    full = _Shared(calls, compute)
    charged_full = db.charge_turn_credits(_Conn(full), 1970, 88346)
    shared = _Shared(calls, compute)
    charged = db.charge_turn_credits(
        _Conn(shared), 1970, 88346,
        exclude_job_ids=[88597, 88833, "89122"])
    expected = model_prices.usd_to_credits(0.06 + 0.0246 + 0.0483)
    assert charged == round(expected, 2) == shared.claimed == shared.debited
    assert charged < charged_full
    # the stagnant slices' renders (agent_job_id) are left out with them
    sql, params = [s for s in shared.statements
                   if "AS compute_cost" in s[0]][0]
    assert "agent_job_id" in sql and params[3] == ["88597", "88833", "89122"]


def test_a_turn_whose_every_slice_stagnated_bills_the_floor_not_nothing():
    calls, compute = _chain()
    shared = _Shared(calls, compute)
    charged = db.charge_turn_credits(
        _Conn(shared), 1970, 88346,
        exclude_job_ids=[88346, 88597, 88833, 89122, 88434])
    assert charged == db.MIN_TURN_CREDITS == shared.debited


def test_a_turn_the_model_never_answered_still_costs_nothing():
    calls = [{"job": 88346, "tin": 0, "tout": 0, "usd": 0.0},
             {"job": 88597, "tin": 0, "tout": 0, "usd": 0.0}]
    shared = _Shared(calls, [])
    assert db.charge_turn_credits(
        _Conn(shared), 1970, 88346, exclude_job_ids=[88597]) == 0.0
    assert shared.debited is None


def test_no_exclusion_keeps_the_existing_metered_charge():
    calls, compute = _chain()
    shared = _Shared(calls, compute)
    charged = db.charge_turn_credits(_Conn(shared), 1970, 88346)
    usd = sum(c["usd"] for c in calls) + sum(c[2] for c in compute)
    assert charged == round(model_prices.usd_to_credits(usd), 2)


def test_completion_hands_the_unbilled_slices_to_the_charge(monkeypatch):
    seen = {}

    class WorkerDb:
        def run(self, fn, *args, **kwargs):
            assert fn is db.finish_accounted_job
            seen["args"], seen["kwargs"] = args, kwargs
            return {"committed": True, "charged": 21.4,
                    "billing_error": None, "qualification_error": None}

    job = {"id": 89122, "type": "agent_turn", "user_id": 1970,
           "project_id": 2592, "payload": {"root_agent_job_id": 88346}}
    result = {"status": "blocked", "outcome": "partial", "billable": True,
              "unbilled_job_ids": [88597, 88833, 89122]}
    assert job_completion.finalize_success(WorkerDb(), job, result, "lease")
    assert seen["args"][-1] == 88346
    assert seen["kwargs"] == {"exclude_job_ids": [88597, 88833, 89122]}
    assert result["credits_charged"] == 21.4


def test_terminal_billing_passes_exclusions_and_keeps_them_for_retry(
        monkeypatch):
    got, patches = {}, []
    monkeypatch.setattr(db, "finish_job", lambda *_a: True)

    def charge(conn, user, root, extra, **kw):
        got.update(kw)
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(db, "charge_turn_credits", charge)
    monkeypatch.setattr(db, "patch_done_job_result",
                        lambda _c, _j, patch=None, remove=(): patches.append(
                            patch) or True)

    class Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *_a):
            pass

    class Conn:
        def cursor(self):
            return Cur()

    out = db.finish_accounted_job(
        Conn(), 89122, {}, 1, 1970, billable=True, accounting_job_id=88346,
        exclude_job_ids=[88597, 88833, 89122])
    assert got == {"exclude_job_ids": [88597, 88833, 89122]}
    assert out["billing_error"]
    assert patches[-1]["billing_unbilled_job_ids"] == [88597, 88833, 89122]
