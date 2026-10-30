"""YTDLP_PROXY_FALLBACK: a second YouTube route once the first is walled.

30 Oct 2026 (C1): the owner's single static ISP proxy served 22-62 full
downloads, then YouTube walled it for 1.5-2 hours. Fallback proxies are
tried in order, each with the whole client ladder, only after a route ended
on the bot wall. With none configured the ladder is the original one, call
for call (tests/test_units.py pins that path too).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import config  # noqa: E402
import url_media  # noqa: E402

WALL = "Sign in to confirm you're not a bot"
YT = "https://www.youtube.com/watch?v=video"


def _strategies():
    return [(s.get("client"), s.get("cookies", False),
             s.get("format", "adaptive"))
            for s in url_media.ytaccess.extraction_strategies()]


@pytest.fixture
def calls(monkeypatch):
    seen = []
    monkeypatch.setattr(url_media, "_clear_partials", lambda workdir: None)
    return seen


def _fake(seen, outcome):
    """An _extract stand-in: outcome(proxy, n) returns a result or raises."""
    def fake(url, workdir, prefer=None, client_override=None,
             use_cookies=True, format_mode="adaptive", **via):
        seen.append((via.get("proxy"), client_override, use_cookies,
                     format_mode))
        return outcome(via.get("proxy"), len(seen))
    return fake


def _walled(proxy, n):
    raise url_media.FetchMediaError(WALL)


def test_without_a_fallback_the_ladder_is_unchanged(monkeypatch, calls):
    monkeypatch.setattr(config, "YTDLP_PROXY_FALLBACK", ())
    monkeypatch.setattr(url_media, "_extract", _fake(calls, _walled))
    with pytest.raises(url_media.FetchMediaError) as err:
        url_media._extract_with_fallback(YT, "/tmp")
    assert [c[1:] for c in calls] == _strategies()
    assert all(c[0] is None for c in calls)          # no proxy kwarg at all
    assert "paperclip" in str(err.value)


def test_a_walled_primary_falls_back_to_the_next_proxy(monkeypatch, calls):
    monkeypatch.setattr(config, "YTDLP_PROXY_FALLBACK",
                        ("http://fallback.invalid:1",))

    def outcome(proxy, n):
        if proxy == "http://fallback.invalid:1":
            return ("/tmp/dl.mp4", {"title": "ok"})
        raise url_media.FetchMediaError(WALL)
    monkeypatch.setattr(url_media, "_extract", _fake(calls, outcome))
    path, info = url_media._extract_with_fallback(YT, "/tmp")
    assert path == "/tmp/dl.mp4" and info["title"] == "ok"
    n = len(_strategies())
    assert [c[0] for c in calls] == [None] * n + ["http://fallback.invalid:1"]


def test_fallbacks_are_tried_in_order_and_a_full_wall_is_reported(
        monkeypatch, calls):
    monkeypatch.setattr(config, "YTDLP_PROXY_FALLBACK",
                        ("http://a.invalid:1", "http://b.invalid:2"))
    monkeypatch.setattr(url_media, "_extract", _fake(calls, _walled))
    with pytest.raises(url_media.FetchMediaError) as err:
        url_media._extract_with_fallback(YT, "/tmp")
    n = len(_strategies())
    assert [c[0] for c in calls] == ([None] * n + ["http://a.invalid:1"] * n
                                     + ["http://b.invalid:2"] * n)
    assert "not a problem with the link" in str(err.value)


def test_a_real_error_never_spends_a_fallback(monkeypatch, calls):
    monkeypatch.setattr(config, "YTDLP_PROXY_FALLBACK",
                        ("http://fallback.invalid:1",))

    def outcome(proxy, n):
        raise url_media.FetchMediaError("Private video" if n == 2 else WALL)
    monkeypatch.setattr(url_media, "_extract", _fake(calls, outcome))
    with pytest.raises(url_media.FetchMediaError) as err:
        url_media._extract_with_fallback(YT, "/tmp")
    assert "Private video" in str(err.value)
    assert [c[0] for c in calls] == [None, None]


def test_non_youtube_links_never_use_any_proxy_route(monkeypatch, calls):
    monkeypatch.setattr(config, "YTDLP_PROXY_FALLBACK",
                        ("http://fallback.invalid:1",))

    def outcome(proxy, n):
        raise url_media.FetchMediaError(WALL)
    monkeypatch.setattr(url_media, "_extract", _fake(calls, outcome))
    with pytest.raises(url_media.FetchMediaError):
        url_media._extract_with_fallback("https://vimeo.com/1", "/tmp")
    assert calls == [(None, None, True, "adaptive")]


class _Proc:
    pid = 123

    def communicate(self, timeout=None):
        return "", ""


def _command(monkeypatch, tmp_path, url, proxy=None):
    seen = {}
    (tmp_path / "dl.mp4").write_bytes(b"media")
    monkeypatch.setattr(config, "YTDLP_PROXY", "http://primary.invalid:8080")
    monkeypatch.setattr(config, "YTDLP_REMOTE_COMPONENTS", "")
    monkeypatch.setattr(url_media.ytaccess, "prepare_run_jar",
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(url_media.ytaccess, "pot_args", lambda: [])

    def fake_popen(cmd, **kwargs):
        seen["cmd"] = list(cmd)
        return _Proc()
    monkeypatch.setattr(url_media.subprocess, "Popen", fake_popen)
    kwargs = {} if proxy is None else {"proxy": proxy}
    url_media._extract(url, str(tmp_path), **kwargs)
    return seen["cmd"]


def test_a_route_proxy_replaces_the_primary_on_the_command_line(
        monkeypatch, tmp_path):
    cmd = _command(monkeypatch, tmp_path, YT, proxy="http://fb.invalid:1")
    assert cmd[cmd.index("--proxy") + 1] == "http://fb.invalid:1"
    assert cmd.count("--proxy") == 1
    cmd = _command(monkeypatch, tmp_path, YT)
    assert cmd[cmd.index("--proxy") + 1] == "http://primary.invalid:8080"
    cmd = _command(monkeypatch, tmp_path, "https://soundcloud.com/a/b",
                   proxy="http://fb.invalid:1")
    assert "--proxy" not in cmd


def test_the_env_list_is_parsed_and_empty_means_none(monkeypatch):
    import importlib
    monkeypatch.setenv("YTDLP_PROXY_FALLBACK",
                       " http://a.invalid:1 , ,http://b.invalid:2 ")
    importlib.reload(config)
    try:
        assert config.YTDLP_PROXY_FALLBACK == ("http://a.invalid:1",
                                               "http://b.invalid:2")
        monkeypatch.setenv("YTDLP_PROXY_FALLBACK", "")
        importlib.reload(config)
        assert config.YTDLP_PROXY_FALLBACK == ()
    finally:
        monkeypatch.delenv("YTDLP_PROXY_FALLBACK", raising=False)
        importlib.reload(config)
