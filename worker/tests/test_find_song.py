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
    monkeypatch.setattr(song_find, "search_soundcloud",
                        lambda q, count=6: [])
    out = agent_tools.find_song(_Ctx(), "song artist")
    assert "fetch_url" in out and "as_kind='music'" in out
    assert "which version you grabbed" in out
    assert "youtube.com/watch?v=x" in out


def test_soundcloud_fallbacks_ride_along_and_survive_their_own_failure(
        monkeypatch):
    """Aug 9: from the worker's IP, YouTube walled every music-label
    upload while SoundCloud fetched clean — so the escape route must be
    IN the first answer (a second search after a wall is a call the
    model often skips), and a broken SoundCloud search must cost the
    YouTube results nothing."""
    monkeypatch.setattr(song_find, "available", lambda: True)
    monkeypatch.setattr(config, "FIND_SONG_USER_IDS", "")
    monkeypatch.setattr(song_find, "search", lambda q, count=6: [
        _c("Song (Official Audio)", uploader="Artist - Topic")])
    monkeypatch.setattr(song_find, "search_soundcloud", lambda q, count=6: [
        {"title": "Song", "uploader": "artist", "duration_s": 200.0,
         "url": "https://api.soundcloud.com/tracks/soundcloud%3Atracks%3A1"}])
    out = agent_tools.find_song(_Ctx(), "song artist")
    assert "SoundCloud fallbacks" in out
    assert "api.soundcloud.com/tracks" in out
    assert "then the SoundCloud fallbacks" in out

    def broken(q, count=6):
        raise song_find.SongFindError("soundcloud search down")
    monkeypatch.setattr(song_find, "search_soundcloud", broken)
    out = agent_tools.find_song(_Ctx(), "song artist")
    assert "youtube.com/watch?v=x" in out
    assert "SoundCloud fallbacks" not in out


def test_a_walled_server_leads_with_soundcloud(monkeypatch):
    """When the boot probe says this box is YouTube-walled, find_song must
    put SoundCloud FIRST — recommending a source the datacenter IP cannot
    reach just buys a guaranteed failed download before the recovery. This
    is what removes the failed-first-attempt the user saw."""
    monkeypatch.setattr(song_find, "available", lambda: True)
    monkeypatch.setattr(config, "FIND_SONG_USER_IDS", "")
    monkeypatch.setattr(song_find, "search", lambda q, count=6: [
        _c("Song (Official Audio)", uploader="Artist - Topic")])
    monkeypatch.setattr(song_find, "search_soundcloud", lambda q, count=6: [
        {"title": "Song", "uploader": "artist", "duration_s": 200.0,
         "url": "https://api.soundcloud.com/tracks/soundcloud%3Atracks%3A1"}])
    monkeypatch.setattr(agent_tools.ytaccess, "youtube_walled",
                        lambda: True)
    out = agent_tools.find_song(_Ctx(), "song artist")
    # SoundCloud is named first and appears before the YouTube list.
    assert "blocking this server's IP" in out
    assert out.index("api.soundcloud.com") < out.index("youtube.com/watch")
    assert "start here" in out


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
    # Total-failure behavior requires both discovery lanes to fail. Leaving
    # the real SoundCloud search live makes this test network-dependent and,
    # when it succeeds, the product is right to return those usable results.
    monkeypatch.setattr(song_find, "search_soundcloud", boom)
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


# ── sighted picks: candidates arrive as PICTURES, not just titles ────────

class _SightedCtx(_Ctx):
    direct_sight = True
    agent_model = "m"
    workdir = "/tmp"

    def __init__(self):
        self.pending_images = []


def test_footage_candidates_reach_the_agents_eyes(monkeypatch, tmp_path):
    import llm
    monkeypatch.setattr(song_find, "footage_available", lambda: True)
    monkeypatch.setattr(config, "FIND_FOOTAGE_USER_IDS", "")
    monkeypatch.setattr(llm, "agent_sees", lambda m: True)
    hit = dict(_c("Starship's Second Flight Test", uploader="SpaceX",
                  dur=115), _thumb="https://i.ytimg.com/vi/x/hqdefault.jpg")
    monkeypatch.setattr(song_find, "search_footage",
                        lambda q, count=6: [hit])
    monkeypatch.setattr(agent_tools.net_fetch, "download",
                        lambda url, path, **kw: open(path, "wb").write(b"j"))
    ctx = _SightedCtx()
    ctx.workdir = str(tmp_path)
    out = agent_tools.find_footage(ctx, "spacex starship launch")
    assert "LOOKING" in out
    assert len(ctx.pending_images) == 1
    label, path = ctx.pending_images[0]
    assert label == hit["url"] or label == hit.get("id", label)
    assert os.path.exists(path)


def test_blind_deployment_degrades_to_text_only(monkeypatch, tmp_path):
    import llm
    monkeypatch.setattr(song_find, "footage_available", lambda: True)
    monkeypatch.setattr(config, "FIND_FOOTAGE_USER_IDS", "")
    monkeypatch.setattr(llm, "agent_sees", lambda m: False)

    def never(*a, **k):
        raise AssertionError("a blind agent must not download thumbnails")
    monkeypatch.setattr(agent_tools.net_fetch, "download", never)
    ctx = _SightedCtx()
    ctx.workdir = str(tmp_path)
    monkeypatch.setattr(song_find, "search_footage", lambda q, count=6: [
        dict(_c("clip"), _thumb="https://t/x.jpg")])
    out = agent_tools.find_footage(ctx, "topic")
    assert "LOOKING" not in out and ctx.pending_images == []


# ── the bot wall's two escape hatches ride every yt-dlp call ─────────────

def test_youtube_search_leads_anonymous_mweb_and_keeps_proxy(monkeypatch,
                                                             tmp_path):
    ck = tmp_path / "yt.txt"
    ck.write_text("# Netscape HTTP Cookie File\n")
    monkeypatch.setattr(config, "YTDLP_COOKIES_FILE", str(ck))
    monkeypatch.setattr(config, "YTDLP_PROXY", "http://u:p@proxy:8080")
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd

        class R:
            stdout = ""
            stderr = ""
            returncode = 0
        return R()
    monkeypatch.setattr(song_find.subprocess, "run", fake_run)
    song_find.search("song")
    assert "--cookies" not in seen["cmd"]
    i = seen["cmd"].index("--extractor-args")
    assert seen["cmd"][i + 1] == "youtube:player_client=mweb"
    assert "--proxy" in seen["cmd"]
    assert "http://u:p@proxy:8080" in seen["cmd"]


def test_youtube_search_uses_cookie_default_only_after_anonymous_wall(
        monkeypatch, tmp_path):
    ck = tmp_path / "yt.txt"
    ck.write_text("# Netscape HTTP Cookie File\n")
    monkeypatch.setattr(config, "YTDLP_COOKIES_FILE", str(ck))
    calls = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))

        class R:
            stdout = ""
            stderr = ("Sign in to confirm you're not a bot"
                      if len(calls) == 1 else "")
            returncode = 1 if len(calls) == 1 else 0
        return R()

    monkeypatch.setattr(song_find.subprocess, "run", fake_run)
    assert song_find.search("song") == []
    assert len(calls) == 2
    assert "--cookies" not in calls[0]
    assert "--cookies" in calls[1]


def test_a_full_album_never_outranks_the_single_track():
    """Production, Aug 8: "Interstellar official audio" ranked the 2.3-hour
    WaterTower Full Album above the 4-minute main theme, and the agent —
    reading "official channel" — reached for it. At that length it is
    never ONE song, and the byte cap would refuse it after minutes."""
    ranked = song_find.rank([
        _c("Interstellar Official Soundtrack | Full Album", dur=8363,
           uploader="WaterTower Music"),
        _c("Interstellar Main Theme - Hans Zimmer", dur=244),
    ], "interstellar soundtrack hans zimmer official audio")
    assert ranked[0]["title"].startswith("Interstellar Main Theme")


def test_a_walled_candidate_says_try_the_next_not_give_up(monkeypatch,
                                                          tmp_path):
    """Aug 9: the wall is per-UPLOAD (an official upload fetched fine while
    two re-uploads of the same track were challenged). One unlucky pick
    must roll to the next find_song candidate, so the failure text has to
    order the retry — and must NOT carry the give-up coda that told the
    model to stop and ask for an upload."""
    class Ctx:
        urls_fetched = []
        workdir = str(tmp_path)
    def walled(*a, **k):
        raise agent_tools.url_media.FetchMediaError(
            "YouTube blocked THIS upload from our server (\"sign in to "
            "confirm you're not a bot\") — a per-video check")
    monkeypatch.setattr(agent_tools.url_media, "fetch", walled)
    out = agent_tools.fetch_url(Ctx(), "https://www.youtube.com/watch?v=x",
                                as_kind="music")
    assert "not a bot" in out
    assert "suggest they upload" not in out     # no give-up script
    assert "Continue the current edit" in out   # do not freeze the picture
    # A REAL failure (private video) keeps the honest full stop.
    def private(*a, **k):
        raise agent_tools.url_media.FetchMediaError("Private video")
    monkeypatch.setattr(agent_tools.url_media, "fetch", private)
    out = agent_tools.fetch_url(Ctx(), "https://www.youtube.com/watch?v=x",
                                as_kind="music")
    assert "suggest they upload" in out and "Do NOT claim" in out

    # A DRM/premium-locked pick (the official chart master on SoundCloud) is
    # per-item too: try another candidate, never pass a cover off as the
    # original, and only fall to "upload it" when nothing else is the song.
    def drm(*a, **k):
        raise agent_tools.url_media.FetchMediaError(
            "[soundcloud] 718846078: This video is DRM protected")
    monkeypatch.setattr(agent_tools.url_media, "fetch", drm)
    out = agent_tools.fetch_url(Ctx(), "https://api.soundcloud.com/tracks/1",
                                as_kind="music")
    assert "another candidate" in out and "cover" in out
    assert "suggest they upload the file instead" not in out  # not a dead end


def test_cookie_jar_is_copied_never_the_mounted_secret(monkeypatch,
                                                       tmp_path):
    """yt-dlp writes rotated cookies back to the jar on every run; Render's
    /etc/secrets is read-only — passing the secret directly crashed every
    cookie-mode call with [Errno 30]. Only a writable copy may reach argv,
    and it is cleaned up after the run."""
    secret = tmp_path / "yt-cookies.txt"
    secret.write_text("# Netscape HTTP Cookie File\n")
    monkeypatch.setattr(config, "YTDLP_COOKIES_FILE", str(secret))
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = list(cmd)

        class R:
            stdout = ""
            stderr = ""
            returncode = 0
        return R()
    monkeypatch.setattr(song_find.subprocess, "run", fake_run)
    # SoundCloud is deliberately unchanged by the YouTube repair: its
    # production-proven path still gets the normalized writable cookie copy.
    song_find.search_soundcloud("song")
    assert str(secret) not in seen["cmd"]
    i = seen["cmd"].index("--cookies")
    copy_path = seen["cmd"][i + 1]
    assert copy_path != str(secret)
    assert not os.path.exists(copy_path)      # cleaned after the run
