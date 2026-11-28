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


TALLIES = []           # payer films being made in this process: each counts what its own calls cost (film._Payer)
_tally_lock = threading.Lock()


def _meter(model, usd):
    if claude_trial.current() is not None:
        claude_trial.meter(model, usd)
        return
    with _tally_lock:
        for t in TALLIES:
            t.spent_usd += max(0.0, float(usd or 0.0))


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
    Refused, or the provider's error.

    An Anthropic billing or auth refusal never closes the lane (#1796): the key is re-read and the
    same call is sent once more (a key rotated a moment ago, P0 #60); a second refusal alerts the
    owner and the call is answered by a recovery provider (llm.provider_recovery_clients) inside the
    same deadline."""
    if not str(model or "").startswith("claude-"):
        # The OpenAI rung (lane Q): only a replay's own models or a Director row limited to its
        # test_user_ids (flags.parse) name one. Same Result, deadline and metering.
        from . import call_openai
        return call_openai.stream(model, system, content, max_tokens=max_tokens, effort=effort, purpose=purpose,
                                  deadline=deadline, on_text=on_text, thinking=thinking, first_text_by=first_text_by)
    if not claude_lane.configured():
        raise Refused("the Claude lane has no key")
    claude_trial.guard_call()
    body = {"model": model, "max_tokens": int(max_tokens), "output_config": {"effort": effort}, "system": system,
            "messages": [{"role": "user", "content": [
                {k: v for k, v in b.items() if k != "detail"} if isinstance(b, dict) else b
                for b in content] if isinstance(content, list) else content}]}
    if isinstance(system, str) and system:
        body["system"] = [{"type": "text", "text": system,
                           "cache_control": {"type": "ephemeral", "ttl": "1h"}}]
    if thinking:
        body["thinking"] = THINKING
    t0 = time.monotonic()
    try:
        return _claude(model, body, purpose, deadline, on_text, first_text_by)
    except Exception as err:  # noqa: BLE001 - classified here
        if not _refused(err):
            raise
    claude_lane._refresh_live_key()
    if first_text_by is not None:      # the refusal's round trip is not the retry's thinking time
        first_text_by = min(deadline, first_text_by + (time.monotonic() - t0))
    try:
        return _claude(model, body, purpose, deadline, on_text, first_text_by)
    except Exception as err:  # noqa: BLE001 - classified here
        if not _refused(err):
            raise
        kind = "billing" if claude_lane.is_billing_error(err) else "auth"
        claude_trial.provider_refused(kind, err)
        refusal = err
    return _recover(model, system, content, max_tokens=max_tokens, purpose=purpose, deadline=deadline,
                    on_text=on_text, refusal=refusal, kind=kind)


def _refused(err):
    return claude_lane.is_billing_error(err) or claude_lane.is_auth_error(err)


def _claude(model, body, purpose, deadline, on_text, first_text_by):
    """One streamed Messages call under the deadline. Returns a Result; raises Late or the provider's error."""
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
            llm.record(purpose, {"model": model, "max_tokens": body["max_tokens"]},
                       {"late": True, "provider_cost_usd": usd}, usage)
            _meter(model, usd)
        raise Late(f"{purpose} past its deadline after {elapsed:.0f} s")
    err = box.get("error")
    if err is not None:
        raise err
    final = box["final"]
    usage = claude_lane._Usage(model, getattr(final, "usage", None))
    usd = float(usage.provider_cost_usd)
    llm.record(purpose, {"model": model, "max_tokens": body["max_tokens"]},
               {"stop": final.stop_reason, "provider_cost_usd": usd, "request_id": box.get("request_id")}, usage)
    _meter(model, usd)
    if final.stop_reason == "refusal":
        raise Refused(f"{purpose}: the model refused")
    text = "".join(box.get("text") or [])
    return Result(text, usage, final.stop_reason, round(box.get("first", 0.0), 2), round(time.monotonic() - t0, 2), usd, box.get("request_id"))


class _ChatUsage:
    """A chat-completions usage under the Claude spellings the callers read."""

    def __init__(self, raw):
        details = getattr(raw, "prompt_tokens_details", None)
        cached = int((details.get("cached_tokens") if isinstance(details, dict)
                      else getattr(details, "cached_tokens", 0)) or 0)
        self.prompt_tokens = int(getattr(raw, "prompt_tokens", 0) or 0)
        self.completion_tokens = int(getattr(raw, "completion_tokens", 0) or 0)
        self.input_tokens = max(0, self.prompt_tokens - cached)
        self.output_tokens = self.completion_tokens
        self.cache_read_input_tokens = cached
        self.cache_creation_input_tokens = 0
        self.service_tier = None


def _chat_content(content):
    """Claude content blocks (text, base64 or url images) as one chat-completions user message."""
    if not isinstance(content, list):
        return str(content or "")
    out = []
    for b in content:
        if isinstance(b, dict) and b.get("type") == "image":
            src = b.get("source") or {}
            url = src.get("url") if src.get("type") == "url" else \
                f"data:{src.get('media_type') or 'image/jpeg'};base64,{src.get('data', '')}"
            out.append({"type": "image_url", "image_url": {"url": url}})
        else:
            out.append({"type": "text", "text": b.get("text", "") if isinstance(b, dict) else str(b)})
    return out


def _recover(asked, system, content, *, max_tokens, purpose, deadline, on_text, refusal, kind):
    """Anthropic refused the key twice: the same call on the next configured provider wallet, one
    request each, bounded by the call's deadline. The answer is recorded and metered on its own
    thread (the turn's recorder and trial scope travel with it), so a late answer is still paid for."""
    from . import call_openai
    import edit_rung
    messages = [{"role": "system", "content": call_openai.instructions(system)},
                {"role": "user", "content": _chat_content(content)}]
    recorder, scope = llm.get_recorder(), claude_trial.current()
    box = {}
    t0 = time.monotonic()

    def run():
        llm.set_recorder(recorder)
        if scope is not None:
            claude_trial.activate(scope)
        try:
            for model, client in llm.provider_recovery_clients():
                left = deadline - time.monotonic()
                if left <= 1.0:
                    return
                try:
                    res = llm.create_with_dialect(llm.without_sdk_retries(client), model, messages,
                                                  max_tokens=int(max_tokens), deadline=deadline,
                                                  cache_role=purpose)
                except Exception as exc:  # noqa: BLE001 - the next wallet answers
                    box["error"] = exc
                    if llm.is_budget_stop(exc):
                        return
                    continue
                raw = getattr(res, "usage", None)
                usd = llm.provider_cost_usd(raw, model)
                usd = float(usd if usd is not None else llm.credit_parity_cost_usd(raw, model) if raw is not None else 0.0)
                choice = (getattr(res, "choices", None) or [None])[0]
                stop = getattr(choice, "finish_reason", None)
                llm.record(purpose, {"model": model, "max_tokens": max_tokens, "asked_model": asked},
                           {"stop": stop, "provider_cost_usd": usd, "provider_fallback": True,
                            "late": bool(box.get("late"))}, raw)
                if claude_trial.current() is not None:
                    claude_trial.meter(model, edit_rung.allowance_usd(raw) if raw is not None else 0.0,
                                       kind="provider_fallback")
                else:
                    _meter(model, usd)
                text = getattr(getattr(choice, "message", None), "content", None) or ""
                box["result"] = (model, text, _ChatUsage(raw), "max_tokens" if stop == "length" else "end_turn", usd,
                                 getattr(res, "id", None))
                return
        except BaseException as exc:  # noqa: BLE001 - carried to the caller's thread
            box["error"] = exc
        finally:
            if scope is not None:
                claude_trial.deactivate()
            llm.set_recorder(None)
    th = threading.Thread(target=run, name=f"film-{purpose}-recovery", daemon=True)
    th.start()
    th.join(max(0.0, deadline - time.monotonic()))
    if th.is_alive():
        box["late"] = True
        raise Late(f"{purpose} past its deadline after {time.monotonic() - t0:.0f} s on the recovery provider")
    if "result" not in box:
        raise Refused(f"Anthropic refused the lane ({kind}) and no recovery provider answered: "
                      f"{str(box.get('error') or 'none configured')[:200]}") from (box.get("error") or refusal)
    model, text, usage, stop, usd, request_id = box["result"]
    print(f"[film] {purpose}: Anthropic refused ({kind}); answered on {model}", flush=True)
    if on_text and text:
        on_text(text)
    return Result(text, usage, stop, round(time.monotonic() - t0, 2), round(time.monotonic() - t0, 2), usd, request_id)
