"""An idempotent upload retry can recover an unstarted deployment refusal."""

import pytest

from video_services.upload import completed_upload_index


class Cursor:
    def __init__(self, state="failed", failure="provider_rollout_pending",
                 *, active=False, recent_count=1):
        self.jobs = [{"id": 81, "state": state, "error": "version mismatch",
                      "payload": {"asset_id": 44, "client_proxy_key": "proxy/7.mp4",
                                  "execution_policy": "redesign", "dedup_src": 22,
                                  "cloudflare_rollout_deferrals": 1,
                                  "execution_provider": "cloudflare"},
                      "result": {"failure": {"kind": failure}}}]
        self.active = active
        self.recent_count = recent_count
        self.inserts = []
        self.row = None

    def execute(self, sql, params):
        if "INSERT INTO video_jobs" in sql:
            project_id, user_id, job_type, payload = params
            self.inserts.append((project_id, user_id, job_type, payload.adapted))
            self.row = {"id": 82}
            self.jobs.append({"id": 82, "state": "queued"})
        else:
            assert "payload->>'asset_id' = %s" in sql
            assert params == (7, "44")
            if "ARRAY_AGG" in sql:
                self.row = {**self.jobs[-1], "active": self.active,
                            "recent_count": self.recent_count}
            else:
                self.row = self.jobs[-1] if self.jobs else None

    def fetchone(self):
        return self.row


def recover(cur, can_retry=True):
    return completed_upload_index(cur, 7, 6, 44, can_retry=can_retry)


def test_retry_recovers_rollout_refusal_once_and_reuses_next_receipt():
    cur = Cursor()
    assert recover(cur) == {"index_job_id": 82, "index_job_state": "queued",
                            "index_restarted": True}
    assert recover(cur) == {"index_job_id": 82, "index_job_state": "queued"}
    assert cur.inserts == [(7, 6, "index", {
        "asset_id": 44, "client_proxy_key": "proxy/7.mp4", "dedup_src": 22,
        "execution_policy": "redesign", "retry_of": 81})]
    assert cur.jobs[0]["state"] == "failed"  # Preserve incident evidence.


@pytest.mark.parametrize("failure", ["provider_error", "execution_failed",
                                    "invalid_media", "ambiguous_execution", None])
def test_retry_does_not_replay_actual_or_ambiguous_execution_failures(failure):
    cur = Cursor(failure=failure)
    assert recover(cur)["index_job_state"] == "failed"
    assert not cur.inserts


@pytest.mark.parametrize("state", ["queued", "running", "done"])
def test_retry_reuses_active_or_completed_analysis(state):
    cur = Cursor(state=state)
    assert recover(cur) == {"index_job_id": 81, "index_job_state": state}
    assert not cur.inserts


@pytest.mark.parametrize("active,recent_count,can_retry", [
    (True, 1, True), (False, 3, True), (False, 1, False),
])
def test_retry_respects_other_active_work_recovery_budget_and_admission(
        active, recent_count, can_retry):
    cur = Cursor(active=active, recent_count=recent_count)
    assert recover(cur, can_retry)["index_job_state"] == "failed"
    assert not cur.inserts


def test_missing_analysis_is_not_mistaken_for_a_rollout_failure():
    cur = Cursor()
    cur.jobs = []
    assert recover(cur) == {"index_job_id": None}
    assert not cur.inserts
