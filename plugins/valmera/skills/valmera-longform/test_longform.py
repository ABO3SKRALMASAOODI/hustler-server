"""Workflow 4's tests: a synthetic long film baked end to end (no model, no network), the spoken-form merge, the script
checks. Run: $PY -m pytest -q test_longform.py"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "valmera-stories"))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "_shell"))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "_shell", "narrate"))


def _fixture(tmp, n_seg=24, words_per_seg=40):
    import design
    from PIL import Image
    chapters = [dict(id=f"c{k}", title=f"Part {k}", question=f"What happened in part {k}?", pace=("slow", "steady", "brisk")[k % 3]) for k in range(1, 6)]
    segs, words, t = [], [], 0.2
    names = ["Alpha Ship", "Bravo Port", "Charlie Engine"]
    for i in range(n_seg):
        ch = chapters[min(i * len(chapters) // n_seg, len(chapters) - 1)]["id"]
        say = " ".join(f"w{i}x{k}" for k in range(words_per_seg - 2)) + f" {names[i % 3].split()[0]} end{i}."
        beat = "hook" if i == 0 else "promise" if i == 1 else "turn" if i == n_seg * 2 // 3 else "setup"
        segs.append(dict(chapter=ch, say=say, beat=beat, pictures=[names[i % 3]], design=None, ticks=[], facts=[]))
        for k, w in enumerate(say.split()):
            words.append(dict(w=w, start=round(t, 3), end=round(t + 0.26, 3), seg=i, chunk=i))
            t += 0.31
        t += 0.6
    segs[3]["ticks"] = [dict(row="cash", to="$4,000", number=4000, word="end3.")]
    sc = dict(title="Synthetic", chapters=chapters, segments=segs, promise="By the end you will know why",
              ledger=dict(title="Ledger", rows=[dict(id="cash", label="CASH", start="$5,000", number=5000), dict(id="days", label="DAYS", start="0", number=0)]),
              close=dict(balance="Five thousand in, one canal out"))
    wj = os.path.join(tmp, "words.json")
    json.dump(dict(duration=round(t, 2), words=words), open(wj, "w"))
    pics = {}
    for k, n in enumerate(names):
        recs = []
        for j in range(3):
            p = os.path.join(tmp, f"p{k}{j}.jpg")
            Image.new("RGB", (1600 if j % 2 == 0 else 900, 1000 if j % 2 == 0 else 1300), (60 + 50 * k, 90, 120 + 30 * j)).save(p)
            recs.append(dict(name=n, path=p, w=1600 if j % 2 == 0 else 900, h=1000 if j % 2 == 0 else 1300, faces=[], title=f"{n} {j}"))
        pics[n] = recs
    d1 = design.timeline([dict(year=1881, label="start"), dict(year=1914, label="end")], os.path.join(tmp, "design", "d005_timeline.png"))
    d2 = design.document("A quote that stands on its page, word for word, in full.", "A page", "https://example.org/x", "word for word", 1900,
                         os.path.join(tmp, "design", "d009_document.png"))
    designs = {"5": dict(d1, kind="timeline", seg=5), "9": dict(d2, kind="document", seg=9)}
    sup = dict(sid="test", words=wj, pictures=pics, designs=designs, sounds={
        "tick_1": dict(file="tick_1.wav", peak_s=0.01, max_db=-3.0), "whoosh_soft_1": dict(file="whoosh_soft_1.wav", peak_s=0.3, max_db=-3.0),
        "whoosh_soft_2": dict(file="whoosh_soft_2.wav", peak_s=0.3, max_db=-3.0), "impact_1": dict(file="impact_1.wav", peak_s=0.05, max_db=-1.0),
        "shutter_2": dict(file="shutter_2.wav", peak_s=0.02, max_db=-3.0)})
    return sc, sup


def test_build_covers_every_frame_and_follows_pace(tmp_path):
    import film_long as L
    from scene import plan as SP
    sc, sup = _fixture(str(tmp_path))
    l1, beats = L.build(sc, sup)
    ok, why = SP.eligible(l1)
    assert ok, why
    dens = L.density(beats, sc)
    assert dens["longest_hold_s"] <= L.MAX_HOLD_S + 2.5, dens
    assert 1.2 <= dens["events_10s"] <= 4.5, dens
    taste = L.taste_check(l1, beats)
    assert taste["sounds"] <= taste["sounds_allowed"], taste
    assert [c["n"] for c in beats["chapters"]] == [1, 2, 3, 4, 5]
    assert l1["frame"] == [1920, 1080]


def test_spoken_merge_never_changes_a_heard_word():
    import spoken
    text, rep = spoken.merge("In 2005 the Rabbit cost $5,000 and Oswald's owner said no.",
                             "In two thousand five the rabbit cost five thousand dollars and Oswald owner said yes.")
    assert text == "In two thousand five the Rabbit cost five thousand dollars and Oswald's owner said no."
    assert rep["spoken_by_model"] == 2 and rep["model_changes_rejected"] == 2


def test_script_check_finds_digits_and_unbacked_figures():
    import story
    dos = dict(facts=[dict(id="f1", quote="It cost 5,000 dollars in 1904.", url="https://x")])
    sc = dict(segments=[dict(say="It cost 5000 dollars.", facts=["f1"]), dict(say="nineteen oh four", facts=["f9"],
              design=dict(kind="number", print="$7,000", fact="f1"))], chapters=[dict(id="c1")])
    rep = story.check_script(sc, dos)
    assert not rep["ok"]
    assert any("digits" in p for p in rep["problems"])
    assert any("f9" in p for p in rep["problems"])
    assert rep["figures_unbacked"] == ["7000"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
