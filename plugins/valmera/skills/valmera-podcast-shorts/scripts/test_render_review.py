import json
import os
import sys
import pytest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(__file__))
import wf1_short as wf


def test_composed_rejection_rebuilds_product_plan_and_invalidates_render(tmp_path, monkeypatch):
    ctx = SimpleNamespace(supply=str(tmp_path), run_dir=str(tmp_path), log=lambda x: None)
    sc = dict(beats=dict(pictures=[dict(path='/supply/bad.jpg')]), spend=[])
    calls = []

    def draw(ctx, script, score):
        if calls:
            assert not (tmp_path/'film_engine.mp4').exists()
            assert not (tmp_path/'sound.m4a').exists()
        for name in ('film_engine.mp4', 'sound.m4a'):
            (tmp_path/name).write_text('old render')
        calls.append(1)
        return dict(film=str(tmp_path/'film_engine.mp4'))

    def review(ctx, rd, score):
        bad = len(calls)==1
        return dict(rejected=int(bad), placements=[dict(id=0,ok=not bad)], spend=[])

    def rebuild(ctx, script):
        assert json.load(open(tmp_path/'render_rejected.json'))==['bad.jpg']
        return dict(beats=dict(pictures=[]),spend=[])

    monkeypatch.setattr(wf.wf3,'render',draw)
    monkeypatch.setattr(wf,'_render_review',review)
    monkeypatch.setattr(wf,'score',rebuild)
    result=wf.render(ctx,{},sc)
    assert len(calls)==2 and sc['beats']['rendered_check']['rejected']==0
    assert len(result['picture_review'])==2
    assert json.load(open(tmp_path/'score.json'))['beats']['pictures']==[]


def test_unresolved_composed_rejection_cannot_claim_a_passing_check(tmp_path,monkeypatch):
    ctx=SimpleNamespace(supply=str(tmp_path),run_dir=str(tmp_path),log=lambda x:None)
    def fresh():return dict(beats=dict(pictures=[dict(path='/supply/bad.jpg')]),spend=[])
    sc=fresh()
    monkeypatch.setattr(wf.wf3,'render',lambda *a:dict(film='fake.mp4'))
    monkeypatch.setattr(wf,'_render_review',lambda *a:dict(rejected=1,placements=[dict(id=0,ok=False)],spend=[]))
    monkeypatch.setattr(wf,'score',lambda *a:fresh())
    with pytest.raises(RuntimeError, match='picture'):
        wf.render(ctx,{},sc)
