"""Measured store health and the automatic failover (12 Nov 2026: R2 refused
every call with NotEntitled for over an hour while B2 sat configured, and
/healthz said storage was fine). Simulated stores only: production is never
failed over to prove this."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import storage_health as sh  # noqa: E402
import storage_routing as routing  # noqa: E402
from ops_loop import gaps  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
UTC = timezone.utc
ENV = {"S3_ENDPOINT": "https://acct.r2.cloudflarestorage.com",
       "S3_ACCESS_KEY_ID": "a", "S3_SECRET_ACCESS_KEY": "b",
       "S3_BUCKET": "media",
       "S3_NEXT_ENDPOINT": "https://s3.us-west-004.backblazeb2.com",
       "S3_NEXT_ACCESS_KEY_ID": "c", "S3_NEXT_SECRET_ACCESS_KEY": "d",
       "S3_NEXT_BUCKET": "media-next"}


class ClientError(Exception):
    def __init__(self, code):
        super().__init__(f"An error occurred ({code})")
        self.response = {"Error": {"Code": code}}


class ReadTimeoutError(Exception):
    pass


class FakeStore:
    """put/head/get/delete/multipart/presign; ``fail`` makes every call
    raise that error (a code string or an exception)."""

    def __init__(self, fail=None):
        self.fail = fail
        self.objects = {}
        self.calls = []

    def _gate(self, op):
        self.calls.append(op)
        if self.fail is not None:
            raise (ClientError(self.fail) if isinstance(self.fail, str)
                   else self.fail)

    def put_object(self, Bucket, Key, Body, **kw):
        self._gate("put")
        self.objects[Key] = Body

    def head_object(self, Bucket, Key):
        self._gate("head")
        if Key not in self.objects:
            raise ClientError("404")
        return {"ContentLength": len(self.objects[Key])}

    def get_object(self, Bucket, Key, **kw):
        self._gate("get")
        if Key not in self.objects:
            raise ClientError("NoSuchKey")
        raise AssertionError("the probe key never exists")

    def delete_object(self, Bucket, Key):
        self._gate("delete")
        self.objects.pop(Key, None)

    def create_multipart_upload(self, Bucket, Key, **kw):
        self._gate("mp_create")
        return {"UploadId": "u1"}

    def abort_multipart_upload(self, Bucket, Key, UploadId):
        self._gate("mp_abort")

    def generate_presigned_url(self, op, Params, ExpiresIn):
        self._gate("presign")
        return "https://example.invalid/" + Params["Key"]


def _world(legacy_fail=None, next_fail=None, mode="legacy", row=None):
    stores = {"legacy": FakeStore(legacy_fail), "next": FakeStore(next_fail)}
    clock = [1_000_000.0]
    state = {"mode": mode,
             "row": row if row is not None else json.dumps({"mode": mode}),
             "alerts": []}

    def client_for(cfg):
        return stores["next" if cfg["name"] == "next" else "legacy"]

    prober = sh.Prober(env=ENV, mode=lambda: state["mode"],
                       client_for=client_for, every_s=60, write_every=5,
                       clock=lambda: clock[0], log=lambda m: None,
                       instance="test")

    def write_row(old, new):
        if state["row"] != old:
            return False
        state["row"] = new
        return True

    def probe_store(name):
        c, bucket, _ = prober.client(name)

        def http(method, url, data=None, headers=None, timeout=30):
            c.put_object(Bucket=bucket,
                         Key=url.split("example.invalid/", 1)[1], Body=data)
            return 200
        return sh.full_probe(c, bucket, http=http, clock=lambda: clock[0])

    fo = sh.Failover(prober, read_row=lambda: state["row"],
                     write_row=write_row,
                     alert=lambda s, h: state["alerts"].append(s),
                     probe_store=probe_store, clock=lambda: clock[0],
                     log=lambda m: None)
    prober.add_listener(fo)
    return prober, fo, stores, state, clock


def test_both_copies_are_byte_identical():
    assert (ROOT / "backend" / "storage_health.py").read_bytes() == \
        (ROOT / "worker" / "storage_health.py").read_bytes()


def test_provider_errors_are_classified():
    assert sh.classify(ClientError("NoSuchKey"))[1] == "missing"
    for code in ("NotEntitled", "AccessDenied", "InvalidAccessKeyId",
                 "NoSuchBucket"):
        assert sh.classify(ClientError(code)) == (code, "account")
    assert sh.classify(ReadTimeoutError("read timed out"))[1] == "timeout"
    assert sh.classify(ClientError("InternalError"))[1] == "other"


def test_failover_modes_never_touch_next_only():
    assert routing.failover_mode("legacy") == "next"
    assert routing.failover_mode("copy") == "next"
    assert routing.failover_mode("dual_write") == "next"
    assert routing.failover_mode("rollback") == "next"
    assert routing.failover_mode("next") == "rollback"
    assert routing.failover_mode("next_only") is None
    assert "next_only" not in routing.FAILOVER.values()
    for mode, target in routing.FAILOVER.items():
        assert routing.write_store(mode) != routing.write_store(target)


def test_health_reports_measured_stores_and_write_ok():
    prober, fo, stores, state, clock = _world(legacy_fail="NotEntitled",
                                              mode="next")
    st = prober.probe_once()
    assert st["write_store"] == "next" and st["write_ok"] is True
    assert st["stores"]["next"]["ok"] is True
    assert st["stores"]["next"]["op"] == "write"       # first probe writes
    leg = st["stores"]["legacy"]
    assert leg["ok"] is False and leg["code"] == "NotEntitled"
    assert leg["kind"] == "account" and leg["fails"] == 1
    assert leg["host"] == "acct.r2.cloudflarestorage.com"
    # the write store is round-tripped every 5th probe, read otherwise
    clock[0] += 60
    st = prober.probe_once()
    assert st["stores"]["next"]["op"] == "read"
    assert stores["next"].objects == {}                  # nothing left behind
    # a reading store whose mode does not write to it gets no failover
    assert fo.last is None and state["row"] == json.dumps({"mode": "next"})


def test_r2_not_entitled_flips_legacy_to_next_after_two_probes():
    prober, fo, stores, state, clock = _world(legacy_fail="NotEntitled")
    prober.probe_once()
    assert fo.last is None                               # one probe: no flip
    clock[0] += 60
    prober.probe_once()
    assert fo.last["action"] == "flipped"
    row = json.loads(state["row"])
    assert row["mode"] == "next" and row["previous"] == "legacy"
    assert row["by"] == routing.AUTO_BY and row["failed_store"] == "legacy"
    assert "NotEntitled" in row["note"] and "Never undone" in row["note"]
    assert len(state["alerts"]) == 1 and "legacy -> next" in state["alerts"][0]
    ops = stores["next"].calls
    assert {"put", "head", "mp_create", "mp_abort", "presign",
            "delete"} <= set(ops)
    assert not [k for k in stores["next"].objects if not
                k.startswith(sh.PROBE_PREFIX)] and not stores["next"].objects


def test_a_failing_next_store_rolls_back_to_r2_and_never_flips_again():
    prober, fo, stores, state, clock = _world(next_fail="InvalidAccessKeyId",
                                              mode="next")
    for _ in range(2):
        prober.probe_once()
        clock[0] += 60
    assert fo.last["action"] == "flipped"
    assert json.loads(state["row"])["mode"] == "rollback"
    # now R2 fails too while the row is the automatic one: held, not flipped
    state["mode"] = "rollback"
    stores["legacy"].fail = "NotEntitled"
    stores["next"].fail = None
    before = state["row"]
    for _ in range(3):
        prober.probe_once()
        clock[0] += 400
    assert fo.last["action"] == "held" and state["row"] == before
    assert len(state["alerts"]) == 1


def test_no_flip_when_the_other_store_fails_its_full_probe():
    prober, fo, stores, state, clock = _world(legacy_fail="NotEntitled",
                                              next_fail="AccessDenied")
    for _ in range(2):
        prober.probe_once()
        clock[0] += 60
    assert fo.last["action"] == "other_failed"
    assert json.loads(state["row"])["mode"] == "legacy" and not state["alerts"]
    # it does not hammer the other store: one full probe per 5 minutes
    stores["next"].calls.clear()
    prober.probe_once()
    assert fo.last["action"] == "wait"


def test_timeouts_flip_only_after_five_in_a_row_and_errors_never():
    prober, fo, stores, state, clock = _world(
        legacy_fail=ReadTimeoutError("Read timeout on endpoint URL"))
    for i in range(4):
        prober.probe_once()
        clock[0] += 60
        assert fo.last is None, i
    prober.probe_once()
    assert fo.last["action"] == "flipped"
    prober, fo, stores, state, clock = _world(legacy_fail="InternalError")
    for _ in range(6):
        prober.probe_once()
        clock[0] += 60
    assert fo.last is None and json.loads(state["row"])["mode"] == "legacy"


def test_next_only_and_a_moved_row_are_left_alone():
    prober, fo, stores, state, clock = _world(next_fail="NotEntitled",
                                              mode="next_only")
    for _ in range(3):
        prober.probe_once()
    assert fo.last["action"] == "none"
    assert json.loads(state["row"]) == {"mode": "next_only"}
    prober, fo, stores, state, clock = _world(
        legacy_fail="NotEntitled", row=json.dumps({"mode": "next"}))
    for _ in range(3):
        prober.probe_once()
    assert fo.last["action"] == "none"
    assert json.loads(state["row"]) == {"mode": "next"}


# ── the intake's P0s ──────────────────────────────────────────────────────

class KvCur:
    def __init__(self, kv):
        self.kv = kv
        self._row = None

    def execute(self, sql, params=()):
        assert sql.strip().startswith("SELECT value FROM app_kv")
        v = self.kv.get(params[0])
        self._row = {"value": v} if v is not None else None

    def fetchone(self):
        return self._row


NOW = datetime(2026, 11, 12, 17, 0, tzinfo=UTC)


def test_the_intake_files_a_p0_when_the_write_store_fails_twice(monkeypatch):
    prober, *_ = _world(legacy_fail="NotEntitled")
    prober._listeners.clear()
    monkeypatch.setattr(gaps, "_store_health", prober.status)
    cur = KvCur({})
    prober.probe_once()
    assert gaps._storage_write_failing(cur, NOW - timedelta(minutes=1), NOW,
                                       {}) == []
    prober.probe_once()
    got = gaps._storage_write_failing(cur, NOW - timedelta(minutes=1), NOW, {})
    assert len(got) == 1 and got[0]["severity"] == "P0"
    assert got[0]["cause_key"] == "auto-gap-storage-write-failing-legacy"
    assert "NotEntitled" in got[0]["title"]


def test_the_intake_files_the_automatic_failover_on_the_same_cause(
        monkeypatch):
    monkeypatch.setattr(gaps, "_store_health", lambda: {})
    row = {"mode": "next", "previous": "legacy", "by": routing.AUTO_BY,
           "failed_store": "legacy", "set_at": "2026-11-12T16:59:30Z",
           "note": "automatic failover: legacy store NotEntitled"}
    cur = KvCur({"storage_routing": json.dumps(row)})
    got = gaps._storage_write_failing(cur, NOW - timedelta(minutes=1), NOW, {})
    assert len(got) == 1 and got[0]["severity"] == "P0"
    assert got[0]["cause_key"] == "auto-gap-storage-write-failing-legacy"
    assert "legacy -> next" in got[0]["title"]
    # outside the window: already filed
    assert gaps._storage_write_failing(cur, NOW, NOW + timedelta(minutes=1),
                                       {}) == []


def test_the_intake_files_a_p0_when_the_claude_lane_is_off_15_min():
    def state(reason, since):
        return KvCur({"claude_credit_state": json.dumps(
            {"state": "off", "reason": reason, "since": since.isoformat()})})

    since = NOW - timedelta(minutes=16)
    got = gaps._claude_lane_off(state("billing_error", since),
                                NOW - timedelta(minutes=1), NOW, {})
    assert len(got) == 1 and got[0]["severity"] == "P0"
    assert got[0]["title"] == \
        "Claude lane off: every new trial meets the paywall"
    assert gaps._claude_lane_off(state("billing_error",
                                       NOW - timedelta(minutes=5)),
                                 NOW - timedelta(minutes=1), NOW, {}) == []
    assert gaps._claude_lane_off(state("budget", since),
                                 NOW - timedelta(minutes=1), NOW, {}) == []
    on = KvCur({"claude_credit_state": json.dumps({"state": "on"})})
    assert gaps._claude_lane_off(on, NOW - timedelta(minutes=1), NOW, {}) == []
    assert gaps._storage_write_failing in gaps.CHECKS
    assert gaps._claude_lane_off in gaps.CHECKS


def test_the_cloudflare_deploy_is_manual_and_the_worker_suite_runs_on_push():
    wf = (ROOT / ".github" / "workflows"
          / "deploy-cloudflare-executor.yml").read_text()
    on = wf.split("\non:", 1)[1].split("\nconcurrency:", 1)[0]
    assert "workflow_dispatch" in on and "push:" not in on
    ce = (ROOT / ".github" / "workflows" / "check-editor.yml").read_text()
    on = ce.split("\non:", 1)[1].split("\npermissions:", 1)[0]
    assert "push:" in on and "worker/**" in on
    assert "python -m pytest -q '${{ matrix.service }}/tests'" in ce


def test_a_pass_that_never_ends_says_where_it_is_stuck():
    prober, *_ = _world(mode="next")
    clock = [5000.0]
    prober._clock = lambda: clock[0]
    st = prober.status()
    assert st["passes"] == 0 and st["pid"] == os.getpid()
    assert "stalled_s" not in st
    prober._pass_started = 4000.0
    st = prober.status()
    assert st["stalled_s"] == 1000 and "stack" in st
