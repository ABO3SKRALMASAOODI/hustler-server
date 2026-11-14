"""The story reel's cut and the stories read, on synthetic supply (no model, no network, no render).

    $PY -m pytest -q test_reel.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(os.path.dirname(HERE), "_shell")]
import film as F  # noqa: E402
import reel as R  # noqa: E402
import voice  # noqa: E402


def _script(n=6, words=12):
    segs = []
    for i in range(n):
        segs.append(dict(say=" ".join(f"w{i}x{k}" for k in range(words)), pictures=["Alpha", "Beta"] if i % 2 else ["Alpha"],
                         beat=["hook", "setup", "setup", "low", "turn", "payoff"][i % 6]))
    return dict(title="t", segments=segs)


def _words(script, wps=3.2):
    out, t = [], 0.0
    for i, s in enumerate(script["segments"]):
        for w in s["say"].split():
            out.append(dict(w=w, start=round(t, 3), end=round(t + 0.25, 3), seg=i))
            t += 1 / wps
        t += 0.22
    return dict(words=out, duration=round(t, 2))


def _supply(n_photos=3, n_clips=2):
    pics = {nm: [dict(path=f"/x/{nm}{k}.jpg", w=2000, h=1500, faces=[]) for k in range(n_photos)] for nm in ("Alpha", "Beta")}
    footage = {i: [dict(path=f"/x/c{i}_{k}.mp4", seconds=1.6, subject="Alpha" if k % 2 == 0 else "Beta", w=1080, h=1920)
                   for k in range(n_clips)] for i in range(6)}
    return pics, footage


def test_cut_never_repeats_and_keeps_pace():
    sc = _script()
    clock = F.Clock(_words(sc), sc)
    pics, footage = _supply()
    sp = R.Supply(pics, footage)
    f_end = F.Clock.f(clock.dur) + 15
    tl = R.cut_story(sc, clock, sp, f_end, stress=[], aim_s=0.6)
    assert tl[0][0] == 0 and tl[-1][1] == f_end
    for a, b in zip(tl, tl[1:]):
        assert a[1] == b[0]                                   # no gap
        assert a[4]["path"] != b[4]["path"] or a[2] == "clip"   # never the same photograph twice in a row
    uses = {}
    for r in tl:
        if r[2] == "photo":
            uses.setdefault(r[4]["path"], []).append(r[0])
    assert max(len(v) for v in uses.values()) <= R.MAX_USES
    for v in uses.values():
        if len(v) > 1:
            assert v[1] - v[0] >= R.REUSE_AFTER_S * F.FPS
    for path, ranges in sp.played.items():                     # a clip's seconds are played twice only as a late replay
        ranges = sorted(ranges)
        overlap = any(b0 < a1 - 1e-6 for (a0, a1), (b0, b1) in zip(ranges, ranges[1:]))
        if overlap:
            shows = sorted(sp.shown_at[path])
            assert len(shows) <= R.MAX_USES + 1 and shows[-1] - shows[0] >= 6.0 * F.FPS
    secs = clock.dur
    assert len(tl) / secs * 10 >= 9                             # synthetic supply: dense enough without holding


def test_talking_clips_only_in_collage_lines():
    sc = _script()
    clock = F.Clock(_words(sc), sc)
    pics, footage = _supply(n_photos=1, n_clips=2)
    talk = {c["path"] for v in footage.values() for c in v if c["subject"] == "Alpha"}
    sp = R.Supply(pics, footage, talk=talk, allow={2})
    tl = R.cut_story(sc, clock, sp, F.Clock.f(clock.dur) + 15, stress=[], aim_s=0.6)
    seg_f = [F.Clock.f(clock.segs[i][0]) if i else 0 for i in range(len(sc["segments"]))]

    def seg_at(f):
        return max(i for i in range(len(seg_f)) if seg_f[i] <= f + 1)
    for r in tl:
        if r[2] == "clip" and r[4]["path"] in talk:
            assert seg_at(r[0]) == 2


def test_section_finish_by_era_and_beat():
    st = R.STYLES["reel"]
    assert R.section_finish(st, dict(beat="low", era=2020)) == "mono"
    assert R.section_finish(st, dict(beat="setup", era=1973)) == "gate"
    assert R.section_finish(st, dict(beat="setup", era=1995)) == "crt"
    assert R.section_finish(st, dict(beat="payoff", era=2024)) is None
    assert R.section_finish(R.STYLES["archive"], dict(beat="setup", era=1995)) == "gate"


def test_talking_without_a_file_is_false():
    assert R.talking(dict(path="/nonexistent.mp4")) == (False, 0.0)


def test_caption_seat_stays_dark_over_white_archive_and_flash(tmp_path):
    import numpy as np
    import pytest
    pytest.importorskip("moderngl")
    from scene import compile as compile_scene
    from scene.run import Engine

    for transition in (False, True):
        plan = F.Plan({}, {})
        plan.page([0, 30], "#FFFFFF", "#FFFFFF", grain=0)
        if transition:
            plan.flash([0, 30], mode="fill", c="#FFFFFF", a=0.8, z=8)
        R.caption_pool(plan, 30, 0.615)
        l1 = plan.done("caption-contrast", 30, 1,
                       dict(grain=0, vignette=0, over_type=False))
        engine = Engine(compile_scene.compile(l1, str(tmp_path), segment=False))
        try:
            frame = engine.stills([15])[15]
        finally:
            engine.close()
        h, w = frame.shape[:2]
        band = frame[int((0.615 - 0.045) * h):int((0.615 + 0.012) * h),
                     int(0.2 * w):int(0.8 * w), :3]
        assert float(np.median(band)) / 255 < 0.6


def test_generations_are_whole_sentences():
    segs = [dict(text="One two three four five six.", beat="hook"), dict(text="Seven eight nine ten,", beat="setup"),
            dict(text="eleven twelve.", beat="setup"), dict(text="A turn comes here.", beat="turn"),
            dict(text="The payoff lands.", beat="payoff")]
    g = voice.groups_of(segs)
    assert [x for grp in g for x in grp] == list(range(len(segs)))
    for grp in g:
        assert segs[grp[-1]]["text"].rstrip().endswith((".", "?", "!"))
    assert any(grp[0] == 3 for grp in g) and any(grp[0] == 4 for grp in g)      # a turn starts its own generation
    gs = voice.group_script(dict(segments=segs), g)
    assert gs["segments"][-1]["pause_after"] == 0.0
    assert all(s["pause_after"] in (voice.JOIN_S, voice.BEAT_S) for s in gs["segments"][:-1])


def test_lra_and_envelope():
    import numpy as np
    sr = 16000
    x = (np.random.default_rng(0).standard_normal(sr * 20) * 0.05).astype(np.float32)
    g = voice.envelope(len(x), sr, [(5.0, 10.0, -6.0)], 0.1)
    assert abs(float(g[int(7 * sr)]) + 6.0) < 1e-3 and abs(float(g[int(15 * sr)])) < 1e-6


def test_one_command_helpers(tmp_path, monkeypatch):
    import json as _json
    monkeypatch.setenv("STORY_NO_REEXEC", "1")
    import importlib
    story = importlib.import_module("story")
    assert story.last_json('log\n{"a": 1}\nmore\n{"delivered": "/x"}\n') == {"delivered": "/x"}
    assert story.last_json("nothing") == {}
    folder = tmp_path / "film"
    folder.mkdir()
    rec = dict(delivered="now", style=dict(name="Story Reel"), music=dict(credit="Track by Someone (licence)"),
               script=dict(script=dict(title="T", facts=[dict(claim="c", quote="q", url="https://e.org")])),
               checks=dict(stories=dict(credits=[dict(title="clip", credit="ch", licence="footage", page="https://y.t/x", time_range=[1.0, 2.5])])))
    (folder / "film.json").write_text(_json.dumps(rec))
    story.sources_md(str(folder))
    md = (folder / "SOURCES.md").read_text()
    assert "https://e.org" in md and "https://y.t/x" in md and "1.0-2.5 s" in md and "Track by Someone" in md
    monkeypatch.setattr(story, "STATE_ROOT", str(tmp_path / "state"))
    (tmp_path / "state" / "work" / "r1").mkdir(parents=True)
    (tmp_path / "state" / "work" / "r1" / "run.json").write_text(_json.dumps(dict(workflow="wf3", pid=None, args=dict(line="Ada", one_command=True))))
    assert story.stopped_run("Ada") == "r1" and story.stopped_run(None) is None
