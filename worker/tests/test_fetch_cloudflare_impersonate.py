"""A Cloudflare-challenged page link retries once with browser impersonation
(ticket 10, 30 Oct 2026), and nothing else changes for other sites."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import config  # noqa: E402
import url_media  # noqa: E402

CLOUDFLARE = ("ERROR: [generic] Got HTTP Error 403 caused by Cloudflare "
              "anti-bot challenge; try again with --extractor-args "
              "\"generic:impersonate\"")


class _Proc:
    pid = 123

    def __init__(self, err):
        self.err = err

    def communicate(self, timeout=None):
        return "", self.err


def _run(monkeypatch, tmp_path, url, first_err):
    calls = []
    monkeypatch.setattr(config, "YTDLP_PROXY", "")
    monkeypatch.setattr(config, "YTDLP_REMOTE_COMPONENTS", "")
    monkeypatch.setattr(url_media.ytaccess, "prepare_run_jar",
                        lambda *a, **k: None)
    monkeypatch.setattr(url_media.ytaccess, "pot_args", lambda: [])

    def fake_popen(cmd, **kwargs):
        calls.append(list(cmd))
        if len(calls) == 1:
            return _Proc(first_err)
        (tmp_path / "dl.mp4").write_bytes(b"media")
        return _Proc("")

    monkeypatch.setattr(url_media.subprocess, "Popen", fake_popen)
    return calls, url_media._extract_with_fallback(url, str(tmp_path))


def _impersonates(cmd):
    return any(a == "generic:impersonate" for a in cmd)


def test_cloudflare_challenge_retries_with_impersonation(monkeypatch,
                                                         tmp_path):
    calls, (path, _info) = _run(monkeypatch, tmp_path,
                                "https://example.com/clip", CLOUDFLARE)
    assert len(calls) == 2
    assert not _impersonates(calls[0])
    assert _impersonates(calls[1])
    assert path.endswith("dl.mp4")


def test_other_failures_are_not_retried(monkeypatch, tmp_path):
    with pytest.raises(url_media.FetchMediaError, match="404"):
        _run(monkeypatch, tmp_path, "https://example.com/clip",
             "ERROR: [generic] Unable to download webpage: HTTP Error 404")


def test_first_call_unchanged_for_working_sites(monkeypatch, tmp_path):
    (tmp_path / "dl.mp4").write_bytes(b"media")
    seen = []
    monkeypatch.setattr(config, "YTDLP_REMOTE_COMPONENTS", "")
    monkeypatch.setattr(url_media.ytaccess, "prepare_run_jar",
                        lambda *a, **k: None)
    monkeypatch.setattr(url_media.ytaccess, "pot_args", lambda: [])
    monkeypatch.setattr(url_media.subprocess, "Popen",
                        lambda cmd, **k: seen.append(cmd) or _Proc(""))
    url_media._extract_with_fallback("https://soundcloud.com/a/b",
                                     str(tmp_path))
    assert len(seen) == 1 and not _impersonates(seen[0])


def test_signature_matches_ytdlp_wording():
    assert url_media._cloudflare_challenged(CLOUDFLARE)
    assert not url_media._cloudflare_challenged("HTTP Error 403: Forbidden")
