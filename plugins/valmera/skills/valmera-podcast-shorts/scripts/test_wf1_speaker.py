"""Who speaks in the take, by signal (wf1_speaker). 19 Nov rating: the hook played over the listener beside the
speaker in the show's two-shot because the take kept the biggest face."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

import wf1_speaker as S


def test_in_a_two_shot_the_moving_mouth_speaks_not_the_bigger_face():
    voiced = np.array([True] * 16 + [False] * 4)
    env = voiced.astype(np.float32)
    talker = np.where(voiced, 0.3, 0.05).astype(np.float32)          # measured talkers read 0.04-0.48
    listener = np.full(20, -0.05, np.float32)                         # measured listeners read -0.22 to 0.01
    who, rows = S.who_speaks([listener, talker], voiced, env)
    assert who == 1 and rows[0]["track"] == 1


def test_no_mouth_follows_the_voice_means_the_speaker_is_off_camera():
    voiced = np.ones(20, bool)
    who, _ = S.who_speaks([np.full(20, 0.005, np.float32), np.full(20, -0.1, np.float32)], voiced, voiced.astype(np.float32))
    assert who is None
    # two mouths moving alike: no one is named (a drink and a word read the same over two seconds)
    who, _ = S.who_speaks([np.full(20, 0.30, np.float32), np.full(20, 0.25, np.float32)], voiced, voiced.astype(np.float32))
    assert who is None


def test_a_face_alone_talks_when_it_moves_with_the_voice():
    voiced = np.array([True, False] * 10)
    act = np.where(voiced, 0.05, 0.0).astype(np.float32)
    who, _ = S.who_speaks([act], voiced, voiced.astype(np.float32))
    assert who == 0
    who, _ = S.who_speaks([np.full(20, -0.08, np.float32)], voiced, voiced.astype(np.float32))
    assert who is None


def test_a_face_off_screen_while_the_voice_speaks_cannot_be_the_speaker():
    voiced = np.ones(20, bool)
    act = np.full(20, np.nan, np.float32)
    act[:3] = 0.5
    assert S.who_speaks([act], voiced, voiced.astype(np.float32))[0] is None


def test_voice_mask_reads_speech_against_its_own_loud_level():
    db = np.array([-20, -22, -60, -58, -21, -25, -70, -19], np.float32)
    voiced, env = S.voice_mask(db)
    assert voiced.tolist() == [True, True, False, False, True, True, False, True]
    assert not S.voice_mask(np.full(8, -70, np.float32))[0].any()


def test_short_runs_join_their_neighbour():
    labels = ["a"] * 10 + ["b"] * 3 + ["a"] * 10 + ["c"] * 12
    assert S.runs_of(labels, 8) == [[0, 23, "a"], [23, 35, "c"]]


def test_one_person_across_the_shows_cameras():
    rng = np.random.default_rng(0)
    zuck, wang = rng.normal(size=128), rng.normal(size=128)

    def tr(v, n=30):
        return dict(faces=[(k, {}) for k in range(n)], emb=[v + rng.normal(scale=0.05, size=128) for _ in range(3)])
    ids = S.identities([[tr(wang), tr(zuck)], [tr(zuck, 40)], [tr(zuck), tr(wang)]])
    assert ids[0][1] == ids[1][0] == ids[2][0] and ids[0][0] == ids[2][1] and ids[0][0] != ids[0][1]


def _win(t, who, on):
    return dict(t=t, who=who, on=on, voiced=True)


def test_a_turn_belongs_to_whoever_spoke_most_of_it():
    """A drink in the wide (the host's glass moving for two windows) does not take the guest's turn; the host's own
    marked turn is his."""
    w = [_win(0, 0, {0: .4, 1: .0}), _win(2, 0, {0: .3, 1: .0}), _win(4, 2, {0: .1, 2: .9}), _win(6, 2, {0: .05, 2: .4}),
         _win(8, 0, {0: .3, 2: .0}), _win(10, 0, {0: .4}), _win(12, 2, {2: .5})]
    got = S.turn_speakers(w, [(0, 12), (12, 14)])
    assert [got[i] for i in range(6)] == [0, 0, 0, 0, 0, 0]
    assert got[6] == 2
    # a long unmarked stretch won by someone else is his own turn (a question the captions did not mark)
    w2 = [_win(2 * i, 0, {0: .4, 1: .0}) for i in range(4)] + [_win(8 + 2 * i, 1, {0: .0, 1: .4}) for i in range(3)]
    got = S.turn_speakers(w2, [(0, 30)])
    assert [got[i] for i in range(7)] == [0, 0, 0, 0, 1, 1, 1]


def test_the_turns_speaker_off_screen_leaves_a_listener_span():
    w = [_win(0, 0, {0: .4}), _win(2, None, {1: .0})]
    got = S.turn_speakers(w, [(0, 10)])
    assert got[0] == 0 and got[1] is None


def test_listener_spans_and_on_camera_share():
    cuts = [dict(spans=[dict(t0=0, t1=2, who="speaker"), dict(t0=2, t1=3.5, who="listener")]),
            dict(spans=[dict(t0=3.5, t1=4, who="listener"), dict(t0=4, t1=8, who="quiet")])]
    assert S.listener_spans(cuts) == [(2, 4)]
    assert S.on_camera(cuts) == 0.5


def test_caption_turns_and_off_camera_words(tmp_path):
    import wf1_short
    words = [[100.0, "So"], [100.4, "I"], [101.0, ">> Oh,"], [101.3, "it"], [102.5, ">> Right."]]
    (tmp_path / "words.json").write_text(json.dumps(words))
    ctx = SimpleNamespace(args=dict(words=str(tmp_path / "words.json")))
    assert wf1_short.caption_turns(ctx, [100.0, 104.0], 4.0) == [(0.0, 1.0), (1.0, 2.5), (2.5, 5.0)]
    ws = [dict(w="a", start=0.1, end=0.3), dict(w="b", start=1.0, end=1.2), dict(w="c", start=1.4, end=1.6), dict(w="d", start=3.0, end=3.2)]
    cuts = [dict(spans=[dict(t0=0, t1=0.9, who="speaker"), dict(t0=0.9, t1=2.0, who="listener"), dict(t0=2.0, t1=4.0, who="speaker")])]
    assert wf1_short.off_camera(cuts, ws) == [dict(first=1, last=2, t0=0.9, t1=2.0)]


def test_the_coverage_director_is_told_which_words_play_on_a_listener(tmp_path):
    import wf1_coverage
    words = [dict(w=w, start=i * .4, end=i * .4 + .3) for i, w in enumerate("Meta rebuilt the whole team last year".split())]
    seen = {}

    def ask(system, content, **kw):
        seen.update(system=system, content=json.loads(content))
        return json.dumps(dict(entities=[], windows=[dict(first=0, last=6, name="Meta", external=True, must_show="Meta HQ", why="x")], moves=[])), {}
    llm = SimpleNamespace(ask=ask, json_block=json.loads)
    out = wf1_coverage.plan(words, "t", str(tmp_path), llm, off_camera=[dict(first=2, last=5, t0=.8, t1=2.3)])
    assert seen["content"]["off_camera"] == [dict(first=2, last=5)] and "LISTENER" in seen["system"]
    assert out["off_camera"] == [dict(first=2, last=5)]
    # the same words with no listener span are planned again, not served the listener plan
    seen.clear()
    wf1_coverage.plan(words, "t", str(tmp_path), llm)
    assert "off_camera" not in seen["content"]


def test_the_framing_box_holds_a_head_that_moves():
    """A head that leans inside a long shot stays inside the crop: the box covers where it moves."""
    def face(x):
        return dict(box=[x, .2, x + .08, .4], eyes=[[x + .02, .27], [x + .06, .27]])
    track = [(k, face(.30 + .002 * k)) for k in range(50)]
    b = S.track_box(track)["box"]
    assert b[0] <= .32 and b[2] >= .46 and b[2] - b[0] < 2.2 * .08
    far = [(k, face(.1 + .02 * k)) for k in range(40)]                     # a walk across the frame: the median look
    assert S.track_box(far)["box"][2] - S.track_box(far)["box"][0] == pytest.approx(.08, abs=1e-4)
