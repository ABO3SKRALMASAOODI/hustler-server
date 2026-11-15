import copy
import json
from types import SimpleNamespace
import encoded_repair as E


def shot(start=0,end=60,src=(2,4),image=None):
    c=dict(path='archive.mp4',time_range=[100,110],source=dict(url='https://archive.test/film'))
    if image:c.update(kind='image',content_hash=image)
    return dict(f=[start,end],src=list(src),clip=c,say='The workshop wing.',seg=0)


def test_encoded_rejection_survives_new_label_and_source_preview(tmp_path):
    row=shot();reject=E.observations([row],dict(evidence={'0':dict(matches=False,reason='Wrong component')}))
    (tmp_path/'encoded-rejections.json').write_text(json.dumps(reject))
    row['clip']['editorial_label']='Exterior mission footage'
    preview=dict(ok=True,missing=[],rejected=[],evidence={0:dict(matches=True,real_moving_footage=True)})
    result=E.enforce(SimpleNamespace(supply=str(tmp_path)),[row],preview)
    assert not result['ok'] and result['rejected']==[0]
    assert result['evidence'][0]['encoded_authority']
    assert preview['ok']  # preserve the original review as evidence


def test_rejection_allows_new_frames_and_different_spoken_interval():
    rows=E.observations([shot()],dict(evidence={0:dict(matches=False,reason='Wrong state')}))
    assert E.blocked(shot(src=(3,5)),rows)=='Wrong state'
    assert E.blocked(shot(src=(4,6)),rows) is None
    assert E.blocked(shot(start=60,end=120),rows) is None
    still=shot(image='one-photo')
    rows=E.observations([still],dict(evidence={0:dict(matches=False,reason='Wrong photo')}))
    mirror=copy.deepcopy(still);mirror['clip']['source']['url']='https://mirror.test'
    assert E.blocked(mirror,rows)=='Wrong photo'
    mirror['clip']['content_hash']='different-photo'
    assert E.blocked(mirror,rows) is None
    mirror['clip']['source']['url']=still['clip']['source']['url']
    assert E.blocked(mirror,rows) is None  # another figure in the same report


def test_failed_witness_is_not_silently_recut(tmp_path,monkeypatch):
    import cinema as C
    import pytest
    supply=tmp_path/'supply';supply.mkdir()
    row=shot();row['real_voice']='Witness'
    C.save(supply/'shots.json',[row]);C.save(supply/'picture_check.json',dict(rejected=[0],evidence={}))
    C.save(tmp_path/'measured.json',dict(longform=dict(visual_duplicates=dict(verdicts=[]))))
    (tmp_path/'final.mp4').write_bytes(b'candidate')
    with pytest.raises(RuntimeError,match='witness picture failed'):
        E.begin(SimpleNamespace(run_dir=str(tmp_path),supply=str(supply)))


def test_other_failed_gates_do_not_trigger_picture_retries(tmp_path,monkeypatch):
    import cinema as C
    import pytest
    C.save(tmp_path/'measured.json',dict(targets=dict(pictures=True,unique=True,voice=False)))
    def failed(*args):raise RuntimeError('delivered-file gates failed: voice')
    monkeypatch.setattr(C,'check',failed)
    with pytest.raises(RuntimeError,match='voice'):
        E.check(SimpleNamespace(run_dir=str(tmp_path),supply=str(tmp_path/'supply')),{}, {})
    assert not (tmp_path/'encoded-repairs').exists()


def test_verified_end_card_context_does_not_remove_samples(tmp_path,monkeypatch):
    import cinema as C
    C.save(tmp_path/'words.json',dict(words=[dict(end=9.4)]))
    renderer=SimpleNamespace(outro_join_frames=lambda fps,end:9)
    monkeypatch.setattr(C.brand,'_renderer',lambda:renderer)
    rows=[shot(),shot(start=240,end=300)]
    result=E.review_shots(rows,dict(endcard_ok=True),dict(fps=30,frames=300),tmp_path/'words.json')
    assert [r['f'] for r in result]==[r['f'] for r in rows]
    assert 'verified_output_presentation' not in result[0]['clip']['source']
    context=result[1]['clip']['source']['verified_output_presentation']
    assert context['interval_s']==[9.7,10] and context['last_spoken_word_end_s']==9.5
    assert 'verified_output_presentation' not in rows[1]['clip']['source']
    assert E.review_shots(rows,dict(endcard_ok=False),{},None)==rows


def test_repair_budget_persists_across_processes(tmp_path):
    import cinema as C
    import pytest
    for name in ('first','second'):C.save(tmp_path/'encoded-repairs'/name/'state.json',dict(phase='checked-next'))
    (tmp_path/'final.mp4').write_bytes(b'third failed candidate')
    with pytest.raises(RuntimeError,match='two-film budget'):
        E.begin(SimpleNamespace(run_dir=str(tmp_path)))
