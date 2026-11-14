import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import openai_cache as cache


def test_cache_boundaries_preserve_content_and_do_not_mutate_input():
    body = {'model': 'gpt-6.1-sol', 'instructions': 'fixed rules',
            'input': [{'role': 'user', 'content': [{'type': 'input_text', 'text': 'film A'}]}]}
    original = copy.deepcopy(body)
    a = cache.responses(body, 'director')
    body['input'][0]['content'][0]['text'] = 'film B'
    b = cache.responses(body, 'director')
    assert a['input'][0] == b['input'][0]
    assert a['input'][1] == original['input'][0]
    assert a['input'][0]['content'][0]['prompt_cache_breakpoint']['ttl'] == '30m'
    assert b['input'][1]['content'][0]['text'] == 'film B'
    assert original['instructions'] == body['instructions']
    assert 'prompt_cache_breakpoint' not in body['input'][0]['content'][0]
    assert a['prompt_cache_options']['mode'] == 'explicit'


def test_editor_kernel_boundary_survives_changing_project_state():
    body = {'model': 'gpt-6.1-sol', 'input': [
        {'role': 'system', 'content': 'kernel'}, {'role': 'system', 'content': 'tools directory'},
        {'role': 'system', 'content': 'state'}, {'role': 'user', 'content': 'edit'}]}
    out = cache.responses(body, 'editor', conversation=True)
    assert out['prompt_cache_options']['mode'] == 'implicit'
    assert all('prompt_cache_breakpoint' in m['content'][0] for m in out['input'][:2])
    assert out['input'][2:] == body['input'][2:]


def test_unsupported_models_never_get_extended_fields():
    assert cache.chat_options('claude-haiku-5-5', 'role') == {}
    assert cache.chat_options('gpt-4o-mini', 'role') == {'prompt_cache_key': 'valmera:role'}
    assert cache.responses({'model': 'gpt-5.5', 'instructions': 'x'}, 'role')['prompt_cache_retention'] == '24h'
    assert 'prompt_cache_retention' not in cache.responses({'model': 'gpt-6-sol'}, 'role')


def test_real_usage_write_lifetimes_are_charged_once():
    u = {'input_tokens': 12000, 'input_tokens_details': {'cached_tokens': 4000,
         'cache_write_tokens': 7000, 'cache_write_tokens_details': {'ttl_30m_tokens': 3000, 'ttl_12h_tokens': 4000}},
         'output_tokens': 100}
    # 1000 ordinary, 4000 cached, 3000 short writes, 4000 long writes; output includes reasoning.
    expected = (1000*2 + 4000*.1 + 3000*2*1.25 + 4000*2*2.5 + 100*10) / 1e6
    assert cache.cost('gpt-6.1-sol', u) == pytest.approx(expected)
    from score.call_openai import Usage
    from edit_rung import _RespUsage
    assert Usage('gpt-6.1-sol', u).provider_cost_usd == pytest.approx(expected)
    assert _RespUsage('gpt-6.1-sol', u).provider_cost_usd == pytest.approx(expected)


def test_parallel_workflow_calls_append_complete_attributed_lines(tmp_path, monkeypatch):
    p = tmp_path / 'spend.jsonl'
    monkeypatch.setenv('VALMERA_OPENAI_SPEND_LOG', str(p))
    monkeypatch.setenv('VALMERA_SKILL', 'wf1')
    monkeypatch.setenv('VALMERA_RUN', 'film-1')
    u = {'input_tokens': 2000, 'input_tokens_details': {'cached_tokens': 1700}, 'output_tokens': 10}
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: cache.record('gpt-6-sol', 'named_check', u, request_id=str(i)), range(40)))
    rows = [json.loads(x) for x in p.read_text().splitlines()]
    assert len(rows) == len({r['request_id'] for r in rows}) == 40
    assert all(r['skill'] == 'wf1' and r['run'] == 'film-1' and r['cached_tokens'] == 1700 for r in rows)
    assert all(r['usd'] == pytest.approx(.00104) for r in rows)


def test_service_copies_are_identical():
    root = Path(__file__).resolve().parents[2]
    worker = (root / 'worker/openai_cache.py').read_bytes()
    assert worker == (root / 'backend/openai_cache.py').read_bytes()
    assert worker == (root / 'plugins/valmera/skills/_shell/openai_cache.py').read_bytes()


def test_workflow_terminal_responses_are_logged_before_failures(tmp_path, monkeypatch):
    import importlib.util
    import httpx
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location('workflow_llm', root / 'plugins/valmera/skills/_shell/llm.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    monkeypatch.setenv('VALMERA_OPENAI_SPEND_LOG', str(tmp_path / 'spend.jsonl'))
    monkeypatch.setenv('VALMERA_SKILL', 'wf3')
    monkeypatch.setenv('VALMERA_RUN', 'story-1')
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, content=('data: ' + json.dumps({'type':'response.failed','response':{
            'id':'resp_failed', 'status':'failed', 'error':{'code':'test'},
            'usage':{'input_tokens':2000,'input_tokens_details':{'cached_tokens':1800},'output_tokens':20}}})+'\n\n').encode())
    real = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(RuntimeError, match='openai failed'):
        module.ask_openai('same instructions', 'story input')
    row = json.loads((tmp_path / 'spend.jsonl').read_text())
    assert row['request_id'] == 'resp_failed' and row['cached_tokens'] == 1800
    assert row['status'] == 'failed' and row['usd'] > 0 and row['run'] == 'story-1'
    assert requests[0]['input'][0]['role'] == 'developer'


def test_a_small_model_does_not_get_an_unsupported_24h_parameter():
    assert 'prompt_cache_retention' not in cache.chat_options('gpt-5-mini', 'role')
    assert 'prompt_cache_retention' not in cache.chat_options('gpt-4.1-mini', 'role')


def test_transcription_prices_reported_tokens_not_duration_estimate():
    raw = {'type':'tokens', 'input_tokens':120, 'input_token_details':{'audio_tokens':120, 'text_tokens':0}, 'output_tokens':36}
    assert cache.cost('gpt-4o-transcribe', raw) == pytest.approx(.00066)


def test_narration_script_is_a_cache_boundary_before_the_changing_chunk():
    import importlib.util
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location('spoken_cache_test', root / 'plugins/valmera/skills/_shell/narrate/spoken.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bodies = []
    def call(system, user, model):
        bodies.append(user)
        return {'output':[{'type':'message','content':[{'type':'output_text','text':'{"spoken":"one"}'}]}]}
    from unittest.mock import patch
    with patch.dict('os.environ', {'OPENAI_API_KEY':'test-only'}), patch.object(module, '_call', call):
        module.spoken_form('1 '*601, 'same whole film script', ['Skylab'])
    assert len(bodies) == 2
    assert bodies[0][0] == bodies[1][0]
    assert bodies[0][0]['prompt_cache_breakpoint']['ttl'] == '30m'
    assert bodies[0][1] != bodies[1][1]


@pytest.mark.parametrize('relative', ['_shell/llm.py', 'valmera-stories/writer.py'])
@pytest.mark.parametrize('terminal', [False, True])
def test_each_interrupted_workflow_attempt_is_metered(tmp_path, monkeypatch, relative, terminal):
    import importlib.util
    import httpx
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location('interrupted_workflow', root / 'plugins/valmera/skills' / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    monkeypatch.setenv('VALMERA_OPENAI_SPEND_LOG', str(tmp_path / 'spend.jsonl'))
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    calls = []
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            if terminal:
                yield ('data: ' + json.dumps({'type':'response.completed','response':{
                    'id':'resp_done','status':'completed','usage':{'input_tokens':2000,'output_tokens':20},
                    'output':[]}}) + '\n\n').encode()
                raise httpx.ReadError('connection ended after terminal event')
            yield b'data: [DONE]\n\n'
    def handler(request):
        calls.append(request)
        return httpx.Response(200, stream=Stream())
    real = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    if terminal:
        module.ask_openai('same instructions', 'story input')
    else:
        with pytest.raises(RuntimeError, match='stream ended without a response'):
            module.ask_openai('same instructions', 'story input')
    rows = [json.loads(line) for line in (tmp_path / 'spend.jsonl').read_text().splitlines()]
    assert len(calls) == len(rows) == (1 if terminal else 4)
    if terminal:
        assert rows[0]['request_id'] == 'resp_done' and rows[0]['usd'] > 0
    else:
        assert [r['attempt'] for r in rows] == [1,2,3,4]
        assert all(r['usd'] is None and r['billing_unconfirmed'] for r in rows)
