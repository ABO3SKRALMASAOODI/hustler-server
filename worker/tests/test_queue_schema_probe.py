"""Metadata checks must never disable ownership during concurrent startup."""
from concurrent.futures import ThreadPoolExecutor
import threading

import psycopg2
import pytest
import db


class ProbeConnection:
    def __init__(self, row, entered=None, release=None, error=None):
        self.row, self.entered, self.release, self.error = row, entered, release, error
        self.rolled_back = False

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, *_args):
        if self.entered:
            self.entered.set()
            assert self.release.wait(5), "probe was not released"
        if self.error:
            raise self.error

    def fetchone(self):
        return self.row

    def rollback(self):
        self.rolled_back = True


@pytest.mark.parametrize("probe,cache,row", [
    (db.claims_column_ready, "_CLAIMS_COL", {"n": 1}),
    (db.remote_executions_table_ready, "_REMOTE_EXEC_TABLE", {"name": "remote_executions"}),
])
def test_inflight_probe_does_not_look_like_missing_schema(monkeypatch, probe, cache, row):
    monkeypatch.setattr(db, cache, {"ok": False, "checked_at": 0.0})
    entered, release = threading.Event(), threading.Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(probe, ProbeConnection(row, entered, release))
        try:
            assert entered.wait(5)
            # Another queue thread has its own DB connection. It must observe
            # the real schema, not a cached False from the unfinished probe.
            assert probe(ProbeConnection(row)) is True
        finally:
            release.set()
        assert first.result(timeout=5) is True


@pytest.mark.parametrize("probe,cache,row", [
    (db.claims_column_ready, "_CLAIMS_COL", {"n": 1}),
    (db.remote_executions_table_ready, "_REMOTE_EXEC_TABLE", {"name": "remote_executions"}),
])
def test_probe_failure_does_not_authorize_an_unfenced_claim(monkeypatch, probe, cache, row):
    monkeypatch.setattr(db, cache, {"ok": False, "checked_at": 0.0})
    failed = ProbeConnection(row, error=psycopg2.OperationalError("connection lost"))
    with pytest.raises(psycopg2.OperationalError):
        probe(failed)
    assert failed.rolled_back
    # Recovery must be visible immediately, not after the missing-schema TTL.
    assert probe(ProbeConnection(row)) is True
