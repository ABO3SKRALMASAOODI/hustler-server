"""Model prices, keyed on the model id that is ALREADY on every llm_calls row.

WHY THIS REPLACED FOUR ENV CONSTANTS
------------------------------------
`LLM_PRICE_IN_PER_M` and friends were process-global: one number, applied to
every row, in four different files. That was fine while exactly one model ever
answered. It stops being fine the moment a single turn can run on a different
model from the one the env var was set for — which is exactly what plan-based
routing does (free -> DeepSeek, trial/paid -> Grok) and what vision already
does today (its own provider, its own model, priced at the agent's rate).

A global constant that is right for one model is silently WRONG for the other,
and "silently wrong by a constant factor" is the precise bug class that cost a
day when cached input was billed at the miss rate. So price is looked up per
ROW, from the model id the worker already stores.

Unknown model -> the env constants, so a provider nobody listed here keeps
working exactly as before instead of pricing at zero.

THIS FILE IS MIRRORED
---------------------
`worker/model_prices.py` and `backend/model_prices.py` are byte-identical: the
worker charges credits, the backend renders the admin cost views, and they are
separate services that cannot import each other. `worker/tests/
test_model_prices.py` loads both by path and fails if they ever drift — the
same guard OUTRO_VERSION uses. Edit one, copy it to the other.

REASONING TOKENS
----------------
Some providers report "thinking" tokens OUTSIDE completion_tokens; others fold
them in. Both spellings exist in the wild and the difference is a 2-4x charge,
so it is a per-model FACT, not a global assumption:

  * folded (reasoning_separate False) -- completion_tokens already contains
    them. Adding the reasoning count would DOUBLE-CHARGE. This is the default
    for anything unlisted, because over-charging a customer is the worse of the
    two errors.
  * separate (reasoning_separate True) -- completion_tokens excludes them and
    they are billed at the output rate, so leaving them out UNDER-charges. xAI
    invoices list "completion" and "reasoning" as two lines, which is what put
    grok in this column.

`reasoning_out` is recorded on every row either way (worker/llm.reasoning_tokens
-> the recorded response payload), so the flag can be checked against reality
before it is trusted. Run this for a full day on a provider and compare with
its dashboard's two output lines:

    SELECT model,
           SUM(completion_tokens) AS completion,
           SUM(COALESCE((response->>'reasoning_out')::float, 0)) AS reasoning
      FROM llm_calls
     WHERE created_at > NOW() - INTERVAL '1 day'
     GROUP BY 1;

If the dashboard's output total matches `completion` alone, the model is folded
and the flag must be False. If it matches completion + reasoning, it is
separate and the flag must be True.
"""

# model id (lowercased, exact) -> price in USD per 1M tokens.
#
#   in         cache-MISS input
#   cached_in  cache-HIT input (a repeated prompt prefix)
#   out        completion
#
# Keep every number sourced from the provider's own price page or a real
# invoice. A guessed price is a silent, permanent billing error.
MODEL_PRICES = {
    # OpenAI GPT-5.6 Luna -- the default agent AND vision model since Jul 31
    # 2026 (round 67). List prices from OpenAI's own model page (developers.
    # openai.com/api/docs/models/gpt-5.6-luna, checked Jul 31 2026): $0.20 in,
    # $0.02 cached input, $1.20 out per 1M. NOTE a third-party blog quotes
    # $1/$6 for "Luna" -- that is wrong (or Sol's); the official page and
    # OpenRouter's list price agree on 0.20/1.20. Reasoning tokens are FOLDED:
    # OpenAI's completion_tokens already contains them (the details object
    # only breaks them out), so reasoning_separate must stay False or every
    # reasoning turn double-charges.
    "gpt-5.6-luna": {
        "in": 0.20, "cached_in": 0.02, "out": 1.20,
        "reasoning_separate": False,
    },
    # Dedicated bounded-clip listener. Official gpt-audio-1.5 model page,
    # checked Aug 11 2026: text $2.50 in / $10 out; audio $32 in / $64 out
    # per 1M tokens. No cached-input price is published, so a cache hit must
    # conservatively cost the miss rate rather than invent a discount.
    "gpt-audio-1.5": {
        "in": 2.50, "cached_in": 2.50, "out": 10.00,
        "audio_in": 32.00, "audio_out": 64.00,
        "reasoning_separate": False,
    },
    # DeepSeek V4 Pro -- the default agent model Jul 26-31 2026.
    # Disk-cached prefix hits are 480x cheaper than a miss, which is why the
    # three-part charge exists at all: an agent turn re-sends the same system
    # prompt and tool schemas every iteration.
    "deepseek-v4-pro": {
        "in": 1.74, "cached_in": 0.003625, "out": 3.48,
        "reasoning_separate": False,
    },
    # Grok 4.5 -- what trial and paid users get once PAID_AGENT_MODEL is set.
    #
    # cached_in is 0.30, NOT 2.0. The comment this replaces said to set the
    # cached price equal to the miss price for Grok, i.e. "Grok has no prompt
    # caching". That is false: the invoice bills cached input as its own line,
    # and the overwhelming majority of an agent turn's input hits it. Pricing
    # those at 2.0 would over-charge cached tokens by ~6.7x, which is the exact
    # failure that was just fixed for DeepSeek.
    "grok-4.5": {
        "in": 2.00, "cached_in": 0.30, "out": 6.00,
        # xAI reports reasoning tokens separately from completion_tokens.
        # Verify with the query in this module's docstring before trusting it
        # on a new xAI tier -- see also AGENT_REASONING_EFFORT, which is what
        # actually shrinks this line.
        "reasoning_separate": True,
    },
}

_FIELDS = ("in", "cached_in", "out", "audio_in", "audio_out")


def normalize(model):
    return (model or "").strip().lower()


def price_for(model, fallback):
    """Prices for `model`, or `fallback` (a dict with the same keys) when the
    model is not listed. Never raises -- an unpriced model must degrade to the
    env constants, not to zero."""
    row = MODEL_PRICES.get(normalize(model))
    if not row:
        out = dict(fallback)
        out.setdefault("audio_in", out.get("in", 0.0))
        out.setdefault("audio_out", out.get("out", 0.0))
        out.setdefault("reasoning_separate", False)
        return out
    out = dict(row)
    out.setdefault("audio_in", out["in"])
    out.setdefault("audio_out", out["out"])
    return out


# ---------------------------------------------------------------------------
#  What a credit is worth
# ---------------------------------------------------------------------------
# A credit is spent at TWICE the model's real cost (round 49). This single
# number is the entire margin of the business, so it lives beside the prices it
# is derived from rather than as a literal in the two places that divide by it.
#
# It used to be 0.01 -- one credit, one cent of real spend -- which made the
# grant and the cost the same number and left the margin to come from the
# bundle size alone. That worked out at 50%/40% on the monthly prices and
# 40%/28% on the annual ones, and an intro discount on top of an annual plan
# was sold BELOW cost. Doubling the burn rate fixed all four at once without
# moving a sticker price: the customer sees a bigger credit grant, and each
# credit buys half as much model.
#
# Everything downstream reads this, so the two must never be set independently:
#   worker/db.charge_turn_credits       what the user is actually charged
#   worker/agent_tools.running_credits  the in-turn cap, same units
# and PLAN_CREDITS / PLAN_MONTHLY_LIMITS are chosen against it (see the margin
# table in backend/routes/paddle.py). Halving it back to 0.01 doubles every
# plan's real cost and takes Frontier to a 0% margin.
USD_PER_CREDIT = 0.005


def usd_to_credits(usd, ndigits=2):
    """Model spend in dollars -> credits. The one conversion, used by the
    charge and by every projection shown to a user, so a quote and an invoice
    cannot use different arithmetic."""
    try:
        return round(float(usd) / USD_PER_CREDIT, ndigits)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
#  SQL
# ---------------------------------------------------------------------------
# The charge and every admin cost view price llm_calls rows in SQL. Doing it
# per ROW (rather than summing tokens and multiplying once) is what makes the
# per-model table work at all, and it is strictly more accurate even on a
# single-model deployment.
#
# NOTE: the expressions below contain NO percent sign, deliberately. psycopg2
# scans the whole statement for its own placeholders, so a stray percent -- even
# inside a comment -- raises before Postgres ever sees the query. That has taken
# a page down here before.


def _lit(text):
    return "'" + str(text).replace("'", "''") + "'"


def _case(field, model_col, fallback):
    """CASE that maps a row's model id to one price field."""
    def _value(cfg):
        if field == "audio_in":
            return cfg.get(field, cfg["in"])
        if field == "audio_out":
            return cfg.get(field, cfg["out"])
        return cfg[field]

    if field == "audio_in":
        fallback_value = fallback.get(field, fallback["in"])
    elif field == "audio_out":
        fallback_value = fallback.get(field, fallback["out"])
    else:
        fallback_value = fallback[field]
    whens = " ".join(
        "WHEN {} THEN {}".format(_lit(mid), float(_value(cfg)))
        for mid, cfg in sorted(MODEL_PRICES.items()))
    return "(CASE lower(COALESCE({}, '')) {} ELSE {} END)".format(
        model_col, whens, float(fallback_value))


def _reasoning_case(model_col):
    """1 when this row's model bills reasoning ON TOP of completion_tokens,
    0 when it folds them in. Unlisted models fold (never over-charge)."""
    whens = " ".join(
        "WHEN {} THEN {}".format(_lit(mid),
                                 1 if cfg.get("reasoning_separate") else 0)
        for mid, cfg in sorted(MODEL_PRICES.items()))
    return "(CASE lower(COALESCE({}, '')) {} ELSE 0 END)".format(
        model_col, whens)


def row_cost_sql(fallback, model_col="model", response_col="response",
                 prompt_col="prompt_tokens", completion_col="completion_tokens"):
    """USD cost of ONE llm_calls row, as a SQL expression.

    Three-part input charge (miss / hit) plus output, each at this row's own
    model price. `cached_in` is clamped to the prompt itself so a bogus provider
    number can never make a charge negative, and reasoning tokens are added only
    for models that report them outside completion_tokens.
    """
    total_in = "GREATEST(COALESCE({}, 0), 0)".format(prompt_col)
    total_out = "GREATEST(COALESCE({}, 0), 0)".format(completion_col)
    audio_in = (
        "LEAST(GREATEST(COALESCE(({}->>'audio_in')::float, 0), 0), {})"
    ).format(response_col, total_in)
    audio_out = (
        "LEAST(GREATEST(COALESCE(({}->>'audio_out')::float, 0), 0), {})"
    ).format(response_col, total_out)
    cached = (
        "LEAST(GREATEST(COALESCE(({}->>'cached_in')::float, 0), 0), "
        "GREATEST({} - {}, 0))"
    ).format(response_col, total_in, audio_in)
    reasoning = ("GREATEST(COALESCE(({}->>'reasoning_out')::float, 0), 0) * {}"
                 ).format(response_col, _reasoning_case(model_col))
    return (
        "((GREATEST({total_in} - {audio_in} - {cached}, 0) * {p_in}"
        " + {cached} * {p_cached}"
        " + {audio_in} * {p_audio_in}"
        " + (GREATEST({total_out} - {audio_out}, 0) + {reason}) * {p_out}"
        " + {audio_out} * {p_audio_out}) / 1000000.0)"
    ).format(total_in=total_in, total_out=total_out,
             audio_in=audio_in, audio_out=audio_out,
             cached=cached, reason=reasoning,
             p_in=_case("in", model_col, fallback),
             p_cached=_case("cached_in", model_col, fallback),
             p_out=_case("out", model_col, fallback),
             p_audio_in=_case("audio_in", model_col, fallback),
             p_audio_out=_case("audio_out", model_col, fallback))
