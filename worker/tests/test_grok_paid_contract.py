"""Paid routing, real invoice metering, cache affinity and read recovery."""
import copy
import json
from types import SimpleNamespace
import pytest
import llm
import config
import model_prices
import agent_tools
import edl_read
import finishing_review
from schemas import default_edl


def context():
    ctx = object.__new__(agent_tools.ToolContext)
    ctx.tokens_in = ctx.tokens_out = ctx.tokens_cached_in = 0
    ctx.model_usage = {}
    ctx.images_generated = []
    ctx.gen_extra_cost_usd = 0
    return ctx


def test_actual_invoice_overrides_estimates_and_survives_checkpoint():
    ctx = context()
    ctx.add_usage('grok-4.6', 250000, 1000, 200000, 50, provider_cost_usd=.10)
    assert ctx.running_credits() == 20
    ctx.model_usage = json.loads(json.dumps(ctx.model_usage))
    ctx.add_usage('grok-4.6', 1000, 100, provider_cost_usd=.02)
    assert ctx.running_credits() == 24


def test_long_context_is_per_request_not_summed_turn():
    ctx = context()
    for _ in range(3): ctx.add_usage('grok-4.6', 100000, 0, 50000)
    assert ctx.running_credits() == 75
    threshold = context()
    threshold.add_usage('grok-4.6', 200000, 1000, 100000, 500)
    expected = model_prices.base_usage_cost('grok-4.6', 200000, 1000, 100000, 500)*2
    assert threshold.running_credits() == model_prices.usd_to_credits(expected)
    assert model_prices.context_multiplier('grok-4.6',199999) == 1


@pytest.mark.parametrize('raw,expected', [(1000000000,.1),(0,0),(None,None),('oops',None),(-1,None),(float('nan'),None)])
def test_provider_invoice_is_validated(raw, expected):
    assert llm.provider_cost_usd(SimpleNamespace(cost_in_usd_ticks=raw),'grok-4.6') == expected
    assert llm.provider_cost_usd(SimpleNamespace(cost_in_usd_ticks=raw),'gpt-test') is None
    assert llm.provider_cost_usd(SimpleNamespace(cost_in_usd_ticks=raw),'grok-imagine-image') is None


def test_record_persists_exact_invoice_without_mutating_callers():
    rows=[]
    old=llm.get_recorder()
    response={'content':'done'}
    try:
        llm.set_recorder(lambda *args: rows.append(args))
        llm.record('agent',{'model':'grok-4.6'},response,SimpleNamespace(cost_in_usd_ticks=1000000000))
    finally: llm.set_recorder(old)
    assert rows[0][2]['provider_cost_usd'] == .1
    assert response == {'content':'done'}


def test_cache_affinity_and_thread_cleanup(monkeypatch):
    requests=[]
    client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: requests.append(kw) or 'ok')))
    try:
        llm.set_turn_plan('ai',project_id=42)
        assert llm.create_with_dialect(client,'grok-4.6',[],max_tokens=20) == 'ok'
        assert requests[0]['extra_headers'] == {'x-grok-conv-id':'valmera-project-42'}
        assert llm.cache_affinity('gpt-other') == {}
        assert llm.cached_assistant_message(SimpleNamespace(reasoning_content='opaque'),'grok-4.6') == {'reasoning_content':'opaque'}
    finally: llm.clear_turn_plan()
    assert llm.cache_affinity('grok-4.6') == {}


def test_openai_priority_setting_never_leaks_into_grok(monkeypatch):
    monkeypatch.setattr(config,'OPENAI_BASE_URL','https://api.openai.com/v1')
    monkeypatch.setattr(config,'OPENAI_SERVICE_TIER','priority')
    assert 'service_tier' not in llm.completion_kwargs('grok-4.6',100,.2)


def test_unknown_section_reads_return_true_index_and_valid_requested_sections():
    row={'version':8,'json':default_edl(30)}
    before=copy.deepcopy(row)
    result=json.loads(edl_read.read_edl(row,30,None,['texts','story','beat_sync']))
    assert result['sections']['texts'] == row['json']['texts']
    assert result['unknown_sections'] == ['beat_sync','story']
    assert result['overview']['available_sections'] == sorted(row['json'])
    assert 'get_edit_plan' in result['notice']
    assert row == before


def test_quoted_asset_kind_and_sfx_skill_are_resolved(monkeypatch):
    db_calls=[]
    ctx=SimpleNamespace(project_id=4,db=SimpleNamespace(run=lambda *args: db_calls.append(args) or []))
    assert agent_tools.list_assets(ctx,'"all"') == 'No all assets in this project.'
    assert 'original' in db_calls[0][-1]
    ctx=SimpleNamespace(editing_metrics={})
    result=agent_tools.read_skill(ctx,'"sfx"')
    assert 'SOUND EFFECTS' in result
    assert 'ALREADY LOADED' in agent_tools.read_skill(ctx,'audio')


def test_finishing_status_changes_append_without_breaking_cache(monkeypatch):
    note=[finishing_review.DIRECTIVE_PREFIX+' EDL v1. Repair.']
    monkeypatch.setattr(finishing_review,'directive',lambda ctx:note[0])
    messages=[{'role':'user','content':'edit'}]
    finishing_review.refresh(None,messages,preserve_prefix=True)
    messages.append({'role':'tool','content':'saved v2'})
    prefix=copy.deepcopy(messages)
    finishing_review.refresh(None,messages,preserve_prefix=True)
    assert messages == prefix
    note[0]=finishing_review.DIRECTIVE_PREFIX+' EDL v2. Passed.'
    finishing_review.refresh(None,messages,preserve_prefix=True)
    assert messages[:len(prefix)] == prefix
    assert 'supersedes' in messages[-1]['content']


def test_discovery_schema_exposes_real_catalog(monkeypatch):
    monkeypatch.setattr(agent_tools,'_tool_disabled',lambda *a:False)
    schemas={s['function']['name']:s['function'] for s in agent_tools.openai_tools(names={'read_skill','list_assets'})}
    assert 'audio' in schemas['read_skill']['parameters']['properties']['name']['enum']
    assert schemas['list_assets']['parameters']['properties']['kind']['enum'] == ['music','image','clip','render','all']


@pytest.mark.parametrize('duration',[0.3,0.49])
def test_short_sound_effects_can_be_heard_without_padding_or_looping(monkeypatch,tmp_path,duration):
    spans=[]
    monkeypatch.setattr(llm,'audio_review_available',lambda:True)
    monkeypatch.setattr(agent_tools,'_resolve_media_asset',lambda *a:({'duration_s':duration,'storage_key':'sfx/one.mp3','meta':{}},None))
    monkeypatch.setattr(agent_tools,'_asset_local_path',lambda *a:'/source.mp3')
    monkeypatch.setattr(agent_tools.media,'extract_audio_clip',lambda src,start,end,dst:spans.append((start,end)))
    monkeypatch.setattr(llm,'ask_audio',lambda *a,**k:'A short click is audible.')
    ctx=SimpleNamespace(workdir=str(tmp_path),editing_metrics={})
    result=agent_tools.review_audio(ctx,asset_key='sfx/one.mp3',times=[0],span_s=duration)
    assert result.startswith('BOUNDED ACTUAL-AUDIO REVIEW')
    assert spans == [(0,duration)]
    spans.clear()
    assert agent_tools.review_audio(ctx,asset_key='sfx/one.mp3',times=[20]).startswith('REJECTED')
    assert spans == []
