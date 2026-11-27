"""Checkpoint take scores, joined readings and bounded same-voice retakes."""
import hashlib
import json
import os
import pickle
from pathlib import Path


def analysis_version(modules, listener=None):
    digest=hashlib.sha256(b''.join(Path(m.__file__).read_bytes() for m in modules)).digest()
    if listener:
        return hashlib.sha256(digest+listener.encode()).digest()
    # The 26 Nov scorer and this revision differ only in dispatch and batching.
    # Preserve its 915 content-addressed documentary receipts; future code changes get fresh keys.
    if digest.hex()=='372a4c020bc497ad53bf40b8091cd99a39c63986a3262422d782e7f1d4b64e6b':
        return bytes.fromhex('0e6b117f194c9d243f71673d4d822b9378aaa351f1ae0aaa4c435296ff54e2d9')
    return digest


def cache_take_analysis(out, listener=None):
    """Keep expensive listening/pitch evidence per immutable take. Selecting a
    film can then resume after a later listener error without rejudging an hour
    of candidates. Compute the ending pitch while that take's track is hot.
    """
    import voice as shared
    import lines as LN
    import prosody
    import perform
    import measure as signal
    import numpy as np
    root=Path(out)/'take-analysis';root.mkdir(parents=True,exist_ok=True)
    version=analysis_version((LN,prosody,perform,shared),listener)
    original=LN._judge_files
    def judged(N,files,seg,script,band,names,sr0):
        evidence=[]
        context=json.dumps(dict(seg=seg,script=script,band=band,names=names),sort_keys=True).encode()
        for f in files:
            key=hashlib.sha256(version+context+f.read_bytes()).hexdigest()
            cache=root/(key+'.pickle')
            if cache.exists():
                with open(cache,'rb') as stream:t=pickle.load(stream)
                x,sr=signal.load_mono(f)
                if sr0 and sr!=sr0:continue
                t['_x']=LN.trim_take(x.astype(np.float32),sr)
                if t.get('errs') and t.get('heard') is not None:
                    # The errors were counted when the take was first heard, by the comparison of that day. Count them
                    # again from the heard words with today's (27 Nov: "Alpöge" cost two errors a mention); no listening.
                    errs=LN.errs_of(N,seg['text'],t['heard'],names)
                    if errs!=t['errs']:
                        t['errs']=errs
                        temp=cache.with_suffix('.tmp')
                        with open(temp,'wb') as stream:pickle.dump({k:v for k,v in t.items() if k!='_x'},stream,pickle.HIGHEST_PROTOCOL)
                        os.replace(temp,cache)
            else:
                rows=original(N,[f],seg,script,band,names,sr0)
                if not rows:continue
                t=rows[0]
                track=prosody.pitch_track(t['_x'],t['_sr']);v=track[np.isfinite(track)]
                t['_ends']=(float(np.median(v[:25])),float(np.median(v[-25:]))) if len(v)>=30 else (None,None)
                temp=cache.with_suffix('.tmp')
                with open(temp,'wb') as stream:pickle.dump({k:v for k,v in t.items() if k!='_x'},stream,pickle.HIGHEST_PROTOCOL)
                os.replace(temp,cache)
            t['file']=f.name
            evidence.append(t)
        return evidence
    LN._judge_files=judged


TAKE_BUDGET=30          # six initial + four six-take rounds
APPROACH_TAKES=6        # takes a changed approach gets
APPROACHES=3            # changed approaches after the budget: respelled, split into phrases, both


def fallback_start(folder, line_id):
    """Older Stories resumes can have over 30 plain takes. Keep those files at their original paths."""
    boundary=folder/(line_id+'_plain_end.json')
    if boundary.exists():
        return json.loads(boundary.read_text())
    memo=folder/'approaches.json'
    forms=json.loads(memo.read_text()) if memo.exists() else {}
    start=TAKE_BUDGET
    if line_id+':1' not in forms:
        start=max([start]+[1+int(p.stem.split('_t')[1]) for p in folder.glob(line_id+'_t*.wav')])
    temp=boundary.with_suffix('.tmp')
    temp.write_text(json.dumps(start))
    temp.replace(boundary)
    return start


def respell_line(text,approach,model=None,heard=None):
    """The line as the voice should be given it after the plain reading did not hold: approach 1 respells only the
    names, initialisms and unusual words in plain letters the voice says right; 2 cuts the line into shorter phrases
    with the same words; 3 does both. A model writes it; a failed or empty answer leaves the line as scripted."""
    how={1:'Respell only the names, initialisms, numbers and unusual words the way a speech synthesiser says them right, in plain letters. Change nothing else.',
         2:'Keep every word and its spelling; cut the sentence into shorter phrases with commas and full stops where a speaker would breathe.',
         3:'Respell only the names, initialisms, numbers and unusual words in plain letters a speech synthesiser says right, and cut the sentence into shorter phrases with commas and full stops. Keep every other word.'}[approach]
    try:
        import spoken
        request=text if heard is None else json.dumps(dict(script=text,unsuccessful_reading=heard))
        r=spoken._call('You prepare one line of a documentary narration for a speech synthesiser that read it wrongly. '+how+' Where words ran together in the unsuccessful reading, separate their sounds. Keep every scripted word and inflection. Answer JSON only: {"line": the line}',request,model or spoken.MODEL)
        out="".join(c.get('text','') for it in r.get('output') or [] if it.get('type')=='message' for c in it.get('content') or [] if c.get('type')=='output_text')
        line=str(json.loads(out[out.index('{'):out.rindex('}')+1])['line']).strip()
        return line or text
    except Exception:
        return text


def bounded_generation(original_generate,respell=None):
    """Shared Read.generate_phonon with the take budget of a resume: six initial + four six-take rounds. The word gate
    remains unchanged. A line whose budget is spent and is asked for more takes is not read the same way again (27 Nov:
    s55 and s65 came back identical each round): its next six takes are read from a respelled form of the line, the six
    after from a form cut into phrases, then both; each approach is recorded and reused on a resume. Only when every
    approach has been heard does the line stop growing. Same signature as the method it wraps."""
    import voice as shared
    def bounded_generate(self,job,out,log,seed0=7):
        import copy
        bounded=copy.deepcopy(job)
        approaches={}
        for row in bounded['lines']:
            requested=int(row.get('takes') or bounded.get('takes') or 6)
            limit=row.get('plain_end',TAKE_BUDGET)
            row['takes']=min(requested,limit)
            if requested>limit:
                row['takes']=min(requested,limit+APPROACH_TAKES*APPROACHES)
                approaches[row['id']]=(row['takes']-limit+APPROACH_TAKES-1)//APPROACH_TAKES
                log(row['id']+': thirty-take retry budget spent; '+('retaining strict gate' if requested>limit+APPROACH_TAKES*APPROACHES else f'reading it a different way (approach {approaches[row["id"]]})'))
        base=int(bounded.get('takes') or 6)
        if all(shared.whole_take(Path(out)/f"{row['id']}_t{k}.wav") for row in bounded['lines'] for k in range(row.get('start_take',0),int(row.get('takes') or base))):
            # every take asked for is on disk: the shared generator would still load the Phonon model (about 100 s) to make none
            log(f"phonon {bounded['voice']}: 0 takes in 0.0s (stories read; every take already on disk, model not loaded)")
            return
        if not approaches:return original_generate(self,bounded,out,log,seed0)
        memo=Path(out)/'approaches.json'
        forms=json.loads(memo.read_text()) if memo.exists() else {}
        plain=[r for r in bounded['lines'] if r['id'] not in approaches]
        if plain:original_generate(self,dict(bounded,lines=plain),out,log,seed0)
        for row in [r for r in bounded['lines'] if r['id'] in approaches]:
            top=approaches[row['id']];total=row['takes']
            limit=row.get('plain_end',TAKE_BUDGET)
            for a in range(1,top+1):
                if row.get('start_take',0)>=limit+APPROACH_TAKES*a:continue
                key=row['id']+':'+str(a)
                if key not in forms:
                    forms[key]=respell(row['text'],a) if respell else respell_line(row['text'],a,heard=row.get('heard'))
                    temp=memo.with_suffix('.tmp')
                    temp.write_text(json.dumps(forms,indent=1))
                    temp.replace(memo)
                step=dict(row,takes=min(total,limit+APPROACH_TAKES*a),text=forms[key])
                original_generate(self,dict(bounded,lines=[step]),out,log,seed0)
    bounded_generate.whole_take = shared.whole_take
    bounded_generate.bounded = True
    return bounded_generate


def cache_gate_listening(out, take_listener=None):
    """The gate listens to the whole joined read, then a model writes the heard words in spoken form. Both are
    pure functions of the audio and the script: when a round joins the same takes as an earlier one (the budget
    is spent and no line changed, or the film resumes after a later crash), the answer is read back from
    gate-cache/ instead of a 4-minute listen and a model call that return the same words.
    """
    import narrate as N
    import spoken
    import numpy as np
    root=Path(out)/'gate-cache';root.mkdir(parents=True,exist_ok=True)
    version=hashlib.sha256(Path(N.__file__).read_bytes()+Path(spoken.__file__).read_bytes()).digest()
    def remembered(key,compute,log_as,keep=lambda value:True):
        path=root/(key+'.pickle')
        if path.exists():
            try:
                with open(path,'rb') as stream:value=pickle.load(stream)
                if log_as:N.log(f'{log_as}: unchanged, heard before; read back, no listening')
                return value
            except Exception:path.unlink(missing_ok=True)
        value=compute()
        if not keep(value):return value      # a failed model call is asked again, never remembered
        temp=path.with_suffix('.tmp')
        with open(temp,'wb') as stream:pickle.dump(value,stream,pickle.HIGHEST_PROTOCOL)
        os.replace(temp,path)
        return value
    if take_listener is not None:
        hear=N.listen_fast
        def cached_fast(x,sr):
            key=hashlib.sha256(version+b'audition'+take_listener.encode()+str(sr).encode()+np.ascontiguousarray(x).tobytes()).hexdigest()
            return remembered(key,lambda:hear(x,sr),None)
        N.listen_fast=cached_fast
    listen_long=N.listen_long
    def cached_listen_long(x,sr,fn=None,max_s=25.0):
        key=hashlib.sha256(version+b'listen'+str(sr).encode()+np.ascontiguousarray(x).tobytes()).hexdigest()
        if fn is not None:
            return listen_long(x,sr,fn,max_s)
        key=hashlib.sha256((key+str([max_s,N.ASR_MODEL,N.vocab_prompt()])).encode()).hexdigest()
        return remembered(key,lambda:listen_long(x,sr,fn,max_s),'gate listener')
    N.listen_long=cached_listen_long
    listen=N.listen
    def cached_listen(x,sr,model=None):
        key=hashlib.sha256(version+b'take'+str(sr).encode()+str([model or N.ASR_MODEL,N.vocab_prompt()]).encode()+np.ascontiguousarray(x).tobytes()).hexdigest()
        return remembered(key,lambda:listen(x,sr,model),None)
    N.listen=cached_listen
    spoken_form=spoken.spoken_form
    def cached_spoken_form(heard,script,names=(),model=None):
        key=hashlib.sha256(version+json.dumps([heard,script,list(names),model]).encode()).hexdigest()
        row=remembered(key,lambda:spoken_form(heard,script,names,model),'spoken form',keep=lambda row:bool(row.get('ok')))
        return row
    spoken.spoken_form=cached_spoken_form
