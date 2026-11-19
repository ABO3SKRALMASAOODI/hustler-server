#!/usr/bin/env python3
"""Explicitly armed, <$3 cache proof. DO NOT RUN BEFORE THE REFILL.

Synthetic inputs, real current prompt builders. Each request is sent twice in
order, without retries. No project edits, tools executed, or publishing. Hosted
search is disabled for this prefix probe: it does NOT prove search-loop savings.
Refill confirmation is required in local config AND central spend_fence.openai;
the date alone is never evidence that credit is available. Quota refusal aborts
and persists a local closed latch. Audio proves one whole-clip call then a memo
hit; audio cache-token usage is reported as unavailable, never invented.
"""
import argparse
import ast
import base64
import copy
import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT/'worker'
SHELL = ROOT/'plugins/valmera/skills/_shell'
sys.path.insert(0, str(WORKER))
import openai_budget as budget
import openai_cache

NOT_BEFORE = dt.datetime(2026, 11, 30, tzinfo=dt.timezone.utc)
RUN_ID = 'cache-live-proof-2026-11-30-v1'  # stable across retries and output folders
RUN_CAP = 2.95


def assert_ready(local, remote, now=None, latch_open=True):
    now = now or dt.datetime.now(dt.timezone.utc)
    if now < NOT_BEFORE:
        raise budget.OverBudget('Live proof is locked until 30 Nov 2026; no request sent.')
    for source, cfg in [('local',local),('central',remote)]:
        if cfg.get('enabled', True) is not True or cfg.get('credit_available') is not True:
            raise budget.OverBudget(f'{source} OpenAI funding is not confirmed available; no request sent.')
        try:
            confirmed = dt.datetime.fromisoformat(cfg['refill_confirmed_at'].replace('Z','+00:00'))
            exhausted = dt.datetime.fromisoformat(cfg.get('exhausted_until', NOT_BEFORE.isoformat()).replace('Z','+00:00'))
        except (KeyError, ValueError, TypeError):
            raise budget.OverBudget(f'{source} refill confirmation is missing/invalid; no request sent.') from None
        if confirmed.tzinfo is None or exhausted.tzinfo is None or confirmed < NOT_BEFORE or confirmed > now or exhausted > now:
            raise budget.OverBudget(f'{source} refill has not cleared the exhausted interval; no request sent.')
    if not latch_open:
        raise budget.OverBudget('OpenAI credit latch is closed; no request sent.')


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    obj=importlib.util.module_from_spec(spec);spec.loader.exec_module(obj)
    return obj


def literal_prompt(path, name):
    tree=ast.parse(Path(path).read_text())
    for node in ast.walk(tree):
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in node.targets):
            try:return ast.literal_eval(node.value)
            except ValueError:continue
    raise ValueError(f'Prompt {name} not found in {path}')


def fixtures():
    """No network. Real instruction/schema builders plus small synthetic data."""
    from PIL import Image
    import agent_prompt, agent_tools, edit_rung, broll_source
    from score import brief, call_openai
    from footage import look
    sys.path.insert(0,str(ROOT/"plugins/valmera/skills/valmera-stories"))
    shell=module('proof_shell_llm',SHELL/'llm.py')
    writer=module('proof_writer',ROOT/'plugins/valmera/skills/valmera-stories/writer.py')
    buf=io.BytesIO();Image.new('RGB',(64,64),(32,96,160)).save(buf,format='PNG')
    image={'type':'image','detail':'low','source':{'type':'base64','media_type':'image/png','data':base64.b64encode(buf.getvalue()).decode()}}
    data=[{'type':'text','text':'Synthetic fixture: a blue rectangle; no named person or historical event.'},image]
    requests=[]
    def add(role,body):
        body=copy.deepcopy(body)
        body.update(max_output_tokens=512,stream=False,store=False)
        if body.get('tools'):body['tool_choice']='none'
        requests.append(dict(site=role,body=body,sha256=hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest()))
    tools=agent_tools.openai_tools(model='gpt-6.1-sol',compact=True,names=['get_edl','read_skill'])
    add('agent',edit_rung.request_body('gpt-6.1-sol',[
        {'role':'system','content':agent_prompt.system_prompt()},
        {'role':'user','content':'Synthetic empty project. Explain which information is needed; execute no tools.'}],
        tools,max_tokens=512,effort='low',tool_choice='none'))
    add('director_direct',call_openai.body('gpt-6.1-sol',brief.prefix_text(),data,512,'low','director_direct'))
    add('footage_look',call_openai.body('gpt-6-sol',look.LOOK_SYSTEM,data,512,'low','footage_look'))
    add('named_check',call_openai.body('gpt-6-sol',broll_source.CHECK_NAMED_SYSTEM,data,512,'low','named_check'))
    placement=literal_prompt(ROOT/'plugins/valmera/skills/valmera-podcast-shorts/scripts/placement_check.py','SYSTEM')
    add('placement_check:check',shell.openai_body(placement,data,model='gpt-6-sol',effort='low',max_tokens=512,purpose='placement_check:check'))
    picture=literal_prompt(ROOT/'plugins/valmera/skills/valmera-longform/picture_check.py','REVIEW_PROMPT')
    add('wf4_picture_check',call_openai.body('gpt-6-sol',picture,data,512,'low','wf4_picture_check'))
    fit=module('proof_fit',ROOT/'plugins/valmera/skills/valmera-stories/fit.py')
    add('fit',shell.openai_body(fit.SYSTEM,data,model='gpt-6.1-sol',effort='low',max_tokens=512,purpose='fit'))
    # Capture the exact current writer builder before its model seam.
    class Captured(BaseException):pass
    def capture(system,content,**kw):
        add('writer:'+kw['what'],writer.openai_body(system,content,model='gpt-6.1-sol',effort='low',max_tokens=512,
            searches=kw.get('searches',0),what=kw['what'],max_tool_calls=kw.get('max_tool_calls')))
        raise Captured()
    card={'id':'reel','name':'Proof','look':'real source images','hook':'one fact','close':'one payoff','needs':'verified facts'}
    with patch.object(writer,'ask_web',capture):
        for run in [lambda:writer.find_ideas('short',[card],line='History of computing'),
                    lambda:writer.write({'title':'Synthetic cache probe'},card,'short')]:
            try:run()
            except Captured:pass
    for role,prompt in [('writer:podcast primary documents',writer.EXTRACT_SYSTEM),('writer:spine research',writer.RESEARCH_SPINE_SYSTEM)]:
        add(role,writer.openai_body(prompt,'Synthetic source: The blue rectangle is 64 pixels wide.',model='gpt-6.1-sol',effort='low',
                                   max_tokens=512,searches=0,what=role.removeprefix('writer:')))
    return requests


def usage_row(site,attempt,body,payload):
    u=payload.get('usage')
    details=(u or {}).get('input_tokens_details') or (u or {}).get('prompt_tokens_details') or {}
    tokens=openai_cache.tokens(u)
    cached=details.get('cached_tokens')
    return dict(site=site,attempt=attempt,model=body['model'],request_id=payload.get('id'),
                request_sha256=hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest(),
                input_tokens=tokens['prompt_tokens'],cached_tokens=cached,
                cached_share=cached/tokens['prompt_tokens'] if cached is not None and tokens['prompt_tokens'] else None,
                usd=openai_cache.cost(body['model'],dict(u,service_tier=payload.get('service_tier'))) if u else None,
                usage=u,status=payload.get('status'))


def run_pair(case,send,on_row=None):
    rows=[]
    for attempt in (1,2):
        body=copy.deepcopy(case['body'])
        # The same central run cap also covers a restart with another output path.
        with budget.call(body,case['site'],lane='internal',run=RUN_ID,run_cap_usd=RUN_CAP) as lease:
            payload=send(body)
            lease.settle(payload.get('usage'),payload=payload)
        rows.append(usage_row(case['site'],attempt,body,payload))
        if on_row:on_row(rows[-1])
        if payload.get('status')=='failed':raise RuntimeError('OpenAI response failed; proof stopped without retry')
    return rows


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--live',action='store_true')
    ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args(argv)
    if not args.live:
        raise budget.OverBudget('Explicit --live is required; no request sent.')
    # Date/refill checks precede key loading, fixture generation and transport.
    local=budget.local_config()
    assert_ready(local,local)
    remote=budget.transaction(lambda cur:(budget._load(cur,'spend_fence') or {}).get('openai',{}))
    import openai_credit
    assert_ready(local,remote,latch_open=openai_credit.available())
    for name in ('VALMERA_SKILL','VALMERA_RUN','OPENAI_INTERNAL_RUN','GAUNTLET_RUN'):
        os.environ.pop(name,None)
    os.environ['SPEND_LANE']='internal'
    shell=module('proof_key',SHELL/'llm.py')
    key=shell.openai_key()
    import httpx
    args.out.mkdir(parents=True,exist_ok=True)
    report=args.out/'usage.jsonl'
    def send(body):
        r=httpx.post('https://api.openai.com/v1/responses',json=body,headers={'Authorization':'Bearer '+key},timeout=180)
        if r.status_code>=400:
            if openai_credit.is_credit_refusal(r.text,status=r.status_code):
                config_path=Path(os.environ.get('OPENAI_INTERNAL_CONFIG',str(budget.LOCAL_CONFIG)))
                config_path.parent.mkdir(parents=True,exist_ok=True)
                config_path.write_text(json.dumps(dict(local,credit_available=False),indent=2)+'\n')
            r.raise_for_status()
        return r.json()
    def save(row):
        with report.open('a') as f:f.write(json.dumps(row)+'\n')
        print(json.dumps({k:row.get(k) for k in ('site','attempt','input_tokens','cached_share','usd')}),flush=True)
    for case in fixtures():
        run_pair(case,send,on_row=save)
    # The high-volume audio path is now disk-memoized. A synthetic silent clip
    # proves call count/timing transport only, never transcription quality.
    import wave
    from footage import hear
    os.environ['FOOTAGE_HEAR_PROVIDER']='openai'
    import config
    config.OPENAI_API_KEY=key
    with tempfile.TemporaryDirectory(prefix='audio-',dir=args.out) as tmp:
        os.environ['FOOTAGE_TRANSCRIPT_CACHE']=str(Path(tmp)/'memo')
        audio=Path(tmp)/'silence.wav'
        with wave.open(str(audio),'wb') as f:
            f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000);f.writeframes(bytes(16000*2*4))
        posts=[]
        post=httpx.post
        def audio_post(*a,**kw):
            result=post(*a,**kw);posts.append(result.json());return result
        def no_fallback(*a,**kw):
            raise RuntimeError('OpenAI audio proof did not complete; local fallback cannot count as proof')
        with budget.scope('internal',RUN_ID), patch.object(httpx,'post',audio_post), patch.object(hear,'_local_clip',no_fallback):
            for attempt in (1,2):
                if not openai_credit.available():raise budget.OverBudget('OpenAI credit exhausted; audio proof stopped')
                before=len(posts)
                rows,usd=hear.transcribe(str(audio),4)
                save(dict(site='footage_hear',attempt=attempt,usd=usd,cached_share=None,
                     usage=posts[-1].get('usage') if len(posts)>before else None,
                     provider_calls=len(posts)-before,cost_basis='duration_rate',segments=len(rows),disk_hit=len(posts)==before))


if __name__=='__main__':
    try:main()
    except budget.OverBudget as exc:raise SystemExit(str(exc))
