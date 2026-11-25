import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import chain


@pytest.fixture
def finished_programme(tmp_path, monkeypatch):
    film = tmp_path / "engine.mp4"
    film.write_bytes(b"engine output")
    ctx = SimpleNamespace(run_dir=str(tmp_path), tmp=str(tmp_path))
    def finish(src, out, tmp, **kwargs):
        Path(out).write_bytes(b"finished export")
        return dict(programme_s=7.3, outro_s=5.0)
    monkeypatch.setattr(chain.brand, "finish", finish)
    chain.cached_finish(ctx, str(film), str(tmp_path / "final.mp4"), last_word_s=None, accent=False)
    return str(film)


def test_one_command_story_retains_checkpoint_when_required_music_is_missing(tmp_path, monkeypatch, finished_programme):
    (tmp_path / "music.json").write_text(json.dumps({"ok": False, "why": "library unavailable", "tried": []}))
    ctx = SimpleNamespace(run_dir=str(tmp_path), supply=str(tmp_path), tmp=str(tmp_path),
                          workflow="wf3", args={"one_command": True})
    monkeypatch.setattr(chain.brand, "check", lambda *args: {"ok": True, "programme_s": 7.3, "outro_s": 5.0})
    monkeypatch.setattr(chain, "music_version", lambda *args: None)
    monkeypatch.setattr(chain, "wants_music", lambda *args: True)
    with pytest.raises(RuntimeError, match="required story music.*library unavailable"):
        chain.finish_and_check(ctx, {"film": finished_programme}, {})
    assert (tmp_path / "final.mp4").exists()


def test_successful_music_delivery_is_returned(tmp_path, monkeypatch, finished_programme):
    ctx = SimpleNamespace(run_dir=str(tmp_path), supply=str(tmp_path), tmp=str(tmp_path),
                          workflow="wf3", args={"one_command": True})
    monkeypatch.setattr(chain.brand, "check", lambda *args: {"ok": True, "programme_s": 7.3, "outro_s": 5.0})
    monkeypatch.setattr(chain, "music_version", lambda *args: {"final_music": "music.mp4"})
    assert chain.finish_and_check(ctx, {"film": finished_programme}, {})["final_music"] == "music.mp4"


def test_cached_finish_keeps_the_measured_programme_boundary(tmp_path, monkeypatch, finished_programme):
    ctx = SimpleNamespace(run_dir=str(tmp_path), supply=str(tmp_path), tmp=str(tmp_path), workflow="wf1", args={})
    monkeypatch.setattr(chain.brand, "check", lambda *args: {"ok": True, "programme_s": 31.167, "outro_s": 5.0})
    monkeypatch.setattr(chain.brand, "finish", lambda *args, **kw: pytest.fail("cached film must not be rendered again"))
    monkeypatch.setattr(chain, "music_version", lambda *args: None)
    result = chain.finish_and_check(ctx, {"film": finished_programme}, {})
    assert result["checks"]["finish"] == {"programme_s": 31.167, "outro_s": 5.0, "reused": True}


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


def test_podcast_resumes_music_with_the_current_picture_and_audio_score(tmp_path, monkeypatch, finished_programme):
    from types import SimpleNamespace
    ctx = SimpleNamespace(workflow="wf1", args={}, run_dir=str(tmp_path), tmp=str(tmp_path), log=lambda msg: None)
    monkeypatch.setattr(chain.brand, "check", lambda *args: dict(ok=True, programme_s=20, outro_s=5))
    attempts = []
    def music(ctx, render, score):
        attempts.append((render, score))
        if len(attempts) == 2:
            return dict(final_music="scored.mp4")
    monkeypatch.setattr(chain, "music_version", music)
    rd, sc = dict(film=finished_programme), dict(l1="scene.json", l1_audio="spine.json")
    assert chain.finish_and_check(ctx, rd, sc)["final_music"] == "scored.mp4"
    assert attempts == [(rd, sc), (rd, sc)]


def test_picture_repair_commits_score_receipt_and_both_finishes_together(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).parent.parent / "valmera-stories"))
    import wf3
    supply = tmp_path / "supply"
    supply.mkdir()
    ctx = SimpleNamespace(workflow="wf3", args={}, supply=str(supply), run_dir=str(tmp_path),
                          state_root=str(tmp_path), tmp=str(tmp_path), run_id="walkman", log=lambda _: None)
    film, scene = supply / "film_engine.mp4", supply / "l1.json"
    (tmp_path / "script.json").write_text("{}")
    scene.write_text("{}")
    rd, sc = {"film": str(film)}, {"l1": str(scene), "last_word_s": 67.44}
    film.write_bytes(b"first cut")
    mixes, finishes = [], []
    monkeypatch.setenv("SHELL_MUSIC_CMD", "adapter")
    def mix(ctx, cmd, **kwargs):
        args = json.loads(Path(cmd[-1]).read_text())
        picture = Path(args["film"]).read_bytes()
        cid = "pb-620576" if not mixes else "mk-595"
        mixes.append(picture)
        Path(args["out"]).write_bytes(picture + cid.encode())
        Path(args["report"]).write_text(json.dumps({"ok": True, "choice": {"cid": cid}}))

    def finish(src, dst, tmp, **kwargs):
        data = Path(src).read_bytes()
        finishes.append(data)
        Path(dst).write_bytes(data + b":brand")
        return {"programme_s": 68, "outro_s": 5}

    monkeypatch.setattr(chain, "sh", mix)
    monkeypatch.setattr(chain.brand, "finish", finish)
    monkeypatch.setattr(chain.brand, "check", lambda *a: dict(ok=True, length_ok=True, endcard_ok=True,
                                                               programme_s=68, outro_s=5))
    chain.finish_and_check(ctx, rd, sc)
    wf3.clear_render(ctx)
    film.write_bytes(b"repaired cut with figure")
    result = chain.finish_and_check(ctx, rd, sc)
    assert Path(result["final_music"]).read_bytes() == film.read_bytes() + b"mk-595:brand"
    assert result["music"]["cid"] == "mk-595"
    assert Path(result["final"]).read_bytes() == film.read_bytes() + b":brand"
    chain.finish_and_check(ctx, rd, sc)
    assert (len(mixes), len(finishes)) == (2, 4)

    # Crash after replacing the movie but before committing the corresponding receipt.
    (supply / "film_engine_music.mp4").write_bytes(b"torn replacement")
    chain.finish_and_check(ctx, rd, sc)
    assert mixes == [b"first cut", film.read_bytes(), film.read_bytes()]
    assert Path(result["final_music"]).read_bytes() == film.read_bytes() + b"mk-595:brand"


def test_resumed_delivery_recovers_the_authoritative_story_check(tmp_path, monkeypatch):
    import shell
    ctx = shell.Ctx(str(tmp_path / "run"), dict(run_id="walkman-copy", workflow="wf3",
                    args=dict(platform_song=False)), str(tmp_path))
    clean, wet = (Path(ctx.run_dir) / name for name in ("final.mp4", "final_music.mp4"))
    clean.write_bytes(b"new clean")
    wet.write_bytes(b"prior scored finish")
    rd, sc = dict(film=str(Path(ctx.supply) / "film_engine.mp4")), dict(l1=str(Path(ctx.supply) / "l1.json"))
    Path(ctx.supply).mkdir(exist_ok=True)
    Path(rd["film"]).write_bytes(b"new picture")
    Path(sc["l1"]).write_text("{}")
    old = dict(final=str(clean), final_music=str(wet), music=dict(cid="old"), checks={})
    results = dict(script=dict(style=dict(id="reel"), title="Walkman"), ideas={}, render=rd, score=sc, check=old)
    def refresh(ctx, render, score):
        assert render == rd and score == sc
        wet.write_bytes(b"new score with figure")
        return dict(old, music=dict(cid="mk-595"))
    monkeypatch.setattr(shell, "load_workflow", lambda _: SimpleNamespace(check=refresh))
    folder = Path(shell.deliver(ctx, {}, str(tmp_path / "scratch"), results)["folder"])
    assert (folder / "film.mp4").read_bytes() == b"new score with figure"
    assert json.loads((folder / "film.json").read_text())["music"]["cid"] == "mk-595"
