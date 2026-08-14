"""Round 98.2 — the license is a label, not a wall.

The bundled library is deleted and search is the music surface, so what
search may SHOW decides what the product can do. The old filters silently
dropped every non-commercial track — meaning a hobbyist scoring a personal
video lost tracks they could lawfully use, and never knew. Now the terms
travel with the hit and the sentence the agent reads says them outright;
the only family still refused is no-derivatives, because syncing music in
timed relation with picture is itself an adaptation — an ND track cannot
be used in ANY edit, so offering one would only manufacture a violation.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import music_search                                            # noqa: E402
import net_fetch                                               # noqa: E402


def test_nc_is_allowed_and_labeled_loudly():
    assert music_search._license_ok("by-nc")
    assert music_search._license_ok(
        "https://creativecommons.org/licenses/by-nc-sa/3.0/")
    note = music_search._license_note("by-nc", "Artist A")
    assert "NON-COMMERCIAL" in note
    assert "Artist A" in note          # the credit obligation rides along


def test_nd_is_still_refused():
    # by-nc-nd and by-nd both carry no-derivatives.
    assert not music_search._license_ok("by-nd")
    assert not music_search._license_ok(
        "https://creativecommons.org/licenses/by-nc-nd/4.0/")


def test_open_licenses_read_as_before():
    assert "public domain" in music_search._license_note("cc0-1.0", None)
    by = music_search._license_note("by-4.0", "Artist B")
    assert "commercial use" in by and "Artist B" in by
    assert "NON-COMMERCIAL" not in by


def test_jamendo_positive_gate_admits_nc_ccurls():
    # The catalog lane's sanity check must accept the licenses the policy
    # now allows — a stale gate here would silently reintroduce the filter.
    assert music_search._CC_LICENSE.search(
        "https://creativecommons.org/licenses/by-nc-sa/3.0/")
    assert music_search._CC_LICENSE.search(
        "https://creativecommons.org/licenses/by/3.0/")


def test_openverse_is_not_commercial_gated(monkeypatch):
    seen = {}

    def fake_get_json(url, params=None, **kw):
        seen.update(params or {})
        return {"results": []}

    monkeypatch.setattr(music_search.net_fetch, "get_json", fake_get_json)
    music_search._openverse_search("lofi", None, None, 5)
    # modification (no ND) is required; commercial must NOT be.
    assert seen.get("license_type") == "modification"


def test_commercial_search_filters_nc_without_hiding_it_from_personal_use(
        monkeypatch):
    page = {"results": [
        {"id": "nc", "title": "Personal", "creator": "A",
         "license": "by-nc-sa", "license_version": "3.0",
         "url": "https://audio.example/nc.mp3", "duration": 30000},
        {"id": "by", "title": "Commercial", "creator": "B",
         "license": "by", "license_version": "4.0",
         "url": "https://audio.example/by.mp3", "duration": 30000},
    ]}
    monkeypatch.setattr(music_search.net_fetch, "get_json",
                        lambda *a, **k: page)
    personal = music_search._openverse_search("tech", None, None, 5)
    commercial = music_search._openverse_search(
        "tech", None, None, 5, commercial_only=True)
    assert [x["id"] for x in personal] == ["openverse:nc", "openverse:by"]
    assert [x["id"] for x in commercial] == ["openverse:by"]


def test_openverse_bad_static_token_falls_back_to_anonymous(monkeypatch):
    calls = []
    monkeypatch.setattr(music_search.config, "OPENVERSE_API_TOKEN", "stale")
    monkeypatch.setattr(music_search.config, "OPENVERSE_CLIENT_ID", "")
    monkeypatch.setattr(music_search.config, "OPENVERSE_CLIENT_SECRET", "")

    def fake_get_json(*args, **kwargs):
        calls.append(kwargs.get("headers"))
        if kwargs.get("headers"):
            raise net_fetch.FetchError("HTTP 401 from api.openverse.org")
        return {"results": []}

    monkeypatch.setattr(music_search.net_fetch, "get_json", fake_get_json)
    assert music_search._openverse_get_json(music_search.OPENVERSE_API) == {
        "results": []}
    assert calls == [{"Authorization": "Bearer stale"}, None]


def test_openverse_client_credentials_are_cached(monkeypatch):
    monkeypatch.setattr(music_search.config, "OPENVERSE_API_TOKEN", "")
    monkeypatch.setattr(music_search.config, "OPENVERSE_CLIENT_ID", "client")
    monkeypatch.setattr(music_search.config, "OPENVERSE_CLIENT_SECRET", "secret")
    monkeypatch.setattr(music_search, "_openverse_token", None)
    monkeypatch.setattr(music_search, "_openverse_token_expires_at", 0.0)
    monkeypatch.setattr(music_search, "_openverse_token_retry_at", 0.0)
    posts = []

    def fake_post(*args, **kwargs):
        posts.append(kwargs["data"])
        return {"access_token": "fresh", "expires_in": 36000}

    monkeypatch.setattr(music_search.net_fetch, "post_form_json", fake_post)
    first = music_search._openverse_auth_headers()
    second = music_search._openverse_auth_headers()
    assert first == second == {"Authorization": "Bearer fresh"}
    assert len(posts) == 1
    assert posts[0]["grant_type"] == "client_credentials"


def test_music_search_merges_and_diversifies_all_live_providers(monkeypatch):
    monkeypatch.setattr(music_search.config, "MUSIC_SEARCH_ENABLED", True)
    monkeypatch.setattr(music_search.config, "JAMENDO_CLIENT_ID", "client")
    called = []

    def jamendo(*_args):
        called.append("jamendo")
        return [
            {"provider": "jamendo", "id": "jamendo:1",
             "title": "Dark Pulse", "duration_s": 90, "license": "by"},
            {"provider": "jamendo", "id": "jamendo:2",
             "title": "Dark Engine", "duration_s": 80, "license": "by"},
        ]

    def openverse(*_args):
        called.append("openverse")
        return [
            {"provider": "openverse", "id": "openverse:1",
             "title": "Dark Cinematic Pulse", "duration_s": 100,
             "license": "by"},
        ]

    monkeypatch.setattr(music_search, "_jamendo_search", jamendo)
    monkeypatch.setattr(music_search, "_openverse_search", openverse)
    hits = music_search.search("dark cinematic pulse", count=3)
    assert set(called) == {"jamendo", "openverse"}
    assert {hit["provider"] for hit in hits[:2]} == {
        "jamendo", "openverse"}
    assert hits[0]["id"] == "openverse:1"  # strongest literal intent


def test_one_broken_music_provider_does_not_hide_the_other(monkeypatch):
    monkeypatch.setattr(music_search.config, "MUSIC_SEARCH_ENABLED", True)
    monkeypatch.setattr(music_search.config, "JAMENDO_CLIENT_ID", "client")
    monkeypatch.setattr(
        music_search, "_jamendo_search",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(
        music_search, "_openverse_search",
        lambda *_args: [{"provider": "openverse", "id": "openverse:ok",
                         "title": "Clean Beat", "duration_s": 70,
                         "license": "by"}])
    assert [h["id"] for h in music_search.search("clean beat")] == [
        "openverse:ok"]
