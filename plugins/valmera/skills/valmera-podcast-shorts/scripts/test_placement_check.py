import os,sys
sys.path.insert(0,os.path.dirname(__file__))
from placement_check import accepted, propose, words_under


def test_missing_duplicate_and_truthy_model_values_cannot_approve():
    rows={'placements':[{'id':0,'ok':True},{'id':1,'ok':'true'}, {'id':2,'ok':True},{'id':2,'ok':False}]}
    got=accepted(rows,[0,1,2,3])
    assert [got[i]['ok'] for i in range(4)] == [True,False,False,False]


def test_proposals_do_not_borrow_footage_from_another_sentence_or_repeat_it():
    class Clock:
        segs=[(0,3),(3,6),(6,9)]
    clip=dict(path='a.mp4',seconds=2,subject='a')
    script={'segments':[{'say':'a'},{'say':'unrelated'},{'say':'a again'}]}
    rows=propose(script,Clock(),{0:[clip],2:[clip]}, {},0,270,[])
    assert len(rows)==1 and rows[0][1]<=90
    assert abs(rows[0][4]['src'][1]-rows[0][4]['src'][0]-(rows[0][1]-rows[0][0])/30)<0.001


def test_words_use_overlap_with_displayed_interval():
    class Clock:
        words=[dict(w='Earth',t0=1,t1=1.3),dict(w='Sun',t0=1.4,t1=1.8)]
    assert words_under(Clock(),30,40)=='Earth'


def test_source_provenance_reaches_checker_and_invalidates_cached_decision(tmp_path,monkeypatch):
    import json
    import placement_check as pc
    path=tmp_path/'clip.mp4';path.write_bytes(b'fixture')
    monkeypatch.setattr(pc,'evidence',lambda *a:b'pixels')
    class Clock:
        words=[dict(w='Google',t0=0,t1=1)]
        segs=[(0,1)]
    calls=[]
    def call(system,content,**kwargs):
        row=json.loads(content[0]['text']);calls.append(row['source_evidence'])
        return json.dumps({'placements':[{'id':0,'ok':False,'reason':'test'}]}),{}
    rec=dict(path=str(path),src=[0,1],source=dict(url='https://example.org/film',channel='Original publisher'))
    args=([[0,30,'clip','Google',rec]],{'segments':[{'say':'Google'}]},Clock(),str(tmp_path),'unused')
    pc.check(*args,call=call);pc.check(*args,call=call)
    rec['source']['channel']='Different publisher'
    pc.check(*args,call=call)
    assert [x['channel'] for x in calls]==['Original publisher','Different publisher']


def test_composed_frame_evidence_preserves_phone_resolution_and_half_open_cut(tmp_path,monkeypatch):
    import io
    from PIL import Image
    import placement_check as pc
    from types import SimpleNamespace
    buffer=io.BytesIO();Image.new('RGB',(1080,1920),'red').save(buffer,format='JPEG')
    commands=[]
    def run(cmd,**kwargs):
        commands.append(cmd);return SimpleNamespace(stdout=buffer.getvalue())
    monkeypatch.setattr(pc.subprocess,'run',run)
    frames=pc.evidence([0,60,'clip','subject',dict(path='composed.mp4',src=[0,2],check_times=[0,1,59/30],composed_fps=30)],'ffmpeg')
    assert len(frames)==3
    assert all(Image.open(io.BytesIO(x)).size==(720,1280) for x in frames)
    assert float(commands[-1][commands[-1].index('-ss')+1])<59/30
    assert float(commands[0][commands[0].index('-ss')+1])==0


def test_caption_context_is_separate_from_display_claim_and_rechecked_on_change(tmp_path,monkeypatch):
    import json
    import placement_check as pc
    path=tmp_path/'film.mp4';path.write_bytes(b'fixture')
    monkeypatch.setattr(pc,'evidence',lambda *a:b'pixels')
    class Clock:
        words=[dict(w='the',t0=0,t1=.3),dict(w='Sun',t0=.3,t1=1)]
        segs=[(0,1)]
    calls=[]
    def call(system,content,**kwargs):
        calls.append(json.loads(content[0]['text']))
        return json.dumps({'placements':[dict(id=0,ok=False,reason='fixture review')]}),{}
    rec=dict(path=str(path),src=[.3,1],caption_words=[dict(f=[0,30],text='the')],display_words=[dict(f=[9,30],text='SUN')])
    args=([[9,30,'clip','Sun',rec]],dict(segments=[dict(say='the Sun')]),Clock(),str(tmp_path),'unused')
    pc.check(*args,call=call,review='transition');pc.check(*args,call=call,review='transition')
    rec['caption_words']=[dict(f=[0,30],text='Earth')]
    pc.check(*args,call=call,review='transition')
    assert len(calls)==2
    assert calls[0]['caption_words'][0]['text']=='the'
    assert calls[1]['caption_words'][0]['text']=='Earth'
    assert calls[0]['display_words']==calls[1]['display_words']


def test_rejected_frame_evidence_survives_replacing_the_render(tmp_path,monkeypatch):
    import json
    import placement_check as pc
    path=tmp_path/'film.mp4';path.write_bytes(b'old-render')
    monkeypatch.setattr(pc,'evidence',lambda *a:[b'exact-frame-one',b'exact-frame-two'])
    class Clock:
        words=[dict(w='Sun',t0=0,t1=1)]
        segs=[(0,1)]
    def call(*a,**kw):return json.dumps({'placements':[dict(id=0,ok=False,reason='wrong object')]}),{}
    _,report=pc.check([[0,30,'clip','Sun',dict(path=str(path),src=[0,1])]],dict(segments=[dict(say='Sun')]),Clock(),str(tmp_path),'unused',call=call)
    path.unlink()
    saved=tmp_path/'rejected-frames'/report['placements'][0]['key']
    assert (saved/'00.jpg').read_bytes()==b'exact-frame-one'
    assert (saved/'01.jpg').read_bytes()==b'exact-frame-two'
    assert json.loads((saved/'finding.json').read_text())['reason']=='wrong object'


# 16 Nov cost lane: the frames per placement of every composed review of the three shorts the Jensen Huang run made
# (staging-1115/xCUala5j7aQ/evidence/mNN/render_reviews.json; 3 frames, plus 3 round each display word's edges), and
# the calls those reviews cost then (batches of 3 on the proxy, a second transition pass after a clean first one).
JENSEN_RENDER = {
    'm02': [[3, 3, 3, 3, 3, 3, 3, 6, 6, 6]],
    'm03': [[3, 6, 6, 3, 6, 4, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 6, 6, 3, 6, 6, 3, 3, 3, 3, 3, 3],
            [3, 6, 6, 3, 6, 4, 3, 3, 3, 3, 3, 3, 3, 3, 3, 6, 6, 3, 6, 6, 3, 3, 3, 3, 3, 3],
            [3, 6, 6, 3, 6, 4, 3, 3, 3, 3, 3, 3, 3, 3, 3, 6, 6, 3, 6, 6, 3, 3, 3, 3, 3, 3]],
    'm04': [[3, 3, 3, 3, 3, 3, 3, 3, 6, 5, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3]],
}
JENSEN_RENDER_CALLS = {'m02': 8, 'm03': 10 + 18 + 18, 'm04': 18}


def _counting(monkeypatch, frames):
    """A fake model that approves every placement it is shown, counting calls and images; evidence gives each row its
    own frames (frames[i] of them, tagged by the row's name and the render it came from)."""
    import json
    import placement_check as pc
    monkeypatch.setattr(pc, 'evidence', lambda row, ffmpeg: [f"{row[4]['tag']}|{row[3]}|{k}".encode() for k in range(row[4]['n'])])
    calls = []

    def call(system, content, **kwargs):
        ids = [json.loads(c['text'])['id'] for c in content if c['type'] == 'text' and c['text'].startswith('{')]
        calls.append(dict(system=system, ids=ids, images=sum(c['type'] == 'image' for c in content)))
        return json.dumps({'placements': [dict(id=i, ok=True, reason='fits', motion_ok=True) for i in ids]}), {}
    return pc, calls, call


def _rows(path, frames, tag='render-1'):
    return [[i * 30, i * 30 + 30, 'clip', f'p{i}', dict(path=str(path), src=[i, i + 1], composed_fps=30, n=n, tag=tag)]
            for i, n in enumerate(frames)]


class _Clock:
    words = [dict(w='word', t0=0, t1=1)]
    segs = [(0, 1000)]


def test_recorded_composed_reviews_cost_under_a_third_of_their_calls(tmp_path, monkeypatch):
    """Every placement of every recorded review is still shown to the model with all its frames, in one composed
    pass, packed MAX_PLACEMENTS / MAX_IMAGES a call: 72 calls on the Jensen run's three made shorts become 21."""
    pc, calls, call = _counting(monkeypatch, None)
    film = tmp_path / 'film.mp4'
    film.write_bytes(b'render')
    got = {}
    for short, reviews in JENSEN_RENDER.items():
        before = len(calls)
        for n, frames in enumerate(reviews):
            # each recorded review was a fresh render: no frame of it is reused here
            _, report = pc.check(_rows(film, frames, tag=f'{short}-{n}'), dict(segments=[dict(say='word')]), _Clock(),
                                 str(tmp_path / short), 'unused', call=call, review='composed')
            assert report['accepted'] == len(frames)
        got[short] = len(calls) - before
    shown = [i for c in calls for i in c['ids']]
    assert len(shown) == sum(len(f) for r in JENSEN_RENDER.values() for f in r)        # nothing skipped
    assert all(c['images'] <= pc.MAX_IMAGES and len(c['ids']) <= pc.MAX_PLACEMENTS for c in calls)
    assert all(pc.COMPOSED in c['system'] for c in calls)
    assert got == {'m02': 2, 'm03': 15, 'm04': 4}
    assert sum(got.values()) * 3 <= sum(JENSEN_RENDER_CALLS.values())


def test_a_rerender_reuses_the_verdicts_of_unchanged_frames_and_checks_changed_ones(tmp_path, monkeypatch):
    pc, calls, call = _counting(monkeypatch, None)
    film = tmp_path / 'film.mp4'
    film.write_bytes(b'render one')
    frames = JENSEN_RENDER['m04'][0]
    args = (dict(segments=[dict(say='word')]), _Clock(), str(tmp_path / 'review'), 'unused')
    pc.check(_rows(film, frames), *args, call=call, review='composed')
    first = len(calls)
    film.write_bytes(b'render two, the same programme')            # a rebuild re-encodes the film
    pc.check(_rows(film, frames), *args, call=call, review='composed')
    assert len(calls) == first
    rows = _rows(film, frames)
    rows[4][4]['tag'] = 'render-2'                                  # one placement's pixels changed
    _, report = pc.check(rows, *args, call=call, review='composed')
    assert len(calls) == first + 1 and calls[-1]['ids'] == [4] and report['accepted'] == len(frames)


def test_batches_keep_each_placement_whole_and_in_order():
    import placement_check as pc
    items = [(dict(id=i), None, [b'x'] * n) for i, n in enumerate([3, 30, 3, 3, 3, 3, 3, 3, 3, 3, 3])]
    got = [[e['id'] for e, _, _ in b] for b in pc.batches(items)]
    assert got == [[0], [1], [2, 3, 4, 5, 6, 7, 8, 9], [10]]


# ---------------------------------------------------------------- round 1, 19 Nov: the check chooses and repairs

FF_LOCAL = next((p for p in [__import__("shutil").which("ffmpeg"),
                             "/Users/masaoodi/Documents/Valmera/.valmera/week-1106/gauntlet/tools/ff/ffmpeg"] if p and os.path.exists(p)), None)


def _lower_third_clip(path):
    """A 16:9 interview with a name super across the bottom left, outside the phone slice round its subject."""
    import cv2
    import numpy as np
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30, (1920, 1080))
    for k in range(60):
        f = np.full((1080, 1920, 3), (60, 70, 80), np.uint8)
        cv2.circle(f, (1300 + k, 420), 160, (190, 170, 150), -1)
        cv2.rectangle(f, (80, 860), (900, 980), (20, 20, 160), -1)
        cv2.putText(f, "SUSAN LI", (110, 950), cv2.FONT_HERSHEY_SIMPLEX, 2.6, (255, 255, 255), 6, cv2.LINE_AA)
        vw.write(f)
    vw.release()
    return str(path)


import pytest  # noqa: E402


@pytest.mark.skipif(not FF_LOCAL, reason="no ffmpeg")
def test_a_wide_clip_is_judged_on_its_full_frame_and_the_slice_the_film_shows(tmp_path, monkeypatch):
    """19 Nov m01: a 'SUSAN LI' lower third outside the phone slice was judged 'cropped'. The model now sees the full
    source frame (the lower third included) and then the phone slice."""
    import io
    from PIL import Image
    import placement_check as pc
    monkeypatch.setattr(sys, "path", list(sys.path))            # the worker it reaches for footage.overlay leaves with the test
    clip = _lower_third_clip(tmp_path / "li.mp4")
    rec = dict(path=clip, w=1920, h=1080, x=0.68, seconds=2.0, src=[0.6, 1.4])
    frames = pc.evidence([0, 30, "clip", "Susan Li", rec], FF_LOCAL)
    sizes = [Image.open(io.BytesIO(x)).size for x in frames]
    assert len(frames) == 4 and sizes[:3] == [(960, 540)] * 3
    assert abs(sizes[3][0] / sizes[3][1] - 9 / 16) < 0.01
    labels = pc.frame_labels([0, 30, "clip", "Susan Li", rec], 4)
    assert labels[0].startswith("full source frame") and labels[3].startswith("the phone-shaped slice")
    # the frames span the whole clip, not the slice the proposal happened to cut
    assert pc.sample_times(rec) == [0.3, 1.0, 1.7]


def _clock():
    class Clock:
        words = [dict(w=w, t0=i * 0.4, t1=i * 0.4 + 0.35) for i, w in enumerate("even other opinionated founder CEOs would not".split())]
        segs = [(0, 3)]
    return Clock()


def _approving(calls, **extra):
    import json

    def call(system, content, **kw):
        rows = [json.loads(c["text"]) for c in content if c["type"] == "text" and c["text"].startswith("{")]
        calls.append(dict(system=system, rows=rows))
        return json.dumps(dict(placements=[dict(id=r["id"], ok=True, fit=8, reason="the very scene", motion_ok=True, **extra)
                                           for r in rows])), {}
    return call


def test_the_verdict_belongs_to_the_picture_and_its_clause_not_its_slice_or_the_brief(tmp_path, monkeypatch):
    """19 Nov: a re-brief and a moved boundary re-judged approved pictures (m03: the watermelon shots approved in
    review 0 were gone after the re-brief; m07: a lab clip approved three times was lost when its slice moved)."""
    import placement_check as pc
    seen = []
    monkeypatch.setattr(pc, "evidence", lambda row, ff: seen.append(pc.sample_times(row[4])) or [b"frame-a", b"frame-b", b"frame-c"])
    calls = []
    clause = "even other opinionated founder CEOs would not"
    rec = dict(path="/clips/jobs.mp4", seconds=2.4, src=[0.2, 1.2], clause=clause, clause_t=[0, 2.8], window=0,
               illustration="Any ONE shot with readable on-screen attribution",
               source=dict(url="https://youtube.example/jobs", title="Steve Jobs holding a small staff meeting in Sept 23, 1997",
                           channel="Jose E. Puente", description="Steve Jobs talks to Apple staff"),
               shows="a man in a black top addressing a room")
    script = dict(segments=[dict(say=clause)])
    pc.check([[0, 30, "clip", "Founder CEOs", rec]], script, _clock(), str(tmp_path), "ff", call=_approving(calls))
    moved = dict(rec, src=[1.0, 2.0], illustration="A complete, uncropped lower third is required")
    kept, report = pc.check([[45, 75, "clip", "Founder CEOs", moved]], script, _clock(), str(tmp_path), "ff", call=_approving(calls))
    assert len(calls) == 1 and report["accepted"] == 1 and kept[0][4]["fit"] == 8
    sent = calls[0]["rows"][0]
    assert sent["words"] == clause and "illustration_intent" not in sent
    assert sent["source_evidence"]["title"].startswith("Steve Jobs") and sent["source_evidence"]["description"]
    assert sent["source_evidence"]["shows"].startswith("a man")
    assert seen[0] == seen[1] == [0.36, 1.2, 2.04]


def test_an_unread_candidate_returns_the_slot_to_the_interview(tmp_path, monkeypatch):
    import json
    import placement_check as pc
    monkeypatch.setattr(pc, "evidence", lambda row, ff: [b"pixels"])
    calls = []

    def call(system, content, **kw):
        calls.append(1)
        return "I could not look at these", {}
    rec = dict(path="/clips/a.mp4", seconds=1.5, src=[0, 1], clause="Nvidia builds", source=dict(url="u", title="Nvidia"))
    kept, report = pc.check([[0, 30, "clip", "Nvidia", rec]], dict(segments=[dict(say="Nvidia builds")]), _clock(), str(tmp_path),
                            "ff", call=call)
    assert len(calls) == 2 and not kept and report["unchecked"] == 1
    assert json.load(open(tmp_path / "placement_checks.json")) == {}          # silence is never kept as a verdict
    later = []
    pc.check([[0, 30, "clip", "Nvidia", rec]], dict(segments=[dict(say="Nvidia builds")]), _clock(), str(tmp_path), "ff",
             call=_approving(later))
    assert len(later) == 1


def test_the_check_ranks_and_recentres_and_a_contradiction_passes_the_slot_on(tmp_path):
    """The model chooses: its fit orders a window's candidates, its subject_x recentres a clip's slice, and a
    contradiction gives the slot to the next candidate, local to that window."""
    import wf1_coverage as cov
    windows = [dict(name="Meta", t0=0.0, t1=3.0, say="Meta rebuilt its whole team", must_show="Meta")]
    clips = {0: [dict(path=f"/c/{n}.mp4", seconds=2.0, x=0.5) for n in ("publix", "weak", "best")]}
    verdicts = {"/c/publix.mp4": dict(ok=False, fit=9, reason="a Publix banner: a different company"),
                "/c/weak.mp4": dict(ok=True, fit=2), "/c/best.mp4": dict(ok=True, fit=9, subject_x=0.8)}
    seen = []

    def checker(rows):
        seen.append([r[4]["path"] for r in rows])
        out = []
        for r in rows:
            v = dict(verdicts[r[4]["path"]], motion_ok=True)
            if v["ok"] and v.get("subject_x") is not None:
                r[4]["x"] = v["subject_x"]
            r[4].update(verified_motion=True, placed_motion_p90=2.0)
            out.append(v)
        return [r for r, v in zip(rows, out) if v["ok"]], dict(placements=out, rejected=sum(not v["ok"] for v in out), spend=[])
    accepted, report = cov.checked_proposals(dict(windows=windows), clips, {}, 3.0, checker)
    assert [r[4]["path"] for r in accepted][0] == "/c/best.mp4"          # the best-ranked first, the contradiction gone
    assert all(r[4]["path"] != "/c/publix.mp4" for r in accepted)
    assert all(r[4]["clause"] == "Meta rebuilt its whole team" for r in accepted)
    assert next(c for c in clips[0] if c["path"] == "/c/best.mp4")["x"] == 0.8
    # the contradiction is local: a second window of another subject may still use the picture
    two = dict(windows=windows + [dict(name="Grocery", t0=4.0, t1=6.0, say="a Publix store", must_show="Publix")])
    rows = cov.proposals(two, {0: clips[0], 1: [clips[0][0]]}, {}, rejected={(0, "/c/publix.mp4")})
    assert [r[4]["path"] for r in rows if r[4]["window"] == 1] == ["/c/publix.mp4"]
    assert "/c/publix.mp4" not in [r[4]["path"] for r in rows if r[4]["window"] == 0]


def test_a_clause_with_few_chosen_pictures_is_covered_by_them_not_handed_back_to_the_speaker():
    """Round 1, 19 Nov (the watermelon film's 34 s hold, four-moves' 15 s): a window with one or two survivors showed
    1.4 s of each and then the speaker for the rest of the clause. The chosen pictures now share the whole clause, a clip
    up to its own seconds; a window with enough pictures still changes about every 1.4 s."""
    import wf1_coverage as cov
    window = dict(name="Watermelon", t0=0.0, t1=8.0, say="the watermelon trick", must_show="a watermelon")
    a, b = 3, 3 + 240
    one = cov.proposals(dict(windows=[window]), {}, {"Watermelon": [dict(path="/p/melon.jpg")]})
    assert [(r[0], r[1]) for r in one] == [(a, b)]                                   # one photo: the whole clause
    short_clip = dict(path="/c/cut.mp4", seconds=2.0)
    two = cov.proposals(dict(windows=[window]), {0: [short_clip]}, {"Watermelon": [dict(path="/p/melon.jpg")]})
    assert two[0][4]["path"] == "/c/cut.mp4" and two[0][1] - two[0][0] <= 2.0 * 30 - 1     # a clip within its own seconds
    assert two[-1][1] == b and two[1][0] == two[0][1]                                  # the photo carries the rest
    assert two[0][4]["src"][1] <= 2.0
    many = {0: [dict(path=f"/c/{n}.mp4", seconds=4.0) for n in range(7)]}
    rows = cov.proposals(dict(windows=[window]), many, {})
    assert len(rows) == 7 and all(r[1] - r[0] <= 1.4 * 30 + 1 for r in rows)          # enough pictures: the usual pace
    assert cov.clause_shares(6.0, [1.0, float("inf"), float("inf")]) == [1.0, 2.5, 2.5]


def test_the_measured_holds_send_their_windows_back_to_research_once(tmp_path, monkeypatch):
    """Verdict better_fix #6: the rendered film's measured holds and cuts feed supply, never a gate. Windows under a
    hold longer than RESEARCH_HOLD_S go back to research once; the rebuilt film then stands whatever it measures."""
    import json
    from types import SimpleNamespace
    import wf1_short as wf
    coverage = dict(windows=[dict(t0=1.0, t1=4.0), dict(t0=6.0, t1=20.0), dict(t0=22.0, t1=24.0)])
    pack = dict(coverage=coverage)
    (tmp_path / "pack.json").write_text(json.dumps(dict(pack=pack)))
    monkeypatch.setattr(wf.wf3, "load_pack", lambda _: pack)
    film = tmp_path / "programme.mp4"
    film.write_bytes(b"x")
    measured = dict(cuts=3, cuts_per_10s=5.0, longest_hold_s=13.0, seconds=30.0, holds=[[7.0, 20.0]])
    monkeypatch.setattr(wf, "measure_cuts", lambda *a, **k: measured)
    asked, rebuilt = [], []
    monkeypatch.setattr(wf, "refill", lambda ctx, script, pack, shortage: asked.append(shortage))
    monkeypatch.setattr(wf, "score", lambda *a: rebuilt.append(1) or dict(beats=dict(pictures=[]), spend=[]))
    ctx = SimpleNamespace(supply=str(tmp_path), run_dir=str(tmp_path), log=lambda _: None)
    sc = dict(beats=dict(pictures=[dict(window=0), dict(window=1)], coverage=dict(real_seconds=9.0, missing_windows=[2])), spend=[])
    assert wf._rebuild_holds(ctx, {}, sc, dict(film=str(film))) is True
    assert asked[0]["weak_windows"] == [1] and asked[0]["real_seconds"] == 9.0 and rebuilt == [1]
    assert sc["beats"]["measured_before_research"] == measured and pack["holds_researched"] is True
    assert wf._rebuild_holds(ctx, {}, sc, dict(film=str(film))) is False             # once a short: the film stands
    assert len(asked) == 1
    # too few cuts: every window with fewer pictures than its clause wants is researched
    few = dict(measured, holds=[], cuts_per_10s=1.0)
    assert wf.hold_windows(few, coverage, [dict(window=0)] * 3 + [dict(window=1)] * 12 + [dict(window=2)]) == [2]
    assert wf.hold_windows(dict(measured, holds=[], cuts_per_10s=6.0), coverage, []) == []


def test_the_check_names_the_object_a_picture_shows_from_the_sound_sets_own_families(tmp_path, monkeypatch):
    """Lane sfx-moments (20 Nov): the raters heard no paper on the letter or the cheque a picture showed. The check (a
    model) names the object, from the families the workflow's sound-effect set holds (its own rows); a name outside
    them is no object; the picture's record carries it to its asset (worker/score/sound_screen.py sounds it)."""
    import json
    import placement_check as pc
    sounds = tmp_path / "set"
    sounds.mkdir()
    (sounds / "manifest.json").write_text(json.dumps([
        {"id": "paper_2", "file": "paper_2.flac", "family": "paper", "use": "book pages flipping: a document on screen"},
        {"id": "shutter_1", "file": "shutter_1.flac", "family": "shutter", "use": "a camera shutter"},
        {"id": "w", "file": "w.flac"}]))
    monkeypatch.setenv("VALMERA_SOUNDS", str(sounds))
    assert pc.foley_menu() == {"paper": "book pages flipping: a document on screen"}     # effects are not objects
    path = tmp_path / "letter.jpg"
    path.write_bytes(b"fixture")
    monkeypatch.setattr(pc, "evidence", lambda *a: b"pixels")

    class Clock:
        words = [dict(w="letter", t0=0, t1=1)]
        segs = [(0, 1)]
    systems = []

    def call(system, content, **kwargs):
        systems.append(system)
        return json.dumps({"placements": [{"id": 0, "ok": True, "fit": 8, "reason": "the letter", "foley": "Paper"},
                                          {"id": 1, "ok": True, "fit": 6, "reason": "a phone", "foley": "phone"}]}), {}
    letter = dict(path=str(path), src=[0, 1], page="https://example.org/letter")
    phone = dict(path=str(tmp_path / "phone.jpg"), src=[0, 1], page="https://example.org/phone")
    (tmp_path / "phone.jpg").write_bytes(b"other")
    kept, report = pc.check([[0, 30, "image", "letter", letter], [30, 60, "image", "phone", phone]],
                            {"segments": [{"say": "letter"}]}, Clock(), str(tmp_path), "unused", call=call)
    assert "foley" in systems[0] and "paper (book pages flipping" in systems[0]
    assert letter["foley"] == "paper" and "foley" not in phone                   # the set holds no phone recording
    assert [p["foley"] for p in report["placements"]] == ["paper", None]
    monkeypatch.delenv("VALMERA_SOUNDS")
    assert pc.foley_menu() == {} and pc.foley_ask({}) == ""                         # no set: nothing asked


def test_a_pictures_object_reaches_its_asset():
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location("film_for_foley", os.path.join(here, "..", "..", "valmera-stories", "film.py"))
    film = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(film)
    except Exception as e:  # noqa: BLE001 - film.py needs the worker on its path in this environment
        pytest.skip(f"film.py not importable here: {e!r}"[:200])
    P = film.Plan({}, {})
    P.image("p00", dict(path="/x/letter.jpg", w=800, h=600, foley="paper"))
    P.video("v01", dict(path="/x/clip.mp4", w=1920, h=1080, fps=30, seconds=3.0))
    assert P.assets["p00"]["foley"] == "paper" and "foley" not in P.assets["v01"]


def test_duplicate_excerpt_in_one_batch_shares_one_source_reading(tmp_path, monkeypatch):
    import placement_check as pc
    monkeypatch.setattr(pc, "evidence", lambda *args: [b"same excerpt"])
    calls = []
    clip = dict(path="/clips/portrait.mp4", seconds=1.5, clause="Alexandr Wang speaks",
                clause_t=[0, 1], source=dict(url="publisher/launch", title="Launch recording"))
    rows = [[0, 30, "clip", "Alexandr Wang", dict(clip)] for _ in range(2)]
    kept, report = pc.check(rows, dict(segments=[dict(say=clip["clause"])]), _clock(), str(tmp_path),
                            "ff", call=_approving(calls, name_super="Alexandr Wang"))
    assert [len(c["rows"]) for c in calls] == [1]
    assert [r[4]["name_super"] for r in kept] == ["Alexandr Wang"] * 2
    assert len(report["placements"]) == 2
