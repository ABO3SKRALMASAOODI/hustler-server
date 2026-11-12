"""One model call for a workflow stage, with its dollars recorded.

The key comes from this process's environment only (run the shell under
.valmera/claude_trial_env.sh). Nothing here prints or stores a key.
"""
import json
import time

MODEL = "claude-opus-5-5"
# USD per token: input, cache write, cache read, output (slice/direct.py's card)
PRICES = {"claude-opus-5-5": (4.00e-6, 5.00e-6, 0.20e-6, 20.00e-6),
          "claude-haiku-5-5": (0.10e-6, 0.125e-6, 0.01e-6, 0.50e-6)}


def cost(model, u):
    pin, pw, pr, pout = PRICES.get(model, PRICES[MODEL])
    return (u.input_tokens * pin + (u.cache_creation_input_tokens or 0) * pw
            + (u.cache_read_input_tokens or 0) * pr + u.output_tokens * pout)


def ask(system, content, *, model=MODEL, effort="medium", max_tokens=16000):
    """(text, usage record). Streams, thinking adaptive, no temperature."""
    import anthropic
    c = anthropic.Anthropic()
    t0 = time.time()
    with c.messages.stream(model=model, max_tokens=max_tokens, thinking={"type": "adaptive"},
                           output_config={"effort": effort}, system=system,
                           messages=[{"role": "user", "content": content}]) as st:
        text = "".join(st.text_stream)
        final = st.get_final_message()
    if final.stop_reason == "refusal":
        raise RuntimeError("the model refused this stage")
    u = final.usage
    rec = dict(provider="anthropic", model=model, effort=effort, input=u.input_tokens,
               cache_write=u.cache_creation_input_tokens or 0, cache_read=u.cache_read_input_tokens or 0,
               output=u.output_tokens, usd=round(cost(model, u), 5), seconds=round(time.time() - t0, 1),
               stop=final.stop_reason)
    return text, rec


def json_block(text):
    """The model's JSON answer: the outermost object or array in its text (machine syntax only)."""
    s = text.strip()
    for o, c in (("{", "}"), ("[", "]")):
        i, j = s.find(o), s.rfind(c)
        if i != -1 and j > i:
            try:
                return json.loads(s[i:j + 1])
            except ValueError:
                continue
    raise ValueError("no JSON in the model's answer")
