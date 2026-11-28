"""The hook's lead and the close's length measured on the read, the beats the writer is asked to land, and the beat
names kept off the screen (round 1, 19 Nov: C4, C6). No model, no network, no narration.

    $PY -m pytest -q test_timing.py
"""
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(os.path.dirname(HERE), "_shell")]
import ear  # noqa: E402
import timing  # noqa: E402
import writer  # noqa: E402

DATE_HOOK = "On August sixth, nineteen ninety-seven, Steve Jobs told his own fans that Microsoft was buying into Apple, and they booed him."
SHARP_HOOK = "Steve Jobs said Microsoft would save Apple, and his fans booed him."
LONG_CLOSE = ("The fans who booed that day were watching a relatively small sum, not a make-or-break amount, buy something the "
              "cash alone could not, credibility for a company that had been written off and was close to imminent bankruptcy.")
SHORT_CLOSE = "A hundred and fifty million bought the comeback."


def _sc(hook=DATE_HOOK, close=LONG_CLOSE):
    return dict(names=["Steve Jobs", "Microsoft", "Apple"],
                facts=[dict(id="b1", quote="Jobs announced Microsoft would invest $150 million in Apple, and the crowd booed."),
                       dict(id="b2", quote="Apple had $1.2 billion in cash.")],
                segments=[dict(say=hook, beat="hook", emphasis=["booed"], facts=["b1"]),
                          dict(say="Apple was weeks from the edge.", beat="setup", facts=["b2"]),
                          dict(say=close, beat="close", facts=["b1", "b2"])])


def _words(sc, wps=3.3, gap=0.25):
    out, t = [], 0.0
    for i, s in enumerate(sc["segments"]):
        for w in s["say"].split():
            out.append(dict(w=w, start=round(t, 3), end=round(t + 0.8 / wps, 3), seg=i))
            t += 1 / wps
        t += gap
    return dict(words=out, duration=round(t, 2))


def test_the_hook_landing_and_the_close_are_measured_on_the_word_times():
    sc = _sc()
    m = timing.measure(_words(sc), sc)
    assert m["hook_word"] == "him." or m["hook_word"].startswith("booed")
    assert m["hook_land_s"] > timing.HOOK_LAND_S and "hook" in m["over"]          # a date first: the collision lands late
    assert m["close_s"] > timing.CLOSE_S and "close" in m["over"]                  # a five-fact close
    sharp = _sc(SHARP_HOOK, SHORT_CLOSE)
    sharp["segments"][0]["lands"] = "booed"
    m2 = timing.measure(_words(sharp), sharp)
    assert m2["hook_word"] == "booed" and m2["hook_land_s"] <= timing.HOOK_LAND_S and m2["close_s"] <= timing.CLOSE_S
    assert m2["over"] == {} and timing.overage(m2) == 0


def _ctx(tmp_path):
    return types.SimpleNamespace(run_dir=str(tmp_path), supply=str(tmp_path / "supply"),
                                 args={}, run_id="test", log=lambda *_: None)


def test_draft_clock_measures_the_meaning_before_a_coda(tmp_path):
    import wf3
    sc = _sc()
    sc["beats"] = {"meaning": 2}
    sc["segments"].append(dict(say="Find out more.", beat="coda"))
    read = wf3.timed(_ctx(tmp_path), sc, narrate=lambda ctx, sample, out: {"words": _words(sample)})
    assert read["segment_indexes"] == [0, 2]
    assert read["over"] == timing.measure(_words(sc), sc)["over"]
    assert sc["segments"][2]["say"] == LONG_CLOSE


def test_cpu_read_failure_falls_back_to_the_production_reader(tmp_path, monkeypatch):
    import wf3
    import subprocess

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "draft reader")

    monkeypatch.setattr(wf3.subprocess, "run", fail)
    monkeypatch.setattr(wf3, "narrate_script", lambda ctx, sample, out: {"words": _words(sample)})
    result = wf3.timed(_ctx(tmp_path), _sc())
    assert result["source"] == "production reader"
    assert set(result["over"]) == {"hook", "close"}


def test_supplied_final_read_is_reserved_for_the_final_script(tmp_path):
    import wf3
    ctx = _ctx(tmp_path)
    ctx.args["narration"] = "/saved/final"
    assert wf3.timed(ctx, _sc())["available"] is False
    assert not os.path.exists(ctx.supply)


def test_cached_draft_read_is_reused_only_for_its_landing_lines(tmp_path, monkeypatch):
    from pathlib import Path
    import wf3
    reads = []

    def take(command, **kwargs):
        sample_file, folder = map(Path, command[-2:])
        sample = json.loads(sample_file.read_text())
        reads.append([s["say"] for s in sample["segments"]])
        clock = dict(_words(sample), request=timing.signature(sample), source="draft", voice="actor")
        (folder / "draft-words.json").write_text(json.dumps(clock))

    monkeypatch.setattr(wf3.subprocess, "run", take)
    ctx = _ctx(tmp_path)
    first = wf3.timed(ctx, _sc())
    assert wf3.timed(ctx, _sc()) == first
    edited = wf3.timed(ctx, _sc(close=SHORT_CLOSE))
    assert reads == [[DATE_HOOK, LONG_CLOSE], [DATE_HOOK, SHORT_CLOSE]]
    assert edited["close_s"] < first["close_s"] and edited["hook_land_s"] == first["hook_land_s"]


def test_the_ear_keeps_a_rewrite_that_cuts_the_close():
    """The whole-film 10% band threw the ear's rewrite out when the close was cut to one line (C4)."""
    import inspect
    src = inspect.getsource(ear.for_ear)
    assert "the script stands\")" not in src and "outside {KEEP_LEN" not in src
    assert "hook (the first segment)" in inspect.getsource(ear.rewrite)


def test_research_and_revision_share_the_hook_and_meaning_line():
    assert "measured hook and close seconds" in writer.BANK_WRITE_RULES
    assert "one payoff figure that answers the hook" in writer.SPINE_CLAIMS_SYSTEM


def test_beat_names_are_never_offered_as_chapter_titles():
    """C6: 'beat=turn' beside the words and '(the turn, the low point, the payoff)' in the ask printed THE TURN."""
    sc = _sc()
    sc["segments"][1]["beat"] = "turn"
    content = writer._ann_content(sc, {})
    assert "beat=" not in content and "role in the story: where the story turns" in content
    assert "(the turn, the low point, the payoff)" not in writer.ANNOTATE_SYSTEM and "a chapter at each turn" not in writer.ANNOTATE_SYSTEM
    assert "Anchor each existing show item to its spoken word_i" in writer.ANNOTATE_SYSTEM


def test_annotate_keeps_which_prompt_wrote_each_item(monkeypatch):
    sc = _sc(SHARP_HOOK, SHORT_CLOSE)
    sc["named"] = [dict(name="Steve Jobs", **{"is": "person"})]
    sc["segments"][0]["show"] = [dict(text="Steve Jobs", role="name", word_i=0)]
    got = dict(colour_turn=1, segments=[dict(i=k, see=[dict(thing="Steve Jobs", as_of="1997", must_show="Jobs on stage")],
                                             finish="none",
                                             show=([dict(text="Booed", role="key", word_i=11)] if k == 0 else []))
                                      for k, s in enumerate(sc["segments"])])
    out, rep = writer.annotate(sc, {}, ask=lambda system, content: (json.dumps(got), {}), verify_fn=lambda s: dict(ok=True),
                               log=lambda *_: None)
    froms = {x["text"]: x["from"] for x in out["segments"][0]["show"]}
    assert froms == {"Steve Jobs": "write", "Booed": "annotate"}
