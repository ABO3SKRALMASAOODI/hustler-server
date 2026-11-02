"""P0 #77: platform craft instructions are not the customer's deliverables."""
from types import SimpleNamespace
import agent_loop
import ask_ledger
import trial_reply

BRIEF = 'Silent footage, no speech. Story told through picture and short text hooks. Vertical 9:16. No music, no captions from speech.'
BOOT = ("You are the fresh lead editor for one selected podcast reel.\n"
        "The user's preserved direction: " + BRIEF + "\n\n"
        "Before making edits, call read_skill for short-form-direction. Load "
        "captions, music, transitions, broll-inserts when those crafts enter the edit. "
        "Finish with motion captions, hero moments on exact spoken words.")


def test_legacy_generated_boot_does_not_demand_the_tool_catalog():
    message = {'id': 1, 'content': BOOT, 'meta': {'shorts_boot': True}}
    text = agent_loop._customer_verification_request(None, 1, message, {})
    assert BRIEF in text
    assert ask_ledger.asks(text) == []
    # Continuations written by the old release must not resurrect the bug.
    assert agent_loop._customer_verification_request(
        None, 1, message, {'verification_request': BOOT}) == text


def test_structured_boot_preserves_positive_customer_asks():
    request = 'Make this a finished short. Add transitions and music.'
    message = {'content': BOOT, 'meta': {
        'shorts_boot': True, 'customer_request': request}}
    assert agent_loop._customer_verification_request(None, 1, message, {}) == request
    assert set(ask_ledger.asks(request)) == {'transitions', 'music'}


def test_normal_customer_text_is_never_rewritten_by_template_detection():
    message = {'content': BOOT, 'meta': {}}
    assert agent_loop._customer_verification_request(None, 1, message, {}) == BOOT


def test_boot_checkpoint_keeps_subsequent_customer_steering():
    message = {'content': BOOT, 'meta': {'shorts_boot': True}}
    for previous in (BRIEF, BOOT):
        text = agent_loop._customer_verification_request(None, 1, message, {
            'verification_request': previous + '\nActually, add transitions.'})
        assert text.endswith('Actually, add transitions.')
        assert ask_ledger.asks(text) == ['transitions']


def test_reply_uses_customer_contract_not_generated_instructions():
    ctx = SimpleNamespace(latest_edl=lambda: {'version': 8, 'json': {}},
                          verification_request=BRIEF)
    facts = trial_reply.facts_from_ctx(ctx, BOOT)
    assert facts.user_text == BRIEF
    assert ask_ledger.asks(facts.user_text) == []
