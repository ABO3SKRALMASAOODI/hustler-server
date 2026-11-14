"""Offline tests of the one-command proof reel (proof.py): no model call, no stack, no render.

    $PY -m pytest -q test_proof.py
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def load():
    """proof.py on the stories' film.py; this folder's tests (test_film.py) import this folder's film.py, so the path and
    the module table are put back as they were once it is loaded."""
    path, mods = list(sys.path), dict(sys.modules)
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "_shell"))
    spec = importlib.util.spec_from_file_location("wf_proof_test", os.path.join(HERE, "proof.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    sys.path[:] = path
    for k in [k for k in sys.modules if k not in mods]:
        del sys.modules[k]
    return m


P = load()


def test_uses_the_stories_film_not_this_folders():
    assert os.path.dirname(P.F.__file__).endswith("valmera-stories")
    assert set(P.cards()) <= set(P.R.STYLES)


def test_boundaries_on_full_stops_and_on_pauses():
    w = [(0.0, "one"), (0.3, "two."), (0.6, "three"), (0.9, "four"), (2.4, "five")]
    assert P.boundaries(w) == {1, 3}


def test_snap_window_starts_and_ends_on_sentences():
    w = [(float(t), f"w{t}" + ("." if t % 8 == 7 else "")) for t in range(60)]
    a, b = P.snap_window(w, 9.2, 31.0)
    assert a == 7.75 and 30.0 <= b <= 41.0


def test_script_problems_catch_untrue_or_missing_parts():
    rc = {"typed": "Make it a vertical short with bold captions.", "first_result_s": 95}
    segs = [dict(say=f"line {k} words here", show=[]) for k in range(8)]
    segs[5]["say"] = "So I typed make it a vertical short with bold captions"
    segs[-1]["show"] = ["VALMERA.IO"]
    sc = dict(segments=segs, proof=dict(before_after=2, after_after=5))
    probs = P.script_problems(sc, rc["typed"], rc)
    assert all("typed sentence" not in p for p in probs)
    sc["proof"] = dict(before_after=5, after_after=7)
    segs[1]["show"] = ["3x faster"]
    segs[2]["say"] = "in 3 minutes"
    probs = P.script_problems(sc, rc["typed"], rc)
    assert any("before_after" in p for p in probs)
    assert any("'3x'" in p for p in probs)
    assert any("digits in say" in p for p in probs)


def test_recording_is_shown_as_delivered():
    l1 = dict(assets={"v1": dict(path="/x/after.mp4"), "v2": dict(path="/x/clip.mp4")},
              items=[dict(id="s1", k="shot", f=[0, 100], pic="v2", look="mono_silver"),
                     dict(id="s2", k="shot", f=[100, 300], pic="v1", look="mono_silver"),
                     dict(id="fl", k="flash", f=[0, 400], mode="pool", a=0.4),
                     dict(id="fx", k="flash", f=[150, 153], mode="fill"),
                     dict(id="gt", k="card", f=[50, 350], z=40, pic="gate", op=[0.9] * 300)])
    spans = P.as_delivered(l1, {"/x/after.mp4"})
    assert spans == [(100, 300)]
    ids = {it["id"]: it for it in l1["items"]}
    assert "look" not in ids["s2"] and ids["s1"]["look"] == "mono_silver"
    assert "fx" not in ids
    pools = sorted(it["f"] for it in l1["items"] if it.get("mode") == "pool")
    assert pools == [[0, 100], [300, 400]]
    gates = sorted((it["f"], len(it["op"])) for it in l1["items"] if it.get("pic") == "gate")
    assert gates == [([50, 100], 50), ([300, 350], 50)]
