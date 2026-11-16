"""A Director cut must see the same acoustic refusal as the final writer."""
import copy
import json

import numpy as np

import cut_audio
import word_edges
from score import compiler, source_audio, speech_edges


def recording():
    index = {"first_film": {"frame": "9:16"},
             "video": {"duration": 3., "fps": 30, "width": 1080, "height": 1920},
             "words": [{"w": "previous", "t0": .3, "t1": 1.},
                       {"w": "founder", "t0": 1., "t1": 1.6}]}
    audio = np.full(3 * cut_audio.SR, .0001, dtype=np.float32)
    audio[int(.3 * cut_audio.SR):int(.84 * cut_audio.SR)] = .06
    audio[int(.91 * cut_audio.SR):int(1.6 * cut_audio.SR)] = .06
    return index, audio


def test_brief_exposes_measured_onset_without_changing_words_or_sound():
    index, audio = recording()
    original, original_audio = copy.deepcopy(index), audio.copy()
    facts = speech_edges.at_words(index, audio)
    onset = next(f for f in facts if f["side"] == "start" and f["word_id"] == 1)
    assert onset["b"] == 1 and .84 < onset["t"] < .91
    assert "w1 start 1.000s" in "\n".join(speech_edges.brief_lines(facts))
    assert index == original and np.array_equal(audio, original_audio)
    # The measurement is not an exception to the whole-word guard.
    assert word_edges.introduced([], [[onset["t"], 1.7]], index["words"], 3)


def test_continuous_sound_and_silence_do_not_invent_a_safe_word_boundary():
    index, audio = recording()
    assert speech_edges.at_words(index, np.full_like(audio, .06)) == []
    assert speech_edges.at_words(index, np.zeros_like(audio)) == []


def test_selected_reordered_runs_keep_the_acoustic_finding():
    index, audio = recording()
    spine = [{"src": [2., 2.5]}, {"src": [1., 1.7]}]
    facts = speech_edges.selected(index, audio, spine)
    assert any(f["side"] == "start" and f["b"] == 1 for f in facts)
    assert not speech_edges.selected(index, audio, [{"src": [.2, 1.7]}])


def test_compiler_gives_acoustic_refusal_to_existing_repair_round(tmp_path, monkeypatch):
    index, audio = recording()
    (tmp_path / "source.mp4").write_bytes(b"fixture")
    monkeypatch.setattr(source_audio, "decode", lambda *_: audio)
    monkeypatch.setattr(compiler, "GOVERN", False)
    text = "\n".join(json.dumps(x) for x in [
        {"k": "film", "v": 1, "fps": 30, "frame": "9:16", "tail": .1, "drop": ["0"]},
        {"k": "b", "id": "b1", "w": "1", "pic": "main", "type": "1"},
    ])
    result = compiler.compile_score(text, index=index, work_dir=str(tmp_path))
    assert result.l1["spine"][0]["src"][0] == 1.
    assert any("speech boundary:" in f and "founder" in f for f in result.meter["findings"])
    assert "speech boundary:" in result.meter_text


def test_missing_source_is_not_synthesized(tmp_path):
    index, _audio = recording()
    assert speech_edges.in_film(str(tmp_path), index, [{"src": [1., 1.7]}]) == []
