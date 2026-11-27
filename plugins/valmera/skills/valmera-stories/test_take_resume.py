"""Resume a legacy 78-take line as if its original six candidates had just arrived."""
import argparse
from pathlib import Path
import sys
import types

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_shell" / "narrate"))
import lines
import perform


def test_clean_first_batch_does_not_hear_72_later_takes(tmp_path, monkeypatch):
    script = {"segments": [{"text": "Deep Blue won."}]}
    folder = tmp_path / "_takes"
    folder.mkdir()
    for k in range(78):
        (folder / f"s00_t{k}.wav").touch()
    heard = []
    def judge(narrator, files, *args):
        heard.extend(f.name for f in files)
        return [{"file": f.name, "errs": 0, "_sr": 24000} for f in files]
    class Chosen(Exception):
        pass
    def choose(takes, prior):
        raise Chosen
    monkeypatch.setattr(lines, "_judge_files", judge)
    monkeypatch.setattr(lines, "generate", lambda *args: None)
    monkeypatch.setattr(lines, "phonon_voice", lambda *args: "Easton")
    monkeypatch.setattr(perform, "choose", choose)
    args = argparse.Namespace(engine="phonon", band="03", takes=6)
    with pytest.raises(Chosen):
        lines.render_lines(args, script, "Easton", tmp_path, types.SimpleNamespace(log=print))
    assert heard == [f"s00_t{k}.wav" for k in range(6)]


def test_pool_timeout_switches_once_and_restores_name_prompt(monkeypatch):
    import subprocess
    import take_hearing
    calls = []
    def timeout(x, sr):
        calls.append(1)
        raise subprocess.TimeoutExpired("pool", 45)
    monkeypatch.setattr(take_hearing, "pooled_take", timeout)
    def local(x, sr):
        assert not narrator.VOCAB
        return "real", [{"w": "real", "start": 0, "end": 1}]
    narrator = types.SimpleNamespace(VOCAB=["Kasparov"], listen=local, log=print)
    listen = take_hearing.pool_or_local(narrator)
    assert listen(None, 16000) == listen(None, 16000)
    assert narrator.VOCAB == ["Kasparov"] and calls == [1]


def test_legacy_plain_takes_leave_room_for_changed_delivery(tmp_path, monkeypatch):
    import numpy as np
    import soundfile as sf
    from read_cache import bounded_generation
    for k in range(78):
        sf.write(tmp_path / f"s02_t{k}.wav", np.zeros(160, np.float32), 16000)
    attempts = []
    def generate(read, job, out, log, seed0=7):
        row = job["lines"][0]
        attempts.append((row["text"], row["start_take"], row["takes"]))
        for k in range(row["start_take"], row["takes"]):
            sf.write(out / f"s02_t{k}.wav", np.zeros(160, np.float32), 16000)
    import types
    read = types.SimpleNamespace(generate=types.MethodType(bounded_generation(generate, lambda text, a: "changed"), object()))
    monkeypatch.setattr(lines, "generate_phonon", read.generate)
    job = {"voice": "Easton", "lines": [{"id": "s02", "text": "scripted", "takes": 6}], "takes": 6}
    for resume in range(2):
        files = lines.next_batch(job, "phonon", tmp_path, 0, 30, 6, print)
        assert [f.stem for f in files] == [f"s02_t{k}" for k in range(78, 84)]
    assert attempts == [("changed", 78, 84)]
    assert not lines.next_batch(job, "phonon", tmp_path, 0, 96, 6, print)


def test_no_second_joined_listen_when_exhausted_audio_did_not_change(monkeypatch, tmp_path):
    import numpy as np
    import spoken
    monkeypatch.setattr(lines, "INPROC_ROUNDS", 1)
    monkeypatch.setattr(spoken, "spoken_form", lambda heard, text, names: {"ok": False})
    listens = []
    def hear(x, sr):
        listens.append(1)
        return "one bad", []
    narrator = types.SimpleNamespace(listen_long=hear, listen=lambda x, sr: ("one bad", []), log=print)
    row = {"file": "s00_t0.wav", "errs": 1, "voice": (None, None)}
    lines.gate_repair(narrator, "phonon", {}, tmp_path, {"segments": [{"text": "one word"}]},
                      {}, [], 16000, [np.zeros(16000)], [row["voice"]], [row], [[row]], [{}])
    assert listens == [1]
