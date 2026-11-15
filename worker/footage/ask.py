"""One model call for the footage engine: OpenAI through score.call's seam (call_openai), metered.

MODEL is gpt-6-sol at low effort: lane Q's skeptic (week-1112/lanes/Q/skeptic.json) found it safe,
and better than Opus, for picture checks. FOOTAGE_MODEL overrides it for a replay.
Paid from the OpenAI credit until 30 Nov 2026, OpenAI money after that.
"""
import base64
import json
import os
import time
import threading

MODEL = os.environ.get("FOOTAGE_MODEL", "gpt-6-sol")
EFFORT = os.environ.get("FOOTAGE_EFFORT", "low")
_LOCAL_CALL = threading.BoundedSemaphore(1)


def ask(system, content, purpose, budget_s=120.0, max_tokens=6000, model=None):
    if os.environ.get('CODEX_PROXY_LOCAL') == '1':
        # The adapter is shared with other lanes. Start the call's own deadline
        # after admission rather than queuing many calls that expire together.
        with _LOCAL_CALL:
            return _ask(system,content,purpose,budget_s,max_tokens,model)
    return _ask(system,content,purpose,budget_s,max_tokens,model)


def _ask(system, content, purpose, budget_s=120.0, max_tokens=6000, model=None):
    """(text, usd). content: text or Claude-shaped blocks (text and base64 images)."""
    from score import call
    tries = 1 if os.environ.get("CODEX_PROXY_LOCAL") == "1" else 2
    # Buffered local SSE has no early token stream. Allow the adapter's full
    # 180-second response window, while the planner bounds individual inputs.
    if os.environ.get('CODEX_PROXY_LOCAL')=='1':budget_s=max(budget_s,185.)
    deadline = time.monotonic() + budget_s
    for k in range(tries):
        if time.monotonic() >= deadline:
            raise TimeoutError("Footage model budget exhausted")
        try:
            r = call.stream(model or MODEL, system, content, max_tokens=max_tokens, effort=EFFORT, purpose=purpose,
                            deadline=deadline, thinking=False)
            return r.text, float(r.usd or 0.0)
        except call.Refused:
            raise                # out of credit or a refusal: a retry would hear the same answer
        except Exception:  # noqa: BLE001 one retry, then the caller's honest fallback
            if k == tries - 1 or deadline - time.monotonic() <= 3.0:
                raise
            time.sleep(3.0)


def image_block(path):
    with open(path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}


def json_obj(text):
    """The first JSON object in an answer, or {}."""
    s = str(text or "")
    i = s.find("{")
    dec = json.JSONDecoder()
    while i >= 0:
        try:
            obj, _ = dec.raw_decode(s, i)
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
        i = s.find("{", i + 1)
    return {}
