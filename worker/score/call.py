"""One streamed Claude call for a film: fenced, deadlined and metered like every trial call.

No tools (Director DESIGN 3.4). The deadline is hard: a stalled stream can't hold a customer's job,
because the read runs on its own thread and is closed when time is up. The tokens a closed stream
already bought are estimated and metered, so a late call still shows in the pool's ledger.
"""
import threading
import time

import claude_lane
import claude_trial
import llm

THINKING = {"type": "adaptive"}
OUT_TOKENS_PER_S = 130.0        # the slice's streams: 13,000 tokens in 100 s


class Late(Exception):
    """The deadline passed before the answer finished."""


class Refused(Exception):
    """The model refused, or the lane is closed."""


class Result:
    def __init__(self, text, usage, stop, first_s, seconds, usd, request_id=None):
        self.text, self.usage, self.stop = text, usage, stop
        self.first_s, self.seconds, self.usd, self.request_id = first_s, seconds, usd, request_id


def _meter(model, usd):
    if claude_trial.current() is not None:
        claude_trial.meter(model, usd)


def _late_cost(model, snap, elapsed):
    """What a closed stream cost: its real input, and its output by the clock."""
    raw = getattr(snap, "usage", None)
    out = int(elapsed * OUT_TOKENS_PER_S)

    class U:
        input_tokens = int(getattr(raw, "input_tokens", 0) or 0)
        cache_read_input_tokens = int(getattr(raw, "cache_read_input_tokens", 0) or 0)
        cache_creation_input_tokens = int(getattr(raw, "cache_creation_input_tokens", 0) or 0)
        output_tokens = out
        service_tier = None
    return claude_lane._Usage(model, U)


def stream(model, system, content, *, max_tokens, effort, purpose, deadline, on_text=None, thinking=True, first_text_by=None):
    """deadline is time.monotonic(). A call that has written nothing by first_text_by is late already:
    in the slice, a Score took 12-40 s to write after its first line. Returns a Result. Raises Late,
    Refused, or the provider's error."""
    if not claude_lane.configured():
        raise Refused("the Claude lane has no key")
    if not claude_lane.available():
        raise Refused("the Claude lane is closed: " + claude_lane.closed_reason())
    claude_trial.guard_call()
    body = {"model": model, "max_tokens": int(max_tokens), "output_config": {"effort": effort}, "system": system,
            "messages": [{"role": "user", "content": content}]}
    if thinking:
        body["thinking"] = THINKING
    box = {}
    t0 = time.monotonic()

    def read():
        try:
            client = claude_lane._sdk().with_options(timeout=max(5.0, deadline - t0 + 5.0), max_retries=0)
            with client.messages.stream(**body) as st:
                box["stream"] = st
                for delta in st.text_stream:
                    if box.get("cancel"):
                        return
                    if "first" not in box:
                        box["first"] = time.monotonic() - t0
                    box.setdefault("text", []).append(delta)
                    if on_text:
                        on_text(delta)
                box["final"] = st.get_final_message()
                box["request_id"] = getattr(st, "request_id", None)
        except BaseException as exc:  # noqa: BLE001 - carried to the caller's thread
            box["error"] = exc
    th = threading.Thread(target=read, name=f"film-{purpose}", daemon=True)
    th.start()
    if first_text_by is not None and first_text_by < deadline:
        while th.is_alive() and "first" not in box and time.monotonic() < first_text_by:
            th.join(min(0.5, max(0.0, first_text_by - time.monotonic())))
        if th.is_alive() and "first" not in box:
            deadline = time.monotonic()
    th.join(max(0.0, deadline - time.monotonic()))
    if th.is_alive():
        box["cancel"] = True
        st = box.get("stream")
        elapsed = time.monotonic() - t0
        if st is not None:
            usage = _late_cost(model, getattr(st, "current_message_snapshot", None), elapsed)
            usd = float(usage.provider_cost_usd)
            try:
                st.close()
            except Exception:  # noqa: BLE001
                pass
            llm.record(purpose, {"model": model, "max_tokens": max_tokens}, {"late": True, "provider_cost_usd": usd}, usage)
            _meter(model, usd)
        raise Late(f"{purpose} past its deadline after {elapsed:.0f} s")
    err = box.get("error")
    if err is not None:
        if claude_lane.is_billing_error(err) or claude_lane.is_auth_error(err):
            kind = "billing" if claude_lane.is_billing_error(err) else "auth"
            claude_lane._close(f"{kind}: {str(err)[:200]}")
            claude_trial.provider_refused(kind, err)
            raise Refused(f"Anthropic refused the lane ({kind})") from err
        raise err
    final = box["final"]
    usage = claude_lane._Usage(model, getattr(final, "usage", None))
    usd = float(usage.provider_cost_usd)
    llm.record(purpose, {"model": model, "max_tokens": max_tokens},
               {"stop": final.stop_reason, "provider_cost_usd": usd, "request_id": box.get("request_id")}, usage)
    _meter(model, usd)
    if final.stop_reason == "refusal":
        raise Refused(f"{purpose}: the model refused")
    text = "".join(box.get("text") or [])
    return Result(text, usage, final.stop_reason, round(box.get("first", 0.0), 2), round(time.monotonic() - t0, 2), usd, box.get("request_id"))
