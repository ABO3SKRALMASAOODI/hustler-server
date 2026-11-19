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


def test_the_rewrite_goes_back_to_the_writer_with_the_seconds_and_recovers_an_unanswered_call():
    sc = _sc()
    m = timing.measure(_words(sc), sc)
    seen, slept = [], []

    def ask(system, content):
        seen.append(content)
        if len(seen) == 1:
            raise TimeoutError("no answer")
        return json.dumps(dict(segments=[dict(i=0, say=SHARP_HOOK, facts=["b1"], emphasis=["booed"], lands="booed"),
                                         dict(i=2, say=SHORT_CLOSE, facts=["b1", "zz"], emphasis=["comeback."]),
                                         dict(i=1, say="never asked for")])), dict(usd=0.01)
    out, uses, changed = timing.rewrite(sc, m, ask=ask, log=lambda *_: None, sleep=slept.append)
    assert len(seen) == 2 and slept == [timing.BACKOFF_S]                       # an outage is retried with backoff
    rows = json.loads(seen[-1].split("# The beats to rewrite (measured on the narration)\n")[1].split("\n\n#")[0])
    assert {r["beat"] for r in rows} == {"hook", "close"} and all(r["seconds"] > r["budget_s"] and r["budget_words"] > 0 for r in rows)
    assert changed == [0, 2] and out["segments"][1]["say"] == sc["segments"][1]["say"]
    assert out["segments"][0]["say"] == SHARP_HOOK and out["segments"][0]["lands"] == "booed" and out["hook_line"] == SHARP_HOOK
    assert out["segments"][2]["facts"] == ["b1"] and out["close_line"] == SHORT_CLOSE      # only bank facts are cited
    assert "date" in timing.REWRITE_SYSTEM and "ONE landing line" in timing.REWRITE_SYSTEM


def test_the_script_stage_reads_rewrites_and_keeps_the_read_that_lands(tmp_path):
    import wf3
    ctx = types.SimpleNamespace(run_dir=str(tmp_path), supply=str(tmp_path / "supply"), tmp=str(tmp_path), args={}, run_id="t",
                                log=lambda *_: None)
    reads, finals = [], []

    def narrate(sc_, out):
        os.makedirs(out, exist_ok=True)
        w = _words(sc_)
        json.dump(w, open(os.path.join(out, "words.json"), "w"))
        reads.append(sc_["segments"][0]["say"])
        return dict(words=os.path.join(out, "words.json"))

    def rewrite(sc_, m, notes):
        out = json.loads(json.dumps(sc_))
        out["segments"][0].update(say=SHARP_HOOK, lands="booed")
        out["segments"][-1].update(say=SHORT_CLOSE)
        return out, [dict(usd=0.02)], [0, 2]

    def final(sc_, rep_):
        finals.append(sc_["segments"][-1]["say"])
        return sc_, dict(rep_, judged=True)
    spend = []
    sc2, rep2 = wf3.timed(ctx, _sc(), dict(ok=True), spend, narrate=narrate, rewrite=rewrite, final=final)
    assert reads == [DATE_HOOK, SHARP_HOOK] and finals == [SHORT_CLOSE]            # judged on its quotes before it is read
    assert sc2["segments"][0]["say"] == SHARP_HOOK and rep2["judged"] and sc2["timing"]["kept"] == 1
    assert [r["overage_s"] > 0 for r in sc2["timing"]["rounds"]] == [True, False]
    assert os.path.exists(os.path.join(ctx.supply, "narration", "words.json"))     # supply reuses the read it was timed on
    assert not any(n.startswith("narration_t") for n in os.listdir(ctx.supply))
    assert spend == [dict(stage="timing", usd=0.02)]


def test_a_rewrite_the_fact_check_cannot_stand_keeps_the_script_and_tells_the_writer(tmp_path):
    import wf3
    ctx = types.SimpleNamespace(run_dir=str(tmp_path), supply=str(tmp_path / "supply"), tmp=str(tmp_path), args={}, run_id="t",
                                log=lambda *_: None)
    notes = []

    def narrate(sc_, out):
        os.makedirs(out, exist_ok=True)
        json.dump(_words(sc_), open(os.path.join(out, "words.json"), "w"))
        return dict(words=os.path.join(out, "words.json"))

    def rewrite(sc_, m, n):
        notes.append(n)
        out = json.loads(json.dumps(sc_))
        out["segments"][0]["say"] = SHARP_HOOK
        return out, [], [0]

    def final(sc_, rep_):
        raise RuntimeError("the final support pass does not stand: hook unsupported")
    sc0 = _sc()
    sc2, _ = wf3.timed(ctx, sc0, dict(ok=True), [], narrate=narrate, rewrite=rewrite, final=final)
    assert sc2["segments"][0]["say"] == DATE_HOOK and notes[0] is None and "could not stand" in notes[1]
    assert len(notes) == timing.TIMING_ASKS and len(sc2["timing"]["rounds"]) == 1      # every refused ask, one read


def _ctx(tmp_path):
    return types.SimpleNamespace(run_dir=str(tmp_path), supply=str(tmp_path / "supply"), tmp=str(tmp_path), args={}, run_id="t",
                                 log=lambda *_: None)


def _reader(reads):
    def narrate(sc_, out):
        os.makedirs(out, exist_ok=True)
        json.dump(_words(sc_), open(os.path.join(out, "words.json"), "w"))
        reads.append(tuple(s_["say"] for s_ in sc_["segments"]))
        return dict(words=os.path.join(out, "words.json"))
    return narrate


def test_unchanged_words_are_never_read_twice(tmp_path):
    """A refused or empty rewrite narrated the same words into a new narration_t folder: a second full Phonon read of
    identical text. The last read's measure stands; the writer is asked again with the reason."""
    import wf3
    reads, notes, finals = [], [], []

    def rewrite(sc_, m, n):
        notes.append(n)
        out = json.loads(json.dumps(sc_))
        if len(notes) == 2:
            return out, [], []                                                      # an answer that changes nothing
        out["segments"][0].update(say=SHARP_HOOK, lands="booed")
        out["segments"][-1].update(say=SHORT_CLOSE)
        return out, [], [0, 2]

    def final(sc_, rep_):
        finals.append(1)
        if len(finals) == 1:
            raise RuntimeError("the final support pass does not stand: hook unsupported")
        return sc_, dict(rep_, judged=True)
    sc2, rep2 = wf3.timed(_ctx(tmp_path), _sc(), dict(ok=True), [], narrate=_reader(reads), rewrite=rewrite, final=final)
    assert len(reads) == len(set(reads)) == 2                                       # no words read twice
    assert reads[0][0] == DATE_HOOK and reads[1][0] == SHARP_HOOK
    assert notes[0] is None and "could not stand" in notes[1] and "changed none" in notes[2] and len(notes) == 3
    assert sc2["segments"][0]["say"] == SHARP_HOOK and rep2["judged"] and sc2["timing"]["kept"] == 1
    assert [a["read"] for a in sc2["timing"]["asks"]] == [False, False, True]


def test_script_final_is_the_kept_script(tmp_path, monkeypatch):
    """timed's rewrites ran final_gate, which overwrote script_final.json with a rewrite later dropped (its read lost
    to round 0). Each rewrite's gate has its own record; script_final.json is the kept script's."""
    import wf3
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(wf3.writer, "final_support", lambda sc_, **kw: (sc_, dict(ok=True, verify=dict(ok=True), mark=sc_["segments"][0]["say"]), []))
    sc0, rep0 = wf3.final_gate(ctx, _sc(SHARP_HOOK, LONG_CLOSE), dict(ok=True), [])
    reads = []

    def rewrite(sc_, m, n):                                                          # a longer close: its read is worse
        out = json.loads(json.dumps(sc_))
        out["segments"][0]["say"] = DATE_HOOK
        out["segments"][-1]["say"] = LONG_CLOSE + " " + LONG_CLOSE
        return out, [], [0, 2]
    sc2, _ = wf3.timed(ctx, sc0, rep0, [], narrate=_reader(reads), rewrite=rewrite)
    assert len(reads) == 2 and sc2["timing"]["kept"] == 0                           # its repeat is the same words: not read again
    rec = json.load(open(tmp_path / "script_final.json"))
    assert rec["script"]["segments"] == sc2["segments"] and rec["script"]["segments"][0]["say"] == SHARP_HOOK
    assert rec["report"]["mark"] == SHARP_HOOK and rec["record"] == "script_final.json"
    assert json.load(open(tmp_path / "script_timing_final1.json"))["script"]["segments"][0]["say"] == DATE_HOOK


def test_the_ear_keeps_a_rewrite_that_cuts_the_close():
    """The whole-film 10% band threw the ear's rewrite out when the close was cut to one line (C4)."""
    import inspect
    src = inspect.getsource(ear.for_ear)
    assert "the script stands\")" not in src and "outside {KEEP_LEN" not in src
    assert "hook (the first segment)" in inspect.getsource(ear.rewrite)


def test_the_writer_and_the_spine_are_given_the_beat_budgets():
    assert "never on a date" in writer.BANK_WRITE_RULES and str(writer.CLOSE_WORDS) in writer.BANK_WRITE_RULES
    assert "ONE fact" in writer.SPINE_CLAIMS_SYSTEM and "never a date" in writer.SPINE_CLAIMS_SYSTEM


def test_beat_names_are_never_offered_as_chapter_titles():
    """C6: 'beat=turn' beside the words and '(the turn, the low point, the payoff)' in the ask printed THE TURN."""
    sc = _sc()
    sc["segments"][1]["beat"] = "turn"
    content = writer._ann_content(sc, {})
    assert "beat=" not in content and "role in the story: where the story turns" in content
    assert "(the turn, the low point, the payoff)" not in writer.ANNOTATE_SYSTEM and "a chapter at each turn" not in writer.ANNOTATE_SYSTEM
    assert "private notes" in writer.ANNOTATE_SYSTEM and "never words for the screen" in writer.REEL_DATA


def test_the_density_pass_asks_for_chapters_the_way_annotate_does():
    """The density pass still asked for 'a two-to-four-word chapter title on a segment that opens a section' beside each
    segment's role: a chapter card on every turn, against ANNOTATE_SYSTEM and FORMS['short'] (no chapters)."""
    import wf3
    assert "a two-to-four-word chapter title on a segment that opens a section" not in wf3.DENSITY_SYSTEM
    assert "truly changes subject" in wf3.DENSITY_SYSTEM and "at most one in the whole film" in wf3.DENSITY_SYSTEM
    assert "key word" in wf3.DENSITY_SYSTEM and "names" in wf3.DENSITY_SYSTEM and "never becomes words on the screen" in wf3.DENSITY_SYSTEM
    sc = _sc(SHARP_HOOK, SHORT_CLOSE)
    sc["segments"][1].update(beat="turn", show=[dict(text="The Edge", role="chapter", word_i=0)])
    seen = []
    wf3.density_repair(sc, [1, 2], ask=lambda system, content: (seen.append(content), ('{"items": []}', {}))[1])
    assert '# The film\'s chapter titles so far\n["The Edge"]' in seen[0] and "role in the story: where the story turns" in seen[0]


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
