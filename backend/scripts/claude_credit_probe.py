"""The automatic credit switch's probe (backend/claude_credit.py).

Run every 3 hours by .github/workflows/claude-credit-probe.yml with the
credited Anthropic organisation's key (GitHub secret ANTHROPIC_API_KEY) and
the production database (PRODUCTION_DATABASE_URL). It writes ONLY the
code-owned app_kv row ``claude_credit_state`` and, on a switch, one admin
alert row in ``trial_alerts``.

* Switch ON: no API call. If the cycle's recorded Claude spend is already
  past the line (budget - safety margin), it switches OFF (belt and braces
  behind the worker's own check).
* Switch OFF: one minimal Messages call (Claude Haiku 5.5, a few tokens,
  well under $0.0001). It switches back ON when the call succeeds in a NEW
  billing cycle, or, when it went off on a key (auth) refusal, as soon as a
  call succeeds and the cycle is under the line. Otherwise it records the
  probe and stays off.

Never prints the key or the database URL.

    python backend/scripts/claude_credit_probe.py            # act
    python backend/scripts/claude_credit_probe.py --dry-run  # decide only
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import claude_credit  # noqa: E402

PROBE_MODEL = os.getenv("CLAUDE_CREDIT_PROBE_MODEL", "claude-haiku-5-5")


def _scrub(text):
    return re.sub(r"sk-ant-[A-Za-z0-9_\-]+", "[REDACTED]", str(text))[:300]


def probe_call(api_key):
    """(ok, error_text). One tiny call; nothing is retried."""
    try:
        import anthropic
    except Exception as exc:  # pragma: no cover - the workflow installs it
        return False, f"anthropic SDK missing: {exc}"
    try:
        client = anthropic.Anthropic(api_key=api_key, max_retries=0,
                                     timeout=60)
        # The smallest billable request: a few input tokens, a 16-token
        # ceiling, no optional parameters (any SDK that can reach the API
        # can send it). Any 2xx answer proves the key and the balance.
        msg = client.messages.create(
            model=PROBE_MODEL, max_tokens=16,
            messages=[{"role": "user", "content": "Reply with OK."}])
        return True, f"stop_reason={getattr(msg, 'stop_reason', None)}"
    except Exception as exc:  # noqa: BLE001 - every failure is a result
        status = getattr(exc, "status_code", None)
        return False, _scrub(f"{type(exc).__name__} {status}: {exc}")


def run(conn, api_key, dry_run=False, call=probe_call, now=None):
    plan = claude_credit.probe_plan(conn, now=now)
    out = {"plan": {k: (round(v, 4) if isinstance(v, float) else v)
                    for k, v in plan.items()}}
    if plan["action"] == "none":
        out["result"] = "nothing to do"
        return out
    if plan["action"] == "enforce_line":
        if dry_run:
            out["result"] = "would switch off (budget)"
            return out
        switched = claude_credit.switch_off(conn, claude_credit.REASON_BUDGET,
                                            now=now)
        conn.commit()
        out["result"] = "switched off (budget)" if switched else "already off"
        return out
    if not api_key:
        out["result"] = "no ANTHROPIC_API_KEY: cannot probe"
        return out
    if dry_run:
        out["result"] = ("would probe; would switch on if it succeeds"
                         if plan.get("may_switch_on")
                         else "would probe; stays off (same cycle)")
        return out
    ok, error = call(api_key)
    out["probe_ok"] = ok
    if not ok:
        out["probe_error"] = error
    out["result"] = claude_credit.after_probe(conn, plan, ok,
                                              "" if ok else error, now=now)
    conn.commit()
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    dsn = (os.environ.get("PRODUCTION_DATABASE_URL")
           or os.environ.get("DATABASE_URL") or "")
    if not dsn:
        print("no PRODUCTION_DATABASE_URL / DATABASE_URL", file=sys.stderr)
        return 2
    import psycopg2
    from psycopg2.extras import RealDictCursor
    conn = psycopg2.connect(dsn, cursor_factory=RealDictCursor,
                            connect_timeout=15,
                            options="-c statement_timeout=30000")
    try:
        if args.dry_run:
            conn.set_session(readonly=True)
        out = run(conn, os.environ.get("ANTHROPIC_API_KEY", "").strip(),
                  dry_run=args.dry_run)
    finally:
        conn.close()
    print(json.dumps(out, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
