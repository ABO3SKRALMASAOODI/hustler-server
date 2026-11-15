import pytest
import provider_readiness as readiness
from routes import paddle
from flask import Flask

@pytest.fixture(autouse=True)
def reset(monkeypatch):
    import openai_credit
    readiness._cache.clear()
    openai_credit.reopen()
    monkeypatch.setattr(openai_credit, '_alert', lambda *a: None)
    monkeypatch.setenv('OPENAI_API_KEY','test-unused')
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.setattr(readiness.provider_creds, '_row', lambda: {})
    yield
    readiness._cache.clear()
    openai_credit.reopen()

class Response:
    ok = False
    def json(self):return {'error':{'type':'insufficient_quota'}}

def test_no_checkout_when_no_editor_provider_can_answer(monkeypatch):
    monkeypatch.setattr(readiness.requests,'post',lambda *a,**k: Response())
    assert readiness.advanced_readiness() == {'ready':False,'model':'gpt-6.1-sol','reason':'provider_funding'}
    app=Flask(__name__)
    with app.app_context():
        result=paddle._new_plan_readiness('advanced')
        assert result[1] == 503
        assert result[0].get_json()['code'] == 'advanced_unavailable'
        assert paddle._new_plan_readiness('ai') is None
        assert paddle._new_plan_readiness('mcp_connect') is None

def test_ready_check_is_bounded_and_cached(monkeypatch):
    calls=[]
    class Ready:
        ok=True
        def json(self):return {'status':'completed','output':[{'type':'message'}]}
    def post(*args,**kwargs):
        calls.append(kwargs);return Ready()
    monkeypatch.setattr(readiness.requests,'post',post)
    assert readiness.advanced_readiness()['ready']
    assert readiness.advanced_readiness()['ready']
    assert len(calls)==1
    assert calls[0]['timeout']==(3.05,12)
    assert calls[0]['json']['model']=='gpt-6.1-sol'
    assert calls[0]['json']['reasoning'] == {'effort':'low'}
    assert calls[0]['json']['max_output_tokens'] == 512


def test_openai_out_of_credit_never_fails_checkout_when_claude_edits(monkeypatch):
    """15 Nov 2026: every plan edits on Claude and the rung falls back to Haiku, so an empty
    OpenAI wallet must not refuse a checkout, and a credit refusal is not re-probed per checkout."""
    import openai_credit
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-unused')
    calls = []
    class Quota:
        ok = False
        status_code = 429
        def json(self):
            return {'error': {'type': 'insufficient_quota', 'code': 'insufficient_quota',
                              'message': 'You have no credits remaining.'}}
    monkeypatch.setattr(readiness.requests, 'post', lambda *a, **k: calls.append(1) or Quota())
    got = readiness.advanced_readiness()
    assert got['ready'] is True and got['model'] == 'claude-haiku-5-5'
    assert got['fallback'] == 'provider_funding'
    assert not openai_credit.available()
    app = Flask(__name__)
    with app.app_context():
        assert paddle._new_plan_readiness('pro_2026') is None
        assert paddle._new_plan_readiness('max_2026') is None
    readiness._cache.clear()
    assert readiness.advanced_readiness()['ready'] is True
    assert len(calls) == 1            # latched: the next checkout sends no OpenAI call


def test_openai_transport_failure_never_fails_checkout_when_claude_edits(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-unused')
    def boom(*a, **k):
        raise readiness.requests.ConnectionError('down')
    monkeypatch.setattr(readiness.requests, 'post', boom)
    assert readiness.advanced_readiness()['ready'] is True
