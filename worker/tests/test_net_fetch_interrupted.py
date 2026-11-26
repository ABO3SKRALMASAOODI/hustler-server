"""net_fetch.download when the body breaks off (PR #179 finding 3).

A body read that failed inside requests (a reset, an incomplete read, the deadline watchdog tearing
the socket down) escaped download() as a raw requests error and left the half-written file at
out_path: net_fetch cleaned up only on FetchError, and stock's rendition fallback never saw it.
"""
import os
import sys
import time
from types import SimpleNamespace

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import net_fetch                                              # noqa: E402


class _Resp:
    status_code = 200
    headers = {"content-length": "0"}

    def __init__(self, error, aborted=False):
        self.error = error
        self._valmera_session = SimpleNamespace(
            _valmera_adapter=SimpleNamespace(aborted=aborted),
            _valmera_timer=None, close=lambda: None)

    def iter_content(self, size):
        yield b"x" * 1024
        raise self.error

    def close(self):
        pass


def _serve(monkeypatch, resp):
    monkeypatch.setattr(net_fetch, "_open", lambda url, *a, **k: (resp, url))


def test_a_reset_mid_body_is_a_fetch_error_and_leaves_no_file(monkeypatch, tmp_path):
    _serve(monkeypatch, _Resp(requests.exceptions.ChunkedEncodingError("IncompleteRead")))
    out = tmp_path / "clip.mp4"
    with pytest.raises(net_fetch.FetchError) as err:
        net_fetch.download("https://cdn.example/clip.mp4", str(out), max_bytes=1 << 20, timeout_s=90)
    assert not isinstance(err.value, net_fetch.TimeLimit)
    assert "interrupted" in str(err.value) and "cdn.example" in str(err.value)
    assert not out.exists()


def test_the_watchdog_ending_the_body_is_the_time_limit(monkeypatch, tmp_path):
    _serve(monkeypatch, _Resp(requests.exceptions.ConnectionError("socket shut down"), aborted=True))
    out = tmp_path / "clip.mp4"
    with pytest.raises(net_fetch.TimeLimit, match="download exceeded 90s"):
        net_fetch.download("https://cdn.example/clip.mp4", str(out), max_bytes=1 << 20, timeout_s=90)
    assert not out.exists()


def test_a_read_timeout_at_the_deadline_is_the_time_limit(monkeypatch, tmp_path):
    start = time.monotonic()
    clock = iter([start, start + 90.0, start + 90.0, start + 90.0])
    monkeypatch.setattr(net_fetch.time, "monotonic", lambda: next(clock, start + 90.0))
    _serve(monkeypatch, _Resp(requests.exceptions.ConnectionError("Read timed out")))
    with pytest.raises(net_fetch.TimeLimit):
        net_fetch.download("https://cdn.example/clip.mp4", str(tmp_path / "c.mp4"), max_bytes=1 << 20,
                           timeout_s=90)


def test_size_and_time_limits_are_typed():
    assert issubclass(net_fetch.TooLarge, net_fetch.FetchError)
    assert issubclass(net_fetch.TimeLimit, net_fetch.FetchError)
    assert not issubclass(net_fetch.PolicyError, (net_fetch.TooLarge, net_fetch.TimeLimit))
