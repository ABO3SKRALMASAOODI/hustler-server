"""The take follows who speaks and moves like an editor; the clip keeps a breath before the end card (19 Nov rating,
causes static-take and clip-edges)."""
import os

import pytest

import wf1_film as film


def cuts_two_shot():
    """The show's own edit: one two-shot (the guest on the left speaks, then the host on the right), then a single."""
    zuck = dict(box=[.28, .19, .36, .38], eyes=[[.30, .26], [.34, .26]], path=[])
    wang = dict(box=[.65, .17, .74, .40], eyes=[[.67, .25], [.72, .25]], path=[])
    return [dict(t0=0.0, t1=4.0, faces=[dict(box=wang["box"], eyes=wang["eyes"], score=1.0)],
                 spans=[dict(t0=0.0, t1=2.5, who="speaker", box=zuck, track=1), dict(t0=2.5, t1=4.0, who="speaker", box=wang, track=0)]),
            dict(t0=4.0, t1=7.0, faces=[dict(box=zuck["box"], eyes=zuck["eyes"], score=1.0)],
                 spans=[dict(t0=4.0, t1=4.6, who="quiet", box=None), dict(t0=4.6, t1=7.0, who="speaker", box=zuck, track=0)])]


def test_take_pieces_frame_whoever_speaks_and_punch_in_on_the_payoff():
    pieces = film.take_pieces(cuts_two_shot(), 7.0, punch=[int(5.5 * 30) + 3])
    assert [p["f"] for p in pieces] == [[0, 78], [78, 123], [123, 168], [168, 213]]
    assert pieces[0]["faces"][0]["box"][0] == .28            # the guest speaking, not the bigger face beside him
    assert pieces[1]["faces"][0]["box"][0] == .65            # the host's answer: a cut inside the show's two-shot
    assert pieces[2]["faces"][0]["box"][0] == .28            # the quiet start of the single keeps the speaker's framing
    assert [p["punch"] for p in pieces] == [False, False, False, True]


def test_old_cuts_without_spans_stay_one_piece_a_shot():
    cuts = [dict(t0=0.0, t1=3.0, faces=[dict(box=[.4, .2, .5, .4], eyes=[[.42, .27], [.48, .27]], score=1.0)]),
            dict(t0=3.0, t1=6.0, faces=[])]
    pieces = film.take_pieces(cuts, 6.0)
    assert [p["f"] for p in pieces] == [[0, 93], [93, 183]]
    assert pieces[0]["faces"] and pieces[1]["faces"] == []


def test_stress_moves_break_the_holds_the_director_left():
    holds = [[30, 200], [300, 330]]
    stress = [(40, 0.9), (100, 0.7), (150, 0.95), (195, 1.0), (310, 0.9)]
    assert film.stress_moves(holds, stress) == [150]


def _sup(tmp_path, cuts):
    words = "My favorite use case is having it coach me between the rounds and that is a hard problem".split()
    wj = dict(duration=7.0, words=[dict(w=w, start=round(.1 + i * .36, 3), end=round(.38 + i * .36, 3), seg=0 if i < 9 else 1)
                                   for i, w in enumerate(words)])
    return dict(words=wj, work=str(tmp_path), ffmpeg="ffmpeg", pictures={}, footage=[], narration="/take.mp4",
                take=dict(path="/take.mp4", w=1920, h=1080, fps=30, dur=7.0, faces=[], cuts=cuts),
                coverage=dict(words=wj["words"], moves=[], windows=[]))


@pytest.fixture
def quiet_engines(monkeypatch):
    monkeypatch.setattr(film.F, "voice_stress", lambda sup: [])
    monkeypatch.setattr(film.F, "engines_finish", lambda l1, *a, **k: l1)
    import placement_check
    monkeypatch.setattr(placement_check, "check", lambda rows, *a, **k: (rows, dict(placements=[], rejected=[], spend=[])))


def test_build_frames_the_speaker_ends_after_a_breath_and_flashes_the_turn(tmp_path, quiet_engines):
    words = _sup(tmp_path, cuts_two_shot())
    script = dict(title="Coach", segments=[dict(say=" ".join(w["w"] for w in words["words"]["words"] if w["seg"] == i), pictures=[])
                                           for i in (0, 1)])
    l1, beats = film.build(script, words, "footage-cut", dict(turn_seg=1, coverage_final=True))
    last_word_f = round(words["words"]["words"][-1]["end"] * 30) + 3
    # the programme holds its breath after the last word (the clip kept it); the card joins after it
    assert beats["dur_f"] == min(last_word_f + film.BREATH_F, round(7.0 * 30) + 3)
    assert beats["voice_f"][1] == last_word_f
    # the take is cut where the speaker changes inside the show's two-shot, and punches in on the payoff
    pieces = beats["coverage"]["take_pieces"]
    assert any(p["punch"] for p in pieces) and len(pieces) >= 4
    assert beats["finishes"]["flashes"] == 1
    assert beats["turn_f"] in [it["f"][0] for it in l1["items"] if it["k"] == "flash" and it.get("mode") == "fill"]
    metas = beats["face_metadata"]
    takes = [it for it in l1["items"] if it["k"] == "shot"]
    assert {metas[it["pic"]]["face_h"] for it in takes if not metas[it["pic"]]["face_h"] == film.PUNCH_FACE_H} == \
        {film.medium_face_h([[0, 0, 0, .19]], 1080), film.medium_face_h([[0, 0, 0, .23]], 1080)}
    assert any(metas[it["pic"]]["face_h"] == film.PUNCH_FACE_H for it in takes)
    assert all(metas[it["pic"]]["max_up"] == film.TAKE_UP for it in takes)


def test_the_medium_leaves_room_for_a_real_punch_in():
    """A face 20% of a 1080p two-shot reaches the 3x ceiling at a 0.3 share: the medium widens so the payoff's
    close-up is still 1.3x tighter (19 Nov: every 'move' on the take was 4-7%)."""
    assert film.medium_face_h([[.3, .2, .08, .2]], 1080) == pytest.approx(3.0 / 1.3 * .2 * 1080 / 1920, abs=1e-4)
    assert film.medium_face_h([[.3, .2, .2, .5]], 2160) == film.TAKE_FACE_H         # a big face in 4K: the references' medium
    assert film.medium_face_h([], 1080) == film.TAKE_FACE_H
