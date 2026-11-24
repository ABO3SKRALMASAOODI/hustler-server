from test_wf1_take import _sup, cuts_two_shot, quiet_engines
import wf1_film


def test_changing_instrument_survives_comparison_and_moves_on_spoken_word(tmp_path, quiet_engines):
    from scene import palette

    pack = _sup(tmp_path, cuts_two_shot())
    words = pack["coverage"]["words"]
    terms = "Imagine you could read a dial on the wall and see the needle move higher after lunch today".split()
    for word, term in zip(words, terms):
        word["w"] = term
    change = dict(enter=5, change=14, leave=15, trend="up")
    pack["coverage"]["comparisons"] = [dict(first=0, last=len(words)-1, premise="Imagine a reading",
        consequence="It might rise", reveal=14, instrument=change)]
    ask = dict(dev="analogy_dial", at=dict(w=5, seg=0), until=dict(w=15, seg=1),
               args=dict(change=14, trend="up"))
    direction = dict(coverage_final=True, director=dict(asks=[ask], sections=[], beats=[]))
    script = dict(title="An imagined reading", segments=[dict(say=" ".join(w["w"] for w in words if w["seg"] == seg))
                                                        for seg in (0, 1)])
    scene, beats = wf1_film.build(script, pack, "prints", direction)
    assert beats["devices"]["asked"] == ["analogy_dial"]
    device, = scene["devices"]
    assert device["change"] == scene["said"][14][1]
    landing = next(it for it in scene["items"] if it.get("role") == "cap" and "higher" in it["t"].split())
    assert landing["f"][0] >= device["change"]
    palette._discover()
    patch = palette.DEVICES["analogy_dial"]["plan"](scene, str(tmp_path), device)
    needle = patch["items"][1]
    onset = device["change"] - needle["f"][0]
    assert len(set(needle["rot"][:onset])) == 1
    assert needle["rot"][onset-1] < needle["rot"][onset] < needle["rot"][-1]
