import pytest
import config
import llm
import model_prices


def test_luna6_reasoning_tools_use_responses_from_first_call():
    assert config.EDITOR_MODEL == 'gpt-6-luna'
    assert llm.responses_available(config.EDITOR_MODEL, 'https://api.openai.com/v1')
    assert llm.completion_kwargs('gpt-6-luna', max_tokens=100, temperature=.3) == {
        'max_completion_tokens': 100}


@pytest.mark.parametrize('tier,factor', [('default',1),('priority',2),('flex',.5)])
def test_luna6_reports_cache_writes_without_double_billing_reasoning(tier,factor):
    usage = llm._RespUsage({'input_tokens': 10000,'output_tokens':1000,
        'input_tokens_details': {'cached_tokens':4000,'cache_write_tokens':3000},
        'output_tokens_details': {'reasoning_tokens':800},'service_tier':tier})
    expected = (3000*.1 + 4000*.01 + 3000*.125 + 1000*.5)/1e6*factor
    assert llm.provider_cost_usd(usage,'gpt-6-luna') == pytest.approx(expected)


def test_luna6_long_context_prices_input_and_output_separately():
    assert model_prices.luna6_usage_cost(272000,1000) == pytest.approx(.0277)
    assert model_prices.luna6_usage_cost(272001,1000) == pytest.approx(.0551502)
    assert model_prices.luna6_usage_cost(1000,0,2000,2000) == pytest.approx(.00001)


def test_luna6_record_keeps_auditable_cost_categories():
    rows=[]
    usage=llm._RespUsage({'input_tokens':1000,'output_tokens':100,
        'input_tokens_details': {'cache_write_tokens':800}})
    llm.set_recorder(lambda *args: rows.append(args))
    try: llm.record('agent',{'model':'gpt-6-luna'},{'text':'ok'},usage)
    finally: llm.set_recorder(None)
    assert rows[0][2]['cache_write_in'] == 800
    assert rows[0][2]['provider_cost_usd'] == pytest.approx(.00017)
