"""Round 80 — the two silent failures behind Aug 1-2 2026.

**1. Vision lost its key and nobody lost a request.** `VISION_API_KEY`
inherits `OPENAI_API_KEY` only when `VISION_BASE_URL == OPENAI_BASE_URL` — a
string equality between two independently-set env vars. The Jul 31 move to
Luna left the two pointing at different hosts, the inheritance produced "",
and `vision_available()` went False. Nothing failed: index-time captioning is
gated on exactly that flag, so it simply stopped running. Every index from
Jul 31 13:24 onward shipped `moments: []` and zero shot captions, `get_shots`
answered "(no visual caption)" for every shot of every upload, and the agent
edited blind for two days — including for a user who asked for the women in
his footage to be covered (it offered to black out the whole frame) and one
who uploaded a 31-minute match with two transcribed words in it.

The fix is structural, not a better comparison: the agent model is already a
funded, working, multimodal provider on the same box (that is the whole basis
of round 67's direct-sight look_at), so "no vision configured" cannot be true
while the agent itself can see.

**2. A 429 that named its own wait killed the turn.** `Please try again in
5.198s` became "I'm being rate-limited by the model right now" in the chat,
because `LLM_MAX_RETRIES` was 1 and `MAX_ATTEMPTS_AGENT` is 1 — the SDK's
retries are the only retries an agent turn gets.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import config                                                  # noqa: E402
import llm                                                     # noqa: E402


@pytest.fixture(autouse=True)
def _clean_latches():
    """Both blind latches are process-wide by design. Save and restore them so
    one test cannot blind another."""
    vb, ab = llm._vision_blind, set(llm._agent_blind)
    yield
    llm._vision_blind = vb
    llm._agent_blind.clear()
    llm._agent_blind.update(ab)


# ── vision degrades to the agent's own eyes, never to nothing ───────────

def test_vision_survives_an_unconfigured_vision_provider(monkeypatch):
    """THE REGRESSION. No VISION_API_KEY — the exact production state from
    Jul 31 to Aug 2 — must still leave vision available, because the agent
    model can see."""
    monkeypatch.setattr(config, "VISION_API_KEY", "")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(config, "AGENT_MULTIMODAL", True)

    assert llm.shared_vision_configured() is False
    assert llm.vision_available() is True, \
        "vision went dark with a perfectly good multimodal agent on the box"

    _, model = llm.vision_client_for(None)
    assert model == config.AGENT_MODEL


def test_the_dedicated_provider_still_wins_when_it_is_configured(monkeypatch):
    """The fallback is a fallback. A deployment that DOES point VISION_* at its
    own provider (a cheaper captioner, a different vendor) keeps using it."""
    monkeypatch.setattr(config, "VISION_API_KEY", "sk-vision")
    monkeypatch.setattr(config, "VISION_MODEL", "some-captioner")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(llm, "_vision_client", None)

    assert llm.shared_vision_configured() is True
    _, model = llm.vision_client_for(None)
    assert model == "some-captioner"


def test_honest_off_when_the_agent_cannot_see_either(monkeypatch):
    """The honest-off contract is not weakened. A genuinely text-only agent
    model must report vision UNAVAILABLE — tools then say "visual inspection
    unavailable" instead of burning a doomed 400 per look."""
    monkeypatch.setattr(config, "VISION_API_KEY", "")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(config, "AGENT_MULTIMODAL", False)

    assert llm.vision_fallback() is None
    assert llm.vision_available() is False


def test_a_latched_blind_agent_stops_standing_in_for_vision(monkeypatch):
    """Once the agent model has proven it rejects image parts, it is no longer
    a vision provider — otherwise the fallback re-offers the model that just
    refused, forever."""
    monkeypatch.setattr(config, "VISION_API_KEY", "")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(config, "AGENT_MULTIMODAL", True)
    assert llm.vision_available() is True

    llm.mark_agent_blind(config.AGENT_MODEL)
    assert llm.vision_fallback() is None
    assert llm.vision_available() is False


def test_no_key_at_all_is_still_off(monkeypatch):
    """A worker with no LLM configured has no eyes. The fallback must not
    manufacture a client out of an empty key."""
    monkeypatch.setattr(config, "VISION_API_KEY", "")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    monkeypatch.setattr(config, "AGENT_MULTIMODAL", True)

    assert llm.vision_fallback() is None
    assert llm.vision_available() is False


def test_the_shared_blind_latch_does_not_disable_the_fallback(monkeypatch):
    """_vision_blind is about the SHARED provider. Latching it must not take
    the agent's own eyes away — that would turn one vendor's 400 into a
    product-wide blackout."""
    monkeypatch.setattr(config, "VISION_API_KEY", "sk-vision")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(config, "AGENT_MULTIMODAL", True)
    llm._vision_blind = True

    assert llm.shared_vision_configured() is False
    assert llm.vision_available() is True
    _, model = llm.vision_client_for(None)
    assert model == config.AGENT_MODEL


# ── the rate-limit window ───────────────────────────────────────────────

def test_retries_can_ride_out_a_tpm_window():
    """MAX_ATTEMPTS_AGENT is 1 — an agent turn is deliberately never re-run by
    the job lane, because half an edit has already been written by the time it
    fails. So the SDK's own retries (which honour the provider's `retry-after`)
    are the ONLY retries a turn gets, and at 1 a single 429 ends it. Four real
    turns died that way on Aug 1-2 2026 against a limit whose error body said
    to try again in 5.198 seconds."""
    assert config.LLM_MAX_RETRIES >= 4, (
        "one retry is not enough to cross a token-per-minute window; this is "
        "the only retry an agent turn gets")


# ── the provider mismatch is legible in one curl ────────────────────────

def test_config_report_shows_the_vendor_mismatch(monkeypatch):
    """THE FIVE-DAY OUTAGE, as one line of JSON. The executor kept
    OPENAI_BASE_URL on xAI while AGENT_MODEL moved to OpenAI's Luna; xAI
    answered `Model not found: gpt-5.6-luna` to every index greeting from
    Jul 28 to Aug 2 2026 and nothing anywhere said so. An OpenAI model id
    sitting next to `api.x.ai` has to be readable at a glance."""
    monkeypatch.setattr(config, "OPENAI_BASE_URL", "https://api.x.ai/v1")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "xai-key")
    monkeypatch.setattr(config, "AGENT_MODEL", "gpt-5.6-luna")

    rep = llm.config_report()
    assert rep["llm_host"] == "api.x.ai"
    assert rep["agent_model"] == "gpt-5.6-luna"


def test_config_report_never_leaks_a_key(monkeypatch):
    """/health is unauthenticated — that is the whole point of it. It may say
    WHETHER a key is set and never what it is."""
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-super-secret-value")
    monkeypatch.setattr(config, "VISION_API_KEY", "sk-another-secret")

    blob = repr(llm.config_report())
    assert "sk-super-secret-value" not in blob
    assert "sk-another-secret" not in blob
    assert llm.config_report()["llm_key_set"] is True


def test_config_report_names_which_provider_answers_looks(monkeypatch):
    """"vision" must distinguish the three states, because they have three
    different fixes: a dedicated provider, the agent standing in, or no eyes
    at all."""
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(config, "AGENT_MULTIMODAL", True)

    monkeypatch.setattr(config, "VISION_API_KEY", "sk-vision")
    monkeypatch.setattr(config, "VISION_MODEL", "some-captioner")
    assert llm.config_report()["vision"] == "shared"

    monkeypatch.setattr(config, "VISION_API_KEY", "")
    assert llm.config_report()["vision"] == "agent"

    monkeypatch.setattr(config, "AGENT_MULTIMODAL", False)
    assert llm.config_report()["vision"] == "off"
