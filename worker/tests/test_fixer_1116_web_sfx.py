"""#916: a sound the user asks for that the library lacks is found online.

Four customers (laughter track p5562, ocean waves and coastal birds p5750,
and two more) were told "the sound library has none": the web-SFX chain was
still off by the Wave 0 owner-audition default although the owner replaced
that rule on 13 Nov 2026 (SFX judged by the references, no owner audition,
real recordings only). The editor read list_sound_library, saw no match and
declined. These tests fail on that code and pass on the fix.
"""

import config
import motion_tools
import sfx_search
import agent_tools
import agent_prompt
from w0c_support import tool_context


def test_web_sounds_are_on_by_default():
    assert config.WEB_SFX_ENABLED is True
    assert sfx_search.available() is True
    names = {t["function"]["name"] for t in agent_tools.openai_tools()}
    assert {"search_sfx", "fetch_sfx"} <= names


def test_a_sound_the_library_lacks_points_at_the_live_search():
    ctx, fake = tool_context({"keep": [[0.0, 10.0]]})
    out = motion_tools.list_sound_library(ctx, role="ocean waves and seabirds")
    assert "search_sfx(query='ocean waves and seabirds')" in out
    assert "filed it for the owner" not in out
    assert "sound_requests" not in fake.kv          # nothing waits on an audition


def test_the_full_listing_names_the_route_for_an_unlisted_sound():
    ctx, _fake = tool_context({"keep": [[0.0, 10.0]]})
    out = motion_tools.list_sound_library(ctx)
    assert "search_sfx(query=" in out


def test_the_editor_is_told_where_an_unlisted_sound_comes_from():
    state = agent_prompt.project_state_block("v", "i", "e", [], [])
    assert "search_sfx" in state


def test_the_kill_switch_still_files_for_the_owner(monkeypatch):
    monkeypatch.setattr(config, "WEB_SFX_ENABLED", False)
    ctx, fake = tool_context({"keep": [[0.0, 10.0]]})
    out = motion_tools.list_sound_library(ctx, role="laughter track")
    assert "filed it for the owner" in out and "search_sfx" not in out


def test_the_web_sfx_share_of_the_compact_catalog_is_capped():
    """The four web-SFX schemas Wave 0 removed came back with the owner's
    13 Nov rule; the per-turn list loads them on demand (TOOL_DOMAINS
    'sfx'). Their share of the static catalog stays small."""
    import json
    names = ("search_sfx", "audition_sfx_candidates", "fetch_sfx",
             "add_web_sfx")
    assert all(n in agent_tools.TOOL_DOMAINS["sfx"] for n in names)
    assert not set(names) & set(agent_tools.TOOL_CORE)
    cat = agent_tools.openai_tools("any-model", compact=True, names=names)
    assert len(cat) == 4
    assert len(json.dumps(cat, separators=(",", ":"))) <= 1_300


def test_an_anonymous_openverse_page_never_exceeds_twenty(monkeypatch):
    """Live on 16 Nov the box had no Openverse credentials and sfx_search
    asked for page_size 24: Openverse answered HTTP 401 to every search."""
    import music_search
    import net_fetch
    seen = []

    def fake_get_json(url, params=None, headers=None, **kw):
        seen.append((dict(params or {}), headers))
        if not headers and int((params or {}).get("page_size", 0)) > 20:
            raise net_fetch.FetchError("HTTP 401 from api.openverse.org")
        return {"results": []}

    monkeypatch.setattr(config, "OPENVERSE_API_TOKEN", "")
    monkeypatch.setattr(config, "OPENVERSE_CLIENT_ID", "")
    monkeypatch.setattr(config, "OPENVERSE_CLIENT_SECRET", "")
    monkeypatch.setattr(net_fetch, "get_json", fake_get_json)
    sfx_search.search("ocean waves")                 # count 12 -> asks 24
    assert seen and all(int(p["page_size"]) <= 20 for p, _h in seen)
    # an authenticated request keeps its own page size
    monkeypatch.setattr(music_search, "_openverse_auth_headers",
                        lambda force_refresh=False: {"Authorization": "x"})
    seen.clear()
    music_search._openverse_get_json("u", params={"page_size": 24})
    assert seen[0][0]["page_size"] == 24
