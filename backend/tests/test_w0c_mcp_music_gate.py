"""Owner rule 1 on the MCP batch path (Wave 0, track 0C).

An MCP ``apply_edit_batch`` / ``apply_short_edit_batches`` is written by the
backend (apply_edit_batch_core), not by the worker's ToolContext, so it runs
the same pure music gate (worker/music_gate.py, loaded by path): a track new
to the edit takes its provenance from its project asset, never from what the
batch claims, and a library track the user never picked is refused.

    cd backend && python -m pytest tests -q
"""

import contextlib
import copy
import json
import os
import sys

import pytest

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes import video                                   # noqa: E402

UPLOAD = "music/7/upload-song.mp3"
LIBRARY = "music/7/library-cinematic-after-midnight.mp3"
SFX = "sfx/7/abc123.mp3"
STOCK_AUDIO = "music/7/stockaudio.m4a"
ASSETS = {
    "clips/7/stock.mp4": {"storage_key": "clips/7/stock.mp4",
                          "kind": "video_clip", "duration_s": 8,
                          "meta": {"stock": True, "provider": "pexels"}},
    STOCK_AUDIO: {"storage_key": STOCK_AUDIO, "kind": "music",
                  "duration_s": 8,
                  "meta": {"extracted_from_video": True,
                           "from_asset_key": "clips/7/stock.mp4"}},
    UPLOAD: {"storage_key": UPLOAD, "kind": "music", "duration_s": 20,
             "meta": {"filename": "song.mp3"}},
    LIBRARY: {"storage_key": LIBRARY, "kind": "music", "duration_s": 20,
              "meta": {"source": "valmera-music-library",
                       "library_slug": "cinematic-after-midnight"}},
    SFX: {"storage_key": SFX, "kind": "music", "duration_s": 1,
          "meta": {"caption": "found online by search_sfx"}},
}


@pytest.fixture
def api(monkeypatch):
    class Store:
        def __init__(self):
            self.edls = {1: video.wschemas.default_edl(20)}
            self.messages, self.queries = [], []
            self.picks = None
            self.history = set()

        def cursor(self):
            return self

        def execute(self, sql, args=()):
            self.one, self.many = None, []
            q = " ".join(sql.split())
            self.queries.append(q)
            if "pg_advisory_xact_lock" in q:
                return
            if q.startswith("SELECT id FROM video_jobs"):
                self.one = None
            elif q.startswith("SELECT storage_key,duration_s FROM assets"):
                self.many = [{"storage_key": k, "duration_s": a["duration_s"]}
                             for k, a in ASSETS.items()]
            elif q.startswith("SELECT storage_key, kind, meta FROM assets"):
                self.many = [copy.deepcopy(ASSETS[k]) for k in args[1]
                             if k in ASSETS]
            elif q.startswith("SELECT to_regclass('public.app_kv')"):
                self.one = {"t": "app_kv"}
            elif q.startswith("SELECT value FROM app_kv"):
                assert args == ("music_picks:2",)
                self.one = ({"value": json.dumps(self.picks)}
                            if self.picks is not None else None)
            elif q.startswith("SELECT 1 FROM edls"):
                key = json.loads(args[1])["music"][0]["storage_key"]
                self.one = {"?column?": 1} if key in self.history else None
            elif q.startswith("INSERT INTO edls"):
                _, version, edl, _ = args
                self.edls[version] = copy.deepcopy(edl.adapted)
                self.one = {"version": version}
            elif q.startswith("INSERT INTO chat_messages"):
                self.messages.append(copy.deepcopy(args[-1].adapted))
            elif q.startswith("UPDATE video_jobs SET state="):
                pass
            else:
                raise AssertionError(q)

        def fetchone(self):
            return self.one

        def fetchall(self):
            return self.many

    db = Store()

    @contextlib.contextmanager
    def connect():
        yield db
    monkeypatch.setattr(video, "vdb", connect)
    monkeypatch.setattr(video, "_project_for_user",
                        lambda *_: dict(chat_session_id=12))
    monkeypatch.setattr(video, "_subscribe_gate_applies", lambda *_: False)
    monkeypatch.setattr(video, "_latest_edl", lambda *_: dict(
        version=max(db.edls), json=db.edls[max(db.edls)]))
    monkeypatch.setattr(video, "_active_original",
                        lambda *_: dict(duration_s=20))
    monkeypatch.setattr(video, "read_receipt", lambda *_a: None)
    return db


def _batch(*items, base=1, op="m1"):
    return dict(base_version=base, operation_id=f"music-gate-{op}-0123456789",
                operations=[dict(action="upsert", layer="music", id=i["id"],
                                 value=i) for i in items])


def _item(key, iid="m1", **extra):
    return dict(id=iid, storage_key=key, start=0.0, end=10.0, gain_db=-18.0,
                **extra)


def test_an_mcp_batch_stamps_the_users_upload(api):
    result, status = video.apply_edit_batch_core(
        1, 2, _batch(_item(UPLOAD)), origin="mcp")
    assert status == 200, result
    assert api.edls[2]["music"][0]["provenance"] == "user_upload"


def test_an_mcp_batch_cannot_place_a_library_track_nobody_picked(api):
    result, status = video.apply_edit_batch_core(
        1, 2, _batch(_item(LIBRARY, provenance="user_picked_library")),
        origin="mcp")
    assert status == 400
    assert "agents never choose music" in result["error"]
    assert "add_library_music" in result["error"]
    assert list(api.edls) == [1] and not api.messages


def test_the_users_recorded_pick_places_it(api):
    api.picks = {"cinematic-after-midnight": {"slug":
                                              "cinematic-after-midnight"}}
    result, status = video.apply_edit_batch_core(
        1, 2, _batch(_item(LIBRARY)), origin="mcp")
    assert status == 200, result
    assert api.edls[2]["music"][0]["provenance"] == "user_picked_library"


def test_a_track_from_the_edit_history_is_not_a_new_choice(api):
    api.history.add(LIBRARY)
    result, status = video.apply_edit_batch_core(
        1, 2, _batch(_item(LIBRARY)), origin="mcp")
    assert status == 200, result
    assert api.edls[2]["music"][0].get("provenance") is None   # legacy


def test_a_web_sound_is_refused_as_music(api):
    result, status = video.apply_edit_batch_core(
        1, 2, _batch(_item(SFX, provenance="user_upload")), origin="mcp")
    assert status == 400 and "sound effect" in result["error"]


def test_existing_tracks_keep_their_provenance_whatever_the_batch_claims(api):
    edl = copy.deepcopy(api.edls[1])
    edl["music"] = [_item(LIBRARY)]                      # legacy, no pick
    api.edls[1] = video.wschemas.validate_edl(edl, 20).model_dump()
    result, status = video.apply_edit_batch_core(
        1, 2, _batch(dict(id="m1", gain_db=-20.0,
                          provenance="user_picked_library")), origin="mcp")
    assert status == 200, result
    assert api.edls[2]["music"][0]["gain_db"] == -20.0
    assert api.edls[2]["music"][0].get("provenance") is None
    assert not any("FROM assets WHERE project_id=%s AND" in q
                   for q in api.queries)


def test_the_users_own_studio_batches_are_not_gated(api):
    result, status = video.apply_edit_batch_core(
        1, 2, _batch(_item(LIBRARY)), origin="user")
    assert status == 200, result
    assert not any(q.startswith("SELECT storage_key, kind, meta")
                   for q in api.queries)


def test_the_sound_of_a_stock_clip_is_not_the_users_music(api):
    result, status = video.apply_edit_batch_core(
        1, 2, _batch(_item(STOCK_AUDIO)), origin="mcp")
    assert status == 400 and "agent found" in result["error"]
