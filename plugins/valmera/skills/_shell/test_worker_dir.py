"""brand.worker_dir(): a film started by hand builds on the same engines as the factory's (26 Nov ImportError)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import brand  # noqa: E402


def worker(root, current):
    w = root / "worker"
    for n in brand.ENGINE_FILES:
        (w / n).parent.mkdir(parents=True, exist_ok=True)
        (w / n).write_text("")
    (w / "scene" / "text_layout.py").write_text("def shared_source_credits(views):\n    return []\n" if current else "")
    return str(w)


def plugin_in(root, monkeypatch):
    here = root / "plugins" / "valmera" / "skills" / "_shell"
    here.mkdir(parents=True)
    monkeypatch.setattr(brand, "HERE", str(here))


def test_the_env_worker_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("VALMERA_WORKER", "/factory/worker")
    assert brand.worker_dir() == "/factory/worker"


def test_an_older_worker_beside_the_plugin_gives_way_to_the_synced_workflow_worker(tmp_path, monkeypatch):
    monkeypatch.delenv("VALMERA_WORKER", raising=False)
    checkout = tmp_path / "hustler-server"
    worker(checkout, current=False)
    plugin_in(checkout, monkeypatch)
    monkeypatch.setattr(brand, "V", str(tmp_path / "V"))
    synced = worker(tmp_path / "V" / "worktrees" / "skill-worker", current=True)
    assert brand.worker_dir() == synced


def test_a_current_worker_beside_the_plugin_is_used(tmp_path, monkeypatch):
    monkeypatch.delenv("VALMERA_WORKER", raising=False)
    checkout = tmp_path / "cr-worker"
    beside = worker(checkout, current=True)
    plugin_in(checkout, monkeypatch)
    monkeypatch.setattr(brand, "V", str(tmp_path / "V"))
    worker(tmp_path / "V" / "worktrees" / "skill-worker", current=True)
    assert brand.worker_dir() == beside


def test_without_a_synced_worker_the_one_beside_is_kept(tmp_path, monkeypatch):
    monkeypatch.delenv("VALMERA_WORKER", raising=False)
    checkout = tmp_path / "ci"
    beside = worker(checkout, current=False)
    plugin_in(checkout, monkeypatch)
    monkeypatch.setattr(brand, "V", str(tmp_path / "V"))
    assert brand.worker_dir() == beside


def test_with_no_worker_anywhere_the_synced_main_checkout_is_named(tmp_path, monkeypatch):
    monkeypatch.delenv("VALMERA_WORKER", raising=False)
    plugin_in(tmp_path / "cache", monkeypatch)
    monkeypatch.setattr(brand, "V", str(tmp_path / "V"))
    assert brand.worker_dir() == f"{tmp_path}/V/worktrees/skill-main/worker"
