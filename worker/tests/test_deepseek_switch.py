"""Round 46 — the move to DeepSeek V4 Pro.

Three things here would fail silently and expensively rather than loudly:

  1. **Cache-hit billing.** DeepSeek serves a repeated prompt prefix at
     $0.003625/1M against $1.74/1M for a miss — 480x. An agent turn re-sends
     the same system prompt + ~60 tool schemas every iteration, so most of its
     input after step one is a cache hit. Bill it at the miss rate and a
     10-step turn costs the user several times what it cost us; nothing errors,
     the number is just wrong. The in-turn cap and the final DB charge have to
     agree on that, or a turn stops on spend the user is never billed for.
  2. **The image provider must stay off the chat provider.** DeepSeek has no
     /images/generations endpoint. Inheriting its base URL would 404 every
     generation — which is invisible in the UI (the agent just says it couldn't
     make an image) and has already cost two multi-week silent outages on this
     one capability. Absent key => capability OFF, not broken.
  3. **The failure message must not paste the provider's body into the chat.**
     On Jul 26 2026 every user in the product read our xAI team UUID and a
     sentence truncated mid-word, four times in a row, alongside an invitation
     to retry something that could not succeed.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import agent_loop                                              # noqa: E402
import agent_tools                                             # noqa: E402
import config                                                  # noqa: E402
import llm                                                     # noqa: E402
import model_prices                                            # noqa: E402


# ── the model actually configured ──────────────────────────────────────

def test_agent_runs_on_v4_pro():
    assert config.AGENT_MODEL == "deepseek-v4-pro"
    assert config.OPENAI_BASE_URL == "https://api.deepseek.com"


def test_vision_does_not_follow_the_agent_onto_deepseek():
    """CORRECTS THIS FILE'S ORIGINAL CLAIM (round 46): v4-PRO does not accept
    images either. It rejects an image_url content part at the JSON layer —
    400 "unknown variant `image_url`, expected `text`" — so from 13:06 UTC on
    Jul 26 2026 every look_at, look_at_asset and preview self-check failed,
    59 in a row, and the agent asked users to describe their own footage.
    Vision keeps its own provider, like image generation does."""
    assert config.VISION_BASE_URL != config.OPENAI_BASE_URL or \
        "deepseek" not in config.VISION_BASE_URL
    assert "deepseek" not in config.VISION_MODEL


def test_vision_key_is_never_inherited_across_providers():
    """A DeepSeek key sent to xAI is a 401 per call. Absent key => capability
    OFF (vision_available() False, tools say so), never broken."""
    import llm

    for base, key, expect in (
            ("https://api.x.ai/v1", "", False),      # no key for that provider
            ("https://api.x.ai/v1", "xai-k", True),
    ):
        old = (config.VISION_BASE_URL, config.VISION_API_KEY)
        config.VISION_BASE_URL, config.VISION_API_KEY = base, key
        try:
            assert llm.vision_available() is expect
        finally:
            config.VISION_BASE_URL, config.VISION_API_KEY = old


def test_a_provider_that_rejects_images_turns_vision_off():
    """The 59-doomed-calls-in-a-row failure: one 400 on the image part proves
    the provider is blind, and every later call would fail identically."""
    import llm

    old = (config.VISION_MODEL, config.VISION_API_KEY, llm._vision_blind)
    config.VISION_MODEL, config.VISION_API_KEY = "blind-model", "k"
    llm._vision_blind = False

    class _Boom:
        def with_options(self, **_kw):
            return self

        @property
        def chat(self):
            return self

        @property
        def completions(self):
            return self

        def create(self, **_kw):
            raise RuntimeError(
                "Error code: 400 - {'error': {'message': 'Failed to "
                "deserialize the JSON body into the target type: messages[0]: "
                "unknown variant `image_url`, expected `text`'}}")

    real_client, real_part = llm.vision_client, llm.image_part
    llm.vision_client = lambda: _Boom()
    llm.image_part = lambda p: {"type": "image_url", "image_url": {"url": ""}}
    try:
        assert llm.vision_available() is True
        assert llm.ask_vision("what is this?", ["a.jpg"]) is None
        assert llm.vision_available() is False, \
            "a blind provider must disable vision, not fail once per call"
    finally:
        llm.vision_client, llm.image_part = real_client, real_part
        (config.VISION_MODEL, config.VISION_API_KEY,
         llm._vision_blind) = old


def test_prices_match_the_configured_model():
    """CLAUDE.md's standing rule: change AGENT_MODEL, change these, or credits
    drift from real cost."""
    assert config.LLM_PRICE_IN_PER_M == 1.74
    assert config.LLM_PRICE_OUT_PER_M == 3.48
    assert config.LLM_PRICE_CACHED_IN_PER_M < config.LLM_PRICE_IN_PER_M


def test_worker_and_db_price_constants_agree():
    """Two modules read the same env for the same number; a default that drifts
    between them means the in-turn cap and the final charge disagree."""
    import db
    assert db.LLM_PRICE_IN_PER_M == config.LLM_PRICE_IN_PER_M
    assert db.LLM_PRICE_OUT_PER_M == config.LLM_PRICE_OUT_PER_M
    assert db.LLM_PRICE_CACHED_IN_PER_M == config.LLM_PRICE_CACHED_IN_PER_M


# ── cached-token accounting ────────────────────────────────────────────

class _Usage:
    def __init__(self, prompt_tokens=0, completion_tokens=0, **kw):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        for k, v in kw.items():
            setattr(self, k, v)


class _Details:
    def __init__(self, cached_tokens):
        self.cached_tokens = cached_tokens


def test_reads_deepseeks_spelling():
    u = _Usage(prompt_tokens=1000, prompt_cache_hit_tokens=900)
    assert llm.cached_input_tokens(u) == 900


def test_reads_openais_spelling():
    u = _Usage(prompt_tokens=1000, prompt_tokens_details=_Details(750))
    assert llm.cached_input_tokens(u) == 750


def test_reads_a_dict_details_payload():
    u = _Usage(prompt_tokens=1000, prompt_tokens_details={"cached_tokens": 400})
    assert llm.cached_input_tokens(u) == 400


def test_a_provider_with_no_caching_reports_zero():
    """Zero makes the cached price inert — a provider without prompt caching
    must not accidentally receive a discount."""
    assert llm.cached_input_tokens(_Usage(prompt_tokens=1000)) == 0
    assert llm.cached_input_tokens(None) == 0


def test_cached_can_never_exceed_the_prompt():
    """The charge subtracts this from prompt_tokens. A bogus larger value would
    otherwise credit the user for tokens they really did spend."""
    u = _Usage(prompt_tokens=100, prompt_cache_hit_tokens=100000)
    assert llm.cached_input_tokens(u) == 100


def test_garbage_is_not_a_discount():
    assert llm.cached_input_tokens(
        _Usage(prompt_tokens=500, prompt_cache_hit_tokens="lots")) == 0


# ── the in-turn cap uses the same formula as the DB charge ──────────────

def _ctx():
    """A bare ToolContext: its __init__ wants a live DB/job/project, and none
    of that is involved in pricing. This still calls the REAL running_credits,
    which is the thing under test."""
    ctx = object.__new__(agent_tools.ToolContext)
    ctx.images_generated = []
    ctx.gen_extra_cost_usd = 0.0
    ctx.tokens_in = 0
    ctx.tokens_out = 0
    ctx.tokens_cached_in = 0
    # Empty per-model breakdown: these tests poke the flat totals directly, so
    # running_credits falls back to the fallback prices. The per-model path has
    # its own tests in test_model_prices.py.
    ctx.model_usage = {}
    ctx.credit_budget = None
    return ctx


def _expected_credits(tin, cached, tout):
    cost = ((tin - cached) * config.LLM_PRICE_IN_PER_M +
            cached * config.LLM_PRICE_CACHED_IN_PER_M +
            tout * config.LLM_PRICE_OUT_PER_M) / 1e6
    return model_prices.usd_to_credits(cost)


def test_running_credits_discounts_cache_hits():
    ctx = _ctx()
    ctx.tokens_in, ctx.tokens_out, ctx.tokens_cached_in = 500_000, 20_000, 0
    full = ctx.running_credits()
    ctx.tokens_cached_in = 450_000
    cached = ctx.running_credits()
    assert cached < full
    assert cached == _expected_credits(500_000, 450_000, 20_000)
    # A realistic multi-step turn: billing it as all-miss would overcharge by
    # well over 2x. This is the whole reason the split exists.
    assert full > cached * 2


def test_running_credits_matches_all_miss_when_nothing_is_cached():
    """Legacy parity: with no cache reporting, the number must be exactly what
    it was before this change."""
    ctx = _ctx()
    ctx.tokens_in, ctx.tokens_out, ctx.tokens_cached_in = 300_000, 10_000, 0
    legacy = model_prices.usd_to_credits(
        (300_000 * config.LLM_PRICE_IN_PER_M +
         10_000 * config.LLM_PRICE_OUT_PER_M) / 1e6)
    assert ctx.running_credits() == legacy


def test_running_credits_clamps_a_bad_cached_count():
    ctx = _ctx()
    ctx.tokens_in, ctx.tokens_out = 1000, 0
    ctx.tokens_cached_in = 999_999
    assert ctx.running_credits() >= 0


def test_the_db_charge_uses_the_same_three_part_formula():
    """The SQL and the Python cap must not drift. Asserting the shape of the
    query is weaker than running it, but it does catch someone 'simplifying'
    the charge back to prompt_tokens * price_in."""
    import inspect
    import db
    src = inspect.getsource(db.charge_turn_credits)
    assert "cached_in" in src
    # The three-part split now lives in the shared per-row expression that the
    # charge SUMs, so assert on THAT rather than on inlined constants.
    assert "_ROW_COST_SQL" in src
    assert "cached_in" in db._ROW_COST_SQL
    assert "reasoning_out" in db._ROW_COST_SQL
    assert str(config.LLM_PRICE_IN_PER_M) in db._ROW_COST_SQL
    assert str(config.LLM_PRICE_CACHED_IN_PER_M) in db._ROW_COST_SQL


# ── the image provider is independent of the chat provider ─────────────

def test_image_defaults_do_not_follow_the_chat_provider():
    """DeepSeek has no image endpoint; the default must not point there."""
    assert "deepseek" not in config.IMAGE_BASE_URL


def test_image_key_is_not_inherited_across_providers(monkeypatch):
    """A DeepSeek chat key is not an xAI image key. Inheriting it would send a
    valid-looking request with the wrong credential."""
    monkeypatch.setattr(config, "OPENAI_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setattr(config, "IMAGE_BASE_URL", "https://api.x.ai/v1")
    monkeypatch.setattr(config, "IMAGE_API_KEY", "")
    monkeypatch.setattr(config, "IMAGE_GEN_MODEL", "grok-imagine-image-quality")
    assert not llm.image_available()
    # ...and the capability is HIDDEN, not advertised-then-failing.
    assert "generate_image" not in agent_tools.capabilities_digest()
    assert all(t["function"]["name"] != "generate_image"
               for t in agent_tools.openai_tools())


def test_image_key_is_inherited_when_the_provider_is_the_same(monkeypatch):
    """Single-provider deployments (everything on xAI) must keep working with
    only OPENAI_API_KEY set — this is the backward-compatible path."""
    import importlib
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.x.ai/v1")
    monkeypatch.setenv("IMAGE_BASE_URL", "https://api.x.ai/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "xai-key")
    monkeypatch.delenv("IMAGE_API_KEY", raising=False)
    fresh = importlib.reload(config)
    try:
        assert fresh.IMAGE_API_KEY == "xai-key"
    finally:
        importlib.reload(config)      # restore the real process config


# ── the message a customer reads when the provider is out of credit ────

# The verbatim exception text from the Jul 26 2026 outage.
_REAL_403 = ("Error code: 403 - {'code': 'permission-denied', 'error': 'Your "
             "team 166666fc-e639-48d4-b7be-54d9c8cd277c has either used all "
             "available credits or reached its monthly spending limit'}")


class _Quota(Exception):
    status_code = 403


def test_the_provider_body_never_reaches_the_user():
    msg = agent_loop._user_facing_failure(_Quota(_REAL_403))
    for leak in ("166666fc", "permission-denied", "403", "team",
                 "Error code", "{"):
        assert leak not in msg, f"leaked {leak!r}: {msg}"


def test_a_quota_failure_does_not_invite_a_retry_that_cannot_work():
    """"try sending that again" was the worst part: a provider with no credit
    fails identically every time, so people burned their session retrying."""
    msg = agent_loop._user_facing_failure(_Quota(_REAL_403))
    assert "try sending that again" not in msg.lower()
    assert "my side" in msg.lower() or "on it" in msg.lower()
    # It must still say their work survived and they weren't billed.
    assert "safe" in msg.lower()
    assert "credits" in msg.lower()


def test_quota_is_detected_from_the_body_alone():
    """Not every SDK surfaces status_code — the text must be enough."""
    msg = agent_loop._user_facing_failure(RuntimeError(_REAL_403))
    assert "a little later" in msg


def test_insufficient_balance_phrasings_are_caught():
    for body in ("Insufficient Balance",
                 "You exceeded your current quota",
                 "billing hard limit reached",
                 "Your credit balance is too low"):
        msg = agent_loop._user_facing_failure(RuntimeError(body))
        assert "later" in msg, body


def test_rate_limit_is_its_own_message():
    class _RL(Exception):
        status_code = 429
    msg = agent_loop._user_facing_failure(_RL("Too Many Requests"))
    assert "rate-limit" in msg.lower()
    assert "minute" in msg.lower()


def test_a_timeout_says_so_and_suggests_smaller_steps():
    msg = agent_loop._user_facing_failure(
        TimeoutError("Request timed out after 90s"))
    assert "too long" in msg.lower() and "smaller" in msg.lower()


def test_an_ordinary_bug_keeps_the_old_generic_wording():
    """Unknown failures ARE usually worth one retry, and the old sentence was
    fine for them — it just must not carry the exception text."""
    msg = agent_loop._user_facing_failure(
        KeyError("shots"))
    assert msg == ("Something went wrong on my end while editing. Your video "
                   "and edit history are safe — try sending that again.")


# ── an outage has to be VISIBLE in admin ───────────────────────────────

def test_a_failed_agent_call_is_recorded():
    """llm.record used to run only after a successful call, so during the
    Jul 26 2026 outage the admin Model I/O tab showed nothing at all for the
    turns users were watching fail. The error must be recorded before it
    propagates."""
    import inspect
    src = inspect.getsource(agent_loop)
    # `model` is now resolved per user (free vs paid tier), so match on the
    # call itself rather than on the constant it used to hardcode.
    call = src.split("resp = client.chat.completions.create(\n"
                     "                model=model,")[1][:1600]
    assert "except Exception" in call
    assert 'llm.record("agent"' in call
    assert '"error"' in call
    assert "raise" in call


def test_ask_text_and_vision_publish_their_error():
    """last_error() is what lets a caller record the real reason instead of a
    placeholder."""
    import inspect
    src = inspect.getsource(llm)
    assert src.count("_note_error(e)") >= 2
    assert "def last_error()" in src


def test_the_indexer_records_the_real_error_not_call_failed():
    import inspect
    import indexer
    src = inspect.getsource(indexer)
    assert 'llm.last_error() or "call failed"' in src


def test_last_error_is_none_until_something_fails():
    # Thread-local and unset on a fresh thread: absence must read as None, not
    # as a stale error from another lane's turn.
    import threading
    seen = []
    t = threading.Thread(target=lambda: seen.append(llm.last_error()))
    t.start()
    t.join()
    assert seen == [None]


def test_last_error_captures_the_exception_text():
    try:
        raise RuntimeError("Insufficient Balance")
    except RuntimeError as e:
        llm._note_error(e)
    assert "Insufficient Balance" in llm.last_error()
    assert "RuntimeError" in llm.last_error()


class _Cur:
    """Enough of a psycopg2 cursor to drive charge_turn_credits' first query.
    If the usage guard works, execute() is called exactly once and no UPDATE
    ever runs — so recording the statements is the assertion."""
    def __init__(self, row):
        self._row = row
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.statements.append(sql)

    def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, row):
        self.cur = _Cur(row)

    def cursor(self):
        return self.cur


def _agg(n=1, tin=0, tout=0, token_cost=None, n_images=0, gen_cost=0):
    # token_cost is what the per-row pricing SQL returns (dollars). It defaults
    # to the fallback rates applied to the flat totals, which is what a
    # single-model turn on an unlisted model would produce.
    if token_cost is None:
        token_cost = (tin * config.LLM_PRICE_IN_PER_M
                      + tout * config.LLM_PRICE_OUT_PER_M) / 1e6
    return {"n": n, "tin": tin, "tout": tout, "token_cost": token_cost,
            "n_images": n_images, "gen_cost": gen_cost}


def test_a_turn_with_only_error_rows_charges_nothing():
    """Failed calls now leave llm_calls rows so an outage is visible in admin.
    Those rows must not trip MIN_TURN_CREDITS: the chat tells the user a failed
    turn cost them nothing, and that has to be true."""
    import db
    conn = _Conn(_agg(n=3))            # three rows, all errors: zero usage
    assert db.charge_turn_credits(conn, 1, "video:1") == 0.0
    # It must not have gone on to read or write the user's balance.
    assert len(conn.cur.statements) == 1
    assert not any("UPDATE users" in s for s in conn.cur.statements)


def test_a_turn_with_no_rows_at_all_still_charges_nothing():
    import db
    conn = _Conn(_agg(n=0))
    assert db.charge_turn_credits(conn, 1, "video:1") == 0.0


def test_real_usage_is_still_charged():
    """The guard must not swallow genuine turns — it keys on usage, not errors."""
    import db
    conn = _Conn(_agg(n=2, tin=100_000, tout=5_000))
    # Reaches the balance lookup, which our stub answers with the same row;
    # what matters is that it did NOT early-return 0.
    try:
        db.charge_turn_credits(conn, 1, "video:1")
    except Exception:
        pass
    assert len(conn.cur.statements) > 1, "a real turn must reach the balance"


def test_an_image_only_turn_is_charged():
    """No tokens, but a real image cost money."""
    import db
    conn = _Conn(_agg(n=1, n_images=1))
    try:
        db.charge_turn_credits(conn, 1, "video:1")
    except Exception:
        pass
    assert len(conn.cur.statements) > 1


def test_no_failure_message_ever_contains_a_stack_or_repr():
    for e in (_Quota(_REAL_403), RuntimeError("boom at 0x7f9"),
              KeyError("k"), TimeoutError("t"), ValueError("v")):
        msg = agent_loop._user_facing_failure(e)
        for junk in ("Traceback", "0x7f9", "Error code", "{'"):
            assert junk not in msg
