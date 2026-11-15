"""Delivered cuts and holds using judge3's edit_delta implementation unchanged."""
from pathlib import Path
import argparse,hashlib,json,sys

RATER=Path('/Users/masaoodi/Documents/Valmera/.valmera/week-1106/rater')


def measure(film,out):
    sys.path.insert(0,str(RATER))
    import edit_delta as E
    fingerprint=hashlib.sha256(Path(__file__).read_bytes()+(RATER/'edit_delta.py').read_bytes())
    with open(film,'rb') as stream:fingerprint.update(hashlib.file_digest(stream,'sha256').digest())
    signature=fingerprint.hexdigest();out=Path(out)
    if out.exists():
        prior=json.loads(out.read_text())
        if prior.get('input_sha256')==signature:return prior
    info=E.probe(str(film));analysis=E.cut_analysis(str(film),info)
    gray=analysis['gray'];fps=analysis['fps'];dur=analysis['duration']
    camera=E.camera_moves(gray,fps,analysis['cuts']);rows=[]
    for a,b in E.shots_from(analysis['cuts'],dur):
        sample=gray[round(a*fps):round(b*fps)]
        motion=E.camera_moves(sample,fps,[])
        rows.append(dict(start=a,end=b,seconds=b-a,camera=motion))
    holds=[r for r in rows if r['seconds']>8 and not r['camera']['moves']]
    import numpy as np
    result=dict(input_sha256=signature,method='judge3/edit_delta.cut_analysis and camera_moves, unchanged thresholds',
                seconds=dur,cuts=len(analysis['cuts']),picture_changes_per_10s=len(analysis['cuts'])/dur*10,
                median_shot_s=float(np.median([r['seconds'] for r in rows])),max_shot_s=max(r['seconds'] for r in rows),
                camera=camera,shots=rows,holds_over_8s_without_camera_move=holds,holds_ok=not holds)
    out.write_text(json.dumps(result,indent=1));return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('film');p.add_argument('out');a=p.parse_args()
    result=measure(a.film,a.out)
    print(json.dumps({k:v for k,v in result.items() if k!='shots'}))
