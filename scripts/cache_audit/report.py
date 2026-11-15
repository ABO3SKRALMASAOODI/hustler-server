"""Audit actual usage and attempt a falsifiable offline calibration.

Missing wire data is never silently fabricated. Outputs contain aggregates only.
Run with the worker test Python (tiktoken 0.12.0, o200k_base cached locally).
"""
import argparse, collections, datetime as dt, gzip, json, re, sys
from pathlib import Path
from simulator import Prompt, Simulator, calibration
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'worker'))
import openai_cache


def obj(v):
    if isinstance(v, str):
        try:return json.loads(v)
        except ValueError:return {}
    return v or {}


def role(s):
    # Strip a legacy SHA suffix, never interpret prompt/user intent.
    return re.sub(r':[0-9a-f]{12}$', '', str(s))


def rows(path, ledger):
    ids=set()
    with gzip.open(path,'rt') as f:
        for line in f:
            r=json.loads(line)
            if not str(r['model']).startswith('gpt-'):continue
            resp=obj(r['response']); req=obj(r['request']); rid=resp.get('request_id')
            if rid:
                if rid in ids:continue
                ids.add(rid)
            usage=resp.get('usage') or {}
            details=usage.get('input_tokens_details') or usage.get('prompt_tokens_details') or {}
            cached=resp.get('cached_in',details.get('cached_tokens'))
            n=r['prompt_tokens'] or 0; output=r['completion_tokens'] or 0
            u=dict(input_tokens=n, output_tokens=output,input_tokens_details=dict(cached_tokens=cached or 0,
                   cache_write_tokens=resp.get('cache_write_in') or 0),service_tier=resp.get('service_tier'))
            price=resp.get('provider_cost_usd')
            yield dict(source=r['source'],ts=r['created_at'],purpose=role(r['purpose']),model=r['model'],
                       input=n,output=output,cached=cached,usd=price if price is not None else openai_cache.cost(r['model'],u),
                       price_basis='recorded' if price is not None else 'reconstructed',request=req,
                       response=resp, id=str(r['id']),request_id=rid)
    for line in ledger.open():
        r=json.loads(line)
        if r.get('request_id') in ids:continue
        # Failed/unknown calls stay visible, but are not counted as cached zeros.
        reported=r.get('usage_reported',bool(r.get('prompt_tokens')))
        yield dict(source='workflow',ts=r['ts'],purpose=role(r['purpose']),model=r['model'],
                   input=r.get('prompt_tokens') or 0,output=r.get('completion_tokens') or 0,
                   cached=r.get('cached_tokens') if reported else None,usd=r.get('usd'),
                   price_basis='recorded',request={},response={},id=r.get('request_id'),status=r.get('status'),
                   request_id=r.get('request_id'))


def flatten(req, enc):
    """Text proxy only; full tool schemas and pixels were not logged historically."""
    messages=req.get('messages')
    if not isinstance(messages,list):return None
    chunks=[]; boundaries=[]; n=0
    tools=req.get('tools') or []
    if tools:
        t=enc.encode(json.dumps(tools,ensure_ascii=False,sort_keys=True));chunks.extend(t);n+=len(t)
    for m in messages:
        if not isinstance(m,dict):continue
        c=m.get('content') or ''
        if isinstance(c,list):c='\n'.join(str(b.get('text','[unlogged image]')) for b in c)
        t=enc.encode(str(m.get('role',''))+'\n'+str(c),disallowed_special=());chunks.extend(t);n+=len(t);boundaries.append(n)
    return tuple(chunks),boundaries


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--evidence',type=Path,required=True);ap.add_argument('--ledger',type=Path,required=True);args=ap.parse_args()
    data=list(rows(args.evidence/'calls.jsonl.gz',args.ledger))
    sites=collections.defaultdict(list)
    for r in data:sites[(r['purpose'],r['model'])].append(r)
    table=[]
    for (purpose,model),rs in sorted(sites.items(),key=lambda kv:-sum(r['input'] for r in kv[1])):
        known=[r for r in rs if r['cached'] is not None]; total=sum(r['input'] for r in known)
        days=[r for r in rs if '2026-11-14' <= r['ts'] < '2026-11-16']
        table.append(dict(purpose=purpose,model=model,calls=len(rs),input_tokens=sum(r['input'] for r in rs),
                          cached_tokens=sum(r['cached'] for r in known),usage_known_calls=len(known),
                          actual_cached_share=sum(r['cached'] for r in known)/total if total else None,
                          full_wire_calls=0, days_14_15_calls=len(days),days_14_15_input=sum(r['input'] for r in days),
                          dollars_14_15=sum(r['usd'] or 0 for r in days),simulated_after=None,dollars_after=None,
                          cause='unlogged prompt/audio identity; per-call cause unidentifiable' if all(not r['request'].get('messages') for r in rs)
                          else 'sanitized images/tool names, wire options missing; exact prefix unidentifiable'))
    # Diagnostic simulation across every available message log, not cherry-picked.
    import tiktoken
    enc=tiktoken.get_encoding('o200k_base'); diag=[]
    for r in sorted(data,key=lambda x:x['ts']):
        flat=flatten(r['request'],enc)
        if flat is None:continue
        tokens,bounds=flat
        p=Prompt(at=dt.datetime.fromisoformat(r['ts']).timestamp(),model=r['model'],role=r['purpose'],tokens=tokens,
                 key=r['purpose'],modern=r['model'].startswith(('gpt-6','gpt-5.6')),
                 boundaries=dict.fromkeys(bounds,1800),lookup=tuple(bounds),complete=False,
                 actual=r['cached'],reported_input=r['input'])
        diag.append(p)
    sim=Simulator();results=[sim.run(p) for p in diag];cal=calibration(diag,results)
    missing=collections.Counter()
    for r in data:
        req=r['request']
        if not req:missing['no_request']+=1
        elif req.get('messages'):missing['sanitized_messages_and_tool_names']+=1
        elif 'question' in req:missing['question_only_missing_image_bytes']+=1
        else:missing['settings_only_or_partial']+=1
    summary=dict(period='2026-11-09 through 2026-11-15 UTC (ledger starts Nov 14 13:31 UTC)',
                 rows=len(data),input_tokens=sum(r['input'] for r in data),
                 actual_cached_tokens=sum(r['cached'] or 0 for r in data),
                 dollars_14_15=sum(r['usd'] or 0 for r in data if '2026-11-14' <= r['ts'] < '2026-11-16'),
                 missing_wire=dict(missing),calibration=cal,
                 diagnostic_miss_classes=dict(collections.Counter(r['cause'] for r in results)),
                 accepted_forecast=False,forecast_reason='No complete wire requests; calibration cannot pass. No whole-bill forecast or identical-output proof is justified.',
                 sources=json.loads((args.evidence/'sources.json').read_text()))
    (args.evidence/'report.json').write_text(json.dumps(dict(summary=summary,sites=table),indent=2))
    lines=['# CACHE-FIX measured evidence','',json.dumps({k:v for k,v in summary.items() if k!='sources'},indent=2),'',
           '| Site / model | Calls | Input | Actual cached | 14–15 Nov $ | Simulated after | After $ |',
           '|---|---:|---:|---:|---:|---:|---:|']
    for r in table:
        share='unknown' if r['actual_cached_share'] is None else f"{100*r['actual_cached_share']:.2f}%"
        lines.append(f"| {r['purpose']} / {r['model']} | {r['calls']} | {r['input_tokens']} | {share} | {r['dollars_14_15']:.4f} | uncalibrated | unknown |")
    (args.evidence/'TABLE.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({k:v for k,v in summary.items() if k!='sources'},indent=2))
if __name__=='__main__':main()
