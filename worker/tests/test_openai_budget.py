"""Offline admission, concurrency and customer isolation. No provider calls."""
import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
import openai_budget as b


def cfg(**kw):
    return b.limits(kw, {})


def test_combined_day_across_replays_skills_and_tests():
    s = {}
    for lane, amount in [('replay', 3), ('workflow', 8), ('workflow', 8), ('workflow', 8), ('internal', 3)]:
        t = b.reserve_state(s, cfg(), lane, str(len(s.get('runs', {}))), b._money(amount), '2026-11-30')
        b.settle_state(s, t, b._money(amount))
    with pytest.raises(b.OverBudget, match='today'):
        b.reserve_state(s, cfg(), 'workflow', 'new', 1, '2026-11-30')
    assert s['days']['2026-11-30'] == 30_000_000


def test_atomic_reservations_prevent_concurrent_overshoot():
    s, lock = {}, threading.Lock()
    def attempt(i):
        with lock:
            try:
                return b.reserve_state(s, cfg(), 'replay', 'same', 2_000_000, '2026-11-30')
            except b.OverBudget:
                return None
    with ThreadPoolExecutor(max_workers=16) as pool:
        result = list(pool.map(attempt, range(40)))
    assert sum(x is not None for x in result) == 1
    assert sum(x['amount'] for x in s['pending'].values()) == 2_000_000


def test_run_does_not_reset_at_midnight_and_pending_is_not_free():
    s = {}
    token = b.reserve_state(s, cfg(), 'replay', 'a', 3_000_000, '2026-11-30')
    with pytest.raises(b.OverBudget):
        b.reserve_state(s, cfg(), 'replay', 'a', 1, '2026-12-01')
    b.settle_state(s, token, 500_000)
    b.settle_state(s, token, 500_000)
    assert s['runs']['a'] == 500_000
    assert s['days']['2026-11-30'] == 500_000
    b.reserve_state(s, cfg(), 'replay', 'a', 2_500_000, '2026-12-01')


@pytest.mark.parametrize('lane,cap', [('replay', 3), ('workflow', 8), ('internal', 3)])
def test_run_caps_and_legacy_day_total(lane, cap):
    with pytest.raises(b.OverBudget):
        b.reserve_state({}, cfg(), lane, 'x', b._money(cap + .01), 'day')
    with pytest.raises(b.OverBudget):
        b.reserve_state({}, cfg(), lane, 'x', 1, 'day', baseline=30_000_000)


def test_kill_switch_and_local_cannot_expand_central_cap():
    c = b.limits({'internal_day_usd': 20}, {'internal_day_usd': 100, 'workflow_run_usd': 1})
    assert c['internal_day_usd'] == 20_000_000 and c['workflow_run_usd'] == 1_000_000
    with pytest.raises(b.OverBudget, match='kill switch'):
        b.reserve_state({}, cfg(enabled=False), 'internal', 'x', 1, 'day')
    assert b.limits({}, {'enabled': False})['enabled'] is False


def test_customer_never_reads_config_ledger_or_price_card(monkeypatch):
    monkeypatch.setattr(b, 'connect', lambda: pytest.fail('customer accessed budget DB'))
    monkeypatch.setattr(b, 'local_config', lambda: pytest.fail('customer read config'))
    with b.scope('customer', 'unused'):
        lease = b.reserve({'model': 'unknown'}, 'customer')
        lease.settle(usd=100000)
    assert lease.token is None


def test_bound_breach_latches_and_unknown_calls_stay_reserved():
    s = {}
    t = b.reserve_state(s, cfg(), 'internal', 'x', 1000, 'day')
    assert len(s['pending']) == 1  # crash/timeout has no release operation
    b.settle_state(s, t, 1001)
    assert s['bound_breach'] is True


@pytest.mark.parametrize('v', [float('nan'), float('inf'), -1])
def test_invalid_money_fails_closed(v):
    with pytest.raises(b.OverBudget): b._money(v)


def test_unknown_model_unbounded_tools_and_output_stop_before_transport():
    base = {'model': 'gpt-6-sol', 'input': 'test', 'max_output_tokens': 512}
    assert b.upper_bound(base) > 0
    for body in [dict(base, model='unknown'), {k:v for k,v in base.items() if k != 'max_output_tokens'},
                 dict(base, tools=[{'type':'web_search'}])]:
        with pytest.raises(b.OverBudget): b.upper_bound(body)
    assert b.upper_bound(dict(base, tools=[{'type':'web_search'}], max_tool_calls=8)) > 8


def test_replay_uses_one_run_identity_in_all_processes(monkeypatch):
    monkeypatch.delenv('VALMERA_SKILL', raising=False)
    monkeypatch.delenv('VALMERA_RUN', raising=False)
    monkeypatch.setenv('SPEND_LANE', 'replay')
    monkeypatch.setenv('GAUNTLET_RUN', 'fixed')
    first = b.identity()
    with b.scope('replay', 'concierge:55'):
        assert b.identity() == first


def test_definite_http_refusal_releases_but_timeout_does_not(monkeypatch):
    class Lease:
        def settle(self, **kw): settled.append(kw)
    settled = []
    monkeypatch.setattr(b, 'reserve', lambda *a, **kw: Lease())
    for code in [429, 500]:
        b.post(lambda *a, **k: NS(status_code=code), 'https://api.openai.com/v1/responses', json={})
    assert settled == [{'usd': 0}]
    with pytest.raises(TimeoutError):
        with b.call({}, 'test'): raise TimeoutError()
    assert len(settled) == 1


def test_mirrors_are_identical():
    root = Path(__file__).resolve().parents[2]
    assert (root/'worker/openai_budget.py').read_bytes() == (root/'backend/openai_budget.py').read_bytes()
    assert (root/'worker/openai_budget.py').read_bytes() == (root/'plugins/valmera/skills/_shell/openai_budget.py').read_bytes()


def test_nested_pools_keep_the_same_run_and_customer_identity(monkeypatch):
    monkeypatch.delenv('VALMERA_SKILL', raising=False)
    monkeypatch.delenv('VALMERA_RUN', raising=False)
    monkeypatch.delenv('GAUNTLET_RUN', raising=False)
    for lane in ('customer','replay','workflow'):
        with b.scope(lane,'same-film'):
            expected=b.identity()
            def nested():
                with b.ThreadPoolExecutor(max_workers=1) as pool:
                    return pool.submit(b.identity).result()
            with b.ThreadPoolExecutor(max_workers=2) as pool:
                assert pool.submit(nested).result()==expected


def test_budget_stop_is_not_a_provider_fallback(monkeypatch):
    monkeypatch.setattr(b,'_reserve',lambda *a,**k: (_ for _ in ()).throw(b.OverBudget('daily cap')))
    with b.scope('replay','one'):
        with pytest.raises(b.OverBudget): b.reserve({},'test')
        with pytest.raises(b.OverBudget): b.raise_if_stopped()
    with b.scope('customer','two'):
        b.raise_if_stopped()


def test_legacy_workflow_usage_is_read_without_treating_unknown_as_free(tmp_path,monkeypatch):
    path=tmp_path/'spend.jsonl'
    monkeypatch.setenv('VALMERA_OPENAI_SPEND_LOG',str(path))
    path.write_text(json.dumps({'ts':'2026-11-30T01:00:00Z','usd':1.25})+'\n')
    assert b.legacy_workflow_total('2026-11-30')==1_250_000
    with path.open('a') as f:f.write(json.dumps({'ts':'2026-11-30T01:01:00Z','usd':None,'billing_unconfirmed':True})+'\n')
    with pytest.raises(b.OverBudget):b.legacy_workflow_total('2026-11-30')


def test_priority_extra_body_is_included_in_admission():
    body={'model':'gpt-6-sol','input':'fixed','max_output_tokens':512}
    assert b.upper_bound(dict(body,extra_body={'service_tier':'priority'})) >= 2*b.upper_bound(body)
