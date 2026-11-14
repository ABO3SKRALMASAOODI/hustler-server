import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import chain


def test_one_command_story_retains_checkpoint_when_required_music_is_missing(tmp_path, monkeypatch):
    (tmp_path / "final.mp4").write_bytes(b"clean export")
    (tmp_path / "music.json").write_text(json.dumps({"ok": False, "why": "library unavailable", "tried": []}))
    ctx = SimpleNamespace(run_dir=str(tmp_path), supply=str(tmp_path), tmp=str(tmp_path),
                          workflow="wf3", args={"one_command": True})
    monkeypatch.setattr(chain.brand, "check", lambda *args: {"ok": True})
    monkeypatch.setattr(chain, "music_version", lambda *args: None)
    monkeypatch.setattr(chain, "wants_music", lambda *args: True)
    with pytest.raises(RuntimeError, match="required story music.*library unavailable"):
        chain.finish_and_check(ctx, {"film": "engine.mp4"}, {})
    assert (tmp_path / "final.mp4").exists()


def test_successful_music_delivery_is_returned(tmp_path, monkeypatch):
    (tmp_path / "final.mp4").write_bytes(b"clean export")
    ctx = SimpleNamespace(run_dir=str(tmp_path), supply=str(tmp_path), tmp=str(tmp_path),
                          workflow="wf3", args={"one_command": True})
    monkeypatch.setattr(chain.brand, "check", lambda *args: {"ok": True})
    monkeypatch.setattr(chain, "music_version", lambda *args: {"final_music": "music.mp4"})
    assert chain.finish_and_check(ctx, {"film": "engine.mp4"}, {})["final_music"] == "music.mp4"


def test_footage_passes_the_faster_pace_only_when_asked(tmp_path, monkeypatch):
    seen = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        out = cmd[cmd.index("--out") + 1]
        Path(out, "footage.json").write_text(json.dumps({"lines": []}))
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(chain.subprocess, "run", fake_run)
    monkeypatch.setenv("VALMERA_FOOTAGE", "1")
    ctx = SimpleNamespace(run_dir=str(tmp_path), supply=str(tmp_path), tmp=str(tmp_path), workflow="wf3", run_id="r",
                          log=lambda m: None, log_raw=lambda m: None)
    chain.footage(ctx, {"segments": []}, [1.0])
    plain = seen[-1]
    assert not any(f in plain for f in ("--shot-s", "--line-max", "--per", "--lines", "--avoid"))   # WF1 and WF2: as before
    chain.footage(ctx, {"segments": []}, [1.0], shot_s=0.45, line_max=10, per=5, out_name="footage2", lines=[3, 7],
                  avoid=[str(tmp_path / "footage" / "footage.json")])
    c = seen[-1]
    assert c[c.index("--shot-s") + 1] == "0.45" and c[c.index("--line-max") + 1] == "10" and c[c.index("--per") + 1] == "5"
    assert c[c.index("--lines") + 1] == "3,7" and c[c.index("--out") + 1].endswith("footage2") and "--avoid" in c


def test_story_joints_from_the_beats_and_a_real_voice(tmp_path):
    sys.path[:0] = [str(Path(__file__).parent.parent / "valmera-stories")]
    import wf3
    sc = {"segments": [{"say": "a b", "beat": "hook"}, {"say": "c d", "beat": "setup"}, {"say": "e f", "beat": "turn"},
                       {"say": "g h", "beat": "payoff"}, {"say": "i j", "beat": "close"}]}
    words, t = [], 0.0
    for i, s in enumerate(sc["segments"]):
        for w in s["say"].split():
            words.append({"w": w, "start": t, "end": t + 0.3, "seg": i})
            t += 0.35
        t += 0.9
    pack = {"words": {"words": words, "duration": t}, "voices": [{"start": 2.0, "end": 3.5, "person": "X"}]}
    j = wf3.joints_of(sc, pack)
    j = [x for x in j if x["kind"] != "swell"]          # round 3: a hole's exit may earn a swell in the narrator's next gap
    assert [x["kind"] for x in j] == ["turn", "payoff", "close", "voice"]
    for x in j[:3]:
        assert 0.5 <= x["next_first_word_t"] - x["last_word_t"] <= 2.3 and x["t"] == x["next_first_word_t"]
    assert j[3]["t1"] - j[3]["t0"] == 1.5
