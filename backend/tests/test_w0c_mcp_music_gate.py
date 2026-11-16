"""Music provenance on the MCP batch path (Wave 0, track 0C; 16 Nov 2026).

An MCP ``apply_edit_batch`` / ``apply_short_edit_batches`` is written by the
backend (apply_edit_batch_core), not by the worker's ToolContext, so it runs
the same pure provenance core (worker/music_gate.py, loaded by path): a track
new to the edit takes its provenance from its project asset, never from what
the batch claims. Valmera may choose the music (owner, 13 Nov 2026): a track
nobody picked is stamped valmera_chosen, never refused. With web sounds off a
web-found sound plays the closest approved recording instead (a repair).

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
            self.assets = {}

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
            elif q.startswith("SELECT 1 FROM assets WHERE project_id=%s AND storage_key=%s"):
                self.one = {"?column?": 1} if args[1] in self.assets else None
            elif q.startswith("INSERT INTO assets"):
                self.assets[args[1]] = {"bytes": args[2], "duration_s": args[3],
                                        "meta": copy.deepcopy(args[4].adapted)}
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


def test_an_mcp_batch_places_a_library_track_nobody_picked_as_valmeras_choice(api):
    result, status = video.apply_edit_batch_core(
        1, 2, _batch(_item(LIBRARY, provenance="user_picked_library")),
        origin="mcp")
    assert status == 200, result
    assert api.edls[2]["music"][0]["provenance"] == "valmera_chosen"


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


def test_a_web_sound_placed_as_music_is_valmeras_choice(api):
    result, status = video.apply_edit_batch_core(
        1, 2, _batch(_item(SFX, provenance="user_upload")), origin="mcp")
    assert status == 200, result
    assert api.edls[2]["music"][0]["provenance"] == "valmera_chosen"


def test_only_a_key_that_is_no_project_audio_cannot_play(api):
    result, status = video.apply_edit_batch_core(
        1, 2, _batch(_item("music/7/nowhere.mp3")), origin="mcp")
    assert status == 400 and "not attached" in result["error"], result   # technical check


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


def test_the_sound_of_a_stock_clip_is_valmeras_choice(api):
    result, status = video.apply_edit_batch_core(
        1, 2, _batch(_item(STOCK_AUDIO)), origin="mcp")
    assert status == 200, result
    assert api.edls[2]["music"][0]["provenance"] == "valmera_chosen"


# ── owner rule 2 on the same path, as a repair (16 Nov 2026) ───────────
# With web sounds off, a sound an earlier search_sfx fetched into the
# project plays the closest approved recording instead (the worker's add_sfx
# and its own writes repair it the same way); a sound already in the edit
# keeps playing.

def _sfx_batch(*items, base=1, op="s1"):
    return dict(base_version=base, operation_id=f"web-sound-{op}-0123456789",
                operations=[dict(action="upsert", layer="sfx", id=i["id"],
                                 value=i) for i in items])


def _sfx(key, sid="s1", at=2.0):
    return dict(id=sid, storage_key=key, at=at)


def test_an_mcp_batch_web_sound_plays_the_closest_library_recording(
        api, monkeypatch):
    monkeypatch.setenv("WEB_SFX_ENABLED", "0")
    uploaded = []
    monkeypatch.setattr(video.storage, "put_bytes",
                        lambda key, data, ctype: uploaded.append((key, len(data), ctype)))
    result, status = video.apply_edit_batch_core(
        1, 2, _sfx_batch(_sfx(SFX)), origin="mcp")
    assert status == 200, result
    item = api.edls[2]["sfx"][0]
    sid = video.wsound.id_for_key(item["storage_key"])
    assert sid and uploaded and uploaded[0][0] == item["storage_key"]
    assert api.assets[item["storage_key"]]["meta"]["library_sound"] == sid
    assert abs(video.wsound.hit_at(item) - 2.0) <= 0.011
    assert "closest recording of the approved library" in json.dumps(result)
    # the user's own file still places as it is
    result, status = video.apply_edit_batch_core(
        1, 2, _sfx_batch(_sfx(UPLOAD, sid="s2"), base=2, op="s2"), origin="mcp")
    assert status == 200, result
    assert any(s["storage_key"] == UPLOAD for s in api.edls[3]["sfx"])


def test_a_web_sound_already_in_the_edit_still_moves(api, monkeypatch):
    monkeypatch.setenv("WEB_SFX_ENABLED", "0")
    edl = copy.deepcopy(api.edls[1])
    edl["sfx"] = [_sfx(SFX)]
    api.edls[1] = video.wschemas.validate_edl(edl, 20).model_dump()
    result, status = video.apply_edit_batch_core(
        1, 2, _sfx_batch(dict(id="s1", at=4.0)), origin="mcp")
    assert status == 200, result
    assert api.edls[2]["sfx"][0]["at"] == 4.0


def test_with_web_sounds_on_the_batch_gate_stands_aside(api, monkeypatch):
    monkeypatch.setenv("WEB_SFX_ENABLED", "1")
    monkeypatch.setenv("SFX_SEARCH_ENABLED", "1")
    result, status = video.apply_edit_batch_core(
        1, 2, _sfx_batch(_sfx(SFX)), origin="mcp")
    assert status == 200, result
