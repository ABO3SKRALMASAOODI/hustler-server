"""Workflow model costs belong to the local run, never a customer's DB row."""
from pathlib import Path
import fcntl,json,time


def install(path):
    import llm
    if not hasattr(llm,'_call_cost_usd'):raise RuntimeError('worker model adapter was shadowed')
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    def charge(purpose,request,response,usage):
        row=dict(at=time.time(),purpose=purpose,model=(request or {}).get('model'),
                 usd=llm._call_cost_usd(purpose,request,response,usage),
                 request_id=(response or {}).get('request_id'))
        with open(path,'a') as stream:
            fcntl.flock(stream,fcntl.LOCK_EX)
            stream.write(json.dumps(row)+'\n');stream.flush()
            fcntl.flock(stream,fcntl.LOCK_UN)
    llm._charge=charge
