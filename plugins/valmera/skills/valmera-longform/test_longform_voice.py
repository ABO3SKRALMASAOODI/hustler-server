"""The long-form narrator substitutes hooks into the shared reader (voice.py). Whatever the shared reader passes
to a hook, the hook must take: a read that spent hours must not die at its last step on a signature."""
import ast
import inspect
import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

import longform_voice as LV

VOICE_SOURCE = Path(LV.shared.__file__)


def _calls_of(name, in_function):
    tree = ast.parse(VOICE_SOURCE.read_text())
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and fn.name == in_function:
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name:
                    yield node


def _read_folder(tmp_path):
    out = tmp_path / "narration"
    out.mkdir()
    sf.write(out / "narration.wav", np.zeros(2400, np.float32), 24000)
    (out / "words.json").write_text(json.dumps(dict(words=[])))
    (out / "render.json").write_text(json.dumps(dict(engine="lines:phonon")))
    return out


def test_checkpoint_takes_the_call_the_shared_reader_makes(tmp_path, monkeypatch):
    """voice.read_block ends with finish(folder, local, local_groups, rd.picks, max_tempo=LN.MAX_TEMPO); the
    long-form checkpoint is substituted for it. The old signature raised: unexpected keyword argument 'max_tempo'."""
    import narrate
    monkeypatch.setattr(narrate, "free_gate_listener", lambda: None)
    monkeypatch.setattr(narrate, "free_take_listener", lambda: None)
    out = _read_folder(tmp_path)
    script = dict(segments=[dict(text="one two")])
    LV.raw_checkpoint(out, script, [[0]], [dict(line=0)], max_tempo=1.08)
    stage = json.loads((out / "read-stage.json").read_text())
    assert stage["max_tempo"] == 1.08          # the render's tempo ceiling is kept for the mastering that follows
    assert stage["groups"] == [[0]] and stage["picks"] == [dict(line=0)]
    for name in ("narration_raw.wav", "words_raw.json", "render_raw.json"):
        assert (out / name).exists()


def test_every_shared_finish_call_binds_to_the_checkpoint():
    calls = list(_calls_of("finish", "read_block"))
    assert calls, "voice.read_block no longer calls finish(...)"
    sig = inspect.signature(LV.raw_checkpoint)
    for call in calls:
        sig.bind(*[None] * len(call.args), **{k.arg: None for k in call.keywords})


def _shape(fn):
    return [(p.name, p.kind, p.default) for p in inspect.signature(fn).parameters.values()]


def test_checkpoint_and_shared_finish_stay_compatible():
    assert _shape(LV.raw_checkpoint) == _shape(LV.shared.finish)


def test_bounded_generation_keeps_the_signature_of_the_method_it_wraps():
    wrapped = LV.bounded_generation(LV.shared.Read.generate_phonon)
    assert _shape(wrapped) == _shape(LV.shared.Read.generate_phonon)


def test_bounded_generation_caps_takes_and_passes_the_rest(tmp_path):
    seen = []
    wrapped = LV.bounded_generation(lambda self, job, dest, log, seed0=7: seen.append((job, seed0)),
                                    respell=lambda text, approach: text + str(approach))
    logs = []
    job = dict(takes=6, lines=[dict(id="s01", takes=64, text="a"), dict(id="s02")])
    wrapped(None, job, tmp_path, logs.append, 9)
    assert [[r.get("takes") for r in j["lines"]] for j, _ in seen] == [[6], [36], [42], [48]]
    assert {sd for _, sd in seen} == {9}
    assert job["lines"][0]["takes"] == 64 and logs == ["s01: thirty-take retry budget spent; retaining strict gate"]


def test_a_repeated_round_after_the_budget_is_read_a_different_way(tmp_path):
    """27 Nov (documentary 11260010): s55 and s65 had spent thirty takes; every later round asked for more, got the same
    thirty, and came back identical. Now the next six takes are read from a respelled form, then a phrased one, then both."""
    calls = []
    def original(self, job, out, log, seed0=7):
        row = job["lines"][0]
        calls.append((row["id"], row["takes"], row["text"]))
    forms = {1: "alp-OH-gay says", 2: "Alpöge, says", 3: "alp-OH-gay, says"}
    wrapped = LV.bounded_generation(original, respell=lambda text, approach: forms[approach])
    logs = []
    for requested in (36, 42, 48):
        calls.clear()
        wrapped(None, dict(voice="Easton", takes=6, lines=[dict(id="s55", takes=requested, text="Alpöge says")]), tmp_path, logs.append)
        assert calls[-1] == ("s55", requested, forms[(requested - 30) // 6]), calls
        assert all(text != "Alpöge says" for _, _, text in calls)
    calls.clear()
    wrapped(None, dict(voice="Easton", takes=6, lines=[dict(id="s55", takes=54, text="Alpöge says")]), tmp_path, logs.append)
    assert calls[-1][1] == 48 and logs[-1].endswith("retaining strict gate")
    assert json.loads((tmp_path / "approaches.json").read_text())["s55:1"] == forms[1]   # a resume reuses it


def test_a_listener_spelling_a_name_or_letters_differently_is_not_an_error():
    import measure
    script = "Levent Alpöge said Alpöge's proof ran at em eye tee and open ay eye, with eff are oh and caf\u00e9 notes."
    heard = "Levent Alpoge said Alpoge's proof ran at MIT and OpenAI, with FRO and cafe notes."
    assert measure.wer(script, heard)["wer"] == 0
    assert measure.wer("the road is clearly open", "the road is clear open")["wer"] > 0     # a real misread still counts


def test_a_cached_take_is_counted_again_from_the_words_it_was_heard_saying(tmp_path):
    import lines as LN
    seg = dict(text="Alpöge says hello.")
    assert LN.errs_of(None, seg["text"], "Alpoge says hello.", []) == 0


def test_residual_spelling_variants_are_judged_by_a_model_and_the_film_goes_on(tmp_path):
    import measure_voice as MV
    script = "Alpöge said the river is clearly wide. " + " ".join(["word"] * 40)
    seen = []
    def judge(system, user):
        seen.append(user)
        return json.dumps(dict(same=[i for i, row in enumerate(user.splitlines()) if "nenoda" in row]))
    details = [dict(op="replace", script="nanoda", heard="nenoda", at=6), dict(op="replace", script="clearly", heard="clear", at=5)]
    assert MV.judged_equivalents(details, script, tmp_path, judge) == {0}
    assert MV.judged_equivalents(details, script, tmp_path, lambda *a: 1 / 0) == {0}      # answered once, read back
    assert MV.line_of(3, [dict(text="a b"), dict(text="c d e")]) == 1


def test_judged_take_hook_keeps_the_signature_of_the_function_it_replaces(tmp_path):
    import lines as LN
    original = LN._judge_files
    try:
        LV.cache_take_analysis(tmp_path)
        assert _shape(LN._judge_files) == _shape(original)
    finally:
        LN._judge_files = original


def test_the_mastering_gets_the_ceiling_the_read_ran_under():
    src = Path(LV.__file__).read_text()
    assert "max_tempo=stage.get('max_tempo'" in src


@pytest.fixture
def listeners(monkeypatch):
    import narrate
    import spoken
    calls = dict(listen=0, spoken=0)
    heard = ("one two", [dict(w="one", start=0.0, end=0.1)])
    def listen_long(x, sr, fn=None, max_s=25.0):
        calls["listen"] += 1
        return heard
    answers = [dict(ok=False, why="model call failed"), dict(ok=True, text="one two")]
    def spoken_form(h, script, names=(), model=None):
        calls["spoken"] += 1
        return answers[min(calls["spoken"] - 1, 1)]
    monkeypatch.setattr(narrate, "listen_long", listen_long)
    monkeypatch.setattr(spoken, "spoken_form", spoken_form)
    return calls, narrate, spoken


def test_an_unchanged_join_is_not_listened_to_again(tmp_path, listeners):
    calls, narrate, spoken = listeners
    LV.cache_gate_listening(tmp_path)
    x = np.linspace(-.1, .1, 4800, dtype=np.float32)
    assert narrate.listen_long(x, 24000) == narrate.listen_long(x.copy(), 24000)
    assert calls["listen"] == 1
    narrate.listen_long(x * .5, 24000)          # a join that changed is heard
    assert calls["listen"] == 2
    # a new process of the same film (a resume) finds the answers on disk
    LV.cache_gate_listening(tmp_path)
    narrate.listen_long(x, 24000)
    assert calls["listen"] == 2


def test_a_failed_spoken_form_is_asked_again_and_a_good_one_is_kept(tmp_path, listeners):
    calls, narrate, spoken = listeners
    LV.cache_gate_listening(tmp_path)
    assert spoken.spoken_form("1 two", "one two", ["A"])["ok"] is False
    assert spoken.spoken_form("1 two", "one two", ["A"])["ok"] is True
    assert spoken.spoken_form("1 two", "one two", ["A"])["ok"] is True
    assert calls["spoken"] == 2


def test_a_resume_with_every_take_on_disk_does_not_load_the_voice_model(tmp_path):
    for k in range(2):
        sf.write(tmp_path / f"s00_t{k}.wav", np.zeros(480, np.float32), 24000, subtype="FLOAT")
    def original(self, job, out, log, seed0=7):
        raise AssertionError("the model would be loaded to make no take")
    logs = []
    LV.bounded_generation(original)(None, dict(voice="Easton", takes=2, lines=[dict(id="s00")]), tmp_path, logs.append)
    assert logs and "0 takes" in logs[0]
    seen = []
    LV.bounded_generation(lambda self, job, out, log, seed0=7: seen.append(job))(
        None, dict(voice="Easton", takes=2, lines=[dict(id="s00", takes=3)]), tmp_path, logs.append)
    assert seen, "a take that is missing is still generated"
