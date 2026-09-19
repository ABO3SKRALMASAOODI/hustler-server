"""Only durable success for the exact physical call can release capacity."""
import sqlite3

import pytest

import db
import remote


@pytest.mark.parametrize("changes,expected", [
    ({}, True),
    ({"job_state": "running"}, False),
    ({"job_state": "failed"}, False),
    ({"remote_state": "running"}, False),
    ({"remote_state": "cancelled"}, False),
    ({"job_claim": 2}, False),
    ({"provider": "modal"}, False),
    ({"call_id": "cf-another-identity"}, False),
    ({"lane": "interactive"}, False),
])
def test_completed_call_proof_uses_both_durable_records(monkeypatch, changes, expected):
    # Execute the real SELECT and joins against local rows, including stale
    # claims whose queue job was completed by a newer executor.
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE video_jobs (id, type, project_id, total_claims, result, state)")
    conn.execute("CREATE TABLE remote_executions (job_id, total_claims, provider, call_id, function_name, state)")
    row = dict(job_state="done", remote_state="done", job_claim=1,
               provider="cloudflare", call_id="cf-completed-identity", lane="mcp")
    row.update(changes)
    conn.execute("INSERT INTO video_jobs VALUES (42, 'mcp_tool', 7, ?, ?, ?)",
                 (row["job_claim"], '{"asset_id":17}', row["job_state"]))
    conn.execute("INSERT INTO remote_executions VALUES (42, 1, ?, ?, ?, ?)",
                 (row["provider"], row["call_id"], row["lane"], row["remote_state"]))

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def execute(self, sql, args): self.cur = conn.execute(sql.replace("%s", "?"), args)
        def fetchone(self):
            found = self.cur.fetchone()
            return dict(found) if found else None

    class Conn:
        def cursor(self): return Cursor()

    monkeypatch.setattr(db, "remote_executions_table_ready", lambda _conn: True)
    proof = db.completed_remote_call(Conn(), "cloudflare", "cf-completed-identity", "mcp")
    assert bool(proof) is expected
    if expected:
        assert proof == dict(id=42, type="mcp_tool", project_id=7, total_claims=1,
                             result='{"asset_id":17}')
    conn.close()


@pytest.mark.parametrize("proof", [None, RuntimeError("database unavailable")])
def test_missing_proof_never_contacts_provider(monkeypatch, proof):
    class Probe:
        def run(self, fn, *args):
            assert fn is db.completed_remote_call
            if isinstance(proof, Exception): raise proof
            return proof
        def reset(self): pass
    monkeypatch.setattr(db, "Db", Probe)
    monkeypatch.setattr(remote.requests, "post", lambda *_a, **_k: pytest.fail("must not acknowledge"))
    assert remote._reconcile_completed_cloudflare_call("cf-completed-identity", "mcp") is False


def test_verified_completion_is_sent_with_original_result_and_identity(monkeypatch):
    proof = dict(id=42, total_claims=1, project_id=7, type="mcp_tool", result={"asset_id":17})
    class Probe:
        def run(self, fn, *args):
            assert fn is db.completed_remote_call
            assert args == ("cloudflare", "cf-completed-identity", "mcp")
            return proof
        def reset(self): pass
    class Response:
        status_code = 200
    posted = []
    monkeypatch.setattr(db, "Db", Probe)
    monkeypatch.setattr(remote.config, "CLOUDFLARE_EXECUTOR_URL", "https://executor.example")
    monkeypatch.setattr(remote.requests, "post", lambda url, **kwargs: posted.append((url, kwargs)) or Response())
    assert remote._reconcile_completed_cloudflare_call("cf-completed-identity", "mcp") is True
    assert posted[0][0] == "https://executor.example/calls/mcp/cf-completed-identity/complete"
    assert posted[0][1]["json"] == {
        "job": {k:v for k,v in proof.items() if k != "result"},
        "envelope": {"result": proof["result"], "job_completed": True},
    }


def test_busy_slot_checks_its_owner_without_relaunching_the_waiting_call(monkeypatch):
    job = dict(id=43, type="mcp_tool", project_id=7, total_claims=1, payload={})
    calls = []
    class Probe:
        def run(self, *_args, **_kwargs): return True
        def reset(self): pass
    class Response:
        status_code = 429
        def json(self):
            return {"error": "Cloudflare Container shard is busy", "safe_to_fallback": True,
                    "active_call_id": "cf-completed-identity"}
    monkeypatch.setattr(db, "Db", Probe)
    monkeypatch.setattr(db, "mark_remote_owned", lambda _id: True)
    monkeypatch.setattr(db, "remote_launch_recorded", lambda _id: None)
    monkeypatch.setattr(db, "unmark_remote_owned", lambda _id: None)
    monkeypatch.setattr(remote, "_cloudflare_preflight", lambda **_kwargs: {})
    monkeypatch.setattr(remote.requests, "post", lambda url, **_kwargs: calls.append(url) or Response())
    reconciled = []
    monkeypatch.setattr(remote, "_reconcile_completed_cloudflare_call", lambda *args: reconciled.append(args) or True)
    with pytest.raises(remote.CloudflareCapacityBusy): remote._run_cloudflare(job)
    assert len(calls) == 1
    assert reconciled == [("cf-completed-identity", "mcp")]
