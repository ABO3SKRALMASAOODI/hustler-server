"""The browser may make the proxy — but it does not get to be believed.

This is the standard proxy workflow (Premiere generates proxies on ingest and
swaps originals back for export; Descript builds its optimized assets on the
user's device; Frame.io uploads proxies before camera originals so editing can
start immediately). What every one of those has, and what these tests pin, is
the RELINK CHECK: a proxy is only usable as a stand-in for as long as it still
agrees with the original it stands in for.

Ours matters more than most, because the index built from that proxy is pure
timestamps — every cut, caption and zoom is an offset into a video we have not
looked at yet.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import indexer                                          # noqa: E402


class _DB:
    """Runs callables inline and records enqueued jobs."""

    def __init__(self, proxy_meta):
        self.enqueued = []
        self.updated = []
        self._proxy = {"meta": proxy_meta}

    def run(self, fn, *a, **kw):
        import db as dbx
        if fn is dbx.update_asset_probe:
            self.updated.append(a)
            return None
        if fn is dbx.latest_asset:
            return self._proxy
        if fn is dbx.enqueue_job:
            self.enqueued.append(a)
            return 999
        raise AssertionError(f"unexpected db call {fn}")


def _run(monkeypatch, claimed_s, actual_s, w=3840, h=2160, fps=30.0):
    monkeypatch.setattr(indexer.storage, "presign_get",
                        lambda key, expires=3600: "https://example/x.mp4")
    monkeypatch.setattr(indexer.media, "probe", lambda p: {
        "duration": actual_s, "width": w, "height": h, "fps": fps,
        "video_duration": actual_s, "has_audio": True, "vfr": False})
    db = _DB({"source_duration_s": claimed_s} if claimed_s else {})
    job = {"id": 1, "user_id": 7, "payload": {"verify_original": True}}
    asset = {"id": 42, "storage_key": "originals/1/x.mp4", "sha256": None}
    return db, indexer._verify_client_proxy(db, job, asset, 1)


def test_a_matching_original_verifies_and_reindexes_nothing(monkeypatch):
    db, res = _run(monkeypatch, claimed_s=355.0, actual_s=355.0)
    assert res["verified"] is True
    assert db.enqueued == []


def test_rounding_drift_is_tolerated(monkeypatch):
    """The browser measures duration from frames it decoded; the container
    reports its own. They disagree by fractions routinely and that is not a
    different video."""
    db, res = _run(monkeypatch, claimed_s=355.0, actual_s=355.2)
    assert res["verified"] is True
    assert db.enqueued == []


def test_a_different_length_original_forces_a_real_reindex(monkeypatch):
    """The failure this exists to catch: the index is a set of timestamps into
    a video, and the video turned out to be a different length. Every cut in it
    is now suspect, so it is rebuilt from the original — slow, and correct."""
    db, res = _run(monkeypatch, claimed_s=355.0, actual_s=402.0)
    assert res["verified"] is False
    assert len(db.enqueued) == 1
    project_id, user_id, jtype, payload = db.enqueued[0]
    assert jtype == "index"
    assert payload["asset_id"] == 42
    assert payload.get("reindex") is True
    assert "client_proxy" not in payload      # the slow path, from the original


def test_a_claim_that_never_arrived_is_not_treated_as_agreement(monkeypatch):
    """Missing metadata must fail CLOSED here. An absent claim compared against
    anything is trivially 'no drift', which would silently bless an index built
    on numbers nobody ever checked."""
    db, res = _run(monkeypatch, claimed_s=None, actual_s=355.0)
    assert res["verified"] is False
    assert len(db.enqueued) == 1


def test_the_originals_true_shape_is_written_back(monkeypatch):
    """The index was built describing the source with the browser's numbers.
    Whatever happens to verification, the asset row must end up holding what
    the FILE says — it is what the renderer reads for the export frame."""
    db, res = _run(monkeypatch, claimed_s=355.0, actual_s=355.0,
                   w=3840, h=2160, fps=29.97)
    assert db.updated, "the original's probe must be written back"
    _asset_id, dur, w, h, fps, _sha = db.updated[0]
    assert (w, h) == (3840, 2160)
    assert fps == 29.97
    assert dur == 355.0
    assert res["width"] == 3840 and res["height"] == 2160


def test_tolerance_scales_with_length(monkeypatch):
    """A half-second on a 6-second clip is a real difference; on a 3-hour
    recording it is nothing. Fixed-only tolerance would re-index every long
    upload."""
    db, res = _run(monkeypatch, claimed_s=10800.0, actual_s=10830.0)
    assert res["verified"] is True
    db, res = _run(monkeypatch, claimed_s=6.0, actual_s=8.0)
    assert res["verified"] is False
