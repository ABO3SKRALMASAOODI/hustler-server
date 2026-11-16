"""Full-bleed cutaways and other editors' text (owner's rating, 16 Nov 2026)."""
import io
import json
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import placement_check as pc  # noqa: E402
import wf1_film as film  # noqa: E402

FF = shutil.which("ffmpeg")


def test_a_wide_clip_is_framed_full_bleed_round_its_subject():
    rec = dict(w=1920, h=1080, x=0.7, faces=[])
    assert film.clip_cover_up(rec) == pytest.approx(1920 / 1080)
    m = film.clip_meta(rec)
    assert m["pw"] == 1920 and m["ph"] == 1080 and m["focus"] == (0.7, 0.45)
    assert m["max_up"] == pytest.approx(1920 / 1080 * 1.12, abs=1e-3)


def test_a_portrait_clip_keeps_the_earlier_limits():
    face = dict(box=[0.3, 0.2, 0.7, 0.5], eyes=[[0.4, 0.3], [0.6, 0.3]])
    assert film.clip_meta(dict(w=1080, h=1920, faces=[face]))["max_up"] == 1.0
    assert film.clip_meta(dict(w=1080, h=1920, faces=[]))["max_up"] == pytest.approx(1.12)


def test_only_a_clip_too_small_for_the_phone_is_left_in_a_window():
    assert film.clip_cover_up(dict(w=1280, h=720)) <= film.F.MAX_UP
    assert film.clip_cover_up(dict(w=640, h=360)) > film.F.MAX_UP


def test_the_check_sees_the_phone_slice_the_film_shows():
    view = pc.visible_slice(dict(w=1920, h=1080, x=0.9), "clip")
    assert view[1] == pytest.approx(1.0) and view[1] - view[0] == pytest.approx(1080 * 9 / 16 / 1920)
    assert pc.visible_slice(dict(w=1080, h=1920, x=0.5), "clip") is None
    assert pc.visible_slice(dict(w=1920, h=1080, composed_fps=30), "clip") is None
    assert pc.visible_slice(dict(w=1920, h=1080), "photo") is None


def _subtitled(path):
    import cv2
    import numpy as np
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30, (1920, 1080))
    for k in range(45):
        f = np.zeros((1080, 1920, 3), np.uint8)
        f[:] = (50, 90, 130)
        cv2.circle(f, (500 + k * 6, 320), 150, (210, 170, 70), -1)
        cv2.putText(f, ["one line of their words", "and then another one"][k // 23], (420, 975),
                    cv2.FONT_HERSHEY_SIMPLEX, 2.4, (255, 255, 255), 5, cv2.LINE_AA)
        vw.write(f)
    vw.release()
    return str(path)


class Clock:
    words = [dict(w="Nvidia", t0=0, t1=0.6), dict(w="builds", t0=0.6, t1=1.2)]
    segs = [(0, 1.5)]


def _reply(ok_first=True, text_first=True, text_after=False):
    seen = []

    def call(system, content, **kw):
        ids = [json.loads(c["text"])["id"] for c in content if c["type"] == "text" and c["text"].startswith("{")]
        first = not seen
        seen.append(ids)
        foreign = text_first if first else text_after
        rows = [dict(id=i, ok=ok_first if first else True, reason="shows it", motion_ok=True, motion_reason="moves",
                     foreign_text=foreign, text_box=[0.0, 0.84, 1.0, 0.96] if foreign else None,
                     text_reason="someone's subtitles" if foreign else "") for i in ids]
        return json.dumps(dict(placements=rows)), dict(usd=0)
    return call, seen


@pytest.mark.skipif(not FF, reason="no ffmpeg")
def test_other_editors_subtitles_are_cut_away_and_the_clip_checked_again(tmp_path):
    clip = _subtitled(tmp_path / "c.mp4")
    rec = dict(path=clip, w=1920, h=1080, x=0.5, seconds=1.5, src=[0, 1.4], faces=[], source=dict(url="u"))
    call, seen = _reply()
    kept, report = pc.check([[0, 40, "clip", "Nvidia", rec]], dict(segments=[dict(say="Nvidia builds")]), Clock(),
                            str(tmp_path / "work"), FF, call=call)
    assert len(seen) == 2                                  # the cleared clip is a new picture: checked again
    assert len(kept) == 1 and report["text_cleared"]
    new = kept[0][4]
    assert new["path"] != clip and new["h"] < 1080 * 0.84 and new["w"] == 1920, new["text_cleared"]
    assert new["text_cleared"]["rect"][3] < 0.84
    import cv2
    cap = cv2.VideoCapture(new["path"])
    assert int(cap.get(4)) == new["h"]
    cap.release()


@pytest.mark.skipif(not FF, reason="no ffmpeg")
def test_text_that_cannot_be_cut_away_rejects_the_placement(tmp_path):
    clip = _subtitled(tmp_path / "c.mp4")
    rec = dict(path=clip, w=1920, h=1080, x=0.5, seconds=1.5, src=[0, 1.4], faces=[], source=dict(url="u"))

    def call(system, content, **kw):
        ids = [json.loads(c["text"])["id"] for c in content if c["type"] == "text" and c["text"].startswith("{")]
        return json.dumps(dict(placements=[dict(id=i, ok=True, reason="", motion_ok=True, foreign_text=True,
                                               text_box=[0.0, 0.1, 1.0, 0.9], text_reason="a quote card") for i in ids])), {}
    kept, report = pc.check([[0, 40, "clip", "Nvidia", rec]], dict(segments=[dict(say="Nvidia builds")]), Clock(),
                            str(tmp_path / "work"), FF, call=call)
    assert kept == [] and report["rejected"] == 1 and "another editor's text" in report["placements"][0]["reason"]


def test_composed_frames_with_foreign_text_are_rejected_not_repaired(tmp_path, monkeypatch):
    monkeypatch.setattr(pc, "evidence", lambda *a: [b"pixels"])
    p = tmp_path / "film.mp4"
    p.write_bytes(b"film")
    rec = dict(path=str(p), src=[0, 1], composed_fps=30, check_times=[0.1])

    def call(system, content, **kw):
        assert "foreign_text" in system
        return json.dumps(dict(placements=[dict(id=0, ok=True, reason="", motion_ok=False, foreign_text=True,
                                               text_box=[0, 0.8, 1, 0.9], text_reason="a channel's title")])), {}
    kept, report = pc.check([[0, 30, "clip", "Nvidia", rec]], dict(segments=[dict(say="Nvidia builds")]), Clock(),
                            str(tmp_path / "w"), "ffmpeg", call=call, review="transition")
    assert kept == [] and report["foreign_text"] == 1


def test_the_check_asks_for_pictures_of_what_is_said():
    import inspect
    src = inspect.getsource(pc.check)
    assert "merely associated with the topic" in src and "text_box" in src
