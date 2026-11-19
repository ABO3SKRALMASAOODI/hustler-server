"""The workflows' music step chooses from the reference music set first, on every route (18 Nov 2026)."""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import chain


def _set(tmp_path):
    root = tmp_path / "music_ref"
    root.mkdir()
    (root / "index.json").write_text(json.dumps({"tracks": []}))
    return str(root)


def test_points_choosers_at_the_set_when_its_index_exists(tmp_path, monkeypatch):
    monkeypatch.delenv(chain.SET_ENV, raising=False)
    monkeypatch.setattr(chain, "MUSIC_SET", _set(tmp_path))
    assert chain.use_music_set() == chain.MUSIC_SET
    assert os.environ[chain.SET_ENV] == chain.MUSIC_SET
    # a child process (the SHELL_MUSIC_CMD adapter, the long form's music_chapters.py) inherits it
    out = subprocess.run([sys.executable, "-c", f"import os; print(os.environ.get('{chain.SET_ENV}'))"],
                         capture_output=True, text=True).stdout.strip()
    assert out == chain.MUSIC_SET


def test_an_explicit_setting_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(chain, "MUSIC_SET", _set(tmp_path))
    monkeypatch.setenv(chain.SET_ENV, "/elsewhere/set")
    assert chain.use_music_set() == "/elsewhere/set"
    assert os.environ[chain.SET_ENV] == "/elsewhere/set"


def test_without_the_set_nothing_changes(tmp_path, monkeypatch):
    monkeypatch.delenv(chain.SET_ENV, raising=False)
    monkeypatch.setattr(chain, "MUSIC_SET", str(tmp_path / "missing"))
    assert chain.use_music_set() is None
    assert chain.SET_ENV not in os.environ


def test_music_version_sets_it_before_choosing(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.delenv(chain.SET_ENV, raising=False)
    monkeypatch.setattr(chain, "MUSIC_SET", _set(tmp_path))
    seen = {}

    def fake_sh(ctx, cmd, **kw):
        seen["env"] = os.environ.get(chain.SET_ENV)
        raise RuntimeError("stop after the chooser starts")
    monkeypatch.setattr(chain, "sh", fake_sh)
    monkeypatch.setattr(chain.brand, "worker_dir", lambda: str(tmp_path))
    monkeypatch.setenv("SHELL_MUSIC_CMD", str(tmp_path / "adapter.sh"))
    ctx = SimpleNamespace(run_dir=str(tmp_path), supply=str(tmp_path), state_root=str(tmp_path), run_id="t",
                          workflow="wf3", args={}, log=lambda *a, **k: None)
    try:
        chain.music_version(ctx, {"film": str(tmp_path / "film.mp4")}, {"l1": str(tmp_path / "l1.json")})
    except RuntimeError:
        pass
    assert seen.get("env") == chain.MUSIC_SET


def test_the_brief_says_who_the_film_is_about(tmp_path, monkeypatch):
    """owner, 20 Nov: the music is chosen for the story and the person (a Mark Cuban short is not a club night)."""
    from types import SimpleNamespace
    monkeypatch.setattr(chain, "MUSIC_SET", _set(tmp_path))
    (tmp_path / "script.json").write_text(json.dumps({"title": "$285M team sold for $4.3B", "hook_line": "it went from basketball to valuations",
                                                       "direction": "reflective, then a stunning reveal",
                                                       "names": ["Mark Cuban", {"name": "Dallas Mavericks"}]}))

    def fake_sh(ctx, cmd, **kw):
        raise RuntimeError("stop after the brief is written")
    monkeypatch.setattr(chain, "sh", fake_sh)
    monkeypatch.setattr(chain.brand, "worker_dir", lambda: str(tmp_path))
    monkeypatch.delenv("SHELL_MUSIC_CMD", raising=False)
    ctx = SimpleNamespace(run_dir=str(tmp_path), supply=str(tmp_path), state_root=str(tmp_path), run_id="t",
                          workflow="wf1", args={}, log=lambda *a, **k: None)
    try:
        chain.music_version(ctx, {"film": str(tmp_path / "film.mp4")}, {"l1": str(tmp_path / "l1.json")})
    except RuntimeError:
        pass
    brief = (tmp_path / "music_brief.txt").read_text()
    assert brief.startswith("About: Mark Cuban, Dallas Mavericks.") and "stunning reveal" in brief


def _tiny_film(path, seconds=3.0):
    import brand
    subprocess.run([brand.FF, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=gray:s=108x192:d={seconds}:r=30", "-f", "lavfi",
                    "-i", f"sine=frequency=220:duration={seconds}:sample_rate=48000", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(path)], check=True)


def test_a_delivered_films_music_is_chosen_again_into_a_new_folder_only(tmp_path, monkeypatch):
    import hashlib
    import shutil
    import pytest
    import brand
    import rescore_music as R
    wk = brand.worker_dir()
    sys.path.insert(0, wk)
    import music_film as MF
    from score import music as MU
    src = tmp_path / "delivered"
    src.mkdir()
    _tiny_film(src / "film_clean.mp4")
    shutil.copy2(src / "film_clean.mp4", src / "film.mp4")
    doc = {"workflow": "wf1", "script": {"title": "t", "names": ["Mark Cuban"]}, "checks": {"finish": {"programme_s": 2.0},
           "stories": {"pictures": [{"f": [0, 30]}, {"f": [30, 60]}]}},
           "music": {"title": "Night Terrors", "cid": "mk-334", "does": {"marks": {"turn_s": 0.8, "payoff_s": 1.5, "mood": "reflective, triumphant"}}}}
    (src / "film.json").write_text(json.dumps(doc))
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in src.iterdir()}
    with pytest.raises(SystemExit):                                   # never into the delivered folder
        R.main([str(src), str(src / "again"), "--no-model"])
    out = tmp_path / "copy"
    out.mkdir()
    (out / "rescore_heard.json").write_text(json.dumps({"words": [[0.1, 0.5], [0.6, 1.0]], "lines": [[0.1, "we sold it"]]}))
    seen = {}

    def choose(film, marks, cache, used=(), call=None, profile=None):
        seen.update(film=film, marks=marks, used=used)
        return {"cid": "pb-1", "title": "Rise", "author": "A", "license": "Pixabay Content License", "credit": "\"Rise\" by A (Pixabay)",
                "chosen_by": "model (story)", "why": "a reflective story", "sung": False, "entry": {"point": None}}, str(src / "film_clean.mp4"), [{"cid": "pb-1"}]

    def mix_film(film, song, marks, out=None, **kw):
        shutil.copy2(film, out)                                      # the engine's mux keeps the picture; here: the same file
        return {"out": out, "plan": {"ok": True, "drop_at": 1.5}, "music": {"under_voice_db": 6.0}}
    monkeypatch.setattr(MF, "choose", choose)
    monkeypatch.setattr(MU, "mix_film", mix_film)
    rep = R.main([str(src), str(out), "--no-model", "--used", "mk-334"])
    assert seen["film"]["lines"] == [(0.1, "we sold it")] and seen["film"]["kind"] == "podcast" and "Mark Cuban" in seen["film"]["brief"]
    assert seen["marks"]["payoff_s"] == 1.5 and seen["marks"]["turn_drop"] and seen["marks"]["cuts"] == [1.0] and seen["used"] == ["mk-334"]
    assert rep["checks"]["picture_identical"] and (out / "film.mp4").exists() and (out / "film_clean.mp4").exists()
    new = json.loads((out / "film.json").read_text())
    assert new["music"]["title"] == "Rise" and new["music"]["rescored"]["previous"]["title"] == "Night Terrors"
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in src.iterdir()} == before      # the delivered folder is untouched
