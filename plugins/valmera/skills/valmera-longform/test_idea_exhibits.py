"""An ideas-stage checkpoint with six motion subjects can supply a documentary."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import wf4
import idea_exhibits


def test_resume_mixed_evidence_into_script_and_supply(tmp_path, monkeypatch):
    import cinema

    url = 'https://research.example/report'
    log_url = url + '/check.log'
    quote = 'The independent reconstruction produced the same published result.'
    output = 'All five theorem statements matched.'
    date = 'November 21, 2026'
    html = f'<html><p>Research group</p><time>{date}</time><p>{quote}</p><a href="{log_url}">Output</a></html>'
    bodies = {url: html, log_url: output}
    def acquire(root, address, limit):
        path = root / idea_exhibits.key(address)
        path.write_text(bodies[address])
        return path
    monkeypatch.setattr(idea_exhibits, 'acquire', acquire)
    names = ['Speaker', 'Certification', 'Comparison result', 'Other document'] + [f'Beat {i}' for i in range(8)]
    named = [dict(name=n, **{'is':'person' if n == 'Speaker' else 'document'}) for n in names]
    motion = []
    for idx, name in enumerate(names):
        count = 2 if idx in range(6, 11) else int(idx == 11)
        sources = [dict(url=f'https://video.example/{idx}/{j}', id=f'v{idx}{j}') for j in range(count)]
        motion.append(dict(name=name, found=count, sources=sources))
    idea = dict(id='i1', strength=9, title='Who checked?', style='dark-room', named=named,
                supply=dict(covered=6, clips=11, subjects=copy.deepcopy(motion), ok=False))
    ideas_path = tmp_path / 'ideas.json'
    ideas_path.write_text(json.dumps(dict(ideas=[idea], no_repeat={'checked': 6})))
    ledger = tmp_path / 'idea-research.json'
    ledger.write_text(json.dumps(dict(ideas=[idea], probes=[idea['supply']], usage={'web_searches':2})))
    before = ledger.read_bytes()
    def extract(system, content, **kw):
        assert output in content and quote in content
        rows = [('Certification', url, quote), ('Comparison result', log_url, output),
                ('Other document', url, 'This passage was invented.'), ('Speaker', url, quote),
                ('Certification', url, quote)]
        return json.dumps(dict(exhibits=[dict(subject=n,url=u,quote=q,date=date,author='Research group')
                                         for n,u,q in rows])), {}
    monkeypatch.setattr(wf4.story.writer, 'ask', extract)
    ctx = SimpleNamespace(run_dir=tmp_path, supply=tmp_path/'supply', args=dict(line=f'Use {url}.'),
                          styles_allowed=lambda: [dict(id='dark-room')], log=lambda msg: None)
    # A previous dossier can also resume. Its existing facts must survive.
    facts = [dict(id=f'f{i}', url=url, quote=f'Previous verified passage {i}') for i in range(25)]
    (tmp_path/'dossier.json').write_text(json.dumps(dict(dossier=dict(facts=facts, named=named),
                                                        verify=dict(verified=25, total=25))))
    class ReachedWriter(Exception):
        pass
    def write(pick, dossier, *args, **kw):
        assert pick['supply']['subjects'] == motion
        assert [f['quote'] for f in dossier['facts'][25:]] == [quote, output]
        assert dossier['facts'][:25] == facts
        raise ReachedWriter
    monkeypatch.setattr(wf4.story, 'write', write)
    with pytest.raises(ReachedWriter):
        wf4.script(ctx, json.loads(ideas_path.read_text()))
    saved = json.loads(ideas_path.read_text())
    probe = saved['ideas'][0]['supply']
    assert (probe['motion_covered'], probe['covered'], probe['clips'], probe['ok']) == (6, 8, 11, True)
    assert saved['no_repeat'] == {'checked': 6} and ledger.read_bytes() == before
    monkeypatch.setattr(idea_exhibits, 'sources', lambda *a: pytest.fail('repeated solved probe'))
    with pytest.raises(ReachedWriter):
        wf4.script(ctx, saved)

    def box(payload, dest, log):
        assert payload['known_sources'] == [s for r in motion for s in r['sources']]
        return dict(lines=[], credits=[], spend={})
    monkeypatch.setattr(cinema.footage_box, 'fetch', box)
    monkeypatch.setattr(cinema, 'prefill_footage', lambda *a: None)
    script = dict(idea_full=saved['ideas'][0], dossier=dict(named=named), script=dict(
        title=idea['title'], chapters=[dict(id='c1', title='Lab')], segments=[dict(say='Results', chapter='c1')]))
    clips = cinema.footage(ctx, script)['editorial']['bank']
    assert [c['shows'] for c in clips] == [quote, output]
    assert [(c['source']['url'],c['source']['date']) for c in clips] == [(url,date),(log_url,date)]
    assert all(c['document'] and c['kind']=='image' and Path(c['path']).is_file() for c in clips)
