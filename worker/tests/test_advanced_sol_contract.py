import pytest
import llm, config, model_prices

@pytest.mark.parametrize('plan', ['ai','ai_pro','ai_max','mcp','plus','pro','ultra','titan','ace'])
def test_legacy_model_is_unchanged(monkeypatch,plan):
    monkeypatch.setattr(config,'OPENAI_API_KEY','unit-test')
    monkeypatch.setattr(llm,'client',lambda: object())
    assert llm.agent_client_for(True,plan)[1] == config.EDITOR_MODEL
    assert llm.agent_lanes_for(True,plan)[0]['model'] == config.EDITOR_MODEL

def test_advanced_is_sol_without_cheap_fallback(monkeypatch):
    monkeypatch.setattr(config,'OPENAI_API_KEY','unit-test')
    monkeypatch.setattr(llm,'client',lambda: object())
    assert llm.agent_client_for(True,'advanced')[1]=='gpt-6-sol'
    assert [lane['model'] for lane in llm.agent_lanes_for(True,'advanced')]==['gpt-6-sol']
    monkeypatch.setattr(config,'OPENAI_API_KEY','')
    with pytest.raises(RuntimeError): llm.agent_client_for(True,'advanced')

def test_sol_cost_accounts_for_cache_writes_and_long_context():
    cost=model_prices.luna6_usage_cost(300000,10000,100000,50000,model='gpt-6-sol')
    assert cost == pytest.approx(((150000*2+100000*.2+50000*2.5)*2 + 10000*10*1.5)/1e6)
    usage={'input_tokens':300000,'output_tokens':10000,'input_tokens_details':{'cached_tokens':100000,'cache_write_tokens':50000},'service_tier':'default'}
    assert llm.provider_cost_usd(usage,'gpt-6-sol') == pytest.approx(cost)


def test_sol_starts_with_known_openai_request_dialect():
    kwargs=llm.completion_kwargs('gpt-6-sol',max_tokens=100,temperature=.5)
    assert kwargs['max_completion_tokens']==100
    assert 'max_tokens' not in kwargs
    assert 'temperature' not in kwargs
