"""The per-line picture verdicts (fit.py) and the story reel's shot audit, on a stubbed model: no network, no spend.

    $PY -m pytest -q test_fit.py
"""
import json
import os
import sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(os.path.dirname(HERE), "_shell")]
import fit as FIT  # noqa: E402


def _sc():
    return dict(segments=[dict(say="Woody almost killed Toy Story.", see=[dict(thing="Toy Story", as_of="1993 reel")], beat="hook", era=1993),
                          dict(say="In the finished film the fall was an accident.", see=[dict(thing="Toy Story", as_of="1995 release")], beat="setup"),
                          dict(say="John Lasseter directed it.", see=[dict(thing="John Lasseter", as_of="1993")], beat="low")])


def _jpg(tmp_path, name):
    from PIL import Image
    p = str(tmp_path / f"{name}.jpg")
    Image.new("RGB", (400, 300), (120, 90, 60)).save(p)
    return p


def _verdict(**kw):
    v = dict(fits_line=True, exact_thing=True, time_ok=True, version_ok=True, contradicts=False, grade=4, live_action=False,
             speaking_on_camera=False, shows="x", why="y")
    v.update(kw)
    return v


def test_the_gate_reads_only_the_booleans():
    assert FIT.usable(_verdict())
    assert not FIT.usable(_verdict(version_ok=False, why="looks right to me"))
    assert not FIT.usable(_verdict(time_ok=False, why="looks right to me"))
    assert not FIT.usable(_verdict(exact_thing=False, why="the exact moment, perfect"))
    assert not FIT.usable(_verdict(contradicts=True))
    assert not FIT.usable(_verdict(time_ok="yes"))                 # a string is not a boolean
    assert not FIT.usable(_verdict(fits_line=False, live_action=True))   # the model's own "would a careful viewer accept it"
    assert not FIT.usable(_verdict(fits_line=None))
    assert FIT.usable(_verdict(grade=2, why="the right thing, not the brief's moment"))   # the grade ranks; it never vetoes a fitting picture
    assert not FIT.usable(None)


def test_the_two_times_are_kept_with_the_verdict():
    v = FIT._clean(_verdict(story_time="1993 storyboard reel, before the rewrite", picture_time="1993 Black Friday reel"))
    assert v["story_time"].startswith("1993") and v["picture_time"] == "1993 Black Friday reel"
    assert "story_time" not in FIT._clean(_verdict())


def test_the_prompt_states_one_time_rule_and_the_brief_as_wish_list():
    s = FIT.SYSTEM
    assert '"story_time"' in s and '"picture_time"' in s and "the same answer as time_ok" in s
    assert "not the test" in s and "for ranking only" in s


def test_the_cache_key_changes_when_the_words_change():
    assert FIT.key("/a.jpg", 2, "the fall was an accident") != FIT.key("/a.jpg", 2, "the fall was on purpose")
    assert FIT.key("/a.jpg", 2, "the  fall") == FIT.key("/a.jpg", 2, "the fall")


def test_a_candidate_is_judged_on_the_lines_that_name_its_subject_and_its_own(tmp_path):
    lines = FIT.lines_of(_sc())
    assert FIT.judged_lines(dict(subject="Toy Story", line=None), lines) == [0, 1]
    assert FIT.judged_lines(dict(subject="Woody", line=2), lines) == [2]


def _id_answer(content):
    n = sum(1 for b in content if b.get("type") == "text" and b["text"].startswith("\nTake "))
    return json.dumps(dict(takes=[dict(t=k, work="Toy Story", version="finished 1995 film", year=1995, depicts="d", live_action=False,
                                       speaking_on_camera=False, shows="s") for k in range(n)])), dict(usd=0.0)


def test_verdicts_batch_cache_and_ignore_answers_for_lines_not_asked(tmp_path):
    cands = [dict(path=_jpg(tmp_path, f"p{k}"), kind="photo", subject="Toy Story", line=None, meta=dict(title=f"t{k}")) for k in range(10)]
    calls, ids = [], []

    def ask(system, content):
        if system == FIT.IDENTITY_SYSTEM:
            ids.append(content)
            return _id_answer(content)
        calls.append(content)
        n = sum(1 for b in content if b.get("type") == "image")
        out = []
        for c in range(n):
            out.append(dict(c=c, line=0, **_verdict(version_ok=(c % 2 == 0))))
            out.append(dict(c=c, line=1, **_verdict()))
            out.append(dict(c=c, line=2, **_verdict()))          # line 2 was never asked for this subject: ignored
        return json.dumps(dict(verdicts=out)), dict(usd=0.01)
    cache = str(tmp_path / "fit.json")
    table, usd, n_calls, idn = FIT.verdicts(cands, _sc(), cache_path=cache, ask=ask, work=str(tmp_path / "w"))
    assert len(calls) == 2 and len(ids) == 2 and n_calls == 4 and abs(usd - 0.02) < 1e-9          # batches of eight
    assert len(idn) == 10 and all(v["work"] == "Toy Story" for v in idn.values())
    fits = FIT.fits(table)
    assert fits[cands[0]["path"]] == {0: [0, 1]} and fits[cands[1]["path"]] == {0: [1]}
    assert all(2 not in rows[0] for rows in table.values())
    assert "identity (settled once for every line)" in " ".join(b.get("text", "") for b in calls[0] if b.get("type") == "text")
    table2, usd2, _, _ = FIT.verdicts(cands, _sc(), cache_path=cache, ask=ask, work=str(tmp_path / "w"))
    assert len(calls) == 2 and len(ids) == 2 and usd2 == 0 and FIT.fits(table2) == fits             # cached, identities too
    sc = _sc()
    sc["segments"][1]["say"] = "In the finished film the fall was on purpose."
    FIT.verdicts(cands, sc, cache_path=cache, ask=ask, work=str(tmp_path / "w"))
    assert len(calls) == 4 and len(ids) == 2                                         # line 1's words changed: judged again, not identified


def _clip(tmp_path, name, seconds=2.0, cuts=()):
    import subprocess
    p = str(tmp_path / f"{name}.mp4")
    subprocess.run([FIT._ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=15:duration={seconds}", "-pix_fmt", "yuv420p", p],
                   check=True)
    return dict(path=p, kind="clip", subject="Toy Story", line=0, seconds=seconds, cuts=list(cuts), meta=dict(title=name))


def test_a_take_is_identified_once_and_each_take_judged_per_line(tmp_path):
    """A clip with two takes (early drawings, then an attraction figure) gets two identities, once each, and each line's
    usability is per take."""
    c = _clip(tmp_path, "two", seconds=2.0, cuts=[1.0])
    c["takes"] = FIT.takes_of(c)
    assert c["takes"] == [(0.0, 1.0), (1.0, 2.0)]
    id_calls, v_calls = [], []

    def ask(system, content):
        if system == FIT.IDENTITY_SYSTEM:
            id_calls.append(content)
            return json.dumps(dict(takes=[dict(t=0, work="Toy Story", version="1993 concept drawings", year=1993, depicts="drawings",
                                               live_action=False, speaking_on_camera=False, shows="drawings"),
                                          dict(t=1, work="Toy Story Midway Mania", version="theme-park figure", year=2008, depicts="figure",
                                               live_action=True, speaking_on_camera=False, shows="figure")])), dict(usd=0.0)
        v_calls.append(content)
        txt = " ".join(b.get("text", "") for b in content if b.get("type") == "text")
        out = []
        for k in range(2):
            figure = "theme-park" in txt.split(f"Candidate {k}:")[1].split("Candidate")[0]
            for ln in (0, 1):
                out.append(dict(c=k, line=ln, **_verdict(exact_thing=not figure)))
        return json.dumps(dict(verdicts=out)), dict(usd=0.0)
    table, _, _, ids = FIT.verdicts([c], _sc(), cache_path=str(tmp_path / "fit.json"), ask=ask, work=str(tmp_path / "w"))
    assert len(id_calls) == 1 and len(ids) == 2
    assert {v["work"] for v in ids.values()} == {"Toy Story", "Toy Story Midway Mania"}
    fits = FIT.fits(table)[c["path"]]
    assert fits == {0: [0, 1], 1: []}
    import reel as R
    sp = R.Supply({}, {0: [dict(c, motion=dict(p90=2.0, curve=[2.0] * 30))]}, fit=json.loads(json.dumps(FIT.fits(table))))
    assert sp.fits(c["path"], 0, 0) and not sp.fits(c["path"], 0, 1) and sp.fits(c["path"], 0)
    assert R.Takes.of(c) == [tuple(t) for t in c["takes"]]                       # the cut plays the very takes the verdicts judged


def test_the_audit_judges_each_shot_on_its_words_and_a_failing_shot_is_dropped_on_its_line(tmp_path):
    shots = [dict(key=f"a.mp4@{k}", path=_jpg(tmp_path, f"s{k}"), kind="photo", line=k % 3, f=[k * 10, k * 10 + 10], words=f"w{k}", meta={})
             for k in range(4)]
    seen = []

    def ask(system, content):
        txt = " ".join(b.get("text", "") for b in content if b.get("type") == "text")
        seen.append(txt)
        return json.dumps(dict(verdicts=[dict(c=c, line=10000 + c, **_verdict(version_ok=(c != 1))) for c in range(4)])), dict(usd=0.0)
    res, _ = FIT.audit(shots, _sc(), ask=ask, work=str(tmp_path / "w"))
    assert [r["fit"] for r in res] == [True, False, True, True]
    assert "words spoken exactly under this shot: \"w1\"" in seen[0]


def test_the_audit_loop_drops_failing_shots_by_line_and_builds_again(tmp_path, monkeypatch):
    import wf3 as W3
    calls = []

    def build(sc, pack, knobs):
        calls.append(list(knobs.get("drop") or []))
        bad = "x.jpg@1" not in (knobs.get("drop") or [])
        pics = [dict(kind="photo", path="/p/x.jpg" if bad else "/p/y.jpg", line=1, f=[0, 30], finish=None)]
        return dict(items=[]), dict(pictures=pics, measured=dict(repeat_share=0.0))

    def audit(shots, sc, **kw):
        return [dict(key=s["key"], line=s["line"], f=s["f"], path=s["path"], fit=not s["path"].endswith("x.jpg"), why="w") for s in shots], 0.0
    monkeypatch.setattr(W3.FIT, "audit", audit)
    monkeypatch.setattr(W3.F, "taste_check", lambda l1, beats: dict(ok=True, problems=[]))
    (tmp_path / "pack.json").write_text(json.dumps(dict(record=dict(spend=[]), pack={})))
    ctx = SimpleNamespace(supply=str(tmp_path), tmp=str(tmp_path), args={}, log=lambda m: None)
    words = dict(duration=2.0, words=[dict(w="a", start=0.0, end=0.5, seg=0), dict(w="b", start=0.6, end=1.0, seg=1)])
    pack = dict(words=words, fit={}, pictures={}, footage=[])
    sc = dict(segments=[dict(say="a"), dict(say="b")])
    l1, beats, taste = W3.stories_gates(ctx, sc, pack, build, {}, *build(sc, pack, {}), dict(ok=True, problems=[]))
    assert calls[-1] == ["x.jpg@1"] and beats["pictures"][0]["path"] == "/p/y.jpg"
    assert beats["measured"]["audit"]["rounds"][-1]["failed"] == 0 and taste["ok"]


def test_a_repeat_share_over_the_bar_breaks_the_plan(tmp_path, monkeypatch):
    import wf3 as W3

    def build(sc, pack, knobs):
        return dict(items=[]), dict(pictures=[], measured=dict(repeat_share=0.2))
    (tmp_path / "pack.json").write_text(json.dumps(dict(record=dict(spend=[]), pack={})))
    ctx = SimpleNamespace(supply=str(tmp_path), tmp=str(tmp_path), args={"audit": False}, log=lambda m: None)
    pack = dict(words=dict(duration=1.0, words=[dict(w="a", start=0.0, end=0.5, seg=0)]), fit={})
    _, _, taste = W3.stories_gates(ctx, dict(segments=[dict(say="a")]), pack, build, {}, *build(None, None, {}), dict(ok=True, problems=[]))
    assert not taste["ok"] and "repeat a take" in taste["problems"][-1]


# ----------------------------------------------------------------- round 2: the WF3 stages on stubs

def _ctx(tmp_path, **args):
    (tmp_path / "run").mkdir(exist_ok=True)
    (tmp_path / "sup").mkdir(exist_ok=True)
    return SimpleNamespace(supply=str(tmp_path / "sup"), tmp=str(tmp_path), run_dir=str(tmp_path / "run"), args=args, log=lambda m: None,
                           workflow="wf3", run_id="r1")


def _ann_sc():
    return dict(named=[dict(name="Toy Story", **{"is": "thing"}), dict(name="John Lasseter", **{"is": "person"})], facts=[],
                segments=[dict(say="Woody almost killed Toy Story.", beat="hook"), dict(say="John Lasseter directed it.", beat="low")])


def test_a_segment_without_a_brief_gets_one_or_uses_its_spoken_words(tmp_path, monkeypatch):
    import pytest
    import wf3 as W3
    ok = dict(ok=True, numbers_unbacked=[], facts_unverified=[], facts=[1])
    monkeypatch.setattr(W3.writer, "verify", lambda s: ok)
    calls = []

    def annotate(sc, idea, verify_fn=None, log=None, problems=None):
        calls.append(problems)
        out = json.loads(json.dumps(sc))
        out["segments"][0]["see"] = [dict(thing="Toy Story", as_of="1993", must_show="x", moment=None, search=None)]
        return out, dict(ok=False, problems=["segment 1: no valid see item (thing must be ...)"], verify=ok, device_share_words=0.0, spend=[])
    monkeypatch.setattr(W3.writer, "annotate", annotate)
    monkeypatch.setattr(W3.writer, "annotate_segment",
                        lambda sc, k, idea=None, ask=None: ([dict(thing="John Lasseter", as_of="1993", must_show="him", moment=None, search="John Lasseter 1993")], dict(usd=0.0)))
    sc2, rep = W3.annotate_script(_ctx(tmp_path), _ann_sc(), {}, [], reel=True)
    assert len(calls) == 2 and calls[1]                                    # the repair call carries the problems back
    assert all(s["see"] for s in sc2["segments"]) and sc2["segments"][1]["see"][0]["thing"] == "John Lasseter"
    monkeypatch.setattr(W3.writer, "annotate_segment", lambda sc, k, idea=None, ask=None: ([], dict(usd=0.0)))
    repaired, _ = W3.annotate_script(_ctx(tmp_path), _ann_sc(), {}, [], reel=True)
    seg = repaired["segments"][1]
    assert seg["see"][0]["must_show"] == seg["say"] and seg["see"][0]["word_i"] == 0


def test_fit_supply_researches_a_legacy_line_with_its_own_words(tmp_path,monkeypatch):
    import wf3 as W3
    captured=[]
    monkeypatch.setattr(W3,"TOPUP_CALLS",0)
    monkeypatch.setattr(W3,"measure_clips",lambda pack:None)
    def judge(ctx,sc,pack):
        captured.extend(b for seg in sc["segments"] for b in seg["see"])
        return 0,0
    monkeypatch.setattr(W3,"judge_supply",judge)
    pack=dict(footage=[],pictures={})
    W3.fit_supply(_ctx(tmp_path),dict(script=_ann_sc()),pack,[1.,1.],[],{}, {})
    assert [b["must_show"] for b in captured] == [seg["say"] for seg in _ann_sc()["segments"]]
    assert pack["fit_report"]["deficit"]==2


def _report_pack(n_photo=0, moving=True, shared=False):
    clips = []
    for k in range(4):
        clips.append(dict(path=f"/c{k}.mp4", seconds=1.6, cuts=[], take_group=f"/c{k}.mp4",
                          motion=dict(p90=2.0 if moving else 0.1, curve=[2.0 if moving else 0.1] * 24)))
    fit = {c["path"]: {"0": [0, 1, 2] if shared else [0]} for c in clips}
    pics = {"Toy Story": [dict(path=f"/p{k}.jpg") for k in range(n_photo)]}
    fit.update({r["path"]: {"0": [1]} for r in pics["Toy Story"]})
    return dict(footage=[dict(i=0, clips=clips)], pictures=pics, fit=fit)


def test_fit_report_counts_a_take_that_fits_three_lines_once():
    import wf3 as W3
    sc = dict(named=[], segments=[dict(say="a", see=[dict(thing="Toy Story")]) for _ in range(3)])
    rows = W3.fit_report(_report_pack(shared=True), sc, [1.0, 1.0, 1.0])
    assert sum(r["have"] for r in rows) == 4                               # four takes, each counted on one line only


def test_a_line_with_photographs_and_no_moving_clip_is_short():
    import wf3 as W3
    sc = dict(named=[], segments=[dict(say="a", see=[dict(thing="Toy Story")]), dict(say="b", see=[dict(thing="Toy Story")])])
    rows = W3.fit_report(_report_pack(n_photo=8), sc, [1.0, 1.0])
    assert rows[1]["have"] >= 1 and rows[1]["moving"] == 0 and rows[1]["short"]


def test_the_topup_script_names_each_short_lines_moments_and_photo_searches_come_from_the_brief():
    import wf3 as W3
    sc = dict(named=[dict(name="Toy Story", **{"is": "thing"}), dict(name="John Lasseter", **{"is": "person"})],
              segments=[dict(say="a", pictures=["Toy Story"], see=[dict(thing="Toy Story", as_of="1995 release", moment="Woody knocks Buzz out of the window in the 1995 film",
                                                                         must_show="x", search="Toy Story 1995")]),
                        dict(say="b", pictures=["John Lasseter"], see=[dict(thing="John Lasseter", as_of="1995", moment=None, must_show="him", search="John Lasseter 1995")])])
    rec = W3.topup_script(dict(script=sc, style=dict(id="reel")), [0])
    seg = rec["script"]["segments"][0]
    assert seg["pictures"][0] == "Woody knocks Buzz out of the window in the 1995 film"
    assert any(n["name"] == seg["pictures"][0] for n in rec["script"]["named"])
    assert rec["script"]["segments"][1]["pictures"] == ["John Lasseter"]          # a line that is not short keeps its own
    subj = W3.photo_searches(sc, sc["named"])
    assert [s["searches"] for s in subj] == [["Toy Story 1995"], ["John Lasseter 1995"]] and subj[0]["key"] == "Toy Story"


def test_a_shot_still_failing_after_the_audit_rounds_is_replaced_not_kept(tmp_path, monkeypatch):
    """Each round's rebuild places the line's next take; the four takes after the rounds all fail, the fifth passes. The
    shots judged unfit are dropped on their line, the line takes its next supply-passed take, and the line is never
    narrowed to only what passed the last round (20 Nov: no only_ok)."""
    import wf3 as W3
    seen_knobs = []

    def build(sc, pack, knobs):
        seen_knobs.append(knobs)
        k = sum(1 for d in knobs.get("drop") or [] if d.endswith("@1"))     # the cut takes the line's next take past the dropped
        path = f"/p/bad{k}.jpg" if k < 4 else "/p/good.jpg"
        pics = [dict(kind="photo", path=path, line=1, f=[0, 30], finish=None, tier="photo")]
        return dict(items=[]), dict(pictures=pics, measured=dict(repeat_share=0.0, static_shots=0), voice_f=[0, 30])

    def audit(shots, sc, **kw):
        return [dict(key=s["key"], line=s["line"], f=s["f"], path=s["path"], fit="bad" not in s["path"], why="w") for s in shots], 0.0
    monkeypatch.setattr(W3.FIT, "audit", audit)
    monkeypatch.setattr(W3.F, "taste_check", lambda l1, beats: dict(ok=True, problems=[]))
    (tmp_path / "pack.json").write_text(json.dumps(dict(record=dict(spend=[]), pack={})))
    ctx = SimpleNamespace(supply=str(tmp_path), tmp=str(tmp_path), args={}, log=lambda m: None)
    words = dict(duration=2.0, words=[dict(w="a", start=0.0, end=0.5, seg=0), dict(w="b", start=0.6, end=1.0, seg=1)])
    pack = dict(words=words, fit={}, pictures={}, footage=[])
    sc = dict(segments=[dict(say="a"), dict(say="b")])
    l1, beats, taste = W3.stories_gates(ctx, sc, pack, build, {}, *build(sc, pack, {}), dict(ok=True, problems=[]))
    assert beats["pictures"][0]["path"] == "/p/good.jpg"
    assert beats["measured"]["audit"]["rounds"][-1]["round"] == "fill" and beats["measured"]["audit"]["rounds"][-1]["failed"] == 0
    assert not any(k.get("only_ok") for k in seen_knobs)                 # never narrowed to what passed the last round


def test_static_shots_on_the_plan_break_it(tmp_path):
    import wf3 as W3

    def build(sc, pack, knobs):
        return dict(items=[]), dict(pictures=[], measured=dict(repeat_share=0.0, static_shots=2, static_s=1.1), voice_f=[0, 30])
    (tmp_path / "pack.json").write_text(json.dumps(dict(record=dict(spend=[]), pack={})))
    ctx = SimpleNamespace(supply=str(tmp_path), tmp=str(tmp_path), args={"audit": False}, log=lambda m: None)
    pack = dict(words=dict(duration=1.0, words=[dict(w="a", start=0.0, end=0.5, seg=0)]), fit={})
    _, _, taste = W3.stories_gates(ctx, dict(segments=[dict(say="a")]), pack, build, {}, *build(None, None, {}), dict(ok=True, problems=[]))
    assert not taste["ok"] and any("static" in p for p in taste["problems"])


def test_the_film_check_flags_a_still_shot(tmp_path):
    import subprocess
    import wf3 as W3
    p = str(tmp_path / "f.mp4")
    ff = FIT._ffmpeg()
    # 1 s of moving test pattern, then 1 s of one frozen frame
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=270x480:rate=30:duration=1", "-f", "lavfi", "-i",
                    "color=c=gray:size=270x480:rate=30:duration=1", "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]", "-map", "[v]",
                    "-pix_fmt", "yuv420p", p], check=True)
    m = W3.film_motion(p, [dict(kind="clip", f=[0, 30], mode="bleed"), dict(kind="photo", f=[30, 60], mode="bleed")], ff)
    assert m["static_shots"] == 1 and m["static_list"][0]["f"] == [30, 60]


def test_a_wf3_check_without_verdicts_never_passes(tmp_path, monkeypatch):
    import wf3 as W3
    ctx = _ctx(tmp_path)
    (tmp_path / "sup" / "pack.json").write_text(json.dumps(dict(record=dict(narration_s=1.0), pack=dict(words={}, ffmpeg="ffmpeg"))))
    monkeypatch.setattr(W3.chain, "finish_and_check", lambda ctx, rd, sc: dict(final="f.mp4", checks=dict(ok=True, mark_ok=True)))
    monkeypatch.setattr(W3, "mark_samples", lambda a, b: [])
    monkeypatch.setattr(W3, "caption_contrast", lambda *a: dict(ok=True))
    monkeypatch.setattr(W3, "film_checks", lambda *a, **k: dict(picture_fit=dict(fail_s=0), gates_ok=True, defects=[]))
    (tmp_path / "l1.json").write_text(json.dumps(dict(items=[], voices={})))
    sc = dict(taste=dict(ok=True), beats=dict(pictures=[], measured={}), l1=str(tmp_path / "l1.json"))
    r = W3.check(ctx, dict(film="f.mp4"), sc)
    assert r["checks"]["ok"] is False and any("verdicts" in d for d in r["checks"]["stories"]["defects"])


def test_a_remake_keeps_its_real_voices(tmp_path):
    import wf3 as W3
    src = tmp_path / "old"
    (src / "clips").mkdir(parents=True)
    (src / "voices").mkdir()
    (src / "clips" / "a.mp4").write_bytes(b"c")
    (src / "voices" / "v.wav").write_bytes(b"w")
    (src / "voices" / "v.mp4").write_bytes(b"p")
    (src / "footage.json").write_text(json.dumps(dict(lines=[dict(i=0, clips=[dict(file="clips/a.mp4", source=dict(url="u"), time_range=[0, 1])])],
                                                      voices=[dict(after=0, person="P", wav="voices/v.wav", file="voices/v.mp4", source=dict(url="u2"),
                                                                   time_range=[3, 5])])))
    ctx = _ctx(tmp_path, footage_from=[str(src)])
    out = tmp_path / "sup" / "footage"
    W3.given_footage(ctx, str(out))
    rec = json.loads((out / "footage.json").read_text())
    assert rec["voices"] and (out / "voices" / "v.wav").exists() and (out / "voices" / "v.mp4").exists()


def test_joints_open_their_hole_at_the_last_phrase_and_swell_into_type_pages():
    import wf3 as W3
    sc = dict(segments=[dict(say="one two three four five six", beat="setup", phrases=[[0, 3], [3, 6]]),
                        dict(say="seven eight nine", beat="turn", phrases=[[0, 3]]),
                        dict(say="ten eleven twelve", beat="close", phrases=[[0, 3]])])
    words, t = [], 0.2
    for i, s in enumerate(sc["segments"]):
        for w in s["say"].split():
            words.append(dict(w=w, start=round(t, 3), end=round(t + 0.25, 3), seg=i))
            t += 0.35
        t += 0.7                     # a narrator gap at each joint: a swell rises only in one
    pack = dict(words=dict(words=words, duration=t + 1))
    beats = dict(pictures=[dict(kind="designed", f=[20, 50], tier="designed")])
    js = W3.joints_of(sc, pack, beats)
    holes = [j for j in js if j["kind"] in ("turn", "close")]
    assert all(0.8 - 1e-6 <= j["next_first_word_t"] - j["last_word_t"] <= 2.3 + 1e-6 for j in holes)
    # the close keeps the bed at the references' depth (FLOW.json last_line step -0.49 dB): never a 20 dB dry close
    assert 0 < next(j for j in js if j["kind"] == "close")["bed_db"] <= 4
    # round 3: a swell rises only in a narrator gap, never into a hole's re-entry or the dry close
    assert all(not any(0 <= h["next_first_word_t"] - j["t"] < 1.0 for h in holes) for j in js if j["kind"] == "swell")


def test_sound_measures_read_drops_and_the_bed_from_two_mixes(tmp_path):
    import subprocess
    import numpy as np
    import wf3 as W3
    sr = 16000
    t = np.arange(int(sr * 12)) / sr
    voice = 0.3 * np.sin(2 * np.pi * 220 * t) * ((t % 2) < 1.4)
    bed = 0.05 * np.sin(2 * np.pi * 330 * t)
    bed[(t >= 5.45) & (t < 5.95)] *= 0.01                               # one drop of 0.5 s in a pause of the voice
    paths = []
    for name, sig in (("clean", voice), ("music", voice + bed)):
        raw = tmp_path / f"{name}.f32"
        sig.astype(np.float32).tofile(raw)
        out = str(tmp_path / f"{name}.wav")
        subprocess.run([FIT._ffmpeg(), "-v", "error", "-y", "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", str(raw), out], check=True)
        paths.append(out)
    m = W3.sound_measures(paths[1], paths[0], FIT._ffmpeg(), dict(sound=[]), [dict(next_first_word_t=5.95)])
    assert m["drops"]["count"] == 1 and abs(m["drops"]["list"][0]["t"] - 5.45) <= 0.15 and m["drops"]["reentry_on_word"], m
    assert 0.5 <= m["music_runtime_share"] <= 1.0


def test_sound_measures_do_not_count_voice_left_in_the_bed_or_the_end_card(monkeypatch):
    """C7 (19 Nov): under a low close the fixed 0.845 voice gain left the narrator's words in the 'bed', so each gap
    between words read as a drop, and the music-free end card pulled the music share under the bar. The bed is now
    the residual at each frame's least-squares gain, over the music's span."""
    import numpy as np
    import wf3 as W3
    sr = 16000
    t = np.arange(int(sr * 30)) / sr
    rng = np.random.default_rng(3)
    talk = (t < 24.0) & ((t % 0.5) < 0.32)                             # words of 0.32 s, gaps of 0.18 s, then 6 s of end card
    voice = 0.3 * np.sin(2 * np.pi * 180 * t) * talk
    bed = 0.03 * rng.standard_normal(len(t)) * (t < 25.0)              # the bed, level to the last word and its ring, none on the card
    bed[(t >= 12.0) & (t < 12.8)] *= 0.02                               # one designed drop at a joint
    mixed = 0.7 * voice + bed                                          # the mix's voice gain is not 0.845
    arrays = {"music": mixed.astype(np.float32), "clean": voice.astype(np.float32)}
    monkeypatch.setattr(W3, "_audio", lambda path, ffmpeg, sr=16000: arrays[path])
    m = W3.sound_measures("music", "clean", "ffmpeg", dict(sound=[]), [dict(next_first_word_t=12.8)])
    assert m["drops"]["count"] == 1 and abs(m["drops"]["list"][0]["t"] - 12.0) <= 0.15, m
    assert m["music_runtime_share"] >= 0.95 and m["music_span_s"][1] <= 25.5, m
    assert W3.MUSIC_SHARE[0] <= m["music_runtime_share"] <= 1.0


# ----------------------------------------------------------------- round 3

def test_a_clip_found_for_a_moment_is_judged_on_every_line_that_names_its_thing():
    lines = FIT.lines_of(_sc())
    assert FIT.judged_lines(dict(subject="Woody knocks Buzz out of the window", thing="Toy Story", line=1), lines) == [0, 1]
    assert FIT.judged_lines(dict(subject="John Lasseter interview", thing="John Lasseter", line=None), lines) == [2]


def _words_for(sc, wps=2.6):
    out, t = [], 0.0
    for i, s in enumerate(sc["segments"]):
        for w in s["say"].split():
            out.append(dict(w=w, start=round(t, 3), end=round(t + 0.25, 3), seg=i))
            t += 1 / wps
        t += 0.22
    return dict(words=out, duration=round(t, 2))


def test_demand_follows_the_cuts_shot_plan_and_moments_and_stills_shape_short():
    import math
    import film as F
    import reel as R
    import wf3 as W3
    sc = dict(named=[], segments=[dict(say=" ".join(f"w{i}{k}" for k in range(12)), beat="setup",
                                       see=[dict(thing="Toy Story", as_of="1995", moment="the window fall" if i == 1 else None,
                                                 still_only=(i == 2))]) for i in range(3)])
    words = _words_for(sc)
    clock = F.Clock(words, sc)
    plan = R.shot_plan(sc, clock, F.Clock.f(clock.dur) + 15, 1114)
    pack = dict(_report_pack(), words=words)
    want = {i: max(1, math.ceil(W3.DEMAND_K * sum(1 for x in plan[i] if x > W3.GLIMPSE_MAX_F))) for i in range(3)}
    assert W3.line_demand(pack, sc, [4.0] * 3) == want
    # line 1 names a moment: grade-3 takes only keep it short; line 2 is still by nature: no motion asked of it
    clips = [dict(path=f"/m{k}.mp4", seconds=1.6, cuts=[], take_group=f"/m{k}.mp4", motion=dict(p90=0.1, curve=[0.1] * 24)) for k in range(40)]
    fit = {c["path"]: {"0": [1, 2]} for c in clips}
    ver = {c["path"]: {"0": {"1": dict(grade=3), "2": dict(grade=3)}} for c in clips}
    pack = dict(footage=[dict(i=1, clips=clips)], pictures={}, fit=fit, fit_verdicts=ver, words=words)
    rows = W3.fit_report(pack, sc, [4.0] * 3)
    assert rows[1]["moment_ok"] is False and rows[1]["short"]
    assert rows[2]["still_only"] and rows[2]["moving"] == 0 and not rows[2]["short"] and rows[2]["have"] >= rows[2]["need"]
    ver[clips[0]["path"]]["0"]["1"]["grade"] = 5
    rows = W3.fit_report(pack, sc, [4.0] * 3)
    assert rows[1]["moment_ok"] is True and W3.moment_cover(rows) == 1.0


def test_failing_shots_get_the_top_up_before_the_fill(tmp_path, monkeypatch):
    import wf3 as W3
    state = dict(topped=False)
    calls = []

    def build(sc, pack, knobs):
        good = state["topped"]
        pics = [dict(kind="photo", path="/p/good.jpg" if good else "/p/bad.jpg", line=1, f=[0, 30], finish=None, tier="photo")]
        return dict(items=[]), dict(pictures=pics, measured=dict(repeat_share=0.0, static_shots=0), voice_f=[0, 30])

    def audit(shots, sc, **kw):
        return [dict(key=s["key"], line=s["line"], f=s["f"], path=s["path"], fit=not s["path"].endswith("bad.jpg"), why="w") for s in shots], 0.0

    def topup(ctx, script_rec, pack, lines):
        calls.append(("topup", lines))
        state["topped"] = True
        return dict(lines=lines, clips=3)
    monkeypatch.setattr(W3.FIT, "audit", audit)
    monkeypatch.setattr(W3, "topup_from_score", topup)
    monkeypatch.setattr(W3.F, "taste_check", lambda l1, beats: dict(ok=True, problems=[]))
    (tmp_path / "pack.json").write_text(json.dumps(dict(record=dict(spend=[]), pack={})))
    ctx = SimpleNamespace(supply=str(tmp_path), tmp=str(tmp_path), args={}, log=lambda m: None)
    words = dict(duration=2.0, words=[dict(w="a", start=0.0, end=0.5, seg=0), dict(w="b", start=0.6, end=1.0, seg=1)])
    pack = dict(words=words, fit={}, pictures={}, footage=[])
    sc = dict(segments=[dict(say="a"), dict(say="b")])
    l1, beats, taste = W3.stories_gates(ctx, sc, pack, build, {}, *build(sc, pack, {}), dict(ok=True, problems=[]), script_rec=dict(script=sc))
    assert calls[0] == ("topup", [1])
    rounds = beats["measured"]["audit"]["rounds"]
    assert rounds[-1]["round"] == "topup" and rounds[-1]["failed"] == 0 and not any(r["round"] == "fill" for r in rounds)
    assert beats["pictures"][0]["path"] == "/p/good.jpg"


def _fc(fail_s, defects=()):
    return dict(picture_fit=dict(fail_s=fail_s, shots=[dict(key="s1", drop="bad.jpg@1", line=1, fit=fail_s == 0, why="w")]),
                defects=list(defects), gates_ok=not defects)


def test_the_picture_gate_draws_again_while_it_improves_then_delivers_with_the_record(tmp_path, monkeypatch):
    import pytest
    import wf3 as W3
    ctx = _ctx(tmp_path)
    (tmp_path / "run" / "script.json").write_text(json.dumps(dict(script=dict(segments=[]), style=dict(id="reel"))))
    seen = []
    monkeypatch.setattr(W3, "check_once", lambda ctx, rd, sc: (dict(checks=dict(ok=False)), _fc(1.2)))
    monkeypatch.setattr(W3, "score", lambda ctx, s: seen.append(("score", W3.render_drop(ctx))) or {})
    film = tmp_path / "repaired.mp4"
    def render(ctx, s, sc):
        seen.append("render")
        film.write_bytes(b"rendered")
        return dict(film=str(film))
    monkeypatch.setattr(W3, "render", render)
    r = W3.check(ctx, {}, {})                                              # owner 16 Nov: repaired, recorded, delivered
    assert seen == [("score", ["bad.jpg@1"]), "render", ("score", ["bad.jpg@1"]), "render"]   # a second round; no third
    assert any("picture gate" in d for d in r["checks"]["stories"]["defects"])   # nothing gained: stop repairing, record
    assert len(r["checks"]["stories"]["picture_gate"]["rounds"]) == 2
    W3.check(ctx, {}, {})                                                  # a resumed check does not start the rounds again
    assert len(seen) == 4
    evidence = json.loads((tmp_path / "run" / "check_attempt.json").read_text())
    assert evidence["checks"]["stories"]["picture_gate"]["after"] == 1.2


def test_picture_repair_resumes_after_score_refusal_without_checking_deleted_film(tmp_path, monkeypatch):
    import pytest
    import wf3 as W3
    ctx = _ctx(tmp_path)
    (tmp_path / "run" / "script.json").write_text(json.dumps(dict(script={})))
    original = tmp_path / "film_engine.mp4"
    original.write_bytes(b"old")
    calls = []
    def check_once(ctx, rd, sc):
        assert os.path.exists(rd["film"])
        calls.append(("check", sc["version"]))
        return dict(checks=dict(ok=sc["version"] == "repair")), _fc(0 if sc["version"] == "repair" else 1.2)
    def score(ctx, script):
        calls.append("score")
        if calls.count("score") == 1:
            raise RuntimeError("the plan breaks a taste rule")
        return dict(version="repair")
    def render(ctx, script, sc):
        calls.append("render")
        original.write_bytes(b"repaired")
        return dict(film=str(original))
    monkeypatch.setattr(W3, "clear_render", lambda ctx: original.unlink(missing_ok=True))
    monkeypatch.setattr(W3, "check_once", check_once)
    monkeypatch.setattr(W3, "score", score)
    monkeypatch.setattr(W3, "render", render)
    stale_rd, stale_sc = dict(film=str(original)), dict(version="original")
    with pytest.raises(RuntimeError, match="taste rule"):
        W3.check(ctx, stale_rd, stale_sc)
    assert not original.exists()
    result = W3.check(ctx, stale_rd, stale_sc)
    assert result["checks"]["stories"]["picture_gate"]["after"] == 0
    assert calls == [("check", "original"), "score", "score", "render", ("check", "repair")]


def test_picture_repair_resumes_render_with_its_checkpointed_score(tmp_path, monkeypatch):
    import pytest
    import wf3 as W3
    ctx = _ctx(tmp_path)
    (tmp_path / "run" / "script.json").write_text(json.dumps(dict(script={})))
    (tmp_path / "sup" / "render_repair.json").write_text(json.dumps(dict(fail_s=1.2, dropped=[])))
    calls = []
    film = tmp_path / "repaired.mp4"
    monkeypatch.setattr(W3, "score", lambda *a: calls.append("score") or dict(version="repair"))
    def render(ctx, script, sc):
        assert sc == dict(version="repair")
        calls.append("render")
        if calls.count("render") == 1:
            raise RuntimeError("render interrupted")
        film.write_bytes(b"repaired")
        return dict(film=str(film))
    def check_once(ctx, rd, sc):
        assert rd["film"] == str(film) and sc == dict(version="repair")
        return dict(checks=dict(ok=False)), _fc(0.4)
    monkeypatch.setattr(W3, "render", render)
    monkeypatch.setattr(W3, "check_once", check_once)
    with pytest.raises(RuntimeError, match="render interrupted"):
        W3.check(ctx, {}, {"version": "stale"})
    r = W3.check(ctx, {}, {"version": "stale"})                            # resumed: two more rounds, then recorded
    assert r["checks"]["stories"]["picture_gate"]["after"] == 0.4
    assert any("0.4 s of pictures still fail" in d for d in r["checks"]["stories"]["defects"])
    W3.check(ctx, {}, {"version": "stale"})
    assert calls == ["score", "render", "render", "score", "render", "score", "render"]


def test_a_film_whose_only_defect_is_a_fill_overflow_is_delivered(tmp_path, monkeypatch):
    import wf3 as W3
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(W3, "check_once", lambda ctx, rd, sc: (dict(checks=dict(ok=False, stories=dict(defects=["fill 0.12"]))), _fc(0.0, ["fill 0.12"])))
    monkeypatch.setattr(W3, "score", lambda *a: (_ for _ in ()).throw(AssertionError("no repair")))
    r = W3.check(ctx, {}, {})
    assert r["checks"]["stories"]["defects"] == ["fill 0.12"]


def test_a_repeated_take_group_is_denied_on_its_later_line_and_the_plan_built_once_more(tmp_path, monkeypatch):
    import wf3 as W3
    seen = []
    monkeypatch.setattr(W3.F, "taste_check", lambda l1, beats: dict(ok=True, problems=[]))

    def build(sc, pack, knobs):
        seen.append(list(knobs.get("drop") or []))
        rep = 0.0 if knobs.get("drop") else 0.2
        pics = [dict(kind="clip", path="/c/a.mp4", line=0, f=[0, 10], take=0), dict(kind="clip", path="/c/b.mp4", line=2, f=[10, 20], take=0),
                dict(kind="clip", path="/c/a.mp4", line=2, f=[20, 22], take=0, cont=True)]
        return dict(items=[]), dict(pictures=pics, measured=dict(repeat_share=rep, static_shots=0), voice_f=[0, 30])
    (tmp_path / "pack.json").write_text(json.dumps(dict(record=dict(spend=[]), pack={})))
    ctx = SimpleNamespace(supply=str(tmp_path), tmp=str(tmp_path), args={"audit": False, "topup": False}, log=lambda m: None)
    pack = dict(words=dict(duration=1.0, words=[dict(w="a", start=0.0, end=0.5, seg=0)]), fit={},
                footage=[dict(i=0, clips=[dict(path="/c/a.mp4", take_group="G"), dict(path="/c/b.mp4", take_group="G")])])
    _, beats, taste = W3.stories_gates(ctx, dict(segments=[dict(say="a")]), pack, build, {}, *build(None, None, {}), dict(ok=True, problems=[]))
    assert seen[-1] == ["b.mp4@2"] and beats["measured"]["repeat_rebuild"]["after"] == 0.0
    assert not any("repeat a take" in p for p in taste["problems"])


def test_render_measures_read_flips_whites_hash_repeats_and_layout():
    import numpy as np
    import wf3 as W3
    rng = np.random.default_rng(3)
    a = (rng.random((160, 90)) * 200).astype(np.uint8)
    frames = [a, a, 255 - a, a, np.full((160, 90), 240, np.uint8), a]
    flips, whites = W3.flips_and_whites(frames)
    assert flips >= 1 and whites == 1                       # the negated frame flips, the white frame is white
    b = (rng.random((160, 90)) * 200).astype(np.uint8)
    seq = [a] * 10 + [b] * 10 + [a] * 10
    pics = [dict(kind="clip", f=[0, 10], path="x"), dict(kind="clip", f=[10, 20], path="y"), dict(kind="clip", f=[20, 30], path="x")]
    assert W3.hash_repeats(seq, pics) == round(1 / 3, 3)
    pics[2]["cont"] = True                                   # a time-continuous cut-in is not a repeat
    assert W3.hash_repeats(seq, pics) == 0.0
    beats = dict(pictures=[dict(kind="clip", mode="bleed"), dict(kind="clip", mode="window"), dict(kind="photo", mode="print"),
                           dict(kind="clip", mode="pushed")])
    assert W3.bleed_share(beats) == (0.5, 0.5)


def test_joints_keep_three_holes_swell_only_in_narrator_gaps_and_long_voices_keep_the_bed():
    import wf3 as W3
    beats_ = ["setup", "low", "turn", "low", "payoff", "close"]
    sc = dict(segments=[dict(say="a b c d", beat=b, phrases=[[0, 4]]) for b in beats_])
    words, t = [], 0.2
    for i, s in enumerate(sc["segments"]):
        for w in s["say"].split():
            words.append(dict(w=w, start=round(t, 3), end=round(t + 0.25, 3), seg=i))
            t += 0.3
        t += 0.9                                                               # a 0.95 s gap at every joint
    pack = dict(words=dict(words=words, duration=t + 1),
                voices=[dict(start=2.0, end=3.5), dict(start=8.0, end=12.0)])
    first = {}
    for w in words:
        first.setdefault(w["seg"], w["start"])
    beats = dict(pictures=[dict(kind="designed", f=[int((first[2] + 0.2 + 1.0 / 3) * 30), int((first[2] + 1.5) * 30)], tier="designed")])
    js = W3.joints_of(sc, pack, beats)
    holes = [j for j in js if j["kind"] in W3.HOLE_RANK]
    assert len(holes) == 3 and {j["kind"] for j in holes} == {"turn", "close", "payoff"}       # the lows go first
    v = [j for j in js if j["kind"] == "voice"]
    assert v[0]["db"] == W3.JOINT_HOLE_DB and v[1]["db"] == W3.VOICE_LONG_DB
    sw = [j for j in js if j["kind"] == "swell"]
    gaps = W3.narrator_gaps(W3.F.Clock(pack["words"], sc).words)
    assert 1 <= len(sw) <= W3.SWELLS_MAX
    for j in sw:
        g = next(g for g in gaps if abs(g[1] - j["t"]) < 1e-3)                # each at a gap's end, rising inside it
        assert g[1] - g[0] >= 0.6 and j["rise_s"] <= g[1] - g[0] + 1e-6


def test_a_thin_display_density_asks_the_writer_once_and_builds_again(tmp_path, monkeypatch):
    import wf3 as W3
    calls = []
    orig = W3.density_repair

    def build(sc, pack, knobs):
        n = sum(len(s_.get("show") or []) for s_ in sc["segments"])
        pics = [dict(kind="clip", path="/c/a.mp4", line=0, spoken_line=0, f=[0, 10], take=0, mode="bleed")]
        return dict(items=[], n=n), dict(pictures=pics, measured=dict(repeat_share=0.0, static_shots=0), voice_f=[0, 30], display=[])
    monkeypatch.setattr(W3.F, "taste_check", lambda l1, beats: dict(ok=True, problems=[]))
    monkeypatch.setattr(W3, "type_measures", lambda l1, beats, sc: dict(display_shot_share=0.05 if not l1.get("n") else 0.18))

    def repair(sc, lines, ask=None, verify_fn=None):
        calls.append(lines)
        out = json.loads(json.dumps(sc))
        out["segments"][0]["show"] = [dict(text="Woody", role="name", word_i=0)]
        return out, [dict(i=0, text="Woody", role="name")], dict(usd=0.001)
    monkeypatch.setattr(W3, "density_repair", repair)
    (tmp_path / "pack.json").write_text(json.dumps(dict(record=dict(spend=[]), pack={})))
    ctx = SimpleNamespace(supply=str(tmp_path), tmp=str(tmp_path), args={"audit": False, "topup": False}, log=lambda m: None)
    pack = dict(words=dict(duration=1.0, words=[dict(w="a", start=0.0, end=0.5, seg=0)]), fit={}, footage=[])
    sc = dict(segments=[dict(say="Woody fell")])
    _, beats, _ = W3.stories_gates(ctx, sc, pack, build, {}, *build(sc, pack, {}), dict(ok=True, problems=[]))
    assert calls == [[0]] and beats["measured"]["display_density"]["after"] == 0.18
    out, added, _ = orig(dict(segments=[dict(say="the box office", show=[])]), [0],
                                      ask=lambda system, content: (json.dumps(dict(items=[dict(i=0, text="BOX OFFICE", role="key", word_i=1),
                                                                                            dict(i=0, text="$362M", role="key", word_i=2)])), {}))
    assert [a["text"] for a in added] == ["BOX OFFICE"]                       # a number is never added here


# ----------------------------------------------------------------- 20 Nov: an unanswered call is an outage, never a verdict

def _cap(*a, **k):
    raise RuntimeError('openai 503: {"error": {"message": "Claude backend daily call cap reached (1500/1500)"}}')


def test_an_unanswered_audit_keeps_the_supply_verdict_and_is_never_a_failure(tmp_path):
    """19 Nov: a 90-second proxy cap answered every audit batch with 503 and each shot became fit=False 'not judged'."""
    shots = [dict(key=f"a{k}.jpg@{k % 3}", path=_jpg(tmp_path, f"a{k}"), kind="photo", line=k % 3, f=[k * 10, k * 10 + 10], words="w", meta={},
                  take_i=0) for k in range(3)]
    supply = {shots[0]["path"]: {0: [0]}, shots[1]["path"]: {"0": [0]}}          # shot 1 (line 1): the supply never passed it there
    sleeps = []
    res, _ = FIT.audit(shots, _sc(), ask=_cap, work=str(tmp_path / "w"), supply=supply, sleep=sleeps.append, log=lambda m: None)
    assert [r["judged"] for r in res] == [False, False, False] and all(r.get("unjudged") for r in res)
    assert [r["fit"] for r in res] == [True, False, True]                         # the supply's verdict; no row at all: kept as placed
    assert FIT.judged_bad(res) == []                                               # nothing to drop, nothing to count
    assert sleeps == list(FIT.RETRY_WAITS)                                         # asked again after a pause, twice
    assert not os.path.exists(str(tmp_path / "audit.json"))


def test_a_batch_unanswered_in_an_outage_is_asked_again_and_the_rest_wait_with_it(tmp_path):
    items = [dict(path=_jpg(tmp_path, f"b{k}"), kind="photo", lines=[0], meta={}) for k in range(3)]
    lines = FIT.lines_of(_sc())
    calls, sleeps = [], []

    def ask(system, content):
        calls.append(content)
        if len(calls) <= 2:                                                         # the provider is out for the first two calls
            raise RuntimeError("openai 503: cap")
        return json.dumps(dict(verdicts=[dict(c=0, line=0, **_verdict())])), dict(usd=0.0)
    left = []
    got, _ = FIT.judge(items, lines, ask=ask, work=str(tmp_path / "w"), batch=1, sleep=sleeps.append, log=lambda m: None, unanswered=left)
    assert sorted(k for k, _, _ in got) == [0, 1, 2] and left == []
    assert len(calls) == 5 and sleeps == list(FIT.RETRY_WAITS)                    # one failure a pass; the rest waited with it


def test_one_unreadable_answer_never_holds_the_other_batches_back(tmp_path):
    items = [dict(path=_jpg(tmp_path, f"u{k}"), kind="photo", lines=[0], meta={"title": f"u{k}"}) for k in range(3)]

    def ask(system, content):
        txt = " ".join(b.get("text", "") for b in content if b.get("type") == "text")
        if '"u1"' in txt:
            return "no json here", dict(usd=0.0)
        return json.dumps(dict(verdicts=[dict(c=0, line=0, **_verdict())])), dict(usd=0.0)
    left = []
    got, _ = FIT.judge(items, FIT.lines_of(_sc()), ask=ask, work=str(tmp_path / "w"), batch=1, sleep=lambda s: None, log=lambda m: None,
                       unanswered=left)
    assert sorted(k for k, _, _ in got) == [0, 2] and left == [1]


def test_a_supply_take_no_model_answered_for_stays_on_the_line_it_was_found_for(tmp_path):
    c = _clip(tmp_path, "found", seconds=1.0)
    c["takes"] = FIT.takes_of(c)
    ph = dict(path=_jpg(tmp_path, "ph"), kind="photo", subject="Toy Story", line=None, takes=[(0.0, 0.0)], meta=dict(title="ph"))

    def ask(system, content):
        if system == FIT.IDENTITY_SYSTEM:
            return json.dumps(dict(takes=[dict(t=k, work="Toy Story", version=None, year=1995, depicts="x", live_action=True,
                                               speaking_on_camera=True, shows="x") for k in range(2)])), dict(usd=0.0)
        return _cap()
    cache = str(tmp_path / "fit.json")
    table, _, _, _ = FIT.verdicts([c, ph], _sc(), cache_path=cache, ask=ask, work=str(tmp_path / "w"), sleep=lambda s: None, log=lambda m: None)
    fits = FIT.fits(table)
    assert fits[c["path"]] == {0: [0]}                                              # its own line only, never line 1
    assert table[c["path"]][0][0]["unjudged"] is True and table[c["path"]][0][0]["speaking_on_camera"] is True
    assert fits[ph["path"]] == {0: []}                                             # a photograph has no found line: not placed unjudged
    assert json.load(open(cache)) == {}                                             # never cached: the next call asks again
    import reel as R
    assert R.fit_grades(table)[c["path"]][0][0] == 0                               # ranked after every judged take


def test_an_audit_outage_never_empties_the_plan(tmp_path, monkeypatch):
    import wf3 as W3
    builds = []

    def build(sc, pack, knobs):
        builds.append(knobs)
        pics = [dict(kind="photo", path="/p/a.jpg", line=1, f=[0, 30], finish=None, tier="photo")]
        return dict(items=[]), dict(pictures=pics, measured=dict(repeat_share=0.0, static_shots=0), voice_f=[0, 30])

    def audit(shots, sc, **kw):
        assert kw.get("supply") == {"/p/a.jpg": {0: [1]}}
        return [dict(key=s["key"], line=s["line"], f=s["f"], path=s["path"], judged=False, unjudged=True, fit=True, why="not judged")
                for s in shots], 0.0
    monkeypatch.setattr(W3.FIT, "audit", audit)
    monkeypatch.setattr(W3.F, "taste_check", lambda l1, beats: dict(ok=True, problems=[]))
    (tmp_path / "pack.json").write_text(json.dumps(dict(record=dict(spend=[]), pack={})))
    ctx = SimpleNamespace(supply=str(tmp_path), tmp=str(tmp_path), args={}, log=lambda m: None)
    words = dict(duration=2.0, words=[dict(w="a", start=0.0, end=0.5, seg=0), dict(w="b", start=0.6, end=1.0, seg=1)])
    pack = dict(words=words, fit={"/p/a.jpg": {0: [1]}}, pictures={}, footage=[])
    sc = dict(segments=[dict(say="a"), dict(say="b")])
    l1, beats, taste = W3.stories_gates(ctx, sc, pack, build, {}, *build(sc, pack, {}), dict(ok=True, problems=[]))
    assert not any(k.get("drop") or k.get("only_ok") for k in builds) and beats["pictures"][0]["path"] == "/p/a.jpg"   # no drop, no narrowing
    rounds = beats["measured"]["audit"]["rounds"]
    assert len(rounds) == 1 and rounds[0]["failed"] == 0 and rounds[0]["unjudged"] == 1 and beats["measured"]["audit"]["failed"] == []


def test_the_plan_pace_reads_a_held_card_as_one_shot():
    import wf3 as W3
    pics = [dict(kind="designed", shows="Apple", f=[k * 10, k * 10 + 10]) for k in range(6)] + [dict(kind="clip", f=[60, 70])]
    pc = W3.pace_of(dict(pictures=pics, voice_f=[0, 70]))
    assert pc["cuts_10s"] == round(2 / (70 / W3.F.FPS) * 10, 2) and pc["median_s"] == round(60 / W3.F.FPS, 3)


def test_the_credits_are_what_the_film_shows_and_says():
    """19 Nov: SOURCES.md credited 76 clips and 8 photographs the film never showed."""
    import wf3 as W3
    url = "https://www.youtube.com/watch?v=WxOp5mBY9IY"
    pack = dict(credits=[dict(name="P", title="Photo P", licence="CC BY-SA 3.0", credit="M. Y.", page="https://commons/P"),
                         dict(name="Q", title="Photo Q", licence="Public domain", credit="B. G.", page="https://commons/Q"),
                         dict(name="Deal", title="Deal", licence=W3.FOOTAGE_LICENCE, credit="JoshuaG", page=url, time_range=[185.79, 186.79], line=5),
                         dict(name="Deal", title="Deal", licence=W3.FOOTAGE_LICENCE, credit="JoshuaG", page=url, time_range=[234.27, 235.27], line=5)],
                voices=[dict(person="Steve Jobs", after=5, source=dict(url="https://cnbc/x", title="CNBC 1997", channel="CNBC"), time_range=[441.2, 449.5])])
    pictures = [dict(kind="designed", path="designed:0:0:$150 MILLION", f=[0, 30]),
                dict(kind="photo", path="/s/p.jpg", source="https://commons/P", f=[30, 50]),
                dict(kind="clip", path="/s/l05_0.mp4", source=url, time_range=[185.79, 186.79], src=[0.2, 0.6], f=[50, 62]),
                dict(kind="clip", path="/s/l05_0.mp4", source=url, time_range=[185.79, 186.79], src=[0.6, 0.9], f=[62, 70], cont=True)]
    cr = W3.screen_credits(pack, pictures)
    assert [c["page"] for c in cr] == ["https://commons/P", url, "https://cnbc/x"]   # Q and the second clip never appear
    assert cr[0]["credit"] == "M. Y." and "time_range" not in cr[0]
    assert cr[1]["time_range"] == [185.99, 186.69] and cr[1]["credit"] == "JoshuaG" and "line" not in cr[1]
    assert cr[2]["voice"] == "Steve Jobs"


class _Stream:
    def __init__(self, status, headers=None, body=b"", lines=()):
        self.status_code, self.headers, self._body, self._lines = status, headers or {}, body, list(lines)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body

    def iter_lines(self):
        return iter(self._lines)


def _proxy(monkeypatch, answers):
    """writer.ask_openai against a stand-in local proxy: each POST takes the next of `answers` (status, headers); a 200
    streams one completed response. Returns the recorded sleeps and the number of posts."""
    import httpx
    import writer
    done = json.dumps(dict(type="response.completed", response=dict(
        id="r1", status="completed", usage=dict(input_tokens=10, output_tokens=5),
        output=[dict(type="message", content=[dict(type="output_text", text='{"ok": true}')])])))
    posts, sleeps = [], []

    class Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def stream(self, method, url, headers=None, json=None):
            status, hdr = answers[len(posts)]
            posts.append(url)
            if isinstance(status, BaseException):
                raise status
            if status == 200:
                return _Stream(200, lines=["data: " + done, "data: [DONE]"])
            return _Stream(status, hdr, b'{"error": {"message": "Claude backend daily call cap reached"}}')
    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setattr(writer.time, "sleep", sleeps.append)
    monkeypatch.setattr(writer.openai_cache, "record", lambda *a, **k: None)
    monkeypatch.setenv("CODEX_PROXY_LOCAL", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:8787/v1")
    return writer, sleeps, posts


def test_the_local_proxy_refusing_with_503_is_waited_out_not_asked_once(monkeypatch):
    """19 Nov: under CODEX_PROXY_LOCAL ask_openai made one attempt, so a 90-second cap answered every audit call."""
    writer, sleeps, posts = _proxy(monkeypatch, [(503, {}), (503, {"retry-after": "2"}), (429, {}), (200, {})])
    text, use = writer.ask_openai("s", "c", what="picture check", model="gpt-6.1-sol")
    assert text == '{"ok": true}' and len(posts) == 4 and posts[0].startswith("http://127.0.0.1:8787/")
    assert sleeps == [writer.PROXY_WAITS[0], 2.0, writer.PROXY_WAITS[2]]          # its own Retry-After honoured
    assert use["usd"] == 0.0


def test_a_request_the_proxy_refuses_for_itself_is_not_retried(monkeypatch):
    import pytest
    writer, sleeps, posts = _proxy(monkeypatch, [(400, {}), (200, {})])
    with pytest.raises(RuntimeError, match="openai 400"):
        writer.ask_openai("s", "c", what="picture check", model="gpt-6.1-sol")
    assert len(posts) == 1 and sleeps == []


def test_a_proxy_outage_longer_than_the_waits_raises_to_the_judge(monkeypatch):
    import pytest
    writer, sleeps, posts = _proxy(monkeypatch, [(503, {})] * (len(__import__("writer").PROXY_WAITS) + 1))
    with pytest.raises(RuntimeError, match="openai 503"):
        writer.ask_openai("s", "c", what="picture check", model="gpt-6.1-sol")
    assert len(posts) == len(writer.PROXY_WAITS) + 1 and sleeps == list(writer.PROXY_WAITS)


def test_a_read_timeout_on_the_proxy_is_asked_again_at_most_once(monkeypatch):
    """The proxy may still be running a request that timed out: each further attempt could start another backend call on
    the shared daily cap and block for another full timeout. It is asked once more (the proxy's cache answers a completed
    duplicate); a connection that never opened ran nothing and is waited out like a refusal."""
    import httpx
    import pytest
    rt = lambda: httpx.ReadTimeout("read timed out")
    writer, sleeps, posts = _proxy(monkeypatch, [(rt(), {}), (rt(), {}), (200, {}), (200, {})])
    with pytest.raises(httpx.ReadTimeout):
        writer.ask_openai("s", "c", what="picture check", model="gpt-6.1-sol")
    assert len(posts) == 2 and sleeps == [writer.PROXY_WAITS[0]]
    writer, sleeps, posts = _proxy(monkeypatch, [(rt(), {}), (200, {})])
    assert writer.ask_openai("s", "c", what="picture check", model="gpt-6.1-sol")[0] == '{"ok": true}' and len(posts) == 2
    ce = lambda: httpx.ConnectError("connection refused")
    writer, sleeps, posts = _proxy(monkeypatch, [(ce(), {}), (httpx.ConnectTimeout("connect"), {}), (rt(), {}), (ce(), {}), (200, {})])
    assert writer.ask_openai("s", "c", what="picture check", model="gpt-6.1-sol")[0] == '{"ok": true}'
    assert len(posts) == 5 and sleeps == list(writer.PROXY_WAITS)


def test_the_scene_worker_is_the_worker_that_laid_the_devices(monkeypatch):
    """21 Nov: devices laid by VALMERA_WORKER's palette were drawn by skill-main's older engine ('unknown flash mode
    paper'); the render follows VALMERA_WORKER unless VALMERA_SCENE_WORKER names another."""
    import film
    monkeypatch.delenv("VALMERA_SCENE_WORKER", raising=False)
    monkeypatch.setenv("VALMERA_WORKER", "/tmp/w-palette")
    assert film.scene_worker() == "/tmp/w-palette"
    monkeypatch.setenv("VALMERA_SCENE_WORKER", "/tmp/w-scene")
    assert film.scene_worker() == "/tmp/w-scene"


def test_interrupted_new_round_does_not_check_the_previous_picture(tmp_path, monkeypatch):
    import wf3 as W3
    ctx = _ctx(tmp_path)
    run, supply = tmp_path / "run", tmp_path / "sup"
    (run / "script.json").write_text("{}")
    film = supply / "film_engine.mp4"
    film.write_bytes(b"old master")
    (run / "repair_score.json").write_text(json.dumps(dict(version="first", _picture_revision=1)))
    (run / "repair_render.json").write_text(json.dumps(dict(film=str(film), _picture_revision=1)))
    (supply / "render_repair.json").write_text(json.dumps(dict(revision=2, rounds=2)))
    monkeypatch.setattr(W3, "score", lambda *args: dict(version="second"))
    def draw(ctx, script, score):
        film.write_bytes(score["version"].encode())
        return dict(film=str(film))
    monkeypatch.setattr(W3, "render", draw)
    monkeypatch.setattr(W3, "check_once", lambda ctx, rd, sc: (film.read_bytes(), sc["version"]))
    assert W3._check_repair(ctx) == (b"second", "second")
