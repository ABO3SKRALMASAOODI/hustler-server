"""The weekly fallback canary (PLAN.md C2.9): one trivial Modal job, one
app_kv row, ok only on this release's exact code."""
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "fallback_canary", HERE.parent / "scripts" / "fallback_canary.py")
canary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(canary)

NOW = datetime(2026, 10, 26, 5, 17, tzinfo=timezone.utc)


class Call:
    def __init__(self, out=None, error=None):
        self.out, self.error = out, error
        self.timeout = None

    def get(self, timeout=None):
        self.timeout = timeout
        if self.error:
            raise self.error
        return self.out


class Function:
    def __init__(self, call):
        self.call, self.inputs = call, []

    def spawn(self, payload):
        self.inputs.append(payload)
        return self.call


def ticks(*values):
    it = iter(values)
    return lambda: next(it)


def warm(code="c" * 12, adapter="a" * 12, warmed=True):
    return {"result": {"warmed": warmed, "profile": "probe",
                       "code_version": code, "adapter_version": adapter,
                       "ffmpeg": "ffmpeg version n8.1",
                       "compute_region": "us-east"},
            "job_completed": False}


def test_a_matching_release_passes_and_the_job_is_only_a_warm_input():
    fn = Function(Call(warm()))
    out = canary.check("c" * 12, "a" * 12, function=fn, timeout_s=300,
                       clock=ticks(10.0, 52.4), now=NOW)
    assert fn.inputs == [{"type": "__warm"}]
    assert fn.call.timeout == 300
    assert out["ok"] is True and out["provider"] == "modal"
    assert out["checked_at"] == NOW.isoformat()
    assert out["elapsed_s"] == 42.4
    assert "on this release" in out["detail"]


def test_older_code_on_modal_fails_and_says_what_to_do():
    out = canary.check("c" * 12, "a" * 12,
                       function=Function(Call(warm(code="old000000000"))),
                       clock=ticks(0.0, 30.0), now=NOW)
    assert out["ok"] is False
    assert "old000000000" in out["detail"] and "c" * 12 in out["detail"]
    assert "deploy-modal-executor" in out["detail"]
    out = canary.check("c" * 12, "a" * 12,
                       function=Function(Call(warm(adapter="b" * 12))),
                       clock=ticks(0.0, 1.0), now=NOW)
    assert out["ok"] is False and "adapter" in out["detail"]


def test_an_unreachable_or_refusing_modal_is_a_failed_canary_not_a_crash():
    out = canary.check("c", "a", function=Function(Call(
        error=TimeoutError("no answer in 600 s"))),
        clock=ticks(0.0, 600.0), now=NOW)
    assert out["ok"] is False and "TimeoutError" in out["detail"]
    out = canary.check("c", "a", function=Function(Call(warm(warmed=False))),
                       clock=ticks(0.0, 1.0), now=NOW)
    assert out["ok"] is False and "did not run" in out["detail"]


def test_no_credentials_is_reported_without_calling_modal():
    out = canary.check("c", "a", has_credentials=False, now=NOW)
    assert out["ok"] is False and "credentials" in out["detail"]
    assert canary.credentials_present(
        env={"MODAL_TOKEN_ID": "x", "MODAL_TOKEN_SECRET": "y"},
        home="/nonexistent")
    assert not canary.credentials_present(env={}, home="/nonexistent")


def test_it_writes_only_its_own_app_kv_row():
    class Cur:
        def __init__(self):
            self.calls = []

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))

    class Conn:
        def __init__(self):
            self.cur, self.committed = Cur(), False

        def cursor(self):
            return self.cur

        def commit(self):
            self.committed = True

    conn = Conn()
    canary.save(conn, {"ok": True, "provider": "modal",
                       "checked_at": NOW.isoformat(), "detail": "fine"})
    (sql, params), = conn.cur.calls
    assert sql.startswith("INSERT INTO app_kv")
    assert params[0] == "fallback_canary" == canary.KV_KEY
    assert json.loads(params[1])["ok"] is True and conn.committed


def test_expected_versions_match_what_the_modal_deploy_verifies():
    import hashlib
    import subprocess
    code, adapter = canary.expected_versions()
    worker = HERE.parents[1] / "worker"
    # The deploy workflow's own computation: `cd worker && import version`.
    want = subprocess.check_output(
        [sys.executable, "-c", "import version; print(version.code_version())"],
        cwd=worker, text=True).strip()
    assert code == want and len(code) >= 12
    assert adapter == hashlib.sha256(
        (worker / "modal_app.py").read_bytes()).hexdigest()[:12]
    assert canary.ROOT / "worker" == worker


def test_the_board_reads_the_same_key():
    from inspect import getsource
    sys.path.insert(0, str(HERE.parent))
    from admin_metrics import cockpit
    assert '"fallback_canary"' in getsource(cockpit.trial_spend)


def test_the_schedule_is_weekly_and_runs_on_its_own_release():
    wf = (HERE.parents[1] / ".github" / "workflows"
          / "fallback-canary.yml").read_text()
    assert "cron:" in wf and "* * 1" in wf
    assert "backend/scripts/fallback_canary.py --write" in wf
    assert "secrets.MODAL_TOKEN_ID" in wf
    assert "secrets.PRODUCTION_DATABASE_URL" in wf
