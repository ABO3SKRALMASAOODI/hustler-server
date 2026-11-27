"""Reuse a rendered review only for identical encoded GOPs, frames and narration."""
import bisect
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from picture_check import check


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def keys(film, shots, probe):
    raw = subprocess.check_output([probe, '-v','error','-select_streams','v:0',
        '-show_packets','-show_streams','-show_data_hash','sha256','-show_entries',
        'stream=codec_name,extradata_hash,width,height,color_space,color_range,time_base,start_time:packet=pts_time,flags,data_hash',
        '-of','json',str(film)])
    stream = json.loads(raw)
    start = float(stream['streams'][0].get('start_time',0))
    groups = []
    for packet in stream['packets']:
        pts = float(packet['pts_time']) - start
        if not groups or 'K' in packet['flags']:
            groups.append((pts, []))
        groups[-1][1].append((round((pts-groups[-1][0])*90000),packet['data_hash']))
    starts = [group[0] for group in groups]
    fingerprints = [digest(g[1]) for g in groups]
    checker = hashlib.sha256(Path(__file__).with_name('picture_check.py').read_bytes()).hexdigest()
    for row in shots:
        a,b = row['f']
        lo = max(0,bisect.bisect_right(starts,a/30+1e-6)-1)
        hi = bisect.bisect_left(starts,b/30-1e-6)
        clip = row['clip']
        yield digest([checker,stream['streams'],fingerprints[lo:hi],
                      round((a/30-starts[lo])*90000),b-a,row['src'],clip.get('time_range'),
                      row['say'],row.get('context',row['say']),clip['source'],
                      row.get('crop'),clip.get('crop'),row.get('companion_source'),
                      clip.get('editorial_label'),clip.get('document'),clip.get('source_label')])


def store(cache, key, folder, index, verdict):
    dest = cache/key
    if dest.exists():
        return
    cache.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache) as temp:
        stage = Path(temp)/'row'
        stage.mkdir()
        for name in ('shot','detail'):
            shutil.copyfile(folder/f'{name}-{index:04d}.jpg', stage/(name+'.jpg'))
        (stage/'verdict.json').write_text(json.dumps(verdict))
        try:
            stage.rename(dest)
        except OSError:
            if not dest.is_dir():
                raise


def review(film, rows, out, ffmpeg, probe):
    out=Path(out)
    out.mkdir(parents=True,exist_ok=True)
    cache=out.parent/'encoded-shot-cache'
    identities=list(keys(film,rows,probe))
    evidence={}
    missing=[]
    for i,key in enumerate(identities):
        receipt=cache/key/'verdict.json'
        if receipt.exists():
            evidence[i]=dict(json.loads(receipt.read_text()),i=i)
        else:
            missing.append(i)
    cost=0
    # A batch is durable immediately; a later interrupted batch can resume.
    for offset in range(0,len(missing),5):
        batch=missing[offset:offset+5]
        folder=out/('batch-'+digest([identities[i] for i in batch]))
        result=check(film,[rows[i] for i in batch],folder,ffmpeg)
        cost+=result['spend_usd']
        for j,i in enumerate(batch):
            verdict=result['evidence'].get(j)
            if verdict is None:
                for name in ('shot','detail'):
                    path=folder/f'{name}-{j:04d}.jpg'
                    if path.is_file():
                        shutil.copyfile(path,out/f'{name}-{i:04d}.jpg')
            else:
                store(cache,identities[i],folder,j,verdict)
                evidence[i]=dict(verdict,i=i)
    for i in evidence:
        for name in ('shot','detail'):
            shutil.copyfile(cache/identities[i]/(name+'.jpg'),out/f'{name}-{i:04d}.jpg')
    absent=[i for i in range(len(rows)) if i not in evidence]
    rejected=[i for i,v in evidence.items() if v.get('matches') is not True]
    frames=[r['f'][1]-r['f'][0] for r in rows]
    moving=sum(frames[i] for i,v in evidence.items() if v.get('real_moving_footage') is True)
    return dict(ok=not absent and not rejected,total=len(rows),checked=len(evidence),
                cached_shots=len(rows)-len(missing),missing=absent,rejected=sorted(rejected),
                evidence=evidence,moving_share=moving/max(1,sum(frames)),spend_usd=cost)


def adopt(ctx, rows, words):
    """Index previous evidence before a copied run replaces its old master."""
    import cinema as C
    from encoded_repair import review_shots
    root = Path(ctx.supply)
    stamp, report = root/'picture-input.json', root/'picture_check.json'
    film = Path(ctx.run_dir)/'final.mp4'
    programme = root/'film_scored.mp4'
    if not all(p.is_file() for p in (stamp,report,film,programme)):
        return
    receipt = C.read(stamp)
    cache = root/'encoded-shot-cache'
    done = cache/(digest(receipt)+'.json')
    if done.exists() or receipt.get('checker') != C.digest(Path(__file__).with_name('picture_check.py')):
        return
    if receipt.get('film') != C.digest(film) or receipt.get('shots') != C.digest(root/'shots.json'):
        return
    if digest(rows) != receipt['presentation']:
        rows = review_shots(rows,dict(endcard_ok=True),C.brand.probe(str(programme)),words)
    if digest(rows) != receipt['presentation']:
        return
    verdicts = C.read(report)['evidence']
    folder = root/'picture-evidence'
    count = 0
    for i,key in enumerate(keys(film,rows,C.PX.FP)):
        if str(i) in verdicts and all((folder/f'{n}-{i:04d}.jpg').exists() for n in ('shot','detail')):
            store(cache,key,folder,i,verdicts[str(i)])
            count += 1
    C.save(done,dict(shots=count,film=receipt['film']))
