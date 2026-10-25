"""Weekly fallback canary (strategy 1025, PLAN.md C2.9 and REDTEAM §1).

Question: can the Modal fallback run a trivial job at the current code
version? Modal has been idle since 30 Aug 2026 and is deployed only by the
operator-invoked deploy-modal-executor workflow, so the answer can drift
from "yes" without anyone noticing. Modal is added back as a fallback only
after this passes.

What it does, once, with no customer impact:
  * computes this checkout's worker code fingerprint (worker/version.py) and
    Modal adapter fingerprint (sha256 of worker/modal_app.py), exactly as the
    Modal deploy workflow verifies a release;
  * runs ``{"type": "__warm"}`` on the deployed ``probe`` function (0.25-0.5
    CPU, scale-to-zero). That input boots the real executor image, imports
    the executor and returns its fingerprints and runner set before any
    database, storage or queue work: no job row, no lease, no customer data;
  * writes app_kv ``fallback_canary`` = {ok, provider, checked_at, detail,
    ...} (the only row it ever writes); the Health board reads it.

ok is true only when the job ran AND both fingerprints match this release.

    # what the scheduled workflow runs (.github/workflows/fallback-canary.yml)
    python backend/scripts/fallback_canary.py --write
    # anywhere, nothing written:
    python backend/scripts/fallback_canary.py
"""
import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "worker"
KV_KEY = "fallback_canary"
PROVIDER = "modal"
FUNCTION = "probe"
MODAL_APP = os.getenv("MODAL_EXECUTOR_APP", "valmera-executor")
MODAL_ENV = os.getenv("MODAL_EXECUTOR_ENVIRONMENT", "main")
TIMEOUT_S = 600


def _now():
    return dt.datetime.now(dt.timezone.utc)


def expected_versions(worker_dir=WORKER):
    """(code_version, adapter_version) of this checkout, as Modal reports
    them: version.code_version() over the worker source and the first 12 hex
    of sha256(modal_app.py)."""
    spec = importlib.util.spec_from_file_location(
        "fallback_canary_worker_version", Path(worker_dir) / "version.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    adapter = hashlib.sha256(
        (Path(worker_dir) / "modal_app.py").read_bytes()).hexdigest()[:12]
    return module.code_version(), adapter


def credentials_present(env=None, home=None):
    env = os.environ if env is None else env
    home = Path.home() if home is None else Path(home)
    return bool(env.get("MODAL_TOKEN_ID") and env.get("MODAL_TOKEN_SECRET")) \
        or (home / ".modal.toml").exists()


def _lookup():
    import modal
    return modal.Function.from_name(MODAL_APP, FUNCTION,
                                    environment_name=MODAL_ENV)


def check(want_code, want_adapter, function=None, timeout_s=TIMEOUT_S,
          clock=time.monotonic, now=None, has_credentials=None):
    """Run the trivial job once and say what happened. Never raises."""
    result = {"ok": False, "provider": PROVIDER,
              "checked_at": (now or _now()).isoformat(),
              "function": f"{MODAL_APP}/{FUNCTION}",
              "expected_code_version": want_code,
              "expected_adapter_version": want_adapter}
    if function is None:
        if not (credentials_present() if has_credentials is None
                else has_credentials):
            result["detail"] = ("no Modal credentials where the canary runs "
                                "(MODAL_TOKEN_ID / MODAL_TOKEN_SECRET)")
            return result
        try:
            function = _lookup()
        except Exception as exc:  # noqa: BLE001 — the answer, not a crash
            result["detail"] = (f"Modal function lookup failed: "
                                f"{type(exc).__name__}: {str(exc)[:240]}")
            return result
    started = clock()
    try:
        call = function.spawn({"type": "__warm"})
        out = call.get(timeout=timeout_s)
    except Exception as exc:  # noqa: BLE001
        result["elapsed_s"] = round(clock() - started, 1)
        result["detail"] = (f"Modal could not run the trivial job: "
                            f"{type(exc).__name__}: {str(exc)[:240]}")
        return result
    elapsed = round(clock() - started, 1)
    report = (out or {}).get("result") if isinstance(out, dict) else None
    report = report if isinstance(report, dict) else {}
    seen_code = report.get("code_version")
    seen_adapter = report.get("adapter_version")
    result.update({"elapsed_s": elapsed, "code_version": seen_code,
                   "adapter_version": seen_adapter,
                   "profile": report.get("profile"),
                   "ffmpeg": report.get("ffmpeg"),
                   "compute_region": report.get("compute_region")})
    redeploy = ("; redeploy Modal (workflow deploy-modal-executor) before "
                "it is used as a fallback")
    if report.get("warmed") is not True:
        result["detail"] = ("Modal answered but did not run the job: "
                            + json.dumps(out, default=str)[:240])
    elif seen_code != want_code:
        result["detail"] = (f"Modal ran the job in {elapsed:.0f} s on code "
                            f"{seen_code}, this release is {want_code}"
                            + redeploy)
    elif seen_adapter != want_adapter:
        result["detail"] = (f"Modal ran the job in {elapsed:.0f} s on this "
                            f"code but adapter {seen_adapter}, this release "
                            f"is {want_adapter}" + redeploy)
    else:
        result["ok"] = True
        result["detail"] = (f"Modal ran a trivial job on this release "
                            f"({want_code}) in {elapsed:.0f} s")
    return result


UPSERT_SQL = """INSERT INTO app_kv (key, value, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (key) DO UPDATE
                  SET value = EXCLUDED.value, updated_at = NOW()"""


def save(conn, result):
    """Write the one app_kv row this canary owns."""
    with conn.cursor() as cur:
        cur.execute(UPSERT_SQL, (KV_KEY, json.dumps(result, default=str)))
    conn.commit()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--write", action="store_true",
                        help="record the result in app_kv fallback_canary")
    parser.add_argument("--database-env", default="PRODUCTION_DATABASE_URL",
                        help="environment variable holding the DSN")
    parser.add_argument("--timeout", type=float, default=TIMEOUT_S)
    args = parser.parse_args(argv)
    want_code, want_adapter = expected_versions()
    result = check(want_code, want_adapter, timeout_s=args.timeout)
    print(json.dumps(result, indent=2, default=str))
    if not result["ok"]:
        print(f"::warning::fallback canary: {result['detail']}")
    if args.write:
        dsn = os.environ.get(args.database_env) or ""
        if not dsn:
            print(f"::error::{args.database_env} is not set; nothing recorded")
            return 2
        import psycopg2
        conn = psycopg2.connect(dsn, connect_timeout=20)
        try:
            save(conn, result)
        finally:
            conn.close()
        print(f"recorded app_kv {KV_KEY}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
