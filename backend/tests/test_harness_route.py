"""Harness v2 routing (A1): the stamp is decided once at enqueue, off by default."""
import json
import pathlib

from video_services import harness_route, jobs

ROOT = pathlib.Path(__file__).resolve().parents[2]


class Cur:
    """A cursor holding one optional app_kv row and recording inserts."""

    def __init__(self, kv=None, fail=False):
        self.kv, self.fail = kv, fail
        self.inserted, self._one, self.sql = [], None, []

    def execute(self, sql, params=()):
        self.sql.append(" ".join(sql.split()))
        if "FROM app_kv" in sql:
            if self.fail:
                raise RuntimeError("relation app_kv does not exist")
            self._one = None if self.kv is None else {
                "value": json.dumps(self.kv)}
        elif sql.lstrip().startswith("INSERT INTO video_jobs"):
            self.inserted.append({"type": params[2],
                                  "payload": params[3].adapted})
            self._one = {"id": 900 + len(self.inserted)}
        else:
            self._one = None

    def fetchone(self):
        return self._one


def _payload(cur):
    return cur.inserted[-1]["payload"]


def test_no_row_means_no_stamp():
    cur = Cur(kv=None)
    jobs.enqueue(cur, 7, 3120, "agent_turn", {"message_id": 1})
    assert "harness" not in _payload(cur)


def test_unreadable_row_or_failed_read_means_no_stamp():
    for cur in (Cur(kv="not json at all"), Cur(fail=True),
                Cur(kv={"mode": "sideways"})):
        jobs.enqueue(cur, 7, 3120, "agent_turn", {"message_id": 1})
        assert "harness" not in _payload(cur)


def test_mode_off_stamps_nobody_even_when_listed():
    cur = Cur(kv={"mode": "off", "user_ids": [3120]})
    jobs.enqueue(cur, 7, 3120, "agent_turn", {"message_id": 1})
    assert "harness" not in _payload(cur)


def test_a_listed_user_and_a_listed_project_are_stamped():
    cur = Cur(kv={"mode": "list", "user_ids": [3120], "project_ids": [5845]})
    jobs.enqueue(cur, 7, 3120, "agent_turn", {"message_id": 1})
    assert _payload(cur)["harness"] == "v2"
    jobs.enqueue(cur, 5845, 99, "mcp_tool", {"tool": "look"})
    assert _payload(cur)["harness"] == "v2"
    jobs.enqueue(cur, 8, 99, "agent_turn", {"message_id": 2})
    assert "harness" not in _payload(cur)


def test_mode_on_respects_surfaces():
    cur = Cur(kv={"mode": "on", "surfaces": ["studio"]})
    jobs.enqueue(cur, 7, 1, "agent_turn", {"message_id": 1})
    assert _payload(cur)["harness"] == "v2"
    jobs.enqueue(cur, 7, 1, "mcp_tool", {"tool": "look"})
    assert "harness" not in _payload(cur)
    jobs.enqueue(cur, 7, 1, "agent_turn", {"first_cut": True, "via": "mcp"})
    assert "harness" not in _payload(cur)


def test_other_job_types_are_never_stamped():
    cur = Cur(kv={"mode": "on"})
    for job_type in ("index", "preview", "final", "shorts_plan"):
        jobs.enqueue(cur, 7, 1, job_type, {})
        assert "harness" not in _payload(cur)


def test_a_non_internal_override_is_ignored_and_never_stored():
    cur = Cur(kv={"mode": "off", "override": True})
    jobs.enqueue(cur, 7, 3120, "agent_turn",
                 {"message_id": 1, "harness_request": "v2"})
    assert "harness" not in _payload(cur)
    assert "harness_request" not in _payload(cur)


def test_an_internal_override_needs_the_override_flag():
    cur = Cur(kv={"mode": "off", "override": False})
    jobs.enqueue(cur, 7, 60, "agent_turn",
                 {"message_id": 1, "harness_request": "v2"})
    assert "harness" not in _payload(cur)
    cur = Cur(kv={"mode": "off", "override": True})
    jobs.enqueue(cur, 7, 60, "agent_turn",
                 {"message_id": 1, "harness_request": "v2"})
    assert _payload(cur)["harness"] == "v2"


def test_an_internal_v1_override_keeps_a_listed_owner_on_v1():
    cur = Cur(kv={"mode": "on", "override": True})
    jobs.enqueue(cur, 7, 60, "agent_turn",
                 {"message_id": 1, "harness_request": "v1"})
    assert "harness" not in _payload(cur)


def test_a_continuation_keeps_its_stamp_after_the_kill_switch():
    cur = Cur(kv={"mode": "off"})
    jobs.enqueue(cur, 7, 3120, "agent_turn",
                 {"message_id": 1, "harness": "v2", "continuation": True})
    assert _payload(cur)["harness"] == "v2"


def test_requested_from_reads_body_and_meta():
    assert harness_route.requested_from({"harness": "V2"}) == "v2"
    assert harness_route.requested_from({"_meta": {"harness": "v1"}}) == "v1"
    assert harness_route.requested_from({"harness": "v9"}) is None
    assert harness_route.requested_from(None) is None


def test_a_failed_read_rolls_back_to_its_savepoint_and_the_job_is_queued():
    """The read runs in the caller's transaction; a failed read must not
    leave it aborted, or the INSERT that follows would fail with it."""
    cur = Cur(fail=True)
    jobs.enqueue(cur, 7, 3120, "agent_turn", {"message_id": 1})
    assert "harness" not in _payload(cur)
    kinds = [k for s in cur.sql
             for k in ("SAVEPOINT", "ROLLBACK TO SAVEPOINT",
                       "RELEASE SAVEPOINT", "INSERT INTO video_jobs")
             if s.startswith(k)]
    assert kinds == ["SAVEPOINT", "ROLLBACK TO SAVEPOINT",
                     "INSERT INTO video_jobs"]
    cur = Cur(kv={"mode": "on"})
    jobs.enqueue(cur, 7, 3120, "agent_turn", {"message_id": 1})
    assert _payload(cur)["harness"] == "v2"
    kinds = [k for s in cur.sql
             for k in ("SAVEPOINT", "ROLLBACK TO SAVEPOINT",
                       "RELEASE SAVEPOINT", "INSERT INTO video_jobs")
             if s.startswith(k)]
    assert kinds == ["SAVEPOINT", "RELEASE SAVEPOINT",
                     "INSERT INTO video_jobs"]


def test_stamp_is_the_one_rule_both_processes_apply():
    payload = {"message_id": 1, "harness_request": "v2"}
    out = harness_route.stamp(Cur(kv={"mode": "on"}), 3120, 7,
                              "agent_turn", payload)
    assert out is payload and payload == {"message_id": 1, "harness": "v2"}
    payload = {"message_id": 1, "harness": "v1-pinned"}
    harness_route.stamp(Cur(kv={"mode": "on"}), 3120, 7, "agent_turn",
                        payload)
    assert payload["harness"] == "v1-pinned"


def test_the_worker_twin_is_identical():
    """The worker queues requests too (db.enqueue_job: the index's
    auto-resume, gate_lift); it applies this same file."""
    a = (ROOT / "backend" / "video_services" / "harness_route.py").read_bytes()
    b = (ROOT / "worker" / "harness_route.py").read_bytes()
    assert a == b, ("edit backend/video_services/harness_route.py and copy "
                    "it to worker/")
