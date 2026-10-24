"""The final gate asks the WORKER's cache rules, not a hand-written mirror.

74ffcc0e fixed repaint patches that ran one frame late (an orphan frame where
an erase window spans a camera cut) and stamped renders `patch_v`. The
worker's cache busts every pre-fix render of an EDL with a patch, but the
backend's gate mirrored only outro_v, trans_v and wm_v, and the studio and
export_final short-circuit to an existing final. So export_final on Gates s23
(project 4356, EDL v36) handed back the pre-fix final (job 95595, asset
31550) with the orphan still in it. card_v, tp_v and every other worker-only
stamp had the same hole.

The general fix: every render stamps the rules it was made under, every
final job's answer carries the rules it was given under, the backend learns
the deployed rules from the newest renders, and a final no worker running
them has made or vouched for is hidden, so the next Download asks the
worker's own cache. These tests pin that, and the two properties that keep it
from deadlocking Download (tests/test_final_gate.py): it never demands rules
no deployed worker writes, and an answer given while today's rules were live
is final.

    cd backend && python -m pytest tests/test_final_gate_rules.py -q
"""

import os
import sys

import pytest

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask                                     # noqa: E402

from routes import video                                    # noqa: E402

# The rules today's worker writes (a subset is enough: the gate treats them
# as opaque versions).
TODAY = {"OUTRO_VERSION": 15, "CARD_LAYOUT_VERSION": 3,
         "PATCH_CLOCK_VERSION": 1, "TRUE_PEAK_GUARD_VERSION": 1}
BEFORE_PATCH = {k: v for k, v in TODAY.items() if k != "PATCH_CLOCK_VERSION"}
# Asset 31550 as stored: every mirrored stamp current, no `rules`, no patch_v.
S23_META = {"outro_v": video.OUTRO_VERSION,
            "trans_v": video.TRANSITION_VERSION, "wm_v": 0,
            "card_v": 3, "delivery_v": 1, "edl_version": 36,
            "render_job_id": 95595}
# The newest renders after the release, newest first: (rules, job id).
LIVE = [(TODAY, 96010), (TODAY, 96004), (TODAY, 96001)]


class _Cur:
    """Canned answers for every query _final_gate issues."""

    def __init__(self, *, rendered=(), answers=()):
        self.rendered = list(rendered)
        self.answers = list(answers)
        self.calls = []
        self._rows, self._one = [], None

    def execute(self, sql, params=None):
        self.calls.append(sql)
        self._rows, self._one = [], None
        if "meta -> 'rules'" in sql:
            self._rows = [{"rules": r, "job": str(j) if j else None}
                          for r, j in self.rendered]
        elif "render_asset_id" in sql:
            self._rows = list(self.answers)
        elif "bool_or" in sql:
            self._one = {"emits": True}
        elif "FROM users" in sql:
            self._one = {"is_subscribed": 1, "plan": "ai"}
        elif "to_regclass" in sql:
            self._one = {"t": None}

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._one


def _answer(aid, job, cached=False, rules=None):
    return {"job_id": job, "aid": aid, "cached": cached, "rules": rules}


def _gate(**kw):
    video._pipeline_probe.clear()
    return video._final_gate(_Cur(**kw), project_id=4356, user_id=7)


# ── the Gates s23 shape ─────────────────────────────────────────────────────

def test_a_final_made_before_todays_rules_is_hidden_so_the_worker_is_asked():
    ok = _gate(rendered=LIVE, answers=[_answer(31550, 95595)])
    assert not ok(31550, S23_META, 36)


def test_its_replacement_made_under_todays_rules_is_downloadable():
    ok = _gate(rendered=LIVE, answers=[_answer(31550, 95595),
                                       _answer(31700, 96020)])
    assert ok(31700, dict(S23_META, patch_v=1, rules=TODAY,
                          render_job_id=96020), 36)


def test_a_final_the_worker_vouched_for_under_todays_rules_is_served():
    """A pre-release final that is still current costs one `cached: true`
    answer, not a re-encode, and is then downloadable like any other."""
    ok = _gate(rendered=LIVE, answers=[
        _answer(200, 90000), _answer(200, 96030, cached=True, rules=TODAY)])
    assert ok(200, S23_META, 5)


def test_the_old_loop_breaker_cannot_pin_a_final_from_before_the_rules():
    """`cached: true` once, before a rule moved, is not the worker running
    today's rules saying so. It used to pass every check forever."""
    ok = _gate(rendered=LIVE, answers=[_answer(200, 90000, cached=True)])
    assert not ok(200, S23_META, 5)


def test_card_and_true_peak_bumps_hide_too_every_rule_is_the_same_rule():
    newer = dict(TODAY, CARD_LAYOUT_VERSION=4)
    ok = _gate(rendered=[(newer, 97000)] + LIVE,
               answers=[_answer(300, 96500, rules=TODAY)])
    assert not ok(300, dict(S23_META, rules=TODAY), 5)
    assert ok(301, dict(S23_META, rules=newer), 5)


def test_a_rule_the_final_never_heard_of_counts_as_never_applied():
    ok = _gate(rendered=[(dict(TODAY, NEW_RULE_VERSION=1), 97000)])
    assert not ok(400, dict(S23_META, rules=TODAY), 5)


def test_rules_that_are_not_numbers_must_match_exactly():
    assert video._rules_cover({"A": 2, "B": "x"}, {"A": 1, "B": "x"})
    assert not video._rules_cover({"A": 2, "B": "y"}, {"A": 1, "B": "x"})
    assert not video._rules_cover({"A": True}, {"A": 1})
    assert not video._rules_cover(None, {"A": 1})


# ── never demand what no deployed worker writes ─────────────────────────────

def test_an_executor_that_writes_no_rules_turns_the_check_off():
    """The backend deploys minutes after a push, the executor up to an hour
    later: until a render carries `rules`, finals keep the mirrored checks."""
    ok = _gate(rendered=[(None, 96000), ({}, 95999)],
               answers=[_answer(31550, 95595)])
    assert ok(31550, S23_META, 36)


def test_a_mixed_rollout_asks_for_the_newer_rules():
    video._pipeline_probe.clear()
    want, since = video._deployed_render_rules(_Cur(
        rendered=[(BEFORE_PATCH, 96011), (TODAY, 96010), (BEFORE_PATCH, 96002),
                  (TODAY, 96001)]))
    assert want == TODAY
    assert since == 96001


def test_an_older_lanes_answer_after_the_rules_went_live_is_final():
    """Mid rollout an older lane may answer `cached: true` under its older
    rules. Asking again could land on it again: the answer stands, so
    Download can never spin."""
    ok = _gate(rendered=LIVE, answers=[
        _answer(500, 96005, cached=True, rules=BEFORE_PATCH)])
    assert ok(500, S23_META, 5)
    ok = _gate(rendered=LIVE, answers=[
        _answer(501, 96007, cached=False, rules=None)])     # a fresh render
    assert ok(501, S23_META, 5)


def test_the_probe_is_one_query_per_ttl_not_one_per_poll():
    video._pipeline_probe.clear()
    cur = _Cur(rendered=LIVE)
    for _ in range(3):
        video._final_gate(cur, project_id=4356, user_id=7)
    assert sum("meta -> 'rules'" in c for c in cur.calls) == 1
    # ...and the answers are still one indexed read per gate.
    assert sum("render_asset_id" in c for c in cur.calls) == 3


def test_held_and_undersized_finals_stay_hidden_whatever_the_rules():
    ok = _gate(rendered=LIVE)
    held = [{"device": "x", "rung": "substitute"}]
    assert not ok(600, dict(S23_META, rules=TODAY, degraded_hold=held), 5)


# ── export_final: a hidden final is a new job, not the old file ─────────────

class _ReqCur(_Cur):
    def __init__(self, existing, **kw):
        super().__init__(**kw)
        self.existing = existing

    def execute(self, sql, params=None):
        if "meta->>'variant'='final' AND meta->>'edl_version'=%s" in sql:
            self.calls.append(sql)
            self._rows, self._one = [], self.existing
            return
        if any(f in sql for f in ("pg_advisory_xact_lock",
                                  "SELECT version, json FROM edls",
                                  "type = 'agent_turn'", "SELECT cm.meta",
                                  "'queued','running'", "state = 'failed'")):
            self.calls.append(sql)
            self._rows = []
            self._one = ({"version": 36, "json": {}}
                         if "SELECT version, json FROM edls" in sql else None)
            return
        super().execute(sql, params)


@pytest.fixture
def _request(monkeypatch):
    monkeypatch.setattr(video, "_project_for_user", lambda *a: {"id": 4356})
    monkeypatch.setattr(video, "_obsolete_failed_render_version",
                        lambda *a: None)
    monkeypatch.setattr(video, "_active_original",
                        lambda *a: {"duration_s": 3600, "meta": {}})
    monkeypatch.setattr(video, "_export_edl_error", lambda *a: None)
    monkeypatch.setattr(video, "record_client_event", lambda *a, **k: None)
    monkeypatch.setattr(video, "_enqueue", lambda *a, **k: 98000)

    def run(cur):
        video._pipeline_probe.clear()
        with Flask(__name__).app_context():
            res = video._request_final(cur, 7, 4356, 36, reuse_existing=True)
            resp, status = res if isinstance(res, tuple) else (res, 200)
            return resp.get_json(), status
    return run


def test_export_final_renders_again_instead_of_returning_the_pre_fix_final(
        _request):
    cur = _ReqCur({"id": 31550, "meta": S23_META}, rendered=LIVE,
                  answers=[_answer(31550, 95595)])
    assert _request(cur) == ({"job_id": 98000}, 200)


def test_export_final_reuses_a_final_made_under_todays_rules(_request):
    cur = _ReqCur({"id": 31700, "meta": dict(S23_META, rules=TODAY)},
                  rendered=LIVE, answers=[_answer(31700, 96020)])
    assert _request(cur) == ({"asset_id": 31700, "reused": True}, 200)
