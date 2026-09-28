import pytest
import provider_readiness as readiness
from routes import paddle
from flask import Flask

@pytest.fixture(autouse=True)
def reset(monkeypatch):
    readiness._cache.clear()
    monkeypatch.setenv('OPENAI_API_KEY','test-unused')
    yield
    readiness._cache.clear()

class Response:
    ok = False
    def json(self):return {'error':{'type':'insufficient_quota'}}

def test_no_checkout_when_contracted_provider_has_no_funds(monkeypatch):
    monkeypatch.setattr(readiness.requests,'post',lambda *a,**k: Response())
    assert readiness.advanced_readiness() == {'ready':False,'model':'gpt-6-sol','reason':'provider_funding'}
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
    assert calls[0]['json']['model']=='gpt-6-sol'
