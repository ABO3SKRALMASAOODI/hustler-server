"""The OpenAI credit gate: one latch for every OpenAI call this process makes.

15 Nov 2026: the OpenAI API credit ran out (HTTP 429 insufficient_quota, "You have no credits
remaining") and the owner will not refill it before 30 Nov. Every OpenAI role then failed on its
own: some fell back, some retried into the same refusal, one made the first film wait for good.

What this gives every call site:

* ``available()``: False for ``config.OPENAI_CREDIT_LATCH_S`` (15 min) after a credit refusal, so
  callers go straight to their fallback with no failed call in front. When the latch expires the
  next call is a real one: the re-probe. A refusal latches again; an answer means the credit is
  back and the latch stays open. The product returns to OpenAI by itself when it is refilled.
* ``note(exc_or_text, where, status=None)``: classify one failure. A credit refusal trips the
  latch and returns True. Rate limits, outages and bad requests are not credit and return False.
* one admin alert per outage month across every process (trial_alerts' dedupe key is shared by
  the backend and all box slots), never one per call.

It classifies the provider's own error body (insufficient_quota and its wordings); it never reads
what a customer asked.
"""
import threading
import time

import config

_lock = threading.Lock()
_closed_until = 0.0
_closed_reason = ""
_alerted = set()

# The provider's wordings for an empty wallet. 429 is also a rate limit, so the status alone is
# never enough: only these bodies latch.
CREDIT_MARKERS = (
    "insufficient_quota", "credit_balance_exhausted", "no credits remaining",
    "used all available credits", "exceeded your current quota", "billing_hard_limit",
    "billing hard limit", "credit balance is too low",
)


class CreditOut(RuntimeError):
    """OpenAI is out of credit (latched or just refused). The caller's fallback answers."""


def latch_s():
    return float(getattr(config, "OPENAI_CREDIT_LATCH_S", 900.0) or 900.0)


def is_credit_refusal(exc_or_text, status=None):
    if isinstance(exc_or_text, CreditOut):
        return True
    text = str(exc_or_text or "").lower()
    if not any(m in text for m in CREDIT_MARKERS):
        return False
    return status in (None, 402, 403, 429)


def available():
    return time.monotonic() >= _closed_until


def closed_reason():
    return _closed_reason if not available() else ""


def seconds_left():
    return max(0.0, _closed_until - time.monotonic())


def alert_key():
    return "openai_credit_out:" + time.strftime("%Y-%m", time.gmtime())


def _alert(where, detail):
    key = alert_key()
    if key in _alerted:
        return
    _alerted.add(key)
    try:
        import claude_trial
        claude_trial._alert(
            key, "openai_credit",
            "OpenAI API credit is out: fallbacks are answering",
            "<p>OpenAI refused a call for credit "
            f"(first seen in <b>{where}</b>). Every OpenAI role now runs its fallback: transcription on "
            "the box's local faster-whisper, picture checks on Haiku where a wrong picture could "
            "reach a customer, optional checks skipped, the concierge and greetings on Haiku or "
            "their plain reply, checkout never blocked. OpenAI is re-tried every "
            f"{int(latch_s() // 60)} minutes and comes back by itself when the credit is refilled."
            f"</p><p>Provider said: {str(detail)[:300]}</p>")
    except Exception:  # noqa: BLE001 - an alert never breaks a call
        pass


def trip(where, detail=""):
    """Latch OpenAI shut for this process and alert once."""
    global _closed_until, _closed_reason
    with _lock:
        _closed_until = time.monotonic() + latch_s()
        _closed_reason = f"{where}: {str(detail)[:240]}"
    print(f"[openai-credit] {where}: OpenAI refused for credit; fallbacks for "
          f"{int(latch_s())} s: {str(detail)[:160]}", flush=True)
    _alert(where, detail)


def note(exc_or_text, where, status=None):
    """A failed OpenAI call. Returns True (and trips) when it was a credit refusal."""
    if isinstance(exc_or_text, CreditOut):
        return True
    if status is None:
        status = getattr(exc_or_text, "status_code", None) or getattr(
            getattr(exc_or_text, "response", None), "status_code", None)
    if not is_credit_refusal(exc_or_text, status):
        return False
    trip(where, exc_or_text)
    return True


def check(where):
    """Raise CreditOut while latched: the call is not sent."""
    if not available():
        raise CreditOut(f"OpenAI credit out (latched {int(seconds_left())} s more; "
                        f"{closed_reason()[:160]})")


def reopen():
    """Tests/operators: clear the latch."""
    global _closed_until, _closed_reason
    with _lock:
        _closed_until, _closed_reason = 0.0, ""
    _alerted.clear()


def status():
    return {"open": available(), "closed_reason": closed_reason() or None,
            "seconds_left": round(seconds_left()), "latch_s": latch_s()}


__all__ = ["CreditOut", "available", "check", "note", "trip", "reopen", "status",
           "is_credit_refusal", "closed_reason"]
