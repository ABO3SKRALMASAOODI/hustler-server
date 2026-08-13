"""search_sfx / fetch_sfx — real recorded sounds, found on demand.

The bundled pack and AI sound generation are both deleted; what an edit
needs now is FETCHED: Openverse (fronting Freesound's half-million
recordings) searched by the sound's physical name, license terms labeled
per hit, downloads through net_fetch into ordinary project assets. These
tests pin the duration cap that keeps field recordings out of a one-shot
picker, the license reuse from music_search, and the tool-level contract
(same-turn ids, honest failures, add_sfx handoff).
"""
import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import agent_tools                                             # noqa: E402
import config                                                  # noqa: E402
import sfx_search                                              # noqa: E402


def _hit(title="Camera Shutter", dur=0.3, lic="cc0"):
    return {"provider": "openverse", "id": "openverse:x", "title": title,
            "author": "A", "duration_s": dur, "license": lic,
            "page_url": "https://freesound.org/x",
            "_url": "https://cdn.freesound.org/previews/x-hq.mp3"}


def test_duration_cap_keeps_field_recordings_out(monkeypatch):
    rows = [{"id": "a", "title": "Click", "duration": 300, "license": "cc0",
             "url": "https://cdn/x.mp3", "creator": "A"},
            {"id": "b", "title": "One hour of rain", "duration": 3.6e6,
             "license": "cc0", "url": "https://cdn/y.mp3", "creator": "B"}]
    monkeypatch.setattr(sfx_search.net_fetch, "get_json",
                        lambda *a, **k: {"results": rows})
    hits = sfx_search.search("click")
    assert [h["title"] for h in hits] == ["Click"]      # 1hr filtered
    # ND is refused outright; NC passes and is labeled by the shared
    # music_search note (its own tests own the wording).
    rows[0]["license"] = "by-nc-nd"
    assert sfx_search.search("click") == []


def test_openverse_query_is_modification_only(monkeypatch):
    seen = {}

    def fake(url, params=None, **kw):
        seen.update(params or {})
        return {"results": []}
    monkeypatch.setattr(sfx_search.net_fetch, "get_json", fake)
    sfx_search.search("whoosh")
    assert seen.get("license_type") == "modification"


class _Db:
    def run(self, *a, **k):
        return None


class _Ctx:
    db = _Db()
    project_id = 3
    workdir = "/tmp"


def test_fetch_rejects_an_unrecoverable_result_id():
    ctx = _Ctx()
    out = agent_tools.fetch_sfx(ctx, "openverse:never-searched")
    assert out.startswith("REJECTED") and "Call search_sfx" in out


def test_search_results_hand_off_to_fetch_and_add(monkeypatch):
    monkeypatch.setattr(sfx_search, "search", lambda q, max_s=None: [_hit()])
    ctx = _Ctx()
    out = agent_tools.search_sfx(ctx, "camera shutter")
    assert "fetch_sfx(id)" in out and "add_sfx" in out
    assert "license terms" in out
    assert ctx._sfx_hits["openverse:x"]["title"] == "Camera Shutter"


def test_disabled_deployment_rejects_honestly(monkeypatch):
    monkeypatch.setattr(config, "SFX_SEARCH_ENABLED", False)
    out = agent_tools.search_sfx(_Ctx(), "whoosh")
    assert out.startswith("REJECTED") and "upload" in out


def test_failed_download_never_claims_success(monkeypatch):
    ctx = _Ctx()
    ctx._sfx_hits = {"openverse:x": _hit()}

    def boom(item, path):
        raise sfx_search.SfxSearchError("cdn said no")
    monkeypatch.setattr(sfx_search, "download", boom)
    out = agent_tools.fetch_sfx(ctx, "openverse:x")
    assert "Could not download" in out and "Do NOT claim" in out


def test_search_hit_cache_survives_the_agent_turn():
    hit = _hit()

    class CacheDb:
        def run(self, fn, *args):
            if fn.__name__ == "kv_get":
                return json.dumps({hit["id"]: hit})
            return None

    class CacheCtx:
        db = CacheDb()
        project_id = 44
        _sfx_hits = {}

    got, err = agent_tools._recover_search_hit(
        CacheCtx(), "sfx", hit["id"],
        lambda _rid: (_ for _ in ()).throw(AssertionError("no API call")))
    assert err is None and got["title"] == "Camera Shutter"


def test_editorial_ranking_prefers_clean_one_shot_over_music_and_loops(
        monkeypatch):
    rows = [
        {"id": "noisy", "title": "Cinematic Whoosh Music Loop Pack",
         "duration": 1400, "license": "cc0", "url": "https://cdn/noisy.mp3",
         "creator": "A"},
        {"id": "clean", "title": "Clean Cinematic Whoosh One Shot",
         "duration": 1200, "license": "cc0", "url": "https://cdn/clean.mp3",
         "creator": "B", "description": "dry transition foley"},
        {"id": "long", "title": "Whoosh ambience background",
         "duration": 12000, "license": "cc0", "url": "https://cdn/long.mp3",
         "creator": "C"},
    ]
    monkeypatch.setattr(sfx_search.net_fetch, "get_json",
                        lambda *a, **k: {"results": rows})
    hits = sfx_search.search("cinematic whoosh", count=3)
    assert hits[0]["id"] == "openverse:clean"
    assert hits[-1]["id"] != "openverse:clean"


def test_one_call_web_sfx_searches_fetches_and_places(monkeypatch, tmp_path):
    hit = _hit("Clean Cinematic Whoosh", 1.2)
    monkeypatch.setattr(sfx_search, "search",
                        lambda query, max_s=None, count=6: [hit])
    fetched = {"title": hit["title"], "duration_s": 1.2,
               "storage_key": "sfx/3/real.mp3", "license_note": "CC0",
               "hit": hit}
    monkeypatch.setattr(agent_tools, "_download_sfx_hit",
                        lambda ctx, selected: (fetched, None))
    placed = {}

    def fake_add(_ctx, storage_key, at, gain_db):
        placed.update(storage_key=storage_key, at=at, gain_db=gain_db)
        return "EDL v1 -> v2: sfx placed"

    monkeypatch.setattr(agent_tools, "add_sfx", fake_add)

    class Ctx(_Ctx):
        workdir = str(tmp_path)

        @staticmethod
        def latest_edl():
            return {"json": {"keep": [[0, 10]]}}

    out = agent_tools.add_web_sfx(Ctx(), "cinematic whoosh", 3.2, -7)
    assert out.startswith("EDL v1 -> v2")
    assert placed == {"storage_key": "sfx/3/real.mp3", "at": 3.2,
                      "gain_db": -7.0}
    assert "Clean Cinematic Whoosh" in out and "license: CC0" in out
