"""Clip edges serve the story (19 Nov rating, cause clip-edges): the selection reads timed sentences and a moment
measured longer than a short is tightened by the same model; the script stage gets the hook and payoff as measured
anchors and a window it gave too long is handed back, never a stop."""
import json
from types import SimpleNamespace

import pytest

import podcast


WORDS = [(0.0, "So"), (0.3, "we"), (0.6, "wrote"), (0.9, "the"), (1.2, "check."), (3.0, ">> Was"), (3.3, "it"), (3.6, "scary?"),
         (4.0, ">> Yeah,"), (4.4, "it"), (4.6, "was"), (5.9, "really"), (6.2, "bad")]


def test_the_selection_reads_one_sentence_a_line_with_its_measured_times():
    lines = podcast.sentence_lines(WORDS)
    assert lines[0] == "[0:00:00|0.0-2.0] So we wrote the check."
    assert lines[1].startswith("[0:00:03|3.0-") and lines[1].endswith(">> Was it scary?")
    assert lines[2].startswith("[0:00:04|4.0-") and lines[2].endswith(">> Yeah, it was")      # a 1.3 s pause ends a line
    assert lines[3].startswith("[0:00:05|5.9-6.8]")
    assert "start-end seconds" in podcast.select_prompt({}, [], lines)


def test_a_long_moment_is_tightened_by_the_same_model_on_its_timed_sentences():
    words = [(float(t), "word.") if t % 5 == 4 else (float(t), "word") for t in range(3150, 3330)]
    m = dict(id="m01", title="Writing a $100B check blind", t0=3183.0, t1=3307.0, hook="write this check", payoff="really bad")
    seen = []

    def ask(system, content, **kw):
        seen.append(content)
        if len(seen) == 1:
            return json.dumps(dict(t0=3190.0, t1=3300.0)), {}                       # still too long: handed back measured
        return json.dumps(dict(t0=3270.0, t1=3306.5, hook="Susan pulled me aside", payoff="really really bad", title="If it's bad", why="w")), {}
    spend = podcast.tighten({}, m, words, ask=ask)
    assert len(spend) == 2 and "measured at 124 s" in seen[0] and "runs 110 s" in seen[1]
    assert (m["t0"], m["t1"], m["hook"], m["title"]) == (3270.0, 3306.5, "Susan pulled me aside", "If it's bad")
    assert m["tightened"]["from_s"] == [3183.0, 3307.0] and m["duration_s"] == 36.5
    # no answer that fits: the moment stays as chosen and the script stage cuts it
    m2 = dict(t0=100.0, t1=200.0, hook="h", payoff="p")
    podcast.tighten({}, m2, words, ask=lambda *a, **k: ("no json", {}))
    assert (m2["t0"], m2["t1"]) == (100.0, 200.0) and "tightened" not in m2


def test_only_moments_longer_than_a_short_are_handed_back(tmp_path, monkeypatch):
    import llm
    words = [(float(t), "word.") for t in range(0, 300)]
    (tmp_path / "words.json").write_text(json.dumps(words))
    calls = []

    def ask(system, content, **kw):
        calls.append(system)
        if system is podcast.TIGHTEN_SYSTEM:
            return json.dumps(dict(t0=20.0, t1=60.0, hook="h", payoff="p")), {}
        return json.dumps(dict(podcast={}, moments=[dict(title="long", t0=10, t1=110, strength=8, shows=["x"]),
                                                    dict(title="ok", t0=150, t1=190, strength=8, shows=["y"])])), {}
    monkeypatch.setattr(llm, "ask", ask)
    sel = podcast.moments(SimpleNamespace(p=lambda *a: str(tmp_path.joinpath(*a))), dict(info=dict(id="x", duration=300)))
    assert sum(1 for c in calls if c is podcast.TIGHTEN_SYSTEM) == 1
    by = {m["title"]: m for m in sel["moments"]}
    assert (by["long"]["t0"], by["long"]["t1"], by["long"]["duration_s"]) == (20.0, 60.0, 40.0) and by["ok"]["duration_s"] == 40.0


def _ctx(tmp_path, moment):
    words = [[float(t) / 2, f"w{t}"] for t in range(0, 400)]
    (tmp_path / "words.json").write_text(json.dumps(words))
    return SimpleNamespace(args=dict(moment=moment, words=str(tmp_path / "words.json"), style="window", identity="Speaker: X"),
                           source="/podcast.mp4")


def test_the_script_gets_the_measured_anchors_and_a_long_window_is_handed_back(tmp_path, monkeypatch):
    import wf1_short
    moment = dict(title="T", t0=40.0, t1=90.0, hook="the hook", payoff="the payoff")
    ctx = _ctx(tmp_path, moment)
    seen = []

    def ask(system, content, **kw):
        seen.append(content)
        out = (10.0, 100.0) if len(seen) == 1 else (39.8, 90.6)
        return json.dumps({"in": out[0], "out": out[1], "title": "Window", "hook_line": "a", "close_line": "b"}), {}
    monkeypatch.setattr(wf1_short, "_llm", lambda: SimpleNamespace(ask=ask, json_block=json.loads))
    sc = wf1_short.script(ctx, dict(ideas=[moment]))
    assert "The hook opens at 40.00 s: the hook" in seen[0] and "The payoff closes at 90.00 s" in seen[0]
    assert "runs 90.0 s" in seen[1]
    assert sc["window"] == [39.8, 90.6] and sc["title"] == "Window"


def test_the_script_stage_never_stops_for_want_of_a_window(tmp_path, monkeypatch):
    import wf1_short
    moment = dict(title="T", t0=40.0, t1=90.0, hook="the hook", payoff="the payoff")
    ctx = _ctx(tmp_path, moment)
    monkeypatch.setattr(wf1_short, "_llm", lambda: SimpleNamespace(
        ask=lambda *a, **k: (json.dumps({"in": 0, "out": 200, "title": "Too long"}), {}), json_block=json.loads))
    sc = wf1_short.script(ctx, dict(ideas=[moment]))
    assert sc["window"] == [39.8, 90.5] and sc["title"] == "Too long"


def test_the_cut_holds_the_picture_and_fades_the_word_it_does_not_keep(tmp_path):
    """The breath after the last word: the picture runs to the out-point, the sound is gone before the next word."""
    import subprocess
    import numpy as np
    import wf1_short  # noqa: F401  (puts the shell on the path)
    import brand
    import chain
    src = str(tmp_path / "src.mp4")
    subprocess.run([brand.FF, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=320x240:r=30:d=4", "-f", "lavfi",
                    "-i", "sine=f=440:r=48000:d=4", "-shortest", "-c:v", "libx264", "-c:a", "aac", src], check=True)
    out = str(tmp_path / "cut.mp4")
    chain.cut(None, src, 0.5, 3.5, out, fade_at=1.5)
    assert abs(brand.probe(out)["dur"] - 3.0) < 0.1
    pcm = subprocess.run([brand.FF, "-v", "error", "-i", out, "-ac", "1", "-ar", "8000", "-f", "s16le", "-"], capture_output=True).stdout
    x = np.frombuffer(pcm, np.int16).astype(np.float32)
    assert np.abs(x[:8000]).mean() > 1000 and np.abs(x[int(1.7 * 8000):int(2.9 * 8000)]).max() < 50
