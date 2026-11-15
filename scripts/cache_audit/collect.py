"""Read-only evidence collector. Raw requests stay private, outside git. No API calls."""
import argparse, collections, datetime, gzip, json, os
from pathlib import Path
from urllib.parse import urlparse
import psycopg2
import psycopg2.extras


def collect(url, source, out):
    c = psycopg2.connect(url, connect_timeout=8, options='-c default_transaction_read_only=on -c statement_timeout=120000')
    c.set_session(readonly=True)
    n = 0
    with c, c.cursor(name='cache_audit', cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.itersize = 100
        cur.execute("select id, created_at, project_id, job_id, purpose, model, request, response, prompt_tokens, completion_tokens from llm_calls where created_at >= '2026-11-09' and created_at < '2026-11-16' order by created_at,id")
        for row in cur:
            out.write(json.dumps(dict(row, source=source), default=str, ensure_ascii=False)+'\n'); n += 1
    c.close()
    return n


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--workspace',type=Path,required=True); ap.add_argument('--out',type=Path,required=True); a=ap.parse_args()
    os.umask(0o077);a.out.mkdir(parents=True,exist_ok=True)
    env=a.workspace/'hustler-server/backend/.env'
    url=next(s.split('=',1)[1].strip().strip('"\'') for s in env.read_text().splitlines() if s.startswith('DATABASE_URL='))
    sources=[('production',url)]
    for p in sorted((a.workspace/'.valmera/week-1106/gauntlet/runs').glob('*/stack.json')):
        r=json.loads(p.read_text());u=r.get('db_url',''); parsed=urlparse(u)
        if parsed.hostname in ('localhost','127.0.0.1'):sources.append((p.parent.name,u))
    seen=set(); manifest=[]
    with gzip.open(a.out/'calls.jsonl.gz','wt') as out:
        for source,u in sources:
            if u in seen:continue
            seen.add(u)
            try:
                n=collect(u,source,out);manifest.append(dict(source=source,rows=n));print(source,n,flush=True)
            except psycopg2.Error as e:
                # Never print exception text: connection errors can include credentials.
                manifest.append(dict(source=source,error=type(e).__name__,sqlstate=e.pgcode))
    (a.out/'sources.json').write_text(json.dumps(manifest,indent=2))
    print('sources',len(manifest),'read',sum('rows' in x for x in manifest),'rows',sum(x.get('rows',0) for x in manifest))
if __name__=='__main__':main()
