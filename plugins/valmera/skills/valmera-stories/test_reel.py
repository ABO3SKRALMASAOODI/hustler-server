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


def test_section_finish_is_the_writers_device_never_the_years():
    st = R.STYLES["reel"]
    sc = dict(colour_turn=3, segments=[dict(beat="setup", era=1995), dict(beat="setup", era=1973, finish="gate", finish_rank=1),
                                       dict(beat="low", era=1995, finish="bw", finish_rank=2), dict(beat="turn", finish="bw"),
                                       dict(beat="payoff", finish="bw")])
    fin = [R.section_finish(st, s, i, sc) for i, s in enumerate(sc["segments"])]
    assert fin == [None, "gate", "mono", None, None]          # an old era alone is colour; from the turn and on the payoff: colour
    assert R.section_finish(R.STYLES["archive"], dict(beat="setup", era=1995)) is None


def test_device_budget_clipped_by_rank_on_frames():
    sc = dict(segments=[dict(finish_rank=2), dict(finish_rank=1), dict(finish_rank=3), dict()])
    fin, share = R.finish_budget(["mono", "gate", "crt", None], [30, 30, 30, 100], sc)
    assert fin == [None, "gate", None, None] and share == round(30 / 190, 3)


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


# ----------------------------------------------------------------- the fit-gated cut

def _fit_script(n=6, words=14):
    sc = _script(n, words)
    for i, s in enumerate(sc["segments"]):
        s["see"] = [dict(thing="Alpha", as_of="x", must_show="x")] + ([dict(thing="Beta", as_of="x", must_show="x")] if i % 2 else [])
    return sc


def _fit_supply(n_clips=3, motion=2.0):
    pics, footage = _supply(n_photos=2, n_clips=n_clips)
    for i, lst in footage.items():
        for k, c in enumerate(lst):
            c["motion"] = dict(p90=motion, curve=[motion] * 24)
    return pics, footage


def test_shot_draw_follows_the_judge():
    import random
    rng = random.Random(3)
    xs = sorted(R.shot_draw(rng) for _ in range(4000))
    assert xs[len(xs) // 2] <= 0.52 and sum(1 for x in xs if x < 0.5) / len(xs) >= 0.45
    assert sum(1 for x in xs if x >= 2.0) / len(xs) <= 0.03


def test_a_clip_that_fits_only_line_2_never_lands_on_another_line():
    sc = _fit_script()
    clock = F.Clock(_words(sc), sc)
    pics, footage = _fit_supply()
    only = footage[2][0]["path"]
    fit = {c["path"]: ({2} if c["path"] == only else {i}) for i, lst in footage.items() for c in lst}
    fit.update({r["path"]: {0, 1, 2, 3, 4, 5} for lst in pics.values() for r in lst})
    sp = R.Supply(pics, footage, fit=fit)
    tl = R.cut_fit(sc, clock, sp, F.Clock.f(clock.dur) + 15, stress=[])
    seg_f = [F.Clock.f(clock.segs[i][0]) if i else 0 for i in range(len(sc["segments"]))]
    for r in tl:
        line = max(i for i in range(len(seg_f)) if seg_f[i] <= r[0] + 1)
        if r[4]["path"] == only:
            assert line == 2
        if r[2] in ("clip", "photo"):
            assert line in fit[r[4]["path"]]                   # nothing on screen that does not fit its line


def test_an_exhausted_line_gets_a_designed_card_then_a_reframe_never_another_photo():
    sc = _fit_script(n=2, words=24)
    sc["segments"][1]["show"] = [dict(text="$5 million", role="figure", word_i=3)]
    clock = F.Clock(_words(sc), sc)
    pics, footage = _fit_supply(n_clips=1)
    fit = {footage[0][0]["path"]: {0}, footage[1][0]["path"]: {1}}
    fit.update({r["path"]: {0} for lst in pics.values() for r in lst})        # every photograph fits line 0 only
    cards = {1: [dict(text="$5 million", role="figure")]}
    sp = R.Supply(pics, footage, fit=fit, cards=cards)
    tl = R.cut_fit(sc, clock, sp, F.Clock.f(clock.dur) + 15, stress=[])
    a1 = F.Clock.f(clock.segs[1][0])
    on1 = [r for r in tl if r[0] >= a1 - 1]
    assert [r[2] for r in on1][:2] == ["clip", "designed"]
    assert all(r[2] != "photo" for r in on1)
    assert sp.reframed.get(1, 0) <= int(R.REFRAME_MAX_S * F.FPS) and sp.shortfall.get(1, 0) > 0


def test_the_moving_clip_comes_first_and_stills_never_follow_stills():
    sc = _fit_script(n=1, words=30)
    clock = F.Clock(_words(sc), sc)
    footage = {0: [dict(path=f"/x/s{k}.mp4", seconds=1.6, subject="Alpha", w=1080, h=1920, motion=dict(p90=0.2, curve=[0.2] * 24))
                   for k in range(3)] + [dict(path="/x/m.mp4", seconds=1.6, subject="Alpha", w=1080, h=1920, motion=dict(p90=3.0, curve=[3.0] * 24))]}
    sp = R.Supply({}, footage, fit={c["path"]: {0} for c in footage[0]})
    tl = R.cut_fit(sc, clock, sp, F.Clock.f(clock.dur) + 15, stress=[])
    assert tl[0][4]["path"] == "/x/m.mp4"
    for a, b in zip(tl, tl[1:]):
        assert not (not R.moving(a[4]) and not R.moving(b[4]) and b[4].get("tier") != "line") or a[4]["path"] != b[4]["path"]


def test_takes_most_moving_window():
    c = dict(seconds=2.0, motion=dict(curve=[0.1] * 15 + [5.0] * 6 + [0.1] * 9))
    t0, t1 = R.Takes.best(c, (0.0, 2.0), 0.4)
    assert 0.9 <= t0 <= 1.1 and abs(t1 - t0 - 0.4) < 1e-6
    assert R.Takes.of(dict(seconds=2.0, cuts=[1.0])) == [(0.0, 1.0), (1.0, 2.0)]


def test_the_same_take_counts_as_a_repeat():
    footage = {0: [dict(path="/a.mp4", take_group="g"), dict(path="/b.mp4", take_group="g")]}
    pics = [dict(f=[0, 10], kind="clip", path="/a.mp4", take=0), dict(f=[10, 20], kind="clip", path="/b.mp4", take=0),
            dict(f=[20, 30], kind="photo", path="/p.jpg")]
    t = R.take_stats(pics, footage)
    assert t["repeats"] == 1 and t["new_pictures_10s_n"] == 2


def test_no_pool_item_covers_a_white_frame():
    P = F.Plan({}, {})
    R.caption_pool(P, 100, 0.615, holes=[(20, 22), (50, 51)])
    pools = [it for it in P.items if it["k"] == "flash" and it["mode"] == "pool"]
    for f in (20, 21, 50):
        assert not any(p["f"][0] <= f < p["f"][1] for p in pools)
    assert sum(p["f"][1] - p["f"][0] for p in pools) == 97


def _files(tmp_path, footage, pics):
    from PIL import Image
    for lst in list(footage.values()) + list(pics.values()):
        for c in lst:
            p = str(tmp_path / os.path.basename(c["path"]).replace(".mp4", ".jpg"))
            Image.new("RGB", (int(c["w"]), int(c["h"])), (90, 100, 110)).save(p)
            c["path"] = p


def test_fit_build_compiles_passes_taste_and_prints_keep_moving(tmp_path):
    import scene
    sc = _fit_script(n=6, words=14)
    sc["colour_turn"] = 3
    sc["segments"][1].update(finish="gate", finish_rank=1, collage=True)
    sc["segments"][5]["show"] = [dict(text="$5 million", role="figure", word_i=2)]
    sc["segments"][2]["show"] = [dict(text="Ada Lovelace", role="name", word_i=0)]
    for s in sc["segments"]:
        s["phrases"] = [[0, 4], [4, 9], [9, 14]]
    pics, footage = _fit_supply(n_clips=4)
    for lst in footage.values():
        for c in lst:
            c.update(w=1920, h=1080) if c["path"].endswith("_1.mp4") else None
    _files(tmp_path, footage, pics)
    fit = {c["path"]: [i] for i, lst in footage.items() for c in lst}
    fit.update({r["path"]: [0, 1, 2, 3, 4, 5] for lst in pics.values() for r in lst})
    sup = dict(sid="t", words=_words(sc), narration=str(tmp_path / "none.wav"), ffmpeg="ffmpeg", pictures=pics, sounds={},
               footage=[dict(i=i, clips=v) for i, v in footage.items()], fit=fit, fit_verdicts={}, work=str(tmp_path / "fin"))
    l1, beats = R.build(sc, sup, "reel", {})
    F._type()
    ok, why = scene.eligible(l1)
    assert ok, why
    t = F.taste_check(l1, beats)
    assert t["ok"], t["problems"]
    m = beats["measured"]
    assert m["repeat_share"] == 0 and beats["display_dropped"] == []
    assert m["device_segments"] == [1] and m["colour_turn_ramp_f"] is not None
    prints = [it for it in l1["items"] if it["k"] == "card" and isinstance(it.get("s"), list) and isinstance(it.get("x"), list)]
    assert prints
    for it in prints:
        tail = it["s"][8:]
        assert len(tail) < 2 or all(b > a for a, b in zip(tail, tail[1:]))      # never flat after landing
    figs = [it for it in l1["items"] if it["k"] == "word" and "5 MILLION" in it["t"]]
    assert len(figs) == 1 and figs[0]["f"][1] - figs[0]["f"][0] >= int(1.2 * F.FPS)      # said once: as display type or as a card


def test_the_talking_rule_is_skipped_when_the_verdict_says_not_live_action(tmp_path):
    sc = _fit_script(n=6, words=14)
    pics, footage = _fit_supply(n_clips=2)
    _files(tmp_path, footage, pics)
    talk = {c["path"]: dict(talking=True, score=2.0) for lst in footage.values() for c in lst}
    ver = {c["path"]: {str(i): dict(live_action=False, speaking_on_camera=True)} for i, lst in footage.items() for c in lst}
    fit = {c["path"]: [i] for i, lst in footage.items() for c in lst}
    sup = dict(sid="t", words=_words(sc), narration=str(tmp_path / "none.wav"), ffmpeg="ffmpeg", pictures=pics, sounds={},
               footage=[dict(i=i, clips=v) for i, v in footage.items()], fit=fit, fit_verdicts=ver, talking=talk, work=str(tmp_path / "fin"))
    l1, beats = R.build(sc, sup, "reel", {})
    assert beats["measured"]["talking_live"] == 0
    assert any(p["kind"] == "clip" and p["mode"] == "bleed" for p in beats["pictures"])


def test_a_build_without_fit_cuts_as_before(tmp_path):
    sc = _script()
    pics, footage = _supply()
    _files(tmp_path, footage, pics)
    sup = dict(sid="t", words=_words(sc), narration=str(tmp_path / "none.wav"), ffmpeg="ffmpeg", pictures=pics, sounds={},
               footage=[dict(i=i, clips=v) for i, v in footage.items()], work=str(tmp_path / "fin"))
    l1, beats = R.build(sc, sup, "reel", {})
    clock = F.Clock(_words(sc), sc)
    sp = R.Supply(pics, footage)
    tl = R.cut_story(sc, clock, sp, F.Clock.f(clock.dur) + 15, R.F.voice_stress(sup), aim_s=R.STYLES["reel"]["aim_s"])
    assert sorted((p["f"][0], p["path"]) for p in beats["pictures"]) == sorted((r[0], r[4]["path"]) for r in tl)
    assert beats.get("display_dropped") is None and "repeat_share" not in beats["measured"]
