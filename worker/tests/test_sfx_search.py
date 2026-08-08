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


def test_fetch_requires_a_same_turn_search_id():
    ctx = _Ctx()
    out = agent_tools.fetch_sfx(ctx, "openverse:never-searched")
    assert out.startswith("REJECTED") and "search_sfx first" in out


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
