"""Primary archive photographs through the worker's existing NASA provider.

Commons is an index, not the whole archive. A missing Commons result must not
be mistaken for a missing historical picture. Every candidate still faces the
same named-subject vision check, and the final film's word/picture check.
"""
from pathlib import Path
import hashlib
import json
import pictures as PX


def find(subjects, out):
    B=PX._worker();out=Path(out);out.mkdir(parents=True,exist_ok=True)
    results={};reports=[]
    for subject in subjects:
        key=hashlib.sha256(json.dumps(subject,sort_keys=True).encode()).hexdigest()[:16]
        cache=out/(key+'.json')
        if cache.exists():
            row=json.loads(cache.read_text())
            results[subject['name']]=row['pictures'];reports.append(row['report']);continue
        candidates={};errors=[]
        for query in subject.get('searches',[subject['name']])[:3]:
            try:
                for c in B._nasa(query,B.KIND_PHOTO,None,6):candidates[c['id']]=c
            except Exception as e:errors.append(str(e)[:200])
        records=[];checks=[];usd=0.
        values=list(candidates.values())
        for start in range(0,len(values),B.NAMED_CHECK_MAX):
            kept,checked,cost=B.check_named(subject,values[start:start+B.NAMED_CHECK_MAX],str(out))
            checks.extend(checked);usd+=cost
            for c in kept:
                try:
                    B.nasa_renditions(c)
                    got=B.fetch_local(c,str(out/'pics'),1920,1080)
                    if max(got['width'],got['height'])<1200:continue
                    records.append(PX._record(c,got,subject['name']))
                except Exception as e:errors.append(c['id']+': '+str(e)[:200])
        report=dict(subject=subject['name'],checked=checks,errors=errors,usd=usd,candidates=len(values),downloaded=len(records))
        row=dict(pictures=records,report=report)
        cache.write_text(json.dumps(row,indent=1))
        results[subject['name']]=records;reports.append(report)
    return results,reports
