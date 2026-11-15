"""The backend's OpenAI credit gate (the worker's twin: worker/openai_credit.py).

15 Nov 2026: the OpenAI API credit ran out (HTTP 429 insufficient_quota). The backend's OpenAI
roles are the concierge (Claude answers when OpenAI cannot) and the checkout readiness probe
(which must never fail a checkout over OpenAI: customers edit on Haiku, and the edit rung falls
back to Haiku on any OpenAI error). After a credit refusal this process skips OpenAI for
OPENAI_CREDIT_LATCH_S (15 min), then one real call re-probes, so OpenAI returns by itself when the
credit is refilled. One admin alert per outage month, shared with every worker process through
trial_alerts' dedupe key; never one per call. It classifies the provider's error body only.
"""
import os
import threading
import time

_lock = threading.Lock()
_closed_until = 0.0
_closed_reason = ""
_alerted = set()

CREDIT_MARKERS = (
    "insufficient_quota", "credit_balance_exhausted", "no credits remaining",
    "used all available credits", "exceeded your current quota", "billing_hard_limit",
    "billing hard limit", "credit balance is too low",
)


def latch_s():
    try:
        return float(os.getenv("OPENAI_CREDIT_LATCH_S", "900") or 900)
    except ValueError:
        return 900.0


def is_credit_refusal(text, status=None):
    low = str(text or "").lower()
    return any(m in low for m in CREDIT_MARKERS) and status in (None, 402, 403, 429)


def available():
    return time.monotonic() >= _closed_until


def closed_reason():
    return _closed_reason if not available() else ""


def alert_key():
    return "openai_credit_out:" + time.strftime("%Y-%m", time.gmtime())


def _alert(where, detail):
    key = alert_key()
    if key in _alerted:
        return
    _alerted.add(key)
    try:
        from claude_trial import _connect
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.trial_alerts') AS t")
                row = cur.fetchone()
                if not (row.get("t") if isinstance(row, dict) else row[0]):
                    return
                cur.execute(
                    """INSERT INTO trial_alerts (dedupe_key, kind, subject, html_content)
                       VALUES (%s, %s, %s, %s) ON CONFLICT (dedupe_key) DO NOTHING""",
                    (key, "openai_credit", "OpenAI API credit is out: fallbacks are answering",
                     "<p>OpenAI refused a call for credit (first seen in <b>%s</b>). Every OpenAI "
                     "role now runs its fallback: the concierge on Haiku, checkout never "
                     "blocked, transcription on the box's local faster-whisper, picture checks on "
                     "Haiku where a wrong picture could reach a customer, optional checks "
                     "skipped. OpenAI is re-tried every %d minutes and comes back by itself when "
                     "the credit is refilled.</p><p>Provider said: %s</p>"
                     % (where, int(latch_s() // 60), str(detail)[:300].replace("<", "&lt;"))))
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - an alert never breaks a request
        print(f"[openai-credit] alert not queued: {type(exc).__name__}", flush=True)


def trip(where, detail=""):
    global _closed_until, _closed_reason
    with _lock:
        _closed_until = time.monotonic() + latch_s()
        _closed_reason = f"{where}: {str(detail)[:240]}"
    print(f"[openai-credit] {where}: OpenAI refused for credit; skipped for "
          f"{int(latch_s())} s", flush=True)
    _alert(where, detail)


def note(text, where, status=None):
    """A failed OpenAI call: True (and the latch shut) when it was a credit refusal."""
    if status is None:
        status = getattr(text, "status_code", None)
    if not is_credit_refusal(text, status):
        return False
    trip(where, text)
    return True


def reopen():
    global _closed_until, _closed_reason
    with _lock:
        _closed_until, _closed_reason = 0.0, ""
    _alerted.clear()
