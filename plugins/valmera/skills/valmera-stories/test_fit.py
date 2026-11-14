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


def test_the_gate_reads_only_the_booleans_and_the_grade():
    assert FIT.usable(_verdict())
    assert not FIT.usable(_verdict(version_ok=False, why="looks right to me"))
    assert not FIT.usable(_verdict(contradicts=True))
    assert not FIT.usable(_verdict(grade=2, why="the exact moment, perfect"))
    assert not FIT.usable(_verdict(time_ok="yes"))                 # a string is not a boolean
    assert FIT.usable(_verdict(fits_line=False, live_action=True))      # the gate is the five fields the plan names, nothing else
    assert not FIT.usable(None)


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


def test_a_segment_without_a_brief_gets_one_or_the_script_stage_stops(tmp_path, monkeypatch):
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
    with pytest.raises(RuntimeError):
        W3.annotate_script(_ctx(tmp_path), _ann_sc(), {}, [], reel=True)


def test_fit_supply_never_cuts_a_reel_without_a_brief(tmp_path):
    import pytest
    import wf3 as W3
    with pytest.raises(RuntimeError):
        W3.fit_supply(_ctx(tmp_path), dict(script=_ann_sc()), {}, [1.0, 1.0], [], {}, {})


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
    import wf3 as W3

    def build(sc, pack, knobs):
        only = knobs.get("only_ok") or {}
        bad = 1 not in only                                                  # the cut keeps the bad take until only_ok says otherwise
        pics = [dict(kind="photo", path="/p/bad.jpg" if bad else "/p/good.jpg", line=1, f=[0, 30], finish=None, tier="photo")]
        return dict(items=[]), dict(pictures=pics, measured=dict(repeat_share=0.0, static_shots=0), voice_f=[0, 30])

    def audit(shots, sc, **kw):
        return [dict(key=s["key"], line=s["line"], f=s["f"], path=s["path"], fit=not s["path"].endswith("bad.jpg"), why="w") for s in shots], 0.0
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
    assert next(j for j in js if j["kind"] == "close")["bed_db"] >= 20
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


def test_the_picture_gate_draws_again_once_then_stops_before_delivery(tmp_path, monkeypatch):
    import pytest
    import wf3 as W3
    ctx = _ctx(tmp_path)
    (tmp_path / "run" / "script.json").write_text(json.dumps(dict(script=dict(segments=[]), style=dict(id="reel"))))
    seen = []
    monkeypatch.setattr(W3, "check_once", lambda ctx, rd, sc: (dict(checks=dict(ok=False)), _fc(1.2)))
    monkeypatch.setattr(W3, "score", lambda ctx, s: seen.append(("score", W3.render_drop(ctx))) or {})
    monkeypatch.setattr(W3, "render", lambda ctx, s, sc: seen.append("render") or {})
    with pytest.raises(RuntimeError, match="picture gate"):
        W3.check(ctx, {}, {})
    assert seen == [("score", ["bad.jpg@1"]), "render"]                    # dropped, scored and drawn again once
    with pytest.raises(RuntimeError):                                      # a resumed check never loops: it stops again
        W3.check(ctx, {}, {})
    assert len(seen) == 2


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
