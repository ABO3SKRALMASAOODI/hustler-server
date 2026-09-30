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
    assert llm.agent_client_for(True,'advanced')[1]=='gpt-6.1-sol'
    assert [lane['model'] for lane in llm.agent_lanes_for(True,'advanced')]==['gpt-6.1-sol']
    monkeypatch.setattr(config,'OPENAI_API_KEY','')
    with pytest.raises(RuntimeError): llm.agent_client_for(True,'advanced')

def test_sol_cost_accounts_for_cache_writes_and_long_context():
    cost=model_prices.luna6_usage_cost(300000,10000,100000,50000,model='gpt-6.1-sol')
    assert cost == pytest.approx(((150000*2+100000*.1+50000*2.5)*2 + 10000*10*1.5)/1e6)
    usage={'input_tokens':300000,'output_tokens':10000,'input_tokens_details':{'cached_tokens':100000,'cache_write_tokens':50000},'service_tier':'default'}
    assert llm.provider_cost_usd(usage,'gpt-6.1-sol') == pytest.approx(cost)


def test_sol_starts_with_known_openai_request_dialect():
    kwargs=llm.completion_kwargs('gpt-6.1-sol',max_tokens=100,temperature=.5)
    assert kwargs['max_completion_tokens']==100
    assert 'max_tokens' not in kwargs
    assert 'temperature' not in kwargs


def test_advanced_vision_uses_the_same_upgraded_model(monkeypatch):
    monkeypatch.setattr(config, 'OPENAI_API_KEY', 'unit-test')
    monkeypatch.setattr(llm, 'client', lambda: object())
    assert llm.vision_client_for('advanced')[1] == 'gpt-6.1-sol'


def test_sol61_requires_responses_even_with_legacy_disable_or_dead_latch(monkeypatch):
    monkeypatch.setattr(config, 'AGENT_RESPONSES_LANE', False)
    monkeypatch.setattr(llm, '_responses_dead', {'gpt-6.1-sol'})
    assert llm.responses_available('gpt-6.1-sol', 'https://api.openai.com/v1', effort='high')
    assert not llm.tools_need_effort_none('gpt-6.1-sol')


@pytest.mark.parametrize('effort,expected', [('high','high'), ('none','low'), ('minimal','low'), (None,'low')])
def test_sol61_cache_request_and_supported_reasoning(monkeypatch, effort, expected):
    bodies=[]
    class Response:
        status_code=200
        def json(self):
            return {'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'OK'}]}],
                    'usage':{'input_tokens':1600,'output_tokens':10,'input_tokens_details':{'cached_tokens':1200,'cache_write_tokens':400}},
                    'service_tier':'default'}
    monkeypatch.setattr(llm.requests, 'post', lambda *a,**kw: bodies.append(kw['json']) or Response())
    llm.set_turn_plan('advanced', project_id=123)
    try:
        response=llm.responses_create('https://api.openai.com/v1','test','gpt-6.1-sol',
            [{'role':'user','content':'Check'}],[],effort=effort,tool_choice='none')
    finally:
        llm.clear_turn_plan()
    body=bodies[0]
    assert body['prompt_cache_options'] == {'ttl':'30m'}
    assert body['prompt_cache_key'] == 'valmera-project-123'
    assert 'prompt_cache_retention' not in body
    assert body['reasoning'] == {'effort':expected}
    assert body['tool_choice'] == 'none'
    assert response.usage.prompt_tokens_details == {'cached_tokens':1200,'cache_write_tokens':400}
    assert llm.provider_cost_usd(response.usage,'gpt-6.1-sol') == pytest.approx(.00122)


def test_sol61_reply_regeneration_keeps_tool_schemas_on_responses(monkeypatch):
    from types import SimpleNamespace
    calls=[]
    monkeypatch.setattr(llm, 'responses_create', lambda *args,**kwargs: calls.append((args,kwargs)) or 'ok')
    client=SimpleNamespace(base_url='https://api.openai.com/v1/',api_key='unit-test')
    assert llm.create_with_dialect(client,'gpt-6.1-sol',[],tools=[],tool_choice='none',max_tokens=100,temperature=.5) == 'ok'
    assert calls[0][1]['tool_choice'] == 'none'
    assert calls[0][1]['effort'] == 'high'


@pytest.mark.parametrize('tier,multiplier',[('default',1),('priority',2),('flex',.5)])
def test_sol61_usage_record_prices_reads_and_writes_separately(tier,multiplier):
    rows=[]
    usage=llm._RespUsage({'input_tokens':10000,'output_tokens':1000,
        'input_tokens_details':{'cached_tokens':4000,'cache_write_tokens':3000},
        'output_tokens_details':{'reasoning_tokens':800},'service_tier':tier})
    llm.set_recorder(lambda *args: rows.append(args))
    try: llm.record('agent',{'model':'gpt-6.1-sol'},{'text':'ok'},usage)
    finally: llm.set_recorder(None)
    recorded=rows[0][2]
    assert recorded['provider_cost_usd'] == pytest.approx((3000*2+4000*.1+3000*2.5+1000*10)/1e6*multiplier)
    assert recorded['cache_write_in'] == 3000
    assert recorded['service_tier'] == tier
    assert recorded['cost_basis'] == 'reported_tokens_official_rates'
    sql=model_prices.row_cost_sql({'in':1,'cached_in':1,'out':1})
    assert "'gpt-6.1-sol'" in sql.split('AND jsonb_typeof')[0]


def test_prior_sol_usage_keeps_its_historical_cache_rate():
    assert model_prices.luna6_usage_cost(10000,0,10000,model='gpt-6-sol') == pytest.approx(.002)
    assert model_prices.luna6_usage_cost(10000,0,10000,model='gpt-6.1-sol') == pytest.approx(.001)
