"""Real voices repaired, never refused, when a workflow opts in (voice.find_voices repair=True); the product's own
defaults unchanged (stories round 1, 19 Nov: Jobs's Macworld 1997 words refused for 11.6 s and for 1984/2010 photos)."""
import sys
import types

import numpy as np
import pytest

from footage import ask, identity, voice


def _words(spec, t=10.0, gap=0.08, dur=0.32):
    out = []
    for w in spec.split():
        out.append(dict(w=w, start=round(t, 3), end=round(t + dur, 3)))
        t += dur + gap
    return out


def test_the_product_prompt_is_unchanged_and_the_repair_prompt_names_the_references_band():
    assert "about 2 to 9 seconds spoken, no interviewer" in voice.PICK_SYSTEM.format(most=2, span=voice.SPAN_DEFAULT)
    assert "about 2 to 12 seconds" in voice.PICK_SYSTEM.format(most=2, span=voice.SPAN_REPAIR)


def test_best_span_trims_a_long_choice_to_a_sentence_end_inside_the_band():
    ws = _words("We have to let go of this notion. That for Apple to win Microsoft has to lose. "
                "We have to embrace a notion that for Apple to win Apple has to do a really good job and if others "
                "are going to help us that's great")
    i, j = 0, len(ws)
    t0, t1 = voice.edges(ws, i, j)
    assert t1 - t0 > 12.0
    a, b = voice.best_span(ws, i, j, 12.0)
    s0, s1 = voice.edges(ws, a, b)
    assert s1 - s0 <= 12.0 and s1 - s0 >= voice.MIN_S
    assert ws[b - 1]["w"].endswith(".") and a == 0          # whole sentences from the choice's own start


def test_run_on_finishes_a_short_choice_at_its_sentence_end():
    ws = _words("I think so. Because we were going to die otherwise.")
    i, j = voice.run_on(ws, 3, 5, 12.0)           # "Because we" (0.7 s) runs on to "otherwise."
    assert (i, j) == (3, len(ws))


def test_own_line_measures_a_line_that_is_the_persons_sentence():
    assert voice.own_line("for Apple to win, Microsoft has to lose", "that for Apple to win Microsoft has to lose. We") > 0.9
    assert voice.own_line("The deal was signed in August.", "I think it is a great day for Apple") < 0.3


def _fake_cv2(monkeypatch):
    fake = types.SimpleNamespace(FaceDetectorYN=types.SimpleNamespace(create=lambda *a: object()),
                                 FaceRecognizerSF=types.SimpleNamespace(create=lambda *a: object()))
    monkeypatch.setitem(sys.modules, "cv2", fake)
    monkeypatch.setattr(identity, "models", lambda: ("y", "s"))


def _unit(*v):
    v = np.array(v + (0.0,) * (8 - len(v)), dtype=np.float32)
    return v / np.linalg.norm(v)


def _face_world(monkeypatch, by_path):
    _fake_cv2(monkeypatch)
    monkeypatch.setattr(identity, "full_frame", lambda proxy, t, out: f"frame@{t:.3f}")
    monkeypatch.setattr(identity, "faces", lambda p, det, rec: [(v, [0, 0, 80, 80], 0.1, 0.5) for v in by_path(p)])


def test_face_check_anchors_on_the_sources_own_frames_when_old_photographs_do_not_match(monkeypatch, tmp_path):
    jobs97, jobs84 = _unit(1, 0.1), _unit(0, 0, 1)

    def by_path(p):
        if p.startswith("photo"):
            return [jobs84]                      # another decade: no match on 1997 stage frames
        if p.startswith("own"):
            return [jobs97]
        return [jobs97]
    _face_world(monkeypatch, by_path)
    got = voice.face_check("proxy", 1.0, 4.0, ["photo1"], ["own1", "own2", "own3"], str(tmp_path))
    assert got["verdict"] == "seen" and got["matched"] >= 2
    # the same stretch against the photographs alone: not refused, kept as unseen after looking at more frames
    alone = voice.face_check("proxy", 1.0, 4.0, ["photo1"], [], str(tmp_path))
    assert alone["verdict"] == "unseen" and alone["frames"] == voice.FACE_FRAMES + voice.MORE_FRAMES


def test_face_check_keeps_a_voice_over_a_wide_stage_and_names_another_speaker_only_against_own_frames(monkeypatch, tmp_path):
    jobs97, host = _unit(1, 0.1), _unit(0, 1)
    _face_world(monkeypatch, lambda p: [jobs97] if p.startswith("own") else [])
    wide = voice.face_check("proxy", 1.0, 4.0, [], ["own1", "own2"], str(tmp_path))
    assert wide["verdict"] == "unseen" and wide["faceless"] == wide["frames"]
    _face_world(monkeypatch, lambda p: [jobs97] if p.startswith("own") else [host])
    other = voice.face_check("proxy", 1.0, 4.0, [], ["own1", "own2"], str(tmp_path))
    assert other["verdict"] == "other"


def test_pick_asks_the_next_route_when_the_provider_does_not_answer(monkeypatch):
    calls = []

    def fake(system, content, purpose, budget_s=120.0, max_tokens=6000, model=None):
        calls.append(model)
        if model is None:
            raise RuntimeError("OpenAI HTTP 503: daily call cap reached")
        return '{"voices":[{"src":0,"piece_t0":12,"after":5,"person":"Steve Jobs","said":"we have to let go"}]}', 0.01
    monkeypatch.setattr(ask, "ask", fake)
    monkeypatch.setattr(voice.time, "sleep", lambda s: None)
    src = [dict(title="Macworld 1997", channel="x", transcript=[dict(t0=12, t1=24, text="we have to let go")])]
    got, _ = voice.pick([dict(i=5, say="a")], ["Steve Jobs"], src, repair=True)
    assert got and got[0]["after"] == 5 and calls == [None] + voice.ROUTES
    with pytest.raises(RuntimeError):        # the product's pick is as before: one route
        voice.pick([dict(i=5, say="a")], ["Steve Jobs"], src)


def _voice_world(monkeypatch, tmp_path, words, face):
    monkeypatch.setattr(voice, "pick", lambda *a, **k: ([dict(src=0, piece_t0=10.0, after=5, person="Steve Jobs",
                                                              said=" ".join(w["w"] for w in words))], 0.0))
    monkeypatch.setattr(voice, "heard_words", lambda proxy, t0, t1, work: words)
    monkeypatch.setattr(voice, "audio", lambda *a, **k: open(a[3], "w").close())
    monkeypatch.setattr(voice.cut, "local", lambda src, t0, t1, out: open(out, "w").close())
    monkeypatch.setattr(voice.cut, "finish", lambda raw, out, W, H, x: open(out, "w").close())
    monkeypatch.setattr(voice, "face_shows", lambda *a: 0)               # the product's check: 0 of 3 frames
    monkeypatch.setattr(voice, "face_check", lambda *a: face)
    monkeypatch.setattr(identity, "full_frame", lambda proxy, t, out: out)
    subjects = [dict(name="Steve Jobs", kind="person")]
    sources = [dict(key="s0", url="u", title="Macworld Boston 1997", transcript=[dict(t0=10, t1=22, text="x")], proxy="p", subjects=[0],
                    whole="w.mp4", duration=600.0)]
    return subjects, sources


def test_repair_trims_a_long_voice_and_keeps_an_unseen_speaker_over_the_persons_own_frames(monkeypatch, tmp_path):
    words = _words("We have to let go of this notion. That for Apple to win Microsoft has to lose. "
                   "We have to embrace a notion that for Apple to win Apple has to do a really good job and if others "
                   "are going to help us that's great")
    subjects, sources = _voice_world(monkeypatch, tmp_path, words, dict(verdict="unseen", matched=0, frames=9))
    lines = [dict(i=5, say="He walked onto the stage in Boston and turned to the screen.")]
    tried = []
    out, _ = voice.find_voices(lines, subjects, sources, {"Steve Jobs": ["old.jpg"]}, str(tmp_path), 1080, 1920, log=lambda m: None,
                               tried=tried, repair=True, own={"s0": [("Steve Jobs", 300.0), ("Apple", 5.0)]})
    assert len(out) == 1 and out[0]["seconds"] <= voice.REPAIR_MAX_S and out[0]["repaired"].startswith("trimmed")
    assert out[0]["said"].endswith(".")
    assert out[0]["picture_range"][0] == pytest.approx(300.0 - out[0]["seconds"] / 2, abs=0.05)
    assert tried and tried[0]["reason"] is None


def test_the_product_still_refuses_as_before_without_repair(monkeypatch, tmp_path):
    words = _words(" ".join(["word"] * 40) + ".")           # 16 s: outside 1.8-9.5 s
    subjects, sources = _voice_world(monkeypatch, tmp_path, words, dict(verdict="seen", matched=3, frames=3))
    tried = []
    out, _ = voice.find_voices([dict(i=5, say="a")], subjects, sources, {}, str(tmp_path), 1080, 1920, log=lambda m: None, tried=tried)
    assert out == [] and "outside" in tried[0]["reason"]
