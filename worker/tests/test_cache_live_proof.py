"""Only offline proof-script functions. The live entry point is never run."""
import copy
import datetime as dt
import importlib.util
from pathlib import Path
import pytest
import openai_budget

P=Path(__file__).resolve().parents[2]/'scripts/cache_audit/live_proof_1130.py'
spec=importlib.util.spec_from_file_location('live_proof_test',P)
proof=importlib.util.module_from_spec(spec);spec.loader.exec_module(proof)


def funded():
    return dict(enabled=True,credit_available=True,refill_confirmed_at='2026-11-30T00:00:00Z')


def test_date_is_not_funding_and_all_gates_must_be_open():
    now=dt.datetime(2026,11,30,12,tzinfo=dt.timezone.utc)
    proof.assert_ready(funded(),funded(),now)
    with pytest.raises(openai_budget.OverBudget):proof.assert_ready(funded(),funded(),now-dt.timedelta(days=1))
    for bad in [{},dict(funded(),credit_available=False),dict(funded(),enabled=False),dict(funded(),refill_confirmed_at='2026-11-15T00:00:00Z')]:
        with pytest.raises(openai_budget.OverBudget):proof.assert_ready(bad,funded(),now)
        with pytest.raises(openai_budget.OverBudget):proof.assert_ready(funded(),bad,now)
    with pytest.raises(openai_budget.OverBudget):proof.assert_ready(funded(),funded(),now,latch_open=False)


def test_identical_twice_actual_usage_and_cap(monkeypatch):
    reserved=[];sent=[]
    def reserve(body,purpose,**kw):reserved.append(kw);return openai_budget.Lease()
    monkeypatch.setattr(openai_budget,'reserve',reserve)
    def send(body):
        sent.append(copy.deepcopy(body))
        return dict(id=str(len(sent)),status='completed',usage=dict(input_tokens=2048,output_tokens=2,
                    input_tokens_details=dict(cached_tokens=0 if len(sent)==1 else 2048)))
    rows=proof.run_pair(dict(site='site',body=dict(model='gpt-6-sol',input='same',max_output_tokens=512)),send)
    assert sent[0]==sent[1]
    assert [r['cached_share'] for r in rows]==[0,1]
    assert rows[1]['usd']<rows[0]['usd']
    assert all(x['run_cap_usd']==2.95 and x['run']==proof.RUN_ID for x in reserved)


def test_no_second_call_after_failure(monkeypatch):
    monkeypatch.setattr(openai_budget,'reserve',lambda *a,**kw:openai_budget.Lease())
    sent=[]
    def send(body):sent.append(body);raise RuntimeError('quota exhausted')
    with pytest.raises(RuntimeError):proof.run_pair(dict(site='site',body={'model':'gpt-6-sol'}),send)
    assert len(sent)==1


def test_real_fixture_builders_are_offline_and_repeatable(monkeypatch):
    import requests,httpx
    monkeypatch.setattr(requests,'post',lambda *a,**k:pytest.fail('network sent'))
    monkeypatch.setattr(httpx,'post',lambda *a,**k:pytest.fail('network sent'))
    one,two=proof.fixtures(),proof.fixtures()
    assert one==two
    sites={r['site'] for r in one}
    assert {'agent','director_direct','footage_look','named_check','placement_check:check','wf4_picture_check','writer:find ideas','writer:write'} <= sites
    assert all(x['body']['max_output_tokens']==512 for x in one)
    assert all(x['body'].get('tool_choice')=='none' for x in one if x['body'].get('tools'))
