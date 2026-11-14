"""Regressions from the independent WF1 round-four teardown."""
import importlib.util
from pathlib import Path
import numpy as np
import pytest

from scene import pace
from score import audibility
from score import music

path = Path(__file__).resolve().parents[2]/'plugins/valmera/skills/valmera-podcast-shorts/scripts/wf1_coverage.py'
spec = importlib.util.spec_from_file_location('wf1_coverage_test',path)
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)


def test_empty_picture_audit_cannot_pass_coverage():
    coverage = {'windows':[{'t0':3.,'t1':10.}]}
    with pytest.raises(RuntimeError,match='documentary supply incomplete'):
        C.require_coverage([],coverage,12)


def test_static_video_files_do_not_satisfy_moving_coverage():
    coverage = {'windows':[{'t0':0.,'t1':10.}]}
    rows = [[i*30,(i+1)*30,'clip','subject',dict(window=0,placed_motion_p90=0.)] for i in range(8)]
    with pytest.raises(RuntimeError,match='moving=0.000'):
        C.require_coverage(rows,coverage,10)
    for row in rows: row[4]['placed_motion_p90']=2.
    assert C.require_coverage(rows,coverage,10)['moving_seconds']==8


def test_picture_starts_at_its_word_window_not_sentence_start():
    coverage = {'windows':[{'t0':6.3,'t1':8.,'name':'Facebook','must_show':'actual social network'}]}
    footage = {0:[{'path':f'new-shot-{i}', 'seconds':2.,'subject':'Facebook'} for i in range(3)]}
    rows = C.proposals(coverage,footage,{})
    assert rows[0][0] == 192
    assert all(192 <= r[0] < r[1] <= 243 for r in rows)
    assert len({r[4]['path'] for r in rows})==len(rows)


def test_speaker_camera_moves_only_at_directed_words():
    shots = [dict(id='s',f=[0,300],pic='main',pw=1920,ph=1080,src=[0,10],faces=[[.4,.2,.12,.35]])]
    stress = [(i,.9) for i in range(10,300,10)]
    a, log = pace.drive(shots,stress,family='podcast',band={'motivated_frames':[]})
    assert len(a)==1 and log['eased']==0 and log['punches']==0
    b, log = pace.drive(shots,stress,family='podcast',band={'motivated_frames':[150]})
    assert len(b)==1 and log['eased']==1 and log['punches']==0
    crop = np.array(b[0]['crop'])
    assert np.max(np.abs(np.diff(crop,axis=0))) < .02


def test_spectral_or_planned_presence_cannot_count_a_masked_hit():
    sr=48000; t=np.arange(sr*4)/sr
    voice=.1*np.sin(2*np.pi*220*t);bed=.05*np.sin(2*np.pi*110*t)
    hit=np.zeros_like(voice);at=sr*2;hit[at:at+4800]=.002*np.sin(2*np.pi*1200*t[:4800])*np.hanning(4800)
    assert audibility.measure(voice,hit,bed)['T2']==0
    hit*=200
    assert audibility.measure(voice,hit,bed)['T2']>=1
    calibrated,_=audibility.calibrate_bed(voice,bed)
    assert abs(audibility.measure(voice,hit,calibrated)['bed_under_voice_db']-5.5)<.1


def test_podcast_payoff_hole_survives_half_second_measurement():
    p=music.profile_for('podcast')
    assert 1 <= p['hole_min_s'] <= p['hole_max_s'] <= 2.3
    assert 10 <= p['hole_db'] <= 24
    assert p['broadband_bed_db']==5.5
