"""find_song — a named song is a search, not a dead end.

The user asks for "Blinding Lights"; the open catalogs don't carry it; the
old answer was "paste a link". find_song runs yt-dlp's own web search (the
tool the fetch path already trusts) and hands candidate LINKS to the model,
which picks one and downloads it through the existing fetch_url pipeline —
no new network path, no bytes moved by the search itself. These tests pin
the ranking heuristics, the honesty of every gate, and the handoff wording
that keeps the pick correctable.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import agent_tools                                             # noqa: E402
import config                                                  # noqa: E402
import song_find                                               # noqa: E402


def _c(title, uploader="Someone", dur=200):
    return {"title": title, "uploader": uploader, "duration_s": dur,
            "url": "https://www.youtube.com/watch?v=x"}


# ── ranking: the junk sinks, the real song rises ─────────────────────────

def test_official_audio_outranks_lyric_and_loop_versions():
    ranked = song_find.rank([
        _c("Song (Lyrics)"),
        _c("Song (Official Audio)"),
        _c("Song [1 hour loop]", dur=3600),
    ], "song artist")
    assert ranked[0]["title"] == "Song (Official Audio)"
    assert ranked[-1]["title"] == "Song [1 hour loop]"


def test_topic_channel_is_the_labels_own_audio():
    ranked = song_find.rank([
        _c("Song (Music Video)", uploader="RandomFan"),
        _c("Song", uploader="Artist - Topic"),
    ], "song")
    assert ranked[0]["uploader"] == "Artist - Topic"


def test_the_users_own_words_forgive_a_junk_marker():
    # Asking FOR the slowed version makes "slowed" the point.
    ranked = song_find.rank([
        _c("Song (Official Audio)"),
        _c("Song (slowed + reverb)"),
    ], "song slowed reverb")
    assert ranked[0]["title"] == "Song (slowed + reverb)"


# ── gates: every off-state is an honest, actionable rejection ────────────

class _Ctx:
    job = {"user_id": 7}


def test_disabled_deployment_points_at_the_link_path(monkeypatch):
    monkeypatch.setattr(config, "FIND_SONG_ENABLED", False)
    out = agent_tools.find_song(_Ctx(), "anything")
    assert out.startswith("REJECTED") and "fetch_url" in out


def test_allowlist_narrows_to_named_accounts(monkeypatch):
    monkeypatch.setattr(config, "FIND_SONG_USER_IDS", "7, 12")
    assert song_find.allowed_for(7) and song_find.allowed_for("12")
    assert not song_find.allowed_for(99)
    monkeypatch.setattr(config, "FIND_SONG_USER_IDS", "")
    assert song_find.allowed_for(99)      # empty = everyone


def test_blocked_account_gets_the_link_fallback(monkeypatch):
    monkeypatch.setattr(config, "FIND_SONG_USER_IDS", "12")
    monkeypatch.setattr(song_find, "available", lambda: True)
    out = agent_tools.find_song(_Ctx(), "song")   # user 7, not listed
    assert out.startswith("REJECTED") and "paste a link" in out


def test_tool_hides_with_the_fetch_path(monkeypatch):
    monkeypatch.setattr(config, "URL_FETCH_ENABLED", False)
    assert not song_find.available()
    assert agent_tools._tool_disabled("find_song")


# ── the handoff: candidates in, fetch_url instruction out ────────────────

def test_results_teach_the_pick_and_the_download(monkeypatch):
    monkeypatch.setattr(song_find, "available", lambda: True)
    monkeypatch.setattr(config, "FIND_SONG_USER_IDS", "")
    monkeypatch.setattr(song_find, "search", lambda q, count=6: [
        _c("Song (Official Audio)", uploader="Artist - Topic")])
    out = agent_tools.find_song(_Ctx(), "song artist")
    assert "fetch_url" in out and "as_kind='music'" in out
    assert "which version you grabbed" in out
    assert "youtube.com/watch?v=x" in out


def test_no_results_never_claims_success(monkeypatch):
    monkeypatch.setattr(song_find, "available", lambda: True)
    monkeypatch.setattr(config, "FIND_SONG_USER_IDS", "")
    monkeypatch.setattr(song_find, "search", lambda q, count=6: [])
    out = agent_tools.find_song(_Ctx(), "gibberish qzx")
    assert "No results" in out and "paste a link" in out


def test_search_failure_is_reported_plainly(monkeypatch):
    monkeypatch.setattr(song_find, "available", lambda: True)
    monkeypatch.setattr(config, "FIND_SONG_USER_IDS", "")

    def boom(q, count=6):
        raise song_find.SongFindError("the web search timed out")
    monkeypatch.setattr(song_find, "search", boom)
    out = agent_tools.find_song(_Ctx(), "song")
    assert "failed" in out and "do NOT claim" in out


# ── find_footage: the same web search, pointed at b-roll (2026-08-08) ────

def test_footage_ranking_prefers_short_real_clips():
    ranked = song_find.rank_footage([
        _c("Starship Launch FULL 3 HOUR LIVESTREAM", dur=10800),
        _c("Starship's Second Flight Test", uploader="SpaceX", dur=115),
        _c("Reacting to the Starship launch!!", dur=300),
    ], "spacex starship launch")
    assert ranked[0]["title"] == "Starship's Second Flight Test"
    assert ranked[-1]["title"].startswith("Starship Launch FULL")


def test_footage_junk_is_forgiven_when_asked_for():
    ranked = song_find.rank_footage([
        _c("Starship flight test", dur=120),
        _c("Starship launch reaction", dur=120),
    ], "starship launch reaction")
    assert ranked[0]["title"] == "Starship launch reaction"


def test_find_footage_hands_off_to_clip_fetch(monkeypatch):
    monkeypatch.setattr(song_find, "footage_available", lambda: True)
    monkeypatch.setattr(config, "FIND_FOOTAGE_USER_IDS", "")
    monkeypatch.setattr(song_find, "search_footage", lambda q, count=6: [
        _c("Starship's Second Flight Test", uploader="SpaceX", dur=115)])
    out = agent_tools.find_footage(_Ctx(), "spacex starship launch")
    assert "as_kind='clip'" in out and "look_at_asset" in out
    assert "add_overlay" in out          # the cutaway is the point
    assert "youtube.com/watch?v=x" in out


def test_find_footage_gates_like_find_song(monkeypatch):
    monkeypatch.setattr(config, "FIND_FOOTAGE_ENABLED", False)
    assert not song_find.footage_available()
    assert agent_tools._tool_disabled("find_footage")
    out = agent_tools.find_footage(_Ctx(), "anything")
    assert out.startswith("REJECTED") and "search_stock" in out
    monkeypatch.setattr(config, "FIND_FOOTAGE_ENABLED", True)
    monkeypatch.setattr(config, "FIND_FOOTAGE_USER_IDS", "99")
    monkeypatch.setattr(song_find, "footage_available", lambda: True)
    out = agent_tools.find_footage(_Ctx(), "x")   # user 7, not listed
    assert out.startswith("REJECTED") and "fetch_url" in out
