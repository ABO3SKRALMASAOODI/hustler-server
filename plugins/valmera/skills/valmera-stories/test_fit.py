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


def test_verdicts_batch_cache_and_ignore_answers_for_lines_not_asked(tmp_path):
    cands = [dict(path=_jpg(tmp_path, f"p{k}"), kind="photo", subject="Toy Story", line=None, meta=dict(title=f"t{k}")) for k in range(10)]
    calls = []

    def ask(system, content):
        calls.append(content)
        n = sum(1 for b in content if b.get("type") == "image")
        out = []
        for c in range(n):
            out.append(dict(c=c, line=0, **_verdict(version_ok=(c % 2 == 0))))
            out.append(dict(c=c, line=1, **_verdict()))
            out.append(dict(c=c, line=2, **_verdict()))          # line 2 was never asked for this subject: ignored
        return json.dumps(dict(verdicts=out)), dict(usd=0.01)
    cache = str(tmp_path / "fit.json")
    table, usd, n_calls = FIT.verdicts(cands, _sc(), cache_path=cache, ask=ask, work=str(tmp_path / "w"))
    assert len(calls) == 2 and n_calls == 2 and abs(usd - 0.02) < 1e-9          # batches of eight
    fits = FIT.fits(table)
    assert fits[cands[0]["path"]] == [0, 1] and fits[cands[1]["path"]] == [1]
    assert all(2 not in v for v in table.values())
    table2, usd2, _ = FIT.verdicts(cands, _sc(), cache_path=cache, ask=ask, work=str(tmp_path / "w"))
    assert len(calls) == 2 and usd2 == 0 and FIT.fits(table2) == fits             # cached
    sc = _sc()
    sc["segments"][1]["say"] = "In the finished film the fall was on purpose."
    FIT.verdicts(cands, sc, cache_path=cache, ask=ask, work=str(tmp_path / "w"))
    assert len(calls) == 4                                                           # line 1's words changed: judged again


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
