"""Carry the brief's running time through both documentary screenplays."""
import json
from pathlib import Path


def minutes(ctx):
    import cinema as C
    line = ctx.args.get('line')
    if not line:
        return None
    path = Path(ctx.run_dir)/'runtime-plan.json'
    saved = C.read(path) if path.is_file() else {}
    if saved.get('brief') != line:
        text, usage = C.wf4.story.writer.ask(
            'Read the running time requested for the main documentary. Return JSON '
            '{minutes:[minimum,aim,maximum]} with positive numbers in minutes, or '
            '{minutes:null} when unspecified. For a single duration repeat the number.',
            line, effort='low', max_tokens=200, what='WF4 running time')
        saved = dict(C.wf4.story.writer.json_block(text), brief=line, usage=usage)
        C.save(path, saved)
    return saved['minutes']


def plan(dossier, requested, *, words_per_minute=175, testimony=0):
    if requested:
        dossier['runtime_minutes'] = list(requested)
        dossier['runtime_words'] = [round(max(0, m-testimony/60)*words_per_minute) for m in requested]


def budget(dossier):
    if not dossier.get('runtime_words'):
        return ''
    lo, aim, hi = dossier['runtime_words']
    return (f'Plan {aim} spoken words, range {lo}–{hi}, across the chapters. '
            'Trace what people did and what changed as a result. '
            'Spend the extra time on distinct sourced consequences or observed actions. '
            'Find and name their specific available source windows or dated original exhibits before drafting.')


def develop(root, script, dossier, catalogue):
    """Expand a short draft where the retained primary sources add a distinct story beat."""
    from hashlib import sha256
    import cinema as C
    from wf4 import story
    bounds = dossier.get('runtime_words')
    count = story.writer.words_of(script)
    if not bounds or count >= bounds[0]:
        return script
    passages = {key:dict(fact=row['clip'].get('fact'), visible=row['visible'],
                         source=row['clip']['source']['url'], seconds=row['clip']['seconds'])
                for key,row in catalogue.items() if row['usable']}
    material = dict(aim_additional_words=bounds[1]-count,
                    beats=[dict(after=i,chapter=s['chapter'],say=s['say']) for i,s in enumerate(script['segments'])],
                    facts=[{k:f.get(k) for k in ('id','claim','quote','url')} for f in dossier['facts']],
                    pictures=passages)
    instruction = ('Develop additional, source-supported beats inside this existing documentary. Explain the consequential '
        'human decisions and what changed for later users of the work. Find the picture first. Use specific records, '
        'worked examples or inspected source footage. Preserve the opening and closing and every existing line. '
        'Find new substance in these sources rather than paraphrasing the preceding beat. '
        'Return JSON {additions:[{after:existing beat index,say:spoken words, facts:[verified fact ids], '
        'visual_beats:[{word_start:zero-based spoken word offset,archive_ids:[literal supplied picture ids]}], '
        'direction:performance direction}]}. Write out spoken numbers. Start each beat at word zero. '
        'Keep each added segment around twenty to thirty words. Distribute the requested additional words across the story.')
    content=json.dumps(material,ensure_ascii=False)
    path=Path(root)/('development-'+sha256((instruction+content).encode()).hexdigest()+'.json')
    if path.exists():
        result=C.read(path)
    else:
        text,usage=story.writer.ask(instruction,content,effort='medium',max_tokens=6500,what='WF4 sourced story development')
        result=dict(story.writer.json_block(text),usage=usage)
        C.save(path,result)
    by={}
    for addition in result['additions']:
        i=addition['after']
        if isinstance(i,int) and 0<=i<len(script['segments'])-1:
            beat = dict(script['segments'][i], beat='setup', design=None, emphasis=[], ticks=[])
            beat.update({k:addition[k] for k in ('say','facts','direction','visual_beats')})
            beat['pictures']=[catalogue[key]['visible'] for v in beat['visual_beats'] for key in v['archive_ids'] if key in catalogue]
            by.setdefault(i,[]).append(beat)
    return dict(script,segments=[r for i,s in enumerate(script['segments']) for r in (s,*by.get(i,[]))])
