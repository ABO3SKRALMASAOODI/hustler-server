import json
from pathlib import Path

import shell


def test_documentary_sources_are_staged_with_the_film(tmp_path, monkeypatch):
    out = tmp_path / 'out'
    monkeypatch.setenv('VALMERA_DELIVERY_ROOTS', str(out))
    ctx = shell.Ctx(str(tmp_path / 'run'), dict(run_id='wf4-test', workflow='wf4',
                    args={'platform_song': False}), str(tmp_path / 'state'))
    Path(ctx.state_root).mkdir()
    supply = Path(ctx.supply)
    supply.mkdir()
    film = tmp_path / 'fixture.mp4'
    film.write_bytes(b'fixture')
    fact = dict(id='f1', claim='Two of the three samples passed.', quote='2/3 passed.',
                url='https://example.org/report.pdf#page=8', author='A. Singh', date='2026-11-10')
    unused = dict(fact, id='f2', url='https://example.org/unused')
    arc = dict(setup='The lab opens.', escalation='One sample fails.', payoff='Two pass.')
    script = dict(title='Samples', style=dict(id='ledger'), idea_full=dict(arc=arc),
                  dossier=dict(facts=[fact, unused]), facts_cited=[fact, unused],
                  script=dict(segments=[dict(facts=['f1'], chapter='c1', design=dict(kind="document", fact="f1", title="Lab report"))]))
    photo = dict(kind='image', name='Test plate', source=dict(title='Plate A', credit='Lab',
                 url='https://example.org/plate'), page='https://example.org/collection/plate',
                 time_range=[0, 7.8])
    clip = dict(time_range=[30, 40], source=dict(title='Lab tour', channel='Lab Archive',
                url='https://example.org/tour'))
    shots = [dict(f=[0, 60], seg=0, src=[0, 2], clip=photo),
             dict(f=[60, 150], seg=0, src=[1.5, 4.5], clip=clip, real_voice='R. Lee')]
    (supply / 'shots.json').write_text(json.dumps(shots))
    original = dict(seg=0, source_url=fact['url'], kind='original', path='page.png', attribution='Report by A. Singh')
    pack = dict(designs={'0': original}, footage=dict(credits=[unused]))
    (supply / 'pack.json').write_text(json.dumps(dict(pack=pack)))
    scene = dict(assets={'evidence-0': dict(path='page.png')}, items=[dict(pic='evidence-0')])
    (supply / 'l1.json').write_text(json.dumps(scene))
    results = dict(script=script, ideas=dict(source=dict(sources=[unused['url']])), score=dict(l1=supply / 'l1.json'),
                   render={}, check=dict(final=str(film), checks=dict(longform=dict(credits=[unused]))))
    # Stub only the chapter rendering in this source-credit fixture.
    import sys
    from types import SimpleNamespace
    monkeypatch.setitem(sys.modules, "chapter_shorts", SimpleNamespace(
        write=lambda ctx, part, results: dict(folder="Shorts", count=1)))
    rename = shell.os.rename

    def commit(staged, final):
        report = (Path(staged) / 'SOURCES.md').read_text()
        for literal in (*arc.values(), fact['claim'], fact['quote'], fact['url'], fact['author'],
                        fact['date'], photo['name'], photo['page'], photo['source']['url'],
                        'R. Lee', 'Lab tour', '31.5–34.5 s', 'Source capture · segment 1 · f1 · Lab report'):
            assert literal in report
        assert unused['url'] not in report
        assert not Path(final).exists()
        return rename(staged, final)

    monkeypatch.setattr(shell.os, 'rename', commit)
    delivered = shell.deliver(ctx, {}, str(out), results)
    folder = Path(delivered['folder'])
    assert (folder / 'film.mp4').read_bytes() == b'fixture'
    assert shell.deliver(ctx, {}, str(out), results)['already']
    assert [r['run_id'] for r in shell.DL.rows('wf4')] == [ctx.run_id]
