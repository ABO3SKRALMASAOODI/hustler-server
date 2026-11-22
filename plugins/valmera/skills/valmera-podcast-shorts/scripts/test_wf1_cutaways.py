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

FF = shutil.which("ffmpeg") or next((p for p in ["/Users/masaoodi/Documents/Valmera/.valmera/week-1106/gauntlet/tools/ff/ffmpeg"]
                                     if os.path.exists(p)), None)


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


def _clip(path, w, h, seconds=1.0):
    import subprocess
    subprocess.run([FF, "-v", "error", "-f", "lavfi", "-i", f"testsrc=size={w}x{h}:rate=30:d={seconds}", "-pix_fmt", "yuv420p",
                    "-y", str(path)], check=True)
    return str(path)


@pytest.mark.skipif(not FF, reason="no ffmpeg")
def test_a_clip_too_small_for_the_phone_is_brought_up_to_fill_it_never_put_in_a_window(tmp_path):
    """Round 1, 19 Nov: every clip under 640 px tall (and every window/prints clip, fetched at 1080x608) was shown as a
    window card about 29% of the frame over its own blur. It is now scaled up once with a detail-keeping scaler."""
    import subprocess
    assert film.clip_cover_up(dict(w=1280, h=720)) <= film.F.MAX_UP
    big = dict(path=_clip(tmp_path / "hd.mp4", 1280, 720), w=1280, h=720, seconds=1.0)
    assert film.fill_size(big, str(tmp_path / "work"), FF) is big            # fills within 3x: as it is
    small = dict(path=_clip(tmp_path / "sd.mp4", 640, 360), w=640, h=360, seconds=1.0, x=0.6,
                 faces=[dict(box=[0.4, 0.2, 0.6, 0.6])])
    assert film.clip_cover_up(small) > film.F.MAX_UP
    filled = film.fill_size(small, str(tmp_path / "work"), FF)
    assert filled["path"] != small["path"] and os.path.isfile(filled["path"])
    assert film.clip_cover_up(filled) == pytest.approx(1.0, abs=0.01)
    assert (filled["w"], filled["h"]) == (3414, 1920) and filled["upscaled_from"]["h"] == 360
    assert filled["x"] == 0.6 and filled["faces"] == small["faces"]           # fractions stand
    probe = subprocess.run([FF, "-i", filled["path"]], capture_output=True, text=True).stderr
    assert "3414x1920" in probe
    again = film.fill_size(small, str(tmp_path / "work"), FF)                 # once: the same file on a rebuild
    assert again["path"] == filled["path"]
    unreadable = dict(small, path=str(tmp_path / "gone.mp4"))
    assert film.fill_size(unreadable, str(tmp_path / "work"), FF) is unreadable


def test_a_clip_framed_again_is_never_pushed_past_its_cover_and_sits_on_its_face():
    face = dict(box=[0.62, 0.2, 0.78, 0.4], eyes=[[0.66, 0.28], [0.74, 0.28]])
    m = film.clip_meta(dict(w=1920, h=1080, x=0.3, faces=[face], reframe=True))
    assert m["max_up"] == pytest.approx(1920 / 1080, abs=1e-3) and m["focus"] == (pytest.approx(0.7), pytest.approx(0.28))
    assert film.clip_meta(dict(w=1920, h=1080, x=0.3, faces=[face]))["max_up"] == pytest.approx(1920 / 1080 * 1.12, abs=1e-3)


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
def test_text_that_cannot_be_cut_away_stays_and_the_picture_keeps_its_place(tmp_path):
    """19 Nov m01: a Bloomberg graphic judged a fit five times lost its place each time to its own banner. Text no
    band can shed stays on the picture, recorded; the picture is never lost to it."""
    clip = _subtitled(tmp_path / "c.mp4")
    rec = dict(path=clip, w=1920, h=1080, x=0.5, seconds=1.5, src=[0, 1.4], faces=[], source=dict(url="u"))

    def call(system, content, **kw):
        ids = [json.loads(c["text"])["id"] for c in content if c["type"] == "text" and c["text"].startswith("{")]
        return json.dumps(dict(placements=[dict(id=i, ok=True, reason="", motion_ok=True, foreign_text=True,
                                               text_box=[0.0, 0.1, 1.0, 0.9], text_reason="a quote card") for i in ids])), {}
    kept, report = pc.check([[0, 40, "clip", "Nvidia", rec]], dict(segments=[dict(say="Nvidia builds")]), Clock(),
                            str(tmp_path / "work"), FF, call=call)
    assert len(kept) == 1 and kept[0][4]["path"] == clip and report["rejected"] == 0
    assert report["placements"][0]["text_kept"] == "a quote card" and report["text_kept"] == 1


def test_composed_frames_judge_composing_only_and_never_lose_a_picture_to_text(tmp_path, monkeypatch):
    monkeypatch.setattr(pc, "evidence", lambda *a: [b"pixels"])
    p = tmp_path / "film.mp4"
    p.write_bytes(b"film")
    rec = dict(path=str(p), src=[0, 1], composed_fps=30, check_times=[0.1])

    def call(system, content, **kw):
        assert "foreign_text" in system
        return json.dumps(dict(placements=[dict(id=0, ok=True, reason="", motion_ok=False, foreign_text=True,
                                               text_box=[0, 0.8, 1, 0.9], text_reason="a channel's title")])), {}
    kept, report = pc.check([[0, 30, "clip", "Nvidia", rec]], dict(segments=[dict(say="Nvidia builds")]), Clock(),
                            str(tmp_path / "w"), "ffmpeg", call=call, review="composed")
    assert len(kept) == 1 and report["foreign_text"] == 1 and report["placements"][0]["fault"] == "none"

    def cut_face(system, content, **kw):
        assert pc.COMPOSED in system
        return json.dumps(dict(placements=[dict(id=0, ok=False, reason="the crop cuts his chin", motion_ok=False)])), {}
    rec2 = dict(rec, check_times=[0.2])
    kept, report = pc.check([[0, 30, "clip", "Nvidia", rec2]], dict(segments=[dict(say="Nvidia builds")]), Clock(),
                            str(tmp_path / "w2"), "ffmpeg", call=cut_face, review="composed")
    assert kept == [] and report["placements"][0]["fault"] == "reframe"    # a 'no' without a class: framed again first


def test_the_check_chooses_and_says_no_only_for_a_contradiction():
    """Round 1, 19 Nov: 'readable attribution in every sampled frame; otherwise reject' and 'merely associated ...
    must be rejected' emptied six of twelve films (a 'Steve Jobs holding a small staff meeting' source under 'even
    other founder CEOs' was rejected nine times for having no lower third)."""
    assert "ONLY for a contradiction" in pc.SYSTEM and "fit ranks the candidates" in pc.SYSTEM
    assert "on-screen attribution is not required" in pc.SYSTEM and "attribution_text" in pc.SYSTEM
    assert "text_box" in pc.SYSTEM and "subject_x" in pc.SYSTEM
    for veto in ("otherwise reject", "must be rejected", "cannot be established from these frames and the context, reject"):
        assert veto not in pc.SYSTEM and veto not in pc.COMPOSED


@pytest.mark.skipif(not FF, reason="no ffmpeg")
@pytest.mark.parametrize("style", ["window", "prints", "footage-cut", "archive-reel"])
def test_every_style_cuts_a_small_clip_full_bleed_with_its_size_on_record(tmp_path, monkeypatch, style):
    """Round 1, 19 Nov: the window card (a 16:9 box about 29% of the frame over its own blur) held every clip of four
    films. A 640x360 clip now fills the phone in every style, and the record says its size and where it came from."""
    import placement_check
    words = "Meta rebuilt its whole team from the ground up last year".split()
    wj = dict(duration=4.6, words=[dict(w=w, start=.1 + i * .4, end=.45 + i * .4, seg=0) for i, w in enumerate(words)])
    script = dict(title="Meta", segments=[dict(say=" ".join(words), pictures=["Meta"])])
    clip = dict(path=_clip(tmp_path / "small.mp4", 640, 360, seconds=3.0), w=640, h=360, seconds=3.0, x=0.5, faces=[],
                still=None, subject="Meta", rung="default", source_size=[640, 360], how="whole",
                source=dict(url="https://youtube.example/meta", title="Meta all-hands", channel="Meta"))
    sup = dict(words=wj, work=str(tmp_path / "work"), ffmpeg=FF, pictures={}, footage=[dict(i=0, clips=[clip])],
               take=dict(path="/take.mp4", w=1920, h=1080, fps=30, dur=4.8, faces=[]),
               coverage=dict(words=wj["words"], moves=[], windows=[dict(t0=.1, t1=4.4, name="Meta", say=" ".join(words),
                                                                        must_show="Meta")]))
    monkeypatch.setattr(film.F, "voice_stress", lambda sup: [])
    monkeypatch.setattr(film.F, "engines_finish", lambda l1, *a, **k: l1)

    def check(rows, *a, **k):
        report = dict(placements=[dict(id=i, ok=True, fit=7, motion_ok=True) for i in range(len(rows))], rejected=0, spend=[])
        for r in rows:
            r[4].update(verified_motion=True, placed_motion_p90=2.0)
        return rows, report
    monkeypatch.setattr(placement_check, "check", check)
    import wf1_coverage
    monkeypatch.setattr(wf1_coverage, "speaker_moves", lambda *a, **k: ([], []))      # no model: the speaker's holds stay
    l1, beats = film.build(script, sup, style, dict(coverage_final=True))
    clips = [p for p in beats["pictures"] if p["kind"] == "clip"]
    assert clips and all(p["mode"] == "bleed" for p in clips)
    assert not [it for it in l1["items"] if it["k"] == "card"]
    assert all((p["w"], p["h"]) == (3414, 1920) and p["upscaled_from"]["h"] == 360 and p["rung"] == "default"
               and p["window"] == 0 and p["clause"] == script["segments"][0]["say"] for p in clips)


def _meta_short(tmp_path, clips):
    words = "Meta rebuilt its whole team from the ground up last year".split()
    wj = dict(duration=4.6, words=[dict(w=w, start=.1 + i * .4, end=.45 + i * .4, seg=0) for i, w in enumerate(words)])
    script = dict(title="Meta", segments=[dict(say=" ".join(words), pictures=["Meta"])])
    sup = dict(words=wj, work=str(tmp_path / "work"), ffmpeg=FF, pictures={}, footage=[dict(i=0, clips=clips)],
               take=dict(path="/take.mp4", w=1920, h=1080, fps=30, dur=4.8, faces=[]),
               coverage=dict(words=wj["words"], moves=[], windows=[dict(t0=.1, t1=4.4, name="Meta", say=" ".join(words),
                                                                        must_show="Meta")]))
    return script, sup


def _offline_build(monkeypatch):
    monkeypatch.setattr(film.F, "voice_stress", lambda sup: [])
    monkeypatch.setattr(film.F, "engines_finish", lambda l1, *a, **k: l1)
    import wf1_coverage
    monkeypatch.setattr(wf1_coverage, "speaker_moves", lambda *a, **k: ([], []))


def _meta_clip(tmp_path, name, **kw):
    return dict(dict(path=_clip(tmp_path / f"{name}.mp4", 1280, 720, seconds=3.0), w=1280, h=720, seconds=3.0, x=0.5, faces=[],
                     still=None, subject="Meta", source=dict(url=f"https://example.org/{name}", title=f"Meta {name}", channel="Meta")), **kw)


@pytest.mark.skipif(not FF, reason="no ffmpeg")
def test_a_text_cleared_clip_found_twice_by_the_composed_review_leaves_its_window_and_the_film_is_delivered(tmp_path, monkeypatch):
    """Adversarial review, 20 Nov: a clip the check cut clear of foreign text is placed under a new path, and the
    composed review's findings were keyed on that path. Neither the reframe nor the contradiction ever reached the
    candidate the next build proposed; the third pass raised 'made no progress' and the short was lost. Findings are now
    keyed on the candidate: framed again, then the window's slot goes to its next picture, and the film is delivered."""
    import placement_check
    import wf1_short as wf
    from types import SimpleNamespace
    a, b = _meta_clip(tmp_path, "a"), _meta_clip(tmp_path, "b")
    script, sup = _meta_short(tmp_path, [a, b])
    _offline_build(monkeypatch)
    cleared = tmp_path / "work" / "text-cleared" / "a-cleared.mp4"
    cleared.parent.mkdir(parents=True)
    shutil.copy(a["path"], cleared)

    def check(rows, *args, **kw):
        out = []
        for r in rows:
            rec = r[4]
            if rec["path"] == a["path"]:                                  # the check cuts a's subtitles away: a new file
                rec = dict(rec, path=str(cleared), text_cleared=dict(from_path=a["path"], rect=[0, 0, 1, .8]))
            rec.update(verified_motion=True, placed_motion_p90=2.0)
            out.append([r[0], r[1], r[2], r[3], rec])
        fit = {a["path"]: 9, b["path"]: 6}
        return out, dict(placements=[dict(id=i, ok=True, fit=fit[r[4]["path"]], motion_ok=True) for i, r in enumerate(rows)],
                         rejected=0, spend=[])
    monkeypatch.setattr(placement_check, "check", check)
    supply = tmp_path / "supply"
    supply.mkdir()
    ctx = SimpleNamespace(supply=str(supply), run_dir=str(tmp_path), log=lambda _: None)
    builds = []

    def score(*_):
        found = wf.current_rejections(str(supply))
        knobs = dict(coverage_final=True, reject=[r for r in found if r.get("fault") != "reframe"],
                     reframe=[r for r in found if r.get("fault") == "reframe"])
        _, beats = film.build(script, sup, "footage-cut", knobs)
        builds.append(beats["pictures"])
        return dict(beats=beats, spend=[])

    def review(ctx_, rd, sc):                               # the composed frames: a's crop cuts through a face, every time
        pics = sc["beats"]["pictures"]
        rows = [dict(id=k, ok=p["origin"] != a["path"], fault="reframe", reason="the crop cuts his face", motion_ok=True,
                     f=p["f"]) for k, p in enumerate(pics)]
        return dict(rejected=sum(not r["ok"] for r in rows), placements=rows, moving_share=1.0, spend=[])
    monkeypatch.setattr(wf, "score", score)
    monkeypatch.setattr(wf, "_render_review", review)
    monkeypatch.setattr(wf, "_repair_window_boundaries", lambda *args: None)
    monkeypatch.setattr(wf.wf3, "render", lambda *args: dict(film=str(tmp_path / "programme.mp4")))
    sc = score()
    first = [p for p in builds[0] if p["window"] == 0]
    assert first[0]["path"] == str(cleared) and first[0]["origin"] == a["path"] and not first[0]["reframed"]
    rd = wf.render(ctx, script, sc)
    assert rd["film"].endswith("programme.mp4") and sc["beats"]["rendered_check"]["rejected"] == 0
    assert len(builds) == 3
    again = [p for p in builds[1] if p["origin"] == a["path"]]
    assert again and again[0]["path"] == str(cleared) and again[0]["reframed"] is True       # framed again, still cleared
    assert not [p for p in builds[2] if p["window"] == 0 and p["origin"] == a["path"]]      # then the window gives it away
    assert [p["origin"] for p in builds[2] if p["window"] == 0] == [b["path"]]
    rows = wf.current_rejections(str(supply))
    assert [(r["window"], r["path"], r["fault"]) for r in rows] == [(0, a["path"], "reframe"), (0, a["path"], "contradiction")]
    assert all(r["placed"] == str(cleared) for r in rows)


def test_a_finding_that_survives_every_repair_gives_its_window_to_the_speaker_then_stands_on_record(tmp_path, monkeypatch):
    import wf1_short as wf
    import wf1_coverage
    from types import SimpleNamespace
    ctx = SimpleNamespace(supply=str(tmp_path), run_dir=str(tmp_path), log=lambda _: None)
    monkeypatch.setattr(wf, "_repair_window_boundaries", lambda *args: None)
    monkeypatch.setattr(wf, "score", lambda *a: dict(beats=dict(pictures=[dict(path="/w/c.mp4", origin="/c.mp4", window=2, reframed=True)]), spend=[]))
    sc = dict(beats=dict(pictures=[dict(path="/w/c.mp4", origin="/c.mp4", window=2)]), spend=[])
    finding = dict(placements=[dict(id=0, ok=False, fault="reframe")])
    assert [wf._rebuild_rejected(ctx, {}, sc, finding) for _ in range(4)] == [True, True, True, False]
    rows = wf.current_rejections(str(tmp_path))
    assert [(r["window"], r["path"], r["fault"]) for r in rows] == [(2, "/c.mp4", "reframe"), (2, "/c.mp4", "contradiction"),
                                                                     (2, None, "speaker")]
    pairs = {(r["window"], r["path"]) for r in rows if r["fault"] != "reframe"}
    assert wf1_coverage.is_rejected(pairs, 2, dict(path="/other.mp4"))                   # the window rests on the speaker
    assert not wf1_coverage.is_rejected(pairs, 1, dict(path="/c.mp4"))                    # every other window keeps it
    assert wf1_coverage.is_rejected({(1, "/c.mp4")}, 1, dict(path="/w/x.mp4", text_cleared=dict(from_path="/c.mp4")))
    assert wf.unrepaired(dict(path="/w/c.mp4", origin="/c.mp4", window=2, reframed=True), rows)


@pytest.mark.skipif(not FF, reason="no ffmpeg")
def test_source_attribution_alone_does_not_print_an_unspoken_name(tmp_path, monkeypatch):
    """The frame reader sees Susan Li, while the transcript here speaks about Meta."""
    import placement_check
    clip = _meta_clip(tmp_path, "li")
    script, sup = _meta_short(tmp_path, [clip])
    _offline_build(monkeypatch)
    calls = []

    def call(system, content, **kw):
        calls.append(system)
        ids = [json.loads(c["text"])["id"] for c in content if c["type"] == "text" and c["text"].startswith("{")]
        return json.dumps(dict(placements=[dict(id=i, ok=True, fit=9, reason="she is named", motion_ok=True,
                                                name_super="Susan Li, Fox Business", attribution_text="SUSAN LI lower third")
                                           for i in ids])), dict(usd=0)
    real = placement_check.check
    monkeypatch.setattr(placement_check, "check", lambda rows, *a, **k: real(rows, *a, call=call, **k))
    l1, beats = film.build(script, sup, "footage-cut", dict(coverage_final=True))
    assert calls and "name_super" in calls[0]
    clips = [p for p in beats["pictures"] if p["kind"] == "clip"]
    assert clips and clips[0]["attribution"] == "SUSAN LI lower third"
    assert not beats["labels"]
    assert not [it for it in l1["items"] if it.get("name_picture")]
    taste = film.F.taste_check(l1, beats)
    assert not [p for p in taste["problems"] if "SUSAN" in p or "FOX" in p], taste["problems"]
