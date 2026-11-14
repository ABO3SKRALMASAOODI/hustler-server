"""Repair difficult spoken sentences from listener evidence, never their gate.
The model rewrites only named lines against their verified source quotes. A
second factual check must pass. Unchanged takes/analysis survive; changed takes
are archived and regenerated. Run only while this film's narrator is stopped.
"""
import argparse,copy,hashlib,json,os,shutil,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path[:0]=[str(HERE),str(HERE.parent/'valmera-stories'),str(HERE.parent/'_shell'/'narrate')]
import wf4
import voice

def save(p,j):Path(p).write_text(json.dumps(j,ensure_ascii=False,indent=1))

def prepare_takes(nd,ns,backup,old=None):
    import lines,prosody,perform
    old=old or json.loads((nd/'_generations.json').read_text())
    new=voice.group_script(ns,voice.groups_of(ns['segments']))
    if [s['members'] for s in old['segments']]!=[s['members'] for s in new['segments']]:
        raise ValueError('Sentence repair changed generation grouping; revise it before applying')
    version=hashlib.sha256(b''.join(Path(m.__file__).read_bytes() for m in (lines,prosody,perform,voice))).digest()
    band=json.loads((HERE.parent/'_shell/narrate/voice_ref.json').read_text())['band']['04']
    cache=nd/'take-analysis';changed=[];copied=0
    for i,(before,after) in enumerate(zip(old['segments'],new['segments'])):
        files=list((nd/'_takes').glob(f's{i:02d}_t*.wav'))
        if before!=after:
            changed.append(i)
            for p in files:shutil.move(p,backup/p.name)
            continue
        contexts=[json.dumps(dict(seg=s['segments'][i],script=s,band=band,names=list(s.get('names') or [])),sort_keys=True).encode() for s in (old,new)]
        for p in files:
            data=p.read_bytes();keys=[hashlib.sha256(version+c+data).hexdigest()+'.pickle' for c in contexts]
            if keys[0]!=keys[1] and (cache/keys[0]).exists():
                shutil.copy2(cache/keys[0],cache/keys[1]);copied+=1
    for name in ('narration.wav','words.json','render.json','check.json','narration_performance.wav','words_performance.json','render_performance.json','longform_voice.json'):
        p=nd/name
        if p.exists():shutil.move(p,backup/name)
    save(nd/'narration_script.json',ns)
    return dict(changed_generations=changed,analysis_preserved=copied)

def finish_pending(run):
    run=Path(run);pending=run/'read-repair.pending.json'
    if not pending.exists():return None
    dest=Path(json.loads(pending.read_text())['folder']);sc=json.loads((dest/'script-after.json').read_text())
    backup=dest/'rejected-takes';backup.mkdir(exist_ok=True)
    old=json.loads((dest/'generations-before.json').read_text())
    report=prepare_takes(run/'supply/narration',wf4.story.narration_script(sc['script']),backup,old)
    save(run/'script.json',sc);save(dest/'applied.json',report);pending.unlink()
    return dict(folder=str(dest),**report)


def repair(run,diagnosis):
    run=Path(run);nd=run/'supply/narration';old=json.loads((run/'script.json').read_text())
    if (run/'read-repair.pending.json').exists():return finish_pending(run)
    if (run/'supply/pack.json').exists():raise ValueError('Sentence repair belongs before the completed supply checkpoint')
    sc=copy.deepcopy(old);d=json.loads(Path(diagnosis).read_text());ids=[r['i'] for r in d['lines']]
    facts={f['id']:f for f in sc['dossier']['facts']}
    material=[dict(i=i,before=sc['script']['segments'][i],facts=[facts[f] for f in sc['script']['segments'][i]['facts']],
                   previous=[s['say'] for s in sc['script']['segments'][:i] if s['chapter']==sc['script']['segments'][i]['chapter']]) for i in ids]
    root=run/'read-repairs';root.mkdir(exist_ok=True);dest=root/f'round-{len(list(root.glob("round-*")))+1}';dest.mkdir()
    text,use=wf4.story.writer.ask('Repair documentary sentences for a spoken performance. The listener evidence names difficult wording; preserve every factual claim and every meaningful detail, but use clear natural words that a narrator can pronounce reliably. '
        'Do not change the hook, story sequence, chapter, beats, or evidence. Avoid forcing the narrator through a failed technical word: explain it in common language when the source supports that. '
        'A repeated personal or vehicle name may become an unambiguous reference to the already introduced subject. Keep each sentence group near its original word count, so none merges with its neighbour. All numbers in spoken words. '
        'Return JSON {replacements:[{i,say,reason}]}, exactly the requested indexes.',json.dumps(dict(diagnosis=d,material=material)),effort='high',max_tokens=3500,what='WF4 spoken sentence repair')
    proposal=wf4.story.writer.json_block(text);proposal['usage']=use;save(dest/'proposal.json',proposal)
    replacements=proposal.get('replacements',[])
    if sorted(r['i'] for r in replacements)!=sorted(ids):raise ValueError('repair changed the requested scope')
    text,use=wf4.story.writer.ask('Verify each proposed spoken sentence against the supplied original narration and verified source quotes. Every factual claim and meaningful detail must be preserved, no cause, date, identity, quantity or outcome changed. '
        'Only clearer phrasing and unambiguous contextual references are allowed. Return JSON {faithful:bool,issues:[string],why:string}.',json.dumps(dict(material=material,proposal=proposal)),effort='high',max_tokens=2000,what='WF4 repaired sentence fact check')
    proof=wf4.story.writer.json_block(text);proof['usage']=use;save(dest/'fact-check.json',proof)
    if proof.get('faithful') is not True:raise ValueError('sentence repair failed factual review: '+json.dumps(proof))
    for r in replacements:sc['script']['segments'][r['i']]['say']=r['say']
    check=wf4.story.check_script(sc['script'],sc['dossier']);save(dest/'script-check.json',check)
    if not check['ok']:raise ValueError('repaired script check failed: '+json.dumps(check))
    save(dest/'script-before.json',old);save(dest/'narration-before.json',json.loads((nd/'narration_script.json').read_text()))
    save(dest/'script-after.json',sc);save(dest/'generations-before.json',json.loads((nd/'_generations.json').read_text()))
    save(run/'read-repair.pending.json',dict(folder=str(dest)))
    return finish_pending(run)

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('run');a.add_argument('diagnosis');ns=a.parse_args()
    if not os.environ.get('OPENAI_API_KEY'):
        for line in Path('/Users/masaoodi/Documents/Valmera/hustler-server/backend/.env').read_text().splitlines():
            if line.startswith('OPENAI_API_KEY='):os.environ['OPENAI_API_KEY']=line.split('=',1)[1].strip().strip('"').strip("'")
    os.environ['WRITER_PROVIDER']='openai'
    print(json.dumps(repair(ns.run,ns.diagnosis)))
