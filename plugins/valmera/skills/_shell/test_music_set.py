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
