"""The model call beneath the loop: a chain of lanes the model never sees.

The lanes are the ones the turn was given (``ctx.agent_lanes``: the trial or
paid Claude lane, then the account's own lanes, already filtered by
``llm.editing_lanes``). A lane is left for the next one on a billing, auth,
quota, overload, 5xx or timeout error (today's loop switches only on
insufficient_quota). A billing or auth refusal latches that lane closed for
``LATCH_S``; the first call after the latch is the probe. A rate limit waits
on the same lane (``llm.rate_limit_wait``); a dialect error is adapted once
(``llm.adapt_completion_kwargs``). When every lane is down the call raises
``AllLanesDown`` and the loop parks the request.
"""
import threading
import time

import claude_lane
import config
import llm

LATCH_S = 300.0
TRANSIENT_RETRIES = 2
_LATCH = {}
_LATCH_LOCK = threading.Lock()

_BILLING_WORDS = ("credit balance", "insufficient", "quota", "billing",
                  "spending limit", "payment required", "used all available")
_AUTH_WORDS = ("invalid api key", "invalid x-api-key", "authentication",
               "unauthorized", "permission")
_OVERLOAD_WORDS = ("overloaded", "service unavailable", "bad gateway",
                   "gateway timeout", "internal server error", "timed out",
                   "timeout", "connection reset", "connection error")


class AllLanesDown(Exception):
    def __init__(self, reasons):
        super().__init__("; ".join(reasons)[:600])
        self.reasons = list(reasons)


def classify(exc):
    """rate_limit | billing | auth | transient | dialect | other."""
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    try:
        status = int(status) if status is not None else None
    except (TypeError, ValueError):
        status = None
    text = f"{type(exc).__name__}: {exc}".lower()
    try:
        if llm.is_quota_error(exc):
            return "billing"
    except Exception:  # noqa: BLE001
        pass
    if status == 402 or any(w in text for w in _BILLING_WORDS):
        return "billing"
    if status in (401, 403) or any(w in text for w in _AUTH_WORDS):
        return "auth"
    try:
        if llm.is_rate_limit_error(exc):
            return "rate_limit"
    except Exception:  # noqa: BLE001
        pass
    if status == 429:
        return "rate_limit"
    if (status is not None and (status >= 500 or status == 408)) or \
            "timeout" in type(exc).__name__.lower() or \
            any(w in text for w in _OVERLOAD_WORDS):
        return "transient"
    if status == 400 or status == 422:
        return "dialect"
    return "other"


def _lane_key(lane):
    return f"{lane.get('name') or ''}|{lane.get('model') or ''}|" \
           f"{lane.get('base_url') or ''}"


def latched(lane):
    with _LATCH_LOCK:
        until = _LATCH.get(_lane_key(lane))
    return until is not None and time.monotonic() < until


def latch(lane, seconds=LATCH_S):
    with _LATCH_LOCK:
        _LATCH[_lane_key(lane)] = time.monotonic() + seconds


def reopen(lane=None):
    with _LATCH_LOCK:
        if lane is None:
            _LATCH.clear()
        else:
            _LATCH.pop(_lane_key(lane), None)


def _strip_images(messages):
    removed = 0
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            kept = [p for p in content if not (
                isinstance(p, dict) and p.get("type") == "image_url")]
            removed += len(content) - len(kept)
            m["content"] = kept or [{"type": "text", "text": "[images]"}]
    return removed


def call(lanes, messages, tools, max_tokens=None, sleep=None,
         seconds_left=None, on_switch=None):
    """(response, lane, switches). Raises AllLanesDown."""
    sleep = sleep or time.sleep
    reasons, switches = [], 0
    for index, lane in enumerate(lanes or []):
        if latched(lane):
            reasons.append(f"{lane.get('name') or lane.get('model')}: "
                           "latched closed")
            continue
        model = lane["model"]
        client = llm.without_sdk_retries(lane["client"])
        kw = llm.completion_kwargs(model, max_tokens, config.AGENT_TEMPERATURE)
        if claude_lane.is_claude_model(model):
            kw.pop("reasoning_effort", None)
        transient, waits, adapted = 0, 0, 0
        while True:
            try:
                if not claude_lane.is_claude_model(model):
                    llm.fence(model)
                import openai_budget
                resp = openai_budget.completion(
                    client, lane=llm.spend_lane(), model=model,
                    messages=messages, tools=tools, **kw)
                return resp, lane, switches
            except Exception as exc:  # noqa: BLE001 - classified below
                kind = classify(exc)
                if kind == "rate_limit":
                    left = seconds_left() if seconds_left else 300.0
                    wait = llm.rate_limit_wait(exc, waits + 1, left)
                    if wait is not None:
                        waits += 1
                        sleep(wait)
                        continue
                    kind = "transient"
                if kind == "dialect" and adapted < 3:
                    new_kw = llm.adapt_completion_kwargs(exc, model, kw)
                    if new_kw is not None:
                        kw, adapted = new_kw, adapted + 1
                        continue
                    if llm.looks_like_blind_model(exc) and \
                            _strip_images(messages):
                        llm.mark_agent_blind(model)
                        adapted += 1
                        continue
                if kind == "transient" and transient < TRANSIENT_RETRIES:
                    transient += 1
                    sleep(min(4.0, 1.5 * transient))
                    continue
                if kind in ("billing", "auth"):
                    latch(lane)
                    try:
                        llm.note_quota_error(exc, model,
                                             lane.get("base_url"))
                    except Exception:  # noqa: BLE001
                        pass
                reasons.append(f"{lane.get('name') or model}: {kind} "
                               f"({str(exc)[:160]})")
                llm.record("agent", {"model": model, "harness": "v2"},
                           {"error": f"{type(exc).__name__}: "
                                     f"{str(exc)[:400]}"}, None)
                if kind == "other" or (kind == "dialect"
                                       and index + 1 >= len(lanes)):
                    raise
                switches += 1
                if on_switch:
                    on_switch(lane, kind)
                break
    raise AllLanesDown(reasons or ["no lane may edit this turn"])
