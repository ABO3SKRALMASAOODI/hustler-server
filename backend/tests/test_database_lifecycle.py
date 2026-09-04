"""Request-scoped database connections have an explicit end of life."""

import os
import sys

from flask import Flask

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import models  # noqa: E402


def test_cached_connection_is_rolled_back_and_closed_at_context_end(
        monkeypatch):
    class Connection:
        rolled_back = 0
        closed = 0

        def rollback(self):
            self.rolled_back += 1

        def close(self):
            self.closed += 1

    conn = Connection()
    monkeypatch.setattr(models.psycopg2, "connect", lambda _url: conn)

    app = Flask(__name__)
    app.config["DATABASE_URL"] = "postgresql://stub/stub"
    app.teardown_appcontext(models.close_db)
    with app.app_context():
        assert models.get_db() is conn
        assert models.get_db() is conn
        assert conn.closed == 0

    assert conn.rolled_back == 1
    assert conn.closed == 1
