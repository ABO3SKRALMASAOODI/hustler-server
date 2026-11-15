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


def test_unreadable_product_picture_cannot_pass(monkeypatch):
    monkeypatch.setattr(P, "words_under", lambda *args: "the finished edit")
    monkeypatch.setattr(P, "still", lambda *args, **kwargs: None)
    monkeypatch.setattr(P, "ask", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no frames")))
    picture = dict(path="/x/after.mp4", name="after", f=[0, 90], src=[0, 3])
    bad, spend = P.picture_check(None, {"pictures": [picture]}, None, {picture["path"]}, {})
    assert len(bad) == 1 and "could not be inspected" in bad[0][2]
    assert not spend


def test_missing_and_loose_verdicts_fail_including_product(monkeypatch):
    monkeypatch.setattr(P, "words_under", lambda *args: "captions on the words")
    monkeypatch.setattr(P, "still", lambda *args, **kwargs: "jpeg")
    monkeypatch.setattr(P, "ask", lambda *args, **kwargs: (
        {"pictures": [{"n": 1, "fit": "loose", "why": "only the same speaker"}]}, {}))
    pictures = [dict(path=f"/x/{i}.mp4", name="after", f=[i * 30, (i + 1) * 30], src=[0, 1]) for i in range(2)]
    bad, _ = P.picture_check(None, {"pictures": pictures}, None, {p["path"] for p in pictures}, {})
    assert len(bad) == 2
    assert bad[1][2] == "missing picture verdict"


def test_picture_verdict_is_bound_to_played_window():
    a = dict(path="/x/a.mp4", src=[0, 1])
    b = dict(a, src=[8, 9])
    assert P.picture_key(a, "the edit") != P.picture_key(b, "the edit")


def test_proof_holds_stay_bounded_after_audio_snap(tmp_path, monkeypatch):
    import json
    record = tmp_path / "record.json"
    record.write_text(json.dumps(dict(upload=dict(file="raw.mp4", dur=30, sha256="raw"),
                                      exports=[dict(tag="final", file="export.mp4", dur=35, sha256="export")])))
    cuts = []
    monkeypatch.setattr(P, "endcard_s", lambda: 5)
    monkeypatch.setattr(P, "quiet_snap", lambda path, t: t + (0.4 if t > 4 else -0.1))
    monkeypatch.setattr(P, "cut", lambda src, a, b, out, **kw: cuts.append((src, a, b, out)))
    script = dict(record=str(record), export_check=dict(before=[1, 9], after=[4, 13], tease_s=15))
    product = P.product_clips(script, str(tmp_path / "product"))
    assert product["before"]["t"][1] - product["before"]["t"][0] <= 3
    assert product["after"]["t"][1] - product["after"]["t"][0] <= 4
    assert len(cuts) == 3


def test_instruction_panel_preserves_submitted_sentence():
    class Clock:
        def seg_words(self, i):
            return [dict(t0=4.0, t1=8.0)]
    text = "Make a vertical short cropped to the speaker with white captions on black."
    scene = dict(voices={}, items=[])
    panel = P.instruction_panel(scene, Clock(), dict(proof=dict(after_after=2)), text)
    assert " ".join(panel["lines"]) == text
    assert all(v["px"] >= 38 for v in scene["voices"].values())
    assert panel["frames"] == [120, 240]
