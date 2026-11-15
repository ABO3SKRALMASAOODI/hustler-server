"""Real PostgreSQL cross-process admission against an isolated local test schema."""
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
import pytest
import psycopg2
import openai_budget as b


@pytest.fixture
def authority(monkeypatch, tmp_path):
    dsn = os.environ.get('QUEUE_TEST_DATABASE_URL')
    if not dsn:
        pytest.skip('local/CI test PostgreSQL required')
    params = psycopg2.extensions.parse_dsn(dsn)
    if params.get('host') not in ('127.0.0.1', 'localhost') or params.get('dbname') != 'valmera_test':
        pytest.fail('only explicitly local valmera_test is allowed')
    schema = 'cache_budget_' + uuid.uuid4().hex
    with psycopg2.connect(dsn) as c:
        with c.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{schema}"')
            cur.execute(f'CREATE TABLE "{schema}".app_kv(key text PRIMARY KEY,value text,updated_at timestamptz DEFAULT NOW())')
            cur.execute(f'CREATE TABLE "{schema}".spend_ledger(day date,provider text,lane text,usd numeric)')
    def connect():
        return psycopg2.connect(dsn, options=f'-c search_path={schema}')
    monkeypatch.setattr(b, 'connect', connect)
    monkeypatch.setenv('OPENAI_INTERNAL_CONFIG', str(tmp_path/'absent.json'))
    monkeypatch.setenv('VALMERA_OPENAI_SPEND_LOG', str(tmp_path/'absent.jsonl'))
    monkeypatch.delenv('VALMERA_SKILL', raising=False)
    monkeypatch.delenv('VALMERA_RUN', raising=False)
    monkeypatch.delenv('GAUNTLET_RUN', raising=False)
    try:
        yield connect
    finally:
        with psycopg2.connect(dsn) as c:
            with c.cursor() as cur: cur.execute(f'DROP SCHEMA "{schema}" CASCADE')


def test_concurrent_callers_share_reservations_and_restart_state(authority):
    def attempt(i):
        try:
            return b.reserve({'model':'gpt-6-sol'}, 'test', lane='replay', run='one-run', ceiling_usd=2)
        except b.OverBudget:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        leases = [x for x in pool.map(attempt, range(16)) if x]
    assert len(leases) == 1
    leases[0].settle(usd=.01)
    b._stopped.clear()  # simulate another process after the first lane stopped
    b.reserve({'model':'gpt-6-sol'}, 'test', lane='replay', run='one-run', ceiling_usd=2)
    with pytest.raises(b.OverBudget):
        b.reserve({'model':'gpt-6-sol'}, 'test', lane='replay', run='one-run', ceiling_usd=2)


def test_shared_daily_legacy_usage_and_customer_exemption(authority):
    with authority() as c:
        with c.cursor() as cur:
            cur.execute("INSERT INTO spend_ledger VALUES(CURRENT_DATE,'openai','customer',1000),(CURRENT_DATE,'openai','replay',29)")
    x = b.reserve({'model':'gpt-6-sol'}, 'test', lane='workflow', run='film', ceiling_usd=1)
    with pytest.raises(b.OverBudget):
        b.reserve({'model':'gpt-6-sol'}, 'test', lane='internal', run='lane', ceiling_usd=.01)
    x.settle(usd=.1)
    b._stopped.clear()
    b.reserve({'model':'gpt-6-sol'}, 'test', lane='internal', run='lane', ceiling_usd=.5)


def test_configuration_kill_and_outage_fail_closed(authority, monkeypatch):
    with authority() as c:
        with c.cursor() as cur:
            cur.execute('INSERT INTO app_kv(key,value) VALUES(%s,%s)', ('spend_fence',json.dumps({'openai':{'enabled':False}})))
    with pytest.raises(b.OverBudget, match='kill switch'):
        b.reserve({'model':'gpt-6-sol'}, 'test', lane='internal', run='lane', ceiling_usd=.01)
    b._stopped.clear()
    monkeypatch.setattr(b,'connect',lambda: (_ for _ in ()).throw(RuntimeError('SECRET must not be printed')))
    with pytest.raises(b.OverBudget, match='budget unavailable') as e:
        b.reserve({'model':'gpt-6-sol'}, 'test', lane='internal', run='lane', ceiling_usd=.01)
    assert 'SECRET' not in str(e.value)
    assert b.reserve({'model':'unpriced'}, 'customer',lane='customer').token is None


def test_real_model_seams_stop_before_any_transport(authority, monkeypatch):
    import importlib.util,time
    from pathlib import Path
    from types import SimpleNamespace as NS
    import config,llm,edit_rung,httpx,requests
    from score import call_openai
    from footage import hear
    root=Path(__file__).resolve().parents[2]
    def load(name,path):
        spec=importlib.util.spec_from_file_location(name,path)
        m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
    shell=load('budget_shell',root/'plugins/valmera/skills/_shell/llm.py')
    writer=load('budget_writer',root/'plugins/valmera/skills/valmera-stories/writer.py')
    monkeypatch.setenv('OPENAI_API_KEY','synthetic')
    monkeypatch.setattr(config,'OPENAI_API_KEY','synthetic')
    monkeypatch.setattr(config,'OPENAI_BASE_URL','https://api.openai.com/v1')
    monkeypatch.setattr(llm,'fence',lambda *a:None)  # isolate the new hard admission from the older soft fence
    def sent(*a,**k):pytest.fail('internal request reached provider transport')
    monkeypatch.setattr(requests,'post',sent)
    monkeypatch.setattr(httpx,'post',sent)
    monkeypatch.setattr(httpx.Client,'stream',sent)
    with authority() as c:
        with c.cursor() as cur:cur.execute('INSERT INTO app_kv(key,value) VALUES(%s,%s)',('spend_fence','{"openai":{"enabled":false}}'))
    body={'model':'gpt-6-sol','input':'test','max_output_tokens':512}
    client=NS(base_url='https://api.openai.com/v1',chat=NS(completions=NS(create=sent)))
    calls=[lambda:llm.responses_create('https://api.openai.com/v1','synthetic','gpt-6-sol',[],[],max_tokens=512),
           lambda:llm.create_with_dialect(client,'gpt-6-sol',[],max_tokens=512),
           lambda:edit_rung._post(body,5),
           lambda:call_openai.stream('gpt-6-sol','rubric','data',max_tokens=512,effort='low',purpose='test',deadline=time.monotonic()+10),
           lambda:shell.ask_openai('rubric','data',max_tokens=512),
           lambda:writer.ask_openai('rubric','data',max_tokens=512),
           lambda:hear._openai_clip('/not-opened.wav',4)]
    with b.scope('replay','run'):
        for call in calls:
            b._stopped.clear()
            with pytest.raises(b.OverBudget):call()
