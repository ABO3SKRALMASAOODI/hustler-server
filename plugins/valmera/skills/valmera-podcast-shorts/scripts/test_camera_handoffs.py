"""The heard m38 turns precede the incoming cameras at source frames 304 and 476."""
import pytest

import wf1_film as film
import wf1_source_frame as source
from test_wf1_take import _sup, quiet_engines


def test_m38_reply_and_closing_camera_use_the_aligned_take(tmp_path, quiet_engines, monkeypatch):
    import wf1_coverage
    monkeypatch.setattr(wf1_coverage, "speaker_moves", lambda *args: ([], []))
    bounds = [0, 304/30, 476/30, 648/30, 22.12]
    shots = [dict(t0=a, t1=b, faces=[]) for a,b in zip(bounds, bounds[1:])]
    pack = _sup(tmp_path, shots)
    words = [dict(w=w, start=a, end=b, seg=i) for i,(w,a,b) in enumerate([
        ("way…",9.50,9.68), ("Because",9.68,9.86), ("dogged.",14.92,15.36),
        ("Yeah,",15.52,15.84), ("scan",16.16,16.34), ("legacy",18.26,18.46),
        ("through.",20.98,21.12)])]
    pack["take"]["dur"] = pack["words"]["duration"] = 22.12
    coverage = pack["coverage"]
    coverage["words"] = pack["words"]["words"] = words
    views = [dict(shot=[s["t0"],s["t1"]]) for s in shots]
    coverage["source_reading"] = source.bind(coverage, views, dict(
        voice_entries=[dict(first=1,frame=0,speaker="Jason Calacanis",listener="Ali Ghodsi"),
                       dict(first=3,frame=1,speaker="Ali Ghodsi",listener="Jason Calacanis")],
        closing_views=[dict(first=6,frame=2,box=[.2,0,.7,1]),dict(first=6,frame=3,box=[0,0,.5,1])]))
    script = dict(title="The Coming Surge in Code", segments=[dict(say=w["w"]) for w in words])
    scene,_ = film.build(script, pack, "footage-cut", dict(coverage_final=True,caption_ends=list(range(len(words)))))
    for frame,name in [(292,"Jason Calacanis"),(468,"Ali Ghodsi")]:
        visible = [it for it in scene["items"] if it["f"][0] <= frame < it["f"][1]]
        assert any(it.get("t") == name and it.get("fact_type") for it in visible)
        assert all(it.get("pic","plate") == "plate" for it in visible)
    last = [it for it in scene["items"] if it.get("closing_view")]
    assert last[-2]["f"][1] == last[-1]["f"][0] == 651
    assert last[-1]["src"][0] == pytest.approx(648/30)
    assert last[-1]["crop"][2] <= .5
    assert (last[-1]["x"],last[-1]["y"],last[-1]["w"],last[-1]["h"]) == (
        last[-2]["x"],last[-2]["y"],last[-2]["w"],last[-2]["h"])


def test_checked_examples_share_the_legacy_word_and_leave_the_next_picture():
    from wf1_evidence_reading import seat
    rows = [
        [32,70,'photo','opening',{}],
        [478,511,'photo','scan',dict(provider='primary-document',window=1,page='tutorial',
            document=dict(url='figure'),action_box=[.012,.049,.241,.505],mark_box=[.123,.423,.203,.499])],
        [515,565,'photo','release',dict(provider='primary-document',window=2,page='release')],
        [612,620,'photo','other',{}]]
    coverage = dict(windows=[{},dict(example=dict(source='tutorial',figure_url='figure',
                    reading=dict(first=64,at=67,last=76))),dict(example=dict(source='release',
                    reading=dict(first=77,at=77,last=88)))])
    sources = [dict(url=u) for u in ('original','rename','release')]
    history = [dict(source=s['url'],stamp=d,label=t) for s,d,t in zip(sources,
               ['2004','Python 3','25 Oct 2026'],['cookielib','http.cookiejar','Updated'])]
    research = [dict(windows=[2],sources=sources,history=history)]
    frames = {64:468,67:487,77:550,89:606}
    result = seat(rows,coverage,research,frames.get,98,664)
    assert [r[:2] for r in result] == [[32,70],[468,550],[550,606],[612,620]]
    assert result[1][4]['spoken_reading']['at'] == 487
    assert [r['source'] for r in result[2][4]['spoken_reading']['history']] == sources
    assert result[0] == rows[0] and result[3] == rows[3]
