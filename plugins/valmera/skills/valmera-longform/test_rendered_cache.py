import json

import rendered_cache as cache


def test_a_changed_picture_crop_or_line_cannot_inherit_a_rendered_verdict(tmp_path,monkeypatch):
    packets=[dict(pts_time=str(i),flags='K_',data_hash=name) for i,name in enumerate(('frame-a','frame-b'))]
    monkeypatch.setattr(cache.subprocess,'check_output',lambda *a:json.dumps(dict(
        streams=[dict(codec_name='h264')],packets=packets)).encode())
    rows=[dict(f=[30*i,30*(i+1)],src=[0,1],say='He checked it.',context='The rebuild.',
               clip=dict(source=dict(url='primary'),time_range=[0,1])) for i in range(2)]
    calls=[]
    def read_picture(film, batch, folder, ffmpeg):
        calls.append(len(batch))
        folder.mkdir(parents=True,exist_ok=True)
        for i in range(len(batch)):
            for name in ('shot','detail'):
                (folder/f'{name}-{i:04d}.jpg').write_bytes(b'frame')
        return dict(evidence={i:dict(i=i,matches=False,reason='Wrong date') for i in range(len(batch))},spend_usd=0)
    monkeypatch.setattr(cache,'check',read_picture)
    def run():
        return cache.review('film',rows,tmp_path/'pictures','ffmpeg','probe')
    assert run()['rejected']==[0,1]
    assert run()['cached_shots']==2
    packets[1]['data_hash']='changed pixels'
    assert run()['cached_shots']==1
    rows[0]['crop']=[0,.2,1,.7]
    assert run()['cached_shots']==1
    rows[0]['context']='A different build.'
    assert run()['cached_shots']==1
    assert calls==[2,1,1,1]
