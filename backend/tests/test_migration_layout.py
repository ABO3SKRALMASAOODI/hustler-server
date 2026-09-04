"""Schema ownership is centralized and absent from web-process startup."""

from pathlib import Path
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from apply_migrations import is_local_database_url, main  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "backend" / "migrations"


def test_all_historical_migrations_are_in_the_canonical_directory():
    names = sorted(path.name for path in MIGRATIONS.glob("*.sql"))
    numbers = {int(name.split("_", 1)[0]) for name in names}

    assert numbers == set(range(27))
    assert not list((ROOT / "migrations").glob("*.sql"))
    assert "013_index_greet_unique.sql" in names
    assert "018_preview_check.sql" in names


def test_web_startup_does_not_create_or_migrate_schema():
    app_source = (ROOT / "backend" / "app.py").read_text(encoding="utf-8")
    model_source = (ROOT / "backend" / "models.py").read_text(
        encoding="utf-8")
    runner_source = (ROOT / "backend" / "apply_migrations.py").read_text(
        encoding="utf-8")

    assert "init_db" not in app_source
    assert "def init_db" not in model_source
    assert "from app import create_app" not in runner_source
    assert 'os.path.join(HERE, "migrations")' in runner_source

    integration_source = (ROOT / "scripts" / "integration_test.py").read_text(
        encoding="utf-8")
    assert integration_source.index("apply_migrations()") \
        < integration_source.index("app = create_app()")


def test_relocated_constraint_migration_is_replay_safe():
    source = (MIGRATIONS / "015_shorts_mode.sql").read_text(encoding="utf-8")

    assert "DROP CONSTRAINT IF EXISTS video_jobs_type_check" in source


@pytest.mark.parametrize("dsn", (
    "postgresql://valmera:valmera@postgres:5432/valmera",
    "postgresql://user:valmera@localhost:5432/test",
    "postgresql://user:valmera@127.0.0.1:5432/test",
    "postgresql://user:valmera@[::1]:5432/test",
))
def test_migration_runner_recognizes_only_local_database_hosts(dsn):
    assert is_local_database_url(dsn)


def test_migration_runner_refuses_remote_database_before_connecting(
        monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql" + "://redacted:redacted@db.example.com/prod")
    called = False

    def forbidden_connect(_dsn):
        nonlocal called
        called = True
        raise AssertionError("remote connection should not be attempted")

    monkeypatch.setattr("apply_migrations.psycopg2.connect", forbidden_connect)
    with pytest.raises(RuntimeError, match="refusing to replay"):
        main()
    assert not called
