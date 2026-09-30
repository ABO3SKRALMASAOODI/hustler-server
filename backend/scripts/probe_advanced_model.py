"""Bounded synthetic tool/cache release check using the configured provider key.

No customer projects, media, billing records, or credits are touched. Emits only
model metadata, usage counts and computed cost, never prompts or credentials.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'worker'))
import config
import llm


def main():
    lane = llm.paid_editor_lanes('advanced')[0]
    model = lane['model']
    prefix = 'Validate a synthetic editing connection. Call check_connection with ok=true.\n'
    prefix += '\n'.join(
        f'Editing rule {n}: preserve dialogue timing, source identity, caption readability, '
        'and original sound; use only the explicit editing instructions.' for n in range(75))
    messages = [{'role':'system','content':prefix},
                {'role':'user','content':'Check the connection now.'}]
    tools = [{'type':'function','function':{
        'name':'check_connection','description':'Harmless connection check; makes no changes.',
        'parameters':{'type':'object','properties':{'ok':{'type':'boolean'}},
                      'required':['ok'],'additionalProperties':False}}}]
    hits = []
    llm.set_turn_plan('advanced', project_id=0)
    try:
        for attempt in range(3):
            started = time.monotonic()
            response = llm.responses_create(
                lane['base_url'], lane['api_key'], model, messages, tools,
                max_tokens=1500, effort='high', timeout=55, tool_choice='required')
            usage = response.usage
            calls = response.choices[0].message.tool_calls or []
            assert any(c.function.name == 'check_connection'
                       and json.loads(c.function.arguments).get('ok') is True for c in calls), 'Tool call failed'
            cached = llm.cached_input_tokens(usage)
            hits.append(cached)
            print(json.dumps({'attempt':attempt+1, 'model':model,
                'seconds':round(time.monotonic()-started,2),
                'input_tokens':usage.prompt_tokens, 'output_tokens':usage.completion_tokens,
                'cached_tokens':cached,
                'cache_write_tokens':usage.prompt_tokens_details.get('cache_write_tokens',0),
                'provider_cost_usd':llm.provider_cost_usd(usage,model), 'tool_call_ok':True}), flush=True)
        assert any(hits[1:]), 'No cache reuse observed across repeated requests'
    finally:
        llm.clear_turn_plan()


if __name__ == '__main__':
    main()
