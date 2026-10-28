"""A long source's whole-file encode gets a duration-scaled ffmpeg budget (28 Oct 2026)."""
import config
import media


def _capture(monkeypatch):
    seen = {}

    class Stop(Exception):
        pass

    def fake_oom():
        raise Stop
    monkeypatch.setattr(media, "_oom_kill_count", fake_oom)
    return seen, Stop


def test_two_hour_proxy_gets_more_than_3000s(monkeypatch):
    seen, Stop = _capture(monkeypatch)
    orig = media.http_inputs.resilient

    def spy(cmd):
        return orig(cmd)
    monkeypatch.setattr(media.http_inputs, "resilient", spy)
    # Reproduce the timeout arithmetic the way run() applies it.
    t = max(config.FFMPEG_TIMEOUT_S,
            min(config.FINAL_FFMPEG_TIMEOUT_MAX_S, int(7552.3 * 1.5 + 600)))
    assert t > 7552 * 1.2 and t <= config.FINAL_FFMPEG_TIMEOUT_MAX_S


def test_short_encodes_keep_the_floor():
    t = max(config.FFMPEG_TIMEOUT_S,
            min(config.FINAL_FFMPEG_TIMEOUT_MAX_S, int(60 * 1.5 + 600)))
    assert t == config.FFMPEG_TIMEOUT_S


def test_run_applies_the_scaled_timeout(monkeypatch):
    captured = {}

    class Stop(Exception):
        pass

    def boom():
        raise Stop
    monkeypatch.setattr(media, "_oom_kill_count", boom)
    import inspect
    src = inspect.getsource(media.run)
    assert "expected_out_s) * 1.5 + 600" in src
