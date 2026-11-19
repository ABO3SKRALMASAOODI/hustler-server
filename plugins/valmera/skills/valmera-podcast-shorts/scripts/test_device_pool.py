"""The supply that never leaves a moment bare (creativity lane B, 20 Nov 2026): every picture the supply holds rides the
scene as the palette's pool, the chooser keeps a picture in every window that has candidates, and a picture of what
the words mean is a workflow's own choice."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import placement_check as pc  # noqa: E402
import wf1_coverage as cov  # noqa: E402


def _files(tmp_path, names):
    out = {}
    for n in names:
        p = tmp_path / n
        p.write_bytes(b"x")
        out[n] = str(p)
    return out


def _pack(tmp_path):
    f = _files(tmp_path, ["a.mp4", "b.mp4", "dull.mp4", "c.mp4", "p.jpg", "q.jpg", "cleared.mp4"])
    windows = [dict(first=0, last=3, name="Mark Cuban", t0=0.0, t1=2.0, say="w", must_show="m"),
               dict(first=4, last=9, name="Shark Tank", t0=3.0, t1=6.0, say="w", must_show="s")]
    footage = [dict(i=0, clips=[dict(path=f["a.mp4"], seconds=1.6, w=1920, h=1080, x=0.62, subject="Mark Cuban", shows="close-up",
                                     faces=[dict(box=[0.5, 0.2, 0.7, 0.5], eyes=[[0.55, 0.3], [0.65, 0.3]])]),
                                dict(path=f["dull.mp4"], seconds=1.6, dull=True)]),
               dict(i=1, clips=[dict(path=f["b.mp4"], seconds=1.5, subject="Shark Tank"), dict(path=f["c.mp4"], seconds=1.5),
                                dict(path=str(tmp_path / "gone.mp4"), seconds=1.0)])]
    pictures = {"Shark Tank": [dict(path=f["p.jpg"], w=1200, h=800, title="The set")], "Elsewhere": [dict(path=f["q.jpg"], w=800, h=800)]}
    return dict(coverage=dict(windows=windows), footage=footage, pictures=pictures), f


def test_every_supply_picture_reaches_the_pool_with_what_was_measured_of_it(tmp_path):
    pack, f = _pack(tmp_path)
    report = dict(rejected_paths=[f["c.mp4"]], text_cleared=[dict(from_path=f["b.mp4"], path=f["cleared.mp4"])])
    pool = cov.device_pool(pack, report)
    by = {os.path.basename(r["path"]): r for r in pool}
    assert set(by) == {"a.mp4", "cleared.mp4", "p.jpg", "q.jpg"}          # dull, contradicted and missing pictures stay out
    a = by["a.mp4"]
    assert a["kind"] == "clip" and a["seconds"] == 1.6 and a["x"] == 0.62 and a["faces"] and a["window"] == 0
    assert a["t"] == [0.1, 2.1]                                             # programme seconds of its window (the lead in)
    assert by["cleared.mp4"]["window"] == 1 and "w" not in by["cleared.mp4"]      # the cleared copy, sized from its own file
    assert by["p.jpg"]["kind"] == "photo" and by["p.jpg"]["window"] == 1 and by["q.jpg"]["t"] is None
    assert len({r["id"] for r in pool}) == len(pool)


def test_the_chooser_keeps_a_picture_in_every_window_that_has_candidates(tmp_path):
    f = _files(tmp_path, [f"w{i}c{k}.jpg" for i in range(3) for k in range(3)])
    windows = [dict(first=i * 4, last=i * 4 + 3, name=f"S{i}", t0=i * 3.0, t1=i * 3.0 + 2.5, say=f"words {i}", must_show="x") for i in range(3)]
    pictures = {f"S{i}": [dict(path=f[f"w{i}c{k}.jpg"], w=800, h=800) for k in range(3)] for i in range(3)}

    def checker(rows):
        # the first-ranked candidate of every window contradicts its words: each window takes its next one
        out = []
        for r in rows:
            bad = r[4]["path"].endswith("c0.jpg")
            out.append(dict(ok=not bad, fit=8 if not bad else 0, subject_x=None, motion_ok=False))
        return [r for r, d in zip(rows, out) if d["ok"]], dict(placements=out, spend=[])
    ins, report = cov.checked_proposals(dict(windows=windows), {}, pictures, 12.0, checker)
    assert {r[4]["window"] for r in ins} == {0, 1, 2}
    assert not any(r[4]["path"].endswith("c0.jpg") for r in ins)


def test_an_association_window_reaches_the_check_as_a_picture_of_the_meaning(tmp_path):
    f = _files(tmp_path, ["cheetah.jpg"])
    windows = [dict(first=0, last=5, name="cheetah at full speed", t0=0.0, t1=2.0, say="there is no plan B", must_show="a cheetah running",
                    association=True)]
    ins = cov.proposals(dict(windows=windows), {}, {"cheetah at full speed": [dict(path=f["cheetah.jpg"], w=800, h=600)]})
    assert ins and ins[0][4]["association"] == "a cheetah running"
    assert "association" in pc.SYSTEM and "never a contradiction for not showing the thing named" in pc.SYSTEM
    plain = cov.proposals(dict(windows=[dict(windows[0], association=False)]), {}, {"cheetah at full speed": [dict(path=f["cheetah.jpg"], w=800, h=600)]})
    assert "association" not in plain[0][4]


def test_the_coverage_director_may_plan_a_picture_of_what_the_words_mean():
    import inspect
    src = inspect.getsource(cov.plan)
    assert '"association": true' in src and "never in place of a named subject's own picture" in src
