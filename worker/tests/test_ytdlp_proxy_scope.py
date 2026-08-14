"""The ISP proxy repairs YouTube without changing working media providers."""

import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import config  # noqa: E402
import song_find  # noqa: E402
import url_media  # noqa: E402


class _ExtractProcess:
    pid = 123

    def communicate(self, timeout=None):
        return "", ""


def _extract_command(monkeypatch, tmp_path, url):
    seen = {}
    (tmp_path / "dl.mp4").write_bytes(b"media")
    monkeypatch.setattr(config, "YTDLP_PROXY", "http://proxy.invalid:8080")
    monkeypatch.setattr(config, "YTDLP_REMOTE_COMPONENTS", "")
    monkeypatch.setattr(url_media.ytaccess, "prepare_run_jar",
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(url_media.ytaccess, "pot_args", lambda: [])

    def fake_popen(cmd, **kwargs):
        seen["cmd"] = list(cmd)
        return _ExtractProcess()

    monkeypatch.setattr(url_media.subprocess, "Popen", fake_popen)
    url_media._extract(url, str(tmp_path))
    return seen["cmd"]


def test_fetch_uses_proxy_for_youtube(monkeypatch, tmp_path):
    cmd = _extract_command(
        monkeypatch, tmp_path, "https://www.youtube.com/watch?v=video")
    assert cmd[cmd.index("--proxy") + 1] == "http://proxy.invalid:8080"


def test_fetch_keeps_non_youtube_providers_direct(monkeypatch, tmp_path):
    cmd = _extract_command(
        monkeypatch, tmp_path, "https://soundcloud.com/artist/song")
    assert "--proxy" not in cmd


def test_soundcloud_search_keeps_direct_path(monkeypatch):
    seen = {}
    monkeypatch.setattr(config, "YTDLP_PROXY", "http://proxy.invalid:8080")
    monkeypatch.setattr(config, "YTDLP_REMOTE_COMPONENTS", "")
    monkeypatch.setattr(song_find.ytaccess, "prepare_run_jar",
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(song_find.ytaccess, "pot_args", lambda: [])

    def fake_run(cmd, **kwargs):
        seen["cmd"] = list(cmd)

        class Result:
            stdout = ""
            stderr = ""
            returncode = 0

        return Result()

    monkeypatch.setattr(song_find.subprocess, "run", fake_run)
    song_find.search_soundcloud("artist song")
    assert "--proxy" not in seen["cmd"]
