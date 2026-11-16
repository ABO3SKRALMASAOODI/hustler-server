"""A first-film deadline must not delete the engine's still-open sources."""
import threading
import time
from types import SimpleNamespace as NS

import pytest

from score import film


@pytest.fixture
def engine(monkeypatch, tmp_path):
    import broll_source
    from footage import seam, switch

    entered, release = threading.Event(), threading.Event()
    work = tmp_path / "film"
    work.mkdir()
    source = work / "source.mp4"
    source.write_bytes(b"source frames")
    checked, shelved = [], []
    monkeypatch.setattr(switch, "allowed", lambda *a: True)
    monkeypatch.setattr(seam, "customer_cfg", lambda: {})
    monkeypatch.setattr(broll_source, "carried", lambda fn: fn)
    monkeypatch.setattr(seam, "name_for_footage", lambda *a: ([[{"name": "subject"}]], 0))
    monkeypatch.setattr(seam, "shelved", lambda *a: ([], []))

    def offers(*a, **kw):
        entered.set()
        assert release.wait(5), "test did not release its reader"
        checked.append(source.read_bytes())
        return [{"kind": "video", "path": str(source)}]

    def shelve(rows, *a):
        shelved.append(source.read_bytes())
        return len(rows)

    monkeypatch.setattr(seam, "bin_offers", offers)
    monkeypatch.setattr(seam, "shelve", shelve)
    box = film.footage_start({"user_id": 1}, str(work), {"wishes": [{"what": "subject"}]}, {})
    assert box and entered.wait(2)
    try:
        yield NS(work=work, source=source, box=box, release=release, checked=checked, shelved=shelved)
    finally:
        release.set()
        box["thread"].join(5)
        assert not box["thread"].is_alive()


@pytest.mark.parametrize("reason", ["deadline", "close", "discard"])
def test_pending_reader_survives_consumer_release(engine, reason):
    e = engine
    state = {"work": str(e.work), "footage": e.box}
    ctx = NS(director_upgrade=state, director_state=state, director_version=None)
    if reason == "deadline":
        start = time.monotonic() - film.UPGRADE_BY_S - 1
        before = time.monotonic()
        assert film.upgrade(ctx, None, {}, start) is None
        assert time.monotonic() - before < 1
        assert "not there" in ctx.first_cut_phases["director"]["footage_later"]["skipped"]
    elif reason == "close":
        film.close(ctx)
    else:
        film.discard_work(str(e.work), e.box)
    assert e.source.exists(), "deadline deleted an active reader's source"
    e.release.set()
    e.box["thread"].join(3)
    assert e.checked == e.shelved == [b"source frames"]
    assert "error" not in e.box
    assert not e.work.exists(), "the last reader must clean deferred scratch"


def test_finished_engine_keeps_files_until_consumer_is_done(engine):
    e = engine
    e.release.set()
    e.box["thread"].join(3)
    assert e.source.exists()
    film.discard_work(str(e.work), e.box)
    assert not e.work.exists()


def test_no_engine_cleanup_is_immediate(tmp_path):
    work = tmp_path / "film"
    work.mkdir()
    film.discard_work(str(work))
    assert not work.exists()


def test_failed_attempt_releases_pending_reader(monkeypatch, engine):
    e = engine
    ctx = NS(workdir=str(e.work.parent), latest_edl=lambda: {}, director_state=None)
    monkeypatch.setattr(film, "_room_today", lambda *a: True)
    monkeypatch.setattr(film, "_allowance", lambda *a: None)
    monkeypatch.setattr(film, "_event", lambda *a: None)

    def fail(*a):
        ctx.director_footage = e.box
        raise film.Fall("direct", "model unavailable")

    monkeypatch.setattr(film, "_make", fail)
    assert film._attempt(ctx, None, {}, time.monotonic(), {}, NS(spent_usd=0),
                         {"deadline_s": 100, "film_usd": 1}) is None
    assert e.source.exists()
    e.release.set()
    e.box["thread"].join(3)
    assert e.checked == e.shelved == [b"source frames"]
    assert not e.work.exists()
